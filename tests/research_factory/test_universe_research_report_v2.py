"""实际账户、信号分母、独立区段、尾部删失和公司行动权益口径。"""
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import pandas as pd
import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.universe_research_report_v2 import (
    _Observations, _account_episodes, build_research_reports, default_observation_plan,
)
from chanlun_trader.research_factory.universe_account_inputs_v1 import UniverseAccountInputsV1
from test_universe_account_backend_v1 import account_result
from universe_test_fixture_v1 import fixture


def prepared():
    window, bundle = fixture(days_count=100)
    return UniverseAccountInputsV1(bundle, window, stage='ACCOUNT', required_fields=('close',), warmup_bars=1)


def result_for(inputs, opportunities=(), unknown=(), score_unknown=False):
    days = inputs.window['calendar']
    account = [day for day in days if day >= inputs.window['account_start']]
    scans = []
    for day in account:
        rows = []
        for symbol in inputs.window['symbols']:
            hit = (symbol, day) in opportunities
            ready = (symbol, day) not in unknown
            rows.append({'symbol': symbol, 'side': 'HOLD', 'reason': 'NO_PERMITTED_QUANTITY',
                'conditions': {'buy': hit if ready else None, 'sell': False, 'market_filter': True,
                    'condition_ready': ready, 'ready': ready, 'score': None if score_unknown else .2,
                    'score_ready': not score_unknown},
                'qualification': {'status': 'WARMUP_INSUFFICIENT' if score_unknown else 'TRADING',
                    'signal_ready': not score_unknown, 'state_known': True, 'bar_present': True, 'gap_reasons': []}})
        scans.append({'date': day, 'rows': rows, 'target': len(rows), 'processed': len(rows)})
    return {'input_identity': inputs.input_identity, 'account_policy': {'initial_cash': 50000.},
        'daily_accounts': [{'date': day, 'cash': 50000., 'equity': 50000., 'positions': []} for day in account],
        'fills': [], 'scan_days': scans, 'final_account_checkpoint': {'economic': {'positions': {}, 'lots': {}}},
        'reconciliation': {'passed': True}}


def test_public_account_result_has_two_different_reports_and_no_invented_qualification():
    window, bundle = fixture(days_count=100)
    inputs = UniverseAccountInputsV1(bundle, window, stage='ACCOUNT', required_fields=('close',), warmup_bars=1)
    actual = account_result(bundle, window)
    report = build_research_reports(actual, inputs, default_observation_plan(window))
    assert report['account']['net_return'] == actual['metrics']['net_return']
    assert report['account']['reconciliation']['passed']
    assert report['account']['closed_episodes'] > 0
    assert report['signal']['opportunity_security_days'] > len(actual['fills'])
    assert report['signal']['statistics']['SECURITY_DAY']['5']['observed'] > 0
    assert not report['strategy_qualified'] and not report['formal_method_applicable'] and not report['paper_qualified']
    assert report['report_identity'] == stable_hash({key: value for key, value in report.items() if key != 'report_identity'})


def test_score_unknown_and_no_fills_do_not_shrink_signal_denominator():
    inputs = prepared()
    symbol, day = inputs.window['symbols'][0], inputs.window['account_start']
    result = result_for(inputs, {(symbol, day)}, score_unknown=True)
    rows = []
    report = build_research_reports(result, inputs, default_observation_plan(inputs.window), event_sink=rows.append)
    assert report['account']['trade_count'] == 0
    assert report['signal']['opportunity_security_days'] == 1
    assert report['signal']['score_unknown_opportunities'] == 1
    assert report['signal']['event_detail_count'] == 6
    assert len(rows) == 6
    assert all(row['status'] == 'OBSERVED' and row['return'] == 0 for row in rows)
    assert all(row['comparator']['observed'] == 3 for row in rows)


def test_unknown_gap_does_not_split_same_condition_into_fresh_independent_episode():
    inputs = prepared()
    symbol, days = inputs.window['symbols'][0], inputs.window['calendar'][60:]
    result = result_for(inputs, {(symbol, days[1]), (symbol, days[3])}, {(symbol, days[2])})
    report = build_research_reports(result, inputs, default_observation_plan(inputs.window))
    assert report['signal']['opportunity_security_days'] == 2
    assert report['signal']['condition_episode_count'] == 1
    assert report['signal']['boundary_unknown_episodes'] == 1
    assert report['signal']['statistics']['CONDITION_EPISODE']['5']['observed'] == 1


def test_known_false_ends_episode_and_last_session_is_censored_not_zero_return():
    inputs = prepared()
    symbol, days = inputs.window['symbols'][0], inputs.window['calendar'][60:]
    result = result_for(inputs, {(symbol, days[1]), (symbol, days[3]), (symbol, days[-1])})
    report = build_research_reports(result, inputs, default_observation_plan(inputs.window))
    assert report['signal']['condition_episode_count'] == 3
    stat = report['signal']['statistics']['SECURITY_DAY']['5']
    assert stat['observed'] == 2
    assert stat['censored'] == {'END_OF_OBSERVATION_NO_NEXT_SESSION': 1}
    assert stat['wins'] == 0 and stat['win_rate'] == 0


def test_observation_requires_next_exchange_session_and_does_not_skip_halt():
    inputs = prepared()
    symbol, days = inputs.window['symbols'][0], inputs.window['calendar']
    original = inputs.state
    synthetic = SimpleNamespace(window=inputs.window, bundle=inputs.bundle, input_identity=inputs.input_identity,
        state=lambda security, day: {**original(security, day), 'suspension_status': 'SUSPENDED'}
            if security == symbol and day == days[61] else original(security, day))
    observations = _Observations(synthetic)
    label = observations.observe(symbol, 60, 5)
    assert label['status'] == 'CENSORED' and label['reason'] == 'OPEN_SUSPENDED'
    assert label['entry_session'] == days[61]
    assert label['return'] is None


def test_one_share_economic_value_handles_paid_unpaid_cash_and_locked_bonus():
    inputs = prepared()
    symbol, days = inputs.window['symbols'][0], inputs.window['calendar']
    cash = {'event_id': 'cash', 'symbol': symbol, 'event_type': 'CASH_DIVIDEND',
        'record_date': days[62], 'effective_date': days[63], 'payment_date': days[69],
        'source': 'synthetic', 'terms': {'cash_per_share': .8}}
    shares = {'event_id': 'shares', 'symbol': symbol, 'event_type': 'CAPITALIZATION',
        'record_date': days[62], 'effective_date': days[63], 'share_credit_date': days[70], 'tradable_date': days[71],
        'source': 'synthetic', 'terms': {'ratio_numerator': 13, 'ratio_denominator': 10}}
    changed = SimpleNamespace(window=inputs.window, bundle={**inputs.bundle, 'events': [shares, cash]}, state=inputs.state)
    observations = _Observations(changed)
    close = (12. - .8) / 1.3
    value, reason = observations._economic_value(symbol, days[61], days[65], close)
    assert reason is None and value['economic_value'] == pytest.approx(12.)
    assert value['gross_cash_receivable'] == .8 and value['paid_gross_cash'] == 0
    assert value['uncredited_shares'] == pytest.approx(.3) and value['locked_shares'] == pytest.approx(.3)
    after, reason = observations._economic_value(symbol, days[61], days[72], close)
    assert reason is None and after['economic_value'] == pytest.approx(12.)
    assert after['paid_gross_cash'] == .8 and after['gross_cash_receivable'] == 0
    assert after['uncredited_shares'] == 0 and after['locked_shares'] == 0
    # 登记日以后买入不享有这次现金或新增股，不能用后复权补给它权益。
    no_right, _ = observations._economic_value(symbol, days[63], days[72], close)
    assert no_right['share_quantity'] == 1 and no_right['paid_gross_cash'] == 0


def test_partial_fills_same_episode_and_best_three_sorted_before_attribution():
    inputs = prepared()
    result = result_for(inputs)
    days, symbol = inputs.window['calendar'], inputs.window['symbols'][0]
    fills = []
    for number, profit in enumerate((20., -10., 50., 30.)):
        base = 61 + number * 3
        def trade(index, side, quantity, price, suffix):
            return {'trade_id': f'{number}-{suffix}', 'lot_id': f'lot{number}', 'symbol': symbol,
                'fill_time': f'{days[index]} 09:30:00+08:00', 'side': side, 'quantity': quantity,
                'price': price, 'gross_value': quantity * price, 'fee': 0.}
        fills.extend([trade(base, 'BUY', 100, 10., 'buy'), trade(base + 1, 'SELL', 40, 10. + profit / 100, 's1'),
            trade(base + 2, 'SELL', 60, 10. + profit / 100, 's2')])
    result['fills'] = fills
    report = build_research_reports(result, inputs, default_observation_plan(inputs.window))
    account = report['account']
    assert account['closed_episodes'] == 4 and account['closed_episode_win_rate'] == .75
    assert account['without_best_1_closed_episode_profit'] == pytest.approx(40.)
    assert account['without_best_3_closed_episode_profit'] == pytest.approx(-10.)
    assert all(len(episode['trade_ids']) == 3 for episode in account['episodes'])


def test_missing_close_keeps_unknown_label_and_denominator():
    inputs = prepared()
    symbol = inputs.window['symbols'][0]
    observations = _Observations(inputs)
    observations.prices['close'][0, 65] = float('nan')
    row = observations.observe(symbol, 60, 5)
    assert row['status'] == 'UNKNOWN' and row['return'] is None
    assert row['reason'] == 'HORIZON_RAW_CLOSE_MISSING'


def test_real_cash_bonus_ledger_episode_includes_all_share_sales_and_deferred_tax():
    from test_universe_corporate_accounting_v2 import share_event, cash_event, prepared as dividend_prepared, fill, at
    from chanlun_trader.engine.signal import Side
    events = [share_event(), cash_event()]
    ledger, parent = dividend_prepared(events, initial_cash=2000.)
    ledger.on_open(at(4))
    child = next(key for key in ledger.lots if key != parent)
    fill(ledger, Side.SELL, 100, 5, price=9.2 / 1.3, lot_id=parent)
    ledger.on_open(at(8))
    fill(ledger, Side.SELL, 30, 9, price=9.2 / 1.3, lot_id=child)
    economic = {'lots': {key: asdict(lot) for key, lot in ledger.lots.items()},
        'entitlements': ledger.entitlements, 'applied': list(ledger.applied), 'action_audit': ledger.action_audit,
        'bonus_parent_lots': ledger.bonus_parent_lots}
    result = {'fills': [asdict(trade) for trade in ledger.trades], 'final_account_checkpoint': {'economic': economic}}
    episodes = _account_episodes(result, SimpleNamespace(bundle={'events': events}))
    assert len(episodes) == 1 and episodes[0]['status'] == 'CLOSED'
    assert episodes[0]['end_session'] == 20240109
    assert episodes[0]['quantity'] == 0 and episodes[0]['cash_dividend'] == 80.
    assert episodes[0]['dividend_tax'] == 16.
    assert episodes[0]['net_profit'] == pytest.approx(ledger.cash - 2000.)


def test_score_comparison_is_frozen_direction_on_raw_opportunities_not_fills():
    inputs = prepared()
    day = inputs.window['account_start']
    result = result_for(inputs, {(symbol, day) for symbol in inputs.window['symbols']})
    result['strategy_plan'] = {'strategy': {'parameters': {'candidate_payload': {'selection': {'direction': 'DESCENDING'}}}}}
    result['account_policy']['portfolio'] = {'max_positions': 1}
    for row, score in zip(result['scan_days'][0]['rows'], (1., 3., 2.)):
        row['conditions']['score'] = score
    rows = []
    report = build_research_reports(result, inputs, default_observation_plan(inputs.window), event_sink=rows.append)
    assert report['account']['trade_count'] == 0
    assert report['signal']['score_statistics']['TOP_FROZEN_SLOT_COUNT']['5']['observed'] == 1
    assert report['signal']['score_statistics']['OTHER_KNOWN_SCORE']['5']['observed'] == 2
    top = {row['symbol'] for row in rows if row['score_group'] == 'TOP_FROZEN_SLOT_COUNT'}
    assert top == {inputs.window['symbols'][1]}
    assert not report['signal']['score_comparison_policy']['actual_account_ranking_or_fill_claim']


def test_matched_comparator_is_cached_per_entry_and_horizon():
    inputs = prepared()
    observations = _Observations(inputs)
    called, original = [], observations.observe
    def tracked(symbol, index, horizon):
        called.append((symbol, index, horizon))
        return original(symbol, index, horizon)
    observations.observe = tracked
    first = observations.comparator(60, 5)
    assert observations.comparator(60, 5) is first
    assert len(called) == len(inputs.window['symbols'])


@pytest.mark.parametrize('field,value', [('horizons', [1, 3]), ('end', 20250731), ('entry', 'NEXT_AVAILABLE_TRADE')])
def test_plan_changes_require_new_frozen_request(field, value):
    inputs = prepared()
    plan = deepcopy(default_observation_plan(inputs.window))
    plan[field] = value
    with pytest.raises(ValueError, match='PLAN_OR_SCOPE'):
        build_research_reports(result_for(inputs), inputs, plan)
