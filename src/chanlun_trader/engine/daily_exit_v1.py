"""BT_DAILY_EXIT_V1 — 日线"收盘判断、次一 session 开盘尝试执行"退出规则。

语义冻结（见 docs/BACKTEST_BEHAVIOR_ACCEPTANCE_V1.md）：

- 成本锚 = 该 lot 的**实际成交价**（含撮合滑点、不含佣金），即 ``lot.entry_price``。
- 触发判定只用**已完成日线收盘价**（RAW 口径，与成交价同一口径）。
- 触发只产生"退出意图"；最早在**下一交易日开盘**尝试成交。
- 盘中触价、Tick / 盘口、多周期精确执行**不属于本版本范围**，请求即明确拒绝。
- 旧结构止损保留独立语义：RAW 成交价与 QFQ 结构价不可直接比较，缺少
  可用转换证据时必须**拒绝配置**，不得静默忽略或偷偷改成成本止损。

本模块不修改 ``PortfolioExitEvaluatorV1``；两者并存，语义分别版本化。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import pandas as pd

from .portfolio_exit import PortfolioExitDecision
from .position import PositionLot
from .time_types import ensure_aware

DAILY_EXIT_CONTRACT_VERSION = "BT_DAILY_EXIT_V1"
DAILY_EXIT_EXECUTION_MODE = "CLOSE_CONFIRM_NEXT_SESSION_OPEN"

# 仅主展示原因采用固定优先级；这不是伪造盘中先后顺序。
REASON_PRIORITY: Sequence[str] = (
    "EXIT_FIXED_COST_STOP",
    "EXIT_TRAILING_STOP",
    "EXIT_FIXED_TAKE_PROFIT",
    "EXIT_STRUCTURE_OR_INDICATOR",
    "EXIT_FIXED_HOLD",
)

SUPPORTED_EXIT_TYPES = (
    "FIXED_COST_STOP",
    "FIXED_TAKE_PROFIT",
    "TRAILING_CLOSE_STOP",
    "FIXED_HOLD",
)
# 明确保留但本轮未验收的类型：配置即拒绝，绝不静默降级。
RESERVED_EXIT_TYPES = (
    "STRUCTURE_STOP",
    "INDICATOR_EXIT",
    "INTRADAY_TOUCH_STOP",
    "TICK_LEVEL_EXIT",
    "MULTI_TIMEFRAME_EXIT",
)


class ExitConfigError(ValueError):
    """退出配置非法或不支持。调用方必须显式处理，不得吞掉。"""


@dataclass(frozen=True)
class DailyExitRuleSetV1:
    """本版本支持的四类退出规则；字段为 None 表示未启用。"""

    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    trailing_activate_pct: Optional[float] = None
    trailing_pct: Optional[float] = None
    fixed_holding_sessions: Optional[int] = None
    exit_types: tuple = SUPPORTED_EXIT_TYPES
    requested_unsupported: tuple = ()

    def __post_init__(self):
        for name in self.requested_unsupported:
            raise ExitConfigError(f"UNSUPPORTED_EXIT_TYPE:{name}")
        for name in self.exit_types:
            if name in RESERVED_EXIT_TYPES:
                raise ExitConfigError(f"UNSUPPORTED_EXIT_TYPE:{name}")
            if name not in SUPPORTED_EXIT_TYPES:
                raise ExitConfigError(f"UNKNOWN_EXIT_TYPE:{name}")
        for label, value in (
            ("stop_loss_pct", self.stop_loss_pct),
            ("take_profit_pct", self.take_profit_pct),
            ("trailing_activate_pct", self.trailing_activate_pct),
            ("trailing_pct", self.trailing_pct),
        ):
            if value is not None and not (0.0 < float(value) < 1.0):
                raise ExitConfigError(f"INVALID_EXIT_PARAMETER:{label}")
        if (self.trailing_activate_pct is None) != (self.trailing_pct is None):
            raise ExitConfigError("TRAILING_PARAMETERS_INCOMPLETE")
        if self.fixed_holding_sessions is not None and int(self.fixed_holding_sessions) < 0:
            raise ExitConfigError("INVALID_EXIT_PARAMETER:fixed_holding_sessions")

    @property
    def enabled(self) -> bool:
        return any((
            self.stop_loss_pct is not None,
            self.take_profit_pct is not None,
            self.trailing_pct is not None,
            self.fixed_holding_sessions is not None,
        ))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_version": DAILY_EXIT_CONTRACT_VERSION,
            "execution_mode": DAILY_EXIT_EXECUTION_MODE,
            "exit_types": list(self.exit_types),
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "trailing_activate_pct": self.trailing_activate_pct,
            "trailing_pct": self.trailing_pct,
            "fixed_holding_sessions": self.fixed_holding_sessions,
            "cost_anchor": "ACTUAL_LOT_ENTRY_FILL_PRICE_EXCLUDING_COMMISSION",
            "trigger_price_field": "COMPLETED_DAILY_CLOSE_RAW",
            "earliest_execution": "NEXT_SESSION_OPEN",
            "reason_priority": list(REASON_PRIORITY),
        }


@dataclass
class LotTrailingState:
    """每个 lot 独立的移动止损状态。部分卖出不重置。"""

    peak_close: float
    activated: bool = False
    line: float = 0.0
    peak_session: Optional[int] = None


class DailyExitEvaluatorV1:
    """在已完成收盘价上评估四类日线退出规则。

    与 ``PortfolioExitEvaluatorV1`` 的差异（必须显式标注）：
    - 成本锚为 lot 实际成交价，而非信号结构价；
    - 触发价格来自已完成日线收盘（RAW），而非 factor 值；
    - 保留独立的固定止盈 / 移动止损语义。
    """

    VERSION = DAILY_EXIT_CONTRACT_VERSION

    def __init__(self, candidate_id: str, portfolio_id: str, rules: DailyExitRuleSetV1):
        self.candidate_id = str(candidate_id)
        self.portfolio_id = str(portfolio_id)
        self.rules = rules
        self.trailing: Dict[str, LotTrailingState] = {}
        self.evaluations: List[dict] = []

    # -- 价格访问 ---------------------------------------------------------
    @staticmethod
    def _completed_close(store, symbol: str, trade_session: int) -> Optional[float]:
        """只读取 trade_session 当天已完成的 RAW 日线收盘价。"""
        bar = store.get_daily_bar(symbol, int(trade_session), price_mode="raw")
        if bar is None:
            return None
        price = float(bar.get("close", 0.0))
        return price if price > 0 else None

    def _trailing_state(self, lot: PositionLot) -> Optional[LotTrailingState]:
        if self.rules.trailing_pct is None:
            return None
        anchor = float(lot.entry_price)
        if anchor <= 0:
            return None
        state = self.trailing.get(lot.lot_id)
        if state is None:
            state = LotTrailingState(peak_close=anchor)
            self.trailing[lot.lot_id] = state
        return state

    def _update_trailing(self, lot: PositionLot, close: float, trade_session: int) -> Optional[LotTrailingState]:
        state = self._trailing_state(lot)
        if state is None:
            return None
        anchor = float(lot.entry_price)
        if close > state.peak_close:
            state.peak_close = close
            state.peak_session = int(trade_session)
        if not state.activated and state.peak_close >= anchor * (1.0 + float(self.rules.trailing_activate_pct)):
            state.activated = True
        if state.activated:
            candidate_line = state.peak_close * (1.0 - float(self.rules.trailing_pct))
            # 同一 lot 只能收紧，不能放松。
            state.line = max(state.line, candidate_line)
        return state

    # -- 评估 -------------------------------------------------------------
    def evaluate(
        self,
        lots: Iterable[PositionLot],
        trade_session: int,
        trade_session_index: int,
        store,
        *,
        session_index_of: Mapping[int, int],
    ) -> List[PortfolioExitDecision]:
        trade_session = int(trade_session)
        decisions: List[PortfolioExitDecision] = []
        for lot in sorted(lots, key=lambda item: (item.symbol, item.buy_time, item.lot_id)):
            if lot.remaining_quantity <= 0 or lot.exit_state == "CLOSED":
                continue
            # 已成立但未成交的退出意图不可撤销：价格反弹不得取消它。
            if lot.exit_state in {"EXIT_DUE", "SELL_PENDING", "PARTIALLY_FILLED"}:
                decisions.append(self._decision(lot, trade_session, trade_session_index,
                                                "SELL_PENDING" if lot.exit_state != "EXIT_DUE" else "EXIT_DUE",
                                                lot.exit_reason or "EXIT_PENDING_RETRY", {}))
                continue
            if lot.entry_session_index is None:
                lot.exit_reason = "ENTRY_SESSION_INDEX_MISSING"
                continue
            close = self._completed_close(store, lot.symbol, trade_session)
            if close is None:
                # 停牌/无 bar：不产生新意图，也不把缺失当正常。
                self.evaluations.append({
                    "trade_session": trade_session, "lot_id": lot.lot_id, "symbol": lot.symbol,
                    "state": "NO_COMPLETED_CLOSE", "reason": "NO_COMPLETED_CLOSE",
                })
                continue
            anchor = float(lot.entry_price)
            if anchor <= 0:
                lot.exit_reason = "ENTRY_PRICE_MISSING"
                continue

            hits: List[str] = []
            if self.rules.stop_loss_pct is not None and "FIXED_COST_STOP" in self.rules.exit_types:
                line = anchor * (1.0 - float(self.rules.stop_loss_pct))
                if close <= line:
                    hits.append("EXIT_FIXED_COST_STOP")
            trailing_state = self._update_trailing(lot, close, trade_session)
            if trailing_state is not None and "TRAILING_CLOSE_STOP" in self.rules.exit_types:
                if trailing_state.activated and close <= trailing_state.line:
                    hits.append("EXIT_TRAILING_STOP")
            if self.rules.take_profit_pct is not None and "FIXED_TAKE_PROFIT" in self.rules.exit_types:
                line = anchor * (1.0 + float(self.rules.take_profit_pct))
                if close >= line:
                    hits.append("EXIT_FIXED_TAKE_PROFIT")
            if self.rules.fixed_holding_sessions is not None and "FIXED_HOLD" in self.rules.exit_types:
                due_index = int(lot.entry_session_index) + int(self.rules.fixed_holding_sessions)
                lot.exit_due_index = due_index
                if trade_session_index >= due_index:
                    hits.append("EXIT_FIXED_HOLD")

            if not hits:
                self.evaluations.append({
                    "trade_session": trade_session, "lot_id": lot.lot_id, "symbol": lot.symbol,
                    "state": "OPEN", "close": close, "anchor": anchor,
                    "trailing_activated": bool(trailing_state.activated) if trailing_state else None,
                    "trailing_line": round(trailing_state.line, 6) if trailing_state else None,
                    "peak_close": round(trailing_state.peak_close, 6) if trailing_state else None,
                })
                continue

            primary = next(reason for reason in REASON_PRIORITY if reason in hits)
            lot.exit_state = "EXIT_DUE"
            lot.exit_reason = primary
            lot.exit_due_session = trade_session
            lot.exit_due_index = int(lot.entry_session_index) + int(self.rules.fixed_holding_sessions or 0)
            self.evaluations.append({
                "trade_session": trade_session, "lot_id": lot.lot_id, "symbol": lot.symbol,
                "state": "EXIT_DUE", "close": close, "anchor": anchor,
                "triggered": hits, "primary_reason": primary,
                "trailing_activated": bool(trailing_state.activated) if trailing_state else None,
                "trailing_line": round(trailing_state.line, 6) if trailing_state else None,
                "peak_close": round(trailing_state.peak_close, 6) if trailing_state else None,
            })
            decisions.append(self._decision(lot, trade_session, trade_session_index, "EXIT_DUE", primary,
                                            {"triggered_reasons": list(hits)}))
        return decisions

    def _decision(self, lot: PositionLot, trade_session: int, trade_session_index: int,
                  state: str, reason: str, factor_values: Mapping[str, Any]) -> PortfolioExitDecision:
        return PortfolioExitDecision(
            candidate_id=self.candidate_id,
            portfolio_id=self.portfolio_id,
            lot_id=lot.lot_id,
            symbol=lot.symbol,
            trade_session=int(trade_session),
            trade_session_index=int(trade_session_index),
            state=state,
            reason_code=reason,
            factor_values=dict(factor_values),
        )


def daily_exit_fn(evaluator: DailyExitEvaluatorV1, store, calendar: Sequence[int]):
    """构造 engine.run(exit_fn=...) 使用的回调。

    只在日线 BAR_CLOSE（15:00）评估一次，避免同一 session 重复触发。
    """
    session_index_of = {int(day): index for index, day in enumerate(calendar)}

    def fn(_view, ts, day, ledger):
        ts = ensure_aware(ts)
        if ts.hour != 15 or ts.minute != 0:
            return []
        if int(day) not in session_index_of:
            return []
        return evaluator.evaluate(
            ledger.lots.values(), int(day), session_index_of[int(day)], store,
            session_index_of=session_index_of,
        )

    return fn
