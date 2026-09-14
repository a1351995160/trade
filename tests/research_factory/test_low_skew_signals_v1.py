import numpy as np
import pandas as pd
import pytest
from chanlun_trader.research_factory.low_skew_signals_v1 import NAME,total_skew,chosen


def test_population_moment_and_degenerate():
    a=np.array([-2.,-1.,0.,0.,3.])
    assert total_skew(a)==pytest.approx(18/5/(14/5)**1.5)
    assert total_skew(-a)==pytest.approx(-total_skew(a))
    assert total_skew(a*10+7)==pytest.approx(total_skew(a))
    assert np.isnan(total_skew(np.zeros(20)))


def test_hand_calendar_causality_prepare_account(tmp_path,monkeypatch):
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-07-01',periods=70)]
    c=100+np.arange(70)*.3;c[64]-=1
    p=pd.DataFrame({'date':days,'close':c,'raw_close':10.})
    returns=(c[46:66]/c[45:65]-1)
    centered=returns-returns.mean()
    skew=np.mean(centered**3)/np.mean(centered**2)**1.5
    actual=chosen(p,NAME,days)
    assert actual.iloc[65]==pytest.approx(-1/(1+np.exp(skew)))
    assert actual.iloc[64]==1
    pd.testing.assert_series_equal(actual.iloc[:66],chosen(p.iloc[:66],NAME,days))
    bad=p.copy();bad.loc[65,'raw_close']=34
    assert chosen(bad,NAME,days).iloc[65]==1
    from test_trend_risk_horizon_signals_v1 import test_actual_prepare_keeps_raw_and_hfq
    test_actual_prepare_keeps_raw_and_hfq(monkeypatch,tmp_path,NAME)
    from test_return_path_signals_v1 import test_actual_account_fixed_exit_and_budget
    test_actual_account_fixed_exit_and_budget(NAME,tmp_path)
