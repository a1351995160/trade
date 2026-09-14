import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.skill_alpha_signals_v1 import NAMES,SWITCH,DIVERGENCE,alpha009,chosen
from chanlun_trader.research_factory.technical_train_signals_v1 import transform
from chanlun_trader.research_factory.train_search_batch_v1 import contract,design
from test_return_path_signals_v1 import fixture


def test_alpha009_original_branches():
    assert alpha009(pd.Series([10.,11,12,13,14,15])).iloc[-1]==1
    assert alpha009(pd.Series([15.,14,13,12,11,10])).iloc[-1]==-1
    assert alpha009(pd.Series([10.,11,10,11,12,11])).iloc[-1]==1
    assert alpha009(pd.Series([10.,11,10,11,12,13])).iloc[-1]==-1
    assert alpha009(pd.Series([10.,11,10,11,12,12])).iloc[-1]==0


def sample():
    f,days=fixture()
    f.loc[74,'close']=f.close.iloc[69]*.995
    f['open']=f.close*1.001;f['high']=f.open+1;f['low']=f.close-1
    f['volume']=1e6-100*f.open
    return f,days


def test_scores_and_degenerate_correlation():
    f,days=sample()
    assert chosen(f,DIVERGENCE,days).iloc[74]==pytest.approx(-1.)
    expected=-alpha009(f.close).iloc[74]/f.close.iloc[73]
    assert chosen(f,SWITCH,days).iloc[74]==pytest.approx(expected)
    f.volume=1000
    assert chosen(f,DIVERGENCE,days).isna().all()


@pytest.mark.parametrize('name',NAMES)
def test_prefix_scale_missing_market_and_late(name):
    f,days=sample();out=chosen(f,name,days)
    pd.testing.assert_series_equal(out.iloc[:75],chosen(f.iloc[:75],name,days))
    scaled=f.copy();scaled[['open','high','low','close']]*=100;scaled.volume*=100
    np.testing.assert_allclose(out,chosen(scaled,name,days),equal_nan=True,atol=1e-8)
    rows=f.rename(columns={c:'signal_'+c for c in ('open','high','low','close')}).rename(columns={'date':'timestamp'})
    rows=rows.assign(symbol='000001.SZ',effective_available_at=pd.Timestamp('2022-01-01',tz='UTC'),market_median=.01,market_available_at=pd.Timestamp('2022-01-01',tz='UTC'))
    assert (transform(rows,days,name).value<0).any()
    assert not transform(rows.drop(index=65),days,name).computable.iloc[65:].any()
    rows.loc[74,'market_median']=np.nan
    assert not transform(rows,days,name).computable.iloc[74]
    rows.loc[74,'market_median']=.01;rows.loc[74,'market_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    assert transform(rows,days,name).effective_available_at.iloc[74].year==2028
    rows.market_median=-.01
    assert transform(rows,days,name).value.dropna().eq(1).all()


@pytest.mark.parametrize('name',NAMES)
def test_three_session_account_contract_novelty_and_budget(name,tmp_path):
    from test_structured_exit_trial_v1 import bundle
    from test_train_search_batch_v1 import test_governed_increment_repeat_and_revocation
    from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
    b,days=bundle(name)
    assert contract(name)['holding_sessions']==design(name)['holding_period_days']==3
    assert design(name)['parameter_fingerprint']['holding_sessions']==3
    result=_run_account(b,('SYNTHETIC',False),None,contract(name))
    sells=[f for f in result['fills'] if f['side']=='SELL']
    assert len(sells)==3
    assert {int(f['fill_time'].strftime('%Y%m%d')) for f in sells}=={days[5]}
    assert result['metrics']['ending_equity']==pytest.approx(10000-30-18-4.4955)
    test_governed_increment_repeat_and_revocation(tmp_path,name)
