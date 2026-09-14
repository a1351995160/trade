"""相对偏离与滞后响应确认；保留父家族历史负担。"""
import numpy as np
from .lag_response_signals_v1 import forecast

SELF='RESIDUAL_SELF_REVERSION_CONFIRM_HOLD_20'
MARKET='RESIDUAL_MARKET_LAG_CONFIRM_HOLD_20'
NAMES=(SELF,MARKET)
FORMULAS={
    SELF:'Original prior60 market residual/prior20 residual sigma Z<0 and market>0; additionally prior60 lag5 SELF forecast>0, -1<b<0, stock RETURN5<0; score=Z; original daily entry/fixed20',
    MARKET:'Original prior60 market residual/prior20 residual sigma Z<0 and market>0; additionally prior60 lag5 MARKET forecast>0, b>0, stock RETURN5<market; score=Z; original daily entry/fixed20',
}


def chosen(piece,error,name):
    if name not in NAMES:raise ValueError('UNFROZEN_RESPONSE_CONFIRMATION')
    sigma=error.shift(1).rolling(20,min_periods=20).std(ddof=0)
    x=piece.value if name==SELF else piece.market_median
    predicted,b=forecast(piece.value,x)
    z=error/sigma.where(sigma>0)
    event=z.lt(0)&piece.market_median.gt(0)&predicted.gt(0)
    if name==SELF:event=event&b.gt(-1)&b.lt(0)&piece.value.lt(0)
    else:event=event&b.gt(0)&piece.value.lt(piece.market_median)
    known=np.isfinite(z)&np.isfinite(predicted)&np.isfinite(b)
    return z.where(event,1.).where(known)
