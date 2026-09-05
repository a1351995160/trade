"""V2 FeeModel — 唯一费用计算源。禁止 Trade/Runner/Metrics 重新计算费用。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FeeBreakdown:
    commission: float = 0.0
    stamp_tax: float = 0.0
    other_fee: float = 0.0
    total_fee: float = 0.0


class FeeModel:
    def calc(self, side: str, quantity: int, price: float) -> FeeBreakdown:
        raise NotImplementedError


@dataclass
class ChinaAStockFeeModel(FeeModel):
    """A股费用：佣金双边 + 最低5元 + 卖出印花税。

    旧 config: commission_rate=0.00025, min_commission=5, stamp_tax_rate=0.0005
    """

    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005

    def calc(self, side: str, quantity: int, price: float) -> FeeBreakdown:
        value = quantity * price
        commission = max(value * self.commission_rate, self.min_commission)
        stamp = value * self.stamp_tax_rate if side in ("SELL", "sell", 1) else 0.0
        other = 0.0
        return FeeBreakdown(
            commission=round(commission, 4),
            stamp_tax=round(stamp, 4),
            other_fee=other,
            total_fee=round(commission + stamp + other, 4),
        )
