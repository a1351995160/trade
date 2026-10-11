from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
import pandas as pd

from chanlun_trader.research_factory.universe_benchmark_v1 import (
    _universe_price_reference_from_inputs, universe_price_reference,
)
from chanlun_trader.research_factory.universe_account_inputs_v1 import prepare_universe_account_inputs_v1
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


@pytest.mark.parametrize('corporate_actions', [False, True])
def test_prepared_reference_matches_public_reference_without_new_input_copy(monkeypatch, corporate_actions):
    if corporate_actions:
        from test_universe_evidence_v1 import share_case
        bundle, window, _, _ = share_case(cash=True)
    else:
        window, bundle = fixture(prices=[12., 13., 13.])
    expected = universe_price_reference(bundle, window, initial_cash=50000)
    inputs = prepare_universe_account_inputs_v1(bundle, window)
    daily = inputs.daily
    monkeypatch.setattr('chanlun_trader.research_factory.universe_benchmark_v1.prepare_universe_account_inputs_v1',
                        lambda *args, **kwargs: pytest.fail('不能再次复制已认证的全市场资料'))
    assert _universe_price_reference_from_inputs(inputs, initial_cash=50000) == expected
    assert inputs.daily is daily
    inputs.assert_unchanged()


def test_prepared_reference_obeys_existing_worker_deadline(monkeypatch):
    from chanlun_trader.research_factory.universe_account_backend_v2 import SegmentBoundary
    window, bundle = fixture()
    inputs = prepare_universe_account_inputs_v1(bundle, window)
    monkeypatch.setattr('chanlun_trader.research_factory.universe_report_state_v1.time.monotonic', lambda: 830.)
    with pytest.raises(SegmentBoundary, match='UNIVERSE_PRICE_REFERENCE_COOPERATIVE_DEADLINE'):
        _universe_price_reference_from_inputs(inputs, initial_cash=50000, deadline=830.)


def test_strategy_warmup_does_not_change_price_reference_members():
    window, bundle = fixture(prices=[12., 13., 13.])
    expected = universe_price_reference(bundle, window, initial_cash=50000)
    inputs = prepare_universe_account_inputs_v1(bundle, window, required_fields=['turn'], warmup_bars=100)
    assert not inputs.scan_status(inputs.symbols[0], inputs.calendar[59])['entry_eligible']
    assert _universe_price_reference_from_inputs(inputs, initial_cash=50000) == expected
    assert inputs.warmup_bars == 100 and inputs.required_fields == frozenset({'turn'})


@pytest.mark.parametrize('scenario', ['ordinary', 'bonus_cash', 'capitalization_cash', 'suspension',
                                     'weekend_payment', 'unpaid'])
def test_daily_checkpoint_preserves_every_reference_field(tmp_path, monkeypatch, scenario):
    from chanlun_trader.research_factory.universe_account_backend_v2 import SegmentBoundary
    from chanlun_trader.research_factory.common import canonical_json, stable_hash
    if scenario in ('bonus_cash', 'capitalization_cash'):
        from test_universe_evidence_v1 import share_case
        bundle, window, _, _ = share_case(cash=True, taxable=scenario == 'bonus_cash')
    else:
        window, bundle = fixture(prices=None if scenario in ('weekend_payment', 'unpaid') else [12., 13., 13.])
    days, symbol = window['calendar'], '300001.SZ'
    if scenario == 'suspension':
        original = deepcopy(bundle['states'].loc[bundle['states'].symbol == symbol].iloc[0].to_dict())
        before, halt, after = (deepcopy(original) for _ in range(3))
        before['valid_to'] = days[61]
        halt.update(effective_date=days[62], valid_to=days[62], suspension_status='SUSPENDED')
        after['effective_date'] = days[63]
        bundle['states'] = pd.concat([bundle['states'].loc[bundle['states'].symbol != symbol],
            pd.DataFrame([before, halt, after])], ignore_index=True)
        bundle['daily'] = bundle['daily'].loc[~((bundle['daily'].symbol == symbol)
                                               & (bundle['daily'].date == days[62]))].copy()
    elif scenario in ('weekend_payment', 'unpaid'):
        bundle['events'] = [{'event_id': 'SYNTHETIC_DIVIDEND', 'symbol': symbol,
            'event_type': 'CASH_DIVIDEND', 'record_date': 20220329, 'effective_date': 20220330,
            'payment_date': 20220402 if scenario == 'weekend_payment' else 20220501,
            'source': 'synthetic', 'source_published_at': '2022-01-03', 'units': 'CNY_PER_SHARE',
            'terms': {'cash_per_share': .6, 'tax_rule': {'kind': 'DEFERRED_INDIVIDUAL_2015_101',
                                                      'source': 'synthetic'}}}]
        later = (bundle['daily'].symbol == symbol) & (bundle['daily'].date >= 20220330)
        bundle['daily'].loc[later, ['open', 'high', 'low', 'close', 'prev_close']] -= .6
    inputs = prepare_universe_account_inputs_v1(bundle, window)
    expected = _universe_price_reference_from_inputs(inputs, initial_cash=50000)
    daily_frame = inputs.daily
    timer = SimpleNamespace(now=0.)
    monkeypatch.setattr('chanlun_trader.research_factory.universe_report_state_v1.time.monotonic', lambda: timer.now)
    original_state, visits = inputs.state, []

    def state(symbol, day):
        value = original_state(symbol, day)
        if day >= inputs.window['account_start']:
            visits.append((symbol, day))
            if symbol == inputs.symbols[-1]:
                timer.now += 1.
        return value

    monkeypatch.setattr(inputs, 'state', state)
    path = tmp_path / 'PRICE_REFERENCE_CHECKPOINT.json'
    first = inputs.calendar.index(inputs.window['account_start'])
    count = len(inputs.calendar) - first
    prefix = []
    for _ in range(count + 1):
        timer.now = 0.
        try:
            actual = _universe_price_reference_from_inputs(inputs, initial_cash=50000,
                deadline=.5, report_checkpoint_path=path)
        except SegmentBoundary:
            saved = json.loads(path.read_text(encoding='utf-8'))
            assert saved['state']['daily'][:-1] == prefix
            prefix = saved['state']['daily']
            assert prefix == expected['daily'][:len(prefix)]
            assert saved['state']['cursor'] == len(prefix) and saved['complete'] is False
        else:
            assert actual == expected
            assert canonical_json(actual) == canonical_json(expected)
            assert stable_hash(actual) == stable_hash(expected)
            assert json.loads(path.read_text(encoding='utf-8'))['complete'] is True
            break
    else:
        pytest.fail('checkpoint failed to advance')
    assert len(visits) == len(set(visits)) == count * len(inputs.symbols)
    assert inputs.daily is daily_frame
    inputs.assert_unchanged()


@pytest.mark.parametrize('conflict', ['capital', 'input', 'window', 'source', 'tamper', 'cursor'])
def test_reference_checkpoint_rejects_identity_and_state_changes(tmp_path, monkeypatch, conflict):
    from chanlun_trader.research_factory import universe_benchmark_v1 as benchmark
    from chanlun_trader.research_factory.common import stable_hash
    window, bundle = fixture()
    inputs = prepare_universe_account_inputs_v1(bundle, window)
    path = tmp_path / 'PRICE_REFERENCE_CHECKPOINT.json'
    _universe_price_reference_from_inputs(inputs, initial_cash=50000, report_checkpoint_path=path)
    capital = 50000
    if conflict == 'capital':
        capital = 100000
    elif conflict == 'input':
        changed = deepcopy(bundle)
        changed['daily'].loc[changed['daily'].date == window['account_start'], 'high'] += .1
        inputs = prepare_universe_account_inputs_v1(changed, window)
    elif conflict == 'window':
        inputs.window = {**inputs.window, 'account_end': inputs.calendar[-2]}
    elif conflict == 'source':
        monkeypatch.setattr(benchmark, 'source_identity', lambda names: {'synthetic_changed_source': 'f' * 64})
    else:
        saved = json.loads(path.read_text(encoding='utf-8'))
        saved['state']['cursor'] -= 1
        if conflict == 'cursor':
            saved['checkpoint_hash'] = stable_hash({key: value for key, value in saved.items() if key != 'checkpoint_hash'})
        path.write_text(json.dumps(saved), encoding='utf-8')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='CHECKPOINT'):
        _universe_price_reference_from_inputs(inputs, initial_cash=capital, report_checkpoint_path=path)
    assert path.read_bytes() == before
