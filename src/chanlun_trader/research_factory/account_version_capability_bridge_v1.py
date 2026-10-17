"""账户初始化接续的 Owner 能力映射；不创建授权、用途或预算。"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re

from chanlun_trader.account_startup_boundary_v1 import startup_checkpoint
from .campaign_scope_v1 import OwnerApprovalStoreV1, VERSION as SCOPE_VERSION, _write_once, scope_summary
from .common import stable_hash
from .mutation_boundary import ObjectiveMutationLock
from .research_capabilities_v1 import capabilities
from .secure_file_reference_v1 import (
    checked_directory_path, file_sha256, read_file_bytes, read_pinned_json, validated_reference_path,
)


VERSION = 'OWNER_ACCOUNT_VERSION_CAPABILITY_BRIDGE_V1'
BUSINESS_PATH = 'src/chanlun_trader/research_factory/universe_account_inputs_v1.py'
PROTOCOL_PATH = 'src/chanlun_trader/research_factory/account_version_continuation_v1.py'
BRIDGE_PATH = 'src/chanlun_trader/research_factory/account_version_capability_bridge_v1.py'
RUNTIME_PATHS = frozenset({PROTOCOL_PATH, BRIDGE_PATH,
    'scripts/run_strategy_account_v1.py',
    'scripts/run_account_version_session_resume_v1.py', 'scripts/setup_account_version_continuation_v1.py',
    'src/chanlun_trader/research_factory/campaign_scope_v1.py',
    'src/chanlun_trader/research_factory/research_evidence_v1.py',
    'src/chanlun_trader/research_factory/business_validation_protocol_v1.py',
    'src/chanlun_trader/research_factory/continuous_submission_v1.py',
})
SESSION_NAMES = frozenset({'session_public_runner_v1.py', 'session_account_version_setup_v1.py'})
REQUIRED_RUNTIME_PATHS = frozenset({PROTOCOL_PATH, BRIDGE_PATH,
    'scripts/run_strategy_account_v1.py', 'src/chanlun_trader/research_factory/campaign_scope_v1.py',
    'src/chanlun_trader/research_factory/research_evidence_v1.py',
    'scripts/run_account_version_session_resume_v1.py', 'scripts/setup_account_version_continuation_v1.py'})


def _require(condition, reason):
    if not condition:
        raise PermissionError('ACCOUNT_VERSION_' + reason)


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _path(value, root):
    return validated_reference_path(value, root=root, error_code='ACCOUNT_VERSION_PATH_INVALID')


def _reference(path, root):
    startup_checkpoint('ACCOUNT_VERSION_REFERENCE')
    return {'path': str(_path(path, root)),
        'sha256': file_sha256(path, root=root, error_code='ACCOUNT_VERSION_ORIGINAL_CHANGED')}


def _read(reference, root):
    startup_checkpoint('ACCOUNT_VERSION_READ')
    return read_pinned_json(reference, root=root, error_code='ACCOUNT_VERSION_ORIGINAL_CHANGED')


def _fingerprint(value):
    value['fingerprint'] = stable_hash({key: item for key, item in value.items() if key != 'fingerprint'})
    return value


def _runtime_allowed(path, root):
    relative = path.relative_to(root)
    return relative.as_posix() in RUNTIME_PATHS or (
        bool(relative.parts) and relative.parts[0] == 'reports' and path.name in SESSION_NAMES)


def _project_snapshot(snapshot, replacements):
    result = deepcopy(snapshot)
    for row in replacements:
        name = Path(row['path']).name
        for value in (result, result.get('full_universe', {}), result.get('long_horizon', {})):
            hashes = value.get('source_hashes', {})
            if name in hashes:
                _require(hashes[name] == row['new_sha256'], 'CAPABILITY_SOURCE_CONFLICT')
                hashes[name] = row['old_sha256']
    return _fingerprint(result)


class AccountVersionCapabilityBridgeV1:
    """先安装纯能力桥，再冻结账户边界；read 不访问 campaign，避免校验递归。"""

    @staticmethod
    def _predecessor(root, base, approvals, reference, previous):
        if reference is None:
            return previous
        from .report_repair_capability_bridge_v1 import ReportRepairCapabilityBridgeV1, VERSION as OLD_VERSION
        _require(isinstance(reference, dict) and set(reference) == {'path', 'sha256', 'approval_ref'},
                 'PREDECESSOR_REFERENCE_INVALID')
        record = _read({key: reference[key] for key in ('path', 'sha256')}, approvals.directory)
        _require(set(record) == {'schema_version', 'summary', 'approval_ref', 'identity'}
            and record['schema_version'] == OLD_VERSION
            and record['identity'] == stable_hash({key: value for key, value in record.items() if key != 'identity'})
            and record['approval_ref'] == reference['approval_ref']
            and Path(reference['path']) == approvals.directory / 'report_repair_capability_bridges' / 'records'
                / (record['identity'] + '.json'), 'PREDECESSOR_IDENTITY_CONFLICT')
        old = record['summary']
        policy = base['scope_policy']
        _require(old.get('schema_version') == OLD_VERSION and old.get('root') == os.path.normcase(str(root))
            and old.get('base_authorization_identity') == stable_hash(base)
            and old.get('scope_hash') == policy['scope_hash']
            and old.get('approval_store') == str(approvals.directory)
            and old.get('expires_at') == base['expires_at']
            and old.get('authorization_id') == base['authorization_id']
            and old.get('objective_id') == base['objective_id']
            and old.get('allowed_action') == 'INPUT_IDENTITY_MEMORY_FIX_ONLY'
            and old.get('live_capabilities_fingerprint') == previous['fingerprint'], 'PREDECESSOR_SCOPE_CONFLICT')
        approvals.require(record['approval_ref'], old)
        binding_path = ReportRepairCapabilityBridgeV1._binding_path(approvals, base, previous['fingerprint'])
        binding = json.loads(read_file_bytes(binding_path, root=approvals.directory, maximum_bytes=65536,
            error_code='ACCOUNT_VERSION_PREDECESSOR_BINDING_INVALID'))
        _require(binding == {'schema_version': OLD_VERSION, 'base_authorization_identity': stable_hash(base),
            'live_capabilities_fingerprint': previous['fingerprint'], 'reference': reference},
            'PREDECESSOR_BINDING_CONFLICT')
        rows = old.get('source_replacements')
        _require(isinstance(rows, list) and len(rows) == 1 and rows[0].get('path') == str(root / BUSINESS_PATH),
            'PREDECESSOR_SOURCE_INVALID')
        row = rows[0]
        _require(row.get('archive_ref', {}).get('sha256') == row.get('old_sha256')
            and _reference(row['archive_ref']['path'], root / 'reports') == row['archive_ref'],
            'PREDECESSOR_ARCHIVE_CHANGED')
        original = _project_snapshot(previous, rows)
        _require(original['fingerprint'] == old.get('original_capabilities_fingerprint'),
                 'PREDECESSOR_CAPABILITIES_CONFLICT')
        return original

    @classmethod
    def _capture(cls, root, base, replacements, runtime_refs, predecessor_ref, *, _time_extension=None):
        startup_checkpoint('ACCOUNT_VERSION_CAPABILITY_CAPTURE')
        root = checked_directory_path(root, error_code='ACCOUNT_VERSION_ROOT_INVALID').resolve(strict=True)
        _require(isinstance(base, dict) and base.get('root') == os.path.normcase(str(root)), 'ROOT_CONFLICT')
        policy = base.get('scope_policy')
        _require(isinstance(policy, dict) and set(policy) == {
            'schema_version', 'summary', 'approval_store', 'approval_ref', 'scope_hash'}
            and policy['schema_version'] == SCOPE_VERSION
            and policy['scope_hash'] == stable_hash({key: value for key, value in policy.items() if key != 'scope_hash'}),
            'ORIGINAL_SCOPE_INVALID')
        owner = checked_directory_path(policy['approval_store'], error_code='ACCOUNT_VERSION_OWNER_ROOT_INVALID')
        approvals = OwnerApprovalStoreV1.from_checked_directory(owner)
        approvals.require(policy['approval_ref'], policy['summary'])
        successor = {}
        if _time_extension is not None:
            from .account_purpose_time_extension_v1 import AccountPurposeTimeExtensionV1
            _require(type(_time_extension) is AccountPurposeTimeExtensionV1
                and _time_extension.summary_value['base_authorization_identity'] == stable_hash(base),
                'TIME_EXTENSION_CONTEXT_REQUIRED')
            successor = {row['path']: row for row in _time_extension.summary_value['source_replacements']}

        def source_matches(reference):
            row = successor.get(reference['path'])
            if row is None:
                return reference == _reference(reference['path'], root)
            return (row['old_sha256'] == reference['sha256']
                and row['archive_ref']['sha256'] == reference['sha256']
                and _reference(row['archive_ref']['path'], root / 'reports') == row['archive_ref']
                and _reference(row['path'], root)['sha256'] == row['new_sha256'])

        _require(isinstance(replacements, list) and replacements, 'SOURCE_ALLOWLIST_REQUIRED')
        seen = set()
        for row in replacements:
            startup_checkpoint('ACCOUNT_VERSION_CAPABILITY_SOURCE')
            _require(isinstance(row, dict) and set(row) == {'path', 'old_sha256', 'new_sha256', 'archive_ref'},
                'SOURCE_FIELDS_INVALID')
            path = _path(row['path'], root)
            _require((path == root / BUSINESS_PATH or _runtime_allowed(path, root)) and path not in seen
                and _hash(row['old_sha256']) and _hash(row['new_sha256'])
                and row['old_sha256'] != row['new_sha256'], 'SOURCE_ALLOWLIST_REQUIRED')
            seen.add(path)
            _require(isinstance(row['archive_ref'], dict) and row['archive_ref'].get('sha256') == row['old_sha256']
                and _reference(row['archive_ref']['path'], root / 'reports') == row['archive_ref'], 'ARCHIVE_CHANGED')
            _require(source_matches({'path': str(path), 'sha256': row['new_sha256']}), 'SOURCE_CHANGED')
        _require(root / BUSINESS_PATH in seen, 'BUSINESS_SOURCE_REQUIRED')
        _require(isinstance(runtime_refs, list), 'RUNTIME_SOURCE_PINS_REQUIRED')
        runtime_seen = set()
        for reference in runtime_refs:
            startup_checkpoint('ACCOUNT_VERSION_CAPABILITY_RUNTIME')
            path = _path(reference.get('path', ''), root)
            _require(_runtime_allowed(path, root) and path not in runtime_seen
                and source_matches(reference), 'RUNTIME_SOURCE_CHANGED')
            runtime_seen.add(path)
        _require(all(root / path in runtime_seen for path in REQUIRED_RUNTIME_PATHS),
            'PROTOCOL_SOURCE_PIN_REQUIRED')
        _require(all(Path(row['path']) in runtime_seen for row in replacements if Path(row['path']) != root / BUSINESS_PATH),
            'REPLACEMENT_RUNTIME_PIN_REQUIRED')
        current = capabilities()
        _require(current.get('fingerprint') == _fingerprint(deepcopy(current))['fingerprint'], 'CURRENT_SNAPSHOT_INVALID')
        previous = _project_snapshot(current, replacements)
        original = cls._predecessor(root, base, approvals, predecessor_ref, previous)
        _require(original['fingerprint'] == policy['summary']['contract'].get('capabilities_fingerprint'),
                 'OTHER_CAPABILITIES_CHANGED')
        _require(scope_summary(root, base, policy['summary']['contract'], policy['summary']['stage_limits'],
            capabilities_snapshot=original) == policy['summary'], 'ORIGINAL_AUTHORIZATION_CONFLICT')
        summary = {'schema_version': VERSION, 'root': os.path.normcase(str(root)),
            'authorization_id': base['authorization_id'], 'objective_id': base['objective_id'],
            'base_authorization_identity': stable_hash(base), 'scope_hash': policy['scope_hash'],
            'approval_store': str(owner), 'expires_at': base['expires_at'],
            'original_capabilities_fingerprint': original['fingerprint'],
            'previous_capabilities_fingerprint': previous['fingerprint'],
            'live_capabilities_fingerprint': current['fingerprint'],
            'source_replacements': sorted(deepcopy(replacements), key=lambda row: row['path']),
            'runtime_sources': sorted(deepcopy(runtime_refs), key=lambda row: row['path']),
            'predecessor_bridge_ref': deepcopy(predecessor_ref),
            'allowed_action': 'ONE_ACCOUNT_INITIALIZATION_VERSION_CONTINUATION', 'grant_delta': 0}
        return summary, original, approvals

    @classmethod
    def summary(cls, root, base_authorization, *, source_replacements, runtime_source_refs,
                predecessor_bridge_ref=None):
        return cls._capture(root, base_authorization, source_replacements, runtime_source_refs, predecessor_bridge_ref)[0]

    @staticmethod
    def _binding_path(approvals, base, fingerprint):
        return approvals.directory / 'account_version_capability_bridges' / stable_hash(base) / (fingerprint + '.json')

    @classmethod
    def install(cls, root, base_authorization, summary, *, approval_ref):
        current, _, approvals = cls._capture(root, base_authorization, summary.get('source_replacements'),
            summary.get('runtime_sources'), summary.get('predecessor_bridge_ref'))
        _require(current == summary, 'SUMMARY_CHANGED')
        approvals.require(approval_ref, summary)
        record = {'schema_version': VERSION, 'summary': deepcopy(summary), 'approval_ref': deepcopy(approval_ref)}
        record['identity'] = stable_hash(record)
        folder = approvals.directory / 'account_version_capability_bridges'
        path = folder / 'records' / (record['identity'] + '.json')
        binding_path = cls._binding_path(approvals, base_authorization, summary['live_capabilities_fingerprint'])
        with ObjectiveMutationLock.for_resource(folder):
            _path(path, approvals.directory); _path(binding_path, approvals.directory)
            _write_once(path, record)
            reference = {**_reference(path, approvals.directory), 'approval_ref': deepcopy(approval_ref)}
            _write_once(binding_path, {'schema_version': VERSION, 'base_authorization_identity': stable_hash(base_authorization),
                'live_capabilities_fingerprint': summary['live_capabilities_fingerprint'], 'reference': reference})
        return reference

    @classmethod
    def read(cls, root, base_authorization, *, reference=None, time_extension_ref=None, recovery=None):
        base = base_authorization
        policy = base.get('scope_policy', {})
        owner = checked_directory_path(policy.get('approval_store', ''), error_code='ACCOUNT_VERSION_OWNER_ROOT_INVALID')
        approvals = OwnerApprovalStoreV1.from_checked_directory(owner)
        approvals.require(policy['approval_ref'], policy['summary'])
        extension = None
        if time_extension_ref is not None:
            from .account_purpose_time_extension_v1 import AccountPurposeTimeExtensionV1
            extension = AccountPurposeTimeExtensionV1.read(root, time_extension_ref,
                base_authorization=base, recovery=recovery)
            recovery = extension.recovery
        if recovery is not None:
            from .failed_account_recovery_v1 import FailedAccountRecoveryV1
            _require(type(recovery) is FailedAccountRecoveryV1, 'SIGNED_RECOVERY_CONTEXT_REQUIRED')
            checked_root = checked_directory_path(root, error_code='ACCOUNT_VERSION_ROOT_INVALID')
            _require(os.path.normcase(str(checked_root)) == os.path.normcase(str(recovery.root)), 'RECOVERY_ROOT_CONFLICT')
            if extension is not None:
                from .failed_account_recovery_v1 import (
                    _owner_locator, _read as _read_recovery_record, _require as _recovery_require,
                )
                recovery_reference = deepcopy(recovery.scope_reference)
                _owner_locator(checked_root, recovery_reference, base_authorization=base, job=extension.job,
                    predecessor_reference=time_extension_ref, original=extension.summary_value['original'])
                inherited_reference = deepcopy(extension.summary_value['predecessor_bridge_ref'])
                _recovery_require(reference is None or reference == inherited_reference,
                    'PREDECESSOR_BRIDGE_REFERENCE_CONFLICT')
                # 原件只作当前调用候选；下方最后 full read 仍复验期间的持续变化。
                record = _read_recovery_record({key: inherited_reference[key] for key in ('path', 'sha256')}, owner)
                fresh = FailedAccountRecoveryV1.read(checked_root, recovery_reference, base_authorization=base)
                _recovery_require(time_extension_ref == fresh.summary_value['predecessor_manifest_ref'],
                    'PREDECESSOR_REFERENCE_CONFLICT')
                _require(fresh.predecessor_summary == extension.summary_value
                    and fresh.predecessor_summary['predecessor_bridge_ref'] == inherited_reference, 'RECOVERY_BRIDGE_CONFLICT')
                _require(fresh.job == extension.job and fresh.job_path == extension.job_path, 'JOB_CHANGED')
                _recovery_require(os.path.normcase(str(owner)) == os.path.normcase(fresh.summary_value['approval_store']),
                    'AUTHORIZATION_CONFLICT')
                return {'reference': inherited_reference, 'record': record,
                    'capabilities_snapshot': deepcopy(fresh.summary_value['capabilities_snapshot'])}
            inherited = recovery.require_capability_bridge(reference, base_authorization=base)
            return inherited
        fingerprint = capabilities()['fingerprint']
        path = cls._binding_path(approvals, base, fingerprint)
        _path(path, owner)
        if not path.is_file():
            raise ValueError('CONTINUOUS_CAPABILITIES_CHANGED_NEW_VERSION_REQUIRED')
        binding = json.loads(read_file_bytes(path, root=owner, maximum_bytes=65536,
            error_code='ACCOUNT_VERSION_BINDING_INVALID'))
        _require(set(binding) == {'schema_version', 'base_authorization_identity', 'live_capabilities_fingerprint', 'reference'}
            and binding['schema_version'] == VERSION and binding['base_authorization_identity'] == stable_hash(base)
            and binding['live_capabilities_fingerprint'] == fingerprint, 'BINDING_CONFLICT')
        approved = binding['reference']
        _require(isinstance(approved, dict) and set(approved) == {'path', 'sha256', 'approval_ref'}
            and (reference is None or approved == reference), 'REFERENCE_CONFLICT')
        if extension is not None:
            _require(extension.summary_value['predecessor_bridge_ref'] == approved, 'TIME_EXTENSION_PREDECESSOR_CONFLICT')
        record = _read({key: approved[key] for key in ('path', 'sha256')}, owner)
        _require(set(record) == {'schema_version', 'summary', 'approval_ref', 'identity'}
            and record['schema_version'] == VERSION
            and record['identity'] == stable_hash({key: value for key, value in record.items() if key != 'identity'})
            and record['approval_ref'] == approved['approval_ref']
            and Path(approved['path']) == owner / 'account_version_capability_bridges' / 'records'
                / (record['identity'] + '.json'), 'RECORD_IDENTITY_CONFLICT')
        summary, original, _ = cls._capture(root, base, record['summary'].get('source_replacements'),
            record['summary'].get('runtime_sources'), record['summary'].get('predecessor_bridge_ref'),
            _time_extension=extension)
        _require(summary == record['summary'], 'SUMMARY_CHANGED')
        approvals.require(record['approval_ref'], summary)
        return {'reference': deepcopy(approved), 'record': record, 'capabilities_snapshot': original}

    @classmethod
    def read_historical(cls, root, base_authorization, *, reference, time_extension_ref, recovery=None):
        """已批准后继治理源码只映射历史字节；不授予新账户额度。"""
        _require(time_extension_ref is not None, 'TIME_EXTENSION_REFERENCE_REQUIRED')
        return cls.read(root, base_authorization, reference=reference, time_extension_ref=time_extension_ref, recovery=recovery)
