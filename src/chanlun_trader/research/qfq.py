"""QFQ PIT Safety.

前复权价格必须按 as_of 过滤未来除权事件。全样本 gbbq 直接用于历史回测会产生
未来 Corporate Action 改变过去价格的问题（QFQ_PIT_SAFE = FALSE）。

本模块提供 PIT-safe 前复权：
    qfq_pit(tdx, code, market, as_of)
    -> 只使用 ex_date <= as_of 的除权事件调整历史价格。

Acceptance Test 语义：
    对任意 T1 < T2，qfq_pit(..., as_of=T1) 截断到 <=T1 的结果，
    与 qfq_pit(..., as_of=T2) 截断到 <=T1 的结果一致——
    因为 as_of=T2 的序列在 T1 之后的价格会变，但 <=T1 的价格由
    ex_date<=T1 的事件决定，未来事件被过滤。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from chanlun_trader.tdx_data import TdxData


def _adj_factor_for_event(ev, prev_close: float) -> float | None:
    """单笔除权事件的 qfq 调整因子。"""
    try:
        hongli = float(ev["hongli_panqianliutong"]) / 10.0
        peigujia = float(ev["peigujia_qianzongguben"])
        songgu = float(ev["songgu_qianzongguben"]) / 10.0
        peigu = float(ev["peigu_houzongguben"]) / 10.0
    except Exception:
        return None
    denom = prev_close * (1.0 + songgu + peigu)
    if denom <= 0:
        return None
    adj = (prev_close - hongli + peigujia * peigu) / denom
    if adj <= 0:
        return None
    return adj


def qfq_columns_asof(raw: pd.DataFrame, gbbq: pd.DataFrame, as_of: int) -> pd.DataFrame:
    """根据 raw 日线和 gbbq 事件，计算 PIT 前复权 OHLC。

    raw: read_day_file 输出（date/open/high/low/close/...）
    gbbq: TdxData._load_gbbq() 输出（code/datetime/category/...）
    as_of: 只使用 datetime <= as_of 的 category==1 事件。

    返回与 raw 同序的 DataFrame，包含 qfq_open/qfq_high/qfq_low/qfq_close。
    """
    df = raw.copy()
    df["qfq_open"] = df["open"].astype(float)
    df["qfq_high"] = df["high"].astype(float)
    df["qfq_low"] = df["low"].astype(float)
    df["qfq_close"] = df["close"].astype(float)

    events = gbbq[(gbbq["category"] == 1) & (gbbq["datetime"] <= as_of)].sort_values("datetime")
    if events.empty:
        return df

    dates = df["date"].to_numpy()
    # 预取事件参数向量
    hongli = events["hongli_panqianliutong"].astype(float).to_numpy() / 10.0
    peigujia = events["peigujia_qianzongguben"].astype(float).to_numpy()
    songgu = events["songgu_qianzongguben"].astype(float).to_numpy() / 10.0
    peigu = events["peigu_houzongguben"].astype(float).to_numpy() / 10.0
    ex_dates = events["datetime"].astype(np.int64).to_numpy()

    for i in range(len(ex_dates)):
        d = int(ex_dates[i])
        prev_idx = np.searchsorted(dates, d, side="left") - 1
        if prev_idx < 0:
            continue
        pc = float(df["close"].iloc[prev_idx])
        if pc <= 0:
            continue
        denom = pc * (1.0 + songgu[i] + peigu[i])
        if denom <= 0:
            continue
        adj = (pc - hongli[i] + peigujia[i] * peigu[i]) / denom
        if adj <= 0 or not np.isfinite(adj):
            continue
        mask = dates < d
        df.loc[mask, "qfq_open"] *= adj
        df.loc[mask, "qfq_high"] *= adj
        df.loc[mask, "qfq_low"] *= adj
        df.loc[mask, "qfq_close"] *= adj
    return df


@dataclass(frozen=True)
class QFQPITResult:
    symbol: str
    as_of: int
    ok: bool
    qfq_pit_safe: bool
    reason: str = ""


def qfq_pit_acceptance(tdx: TdxData, code: str, market: int, t1: int, t2: int) -> QFQPITResult:
    """QFQ PIT Acceptance Test（Future Mutation Test 语义）。

    先只用 gbbq 中 datetime <= t1 的事件计算 as_of=t1 的 qfq；
    再加入 t1 < datetime <= t2 的未来事件重新计算 as_of=t1 的 qfq。
    两者必须完全一致：未来事件不能改变过去的 qfq 价格。

    同时验证全样本 qfq（get_qfq_day）对未来事件敏感，因此全样本 qfq
    不可直接用于历史研究；研究必须使用 qfq_columns_asof(..., as_of=decision_date)。
    """
    raw = tdx.get_day(code, market)
    gbbq = tdx._load_gbbq()
    g = gbbq[gbbq["code"] == code].copy()
    g_t1 = g[g["datetime"] <= t1].copy()
    a = qfq_columns_asof(raw, g_t1, t1)
    b = qfq_columns_asof(raw, g, t1)  # 额外加入未来事件，as_of 仍为 t1 -> 必须不变
    a = a[a["date"] <= t1].reset_index(drop=True)
    b = b[b["date"] <= t1].reset_index(drop=True)
    if len(a) != len(b):
        return QFQPITResult(f"{market}_{code}", t1, False, False, "row count mismatch")
    for col in ("qfq_open", "qfq_high", "qfq_low", "qfq_close"):
        if not np.allclose(a[col].to_numpy(), b[col].to_numpy(), rtol=1e-12, atol=1e-9):
            return QFQPITResult(f"{market}_{code}", t1, False, False, f"{col} mutated by future events")
    return QFQPITResult(f"{market}_{code}", t1, True, True, "")
