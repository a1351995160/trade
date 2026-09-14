import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.response_confirmation_signals_v1 import NAMES,SELF,MARKET
from chanlun_trader.research_factory.lag_response_signals_v1 import forecast,SELF as OLD_SELF,MARKET as OLD_MARKET
from chanlun_trader.research_factory.market_residual_signals_v1 import residual,transform
from test_lag_response_signals_v1 import inputs as base_inputs
from test_return_path_signals_v1 import test_actual_account_fixed_exit_and_budget as account_check


def inputs(name):
    r,days=base_inputs(OLD_SELF if name==SELF else OLD_MARKET)
    if name==SELF:r['market_median']=.02+.01*np.sin(np.arange(len(r))*.13)
    return r,days


@pytest.mark.parametrize('name',NAMES)
def test_exact_intersection_and_prefix(name):
    r,days=inputs(name);out=transform(r,days,name)
    e=residual(r.value,r.market_median)
    sigma=e.shift(1).rolling(20,min_periods=20).std(ddof=0)
    pred,b=forecast(r.value,r.value if name==SELF else r.market_median)
    z=e/sigma
    event=(z<0)&(r.market_median>0)&(pred>0)
    event&=((b>-1)&(b<0)&(r.value<0)) if name==SELF else ((b>0)&(r.value<r.market_median))
    known=np.isfinite(z)&np.isfinite(pred)&np.isfinite(b)
    expected=z.where(event,1.).where(known)
    np.testing.assert_allclose(out.value,expected,equal_nan=True)
    assert (out.value<0).any()
    assert not out.computable.iloc[:80].any()
    pd.testing.assert_frame_equal(out.iloc[:95],transform(r.iloc[:95],days,name).iloc[:95])
    assert not transform(r.drop(index=85),days,name).computable.iloc[85:].any()
    r.loc[60,'market_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    assert transform(r,days,name).effective_available_at.iloc[-1].year==2028


@pytest.mark.parametrize('name',NAMES)
def test_original_twenty_session_account_and_budget(name,tmp_path):
    account_check(name,tmp_path)
