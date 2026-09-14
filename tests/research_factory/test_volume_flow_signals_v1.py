import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.volume_flow_signals_v1 import NAMES,OBV,RANGE,chosen
from chanlun_trader.research_factory.technical_train_signals_v1 import transform
from chanlun_trader.research_factory.train_search_batch_v1 import contract


def frame(name):
    c=100+np.arange(100)*.2
    if name==OBV:c[-3:]=[118,118,120]
    else:c[-1]=122
    f=pd.DataFrame({'close':c,'open':c,'high':c+.2,'low':c-.8,'volume':100.})
    if name==RANGE:
        f.loc[59:78,'high']+=10
        f.loc[59:78,'low']-=10
    return f


@pytest.mark.parametrize('name',NAMES)
def test_flow_event_prefix_and_scale(name):
    f=frame(name);out=chosen(f,name)
    assert out.iloc[-1]<0
    pd.testing.assert_series_equal(out.iloc[:-1],chosen(f.iloc[:-1],name))
    np.testing.assert_allclose(out,chosen(f.assign(volume=f.volume*100),name),atol=1e-12)
    np.testing.assert_allclose(out,chosen(f*10,name),atol=1e-12)


def test_obv_score_hand_and_confirmation():
    f=frame(OBV)
    expected=-(np.sign(f.close.diff())*f.volume).iloc[-20:].sum()/f.volume.iloc[-20:].sum()
    assert chosen(f,OBV).iloc[-1]==pytest.approx(expected)
    f.loc[97,'volume']=100000
    assert chosen(f,OBV).iloc[-1]==1


def test_ad_score_hand_and_no_contraction():
    f=frame(RANGE)
    expected=-(f.volume*(2*f.close-f.high-f.low)/(f.high-f.low)).iloc[-20:].sum()/2000
    assert chosen(f,RANGE).iloc[-1]==pytest.approx(expected)
    f.loc[79:98,'high']+=30
    assert chosen(f,RANGE).iloc[-1]==1


@pytest.mark.parametrize('name',NAMES)
def test_missing_volume_resets_segment(name):
    f=frame(name);days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-08-01',periods=len(f))]
    rows=f.rename(columns={k:'signal_'+k for k in ['open','high','low','close']}).assign(
        symbol='000001.SZ',timestamp=days,effective_available_at=pd.Timestamp('2022-01-01',tz='UTC'))
    assert transform(rows,days,name).value.iloc[-1]<0
    rows.loc[80,'volume']=np.nan
    assert not transform(rows,days,name).computable.iloc[80:].any()


@pytest.mark.parametrize('name',NAMES)
def test_fixed_account_and_canonical_budget(name,tmp_path):
    from test_structured_exit_trial_v1 import bundle
    from test_train_search_batch_v1 import test_governed_increment_repeat_and_revocation
    from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
    b,days=bundle(name)
    result=_run_account(b,('SYNTHETIC',False),None,contract(name))
    sells=[f for f in result['fills'] if f['side']=='SELL']
    assert len(sells)==3
    assert {int(f['fill_time'].strftime('%Y%m%d')) for f in sells}=={days[22]}
    test_governed_increment_repeat_and_revocation(tmp_path,name)
