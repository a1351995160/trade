"""供应商日换手序列的市场内分组；不用成交量冒充换手率。"""
import numpy as np
import pandas as pd

LOW='LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_3'
HIGH='HIGH_TURNOVER_WEEKLY_RECOVERY_HOLD_3'
NAMES=(LOW,HIGH)
LOW20='LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_20'
ALL_NAMES=(*NAMES,LOW20)
FORMULAS={
    LOW:'Calendar week-end; mean20 vendor turn percentile<=0.25 among >=100 eligible known peers; HFQ C/C[-20]-1>0; market median RETURN5>0; score=-(C/C[-20]-1); fixed3',
    HIGH:'Calendar week-end; mean20 vendor turn percentile>=0.75 among >=100 eligible known peers; HFQ C/C[-5]-1<0,C>Cprev; market median RETURN5>0; score=C/C[-5]-1; fixed3',
}


FORMULAS[LOW20]=FORMULAS[LOW].removesuffix('fixed3')+'fixed20'


def context(ready,turns,sessions):
    """独立日历20日均值，先按原适格ready集合分组，再决定买卖条件。"""
    if sessions!=sorted(set(sessions)) or turns.duplicated(['symbol','timestamp']).any():
        raise ValueError('TURNOVER_CALENDAR_OR_IDENTITY_CONFLICT')
    if not turns.timestamp.isin(sessions).all() or ready.duplicated(['symbol','timestamp']).any():
        raise ValueError('TURNOVER_ROW_IDENTITY_CONFLICT')
    values=pd.to_numeric(turns.turn,errors='raise')
    if (values.dropna()<0).any() or not np.isfinite(values.dropna()).all():
        raise ValueError('TURNOVER_VALUE_INVALID')
    grouped={symbol:g.set_index('timestamp').turn for symbol,g in turns.assign(turn=values).groupby('symbol',observed=True)}
    parts=[]
    for symbol,group in ready.groupby('symbol',sort=False,observed=True):
        series=grouped.get(symbol,pd.Series(dtype=float)).reindex(sessions)
        mean=series.rolling(20,min_periods=20).mean()
        part=group.copy();part['turnover_mean20']=mean.reindex(group.timestamp).to_numpy();parts.append(part)
    out=pd.concat(parts,ignore_index=True)
    known=out.turnover_mean20.notna()&out.effective_available_at.notna()
    out['turnover_mean20']=out.turnover_mean20.where(known)
    groups=out.groupby('timestamp',observed=True)
    out['turnover_members']=groups.turnover_mean20.transform('count')
    out['turnover_rank']=groups.turnover_mean20.rank(method='average',pct=True).where(out.turnover_members>=100)
    # 本版本明确只对日交易换手量采用完成日模型；不用于pe/pb财报发布时间。
    modeled=pd.to_datetime(out.timestamp.astype(str),format='%Y%m%d').dt.tz_localize('Asia/Shanghai')+pd.Timedelta(hours=18)
    original=pd.to_datetime(out.effective_available_at,utc=True)
    available=pd.concat([original,modeled.dt.tz_convert('UTC')],axis=1).max(axis=1).where(known)
    peer_times=available.groupby(out.timestamp).transform('max')
    out['effective_available_at']=pd.concat([original,peer_times],axis=1).max(axis=1)
    return out


def chosen(prices,name,sessions):
    if name not in ALL_NAMES:raise ValueError('UNFROZEN_TURNOVER_REGIME')
    weeks=[pd.Timestamp(str(d)).isocalendar()[:2] for d in sessions]
    ends={d for i,d in enumerate(sessions[:-1]) if weeks[i]!=weeks[i+1]}
    c=prices.close;rank=prices.turnover_rank
    event=prices.date.isin(ends)
    if name in (LOW,LOW20):
        change=c/c.shift(20)-1;event&=(rank<=.25)&(change>0);score=-change
    else:
        change=c/c.shift(5)-1;event&=(rank>=.75)&(change<0)&(c>c.shift(1));score=change
    return score.where(event,1.).where(rank.notna()&np.isfinite(change))
