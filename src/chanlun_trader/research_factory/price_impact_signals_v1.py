"""成交额价格冲击的两个固定假设；不把流动性代理当真实冲击成本。"""
import numpy as np
import pandas as pd

PREMIUM='WEEKLY_ILLIQUIDITY_PREMIUM_HOLD_20'
IMPROVING='WEEKLY_IMPACT_IMPROVEMENT_HOLD_20'
NAMES=(PREMIUM,IMPROVING)
FORMULAS={
    PREMIUM:'Calendar week-end; HFQ C>SMA60 and C>C[-60]; I20=MEAN20(abs(HFQ daily return)/RAW AMOUNT_CNY); I20>0; score=-I20; fixed20',
    IMPROVING:'Calendar week-end; HFQ C>SMA60 and C>C[-60]; I20 and I60=MEAN20/60(abs(HFQ daily return)/RAW AMOUNT_CNY); 0<I20<I60; score=-I60/I20; fixed20',
}


def chosen(prices,name,sessions):
    if name not in NAMES:raise ValueError('UNFROZEN_PRICE_IMPACT')
    c=prices.close
    impact=c.pct_change(fill_method=None).abs()/prices.amount
    i20=impact.rolling(20,min_periods=20).mean()
    i60=impact.rolling(60,min_periods=60).mean()
    weeks=[pd.Timestamp(str(d)).isocalendar()[:2] for d in sessions]
    ends={d for i,d in enumerate(sessions[:-1]) if weeks[i]!=weeks[i+1]}
    event=prices.date.isin(ends)&c.gt(c.rolling(60,min_periods=60).mean())&c.gt(c.shift(60))&i20.gt(0)
    if name==PREMIUM:
        score=-i20
    else:
        event=event&i20.lt(i60)
        score=-i60/i20.where(i20>0)
    return score.where(event,1.).where(np.arange(len(prices))>=60)
