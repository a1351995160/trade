"""一次受控报告恢复的 Owner 版本桥；不修改原合同或旧版输入桥。"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re

from .campaign_scope_v1 import OwnerApprovalStoreV1, VERSION as SCOPE_VERSION, _write_once, scope_summary
from .common import stable_hash
from .continuous_research_contract_v1 import validate_continuous_research_contract
from .mutation_boundary import ObjectiveMutationLock
from .research_capabilities_v1 import capabilities
from .secure_file_reference_v1 import (
    checked_directory_path, file_sha256, read_file_bytes, read_pinned_json, validated_reference_path,
)


VERSION = 'OWNER_REPORT_RECOVERY_CAPABILITY_BRIDGE_V2'
RECOVERY_VERSION = 'REPORT_RECOVERY_MANIFEST_V2'
DIRECTORY = 'report_recovery_capability_bridges'
SOURCE_PATHS = frozenset({
    'scripts/run_strategy_account_v1.py',
    'src/chanlun_trader/research_factory/universe_account_inputs_v1.py',
    'src/chanlun_trader/research_factory/universe_benchmark_v1.py',
})
CONTEXT_FIELDS = frozenset({'parent_terminal_ref', 'recovery_id', 'proposal_ref', 'human_confirmation_ref'})


def _require(condition, reason):
    if not condition:
        raise PermissionError('REPORT_RECOVERY_CAPABILITY_' + reason)


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _path(value, root=None):
    return validated_reference_path(value, root=root, error_code='REPORT_RECOVERY_CAPABILITY_PATH_INVALID')


def _read(reference, root):
    _require(isinstance(reference, dict) and set(reference) == {'path', 'sha256'}
             and _hash(reference['sha256']), 'REFERENCE_INVALID')
    value = read_pinned_json(reference, root=root, error_code='REPORT_RECOVERY_CAPABILITY_EVIDENCE_CHANGED')
    _require(isinstance(value, dict), 'EVIDENCE_OBJECT_REQUIRED')
    return value


def _terminal(root, reference, base):
    # 终态核验只重放原目标事件，不调用 campaign.peek_status，避免能力桥递归。
    from .report_repair_stop_v2 import read_report_repair_terminal
    return read_report_repair_terminal(root, reference, base_authorization=base)


def _base(root, base):
    root = checked_directory_path(root, error_code='REPORT_RECOVERY_CAPABILITY_ROOT_INVALID')
    _require(isinstance(base, dict) and base.get('root') == os.path.normcase(str(root)), 'ROOT_CONFLICT')
    policy = base.get('scope_policy')
    _require(isinstance(policy, dict) and set(policy) == {
        'schema_version', 'summary', 'approval_store', 'approval_ref', 'scope_hash'}
        and policy.get('schema_version') == SCOPE_VERSION
        and policy.get('scope_hash') == stable_hash({key: item for key, item in policy.items()
                                                   if key != 'scope_hash'}), 'ORIGINAL_SCOPE_INVALID')
    owner = checked_directory_path(policy['approval_store'],
        error_code='REPORT_RECOVERY_CAPABILITY_OWNER_ROOT_INVALID')
    approvals = OwnerApprovalStoreV1(owner)
    approvals.require(policy['approval_ref'], policy['summary'])
    return root, policy, approvals


def _context(root, base, context, approvals):
    _require(isinstance(context, dict) and set(context) == CONTEXT_FIELDS, 'CONTEXT_FIELDS_INVALID')
    terminal = _terminal(root, context['parent_terminal_ref'], base)
    _require(isinstance(terminal, dict) and terminal.get('schema_version') == 'REPORT_REPAIR_STOP_TERMINAL_V2'
        and terminal.get('status') == 'REPORT_REPAIR_STOPPED'
        and terminal.get('authorization_id') == base['authorization_id']
        and terminal.get('base_authorization_identity') == stable_hash(base), 'PARENT_TERMINAL_CONFLICT')
    parent = _read(terminal['parent_manifest_ref'], root)
    _require(parent.get('schema_version') == 'REPORT_REPAIR_MANIFEST_V1'
        and parent.get('identity') == stable_hash({key: value for key, value in parent.items() if key != 'identity'}),
        'PARENT_MANIFEST_INVALID')
    old = parent.get('summary')
    _require(isinstance(old, dict), 'PARENT_SUMMARY_INVALID')
    approvals.require(parent['approval_ref'], old)
    _require(old.get('base_authorization_identity') == stable_hash(base)
        and old.get('authorization_id') == base['authorization_id']
        and old.get('objective_id') == base['objective_id']
        and old.get('approval_store') == str(approvals.directory)
        and terminal.get('repair_id') == old.get('repair_id')
        and terminal.get('task_id') == old.get('task_id'), 'PARENT_SCOPE_CONFLICT')
    recovery_id = stable_hash({'version': RECOVERY_VERSION, 'authorization_id': base['authorization_id'],
                              'parent_manifest_ref': terminal['parent_manifest_ref']})
    _require(context['recovery_id'] == recovery_id, 'RECOVERY_ID_CONFLICT')
    request = _read(terminal['stop_request_ref'], root)
    stop = request.get('summary', {})
    _require(stop.get('proposal_ref') == context['proposal_ref']
        and stop.get('human_confirmation_ref') == context['human_confirmation_ref'], 'HUMAN_BINDING_CONFLICT')
    proposal = context['proposal_ref']
    human_ref = context['human_confirmation_ref']
    _require(all(isinstance(value, dict) and set(value) == {'path', 'sha256'} and _hash(value['sha256'])
                 for value in (proposal, human_ref)), 'HUMAN_REFERENCE_INVALID')
    proposal_path = _path(proposal.get('path', ''), root / 'reports')
    human_path = _path(human_ref.get('path', ''), root / 'reports')
    _require(proposal_path.name == 'REPORT_RENDER_CONTINUATION_RECOVERY_PROPOSAL_CN.md'
        and human_path.name == 'HUMAN_REPORT_RENDER_RECOVERY_CONFIRMATION.json'
        and proposal_path.parent == human_path.parent
        and file_sha256(proposal_path, root=root / 'reports', error_code='REPORT_RECOVERY_CAPABILITY_PROPOSAL_CHANGED')
        == proposal['sha256'], 'PROPOSAL_CONFLICT')
    human = _read(human_ref, root / 'reports')
    profile = old['execution_profile']
    expected = {
        'schema_version': 'HUMAN_REPORT_RENDER_RECOVERY_CONFIRMATION_V1',
        'origin': 'CURRENT_USER_REPLY_TO_EXACT_FINITE_REPORT_RENDER_RECOVERY_PROPOSAL',
        'proposal': proposal, 'parent_repair_id': old['repair_id'],
        'parent_manifest_sha256': terminal['parent_manifest_ref']['sha256'],
        'campaign_id': base['authorization_id'], 'objective_id': base['objective_id'],
        'verification_jobs_delta': 1,
        'effective_verification_jobs_from': base['resource_limits']['verification_jobs'] + 1,
        'effective_verification_jobs_to': base['resource_limits']['verification_jobs'] + 2,
        'stage': 'EXPLORATION', 'maximum_additional_report_recoveries': 1,
        'recovery_total_seconds': profile['total_seconds'], 'worker_seconds': profile['worker_seconds'],
        'memory_mib': profile['memory_mib'], 'campaign_total_wall_seconds': base['resource_limits']['wall_seconds'],
        'expires_at': base['expires_at'], 'account_replay_permitted': False, 'old_records_immutable': True,
        'controlled_boundary_settlement_authorized': True, 'exact_source_version_recovery_authorized': True,
        'strategy_threshold_changes_permitted': False, 'project_model_api_calls': 0,
    }
    _require(all(type(human.get(key)) is type(value) and human.get(key) == value
                 for key, value in expected.items()) and isinstance(human.get('user_reply'), str)
             and bool(human['user_reply']), 'HUMAN_SCOPE_CONFLICT')
    return terminal, old


class ReportRecoveryCapabilityBridgeV2:
    """summary/read 只读；install 追加新 Owner 记录，不覆盖旧版授权。"""

    @staticmethod
    def _capture(root, base, context, replacements):
        root, policy, approvals = _base(root, base)
        terminal, parent = _context(root, base, context, approvals)
        job = _read(parent['original']['job'], root)
        _require(job.get('objective_id') == base['objective_id'] and isinstance(job.get('source_hashes'), dict)
            and job['source_hashes'], 'ORIGINAL_JOB_CONFLICT')
        archive = _path(job.get('root', ''), root / 'reports') / 'source-archive'
        _require(isinstance(replacements, list) and len(replacements) == len(SOURCE_PATHS), 'SOURCE_ALLOWLIST_REQUIRED')
        allowed = {}
        for item in replacements:
            _require(isinstance(item, dict) and set(item) == {'path', 'old_sha256', 'new_sha256', 'archive_ref'},
                     'SOURCE_FIELDS_INVALID')
            source = _path(item['path'], root)
            _require(source.relative_to(root).as_posix() in SOURCE_PATHS and str(source) not in allowed
                and _hash(item['old_sha256']) and _hash(item['new_sha256'])
                and item['old_sha256'] != item['new_sha256']
                and job['source_hashes'].get(str(source)) == item['old_sha256'], 'SOURCE_ALLOWLIST_REQUIRED')
            archived = archive / (item['old_sha256'] + '_' + source.name)
            _require(item['archive_ref'] == {'path': str(archived), 'sha256': item['old_sha256']}, 'ARCHIVE_BINDING_INVALID')
            allowed[str(source)] = item
        _require({Path(path).relative_to(root).as_posix() for path in allowed} == SOURCE_PATHS, 'SOURCE_ALLOWLIST_REQUIRED')
        for source, digest in job['source_hashes'].items():
            _require(_hash(digest), 'SOURCE_HASH_INVALID')
            path = _path(source, root)
            _require(file_sha256(archive / (digest + '_' + path.name), root=root / 'reports',
                error_code='REPORT_RECOVERY_CAPABILITY_ARCHIVE_CHANGED') == digest, 'ARCHIVE_CHANGED')
            expected = allowed[source]['new_sha256'] if source in allowed else digest
            _require(file_sha256(path, root=root, error_code='REPORT_RECOVERY_CAPABILITY_SOURCE_CHANGED') == expected,
                     'UNAPPROVED_SOURCE_CHANGED')
        current = capabilities()
        _require(current.get('fingerprint') == stable_hash({key: value for key, value in current.items()
                                                          if key != 'fingerprint'}), 'CURRENT_SNAPSHOT_INVALID')
        original = deepcopy(current)
        for section in (original, original['full_universe'], original['long_horizon']):
            for source, item in allowed.items():
                name = Path(source).name
                if name in section['source_hashes']:
                    _require(section['source_hashes'][name] == item['new_sha256'], 'LIVE_CAPABILITY_SOURCE_CONFLICT')
                    section['source_hashes'][name] = item['old_sha256']
        original['fingerprint'] = stable_hash({key: value for key, value in original.items() if key != 'fingerprint'})
        contract = policy['summary']['contract']
        _require(original['fingerprint'] == contract.get('capabilities_fingerprint'), 'OTHER_CAPABILITIES_CHANGED')
        validate_continuous_research_contract(contract, capabilities_snapshot=original)
        _require(scope_summary(root, base, contract, policy['summary']['stage_limits'],
            capabilities_snapshot=original) == policy['summary'], 'ORIGINAL_AUTHORIZATION_CONFLICT')
        summary = {'schema_version': VERSION, 'root': os.path.normcase(str(root)),
            'authorization_id': base['authorization_id'], 'objective_id': base['objective_id'],
            'base_authorization_identity': stable_hash(base), 'scope_hash': policy['scope_hash'],
            'approval_store': str(approvals.directory), 'expires_at': base['expires_at'],
            'original_capabilities_fingerprint': original['fingerprint'],
            'live_capabilities_fingerprint': current['fingerprint'],
            'recovery_context': deepcopy(context), 'parent_terminal_identity': terminal['identity'],
            'source_replacements': sorted(deepcopy(replacements), key=lambda item: item['path']),
            'allowed_action': 'ONE_REPORT_RENDER_RECOVERY_EXACT_SOURCES_ONLY'}
        return summary, original, approvals

    @classmethod
    def summary(cls, root, base_authorization, *, recovery_context, source_replacements):
        return cls._capture(root, base_authorization, recovery_context, source_replacements)[0]

    @staticmethod
    def _binding_path(approvals, base, fingerprint):
        return approvals.directory / DIRECTORY / stable_hash(base) / (fingerprint + '.json')

    @classmethod
    def is_registered(cls, root, base_authorization):
        """存在新版定位后不能退回旧桥；损坏、目录或链接由 read 严格拒绝。"""
        _, _, approvals = _base(root, base_authorization)
        binding = cls._binding_path(approvals, base_authorization, capabilities()['fingerprint'])
        _path(binding, approvals.directory)
        return binding.exists() or binding.is_symlink()

    @classmethod
    def install(cls, root, base_authorization, summary, *, approval_ref):
        _require(isinstance(summary, dict), 'SUMMARY_INVALID')
        current, _, approvals = cls._capture(root, base_authorization,
            summary.get('recovery_context'), summary.get('source_replacements'))
        _require(current == summary, 'SUMMARY_CHANGED')
        approvals.require(approval_ref, summary)
        record = {'schema_version': VERSION, 'summary': deepcopy(summary), 'approval_ref': deepcopy(approval_ref)}
        record['identity'] = stable_hash(record)
        path = approvals.directory / DIRECTORY / 'records' / (record['identity'] + '.json')
        binding_path = cls._binding_path(approvals, base_authorization, summary['live_capabilities_fingerprint'])
        with ObjectiveMutationLock.for_resource(approvals.directory / DIRECTORY):
            _path(path, approvals.directory)
            _path(binding_path, approvals.directory)
            _write_once(path, record)
            reference = {'path': str(path), 'sha256': file_sha256(path, root=approvals.directory,
                error_code='REPORT_RECOVERY_CAPABILITY_RECORD_CHANGED'), 'approval_ref': deepcopy(approval_ref)}
            _write_once(binding_path, {'schema_version': VERSION,
                'base_authorization_identity': stable_hash(base_authorization),
                'live_capabilities_fingerprint': summary['live_capabilities_fingerprint'], 'reference': reference})
        return reference

    @classmethod
    def read(cls, root, base_authorization, *, reference=None):
        from .report_repair_stop_v2 import _within_terminal_proof
        fixed_root = Path(root).resolve(strict=True)
        fixed_base, fixed_reference = deepcopy(base_authorization), deepcopy(reference)
        return _within_terminal_proof(('bridge', str(fixed_root), stable_hash(fixed_base), stable_hash(fixed_reference)),
            lambda: cls._uncached_read(fixed_root, fixed_base, reference=fixed_reference))

    @classmethod
    def _uncached_read(cls, root, base_authorization, *, reference=None):
        _, _, approvals = _base(root, base_authorization)
        current = capabilities()
        path = cls._binding_path(approvals, base_authorization, current['fingerprint'])
        _path(path, approvals.directory)
        if not path.exists() and not path.is_symlink():
            raise ValueError('CONTINUOUS_CAPABILITIES_CHANGED_NEW_VERSION_REQUIRED')
        binding = json.loads(read_file_bytes(path, root=approvals.directory, maximum_bytes=65536,
            error_code='REPORT_RECOVERY_CAPABILITY_BINDING_INVALID'))
        _require(isinstance(binding, dict) and set(binding) == {
            'schema_version', 'base_authorization_identity', 'live_capabilities_fingerprint', 'reference'}
            and binding['schema_version'] == VERSION
            and binding['base_authorization_identity'] == stable_hash(base_authorization)
            and binding['live_capabilities_fingerprint'] == current['fingerprint'], 'BINDING_CONFLICT')
        approved = binding['reference']
        _require(isinstance(approved, dict) and set(approved) == {'path', 'sha256', 'approval_ref'}
            and (reference is None or reference == approved), 'REFERENCE_CONFLICT')
        record = _read({key: approved[key] for key in ('path', 'sha256')}, approvals.directory)
        _require(isinstance(record, dict) and set(record) == {'schema_version', 'summary', 'approval_ref', 'identity'}
            and record['schema_version'] == VERSION
            and isinstance(record['summary'], dict)
            and record['identity'] == stable_hash({key: value for key, value in record.items() if key != 'identity'})
            and record['approval_ref'] == approved['approval_ref']
            and Path(approved['path']) == approvals.directory / DIRECTORY / 'records' / (record['identity'] + '.json'),
            'RECORD_IDENTITY_CONFLICT')
        summary, original, checked = cls._capture(root, base_authorization,
            record['summary'].get('recovery_context'), record['summary'].get('source_replacements'))
        _require(summary == record['summary'], 'SUMMARY_CHANGED')
        checked.require(record['approval_ref'], summary)
        return {'reference': deepcopy(approved), 'record': record, 'capabilities_snapshot': original}
