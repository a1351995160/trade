"""实际到达的行情证据；真实采集与合成输入分离，不回填供应商发布时间。"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
import platform
import re
import subprocess

import pandas as pd

from .bounded_research_v1 import _put, _read
from .common import stable_hash
from .mutation_boundary import ObjectiveMutationLock


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError('SNAPSHOT_AWARE_TIME_REQUIRED')
    return stamp.tz_convert('Asia/Shanghai')


def _number(value, *, positive=False):
    if isinstance(value, bool):
        raise ValueError('SNAPSHOT_NUMBER_INVALID')
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError('SNAPSHOT_NUMBER_INVALID')
    return result


def validate_snapshot(value):
    if value.get('schema_version') != 'FORWARD_MARKET_SNAPSHOT_V1':
        raise ValueError('SNAPSHOT_SCHEMA_INVALID')
    if value.get('profile') not in ('SYNTHETIC', 'REAL_OBSERVED') or value.get('phase') not in ('OPEN', 'CLOSE'):
        raise ValueError('SNAPSHOT_PROFILE_OR_PHASE_INVALID')
    stamp = _stamp(value['received_at'])
    day = value['market_date']
    if type(day) is not int or int(stamp.strftime('%Y%m%d')) != day:
        raise ValueError('SNAPSHOT_NOT_SAME_DAY_RECEIPT')
    payload = value['payload']
    if type(payload.get('corporate_actions_complete')) is not bool or not isinstance(payload.get('corporate_actions'), list):
        raise ValueError('SNAPSHOT_ACTION_EVIDENCE_REQUIRED')
    collections = {}
    for field in ('bars', 'turn', 'states'):
        rows = payload[field]
        if not isinstance(rows, list) or not rows:
            raise ValueError('SNAPSHOT_EMPTY_INPUT')
        keys = [row['symbol'] for row in rows]
        if len(keys) != len(set(keys)) or any(not re.fullmatch(r'\d{6}\.(SH|SZ)', s) for s in keys):
            raise ValueError('SNAPSHOT_SYMBOL_INVALID')
        if any(type(row['date']) is not int or row['date'] != day for row in rows):
            raise ValueError('SNAPSHOT_ROW_DATE_INVALID')
        collections[field] = set(keys)
    if len({frozenset(x) for x in collections.values()}) != 1:
        raise ValueError('SNAPSHOT_COVERAGE_MISMATCH')
    for row in payload['bars']:
        for field in ('open', 'high', 'low', 'close', 'prev_close'):
            _number(row[field], positive=True)
        for field in ('volume', 'amount'):
            _number(row[field])
        if row['low'] > min(row['open'], row['close']) or row['high'] < max(row['open'], row['close']):
            raise ValueError('SNAPSHOT_OHLC_INVALID')
    for row in payload['turn']:
        _number(row['turn'])
        if row['tradestatus'] not in (0, 1) or isinstance(row['tradestatus'], bool):
            raise ValueError('SNAPSHOT_TRADE_STATUS_INVALID')
    for row in payload['states']:
        if any(type(row[k]) is not bool for k in ('listed', 'delisted', 'is_st', 'suspended')):
            raise ValueError('SNAPSHOT_UNKNOWN_SECURITY_STATE')
        if row['board'] not in ('MAIN', 'CHINEXT', 'STAR'):
            raise ValueError('SNAPSHOT_UNSUPPORTED_BOARD')
    return value


class SnapshotStoreV1:
    def __init__(self, root):
        self.root = Path(root).absolute()
        if self.root.resolve() != self.root:
            raise ValueError('SNAPSHOT_ROOT_REDIRECTED')

    def _path(self, snapshot_id):
        if not re.fullmatch(r'SNAP_[0-9a-f]{64}', snapshot_id):
            raise ValueError('SNAPSHOT_ID_INVALID')
        path = self.root / (snapshot_id + '.json')
        if path.resolve() != path:
            raise ValueError('SNAPSHOT_PATH_REDIRECTED')
        return path

    def _record(self, body):
        validate_snapshot(body)
        digest = stable_hash(body)
        value = {**body, 'snapshot_id': 'SNAP_' + digest, 'snapshot_hash': digest}
        with ObjectiveMutationLock.for_resource(self.root / 'snapshot-store'):
            _put(self._path(value['snapshot_id']), value)
        return value

    def load(self, snapshot_id):
        value = _read(self._path(snapshot_id))
        body = {k:v for k,v in value.items() if k not in ('snapshot_id', 'snapshot_hash')}
        if value['snapshot_id'] != snapshot_id or value['snapshot_hash'] != stable_hash(body) or snapshot_id != 'SNAP_' + stable_hash(body):
            raise ValueError('SNAPSHOT_IDENTITY_CONFLICT')
        if value['source_response_hash'] != stable_hash(value['source_responses']):
            raise ValueError('SNAPSHOT_SOURCE_CONFLICT')
        validate_snapshot(value)
        if value['profile'] == 'REAL_OBSERVED' and value['provider'] != 'TDX_TQ_LOCAL_UNCACHED_V1':
            raise ValueError('SNAPSHOT_REAL_PROVIDER_UNSUPPORTED')
        return value

    def record_synthetic(self, *, phase, market_date, payload, received_at, provider='SYNTHETIC'):
        raw = {'synthetic_payload': payload}
        return self._record({'schema_version':'FORWARD_MARKET_SNAPSHOT_V1', 'profile':'SYNTHETIC',
            'phase':phase, 'market_date':market_date, 'received_at':_stamp(received_at).isoformat(),
            'provider':provider, 'payload':payload, 'source_responses':raw, 'source_response_hash':stable_hash(raw)})

    def capture_tdx(self, *, phase, symbols):
        """真实入口不接受用户填写接收时间、来源响应或真假标签。"""
        if phase not in ('OPEN', 'CLOSE') or not symbols or len(symbols) > 20 or len(set(symbols)) != len(symbols):
            raise ValueError('SNAPSHOT_CAPTURE_SCOPE_INVALID')
        if any(not re.fullmatch(r'\d{6}\.(SH|SZ)', s) for s in symbols):
            raise ValueError('SNAPSHOT_SYMBOL_INVALID')
        assert_tdx_ready()
        from ..data.tdx.tq_client import TQClient
        client = TQClient(use_cache=False, retries=1, timeout=10)
        started = _stamp(_now())
        minute = started.hour * 60 + started.minute
        if not ((phase == 'OPEN' and 570 <= minute <= 575) or (phase == 'CLOSE' and 900 <= minute <= 1020)):
            raise ValueError('SNAPSHOT_OUTSIDE_CAPTURE_WINDOW')
        day = int(started.strftime('%Y%m%d'))
        raw = []
        def request(method, params):
            result = client.request(method, params, use_cache=False)
            raw.append({'method':method, 'params':params, 'received_at':_stamp(_now()).isoformat(), 'result':result})
            return result
        # 按技能检查本地HTTP服务，再读取今日交易所日历。
        request('get_match_stkinfo', {'key_word':'茅台'})
        calendar = request('get_trading_calendar', {'market':'SH','start_time':str(day),'end_time':str(day)})
        dates = calendar.get('Value', []) if isinstance(calendar, dict) else calendar
        if not isinstance(dates, list) or day not in [int(str(x).replace('-', '')) for x in dates]:
            raise ValueError('SNAPSHOT_NOT_EXCHANGE_SESSION')
        payload = {'bars':[], 'turn':[], 'states':[], 'corporate_actions':[], 'corporate_actions_complete':True}
        for symbol in symbols:
            snapshot = _symbol_row(request('get_market_snapshot', {'stock_code':symbol}), symbol)
            info = _symbol_row(request('get_stock_info', {'stock_code':symbol}), symbol)
            more = _symbol_row(request('get_more_info', {'stock_code':symbol,'field_list':['HqDate','TPFlag','fHSL']}), symbol)
            if int(str(more['HqDate']).replace('-', '')) != day:
                raise ValueError('SNAPSHOT_STALE_MARKET_DATE')
            if _number(info['DelayMin']) != 0:
                raise ValueError('SNAPSHOT_DELAYED_FEED_UNSUPPORTED')
            board = {'1':'MAIN','3':'CHINEXT','4':'STAR'}.get(str(info['HSStockKind']))
            if board is None:
                raise ValueError('SNAPSHOT_UNSUPPORTED_SECURITY_TYPE')
            listed_date = int(str(info['J_start']).replace('-', ''))
            state = {'symbol':symbol,'date':day,'listed':listed_date <= day,
                'delisted':_flag(info['IsQuitGP']), 'is_st':_flag(info['IsSTGP']),
                'board':board,'suspended':_flag(more['TPFlag'])}
            payload['states'].append(state)
            if _flag(info['TodayDRFlag']):
                payload['corporate_actions_complete'] = False
                payload['corporate_actions'].append({'symbol':symbol,'date':day,'type':'UNRESOLVED_TDX_ACTION'})
            if phase == 'OPEN':
                price = _number(snapshot['Now'], positive=True)
                # 以收到时的现价模拟，不回填为9:30的历史开盘成交；总手转换为股。
                bar = {'symbol':symbol,'date':day,'open':price,'high':price,'low':price,'close':price,
                    'volume':_number(snapshot['Volume'])*100,'amount':_number(snapshot['Amount']),
                    'prev_close':_number(snapshot['LastClose'], positive=True),
                    'price_basis':'OBSERVED_NOW', 'amount_unit':'TDX_NATIVE_NOT_USED_AT_OPEN'}
            else:
                daily = _symbol_row(request('get_market_data', {'stock_list':[symbol], 'period':'1d',
                    'start_time':str(day),'end_time':str(day),'count':0,'dividend_type':'none','fill_data':False}), symbol)
                if daily['Date'] != [str(day)] and daily['Date'] != [day]:
                    raise ValueError('SNAPSHOT_DAILY_DATE_MISMATCH')
                bar = {'symbol':symbol,'date':day,'prev_close':_number(snapshot['LastClose'],positive=True),
                    **{k:_number(daily[v][0]) for k,v in [('open','Open'),('high','High'),('low','Low'),('close','Close'),('volume','Volume')]},
                    'amount':_number(daily['Amount'][0])*10000,'amount_unit':'CNY','price_basis':'RAW_CLOSE'}
            payload['bars'].append(bar)
            payload['turn'].append({'symbol':symbol,'date':day,'turn':_number(more['fHSL']),
                'tradestatus':0 if state['suspended'] else 1})
        finished = _stamp(_now())
        if finished.date() != started.date() or (finished-started).total_seconds() > 60:
            raise ValueError('SNAPSHOT_CAPTURE_TOO_SLOW')
        return self._record({'schema_version':'FORWARD_MARKET_SNAPSHOT_V1','profile':'REAL_OBSERVED',
            'phase':phase,'market_date':day,'received_at':finished.isoformat(),'capture_started_at':started.isoformat(),
            'provider':'TDX_TQ_LOCAL_UNCACHED_V1','payload':payload,'source_responses':raw,
            'source_response_hash':stable_hash(raw),'quote_freshness':'PROVIDER_DATE_AND_REQUEST_TIME_ONLY'})


def _symbol_row(value, symbol):
    if not isinstance(value, dict):
        raise ValueError('SNAPSHOT_PROVIDER_SHAPE_UNSUPPORTED')
    row = value.get(symbol, value)
    if not isinstance(row, dict):
        raise ValueError('SNAPSHOT_PROVIDER_SHAPE_UNSUPPORTED')
    return row


def _flag(value):
    if str(value) not in ('0', '1'):
        raise ValueError('SNAPSHOT_PROVIDER_FLAG_UNKNOWN')
    return str(value) == '1'


def assert_tdx_ready():
    if platform.system() != 'Windows':
        raise RuntimeError('TDX_WINDOWS_REQUIRED')
    import winreg
    found = False
    for name in ('通达信金融终端64','通达信专业版','通达信金融终端(量化模拟)','通达信金融终端(测试)'):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\'+name):
                found = True
                break
        except FileNotFoundError:
            continue
    if not found:
        raise RuntimeError('TDX_INSTALLATION_NOT_FOUND')
    processes = subprocess.run(['tasklist','/FI','IMAGENAME eq TdxW.exe','/FO','CSV','/NH'],
        capture_output=True,text=True,timeout=10,check=True)
    if 'tdxw.exe' not in processes.stdout.lower():
        raise RuntimeError('TDX_CLIENT_NOT_RUNNING')
