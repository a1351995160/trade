"""事前机制组合：趋势回调恢复与波动收缩后成交额确认突破。"""
import numpy as np
import pandas as pd

from .technical_smoothing_signals_v1 import atr

FORMULAS={
    'ADX_EMA_PULLBACK_HOLD_20':
        'previous ADX14>=30 and PLUS_DI14>MINUS_DI14; previous EMA20>EMA20.shift(6); previous L<=previous EMA20; C>previous H; score=-(C-previous H)/previous ATR14',
    'SQUEEZE_TREND_TURNOVER_HOLD_20':
        'previous BB_WIDTH20<=previous trailing120 Q20(BB_WIDTH20); C>SMA50>SMA50.shift(5); C>previous20 H_MAX and previous C<=prior20 H_MAX; AMOUNT>=1.5*previous20 AMOUNT_MEAN; score=-(C-previous20 H_MAX)/previous ATR14',
}
WARMUP={'ADX_EMA_PULLBACK_HOLD_20':130,'SQUEEZE_TREND_TURNOVER_HOLD_20':140}


def directional(prices):
    """14个真实变动初始化TR/DM，14个DX初始化ADX；同幅双向变动DM均0。"""
    n=len(prices)
    plus=np.full(n,np.nan);minus=plus.copy();adx=plus.copy()
    if n<15:return tuple(pd.Series(a,index=prices.index) for a in (adx,plus,minus))
    up=prices.high.diff().to_numpy();down=-prices.low.diff().to_numpy()
    pos=np.where((up>down)&(up>0),up,0.)
    neg=np.where((down>up)&(down>0),down,0.)
    prev=prices.close.shift(1)
    tr=pd.concat([prices.high-prices.low,(prices.high-prev).abs(),(prices.low-prev).abs()],axis=1).max(axis=1).to_numpy()
    smooth_tr=float(tr[1:15].mean());smooth_pos=float(pos[1:15].mean());smooth_neg=float(neg[1:15].mean())
    dx=np.full(n,np.nan)
    for i in range(14,n):
        if i>14:
            smooth_tr=(13*smooth_tr+tr[i])/14
            smooth_pos=(13*smooth_pos+pos[i])/14
            smooth_neg=(13*smooth_neg+neg[i])/14
        plus[i]=100*smooth_pos/smooth_tr if smooth_tr>0 else 0.
        minus[i]=100*smooth_neg/smooth_tr if smooth_tr>0 else 0.
        total=plus[i]+minus[i]
        dx[i]=100*abs(plus[i]-minus[i])/total if total>0 else 0.
        if i==27:adx[i]=float(dx[14:28].mean())
        elif i>27:adx[i]=(13*adx[i-1]+dx[i])/14
    return tuple(pd.Series(a,index=prices.index) for a in (adx,plus,minus))


def chosen(prices,name):
    c=prices.close
    score=pd.Series(1.,index=prices.index)
    previous_atr=atr(prices).shift(1)
    if name=='ADX_EMA_PULLBACK_HOLD_20':
        adx,plus,minus=directional(prices)
        ema=c.ewm(span=20,adjust=False).mean()
        event=(adx.shift(1)>=30)&(plus.shift(1)>minus.shift(1))
        event&=(ema.shift(1)>ema.shift(6))&(prices.low.shift(1)<=ema.shift(1))
        event&=(c>prices.high.shift(1))&(previous_atr>0)
        return score.where(~event,-(c-prices.high.shift(1))/previous_atr)
    if name=='SQUEEZE_TREND_TURNOVER_HOLD_20':
        mean=c.rolling(20,min_periods=20).mean()
        width=4*c.rolling(20,min_periods=20).std(ddof=0)/mean
        threshold=width.rolling(120,min_periods=120).quantile(.2,interpolation='linear')
        squeeze=width.shift(1)<=threshold.shift(1)
        trend=c.rolling(50,min_periods=50).mean()
        upper=prices.high.shift(1).rolling(20,min_periods=20).max()
        amount=prices.amount
        average=amount.shift(1).rolling(20,min_periods=20).mean()
        event=squeeze&(c>trend)&(trend>trend.shift(5))
        event&=(c>upper)&(c.shift(1)<=upper.shift(1))&(amount>=1.5*average)&(average>0)&(previous_atr>0)
        return score.where(~event,-(c-upper)/previous_atr)
    raise ValueError('UNFROZEN_COMPOSITE_SIGNAL')
