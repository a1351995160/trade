"""用来源绑定的限定解释核验公司行动日期差异；未知日期仍保留。"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from chanlun_trader.research.guard import ResearchDataAccessGuard
from chanlun_trader.research_factory.research_universe_v1 import _day
from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1
from scripts.prepare_universe_actions_v1 import _ACTION_FIELDS, _query_scope, _read_response, _response_header
from scripts.prepare_universe_supplements_v1 import (
    _RAW_FIELDS, _append, _load, _query_header, _sha, _write, read_account_raw_response_v1,
)

VERSION = 'UNIVERSE_ACTION_DATE_RESOLUTION_V1'
VERIFIED = 'VERIFIED_NON_EVENT_FACTOR_ROW'
DATE_CONFLICT = 'ADJUST_AND_ACTION_DATES_CONFLICT'
FACTORS = ('foreAdjustFactor', 'backAdjustFactor', 'adjustFactor')


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _positive(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('ACTION_FACTOR_OR_PRICE_INVALID') from exc
    if not result.is_finite() or result <= 0:
        raise ValueError('ACTION_FACTOR_OR_PRICE_INVALID')
    return result


def _continuity(market_rows, day, calendar):
    own = [row for row in market_rows if _day(row.get('date')) == day]
    prior_days = [session for session in calendar if session < day]
    previous = prior_days[-1] if prior_days else None
    prior = [row for row in market_rows if _day(row.get('date')) == previous]
    evidence = {'previous_session': previous, 'current_market_rows': own,
                'previous_market_rows': prior, 'preclose_equals_previous_close': False}
    if len(own) == len(prior) == 1:
        evidence['preclose_equals_previous_close'] = (
            own[0].get('tradestatus') == prior[0].get('tradestatus') == '1'
            and _positive(own[0].get('preclose')) == _positive(prior[0].get('close')))
    return evidence


def interpret_factor_row_v1(rows, index, *, action_dates, listing_date,
                            market_rows, calendar):
    """只解释该因子行，不宣称该证券当日所有其他事件都不存在。"""
    current = rows[index]
    day = _day(current['dividOperateDate'])
    values = tuple(_positive(current[field]) for field in FACTORS)
    if day in action_dates:
        return 'UNKNOWN', 'DATE_IS_AN_ACTUAL_DIVIDEND_SOURCE_DATE', {}
    first_market = min((_day(row.get('date')) for row in market_rows), default=None)
    detail = {'first_market_date': first_market, 'master_listing_date': listing_date}
    if index == 0 and values[1] == values[2] == 1:
        # 原件查询须覆盖上市前；调用者核验查询和完整市场响应。
        if day == listing_date == first_market:
            return VERIFIED, 'INITIAL_OBSERVED_QUOTE_UNIT_FACTOR_ROW', detail
        continuity = _continuity(market_rows, day, calendar)
        detail.update(continuity)
        if (listing_date == first_market and first_market is not None and first_market < day
                and continuity['preclose_equals_previous_close']):
            return VERIFIED, 'INITIAL_UNIT_FACTOR_ANCHOR', detail
        return 'UNKNOWN', 'UNIT_FACTOR_ANCHOR_MARKET_EVIDENCE_INCOMPLETE', detail
    if index == 0:
        return 'UNKNOWN', 'INITIAL_NONUNIT_FACTOR_WITHOUT_ACTION', detail
    previous = rows[index - 1]
    previous_values = tuple(_positive(previous[field]) for field in FACTORS)
    detail['previous_factor_date'] = _day(previous['dividOperateDate'])
    if values == previous_values:
        return VERIFIED, 'REPEATED_CUMULATIVE_FACTORS', detail
    if values[:2] == previous_values[:2]:
        detail.update(_continuity(market_rows, day, calendar))
        if (previous_values[2] == 1 and values[2] == values[1]
                and detail['previous_factor_date'] in action_dates
                and detail['preclose_equals_previous_close']):
            return VERIFIED, 'UNIT_ANCHOR_TO_UNCHANGED_CUMULATIVE_FACTORS', detail
        return 'UNKNOWN', 'UNCHANGED_FORE_BACK_WITH_UNEXPLAINED_ADJUST_FACTOR', detail
    return 'UNKNOWN', 'CUMULATIVE_FACTORS_CHANGE_WITHOUT_DIVIDEND_SOURCE', detail


def _action_source(source, audit):
    item = dict(source)
    # SOURCE_CATALOG 的 physical_* 是 V1 验证过的范围，不把年度查询范围
    # 冒充原件所有计划/支付字段的物理范围。
    ResearchDataAccessGuard().check_range(source['physical_start'], source['physical_end'],
                                        'action resolution registered source')
    _query_scope(source['api'], source['request'])
    if source['origin'] == 'LEGACY_REGISTERED_ORIGINAL_RESPONSE':
        item['physical_metadata'] = {'start': source['physical_start'], 'end': source['physical_end']}
    header = _response_header(source['path'])
    rows, evidence = _read_response(item, audit)
    raw_rows = [[row[field] for field in header['fields']] for row in rows]
    binding = {'path': evidence['path'], 'sha256': evidence['sha256'],
        'api': source['api'], 'request': source['request'], 'header': header,
        'normalized_rows_hash': _hash(rows), 'normalized_rows_sha256': _hash(rows),
        'raw_rows_sha256': hashlib.sha256(json.dumps(raw_rows, ensure_ascii=False,
                                                    separators=(',', ':')).encode()).hexdigest(),
        'physical_start': source['physical_start'], 'physical_end': source['physical_end'],
        'request_verified': evidence['request_verified'], 'source_year_type': evidence['source_year_type']}
    return rows, binding


def _registered_catalog(manifest_path, manifest, path, kind):
    registrations = [item.get('evidence', {}) for item in manifest['files'].values()
                     if item['kind'] == kind]
    if not registrations:
        raise ValueError('ACTION_RESOLUTION_CATALOG_REGISTRATION_REQUIRED')
    digest = _sha(path)
    for evidence in registrations:
        registered = UniverseDataProviderV1._path(manifest_path.parent, evidence['source_catalog_path'])
        if registered != Path(path).absolute() or digest != evidence['source_catalog_sha256']:
            raise ValueError('ACTION_RESOLUTION_REGISTERED_CATALOG_CHANGED')


def _action_source_readonly(source):
    """复验被既有 catalog 绑定的原件；不创建审计文件或改写来源。"""
    guard = ResearchDataAccessGuard()
    lo, hi = _day(source['physical_start']), _day(source['physical_end'])
    guard.check_range(lo, hi, 'resolution proof whole registered action original')
    physical = {'start': lo, 'end': hi} if source['origin'] == 'LEGACY_REGISTERED_ORIGINAL_RESPONSE' else None
    _query_scope(source['api'], source['request'], physical)
    header = _response_header(source['path'])
    _query_scope(header['api'], header['request'], physical)
    if (header.get('provider') != 'BaoStock' or header['api'] != source['api']
            or header['request'] != source['request']):
        raise ValueError('ACTION_RESOLUTION_ORIGINAL_HEADER_CHANGED')
    if _sha(source['path']) != source['sha256']:
        raise ValueError('ACTION_RESOLUTION_ORIGINAL_SHA_CHANGED')
    value = _load(source['path'])
    fields, raw = value.get('fields'), value.get('raw_rows')
    required = _ACTION_FIELDS if source['kind'] == 'DIVIDEND' else {'code', 'dividOperateDate', *FACTORS}
    if (value.get('error_code') != '0' or not isinstance(fields, list)
            or len(fields) != len(set(fields)) or not required <= set(fields)
            or not isinstance(raw, list) or value.get('request') != source['request']
            or value.get('historical_available_at_verified') is not False
            or len(raw) != source.get('row_count')):
        raise ValueError('ACTION_RESOLUTION_ORIGINAL_RESPONSE_INVALID')
    raw_sha = hashlib.sha256(json.dumps(raw, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    if raw_sha != value.get('raw_rows_sha256'):
        raise ValueError('ACTION_RESOLUTION_RAW_ROWS_SHA_CHANGED')
    rows = []
    for original in raw:
        if not isinstance(original, list) or len(original) != len(fields):
            raise ValueError('ACTION_RESOLUTION_ORIGINAL_ROW_INVALID')
        row = dict(zip(fields, original))
        if row['code'] != source['request']['code']:
            raise ValueError('ACTION_RESOLUTION_ORIGINAL_SECURITY_CHANGED')
        for name, item in row.items():
            if name.endswith('Date') and item:
                day = _day(item)
                guard.check_date(day, 'resolution proof original date field')
                if physical and not lo <= day <= hi:
                    raise ValueError('ACTION_RESOLUTION_ORIGINAL_PHYSICAL_SCOPE_CONFLICT')
        day = _day(row['dividOperateDate'])
        if source['kind'] == 'ADJUST' and not lo <= day <= hi:
            raise ValueError('ACTION_RESOLUTION_ADJUST_ROW_OUTSIDE_QUERY')
        if (source['kind'] == 'DIVIDEND' and source.get('source_year_type') == 'operate'
                and (day is None or day // 10000 != int(source['request']['year']))):
            raise ValueError('ACTION_RESOLUTION_OPERATE_YEAR_CONFLICT')
        rows.append(row)
    binding = {'path': str(Path(source['path']).absolute()), 'sha256': source['sha256'],
        'api': source['api'], 'request': source['request'], 'header': header,
        'normalized_rows_hash': _hash(rows), 'normalized_rows_sha256': _hash(rows),
        'raw_rows_sha256': raw_sha, 'physical_start': source['physical_start'],
        'physical_end': source['physical_end'], 'request_verified': source['request_verified'],
        'source_year_type': source['source_year_type']}
    return rows, binding


def _market_source_readonly(source, plan):
    if source.get('format') == 'BAOSTOCK_COLLECTOR_RAW_V1':
        from chanlun_trader.research_factory.baostock_raw_supplement_v1 import read_collected_raw_v1
        return read_collected_raw_v1(source, plan)
    guard = ResearchDataAccessGuard()
    guard.check_range(_day(source['start']), _day(source['end']), 'resolution proof registered raw market')
    path = Path(source['path']).absolute()
    started_path, access_path = path.with_name('3.started.json'), path.with_name('3.access.json')
    started, access = _load(started_path), _load(access_path)
    query = started.get('query')
    if not isinstance(query, dict):
        raise ValueError('ACTION_RESOLUTION_MARKET_QUERY_INVALID')
    lo, hi = _day(query.get('start_date')), _day(query.get('end_date'))
    guard.check_range(lo, hi, 'resolution proof original raw market query')
    original_query = _query_header(path)
    guard.check_range(_day(original_query.get('start_date')), _day(original_query.get('end_date')),
                      'resolution proof original raw market header')
    symbol = source['symbol']
    code, exchange = symbol.split('.')
    if (query != original_query or query['code'] != exchange.lower() + '.' + code
            or query.get('frequency') != 'd' or query.get('adjustflag') != '3'
            or query['start_date'] != plan['start'] or query['end_date'] != plan['end']
            or symbol not in plan['symbols'] or not _RAW_FIELDS <= set(query.get('fields', '').split(','))
            or (lo, hi) != (source['start'], source['end'])
            or Path(access.get('path', '')).absolute() != path
            or access.get('error_code') != '0' or access.get('sha256') != source['sha256']):
        raise ValueError('ACTION_RESOLUTION_MARKET_QUERY_OR_ACCESS_CHANGED')
    if (_sha(started_path) != source['started_sha256'] or _sha(access_path) != source['access_sha256']
            or _sha(path) != source['sha256']):
        raise ValueError('ACTION_RESOLUTION_MARKET_OR_RECEIPT_SHA_CHANGED')
    value = _load(path)
    rows = value.get('rows')
    if (value.get('query') != query or value.get('error_code') != '0'
            or not _RAW_FIELDS <= set(value.get('fields', [])) or not isinstance(rows, list)
            or len(rows) != source['row_count'] or len(rows) != access.get('row_count')):
        raise ValueError('ACTION_RESOLUTION_MARKET_RESPONSE_INVALID')
    seen = set()
    for row in rows:
        day = _day(row.get('date'))
        if (day is None or not lo <= day <= hi or day in seen or row.get('code') != query['code']
                or row.get('adjustflag') != '3'):
            raise ValueError('ACTION_RESOLUTION_MARKET_ROW_INVALID')
        guard.check_date(day, 'resolution proof raw market date')
        seen.add(day)
    captured = datetime.fromisoformat(value['completed_at'])
    if captured.tzinfo is None or captured.utcoffset().total_seconds() != 0:
        raise ValueError('ACTION_RESOLUTION_MARKET_CAPTURE_TIME_INVALID')
    binding = {'symbol': symbol, 'path': str(path), 'sha256': source['sha256'],
        'started_sha256': source['started_sha256'], 'access_sha256': source['access_sha256'],
        'start': lo, 'end': hi, 'row_count': len(rows),
        'source_captured_at_utc': value['completed_at'], 'format': 'BAOSTOCK_ACCOUNT_RAW_V1',
        'historical_available_at_verified': False,
        'query_covers_before_first_bar': lo < min((_day(row['date']) for row in rows), default=0)}
    return rows, binding


def verify_action_date_resolution_receipt_v1(receipt_path, source_catalog, action_gaps, manifest):
    """只读重算每条限定解释，返回可消费的 (symbol, effective_date) 集合。"""
    receipt = _load(receipt_path)
    if (receipt.get('version') != VERSION
            or receipt.get('algorithm', {}).get('sha256') != _sha(Path(__file__))):
        raise ValueError('ACTION_RESOLUTION_ALGORITHM_SOURCE_CHANGED')
    inputs = {'input_catalog': source_catalog, 'input_gaps': action_gaps, 'manifest': manifest}
    for key, path in inputs.items():
        binding = receipt[key]
        if Path(binding['path']).absolute() != Path(path).absolute() or binding['sha256'] != _sha(path):
            raise ValueError('ACTION_RESOLUTION_PROOF_INPUT_CHANGED:' + key)
    catalog, gaps, metadata = map(_load, (source_catalog, action_gaps, manifest))
    manifest_path = Path(manifest).absolute()
    _registered_catalog(manifest_path, metadata, source_catalog, 'CORPORATE_ACTION_COVERAGE')
    start, end = receipt['window']['start'], receipt['window']['end']
    ResearchDataAccessGuard().check_range(start, end, 'action resolution proof window')
    calendar, calendar_binding = _calendar(manifest_path, metadata, start, end)
    if calendar_binding != receipt['calendar']:
        raise ValueError('ACTION_RESOLUTION_CALENDAR_BINDING_CHANGED')
    market_path = Path(receipt['market_catalog']['path'])
    _registered_catalog(manifest_path, metadata, market_path, 'REFERENCE_PRICES')
    if _sha(market_path) != receipt['market_catalog']['sha256']:
        raise ValueError('ACTION_RESOLUTION_MARKET_CATALOG_CHANGED')
    market_catalog = _load(market_path)
    plan_path = market_catalog['read_plan_path']
    if _sha(plan_path) != market_catalog['read_plan_sha256']:
        raise ValueError('ACTION_RESOLUTION_MARKET_PLAN_CHANGED')
    plan = _load(plan_path)
    market_sources = {item['symbol']: item for item in market_catalog['responses']}
    masters = {row['symbol']: row for row in metadata['master']['records']}
    original = {(gap['symbol'], gap['effective_date']): gap for gap in gaps if gap['reason'] == DATE_CONFLICT
                and start <= gap['effective_date'] <= end}
    rows = receipt['resolutions']
    if (len(rows) != len(original) or len({(row['symbol'], row['effective_date']) for row in rows}) != len(rows)
            or {(row['symbol'], row['effective_date']) for row in rows} != set(original)):
        raise ValueError('ACTION_RESOLUTION_PROOF_GAP_IDENTITY_CHANGED')
    action_cache, market_cache, accepted = {}, {}, set()
    for row in rows:
        symbol, day = row['symbol'], row['effective_date']
        if row['original_gap'] != original[(symbol, day)]:
            raise ValueError('ACTION_RESOLUTION_ORIGINAL_GAP_CHANGED')
        if row['status'] != VERIFIED:
            continue
        if (row.get('source_errors') or row.get('global_no_other_event_claim') is not False
                or row.get('ipo_independently_verified') is not False
                or row.get('does_not_imply_new_adjustment') is not True):
            raise ValueError('ACTION_RESOLUTION_SCOPE_FLAGS_CHANGED')
        if symbol not in action_cache:
            own = []
            for source in catalog['sources']:
                if source['symbol'] == symbol:
                    raw, binding = _action_source_readonly(source)
                    if binding['request_verified'] is not True:
                        raise ValueError('ACTION_RESOLUTION_REQUEST_VERIFICATION_REQUIRED')
                    own.append((source, raw, binding))
            action_cache[symbol] = own
        if symbol not in market_cache:
            market_cache[symbol] = _market_source_readonly(market_sources[symbol], plan)
        market_rows, market_binding = market_cache[symbol]
        if market_binding != row['market_evidence']:
            raise ValueError('ACTION_RESOLUTION_MARKET_EVIDENCE_CHANGED')
        own = action_cache[symbol]
        dividends = [(source, raw, binding) for source, raw, binding in own if source['kind'] == 'DIVIDEND']
        if [binding for _, _, binding in dividends] != row['dividend_sources']:
            raise ValueError('ACTION_RESOLUTION_DIVIDEND_BINDING_CHANGED')
        action_dates = {_day(item['dividOperateDate']) for _, raw, binding in dividends for item in raw
                        if binding['source_year_type'] == 'operate'}
        adjusts = [(source, raw, binding) for source, raw, binding in own if source['kind'] == 'ADJUST'
                   and _day(source['request']['start_date']) <= start
                   and _day(source['request']['end_date']) >= end]
        bindings = []
        for source, raw, binding in adjusts:
            dates = [_day(item['dividOperateDate']) for item in raw]
            if dates != sorted(set(dates)) or day not in dates:
                raise ValueError('ACTION_RESOLUTION_FACTOR_DATE_AXIS_CHANGED')
            index = dates.index(day)
            bindings.append({**binding, 'row_index': index, 'raw_row': raw[index],
                             'previous_raw_row': raw[index - 1] if index else None})
            status, meaning, detail = interpret_factor_row_v1(raw, index, action_dates=action_dates,
                listing_date=_day(masters.get(symbol, {}).get('listing_date')),
                market_rows=market_rows, calendar=calendar)
            if meaning in {'INITIAL_OBSERVED_QUOTE_UNIT_FACTOR_ROW', 'INITIAL_UNIT_FACTOR_ANCHOR'}:
                if not market_binding['query_covers_before_first_bar']:
                    raise ValueError('ACTION_RESOLUTION_LISTING_START_NOT_PROVEN')
            if (status != VERIFIED or meaning != row['interpretation']
                    or detail != row['factor_and_market_comparison']):
                raise ValueError('ACTION_RESOLUTION_PREDICATE_NOT_REPRODUCED')
        if not bindings or bindings != row['adjust_sources']:
            raise ValueError('ACTION_RESOLUTION_ADJUST_BINDING_CHANGED')
        accepted.add((symbol, day))
    return accepted


def _calendar(manifest_path, manifest, start, end):
    entries = [(name, item) for name, item in manifest['files'].items() if item['kind'] == 'CALENDAR']
    if len(entries) != 1:
        raise ValueError('ACTION_RESOLUTION_SINGLE_REGISTERED_CALENDAR_REQUIRED')
    name, item = entries[0]
    ResearchDataAccessGuard().check_range(_day(item['start']), _day(item['end']),
                                        'action resolution calendar source')
    path = UniverseDataProviderV1._path(manifest_path.parent, name)
    if _sha(path) != item['sha256']:
        raise ValueError('ACTION_RESOLUTION_CALENDAR_SHA_CHANGED')
    days = _load(path)
    if (not isinstance(days, list) or days != sorted(set(days))
            or any(not _day(item['start']) <= _day(day) <= _day(item['end']) for day in days)
            or _day(item['start']) > start or _day(item['end']) < end):
        raise ValueError('ACTION_RESOLUTION_CALENDAR_INVALID')
    return days, {'path': str(path), 'sha256': item['sha256']}


def resolve_universe_action_dates_v1(*, source_catalog, gaps, manifest, output_dir,
                                      market_catalog=None, feature_start=20220801,
                                      account_end=20240731):
    guard = ResearchDataAccessGuard()
    start, end = _day(feature_start), _day(account_end)
    if start is None or end is None or start >= end:
        raise ValueError('ACTION_RESOLUTION_WINDOW_INVALID')
    guard.check_range(start, end, 'action resolution window')
    catalog_path, gap_path, manifest_path = (Path(path).absolute()
                                          for path in (source_catalog, gaps, manifest))
    catalog, original_gaps, metadata = map(_load, (catalog_path, gap_path, manifest_path))
    _registered_catalog(manifest_path, metadata, catalog_path, 'CORPORATE_ACTION_COVERAGE')
    selected = [gap for gap in original_gaps if gap['reason'] == DATE_CONFLICT
                and start <= gap['effective_date'] <= end]
    keys = [(gap['symbol'], gap['effective_date']) for gap in selected]
    if len(keys) != len(set(keys)):
        raise ValueError('ACTION_RESOLUTION_DUPLICATE_GAP_IDENTITY')
    output = Path(output_dir).absolute()
    if output.resolve() != output or output.exists():
        raise ValueError('ACTION_RESOLUTION_NEW_OUTPUT_REQUIRED')
    calendar, calendar_evidence = _calendar(manifest_path, metadata, start, end)
    masters = {row['symbol']: row for row in metadata['master']['records']}
    if market_catalog is None:
        references = [item for item in metadata['files'].values() if item['kind'] == 'REFERENCE_PRICES']
        if len(references) != 1:
            raise ValueError('ACTION_RESOLUTION_MARKET_CATALOG_REQUIRED')
        evidence = references[0]['evidence']
        market_catalog = UniverseDataProviderV1._path(manifest_path.parent, evidence['source_catalog_path'])
        if _sha(market_catalog) != evidence['source_catalog_sha256']:
            raise ValueError('ACTION_RESOLUTION_MARKET_CATALOG_SHA_CHANGED')
    market_path = Path(market_catalog)
    market = _load(market_path)
    plan_path = Path(market['read_plan_path'])
    if _sha(plan_path) != market['read_plan_sha256']:
        raise ValueError('ACTION_RESOLUTION_MARKET_PLAN_SHA_CHANGED')
    plan = _load(plan_path)
    market_sources = {item['symbol']: item for item in market['responses']}
    if len(market_sources) != len(market['responses']):
        raise ValueError('ACTION_RESOLUTION_DUPLICATE_MARKET_SOURCE')
    output.mkdir(parents=True)
    audit = output / 'PHYSICAL_READ_EVENTS.jsonl'
    by_symbol = defaultdict(list)
    relevant = {symbol for symbol, _ in keys}
    failures = defaultdict(list)
    for source in catalog['sources']:
        if source['symbol'] not in relevant:
            continue
        try:
            rows, binding = _action_source(source, audit)
            if binding['request_verified'] is not True:
                raise ValueError('ACTION_RESOLUTION_REQUEST_NOT_VERIFIED')
            by_symbol[source['symbol']].append((source, rows, binding))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            failures[source['symbol']].append({'path': source['path'], 'reason': str(exc)})
    market_by_symbol, market_bindings = {}, {}
    for symbol in sorted(relevant):
        try:
            source = market_sources[symbol]
            if source.get('format') == 'BAOSTOCK_COLLECTOR_RAW_V1':
                _append(audit, {'event': 'ACTION_RESOLUTION_COLLECTOR_RAW_READ_ATTEMPT',
                    'symbol': symbol, 'path': source['path'], 'sha256': source['sha256']})
                rows, binding = _market_source_readonly(source, plan)
                _append(audit, {'event': 'ACTION_RESOLUTION_COLLECTOR_RAW_READ_VERIFIED',
                    'symbol': symbol, 'path': binding['path'], 'sha256': binding['sha256']})
            else:
                root = Path(source['path']).parents[2]
                rows, binding = read_account_raw_response_v1(root, symbol, plan, audit)
                if any(binding[key] != source[key] for key in ('path', 'sha256', 'started_sha256', 'access_sha256')):
                    raise ValueError('ACTION_RESOLUTION_MARKET_BINDING_CHANGED')
            if _day(plan['start']) >= min((_day(row['date']) for row in rows), default=0):
                # 不能用截窗首行来证明上市起点；重复因子仍可核对同日配对价格。
                binding['query_covers_before_first_bar'] = False
            else:
                binding['query_covers_before_first_bar'] = True
            market_by_symbol[symbol], market_bindings[symbol] = rows, binding
        except (ValueError, OSError, KeyError, TypeError) as exc:
            failures[symbol].append({'kind': 'MARKET', 'reason': str(exc)})
    resolutions, queue = [], []
    for gap in selected:
        symbol, day = gap['symbol'], gap['effective_date']
        own = by_symbol[symbol]
        dividends = [(source, rows, binding) for source, rows, binding in own if source['kind'] == 'DIVIDEND']
        action_dates = {_day(row['dividOperateDate']) for source, rows, binding in dividends for row in rows
                        if binding['source_year_type'] == 'operate'}
        adjusts = [(source, rows, binding) for source, rows, binding in own if source['kind'] == 'ADJUST'
                   and _day(source['request']['start_date']) <= start
                   and _day(source['request']['end_date']) >= end]
        row = {'symbol': symbol, 'effective_date': day, 'original_gap': gap,
            'status': 'UNKNOWN', 'interpretation': 'SOURCE_EVIDENCE_INCOMPLETE',
            'adjust_sources': [], 'dividend_sources': [binding for _, _, binding in dividends],
            'market_evidence': market_bindings.get(symbol), 'source_errors': failures[symbol],
            'global_no_other_event_claim': False, 'provider_internal_generation_cause': 'UNKNOWN',
            'historical_available_at_verified': False, 'independent_confirmation_eligible': False,
            'ipo_independently_verified': False}
        decisions = []
        for source, raw, binding in adjusts:
            dates = [_day(value['dividOperateDate']) for value in raw]
            matching = [index for index, date in enumerate(dates) if date == day]
            if dates != sorted(set(dates)):
                decisions.append(('UNKNOWN', 'ADJUST_DATE_AXIS_INVALID', {}))
                continue
            if not matching:
                row['adjust_sources'].append(binding)
                decisions.append(('UNKNOWN', 'DIVIDEND_DATE_WITHOUT_ADJUST_ROW', {}))
                continue
            index = matching[0]
            row['adjust_sources'].append({**binding, 'row_index': index, 'raw_row': raw[index],
                                         'previous_raw_row': raw[index - 1] if index else None})
            try:
                decision = interpret_factor_row_v1(raw, index, action_dates=action_dates,
                    listing_date=_day(masters.get(symbol, {}).get('listing_date')),
                    market_rows=market_by_symbol.get(symbol, []), calendar=calendar)
                if (decision[1] in {'INITIAL_OBSERVED_QUOTE_UNIT_FACTOR_ROW', 'INITIAL_UNIT_FACTOR_ANCHOR'}
                        and not market_bindings.get(symbol, {}).get('query_covers_before_first_bar')):
                    decision = ('UNKNOWN', 'MARKET_QUERY_DOES_NOT_PROVE_LISTING_START', decision[2])
                decisions.append(decision)
            except (ValueError, KeyError, TypeError) as exc:
                decisions.append(('UNKNOWN', str(exc), {}))
        if decisions and not failures[symbol]:
            if len({_hash(decision) for decision in decisions}) == 1:
                row['status'], row['interpretation'], row['factor_and_market_comparison'] = decisions[0]
            else:
                row['interpretation'] = 'ADJUST_SOURCE_EXPLANATIONS_CONFLICT'
                row['competing_explanations'] = decisions
        row['does_not_imply_new_adjustment'] = row['status'] == VERIFIED
        resolutions.append(row)
        if row['status'] == 'UNKNOWN':
            queue.append({'symbol': symbol, 'effective_date': day,
                'type': 'ADJUST_RESPONSE_AND_PRIMARY_ACTION_NOTICE' if day in action_dates else
                        'PRIMARY_ACTION_NOTICE_AND_FACTOR_FIELD_SEMANTICS',
                'reason': row['interpretation'], 'automatic_execution': False,
                'scope': {'start': day, 'end': day}, 'preserve_original_gap': True})
    receipt = {'version': VERSION, 'window': {'start': start, 'end': end},
        'algorithm': {'path': str(Path(__file__).absolute()), 'sha256': _sha(Path(__file__))},
        'input_catalog': {'path': str(catalog_path.absolute()), 'sha256': _sha(catalog_path)},
        'input_gaps': {'path': str(gap_path.absolute()), 'sha256': _sha(gap_path)},
        'manifest': {'path': str(manifest_path.absolute()), 'sha256': _sha(manifest_path)},
        'market_catalog': {'path': str(market_path.absolute()), 'sha256': _sha(market_path)},
        'calendar': calendar_evidence, 'master_completeness_evidence': metadata['master'].get('completeness_evidence'),
        'resolutions': resolutions,
        'original_rows_modified': False, 'account_executed': False,
        'historical_available_at_verified': False, 'independent_confirmation_eligible': False}
    summary = {'version': VERSION, 'difference_date_count': len(resolutions),
        'difference_symbol_count': len(relevant),
        'status_counts': dict(Counter(row['status'] for row in resolutions)),
        'interpretation_counts': dict(Counter(row['interpretation'] for row in resolutions)),
        'targeted_supplement_request_count': len(queue),
        'receipt': str(output / 'ACTION_DATE_RESOLUTION_RECEIPT.json')}
    _write(output / 'ACTION_DATE_RESOLUTION_RECEIPT.json', receipt)
    _write(output / 'TARGETED_DATE_EVIDENCE_QUEUE.json', {'version': VERSION, 'requests': queue,
        'request_count': len(queue), 'automatic_execution': False, 'automatic_retry': False})
    _write(output / 'RESOLUTION_SUMMARY.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ('source-catalog', 'gaps', 'manifest', 'output-dir'):
        parser.add_argument('--' + argument, required=True)
    parser.add_argument('--market-catalog')
    parser.add_argument('--feature-start', type=int, default=20220801)
    parser.add_argument('--account-end', type=int, default=20240731)
    result = resolve_universe_action_dates_v1(**vars(parser.parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
