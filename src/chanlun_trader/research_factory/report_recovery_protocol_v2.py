"""已结清报告修复的一次显式新版恢复；不重放账户或原PASS核账。"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from .campaign_scope_v1 import CampaignScopeV1, OwnerApprovalStoreV1, grant_summary
from .common import stable_hash
from .exploration_governance import immutable
from .mutation_boundary import ObjectiveMutationLock
from .report_repair_protocol_v1 import (
    ReportRepairV1, _identity, _path, _read, _reference, _require,
)
from .report_repair_stop_v2 import _control, _publish, _terminal_proof_scope, read_report_repair_terminal
from .research_campaign_v1 import ResearchCampaignV1


VERSION = 'REPORT_RECOVERY_MANIFEST_V2'
SUMMARY_VERSION = 'OWNER_REPORT_RECOVERY_SUMMARY_V2'
RECEIPT_VERSION = 'REPORT_RECOVERY_COMPLETION_V2'
REPLACEMENT_PATHS = frozenset({
    'scripts/run_strategy_account_v1.py',
    'src/chanlun_trader/research_factory/universe_account_inputs_v1.py',
    'src/chanlun_trader/research_factory/universe_benchmark_v1.py',
})
REQUIRED_RUNTIME_PATHS = frozenset({
    'src/chanlun_trader/research_factory/report_recovery_protocol_v2.py',
    'src/chanlun_trader/research_factory/report_repair_stop_v2.py',
    'scripts/run_report_repair_stop_v2.py', 'scripts/run_report_recovery_v2.py',
    'src/chanlun_trader/research_factory/report_repair_protocol_v1.py',
    'src/chanlun_trader/research_factory/report_repair_capability_bridge_v1.py',
    'src/chanlun_trader/research_factory/report_recovery_capability_bridge_v2.py',
    'src/chanlun_trader/research_factory/business_validation_protocol_v1.py',
    'src/chanlun_trader/research_factory/campaign_scope_v1.py',
})
RUNTIME_PATHS = REQUIRED_RUNTIME_PATHS | frozenset({
    'reports/continuous_universe_real_acceptance_20261010/session_report_recovery_setup_v2.py',
    'reports/continuous_universe_real_acceptance_20261010/session_next_batch_setup_v3.py',
})


class ReportRecoveryV2(ReportRepairV1):
    replacement_paths = REPLACEMENT_PATHS
    runtime_paths = RUNTIME_PATHS
    required_runtime_paths = REQUIRED_RUNTIME_PATHS

    @staticmethod
    def recovery_identity(authorization_id, parent_manifest_ref):
        return stable_hash({'version': VERSION, 'authorization_id': authorization_id,
                            'parent_manifest_ref': parent_manifest_ref})

    @classmethod
    def _capability_bridge_reference(cls, root, base, job, replacements, reference):
        from .report_recovery_capability_bridge_v2 import ReportRecoveryCapabilityBridgeV2
        _require(reference is not None, 'RECOVERY_CAPABILITY_BRIDGE_REQUIRED')
        bridge = ReportRecoveryCapabilityBridgeV2.read(root, base, reference=reference)
        entries = bridge['record']['summary']['source_replacements']
        _require(sorted([{key: item[key] for key in ('path', 'old_sha256', 'new_sha256')}
                         for item in entries], key=lambda item: item['path']) == replacements,
                 'RECOVERY_CAPABILITY_SOURCE_CONFLICT')
        return deepcopy(bridge['reference'])

    @classmethod
    @_terminal_proof_scope
    def summary(cls, root, *, parent_terminal_ref, grant, source_replacements,
                runtime_source_refs, capability_bridge_ref):
        root = Path(root).resolve(strict=True)
        terminal = read_report_repair_terminal(root, parent_terminal_ref)
        parent = _read(terminal['parent_manifest_ref'], root)['summary']
        _require(isinstance(source_replacements, list)
                 and {Path(item['path']).relative_to(root).as_posix() for item in source_replacements} == REPLACEMENT_PATHS
                 and len(source_replacements) == len(REPLACEMENT_PATHS), 'RECOVERY_SOURCE_ALLOWLIST_REQUIRED')
        # 旧版在停派前已完整重核并获Owner批准；终态证明重核全部原件SHA和旧收费。
        # 沿用这些不可变语义，不在每次新入口反复解析大型核账/输入JSON。
        campaign = ResearchCampaignV1(root, parent['authorization_id'])
        view = campaign.peek_status()
        base = view['base_authorization']
        failure = parent['original_failure']
        _require(view['operations'].get(failure['operation']['operation_id']) == failure['operation']
                 and view['operations'].get(failure['candidate_operation']['operation_id']) == failure['candidate_operation'],
                 'ORIGINAL_FAILURE_LEDGER_CHANGED')
        scope = _read(failure['scope'], root)
        binding = CampaignScopeV1(campaign).resolve(scope['request']['research_binding_ref'], for_dispatch=False)
        _require(binding['candidate_identity'] == parent['rule_identity'] and binding['batch_id'] == parent['batch_id']
                 and stable_hash(base) == parent['base_authorization_identity'], 'RECOVERY_ORIGINAL_SCOPE_CHANGED')
        job = _read(parent['original']['job'], root)
        replacements = cls._sources(root, job, source_replacements)
        runtime = cls._runtime_sources(root, runtime_source_refs)
        cls._grant(base, parent['grant'])
        _require(view['grants'].get(parent['grant']['grant']['grant_id']) == parent['grant'],
                 'RECOVERY_PARENT_INCREMENT_CHANGED')
        bridge_reference = cls._capability_bridge_reference(root, base, job, replacements, capability_bridge_ref)
        captured = {**deepcopy(parent), 'source_replacements': replacements, 'runtime_sources': runtime,
                    'capability_bridge_ref': bridge_reference}
        # 新grant尚未登记：专门Owner审批与原子freeze会登记并立即预留。
        grant_summary(base, grant)
        delta = dict.fromkeys(base['resource_limits'], 0)
        delta['verification_jobs'] = 1
        stages = {stage: dict.fromkeys(delta, 0) for stage in base['stages']}
        stages['EXPLORATION']['verification_jobs'] = 1
        _require(grant.get('resource_limits_delta') == delta and grant.get('stage_limits_delta') == stages
                 and grant.get('max_batches_delta') == 0 and grant.get('max_total_predictive_trials_delta') == 0
                 and grant.get('expires_at') == base['expires_at']
                 and grant['grant_id'] != parent['grant']['grant']['grant_id'], 'RECOVERY_INCREMENT_SCOPE_CONFLICT')
        from .report_recovery_capability_bridge_v2 import ReportRecoveryCapabilityBridgeV2
        bridge = ReportRecoveryCapabilityBridgeV2.read(root, base, reference=capability_bridge_ref)
        expected_context = bridge['record']['summary']['recovery_context']
        stop = _read(terminal['stop_request_ref'], root)['summary']
        repair_id = cls.recovery_identity(parent['authorization_id'], terminal['parent_manifest_ref'])
        _require(expected_context == {'parent_terminal_ref': parent_terminal_ref, 'recovery_id': repair_id,
            'proposal_ref': stop['proposal_ref'], 'human_confirmation_ref': stop['human_confirmation_ref']},
            'RECOVERY_CAPABILITY_PARENT_CONFLICT')
        return {**captured, 'schema_version': SUMMARY_VERSION, 'repair_id': repair_id,
            'parent_manifest_ref': terminal['parent_manifest_ref'], 'parent_terminal_ref': deepcopy(parent_terminal_ref),
            'parent_terminal_identity': terminal['identity'], 'grant': {'grant': deepcopy(grant)},
            'proposal_ref': deepcopy(stop['proposal_ref']), 'human_confirmation_ref': deepcopy(stop['human_confirmation_ref']),
            'maximum_additional_recoveries': 1, 'allowed_action': 'REPORT_RENDER_CONTINUATION_RECOVERY_ONLY',
            'reuse_parent_report_intermediates': False}

    @classmethod
    def _recapture(cls, root, summary):
        _require(isinstance(summary, dict) and summary.get('schema_version') == SUMMARY_VERSION, 'RECOVERY_SUMMARY_INVALID')
        return cls.summary(root, parent_terminal_ref=summary['parent_terminal_ref'], grant=summary['grant']['grant'],
            source_replacements=summary['source_replacements'], runtime_source_refs=summary['runtime_sources'],
            capability_bridge_ref=summary['capability_bridge_ref'])

    @classmethod
    def freeze(cls, root, summary, *, approval_ref, grant_approval_ref):
        root = Path(root).resolve(strict=True)
        campaign = ResearchCampaignV1(root, summary['authorization_id'])
        with campaign._lock():
            current = cls._recapture(root, summary)
            _require(current == summary, 'RECOVERY_SUMMARY_CHANGED')
            view = campaign.peek_status()
            campaign._dispatchable(view, 'EXPLORATION')
            approvals = OwnerApprovalStoreV1(summary['approval_store'])
            approvals.require(approval_ref, summary)
            approvals.require(grant_approval_ref, grant_summary(view['base_authorization'], summary['grant']['grant']))
            path = Path(summary['original']['task']['path']).parent / 'report-repairs' / summary['repair_id'] / 'MANIFEST.json'
            value = {'schema_version': VERSION, 'summary': deepcopy(summary), 'approval_ref': deepcopy(approval_ref),
                     'grant_approval_ref': deepcopy(grant_approval_ref)}
            value['identity'] = stable_hash(value)
            raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).replace('\n', os.linesep).encode('utf-8')
            reference = {'path': str(_path(path, root)), 'sha256': hashlib.sha256(raw).hexdigest()}
            slot = campaign.directory / 'report-recovery-slots' / (summary['repair_id'] + '.json')
            slot_value = {'repair_id': summary['repair_id'], 'parent_manifest_ref': summary['parent_manifest_ref'],
                          'manifest_ref': reference, 'grant_id': summary['grant']['grant']['grant_id']}
            if slot.exists():
                _require(_read(_reference(slot, root), root) == slot_value, 'RECOVERY_SLOT_CONFLICT')
            if path.exists():
                _require(_reference(path, root) == reference, 'RECOVERY_MANIFEST_CONFLICT')
            else:
                _require(summary['grant']['grant']['grant_id'] not in view['grants'], 'RECOVERY_GRANT_ALREADY_ALLOCATED')
                _require(view['remaining']['verification_jobs'] >= 1
                         and view['stage_remaining']['EXPLORATION']['verification_jobs'] >= 1
                         and view['remaining']['wall_seconds'] >= summary['execution_profile']['total_seconds']
                         and view['stage_remaining']['EXPLORATION']['wall_seconds'] >= summary['execution_profile']['total_seconds'],
                         'RESOURCE_LIMIT')
            immutable(slot, slot_value)
            immutable(path, value)
            _require(_reference(path, root) == reference, 'RECOVERY_MANIFEST_BYTES_CONFLICT')
            # 先用已有余量占位；中断最多暂时减少余量，不会暴露新增的专用名额。
            context = cls(root, reference)
            context._bind_summary(current)
            from .universe_compute_governance_v1 import UniverseComputeGovernanceV1
            meter = UniverseComputeGovernanceV1(context.compute_root, context.authority, context.request, 'REPORT',
                campaign_operation=context.campaign_operation, for_dispatch=False)
            campaign.reserve_operation(operation_id=context.campaign_operation['operation_id'], batch_id=summary['batch_id'],
                stage='EXPLORATION', kind='VERIFY', subject_identity=meter.binding['compute_identity'],
                upper_bounds={'verification_jobs': 1, 'wall_seconds': summary['execution_profile']['total_seconds']},
                execution_profile=summary['execution_profile'])
            campaign.add_grant(summary['grant']['grant'], approval_ref=grant_approval_ref)
            context.validate(for_dispatch=False)
            return reference

    @_terminal_proof_scope
    def validate(self, *, for_dispatch=True):
        _require(type(for_dispatch) is bool, 'DISPATCH_MODE_INVALID')
        manifest = _read(self.scope_reference, self.root)
        _require(set(manifest) == {'schema_version', 'summary', 'approval_ref', 'grant_approval_ref', 'identity'}
                 and manifest.get('schema_version') == VERSION and _identity(manifest), 'RECOVERY_MANIFEST_INVALID')
        summary = self._recapture(self.root, manifest['summary'])
        _require(summary == manifest['summary'], 'RECOVERY_SUMMARY_CHANGED')
        self._bind_summary(summary)
        _require(Path(self.scope_reference['path']) == self.output_root / 'MANIFEST.json', 'RECOVERY_MANIFEST_PATH_CONFLICT')
        slot = self.campaign.directory / 'report-recovery-slots' / (self.repair_id + '.json')
        _require(_read(_reference(slot, self.root), self.root) == {'repair_id': self.repair_id,
            'parent_manifest_ref': summary['parent_manifest_ref'], 'manifest_ref': self.scope_reference,
            'grant_id': summary['grant']['grant']['grant_id']}, 'RECOVERY_SLOT_CONFLICT')
        approvals = OwnerApprovalStoreV1(summary['approval_store'])
        approvals.require(manifest['approval_ref'], summary)
        view = self.campaign.peek_status()
        item = {'grant': summary['grant']['grant'], 'approval_ref': manifest['grant_approval_ref']}
        self._grant(view['base_authorization'], item)
        _require(view['grants'].get(item['grant']['grant_id']) == item, 'RECOVERY_INCREMENT_NOT_REGISTERED')
        if for_dispatch:
            self.campaign._dispatchable(view, 'EXPLORATION')
            _require(datetime.now(timezone.utc) < datetime.fromisoformat(summary['expires_at']), 'EXPIRED')
            CampaignScopeV1(self.campaign).resolve(self.request['research_binding_ref'], for_dispatch=True)
            operation = view['operations'].get(self.campaign_operation['operation_id'])
            _require(operation is not None, 'RECOVERY_RESERVATION_NOT_REGISTERED')
            _require(operation['status'] not in ('FAILED', 'UNKNOWN'), 'FAILED_OR_UNKNOWN_NO_RETRY')
            _require(all(old['status'] in ('COMPLETED', 'FAILED') for key, old in view['operations'].items()
                         if old['batch_id'] == summary['batch_id'] and key != self.campaign_operation['operation_id']),
                     'ORIGINAL_OPERATION_UNRESOLVED')
        return self

    def _bind_summary(self, summary):
        """纯构建确定身份；不能代替 validate，freeze仅在批准及固定锁内使用。"""
        self.summary_value, self.summary = summary, deepcopy(summary)
        self.repair_id = summary['repair_id']
        self.campaign = ResearchCampaignV1(self.root, summary['authorization_id'])
        self.task_root = Path(summary['original']['task']['path']).parent
        self.output_root = self.task_root / 'report-repairs' / self.repair_id
        self.compute_root = self.output_root / 'COMPUTE_REPORT'
        self.job_path = Path(summary['original']['job']['path'])
        original = _read(summary['original_failure']['scope'], self.root)
        self.request, self.authority = original['request'], deepcopy(original['authority'])
        self.authority.update(repair_ref=deepcopy(self.scope_reference), repair_budget_identity=stable_hash({
            'version': VERSION, 'repair_id': self.repair_id, 'manifest_ref': self.scope_reference}))
        self.authority['compute_authorization'] = {'preparation_jobs': 1, 'verification_jobs': 1, 'report_jobs': 1}
        self.authority['execution_profiles'] = [deepcopy(summary['execution_profile'])]
        self.campaign_operation = {'root': str(self.root), 'authorization_id': summary['authorization_id'],
                                  'operation_id': 'report_recovery_' + self.repair_id}

    @_terminal_proof_scope
    def control_paused(self):
        """原stop PAUSE只封旧用途；新用途有独立控制链，不能清旧历史。"""
        self.validate(for_dispatch=False)
        terminal = read_report_repair_terminal(self.root, self.summary['parent_terminal_ref'])
        controls = _control(self.job_path, self.root)
        expected = _read(terminal['control_ref'], self.root) if terminal.get('control_ref') else None
        _require((controls[-1] if controls else None) == expected, 'RECOVERY_PARENT_CONTROL_CHANGED')
        head, paused = None, False
        for number, path in enumerate(sorted(self.output_root.glob('CONTROL_*.json')), 1):
            value = _read(_reference(path, self.root), self.root)
            _require(value.get('sequence') == number and value.get('previous_head') == head
                     and value.get('manifest_ref') == self.scope_reference
                     and value.get('event') in ('PAUSE', 'RESUME') and _identity(value, 'event_id'),
                     'RECOVERY_CONTROL_CHAIN_CONFLICT')
            head, paused = value['event_id'], value['event'] == 'PAUSE'
        return paused

    def control(self, event, reason):
        self.validate(for_dispatch=False)
        _require(event in ('PAUSE', 'RESUME') and isinstance(reason, str) and reason, 'RECOVERY_CONTROL_REQUIRED')
        with ObjectiveMutationLock.for_resource(self.output_root / 'CONTROL.lock'):
            paused = self.control_paused()
            if paused == (event == 'PAUSE'):
                return {'paused': paused}
            rows = sorted(self.output_root.glob('CONTROL_*.json'))
            previous = _read(_reference(rows[-1], self.root), self.root) if rows else None
            value = {'sequence': len(rows) + 1, 'previous_head': previous['event_id'] if previous else None,
                'manifest_ref': deepcopy(self.scope_reference), 'event': event, 'reason': reason,
                'recorded_at': datetime.now(timezone.utc).isoformat()}
            value['event_id'] = stable_hash(value)
            _publish(self.output_root / ('CONTROL_' + str(value['sequence']).zfill(6) + '.json'), value, self.root)
            return {'paused': event == 'PAUSE'}

    @_terminal_proof_scope
    def validate_sources(self, job):
        return super().validate_sources(job)

    @_terminal_proof_scope
    def meter(self, *, for_dispatch=True):
        # 构造器只核身份；scope结束后才可start/dispatch/charge。
        return super().meter(for_dispatch=for_dispatch)

    @_terminal_proof_scope
    def verify_receipt(self):
        return super().verify_receipt()

    @_terminal_proof_scope
    def _completion(self, outcome):
        record = super()._completion(outcome)
        record.update(schema_version=RECEIPT_VERSION, status='REPORT_RECOVERED',
                      parent_terminal_ref=deepcopy(self.summary['parent_terminal_ref']))
        return record
