"""停牌除息条款、分段恢复及另一证券开盘分配的合成证据。"""
import json
import math

import pandas as pd
import pytest

from chanlun_trader.research_factory.formal_account_backend_v1 import BASE_COSTS
from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3
from chanlun_trader.research_factory.universe_account_backend_v2 import UniverseAccountBackendV2, SegmentBoundary
from chanlun_trader.research_factory.universe_account_inputs_v1 import universe_input_identity_v1
from chanlun_trader.research_factory.universe_evidence_v1 import reconstruct_universe_account
from chanlun_trader.research_factory.universe_execution_artifacts_v1 import ArtifactSequence, hydrated_result
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile
from universe_test_fixture_v1 import fixture, proposal


SYMBOL = '000001.SZ'
OTHER = '600000.SH'
TAX_SOURCE = 'https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html'


def _cash_event(days, key, *, record, effective, payment, cash, price_cash=None):
    event = {'event_id': key, 'symbol': SYMBOL, 'event_type': 'CASH_DIVIDEND',
        'record_date': days[record], 'effective_date': days[effective], 'payment_date': days[payment],
        'source_published_at': str(days[62]), 'source': 'synthetic', 'units': 'CNY_PER_SHARE',
        'terms': {'cash_per_share': cash,
            'tax_rule': {'kind': 'DEFERRED_INDIVIDUAL_2015_101', 'source': TAX_SOURCE}}}
    if price_cash is not None:
        event['price_terms'] = {'cash_per_share': price_cash, 'source': 'synthetic',
            'evidence_quote': '合成夹具明确区分账户获息现金与除息参考价现金；不授予真实资格。'}
    return event


def _suspended_case(*, days_count=72, resume=69, symbols=None, price_cash=.03):
    window, bundle = fixture(symbols=symbols or [SYMBOL], days_count=days_count)
    days = window['calendar']
    original = bundle['states'].loc[bundle['states'].symbol.eq(SYMBOL)].iloc[0].to_dict()
    ordinary = bundle['states'].loc[~bundle['states'].symbol.eq(SYMBOL)].to_dict('records')
    bundle['states'] = pd.DataFrame([*ordinary, {**original, 'valid_to': days[63]},
        {**original, 'effective_date': days[64], 'valid_to': days[resume-1],
            'suspension_status': 'SUSPENDED'}, {**original, 'effective_date': days[resume]}])
    bundle['daily'] = bundle['daily'].loc[~(bundle['daily'].symbol.eq(SYMBOL)
        & bundle['daily'].date.isin(days[64:resume]))].copy()
    after = bundle['daily'].symbol.eq(SYMBOL) & bundle['daily'].date.ge(days[resume])
    bundle['daily'].loc[after, ['open', 'high', 'low', 'close', 'prev_close']] -= price_cash
    bundle['daily'].loc[after, 'amount'] = bundle['daily'].loc[after, 'close'] * bundle['daily'].loc[after, 'volume']
    rule = proposal()
    rule['max_hold_sessions'] = 20
    return window, bundle, rule


def _run(root, window, bundle, rule, *, stop=None):
    root.mkdir(parents=True, exist_ok=True)
    strategy = ResearchRuleStrategyV3(rule, strategy_id='paused_allocation')
    first = window['calendar'].index(window['account_start'])
    backend = UniverseAccountBackendV2(window,
        execution_profile=execution_profile(SEGMENTED_PROFILE, len(window['calendar'])-first),
        initial_cash=50000, max_positions=min(2, len(window['symbols'])),
        max_symbol_exposure_bps=5000, checkpoint_path=root/'EXECUTION.json')
    return backend.run(strategy, bundle, bundle['events'],
        lambda: {'input_identity': universe_input_identity_v1(bundle, window)}, stop_after_date=stop)


def _audit(window, bundle, rule, result):
    return reconstruct_universe_account(bundle, window, hydrated_result(result), initial_cash=50000,
        costs=BASE_COSTS, strategy_id='paused_allocation', rule=rule)


def _saved(root):
    return json.loads((root/'EXECUTION.json').read_bytes())['ledger']['state']


def test_distinct_price_and_account_cash_keep_both_terms_at_paused_ex_date(tmp_path):
    window, bundle, rule = _suspended_case(resume=67, price_cash=.01)
    days = window['calendar']
    bundle['events'] = [_cash_event(days, 'distinct-cash', record=64, effective=65,
        payment=68, cash=.02, price_cash=.01)]
    root = tmp_path/'job'
    with pytest.raises(SegmentBoundary):
        _run(root, window, bundle, rule, stop=days[65])
    saved = _saved(root)
    quantity = sum(position['quantity'] for position in saved['positions'].values())
    assert quantity > 0
    assert saved['last_price'][SYMBOL] == pytest.approx(11.99)
    assert saved['receivables'] == {'distinct-cash': pytest.approx(quantity*.02)}
    trace = [row for row in saved['action_audit'] if row['phase'] == 'SUSPENDED_CASH_MARK']
    assert len(trace) == 1 and trace[0]['price_cash_per_share'] == .01
    result = _run(root, window, bundle, rule)
    accounts = {row['date']: row for row in hydrated_result(result)['daily_accounts']}
    assert accounts[days[65]]['cash'] == accounts[days[64]]['cash']
    assert accounts[days[65]]['equity'] == pytest.approx(
        accounts[days[64]]['equity'] + quantity*(.02-.01), abs=1e-6)
    assert _audit(window, bundle, rule, result)['daily_accounts'] == list(hydrated_result(result)['daily_accounts'])


def test_two_ex_dates_during_one_halt_survive_two_resumes_without_reapplication(tmp_path):
    window, bundle, rule = _suspended_case()
    days = window['calendar']
    bundle['events'] = [_cash_event(days, 'first-cash', record=64, effective=65, payment=68, cash=.01),
        _cash_event(days, 'second-cash', record=66, effective=67, payment=69, cash=.02)]
    root = tmp_path/'segmented'
    for stop, last_mark, ids in [(65, 11.99, {'first-cash'}),
                                (67, 11.97, {'first-cash', 'second-cash'})]:
        with pytest.raises(SegmentBoundary):
            _run(root, window, bundle, rule, stop=days[stop])
        saved = _saved(root)
        assert saved['last_price'][SYMBOL] == pytest.approx(last_mark)
        assert set(saved['applied']) == ids
        assert {row['event_id'] for row in saved['action_audit']
            if row['phase'] == 'SUSPENDED_CASH_MARK'} == ids
    resumed = _run(root, window, bundle, rule)
    continuous = _run(tmp_path/'continuous', window, bundle, rule)
    assert resumed['final_account_checkpoint'] == continuous['final_account_checkpoint']
    accounts = list(hydrated_result(resumed)['daily_accounts'])
    assert accounts == list(hydrated_result(continuous)['daily_accounts'])
    by_day = {row['date']: row for row in accounts}
    for day in days[64:70]:
        assert by_day[day]['equity'] == pytest.approx(by_day[days[63]]['equity'], abs=1e-6)
    traces = resumed['final_account_checkpoint']['economic']['action_audit']
    assert sum(row['phase'] == 'SUSPENDED_CASH_MARK' for row in traces) == 2
    assert sum(row['phase'] == 'PAYMENT' for row in traces) == 2
    assert _audit(window, bundle, rule, resumed)['daily_accounts'] == accounts


def test_other_security_opening_allocation_uses_corrected_suspended_equity(tmp_path):
    window, bundle, rule = _suspended_case(symbols=[SYMBOL, OTHER], resume=67, price_cash=1.)
    days = window['calendar']
    bundle['events'] = [_cash_event(days, 'allocation-cash', record=64, effective=65,
        payment=68, cash=.5, price_cash=1.)]
    # 第二只证券首次满足买入规则在登记日收盘，实际下单在除息日开盘。
    before_signal = bundle['daily'].symbol.eq(OTHER) & bundle['daily'].date.lt(days[64])
    bundle['daily'].loc[before_signal, ['open', 'high', 'low', 'close', 'prev_close']] = 9.
    bundle['daily'].loc[bundle['daily'].symbol.eq(OTHER) & bundle['daily'].date.eq(days[64]), 'prev_close'] = 9.
    other_rows = bundle['daily'].symbol.eq(OTHER)
    bundle['daily'].loc[other_rows, 'amount'] = bundle['daily'].loc[other_rows, 'close'] * bundle['daily'].loc[other_rows, 'volume']
    rule['target_weight'] = .31
    result = _run(tmp_path/'job', window, bundle, rule)
    accounts = {row['date']: row for row in hydrated_result(result)['daily_accounts']}
    held = next(row['quantity'] for row in accounts[days[64]]['positions'] if row['symbol'] == SYMBOL)
    assert held > 0
    opening_equity = accounts[days[64]]['equity'] + held*(.5-1.)
    allocation = next(row for packet in ArtifactSequence(result['artifacts'], 'allocation_records')
        for row in packet if row['symbol'] == OTHER and row['execution_session'] == days[65])
    basis = allocation['basis']
    assert basis['opening_equity'] == pytest.approx(opening_equity, abs=1e-6)
    assert basis['equity'] == pytest.approx(opening_equity, abs=1e-6)
    expected = math.floor(opening_equity*.31/basis['estimated_price']/100)*100
    stale_quantity = math.floor((opening_equity+held)*.31/basis['estimated_price']/100)*100
    assert stale_quantity > expected
    assert allocation['requested_quantity'] == allocation['allocated_quantity'] == expected
    bought = next(fill for fill in result['fills'] if fill['symbol'] == OTHER and fill['side'] == 'BUY')
    assert bought['quantity'] == expected
    assert int(pd.Timestamp(bought['fill_time']).strftime('%Y%m%d')) == days[65]
    assert _audit(window, bundle, rule, result)['daily_accounts'] == list(hydrated_result(result)['daily_accounts'])
