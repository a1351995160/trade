"""统一的历史 5 分钟数据契约。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

import pandas as pd

TZ = "Asia/Shanghai"
BAR_COLUMNS = (
    "symbol", "timestamp", "open", "high", "low", "close", "volume", "amount",
    "source", "source_symbol", "fetched_at",
)
NORMALIZED_COLUMNS = (
    "symbol", "timestamp", "trade_date", "bar_time", "open", "high", "low", "close",
    "volume", "amount", "source",
)


class BarTimestampSemantics(StrEnum):
    BAR_START = "BAR_START"
    BAR_END = "BAR_END"
    UNKNOWN = "UNKNOWN"


# BaoStock 返回的 5 分钟时刻经核验为 09:35...15:00 的结束标签。
BAR_TIMESTAMP_SEMANTICS = BarTimestampSemantics.BAR_END


def normalize_symbol(value: str) -> str:
    value = str(value).strip().upper()
    if "." in value:
        code, market = value.split(".", 1)
        if len(code) == 6 and code.isdigit() and market in {"SH", "SZ"}:
            return f"{code}.{market}"
    if value.startswith(("SH", "SZ")) and len(value) == 8 and value[2:].isdigit():
        return f"{value[2:]}.{value[:2]}"
    if len(value) == 6 and value.isdigit():
        market = "SH" if value.startswith(("5", "6", "9")) else "SZ"
        return f"{value}.{market}"
    raise ValueError(f"invalid A-share symbol: {value}")


def source_symbol(value: str) -> str:
    symbol = normalize_symbol(value)
    code, market = symbol.split(".")
    return f"{market.lower()}.{code}"


def parse_timestamp(value: Any, date_value: Any = None, time_value: Any = None) -> pd.Timestamp:
    """解析 provider 时间并固定为 Asia/Shanghai。"""
    candidate = value
    if candidate is None or (isinstance(candidate, float) and pd.isna(candidate)):
        candidate = f"{date_value} {time_value}" if time_value is not None else date_value
    if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
        text = str(int(candidate))
        if len(text) >= 14:
            candidate = pd.to_datetime(text[:14], format="%Y%m%d%H%M%S")
        else:
            candidate = pd.to_datetime(text)
    ts = pd.Timestamp(candidate)
    if ts.tzinfo is None:
        ts = ts.tz_localize(TZ)
    else:
        ts = ts.tz_convert(TZ)
    return ts


def date_chunks(start: date, end: date, chunk_days: int = 120):
    if start > end:
        raise ValueError("start date must not be after end date")
    if chunk_days < 1:
        raise ValueError("chunk_days must be positive")
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


class MinuteDataProvider(Protocol):
    source: str
    timestamp_semantics: BarTimestampSemantics

    def fetch(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        ...


@dataclass(frozen=True)
class ChunkRequest:
    symbol: str
    requested_start: date
    requested_end: date


@dataclass
class DownloadReport:
    symbol: str
    requested_start: date
    requested_end: date
    chunks: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.failures:
            return "PARTIAL" if self.chunks else "FAILED"
        return "COMPLETE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "requested_start": self.requested_start.isoformat(),
            "requested_end": self.requested_end.isoformat(),
            "status": self.status,
            "chunks": self.chunks,
            "failures": self.failures,
        }
