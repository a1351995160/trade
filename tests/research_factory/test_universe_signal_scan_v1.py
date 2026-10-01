from copy import deepcopy

import pandas as pd

from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3
from chanlun_trader.research_factory.strategy_interface_v1 import Context
from chanlun_trader.research_factory.universe_account_inputs_v1 import UniverseAccountInputsV1
from chanlun_trader.research_factory.universe_signal_scan_v1 import UniverseSignalScanV1, decision_from_conditions
from universe_test_fixture_v1 import fixture, proposal


def test_last_fragment_and_batch_order_keep_all_symbols():
    window, bundle = fixture()
    rule = ResearchRuleStrategyV3(proposal(), strategy_id='scan')
    inputs = UniverseAccountInputsV1(bundle, window, stage='ACCOUNT')
    small = UniverseSignalScanV1(rule, inputs, batch_size=1)
    large = UniverseSignalScanV1(rule, inputs, batch_size=999)
    assert small.identity == large.identity
    assert set(small.conditions) == set(window['symbols'])
    assert all(small.at(symbol, window['account_start'])['buy'] for symbol in window['symbols'])


def test_condition_and_account_state_match_v3_direct_rule():
    window, bundle = fixture(prices=[12, 12, 11, 11, 4, 12])
    rule = ResearchRuleStrategyV3(proposal(), strategy_id='parity')
    inputs = UniverseAccountInputsV1(bundle, window, stage='ACCOUNT')
    scanner = UniverseSignalScanV1(rule, inputs)
    symbol, day = window['symbols'][0], window['calendar'][63]
    raw = bundle['daily'].loc[bundle['daily'].symbol.eq(symbol) & bundle['daily'].date.le(day)].set_index('date')
    features = rule.build_feature_matrix(raw)
    index = len(features) - 1
    account = {'quantity': 100, 'sellable_quantity': 100, 'entry_session_index': 61, 'last_exit_session_index': None}
    direct = rule.on_signal_close(Context(features, tuple(features.index), index, account, {}))
    prepared = decision_from_conditions(rule, scanner.at(symbol, day), account=account, state={}, index=index)
    assert direct.reason == prepared.reason
    assert direct.intent == prepared.intent
    assert direct.state == prepared.state


def test_changing_future_bar_cannot_change_earlier_conditions():
    window, bundle = fixture()
    rule = ResearchRuleStrategyV3(proposal(), strategy_id='causal')
    before = UniverseSignalScanV1(rule, UniverseAccountInputsV1(bundle, window, stage='SCAN'))
    altered = deepcopy(bundle)
    altered['daily'].loc[altered['daily'].date.eq(window['calendar'][-1]), 'close'] = 2.
    altered['daily'].loc[altered['daily'].date.eq(window['calendar'][-1]), 'low'] = 1.
    after = UniverseSignalScanV1(rule, UniverseAccountInputsV1(altered, window, stage='SCAN'))
    for symbol in window['symbols']:
        pd.testing.assert_frame_equal(before.conditions[symbol].iloc[:-1], after.conditions[symbol].iloc[:-1])
