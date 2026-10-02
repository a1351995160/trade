from copy import deepcopy

import pandas as pd
import pytest

from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3
from chanlun_trader.research_factory.strategy_interface_v1 import Context
from chanlun_trader.research_factory.universe_account_inputs_v1 import UniverseAccountInputsV1
from chanlun_trader.research_factory.universe_signal_scan_v1 import UniverseSignalScanV1, decision_from_conditions
from universe_test_fixture_v1 import fixture, proposal


def halted_ex_date_case():
    """合成除息发生在停牌期间，保留真实缺 bar，不能借夹具补造行情。"""
    window, bundle = fixture()
    symbol, days, cash = '000001.SZ', window['calendar'], .01
    state = bundle['states'].loc[bundle['states'].symbol.eq(symbol)].iloc[0].to_dict()
    bundle['states'] = pd.concat([bundle['states'].loc[~bundle['states'].symbol.eq(symbol)],
        pd.DataFrame([{**state, 'valid_to': days[63]},
            {**state, 'effective_date': days[64], 'valid_to': days[66], 'suspension_status': 'SUSPENDED'},
            {**state, 'effective_date': days[67]}])], ignore_index=True)
    own = bundle['daily'].symbol.eq(symbol)
    bundle['daily'] = bundle['daily'].loc[~(own & bundle['daily'].date.isin(days[64:67]))].copy()
    resumed = bundle['daily'].symbol.eq(symbol) & bundle['daily'].date.ge(days[67])
    bundle['daily'].loc[resumed, ['open', 'high', 'low', 'close', 'prev_close']] -= cash
    bundle['events'] = [{'event_id': 'SYNTHETIC_HALTED_CASH', 'symbol': symbol,
        'event_type': 'CASH_DIVIDEND', 'record_date': days[64], 'effective_date': days[65],
        'payment_date': days[68], 'source': 'synthetic', 'source_published_at': str(days[62]),
        'units': 'CNY_PER_SHARE', 'terms': {'cash_per_share': cash,
            'tax_rule': {'kind': 'DEFERRED_INDIVIDUAL_2015_101',
                'source': 'https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html'}}}]
    return window, bundle


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


def test_only_opted_in_scan_keeps_halted_ex_date_unknown_without_computing_its_features(monkeypatch):
    window, bundle = halted_ex_date_case()
    inputs = UniverseAccountInputsV1(bundle, window, stage='ACCOUNT')
    rule = ResearchRuleStrategyV3(proposal(), strategy_id='HALTED_EX_DATE')
    with pytest.raises(ValueError, match='^CAUSAL_PRICE_EX_DATE_MISSING$'):
        UniverseSignalScanV1(rule, inputs)
    original, calculated = rule.build_feature_matrix, []
    def record(bars, *args, **kwargs):
        calculated.append(set(bars.symbol))
        return original(bars, *args, **kwargs)
    monkeypatch.setattr(rule, 'build_feature_matrix', record)
    before = inputs.input_identity
    scanner = UniverseSignalScanV1(rule, inputs, allow_data_gaps=True)
    assert calculated == [{'300001.SZ'}, {'600000.SH'}]
    assert set(scanner.conditions) == set(window['symbols'])
    failed = scanner.at('000001.SZ', window['account_start'])
    assert failed == {'buy': None, 'sell': None, 'market_filter': None,
        'ready': False, 'reason': 'CAUSAL_PRICE_EX_DATE_MISSING'}
    assert scanner.preparation[0] == {'symbol': '000001.SZ', 'status': 'UNKNOWN',
        'reason': 'CAUSAL_PRICE_EX_DATE_MISSING'}
    assert scanner.at('300001.SZ', window['account_start'])['ready'] is True
    assert inputs.bar('000001.SZ', window['calendar'][65]) is None
    assert inputs.bundle['events'] == bundle['events'] and inputs.input_identity == before
    inputs.assert_unchanged()


@pytest.mark.parametrize('error', [ValueError('CAUSAL_PRICE_RECORD_DATE_MISMATCH'),
    ValueError('CAUSAL_PRICE_EX_DATE_MISSING:unexpected'), RuntimeError('other failure')])
def test_opted_in_scan_still_raises_all_other_price_preparation_failures(monkeypatch, error):
    from chanlun_trader.research_factory import universe_signal_scan_v1 as signal_scan
    window, bundle = fixture()
    inputs = UniverseAccountInputsV1(bundle, window, stage='SCAN')
    rule = ResearchRuleStrategyV3(proposal(), strategy_id='STRICT_OTHER_PRICE_FAILURES')
    def fail(*args):
        raise error
    monkeypatch.setattr(signal_scan, 'causal_hfq_bars', fail)
    with pytest.raises(type(error), match=str(error)):
        UniverseSignalScanV1(rule, inputs, allow_data_gaps=True)
