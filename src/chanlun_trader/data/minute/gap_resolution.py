"""5m session gap evidence and conservative classification helpers."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import pandas as pd

from .validator import expected_session_times
from .base import BAR_TIMESTAMP_SEMANTICS


@dataclass(frozen=True)
class GapEvidence:
    symbol: str
    trade_date: int
    expected_count: int
    observed_count: int
    missing_bar_times: tuple[str, ...]
    baostock_rows: int
    tdx_raw_rows: int
    tdx_local_lc5_rows: int
    daily_rows: int
    daily_volume: float | None
    security_state: str
    suspension_state: str
    classification: str
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def bar_times(frame: pd.DataFrame) -> set[str]:
    if frame is None or frame.empty:
        return set()
    if "bar_time" in frame:
        return set(frame["bar_time"].astype(str))
    if "timestamp" in frame:
        return set(pd.to_datetime(frame["timestamp"]).dt.strftime("%H:%M"))
    return set()


def classify_gap(observed_count: int, expected_count: int, daily_rows: int,
                 daily_volume: float | None, explicit_suspension: bool = False,
                 cross_source_evidence: bool = False) -> str:
    if observed_count == expected_count:
        return "EXPECTED_FULL_SESSION"
    if observed_count == 0 and explicit_suspension:
        return "EXPECTED_SUSPENSION"
    if observed_count != expected_count and cross_source_evidence:
        return "CROSS_SOURCE_CONFLICT"
    if observed_count > 0 and daily_rows > 0 and (daily_volume or 0) > 0:
        return "PROVIDER_DATA_GAP"
    if observed_count == 0:
        return "UNKNOWN"
    return "UNKNOWN"


def build_gap_evidence(symbol: str, trade_date: int, normalized: pd.DataFrame,
                       baostock: pd.DataFrame, tdx_raw: pd.DataFrame,
                       tdx_local: pd.DataFrame, daily: pd.DataFrame,
                       security_state: str, suspension_state: str,
                       explicit_suspension: bool = False,
                       cross_source_evidence: bool = False) -> GapEvidence:
    expected = expected_session_times(BAR_TIMESTAMP_SEMANTICS)
    actual = bar_times(normalized)
    daily_row = daily[daily["date"] == trade_date] if daily is not None and not daily.empty and "date" in daily else pd.DataFrame()
    volume = float(daily_row.iloc[0]["volume"]) if not daily_row.empty and "volume" in daily_row else None
    return GapEvidence(
        symbol=symbol,
        trade_date=int(trade_date),
        expected_count=len(expected),
        observed_count=len(actual),
        missing_bar_times=tuple(sorted(expected - actual)),
        baostock_rows=int(len(baostock)),
        tdx_raw_rows=int(len(tdx_raw)),
        tdx_local_lc5_rows=int(len(tdx_local)),
        daily_rows=int(len(daily_row)),
        daily_volume=volume,
        security_state=security_state,
        suspension_state=suspension_state,
        classification=classify_gap(
            len(actual), len(expected), len(daily_row), volume,
            explicit_suspension, cross_source_evidence,
        ),
        evidence=tuple([
            f"normalized_rows={len(normalized)}",
            f"baostock_rows={len(baostock)}",
            f"tdx_raw_rows={len(tdx_raw)}",
            f"tdx_local_lc5_rows={len(tdx_local)}",
            f"local_daily_rows={len(daily_row)}",
            "BaoStock empty data is not treated as suspension evidence",
        ]),
    )
