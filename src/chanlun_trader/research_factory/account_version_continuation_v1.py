"""零账户日边界的一次版本接续；账户用途、原件及累计计费保持原身份。"""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import re

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

    @staticmethod
    def continuation_identity(authorization_id, task_id):
        return stable_hash({'schema_version': VERSION, 'authorization_id': authorization_id, 'task_id': task_id})

    @staticmethod
    def _source_check(root, job, replacements):
        mapping = {row['path']: row for row in replacements}
        _require(len(mapping) == len(replacements), 'SOURCE_REPLACEMENTS_DUPLICATED')
        _require(isinstance(job.get('source_hashes'), dict) and job['source_hashes'], 'FROZEN_SOURCES_REQUIRED')
        for path, digest in job['source_hashes'].items():
            source = _path(path, root)
            row = mapping.get(path)
            _require(row is None or row['old_sha256'] == digest, 'ORIGINAL_SOURCE_CONFLICT')
            _require(_reference(source, root)['sha256'] == (row['new_sha256'] if row else digest),
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
            dispatch, charge = row['dispatch'], row['charge']
            _require(parent.get('segment_number') == dispatch['segment_number']
                and all(parent.get(key) == dispatch[key] for key in ('profile_hash', 'upper_bound_seconds', 'dispatched_at')),
                'PARENT_DISPATCH_CONFLICT')
            if charge is not None:
                _require(all(parent.get(key) == charge[key] for key in (
                    'measured_seconds', 'seconds', 'basis', 'evidence_identity', 'outcome', 'charged_at')),
                    'PARENT_CHARGE_CONFLICT')
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
            _require(parent.get('segment_number') == index + 1
                and parent.get('operation_id') == operation['operation_id']
                and parent.get('profile_hash') == state['profile']['profile_hash'], 'PARENT_DISPATCH_CONFLICT')
            if index < len(rows):
                dispatch, charge = rows[index]['dispatch'], rows[index]['charge']
                _require(all(parent.get(key) == dispatch[key] for key in (
                    'profile_hash', 'upper_bound_seconds', 'dispatched_at')), 'PARENT_DISPATCH_CONFLICT')
                if charge is not None:
                    _require(all(parent.get(key) == charge[key] for key in (
                        'measured_seconds', 'seconds', 'basis', 'evidence_identity', 'outcome', 'charged_at')),
                        'PARENT_CHARGE_CONFLICT')
                else:
                    _require(index >= count and index == len(parents) - 1, 'PARENT_CHARGE_CONFLICT')
            else:
                _require(index >= count and state['pending'] is None
                    and type(parent.get('upper_bound_seconds')) in (int, float)
                    and 0 < parent['upper_bound_seconds'] <= min(state['profile']['worker_seconds'],
                        state['profile']['total_seconds'] - state['charged_seconds']), 'PARENT_DISPATCH_CONFLICT')
            if 'basis' in parent:
                from .universe_execution_profile_v1 import segment_charge
                seconds, basis = segment_charge(parent.get('measured_seconds'), parent['upper_bound_seconds'],
                    parent.get('evidence_identity'), outcome=parent.get('outcome'))
                _require(parent.get('seconds') == seconds and parent.get('basis') == basis, 'PARENT_CHARGE_CONFLICT')
                charged += seconds
            else:
                _require(index == len(parents) - 1 and operation.get('active_segment') == parent,
                    'PARENT_ACTIVE_SEGMENT_CONFLICT')
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
        view = self.campaign.peek_status(); base = view['base_authorization']
        _require(stable_hash(base) == summary['base_authorization_identity']
            and base['scope_policy']['scope_hash'] == summary['scope_hash']
            and base['scope_policy']['approval_store'] == summary['approval_store'], 'AUTHORIZATION_CONFLICT')
        bridge = AccountVersionCapabilityBridgeV1.read(self.root, base, reference=summary['capability_bridge_ref'])
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
            _require(reference == _reference(reference['path'], self.root), 'ORIGINAL_CHANGED')
        for reference in summary['session_refs'].values():
            _session_reference(reference, self.root, summary['approval_store'])
        self.job = _read(original['job'], self.root)
        self._source_check(self.root, self.job, summary['source_replacements'])
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
        for member, frozen in summary['accounts'].items():
            op = view['operations'].get(frozen['operation_id'], {})
            _require(all(op.get(key) == frozen['operation'].get(key) for key in (
                'operation_id', 'kind', 'stage', 'batch_id', 'subject_identity', 'upper_bounds', 'execution_profile',
                'cost_bound_evidence', 'hypothesis_identity')), 'ORIGINAL_OPERATION_CONFLICT')
            start_path = Path(self.job['root']) / (member + '_START.json')
            if start_path.exists():
                state = self.governance.segment_status(member)
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
            self.campaign._dispatchable(view, 'EXPLORATION')
            _require(not view.get('revoked') and datetime.now(timezone.utc) < datetime.fromisoformat(summary['expires_at']), 'EXPIRED')
            _require(not any(op.get('status') == 'UNKNOWN' for op in view['operations'].values()), 'UNKNOWN_OPERATION')
            if name is not None:
                _require(not control['paused'], 'PAUSED')
                _require(name in summary['accounts'], 'PURPOSE_CONFLICT')
                frozen = summary['accounts'][name]; op = view['operations'][frozen['operation_id']]
                _require(op['status'] in ('RUNNING', 'RESERVED'), 'ACCOUNT_TERMINAL')
                if (Path(self.job['root']) / (name + '_START.json')).exists():
                    state = self.governance.segment_status(name)
                    if segment_number is not None:
                        _require(state['pending'] is not None and state['pending']['segment_number'] == segment_number,
                            'DISPATCH_CONFLICT')
                    else:
                        _require(state['pending'] is None, 'ACTIVE_SEGMENT')
                        segment_allowance(state['profile'], state['charged_seconds'], seconds_to_expiry=(
                            datetime.fromisoformat(summary['expires_at']) - datetime.now(timezone.utc)).total_seconds())
                else:
                    _require(segment_number is None, 'DISPATCH_CONFLICT')
        return self

    def validate_sources(self, job):
        self.validate(for_dispatch=False)
        _require(job == self.job, 'JOB_CHANGED')
        return True

    def active_item(self, name):
        _require(name in self.summary_value['active_plans'], 'PURPOSE_CONFLICT')
        return deepcopy(self.summary_value['active_plans'][name]['runtime'])

    def active_plan(self, name):
        _require(name in self.summary_value['active_plans'], 'PURPOSE_CONFLICT')
        return deepcopy(self.summary_value['active_plans'][name])

    def project_receipt(self, receipt, name):
        _require(receipt.get('strategy_plans') == self.job['plans'] and receipt.get('execution_purpose') == name
            and receipt.get('execution_consumed') is True and receipt.get('input_identity') == self.job['input_identity'],
            'ORIGINAL_RECEIPT_REQUIRED')
        _require(name in self.summary_value['active_plans'], 'PURPOSE_CONFLICT')
        value = deepcopy(receipt)
        value['strategy_plans'] = deepcopy(self.summary_value['active_plans'])
        for member, plan in value['strategy_plans'].items():
            value['novelty'][member]['plan_id'] = plan['plan_id']
        value['account_version_continuation'] = self.proof(name)
        return value

    def proof(self, name=None):
        if name is not None:
            _require(name in self.summary_value['active_plans'], 'PURPOSE_CONFLICT')
        return {'manifest_ref': deepcopy(self.scope_reference), 'version_id': self.version_id,
            'original_job_sha256': self.summary_value['original']['job']['sha256'],
            'active_plan_ids': {member: plan['plan_id'] for member, plan in self.summary_value['active_plans'].items()},
            **({'purpose': name} if name is not None else {})}

    def _dispatch_path(self, name, number):
        return Path(self.job['root']) / (name + '_SEGMENT_' + str(number).zfill(6) + '_EXECUTION_VERSION.json')

    def mirrors_pending(self):
        """已证明合法的父子镜像或未启动版本侧车差异，不吞掉其他校验错误。"""
        self.validate(for_reconcile=True)
        root = Path(self.job['root']); pending = False
        operations = self.campaign.peek_status()['operations']
        for name, frozen in self.summary_value['accounts'].items():
            if not (root / (name + '_START.json')).exists():
                continue
            state = self.governance.segment_status(name)
            parents = operations[frozen['operation_id']].get('segments', [])
            pending |= len(parents) != len(state['segments']) or operations[frozen['operation_id']].get(
                'active_wall_seconds', 0) != state['charged_seconds']
            pending |= bool(state['pending'] is not None and parents and 'basis' in parents[-1])
            for parent in parents[frozen['segment_count']:]:
                number = parent['segment_number']
                if self._dispatch_path(name, number).exists():
                    _require(number <= len(state['segments']), 'DISPATCH_VERSION_WITHOUT_LOCAL_DISPATCH')
                    self.validate_dispatch(name, number)
                else:
                    prefix = name + '_SEGMENT_' + str(number).zfill(6)
                    _require(not (root / (prefix + '_WORKER.json')).exists()
                        and not (root / (prefix + '_INPUT_ACCESS.json')).exists(),
                        'DISPATCH_VERSION_WORKER_ALREADY_PRESENT')
                    pending = True
        return pending

    def reconcile_segment_mirrors(self):
        """只补原父事件的本地镜像及未启动段版本证明，不派发、不收费。"""
        self.validate(for_reconcile=True)
        root = Path(self.job['root'])
        with ObjectiveMutationLock.for_resource(root / 'LONG_EXECUTION.lock'):
            with ObjectiveMutationLock.for_resource(root / 'RESUME.lock'):
                self.validate(for_reconcile=True)
                for name in self.job['plans']:
                    if not (root / (name + '_START.json')).exists():
                        continue
                    frozen = self.summary_value['accounts'][name]
                    operation = self.campaign.peek_status()['operations'][frozen['operation_id']]
                    for parent in operation.get('segments', [])[frozen['segment_count']:]:
                        number = parent['segment_number']
                        if not self._dispatch_path(name, number).exists():
                            prefix = name + '_SEGMENT_' + str(number).zfill(6)
                            _require(not (root / (prefix + '_WORKER.json')).exists()
                                and not (root / (prefix + '_INPUT_ACCESS.json')).exists(),
                                'DISPATCH_VERSION_WORKER_ALREADY_PRESENT')
                    state = self.governance.reconcile_segment_mirrors(name)
                    for row in state['segments'][frozen['segment_count']:]:
                        dispatch = row['dispatch']; number = dispatch['segment_number']
                        if self._dispatch_path(name, number).exists():
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
                                'operation_id', 'segment_number', 'profile_hash', 'upper_bound_seconds', 'dispatched_at')}),
                            'worker_absent': True}
                        _require(not (root / (prefix + '_WORKER.json')).exists()
                            and not (root / (prefix + '_INPUT_ACCESS.json')).exists(),
                            'DISPATCH_VERSION_WORKER_ALREADY_PRESENT')
                        if not marker.exists():
                            # 原 DISPATCH 已写、版本侧车尚未写时崩溃；无侧车不能合法启动 reader。
                            _require(row['charge'] is None and state['pending'] == dispatch
                                and operation.get('active_segment') == parent,
                                'DISPATCH_VERSION_NO_WORKER_PROOF_REQUIRED')
                            immutable(marker, expected)
                        _require(_read(_reference(marker, self.root), self.root) == expected,
                            'DISPATCH_VERSION_NO_WORKER_PROOF_REQUIRED')
                        path = _path(self._dispatch_path(name, number), self.root)
                        immutable(path, {
                            'schema_version': 'ACCOUNT_VERSION_SEGMENT_V1', 'dispatch_id': dispatch['dispatch_id'],
                            'segment_number': number, 'account_version_continuation': self.proof(name)})
                return self.validate()

    def segment_proof(self, name, number):
        _require(name in self.summary_value['accounts'] and type(number) is int and number > 0, 'DISPATCH_CONFLICT')
        return None if number <= self.summary_value['accounts'][name]['segment_count'] else self.proof(name)

    def record_dispatch(self, name, dispatch):
        self.validate(for_dispatch=True, name=name, segment_number=dispatch.get('segment_number'))
        state = self.governance.segment_status(name)
        _require(state['pending'] == dispatch and dispatch['segment_number'] > self.summary_value['accounts'][name]['segment_count'],
            'DISPATCH_CONFLICT')
        value = {'schema_version': 'ACCOUNT_VERSION_SEGMENT_V1', 'dispatch_id': dispatch['dispatch_id'],
            'segment_number': dispatch['segment_number'], 'account_version_continuation': self.proof(name)}
        immutable(_path(self._dispatch_path(name, dispatch['segment_number']), self.root), value)
        return value

    def validate_dispatch(self, name, number):
        if not hasattr(self, 'summary_value'):
            self.validate(for_dispatch=False)
        _require(name in self.summary_value['accounts'] and type(number) is int and number > 0, 'DISPATCH_CONFLICT')
        state = self.governance.segment_status(name)
        _require(number <= len(state['segments']), 'DISPATCH_CONFLICT')
        if self.segment_proof(name, number) is None:
            for reference in self.summary_value['accounts'][name]['segment_refs']:
                if Path(reference['path']).name.startswith(name + '_SEGMENT_' + str(number).zfill(6) + '_'):
                    _require(reference == _reference(reference['path'], self.root), 'ORIGINAL_CHANGED')
            return None
        dispatch = state['segments'][number - 1]['dispatch']
        expected = {'schema_version': 'ACCOUNT_VERSION_SEGMENT_V1', 'dispatch_id': dispatch['dispatch_id'],
            'segment_number': number, 'account_version_continuation': self.proof(name)}
        _require(_read(_reference(self._dispatch_path(name, number), self.root), self.root) == expected, 'DISPATCH_VERSION_CONFLICT')
        return expected
