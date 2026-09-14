import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.weekly_defensive_signals_v1 import chosen,NAMES,LOW_VOL,MOM_RISK


def data():
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-01-03',periods=300)]
    c=100+np.arange(300)*.2+np.sin(np.arange(300))*.1
    return days,pd.DataFrame({'date':days,'close':c})


@pytest.mark.parametrize('name',NAMES)
def test_weekly_only_and_true_past_prefix(name):
    days,p=data();out=chosen(p,name,days)
    assert out.lt(0).any()
    for i in np.flatnonzero(out.lt(0)):
        assert i>=199 and pd.Timestamp(str(days[i])).isocalendar()[:2]!=pd.Timestamp(str(days[i+1])).isocalendar()[:2]
    pd.testing.assert_series_equal(out.iloc[:260],chosen(p.iloc[:260],name,days))
    assert not chosen(p.assign(close=100.),name,days).lt(0).any()
    assert not chosen(p.assign(close=200-np.arange(300)*.1),name,days).lt(0).any()


def test_hand_ranking_uses_daily_returns_and_not_overlapping_five_day_returns():
    days,p=data()
    index=next(i for i in range(220,299) if pd.Timestamp(str(days[i])).isocalendar()[:2]!=pd.Timestamp(str(days[i+1])).isocalendar()[:2])
    c=p.close.to_numpy();daily=c[1:]/c[:-1]-1
    assert chosen(p,LOW_VOL,days).iloc[index]==pytest.approx(-1/(1+np.std(daily[index-20:index])))
    assert chosen(p,MOM_RISK,days).iloc[index]==pytest.approx(-(c[index]/c[index-60]-1)/np.std(daily[index-60:index]))


@pytest.mark.parametrize('name',NAMES)
def test_calendar_holiday_and_daily_exit_with_gap_reset(name):
    from chanlun_trader.research_factory.technical_train_signals_v1 import transform
    days,p=data()
    # 日历明确移除周五，则周四是该周最后session，不按证券条数取样。
    friday=next(d for d in days[220:240] if pd.Timestamp(str(d)).weekday()==4)
    i=days.index(friday);thursday=days[i-1]
    days.remove(friday);p=p[p.date!=friday].reset_index(drop=True)
    assert chosen(p,name,days).loc[p.date==thursday].iloc[0]<0
    rows=pd.DataFrame({'symbol':'000001.SZ','timestamp':days,'signal_close':p.close,
        'signal_open':p.close,'signal_high':p.close+.1,'signal_low':p.close-.1,
        'effective_available_at':pd.Timestamp('2022-01-01',tz='UTC')})
    out=transform(rows,days,name)
    assert out.exit_invalidated.iloc[199:].notna().all()
    assert out.value.iloc[-1]==1  # 日历末端不能确认下一个周边界。
    missing=transform(rows.drop(index=230),days,name)
    assert not missing.computable.iloc[230:].any()
    late=rows.copy();late.loc[240,'effective_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    out=transform(late,days,name)
    assert out.exit_available_at.iloc[250].year==2028


@pytest.mark.parametrize('name',NAMES)
def test_original_account_exit_and_governed_increment(tmp_path,name):
    from test_structured_exit_trial_v1 import bundle
    from test_train_search_batch_v1 import test_governed_increment_repeat_and_revocation
    from chanlun_trader.research_factory.train_search_batch_v1 import contract
    from chanlun_trader.research_factory.degraded_execution_v2 import _run_account
    b,days=bundle(name)
    result=_run_account(b,('SYNTHETIC',False),None,contract(name))
    sells=[f for f in result['fills'] if f['side']=='SELL']
    assert len(sells)==3
    assert {int(f['fill_time'].strftime('%Y%m%d')) for f in sells}=={days[5]}
    assert result['metrics']['ending_equity']==pytest.approx(10000-30-18-4.4955)
    test_governed_increment_repeat_and_revocation(tmp_path,name)
