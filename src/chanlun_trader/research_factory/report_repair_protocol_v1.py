"""一次失败报告的专门批准与证据桥；不恢复旧消费，不运行账户。"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from .campaign_scope_v1 import CampaignScopeV1, OwnerApprovalStoreV1, RESOURCES, grant_summary
from .common import stable_hash
from .exploration_governance import immutable
from .research_campaign_v1 import ResearchCampaignV1
from .secure_file_reference_v1 import file_sha256, read_pinned_json, validated_reference_path
from .universe_compute_governance_v1 import UniverseComputeGovernanceV1
from .universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile


VERSION = 'REPORT_REPAIR_MANIFEST_V1'
SUMMARY_VERSION = 'OWNER_REPORT_REPAIR_SUMMARY_V1'
RECEIPT_VERSION = 'REPORT_REPAIR_COMPLETION_V1'
MAX_REPAIR_EVIDENCE_BYTES = 64 * 1024 * 1024
PROTOCOL_SOURCE = 'src/chanlun_trader/research_factory/report_repair_protocol_v1.py'
REPLACEMENT_PATHS = frozenset({
    'scripts/run_strategy_account_v1.py',
    'src/chanlun_trader/research_factory/universe_account_inputs_v1.py',
    'src/chanlun_trader/research_factory/universe_research_report_v2.py',
    'src/chanlun_trader/research_factory/universe_signal_funnel_v1.py',
})
REQUIRED_RUNTIME_PATHS = frozenset({PROTOCOL_SOURCE, 'scripts/run_report_repair_v1.py',
    'src/chanlun_trader/research_factory/business_validation_protocol_v1.py',
    'src/chanlun_trader/research_factory/report_repair_capability_bridge_v1.py',
    'src/chanlun_trader/research_factory/campaign_scope_v1.py'})
RUNTIME_PATHS = REQUIRED_RUNTIME_PATHS | frozenset({
    'reports/continuous_universe_real_acceptance_20261010/session_report_repair_setup_v1.py',
    'reports/continuous_universe_real_acceptance_20261010/session_next_batch_setup_v2.py'})


def _require(condition, reason):
    if not condition:
        raise PermissionError('REPORT_REPAIR_' + reason)


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _path(value, root):
    return validated_reference_path(value, root=root, error_code='REPORT_REPAIR_PATH_INVALID')


def _reference(path, root):
    return {'path': str(_path(path, root)),
            'sha256': file_sha256(path, root=root, error_code='REPORT_REPAIR_ORIGINAL_CHANGED')}


def _read(reference, root):
    return read_pinned_json(reference, root=root, error_code='REPORT_REPAIR_ORIGINAL_CHANGED')


def _read_evidence(reference, root):
    return read_pinned_json(reference, root=root, maximum_bytes=MAX_REPAIR_EVIDENCE_BYTES,
                            error_code='REPORT_REPAIR_ORIGINAL_CHANGED')


def _identity(value, key='identity'):
    return value.get(key) == stable_hash({name: item for name, item in value.items() if name != key})


def _parent_mirror(meter, state, operation):
    _require(operation.get('subject_identity') == meter.binding['compute_identity']
             and operation.get('execution_profile') == meter.profile
             and operation.get('kind') == 'VERIFY'
             and operation.get('stage') == 'EXPLORATION'
             and operation.get('batch_id') == meter.research_binding['batch_id']
             and len(operation.get('segments', [])) == len(state['segments'])
             and operation.get('active_wall_seconds', 0) == state['charged_seconds'], 'PARENT_MIRROR_CONFLICT')
    for parent, row in zip(operation.get('segments', []), state['segments']):
        dispatch, charge = row['dispatch'], row['charge']
        _require(parent.get('segment_number') == dispatch['number']
                 and parent.get('profile_hash') == meter.profile['profile_hash']
                 and all(parent.get(key) == dispatch[key] for key in ('upper_bound_seconds', 'dispatched_at')),
                 'PARENT_DISPATCH_CONFLICT')
        if charge is not None:
            _require(all(parent.get(key) == charge[key] for key in (
                'measured_seconds', 'seconds', 'basis', 'evidence_identity', 'outcome', 'charged_at')),
                'PARENT_CHARGE_CONFLICT')


class ReportRepairV1:
    """只有明确 freeze 写新批准上下文；构造和 validate 都只读。"""

    def __init__(self, root, manifest_ref):
        self.root = Path(root).resolve(strict=True)
        self.scope_reference = deepcopy(manifest_ref)

    @staticmethod
    def repair_identity(authorization_id, operation_id):
        return stable_hash({'schema_version': VERSION, 'authorization_id': authorization_id,
                            'original_report_operation_id': operation_id})

    @classmethod
    def summary(cls, root, *, authorization_id, task_ref, attempt_end_ref,
                source_replacements, grant_id, runtime_source_refs=None, capability_bridge_ref=None):
        """构建无收益字段的完整 Owner 摘要；不会批准、扩额或预留。"""
        root = Path(root).resolve(strict=True)
        campaign = ResearchCampaignV1(root, authorization_id)
        view = campaign.peek_status()
        base = view['base_authorization']
        _require(base.get('scope_policy') is not None, 'CONTINUOUS_SCOPE_REQUIRED')
        task = _read(task_ref, root)
        task_root = _path(Path(task_ref['path']).parent, root / 'reports')
        _require(Path(task_ref['path']) == task_root / 'TASK.json'
                 and _hash(task.get('task_id')) and task_root.name == task['task_id'], 'TASK_PATH_CONFLICT')
        job_path = task_root / 'account' / 'JOB.json'
        _require(task.get('job_path') == str(job_path), 'JOB_PATH_CONFLICT')
        job_ref = {'path': str(job_path), 'sha256': task['job_sha256']}
        job = _read(job_ref, root)
        _require(job.get('root') == str(job_path.parent)
                 and job.get('objective_id') == base['objective_id']
                 and task.get('input_identity') == job.get('input_identity')
                 and task.get('plan_ids') == {name: plan['plan_id'] for name, plan in job['plans'].items()},
                 'FROZEN_JOB_CONFLICT')
        folder = job_path.parent / 'COMPUTE_REPORT'
        scope_ref = _reference(folder / 'SCOPE.json', root)
        scope = _read(scope_ref, root)
        request, authority = scope['request'], scope['authority']
        _require(scope.get('stage') == 'REPORT' and scope.get('job_sha256') == job_ref['sha256']
                 and request.get('version') == 'FULL_UNIVERSE_SUBMISSION_V4'
                 and request.get('phase') == 'EXPLORATION'
                 and authority.get('objective_id') == base['objective_id'], 'ORIGINAL_SCOPE_CONFLICT')
        binding = CampaignScopeV1(campaign).resolve(request['research_binding_ref'], for_dispatch=False)
        preview_ref = _reference(task_root / 'PREVIEW.json', root)
        preview = _read(preview_ref, root)
        _require(task.get('submission_version') == 'FULL_UNIVERSE_SUBMISSION_V4'
                 and preview.get('preview_identity') == task.get('preview_identity')
                 and _identity(preview, 'preview_identity') and preview.get('request') == request
                 and preview.get('rule_identity') == binding['candidate_identity']
                 and all(item.get('factory_kwargs', {}).get('payload') == request.get('rule')
                         for item in job['items'].values()), 'PREVIEW_OR_RULE_CONFLICT')
        from .strategy_submission_v1 import public_rule_factory
        _require(public_rule_factory(request['rule'], request['strategy_id']).rule_identity
                 == binding['candidate_identity'], 'RULE_IDENTITY_CONFLICT')
        profile = execution_profile(SEGMENTED_PROFILE, 504, 'RESEARCH_REPORT')
        _require(request.get('execution_profile') == execution_profile(SEGMENTED_PROFILE, 504)
                 and job.get('resources') == execution_profile(SEGMENTED_PROFILE, 504)
                 and profile in base['execution_profiles'], 'PROFILE_CONFLICT')
        meter = UniverseComputeGovernanceV1(folder, authority, request, 'REPORT', for_dispatch=False)
        _require(meter.campaign_operation is not None
                 and meter.campaign_operation['root'] == str(root)
                 and meter.campaign_operation['authorization_id'] == authorization_id, 'CAMPAIGN_CONFLICT')
        operation_id = meter.campaign_operation['operation_id']
        failed = view['operations'][operation_id]
        state = meter.status()
        _parent_mirror(meter, state, failed)
        _require(failed['kind'] == 'VERIFY' and failed['stage'] == 'EXPLORATION'
                 and failed['batch_id'] == binding['batch_id'] and failed['status'] == 'FAILED'
                 and failed['subject_identity'] == meter.binding['compute_identity']
                 and failed.get('active_segment') is None and state['pending'] is None
                 and state['segments'] and state['segments'][-1]['charge']['outcome'] == 'FAILED',
                 'ORIGINAL_REPORT_NOT_FAILED')
        last = state['segments'][-1]
        number = last['dispatch']['number']
        _require(failed.get('segments') and failed['segments'][-1]['segment_number'] == number
                 and failed['segments'][-1].get('outcome') == 'FAILED'
                 and failed.get('evidence_identity') == last['charge']['evidence_identity']
                 and failed.get('active_wall_seconds') == state['charged_seconds'], 'FAILED_CHARGE_CONFLICT')
        resource_ref = _reference(folder / ('SEGMENT_' + str(number).zfill(6) + '_RESOURCE.json'), root)
        resource = _read(resource_ref, root)
        _require(resource_ref['sha256'] == last['charge']['evidence_identity']
                 and (resource.get('returncode') != 0 or resource.get('timed_out') is True),
                 'FAILED_RESOURCE_CONFLICT')
        attempt = _read(attempt_end_ref, root)
        candidate_id = attempt.get('candidate_id')
        _require(isinstance(candidate_id, str), 'CANDIDATE_REQUIRED')
        candidate = view['operations'].get(candidate_id + '_CANDIDATE', {})
        _require(_identity(attempt) and attempt.get('outcome') == 'FAILED'
                 and attempt.get('batch_id') == binding['batch_id']
                 and attempt.get('rule_identity') == binding['candidate_identity']
                 and attempt.get('evidence', {}).get('task_id') == task['task_id']
                 and candidate.get('kind') == 'CANDIDATE' and candidate.get('status') == 'FAILED'
                 and candidate.get('batch_id') == binding['batch_id']
                 and candidate.get('evidence_identity') == attempt['identity'], 'CANDIDATE_FAILURE_CONFLICT')
        attempt_evidence = attempt['evidence'].get('canonical_files', [])
        _require(isinstance(attempt_evidence, list), 'CANDIDATE_EVIDENCE_INVALID')
        for reference in attempt_evidence:
            _require(reference == _reference(reference['path'], root), 'CANDIDATE_EVIDENCE_CHANGED')
        original = {'task': deepcopy(task_ref), 'job': job_ref, 'preview': preview_ref,
                    'verification': _reference(job_path.parent / 'VERIFICATION.json', root),
                    'results_index': _reference(job_path.parent / 'RESULTS_INDEX.json', root),
                    'inputs': [], 'accounts': {}}
        verification = _read_evidence(original['verification'], root)
        index = _read(original['results_index'], root)
        _require(verification.get('job_sha256') == job_ref['sha256']
                 and verification.get('advance_allowed') is True
                 and set(verification.get('items', {})) == set(job['plans'])
                 and set(index.get('items', {})) == set(job['plans']) and len(job['plans']) == 2,
                 'DUAL_VERIFICATION_REQUIRED')
        costs = set()
        inputs = {}
        for name, plan in job['plans'].items():
            cost = job['items'][name]['backend_options']['costs']
            _require(cost in ('BASE', 'STRESS') and cost not in costs, 'DUAL_COST_REQUIRED')
            costs.add(cost)
            result_ref = _reference(job_path.parent / (name + '_RESULT.json'), root)
            settlement_ref = _reference(job_path.parent / (name + '_SETTLEMENT.json'), root)
            settlement = _read(settlement_ref, root)
            check = verification['items'][name]
            _require(settlement.get('completed') is True and settlement.get('error') is None
                     and settlement.get('result_sha256') == result_ref['sha256']
                     and check.get('status') == 'PASS' and check.get('advance_allowed') is True
                     and check.get('result_sha256') == result_ref['sha256']
                     and check.get('plan_id') == plan['plan_id']
                     and check.get('input_identity') == job['input_identity']
                     and isinstance(check.get('account_audit', {}).get('daily_accounts'), list)
                     and len(check['account_audit']['daily_accounts']) == 504
                     and plan.get('backend', {}).get('initial_cash') == 50000
                     and index['items'][name].get('sha256') == result_ref['sha256']
                     and index['items'][name].get('result') == result_ref['path']
                     and index['items'][name].get('settlement') == settlement_ref['path'],
                     'ACCOUNT_OR_VERIFICATION_CONFLICT')
            original['accounts'][name] = {'cost': cost, 'plan_id': plan['plan_id'],
                                          'result': result_ref, 'settlement': settlement_ref}
            args = job['items'][name]['loader_kwargs']
            for path_key, sha_key in (('path', 'sha256'), ('parent_path', 'parent_sha256')):
                if path_key in args:
                    reference = {'path': args[path_key], 'sha256': args[sha_key]}
                    _read(reference, root)
                    inputs[reference['path']] = reference
        _require(costs == {'BASE', 'STRESS'} and inputs, 'FROZEN_INPUT_REQUIRED')
        original['inputs'] = [inputs[key] for key in sorted(inputs)]
        replacements = cls._sources(root, job, source_replacements)
        from .research_capabilities_v1 import capabilities
        bridge_reference = None
        if capability_bridge_ref is not None or (
                base['scope_policy']['summary']['contract']['capabilities_fingerprint'] != capabilities()['fingerprint']):
            from .report_repair_capability_bridge_v1 import ReportRepairCapabilityBridgeV1
            bridge = ReportRepairCapabilityBridgeV1.read(root, base, reference=capability_bridge_ref)
            replacement = bridge['record']['summary']['source_replacements'][0]
            _require({key: replacement[key] for key in ('path', 'old_sha256', 'new_sha256')} in replacements
                     and replacement['archive_ref'] == _reference(Path(job['root']) / 'source-archive' /
                         (replacement['old_sha256'] + '_' + Path(replacement['path']).name), root),
                     'CAPABILITY_BRIDGE_SOURCE_CONFLICT')
            bridge_reference = deepcopy(bridge['reference'])
        runtime = runtime_source_refs if runtime_source_refs is not None else [
            _reference(root / path, root) for path in sorted(REQUIRED_RUNTIME_PATHS)]
        runtime = cls._runtime_sources(root, runtime)
        grant_item = view['grants'].get(grant_id)
        _require(grant_item is not None, 'REGISTERED_INCREMENT_REQUIRED')
        cls._grant(base, grant_item)
        repair_id = cls.repair_identity(authorization_id, operation_id)
        failure = {'operation': deepcopy(failed), 'candidate_operation': deepcopy(candidate),
            'attempt_end': deepcopy(attempt_end_ref), 'scope': scope_ref,
            'compute_start': _reference(folder / 'COMPUTE_START.json', root),
            'dispatch': _reference(folder / ('COMPUTE_SEGMENT_' + str(number).zfill(6) + '_DISPATCH.json'), root),
            'charge': _reference(folder / ('COMPUTE_SEGMENT_' + str(number).zfill(6) + '_CHARGE.json'), root),
            'resource': resource_ref, 'candidate_evidence': deepcopy(attempt_evidence)}
        return {'schema_version': SUMMARY_VERSION, 'root': os.path.normcase(str(root)),
            'authorization_id': authorization_id, 'objective_id': base['objective_id'],
            'base_authorization_identity': stable_hash(base), 'scope_hash': base['scope_policy']['scope_hash'],
            'approval_store': base['scope_policy']['approval_store'], 'batch_id': binding['batch_id'],
            'task_id': task['task_id'], 'candidate_id': candidate_id,
            'rule_identity': binding['candidate_identity'], 'input_identity': job['input_identity'],
            'repair_id': repair_id, 'original_failure': failure, 'original': original,
            'source_hashes_identity': stable_hash(job['source_hashes']), 'source_replacements': replacements,
            'runtime_sources': runtime, 'grant': deepcopy(grant_item),
            'capability_bridge_ref': bridge_reference,
            'execution_profile': profile, 'expires_at': base['expires_at'],
            'maximum_repairs': 1, 'allowed_action': 'REPORT_ONLY', 'replay_account': False,
            'replay_verification': False}

    @staticmethod
    def _grant(base, item):
        grant = item['grant']
        delta = dict.fromkeys(RESOURCES, 0)
        delta['verification_jobs'] = 1
        stages = {stage: dict.fromkeys(RESOURCES, 0) for stage in base['stages']}
        _require('EXPLORATION' in stages, 'EXPLORATION_SCOPE_REQUIRED')
        stages['EXPLORATION']['verification_jobs'] = 1
        _require(grant.get('resource_limits_delta') == delta and grant.get('stage_limits_delta') == stages
                 and grant.get('max_batches_delta') == 0 and grant.get('max_total_predictive_trials_delta') == 0
                 and grant.get('expires_at') == base['expires_at'], 'INCREMENT_SCOPE_CONFLICT')
        OwnerApprovalStoreV1(base['scope_policy']['approval_store']).require(item['approval_ref'], grant_summary(base, grant))

    @staticmethod
    def _sources(root, job, replacements):
        _require(isinstance(replacements, list), 'SOURCE_REPLACEMENTS_INVALID')
        allowed, seen = {}, set()
        for item in replacements:
            _require(isinstance(item, dict) and set(item) == {'path', 'old_sha256', 'new_sha256'},
                     'SOURCE_REPLACEMENTS_INVALID')
            path = _path(item['path'], root)
            _require(path.relative_to(root).as_posix() in REPLACEMENT_PATHS
                     and str(path) not in seen and _hash(item['new_sha256'])
                     and item['new_sha256'] != item['old_sha256']
                     and job['source_hashes'].get(str(path)) == item['old_sha256'], 'SOURCE_REPLACEMENT_OUTSIDE_ALLOWLIST')
            seen.add(str(path)); allowed[str(path)] = item['new_sha256']
        archive = Path(job['root']) / 'source-archive'
        _require(isinstance(job.get('source_hashes'), dict) and job['source_hashes'], 'FROZEN_SOURCES_REQUIRED')
        for source, digest in job['source_hashes'].items():
            _require(_hash(digest), 'SOURCE_HASH_INVALID')
            path = _path(source, root)
            archived = archive / (digest + '_' + path.name)
            _require(_reference(archived, root)['sha256'] == digest, 'SOURCE_ARCHIVE_CHANGED')
            _require(_reference(path, root)['sha256'] == allowed.get(source, digest), 'UNAPPROVED_SOURCE_CHANGED')
        return sorted(deepcopy(replacements), key=lambda item: item['path'])

    @staticmethod
    def _runtime_sources(root, references):
        _require(isinstance(references, list) and references, 'RUNTIME_SOURCE_PINS_REQUIRED')
        paths = set()
        for reference in references:
            _require(isinstance(reference, dict), 'RUNTIME_SOURCE_CHANGED')
            path = _path(reference.get('path', ''), root)
            _require(set(reference) == {'path', 'sha256'} and path.relative_to(root).as_posix() in RUNTIME_PATHS
                     and str(path) not in paths and reference == _reference(path, root), 'RUNTIME_SOURCE_CHANGED')
            paths.add(str(path))
        _require(all(str(root / path) in paths for path in REQUIRED_RUNTIME_PATHS),
            'PROTOCOL_SOURCE_PIN_REQUIRED')
        return sorted(deepcopy(references), key=lambda item: item['path'])

    @classmethod
    def freeze(cls, root, summary, *, approval_ref):
        root = Path(root).resolve(strict=True)
        campaign = ResearchCampaignV1(root, summary['authorization_id'])
        with campaign._lock():
            current = cls._recapture(root, summary)
            _require(current == summary, 'SUMMARY_CHANGED')
            campaign._dispatchable(campaign.peek_status(), 'EXPLORATION')
            _require(datetime.now(timezone.utc) < datetime.fromisoformat(current['expires_at']), 'EXPIRED')
            scope = _read(current['original_failure']['scope'], root)
            CampaignScopeV1(campaign).resolve(scope['request']['research_binding_ref'], for_dispatch=True)
            OwnerApprovalStoreV1(current['approval_store']).require(approval_ref, current)
            value = {'schema_version': VERSION, 'summary': current, 'approval_ref': deepcopy(approval_ref)}
            value['identity'] = stable_hash(value)
            task_root = Path(current['original']['task']['path']).parent
            path = task_root / 'report-repairs' / current['repair_id'] / 'MANIFEST.json'
            _path(path, root)
            raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).replace('\n', os.linesep).encode('utf-8')
            reference = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}
            slot = campaign.directory / 'report-repair-slots' / (current['repair_id'] + '.json')
            _path(slot, root)
            immutable(slot, {'repair_id': current['repair_id'], 'manifest_ref': reference})
            immutable(path, value)
            _require(_reference(path, root) == reference, 'MANIFEST_BYTES_CONFLICT')
            return reference

    @classmethod
    def _recapture(cls, root, summary):
        _require(isinstance(summary, dict) and summary.get('schema_version') == SUMMARY_VERSION, 'SUMMARY_INVALID')
        return cls.summary(root, authorization_id=summary['authorization_id'],
            task_ref=summary['original']['task'], attempt_end_ref=summary['original_failure']['attempt_end'],
            source_replacements=summary['source_replacements'], grant_id=summary['grant']['grant']['grant_id'],
            runtime_source_refs=summary['runtime_sources'], capability_bridge_ref=summary['capability_bridge_ref'])

    def validate(self, *, for_dispatch=True):
        _require(type(for_dispatch) is bool, 'DISPATCH_MODE_INVALID')
        manifest = _read(self.scope_reference, self.root)
        _require(manifest.get('schema_version') == VERSION and _identity(manifest)
                 and set(manifest) == {'schema_version', 'summary', 'approval_ref', 'identity'}, 'MANIFEST_INVALID')
        summary = manifest['summary']
        # 先比已批准原件字节；不要让损坏 CHARGE 在下游解析时变成偶然 KeyError。
        originals = summary['original']
        refs = [originals[key] for key in ('task', 'job', 'preview', 'verification', 'results_index')]
        refs.extend(originals['inputs'])
        for account in originals['accounts'].values():
            refs.extend((account['result'], account['settlement']))
        failure = summary['original_failure']
        refs.extend(failure[key] for key in ('attempt_end', 'scope', 'compute_start', 'dispatch', 'charge', 'resource'))
        refs.extend(failure['candidate_evidence'])
        for reference in refs:
            _require(reference == _reference(reference['path'], self.root), 'ORIGINAL_CHANGED')
        current = self._recapture(self.root, summary)
        _require(current == summary, 'SUMMARY_CHANGED')
        self.summary_value = current
        self.summary = deepcopy(current)
        self.repair_id = current['repair_id']
        self.campaign = ResearchCampaignV1(self.root, current['authorization_id'])
        self.task_root = Path(current['original']['task']['path']).parent
        self.output_root = self.task_root / 'report-repairs' / self.repair_id
        self.compute_root = self.output_root / 'COMPUTE_REPORT'
        self.job_path = Path(current['original']['job']['path'])
        _require(Path(self.scope_reference['path']) == self.output_root / 'MANIFEST.json', 'MANIFEST_PATH_CONFLICT')
        slot = self.campaign.directory / 'report-repair-slots' / (self.repair_id + '.json')
        _require(_read(_reference(slot, self.root), self.root)
                 == {'repair_id': self.repair_id, 'manifest_ref': self.scope_reference}, 'SLOT_CONFLICT')
        OwnerApprovalStoreV1(current['approval_store']).require(manifest['approval_ref'], current)
        scope = _read(current['original_failure']['scope'], self.root)
        self.request = scope['request']
        self.authority = deepcopy(scope['authority'])
        self.authority.update(repair_ref=deepcopy(self.scope_reference),
            repair_budget_identity=stable_hash({'repair_id': self.repair_id, 'manifest_ref': self.scope_reference}))
        self.authority['compute_authorization'] = {'preparation_jobs': 1, 'verification_jobs': 1, 'report_jobs': 1}
        # 旧计算 schema 要求三个正整数；只保留 REPORT profile，另两用途仍无派发权限。
        self.authority['execution_profiles'] = [deepcopy(current['execution_profile'])]
        self.campaign_operation = {'root': str(self.root), 'authorization_id': current['authorization_id'],
                                  'operation_id': 'report_repair_' + self.repair_id}
        view = self.campaign.peek_status()
        operation = view['operations'].get(self.campaign_operation['operation_id'])
        if for_dispatch:
            self.campaign._dispatchable(view, 'EXPLORATION')
            _require(datetime.now(timezone.utc) < datetime.fromisoformat(current['expires_at']), 'EXPIRED')
            CampaignScopeV1(self.campaign).resolve(self.request['research_binding_ref'], for_dispatch=True)
            _require(operation is None or operation['status'] not in ('FAILED', 'UNKNOWN'), 'FAILED_OR_UNKNOWN_NO_RETRY')
            _require(all(old['status'] in ('COMPLETED', 'FAILED') for key, old in view['operations'].items()
                         if old['batch_id'] == current['batch_id'] and key != self.campaign_operation['operation_id']),
                     'ORIGINAL_OPERATION_UNRESOLVED')
            if operation is None:
                required = {'verification_jobs': 1, 'wall_seconds': current['execution_profile']['total_seconds']}
                _require(all(view['remaining'][key] >= amount and view['stage_remaining']['EXPLORATION'][key] >= amount
                             for key, amount in required.items()), 'RESOURCE_LIMIT')
        return self

    def validate_sources(self, job):
        self.validate(for_dispatch=False)
        _require(job == _read(self.summary_value['original']['job'], self.root), 'JOB_CHANGED')
        self._sources(self.root, job, self.summary_value['source_replacements'])
        return True

    def meter(self, *, for_dispatch=True):
        self.validate(for_dispatch=for_dispatch)
        return UniverseComputeGovernanceV1(self.compute_root, self.authority, self.request, 'REPORT',
            campaign_operation=self.campaign_operation, for_dispatch=for_dispatch)

    def _completion(self, outcome):
        self.validate(for_dispatch=False)
        meter = self.meter(for_dispatch=False)
        state = meter.status()
        operation = self.campaign.peek_status()['operations'].get(self.campaign_operation['operation_id'], {})
        _parent_mirror(meter, state, operation)
        _require(operation.get('status') == 'COMPLETED'
                 and operation.get('subject_identity') == meter.binding['compute_identity']
                 and state['pending'] is None and state['segments']
                 and state['segments'][-1]['charge']['outcome'] == 'COMPLETED', 'COMPLETION_NOT_PROVEN')
        reports = outcome.get('reports', {})
        _require(outcome.get('task_id') == self.summary_value['task_id']
                 and outcome.get('status') == 'ACCOUNT_VERIFIED'
                 and outcome.get('input_identity') == self.summary_value['input_identity']
                 and outcome.get('rule_identity') == self.summary_value['rule_identity']
                 and outcome.get('phase') == 'EXPLORATION'
                 and outcome.get('strategy_qualified') is not True
                 and outcome.get('verification') == _read_evidence(self.summary_value['original']['verification'], self.root)
                 and set(reports) == set(self.summary_value['original']['accounts']), 'OUTCOME_CONFLICT')
        report_refs = {}
        for name, reference in reports.items():
            proofs = []
            output = self.compute_root / ('RESULT_' + name + '.json')
            output_ref = _reference(output, self.root)
            worker_output = _read(output_ref, self.root)
            _require(worker_output.get('member') == name and all(
                worker_output.get(key) == reference.get(key) for key in (
                    'research_report', 'research_report_sha256', 'funnel', 'funnel_sha256')),
                'WORKER_REPORT_BINDING_CONFLICT')
            for row in state['segments']:
                charge = row['charge']
                if charge is None or charge['basis'] != 'MEASURED_ACTIVE_WALL_SECONDS' or charge['outcome'] == 'FAILED':
                    continue
                prefix = 'SEGMENT_' + str(row['dispatch']['number']).zfill(6)
                record_ref = _reference(self.compute_root / (prefix + '_STATUS.json'), self.root)
                resource_ref = _reference(self.compute_root / (prefix + '_RESOURCE.json'), self.root)
                record, resource = _read(record_ref, self.root), _read(resource_ref, self.root)
                if (record.get('state') == 'COMPLETED' and record.get('member') == name
                        and record.get('dispatch_id') == row['dispatch']['dispatch_id']
                        and record.get('result_sha256') == output_ref['sha256']
                        and resource_ref['sha256'] == charge['evidence_identity']
                        and resource.get('returncode') == 0 and resource.get('timed_out') is False):
                    proofs.append({'status': record_ref, 'resource': resource_ref})
            _require(proofs, 'MEMBER_COMPLETION_NOT_PROVEN')
            normalized = {}
            for key, suffix in (('research_report', '_RESEARCH_REPORT.json'), ('final_report', '_REPORT.json'),
                                ('funnel', '_SIGNAL_FUNNEL.json')):
                expected = self.output_root / (name + suffix)
                _require(reference.get(key) == str(expected), 'REPORT_PATH_CONFLICT')
                pinned = {'path': str(expected), 'sha256': reference.get(key + '_sha256')}
                _require(_reference(expected, self.root) == pinned, 'REPORT_CHANGED')
                normalized[key] = pinned
            report_refs[name] = {'output': output_ref, 'reports': normalized, 'worker_proofs': proofs}
        # 最终汇总在成员 RESULT 之后产生，必须由最后一个成功受限段另行锚定。
        last = state['segments'][-1]
        prefix = 'SEGMENT_' + str(last['dispatch']['number']).zfill(6)
        status_ref = _reference(self.compute_root / (prefix + '_STATUS.json'), self.root)
        resource_ref = _reference(self.compute_root / (prefix + '_RESOURCE.json'), self.root)
        status, resource = _read(status_ref, self.root), _read(resource_ref, self.root)
        final_ref = _reference(self.compute_root / 'FINAL_REPORTS.json', self.root)
        _require(last['charge']['basis'] == 'MEASURED_ACTIVE_WALL_SECONDS'
                 and status.get('state') == 'COMPLETED'
                 and status.get('dispatch_id') == last['dispatch']['dispatch_id']
                 and status.get('final_reports_sha256') == final_ref['sha256']
                 and resource_ref['sha256'] == last['charge']['evidence_identity']
                 and type(resource.get('returncode')) is int and resource['returncode'] == 0
                 and resource.get('timed_out') is False, 'FINAL_REPORT_WORKER_PROOF_REQUIRED')
        final = _read(final_ref, self.root)
        expected_final = {'job_sha256': self.summary_value['original']['job']['sha256'],
            'repair_id': self.repair_id, 'reports': {name: {key: reference[key] for key in
                ('final_report', 'final_report_sha256')} for name, reference in reports.items()}}
        _require(final == expected_final, 'FINAL_REPORT_BINDING_CONFLICT')
        return {'schema_version': RECEIPT_VERSION, 'status': 'REPORT_REPAIRED',
            'repair_id': self.repair_id, 'manifest_ref': deepcopy(self.scope_reference),
            'original_failure': deepcopy(self.summary_value['original_failure']),
            'source_identity': stable_hash({'source_replacements': self.summary_value['source_replacements'],
                                            'runtime_sources': self.summary_value['runtime_sources']}),
            'compute': {'binding': meter.binding, 'operation': operation,
                        'terminal_charge': deepcopy(state['segments'][-1]['charge'])},
            'report_refs': report_refs, 'final_reports': {'manifest': final_ref,
                'status': status_ref, 'resource': resource_ref}, 'outcome': deepcopy(outcome)}

    def receipt(self, outcome):
        record = self._completion(outcome)
        record['identity'] = stable_hash(record)
        path = self.output_root / 'REPAIR_COMPLETION.json'
        _path(path, self.root)
        immutable(path, record)
        return record

    def verify_receipt(self):
        self.validate(for_dispatch=False)
        path = self.output_root / 'REPAIR_COMPLETION.json'
        record = _read_evidence(_reference(path, self.root), self.root)
        _require(_identity(record), 'RECEIPT_IDENTITY_CONFLICT')
        expected = self._completion(record.get('outcome', {}))
        expected['identity'] = stable_hash(expected)
        _require(record == expected, 'RECEIPT_CONFLICT')
        return record
