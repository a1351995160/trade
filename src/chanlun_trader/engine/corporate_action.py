"""V2 CorporateActionGuard — 数据/实现不足时的安全闸。

当前 CorporateActionProcessor 未实现。Guard 只负责标记跨越公司行为事件的交易为
TRADE_REALITY_UNSUPPORTED，禁止“正常算 PnL + 最后备注”的静默做法。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set

import pandas as pd

from .time_types import date_key, ensure_aware


class CorporateActionGuard:
    """按 symbol 维护除权除息事件日期集合（YYYYMMDD）。

    事件来自 gbbq category==1（现金分红/送转/配股等）。拆股等其它 category 可后续扩展。
    """

    def __init__(self):
        self.events: Dict[str, Set[int]] = {}

    def add_event(self, symbol: str, d: int):
        self.events.setdefault(symbol, set()).add(int(d))

    def add_events(self, symbol: str, days: Iterable[int]):
        for d in days:
            self.add_event(symbol, d)

    @classmethod
    def from_gbbq(cls, gbbq_df: pd.DataFrame) -> "CorporateActionGuard":
        guard = cls()
        if gbbq_df is None or gbbq_df.empty:
            return guard
        cat = gbbq_df[gbbq_df["category"] == 1]
        for _, row in cat.iterrows():
            code = str(row["code"]).zfill(6)
            market = int(row["market"])
            suffix = ".SH" if market == 1 else ".SZ"
            guard.add_event(f"{code}{suffix}", int(row["datetime"]))
        return guard

    @classmethod
    def from_tdx(cls, tdx) -> "CorporateActionGuard":
        try:
            g = tdx._load_gbbq()
            return cls.from_gbbq(g)
        except Exception:
            return cls()

    def has_event_between(self, symbol: str, start_date: int, end_date: int) -> bool:
        """是否存在 ex-date 满足 start_date < d <= end_date。"""
        evs = self.events.get(symbol)
        if not evs:
            return False
        return any(int(start_date) < d <= int(end_date) for d in evs)

    def crossing_dates(self, symbol: str, start_date: int, end_date: int) -> List[int]:
        evs = self.events.get(symbol)
        if not evs:
            return []
        return sorted(d for d in evs if int(start_date) < d <= int(end_date))

    def is_empty(self) -> bool:
        return not self.events
