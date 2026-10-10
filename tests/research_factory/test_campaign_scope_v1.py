"""父授权、分阶段保护、增量及不可伪造的执行引用。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from chanlun_trader.research_factory.budget import BudgetExhaustedError
from chanlun_trader.research_factory.campaign_scope_v1 import (
    CampaignScopeV1, OwnerApprovalStoreV1, bind_campaign_scope, grant_summary, scope_summary,
)
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.run_budget import AutonomousRunBudgetV2
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
from test_continuous_research_contract_v1 import build


def setup_scope(root):
    contract = build(capabilities())
    units = {name: 10000 for name in AutonomousRunBudgetV2.resource_names}
    units['account_jobs'] = 2
    stage_units = {stage: {name: amount // 2 for name, amount in units.items()}
                   for stage in ('EXPLORATION', 'CONFIRMATION')}
    authorization = {'authorization_id': 'continuous', 'objective_id': contract['objective_id'],
        'resource_limits': units, 'stages': list(stage_units),
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        'max_batches': 2, 'max_total_predictive_trials': 2, 'max_trials_per_batch': 2,
        'max_hypotheses_per_batch': 2, 'max_candidates_per_batch': 2,
        'execution_profiles': contract['scope']['execution_profiles']}
    capability = object()
    approvals = OwnerApprovalStoreV1(root / 'protected_owner', owner_capability=capability)
    summary = scope_summary(root, authorization, contract, stage_units)
    reference = approvals.approve(summary, approver='fixture_owner', capability=capability)
    authorization = bind_campaign_scope(root, authorization, contract=contract,
        stage_limits=stage_units, approvals=approvals, approval_ref=reference)
    return ResearchCampaignV1.create(root, authorization), approvals, capability, contract


def reserve(campaign, name, stage='EXPLORATION'):
    return campaign.reserve_operation(operation_id=name, batch_id=name, kind='ACCOUNT',
        stage=stage, subject_identity=stable_hash(name), upper_bounds={'account_jobs': 1, 'wall_seconds': 10})


def increment(campaign, approvals, capability):
    base = campaign.status()['base_authorization']
    units = {name: 0 for name in AutonomousRunBudgetV2.resource_names}
    units.update(account_jobs=2, wall_seconds=100)
    stages = {stage: {name: value // 2 for name, value in units.items()} for stage in base['stages']}
    grant = {'grant_id': 'explicit_extra', 'base_authorization_hash': stable_hash(base),
        'resource_limits_delta': units, 'stage_limits_delta': stages,
        'max_batches_delta': 2, 'max_total_predictive_trials_delta': 2,
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()}
    ref = approvals.approve(grant_summary(base, grant), approver='fixture_owner', capability=capability)
    return grant, ref


def test_execution_customer_cannot_approve_or_change_summary(tmp_path):
    campaign, approvals, capability, _ = setup_scope(tmp_path)
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_REQUIRED'):
        OwnerApprovalStoreV1(approvals.directory).approve({'confirmed': True}, approver='AI', capability=object())
    grant, ref = increment(campaign, approvals, capability)
    changed = deepcopy(grant)
    changed['resource_limits_delta']['account_jobs'] += 1
    with pytest.raises(PermissionError):
        campaign.add_grant(changed, approval_ref=ref)
    assert campaign.status()['authorization']['resource_limits']['account_jobs'] == 2


def test_confirmation_reserve_is_not_available_to_exploration(tmp_path):
    campaign, *_ = setup_scope(tmp_path)
    reserve(campaign, 'first')
    with pytest.raises(BudgetExhaustedError, match='STAGE_RESOURCE'):
        reserve(campaign, 'second')
    reserve(campaign, 'confirm', 'CONFIRMATION')
    assert campaign.status()['reserved']['account_jobs'] == 2


def test_explicit_increment_replays_once_without_losing_unknown_usage(tmp_path):
    campaign, approvals, capability, _ = setup_scope(tmp_path)
    reserve(campaign, 'first')
    campaign.start_operation('first')
    campaign.mark_unknown('first', 'lost worker receipt')
    grant, ref = increment(campaign, approvals, capability)
    campaign.add_grant(grant, approval_ref=ref)
    campaign.add_grant(grant, approval_ref=ref)
    reserve(campaign, 'second')
    campaign.budget_path.unlink()
    view = ResearchCampaignV1(tmp_path, 'continuous').status()
    assert view['operations']['first']['status'] == 'UNKNOWN'
    assert view['reserved']['account_jobs'] == 2
    assert view['authorization']['resource_limits']['account_jobs'] == 4
    assert len(view['grants']) == 1
    assert view['base_authorization']['resource_limits']['account_jobs'] == 2


def test_revocation_is_sticky_but_allows_historical_settlement(tmp_path):
    campaign, *_ = setup_scope(tmp_path)
    reserve(campaign, 'first')
    campaign.start_operation('first')
    campaign.revoke('owner cancellation')
    campaign.resume('cannot revive')
    with pytest.raises(PermissionError, match='REVOKED'):
        reserve(campaign, 'second')
    campaign.settle_operation('first', actual={'account_jobs': 1, 'wall_seconds': 3},
        outcome='COMPLETED', evidence_identity='settled_original')
    assert campaign.status()['used']['account_jobs'] == 1


def test_delegate_binds_dates_cash_scope_and_old_expiry(tmp_path):
    campaign, approvals, capability, contract = setup_scope(tmp_path)
    scope = contract['scope']
    request = {name: deepcopy(scope[name]) for name in
        ('initial_cash', 'max_positions', 'universe_id', 'universe_hash', 'rule_version', 'cost_profiles', 'execution_profiles')}
    request.update(phase='EXPLORATION', dataset_id='exploration', dataset_hash=stable_hash('EXPLORATION'),
        feature_start='2022-01-01', account_start='2022-07-01', account_end='2023-07-31')
    expiry = campaign.status()['authorization']['expires_at']
    service = CampaignScopeV1(campaign)
    ref = service.delegate(batch_id='batch1', candidate_identity=stable_hash('rule'), phase='EXPLORATION',
        request=request, expires_at=expiry)
    before = service.resolve(ref)
    changed = deepcopy(request)
    changed['initial_cash'] = 1000000
    with pytest.raises(PermissionError, match='CHILD_SCOPE'):
        service.delegate(batch_id='batch1', candidate_identity=stable_hash('rule'), phase='EXPLORATION',
            request=changed, expires_at=expiry)
    grant, approval = increment(campaign, approvals, capability)
    campaign.add_grant(grant, approval_ref=approval)
    assert service.resolve(ref) == before


def test_snapshot_reload_accepts_only_event_approved_effective_trial_limit(tmp_path):
    campaign, approvals, capability, _ = setup_scope(tmp_path)
    reserve(campaign, 'one')
    reserve(campaign, 'two', 'CONFIRMATION')
    grant, ref = increment(campaign, approvals, capability)
    campaign.add_grant(grant, approval_ref=ref)
    reserve(campaign, 'three')
    assert ResearchCampaignV1(tmp_path, 'continuous').status()['trial_usage']['remaining_capacity'] == 1
