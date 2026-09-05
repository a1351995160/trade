"""V2 AsOfDataView / MarketDataStore — 未来函数防火墙。

Strategy 只能通过 AsOfDataView 访问 available_at <= clock.now 的数据。
BrokerSimulator 可直接读 MarketDataStore 中订单提交之后的 bar 来判定成交（事后模拟）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from .time_types import ensure_aware, date_key


class LookaheadViolation(Exception):
    """策略尝试读取 clock.now 以后的数据。"""


@dataclass
class MarketDataStore:
    """内存行情仓库。日线 key 为 YYYYMMDD int；5分钟 key 为 Timestamp。

    字段: open/high/low/close/volume/amount。
    raw = 真实未复权执行价格；qfq/hfq = 研究用复权特征价格。
    """

    daily_raw: Dict[str, pd.DataFrame] = field(default_factory=dict)
    daily_qfq: Dict[str, pd.DataFrame] = field(default_factory=dict)
    daily_hfq: Dict[str, pd.DataFrame] = field(default_factory=dict)
    minute_5: Dict[str, pd.DataFrame] = field(default_factory=dict)
    feature_price_mode: str = "qfq"

    @staticmethod
    def _ensure_sorted(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if not df.index.is_monotonic_increasing:
            df = df.sort_index()
        return df

    def add_daily_raw(self, symbol: str, df: pd.DataFrame):
        df = self._ensure_sorted(df)
        if df.index.inferred_type != "integer" and not isinstance(df.index, pd.DatetimeIndex):
            df.index = [date_key(ensure_aware(i)) for i in df.index]
        self.daily_raw[symbol] = df

    def add_daily_qfq(self, symbol: str, df: pd.DataFrame):
        df = self._ensure_sorted(df)
        if df.index.inferred_type != "integer" and not isinstance(df.index, pd.DatetimeIndex):
            df.index = [date_key(ensure_aware(i)) for i in df.index]
        self.daily_qfq[symbol] = df

    def add_minute_5(self, symbol: str, df: pd.DataFrame):
        df = self._ensure_sorted(df)
        self.minute_5[symbol] = df

    def symbols(self) -> List[str]:
        return list(self.daily_raw.keys())

    def get_daily_bar(self, symbol: str, d: int, price_mode: str = "raw") -> Optional[dict]:
        table = {"raw": self.daily_raw, "qfq": self.daily_qfq, "hfq": self.daily_hfq}[price_mode]
        df = table.get(symbol)
        if df is None:
            return None
        if d not in df.index:
            return None
        row = df.loc[d]
        prev_close = float(row.get("prev_close", 0.0) or 0.0)
        if prev_close <= 0:
            pos = df.index.get_loc(d)
            if pos > 0:
                prev_close = float(df.iloc[pos - 1]["close"])
        return {
            "date": int(d),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "amount": float(row.get("amount", 0.0)),
            "prev_close": prev_close,
        }

    def get_daily_history(self, symbol: str, end_date: int, lookback: int = 250, price_mode: str = "raw") -> pd.DataFrame:
        table = {"raw": self.daily_raw, "qfq": self.daily_qfq, "hfq": self.daily_hfq}[price_mode]
        df = table.get(symbol)
        if df is None:
            return pd.DataFrame()
        sub = df[df.index <= end_date]
        return sub.tail(lookback)

    def get_minute_bar(self, symbol: str, ts) -> Optional[dict]:
        df = self.minute_5.get(symbol)
        if df is None:
            return None
        t = ensure_aware(ts)
        if t not in df.index:
            return None
        row = df.loc[t]
        return {
            "ts": t,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "amount": float(row.get("amount", 0.0)),
        }

    def get_minute_bar_at_or_before(self, symbol: str, ts) -> Optional[dict]:
        """返回截至 ts 可见的最后一根 minute bar，不读取未来收盘。"""
        df = self.minute_5.get(symbol)
        if df is None or df.empty:
            return None
        t = ensure_aware(ts)
        visible = df[df.index <= t]
        if visible.empty:
            return None
        row = visible.iloc[-1]
        index_ts = visible.index[-1]
        return {
            "ts": index_ts,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "amount": float(row.get("amount", 0.0)),
        }


class AsOfDataView:
    """Strategy 数据访问防火墙。"""

    def __init__(self, store: MarketDataStore, now: pd.Timestamp, feature_price_mode: str = "qfq"):
        self.store = store
        self.now = ensure_aware(now)
        self.feature_price_mode = feature_price_mode

    def _guard(self, ts):
        t = ensure_aware(ts)
        if t > self.now:
            raise LookaheadViolation(f"attempted to read {t} > clock.now {self.now}")

    def daily_history(self, symbol: str, end_ts, lookback: int = 250, price_mode: Optional[str] = None) -> pd.DataFrame:
        end_ts = ensure_aware(end_ts)
        self._guard(end_ts)
        mode = price_mode or self.feature_price_mode
        return self.store.get_daily_history(symbol, date_key(end_ts), lookback=lookback, price_mode=mode)

    def daily_bar(self, symbol: str, ts, price_mode: Optional[str] = None) -> Optional[dict]:
        ts = ensure_aware(ts)
        self._guard(ts)
        mode = price_mode or self.feature_price_mode
        return self.store.get_daily_bar(symbol, date_key(ts), price_mode=mode)

    def minute_history(self, symbol: str, end_ts, lookback: int = 200) -> pd.DataFrame:
        end_ts = ensure_aware(end_ts)
        self._guard(end_ts)
        df = self.store.minute_5.get(symbol)
        if df is None:
            return pd.DataFrame()
        return df[df.index <= end_ts].tail(lookback)

    def minute_bar(self, symbol: str, ts) -> Optional[dict]:
        ts = ensure_aware(ts)
        self._guard(ts)
        return self.store.get_minute_bar(symbol, ts)
