"""闭包手工数据：单位、缺口、时间最大值和来源覆盖。"""
from types import SimpleNamespace
import pandas as pd
import pytest

from tests.research_factory.test_train_account_runner_v1 import fixture
from chanlun_trader.research_factory.train_input_closure_v1 import close_frames,merge_state_evidence,merge_daily_evidence


def definition():
    return SimpleNamespace(operator_graph={'op':'pct_change','args':[{'op':'field','field':'close'}],'window':5,'min_periods':6},
        feature_price_mode='RETURN_ONLY',implementation_status='EXECUTABLE',required_fields=['close'],cross_sectional=False,
        factor_id='RETURN_5D',version='v1',available_at_rule='T_CLOSE')


def close(b):
    frame=b.daily.rename(columns={'volume':'volume_encoded','amount':'amount_encoded'})
    return close_frames(frame,b.states,b.calendar,b.actions,
        {'source_identity':'SYNTHETIC','evidence_sha256':'a'*64,'price_mode':'RAW','volume_unit':'SHARES','amount_unit':'CNY'},
        definition(),set(b.actions.coverage_symbols),'SYNTHETIC')


def test_original_compiler_calendar_and_observed_late_time():
    b=fixture();b.daily.loc[0,'available_at']='2022-08-10T12:00:00+08:00'
    result=close(b)
    row=result.factors[(result.factors.symbol=='600000.SH')&(result.factors.timestamp==20220808)].iloc[0]
    assert row.value==pytest.approx(9.75/10-1)
    assert row.effective_available_at==pd.Timestamp('2022-08-10T12:00:00+08:00')
    assert result.factors[result.factors.timestamp==20220801].value.isna().all()


def test_missing_member_price_not_removed():
    b=fixture();b.daily=b.daily.iloc[1:]
    with pytest.raises(ValueError,match='COVERAGE'):close(b)


def test_late_state_requires_external_observed_evidence():
    b=fixture();b.states.loc[0,'available_at']='2022-08-02T09:30:00+08:00'
    with pytest.raises(ValueError,match='PIT_TIMING'):close(b)


def test_overlay_keeps_original_and_never_overrides_later_observed_evidence():
    b=fixture();original=b.states.iloc[:1].copy();original.loc[0,'available_at']='2022-08-02T09:30:00+08:00'
    supplement=original.copy();supplement['observed_available_at']='2022-08-01T08:00:00+08:00';supplement['observed_time_source']='SYNTHETIC'
    proof={'kind':'MODELED_NEXT_SESSION_OPEN','owner_attestation':'SYNTHETIC','normalizer_sha256':'a'*64}
    merged=merge_state_evidence(original,supplement,proof)
    assert merged.iloc[0].available_at==original.iloc[0].available_at
    assert pd.Timestamp(merged.iloc[0].effective_state_available_at)==pd.Timestamp('2022-08-01T08:00:00+08:00')
    merged=merge_state_evidence(original,supplement,{'kind':'OBSERVED'})
    assert pd.Timestamp(merged.iloc[0].effective_state_available_at)==pd.Timestamp(original.iloc[0].available_at)


def test_overlay_adds_missing_warmup_without_altering_old_state():
    b=fixture();original=b.states.iloc[:1].copy();supplement=original.copy();supplement['trade_date']='20220729'
    merged=merge_state_evidence(original,supplement,{})
    assert len(merged)==2 and set(merged.trade_date)=={20220729,20220801}


def test_daily_reference_overlay_preserves_prices_and_adds_missing_day():
    original=fixture().daily.iloc[:1].rename(columns={'volume':'volume_encoded','amount':'amount_encoded'})
    supplement=original.copy();supplement['exchange_reference_price']=9.0
    missing=supplement.copy();missing['date']=20220729
    merged=merge_daily_evidence(original,pd.concat([supplement,missing]))
    assert len(merged)==2 and merged.iloc[0].close==original.iloc[0].close
    assert merged.iloc[0].exchange_reference_price==9.0
    supplement.loc[supplement.index[0],'close']=999
    with pytest.raises(ValueError,match='PRICE_CONFLICT'):merge_daily_evidence(original,supplement)


def test_unknown_boolean_is_not_truthy_membership():
    b=fixture();b.states['listed']=b.states.listed.astype(object);b.states.loc[0,'listed']='UNKNOWN'
    with pytest.raises(ValueError,match='BOOLEAN_EVIDENCE'):close(b)
