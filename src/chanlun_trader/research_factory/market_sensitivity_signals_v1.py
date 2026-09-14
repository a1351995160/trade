"""市场代理敏感度假设；重叠五日观测不用于统计显著性。"""
import numpy as np
import pandas as pd

LOW='WEEKLY_LOW_PROXY_BETA_GATE_HOLD_20'
DOWN='WEEKLY_LOW_DOWNSIDE_PROXY_BETA_GATE_HOLD_20'
NAMES=(LOW,DOWN)
FORMULAS={
    LOW:'Prior60 OLS slope of RETURN5 on median eligible RETURN5; week-end, mean prior60 stock RETURN5>0, current market>0, 0<=beta<=1; score=-1/(1+beta); fixed20',
    DOWN:'Prior60 conditional OLS slope only where market RETURN5<0, at least20 negative observations and full60 valid; week-end, mean prior60 stock RETURN5>0, current market>0, 0<=downside_beta<=1; score=-1/(1+downside_beta); fixed20',
}


def slope(y,x,downside=False):
    y=y.shift(1);x=x.shift(1)
    full=(y.notna()&x.notna()).rolling(60,min_periods=60).sum().eq(60)
    if downside:
        mask=x<0;y=y.where(mask);x=x.where(mask)
    minimum=20 if downside else 60
    variance=x.rolling(60,min_periods=minimum).var(ddof=0)
    covariance=x.rolling(60,min_periods=minimum).cov(y,ddof=0)
    return (covariance/variance.where(variance>0)).where(full)


def chosen(piece,name,sessions):
    if name not in NAMES:raise ValueError('UNFROZEN_MARKET_SENSITIVITY')
    b=slope(piece.value,piece.market_median,name==DOWN)
    weeks=[pd.Timestamp(str(d)).isocalendar()[:2] for d in sessions]
    ends={d for i,d in enumerate(sessions[:-1]) if weeks[i]!=weeks[i+1]}
    trend=piece.value.shift(1).rolling(60,min_periods=60).mean()
    event=piece.index.isin(ends)&trend.gt(0)&piece.market_median.gt(0)&b.between(0,1)
    return (-1/(1+b)).where(event,1.).where(np.isfinite(b)&np.isfinite(trend))
