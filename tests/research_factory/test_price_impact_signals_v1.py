import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.price_impact_signals_v1 import NAMES,PREMIUM,IMPROVING,chosen
from chanlun_trader.research_factory.technical_train_signals_v1 import transform
from test_return_path_signals_v1 import fixture,test_actual_account_fixed_exit_and_budget


def sample():
    f,days=fixture()
    return f.assign(amount=1e6*np.exp(np.arange(len(f))*.01)),days


def test_hand_calculation_and_no_final_week():
    f,days=sample()
    i=next(i for i in range(61,89) if chosen(f,IMPROVING,days).iloc[i]<0)
    impact=f.close.pct_change(fill_method=None).abs()/f.amount
    m20=impact.iloc[i-19:i+1].mean();m60=impact.iloc[i-59:i+1].mean()
    assert chosen(f,PREMIUM,days).iloc[i]==pytest.approx(-m20,abs=1e-20)
    assert chosen(f,IMPROVING,days).iloc[i]==pytest.approx(-m60/m20)
    assert chosen(f,PREMIUM,days).iloc[-1]==1
    flat=f.copy();flat[['open','high','low','close']]=100
    assert chosen(flat,IMPROVING,days).iloc[60:].eq(1).all()


@pytest.mark.parametrize('name',NAMES)
def test_prefix_units_missing_amount_and_time(name):
    f,days=sample();out=chosen(f,name,days)
    pd.testing.assert_series_equal(out.iloc[:75],chosen(f.iloc[:75],name,days))
    scaled=f.copy();scaled[['open','high','low','close']]*=100
    np.testing.assert_allclose(out,chosen(scaled,name,days),equal_nan=True,rtol=1e-10,atol=1e-20)
    money=f.copy();money.amount*=100
    target=out.where(out>=0,out/100) if name==PREMIUM else out
    np.testing.assert_allclose(target,chosen(money,name,days),equal_nan=True,rtol=1e-10,atol=1e-20)
    rows=f.rename(columns={c:'signal_'+c for c in ('open','high','low','close')}).rename(columns={'date':'timestamp'})
    rows=rows.assign(symbol='000001.SZ',effective_available_at=pd.Timestamp('2022-01-01',tz='UTC'))
    assert (transform(rows,days,name).value<0).any()
    broken=rows.copy();broken.loc[65,'amount']=0
    assert not transform(broken,days,name).computable.iloc[65:].any()
    assert not transform(rows.drop(index=65),days,name).computable.iloc[65:].any()
    rows.loc[65,'effective_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    assert transform(rows,days,name).effective_available_at.iloc[-1].year==2028


@pytest.mark.parametrize('name',NAMES)
def test_account_and_budget(name,tmp_path):
    test_actual_account_fixed_exit_and_budget(name,tmp_path)
