"""按冻结缺口队列补采公司资料；逐批最多九请求，可核验恢复，不自动重试失败。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock
from chanlun_trader.research.guard import ResearchDataAccessGuard
from scripts import fetch_research_baostock_v1 as acquisition
from chanlun_trader.research_factory.baostock_universe_adapter_v1 import (
    guard_baostock_response_header_v1,
)


VERSION = 'UNIVERSE_GAP_COLLECTOR_V1'
MAX_EXPLICIT_OPERATION_RETRIES = 3


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path):
    if path.resolve() != path.absolute():
        raise ValueError('COLLECTOR_CONTENT_PATH_REDIRECTED')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _load_queue(path):
    raw = path.read_bytes()
    queue = json.loads(raw.decode('utf-8-sig'))
    source = queue.get('authorization_source')
    if (queue.get('version') != 'UNIVERSE_BAOSTOCK_SUPPLEMENTS_V1'
            or not isinstance(source, dict) or source.get('origin') != 'USER_EXPLICIT_CURRENT_TASK'
            or not source.get('statement') or not source.get('scope')
            or queue.get('account_execution_authorized') is True
            or queue.get('independent_validation_authorized') is True):
        raise ValueError('COLLECTOR_EXPLICIT_DATA_AUTHORIZATION_REQUIRED')
    batches = queue.get('batches')
    if not isinstance(batches, list) or not batches:
        raise ValueError('COLLECTOR_BATCH_QUEUE_INVALID')
    seen, operations = set(), set()
    for plan in batches:
        acquisition.validate_plan(plan)
        name = plan.get('batch_id')
        if (not isinstance(name, str) or re.fullmatch(r'[A-Za-z0-9_-]+', name) is None
                or name in seen or plan['authorization_scope'] != source['scope']):
            raise ValueError('COLLECTOR_BATCH_ID_OR_SCOPE_INVALID')
        seen.add(name)
        for item in plan['requests']:
            operation = _hash({'api': item['api'], 'request': item['request']})
            if operation in operations:
                raise ValueError('COLLECTOR_DUPLICATE_OPERATION')
            operations.add(operation)
    if queue.get('request_count') != len(operations):
        raise ValueError('COLLECTOR_REQUEST_COUNT_CONFLICT')
    continuation = queue.get('continuation')
    if continuation is not None:
        if (not isinstance(continuation, dict)
                or type(continuation.get('previous_request_count')) is not int
                or continuation['previous_request_count'] < 0
                or not continuation.get('receipt_path') or not continuation.get('receipt_sha256')):
            raise ValueError('COLLECTOR_CONTINUATION_SCOPE_INVALID')
        receipt_path = Path(continuation['receipt_path']).absolute()
        if _file_hash(receipt_path) != continuation['receipt_sha256']:
            raise ValueError('COLLECTOR_CONTINUATION_RECEIPT_CHANGED')
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if (receipt.get('version') != 'UNIVERSE_COLLECTION_CONTINUATION_V1'
                or receipt.get('original_request_count') != continuation['previous_request_count']
                or receipt.get('already_success_count') != continuation.get('already_success_count')
                or receipt.get('retry_failed_count') != continuation.get('retry_failed_count')
                or receipt.get('remaining_operation_identities') != [
                    _hash({'api': item['api'], 'request': item['request']})
                    for plan in batches for item in plan['requests']]):
            raise ValueError('COLLECTOR_CONTINUATION_RECEIPT_SCOPE_CONFLICT')
    return raw, queue


def _collection_scope(raw, queue):
    queue_hash = hashlib.sha256(raw).hexdigest()
    frozen = {'version': VERSION, 'queue_sha256': queue_hash,
        'authorization_source': queue['authorization_source'],
        'batch_hashes': {plan['batch_id']: _hash(plan) for plan in queue['batches']},
        'request_count': queue['request_count'], 'automatic_retry': False,
        'account_execution_authorized': False, 'independent_validation_authorized': False}
    if queue.get('continuation') is not None:
        frozen['continuation'] = queue['continuation']
    return frozen


def continuation_control_path_v1(receipt):
    """原失败队列与日志决定唯一控制记录，保存在原资料目录之外。"""
    root = Path(receipt['original_collection_root']).absolute()
    if root.resolve() != root:
        raise ValueError('COLLECTOR_CONTINUATION_SOURCE_REDIRECTED')
    identity = _hash({'original_queue_sha256': receipt['original_queue_sha256'],
        'original_journal_sha256': receipt['original_journal_sha256']})
    return root.parent / ('.' + root.name + '.continuation-' + identity + '.json')


def _continuation_registration(raw, queue, collection_root):
    continuation = queue['continuation']
    receipt_path = Path(continuation['receipt_path']).absolute()
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    control_path = continuation_control_path_v1(receipt)
    payload = {'version': 'UNIVERSE_COLLECTION_CONTINUATION_REGISTRATION_V1',
        'original_collection_root': receipt['original_collection_root'],
        'original_queue_sha256': receipt['original_queue_sha256'],
        'original_journal_sha256': receipt['original_journal_sha256'],
        'plan_root': str(receipt_path.parent), 'receipt_path': str(receipt_path),
        'receipt_sha256': continuation['receipt_sha256'],
        'continuation_queue_sha256': hashlib.sha256(raw).hexdigest(),
        'collection_root': str(Path(collection_root).absolute())}
    return control_path, payload, receipt


def register_continuation_v1(batches, collection_root, *, reason):
    """登记唯一续采目标；兼容已冻结或正在执行的旧清单，只写外部控制记录。"""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('COLLECTOR_CONTINUATION_REGISTRATION_REASON_REQUIRED')
    raw, queue = _load_queue(Path(batches).absolute())
    if queue.get('continuation') is None:
        raise ValueError('COLLECTOR_CONTINUATION_QUEUE_REQUIRED')
    root = Path(collection_root).absolute()
    if root.resolve() != root:
        raise ValueError('COLLECTOR_OUTPUT_REDIRECTED')
    control_path, payload, receipt = _continuation_registration(raw, queue, root)
    original = Path(receipt['original_collection_root']).absolute()
    for name, digest in [('ACQUISITION_BATCHES.json', receipt['original_queue_sha256']),
            ('FROZEN_COLLECTION.json', receipt['original_freeze_sha256']),
            ('COLLECTION_JOURNAL.jsonl', receipt['original_journal_sha256'])]:
        if _file_hash(original / name) != digest:
            raise ValueError('COLLECTOR_CONTINUATION_ORIGINAL_IDENTITY_CHANGED')
    source_raw, source_queue = _load_queue(original / 'ACQUISITION_BATCHES.json')
    if source_queue.get('continuation') is not None:
        verify_continuation_lineage_v1(source_raw, source_queue, original)
    plan_queue = Path(payload['plan_root']) / 'ACQUISITION_BATCHES.json'
    if _file_hash(plan_queue) != payload['continuation_queue_sha256']:
        raise ValueError('COLLECTOR_CONTINUATION_PLAN_CONTENT_CHANGED')
    frozen_path = root / 'FROZEN_COLLECTION.json'
    if frozen_path.exists():
        if (json.loads(frozen_path.read_text(encoding='utf-8')) != _collection_scope(raw, queue)
                or _file_hash(root / 'ACQUISITION_BATCHES.json') != payload['continuation_queue_sha256']):
            raise ValueError('COLLECTOR_CONTINUATION_EXISTING_COLLECTION_CHANGED')
    elif root.exists() and any(root.iterdir()):
        raise ValueError('COLLECTOR_CONTINUATION_UNRECOGNIZED_COLLECTION')
    with ObjectiveMutationLock.for_resource(control_path):
        if control_path.exists():
            actual = json.loads(control_path.read_text(encoding='utf-8'))
            if {key: actual.get(key) for key in payload} != payload:
                raise ValueError('COLLECTOR_CONTINUATION_ALREADY_REGISTERED_ELSEWHERE')
            return actual
        value = {**payload, 'explicit_registration_reason': reason.strip(),
            'automatic_retry': False, 'account_execution_authorized': False,
            'recorded_at_utc': datetime.now(timezone.utc).isoformat()}
        with control_path.open('xb') as stream:
            stream.write(_canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        return value


def _verify_continuation_registration(raw, queue, root):
    if queue.get('continuation') is None:
        return
    control_path, payload, _ = _continuation_registration(raw, queue, root)
    if not control_path.exists():
        raise ValueError('COLLECTOR_CONTINUATION_REGISTRATION_REQUIRED')
    _file_hash(control_path)
    actual = json.loads(control_path.read_text(encoding='utf-8'))
    if {key: actual.get(key) for key in payload} != payload:
        raise ValueError('COLLECTOR_CONTINUATION_ALREADY_REGISTERED_ELSEWHERE')


def verify_continuation_lineage_v1(raw, queue, root, *, include_sources=False):
    """只读核对已登记的父链、成功来源和累计计数；不重新发起任何请求。"""
    nodes, seen = [], set()
    source_view, collection_view = [], []
    while queue.get('continuation') is not None:
        _verify_continuation_registration(raw, queue, root)
        binding = queue['continuation']
        receipt_path = Path(binding['receipt_path']).absolute()
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if binding['receipt_sha256'] in seen:
            raise ValueError('COLLECTOR_CONTINUATION_LINEAGE_CYCLE')
        seen.add(binding['receipt_sha256'])
        original = Path(receipt['original_collection_root']).absolute()
        for name, digest in [('ACQUISITION_BATCHES.json', receipt['original_queue_sha256']),
                ('FROZEN_COLLECTION.json', receipt['original_freeze_sha256']),
                ('COLLECTION_JOURNAL.jsonl', receipt['original_journal_sha256'])]:
            if _file_hash(original / name) != digest:
                raise ValueError('COLLECTOR_CONTINUATION_ORIGINAL_IDENTITY_CHANGED')
        source_raw, source_queue = _load_queue(original / 'ACQUISITION_BATCHES.json')
        frozen = json.loads((original / 'FROZEN_COLLECTION.json').read_text(encoding='utf-8'))
        if frozen != _collection_scope(source_raw, source_queue):
            raise ValueError('COLLECTOR_CONTINUATION_SOURCE_SCOPE_CHANGED')
        events = _read_events(original, frozen)
        completed = {event['batch_id']: event for event in events if event['event'] == 'BATCH_COMPLETED'}
        blocked = {event['batch_id']: event for event in events if event['event'] == 'BATCH_BLOCKED'}
        plans = {plan['batch_id']: plan for plan in source_queue['batches']}
        successes, local_count = set(), 0
        expected_complete = {row['batch_id']: row for row in receipt['completed_batch_evidence']}
        expected_blocked = {row['batch_id']: row for row in receipt['blocked_batch_evidence']}
        if set(completed) != set(expected_complete) or set(blocked) != set(expected_blocked):
            raise ValueError('COLLECTOR_CONTINUATION_SOURCE_BATCHES_CHANGED')
        for name, event in completed.items():
            proof = expected_complete[name]
            if proof['event_hash'] != event['event_hash'] or proof['result_sha256'] != event['result_sha256']:
                raise ValueError('COLLECTOR_CONTINUATION_COMPLETED_PROOF_CONFLICT')
            verified = _verify_success(original, plans[name], event,
                include_witnesses=include_sources)
            local_count += verified['request_count']
            successes.update(_hash({'api': item['api'], 'request': item['request']})
                for item in plans[name]['requests'])
            if include_sources:
                rows = {row['file']: row for row in verified['responses']}
                source_view.extend(_success_reference(original, name, item, rows[item['file']],
                    verified['result_sha256'], verified['witnesses'][item['file']], True)
                    for item in plans[name]['requests'])
        partial = {row['operation_identity']: row for row in receipt['partial_success_witnesses']}
        failures = {row['operation_identity']: row for row in receipt['failed_request_retries']}
        observed_partial, observed_failures = set(), set()
        for name, event in blocked.items():
            plan, proof = plans[name], expected_blocked[name]
            directory = original / 'batches' / name
            if (proof['event_hash'] != event['event_hash']
                    or _file_hash(directory / 'ACQUISITION_RESULT.json') != proof['result_sha256']
                    or _hash(json.loads((directory / 'ACQUISITION_PLAN.json').read_text(encoding='utf-8'))) != _hash(plan)):
                raise ValueError('COLLECTOR_CONTINUATION_BLOCKED_PROOF_CONFLICT')
            result = json.loads((directory / 'ACQUISITION_RESULT.json').read_text(encoding='utf-8'))
            rows = {row['file']: row for row in result['responses']}
            items = {item['file']: item for item in plan['requests']}
            starts = {path.name[:-len('.START.json')] for path in directory.glob('*.START.json')}
            if (result.get('completed') is not False or not starts <= set(items)
                    or not set(rows) <= starts or len(rows) != len(result['responses'])
                    or result['request_count'] != len(starts) or event['request_count'] != len(starts)):
                raise ValueError('COLLECTOR_CONTINUATION_PARTIAL_COUNT_CONFLICT')
            local_count += len(starts)
            for filename in starts:
                item, row = items[filename], rows.get(filename)
                operation = _hash({'api': item['api'], 'request': item['request']})
                if row is not None and row.get('error_code') == '0':
                    witness = verify_collected_response_v1(directory, item, row)
                    declared = partial.get(operation, {})
                    if declared.get('batch_id') != name or any(declared.get(key) != value for key, value in witness.items()):
                        raise ValueError('COLLECTOR_CONTINUATION_PARTIAL_SOURCE_CONFLICT')
                    observed_partial.add(operation)
                    successes.add(operation)
                    if include_sources:
                        source_view.append(_success_reference(original, name, item, row,
                            proof['result_sha256'], witness, False))
                else:
                    declared = failures.get(operation, {})
                    start_path = directory / (filename + '.START.json')
                    start = json.loads(start_path.read_text(encoding='utf-8'))
                    if (declared.get('batch_id') != name or declared.get('request') != item['request']
                            or declared.get('api') != item['api']
                            or declared.get('start_sha256') != _file_hash(start_path)
                            or {key: start.get(key) for key in item} != item
                            or declared.get('requested_at_utc') != start.get('started_at')):
                        raise ValueError('COLLECTOR_CONTINUATION_FAILED_START_CONFLICT')
                    observed_failures.add(operation)
        if set(partial) != observed_partial or set(failures) != observed_failures:
            raise ValueError('COLLECTOR_CONTINUATION_LOCAL_OPERATIONS_CONFLICT')
        planned = {_hash({'api': item['api'], 'request': item['request']})
            for plan in source_queue['batches'] for item in plan['requests']}
        remaining = set(receipt['remaining_operation_identities'])
        parent = source_queue.get('continuation')
        if (remaining != planned - successes or successes & remaining
                or receipt['authorization_source'] != source_queue['authorization_source']
                or (parent is not None and (receipt.get('parent_receipt_sha256') != parent['receipt_sha256']
                    or receipt.get('parent_receipt_path') != parent['receipt_path']))
                or (parent is None and receipt.get('parent_receipt_sha256') is not None)):
            raise ValueError('COLLECTOR_CONTINUATION_PARENT_OR_SCOPE_CONFLICT')
        nodes.append((receipt, local_count, len(successes), set(failures)))
        if include_sources:
            collection_view.append(_collection_reference(original, frozen, events,
                local_count, len(successes), len(observed_partial)))
        raw, queue, root = source_raw, source_queue, original
    count, success_count, retry_counts = 0, 0, {}
    root_planned = queue['request_count']
    for generation, (receipt, local_count, local_success_count, failures) in enumerate(reversed(nodes), 1):
        count += local_count
        success_count += local_success_count
        for operation in failures:
            retry_counts[operation] = retry_counts.get(operation, 0) + 1
        if any(number > MAX_EXPLICIT_OPERATION_RETRIES for number in retry_counts.values()):
            raise ValueError('COLLECTOR_CONTINUATION_OPERATION_RETRY_LIMIT')
        if (receipt['original_request_count'] != count or receipt['already_success_count'] != success_count
                or receipt['retry_failed_count'] != len(failures)
                or receipt['remaining_request_count'] != root_planned - success_count):
            raise ValueError('COLLECTOR_CONTINUATION_CUMULATIVE_COUNT_CONFLICT')
        if 'local_request_count' in receipt and (
                receipt['local_request_count'] != local_count or receipt['local_success_count'] != local_success_count
                or receipt['root_planned_request_count'] != root_planned
                or receipt['retry_counts_by_operation'] != retry_counts
                or receipt['explicit_operation_retry_limit'] != MAX_EXPLICIT_OPERATION_RETRIES):
            raise ValueError('COLLECTOR_CONTINUATION_CHAIN_COUNTS_CONFLICT')
    result = {'request_count': count, 'success_count': success_count,
        'root_planned_request_count': root_planned, 'retry_counts_by_operation': retry_counts,
        'generation': len(nodes)}
    if include_sources:
        result.update(sources=source_view, collections=list(reversed(collection_view)))
    return result


def _initialize(root, raw, queue):
    frozen = _collection_scope(raw, queue)
    queue_hash = frozen['queue_sha256']
    path = root / 'FROZEN_COLLECTION.json'
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != frozen:
            raise ValueError('COLLECTOR_FROZEN_QUEUE_OR_SCOPE_CHANGED')
        if _file_hash(root / 'ACQUISITION_BATCHES.json') != queue_hash:
            raise ValueError('COLLECTOR_FROZEN_QUEUE_CONTENT_CHANGED')
    else:
        if any(item.name != 'COLLECTION_STATE.json.mutation.lock' for item in root.iterdir()):
            raise ValueError('COLLECTOR_UNRECOGNIZED_EXISTING_OUTPUT')
        with (root / 'ACQUISITION_BATCHES.json').open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        acquisition.put(path, frozen)
    return frozen


def _read_events(root, frozen):
    path = root / 'COLLECTION_JOURNAL.jsonl'
    if not path.exists():
        return []
    events, previous = [], None
    for line in path.read_text(encoding='utf-8').splitlines():
        entry = json.loads(line)
        payload = {key: value for key, value in entry.items() if key != 'event_hash'}
        if (entry.get('sequence') != len(events) + 1 or entry.get('previous_hash') != previous
                or entry.get('queue_sha256') != frozen['queue_sha256']
                or entry.get('event_hash') != _hash(payload)):
            raise ValueError('COLLECTOR_JOURNAL_IDENTITY_CONFLICT')
        if 'batch_id' in entry and entry.get('plan_hash') != frozen['batch_hashes'].get(entry['batch_id']):
            raise ValueError('COLLECTOR_JOURNAL_SCOPE_CONFLICT')
        events.append(entry)
        previous = entry['event_hash']
    return events


def _append_event(root, frozen, events, event, **values):
    payload = {'sequence': len(events) + 1, 'event': event,
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'queue_sha256': frozen['queue_sha256'],
        'previous_hash': events[-1]['event_hash'] if events else None, **values}
    entry = {**payload, 'event_hash': _hash(payload)}
    with (root / 'COLLECTION_JOURNAL.jsonl').open('ab') as stream:
        stream.write(_canonical(entry) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    events.append(entry)
    return entry


def verify_collected_response_v1(directory, item, resultrow):
    """逐请求只读核验，支持失败批中的成功原件；不证明整个批次完成。"""
    directory = Path(directory).absolute()
    if directory.resolve() != directory:
        raise ValueError('COLLECTOR_BATCH_PATH_REDIRECTED')
    if (not isinstance(item, dict) or not isinstance(resultrow, dict)
            or item.get('api') not in {'query_history_k_data_plus', 'query_dividend_data', 'query_adjust_factor'}
            or not isinstance(item.get('file'), str)
            or re.fullmatch(r'[A-Za-z0-9_.-]+[.]json', item['file']) is None
            or resultrow.get('file') != item['file'] or resultrow.get('error_code') != '0'):
        raise ValueError('COLLECTOR_RESPONSE_NOT_SUCCESSFUL')
    guard = ResearchDataAccessGuard()
    response_path = directory / resultrow['file']
    start_path = directory / (resultrow['file'] + '.START.json')
    start_hash = _file_hash(start_path)
    started = json.loads(start_path.read_text(encoding='utf-8'))
    if {key: started.get(key) for key in item} != item or not started.get('started_at'):
        raise ValueError('COLLECTOR_REQUEST_START_IDENTITY_CONFLICT')
    query = item['request']
    if item['api'] == 'query_dividend_data':
        year = int(query['year'])
        source_scope = {'start': year * 10000 + 101,
            'end': (year + (query.get('yearType', 'report') == 'report')) * 10000 + 1231}
    else:
        source_scope = {'start': query['start_date'], 'end': query['end_date']}
    if response_path.resolve() != response_path:
        raise ValueError('COLLECTOR_CONTENT_PATH_REDIRECTED')
    header = guard_baostock_response_header_v1(response_path, source_scope,
        expected_api=item['api'])['header']
    if header['request'] != query:
        raise ValueError('COLLECTOR_RAW_RESPONSE_IDENTITY_CONFLICT')
    if _file_hash(response_path) != resultrow['sha256']:
        raise ValueError('COLLECTOR_RESPONSE_CONTENT_CHANGED:' + resultrow['file'])
    value = json.loads(response_path.read_text(encoding='utf-8'))
    if (value.get('provider') != 'BaoStock' or value.get('api') != item['api']
            or value.get('request') != item['request'] or value.get('error_code') != '0'
            or value.get('historical_available_at_verified') is not False
            or value.get('requested_at_utc') != started['started_at']
            or not isinstance(value.get('fields'), list) or not value['fields']
            or len(value['fields']) != len(set(value['fields']))
            or not isinstance(value.get('raw_rows'), list)
            or any(not isinstance(raw_row, list) for raw_row in value['raw_rows'])
            or len(value['raw_rows']) != resultrow.get('row_count')):
        raise ValueError('COLLECTOR_RAW_RESPONSE_IDENTITY_CONFLICT')
    requested = datetime.fromisoformat(value['requested_at_utc'])
    received = datetime.fromisoformat(value['received_at_utc'])
    if requested.tzinfo is None or received.tzinfo is None or received < requested:
        raise ValueError('COLLECTOR_RESPONSE_TIME_CONFLICT')
    raw_hash = hashlib.sha256(json.dumps(value['raw_rows'], ensure_ascii=False,
        separators=(',', ':')).encode('utf-8')).hexdigest()
    if raw_hash != value.get('raw_rows_sha256'):
        raise ValueError('COLLECTOR_RAW_ROWS_IDENTITY_CONFLICT')
    acquisition._validate_response_dates(item, value['fields'], value['raw_rows'], guard)
    return {'request_verified': True, 'source_path': str(response_path),
        'source_sha256': resultrow['sha256'], 'start_sha256': start_hash,
        'raw_rows_sha256': raw_hash, 'request': item['request'], 'api': item['api'],
        'requested_at_utc': value['requested_at_utc'], 'received_at_utc': value['received_at_utc'],
        'row_count': len(value['raw_rows']), 'independent_confirmation_eligible': False}


def _verify_success(root, plan, completed=None, *, include_witnesses=False):
    directory = root / 'batches' / plan['batch_id']
    if directory.resolve() != directory:
        raise ValueError('COLLECTOR_BATCH_PATH_REDIRECTED')
    actual_plan = json.loads((directory / 'ACQUISITION_PLAN.json').read_text(encoding='utf-8'))
    if _hash(actual_plan) != _hash(plan):
        raise ValueError('COLLECTOR_BATCH_PLAN_CHANGED')
    result_path = directory / 'ACQUISITION_RESULT.json'
    result_hash = _file_hash(result_path)
    if completed is not None and completed.get('result_sha256') != result_hash:
        raise ValueError('COLLECTOR_BATCH_RESULT_CHANGED')
    result = json.loads(result_path.read_text(encoding='utf-8'))
    responses = result.get('responses', [])
    expected = {item['file'] for item in plan['requests']}
    if (result.get('completed') is not True or result.get('request_count') != len(expected)
            or len(responses) != len(expected) or {row.get('file') for row in responses} != expected
            or any(row.get('error_code') != '0' for row in responses)):
        raise ValueError('COLLECTOR_BATCH_NOT_COMPLETED')
    acquisition.validate_plan(plan)
    expected_items = {item['file']: item for item in plan['requests']}
    if include_witnesses and {path.name[:-len('.START.json')]
            for path in directory.glob('*.START.json')} != expected:
        raise ValueError('COLLECTOR_COMPLETED_REQUEST_START_SET_CONFLICT')
    witnesses = {}
    for row in responses:
        witness = verify_collected_response_v1(directory, expected_items[row['file']], row)
        if include_witnesses:
            witnesses[row['file']] = witness
    if completed is not None and completed.get('responses') != responses:
        raise ValueError('COLLECTOR_RESPONSE_IDENTITY_CONFLICT')
    result = {'result_sha256': result_hash, 'responses': responses, 'request_count': len(expected)}
    if include_witnesses:
        result['witnesses'] = witnesses
    return result


def _success_reference(root, batch, item, row, result_hash, witness, completed):
    return {'operation_identity': _hash({'api': item['api'], 'request': item['request']}),
        'collection_root': str(root), 'batch_id': batch, 'request_item': item,
        'result_row': row, 'result_path': str(root / 'batches' / batch / 'ACQUISITION_RESULT.json'),
        'result_sha256': result_hash, 'batch_completed': completed,
        'parent_batch_status': 'COMPLETED' if completed else 'BLOCKED',
        'request_verification_witness': witness}


def _collection_reference(root, frozen, events, request_count, success_count, partial_count):
    journal = root / 'COLLECTION_JOURNAL.jsonl'
    return {'root': str(root), 'queue_sha256': frozen['queue_sha256'],
        'frozen_collection_sha256': _file_hash(root / 'FROZEN_COLLECTION.json'),
        'journal_sha256': _file_hash(journal) if journal.is_file() else None,
        'verified_journal_events': len(events),
        'completed_batch_count': sum(e['event'] == 'BATCH_COMPLETED' for e in events),
        'blocked_batch_count': sum(e['event'] == 'BATCH_BLOCKED' for e in events),
        'request_count': request_count, 'success_count': success_count,
        'partial_verified_request_count': partial_count}


def verified_collection_successes_v1(root, *, require_complete=False):
    """只读投影完整已校验父链及本叶的成功原件；不合并或改写采集日志。"""
    root = Path(root).absolute()
    if root.resolve() != root:
        raise ValueError('COLLECTOR_OUTPUT_REDIRECTED')
    raw, queue = _load_queue(root / 'ACQUISITION_BATCHES.json')
    frozen = json.loads((root / 'FROZEN_COLLECTION.json').read_text(encoding='utf-8'))
    if frozen != _collection_scope(raw, queue):
        raise ValueError('COLLECTOR_FROZEN_QUEUE_OR_SCOPE_CHANGED')
    events = _read_events(root, frozen)
    completed, blocked = {}, {}
    for event in events:
        if event['event'] not in {'BATCH_COMPLETED', 'BATCH_BLOCKED'}:
            continue
        name = event['batch_id']
        if name in completed or name in blocked:
            raise ValueError('COLLECTOR_DUPLICATE_TERMINAL_BATCH')
        (completed if event['event'] == 'BATCH_COMPLETED' else blocked)[name] = event
    complete = (len(completed) == len(queue['batches']) and not blocked
        and not any(e['event'] == 'LOGIN_BLOCKED' for e in events))
    if require_complete and not complete:
        raise ValueError('COLLECTOR_COLLECTION_NOT_COMPLETE')
    lineage = verify_continuation_lineage_v1(raw, queue, root, include_sources=True)
    sources, local_count, partial_count = list(lineage['sources']), 0, 0
    local_successes, local_failures = 0, set()
    for plan in queue['batches']:
        name = plan['batch_id']
        if name in completed:
            proof = _verify_success(root, plan, completed[name], include_witnesses=True)
            rows = {row['file']: row for row in proof['responses']}
            local_count += proof['request_count']
            for item in plan['requests']:
                sources.append(_success_reference(root, name, item, rows[item['file']],
                    proof['result_sha256'], proof['witnesses'][item['file']], True))
                local_successes += 1
        elif name in blocked:
            directory = root / 'batches' / name
            if _hash(json.loads((directory / 'ACQUISITION_PLAN.json').read_text(encoding='utf-8'))) != _hash(plan):
                raise ValueError('COLLECTOR_BATCH_PLAN_CHANGED')
            result_path = directory / 'ACQUISITION_RESULT.json'
            result_hash = _file_hash(result_path)
            result = json.loads(result_path.read_text(encoding='utf-8'))
            responses = result.get('responses', [])
            rows = {row['file']: row for row in responses}
            items = {item['file']: item for item in plan['requests']}
            starts = {path.name[:-len('.START.json')] for path in directory.glob('*.START.json')}
            if (result.get('completed') is not False or not starts <= set(items)
                    or not set(rows) <= starts or len(rows) != len(responses)
                    or result.get('request_count') != len(starts)
                    or blocked[name].get('request_count') != len(starts)):
                raise ValueError('COLLECTOR_CONTINUATION_PARTIAL_COUNT_CONFLICT')
            local_count += len(starts)
            for filename in sorted(starts):
                item, row = items[filename], rows.get(filename)
                start_path = directory / (filename + '.START.json')
                _file_hash(start_path)
                start = json.loads(start_path.read_text(encoding='utf-8'))
                if {key: start.get(key) for key in item} != item or not start.get('started_at'):
                    raise ValueError('COLLECTOR_REQUEST_START_IDENTITY_CONFLICT')
                if row is not None and row.get('error_code') == '0':
                    witness = verify_collected_response_v1(directory, item, row)
                    sources.append(_success_reference(root, name, item, row, result_hash, witness, False))
                    partial_count += 1
                    local_successes += 1
                else:
                    local_failures.add(_hash({'api': item['api'], 'request': item['request']}))
    collections = [*lineage['collections'], _collection_reference(root, frozen, events,
        local_count, local_successes, partial_count)]
    operations = [source['operation_identity'] for source in sources]
    if (len(set(operations)) != len(operations)
            or len(sources) != lineage['success_count'] + local_successes):
        raise ValueError('COLLECTOR_CONTINUATION_SUCCESS_SET_CONFLICT')
    if complete and len(sources) != lineage['root_planned_request_count']:
        raise ValueError('COLLECTOR_CONTINUATION_SUCCESS_COUNT_CONFLICT')
    order = {entry['root']: index for index, entry in enumerate(collections)}
    sources.sort(key=lambda source: (order[source['collection_root']],
        source['batch_id'], source['request_item']['file']))
    retry_counts = dict(lineage['retry_counts_by_operation'])
    for operation in local_failures:
        retry_counts[operation] = retry_counts.get(operation, 0) + 1
    return {**lineage, 'sources': sources, 'collections': collections,
        'request_count': lineage['request_count'] + local_count,
        'success_count': len(sources), 'retry_counts_by_operation': retry_counts, 'complete': complete}


def _write_state(root, frozen, queue, events, status):
    completed = {entry['batch_id']: entry for entry in events
                 if entry['event'] == 'BATCH_COMPLETED'}
    blocked = [entry for entry in events if entry['event'] in {'BATCH_BLOCKED', 'LOGIN_BLOCKED'}]
    prior_count = queue.get('continuation', {}).get('previous_request_count', 0)
    state = {'version': VERSION, 'status': status, 'queue_sha256': frozen['queue_sha256'],
        'authorization_source': frozen['authorization_source'],
        'batch_count': len(queue['batches']), 'completed_batch_count': len(completed),
        'remaining_batch_count': len(queue['batches']) - len(completed),
        'request_count': prior_count + sum(entry.get('request_count', 0) for entry in completed.values())
                         + sum(entry.get('request_count', 0) for entry in blocked),
        'planned_request_count': frozen['request_count'],
        'completed_batch_ids': list(completed), 'blockers': blocked,
        'last_event_hash': events[-1]['event_hash'] if events else None,
        'automatic_retry': False, 'account_execution_authorized': False,
        'independent_confirmation_eligible': False}
    if queue.get('continuation') is not None:
        state.update(prior_request_count=prior_count,
            session_request_count=state['request_count'] - prior_count,
            prior_success_count=queue['continuation']['already_success_count'],
            continuation_receipt_sha256=queue['continuation']['receipt_sha256'])
    temp = root / 'COLLECTION_STATE.json.tmp'
    with temp.open('wb') as stream:
        stream.write(_canonical(state))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, root / 'COLLECTION_STATE.json')
    return state


def _collect_locked(root, raw, queue, *, max_batches, progress_every, client):
    frozen = _initialize(root, raw, queue)
    events = _read_events(root, frozen)
    completed = {entry['batch_id']: entry for entry in events if entry['event'] == 'BATCH_COMPLETED'}
    starts = {entry['batch_id']: entry for entry in events if entry['event'] == 'BATCH_STARTED'}
    if any(entry['event'] in {'BATCH_BLOCKED', 'LOGIN_BLOCKED'} for entry in events):
        _write_state(root, frozen, queue, events, 'BLOCKED')
        raise RuntimeError('COLLECTOR_BLOCKED_NO_AUTOMATIC_RETRY')
    for plan in queue['batches']:
        name = plan['batch_id']
        if name in completed:
            _verify_success(root, plan, completed[name])
        elif name in starts:
            try:
                success = _verify_success(root, plan)
            except (ValueError, OSError, KeyError) as exc:
                if not any(entry.get('batch_id') == name and entry['event'] == 'BATCH_BLOCKED' for entry in events):
                    directory = root / 'batches' / name
                    count = len(list(directory.glob('*.START.json'))) if directory.exists() else 0
                    _append_event(root, frozen, events, 'BATCH_BLOCKED', batch_id=name,
                        plan_hash=frozen['batch_hashes'][name], reason=type(exc).__name__,
                        request_count=count, interrupted=True)
            else:
                completed[name] = _append_event(root, frozen, events, 'BATCH_COMPLETED',
                    batch_id=name, plan_hash=frozen['batch_hashes'][name], recovered=True, **success)
    if any(entry['event'] in {'BATCH_BLOCKED', 'LOGIN_BLOCKED'} for entry in events):
        _write_state(root, frozen, queue, events, 'BLOCKED')
        raise RuntimeError('COLLECTOR_BLOCKED_NO_AUTOMATIC_RETRY')
    remaining = [plan for plan in queue['batches'] if plan['batch_id'] not in completed]
    if not remaining:
        return _write_state(root, frozen, queue, events, 'COMPLETED')
    _write_state(root, frozen, queue, events, 'READY')
    if client is None:
        import baostock as client
    socket.setdefaulttimeout(30)
    try:
        login = client.login()
    except Exception as exc:
        _append_event(root, frozen, events, 'LOGIN_BLOCKED', reason=type(exc).__name__,
                      request_count=0)
        _write_state(root, frozen, queue, events, 'BLOCKED')
        raise
    if login.error_code != '0':
        _append_event(root, frozen, events, 'LOGIN_BLOCKED', error_code=login.error_code,
                      request_count=0)
        _write_state(root, frozen, queue, events, 'BLOCKED')
        raise RuntimeError('BAOSTOCK_LOGIN_FAILED')
    plans_root = root / 'plans'
    executed = 0
    try:
        _append_event(root, frozen, events, 'SESSION_CONNECTED')
        plans_root.mkdir(exist_ok=True)
        for plan in remaining:
            if max_batches is not None and executed >= max_batches:
                break
            name = plan['batch_id']
            plan_path = plans_root / (name + '.json')
            if plan_path.resolve() != plan_path:
                raise ValueError('COLLECTOR_BATCH_PLAN_REDIRECTED')
            if plan_path.exists():
                if _hash(json.loads(plan_path.read_text(encoding='utf-8'))) != frozen['batch_hashes'][name]:
                    raise ValueError('COLLECTOR_BATCH_PLAN_CHANGED')
            else:
                acquisition.put(plan_path, plan)
            _append_event(root, frozen, events, 'BATCH_STARTED', batch_id=name,
                          plan_hash=frozen['batch_hashes'][name])
            directory = root / 'batches' / name
            try:
                if directory.resolve() != directory:
                    raise ValueError('COLLECTOR_BATCH_PATH_REDIRECTED')
                acquisition.execute(plan_path, directory, connected_client=client)
                success = _verify_success(root, plan)
            except Exception as exc:
                count = len(list(directory.glob('*.START.json'))) if directory.exists() else 0
                _append_event(root, frozen, events, 'BATCH_BLOCKED', batch_id=name,
                    plan_hash=frozen['batch_hashes'][name], reason=type(exc).__name__,
                    request_count=count, interrupted=False)
                _write_state(root, frozen, queue, events, 'BLOCKED')
                raise
            completed[name] = _append_event(root, frozen, events, 'BATCH_COMPLETED',
                batch_id=name, plan_hash=frozen['batch_hashes'][name], recovered=False, **success)
            executed += 1
            _write_state(root, frozen, queue, events, 'RUNNING')
            if executed % progress_every == 0 or len(completed) == len(queue['batches']):
                print(f"已完成 {len(completed)}/{len(queue['batches'])} 批；"
                      f"本次新增 {executed} 批。", flush=True)
    finally:
        client.logout()
    status = 'COMPLETED' if len(completed) == len(queue['batches']) else 'PAUSED_BY_BATCH_LIMIT'
    _append_event(root, frozen, events, 'SESSION_FINISHED', status=status)
    return _write_state(root, frozen, queue, events, status)


def collect(batches, output_root, *, max_batches=None, progress_every=50, client=None):
    """维护者队列内的顺序补采；client 仅用于测试或内部受信会话。"""
    if (max_batches is not None and (type(max_batches) is not int or max_batches <= 0)
            or type(progress_every) is not int or progress_every <= 0):
        raise ValueError('COLLECTOR_LIMIT_INVALID')
    raw, queue = _load_queue(Path(batches).absolute())
    root = Path(output_root).absolute()
    if root.resolve() != root:
        raise ValueError('COLLECTOR_OUTPUT_REDIRECTED')
    verify_continuation_lineage_v1(raw, queue, root)
    root.mkdir(parents=True, exist_ok=True)
    with ObjectiveMutationLock.for_resource(root / 'COLLECTION_STATE.json'):
        return _collect_locked(root, raw, queue, max_batches=max_batches,
                               progress_every=progress_every, client=client)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batches', required=True)
    parser.add_argument('--output-root', required=True)
    parser.add_argument('--max-batches', type=int)
    parser.add_argument('--progress-every', type=int, default=50)
    args = parser.parse_args()
    state = collect(args.batches, args.output_root, max_batches=args.max_batches,
                    progress_every=args.progress_every)
    print(f"采集状态：{state['status']}；已完成 {state['completed_batch_count']} 批；"
          f"实际请求 {state['request_count']} 次。", flush=True)
