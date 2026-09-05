"""V2 OrderManager — 唯一订单生命周期管理。状态转换集中管理。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from .event_log import BacktestEventLog
from .events import OrderEvent
from .order import Order, OrderStatus, TimeInForce
from .signal import Side
from .time_types import ensure_aware


class OrderManager:
    def __init__(self, event_log: Optional[BacktestEventLog] = None):
        self.orders: Dict[str, Order] = {}
        self.event_log = event_log if event_log is not None else BacktestEventLog()
        self._seq = 0
        self._id_counter = 0
        self._open_ids = set()

    def create_order(self, order: Order, ts) -> Order:
        ts = ensure_aware(ts)
        if order.order_id:
            oid = order.order_id
        else:
            self._id_counter += 1
            oid = f"ord-{self._id_counter:06d}"
            order.order_id = oid
        order.created_at = ts
        order.status = OrderStatus.CREATED
        self.orders[oid] = order
        self._open_ids.add(oid)
        self._emit(order, ts, "CREATED", "order created")
        return order

    def submit(self, order: Order, ts) -> Order:
        ts = ensure_aware(ts)
        if order.status != OrderStatus.CREATED:
            raise ValueError(f"cannot submit order in state {order.status}")
        order.status = OrderStatus.SUBMITTED
        order.submitted_at = ts
        self._emit(order, ts, "SUBMITTED", "order submitted")
        order.status = OrderStatus.ACCEPTED
        self._emit(order, ts, "ACCEPTED", "order accepted")
        return order

    def reject(self, order: Order, ts, message: str) -> Order:
        order.status = OrderStatus.REJECTED
        self._open_ids.discard(order.order_id)
        self._emit(order, ts, "REJECTED", message)
        return order

    def cancel(self, order: Order, ts, message: str = "cancelled") -> Order:
        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED):
            return order
        order.status = OrderStatus.CANCELLED
        self._open_ids.discard(order.order_id)
        self._emit(order, ts, "CANCELLED", message)
        return order

    def expire(self, order: Order, ts, message: str = "day expired") -> Order:
        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED):
            return order
        order.status = OrderStatus.EXPIRED
        self._open_ids.discard(order.order_id)
        self._emit(order, ts, "EXPIRED", message)
        return order

    def apply_fill(self, order: Order, fill_qty: int, fill_price: float, ts, fee_total: float) -> Order:
        ts = ensure_aware(ts)
        order.filled_quantity += fill_qty
        order.remaining_quantity = max(0, order.quantity - order.filled_quantity)
        order.total_fee += fee_total
        if order.filled_quantity <= 0:
            return order
        if order.filled_quantity >= order.quantity:
            order.status = OrderStatus.FILLED
            self._open_ids.discard(order.order_id)
            self._emit(order, ts, "FILLED", f"filled {fill_qty} @ {fill_price}")
        else:
            order.status = OrderStatus.PARTIALLY_FILLED
            self._emit(order, ts, "PARTIALLY_FILLED", f"filled {fill_qty} @ {fill_price}")
        return order

    def open_orders(self) -> List[Order]:
        return [self.orders[oid] for oid in sorted(self._open_ids)]

    def has_open_sell_for_position(self, position_id: str) -> bool:
        for oid in self._open_ids:
            o = self.orders[oid]
            if o.side == Side.SELL and o.position_id == position_id:
                return True
        return False

    def has_open_sell_for_lot(self, lot_id: str) -> bool:
        for oid in self._open_ids:
            o = self.orders[oid]
            if o.side == Side.SELL and o.lot_id == lot_id:
                return True
        return False

    def _emit(self, order: Order, ts, status: str, message: str):
        ev = OrderEvent(
            event_id="",
            timestamp=ts,
            event_type="ORDER_EVENT",
            strategy_id=order.strategy_id,
            symbol=order.symbol,
            signal_id=order.signal_id,
            intent_id=order.intent_id,
            order_id=order.order_id,
            position_id=order.position_id,
            lot_id=order.lot_id,
            order_status=order.status,
            message=message,
        )
        self.event_log.log(ev)
