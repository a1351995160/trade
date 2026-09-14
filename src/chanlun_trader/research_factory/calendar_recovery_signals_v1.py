"""月初短持仓：独立日历时点、个股趋势/恢复、低日波动排序。"""
import numpy as np

NAME='MONTH_START_LOW_RISK_RECOVERY_HOLD_3'
FORMULAS={NAME:'Independent-calendar month-end signal, next session entry; HFQ C>SMA60,C/C[-5]-1<0,C>Cprev; score=-1/(1+population std20 daily returns); fixed3; no market median gate'}


def chosen(prices,name,sessions):
    if name!=NAME:raise ValueError('UNFROZEN_CALENDAR_RECOVERY')
    c=prices.close
    sigma=c.pct_change(fill_method=None).rolling(20,min_periods=20).std(ddof=0)
    mean=c.rolling(60,min_periods=60).mean();change=c/c.shift(5)-1
    ends={d for i,d in enumerate(sessions[:-1]) if d//100!=sessions[i+1]//100}
    event=prices.date.isin(ends)&(c>mean)&(change<0)&(c>c.shift(1))
    return (-1/(1+sigma)).where(event,1.).where(np.isfinite(sigma)&mean.notna()&change.notna())
