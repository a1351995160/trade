from copy import deepcopy

import pandas as pd
import pytest

from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3
from chanlun_trader.research_factory.strategy_interface_v1 import prepare, run
from chanlun_trader.research_factory.universe_account_backend_v1 import UniverseAccountBackendV1
from chanlun_trader.research_factory.universe_account_inputs_v1 import universe_input_identity_v1
from universe_test_fixture_v1 import fixture, proposal


def account_result(bundle=None, window=None, exits=None, batch_size=128, prices=None, max_positions=2):
    if bundle is None:
        window, bundle = fixture(prices=prices)
    rule = ResearchRuleStrategyV3(proposal(exits), strategy_id='universe')
    backend = UniverseAccountBackendV1(window, initial_cash=50000, max_positions=max_positions,
                                       max_symbol_exposure_bps=5000, batch_size=batch_size)
    identity = universe_input_identity_v1(bundle, window)
    receipt = {'strategy_plans': {'universe': prepare(rule, backend)}, 'input_identity': identity,
               'novelty': {'universe': {'allowed': True}}, 'execution_purpose': 'universe', 'execution_consumed': True}
    return run(rule, backend, frame=bundle, actions=bundle['events'], input_identity=identity, active_check=lambda: receipt)


def test_three_boards_compete_for_single_cash_and_batch_order_is_stable():
    window, bundle = fixture()
    first = account_result(bundle, window, batch_size=1)
    reversed_bundle = deepcopy(bundle)
    for key in ('daily', 'states', 'turn'):
        reversed_bundle[key] = reversed_bundle[key].iloc[::-1].reset_index(drop=True)
    second = account_result(reversed_bundle, window, batch_size=999)
    assert first == second
    assert first['reconciliation']['passed']
    assert all(row['cash'] >= 0 for row in first['daily_accounts'])
    assert all(sum(p['quantity'] > 0 for p in row['positions']) <= 2 for row in first['daily_accounts'])
    assert set(first['scan_days'][0]['rows'][i]['symbol'] for i in range(3)) == set(window['symbols'])


@pytest.mark.parametrize('exits,prices,reason', [
    ({'stop_loss_pct': .05}, [12., 12., 11.2, 11.3], 'EXIT_FIXED_COST_STOP'),
    ({'take_profit_pct': .05}, [12., 12., 12.8, 12.7], 'EXIT_FIXED_TAKE_PROFIT'),
    ({'trailing_activate_pct': .05, 'trailing_pct': .03}, [12., 12., 12.8, 12.3, 12.4], 'EXIT_TRAILING_STOP')])
def test_each_risk_exit_uses_cost_and_next_real_open(exits, prices, reason):
    result = account_result(exits=exits, prices=prices)
    traces = result['final_account_checkpoint']['rule_exit_states']['universe']['evaluations']
    hit = next(row for row in traces if reason in row['reasons'])
    sold = [t for t in result['fills'] if t['side'] == 'SELL' and t['lot_id'] == hit['lot_id']]
    assert sold
    assert int(pd.Timestamp(sold[0]['fill_time']).strftime('%Y%m%d')) > hit['date']


def test_too_expensive_for_one_lot_retains_cash():
    window, bundle = fixture()
    # 首个评价期前收也相应更新，保持价格参考递推合法。
    bundle['daily'].loc[:, ['open', 'high', 'low', 'close', 'prev_close']] *= 100
    result = account_result(bundle, window)
    assert not result['fills']
    assert result['daily_accounts'][-1]['cash'] == 50000
    assert result['daily_accounts'][-1]['equity'] == 50000


@pytest.mark.parametrize('symbol', ['000001.SZ', '300001.SZ', '600000.SH'])
@pytest.mark.parametrize('field,value', [('universe_member', False), ('eligibility_status', 'INELIGIBLE')])
def test_overnight_removal_blocks_prior_buy_intent_on_all_boards(symbol, field, value):
    window, bundle = fixture()
    days = window['calendar']
    original = bundle['states'].loc[bundle['states'].symbol == symbol].iloc[0].to_dict()
    before, removed = deepcopy(original), deepcopy(original)
    before['valid_to'] = days[60]
    removed.update(effective_date=days[61], **{field: value})
    bundle['states'] = pd.concat([bundle['states'].loc[bundle['states'].symbol != symbol],
                                 pd.DataFrame([before, removed])], ignore_index=True)
    result = account_result(bundle, window)
    assert result['reconciliation']['passed']
    assert not any(fill['symbol'] == symbol and fill['side'] == 'BUY' for fill in result['fills'])
