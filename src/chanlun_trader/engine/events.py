"""V2 EventLog 事件类型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import pandas as pd

from .order import OrderStatus
from .time_types import ensure_aware


@dataclass
class BacktestEvent:
    event_id: str
    timestamp: pd.Timestamp
    event_type: str
    strategy_id: Optional[str] = None
    symbol: Optional[str] = None
    signal_id: Optional[str] = None
    intent_id: Optional[str] = None
    order_id: Optional[str] = None
    position_id: Optional[str] = None
    lot_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.timestamp = ensure_aware(self.timestamp)


@dataclass
class OrderEvent(BacktestEvent):
    event_type: str = "ORDER_EVENT"
    order_status: Optional[OrderStatus] = None
    message: str = ""
