"""零账户日边界的一次版本接续；账户用途、原件及累计计费保持原身份。"""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import re

from chanlun_trader.account_startup_boundary_v1 import startup_checkpoint
from .account_version_capability_bridge_v1 import (
    AccountVersionCapabilityBridgeV1, BUSINESS_PATH, _hash, _path, _read, _reference, _require,
)
from .campaign_scope_v1 import OwnerApprovalStoreV1
from .common import stable_hash
from .exploration_governance import immutable
from .mutation_boundary import ObjectiveMutationLock
from .research_campaign_v1 import ResearchCampaignV1
from .universe_execution_profile_v1 import SEGMENTED_PROFILE, segment_allowance, validate_execution_profile


VERSION = 'ACCOUNT_VERSION_CONTINUATION_MANIFEST_V1'
SUMMARY_VERSION = 'OWNER_ACCOUNT_VERSION_CONTINUATION_SUMMARY_V1'


def _identity(value, field='identity'):
    return isinstance(value, dict) and value.get(field) == stable_hash({k: v for k, v in value.items() if k != field})


def _project(value, replacements):
    """仅替换已批准的源码身份，语义、输入和路径逐字段保留。"""
    if isinstance(value, list):
        return [_project(row, replacements) for row in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    result = {}
    for key, item in value.items():
        if key == 'source_hashes':
            result[key] = {}
            for path, digest in item.items():
                row = replacements.get(path)
                _require(row is None or digest == row['old_sha256'], 'PLAN_SOURCE_CONFLICT')
                result[key][path] = row['new_sha256'] if row else digest
        else:
            result[key] = _project(item, replacements)
    return result


def _control(job):
    root = Path(job['root']); rows = []; head = None; paused = False
    for path in sorted(root.glob('CONTROL_*.json')):
        startup_checkpoint('ACCOUNT_VERSION_CONTROL')
        row = _read(_reference(path, root), root)
        _require(path.name == 'CONTROL_' + str(len(rows) + 1).zfill(6) + '.json'
            and row.get('sequence') == len(rows) + 1 and row.get('previous_head') == head
            and row.get('job_sha256') == _reference(root / 'JOB.json', root)['sha256']
            and row.get('event') in ('PAUSE', 'RESUME') and _identity(row, 'event_id'), 'CONTROL_CHAIN_CONFLICT')
        head = row['event_id']; paused = row['event'] == 'PAUSE'; rows.append(row)
    return {'paused': paused, 'head': head, 'events': rows}


def _session_reference(reference, root, owner):
    """保留 START 的逻辑路径；MSIX 原件按实际归属和字节身份核验。"""
    _require(isinstance(reference, dict) and set(reference) == {'path', 'sha256'}
        and _hash(reference['sha256']), 'SESSION_REFERENCE_INVALID')
    logical = _path(reference['path'], None)
    canonical = logical.resolve(strict=True)
    owner = Path(owner).resolve(strict=True)
    boundary = owner if canonical.is_relative_to(owner) else root / 'reports'
    _require(_reference(canonical, boundary)['sha256'] == reference['sha256'], 'SESSION_REFERENCE_CHANGED')


def _feature_prefix(job, name, *, frozen=None):
    """只接受逐证券已提交回执；未提交数组行不作为可复用证据。"""
    root = Path(job['root']); folder = root / (name + '_FEATURES')
    if not folder.exists():
        _require(frozen is None, 'FEATURE_PREFIX_MISSING')
        return None
    import hashlib
    import numpy as np
    binding_ref = _reference(folder / 'PREPARATION_BINDING.json', root)
    binding = _read(binding_ref, root)
    _require(set(binding) == {'identity'} and _hash(binding['identity']), 'FEATURE_BINDING_INVALID')
    array_path = _path(folder / 'CONDITIONS.npy', root)
    symbols = job['plans'][name]['backend']['window']['symbols']
    values = np.load(array_path, mmap_mode='r', allow_pickle=False)
    try:
        _require(len(values.shape) == 3 and values.shape[0] == len(symbols)
            and values.shape[1] == len(job['plans'][name]['backend']['window']['calendar'])
            and values.shape[2] in (4, 7) and values.dtype == np.dtype('float64'), 'FEATURE_ARRAY_INVALID')
        rows = []
        for path in sorted(folder.glob('*.json')):
            startup_checkpoint('ACCOUNT_VERSION_FEATURE_RECEIPT')
            if not re.fullmatch(r'[0-9]{6}\.(SZ|SH)\.json', path.name):
                continue
            symbol = path.name[:-5]
            _require(symbol in symbols, 'FEATURE_SYMBOL_CONFLICT')
            ref = _reference(path, root); row = _read(ref, root)
            _require(set(row) == {'identity', 'preparation', 'array_hash'} and row['identity'] == binding['identity']
                and isinstance(row['preparation'], list)
                and row['array_hash'] == hashlib.sha256(values[symbols.index(symbol)].tobytes()).hexdigest(),
                'FEATURE_RECEIPT_CHANGED')
            rows.append({'symbol': symbol, 'reference': ref, 'array_hash': row['array_hash']})
        result = {'binding': binding_ref, 'identity': binding['identity'], 'array_path': str(array_path),
            'shape': list(values.shape), 'dtype': str(values.dtype), 'receipts': rows}
        if frozen is not None:
            _require(all(result[key] == frozen[key] for key in ('binding', 'identity', 'array_path', 'shape', 'dtype')),
                'FEATURE_PREFIX_CHANGED')
            indexed = {row['symbol']: row for row in rows}
            _require(all(indexed.get(row['symbol']) == row for row in frozen['receipts']), 'FEATURE_PREFIX_CHANGED')
        return result
    finally:
        values._mmap.close()


class AccountVersionContinuationV1:
    """from_job/validate 只读；freeze 只追加版本清单，派发由原公共入口完成。"""

    def __init__(self, root, manifest_ref):
        self.root = Path(root).resolve(strict=True)
        self.scope_reference = deepcopy(manifest_ref)
        self.time_extension = None
        self.time_extension_installed = False
        self.time_extension_mirror_pending = False
        self.recovery = None
        self.recovery_installed = False
        self.recovery_mirror_pending = False

    @staticmethod
    def continuation_identity(authorization_id, task_id):
        return stable_hash({'schema_version': VERSION, 'authorization_id': authorization_id, 'task_id': task_id})

    @staticmethod
    def _source_check(root, job, replacements, *, time_extension=None, recovery=None):
        mapping = {row['path']: row for row in replacements}
        successor = {}
        if time_extension is not None:
            from .account_purpose_time_extension_v1 import AccountPurposeTimeExtensionV1
            _require(type(time_extension) is AccountPurposeTimeExtensionV1, 'TIME_EXTENSION_CONTEXT_REQUIRED')
            successor = {row['path']: row for row in time_extension.summary_value['source_replacements']}
        recovered = {}
        if recovery is not None:
            from .failed_account_recovery_v1 import FailedAccountRecoveryV1, validate_predecessor_sources
            _require(type(recovery) is FailedAccountRecoveryV1 and time_extension is not None, 'SIGNED_RECOVERY_CONTEXT_REQUIRED')
            recovered = {row['path']: row for row in validate_predecessor_sources(time_extension.summary_value, recovery=recovery)}
        _require(len(mapping) == len(replacements), 'SOURCE_REPLACEMENTS_DUPLICATED')
        _require(isinstance(job.get('source_hashes'), dict) and job['source_hashes'], 'FROZEN_SOURCES_REQUIRED')
        for path, digest in job['source_hashes'].items():
            startup_checkpoint('ACCOUNT_VERSION_SOURCE_VERIFY')
            source = _path(path, root)
            row = mapping.get(path)
            _require(row is None or row['old_sha256'] == digest, 'ORIGINAL_SOURCE_CONFLICT')
            expected = row['new_sha256'] if row else digest
            following = successor.get(path)
            if following is not None:
                _require(following['old_sha256'] == expected
                    and following['archive_ref']['sha256'] == expected
                    and _reference(following['archive_ref']['path'], root / 'reports') == following['archive_ref'],
                    'TIME_EXTENSION_SOURCE_ARCHIVE_CHANGED')
                expected = following['new_sha256']
            following = recovered.get(path)
            if following is not None:
                _require(following['old_sha256'] == expected
                    and following['archive_ref']['sha256'] == expected
                    and _reference(following['archive_ref']['path'], root / 'reports') == following['archive_ref'],
                    'RECOVERY_SOURCE_ARCHIVE_CHANGED')
                expected = following['new_sha256']
            _require(_reference(source, root)['sha256'] == expected,
                'UNAPPROVED_SOURCE_CHANGED')
            if source.suffix == '.py':
                archive = Path(job['root']) / 'source-archive' / (digest + '_' + source.name)
                _require(_reference(archive, root)['sha256'] == digest, 'SOURCE_ARCHIVE_CHANGED')
        _require(str(root / BUSINESS_PATH) in mapping
            and job['source_hashes'].get(str(root / BUSINESS_PATH)) == mapping[str(root / BUSINESS_PATH)]['old_sha256'],
            'BUSINESS_ORIGINAL_SOURCE_CONFLICT')

    @classmethod
    def summary(cls, root, *, authorization_id, task_ref, capability_bridge_ref, proposal_ref, ci_ref,
                session_refs=None):
        root = Path(root).resolve(strict=True)
        campaign = ResearchCampaignV1(root, authorization_id)
        view = campaign.peek_status(); base = view['base_authorization']
        bridge = AccountVersionCapabilityBridgeV1.read(root, base, reference=capability_bridge_ref)
        bridge_summary = bridge['record']['summary']
        task = _read(task_ref, root); task_root = _path(Path(task_ref['path']).parent, root / 'reports')
        _require(_hash(task.get('task_id')) and task_root.name == task['task_id']
            and Path(task_ref['path']) == task_root / 'TASK.json', 'TASK_PATH_CONFLICT')
        job_path = task_root / 'account' / 'JOB.json'
        _require(task.get('job_path') == str(job_path), 'JOB_PATH_CONFLICT')
        job_ref = {'path': str(job_path), 'sha256': task['job_sha256']}; job = _read(job_ref, root)
        _require(job.get('root') == str(job_path.parent) and job.get('objective_id') == base['objective_id']
            and task.get('input_identity') == job.get('input_identity') and len(job.get('plans', {})) == 2
            and set(job.get('items', {})) == set(job['plans'])
            and task.get('plan_ids') == {name: plan['plan_id'] for name, plan in job['plans'].items()}, 'FROZEN_JOB_CONFLICT')
        profile = validate_execution_profile(job['resources'])
        _require(profile['profile_id'] == SEGMENTED_PROFILE and profile['purpose'] == 'RESEARCH_ACCOUNT'
            and profile['account_sessions'] == 504 and profile in base['execution_profiles']
            and job.get('resource_job_total_seconds') == profile['total_seconds'] * 2, 'PROFILE_CONFLICT')
        cls._source_check(root, job, bridge_summary['source_replacements'])
        preview_ref = _reference(task_root / 'PREVIEW.json', root); preview = _read(preview_ref, root)
        _require(_identity(preview, 'preview_identity') and preview.get('preview_identity') == task.get('preview_identity'),
            'PREVIEW_CONFLICT')
        receipt_ref = _reference(job_path.parent / 'CONFIRMATION.json', root); receipt = _read(receipt_ref, root)
        _require(_identity(receipt, 'receipt_id') and receipt.get('strategy_plans') == job['plans']
            and receipt.get('input_identity') == job['input_identity'] and receipt.get('objective_id') == job['objective_id']
            and receipt.get('budget_path') == job['budget_path'] and receipt['source'].get('origin') == 'CAMPAIGN_V1'
            and receipt['source'].get('campaign_root') == str(root)
            and receipt['source'].get('authorization_id') == authorization_id, 'RECEIPT_CONFLICT')
        control = _control(job)
        _require(control['paused'] and not view.get('revoked')
            and not any(op.get('status') == 'UNKNOWN' or op.get('active_segment') is not None
                        for op in view['operations'].values()), 'PAUSED_BOUNDARY_REQUIRED')
        mapping = receipt['source'].get('operation_ids')
        _require(isinstance(mapping, dict) and set(mapping) == set(job['plans'])
            and len(set(mapping.values())) == 2, 'OPERATION_MAPPING_CONFLICT')
        from .etf_account_governance_v1 import StrategyBatchGovernanceV1
        gov = StrategyBatchGovernanceV1(job['root'], job['budget_path'], job['objective_id'], job['plans'])
        accounts = {}; running = 0
        for name, plan in job['plans'].items():
            _require(_identity(plan, 'plan_id') and plan.get('runtime') == job['items'][name]
                and plan['backend'].get('backend') == 'UNIVERSE_ACCOUNT_BACKEND_V2'
                and plan['runtime'].get('execution_profile') == profile
                and plan['backend'].get('execution_profile') == profile, 'PLAN_CONFLICT')
            op = view['operations'].get(mapping[name], {})
            _require(op.get('kind') == 'ACCOUNT' and op.get('subject_identity') == plan['plan_id']
                and op.get('execution_profile') == profile and op.get('stage') == 'EXPLORATION'
                and op.get('upper_bounds', {}).get('account_jobs') == 1
                and op['upper_bounds'].get('wall_seconds') == profile['total_seconds']
                and op.get('status') in ('RUNNING', 'RESERVED'), 'ORIGINAL_OPERATION_CONFLICT')
            checkpoint = _path(plan['runtime']['backend_options']['checkpoint_path'], job_path.parent)
            days_root = job_path.parent / (name + '_DAYS')
            _require(not checkpoint.exists() and not any(days_root.glob('*.json.gz'))
                and not (job_path.parent / (name + '_RESULT.json')).exists()
                and not (job_path.parent / (name + '_SETTLEMENT.json')).exists(), 'ACCOUNT_DAYS_ALREADY_COMMITTED')
            start_path = job_path.parent / (name + '_START.json')
            row = {'operation_id': mapping[name], 'operation': deepcopy(op), 'plan_id': plan['plan_id'],
                'checkpoint_path': str(checkpoint), 'features': _feature_prefix(job, name), 'start': None,
                'segment_refs': [], 'segment_count': 0, 'charged_seconds': 0., 'head': None}
            if op['status'] == 'RUNNING':
                running += 1; row['start'] = _reference(start_path, root)
                from .budget import SearchBudgetRegistryV1
                start = _read(row['start'], root)
                budget = SearchBudgetRegistryV1(job['objective_id'], job['budget_path']).snapshot()
                _require(start.get('receipt_id') == receipt['receipt_id'] and start.get('kind') == name
                    and budget['settled_reservations'].get(start.get('reservation')) == 'CONSUMED',
                    'ORIGINAL_RESERVATION_CONFLICT')
                state = gov.segment_status(name)
                _require(state['pending'] is None and state['segments']
                    and state['segments'][-1]['charge']['outcome'] == 'PAUSED', 'PAUSED_BOUNDARY_REQUIRED')
                cls._parent_prefix(state, op, len(state['segments']))
                row.update(segment_count=len(state['segments']), charged_seconds=state['charged_seconds'], head=state['head'])
                for index in range(1, len(state['segments']) + 1):
                    prefix = name + '_SEGMENT_' + str(index).zfill(6)
                    row['segment_refs'].extend(_reference(job_path.parent / (prefix + suffix), root)
                        for suffix in ('_DISPATCH.json', '_CHARGE.json', '_RESOURCE.json'))
            else:
                _require(not start_path.exists() and not op.get('segments') and not op.get('active_wall_seconds', 0),
                    'RESERVED_ACCOUNT_ALREADY_STARTED')
            accounts[name] = row
        _require(running == 1, 'ONE_RUNNING_ACCOUNT_REQUIRED')
        _read(proposal_ref, root) if Path(proposal_ref['path']).suffix == '.json' else _require(
            proposal_ref == _reference(proposal_ref['path'], root / 'reports'), 'PROPOSAL_CHANGED')
        ci = _read(ci_ref, root)
        _require(ci.get('status') in ('PASSED', 'SUCCESS', 'CI_PASSED') and isinstance(ci.get('head'), str)
            and re.fullmatch(r'[a-f0-9]{40}', ci['head']) and ci.get('runtime_sources') == bridge_summary['runtime_sources']
            and ci.get('source_replacements') == bridge_summary['source_replacements'], 'EXACT_CI_REQUIRED')
        session_refs = deepcopy(session_refs or {})
        _require(isinstance(session_refs, dict), 'SESSION_REFERENCES_INVALID')
        for reference in session_refs.values():
            _session_reference(reference, root, base['scope_policy']['approval_store'])
        expires_at = min(base['expires_at'], receipt['expires_at'], view['authorization']['expires_at'],
            key=lambda value: datetime.fromisoformat(value))
        _require(datetime.now(timezone.utc) < datetime.fromisoformat(expires_at), 'EXPIRED')
        for row in accounts.values():
            segment_allowance(profile, row['charged_seconds'],
                seconds_to_expiry=(datetime.fromisoformat(expires_at) - datetime.now(timezone.utc)).total_seconds())
        core = {'schema_version': SUMMARY_VERSION, 'root': str(root), 'authorization_id': authorization_id,
            'objective_id': job['objective_id'], 'task_id': task['task_id'],
            'continuation_id': cls.continuation_identity(authorization_id, task['task_id']),
            'approval_store': base['scope_policy']['approval_store'], 'base_authorization_identity': stable_hash(base),
            'scope_hash': base['scope_policy']['scope_hash'], 'expires_at': expires_at,
            'original': {'task': deepcopy(task_ref), 'job': job_ref, 'preview': preview_ref, 'receipt': receipt_ref,
                'control': [_reference(job_path.parent / ('CONTROL_' + str(row['sequence']).zfill(6) + '.json'), root)
                    for row in control['events']], 'control_head': control['head']},
            'accounts': accounts, 'capability_bridge_ref': deepcopy(capability_bridge_ref),
            'source_replacements': deepcopy(bridge_summary['source_replacements']),
            'runtime_sources': deepcopy(bridge_summary['runtime_sources']), 'session_refs': session_refs,
            'proposal_ref': deepcopy(proposal_ref), 'ci_ref': deepcopy(ci_ref),
            'maximum_version_switches': 1, 'daily_commit_count_at_install': 0,
            'grant_delta': 0, 'new_account_jobs': 0, 'new_preparation_jobs': 0}
        version_id = stable_hash(core)
        replacements = {row['path']: row for row in core['source_replacements']}
        active = {}
        for name, plan in job['plans'].items():
            projected = _project(plan, replacements)
            projected['plan_id'] = stable_hash({k: v for k, v in projected.items() if k != 'plan_id'})
            active[name] = projected
        return {**core, 'version_id': version_id, 'active_plans': active}

    @staticmethod
    def _parent_prefix(state, operation, count):
        _require(operation.get('active_segment') is None or len(state['segments']) > count,
            'PARENT_ACTIVE_SEGMENT_CONFLICT')
        parents = operation.get('segments', [])
        _require(len(parents) == len(state['segments']), 'PARENT_SEGMENT_COUNT_CONFLICT')
        for parent, row in zip(parents, state['segments']):
            startup_checkpoint('ACCOUNT_VERSION_PARENT_PREFIX')
            dispatch, charge = row['dispatch'], row['charge']
            _require(parent.get('segment_number') == dispatch['segment_number']
                and all(parent.get(key) == dispatch[key] for key in ('profile_hash', 'upper_bound_seconds', 'dispatched_at')),
                'PARENT_DISPATCH_CONFLICT')
            _require(all(parent.get(key) == dispatch.get(key) for key in (
                'time_extension_proof', 'failed_account_recovery_proof', 'dispatch_identity')), 'PARENT_DISPATCH_PROOF_CONFLICT')
            if parent.get('failed_account_recovery_proof') is not None:
                from .run_budget import execution_dispatch_identity
                _require(parent.get('dispatch_identity') == execution_dispatch_identity(parent), 'PARENT_DISPATCH_IDENTITY_CONFLICT')
            if charge is not None:
                _require(all(parent.get(key) == charge[key] for key in (
                    'measured_seconds', 'seconds', 'basis', 'evidence_identity', 'outcome', 'charged_at')),
                    'PARENT_CHARGE_CONFLICT')
                _require(all(parent.get(key) == charge.get(key) for key in (
                    'time_extension_proof', 'failed_account_recovery_proof', 'dispatch_identity')), 'PARENT_CHARGE_PROOF_CONFLICT')
            else:
                _require('basis' not in parent and operation.get('active_segment') == parent,
                    'PARENT_CHARGE_CONFLICT')
        _require(operation.get('active_wall_seconds', 0) == state['charged_seconds'], 'PARENT_CHARGE_CONFLICT')

    @staticmethod
    def _reconcilable_parent_prefix(state, operation, count):
        """仅接受父端先提交、尚未镜像的一个段或最后一笔收费。"""
        parents = operation.get('segments', [])
        rows = state['segments']
        _require(len(rows) >= count and len(parents) in (len(rows), len(rows) + 1),
            'PARENT_SEGMENT_COUNT_CONFLICT')
        charged = 0.
        for index, parent in enumerate(parents):
            startup_checkpoint('ACCOUNT_VERSION_RECONCILE_PARENT_PREFIX')
            _require(parent.get('segment_number') == index + 1
                and parent.get('operation_id') == operation['operation_id']
                and parent.get('profile_hash') == state['profile']['profile_hash'], 'PARENT_DISPATCH_CONFLICT')
            if index < len(rows):
                dispatch, charge = rows[index]['dispatch'], rows[index]['charge']
                _require(all(parent.get(key) == dispatch[key] for key in (
                    'profile_hash', 'upper_bound_seconds', 'dispatched_at')), 'PARENT_DISPATCH_CONFLICT')
                _require(all(parent.get(key) == dispatch.get(key) for key in (
                    'time_extension_proof', 'failed_account_recovery_proof', 'dispatch_identity')), 'PARENT_DISPATCH_PROOF_CONFLICT')
                if charge is not None:
                    _require(all(parent.get(key) == charge[key] for key in (
                        'measured_seconds', 'seconds', 'basis', 'evidence_identity', 'outcome', 'charged_at')),
                        'PARENT_CHARGE_CONFLICT')
                    _require(all(parent.get(key) == charge.get(key) for key in (
                        'time_extension_proof', 'failed_account_recovery_proof', 'dispatch_identity')), 'PARENT_CHARGE_PROOF_CONFLICT')
                else:
                    _require(index >= count and index == len(parents) - 1, 'PARENT_CHARGE_CONFLICT')
            else:
                total = state['profile']['total_seconds']
                proof = parent.get('time_extension_proof')
                if proof is not None:
                    _require(proof == state.get('time_extension_proof')
                        and proof == operation.get('account_purpose_time_extension')
                        and state.get('effective_total_seconds') == operation.get('effective_total_seconds') == 72000,
                        'PARENT_DISPATCH_TIME_EXTENSION_CONFLICT')
                    total = state['effective_total_seconds']
                recovery_proof = parent.get('failed_account_recovery_proof')
                if recovery_proof is not None:
                    _require(recovery_proof == state.get('failed_account_recovery_proof')
                        and recovery_proof == operation.get('failed_account_recovery_proof'), 'PARENT_DISPATCH_RECOVERY_CONFLICT')
                _require(index >= count and state['pending'] is None
                    and type(parent.get('upper_bound_seconds')) in (int, float)
                    and 0 < parent['upper_bound_seconds'] <= min(state['profile']['worker_seconds'],
                        total - state['charged_seconds']), 'PARENT_DISPATCH_CONFLICT')
            if 'basis' in parent:
                from .universe_execution_profile_v1 import segment_charge
                seconds, basis = segment_charge(parent.get('measured_seconds'), parent['upper_bound_seconds'],
                    parent.get('evidence_identity'), outcome=parent.get('outcome'))
                _require(parent.get('seconds') == seconds and parent.get('basis') == basis, 'PARENT_CHARGE_CONFLICT')
                charged += seconds
            else:
                _require(index == len(parents) - 1 and operation.get('active_segment') == parent,
                    'PARENT_ACTIVE_SEGMENT_CONFLICT')
            if parent.get('failed_account_recovery_proof') is not None:
                from .run_budget import execution_dispatch_identity
                _require(parent.get('dispatch_identity') == execution_dispatch_identity(parent), 'PARENT_DISPATCH_IDENTITY_CONFLICT')
        _require(charged == operation.get('active_wall_seconds', 0)
            and (not parents or 'basis' not in parents[-1] or operation.get('active_segment') is None),
            'PARENT_CHARGE_CONFLICT')

    @classmethod
    def freeze(cls, root, summary, *, approval_ref):
        root = Path(root).resolve(strict=True)
        _require(isinstance(summary, dict) and summary.get('schema_version') == SUMMARY_VERSION, 'SUMMARY_INVALID')
        task_root = Path(summary['original']['task']['path']).parent
        campaign = ResearchCampaignV1(root, summary['authorization_id'])
        slot = campaign.directory / 'account-version-continuation-slots' / (summary['continuation_id'] + '.json')
        with ObjectiveMutationLock.for_resource(task_root / 'account' / 'LONG_EXECUTION.lock'):
            with ObjectiveMutationLock.for_resource(task_root / 'account' / 'CONTROL.lock'):
                _path(slot, root)
                if slot.exists():
                    record = _read(_reference(slot, root), root)
                    existing = cls(root, record['manifest_ref']).validate(for_dispatch=False)
                    _require(existing.summary_value == summary and existing.approval_ref == approval_ref, 'SLOT_CONFLICT')
                    return deepcopy(existing.scope_reference)
                current = cls.summary(root, authorization_id=summary['authorization_id'],
                    task_ref=summary['original']['task'], capability_bridge_ref=summary['capability_bridge_ref'],
                    proposal_ref=summary['proposal_ref'], ci_ref=summary['ci_ref'], session_refs=summary['session_refs'])
                _require(current == summary, 'SUMMARY_CHANGED')
                OwnerApprovalStoreV1(summary['approval_store']).require(approval_ref, summary)
                manifest = {'schema_version': VERSION, 'summary': deepcopy(summary), 'approval_ref': deepcopy(approval_ref)}
                manifest['identity'] = stable_hash(manifest)
                path = task_root / 'account-version-continuations' / summary['continuation_id'] / 'MANIFEST.json'
                _path(path, root)
                immutable(path, manifest)
                reference = _reference(path, root)
                immutable(slot, {'continuation_id': summary['continuation_id'], 'manifest_ref': reference})
                return reference

    @classmethod
    def from_job(cls, job, *, for_dispatch=False, for_reconcile=False):
        if not isinstance(job, dict) or not isinstance(job.get('root'), str):
            return None
        job_root = Path(job['root']); folder = job_root.parent / 'account-version-continuations'
        if not folder.exists():
            return None
        _path(folder, job_root.parent)
        paths = list(folder.glob('*/MANIFEST.json'))
        _require(len(paths) == 1, 'MANIFEST_DISCOVERY_CONFLICT')
        value = _read(_reference(paths[0], job_root.parent), job_root.parent)
        root = value.get('summary', {}).get('root')
        _require(isinstance(root, str), 'MANIFEST_INVALID')
        context = cls(root, _reference(paths[0], Path(root))).validate(
            for_dispatch=for_dispatch, for_reconcile=for_reconcile)
        _require(context.job_path == job_root / 'JOB.json' and _read(context.summary_value['original']['job'], context.root) == job,
            'JOB_CHANGED')
        return context

    def validate(self, *, for_dispatch=False, name=None, segment_number=None, for_reconcile=False):
        startup_checkpoint('ACCOUNT_VERSION_CONTEXT')
        _require(type(for_dispatch) is bool and type(for_reconcile) is bool
            and not (for_dispatch and for_reconcile), 'DISPATCH_MODE_INVALID')
        manifest = _read(self.scope_reference, self.root)
        _require(set(manifest) == {'schema_version', 'summary', 'approval_ref', 'identity'}
            and manifest['schema_version'] == VERSION and _identity(manifest), 'MANIFEST_INVALID')
        summary = manifest['summary']
        _require(summary.get('schema_version') == SUMMARY_VERSION
            and summary.get('version_id') == stable_hash({k: v for k, v in summary.items() if k not in ('version_id', 'active_plans')})
            and summary.get('maximum_version_switches') == 1 and summary.get('daily_commit_count_at_install') == 0
            and summary.get('grant_delta') == summary.get('new_account_jobs') == summary.get('new_preparation_jobs') == 0,
            'SUMMARY_INVALID')
        self.summary_value = deepcopy(summary); self.summary = deepcopy(summary)
        self.approval_ref = deepcopy(manifest['approval_ref']); self.version_id = summary['version_id']
        self.task_root = Path(summary['original']['task']['path']).parent
        self.job_path = Path(summary['original']['job']['path'])
        self.campaign = ResearchCampaignV1(self.root, summary['authorization_id'])
        _require(summary['root'] == str(self.root) and self.job_path == self.task_root / 'account' / 'JOB.json'
            and summary['continuation_id'] == self.continuation_identity(summary['authorization_id'], summary['task_id'])
            and Path(self.scope_reference['path']) == self.task_root / 'account-version-continuations'
                / summary['continuation_id'] / 'MANIFEST.json', 'MANIFEST_PATH_CONFLICT')
        slot = self.campaign.directory / 'account-version-continuation-slots' / (summary['continuation_id'] + '.json')
        _require(_read(_reference(slot, self.root), self.root) == {
            'continuation_id': summary['continuation_id'], 'manifest_ref': self.scope_reference}, 'SLOT_CONFLICT')
        OwnerApprovalStoreV1(summary['approval_store']).require(self.approval_ref, summary)
        self.job = _read(summary['original']['job'], self.root)
        from .failed_account_recovery_v1 import FailedAccountRecoveryV1
        self.recovery = FailedAccountRecoveryV1.from_job(self.job)
        from .account_purpose_time_extension_v1 import AccountPurposeTimeExtensionV1
        self.time_extension = AccountPurposeTimeExtensionV1.from_job(self.job, recovery=self.recovery)
        if self.time_extension is not None:
            _require(self.time_extension.summary_value['predecessor_manifest_ref'] == self.scope_reference,
                'TIME_EXTENSION_PREDECESSOR_CONFLICT')
        view = self.campaign.peek_status(); base = view['base_authorization']
        _require(stable_hash(base) == summary['base_authorization_identity']
            and base['scope_policy']['scope_hash'] == summary['scope_hash']
            and base['scope_policy']['approval_store'] == summary['approval_store'], 'AUTHORIZATION_CONFLICT')
        bridge = (AccountVersionCapabilityBridgeV1.read_historical(self.root, base,
            reference=summary['capability_bridge_ref'], time_extension_ref=self.time_extension.scope_reference, recovery=self.recovery)
            if self.time_extension is not None else AccountVersionCapabilityBridgeV1.read(
                self.root, base, reference=summary['capability_bridge_ref']))
        _require(summary['source_replacements'] == bridge['record']['summary']['source_replacements']
            and summary['runtime_sources'] == bridge['record']['summary']['runtime_sources'], 'BRIDGE_CONFLICT')
        original = summary['original']
        refs = [original[key] for key in ('task', 'job', 'preview', 'receipt')] + original['control']
        refs += [summary['proposal_ref'], summary['ci_ref']]
        for row in summary['accounts'].values():
            refs.extend(row['segment_refs'])
            if row['start'] is not None:
                refs.append(row['start'])
        for reference in refs:
            startup_checkpoint('ACCOUNT_VERSION_ORIGINAL_REFERENCES')
            _require(reference == _reference(reference['path'], self.root), 'ORIGINAL_CHANGED')
        for reference in summary['session_refs'].values():
            _session_reference(reference, self.root, summary['approval_store'])
        self.job = _read(original['job'], self.root)
        self._source_check(self.root, self.job, summary['source_replacements'], time_extension=self.time_extension, recovery=self.recovery)
        replacements = {row['path']: row for row in summary['source_replacements']}
        for member, plan in self.job['plans'].items():
            active = _project(plan, replacements)
            active['plan_id'] = stable_hash({k: v for k, v in active.items() if k != 'plan_id'})
            _require(summary['active_plans'].get(member) == active, 'ACTIVE_PLAN_CONFLICT')
        _require(set(summary['active_plans']) == set(self.job['plans']) == set(summary['accounts']), 'ACCOUNTS_CONFLICT')
        control = _control(self.job)
        _require(len(control['events']) >= len(original['control'])
            and (not original['control'] or control['events'][len(original['control']) - 1]['event_id'] == original['control_head']),
            'CONTROL_PREFIX_CHANGED')
        from .etf_account_governance_v1 import StrategyBatchGovernanceV1
        self.governance = StrategyBatchGovernanceV1(self.job['root'], self.job['budget_path'],
            self.job['objective_id'], self.job['plans'])
        self.time_extension_installed = False
        self.time_extension_mirror_pending = False
        self.recovery_installed = False
        self.recovery_mirror_pending = False
        if self.time_extension is not None:
            expected = self.time_extension.proof()
            operations = [view['operations'].get(row['operation_id'], {})
                for row in self.time_extension.summary_value['accounts'].values()]
            proofs = [operation.get('account_purpose_time_extension') for operation in operations]
            if any(proof is not None for proof in proofs):
                _require(all(proof == expected for proof in proofs)
                    and all(operation.get('effective_total_seconds') == 72000
                        and operation.get('effective_upper_bounds', {}).get('wall_seconds') == 72000
                        for operation in operations), 'TIME_EXTENSION_INSTALLATION_CONFLICT')
                self.time_extension_installed = True
        if self.recovery is not None:
            expected = self.recovery.proof(); accounts = self.recovery.summary_value['accounts']
            operations = [view['operations'].get(row['operation_id'], {}) for row in accounts.values()]
            proofs = [operation.get('failed_account_recovery_proof') for operation in operations]
            if any(proof is not None for proof in proofs):
                failed = view['operations'].get(accounts[self.recovery.original_failed_account]['operation_id'], {})
                installed = failed.get('failed_account_recovery', {})
                _require(self.time_extension_installed and all(proof == expected for proof in proofs)
                    and all(operation.get('recovery_boundary') == accounts[member]
                        for member, operation in zip(accounts, operations))
                    and installed.get('proof') == expected
                    and installed.get('boundary') == accounts[self.recovery.original_failed_account], 'RECOVERY_INSTALLATION_CONFLICT')
                self.recovery_installed = True
        for member, frozen in summary['accounts'].items():
            startup_checkpoint('ACCOUNT_VERSION_ACCOUNT_PREFIX')
            op = view['operations'].get(frozen['operation_id'], {})
            _require(all(op.get(key) == frozen['operation'].get(key) for key in (
                'operation_id', 'kind', 'stage', 'batch_id', 'subject_identity', 'upper_bounds', 'execution_profile',
                'cost_bound_evidence', 'hypothesis_identity')), 'ORIGINAL_OPERATION_CONFLICT')
            start_path = Path(self.job['root']) / (member + '_START.json')
            if start_path.exists():
                state = (self.governance.segment_status_for_reconcile(member) if for_reconcile
                    else self.governance.segment_status(member))
                self.time_extension_mirror_pending |= state.get('time_extension_mirror_pending', False)
                self.recovery_mirror_pending |= state.get('failed_account_recovery_mirror_pending', False)
                _require(len(state['segments']) >= frozen['segment_count']
                    and state['charged_seconds'] >= frozen['charged_seconds'], 'SEGMENT_PREFIX_CHANGED')
                if for_reconcile:
                    self._reconcilable_parent_prefix(state, op, frozen['segment_count'])
                else:
                    self._parent_prefix(state, op, frozen['segment_count'])
            else:
                _require(frozen['start'] is None and op['status'] == 'RESERVED', 'START_PREFIX_CHANGED')
            if frozen['features'] is not None:
                _feature_prefix(self.job, member, frozen=frozen['features'])
        if for_dispatch:
            _require(self.time_extension is None or self.time_extension_installed, 'TIME_EXTENSION_NOT_INSTALLED')
            _require(self.recovery is None or self.recovery_installed, 'RECOVERY_NOT_INSTALLED')
            _require(not self.recovery_mirror_pending, 'RECOVERY_MIRROR_REQUIRED')
            if self.recovery is not None:
                self.recovery = FailedAccountRecoveryV1.read(self.root, self.recovery.scope_reference, for_dispatch=True)
            deadline = self.recovery.execution_expires_at if self.recovery is not None else summary['expires_at']
            selected = name or (self.recovery.original_failed_account if self.recovery is not None else None)
            operation = (view['operations'][summary['accounts'][selected]['operation_id']] if selected is not None else None)
            self.campaign._dispatchable(view, 'EXPLORATION', operation=operation)
            _require(not view.get('revoked') and datetime.now(timezone.utc) < datetime.fromisoformat(deadline), 'EXPIRED')
            from .run_budget import effective_execution_status
            _require(not any(effective_execution_status(op) == 'UNKNOWN' for op in view['operations'].values()), 'UNKNOWN_OPERATION')
            if name is not None:
                _require(not self.execution_paused(control), 'PAUSED')
                _require(name in summary['accounts'], 'PURPOSE_CONFLICT')
                frozen = summary['accounts'][name]; op = view['operations'][frozen['operation_id']]
                _require(effective_execution_status(op) in ('RUNNING', 'RESERVED'), 'ACCOUNT_TERMINAL')
                if (Path(self.job['root']) / (name + '_START.json')).exists():
                    state = self.governance.segment_status(name)
                    if segment_number is not None:
                        _require(state['pending'] is not None and state['pending']['segment_number'] == segment_number,
                            'DISPATCH_CONFLICT')
                    else:
                        _require(state['pending'] is None, 'ACTIVE_SEGMENT')
                        from .research_campaign_v1 import effective_segment_allowance
                        effective_segment_allowance(state['profile'], state['charged_seconds'],
                            effective_total_seconds=state.get('effective_total_seconds'),
                            time_extension_proof=state.get('time_extension_proof'), seconds_to_expiry=(
                                datetime.fromisoformat(deadline) - datetime.now(timezone.utc)).total_seconds())
                else:
                    _require(segment_number is None, 'DISPATCH_CONFLICT')
        return self

    def validate_sources(self, job):
        self.validate(for_dispatch=False)
        _require(job == self.job, 'JOB_CHANGED')
        return True

    def active_item(self, name):
        return self.active_plan(name)['runtime']

    def active_plan(self, name):
        _require(name in self.summary_value['active_plans'], 'PURPOSE_CONFLICT')
        if self.recovery is not None:
            _require(self.recovery_installed, 'RECOVERY_NOT_INSTALLED')
            _require(not self.time_extension_mirror_pending, 'TIME_EXTENSION_MIRROR_REQUIRED')
            _require(not self.recovery_mirror_pending, 'RECOVERY_MIRROR_REQUIRED')
            return self.recovery.successor_plan(name)
        if self.time_extension is not None and self.time_extension_installed:
            _require(not self.time_extension_mirror_pending, 'TIME_EXTENSION_MIRROR_REQUIRED')
            return self.time_extension.successor_plan(name)
        return deepcopy(self.summary_value['active_plans'][name])

    def execution_paused(self, control=None):
        """新能力只消费已批准的历史暂停head；之后的新暂停仍立即生效。"""
        current = _control(self.job)
        _require(control is None or control == current, 'CONTROL_VIEW_CHANGED')
        if not self.recovery_installed:
            return current['paused']
        from .failed_account_recovery_v1 import FailedAccountRecoveryV1
        _require(type(self.recovery) is FailedAccountRecoveryV1, 'SIGNED_RECOVERY_CONTEXT_REQUIRED')
        fresh = FailedAccountRecoveryV1.read(self.root, self.recovery.scope_reference)
        boundary = fresh.summary_value['control_boundary']
        if current['head'] == boundary['head'] and boundary['consumes_historical_pause']:
            return False
        return current['paused']

    def project_receipt(self, receipt, name):
        _require(receipt.get('strategy_plans') == self.job['plans'] and receipt.get('execution_purpose') == name
            and receipt.get('execution_consumed') is True and receipt.get('input_identity') == self.job['input_identity'],
            'ORIGINAL_RECEIPT_REQUIRED')
        _require(name in self.summary_value['active_plans'], 'PURPOSE_CONFLICT')
        value = deepcopy(receipt)
        value['strategy_plans'] = {member: self.active_plan(member) for member in self.summary_value['active_plans']}
        for member, plan in value['strategy_plans'].items():
            value['novelty'][member]['plan_id'] = plan['plan_id']
        value['account_version_continuation'] = self.proof(name)
        if self.recovery is not None and self.recovery_installed:
            value['expires_at'] = self.recovery.execution_expires_at
        return value

    def _predecessor_proof(self, name=None):
        if name is not None:
            _require(name in self.summary_value['active_plans'], 'PURPOSE_CONFLICT')
        return {'manifest_ref': deepcopy(self.scope_reference), 'version_id': self.version_id,
            'original_job_sha256': self.summary_value['original']['job']['sha256'],
            'active_plan_ids': {member: plan['plan_id'] for member, plan in self.summary_value['active_plans'].items()},
            **({'purpose': name} if name is not None else {})}

    def _time_extension_proof(self, name=None):
        value = self._predecessor_proof(name)
        if self.time_extension is not None and self.time_extension_installed:
            value['predecessor_active_plan_ids'] = value['active_plan_ids']
            value['active_plan_ids'] = {member: self.time_extension.successor_plan(member)['plan_id']
                for member in self.summary_value['active_plans']}
            value['account_purpose_time_extension'] = self.time_extension.proof()
        return value

    def proof(self, name=None):
        value = self._time_extension_proof(name)
        if self.recovery is not None and self.recovery_installed:
            value['predecessor_recovery_active_plan_ids'] = value['active_plan_ids']
            value['active_plan_ids'] = {member: self.recovery.successor_plan(member)['plan_id']
                for member in self.summary_value['active_plans']}
            value['failed_account_recovery'] = self.recovery.proof()
        return value

    def approved_source_replacements(self):
        """旧会话pin仍指原字节；三层有限映射串接后核最终源码。"""
        mapping = {row['path']: deepcopy(row) for row in self.summary_value['source_replacements']}
        if self.time_extension is not None:
            for row in self.time_extension.summary_value['source_replacements']:
                previous = mapping.get(row['path'])
                if previous is None:
                    mapping[row['path']] = deepcopy(row)
                else:
                    _require(previous['new_sha256'] == row['old_sha256'], 'TIME_EXTENSION_SOURCE_CHAIN_CONFLICT')
                    previous['new_sha256'] = row['new_sha256']
        if self.recovery is not None:
            for row in self.recovery.summary_value['source_replacements']:
                previous = mapping.get(row['path'])
                if previous is None:
                    mapping[row['path']] = deepcopy(row)
                else:
                    _require(previous['new_sha256'] == row['old_sha256'], 'RECOVERY_SOURCE_CHAIN_CONFLICT')
                    previous['new_sha256'] = row['new_sha256']
        return list(mapping.values())

    def approved_runtime_sources(self):
        mapping = {row['path']: deepcopy(row) for row in self.summary_value['runtime_sources']}
        if self.time_extension is not None:
            mapping.update({row['path']: deepcopy(row) for row in self.time_extension.summary_value['runtime_sources']})
        if self.recovery is not None:
            mapping.update({row['path']: deepcopy(row) for row in self.recovery.summary_value['runtime_sources']})
        return list(mapping.values())

    def _dispatch_path(self, name, number):
        return Path(self.job['root']) / (name + '_SEGMENT_' + str(number).zfill(6) + '_EXECUTION_VERSION.json')

    def _authority_ledger_reference(self):
        return _reference(self.campaign.budget_path.with_name('run_budget_events.jsonl'), self.root)

    def _require_aggregate_authority(self, reference):
        _require(reference == self._authority_ledger_reference(), 'AGGREGATE_AUTHORITY_CHANGED')

    def _validate_aggregate_prewrite(self, authority):
        self._require_aggregate_authority(authority)
        self.validate(for_reconcile=True)
        self._require_aggregate_authority(authority)

    def mirrors_pending(self):
        """已证明合法的父子镜像或未启动版本侧车差异，不吞掉其他校验错误。"""
        self.validate(for_reconcile=True)
        aggregate = self.time_extension is not None and self.time_extension_installed
        authority = self._authority_ledger_reference() if aggregate else None
        root = Path(self.job['root']); pending = False
        operations = self.campaign.peek_status()['operations']
        if aggregate:
            self._require_aggregate_authority(authority)
        for name, frozen in self.summary_value['accounts'].items():
            if not (root / (name + '_START.json')).exists():
                continue
            state = self.governance.segment_status_for_reconcile(name)
            if aggregate:
                self._require_aggregate_authority(authority)
            pending |= state.get('time_extension_mirror_pending', False)
            pending |= state.get('failed_account_recovery_mirror_pending', False)
            parents = operations[frozen['operation_id']].get('segments', [])
            pending |= len(parents) != len(state['segments']) or operations[frozen['operation_id']].get(
                'active_wall_seconds', 0) != state['charged_seconds']
            pending |= bool(state['pending'] is not None and parents and 'basis' in parents[-1])
            for parent in parents[frozen['segment_count']:]:
                number = parent['segment_number']
                if self._dispatch_path(name, number).exists():
                    _require(number <= len(state['segments']), 'DISPATCH_VERSION_WITHOUT_LOCAL_DISPATCH')
                    if aggregate:
                        self._validate_dispatch_from_state(name, number, state)
                    else:
                        self.validate_dispatch(name, number, for_reconcile=True)
                else:
                    prefix = name + '_SEGMENT_' + str(number).zfill(6)
                    _require(not (root / (prefix + '_WORKER.json')).exists()
                        and not (root / (prefix + '_INPUT_ACCESS.json')).exists(),
                        'DISPATCH_VERSION_WORKER_ALREADY_PRESENT')
                    pending = True
        if aggregate:
            self._require_aggregate_authority(authority)
            self.validate(for_reconcile=True)
            self._require_aggregate_authority(authority)
        return pending

    def reconcile_segment_mirrors(self):
        """只补原父事件的本地镜像及未启动段版本证明，不派发、不收费。"""
        self.validate(for_reconcile=True)
        aggregate = self.time_extension is not None and self.time_extension_installed
        authority = self._authority_ledger_reference() if aggregate else None
        root = Path(self.job['root'])
        with ObjectiveMutationLock.for_resource(root / 'LONG_EXECUTION.lock'):
            with ObjectiveMutationLock.for_resource(root / 'RESUME.lock'):
                self.validate(for_reconcile=True)
                if aggregate:
                    self._require_aggregate_authority(authority)
                for name in self.job['plans']:
                    if not (root / (name + '_START.json')).exists():
                        continue
                    frozen = self.summary_value['accounts'][name]
                    operation = self.campaign.peek_status()['operations'][frozen['operation_id']]
                    if aggregate:
                        self._require_aggregate_authority(authority)
                    for parent in operation.get('segments', [])[frozen['segment_count']:]:
                        number = parent['segment_number']
                        if not self._dispatch_path(name, number).exists():
                            prefix = name + '_SEGMENT_' + str(number).zfill(6)
                            _require(not (root / (prefix + '_WORKER.json')).exists()
                                and not (root / (prefix + '_INPUT_ACCESS.json')).exists(),
                                'DISPATCH_VERSION_WORKER_ALREADY_PRESENT')
                    if aggregate:
                        state = self.governance.reconcile_segment_mirrors(name,
                            _before_immutable=lambda: self._validate_aggregate_prewrite(authority))
                    else:
                        state = self.governance.reconcile_segment_mirrors(name)
                    if aggregate:
                        self._require_aggregate_authority(authority)
                    for row in state['segments'][frozen['segment_count']:]:
                        dispatch = row['dispatch']; number = dispatch['segment_number']
                        if self._dispatch_path(name, number).exists():
                            if aggregate:
                                self._validate_dispatch_from_state(name, number, state)
                            else:
                                self.validate_dispatch(name, number)
                            continue
                        prefix = name + '_SEGMENT_' + str(number).zfill(6)
                        marker = _path(root / (prefix + '_MIRROR_REPAIR.json'), self.root)
                        parent = operation['segments'][number - 1]
                        expected = {'version': 'CAMPAIGN_SEGMENT_MIRROR_REPAIR_V1',
                            'receipt_id': dispatch['receipt_id'], 'source_identity': stable_hash(
                                _read(self.summary_value['original']['receipt'], self.root)['source']),
                            'operation_id': frozen['operation_id'], 'dispatch_id': dispatch['dispatch_id'],
                            'parent_dispatch_identity': stable_hash({key: parent[key] for key in (
                                'operation_id', 'segment_number', 'profile_hash', 'upper_bound_seconds', 'dispatched_at')
                                + (('time_extension_proof',) if parent.get('time_extension_proof') is not None else ())
                                + (('failed_account_recovery_proof',) if parent.get('failed_account_recovery_proof') is not None else ())}),
                            'worker_absent': True}
                        if parent.get('failed_account_recovery_proof') is not None:
                            expected.update(failed_account_recovery_proof=deepcopy(parent['failed_account_recovery_proof']),
                                dispatch_identity=parent['dispatch_identity'])
                        _require(not (root / (prefix + '_WORKER.json')).exists()
                            and not (root / (prefix + '_INPUT_ACCESS.json')).exists(),
                            'DISPATCH_VERSION_WORKER_ALREADY_PRESENT')
                        if not marker.exists():
                            # 原 DISPATCH 已写、版本侧车尚未写时崩溃；无侧车不能合法启动 reader。
                            _require(row['charge'] is None and state['pending'] == dispatch
                                and operation.get('active_segment') == parent,
                                'DISPATCH_VERSION_NO_WORKER_PROOF_REQUIRED')
                            if aggregate:
                                self._validate_aggregate_prewrite(authority)
                            immutable(marker, expected)
                        _require(_read(_reference(marker, self.root), self.root) == expected,
                            'DISPATCH_VERSION_NO_WORKER_PROOF_REQUIRED')
                        path = _path(self._dispatch_path(name, number), self.root)
                        if aggregate:
                            self._validate_aggregate_prewrite(authority)
                        value = {
                            'schema_version': 'ACCOUNT_VERSION_SEGMENT_V1', 'dispatch_id': dispatch['dispatch_id'],
                            'segment_number': number, 'account_version_continuation': self.segment_proof(name, number)}
                        if dispatch.get('failed_account_recovery_proof') is not None:
                            value.update(failed_account_recovery_proof=deepcopy(dispatch['failed_account_recovery_proof']),
                                dispatch_identity=dispatch['dispatch_identity'])
                        immutable(path, value)
                if aggregate:
                    self._require_aggregate_authority(authority)
                result = self.validate()
                if aggregate:
                    self._require_aggregate_authority(authority)
                return result

    def segment_proof(self, name, number):
        _require(name in self.summary_value['accounts'] and type(number) is int and number > 0, 'DISPATCH_CONFLICT')
        if number <= self.summary_value['accounts'][name]['segment_count']:
            return None
        if (self.time_extension is not None and self.time_extension_installed
                and number <= self.time_extension.summary_value['accounts'][name]['segment_count']):
            return self._predecessor_proof(name)
        if (self.recovery is not None and self.recovery_installed
                and number <= self.recovery.summary_value['accounts'][name]['segment_count']):
            return self._time_extension_proof(name)
        return self.proof(name)

    def record_dispatch(self, name, dispatch):
        self.validate(for_dispatch=True, name=name, segment_number=dispatch.get('segment_number'))
        state = self.governance.segment_status(name)
        _require(state['pending'] == dispatch and dispatch['segment_number'] > self.summary_value['accounts'][name]['segment_count'],
            'DISPATCH_CONFLICT')
        value = {'schema_version': 'ACCOUNT_VERSION_SEGMENT_V1', 'dispatch_id': dispatch['dispatch_id'],
            'segment_number': dispatch['segment_number'], 'account_version_continuation': self.proof(name)}
        if dispatch.get('failed_account_recovery_proof') is not None:
            value.update(failed_account_recovery_proof=deepcopy(dispatch['failed_account_recovery_proof']),
                dispatch_identity=dispatch['dispatch_identity'])
        immutable(_path(self._dispatch_path(name, dispatch['segment_number']), self.root), value)
        return value

    def validate_dispatch(self, name, number, *, for_reconcile=False):
        if not hasattr(self, 'summary_value'):
            self.validate(for_dispatch=False)
        _require(name in self.summary_value['accounts'] and type(number) is int and number > 0, 'DISPATCH_CONFLICT')
        state = (self.governance.segment_status_for_reconcile(name) if for_reconcile
            else self.governance.segment_status(name))
        return self._validate_dispatch_from_state(name, number, state)

    def _validate_dispatch_from_state(self, name, number, state):
        """单次聚合内的完整逐段检查；调用方负责 fresh 状态及权威事件流一致。"""
        _require(name in self.summary_value['accounts'] and type(number) is int and number > 0, 'DISPATCH_CONFLICT')
        _require(number <= len(state['segments']), 'DISPATCH_CONFLICT')
        if self.segment_proof(name, number) is None:
            for reference in self.summary_value['accounts'][name]['segment_refs']:
                if Path(reference['path']).name.startswith(name + '_SEGMENT_' + str(number).zfill(6) + '_'):
                    _require(reference == _reference(reference['path'], self.root), 'ORIGINAL_CHANGED')
            return None
        dispatch = state['segments'][number - 1]['dispatch']
        expected = {'schema_version': 'ACCOUNT_VERSION_SEGMENT_V1', 'dispatch_id': dispatch['dispatch_id'],
            'segment_number': number, 'account_version_continuation': self.segment_proof(name, number)}
        recovery_proof = expected['account_version_continuation'].get('failed_account_recovery')
        _require(dispatch.get('failed_account_recovery_proof') == recovery_proof, 'DISPATCH_RECOVERY_CONFLICT')
        if recovery_proof is not None:
            expected.update(failed_account_recovery_proof=deepcopy(recovery_proof), dispatch_identity=dispatch['dispatch_identity'])
        _require(_read(_reference(self._dispatch_path(name, number), self.root), self.root) == expected, 'DISPATCH_VERSION_CONFLICT')
        time_proof = expected['account_version_continuation'].get('account_purpose_time_extension')
        _require(dispatch.get('time_extension_proof') == time_proof, 'DISPATCH_TIME_EXTENSION_CONFLICT')
        charge = state['segments'][number - 1]['charge']
        _require(charge is None or charge.get('time_extension_proof') == time_proof, 'CHARGE_TIME_EXTENSION_CONFLICT')
        _require(charge is None or charge.get('failed_account_recovery_proof') == recovery_proof, 'CHARGE_RECOVERY_CONFLICT')
        if recovery_proof is not None:
            _require(charge is None or charge.get('dispatch_identity') == dispatch['dispatch_identity'], 'CHARGE_DISPATCH_IDENTITY_CONFLICT')
        if time_proof is not None:
            prefix = name + '_SEGMENT_' + str(number).zfill(6)
            for suffix in ('_RESOURCE.json', '_WORKER.json', '_INPUT_ACCESS.json', '_STATUS.json',
                    '_FAILURE.json', '_RESUME.json'):
                path = Path(self.job['root']) / (prefix + suffix)
                if path.exists():
                    value = _read(_reference(path, self.root), self.root)
                    _require(value.get('time_extension_proof') == time_proof, 'SEGMENT_TIME_EXTENSION_CONFLICT')
                    _require(value.get('failed_account_recovery_proof') == recovery_proof, 'SEGMENT_RECOVERY_CONFLICT')
                    if recovery_proof is not None:
                        _require(value.get('dispatch_identity') == dispatch['dispatch_identity'], 'SEGMENT_DISPATCH_IDENTITY_CONFLICT')
                    if suffix not in ('_FAILURE.json', '_RESUME.json'):
                        _require(value.get('account_version_continuation') == expected['account_version_continuation'],
                            'SEGMENT_SUCCESSOR_VERSION_CONFLICT')
        return expected
