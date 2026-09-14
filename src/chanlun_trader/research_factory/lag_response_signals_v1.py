"""严格滞后配对的短周期预测；非统计资格或论文复现。"""
import numpy as np
import pandas as pd

SELF='WEEKLY_SELF_REVERSION_FORECAST_HOLD_3'
MARKET='WEEKLY_MARKET_LAG_FORECAST_HOLD_3'
NAMES=(SELF,MARKET)
FORMULAS={
    SELF:'Prior60 complete pairs (stock RETURN5[s-5],stock RETURN5[s]), s<=t-1; OLS intercept+slope; forecast a+b*stock RETURN5[t]; -1<b<0 and current stock<0; week-end, market>0, forecast>0; score=-forecast; fixed3',
    MARKET:'Prior60 complete pairs (market median RETURN5[s-5],stock RETURN5[s]), s<=t-1; OLS intercept+slope; forecast a+b*market RETURN5[t]; b>0 and current stock<market; week-end, market>0, forecast>0; score=-forecast; fixed3',
}


def forecast(y,x):
    # 每对的两段五日价格变化不重叠；不同配对之间仍相关，不作p/q检验。
    lag=x.shift(6);target=y.shift(1)
    mean_x=lag.rolling(60,min_periods=60).mean()
    mean_y=target.rolling(60,min_periods=60).mean()
    variance=lag.rolling(60,min_periods=60).var(ddof=0)
    beta=lag.rolling(60,min_periods=60).cov(target,ddof=0)/variance.where(variance>0)
    return mean_y+beta*(x-mean_x),beta


def chosen(piece,name,sessions):
    if name not in NAMES:raise ValueError('UNFROZEN_LAG_RESPONSE')
    x=piece.value if name==SELF else piece.market_median
    predicted,beta=forecast(piece.value,x)
    weeks=[pd.Timestamp(str(d)).isocalendar()[:2] for d in sessions]
    ends={d for i,d in enumerate(sessions[:-1]) if weeks[i]!=weeks[i+1]}
    event=piece.index.isin(ends)&piece.market_median.gt(0)&predicted.gt(0)
    if name==SELF:event=event&beta.gt(-1)&beta.lt(0)&piece.value.lt(0)
    else:event=event&beta.gt(0)&piece.value.lt(piece.market_median)
    return (-predicted).where(event,1.).where(np.isfinite(predicted)&np.isfinite(beta))
