"""原两账户用途的有限附加限时证明；不签发 Owner 能力、不修改经济状态。"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from chanlun_trader.account_startup_boundary_v1 import startup_checkpoint
from .campaign_scope_v1 import OwnerApprovalStoreV1, _write_once, replay_grant, stage_remaining
from .common import stable_hash
from .mutation_boundary import ObjectiveMutationLock
from .secure_file_reference_v1 import (
    checked_directory_path, file_sha256, read_file_bytes, read_pinned_json, validated_reference_path,
)
from .universe_execution_profile_v1 import SEGMENTED_PROFILE, segment_charge, validate_execution_profile


VERSION = 'ACCOUNT_PURPOSE_TIME_EXTENSION_MANIFEST_V1'
SUMMARY_VERSION = 'OWNER_ACCOUNT_PURPOSE_TIME_EXTENSION_SUMMARY_V1'
OLD_TOTAL_SECONDS = 28800
TOTAL_SECONDS = 72000
ADDITIONAL_SECONDS = TOTAL_SECONDS - OLD_TOTAL_SECONDS
GOVERNANCE_PATHS = frozenset({
    'src/chanlun_trader/research_factory/account_purpose_time_extension_v1.py',
    'scripts/setup_account_purpose_time_extension_v1.py',
    'src/chanlun_trader/research_factory/account_version_continuation_v1.py',
    'src/chanlun_trader/research_factory/account_version_capability_bridge_v1.py',
    'src/chanlun_trader/research_factory/report_repair_capability_bridge_v1.py',
    'src/chanlun_trader/research_factory/campaign_scope_v1.py',
    'src/chanlun_trader/research_factory/run_budget.py',
    'src/chanlun_trader/research_factory/research_campaign_v1.py',
    'src/chanlun_trader/research_factory/etf_account_governance_v1.py',
    'src/chanlun_trader/research_factory/universe_execution_recovery_v1.py',
    'src/chanlun_trader/research_factory/continuous_submission_v1.py',
    'src/chanlun_trader/research_factory/business_validation_protocol_v1.py',
    'scripts/run_strategy_account_v1.py',
    'scripts/run_account_version_session_resume_v1.py',
})


def _require(condition, reason):
    if not condition:
        raise PermissionError('ACCOUNT_PURPOSE_TIME_EXTENSION_' + reason)


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _identity(value, field='identity'):
    return isinstance(value, dict) and value.get(field) == stable_hash({k: v for k, v in value.items() if k != field})


def _path(value, root):
    return validated_reference_path(value, root=root, error_code='ACCOUNT_PURPOSE_TIME_EXTENSION_PATH_INVALID')


def _reference(path, root):
    startup_checkpoint('TIME_EXTENSION_REFERENCE')
    # 安全读取器仍完整核打开前后路径和对象；成功路径不再额外遍历第三次。
    try:
        digest = file_sha256(path, root=root, error_code='ACCOUNT_PURPOSE_TIME_EXTENSION_REFERENCE_CHANGED')
    except ValueError:
        _path(path, root)  # 错误路径仍沿用原 PATH_INVALID 分类。
        raise
    return {'path': str(Path(path)), 'sha256': digest}


def _read(reference, root):
    startup_checkpoint('TIME_EXTENSION_READ')
    return read_pinned_json(reference, root=root, error_code='ACCOUNT_PURPOSE_TIME_EXTENSION_REFERENCE_CHANGED')


def _refs(value, root):
    """固定引用列表不得通过重复或缺失项改变允许的源码集合。"""
    _require(isinstance(value, list), 'REFERENCES_REQUIRED')
    result = []
    for reference in value:
        startup_checkpoint('TIME_EXTENSION_REFERENCES')
        _require(isinstance(reference, dict) and set(reference) == {'path', 'sha256'}
            and _hash(reference['sha256']) and _reference(reference['path'], root) == reference,
            'REFERENCE_CHANGED')
        result.append(deepcopy(reference))
    _require(len({row['path'] for row in result}) == len(result), 'REFERENCES_DUPLICATED')
    return sorted(result, key=lambda row: row['path'])


def _source_inventory(value):
    result = {}
    if isinstance(value, list):
        for member in value:
            for path, digest in _source_inventory(member).items():
                _require(path not in result or result[path] == digest, 'SOURCE_INVENTORY_CONFLICT')
                result[path] = digest
    elif isinstance(value, dict):
        for key, member in value.items():
            if key == 'source_hashes':
                _require(isinstance(member, dict), 'SOURCE_INVENTORY_INVALID')
                for path, digest in member.items():
                    _require(isinstance(path, str) and _hash(digest), 'SOURCE_INVENTORY_INVALID')
                    _require(path not in result or result[path] == digest, 'SOURCE_INVENTORY_CONFLICT')
                    result[path] = digest
            else:
                for path, digest in _source_inventory(member).items():
                    _require(path not in result or result[path] == digest, 'SOURCE_INVENTORY_CONFLICT')
                    result[path] = digest
    return result


def project_governance_plan(plan, replacements):
    """只投影 runtime.source_hashes；backend/strategy/profile 等经济描述完全保留。"""
    result = deepcopy(plan)
    hashes = result.get('runtime', {}).get('source_hashes')
    _require(isinstance(hashes, dict), 'RUNTIME_SOURCE_HASHES_REQUIRED')
    for row in replacements:
        if row['path'] in hashes:
            _require(hashes[row['path']] == row['old_sha256'], 'PLAN_SOURCE_CONFLICT')
            hashes[row['path']] = row['new_sha256']
    result['plan_id'] = stable_hash({k: v for k, v in result.items() if k != 'plan_id'})
    return result


def _authorization(root, authorization_id):
    _require(isinstance(authorization_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,120}', authorization_id),
        'AUTHORIZATION_ID_INVALID')
    directory = root / 'reports' / 'research_campaigns' / stable_hash(authorization_id)
    reference = _reference(directory / 'authorization.json', root)
    base = _read(reference, root)
    _require(base.get('root') == os.path.normcase(str(root)) and base.get('authorization_id') == authorization_id,
        'AUTHORIZATION_CONFLICT')
    policy = base.get('scope_policy', {})
    _require(_identity(policy, 'scope_hash') and policy.get('schema_version') == 'CONTINUOUS_CAMPAIGN_SCOPE_V1',
        'ORIGINAL_SCOPE_INVALID')
    owner = checked_directory_path(policy['approval_store'], error_code='ACCOUNT_PURPOSE_TIME_EXTENSION_OWNER_INVALID')
    approvals = OwnerApprovalStoreV1.from_checked_directory(owner)
    approvals.require(policy['approval_ref'], policy['summary'])
    return directory, reference, base, approvals


def _ledger_boundary(root, directory, base, frozen=None):
    """安装摘要只读原事件前缀；完整治理重放由原预算安装/派发入口另行执行。

    本读取器不调用 campaign，防止 scope→proof→campaign 的自举递归。冻结后
    只核对已批准前缀；后续新段不能改变安装时的两用途身份、费用或可用预留。
    """
    path = _path(directory / 'run_budget_events.jsonl', root)
    raw = read_file_bytes(path, root=root, error_code='ACCOUNT_PURPOSE_TIME_EXTENSION_LEDGER_CHANGED')
    lines = raw.splitlines(keepends=True)
    if frozen is not None:
        _require(frozen.get('path') == str(path) and type(frozen.get('line_count')) is int
            and 0 < frozen['line_count'] <= len(lines), 'LEDGER_PREFIX_CHANGED')
        lines = lines[:frozen['line_count']]
        _require(hashlib.sha256(b''.join(lines)).hexdigest() == frozen.get('prefix_sha256'), 'LEDGER_PREFIX_CHANGED')
    _require(lines and all(line.strip() for line in lines), 'LEDGER_INVALID')
    events = [json.loads(line) for line in lines]
    ids = set(); operations = {}; effective = None; revoked = False; extension_seen = False
    for event in events:
        startup_checkpoint('TIME_EXTENSION_LEDGER_REPLAY')
        _require(event.get('run_id') == base['authorization_id'] and event.get('event_id') == stable_hash({
            'run_id': event['run_id'], 'event_type': event['event_type'], 'payload': event['payload']})
            and event['event_id'] not in ids, 'LEDGER_EVENT_INVALID')
        ids.add(event['event_id']); kind, item = event['event_type'], event['payload']
        if kind == 'CAMPAIGN_AUTHORIZED':
            _require(effective is None and item == base, 'AUTHORIZATION_EVENT_CONFLICT')
            effective = deepcopy(base)
        elif kind == 'CAMPAIGN_GRANT_ADDED':
            _require(effective is not None and not revoked, 'GRANT_EVENT_CONFLICT')
            effective = replay_grant(base, effective, item)
        elif kind == 'CAMPAIGN_REVOKED':
            revoked = True
        elif kind == 'CAMPAIGN_ACCOUNT_PURPOSE_TIME_EXTENDED':
            extension_seen = True
        elif kind == 'CAMPAIGN_OPERATION_RESERVED':
            _require(item.get('operation_id') not in operations, 'OPERATION_DUPLICATED')
            operations[item['operation_id']] = {**deepcopy(item), 'status': 'RESERVED'}
        elif kind in ('CAMPAIGN_OPERATION_STARTED', 'CAMPAIGN_OPERATION_UNKNOWN', 'CAMPAIGN_OPERATION_SETTLED'):
            op = operations.get(item.get('operation_id'))
            _require(op is not None and op['status'] not in ('COMPLETED', 'FAILED'), 'OPERATION_EVENT_CONFLICT')
            op.update(deepcopy(item))
            op['status'] = {'CAMPAIGN_OPERATION_STARTED': 'RUNNING', 'CAMPAIGN_OPERATION_UNKNOWN': 'UNKNOWN',
                'CAMPAIGN_OPERATION_SETTLED': item.get('outcome')}[kind]
        elif kind == 'CAMPAIGN_EXECUTION_SEGMENT_DISPATCHED':
            op = operations.get(item.get('operation_id'))
            _require(op is not None and op['status'] == 'RUNNING' and op.get('active_segment') is None,
                'ACTIVE_SEGMENT_CONFLICT')
            profile = validate_execution_profile(op['execution_profile'])
            segments = op.setdefault('segments', []); upper = item.get('upper_bound_seconds')
            _require(item.get('segment_number') == len(segments) + 1 and item.get('profile_hash') == profile['profile_hash']
                and type(upper) in (int, float) and 0 < upper <= profile['worker_seconds']
                and upper <= profile['total_seconds'] - op.get('active_wall_seconds', 0)
                and (not segments or segments[-1].get('outcome') not in ('COMPLETED', 'FAILED')),
                'SEGMENT_EVENT_CONFLICT')
            segment = deepcopy(item); segments.append(segment); op['active_segment'] = segment
        elif kind == 'CAMPAIGN_EXECUTION_SEGMENT_CHARGED':
            op = operations.get(item.get('operation_id')); active = op.get('active_segment') if op else None
            _require(active is not None and op['status'] in ('RUNNING', 'UNKNOWN')
                and item.get('segment_number') == active['segment_number'], 'SEGMENT_CHARGE_CONFLICT')
            seconds, basis = segment_charge(item.get('measured_seconds'), active['upper_bound_seconds'],
                item.get('evidence_identity'), outcome=item.get('outcome'))
            _require(item.get('seconds') == seconds and item.get('basis') == basis, 'SEGMENT_CHARGE_CONFLICT')
            active.update(deepcopy(item)); op['active_wall_seconds'] = op.get('active_wall_seconds', 0) + seconds
            op['active_segment'] = None
        elif kind.startswith('CAMPAIGN_') and kind not in (
                'CAMPAIGN_PAUSED', 'CAMPAIGN_RESUMED', 'CAMPAIGN_STAGE_CHANGED'):
            _require(False, 'UNSUPPORTED_CAMPAIGN_EVENT')
    _require(effective is not None and not revoked and not extension_seen, 'EXTENSION_ALREADY_REGISTERED_OR_REVOKED')
    used = dict.fromkeys(effective['resource_limits'], 0); reserved = dict(used)
    for op in operations.values():
        destination = used if op['status'] in ('COMPLETED', 'FAILED') else reserved
        for name, amount in op.get('actual' if op['status'] in ('COMPLETED', 'FAILED') else 'upper_bounds', {}).items():
            _require(name in destination and type(amount) is int and amount >= 0, 'RESOURCE_UNITS_INVALID')
            destination[name] += amount
    remaining = {name: effective['resource_limits'][name] - used[name] - reserved[name] for name in used}
    view = {'authorization': effective, 'operations': operations, 'used': used, 'reserved': reserved}
    stages = stage_remaining(view)
    _require(min(remaining.values()) >= 0 and stages is not None
        and remaining['wall_seconds'] >= ADDITIONAL_SECONDS * 2
        and stages.get('EXPLORATION', {}).get('wall_seconds', -1) >= ADDITIONAL_SECONDS * 2,
        'RESERVATION_CAPACITY_INSUFFICIENT')
    reference = {'path': str(path), 'line_count': len(lines),
        'prefix_sha256': hashlib.sha256(b''.join(lines)).hexdigest(), 'event_head': events[-1]['event_id']}
    if frozen is not None:
        _require(reference == frozen, 'LEDGER_PREFIX_CHANGED')
    return reference, operations, effective


def _predecessor(root, reference, base, approvals):
    manifest = _read(reference, root)
    _require(set(manifest) == {'schema_version', 'summary', 'approval_ref', 'identity'}
        and manifest.get('schema_version') == 'ACCOUNT_VERSION_CONTINUATION_MANIFEST_V1' and _identity(manifest),
        'PREDECESSOR_MANIFEST_INVALID')
    value = manifest['summary']
    _require(value.get('schema_version') == 'OWNER_ACCOUNT_VERSION_CONTINUATION_SUMMARY_V1'
        and value.get('version_id') == stable_hash({k: v for k, v in value.items() if k not in ('version_id', 'active_plans')})
        and value.get('root') == str(root) and value.get('base_authorization_identity') == stable_hash(base)
        and value.get('scope_hash') == base['scope_policy']['scope_hash']
        and value.get('approval_store') == str(approvals.directory)
        and value.get('maximum_version_switches') == 1 and value.get('daily_commit_count_at_install') == 0,
        'PREDECESSOR_SCOPE_CONFLICT')
    approvals.require(manifest['approval_ref'], value)
    task_root = Path(value['original']['task']['path']).parent
    _require(Path(reference['path']) == task_root / 'account-version-continuations' / value['continuation_id'] / 'MANIFEST.json',
        'PREDECESSOR_PATH_CONFLICT')
    directory = root / 'reports' / 'research_campaigns' / stable_hash(base['authorization_id'])
    slot = _read(_reference(directory / 'account-version-continuation-slots' / (value['continuation_id'] + '.json'), root), root)
    _require(slot == {'continuation_id': value['continuation_id'], 'manifest_ref': reference}, 'PREDECESSOR_SLOT_CONFLICT')
    refs = [value['original'][key] for key in ('task', 'job', 'preview', 'receipt')]
    refs += value['original']['control'] + [value['proposal_ref'], value['ci_ref']]
    for row in value['accounts'].values():
        refs += row['segment_refs']
        if row['start']:
            refs.append(row['start'])
    _refs(refs, root)
    bridge_ref = value['capability_bridge_ref']
    _require(isinstance(bridge_ref, dict) and set(bridge_ref) == {'path', 'sha256', 'approval_ref'},
        'PREDECESSOR_BRIDGE_INVALID')
    bridge = _read({key: bridge_ref[key] for key in ('path', 'sha256')}, approvals.directory)
    _require(_identity(bridge) and bridge.get('schema_version') == 'OWNER_ACCOUNT_VERSION_CAPABILITY_BRIDGE_V1'
        and bridge.get('approval_ref') == bridge_ref['approval_ref']
        and bridge.get('summary', {}).get('base_authorization_identity') == stable_hash(base)
        and bridge['summary'].get('source_replacements') == value['source_replacements']
        and bridge['summary'].get('runtime_sources') == value['runtime_sources'], 'PREDECESSOR_BRIDGE_CONFLICT')
    approvals.require(bridge['approval_ref'], bridge['summary'])
    for row in value['source_replacements']:
        _require(row.get('archive_ref', {}).get('sha256') == row.get('old_sha256')
            and _reference(row['archive_ref']['path'], root) == row['archive_ref'], 'PREDECESSOR_ARCHIVE_CHANGED')
    return value


def _predecessor_inventory(root, previous, job):
    inventory = _source_inventory(previous['active_plans'])
    # JOB顶层含派发源码及输入，原 V1 已批准的替换给出当前旧治理基线。
    old_mapping = {row['path']: row['new_sha256'] for row in previous['source_replacements']}
    for path, digest in job['source_hashes'].items():
        expected = old_mapping.get(path, digest)
        _require(path not in inventory or inventory[path] == expected, 'PREDECESSOR_SOURCE_INVENTORY_CONFLICT')
        inventory[path] = expected
        if Path(path).suffix == '.py':
            archive = Path(job['root']) / 'source-archive' / (digest + '_' + Path(path).name)
            _require(_reference(archive, root)['sha256'] == digest, 'ORIGINAL_JOB_SOURCE_ARCHIVE_CHANGED')
    for reference in previous['runtime_sources']:
        _require(reference['path'] not in inventory or inventory[reference['path']] == reference['sha256'],
            'PREDECESSOR_SOURCE_INVENTORY_CONFLICT')
        inventory[reference['path']] = reference['sha256']
    return inventory


def _economic_inventory(root, previous, inventory):
    # 某治理文件如同时进入 backend/strategy 描述，它就是经济依赖，不能因目录允许而移出。
    economic = {path: digest for path, digest in inventory.items()
        if _path(path, root).relative_to(root).as_posix() not in GOVERNANCE_PATHS}
    for plan in previous['active_plans'].values():
        for path, digest in _source_inventory([plan['backend'], plan['strategy']]).items():
            _require(path not in economic or economic[path] == digest, 'ECONOMIC_SOURCE_INVENTORY_CONFLICT')
            economic[path] = digest
    return economic


def source_inventory(root, *, authorization_id, task_ref, predecessor_manifest_ref):
    """只读列出当前旧治理基线和经济库存；部署端据此生成新治理映射，不签发许可。"""
    root = checked_directory_path(root, error_code='ACCOUNT_PURPOSE_TIME_EXTENSION_ROOT_INVALID')
    _, _, base, approvals = _authorization(root, authorization_id)
    previous = _predecessor(root, predecessor_manifest_ref, base, approvals)
    _require(previous['original']['task'] == task_ref, 'TASK_OR_JOB_CONFLICT')
    job = _read(previous['original']['job'], root)
    inventory = _predecessor_inventory(root, previous, job)
    economic = [{'path': path, 'sha256': digest} for path, digest in sorted(_economic_inventory(root, previous, inventory).items())]
    _refs(economic, root)
    return {'source_hashes': inventory, 'economic_sources': economic,
        'predecessor_summary': deepcopy(previous), 'original_job': job}


def _sources(root, previous, job, replacements, runtime_refs, economic_refs):
    _require(isinstance(replacements, list) and replacements, 'GOVERNANCE_REPLACEMENTS_REQUIRED')
    inventory = _predecessor_inventory(root, previous, job)
    expected_economic = _economic_inventory(root, previous, inventory)
    mapping = {}
    for row in replacements:
        startup_checkpoint('TIME_EXTENSION_SOURCE_REPLACEMENT')
        _require(isinstance(row, dict) and set(row) == {'path', 'old_sha256', 'new_sha256', 'archive_ref'},
            'SOURCE_REPLACEMENT_FIELDS_INVALID')
        path = _path(row['path'], root)
        _require(path.relative_to(root).as_posix() in GOVERNANCE_PATHS and row['path'] not in mapping
            and inventory.get(row['path']) == row['old_sha256'] and _hash(row['new_sha256'])
            and row['old_sha256'] != row['new_sha256'], 'GOVERNANCE_SOURCE_ALLOWLIST_CONFLICT')
        _require(row['path'] not in expected_economic, 'ECONOMIC_DEPENDENCY_SOURCE_CHANGED')
        _require(row['archive_ref'].get('sha256') == row['old_sha256']
            and _reference(row['archive_ref']['path'], root / 'reports') == row['archive_ref'], 'SOURCE_ARCHIVE_CHANGED')
        _require(_reference(path, root)['sha256'] == row['new_sha256'], 'GOVERNANCE_SOURCE_CHANGED')
        mapping[row['path']] = row
    runtime = _refs(runtime_refs, root)
    _require(runtime and all(Path(ref['path']).relative_to(root).as_posix() in GOVERNANCE_PATHS for ref in runtime)
        and set(mapping) <= {row['path'] for row in runtime}
        and str(root / 'src/chanlun_trader/research_factory/account_purpose_time_extension_v1.py')
            in {row['path'] for row in runtime}, 'RUNTIME_CLOSURE_INVALID')
    economic = _refs(economic_refs, root)
    _require(economic and {row['path']: row['sha256'] for row in economic} == expected_economic,
        'ECONOMIC_SOURCE_INVENTORY_CONFLICT')
    for path, digest in inventory.items():
        startup_checkpoint('TIME_EXTENSION_SOURCE_VERIFY')
        _require(_reference(path, root)['sha256'] == (mapping[path]['new_sha256'] if path in mapping else digest),
            'UNAPPROVED_SOURCE_CHANGED')
    for reference in runtime:
        if reference['path'] in inventory:
            expected = mapping.get(reference['path'], {}).get('new_sha256', inventory[reference['path']])
            _require(reference['sha256'] == expected, 'RUNTIME_SOURCE_CONFLICT')
    return sorted(deepcopy(replacements), key=lambda row: row['path']), runtime, economic


def _local_segment_boundary(root, job, name, receipt, parent):
    """新 Owner 证明尚未冻结时，用原规范逐段复验，绝不从治理入口自举新能力。"""
    account_root = Path(job['root']); profile = validate_execution_profile(job['resources'])
    start = _read(_reference(account_root / (name + '_START.json'), root), root)
    _require(_identity(receipt, 'receipt_id') and receipt.get('strategy_plans') == job['plans']
        and receipt.get('input_identity') == job['input_identity'] and receipt.get('objective_id') == job['objective_id']
        and receipt.get('budget_path') == job['budget_path'] and start.get('receipt_id') == receipt['receipt_id']
        and start.get('kind') == name, 'START_OR_RECEIPT_CONFLICT')
    rows = []; charged = 0.; head = None; charge_paths = set()
    for path in sorted(account_root.glob(name + '_SEGMENT_*_DISPATCH.json')):
        number = len(rows) + 1; prefix = name + '_SEGMENT_' + str(number).zfill(6)
        dispatch = _read(_reference(path, root), root)
        _require(path.name == prefix + '_DISPATCH.json' and _identity(dispatch, 'dispatch_id')
            and dispatch.get('schema_version') == 'strategy-account-segment-dispatch-v1'
            and dispatch.get('segment_number') == number and dispatch.get('previous_head') == head
            and dispatch.get('receipt_id') == receipt['receipt_id'] and dispatch.get('kind') == name
            and dispatch.get('profile_hash') == profile['profile_hash'] and dispatch.get('charged_before') == charged
            and dispatch.get('time_extension_proof') is None and type(dispatch.get('upper_bound_seconds')) in (int, float)
            and 0 < dispatch['upper_bound_seconds'] <= min(profile['worker_seconds'], profile['total_seconds'] - charged)
            and (not rows or rows[-1]['charge']['outcome'] not in ('COMPLETED', 'FAILED')), 'LOCAL_DISPATCH_CHAIN_CONFLICT')
        charge_path = account_root / (prefix + '_CHARGE.json')
        _require(charge_path.exists(), 'PENDING_SEGMENT')
        charge_paths.add(charge_path); charge = _read(_reference(charge_path, root), root)
        seconds, basis = segment_charge(charge.get('measured_seconds'), dispatch['upper_bound_seconds'],
            charge.get('evidence_identity'), outcome=charge.get('outcome'))
        _require(_identity(charge, 'charge_id') and charge.get('schema_version') == 'strategy-account-segment-charge-v1'
            and charge.get('dispatch_id') == dispatch['dispatch_id'] and charge.get('seconds') == seconds
            and charge.get('basis') == basis and charge.get('time_extension_proof') is None
            and charge.get('outcome') in ('CONTINUE', 'PAUSED', 'COMPLETED', 'FAILED'), 'LOCAL_CHARGE_CHAIN_CONFLICT')
        resource_path = account_root / (prefix + '_RESOURCE.json')
        if charge['measured_seconds'] is not None:
            _require(resource_path.exists(), 'MEASURED_RESOURCE_REQUIRED')
            resource_ref = _reference(resource_path, root)
            _require(resource_ref['sha256'] == charge['evidence_identity'], 'MEASURED_RESOURCE_SHA_CONFLICT')
            resource = _read(resource_ref, root)
            _require(resource.get('dispatch_id') == dispatch['dispatch_id'], 'MEASURED_RESOURCE_DISPATCH_CONFLICT')
            _require(resource.get('elapsed_wall_seconds') == charge['measured_seconds'], 'MEASURED_RESOURCE_SECONDS_CONFLICT')
        else:
            _require(charge['evidence_identity'] is None and charge['basis'] == 'UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND',
                'UNKNOWN_RESOURCE_BASIS_CONFLICT')
        charged += seconds; head = charge['charge_id']; rows.append({'dispatch': dispatch, 'charge': charge})
    _require(set(account_root.glob(name + '_SEGMENT_*_CHARGE.json')) == charge_paths, 'ORPHAN_LOCAL_CHARGE')
    parents = parent.get('segments', [])
    _require(parent.get('active_segment') is None and len(parents) == len(rows)
        and parent.get('active_wall_seconds', 0) == charged, 'PARENT_SEGMENT_PREFIX_CONFLICT')
    for parent_row, local in zip(parents, rows):
        dispatch, charge = local['dispatch'], local['charge']
        _require(parent_row.get('operation_id') == parent['operation_id']
            and parent_row.get('segment_number') == dispatch['segment_number']
            and all(parent_row.get(key) == dispatch.get(key) for key in (
                'profile_hash', 'upper_bound_seconds', 'dispatched_at'))
            and all(parent_row.get(key) == charge.get(key) for key in (
                'measured_seconds', 'seconds', 'basis', 'evidence_identity', 'outcome', 'charged_at')),
            'PARENT_SEGMENT_PREFIX_CONFLICT')
    return {'profile': profile, 'segments': rows, 'charged_seconds': charged, 'head': head,
        'pending': None, 'next_segment_number': len(rows) + 1}


def _checkpoint(root, job, name, *, frozen=None, exact=False):
    account_root = Path(job['root']); plan = job['plans'][name]
    path = _path(plan['runtime']['backend_options']['checkpoint_path'], account_root)
    if not path.exists():
        _require(frozen is None, 'CHECKPOINT_MISSING')
        _require(not any((account_root / (name + '_DAYS')).glob('*.json.gz')), 'ORPHAN_ACCOUNT_DAYS')
        return None
    reference = _reference(path, root); value = _read(reference, root)
    _require(_identity(value, 'state_identity') and value.get('version') == 'UNIVERSE_EXECUTION_STATE_V2'
        and _hash(value.get('execution_identity')), 'CHECKPOINT_INVALID')
    artifacts = value.get('artifacts', {})
    _require(_identity(artifacts, 'manifest_identity') and artifacts.get('version') == 'UNIVERSE_EXECUTION_ARTIFACTS_V1'
        and artifacts.get('identity') == value['execution_identity']
        and artifacts.get('root') == str(account_root / (name + '_DAYS')), 'DAY_MANIFEST_INVALID')
    calendar = plan['backend']['window']['calendar']; first = calendar.index(plan['backend']['window']['account_start'])
    days = artifacts.get('days'); _require(isinstance(days, list) and days, 'ACCOUNT_DAYS_REQUIRED')
    _require([row.get('date') for row in days] == calendar[first:first + len(days)]
        and value.get('last_day') == days[-1]['date'] and len(days) <= len(calendar) - first
        and value.get('calendar_identity') == stable_hash(calendar), 'ACCOUNT_CALENDAR_CONFLICT')
    head = stable_hash(['UNIVERSE_EXECUTION_ARTIFACTS_V1', value['execution_identity']]); day_refs = []
    for row in days:
        startup_checkpoint('TIME_EXTENSION_DAY_VERIFY')
        _require(row.get('previous') == head and _identity(row, 'chain') and _hash(row.get('payload_identity'))
            and isinstance(row.get('file'), str) and Path(row['file']).name == row['file'], 'DAY_CHAIN_CHANGED')
        reference_day = _reference(account_root / (name + '_DAYS') / row['file'], root)
        _require(reference_day['sha256'] == row.get('sha256'), 'DAY_BYTES_CHANGED')
        day_refs.append(reference_day); head = row['chain']
    _require(head == artifacts.get('head'), 'DAY_CHAIN_CHANGED')
    result = {'reference': reference, 'state_identity': value['state_identity'],
        'execution_identity': value['execution_identity'], 'last_day': value['last_day'],
        'processed_days': len(days), 'artifacts': artifacts, 'day_refs': day_refs}
    if frozen is not None:
        _require(value['execution_identity'] == frozen['execution_identity']
            and days[:frozen['processed_days']] == frozen['artifacts']['days'], 'CHECKPOINT_PREFIX_CHANGED')
        if exact:
            _require(result == {key: item for key, item in frozen.items() if key != 'archive_ref'},
                'CHECKPOINT_BOUNDARY_CHANGED')
    return result


def _session_boundary(root, previous):
    """会话 ERROR 或未复核的 ADVANCE_STARTED 不能被新治理证明掩盖。"""
    from .account_version_continuation_v1 import _session_reference
    references = previous.get('session_refs', {})
    _require(isinstance(references, dict), 'SESSION_REFERENCES_INVALID')
    for reference in references.values():
        _session_reference(reference, root, previous['approval_store'])
    if not references:
        return {'session_refs': {}, 'event_refs': [], 'event_head': None}
    _require(set(references) == {'config', 'runner', 'audit_start', 'bound_request', 'preview', 'freeze_started'},
        'SESSION_REFERENCES_INVALID')
    audit = _path(Path(references['audit_start']['path']).parent, root / 'reports')
    _require(not (audit / 'ATTEMPT_END.json').exists(), 'SESSION_TERMINAL')
    rows = []; refs = []; head = None
    for path in sorted((audit / 'events').glob('*.json')):
        reference = _reference(path, root); row = _read(reference, root)
        _require(path.stem == str(len(rows) + 1).zfill(6) and row.get('previous') == head
            and _identity(row) and row.get('action') != 'ERROR', 'SESSION_EVENT_CONFLICT')
        head = row['identity']; rows.append(row); refs.append(reference)
    pending = next((row['action'] for row in reversed(rows) if row['action'] in (
        'ADVANCE_STARTED', 'ADVANCE_RETURNED', 'RECONCILE_RETURNED', 'PUBLIC_RECONCILED', 'ERROR')), None)
    _require(pending != 'ADVANCE_STARTED', 'SESSION_RECONCILIATION_REQUIRED')
    return {'session_refs': deepcopy(references), 'event_refs': refs, 'event_head': head}


class AccountPurposeTimeExtensionV1:
    """一个任务只可有一份合并证明；原费用和经济身份始终作为不可变前驱。"""

    def __init__(self, root, manifest_ref):
        self.root = checked_directory_path(root, error_code='ACCOUNT_PURPOSE_TIME_EXTENSION_ROOT_INVALID')
        self.scope_reference = deepcopy(manifest_ref)
        self.recovery = None

    @staticmethod
    def extension_identity(authorization_id, task_id):
        return stable_hash({'schema_version': VERSION, 'authorization_id': authorization_id, 'task_id': task_id})

    @classmethod
    def summary(cls, root, *, authorization_id, task_ref, predecessor_manifest_ref, source_replacements,
                runtime_source_refs, economic_source_refs, proposal_ref, ci_ref):
        root = checked_directory_path(root, error_code='ACCOUNT_PURPOSE_TIME_EXTENSION_ROOT_INVALID')
        directory, auth_ref, base, approvals = _authorization(root, authorization_id)
        previous = _predecessor(root, predecessor_manifest_ref, base, approvals)
        task = _read(task_ref, root); original = previous['original']; job = _read(original['job'], root)
        _require(task_ref == original['task'] and task.get('task_id') == previous['task_id']
            and original['job']['path'] == str(Path(task_ref['path']).parent / 'account' / 'JOB.json')
            and job.get('objective_id') == base['objective_id'] and job.get('root') == str(Path(original['job']['path']).parent),
            'TASK_OR_JOB_CONFLICT')
        _require(len(job.get('plans', {})) == 2 and set(job['plans']) == set(previous['accounts'])
            == set(previous['active_plans']) and {name.rsplit('_', 1)[-1] for name in job['plans']} == {'BASE', 'STRESS'},
            'TWO_ORIGINAL_ACCOUNTS_REQUIRED')
        extension_id = cls.extension_identity(authorization_id, task['task_id'])
        profile = validate_execution_profile(job['resources'])
        _require(profile['profile_id'] == SEGMENTED_PROFILE and profile['purpose'] == 'RESEARCH_ACCOUNT'
            and profile['total_seconds'] == OLD_TOTAL_SECONDS and profile['worker_seconds'] == 900
            and profile['memory_mib'] == 2048 and profile['account_sessions'] == 504
            and profile in base['execution_profiles'], 'ORIGINAL_PROFILE_CONFLICT')
        replacements, runtime, economic = _sources(root, previous, job, source_replacements,
            runtime_source_refs, economic_source_refs)
        session_boundary = _session_boundary(root, previous)
        ledger, operations, effective = _ledger_boundary(root, directory, base)
        from .account_version_continuation_v1 import _control
        control = _control(job); _require(control['paused'], 'PAUSED_BOUNDARY_REQUIRED')
        receipt = _read(original['receipt'], root); mapping = receipt.get('source', {}).get('operation_ids')
        _require(isinstance(mapping, dict) and set(mapping) == set(job['plans']) and len(set(mapping.values())) == 2,
            'OPERATION_MAPPING_CONFLICT')
        accounts = {}; successor = {}; running = 0
        for name, plan in job['plans'].items():
            _require(_identity(plan, 'plan_id') and plan['runtime'] == job['items'][name]
                and plan['runtime']['execution_profile'] == profile and plan['backend']['execution_profile'] == profile,
                'ORIGINAL_PLAN_CONFLICT')
            operation = operations.get(mapping[name], {}); prior = previous['accounts'][name]
            _require(operation.get('operation_id') == prior['operation_id'] and operation.get('kind') == 'ACCOUNT'
                and operation.get('stage') == 'EXPLORATION' and operation.get('subject_identity') == plan['plan_id']
                and operation.get('execution_profile') == profile and operation.get('status') in ('RUNNING', 'RESERVED')
                and operation.get('active_segment') is None and not operation.get('resource_overrun')
                and operation.get('upper_bounds', {}).get('wall_seconds') == OLD_TOTAL_SECONDS
                and operation['upper_bounds'].get('account_jobs') == 1, 'ORIGINAL_OPERATION_CONFLICT')
            successor[name] = project_governance_plan(previous['active_plans'][name], replacements)
            row = {'operation_id': mapping[name], 'original_plan_id': plan['plan_id'],
                'predecessor_plan_id': previous['active_plans'][name]['plan_id'],
                'successor_plan_id': successor[name]['plan_id'], 'status_at_install': operation['status'],
                'original_profile': deepcopy(profile), 'original_upper_bounds': deepcopy(operation['upper_bounds']),
                'old_total_seconds': OLD_TOTAL_SECONDS, 'effective_total_seconds': TOTAL_SECONDS,
                'additional_reserved_seconds': ADDITIONAL_SECONDS, 'charged_seconds': 0., 'segment_count': 0,
                'charge_head': None, 'next_segment_number': 1, 'start_ref': None, 'segment_refs': [], 'checkpoint': None,
                'parent_segment_prefix_hash': stable_hash(operation.get('segments', []))}
            account_root = Path(job['root'])
            _require(not (account_root / (name + '_RESULT.json')).exists()
                and not (account_root / (name + '_SETTLEMENT.json')).exists()
                and not (account_root / (name + '_FAILURE.json')).exists(), 'ACCOUNT_TERMINAL')
            if operation['status'] == 'RUNNING':
                running += 1; state = _local_segment_boundary(root, job, name, receipt, operation)
                _require(state['pending'] is None and state['segments']
                    and state['segments'][-1]['charge']['outcome'] in ('CONTINUE', 'PAUSED'),
                    'PAUSED_SEGMENT_REQUIRED')
                row.update(charged_seconds=state['charged_seconds'], segment_count=len(state['segments']),
                    charge_head=state['head'], next_segment_number=state['next_segment_number'],
                    start_ref=_reference(account_root / (name + '_START.json'), root),
                    checkpoint=_checkpoint(root, job, name))
                _require(row['checkpoint'] is not None, 'COMMITTED_BOUNDARY_REQUIRED')
                _require(row['checkpoint']['processed_days'] < profile['account_sessions'], 'ACCOUNT_ALREADY_COMPLETE')
                row['checkpoint']['archive_ref'] = {'path': str(Path(task_ref['path']).parent /
                    'account-purpose-time-extensions' / extension_id / 'BOUNDARY_CHECKPOINTS' / (name + '.json')),
                    'sha256': row['checkpoint']['reference']['sha256']}
                for number in range(1, len(state['segments']) + 1):
                    prefix = name + '_SEGMENT_' + str(number).zfill(6)
                    row['segment_refs'].extend(_reference(account_root / (prefix + suffix), root)
                        for suffix in ('_DISPATCH.json', '_CHARGE.json'))
                    for suffix in ('_RESOURCE.json', '_EXECUTION_VERSION.json', '_RESUME.json'):
                        path = account_root / (prefix + suffix)
                        if path.exists():
                            row['segment_refs'].append(_reference(path, root))
            else:
                _require(not (account_root / (name + '_START.json')).exists() and not operation.get('segments')
                    and not operation.get('active_wall_seconds', 0) and _checkpoint(root, job, name) is None,
                    'RESERVED_ACCOUNT_ALREADY_STARTED')
            accounts[name] = row
        _require(running == 1, 'ONE_RUNNING_ACCOUNT_REQUIRED')
        _require(proposal_ref == _reference(proposal_ref['path'], root / 'reports'), 'PROPOSAL_CHANGED')
        ci = _read(ci_ref, root)
        _require(ci.get('status') == 'PASSED' and isinstance(ci.get('head'), str)
            and re.fullmatch(r'[a-f0-9]{40}', ci['head']) and ci.get('runtime_sources') == runtime
            and ci.get('source_replacements') == replacements and ci.get('economic_sources') == economic,
            'EXACT_CI_REQUIRED')
        expires = min(base['expires_at'], effective['expires_at'], previous['expires_at'], receipt['expires_at'],
            key=lambda value: datetime.fromisoformat(value))
        _require(datetime.now(timezone.utc) < datetime.fromisoformat(expires), 'EXPIRED')
        core = {'schema_version': SUMMARY_VERSION, 'root': str(root), 'authorization_id': authorization_id,
            'objective_id': base['objective_id'], 'task_id': task['task_id'], 'extension_id': extension_id,
            'approval_store': str(approvals.directory), 'base_authorization_ref': auth_ref,
            'base_authorization_identity': stable_hash(base), 'scope_hash': base['scope_policy']['scope_hash'],
            'expires_at': expires, 'predecessor_manifest_ref': deepcopy(predecessor_manifest_ref),
            'predecessor_bridge_ref': deepcopy(previous['capability_bridge_ref']), 'predecessor_version_id': previous['version_id'],
            'original': {**deepcopy(original), 'control': [_reference(Path(job['root']) /
                ('CONTROL_' + str(row['sequence']).zfill(6) + '.json'), root) for row in control['events']],
                'control_head': control['head']}, 'accounts': accounts, 'successor_plans': successor,
            'budget_boundary': ledger, 'source_replacements': replacements, 'runtime_sources': runtime,
            'session_boundary': session_boundary,
            'economic_sources': economic, 'proposal_ref': deepcopy(proposal_ref), 'ci_ref': deepcopy(ci_ref),
            'aggregate_reserved_delta': ADDITIONAL_SECONDS * 2, 'maximum_extensions': 1,
            'grant_delta': 0, 'new_account_jobs': 0, 'new_candidate_attempts': 0, 'new_preparation_jobs': 0}
        return {**core, 'summary_identity': stable_hash(core)}

    @classmethod
    def freeze(cls, root, summary, *, approval_ref):
        root = checked_directory_path(root, error_code='ACCOUNT_PURPOSE_TIME_EXTENSION_ROOT_INVALID')
        _require(isinstance(summary, dict) and summary.get('schema_version') == SUMMARY_VERSION
            and _identity(summary, 'summary_identity'), 'SUMMARY_INVALID')
        directory, _, base, approvals = _authorization(root, summary['authorization_id'])
        task_root = Path(summary['original']['task']['path']).parent
        slot = directory / 'account-purpose-time-extension-slots' / (summary['extension_id'] + '.json')
        with ObjectiveMutationLock.for_resource(task_root / 'account' / 'LONG_EXECUTION.lock'):
            with ObjectiveMutationLock.for_resource(task_root / 'account' / 'CONTROL.lock'):
                _path(slot, root)
                if slot.exists():
                    value = _read(_reference(slot, root), root)
                    context = cls.read(root, value['manifest_ref'], base_authorization=base)
                    _require(context.summary_value == summary and context.approval_ref == approval_ref, 'SLOT_CONFLICT')
                    return deepcopy(context.scope_reference)
                current = cls.summary(root, authorization_id=summary['authorization_id'], task_ref=summary['original']['task'],
                    predecessor_manifest_ref=summary['predecessor_manifest_ref'], source_replacements=summary['source_replacements'],
                    runtime_source_refs=summary['runtime_sources'], economic_source_refs=summary['economic_sources'],
                    proposal_ref=summary['proposal_ref'], ci_ref=summary['ci_ref'])
                _require(current == summary, 'SUMMARY_CHANGED')
                approvals.require(approval_ref, summary)
                for row in summary['accounts'].values():
                    if row['checkpoint'] is None:
                        continue
                    checkpoint = row['checkpoint']; archive = _path(checkpoint['archive_ref']['path'], root)
                    raw = read_file_bytes(checkpoint['reference']['path'], root=root,
                        error_code='ACCOUNT_PURPOSE_TIME_EXTENSION_CHECKPOINT_CHANGED')
                    _require(hashlib.sha256(raw).hexdigest() == checkpoint['archive_ref']['sha256'], 'CHECKPOINT_CHANGED')
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with archive.open('xb') as output:
                            output.write(raw)
                    except FileExistsError:
                        _require(_reference(archive, root) == checkpoint['archive_ref'], 'CHECKPOINT_ARCHIVE_CHANGED')
                manifest = {'schema_version': VERSION, 'summary': deepcopy(summary), 'approval_ref': deepcopy(approval_ref)}
                manifest['identity'] = stable_hash(manifest)
                path = task_root / 'account-purpose-time-extensions' / summary['extension_id'] / 'MANIFEST.json'
                _path(path, root); _write_once(path, manifest)
                reference = _reference(path, root)
                _write_once(slot, {'extension_id': summary['extension_id'], 'manifest_ref': reference})
                return reference

    @classmethod
    def read(cls, root, reference, *, base_authorization=None, for_install=False, for_dispatch=False, recovery=None):
        _require(type(for_install) is bool and type(for_dispatch) is bool, 'MODE_INVALID')
        context = cls(root, reference)
        manifest = _read(reference, context.root); summary = manifest.get('summary', {})
        _require(set(manifest) == {'schema_version', 'summary', 'approval_ref', 'identity'}
            and manifest['schema_version'] == VERSION and _identity(manifest)
            and summary.get('schema_version') == SUMMARY_VERSION and _identity(summary, 'summary_identity')
            and summary.get('maximum_extensions') == 1 and summary.get('aggregate_reserved_delta') == ADDITIONAL_SECONDS * 2
            and summary.get('grant_delta') == summary.get('new_account_jobs') == summary.get('new_candidate_attempts')
                == summary.get('new_preparation_jobs') == 0, 'MANIFEST_INVALID')
        # Windows授权根为normcase身份；先在调用者受限根读取/核摘要，再保留批准原件的路径拼写。
        # 不改写摘要或引用，否则同一目录的大写/小写入口会改变已冻结的字节身份。
        _require(isinstance(summary.get('root'), str)
            and os.path.normcase(str(Path(summary['root']))) == os.path.normcase(str(context.root)),
            'AUTHORIZATION_CONFLICT')
        context.root = checked_directory_path(summary['root'], root=context.root,
            error_code='ACCOUNT_PURPOSE_TIME_EXTENSION_ROOT_INVALID')
        directory, auth_ref, base, approvals = _authorization(context.root, summary['authorization_id'])
        _require(base_authorization is None or base == base_authorization, 'AUTHORIZATION_CONFLICT')
        _require(summary.get('root') == str(context.root) and summary.get('base_authorization_identity') == stable_hash(base)
            and summary.get('scope_hash') == base['scope_policy']['scope_hash']
            and summary.get('approval_store') == str(approvals.directory)
            and summary.get('base_authorization_ref') == auth_ref, 'AUTHORIZATION_CONFLICT')
        approvals.require(manifest['approval_ref'], summary)
        previous = _predecessor(context.root, summary['predecessor_manifest_ref'], base, approvals)
        original = summary['original']; task_root = Path(original['task']['path']).parent
        _require(summary['extension_id'] == cls.extension_identity(summary['authorization_id'], summary['task_id'])
            and Path(reference['path']) == task_root / 'account-purpose-time-extensions' / summary['extension_id'] / 'MANIFEST.json'
            and summary['predecessor_bridge_ref'] == previous['capability_bridge_ref']
            and summary['predecessor_version_id'] == previous['version_id'], 'MANIFEST_PATH_OR_PREDECESSOR_CONFLICT')
        _require(all(original[key] == previous['original'][key] for key in ('task', 'job', 'preview', 'receipt')),
            'ORIGINAL_REFERENCE_CONFLICT')
        slot = _read(_reference(directory / 'account-purpose-time-extension-slots' / (summary['extension_id'] + '.json'), context.root), context.root)
        _require(slot == {'extension_id': summary['extension_id'], 'manifest_ref': reference}, 'SLOT_CONFLICT')
        job = _read(original['job'], context.root)
        from .failed_account_recovery_v1 import (
            FailedAccountRecoveryV1, _job_manifest_reference, _owner_locator, _require as _recovery_require,
        )
        recovery_reference = None
        if recovery is None:
            located = _job_manifest_reference(job)
            if located is not None:
                recovery_root, recovery_reference, _ = located
                _require(os.path.normcase(recovery_root) == os.path.normcase(str(context.root)), 'RECOVERY_ROOT_CONFLICT')
        else:
            _require(type(recovery) is FailedAccountRecoveryV1, 'SIGNED_RECOVERY_CONTEXT_REQUIRED')
            _require(os.path.normcase(str(recovery.root)) == os.path.normcase(str(context.root)), 'RECOVERY_ROOT_CONFLICT')
            recovery_reference = deepcopy(recovery.scope_reference)
        if recovery_reference is not None:
            # 发现和外部对象只作 locator；原A认证Owner/slot，原C才交付完整 fresh 结果。
            _owner_locator(context.root, recovery_reference, base_authorization=base, job=job,
                predecessor_reference=reference, original=original)
        _refs([original[key] for key in ('task', 'job', 'preview', 'receipt')] + original['control']
            + [summary['proposal_ref'], summary['ci_ref']], context.root)
        if recovery_reference is None:
            _sources(context.root, previous, job, summary['source_replacements'], summary['runtime_sources'], summary['economic_sources'])
        else:
            # 原最后完整读取位置不后移；只按本次 fresh 对象投影前驱与来源。
            context.recovery = FailedAccountRecoveryV1.read(context.root, recovery_reference, base_authorization=base)
            _recovery_require(reference == context.recovery.summary_value['predecessor_manifest_ref'],
                'PREDECESSOR_REFERENCE_CONFLICT')
            _recovery_require(context.recovery.predecessor_summary == summary, 'PREDECESSOR_SUMMARY_CONFLICT')
            _require(context.recovery.job == job and context.recovery.job_path == Path(original['job']['path']), 'JOB_CHANGED')
        frozen_session = summary['session_boundary']
        _require(frozen_session['session_refs'] == previous.get('session_refs', {}), 'SESSION_REFERENCES_INVALID')
        _refs(frozen_session['event_refs'], context.root)
        _, operations, effective = _ledger_boundary(context.root, directory, base, summary['budget_boundary'])
        receipt = _read(original['receipt'], context.root)
        _require(summary['expires_at'] == min(base['expires_at'], effective['expires_at'], previous['expires_at'],
            receipt['expires_at'], key=lambda value: datetime.fromisoformat(value)), 'EXPIRY_CONFLICT')
        _require(set(summary['accounts']) == set(job['plans']) == set(summary['successor_plans']), 'ACCOUNTS_CONFLICT')
        for name, row in summary['accounts'].items():
            op = operations.get(row['operation_id'], {})
            _require(row['old_total_seconds'] == OLD_TOTAL_SECONDS and row['effective_total_seconds'] == TOTAL_SECONDS
                and row['additional_reserved_seconds'] == ADDITIONAL_SECONDS
                and row['original_profile'] == job['resources'] == op.get('execution_profile')
                and row['original_upper_bounds'] == op.get('upper_bounds')
                and row['original_plan_id'] == job['plans'][name]['plan_id']
                and row['predecessor_plan_id'] == previous['active_plans'][name]['plan_id']
                and row['next_segment_number'] == row['segment_count'] + 1
                and row['charged_seconds'] == op.get('active_wall_seconds', 0)
                and row['segment_count'] == len(op.get('segments', []))
                and row['parent_segment_prefix_hash'] == stable_hash(op.get('segments', [])), 'ACCOUNT_BOUNDARY_CONFLICT')
            projected = project_governance_plan(previous['active_plans'][name], summary['source_replacements'])
            _require(summary['successor_plans'][name] == projected and row['successor_plan_id'] == projected['plan_id'],
                'SUCCESSOR_PLAN_CONFLICT')
            _refs(row['segment_refs'] + ([row['start_ref']] if row['start_ref'] else []), context.root)
            if row['checkpoint'] is not None:
                checkpoint = row['checkpoint']
                _require(Path(checkpoint['archive_ref']['path']) == task_root / 'account-purpose-time-extensions'
                    / summary['extension_id'] / 'BOUNDARY_CHECKPOINTS' / (name + '.json')
                    and _reference(checkpoint['archive_ref']['path'], context.root) == checkpoint['archive_ref']
                    and checkpoint['archive_ref']['sha256'] == checkpoint['reference']['sha256'], 'CHECKPOINT_ARCHIVE_CHANGED')
                _checkpoint(context.root, job, name, frozen=row['checkpoint'], exact=for_install)
        if for_install:
            _require(_ledger_boundary(context.root, directory, base)[0] == summary['budget_boundary'], 'INSTALL_BOUNDARY_CHANGED')
            _require(_session_boundary(context.root, previous) == frozen_session, 'SESSION_INSTALL_BOUNDARY_CHANGED')
            from .account_version_continuation_v1 import _control
            _require(_control(job)['paused'] and _control(job)['head'] == original['control_head'], 'PAUSED_BOUNDARY_REQUIRED')
        if for_install or for_dispatch:
            _require(datetime.now(timezone.utc) < datetime.fromisoformat(summary['expires_at']), 'EXPIRED')
        context.summary_value = deepcopy(summary); context.summary = deepcopy(summary)
        context.approval_ref = deepcopy(manifest['approval_ref']); context.extension_id = summary['extension_id']
        context.job = job; context.job_path = Path(original['job']['path']); context.task_root = task_root
        return context

    @classmethod
    def from_job(cls, job, *, for_dispatch=False, recovery=None):
        if not isinstance(job, dict) or not isinstance(job.get('root'), str):
            return None
        job_root = _path(job['root'], None); folder = job_root.parent / 'account-purpose-time-extensions'
        if not folder.exists():
            return None
        _path(folder, job_root.parent); paths = list(folder.glob('*/MANIFEST.json'))
        _require(len(paths) == 1, 'MANIFEST_DISCOVERY_CONFLICT')
        manifest = _read(_reference(paths[0], job_root.parent), job_root.parent)
        root = manifest.get('summary', {}).get('root'); _require(isinstance(root, str), 'MANIFEST_INVALID')
        context = cls.read(root, _reference(paths[0], Path(root)), for_dispatch=for_dispatch, recovery=recovery)
        _require(context.job == job and context.job_path == job_root / 'JOB.json', 'JOB_CHANGED')
        return context

    def proof(self, name=None):
        _require(name is None or name in self.summary_value['accounts'], 'PURPOSE_CONFLICT')
        return {'manifest_ref': deepcopy(self.scope_reference), 'extension_id': self.extension_id,
            **({'purpose': name} if name else {})}

    def effective_total_seconds(self, name):
        _require(name in self.summary_value['accounts'], 'PURPOSE_CONFLICT')
        return self.summary_value['accounts'][name]['effective_total_seconds']

    def successor_plan(self, name):
        _require(name in self.summary_value['successor_plans'], 'PURPOSE_CONFLICT')
        return deepcopy(self.summary_value['successor_plans'][name])


def resolve_time_extension_manifest(manifest_ref, *, root, authorization_id, objective_id, for_dispatch=False, for_install=False):
    """预算重放调用的纯证明读取，不访问 campaign/账户入口，不取得 Owner 签发能力。"""
    context = AccountPurposeTimeExtensionV1.read(root, manifest_ref, for_dispatch=for_dispatch, for_install=for_install)
    summary = context.summary_value
    _require(summary['authorization_id'] == authorization_id and summary['objective_id'] == objective_id,
        'AUTHORIZATION_OR_OBJECTIVE_CONFLICT')
    accounts = deepcopy(summary['accounts'])
    return {'extension_id': context.extension_id, 'manifest_ref': deepcopy(manifest_ref), 'proof': context.proof(),
        'authorization_id': authorization_id, 'objective_id': objective_id,
        'base_authorization_identity': summary['base_authorization_identity'],
        'expires_at': summary['expires_at'], 'aggregate_reserved_delta': summary['aggregate_reserved_delta'],
        'summary_value': deepcopy(summary), 'source_replacements': deepcopy(summary['source_replacements']),
        'runtime_sources': deepcopy(summary['runtime_sources']), 'economic_sources': deepcopy(summary['economic_sources']),
        'successor_plans': deepcopy(summary['successor_plans']), 'active_plans': deepcopy(summary['successor_plans']),
        'operation_ids': {name: row['operation_id'] for name, row in accounts.items()}, 'accounts': accounts,
        'limits': {row['operation_id']: {'old_total_seconds': row['old_total_seconds'],
            'effective_total_seconds': row['effective_total_seconds'],
            'additional_reserved_seconds': row['additional_reserved_seconds'],
            'profile_hash': row['original_profile']['profile_hash']} for row in accounts.values()},
        'operation_boundaries': {row['operation_id']: {'segment_count': row['segment_count'],
            'charged_seconds': row['charged_seconds'], 'charge_head': row['charge_head'],
            'parent_segment_prefix_hash': row['parent_segment_prefix_hash'],
            'next_segment_number': row['next_segment_number']} for row in accounts.values()}}
