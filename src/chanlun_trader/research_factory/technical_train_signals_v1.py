"""公开方法启发的固定技术信号；复权仅用于特征，缺日分段，不回填分型。"""
import numpy as np
import pandas as pd

from chanlun_trader.chan import add_macd, merge_containment, detect_fx


FORMULAS = {
    'MACD_CROSS_HOLD_20':'EMA12-EMA26 crosses EMA9(DIF) upward with DIF>0; score=-(DIF-DEA)/C',
    'KDJ_OVERSOLD_CROSS_HOLD_20':'KDJ(9,3,3), seed K=D=50; K crosses D upward, previous K<20; score=-(K-D)/100',
    'CHAN_BOTTOM_MACD_HOLD_20':'SEALED_CONTAINMENT_BOTTOM_FRACTAL and MACD_HIST[t]>MACD_HIST[t-1]; score=-1',
    'MONTHLY_REVERSAL_HOLD_20':'C[t]/C[t-20]-1, only at independent-calendar month end; long negative score',
    'HIGH_252_HOLD_20':'-C[t]/MAX(C[t-251:t]); all calendar months retained',
    'LOW_MAX_20_HOLD_20':'-1/(1+MAX(DAILY_HFQ_RETURN[t-19:t]))',
}
WARMUP = {'MACD_CROSS_HOLD_20':130,'KDJ_OVERSOLD_CROSS_HOLD_20':30,
          'CHAN_BOTTOM_MACD_HOLD_20':130,'MONTHLY_REVERSAL_HOLD_20':21,
          'HIGH_252_HOLD_20':252,'LOW_MAX_20_HOLD_20':21}


def adjusted_rows(rows, raw, hfq_rows):
    """同日HFQ/RAW收盘比率派生信号OHLC；不改变成交原值，来源须由调用者验哈希。"""
    hfq=pd.DataFrame(hfq_rows)
    expected=rows.symbol.iloc[0][-2:].lower()+'.'+rows.symbol.iloc[0][:6]
    if not hfq.code.eq(expected).all():
        raise ValueError('HFQ_SYMBOL_CONFLICT')
    hfq['timestamp']=pd.to_datetime(hfq.date,format='%Y-%m-%d').dt.strftime('%Y%m%d').astype(int)
    hfq['adjusted_close']=pd.to_numeric(hfq.close,errors='raise')
    out=rows.merge(raw[['date','open','high','low','close']],left_on='timestamp',right_on='date',
                   how='left',validate='one_to_one')
    out=out.merge(hfq[['timestamp','adjusted_close']],on='timestamp',how='left',validate='one_to_one')
    ratio=(out.adjusted_close/out.close).where(out.close.gt(0)&out.adjusted_close.gt(0))
    for field in ['open','high','low','close']:
        out['signal_'+field]=out[field]*ratio
    return out


def sealed_bottoms(prices):
    """第三根合并K线封闭后才发布底分型；时间取下一合并组首bar，不取中间bar。"""
    merged = merge_containment(prices)
    observed = np.zeros(len(prices),dtype=bool)
    for fx in detect_fx(merged):
        if fx.kind=='bottom' and fx.idx+2<len(merged):
            available = int(merged.iloc[fx.idx+2].orig_idx_start)
            observed[available] = True
    return observed


def indicators(prices):
    out=add_macd(prices)
    low=out.low.rolling(9,min_periods=9).min()
    high=out.high.rolling(9,min_periods=9).max()
    rsv=(100*(out.close-low)/(high-low)).where(high>low)
    k=d=50.0
    ks,ds=[],[]
    for value in rsv:
        if not np.isfinite(value):
            k=d=50.0
            ks.append(np.nan);ds.append(np.nan)
            continue
        k=(2*k+value)/3
        d=(2*d+k)/3
        ks.append(k);ds.append(d)
    out['k'],out['d']=ks,ds
    out['j']=3*out.k-2*out.d
    return out


def transform(rows,sessions,name):
    if name not in FORMULAS:
        raise ValueError('UNFROZEN_TECHNICAL_SIGNAL')
    if sessions!=sorted(set(sessions)) or rows.timestamp.duplicated().any():
        raise ValueError('TECHNICAL_CALENDAR_IDENTITY_REQUIRED')
    if not rows.timestamp.isin(sessions).all() or rows.symbol.nunique()!=1:
        raise ValueError('TECHNICAL_ROW_IDENTITY_REQUIRED')
    aligned=rows.set_index('timestamp').reindex(sessions)
    fields=['signal_open','signal_high','signal_low','signal_close']
    valid=np.isfinite(aligned[fields]).all(axis=1) & aligned[fields].gt(0).all(axis=1)
    valid &= aligned.signal_high.ge(aligned[fields].max(axis=1))
    valid &= aligned.signal_low.le(aligned[fields].min(axis=1))
    valid &= aligned.effective_available_at.notna()
    score=pd.Series(np.nan,index=sessions)
    available=pd.Series(pd.NaT,index=sessions,dtype='datetime64[ns, UTC]')
    segments=(~valid).cumsum()
    for _, segment in aligned.loc[valid].groupby(segments[valid]):
        # 分段后只使用实际存在且连续的独立session；不跨缺日延续EMA状态。
        prices=segment[fields].rename(columns={f:f.removeprefix('signal_') for f in fields})
        prices=prices.reset_index(names='date')
        calc=indicators(prices)
        c=calc.close
        chosen=pd.Series(1.0,index=calc.index)
        if name=='MACD_CROSS_HOLD_20':
            event=(calc.dif>calc.dea)&(calc.dif.shift(1)<=calc.dea.shift(1))&(calc.dif>0)
            chosen=chosen.where(~event,-(calc.dif-calc.dea)/c)
        elif name=='KDJ_OVERSOLD_CROSS_HOLD_20':
            event=(calc.k>calc.d)&(calc.k.shift(1)<=calc.d.shift(1))&(calc.k.shift(1)<20)
            chosen=chosen.where(~event,-(calc.k-calc.d)/100)
            chosen=chosen.where(calc.k.notna()&calc.d.notna())
        elif name=='CHAN_BOTTOM_MACD_HOLD_20':
            event=sealed_bottoms(prices)&(calc.macd>calc.macd.shift(1))
            chosen=chosen.where(~event,-1.0)
        elif name=='MONTHLY_REVERSAL_HOLD_20':
            change=c/c.shift(20)-1
            month_end={day for i,day in enumerate(sessions[:-1]) if day//100!=sessions[i+1]//100}
            chosen=change.where(calc.date.isin(month_end),1.0).where(change.notna())
        elif name=='HIGH_252_HOLD_20':
            chosen=-c/c.rolling(252,min_periods=252).max()
        else:
            maximum=c.pct_change(fill_method=None).rolling(20,min_periods=20).max()
            chosen=-1/(1+maximum)
        chosen=chosen.where(np.arange(len(calc))>=WARMUP[name]-1)
        chosen=chosen.where(np.isfinite(chosen))
        score.loc[segment.index]=chosen.to_numpy()
        # EMA/分型依赖完整段前缀；真实更晚时间不能被下一开盘模型覆盖。
        available.loc[segment.index]=pd.to_datetime(segment.effective_available_at,utc=True).cummax()
    out=pd.DataFrame({'symbol':rows.symbol.iloc[0],'timestamp':sessions,'value':score.to_numpy(),
        'effective_available_at':available.to_numpy(),'signal_version':'TRAIN_SEARCH_BATCH_V1_'+name})
    out['computable']=out.value.notna()&out.effective_available_at.notna()
    return out
