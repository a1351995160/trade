"""聚宽Alpha191(003/040)启发的固定承接假设；归一化与入场均为本地改编。"""
import numpy as np
import pandas as pd

PRICE='ALPHA003_PRESSURE_RECLAIM_GATE_HOLD_3'
VOLUME='ALPHA040_VOLUME_PULLBACK_GATE_HOLD_3'
NAMES=(PRICE,VOLUME)
FORMULAS={
    PRICE:'C>SMA60,C<C[-5], market median RETURN5>0; D=0 if C=Cprev else C-min(L,Cprev) if C>Cprev else C-max(H,Cprev); SUM6(D)>0,D[t]>0; score=-SUM6(D)/SUM6(TR); fixed3',
    VOLUME:'C>SMA60,C<C[-5],C>Cprev, market median RETURN5>0; U=SUM26(V if C>Cprev else0),D=SUM26(V if C<=Cprev else0); D>0,U/D>1; score=-U/D; fixed3',
}


def components(prices,name):
    c=prices.close;prev=c.shift(1)
    if name==PRICE:
        lower=pd.concat([prices.low,prev],axis=1).min(axis=1)
        upper=pd.concat([prices.high,prev],axis=1).max(axis=1)
        pressure=(c-lower).where(c>prev,c-upper).where(c!=prev,0.).where(prev.notna())
        tr=pd.concat([prices.high-prices.low,(prices.high-prev).abs(),(prices.low-prev).abs()],axis=1).max(axis=1).where(prev.notna())
        numerator=pressure.rolling(6,min_periods=6).sum()
        denominator=tr.rolling(6,min_periods=6).sum()
        confirm=pressure>0
    elif name==VOLUME:
        v=prices.volume
        numerator=v.where(c>prev,0.).where(prev.notna()).rolling(26,min_periods=26).sum()
        denominator=v.where(c<=prev,0.).where(prev.notna()).rolling(26,min_periods=26).sum()
        confirm=(c>prev)&(numerator>denominator)
    else:raise ValueError('UNFROZEN_ALPHA191_PRESSURE')
    return numerator,denominator,confirm


def chosen(prices,name):
    numerator,denominator,confirm=components(prices,name)
    c=prices.close
    event=(c>c.rolling(60,min_periods=60).mean())&(c<c.shift(5))&confirm&(numerator>0)
    score=-numerator/denominator.where(denominator>0)
    return score.where(event,1.).where(np.isfinite(score))
