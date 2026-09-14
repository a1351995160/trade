import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.return_path_signals_v1 import NAMES,NIGHT,GRADUAL,components,chosen
from chanlun_trader.research_factory.technical_train_signals_v1 import transform
from chanlun_trader.research_factory.train_search_batch_v1 import contract


def fixture():
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-08-01',periods=90)]
    c=100*np.exp(np.arange(90)*.001)
    f=pd.DataFrame({'date':days,'close':c,'open':c*1.001,'high':c*1.001+1,'low':c-1})
    return f,days


def test_log_decomposition_and_scores_by_hand():
    f,days=fixture();n,d=components(f)
    np.testing.assert_allclose((n+d).iloc[1:],np.log(f.close/f.close.shift()).iloc[1:],atol=1e-14)
    out=chosen(f,NIGHT,days)
    i=next(i for i in range(61,89) if out.iloc[i]<0)
    sigma=f.close.pct_change(fill_method=None).iloc[i-19:i+1].std(ddof=0)
    assert out.iloc[i]==pytest.approx(-n.iloc[i-19:i+1].sum()/(1+sigma))
    assert chosen(f,GRADUAL,days).iloc[i]==pytest.approx(-(1-5/60)/(1+sigma))
    assert out.iloc[-1]==1  # 不把日历末端自动当作已确认周末


@pytest.mark.parametrize('name',NAMES)
def test_prefix_scale_missing_and_late(name):
    f,days=fixture()
    pd.testing.assert_series_equal(chosen(f,name,days).iloc[:75],chosen(f.iloc[:75],name,days))
    scaled=f.copy();scaled[['open','high','low','close']]*=100
    np.testing.assert_allclose(chosen(f,name,days),chosen(scaled,name,days),equal_nan=True,atol=1e-10)
    rows=f.rename(columns={c:'signal_'+c for c in ('open','high','low','close')}).rename(columns={'date':'timestamp'})
    rows=rows.assign(symbol='000001.SZ',effective_available_at=pd.Timestamp('2022-01-01',tz='UTC'))
    out=transform(rows,days,name)
    assert (out.value<0).any()
    assert not transform(rows.drop(index=65),days,name).computable.iloc[65:].any()
    rows.loc[65,'effective_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    assert transform(rows,days,name).effective_available_at.iloc[-1].year==2028


@pytest.mark.parametrize('name',NAMES)
def test_actual_account_fixed_exit_and_budget(name,tmp_path):
    from test_structured_exit_trial_v1 import bundle
    from test_train_search_batch_v1 import test_governed_increment_repeat_and_revocation
    from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
    b,days=bundle(name)
    result=_run_account(b,('SYNTHETIC',False),None,contract(name))
    sells=[f for f in result['fills'] if f['side']=='SELL']
    assert len(sells)==3
    assert {int(f['fill_time'].strftime('%Y%m%d')) for f in sells}=={days[22]}
    assert result['metrics']['ending_equity']==pytest.approx(10000-30-18-4.4955)
    test_governed_increment_repeat_and_revocation(tmp_path,name)
