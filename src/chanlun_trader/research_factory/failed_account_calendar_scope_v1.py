"""原失败账户的有限收尾期限；原授权、绑定及计算身份保持不变。"""
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import re

from .common import stable_hash
from .secure_file_reference_v1 import checked_directory_path, read_pinned_json, validated_reference_path
from .universe_compute_governance_v1 import STAGES
from .universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile, segment_allowance


VERSION = 'FAILED_ACCOUNT_CALENDAR_TASK_SCOPE_V1'
RESOURCES = ('candidate_attempts', 'data_experiments', 'account_jobs', 'model_calls',
             'model_tokens', 'model_cost_microunits', 'verification_jobs', 'wall_seconds')
COMPUTE_STAGES = ('VERIFICATION', 'REPORT')


def _require(condition, code):
    if not condition:
        raise PermissionError('FAILED_ACCOUNT_CALENDAR_' + code)


def _time(value):
    result = datetime.fromisoformat(value)
    _require(result.tzinfo is not None, 'TIMEZONE_REQUIRED')
    return result


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _read(reference, root):
    return read_pinned_json(reference, root=root, error_code='FAILED_ACCOUNT_CALENDAR_ORIGINAL_CHANGED')


def _compute_binding(scope, request, stage):
    profile = execution_profile(SEGMENTED_PROFILE, request['execution_profile']['account_sessions'], STAGES[stage][0])
    binding = {'stage': stage, 'profile': profile, 'authorization_identity': scope['authority_identity'],
        'request_identity': scope['request_identity'], 'objective_id': scope['objective_id'],
        'budget_path': str(Path(scope['root']) / 'reports' / 'research_campaigns'
            / stable_hash(scope['authorization_id']) / 'execution_registry.json'),
        'budget_key': scope['authority_identity'], 'expires_at': scope['previous_expires_at'],
        'root': str(Path(scope['original']['job']['path']).parent / ('COMPUTE_' + stage)),
        'campaign_operation': {'root': scope['root'], 'authorization_id': scope['authorization_id'],
            'operation_id': 'compute_' + stage.lower() + '_' + scope['request_identity'][:48]}}
    binding['compute_identity'] = stable_hash(binding)
    return binding


def calendar_task_scope(root, original, mapping, execution_expires_at):
    """只读冻结原件，不进入父账本、Owner 或账户调用链。"""
    root = checked_directory_path(root, error_code='FAILED_ACCOUNT_CALENDAR_ROOT_INVALID')
    _require(isinstance(original, dict) and {'task', 'job', 'preview', 'receipt'} <= set(original), 'ORIGINAL_REQUIRED')
    preview = _read(original['preview'], root)
    request = preview.get('request', {})
    if request.get('version') != 'FULL_UNIVERSE_SUBMISSION_V4':
        return None
    task, job, receipt = (_read(original[key], root) for key in ('task', 'job', 'receipt'))
    task_root = Path(original['task']['path']).parent
    account_root = task_root / 'account'
    source = receipt.get('source', {})
    reference = request.get('research_binding_ref')
    _require(isinstance(reference, dict) and set(reference) == {'binding_id'}
        and _hash(reference['binding_id']) and _hash(preview.get('authority_identity')),
        'AUTHORITY_AND_BINDING_REQUIRED')
    _require(_hash(task.get('task_id')) and task_root.name == task['task_id']
        and Path(original['task']['path']) == task_root / 'TASK.json'
        and Path(original['preview']['path']) == task_root / 'PREVIEW.json'
        and Path(original['job']['path']) == account_root / 'JOB.json'
        and Path(job.get('root', '')) == account_root and Path(task.get('job_path', '')) == account_root / 'JOB.json'
        and task.get('job_sha256') == original['job']['sha256'], 'TASK_OR_JOB_CONFLICT')
    _require(preview.get('preview_identity') == stable_hash({key: value for key, value in preview.items()
            if key != 'preview_identity'}) == task.get('preview_identity')
        and task['task_id'] == stable_hash({'preview': preview['preview_identity'], 'objective_id': job.get('objective_id')})
        and preview.get('parent_binding_identity') == reference['binding_id'], 'PREVIEW_CONFLICT')
    plans = job.get('plans', {})
    _require(isinstance(mapping, dict) and len(mapping) == 2 and set(mapping) == set(plans)
        and all(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,120}', value) for value in mapping.values())
        and len(set(mapping.values())) == 2
        and task.get('plan_ids') == {name: plan['plan_id'] for name, plan in plans.items()}
        and receipt.get('strategy_plans') == plans and source.get('operation_ids') == mapping,
        'ACCOUNT_MAPPING_CONFLICT')
    authorization_id = source.get('authorization_id')
    _require(isinstance(authorization_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,120}', authorization_id)
        and source.get('origin') == 'CAMPAIGN_V1' and source.get('campaign_root') == str(root)
        and source.get('research_binding_ref') == reference
        and source.get('phase') == request.get('phase') == 'EXPLORATION'
        and isinstance(source.get('batch_id'), str) and re.fullmatch(r'[A-Za-z0-9_-]{1,120}', source['batch_id']),
        'CAMPAIGN_SOURCE_CONFLICT')
    budget_path = str(root / 'reports' / 'research_campaigns' / stable_hash(authorization_id) / 'execution_registry.json')
    _require(receipt.get('receipt_id') == stable_hash({key: value for key, value in receipt.items() if key != 'receipt_id'})
        and receipt.get('objective_id') == task.get('objective_id') == job.get('objective_id')
        and receipt.get('input_identity') == task.get('input_identity') == job.get('input_identity')
        and receipt.get('budget_path') == job.get('budget_path') == budget_path
        and Path(original['receipt']['path']).parent == account_root
        and _time(receipt['expires_at']) == _time(source['expires_at']), 'RECEIPT_CONFLICT')
    symbols = request.get('symbols')
    _require(isinstance(symbols, list) and bool(symbols) and all(isinstance(symbol, str) for symbol in symbols)
        and symbols == sorted(set(symbols)) and _time(execution_expires_at) > _time(source['expires_at']),
        'REQUEST_OR_DEADLINE_CONFLICT')
    scope = {'schema_version': VERSION, 'root': str(root), 'task_id': task['task_id'],
        'objective_id': job['objective_id'], 'authorization_id': authorization_id, 'batch_id': source['batch_id'],
        'original': {key: deepcopy(original[key]) for key in ('task', 'job', 'preview', 'receipt')},
        'research_binding_ref': deepcopy(reference), 'request_identity': stable_hash(request),
        'authority_identity': preview['authority_identity'], 'previous_expires_at': source['expires_at'],
        'execution_expires_at': execution_expires_at, 'account_operation_ids': deepcopy(mapping),
        'compute_operations': {}}
    for stage in COMPUTE_STAGES:
        binding = _compute_binding(scope, request, stage)
        units = dict.fromkeys(RESOURCES, 0)
        units.update(verification_jobs=1, wall_seconds=binding['profile']['total_seconds'])
        scope['compute_operations'][stage] = {'operation_id': binding['campaign_operation']['operation_id'],
            'batch_id': scope['batch_id'], 'stage': source['phase'], 'kind': 'VERIFY',
            'subject_identity': binding['compute_identity'], 'upper_bounds': units, 'cost_bound_evidence': None,
            'hypothesis_identity': binding['compute_identity'], 'execution_profile': deepcopy(binding['profile'])}
    return scope


def calendar_operation_deadline(authorization, operations, operation):
    """完整父重放视图中的精确收尾用途；无 IO，不扩大原授权。"""
    from .run_budget import effective_execution_status
    original = authorization['expires_at']
    if not isinstance(operation, dict) or operation.get('kind') != 'VERIFY':
        return original
    existing = operations.get(operation.get('operation_id'))
    if existing is not None and existing != operation:
        return original
    for account in operations.values():
        scope = account.get('calendar_task_scope')
        if not isinstance(scope, dict) or scope.get('schema_version') != VERSION:
            continue
        candidates = scope.get('compute_operations', {})
        canonical = next((value for value in candidates.values()
            if isinstance(value, dict) and value.get('operation_id') == operation.get('operation_id')), None)
        if canonical is None or any(operation.get(key) != value for key, value in canonical.items()):
            continue
        if existing is None and set(operation) != set(canonical):
            continue
        mapping = scope.get('account_operation_ids', {})
        if (scope.get('authorization_id') != authorization.get('authorization_id')
                or scope.get('objective_id') != authorization.get('objective_id')
                or os.path.normcase(scope.get('root', '')) != authorization.get('root')
                or _time(scope['previous_expires_at']) != _time(original)
                or len(mapping) != 2 or len(set(mapping.values())) != 2
                or set(candidates) != set(COMPUTE_STAGES)):
            continue
        proof = account.get('failed_account_recovery_proof')
        if (not isinstance(proof, dict) or set(proof) != {'manifest_ref', 'recovery_id'}
                or _time(scope['execution_expires_at']) <= _time(original)):
            continue
        pair = [operations.get(operation_id, {}) for operation_id in mapping.values()]
        if any(item.get('kind') != 'ACCOUNT' or item.get('batch_id') != scope['batch_id']
                or item.get('stage') != 'EXPLORATION' or item.get('calendar_task_scope') != scope
                or item.get('execution_expires_at') != scope['execution_expires_at']
                or item.get('failed_account_recovery_proof') != proof or item.get('active_segment') is not None
                or item.get('resource_overrun') or effective_execution_status(item) != 'COMPLETED' for item in pair):
            continue
        return scope['execution_expires_at']
    return original


class FailedAccountCalendarContextV1:
    """只保存原 JOB locator；每个授权调用均重新验证完整恢复及已安装父投影。"""
    __slots__ = ('root', 'job_ref')

    def __init__(self, root, job_ref):
        self.root = checked_directory_path(root, error_code='FAILED_ACCOUNT_CALENDAR_ROOT_INVALID')
        self.job_ref = deepcopy(job_ref)

    @classmethod
    def from_job(cls, root, job, *, for_dispatch=True):
        from .failed_account_recovery_v1 import FailedAccountRecoveryV1
        recovery = FailedAccountRecoveryV1.from_job(job, for_dispatch=False)
        if recovery is None or 'calendar_deadline_extension' not in recovery.summary_value:
            return None
        _require(recovery.root == Path(root) and recovery.summary_value.get('calendar_task_scope') is not None,
            'RECOVERY_SCOPE_REQUIRED')
        context = cls(root, recovery.summary_value['original']['job'])
        FailedAccountCalendarContextV1._fresh(context, for_dispatch=for_dispatch)
        return context

    def _fresh(self, *, for_dispatch):
        _require(type(self) is FailedAccountCalendarContextV1 and type(for_dispatch) is bool, 'SIGNED_CONTEXT_REQUIRED')
        from .failed_account_recovery_v1 import FailedAccountRecoveryV1
        from .research_campaign_v1 import ResearchCampaignV1
        job = _read(self.job_ref, self.root)
        recovery = FailedAccountRecoveryV1.from_job(job, for_dispatch=for_dispatch)
        _require(type(recovery) is FailedAccountRecoveryV1 and recovery.root == self.root
            and recovery.summary_value['original']['job'] == self.job_ref, 'RECOVERY_CHANGED')
        summary = recovery.summary_value
        calendar = summary.get('calendar_deadline_extension')
        _require(isinstance(calendar, dict), 'DEADLINE_REQUIRED')
        scope = calendar_task_scope(self.root, summary['original'],
            {name: row['operation_id'] for name, row in summary['accounts'].items()}, calendar['expires_at'])
        _require(scope is not None and scope == summary.get('calendar_task_scope')
            and scope['previous_expires_at'] == summary['expires_at'], 'TASK_SCOPE_CHANGED')
        campaign = ResearchCampaignV1(self.root, summary['authorization_id'])
        view = campaign.peek_status()
        _require(view['authorization']['objective_id'] == scope['objective_id']
            and _time(view['authorization']['expires_at']) == _time(scope['previous_expires_at']), 'AUTHORIZATION_CHANGED')
        for operation_id in scope['account_operation_ids'].values():
            operation = view['operations'].get(operation_id, {})
            _require(operation.get('kind') == 'ACCOUNT' and operation.get('batch_id') == scope['batch_id']
                and operation.get('stage') == 'EXPLORATION' and operation.get('failed_account_recovery_proof') == recovery.proof()
                and operation.get('calendar_task_scope') == scope
                and operation.get('execution_expires_at') == scope['execution_expires_at'], 'INSTALLATION_REQUIRED')
            if for_dispatch:
                campaign._dispatchable(view, operation['stage'], operation=operation)
        return recovery, scope, campaign, view

    def require_binding(self, campaign, reference, binding, *, for_dispatch=True):
        _, scope, _, _ = FailedAccountCalendarContextV1._fresh(self, for_dispatch=for_dispatch)
        _require(campaign.root == self.root and campaign.authorization_id == scope['authorization_id']
            and reference == scope['research_binding_ref'] and binding.get('binding_id') == reference['binding_id']
            and binding.get('batch_id') == scope['batch_id'] and binding.get('phase') == 'EXPLORATION'
            and binding.get('expires_at') == scope['previous_expires_at'], 'BINDING_CONFLICT')
        return scope['execution_expires_at']

    def require_authority(self, authority, request, *, for_dispatch=True, without_symbols=False):
        _, scope, _, _ = FailedAccountCalendarContextV1._fresh(self, for_dispatch=for_dispatch)
        original_request = _read(scope['original']['preview'], self.root)['request']
        if without_symbols:
            original_request = {key: value for key, value in original_request.items() if key != 'symbols'}
        _require(request == original_request and stable_hash(authority) == scope['authority_identity'],
            'AUTHORITY_OR_REQUEST_CONFLICT')
        return scope['execution_expires_at']

    def require_compute(self, meter, *, for_dispatch):
        _, scope, campaign, view = FailedAccountCalendarContextV1._fresh(self, for_dispatch=for_dispatch)
        _require(meter.stage in COMPUTE_STAGES and stable_hash(meter.authority) == scope['authority_identity']
            and meter.request == _read(scope['original']['preview'], self.root)['request'], 'COMPUTE_REQUEST_CONFLICT')
        binding = _compute_binding(scope, meter.request, meter.stage)
        _require(meter.binding == binding and meter.campaign_operation == binding['campaign_operation']
            and str(meter.root) == binding['root'] and meter.profile == binding['profile']
            and meter.budget_key == binding['budget_key'] and str(meter.budget_path) == binding['budget_path'],
            'COMPUTE_IDENTITY_CONFLICT')
        research_binding = meter.research_binding
        _require(isinstance(research_binding, dict)
            and research_binding.get('binding_id') == scope['research_binding_ref']['binding_id']
            and research_binding.get('phase') == 'EXPLORATION' and research_binding.get('batch_id') == scope['batch_id']
            and research_binding.get('expires_at') == scope['previous_expires_at'], 'BINDING_CONFLICT')
        operation = view['operations'].get(binding['campaign_operation']['operation_id'],
            scope['compute_operations'][meter.stage])
        canonical = scope['compute_operations'][meter.stage]
        _require(all(operation.get(key) == value for key, value in canonical.items()), 'COMPUTE_OPERATION_CONFLICT')
        deadline = calendar_operation_deadline(view['authorization'], view['operations'], operation)
        if for_dispatch:
            _require(deadline == scope['execution_expires_at'], 'ACCOUNTS_NOT_COMPLETED')
            campaign._dispatchable(view, operation['stage'], operation=operation)
        return deadline, canonical


def _calendar_compute_governance(root, authority, request, stage, *, calendar_context,
        campaign_operation, for_dispatch):
    from .universe_compute_governance_v1 import UniverseComputeGovernanceV1

    class CalendarComputeGovernanceV1(UniverseComputeGovernanceV1):
        def __init__(self):
            _require(type(calendar_context) is FailedAccountCalendarContextV1 and type(for_dispatch) is bool,
                'SIGNED_CONTEXT_REQUIRED')
            super().__init__(root, authority, request, stage, campaign_operation=campaign_operation, for_dispatch=False)
            self.for_dispatch = for_dispatch
            self._calendar_context = calendar_context
            FailedAccountCalendarContextV1.require_compute(calendar_context, self, for_dispatch=for_dispatch)

        def _active_deadline(self):
            _require(self.for_dispatch, 'COMPUTE_RECONCILIATION_ONLY')
            if (self.root / 'REVOKED.json').exists():
                raise PermissionError('UNIVERSE_COMPUTE_REVOKED')
            deadline, canonical = FailedAccountCalendarContextV1.require_compute(
                self._calendar_context, self, for_dispatch=True)
            if datetime.now(timezone.utc) >= _time(deadline):
                raise PermissionError('UNIVERSE_COMPUTE_AUTHORIZATION_EXPIRED')
            from .research_campaign_v1 import ResearchCampaignV1
            ref = self.campaign_operation
            campaign = ResearchCampaignV1(ref['root'], ref['authorization_id'])
            view = campaign.status()
            operation = view['operations'].get(ref['operation_id'], canonical)
            _require(calendar_operation_deadline(view['authorization'], view['operations'], operation) == deadline,
                'COMPUTE_OPERATION_CONFLICT')
            campaign._dispatchable(view, operation['stage'], operation=operation)
            return deadline

        def active(self):
            self._active_deadline()
            return self.binding

        def dispatch(self):
            from .exploration_governance import immutable
            with self.lock():
                deadline = self._active_deadline()
                state = self.status()
                if state['pending'] or (state['segments'] and state['segments'][-1]['charge']['outcome'] in ('COMPLETED', 'FAILED')):
                    raise PermissionError('UNIVERSE_COMPUTE_PENDING_OR_TERMINAL')
                upper = segment_allowance(self.profile, state['charged_seconds'],
                    seconds_to_expiry=(_time(deadline) - datetime.now(timezone.utc)).total_seconds())
                value = {'number': state['next_segment_number'], 'compute_identity': self.binding['compute_identity'],
                    'upper_bound_seconds': upper, 'previous_head': state['head'],
                    'dispatched_at': datetime.now(timezone.utc).isoformat()}
                value['dispatch_id'] = stable_hash(value)
                from .research_campaign_v1 import ResearchCampaignV1
                ref = self.campaign_operation
                parent = ResearchCampaignV1(ref['root'], ref['authorization_id']).start_execution_segment(ref['operation_id'],
                    segment_number=value['number'], profile=self.profile, upper_bound_seconds=upper)
                value['dispatched_at'] = parent['dispatched_at']
                value['dispatch_id'] = stable_hash({key: item for key, item in value.items() if key != 'dispatch_id'})
                immutable(self.root / ('COMPUTE_SEGMENT_' + str(value['number']).zfill(6) + '_DISPATCH.json'), value)
                return value

    return CalendarComputeGovernanceV1()


def calendar_compute_governance_v1(root, authority, request, stage, *, campaign_operation=None, for_dispatch=True):
    """普通路径直接构造原类；只有精确原任务收尾走有限期限适配。"""
    context = None
    if request.get('version') == 'FULL_UNIVERSE_SUBMISSION_V4' and stage in COMPUTE_STAGES:
        folder = Path(root).absolute()
        campaign_ref = authority.get('account_authorization', {}).get('campaign_ref')
        if folder.name == 'COMPUTE_' + stage and isinstance(campaign_ref, dict):
            campaign_root = checked_directory_path(campaign_ref.get('root'), error_code='FAILED_ACCOUNT_CALENDAR_ROOT_INVALID')
            job_path = validated_reference_path(folder.parent / 'JOB.json', root=campaign_root,
                error_code='FAILED_ACCOUNT_CALENDAR_JOB_PATH_INVALID')
            if job_path.is_file():
                from .secure_file_reference_v1 import read_file_bytes
                import json
                job = json.loads(read_file_bytes(job_path, root=campaign_root, error_code='FAILED_ACCOUNT_CALENDAR_JOB_CHANGED'))
                context = FailedAccountCalendarContextV1.from_job(campaign_root, job, for_dispatch=for_dispatch)
    if context is None:
        # 保持原入口的调用时绑定及默认四位置参数，不让适配器改变普通计算入口。
        from .universe_compute_governance_v1 import UniverseComputeGovernanceV1
        options = {}
        if campaign_operation is not None:
            options['campaign_operation'] = campaign_operation
        if for_dispatch is not True:
            options['for_dispatch'] = for_dispatch
        return UniverseComputeGovernanceV1(root, authority, request, stage, **options)
    return _calendar_compute_governance(root, authority, request, stage, calendar_context=context,
        campaign_operation=campaign_operation, for_dispatch=for_dispatch)
