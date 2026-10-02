"""只读核验失败采集并冻结有限的显式续采清单；不联网，不改原件或预算。"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import collect_universe_gaps_v1 as collector
from scripts import fetch_research_baostock_v1 as acquisition


VERSION = 'UNIVERSE_COLLECTION_CONTINUATION_V1'


def _metadata(path):
    digest = collector._file_hash(path)
    return json.loads(path.read_text(encoding='utf-8')), digest


def _operation(item):
    return collector._hash({'api': item['api'], 'request': item['request']})


def _verify_start(directory, item):
    path = directory / (item['file'] + '.START.json')
    value, digest = _metadata(path)
    if {key: value.get(key) for key in item} != item or not value.get('started_at'):
        raise ValueError('CONTINUATION_REQUEST_START_IDENTITY_CONFLICT')
    stamp = datetime.fromisoformat(value['started_at'])
    if stamp.tzinfo is None:
        raise ValueError('CONTINUATION_REQUEST_START_TIME_INVALID')
    return {'start_sha256': digest, 'requested_at_utc': value['started_at']}


def _remaining_batches(items, source):
    batches = []
    for item in items:
        if (not batches or len(batches[-1]['requests']) == 9
                or item['file'] in {row['file'] for row in batches[-1]['requests']}):
            batches.append({'batch_id': f'CONTINUE_{len(batches) + 1:05d}',
                'purpose': 'EXPLORATORY_ENGINEERING_ACCEPTANCE',
                'authorization_scope': source['scope'], 'max_requests': 9,
                'requests': [], 'execution_status': 'NOT_EXECUTED',
                'collector': 'scripts/collect_universe_gaps_v1.py'})
        batches[-1]['requests'].append(item)
    for plan in batches:
        acquisition.validate_plan(plan)
    return batches


def prepare_continuation(collection_root, output_root, *, collection_output_root, reason):
    """复用父链成功原件；同一操作最多三次显式重试，每个失败目录只登记一个后继。"""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('CONTINUATION_EXPLICIT_REASON_REQUIRED')
    root, output = Path(collection_root).absolute(), Path(output_root).absolute()
    target = Path(collection_output_root).absolute()
    if root.resolve() != root or output.resolve() != output or target.resolve() != target:
        raise ValueError('CONTINUATION_PATH_REDIRECTED')
    if output.exists() or output.is_relative_to(root) or target.is_relative_to(root) or target == output:
        raise ValueError('CONTINUATION_NEW_SEPARATE_OUTPUT_REQUIRED')
    queue_path = root / 'ACQUISITION_BATCHES.json'
    queue_digest = collector._file_hash(queue_path)
    queue_raw, queue = collector._load_queue(queue_path)
    lineage = collector.verify_continuation_lineage_v1(queue_raw, queue, root)
    frozen, frozen_digest = _metadata(root / 'FROZEN_COLLECTION.json')
    if frozen != collector._collection_scope(queue_raw, queue):
        raise ValueError('CONTINUATION_FROZEN_QUEUE_OR_SCOPE_CHANGED')
    journal_path = root / 'COLLECTION_JOURNAL.jsonl'
    journal_digest = collector._file_hash(journal_path)
    control_path = collector.continuation_control_path_v1({
        'original_collection_root': str(root), 'original_queue_sha256': queue_digest,
        'original_journal_sha256': journal_digest})
    if control_path.exists():
        raise ValueError('CONTINUATION_ALREADY_REGISTERED')
    events = collector._read_events(root, frozen)
    completed, blocked, started = {}, {}, set()
    login_blocked = False
    for event in events:
        status = event['event']
        if status == 'BATCH_STARTED':
            if event['batch_id'] in started:
                raise ValueError('CONTINUATION_DUPLICATE_BATCH_START')
            started.add(event['batch_id'])
        elif status in {'BATCH_COMPLETED', 'BATCH_BLOCKED'}:
            name = event['batch_id']
            if name in completed or name in blocked or name not in started:
                raise ValueError('CONTINUATION_BATCH_STATE_CONFLICT')
            (completed if status == 'BATCH_COMPLETED' else blocked)[name] = event
        elif status == 'LOGIN_BLOCKED':
            if event.get('request_count') != 0:
                raise ValueError('CONTINUATION_LOGIN_REQUEST_COUNT_CONFLICT')
            login_blocked = True
    if not blocked and not login_blocked:
        raise ValueError('CONTINUATION_ORIGINAL_BLOCKER_REQUIRED')
    if started != set(completed) | set(blocked):
        raise ValueError('CONTINUATION_UNRESOLVED_BATCH_REQUIRED')

    remaining, successful, partial, failures, complete_evidence, blocked_evidence = [], set(), [], [], [], []
    attempted, unstarted = 0, 0
    stable_files = {queue_path: queue_digest,
        root / 'FROZEN_COLLECTION.json': frozen_digest, journal_path: journal_digest}
    for plan in queue['batches']:
        name = plan['batch_id']
        directory = root / 'batches' / name
        if name in completed:
            verified = collector._verify_success(root, plan, completed[name])
            attempted += verified['request_count']
            successful.update(_operation(item) for item in plan['requests'])
            complete_evidence.append({'batch_id': name, 'event_hash': completed[name]['event_hash'],
                'result_sha256': verified['result_sha256'], 'request_count': verified['request_count']})
            stable_files[directory / 'ACQUISITION_RESULT.json'] = verified['result_sha256']
            continue
        if name not in blocked:
            if directory.exists():
                raise ValueError('CONTINUATION_UNREGISTERED_BATCH_FILES')
            remaining.extend(plan['requests'])
            unstarted += len(plan['requests'])
            continue
        if directory.resolve() != directory:
            raise ValueError('CONTINUATION_BATCH_PATH_REDIRECTED')
        actual_plan, plan_digest = _metadata(directory / 'ACQUISITION_PLAN.json')
        if collector._hash(actual_plan) != collector._hash(plan):
            raise ValueError('CONTINUATION_BATCH_PLAN_CHANGED')
        result_path = directory / 'ACQUISITION_RESULT.json'
        result, result_digest = _metadata(result_path)
        rows = result.get('responses')
        expected = {item['file']: item for item in plan['requests']}
        if (result.get('completed') is not False or not isinstance(rows, list)
                or any(not isinstance(row, dict) or row.get('file') not in expected for row in rows)
                or len({row['file'] for row in rows}) != len(rows)):
            raise ValueError('CONTINUATION_PARTIAL_RESULT_INVALID')
        rows_by_file = {row['file']: row for row in rows}
        starts = {path.name[:-len('.START.json')] for path in directory.glob('*.START.json')}
        if (not starts <= set(expected) or not set(rows_by_file) <= starts
                or result.get('request_count') != len(starts)
                or blocked[name].get('request_count') != len(starts)):
            raise ValueError('CONTINUATION_PARTIAL_REQUEST_COUNT_CONFLICT')
        attempted += len(starts)
        stable_files[directory / 'ACQUISITION_PLAN.json'] = plan_digest
        stable_files[result_path] = result_digest
        blocked_evidence.append({'batch_id': name, 'event_hash': blocked[name]['event_hash'],
            'result_sha256': result_digest, 'request_count': len(starts), 'parent_batch_completed': False})
        for item in plan['requests']:
            row = rows_by_file.get(item['file'])
            operation = _operation(item)
            if row is not None and row.get('error_code') == '0':
                witness = collector.verify_collected_response_v1(directory, item, row)
                successful.add(operation)
                partial.append({'batch_id': name, 'operation_identity': operation,
                    'parent_batch_completed': False, 'parent_status': 'BLOCKED', **witness})
                stable_files[Path(witness['source_path'])] = witness['source_sha256']
                stable_files[directory / (item['file'] + '.START.json')] = witness['start_sha256']
            else:
                remaining.append(item)
                if item['file'] in starts:
                    witness = _verify_start(directory, item)
                    stable_files[directory / (item['file'] + '.START.json')] = witness['start_sha256']
                    failures.append({'batch_id': name, 'operation_identity': operation,
                        'request': item['request'], 'api': item['api'], **witness,
                        'result_sha256': result_digest,
                        'error_code': row.get('error_code') if row else None,
                        'request_verified': False, 'parent_batch_completed': False,
                        'retry_count_in_new_queue': 1})
                else:
                    unstarted += 1

    operations = [_operation(item) for item in remaining]
    if (len(operations) != len(set(operations)) or set(operations) & successful
            or len(successful) + len(operations) != queue['request_count']
            or len(successful) + len(failures) != attempted
            or unstarted + len(failures) != len(operations)):
        raise ValueError('CONTINUATION_OPERATION_ACCOUNTING_CONFLICT')
    if not remaining:
        raise ValueError('CONTINUATION_NO_REMAINING_OPERATIONS')
    retry_counts = dict(lineage['retry_counts_by_operation'])
    for failure in failures:
        operation = failure['operation_identity']
        retry_number = retry_counts.get(operation, 0) + 1
        if retry_number > collector.MAX_EXPLICIT_OPERATION_RETRIES:
            raise ValueError('CONTINUATION_OPERATION_RETRY_LIMIT:' + operation)
        retry_counts[operation] = retry_number
        failure['operation_explicit_retry_number'] = retry_number
    cumulative_count = lineage['request_count'] + attempted
    cumulative_success = lineage['success_count'] + len(successful)
    parent = queue.get('continuation')
    receipt_path = output / 'READONLY_CONTINUATION_RECEIPT.json'
    receipt = {'version': VERSION, 'original_collection_root': str(root),
        'original_queue_sha256': queue_digest, 'original_freeze_sha256': frozen_digest,
        'original_journal_sha256': journal_digest, 'authorization_source': queue['authorization_source'],
        'explicit_continuation_reason': reason.strip(),
        'original_planned_request_count': queue['request_count'],
        'original_request_count': cumulative_count, 'already_success_count': cumulative_success,
        'local_request_count': attempted, 'local_success_count': len(successful),
        'cumulative_request_count': cumulative_count, 'cumulative_success_count': cumulative_success,
        'root_planned_request_count': lineage['root_planned_request_count'],
        'parent_receipt_path': parent['receipt_path'] if parent else None,
        'parent_receipt_sha256': parent['receipt_sha256'] if parent else None,
        'chain_generation': lineage['generation'] + 1,
        'explicit_operation_retry_limit': collector.MAX_EXPLICIT_OPERATION_RETRIES,
        'retry_limit_semantics': 'ORIGINAL_ATTEMPT_PLUS_AT_MOST_THREE_EXPLICIT_RETRIES',
        'retry_counts_by_operation': retry_counts,
        'retry_failed_count': len(failures), 'unstarted_count': unstarted,
        'remaining_request_count': len(remaining), 'remaining_operation_identities': operations,
        'completed_batch_evidence': complete_evidence, 'blocked_batch_evidence': blocked_evidence,
        'partial_success_witnesses': partial, 'failed_request_retries': failures,
        'original_files_changed': False, 'automatic_execution': False, 'automatic_retry': False,
        'account_execution_authorized': False, 'independent_validation_authorized': False,
        'independent_confirmation_eligible': False}
    receipt_raw = collector._canonical(receipt)
    next_queue = {'version': queue['version'], 'authorization_source': queue['authorization_source'],
        'request_count': len(remaining), 'batches': _remaining_batches(remaining, queue['authorization_source']),
        'automatic_execution': False, 'automatic_retry': False,
        'account_execution_authorized': False, 'independent_validation_authorized': False,
        'continuation': {'receipt_path': str(receipt_path),
            'receipt_sha256': hashlib.sha256(receipt_raw).hexdigest(),
            'previous_request_count': cumulative_count, 'already_success_count': cumulative_success,
            'retry_failed_count': len(failures)}}
    for path, digest in stable_files.items():
        if collector._file_hash(path) != digest:
            raise ValueError('CONTINUATION_ORIGINAL_CHANGED_DURING_VERIFICATION')
    output.mkdir(parents=True, exist_ok=False)
    with receipt_path.open('xb') as stream:
        stream.write(receipt_raw)
    with (output / 'ACQUISITION_BATCHES.json').open('xb') as stream:
        stream.write(collector._canonical(next_queue))
    collector.register_continuation_v1(output / 'ACQUISITION_BATCHES.json', target, reason=reason)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection-root')
    parser.add_argument('--output-root')
    parser.add_argument('--collection-output-root', required=True)
    parser.add_argument('--register-existing', action='store_true')
    parser.add_argument('--batches')
    parser.add_argument('--reason', required=True)
    args = parser.parse_args()
    if args.register_existing:
        if not args.batches or args.collection_root or args.output_root:
            parser.error('--register-existing 要求 --batches，不接受创建计划的目录参数')
        result = collector.register_continuation_v1(args.batches, args.collection_output_root,
            reason=args.reason)
        print(f"唯一续采已登记：{result['collection_root']}。未联网或修改原清单。")
    else:
        if not args.collection_root or not args.output_root or args.batches:
            parser.error('创建计划要求 --collection-root 和 --output-root')
        result = prepare_continuation(args.collection_root, args.output_root,
            collection_output_root=args.collection_output_root, reason=args.reason)
        print(f"原请求 {result['original_request_count']} 次，已成功 {result['already_success_count']} 次；"
              f"新清单 {result['remaining_request_count']} 项，含显式重试 {result['retry_failed_count']} 项。")
