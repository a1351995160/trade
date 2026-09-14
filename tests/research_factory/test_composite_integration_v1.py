import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.technical_composite_signals_v1 import FORMULAS,WARMUP
from chanlun_trader.research_factory.technical_train_signals_v1 import transform


def rows(n=310):
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-08-01',periods=n)]
    c=100+np.arange(n)*.03+np.sin(np.arange(n))
    data=pd.DataFrame({'symbol':'000001.SZ','timestamp':days,'signal_open':c,
        'signal_high':c+.1,'signal_low':c-.1,'signal_close':c,'amount':100.,
        'effective_available_at':pd.Timestamp('2022-01-01',tz='UTC')})
    return days,data


@pytest.mark.parametrize('name',FORMULAS)
def test_composite_contract_uses_original_governance(tmp_path,name):
    from test_train_search_batch_v1 import test_governed_increment_repeat_and_revocation
    from chanlun_trader.research_factory.train_search_batch_v1 import contract
    from chanlun_trader.research_factory.fixed_account_rules import FixedAccountRules
    test_governed_increment_repeat_and_revocation(tmp_path,name)
    assert FixedAccountRules(contract(name)).contract['holding_sessions']==20


@pytest.mark.parametrize('invalid',[np.nan,0.,-1.,np.inf])
def test_amount_missing_resets_only_amount_dependent_strategy(invalid):
    days,data=rows()
    data.loc[160,'amount']=invalid
    name='SQUEEZE_TREND_TURNOVER_HOLD_20'
    result=transform(data,days,name)
    assert not result.computable.iloc[160:160+WARMUP[name]].any()
    assert result.computable.iloc[160+WARMUP[name]]
    suffix=transform(data.iloc[161:],days[161:],name)
    pd.testing.assert_frame_equal(result.iloc[161:].reset_index(drop=True),suffix)
    assert transform(data,days,'ADX_EMA_PULLBACK_HOLD_20').computable.iloc[160]


def test_amount_scale_is_not_hfq_price_scale_and_late_availability_preserved():
    days,data=rows(200)
    close=110.+np.arange(20)*.001
    data.loc[179:198,'signal_close']=close
    data.loc[199,'signal_close']=112.
    for key,offset in [('signal_open',0),('signal_high',.1),('signal_low',-.1)]:
        data[key]=data.signal_close+offset
    data.loc[199,'amount']=200.
    name='SQUEEZE_TREND_TURNOVER_HOLD_20'
    result=transform(data,days,name)
    assert result.value.iloc[-1]<0
    changed=data.copy()
    changed['amount']*=1000
    pd.testing.assert_frame_equal(result,transform(changed,days,name))
    changed=data.copy()
    for field in ['signal_open','signal_high','signal_low','signal_close']:changed[field]*=10
    np.testing.assert_allclose(result.value,transform(changed,days,name).value,equal_nan=True,rtol=1e-10)
    late=pd.Timestamp('2028-01-01',tz='UTC')
    data.loc[198,'effective_available_at']=late
    assert transform(data,days,name).effective_available_at.iloc[-1]==late
