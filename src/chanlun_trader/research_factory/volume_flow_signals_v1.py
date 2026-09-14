"""第22批：量价方向与区间内收盘位置的不同确认机制。"""
import numpy as np
import pandas as pd

OBV='OBV20_TREND_RECOVERY_HOLD_20'
RANGE='AD20_CONTRACTION_BREAK_HOLD_20'
NAMES=(OBV,RANGE)
FORMULAS={
    OBV:'C>SMA60>SMA60.shift(5); C crosses SMA5 upward; F=SUM20(sign(delta C)*V)/SUM20(V)>0; score=-F; fixed20',
    RANGE:'C>SMA60; prior20 range ratio < preceding20 range ratio; C crosses prior5 HIGH upward; A=SUM20(V*(2*C-H-L)/(H-L))/SUM20(V)>0; score=-A; fixed20',
}


def chosen(prices,name):
    c=prices.close;h=prices.high;l=prices.low;v=prices.volume
    ma=c.rolling(60,min_periods=60).mean()
    denominator=v.rolling(20,min_periods=20).sum()
    if name==OBV:
        flow=(np.sign(c.diff())*v).rolling(20,min_periods=20).sum()/denominator
        fast=c.rolling(5,min_periods=5).mean()
        event=(c>ma)&(ma>ma.shift(5))&(c>fast)&(c.shift(1)<=fast.shift(1))&(flow>0)
    elif name==RANGE:
        flow=(v*(2*c-h-l)/(h-l)).rolling(20,min_periods=20).sum()/denominator
        width=h.shift(1).rolling(20,min_periods=20).max()/l.shift(1).rolling(20,min_periods=20).min()-1
        upper=h.shift(1).rolling(5,min_periods=5).max()
        event=(c>ma)&(width<width.shift(20))&(c>upper)&(c.shift(1)<=upper.shift(1))&(flow>0)
    else:
        raise ValueError('UNFROZEN_VOLUME_FLOW_SIGNAL')
    return pd.Series(1.,index=prices.index).where(~event,-flow)
