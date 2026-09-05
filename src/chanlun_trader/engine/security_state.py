"""V2 SecurityState / PriceLimitModel / SuspensionModel — A股 Reality。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import pandas as pd

from .asof import MarketDataStore
from .time_types import ensure_aware, date_key


class PriceLimitState(str, Enum):
    NORMAL = "NORMAL"
    LIMIT_UP_LOCKED = "LIMIT_UP_LOCKED"
    LIMIT_DOWN_LOCKED = "LIMIT_DOWN_LOCKED"
    LIMIT_UP_OPENED = "LIMIT_UP_OPENED"
    LIMIT_DOWN_OPENED = "LIMIT_DOWN_OPENED"
    SUSPENDED = "SUSPENDED"
    CONSERVATIVE_DAILY_MODEL = "CONSERVATIVE_DAILY_MODEL"


@dataclass
class SecurityState:
    symbol: str
    asof_date: int
    listed: bool = True
    delisted: bool = False
    is_st: bool = False
    board: str = "MAIN"
    suspended: bool = False

    @staticmethod
    def infer_board(symbol: str) -> str:
        code = symbol.split(".")[0]
        if code.startswith(("300", "301")):
            return "CHINEXT"
        if code.startswith(("688", "689")):
            return "STAR"
        if symbol.endswith(".BJ") or code.startswith(("43", "83", "87", "92")):
            return "BSE"
        return "MAIN"


class SecurityMaster:
    """Security lifecycle as_of。支持逐日状态更新，未提供时按代码前缀推断。"""

    def __init__(self):
        self._states: Dict[str, List[SecurityState]] = {}

    def add_state(self, state: SecurityState):
        self._states.setdefault(state.symbol, []).append(state)
        self._states[state.symbol].sort(key=lambda s: s.asof_date)

    def as_of(self, symbol: str, ts) -> SecurityState:
        d = date_key(ensure_aware(ts))
        states = self._states.get(symbol, [])
        cur = None
        for s in states:
            if s.asof_date <= d:
                cur = s
            else:
                break
        if cur is None:
            return SecurityState(symbol=symbol, asof_date=d, board=SecurityState.infer_board(symbol))
        return cur


class ChinaPriceLimitModel:
    """PIT 涨跌停制度。当前 PIT ST 状态来自 SecurityMaster；没有数据时按代码前缀近似。

    明确标记 CONSERVATIVE_DAILY_MODEL：日线无法精确定义盘口时保守拒绝。
    """

    def __init__(self, security_master: Optional[SecurityMaster] = None):
        self.master = security_master or SecurityMaster()

    def limit_pct(self, symbol: str, ts) -> float:
        st = self.master.as_of(symbol, ts)
        if st.is_st:
            return 0.05
        if st.board == "CHINEXT" or st.board == "STAR":
            return 0.20
        if st.board == "BSE":
            return 0.30
        return 0.10

    def limit_prices(self, symbol: str, ts, prev_close: float) -> tuple:
        pct = self.limit_pct(symbol, ts)
        up = round(prev_close * (1 + pct), 2)
        down = round(prev_close * (1 - pct), 2)
        return up, down

    def classify_daily(self, symbol: str, ts, bar: Optional[dict]) -> PriceLimitState:
        """日线保守分类。bar 为 raw 日线 bar (date=当日)。"""
        d = date_key(ensure_aware(ts))
        if bar is None or float(bar.get("volume", 0.0)) <= 0 or float(bar.get("high", 0)) <= float(bar.get("low", 0)):
            if bar is None or float(bar.get("volume", 0.0)) <= 0:
                return PriceLimitState.SUSPENDED
        prev_close = float(bar.get("prev_close", 0.0))
        open_px = float(bar["open"])
        high_px = float(bar["high"])
        low_px = float(bar["low"])
        if prev_close <= 0:
            return PriceLimitState.CONSERVATIVE_DAILY_MODEL
        up, down = self.limit_prices(symbol, ts, prev_close)
        one_word = high_px <= low_px + 1e-8
        if open_px >= up * 0.98:
            if one_word:
                return PriceLimitState.LIMIT_UP_LOCKED
            return PriceLimitState.LIMIT_UP_OPENED
        if open_px <= down * 1.02:
            if one_word:
                return PriceLimitState.LIMIT_DOWN_LOCKED
            return PriceLimitState.LIMIT_DOWN_OPENED
        return PriceLimitState.NORMAL

    def can_buy_at_open(self, symbol: str, ts, bar: Optional[dict]) -> tuple:
        """开盘可买？保守模型：一字涨停 / 涨停开盘即不可买。"""
        if bar is None or float(bar.get("volume", 0.0)) <= 0:
            return False, "SUSPENDED"
        st = self.classify_daily(symbol, ts, bar)
        if st == PriceLimitState.LIMIT_UP_LOCKED:
            return False, "LIMIT_UP_LOCKED"
        if st == PriceLimitState.LIMIT_UP_OPENED:
            # 日线无法知道开板时点；保守：涨停开盘视为不可追（DAILY_APPROXIMATION）
            return False, "LIMIT_UP_OPEN_DAILY_CONSERVATIVE"
        return True, "OK"

    def can_sell_at_open(self, symbol: str, ts, bar: Optional[dict]) -> tuple:
        if bar is None or float(bar.get("volume", 0.0)) <= 0:
            return False, "SUSPENDED"
        st = self.classify_daily(symbol, ts, bar)
        if st == PriceLimitState.LIMIT_DOWN_LOCKED:
            return False, "LIMIT_DOWN_LOCKED"
        if st == PriceLimitState.LIMIT_DOWN_OPENED:
            return False, "LIMIT_DOWN_OPEN_DAILY_CONSERVATIVE"
        return True, "OK"


class SuspensionModel:
    """统一判断停牌：无 bar / volume<=0。"""

    def is_suspended(self, symbol: str, ts, bar: Optional[dict]) -> bool:
        if bar is None:
            return True
        return float(bar.get("volume", 0.0)) <= 0
