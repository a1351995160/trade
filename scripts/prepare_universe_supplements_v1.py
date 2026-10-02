"""核验已有 BaoStock 原件并准备全股票池状态、参考价和逐股补采清单。"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from chanlun_trader.research.guard import ResearchDataAccessGuard
from chanlun_trader.presentation import ZhCNPresentation
from chanlun_trader.research_factory.research_universe_v1 import (
    BOARDS, _day, canonical_symbol, scope_board,
)
from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1


VERSION = 'UNIVERSE_BAOSTOCK_SUPPLEMENTS_V1'
STATE_POLICY = 'MODELED_SAME_SESSION_VENDOR_STATE'
STATE_SOURCE = 'baostock_account_raw_state'
REFERENCE_SOURCE = 'baostock_account_raw_reference'
_RAW_FIELDS = {'date', 'code', 'open', 'high', 'low', 'close', 'preclose',
               'volume', 'amount', 'adjustflag', 'tradestatus', 'isST'}
_EXPLICIT_AVAILABILITY_FIELDS = {'available_at', 'effective_available_at', 'researcher_available_at'}
_STATE_SCHEMA = pa.schema([
    ('symbol', pa.string()), ('trade_date', pa.int64()), ('listed', pa.bool_()),
    ('delisted', pa.bool_()), ('universe_member', pa.bool_()),
    ('eligibility_status', pa.string()), ('st_status', pa.string()),
    ('suspension_status', pa.string()), ('board', pa.string()),
    ('source', pa.string()), ('availability_status', pa.string()),
    ('available_at', pa.string()), ('historical_available_at_verified', pa.bool_()),
    ('source_availability_policy', pa.string()), ('source_captured_at_utc', pa.string()),
    ('listing_date', pa.int64()), ('delisting_date', pa.int64()),
    ('listing_date_source', pa.string()), ('original_response_path', pa.string()),
    ('original_response_sha256', pa.string()), ('master_manifest_sha256', pa.string()),
])
_REFERENCE_SCHEMA = pa.schema([
    ('symbol', pa.string()), ('date', pa.int64()), ('prev_close', pa.float64()),
    ('source', pa.string()), ('original_response_path', pa.string()),
    ('original_response_sha256', pa.string()), ('pairing_basis', pa.string()),
])


def _safe(path):
    path = Path(path).absolute()
    if path.resolve() != path or not path.is_file():
        raise ValueError('SUPPLEMENT_SOURCE_MISSING_OR_REDIRECTED:' + str(path))
    return path


def _sha(path):
    with _safe(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _load(path):
    return json.loads(_safe(path).read_text(encoding='utf-8-sig'))


def _write(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')


def _append(path, value):
    with path.open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n')


def _query_header(path):
    """无缓冲停在 rows 之前；原件自己声明的全查询范围也先经过守卫。"""
    prefix = bytearray()
    with _safe(path).open('rb', buffering=0) as stream:
        while len(prefix) < 65536:
            value = stream.read(1)
            if not value:
                break
            prefix.extend(value)
            marker = re.search(rb'"rows"\s*:\s*\[$', prefix)
            if marker:
                try:
                    return json.loads((prefix[:marker.start()] + b'"rows":[]}').decode('utf-8'))['query']
                except (ValueError, KeyError, TypeError) as exc:
                    raise ValueError('SUPPLEMENT_RAW_QUERY_HEADER_INVALID') from exc
    raise ValueError('SUPPLEMENT_RAW_QUERY_HEADER_REQUIRED')


def read_account_raw_response_v1(response_root, symbol, plan, access_log):
    """支持既有 account 原件格式，原件、started 和 SHA 回执分别核对。"""
    root = Path(response_root).absolute()
    code, exchange = symbol.split('.')
    path = root / 'responses' / symbol / '3.json'
    started_path, access_path = path.with_name('3.started.json'), path.with_name('3.access.json')
    started, access = _load(started_path), _load(access_path)
    query = started.get('query')
    if isinstance(query, dict) and _EXPLICIT_AVAILABILITY_FIELDS & set(str(query.get('fields', '')).split(',')):
        raise ValueError('SUPPLEMENT_RAW_EXPLICIT_AVAILABILITY_UNSUPPORTED')
    if (not isinstance(query, dict) or query.get('code') != exchange.lower() + '.' + code
            or query.get('frequency') != 'd' or query.get('adjustflag') != '3'
            or not _RAW_FIELDS <= set(str(query.get('fields', '')).split(','))
            or symbol not in plan.get('symbols', [])
            or query.get('start_date') != plan.get('start') or query.get('end_date') != plan.get('end')):
        raise ValueError('SUPPLEMENT_RAW_QUERY_IDENTITY_INVALID')
    guard = ResearchDataAccessGuard()
    lo, hi = _day(query['start_date']), _day(query['end_date'])
    if lo is None or hi is None or lo > hi:
        raise ValueError('SUPPLEMENT_RAW_QUERY_WINDOW_INVALID')
    guard.check_range(lo, hi, 'supplement whole BaoStock RAW query')
    original_query = _query_header(path)
    original_lo, original_hi = _day(original_query.get('start_date')), _day(original_query.get('end_date'))
    if original_lo is None or original_hi is None or original_lo > original_hi:
        raise ValueError('SUPPLEMENT_RAW_QUERY_HEADER_INVALID')
    guard.check_range(original_lo, original_hi,
                      'supplement original RAW query header')
    if original_query != query:
        raise ValueError('SUPPLEMENT_RAW_QUERY_CHANGED')
    if (access.get('error_code') != '0' or not re.fullmatch(r'[0-9a-f]{64}', access.get('sha256', ''))
            or Path(access.get('path', '')).absolute() != path):
        raise ValueError('SUPPLEMENT_RAW_RECEIPT_INVALID')
    before = _safe(path).stat()
    _append(access_log, {'event': 'BAOSTOCK_RAW_READ_ATTEMPT', 'path': str(path),
        'start': lo, 'end': hi, 'purpose': 'ENGINEERING_SUPPLEMENT_PREPARATION',
        'physical_query_header_checked_before_rows': True})
    digest = _sha(path)
    if digest != access['sha256']:
        raise ValueError('SUPPLEMENT_RAW_SHA_CHANGED')
    value = _load(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('SUPPLEMENT_RAW_CHANGED_DURING_READ')
    rows = value.get('rows')
    if _EXPLICIT_AVAILABILITY_FIELDS & set(value.get('fields', [])):
        raise ValueError('SUPPLEMENT_RAW_EXPLICIT_AVAILABILITY_UNSUPPORTED')
    if (value.get('query') != query or value.get('error_code') != '0'
            or not _RAW_FIELDS <= set(value.get('fields', []))
            or not isinstance(rows, list) or len(rows) != access.get('row_count')):
        raise ValueError('SUPPLEMENT_RAW_RESPONSE_INVALID')
    try:
        captured = pd.Timestamp(value['completed_at'])
        if captured.tzinfo is None or captured.utcoffset().total_seconds() != 0:
            raise ValueError()
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise ValueError('SUPPLEMENT_RAW_CAPTURE_TIME_INVALID') from exc
    seen = set()
    for row in rows:
        if isinstance(row, dict) and _EXPLICIT_AVAILABILITY_FIELDS & set(row):
            raise ValueError('SUPPLEMENT_RAW_EXPLICIT_AVAILABILITY_UNSUPPORTED')
        day = _day(row.get('date')) if isinstance(row, dict) else None
        if day is None or not lo <= day <= hi:
            raise ValueError('SUPPLEMENT_RAW_ROW_OUTSIDE_QUERY')
        guard.check_date(day, 'BaoStock original row')
        if day in seen or row.get('code') != query['code'] or row.get('adjustflag') != '3':
            raise ValueError('SUPPLEMENT_RAW_ROW_IDENTITY_INVALID')
        seen.add(day)
    evidence = {'symbol': symbol, 'path': str(path), 'sha256': digest,
        'started_sha256': _sha(started_path), 'access_sha256': _sha(access_path),
        'start': lo, 'end': hi, 'row_count': len(rows),
        'source_captured_at_utc': value.get('completed_at'),
        'format': 'BAOSTOCK_ACCOUNT_RAW_V1', 'historical_available_at_verified': False}
    _append(access_log, {'event': 'BAOSTOCK_RAW_READ_VERIFIED', **evidence})
    return rows, evidence


def _tdx_prices(manifest_path, manifest, start, end):
    """原文件先验证完整物理范围；只读取本次窗口的同日 OHLC 配对列。"""
    frames = []
    for name, item in manifest['files'].items():
        if item.get('kind') != 'DAILY':
            continue
        if item.get('format') != 'PARQUET':
            raise ValueError('SUPPLEMENT_TDX_PAIRING_REQUIRES_PARQUET')
        path = UniverseDataProviderV1._path(manifest_path.parent, name)
        guard = ResearchDataAccessGuard()
        guard.check_range(_day(item['start']), _day(item['end']), 'supplement TDX whole source')
        UniverseDataProviderV1._check_parquet_range(path, item, guard)
        if _sha(path) != item['sha256']:
            raise ValueError('SUPPLEMENT_TDX_SHA_CHANGED')
        columns = ['symbol', 'date', 'open', 'high', 'low', 'close']
        frame = pq.read_table(path, columns=columns, filters=[('date', '>=', start),
                              ('date', '<=', end)], use_threads=False).to_pandas(use_threads=False)
        frames.append(frame)
    prices = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=['symbol', 'date', 'open', 'high', 'low', 'close'])
    if prices.duplicated(['symbol', 'date']).any():
        raise ValueError('SUPPLEMENT_TDX_DUPLICATE_PAIRING_KEYS')
    return {symbol: part.set_index('date') for symbol, part in prices.groupby('symbol', sort=False)}


def _legacy_sources(catalog_path):
    """仅使用已登记的文件元信息，不读取公司行动正文或继承完成资格。"""
    if catalog_path is None:
        return {}
    catalog = _load(catalog_path)
    roots, datasets = catalog.get('roots', {}), catalog.get('datasets', [])
    result = {}
    for dataset in datasets:
        root = Path(roots[dataset['root_id']]).absolute()
        metadata = _load(dataset['manifest_path'])
        for name, item in metadata.get('files', {}).items():
            match = re.fullmatch(r'(DIVIDEND|ADJUST)_([0-9]{6}[.](?:SH|SZ))(?:_([0-9]{4}))?[.]json', name)
            if not match:
                continue
            path = UniverseDataProviderV1._path(root, name)
            kind, symbol, year = match.groups()
            result.setdefault((symbol, kind, int(year) if year else None), []).append({
                'path': str(path), 'sha256': item.get('sha256'),
                'declared_start': item.get('start'), 'declared_end': item.get('end'),
                'status': 'LOCAL_REQUIRES_VALIDATION', 'content_read': False})
    return result


def _state(row, symbol, master, evidence, master_sha):
    if _EXPLICIT_AVAILABILITY_FIELDS & set(row):
        raise ValueError('SUPPLEMENT_RAW_EXPLICIT_AVAILABILITY_UNSUPPORTED')
    day = _day(row['date'])
    listed_day, delisted_day = _day(master.get('listing_date')), _day(master.get('delisting_date'))
    listed = day >= listed_day and (delisted_day is None or day < delisted_day) if listed_day else None
    delisted = day >= delisted_day if delisted_day else False
    st = {'0': 'NORMAL', '1': 'ST'}.get(row.get('isST'), 'UNKNOWN')
    suspension = {'0': 'SUSPENDED', '1': 'TRADING'}.get(row.get('tradestatus'), 'UNKNOWN')
    if delisted:
        suspension = 'DELISTED'
    elif listed is False:
        suspension = 'NOT_LISTED'
    eligible = 'UNKNOWN' if listed is None or st == 'UNKNOWN' or suspension == 'UNKNOWN' else (
        'ELIGIBLE' if listed and not delisted and st == 'NORMAL' and suspension == 'TRADING' else 'INELIGIBLE')
    return {'symbol': symbol, 'trade_date': day, 'listed': listed, 'delisted': delisted,
        'universe_member': listed, 'eligibility_status': eligible, 'st_status': st,
        'suspension_status': suspension, 'board': scope_board(symbol), 'source': STATE_SOURCE,
        'availability_status': 'MODELED', 'available_at': 'MODELED',
        'historical_available_at_verified': False, 'source_availability_policy': STATE_POLICY,
        'source_captured_at_utc': evidence['source_captured_at_utc'],
        'listing_date': listed_day, 'delisting_date': delisted_day,
        'listing_date_source': STATE_SOURCE, 'original_response_path': evidence['path'],
        'original_response_sha256': evidence['sha256'], 'master_manifest_sha256': master_sha}


def _request(symbol, kind, start, end, year=None):
    code, market = symbol.split('.')
    vendor = market.lower() + '.' + code
    if kind == 'DIVIDEND':
        return {'file': f'DIVIDEND_{symbol}_{year}.json', 'api': 'query_dividend_data',
                'request': {'code': vendor, 'year': str(year), 'yearType': 'operate'}}
    return {'file': f'ADJUST_{symbol}.json', 'api': 'query_adjust_factor',
            'request': {'code': vendor, 'start_date': str(pd.Timestamp(str(start)).date()),
                        'end_date': str(pd.Timestamp(str(end)).date())}}


def prepare_universe_supplements_v1(*, manifest, response_root, output_dir,
                                   legacy_catalog=None, feature_start=20220801, account_end=20240731):
    """只物化来源资料和缺口；不运行策略、不修改预算、不开启后台采集。"""
    manifest_path, response_root = _safe(manifest), Path(response_root).absolute()
    output = Path(output_dir).absolute()
    if (output.resolve() != output or not output.is_relative_to(manifest_path.parent)
            or output == manifest_path.parent or output.exists()):
        raise ValueError('SUPPLEMENT_NEW_CHILD_OUTPUT_REQUIRED')
    new_manifest_path = manifest_path.parent / 'manifest_supplements_v1.json'
    if new_manifest_path.exists():
        raise ValueError('SUPPLEMENT_MANIFEST_ALREADY_EXISTS')
    start, end = _day(feature_start), _day(account_end)
    if start is None or end is None or start >= end:
        raise ValueError('SUPPLEMENT_REQUEST_WINDOW_INVALID')
    ResearchDataAccessGuard().check_range(start, end, 'supplement requested window')
    metadata = _load(manifest_path)
    if start < _day(metadata['start']) or end > _day(metadata['end']):
        raise ValueError('SUPPLEMENT_MANIFEST_WINDOW_NOT_COVERED')
    masters = {canonical_symbol(row['symbol']): row for row in metadata['master']['records']
               if scope_board(canonical_symbol(row['symbol'])) in BOARDS}
    plan = _load(response_root / 'READ_PLAN.json')
    plan_start, plan_end = _day(plan.get('start')), _day(plan.get('end'))
    if plan_start is None or plan_end is None:
        raise ValueError('SUPPLEMENT_READ_PLAN_WINDOW_INVALID')
    ResearchDataAccessGuard().check_range(plan_start, plan_end, 'supplement whole source READ_PLAN')
    legacy = _legacy_sources(legacy_catalog)
    prices = _tdx_prices(manifest_path, metadata, start, end)
    master_sha = _sha(manifest_path)
    output.mkdir()
    access_log = output / 'PHYSICAL_READ_EVENTS.jsonl'
    _write(output / 'PREPARATION_STARTED.json', {'version': VERSION, 'source_manifest': str(manifest_path),
        'source_manifest_sha256': master_sha, 'start': start, 'end': end,
        'state_policy': STATE_POLICY, 'independent_confirmation_eligible': False})
    source_rows, gaps, queue, counters = [], [], [], Counter()
    state_path, reference_path = output / 'states.parquet', output / 'reference_prices.parquet'
    with pq.ParquetWriter(state_path, _STATE_SCHEMA, compression='zstd') as states_writer, \
            pq.ParquetWriter(reference_path, _REFERENCE_SCHEMA, compression='zstd') as refs_writer:
        for symbol, master in sorted(masters.items()):
            reasons, states, references, pair_conflicts, raw_rows = [], [], [], [], []
            try:
                raw_rows, evidence = read_account_raw_response_v1(response_root, symbol, plan, access_log)
                source_rows.append(evidence)
            except (ValueError, FileNotFoundError) as exc:
                reasons.append('RAW_RESPONSE_UNAVAILABLE_OR_INVALID:' + str(exc))
                evidence = None
            vendor_days = []
            paired = prices.get(symbol)
            for row in raw_rows:
                day = _day(row['date'])
                if not start <= day <= end:
                    continue
                vendor_days.append(day)
                states.append(_state(row, symbol, master, evidence, master_sha))
                if paired is None or day not in paired.index:
                    continue
                source_bar = paired.loc[day]
                try:
                    match = all(math.isfinite(float(row[field])) and math.isclose(
                        float(row[field]), float(source_bar[field]), rel_tol=0, abs_tol=1e-8)
                        for field in ['open', 'high', 'low', 'close'])
                    reference = float(row['preclose'])
                    if not math.isfinite(reference) or reference <= 0:
                        raise ValueError()
                except (ValueError, TypeError, OverflowError, KeyError):
                    match = False
                if not match:
                    pair_conflicts.append(day)
                    continue
                references.append({'symbol': symbol, 'date': day, 'prev_close': reference,
                    'source': REFERENCE_SOURCE, 'original_response_path': evidence['path'],
                    'original_response_sha256': evidence['sha256'],
                    'pairing_basis': 'RAW_OHLC_MATCH_ABS_TOL_1E_8'})
            if states:
                states_writer.write_table(pa.Table.from_pylist(states, schema=_STATE_SCHEMA))
            if references:
                refs_writer.write_table(pa.Table.from_pylist(references, schema=_REFERENCE_SCHEMA))
            counters['states_rows'] += len(states)
            counters['reference_rows'] += len(references)
            counters['raw_responses_verified'] += evidence is not None
            counters['symbols_with_price_conflict'] += bool(pair_conflicts)
            if start < plan_start or end > plan_end:
                reasons.append('VENDOR_RAW_WINDOW_DOES_NOT_COVER_REQUEST')
            if not vendor_days:
                reasons.append('VENDOR_RAW_ROWS_MISSING_FOR_REQUEST')
            if pair_conflicts:
                reasons.append('TDX_BAOSTOCK_RAW_OHLC_CONFLICT')
            if any(state['eligibility_status'] == 'UNKNOWN' for state in states):
                reasons.append('VENDOR_STATE_OR_LISTING_UNKNOWN')
            for year in range(start // 10000, end // 10000 + 1):
                local = legacy.get((symbol, 'DIVIDEND', year), [])
                gaps.append({'symbol': symbol, 'year': year, 'kind': 'DIVIDEND',
                    'status': 'LOCAL_REQUIRES_VALIDATION' if local else 'LOCAL_SOURCE_MISSING',
                    'sources': local, 'desired_year_type': 'operate'})
                if not local:
                    queue.append(_request(symbol, 'DIVIDEND', start, end, year))
            adjust = legacy.get((symbol, 'ADJUST', None), [])
            gaps.append({'symbol': symbol, 'year': None, 'kind': 'ADJUST',
                'status': 'LOCAL_REQUIRES_VALIDATION' if adjust else 'LOCAL_SOURCE_MISSING',
                'start': start, 'end': end, 'sources': adjust})
            if not adjust:
                queue.append(_request(symbol, 'ADJUST', start, end))
            gaps.extend([
                {'symbol': symbol, 'year': None, 'kind': 'STATE_AND_REFERENCE',
                 'status': 'RAW_SOURCE_NORMALIZED' if not reasons and evidence else 'GAPS',
                 'reasons': reasons, 'normalized_state_rows': len(states),
                 'verified_reference_rows': len(references), 'raw_rows': len(vendor_days),
                 'price_conflict_dates': pair_conflicts,
                 'warmup_missing_start': start if start < plan_start else None,
                 'warmup_missing_end': int((pd.Timestamp(str(plan_start)) - pd.Timedelta(days=1)).strftime('%Y%m%d'))
                                      if start < plan_start else None},
                {'symbol': symbol, 'year': None, 'kind': 'TURN',
                 'status': 'OPTIONAL_STRATEGY_DEPENDENT_SOURCE_MISSING',
                 'required_only_if_indicator_requires_turn': True},
            ])
    batches = []
    for number, offset in enumerate(range(0, len(queue), 9), 1):
        batches.append({'batch_id': f'SUPPLEMENT_{number:05d}',
            'purpose': 'EXPLORATORY_ENGINEERING_ACCEPTANCE',
            'authorization_scope': 'USER_REQUEST_EXISTING_DATA_FIRST_AND_MISSING_SUPPLEMENTS',
            'max_requests': 9, 'requests': queue[offset:offset + 9],
            'execution_status': 'NOT_EXECUTED', 'dividend_year_type': 'operate',
            'collector': 'scripts/fetch_research_baostock_v1.py'})
    _write(output / 'SOURCE_CATALOG.json', {'version': VERSION, 'responses': source_rows,
        'read_plan_path': str(response_root / 'READ_PLAN.json'),
        'read_plan_sha256': _sha(response_root / 'READ_PLAN.json'),
        'historical_availability': 'MODELED', 'state_policy': STATE_POLICY,
        'legacy_sources_content_read': False, 'independent_confirmation_eligible': False})
    _write(output / 'PER_STOCK_YEAR_GAPS.json', gaps)
    _write(output / 'ACQUISITION_BATCHES.json', {'version': VERSION, 'request_count': len(queue),
        'batches': batches, 'automatic_execution': False, 'automatic_retry': False,
        'authorization_source': {'origin': 'USER_EXPLICIT_CURRENT_TASK',
            'statement': '接通后，再按“股票＋年份＋缺失资料”补齐全市场。按这个做吧',
            'scope': 'USER_REQUEST_EXISTING_DATA_FIRST_AND_MISSING_SUPPLEMENTS',
            'account_execution_authorized': False, 'independent_validation_authorized': False},
        'local_sources_require_validation_before_any_qualification': True})
    new_manifest = deepcopy(metadata)
    new_manifest['files'] = {name: value for name, value in metadata['files'].items()
                             if value['kind'] not in {'STATES', 'REFERENCE_PRICES'}}
    for path, kind, source_id in [(state_path, 'STATES', STATE_SOURCE),
                                  (reference_path, 'REFERENCE_PRICES', REFERENCE_SOURCE)]:
        new_manifest['files'][path.relative_to(manifest_path.parent).as_posix()] = {
            'kind': kind, 'format': 'PARQUET', 'source_id': source_id, 'sha256': _sha(path),
            'start': start, 'end': end, 'historical_availability': 'MODELED',
            'evidence': {'source_catalog_path': (output / 'SOURCE_CATALOG.json').relative_to(
                manifest_path.parent).as_posix(), 'source_catalog_sha256': _sha(output / 'SOURCE_CATALOG.json'),
                'state_policy': STATE_POLICY, 'original_manifest_sha256': master_sha}}
    new_manifest['listing_date_sources'] = {symbol: [STATE_SOURCE]
        for symbol in masters if _day(masters[symbol].get('listing_date')) is not None}
    new_manifest['supplement_preparation'] = {'version': VERSION, 'state_policy': STATE_POLICY,
        'original_manifest_sha256': master_sha, 'corporate_sources_validated': False,
        'historical_available_at_verified': False, 'independent_confirmation_eligible': False}
    _write(new_manifest_path, new_manifest)
    summary = {'version': VERSION, 'status': 'SUPPLEMENTS_PREPARED_WITH_CORPORATE_GAPS',
        'target_count': len(masters), **counters, 'feature_start': start, 'account_end': end,
        'raw_query_start': plan_start, 'raw_query_end': plan_end,
        'new_manifest': str(new_manifest_path), 'new_manifest_sha256': _sha(new_manifest_path),
        'acquisition_request_count': len(queue), 'acquisition_batch_count': len(batches),
        'corporate_actions_complete': False, 'account_data_ready': False,
        'independent_confirmation_eligible': False, 'strategy_qualified': False,
        'original_files_modified': False, 'account_executed': False, 'budget_created': False}
    _write(output / 'PREPARATION_COMPLETE.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--response-root', required=True)
    parser.add_argument('--legacy-catalog')
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--feature-start', type=int, default=20220801)
    parser.add_argument('--account-end', type=int, default=20240731)
    parser.add_argument('--json', action='store_true', help='输出机器 JSON；默认显示中文摘要。')
    args = parser.parse_args()
    options = vars(args)
    as_json = options.pop('json')
    result = prepare_universe_supplements_v1(**options)
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print('配套资料准备' + ZhCNPresentation.status_name('COMPLETED') + '。')
        print(f"目标 {result['target_count']} 只，已核验原件 {result['raw_responses_verified']} 份，"
              f"状态 {result['states_rows']} 行，配对参考价 {result['reference_rows']} 行。")
        print(f"待补采 {result['acquisition_request_count']} 个请求，"
              f"分为 {result['acquisition_batch_count']} 个批次；公司行动资料仍需核验。")
        print('新登记清单：' + result['new_manifest'])


if __name__ == '__main__':
    main()
