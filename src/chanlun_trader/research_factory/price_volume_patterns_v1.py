"""A股价量结构假设；非严格缠论买点，也不推断主力身份。"""
import pandas as pd

RETEST='RANGE_BREAK_RETEST_VOLUME_HOLD_20'
SPRING='RANGE_SPRING_LOW_VOLUME_HOLD_20'
NAMES=(RETEST,SPRING)
FORMULAS={
    RETEST:'SMA60>SMA60.shift(5), C>SMA60; t-2 C>MAX(H[t-22:t-3]); t-1 L>=that fixed upper, L<=C[t-2], C<C[t-2], V<V[t-2]; t C>H[t-1]; score=-(C/H[t-1]-1); fixed20 exit',
    SPRING:'SMA60>SMA60.shift(5), C>SMA60; t-1 L<MIN(L[t-21:t-2]), C>=that fixed lower, V<MEAN(V[t-21:t-2]); t C>H[t-1]; score=-(C/H[t-1]-1); fixed20 exit',
}


def chosen(prices,name):
    c=prices.close;h=prices.high;l=prices.low;v=prices.volume
    ma=c.rolling(60,min_periods=60).mean()
    event=(c>ma)&(ma>ma.shift(5))&(c>h.shift(1))
    if name==RETEST:
        upper=h.shift(3).rolling(20,min_periods=20).max()
        event&=(c.shift(2)>upper)&(l.shift(1)>=upper)&(l.shift(1)<=c.shift(2))
        event&=(c.shift(1)<c.shift(2))&(v.shift(1)<v.shift(2))
    elif name==SPRING:
        lower=l.shift(2).rolling(20,min_periods=20).min()
        average=v.shift(2).rolling(20,min_periods=20).mean()
        event&=(l.shift(1)<lower)&(c.shift(1)>=lower)&(v.shift(1)<average)
    else:raise ValueError('UNFROZEN_PRICE_VOLUME_PATTERN')
    return pd.Series(1.,index=prices.index).where(~event,-(c/h.shift(1)-1))
