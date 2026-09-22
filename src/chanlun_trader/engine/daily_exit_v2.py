"""BT_DAILY_EXIT_V2 — 退出规则扩展（ATR / 指标条件 / 反向信号 / 结构价）。

与 V1 的关系
------------
本模块**扩展** ``daily_exit_v1.py``：

- V1 的四类退出（固定成本止损、固定止盈、最高收盘价移动止损、固定持有）
  原样复用：``DailyExitRuleSetV2`` 委托给 V1 的 ``DailyExitRuleSetV1`` 与
  ``DailyExitEvaluatorV1``，公式、触发时点与执行语义完全不变；
- V2 新增：ATR 距离止损/跟踪、指标条件退出、反向信号退出、明确版本的结构价退出；
- **旧结构止损独立保留**：``STRUCTURE_STOP`` 与 ``FIXED_COST_STOP`` 分别命名，
  不把前者偷偷改成后者，也不改变旧策略全局默认。

执行语义（与 V1 一致）
----------------------
``CLOSE_CONFIRM_NEXT_SESSION_OPEN``：已完成收盘判断 → 下一 session 开盘尝试执行。
日线高低价不能证明盘中触及先后；未验收的盘中/Tick/盘口执行**明确拒绝**。

尺度纪律
--------
- ATR 距离必须与 RAW 成交/持仓价在**同一尺度**；
- 入场 ATR 锚使用**入场前已可用**的最近值，不能用当天未完成 ATR；
- RAW 成交价与 QFQ 结构价**不能直接相减比较**；缺少可用转换证据时拒绝该配置，
  不伪造完整账户结果。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .daily_exit_v1 import (
    DAILY_EXIT_CONTRACT_VERSION,
    DAILY_EXIT_EXECUTION_MODE,
    REASON_PRIORITY as V1_REASON_PRIORITY,
    SUPPORTED_EXIT_TYPES as V1_SUPPORTED_EXIT_TYPES,
    DailyExitEvaluatorV1,
    DailyExitRuleSetV1,
    ExitConfigError,
)
from .portfolio_exit import PortfolioExitDecision
from .position import PositionLot
from .time_types import ensure_aware

DAILY_EXIT_CONTRACT_V2 = "BT_DAILY_EXIT_V2"

# V2 新增支持
V2_ADDITIONAL_EXIT_TYPES = (
    "ATR_DISTANCE_STOP",
    "ATR_TRAILING_STOP",
    "INDICATOR_CONDITION_EXIT",
    "REVERSE_SIGNAL_EXIT",
    "STRUCTURE_PRICE_STOP",
)
SUPPORTED_EXIT_TYPES_V2 = tuple(V1_SUPPORTED_EXIT_TYPES) + V2_ADDITIONAL_EXIT_TYPES

# 仍不支持的执行模式：请求即拒绝，绝不静默降级。
UNSUPPORTED_EXIT_TYPES_V2 = (
    "INTRADAY_TOUCH_STOP",
    "TICK_LEVEL_EXIT",
    "MULTI_TIMEFRAME_EXIT",
)

# 主展示原因优先级（V2 扩展；顺序不代表盘中先后）
REASON_PRIORITY_V2: Sequence[str] = (
    "EXIT_FIXED_COST_STOP",
    "EXIT_ATR_DISTANCE_STOP",
    "EXIT_ATR_TRAILING_STOP",
    "EXIT_TRAILING_STOP",
    "EXIT_FIXED_TAKE_PROFIT",
    "EXIT_STRUCTURE_PRICE_STOP",
    "EXIT_INDICATOR_CONDITION",
    "EXIT_REVERSE_SIGNAL",
    "EXIT_FIXED_HOLD",
)


@dataclass(frozen=True)
class AtrBinding:
    """一条 ATR 序列的**绑定身份**。

    距离止损与跟踪止损可能使用不同窗口/版本/价格尺度，因此不能共用一条
    未核验的 ATR。绑定身份进入规则合同，缺失即拒绝运行。
    """

    instance_key: str
    atr_window: int
    version: str
    price_mode: str
    available_at_rule: str

    def to_dict(self) -> Dict[str, Any]:
        return {"instance_key": self.instance_key, "atr_window": int(self.atr_window),
                "version": self.version, "price_mode": self.price_mode,
                "available_at_rule": self.available_at_rule}


@dataclass(frozen=True)
class AtrDistanceSpec:
    """ATR 距离规则。

    ``multiple`` 与 ``atr_window`` 决定距离；``anchor`` 决定用哪个 ATR 值：

    - ``ENTRY_LAST_KNOWN``：入场**之前**已可用的最近 ATR（默认，无前视）；
    - ``DYNAMIC_CURRENT``：当根已完成收盘的 ATR（必须显式选择，且只收紧）。

    ``ENTRY_LAST_KNOWN`` 的锚必须在**持仓创建时**冻结：不得回退使用
    入场当天的最终 ATR（那会用到入场之后才知道的波动）。
    """

    multiple: float
    atr_window: int = 14
    anchor: str = "ENTRY_LAST_KNOWN"
    tighten_only: bool = True

    def __post_init__(self):
        if not (float(self.multiple) > 0):
            raise ExitConfigError("INVALID_EXIT_PARAMETER:atr_multiple")
        if int(self.atr_window) < 1:
            raise ExitConfigError("INVALID_EXIT_PARAMETER:atr_window")
        if self.anchor not in {"ENTRY_LAST_KNOWN", "DYNAMIC_CURRENT"}:
            raise ExitConfigError(f"UNSUPPORTED_ATR_ANCHOR:{self.anchor}")

    def to_dict(self) -> Dict[str, Any]:
        return {"multiple": float(self.multiple), "atr_window": int(self.atr_window),
                "anchor": self.anchor, "tighten_only": bool(self.tighten_only)}


@dataclass(frozen=True)
class DailyExitRuleSetV2:
    """V1 四类 + V2 新增退出。字段为 None / 空表示未启用。"""

    # --- V1 部分（原样复用） ---
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    trailing_activate_pct: Optional[float] = None
    trailing_pct: Optional[float] = None
    fixed_holding_sessions: Optional[int] = None
    # --- V2 新增 ---
    atr_distance: Optional[AtrDistanceSpec] = None
    atr_trailing: Optional[AtrDistanceSpec] = None
    indicator_condition_exit: Optional[Any] = None       # Expr（三值为 TRUE 时退出）
    reverse_signal_exit: Optional[Any] = None            # Expr（三值为 TRUE 时退出）
    structure_stop_price: Optional[float] = None         # 结构价（RAW 尺度，显式声明）
    structure_stop_scale: str = "UNSPECIFIED"
    exit_types: tuple = SUPPORTED_EXIT_TYPES_V2
    requested_unsupported: tuple = ()

    def __post_init__(self):
        for name in self.requested_unsupported:
            raise ExitConfigError(f"UNSUPPORTED_EXIT_TYPE:{name}")
        for name in self.exit_types:
            if name in UNSUPPORTED_EXIT_TYPES_V2:
                raise ExitConfigError(f"UNSUPPORTED_EXIT_TYPE:{name}")
            if name not in SUPPORTED_EXIT_TYPES_V2:
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
        # 结构价止损：RAW 与 QFQ 不可直接比较；未声明可用尺度即拒绝。
        if self.structure_stop_price is not None:
            if self.structure_stop_scale not in {"RAW", "QFQ_WITH_CONVERSION_EVIDENCE"}:
                raise ExitConfigError("STRUCTURE_STOP_SCALE_NOT_USABLE")
            if self.structure_stop_price <= 0:
                raise ExitConfigError("INVALID_EXIT_PARAMETER:structure_stop_price")
            if self.structure_stop_scale == "QFQ_WITH_CONVERSION_EVIDENCE":
                raise ExitConfigError("STRUCTURE_STOP_CONVERSION_NOT_AVAILABLE")

    @property
    def v1_rules(self) -> DailyExitRuleSetV1:
        """V1 部分；原样委托，不改语义。"""
        return DailyExitRuleSetV1(
            stop_loss_pct=self.stop_loss_pct,
            take_profit_pct=self.take_profit_pct,
            trailing_activate_pct=self.trailing_activate_pct,
            trailing_pct=self.trailing_pct,
            fixed_holding_sessions=self.fixed_holding_sessions,
        )

    @property
    def enabled(self) -> bool:
        return any((
            self.v1_rules.enabled,
            self.atr_distance is not None,
            self.atr_trailing is not None,
            self.indicator_condition_exit is not None,
            self.reverse_signal_exit is not None,
            self.structure_stop_price is not None,
        ))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_version": DAILY_EXIT_CONTRACT_V2,
            "v1_contract_version": DAILY_EXIT_CONTRACT_VERSION,
            "execution_mode": DAILY_EXIT_EXECUTION_MODE,
            "exit_types": list(self.exit_types),
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "trailing_activate_pct": self.trailing_activate_pct,
            "trailing_pct": self.trailing_pct,
            "fixed_holding_sessions": self.fixed_holding_sessions,
            "atr_distance": self.atr_distance.to_dict() if self.atr_distance else None,
            "atr_trailing": self.atr_trailing.to_dict() if self.atr_trailing else None,
            "indicator_condition_exit": (
                self.indicator_condition_exit.to_dict()
                if hasattr(self.indicator_condition_exit, "to_dict") else None),
            "reverse_signal_exit": (
                self.reverse_signal_exit.to_dict()
                if hasattr(self.reverse_signal_exit, "to_dict") else None),
            "structure_stop_price": self.structure_stop_price,
            "structure_stop_scale": self.structure_stop_scale,
            "cost_anchor": "ACTUAL_LOT_ENTRY_FILL_PRICE_EXCLUDING_COMMISSION",
            "trigger_price_field": "COMPLETED_DAILY_CLOSE_RAW",
            "earliest_execution": "NEXT_SESSION_OPEN",
            "reason_priority": list(REASON_PRIORITY_V2),
        }


@dataclass
class AtrTrailingState:
    """每个 lot 独立的 ATR 跟踪状态。部分卖出不重置。"""

    entry_atr: float
    line: float
    peak_close: float
    activated: bool = True


class DailyExitEvaluatorV2:
    """V1 四类 + V2 新增退出的评估器。

    内部**委托** V1 评估器处理四类基础退出，因此 V1 的触发时点、成本锚、
    移动止损语义完全不变；V2 只在其上追加新规则并合并原因。
    """

    VERSION = DAILY_EXIT_CONTRACT_V2

    def __init__(self, candidate_id: str, portfolio_id: str, rules: DailyExitRuleSetV2,
                 *, condition_evaluator=None):
        self.candidate_id = str(candidate_id)
        self.portfolio_id = str(portfolio_id)
        self.rules = rules
        self._v1 = DailyExitEvaluatorV1(candidate_id, portfolio_id, rules.v1_rules)
        self._condition_evaluator = condition_evaluator
        self.atr_trailing: Dict[str, AtrTrailingState] = {}
        self._entry_atr: Dict[str, float] = {}
        self._entry_atr_binding: Dict[str, AtrBinding] = {}
        # 规则启用但缺少必需 ATR 绑定时的阻断记录（不得把未执行止损报成正常完成）
        self.blocked_lots: List[dict] = []
        self.evaluations: List[dict] = []
        self._v1_consumed = 0

    # -- 价格与指标访问 ---------------------------------------------------
    @staticmethod
    def _completed_close(store, symbol: str, trade_session: int) -> Optional[float]:
        bar = store.get_daily_bar(symbol, int(trade_session), price_mode="raw")
        if bar is None:
            return None
        price = float(bar.get("close", 0.0))
        return price if price > 0 else None

    @staticmethod
    def _atr_at(atr_series: Mapping[str, pd.Series], symbol: str, session: int) -> Optional[float]:
        """取 ``session`` 当天**已完成**的 ATR。缺失即 None（不补值）。"""
        series = atr_series.get(symbol)
        if series is None or int(session) not in series.index:
            return None
        value = series.loc[int(session)]
        value = float(value)
        return value if np.isfinite(value) and value > 0 else None

    def register_entry_atr(self, lot_id: str, atr_value: float, *,
                           binding: Optional[AtrBinding] = None) -> None:
        """登记该 lot 的**入场前已可用** ATR 锚。

        调用方必须在成交发生时（或之前）用当时可见的 ATR 调用本方法；
        不得传入入场当天未完成的 ATR。
        """
        value = float(atr_value)
        if not np.isfinite(value) or value <= 0:
            raise ExitConfigError(f"INVALID_ENTRY_ATR:{atr_value!r}")
        self._entry_atr[lot_id] = value
        if binding is not None:
            self._entry_atr_binding[lot_id] = binding

    def freeze_entry_anchor(self, lot_id: str, symbol: str, entry_session: int,
                            atr_series: Mapping[str, pd.Series]) -> Optional[float]:
        """在**持仓创建时**冻结入场 ATR 锚：取严格早于入场日的最近可用 ATR。

        绝不回退到入场当天的最终 ATR —— 那是入场之后才知道的信息。
        无可用历史 ATR 时返回 None（调用方必须据此拒绝或阻断入场）。
        """
        series = atr_series.get(symbol)
        if series is None:
            return None
        history = series[series.index < int(entry_session)]
        history = history[np.isfinite(history.to_numpy(dtype=float)) & (history.to_numpy(dtype=float) > 0)]
        if history.empty:
            return None
        value = float(history.iloc[-1])
        self._entry_atr[lot_id] = value
        return value

    # -- 评估 -------------------------------------------------------------
    def evaluate(
        self,
        lots: Iterable[PositionLot],
        trade_session: int,
        trade_session_index: int,
        store,
        *,
        session_index_of: Mapping[int, int],
        atr_series: Optional[Mapping[str, pd.Series]] = None,
        condition_context: Optional[Any] = None,
    ) -> List[PortfolioExitDecision]:
        trade_session = int(trade_session)
        atr_series = dict(atr_series or {})

        # 1) V1 四类：委托评估以取得 V1 判定，但不让 V1 提前改写 lot 状态，
        #    否则 V2 追加规则无法与 V1 原因合并。先快照，评估后原样回滚。
        lots_list = list(lots)
        pre_state = {
            lot.lot_id: (lot.exit_state, lot.exit_reason, lot.exit_due_session, lot.exit_due_index)
            for lot in lots_list
        }
        v1_decisions = self._v1.evaluate(
            lots_list, trade_session, trade_session_index, store, session_index_of=session_index_of)
        for lot in lots_list:
            state, reason, due_session, due_index = pre_state[lot.lot_id]
            lot.exit_state, lot.exit_reason = state, reason
            lot.exit_due_session, lot.exit_due_index = due_session, due_index
        # V1 评估器本轮新产生的记录不并入 V2 轨迹：V2 会为每个 lot 输出一条
        # 统一记录（含 V1 原因 + V2 原因）。若直接合并会造成同一 lot 同一 session
        # 出现两条评估记录，导致"重复退出"被误判。
        self._v1_consumed = len(self._v1.evaluations)
        v1_by_lot = {
            decision.lot_id: list(decision.factor_values.get("triggered_reasons", []) or [decision.reason_code])
            for decision in v1_decisions if decision.state == "EXIT_DUE"
        }

        decisions: List[PortfolioExitDecision] = []
        for lot in sorted(lots_list, key=lambda item: (item.symbol, item.buy_time, item.lot_id)):
            if lot.remaining_quantity <= 0 or lot.exit_state == "CLOSED":
                continue
            # 已成立但未成交的退出意图不可撤销。
            if lot.exit_state in {"EXIT_DUE", "SELL_PENDING", "PARTIALLY_FILLED"}:
                decisions.append(self._decision(
                    lot, trade_session, trade_session_index,
                    "SELL_PENDING" if lot.exit_state != "EXIT_DUE" else "EXIT_DUE",
                    lot.exit_reason or "EXIT_PENDING_RETRY", {}))
                continue
            if lot.entry_session_index is None:
                continue
            close = self._completed_close(store, lot.symbol, trade_session)
            if close is None:
                continue
            anchor = float(lot.entry_price)
            if anchor <= 0:
                continue

            hits: List[str] = []
            extra: Dict[str, Any] = {}

            # V1 已判定的原因（成本止损 / 移动止损 / 固定止盈 / 固定持有）。
            hits.extend(v1_by_lot.get(lot.lot_id, []))

            # 2) ATR 距离止损（固定距离，锚为**入场前已冻结**的 ATR）
            if self.rules.atr_distance is not None and "ATR_DISTANCE_STOP" in self.rules.exit_types:
                entry_atr = self._entry_atr.get(lot.lot_id)
                if entry_atr is None:
                    # 规则启用但缺少冻结锚：**阻断并披露**，不回退到入场当日 ATR，
                    # 也不生成退出意图（那会卖出而非阻断入场）。
                    self._blocked(lot, trade_session, "ATR_DISTANCE_STOP",
                                  "ENTRY_ATR_ANCHOR_NOT_FROZEN")
                else:
                    line = anchor - float(self.rules.atr_distance.multiple) * entry_atr
                    extra["atr_distance_line"] = round(line, 6)
                    extra["entry_atr"] = round(entry_atr, 6)
                    binding = self._entry_atr_binding.get(lot.lot_id)
                    if binding is not None:
                        extra["entry_atr_binding"] = binding.to_dict()
                    if close <= line:
                        hits.append("EXIT_ATR_DISTANCE_STOP")

            # 3) ATR 跟踪止损（只收紧）
            if self.rules.atr_trailing is not None and "ATR_TRAILING_STOP" in self.rules.exit_types:
                if self._entry_atr.get(lot.lot_id) is None and self.rules.atr_trailing.anchor == "ENTRY_LAST_KNOWN":
                    self._blocked(lot, trade_session, "ATR_TRAILING_STOP",
                                  "ENTRY_ATR_ANCHOR_NOT_FROZEN")
                else:
                    state = self._update_atr_trailing(lot, close, atr_series, trade_session)
                    if state is None:
                        self._blocked(lot, trade_session, "ATR_TRAILING_STOP",
                                      "ATR_SERIES_UNAVAILABLE")
                    else:
                        extra["atr_trailing_line"] = round(state.line, 6)
                        extra["atr_trailing_peak"] = round(state.peak_close, 6)
                        if close <= state.line:
                            hits.append("EXIT_ATR_TRAILING_STOP")

            # 4) 结构价止损（RAW 尺度，显式声明；不隐含转换成成本止损）
            if self.rules.structure_stop_price is not None and "STRUCTURE_PRICE_STOP" in self.rules.exit_types:
                line = float(self.rules.structure_stop_price)
                extra["structure_stop_line"] = line
                if close <= line:
                    hits.append("EXIT_STRUCTURE_PRICE_STOP")

            # 5) 指标条件退出 / 反向信号退出（三值：只有 TRUE 才退出）
            #    上下文必须按 **lot.symbol** 取，多证券不得共用一个上下文。
            if condition_context is not None:
                lot_context = self._context_for(condition_context, lot.symbol)
                if (self.rules.indicator_condition_exit is not None
                        and "INDICATOR_CONDITION_EXIT" in self.rules.exit_types):
                    if self._condition_true(self.rules.indicator_condition_exit,
                                            lot_context, trade_session):
                        hits.append("EXIT_INDICATOR_CONDITION")
                if (self.rules.reverse_signal_exit is not None
                        and "REVERSE_SIGNAL_EXIT" in self.rules.exit_types):
                    if self._condition_true(self.rules.reverse_signal_exit,
                                            lot_context, trade_session):
                        hits.append("EXIT_REVERSE_SIGNAL")

            if not hits:
                self.evaluations.append({
                    "trade_session": trade_session, "lot_id": lot.lot_id, "symbol": lot.symbol,
                    "state": "OPEN", "close": close, "anchor": anchor, **extra,
                })
                continue

            primary = next((reason for reason in REASON_PRIORITY_V2 if reason in hits), hits[0])
            lot.exit_state = "EXIT_DUE"
            lot.exit_reason = primary
            lot.exit_due_session = trade_session
            self.evaluations.append({
                "trade_session": trade_session, "lot_id": lot.lot_id, "symbol": lot.symbol,
                "state": "EXIT_DUE", "close": close, "anchor": anchor,
                "triggered": hits, "primary_reason": primary, **extra,
            })
            decisions.append(self._decision(
                lot, trade_session, trade_session_index, "EXIT_DUE", primary,
                {"triggered_reasons": list(hits)}))

        return decisions

    @staticmethod
    def _context_for(condition_context, symbol: str):
        """按 lot.symbol 取该证券的条件上下文。

        支持两种形态：

        - ``{symbol: ConditionContext}``（多证券，**必须**按 symbol 取）；
        - 单个 ``ConditionContext``（仅当该请求只有一个证券时由调用方给出）。

        多证券却拿到单一上下文即拒绝，绝不"取第一个"。
        """
        if isinstance(condition_context, Mapping):
            context = condition_context.get(symbol)
            if context is None:
                raise ExitConfigError(f"CONDITION_CONTEXT_MISSING_FOR_SYMBOL:{symbol}")
            return context
        return condition_context

    def _condition_true(self, node, condition_context, trade_session: int) -> bool:
        """只有三值为 TRUE 才算命中；FALSE 与 UNKNOWN 都不触发。

        必须按**当前 trade_session** 取值，不能用上下文最后一根（那会造成前视）。
        """
        if self._condition_evaluator is None:
            raise ExitConfigError("CONDITION_EVALUATOR_REQUIRED")
        series = self._condition_evaluator.evaluate(node, condition_context)
        session = int(trade_session)
        if session not in series.index:
            return False
        value = series.loc[session]
        return bool(np.isfinite(value) and value > 0)

    def _blocked(self, lot: PositionLot, trade_session: int, rule: str, reason: str) -> None:
        """记录"规则已启用但无法执行"的阻断。

        这类情况**不得**被当作正常完成：调用方据此拒绝运行或披露未执行止损。
        """
        record = {"trade_session": int(trade_session), "lot_id": lot.lot_id,
                  "symbol": lot.symbol, "rule": rule, "blocked_reason": reason}
        if record not in self.blocked_lots:
            self.blocked_lots.append(record)

    def _update_atr_trailing(self, lot: PositionLot, close: float,
                             atr_series: Mapping[str, pd.Series],
                             trade_session: int) -> Optional[AtrTrailingState]:
        spec = self.rules.atr_trailing
        if spec is None:
            return None
        state = self.atr_trailing.get(lot.lot_id)
        if state is None:
            entry_atr = self._entry_atr.get(lot.lot_id)
            if entry_atr is None:
                return None
            state = AtrTrailingState(
                entry_atr=entry_atr, line=float(lot.entry_price) - float(spec.multiple) * entry_atr,
                peak_close=max(float(lot.entry_price), close),
            )
            self.atr_trailing[lot.lot_id] = state
            # 首次创建后**继续**走下面的更新逻辑，否则 DYNAMIC_CURRENT 在首根被跳过。
        if close > state.peak_close:
            state.peak_close = close
        if spec.anchor == "DYNAMIC_CURRENT":
            current_atr = self._atr_at(atr_series, lot.symbol, trade_session)
            if current_atr is None:
                # 显式选择 DYNAMIC_CURRENT 却拿不到当日 ATR：按合同拒绝，
                # 不退化为入场锚（那会静默改变语义）。
                raise ExitConfigError(
                    f"DYNAMIC_ATR_UNAVAILABLE:{lot.symbol}:{trade_session}")
            candidate = state.peak_close - float(spec.multiple) * current_atr
            state.line = max(state.line, candidate) if spec.tighten_only else candidate
        else:
            candidate = state.peak_close - float(spec.multiple) * state.entry_atr
            state.line = max(state.line, candidate) if spec.tighten_only else candidate
        return state
    def _decision(self, lot: PositionLot, trade_session: int, trade_session_index: int,
                  state: str, reason: str, factor_values: Mapping[str, Any]) -> PortfolioExitDecision:
        return PortfolioExitDecision(
            candidate_id=self.candidate_id, portfolio_id=self.portfolio_id,
            lot_id=lot.lot_id, symbol=lot.symbol,
            trade_session=int(trade_session), trade_session_index=int(trade_session_index),
            state=state, reason_code=reason, factor_values=dict(factor_values),
        )


def daily_exit_fn_v2(evaluator: DailyExitEvaluatorV2, store, calendar: Sequence[int], *,
                     atr_series: Optional[Mapping[str, pd.Series]] = None,
                     condition_context_fn: Optional[Callable[[int], Any]] = None):
    """构造 ``engine.run(exit_fn=...)`` 使用的回调（V2）。

    只在日线 BAR_CLOSE（15:00）评估一次，避免同一 session 重复触发。
    """
    session_index_of = {int(day): index for index, day in enumerate(calendar)}

    def fn(_view, ts, day, ledger):
        ts = ensure_aware(ts)
        if ts.hour != 15 or ts.minute != 0:
            return []
        if int(day) not in session_index_of:
            return []
        context = condition_context_fn(int(day)) if condition_context_fn else None
        return evaluator.evaluate(
            ledger.lots.values(), int(day), session_index_of[int(day)], store,
            session_index_of=session_index_of, atr_series=atr_series,
            condition_context=context,
        )

    return fn
