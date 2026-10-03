from copy import deepcopy

import pytest

from scripts.run_long_horizon_universe_acceptance_v1 import CONFIG_VERSION, fixed_requests, reference_config, mechanism_requests, check_funnel
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
from chanlun_trader.research_factory.universe_execution_profile_v1 import execution_profile, SEGMENTED_PROFILE


def config():
    window = {'dataset_id': 'registered', 'universe_id': 'all_three_boards',
              'feature_start': 20220902, 'account_start': 20221205, 'account_end': 20241231}
    return {'version': CONFIG_VERSION, 'windows': {'252': {**window, 'account_start': 20231218}, '504': window},
            'authorization_refs': {'252': 'AUTHORIZED_252', '504': 'AUTHORIZED_504'},
            'engineering_authorization_ref': 'AUTHORIZED_CONTINUOUS_REFERENCE'}


def test_fixed_acceptance_has_no_profit_search_and_no_manual_small_pool():
    requests = fixed_requests(config(), capabilities())
    assert requests['252']['rule'] == requests['504']['rule']
    assert all('symbols' not in item and item['account_scope'] == 'DATA_QUALIFIED' for item in requests.values())
    assert all(item['initial_cash'] == 50000 and item['costs'] == ['BASE', 'STRESS'] for item in requests.values())
    assert [item['execution_profile']['account_sessions'] for item in requests.values()] == [252, 504]
    changed = config()
    changed['windows']['504']['symbols'] = ['000001.SZ']
    with pytest.raises(ValueError, match='WINDOW_INVALID'):
        fixed_requests(changed, capabilities())


def test_reference_changes_resource_purpose_and_preserves_raw_input_and_strategy():
    profile = execution_profile(SEGMENTED_PROFILE, 504)
    item = {'factory': 'trusted:factory', 'factory_kwargs': {'strategy_id': 'unchanged', 'payload': {'fixed': True}},
            'loader': 'trusted:loader', 'loader_kwargs': {'path': 'frozen_INPUT.json', 'sha256': 'unchanged'},
            'execution_profile': profile, 'backend_options': {'execution_profile': profile, 'checkpoint_path': 'old.json',
            'window': {'symbols': ['000001.SZ', '300001.SZ', '600000.SH']}, 'initial_cash': 50000, 'costs': 'BASE'}}
    job = {'resources': profile, 'items': {'BASE': item, 'STRESS': {**deepcopy(item), 'factory_kwargs': {'strategy_id': 'unchanged_stress'}}},
           'input_identity': 'same_original_identity', 'benchmark_mode': 'CASH_AND_PRICE_REFERENCE', 'observation_plan': {'fixed': True}}
    before = deepcopy(job)
    result = reference_config(job, root='REFERENCE', objective_id='same_goal', budget_path='same_budget.json')
    assert job == before
    assert result['input_identity'] == job['input_identity']
    assert result['execution_profile']['purpose'] == 'ENGINEERING_CONTINUOUS_REFERENCE'
    for original, copied in zip(job['items'].values(), result['items']):
        assert copied['factory_kwargs'] == original['factory_kwargs']
        assert copied['loader_kwargs'] == original['loader_kwargs']
        assert copied['backend_options']['window'] == original['backend_options']['window']
        assert copied['backend_options']['initial_cash'] == original['backend_options']['initial_cash']
    job['resources'] = execution_profile(SEGMENTED_PROFILE, 252)
    with pytest.raises(ValueError, match='PRIMARY_504_REQUIRED'):
        reference_config(job, root='REFERENCE', objective_id='same_goal', budget_path='same_budget.json')


def test_three_frozen_mechanisms_use_the_actual_public_parser_without_accounts():
    from chanlun_trader.research_factory.strategy_submission_v1 import public_rule_factory
    snapshot = capabilities()
    request = fixed_requests(config(), snapshot)['504']
    variants = mechanism_requests(request, snapshot)
    parsed = [public_rule_factory(item['rule'], item['strategy_id']) for item in variants.values()]
    assert len({item.rule_identity for item in parsed}) == 3
    assert all(item['costs'] == ['BASE', 'STRESS'] and item['account_scope'] == 'DATA_QUALIFIED'
               and 'symbols' not in item for item in variants.values())


def test_funnel_acceptance_uses_account_days_and_original_native_quantities():
    result = {'execution_description': {'window': {'calendar': [1, 2, 3], 'symbols': ['000001.SZ', '300001.SZ']}},
              'artifacts': {'days': [{'date': 2}, {'date': 3}]}, 'fills': [{'fill_id': 'f'}],
              'final_account_checkpoint': {'economic': {'orders': {'o': {'order_id': 'o'}}}}}
    funnel = {'counts': {'scan_rows': 4, 'opportunity_signals': 3, 'orders': 1, 'fills': 1},
              'layer_counts_by_side': {'BUY': {'orders': 1, 'fills': 1}, 'SELL': {'orders': 0, 'fills': 0}},
              'opportunity_dispositions': {'REALIZED': 1, 'POSITION_LIMIT': 1, 'END_OF_OBSERVATION_NO_NEXT_SESSION': 1}}
    assert check_funnel(result, funnel)['passed']
    for key, value in [('scan_rows', 2), ('opportunity_signals', 2), ('orders', 0), ('fills', 0)]:
        changed = deepcopy(funnel)
        changed['counts'][key] = value
        with pytest.raises(ValueError, match='FUNNEL_QUANTITY_CONFLICT'):
            check_funnel(result, changed)
