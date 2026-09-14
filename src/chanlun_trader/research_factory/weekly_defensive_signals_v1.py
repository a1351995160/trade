"""周末防御型轮换假设；价格趋势、节奏、波动排序各自承担不同职责。"""
import pandas as pd

LOW_VOL='WEEKLY_LOW_VOL_EMA_EXIT_HOLD_20'
MOM_RISK='WEEKLY_MOM_RISK_EMA_EXIT_HOLD_20'
NAMES=(LOW_VOL,MOM_RISK)
FORMULAS={
    LOW_VOL:'Independent-calendar week end only; HFQ C>SMA200 and C/C.shift(60)-1>0; score=-1/(1+STD_POP20(daily HFQ return)); EMA20 invalidation exit or fixed20',
    MOM_RISK:'Same weekly positive-trend gate; score=-(C/C.shift(60)-1)/STD_POP60(daily HFQ return), sigma60>0; EMA20 invalidation exit or fixed20',
}


def chosen(prices,name,sessions):
    c=prices.close
    weeks=[pd.Timestamp(str(d)).isocalendar()[:2] for d in sessions]
    week_ends={d for i,d in enumerate(sessions[:-1]) if weeks[i]!=weeks[i+1]}
    momentum=c/c.shift(60)-1
    event=prices.date.isin(week_ends)&(c>c.rolling(200,min_periods=200).mean())&(momentum>0)
    returns=c.pct_change(fill_method=None)
    score=pd.Series(1.,index=prices.index)
    if name==LOW_VOL:
        sigma=returns.rolling(20,min_periods=20).std(ddof=0)
        return score.where(~event,-1/(1+sigma))
    if name==MOM_RISK:
        sigma=returns.rolling(60,min_periods=60).std(ddof=0)
        return score.where(~(event&sigma.gt(0)),-momentum/sigma)
    raise ValueError('UNFROZEN_WEEKLY_DEFENSIVE_SIGNAL')
