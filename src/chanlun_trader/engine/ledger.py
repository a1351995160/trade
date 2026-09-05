"""V2 PortfolioLedger — SINGLE SOURCE OF TRUTH.

所有 Fill 必须经过 Ledger.apply_fill()。禁止其他模块直接 cash -= / cash +=。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .fill import Fill
from .position import Position, PositionLot
from .signal import Side
from .time_types import ensure_aware


@dataclass
class TradeRecord:
    trade_id: str
    strategy_id: str
    symbol: str
    side: Side
    quantity: int
    price: float
    gross_value: float
    fee: float
    fill_time: pd.Timestamp
    position_id: str
    lot_id: Optional[str] = None
    realized_pnl: float = 0.0
    order_id: str = ""
    reality_flag: str = "OK"

    def __post_init__(self):
        self.fill_time = ensure_aware(self.fill_time)
        if not isinstance(self.side, Side):
            self.side = Side(self.side)


@dataclass
class LedgerSnapshot:
    timestamp: pd.Timestamp
    cash: float
    market_value: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    positions: int
    turnover: float

    def __post_init__(self):
        self.timestamp = ensure_aware(self.timestamp)


class PortfolioLedger:
    def __init__(self, initial_cash: float = 1_000_000.0):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.reserved_cash = 0.0
        self.positions: Dict[str, Position] = {}
        self.lots: Dict[str, PositionLot] = {}
        self.trades: List[TradeRecord] = []
        self.snapshots: List[LedgerSnapshot] = []
        self.last_price: Dict[str, float] = {}
        self.total_fees: float = 0.0
        self.realized_pnl: float = 0.0
        self.turnover: float = 0.0
        self.corporate_action_guard = None
        self.fill_audit: List[dict] = []
        self.unsupported_trade_count: int = 0
        self._pos_counter = 0
        self._lot_counter = 0
        self._trade_counter = 0

    def _new_position_id(self) -> str:
        self._pos_counter += 1
        return f"pos-{self._pos_counter:06d}"

    def _new_lot_id(self) -> str:
        self._lot_counter += 1
        return f"lot-{self._lot_counter:06d}"

    def _position_key(self, strategy_id: str, symbol: str) -> str:
        return f"{strategy_id}:{symbol}"

    def get_position(self, strategy_id: str, symbol: str) -> Optional[Position]:
        return self.positions.get(self._position_key(strategy_id, symbol))

    def position_qty(self, strategy_id: str, symbol: str) -> int:
        p = self.get_position(strategy_id, symbol)
        return p.quantity if p else 0

    def sellable_quantity(self, strategy_id: str, symbol: str, ts: pd.Timestamp) -> int:
        ts = ensure_aware(ts)
        p = self.get_position(strategy_id, symbol)
        if not p:
            return 0
        qty = 0
        for lot in self.lots.values():
            if lot.position_id == p.position_id and lot.remaining_quantity > 0 and lot.sellable_from <= ts:
                qty += lot.remaining_quantity
        return qty

    def sellable_lot_quantity(self, lot_id: str, ts: pd.Timestamp) -> int:
        """Return the currently sellable quantity for one FIFO lot."""
        ts = ensure_aware(ts)
        lot = self.lots.get(str(lot_id))
        if lot is None or lot.remaining_quantity <= 0 or lot.sellable_from > ts:
            return 0
        return int(lot.remaining_quantity)

    def total_quantity(self, symbol: str) -> int:
        return sum(p.quantity for p in self.positions.values() if p.symbol == symbol)

    def available_cash(self) -> float:
        return max(0.0, self.cash - self.reserved_cash)

    def current_equity(self) -> float:
        mv = 0.0
        for pos in self.positions.values():
            if pos.quantity > 0:
                mv += self.last_price.get(pos.symbol, pos.average_cost) * pos.quantity
        return self.cash + mv

    def apply_fill(self, fill: Fill, order_id: str = "", lot_id: Optional[str] = None) -> Tuple[Optional[TradeRecord], str]:
        """应用一笔成交。返回 (TradeRecord or None, message)。"""
        if fill.quantity <= 0:
            return None, "ZERO_QUANTITY"
        if fill.side == Side.BUY:
            return self._apply_buy(fill, order_id)
        return self._apply_sell(fill, order_id, lot_id=lot_id)

    def _apply_buy(self, fill: Fill, order_id: str) -> Tuple[Optional[TradeRecord], str]:
        gross = fill.gross_value
        total = gross + fill.total_fee
        if total > self.cash + 1e-8:
            return None, "INSUFFICIENT_CASH"
        pos_key = self._position_key(fill.strategy_id, fill.symbol)
        pos = self.positions.get(pos_key)
        if pos is None or pos.quantity <= 0:
            # 完全平仓后再次买入 -> 新 Position 对象；旧卖单按 position_id 绑定不再生效
            pos = Position(
                position_id=self._new_position_id(),
                symbol=fill.symbol,
                strategy_id=fill.strategy_id,
                opened_at=fill.fill_time,
            )
            self.positions[pos_key] = pos
        sellable_from = _next_session_open_from(fill.fill_time)
        lot = PositionLot(
            lot_id=self._new_lot_id(),
            position_id=pos.position_id,
            symbol=fill.symbol,
            strategy_id=fill.strategy_id,
            buy_time=fill.fill_time,
            quantity=fill.quantity,
            remaining_quantity=fill.quantity,
            cost=gross + fill.total_fee,
            sellable_from=sellable_from,
            entry_session=int(fill.fill_time.strftime("%Y%m%d")),
            entry_price=float(fill.price),
        )
        self.lots[lot.lot_id] = lot
        self.cash -= total
        self.total_fees += fill.total_fee
        self.turnover += gross
        prev_cost = pos.average_cost * pos.quantity
        pos.quantity += fill.quantity
        pos.average_cost = (prev_cost + gross + fill.total_fee) / pos.quantity if pos.quantity else 0.0
        pos.updated_at = fill.fill_time
        rec = TradeRecord(
            trade_id=f"tr-{self._trade_counter + 1:06d}",
            strategy_id=fill.strategy_id,
            symbol=fill.symbol,
            side=Side.BUY,
            quantity=fill.quantity,
            price=fill.price,
            gross_value=gross,
            fee=fill.total_fee,
            fill_time=fill.fill_time,
            position_id=pos.position_id,
            lot_id=lot.lot_id,
            realized_pnl=0.0,
            order_id=order_id,
        )
        self._trade_counter += 1
        self.trades.append(rec)
        self.last_price[fill.symbol] = fill.price
        return rec, "OK"

    def _apply_sell(self, fill: Fill, order_id: str, lot_id: Optional[str] = None) -> Tuple[Optional[TradeRecord], str]:
        pos = self.get_position(fill.strategy_id, fill.symbol)
        if pos is None or pos.quantity <= 0:
            return None, "NO_POSITION"
        ts = fill.fill_time
        if lot_id is not None:
            target = self.lots.get(str(lot_id))
            if target is None or target.position_id != pos.position_id:
                return None, "LOT_NOT_FOUND"
            if target.remaining_quantity <= 0:
                return None, "LOT_ALREADY_CLOSED"
            if target.sellable_from > ts:
                return None, "T1_NOT_SELLABLE"
            sellable = [target]
        else:
            sellable = [
                l for l in self.lots.values()
                if l.position_id == pos.position_id and l.remaining_quantity > 0 and l.sellable_from <= ts
            ]
            sellable.sort(key=lambda l: l.buy_time)
        sellable_qty = sum(l.remaining_quantity for l in sellable)
        if sellable_qty <= 0:
            return None, "T1_NOT_SELLABLE"
        if fill.quantity > sellable_qty:
            return None, "LOT_QUANTITY_EXCEEDED" if lot_id is not None else "T1_PARTIAL_NOT_SELLABLE"

        # Corporate-action crossing is fail-closed.  Keep an execution audit record,
        # but do not mutate cash, lots, position quantity, realized PnL, or equity.
        if self.corporate_action_guard is not None:
            remaining_probe = fill.quantity
            probe_buy_dates = []
            probe_lot_id = None
            for lot in sellable:
                if remaining_probe <= 0:
                    break
                q = min(lot.remaining_quantity, remaining_probe)
                probe_buy_dates.append(lot.buy_time)
                probe_lot_id = probe_lot_id or lot.lot_id
                remaining_probe -= q
            buy_d = min(int(t.strftime("%Y%m%d")) for t in probe_buy_dates)
            sell_d = int(fill.fill_time.strftime("%Y%m%d"))
            if self.corporate_action_guard.has_event_between(fill.symbol, buy_d, sell_d):
                rec = TradeRecord(
                    trade_id=f"tr-{self._trade_counter + 1:06d}",
                    strategy_id=fill.strategy_id,
                    symbol=fill.symbol,
                    side=Side.SELL,
                    quantity=fill.quantity,
                    price=fill.price,
                    gross_value=fill.gross_value,
                    fee=fill.total_fee,
                    fill_time=fill.fill_time,
                    position_id=pos.position_id,
                    lot_id=probe_lot_id,
                    order_id=order_id,
                    reality_flag="TRADE_REALITY_UNSUPPORTED",
                )
                self._trade_counter += 1
                self.trades.append(rec)
                self.unsupported_trade_count += 1
                self.fill_audit.append({
                    "fill_id": fill.fill_id,
                    "symbol": fill.symbol,
                    "side": "SELL",
                    "fill_time": str(fill.fill_time),
                    "status": "CORPORATE_ACTION_UNSUPPORTED",
                    "economic_effect_applied": False,
                })
                return rec, "CORPORATE_ACTION_UNSUPPORTED"
        self.fill_audit.append({
            "fill_id": fill.fill_id,
            "symbol": fill.symbol,
            "side": "SELL",
            "fill_time": str(fill.fill_time),
            "sellable_before": int(sellable_qty),
            "fill_quantity": int(fill.quantity),
            "fill_price": float(fill.price),
        })
        proceeds = fill.gross_value - fill.total_fee
        remaining = fill.quantity
        realized = 0.0
        sold_lot_buy_dates = []
        for lot in sellable:
            if remaining <= 0:
                break
            q = min(lot.remaining_quantity, remaining)
            avg_cost_per_share = lot.cost / lot.quantity if lot.quantity else 0.0
            realized += (fill.price - avg_cost_per_share) * q - fill.total_fee * (q / fill.quantity)
            sold_lot_buy_dates.append(lot.buy_time)
            lot.remaining_quantity -= q
            if lot.remaining_quantity <= 0:
                lot.exit_state = "CLOSED"
            elif lot.exit_state in {"EXIT_DUE", "SELL_PENDING", "PARTIALLY_FILLED"}:
                lot.exit_state = "PARTIALLY_FILLED"
            remaining -= q
        self.cash += proceeds
        self.total_fees += fill.total_fee
        self.turnover += fill.gross_value
        self.realized_pnl += realized
        pos.quantity -= fill.quantity
        pos.realized_pnl += realized
        pos.updated_at = ts
        if pos.quantity <= 0:
            pos.quantity = 0
            pos.unrealized_pnl = 0.0
        rec = TradeRecord(
            trade_id=f"tr-{self._trade_counter + 1:06d}",
            strategy_id=fill.strategy_id,
            symbol=fill.symbol,
            side=Side.SELL,
            quantity=fill.quantity,
            price=fill.price,
            gross_value=fill.gross_value,
            fee=fill.total_fee,
            fill_time=fill.fill_time,
            position_id=pos.position_id,
            lot_id=str(lot_id) if lot_id is not None else (sellable[0].lot_id if len(sellable) == 1 else None),
            realized_pnl=realized,
            order_id=order_id,
        )
        if self.corporate_action_guard is not None and sold_lot_buy_dates:
            buy_d = min(int(t.strftime("%Y%m%d")) for t in sold_lot_buy_dates)
            sell_d = int(fill.fill_time.strftime("%Y%m%d"))
            if self.corporate_action_guard.has_event_between(fill.symbol, buy_d, sell_d):
                rec.reality_flag = "TRADE_REALITY_UNSUPPORTED"
        self._trade_counter += 1
        self.trades.append(rec)
        self.last_price[fill.symbol] = fill.price
        return rec, "OK"

    def mark_to_market(self, symbol: str, price: float, ts: pd.Timestamp):
        ts = ensure_aware(ts)
        self.last_price[symbol] = price
        for pos in self.positions.values():
            if pos.symbol == symbol and pos.quantity > 0:
                pos.unrealized_pnl = (price - pos.average_cost) * pos.quantity

    def snapshot(self, ts: pd.Timestamp) -> LedgerSnapshot:
        ts = ensure_aware(ts)
        mv = 0.0
        ur = 0.0
        for pos in self.positions.values():
            if pos.quantity > 0:
                px = self.last_price.get(pos.symbol, pos.average_cost)
                mv += px * pos.quantity
                ur += (px - pos.average_cost) * pos.quantity
        snap = LedgerSnapshot(
            timestamp=ts,
            cash=round(self.cash, 4),
            market_value=round(mv, 4),
            equity=round(self.cash + mv, 4),
            realized_pnl=round(self.realized_pnl, 4),
            unrealized_pnl=round(ur, 4),
            positions=sum(1 for p in self.positions.values() if p.quantity > 0),
            turnover=round(self.turnover, 4),
        )
        self.snapshots.append(snap)
        return snap

    @property
    def valid_trades(self):
        return [t for t in self.trades if t.reality_flag == "OK"]

    @property
    def certification_status(self) -> str:
        return "INVALID" if self.unsupported_trade_count else "VALID"

    def check_invariants(self) -> List[str]:
        errors = []
        if self.cash < -1e-6:
            errors.append(f"cash<0: {self.cash}")
        for p in self.positions.values():
            if p.quantity < 0:
                errors.append(f"negative position {p.symbol} {p.quantity}")
            lot_qty = sum(l.remaining_quantity for l in self.lots.values() if l.position_id == p.position_id)
            if lot_qty != p.quantity:
                errors.append(f"lot mismatch {p.symbol}: lots={lot_qty} pos={p.quantity}")
        return errors


def _next_session_open_from(ts: pd.Timestamp) -> pd.Timestamp:
    """A股 T+1 近似：下一自然日 09:30。Broker 会用交易日历覆盖为下一交易日。"""
    t = ensure_aware(ts)
    d = t.normalize() + pd.Timedelta(days=1)
    return ensure_aware(pd.Timestamp(d.year, d.month, d.day, 9, 30))
