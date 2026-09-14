import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.market_sensitivity_signals_v1 import NAMES,LOW,DOWN,slope
from chanlun_trader.research_factory.market_residual_signals_v1 import transform
from test_market_residual_signals_v1 import inputs
from test_return_path_signals_v1 import test_actual_account_fixed_exit_and_budget as account_check


@pytest.mark.parametrize('downside',[False,True])
def test_slope_hand_and_current_exclusion(downside):
    r,_=inputs();r['value']=.01+.5*r.market_median
    r.loc[60,'value']=10
    assert slope(r.value,r.market_median,downside).iloc[60]==pytest.approx(.5)
    x=r.market_median.iloc[20:80];y=r.value.iloc[20:80]
    if downside:y=y[x<0];x=x[x<0]
    expected=((x-x.mean())*(y-y.mean())).mean()/((x-x.mean())**2).mean()
    assert slope(r.value,r.market_median,downside).iloc[80]==pytest.approx(expected)
    assert slope(r.value,pd.Series(1.,index=r.index),downside).isna().all()
    assert slope(r.value,r.market_median.abs(),True).isna().all()


@pytest.mark.parametrize('name',NAMES)
def test_dispatch_prefix_missing_time_and_week(name):
    from chanlun_trader.research_factory.train_search_batch_v1 import transform as dispatch
    r,days=inputs();r['value']=.01+.5*r.market_median
    out=transform(r,days,name)
    assert (out.value<0).any()
    np.testing.assert_allclose(out.loc[out.value<0,'value'],-1/1.5)
    pd.testing.assert_frame_equal(dispatch(r,days,name),out)
    pd.testing.assert_frame_equal(out.iloc[:90],transform(r.iloc[:90],days,name).iloc[:90])
    assert out.value.iloc[-1]==1
    assert not transform(r.drop(index=70),days,name).computable.iloc[70:].any()
    r.loc[59,'market_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    assert transform(r,days,name).effective_available_at.iloc[-1].year==2028
    r['value']=.01+2*r.market_median
    assert transform(r,days,name).value.dropna().eq(1).all()


@pytest.mark.parametrize('name',NAMES)
def test_original_account_and_budget(name,tmp_path):
    account_check(name,tmp_path)
