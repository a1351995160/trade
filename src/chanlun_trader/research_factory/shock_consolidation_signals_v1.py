"""大涨后承接的两条事前固定假设；不将8%日涨幅称作实际涨停。"""
import pandas as pd

BREAK='LARGE_UP_INSIDE_BREAK_GATE_HOLD_3'
SUPPORT='LARGE_UP_THREE_DAY_SUPPORT_GATE_HOLD_3'
NAMES=(BREAK,SUPPORT)
FORMULAS={
    BREAK:'HFQ shock at t-2: C/Cprev-1>=0.08,C>O,H>L,(C-L)/(H-L)>=0.75,V>prior20meanV; t-1 L>=shock body midpoint,H<=shock H,V<shock V; t C>shock H,V>Vprev; market median RETURN5>0; score=-(C/shock H-1); fixed3',
    SUPPORT:'same HFQ shock at t-3; all t-2..t L>=shock body midpoint,C<=shock C,V<shock V; t C>O,C>Cprev; market median RETURN5>0; score=-shock body midpoint/C; fixed3',
}


def chosen(prices,name):
    c=prices.close;o=prices.open;h=prices.high;l=prices.low;v=prices.volume
    shock=(c/c.shift(1)-1>=.08)&(c>o)&(h>l)&((c-l)/(h-l)>=.75)
    shock&=v>v.shift(1).rolling(20,min_periods=20).mean()
    if name==BREAK:
        mid=(o.shift(2)+c.shift(2))/2
        event=shock.shift(2,fill_value=False)&(l.shift(1)>=mid)&(h.shift(1)<=h.shift(2))
        event&=(v.shift(1)<v.shift(2))&(c>h.shift(2))&(v>v.shift(1))
        score=-(c/h.shift(2)-1)
    elif name==SUPPORT:
        mid=(o.shift(3)+c.shift(3))/2
        event=shock.shift(3,fill_value=False)
        for lag in (2,1,0):
            event&=(l.shift(lag)>=mid)&(c.shift(lag)<=c.shift(3))&(v.shift(lag)<v.shift(3))
        event&=(c>o)&(c>c.shift(1))
        score=-mid/c
    else:raise ValueError('UNFROZEN_SHOCK_CONSOLIDATION')
    return pd.Series(1.,index=prices.index).where(~event,score)
