import pandas as pd
import pytest

from chanlun_trader.research_factory.causal_dividend_features_v1 import causal_hfq_bars_v2
from chanlun_trader.research_factory.corporate_action_price_v2 import expected_reference_v2


def event(kind='CASH_DIVIDEND', key='cash', **kw):
    value = {'event_id': key, 'symbol': '000001.SZ', 'event_type': kind,
        'record_date': 20230103, 'effective_date': 20230104, 'source_published_at': '2023-01-02',
        'units': 'CNY_PER_SHARE', 'terms': {'cash_per_share': .8}}
    if kind != 'CASH_DIVIDEND':
        value.update(units='NEW_SHARES_PER_OLD_SHARE', terms={'ratio_numerator': 13, 'ratio_denominator': 10})
    return {**value, **kw}


def bars(reference):
    return pd.DataFrame([{'symbol': '000001.SZ', 'date': day, 'open': close, 'high': close,
        'low': close, 'close': close, 'prev_close': 10 if i == 0 else reference,
        'volume': 100 if i == 0 else 130, 'amount': close * (100 if i == 0 else 130), 'adjustflag': '3'}
        for i, (day, close) in enumerate([(20230103, 10.), (20230104, reference)])])


def test_cash_and_share_transform_preserves_original_share_units_and_raw_input():
    actions = (event(), event('CAPITALIZATION', 'share'))
    ref = (10 - .8) / 1.3
    raw = bars(ref)
    out, audit = causal_hfq_bars_v2(raw, actions)
    assert raw.iloc[1]['close'] == ref
    assert out.iloc[0]['close'] == 10
    assert out.iloc[1]['close'] == pytest.approx(10)
    assert out.iloc[1]['volume'] == pytest.approx(100)
    assert out.iloc[1]['amount'] == pytest.approx(1000)
    assert audit[0]['share_ratio'] == 1.3
    assert expected_reference_v2(10, actions) == pytest.approx(ref)


def test_multiple_cash_components_are_summed_once():
    actions = (event(terms={'cash_per_share': .286}), event(key='quarter', terms={'cash_per_share': .091}))
    out, audit = causal_hfq_bars_v2(bars(9.623), actions)
    assert out.iloc[1]['close'] == pytest.approx(10)
    assert audit[0]['cash_per_share'] == pytest.approx(.377)
    assert len(audit[0]['event_ids']) == 2


def test_pure_capitalization_needs_no_fake_cash():
    out, _ = causal_hfq_bars_v2(bars(10 / 1.3), (event('CAPITALIZATION'),))
    assert out.iloc[1]['close'] == pytest.approx(10)


def test_differential_dividend_price_reference_is_separate_from_account_cash():
    action = event(terms={'cash_per_share': .377}, price_terms={'cash_per_share': .37444,
        'source': 'official_implementation', 'evidence_quote': '差异化每股除权分红0.37444'})
    _, audit = causal_hfq_bars_v2(bars(9.62556), (action,))
    assert audit[0]['cash_per_share'] == .37444
    assert audit[0]['account_cash_per_share'] == .377
    assert expected_reference_v2(10, (action,)) == pytest.approx(9.62556)


@pytest.mark.parametrize('actions', [
    (event('CAPITALIZATION', 'one'), event('BONUS', 'two')),
    (event(), event(key='other', record_date=20230102)),
    (event(), event()),
])
def test_ambiguous_or_duplicate_terms_are_not_silently_combined(actions):
    with pytest.raises(ValueError):
        causal_hfq_bars_v2(bars(10), actions)


def test_reference_conflict_still_rejected():
    with pytest.raises(ValueError, match='REFERENCE_CONFLICT'):
        causal_hfq_bars_v2(bars(10), (event(), event('CAPITALIZATION', 'share')))
