"""独立朴素参考实现（oracle）——不调用被测函数。

这些实现刻意写成最直白的逐元素循环，只依赖 Python 浮点运算，
用来给 MACD_V1 / KDJ_V1 提供独立的预期值，避免"两个生产包装函数互证"。
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence


def naive_ema(values: Sequence[float], span: int) -> List[Optional[float]]:
    """显式递推 EMA；首值 = 首个输入值，alpha = 2/(span+1)。"""
    alpha = 2.0 / (span + 1.0)
    out: List[Optional[float]] = []
    previous: Optional[float] = None
    for value in values:
        if value is None:
            previous = None
            out.append(None)
            continue
        previous = value if previous is None else alpha * value + (1.0 - alpha) * previous
        out.append(previous)
    return out


def naive_macd(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> dict:
    """返回 dif / dea / hist / ready 的独立预期值。

    段语义与实现约定一致：任一不合格输入（非有限或 <= 0）结束当前段。
    """
    alpha_fast = 2.0 / (fast + 1.0)
    alpha_slow = 2.0 / (slow + 1.0)
    alpha_signal = 2.0 / (signal + 1.0)
    warmup = slow + signal - 1

    dif: List[Optional[float]] = []
    dea: List[Optional[float]] = []
    hist: List[Optional[float]] = []
    ready: List[bool] = []
    previous_fast = previous_slow = previous_dea = None
    count = 0
    for close in closes:
        if close is None or close != close or close <= 0:
            previous_fast = previous_slow = previous_dea = None
            count = 0
            dif.append(None)
            dea.append(None)
            hist.append(None)
            ready.append(False)
            continue
        if previous_fast is None:
            previous_fast = close
            previous_slow = close
        else:
            previous_fast = alpha_fast * close + (1.0 - alpha_fast) * previous_fast
            previous_slow = alpha_slow * close + (1.0 - alpha_slow) * previous_slow
        current_dif = previous_fast - previous_slow
        if previous_dea is None:
            previous_dea = current_dif
        else:
            previous_dea = alpha_signal * current_dif + (1.0 - alpha_signal) * previous_dea
        dif.append(current_dif)
        dea.append(previous_dea)
        hist.append(2.0 * (current_dif - previous_dea))
        count += 1
        ready.append(count >= warmup)
    return {"dif": dif, "dea": dea, "hist": hist, "ready": ready}


def naive_kdj(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    n: int = 9,
) -> dict:
    """KDJ(9,3,3) 独立预期值。

    - 窗口 = 段内最近 N 根合格 bar（预热不足时窗口退化为段内已有全部合格 bar）；
    - 满 N 根后才输出第一根，且该根 K/D 从 50 起；
    - HHV == LLV 视为无效输入，结束当前段并从下一根重新预热；
    - K = (2*K_prev + RSV)/3，D = (2*D_prev + K)/3，J = 3K - 2D。
    """
    k_values: List[Optional[float]] = []
    d_values: List[Optional[float]] = []
    j_values: List[Optional[float]] = []
    rsv_values: List[Optional[float]] = []
    ready: List[bool] = []

    previous_k = previous_d = 50.0
    run_start = 0

    for index in range(len(closes)):
        high = highs[index]
        low = lows[index]
        close = closes[index]
        bad = (
            high is None or low is None or close is None
            or high != high or low != low or close != close
            or high <= 0 or low <= 0 or close <= 0
            or high < low
        )
        if bad:
            run_start = index + 1
            previous_k = previous_d = 50.0
            k_values.append(None)
            d_values.append(None)
            j_values.append(None)
            rsv_values.append(None)
            ready.append(False)
            continue
        window_start = max(run_start, index - n + 1)
        window_high = max(highs[window_start : index + 1])
        window_low = min(lows[window_start : index + 1])
        if not window_high > window_low:
            run_start = index + 1
            previous_k = previous_d = 50.0
            k_values.append(None)
            d_values.append(None)
            j_values.append(None)
            rsv_values.append(None)
            ready.append(False)
            continue
        count = index - run_start + 1
        if count < n:
            k_values.append(None)
            d_values.append(None)
            j_values.append(None)
            rsv_values.append(None)
            ready.append(False)
            continue
        if count == n:
            previous_k = previous_d = 50.0
        rsv = 100.0 * (close - window_low) / (window_high - window_low)
        current_k = (2.0 * previous_k + rsv) / 3.0
        current_d = (2.0 * previous_d + current_k) / 3.0
        previous_k, previous_d = current_k, current_d
        rsv_values.append(rsv)
        k_values.append(current_k)
        d_values.append(current_d)
        j_values.append(3.0 * current_k - 2.0 * current_d)
        ready.append(True)
    return {"rsv": rsv_values, "k": k_values, "d": d_values, "j": j_values, "ready": ready}


def naive_cross_up(left: Sequence[Optional[float]], right: Sequence[Optional[float]]) -> List[bool]:
    out: List[bool] = []
    for index in range(len(left)):
        if index == 0:
            out.append(False)
            continue
        a0, b0, a1, b1 = left[index - 1], right[index - 1], left[index], right[index]
        if None in (a0, b0, a1, b1):
            out.append(False)
            continue
        out.append(bool(a0 <= b0 and a1 > b1))
    return out


def naive_cross_down(left: Sequence[Optional[float]], right: Sequence[Optional[float]]) -> List[bool]:
    out: List[bool] = []
    for index in range(len(left)):
        if index == 0:
            out.append(False)
            continue
        a0, b0, a1, b1 = left[index - 1], right[index - 1], left[index], right[index]
        if None in (a0, b0, a1, b1):
            out.append(False)
            continue
        out.append(bool(a0 >= b0 and a1 < b1))
    return out
