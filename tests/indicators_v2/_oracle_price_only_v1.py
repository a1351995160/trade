"""A0 增量 oracle：本轮补的独立朴素参考实现。

要求（任务第 5 节）：
- 独立朴素参考或手算序列，**不用被测实现生成 expected**；
- 与被测实现相同的初始化、预热、分段与缺失语义。

本模块只写朴素实现，不导入被测指标模块（避免循环自证）。
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np


def _finite(values: Sequence[float]) -> np.ndarray:
    return np.asarray(values, dtype=float)


def naive_ema(values: Sequence[float], window: int) -> List[Optional[float]]:
    """EMA：alpha = 2/(N+1)，首值取第一个有限值（与被测实现一致的初值约定）。"""
    arr = _finite(values)
    out: List[Optional[float]] = [None] * len(arr)
    alpha = 2.0 / (window + 1.0)
    previous: Optional[float] = None
    for i, value in enumerate(arr):
        if not np.isfinite(value):
            previous = None
            continue
        previous = value if previous is None else alpha * value + (1 - alpha) * previous
        out[i] = previous
    return out


def naive_dema(values: Sequence[float], window: int) -> List[Optional[float]]:
    """DEMA = 2*E1 - E2，其中 E2 = EMA(E1)。"""
    e1 = naive_ema(values, window)
    arr = _finite(values)
    # E2 只在 E1 有限处递推；E1 的缺口处重置
    e2: List[Optional[float]] = [None] * len(arr)
    alpha = 2.0 / (window + 1.0)
    previous: Optional[float] = None
    for i, value in enumerate(e1):
        if value is None:
            previous = None
            continue
        previous = value if previous is None else alpha * value + (1 - alpha) * previous
        e2[i] = previous
    return [None if (a is None or b is None) else 2 * a - b for a, b in zip(e1, e2)]


def naive_tema(values: Sequence[float], window: int) -> List[Optional[float]]:
    """TEMA = 3*E1 - 3*E2 + E3。"""
    e1 = naive_ema(values, window)
    arr = _finite(values)
    alpha = 2.0 / (window + 1.0)

    def chain(prev_chain: List[Optional[float]]) -> List[Optional[float]]:
        out: List[Optional[float]] = [None] * len(arr)
        previous: Optional[float] = None
        for i, value in enumerate(prev_chain):
            if value is None:
                previous = None
                continue
            previous = value if previous is None else alpha * value + (1 - alpha) * previous
            out[i] = previous
        return out

    e2 = chain(e1)
    e3 = chain(e2)
    result: List[Optional[float]] = []
    for a, b, c in zip(e1, e2, e3):
        result.append(None if (a is None or b is None or c is None) else 3 * a - 3 * b + c)
    return result


def naive_rolling_mean(values: Sequence[float], window: int) -> List[Optional[float]]:
    """滚动均值：前 window-1 个为 None。"""
    arr = _finite(values)
    out: List[Optional[float]] = []
    for i in range(len(arr)):
        if i + 1 < window:
            out.append(None)
            continue
        chunk = arr[i - window + 1 : i + 1]
        out.append(None if not np.all(np.isfinite(chunk)) else float(chunk.mean()))
    return out


def naive_cci(highs, lows, closes, window: int) -> List[Optional[float]]:
    """CCI = (TP - SMA(TP,N)) / (0.015 * 平均绝对偏差)。"""
    tp = (np.asarray(highs, float) + np.asarray(lows, float) + np.asarray(closes, float)) / 3.0
    mean = naive_rolling_mean(tp, window)
    out: List[Optional[float]] = []
    for i in range(len(tp)):
        if mean[i] is None:
            out.append(None)
            continue
        chunk = tp[i - window + 1 : i + 1]
        mad = float(np.mean(np.abs(chunk - mean[i])))
        out.append(None if mad == 0 else (tp[i] - mean[i]) / (0.015 * mad))
    return out


def naive_true_range(highs, lows, closes, prev_closes) -> List[Optional[float]]:
    """TR：有可用前收时取三者最大；否则退化为 H-L（与被测实现同约定）。"""
    h = np.asarray(highs, float)
    l = np.asarray(lows, float)
    c = np.asarray(closes, float)
    pc = np.asarray(prev_closes, float)
    valid = (np.isfinite(c) & (c > 0) & np.isfinite(h) & (h > 0)
             & np.isfinite(l) & (l > 0) & (h >= l))
    out: List[Optional[float]] = []
    for i in range(len(h)):
        if not valid[i]:
            out.append(None)
        elif i > 0 and valid[i - 1] and np.isfinite(pc[i]) and pc[i] > 0:
            out.append(float(max(h[i] - l[i], abs(h[i] - pc[i]), abs(l[i] - pc[i]))))
        else:
            out.append(float(h[i] - l[i]))
    return out


def naive_wilder_rma(values: Sequence[float], window: int) -> List[Optional[float]]:
    """Wilder RMA：**按连续段独立播种**。

    段首 = 该段前 N 个有限值之和，其后 ``prev - prev/N + value``。
    缺口（非有限值）切断段，**不得跨缺口继承旧段状态** ——
    否则会把缺口前的累加量带到缺口后，产生错误的递推值。
    """
    arr = _finite(values)
    n = len(arr)
    out: List[Optional[float]] = [None] * n
    segment_start = 0
    while segment_start < n:
        # 找本段结束位置（连续有限区间）
        if not np.isfinite(arr[segment_start]):
            segment_start += 1
            continue
        segment_end = segment_start
        while segment_end < n and np.isfinite(arr[segment_end]):
            segment_end += 1
        positions = list(range(segment_start, segment_end))
        if len(positions) >= window:
            seed = float(np.sum(arr[positions[:window]]))
            out[positions[window - 1]] = seed
            previous = seed
            for offset in range(window, len(positions)):
                i = positions[offset]
                previous = previous - previous / window + arr[i]
                out[i] = previous
        segment_start = segment_end
    return out


def naive_natr(highs, lows, closes, prev_closes, window: int) -> List[Optional[float]]:
    """NATR = ATR / close * 100，ATR = RMA(TR, N)/N。"""
    tr = naive_true_range(highs, lows, closes, prev_closes)
    rma = naive_wilder_rma(tr, window)
    c = np.asarray(closes, float)
    out: List[Optional[float]] = []
    for i, value in enumerate(rma):
        if value is None or not (c[i] > 0):
            out.append(None)
        else:
            out.append(value / window / c[i] * 100.0)
    return out


def naive_psy(values: Sequence[float], window: int) -> List[Optional[float]]:
    """PSY：窗口内上涨根数占**有限观察数**的比例（%）。

    与实现同约定：``up`` 序列首根为 None（无前值），
    分母是窗口内有限值的个数（不是固定 window）；
    窗口内有限值不足 window 个时不产出（min_periods=window）。
    """
    arr = _finite(values)
    up: List[Optional[float]] = [None] * len(arr)
    for i in range(1, len(arr)):
        if np.isfinite(arr[i - 1]) and arr[i - 1] > 0 and np.isfinite(arr[i]) and arr[i] > 0:
            up[i] = 1.0 if arr[i] > arr[i - 1] else 0.0
    out: List[Optional[float]] = []
    for i in range(len(arr)):
        if i + 1 < window:
            out.append(None)
            continue
        chunk = up[i - window + 1 : i + 1]
        finite = [v for v in chunk if v is not None]
        if len(finite) < window:
            out.append(None)
            continue
        out.append(float(sum(finite)) / len(finite) * 100.0)
    return out


def naive_donchian(highs, lows, window: int, shift: int = 0) -> dict:
    """Donchian：upper = HHV(H,N)，lower = LLV(L,N)；shift=1 参考前 N 根。"""
    h = np.asarray(highs, float)
    l = np.asarray(lows, float)
    n = len(h)
    upper: List[Optional[float]] = [None] * n
    lower: List[Optional[float]] = [None] * n
    for i in range(n):
        end = i - shift
        start = end - window + 1
        if start < 0:
            continue
        upper[i] = float(np.max(h[start : end + 1]))
        lower[i] = float(np.min(l[start : end + 1]))
    return {"upper": upper, "lower": lower}


def naive_rolling_volatility(values: Sequence[float], window: int, ddof: int = 0) -> List[Optional[float]]:
    """滚动收益标准差：先算 pct_change，再取 N 期总体/样本标准差。"""
    arr = _finite(values)
    returns = np.full(len(arr), np.nan)
    for i in range(1, len(arr)):
        if np.isfinite(arr[i - 1]) and arr[i - 1] != 0 and np.isfinite(arr[i]):
            returns[i] = arr[i] / arr[i - 1] - 1.0
    out: List[Optional[float]] = []
    for i in range(len(arr)):
        if i + 1 < window:
            out.append(None)
            continue
        chunk = returns[i - window + 1 : i + 1]
        out.append(None if not np.all(np.isfinite(chunk)) else float(np.std(chunk, ddof=ddof)))
    return out


def naive_historical_return(values: Sequence[float], window: int) -> List[Optional[float]]:
    """HISTORICAL_RETURN：close / close[t-N] - 1。"""
    arr = _finite(values)
    out: List[Optional[float]] = []
    for i in range(len(arr)):
        if i < window:
            out.append(None)
            continue
        base = arr[i - window]
        out.append(None if (not np.isfinite(base) or base == 0) else arr[i] / base - 1.0)
    return out


def naive_price_extremes(highs, lows, window: int) -> dict:
    """HHV/LLV：含当前 bar。"""
    h = np.asarray(highs, float)
    l = np.asarray(lows, float)
    n = len(h)
    hhv: List[Optional[float]] = [None] * n
    llv: List[Optional[float]] = [None] * n
    for i in range(n):
        if i + 1 < window:
            continue
        hhv[i] = float(np.max(h[i - window + 1 : i + 1]))
        llv[i] = float(np.min(l[i - window + 1 : i + 1]))
    return {"hhv": hhv, "llv": llv}


def naive_prior_breakout(highs, lows, closes, window: int) -> dict:
    """参考**前 N 根**（不含当前 bar）的高低点，判断当根是否突破。"""
    h = np.asarray(highs, float)
    l = np.asarray(lows, float)
    c = np.asarray(closes, float)
    n = len(h)
    prior_high: List[Optional[float]] = [None] * n
    prior_low: List[Optional[float]] = [None] * n
    for i in range(n):
        if i < window:
            continue
        prior_high[i] = float(np.max(h[i - window : i]))
        prior_low[i] = float(np.min(l[i - window : i]))
    return {"prior_high": prior_high, "prior_low": prior_low}


def naive_drawdown_from_peak(values: Sequence[float], window: int) -> dict:
    """回撤 = close / 窗口内峰值 - 1。"""
    arr = _finite(values)
    out: List[Optional[float]] = []
    peak: List[Optional[float]] = []
    for i in range(len(arr)):
        if i + 1 < window:
            out.append(None)
            peak.append(None)
            continue
        top = float(np.max(arr[i - window + 1 : i + 1]))
        peak.append(top)
        out.append(None if top == 0 else arr[i] / top - 1.0)
    return {"drawdown": out, "peak": peak}


def naive_rolling_slope(values: Sequence[float], window: int) -> List[Optional[float]]:
    """OLS 斜率：x = 0..N-1，y = 窗口内值。"""
    arr = _finite(values)
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    denom = float(np.sum((x - x_mean) ** 2))
    out: List[Optional[float]] = []
    for i in range(len(arr)):
        if i + 1 < window:
            out.append(None)
            continue
        chunk = arr[i - window + 1 : i + 1]
        if not np.all(np.isfinite(chunk)):
            out.append(None)
            continue
        y_mean = float(chunk.mean())
        out.append(float(np.sum((x - x_mean) * (chunk - y_mean)) / denom))
    return out


def naive_volume_ma(volumes: Sequence[float], window: int) -> List[Optional[float]]:
    return naive_rolling_mean(volumes, window)


def naive_rvol_prior(volumes: Sequence[float], window: int) -> List[Optional[float]]:
    """RVOL_PRIOR：当前量 / 前 N 期均量（不含当前）。"""
    arr = _finite(volumes)
    out: List[Optional[float]] = []
    for i in range(len(arr)):
        if i < window:
            out.append(None)
            continue
        base = float(np.mean(arr[i - window : i]))
        out.append(None if base == 0 else arr[i] / base)
    return out


def naive_rvol_incl_current(volumes: Sequence[float], window: int) -> List[Optional[float]]:
    """RVOL_INCL_CURRENT：当前量 / 含当前的 N 期均量。"""
    arr = _finite(volumes)
    mean = naive_rolling_mean(arr, window)
    out: List[Optional[float]] = []
    for i in range(len(arr)):
        if mean[i] is None or mean[i] == 0:
            out.append(None)
        else:
            out.append(arr[i] / mean[i])
    return out


def naive_accumulation_distribution(highs, lows, closes, volumes) -> List[float]:
    """A/D 累计：MFM = ((C-L)-(H-C))/(H-L)，A/D += MFM * volume。"""
    h = np.asarray(highs, float)
    l = np.asarray(lows, float)
    c = np.asarray(closes, float)
    v = np.asarray(volumes, float)
    total = 0.0
    out: List[float] = []
    for i in range(len(h)):
        span = h[i] - l[i]
        mfm = 0.0 if span == 0 else ((c[i] - l[i]) - (h[i] - c[i])) / span
        total += mfm * v[i]
        out.append(total)
    return out


def naive_chaikin_money_flow(highs, lows, closes, volumes, window: int) -> List[Optional[float]]:
    """CMF = N 期 MFV 之和 / N 期 volume 之和。"""
    h = np.asarray(highs, float)
    l = np.asarray(lows, float)
    c = np.asarray(closes, float)
    v = np.asarray(volumes, float)
    mfv = np.zeros(len(h))
    for i in range(len(h)):
        span = h[i] - l[i]
        mfm = 0.0 if span == 0 else ((c[i] - l[i]) - (h[i] - c[i])) / span
        mfv[i] = mfm * v[i]
    out: List[Optional[float]] = []
    for i in range(len(h)):
        if i + 1 < window:
            out.append(None)
            continue
        vol_sum = float(np.sum(v[i - window + 1 : i + 1]))
        out.append(None if vol_sum == 0 else float(np.sum(mfv[i - window + 1 : i + 1])) / vol_sum)
    return out


def naive_pvt(closes, volumes) -> List[float]:
    """PVT 累计：PVT += volume * pct_change(close)。"""
    c = np.asarray(closes, float)
    v = np.asarray(volumes, float)
    total = 0.0
    out: List[float] = [0.0]
    for i in range(1, len(c)):
        change = 0.0 if c[i - 1] == 0 else c[i] / c[i - 1] - 1.0
        total += v[i] * change
        out.append(total)
    return out


def naive_vwap_session_proxy(highs, lows, closes, volumes, amounts=None) -> List[Optional[float]]:
    """session 成交均价代理：**当根** amount / volume（不是累计）。

    与实现同约定：volume>0 且 amount>=0 才有效。
    """
    v = np.asarray(volumes, float)
    if amounts is None:
        tp = (np.asarray(highs, float) + np.asarray(lows, float) + np.asarray(closes, float)) / 3.0
        a = tp * v
    else:
        a = np.asarray(amounts, float)
    out: List[Optional[float]] = []
    for i in range(len(v)):
        if v[i] > 0 and np.isfinite(a[i]) and a[i] >= 0:
            out.append(float(a[i] / v[i]))
        else:
            out.append(None)
    return out


def naive_mfi(highs, lows, closes, volumes, window: int) -> List[Optional[float]]:
    """MFI：典型价变动方向把 `TP*volume` 分入正/负流，再取 N 期比值。"""
    h = np.asarray(highs, float)
    l = np.asarray(lows, float)
    c = np.asarray(closes, float)
    v = np.asarray(volumes, float)
    tp = (h + l + c) / 3.0
    flow = tp * v
    pos = np.full(len(tp), np.nan)
    neg = np.full(len(tp), np.nan)
    valid = (np.isfinite(c) & (c > 0) & np.isfinite(h) & (h > 0)
             & np.isfinite(l) & (l > 0) & (h >= l)
             & np.isfinite(v) & (v >= 0))
    for i in range(1, len(tp)):
        if not (valid[i - 1] and valid[i]):
            continue
        if tp[i] > tp[i - 1]:
            pos[i] = flow[i]
            neg[i] = 0.0
        elif tp[i] < tp[i - 1]:
            pos[i] = 0.0
            neg[i] = flow[i]
        else:
            pos[i] = 0.0
            neg[i] = 0.0
    out: List[Optional[float]] = [None] * len(tp)
    for i in range(len(tp)):
        if i + 1 < window:
            continue
        p_chunk = pos[i - window + 1 : i + 1]
        n_chunk = neg[i - window + 1 : i + 1]
        if not (np.all(np.isfinite(p_chunk)) and np.all(np.isfinite(n_chunk))):
            continue
        p = float(np.sum(p_chunk))
        n = float(np.sum(n_chunk))
        if n == 0:
            out[i] = 100.0 if p > 0 else None
        else:
            out[i] = 100.0 - 100.0 / (1.0 + p / n)
    return out


def naive_macd_hist_raw(closes, fast: int, slow: int, signal: int) -> dict:
    """MACD_HIST_RAW：HIST = DIF - DEA（不乘 2）。"""
    dif = [None if (a is None or b is None) else a - b
           for a, b in zip(naive_ema(closes, fast), naive_ema(closes, slow))]
    dea = naive_ema([0.0 if v is None else v for v in dif], signal)
    dea = [None if dif[i] is None else dea[i] for i in range(len(dif))]
    hist = [None if (a is None or b is None) else a - b for a, b in zip(dif, dea)]
    return {"dif": dif, "dea": dea, "hist": hist}


def naive_trix(values: Sequence[float], window: int, signal: int) -> dict:
    """TRIX = 三重 EMA 的单期百分比变动；trix_ma = SMA(TRIX, signal)。"""
    e1 = naive_ema(values, window)
    alpha = 2.0 / (window + 1.0)

    def chain(prev_chain):
        out: List[Optional[float]] = [None] * len(prev_chain)
        previous: Optional[float] = None
        for i, value in enumerate(prev_chain):
            if value is None:
                previous = None
                continue
            previous = value if previous is None else alpha * value + (1 - alpha) * previous
            out[i] = previous
        return out

    e2 = chain(e1)
    e3 = chain(e2)
    trix: List[Optional[float]] = [None] * len(e3)
    for i in range(1, len(e3)):
        if e3[i] is None or e3[i - 1] is None or e3[i - 1] == 0:
            continue
        trix[i] = (e3[i] / e3[i - 1] - 1.0) * 100.0
    trix_ma = naive_rolling_mean(trix, signal)
    return {"trix": trix, "trix_ma": trix_ma}


def naive_keltner(highs, lows, closes, prev_closes, window: int, atr_window: int,
                  multiplier: float) -> dict:
    """Keltner：中轨 = EMA(close, N)，上下轨 = 中轨 ± k*ATR(M)。"""
    middle = naive_ema(closes, window)
    tr = naive_true_range(highs, lows, closes, prev_closes)
    rma = naive_wilder_rma(tr, atr_window)
    atr_vals = [None if v is None else v / atr_window for v in rma]
    upper: List[Optional[float]] = []
    lower: List[Optional[float]] = []
    for m, a in zip(middle, atr_vals):
        if m is None or a is None:
            upper.append(None)
            lower.append(None)
        else:
            upper.append(m + multiplier * a)
            lower.append(m - multiplier * a)
    return {"middle": middle, "upper": upper, "lower": lower}


def naive_amount_ma(amounts: Sequence[float], window: int) -> List[Optional[float]]:
    return naive_rolling_mean(amounts, window)
