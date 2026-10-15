"""连续账户每次guard都新鲜核验；一次调用不反复重放，也不改观察快照。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import time

import pytest

from chanlun_trader.research_factory.budget import BudgetLedgerMismatchError
from chanlun_trader.research_factory.campaign_scope_v1 import (
    CampaignScopeV1, OwnerApprovalStoreV1, bind_campaign_scope, scope_summary,
)
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
from chanlun_trader.research_factory.run_budget import AutonomousRunBudgetV2
from test_continuous_research_contract_v1 import build
from test_universe_execution_profile_v1 import plans


@pytest.fixture
def continuous_guard(tmp_path):
    """原Owner/连续binding/父START/消费登记；只造治理元信息，不执行行情。"""
    root = tmp_path / 'synthetic_continuous_guard'
    root.mkdir()
    contract = build(capabilities())
    scope = contract['scope']
    limits = dict.fromkeys(AutonomousRunBudgetV2.resource_names, 100000)
    stages = {phase: {name: value // 2 for name, value in limits.items()}
              for phase in ('EXPLORATION', 'CONFIRMATION')}
    authority = {'authorization_id': 'synthetic_guard', 'objective_id': contract['objective_id'],
                 'resource_limits': limits, 'stages': list(stages),
                 'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                 'max_batches': 2, 'max_total_predictive_trials': 4, 'max_trials_per_batch': 2,
                 'max_hypotheses_per_batch': 2, 'max_candidates_per_batch': 2,
                 'execution_profiles': scope['execution_profiles']}
    owner = object()
    approvals = OwnerApprovalStoreV1(tmp_path / 'protected_synthetic_owner', owner_capability=owner)
    summary = scope_summary(root, authority, contract, stages)
    approval = approvals.approve(summary, approver='SYNTHETIC_GUARD_OWNER', capability=owner)
    authority = bind_campaign_scope(root, authority, contract=contract, stage_limits=stages,
                                    approvals=approvals, approval_ref=approval)
    campaign = ResearchCampaignV1.create(root, authority)
    request = {name: deepcopy(scope[name]) for name in ('initial_cash', 'max_positions', 'universe_id',
               'universe_hash', 'rule_version', 'cost_profiles', 'execution_profiles')}
    request.update(phase='EXPLORATION', dataset_id='exploration', dataset_hash=stable_hash('EXPLORATION'),
                   feature_start='2022-01-01', account_start='2022-07-01', account_end='2023-07-31')
    binding = CampaignScopeV1(campaign).delegate(batch_id='synthetic_guard_batch',
        candidate_identity=stable_hash('synthetic_guard_rule'), phase='EXPLORATION', request=request,
        expires_at=authority['expires_at'])
    frozen = plans()
    campaign.reserve_operation(operation_id='synthetic_account', batch_id='synthetic_guard_batch',
        kind='ACCOUNT', stage='EXPLORATION', subject_identity=frozen['FIXED']['plan_id'],
        upper_bounds={'account_jobs': 1, 'wall_seconds': 100})
    governor = StrategyBatchGovernanceV1(root / 'account', root / 'search_budget.json', contract['objective_id'], frozen)
    governor.confirm_campaign_scope(campaign, {'FIXED': 'synthetic_account'}, {'input_identity': 'SYNTHETIC',
        'novelty': {'FIXED': {'allowed': True, 'plan_id': frozen['FIXED']['plan_id']}}}, research_binding_ref=binding)
    governor.start('FIXED')
    return governor, campaign, approvals, approval, binding


def test_guard_uses_one_fresh_campaign_view_without_writing_observation(continuous_guard, monkeypatch, record_property):
    governor, campaign, *_ = continuous_guard
    paths = [campaign.budget_path, campaign.budget_path.with_name('run_budget_events.jsonl'),
             governor.receipt_path, governor.root / 'FIXED_START.json', governor.budget_path]
    before = {path: path.read_bytes() for path in paths}
    counts = {'view': 0}
    native = AutonomousRunBudgetV2.campaign_view

    def counted(budget):
        counts['view'] += 1
        return native(budget)

    monkeypatch.setattr(AutonomousRunBudgetV2, 'campaign_view', counted)
    started = time.perf_counter()
    receipt = governor.active_execution('FIXED')
    elapsed = time.perf_counter() - started
    record_property('campaign_view_calls', counts['view'])
    record_property('guard_seconds', elapsed)
    assert receipt['execution_consumed'] is True and receipt['execution_purpose'] == 'FIXED'
    assert counts['view'] == 1, counts
    assert all(path.read_bytes() == data for path, data in before.items())


@pytest.mark.parametrize('change', ['owner', 'binding', 'event', 'receipt', 'start', 'settled_parent'])
def test_next_guard_revalidates_authority_and_consumption(continuous_guard, change):
    governor, campaign, approvals, approval, binding = continuous_guard
    governor.active_execution('FIXED')
    if change == 'owner':
        path = approvals.directory / (approval['approval_id'] + '.json')
        path.write_text('{}', encoding='utf-8')
    elif change == 'binding':
        path = campaign.directory / 'delegations' / (binding['binding_id'] + '.json')
        value = json.loads(path.read_text(encoding='utf-8'))
        value['batch_id'] = 'changed'
        path.write_text(json.dumps(value), encoding='utf-8')
    elif change == 'event':
        path = campaign.budget_path.with_name('run_budget_events.jsonl')
        with path.open('a', encoding='utf-8') as stream:
            stream.write('{"event_id":"tampered"}\n')
    elif change == 'receipt':
        value = json.loads(governor.receipt_path.read_text(encoding='utf-8'))
        value['input_identity'] = 'CHANGED'
        governor.receipt_path.write_text(json.dumps(value), encoding='utf-8')
    elif change == 'start':
        value = json.loads((governor.root / 'FIXED_START.json').read_text(encoding='utf-8'))
        value['receipt_id'] = 'CHANGED'
        (governor.root / 'FIXED_START.json').write_text(json.dumps(value), encoding='utf-8')
    else:
        campaign.settle_operation('synthetic_account', actual={'account_jobs': 1, 'wall_seconds': 1},
                                  outcome='FAILED', evidence_identity='SYNTHETIC_FAILED_NO_FREE_RETRY')
    with pytest.raises((PermissionError, ValueError, KeyError, BudgetLedgerMismatchError)):
        governor.active_execution('FIXED')


def test_next_guard_sees_local_revocation_and_expiry(continuous_guard, monkeypatch):
    from chanlun_trader.research_factory import etf_account_governance_v1 as module
    governor, *_ = continuous_guard
    governor.active_execution('FIXED')
    revoked = governor.root / 'REVOKED.json'
    revoked.write_text('{}', encoding='utf-8')
    with pytest.raises(PermissionError, match='STRATEGY_REVOKED'):
        governor.active_execution('FIXED')
    revoked.unlink()

    class FutureClock:
        @staticmethod
        def now(tz):
            return datetime.now(tz) + timedelta(hours=2)

        fromisoformat = staticmethod(datetime.fromisoformat)

    monkeypatch.setattr(module, 'datetime', FutureClock)
    with pytest.raises(PermissionError, match='STRATEGY_EXPIRED'):
        governor.active_execution('FIXED')


@pytest.mark.parametrize('change', ['missing_access', 'wrong_identity'])
def test_guard_rejects_parent_trial_mirror_gap_without_repair(continuous_guard, change):
    governor, campaign, *_ = continuous_guard
    # 末尾仍留合法独立父事件，不能靠快照head已丢失而偶然拒绝。
    campaign.set_stage('CONFIRMATION', 'WAITING', 'SYNTHETIC_UNRELATED_TAIL')
    path = campaign.budget_path.with_name('run_budget_events.jsonl')
    original = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    events = []
    changed = 0
    for event in original:
        if event['payload'].get('trial_id') == 'synthetic_account':
            if change == 'missing_access' and event['event_type'] == 'RUN_TRIAL_PERFORMANCE_ACCESSED':
                changed += 1
                continue
            if change == 'wrong_identity' and event['event_type'].startswith('RUN_TRIAL_'):
                event['payload']['candidate_id'] = 'SYNTHETIC_WRONG_IDENTITY'
                event['event_id'] = stable_hash({key: event[key] for key in ('run_id', 'event_type', 'payload')})
                changed += 1
        events.append(event)
    assert changed and events[-1] == original[-1]
    path.write_text(''.join(json.dumps(event, ensure_ascii=False) + '\n' for event in events), encoding='utf-8')
    before = {item: item.read_bytes() for item in (path, campaign.budget_path,
              governor.root / 'FIXED_START.json', governor.receipt_path, governor.budget_path)}
    with pytest.raises(BudgetLedgerMismatchError):
        governor.active_execution('FIXED')
    assert all(item.read_bytes() == data for item, data in before.items())



def test_campaign_boundary_views_are_local_and_tail_replay_is_retained(tmp_path, monkeypatch, record_property):
    from test_universe_execution_profile_v1 import campaign as segmented_campaign
    campaign = segmented_campaign(tmp_path)
    native = AutonomousRunBudgetV2.campaign_view
    counts = []

    def counted(budget):
        counts.append(1)
        return native(budget)

    monkeypatch.setattr(AutonomousRunBudgetV2, 'campaign_view', counted)
    for name in ('peek_status', '_peek_execution_status', 'effective_execution_limit'):
        counts.clear()
        method = getattr(campaign, name)
        method('operation') if name == 'effective_execution_limit' else method()
        record_property(name + '_full_replays', len(counts))
        assert len(counts) == 1
    counts.clear()
    dispatched = campaign.start_execution_segment('operation', segment_number=1)
    record_property('dispatch_full_replays', len(counts))
    assert len(counts) == 4 and dispatched['segment_number'] == 1
    counts.clear()
    charged = campaign.end_execution_segment('operation', segment_number=1, seconds=1,
                                             evidence_identity='SYNTHETIC_MEASURED_RESOURCE')
    record_property('charge_full_replays', len(counts))
    assert len(counts) == 4 and charged['seconds'] == 1


def test_mutating_view_rechecks_owner_after_trial_mirror_processing(continuous_guard, monkeypatch):
    _, campaign, approvals, approval, _ = continuous_guard
    event_path = campaign.budget_path.with_name('run_budget_events.jsonl')
    before = event_path.read_bytes()
    native = AutonomousRunBudgetV2.mark_performance_started
    removed = []

    def revoke_after_trial_processing(budget, trial_id):
        result = native(budget, trial_id)
        if not removed:
            (approvals.directory / (approval['approval_id'] + '.json')).write_text('{}', encoding='utf-8')
            removed.append(trial_id)
        return result

    monkeypatch.setattr(AutonomousRunBudgetV2, 'mark_performance_started', revoke_after_trial_processing)
    with pytest.raises(PermissionError):
        campaign.status()
    assert removed and event_path.read_bytes() == before



def test_mutation_rechecks_owner_before_trial_repair_after_constructor(continuous_guard, monkeypatch):
    _, campaign, approvals, approval, _ = continuous_guard
    campaign.set_stage('CONFIRMATION', 'WAITING', 'SYNTHETIC_TAIL_AFTER_CRASH')
    event_path = campaign.budget_path.with_name('run_budget_events.jsonl')
    original = [json.loads(line) for line in event_path.read_text(encoding='utf-8').splitlines()]
    remaining = [row for row in original if not (row['event_type'] == 'RUN_TRIAL_PERFORMANCE_ACCESSED'
                 and row['payload'].get('trial_id') == 'synthetic_account')]
    assert len(remaining) == len(original) - 1 and remaining[-1] == original[-1]
    event_path.write_text(''.join(json.dumps(row) + '\n' for row in remaining), encoding='utf-8')
    before = event_path.read_bytes()
    native = AutonomousRunBudgetV2._persist
    revoked = []

    def revoke_after_constructor_snapshot(budget):
        result = native(budget)
        if not revoked:
            (approvals.directory / (approval['approval_id'] + '.json')).write_text('{}', encoding='utf-8')
            revoked.append(True)
        return result

    monkeypatch.setattr(AutonomousRunBudgetV2, '_persist', revoke_after_constructor_snapshot)
    with pytest.raises(PermissionError):
        campaign.status()
    assert revoked and event_path.read_bytes() == before


@pytest.mark.parametrize('policy', [{}, False, [], ''])
def test_readonly_metadata_rejects_non_none_empty_scope(continuous_guard, policy):
    _, campaign, *_ = continuous_guard
    authority = json.loads(campaign.authorization_path.read_text(encoding='utf-8'))
    authority['scope_policy'] = policy
    campaign.authorization_path.write_text(json.dumps(authority), encoding='utf-8')
    before = {path: path.read_bytes() for path in campaign.directory.rglob('*') if path.is_file()}
    with pytest.raises(PermissionError, match='CAMPAIGN_SCOPE_IDENTITY_CONFLICT'):
        campaign.peek_status()
    assert before == {path: path.read_bytes() for path in campaign.directory.rglob('*') if path.is_file()}


def test_readonly_constructor_rechecks_owner_after_metadata(continuous_guard, monkeypatch):
    _, campaign, approvals, approval, _ = continuous_guard
    before = {path: path.read_bytes() for path in campaign.directory.rglob('*') if path.is_file()}
    native = campaign._authorization_metadata

    def revoke_after_metadata():
        value = native()
        (approvals.directory / (approval['approval_id'] + '.json')).write_text('{}', encoding='utf-8')
        return value

    monkeypatch.setattr(campaign, '_authorization_metadata', revoke_after_metadata)
    with pytest.raises(PermissionError):
        campaign.peek_status()
    assert before == {path: path.read_bytes() for path in campaign.directory.rglob('*') if path.is_file()}


def test_readonly_create_validates_owner_before_constructing_budget(continuous_guard, monkeypatch):
    _, campaign, approvals, approval, _ = continuous_guard
    (approvals.directory / (approval['approval_id'] + '.json')).write_text('{}', encoding='utf-8')

    def unexpected_constructor(*args, **kwargs):
        pytest.fail('create must validate Owner before budget construction')

    monkeypatch.setattr(AutonomousRunBudgetV2, '__init__', unexpected_constructor)
    with pytest.raises(PermissionError):
        campaign._budget(create=True, readonly=True)
