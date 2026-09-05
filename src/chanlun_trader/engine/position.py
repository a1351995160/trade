"""V2 Position / PositionLot — A股 T+1 可卖数量显式实现。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .time_types import ensure_aware


@dataclass
class Position:
    position_id: str
    symbol: str
    strategy_id: str
    quantity: int = 0
    average_cost: float = 0.0
    opened_at: Optional[pd.Timestamp] = None
    updated_at: Optional[pd.Timestamp] = None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0

    def __post_init__(self):
        if self.opened_at is not None:
            self.opened_at = ensure_aware(self.opened_at)
        if self.updated_at is not None:
            self.updated_at = ensure_aware(self.updated_at)


@dataclass
class PositionLot:
    """A股持仓 Lot。T日买入 -> sellable_from = T+1 开盘。"""
    lot_id: str
    position_id: str
    symbol: str
    strategy_id: str
    buy_time: pd.Timestamp
    quantity: int
    remaining_quantity: int
    cost: float  # 该 lot 买入总成本（含费用）
    sellable_from: pd.Timestamp
    entry_session: Optional[int] = None
    entry_session_index: Optional[int] = None
    sellable_from_session: Optional[int] = None
    sellable_from_session_index: Optional[int] = None
    entry_price: float = 0.0
    exit_due_session: Optional[int] = None
    exit_due_index: Optional[int] = None
    exit_state: str = "OPEN"
    exit_reason: str = ""

    def __post_init__(self):
        self.buy_time = ensure_aware(self.buy_time)
        self.sellable_from = ensure_aware(self.sellable_from)

    @property
    def is_sellable(self) -> bool:
        return self.remaining_quantity > 0
