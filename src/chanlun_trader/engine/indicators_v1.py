"""BT_INDICATORS_V1 — 版本化指标实现（MACD / KDJ / MA / EMA / CROSS）。

纯函数模块：不读写磁盘、不访问真实行情、不依赖全局状态。
口径冻结见 docs/BACKTEST_BEHAVIOR_ACCEPTANCE_V1.md。

分段语义（本版本的核心约定）
----------------------------
连续"合格输入"构成一个计算段。任一不合格输入（NaN / 非有限 / 价格 <= 0 /
high < low / HHV == LLV）立即结束当前段；递推状态在下一段从初值重新开始。
跨缺口不得延续 EMA / K / D 状态，也不得跨缺口判定交叉。

本版本不声称与任何第三方软件（通达信等）逐值一致；没有同输入同复权的
逐值对照证据时，只能声明"本版本公式验收通过"。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

INDICATOR_CONTRACT_VERSION = "BT_INDICATORS_V1"
MACD_IMPLEMENTATION_VERSION = "MACD_V1"
KDJ_IMPLEMENTATION_VERSION = "KDJ_V1"
MA_IMPLEMENTATION_VERSION = "MA_EMA_CROSS_V1"


class IndicatorInputError(ValueError):
    """指标输入不满足合同（时间轴非法、参数非法、列缺失）。"""


def _require_time_index(index: pd.Index) -> pd.Index:
    """时间轴必须是严格递增且无重复的交易日整数键。"""
    values = np.asarray([int(v) for v in index], dtype=np.int64)
    if len(values) != len(index):
        raise IndicatorInputError("TIME_INDEX_LENGTH_MISMATCH")
    if len(values) > 1 and not np.all(np.diff(values) > 0):
        raise IndicatorInputError("TIME_INDEX_NOT_STRICTLY_INCREASING")
    return pd.Index(values)


def _require_positive_int(value: int, name: str, *, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise IndicatorInputError(f"INVALID_PARAMETER:{name}") from exc
    if parsed < minimum:
        raise IndicatorInputError(f"INVALID_PARAMETER:{name}")
    return parsed


def _numeric(series: pd.Series, name: str) -> np.ndarray:
    try:
        values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise IndicatorInputError(f"NON_NUMERIC_INPUT:{name}") from exc
    return values


def segment_ids(valid: np.ndarray) -> np.ndarray:
    """把布尔合格掩码切成连续段编号；不合格位置为 -1。"""
    out = np.full(len(valid), -1, dtype=np.int64)
    current = -1
    previous = False
    for i, ok in enumerate(valid):
        if not ok:
            previous = False
            continue
        if not previous:
            current += 1
        out[i] = current
        previous = True
    return out


@dataclass(frozen=True)
class IndicatorFrameV1:
    """指标计算结果。index 为交易日整数键；ready 标记该行是否可发布信号。"""

    version: str
    index: pd.Index
    columns: dict
    ready: pd.Series
    segment: pd.Series

    def value(self, name: str) -> pd.Series:
        return self.columns[name]

    def to_frame(self) -> pd.DataFrame:
        frame = pd.DataFrame(dict(self.columns), index=self.index)
        frame["__ready__"] = self.ready.to_numpy()
        frame["__segment__"] = self.segment.to_numpy()
        return frame


# --------------------------------------------------------------------------
# EMA / MA / CROSS
# --------------------------------------------------------------------------
def ema_recursive(values: Sequence[float], span: int) -> np.ndarray:
    """显式递推 EMA：首值 = 首个输入值，alpha = 2/(span+1)。

    与 pandas ``Series.ewm(span=span, adjust=False).mean()`` 的约定一致，
    但本函数不依赖 pandas，便于独立核对。
    """
    span = _require_positive_int(span, "span")
    array = np.asarray(values, dtype=float)
    out = np.full(array.shape, np.nan, dtype=float)
    alpha = 2.0 / (span + 1.0)
    previous = np.nan
    for i, value in enumerate(array):
        if not np.isfinite(value):
            previous = np.nan
            continue
        previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
        out[i] = previous
    return out


def sma(values: Sequence[float], window: int, *, min_periods: Optional[int] = None) -> np.ndarray:
    """简单移动平均。默认要求窗口内全部合格（min_periods = window）。"""
    window = _require_positive_int(window, "window")
    required = window if min_periods is None else _require_positive_int(min_periods, "min_periods")
    array = np.asarray(values, dtype=float)
    out = np.full(array.shape, np.nan, dtype=float)
    for i in range(len(array)):
        start = i - window + 1
        if start < 0:
            continue
        chunk = array[start : i + 1]
        if np.count_nonzero(np.isfinite(chunk)) < required:
            continue
        out[i] = float(np.nanmean(chunk))
    return out


def cross_up(left: Sequence[float], right: Sequence[float]) -> np.ndarray:
    """上穿：前一根 left <= right 且当前 left > right。两侧都必须有效。"""
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    if a.shape != b.shape:
        raise IndicatorInputError("CROSS_SHAPE_MISMATCH")
    out = np.zeros(a.shape, dtype=bool)
    for i in range(1, len(a)):
        if not (np.isfinite(a[i]) and np.isfinite(b[i]) and np.isfinite(a[i - 1]) and np.isfinite(b[i - 1])):
            continue
        out[i] = bool(a[i - 1] <= b[i - 1] and a[i] > b[i])
    return out


def cross_down(left: Sequence[float], right: Sequence[float]) -> np.ndarray:
    """下穿：前一根 left >= right 且当前 left < right。两侧都必须有效。"""
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    if a.shape != b.shape:
        raise IndicatorInputError("CROSS_SHAPE_MISMATCH")
    out = np.zeros(a.shape, dtype=bool)
    for i in range(1, len(a)):
        if not (np.isfinite(a[i]) and np.isfinite(b[i]) and np.isfinite(a[i - 1]) and np.isfinite(b[i - 1])):
            continue
        out[i] = bool(a[i - 1] >= b[i - 1] and a[i] < b[i])
    return out


# --------------------------------------------------------------------------
# MACD_V1
# --------------------------------------------------------------------------
def macd_v1(
    close: pd.Series,
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> IndicatorFrameV1:
    """MACD_V1。

    - EMA_fast / EMA_slow 使用显式递推，段内首值 = 该段首个合格 close。
    - DIF = EMA_fast(close) - EMA_slow(close)
    - DEA = EMA_signal(DIF)，段内首值 = 该段首个 DIF。
    - HIST = 2 * (DIF - DEA)
    - ready 要求段内连续合格输入数 >= slow + signal - 1。

    ``slow + signal - 1`` 是本实现选定的 warmup 口径，不声称与任意客户端初始化相同。
    """
    fast = _require_positive_int(fast, "fast")
    slow = _require_positive_int(slow, "slow")
    signal = _require_positive_int(signal, "signal")
    if fast >= slow:
        raise IndicatorInputError("FAST_MUST_BE_LESS_THAN_SLOW")
    index = _require_time_index(close.index)
    values = _numeric(close, "close")
    valid = np.isfinite(values) & (values > 0)
    segments = segment_ids(valid)

    n = len(values)
    dif = np.full(n, np.nan)
    dea = np.full(n, np.nan)
    hist = np.full(n, np.nan)
    ema_fast = np.full(n, np.nan)
    ema_slow = np.full(n, np.nan)
    ready = np.zeros(n, dtype=bool)

    alpha_fast = 2.0 / (fast + 1.0)
    alpha_slow = 2.0 / (slow + 1.0)
    alpha_signal = 2.0 / (signal + 1.0)
    warmup = slow + signal - 1

    for segment in range(segments.max() + 1) if n else []:
        positions = np.flatnonzero(segments == segment)
        if positions.size == 0:
            continue
        previous_fast = np.nan
        previous_slow = np.nan
        previous_dea = np.nan
        for count, position in enumerate(positions, start=1):
            price = values[position]
            if not np.isfinite(previous_fast):
                previous_fast = price
                previous_slow = price
            else:
                previous_fast = alpha_fast * price + (1.0 - alpha_fast) * previous_fast
                previous_slow = alpha_slow * price + (1.0 - alpha_slow) * previous_slow
            ema_fast[position] = previous_fast
            ema_slow[position] = previous_slow
            current_dif = previous_fast - previous_slow
            dif[position] = current_dif
            if not np.isfinite(previous_dea):
                previous_dea = current_dif
            else:
                previous_dea = alpha_signal * current_dif + (1.0 - alpha_signal) * previous_dea
            dea[position] = previous_dea
            hist[position] = 2.0 * (current_dif - previous_dea)
            ready[position] = count >= warmup

    return IndicatorFrameV1(
        version=MACD_IMPLEMENTATION_VERSION,
        index=index,
        columns={
            "ema_fast": pd.Series(ema_fast, index=index),
            "ema_slow": pd.Series(ema_slow, index=index),
            "dif": pd.Series(dif, index=index),
            "dea": pd.Series(dea, index=index),
            "hist": pd.Series(hist, index=index),
        },
        ready=pd.Series(ready, index=index),
        segment=pd.Series(segments, index=index),
    )


# --------------------------------------------------------------------------
# KDJ_V1
# --------------------------------------------------------------------------
def kdj_v1(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    *,
    n: int = 9,
    k_period: int = 3,
    d_period: int = 3,
) -> IndicatorFrameV1:
    """KDJ(9,3,3) 工程版本。

    - LLV = 最近 N 根合格已完成 bar 的 low 最小值
    - HHV = 最近 N 根合格已完成 bar 的 high 最大值
    - RSV = 100 * (close - LLV) / (HHV - LLV)
    - K = (2*K_prev + RSV) / 3
    - D = (2*D_prev + K) / 3
    - J = 3*K - 2*D（不裁剪到 [0, 100]）

    段首 K_prev = D_prev = 50；满 N 根后才输出第一条有效结果。
    HHV == LLV（一字板或全平）视为无效输入，结束该段并重新预热，
    不把 RSV 补 0 来制造信号。
    """
    n = _require_positive_int(n, "n")
    k_period = _require_positive_int(k_period, "k_period")
    d_period = _require_positive_int(d_period, "d_period")
    if k_period != 3 or d_period != 3:
        raise IndicatorInputError("UNSUPPORTED_KDJ_SMOOTHING_PERIOD")
    index = _require_time_index(close.index)
    if not (high.index.equals(close.index) and low.index.equals(close.index)):
        raise IndicatorInputError("KDJ_INDEX_MISMATCH")
    high_values = _numeric(high, "high")
    low_values = _numeric(low, "low")
    close_values = _numeric(close, "close")

    finite = (
        np.isfinite(high_values)
        & np.isfinite(low_values)
        & np.isfinite(close_values)
        & (high_values > 0)
        & (low_values > 0)
        & (close_values > 0)
        & (high_values >= low_values)
    )

    size = len(close_values)
    k_values = np.full(size, np.nan)
    d_values = np.full(size, np.nan)
    j_values = np.full(size, np.nan)
    rsv_values = np.full(size, np.nan)
    ready = np.zeros(size, dtype=bool)
    segments = np.full(size, -1, dtype=np.int64)

    segment = -1
    run_start = 0          # 当前合格预热段的起始位置
    previous_k = 50.0
    previous_d = 50.0
    for i in range(size):
        if not finite[i]:
            # 非法价格 / NaN：结束该计算段，待新的合格预热段完成再恢复。
            run_start = i + 1
            previous_k = 50.0
            previous_d = 50.0
            continue
        # 窗口起点：段内最近 N 根；预热不足时退化为段内已有全部合格 bar。
        window_start = max(run_start, i - n + 1)
        hhv = float(high_values[window_start : i + 1].max())
        llv = float(low_values[window_start : i + 1].min())
        if not (hhv > llv):
            # HHV == LLV（一字区间）：无效输入，结束该段并从下一根重新预热。
            run_start = i + 1
            previous_k = 50.0
            previous_d = 50.0
            continue
        count = i - run_start + 1
        if count < n:
            # 预热不足：不输出，也不补 0 制造信号。
            continue
        if count == n:
            # 该段第一根有效输出：新段起点，K/D 从 50 起。
            segment += 1
            previous_k = 50.0
            previous_d = 50.0
        rsv = 100.0 * (close_values[i] - llv) / (hhv - llv)
        current_k = (2.0 * previous_k + rsv) / 3.0
        current_d = (2.0 * previous_d + current_k) / 3.0
        previous_k = current_k
        previous_d = current_d
        segments[i] = segment
        rsv_values[i] = rsv
        k_values[i] = current_k
        d_values[i] = current_d
        j_values[i] = 3.0 * current_k - 2.0 * current_d
        ready[i] = True

    return IndicatorFrameV1(
        version=KDJ_IMPLEMENTATION_VERSION,
        index=index,
        columns={
            "rsv": pd.Series(rsv_values, index=index),
            "k": pd.Series(k_values, index=index),
            "d": pd.Series(d_values, index=index),
            "j": pd.Series(j_values, index=index),
        },
        ready=pd.Series(ready, index=index),
        segment=pd.Series(segments, index=index),
    )


# --------------------------------------------------------------------------
# MA / EMA 系列（供条件与移动平均过滤使用）
# --------------------------------------------------------------------------
def moving_average(close: pd.Series, *, window: int) -> IndicatorFrameV1:
    index = _require_time_index(close.index)
    values = _numeric(close, "close")
    valid = np.isfinite(values) & (values > 0)
    segments = segment_ids(valid)
    out = np.full(len(values), np.nan)
    for segment in range(segments.max() + 1) if len(values) else []:
        positions = np.flatnonzero(segments == segment)
        if positions.size < window:
            continue
        segment_values = values[positions]
        for offset in range(window - 1, len(segment_values)):
            out[positions[offset]] = float(segment_values[offset - window + 1 : offset + 1].mean())
    return IndicatorFrameV1(
        version=MA_IMPLEMENTATION_VERSION,
        index=index,
        columns={f"ma{window}": pd.Series(out, index=index)},
        ready=pd.Series(np.isfinite(out), index=index),
        segment=pd.Series(segments, index=index),
    )
