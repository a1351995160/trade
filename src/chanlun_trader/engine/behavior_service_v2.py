"""BT_BEHAVIOR_DAILY_V2 — 注册表驱动的通用行为回测服务。

与 V1 的关系
------------
本模块**扩展** ``behavior_service_v1.py``，而不是替换它：

- 引擎链路完全复用：``BacktestEngineV2`` → ``BrokerSimulator`` → ``PortfolioLedger``
  → ``official_equity_curve``（正式估值）；
- 文件入口与路径约束复用 V1 的 ``resolve_within_root``；
- 日历覆盖校验、正式估值接线、只读边界全部沿用 V1 语义；
- V1 的请求/结果契约保持可用（``BT_BEHAVIOR_DAILY_V1`` 模式不变）。

V2 新增
-------
1. **注册表驱动**：指标、输出名、参数 schema 全部来自 ``IndicatorRegistry``，
   不再硬编码 MACD/KDJ 选项。用户请求只需给指标 id 与参数。
2. **表达式条件**：入场/退出条件用 ``conditions_v2.Expr`` 表达，
   支持 AND/OR/NOT（三值）、CROSS、位置、区间、滚动逻辑。
3. **多家族指标**：任何已注册指标都可作为条件输入并进入账户链。
4. **V2 退出规则**：ATR 距离/跟踪、指标条件、反向信号、明确版本结构价。

边界不变
--------
- 只读计算：不读研究目录、不写盘（除显式 ``write_result``）、不启动进程；
- 不静默回落到旧引擎；未支持模式/条件/参数明确拒绝；
- 盘中触价、Tick/盘口、多周期执行仍明确拒绝。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from .asof import MarketDataStore
from .behavior_service_v1 import (
    BEHAVIOR_MODE as BEHAVIOR_MODE_V1,
    BehaviorPathError,
    BehaviorRequestError,
    _bars_from_mapping,
    load_bars_from_file,
    resolve_within_root,
)
from .conditions_v2 import (
    CONDITION_LAYER_VERSION,
    ConditionContext,
    ConditionError,
    ConditionEvaluator,
    Expr,
    is_eligible,
    rejection_reason,
)
from .custom_indicators_v2 import (
    CUSTOM_DEPENDENCIES,
    custom_condition_fixtures,
    custom_exit_condition_fixtures,
    register_custom_indicators,
)
from .daily_exit_v1 import ExitConfigError
from .daily_exit_v2 import (
    DAILY_EXIT_CONTRACT_V2,
    DAILY_EXIT_EXECUTION_MODE,
    DailyExitEvaluatorV2,
    DailyExitRuleSetV2,
    daily_exit_fn_v2,
)
from .engine import BacktestEngineV2, EngineConfig
from .indicator_registry_v2 import (
    REGISTRY_VERSION,
    IndicatorRegistry,
    IndicatorRegistryError,
    IndicatorResult,
    default_registry,
)
from .indicators_v2 import INDICATORS_V2_VERSION
from .official_valuation import OFFICIAL_VALUATION_TIME, official_equity_curve
from .signal import ExecutionPolicy, Side, Signal
from .time_types import tz_aware
from ..research.run_manifest import stable_hash

BEHAVIOR_MODE_V2 = "BT_BEHAVIOR_DAILY_V2"
SUPPORTED_MODES_V2 = (BEHAVIOR_MODE_V1, BEHAVIOR_MODE_V2)
RESERVED_MODES_V2 = (
    "BT_BEHAVIOR_INTRADAY_V1",
    "BT_BEHAVIOR_TICK_V1",
    "BT_BEHAVIOR_MULTI_TIMEFRAME_V1",
)


def _timestamp(day: int, hour: int, minute: int) -> pd.Timestamp:
    return tz_aware(day // 10000, (day // 100) % 100, day % 100, hour, minute)


def _series(frame: pd.DataFrame, name: str) -> pd.Series:
    """取字段序列，并把**交易日整数键**设为索引。

    指标层要求严格递增的交易日整数键作为时间轴（``_require_time_index``）。
    若这里返回 RangeIndex，指标会把它当时间轴，之后按日期 reindex 会全部变 NaN。
    """
    if name not in frame.columns:
        raise BehaviorRequestError(f"MISSING_BAR_FIELD:{name}")
    values = pd.to_numeric(frame[name], errors="coerce")
    if "date" in frame.columns:
        values = pd.Series(values.to_numpy(), index=pd.Index(frame["date"].astype(int).to_numpy(), name="date"))
    return values


@dataclass
class IndicatorRequest:
    """一个指标请求：id + 可选版本 + 参数。"""

    indicator_id: str
    version: Optional[str] = None
    params: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "IndicatorRequest":
        if isinstance(payload, str):
            return cls(indicator_id=payload)
        if not isinstance(payload, Mapping):
            raise BehaviorRequestError("INVALID_INDICATOR_REQUEST")
        known = {"indicator_id", "version", "params"}
        unknown = sorted(set(payload) - known)
        if unknown:
            raise BehaviorRequestError(f"UNKNOWN_INDICATOR_REQUEST_FIELD:{','.join(unknown)}")
        if not payload.get("indicator_id"):
            raise BehaviorRequestError("INDICATOR_ID_REQUIRED")
        return cls(indicator_id=str(payload["indicator_id"]),
                   version=payload.get("version"),
                   params=dict(payload.get("params") or {}))


@dataclass
class BehaviorRequestV2:
    """V2 请求。``mode`` 缺省为 V2；V1 模式仍可用（走 V1 服务）。"""

    mode: str = BEHAVIOR_MODE_V2
    calendar: Sequence[int] = field(default_factory=list)
    symbols: Sequence[str] = field(default_factory=list)
    bars: Mapping[str, Sequence[Mapping[str, Any]]] = field(default_factory=dict)
    dataset_path: Optional[str] = None
    dataset_root: Optional[str] = None
    # 指标与条件
    indicators: Sequence[Any] = field(default_factory=list)
    entry_condition: Optional[Any] = None
    exit_condition: Optional[Any] = None
    reverse_signal_condition: Optional[Any] = None
    named_entry_condition: Optional[str] = None     # 自定义 fixture 名称
    named_exit_condition: Optional[str] = None
    # 退出规则
    exit_rules: Mapping[str, Any] = field(default_factory=dict)
    # 账户
    initial_cash: float = 100_000.0
    max_positions: int = 1
    max_position_weight: Optional[float] = None
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    slippage_bps: float = 0.001
    max_holding_days: int = 0
    strategy_id: str = "BT_BEHAVIOR_V2"
    persist_run_manifest: bool = False

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "BehaviorRequestV2":
        if not isinstance(payload, Mapping):
            raise BehaviorRequestError("REQUEST_NOT_AN_OBJECT")
        known = {f for f in cls.__dataclass_fields__}
        unknown = sorted(set(payload) - known)
        if unknown:
            raise BehaviorRequestError(f"UNKNOWN_REQUEST_FIELD:{','.join(unknown)}")
        return cls(**dict(payload))

    def validate(self) -> None:
        if self.mode in RESERVED_MODES_V2:
            raise BehaviorRequestError(f"UNSUPPORTED_MODE:{self.mode}")
        if self.mode not in SUPPORTED_MODES_V2:
            raise BehaviorRequestError(f"UNKNOWN_MODE:{self.mode}")
        if not self.calendar:
            raise BehaviorRequestError("EMPTY_CALENDAR")
        if list(self.calendar) != sorted(set(int(d) for d in self.calendar)):
            raise BehaviorRequestError("CALENDAR_NOT_STRICTLY_INCREASING")
        if not self.symbols:
            raise BehaviorRequestError("EMPTY_SYMBOL_SET")
        if self.dataset_path and self.bars:
            raise BehaviorRequestError("AMBIGUOUS_DATA_SOURCE")
        if self.dataset_path and not self.dataset_root:
            raise BehaviorRequestError("DATASET_ROOT_REQUIRED")
        if not self.dataset_path and not self.bars:
            raise BehaviorRequestError("NO_DATA_SOURCE")
        if self.entry_condition is None and not self.named_entry_condition:
            raise BehaviorRequestError("EMPTY_ENTRY_CONDITIONS")


# --------------------------------------------------------------------------
# 表达式解析（受限：只接受显式结构，不接受字符串代码）
# --------------------------------------------------------------------------

_ALLOWED_EXPR_OPS = {
    "const", "indicator", "field", "gt", "ge", "lt", "le", "eq", "ne",
    "and", "or", "not", "cross_up", "cross_down", "above", "below", "between",
    "every", "exist", "barslast", "shift", "ref", "delta", "pct_change",
    "rank", "percentile", "top_n", "add", "sub", "mul", "div",
}


def parse_expression(payload: Any) -> Expr:
    """把 JSON 结构解析成受限 ``Expr``。

    **只接受显式结构**：字符串代码、``eval``/``exec``、动态导入一律拒绝。
    """
    if isinstance(payload, Expr):
        return payload
    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        return Expr("const", (), {"value": float(payload)})
    if not isinstance(payload, Mapping):
        raise BehaviorRequestError(f"INVALID_EXPRESSION_PAYLOAD:{type(payload).__name__}")
    operation = str(payload.get("op", ""))
    if operation not in _ALLOWED_EXPR_OPS:
        raise BehaviorRequestError(f"UNKNOWN_OPERATOR:{operation}")
    params = dict(payload.get("params") or {})
    raw_args = payload.get("args") or []
    if operation == "indicator":
        if not raw_args:
            raise BehaviorRequestError("INDICATOR_OPERATOR_REQUIRES_ID")
        return Expr("indicator", (str(raw_args[0]),), params)
    if operation == "field":
        if not raw_args:
            raise BehaviorRequestError("FIELD_OPERATOR_REQUIRES_NAME")
        return Expr("field", (str(raw_args[0]),), params)
    if operation == "const":
        return Expr("const", (), {"value": float(params.get("value", 0.0))})
    if operation in {"rank", "percentile", "top_n"} and raw_args:
        head = raw_args[0]
        if isinstance(head, Mapping):
            return Expr(operation, (parse_expression(head),), params)
        return Expr(operation, (str(head),), params)
    return Expr(operation, tuple(parse_expression(arg) for arg in raw_args), params)


# --------------------------------------------------------------------------
# 计算：指标 → 条件 → 信号
# --------------------------------------------------------------------------


def compute_indicators(
    registry: IndicatorRegistry,
    frames: Mapping[str, pd.DataFrame],
    requests: Sequence[IndicatorRequest],
) -> Dict[str, Dict[str, IndicatorResult]]:
    """对每个证券计算全部请求指标。返回 ``{symbol: {indicator_id: result}}``。"""
    if not requests:
        raise BehaviorRequestError("EMPTY_INDICATOR_LIST")
    output: Dict[str, Dict[str, IndicatorResult]] = {}
    for symbol in sorted(frames):
        frame = frames[symbol]
        results: Dict[str, IndicatorResult] = {}
        for request in requests:
            try:
                result = registry.compute(
                    request.indicator_id, _series(frame, "close"),
                    version=request.version,
                    high=_series(frame, "high") if "high" in frame.columns else None,
                    low=_series(frame, "low") if "low" in frame.columns else None,
                    open_=_series(frame, "open") if "open" in frame.columns else None,
                    volume=_series(frame, "volume") if "volume" in frame.columns else None,
                    amount=_series(frame, "amount") if "amount" in frame.columns else None,
                    params=request.params,
                )
            except IndicatorRegistryError as exc:
                raise BehaviorRequestError(f"INDICATOR_ERROR:{symbol}:{exc}") from exc
            results[result.indicator_id] = result
        output[symbol] = results
    return output


def build_condition_context(
    frame: pd.DataFrame,
    results: Mapping[str, IndicatorResult],
    index: pd.Index,
) -> ConditionContext:
    """构造求值上下文：指标输出键为 ``f"{indicator_id}.{output}"``。"""
    values: Dict[str, pd.Series] = {}
    ready: Dict[str, pd.Series] = {}
    for indicator_id, result in results.items():
        for name in result.output_names:
            key = f"{indicator_id}.{name}"
            values[key] = result.output(name).reindex(index)
            ready[key] = result.ready().reindex(index).fillna(False)
    fields: Dict[str, pd.Series] = {}
    for name in ("close", "high", "low", "open", "volume", "amount"):
        if name in frame.columns:
            fields[name] = pd.to_numeric(frame[name], errors="coerce").reindex(index)
    return ConditionContext(indicator_values=values, fields=fields, index=index, ready=ready)


def build_entry_signals_v2(
    frames: Mapping[str, pd.DataFrame],
    calendar: Sequence[int],
    indicator_requests: Sequence[IndicatorRequest],
    entry_condition: Expr,
    *,
    registry: Optional[IndicatorRegistry] = None,
    strategy_id: str = "BT_BEHAVIOR_V2",
) -> tuple[List[Signal], Dict[str, Any], Dict[str, Dict[str, IndicatorResult]]]:
    """在已完成日线上判定入场条件，生成次日开盘执行的 V2 Signal。

    只有条件为 **TRUE** 才产生信号；``FALSE`` 与 ``UNKNOWN`` 都不产生，
    且原因可区分地记录在 trace 中。
    """
    reg = registry or default_registry()
    indicator_results = compute_indicators(reg, frames, indicator_requests)
    evaluator = ConditionEvaluator(registry=reg)
    signals: List[Signal] = []
    trace: Dict[str, Any] = {}
    calendar_set = {int(d) for d in calendar}

    for symbol in sorted(frames):
        frame = frames[symbol].sort_values("date").reset_index(drop=True)
        index = pd.Index(frame["date"].astype(int).to_numpy(), name="date")
        prepared = frame.set_index(index)
        context = build_condition_context(prepared, indicator_results[symbol], index)
        condition = evaluator.evaluate(entry_condition, context)
        eligible = is_eligible(condition)
        reasons = rejection_reason(condition)

        symbol_trace: Dict[str, Any] = {
            "indicator_outputs": {
                indicator_id: list(result.output_names)
                for indicator_id, result in indicator_results[symbol].items()
            },
            "condition_true": int(eligible.sum()),
            "condition_false": int((reasons == "CONDITION_FALSE").sum()),
            "condition_unknown": int((reasons == "CONDITION_UNKNOWN").sum()),
        }
        trace[symbol] = symbol_trace

        for day in index:
            if int(day) not in calendar_set or not bool(eligible.loc[day]):
                continue
            signals.append(Signal(
                strategy_id=strategy_id,
                signal_id=f"{strategy_id}:{symbol}:{int(day)}",
                symbol=symbol,
                generated_at=_timestamp(int(day), 15, 0),
                direction=Side.BUY,
                signal_type="EXPRESSION_ENTRY",
                execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
                score=0.0,
            ))
    signals.sort(key=lambda s: (s.generated_at, s.symbol))
    return signals, trace, indicator_results


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


def _exit_rules_from_mapping(payload: Mapping[str, Any]) -> DailyExitRuleSetV2:
    data = dict(payload or {})
    if "atr_distance" in data and isinstance(data["atr_distance"], Mapping):
        from .daily_exit_v2 import AtrDistanceSpec

        data["atr_distance"] = AtrDistanceSpec(**dict(data["atr_distance"]))
    if "atr_trailing" in data and isinstance(data["atr_trailing"], Mapping):
        from .daily_exit_v2 import AtrDistanceSpec

        data["atr_trailing"] = AtrDistanceSpec(**dict(data["atr_trailing"]))
    return DailyExitRuleSetV2(**data)


def run_behavior_backtest_v2(
    request: BehaviorRequestV2 | Mapping[str, Any],
    *,
    registry: Optional[IndicatorRegistry] = None,
    with_custom_fixtures: bool = True,
) -> dict:
    """执行一次 BT_BEHAVIOR_DAILY_V2 回测。

    链路：数据 → 指标 → 条件 → Signal → OrderIntent → Sizer/Risk → Broker
    → Fill → Ledger → OfficialValuation。全程使用真实引擎组件。
    """
    if isinstance(request, Mapping):
        request = BehaviorRequestV2.from_mapping(request)
    request.validate()

    frames = (load_bars_from_file(request.dataset_path, root=request.dataset_root)
              if request.dataset_path else _bars_from_mapping(request.bars))
    missing = [s for s in request.symbols if s not in frames]
    if missing:
        raise BehaviorRequestError(f"SYMBOL_NOT_IN_DATASET:{','.join(sorted(missing))}")
    frames = {s: frames[s] for s in request.symbols}
    calendar = [int(d) for d in request.calendar]

    # 声明日历必须被数据完整覆盖（与 V1 同口径）：缺任一天即拒绝。
    for symbol in request.symbols:
        present = set(int(d) for d in frames[symbol]["date"])
        absent = [d for d in calendar if d not in present]
        if absent:
            raise BehaviorRequestError(
                "CALENDAR_NOT_COVERED_BY_DATA:%s:missing=%d:first=%s%s"
                % (symbol, len(absent), absent[0],
                   " (INCLUDES END SESSION)" if absent[-1] == calendar[-1] else ""))

    reg = registry or default_registry()
    if with_custom_fixtures:
        existing = {spec.indicator_id for spec in reg.specs()}
        if "VOLUME_BREAKOUT_SCORE" not in existing:
            register_custom_indicators(reg)

    # 条件解析：显式表达式优先，其次命名 fixture。
    if request.entry_condition is not None:
        entry_condition = parse_expression(request.entry_condition)
    else:
        fixtures = custom_condition_fixtures()
        name = str(request.named_entry_condition)
        if name not in fixtures:
            raise BehaviorRequestError(f"UNKNOWN_NAMED_ENTRY_CONDITION:{name}")
        entry_condition = fixtures[name]

    exit_condition = None
    if request.exit_condition is not None:
        exit_condition = parse_expression(request.exit_condition)
    elif request.named_exit_condition:
        exit_fixtures = custom_exit_condition_fixtures()
        name = str(request.named_exit_condition)
        if name not in exit_fixtures:
            raise BehaviorRequestError(f"UNKNOWN_NAMED_EXIT_CONDITION:{name}")
        exit_condition = exit_fixtures[name]

    reverse_condition = (parse_expression(request.reverse_signal_condition)
                         if request.reverse_signal_condition is not None else None)

    indicator_requests = [IndicatorRequest.from_mapping(item) for item in request.indicators]
    # 条件里引用的指标若未显式请求，自动补上（保证条件可求值）。
    required = _referenced_indicators(entry_condition)
    for extra in (exit_condition, reverse_condition):
        required |= _referenced_indicators(extra)
    declared = {item.indicator_id for item in indicator_requests}
    for indicator_id in sorted(required - declared):
        indicator_requests.append(IndicatorRequest(indicator_id=indicator_id))
    if not indicator_requests:
        raise BehaviorRequestError("NO_INDICATORS_FOR_CONDITION")

    signals, condition_trace, indicator_results = build_entry_signals_v2(
        frames, calendar, indicator_requests, entry_condition,
        registry=reg, strategy_id=request.strategy_id,
    )

    store = MarketDataStore(feature_price_mode="raw")
    for symbol in request.symbols:
        frame = frames[symbol]
        columns = [c for c in ("open", "high", "low", "close", "volume", "amount") if c in frame.columns]
        indexed = frame.set_index("date")[columns]
        store.add_daily_raw(symbol, indexed.copy())
        store.add_daily_qfq(symbol, indexed.copy())

    weight = request.max_position_weight
    if weight is None:
        weight = 1.0 / max(1, int(request.max_positions))
    config = EngineConfig(
        initial_cash=float(request.initial_cash),
        max_positions=int(request.max_positions),
        max_position_weight=float(weight),
        commission_rate=float(request.commission_rate),
        min_commission=float(request.min_commission),
        stamp_tax_rate=float(request.stamp_tax_rate),
        slippage_bps=float(request.slippage_bps),
        mode="DAILY",
        max_holding_days=int(request.max_holding_days),
        enable_index_filter=False,
        index_filter_enabled=False,
        persist_run_manifest=bool(request.persist_run_manifest),
        execution_model_version="BT_BEHAVIOR_DAILY_V2",
    )
    engine = BacktestEngineV2(store, calendar, config=config,
                              source_identity=("BT_BEHAVIOR_SYNTHETIC_V2", True))
    engine.add_signals(signals)

    rules = _exit_rules_from_mapping(request.exit_rules)
    # 条件退出/反向信号作为规则字段一次性注入，避免事后改写规则对象。
    if exit_condition is not None or reverse_condition is not None:
        rules = DailyExitRuleSetV2(
            stop_loss_pct=rules.stop_loss_pct,
            take_profit_pct=rules.take_profit_pct,
            trailing_activate_pct=rules.trailing_activate_pct,
            trailing_pct=rules.trailing_pct,
            fixed_holding_sessions=rules.fixed_holding_sessions,
            atr_distance=rules.atr_distance,
            atr_trailing=rules.atr_trailing,
            indicator_condition_exit=exit_condition,
            reverse_signal_exit=reverse_condition,
            structure_stop_price=rules.structure_stop_price,
            structure_stop_scale=rules.structure_stop_scale,
            exit_types=rules.exit_types,
        )
    evaluator = DailyExitEvaluatorV2(
        request.strategy_id, request.strategy_id, rules,
        condition_evaluator=ConditionEvaluator(registry=reg) if (
            exit_condition is not None or reverse_condition is not None) else None,
    )

    # 条件上下文按 session 预构建（每个 session 只暴露该日及之前的数据）。
    context_by_session: Dict[int, ConditionContext] = {}
    session_index_of = {int(day): index for index, day in enumerate(calendar)}
    for symbol in request.symbols:
        frame = frames[symbol].sort_values("date").reset_index(drop=True)
        full_index = pd.Index(frame["date"].astype(int).to_numpy(), name="date")
        prepared = frame.set_index(full_index)
        for day in calendar:
            visible = full_index[full_index <= int(day)]
            context_by_session.setdefault(int(day), {})[symbol] = build_condition_context(
                prepared.loc[visible], indicator_results[symbol], visible)

    def condition_context_fn(day: int):
        per_symbol = context_by_session.get(int(day), {})
        if not per_symbol:
            return None
        if len(per_symbol) == 1:
            return next(iter(per_symbol.values()))
        raise BehaviorRequestError("MULTI_SYMBOL_CONDITION_EXIT_NOT_SUPPORTED")

    atr_series = {
        symbol: indicator_results[symbol]["ATR"].output("atr")
        for symbol in request.symbols
        if "ATR" in indicator_results[symbol]
    }
    exit_callback = (daily_exit_fn_v2(evaluator, store, calendar,
                                      atr_series=atr_series,
                                      condition_context_fn=condition_context_fn)
                     if evaluator.rules.enabled else None)
    result = engine.run(exit_fn=exit_callback)

    ledger = result.ledger
    official = official_equity_curve(
        ledger.snapshots, calendar=calendar,
        start_date=calendar[0], end_date=calendar[-1],
        calendar_identity=stable_hash(list(calendar)),
    )
    settlement: Dict[int, Any] = {}
    for snapshot in ledger.snapshots:
        ts = snapshot.timestamp
        if ts.hour != OFFICIAL_VALUATION_TIME[0] or ts.minute != OFFICIAL_VALUATION_TIME[1]:
            continue
        settlement[int(ts.strftime("%Y%m%d"))] = snapshot
    equity_curve = []
    for point in official.points:
        snapshot = settlement[point.date]
        equity_curve.append({
            "date": int(point.date), "timestamp": point.timestamp,
            "event_kind": point.event_kind, "event_sequence": int(point.event_sequence),
            "equity": float(point.equity),
            "cash": float(snapshot.cash), "market_value": float(snapshot.market_value),
        })

    orders = [{
        "order_id": order.order_id, "symbol": order.symbol, "side": order.side.value,
        "quantity": int(order.quantity), "filled_quantity": int(order.filled_quantity),
        "status": order.status.value, "created_at": str(order.created_at),
        "eligible_at": str(order.eligible_at) if order.eligible_at is not None else None,
        "reason_code": str(order.reason_code), "lot_id": order.lot_id,
    } for order in sorted(result.orders.orders.values(), key=lambda o: (o.created_at, o.order_id))]
    rejections = [{"symbol": o["symbol"], "reason": o["reason_code"], "at": o["created_at"],
                   "order_id": o["order_id"]}
                  for o in orders if o["status"] in {"REJECTED", "EXPIRED"}]
    fills = [{
        "fill_time": str(trade.fill_time), "symbol": trade.symbol, "side": trade.side.value,
        "quantity": int(trade.quantity), "price": float(trade.price), "fee": float(trade.fee),
        "gross_value": float(trade.gross_value), "lot_id": trade.lot_id,
        "order_id": trade.order_id, "realized_pnl": float(trade.realized_pnl),
        "reality_flag": trade.reality_flag,
    } for trade in ledger.trades]
    lots = [{
        "lot_id": lot.lot_id, "symbol": lot.symbol, "quantity": int(lot.quantity),
        "remaining_quantity": int(lot.remaining_quantity), "entry_price": float(lot.entry_price),
        "cost": float(lot.cost), "buy_time": str(lot.buy_time),
        "sellable_from": str(lot.sellable_from), "entry_session": lot.entry_session,
        "entry_session_index": lot.entry_session_index,
        "exit_state": lot.exit_state, "exit_reason": lot.exit_reason,
    } for lot in sorted(ledger.lots.values(), key=lambda item: item.lot_id)]

    return {
        "mode": BEHAVIOR_MODE_V2,
        "engine_version": result.context.engine_version,
        "registry_version": REGISTRY_VERSION,
        "indicators_version": INDICATORS_V2_VERSION,
        "condition_contract": CONDITION_LAYER_VERSION,
        "exit_contract": DAILY_EXIT_CONTRACT_V2,
        "exit_execution_mode": DAILY_EXIT_EXECUTION_MODE,
        "price_mode": "RAW_EXECUTION_CALLER_DECLARED_FEATURE",
        "time_rules": {
            "signal_time": "COMPLETED_DAILY_CLOSE_15:00_ASIA_SHANGHAI",
            "earliest_execution": "NEXT_SESSION_OPEN_09:30",
            "timezone": "Asia/Shanghai",
            "intraday_touch": "NOT_SUPPORTED",
            "multi_timeframe": "NOT_SUPPORTED",
        },
        "resolved_config": {
            "request": {
                "mode": request.mode,
                "symbols": list(request.symbols),
                "indicators": [
                    {"indicator_id": item.indicator_id, "version": item.version,
                     "params": dict(item.params)} for item in indicator_requests
                ],
                "entry_condition": (entry_condition.to_dict()
                                    if hasattr(entry_condition, "to_dict") else None),
                "exit_condition": (exit_condition.to_dict()
                                   if hasattr(exit_condition, "to_dict") else None),
                "reverse_signal_condition": (reverse_condition.to_dict()
                                             if hasattr(reverse_condition, "to_dict") else None),
                "exit_rules": rules.to_dict(),
                "initial_cash": float(request.initial_cash),
                "max_positions": int(request.max_positions),
                "max_position_weight": float(weight),
                "commission_rate": float(request.commission_rate),
                "min_commission": float(request.min_commission),
                "stamp_tax_rate": float(request.stamp_tax_rate),
                "slippage_bps": float(request.slippage_bps),
                "max_holding_days": int(request.max_holding_days),
            },
            "engine_config_hash": result.context.config_hash,
            "condition_trace": condition_trace,
            "indicator_outputs": {
                symbol: {iid: list(res.output_names)
                         for iid, res in results.items()}
                for symbol, results in indicator_results.items()
            },
            "custom_dependencies": dict(CUSTOM_DEPENDENCIES),
        },
        "signals": [{
            "signal_id": s.signal_id, "symbol": s.symbol, "generated_at": str(s.generated_at),
            "direction": s.direction.value, "signal_type": s.signal_type,
            "execution_policy": s.execution_policy.value,
        } for s in signals],
        "orders": orders,
        "fills": fills,
        "lots": lots,
        "trades": [{
            "trade_id": t.trade_id, "symbol": t.symbol, "side": t.side.value,
            "quantity": int(t.quantity), "price": float(t.price), "fee": float(t.fee),
            "fill_time": str(t.fill_time), "lot_id": t.lot_id,
            "realized_pnl": float(t.realized_pnl), "reality_flag": t.reality_flag,
        } for t in ledger.trades],
        "rejections": rejections,
        "cash": float(ledger.cash),
        "initial_cash": float(ledger.initial_cash),
        "equity_curve": equity_curve,
        "final_equity": float(official.points[-1].equity),
        "official_valuation": official.to_dict(),
        "exit_evaluations": list(evaluator.evaluations),
        "run_summary": result.summary(),
    }


def _referenced_indicators(node: Optional[Expr]) -> set:
    """收集表达式里引用的指标 id（用于自动补齐指标请求）。"""
    found: set = set()
    if node is None:
        return found
    if not isinstance(node, Expr):
        return found
    if node.op == "indicator" and node.args:
        found.add(str(node.args[0]))
    for arg in node.args:
        if isinstance(arg, Expr):
            found |= _referenced_indicators(arg)
    return found


def result_semantics(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """提取业务语义字段（丢弃随机 ID），供 API/CLI 一致性比较。"""
    def norm_fill(row):
        return {"symbol": row["symbol"], "side": row["side"], "quantity": row["quantity"],
                "price": round(float(row["price"]), 6), "time": row["fill_time"],
                "fee": round(float(row["fee"]), 6)}

    def norm_order(row):
        return {"symbol": row["symbol"], "side": row["side"], "quantity": row["quantity"],
                "status": row["status"], "created_at": row["created_at"],
                "reason_code": row["reason_code"]}

    return {
        "mode": payload["mode"],
        "engine_version": payload["engine_version"],
        "registry_version": payload["registry_version"],
        "condition_contract": payload["condition_contract"],
        "exit_contract": payload["exit_contract"],
        "resolved_config": payload["resolved_config"],
        "signals": [{"symbol": s["symbol"], "signal_id": s["signal_id"],
                     "generated_at": s["generated_at"], "direction": s["direction"]}
                    for s in payload["signals"]],
        "orders": sorted((norm_order(o) for o in payload["orders"]),
                         key=lambda o: (o["created_at"], o["symbol"], o["side"])),
        "fills": sorted((norm_fill(f) for f in payload["fills"]),
                        key=lambda f: (f["time"], f["symbol"], f["side"])),
        "rejections": sorted(({"symbol": r["symbol"], "reason": r["reason"], "at": r["at"]}
                              for r in payload["rejections"]),
                             key=lambda r: (r["at"], r["symbol"], r["reason"])),
        "cash": round(float(payload["cash"]), 6),
        "final_equity": round(float(payload["final_equity"]), 6),
        "equity_curve": [{"date": row["date"], "cash": round(row["cash"], 6),
                          "market_value": round(row["market_value"], 6),
                          "equity": round(row["equity"], 6)}
                         for row in payload["equity_curve"]],
    }
