import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.affordable_portfolio_signals_v1 import NAMES,PARENTS,POLICY
from chanlun_trader.research_factory.technical_train_signals_v1 import transform
from chanlun_trader.research_factory.train_search_batch_v1 import contract


def fixture():
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-07-01',periods=260)]
    close=100+np.arange(len(days))*.1
    rows=pd.DataFrame({'symbol':'000001.SZ','timestamp':days,'signal_open':close,'signal_high':close+1,
        'signal_low':close-1,'signal_close':close,'close':10.,'effective_available_at':pd.Timestamp('2022-01-01',tz='UTC'),
        'turnover_rank':.2,'market_median':.01,'market_available_at':pd.Timestamp('2022-01-01',tz='UTC')})
    return rows,days


@pytest.mark.parametrize('name',NAMES)
def test_raw_ticket_gate_parent_parity_and_no_future(name):
    rows,days=fixture();out=transform(rows,days,name);parent=transform(rows,days,PARENTS[name])
    assert out.value.lt(0).any()
    for col in ('value','computable','effective_available_at'):pd.testing.assert_series_equal(out[col],parent[col])
    last=int(out.index[out.value.lt(0)][-1])
    costly=rows.copy();costly.loc[last,'close']=34
    assert transform(costly,days,name).value.iloc[last]==1
    assert transform(costly,days,PARENTS[name]).value.iloc[last]<0
    costly.loc[last,'close']=np.nan
    assert not transform(costly,days,name).computable.iloc[last]
    costly.loc[last,'close']=33
    assert transform(costly,days,name).value.iloc[last]<0
    costly.loc[last+1:,'close']=1000
    pd.testing.assert_frame_equal(out.iloc[:last+1],transform(costly,days,name).iloc[:last+1])
    assert contract(name)['parent_candidate']==PARENTS[name]
    assert contract(name)['signal_day_affordability']==POLICY
    assert contract(name)['initial_cash']==10000


@pytest.mark.parametrize('name',NAMES)
def test_original_account_costs_exit_and_canonical_budget(name,tmp_path):
    from test_return_path_signals_v1 import test_actual_account_fixed_exit_and_budget
    test_actual_account_fixed_exit_and_budget(name,tmp_path)
