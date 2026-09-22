"""BT_INDICATORS_V2 — 多家族技术指标实现（单证券时序层）。

与 V1 的关系
------------
本模块**扩展**而非替换 ``engine/indicators_v1.py``：

- 复用 V1 的 ``IndicatorInputError`` / ``_require_time_index`` / ``_numeric`` /
  ``segment_ids`` / ``ema_recursive`` / ``sma`` / ``cross_up`` / ``cross_down``；
- 复用 V1 的分段语义：任一不合格输入结束当前计算段，递推状态在下一段从初值
  重新开始，跨缺口不伪造交叉；
- V1 的 ``MACD_V1`` / ``KDJ_V1`` / ``MA_EMA_CROSS_V1`` 保持原样，本模块通过
  注册表以别名指向它们（``REUSE_AS_IS``），不重写、不改输出。

价格与单位
----------
所有价格类指标在**调用方给定的价格序列**上计算。执行尺度（RAW）与特征尺度
（PIT_QFQ）是两个不同口径，由调用方声明；本模块不做隐式复权换算。

公式方言
--------
明确区分容易混淆的口径，各自独立命名、独立版本：

- ``sma_arithmetic``（算术移动平均）与 ``sma_tdx``（通达信 SMA(X,N,M)）；
- ``ema_recursive``（初值=首个输入值）与 ``rma_wilder``（alpha=1/N）；
- MACD 柱：``MACD_V1`` 用 ``2*(DIF-DEA)``，另提供 ``macd_hist_raw`` 为 ``DIF-DEA``；
- WR 使用 0~100 表达（``(HHV-C)/(HHV-LLV)*100``），不返回负值形式；
- BOLL 默认总体标准差（``ddof=0``），可显式切换样本标准差；
- RVOL 明确区分"分母含当前 bar"与"分母为前 N 根"。

本模块不声称与任何第三方库逐值一致；未做同口径外部对照时不声明软件对齐。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from .indicators_v1 import (
    IndicatorInputError,
    IndicatorFrameV1,
    _numeric,
    _require_positive_int,
    _require_time_index,
    cross_down,
    cross_up,
    ema_recursive,
    segment_ids,
    sma,
)

INDICATORS_V2_VERSION = "BT_INDICATORS_V2"

# --------------------------------------------------------------------------
# 分段滚动工具
# --------------------------------------------------------------------------


def _segment_series(values: np.ndarray, segments: np.ndarray) -> pd.Series:
    """构造 (segment, position) 二级索引序列，便于按段滚动且不跨缺口。"""
    return pd.Series(values, index=pd.MultiIndex.from_arrays(
        [segments, np.arange(len(values))], names=["segment", "position"]))


def _rolling_in_segments(
    values: np.ndarray,
    segments: np.ndarray,
    window: int,
    min_periods: int,
    method: str,
    *,
    ddof: int = 1,
) -> np.ndarray:
    """按段滚动。窗口右对齐（含当前 bar），不跨段。

    段编号为 -1（不合格输入）的行恒为 NaN。
    """
    window = _require_positive_int(window, "window")
    if min_periods <= 0 or min_periods > window:
        raise IndicatorInputError("INVALID_MIN_PERIODS")
    out = np.full(len(values), np.nan, dtype=float)
    valid_positions = np.flatnonzero(segments >= 0)
    if valid_positions.size == 0:
        return out
    series = _segment_series(values, segments).iloc[valid_positions]
    grouped = series.groupby(level="segment", sort=False)
    roll = grouped.rolling(window, min_periods=min_periods)
    if method == "sum":
        computed = roll.sum()
    elif method == "mean":
        computed = roll.mean()
    elif method == "std":
        computed = roll.std(ddof=ddof)
    elif method == "var":
        computed = roll.var(ddof=ddof)
    elif method == "min":
        computed = roll.min()
    elif method == "max":
        computed = roll.max()
    elif method == "count":
        computed = roll.count().astype(float)
    else:
        raise IndicatorInputError(f"UNSUPPORTED_ROLLING_METHOD:{method}")
    positions = computed.index.get_level_values("position").to_numpy()
    out[positions] = computed.to_numpy(dtype=float)
    return out


def _recursive_in_segments(values: np.ndarray, segments: np.ndarray, step) -> np.ndarray:
    """按段递推。``step(previous, value) -> new``；段首 previous 为 None。"""
    out = np.full(len(values), np.nan, dtype=float)
    for segment in range(int(segments.max()) + 1) if len(segments) else []:
        positions = np.flatnonzero(segments == segment)
        if positions.size == 0:
            continue
        previous = None
        for position in positions:
            previous = step(previous, values[position])
            out[position] = previous
    return out


def _segment_count(segments: np.ndarray) -> np.ndarray:
    """段内第几根（从 1 起）。不合格位置为 0。"""
    counts = np.zeros(len(segments), dtype=int)
    counter = 0
    previous = -2
    for i, segment in enumerate(segments):
        if segment < 0:
            counter = 0
            previous = -2
            continue
        counter = counter + 1 if segment == previous else 1
        previous = segment
        counts[i] = counter
    return counts


# --------------------------------------------------------------------------
# 结果类型
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class IndicatorFrameV2:
    """V2 指标输出：稳定输出名 + ready/segment + 预热与版本身份。"""

    indicator_id: str
    version: str
    index: pd.Index
    columns: Dict[str, pd.Series]
    ready: pd.Series
    segment: pd.Series
    warmup_bars: int

    def value(self, name: str) -> pd.Series:
        if name not in self.columns:
            raise IndicatorInputError(f"UNKNOWN_OUTPUT:{self.indicator_id}:{name}")
        return self.columns[name]

    @property
    def output_names(self) -> list:
        return list(self.columns)

    def to_frame(self) -> pd.DataFrame:
        frame = pd.DataFrame(dict(self.columns), index=self.index)
        frame["__ready__"] = self.ready.to_numpy()
        frame["__segment__"] = self.segment.to_numpy()
        return frame

    def as_v1(self) -> IndicatorFrameV1:
        """与 V1 帧互操作（V1 只有单一 version 字段）。"""
        return IndicatorFrameV1(
            version=self.version, index=self.index, columns=dict(self.columns),
            ready=self.ready, segment=self.segment,
        )


# --------------------------------------------------------------------------
# 输入准备
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceInput:
    """已校验的输入序列。缺失字段为 None，调用方必须显式拒绝依赖它的指标。"""

    index: pd.Index
    close: np.ndarray
    high: Optional[np.ndarray]
    low: Optional[np.ndarray]
    open_price: Optional[np.ndarray]
    volume: Optional[np.ndarray]
    amount: Optional[np.ndarray]
    prev_close: Optional[np.ndarray]

    @property
    def close_valid(self) -> np.ndarray:
        return np.isfinite(self.close) & (self.close > 0)

    @property
    def close_segments(self) -> np.ndarray:
        return segment_ids(self.close_valid)

    def ohlc_valid(self) -> np.ndarray:
        if self.high is None or self.low is None:
            raise IndicatorInputError("MISSING_OHLC_FIELD")
        valid = (
            self.close_valid
            & np.isfinite(self.high) & (self.high > 0)
            & np.isfinite(self.low) & (self.low > 0)
            & (self.high >= self.low)
        )
        return valid


def make_price_input(
    close: pd.Series,
    *,
    high: Optional[pd.Series] = None,
    low: Optional[pd.Series] = None,
    open_: Optional[pd.Series] = None,
    volume: Optional[pd.Series] = None,
    amount: Optional[pd.Series] = None,
    prev_close: Optional[pd.Series] = None,
) -> PriceInput:
    index = _require_time_index(close.index)

    def _optional(series: Optional[pd.Series], name: str) -> Optional[np.ndarray]:
        if series is None:
            return None
        if not series.index.equals(close.index):
            raise IndicatorInputError(f"INDEX_MISMATCH:{name}")
        return _numeric(series, name)

    values = _numeric(close, "close")
    prev = _optional(prev_close, "prev_close")
    if prev is None:
        prev = np.concatenate(([np.nan], values[:-1])) if len(values) else values
    return PriceInput(
        index=index, close=values,
        high=_optional(high, "high"), low=_optional(low, "low"),
        open_price=_optional(open_, "open"), volume=_optional(volume, "volume"),
        amount=_optional(amount, "amount"), prev_close=prev,
    )


def _frame(
    indicator_id: str,
    version: str,
    index: pd.Index,
    columns: Dict[str, np.ndarray],
    ready: np.ndarray,
    segments: np.ndarray,
    warmup_bars: int,
) -> IndicatorFrameV2:
    return IndicatorFrameV2(
        indicator_id=indicator_id, version=version, index=index,
        columns={name: pd.Series(values, index=index) for name, values in columns.items()},
        ready=pd.Series(ready, index=index), segment=pd.Series(segments, index=index),
        warmup_bars=int(warmup_bars),
    )


def _require_volume(data: PriceInput, indicator_id: str) -> np.ndarray:
    if data.volume is None:
        raise IndicatorInputError(f"DATA_DEPENDENCY_NOT_MET:{indicator_id}:volume")
    return data.volume


def _require_amount(data: PriceInput, indicator_id: str) -> np.ndarray:
    if data.amount is None:
        raise IndicatorInputError(f"DATA_DEPENDENCY_NOT_MET:{indicator_id}:amount")
    return data.amount


def _require_high_low(data: PriceInput, indicator_id: str):
    if data.high is None or data.low is None:
        raise IndicatorInputError(f"DATA_DEPENDENCY_NOT_MET:{indicator_id}:high_low")
    return data.high, data.low


# ==========================================================================
# 家族一：均线与趋势
# ==========================================================================


def ma_arithmetic(data: PriceInput, *, window: int = 20, price: str = "close") -> IndicatorFrameV2:
    """算术移动平均（MA/SMA）。窗口含当前 bar，要求窗口内全部合格。"""
    window = _require_positive_int(window, "window")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    out = _rolling_in_segments(values, segments, window, window, "mean")
    ready = np.isfinite(out)
    return _frame("MA_ARITHMETIC", "MA_ARITHMETIC_V1", data.index,
                  {"ma": out}, ready, segments, window)


def sma_tdx(data: PriceInput, *, window: int = 9, weight: int = 1, price: str = "close") -> IndicatorFrameV2:
    """通达信 SMA(X, N, M)：``Y = (M*X + (N-M)*Y_prev) / N``。

    与算术移动平均是**不同公式**，因此独立命名、独立版本。
    段首 ``Y_prev`` 取该段首个合格值（等价于从首值起递推）。
    """
    window = _require_positive_int(window, "window")
    weight = _require_positive_int(weight, "weight")
    if weight > window:
        raise IndicatorInputError("SMA_WEIGHT_EXCEEDS_WINDOW")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    counts = _segment_count(segments)

    def step(previous, value):
        if previous is None:
            return value
        return (weight * value + (window - weight) * previous) / window

    out = _recursive_in_segments(values, segments, step)
    ready = (counts >= window) & np.isfinite(out)
    return _frame("SMA_TDX", "SMA_TDX_V1", data.index,
                  {"sma_tdx": out}, ready, segments, window)


def ema(data: PriceInput, *, window: int = 12, price: str = "close") -> IndicatorFrameV2:
    """指数移动平均。段内首值 = 该段首个合格输入值（与 V1 ``ema_recursive`` 同口径）。"""
    window = _require_positive_int(window, "window")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    out = np.full(len(values), np.nan)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        if positions.size:
            out[positions] = ema_recursive(values[positions], window)
    counts = _segment_count(segments)
    ready = (counts >= window) & np.isfinite(out)
    return _frame("EMA", "EMA_V1", data.index, {"ema": out}, ready, segments, window)


def wma(data: PriceInput, *, window: int = 20, price: str = "close") -> IndicatorFrameV2:
    """线性加权移动平均：权重 1..N，最近的 bar 权重最大。"""
    window = _require_positive_int(window, "window")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    out = np.full(len(values), np.nan)
    weights = np.arange(1, window + 1, dtype=float)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        for offset in range(window - 1, positions.size):
            chunk = values[positions[offset - window + 1 : offset + 1]]
            out[positions[offset]] = float(np.dot(chunk, weights) / weights.sum())
    ready = np.isfinite(out)
    return _frame("WMA", "WMA_V1", data.index, {"wma": out}, ready, segments, window)


def rma_wilder(data: PriceInput, *, window: int = 14, price: str = "close") -> IndicatorFrameV2:
    """Wilder 平滑（RMA）：alpha = 1/N。

    段首值 = 该段前 N 根的算术均值（Wilder 原始定义）；不足 N 根不输出。
    与 ``ema``（alpha = 2/(N+1)）是不同公式。
    """
    window = _require_positive_int(window, "window")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    out = np.full(len(values), np.nan)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        if positions.size < window:
            continue
        seed = float(values[positions[:window]].mean())
        out[positions[window - 1]] = seed
        previous = seed
        for offset in range(window, positions.size):
            previous = (previous * (window - 1) + values[positions[offset]]) / window
            out[positions[offset]] = previous
    ready = np.isfinite(out)
    return _frame("RMA_WILDER", "RMA_WILDER_V1", data.index, {"rma": out}, ready, segments, window)


def dema(data: PriceInput, *, window: int = 20, price: str = "close") -> IndicatorFrameV2:
    """双重指数移动平均：``DEMA = 2*EMA - EMA(EMA)``。"""
    window = _require_positive_int(window, "window")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    first = np.full(len(values), np.nan)
    second = np.full(len(values), np.nan)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        if not positions.size:
            continue
        e1 = ema_recursive(values[positions], window)
        first[positions] = e1
        e2 = ema_recursive(e1, window)
        second[positions] = e2
    out = 2.0 * first - second
    counts = _segment_count(segments)
    ready = (counts >= 2 * window - 1) & np.isfinite(out)
    return _frame("DEMA", "DEMA_V1", data.index,
                  {"dema": out, "ema1": first, "ema2": second}, ready, segments, 2 * window - 1)


def tema(data: PriceInput, *, window: int = 20, price: str = "close") -> IndicatorFrameV2:
    """三重指数移动平均：``TEMA = 3*E1 - 3*E2 + E3``。"""
    window = _require_positive_int(window, "window")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    e1 = np.full(len(values), np.nan)
    e2 = np.full(len(values), np.nan)
    e3 = np.full(len(values), np.nan)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        if not positions.size:
            continue
        a = ema_recursive(values[positions], window)
        b = ema_recursive(a, window)
        c = ema_recursive(b, window)
        e1[positions], e2[positions], e3[positions] = a, b, c
    out = 3.0 * e1 - 3.0 * e2 + e3
    counts = _segment_count(segments)
    ready = (counts >= 3 * window - 2) & np.isfinite(out)
    return _frame("TEMA", "TEMA_V1", data.index,
                  {"tema": out, "ema1": e1, "ema2": e2, "ema3": e3}, ready, segments, 3 * window - 2)


def macd_hist_raw(data: PriceInput, *, fast: int = 12, slow: int = 26, signal: int = 9) -> IndicatorFrameV2:
    """MACD 柱的**另一方言**：``HIST_RAW = DIF - DEA``（不含 2 倍系数）。

    与 ``MACD_V1`` 的 ``HIST = 2*(DIF-DEA)`` 并存，独立命名以免混淆。
    """
    from .indicators_v1 import macd_v1

    fast = _require_positive_int(fast, "fast")
    slow = _require_positive_int(slow, "slow")
    signal = _require_positive_int(signal, "signal")
    if fast >= slow:
        raise IndicatorInputError("FAST_MUST_BE_LESS_THAN_SLOW")
    v1 = macd_v1(pd.Series(data.close, index=data.index), fast=fast, slow=slow, signal=signal)
    dif = v1.value("dif").to_numpy(dtype=float)
    dea = v1.value("dea").to_numpy(dtype=float)
    hist_raw = dif - dea
    return _frame("MACD_HIST_RAW", "MACD_HIST_RAW_V1", data.index,
                  {"dif": dif, "dea": dea, "hist_raw": hist_raw},
                  v1.ready.to_numpy(dtype=bool), v1.segment.to_numpy(), slow + signal - 1)


def dmi_adx(data: PriceInput, *, window: int = 14) -> IndicatorFrameV2:
    """DMI / ADX（Wilder 原始口径）。

    ``TR = max(H-L, |H-PC|, |L-PC|)``；
    ``+DM = H-PH`` 若 ``H-PH > PL-L`` 且 ``> 0``，否则 0；``-DM`` 对称；
    三者用 Wilder RMA(N) 平滑；``+DI = 100*RMA(+DM)/RMA(TR)``；
    ``DX = 100*|+DI - -DI|/(+DI + -DI)``；``ADX = RMA(DX, N)``。

    预热：``+DI/-DI`` 需 N 根；``ADX`` 需约 ``2N-1`` 根。
    """
    window = _require_positive_int(window, "window")
    high, low = _require_high_low(data, "DMI_ADX")
    valid = data.ohlc_valid() & np.isfinite(data.prev_close) & (data.prev_close > 0)
    segments = segment_ids(valid)

    tr = np.full(len(high), np.nan)
    plus_dm = np.full(len(high), np.nan)
    minus_dm = np.full(len(high), np.nan)
    for i in range(len(high)):
        if not valid[i]:
            continue
        up_move = high[i] - high[i - 1] if i > 0 and valid[i - 1] else np.nan
        down_move = low[i - 1] - low[i] if i > 0 and valid[i - 1] else np.nan
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - data.prev_close[i]),
            abs(low[i] - data.prev_close[i]),
        )
        plus_dm[i] = up_move if np.isfinite(up_move) and up_move > 0 and (
            not np.isfinite(down_move) or up_move > down_move) else 0.0
        minus_dm[i] = down_move if np.isfinite(down_move) and down_move > 0 and (
            not np.isfinite(up_move) or down_move > up_move) else 0.0

    atr = _wilder_smooth(tr, segments, window)
    smooth_plus = _wilder_smooth(plus_dm, segments, window)
    smooth_minus = _wilder_smooth(minus_dm, segments, window)

    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * np.where(atr > 0, smooth_plus / atr, np.nan)
        minus_di = 100.0 * np.where(atr > 0, smooth_minus / atr, np.nan)
        denom = plus_di + minus_di
        dx = 100.0 * np.where(denom > 0, np.abs(plus_di - minus_di) / denom, np.nan)
    adx = _wilder_smooth(dx, segments, window)
    counts = _segment_count(segments)
    ready = (counts >= 2 * window) & np.isfinite(adx)
    return _frame("DMI_ADX", "DMI_ADX_V1", data.index, {
        "plus_di": plus_di, "minus_di": minus_di, "adx": adx, "atr": atr,
    }, ready, segments, 2 * window)


def _wilder_smooth(values: np.ndarray, segments: np.ndarray, window: int) -> np.ndarray:
    """对已分段序列做 Wilder RMA：段首 = 前 N 个合格值之和（Wilder 原始 TR 累加口径）。"""
    out = np.full(len(values), np.nan)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        usable = positions[np.isfinite(values[positions])]
        if usable.size < window:
            continue
        seed = float(np.nansum(values[usable[:window]]))
        out[usable[window - 1]] = seed
        previous = seed
        for offset in range(window, usable.size):
            previous = previous - previous / window + values[usable[offset]]
            out[usable[offset]] = previous
    return out


def sar(data: PriceInput, *, step: float = 0.02, max_step: float = 0.2) -> IndicatorFrameV2:
    """抛物线 SAR。

    初始趋势由前两根确定；初始极值取该方向上的极值；AF 从 ``step`` 起，
    每次创新极值加 ``step``，上限 ``max_step``；反转时 AF 重置。
    价格约束：上升趋势 SAR 不得高于前两根最低价，下降趋势不得低于前两根最高价。
    """
    if not (0 < float(step) <= float(max_step) < 1):
        raise IndicatorInputError("INVALID_SAR_PARAMETER")
    high, low = _require_high_low(data, "SAR")
    valid = data.ohlc_valid()
    segments = segment_ids(valid)
    out = np.full(len(high), np.nan)
    trend_flag = np.zeros(len(high), dtype=int)   # 1 上升 / -1 下降

    for segment in range(int(segments.max()) + 1) if len(high) else []:
        positions = np.flatnonzero(segments == segment)
        if positions.size < 2:
            continue
        first, second = positions[0], positions[1]
        rising = high[second] >= high[first]
        extreme = high[first] if rising else low[first]
        sar_value = low[first] if rising else high[first]
        af = float(step)
        out[first] = sar_value
        trend_flag[first] = 1 if rising else -1
        for offset in range(1, positions.size):
            i = positions[offset]
            previous_i = positions[offset - 1]
            sar_value = sar_value + af * (extreme - sar_value)
            if rising:
                if offset >= 2:
                    sar_value = min(sar_value, low[positions[offset - 1]], low[positions[offset - 2]])
                else:
                    sar_value = min(sar_value, low[previous_i])
                if low[i] < sar_value:
                    rising = False
                    sar_value = extreme
                    extreme = low[i]
                    af = float(step)
                elif high[i] > extreme:
                    extreme = high[i]
                    af = min(af + float(step), float(max_step))
            else:
                if offset >= 2:
                    sar_value = max(sar_value, high[positions[offset - 1]], high[positions[offset - 2]])
                else:
                    sar_value = max(sar_value, high[previous_i])
                if high[i] > sar_value:
                    rising = True
                    sar_value = extreme
                    extreme = high[i]
                    af = float(step)
                elif low[i] < extreme:
                    extreme = low[i]
                    af = min(af + float(step), float(max_step))
            out[i] = sar_value
            trend_flag[i] = 1 if rising else -1
    counts = _segment_count(segments)
    ready = (counts >= 2) & np.isfinite(out)
    return _frame("SAR", "SAR_V1", data.index,
                  {"sar": out, "sar_trend": trend_flag.astype(float)}, ready, segments, 2)


# ==========================================================================
# 家族二：动量与震荡
# ==========================================================================


def rsi(data: PriceInput, *, window: int = 14, price: str = "close") -> IndicatorFrameV2:
    """Wilder RSI。

    - 首值：前 N 个变动的算术平均（gain/loss 分别平均）；
    - 之后 Wilder 递推：``avg = (avg*(N-1) + current) / N``；
    - 边界：``loss == 0 且 gain > 0 -> 100``；``gain == 0 且 loss > 0 -> 0``；
      ``gain == loss == 0``（完全无波动）-> ``50``（中性，明确声明，不返回 NaN）。
    """
    window = _require_positive_int(window, "window")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    out = np.full(len(values), np.nan)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        if positions.size <= window:
            continue
        deltas = np.diff(values[positions])
        gains = np.maximum(deltas, 0.0)
        losses = np.maximum(-deltas, 0.0)
        average_gain = float(gains[:window].mean())
        average_loss = float(losses[:window].mean())
        out[positions[window]] = _rsi_value(average_gain, average_loss)
        for offset in range(window, deltas.size):
            average_gain = (average_gain * (window - 1) + gains[offset]) / window
            average_loss = (average_loss * (window - 1) + losses[offset]) / window
            out[positions[offset + 1]] = _rsi_value(average_gain, average_loss)
    ready = np.isfinite(out)
    return _frame("RSI", "RSI_V1", data.index, {"rsi": out}, ready, segments, window + 1)


def _rsi_value(average_gain: float, average_loss: float) -> float:
    """RSI 边界：无涨无跌 -> 50（中性）；仅涨 -> 100；仅跌 -> 0。

    用容差比较而非浮点相等，避免"极小但非零"的 gain/loss 被误判为 0。
    """
    tolerance = 1e-12
    flat_gain = abs(average_gain) <= tolerance
    flat_loss = abs(average_loss) <= tolerance
    if flat_gain and flat_loss:
        return 50.0
    if flat_loss:
        return 100.0
    if flat_gain:
        return 0.0
    return 100.0 * average_gain / (average_gain + average_loss)


def cci(data: PriceInput, *, window: int = 14) -> IndicatorFrameV2:
    """CCI：``(TP - SMA(TP,N)) / (0.015 * 平均绝对偏差)``，``TP = (H+L+C)/3``。"""
    window = _require_positive_int(window, "window")
    high, low = _require_high_low(data, "CCI")
    valid = data.ohlc_valid()
    segments = segment_ids(valid)
    typical = (high + low + data.close) / 3.0
    mean = _rolling_in_segments(typical, segments, window, window, "mean")
    deviation = np.full(len(typical), np.nan)
    for segment in range(int(segments.max()) + 1) if len(typical) else []:
        positions = np.flatnonzero(segments == segment)
        for offset in range(window - 1, positions.size):
            i = positions[offset]
            chunk = typical[positions[offset - window + 1 : offset + 1]]
            deviation[i] = float(np.abs(chunk - chunk.mean()).mean())
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(deviation > 0, (typical - mean) / (0.015 * deviation), np.nan)
    return _frame("CCI", "CCI_V1", data.index,
                  {"cci": out, "typical_price": typical}, np.isfinite(out), segments, window)


def williams_r(data: PriceInput, *, window: int = 14) -> IndicatorFrameV2:
    """Williams %R，采用通达信 0~100 表达：``(HHV(H,N) - C) / (HHV(H,N) - LLV(L,N)) * 100``。

    值域 0（最强）~100（最弱）；不返回负值形式。分母为零（窗口内高低相同）-> NaN。
    """
    window = _require_positive_int(window, "window")
    high, low = _require_high_low(data, "WILLIAMS_R")
    valid = data.ohlc_valid()
    segments = segment_ids(valid)
    hhv = _rolling_in_segments(high, segments, window, window, "max")
    llv = _rolling_in_segments(low, segments, window, window, "min")
    span = hhv - llv
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(span > 0, (hhv - data.close) / span * 100.0, np.nan)
    return _frame("WILLIAMS_R", "WILLIAMS_R_V1", data.index,
                  {"wr": out, "hhv": hhv, "llv": llv}, np.isfinite(out), segments, window)


def roc(data: PriceInput, *, window: int = 12, price: str = "close") -> IndicatorFrameV2:
    """变动率：``(C / REF(C,N) - 1) * 100``（百分比）。"""
    window = _require_positive_int(window, "window")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    reference = np.full(len(values), np.nan)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        if positions.size > window:
            reference[positions[window:]] = values[positions[:-window]]
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(reference > 0, (values / reference - 1.0) * 100.0, np.nan)
    return _frame("ROC", "ROC_V1", data.index,
                  {"roc": out, "ref_close": reference}, np.isfinite(out), segments, window + 1)


def mtm(data: PriceInput, *, window: int = 12, price: str = "close") -> IndicatorFrameV2:
    """动量（MOM/MTM）：``C - REF(C,N)``，价格单位。"""
    window = _require_positive_int(window, "window")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    reference = np.full(len(values), np.nan)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        if positions.size > window:
            reference[positions[window:]] = values[positions[:-window]]
    out = values - reference
    return _frame("MTM", "MTM_V1", data.index,
                  {"mtm": out, "ref_close": reference}, np.isfinite(out), segments, window + 1)


def bias(data: PriceInput, *, window: int = 6, price: str = "close") -> IndicatorFrameV2:
    """乖离率：``(C - MA(C,N)) / MA(C,N) * 100``。"""
    window = _require_positive_int(window, "window")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    mean = _rolling_in_segments(values, segments, window, window, "mean")
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(mean > 0, (values - mean) / mean * 100.0, np.nan)
    return _frame("BIAS", "BIAS_V1", data.index,
                  {"bias": out, "ma": mean}, np.isfinite(out), segments, window)


def trix(data: PriceInput, *, window: int = 12, signal: int = 9, price: str = "close") -> IndicatorFrameV2:
    """TRIX：三重 EMA 的单期百分比变动，再取 M 期 EMA 作为信号线。"""
    window = _require_positive_int(window, "window")
    signal = _require_positive_int(signal, "signal")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    triple = np.full(len(values), np.nan)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        if not positions.size:
            continue
        a = ema_recursive(values[positions], window)
        b = ema_recursive(a, window)
        c = ema_recursive(b, window)
        triple[positions] = c
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(
            np.isfinite(triple) & (np.concatenate(([np.nan], triple[:-1])) > 0),
            (triple / np.concatenate(([np.nan], triple[:-1])) - 1.0) * 100.0, np.nan,
        )
    # 信号线在同一段内对 TRIX 做滚动均值；段内 NaN 位置不参与，不跨段。
    ma = _rolling_in_segments(out, segments, signal, signal, "mean")
    counts = _segment_count(segments)
    ready = (counts >= 3 * window + signal) & np.isfinite(ma)
    return _frame("TRIX", "TRIX_V1", data.index,
                  {"trix": out, "trix_ma": ma}, ready, segments, 3 * window + signal)


def _rolling_values(values: np.ndarray, window: int, min_periods: int, method: str) -> np.ndarray:
    series = pd.Series(values, dtype=float)
    roll = series.rolling(window, min_periods=min_periods)
    computed = {"mean": roll.mean, "std": roll.std, "sum": roll.sum,
                "max": roll.max, "min": roll.min}[method]()
    return computed.to_numpy(dtype=float)


def psy(data: PriceInput, *, window: int = 12, price: str = "close") -> IndicatorFrameV2:
    """PSY：N 根中上涨根数占比（%）。"""
    window = _require_positive_int(window, "window")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    previous = np.concatenate(([np.nan], values[:-1]))
    up = np.where(np.isfinite(previous) & (values > previous), 1.0, 0.0)
    up[~np.isfinite(previous)] = np.nan
    counts = _rolling_in_segments(up, segments, window, window, "count")
    totals = _rolling_in_segments(np.where(np.isfinite(up), 1.0, np.nan), segments, window, window, "sum")
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(totals > 0, counts / totals * 100.0, np.nan)
    return _frame("PSY", "PSY_V1", data.index, {"psy": out}, np.isfinite(out), segments, window + 1)


# ==========================================================================
# 家族三：波动与通道
# ==========================================================================


def true_range(data: PriceInput) -> IndicatorFrameV2:
    """真实波幅：``max(H-L, |H-PC|, |L-PC|)``。

    段首（或前收盘不可用时）：按惯例取 ``H-L``，不因缺少前收盘而丢弃该根。
    这使 ATR 的预热窗口与常规实现一致（种子用前 N 根的 TR，含首根）。
    """
    high, low = _require_high_low(data, "TRUE_RANGE")
    valid = data.ohlc_valid()
    segments = segment_ids(valid)
    tr = np.full(len(high), np.nan)
    for i in range(len(high)):
        if not valid[i]:
            continue
        previous_close = data.prev_close[i]
        if np.isfinite(previous_close) and previous_close > 0:
            tr[i] = max(high[i] - low[i], abs(high[i] - previous_close),
                        abs(low[i] - previous_close))
        else:
            tr[i] = high[i] - low[i]
    return _frame("TRUE_RANGE", "TRUE_RANGE_V1", data.index, {"tr": tr}, np.isfinite(tr), segments, 1)


def atr(data: PriceInput, *, window: int = 14) -> IndicatorFrameV2:
    """ATR：对真实波幅做 Wilder 平滑（``_wilder_smooth``，与 DMI 共用同一内核）。"""
    window = _require_positive_int(window, "window")
    tr_frame = true_range(data)
    tr = tr_frame.value("tr").to_numpy(dtype=float)
    segments = tr_frame.segment.to_numpy()
    smoothed = _wilder_smooth(tr, segments, window)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = smoothed / window
    ready = np.isfinite(out)
    return _frame("ATR", "ATR_V1", data.index,
                  {"atr": out, "tr": tr}, ready, segments, window)


def natr(data: PriceInput, *, window: int = 14) -> IndicatorFrameV2:
    """NATR：``ATR / C * 100``（百分比，跨证券可比）。"""
    window = _require_positive_int(window, "window")
    atr_frame = atr(data, window=window)
    values = atr_frame.value("atr").to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(data.close > 0, values / data.close * 100.0, np.nan)
    return _frame("NATR", "NATR_V1", data.index,
                  {"natr": out, "atr": values}, np.isfinite(out),
                  atr_frame.segment.to_numpy(), window)


def rolling_volatility(data: PriceInput, *, window: int = 20, price: str = "close",
                       annualize: int = 0, ddof: int = 1) -> IndicatorFrameV2:
    """滚动波动率：**基于收益率**的标准差。

    ``annualize=0`` 返回单期标准差；``annualize=252`` 返回年化值。
    与基于价格水平的 ``BOLL`` 带宽是不同口径。
    """
    window = _require_positive_int(window, "window")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    returns = np.full(len(values), np.nan)
    previous = np.concatenate(([np.nan], values[:-1]))
    with np.errstate(divide="ignore", invalid="ignore"):
        returns = np.where(np.isfinite(previous) & (previous > 0), values / previous - 1.0, np.nan)
    out = _rolling_in_segments(returns, segments, window, window, "std", ddof=ddof)
    if annualize:
        out = out * float(np.sqrt(annualize))
    return _frame("ROLLING_VOLATILITY", "ROLLING_VOLATILITY_V1", data.index,
                  {"volatility": out, "return": returns}, np.isfinite(out), segments, window + 1)


def bollinger(data: PriceInput, *, window: int = 20, multiplier: float = 2.0,
              ddof: int = 0, price: str = "close") -> IndicatorFrameV2:
    """BOLL：中轨 = 算术均线；上下轨 = 中轨 ± k*标准差。

    ``ddof`` 默认 0（总体标准差）；显式传 1 得到样本标准差。二者是不同口径，
    因此 ``ddof`` 进入参数身份。
    """
    window = _require_positive_int(window, "window")
    if not (float(multiplier) > 0):
        raise IndicatorInputError("INVALID_BOLL_MULTIPLIER")
    if ddof not in (0, 1):
        raise IndicatorInputError("INVALID_BOLL_DDOF")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    middle = _rolling_in_segments(values, segments, window, window, "mean")
    deviation = _rolling_in_segments(values, segments, window, window, "std", ddof=ddof)
    upper = middle + float(multiplier) * deviation
    lower = middle - float(multiplier) * deviation
    span = upper - lower
    with np.errstate(divide="ignore", invalid="ignore"):
        bandwidth = np.where(middle > 0, span / middle, np.nan)
        percent_b = np.where(span > 0, (values - lower) / span, np.nan)
    ready = np.isfinite(middle)
    return _frame("BOLLINGER", "BOLLINGER_V1", data.index, {
        "middle": middle, "upper": upper, "lower": lower,
        "bandwidth": bandwidth, "percent_b": percent_b, "std": deviation,
    }, ready, segments, window)


def donchian(data: PriceInput, *, window: int = 20, shift: int = 0) -> IndicatorFrameV2:
    """Donchian 通道：``upper = HHV(H,N)``，``lower = LLV(L,N)``。

    ``shift`` 用于突破口径：``shift=1`` 表示参考**前 N 根**（不含当前 bar）的通道，
    从而"当根突破前序通道"可以被明确表达，而不是把当根也算进窗口。
    """
    window = _require_positive_int(window, "window")
    if int(shift) < 0:
        raise IndicatorInputError("NEGATIVE_SHIFT_FORBIDDEN")
    high, low = _require_high_low(data, "DONCHIAN")
    valid = data.ohlc_valid()
    segments = segment_ids(valid)
    upper = _rolling_in_segments(high, segments, window, window, "max")
    lower = _rolling_in_segments(low, segments, window, window, "min")
    if shift:
        upper = _shift_in_segments(upper, segments, int(shift))
        lower = _shift_in_segments(lower, segments, int(shift))
    middle = (upper + lower) / 2.0
    return _frame("DONCHIAN", "DONCHIAN_V1", data.index,
                  {"upper": upper, "lower": lower, "middle": middle},
                  np.isfinite(upper) & np.isfinite(lower), segments, window + int(shift))


def keltner(data: PriceInput, *, window: int = 20, atr_window: int = 10,
            multiplier: float = 2.0) -> IndicatorFrameV2:
    """Keltner 通道：中轨 = EMA(close)，上下轨 = 中轨 ± k*ATR。"""
    window = _require_positive_int(window, "window")
    atr_frame = atr(data, window=_require_positive_int(atr_window, "atr_window"))
    atr_values = atr_frame.value("atr").to_numpy(dtype=float)
    ema_frame = ema(data, window=window)
    middle = ema_frame.value("ema").to_numpy(dtype=float)
    upper = middle + float(multiplier) * atr_values
    lower = middle - float(multiplier) * atr_values
    ready = np.isfinite(middle) & np.isfinite(atr_values)
    return _frame("KELTNER", "KELTNER_V1", data.index,
                  {"upper": upper, "middle": middle, "lower": lower, "atr": atr_values},
                  ready, atr_frame.segment.to_numpy(), max(window, atr_window))


def _shift_in_segments(values: np.ndarray, segments: np.ndarray, periods: int) -> np.ndarray:
    """按段右移（取前 periods 根）。段内不足则 NaN，不跨段。"""
    if periods < 0:
        raise IndicatorInputError("NEGATIVE_LAG_FORBIDDEN")
    out = np.full(len(values), np.nan)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        if positions.size > periods:
            out[positions[periods:]] = values[positions[:-periods]]
    return out


# ==========================================================================
# 家族四：量价与资金代理
# ==========================================================================


def volume_ma(data: PriceInput, *, window: int = 20) -> IndicatorFrameV2:
    """成交量均线。"""
    window = _require_positive_int(window, "window")
    volume = _require_volume(data, "VOLUME_MA")
    valid = np.isfinite(volume) & (volume >= 0)
    segments = segment_ids(valid)
    out = _rolling_in_segments(volume, segments, window, window, "mean")
    return _frame("VOLUME_MA", "VOLUME_MA_V1", data.index,
                  {"volume_ma": out}, np.isfinite(out), segments, window)


def amount_ma(data: PriceInput, *, window: int = 20) -> IndicatorFrameV2:
    """成交额均线（与成交量是不同单位，独立命名）。"""
    window = _require_positive_int(window, "window")
    amount = _require_amount(data, "AMOUNT_MA")
    valid = np.isfinite(amount) & (amount >= 0)
    segments = segment_ids(valid)
    out = _rolling_in_segments(amount, segments, window, window, "mean")
    return _frame("AMOUNT_MA", "AMOUNT_MA_V1", data.index,
                  {"amount_ma": out}, np.isfinite(out), segments, window)


def rvol_incl_current(data: PriceInput, *, window: int = 20) -> IndicatorFrameV2:
    """相对成交量（分母**含当前 bar**）：``V / MA(V,N)``。"""
    window = _require_positive_int(window, "window")
    volume = _require_volume(data, "RVOL_INCL_CURRENT")
    valid = np.isfinite(volume) & (volume >= 0)
    segments = segment_ids(valid)
    mean = _rolling_in_segments(volume, segments, window, window, "mean")
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(mean > 0, volume / mean, np.nan)
    return _frame("RVOL_INCL_CURRENT", "RVOL_INCL_CURRENT_V1", data.index,
                  {"rvol": out, "volume_ma": mean}, np.isfinite(out), segments, window)


def rvol_prior(data: PriceInput, *, window: int = 20) -> IndicatorFrameV2:
    """相对成交量（分母为**前 N 根**，不含当前 bar）：``V / MA(V[t-N..t-1])``。

    与 ``RVOL_INCL_CURRENT`` 是不同口径（前者把当根放量计入分母，会自我稀释），
    因此独立命名、独立版本。
    """
    window = _require_positive_int(window, "window")
    volume = _require_volume(data, "RVOL_PRIOR")
    valid = np.isfinite(volume) & (volume >= 0)
    segments = segment_ids(valid)
    mean = _rolling_in_segments(volume, segments, window, window, "mean")
    shifted = _shift_in_segments(mean, segments, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(shifted > 0, volume / shifted, np.nan)
    return _frame("RVOL_PRIOR", "RVOL_PRIOR_V1", data.index,
                  {"rvol": out, "volume_ma_prior": shifted}, np.isfinite(out), segments, window + 1)


def obv(data: PriceInput) -> IndicatorFrameV2:
    """OBV：``OBV_t = OBV_{t-1} + sign(C_t - C_{t-1}) * V_t``；段首为 0。"""
    volume = _require_volume(data, "OBV")
    values = data.close
    valid = data.close_valid & np.isfinite(volume) & (volume >= 0)
    segments = segment_ids(valid)
    out = np.full(len(values), np.nan)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        if not positions.size:
            continue
        running = 0.0
        out[positions[0]] = running
        for offset in range(1, positions.size):
            i, previous_i = positions[offset], positions[offset - 1]
            difference = values[i] - values[previous_i]
            running += np.sign(difference) * volume[i]
            out[positions[offset]] = running
    counts = _segment_count(segments)
    return _frame("OBV", "OBV_V1", data.index, {"obv": out},
                  (counts >= 1) & np.isfinite(out), segments, 1)


def mfi(data: PriceInput, *, window: int = 14) -> IndicatorFrameV2:
    """资金流量指标 MFI：基于典型价与成交量的**公式代理**，不是实际主力净买卖。"""
    window = _require_positive_int(window, "window")
    high, low = _require_high_low(data, "MFI")
    volume = _require_volume(data, "MFI")
    valid = data.ohlc_valid() & np.isfinite(volume) & (volume >= 0)
    segments = segment_ids(valid)
    typical = (high + low + data.close) / 3.0
    raw_flow = typical * volume
    previous = np.concatenate(([np.nan], typical[:-1]))
    positive = np.where(np.isfinite(previous) & (typical > previous), raw_flow, 0.0)
    negative = np.where(np.isfinite(previous) & (typical < previous), raw_flow, 0.0)
    positive[~np.isfinite(previous)] = np.nan
    negative[~np.isfinite(previous)] = np.nan
    positive_sum = _rolling_in_segments(positive, segments, window, window, "sum")
    negative_sum = _rolling_in_segments(negative, segments, window, window, "sum")
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(negative_sum > 0, positive_sum / negative_sum, np.nan)
        out = np.where(
            np.isfinite(positive_sum) & np.isfinite(negative_sum),
            np.where(negative_sum == 0, np.where(positive_sum > 0, 100.0, 50.0),
                     100.0 - 100.0 / (1.0 + ratio)),
            np.nan,
        )
    return _frame("MFI", "MFI_V1", data.index,
                  {"mfi": out, "typical_price": typical}, np.isfinite(out), segments, window + 1)


def accumulation_distribution(data: PriceInput) -> IndicatorFrameV2:
    """A/D 线：累积 ``((C-L)-(H-C))/(H-L) * V``；窗口内高低相同当根贡献 0。"""
    high, low = _require_high_low(data, "ACCUMULATION_DISTRIBUTION")
    volume = _require_volume(data, "ACCUMULATION_DISTRIBUTION")
    valid = data.ohlc_valid() & np.isfinite(volume) & (volume >= 0)
    segments = segment_ids(valid)
    span = high - low
    multiplier = np.where(span > 0, ((data.close - low) - (high - data.close)) / np.where(span > 0, span, 1.0), 0.0)
    flow = multiplier * volume
    out = np.full(len(high), np.nan)
    for segment in range(int(segments.max()) + 1) if len(high) else []:
        positions = np.flatnonzero(segments == segment)
        if positions.size:
            out[positions] = np.cumsum(flow[positions])
    return _frame("ACCUMULATION_DISTRIBUTION", "ACCUMULATION_DISTRIBUTION_V1", data.index,
                  {"ad_line": out, "money_flow_volume": flow}, np.isfinite(out), segments, 1)


def chaikin_money_flow(data: PriceInput, *, window: int = 20) -> IndicatorFrameV2:
    """CMF：``sum(MFV, N) / sum(V, N)``；成交量合计为零 -> NaN。"""
    window = _require_positive_int(window, "window")
    ad_frame = accumulation_distribution(data)
    flow = ad_frame.value("money_flow_volume").to_numpy(dtype=float)
    volume = _require_volume(data, "CHAIKIN_MONEY_FLOW")
    segments = ad_frame.segment.to_numpy()
    flow_sum = _rolling_in_segments(flow, segments, window, window, "sum")
    volume_sum = _rolling_in_segments(np.where(np.isfinite(volume), volume, np.nan),
                                      segments, window, window, "sum")
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(volume_sum > 0, flow_sum / volume_sum, np.nan)
    return _frame("CHAIKIN_MONEY_FLOW", "CHAIKIN_MONEY_FLOW_V1", data.index,
                  {"cmf": out, "mfv_sum": flow_sum, "volume_sum": volume_sum},
                  np.isfinite(out), segments, window)


def pvt(data: PriceInput) -> IndicatorFrameV2:
    """PVT：累积 ``pct_change(C) * V``。"""
    volume = _require_volume(data, "PVT")
    values = data.close
    valid = data.close_valid & np.isfinite(volume) & (volume >= 0)
    segments = segment_ids(valid)
    out = np.full(len(values), np.nan)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        if not positions.size:
            continue
        out[positions[0]] = 0.0
        running = 0.0
        for offset in range(1, positions.size):
            i, previous_i = positions[offset], positions[offset - 1]
            running += (values[i] / values[previous_i] - 1.0) * volume[i]
            out[positions[offset]] = running
    return _frame("PVT", "PVT_V1", data.index, {"pvt": out}, np.isfinite(out), segments, 1)


def vwap_session_proxy(data: PriceInput) -> IndicatorFrameV2:
    """**session 成交均价代理**：``amount / volume``。

    这是"日线成交额/成交量"的成交均价，**不是**真实 VWAP，也不等于 HLC3 代理，
    更不等于滚动 VWAP。三者必须使用不同名称（见 ``hlc3`` 与 ``rolling_vwap``）。
    """
    amount = _require_amount(data, "VWAP_SESSION_PROXY")
    volume = _require_volume(data, "VWAP_SESSION_PROXY")
    valid = np.isfinite(amount) & np.isfinite(volume) & (volume > 0) & (amount >= 0)
    segments = segment_ids(valid)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(volume > 0, amount / volume, np.nan)
    return _frame("VWAP_SESSION_PROXY", "VWAP_SESSION_PROXY_V1", data.index,
                  {"vwap_session_proxy": out}, np.isfinite(out), segments, 1)


def hlc3(data: PriceInput) -> IndicatorFrameV2:
    """HLC3 典型价代理：``(H+L+C)/3``。与成交均价、VWAP 是不同口径。"""
    high, low = _require_high_low(data, "HLC3")
    valid = data.ohlc_valid()
    segments = segment_ids(valid)
    out = (high + low + data.close) / 3.0
    return _frame("HLC3", "HLC3_V1", data.index, {"hlc3": out}, np.isfinite(out), segments, 1)


def rolling_vwap(data: PriceInput, *, window: int = 20) -> IndicatorFrameV2:
    """滚动 VWAP：``sum(amount, N) / sum(volume, N)``。与 session 代理、HLC3 均不同名。"""
    window = _require_positive_int(window, "window")
    amount = _require_amount(data, "ROLLING_VWAP")
    volume = _require_volume(data, "ROLLING_VWAP")
    valid = np.isfinite(amount) & np.isfinite(volume) & (volume >= 0) & (amount >= 0)
    segments = segment_ids(valid)
    amount_sum = _rolling_in_segments(amount, segments, window, window, "sum")
    volume_sum = _rolling_in_segments(volume, segments, window, window, "sum")
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(volume_sum > 0, amount_sum / volume_sum, np.nan)
    return _frame("ROLLING_VWAP", "ROLLING_VWAP_V1", data.index,
                  {"rolling_vwap": out}, np.isfinite(out), segments, window)


def turnover_rate(data: PriceInput, *, float_shares: Optional[pd.Series] = None,
                  window: int = 1) -> IndicatorFrameV2:
    """换手率：``sum(volume, N) / 流通股本 * 100``。

    **依赖额外数据**：需要与成交量同口径的历史流通股本。缺少时明确
    ``DATA_DEPENDENCY_NOT_MET``，不生成随机值/0/当前快照替代。
    """
    window = _require_positive_int(window, "window")
    volume = _require_volume(data, "TURNOVER_RATE")
    if float_shares is None:
        raise IndicatorInputError("DATA_DEPENDENCY_NOT_MET:TURNOVER_RATE:float_shares")
    if not float_shares.index.equals(data.index):
        raise IndicatorInputError("INDEX_MISMATCH:float_shares")
    shares = _numeric(float_shares, "float_shares")
    valid = np.isfinite(volume) & (volume >= 0) & np.isfinite(shares) & (shares > 0)
    segments = segment_ids(valid)
    volume_sum = _rolling_in_segments(volume, segments, window, window, "sum")
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(shares > 0, volume_sum / shares * 100.0, np.nan)
    return _frame("TURNOVER_RATE", "TURNOVER_RATE_V1", data.index,
                  {"turnover_rate": out}, np.isfinite(out), segments, window)


# ==========================================================================
# 家族五：价格结构
# ==========================================================================


def price_extremes(data: PriceInput, *, window: int = 20) -> IndicatorFrameV2:
    """区间高低点：``HHV(H,N)`` / ``LLV(L,N)``（窗口含当前 bar）。"""
    window = _require_positive_int(window, "window")
    high, low = _require_high_low(data, "PRICE_EXTREMES")
    segments = segment_ids(data.ohlc_valid())
    hhv = _rolling_in_segments(high, segments, window, window, "max")
    llv = _rolling_in_segments(low, segments, window, window, "min")
    return _frame("PRICE_EXTREMES", "PRICE_EXTREMES_V1", data.index,
                  {"hhv": hhv, "llv": llv}, np.isfinite(hhv) & np.isfinite(llv), segments, window)


def prior_breakout(data: PriceInput, *, window: int = 20) -> IndicatorFrameV2:
    """前 N 根突破：``prior_high = HHV(H[t-N..t-1])``，``prior_low = LLV(L[t-N..t-1])``。

    明确**不含当前 bar**，因此"当根突破前序区间"可被表达且不产生前视。
    """
    window = _require_positive_int(window, "window")
    high, low = _require_high_low(data, "PRIOR_BREAKOUT")
    segments = segment_ids(data.ohlc_valid())
    prior_high = _shift_in_segments(_rolling_in_segments(high, segments, window, window, "max"), segments, 1)
    prior_low = _shift_in_segments(_rolling_in_segments(low, segments, window, window, "min"), segments, 1)
    return _frame("PRIOR_BREAKOUT", "PRIOR_BREAKOUT_V1", data.index, {
        "prior_high": prior_high, "prior_low": prior_low,
        "break_up": np.where(np.isfinite(prior_high), (data.close > prior_high).astype(float), np.nan),
        "break_down": np.where(np.isfinite(prior_low), (data.close < prior_low).astype(float), np.nan),
    }, np.isfinite(prior_high) & np.isfinite(prior_low), segments, window + 1)


def drawdown_from_peak(data: PriceInput, *, window: int = 60, price: str = "close") -> IndicatorFrameV2:
    """自窗口内最高收盘的回撤（负值或 0）：``C / HHV(C,N) - 1``。"""
    window = _require_positive_int(window, "window")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    peak = _rolling_in_segments(values, segments, window, window, "max")
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(peak > 0, values / peak - 1.0, np.nan)
    return _frame("DRAWDOWN_FROM_PEAK", "DRAWDOWN_FROM_PEAK_V1", data.index,
                  {"drawdown": out, "peak": peak}, np.isfinite(out), segments, window)


def bar_shape(data: PriceInput, *, window: int = 20) -> IndicatorFrameV2:
    """K 线形态比例（相对当根振幅归一，跨证券可比）：

    - ``body_ratio`` = |C-O| / (H-L)
    - ``upper_shadow_ratio`` = (H - max(O,C)) / (H-L)
    - ``lower_shadow_ratio`` = (min(O,C) - L) / (H-L)
    - ``range_pct`` = (H-L) / prev_close * 100
    - ``gap_pct`` = (O - prev_close) / prev_close * 100

    振幅为零（一字）时三个比例返回 0（明确约定，不返回 NaN）。
    """
    _require_positive_int(window, "window")   # 保留参数位以便滚动统计扩展
    if data.open_price is None:
        raise IndicatorInputError("DATA_DEPENDENCY_NOT_MET:BAR_SHAPE:open")
    high, low = _require_high_low(data, "BAR_SHAPE")
    valid = data.ohlc_valid() & np.isfinite(data.open_price) & (data.open_price > 0)
    segments = segment_ids(valid)
    span = high - low
    safe_span = np.where(span > 0, span, np.nan)
    body = np.abs(data.close - data.open_price)
    upper = high - np.maximum(data.open_price, data.close)
    lower = np.minimum(data.open_price, data.close) - low
    with np.errstate(divide="ignore", invalid="ignore"):
        body_ratio = np.where(span > 0, body / safe_span, 0.0)
        upper_ratio = np.where(span > 0, upper / safe_span, 0.0)
        lower_ratio = np.where(span > 0, lower / safe_span, 0.0)
        range_pct = np.where(data.prev_close > 0, span / data.prev_close * 100.0, np.nan)
        gap_pct = np.where(data.prev_close > 0, (data.open_price - data.prev_close) / data.prev_close * 100.0, np.nan)
    return _frame("BAR_SHAPE", "BAR_SHAPE_V1", data.index, {
        "body_ratio": body_ratio, "upper_shadow_ratio": upper_ratio,
        "lower_shadow_ratio": lower_ratio, "range_pct": range_pct, "gap_pct": gap_pct,
    }, valid & np.isfinite(range_pct), segments, 1)


def streak(data: PriceInput, *, price: str = "close") -> IndicatorFrameV2:
    """连续上涨/下跌根数：``up_streak`` / ``down_streak``（平盘重置为 0）。

    结构发生时刻 = 当根收盘；不把未来确认回填到更早的 bar。
    """
    values = _price_field(data, price)
    valid = np.isfinite(values) & (values > 0)
    segments = segment_ids(valid)
    up = np.full(len(values), np.nan)
    down = np.full(len(values), np.nan)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        if not positions.size:
            continue
        up_run = down_run = 0
        up[positions[0]] = 0.0
        down[positions[0]] = 0.0
        for offset in range(1, positions.size):
            i, previous_i = positions[offset], positions[offset - 1]
            if values[i] > values[previous_i]:
                up_run += 1
                down_run = 0
            elif values[i] < values[previous_i]:
                down_run += 1
                up_run = 0
            else:
                up_run = down_run = 0
            up[i] = float(up_run)
            down[i] = float(down_run)
    return _frame("STREAK", "STREAK_V1", data.index,
                  {"up_streak": up, "down_streak": down}, valid, segments, 1)


def price_field(data: PriceInput, name: str) -> np.ndarray:
    return _price_field(data, name)


def _price_field(data: PriceInput, name: str) -> np.ndarray:
    mapping = {"close": data.close, "high": data.high, "low": data.low,
               "open": data.open_price, "prev_close": data.prev_close}
    if name not in mapping:
        raise IndicatorInputError(f"UNKNOWN_PRICE_FIELD:{name}")
    values = mapping[name]
    if values is None:
        raise IndicatorInputError(f"DATA_DEPENDENCY_NOT_MET:price_field:{name}")
    return values


# ==========================================================================
# 家族六：统计与截面组合
# ==========================================================================


def historical_return(data: PriceInput, *, window: int = 20, price: str = "close",
                      log: bool = False) -> IndicatorFrameV2:
    """历史收益：``C/REF(C,N) - 1``（``log=True`` 时为对数收益）。"""
    window = _require_positive_int(window, "window")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    reference = np.full(len(values), np.nan)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        if positions.size > window:
            reference[positions[window:]] = values[positions[:-window]]
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(reference > 0, values / reference - 1.0, np.nan)
        if log:
            out = np.where(reference > 0, np.log(values / reference), np.nan)
    name = "log_return" if log else "return"
    return _frame("HISTORICAL_RETURN", "HISTORICAL_RETURN_V1", data.index,
                  {name: out, "ref_close": reference}, np.isfinite(out), segments, window + 1)


def rolling_correlation(data: PriceInput, other: pd.Series, *, window: int = 60,
                        price: str = "close") -> IndicatorFrameV2:
    """滚动相关系数（本证券价格与另一序列）。样本不足或零方差 -> NaN。"""
    window = _require_positive_int(window, "window")
    values = _price_field(data, price)
    if not other.index.equals(data.index):
        raise IndicatorInputError("INDEX_MISMATCH:other")
    others = _numeric(other, "other")
    valid = np.isfinite(values) & (values > 0) & np.isfinite(others)
    segments = segment_ids(valid)
    out = np.full(len(values), np.nan)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        if positions.size < window:
            continue
        a = pd.Series(values[positions])
        b = pd.Series(others[positions])
        computed = a.rolling(window, min_periods=window).corr(b).to_numpy()
        out[positions] = computed
    return _frame("ROLLING_CORRELATION", "ROLLING_CORRELATION_V1", data.index,
                  {"correlation": out}, np.isfinite(out), segments, window)


def rolling_beta(data: PriceInput, benchmark: pd.Series, *, window: int = 60,
                 price: str = "close") -> IndicatorFrameV2:
    """滚动 Beta：``cov(asset_return, bench_return) / var(bench_return)``（样本口径）。"""
    window = _require_positive_int(window, "window")
    if not benchmark.index.equals(data.index):
        raise IndicatorInputError("INDEX_MISMATCH:benchmark")
    values = _price_field(data, price)
    bench = _numeric(benchmark, "benchmark")
    asset_previous = np.concatenate(([np.nan], values[:-1]))
    bench_previous = np.concatenate(([np.nan], bench[:-1]))
    with np.errstate(divide="ignore", invalid="ignore"):
        asset_return = np.where((asset_previous > 0) & (values > 0), values / asset_previous - 1.0, np.nan)
        bench_return = np.where((bench_previous > 0) & (bench > 0), bench / bench_previous - 1.0, np.nan)
    valid = np.isfinite(asset_return) & np.isfinite(bench_return)
    segments = segment_ids(valid)
    out = np.full(len(values), np.nan)
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        if positions.size < window:
            continue
        a = pd.Series(asset_return[positions])
        b = pd.Series(bench_return[positions])
        covariance = a.rolling(window, min_periods=window).cov(b)
        variance = b.rolling(window, min_periods=window).var()
        computed = (covariance / variance.where(variance > 0)).to_numpy()
        out[positions] = computed
    return _frame("ROLLING_BETA", "ROLLING_BETA_V1", data.index,
                  {"beta": out}, np.isfinite(out), segments, window + 1)


def rolling_slope(data: PriceInput, *, window: int = 20, price: str = "close") -> IndicatorFrameV2:
    """线性斜率：对窗口内价格对时间做最小二乘回归的斜率（价格单位/根）。"""
    window = _require_positive_int(window, "window")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    out = np.full(len(values), np.nan)
    x = np.arange(window, dtype=float)
    x_centered = x - x.mean()
    denominator = float((x_centered ** 2).sum())
    for segment in range(int(segments.max()) + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        for offset in range(window - 1, positions.size):
            chunk = values[positions[offset - window + 1 : offset + 1]]
            out[positions[offset]] = float(np.dot(x_centered, chunk) / denominator)
    return _frame("ROLLING_SLOPE", "ROLLING_SLOPE_V1", data.index,
                  {"slope": out}, np.isfinite(out), segments, window)


def time_series_zscore(data: PriceInput, *, window: int = 60, price: str = "close",
                       ddof: int = 1) -> IndicatorFrameV2:
    """**时序**标准化：``(C - MA(C,N)) / STD(C,N)``。与**截面** z-score 是不同口径。"""
    window = _require_positive_int(window, "window")
    if ddof not in (0, 1):
        raise IndicatorInputError("INVALID_ZSCORE_DDOF")
    values = _price_field(data, price)
    segments = segment_ids(np.isfinite(values) & (values > 0))
    mean = _rolling_in_segments(values, segments, window, window, "mean")
    deviation = _rolling_in_segments(values, segments, window, window, "std", ddof=ddof)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(deviation > 0, (values - mean) / deviation, np.nan)
    return _frame("TIME_SERIES_ZSCORE", "TIME_SERIES_ZSCORE_V1", data.index,
                  {"ts_zscore": out, "mean": mean, "std": deviation},
                  np.isfinite(out), segments, window)
