"""Skill Alpha101启发的短周期改编；非原论文组合复现。"""
import numpy as np
import pandas as pd

SWITCH='WEEKLY_ALPHA009_REGIME_GATE_HOLD_3'
DIVERGENCE='WEEKLY_ALPHA006_PULLBACK_GATE_HOLD_3'
NAMES=(SWITCH,DIVERGENCE)
FORMULAS={
    SWITCH:'HFQ delta=C-Cprev; a=delta if MIN5(delta)>0 or MAX5(delta)<0 else -delta; week-end and C>SMA60; score=-a/Cprev; original median RETURN5>0 gate; fixed3',
    DIVERGENCE:'alpha006=-CORR10(HFQ open,RAW volume); week-end, C>SMA60, C<C[-5], corr<0; score=corr; original median RETURN5>0 gate; fixed3',
}


def alpha009(close):
    delta=close.diff()
    monotone=delta.rolling(5,min_periods=5).min().gt(0)|delta.rolling(5,min_periods=5).max().lt(0)
    return delta.where(monotone,-delta).where(delta.rolling(5,min_periods=5).count().eq(5))


def chosen(prices,name,sessions):
    if name not in NAMES:raise ValueError('UNFROZEN_SKILL_ALPHA')
    c=prices.close
    weeks=[pd.Timestamp(str(d)).isocalendar()[:2] for d in sessions]
    ends={d for i,d in enumerate(sessions[:-1]) if weeks[i]!=weeks[i+1]}
    event=prices.date.isin(ends)&c.gt(c.rolling(60,min_periods=60).mean())
    if name==SWITCH:
        score=-alpha009(c)/c.shift(1)
    else:
        corr=prices.open.rolling(10,min_periods=10).corr(prices.volume)
        known=prices.open.rolling(10,min_periods=10).var(ddof=0).gt(0)&prices.volume.rolling(10,min_periods=10).var(ddof=0).gt(0)
        score=corr.where(known)
        event=event&c.lt(c.shift(5))&score.lt(0)
    return score.where(event,1.).where(np.isfinite(score)&(np.arange(len(prices))>=60))
