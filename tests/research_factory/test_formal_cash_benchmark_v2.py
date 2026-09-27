"""原买持公共账户上的现金分红保存结果核验；全部行情是合成夹具。"""
from copy import deepcopy

import pandas as pd
import pytest

from chanlun_trader.research_factory.formal_account_backend_v1 import (
    prepare_formal_account, run_formal_account, validate_formal_result, window_input_identity,
)
from chanlun_trader.research_factory.formal_cash_benchmark_v2 import validate_cash_benchmark
from test_formal_account_backend_v1 import fixture, snapshots


@pytest.fixture(scope='module')
def cash_account():
    window, bundle = fixture()
    events = []
    for index in (35, 90):
        record, effective = window['calendar'][index-1:index+1]
        symbol = window['symbols'][0]
        events.append({'event_id': 'CASH_' + str(index), 'symbol': symbol, 'event_type': 'CASH_DIVIDEND',
            'record_date': record, 'effective_date': effective, 'payment_date': effective,
            'source_published_at': str(pd.Timestamp(str(record)).date()) + 'T08:00:00+08:00',
            'source': 'SYNTHETIC_ANNOUNCEMENT', 'units': 'CNY_PER_SHARE',
            'terms': {'cash_per_share': .1, 'tax_rule': {'kind': 'DEFERRED_INDIVIDUAL_2015_101',
                                                       'source': 'SYNTHETIC_TAX'}}})
        bundle['daily'].loc[(bundle['daily'].symbol == symbol) & (bundle['daily'].date == effective), 'prev_close'] -= .1
    bundle['events'] = events
    snapshots(window, bundle)
    plan = prepare_formal_account(None, strategy_id='BENCHMARK_BASE', window=window)
    identity = window_input_identity(bundle, window)
    receipt = {'strategy_plans': {'BENCHMARK_BASE': plan}, 'input_identity': identity,
        'novelty': {'BENCHMARK_BASE': {'allowed': True}}, 'execution_purpose': 'BENCHMARK_BASE',
        'execution_consumed': True}
    result = run_formal_account(None, strategy_id='BENCHMARK_BASE', bundle=bundle, window=window,
                               input_identity=identity, active_check=lambda: receipt)
    return window, bundle, receipt, result


def test_supported_dividend_replays_original_buy_hold_and_keeps_v1_unchanged(cash_account):
    window, bundle, receipt, result = cash_account
    with pytest.raises(ValueError, match='CORPORATE_ACTIONS_UNSUPPORTED'):
        validate_formal_result(result, bundle=bundle, window=window)
    before = deepcopy(receipt)
    checked = validate_cash_benchmark(result, bundle=bundle, window=window, active_check=lambda: receipt)
    assert checked['status'] == 'VERIFIED' and not checked['strategy_qualified']
    assert receipt == before
    assert len(result['fills']) == 2 and all(trade['side'] == 'BUY' for trade in result['fills'])
    assert result['chain']['corporate_account']['dividend_income'] > 0
    assert 'CASH_35' not in result['chain']['corporate_account']['independent_entitlements']


def test_saved_dividend_income_tamper_rejected(cash_account):
    window, bundle, receipt, result = cash_account
    changed = deepcopy(result)
    changed['chain']['corporate_account']['dividend_income'] += 1
    with pytest.raises(ValueError, match='FROZEN_TRAJECTORY_CONFLICT'):
        validate_cash_benchmark(changed, bundle=bundle, window=window, active_check=lambda: receipt)


def test_existing_consumed_authorization_cannot_be_omitted(cash_account):
    window, bundle, _, result = cash_account
    with pytest.raises(PermissionError):
        validate_cash_benchmark(result, bundle=bundle, window=window, active_check=lambda: {})


def test_non_buy_hold_payload_is_not_accepted_as_benchmark(cash_account):
    window, bundle, receipt, result = cash_account
    changed = deepcopy(result)
    changed['strategy_plan']['strategy']['parameters']['candidate_payload'] = {'indicators': ['MACD']}
    with pytest.raises(ValueError, match='BUY_AND_HOLD_ONLY'):
        validate_cash_benchmark(changed, bundle=bundle, window=window, active_check=lambda: receipt)
