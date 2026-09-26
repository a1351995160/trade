"""初筛边界与核账证据验证；合成账户不代表策略有效。"""
from copy import deepcopy

import pytest

from chanlun_trader.research_factory import research_screening_v1 as screening
from chanlun_trader.research_factory.formal_account_backend_v1 import BASE_COSTS, STRESS_COSTS


def account(rate=.0001, *, stress=False, benchmark=False):
    equity, rows, returns, peak, drawdown = 1_000_000., [], [], 1_000_000., 0.
    costs = STRESS_COSTS if stress else BASE_COSTS
    for day in range(504):
        previous = equity
        equity *= 1 + (rate[day] if isinstance(rate, list) else rate)
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1)
        rows.append({'date': day, 'equity': equity, 'cash': equity, 'market_value': 0., 'max_abs_ledger_delta': 0.})
        returns.append({'date': day, 'net_return': equity / previous - 1})
    result = {'status': 'RECONCILED_DIAGNOSTIC', 'input_identity': 'SYNTHETIC_INPUT',
        'rule_identity': 'SYNTHETIC_RULE', 'decisions': {},
        'chain': {'status': 'RECONCILED_DIAGNOSTIC', 'issues': [], 'independent_account_checks': rows,
                  'trades': [], 'n_trades': 0, 'n_account_days': 504, 'account_dates': [0, 503],
                  'execution_assumptions': {'initial_cash': 1_000_000., 'fee_commission_rate': costs['commission_rate'],
                     'min_commission': costs['min_commission'], 'sell_stamp_tax_rate': costs['stamp_tax_rate'],
                     'slippage_fraction': costs['slippage_bps']}},
        'daily_returns': returns,
        'metrics': {'net_return': equity / 1_000_000 - 1, 'max_drawdown': drawdown,
                    'total_fees': 0., 'trade_count': 0},
        'strategy_plan': {'backend': {'costs': STRESS_COSTS if stress else BASE_COSTS},
                          'strategy': {'parameters': {'candidate_payload': None if benchmark else {'synthetic': True},
                                                      'rule_identity': 'SYNTHETIC_RULE'}}}}
    return result


def test_positive_screen_is_only_exploratory_and_feedback_contains_no_numbers():
    result = screening.screen(account(), account(.00005, stress=True), account(0, benchmark=True))
    assert result['passed'] and not result['strategy_qualified']
    feedback = screening.qualitative_feedback(result)
    assert [e['reason_code'] for e in feedback['entries']] == ['COST_SENSITIVITY', 'SCREEN_PASSED']
    assert not any(isinstance(v, (int, float)) for e in feedback['entries'] for v in e.values())
    assert 'base' not in feedback and 'sum_daily_excess_each_half' not in feedback


@pytest.mark.parametrize('rate,expected', [(0., 'NONPOSITIVE_NET_RETURN'), (-.001, 'DRAWDOWN_LIMIT_EXCEEDED')])
def test_economic_failures(rate, expected):
    report = screening.screen(account(rate), account(rate, stress=True), account(0, benchmark=True))
    assert not report['passed'] and expected in report['failure_codes']
    assert 'COST_STRESS_FAILED' in report['failure_codes']
    assert 'SUBPERIOD_EXCESS_NOT_POSITIVE' in report['failure_codes']


@pytest.mark.parametrize('mutation,code', [
    (lambda b: b['daily_returns'][1].update(net_return=.5), 'DAILY_RETURNS_CONFLICT'),
    (lambda b: b['metrics'].update(net_return=float('nan')), 'METRICS_CONFLICT'),
    (lambda b: b['chain'].update(issues=['MISMATCH']), 'ACCOUNT_EVIDENCE_INVALID'),
    (lambda b: b.update(input_identity='OTHER'), 'INPUT_CONFLICT'),
    (lambda b: b.update(decisions={'changed': True}), 'STRESS_RULE_CHANGED'),
    (lambda b: b['strategy_plan']['backend'].update(costs={}), 'COST_MODEL_CHANGED'),
])
def test_corrupt_or_incomparable_account_rejected(mutation, code):
    base = deepcopy(account())
    mutation(base)
    with pytest.raises(ValueError, match=code):
        screening.screen(base, account(stress=True), account(0, benchmark=True))


def test_equal_to_benchmark_is_not_positive_excess():
    result = screening.screen(account(), account(stress=True), account(benchmark=True))
    assert result['failure_codes'] == ['SUBPERIOD_EXCESS_NOT_POSITIVE']


def test_positive_total_does_not_hide_losing_second_half():
    rates = [.001] * 252 + [-.0001] * 252
    result = screening.screen(account(rates), account(rates, stress=True), account(0., benchmark=True))
    assert result['base']['net_return'] > 0
    assert result['failure_codes'] == ['SUBPERIOD_EXCESS_NOT_POSITIVE']


def test_exact_drawdown_boundary_is_not_exceeded():
    rates = [0.] * 503 + [-.25]
    result = screening.screen(account(rates), account(rates, stress=True), account(0., benchmark=True))
    assert 'DRAWDOWN_LIMIT_EXCEEDED' not in result['failure_codes']


def test_declared_costs_do_not_override_executed_costs():
    result = account()
    result['chain']['execution_assumptions']['min_commission'] = 0
    with pytest.raises(ValueError, match='EXECUTED_COST_CONFLICT'):
        screening.screen(result, account(stress=True), account(0., benchmark=True))
