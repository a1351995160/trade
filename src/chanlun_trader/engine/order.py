"""V2 Order / OrderStatus / TimeInForce — 真实提交状态。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd

from .signal import Side
from .time_types import ensure_aware


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class TimeInForce(str, Enum):
    DAY = "DAY"          # 当日有效，收盘未成交自动 EXPIRED
    GTC_SIM = "GTC_SIM"  # 回测中允许跨日，直到成交/取消/过期条件


@dataclass
class Order:
    order_id: str
    strategy_id: str
    intent_id: str
    signal_id: str
    symbol: str
    side: Side
    quantity: int
    created_at: pd.Timestamp
    submitted_at: Optional[pd.Timestamp] = None
    eligible_at: Optional[pd.Timestamp] = None  # 最早可成交时间
    status: OrderStatus = OrderStatus.CREATED
    time_in_force: TimeInForce = TimeInForce.DAY
    filled_quantity: int = 0
    remaining_quantity: int = 0
    avg_fill_price: float = 0.0
    total_fee: float = 0.0
    reason: str = ""
    reason_code: str = "OTHER"
    metadata: dict = field(default_factory=dict)
    position_id: Optional[str] = None  # 卖单必须绑定 position_id / lot_id
    lot_id: Optional[str] = None
    priority: int = 0
    sequence: int = 0

    def __post_init__(self):
        self.created_at = ensure_aware(self.created_at)
        if self.submitted_at is not None:
            self.submitted_at = ensure_aware(self.submitted_at)
        if self.eligible_at is not None:
            self.eligible_at = ensure_aware(self.eligible_at)
        if not isinstance(self.side, Side):
            self.side = Side(self.side)
        if self.status is None:
            self.status = OrderStatus.CREATED
        if not isinstance(self.status, OrderStatus):
            self.status = OrderStatus(self.status)
        if not isinstance(self.time_in_force, TimeInForce):
            self.time_in_force = TimeInForce(self.time_in_force)
        if self.remaining_quantity <= 0 and self.status not in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED, OrderStatus.CREATED):
            self.remaining_quantity = self.quantity - self.filled_quantity
        if self.remaining_quantity <= 0:
            self.remaining_quantity = self.quantity - self.filled_quantity

    @property
    def is_open(self) -> bool:
        return self.status in (OrderStatus.CREATED, OrderStatus.SUBMITTED, OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)

    @property
    def is_done(self) -> bool:
        return not self.is_open
