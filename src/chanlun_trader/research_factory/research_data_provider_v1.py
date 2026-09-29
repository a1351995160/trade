"""维护者登记的只读 BaoStock 原件目录；仅供探索，不授予独立资格。"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import numpy as np
import pandas as pd
from ..research.guard import ResearchDataAccessGuard

TAX_SOURCE = 'https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html'

def day(value):
    return int(datetime.strptime(str(value).replace('-', ''), '%Y%m%d').strftime('%Y%m%d'))

class ResearchDataProviderV1:
    """仅维护者登记：manifest 包含 adapter、symbols、start/end、files。

    files 的相对路径逐项绑定 sha256/start/end（整文件覆盖范围）。
    access_recorder 必须在读原件前持久记录；authorization 由可信服务解析，
    不应从模型提交中直接取得。目录查询只读登记元数据，不读行情。
    """
    def __init__(self, roots: dict, access_recorder):
        if not callable(access_recorder):
            raise ValueError('DATA_ACCESS_RECORDER_REQUIRED')
        self.roots, self._datasets, self.record = {}, {}, access_recorder
        for key, value in roots.items():
            path = Path(value).absolute()
            if path.resolve() != path or not path.is_dir():
                raise ValueError('DATA_ROOT_INVALID')
            self.roots[key] = path

    def _path(self, root, relative):
        value = Path(relative)
        if value.is_absolute() or '..' in value.parts:
            raise ValueError('DATA_PATH_OUTSIDE_ROOT')
        path = root / value
        if path.resolve() != path or not path.is_file():
            raise ValueError('DATA_PATH_INVALID_OR_REDIRECTED')
        return path

    def register(self, dataset_id: str, root_id: str, manifest_path: str):
        self.register_manifest(dataset_id, root_id, self._path(self.roots[root_id], manifest_path))

    def register_manifest(self, dataset_id: str, root_id: str, manifest_path):
        """维护者可将清单放在独立工作区；内容路径仍只能位于已登记原件根。"""
        if dataset_id in self._datasets or not re.fullmatch(r'[A-Za-z0-9_-]+', dataset_id):
            raise ValueError('DATASET_ID_INVALID_OR_DUPLICATE')
        root = self.roots[root_id]
        metadata = Path(manifest_path).absolute()
        if metadata.resolve() != metadata or not metadata.is_file():
            raise ValueError('DATA_MANIFEST_PATH_INVALID_OR_REDIRECTED')
        raw = metadata.read_bytes()
        manifest = json.loads(raw)
        if manifest.get('adapter') != 'BAOSTOCK_RESPONSE_JSON_V1':
            raise ValueError('DATA_ADAPTER_UNSUPPORTED')
        if (not isinstance(manifest.get('files'), dict) or not manifest['files']
                or not manifest.get('symbols') or day(manifest['start']) > day(manifest['end'])):
            raise ValueError('DATA_MANIFEST_INVALID')
        for name, item in manifest['files'].items():
            self._path(root, name)
            if (not re.fullmatch(r'[0-9a-f]{64}', item.get('sha256', ''))
                    or day(item['start']) > day(item['end'])):
                raise ValueError('DATA_SOURCE_IDENTITY_INVALID')
        self._datasets[dataset_id] = (root, deepcopy(manifest), hashlib.sha256(raw).hexdigest())

    def catalog(self):
        return {'schema_version': 'RESEARCH_DATA_PROVIDER_V1', 'content_read': False,
            'datasets': [{'dataset_id': key, 'symbols': deepcopy(value[1]['symbols']),
                'start': value[1]['start'], 'end': value[1]['end'], 'adapter': value[1]['adapter'],
                'metadata_hash': value[2], 'historical_independence': 'UNKNOWN',
                'independent_confirmation_eligible': False, 'data_qualification': 'CONTENT_NOT_VALIDATED',
                'limitations': ['原件待检查；历史可见性仅建模；停牌、非现金公司行动尚不支持。']}
                for key, value in sorted(self._datasets.items())]}

    def prepare(self, dataset_id, *, symbols, feature_start, account_start, account_end,
                purpose='EXPLORATORY', required_fields=None, authorization=None):
        if purpose != 'EXPLORATORY':
            raise ValueError('DATA_PURPOSE_NOT_QUALIFIED')
        if (not isinstance(symbols, (list, tuple)) or not symbols
                or any(not isinstance(s, str) or not re.fullmatch(r'(?:00[0-9]{4}\.SZ|60[0-9]{4}\.SH)', s) for s in symbols)
                or len(set(symbols)) != len(symbols)):
            raise ValueError('DATA_SYMBOLS_INVALID')
        if not isinstance(dataset_id, str) or dataset_id not in self._datasets:
            raise ValueError('DATASET_NOT_REGISTERED')
        root, manifest, manifest_hash = self._datasets[dataset_id]
        start, account, end = map(day, (feature_start, account_start, account_end))
        ResearchDataAccessGuard().check_range(start, end, 'provider request')
        if (not isinstance(authorization, dict) or authorization.get('purpose') != purpose
                or dataset_id not in authorization.get('dataset_ids', [])
                or not authorization.get('authorization_id') or not authorization.get('start') or not authorization.get('end')
                or start < day(authorization['start']) or end > day(authorization['end'])):
            raise ValueError('DATA_ACCESS_NOT_AUTHORIZED')
        if not set(symbols) <= set(manifest['symbols']):
            raise ValueError('DATA_UNIVERSE_NOT_COVERED')
        if start < day(manifest['start']) or end > day(manifest['end']):
            raise ValueError('DATA_WINDOW_NOT_COVERED')
        # 未声明依赖的旧调用保持旧契约；V3公共提交总是显式传入实际字段。
        required_fields = ('turn',) if required_fields is None else required_fields
        if not isinstance(required_fields, (list, tuple)) or not all(isinstance(f, str) for f in required_fields):
            raise ValueError('DATA_FIELD_UNSUPPORTED')
        if not set(required_fields) <= {'open','high','low','close','prev_close','volume','amount','turn'}:
            raise ValueError('DATA_FIELD_UNSUPPORTED')
        cache, hashes = {}, {}
        def read(path):
            name = path.relative_to(root).as_posix()
            if name not in manifest['files']:
                raise ValueError('DATA_SOURCE_NOT_REGISTERED:' + name)
            meta = manifest['files'][name]
            lo, hi = day(meta['start']), day(meta['end'])
            ResearchDataAccessGuard().check_range(lo, hi, 'provider whole source')
            if lo < day(authorization['start']) or hi > day(authorization['end']):
                raise ValueError('DATA_WHOLE_SOURCE_NOT_AUTHORIZED')
            if name not in cache:
                safe = self._path(root, name)
                self.record({'event': 'DATA_CONTENT_READ_ATTEMPT', 'dataset_id': dataset_id,
                    'source': name, 'expected_sha256': meta['sha256'], 'purpose': purpose,
                    'authorization_id': authorization['authorization_id'], 'start': lo, 'end': hi})
                raw = safe.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                if digest != meta['sha256']:
                    raise ValueError('DATA_SOURCE_CONTENT_CHANGED:' + name)
                cache[name], hashes[name] = raw, digest
            return cache[name]
        def response(path, api):
            value = json.loads(read(path).decode('utf-8-sig'))
            if (value.get('provider') != 'BaoStock' or value.get('api') != api
                    or value.get('error_code') != '0'
                    or value.get('historical_available_at_verified') is not False):
                raise ValueError('DATA_VENDOR_RESPONSE_INVALID')
            if any(len(row) != len(value['fields']) for row in value['raw_rows']):
                raise ValueError('DATA_VENDOR_RESPONSE_SHAPE_INVALID')
            digest = hashlib.sha256(json.dumps(value['raw_rows'], ensure_ascii=False,
                separators=(',', ':')).encode()).hexdigest()
            if digest != value['raw_rows_sha256']:
                raise ValueError('DATA_VENDOR_ROWS_HASH_INVALID')
            return value, pd.DataFrame(value['raw_rows'], columns=value['fields'])
        window, bundle, checks = _baostock_bundle(root, symbols=symbols,
            feature_start=start, account_start=account, account_end=end, response=response,
            file_hash=lambda path: hashlib.sha256(read(path)).hexdigest(), required_fields=required_fields)
        qualification = {'purpose': purpose, 'account_data_ready': True,
            'historical_availability': 'MODELED', 'historical_independence': 'UNKNOWN',
            'independent_confirmation_eligible': False, 'strategy_qualified': False,
            'field_status': {'prices': 'VENDOR_ASSERTED', 'turn': ('VENDOR_ASSERTED' if 'turn' in bundle['turn'] and bundle['turn']['turn'].notna().all() else 'UNKNOWN'),
                'security_status': 'VENDOR_ASSERTED_WITH_MODELED_ELIGIBILITY',
                'action_dates': 'VENDOR_ASSERTED', 'availability_time': 'MODELED'},
            'price_basis': {'execution': 'RAW', 'indicators': 'CAUSAL_CASH_ACTION_TRANSFORM'},
            'checks': checks, 'source_hashes': hashes, 'manifest_hash': manifest_hash}
        from .rule_account_backend_v2 import rule_input_identity
        identity = rule_input_identity(bundle, window)
        self.record({'event': 'DATA_BUNDLE_PREPARED', 'dataset_id': dataset_id,
            'authorization_id': authorization['authorization_id'], 'purpose': purpose,
            'input_identity': identity, 'source_hashes': hashes, 'window': window})
        return {'window': window, 'bundle': bundle, 'qualification': qualification,
                'input_identity': identity}

def _baostock_bundle(data_root, *, symbols, feature_start, account_start, account_end, response, file_hash, required_fields=('turn',)):
    """先固定真实日历，再验原始响应与公司行动，不访问策略表现。"""
    if (not isinstance(symbols, (list, tuple)) or not symbols or len(symbols) != len(set(symbols))
            or any(not isinstance(s, str) or not re.fullmatch(r'(00\d{4}\.SZ|60\d{4}\.SH)', s) for s in symbols)):
        raise ValueError('HISTORICAL_MAIN_BOARD_SYMBOLS_INVALID')
    symbols = sorted(symbols)
    raw_calendar, calendar = response(data_root / 'TRADE_DATES.json', 'query_trade_dates')
    expected_dates = raw_calendar['request']
    if day(expected_dates['start_date']) > feature_start or day(expected_dates['end_date']) < account_end:
        raise ValueError('DATA_CALENDAR_REQUEST_COVERAGE')
    if raw_calendar['request'] != expected_dates:
        raise ValueError('HISTORICAL_CALENDAR_REQUEST_CHANGED')
    natural_days = pd.date_range(expected_dates['start_date'], expected_dates['end_date']).strftime('%Y-%m-%d').tolist()
    if (calendar.calendar_date.tolist() != natural_days
            or not calendar.is_trading_day.isin(['0', '1']).all()):
        raise ValueError('HISTORICAL_CALENDAR_RESPONSE_INCOMPLETE')
    dates = [day(x) for x in calendar.loc[calendar.is_trading_day == '1', 'calendar_date']]
    dates = [d for d in dates if feature_start <= d <= account_end]
    if (not dates or dates != sorted(set(dates)) or dates[0] != feature_start
            or dates[-1] != account_end or account_start not in dates
            or dates.index(account_start) < 60 or account_start >= account_end):
        raise ValueError('DATA_WINDOW_OR_WARMUP_INVALID')
    window = dict(symbols=symbols, feature_start=dates[0], account_start=account_start,
                  account_end=dates[-1], calendar=dates)
    frames, turns, states, events, action_checks = [], [], [], [], []
    sources = {'execution_profile': 'HISTORICAL_MODELED',
               'TRADE_DATES.json': file_hash(data_root / 'TRADE_DATES.json')}
    for symbol in symbols:
        code = ('sz.' if symbol.endswith('SZ') else 'sh.') + symbol[:6]
        name = f'DAILY_{symbol}.json'
        raw, frame = response(data_root / name, 'query_history_k_data_plus')
        if any(raw['request'].get(k) != v for k, v in
               {**expected_dates, 'code': code, 'frequency': 'd', 'adjustflag': '3'}.items()):
            raise ValueError('HISTORICAL_DAILY_REQUEST_CHANGED')
        sources[name] = file_hash(data_root / name)
        frame['date'] = frame.date.map(day)
        frame = frame.loc[frame.date.isin(dates)].copy().sort_values('date')
        if (frame.date.tolist() != dates or not frame.code.eq(code).all()
                or not frame.adjustflag.eq('3').all()
                or not frame.tradestatus.eq('1').all() or not frame.isST.isin(['0', '1']).all()):
            raise ValueError('HISTORICAL_DAILY_COVERAGE_OR_STATE_INVALID')
        if 'turn' in required_fields and 'turn' not in frame:
            raise ValueError('DATA_REQUIRED_FIELD_MISSING:turn')
        numeric = ['open', 'high', 'low', 'close', 'preclose', 'volume', 'amount'] + (['turn'] if 'turn' in frame else [])
        for key in numeric:
            frame[key] = pd.to_numeric(frame[key], errors='raise')
        if not np.isfinite(frame[numeric]).all().all():
            raise ValueError('HISTORICAL_NONFINITE_DATA')
        if (('turn' in frame and (frame.turn < 0).any()) or (frame.volume <= 0).any()
                or (frame[['open', 'high', 'low', 'close', 'preclose']] <= 0).any().any()
                or (frame.amount < 0).any()
                or (frame.high < frame[['open', 'close', 'low']].max(axis=1)).any()
                or (frame.low > frame[['open', 'close', 'high']].min(axis=1)).any()):
            raise ValueError('HISTORICAL_TURN_OR_VOLUME_INVALID')
        frame['symbol'] = symbol
        frame = frame.rename(columns={'preclose': 'prev_close'})
        frames.append(frame[['symbol', 'date', 'open', 'high', 'low', 'close', 'prev_close', 'volume', 'amount', 'adjustflag']])
        turn = frame[['symbol', 'date', 'volume'] + (['turn'] if 'turn' in frame else [])].copy()
        turn['tradestatus'] = 1
        turns.append(turn)
        for row in frame.loc[frame.date >= account_start].to_dict('records'):
            states.append(dict(symbol=symbol, trade_date=row['date'], listed=True, delisted=False,
                universe_member=True, eligibility_status='INELIGIBLE' if row['isST'] == '1' else 'ELIGIBLE',
                st_status='ST' if row['isST'] == '1' else 'NORMAL', suspension_status='TRADING',
                board='SZ_MAIN' if symbol.endswith('SZ') else 'SH_MAIN'))
        for year in range(feature_start // 10000, account_end // 10000 + 1):
            name = f'DIVIDEND_{symbol}_{year}.json'
            raw, actions = response(data_root / name, 'query_dividend_data')
            if raw['request'] != dict(code=code, year=str(year), yearType='report'):
                raise ValueError('HISTORICAL_ACTION_REQUEST_CHANGED')
            sources[name] = file_hash(data_root / name)
            for row in actions.to_dict('records'):
                effective = day(row['dividOperateDate'])
                if not dates[0] <= effective <= dates[-1]:
                    continue
                record = day(row['dividRegistDate'])
                if (row['code'] != code or float(row['dividStocksPs'] or 0) != 0
                        or float(row['dividReserveToStockPs'] or 0) != 0 or row['dividStockMarketDate']
                        or day(row['dividPayDate']) != effective or record not in dates
                        or day(row['dividPlanDate']) > record):
                    raise ValueError('HISTORICAL_NONCASH_OR_UNSUPPORTED_ACTION')
                cash_per_share = float(row['dividCashPsBeforeTax'])
                if not np.isfinite(cash_per_share) or cash_per_share <= 0:
                    raise ValueError('HISTORICAL_CASH_AMOUNT_INVALID')
                events.append(dict(event_id=f'{symbol}:{effective}:CASH', symbol=symbol,
                    event_type='CASH_DIVIDEND', record_date=record, effective_date=effective,
                    payment_date=effective, units='CNY_PER_SHARE',
                    source=f'BaoStock:query_dividend_data:{code}:{year}:report:sha256:{sources[name]}',
                    source_published_at=row['dividPlanDate'],
                    terms={'cash_per_share': cash_per_share,
                           'tax_rule': {'kind': 'DEFERRED_INDIVIDUAL_2015_101', 'source': TAX_SOURCE}}))
        own = {e['effective_date']: e for e in events if e['symbol'] == symbol}
        if len(own) != len([e for e in events if e['symbol'] == symbol]):
            raise ValueError('HISTORICAL_DUPLICATE_ACTION')
        name = f'ADJUST_{symbol}.json'
        raw, factors = response(data_root / name, 'query_adjust_factor')
        if raw['request'] != {**expected_dates, 'code': code} or not factors.code.eq(code).all():
            raise ValueError('HISTORICAL_ADJUST_REQUEST_CHANGED')
        factor_dates = [day(value) for value in factors.dividOperateDate]
        if (factor_dates != sorted(set(factor_dates))
                or any(not day(expected_dates['start_date']) <= d <= day(expected_dates['end_date']) for d in factor_dates)
                or {d for d in factor_dates if dates[0] <= d <= dates[-1]} != set(own)):
            raise ValueError('HISTORICAL_CORPORATE_COVERAGE_CONFLICT')
        sources[name] = file_hash(data_root / name)
        rows = frame.to_dict('records')
        for before, current in zip(rows, rows[1:]):
            action = own.get(current['date'])
            cash = action['terms']['cash_per_share'] if action else 0.
            delta = current['prev_close'] - (before['close'] - cash)
            if abs(delta) > .011 or (action and action['record_date'] != before['date']):
                raise ValueError(f'HISTORICAL_UNEXPLAINED_PRICE_REFERENCE:{symbol}:{current["date"]}')
            if action:
                action_checks.append({'event_id': action['event_id'], 'reference_delta': delta})
    # 因子无法覆盖全池时整体保持UNKNOWN，避免concat将缺列变为伪造的NaN数值字段。
    if any('turn' not in frame for frame in turns):
        turns = [frame.drop(columns=['turn'], errors='ignore') for frame in turns]
    bundle = dict(profile='HISTORICAL_MODELED', daily=pd.concat(frames, ignore_index=True),
        turn=pd.concat(turns, ignore_index=True), states=pd.DataFrame(states), events=events,
        calendar=dates, corporate_actions_complete=True, source_hashes=sources,
        open_snapshots=[], close_snapshots=[])
    return window, bundle, {'cash_action_reference_checks': action_checks,
        'coverage_basis': 'VENDOR_CASH_ACTION_RECORDS_AND_EVERY_RAW_PRECLOSE_TRANSITION',
        'publication_dates': 'VENDOR_ASSERTED_NOT_INDEPENDENTLY_VERIFIED',
        'historical_available_at_verified': False}

