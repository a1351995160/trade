import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.technical_train_signals_v1 import (
    FORMULAS, indicators, sealed_bottoms, transform, adjusted_rows,
)


def prices(n=280):
    x=np.arange(n)
    close=10+np.sin(x/3)+x/100
    return pd.DataFrame({'date':np.arange(n),'open':close,'high':close+1,'low':close-1,'close':close})


def test_macd_and_kdj_hand_recurrence():
    p=prices(10)
    p.loc[:,'close']=10.
    p.loc[:,'high']=12.
    p.loc[:,'low']=8.
    p.loc[8,'close']=12.
    out=indicators(p)
    assert out.k.iloc[:8].isna().all()
    assert out.k.iloc[8]==pytest.approx(200/3)
    assert out.d.iloc[8]==pytest.approx((100+200/3)/3)
    assert out.dif.iloc[8]==pytest.approx(2*(2/13-2/27))
    flat=p.assign(high=10.,low=10.,close=10.)
    assert indicators(flat).k.isna().all()


def test_sealed_fractal_prefix_invariance():
    p=prices(100)
    # 加入包含关系，未来数据不能修改已发布信号。
    p.loc[30:35,['high','low']]=[15.,5.]
    full=sealed_bottoms(p)
    assert full.any()
    for end in range(4,len(p)+1):
        np.testing.assert_array_equal(full[:end],sealed_bottoms(p.iloc[:end]))


@pytest.mark.parametrize('name',list(FORMULAS))
def test_signal_prefix_gap_and_original_rules(name):
    from chanlun_trader.research_factory.train_search_batch_v1 import contract,design
    from chanlun_trader.research_factory.fixed_account_rules import FixedAccountRules
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-08-01',periods=280)]
    p=prices().rename(columns={f:'signal_'+f for f in ['open','high','low','close']})
    rows=p.assign(symbol='000001.SZ',timestamp=days,effective_available_at=pd.Timestamp('2022-01-01',tz='UTC'))
    out=transform(rows,days,name)
    changed=rows.copy()
    changed.loc[279,'signal_close']+=100
    pd.testing.assert_frame_equal(out.iloc[:279],transform(changed,days,name).iloc[:279])
    gap=transform(rows.drop(index=260),days,name)
    assert not gap.computable.iloc[260:].any()
    assert FixedAccountRules(contract(name)).contract['holding_sessions']==20
    assert design(name)['semantic_fingerprint']==FORMULAS[name]


def test_adjusted_signal_does_not_change_raw():
    rows=pd.DataFrame({'symbol':['000001.SZ'],'timestamp':[20220801]})
    raw=pd.DataFrame({'date':[20220801],'open':[9.],'high':[11.],'low':[8.],'close':[10.]})
    before=raw.copy(deep=True)
    result=adjusted_rows(rows,raw,[{'code':'sz.000001','date':'2022-08-01','close':'20'}])
    assert result.signal_open.iloc[0]==18
    assert result.signal_low.iloc[0]==16
    pd.testing.assert_frame_equal(raw,before)


def test_month_end_high_position_and_maximum_return_by_hand():
    dates=pd.bdate_range('2022-08-01',periods=280)
    days=[int(d.strftime('%Y%m%d')) for d in dates]
    c=10*1.001**np.arange(280)
    rows=pd.DataFrame({'symbol':'000001.SZ','timestamp':days,
        'signal_open':c,'signal_high':c*1.01,'signal_low':c*.99,'signal_close':c,
        'effective_available_at':pd.Timestamp('2022-01-01',tz='UTC')})
    month=transform(rows,days,'MONTHLY_REVERSAL_HOLD_20')
    at=days.index(20220831)
    assert month.value.iloc[at]==pytest.approx(1.001**20-1)
    assert month.value.iloc[at+1]==1
    high=transform(rows,days,'HIGH_252_HOLD_20')
    assert high.value.iloc[:251].isna().all()
    assert high.value.iloc[251]==pytest.approx(-1)
    maximum=transform(rows,days,'LOW_MAX_20_HOLD_20')
    assert maximum.value.iloc[:20].isna().all()
    assert maximum.value.iloc[20]==pytest.approx(-1/1.001)
