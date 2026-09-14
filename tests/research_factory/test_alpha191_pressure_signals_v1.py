import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.alpha191_pressure_signals_v1 import NAMES,PRICE,VOLUME,components,chosen
from chanlun_trader.research_factory.technical_train_signals_v1 import transform


def fixture():
    c=np.r_[np.full(70,100.),106.,107,108,109,110,109,108,107,106,107]
    p=pd.DataFrame({'close':c,'open':c,'high':c+.1,'low':c-.1,'volume':1000.})
    p.loc[70:74,'volume']=5000.
    p.loc[79,['open','low','high']]=[106.,100.,108.]
    return p


@pytest.mark.parametrize('name',NAMES)
def test_manual_sum_and_scale_prefix(name):
    p=fixture();u,d,_=components(p,name)
    if name==PRICE:
        expected_u=expected_d=0.
        for i in range(74,80):
            c=p.close[i];prev=p.close[i-1];l=p.low[i];h=p.high[i]
            expected_u+=0 if c==prev else c-min(l,prev) if c>prev else c-max(h,prev)
            expected_d+=max(h-l,abs(h-prev),abs(l-prev))
    else:
        expected_u=sum(p.volume[i] for i in range(54,80) if p.close[i]>p.close[i-1])
        expected_d=sum(p.volume[i] for i in range(54,80) if p.close[i]<=p.close[i-1])
    assert u.iloc[-1]==pytest.approx(expected_u)
    assert d.iloc[-1]==pytest.approx(expected_d)
    r=chosen(p,name);assert r.iloc[-1]==pytest.approx(-expected_u/expected_d)
    assert r.iloc[-1]<0
    pd.testing.assert_series_equal(r.iloc[:-1],chosen(p.iloc[:-1],name))
    scaled=p.copy();scaled[['open','high','low','close']]*=100;scaled.volume*=3
    np.testing.assert_allclose(r,chosen(scaled,name),atol=1e-8,equal_nan=True)
    bad=p.copy()
    if name==PRICE:bad.loc[79,'low']=106.9
    else:bad.loc[70:74,'volume']=100
    assert chosen(bad,name).iloc[-1]==1


@pytest.mark.parametrize('name',NAMES)
def test_zero_denominator_and_missing(name):
    p=fixture()
    if name==PRICE:p[['open','high','low','close']]=100.
    else:
        p['close']=np.arange(80)+100.
        p['open']=p.close;p['high']=p.close+1;p['low']=p.close-1
    assert chosen(p,name).isna().all()
    p=fixture();days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-01-03',periods=80)]
    rows=p.rename(columns={k:'signal_'+k for k in ('open','high','low','close')}).assign(timestamp=days,symbol='000001.SZ',effective_available_at=pd.Timestamp('2022-01-01',tz='UTC'),market_median=.01,market_available_at=pd.Timestamp('2022-01-01',tz='UTC'))
    assert transform(rows,days,name).value.iloc[-1]<0
    assert not transform(rows.drop(index=65),days,name).computable.iloc[65:].any()
    rows.loc[79,'market_median']=np.nan
    assert not transform(rows,days,name).computable.iloc[-1]
    rows.loc[79,'market_median']=.01;rows.loc[65,'effective_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    assert transform(rows,days,name).effective_available_at.iloc[-1].year==2028


@pytest.mark.parametrize('name',NAMES)
def test_original_account_and_governance(name,tmp_path):
    from test_skill_alpha_signals_v1 import test_three_session_account_contract_novelty_and_budget
    test_three_session_account_contract_novelty_and_budget(name,tmp_path)
