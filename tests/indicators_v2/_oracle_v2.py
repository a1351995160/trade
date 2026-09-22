"""独立朴素参考实现（V2 oracle）——不调用被测函数。

用于给多家族指标提供**独立**预期值。刻意写成最直白的逐元素循环，
只依赖 Python 浮点与 numpy，避免"生产函数自证"。
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np


def naive_sma(values: Sequence[float], window: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for i in range(len(values)):
        start = i - window + 1
        if start < 0:
            out.append(None)
            continue
        chunk = [v for v in values[start : i + 1]]
        if any(v is None or v != v for v in chunk):
            out.append(None)
            continue
        out.append(sum(chunk) / window)
    return out


def naive_ema(values: Sequence[float], window: int) -> List[Optional[float]]:
    alpha = 2.0 / (window + 1.0)
    out: List[Optional[float]] = []
    previous = None
    for value in values:
        if value is None or value != value:
            previous = None
            out.append(None)
            continue
        previous = value if previous is None else alpha * value + (1 - alpha) * previous
        out.append(previous)
    return out


def naive_wma(values: Sequence[float], window: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    weights = list(range(1, window + 1))
    total = sum(weights)
    for i in range(len(values)):
        start = i - window + 1
        if start < 0:
            out.append(None)
            continue
        chunk = values[start : i + 1]
        out.append(sum(v * w for v, w in zip(chunk, weights)) / total)
    return out


def naive_rma_wilder(values: Sequence[float], window: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    seed = None
    previous = None
    for i, value in enumerate(values):
        if i + 1 < window:
            out.append(None)
            continue
        if seed is None:
            seed = sum(values[:window]) / window
            previous = seed
            out.append(previous)
            continue
        previous = (previous * (window - 1) + value) / window
        out.append(previous)
    return out


def naive_rsi(closes: Sequence[float], window: int) -> List[Optional[float]]:
    """Wilder RSI 独立实现。无涨跌 -> 50；仅涨 -> 100；仅跌 -> 0。"""
    out: List[Optional[float]] = [None] * len(closes)
    if len(closes) <= window:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    average_gain = sum(gains[:window]) / window
    average_loss = sum(losses[:window]) / window

    def value(g: float, l: float) -> float:
        if g == 0 and l == 0:
            return 50.0
        if l == 0:
            return 100.0
        if g == 0:
            return 0.0
        return 100.0 * g / (g + l)

    out[window] = value(average_gain, average_loss)
    for i in range(window, len(gains)):
        average_gain = (average_gain * (window - 1) + gains[i]) / window
        average_loss = (average_loss * (window - 1) + losses[i]) / window
        out[i + 1] = value(average_gain, average_loss)
    return out


def naive_williams_r(highs, lows, closes, window: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for i in range(len(closes)):
        start = i - window + 1
        if start < 0:
            out.append(None)
            continue
        hhv = max(highs[start : i + 1])
        llv = min(lows[start : i + 1])
        span = hhv - llv
        out.append(None if span <= 0 else (hhv - closes[i]) / span * 100.0)
    return out


def naive_bollinger(closes, window: int, multiplier: float, ddof: int):
    middle: List[Optional[float]] = []
    upper: List[Optional[float]] = []
    lower: List[Optional[float]] = []
    for i in range(len(closes)):
        start = i - window + 1
        if start < 0:
            middle.append(None)
            upper.append(None)
            lower.append(None)
            continue
        chunk = np.array(closes[start : i + 1], dtype=float)
        mean = float(chunk.mean())
        std = float(chunk.std(ddof=ddof))
        middle.append(mean)
        upper.append(mean + multiplier * std)
        lower.append(mean - multiplier * std)
    return middle, upper, lower


def naive_true_range(highs, lows, closes, prev_closes) -> List[Optional[float]]:
    """TR 独立实现：段首按惯例取 H-L（不因缺前收盘丢弃该根）。"""
    out: List[Optional[float]] = []
    for i in range(len(closes)):
        pc = prev_closes[i]
        if pc is None or pc != pc or pc <= 0:
            out.append(highs[i] - lows[i])
            continue
        out.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    return out


def naive_atr(highs, lows, closes, prev_closes, window: int) -> List[Optional[float]]:
    """Wilder ATR：TR 的累加平滑 / window。段首 TR 取 H-L。"""
    tr = naive_true_range(highs, lows, closes, prev_closes)
    out: List[Optional[float]] = [None] * len(closes)
    usable = [i for i in range(len(tr)) if tr[i] is not None]
    if len(usable) < window:
        return out
    seed = sum(tr[i] for i in usable[:window])
    out[usable[window - 1]] = seed / window
    previous = seed
    for offset in range(window, len(usable)):
        i = usable[offset]
        previous = previous - previous / window + tr[i]
        out[i] = previous / window
    return out


def naive_obv(closes, volumes) -> List[float]:
    out = [0.0]
    running = 0.0
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            running += volumes[i]
        elif closes[i] < closes[i - 1]:
            running -= volumes[i]
        out.append(running)
    return out


def naive_roc(closes, window: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for i in range(len(closes)):
        if i < window or closes[i - window] == 0:
            out.append(None)
            continue
        out.append((closes[i] / closes[i - window] - 1.0) * 100.0)
    return out


def naive_mtm(closes, window: int) -> List[Optional[float]]:
    return [None if i < window else closes[i] - closes[i - window] for i in range(len(closes))]


def naive_bias(closes, window: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for i in range(len(closes)):
        if i + 1 < window:
            out.append(None)
            continue
        mean = sum(closes[i - window + 1 : i + 1]) / window
        out.append(None if mean == 0 else (closes[i] - mean) / mean * 100.0)
    return out


def naive_hlc3(highs, lows, closes) -> List[float]:
    return [(h + l + c) / 3.0 for h, l, c in zip(highs, lows, closes)]


def naive_streak(closes) -> tuple:
    up, down = [0], [0]
    up_run = down_run = 0
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            up_run, down_run = up_run + 1, 0
        elif closes[i] < closes[i - 1]:
            up_run, down_run = 0, down_run + 1
        else:
            up_run = down_run = 0
        up.append(up_run)
        down.append(down_run)
    return up, down


def naive_bar_shape(opens, highs, lows, closes, prev_closes):
    body, upper, lower = [], [], []
    for i in range(len(closes)):
        span = highs[i] - lows[i]
        if span <= 0:
            body.append(0.0)
            upper.append(0.0)
            lower.append(0.0)
            continue
        body.append(abs(closes[i] - opens[i]) / span)
        upper.append((highs[i] - max(opens[i], closes[i])) / span)
        lower.append((min(opens[i], closes[i]) - lows[i]) / span)
    return body, upper, lower


def naive_rolling_slope(values, window: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    x = np.arange(window, dtype=float)
    xc = x - x.mean()
    denom = float((xc ** 2).sum())
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
            continue
        chunk = np.array(values[i - window + 1 : i + 1], dtype=float)
        out.append(float(np.dot(xc, chunk) / denom))
    return out


def naive_ts_zscore(values, window: int, ddof: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
            continue
        chunk = np.array(values[i - window + 1 : i + 1], dtype=float)
        mean = float(chunk.mean())
        std = float(chunk.std(ddof=ddof))
        out.append(None if std == 0 else (values[i] - mean) / std)
    return out


def naive_dmi(highs, lows, closes, prev_closes, window: int) -> dict:
    """DMI/ADX 独立实现（Wilder 原始口径）。

    TR/+DM/-DM 用 Wilder RMA 平滑；+DI/-DI 需 N 根；ADX = RMA(DX, N)。
    """
    n = len(closes)
    tr = [None] * n
    plus_dm = [None] * n
    minus_dm = [None] * n
    for i in range(n):
        pc = prev_closes[i]
        if pc is None or pc != pc or pc <= 0:
            tr[i] = highs[i] - lows[i]
        else:
            tr[i] = max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc))
        if i == 0:
            plus_dm[i] = 0.0
            minus_dm[i] = 0.0
            continue
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm[i] = up_move if (up_move > 0 and up_move > down_move) else 0.0
        minus_dm[i] = down_move if (down_move > 0 and down_move > up_move) else 0.0

    def wilder(values):
        out = [None] * n
        if n < window:
            return out
        seed = sum(values[:window])
        out[window - 1] = seed
        previous = seed
        for i in range(window, n):
            previous = previous - previous / window + values[i]
            out[i] = previous
        return out

    smooth_tr = wilder(tr)
    smooth_plus = wilder(plus_dm)
    smooth_minus = wilder(minus_dm)
    plus_di = [None] * n
    minus_di = [None] * n
    dx = [None] * n
    for i in range(n):
        if smooth_tr[i] and smooth_tr[i] > 0:
            plus_di[i] = 100.0 * smooth_plus[i] / smooth_tr[i]
            minus_di[i] = 100.0 * smooth_minus[i] / smooth_tr[i]
            denom = plus_di[i] + minus_di[i]
            dx[i] = 100.0 * abs(plus_di[i] - minus_di[i]) / denom if denom > 0 else None
    usable = [i for i in range(n) if dx[i] is not None]
    adx = [None] * n
    if len(usable) >= window:
        seed = sum(dx[i] for i in usable[:window])
        adx[usable[window - 1]] = seed / window
        previous = seed
        for offset in range(window, len(usable)):
            i = usable[offset]
            previous = previous - previous / window + dx[i]
            adx[i] = previous / window
    return {"plus_di": plus_di, "minus_di": minus_di, "adx": adx, "tr": tr}


def naive_sar(highs, lows, step: float = 0.02, max_step: float = 0.2):
    """抛物线 SAR 独立实现。

    初始趋势由前两根确定；AF 从 step 起、创新极值递增、上限 max_step；
    反转时 AF 重置并取该方向极值；含"不高于前两根最低/不低于前两根最高"约束。
    """
    n = len(highs)
    sar = [None] * n
    trend = [0] * n
    if n < 2:
        return {"sar": sar, "trend": trend}
    rising = highs[1] >= highs[0]
    extreme = highs[0] if rising else lows[0]
    value = lows[0] if rising else highs[0]
    af = step
    sar[0] = value
    trend[0] = 1 if rising else -1
    for i in range(1, n):
        value = value + af * (extreme - value)
        if rising:
            value = min(value, lows[i - 1]) if i < 2 else min(value, lows[i - 1], lows[i - 2])
            if lows[i] < value:
                rising = False
                value = extreme
                extreme = lows[i]
                af = step
            elif highs[i] > extreme:
                extreme = highs[i]
                af = min(af + step, max_step)
        else:
            value = max(value, highs[i - 1]) if i < 2 else max(value, highs[i - 1], highs[i - 2])
            if highs[i] > value:
                rising = True
                value = extreme
                extreme = highs[i]
                af = step
            elif lows[i] < extreme:
                extreme = lows[i]
                af = min(af + step, max_step)
        sar[i] = value
        trend[i] = 1 if rising else -1
    return {"sar": sar, "trend": trend}
