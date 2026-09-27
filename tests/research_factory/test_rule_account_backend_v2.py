from copy import deepcopy

import pandas as pd
import pytest

from chanlun_trader.research_factory.rule_account_backend_v2 import RuleAccountBackendV2, rule_input_identity
from chanlun_trader.research_factory.research_rule_strategy_v2 import ResearchRuleStrategyV2
from chanlun_trader.research_factory.strategy_interface_v1 import prepare, run
from test_historical_process_v1 import historical_fixture
from test_research_rule_strategy_v2 import payload


def execute(window, bundle, costs='BASE', guard_override=None, proposal=None):
    strategy = ResearchRuleStrategyV2(proposal or payload(), strategy_id='rule')
    backend = RuleAccountBackendV2(window, costs)
    plan = prepare(strategy, backend)
    identity = rule_input_identity(bundle, window)
    receipt = {'strategy_plans': {'rule': plan}, 'input_identity': identity,
        'novelty': {'rule': {'allowed': True}}, 'execution_purpose': 'rule', 'execution_consumed': True}
    return run(strategy, backend, frame=bundle, actions=bundle['events'], input_identity=identity,
        active_check=guard_override or (lambda: receipt))


def test_stateful_rules_use_fills_for_hold_exit_and_cooldown():
    window, bundle = historical_fixture()
    result = execute(window, bundle)
    assert result['fills'] and any(t['side'] == 'SELL' for t in result['fills'])
    assert result['strategy_plan']['backend']['initial_decision'] == 'FIRST_ACCOUNT_CLOSE'
    assert all(int(pd.Timestamp(t['fill_time']).strftime('%Y%m%d')) > window['account_start']
               for t in result['fills'])
    assert result['reconciliation'] == {'passed': True, 'days': 30}
    assert result['strategy_qualified'] is False
    assert result['final_account_checkpoint']['rule_states']
    assert result == execute(window, bundle)
    assert execute(window, bundle, 'STRESS')['metrics']['total_fees'] > result['metrics']['total_fees']


def test_three_symbols_share_one_cash_account_and_identity():
    window, bundle = historical_fixture()
    window['symbols'].append('000002.SZ')
    for key in ('daily', 'turn', 'states'):
        other = bundle[key].loc[bundle[key].symbol == '000001.SZ'].copy()
        other['symbol'] = '000002.SZ'
        bundle[key] = pd.concat([bundle[key], other], ignore_index=True)
    result = execute(window, bundle)
    assert {t['symbol'] for t in result['fills']} == set(window['symbols'])
    assert all(row['cash'] >= 0 for row in result['daily_accounts'])
    assert result['final_account_checkpoint']['invariant_errors'] == []


def test_missing_security_state_or_unconsumed_authorization_rejects():
    window, bundle = historical_fixture()
    bad = deepcopy(bundle)
    bad['states'] = bad['states'].iloc[1:]
    with pytest.raises(ValueError, match='COVERAGE'):
        execute(window, bad)
    with pytest.raises(PermissionError):
        execute(window, bundle, guard_override=lambda: {})


@pytest.mark.parametrize('flat', [True, False])
def test_dividend_entitlement_tax_and_empty_account_reconcile(flat):
    window, bundle = historical_fixture()
    record, effective = window['calendar'][89:91]
    symbol = window['symbols'][0]
    bundle['events'] = [{'event_id': 'cash', 'symbol': symbol, 'event_type': 'CASH_DIVIDEND',
        'record_date': record, 'effective_date': effective, 'payment_date': effective,
        'source_published_at': str(pd.Timestamp(str(record)).date())+'T08:00:00+08:00',
        'source': 'SYNTHETIC_ANNOUNCEMENT', 'units': 'CNY_PER_SHARE',
        'terms': {'cash_per_share': .1, 'tax_rule': {'kind': 'DEFERRED_INDIVIDUAL_2015_101',
            'source': 'SYNTHETIC_TAX'}}}]
    mask = (bundle['daily'].symbol == symbol) & (bundle['daily'].date == effective)
    bundle['daily'].loc[mask, 'prev_close'] -= .1
    proposal = payload()
    proposal['buy']['args'][1]['params']['value'] = 1000000 if flat else 0
    proposal['max_hold_sessions'] = 12
    result = execute(window, bundle, proposal=proposal)
    assert result['reconciliation']['passed']
    assert not result['fills'] if flat else result['fills']
    assert result['final_account_checkpoint']['economic']['dividend_tax_withheld'] == 0 if flat else (
        result['final_account_checkpoint']['economic']['dividend_tax_withheld'] > 0)


def test_observed_backend_uses_captured_price_and_time():
    from test_formal_account_backend_v1 import fixture
    from chanlun_trader.research_factory.formal_account_backend_v1 import window_input_identity
    window, bundle = fixture()
    bundle['profile'] = 'SYNTHETIC'
    for opened in bundle['open_snapshots']:
        for state in opened['payload']['states']:
            state['date'] = opened['market_date']
    for close in bundle['close_snapshots']:
        day = close['market_date']
        close['payload']['states'] = [{'symbol': s, 'date': day, 'listed': True,
            'delisted': False, 'is_st': False, 'suspended': False, 'board': 'MAIN'}
            for s in window['symbols']]
        close['payload']['turn'] = bundle['turn'].loc[bundle['turn'].date == day].to_dict('records')
    strategy = ResearchRuleStrategyV2(payload(), strategy_id='observed')
    backend = RuleAccountBackendV2(window, execution_profile='OBSERVED')
    plan = prepare(strategy, backend)
    identity = window_input_identity(bundle, window)
    receipt = {'strategy_plans': {'observed': plan}, 'input_identity': identity,
        'novelty': {'observed': {'allowed': True}}, 'execution_purpose': 'observed', 'execution_consumed': True}
    result = run(strategy, backend, frame=bundle, actions=bundle['events'], input_identity=identity,
                 active_check=lambda: receipt)
    assert result['status'] == 'OBSERVED_ACCOUNT_COMPLETED'
    assert result['fills']
    assert all(int(pd.Timestamp(t['fill_time']).strftime('%Y%m%d')) > window['account_start']
               for t in result['fills'])
    times = {s['received_at'] for s in bundle['open_snapshots']}
    assert all(t['fill_time'] in times for t in result['fills'])
    assert not result['strategy_qualified']
