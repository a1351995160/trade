"""把已核验的年度公司行动原件接入全股票池，并保留不支持或缺资料的证券。"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from chanlun_trader.engine.individual_dividend_accounting_v1 import IndividualDividendAccountingV1
from chanlun_trader.presentation import ZhCNPresentation
from chanlun_trader.research.guard import FinalTestAccessViolation, ResearchDataAccessGuard
from chanlun_trader.research_factory.research_data_provider_v1 import TAX_SOURCE
from chanlun_trader.research_factory.research_universe_v1 import BOARDS, _day, canonical_symbol, scope_board
from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1
from scripts import collect_universe_gaps_v1 as collector
from scripts.prepare_universe_supplements_v1 import (
    VERSION as SUPPLEMENT_VERSION, _append, _load, _request, _safe, _sha, _write,
)


VERSION = 'UNIVERSE_CASH_ACTION_PREPARATION_V1'
EVENT_SOURCE = 'verified_baostock_cash_actions'
COVERAGE_SOURCE = 'verified_baostock_action_coverage'
SHARE_RATE_POLICY = 'BAOSTOCK_LEGACY_OPTIONAL_SHARE_RATE_EMPTY_STRING_IS_ZERO'
_ACTION_FIELDS = {'code', 'dividOperateDate', 'dividRegistDate', 'dividStocksPs',
    'dividReserveToStockPs', 'dividStockMarketDate', 'dividPayDate',
    'dividPlanDate', 'dividCashPsBeforeTax'}


def _response_header(path):
    """无缓冲，只读 raw_rows 前面的供应商和查询元信息。"""
    prefix = bytearray()
    with _safe(path).open('rb', buffering=0) as stream:
        while len(prefix) < 65536:
            value = stream.read(1)
            if not value:
                break
            prefix.extend(value)
            marker = re.search(rb'"raw_rows"\s*:\s*\[$', prefix)
            if marker:
                try:
                    return json.loads((prefix[:marker.start()] + b'"raw_rows":[]}').decode('utf-8'))
                except ValueError as exc:
                    raise ValueError('ACTION_RAW_HEADER_INVALID') from exc
    raise ValueError('ACTION_RAW_HEADER_REQUIRED')


def _query_scope(api, query, metadata=None):
    guard = ResearchDataAccessGuard()
    if (api == 'query_dividend_data' and query.get('yearType', 'report') not in {'report', 'operate'}):
        raise ValueError('ACTION_DIVIDEND_YEAR_TYPE_UNKNOWN')
    if api == 'query_dividend_data':
        year = int(query['year'])
        last_year = year if query.get('yearType', 'report') == 'operate' else year + 1
        lo, hi = year * 10000 + 101, last_year * 10000 + 1231
    elif api == 'query_adjust_factor':
        lo, hi = _day(query.get('start_date')), _day(query.get('end_date'))
    else:
        raise ValueError('ACTION_SOURCE_API_UNSUPPORTED')
    if lo is None or hi is None or lo > hi:
        raise ValueError('ACTION_SOURCE_SCOPE_UNKNOWN')
    guard.check_range(lo, hi, 'whole company action original source')
    if metadata is not None:
        lo, hi = _day(metadata.get('start')), _day(metadata.get('end'))
        if lo is None or hi is None or lo > hi:
            raise ValueError('ACTION_SOURCE_SCOPE_UNKNOWN')
        guard.check_range(lo, hi, 'registered company action original physical source')
    return lo, hi


def _read_response(item, audit):
    path, api, query = item['path'], item['api'], item['request']
    lo, hi = _query_scope(api, query, item.get('physical_metadata'))
    header = _response_header(path)
    if (header.get('provider') != 'BaoStock' or header.get('api') != api
            or header.get('request') != query):
        raise ValueError('ACTION_RAW_QUERY_OR_PROVIDER_CHANGED')
    _query_scope(header['api'], header['request'], item.get('physical_metadata'))
    _append(audit, {'event': 'ACTION_RAW_READ_ATTEMPT', 'path': str(path),
        'api': api, 'request': query, 'whole_source_start': lo, 'whole_source_end': hi})
    before = _safe(path).stat()
    digest = _sha(path)
    if digest != item['sha256']:
        raise ValueError('ACTION_RAW_SOURCE_SHA_CHANGED')
    value = _load(path)
    after = Path(path).stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('ACTION_RAW_CHANGED_DURING_READ')
    fields, rows = value.get('fields'), value.get('raw_rows')
    if (value.get('error_code') != '0' or value.get('historical_available_at_verified') is not False
            or not isinstance(fields, list) or not fields or len(fields) != len(set(fields))
            or not isinstance(rows, list) or value.get('request') != query):
        raise ValueError('ACTION_RAW_RESPONSE_INVALID')
    required = _ACTION_FIELDS if api == 'query_dividend_data' else {'code', 'dividOperateDate'}
    if not required <= set(fields):
        raise ValueError('ACTION_RAW_REQUIRED_FIELDS_MISSING')
    if item.get('requested_at_utc') is not None:
        requested = datetime.fromisoformat(value.get('requested_at_utc', ''))
        received = datetime.fromisoformat(value.get('received_at_utc', ''))
        if (value.get('requested_at_utc') != item['requested_at_utc'] or requested.tzinfo is None
                or received.tzinfo is None or received < requested):
            raise ValueError('ACTION_RAW_REQUEST_OR_RECEIPT_TIME_CONFLICT')
    raw_hash = hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    if raw_hash != value.get('raw_rows_sha256'):
        raise ValueError('ACTION_RAW_ROWS_SHA_CHANGED')
    if item.get('row_count') is not None and len(rows) != item['row_count']:
        raise ValueError('ACTION_RAW_ROW_COUNT_CONFLICT')
    normalized = []
    guard = ResearchDataAccessGuard()
    for row in rows:
        if not isinstance(row, list) or len(row) != len(fields):
            raise ValueError('ACTION_RAW_ROW_SHAPE_INVALID')
        record = dict(zip(fields, row))
        if record.get('code') != query['code']:
            raise ValueError('ACTION_RAW_SECURITY_CHANGED')
        for name, raw in record.items():
            if name.endswith('Date') and raw:
                day = _day(raw)
                guard.check_date(day, 'company action original date field')
                # 年度查询允许计划/支付时间跨年，但不能超出原件登记的物理范围。
                if item.get('physical_metadata') is not None and not lo <= day <= hi:
                    raise ValueError('ACTION_RAW_PHYSICAL_SCOPE_CONFLICT')
        if api == 'query_dividend_data' and query.get('yearType', 'report') == 'operate':
            effective = _day(record.get('dividOperateDate'))
            if effective is None or effective // 10000 != int(query['year']):
                raise ValueError('ACTION_RAW_OPERATE_YEAR_CONFLICT')
        if api == 'query_adjust_factor':
            day = _day(record.get('dividOperateDate'))
            if day is None or not lo <= day <= hi:
                raise ValueError('ACTION_RAW_ADJUST_DATE_OUTSIDE_REQUEST')
        normalized.append(record)
    evidence = {key: item[key] for key in ('symbol', 'kind', 'year', 'api', 'request', 'sha256', 'origin')}
    evidence.update(path=str(path), row_count=len(rows), physical_start=lo, physical_end=hi,
        source_year_type=query.get('yearType') if api == 'query_dividend_data' else None,
        requested_at_utc=value.get('requested_at_utc'), received_at_utc=value.get('received_at_utc'),
        historical_available_at_verified=False, batch_completed=item.get('batch_completed', True))
    evidence.update(request_verified=item.get('request_verified', item.get('batch_completed', True)),
        verification_status=item.get('verification_status', 'REGISTERED_OR_COMPLETE_BATCH_SOURCE'),
        parent_batch_status=item.get('parent_batch_status'),
        parent_batch_result_sha256=item.get('parent_batch_result_sha256'),
        request_verification_witness=item.get('request_verification_witness'))
    evidence.update({key: item[key] for key in ('operation_identity', 'collection_root',
        'batch_id', 'result_path', 'start_sha256') if key in item})
    _append(audit, {'event': 'ACTION_RAW_READ_VERIFIED', **evidence})
    return normalized, evidence


def _collector_sources(root):
    """验证冻结队列和日志，只投影已成功落盘的响应；不恢复或启动采集。"""
    root = Path(root).absolute()
    if root.resolve() != root:
        raise ValueError('ACTION_ACQUISITION_ROOT_REDIRECTED')
    if not (root / 'FROZEN_COLLECTION.json').is_file():
        return [], {'status': 'NO_FROZEN_COLLECTION', 'root': str(root)}
    frozen = _load(root / 'FROZEN_COLLECTION.json')
    raw, queue = collector._load_queue(root / 'ACQUISITION_BATCHES.json')
    if queue.get('continuation') is not None:
        return _continuation_sources(root)
    if (hashlib.sha256(raw).hexdigest() != frozen.get('queue_sha256')
            or {p['batch_id']: collector._hash(p) for p in queue['batches']} != frozen.get('batch_hashes')):
        raise ValueError('ACTION_FROZEN_COLLECTION_SCOPE_CHANGED')
    events = collector._read_events(root, frozen)
    completed = {e['batch_id']: e for e in events if e['event'] == 'BATCH_COMPLETED'}
    blocked = {e['batch_id']: e for e in events if e['event'] == 'BATCH_BLOCKED'}
    sources = []
    for plan in queue['batches']:
        batch = plan['batch_id']
        # 当前写入批次不读；BLOCKED 原件须逐请求独立核验，不能冒称批次完成。
        if batch not in completed and batch not in blocked:
            continue
        directory = root / 'batches' / batch
        result_path = directory / 'ACQUISITION_RESULT.json'
        if not result_path.is_file():
            continue
        actual_plan = _load(directory / 'ACQUISITION_PLAN.json')
        if collector._hash(actual_plan) != frozen['batch_hashes'][batch]:
            raise ValueError('ACTION_ACQUISITION_PLAN_CHANGED')
        result = _load(result_path)
        responses = result.get('responses', [])
        if batch in completed:
            if (_sha(result_path) != completed[batch]['result_sha256']
                    or responses != completed[batch]['responses'] or result.get('completed') is not True):
                raise ValueError('ACTION_COMPLETED_BATCH_RESULT_CHANGED')
        requested = {item['file']: item for item in plan['requests']}
        if batch in completed and (result.get('request_count') != len(requested) or len(responses) != len(requested)
                or {response.get('file') for response in responses} != set(requested)
                or any(response.get('error_code') != '0' for response in responses)):
            raise ValueError('ACTION_COMPLETED_BATCH_RESPONSE_SET_CHANGED')
        if (not isinstance(responses, list) or result.get('request_count') != len(responses)
                or len(responses) > len(requested)):
            raise ValueError('ACTION_PARTIAL_BATCH_RESPONSE_SET_CHANGED')
        parent_result_hash = _sha(result_path)
        seen = set()
        for response in responses:
            name = response.get('file')
            if name not in requested or name in seen:
                raise ValueError('ACTION_ACQUISITION_RESPONSE_SET_CONFLICT')
            seen.add(name)
            item = requested[name]
            if item['api'] not in {'query_dividend_data', 'query_adjust_factor'} or response.get('error_code') != '0':
                continue
            witness = (collector.verify_collected_response_v1(directory, item, response)
                       if batch not in completed else None)
            if witness is not None and witness.get('request_verified') is not True:
                raise ValueError('ACTION_PARTIAL_REQUEST_VERIFICATION_REQUIRED')
            query = item['request']
            symbol = canonical_symbol(query['code'])
            path = UniverseDataProviderV1._path(directory, name)
            start = _load(path.with_name(name + '.START.json'))
            if start.get('api') != item['api'] or start.get('request') != query or start.get('file') != name:
                raise ValueError('ACTION_ACQUISITION_START_CHANGED')
            if not start.get('started_at'):
                raise ValueError('ACTION_ACQUISITION_START_TIME_UNKNOWN')
            sources.append({'symbol': symbol, 'kind': 'DIVIDEND' if item['api'] == 'query_dividend_data' else 'ADJUST',
                'year': int(query['year']) if item['api'] == 'query_dividend_data' else None,
                'api': item['api'], 'request': query, 'path': path, 'sha256': response['sha256'],
                'row_count': response['row_count'], 'origin': 'FROZEN_COLLECTOR_RESPONSE',
                'requested_at_utc': start['started_at'],
                'request_verified': True, 'request_verification_witness': witness,
                'verification_status': 'COMPLETE_BATCH_SOURCE' if batch in completed else 'BATCH_PARTIAL_REQUEST_VERIFIED',
                'parent_batch_status': 'COMPLETED' if batch in completed else 'BLOCKED',
                'parent_batch_result_sha256': parent_result_hash,
                'batch_completed': batch in completed, 'batch_id': batch})
    return sources, {'root': str(root), 'queue_sha256': frozen['queue_sha256'],
        'frozen_collection_sha256': _sha(root / 'FROZEN_COLLECTION.json'),
        'verified_journal_events': len(events), 'completed_batch_count': len(completed),
        'blocked_batch_count': len(blocked),
        'partial_verified_request_count': sum(not source['batch_completed'] for source in sources)}


def _continuation_sources(root):
    """沿已登记续采链继承成功原件；祖先失败批仍明确保持 BLOCKED。"""
    view = collector.verified_collection_successes_v1(root)
    sources = []
    for reference in view['sources']:
        item, response = reference['request_item'], reference['result_row']
        if item['api'] not in {'query_dividend_data', 'query_adjust_factor'}:
            continue
        query, witness = item['request'], reference['request_verification_witness']
        sources.append({'symbol': canonical_symbol(query['code']),
            'kind': 'DIVIDEND' if item['api'] == 'query_dividend_data' else 'ADJUST',
            'year': int(query['year']) if item['api'] == 'query_dividend_data' else None,
            'api': item['api'], 'request': query, 'path': Path(witness['source_path']),
            'sha256': response['sha256'], 'row_count': response['row_count'],
            'origin': 'FROZEN_COLLECTOR_RESPONSE', 'requested_at_utc': witness['requested_at_utc'],
            'request_verified': True, 'request_verification_witness': witness,
            'verification_status': 'COMPLETE_BATCH_SOURCE' if reference['batch_completed']
                else 'BATCH_PARTIAL_REQUEST_VERIFIED',
            'parent_batch_status': reference['parent_batch_status'],
            'parent_batch_result_sha256': reference['result_sha256'],
            **{key: reference[key] for key in ('operation_identity', 'collection_root',
                'batch_id', 'result_path', 'batch_completed')}, 'start_sha256': witness['start_sha256']})
    leaf = view['collections'][-1]
    return sources, {'root': str(root), **{key: leaf[key] for key in (
        'queue_sha256', 'frozen_collection_sha256', 'verified_journal_events',
        'completed_batch_count', 'blocked_batch_count')},
        'partial_verified_request_count': sum(not source['batch_completed'] for source in sources),
        'collections': view['collections'], 'request_count': view['request_count'],
        'success_count': view['success_count'], 'root_planned_request_count': view['root_planned_request_count'],
        'generation': view['generation'], 'retry_counts_by_operation': view['retry_counts_by_operation'],
        'complete': view['complete']}


def _legacy_sources(catalog_path):
    if catalog_path is None:
        return [], []
    catalog = _load(catalog_path)
    result, skipped = [], []
    for dataset in catalog.get('datasets', []):
        root = Path(catalog['roots'][dataset['root_id']]).absolute()
        manifest = _load(dataset['manifest_path'])
        for name, metadata in manifest.get('files', {}).items():
            match = re.fullmatch(r'(DIVIDEND|ADJUST)_([0-9]{6}[.](?:SH|SZ))(?:_([0-9]{4}))?[.]json', name)
            if not match:
                continue
            kind, symbol, year = match.groups()
            path = UniverseDataProviderV1._path(root, name)
            try:
                ResearchDataAccessGuard().check_range(_day(metadata['start']), _day(metadata['end']),
                                                     'registered legacy whole source physical bounds')
            except FinalTestAccessViolation:
                skipped.append({'symbol': symbol, 'kind': kind, 'year': int(year) if year else None,
                    'path': str(path), 'registered_sha256': metadata['sha256'],
                    'status': 'SKIPPED_WITHOUT_BODY_READ', 'reason': 'LEGACY_PHYSICAL_SCOPE_SEALED'})
                continue
            header = _response_header(path)
            api = 'query_dividend_data' if kind == 'DIVIDEND' else 'query_adjust_factor'
            query = header.get('request')
            if (header.get('provider') != 'BaoStock' or header.get('api') != api
                    or not isinstance(query, dict) or canonical_symbol(query.get('code')) != symbol
                    or kind == 'DIVIDEND' and str(query.get('year')) != year):
                raise ValueError('ACTION_LEGACY_QUERY_IDENTITY_INVALID')
            try:
                _query_scope(api, query, metadata)
            except FinalTestAccessViolation:
                skipped.append({'symbol': symbol, 'kind': kind, 'year': int(year) if year else None,
                    'path': str(path), 'registered_sha256': metadata['sha256'], 'request': query,
                    'status': 'SKIPPED_WITHOUT_BODY_READ', 'reason': 'LEGACY_QUERY_SCOPE_SEALED'})
                continue
            result.append({'symbol': symbol, 'kind': kind, 'year': int(year) if year else None,
                'api': api, 'request': query, 'path': path, 'sha256': metadata['sha256'],
                'physical_metadata': metadata, 'origin': 'LEGACY_REGISTERED_ORIGINAL_RESPONSE',
                'batch_completed': True})
    return result, skipped


def _number(value):
    if value is None or value == '' or isinstance(value, bool):
        raise ValueError('ACTION_NUMERIC_TERM_UNKNOWN')
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError('ACTION_NUMERIC_TERM_INVALID')
    return number


def _normalize_action(symbol, row, evidence):
    if not _ACTION_FIELDS <= set(row):
        raise ValueError('CASH_ACTION_REQUIRED_TERMS_MISSING')
    effective = _day(row['dividOperateDate'])
    # 既有已验证接口对供应商空字符串送股/转增率采用 0；字段缺失仍不可补造。
    shares, reserves = (_number(0 if row[key] == '' else row[key])
                        for key in ('dividStocksPs', 'dividReserveToStockPs'))
    semantic = {key: row[key] for key in sorted(_ACTION_FIELDS)}
    semantic.update(dividStocksPs=shares, dividReserveToStockPs=reserves)
    if shares or reserves or row['dividStockMarketDate']:
        return None, semantic, 'UNSUPPORTED_NONCASH_CORPORATE_ACTION'
    semantic['dividCashPsBeforeTax'] = _number(row['dividCashPsBeforeTax'])
    record, payment, published = (_day(row[key]) for key in
                                 ('dividRegistDate', 'dividPayDate', 'dividPlanDate'))
    amount = semantic['dividCashPsBeforeTax']
    if (None in (record, effective, payment, published) or record < 20150908
            or not published <= record < effective <= payment or amount <= 0):
        raise ValueError('CASH_ACTION_DATES_OR_AMOUNT_UNKNOWN')
    event = {'event_id': f'{symbol}:{effective}:CASH', 'symbol': symbol, 'event_type': 'CASH_DIVIDEND',
        'record_date': record, 'effective_date': effective, 'payment_date': payment,
        'source_published_at': row['dividPlanDate'], 'units': 'CNY_PER_SHARE', 'source': EVENT_SOURCE,
        'terms': {'cash_per_share': amount, 'tax_rule': {'kind': 'DEFERRED_INDIVIDUAL_2015_101',
                                                      'source': TAX_SOURCE}},
        'original_source_path': evidence['path'], 'original_source_sha256': evidence['sha256'],
        'original_query_year_type': evidence['source_year_type'],
        'optional_share_rate_interpretation_policy': SHARE_RATE_POLICY,
        'historical_available_at_verified': False}
    IndividualDividendAccountingV1(0, [event], 'ACTION_INPUT_VALIDATION')._cash_terms(event)
    return event, semantic, None


def _additional_queue(gaps, acquisition_roots, start, end):
    """仅补未申请过的实际缺项，不重投正在采集或已执行的冻结请求。"""
    already_requested = set()
    planned_adjustment_ranges = defaultdict(list)
    queue_sources = []
    for root in acquisition_roots:
        path = Path(root).absolute() / 'ACQUISITION_BATCHES.json'
        if not path.is_file():
            continue
        raw, queue = collector._load_queue(path)
        queue_sources.append({'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()})
        already_requested.update(collector._hash({'api': item['api'], 'request': item['request']})
            for batch in queue['batches'] for item in batch['requests'])
        for batch in queue['batches']:
            for item in batch['requests']:
                if item['api'] == 'query_adjust_factor':
                    query = item['request']
                    planned_adjustment_ranges[canonical_symbol(query['code'])].append(
                        (_day(query['start_date']), _day(query['end_date'])))
    wanted = {}
    for gap in gaps:
        if gap['reason'] == 'DIVIDEND_OPERATE_YEAR_SOURCE_MISSING':
            request = _request(gap['symbol'], 'DIVIDEND', start, end, gap['year'])
        elif gap['reason'] == 'ADJUST_WHOLE_WINDOW_SOURCE_MISSING':
            if any(lo <= start and hi >= end for lo, hi in planned_adjustment_ranges[gap['symbol']]):
                continue
            request = _request(gap['symbol'], 'ADJUST', start, end)
        else:
            continue
        key = collector._hash({'api': request['api'], 'request': request['request']})
        if key not in already_requested:
            wanted[key] = request
    requests = sorted(wanted.values(), key=lambda item: item['file'])
    scope = 'USER_REQUEST_EXISTING_DATA_FIRST_AND_MISSING_SUPPLEMENTS'
    batches = [{'batch_id': f'ACTION_ADDITIONAL_{offset // 9 + 1:05d}',
        'purpose': 'EXPLORATORY_ENGINEERING_ACCEPTANCE', 'authorization_scope': scope,
        'max_requests': 9, 'requests': requests[offset:offset + 9],
        'execution_status': 'NOT_EXECUTED', 'dividend_year_type': 'operate',
        'collector': 'scripts/fetch_research_baostock_v1.py'}
        for offset in range(0, len(requests), 9)]
    return {'version': SUPPLEMENT_VERSION, 'source_version': SUPPLEMENT_VERSION,
        'request_count': len(requests), 'batches': batches,
        'excluded_frozen_queues': queue_sources, 'automatic_execution': False, 'automatic_retry': False,
        'authorization_source': {'origin': 'USER_EXPLICIT_CURRENT_TASK',
            'statement': '接通后，再按“股票＋年份＋缺失资料”补齐全市场。按这个做吧',
            'scope': scope, 'account_execution_authorized': False,
            'independent_validation_authorized': False}}


def prepare_universe_actions_v1(*, manifest, acquisition_root, output_dir,
                              legacy_catalog=None, feature_start=None, account_end=None,
                              additional_acquisition_root=None, manifest_name='manifest_actions_v1.json'):
    manifest_path = _safe(manifest)
    metadata = _load(manifest_path)
    output = Path(output_dir).absolute()
    if (not isinstance(manifest_name, str) or Path(manifest_name).name != manifest_name
            or not re.fullmatch(r'manifest_actions[A-Za-z0-9_.-]*[.]json', manifest_name)):
        raise ValueError('ACTION_MANIFEST_JSON_BASENAME_REQUIRED')
    destination = manifest_path.parent / manifest_name
    if (output.resolve() != output or not output.is_relative_to(manifest_path.parent)
            or output == manifest_path.parent or output.exists() or destination.exists()):
        raise ValueError('ACTION_NEW_CHILD_OUTPUT_REQUIRED')
    states = [f for f in metadata['files'].values() if f.get('kind') == 'STATES']
    if not states:
        raise ValueError('ACTION_STATE_WINDOW_REGISTRATION_REQUIRED')
    start = _day(feature_start) if feature_start else max(_day(f['start']) for f in states)
    end = _day(account_end) if account_end else min(_day(f['end']) for f in states)
    if start is None or end is None or start >= end:
        raise ValueError('ACTION_PREPARATION_WINDOW_INVALID')
    ResearchDataAccessGuard().check_range(start, end, 'company action preparation')
    targets = sorted({canonical_symbol(r['symbol']) for r in metadata['master']['records']
                      if scope_board(canonical_symbol(r['symbol'])) in BOARDS})
    acquisition_roots = [acquisition_root, *(additional_acquisition_root or [])]
    if len({str(Path(root).absolute()) for root in acquisition_roots}) != len(acquisition_roots):
        raise ValueError('ACTION_DUPLICATE_ACQUISITION_ROOT')
    collected, collection_evidence, seen_collections = [], [], set()
    for root in acquisition_roots:
        rows, evidence = _collector_sources(root)
        inherited_roots = {row['root'] for row in evidence.get('collections', [evidence])}
        if inherited_roots & seen_collections:
            raise ValueError('ACTION_DUPLICATE_COLLECTION_LINEAGE')
        seen_collections.update(inherited_roots)
        collected.extend(rows)
        collection_evidence.append(evidence)
    legacy, skipped_sources = _legacy_sources(legacy_catalog)
    sources = collected + legacy
    output.mkdir()
    audit, catalog, gaps = output / 'PHYSICAL_READ_EVENTS.jsonl', [], []
    evidence_by_symbol, rows_by_symbol = defaultdict(list), defaultdict(list)
    source_failures, query_rows = defaultdict(list), {}
    for item in sources:
        if item['symbol'] not in targets:
            continue
        try:
            rows, evidence = _read_response(item, audit)
        except (ValueError, OSError, KeyError) as exc:
            source_failures[item['symbol']].append({'symbol': item['symbol'], 'year': item['year'],
                'kind': item['kind'], 'reason': 'ACTION_SOURCE_INVALID:' + str(exc),
                'path': str(item['path']), 'status': 'UNKNOWN'})
            continue
        catalog.append(evidence)
        query_key = collector._hash({'api': item['api'], 'request': item['request']})
        response_hash = collector._hash(rows)
        if query_key in query_rows and response_hash != query_rows[query_key]:
            source_failures[item['symbol']].append({'symbol': item['symbol'], 'year': item['year'],
                'kind': item['kind'], 'reason': 'ACTION_QUERY_RESPONSE_CONTENT_CONFLICT',
                'path': str(item['path']), 'status': 'UNKNOWN'})
        query_rows[query_key] = response_hash
        evidence_by_symbol[item['symbol']].append(evidence)
        rows_by_symbol[item['symbol']].append((rows, evidence))
    events, coverage = [], []
    for symbol in targets:
        own_gaps = list(source_failures[symbol])
        own_sources = evidence_by_symbol[symbol]
        wanted_years = set(range(start // 10000, end // 10000 + 1))
        yearly = {int(source['request']['year']) for source in own_sources
                  if source['kind'] == 'DIVIDEND' and source['request_verified']
                  and source['source_year_type'] == 'operate'}
        for year in sorted(wanted_years - yearly):
            own_gaps.append({'symbol': symbol, 'year': year, 'kind': 'DIVIDEND',
                             'status': 'UNKNOWN', 'reason': 'DIVIDEND_OPERATE_YEAR_SOURCE_MISSING',
                             'desired_year_type': 'operate'})
        adjustment = [source for source in own_sources if source['kind'] == 'ADJUST'
            and source['request_verified'] and _day(source['request'].get('start_date')) <= start
            and _day(source['request'].get('end_date')) >= end]
        if not adjustment:
            own_gaps.append({'symbol': symbol, 'year': None, 'kind': 'ADJUST',
                             'status': 'UNKNOWN', 'reason': 'ADJUST_WHOLE_WINDOW_SOURCE_MISSING'})
        actions, raw_dates, adjustment_sets = {}, set(), []
        for rows, evidence in rows_by_symbol[symbol]:
            if not evidence['request_verified']:
                own_gaps.append({'symbol': symbol, 'year': evidence['year'], 'kind': evidence['kind'],
                    'status': 'UNKNOWN', 'reason': 'ACQUISITION_BATCH_COMPLETION_NOT_BOUND'})
            if evidence['kind'] == 'ADJUST':
                if evidence in adjustment:
                    dates = [_day(r.get('dividOperateDate')) for r in rows]
                    if dates != sorted(set(dates)):
                        own_gaps.append({'symbol': symbol, 'year': None, 'kind': 'ADJUST',
                            'status': 'UNKNOWN', 'reason': 'ADJUST_DUPLICATE_OR_UNSORTED_DATES'})
                    adjustment_sets.append({d for d in dates if start <= d <= end})
                continue
            for row in rows:
                effective = _day(row.get('dividOperateDate'))
                if effective is None:
                    own_gaps.append({'symbol': symbol, 'year': evidence['year'], 'kind': 'DIVIDEND',
                        'status': 'UNKNOWN', 'reason': 'DIVIDEND_EFFECTIVE_DATE_UNKNOWN'})
                    continue
                if not start <= effective <= end:
                    continue
                raw_dates.add(effective)
                try:
                    event, semantic, unsupported = _normalize_action(symbol, row, evidence)
                except (ValueError, KeyError, TypeError) as exc:
                    own_gaps.append({'symbol': symbol, 'year': effective // 10000, 'kind': 'DIVIDEND',
                        'status': 'UNKNOWN', 'reason': str(exc), 'effective_date': effective,
                        'source_sha256': evidence['sha256']})
                    continue
                if effective in actions:
                    prior = actions[effective]
                    if prior['semantic'] != semantic:
                        prior['conflict'] = True
                        own_gaps.append({'symbol': symbol, 'year': effective // 10000, 'kind': 'DIVIDEND',
                            'status': 'UNKNOWN', 'reason': 'DUPLICATE_ACTION_TERMS_CONFLICT',
                            'effective_date': effective})
                    prior['sources'].append({'path': evidence['path'], 'sha256': evidence['sha256'],
                                             'year_type': evidence['source_year_type']})
                else:
                    actions[effective] = {'event': event, 'semantic': semantic, 'conflict': False,
                        'sources': [{'path': evidence['path'], 'sha256': evidence['sha256'],
                                     'year_type': evidence['source_year_type']}]}
                if unsupported:
                    own_gaps.append({'symbol': symbol, 'year': effective // 10000, 'kind': 'CORPORATE_ACTION',
                        'status': 'UNSUPPORTED', 'reason': unsupported, 'effective_date': effective,
                        'source_sha256': evidence['sha256']})
        if adjustment_sets:
            if any(s != adjustment_sets[0] for s in adjustment_sets[1:]):
                own_gaps.append({'symbol': symbol, 'year': None, 'kind': 'ADJUST',
                    'status': 'UNKNOWN', 'reason': 'ADJUST_SOURCE_DATES_CONFLICT'})
            if adjustment_sets[0] != raw_dates:
                for day in sorted(adjustment_sets[0] ^ raw_dates):
                    own_gaps.append({'symbol': symbol, 'year': day // 10000, 'kind': 'CORPORATE_ACTION',
                        'status': 'UNKNOWN', 'reason': 'ADJUST_AND_ACTION_DATES_CONFLICT', 'effective_date': day})
        for action in actions.values():
            if action['event'] is not None and not action['conflict']:
                event = deepcopy(action['event'])
                event['original_sources'] = action['sources']
                events.append(event)
        gaps.extend(own_gaps)
        coverage.append({'symbol': symbol, 'symbols': [symbol], 'start': start, 'end': end,
            'complete': not own_gaps, 'source': COVERAGE_SOURCE, 'event_types': ['CASH_DIVIDEND'],
            'coverage_basis': 'VERIFIED_ANNUAL_RESPONSES_AND_FULL_WINDOW_VENDOR_ADJUST_DATES',
            'annual_source_year_types': sorted({s['source_year_type'] for s in own_sources
                                              if s['kind'] == 'DIVIDEND'}),
            'source_sha256s': sorted({s['sha256'] for s in own_sources}),
            'price_reference_validation': 'REQUIRED_BY_PUBLIC_PROVIDER_NOT_PERFORMED_HERE',
            'historical_available_at_verified': False,
            'independent_confirmation_eligible': False})
    events.sort(key=lambda event: (event['symbol'], event['effective_date'], event['event_id']))
    _write(output / 'EVENTS.json', events)
    _write(output / 'CORPORATE_ACTION_COVERAGE.json', coverage)
    _write(output / 'ACTION_GAPS.json', gaps)
    additional = _additional_queue(gaps, acquisition_roots, start, end)
    _write(output / 'ADDITIONAL_ACQUISITION_BATCHES.json', additional)
    _write(output / 'SOURCE_CATALOG.json', {'version': VERSION, 'sources': catalog,
        'skipped_sources_without_body_read': skipped_sources,
        'optional_share_rate_interpretation': {'policy': SHARE_RATE_POLICY,
            'fields': ['dividStocksPs', 'dividReserveToStockPs'], 'empty_string_value': 0,
            'missing_fields_are_not_zero': True, 'invalid_or_negative_values_are_rejected': True,
            'cash_amount_empty_string_is_rejected': True,
            'reference': 'src/chanlun_trader/research_factory/research_data_provider_v1.py:_baostock_bundle',
            'reference_source_sha256': _sha(PROJECT_ROOT / 'src/chanlun_trader/research_factory/research_data_provider_v1.py')},
        'collections': collection_evidence, 'historical_availability': 'MODELED',
        'independent_confirmation_eligible': False})
    complete = bool(coverage) and all(row['complete'] for row in coverage)
    new = deepcopy(metadata)
    new['files'] = {name: f for name, f in metadata['files'].items()
                    if f['kind'] not in {'EVENTS', 'CORPORATE_ACTION_COVERAGE'}}
    for name, kind, source in [('EVENTS.json', 'EVENTS', EVENT_SOURCE),
                              ('CORPORATE_ACTION_COVERAGE.json', 'CORPORATE_ACTION_COVERAGE', COVERAGE_SOURCE)]:
        path = output / name
        new['files'][path.relative_to(manifest_path.parent).as_posix()] = {
            'kind': kind, 'format': 'JSON', 'start': start, 'end': end, 'source_id': source,
            'sha256': _sha(path), 'evidence': {'source_catalog_path': (output / 'SOURCE_CATALOG.json').relative_to(
                manifest_path.parent).as_posix(), 'source_catalog_sha256': _sha(output / 'SOURCE_CATALOG.json'),
                'original_source_hashes': {s['path']: s['sha256'] for s in catalog}}}
    new['corporate_actions_complete'] = complete
    new['action_preparation'] = {'version': VERSION, 'original_manifest_sha256': _sha(manifest_path),
        'optional_share_rate_interpretation_policy': SHARE_RATE_POLICY,
        'corporate_actions_complete': complete, 'supported_event_types': ['CASH_DIVIDEND'],
        'historical_available_at_verified': False, 'independent_confirmation_eligible': False,
        'price_reference_validation': 'REQUIRED_BY_PUBLIC_PROVIDER_NOT_PERFORMED_HERE'}
    _write(destination, new)
    summary = {'version': VERSION, 'target_count': len(targets), 'cash_event_count': len(events),
        'source_count': len(catalog), 'corporate_covered_symbol_count': sum(c['complete'] for c in coverage),
        'corporate_actions_complete': complete, 'reason_counts': dict(Counter(g['reason'] for g in gaps)),
        'additional_acquisition_request_count': additional['request_count'],
        'additional_acquisition_queue': str(output / 'ADDITIONAL_ACQUISITION_BATCHES.json'),
        'new_manifest': str(destination), 'new_manifest_sha256': _sha(destination),
        'account_data_ready': False, 'account_executed': False, 'budget_created': False,
        'historical_available_at_verified': False, 'independent_confirmation_eligible': False}
    _write(output / 'PREPARATION_COMPLETE.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--acquisition-root', required=True)
    parser.add_argument('--additional-acquisition-root', action='append')
    parser.add_argument('--manifest-name', default='manifest_actions_v1.json')
    parser.add_argument('--legacy-catalog')
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--feature-start', type=int)
    parser.add_argument('--account-end', type=int)
    parser.add_argument('--json', action='store_true')
    options = vars(parser.parse_args())
    as_json = options.pop('json')
    result = prepare_universe_actions_v1(**options)
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print('公司行动资料准备' + ZhCNPresentation.status_name('COMPLETED') + '。')
        print(f"目标 {result['target_count']} 只，现金事件 {result['cash_event_count']} 个，"
              f"公司行动资料覆盖 {result['corporate_covered_symbol_count']} 只。")
        print('账户仍须由公共入口核验状态、成交参考价和全部资料；本次未运行账户。')
        print('新登记清单：' + result['new_manifest'])


if __name__ == '__main__':
    main()
