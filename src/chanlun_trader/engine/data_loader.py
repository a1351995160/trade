"""把现有 TDX 本地数据层装入 V2 MarketDataStore。

保留 TDX 数据层；Strategy 不直接访问 TQ/DataAdapter。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from .asof import MarketDataStore
from ..research.guard import RESEARCH_END


def _split_symbol(symbol: str) -> Tuple[str, int]:
    code, _, mkt = symbol.partition(".")
    market = 1 if mkt.upper() in ("SH",) else 0
    return code, market


def load_daily_store(tdx, symbols: Sequence[str], start_date: Optional[int] = None,
                     end_date: Optional[int] = None, load_qfq: bool = True) -> MarketDataStore:
    """从 TdxData 读取日线 raw / qfq 并装入 MarketDataStore。"""
    store = MarketDataStore()
    for sym in symbols:
        code, market = _split_symbol(sym)
        raw = tdx.get_day(code, market)
        if raw is None or raw.empty:
            continue
        raw = raw.copy()
        raw["date"] = raw["date"].astype(int)
        if start_date:
            raw = raw[raw["date"] >= int(start_date)]
        if end_date:
            raw = raw[raw["date"] <= int(end_date)]
        if raw.empty:
            continue
        raw["prev_close"] = raw["close"].shift(1)
        raw_df = raw.set_index("date")
        raw_df = raw_df[["open", "high", "low", "close", "volume", "amount", "prev_close"]]
        store.add_daily_raw(sym, raw_df)
        if load_qfq:
            # Formal research loading must be PIT-safe; full-sample qfq is V1-only.
            q = tdx.get_qfq_day_pit(code, market, as_of=int(end_date or RESEARCH_END))
            if q is not None and not q.empty:
                q = q.copy()
                q["date"] = q["date"].astype(int)
                if start_date:
                    q = q[q["date"] >= int(start_date)]
                if end_date:
                    q = q[q["date"] <= int(end_date)]
                q_df = q.set_index("date")
                q_df = q_df[["qfq_open", "qfq_high", "qfq_low", "qfq_close", "volume", "amount"]]
                q_df = q_df.rename(columns={
                    "qfq_open": "open", "qfq_high": "high",
                    "qfq_low": "low", "qfq_close": "close",
                })
                store.add_daily_qfq(sym, q_df)
    return store


def load_index_closes(tdx, code: str = "sh000001",
                      start_date: Optional[int] = None,
                      end_date: Optional[int] = None) -> Dict[int, float]:
    """读取指数收盘价 dict[date] = close（供 V2 指数过滤，只用严格早于当日的值）。"""
    df = tdx.get_benchmark(code)
    if df is None or df.empty:
        return {}
    df = df.copy()
    df["date"] = df["date"].astype(int)
    if start_date:
        df = df[df["date"] >= int(start_date)]
    if end_date:
        df = df[df["date"] <= int(end_date)]
    return dict(zip(df["date"], df["close"].astype(float)))
