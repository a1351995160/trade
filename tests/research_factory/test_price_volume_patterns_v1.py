import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.price_volume_patterns_v1 import NAMES,RETEST,SPRING,chosen


def fixture(name):
    c=np.r_[np.linspace(90,109,50),np.full(30,110.)]
    p=pd.DataFrame({'close':c,'open':c,'low':c-1,'high':c+1,'volume':1000.})
    if name==RETEST:
        p.loc[77,['close','open','low','high']]=[112.,111.5,111.3,112.5]
        p.loc[78,['close','open','low','high']]=[111.5,111.8,111.,111.8]
    else:
        p.loc[78,['close','open','low','high']]=[110.5,110.,108.,111.]
    p.loc[78,'volume']=500.
    p.loc[79,['close','open','low','high']]=[112.1,111.5,111.4,112.5]
    return p


@pytest.mark.parametrize('name',NAMES)
def test_fixed_levels_confirmation_volume_and_prefix(name):
    p=fixture(name);result=chosen(p,name)
    assert result.iloc[-1]==pytest.approx(-(p.close.iloc[-1]/p.high.iloc[-2]-1))
    assert result.iloc[-1]<0
    pd.testing.assert_series_equal(result.iloc[:-1],chosen(p.iloc[:-1],name))
    scaled=p.copy();scaled['volume']*=100
    pd.testing.assert_series_equal(chosen(scaled,name),result)
    bad=p.copy();bad.loc[78,'volume']=1000
    assert chosen(bad,name).iloc[-1]==1
    bad=p.copy();bad.loc[79,'close']=p.high.iloc[78]
    assert chosen(bad,name).iloc[-1]==1
    bad=p.copy()
    if name==RETEST:bad.loc[78,'low']=110.99
    else:bad.loc[78,'low']=109.
    assert chosen(bad,name).iloc[-1]==1


@pytest.mark.parametrize('name',NAMES)
def test_volume_input_reset_availability_original_account_and_governance(tmp_path,name):
    from chanlun_trader.research_factory.technical_train_signals_v1 import transform
    from chanlun_trader.research_factory.train_search_batch_v1 import contract
    from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
    from test_structured_exit_trial_v1 import bundle
    from test_train_search_batch_v1 import test_governed_increment_repeat_and_revocation
    p=fixture(name)
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-01-03',periods=len(p))]
    rows=p.rename(columns={k:'signal_'+k for k in ['open','high','low','close']}).assign(
        timestamp=days,symbol='000001.SZ',effective_available_at=pd.Timestamp('2022-01-01',tz='UTC'))
    assert transform(rows,days,name).value.iloc[-1]<0
    bad=rows.copy();bad.loc[70,'volume']=0
    assert not transform(bad,days,name).computable.iloc[70:].any()
    late=rows.copy();late.loc[70,'effective_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    assert transform(late,days,name).effective_available_at.iloc[-1].year==2028
    b,calendar=bundle(name)
    r=_run_account(b,('SYNTHETIC',False),None,contract(name))
    assert {int(f['fill_time'].strftime('%Y%m%d')) for f in r['fills'] if f['side']=='SELL'}=={calendar[22]}
    test_governed_increment_repeat_and_revocation(tmp_path,name)
