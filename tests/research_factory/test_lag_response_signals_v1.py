import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.lag_response_signals_v1 import NAMES,SELF,MARKET,forecast
from chanlun_trader.research_factory.market_residual_signals_v1 import transform
from test_skill_alpha_signals_v1 import test_three_session_account_contract_novelty_and_budget as account_check


def inputs(name):
    i=np.arange(110);days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-08-01',periods=110)]
    x=.01+.01*np.sin(i*.5)
    y=.002+.02*np.sin(i*.5) if name==SELF else .001+.5*(.01+.01*np.sin((i-5)*.5))
    return pd.DataFrame({'symbol':'000001.SZ','timestamp':days,'value':y,'market_median':x,
        'effective_available_at':pd.Timestamp('2022-01-01',tz='UTC'),
        'market_available_at':pd.Timestamp('2022-01-01',tz='UTC')}),days


def test_pairing_hand_current_not_in_fit():
    rows,_=inputs(MARKET);y=rows.value;x=rows.market_median
    predicted,beta=forecast(y,x)
    i=70;past_x=x.iloc[i-65:i-5];past_y=y.iloc[i-60:i]
    b=np.mean((past_x.to_numpy()-past_x.mean())*(past_y.to_numpy()-past_y.mean()))/past_x.var(ddof=0)
    assert beta.iloc[i]==pytest.approx(b)
    assert predicted.iloc[i]==pytest.approx(past_y.mean()+b*(x.iloc[i]-past_x.mean()))
    changed=y.copy();changed.iloc[i]=100
    p2,b2=forecast(changed,x)
    assert p2.iloc[i]==pytest.approx(predicted.iloc[i]);assert b2.iloc[i]==pytest.approx(beta.iloc[i])
    assert forecast(y,pd.Series(1.,index=y.index))[0].isna().all()
    assert predicted.iloc[:65].isna().all()


@pytest.mark.parametrize('name',NAMES)
def test_prefix_gap_calendar_time_and_dispatch(name):
    from chanlun_trader.research_factory.train_search_batch_v1 import transform as dispatch
    r,days=inputs(name);out=transform(r,days,name)
    assert (out.value<0).any()
    pd.testing.assert_frame_equal(out,dispatch(r,days,name))
    pd.testing.assert_frame_equal(out.iloc[:90],transform(r.iloc[:90],days,name).iloc[:90])
    assert out.value.iloc[-1]==1
    assert not transform(r.drop(index=80),days,name).computable.iloc[80:].any()
    r.loc[60,'effective_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    assert transform(r,days,name).effective_available_at.iloc[-1].year==2028


@pytest.mark.parametrize('name',NAMES)
def test_original_account_and_increment(name,tmp_path):
    account_check(name,tmp_path)
