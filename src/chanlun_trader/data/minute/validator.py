"""历史 5m 数据质量校验。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .base import BarTimestampSemantics, NORMALIZED_COLUMNS

BAR_END_TIMES = {
    f"{h:02d}:{m:02d}"
    for h, m in [(9, 35), (9, 40), (9, 45), (9, 50), (9, 55),
                 *[(10, m) for m in range(0, 60, 5)],
                 *[(11, m) for m in range(0, 35, 5)],
                 *[(13, m) for m in range(5, 60, 5)],
                 *[(14, m) for m in range(0, 60, 5)],
                 (15, 0)]
}
BAR_START_TIMES = {
    f"{h:02d}:{m:02d}"
    for h, m in [(9, 30), (9, 35), (9, 40), (9, 45), (9, 50),
                 *[(10, m) for m in range(0, 55, 5)],
                 *[(11, m) for m in range(0, 30, 5)],
                 (13, 0), *[(13, m) for m in range(5, 55, 5)],
                 *[(14, m) for m in range(0, 55, 5)]]
}


class DataQualityError(ValueError):
    pass


@dataclass(frozen=True)
class QualityReport:
    row_count: int
    duplicate_timestamp_count: int
    malformed_count: int
    impossible_ohlc_count: int
    negative_volume_count: int
    negative_amount_count: int
    invalid_session_count: int
    missing_bar_count: int = 0

    @property
    def ok(self) -> bool:
        return not any((self.duplicate_timestamp_count, self.malformed_count,
                        self.impossible_ohlc_count, self.negative_volume_count,
                        self.negative_amount_count, self.invalid_session_count,
                        self.missing_bar_count))

    def to_dict(self) -> dict[str, int | bool]:
        return {**self.__dict__, "ok": self.ok}


def expected_session_times(semantics: BarTimestampSemantics) -> set[str]:
    return BAR_START_TIMES if semantics == BarTimestampSemantics.BAR_START else BAR_END_TIMES


def _numeric_invalid(df: pd.DataFrame) -> int:
    numeric = ["open", "high", "low", "close", "volume", "amount"]
    invalid = 0
    for col in numeric:
        if col not in df:
            invalid += len(df)
            continue
        invalid += int(pd.to_numeric(df[col], errors="coerce").isna().sum())
    return invalid


def validate_5m(df: pd.DataFrame, semantics: BarTimestampSemantics,
                strict: bool = True) -> QualityReport:
    missing = [c for c in NORMALIZED_COLUMNS if c not in df.columns]
    if missing:
        raise DataQualityError(f"missing normalized columns: {missing}")
    frame = df.copy()
    ts = pd.to_datetime(frame["timestamp"], errors="coerce")
    malformed = int(ts.isna().sum()) + _numeric_invalid(frame)
    duplicate_key = ["symbol", "timestamp"] if "symbol" in frame.columns else ["timestamp"]
    duplicate = int(frame.duplicated(duplicate_key).sum())
    impossible = int(((frame["high"] < frame[["open", "close", "low"]].max(axis=1)) |
                      (frame["low"] > frame[["open", "close", "high"]].min(axis=1))).sum())
    negative_volume = int((pd.to_numeric(frame["volume"], errors="coerce") < 0).sum())
    negative_amount = int((pd.to_numeric(frame["amount"], errors="coerce") < 0).sum())
    times = ts.dt.strftime("%H:%M")
    invalid_session = int((~times.isin(expected_session_times(semantics))).sum())
    report = QualityReport(
        row_count=len(frame), duplicate_timestamp_count=duplicate,
        malformed_count=malformed, impossible_ohlc_count=impossible,
        negative_volume_count=negative_volume, negative_amount_count=negative_amount,
        invalid_session_count=invalid_session,
    )
    if strict and not report.ok:
        raise DataQualityError(str(report.to_dict()))
    return report


def audit_missing_bars(df: pd.DataFrame, semantics: BarTimestampSemantics,
                       expected_trade_dates: set[int] | None = None) -> pd.DataFrame:
    """输出缺口审计，不把停牌/未上市自动伪装成普通缺失。"""
    expected_count = len(expected_session_times(semantics))
    observed = df.groupby(["symbol", "trade_date"], dropna=False).size().rename("observed_count")
    rows = []
    for (symbol, trade_date), count in observed.items():
        rows.append({"symbol": symbol, "trade_date": int(trade_date),
                     "expected_count": expected_count, "observed_count": int(count),
                     "status": "COMPLETE" if count == expected_count else "OBSERVED_PARTIAL_SESSION"})
    if expected_trade_dates:
        symbols = sorted(df["symbol"].unique())
        for symbol in symbols:
            for trade_date in sorted(expected_trade_dates):
                if (symbol, trade_date) not in observed.index:
                    rows.append({"symbol": symbol, "trade_date": trade_date,
                                 "expected_count": expected_count, "observed_count": 0,
                                 "status": "UNKNOWN_NO_DATA_OR_SUSPENDED"})
    return pd.DataFrame(rows, columns=["symbol", "trade_date", "expected_count", "observed_count", "status"])
