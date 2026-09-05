"""Provider 原始字段到研究标准 5m schema 的转换。"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from .base import (BAR_TIMESTAMP_SEMANTICS, BarTimestampSemantics, NORMALIZED_COLUMNS,
                   normalize_symbol, parse_timestamp, source_symbol)
from .validator import validate_5m


def normalize_5m_frame(df: pd.DataFrame, symbol: str, source: str,
                       semantics: BarTimestampSemantics = BAR_TIMESTAMP_SEMANTICS,
                       fetched_at: str | None = None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=NORMALIZED_COLUMNS)
    symbol = normalize_symbol(symbol)
    out = df.copy()
    if "timestamp" not in out:
        out["timestamp"] = [
            parse_timestamp(row.get("datetime"), row.get("date"), row.get("time"))
            for _, row in out.iterrows()
        ]
    else:
        out["timestamp"] = [parse_timestamp(x) for x in out["timestamp"]]
    if "volume" not in out and "vol" in out:
        out["volume"] = out["vol"]
    if "source_symbol" not in out and "code" in out:
        out["source_symbol"] = out["code"]
    out = out.drop(columns=[col for col in ("vol", "code") if col in out], errors="ignore")
    out["symbol"] = symbol
    out["source"] = source
    out["source_symbol"] = out.get("source_symbol", source_symbol(symbol))
    out["fetched_at"] = fetched_at or datetime.now().astimezone().isoformat()
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        out[col] = pd.to_numeric(out.get(col), errors="coerce")
    out["trade_date"] = out["timestamp"].dt.strftime("%Y%m%d").astype("int64")
    out["bar_time"] = out["timestamp"].dt.strftime("%H:%M")
    out = out[["symbol", "timestamp", "trade_date", "bar_time", "open", "high", "low",
               "close", "volume", "amount", "source", "source_symbol", "fetched_at"]]
    out = out.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    validate_5m(out, semantics, strict=True)
    return out
