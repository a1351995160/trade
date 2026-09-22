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
    """一个指标请求：id + 可选版本 + 参数 + 可选**实例别名**。

    ``alias`` 用于在同一请求里引用同一指标的多个实例（例如 ``ma_fast`` / ``ma_slow``）。
    未给 alias 时，实例身份由 ``indicator_id@version#params`` 决定。
    """

    indicator_id: str
    version: Optional[str] = None
    params: Mapping[str, Any] = field(default_factory=dict)
    alias: Optional[str] = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "IndicatorRequest":
        if isinstance(payload, str):
            return cls(indicator_id=payload)
        if not isinstance(payload, Mapping):
            raise BehaviorRequestError("INVALID_INDICATOR_REQUEST")
        known = {"indicator_id", "version", "params", "alias"}
        unknown = sorted(set(payload) - known)
        if unknown:
            raise BehaviorRequestError(f"UNKNOWN_INDICATOR_REQUEST_FIELD:{','.join(unknown)}")
        if not payload.get("indicator_id"):
            raise BehaviorRequestError("INDICATOR_ID_REQUIRED")
        return cls(indicator_id=str(payload["indicator_id"]),
                   version=payload.get("version"),
                   params=dict(payload.get("params") or {}),
                   alias=(str(payload["alias"]) if payload.get("alias") else None))


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
        known = set(cls.__dataclass_fields__)
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
    """对每个证券计算全部请求指标。

    返回 ``{symbol: {instance_key: result}}``。

    实例身份 = ``alias``（若给）否则 ``indicator_id@version#params``。
    **同一请求内重复或冲突的实例身份必须先拒绝**，不能静默覆盖：
    ``MA(5)`` 与 ``MA(20)`` 是两个实例；仅别名不同但解析到同一 canonical
    id 且参数相同也不构成独立实例。
    """
    if not requests:
        raise BehaviorRequestError("EMPTY_INDICATOR_LIST")

    # 先解析全部请求并检测重复/冲突身份。
    resolved_requests: List[Tuple[str, IndicatorRequest, IndicatorResult]] = []
    seen: Dict[str, Tuple[str, str]] = {}
    for request in requests:
        spec, key = registry.resolve_instance(
            request.indicator_id, version=request.version, params=request.params)
        instance_key = request.alias or f"{spec.indicator_id}@{spec.version}#{key.as_string()}"
        fingerprint = key.as_string()
        if instance_key in seen:
            previous = seen[instance_key]
            raise BehaviorRequestError(
                f"DUPLICATE_INDICATOR_INSTANCE:{instance_key}:"
                f"already={previous[0]}@{previous[1]}:conflict={spec.indicator_id}@{spec.version}")
        # 同一 canonical 实例被声明两次（即使 alias 不同）也不允许：
        # 那会伪装成两个独立实例。
        for existing_key, (existing_id, existing_fp) in seen.items():
            if existing_fp == fingerprint:
                raise BehaviorRequestError(
                    f"CONFLICTING_INDICATOR_INSTANCE:{existing_key}:{instance_key}:"
                    f"same_canonical_instance:{spec.indicator_id}@{spec.version}")
        seen[instance_key] = (spec.indicator_id, fingerprint)
        resolved_requests.append((instance_key, request, spec))

    output: Dict[str, Dict[str, IndicatorResult]] = {}
    for symbol in sorted(frames):
        frame = frames[symbol]
        results: Dict[str, IndicatorResult] = {}
        for instance_key, request, spec in resolved_requests:
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
            results[instance_key] = result
        output[symbol] = results
    return output


def instance_lookup(results: Mapping[str, IndicatorResult]) -> Dict[str, str]:
    """构造 ``indicator_id -> instance_key`` 的反查表。

    当同一 indicator_id 有多个实例时，**不允许**按 id 含糊引用：
    调用方必须用 alias 或完整实例键。
    """
    by_id: Dict[str, List[str]] = {}
    for instance_key, result in results.items():
        by_id.setdefault(result.indicator_id, []).append(instance_key)
    lookup: Dict[str, str] = {}
    for indicator_id, keys in by_id.items():
        if len(keys) == 1:
            lookup[indicator_id] = keys[0]
        else:
            for key in keys:
                lookup[key] = key
    return lookup


def build_condition_context(
    frame: pd.DataFrame,
    results: Mapping[str, IndicatorResult],
    index: pd.Index,
    *,
    cross_section: Optional[pd.DataFrame] = None,
) -> ConditionContext:
    """构造求值上下文。

    输出键同时提供两种形式：

    - ``f"{instance_key}.{output}"``（**精确**，推荐，多实例时必须用它）；
    - ``f"{indicator_id}.{output}"``（**仅在该 indicator_id 只有单一实例时**提供）。

    当同一 id 有多个实例时不再提供含糊的 id 形式，避免"引用到了另一个实例"。
    """
    values: Dict[str, pd.Series] = {}
    ready: Dict[str, pd.Series] = {}
    counts: Dict[str, int] = {}
    for result in results.values():
        counts[result.indicator_id] = counts.get(result.indicator_id, 0) + 1
    for instance_key, result in results.items():
        for name in result.output_names:
            series = result.output(name).reindex(index)
            ready_series = result.ready().reindex(index).fillna(False).astype(bool)
            values[f"{instance_key}.{name}"] = series
            ready[f"{instance_key}.{name}"] = ready_series
            if counts[result.indicator_id] == 1:
                values[f"{result.indicator_id}.{name}"] = series
                ready[f"{result.indicator_id}.{name}"] = ready_series
    fields: Dict[str, pd.Series] = {}
    for name in ("close", "high", "low", "open", "volume", "amount"):
        if name in frame.columns:
            fields[name] = pd.to_numeric(frame[name], errors="coerce").reindex(index)
    return ConditionContext(indicator_values=values, fields=fields, index=index,
                            ready=ready, cross_section=cross_section)


def build_cross_section(
    symbol_results: Mapping[str, Mapping[str, IndicatorResult]],
    session: int,
) -> Optional[pd.DataFrame]:
    """为**单个 session** 构造合法截面。

    行 = 证券，列 = ``f"{instance_key}.{output}"``；只包含在该 session
    已 ready 且有限的成员值。不合格成员保留 NaN，由条件层判为 UNKNOWN。
    当某 indicator_id 只有单一实例时，同时提供 ``f"{id}.{output}"`` 形式，
    使条件可按指标 id 引用（与单证券上下文一致）。
    """
    # 实例计数必须按**单个证券**统计（同一 id 是否在该证券上有多个实例），
    # 不能把所有证券的实例数累加，否则单实例指标会被误判为多实例。
    counts: Dict[str, int] = {}
    for results in symbol_results.values():
        per_symbol: Dict[str, int] = {}
        for result in results.values():
            per_symbol[result.indicator_id] = per_symbol.get(result.indicator_id, 0) + 1
        for indicator_id, value in per_symbol.items():
            counts[indicator_id] = max(counts.get(indicator_id, 0), value)
    columns: Dict[str, Dict[str, float]] = {}
    for symbol, results in symbol_results.items():
        for instance_key, result in results.items():
            for name in result.output_names:
                series = result.output(name)
                if session not in series.index:
                    continue
                value = series.loc[session]
                is_ready = bool(result.ready().loc[session]) if session in result.ready().index else False
                # 只有**有限**数值才是合法成员；NaN / Inf 一律不合格。
                finite = bool(np.isfinite(float(value)))
                resolved = float(value) if (is_ready and finite) else np.nan
                keys = [f"{instance_key}.{name}"]
                if counts[result.indicator_id] == 1:
                    keys.append(f"{result.indicator_id}.{name}")
                for key in keys:
                    columns.setdefault(key, {})[symbol] = resolved
    if not columns:
        return None
    return pd.DataFrame(columns).sort_index()


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

    # 表达式里的引用解析为精确实例键（多实例引用必须精确），并核验显式版本。
    alias_map = _alias_map(indicator_results, indicator_requests)
    version_map = _instance_versions(indicator_results)
    entry_condition = _bind_expression_references(entry_condition, alias_map, version_map)

    prepared_frames: Dict[str, pd.DataFrame] = {}
    for symbol in sorted(frames):
        frame = frames[symbol].sort_values("date").reset_index(drop=True)
        index = pd.Index(frame["date"].astype(int).to_numpy(), name="date")
        prepared_frames[symbol] = frame.set_index(index)

    # 逐 session 评估：每个 session 只暴露该日及之前的已完成数据，
    # 截面只包含该日合法成员（成员资格 + ready + finite + 时间可见性）。
    for symbol in sorted(frames):
        prepared = prepared_frames[symbol]
        index = prepared.index
        symbol_trace: Dict[str, Any] = {
            "indicator_outputs": {
                instance_key: list(result.output_names)
                for instance_key, result in indicator_results[symbol].items()
            },
            "condition_true": 0,
            "condition_false": 0,
            "condition_unknown": 0,
        }
        for day in index:
            day_int = int(day)
            if day_int not in calendar_set:
                continue
            visible = index[index <= day]
            cross_section = build_cross_section(indicator_results, day_int)
            context = build_condition_context(
                prepared.loc[visible], indicator_results[symbol], visible,
                cross_section=cross_section)
            condition = evaluator.evaluate(entry_condition, context)
            # 截面算子返回以**证券**为索引的结果；按当前证券取值。
            # 时序算子返回以**时间**为索引的结果；按当前 session 取值。
            if symbol in condition.index:
                value = condition.loc[symbol]
            elif day in condition.index:
                value = condition.loc[day]
            else:
                value = np.nan
            if not (isinstance(value, float) and np.isnan(value)) and value > 0:
                symbol_trace["condition_true"] += 1
                signals.append(Signal(
                    strategy_id=strategy_id,
                    signal_id=f"{strategy_id}:{symbol}:{day_int}",
                    symbol=symbol,
                    generated_at=_timestamp(day_int, 15, 0),
                    direction=Side.BUY,
                    signal_type="EXPRESSION_ENTRY",
                    execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
                    score=0.0,
                ))
            elif isinstance(value, float) and np.isnan(value):
                symbol_trace["condition_unknown"] += 1
            else:
                symbol_trace["condition_false"] += 1
        trace[symbol] = symbol_trace

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

    # 混合截面/时序条件在运行前拒绝（能力矩阵同步标为不支持）。
    _assert_expression_domain_supported(entry_condition, "entry_condition")
    _assert_expression_domain_supported(exit_condition, "exit_condition")
    _assert_expression_domain_supported(reverse_condition, "reverse_signal_condition")

    indicator_requests = [IndicatorRequest.from_mapping(item) for item in request.indicators]
    aliases = {item.alias for item in indicator_requests if item.alias}
    # 条件里引用的指标若未显式请求（且不是已声明的别名），自动补上。
    required = _referenced_indicators(entry_condition)
    for extra in (exit_condition, reverse_condition):
        required |= _referenced_indicators(extra)
    declared = {item.indicator_id for item in indicator_requests}
    for indicator_id in sorted(required - declared - aliases):
        indicator_requests.append(IndicatorRequest(indicator_id=indicator_id))
    if not indicator_requests:
        raise BehaviorRequestError("NO_INDICATORS_FOR_CONDITION")

    signals, condition_trace, indicator_results = build_entry_signals_v2(
        frames, calendar, indicator_requests, entry_condition,
        registry=reg, strategy_id=request.strategy_id,
    )

    # 把表达式里的引用解析为精确实例键，并核验显式版本（在拿到实例列表之后）。
    alias_map = _alias_map(indicator_results, indicator_requests)
    version_map = _instance_versions(indicator_results)
    exit_condition = _bind_expression_references(exit_condition, alias_map, version_map)
    reverse_condition = _bind_expression_references(reverse_condition, alias_map, version_map)

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
    # 仅当**确实**需要指标条件退出/反向信号时才构建，避免为固定持有/成本止损等
    # 基础退出引入不必要的指标上下文限制。
    needs_condition_context = exit_condition is not None or reverse_condition is not None
    context_by_session: Dict[int, Dict[str, ConditionContext]] = {}
    if needs_condition_context:
        for day in calendar:
            # 每个 session 的合法截面：成员资格、ready、finite 与时间可见性
            # 均在该 session 的视图上绑定，绝不用全样本或未来成员。
            cross_section = build_cross_section(indicator_results, int(day))
            for symbol in request.symbols:
                frame = frames[symbol].sort_values("date").reset_index(drop=True)
                full_index = pd.Index(frame["date"].astype(int).to_numpy(), name="date")
                prepared = frame.set_index(full_index)
                visible = full_index[full_index <= int(day)]
                context_by_session.setdefault(int(day), {})[symbol] = build_condition_context(
                    prepared.loc[visible], indicator_results[symbol], visible,
                    cross_section=cross_section)

    def condition_context_fn(day: int):
        """返回 **按证券** 的上下文映射；多证券各自独立，绝不共用一个上下文。"""
        per_symbol = context_by_session.get(int(day))
        if not per_symbol:
            return None
        return dict(per_symbol)

    atr_bindings = _resolve_atr_dependencies(
        reg, request, rules, indicator_results, indicator_requests)
    _assert_atr_dependencies_satisfied(rules, request, atr_bindings)
    atr_series = _atr_series_for(atr_bindings, indicator_results, request.symbols)

    def on_fill_hook(lot) -> None:
        """持仓创建时按**每条规则各自的窗口**冻结入场 ATR 锚。

        严格取早于入场日的最近可用值；不同窗口各自冻结，互不覆盖。
        绑定身份一并登记，使 trace 能溯源到具体 ATR 实例。
        """
        for label, per_symbol in atr_series.items():
            frozen = evaluator.freeze_entry_anchor(
                lot.lot_id, lot.symbol, int(lot.entry_session or 0),
                per_symbol, rule=label, binding=atr_bindings.get(label))
            if frozen is None:
                evaluator._blocked(lot, int(lot.entry_session or 0), label,
                                   "NO_PRIOR_AVAILABLE_ATR")

    engine.fill_hook = on_fill_hook
    exit_callback = (daily_exit_fn_v2(evaluator, store, calendar,
                                      atr_series=atr_series,
                                      condition_context_fn=(condition_context_fn
                                                            if needs_condition_context else None))
                     if evaluator.rules.enabled else None)
    result = engine.run(exit_fn=exit_callback)

    # 规则已启用却无法执行（例如 ATR 锚未冻结）时，不得把该运行报成正常完成。
    if evaluator.blocked_lots:
        reasons = sorted({item["blocked_reason"] for item in evaluator.blocked_lots})
        raise BehaviorRequestError(
            "EXIT_RULE_NOT_EXECUTABLE:%s:affected_lots=%d"
            % (",".join(reasons), len(evaluator.blocked_lots)))

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

    return BehaviorResultV2({
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
            "atr_bindings": {label: binding.to_dict() for label, binding in atr_bindings.items()},
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
    })


def _resolve_atr_dependencies(reg, request, rules, indicator_results, indicator_requests):
    """从规则建立 ATR 依赖：按 atr_window、版本、价格尺度分别绑定。

    每条规则（距离/跟踪）各自持有**实际使用的序列与绑定身份**；
    窗口不同时必须解析到不同实例，绝不共用一条未核验的 ATR。

    窗口取自**本次计算实际使用的参数**（``IndicatorResult.resolved_params``），
    不从 ``spec.params``（契约默认值）重新推断——否则无 alias 的
    ``ATR(window=7)`` 会被误判成默认的 14。
    """
    from .daily_exit_v2 import AtrBinding

    needed: Dict[str, Any] = {}
    for label, spec in (("atr_distance", rules.atr_distance), ("atr_trailing", rules.atr_trailing)):
        if spec is None:
            continue
        needed[label] = spec
    if not needed:
        return {}

    # 每个实例的**实际**窗口（来自计算结果，不来自契约默认值）。
    actual_window: Dict[str, int] = {}
    for symbol in request.symbols:
        for instance_key, result in indicator_results[symbol].items():
            if result.indicator_id != "ATR":
                continue
            actual_window.setdefault(
                instance_key, int(result.param("window", result.param("atr_window", 14))))

    # 每个窗口对应的实例集合（按证券并集，保证多证券一致）。
    by_window: Dict[int, List[str]] = {}
    for instance_key, window in actual_window.items():
        by_window.setdefault(window, []).append(instance_key)

    bindings: Dict[str, AtrBinding] = {}
    for label, spec in needed.items():
        window = int(spec.atr_window)
        candidates = sorted(by_window.get(window, []))
        if not candidates:
            available = ", ".join(f"{key}(window={actual_window[key]})"
                                  for key in sorted(actual_window)) or "无"
            raise BehaviorRequestError(
                f"ATR_DEPENDENCY_NOT_DECLARED:{label}:atr_window={window}:"
                f"已请求的 ATR 实例：{available}；请显式请求 window={window} 的 ATR")
        instance_key = candidates[0]
        sample = None
        for symbol in request.symbols:
            result = indicator_results[symbol].get(instance_key)
            if result is None:
                raise BehaviorRequestError(f"ATR_INSTANCE_MISSING:{symbol}:{instance_key}")
            sample = result
        bindings[label] = AtrBinding(
            instance_key=instance_key, atr_window=window, version=sample.version,
            price_mode=sample.spec.price_mode,
            available_at_rule=sample.spec.available_at_rule,
        )
    return bindings


def _atr_series_for(bindings: Mapping[str, Any], indicator_results, symbols) -> Dict[str, Dict[str, pd.Series]]:
    """``规则标签 -> {symbol: 实际 ATR 序列}``。

    两条规则各自保留自己的序列，不再互相覆盖。
    """
    series: Dict[str, Dict[str, pd.Series]] = {}
    for label, binding in bindings.items():
        per_symbol: Dict[str, pd.Series] = {}
        for symbol in symbols:
            result = indicator_results[symbol].get(binding.instance_key)
            if result is None:
                raise BehaviorRequestError(
                    f"ATR_INSTANCE_MISSING:{symbol}:{binding.instance_key}")
            per_symbol[symbol] = result.output("atr")
        series[label] = per_symbol
    return series


def _assert_atr_dependencies_satisfied(rules, request, bindings) -> None:
    """启用 ATR 规则却没有可用 ATR 依赖时，明确拒绝运行。"""
    for label, spec in (("atr_distance", rules.atr_distance), ("atr_trailing", rules.atr_trailing)):
        if spec is None:
            continue
        if label not in bindings:
            raise BehaviorRequestError(f"ATR_DEPENDENCY_UNRESOLVED:{label}")


def _alias_map(indicator_results: Mapping[str, Mapping[str, IndicatorResult]],
               requests: Sequence[IndicatorRequest]) -> Dict[str, str]:
    """构造 ``引用名 -> instance_key`` 映射。

    允许的引用名：
    - 声明的 ``alias``；
    - 完整实例键；
    - **仅当该 indicator_id 只有单一实例时**的裸 id（兼容旧配置）。

    同一 id 有多个实例时，裸 id **不**进入映射，调用方必须用 alias 或完整实例键；
    含糊引用在运行前被拒绝，而不是悄悄指向某一个实例。
    """
    mapping: Dict[str, str] = {}
    for results in indicator_results.values():
        counts: Dict[str, int] = {}
        for result in results.values():
            counts[result.indicator_id] = counts.get(result.indicator_id, 0) + 1
        for instance_key, result in results.items():
            mapping.setdefault(instance_key, instance_key)
            if counts[result.indicator_id] == 1:
                mapping.setdefault(result.indicator_id, instance_key)
    for request in requests:
        if not request.alias:
            continue
        for results in indicator_results.values():
            for instance_key, result in results.items():
                if instance_key == request.alias:
                    mapping[request.alias] = instance_key
    return mapping


CROSS_SECTIONAL_OPS = frozenset({"rank", "percentile", "top_n"})


def _expression_domain(node: Optional[Expr]) -> Optional[str]:
    """判定表达式的结果索引域：``symbol``（截面）/ ``session``（时序）/ None（混合）。

    混合（同一逻辑节点下同时出现截面与时序操作数）在当前求值器里
    无法对齐，必须在**运行前**拒绝，而不是运行完成但静默不退出。
    """
    if node is None or not isinstance(node, Expr):
        return None
    if node.op in CROSS_SECTIONAL_OPS:
        return "symbol"
    domains = {_expression_domain(arg) for arg in node.args if isinstance(arg, Expr)}
    domains.discard(None)
    if len(domains) > 1:
        return None
    return next(iter(domains)) if domains else "session"


def _assert_expression_domain_supported(node: Optional[Expr], label: str) -> None:
    """混合截面/时序表达式在运行前显式拒绝。"""
    if node is None:
        return
    has_cross = _contains_cross_sectional(node)
    has_series = _contains_series(node)
    if has_cross and has_series:
        raise BehaviorRequestError(
            f"MIXED_CROSS_SECTIONAL_AND_SERIES_CONDITION_NOT_SUPPORTED:{label}")


def _contains_cross_sectional(node: Optional[Expr]) -> bool:
    if node is None or not isinstance(node, Expr):
        return False
    if node.op in CROSS_SECTIONAL_OPS:
        return True
    return any(_contains_cross_sectional(arg) for arg in node.args if isinstance(arg, Expr))


def _contains_series(node: Optional[Expr]) -> bool:
    if node is None or not isinstance(node, Expr):
        return False
    if node.op in CROSS_SECTIONAL_OPS:
        return False
    if node.op in {"indicator", "field", "const"}:
        return True
    return any(_contains_series(arg) for arg in node.args if isinstance(arg, Expr))


def _instance_versions(
    indicator_results: Mapping[str, Mapping[str, IndicatorResult]],
) -> Dict[str, str]:
    """``instance_key -> 解析后的 canonical version``（用于表达式版本核验）。"""
    versions: Dict[str, str] = {}
    for results in indicator_results.values():
        for instance_key, result in results.items():
            versions.setdefault(instance_key, result.version)
    return versions


def _bind_expression_references(node: Optional[Expr],
                                aliases: Mapping[str, str],
                                versions: Mapping[str, str]) -> Optional[Expr]:
    """把表达式里的 ``indicator`` 引用解析为**精确实例键**，并核验版本。

    - 引用名必须在映射中（否则 UNKNOWN_INDICATOR_REFERENCE，运行前拒绝）；
    - 表达式里显式给出的 ``version`` 必须与该实例解析后的 version 一致，
      否则 VERSION_MISMATCH（运行前拒绝），不得静默消费其他版本的结果。
    """
    if node is None or not isinstance(node, Expr):
        return node
    args = tuple(_bind_expression_references(arg, aliases, versions) for arg in node.args)
    params = dict(node.params)
    if node.op == "indicator" and args:
        reference = str(args[0])
        if reference not in aliases:
            raise BehaviorRequestError(f"UNKNOWN_INDICATOR_REFERENCE:{reference}")
        instance_key = aliases[reference]
        declared_version = params.pop("version", None)
        actual_version = versions.get(instance_key)
        if declared_version is not None and str(declared_version) != str(actual_version):
            raise BehaviorRequestError(
                f"VERSION_MISMATCH:{reference}:declared={declared_version}:"
                f"resolved={actual_version}")
        return Expr(node.op, (instance_key,), params)
    return Expr(node.op, args, params)


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


@dataclass(frozen=True)
class BehaviorResultV2:
    """V2 结果对象。

    与 V1 ``BehaviorResultV1`` 同构：由服务内部构造的**类型化**结果，
    而不是把外部传入的 Mapping 直接落盘。这既明确了结果契约，
    也使写入路径不再直接接收未经类型化的外部内容。

    为兼容既有调用方，支持按 key 读取（``result["mode"]``）与 ``dict(result)``。
    """

    payload: Mapping[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)

    def __getitem__(self, key: str) -> Any:
        return self.payload[key]

    def __contains__(self, key: object) -> bool:
        return key in self.payload

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)

    def keys(self):
        return self.payload.keys()

    def items(self):
        return self.payload.items()

    def __iter__(self):
        return iter(self.payload)


def write_result_v2(path: str | Path, result: BehaviorResultV2, *, root: str | Path) -> Path:
    """写出 V2 结果 JSON；``path`` 必须落在调用者显式声明的 ``root`` 内。

    写入放在服务层（与 V1 ``write_result`` 同一模式）：CLI 只传入**已类型化**的
    结果对象与根目录，外部参数不能直接决定任意写入位置。
    """
    target = resolve_within_root(path, root, purpose="RESULT")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str),
                      encoding="utf-8")
    return target


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
