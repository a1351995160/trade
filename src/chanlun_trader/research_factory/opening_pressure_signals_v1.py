"""高开后日内卖压的频率/幅度假设；只消费已完成历史bar。"""
import numpy as np
import pandas as pd
from .affordable_portfolio_signals_v1 import POLICY

FREQUENCY='WEEKLY_OPENING_SELL_FREQUENCY_HOLD_20'
MAGNITUDE='WEEKLY_OPENING_SELL_MAGNITUDE_HOLD_20'
NAMES=(FREQUENCY,MAGNITUDE)
FORMULAS={
    FREQUENCY:'Calendar week-end; NR=(HFQ open>prior HFQ close AND close<open); score=-mean20(NR)/(1+STD_POP20 daily HFQ return); market median RETURN5>0; signal-day RAW 100-share original-cost estimate<=10000/3; fixed20',
    MAGNITUDE:'Calendar week-end; NR=(HFQ open>prior HFQ close AND close<open); score=-mean20((1-close/open)*NR)/(1+STD_POP20 daily HFQ return); market median RETURN5>0; signal-day RAW 100-share original-cost estimate<=10000/3; fixed20',
}


def chosen(prices,name,sessions):
    if name not in NAMES:raise ValueError('UNFROZEN_OPENING_PRESSURE')
    c,o=prices.close,prices.open
    overnight=o/c.shift(1)-1;day=c/o-1
    known=np.isfinite(overnight)&np.isfinite(day)
    event=overnight.gt(0)&day.lt(0)
    observation=event.astype(float) if name==FREQUENCY else (-day).where(event,0.)
    pressure=observation.where(known).rolling(20,min_periods=20).mean()
    sigma=c.pct_change(fill_method=None).rolling(20,min_periods=20).std(ddof=0)
    weeks=[pd.Timestamp(str(d)).isocalendar()[:2] for d in sessions]
    ends={d for i,d in enumerate(sessions[:-1]) if weeks[i]!=weeks[i+1]}
    raw=prices.raw_close
    notional=raw*(1+POLICY['slippage_fraction'])*POLICY['lot_size']
    cost=notional+np.maximum(POLICY['min_commission'],notional*POLICY['commission_rate'])
    visible=np.isfinite(raw)&raw.gt(0)&pressure.notna()&sigma.notna()
    buy=prices.date.isin(ends)&pressure.gt(0)&cost.le(POLICY['reference_cash']/POLICY['slots'])
    return (-pressure/(1+sigma)).where(buy,1.).where(visible)
