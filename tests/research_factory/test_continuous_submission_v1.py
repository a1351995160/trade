"""公共 V4 工程回归：真实受限 worker + 合成原件，不代表真实业务验收。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory.campaign_scope_v1 import (
    CampaignScopeV1, OwnerApprovalStoreV1, bind_campaign_scope, scope_summary,
)
from chanlun_trader.research_factory.continuous_research_contract_v1 import continuous_research_contract
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
from chanlun_trader.research_factory.run_budget import AutonomousRunBudgetV2
from chanlun_trader.research_factory.strategy_submission_v1 import StrategySubmissionV1, public_rule_factory
from test_continuous_research_contract_v1 import build, scope
from test_long_horizon_public_submission_v1 import long_public_case
from test_strategy_submission_v1 import connected, node


def continuous_public_case(tmp_path, *, exploration_minimum_sessions=252, candidates_budget=2):
    legacy, request, parent_authority, accesses = long_public_case(tmp_path)
    request['rule'].update(version='RESEARCH_RULE_STRATEGY_V4',
        hypothesis='三板块合成全池趋势及波动评分', change_reason='测试持续公共链',
        indicator_instances=[{'instance_id': 'ma', 'id': 'MA', 'version': 'MA_ARITHMETIC_V1',
                              'params': {'window': 2}},
                             {'instance_id': 'vol', 'id': 'ROLLING_VOLATILITY',
                              'version': 'ROLLING_VOLATILITY_V1', 'params': {'window': 2}}],
        buy=node('gt', node('field', 'close'),
                 node('indicator', 'ma', output='ma', version='MA_ARITHMETIC_V1')),
        selection={'score': node('indicator', 'vol', output='volatility', version='ROLLING_VOLATILITY_V1'),
                   'direction': 'ASCENDING', 'tie_breaker': 'SYMBOL_ASCENDING'})
    snapshot = capabilities()
    frozen_scope = scope(snapshot, confirmation=False)
    dataset = legacy.provider.catalog()['datasets'][0]
    frozen_scope.update(universe_id=request['universe_id'], universe_hash=dataset['universe_identity'],
        max_positions=request['max_positions'], execution_profiles=deepcopy(parent_authority['execution_profiles']),
        indicator_roles={'MA': ['BUY', 'SELL'], 'ROLLING_VOLATILITY': ['SCORE']})
    frozen_scope['data_routes']['EXPLORATION'].update(dataset_id='sample',
        dataset_hash=dataset['metadata_hash'], start='2020-01-01', end='2025-07-31')
    contract = build(snapshot, frozen_scope=frozen_scope)
    contract = continuous_research_contract(objective_id=contract['objective_id'], scope=contract['scope'],
        exploration_policy={**contract['exploration_policy'],
                            'minimum_account_sessions': exploration_minimum_sessions},
        final_criteria=contract['final_criteria'], criteria_source=contract['criteria_source'],
        capabilities_snapshot=snapshot)
    campaign_root = tmp_path / 'campaign'
    campaign_root.mkdir()
    units = {name: 100000 for name in AutonomousRunBudgetV2.resource_names}
    units.update(account_jobs=4 * candidates_budget, candidate_attempts=2 * candidates_budget,
                 wall_seconds=1000000)
    stage_units = {phase: {name: amount // 2 for name, amount in units.items()}
                   for phase in ('EXPLORATION', 'CONFIRMATION')}
    authorization = {'authorization_id': 'SYNTHETIC_CONTINUOUS_PUBLIC', 'objective_id': contract['objective_id'],
        'resource_limits': units, 'stages': list(stage_units),
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        'max_batches': max(4, candidates_budget), 'max_total_predictive_trials': max(20, 6 * candidates_budget),
        'max_trials_per_batch': max(10, 3 * candidates_budget),
        'max_hypotheses_per_batch': candidates_budget, 'max_candidates_per_batch': candidates_budget,
        'execution_profiles': contract['scope']['execution_profiles']}
    owner = object()
    approvals = OwnerApprovalStoreV1(tmp_path / 'owner', owner_capability=owner)
    summary = scope_summary(campaign_root, authorization, contract, stage_units)
    approval = approvals.approve(summary, approver='SYNTHETIC_FIXTURE_OWNER', capability=owner)
    authorization = bind_campaign_scope(campaign_root, authorization, contract=contract,
        stage_limits=stage_units, approvals=approvals, approval_ref=approval)
    campaign = ResearchCampaignV1.create(campaign_root, authorization)
    parent_authority['objective_id'] = contract['objective_id']

    def new_service():
        restored_campaign = ResearchCampaignV1(campaign_root, authorization['authorization_id'])
        return StrategySubmissionV1(legacy.provider, lambda ref: deepcopy(parent_authority),
            legacy.root, connected, continuous_scope=CampaignScopeV1(restored_campaign))

    service = new_service()
    candidate = public_rule_factory(request['rule'], request['strategy_id']).rule_identity
    bound = service.bind_research_request(request, phase='EXPLORATION',
        candidate_identity=candidate, batch_id='SYNTHETIC_SAME_BATCH')
    return service, bound, campaign, accesses, new_service


def budget_usage(campaign):
    value = campaign.peek_status()
    return {key: deepcopy(value[key]) for key in ('used', 'reserved', 'operations', 'trial_usage', 'batch_count')}


def test_synthetic_fixture_short_window_and_candidate_limit_are_fixed_before_owner_approval(tmp_path):
    _, _, campaign, _, _ = continuous_public_case(tmp_path, exploration_minimum_sessions=20,
                                                  candidates_budget=3)
    base = campaign.peek_status()['base_authorization']
    summary = base['scope_policy']['summary']
    assert summary['contract']['exploration_policy']['minimum_account_sessions'] == 20
    assert summary['stage_limits']['EXPLORATION']['candidate_attempts'] == 3
    assert summary['stage_limits']['EXPLORATION']['account_jobs'] == 6
    assert summary['stage_limits']['CONFIRMATION']['account_jobs'] == 6
    assert summary['contract']['final_criteria']['exploration_minimum_actual_sessions'] == 504
    assert summary['contract']['final_criteria']['independent_validation_minimum_actual_sessions'] == 252


def test_v4_preview_status_and_reloaded_preparation_do_not_dispatch_or_consume(tmp_path, monkeypatch):
    from chanlun_trader.research_factory import universe_scan_service_v1 as scans
    service, request, campaign, accesses, reload_service = continuous_public_case(tmp_path)
    monkeypatch.setattr(scans, 'run_bounded_worker', lambda *a, **k: pytest.fail('只读及 BEGIN 不派发'))
    before = budget_usage(campaign)
    preview = service.preview(request)
    assert preview['coverage']['target_count'] == 3
    assert preview['request']['symbols'] == ['000001.SZ', '300001.SZ', '600000.SH']
    assert preview['phase'] == 'EXPLORATION'
    begun = service.freeze(request, preview['preview_identity'])
    assert begun['status'] == 'PREPARING' and begun['dispatched_segments'] == 0
    reloaded = reload_service()
    assert reloaded.preview(request)['preview_identity'] == preview['preview_identity']
    assert reloaded.freeze(request, preview['preview_identity'])['task_id'] == begun['task_id']
    assert reloaded.status(begun['task_id'])['status'] == 'PREPARING'
    assert budget_usage(campaign) == before and accesses == []


@pytest.mark.parametrize('field', ['symbols', 'runtime', 'loader'])
def test_v4_cannot_replace_deployment_pool_or_execution_routes(tmp_path, field):
    service, request, campaign, accesses, _ = continuous_public_case(tmp_path)
    before = budget_usage(campaign)
    request[field] = ['000001.SZ'] if field == 'symbols' else {'path': 'arbitrary'}
    with pytest.raises(ValueError, match='CONTINUOUS_PUBLIC_REQUEST_FIELDS_INVALID'):
        service.preview(request)
    assert budget_usage(campaign) == before and accesses == []


def test_v4_real_bounded_workers_share_batch_phase_and_resume_without_duplicate_work(tmp_path, monkeypatch):
    from chanlun_trader import synthetic_batch_resources as resources
    from chanlun_trader.research_factory import universe_scan_service_v1 as scans
    service, request, campaign, _, reload_service = continuous_public_case(tmp_path)
    original = resources.run_bounded_worker
    launches = []

    def real_bounded(command, **kwargs):
        result = original(command, **kwargs)
        launches.append({'command': list(command), 'execution': deepcopy(kwargs.get('execution')), 'result': result})
        return result

    monkeypatch.setattr(resources, 'run_bounded_worker', real_bounded)
    monkeypatch.setattr(scans, 'run_bounded_worker', real_bounded)
    preview = service.preview(request)
    begun = service.freeze(request, preview['preview_identity'])
    task_id = begun['task_id']
    assert not launches
    results = []
    for step in range(40):
        before = len(launches)
        result = service.advance(task_id)
        results.append(result)
        dispatched = len(launches) - before
        assert dispatched <= 1, result
        assert result['dispatched_segments'] == dispatched
        if step == 0:
            service = reload_service()
            assert service.freeze(request, preview['preview_identity'])['task_id'] == task_id
        usage = budget_usage(campaign)
        count = len(launches)
        service.status(task_id)
        assert service.preview(request)['preview_identity'] == preview['preview_identity']
        assert len(launches) == count and budget_usage(campaign) == usage
        if result['status'] == 'ACCOUNT_VERIFIED':
            break
        assert result['status'] != 'EVIDENCE_BLOCKED', result
    else:
        pytest.fail('合成短窗应在有限 advance 内完成：' + str([row['status'] for row in results]))

    assert result['verification']['advance_allowed'] is True and result['strategy_qualified'] is False
    assert all(row['result']['returncode'] in (0, 75) and not row['result']['timed_out'] for row in launches)
    task = service._task(task_id)
    root = Path(task['job_path']).parent
    job = json.loads(Path(task['job_path']).read_text(encoding='utf-8'))
    assert len(job['plans']) == 2
    qualified = task['qualification_scope']
    assert qualified['target_symbols'] == preview['request']['symbols']
    assert sorted(qualified['qualified_symbols'] + [row['symbol'] for row in qualified['excluded']]) == preview['request']['symbols']
    for name in job['plans']:
        report = json.loads((root / (name + '_RESEARCH_REPORT.json')).read_text(encoding='utf-8'))
        funnel = json.loads((root / (name + '_SIGNAL_FUNNEL.json')).read_text(encoding='utf-8'))
        assert report['input_identity'] == job['input_identity']
        assert report['strategy_qualified'] is False and report['paper_qualified'] is False
        assert funnel['identity'] and (root / (name + '_REPORT.md')).is_file()
        assert len(list(root.glob(name + '_START.json'))) == 1
    view = campaign.peek_status()
    assert len(view['operations']) == 5
    assert {operation['batch_id'] for operation in view['operations'].values()} == {'SYNTHETIC_SAME_BATCH'}
    assert {operation['stage'] for operation in view['operations'].values()} == {'EXPLORATION'}
    assert all(operation['status'] == 'COMPLETED' for operation in view['operations'].values())
    assert view['used']['account_jobs'] == 2 and view['used']['data_experiments'] == 1
    assert view['used']['verification_jobs'] == 2 and view['batch_count'] == 1
    assert view['stage_remaining']['CONFIRMATION'] == view['authorization']['scope_policy']['summary']['stage_limits']['CONFIRMATION']
    before, count = budget_usage(campaign), len(launches)
    restored = reload_service()
    assert restored.advance(task_id)['status'] == 'ACCOUNT_VERIFIED'
    assert restored.freeze(request, preview['preview_identity'])['task_id'] == task_id
    assert budget_usage(campaign) == before and len(launches) == count
