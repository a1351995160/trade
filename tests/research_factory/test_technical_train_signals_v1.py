import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.technical_train_signals_v1 import (
    FORMULAS, WARMUP, indicators, sealed_bottoms, transform, adjusted_rows, wilder_rsi, aroon,
)


def prices(n=280):
    x=np.arange(n)
    close=10+np.sin(x/3)+x/100
    return pd.DataFrame({'date':np.arange(n),'open':close,'high':close+1,'low':close-1,'close':close})


def test_rsi_wilder_seed_recursion_and_aroon_ties():
    close=pd.Series([100.,101.,100.,102.,100.,103.,100.,104.,100.,105.,100.,106.,100.,107.,100.,114.])
    rsi=wilder_rsi(close)
    assert rsi.iloc[:14].isna().all()
    assert rsi.iloc[14]==50
    assert rsi.iloc[15]==pytest.approx(100*(2*13/14+1)/((2*13/14+1)+(2*13/14)))
    assert wilder_rsi(pd.Series([100.]*20)).iloc[14:].eq(50).all()
    assert wilder_rsi(pd.Series(np.arange(20.))).iloc[14:].eq(100).all()
    assert wilder_rsi(pd.Series(-np.arange(20.))).iloc[14:].eq(0).all()
    up,down=aroon(pd.Series(np.arange(26.)),pd.Series(np.arange(26.)))
    assert up.iloc[25]==100 and down.iloc[25]==0
    up,down=aroon(pd.Series([10.]*26),pd.Series([9.]*26))
    assert up.iloc[25]==down.iloc[25]==100


def test_rsi_and_aroon_entry_events_by_hand():
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-08-01',periods=60)]
    def rows(close):
        close=np.asarray(close,dtype=float)
        return pd.DataFrame({'symbol':'000001.SZ','timestamp':days,'signal_open':close,
            'signal_close':close,'signal_high':close+1,'signal_low':close-1,
            'effective_available_at':pd.Timestamp('2022-01-01',tz='UTC')})
    close=list(np.arange(160.,101.,-1))+[120.]
    rsi=transform(rows(close),days,'RSI_14_RECLAIM_HOLD_20')
    # 前序每天跌1，最后从102涨18；平均涨18/14、平均跌13/14。
    assert rsi.value.iloc[-1]==pytest.approx(-18/31)
    up=transform(rows([100.]*59+[102.]),days,'AROON_25_CROSS_HOLD_20')
    assert up.value.iloc[-1]==pytest.approx(-.04)


def test_long_trend_short_pullback_and_warmup():
    close=np.array([100.]*170+list(np.arange(140.,109.,-1)))
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-08-01',periods=len(close))]
    rows=pd.DataFrame({'symbol':'000001.SZ','timestamp':days,'signal_open':close,
        'signal_close':close,'signal_high':close+1,'signal_low':close-1,
        'effective_available_at':pd.Timestamp('2022-01-01',tz='UTC')})
    result=transform(rows,days,'RSI2_TREND_200_HOLD_20')
    assert not result.computable.iloc[:199].any()
    assert close[-1]>close[-200:].mean()
    assert result.value.iloc[-1]==pytest.approx(wilder_rsi(pd.Series(close),2).iloc[-1]-5)
    assert result.value.iloc[-1]<0


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
    rows=p.assign(symbol='000001.SZ',timestamp=days,amount=100.,volume=100.,effective_available_at=pd.Timestamp('2022-01-01',tz='UTC'))
    rows=rows.assign(market_median=.01,market_available_at=rows.effective_available_at)
    if name in ('LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_3','HIGH_TURNOVER_WEEKLY_RECOVERY_HOLD_3','LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_20','AFFORDABLE_LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_20'):
        rows=rows.assign(turnover_rank=.2 if 'LOW_TURNOVER_' in name else .8)
    if name.startswith('AFFORDABLE_') or name.startswith('WEEKLY_OPENING_SELL_') or '_SCALE_' in name or name.startswith('WEEKLY_VOLUME_STABILITY_') or '_TREND60_FIXED_' in name or '_BREADTH_' in name or '_STOCK_TREND_ONLY_' in name or name.startswith('FAILED_LOW20_') or '_LOW_TOTAL_SKEW_' in name:rows=rows.assign(close=rows.signal_close)
    if '_SCALE_' in name:rows=rows.assign(scale_rank=.5)
    if '_BREADTH_' in name:rows=rows.assign(breadth=.7,breadth5=.65,breadth20=.6,breadth_available_at=rows.effective_available_at)
    out=transform(rows,days,name)
    changed=rows.copy()
    changed.loc[279,'signal_close']+=100
    pd.testing.assert_frame_equal(out.iloc[:279],transform(changed,days,name).iloc[:279])
    gap=transform(rows.drop(index=260),days,name)
    assert not gap.computable.iloc[260:260+WARMUP[name]].any()
    pd.testing.assert_frame_equal(gap.iloc[261:].reset_index(drop=True),
                                  transform(rows.iloc[261:],days[261:],name))
    expected_hold=3 if name in ('WEEKLY_ALPHA009_REGIME_GATE_HOLD_3','WEEKLY_ALPHA006_PULLBACK_GATE_HOLD_3','LARGE_UP_INSIDE_BREAK_GATE_HOLD_3','LARGE_UP_THREE_DAY_SUPPORT_GATE_HOLD_3','ALPHA003_PRESSURE_RECLAIM_GATE_HOLD_3','ALPHA040_VOLUME_PULLBACK_GATE_HOLD_3','LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_3','HIGH_TURNOVER_WEEKLY_RECOVERY_HOLD_3','MONTH_START_LOW_RISK_RECOVERY_HOLD_3','FAILED_LOW20_SAME_CLOSE_GATE_HOLD_3','FAILED_LOW20_NEXT_CLOSE_GATE_HOLD_3') else 20
    assert FixedAccountRules(contract(name)).contract['holding_sessions']==expected_hold
    assert design(name)['semantic_fingerprint']==FORMULAS[name]


def test_adjusted_signal_does_not_change_raw():
    rows=pd.DataFrame({'symbol':['000001.SZ'],'timestamp':[20220801]})
    raw=pd.DataFrame({'date':[20220801],'open':[9.],'high':[11.],'low':[8.],'close':[10.]})
    before=raw.copy(deep=True)
    result=adjusted_rows(rows,raw,[{'code':'sz.000001','date':'2022-08-01','close':'20'}])
    assert result.signal_open.iloc[0]==18
    assert result.signal_low.iloc[0]==16
    pd.testing.assert_frame_equal(raw,before)


def test_new_channels_hand_values_and_exclude_current_high():
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-08-01',periods=60)]
    def rows(close):
        return pd.DataFrame({'symbol':'000001.SZ','timestamp':days,
            'signal_open':close,'signal_close':close,'signal_high':np.asarray(close)+1,
            'signal_low':np.asarray(close)-1,
            'effective_available_at':pd.Timestamp('2022-01-01',tz='UTC')})
    still_below=transform(rows([100.]*58+[75.,85.]),days,'BOLL_REENTRY_HOLD_20')
    assert still_below.value.iloc[-1]>0
    close=[100.]*58+[75.,90.]
    result=transform(rows(close),days,'BOLL_REENTRY_HOLD_20')
    mean=np.mean(close[-20:]);std=np.std(close[-20:],ddof=0)
    assert result.value.iloc[-1]==pytest.approx((90-mean)/std)
    assert result.value.iloc[-2]>0
    flat=transform(rows([100.]*60),days,'BOLL_REENTRY_HOLD_20')
    assert not flat.value.lt(0).any()
    breakout=rows([100.]*59+[102.])
    result=transform(breakout,days,'DONCHIAN_55_BREAKOUT_HOLD_20')
    assert result.value.iloc[-1]==pytest.approx(1-102/101)
    breakout.loc[59,'signal_high']=1000.
    assert transform(breakout,days,'DONCHIAN_55_BREAKOUT_HOLD_20').value.iloc[-1]==result.value.iloc[-1]


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
