"""Owner批准的原失败账户一次性接续；旧失败、费用及Trial始终保留。"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re

from chanlun_trader.account_startup_boundary_v1 import startup_checkpoint
from . import account_purpose_time_extension_v1 as extension
from . import account_version_capability_bridge_v1 as bridges
from .campaign_scope_v1 import _write_once, replay_grant, scope_summary
from .common import stable_hash
from .mutation_boundary import ObjectiveMutationLock
from .secure_file_reference_v1 import (
    checked_directory_path, validated_reference_path,
)
from .account_purpose_time_extension_v1 import file_sha256, read_file_bytes, read_pinned_json
from .universe_execution_profile_v1 import SEGMENTED_PROFILE, segment_charge, validate_execution_profile


VERSION = 'FAILED_ACCOUNT_RECOVERY_MANIFEST_V1'
SUMMARY_VERSION = 'OWNER_FAILED_ACCOUNT_RECOVERY_SUMMARY_V1'
TOTAL_SECONDS = 72000
FAILED_SEGMENT_COUNT = 50
COMMITTED_DAYS = 277
CANONICAL_SECONDS = 42339.896402100036
PARENT_USED_SECONDS = 42340
RESTORED_RESERVED_SECONDS = 29660
GOVERNANCE_PATHS = extension.GOVERNANCE_PATHS | frozenset({
    'src/chanlun_trader/research_factory/failed_account_recovery_v1.py',
    'src/chanlun_trader/research_factory/failed_account_calendar_scope_v1.py',
    'src/chanlun_trader/research_factory/failed_account_recovery_evidence_v1.py',
    'src/chanlun_trader/account_startup_boundary_v1.py',
    'scripts/setup_failed_account_recovery_v1.py',
})
REQUIRED_RUNTIME_PATHS = frozenset({
    'src/chanlun_trader/research_factory/failed_account_recovery_v1.py',
    'src/chanlun_trader/research_factory/failed_account_calendar_scope_v1.py',
    'src/chanlun_trader/research_factory/failed_account_recovery_evidence_v1.py',
    'src/chanlun_trader/account_startup_boundary_v1.py',
    'scripts/setup_failed_account_recovery_v1.py',
})


def _require(condition, reason):
    if not condition:
        raise PermissionError('FAILED_ACCOUNT_RECOVERY_' + reason)


def _path(value, root):
    return validated_reference_path(value, root=root, error_code='FAILED_ACCOUNT_RECOVERY_PATH_INVALID')


def _reference(path, root):
    try:
        digest = file_sha256(path, root=root, error_code='FAILED_ACCOUNT_RECOVERY_REFERENCE_CHANGED')
    except ValueError:
        _path(path, root)
        raise
    return {'path': str(Path(path)), 'sha256': digest}


def _read(reference, root):
    startup_checkpoint('FAILED_ACCOUNT_RECOVERY_REFERENCE')
    return read_pinned_json(reference, root=root, error_code='FAILED_ACCOUNT_RECOVERY_REFERENCE_CHANGED')


def _validate_calendar_deadline_extension(root, value, *, authorization_id, objective_id,
                                         previous_expires_at, global_wall_seconds):
    """只认证本次Owner摘要绑定的人类原件；旧授权和费用前缀不变。"""
    _require(isinstance(value, dict) and set(value) == {
        'previous_expires_at', 'expires_at', 'human_authorization_ref'}, 'CALENDAR_DEADLINE_FIELDS_INVALID')
    _require(isinstance(value['previous_expires_at'], str) and isinstance(value['expires_at'], str)
        and value['previous_expires_at'] == previous_expires_at, 'CALENDAR_PREVIOUS_EXPIRY_CONFLICT')
    try:
        previous = datetime.fromisoformat(value['previous_expires_at'])
        expires = datetime.fromisoformat(value['expires_at'])
        valid = (previous.utcoffset() is not None and expires.utcoffset() is not None
            and expires > previous and math.isfinite((expires - previous).total_seconds()))
    except (ValueError, TypeError, OverflowError):
        valid = False
    _require(valid, 'CALENDAR_DEADLINE_INVALID')
    human = _read(value['human_authorization_ref'], Path(root) / 'reports')
    _require(isinstance(human, dict)
        and human.get('schema_version') == 'HUMAN_REAL_ACCOUNT_CALENDAR_DEADLINE_EXTENSION_AUTHORIZATION_V1'
        and human.get('campaign') == authorization_id and human.get('objective') == objective_id
        and human.get('original_expires_at') == previous_expires_at
        and human.get('authorized_new_expires_at') == value['expires_at'], 'CALENDAR_HUMAN_AUTHORIZATION_CONFLICT')
    fixed = {'each_account_seconds': TOTAL_SECONDS, 'global_seconds': 288002,
        'original_base_days': COMMITTED_DAYS, 'original_stress_days': 0,
        'parent_original_used_integer_seconds': PARENT_USED_SECONDS,
        'base_remaining_integer_seconds': RESTORED_RESERVED_SECONDS, 'stress_remaining_seconds': TOTAL_SECONDS}
    _require(type(global_wall_seconds) is int and global_wall_seconds == fixed['global_seconds']
        and all(type(human.get(key)) is int and human[key] == amount for key, amount in fixed.items())
        and type(human.get('canonical_original_charged_seconds')) in (int, float)
        and human['canonical_original_charged_seconds'] == CANONICAL_SECONDS
        and human.get('preserve_original_FAILED_and_all_fees') is True
        and human.get('new_task_candidate_purpose_grant_resource_or_owner_root_allowed') is False
        and human.get('rerun_committed_days_allowed') is False
        and human.get('all_original_production_gates_required') is True, 'CALENDAR_ORIGINAL_LIMITS_OR_FEES_CONFLICT')
    return deepcopy(value)


def _refs(references, root):
    _require(isinstance(references, list), 'REFERENCES_REQUIRED')
    result = []
    for ref in references:
        startup_checkpoint('FAILED_ACCOUNT_RECOVERY_REFERENCES')
        _require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'}
            and extension._hash(ref['sha256']) and _reference(ref['path'], root) == ref, 'REFERENCE_CHANGED')
        result.append(deepcopy(ref))
    _require(len({ref['path'] for ref in result}) == len(result), 'REFERENCES_DUPLICATED')
    return sorted(result, key=lambda ref: ref['path'])


def _owner_binding(context, reference, *, base_authorization=None):
    """只读认证恢复 Owner 原件；不生成完整恢复能力或跨调用凭证。"""
    manifest = _read(reference, context.root); value = manifest.get('summary', {})
    _require(set(manifest) == {'schema_version', 'summary', 'approval_ref', 'identity'}
        and manifest.get('schema_version') == VERSION and extension._identity(manifest)
        and value.get('schema_version') == SUMMARY_VERSION and extension._identity(value, 'summary_identity')
        and value.get('maximum_recoveries') == 1
        and value.get('allowed_action') == 'ONE_FAILED_ACCOUNT_PRODUCTION_CONTINUATION'
        and all(value.get(key) == 0 for key in (
            'grant_delta', 'new_account_jobs', 'new_candidate_attempts', 'new_preparation_jobs')), 'MANIFEST_INVALID')
    _require(isinstance(value.get('root'), str)
        and os.path.normcase(value['root']) == os.path.normcase(str(context.root)), 'ROOT_CONFLICT')
    context.root = checked_directory_path(value['root'], root=context.root, error_code='FAILED_ACCOUNT_RECOVERY_ROOT_INVALID')
    directory, auth_ref, base, approvals = extension._authorization(context.root, value['authorization_id'])
    _require((base_authorization is None or base == base_authorization)
        and value['base_authorization_ref'] == auth_ref and value['base_authorization_identity'] == stable_hash(base)
        and value['scope_hash'] == base['scope_policy']['scope_hash']
        and value['approval_store'] == str(approvals.directory), 'AUTHORIZATION_CONFLICT')
    approvals.require(manifest['approval_ref'], value)
    return manifest, value, directory, base, approvals


def _owner_locator(root, reference, *, base_authorization, job, predecessor_reference, original):
    """只在当前调用认证受限 locator；来源、费用及日链仍由后续完整 read 核验。"""
    context = FailedAccountRecoveryV1(root, reference)
    _, value, directory, _, _ = _owner_binding(context, reference, base_authorization=base_authorization)
    _require(value['predecessor_manifest_ref'] == predecessor_reference, 'PREDECESSOR_REFERENCE_CONFLICT')
    task_root = Path(original['task']['path']).parent
    _require(value['recovery_id'] == FailedAccountRecoveryV1.recovery_identity(
            value['authorization_id'], value['task_id'])
        and Path(reference['path']) == task_root / 'failed-account-recoveries' / value['recovery_id'] / 'MANIFEST.json'
        and all(value['original'][key] == original[key] for key in ('task', 'job', 'preview', 'receipt')),
        'MANIFEST_PATH_OR_PREDECESSOR_CONFLICT')
    slot = _read(_reference(directory / 'failed-account-recovery-slots' / (value['recovery_id'] + '.json'), context.root), context.root)
    _require(slot == {'recovery_id': value['recovery_id'], 'manifest_ref': reference}, 'SLOT_CONFLICT')
    _require(value['original']['job']['path'] == str(Path(job['root']) / 'JOB.json')
        and _read(value['original']['job'], context.root) == job, 'JOB_CHANGED')


def _job_manifest_reference(job):
    """仅定位规范 manifest；返回值不证明来源有效，调用者仍须认证 Owner 并完整 read。"""
    if not isinstance(job, dict) or not isinstance(job.get('root'), str): return None
    account_root = _path(job['root'], None); folder = account_root.parent / 'failed-account-recoveries'
    if not folder.exists(): return None
    _path(folder, account_root.parent); paths = list(folder.glob('*/MANIFEST.json'))
    _require(len(paths) == 1, 'MANIFEST_DISCOVERY_CONFLICT')
    manifest = _read(_reference(paths[0], account_root.parent), account_root.parent)
    root = manifest.get('summary', {}).get('root'); _require(isinstance(root, str), 'MANIFEST_INVALID')
    return root, _reference(paths[0], Path(root)), account_root


def _predecessor(root, reference, base, approvals):
    """独立核原扩时的签名、规范slot和冻结前缀；不读取当前执行治理。"""
    manifest = _read(reference, root); previous = manifest.get('summary', {})
    _require(set(manifest) == {'schema_version', 'summary', 'approval_ref', 'identity'}
        and manifest.get('schema_version') == extension.VERSION and extension._identity(manifest)
        and previous.get('schema_version') == extension.SUMMARY_VERSION
        and extension._identity(previous, 'summary_identity') and previous.get('maximum_extensions') == 1
        and previous.get('aggregate_reserved_delta') == 86400
        and all(previous.get(key) == 0 for key in ('grant_delta', 'new_account_jobs',
            'new_candidate_attempts', 'new_preparation_jobs')), 'PREDECESSOR_MANIFEST_INVALID')
    _require(previous.get('root') == str(root) and previous.get('base_authorization_identity') == stable_hash(base)
        and previous.get('scope_hash') == base['scope_policy']['scope_hash']
        and previous.get('approval_store') == str(approvals.directory)
        and previous.get('base_authorization_ref') == _reference(root / 'reports' / 'research_campaigns'
            / stable_hash(base['authorization_id']) / 'authorization.json', root), 'PREDECESSOR_SCOPE_CONFLICT')
    approvals.require(manifest['approval_ref'], previous)
    original = previous['original']; task_root = Path(original['task']['path']).parent
    _require(previous['extension_id'] == extension.AccountPurposeTimeExtensionV1.extension_identity(
        base['authorization_id'], previous['task_id']) and Path(reference['path']) == task_root
        / 'account-purpose-time-extensions' / previous['extension_id'] / 'MANIFEST.json',
        'PREDECESSOR_PATH_CONFLICT')
    directory = root / 'reports' / 'research_campaigns' / stable_hash(base['authorization_id'])
    slot = _read(_reference(directory / 'account-purpose-time-extension-slots'
        / (previous['extension_id'] + '.json'), root), root)
    _require(slot == {'extension_id': previous['extension_id'], 'manifest_ref': reference}, 'PREDECESSOR_SLOT_CONFLICT')
    continuation = extension._predecessor(root, previous['predecessor_manifest_ref'], base, approvals)
    _require(previous['predecessor_bridge_ref'] == continuation['capability_bridge_ref']
        and previous['predecessor_version_id'] == continuation['version_id']
        and all(original[key] == continuation['original'][key] for key in ('task', 'job', 'preview', 'receipt')),
        'PREDECESSOR_LINEAGE_CONFLICT')
    job = _read(original['job'], root)
    _refs([original[key] for key in ('task', 'job', 'preview', 'receipt')]
        + original['control'] + [previous['proposal_ref'], previous['ci_ref']], root)
    _, operations, effective = extension._ledger_boundary(root, directory, base, previous['budget_boundary'])
    for name, row in previous['accounts'].items():
        startup_checkpoint('FAILED_ACCOUNT_RECOVERY_PREDECESSOR')
        op = operations.get(row['operation_id'], {})
        _require(row.get('old_total_seconds') == 28800 and row.get('effective_total_seconds') == TOTAL_SECONDS
            and row.get('additional_reserved_seconds') == 43200
            and row.get('original_profile') == job['resources'] == op.get('execution_profile')
            and row.get('original_upper_bounds') == op.get('upper_bounds')
            and row.get('original_plan_id') == job['plans'][name]['plan_id']
            and row.get('predecessor_plan_id') == continuation['active_plans'][name]['plan_id']
            and row.get('segment_count') == len(op.get('segments', []))
            and row.get('charged_seconds') == op.get('active_wall_seconds', 0)
            and row.get('parent_segment_prefix_hash') == stable_hash(op.get('segments', []))
            and row.get('next_segment_number') == row['segment_count'] + 1, 'PREDECESSOR_ACCOUNT_CONFLICT')
        projected = extension.project_governance_plan(continuation['active_plans'][name], previous['source_replacements'])
        _require(previous['successor_plans'][name] == projected and row['successor_plan_id'] == projected['plan_id'],
            'PREDECESSOR_PLAN_CONFLICT')
        _refs(row['segment_refs'] + ([row['start_ref']] if row['start_ref'] else []), root)
        if row['checkpoint'] is not None:
            cp = row['checkpoint']
            _require(Path(cp['archive_ref']['path']) == task_root / 'account-purpose-time-extensions'
                / previous['extension_id'] / 'BOUNDARY_CHECKPOINTS' / (name + '.json')
                and cp['archive_ref']['sha256'] == cp['reference']['sha256']
                and _reference(cp['archive_ref']['path'], root) == cp['archive_ref'], 'PREDECESSOR_CHECKPOINT_CHANGED')
            extension._checkpoint(root, job, name, frozen=cp)
    receipt = _read(original['receipt'], root)
    _require(previous['expires_at'] == min(base['expires_at'], effective['expires_at'], continuation['expires_at'],
        receipt['expires_at'], key=datetime.fromisoformat), 'PREDECESSOR_EXPIRY_CONFLICT')
    _refs(previous['economic_sources'], root)
    for row in previous['source_replacements']:
        startup_checkpoint('FAILED_ACCOUNT_RECOVERY_PREDECESSOR_ARCHIVE')
        _require(row.get('archive_ref', {}).get('sha256') == row.get('old_sha256')
            and _reference(row['archive_ref']['path'], root / 'reports') == row['archive_ref'],
            'PREDECESSOR_ARCHIVE_CHANGED')
    return previous, continuation, job


def _inventory(root, previous, continuation, job):
    inventory = extension._predecessor_inventory(root, continuation, job)
    for row in previous['source_replacements']:
        _require(inventory.get(row['path']) == row['old_sha256'], 'PREDECESSOR_SOURCE_CONFLICT')
        inventory[row['path']] = row['new_sha256']
    for path, digest in extension._source_inventory(previous['successor_plans']).items():
        startup_checkpoint('FAILED_ACCOUNT_RECOVERY_SOURCE_INVENTORY')
        _require(path not in inventory or inventory[path] == digest, 'PREDECESSOR_SOURCE_CONFLICT')
        inventory[path] = digest
    for ref in previous['runtime_sources']:
        _require(ref['path'] not in inventory or inventory[ref['path']] == ref['sha256'], 'PREDECESSOR_SOURCE_CONFLICT')
        inventory[ref['path']] = ref['sha256']
    return inventory


def _sources(root, previous, continuation, job, replacements, runtime_refs, economic_refs):
    inventory = _inventory(root, previous, continuation, job)
    economic = _refs(economic_refs, root)
    _require(economic == sorted(previous['economic_sources'], key=lambda ref: ref['path']), 'ECONOMIC_INVENTORY_CHANGED')
    economic_paths = {ref['path'] for ref in economic}
    _require(isinstance(replacements, list) and replacements, 'GOVERNANCE_REPLACEMENTS_REQUIRED')
    mapping = {}
    for row in replacements:
        startup_checkpoint('FAILED_ACCOUNT_RECOVERY_SOURCE_REPLACEMENT')
        _require(isinstance(row, dict) and set(row) == {'path', 'old_sha256', 'new_sha256', 'archive_ref'},
            'SOURCE_REPLACEMENT_FIELDS_INVALID')
        path = _path(row['path'], root)
        _require(path.relative_to(root).as_posix() in GOVERNANCE_PATHS and row['path'] not in mapping
            and inventory.get(row['path']) == row['old_sha256'] and extension._hash(row['new_sha256'])
            and row['old_sha256'] != row['new_sha256'] and row['path'] not in economic_paths,
            'GOVERNANCE_SOURCE_ALLOWLIST_CONFLICT')
        _require(isinstance(row['archive_ref'], dict) and row['archive_ref'].get('sha256') == row['old_sha256']
            and _reference(row['archive_ref']['path'], root / 'reports') == row['archive_ref'], 'SOURCE_ARCHIVE_CHANGED')
        _require(_reference(path, root)['sha256'] == row['new_sha256'], 'GOVERNANCE_SOURCE_CHANGED')
        mapping[row['path']] = row
    runtime = _refs(runtime_refs, root); runtime_paths = {ref['path'] for ref in runtime}
    _require(runtime and all(Path(ref['path']).relative_to(root).as_posix() in GOVERNANCE_PATHS for ref in runtime)
        and set(mapping) <= runtime_paths and {ref['path'] for ref in previous['runtime_sources']} <= runtime_paths
        and {str(root / path) for path in REQUIRED_RUNTIME_PATHS} <= runtime_paths,
        'RUNTIME_CLOSURE_INVALID')
    for path, digest in inventory.items():
        startup_checkpoint('FAILED_ACCOUNT_RECOVERY_SOURCE_VERIFY')
        _require(_reference(path, root)['sha256'] == mapping.get(path, {}).get('new_sha256', digest),
            'UNAPPROVED_SOURCE_CHANGED')
    for ref in runtime:
        startup_checkpoint('FAILED_ACCOUNT_RECOVERY_RUNTIME_VERIFY')
        if ref['path'] in inventory:
            _require(ref['sha256'] == mapping.get(ref['path'], {}).get('new_sha256', inventory[ref['path']]),
                'RUNTIME_SOURCE_CONFLICT')
    for plan in previous['successor_plans'].values():
        for path, digest in extension._source_inventory([plan['backend'], plan['strategy']]).items():
            _require(path in economic_paths and {'path': path, 'sha256': digest} in economic,
                'ECONOMIC_DEPENDENCY_SOURCE_CHANGED')
    return sorted(deepcopy(replacements), key=lambda row: row['path']), runtime, economic


def source_inventory(root, *, authorization_id, task_ref, predecessor_manifest_ref):
    """部署端只读列出唯一旧治理基线；不签发或派发。"""
    root = checked_directory_path(root, error_code='FAILED_ACCOUNT_RECOVERY_ROOT_INVALID')
    _, _, base, approvals = extension._authorization(root, authorization_id)
    previous, continuation, job = _predecessor(root, predecessor_manifest_ref, base, approvals)
    _require(task_ref == previous['original']['task'], 'TASK_OR_JOB_CONFLICT')
    return {'source_hashes': _inventory(root, previous, continuation, job),
        'economic_sources': deepcopy(previous['economic_sources']),
        'predecessor_summary': deepcopy(previous), 'original_job': job}


def _capabilities(root, base, approvals, previous, continuation, replacements):
    current = bridges.capabilities()
    _require(extension._identity(current, 'fingerprint'), 'CURRENT_CAPABILITIES_INVALID')
    old_extension = bridges._project_snapshot(current, replacements)
    old_continuation = bridges._project_snapshot(old_extension, previous['source_replacements'])
    bridge_ref = continuation['capability_bridge_ref']
    bridge = _read({key: bridge_ref[key] for key in ('path', 'sha256')}, approvals.directory)
    bridge_summary = bridge['summary']
    _require(set(bridge) == {'schema_version', 'summary', 'approval_ref', 'identity'}
        and bridge.get('schema_version') == bridges.VERSION and extension._identity(bridge)
        and bridge.get('approval_ref') == bridge_ref['approval_ref']
        and Path(bridge_ref['path']) == approvals.directory / 'account_version_capability_bridges' / 'records'
            / (bridge['identity'] + '.json')
        and bridge_summary.get('allowed_action') == 'ONE_ACCOUNT_INITIALIZATION_VERSION_CONTINUATION'
        and bridge_summary.get('grant_delta') == 0
        and bridge_summary.get('live_capabilities_fingerprint') == old_continuation['fingerprint'],
        'PREDECESSOR_CAPABILITIES_CHANGED')
    binding_path = bridges.AccountVersionCapabilityBridgeV1._binding_path(approvals, base, old_continuation['fingerprint'])
    binding = _read(_reference(binding_path, approvals.directory), approvals.directory)
    _require(binding == {'schema_version': bridges.VERSION, 'base_authorization_identity': stable_hash(base),
        'live_capabilities_fingerprint': old_continuation['fingerprint'], 'reference': bridge_ref},
        'PREDECESSOR_CAPABILITY_BINDING_CHANGED')
    approvals.require(bridge['approval_ref'], bridge_summary)
    original = bridges._project_snapshot(old_continuation, continuation['source_replacements'])
    original = bridges.AccountVersionCapabilityBridgeV1._predecessor(root, base, approvals,
        bridge['summary'].get('predecessor_bridge_ref'), original)
    policy = base['scope_policy']
    _require(original['fingerprint'] == policy['summary']['contract']['capabilities_fingerprint']
        and scope_summary(root, base, policy['summary']['contract'], policy['summary']['stage_limits'],
            capabilities_snapshot=original) == policy['summary'], 'ORIGINAL_CAPABILITIES_CONFLICT')
    return original, current['fingerprint']


def _events(root, directory, base, frozen=None):
    path = directory / 'run_budget_events.jsonl'
    lines = read_file_bytes(path, root=root, error_code='FAILED_ACCOUNT_RECOVERY_LEDGER_CHANGED').splitlines(keepends=True)
    _require(lines and all(line.strip() for line in lines), 'LEDGER_INVALID')
    all_events = extension._verification_json_lines(lines); seen = set()
    for event in all_events:
        startup_checkpoint('FAILED_ACCOUNT_RECOVERY_LEDGER_VERIFY')
        _require(event.get('run_id') == base['authorization_id'] and event.get('event_id') == stable_hash({
            key: event[key] for key in ('run_id', 'event_type', 'payload')})
            and event['event_id'] not in seen, 'LEDGER_EVENT_INVALID')
        seen.add(event['event_id'])
    snapshot = _read(_reference(directory / 'run_budget.json', root), root)
    _require(snapshot.get('campaign_event_head') in seen, 'LEDGER_COMMITTED_HEAD_MISSING')
    if frozen is not None:
        _require(frozen.get('path') == str(path) and type(frozen.get('line_count')) is int
            and 0 < frozen['line_count'] <= len(lines), 'LEDGER_PREFIX_CHANGED')
        lines = lines[:frozen['line_count']]
    boundary = {'path': str(path), 'line_count': len(lines),
        'prefix_sha256': hashlib.sha256(b''.join(lines)).hexdigest(), 'event_head': all_events[len(lines) - 1]['event_id']}
    _require(frozen is None or boundary == frozen, 'LEDGER_PREFIX_CHANGED')
    return boundary, all_events[:len(lines)], all_events


def _ledger_boundary(root, directory, base, previous, predecessor_ref, frozen=None):
    """局部纯重放安装前事实；完整父预算另在自己的锁内独立复核。"""
    boundary, events, _ = _events(root, directory, base, frozen)
    operations = {}; effective = None; revoked = False; extension_seen = False; trials = {}
    expected_proof = {'manifest_ref': predecessor_ref, 'extension_id': previous['extension_id']}
    for event in events:
        startup_checkpoint('FAILED_ACCOUNT_RECOVERY_LEDGER_REPLAY')
        kind, item = event['event_type'], event['payload']
        if kind == 'CAMPAIGN_AUTHORIZED':
            _require(effective is None and item == base, 'AUTHORIZATION_EVENT_CONFLICT'); effective = extension._copy_json_tree(base)
        elif kind == 'CAMPAIGN_GRANT_ADDED':
            _require(effective is not None and not revoked, 'GRANT_EVENT_CONFLICT')
            effective = replay_grant(base, effective, item)
        elif kind == 'CAMPAIGN_REVOKED':
            revoked = True
        elif kind == 'CAMPAIGN_OPERATION_RESERVED':
            _require(item.get('operation_id') not in operations, 'OPERATION_DUPLICATED')
            operations[item['operation_id']] = {**extension._copy_json_tree(item), 'status': 'RESERVED'}
        elif kind == 'CAMPAIGN_ACCOUNT_PURPOSE_TIME_EXTENDED':
            _require(not extension_seen and item.get('proof') == expected_proof, 'TIME_EXTENSION_CONFLICT')
            for row in previous['accounts'].values():
                op = operations.get(row['operation_id'], {})
                _require(op.get('status') == row['status_at_install'] and op.get('execution_profile') == row['original_profile']
                    and len(op.get('segments', [])) == row['segment_count']
                    and stable_hash(op.get('segments', [])) == row['parent_segment_prefix_hash']
                    and op.get('active_wall_seconds', 0) == row['charged_seconds'], 'TIME_EXTENSION_BOUNDARY_CONFLICT')
                bounds = deepcopy(op['upper_bounds']); bounds['wall_seconds'] = TOTAL_SECONDS
                op.update(effective_upper_bounds=bounds, effective_total_seconds=TOTAL_SECONDS,
                    account_purpose_time_extension=deepcopy(expected_proof), time_extension_boundary=deepcopy(row))
            extension_seen = True
        elif kind in ('CAMPAIGN_OPERATION_STARTED', 'CAMPAIGN_OPERATION_UNKNOWN', 'CAMPAIGN_OPERATION_SETTLED',
                'CAMPAIGN_OPERATION_RESOURCE_OVERRUN_SETTLED', 'CAMPAIGN_MODEL_RESOURCE_OVERRUN_SETTLED'):
            op = operations.get(item.get('operation_id'))
            _require(op is not None and op['status'] not in ('COMPLETED', 'FAILED'), 'OPERATION_EVENT_CONFLICT')
            op.update(extension._copy_json_tree(item)); op['status'] = ('RUNNING' if kind == 'CAMPAIGN_OPERATION_STARTED'
                else 'UNKNOWN' if kind == 'CAMPAIGN_OPERATION_UNKNOWN' else item.get('outcome'))
        elif kind == 'CAMPAIGN_EXECUTION_SEGMENT_DISPATCHED':
            op = operations.get(item.get('operation_id')); upper = item.get('upper_bound_seconds')
            _require(op is not None and op['status'] == 'RUNNING' and op.get('active_segment') is None,
                'ACTIVE_SEGMENT_CONFLICT')
            profile = validate_execution_profile(op['execution_profile']); segments = op.setdefault('segments', [])
            _require(item.get('segment_number') == len(segments) + 1 and item.get('profile_hash') == profile['profile_hash']
                and type(upper) in (int, float) and math.isfinite(upper)
                and 0 < upper <= min(profile['worker_seconds'], op.get('effective_total_seconds', profile['total_seconds'])
                    - op.get('active_wall_seconds', 0))
                and item.get('time_extension_proof') == op.get('account_purpose_time_extension')
                and (not segments or segments[-1].get('outcome') not in ('COMPLETED', 'FAILED')), 'SEGMENT_EVENT_CONFLICT')
            active = extension._copy_json_tree(item); segments.append(active); op['active_segment'] = active
        elif kind == 'CAMPAIGN_EXECUTION_SEGMENT_CHARGED':
            op = operations.get(item.get('operation_id')); active = op.get('active_segment') if op else None
            _require(active is not None and op['status'] in ('RUNNING', 'UNKNOWN')
                and item.get('segment_number') == active['segment_number']
                and item.get('time_extension_proof') == active.get('time_extension_proof'), 'SEGMENT_CHARGE_CONFLICT')
            seconds, basis = segment_charge(item.get('measured_seconds'), active['upper_bound_seconds'],
                item.get('evidence_identity'), outcome=item.get('outcome'))
            _require(item.get('seconds') == seconds and item.get('basis') == basis, 'SEGMENT_CHARGE_CONFLICT')
            active.update(extension._copy_json_tree(item)); op['active_wall_seconds'] = op.get('active_wall_seconds', 0) + seconds
            op['active_segment'] = None
        elif kind in ('RUN_TRIAL_RESERVED', 'RUN_TRIAL_PERFORMANCE_ACCESSED', 'RUN_TRIAL_COMPLETED', 'RUN_TRIAL_RELEASED'):
            trial = trials.setdefault(item['trial_id'], {'identity': deepcopy(item), 'accessed': False, 'completed': False})
            _require(all(trial['identity'].get(key) == item.get(key) for key in (
                'trial_id', 'reservation_id', 'batch_id', 'candidate_id', 'candidate_hash', 'family_id')),
                'TRIAL_IDENTITY_CONFLICT')
            if kind == 'RUN_TRIAL_PERFORMANCE_ACCESSED': trial['accessed'] = True
            if kind == 'RUN_TRIAL_COMPLETED': trial['completed'] = True
        elif kind.startswith('CAMPAIGN_'):
            _require(kind in ('CAMPAIGN_PAUSED', 'CAMPAIGN_RESUMED', 'CAMPAIGN_STAGE_CHANGED'), 'RECOVERY_ALREADY_SEEN_OR_EVENT_INVALID')
    _require(effective is not None and extension_seen and not revoked, 'TIME_EXTENSION_REQUIRED_OR_REVOKED')
    _require(not any(op.get('status') == 'UNKNOWN' or op.get('active_segment') is not None
        for op in operations.values()), 'UNSETTLED_OPERATION')
    return boundary, operations, effective, trials


def _local_boundary(root, job, name, receipt, operation, previous, predecessor_ref):
    account_root = Path(job['root']); profile = validate_execution_profile(job['resources'])
    start_ref = _reference(account_root / (name + '_START.json'), root); start = _read(start_ref, root)
    _require(start.get('kind') == name and start.get('receipt_id') == receipt['receipt_id'], 'START_CONFLICT')
    expected_proof = {'manifest_ref': predecessor_ref, 'extension_id': previous['extension_id']}
    rows = []; refs = []; charged = 0.; head = None
    for number, parent in enumerate(operation.get('segments', []), 1):
        startup_checkpoint('FAILED_ACCOUNT_RECOVERY_SEGMENT_VERIFY')
        prefix = name + '_SEGMENT_' + str(number).zfill(6)
        dispatch_ref = _reference(account_root / (prefix + '_DISPATCH.json'), root)
        charge_ref = _reference(account_root / (prefix + '_CHARGE.json'), root)
        dispatch, charge = _read(dispatch_ref, root), _read(charge_ref, root)
        proof = expected_proof if number > previous['accounts'][name]['segment_count'] else None
        _require(extension._identity(dispatch, 'dispatch_id') and dispatch.get('schema_version') == 'strategy-account-segment-dispatch-v1'
            and dispatch.get('kind') == name and dispatch.get('receipt_id') == receipt['receipt_id']
            and dispatch.get('segment_number') == number and dispatch.get('previous_head') == head
            and dispatch.get('charged_before') == charged and dispatch.get('profile_hash') == profile['profile_hash']
            and dispatch.get('time_extension_proof') == proof, 'LOCAL_DISPATCH_CHAIN_CONFLICT')
        seconds, basis = segment_charge(charge.get('measured_seconds'), dispatch['upper_bound_seconds'],
            charge.get('evidence_identity'), outcome=charge.get('outcome'))
        _require(extension._identity(charge, 'charge_id') and charge.get('schema_version') == 'strategy-account-segment-charge-v1'
            and charge.get('dispatch_id') == dispatch['dispatch_id'] and charge.get('seconds') == seconds
            and charge.get('basis') == basis and charge.get('time_extension_proof') == proof
            and charge.get('outcome') == ('FAILED' if number == FAILED_SEGMENT_COUNT else parent.get('outcome'))
            and (number == FAILED_SEGMENT_COUNT or charge.get('outcome') in ('CONTINUE', 'PAUSED')),
            'LOCAL_CHARGE_CHAIN_CONFLICT')
        resource_path = account_root / (prefix + '_RESOURCE.json')
        resource_ref = None
        if charge.get('measured_seconds') is not None:
            resource_ref = _reference(resource_path, root); resource = _read(resource_ref, root)
            _require(resource_ref['sha256'] == charge.get('evidence_identity')
                and resource.get('dispatch_id') == dispatch['dispatch_id']
                and resource.get('elapsed_wall_seconds') == charge['measured_seconds']
                and resource.get('time_extension_proof') == proof, 'MEASURED_RESOURCE_CONFLICT')
        else:
            # 已收费的早期UNKNOWN仍按原上界保留；第50段原FAILED必须有真实资源原件。
            _require(number < FAILED_SEGMENT_COUNT and charge.get('evidence_identity') is None
                and basis == 'UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND', 'FAILED_RESOURCE_REQUIRED')
            if resource_path.exists(): resource_ref = _reference(resource_path, root)
        _require(parent.get('operation_id') == operation['operation_id'] and parent.get('segment_number') == number
            and all(parent.get(key) == dispatch.get(key) for key in ('profile_hash', 'upper_bound_seconds', 'dispatched_at'))
            and all(parent.get(key) == charge.get(key) for key in ('measured_seconds', 'seconds', 'basis',
                'evidence_identity', 'outcome', 'charged_at', 'time_extension_proof')), 'PARENT_SEGMENT_PREFIX_CONFLICT')
        refs.extend((dispatch_ref, charge_ref))
        if resource_ref is not None: refs.append(resource_ref)
        for suffix in ('_EXECUTION_VERSION.json', '_RESUME.json', '_WORKER.json', '_INPUT_ACCESS.json', '_FAILURE.json', '_STATUS.json'):
            path = account_root / (prefix + suffix)
            if path.exists(): refs.append(_reference(path, root))
        charged += seconds; head = charge['charge_id']; rows.append({'dispatch': dispatch, 'charge': charge})
    _require(len(rows) == FAILED_SEGMENT_COUNT and operation.get('active_wall_seconds') == charged
        and len(list(account_root.glob(name + '_SEGMENT_*_DISPATCH.json'))) == len(rows)
        and len(list(account_root.glob(name + '_SEGMENT_*_CHARGE.json'))) == len(rows), 'SEGMENT_COUNT_CONFLICT')
    settlement_ref = _reference(account_root / (name + '_SETTLEMENT.json'), root)
    settlement = _read(settlement_ref, root)
    _require(all(settlement.get(key) == value for key, value in start.items())
        and settlement.get('completed') is False and settlement.get('wall_seconds') == charged
        and settlement.get('time_extension_proof') == expected_proof and settlement.get('result_sha256') is None
        and isinstance(settlement.get('error'), str) and settlement['error']
        and operation.get('evidence_identity') == head and operation.get('actual', {}).get('wall_seconds') == math.ceil(charged),
        'FAILED_SETTLEMENT_CONFLICT')
    for suffix in ('_RESOURCE.json', '_WORKER.json', '_INPUT_ACCESS.json', '_FAILURE.json'):
        path = account_root / (name + suffix)
        if path.exists(): refs.append(_reference(path, root))
    return {'start_ref': start_ref, 'settlement_ref': settlement_ref, 'charged_seconds': charged,
        'charge_head': head, 'segment_refs': _refs(refs, root)}


def _session_boundary(root, previous, *, frozen=None, exact=False):
    from .account_version_continuation_v1 import _session_reference
    references = previous['session_boundary']['session_refs']
    for ref in references.values(): _session_reference(ref, root, previous['approval_store'])
    if not references:
        result = {'session_refs': {}, 'event_refs': [], 'event_head': None, 'terminal_refs': []}
        _require(frozen is None or frozen == result, 'SESSION_PREFIX_CHANGED')
        return result
    audit = _path(Path(references['audit_start']['path']).parent, root / 'reports')
    refs = []; rows = []; head = None
    for path in sorted((audit / 'events').glob('*.json')):
        startup_checkpoint('FAILED_ACCOUNT_RECOVERY_SESSION_VERIFY')
        ref = _reference(path, root); row = _read(ref, root)
        _require(path.stem == str(len(rows) + 1).zfill(6) and extension._identity(row)
            and row.get('previous') == head, 'SESSION_CHAIN_CHANGED')
        rows.append(row); refs.append(ref); head = row['identity']
    prior = previous['session_boundary']['event_refs']
    _require(refs[:len(prior)] == prior, 'PREDECESSOR_SESSION_PREFIX_CHANGED')
    terminal = [_reference(audit / 'ATTEMPT_END.json', root)] if (audit / 'ATTEMPT_END.json').exists() else []
    if frozen is None or exact:
        if terminal:
            _require(_read(terminal[0], root).get('outcome') == 'FAILED', 'SESSION_ALREADY_COMPLETE')
        _require(not (audit / 'FEEDBACK.json').exists(), 'SESSION_ALREADY_COMPLETE')
    result = {'session_refs': deepcopy(references), 'event_refs': refs, 'event_head': head, 'terminal_refs': terminal}
    if frozen is None or exact:
        latest = next((row for row in reversed(rows) if row['action'] in (
            'ADVANCE_STARTED', 'ADVANCE_RETURNED', 'RECONCILE_RETURNED', 'PUBLIC_RECONCILED', 'ERROR')), None)
        _require(latest is None or latest['action'] != 'ADVANCE_STARTED', 'SESSION_RECONCILIATION_REQUIRED')
    if frozen is not None:
        _require(result['session_refs'] == frozen['session_refs']
            and refs[:len(frozen['event_refs'])] == frozen['event_refs']
            and all(ref in terminal for ref in frozen['terminal_refs']), 'SESSION_PREFIX_CHANGED')
        if exact: _require(result == frozen, 'SESSION_INSTALL_BOUNDARY_CHANGED')
    return result


class FailedAccountRecoveryV1:
    """summary/freeze只冻结新治理证明；父安装和公共派发各自独立执行。"""

    def __init__(self, root, manifest_ref):
        self.root = checked_directory_path(root, error_code='FAILED_ACCOUNT_RECOVERY_ROOT_INVALID')
        self.scope_reference = deepcopy(manifest_ref)

    @staticmethod
    def recovery_identity(authorization_id, task_id):
        return stable_hash({'schema_version': VERSION, 'authorization_id': authorization_id, 'task_id': task_id})

    @classmethod
    def summary(cls, root, *, authorization_id, task_ref, predecessor_manifest_ref, source_replacements,
                runtime_source_refs, economic_source_refs, proposal_ref, ci_ref, calendar_deadline_extension=None):
        root = checked_directory_path(root, error_code='FAILED_ACCOUNT_RECOVERY_ROOT_INVALID')
        directory, auth_ref, base, approvals = extension._authorization(root, authorization_id)
        previous, continuation, job = _predecessor(root, predecessor_manifest_ref, base, approvals)
        task = _read(task_ref, root); original = previous['original']; account_root = Path(job['root'])
        _require(task_ref == original['task'] and task.get('task_id') == previous['task_id']
            and original['job']['path'] == str(Path(task_ref['path']).parent / 'account' / 'JOB.json')
            and job.get('objective_id') == base['objective_id'] and account_root == Path(original['job']['path']).parent,
            'TASK_OR_JOB_CONFLICT')
        _require(set(job['plans']) == set(previous['accounts']) == set(previous['successor_plans'])
            and len(job['plans']) == 2 and {name.rsplit('_', 1)[-1] for name in job['plans']} == {'BASE', 'STRESS'},
            'TWO_ORIGINAL_ACCOUNTS_REQUIRED')
        profile = validate_execution_profile(job['resources'])
        _require(profile['profile_id'] == SEGMENTED_PROFILE and profile['purpose'] == 'RESEARCH_ACCOUNT'
            and profile['total_seconds'] == 28800 and profile['account_sessions'] == 504
            and profile['worker_seconds'] == 900 and profile['memory_mib'] == 2048
            and profile['threads'] == profile['concurrency'] == 1, 'ORIGINAL_PROFILE_CONFLICT')
        replacements, runtime, economic = _sources(root, previous, continuation, job, source_replacements,
            runtime_source_refs, economic_source_refs)
        snapshot, live_fingerprint = _capabilities(root, base, approvals, previous, continuation, replacements)
        ledger, operations, effective, trials = _ledger_boundary(root, directory, base, previous, predecessor_manifest_ref)
        from .account_version_continuation_v1 import _control
        control = _control(job)
        receipt = _read(original['receipt'], root); mapping = receipt.get('source', {}).get('operation_ids', {})
        _require(extension._identity(receipt, 'receipt_id') and receipt.get('strategy_plans') == job['plans']
            and receipt.get('input_identity') == job['input_identity'] and set(mapping) == set(job['plans'])
            and len(set(mapping.values())) == 2, 'RECEIPT_OR_MAPPING_CONFLICT')
        recovery_id = cls.recovery_identity(authorization_id, task['task_id']); accounts = {}; successors = {}; failed = None
        for name, plan in job['plans'].items():
            op = operations.get(mapping[name], {}); old = previous['accounts'][name]
            _require(extension._identity(plan, 'plan_id') and plan['runtime'] == job['items'][name]
                and plan['runtime']['execution_profile'] == plan['backend']['execution_profile'] == profile
                and op.get('operation_id') == old['operation_id'] and op.get('kind') == 'ACCOUNT'
                and op.get('stage') == 'EXPLORATION' and op.get('subject_identity') == plan['plan_id']
                and op.get('execution_profile') == profile and op.get('effective_total_seconds') == TOTAL_SECONDS
                and op.get('effective_upper_bounds', {}).get('wall_seconds') == TOTAL_SECONDS
                and op.get('active_segment') is None and not op.get('resource_overrun')
                and op.get('upper_bounds') == old['original_upper_bounds'], 'ORIGINAL_OPERATION_CONFLICT')
            successors[name] = extension.project_governance_plan(previous['successor_plans'][name], replacements)
            row = {'operation_id': mapping[name], 'status_at_install': op['status'], 'original_profile': deepcopy(profile),
                'original_upper_bounds': deepcopy(op['upper_bounds']), 'effective_total_seconds': TOTAL_SECONDS,
                'original_plan_id': plan['plan_id'], 'predecessor_plan_id': previous['successor_plans'][name]['plan_id'],
                'successor_plan_id': successors[name]['plan_id'], 'parent_operation': deepcopy(op),
                'parent_actual': deepcopy(op.get('actual')), 'original_settlement': None,
                'time_extension_proof': {'manifest_ref': deepcopy(predecessor_manifest_ref), 'extension_id': previous['extension_id']},
                'segment_count': len(op.get('segments', [])), 'charged_seconds': op.get('active_wall_seconds', 0),
                'parent_segment_prefix_hash': stable_hash(op.get('segments', [])),
                'next_segment_number': len(op.get('segments', [])) + 1, 'remaining_reserved_seconds': 0,
                'charge_head': None, 'checkpoint': None, 'start_ref': None, 'settlement_ref': None, 'segment_refs': [],
                'trial_boundary': deepcopy(trials.get(mapping[name]))}
            trial = row['trial_boundary']; identity = trial.get('identity', {}) if trial else {}
            _require(trial is not None and identity.get('trial_id') == mapping[name]
                and identity.get('candidate_id') == identity.get('candidate_hash') == plan['plan_id']
                and identity.get('batch_id') == op['batch_id'] and identity.get('family_id') == 'ACCOUNT'
                and identity.get('reservation_id') == 'RUN-TRIAL-' + stable_hash({'run_id': authorization_id,
                    'batch_id': op['batch_id'], 'trial_id': mapping[name], 'candidate_id': plan['plan_id'],
                    'candidate_hash': plan['plan_id']})[:24], 'TRIAL_IDENTITY_CONFLICT')
            _require(not (account_root / (name + '_RESULT.json')).exists(), 'ACCOUNT_ALREADY_COMPLETE')
            if name.endswith('_BASE'):
                _require(op['status'] == 'FAILED' and trial['accessed'] and trial['completed'], 'ORIGINAL_FAILED_TRIAL_REQUIRED')
                row.update(_local_boundary(root, job, name, receipt, op, previous, predecessor_manifest_ref))
                _require(row['segment_count'] == FAILED_SEGMENT_COUNT and row['charged_seconds'] == CANONICAL_SECONDS
                    and op['actual']['wall_seconds'] == PARENT_USED_SECONDS and op['actual']['account_jobs'] == 1,
                    'FAILED_BOUNDARY_CONFLICT')
                row['original_settlement'] = {key: deepcopy(op[key]) for key in ('actual', 'outcome', 'evidence_identity')}
                row['remaining_reserved_seconds'] = TOTAL_SECONDS - op['actual']['wall_seconds']
                cp = extension._checkpoint(root, job, name)
                _require(cp is not None and cp['processed_days'] == COMMITTED_DAYS, 'COMMITTED_BOUNDARY_REQUIRED')
                cp['archive_ref'] = {'path': str(Path(task_ref['path']).parent / 'failed-account-recoveries'
                    / recovery_id / 'BOUNDARY_CHECKPOINTS' / (name + '.json')), 'sha256': cp['reference']['sha256']}
                row['checkpoint'] = cp; failed = name
            else:
                _require(op['status'] == 'RESERVED' and not trial['accessed'] and not trial['completed']
                    and not op.get('segments') and not op.get('active_wall_seconds', 0)
                    and not any((account_root / (name + suffix)).exists() for suffix in (
                        '_START.json', '_SETTLEMENT.json', '_FAILURE.json'))
                    and extension._checkpoint(root, job, name) is None, 'ORIGINAL_STRESS_MUST_BE_UNSTARTED')
            accounts[name] = row
        _require(failed is not None and len({op['batch_id'] for op in (operations[mapping[name]] for name in mapping)}) == 1,
            'ORIGINAL_ACCOUNT_PAIR_CONFLICT')
        _require(proposal_ref == _reference(proposal_ref['path'], root / 'reports'), 'PROPOSAL_CHANGED')
        ci = _read(ci_ref, root)
        _require(ci.get('status') == 'PASSED' and isinstance(ci.get('head'), str) and re.fullmatch(r'[a-f0-9]{40}', ci['head'])
            and ci.get('runtime_sources') == runtime and ci.get('source_replacements') == replacements
            and ci.get('economic_sources') == economic, 'EXACT_CI_REQUIRED')
        _require(previous['expires_at'] == effective['expires_at'], 'EXPIRED_OR_EXPIRY_CHANGED')
        calendar = None
        if calendar_deadline_extension is not None:
            calendar = _validate_calendar_deadline_extension(root, calendar_deadline_extension,
                authorization_id=authorization_id, objective_id=base['objective_id'],
                previous_expires_at=previous['expires_at'], global_wall_seconds=effective['resource_limits']['wall_seconds'])
        execution_expires_at = calendar['expires_at'] if calendar is not None else previous['expires_at']
        _require(datetime.now(timezone.utc) < datetime.fromisoformat(execution_expires_at), 'EXPIRED_OR_EXPIRY_CHANGED')
        core = {'schema_version': SUMMARY_VERSION, 'root': str(root), 'authorization_id': authorization_id,
            'objective_id': base['objective_id'], 'task_id': task['task_id'], 'recovery_id': recovery_id,
            'approval_store': str(approvals.directory), 'base_authorization_ref': auth_ref,
            'base_authorization_identity': stable_hash(base), 'scope_hash': base['scope_policy']['scope_hash'],
            'expires_at': previous['expires_at'], 'predecessor_manifest_ref': deepcopy(predecessor_manifest_ref),
            'predecessor_summary_identity': previous['summary_identity'], 'original_failed_account': failed,
            'original': {**deepcopy(original), 'control': [_reference(account_root / ('CONTROL_'
                + str(row['sequence']).zfill(6) + '.json'), root) for row in control['events']], 'control_head': control['head']},
            'budget_boundary': ledger, 'accounts': accounts, 'successor_plans': successors,
            'session_boundary': _session_boundary(root, previous), 'source_replacements': replacements,
            'runtime_sources': runtime, 'economic_sources': economic, 'capabilities_snapshot': snapshot,
            'live_capabilities_fingerprint': live_fingerprint, 'effective_authorization_identity': stable_hash(effective),
            'global_wall_seconds': effective['resource_limits']['wall_seconds'],
            'proposal_ref': deepcopy(proposal_ref), 'ci_ref': deepcopy(ci_ref), 'maximum_recoveries': 1,
            'allowed_action': 'ONE_FAILED_ACCOUNT_PRODUCTION_CONTINUATION',
            'control_boundary': {'head': control['head'], 'paused': control['paused'],
                'consumes_historical_pause': control['paused']},
            'restored_reserved_seconds': accounts[failed]['remaining_reserved_seconds'],
            'grant_delta': 0, 'new_account_jobs': 0, 'new_candidate_attempts': 0, 'new_preparation_jobs': 0}
        if calendar is not None:
            core['calendar_deadline_extension'] = calendar
            from .failed_account_calendar_scope_v1 import calendar_task_scope
            task_scope = calendar_task_scope(root, core['original'], mapping, execution_expires_at)
            if task_scope is not None:
                core['calendar_task_scope'] = task_scope
        return {**core, 'summary_identity': stable_hash(core)}

    @classmethod
    def freeze(cls, root, summary, *, approval_ref):
        root = checked_directory_path(root, error_code='FAILED_ACCOUNT_RECOVERY_ROOT_INVALID')
        _require(isinstance(summary, dict) and summary.get('schema_version') == SUMMARY_VERSION
            and extension._identity(summary, 'summary_identity'), 'SUMMARY_INVALID')
        directory, _, base, approvals = extension._authorization(root, summary['authorization_id'])
        task_root = Path(summary['original']['task']['path']).parent
        slot = directory / 'failed-account-recovery-slots' / (summary['recovery_id'] + '.json')
        with ObjectiveMutationLock.for_resource(task_root / 'account' / 'LONG_EXECUTION.lock'):
            with ObjectiveMutationLock.for_resource(task_root / 'account' / 'CONTROL.lock'):
                _path(slot, root)
                if slot.exists():
                    saved = _read(_reference(slot, root), root)
                    context = cls.read(root, saved['manifest_ref'], base_authorization=base)
                    _require(context.summary_value == summary and context.approval_ref == approval_ref, 'SLOT_CONFLICT')
                    return deepcopy(context.scope_reference)
                fresh = cls.summary(root, authorization_id=summary['authorization_id'], task_ref=summary['original']['task'],
                    predecessor_manifest_ref=summary['predecessor_manifest_ref'], source_replacements=summary['source_replacements'],
                    runtime_source_refs=summary['runtime_sources'], economic_source_refs=summary['economic_sources'],
                    proposal_ref=summary['proposal_ref'], ci_ref=summary['ci_ref'],
                    **({'calendar_deadline_extension': summary['calendar_deadline_extension']}
                       if 'calendar_deadline_extension' in summary else {}))
                _require(fresh == summary, 'SUMMARY_CHANGED'); approvals.require(approval_ref, summary)
                for row in summary['accounts'].values():
                    cp = row['checkpoint']
                    if cp is None: continue
                    archive = _path(cp['archive_ref']['path'], root)
                    raw = read_file_bytes(cp['reference']['path'], root=root,
                        error_code='FAILED_ACCOUNT_RECOVERY_CHECKPOINT_CHANGED')
                    _require(hashlib.sha256(raw).hexdigest() == cp['archive_ref']['sha256'], 'CHECKPOINT_CHANGED')
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with archive.open('xb') as output: output.write(raw)
                    except FileExistsError:
                        _require(_reference(archive, root) == cp['archive_ref'], 'CHECKPOINT_ARCHIVE_CHANGED')
                manifest = {'schema_version': VERSION, 'summary': deepcopy(summary), 'approval_ref': deepcopy(approval_ref)}
                manifest['identity'] = stable_hash(manifest)
                path = task_root / 'failed-account-recoveries' / summary['recovery_id'] / 'MANIFEST.json'
                _path(path, root); _write_once(path, manifest); reference = _reference(path, root)
                _write_once(slot, {'recovery_id': summary['recovery_id'], 'manifest_ref': reference})
                return reference

    @classmethod
    @extension._verified_recovery_reader
    def read(cls, root, reference, *, base_authorization=None, for_install=False, for_dispatch=False):
        _require(type(for_install) is bool and type(for_dispatch) is bool and not (for_install and for_dispatch), 'MODE_INVALID')
        context = cls(root, reference)
        manifest, value, directory, base, approvals = _owner_binding(
            context, reference, base_authorization=base_authorization)
        execution_expires_at = value['expires_at']
        if 'calendar_deadline_extension' in value:
            calendar = _validate_calendar_deadline_extension(context.root, value['calendar_deadline_extension'],
                authorization_id=value['authorization_id'], objective_id=value['objective_id'],
                previous_expires_at=value['expires_at'], global_wall_seconds=value['global_wall_seconds'])
            execution_expires_at = calendar['expires_at']
        control_boundary = value.get('control_boundary', {})
        _require(set(control_boundary) == {'head', 'paused', 'consumes_historical_pause'}
            and control_boundary['head'] == value['original']['control_head']
            and type(control_boundary['paused']) is bool
            and control_boundary['consumes_historical_pause'] is control_boundary['paused'], 'CONTROL_BOUNDARY_CONFLICT')
        previous, continuation, job = _predecessor(context.root, value['predecessor_manifest_ref'], base, approvals)
        task_root = Path(value['original']['task']['path']).parent
        _require(value['recovery_id'] == cls.recovery_identity(value['authorization_id'], value['task_id'])
            and Path(reference['path']) == task_root / 'failed-account-recoveries' / value['recovery_id'] / 'MANIFEST.json'
            and value['predecessor_summary_identity'] == previous['summary_identity']
            and all(value['original'][key] == previous['original'][key] for key in ('task', 'job', 'preview', 'receipt')),
            'MANIFEST_PATH_OR_PREDECESSOR_CONFLICT')
        slot = _read(_reference(directory / 'failed-account-recovery-slots' / (value['recovery_id'] + '.json'), context.root), context.root)
        _require(slot == {'recovery_id': value['recovery_id'], 'manifest_ref': reference}, 'SLOT_CONFLICT')
        _refs([value['original'][key] for key in ('task', 'job', 'preview', 'receipt')]
            + value['original']['control'] + [value['proposal_ref'], value['ci_ref']], context.root)
        _sources(context.root, previous, continuation, job, value['source_replacements'], value['runtime_sources'], value['economic_sources'])
        snapshot, live = _capabilities(context.root, base, approvals, previous, continuation, value['source_replacements'])
        _require(snapshot == value['capabilities_snapshot'] and live == value['live_capabilities_fingerprint'], 'CAPABILITIES_CHANGED')
        _, operations, effective, trials = _ledger_boundary(context.root, directory, base, previous,
            value['predecessor_manifest_ref'], value['budget_boundary'])
        _require(stable_hash(effective) == value['effective_authorization_identity']
            and effective['resource_limits']['wall_seconds'] == value['global_wall_seconds']
            and value['expires_at'] == previous['expires_at'] == effective['expires_at'], 'LIMITS_OR_EXPIRY_CHANGED')
        _require(set(value['accounts']) == set(job['plans']) == set(value['successor_plans']), 'ACCOUNTS_CONFLICT')
        failed = value['original_failed_account']; _require(failed in value['accounts'] and failed.endswith('_BASE'), 'FAILED_PURPOSE_CONFLICT')
        for name, row in value['accounts'].items():
            op = operations.get(row['operation_id'], {})
            _require(row['parent_operation'] == op and row['status_at_install'] == op.get('status')
                and row['parent_actual'] == op.get('actual') and row['trial_boundary'] == trials.get(row['operation_id'])
                and row['original_profile'] == job['resources'] == op.get('execution_profile')
                and row['original_upper_bounds'] == op.get('upper_bounds') and row['effective_total_seconds'] == TOTAL_SECONDS
                and row['charged_seconds'] == op.get('active_wall_seconds', 0)
                and row['segment_count'] == len(op.get('segments', [])) and row['next_segment_number'] == row['segment_count'] + 1
                and row['parent_segment_prefix_hash'] == stable_hash(op.get('segments', [])), 'ACCOUNT_BOUNDARY_CONFLICT')
            projected = extension.project_governance_plan(previous['successor_plans'][name], value['source_replacements'])
            _require(value['successor_plans'][name] == projected and row['successor_plan_id'] == projected['plan_id'], 'SUCCESSOR_PLAN_CONFLICT')
            _refs(row['segment_refs'] + ([row['start_ref']] if row['start_ref'] else [])
                + ([row['settlement_ref']] if row['settlement_ref'] else []), context.root)
            if name == failed:
                _require(op.get('status') == 'FAILED' and row['segment_count'] == FAILED_SEGMENT_COUNT
                    and row['charged_seconds'] == CANONICAL_SECONDS and op['actual']['wall_seconds'] == PARENT_USED_SECONDS
                    and row['original_settlement'] == {key: op[key] for key in ('actual', 'outcome', 'evidence_identity')}
                    and row['remaining_reserved_seconds'] == TOTAL_SECONDS - op['actual']['wall_seconds']
                    and value['restored_reserved_seconds'] == row['remaining_reserved_seconds'] == RESTORED_RESERVED_SECONDS,
                    'FAILED_BOUNDARY_CONFLICT')
                cp = row['checkpoint']
                _require(cp is not None and cp['processed_days'] == COMMITTED_DAYS
                    and Path(cp['archive_ref']['path']) == task_root / 'failed-account-recoveries' / value['recovery_id']
                        / 'BOUNDARY_CHECKPOINTS' / (name + '.json')
                    and cp['archive_ref']['sha256'] == cp['reference']['sha256']
                    and _reference(cp['archive_ref']['path'], context.root) == cp['archive_ref'], 'CHECKPOINT_ARCHIVE_CHANGED')
                current_cp = extension._checkpoint(context.root, job, name, frozen=cp, exact=for_install)
                if current_cp['processed_days'] == cp['processed_days']:
                    _require(current_cp == {key: member for key, member in cp.items() if key != 'archive_ref'},
                        'CHECKPOINT_BOUNDARY_CHANGED')
            else:
                _require(op.get('status') == 'RESERVED' and row['segment_count'] == row['remaining_reserved_seconds'] == 0
                    and row['original_settlement'] is None and row['checkpoint'] is None, 'STRESS_BOUNDARY_CONFLICT')
        _session_boundary(context.root, previous, frozen=value['session_boundary'], exact=for_install)
        if 'calendar_deadline_extension' in value:
            from .failed_account_calendar_scope_v1 import calendar_task_scope
            task_scope = calendar_task_scope(context.root, value['original'],
                {name: row['operation_id'] for name, row in value['accounts'].items()}, execution_expires_at)
            _require(value.get('calendar_task_scope') == task_scope
                and ('calendar_task_scope' in value) is (task_scope is not None), 'CALENDAR_TASK_SCOPE_CONFLICT')
        if for_install:
            _require(_ledger_boundary(context.root, directory, base, previous, value['predecessor_manifest_ref'])[0]
                == value['budget_boundary'], 'INSTALL_BOUNDARY_CHANGED')
            from .account_version_continuation_v1 import _control
            control = _control(job)
            _require(control['head'] == value['original']['control_head']
                and control['paused'] is control_boundary['paused'], 'CONTROL_INSTALL_BOUNDARY_CHANGED')
        if for_dispatch:
            _dispatch_boundary(context.root, directory, base, value, reference)
        if for_install or for_dispatch:
            _require(datetime.now(timezone.utc) < datetime.fromisoformat(execution_expires_at), 'EXPIRED')
        context.summary_value = extension._copy_json_tree(value); context.summary = extension._copy_json_tree(value); context.approval_ref = deepcopy(manifest['approval_ref'])
        context.recovery_id = value['recovery_id']; context.job = job; context.job_path = Path(value['original']['job']['path'])
        context.task_root = task_root; context.predecessor_summary = extension._copy_json_tree(previous)
        context.original_failed_account = failed
        context._execution_expires_at = execution_expires_at
        return context

    @property
    def execution_expires_at(self):
        return self._execution_expires_at

    @classmethod
    def from_job(cls, job, *, for_dispatch=False):
        located = _job_manifest_reference(job)
        if located is None: return None
        root, reference, account_root = located
        context = cls.read(root, reference, for_dispatch=for_dispatch)
        _require(context.job == job and context.job_path == account_root / 'JOB.json', 'JOB_CHANGED')
        return context

    def proof(self, name=None):
        _require(name is None or name in self.summary_value['accounts'], 'PURPOSE_CONFLICT')
        return {'manifest_ref': deepcopy(self.scope_reference), 'recovery_id': self.recovery_id,
            **({'purpose': name} if name is not None else {})}

    def successor_plan(self, name):
        _require(name in self.summary_value['successor_plans'], 'PURPOSE_CONFLICT')
        return deepcopy(self.summary_value['successor_plans'][name])

    def effective_total_seconds(self, name):
        _require(name in self.summary_value['accounts'], 'PURPOSE_CONFLICT')
        return self.summary_value['accounts'][name]['effective_total_seconds']

    def require_predecessor(self, reference, *, base_authorization=None):
        """即使构造了同类型伪对象，也须重新读取已签名新manifest与规范slot。"""
        _require(type(self) is FailedAccountRecoveryV1, 'SIGNED_CONTEXT_REQUIRED')
        fresh = type(self).read(self.root, self.scope_reference, base_authorization=base_authorization)
        _require(reference == fresh.summary_value['predecessor_manifest_ref'], 'PREDECESSOR_REFERENCE_CONFLICT')
        return deepcopy(fresh.predecessor_summary)

    def require_capability_bridge(self, reference=None, *, base_authorization=None):
        """只交回已绑定原Owner bridge和原scope快照；不用当前fingerprint猜历史绑定。"""
        _require(type(self) is FailedAccountRecoveryV1, 'SIGNED_CONTEXT_REQUIRED')
        fresh = type(self).read(self.root, self.scope_reference, base_authorization=base_authorization)
        expected = fresh.predecessor_summary['predecessor_bridge_ref']
        _require(reference is None or reference == expected, 'PREDECESSOR_BRIDGE_REFERENCE_CONFLICT')
        owner = checked_directory_path(fresh.summary_value['approval_store'],
            error_code='FAILED_ACCOUNT_RECOVERY_OWNER_INVALID')
        record = _read({key: expected[key] for key in ('path', 'sha256')}, owner)
        return {'reference': deepcopy(expected), 'record': record,
            'capabilities_snapshot': deepcopy(fresh.summary_value['capabilities_snapshot'])}


def _dispatch_boundary(root, directory, base, summary, reference):
    _, _, events = _events(root, directory, base, summary['budget_boundary'])
    proof = {'manifest_ref': reference, 'recovery_id': summary['recovery_id']}; installed = False
    for event in events[summary['budget_boundary']['line_count']:]:
        kind, item = event['event_type'], event['payload']
        _require(kind not in ('CAMPAIGN_REVOKED', 'CAMPAIGN_GRANT_ADDED'), 'REVOKED_OR_LIMITS_CHANGED')
        _require(kind not in ('CAMPAIGN_OPERATION_UNKNOWN', 'CAMPAIGN_FAILED_ACCOUNT_RECOVERY_UNKNOWN'), 'UNKNOWN_OPERATION')
        if kind == 'CAMPAIGN_FAILED_ACCOUNT_RECOVERY_INSTALLED':
            _require(not installed and item.get('proof') == proof, 'INSTALLATION_CONFLICT'); installed = True
        if kind in ('CAMPAIGN_EXECUTION_SEGMENT_CHARGED', 'CAMPAIGN_FAILED_ACCOUNT_RECOVERY_SETTLED'):
            if item.get('failed_account_recovery_proof', item.get('proof')) == proof:
                _require(item.get('outcome') != 'FAILED', 'SECOND_FAILURE_NO_RETRY')
    _require(installed, 'NOT_INSTALLED')
    _require(not (Path(summary['original']['job']['path']).parent / 'REVOKED.json').exists(), 'ACCOUNT_REVOKED')


def validate_predecessor_sources(previous_summary, *, recovery):
    """只允许已签发恢复能力继承其确切扩时前驱，不提供通用历史跳过。"""
    _require(type(recovery) is FailedAccountRecoveryV1, 'SIGNED_CONTEXT_REQUIRED')
    fresh = FailedAccountRecoveryV1.read(recovery.root, recovery.scope_reference)
    _require(previous_summary == fresh.predecessor_summary, 'PREDECESSOR_SUMMARY_CONFLICT')
    # fresh.read已逐项验证old→archive→new及完整经济库存；返回值不接受调用者映射。
    return deepcopy(fresh.summary_value['source_replacements'])


def resolve_failed_account_recovery_manifest(manifest_ref, *, root, authorization_id, objective_id,
                                             for_dispatch=False, for_install=False):
    """父事件重放的纯证明读取；不得进入campaign、账户API或签发路径。"""
    context = FailedAccountRecoveryV1.read(root, manifest_ref, for_dispatch=for_dispatch, for_install=for_install)
    value = context.summary_value
    _require(value['authorization_id'] == authorization_id and value['objective_id'] == objective_id,
        'AUTHORIZATION_OR_OBJECTIVE_CONFLICT')
    accounts = extension._copy_json_tree(value['accounts']); failed = value['original_failed_account']; account = accounts[failed]
    result = {'recovery_id': context.recovery_id, 'manifest_ref': deepcopy(manifest_ref), 'proof': context.proof(),
        'authorization_id': authorization_id, 'objective_id': objective_id,
        'base_authorization_identity': value['base_authorization_identity'], 'expires_at': value['expires_at'],
        'original_failed_account': failed, 'failed_purpose': failed, 'operation_id': account['operation_id'],
        'operation_ids': {name: row['operation_id'] for name, row in accounts.items()}, 'accounts': accounts, 'account': account,
        'summary_value': extension._copy_json_tree(value), 'restored_reserved_seconds': value['restored_reserved_seconds'],
        'capabilities_snapshot': extension._copy_json_tree(value['capabilities_snapshot']),
        'source_replacements': extension._copy_json_tree(value['source_replacements']), 'runtime_sources': extension._copy_json_tree(value['runtime_sources']),
        'economic_sources': extension._copy_json_tree(value['economic_sources']), 'successor_plans': extension._copy_json_tree(value['successor_plans']),
        'active_plans': extension._copy_json_tree(value['successor_plans'])}
    if 'calendar_deadline_extension' in value:
        result.update(execution_expires_at=context.execution_expires_at,
                      calendar_deadline_extension=extension._copy_json_tree(value['calendar_deadline_extension']))
        if 'calendar_task_scope' in value:
            result['calendar_task_scope'] = extension._copy_json_tree(value['calendar_task_scope'])
    return result
