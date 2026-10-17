"""原任务收尾的日历派生范围；人工原件，不运行 worker 或账户。"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory import failed_account_calendar_scope_v1 as calendar
from chanlun_trader.research_factory.campaign_scope_v1 import CampaignScopeV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.universe_compute_governance_v1 import UniverseComputeGovernanceV1
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile


OLD = '2000-01-01T00:00:00+00:00'
NEW = '2099-01-01T00:00:00+00:00'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8')
    path.write_bytes(raw)
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


@pytest.fixture
def frozen_task(tmp_path):
    root = tmp_path.resolve()
    authorization_id, objective_id = 'original_campaign', 'original_objective'
    directory = root / 'reports' / 'research_campaigns' / stable_hash(authorization_id)
    profiles = [execution_profile(SEGMENTED_PROFILE, 504, calendar.STAGES[stage][0])
        for stage in ('PREPARATION', 'VERIFICATION', 'REPORT')]
    reference = {'binding_id': 'b' * 64}
    request = {'version': 'FULL_UNIVERSE_SUBMISSION_V4', 'research_binding_ref': reference,
        'phase': 'EXPLORATION', 'symbols': ['000001.SZ', '600000.SH'],
        'execution_profile': execution_profile(SEGMENTED_PROFILE, 504), 'rule': {'frozen': True}}
    authority = {'objective_id': objective_id, 'budget_path': str(directory / 'execution_registry.json'),
        'expires_at': OLD, 'account_authorization': {'campaign_ref': {'root': str(root),
            'authorization_id': authorization_id}}, 'execution_profiles': profiles,
        'compute_authorization': {'preparation_jobs': 1, 'verification_jobs': 1, 'report_jobs': 1},
        'source': {'origin': 'CONTINUOUS_CAMPAIGN_SCOPE_V1', 'research_binding_ref': reference}}
    preview = {'request': request, 'authority_identity': stable_hash(authority),
        'parent_binding_identity': reference['binding_id']}
    preview['preview_identity'] = stable_hash(preview)
    task_id = stable_hash({'preview': preview['preview_identity'], 'objective_id': objective_id})
    folder = root / 'tasks' / task_id
    account = folder / 'account'
    plans = {name: {'plan_id': stable_hash(name)} for name in ('RULE_BASE', 'RULE_STRESS')}
    mapping = {name: 'account_' + name.lower() for name in plans}
    job = {'root': str(account), 'objective_id': objective_id, 'input_identity': stable_hash('inputs'),
        'budget_path': authority['budget_path'], 'plans': plans}
    job_ref = write(account / 'JOB.json', job)
    task = {'task_id': task_id, 'job_path': job_ref['path'], 'job_sha256': job_ref['sha256'],
        'objective_id': objective_id, 'input_identity': job['input_identity'],
        'plan_ids': {name: plan['plan_id'] for name, plan in plans.items()}, 'preview_identity': preview['preview_identity']}
    source = {'origin': 'CAMPAIGN_V1', 'campaign_root': str(root), 'authorization_id': authorization_id,
        'operation_ids': mapping, 'expires_at': OLD, 'research_binding_ref': reference,
        'phase': 'EXPLORATION', 'batch_id': 'original_batch'}
    receipt = {'strategy_plans': plans, 'source': source, 'objective_id': objective_id,
        'budget_path': authority['budget_path'], 'input_identity': job['input_identity'], 'expires_at': OLD}
    receipt['receipt_id'] = stable_hash(receipt)
    original = {'task': write(folder / 'TASK.json', task), 'job': job_ref,
        'preview': write(folder / 'PREVIEW.json', preview), 'receipt': write(account / 'APPROVAL.json', receipt)}
    scope = calendar.calendar_task_scope(root, original, mapping, NEW)
    proof = {'manifest_ref': {'path': str(folder / 'recovery' / 'MANIFEST.json'), 'sha256': 'c' * 64},
        'recovery_id': 'd' * 64}
    operations = {operation_id: {'operation_id': operation_id, 'kind': 'ACCOUNT', 'stage': 'EXPLORATION',
        'batch_id': source['batch_id'], 'status': 'COMPLETED', 'calendar_task_scope': deepcopy(scope),
        'execution_expires_at': NEW, 'failed_account_recovery_proof': deepcopy(proof)} for operation_id in mapping.values()}
    first = operations[mapping['RULE_BASE']]
    first['status'] = 'FAILED'
    first['failed_account_recovery'] = {'status': 'COMPLETED'}
    authorization = {'authorization_id': authorization_id, 'objective_id': objective_id,
        'root': os.path.normcase(str(root)), 'expires_at': OLD, 'stages': ['EXPLORATION']}
    view = {'authorization': authorization, 'base_authorization': {'scope_policy': {'scope_hash': 'scope'}},
        'operations': operations, 'paused': False, 'revoked': False, 'stages': {}}
    return SimpleNamespace(root=root, folder=folder, account=account, original=original, job=job, task=task,
        request=request, preview=preview, receipt=receipt, mapping=mapping, authority=authority, scope=scope,
        proof=proof, authorization=authorization, operations=operations, view=view)


def test_scope_preserves_original_pins_and_has_only_two_canonical_reservations(frozen_task):
    case = frozen_task
    before = {name: Path(ref['path']).read_bytes() for name, ref in case.original.items()}
    scope = calendar.calendar_task_scope(case.root, case.original, case.mapping, NEW)
    assert scope['original'] == case.original
    assert scope['authority_identity'] == stable_hash(case.authority)
    assert scope['previous_expires_at'] == case.authority['expires_at'] == OLD
    assert set(scope['compute_operations']) == {'VERIFICATION', 'REPORT'}
    for stage, item in scope['compute_operations'].items():
        assert item['kind'] == 'VERIFY' and item['stage'] == 'EXPLORATION'
        assert set(item) == {'operation_id', 'batch_id', 'stage', 'kind', 'subject_identity', 'upper_bounds',
            'cost_bound_evidence', 'hypothesis_identity', 'execution_profile'}
        assert item['upper_bounds'] == dict.fromkeys(calendar.RESOURCES, 0) | {'verification_jobs': 1, 'wall_seconds': 28800}
    assert before == {name: Path(ref['path']).read_bytes() for name, ref in case.original.items()}


@pytest.mark.parametrize('stage', ['VERIFICATION', 'REPORT'])
def test_canonical_subject_is_the_unchanged_original_meter_binding(frozen_task, monkeypatch, stage):
    case = frozen_task
    binding = {'phase': 'EXPLORATION', 'batch_id': 'original_batch'}
    monkeypatch.setattr(CampaignScopeV1, 'resolve', lambda *args, **kwargs: deepcopy(binding))
    meter = UniverseComputeGovernanceV1(case.account / ('COMPUTE_' + stage), case.authority,
        case.request, stage, for_dispatch=False)
    assert meter.binding == calendar._compute_binding(case.scope, case.request, stage)
    assert meter.binding['compute_identity'] == case.scope['compute_operations'][stage]['subject_identity']
    assert meter.binding['expires_at'] == OLD and meter.budget_key == stable_hash(case.authority)


@pytest.mark.parametrize('pin', ['task', 'job', 'preview', 'receipt'])
def test_scope_fails_closed_if_any_pinned_original_changes(frozen_task, pin):
    Path(frozen_task.original[pin]['path']).write_bytes(b'{}\n')
    with pytest.raises((ValueError, PermissionError), match='ORIGINAL_CHANGED'):
        calendar.calendar_task_scope(frozen_task.root, frozen_task.original, frozen_task.mapping, NEW)


def test_v4_missing_authority_identity_is_rejected_but_legacy_fixture_has_no_scope(frozen_task):
    case = frozen_task
    preview = deepcopy(case.preview)
    preview.pop('authority_identity')
    case.original['preview'] = write(Path(case.original['preview']['path']), preview)
    with pytest.raises(PermissionError, match='AUTHORITY_AND_BINDING_REQUIRED'):
        calendar.calendar_task_scope(case.root, case.original, case.mapping, NEW)
    preview['request'].pop('version')
    case.original['preview'] = write(Path(case.original['preview']['path']), preview)
    assert calendar.calendar_task_scope(case.root, case.original, case.mapping, NEW) is None


@pytest.mark.parametrize('changed', ['root', 'batch', 'binding', 'mapping', 'expiry', 'objective', 'budget'])
def test_scope_rejects_rehashed_receipt_mixed_with_another_authority(frozen_task, changed):
    case = frozen_task
    receipt = deepcopy(case.receipt)
    if changed == 'root': receipt['source']['campaign_root'] = str(case.root / 'other')
    if changed == 'batch': receipt['source']['batch_id'] = ''
    if changed == 'binding': receipt['source']['research_binding_ref'] = {'binding_id': 'e' * 64}
    if changed == 'mapping': receipt['source']['operation_ids']['RULE_BASE'] = 'other'
    if changed == 'expiry': receipt['expires_at'] = NEW
    if changed == 'objective': receipt['objective_id'] = 'other'
    if changed == 'budget': receipt['budget_path'] = str(case.root / 'another_registry.json')
    receipt['receipt_id'] = stable_hash({key: value for key, value in receipt.items() if key != 'receipt_id'})
    case.original['receipt'] = write(Path(case.original['receipt']['path']), receipt)
    with pytest.raises(PermissionError):
        calendar.calendar_task_scope(case.root, case.original, case.mapping, NEW)


@pytest.mark.parametrize('stage', ['VERIFICATION', 'REPORT'])
def test_deadline_is_derived_only_after_both_effective_accounts_complete(frozen_task, stage):
    case = frozen_task
    item = deepcopy(case.scope['compute_operations'][stage])
    original = deepcopy((case.authorization, case.operations, item))
    assert calendar.calendar_operation_deadline(case.authorization, case.operations, item) == NEW
    assert (case.authorization, case.operations, item) == original
    existing = {**item, 'status': 'RUNNING', 'execution_expires_at': NEW}
    case.operations[item['operation_id']] = existing
    assert calendar.calendar_operation_deadline(case.authorization, case.operations, existing) == NEW


@pytest.mark.parametrize('status', ['RESERVED', 'RUNNING', 'UNKNOWN', 'FAILED'])
def test_no_compute_deadline_if_recovery_or_stress_is_not_complete(frozen_task, status):
    case = frozen_task
    case.operations[case.mapping['RULE_BASE']]['failed_account_recovery']['status'] = status
    item = case.scope['compute_operations']['VERIFICATION']
    assert calendar.calendar_operation_deadline(case.authorization, case.operations, item) == OLD
    case.operations[case.mapping['RULE_BASE']]['failed_account_recovery']['status'] = 'COMPLETED'
    case.operations[case.mapping['RULE_STRESS']]['status'] = status
    assert calendar.calendar_operation_deadline(case.authorization, case.operations, item) == OLD


@pytest.mark.parametrize('field,value', [
    ('operation_id', 'another'), ('batch_id', 'another'), ('stage', 'CONFIRMATION'), ('kind', 'DATA'),
    ('subject_identity', 'other'), ('hypothesis_identity', 'other'), ('cost_bound_evidence', 'other'),
    ('execution_profile', {}), ('upper_bounds', {'verification_jobs': 1, 'wall_seconds': 28801}),
    ('caller_supplied_extra', True),
])
def test_proposed_compute_must_match_every_original_reserve_field(frozen_task, field, value):
    case = frozen_task
    item = deepcopy(case.scope['compute_operations']['VERIFICATION'])
    item[field] = value
    assert calendar.calendar_operation_deadline(case.authorization, case.operations, item) == OLD


@pytest.mark.parametrize('fault', ['scope', 'proof', 'deadline', 'active', 'overrun', 'missing', 'authorization'])
def test_parent_pair_proof_and_scope_are_indivisible(frozen_task, fault):
    case = frozen_task
    operation = case.operations[case.mapping['RULE_STRESS']]
    if fault == 'scope': operation['calendar_task_scope']['task_id'] = 'e' * 64
    if fault == 'proof': operation['failed_account_recovery_proof']['recovery_id'] = 'e' * 64
    if fault == 'deadline': operation['execution_expires_at'] = '2100-01-01T00:00:00+00:00'
    if fault == 'active': operation['active_segment'] = {'segment_number': 1}
    if fault == 'overrun': operation['resource_overrun'] = 1
    if fault == 'missing': del case.operations[case.mapping['RULE_STRESS']]
    if fault == 'authorization': case.authorization['authorization_id'] = 'another'
    assert calendar.calendar_operation_deadline(case.authorization, case.operations,
        case.scope['compute_operations']['REPORT']) == OLD


def install_context_mock(case, monkeypatch):
    from chanlun_trader.research_factory.failed_account_recovery_v1 import FailedAccountRecoveryV1
    recovered = FailedAccountRecoveryV1.__new__(FailedAccountRecoveryV1)
    recovered.root = case.root
    recovered.scope_reference = case.proof['manifest_ref']
    recovered.recovery_id = case.proof['recovery_id']
    recovered.summary_value = {'original': deepcopy(case.original), 'authorization_id': case.authorization['authorization_id'],
        'expires_at': OLD, 'accounts': {name: {'operation_id': value} for name, value in case.mapping.items()},
        'calendar_deadline_extension': {'expires_at': NEW}, 'calendar_task_scope': deepcopy(case.scope)}
    calls = []
    def fresh(cls, job, *, for_dispatch=False):
        calls.append(for_dispatch)
        assert job == case.job
        return recovered
    monkeypatch.setattr(FailedAccountRecoveryV1, 'from_job', classmethod(fresh))
    monkeypatch.setattr(ResearchCampaignV1, 'peek_status', lambda campaign: deepcopy(case.view))
    monkeypatch.setattr(ResearchCampaignV1, 'status', lambda campaign: deepcopy(case.view))
    return recovered, calls


def test_context_never_caches_recovery_or_installed_parent_view(frozen_task, monkeypatch):
    case = frozen_task
    _, calls = install_context_mock(case, monkeypatch)
    context = calendar.FailedAccountCalendarContextV1.from_job(case.root, case.job)
    assert context.require_authority(case.authority, case.request) == NEW
    assert calls == [False, True, True]
    case.operations[case.mapping['RULE_BASE']].pop('calendar_task_scope')
    with pytest.raises(PermissionError, match='INSTALLATION_REQUIRED'):
        context.require_authority(case.authority, case.request)
    assert calls[-1] is True


@pytest.mark.parametrize('fault', ['authority', 'request', 'symbols'])
def test_calendar_authority_cannot_change_original_request_or_authority(frozen_task, monkeypatch, fault):
    case = frozen_task
    install_context_mock(case, monkeypatch)
    context = calendar.FailedAccountCalendarContextV1.from_job(case.root, case.job)
    authority, request = deepcopy(case.authority), deepcopy(case.request)
    if fault == 'authority': authority['expires_at'] = NEW
    if fault == 'request': request['rule'] = {'another': True}
    if fault == 'symbols': request['symbols'] = ['600000.SH']
    with pytest.raises(PermissionError, match='AUTHORITY_OR_REQUEST_CONFLICT'):
        context.require_authority(authority, request)


def test_scope_rejects_fake_same_interface_calendar_object_before_use(frozen_task):
    case = frozen_task
    binding = {'authorization_id': case.authorization['authorization_id'], 'campaign_root': str(case.root),
        'phase': 'EXPLORATION', 'expires_at': OLD, 'scope_hash': 'scope'}
    binding['binding_id'] = stable_hash(binding)
    directory = case.root / 'reports' / 'research_campaigns' / stable_hash(case.authorization['authorization_id'])
    write(directory / 'delegations' / (binding['binding_id'] + '.json'), binding)
    campaign = SimpleNamespace(root=case.root, authorization_id=case.authorization['authorization_id'],
        directory=directory, peek_status=lambda: deepcopy(case.view))
    fake = SimpleNamespace(require_binding=lambda *args, **kwargs: NEW)
    with pytest.raises(PermissionError, match='SIGNED_CONTEXT_REQUIRED'):
        CampaignScopeV1(campaign).resolve({'binding_id': binding['binding_id']}, calendar_context=fake)


@pytest.mark.parametrize('stage', ['VERIFICATION', 'REPORT'])
def test_calendar_adapter_preserves_original_meter_identity_and_is_fresh(frozen_task, monkeypatch, stage):
    case = frozen_task
    recovered, calls = install_context_mock(case, monkeypatch)
    binding = {'phase': 'EXPLORATION', 'batch_id': 'original_batch', 'binding_id': case.request['research_binding_ref']['binding_id'],
        'expires_at': OLD}
    monkeypatch.setattr(CampaignScopeV1, 'resolve', lambda *args, **kwargs: deepcopy(binding))
    meter = calendar.calendar_compute_governance_v1(case.account / ('COMPUTE_' + stage),
        case.authority, case.request, stage)
    assert isinstance(meter, UniverseComputeGovernanceV1)
    assert meter.binding == calendar._compute_binding(case.scope, case.request, stage)
    before = len(calls)
    assert meter.active() == meter.binding
    assert len(calls) > before and all(calls[index] is True for index in range(before, len(calls)))
    recovered.summary_value['calendar_task_scope']['request_identity'] = 'f' * 64
    with pytest.raises(PermissionError, match='TASK_SCOPE_CHANGED'):
        meter.active()


def test_historical_adapter_read_does_not_require_unexpired_deadline_or_allow_active(frozen_task, monkeypatch):
    case = frozen_task
    install_context_mock(case, monkeypatch)
    binding = {'phase': 'EXPLORATION', 'batch_id': 'original_batch', 'binding_id': case.request['research_binding_ref']['binding_id'],
        'expires_at': OLD}
    monkeypatch.setattr(CampaignScopeV1, 'resolve', lambda *args, **kwargs: deepcopy(binding))
    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2100, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(calendar, 'datetime', Future)
    meter = calendar.calendar_compute_governance_v1(case.account / 'COMPUTE_REPORT', case.authority,
        case.request, 'REPORT', for_dispatch=False)
    assert meter.binding['expires_at'] == OLD
    with pytest.raises(PermissionError, match='RECONCILIATION_ONLY'):
        meter.active()


def test_factory_keeps_preparation_and_ordinary_requests_on_original_class(frozen_task, monkeypatch):
    case = frozen_task
    monkeypatch.setattr(CampaignScopeV1, 'resolve', lambda *args, **kwargs: {'phase': 'EXPLORATION', 'batch_id': 'original_batch'})
    meter = calendar.calendar_compute_governance_v1(case.account / 'COMPUTE_PREPARATION', case.authority,
        case.request, 'PREPARATION', for_dispatch=False)
    assert type(meter) is UniverseComputeGovernanceV1
    with pytest.raises(PermissionError, match='RECONCILIATION_ONLY'):
        meter.active()


@pytest.mark.parametrize('options', [{}, {'for_dispatch': False}, {'campaign_operation': {'operation_id': 'original'}}])
def test_ordinary_factory_uses_current_original_constructor(monkeypatch, tmp_path, options):
    from chanlun_trader.research_factory import universe_compute_governance_v1 as ordinary
    calls = []
    marker = object()

    def constructor(*args, **kwargs):
        calls.append((args, kwargs))
        return marker

    monkeypatch.setattr(ordinary, 'UniverseComputeGovernanceV1', constructor)
    assert calendar.calendar_compute_governance_v1(tmp_path, {}, {}, 'REPORT', **options) is marker
    assert calls == [((tmp_path, {}, {}, 'REPORT'), options)]


@pytest.mark.parametrize('stage', ['VERIFICATION', 'REPORT'])
def test_ordinary_v4_tasks_may_be_outside_campaign_root(frozen_task, monkeypatch, stage):
    case = frozen_task
    campaign_root = case.root / 'separate_campaign'
    campaign_root.mkdir()
    authority = deepcopy(case.authority)
    authority['account_authorization']['campaign_ref']['root'] = str(campaign_root)
    monkeypatch.setattr(CampaignScopeV1, 'resolve', lambda *args, **kwargs: {'phase': 'EXPLORATION'})

    def unexpected(*args, **kwargs):
        pytest.fail('Ordinary external task must not probe calendar paths or Owner')

    monkeypatch.setattr(calendar, 'validated_reference_path', unexpected)
    monkeypatch.setattr(calendar.FailedAccountCalendarContextV1, 'from_job', classmethod(unexpected))
    folder = case.account / ('COMPUTE_' + stage)
    direct = UniverseComputeGovernanceV1(folder, authority, case.request, stage, for_dispatch=False)
    meter = calendar.calendar_compute_governance_v1(folder, authority, case.request, stage, for_dispatch=False)
    assert type(meter) is UniverseComputeGovernanceV1 and meter.binding == direct.binding
    with pytest.raises(PermissionError, match='RECONCILIATION_ONLY'):
        meter.active()
    active = calendar.calendar_compute_governance_v1(folder, authority, case.request, stage)
    assert active.binding['expires_at'] == OLD
    with pytest.raises(PermissionError, match='AUTHORIZATION_EXPIRED'):
        active.active()


@pytest.mark.parametrize('stage', ['VERIFICATION', 'REPORT'])
def test_calendar_candidate_validation_errors_never_fall_back(frozen_task, monkeypatch, stage):
    from chanlun_trader.research_factory import universe_compute_governance_v1 as ordinary
    case = frozen_task

    def reject(path, *, root, error_code):
        assert root == case.root and path == case.account / 'JOB.json'
        raise ValueError(error_code)

    def unexpected(*args, **kwargs):
        pytest.fail('Invalid calendar candidate must not fall back to ordinary constructor')

    monkeypatch.setattr(calendar, 'validated_reference_path', reject)
    monkeypatch.setattr(ordinary, 'UniverseComputeGovernanceV1', unexpected)
    with pytest.raises(ValueError, match='FAILED_ACCOUNT_CALENDAR_JOB_PATH_INVALID'):
        calendar.calendar_compute_governance_v1(case.account / ('COMPUTE_' + stage),
            case.authority, case.request, stage)
