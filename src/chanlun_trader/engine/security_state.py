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
    # PIT 历史资格状态：NORMAL | ST | UNKNOWN | CONFLICT。
    # 默认 UNKNOWN —— 缺失/冲突**不得**默认成 NORMAL（fail-closed）。
    st_status: str = "UNKNOWN"
    st_source: str = ""
    # 两条时间轴分开保存（空串 = 该轴未声明约束）：
    #   effective_available_at  —— 交易所生效事实何时可被观察
    #   researcher_available_at —— 该状态何时进入研究者可用数据集
    # 门控取所有已声明约束中最晚者；任一为 UNKNOWN 哨兵则门控 UNKNOWN。
    available_at: str = ""
    effective_available_at: str = ""
    researcher_available_at: str = ""
    # 有效期（含）。随记录从资格表传入，供执行层检查。
    valid_to: Optional[int] = None
    # 是否来自"日度观测合同"（要求命中当日观测）
    strict_daily: bool = False
    # 冲突来源（CONFLICT 时保留）
    conflict_sources: tuple = ()

    @property
    def st_status_known(self) -> bool:
        """只有 NORMAL/ST 是"有证据"的状态；UNKNOWN/CONFLICT 视为未知。"""
        return self.st_status in ("NORMAL", "ST")

    def gate(self):
        """合成门控 (kind, timestamp)：取最晚约束，任一 UNKNOWN 即 UNKNOWN。"""
        from .historical_eligibility import resolve_gate
        return resolve_gate(self.effective_available_at,
                            self.researcher_available_at,
                            self.available_at)

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
    """Security lifecycle as_of。支持逐日状态更新，未提供时按代码前缀推断。

    `strict_daily_symbols` 中的证券采用**日度观测合同**：查询日必须命中当日观测行，
    否则返回 UNKNOWN（fail-closed），不允许沿用上一观测。
    """

    def __init__(self):
        self._states: Dict[str, List[SecurityState]] = {}
        self.strict_daily_symbols: set = set()

    def add_state(self, state: SecurityState):
        self._states.setdefault(state.symbol, []).append(state)
        self._states[state.symbol].sort(key=lambda s: s.asof_date)

    def as_of(self, symbol: str, ts) -> SecurityState:
        """按知识时刻返回该证券状态（fail-closed）。

        门控取所有已声明约束中最晚者：显式更晚的 available_at 不会被另一条轴覆盖；
        空串严格表示"该轴未声明"，不表示"无限制可用"。
        日度观测合同证券：查询日必须命中当日记录，否则 UNKNOWN。
        **最新生效事实**语义：定位 asof_date <= d 的最后一条后判断可用性；
        若它过期或门控未到，返回 UNKNOWN，**不回退**到更早记录
        （避免已被替代的旧 NORMAL 复活）。
        """
        from .historical_eligibility import gate_allows
        aware = ensure_aware(ts)
        d = date_key(aware)
        states = self._states.get(symbol, [])
        if symbol in self.strict_daily_symbols:
            same_day = [s for s in states if s.asof_date == d]
            if not same_day:
                return SecurityState(symbol=symbol, asof_date=d,
                                     board=SecurityState.infer_board(symbol))
            states = same_day
        latest = None
        for s in states:
            if s.asof_date > d:
                break
            latest = s
        if latest is None:
            return SecurityState(symbol=symbol, asof_date=d,
                                 board=SecurityState.infer_board(symbol))
        if latest.valid_to is not None and d > int(latest.valid_to):
            return SecurityState(symbol=symbol, asof_date=d,
                                 board=SecurityState.infer_board(symbol))
        kind, gate_ts = latest.gate()
        if not gate_allows(kind, gate_ts, aware):
            return SecurityState(symbol=symbol, asof_date=d,
                                 board=SecurityState.infer_board(symbol))
        return latest

    def load_from_eligibility(self, table) -> int:
        """从 PIT 资格表装载状态，保留双轴可用时间、valid_to 与冲突来源。"""
        from .historical_eligibility import AVAIL_NOT_DECLARED, ST_CONFLICT
        n = 0
        for sym in table.symbols():
            board = SecurityState.infer_board(sym)
            daily = getattr(table, "_daily", {}).get(sym)
            if daily:
                self.strict_daily_symbols.add(sym)
                for day in sorted(daily):
                    slot = daily[day]
                    statuses = slot["statuses"]
                    status = (ST_CONFLICT if len(statuses) > 1
                              else next(iter(statuses)))
                    self.add_state(SecurityState(
                        symbol=sym, asof_date=int(day), is_st=(status == "ST"),
                        board=board, st_status=status,
                        st_source=",".join(sorted(slot["sources"])),
                        available_at=slot["res"] or slot["eff"] or AVAIL_NOT_DECLARED,
                        effective_available_at=slot["eff"] or AVAIL_NOT_DECLARED,
                        researcher_available_at=slot["res"] or AVAIL_NOT_DECLARED,
                        strict_daily=True,
                        conflict_sources=tuple(sorted(slot["sources"]))
                        if len(statuses) > 1 else (),
                    ))
                    n += 1
            else:
                for rec in table._rows.get(sym, []):
                    self.add_state(SecurityState(
                        symbol=sym, asof_date=int(rec.effective_date),
                        is_st=(rec.st_status == "ST"), board=board,
                        st_status=rec.st_status, st_source=rec.source,
                        available_at=rec.available_at or AVAIL_NOT_DECLARED,
                        effective_available_at=(rec.effective_available_at
                                                or AVAIL_NOT_DECLARED),
                        researcher_available_at=(rec.researcher_available_at
                                                 or AVAIL_NOT_DECLARED),
                        valid_to=rec.valid_to,
                        conflict_sources=tuple(rec.conflict_sources),
                    ))
                    n += 1
        return n


class ChinaPriceLimitModel:
    """PIT 涨跌停制度。当前 PIT ST 状态来自 SecurityMaster；没有数据时按代码前缀近似。

    明确标记 CONSERVATIVE_DAILY_MODEL：日线无法精确定义盘口时保守拒绝。
    """

    def __init__(self, security_master: Optional[SecurityMaster] = None,
                 pit_enforced: bool = False):
        """pit_enforced=True 时启用 PIT 历史资格 fail-closed 判定。

        默认 False 以保持既有调用方（未接入 PIT 数据）行为不变；
        接入 HistoricalEligibilityTable 的路径必须显式置 True。
        """
        self.master = security_master or SecurityMaster()
        self.pit_enforced = bool(pit_enforced)

    def limit_pct(self, symbol: str, ts) -> float:
        """按**板块**与历史生效日给出涨跌停幅度。

        风险警示（ST/*ST）**不能一律按 5%**：主板风险警示为 5%，
        创业板/科创板风险警示仍为 20%。本模型按 board 分别处理。
        """
        st = self.master.as_of(symbol, ts)
        if st.board in ("CHINEXT", "STAR"):
            return 0.20          # 创业板/科创板：风险警示不改变 20% 限制
        if st.board == "BSE":
            return 0.30
        if st.is_st:
            return 0.05          # 主板风险警示 5%
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
        """开盘可买？保守模型：一字涨停 / 涨停开盘即不可买。

        启用 PIT 时接入历史资格：ST 或**未知/冲突**状态一律不可买（fail-closed）。
        缺失状态不得被当作 NORMAL 放行。
        """
        if bar is None or float(bar.get("volume", 0.0)) <= 0:
            return False, "SUSPENDED"
        if self.pit_enforced:
            state = self.master.as_of(symbol, ts)
            if state.st_status == "ST":
                return False, "ST_NOT_ELIGIBLE"
            if state.st_status == "CONFLICT":
                return False, "ST_STATUS_CONFLICT_FAIL_CLOSED"
            if state.st_status == "UNKNOWN":
                return False, "ST_STATUS_UNKNOWN_FAIL_CLOSED"
        st = self.classify_daily(symbol, ts, bar)
        if st == PriceLimitState.LIMIT_UP_LOCKED:
            return False, "LIMIT_UP_LOCKED"
        if st == PriceLimitState.LIMIT_UP_OPENED:
            # 日线无法知道开板时点；保守：涨停开盘视为不可追（DAILY_APPROXIMATION）
            return False, "LIMIT_UP_OPEN_DAILY_CONSERVATIVE"
        return True, "OK"

    def can_sell_at_open(self, symbol: str, ts, bar: Optional[dict]) -> tuple:
        """开盘可卖？

        **卖出不因 ST/未知状态被阻断**——既有持仓必须能按交易合同退出，
        不能因为入场资格过滤而被"删掉"。仅跌停/停牌阻断。
        """
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
