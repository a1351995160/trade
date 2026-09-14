import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.shock_consolidation_signals_v1 import NAMES,BREAK,SUPPORT,chosen
from chanlun_trader.research_factory.technical_train_signals_v1 import transform


def fixture(name):
    p=pd.DataFrame({'open':100.,'high':101.,'low':99.,'close':100.,'volume':1000.},index=range(50))
    i=47 if name==BREAK else 46
    p.loc[i]=[100.,110.,99.,109.,3000.]
    for j in range(i+1,50):p.loc[j]=[106.,108.,105.,107.,1000.]
    if name==BREAK:p.loc[49]=[108.,112.,107.,111.,1500.]
    else:p.loc[48,'close']=106.
    return p


@pytest.mark.parametrize('name',NAMES)
def test_hand_calculation_scale_and_prefix(name):
    p=fixture(name);r=chosen(p,name)
    expected=-(111/110-1) if name==BREAK else -104.5/107
    assert r.iloc[-1]==pytest.approx(expected)
    pd.testing.assert_series_equal(r.iloc[:-1],chosen(p.iloc[:-1],name))
    scaled=p.copy();scaled[['open','high','low','close']]*=3;scaled.volume*=100
    np.testing.assert_allclose(r,chosen(scaled,name),atol=1e-12)
    bad=p.copy();bad.loc[48,'low']=104.49
    assert chosen(bad,name).iloc[-1]==1
    bad=p.copy();bad.loc[48,'volume']=3000
    assert chosen(bad,name).iloc[-1]==1


@pytest.mark.parametrize('name',NAMES)
def test_missing_market_calendar_and_availability(name):
    p=fixture(name)
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-01-03',periods=len(p))]
    rows=p.rename(columns={k:'signal_'+k for k in ('open','high','low','close')}).assign(
        timestamp=days,symbol='000001.SZ',effective_available_at=pd.Timestamp('2022-01-01',tz='UTC'),
        market_median=.01,market_available_at=pd.Timestamp('2022-01-01',tz='UTC'))
    assert transform(rows,days,name).value.iloc[-1]<0
    assert not transform(rows.drop(index=30),days,name).computable.iloc[30:].any()
    bad=rows.copy();bad.loc[30,'volume']=0
    assert not transform(bad,days,name).computable.iloc[30:].any()
    bad=rows.copy();bad.loc[49,'market_median']=np.nan
    assert not transform(bad,days,name).computable.iloc[-1]
    bad=rows.copy();bad.loc[30,'effective_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    assert transform(bad,days,name).effective_available_at.iloc[-1].year==2028


@pytest.mark.parametrize('name',NAMES)
def test_original_three_session_account_and_budget(name,tmp_path):
    from test_skill_alpha_signals_v1 import test_three_session_account_contract_novelty_and_budget
    test_three_session_account_contract_novelty_and_budget(name,tmp_path)
