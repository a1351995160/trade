"""V2 Fill / FillModel — 成交模型与成交记录。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

import pandas as pd

from .order import Order
from .signal import Side
from .time_types import ensure_aware


@dataclass
class Fill:
    fill_id: str
    order_id: str
    strategy_id: str
    symbol: str
    side: Side
    quantity: int
    price: float
    fill_time: pd.Timestamp
    commission: float = 0.0
    stamp_tax: float = 0.0
    other_fee: float = 0.0
    total_fee: float = 0.0
    bar_kind: str = "DAILY"

    def __post_init__(self):
        self.fill_time = ensure_aware(self.fill_time)
        if not isinstance(self.side, Side):
            self.side = Side(self.side)

    @property
    def gross_value(self) -> float:
        return self.quantity * self.price


class FillModel:
    """成交模型基类。返回 (fill_price, fill_quantity, reason) 或 (None, 0, reason)。"""

    def try_fill(self, order: Order, ts: pd.Timestamp, bar: dict) -> tuple:
        raise NotImplementedError


class DailyBarFillModel(FillModel):
    """日线填充：参考 bar open 作为成交价。可配置 max_participation_rate 做粗略容量压力。

    保守模型：不假装知道盘口；只做 open 参考价 + 滑点 + 涨跌停/停牌过滤。

    容量合同：``int(volume * max_participation_rate) == 0`` 时**拒绝成交**
    （``PARTICIPATION_LIMIT``），不退回全量成交。原实现把 0 视为"无限制"，
    在极低流动性 bar 上会静默放大到订单全量，与容量约束自相矛盾。
    """

    def __init__(self, max_participation_rate: float = 0.10):
        self.max_participation_rate = max_participation_rate

    def try_fill(self, order: Order, ts: pd.Timestamp, bar: dict) -> tuple:
        if order.quantity <= 0:
            return None, 0, "ZERO_QUANTITY"
        open_px = float(bar.get("open", 0.0))
        volume = float(bar.get("volume", 0.0))
        if open_px <= 0 or volume <= 0:
            return None, 0, "NO_TRADABLE_BAR"
        max_qty_by_volume = int(volume * self.max_participation_rate)
        if max_qty_by_volume <= 0:
            return None, 0, "PARTICIPATION_LIMIT"
        qty = min(order.remaining_quantity, max_qty_by_volume)
        if qty <= 0:
            return None, 0, "PARTICIPATION_LIMIT"
        return open_px, qty, "OK"


class EventOpenFillModel(FillModel):
    """事件 T+1 开盘成交模型：保守、可配置 volume participation。

    - 使用 broker 传入的 bar volume（DAILY mode SESSION_OPEN 时为上一交易日 volume）
    - 成交量不得超过 participation_rate * bar volume
    - 涨跌停/停牌过滤由 broker 的 ChinaPriceLimitModel 在 fill 前完成
    """

    def __init__(self, participation_rate: float = 0.05):
        self.participation_rate = participation_rate

    def try_fill(self, order: Order, ts: pd.Timestamp, bar: dict) -> tuple:
        if order.quantity <= 0:
            return None, 0, "ZERO_QUANTITY"
        open_px = float(bar.get("open", 0.0))
        volume = float(bar.get("volume", 0.0))
        if open_px <= 0 or volume <= 0:
            return None, 0, "NO_TRADABLE_BAR"
        max_qty = int(volume * self.participation_rate)
        if max_qty <= 0:
            return None, 0, "PARTICIPATION_LIMIT"
        qty = min(order.remaining_quantity, max_qty)
        if qty <= 0:
            return None, 0, "PARTICIPATION_LIMIT"
        return open_px, qty, "OK"


class FiveMinuteFillModel(FillModel):
    """5分钟填充：参考 bar open；使用 bar volume 做 participation。"""

    def __init__(self, max_participation_rate: float = 0.05):
        self.max_participation_rate = max_participation_rate

    def try_fill(self, order: Order, ts: pd.Timestamp, bar: dict) -> tuple:
        if order.quantity <= 0:
            return None, 0, "ZERO_QUANTITY"
        open_px = float(bar.get("open", 0.0))
        volume = float(bar.get("volume", 0.0))
        if open_px <= 0 or volume <= 0:
            return None, 0, "NO_TRADABLE_BAR"
        max_qty_by_volume = int(volume * self.max_participation_rate)
        if max_qty_by_volume < 100:
            return None, 0, "PARTICIPATION_LIMIT"
        qty = min(order.remaining_quantity, max_qty_by_volume)
        return open_px, qty, "OK"
