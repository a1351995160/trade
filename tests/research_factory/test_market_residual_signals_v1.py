import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.market_residual_signals_v1 import NAMES,REVERSAL,MOMENTUM,residual,transform
from chanlun_trader.research_factory.market_residual_signals_v1 import RISK_NAMES,ZSCORE,LOW_RISK


def inputs():
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-08-01',periods=110)]
    x=np.sin(np.arange(110))*.01
    rows=pd.DataFrame({'symbol':'000001.SZ','timestamp':days,'value':.01+2*x,'market_median':x,
        'effective_available_at':pd.Timestamp('2022-01-01',tz='UTC'),
        'market_available_at':pd.Timestamp('2022-01-01',tz='UTC')})
    return rows,days


def test_ols_by_hand_current_shock_does_not_fit_itself():
    r,_=inputs()
    r.loc[60,'value']-=.05
    error=residual(r.value,r.market_median)
    assert error.iloc[:60].isna().all()
    assert error.iloc[60]==pytest.approx(-.05)
    assert residual(r.value,pd.Series(1.,index=r.index)).isna().all()


@pytest.mark.parametrize('name',NAMES)
def test_prefix_missing_and_late_market(name):
    rows,days=inputs()
    out=transform(rows,days,name)
    pd.testing.assert_frame_equal(out.iloc[:90],transform(rows.iloc[:90],days[:90],name))
    cut=transform(rows.drop(index=70),days,name)
    assert not cut.computable.iloc[70:].any()
    rows.loc[59,'market_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    assert transform(rows,days,name).effective_available_at.iloc[-1].year==2028


def test_persistence_is_mean_of_actual_sequential_residuals():
    rows,days=inputs();rows.loc[60:79,'value']+=np.arange(20)*.001
    expected=-residual(rows.value,rows.market_median).iloc[60:80].mean()
    out=transform(rows,days,MOMENTUM)
    assert not out.computable.iloc[:79].any()
    assert out.value.iloc[79]==pytest.approx(expected)


@pytest.mark.parametrize('name',(*NAMES,*RISK_NAMES))
def test_registered_dispatch_account_and_budget(name,tmp_path):
    from chanlun_trader.research_factory.train_search_batch_v1 import contract,design,transform as dispatch
    from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
    from test_structured_exit_trial_v1 import bundle
    from test_train_search_batch_v1 import test_governed_increment_repeat_and_revocation
    rows,days=inputs()
    pd.testing.assert_frame_equal(dispatch(rows,days,name),transform(rows,days,name))
    assert design(name)['factor_ids']==['RETURN_5D','MEDIAN_ELIGIBLE_RETURN_5D']
    b,days=bundle(name)
    result=_run_account(b,('SYNTHETIC',False),None,contract(name))
    sells=[f for f in result['fills'] if f['side']=='SELL']
    assert len(sells)==3
    assert {int(f['fill_time'].strftime('%Y%m%d')) for f in sells}=={days[22]}
    test_governed_increment_repeat_and_revocation(tmp_path,name)


@pytest.mark.parametrize('name',RISK_NAMES)
def test_risk_score_has_prior_sigma_and_market_gate(name):
    rows,days=inputs()
    rows['value']+=np.cos(np.arange(len(rows))*.73)*.005
    rows.loc[100,'market_median']=.01
    rows.loc[100,'value']= -.03 if name==ZSCORE else .08
    e=residual(rows.value,rows.market_median)
    sigma=e.iloc[80:100].std(ddof=0)
    expected=e.iloc[100]/sigma if name==ZSCORE else -1/(1+sigma)
    result=transform(rows,days,name)
    assert result.value.iloc[100]==pytest.approx(expected)
    assert result.value.iloc[100]<0
    assert not result.computable.iloc[:80].any()
    rows.loc[100,'market_median']=-.01
    assert transform(rows,days,name).value.iloc[100]==1
