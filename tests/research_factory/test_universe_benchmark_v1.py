from copy import deepcopy

import pytest
import pandas as pd

from chanlun_trader.research_factory.universe_benchmark_v1 import universe_price_reference
from universe_test_fixture_v1 import fixture


def test_theoretical_basket_keeps_share_and_cash_entitlements_once():
    from test_universe_evidence_v1 import share_case
    bundle, window, _, _ = share_case(cash=True)
    value = universe_price_reference(bundle, window, initial_cash=50000)
    assert value['status'] == 'AVAILABLE_NONINVESTABLE'
    # 12元原股变成1.3份新股和0.5元现金，末日总财富仍为12元。
    assert value['metrics']['net_return'] == pytest.approx(0.)
    assert value['daily'][-1]['relative_value'] == pytest.approx(1.)
    assert not value['investable'] and not value['account_reconciled']


def test_full_basket_is_not_misrepresented_as_investable_account():
    window, bundle = fixture(prices=[12., 13., 13.])
    value = universe_price_reference(bundle, window, initial_cash=1000)
    assert value['status'] == 'AVAILABLE_NONINVESTABLE'
    assert len(value['initial_members']) == 3
    assert value['target_count'] == 3
    assert not value['whole_pool_lot_allocation_feasible']
    assert not value['investable'] and not value['account_reconciled']
    assert value['metrics']['net_return'] == pytest.approx(13 / 12 - 1)


def test_missing_component_cannot_be_removed_and_reweighted():
    window, bundle = fixture()
    bundle['daily'] = bundle['daily'].loc[~((bundle['daily'].symbol == '300001.SZ')
                                        & (bundle['daily'].date == window['account_start']))].copy()
    value = universe_price_reference(bundle, window, initial_cash=50000)
    assert value['status'] == 'UNAVAILABLE'
    assert value['metrics'] is None and value['daily'] is None
    assert value['target_count'] == 3
    assert any('MISSING' in reason for reason in value['reasons'])


def test_unknown_corporate_action_data_cannot_be_zero_baseline():
    window, bundle = fixture()
    bundle['corporate_actions_complete'] = False
    value = universe_price_reference(bundle, window, initial_cash=50000)
    assert value['status'] == 'UNAVAILABLE'
    assert value['metrics'] is None


def test_later_ipo_not_added_to_initial_basket():
    window, bundle = fixture()
    symbol, day = '300001.SZ', window['account_start']
    before = deepcopy(bundle['states'].loc[bundle['states'].symbol == symbol].iloc[0].to_dict())
    before.update(valid_to=window['calendar'][59], listed=False, universe_member=False,
                  eligibility_status='INELIGIBLE', suspension_status='NOT_LISTED', listing_date=day)
    original = bundle['states'].loc[bundle['states'].symbol == symbol].copy()
    original.loc[:, 'effective_date'] = day
    original.loc[:, 'listing_date'] = day
    bundle['states'] = pd.concat([bundle['states'].loc[bundle['states'].symbol != symbol],
        pd.DataFrame([before]), original], ignore_index=True)
    bundle['daily'] = bundle['daily'].loc[(bundle['daily'].symbol != symbol) | (bundle['daily'].date >= day)].copy()
    bundle.pop('listing_dates', None)
    bundle.pop('listing_date_sources', None)
    value = universe_price_reference(bundle, window, initial_cash=50000)
    assert value['status'] == 'AVAILABLE_NONINVESTABLE'
    assert symbol not in value['initial_members']
    assert value['new_listings_added'] is False


def test_verified_halt_keeps_old_price_and_weight():
    window, bundle = fixture(prices=[12., 12.5, 12.5])
    day = window['calendar'][62]
    symbol = '300001.SZ'
    original = deepcopy(bundle['states'].loc[bundle['states'].symbol == symbol].iloc[0].to_dict())
    before, halt, after = (deepcopy(original) for _ in range(3))
    before['valid_to'] = window['calendar'][61]
    halt.update(effective_date=day, valid_to=day, suspension_status='SUSPENDED')
    after['effective_date'] = window['calendar'][63]
    bundle['states'] = pd.concat([bundle['states'].loc[bundle['states'].symbol != symbol],
        pd.DataFrame([before, halt, after])], ignore_index=True)
    bundle['daily'] = bundle['daily'].loc[~((bundle['daily'].symbol == symbol) & (bundle['daily'].date == day))].copy()
    value = universe_price_reference(bundle, window, initial_cash=50000)
    assert value['status'] == 'AVAILABLE_NONINVESTABLE'
    assert {'date': day, 'symbol': symbol, 'status': 'STALE_VERIFIED_SUSPENSION'} in value['stale_valuations']


def test_dividend_added_on_payment_and_not_reinvested():
    window, bundle = fixture()
    days, symbol = window['calendar'], '300001.SZ'
    event = {'event_id': 'DIV', 'symbol': symbol, 'event_type': 'CASH_DIVIDEND',
        'record_date': days[61], 'effective_date': days[62], 'payment_date': days[64],
        'source': 'synthetic', 'source_published_at': '2022-01-03', 'units': 'CNY_PER_SHARE',
        'terms': {'cash_per_share': .6, 'tax_rule': {'kind': 'DEFERRED_INDIVIDUAL_2015_101',
            'source': 'https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html'}}}
    bundle['events'] = [event]
    later = (bundle['daily'].symbol == symbol) & (bundle['daily'].date >= days[62])
    bundle['daily'].loc[later, ['open', 'high', 'low', 'close', 'prev_close']] -= .6
    value = universe_price_reference(bundle, window, initial_cash=50000)
    assert value['status'] == 'AVAILABLE_NONINVESTABLE'
    rows = {row['date']: row for row in value['daily']}
    assert rows[days[63]]['net_return'] == pytest.approx(-.6 / 12 / 3)
    assert rows[days[64]]['net_return'] == pytest.approx(0)


@pytest.mark.parametrize('payment', [20220402, 20220501])
def test_non_session_payment_is_counted_once_or_remains_unpaid_at_window_end(payment):
    window, bundle = fixture()
    days, symbol = window['calendar'], '300001.SZ'
    bundle['events'] = [{'event_id': 'WEEKEND_DIV', 'symbol': symbol, 'event_type': 'CASH_DIVIDEND',
        'record_date': 20220329, 'effective_date': 20220330, 'payment_date': payment,
        'source': 'synthetic', 'source_published_at': '2022-01-03', 'units': 'CNY_PER_SHARE',
        'terms': {'cash_per_share': .6, 'tax_rule': {'kind': 'DEFERRED_INDIVIDUAL_2015_101',
            'source': 'https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html'}}}]
    later = (bundle['daily'].symbol == symbol) & (bundle['daily'].date >= 20220330)
    bundle['daily'].loc[later, ['open', 'high', 'low', 'close', 'prev_close']] -= .6
    value = universe_price_reference(bundle, window, initial_cash=50000)
    assert value['status'] == 'AVAILABLE_NONINVESTABLE'
    rows = {row['date']: row for row in value['daily']}
    assert rows[20220401]['net_return'] == pytest.approx(-.6 / 12 / 3)
    for day in days:
        if day >= 20220404:
            expected = 0 if payment < days[-1] else -.6 / 12 / 3
            assert rows[day]['net_return'] == pytest.approx(expected)
