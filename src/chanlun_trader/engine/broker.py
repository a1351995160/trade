"""V2 BrokerSimulator — 订单接受/拒绝/等待市场/成交判定/费用/滑点/涨跌停/停牌/T+1。"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

import pandas as pd

from .asof import MarketDataStore
from .fee import FeeModel, ChinaAStockFeeModel
from .fill import Fill, FillModel, DailyBarFillModel, FiveMinuteFillModel
from .ledger import PortfolioLedger
from .order import Order, OrderStatus, TimeInForce
from .order_manager import OrderManager
from .risk import RiskManager
from .security_state import ChinaPriceLimitModel, SuspensionModel
from .signal import ExecutionPolicy, Side
from .slippage import FixedBpsSlippage, SlippageModel
from .time_types import EventKind, TradingClock, ensure_aware, date_key


class BrokerSimulator:
    def __init__(self, store: MarketDataStore, clock: TradingClock, ledger: PortfolioLedger,
                 order_manager: OrderManager,
                 fee_model: Optional[FeeModel] = None,
                 slippage_model: Optional[SlippageModel] = None,
                 fill_model: Optional[FillModel] = None,
                 price_limit_model: Optional[ChinaPriceLimitModel] = None,
                 suspension_model: Optional[SuspensionModel] = None,
                 risk_manager: Optional[RiskManager] = None,
                 lot_size: int = 100,
                 partial_fill: bool = True,
                 index_ok_fn: Optional[Callable[[int], bool]] = None):
        self.store = store
        self.clock = clock
        self.ledger = ledger
        self.orders = order_manager
        self.fee_model = fee_model or ChinaAStockFeeModel()
        self.slippage = slippage_model or FixedBpsSlippage(0.001)
        self.fill_model = fill_model or DailyBarFillModel()
        self.price_limit = price_limit_model or ChinaPriceLimitModel()
        self.suspension = suspension_model or SuspensionModel()
        self.risk = risk_manager
        self.lot_size = lot_size
        self.partial_fill = partial_fill
        self.index_ok_fn = index_ok_fn
        self._fill_counter = 0

    def _new_fill_id(self) -> str:
        self._fill_counter += 1
        return f"fill-{self._fill_counter:06d}"

    def resolve_eligible_at(self, order: Order, ts: pd.Timestamp) -> pd.Timestamp:
        ts = ensure_aware(ts)
        pol = order.execution_policy if hasattr(order, "execution_policy") else ExecutionPolicy.NEXT_SESSION_OPEN
        if pol in (ExecutionPolicy.NEXT_SESSION_OPEN, ExecutionPolicy.MARKET_ON_OPEN):
            return self.clock.next_session_open_ts(order.created_at)
        if pol == ExecutionPolicy.NEXT_BAR_OPEN:
            if self.clock.mode == "5MIN":
                return self.clock.next_bar_ts(order.symbol, order.created_at)
            return self.clock.next_session_open_ts(order.created_at)
        return self.clock.next_session_open_ts(order.created_at)

    def process_orders(self, ev_kind: EventKind, ts: pd.Timestamp):
        """在时钟事件上处理订单。仅处理 eligible_at <= ts 且 open 的订单。"""
        ts = ensure_aware(ts)
        for order in sorted(self.orders.open_orders(), key=lambda o: (o.priority, o.sequence, o.order_id)):
            if order.eligible_at is None:
                order.eligible_at = self.resolve_eligible_at(order, ts)
            if order.eligible_at > ts:
                continue
            if ev_kind in (EventKind.SESSION_OPEN, EventKind.BAR_CLOSE):
                self._try_fill_order(order, ts, ev_kind)
            elif ev_kind == EventKind.SESSION_CLOSE:
                if order.time_in_force == TimeInForce.DAY and order.is_open:
                    self.orders.expire(order, ts, "session close day expiry")

    def _bar_for_order(self, order: Order, ts: pd.Timestamp, ev_kind: EventKind) -> Optional[dict]:
        if self.clock.mode == "5MIN":
            bar = self.store.get_minute_bar(order.symbol, ts)
            if bar is not None:
                bar["date"] = date_key(ts)
                bar["volume_known_asof"] = "BAR_CLOSE"
            return bar
        d = date_key(ts)
        bar = self.store.get_daily_bar(order.symbol, d, price_mode="raw")
        if bar is not None:
            bar["current_volume"] = float(bar.get("volume", 0.0))
            if ev_kind == EventKind.SESSION_OPEN:
                # Daily NEXT_SESSION_OPEN 在 09:30 成交，当日全天 volume 尚未发生。
                # 流动性约束改用上一交易日已知 volume（Option B: previous-day liquidity）。
                prev_day = self.clock.calendar.prev_day(d)
                prev_bar = None
                while prev_day is not None:
                    candidate = self.store.get_daily_bar(order.symbol, prev_day, price_mode="raw")
                    if candidate is not None and float(candidate.get("volume", 0.0)) > 0:
                        prev_bar = candidate
                        break
                    prev_day = self.clock.calendar.prev_day(prev_day)
                if prev_bar is None:
                    bar["volume"] = 0.0
                    bar["volume_known_asof"] = "NO_PRIOR_TRADABLE_HISTORY"
                else:
                    bar["volume"] = float(prev_bar["volume"])
                    bar["volume_known_asof"] = "LAST_PRIOR_TRADABLE_CLOSE"
            else:
                bar["volume_known_asof"] = "SESSION_CLOSE"
        return bar

    def _try_fill_order(self, order: Order, ts: pd.Timestamp, ev_kind: EventKind):
        if not order.is_open:
            return
        ts = ensure_aware(ts)
        bar = self._bar_for_order(order, ts, ev_kind)
        # 1. 停牌 / 涨跌停 tradability
        tradability_bar = dict(bar) if bar is not None else None
        if tradability_bar is not None and "current_volume" in tradability_bar:
            tradability_bar["volume"] = tradability_bar["current_volume"]
        if order.side == Side.BUY:
            ok, reason = self.price_limit.can_buy_at_open(order.symbol, ts, tradability_bar)
            if not ok:
                if reason == "SUSPENDED" and order.time_in_force == TimeInForce.GTC_SIM:
                    return  # 保留
                return self._reject_or_expire(order, ts, reason)
        else:
            ok, reason = self.price_limit.can_sell_at_open(order.symbol, ts, tradability_bar)
            if not ok:
                if reason == "SUSPENDED" and order.time_in_force == TimeInForce.GTC_SIM:
                    return
                return self._reject_or_expire(order, ts, reason)
        # 2. Stale order / position binding 检查
        if order.side == Side.SELL:
            if order.position_id:
                pos = self.ledger.get_position(order.strategy_id, order.symbol)
                if pos is None or pos.position_id != order.position_id:
                    return self._reject_or_expire(order, ts, "STALE_ORDER_POSITION_CHANGED")
        # 3. T+1 卖出检查
        if order.side == Side.SELL:
            if order.lot_id and order.lot_id not in self.ledger.lots:
                return self._reject_or_expire(order, ts, "LOT_NOT_FOUND")
            if order.lot_id:
                sellable = self.ledger.sellable_lot_quantity(order.lot_id, ts)
            else:
                sellable = self.ledger.sellable_quantity(order.strategy_id, order.symbol, ts)
            if sellable <= 0:
                return self._reject_or_expire(order, ts, "T1_NOT_SELLABLE")
            if order.remaining_quantity > sellable:
                # A股 T+1：只卖可卖部分；不卖不可卖 lot
                order.quantity = sellable
                order.remaining_quantity = sellable
        # 3. Fill model
        fill_price_ref, qty, reason = self.fill_model.try_fill(order, ts, bar)
        if qty <= 0:
            return self._reject_or_expire(order, ts, reason)
        # 4. 滑点
        fill_price = self.slippage.apply("BUY" if order.side == Side.BUY else "SELL", fill_price_ref)
        fill_price = round(fill_price, 4)
        if order.side == Side.BUY:
            qty = int(qty // self.lot_size) * self.lot_size
        if qty <= 0:
            return self._reject_or_expire(order, ts, "LOT_SIZE_ROUND_ZERO")
        if order.side == Side.BUY:
            # 成交时点重新 sizing：使用成交时已知 equity 与 cash（与 V1 的 fill-time sizing 对齐）
            if self.risk is not None:
                n = max(1, self.risk.config.max_positions)
                w = min(1.0 / n, self.risk.config.max_position_weight)
            else:
                w = 0.10
            equity = self.ledger.current_equity()
            budget = min(self.ledger.available_cash(), equity * w)
            max_qty = int(budget / fill_price / self.lot_size) * self.lot_size
            qty = min(qty, max_qty)
            order.quantity = qty
            order.remaining_quantity = qty
        if qty <= 0:
            return self._reject_or_expire(order, ts, "SIZING_ZERO_AT_FILL")
        # 5. Risk pre-trade（使用最终 qty 与成交参考价）
        if self.risk is not None:
            idx_ok = True
            if self.index_ok_fn is not None:
                idx_ok = self.index_ok_fn(date_key(ts))
            rr = self.risk.pre_trade(order, ts, fill_price, index_ok=idx_ok)
            if not rr.ok:
                return self._reject_or_expire(order, ts, rr.reason)
        # 6. Fee
        fee = self.fee_model.calc("BUY" if order.side == Side.BUY else "SELL", qty, fill_price)
        fill = Fill(
            fill_id=self._new_fill_id(),
            order_id=order.order_id,
            strategy_id=order.strategy_id,
            symbol=order.symbol,
            side=order.side,
            quantity=qty,
            price=fill_price,
            fill_time=ts,
            commission=fee.commission,
            stamp_tax=fee.stamp_tax,
            other_fee=fee.other_fee,
            total_fee=fee.total_fee,
            bar_kind=self.clock.mode,
        )
        # 7. Ledger
        rec, msg = self.ledger.apply_fill(fill, order_id=order.order_id, lot_id=order.lot_id)
        if rec is None:
            return self._reject_or_expire(order, ts, msg)
        if msg == "CORPORATE_ACTION_UNSUPPORTED":
            self.orders.reject(order, ts, msg)
            return
        # 8. Order manager update
        self.orders.apply_fill(order, qty, fill_price, ts, fee.total_fee)

    def _reject_or_expire(self, order: Order, ts: pd.Timestamp, reason: str):
        order.reason_code = str(reason)
        order.metadata["broker_rejection_reason"] = str(reason)
        if order.time_in_force == TimeInForce.GTC_SIM and reason == "SUSPENDED":
            return
        self.orders.reject(order, ts, reason)
