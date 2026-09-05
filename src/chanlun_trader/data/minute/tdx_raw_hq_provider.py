"""TDX 7709 Raw HQ Provider：SECONDARY/RECENT/GAP_FILL/CROSS_CHECK。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

import pandas as pd

from .base import BarTimestampSemantics, normalize_symbol

DEFAULT_SERVERS = (
    ("shanghai-telecom-z1", "180.153.18.170", 7709),
    ("hangzhou-telecom-j1", "60.191.117.167", 7709),
    ("hangzhou-telecom-j3", "218.75.126.9", 7709),
    ("chengdu-telecom-1", "218.6.170.47", 7709),
    ("beijing-unicom-1", "123.125.108.14", 7709),
)


class TdxRawHQProvider:
    source = "tdx-raw-hq"
    timestamp_semantics = BarTimestampSemantics.BAR_END
    role = "SECONDARY/RECENT/GAP_FILL/CROSS_CHECK"

    def __init__(self, servers=DEFAULT_SERVERS, api_factory: Callable[[], Any] | None = None,
                 max_pages: int = 200, initial_start: int = 0):
        self.servers = tuple(servers)
        self.api_factory = api_factory
        self.max_pages = max_pages
        self.initial_start = max(0, int(initial_start))
        self.failures: list[dict[str, str]] = []

    def _api(self):
        if self.api_factory:
            return self.api_factory()
        from pytdx.hq import TdxHq_API
        return TdxHq_API()

    @staticmethod
    def _market(symbol: str) -> tuple[int, str]:
        code, market = normalize_symbol(symbol).split(".")
        return (1 if market == "SH" else 0), code

    def fetch(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        symbol = normalize_symbol(symbol)
        market, code = self._market(symbol)
        for name, host, port in self.servers:
            api = self._api()
            try:
                if not api.connect(host, port, time_out=3.0):
                    self.failures.append({"server": name, "status": "connect_failed"})
                    continue
                rows: list[dict[str, Any]] = []
                for page in range(self.max_pages):
                    batch = api.get_security_bars(0, market, code, self.initial_start + page * 800, 800) or []
                    if not batch:
                        break
                    rows.extend(batch)
                    dates = [self._row_ts(row) for row in batch]
                    if dates and min(dates).date() <= start_date:
                        break
                frame = pd.DataFrame(rows)
                if frame.empty:
                    self.failures.append({"server": name, "status": "empty"})
                    continue
                frame["timestamp"] = [self._row_ts(row) for row in rows]
                frame = frame[(frame["timestamp"].dt.date >= start_date) &
                              (frame["timestamp"].dt.date <= end_date)]
                frame = frame.rename(columns={"vol": "volume"})
                frame["symbol"] = symbol
                frame["source"] = self.source
                frame["source_symbol"] = f"{market}:{code}"
                frame["fetched_at"] = datetime.now().astimezone().isoformat()
                return frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
            except Exception as exc:
                self.failures.append({"server": name, "status": "request_failed", "error": f"{type(exc).__name__}: {exc}"})
            finally:
                try:
                    api.disconnect()
                except Exception:
                    pass
        raise RuntimeError(f"TDX Raw HQ all servers failed for {symbol}: {self.failures[-len(self.servers):]}")

    @staticmethod
    def _row_ts(row: dict[str, Any]) -> pd.Timestamp:
        value = row.get("datetime") or row.get("date")
        ts = pd.Timestamp(value)
        return ts.tz_localize("Asia/Shanghai") if ts.tzinfo is None else ts.tz_convert("Asia/Shanghai")

    def capability(self) -> dict[str, str]:
        return {"source": self.source, "role": self.role,
                "timestamp_semantics": self.timestamp_semantics.value,
                "pagination": "start_offset", "failover": "enabled"}
