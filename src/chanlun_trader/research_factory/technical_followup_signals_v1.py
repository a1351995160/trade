"""公开定义启发的固定CCI/KAMA假设；不访问数据或结果。"""
import numpy as np
import pandas as pd

FORMULAS={
    'CCI_20_TREND_HOLD_20':'CCI20=(TP-SMA20(TP))/(0.015*MEAN_ABS_DEV20(TP)); crosses +100 upward; score=-CCI/100',
    'KAMA_10_CROSS_HOLD_20':'KAMA(ER10,fast2,slow30), SMA10 seed; C crosses KAMA upward and KAMA rising; score=1-C/KAMA',
}
WARMUP={'CCI_20_TREND_HOLD_20':21,'KAMA_10_CROSS_HOLD_20':31}


def cci(prices):
    tp=(prices.high+prices.low+prices.close)/3
    mean=tp.rolling(20,min_periods=20).mean()
    dev=tp.rolling(20,min_periods=20).apply(lambda x:np.abs(x-x.mean()).mean(),raw=True)
    return ((tp-mean)/(.015*dev)).where(dev>0)


def kama(close):
    result=pd.Series(np.nan,index=close.index)
    if len(close)<10:return result
    current=float(close.iloc[:10].mean())
    result.iloc[9]=current
    variation=close.diff().abs().rolling(10,min_periods=10).sum()
    for i in range(10,len(close)):
        er=abs(close.iloc[i]-close.iloc[i-10])/variation.iloc[i] if variation.iloc[i]>0 else 0.
        alpha=(er*(2/3-2/31)+2/31)**2
        current+=alpha*(close.iloc[i]-current)
        result.iloc[i]=current
    return result


def chosen(prices,name):
    score=pd.Series(1.,index=prices.index)
    if name=='CCI_20_TREND_HOLD_20':
        value=cci(prices)
        event=(value>100)&(value.shift(1)<=100)
        return score.where(~event,-value/100)
    if name=='KAMA_10_CROSS_HOLD_20':
        value=kama(prices.close)
        event=(prices.close>value)&(prices.close.shift(1)<=value.shift(1))&(value>value.shift(1))
        return score.where(~event,1-prices.close/value)
    raise ValueError('UNFROZEN_FOLLOWUP_SIGNAL')
