"""双均线与TRIX固定规则；不涉及执行或外部数据。"""
import pandas as pd
import numpy as np

FORMULAS={
    'HAMMER_DOWN_5_HOLD_20':'bull body>0, lower wick>=2*body, upper wick<=body, C[t-1]<C[t-6]; score=-lower_wick/range',
    'THREE_SOLDIERS_HOLD_20':'three bull candles with body/range>=0.5, higher closes and each later open inside previous body; score=1-C/C.shift(2)',
    'ATR_14_UP_BREAKOUT_HOLD_20':'C>previous C+1.5*previous WILDER_ATR14 and C>SMA50; score=-(C-previous C)/previous ATR',
    'INSIDE_BAR_UP_HOLD_20':'previous range inside its predecessor, C>previous H and C>SMA50; score=1-C/previous H',
    'SMA20_PULLBACK_200_HOLD_20':'C crosses SMA20 upward while C>SMA200; score=1-C/SMA20',
    'ROC20_ACCEL_HOLD_20':'ROC20>0 and daily change in ROC20 crosses zero upward; score=-ROC20',
    'SMA_50_200_CROSS_HOLD_20':'SMA50 crosses SMA200 upward; score=1-SMA50/SMA200',
    'TRIX_15_ZERO_CROSS_HOLD_20':'TRIX15=EMA15(EMA15(EMA15(C))).pct_change; crosses zero upward; score=-TRIX15',
}
WARMUP={'SMA_50_200_CROSS_HOLD_20':201,'TRIX_15_ZERO_CROSS_HOLD_20':130,
    'HAMMER_DOWN_5_HOLD_20':7,'THREE_SOLDIERS_HOLD_20':3,
    'ATR_14_UP_BREAKOUT_HOLD_20':50,'INSIDE_BAR_UP_HOLD_20':50,
    'SMA20_PULLBACK_200_HOLD_20':200,'ROC20_ACCEL_HOLD_20':23}


def atr(prices):
    previous=prices.close.shift(1)
    tr=pd.concat([prices.high-prices.low,(prices.high-previous).abs(),(prices.low-previous).abs()],axis=1).max(axis=1)
    result=pd.Series(np.nan,index=prices.index)
    if len(prices)<14:return result
    current=float(tr.iloc[:14].mean());result.iloc[13]=current
    for i in range(14,len(prices)):
        current=(current*13+tr.iloc[i])/14
        result.iloc[i]=current
    return result


def trix(close):
    average=close
    for _ in range(3):average=average.ewm(span=15,adjust=False).mean()
    return average.pct_change(fill_method=None)


def chosen(prices,name):
    score=pd.Series(1.,index=prices.index)
    c=prices.close;o=prices.open;h=prices.high;l=prices.low
    if name=='HAMMER_DOWN_5_HOLD_20':
        body=c-o;lower=o-l;upper=h-c
        event=(body>0)&(lower>=2*body)&(upper<=body)&(c.shift(1)<c.shift(6))
        return score.where(~event,-lower/(h-l))
    if name=='THREE_SOLDIERS_HOLD_20':
        bull=(c>o)&((c-o)>=.5*(h-l))
        event=bull & bull.shift(1,fill_value=False) & bull.shift(2,fill_value=False)
        event&=(c>c.shift(1))&(c.shift(1)>c.shift(2))
        event&=(o>=o.shift(1))&(o<=c.shift(1))&(o.shift(1)>=o.shift(2))&(o.shift(1)<=c.shift(2))
        return score.where(~event,1-c/c.shift(2))
    if name=='ATR_14_UP_BREAKOUT_HOLD_20':
        previous=atr(prices).shift(1)
        event=(previous>0)&(c>c.shift(1)+1.5*previous)&(c>c.rolling(50,min_periods=50).mean())
        return score.where(~event,-(c-c.shift(1))/previous)
    if name=='INSIDE_BAR_UP_HOLD_20':
        event=(h.shift(1)<=h.shift(2))&(l.shift(1)>=l.shift(2))&(c>h.shift(1))&(c>c.rolling(50,min_periods=50).mean())
        return score.where(~event,1-c/h.shift(1))
    if name=='SMA20_PULLBACK_200_HOLD_20':
        mean=c.rolling(20,min_periods=20).mean()
        event=(c>mean)&(c.shift(1)<=mean.shift(1))&(c>c.rolling(200,min_periods=200).mean())
        return score.where(~event,1-c/mean)
    if name=='ROC20_ACCEL_HOLD_20':
        roc=c/c.shift(20)-1;acceleration=roc.diff()
        event=(roc>0)&(acceleration>0)&(acceleration.shift(1)<=0)
        return score.where(~event,-roc)
    if name=='SMA_50_200_CROSS_HOLD_20':
        fast=prices.close.rolling(50,min_periods=50).mean()
        slow=prices.close.rolling(200,min_periods=200).mean()
        event=(fast>slow)&(fast.shift(1)<=slow.shift(1))
        return score.where(~event,1-fast/slow)
    if name=='TRIX_15_ZERO_CROSS_HOLD_20':
        value=trix(prices.close)
        event=(value>0)&(value.shift(1)<=0)
        return score.where(~event,-value)
    raise ValueError('UNFROZEN_SMOOTHING_SIGNAL')
