"""既有五日变化相对市场中位数的滚动偏离；非论文多因子残差复现。"""
import numpy as np
import pandas as pd
from .response_confirmation_signals_v1 import FORMULAS as CONFIRMATION, chosen as confirmation_chosen
from .lag_response_signals_v1 import FORMULAS as LAG_RESPONSE, chosen as lag_chosen
from .market_sensitivity_signals_v1 import FORMULAS as SENSITIVITY, chosen as sensitivity_chosen

REVERSAL='MARKET_PROXY_RESIDUAL_REVERSAL_HOLD_20'
MOMENTUM='MARKET_PROXY_RESIDUAL_PERSISTENCE_HOLD_20'
NAMES=(REVERSAL,MOMENTUM)
ZSCORE='MARKET_PROXY_Z_REVERSAL_GATE_HOLD_20'
LOW_RISK='MARKET_PROXY_LOW_RESIDUAL_RISK_GATE_HOLD_20'
RISK_NAMES=(ZSCORE,LOW_RISK)
RECOVER='MARKET_PROXY_Z_RECOVERY_EXIT_HOLD_20'
DEFEND='MARKET_PROXY_Z_RECOVERY_MARKET_EXIT_HOLD_20'
EXIT_NAMES=(RECOVER,DEFEND)
EXIT_POLICY={'exit_type':'STRUCTURE_INVALIDATION','fixed_holding_sessions':20,
    'factor_conditions':[{'factor_id':'RESIDUAL_EXIT_CONDITION','operator':'EQ','value':1.}], 'logic':'OR'}
FORMULAS={
    **CONFIRMATION,
    **LAG_RESPONSE,
    **SENSITIVITY,
    RECOVER:'Original Z_REVERSAL_GATE entry unchanged; visible prior-session residual>=0 exits next eligible open; original fixed20 fallback',
    DEFEND:'Original Z_REVERSAL_GATE entry unchanged; visible prior-session residual>=0 OR market median RETURN5<=0 exits next eligible open; original fixed20 fallback',
    ZSCORE:'Same prior60 residual; sigma=STD_POP(prior20 sequential residuals)>0; market median RETURN5>0; score=current residual/sigma; long score<0; fixed20',
    LOW_RISK:'Same prior60 residual; sigma=STD_POP(prior20 sequential residuals)>0; current residual>0 and market median RETURN5>0; score=-1/(1+sigma); fixed20',
    REVERSAL:'Prior60 OLS RETURN_5D on median eligible RETURN_5D with intercept; current residual<0; score=current residual; fixed20',
    MOMENTUM:'Same strictly prior60 rolling OLS; mean of last20 sequential residuals>0; score=-mean20(residual); fixed20',
}
WARMUP={**{name:81 for name in CONFIRMATION},**{name:66 for name in LAG_RESPONSE},**{name:61 for name in SENSITIVITY},REVERSAL:61,MOMENTUM:80,ZSCORE:81,LOW_RISK:81,RECOVER:81,DEFEND:81}


def residual(y,x):
    """只用过去60个观测拟合当前预测；零方差不可计算，不设置数据驱动阈值。"""
    prior_y=y.shift(1);prior_x=x.shift(1)
    mean_y=prior_y.rolling(60,min_periods=60).mean()
    mean_x=prior_x.rolling(60,min_periods=60).mean()
    variance=prior_x.rolling(60,min_periods=60).var(ddof=0)
    covariance=prior_x.rolling(60,min_periods=60).cov(prior_y,ddof=0)
    beta=covariance/variance.where(variance>0)
    return y-(mean_y+beta*(x-mean_x))


def transform(rows,sessions,name):
    if name not in FORMULAS:raise ValueError('UNFROZEN_MARKET_RESIDUAL')
    if sessions!=sorted(set(sessions)) or rows.timestamp.duplicated().any():
        raise ValueError('INDEPENDENT_CALENDAR_REQUIRED')
    if rows.symbol.nunique()!=1 or not rows.timestamp.isin(sessions).all():
        raise ValueError('MARKET_RESIDUAL_IDENTITY_CONFLICT')
    a=rows.set_index('timestamp').reindex(sessions)
    valid=np.isfinite(a[['value','market_median']]).all(axis=1)
    valid &= a.effective_available_at.notna()&a.market_available_at.notna()
    score=pd.Series(np.nan,index=sessions)
    exit_values=pd.Series(np.nan,index=sessions)
    times=pd.Series(pd.NaT,index=sessions,dtype='datetime64[ns, UTC]')
    segments=(~valid).cumsum()
    for _,piece in a.loc[valid].groupby(segments[valid]):
        error=residual(piece.value,piece.market_median)
        target=error if name==REVERSAL else -error.rolling(20,min_periods=20).mean()
        if name in CONFIRMATION:
            target=confirmation_chosen(piece,error,name)
        if name in LAG_RESPONSE:
            target=lag_chosen(piece,name,sessions)
        if name in SENSITIVITY:
            target=sensitivity_chosen(piece,name,sessions)
        if name in (*RISK_NAMES,*EXIT_NAMES):
            sigma=error.shift(1).rolling(20,min_periods=20).std(ddof=0)
            known=sigma.gt(0)&np.isfinite(sigma)&np.isfinite(error)
            target=error/sigma if name!=LOW_RISK else (-1/(1+sigma)).where(error>0,1.)
            target=target.where(piece.market_median>0,1.).where(known)
            if name in EXIT_NAMES:
                event=error.ge(0)
                if name==DEFEND:event=event|piece.market_median.le(0)
                exit_values.loc[piece.index]=event.astype(float).where(known)
        score.loc[piece.index]=target.where(np.isfinite(target))
        known=pd.concat([pd.to_datetime(piece.effective_available_at,utc=True),
                         pd.to_datetime(piece.market_available_at,utc=True)],axis=1).max(axis=1).cummax()
        times.loc[piece.index]=known
    out=pd.DataFrame({'symbol':rows.symbol.iloc[0],'timestamp':sessions,'value':score.to_numpy(),
        'effective_available_at':times.to_numpy(),'signal_version':'TRAIN_SEARCH_BATCH_V1_'+name})
    out['computable']=out.value.notna()&out.effective_available_at.notna()
    if name in EXIT_NAMES:
        out['exit_invalidated']=exit_values.to_numpy()
        out['exit_available_at']=out.effective_available_at
    return out
