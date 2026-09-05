"""V2 RiskManager — Pre-Trade Validation。不产生 Alpha，只限制风险。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from .ledger import PortfolioLedger
from .order import Order
from .signal import Side
from .universe import UniverseService


@dataclass
class RiskConfig:
    max_portfolio_exposure: float = 1.0
    max_position_weight: float = 0.10
    max_positions: int = 10
    max_sector_exposure: float = 0.30
    max_order_value: float = 0.0  # <=0 表示不限制
    max_participation_rate: float = 0.10
    require_universe: bool = True
    allow_short: bool = False


@dataclass
class RiskResult:
    ok: bool
    reason: str = "OK"


class RiskManager:
    def __init__(self, ledger: PortfolioLedger, config: RiskConfig,
                 universe: Optional[UniverseService] = None,
                 sector_of: Optional[Dict[str, str]] = None):
        self.ledger = ledger
        self.config = config
        self.universe = universe
        self.sector_of = sector_of or {}

    def pre_trade(self, order: Order, ts, estimated_price: float, index_ok: bool = True) -> RiskResult:
        if order.quantity <= 0:
            return RiskResult(False, "INVALID_QUANTITY")
        if order.side == Side.SELL:
            sellable = self.ledger.sellable_quantity(order.strategy_id, order.symbol, ts)
            if order.quantity > sellable:
                return RiskResult(False, f"T1_SELLABLE_QTY={sellable}")
            return RiskResult(True)
        # BUY
        if self.universe is not None and self.config.require_universe:
            if not self.universe.is_eligible(order.symbol, ts):
                return RiskResult(False, "NOT_IN_UNIVERSE")
        if not index_ok:
            return RiskResult(False, "INDEX_FILTER_BLOCKED")
        value = order.quantity * estimated_price
        cash = self.ledger.available_cash()
        if value > cash:
            return RiskResult(False, "INSUFFICIENT_CASH")
        if self.config.max_order_value > 0 and value > self.config.max_order_value:
            return RiskResult(False, "MAX_ORDER_VALUE")
        equity = self.ledger.snapshots[-1].equity if self.ledger.snapshots else self.ledger.initial_cash
        if equity > 0 and value / equity > self.config.max_position_weight + 1e-9:
            return RiskResult(False, "MAX_POSITION_WEIGHT")
        n_pos = sum(1 for p in self.ledger.positions.values() if p.quantity > 0)
        if n_pos >= self.config.max_positions:
            return RiskResult(False, "MAX_POSITIONS")
        sector = self.sector_of.get(order.symbol)
        if sector:
            sec_val = sum(p.quantity * self.ledger.last_price.get(p.symbol, p.average_cost)
                          for p in self.ledger.positions.values()
                          if p.quantity > 0 and self.sector_of.get(p.symbol) == sector)
            if equity > 0 and (sec_val + value) / equity > self.config.max_sector_exposure + 1e-9:
                return RiskResult(False, "MAX_SECTOR_EXPOSURE")
        return RiskResult(True)
