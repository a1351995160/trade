"""成交股数/换手字段构成规模排序代理；不冒充厂商流通市值或PIT股本。"""
import numpy as np
import pandas as pd
from .affordable_portfolio_signals_v1 import POLICY

SMALL='WEEKLY_SMALL_SCALE_TREND_HOLD_20'
MIDDLE='WEEKLY_MID_SCALE_LOW_RISK_HOLD_20'
NAMES=(SMALL,MIDDLE)
FORMULAS={
    SMALL:'Calendar week-end; HFQ C>SMA60 and C/C[-60]>1; market median RETURN5>0; score=-1/(1+eligible percentile(RAW close*RAW volume/vendor turn)); >=100 known peers; original signal-day ticket budget; fixed20',
    MIDDLE:'Same weekly trend/market/affordability; scale-proxy percentile>0.30 and <=0.70; score=-1/(1+STD_POP20 daily HFQ return); fixed20',
}


def context(ready,daily,turns):
    if ready.duplicated(['symbol','timestamp']).any() or turns.duplicated(['symbol','timestamp']).any():raise ValueError('SCALE_PROXY_DUPLICATE')
    prices=daily[['symbol','date','close','volume']].rename(columns={'date':'timestamp'})
    joined=prices.merge(turns[['symbol','timestamp','turn']],on=['symbol','timestamp'],how='left',validate='one_to_one')
    for column in ('close','volume','turn'):
        joined[column]=pd.to_numeric(joined[column],errors='raise')
        present=joined[column].dropna()
        if (present<0).any() or not np.isfinite(present).all():raise ValueError('SCALE_PROXY_INVALID_VALUE')
    valid=joined[['close','volume','turn']].gt(0).all(axis=1)
    joined['scale_proxy']=(joined.close*joined.volume/joined.turn.where(valid)).where(valid)
    out=ready.merge(joined[['symbol','timestamp','scale_proxy']],on=['symbol','timestamp'],how='left',validate='one_to_one')
    out['scale_proxy']=out.scale_proxy.where(out.effective_available_at.notna())
    groups=out.groupby('timestamp',observed=True)
    members=groups.scale_proxy.transform('count')
    out['scale_rank']=groups.scale_proxy.rank(method='average',pct=True).where(members>=100)
    original=pd.to_datetime(out.effective_available_at,utc=True)
    modeled=pd.to_datetime(out.timestamp.astype(str),format='%Y%m%d').dt.tz_localize('Asia/Shanghai')+pd.Timedelta(hours=18)
    visible=pd.concat([original,modeled.dt.tz_convert('UTC')],axis=1).max(axis=1).where(out.scale_proxy.notna())
    peers=visible.groupby(out.timestamp).transform('max')
    out['effective_available_at']=pd.concat([original,peers],axis=1).max(axis=1)
    return out.drop(columns='scale_proxy')


def chosen(prices,name,sessions):
    if name not in NAMES:raise ValueError('UNFROZEN_SCALE_PROXY')
    c=prices.close;rank=prices.scale_rank
    weeks=[pd.Timestamp(str(d)).isocalendar()[:2] for d in sessions]
    ends={d for i,d in enumerate(sessions[:-1]) if weeks[i]!=weeks[i+1]}
    event=prices.date.isin(ends)&c.gt(c.rolling(60,min_periods=60).mean())&c.gt(c.shift(60))
    notional=prices.raw_close*(1+POLICY['slippage_fraction'])*POLICY['lot_size']
    cost=notional+np.maximum(POLICY['min_commission'],notional*POLICY['commission_rate'])
    known=rank.notna()&np.isfinite(prices.raw_close)&prices.raw_close.gt(0)
    event&=cost.le(POLICY['reference_cash']/POLICY['slots'])
    if name==SMALL:score=-1/(1+rank)
    else:
        sigma=c.pct_change(fill_method=None).rolling(20,min_periods=20).std(ddof=0)
        score=-1/(1+sigma);event&=rank.gt(.3)&rank.le(.7);known&=sigma.notna()
    return score.where(event,1.).where(known)
