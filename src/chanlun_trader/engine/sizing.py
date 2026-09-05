"""V2 PositionSizer — 把 PortfolioTarget / OrderIntent 转成目标数量。

旧 max_positions 语义映射为 FixedSlotSizer。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .ledger import PortfolioLedger
from .signal import TargetType


class LotSizeModel:
    """A股买卖单位。第一版 BUY 按100股整数倍，SELL 允许按 lot 剩余数量处理。"""

    def __init__(self, buy_unit: int = 100, sell_unit: int = 1):
        self.buy_unit = buy_unit
        self.sell_unit = sell_unit

    def round_buy(self, qty: int) -> int:
        return max(0, int(qty // self.buy_unit) * self.buy_unit)

    def round_sell(self, qty: int) -> int:
        return max(0, int(qty // self.sell_unit) * self.sell_unit)


class PositionSizer:
    def size_buy(self, target: "PortfolioTarget", ledger: PortfolioLedger, price: float,
                 lot_model: LotSizeModel, max_positions: int = 10) -> int:
        raise NotImplementedError


class EqualWeightSizer(PositionSizer):
    """按组合目标权重下单。"""

    def __init__(self, max_positions: int = 10):
        self.max_positions = max_positions

    def size_buy(self, target, ledger, price, lot_model, max_positions=None) -> int:
        n = max_positions or self.max_positions
        equity = ledger.current_equity()
        cash = ledger.available_cash()
        if target.target_type == TargetType.WEIGHT:
            budget = min(cash, equity * target.target_value)
        elif target.target_type == TargetType.VALUE:
            budget = min(cash, target.target_value)
        elif target.target_type == TargetType.QUANTITY:
            budget = target.target_value * price
        else:
            budget = min(cash, equity / max(1, n))
        qty = int(budget / price)
        return lot_model.round_buy(qty)


class FixedSlotSizer(PositionSizer):
    """旧引擎语义：每只股票预算 = equity / max_positions。"""

    def __init__(self, max_positions: int = 10):
        self.max_positions = max_positions

    def size_buy(self, target, ledger, price, lot_model, max_positions=None) -> int:
        n = max_positions or self.max_positions
        equity = ledger.current_equity()
        cash = ledger.available_cash()
        budget = min(cash, equity / max(1, n))
        if target.target_type == TargetType.VALUE:
            budget = min(budget, target.target_value)
        if target.target_type == TargetType.QUANTITY:
            budget = min(budget, target.target_value * price)
        qty = int(budget / price)
        return lot_model.round_buy(qty)
