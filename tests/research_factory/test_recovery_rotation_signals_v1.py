import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.recovery_rotation_signals_v1 import NAMES,RECOVERY,ROTATION,chosen
from chanlun_trader.research_factory.technical_train_signals_v1 import wilder_rsi,transform
from chanlun_trader.research_factory.train_search_batch_v1 import contract
from chanlun_trader.research_factory.fixed_account_rules import FixedAccountRules


def prices():
    c=np.r_[np.linspace(100,150,205),[149,148,147,146,145,144,143,142]]
    return pd.DataFrame({'close':c,'open':c,'high':c+.1,'low':c-.1})


@pytest.mark.parametrize('name',NAMES)
def test_fixed_signals_prefix_and_price_scale(name):
    f=prices() if name==RECOVERY else prices().iloc[:-3]
    out=chosen(f,name,wilder_rsi)
    assert out.iloc[-1]<0
    pd.testing.assert_series_equal(out.iloc[:-1],chosen(f.iloc[:-1],name,wilder_rsi))
    np.testing.assert_allclose(out,chosen(f*10,name,wilder_rsi),atol=1e-10)
    assert chosen(f.assign(close=100.),name,wilder_rsi).eq(1).all()


def test_skip_recent_five_by_hand():
    f=prices().iloc[:-3];out=chosen(f,ROTATION,wilder_rsi)
    assert out.iloc[-1]==pytest.approx(1-f.close.iloc[-6]/f.close.iloc[-66])


def test_recovery_contract_and_actual_account_exit():
    from test_structured_exit_trial_v1 import bundle
    from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
    b,days=bundle(RECOVERY)
    rules=FixedAccountRules(contract(RECOVERY))
    assert rules.exit_factor=='CLOSE_ABOVE_SMA5'
    result=_run_account(b,('SYNTHETIC',False),None,contract(RECOVERY))
    sells=[f for f in result['fills'] if f['side']=='SELL']
    assert len(sells)==3
    assert {int(f['fill_time'].strftime('%Y%m%d')) for f in sells}=={days[5]}
    assert result['metrics']['ending_equity']==pytest.approx(10000-30-18-4.4955)
    bad=contract(RECOVERY);bad['exit_policy']['factor_conditions'][0]['factor_id']='CLOSE_BELOW_EMA20'
    with pytest.raises(ValueError,match='CONFLICT'):FixedAccountRules(bad)


def test_recovery_transform_uses_above_mean_and_resets_missing():
    f=prices();days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-08-01',periods=len(f))]
    rows=f.rename(columns={k:'signal_'+k for k in f}).assign(symbol='000001.SZ',timestamp=days,
        effective_available_at=pd.Timestamp('2022-01-01',tz='UTC'))
    out=transform(rows,days,RECOVERY)
    assert out.exit_invalidated.iloc[199]==1 and out.exit_invalidated.iloc[-1]==0
    assert out.value.iloc[-1]<0
    cut=transform(rows.drop(index=200),days,RECOVERY)
    assert not cut.computable.iloc[200:].any()
    rows.loc[199,'effective_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    assert transform(rows,days,RECOVERY).exit_available_at.iloc[-1].year==2028


@pytest.mark.parametrize('name',NAMES)
def test_original_governance_repeat_revocation(name,tmp_path):
    from test_train_search_batch_v1 import test_governed_increment_repeat_and_revocation
    test_governed_increment_repeat_and_revocation(tmp_path,name)
