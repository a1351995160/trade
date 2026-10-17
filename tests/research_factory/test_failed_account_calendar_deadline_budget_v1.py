"""纯预算事件隔离验证；resolver为明确人工替身，不代表Owner或真实账户许可。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from chanlun_trader.research_factory import research_campaign_v1 as campaigns
from chanlun_trader.research_factory import failed_account_recovery_v1 as recoveries
from chanlun_trader.research_factory.budget import BudgetExhaustedError, BudgetLedgerMismatchError
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.run_budget import AutonomousRunBudgetV2, effective_execution_expires_at
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile
from test_failed_account_recovery_budget_v1 import (
    assert_conservation, charge, install, recovery_budget_case,
)


def calendar(case):
    original = case['resolved']['expires_at']
    deadline = (datetime.fromisoformat(original) + timedelta(hours=72)).isoformat()
    case['resolved'].update(execution_expires_at=deadline, calendar_deadline_extension={
        'previous_expires_at': original, 'expires_at': deadline,
        'human_authorization_ref': {'path': 'ARTIFICIAL_NOT_OWNER', 'sha256': 'a' * 64}})
    return original, deadline


def clock(monkeypatch, moment):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr(campaigns, 'datetime', Clock)


def test_calendar_install_is_atomic_and_keeps_global_expiry_and_all_costs(recovery_budget_case, monkeypatch):
    case = recovery_budget_case
    original, deadline = calendar(case)
    campaign = case['campaign']
    authority = campaign.authorization_path.read_bytes()
    clock(monkeypatch, datetime.fromisoformat(original) + timedelta(seconds=1))
    installed = install(case)
    after = campaign.peek_status()
    assert after['authorization'] == case['before']['authorization']
    assert after['authorization']['expires_at'] == original
    assert after['grants'] == case['before']['grants']
    assert after['trial_usage'] == case['before']['trial_usage']
    assert installed['status'] == 'FAILED' and installed['actual']['wall_seconds'] == 42340
    assert installed['segments'] == case['before']['operations']['BASE']['segments']
    assert installed['active_wall_seconds'] == 42339.896402100036
    assert after['reserved']['wall_seconds'] == 29660 + 72000
    for name in ('BASE', 'STRESS'):
        assert effective_execution_expires_at(after['authorization'], after['operations'][name]) == deadline
    assert campaign.authorization_path.read_bytes() == authority
    ledger = campaign.budget_path.with_name('run_budget_events.jsonl')
    assert ledger.read_bytes().startswith(case['original_events'])
    installed_head = ledger.read_bytes()
    assert install(case) == installed
    assert ledger.read_bytes() == installed_head
    assert_conservation(after)
    charge(case, 0.2)
    charged = campaign.peek_status()
    assert len(charged['operations']['BASE']['segments']) == 51
    assert charged['operations']['BASE']['segments'][:50] == case['before']['operations']['BASE']['segments']
    head = ledger.read_bytes()
    clock(monkeypatch, datetime.fromisoformat(deadline))
    with pytest.raises(BudgetExhaustedError, match='EXPIRED'):
        campaign.start_execution_segment('BASE', segment_number=52)
    assert ledger.read_bytes() == head
    assert_conservation(campaign.peek_status())


def test_expired_original_without_calendar_stays_closed(recovery_budget_case, monkeypatch):
    case = recovery_budget_case
    clock(monkeypatch, datetime.fromisoformat(case['resolved']['expires_at']) + timedelta(seconds=1))
    ledger = case['campaign'].budget_path.with_name('run_budget_events.jsonl')
    before = ledger.read_bytes()
    with pytest.raises(BudgetExhaustedError, match='EXPIRED'):
        install(case)
    assert ledger.read_bytes() == before


def test_calendar_does_not_unfreeze_unrelated_operations(recovery_budget_case, monkeypatch):
    case = recovery_budget_case
    original, _ = calendar(case)
    clock(monkeypatch, datetime.fromisoformat(original) + timedelta(seconds=1))
    install(case)
    campaign = case['campaign']
    view = campaign.peek_status()
    ledger = campaign.budget_path.with_name('run_budget_events.jsonl')
    before = ledger.read_bytes()
    with pytest.raises(BudgetExhaustedError, match='EXPIRED'):
        campaign._dispatchable(view, 'EXPLORATION')
    with pytest.raises(BudgetExhaustedError, match='EXPIRED'):
        campaign.reserve_operation(operation_id='UNRELATED', batch_id='ARTIFICIAL_BATCH',
            stage='EXPLORATION', kind='VERIFY', subject_identity='unrelated',
            upper_bounds={'verification_jobs': 1, 'wall_seconds': 1})
    forged = deepcopy(view['operations']['BASE'])
    forged['operation_id'] = 'UNRELATED'
    with pytest.raises(BudgetExhaustedError, match='EXPIRED'):
        campaign._dispatchable(view, 'EXPLORATION', operation=forged)
    assert ledger.read_bytes() == before


def test_execution_expiry_without_installed_proof_is_not_a_capability():
    with pytest.raises(BudgetLedgerMismatchError, match='WITHOUT_RECOVERY'):
        effective_execution_expires_at({'expires_at': '2000-01-01T00:00:00+00:00'},
            {'execution_expires_at': '2100-01-01T00:00:00+00:00'})


@pytest.mark.parametrize('kind', ['CAMPAIGN_OPERATION_RESERVED', 'CAMPAIGN_OPERATION_STARTED'])
@pytest.mark.parametrize('field,value', [
    ('execution_expires_at', '2099-01-01T00:00:00+00:00'),
    ('calendar_task_scope', {}), ('calendar_task_deadline_proof', {}),
])
@pytest.mark.parametrize('ingress', ['api', 'replay'])
def test_event_payload_cannot_inject_calendar_authority(tmp_path, monkeypatch, kind, field, value, ingress):
    units = dict.fromkeys(AutonomousRunBudgetV2.resource_names, 10)
    units['wall_seconds'] = 100
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    authorization = {'authorization_id': 'artificial_calendar_event_injection', 'objective_id': 'artificial_objective',
        'resource_limits': units, 'stages': ['EXPLORATION'], 'expires_at': expiry.isoformat(),
        'max_batches': 2, 'max_total_predictive_trials': 2, 'max_trials_per_batch': 2,
        'max_hypotheses_per_batch': 2, 'max_candidates_per_batch': 2}
    campaign = campaigns.ResearchCampaignV1.create(tmp_path, authorization)
    reserved = campaign.reserve_operation(operation_id='VERIFY', batch_id='ARTIFICIAL_BATCH', stage='EXPLORATION',
        kind='VERIFY', subject_identity='artificial_verify', upper_bounds={'verification_jobs': 1, 'wall_seconds': 1})
    payload = ({key: item for key, item in reserved.items() if key != 'status'}
        if kind == 'CAMPAIGN_OPERATION_RESERVED' else {'operation_id': 'VERIFY', 'started_at': expiry.isoformat()})
    if kind == 'CAMPAIGN_OPERATION_RESERVED':
        payload['operation_id'] = 'INJECTED_VERIFY'
    payload[field] = value
    ledger = campaign.budget_path.with_name('run_budget_events.jsonl')
    before = ledger.read_bytes()
    clock(monkeypatch, expiry + timedelta(seconds=1))
    if ingress == 'api':
        with pytest.raises(BudgetLedgerMismatchError, match='DERIVED_FIELDS_FORBIDDEN'):
            campaign._budget().campaign_event(kind, payload)
        assert ledger.read_bytes() == before
        with pytest.raises(BudgetExhaustedError, match='EXPIRED'):
            campaign.start_operation('VERIFY')
    else:
        # 已有事件即使重算普通event_id，也不能把新派生字段变成Owner许可。
        event = {'event_type': kind, 'payload': payload, 'run_id': authorization['authorization_id']}
        event['event_id'] = stable_hash(event)
        ledger.write_bytes(before + (json.dumps(event, ensure_ascii=True) + '\n').encode('utf-8'))
        injected = ledger.read_bytes()
        with pytest.raises(BudgetLedgerMismatchError, match='DERIVED_FIELDS_FORBIDDEN'):
            campaign.peek_status()
        assert ledger.read_bytes() == injected


@pytest.fixture
def calendar_compute_budget_case(tmp_path, monkeypatch):
    """真实预算生产者；只有完整Owner读取层是明确人工替身，不能作为Owner门禁。"""
    create = campaigns.ResearchCampaignV1.create
    profiles = {stage: execution_profile(SEGMENTED_PROFILE, 504, 'RESEARCH_' + purpose)
        for stage, purpose in (('VERIFICATION', 'VERIFICATION'), ('REPORT', 'REPORT'))}

    def with_tail_profiles(root, authorization):
        authorization = deepcopy(authorization)
        authorization['execution_profiles'] += list(profiles.values())
        return create(root, authorization)

    monkeypatch.setattr(campaigns.ResearchCampaignV1, 'create', staticmethod(with_tail_profiles))
    case = recovery_budget_case.__wrapped__(tmp_path, monkeypatch)
    original, deadline = calendar(case)
    campaign = case['campaign']
    computes = {stage: {'operation_id': 'TAIL_' + stage, 'batch_id': 'ARTIFICIAL_BATCH',
        'stage': 'EXPLORATION', 'kind': 'VERIFY', 'subject_identity': 'artificial_' + stage,
        'hypothesis_identity': 'artificial_' + stage, 'cost_bound_evidence': None,
        'execution_profile': profile, 'upper_bounds': dict.fromkeys(AutonomousRunBudgetV2.resource_names, 0)
            | {'verification_jobs': 1, 'wall_seconds': profile['total_seconds']}}
        for stage, profile in profiles.items()}
    case['resolved']['calendar_task_scope'] = {'schema_version': 'FAILED_ACCOUNT_CALENDAR_TASK_SCOPE_V1',
        'root': str(case['root']), 'authorization_id': campaign.authorization_id,
        'objective_id': case['before']['authorization']['objective_id'], 'previous_expires_at': original,
        'execution_expires_at': deadline, 'batch_id': 'ARTIFICIAL_BATCH',
        'account_operation_ids': case['resolved']['operation_ids'], 'compute_operations': computes}
    clock(monkeypatch, datetime.fromisoformat(original) + timedelta(seconds=1))
    install(case); charge(case, .2, outcome='COMPLETED')
    from test_failed_account_recovery_budget_v1 import settle
    settle(case)
    campaign.start_operation('STRESS')
    dispatch = campaign.start_execution_segment('STRESS', segment_number=1)
    campaign.end_execution_segment('STRESS', segment_number=1, seconds=.2,
        evidence_identity='artificial_stress_resource', outcome='COMPLETED', dispatch_identity=dispatch['dispatch_identity'])
    campaign.settle_operation('STRESS', actual={'account_jobs': 1, 'wall_seconds': 1},
        outcome='COMPLETED', evidence_identity='artificial_stress_settlement')
    ledger = campaign.budget_path.with_name('run_budget_events.jsonl')
    original_resolver = recoveries.resolve_failed_account_recovery_manifest

    def dispatch_owner_read(reference, **kwargs):
        value = original_resolver(reference, **kwargs)
        if kwargs.get('for_dispatch'):
            if (case['root'] / 'REVOKED.json').exists():
                raise PermissionError('ARTIFICIAL_FULL_OWNER_ACCOUNT_REVOKED')
            events = [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines()]
            if any(event['event_type'] == 'CAMPAIGN_OPERATION_UNKNOWN' for event in events):
                raise PermissionError('ARTIFICIAL_FULL_OWNER_UNKNOWN_OPERATION')
        return value

    monkeypatch.setattr(recoveries, 'resolve_failed_account_recovery_manifest', dispatch_owner_read)
    case.update(computes=computes, ledger=ledger, deadline=deadline)
    return case


def reserve_tail(case, stage):
    return case['campaign'].reserve_operation(**deepcopy(case['computes'][stage]))


@pytest.mark.parametrize('fault', ['account_revocation', 'unknown'])
@pytest.mark.parametrize('entry', ['reserve', 'start', 'limit', 'segment'])
def test_parent_calendar_compute_requires_fresh_dispatch_owner_before_any_write(calendar_compute_budget_case, fault, entry):
    case = calendar_compute_budget_case; campaign = case['campaign']; operation = 'TAIL_REPORT'
    if entry != 'reserve':
        reserve_tail(case, 'REPORT')
    if entry == 'segment':
        campaign.start_operation(operation)
    if fault == 'account_revocation':
        (case['root'] / 'REVOKED.json').write_text('{"fixture_only":true}\n', encoding='utf-8')
    else:
        reserve_tail(case, 'VERIFICATION')
        campaign.start_operation('TAIL_VERIFICATION')
        campaign.mark_unknown('TAIL_VERIFICATION', 'artificial valid late UNKNOWN')
    before = case['ledger'].read_bytes()
    # 历史只读仍可核账；新预留/START/限额/段派发必须走当前for_dispatch能力。
    campaign.peek_status()
    actions = {'reserve': lambda: reserve_tail(case, 'REPORT'),
        'start': lambda: campaign.start_operation(operation),
        'limit': lambda: campaign.effective_execution_limit(operation, for_dispatch=True),
        'segment': lambda: campaign.start_execution_segment(operation, segment_number=1)}
    with pytest.raises(PermissionError, match='ARTIFICIAL_FULL_OWNER_'):
        actions[entry]()
    assert case['ledger'].read_bytes() == before


def test_parent_calendar_compute_can_finish_and_reconcile_after_new_deadline(calendar_compute_budget_case, monkeypatch):
    case = calendar_compute_budget_case; campaign = case['campaign']; operation = 'TAIL_REPORT'
    reserved = reserve_tail(case, 'REPORT')
    assert reserved['execution_expires_at'] == case['deadline']
    assert reserved['calendar_task_deadline_proof'] == case['proof']
    campaign.start_operation(operation)
    assert campaign.effective_execution_limit(operation, for_dispatch=True)['execution_expires_at'] == case['deadline']
    campaign.start_execution_segment(operation, segment_number=1)
    clock(monkeypatch, datetime.fromisoformat(case['deadline']))
    campaign.end_execution_segment(operation, segment_number=1, seconds=.2,
        evidence_identity='artificial_tail_resource', outcome='COMPLETED')
    campaign.settle_operation(operation, actual={'verification_jobs': 1, 'wall_seconds': 1},
        outcome='COMPLETED', evidence_identity='artificial_tail_settlement')
    assert campaign.peek_status()['operations'][operation]['status'] == 'COMPLETED'
