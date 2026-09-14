import numpy as np
import pandas as pd
import pytest
from chanlun_trader.research_factory.failed_low_break_signals_v1 import NAMES,SAME,NEXT,chosen


def fixture(name):
    p=pd.DataFrame({'open':102.,'high':104.,'low':101.,'close':103.,'raw_close':10.},index=range(45))
    p.loc[35,'low']=100.
    if name==SAME:p.loc[44,['low','close']]=[99.,102.]
    else:
        p.loc[43,['low','close','open']]=[99.,99.5,101.]
        p.loc[44,['low','close']]=[99.5,102.]
    return p


@pytest.mark.parametrize('name',NAMES)
def test_hand_ties_boundaries_prefix_and_units(name):
    p=fixture(name);r=chosen(p,name)
    assert r.iloc[-1]==pytest.approx(-3/5 if name==SAME else -2.5/4.5)
    pd.testing.assert_series_equal(r.iloc[:-1],chosen(p.iloc[:-1],name))
    scaled=p.copy();scaled[['open','high','low','close']]*=3
    np.testing.assert_allclose(r,chosen(scaled,name),equal_nan=True)
    bad=p.copy();bad.loc[42,'low']=100.
    assert chosen(bad,name).iloc[-1]==1 # 并列的最近一次不足四天
    bad=p.copy();bad.loc[44,'close']=100.
    assert chosen(bad,name).iloc[-1]==1 # 严格收回，不含相等
    bad=p.copy();bad.loc[44,'raw_close']=34.
    assert chosen(bad,name).iloc[-1]==1
    bad=p.copy();bad.loc[44,'raw_close']=np.nan
    assert pd.isna(chosen(bad,name).iloc[-1])
    bad=p.copy();bad.loc[44,['low','high']]=102.
    assert pd.isna(chosen(bad,name).iloc[-1])
    if name==NEXT:
        bad=p.copy();bad.loc[43,'close']=100.1
        assert chosen(bad,name).iloc[-1]==1 # 不把当天收回重复编入次日
        bad=p.copy();bad.loc[44,'low']=98.9
        assert chosen(bad,name).iloc[-1]==1


@pytest.mark.parametrize('name',NAMES)
def test_real_transform_calendar_time_prepare_and_account(name,tmp_path,monkeypatch):
    from chanlun_trader.research_factory.technical_train_signals_v1 import transform
    p=fixture(name)
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-08-01',periods=len(p))]
    rows=p.rename(columns={k:'signal_'+k for k in ('open','high','low','close')}).assign(
        close=p.raw_close,timestamp=days,symbol='000001.SZ',market_median=.01,
        market_available_at=pd.Timestamp('2022-01-01',tz='UTC'),effective_available_at=pd.Timestamp('2022-01-01',tz='UTC'))
    result=transform(rows,days,name)
    assert result.value.iloc[-1]==pytest.approx(chosen(p,name).iloc[-1])
    assert not transform(rows.drop(index=30),days,name).computable.iloc[30:].any()
    bad=rows.copy();bad.loc[40,'effective_available_at']=pd.Timestamp('2030-01-01',tz='UTC')
    assert transform(bad,days,name).effective_available_at.iloc[-1].year==2030
    bad=rows.copy();bad.loc[44,'market_median']=np.nan
    assert not transform(bad,days,name).computable.iloc[-1]
    from test_trend_risk_horizon_signals_v1 import test_actual_prepare_keeps_raw_and_hfq
    test_actual_prepare_keeps_raw_and_hfq(monkeypatch,tmp_path,name)
    from test_skill_alpha_signals_v1 import test_three_session_account_contract_novelty_and_budget
    test_three_session_account_contract_novelty_and_budget(name,tmp_path)
