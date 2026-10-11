"""维护者批准的单项输入实现替换；保留原持续研究合同和总授权。"""
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


VERSION = 'OWNER_REPORT_REPAIR_CAPABILITY_BRIDGE_V1'
SOURCE_PATH = 'src/chanlun_trader/research_factory/universe_account_inputs_v1.py'
SOURCE_NAME = Path(SOURCE_PATH).name


def _require(condition, reason):
    if not condition:
        raise PermissionError('REPORT_REPAIR_CAPABILITY_' + reason)


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _path(value, root=None):
    return validated_reference_path(value, root=root, error_code='REPORT_REPAIR_CAPABILITY_PATH_INVALID')


class ReportRepairCapabilityBridgeV1:
    """summary/read 只读；install 只能写原 Owner 根下经批准的追加记录。"""

    @staticmethod
    def _capture(root, base, replacements):
        root = checked_directory_path(root, error_code='REPORT_REPAIR_CAPABILITY_ROOT_INVALID')
        _require(isinstance(base, dict) and base.get('root') == os.path.normcase(str(root)), 'ROOT_CONFLICT')
        policy = base.get('scope_policy')
        _require(isinstance(policy, dict) and set(policy) == {
            'schema_version', 'summary', 'approval_store', 'approval_ref', 'scope_hash'}
            and policy.get('schema_version') == SCOPE_VERSION
            and policy.get('scope_hash') == stable_hash({key: value for key, value in policy.items()
                                                       if key != 'scope_hash'}), 'ORIGINAL_SCOPE_INVALID')
        owner_root = checked_directory_path(policy['approval_store'],
            error_code='REPORT_REPAIR_CAPABILITY_OWNER_ROOT_INVALID')
        approvals = OwnerApprovalStoreV1(owner_root)
        approvals.require(policy['approval_ref'], policy['summary'])
        _require(isinstance(replacements, list) and len(replacements) == 1, 'SOURCE_ALLOWLIST_REQUIRED')
        item = replacements[0]
        _require(isinstance(item, dict) and set(item) == {
            'path', 'old_sha256', 'new_sha256', 'archive_ref'}, 'SOURCE_FIELDS_INVALID')
        source = _path(item['path'], root)
        _require(source == root / SOURCE_PATH and _hash(item['old_sha256'])
            and _hash(item['new_sha256']) and item['old_sha256'] != item['new_sha256'], 'SOURCE_ALLOWLIST_REQUIRED')
        archived = item['archive_ref']
        _require(isinstance(archived, dict) and set(archived) == {'path', 'sha256'}
            and archived['sha256'] == item['old_sha256'], 'ARCHIVE_BINDING_INVALID')
        _path(archived['path'], root / 'reports')
        _require(file_sha256(archived['path'], root=root / 'reports',
            error_code='REPORT_REPAIR_CAPABILITY_ARCHIVE_CHANGED') == item['old_sha256'], 'ARCHIVE_CHANGED')
        _require(file_sha256(source, root=root, error_code='REPORT_REPAIR_CAPABILITY_SOURCE_CHANGED')
            == item['new_sha256'], 'SOURCE_CHANGED')
        current = capabilities()
        _require(current.get('fingerprint') == stable_hash({key: value for key, value in current.items()
                                                          if key != 'fingerprint'}), 'CURRENT_SNAPSHOT_INVALID')
        _require(current['full_universe']['source_hashes'].get(SOURCE_NAME) == item['new_sha256'],
                 'LIVE_CAPABILITY_SOURCE_CONFLICT')
        original = deepcopy(current)
        original['full_universe']['source_hashes'][SOURCE_NAME] = item['old_sha256']
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
            'source_replacements': deepcopy(replacements), 'allowed_action': 'INPUT_IDENTITY_MEMORY_FIX_ONLY'}
        return summary, original, approvals

    @classmethod
    def summary(cls, root, base_authorization, *, source_replacements):
        return cls._capture(root, base_authorization, source_replacements)[0]

    @staticmethod
    def _binding_path(approvals, base, fingerprint):
        return approvals.directory / 'report_repair_capability_bridges' / stable_hash(base) / (fingerprint + '.json')

    @classmethod
    def install(cls, root, base_authorization, summary, *, approval_ref):
        """维护者先 approve(summary)，再追加内容寻址记录及不可覆盖的定位记录。"""
        _require(isinstance(summary, dict), 'SUMMARY_INVALID')
        current, _, approvals = cls._capture(root, base_authorization, summary.get('source_replacements'))
        _require(current == summary, 'SUMMARY_CHANGED')
        approvals.require(approval_ref, summary)
        record = {'schema_version': VERSION, 'summary': deepcopy(summary), 'approval_ref': deepcopy(approval_ref)}
        record['identity'] = stable_hash(record)
        path = approvals.directory / 'report_repair_capability_bridges' / 'records' / (record['identity'] + '.json')
        binding_path = cls._binding_path(approvals, base_authorization, summary['live_capabilities_fingerprint'])
        with ObjectiveMutationLock.for_resource(approvals.directory / 'report_repair_capability_bridges'):
            _path(path, approvals.directory)
            _path(binding_path, approvals.directory)
            _write_once(path, record)
            reference = {'path': str(path), 'sha256': file_sha256(path, root=approvals.directory,
                error_code='REPORT_REPAIR_CAPABILITY_RECORD_CHANGED'), 'approval_ref': deepcopy(approval_ref)}
            binding = {'schema_version': VERSION, 'base_authorization_identity': stable_hash(base_authorization),
                'live_capabilities_fingerprint': summary['live_capabilities_fingerprint'], 'reference': reference}
            _write_once(binding_path, binding)
        return reference

    @classmethod
    def read(cls, root, base_authorization, *, reference=None):
        """从原 Owner 根定位；显式引用必须与同一不可覆盖定位记录逐字段相等。"""
        base = base_authorization
        _require(isinstance(base, dict) and isinstance(base.get('scope_policy'), dict), 'ORIGINAL_SCOPE_INVALID')
        policy = base['scope_policy']
        owner_root = checked_directory_path(policy['approval_store'],
            error_code='REPORT_REPAIR_CAPABILITY_OWNER_ROOT_INVALID')
        approvals = OwnerApprovalStoreV1(owner_root)
        approvals.require(policy['approval_ref'], policy['summary'])
        current = capabilities()
        binding_path = cls._binding_path(approvals, base, current['fingerprint'])
        _path(binding_path, approvals.directory)
        if not binding_path.is_file():
            raise ValueError('CONTINUOUS_CAPABILITIES_CHANGED_NEW_VERSION_REQUIRED')
        binding = json.loads(read_file_bytes(binding_path, root=approvals.directory,
            maximum_bytes=65536, error_code='REPORT_REPAIR_CAPABILITY_BINDING_INVALID'))
        _require(isinstance(binding, dict) and set(binding) == {
            'schema_version', 'base_authorization_identity', 'live_capabilities_fingerprint', 'reference'}
            and binding['schema_version'] == VERSION and binding['base_authorization_identity'] == stable_hash(base)
            and binding['live_capabilities_fingerprint'] == current['fingerprint'], 'BINDING_CONFLICT')
        approved_ref = binding['reference']
        _require(isinstance(approved_ref, dict) and set(approved_ref) == {'path', 'sha256', 'approval_ref'}
            and (reference is None or reference == approved_ref), 'REFERENCE_CONFLICT')
        record = read_pinned_json({key: approved_ref[key] for key in ('path', 'sha256')},
            root=approvals.directory, error_code='REPORT_REPAIR_CAPABILITY_RECORD_CHANGED')
        _require(isinstance(record, dict) and set(record) == {'schema_version', 'summary', 'approval_ref', 'identity'}
            and record['schema_version'] == VERSION and record['identity'] == stable_hash({key: value
                for key, value in record.items() if key != 'identity'}) and record['approval_ref'] == approved_ref['approval_ref']
            and Path(approved_ref['path']) == approvals.directory / 'report_repair_capability_bridges' / 'records'
                / (record['identity'] + '.json'), 'RECORD_IDENTITY_CONFLICT')
        _require(isinstance(record['summary'], dict), 'SUMMARY_INVALID')
        summary, original, checked_approvals = cls._capture(root, base, record['summary'].get('source_replacements'))
        _require(summary == record['summary'], 'SUMMARY_CHANGED')
        checked_approvals.require(record['approval_ref'], summary)
        return {'reference': deepcopy(approved_ref), 'record': record, 'capabilities_snapshot': original}
