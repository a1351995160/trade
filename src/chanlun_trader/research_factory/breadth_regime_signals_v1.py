"""市场上涨参与范围的持续/恢复状态；只用已存在的适格RETURN5。"""
import numpy as np
import pandas as pd

PERSISTENT='WEEKLY_LOW_VOL_BREADTH_PERSISTENCE_HOLD_20'
RECOVERY='WEEKLY_LOW_VOL_BREADTH_RECOVERY_HOLD_20'
NAMES=(PERSISTENT,RECOVERY)
FORMULAS={
    PERSISTENT:'Keep LOW_VOL_TREND60 weekly entry/rank/affordability/fixed20; replace current median RETURN5 gate by mean20 daily fraction(eligible RETURN5>0)>0.5; >=100 known members each session',
    RECOVERY:'Same fixed low-vol parent; replace current market gate by current positive RETURN5 breadth>0.5 and mean5 breadth>mean20 breadth; >=100 known members per session; fixed20',
}


def context(rows,sessions):
    if rows.duplicated(['symbol','timestamp']).any() or sessions!=sorted(set(sessions)):
        raise ValueError('BREADTH_CALENDAR_OR_DUPLICATE')
    value=pd.to_numeric(rows.value,errors='raise')
    known=np.isfinite(value)&rows.effective_available_at.notna()
    frame=rows[['timestamp','effective_available_at']].copy()
    frame['positive']=(value>0).astype(float).where(known)
    frame['visible']=pd.to_datetime(frame.effective_available_at,utc=True).where(known)
    g=frame.groupby('timestamp',observed=True).agg(breadth=('positive','mean'),count=('positive','count'),visible=('visible','max')).reindex(sessions)
    g['breadth']=g.breadth.where(g['count']>=100)
    g['breadth5']=g.breadth.rolling(5,min_periods=5).mean()
    g['breadth20']=g.breadth.rolling(20,min_periods=20).mean()
    seconds=pd.Series([x.timestamp() if pd.notna(x) else np.nan for x in g.visible],index=g.index).where(g.breadth.notna())
    g['breadth_available_at']=pd.to_datetime(seconds.rolling(20,min_periods=20).max(),unit='s',utc=True)
    g.index.name='timestamp'
    return rows.join(g[['breadth','breadth5','breadth20','breadth_available_at']],on='timestamp')


def gate(aligned,name):
    if name not in NAMES:raise ValueError('UNFROZEN_BREADTH_REGIME')
    known=np.isfinite(aligned[['breadth','breadth5','breadth20']]).all(axis=1)&aligned.breadth_available_at.notna()
    event=aligned.breadth20.gt(.5) if name==PERSISTENT else aligned.breadth.gt(.5)&aligned.breadth5.gt(aligned.breadth20)
    return event,known
