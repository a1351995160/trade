"""公开方法启发的固定技术信号；复权仅用于特征，缺日分段，不回填分型。"""
import numpy as np
import pandas as pd

from chanlun_trader.chan import add_macd, merge_containment, detect_fx
from .technical_followup_signals_v1 import FORMULAS as FOLLOWUP, WARMUP as FOLLOWUP_WARMUP, chosen as followup_chosen
from .technical_pattern_signals_v1 import bullish_engulfing
from .technical_smoothing_signals_v1 import FORMULAS as SMOOTHING, WARMUP as SMOOTHING_WARMUP, chosen as smoothing_chosen
from .technical_composite_signals_v1 import FORMULAS as COMPOSITE, WARMUP as COMPOSITE_WARMUP, chosen as composite_chosen
from .structured_exit_trial_v1 import FORMULAS as STRUCTURED, BASE as STRUCTURED_BASE, WITH_MARKET
from .weekly_defensive_signals_v1 import FORMULAS as DEFENSIVE, chosen as defensive_chosen
from .weekly_defensive_signals_v1 import LOW_VOL
from .weekly_fixed_trial_v1 import FORMULAS as FIXED_DEFENSIVE, WITH_MARKET as FIXED_MARKET
from .price_volume_patterns_v1 import FORMULAS as PRICE_VOLUME, chosen as price_volume_chosen


from .recovery_rotation_signals_v1 import FORMULAS as RECOVERY_ROTATION, WARMUP as RECOVERY_WARMUP, RECOVERY, chosen as recovery_chosen

from .volume_flow_signals_v1 import FORMULAS as VOLUME_FLOW, RANGE as FLOW_RANGE, chosen as flow_chosen
from .skill_alpha_signals_v1 import FORMULAS as SKILL_ALPHA, DIVERGENCE as ALPHA_VOLUME, chosen as alpha_chosen
from .return_path_signals_v1 import FORMULAS as RETURN_PATH, chosen as path_chosen
from .price_impact_signals_v1 import FORMULAS as PRICE_IMPACT, chosen as impact_chosen

from .shock_consolidation_signals_v1 import FORMULAS as SHOCK, chosen as shock_chosen

from .alpha191_pressure_signals_v1 import FORMULAS as A191, VOLUME as A191_VOLUME, chosen as a191_chosen

from .turnover_regime_signals_v1 import FORMULAS as TURNOVER, chosen as turnover_chosen
from .calendar_recovery_signals_v1 import FORMULAS as CALENDAR_RECOVERY, chosen as calendar_chosen

from .affordable_portfolio_signals_v1 import FORMULAS as AFFORDABLE, WARMUP as AFFORDABLE_WARMUP

from .opening_pressure_signals_v1 import FORMULAS as OPENING_PRESSURE, chosen as opening_chosen

from .scale_proxy_signals_v1 import FORMULAS as SCALE_PROXY, chosen as scale_chosen

from .participation_stability_signals_v1 import FORMULAS as PARTICIPATION, chosen as participation_chosen

from .trend_risk_horizon_signals_v1 import FORMULAS as TREND_RISK, chosen as trend_risk_chosen

from .breadth_regime_signals_v1 import FORMULAS as BREADTH, gate as breadth_gate
from .trend_risk_horizon_signals_v1 import LOW_VOL as TREND_LOW_VOL

from .stock_trend_only_signals_v1 import FORMULAS as STOCK_TREND_ONLY
from .failed_low_break_signals_v1 import FORMULAS as FAILED_LOW, chosen as failed_low_chosen
from .low_skew_signals_v1 import FORMULAS as LOW_SKEW, chosen as low_skew_chosen

FORMULAS = {
    **LOW_SKEW,
    **FAILED_LOW,
    **STOCK_TREND_ONLY,
    **BREADTH,
    **TREND_RISK,
    **PARTICIPATION,
    **SCALE_PROXY,
    **OPENING_PRESSURE,
    **AFFORDABLE,
    **TURNOVER, **CALENDAR_RECOVERY,
    **A191,
    **SHOCK,
    **SKILL_ALPHA,
    **PRICE_IMPACT,
    **RETURN_PATH,
    **VOLUME_FLOW,
    **RECOVERY_ROTATION,
    **PRICE_VOLUME,
    **FIXED_DEFENSIVE,
    **DEFENSIVE,
    **STRUCTURED,
    **COMPOSITE,
    **SMOOTHING,
    'RSI2_TREND_200_HOLD_20':'C>SMA200 and C<SMA5 and WILDER_RSI2<5; score=RSI2-5',
    'BULL_ENGULFING_DOWN_5_HOLD_20':'previous bearish body, current bullish body engulfs previous body and C[t-1]<C[t-6]; score=-(C-O)/C',
    **FOLLOWUP,
    'RSI_14_RECLAIM_HOLD_20':'WILDER_RSI14 crosses 30 upward from below; SMA seed of first14 deltas, flat=50; score=-RSI/100',
    'AROON_25_CROSS_HOLD_20':'AROON_UP25 crosses AROON_DOWN25 upward and UP>=70; last26 bars, most recent tied extremum; score=-(UP-DOWN)/100',
    'BOLL_REENTRY_HOLD_20':'C[t-1]<SMA20[t-1]-2*STD_POP20[t-1] and C[t]>=SMA20[t]-2*STD_POP20[t] and C[t]<SMA20[t]; score=(C[t]-SMA20[t])/STD_POP20[t]',
    'DONCHIAN_55_BREAKOUT_HOLD_20':'C[t]>MAX(H[t-55:t-1]) and C[t-1]<=MAX(H[t-56:t-2]); score=1-C[t]/MAX(H[t-55:t-1])',
    'MACD_CROSS_HOLD_20':'EMA12-EMA26 crosses EMA9(DIF) upward with DIF>0; score=-(DIF-DEA)/C',
    'KDJ_OVERSOLD_CROSS_HOLD_20':'KDJ(9,3,3), seed K=D=50; K crosses D upward, previous K<20; score=-(K-D)/100',
    'CHAN_BOTTOM_MACD_HOLD_20':'SEALED_CONTAINMENT_BOTTOM_FRACTAL and MACD_HIST[t]>MACD_HIST[t-1]; score=-1',
    'MONTHLY_REVERSAL_HOLD_20':'C[t]/C[t-20]-1, only at independent-calendar month end; long negative score',
    'HIGH_252_HOLD_20':'-C[t]/MAX(C[t-251:t]); all calendar months retained',
    'LOW_MAX_20_HOLD_20':'-1/(1+MAX(DAILY_HFQ_RETURN[t-19:t]))',
}
WARMUP = {**{name:61 for name in LOW_SKEW},**{name:22 if "NEXT_CLOSE" in name else 21 for name in FAILED_LOW},**{name:61 for name in STOCK_TREND_ONLY},**{name:61 for name in BREADTH},**{name:61 for name in TREND_RISK},**{name:61 for name in PARTICIPATION},**{name:61 for name in SCALE_PROXY},**{name:21 for name in OPENING_PRESSURE},**AFFORDABLE_WARMUP,**{name:61 for name in CALENDAR_RECOVERY},**{name:21 for name in TURNOVER},**{name:61 for name in A191},**{name:24 for name in SHOCK},**{name:61 for name in SKILL_ALPHA},**{name:61 for name in PRICE_IMPACT},**{name:61 for name in RETURN_PATH},**{name:65 for name in VOLUME_FLOW},**RECOVERY_WARMUP,'RSI2_TREND_200_HOLD_20':200,'BULL_ENGULFING_DOWN_5_HOLD_20':7,
          **{name:65 for name in PRICE_VOLUME},
          **{name:200 for name in FIXED_DEFENSIVE},
          **{name:200 for name in DEFENSIVE},
          **{name:130 for name in STRUCTURED},
          **COMPOSITE_WARMUP,
          **SMOOTHING_WARMUP,
          **FOLLOWUP_WARMUP,'RSI_14_RECLAIM_HOLD_20':16,'AROON_25_CROSS_HOLD_20':27,
          'BOLL_REENTRY_HOLD_20':21,'DONCHIAN_55_BREAKOUT_HOLD_20':57,
          'MACD_CROSS_HOLD_20':130,'KDJ_OVERSOLD_CROSS_HOLD_20':30,
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


def wilder_rsi(close, period=14):
    """首period个变动均值初始化，后续Wilder递推；常价为中性50。"""
    delta=close.diff().to_numpy()
    result=np.full(len(close),np.nan)
    if len(close)<=period:return pd.Series(result,index=close.index)
    gain=np.maximum(delta[1:period+1],0).mean()
    loss=np.maximum(-delta[1:period+1],0).mean()
    for i in range(period,len(close)):
        if i>period:
            gain=(gain*(period-1)+max(delta[i],0))/period
            loss=(loss*(period-1)+max(-delta[i],0))/period
        result[i]=100*gain/(gain+loss) if gain+loss>0 else 50.
    return pd.Series(result,index=close.index)


def aroon(high,low,period=25):
    up=high.rolling(period+1,min_periods=period+1).apply(
        lambda x:100*(period-np.argmax(x[::-1]))/period,raw=True)
    down=low.rolling(period+1,min_periods=period+1).apply(
        lambda x:100*(period-np.argmin(x[::-1]))/period,raw=True)
    return up,down


def transform(rows,sessions,name):
    if name in AFFORDABLE:
        from .affordable_portfolio_signals_v1 import transform as affordable_transform
        return affordable_transform(rows,sessions,name)
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
    if name=='SQUEEZE_TREND_TURNOVER_HOLD_20' or name in PRICE_IMPACT:
        valid &= np.isfinite(aligned.amount)&aligned.amount.gt(0)
    if name==A191_VOLUME or name in SHOCK or name in PRICE_VOLUME or name in VOLUME_FLOW or name==ALPHA_VOLUME or name in PARTICIPATION:
        valid &= np.isfinite(aligned.volume)&aligned.volume.gt(0)
    if name==FLOW_RANGE:
        valid &= aligned.signal_high.gt(aligned.signal_low)
    score=pd.Series(np.nan,index=sessions)
    available=pd.Series(pd.NaT,index=sessions,dtype='datetime64[ns, UTC]')
    exit_values=pd.Series(np.nan,index=sessions)
    segments=(~valid).cumsum()
    for _, segment in aligned.loc[valid].groupby(segments[valid]):
        # 分段后只使用实际存在且连续的独立session；不跨缺日延续EMA状态。
        prices=segment[fields].rename(columns={f:f.removeprefix('signal_') for f in fields})
        if name=='SQUEEZE_TREND_TURNOVER_HOLD_20' or name in PRICE_IMPACT:
            prices=prices.assign(amount=segment.amount)
        if name==A191_VOLUME or name in SHOCK or name in PRICE_VOLUME or name in VOLUME_FLOW or name==ALPHA_VOLUME or name in PARTICIPATION:
            prices=prices.assign(volume=segment.volume)
        if name in TURNOVER:
            prices=prices.assign(turnover_rank=segment.turnover_rank)
        if name in OPENING_PRESSURE or name in SCALE_PROXY or name in PARTICIPATION or name in TREND_RISK or name in BREADTH or name in STOCK_TREND_ONLY or name in FAILED_LOW or name in LOW_SKEW:prices=prices.assign(raw_close=segment.close)
        if name in SCALE_PROXY:prices=prices.assign(scale_rank=segment.scale_rank)
        prices=prices.reset_index(names='date')
        calc=indicators(prices)
        c=calc.close
        chosen=pd.Series(1.0,index=calc.index)
        if name in LOW_SKEW:
            chosen=low_skew_chosen(calc,name,sessions)
        elif name in FAILED_LOW:
            chosen=failed_low_chosen(calc,name)
        elif name in STOCK_TREND_ONLY:
            chosen=trend_risk_chosen(calc,TREND_LOW_VOL,sessions)
        elif name in BREADTH:
            chosen=trend_risk_chosen(calc,TREND_LOW_VOL,sessions)
        elif name in TREND_RISK:
            chosen=trend_risk_chosen(calc,name,sessions)
        elif name in PARTICIPATION:
            chosen=participation_chosen(calc,name,sessions)
        elif name in SCALE_PROXY:
            chosen=scale_chosen(calc,name,sessions)
        elif name in OPENING_PRESSURE:
            chosen=opening_chosen(calc,name,sessions)
        elif name in TURNOVER:
            chosen=turnover_chosen(calc,name,sessions)
        elif name in CALENDAR_RECOVERY:
            chosen=calendar_chosen(calc,name,sessions)
        elif name in A191:
            chosen=a191_chosen(calc,name)
        elif name in SHOCK:
            chosen=shock_chosen(calc,name)
        elif name in SKILL_ALPHA:
            chosen=alpha_chosen(calc,name,sessions)
        elif name in PRICE_IMPACT:
            chosen=impact_chosen(calc,name,sessions)
        elif name in RETURN_PATH:
            chosen=path_chosen(calc,name,sessions)
        elif name in RECOVERY_ROTATION:
            chosen=recovery_chosen(calc,name,wilder_rsi)
            if name==RECOVERY:
                exit_values.loc[segment.index]=(c>c.rolling(5,min_periods=5).mean()).astype(float).where(
                    np.arange(len(calc))>=WARMUP[name]-1).to_numpy()
        elif name in PRICE_VOLUME or name in VOLUME_FLOW:
            chosen=flow_chosen(calc,name) if name in VOLUME_FLOW else price_volume_chosen(calc,name)
        elif name in FIXED_DEFENSIVE:
            chosen=defensive_chosen(calc,LOW_VOL,sessions)
        elif name in STRUCTURED or name in DEFENSIVE or name==RECOVERY:
            chosen=(defensive_chosen(calc,name,sessions) if name in DEFENSIVE else
                    composite_chosen(calc,STRUCTURED_BASE))
            exit_values.loc[segment.index]=(c<c.ewm(span=20,adjust=False).mean()).astype(float).where(
                np.arange(len(calc))>=WARMUP[name]-1).to_numpy()
        elif name in COMPOSITE:
            chosen=composite_chosen(calc,name)
        elif name in SMOOTHING:
            chosen=smoothing_chosen(calc,name)
        elif name=='RSI2_TREND_200_HOLD_20':
            rsi=wilder_rsi(c,period=2)
            event=(c>c.rolling(200,min_periods=200).mean())&(c<c.rolling(5,min_periods=5).mean())&(rsi<5)
            chosen=chosen.where(~event,rsi-5)
        elif name=='BULL_ENGULFING_DOWN_5_HOLD_20':
            chosen=bullish_engulfing(calc)
        elif name in FOLLOWUP:
            chosen=followup_chosen(calc,name)
        elif name=='RSI_14_RECLAIM_HOLD_20':
            rsi=wilder_rsi(c)
            event=(rsi.shift(1)<30)&(rsi>=30)
            chosen=chosen.where(~event,-rsi/100)
        elif name=='AROON_25_CROSS_HOLD_20':
            up,down=aroon(calc.high,calc.low)
            event=(up>down)&(up.shift(1)<=down.shift(1))&(up>=70)
            chosen=chosen.where(~event,-(up-down)/100)
        elif name=='BOLL_REENTRY_HOLD_20':
            mean=c.rolling(20,min_periods=20).mean()
            std=c.rolling(20,min_periods=20).std(ddof=0)
            lower=mean-2*std
            event=(c.shift(1)<lower.shift(1))&(c>=lower)&(c<mean)&std.gt(0)
            chosen=chosen.where(~event,(c-mean)/std)
        elif name=='DONCHIAN_55_BREAKOUT_HOLD_20':
            upper=calc.high.shift(1).rolling(55,min_periods=55).max()
            event=(c>upper)&(c.shift(1)<=upper.shift(1))
            chosen=chosen.where(~event,1-c/upper)
        elif name=='MACD_CROSS_HOLD_20':
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
    if name in STRUCTURED or name in DEFENSIVE or name==RECOVERY:
        out['exit_invalidated']=exit_values.to_numpy()
        out['exit_available_at']=available.to_numpy()
    if name in (WITH_MARKET,FIXED_MARKET) or name in SKILL_ALPHA or name in SHOCK or name in A191 or name in TURNOVER or name in OPENING_PRESSURE or name in SCALE_PROXY or name in PARTICIPATION or name in TREND_RISK or name in FAILED_LOW or name in LOW_SKEW:
        median=pd.to_numeric(aligned.market_median,errors='raise')
        market_times=pd.to_datetime(aligned.market_available_at,utc=True)
        known=np.isfinite(median)&market_times.notna()
        out['value']=out.value.where((known&median.gt(0)).to_numpy(),1.).where(score.notna().to_numpy())
        entry_times=pd.concat([available,market_times],axis=1).max(axis=1)
        out['effective_available_at']=entry_times.where(known&available.notna()).to_numpy()
    if name in BREADTH:
        event,known=breadth_gate(aligned,name)
        out['value']=out.value.where(event.to_numpy(),1.).where((known&score.notna()).to_numpy())
        latest=pd.concat([available,pd.to_datetime(aligned.breadth_available_at,utc=True)],axis=1).max(axis=1)
        out['effective_available_at']=latest.where(known&available.notna()).to_numpy()
    out['computable']=out.value.notna()&out.effective_available_at.notna()
    if name in STRUCTURED or name in DEFENSIVE or name==RECOVERY:
        # 市场信息缺失只禁止新买入；保留已可见持仓退出切片供原账户消费。
        out['computable'] |= out.exit_invalidated.notna()&out.exit_available_at.notna()
        out.loc[out.effective_available_at.isna()&out.computable,'value']=1.
        out['effective_available_at']=out.effective_available_at.fillna(out.exit_available_at)
    return out
