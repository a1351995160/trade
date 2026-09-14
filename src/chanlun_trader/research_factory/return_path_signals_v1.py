"""周度选择的收益路径假设；过去观察不是可提前取得的账户收益。"""
import numpy as np
import pandas as pd

NIGHT='WEEKLY_OVERNIGHT_SUPPORT_HOLD_20'
GRADUAL='WEEKLY_GRADUAL_ADVANCE_HOLD_20'
NAMES=(NIGHT,GRADUAL)
FORMULAS={
    NIGHT:'Calendar week-end; HFQ C>SMA60 and C/C[-60]>1; sum20 log(O/Cprev)>0 and sum20 log(C/O)<=0; score=-sum20 overnight/(1+STD_POP20 daily return); fixed20',
    GRADUAL:'Calendar week-end; HFQ C>SMA60 and C/C[-60]>1; positive days60>negative days60; score=-positive_fraction60*(1-top5_positive_log_share60)/(1+STD_POP20 daily return); fixed20',
}


def components(prices):
    return np.log(prices.open/prices.close.shift(1)),np.log(prices.close/prices.open)


def chosen(prices,name,sessions):
    if name not in NAMES:raise ValueError('UNFROZEN_RETURN_PATH')
    c=prices.close
    weeks=[pd.Timestamp(str(d)).isocalendar()[:2] for d in sessions]
    ends={d for i,d in enumerate(sessions[:-1]) if weeks[i]!=weeks[i+1]}
    total=np.log(c/c.shift(1))
    sigma=c.pct_change(fill_method=None).rolling(20,min_periods=20).std(ddof=0)
    event=prices.date.isin(ends)&c.gt(c.rolling(60,min_periods=60).mean())&c.gt(c.shift(60))
    if name==NIGHT:
        night,day=components(prices)
        n=night.rolling(20,min_periods=20).sum();d=day.rolling(20,min_periods=20).sum()
        event=event&n.gt(0)&d.le(0)
        score=-n/(1+sigma)
    else:
        positive=total.clip(lower=0)
        gains=positive.rolling(60,min_periods=60).sum()
        largest=positive.rolling(60,min_periods=60).apply(lambda a:np.partition(a,-5)[-5:].sum(),raw=True)
        up=total.gt(0).where(total.notna()).rolling(60,min_periods=60).sum()
        down=total.lt(0).where(total.notna()).rolling(60,min_periods=60).sum()
        event=event&up.gt(down)&gains.gt(0)
        score=-(up/60)*(1-largest/gains.where(gains>0))/(1+sigma)
    return score.where(event,1.).where(np.arange(len(prices))>=60)
