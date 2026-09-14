import pandas as pd
import pytest

from chanlun_trader.research_factory.weekly_fixed_trial_v1 import NAMES,FIXED,WITH_MARKET
from chanlun_trader.research_factory.weekly_defensive_signals_v1 import LOW_VOL
from chanlun_trader.research_factory.technical_train_signals_v1 import transform
from chanlun_trader.research_factory.train_search_batch_v1 import contract


def test_fixed_entry_identical_and_market_does_not_advance_information():
    from test_weekly_defensive_signals_v1 import data
    days,p=data()
    rows=pd.DataFrame({'symbol':'000001.SZ','timestamp':days,'signal_close':p.close,
        'signal_open':p.close,'signal_high':p.close+.1,'signal_low':p.close-.1,
        'effective_available_at':pd.Timestamp('2022-01-01',tz='UTC'),
        'market_median':.01,'market_available_at':pd.Timestamp('2022-01-01',tz='UTC')})
    original=transform(rows,days,LOW_VOL);fixed=transform(rows,days,FIXED)
    pd.testing.assert_series_equal(original.value,fixed.value)
    assert 'exit_invalidated' not in fixed
    gated=transform(rows,days,WITH_MARKET)
    pd.testing.assert_series_equal(gated.value,fixed.value)
    rows['market_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    late=transform(rows,days,WITH_MARKET)
    assert late.effective_available_at[late.value<0].dt.year.eq(2028).all()
    rows['market_median']=0
    assert not transform(rows,days,WITH_MARKET).value.lt(0).any()
    rows['market_median']=float('nan')
    assert not transform(rows,days,WITH_MARKET).computable.any()


@pytest.mark.parametrize('name',NAMES)
def test_fixed_account_ignores_structure_data_and_keeps_original_governance(tmp_path,name):
    from test_structured_exit_trial_v1 import bundle
    from test_train_search_batch_v1 import test_governed_increment_repeat_and_revocation
    from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
    from chanlun_trader.research_factory.fixed_account_rules import FixedAccountRules
    b,days=bundle(name)
    assert not FixedAccountRules(contract(name)).structure_exit
    assert 'exit_policy' not in contract(name)
    result=_run_account(b,('SYNTHETIC',False),None,contract(name))
    sells=[f for f in result['fills'] if f['side']=='SELL']
    assert len(sells)==3
    assert {int(f['fill_time'].strftime('%Y%m%d')) for f in sells}=={days[22]}
    test_governed_increment_repeat_and_revocation(tmp_path,name)
