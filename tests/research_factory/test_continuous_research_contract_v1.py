"""隔离合同测试；样本证据不代表真实账户、模型或独立资料验收。"""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json

import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.continuous_research_contract_v1 import (
    FINAL_MINIMUMS, REPORT_REQUIREMENTS, account_window_sessions,
    continuous_research_contract, continuous_research_preflight,
    evaluate_final_business_goal, import_final_criteria,
    validate_continuous_research_contract,
)
from chanlun_trader.research_factory.formal_account_backend_v1 import BASE_COSTS, STRESS_COSTS
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile


@pytest.fixture(scope='module')
def snapshot():
    return capabilities()


def criteria():
    return {'version': 'USER_STRATEGY_BUSINESS_ACCEPTANCE_V1',
        'status': 'FROZEN_BEFORE_NEW_CANDIDATE_RESULTS',
        'scope': 'ALL_REGISTERED_SUPPORTED_TARGETS_THEN_ALL_DATA_QUALIFIED_SYMBOLS',
        'submission_version': 'FULL_UNIVERSE_SUBMISSION_V3', 'account_scope': 'DATA_QUALIFIED',
        'selection_rule_version': 'RESEARCH_RULE_STRATEGY_V4',
        'initial_cash_each_independent_cost_account': 50000,
        'exploration_minimum_actual_sessions': 504,
        'independent_validation_minimum_actual_sessions': 252,
        'thresholds_each_stage': deepcopy(FINAL_MINIMUMS),
        'account_equity_includes_open_positions': True,
        'independence_requires_prior_access_review_and_post_freeze_unseen_data': True,
        'cannot_pool_stage_or_cost_account_profits': True,
        'concentration_required': ['symbol', 'complete_round_trip', 'time_period'],
        'benchmarks': ['CASH', 'SYSTEM_SUPPORTED_PRICE_REFERENCE_WITH_INVESTABILITY_DISCLOSED'],
        'formal_statistics': 'ONLY_IF_CURRENT_METHOD_PROVES_APPLICABLE',
        'paper_actual_days_at_registration': 0, 'total_goal_complete': False}


def scope(snapshot, *, confirmation=True):
    result = {'universe_id': 'registered_universe', 'universe_hash': stable_hash('registered_universe'),
        'boards': [item['id'] for item in snapshot['full_universe']['boards']],
        'selection_policy': 'ALL_REGISTERED_TARGETS_THEN_STRATEGY_SIGNALS',
        'account_scope': 'DATA_QUALIFIED', 'initial_cash': 50000, 'max_positions': 5,
        'cost_profiles': {'BASE': deepcopy(BASE_COSTS), 'STRESS': deepcopy(STRESS_COSTS)},
        'data_routes': {stage: {'dataset_id': stage.lower(), 'dataset_hash': stable_hash(stage),
            'start': '2020-01-01', 'end': '2028-12-31',
            'purpose': 'TRAIN' if stage == 'EXPLORATION' else 'INDEPENDENT_VALIDATION'}
            for stage in ('EXPLORATION', 'CONFIRMATION')},
        'rule_version': 'RESEARCH_RULE_STRATEGY_V4',
        'indicator_roles': {'MA': ['BUY', 'SELL'], 'RSI': ['BUY', 'SCORE']},
        'mechanism_combinations': [['TREND', 'MOMENTUM'], ['VOLATILITY', 'RANKING']],
        'ranking_variables': {'score_operators': list(snapshot['long_horizon']['score_operators']),
            'score_directions': list(snapshot['long_horizon']['score_directions']),
            'tie_breaker': snapshot['long_horizon']['tie_breaker']},
        'market_filter_scope': 'SAME_SYMBOL_ONLY',
        'execution_profiles': [execution_profile(SEGMENTED_PROFILE, 252),
                               execution_profile(SEGMENTED_PROFILE, 504)]}
    if not confirmation:
        result['data_routes']['CONFIRMATION'] = None
    return result


def build(snapshot, *, frozen_scope=None, frozen_criteria=None):
    final = criteria() if frozen_criteria is None else frozen_criteria
    raw = json.dumps(final, ensure_ascii=False, indent=2).encode('utf-8')
    return continuous_research_contract(objective_id='continuous_objective',
        scope=scope(snapshot) if frozen_scope is None else frozen_scope,
        exploration_policy={'version': 'SHORT_EXPLORATION_V1', 'minimum_account_sessions': 252,
                            'minimum_complete_round_trips': 10, 'thresholds': {}},
        final_criteria=final, criteria_source={'path': 'synthetic/FROZEN_CRITERIA.json',
            'sha256': hashlib.sha256(raw).hexdigest(), 'content_hash': stable_hash(final)},
        capabilities_snapshot=snapshot)


def calendar(length, start=date(2020, 1, 1)):
    days = []
    while len(days) < length:
        if start.weekday() < 5:
            days.append(start.isoformat())
        start += timedelta(days=1)
    return days


def data(stage, count, *, warmup=120):
    days = calendar(count + warmup)
    return {'dataset_id': stage.lower(), 'dataset_hash': stable_hash(stage),
        'feature_start': days[0], 'account_start': days[warmup], 'account_end': days[-1],
        'calendar': days, 'required_warmup_sessions': 120,
        'target_count': 4607, 'qualified_count': 3669,
        'independent': True, 'post_freeze_unseen': True, 'family_frozen': True, 'exposed': False}


def readiness():
    return {'authorization': {'EXPLORATION': True, 'CONFIRMATION': True},
        'resources': {'EXPLORATION': True, 'CONFIRMATION': True},
        'execution': True, 'storage': True,
        'model': {'available': True, 'hard_budget_enforced': True},
        'final_exploration_evidence_ready': True}


def evidence(stage, count):
    # 这里只测纯投影的字段边界，不生成正式账户工件或授权。
    info = data(stage, count)
    days = info['calendar'][120:]
    rule, input_id = stable_hash('same_rule'), stable_hash(stage)
    accounts = {}
    for cost, costs in (('BASE', BASE_COSTS), ('STRESS', STRESS_COSTS)):
        accounts[cost] = {'initial_cash': 50000, 'account_dates': list(days),
            'audit_passed': True, 'evidence_identity': stable_hash(stage + cost),
            'input_identity': input_id, 'rule_identity': rule, 'cost_profile': deepcopy(costs),
            'annualized_net_return': .15, 'maximum_drawdown': .15,
            'complete_round_trip_net_win_rate': .55, 'complete_round_trip_net_profit_factor': 1.3,
            'complete_round_trips': 100, 'total_net_return': .01}
    return {'evidence_kind': 'REAL_ACCOUNT', 'rule_identity': rule, 'input_identity': input_id,
        'dataset_id': info['dataset_id'], 'dataset_hash': info['dataset_hash'],
        'account_start': days[0], 'account_end': days[-1], 'accounts': accounts,
        'report_sections': list(REPORT_REQUIREMENTS),
        'concentration_dimensions': criteria()['concentration_required'],
        'benchmarks': criteria()['benchmarks'], 'equity_includes_open_positions': True,
        'independence': {'prior_access_review_passed': True, 'post_freeze_unseen': True,
                         'family_frozen': True, 'exposure_identity': stable_hash('exposure')}}


def test_short_exploration_is_ready_while_final_and_confirmation_remain_incomplete(snapshot):
    contract = build(snapshot, frozen_scope=scope(snapshot, confirmation=False))
    before = deepcopy(contract)
    result = continuous_research_preflight(contract, exploration_data=data('EXPLORATION', 462),
                                           readiness=readiness())
    assert result['channels']['EXPLORATION']['can_run'] is True
    assert result['channels']['CONFIRMATION']['can_run'] is False
    assert result['channels']['EXPLORATION']['available_account_sessions'] == 462
    assert result['final_business_gaps'] == [
        {'stage': 'EXPLORATION', 'reason': 'FINAL_ACCOUNT_SESSIONS_INSUFFICIENT',
         'required': 504, 'available': 462, 'shortfall': 42},
        {'stage': 'CONFIRMATION', 'reason': 'FINAL_ACCOUNT_SESSIONS_INSUFFICIENT',
         'required': 252, 'available': 0, 'shortfall': 252}]
    assert result['authorization_granted'] is False and result['read_only'] is True
    assert contract == before


@pytest.mark.parametrize('exploration_days,confirmation_days,passed',
                         [(503, 252, False), (504, 251, False), (504, 252, True)])
def test_final_actual_days_and_threshold_boundaries(snapshot, exploration_days, confirmation_days, passed):
    report = evaluate_final_business_goal(build(snapshot),
        exploration=evidence('EXPLORATION', exploration_days),
        confirmation=evidence('CONFIRMATION', confirmation_days),
        formal_qualification={'status': 'WAITING_METHOD'}, paper={'actual_days': 0})
    assert report['business_goal_met'] is passed
    assert report['strategy_qualified'] is False
    assert report['formal_qualification'] == {'status': 'WAITING_METHOD'}
    assert report['paper'] == {'actual_days': 0}
    assert report['pooled_profits_used'] is False


def test_warmup_not_counted_and_flat_account_days_count(snapshot):
    info = data('EXPLORATION', 503)
    counts = account_window_sessions(info['calendar'], feature_start=info['feature_start'],
        account_start=info['account_start'], account_end=info['account_end'])
    assert counts == {'warmup_sessions': 120, 'account_sessions': 503}
    account = evidence('EXPLORATION', 503)
    # 账户日历独立于持股及交易次数；把预热塞入实际账户日期须拒绝。
    account['accounts']['BASE']['account_dates'] = info['calendar']
    result = evaluate_final_business_goal(build(snapshot), exploration=account,
                                         confirmation=evidence('CONFIRMATION', 252))
    assert 'BASE_ACCOUNT_DATES_OUT_OF_SCOPE' in result['stages']['EXPLORATION']['failure_codes']
    assert result['business_goal_met'] is False


def test_warmup_deficit_blocks_execution_without_changing_final_standard(snapshot):
    info = data('EXPLORATION', 504, warmup=119)
    result = continuous_research_preflight(build(snapshot), exploration_data=info, readiness=readiness())
    assert 'WARMUP_INSUFFICIENT' in result['channels']['EXPLORATION']['waiting_reasons']
    assert result['channels']['EXPLORATION']['can_run'] is False


def test_final_metric_failure_cannot_be_overridden_by_screening_or_other_cost_profit(snapshot):
    exploration = evidence('EXPLORATION', 504)
    exploration['screening_passed'] = True
    exploration['accounts']['STRESS']['total_net_return'] = 0
    exploration['accounts']['BASE']['annualized_net_return'] = 5
    result = evaluate_final_business_goal(build(snapshot), exploration=exploration,
                                         confirmation=evidence('CONFIRMATION', 252))
    assert result['business_goal_met'] is False
    assert 'STRESS_TOTAL_NET_RETURN_FAILED' in result['stages']['EXPLORATION']['failure_codes']


def test_frozen_work_continues_during_model_wait_and_method_does_not_block_business(snapshot):
    ready = readiness()
    ready.update(model={'available': False, 'hard_budget_enforced': False}, frozen_exploration_work=True)
    result = continuous_research_preflight(build(snapshot), exploration_data=data('EXPLORATION', 504),
        confirmation_data=data('CONFIRMATION', 252), readiness=ready)
    assert result['channels']['EXPLORATION']['can_execute_deterministic'] is True
    assert result['channels']['EXPLORATION']['can_run'] is True
    assert result['channels']['EXPLORATION']['can_propose'] is False
    assert result['channels']['CONFIRMATION']['can_run'] is True
    assert result['model_waiting_reasons'] == ['MODEL_UNAVAILABLE', 'REAL_MODEL_BUDGET_UNSUPPORTED']
    assert result['formal_qualification'] == 'WAITING_METHOD'


def test_short_exploration_does_not_spend_ready_independent_data(snapshot):
    ready = readiness()
    ready['final_exploration_evidence_ready'] = False
    result = continuous_research_preflight(build(snapshot), exploration_data=data('EXPLORATION', 462),
        confirmation_data=data('CONFIRMATION', 252), readiness=ready)
    assert result['channels']['EXPLORATION']['can_run'] is True
    assert 'FINAL_EXPLORATION_EVIDENCE_NOT_READY' in result['channels']['CONFIRMATION']['waiting_reasons']


@pytest.mark.parametrize('field,value,reason', [
    ('initial_cash', 10000, 'INITIAL_CASH'),
    ('boards', ['SZ_MAIN', 'SH_MAIN'], 'FULL_REGISTERED_SCOPE'),
    ('selection_policy', 'FIRST_FIVE_SYMBOLS', 'FULL_REGISTERED_SCOPE'),
    ('market_filter_scope', 'EXTERNAL_INDEX_TIMING', 'MARKET_OR_TIE_BREAKER'),
    ('indicator_roles', {'MA': ['BUY'], 'UNREGISTERED': ['SELL']}, 'MULTI_INDICATOR'),
    ('indicator_roles', {'MA': ['BUY'], 'RSI': ['FILTER']}, 'INDICATOR_ROLES'),
    ('rule_version', 'RESEARCH_RULE_STRATEGY_V3', 'RULE_VERSION'),
])
def test_scope_rejects_fund_board_indicator_and_market_overclaims(snapshot, field, value, reason):
    configured = scope(snapshot)
    configured[field] = value
    with pytest.raises(ValueError, match=reason):
        build(snapshot, frozen_scope=configured)


def test_cost_score_and_date_bounds_are_frozen(snapshot):
    configured = scope(snapshot)
    configured['cost_profiles']['STRESS']['min_commission'] = 0
    with pytest.raises(ValueError, match='COST_PROFILES'):
        build(snapshot, frozen_scope=configured)
    configured = scope(snapshot)
    configured['ranking_variables']['score_operators'].append('arbitrary_python')
    with pytest.raises(ValueError, match='RANKING_UNSUPPORTED'):
        build(snapshot, frozen_scope=configured)
    configured = scope(snapshot)
    configured['data_routes']['EXPLORATION']['start'] = '2029-01-01'
    with pytest.raises(ValueError, match='RANGE_OR_PURPOSE'):
        build(snapshot, frozen_scope=configured)
    info = data('EXPLORATION', 252)
    info['feature_start'] = '2019-12-31'
    result = continuous_research_preflight(build(snapshot), exploration_data=info, readiness=readiness())
    assert 'DATA_WINDOW_OUT_OF_SCOPE' in result['channels']['EXPLORATION']['waiting_reasons']


@pytest.mark.parametrize('key,value', [
    ('exploration_minimum_actual_sessions', 503),
    ('independent_validation_minimum_actual_sessions', 251),
    ('initial_cash_each_independent_cost_account', 10000),
    ('cannot_pool_stage_or_cost_account_profits', False),
])
def test_initial_screening_never_lowers_final_standards(snapshot, key, value):
    final = criteria()
    final[key] = value
    with pytest.raises(ValueError, match='CONTINUOUS_FINAL'):
        build(snapshot, frozen_criteria=final)


@pytest.mark.parametrize('key,value', [(key, bound + .01 if key.endswith('_maximum') else bound - .01)
                                     for key, bound in FINAL_MINIMUMS.items()])
def test_final_thresholds_cannot_be_lowered(snapshot, key, value):
    final = criteria()
    final['thresholds_each_stage'][key] = value
    with pytest.raises(ValueError, match='THRESHOLD_LOWERED'):
        build(snapshot, frozen_criteria=final)


def test_goal_and_data_changes_need_new_content_identity(snapshot):
    original = build(snapshot)
    changed = deepcopy(original)
    changed['final_criteria']['exploration_minimum_actual_sessions'] = 505
    with pytest.raises(ValueError, match='IDENTITY_CONFLICT'):
        validate_continuous_research_contract(changed, snapshot)
    final = criteria()
    final['exploration_minimum_actual_sessions'] = 505
    assert build(snapshot, frozen_criteria=final)['content_hash'] != original['content_hash']
    configured = scope(snapshot)
    configured['data_routes']['EXPLORATION']['dataset_hash'] = stable_hash('new_dataset')
    assert build(snapshot, frozen_scope=configured)['content_hash'] != original['content_hash']
    current = deepcopy(snapshot)
    current['fingerprint'] = stable_hash('new_capability_version')
    with pytest.raises(ValueError, match='CAPABILITIES_CHANGED_NEW_VERSION_REQUIRED'):
        validate_continuous_research_contract(original, current)


def test_criteria_import_preserves_source_and_separates_byte_and_content_hash(tmp_path):
    path = tmp_path / 'FROZEN_CRITERIA.json'
    raw = json.dumps(criteria(), ensure_ascii=False, indent=2).encode('utf-8')
    path.write_bytes(raw)
    imported = import_final_criteria(path)
    assert path.read_bytes() == raw
    assert imported['criteria'] == criteria()
    assert imported['source']['sha256'] == hashlib.sha256(raw).hexdigest()
    assert imported['source']['content_hash'] == stable_hash(criteria())
    assert imported['source']['sha256'] != imported['source']['content_hash']
    assert list(tmp_path.iterdir()) == [path]


def test_trusted_future_route_can_freeze_before_dataset_exists(snapshot):
    configured = scope(snapshot)
    route = {'route_id': 'approved_train', 'producer_identity': 'registered_producer_v1',
        'source_ids': ['REGISTERED_ORIGINALS_V1'], 'start': '2020-01-01', 'end': '2028-12-31',
        'purpose': 'TRAIN', 'universe_hash': configured['universe_hash'],
        'quality_policy': {'version': 'REGISTERED_QUALITY_POLICY_V1', 'unknown': 'BLOCK'}}
    configured['data_routes']['EXPLORATION'] = route
    contract = build(snapshot, frozen_scope=configured)
    result = continuous_research_preflight(contract, readiness=readiness())
    assert result['channels']['EXPLORATION']['waiting_reasons'][0] == 'DATA_NOT_REGISTERED'
    info = {**data('EXPLORATION', 252), **{key: route[key] for key in
        ('route_id', 'producer_identity', 'source_ids', 'universe_hash', 'quality_policy')}}
    assert continuous_research_preflight(contract, exploration_data=info,
        readiness=readiness())['channels']['EXPLORATION']['can_run'] is True
    configured['data_routes']['EXPLORATION']['source_ids'] = ['*']
    with pytest.raises(ValueError, match='TRUSTED_ROUTE_IDENTITY'):
        build(snapshot, frozen_scope=configured)


def test_exposed_confirmation_and_mismatched_results_cannot_meet_goal(snapshot):
    info = data('CONFIRMATION', 252)
    info['exposed'] = True
    result = continuous_research_preflight(build(snapshot), exploration_data=data('EXPLORATION', 504),
        confirmation_data=info, readiness=readiness())
    assert result['channels']['EXPLORATION']['can_run'] is True
    assert 'DATA_EXPOSED_OR_UNKNOWN' in result['channels']['CONFIRMATION']['waiting_reasons']
    confirmed = evidence('CONFIRMATION', 252)
    confirmed['accounts']['BASE']['input_identity'] = stable_hash('wrong_input')
    report = evaluate_final_business_goal(build(snapshot), exploration=evidence('EXPLORATION', 504),
                                         confirmation=confirmed)
    assert report['business_goal_met'] is False
    assert 'BASE_AUDITED_ACCOUNT_REQUIRED' in report['stages']['CONFIRMATION']['failure_codes']


def test_zero_loss_profit_factor_uses_explicit_net_profit_basis_without_nonfinite_json(snapshot):
    confirmation = evidence('CONFIRMATION', 252)
    account = confirmation['accounts']['BASE']
    account['complete_round_trip_net_profit_factor'] = None
    account['complete_round_trip_net_profit_factor_basis'] = {
        'positive_net_profit_sum': 1000., 'absolute_negative_net_profit_sum': 0.}
    report = evaluate_final_business_goal(build(snapshot), exploration=evidence('EXPLORATION', 504),
                                         confirmation=confirmation)
    assert report['business_goal_met'] is True
    account['complete_round_trip_net_profit_factor_basis']['positive_net_profit_sum'] = 0.
    assert evaluate_final_business_goal(build(snapshot), exploration=evidence('EXPLORATION', 504),
                                       confirmation=confirmation)['business_goal_met'] is False
