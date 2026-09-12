"""BaoStock双价格输入：复权序列仅生成特征，成交仓库只收RAW。"""
from dataclasses import dataclass
import numpy as np
import pandas as pd

from chanlun_trader.engine.asof import MarketDataStore

VERSION = 'BAOSTOCK_HFQ_RETURN_5D_EXPLORATORY_V1'


@dataclass
class PriceViews:
    raw: pd.DataFrame
    features: pd.DataFrame
    diagnostics: pd.DataFrame

    def execution_store(self):
        store = MarketDataStore(feature_price_mode='raw')
        for symbol, rows in self.raw.groupby('symbol'):
            store.add_daily_raw(symbol, rows.set_index('date')[
                ['open', 'high', 'low', 'close', 'volume', 'amount', 'prev_close']])
        return store


def prepare_symbol(symbol, raw_rows, hfq_rows, sessions, *, state_rows=()):
    """只在授权的计算边界内调用；函数本身不授予真实输入或曝光许可。

    rows是同一Provider版本的原始API字典；不能将TDX价格混入后复权配对。
    调用者须先验证来源回执/哈希，再按原历史池预检决定哪些日期可排名。
    """
    if (not sessions or sessions != sorted(set(sessions)) or
            sessions[0] < 20220722 or sessions[-1] > 20240731):
        raise ValueError('TRAIN_CALENDAR_REQUIRED')
    code = symbol[-2:].lower() + '.' + symbol[:6]
    from .baostock_input_v1 import verified_ipo_prefix
    baseline = verified_ipo_prefix(raw_rows,hfq_rows,state_rows)

    def frame(rows, flag, fields):
        df = pd.DataFrame(rows)
        required = {'code', 'date', 'adjustflag', *fields}
        if not required <= set(df.columns):
            raise ValueError('PROVIDER_FIELDS_MISSING')
        mode_ok = df.adjustflag.astype(str).eq(flag)
        if flag == '1':
            mode_ok |= df.adjustflag.astype(str).eq('3') & df.date.isin(baseline)
        if not df.code.eq(code).all() or not mode_ok.all():
            raise ValueError('SYMBOL_OR_PRICE_MODE_CONFLICT')
        df['date'] = pd.to_datetime(df.date, format='%Y-%m-%d').dt.strftime('%Y%m%d').astype(int)
        if df.date.duplicated().any() or not df.date.isin(sessions).all():
            raise ValueError('DUPLICATE_OR_OUTSIDE_CALENDAR')
        for name in fields:
            df[name] = pd.to_numeric(df[name].replace('', np.nan), errors='raise')
        return df.set_index('date').sort_index()

    raw = frame(raw_rows, '3', ['open', 'high', 'low', 'close', 'volume', 'amount', 'preclose'])
    hfq = frame(hfq_rows, '1', ['close'])
    if not {'tradestatus', 'isST'} <= set(raw.columns):
        raise ValueError('STATE_FIELDS_MISSING')
    # 不按现有记录序号shift，不前填缺日，不把停牌空量填0。
    raw = raw.reindex(sessions)
    adjusted = hfq.close.reindex(sessions)
    ohlc = raw[['open', 'high', 'low', 'close']]
    valid = (np.isfinite(ohlc).all(axis=1) & ohlc.gt(0).all(axis=1) &
             raw.high.ge(ohlc.max(axis=1)) & raw.low.le(ohlc.min(axis=1)))
    valid &= np.isfinite(adjusted) & adjusted.gt(0)
    valid &= raw.tradestatus.astype(str).eq('1') & raw.isST.astype(str).isin(['0', '1'])
    valid &= np.isfinite(raw.volume) & raw.volume.gt(0) & np.isfinite(raw.amount) & raw.amount.gt(0)
    valid &= np.isfinite(raw.preclose) & raw.preclose.gt(0)
    dependency = valid.rolling(6, min_periods=6).sum().eq(6)
    values = (adjusted / adjusted.shift(5) - 1).where(dependency)
    values = values.where(np.isfinite(values))
    next_open = {day: pd.Timestamp(str(sessions[i+1])+' 09:30', tz='Asia/Shanghai')
                 for i, day in enumerate(sessions[:-1])}
    features = pd.DataFrame({'symbol': symbol, 'timestamp': sessions, 'value': values.to_numpy()})
    features['effective_available_at'] = features.timestamp.map(next_open)
    # 已有更晚真实时间优先，不能用下一开盘模型覆盖。
    latest = pd.Series(0.0, index=sessions)
    for df in [raw, hfq.reindex(sessions)]:
        if 'source_published_at' not in df:
            continue
        for day, value in df.source_published_at.items():
            if pd.isna(value) or value == 'UNKNOWN':
                continue
            observed = pd.Timestamp(value)
            if observed.tzinfo is None:
                raise ValueError('SOURCE_TIMEZONE_REQUIRED')
            latest.loc[day] = max(latest.loc[day], observed.timestamp())
    latest = latest.rolling(6, min_periods=1).max()
    for i, day in enumerate(sessions[:-1]):
        if latest.loc[day] > next_open[day].timestamp():
            features.loc[i, 'effective_available_at'] = pd.Timestamp(latest.loc[day], unit='s', tz='UTC').tz_convert('Asia/Shanghai')
    features.loc[features.effective_available_at.isna(), 'value'] = np.nan
    features['signal_version'] = VERSION
    diagnostics = pd.DataFrame({'date': sessions, 'pair_valid': valid.to_numpy(),
        'six_sessions_complete': dependency.to_numpy(), 'computable': features.value.notna()})
    raw = raw.rename(columns={'preclose': 'prev_close'}).reset_index(names='date')
    raw['symbol'] = symbol
    # 这里仅标示模型时间；真实历史发布时间未获证明。
    if 'source_published_at' not in raw:
        raw['source_published_at'] = 'UNKNOWN'
    return PriceViews(raw, features, diagnostics)
