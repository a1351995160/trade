"""成交参与稳定性与下行风险组合；固定事前公式，非论文原样复现。"""
import numpy as np
from .affordable_portfolio_signals_v1 import POLICY

STABLE='WEEKLY_VOLUME_STABILITY_TREND_HOLD_20'
DEFENSIVE='WEEKLY_VOLUME_STABILITY_DOWNSIDE_HOLD_20'
NAMES=(STABLE,DEFENSIVE)
FORMULAS={
    STABLE:'Calendar week-end; HFQ C>SMA60 and C/C[-60]>1; market median RETURN5>0; rank -1/(1+STD_POP20(RAW volume)/MEAN20(RAW volume)); original signal-day ticket budget; fixed20',
    DEFENSIVE:'Same weekly trend/market/affordability; rank -1/(1+CV20(RAW volume)+RMS20(min(HFQ daily return,0))/RMS20(HFQ daily return)); zero RMS unknown; fixed20',
}


def chosen(prices,name,sessions):
    import pandas as pd
    if name not in NAMES:raise ValueError('UNFROZEN_PARTICIPATION_STABILITY')
    c=prices.close;v=prices.volume.where(np.isfinite(prices.volume)&prices.volume.gt(0))
    cv=v.rolling(20,min_periods=20).std(ddof=0)/v.rolling(20,min_periods=20).mean()
    risk=cv.copy()
    if name==DEFENSIVE:
        r=c.pct_change(fill_method=None)
        denominator=r.pow(2).rolling(20,min_periods=20).mean().pow(.5)
        downside=r.clip(upper=0).pow(2).rolling(20,min_periods=20).mean().pow(.5)
        risk+=downside/denominator.where(denominator.gt(0))
    weeks=[pd.Timestamp(str(d)).isocalendar()[:2] for d in sessions]
    ends={d for i,d in enumerate(sessions[:-1]) if weeks[i]!=weeks[i+1]}
    event=prices.date.isin(ends)&c.gt(c.rolling(60,min_periods=60).mean())&c.gt(c.shift(60))
    notional=prices.raw_close*(1+POLICY['slippage_fraction'])*POLICY['lot_size']
    cost=notional+np.maximum(POLICY['min_commission'],notional*POLICY['commission_rate'])
    event&=cost.le(POLICY['reference_cash']/POLICY['slots'])
    known=np.isfinite(risk)&np.isfinite(prices.raw_close)&prices.raw_close.gt(0)
    return (-1/(1+risk)).where(event,1.).where(known)
