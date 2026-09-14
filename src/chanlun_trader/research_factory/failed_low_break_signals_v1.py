"""前20低点假跌破：两条固定日线收回事件，不模拟盘中止损单。"""
import numpy as np
import pandas as pd
from .affordable_portfolio_signals_v1 import POLICY

SAME='FAILED_LOW20_SAME_CLOSE_GATE_HOLD_3'
NEXT='FAILED_LOW20_NEXT_CLOSE_GATE_HOLD_3'
NAMES=(SAME,NEXT)
FORMULAS={
    SAME:'HFQ prior20 low L, most recent occurrence age>=4; today low<L and close>L; score=-(close-low)/(high-low); original market median RETURN5>0, RAW signal-day original-cost ticket<=10000/3; next open fixed3',
    NEXT:'At yesterday s: HFQ prior20 low L with most recent age>=4, low[s]<L and close[s]<=L; today low>=low[s] and close>L; score=-(close-low)/(high-low); original market median RETURN5>0, RAW signal-day original-cost ticket<=10000/3; next open fixed3',
}


def chosen(prices,name):
    if name not in NAMES:raise ValueError('UNFROZEN_FAILED_LOW_BREAK')
    c,h,l=prices.close,prices.high,prices.low
    prior=l.shift(1).rolling(20,min_periods=20)
    level=prior.min()
    # 最后一次并列低点决定年龄，不能用较早的并列记录伪造间隔。
    age=prior.apply(lambda a:20-np.flatnonzero(a==a.min())[-1],raw=True)
    setup=l.lt(level)&age.ge(4)
    known=level.notna()&age.notna()
    if name==SAME:
        event=setup&c.gt(level)
    else:
        event=(setup&c.le(level)).shift(1,fill_value=False)&l.ge(l.shift(1))&c.gt(level.shift(1))
        known=known.shift(1,fill_value=False)
    raw=prices.raw_close
    notional=raw*(1+POLICY['slippage_fraction'])*POLICY['lot_size']
    cost=notional+np.maximum(POLICY['min_commission'],notional*POLICY['commission_rate'])
    visible=known&np.isfinite(raw)&raw.gt(0)&h.gt(l)
    score=-(c-l)/(h-l).where(h.gt(l))
    return score.where(event&cost.le(POLICY['reference_cash']/POLICY['slots']),1.).where(visible)
