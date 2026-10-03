"""停牌区段的价格证据与新版扫描/独立核账；合成资料不授予真实资格。"""
from copy import deepcopy

import pandas as pd
import pytest

from chanlun_trader.research_factory.causal_dividend_features_v1 import (
    causal_hfq_bars, causal_hfq_bars_v2, causal_hfq_bars_v3,
)
from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3
from chanlun_trader.research_factory.universe_account_backend_v2 import SegmentBoundary
from chanlun_trader.research_factory.universe_account_inputs_v1 import UniverseAccountInputsV1
from chanlun_trader.research_factory.universe_execution_artifacts_v1 import hydrated_result
from chanlun_trader.research_factory.universe_signal_scan_v1 import UniverseSignalScanV1
from chanlun_trader.research_factory.universe_signal_scan_v2 import UniverseSignalScanV2
from test_universe_evidence_v2 import audit, rerun_v2
from universe_test_fixture_v1 import fixture, proposal


DAYS = [20230102, 20230103, 20230104, 20230105, 20230106, 20230109]
SOURCES = {'calendar': 'a' * 64, 'suspension': 'b' * 64}


def action(key='cash', *, record=DAYS[1], effective=DAYS[2], cash=.01, ratio=None):
    event = {'event_id': key, 'symbol': '000001.SZ', 'event_type': 'CASH_DIVIDEND',
        'record_date': record, 'effective_date': effective,
        'source_published_at': str(record), 'units': 'CNY_PER_SHARE', 'terms': {'cash_per_share': cash}}
    if ratio is not None:
        event.update(event_type='CAPITALIZATION', units='NEW_SHARES_PER_OLD_SHARE',
            terms={'ratio_numerator': ratio, 'ratio_denominator': 10})
    return event


def raw_bars(reference=5.10, *, before_day=DAYS[0], before=5.11, resume_day=DAYS[-1], volume=100.):
    return pd.DataFrame([{'symbol': '000001.SZ', 'date': day, 'open': close, 'high': close,
        'low': close, 'close': close, 'prev_close': before if index == 0 else reference,
        'volume': 100. if index == 0 else volume, 'amount': close * (100. if index == 0 else volume),
        'adjustflag': '3'} for index, (day, close) in enumerate([(before_day, before), (resume_day, reference)])])


def suspended(symbol, day):
    return {'state_known': True, 'listed': True, 'delisted': False, 'suspension_status': 'SUSPENDED',
        'source': 'suspension', 'historical_availability': 'MODELED',
        'effective_date': DAYS[1], 'valid_to': DAYS[-2]}


def transform(raw, events, state=suspended, **kw):
    return causal_hfq_bars_v3(raw, tuple(events), kw.get('calendar', DAYS), state,
        calendar_source=kw.get('calendar_source', 'calendar'), source_hashes=SOURCES)


@pytest.mark.parametrize('shares', [False, True])
def test_ordinary_cash_and_shares_keep_legacy_prices_and_trace(shares):
    events = [action()]
    if shares:
        events.append(action('shares', ratio=13))
    reference = (5.11 - .01) / (1.3 if shares else 1.)
    raw = raw_bars(reference, before_day=DAYS[1], resume_day=DAYS[2], volume=130. if shares else 100.)
    actual, trace = transform(raw, events)
    legacy, old_trace = (causal_hfq_bars_v2 if shares else causal_hfq_bars)(raw, tuple(events))
    pd.testing.assert_frame_equal(actual, legacy)
    assert trace == old_trace


def test_ordinary_multiple_cash_components_use_grouped_terms():
    events = [action(), action('second', cash=.02)]
    raw = raw_bars(5.08, before_day=DAYS[1], resume_day=DAYS[2])
    actual, trace = transform(raw, events)
    legacy, old_trace = causal_hfq_bars_v2(raw, tuple(events))
    pd.testing.assert_frame_equal(actual, legacy)
    assert trace == old_trace


@pytest.mark.parametrize('record_has_bar', [False, True])
def test_suspended_record_or_ex_date_uses_official_resume_reference(record_has_bar):
    raw = raw_bars(8.81, before=8.94, before_day=DAYS[1] if record_has_bar else DAYS[0])
    original = raw.copy(deep=True)
    out, trace = transform(raw, [action(cash=.126939)])
    pd.testing.assert_frame_equal(raw, original)
    assert out['date'].tolist() == raw['date'].tolist()
    assert out.iloc[0]['close'] == 8.94
    assert out.iloc[-1]['close'] == pytest.approx(8.94)
    assert trace[0]['step_factor'] == 8.94 / 8.81
    assert trace[0]['expected_ex_reference'] == pytest.approx(8.813061)
    assert trace[0]['record_date'] == DAYS[1]
    assert trace[0]['resume_date'] == DAYS[-1]
    assert trace[0]['reference_date'] == raw.iloc[0]['date']
    proof = trace[0]['suspension_sessions']
    assert [row['date'] for row in proof] == DAYS[2 if record_has_bar else 1:-1]
    assert all(row['source'] == 'suspension' and row['historical_availability'] == 'MODELED' for row in proof)


def test_multiple_events_in_one_halt_follow_actual_order_and_share_units():
    events = [action(), action('later-cash', record=DAYS[3], effective=DAYS[4], cash=.02),
              action('shares', record=DAYS[3], effective=DAYS[4], ratio=13)]
    raw = raw_bars((5.11 - .01 - .02) / 1.3, volume=130.)
    out, trace = transform(raw, reversed(events))
    assert [row['event_ids'] for row in trace] == [['cash'], ['later-cash', 'shares']]
    assert trace[-1]['expected_before_reference'] == pytest.approx(5.10)
    assert trace[-1]['cumulative_factor'] == pytest.approx(5.11 / raw.iloc[-1]['prev_close'])
    assert out.iloc[-1]['close'] == pytest.approx(5.11)
    assert out.iloc[-1]['volume'] == pytest.approx(100.)
    assert out.iloc[-1]['amount'] == pytest.approx(511.)


@pytest.mark.parametrize('day', DAYS[1:-1])
@pytest.mark.parametrize('change', [
    {'state_known': False}, {'suspension_status': 'TRADING'},
    {'listed': False, 'suspension_status': 'NOT_LISTED'},
    {'listed': False, 'delisted': True, 'suspension_status': 'DELISTED'},
    {'source': 'unregistered'}, {'source': 'UNKNOWN'},
])
def test_every_missing_session_requires_known_source_backed_listed_suspension(day, change):
    def state(symbol, session):
        return {**suspended(symbol, session), **(change if session == day else {})}
    with pytest.raises(ValueError, match='^CAUSAL_PRICE_SUSPENSION_NOT_PROVEN$'):
        transform(raw_bars(), [action()], state)


def test_absent_state_and_calendar_source_are_rejected():
    with pytest.raises(ValueError, match='SUSPENSION_NOT_PROVEN'):
        transform(raw_bars(), [action()], lambda *args: {})
    with pytest.raises(ValueError, match='CALENDAR_SOURCE_UNVERIFIED'):
        transform(raw_bars(), [action()], calendar_source='UNKNOWN')


@pytest.mark.parametrize('event, error', [
    (action(record=DAYS[0]), 'RECORD_DATE_MISMATCH'),
    (action(record=20221230), 'ACTION_DATE_OUTSIDE_CALENDAR'),
    (action(effective=20230107), 'ACTION_DATE_OUTSIDE_CALENDAR'),
    ({**action(), 'source_published_at': str(DAYS[2])}, 'ACTION_NOT_KNOWN_AT_RECORD'),
])
def test_dates_and_known_at_record_are_checked_against_exchange_calendar(event, error):
    with pytest.raises(ValueError, match=error):
        transform(raw_bars(), [event])


@pytest.mark.parametrize('reference', [5.08, 0., float('nan')])
def test_wrong_or_invalid_official_resume_reference_is_rejected(reference):
    with pytest.raises(ValueError, match='DIVIDEND_REFERENCE_CONFLICT'):
        transform(raw_bars(reference), [action()])


def test_multiple_event_dates_cannot_hide_same_day_record_conflict():
    with pytest.raises(ValueError, match='SAME_DAY_RECORD_CONFLICT'):
        transform(raw_bars(), [action(), action('other', record=DAYS[0])])


def test_future_bar_cannot_change_past_and_zero_activity_cannot_create_feature_bars():
    raw = raw_bars()
    zeros = pd.DataFrame([{**raw.iloc[0].to_dict(), 'date': day, 'volume': 0., 'amount': 0.}
                          for day in DAYS[1:-1]])
    with_zeros = pd.concat([raw, zeros], ignore_index=True)
    out, trace = transform(with_zeros, [action()])
    assert out['date'].tolist() == [DAYS[0], DAYS[-1]]
    assert all(row['raw_record_present'] for row in trace[0]['suspension_sessions'])
    changed = with_zeros.copy(deep=True)
    changed.loc[changed.date.eq(DAYS[-1]), ['open', 'high', 'low', 'close']] = 100.
    after, _ = transform(changed, [action()])
    pd.testing.assert_frame_equal(out.iloc[:1], after.iloc[:1])
    pd.testing.assert_frame_equal(out.iloc[:1], raw.iloc[:1])
    empty, empty_trace = transform(zeros, [])
    assert empty.empty and empty_trace == []


def sparse_case():
    window, bundle = fixture(symbols=['000001.SZ'], days_count=71)
    days = window['calendar']
    original = bundle['states'].iloc[0].to_dict()
    bundle['states'] = pd.DataFrame([{**original, 'valid_to': days[63]},
        {**original, 'effective_date': days[64], 'valid_to': days[66], 'suspension_status': 'SUSPENDED'},
        {**original, 'effective_date': days[67]}])
    bundle['daily'] = bundle['daily'].loc[~bundle['daily'].date.isin(days[64:67])].copy()
    resumed = bundle['daily'].date.ge(days[67])
    bundle['daily'].loc[resumed, ['open', 'high', 'low', 'close', 'prev_close']] -= .01
    bundle['daily'].loc[resumed, 'amount'] = bundle['daily'].loc[resumed, 'close'] * bundle['daily'].loc[resumed, 'volume']
    bundle['events'] = [{**action(record=days[64], effective=days[65]), 'source': 'synthetic',
        'source_published_at': str(days[62]), 'payment_date': days[68],
        'terms': {'cash_per_share': .01, 'tax_rule': {'kind': 'DEFERRED_INDIVIDUAL_2015_101',
            'source': 'https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html'}}}]
    rule = proposal()
    rule['max_hold_sessions'] = 20
    return window, bundle, rule


def test_v1_default_stays_strict_and_v2_matches_its_own_independent_feature_table(tmp_path):
    from chanlun_trader.research_factory.universe_evidence_v2 import _ConditionTable
    window, bundle, rule = sparse_case()
    strategy = ResearchRuleStrategyV3(rule, strategy_id='auditv2')
    inputs = UniverseAccountInputsV1(bundle, window, stage='ACCOUNT')
    with pytest.raises(ValueError, match='^CAUSAL_PRICE_EX_DATE_MISSING$'):
        UniverseSignalScanV1(strategy, inputs)
    old = UniverseSignalScanV1(strategy, inputs, allow_data_gaps=True)
    assert old.preparation[0]['status'] == 'UNKNOWN'
    scanner = UniverseSignalScanV2(strategy, inputs, root=tmp_path / 'execution')
    own = _ConditionTable(strategy, inputs, tmp_path / 'own', 'synthetic-own-identity')
    try:
        assert own.preparation == scanner.preparation
        assert scanner.preparation[0]['bars'] == len(window['calendar']) - 3
        assert scanner.preparation[0]['price_trace'][0]['resume_date'] == window['calendar'][67]
        for day in window['calendar']:
            assert scanner.at('000001.SZ', day) == own.truth('000001.SZ', day)
        assert inputs.historical_availability == 'MODELED'
        assert bundle['events'] == inputs.bundle['events']
    finally:
        scanner.values._mmap.close()
        own.values._mmap.close()


def test_v2_ordinary_preparation_remains_identical_to_legacy_scan(tmp_path):
    window, bundle = fixture(symbols=['000001.SZ'], days_count=65)
    strategy = ResearchRuleStrategyV3(proposal(), strategy_id='same')
    inputs = UniverseAccountInputsV1(bundle, window, stage='ACCOUNT')
    legacy = UniverseSignalScanV1(strategy, inputs)
    scanner = UniverseSignalScanV2(strategy, inputs, root=tmp_path)
    try:
        assert scanner.preparation == legacy.preparation
        for day in window['calendar']:
            assert scanner.at('000001.SZ', day) == legacy.at('000001.SZ', day)
    finally:
        scanner.values._mmap.close()


def test_sparse_dividend_account_daily_and_execution_audit_resume_are_exact(tmp_path, monkeypatch):
    from chanlun_trader.research_factory import universe_evidence_v2 as module
    window, bundle, rule = sparse_case()
    continuous = rerun_v2(tmp_path / 'continuous', window, bundle, rule)
    with pytest.raises(SegmentBoundary):
        rerun_v2(tmp_path / 'segmented', window, bundle, rule, stop=window['calendar'][65])
    resumed = rerun_v2(tmp_path / 'segmented', window, bundle, rule)
    assert resumed['final_account_checkpoint'] == continuous['final_account_checkpoint']
    assert list(hydrated_result(resumed)['daily_accounts']) == list(hydrated_result(continuous)['daily_accounts'])
    data = bundle, window, rule, resumed
    original = module._ReconstructionV2.decisions
    def pause_after_ex_date(self, day, frames):
        value = original(self, day, frames)
        if day == window['calendar'][65]:
            monkeypatch.setattr(module.time, 'monotonic', lambda: 100.)
        return value
    monkeypatch.setattr(module.time, 'monotonic', lambda: 0.)
    monkeypatch.setattr(module._ReconstructionV2, 'decisions', pause_after_ex_date)
    path = tmp_path / 'audit' / 'AUDIT.json'
    with pytest.raises(SegmentBoundary):
        audit(data, audit_checkpoint_path=path, segment_seconds=1.)
    monkeypatch.setattr(module._ReconstructionV2, 'decisions', original)
    rebuilt = audit(data, audit_checkpoint_path=path)
    assert rebuilt == audit(data)
    assert rebuilt['daily_accounts'] == list(hydrated_result(resumed)['daily_accounts'])
    assert rebuilt['metrics'] == resumed['metrics']
    assert resumed['reconciliation']['passed'] and rebuilt['strategy_qualified'] is False
    assert any(row['stale_valuations'] for row in rebuilt['daily_accounts'])


def test_sparse_audit_does_not_use_execution_feature_cache_or_ledger_answers(tmp_path, monkeypatch):
    from chanlun_trader.engine.ledger import PortfolioLedger
    window, bundle, rule = sparse_case()
    result = rerun_v2(tmp_path / 'execution', window, bundle, rule)
    def forbidden(*args, **kw):
        raise AssertionError('EXECUTION_USED_AS_AUDIT_ORACLE')
    monkeypatch.setattr(UniverseSignalScanV1, '__init__', forbidden)
    monkeypatch.setattr(UniverseSignalScanV2, 'at', forbidden)
    monkeypatch.setattr(PortfolioLedger, 'apply_fill', forbidden)
    assert audit((bundle, window, rule, result))['metrics'] == result['metrics']
