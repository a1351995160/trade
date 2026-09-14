"""日收益总体偏度排序；不称特质偏度或高频实现偏度。"""
import numpy as np
from .affordable_portfolio_signals_v1 import POLICY

NAME='WEEKLY_LOW_TOTAL_SKEW_TREND_GATE_HOLD_20'
NAMES=(NAME,)
FORMULAS={NAME:'Calendar week-end, HFQ C>SMA60 and C>C[-60]; total skew20=MEAN((daily_return-mean20)^3)/MEAN((daily_return-mean20)^2)^1.5, zero variance UNKNOWN; score=-1/(1+exp(skew20)); original market median RETURN5>0; RAW signal-day original-cost ticket<=10000/3; next open fixed20'}


def total_skew(values):
    centered=values-values.mean()
    variance=np.mean(centered**2)
    return np.mean(centered**3)/variance**1.5 if variance>0 else np.nan


def chosen(prices,name,sessions):
    import pandas as pd
    if name!=NAME:raise ValueError('UNFROZEN_LOW_SKEW')
    c=prices.close
    skew=c.pct_change(fill_method=None).rolling(20,min_periods=20).apply(total_skew,raw=True)
    mean=c.rolling(60,min_periods=60).mean()
    weeks=[pd.Timestamp(str(d)).isocalendar()[:2] for d in sessions]
    ends={d for i,d in enumerate(sessions[:-1]) if weeks[i]!=weeks[i+1]}
    raw=prices.raw_close
    notional=raw*(1+POLICY['slippage_fraction'])*POLICY['lot_size']
    cost=notional+np.maximum(POLICY['min_commission'],notional*POLICY['commission_rate'])
    visible=skew.notna()&mean.notna()&c.shift(60).notna()&np.isfinite(raw)&raw.gt(0)
    event=prices.date.isin(ends)&c.gt(mean)&c.gt(c.shift(60))&cost.le(POLICY['reference_cash']/POLICY['slots'])
    return (-1/(1+np.exp(skew))).where(event,1.).where(visible)
