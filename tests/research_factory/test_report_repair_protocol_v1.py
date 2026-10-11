"""有账报告修复的合成证据链；不运行行情、账户或真实批准。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory.campaign_scope_v1 import (
    CampaignScopeV1, OwnerApprovalStoreV1, RESOURCES, bind_campaign_scope, grant_summary, scope_summary,
)
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.report_repair_protocol_v1 import (
    MAX_REPAIR_EVIDENCE_BYTES, PROTOCOL_SOURCE, REQUIRED_RUNTIME_PATHS, ReportRepairV1,
    _read, _read_evidence,
)
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
from chanlun_trader.research_factory.strategy_submission_v1 import public_rule_factory
from chanlun_trader.research_factory.universe_compute_governance_v1 import UniverseComputeGovernanceV1
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile
from test_continuous_research_contract_v1 import build, scope


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return ref(path)


def ref(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def case(tmp_path, request):
    root = tmp_path.resolve()
    variant = getattr(request, 'param', None)
    snapshot = capabilities()
    frozen_scope = scope(snapshot, confirmation=False)
    profiles = [execution_profile(SEGMENTED_PROFILE, 504, purpose) for purpose in (
        'RESEARCH_ACCOUNT', 'RESEARCH_PREPARATION', 'RESEARCH_VERIFICATION', 'RESEARCH_REPORT')]
    frozen_scope['execution_profiles'] = profiles
    contract = build(snapshot, frozen_scope=frozen_scope)
    units = dict.fromkeys(RESOURCES, 10)
    units.update(wall_seconds=288002, verification_jobs=4)
    authorization = {'authorization_id': 'SYNTHETIC_REPORT_REPAIR', 'objective_id': contract['objective_id'],
        'resource_limits': units, 'stages': ['EXPLORATION'],
        'expires_at': (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        'max_batches': 2, 'max_total_predictive_trials': 6, 'max_trials_per_batch': 3,
        'max_hypotheses_per_batch': 1, 'max_candidates_per_batch': 1, 'execution_profiles': profiles}
    capability = object()
    approvals = OwnerApprovalStoreV1(root / 'protected_owner', owner_capability=capability)
    total_summary = scope_summary(root, authorization, contract, {'EXPLORATION': units})
    approval = approvals.approve(total_summary, approver='SYNTHETIC_OWNER', capability=capability)
    authorization = bind_campaign_scope(root, authorization, contract=contract, stage_limits={'EXPLORATION': units},
                                        approvals=approvals, approval_ref=approval)
    campaign = ResearchCampaignV1.create(root, authorization)
    rule = deepcopy(snapshot['examples']['multi_indicator_ranked'])
    candidate = 'SYNTHETIC_CANDIDATE'
    rule_identity = public_rule_factory(rule, candidate).rule_identity
    route = contract['scope']['data_routes']['EXPLORATION']
    delegated = {'universe_id': frozen_scope['universe_id'], 'universe_hash': frozen_scope['universe_hash'],
        'rule_version': frozen_scope['rule_version'], 'phase': 'EXPLORATION',
        'initial_cash': 50000, 'max_positions': 5, 'cost_profiles': frozen_scope['cost_profiles'],
        'execution_profiles': profiles, 'dataset_id': route['dataset_id'], 'dataset_hash': route['dataset_hash'],
        'feature_start': '2022-04-07', 'account_start': '2022-07-06', 'account_end': '2024-07-31'}
    binding = CampaignScopeV1(campaign).delegate(batch_id='batch_001', candidate_identity=rule_identity,
        phase='EXPLORATION', request=delegated, expires_at=authorization['expires_at'])
    request = {'version': 'FULL_UNIVERSE_SUBMISSION_V4', 'phase': 'EXPLORATION', 'rule': rule,
        'strategy_id': candidate, 'research_binding_ref': binding, 'execution_profile': profiles[0]}
    preview = {'request': request, 'rule_identity': rule_identity}
    preview['preview_identity'] = stable_hash(preview)
    task_id = stable_hash({'preview': preview['preview_identity'], 'objective': contract['objective_id']})
    task_root = root / 'reports' / 'public' / task_id
    account = task_root / 'account'
    account.mkdir(parents=True)
    write(task_root / 'PREVIEW.json', preview)
    inputs_ref = write(task_root / 'INPUT.json', {'synthetic': True, 'input_identity': stable_hash('synthetic input')})
    sources = {}
    for relative in ('scripts/run_strategy_account_v1.py',
                     'src/chanlun_trader/research_factory/universe_account_inputs_v1.py',
                     'src/chanlun_trader/research_factory/universe_research_report_v2.py'):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'# SYNTHETIC old source\n')
        digest = ref(path)['sha256']; sources[str(path)] = digest
        archive = account / 'source-archive' / (digest + '_' + path.name)
        archive.parent.mkdir(parents=True, exist_ok=True); archive.write_bytes(path.read_bytes())
    for relative in REQUIRED_RUNTIME_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'# SYNTHETIC new runtime\n')
    items, plans, checks, index = {}, {}, {}, {}
    for cost in ('BASE', 'STRESS'):
        name = candidate + '_' + cost
        items[name] = {'backend_options': {'costs': cost}, 'factory_kwargs': {'payload': rule},
            'loader_kwargs': {**inputs_ref, 'input_identity': stable_hash('synthetic input')}}
        plans[name] = {'plan_id': stable_hash(name), 'runtime': deepcopy(items[name]), 'backend': {'initial_cash': 50000}}
        result_ref = write(account / (name + '_RESULT.json'), {'synthetic': True, 'sessions': 504})
        settlement_ref = write(account / (name + '_SETTLEMENT.json'), {
            'completed': not (variant == 'incomplete_account' and cost == 'STRESS'),
            'error': None, 'result_sha256': result_ref['sha256']})
        index[name] = {'result': result_ref['path'], 'sha256': result_ref['sha256'], 'settlement': settlement_ref['path']}
        checks[name] = {'status': 'PASS', 'advance_allowed': True, 'plan_id': plans[name]['plan_id'],
            'result_sha256': result_ref['sha256'], 'input_identity': stable_hash('synthetic input'),
            'account_audit': {'daily_accounts': [None] * (503 if variant == 'short_account' and cost == 'STRESS' else 504)}}
        if variant == 'verification_fail' and cost == 'STRESS':
            checks[name]['status'] = 'FAIL'
    job = {'root': str(account), 'objective_id': contract['objective_id'], 'plans': plans, 'items': items,
        'input_identity': stable_hash('synthetic input'), 'source_hashes': sources, 'resources': profiles[0]}
    job_ref = write(account / 'JOB.json', job)
    task = {'task_id': task_id, 'job_path': job_ref['path'], 'job_sha256': job_ref['sha256'],
        'input_identity': job['input_identity'], 'plan_ids': {name: plan['plan_id'] for name, plan in plans.items()},
        'submission_version': 'FULL_UNIVERSE_SUBMISSION_V4', 'preview_identity': preview['preview_identity']}
    task_ref = write(task_root / 'TASK.json', task)
    if variant == 'large_verification':
        checks[next(iter(checks))]['account_audit']['synthetic_evidence_padding'] = 'x' * (21 * 1024 * 1024)
    verification_ref = write(account / 'VERIFICATION.json', {
        'job_sha256': job_ref['sha256'], 'advance_allowed': True, 'items': checks})
    write(account / 'RESULTS_INDEX.json', {'items': index})
    authority = {'objective_id': contract['objective_id'], 'budget_path': str(root / 'reports' / 'child_budget.json'),
        'expires_at': authorization['expires_at'],
        'source': {'origin': 'CONTINUOUS_CAMPAIGN_SCOPE_V1', 'research_binding_ref': binding},
        'account_authorization': {'campaign_ref': {'root': str(root), 'authorization_id': campaign.authorization_id}},
        'compute_authorization': {'preparation_jobs': 1, 'verification_jobs': 1, 'report_jobs': 1},
        'execution_profiles': profiles}
    original = UniverseComputeGovernanceV1(account / 'COMPUTE_REPORT', authority, request, 'REPORT')
    write(original.root / 'SCOPE.json', {'authority': authority, 'request': request, 'stage': 'REPORT',
                                        'job_sha256': job_ref['sha256']})
    original.start(); dispatch = original.dispatch()
    resource_ref = write(original.root / 'SEGMENT_000001_RESOURCE.json', {
        'returncode': 3221225477, 'timed_out': False, 'wall_seconds': 12})
    original.charge(dispatch['number'], seconds=12, evidence_identity=resource_ref['sha256'], outcome='FAILED')
    operation_id = candidate + '_CANDIDATE'
    campaign.reserve_operation(operation_id=operation_id, batch_id='batch_001', stage='EXPLORATION',
        kind='CANDIDATE', subject_identity=stable_hash('candidate subject'),
        upper_bounds={'candidate_attempts': 1, 'wall_seconds': 1})
    campaign.start_operation(operation_id)
    attempt = {'candidate_id': candidate, 'batch_id': 'batch_001', 'rule_identity': rule_identity,
        'outcome': 'FAILED', 'evidence': {'task_id': task_id, 'reason': 'SYNTHETIC_REPORT_CRASH',
                                       'canonical_files': [verification_ref]},
        'source': 'CODEX_SESSION_DRIVEN', 'external_session_usage': 'UNKNOWN_EXTERNAL'}
    attempt['identity'] = stable_hash(attempt)
    attempt_ref = write(root / 'reports' / 'session' / 'ATTEMPT_END.json', attempt)
    campaign.settle_operation(operation_id, actual={'candidate_attempts': 1, 'wall_seconds': 1},
                             outcome='FAILED', evidence_identity=attempt['identity'])
    base = campaign.peek_status()['base_authorization']
    delta = dict.fromkeys(RESOURCES, 0); delta['verification_jobs'] = 1
    grant = {'grant_id': 'SYNTHETIC_ONE_REPORT_REPAIR', 'base_authorization_hash': stable_hash(base),
        'resource_limits_delta': delta, 'stage_limits_delta': {'EXPLORATION': delta},
        'max_batches_delta': 0, 'max_total_predictive_trials_delta': 0, 'expires_at': base['expires_at']}
    grant_approval = approvals.approve(grant_summary(base, grant), approver='SYNTHETIC_OWNER', capability=capability)
    campaign.add_grant(grant, approval_ref=grant_approval)
    changed = root / 'scripts/run_strategy_account_v1.py'
    old = sources[str(changed)]; changed.write_bytes(b'# SYNTHETIC approved new runner\n')
    replacements = [{'path': str(changed), 'old_sha256': old, 'new_sha256': ref(changed)['sha256']}]
    args = {'authorization_id': campaign.authorization_id, 'task_ref': task_ref, 'attempt_end_ref': attempt_ref,
            'source_replacements': replacements, 'grant_id': grant['grant_id']}
    return {'root': root, 'campaign': campaign, 'approvals': approvals, 'capability': capability,
            'args': args, 'original': original, 'job': job, 'task': task, 'authority': authority}


def approve(case):
    summary = ReportRepairV1.summary(case['root'], **case['args'])
    approval = case['approvals'].approve(summary, approver='SYNTHETIC_OWNER', capability=case['capability'])
    manifest = ReportRepairV1.freeze(case['root'], summary, approval_ref=approval)
    return ReportRepairV1(case['root'], manifest).validate(), summary, approval


def test_dedicated_owner_and_closed_sources_before_dispatch(case):
    context, summary, _ = approve(case)
    assert context.validate_sources(deepcopy(case['job'])) is True
    assert context.job_path == Path(case['task']['job_path'])
    assert context.output_root == context.task_root / 'report-repairs' / context.repair_id
    assert summary['replay_account'] is False and summary['replay_verification'] is False
    assert summary['maximum_repairs'] == 1
    assert context.campaign_operation['operation_id'] == 'report_repair_' + context.repair_id


def test_missing_or_other_owner_cannot_freeze(case):
    summary = ReportRepairV1.summary(case['root'], **case['args'])
    with pytest.raises(PermissionError, match='OWNER_APPROVAL'):
        ReportRepairV1.freeze(case['root'], summary, approval_ref={'approval_id': 'a' * 64, 'summary_hash': stable_hash(summary)})
    capability = object()
    other = OwnerApprovalStoreV1(case['root'] / 'another_owner', owner_capability=capability)
    approval = other.approve(summary, approver='OTHER_SYNTHETIC_OWNER', capability=capability)
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_NOT_REGISTERED'):
        ReportRepairV1.freeze(case['root'], summary, approval_ref=approval)
    assert not list(Path(case['task']['job_path']).parent.parent.glob('report-repairs/*/MANIFEST.json'))


def test_new_bucket_and_parent_charge_do_not_restore_original_failure(case):
    context, _, _ = approve(case)
    before = case['campaign'].peek_status()
    original_start = (case['original'].root / 'COMPUTE_START.json').read_bytes()
    meter = context.meter(); meter.start(); segment = meter.dispatch()
    meter.charge(segment['number'], seconds=5, evidence_identity='a' * 64, outcome='FAILED')
    after = case['campaign'].peek_status()
    old_id = case['original'].campaign_operation['operation_id']
    assert before['operations'][old_id] == after['operations'][old_id]
    assert after['used']['verification_jobs'] == before['used']['verification_jobs'] + 1
    assert after['used']['wall_seconds'] == before['used']['wall_seconds'] + 5
    assert (case['original'].root / 'COMPUTE_START.json').read_bytes() == original_start
    assert case['original'].budget_key != meter.budget_key
    assert meter.authority['compute_authorization']['report_jobs'] == 1
    with pytest.raises(PermissionError, match='FAILED_OR_UNKNOWN_NO_RETRY'):
        context.meter()
    with pytest.raises(PermissionError, match='RECONCILIATION_ONLY'):
        context.meter(for_dispatch=False).dispatch()


@pytest.mark.parametrize('relative', ['TASK.json', 'account/JOB.json', 'INPUT.json',
    'account/SYNTHETIC_CANDIDATE_BASE_RESULT.json', 'account/SYNTHETIC_CANDIDATE_STRESS_SETTLEMENT.json',
    'account/VERIFICATION.json', 'account/COMPUTE_REPORT/COMPUTE_SEGMENT_000001_CHARGE.json',
    'account/COMPUTE_REPORT/SEGMENT_000001_RESOURCE.json'])
def test_changed_original_cannot_be_repaired(case, relative):
    context, _, _ = approve(case)
    (context.task_root / relative).write_bytes(b'{"synthetic_tampered":true}')
    with pytest.raises((PermissionError, ValueError)):
        context.validate()


def test_source_archive_and_unapproved_current_source_are_required(case):
    context, _, _ = approve(case)
    source = case['root'] / 'src/chanlun_trader/research_factory/universe_account_inputs_v1.py'
    source.write_bytes(b'# unapproved change')
    with pytest.raises(PermissionError, match='UNAPPROVED_SOURCE_CHANGED'):
        context.validate_sources(case['job'])
    source.write_bytes(b'# SYNTHETIC old source\n')
    archive = Path(case['job']['root']) / 'source-archive' / (case['job']['source_hashes'][str(source)] + '_' + source.name)
    archive.write_bytes(b'# corrupt old archive')
    with pytest.raises(PermissionError, match='SOURCE_ARCHIVE_CHANGED'):
        context.validate_sources(case['job'])


def test_allowlist_rejects_budget_source_and_duplicate_replacements(case):
    args = deepcopy(case['args'])
    args['source_replacements'].append(deepcopy(args['source_replacements'][0]))
    with pytest.raises(PermissionError, match='OUTSIDE_ALLOWLIST'):
        ReportRepairV1.summary(case['root'], **args)
    args['source_replacements'] = [{'path': str(case['root'] / 'src/chanlun_trader/research_factory/budget.py'),
        'old_sha256': 'a' * 64, 'new_sha256': 'b' * 64}]
    with pytest.raises(PermissionError, match='OUTSIDE_ALLOWLIST'):
        ReportRepairV1.summary(case['root'], **args)


def test_changed_runtime_closure_requires_new_approval_and_cannot_retarget_slot(case):
    context, _, _ = approve(case)
    source = case['root'] / PROTOCOL_SOURCE
    source.write_bytes(b'# SYNTHETIC new protocol revision')
    with pytest.raises(PermissionError, match='RUNTIME_SOURCE_CHANGED'):
        context.validate()
    summary = ReportRepairV1.summary(case['root'], **case['args'])
    approval = case['approvals'].approve(summary, approver='SYNTHETIC_OWNER', capability=case['capability'])
    with pytest.raises(ValueError, match='IMMUTABLE_CONFLICT'):
        ReportRepairV1.freeze(case['root'], summary, approval_ref=approval)


def test_explicit_increment_may_not_extend_expiry_or_other_units(case):
    base = case['campaign'].peek_status()['base_authorization']
    item = deepcopy(case['campaign'].peek_status()['grants'][case['args']['grant_id']])
    for field in ('account_jobs', 'wall_seconds'):
        changed = deepcopy(item); changed['grant']['resource_limits_delta'][field] = 1
        with pytest.raises(PermissionError, match='INCREMENT_SCOPE_CONFLICT'):
            ReportRepairV1._grant(base, changed)
    changed = deepcopy(item)
    changed['grant']['expires_at'] = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    with pytest.raises(PermissionError, match='INCREMENT_SCOPE_CONFLICT'):
        ReportRepairV1._grant(base, changed)


@pytest.mark.parametrize('action', ['pause', 'revoke'])
def test_stopped_scope_only_allows_existing_pending_settlement(case, action):
    context, _, _ = approve(case)
    meter = context.meter(); meter.start(); segment = meter.dispatch()
    getattr(case['campaign'], action)('SYNTHETIC stop')
    with pytest.raises(PermissionError, match='CAMPAIGN_'):
        context.meter()
    settled = context.meter(for_dispatch=False)
    settled.charge(segment['number'], seconds=3, evidence_identity='d' * 64, outcome='FAILED')
    assert settled.status()['pending'] is None
    assert case['campaign'].peek_status()['operations'][context.campaign_operation['operation_id']]['status'] == 'FAILED'


def test_unknown_does_not_create_free_retry(case):
    context, _, _ = approve(case)
    meter = context.meter(); meter.start(); segment = meter.dispatch()
    case['campaign'].mark_unknown(context.campaign_operation['operation_id'], 'SYNTHETIC receipt lost')
    with pytest.raises(PermissionError, match='FAILED_OR_UNKNOWN_NO_RETRY'):
        context.meter()
    reconciler = context.meter(for_dispatch=False)
    reconciler.charge(segment['number'], seconds=None, outcome='FAILED')
    assert reconciler.status()['charged_seconds'] == segment['upper_bound_seconds']


def test_expiry_blocks_new_dispatch_but_not_historical_reconciliation(case, monkeypatch):
    from chanlun_trader.research_factory import report_repair_protocol_v1 as protocol
    context, _, _ = approve(case)
    meter = context.meter(); meter.start(); segment = meter.dispatch()
    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=3)
    monkeypatch.setattr(protocol, 'datetime', ExpiredClock)
    with pytest.raises(PermissionError, match='EXPIRED'):
        context.validate()
    reconciler = context.meter(for_dispatch=False)
    reconciler.charge(segment['number'], seconds=1, evidence_identity='e' * 64, outcome='FAILED')
    assert reconciler.status()['charged_seconds'] == 1


def test_failed_resource_overrun_is_charged_and_never_authorizes_second_attempt(case):
    context, _, _ = approve(case)
    meter = context.meter(); meter.start(); segment = meter.dispatch()
    with pytest.raises(PermissionError, match='USAGE_EXCEEDS_DISPATCH_BOUND'):
        meter.charge(segment['number'], seconds=901, evidence_identity='f' * 64, outcome='COMPLETED')
    meter.charge(segment['number'], seconds=901, evidence_identity='f' * 64, outcome='FAILED')
    assert meter.status()['charged_seconds'] == 901
    assert case['campaign'].peek_status()['operations'][context.campaign_operation['operation_id']]['actual']['wall_seconds'] == 901
    assert case['campaign'].peek_status()['paused'] is True
    with pytest.raises(PermissionError, match='CAMPAIGN_PAUSED'):
        context.meter()
    case['campaign'].resume('SYNTHETIC only inspect terminal protection')
    with pytest.raises(PermissionError, match='FAILED_OR_UNKNOWN_NO_RETRY'):
        context.meter()
    for stage in ('PREPARATION', 'VERIFICATION'):
        with pytest.raises(PermissionError, match='PROFILE_AND_BUDGET_AUTHORIZATION_REQUIRED'):
            UniverseComputeGovernanceV1(context.compute_root, meter.authority, meter.request, stage,
                                        campaign_operation=context.campaign_operation)


def test_insufficient_remaining_has_no_new_reservation(case):
    context, _, _ = approve(case)
    case['campaign'].reserve_operation(operation_id='SYNTHETIC_OTHER', batch_id='batch_002', stage='EXPLORATION',
        kind='VERIFY', subject_identity='synthetic_other', upper_bounds={'verification_jobs': 1, 'wall_seconds': 270000})
    with pytest.raises(PermissionError, match='RESOURCE_LIMIT'):
        context.meter()
    assert context.campaign_operation['operation_id'] not in case['campaign'].peek_status()['operations']
    assert not (context.compute_root / 'COMPUTE_START.json').exists()


def test_id_is_original_failure_not_grant_and_existing_freeze_is_identical(case):
    context, summary, approval = approve(case)
    assert ReportRepairV1.freeze(case['root'], summary, approval_ref=approval) == context.scope_reference
    original = context.scope_reference
    forged = deepcopy(summary); forged['repair_id'] = stable_hash('second ID')
    second_approval = case['approvals'].approve(forged, approver='SYNTHETIC_OWNER', capability=case['capability'])
    with pytest.raises(PermissionError, match='SUMMARY_CHANGED'):
        ReportRepairV1.freeze(case['root'], forged, approval_ref=second_approval)
    assert context.scope_reference == original


def test_dual_complete_and_verification_pass_are_mandatory(case):
    path = Path(case['task']['job_path']).parent / 'VERIFICATION.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    value['items']['SYNTHETIC_CANDIDATE_STRESS']['status'] = 'FAIL'
    write(path, value)
    # 精确原 attempt 证据先变；即使同步它及 candidate settlement 也不能把 FAIL 当 PASS。
    with pytest.raises(PermissionError, match='CANDIDATE_EVIDENCE_CHANGED'):
        ReportRepairV1.summary(case['root'], **case['args'])


    original = case['args']['attempt_end_ref']
    attempt = json.loads(Path(original['path']).read_text(encoding='utf-8'))
    attempt['evidence']['canonical_files'] = []
    # 本例不改权威失败 operation；更换失败身份同样须拒绝。
    attempt['identity'] = stable_hash({key: value for key, value in attempt.items() if key != 'identity'})
    case['args']['attempt_end_ref'] = write(Path(original['path']), attempt)
    with pytest.raises(PermissionError, match='CANDIDATE_FAILURE_CONFLICT'):
        ReportRepairV1.summary(case['root'], **case['args'])


@pytest.mark.parametrize('case', ['verification_fail', 'incomplete_account', 'short_account'], indirect=True)
def test_real_chain_refuses_nonpass_incomplete_or_under_504_accounts(case):
    with pytest.raises(PermissionError, match='ACCOUNT_OR_VERIFICATION_CONFLICT'):
        ReportRepairV1.summary(case['root'], **case['args'])


@pytest.mark.parametrize('case', [None, 'large_verification'], indirect=True)
def test_receipt_requires_completed_meter_and_fixed_dual_reports(case):
    context, _, _ = approve(case)
    meter = context.meter(); meter.start()
    outcome = {'task_id': context.summary['task_id'], 'status': 'ACCOUNT_VERIFIED', 'strategy_qualified': False,
        'input_identity': context.summary['input_identity'], 'rule_identity': context.summary['rule_identity'],
        'phase': 'EXPLORATION',
        'verification': json.loads(Path(context.summary['original']['verification']['path']).read_text(encoding='utf-8')),
        'reports': {}}
    with pytest.raises(PermissionError, match='COMPLETION_NOT_PROVEN'):
        context.receipt(outcome)
    for index, name in enumerate(context.summary['original']['accounts']):
        dispatch = meter.dispatch()
        report_ref = write(context.output_root / (name + '_RESEARCH_REPORT.json'), {'synthetic_report': True})
        final_ref = write(context.output_root / (name + '_REPORT.json'), {'synthetic_final': True})
        funnel_ref = write(context.output_root / (name + '_SIGNAL_FUNNEL.json'), {'synthetic_funnel': True})
        output_ref = write(context.compute_root / ('RESULT_' + name + '.json'), {
            'member': name, 'research_report': report_ref['path'], 'research_report_sha256': report_ref['sha256'],
            'funnel': funnel_ref['path'], 'funnel_sha256': funnel_ref['sha256']})
        resource_ref = write(context.compute_root / ('SEGMENT_' + str(index + 1).zfill(6) + '_RESOURCE.json'),
                             {'returncode': 0, 'timed_out': False})
        status = {'state': 'COMPLETED', 'member': name, 'dispatch_id': dispatch['dispatch_id'], 'result_sha256': output_ref['sha256']}
        if index == 1:
            final_manifest = {'job_sha256': context.summary['original']['job']['sha256'],
                'repair_id': context.repair_id, 'reports': {member: {
                    'final_report': str(context.output_root / (member + '_REPORT.json')),
                    'final_report_sha256': ref(context.output_root / (member + '_REPORT.json'))['sha256']}
                    for member in context.summary['original']['accounts']}}
            final_manifest_ref = write(context.compute_root / 'FINAL_REPORTS.json', final_manifest)
            status['final_reports_sha256'] = final_manifest_ref['sha256']
        write(context.compute_root / ('SEGMENT_' + str(index + 1).zfill(6) + '_STATUS.json'), status)
        meter.charge(dispatch['number'], seconds=2, evidence_identity=resource_ref['sha256'],
                     outcome='CONTINUE' if index == 0 else 'COMPLETED')
        outcome['reports'][name] = {'research_report': report_ref['path'], 'research_report_sha256': report_ref['sha256'],
            'funnel': funnel_ref['path'], 'funnel_sha256': funnel_ref['sha256'],
            'final_report': final_ref['path'], 'final_report_sha256': final_ref['sha256']}
    for field, value in (('rule_identity', 'a' * 64), ('input_identity', 'b' * 64), ('phase', 'CONFIRMATION')):
        changed = deepcopy(outcome); changed[field] = value
        with pytest.raises(PermissionError, match='OUTCOME_CONFLICT'):
            context.receipt(changed)
        changed.pop(field)
        with pytest.raises(PermissionError, match='OUTCOME_CONFLICT'):
            context.receipt(changed)
    record = context.receipt(outcome)
    assert context.verify_receipt() == record
    if 'synthetic_evidence_padding' in next(iter(outcome['verification']['items'].values()))['account_audit']:
        assert Path(context.summary['original']['verification']['path']).stat().st_size > 20 * 1024 * 1024
        assert 20 * 1024 * 1024 < (context.output_root / 'REPAIR_COMPLETION.json').stat().st_size <= MAX_REPAIR_EVIDENCE_BYTES
    assert record['status'] == 'REPORT_REPAIRED'
    assert record['compute']['operation']['actual']['verification_jobs'] == 1
    assert case['campaign'].peek_status()['operations']['SYNTHETIC_CANDIDATE_CANDIDATE']['status'] == 'FAILED'
    research_path = context.output_root / 'SYNTHETIC_CANDIDATE_BASE_RESEARCH_REPORT.json'
    research_path.write_bytes(b'{"synthetic_new_unpaid_report":true}')
    changed = deepcopy(outcome)
    changed['reports']['SYNTHETIC_CANDIDATE_BASE']['research_report_sha256'] = ref(research_path)['sha256']
    with pytest.raises(PermissionError, match='WORKER_REPORT_BINDING_CONFLICT'):
        context.receipt(changed)
    write(research_path, {'synthetic_report': True})
    report_path = context.output_root / 'SYNTHETIC_CANDIDATE_BASE_REPORT.json'
    report_path.write_bytes(b'{"synthetic_tamper":true}')
    changed = deepcopy(outcome)
    changed['reports']['SYNTHETIC_CANDIDATE_BASE']['final_report_sha256'] = ref(report_path)['sha256']
    with pytest.raises(PermissionError, match='FINAL_REPORT_BINDING_CONFLICT'):
        context.receipt(changed)
    with pytest.raises(PermissionError, match='REPORT_CHANGED'):
        context.verify_receipt()


def test_repair_evidence_has_explicit_finite_limit_and_preserves_metadata_and_hash_boundaries(tmp_path):
    path = tmp_path / 'SYNTHETIC_EVIDENCE.json'
    with path.open('wb') as stream:
        stream.write(b'{}')
        remaining = MAX_REPAIR_EVIDENCE_BYTES - 2
        padding = b' ' * (1024 * 1024)
        while remaining:
            chunk = padding[:min(len(padding), remaining)]
            stream.write(chunk)
            remaining -= len(chunk)
    reference = ref(path)
    assert path.stat().st_size == MAX_REPAIR_EVIDENCE_BYTES
    assert _read_evidence(reference, tmp_path) == {}
    with pytest.raises(ValueError, match='REPORT_REPAIR_ORIGINAL_CHANGED'):
        _read(reference, tmp_path)
    with path.open('r+b') as stream:
        stream.write(b'[]')
    with pytest.raises(ValueError, match='REPORT_REPAIR_ORIGINAL_CHANGED'):
        _read_evidence(reference, tmp_path)
    with path.open('ab') as stream:
        stream.write(b' ')
    assert path.stat().st_size == MAX_REPAIR_EVIDENCE_BYTES + 1
    with pytest.raises(ValueError, match='REPORT_REPAIR_ORIGINAL_CHANGED'):
        _read_evidence(ref(path), tmp_path)
