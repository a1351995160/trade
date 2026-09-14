import numpy as np
import pandas as pd
import pytest
from chanlun_trader.research_factory.scale_proxy_signals_v1 import NAMES,SMALL,context,chosen


def fixture():
    symbols=[f'{i:06d}.SZ' for i in range(100)]
    ready=pd.DataFrame({'symbol':symbols,'timestamp':20230106,'effective_available_at':pd.Timestamp('2023-01-06 07:00',tz='UTC')})
    daily=pd.DataFrame({'symbol':symbols,'date':20230106,'close':10.,'volume':np.arange(1,101)*1000.})
    turns=pd.DataFrame({'symbol':symbols,'timestamp':20230106,'turn':1.})
    return ready,daily,turns


def test_rank_common_units_no_unknown_zero_and_peer_time():
    r,d,t=fixture();out=context(r,d,t)
    assert out.scale_rank.tolist()==pytest.approx(np.arange(1,101)/100)
    pd.testing.assert_frame_equal(out,context(r,d,t.assign(turn=100.)))
    # 百分数与比例统一倍数不改变排序，不据此声称实际市值。
    late=r.copy();late.loc[0,'effective_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    assert context(late,d,t).effective_available_at.dt.year.eq(2028).all()
    t.loc[0,'turn']=0
    assert context(r,d,t).scale_rank.isna().all()  # 只剩99个，不偷偷改100门槛。
    t.loc[0,'turn']=np.nan
    assert context(r,d,t).scale_rank.isna().all()
    assert len(context(r,d,t))==100
    t.loc[0,'turn']=-1
    with pytest.raises(ValueError,match='INVALID'):context(r,d,t)
    with pytest.raises(ValueError,match='DUPLICATE'):context(pd.concat([r,r.iloc[:1]]),d,t)


@pytest.mark.parametrize('name',NAMES)
def test_price_rank_rule_affordability_and_original_account(name,tmp_path):
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-07-01',periods=70)]
    c=100+np.arange(70)*.1
    f=pd.DataFrame({'date':days,'open':c,'close':c,'high':c+1,'low':c-1,'raw_close':10.,'scale_rank':.5})
    out=chosen(f,name,days);i=65
    assert pd.Timestamp(str(days[i])).dayofweek==4
    expected=-1/1.5 if name==SMALL else -1/(1+f.close.pct_change(fill_method=None).iloc[i-19:i+1].std(ddof=0))
    assert out.iloc[i]==pytest.approx(expected)
    f.loc[i,'raw_close']=34
    assert chosen(f,name,days).iloc[i]==1
    f.loc[i,'raw_close']=10;f.loc[i,'scale_rank']=.3
    if name!=SMALL:assert chosen(f,name,days).iloc[i]==1
    f.loc[i,'scale_rank']=np.nan
    assert pd.isna(chosen(f,name,days).iloc[i])
    from test_return_path_signals_v1 import test_actual_account_fixed_exit_and_budget
    test_actual_account_fixed_exit_and_budget(name,tmp_path)
