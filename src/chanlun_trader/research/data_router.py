"""Fail-closed research data routing for the Phase 4 V2 rerun.

The router keeps the frozen BaoStock 5-minute training dataset separate from
later-window TDX ``.lc5`` reference data.  It never silently stitches the
known 2024-08-01..2024-10-08 gap and every date range is checked by the
ResearchDataAccessGuard before a physical read.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from concurrent.futures import ThreadPoolExecutor

from .guard import RESEARCH_END, ResearchDataAccessGuard
from .io_safety import GuardedResearchReader, read_lc5_file_range


@dataclass(frozen=True)
class RoutedFiveMinuteResult:
    status: str
    source: str
    frame: pd.DataFrame
    reason: str = ""


class ResearchDataRouter:
    """Route daily, canonical 5m, and later TDX reference reads safely."""

    def __init__(self, repo_root: str | Path, guard: ResearchDataAccessGuard | None = None,
                 tdx_root: str | Path = "E:/new_tdx_mock"):
        self.repo_root = Path(repo_root)
        self.guard = guard or ResearchDataAccessGuard()
        self.reader = GuardedResearchReader(self.guard)
        self.tdx_root = Path(tdx_root)
        self.routing_policy_path = self.repo_root / "data/research/data_routing/routing_policy.json"
        self.routing_policy = json.loads(self.routing_policy_path.read_text(encoding="utf-8"))
        self.train_root = self.repo_root / "data/research/market_5m"
        self.daily_path = self.repo_root / "data/research/daily_all.parquet"
        self._lc5_index: dict[str, Path] | None = None
        self._train_datasets: dict[tuple[int, int], ds.Dataset] = {}
        self._train_file_index: dict[tuple[int, int, str], Path] = {}

    @staticmethod
    def _date(value: Any) -> int:
        if isinstance(value, int):
            return value
        text = str(value).replace("-", "")[:8]
        return int(text)

    def _check(self, start_date: int, end_date: int, label: str) -> tuple[int, int]:
        start = self._date(start_date)
        end = self._date(end_date)
        if start > end:
            raise ValueError(f"invalid range: {start}>{end}")
        self.guard.check_range(start, end, label)
        return start, end

    def read_daily(self, start_date: int, end_date: int,
                   columns: Iterable[str] | None = None) -> pd.DataFrame:
        start, end = self._check(start_date, end_date, "router daily")
        return self.reader.read_parquet(
            self.daily_path, columns=list(columns) if columns is not None else None,
            date_column="date", start_date=start, end_date=end,
        )

    def read_train_5m(self, symbol: str, trade_date: int,
                      columns: Iterable[str] | None = None) -> RoutedFiveMinuteResult:
        """Read one symbol/date from the frozen BaoStock normalized dataset."""
        date = self._date(trade_date)
        if date < 20220801 or date > 20240731:
            return RoutedFiveMinuteResult("DEFERRED_OR_GAP", "TRAIN_5M_DATASET_V1", pd.DataFrame(), "outside frozen BaoStock window")
        self.guard.check_range(date, date, f"router train 5m {symbol}")
        month_key = (date // 10000, (date // 100) % 100, str(symbol))
        indexed_path = self._train_file_index.get(month_key)
        if indexed_path is not None:
            table = pq.read_table(
                indexed_path,
                columns=list(columns) if columns is not None else None,
                filters=[("symbol", "=", str(symbol)), ("trade_date", "=", date)],
            )
            frame = table.to_pandas()
            if "trade_date" in frame.columns:
                self.guard.check_frame(frame, "trade_date")
            return RoutedFiveMinuteResult("AVAILABLE" if not frame.empty else "MISSING", "TRAIN_5M_DATASET_V1", frame, str(indexed_path))
        partition = self.train_root / f"year={date // 10000}" / f"month={(date // 100) % 100:02d}"
        cache_key = (date // 10000, (date // 100) % 100)
        if cache_key not in self._train_datasets:
            if not partition.exists():
                return RoutedFiveMinuteResult("MISSING", "TRAIN_5M_DATASET_V1", pd.DataFrame(), "monthly partition not found")
            self._train_datasets[cache_key] = ds.dataset(partition, format="parquet")
        dataset = self._train_datasets[cache_key]
        expr = (ds.field("trade_date") == date) & (ds.field("symbol") == str(symbol))
        table = dataset.to_table(columns=list(columns) if columns is not None else None, filter=expr)
        frame = table.to_pandas()
        if "trade_date" in frame.columns:
            self.guard.check_frame(frame, "trade_date")
        return RoutedFiveMinuteResult("AVAILABLE" if not frame.empty else "MISSING", "TRAIN_5M_DATASET_V1", frame)

    def build_train_file_index(self, requests: Iterable[tuple[str, int]]) -> int:
        """Index frozen symbol-month fragments once using Parquet statistics."""
        grouped: dict[tuple[int, int], set[str]] = {}
        for symbol, value in requests:
            date = self._date(value)
            if 20220801 <= date <= 20240731:
                grouped.setdefault((date // 10000, (date // 100) % 100), set()).add(str(symbol))

        def inspect(path: Path) -> tuple[str, Path] | None:
            metadata = pq.ParquetFile(path).metadata
            stats = metadata.row_group(0).column(0).statistics
            if stats is None or not stats.has_min_max or str(stats.min) != str(stats.max):
                return None
            return str(stats.min), path

        found = 0
        for (year, month), wanted in sorted(grouped.items()):
            partition = self.train_root / f"year={year}" / f"month={month:02d}"
            if not partition.exists():
                continue
            paths = list(partition.glob("*.parquet"))
            with ThreadPoolExecutor(max_workers=12) as executor:
                for item in executor.map(inspect, paths):
                    if item is None:
                        continue
                    symbol, path = item
                    if symbol in wanted:
                        key = (year, month, symbol)
                        if key not in self._train_file_index:
                            self._train_file_index[key] = path
                            found += 1
        return found

    def save_train_file_index(self, path: str | Path) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"year": year, "month": month, "symbol": symbol, "path": str(file_path)}
            for (year, month, symbol), file_path in sorted(self._train_file_index.items())
        ]
        output.write_text(json.dumps({"schema_version": "train-5m-file-index-v1", "entries": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def load_train_file_index(self, path: str | Path) -> int:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema_version") != "train-5m-file-index-v1":
            raise ValueError("train 5m file index schema mismatch")
        self._train_file_index = {
            (int(row["year"]), int(row["month"]), str(row["symbol"])): Path(row["path"])
            for row in payload.get("entries", [])
        }
        return len(self._train_file_index)

    def _index_lc5(self) -> dict[str, Path]:
        if self._lc5_index is None:
            self._lc5_index = {}
            if self.tdx_root.exists():
                for path in self.tdx_root.glob("vipdoc/*/fzline/*.lc5"):
                    stem = path.stem.upper()
                    if len(stem) == 8 and stem[:2] in {"SH", "SZ"} and stem[2:].isdigit():
                        self._lc5_index.setdefault(f"{stem[2:]}.{stem[:2]}", path)
        return self._lc5_index

    def read_tdx_lc5(self, symbol: str, start_date: int, end_date: int) -> RoutedFiveMinuteResult:
        start, end = self._check(start_date, end_date, f"router TDX lc5 {symbol}")
        if start < 20241009:
            return RoutedFiveMinuteResult("DEFERRED_OR_GAP", "TDX_LOCAL_LC5", pd.DataFrame(), "TDX later-window source begins 2024-10-09")
        path = self._index_lc5().get(str(symbol).upper())
        if path is None:
            return RoutedFiveMinuteResult("MISSING", "TDX_LOCAL_LC5", pd.DataFrame(), "symbol .lc5 file not found")
        frame = read_lc5_file_range(path, start_date=start, end_date=end, guard=self.guard)
        return RoutedFiveMinuteResult("AVAILABLE" if not frame.empty else "MISSING", "TDX_LOCAL_LC5", frame, str(path))

    def route_event_5m(self, symbol: str, trade_date: int) -> RoutedFiveMinuteResult:
        """Apply the frozen routing policy; the known gap is fail-closed."""
        date = self._date(trade_date)
        if 20220801 <= date <= 20240731:
            return self.read_train_5m(symbol, date)
        if 20240801 <= date <= 20241008:
            self.guard.check_range(date, date, f"router known 5m gap {symbol}")
            return RoutedFiveMinuteResult("GAP_FAIL_CLOSED", "NONE", pd.DataFrame(), "known 5m coverage gap")
        if 20241009 <= date <= RESEARCH_END:
            return self.read_tdx_lc5(symbol, date, date)
        return RoutedFiveMinuteResult("OUT_OF_SCOPE", "NONE", pd.DataFrame(), "date outside governed research windows")
