"""既有低波动家族的明确期限变体与路径回撤排序；不清除父试验曝光。"""
import numpy as np
import pandas as pd
from .affordable_portfolio_signals_v1 import POLICY

LOW_VOL='WEEKLY_LOW_VOL_TREND60_FIXED_HOLD_20'
LOW_DRAWDOWN='WEEKLY_LOW_DRAWDOWN_TREND60_FIXED_HOLD_20'
NAMES=(LOW_VOL,LOW_DRAWDOWN)
FORMULAS={
    LOW_VOL:'Registered horizon variant of AFFORDABLE_WEEKLY_LOW_VOL_MARKET_HOLD_20: SMA200 replaced by SMA60, positive60 momentum retained; weekly; score=-1/(1+STD_POP20 daily HFQ return); fixed20; original market/affordability',
    LOW_DRAWDOWN:'Same weekly SMA60/positive60 momentum/market/affordability; rank -1/(1+MAX_DRAWDOWN_IN_LAST20_HFQ_CLOSES), running peak restarts at first close of each fixed20 window; fixed20',
}


def chosen(prices,name,sessions):
    if name not in NAMES:raise ValueError('UNFROZEN_TREND_RISK_HORIZON')
    c=prices.close
    weeks=[pd.Timestamp(str(d)).isocalendar()[:2] for d in sessions]
    ends={d for i,d in enumerate(sessions[:-1]) if weeks[i]!=weeks[i+1]}
    event=prices.date.isin(ends)&c.gt(c.rolling(60,min_periods=60).mean())&c.gt(c.shift(60))
    if name==LOW_VOL:risk=c.pct_change(fill_method=None).rolling(20,min_periods=20).std(ddof=0)
    else:risk=c.rolling(20,min_periods=20).apply(lambda x:np.max(1-x/np.maximum.accumulate(x)),raw=True)
    notional=prices.raw_close*(1+POLICY['slippage_fraction'])*POLICY['lot_size']
    cost=notional+np.maximum(POLICY['min_commission'],notional*POLICY['commission_rate'])
    event&=cost.le(POLICY['reference_cash']/POLICY['slots'])
    known=np.isfinite(risk)&np.isfinite(prices.raw_close)&prices.raw_close.gt(0)
    return (-1/(1+risk)).where(event,1.).where(known)
