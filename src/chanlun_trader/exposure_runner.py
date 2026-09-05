"""动态仓位回测运行器。

在 CustomBacktestRunner 冻结成交循环基础上，仅增加按日 exposure（总仓位比例）控制：
_buy 的单个持仓预算 = 前一交易日收盘权益 * exposure / max_positions。
暴露 0 表示空仓；暴露 1 表示满仓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .strategy_runner import CustomBacktestRunner


class ExposureRunner(CustomBacktestRunner):
    """支持按日 exposure 的外部信号回测运行器。"""

    def __init__(self, tdx, cfg, signals, universe_codes=None):
        super().__init__(tdx, cfg, signals, universe_codes=universe_codes)
        expo = cfg.get("__exposure_by_date__", {})
        self.exposure_by_date = {int(d): float(v) for d, v in expo.items()}

    def _buy(self, cash: float, price: float) -> tuple[int, float]:
        if price <= 0 or cash <= 0:
            return 0, 0.0
        d = int(getattr(self, "_current_date", 0))
        exposure = self.exposure_by_date.get(d, 1.0)
        if exposure <= 0:
            return 0, 0.0
        budget = min(cash, self._last_equity * exposure / self.max_positions)
        shares = int(budget / price / 100) * 100
        while shares >= 100:
            cost = shares * price + max(shares * price * self.commission_rate, self.min_commission)
            if cost <= cash:
                return shares, cost
            shares -= 100
        return 0, 0.0


class PositionExposureRunner(ExposureRunner):
    """真实全引擎动态 exposure：通过目标持仓数实现 100%/50%/20% 等粗粒度仓位。

    每个持仓仍为等权满槽（equity / max_positions），exposure 只改变允许持有的数量。
    减仓在下一交易日开盘卖出（T+1、涨跌停/停牌、费用滑点均走冻结引擎）。
    """

    def _max_positions_for_date(self, date: int) -> int:
        d = int(date)
        prev = self._prev_calendar_day(d)
        expo = self.exposure_by_date.get(prev, self.exposure_by_date.get(d, 1.0))
        if expo <= 0:
            return 0
        return max(0, int(round(self.max_positions * expo)))

    def _prev_calendar_day(self, date: int) -> int:
        if not self.calendar:
            return date
        prev = date
        for d in self.calendar:
            if int(d) >= int(date):
                break
            prev = int(d)
        return prev

    def _buy(self, cash: float, price: float) -> tuple[int, float]:
        """按等权满槽计算，不受 exposure 缩放（仓位大小由持仓数控制）。"""
        if price <= 0 or cash <= 0:
            return 0, 0.0
        d = int(getattr(self, "_current_date", 0))
        if self._max_positions_for_date(d) <= 0:
            return 0, 0.0
        budget = min(cash, self._last_equity / self.max_positions)
        shares = int(budget / price / 100) * 100
        while shares >= 100:
            cost = shares * price + max(shares * price * self.commission_rate, self.min_commission)
            if cost <= cash:
                return shares, cost
            shares -= 100
        return 0, 0.0
