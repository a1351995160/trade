"""Physical IO safety for research data access.

Guarantees:
- Research mode never physically materializes rows with date > RESEARCH_END.
- Parquet reads use PyArrow predicate pushdown so disallowed row groups are not loaded.
- TDX .day / .lc5 binary files use record-level seek + binary search by date; future
  OHLCV records are never handed to the market-data parser.
- All reads are recorded in the physical read audit (requested range, materialized range,
  rows materialized, max date materialized).
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from .guard import FINAL_TEST_START, RESEARCH_END, ResearchDataAccessGuard

AUDIT_PATH = Path("data/research/audit/physical_read_audit.jsonl")
AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)

_DAY_STRUCT = struct.Struct("<IIIIIfI4s")
_LC5_STRUCT = struct.Struct("<HHfffffII")
LC5_RECORD_SIZE = 32
DAY_RECORD_SIZE = 32


def _log_physical_read(**rec):
    with open(AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _day_date_at(path: str, idx: int) -> int:
    with open(path, "rb") as f:
        f.seek(idx * DAY_RECORD_SIZE)
        raw = f.read(4)
    if len(raw) < 4:
        raise IndexError(f"record {idx} out of range for {path}")
    return int(struct.unpack("<I", raw)[0])


def _lc5_date_at(path: str, idx: int) -> int:
    with open(path, "rb") as f:
        f.seek(idx * LC5_RECORD_SIZE)
        raw = f.read(2)
    if len(raw) < 2:
        raise IndexError(f"record {idx} out of range for {path}")
    code = int(struct.unpack("<H", raw)[0])
    year = code // 2048 + 2004
    month = (code % 2048) // 100
    day = (code % 2048) % 100
    return year * 10000 + month * 100 + day


def _rightmost_le(path: str, n_records: int, allowed_end: int, kind: str) -> int:
    """Binary search rightmost record index whose date <= allowed_end. Reads only date bytes."""
    lo, hi = 0, n_records - 1
    ans = -1
    fn = _day_date_at if kind == "day" else _lc5_date_at
    while lo <= hi:
        mid = (lo + hi) // 2
        d = fn(path, mid)
        if d <= allowed_end:
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def read_day_file_range(path, start_date: int = 0, end_date: int = RESEARCH_END,
                        guard: ResearchDataAccessGuard | None = None) -> pd.DataFrame:
    g = guard or ResearchDataAccessGuard()
    g.check_range(start_date, end_date, f"day {path}")
    path = str(path)
    size = Path(path).stat().st_size
    n = size // DAY_RECORD_SIZE
    if n == 0:
        _log_physical_read(dataset="day", path=path, requested=[start_date, end_date],
                           physical_records=0, rows_materialized=0, max_date_materialized=None)
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])
    first = _day_date_at(path, 0)
    if first > end_date:
        _log_physical_read(dataset="day", path=path, requested=[start_date, end_date],
                           physical_records=0, rows_materialized=0, max_date_materialized=None)
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])
    lo = 0
    if start_date > 0:
        # leftmost date >= start_date
        l0, h0 = 0, n - 1
        while l0 <= h0:
            mid = (l0 + h0) // 2
            if _day_date_at(path, mid) < start_date:
                l0 = mid + 1
            else:
                h0 = mid - 1
        lo = l0
        if lo >= n:
            lo = 0
    hi = _rightmost_le(path, n, end_date, "day")
    if hi < lo:
        _log_physical_read(dataset="day", path=path, requested=[start_date, end_date],
                           physical_records=0, rows_materialized=0, max_date_materialized=None)
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])
    n_sel = hi - lo + 1
    with open(path, "rb") as f:
        f.seek(lo * DAY_RECORD_SIZE)
        content = f.read(n_sel * DAY_RECORD_SIZE)
    recs = np.frombuffer(content, dtype=np.uint8).reshape(n_sel, DAY_RECORD_SIZE)
    date = recs[:, 0:4].copy().view(np.uint32).reshape(-1).astype(np.int64)
    open_ = recs[:, 4:8].copy().view(np.uint32).reshape(-1).astype(np.float64) / 100.0
    high = recs[:, 8:12].copy().view(np.uint32).reshape(-1).astype(np.float64) / 100.0
    low = recs[:, 12:16].copy().view(np.uint32).reshape(-1).astype(np.float64) / 100.0
    close = recs[:, 16:20].copy().view(np.uint32).reshape(-1).astype(np.float64) / 100.0
    amount = recs[:, 20:24].copy().view(np.float32).reshape(-1).astype(np.float64)
    volume = recs[:, 24:28].copy().view(np.uint32).reshape(-1).astype(np.int64)
    df = pd.DataFrame(dict(date=date, open=open_, high=high, low=low, close=close,
                           volume=volume, amount=amount))
    df = df[(df["date"] >= start_date) & (df["date"] <= end_date)].sort_values("date").reset_index(drop=True)
    _log_physical_read(dataset="day", path=path, requested=[start_date, end_date],
                       physical_records=int(n_sel), rows_materialized=len(df),
                       max_date_materialized=int(df["date"].max()) if len(df) else None)
    g.check_frame(df, "date")
    return df


def read_lc5_file_range(path, start_date: int = 0, end_date: int = RESEARCH_END,
                        guard: ResearchDataAccessGuard | None = None) -> pd.DataFrame:
    g = guard or ResearchDataAccessGuard()
    g.check_range(start_date, end_date, f"lc5 {path}")
    path = str(path)
    size = Path(path).stat().st_size
    n = size // LC5_RECORD_SIZE
    empty = pd.DataFrame(columns=["date", "minute", "open", "high", "low", "close", "amount", "volume"])
    if n == 0:
        _log_physical_read(dataset="lc5", path=path, requested=[start_date, end_date],
                           physical_records=0, rows_materialized=0, max_date_materialized=None)
        return empty
    first = _lc5_date_at(path, 0)
    if first > end_date:
        _log_physical_read(dataset="lc5", path=path, requested=[start_date, end_date],
                           physical_records=0, rows_materialized=0, max_date_materialized=None)
        return empty
    lo = 0
    if start_date > 0:
        l0, h0 = 0, n - 1
        while l0 <= h0:
            mid = (l0 + h0) // 2
            if _lc5_date_at(path, mid) < start_date:
                l0 = mid + 1
            else:
                h0 = mid - 1
        lo = l0
    hi = _rightmost_le(path, n, end_date, "lc5")
    if hi < lo:
        _log_physical_read(dataset="lc5", path=path, requested=[start_date, end_date],
                           physical_records=0, rows_materialized=0, max_date_materialized=None)
        return empty
    n_sel = hi - lo + 1
    with open(path, "rb") as f:
        f.seek(lo * LC5_RECORD_SIZE)
        content = f.read(n_sel * LC5_RECORD_SIZE)
    recs = np.frombuffer(content, dtype=np.uint8).reshape(n_sel, LC5_RECORD_SIZE)
    date_code = recs[:, 0:2].copy().view(np.uint16).reshape(-1)
    minute_code = recs[:, 2:4].copy().view(np.uint16).reshape(-1)
    open_ = recs[:, 4:8].copy().view(np.float32).reshape(-1).astype(np.float64)
    high = recs[:, 8:12].copy().view(np.float32).reshape(-1).astype(np.float64)
    low = recs[:, 12:16].copy().view(np.float32).reshape(-1).astype(np.float64)
    close = recs[:, 16:20].copy().view(np.float32).reshape(-1).astype(np.float64)
    amount = recs[:, 20:24].copy().view(np.float32).reshape(-1).astype(np.float64)
    volume = recs[:, 24:28].copy().view(np.uint32).reshape(-1).astype(np.int64)

    def _d(c):
        y = int(c) // 2048 + 2004
        m = (int(c) % 2048) // 100
        dd = (int(c) % 2048) % 100
        return y * 10000 + m * 100 + dd

    def _m(c):
        return (int(c) // 60) * 100 + (int(c) % 60)

    df = pd.DataFrame(dict(
        date=np.array([_d(c) for c in date_code], dtype=np.int64),
        minute=np.array([_m(c) for c in minute_code], dtype=np.int64),
        open=open_, high=high, low=low, close=close, amount=amount, volume=volume))
    df = df[(df["date"] >= start_date) & (df["date"] <= end_date)].sort_values(["date", "minute"]).reset_index(drop=True)
    _log_physical_read(dataset="lc5", path=path, requested=[start_date, end_date],
                       physical_records=int(n_sel), rows_materialized=len(df),
                       max_date_materialized=int(df["date"].max()) if len(df) else None)
    g.check_frame(df, "date")
    return df


class GuardedResearchReader:
    """Parquet reader with PyArrow predicate pushdown + physical read audit."""

    def __init__(self, guard: ResearchDataAccessGuard | None = None):
        self.guard = guard or ResearchDataAccessGuard()

    def read_parquet(self, path, columns=None, date_column="date",
                     start_date: int = 0, end_date: int = RESEARCH_END,
                     date_columns: Iterable[str] | None = None) -> pd.DataFrame:
        self.guard.check_range(start_date, end_date, f"parquet {path}")
        path = str(path)
        col_list = list(columns) if columns is not None else None
        filters = None
        filter_columns = list(date_columns) if date_columns is not None else ([date_column] if date_column else [])
        for col in filter_columns:
            current = ds.field(col) >= start_date
            current = current & (ds.field(col) <= end_date)
            filters = current if filters is None else filters & current
        table = ds.dataset(path, format="parquet").to_table(columns=col_list, filter=filters)
        df = table.to_pandas()
        max_date = int(df[date_column].max()) if date_column in df and len(df) else None
        _log_physical_read(dataset="parquet", path=path, requested=[start_date, end_date],
                           date_column=date_column, physical_rows=table.num_rows,
                           rows_materialized=len(df), max_date_materialized=max_date)
        for col in filter_columns:
            self.guard.check_frame(df, col)
        return df

    def read_5m(self, path, start_date: int = 0,
                end_date: int = RESEARCH_END, columns=None) -> pd.DataFrame:
        """读取 normalized 5m store；过滤在 Arrow 物理读取前生效。"""
        return self.read_parquet(path, columns=columns, date_column="trade_date",
                                 start_date=start_date, end_date=end_date)
