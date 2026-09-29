"""基准必须使用全池真实资金账户，不能用收益序列或两股替代。"""
from copy import deepcopy

import pandas as pd
import pytest

from chanlun_trader.research_factory.research_benchmark_v1 import (
    CAPABILITY, FullPoolBuyHoldBackendV1, FullPoolBuyHoldStrategyV1, benchmark_payload,
)
from chanlun_trader.research_factory.rule_account_backend_v2 import rule_input_identity
from chanlun_trader.research_factory.strategy_interface_v1 import Context, prepare, run
from test_historical_process_v1 import historical_fixture


def execute(window, bundle, cash=50_000):
    strategy = FullPoolBuyHoldStrategyV1(benchmark_payload(window['symbols']), strategy_id='benchmark')
    backend = FullPoolBuyHoldBackendV1(window, initial_cash=cash)
    plan = prepare(strategy, backend)
    identity = rule_input_identity(bundle, window)
    receipt = {'strategy_plans': {'benchmark': plan}, 'input_identity': identity,
               'novelty': {'benchmark': {'allowed': True}}, 'execution_purpose': 'benchmark', 'execution_consumed': True}
    return run(strategy, backend, frame=bundle, actions=bundle['events'], input_identity=identity, active_check=lambda: receipt)


def test_full_twelve_stock_pool_uses_one_cash_account_not_first_two():
    window, bundle = historical_fixture()
    symbols = [f'{i:06d}.SZ' for i in range(1, 13)]
    for name in ('daily', 'turn', 'states'):
        sample = bundle[name].loc[bundle[name].symbol == '000001.SZ']
        bundle[name] = pd.concat([sample.assign(symbol=s) for s in symbols], ignore_index=True)
    window['symbols'] = symbols
    result = execute(window, bundle)
    assert {trade['symbol'] for trade in result['fills']} == set(symbols)
    assert all(trade['side'] == 'BUY' for trade in result['fills'])
    assert result['benchmark']['coverage'] == 1
    assert result['benchmark']['max_positions'] == 12
    assert result['benchmark']['initial_cash'] == 50_000
    assert result['reconciliation']['passed']
    assert all(row['cash'] >= 0 for row in result['daily_accounts'])
    assert len({trade['fill_time'] for trade in result['fills']}) == 1


def test_insufficient_cash_remains_cash_without_retry_or_fake_coverage():
    window, bundle = historical_fixture()
    result = execute(window, bundle, cash=100)
    assert result['fills'] == []
    assert result['benchmark']['coverage'] == 0
    assert result['benchmark']['unfilled_symbols'] == sorted(window['symbols'])
    assert result['benchmark']['final_cash'] == 100
    assert not result['benchmark']['retry_unfilled']


def test_no_forced_exit_after_252_and_state_resumes_without_new_buy():
    strategy = FullPoolBuyHoldStrategyV1(benchmark_payload(['000001.SZ']), strategy_id='benchmark')
    calendar = tuple(range(400))
    state = {}
    for index in range(300):
        frame = pd.DataFrame({'close': [10.] * (index + 1)}, index=calendar[:index + 1])
        decision = strategy.on_close(Context(frame, calendar, index, {'quantity': 0 if index == 0 else 100}, state))
        assert (decision.intent is not None) == (index == 0)
        state = decision.state
    restored = FullPoolBuyHoldStrategyV1(deepcopy(strategy.payload), strategy_id='benchmark')
    frame = pd.DataFrame({'close': [10.] * 301}, index=calendar[:301])
    assert restored.on_close(Context(frame, calendar, 300, {'quantity': 100}, deepcopy(state))).intent is None


def test_pool_mismatch_and_tampered_state_fail_closed():
    window, _ = historical_fixture()
    rule = FullPoolBuyHoldStrategyV1(benchmark_payload(['000001.SZ']), strategy_id='benchmark')
    with pytest.raises(ValueError, match='POOL_CONFLICT'):
        FullPoolBuyHoldBackendV1(window).validate_strategy(rule)
    frame = pd.DataFrame({'close': [10.]}, index=[1])
    with pytest.raises(ValueError, match='CONTINUITY'):
        rule.on_close(Context(frame, (1,), 0, {'quantity': 0},
                              {'rule_identity': 'wrong', 'last_session_index': -1, 'attempted': True}))


def test_paper_factory_and_configuration_are_explicit():
    from chanlun_trader.research_factory.forward_paper_engine_v1 import paper_strategy
    rule = paper_strategy(benchmark_payload(['000001.SZ']), strategy_id='benchmark')
    assert type(rule) is FullPoolBuyHoldStrategyV1
    assert rule.requirements.capabilities[0] == CAPABILITY
    assert rule.parameters['exit_policy'] == 'HOLD_TO_WINDOW_END_MARK_TO_MARKET'
    rule.parameters['retry_unfilled'] = True
    with pytest.raises(ValueError, match='CHANGED_AFTER_FREEZE'):
        rule.validate()
