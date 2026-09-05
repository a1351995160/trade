"""BaoStock 历史 5m 主 Provider。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterator

import pandas as pd

from .base import BAR_TIMESTAMP_SEMANTICS, BarTimestampSemantics, normalize_symbol, source_symbol

FIELDS = "date,time,code,open,high,low,close,volume,amount,adjustflag"


class BaoStock5MinProvider:
    source = "baostock"
    timestamp_semantics = BAR_TIMESTAMP_SEMANTICS

    def __init__(self, bs_module: Any | None = None):
        self._bs = bs_module
        self._session_active = False

    @property
    def bs(self):
        if self._bs is None:
            import baostock as bs
            self._bs = bs
        return self._bs

    def _login(self) -> None:
        if self._session_active:
            return
        login = self.bs.login()
        if str(login.error_code) != "0":
            raise RuntimeError(f"BaoStock login failed: {login.error_code} {login.error_msg}")
        self._session_active = True

    def _logout(self) -> None:
        if self._session_active:
            try:
                self.bs.logout()
            finally:
                self._session_active = False

    def reset_session(self) -> None:
        """重建可能已被远端关闭的 BaoStock 登录会话。"""
        self._logout()
        self._login()

    @contextmanager
    def session(self) -> Iterator["BaoStock5MinProvider"]:
        """为批量下载复用 BaoStock 登录会话，退出时必定 logout。"""
        owns_session = not self._session_active
        if owns_session:
            self._login()
        try:
            yield self
        finally:
            if owns_session:
                self._logout()

    def fetch(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        symbol = normalize_symbol(symbol)
        code = source_symbol(symbol)
        bs = self.bs
        owns_session = not self._session_active
        if owns_session:
            self._login()
        try:
            result = bs.query_history_k_data_plus(
                code, FIELDS, start_date=start_date.isoformat(), end_date=end_date.isoformat(),
                frequency="5", adjustflag="3",
            )
            if str(result.error_code) != "0":
                raise RuntimeError(f"BaoStock query failed: {result.error_code} {result.error_msg}")
            fields = list(result.fields)
            rows: list[dict[str, Any]] = []
            while result.next():
                row = dict(zip(fields, result.get_row_data()))
                if row.get("date") and row.get("time"):
                    rows.append(row)
            frame = pd.DataFrame(rows)
            if frame.empty:
                return frame
            frame["timestamp"] = [
                self._parse_time(row["date"], row["time"]) for _, row in frame.iterrows()
            ]
            frame["symbol"] = symbol
            frame["source"] = self.source
            frame["source_symbol"] = code
            frame["fetched_at"] = datetime.now().astimezone().isoformat()
            return frame
        finally:
            if owns_session:
                self._logout()

    @staticmethod
    def _parse_time(day: str, time_value: str) -> pd.Timestamp:
        text = str(time_value).strip()
        if "-" in text or ":" in text:
            return pd.Timestamp(text, tz="Asia/Shanghai") if " " in text else pd.Timestamp(f"{day} {text}", tz="Asia/Shanghai")
        # BaoStock time is normally YYYYMMDDHHMMSSmmm; retain minute precision.
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) >= 14:
            return pd.Timestamp(datetime.strptime(digits[:14], "%Y%m%d%H%M%S"), tz="Asia/Shanghai")
        return pd.Timestamp(f"{day} {digits[:2]}:{digits[2:4]}:00", tz="Asia/Shanghai")

    def capability(self) -> dict[str, str]:
        return {
            "source": self.source,
            "role": "PRIMARY_HISTORICAL_5M_SOURCE",
            "adjustment": "NONE",
            "timestamp_semantics": self.timestamp_semantics.value,
        }
