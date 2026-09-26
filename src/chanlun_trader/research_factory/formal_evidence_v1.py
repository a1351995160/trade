"""正式确认只消费冻结后逐日捕获的证据；不把历史清白声明当成证据。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

import pandas as pd

from .bounded_research_v1 import _put, _read
from .common import stable_hash
from .forward_snapshot_v1 import SnapshotStoreV1, _stamp, _number, _symbol_row, _flag, assert_tdx_ready
from .mutation_boundary import ObjectiveMutationLock


def _now():
    return datetime.now(timezone.utc)


def _require(condition, reason):
    if not condition:
        raise ValueError('FORMAL_EVIDENCE_' + reason)


def _day(value):
    _require(type(value) is int and re.fullmatch(r'\d{8}', str(value)), 'DATE_INVALID')
    return int(pd.Timestamp(str(value)).strftime('%Y%m%d'))


def _calendar_dates(response, start, end):
    values = response.get('Value') if isinstance(response, dict) else response
    _require(isinstance(values, list) and values, 'CALENDAR_EMPTY_OR_INVALID')
    dates = [int(str(value).replace('-', '')) for value in values]
    _require(dates == sorted(set(dates)) and all(start <= _day(day) <= end for day in dates),
             'CALENDAR_SEQUENCE_INVALID')
    return dates


class CalendarEvidenceStoreV1:
    def __init__(self, root):
        self.root = Path(root).absolute()
        _require(self.root.resolve() == self.root, 'CALENDAR_ROOT_REDIRECTED')

    def _path(self, identity):
        _require(isinstance(identity, str) and re.fullmatch(r'CAL_[0-9a-f]{64}', identity), 'CALENDAR_ID_INVALID')
        path = self.root / (identity + '.json')
        _require(path.resolve() == path, 'CALENDAR_PATH_REDIRECTED')
        return path

    def _record(self, body):
        identity = 'CAL_' + stable_hash(body)
        with ObjectiveMutationLock.for_resource(self.root / 'calendar-store'):
            _put(self._path(identity), {**body, 'calendar_id': identity})
        return self.load(identity)

    def record_synthetic(self, *, start, end, dates, received_at):
        start, end = _day(start), _day(end)
        raw = {'synthetic_dates': list(dates)}
        return self._record({'schema_version':'FORMAL_CALENDAR_EVIDENCE_V1', 'profile':'SYNTHETIC',
            'provider':'SYNTHETIC', 'start':start, 'end':end, 'calendar':list(dates),
            'received_at':_stamp(received_at).isoformat(), 'source_responses':raw,
            'source_response_hash':stable_hash(raw)})

    def capture_tdx(self, start, end):
        start, end = _day(start), _day(end)
        started = _stamp(_now())
        _require(start <= end <= int(started.strftime('%Y%m%d')), 'CALENDAR_FUTURE_RANGE')
        assert_tdx_ready()
        from ..data.tdx.tq_client import TQClient
        client = TQClient(use_cache=False, retries=1, timeout=10)
        raw = []
        for method, params in [('get_match_stkinfo', {'key_word':'茅台'}),
                               ('get_trading_calendar', {'market':'SH','start_time':str(start),'end_time':str(end)})]:
            result = client.request(method, params, use_cache=False)
            raw.append({'method':method, 'params':params, 'result':result,
                        'received_at':_stamp(_now()).isoformat()})
        finished = _stamp(_now())
        _require(0 <= (finished-started).total_seconds() <= 60, 'CALENDAR_CAPTURE_TOO_SLOW')
        return self._record({'schema_version':'FORMAL_CALENDAR_EVIDENCE_V1', 'profile':'REAL_OBSERVED',
            'provider':'TDX_TQ_LOCAL_UNCACHED_V1', 'start':start, 'end':end,
            'calendar':_calendar_dates(raw[-1]['result'],start,end),
            'capture_started_at':started.isoformat(), 'received_at':finished.isoformat(),
            'source_responses':raw, 'source_response_hash':stable_hash(raw)})

    def load(self, calendar_id):
        value = _read(self._path(calendar_id))
        _require(value.get('calendar_id') == calendar_id == 'CAL_' + stable_hash(
            {k:v for k,v in value.items() if k != 'calendar_id'}), 'CALENDAR_HASH_CONFLICT')
        _require(value.get('schema_version') == 'FORMAL_CALENDAR_EVIDENCE_V1', 'CALENDAR_SCHEMA')
        start, end = _day(value['start']), _day(value['end'])
        _require(start <= end, 'CALENDAR_RANGE_INVALID')
        _calendar_dates(value['calendar'], start, end)
        raw = value['source_responses']
        _require(value['source_response_hash'] == stable_hash(raw), 'CALENDAR_SOURCE_HASH')
        received = _stamp(value['received_at'])
        if value['profile'] == 'REAL_OBSERVED':
            _require(value['provider'] == 'TDX_TQ_LOCAL_UNCACHED_V1' and isinstance(raw,list) and len(raw)==2,
                     'CALENDAR_REAL_SOURCE_REQUIRED')
            started = _stamp(value['capture_started_at'])
            _require(0 <= (received-started).total_seconds() <= 60
                     and end <= int(received.strftime('%Y%m%d')), 'CALENDAR_CAPTURE_TIME_INVALID')
            expected = [('get_match_stkinfo', {'key_word':'茅台'}),
                        ('get_trading_calendar', {'market':'SH','start_time':str(start),'end_time':str(end)})]
            previous = started
            for item, (method, params) in zip(raw, expected):
                stamp = _stamp(item['received_at'])
                _require(item['method'] == method and item['params'] == params
                         and previous <= stamp <= received, 'CALENDAR_SOURCE_REQUEST_CONFLICT')
                previous = stamp
            _require(value['calendar'] == _calendar_dates(raw[-1]['result'], start,end), 'CALENDAR_SOURCE_CONFLICT')
        else:
            _require(value['profile'] == 'SYNTHETIC' and value['provider'] == 'SYNTHETIC'
                     and raw == {'synthetic_dates':value['calendar']}, 'CALENDAR_PROFILE_INVALID')
        return value


def _verify_real_close(snapshot, symbols):
    """重放既有capture_tdx的CLOSE规范，合成payload不能只改真假标签冒充采集。"""
    raw = snapshot['source_responses']
    is_close = snapshot['phase'] == 'CLOSE'
    _require(snapshot['provider'] == 'TDX_TQ_LOCAL_UNCACHED_V1' and isinstance(raw,list)
             and len(raw) == 2+(4 if is_close else 3)*len(symbols), 'REAL_CAPTURE_SOURCE_REQUIRED')
    started, finished = _stamp(snapshot['capture_started_at']), _stamp(snapshot['received_at'])
    _require(started.date() == finished.date() and 0 <= (finished-started).total_seconds() <= 60,
             'CAPTURE_INTERVAL_INVALID')
    minute = started.hour*60+started.minute
    _require((900 <= minute <= 1020) if is_close else (570 <= minute <= 575), 'CAPTURE_OUTSIDE_SESSION')
    previous, index = started, 0
    def request(method, params):
        nonlocal previous, index
        item = raw[index]
        index += 1
        stamp = _stamp(item['received_at'])
        _require(item['method'] == method and item['params'] == params and previous <= stamp <= finished,
                 'CAPTURE_REQUEST_CONFLICT')
        previous = stamp
        return item['result']
    day = snapshot['market_date']
    request('get_match_stkinfo', {'key_word':'茅台'})
    calendar = request('get_trading_calendar', {'market':'SH','start_time':str(day),'end_time':str(day)})
    _require(_calendar_dates(calendar,day,day) == [day], 'CAPTURE_NOT_EXCHANGE_SESSION')
    rebuilt = {'bars':[], 'turn':[], 'states':[], 'corporate_actions':[], 'corporate_actions_complete':True}
    # 捕获请求序列跟原快照symbol顺序一致，调用方证券顺序不影响身份。
    for symbol in [row['symbol'] for row in snapshot['payload']['bars']]:
        quote = _symbol_row(request('get_market_snapshot', {'stock_code':symbol}),symbol)
        info = _symbol_row(request('get_stock_info', {'stock_code':symbol}),symbol)
        more = _symbol_row(request('get_more_info', {'stock_code':symbol,'field_list':['HqDate','TPFlag','fHSL']}),symbol)
        _require(int(str(more['HqDate']).replace('-','')) == day and _number(info['DelayMin']) == 0,
                 'STALE_OR_DELAYED_SOURCE')
        _require(str(info['HSStockKind']) == '1', 'MAIN_BOARD_REQUIRED')
        _require(not _flag(info['TodayDRFlag']), 'CORPORATE_ACTION_UNRESOLVED')
        suspended = _flag(more['TPFlag'])
        rebuilt['states'].append({'symbol':symbol,'date':day,
            'listed':int(str(info['J_start']).replace('-','')) <= day, 'delisted':_flag(info['IsQuitGP']),
            'is_st':_flag(info['IsSTGP']), 'board':'MAIN', 'suspended':suspended})
        if is_close:
            daily = _symbol_row(request('get_market_data', {'stock_list':[symbol],'period':'1d',
                'start_time':str(day),'end_time':str(day),'count':0,'dividend_type':'none','fill_data':False}),symbol)
            _require(daily['Date'] in ([day],[str(day)]), 'DAILY_DATE_CONFLICT')
            bar = {'symbol':symbol,'date':day,'prev_close':_number(quote['LastClose'],positive=True),
                **{key:_number(daily[field][0]) for key,field in [('open','Open'),('high','High'),('low','Low'),
                    ('close','Close'),('volume','Volume')]}, 'amount':_number(daily['Amount'][0])*10000,
                'amount_unit':'CNY','price_basis':'RAW_CLOSE'}
        else:
            price = _number(quote['Now'],positive=True)
            bar = {'symbol':symbol,'date':day,'open':price,'high':price,'low':price,'close':price,
                'volume':_number(quote['Volume'])*100,'amount':_number(quote['Amount']),
                'prev_close':_number(quote['LastClose'],positive=True), 'price_basis':'OBSERVED_NOW',
                'amount_unit':'TDX_NATIVE_NOT_USED_AT_OPEN'}
        rebuilt['bars'].append(bar)
        rebuilt['turn'].append({'symbol':symbol,'date':day,'turn':_number(more['fHSL']),
                                'tradestatus':0 if suspended else 1})
    _require(rebuilt == snapshot['payload'], 'CAPTURE_PAYLOAD_NOT_REPLAYABLE')


def build_confirmation_bundle(*, snapshot_root, snapshot_ids, calendar_root, calendar_id, symbols, open_snapshot_ids=None,
                              not_before, frozen_at, warmup_sessions=60, account_sessions=504,
                              profile='REAL_OBSERVED'):
    _require(profile in ('REAL_OBSERVED','SYNTHETIC'), 'PROFILE_INVALID')
    _require(type(warmup_sessions) is int and warmup_sessions >= 1 and type(account_sessions) is int
             and account_sessions >= 2, 'SESSION_COUNT_INVALID')
    symbols = tuple(sorted(symbols))
    _require(symbols and len(symbols) == len(set(symbols))
             and all(re.fullmatch(r'\d{6}\.(SH|SZ)', symbol) for symbol in symbols), 'SYMBOLS_INVALID')
    frozen = _stamp(frozen_at)
    not_before = _day(not_before)
    _require(not_before > int(frozen.strftime('%Y%m%d')), 'WINDOW_NOT_FUTURE_FROZEN')
    _require(len(snapshot_ids) == len(set(snapshot_ids)) == warmup_sessions+account_sessions,
             'EXACT_SNAPSHOT_COUNT_REQUIRED')
    # 独立确认使用登记的专用capture目录；多余capture不能被选择性忽略。
    _require(isinstance(open_snapshot_ids,(list,tuple)) and len(open_snapshot_ids)==len(set(open_snapshot_ids))==account_sessions,
             'EXACT_OPEN_SNAPSHOT_COUNT_REQUIRED')
    inventory = {path.stem for path in Path(snapshot_root).glob('SNAP_*.json')}
    _require(inventory == set(snapshot_ids)|set(open_snapshot_ids), 'EXTRA_OR_REVISED_CAPTURE')
    calendar = CalendarEvidenceStoreV1(calendar_root).load(calendar_id)
    _require(calendar['profile'] == profile and _stamp(calendar['received_at']) > frozen,
             'CALENDAR_PROFILE_OR_FREEZE_CONFLICT')
    snapshots = [SnapshotStoreV1(snapshot_root).load(identity) for identity in snapshot_ids]
    opens = [SnapshotStoreV1(snapshot_root).load(identity) for identity in open_snapshot_ids]
    dates = [snapshot['market_date'] for snapshot in snapshots]
    _require(dates == sorted(set(dates)), 'DUPLICATE_OR_REORDERED_DATES')
    _require(calendar['start'] == not_before and calendar['end'] == dates[-1]
             and calendar['calendar'] == dates, 'CALENDAR_COVERAGE_CONFLICT')
    _require([item['market_date'] for item in opens] == dates[warmup_sessions:], 'OPEN_SESSION_COVERAGE_CONFLICT')
    daily, turnover, states = [], [], []
    previous_closes, close_references = {}, {}
    for snapshot in snapshots:
        day, payload = snapshot['market_date'], snapshot['payload']
        received = _stamp(snapshot['received_at'])
        _require(snapshot['phase'] == 'CLOSE' and snapshot['profile'] == profile and received > frozen
                 and day >= not_before and 900 <= received.hour*60+received.minute <= 1020,
                 'SNAPSHOT_PROFILE_TIME_OR_PHASE_CONFLICT')
        _require(all(set(row['symbol'] for row in payload[key]) == set(symbols)
                     for key in ('bars','turn','states')), 'SYMBOL_COVERAGE_CONFLICT')
        _require(payload['corporate_actions_complete'] is True and payload['corporate_actions'] == [],
                 'CORPORATE_ACTION_UNRESOLVED')
        _require(all(row['board'] == 'MAIN' for row in payload['states']), 'MAIN_BOARD_REQUIRED')
        if profile == 'REAL_OBSERVED':
            _verify_real_close(snapshot,symbols)
        for row in payload['bars']:
            symbol = row['symbol']
            reference = _number(row['prev_close'],positive=True)
            if symbol in previous_closes:
                _require(abs(reference-previous_closes[symbol]) <= 0.011,
                         'UNEXPLAINED_PRICE_REFERENCE')
            previous_closes[symbol] = _number(row['close'],positive=True)
            close_references[(day,symbol)] = reference
        volume = {row['symbol']:row['volume'] for row in payload['bars']}
        daily.extend({**row,'adjustflag':'3','available_at':received.isoformat(),
                      'source_snapshot_id':snapshot['snapshot_id']} for row in payload['bars'])
        turnover.extend({**row,'volume':volume[row['symbol']],'available_at':received.isoformat(),
                         'source_snapshot_id':snapshot['snapshot_id']} for row in payload['turn'])
    for snapshot in opens:
        day, payload = snapshot['market_date'], snapshot['payload']
        received = _stamp(snapshot['received_at'])
        _require(snapshot['phase']=='OPEN' and snapshot['profile']==profile and received>frozen
                 and 570<=received.hour*60+received.minute<=575, 'OPEN_PROFILE_OR_TIME_CONFLICT')
        _require(all(set(row['symbol'] for row in payload[key])==set(symbols) for key in ('bars','turn','states')),
                 'OPEN_SYMBOL_COVERAGE_CONFLICT')
        _require(payload['corporate_actions_complete'] is True and payload['corporate_actions']==[],
                 'CORPORATE_ACTION_UNRESOLVED')
        _require(all(row['board']=='MAIN' for row in payload['states']), 'MAIN_BOARD_REQUIRED')
        if profile=='REAL_OBSERVED':
            _verify_real_close(snapshot,symbols)
        for row in payload['bars']:
            _require(abs(_number(row['prev_close'],positive=True)-close_references[(day,row['symbol'])]) <= 0.011,
                     'UNEXPLAINED_PRICE_REFERENCE')
        for row in payload['states']:
            eligible = row['listed'] and not row['delisted'] and not row['is_st'] and not row['suspended']
            states.append({**row,'trade_date':day,'universe_member':row['listed'] and not row['delisted'],
                'eligibility_status':'ELIGIBLE' if eligible else 'INELIGIBLE',
                'st_status':'ST' if row['is_st'] else 'NORMAL',
                'suspension_status':'SUSPENDED' if row['suspended'] else 'TRADING',
                'available_at':received.isoformat(),'observed_at':received.isoformat(),
                'source_evidence_kind':'ACTUAL_CAPTURE','source_snapshot_id':snapshot['snapshot_id']})
    hashes = {'daily_sha256':stable_hash(daily),'turn_sha256':stable_hash(turnover),
              'states_sha256':stable_hash(states),'historical_states_sha256':stable_hash(states),
              'corporate_actions_sha256':stable_hash([]), 'calendar_sha256':stable_hash(calendar)}
    evidence = {'profile':profile,'snapshot_ids':list(snapshot_ids),'open_snapshot_ids':list(open_snapshot_ids),'calendar_id':calendar_id,
                'source_hashes':hashes,'frozen_at':frozen.isoformat(),'not_before':not_before,
                'dates':dates,'symbols':list(symbols),'calendar_hash':stable_hash(calendar),
                'snapshot_hashes':{snapshot['snapshot_id']:snapshot['snapshot_hash'] for snapshot in snapshots+opens},
                'independent_window':profile=='REAL_OBSERVED',
                'corporate_action_scope':'NO_OBSERVED_ACTION_FLAGS; UNRESOLVED_ACTIONS_REJECTED',
                'historical_clean_claim_accepted':False,'strategy_qualified':False,
                'qualification_evidence_eligible':profile=='REAL_OBSERVED'}
    identity = stable_hash(evidence)
    window = {'symbols':list(symbols),'feature_start':dates[0],'account_start':dates[warmup_sessions],
              'account_end':dates[-1],'calendar':dates}
    bundle = {'daily':pd.DataFrame(daily), 'turn':pd.DataFrame(turnover), 'states':pd.DataFrame(states),
              'calendar':dates, 'open_snapshots':opens, 'close_snapshots':snapshots,
              'events':(), 'corporate_actions_complete':True,
              'source_hashes':hashes,'input_identity':identity,'account_end_date':dates[-1]}
    return {'bundle':bundle,'window':window,'evidence':evidence}
