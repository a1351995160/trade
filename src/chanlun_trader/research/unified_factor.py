"""Unified, PIT-safe factor definitions and a small declarative executor.

This module is deliberately separate from the historical ``FactorRegistry``.
The old registry is a research record and must remain immutable during the
unified-library build.  This module provides the new contract used by the
future hypothesis factory: immutable definitions, explicit operator
semantics, as-of cross-sectional operations, and a guarded executor.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .guard import ResearchDataAccessGuard


UNIFIED_SCHEMA_VERSION = "unified-factor-library-v1"
IMPLEMENTATION_STATUSES = {
    "EXECUTABLE",
    "SPEC_ONLY",
    "BLOCKED_PIT",
    "BLOCKED_DATA",
    "BLOCKED_LICENSE",
    "BLOCKED_DUPLICATE",
    "BLOCKED_OPERATOR",
    "DEFERRED",
}
PIT_STATUSES = {"PIT_VERIFIED", "PIT_LIMITED", "PIT_REVIEW_REQUIRED", "PIT_UNKNOWN"}
DATA_STATUSES = {"FULL", "PARTIAL", "DATA_LIMITED", "UNKNOWN"}
PRICE_MODES = {"RAW", "PIT_QFQ", "RETURN_ONLY", "OTHER"}
SOURCE_TYPES = {
    "INTERNAL_EXISTING",
    "INTERNAL_LEGACY",
    "VIBE_TRADING",
    "ACADEMIC_EXTERNAL",
    "DERIVED_INTERNAL",
}


class PITError(ValueError):
    """Raised when an operation cannot prove point-in-time safety."""


class OperatorError(ValueError):
    """Raised when a DSL expression violates the operator contract."""


def _jsonable(value: Any) -> Any:
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def field_node(name: str) -> dict:
    return {"op": "field", "field": name}


def const_node(value: float | int | bool | None) -> dict:
    return {"op": "const", "value": value}


def op_node(name: str, *args: Any, **params: Any) -> dict:
    node = {"op": name, "args": list(args)}
    node.update(params)
    return node


def factor_ref_node(factor_id: str, version: str = "v1") -> dict:
    return {"op": "factor_ref", "factor_id": factor_id, "version": version}


def _normalise_expression(node: Any, ignore_direction: bool = False) -> Any:
    if isinstance(node, Mapping):
        op = node.get("op")
        if ignore_direction and op == "neg" and len(node.get("args", [])) == 1:
            return _normalise_expression(node["args"][0], ignore_direction=True)
        return {
            str(k): _normalise_expression(v, ignore_direction=ignore_direction)
            for k, v in sorted(node.items())
            if k != "args"
        } | {"args": [_normalise_expression(v, ignore_direction=ignore_direction) for v in node.get("args", [])]}
    if isinstance(node, (list, tuple)):
        return [_normalise_expression(v, ignore_direction=ignore_direction) for v in node]
    return _jsonable(node)


def factor_fingerprint(
    canonical_expression: Any,
    required_fields: Sequence[str],
    lookback: int,
    price_mode: str,
    cross_sectional: bool,
    direction_hint: str,
) -> str:
    payload = {
        "expression": _normalise_expression(canonical_expression),
        "required_fields": sorted(set(required_fields)),
        "lookback": int(lookback),
        "price_mode": price_mode,
        "cross_sectional": bool(cross_sectional),
        "direction_hint": direction_hint,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def semantic_fingerprint(expression: Any, required_fields: Sequence[str], lookback: int, price_mode: str, cross_sectional: bool) -> str:
    payload = {
        "expression": _normalise_expression(expression, ignore_direction=True),
        "required_fields": sorted(set(required_fields)),
        "lookback": int(lookback),
        "price_mode": price_mode,
        "cross_sectional": bool(cross_sectional),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def direction_inverse(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    if a.get("factor_fingerprint") == b.get("factor_fingerprint"):
        return False
    a_expr = _normalise_expression(a.get("canonical_formula", a.get("operator_graph", {})), ignore_direction=True)
    b_expr = _normalise_expression(b.get("canonical_formula", b.get("operator_graph", {})), ignore_direction=True)
    if a_expr != b_expr:
        return False
    return a.get("direction_hint") != b.get("direction_hint")


def _collect_ops(node: Any, result: set[str] | None = None) -> set[str]:
    result = result or set()
    if isinstance(node, Mapping):
        if node.get("op"):
            result.add(str(node["op"]))
        for value in node.values():
            _collect_ops(value, result)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _collect_ops(value, result)
    return result


def dependency_ids(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, Mapping):
        if node.get("op") == "factor_ref" and node.get("factor_id"):
            found.append(str(node["factor_id"]))
        for value in node.values():
            found.extend(dependency_ids(value))
    elif isinstance(node, (list, tuple)):
        for value in node:
            found.extend(dependency_ids(value))
    return sorted(set(found))


@dataclass(frozen=True)
class UnifiedFactorDefinition:
    factor_id: str
    version: str
    name: str
    family: str
    theme: list[str]
    description: str
    economic_hypothesis: str
    formula: str
    canonical_formula: Any
    operator_graph: Any
    inputs: list[str]
    required_fields: list[str]
    required_frequency: str
    lookback: int
    min_warmup_bars: int
    cross_sectional: bool
    direction_hint: str
    feature_price_mode: str
    available_at_rule: str
    pit_safe: bool
    pit_evidence: str
    pit_status: str
    data_support_status: str
    data_dependencies: list[str]
    a_share_compatibility: str
    small_capital_relevance: str
    source_type: str
    source_name: str
    source_family: str
    source_id: str
    source_commit: str | None
    license_class: str
    attribution: list[str]
    clean_room_reimplementation: bool
    factor_fingerprint: str
    duplicate_class: str
    implementation_status: str
    test_status: str
    lifecycle_status: str = "DISCOVERED"
    research_status: str = "RESEARCH_ONLY"
    created_at: str = "2026-08-23"
    proxy_definition: str | None = None
    semantic_difference: str | None = None
    is_proxy: bool = False

    def __post_init__(self) -> None:
        if not self.factor_id or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" for ch in self.factor_id):
            raise ValueError(f"invalid factor_id: {self.factor_id!r}")
        if not self.version:
            raise ValueError("factor version is required")
        if self.family not in {
            "TREND", "MOMENTUM", "REVERSAL", "VOLATILITY", "VOLUME", "LIQUIDITY",
            "PRICE_STRUCTURE", "CROSS_SECTIONAL", "MARKET_REGIME", "EVENT", "FUNDAMENTAL",
            "QUALITY", "VALUE", "SIZE", "TECHNICAL", "COMPOSITE",
        }:
            raise ValueError(f"unsupported factor family: {self.family}")
        if self.feature_price_mode not in PRICE_MODES:
            raise ValueError(f"unsupported feature_price_mode: {self.feature_price_mode}")
        if self.implementation_status not in IMPLEMENTATION_STATUSES:
            raise ValueError(f"unsupported implementation_status: {self.implementation_status}")
        if self.pit_status not in PIT_STATUSES:
            raise ValueError(f"unsupported pit_status: {self.pit_status}")
        if self.data_support_status not in DATA_STATUSES:
            raise ValueError(f"unsupported data_support_status: {self.data_support_status}")
        if self.source_type not in SOURCE_TYPES:
            raise ValueError(f"unsupported source_type: {self.source_type}")
        if self.lifecycle_status in {"PROMISING", "ROBUST"}:
            raise ValueError("Phase 1 cannot promote a factor to PROMISING or ROBUST")
        if self.source_type == "VIBE_TRADING":
            if self.source_commit != "e86eb283b409f5521a39b855cef23aad4eb542a5":
                raise ValueError("Vibe provenance commit must be retained")
            if not self.clean_room_reimplementation:
                raise ValueError("external runtime implementations must be clean-room")
        if self.implementation_status == "EXECUTABLE" and self.data_support_status != "FULL":
            raise ValueError("DATA_PARTIAL/DATA_LIMITED factors cannot be EXECUTABLE")
        if self.implementation_status == "EXECUTABLE" and self.pit_status != "PIT_VERIFIED":
            raise ValueError("PIT-limited factors cannot be EXECUTABLE")

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


class UnifiedFactorRegistry:
    def __init__(self, definitions: Iterable[UnifiedFactorDefinition] = ()):
        self._items: dict[tuple[str, str], UnifiedFactorDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: UnifiedFactorDefinition) -> None:
        key = (definition.factor_id, definition.version)
        previous = self._items.get(key)
        if previous is not None and previous.to_dict() != definition.to_dict():
            raise ValueError(f"factor version is immutable: {key}")
        self._items[key] = definition

    def get(self, factor_id: str, version: str = "v1") -> UnifiedFactorDefinition | None:
        return self._items.get((factor_id, version))

    def items(self) -> list[UnifiedFactorDefinition]:
        return list(self._items.values())

    def executable(self) -> list[UnifiedFactorDefinition]:
        return [item for item in self.items() if item.implementation_status == "EXECUTABLE"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": UNIFIED_SCHEMA_VERSION,
            "taxonomy_families": [
                "TREND", "MOMENTUM", "REVERSAL", "VOLATILITY", "VOLUME", "LIQUIDITY",
                "PRICE_STRUCTURE", "CROSS_SECTIONAL", "MARKET_REGIME", "EVENT", "FUNDAMENTAL",
                "QUALITY", "VALUE", "SIZE", "TECHNICAL", "COMPOSITE",
            ],
            "factors": [item.to_dict() for item in sorted(self.items(), key=lambda x: (x.factor_id, x.version))],
        }

    def write(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return output

    @classmethod
    def read(cls, path: str | Path) -> "UnifiedFactorRegistry":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(UnifiedFactorDefinition(**item) for item in payload.get("factors", []))


@dataclass(frozen=True)
class OperatorSpec:
    name: str
    category: str
    input_semantics: str
    output_semantics: str
    window_semantics: str
    timestamp_semantics: str
    nan_semantics: str
    minimum_samples: str
    pit_safety: str
    cross_sectional_semantics: str
    supports_negative_lag: bool = False
    supports_centered_window: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonical_operator_registry() -> list[OperatorSpec]:
    names = {
        "add": ("elementwise", "two aligned series", "elementwise sum"),
        "sub": ("elementwise", "two aligned series", "elementwise difference"),
        "mul": ("elementwise", "two aligned series", "elementwise product"),
        "neg": ("unary", "numeric series", "negated numeric series"),
        "abs": ("unary", "numeric series", "numeric series"),
        "sign": ("unary", "numeric series", "-1/0/1 series"),
        "log": ("unary", "positive numeric series", "numeric series; nonpositive -> NaN"),
        "sqrt": ("unary", "nonnegative numeric series", "numeric series; negative -> NaN"),
        "power": ("elementwise", "series and exponent", "numeric series"),
        "signed_power": ("elementwise", "series and exponent", "signed numeric series"),
        "safe_div": ("elementwise", "numerator and denominator", "numeric series; zero denominator -> NaN"),
        "shift": ("time-series", "one symbol series", "lagged series"),
        "delta": ("time-series", "one symbol series", "difference series"),
        "pct_change": ("time-series", "one symbol series", "return series"),
        "log_return": ("time-series", "one symbol series", "log return series"),
        "ts_sum": ("rolling", "one symbol series", "trailing sum"),
        "ts_mean": ("rolling", "one symbol series", "trailing mean"),
        "ts_std": ("rolling", "one symbol series", "trailing sample std"),
        "ts_min": ("rolling", "one symbol series", "trailing minimum"),
        "ts_max": ("rolling", "one symbol series", "trailing maximum"),
        "ts_rank": ("rolling", "one symbol series", "trailing percentile rank of latest value"),
        "ts_argmax": ("rolling", "one symbol series", "zero-based trailing argmax"),
        "ts_argmin": ("rolling", "one symbol series", "zero-based trailing argmin"),
        "correlation": ("rolling", "two aligned symbol series", "trailing Pearson correlation"),
        "covariance": ("rolling", "two aligned symbol series", "trailing sample covariance"),
        "decay_linear": ("rolling", "one symbol series", "weighted trailing mean"),
        "ema": ("rolling", "one symbol series", "trailing EMA"),
        "wma": ("rolling", "one symbol series", "trailing weighted mean"),
        "rank": ("cross-sectional", "AsOfCrossSection series", "cross-sectional percentile rank"),
        "zscore": ("cross-sectional", "AsOfCrossSection series", "cross-sectional z-score"),
        "scale": ("cross-sectional", "AsOfCrossSection series", "cross-sectional L1 scale"),
        "where": ("conditional", "condition, true, false", "elementwise selected series"),
        "min": ("elementwise", "two aligned series", "elementwise minimum"),
        "max": ("elementwise", "two aligned series", "elementwise maximum"),
        "gt": ("comparison", "two aligned series", "boolean series"),
        "lt": ("comparison", "two aligned series", "boolean series"),
        "eq": ("comparison", "two aligned series", "boolean series"),
        "count": ("rolling", "one symbol series", "trailing non-NaN count"),
        # Public aliases retained so external formulas can be normalized
        # without changing their economic meaning.
        "delay": ("time-series", "one symbol series", "lagged series; alias of shift"),
        "lag": ("time-series", "one symbol series", "lagged series; alias of shift"),
        "rolling_sum": ("rolling", "one symbol series", "trailing sum; alias of ts_sum"),
        "rolling_mean": ("rolling", "one symbol series", "trailing mean; alias of ts_mean"),
        "rolling_std": ("rolling", "one symbol series", "trailing std; alias of ts_std"),
        "rolling_min": ("rolling", "one symbol series", "trailing min; alias of ts_min"),
        "rolling_max": ("rolling", "one symbol series", "trailing max; alias of ts_max"),
        "rolling_rank": ("rolling", "one symbol series", "trailing rank; alias of ts_rank"),
        "rolling_corr": ("rolling", "two aligned symbol series", "trailing correlation; alias of correlation"),
        "rolling_cov": ("rolling", "two aligned symbol series", "trailing covariance; alias of covariance"),
        "argmax": ("rolling", "one symbol series", "trailing argmax; alias of ts_argmax"),
        "argmin": ("rolling", "one symbol series", "trailing argmin; alias of ts_argmin"),
        "condition": ("conditional", "condition, true, false", "elementwise selected series; alias of where"),
        "winsorize": ("unary", "numeric series", "series clipped to explicit bounds"),
    }
    output: list[OperatorSpec] = []
    for name, (category, inputs, outputs) in names.items():
        is_xs = category == "cross-sectional"
        output.append(
            OperatorSpec(
                name=name,
                category=category,
                input_semantics=inputs,
                output_semantics=outputs,
                window_semantics=("No window" if category not in {"rolling", "time-series"} else "Positive trailing right-aligned window; center=false only"),
                timestamp_semantics=("Same decision timestamp; no future rows" if not is_xs else "Universe exactly as-of each decision timestamp"),
                nan_semantics=("NaN propagates; warmup remains NaN" if category != "cross-sectional" else "Exclude NaN from group; insufficient dispersion -> NaN"),
                minimum_samples=("1 for elementwise; explicit min_periods for rolling" if category != "rolling" else "Explicit min_periods, default window"),
                pit_safety=("Reject negative lag and centered windows; trailing only" if category != "cross-sectional" else "Fail closed without PIT universe"),
                cross_sectional_semantics=("Not cross-sectional" if not is_xs else "AsOfCrossSection only; never full-sample rank"),
            )
        )
    return output


class OperatorRegistry:
    """Immutable metadata view over the canonical operator contract."""

    def __init__(self, operators: Iterable[OperatorSpec] | None = None):
        self._items = {item.name: item for item in (operators or canonical_operator_registry())}

    def get(self, name: str) -> OperatorSpec | None:
        return self._items.get(name)

    def items(self) -> list[OperatorSpec]:
        return [self._items[name] for name in sorted(self._items)]

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "operator-registry-v1", "operators": [item.to_dict() for item in self.items()]}


@dataclass
class AsOfCrossSection:
    decision_time: int
    symbols_available_as_of: Mapping[int, Sequence[str]] | Callable[[int], Sequence[str]]
    universe_version: str

    def _members(self, timestamp: int) -> set[str]:
        if timestamp > self.decision_time:
            raise PITError("cross-sectional operation received a future timestamp")
        if callable(self.symbols_available_as_of):
            members = self.symbols_available_as_of(timestamp)
        else:
            if timestamp not in self.symbols_available_as_of:
                raise PITError(f"PIT universe is missing for {timestamp}")
            members = self.symbols_available_as_of[timestamp]
        return {str(symbol) for symbol in members}

    def apply(self, series: pd.Series, operation: str) -> pd.Series:
        if not isinstance(series.index, pd.MultiIndex) or series.index.names != ["timestamp", "symbol"]:
            raise PITError("AsOfCrossSection requires a (timestamp, symbol) index")
        out = pd.Series(np.nan, index=series.index, dtype=float)
        for timestamp, group in series.groupby(level="timestamp", sort=False):
            timestamp_int = int(timestamp)
            members = self._members(timestamp_int)
            mask = group.index.get_level_values("symbol").isin(members)
            eligible = group[mask].astype(float)
            if eligible.empty:
                continue
            if operation == "rank":
                values = eligible.rank(method="average", pct=True)
            elif operation == "zscore":
                mean = eligible.mean()
                std = eligible.std(ddof=0)
                values = (eligible - mean) / std if pd.notna(std) and std > 0 else pd.Series(np.nan, index=eligible.index)
            elif operation == "scale":
                denom = eligible.abs().sum()
                values = eligible / denom if pd.notna(denom) and denom > 0 else pd.Series(np.nan, index=eligible.index)
            else:
                raise OperatorError(f"unknown cross-sectional operation: {operation}")
            out.loc[values.index] = values
        return out


@dataclass
class AsOfDataView:
    frame: pd.DataFrame
    data_manifest: str
    universe_version: str
    universe_by_date: Mapping[int, Sequence[str]] | None = None
    as_of: int | None = None

    def panel(self) -> pd.DataFrame:
        frame = self.frame.copy()
        if "timestamp" not in frame.columns and "date" in frame.columns:
            frame = frame.rename(columns={"date": "timestamp"})
        for col in ("timestamp", "symbol"):
            if col not in frame.columns:
                raise ValueError(f"AsOfDataView missing {col}")
        frame["timestamp"] = frame["timestamp"].astype(int)
        if frame["timestamp"].duplicated(keep=False).any() and frame[["timestamp", "symbol"]].duplicated().any():
            raise ValueError("AsOfDataView has duplicate symbol/timestamp rows")
        frame = frame.sort_values(["timestamp", "symbol"]).set_index(["timestamp", "symbol"])
        return frame

    def validate(self, guard: ResearchDataAccessGuard, required_fields: Sequence[str], as_of: int) -> pd.DataFrame:
        panel = self.panel()
        if panel.empty:
            raise ValueError("AsOfDataView is empty")
        guard.check_range(int(panel.index.get_level_values("timestamp").min()), int(as_of), "unified factor data")
        if int(panel.index.get_level_values("timestamp").max()) > int(as_of):
            raise PITError("data view contains rows after as_of")
        missing = sorted(set(required_fields) - set(panel.columns))
        if missing:
            raise ValueError(f"missing required factor fields: {missing}")
        return panel


@dataclass
class _EvalContext:
    panel: pd.DataFrame
    cross_section: AsOfCrossSection | None
    registry: UnifiedFactorRegistry | None
    cache: dict[str, pd.Series] = field(default_factory=dict)


def _as_series(value: Any, index: pd.MultiIndex) -> pd.Series:
    if isinstance(value, pd.Series):
        return value.reindex(index)
    return pd.Series(value, index=index, dtype=float)


def _group_transform(series: pd.Series, fn: Callable[[pd.Series], pd.Series | np.ndarray]) -> pd.Series:
    pieces = []
    for symbol, group in series.groupby(level="symbol", sort=False):
        timestamp_index = group.index.get_level_values("timestamp")
        values = fn(pd.Series(group.to_numpy(dtype=float), index=timestamp_index))
        values = pd.Series(values, index=timestamp_index, dtype=float)
        pieces.append(pd.Series(values.to_numpy(), index=pd.MultiIndex.from_arrays([timestamp_index, [symbol] * len(timestamp_index)], names=["timestamp", "symbol"])))
    return pd.concat(pieces).reindex(series.index)


def _rolling(series: pd.Series, window: int, min_periods: int, method: str) -> pd.Series:
    if window <= 0 or min_periods <= 0 or min_periods > window:
        raise OperatorError("rolling window/min_periods must satisfy 0 < min_periods <= window")
    if method == "rank":
        fn = lambda s: s.rolling(window, min_periods=min_periods).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=True)
    elif method == "argmax":
        fn = lambda s: s.rolling(window, min_periods=min_periods).apply(lambda x: float(np.argmax(x)), raw=True)
    elif method == "argmin":
        fn = lambda s: s.rolling(window, min_periods=min_periods).apply(lambda x: float(np.argmin(x)), raw=True)
    elif method == "count":
        fn = lambda s: s.rolling(window, min_periods=1).count().where(s.rolling(window, min_periods=1).count() >= min_periods)
    else:
        fn = lambda s: getattr(s.rolling(window, min_periods=min_periods), method)()
    return _group_transform(series, fn)


def _rolling_pair(a: pd.Series, b: pd.Series, window: int, min_periods: int, method: str) -> pd.Series:
    if window <= 0 or min_periods <= 0 or min_periods > window:
        raise OperatorError("rolling window/min_periods must satisfy 0 < min_periods <= window")
    pieces = []
    joined = pd.concat([a.rename("a"), b.rename("b")], axis=1)
    for symbol, group in joined.groupby(level="symbol", sort=False):
        idx = group.index.get_level_values("timestamp")
        local = group.droplevel("symbol")
        roll = local["a"].rolling(window, min_periods=min_periods)
        values = getattr(roll, method)(local["b"])
        pieces.append(pd.Series(values.to_numpy(), index=pd.MultiIndex.from_arrays([idx, [symbol] * len(idx)], names=["timestamp", "symbol"])))
    return pd.concat(pieces).reindex(a.index)


class FactorExecutor:
    """Evaluate the restricted factor DSL against an as-of panel.

    The executor accepts a ready ``AsOfDataView`` rather than a filesystem
    path.  A production adapter must create that view through
    ``GuardedResearchReader``; this keeps the executor independent from and
    unable to bypass the project data guard.
    """

    def __init__(self, guard: ResearchDataAccessGuard | None = None, registry: UnifiedFactorRegistry | None = None):
        self.guard = guard or ResearchDataAccessGuard()
        self.registry = registry

    def _eval(self, node: Any, context: _EvalContext) -> pd.Series:
        key = canonical_json(node)
        if key in context.cache:
            return context.cache[key]
        index = context.panel.index
        if isinstance(node, (int, float, bool)) or node is None:
            result = _as_series(np.nan if node is None else node, index)
            context.cache[key] = result
            return result
        if not isinstance(node, Mapping):
            raise OperatorError(f"invalid expression node: {node!r}")
        operation = node.get("op")
        if node.get("center") is True:
            raise OperatorError("centered rolling is forbidden")
        operation = {
            "delay": "shift", "lag": "shift", "rolling_sum": "ts_sum", "rolling_mean": "ts_mean",
            "rolling_std": "ts_std", "rolling_min": "ts_min", "rolling_max": "ts_max",
            "rolling_rank": "ts_rank", "rolling_corr": "correlation", "rolling_cov": "covariance",
            "argmax": "ts_argmax", "argmin": "ts_argmin", "condition": "where",
        }.get(operation, operation)
        if operation == "field":
            name = str(node["field"])
            if name not in context.panel.columns:
                raise OperatorError(f"missing field: {name}")
            result = context.panel[name].astype(float)
        elif operation == "const":
            result = _as_series(node.get("value"), index)
        elif operation == "factor_ref":
            if context.registry is None:
                raise OperatorError("factor_ref requires a registry")
            definition = context.registry.get(str(node["factor_id"]), str(node.get("version", "v1")))
            if definition is None or definition.implementation_status != "EXECUTABLE":
                raise OperatorError(f"factor_ref is not executable: {node.get('factor_id')}")
            result = self._eval(definition.operator_graph, context)
        else:
            args = [self._eval(arg, context) for arg in node.get("args", [])]
            if operation in {"shift", "delta", "pct_change", "log_return"}:
                periods = int(node.get("periods", node.get("window", 1)))
                if periods < 0:
                    raise OperatorError("negative lag/future shift is forbidden")
                lagged = _group_transform(args[0], lambda s: s.shift(periods))
                if operation == "shift":
                    result = lagged
                elif operation == "delta":
                    result = args[0] - lagged
                elif operation == "pct_change":
                    result = args[0] / lagged - 1.0
                else:
                    result = np.log(args[0] / lagged)
            elif operation in {"ts_sum", "ts_mean", "ts_std", "ts_min", "ts_max", "ts_rank", "ts_argmax", "ts_argmin", "count"}:
                window = int(node.get("window", 1))
                min_periods = int(node.get("min_periods", window))
                method = {
                    "ts_sum": "sum", "ts_mean": "mean", "ts_std": "std", "ts_min": "min", "ts_max": "max",
                    "ts_rank": "rank", "ts_argmax": "argmax", "ts_argmin": "argmin", "count": "count",
                }[operation]
                result = _rolling(args[0], window, min_periods, method)
            elif operation in {"correlation", "covariance"}:
                result = _rolling_pair(args[0], args[1], int(node["window"]), int(node.get("min_periods", node["window"])), "corr" if operation == "correlation" else "cov")
            elif operation in {"decay_linear", "wma"}:
                window = int(node["window"])
                min_periods = int(node.get("min_periods", window))
                weights = np.arange(1, window + 1, dtype=float)
                fn = lambda s: s.rolling(window, min_periods=min_periods).apply(lambda x: float(np.dot(x, weights[-len(x):]) / weights[-len(x):].sum()), raw=True)
                result = _group_transform(args[0], fn)
            elif operation == "ema":
                window = int(node["window"])
                min_periods = int(node.get("min_periods", window))
                result = _group_transform(args[0], lambda s: s.ewm(span=window, adjust=False, min_periods=min_periods).mean())
            elif operation in {"rank", "zscore", "scale"}:
                if context.cross_section is None:
                    raise PITError("cross-sectional operation requires AsOfCrossSection")
                result = context.cross_section.apply(args[0], operation)
            elif operation == "abs":
                result = args[0].abs()
            elif operation == "neg":
                result = -args[0]
            elif operation == "add":
                result = args[0] + args[1]
            elif operation == "sub":
                result = args[0] - args[1]
            elif operation == "mul":
                result = args[0] * args[1]
            elif operation == "sign":
                result = np.sign(args[0])
            elif operation == "log":
                result = np.log(args[0].where(args[0] > 0))
            elif operation == "sqrt":
                result = np.sqrt(args[0].where(args[0] >= 0))
            elif operation == "winsorize":
                lower = float(node.get("lower", -np.inf))
                upper = float(node.get("upper", np.inf))
                if lower > upper:
                    raise OperatorError("winsorize lower bound must not exceed upper bound")
                result = args[0].clip(lower=lower, upper=upper)
            elif operation == "power":
                result = args[0] ** args[1]
            elif operation == "signed_power":
                result = np.sign(args[0]) * (args[0].abs() ** args[1])
            elif operation == "safe_div":
                denominator = args[1].replace([np.inf, -np.inf], np.nan)
                result = args[0].div(denominator.where(denominator != 0))
            elif operation == "where":
                if len(args) != 3:
                    raise OperatorError("where requires condition, true_value, false_value")
                result = pd.Series(np.where(args[0].fillna(False), args[1], args[2]), index=index)
            elif operation in {"min", "max"}:
                result = np.minimum(args[0], args[1]) if operation == "min" else np.maximum(args[0], args[1])
            elif operation in {"gt", "lt", "eq"}:
                result = {"gt": args[0] > args[1], "lt": args[0] < args[1], "eq": args[0] == args[1]}[operation].astype(float)
            else:
                raise OperatorError(f"unsupported operator: {operation}")
        result = pd.Series(result, index=index, dtype=float).replace([np.inf, -np.inf], np.nan)
        context.cache[key] = result
        return result

    @staticmethod
    def _available_at(timestamp: pd.Series, rule: str) -> pd.Series:
        if rule in {"T_CLOSE", "DECISION_DATE_CLOSE", "SAME_TIMESTAMP"}:
            return timestamp.astype(int)
        if rule in {"T_OPEN", "DECISION_DATE_OPEN"}:
            return timestamp.astype(int)
        raise PITError(f"unsupported available_at_rule: {rule}")

    def execute(self, definition: UnifiedFactorDefinition, view: AsOfDataView, as_of: int | None = None) -> pd.DataFrame:
        if definition.implementation_status != "EXECUTABLE":
            raise ValueError(f"factor is not executable: {definition.factor_id} ({definition.implementation_status})")
        raw_columns = set(view.frame.columns)
        if definition.feature_price_mode == "PIT_QFQ" and "pit_qfq_close" not in raw_columns:
            raise PITError("PIT_QFQ factor requires a point-in-time qfq field")
        target = int(as_of if as_of is not None else (view.as_of if view.as_of is not None else view.frame.get("timestamp", view.frame.get("date")).max()))
        panel = view.validate(self.guard, definition.required_fields, target)
        if definition.feature_price_mode == "PIT_QFQ" and "pit_qfq_close" not in panel.columns:
            raise PITError("PIT_QFQ factor requires a point-in-time qfq field")
        xs = None
        if definition.cross_sectional:
            if not view.universe_by_date:
                raise PITError("cross-sectional factor requires a PIT universe")
            xs = AsOfCrossSection(target, view.universe_by_date, view.universe_version)
        context = _EvalContext(panel=panel, cross_section=xs, registry=self.registry)
        values = self._eval(definition.operator_graph, context)
        result = pd.DataFrame({
            "factor_id": definition.factor_id,
            "factor_version": definition.version,
            "symbol": panel.index.get_level_values("symbol"),
            "timestamp": panel.index.get_level_values("timestamp").astype(int),
            "value": values.to_numpy(dtype=float),
        })
        result["available_at"] = self._available_at(result["timestamp"], definition.available_at_rule)
        if (result["available_at"] > target).any():
            raise PITError("factor available_at is after as_of")
        return result

    def execute_batch(self, definitions: Sequence[UnifiedFactorDefinition], view: AsOfDataView, as_of: int | None = None) -> dict[str, pd.DataFrame]:
        executable = [definition for definition in definitions if definition.implementation_status == "EXECUTABLE"]
        if not executable:
            return {}
        if any(definition.feature_price_mode == "PIT_QFQ" for definition in executable) and "pit_qfq_close" not in set(view.frame.columns):
            raise PITError("PIT_QFQ factor requires a point-in-time qfq field")
        target = int(as_of if as_of is not None else (view.as_of if view.as_of is not None else view.frame.get("timestamp", view.frame.get("date")).max()))
        all_fields = sorted({field_name for definition in executable for field_name in definition.required_fields})
        panel = view.validate(self.guard, all_fields, target)
        all_xs = any(definition.cross_sectional for definition in executable)
        xs = AsOfCrossSection(target, view.universe_by_date or {}, view.universe_version) if all_xs else None
        context = _EvalContext(panel=panel, cross_section=xs, registry=self.registry)
        output = {}
        for definition in executable:
            if definition.cross_sectional and xs is None:
                raise PITError("cross-sectional factor requires a PIT universe")
            values = self._eval(definition.operator_graph, context)
            result = pd.DataFrame({
                "factor_id": definition.factor_id,
                "factor_version": definition.version,
                "symbol": panel.index.get_level_values("symbol"),
                "timestamp": panel.index.get_level_values("timestamp").astype(int),
                "value": values.to_numpy(dtype=float),
            })
            result["available_at"] = self._available_at(result["timestamp"], definition.available_at_rule)
            if (result["available_at"] > target).any():
                raise PITError("factor available_at is after as_of")
            output[definition.factor_id] = result
        return output


# Naming aliases keep the contract compatible with the design vocabulary:
# a compiler produces values, while the runtime object is intentionally
# guarded and deterministic.
FactorCompiler = FactorExecutor
CanonicalFactorSpec = UnifiedFactorDefinition


@dataclass(frozen=True)
class FactorCacheKey:
    factor_id: str
    factor_version: str
    data_manifest: str
    as_of: int
    universe_version: str
    price_mode: str
    operator_version: str

    def as_string(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self)).encode("utf-8")).hexdigest()


class FactorComputationCache:
    def __init__(self):
        self._values: dict[str, pd.DataFrame] = {}

    def get_or_compute(self, key: FactorCacheKey, compute: Callable[[], pd.DataFrame]) -> pd.DataFrame:
        cache_key = key.as_string()
        if cache_key not in self._values:
            self._values[cache_key] = compute().copy()
        return self._values[cache_key].copy()

    def clear(self) -> None:
        self._values.clear()

    def __len__(self) -> int:
        return len(self._values)


@dataclass(frozen=True)
class FactorEvaluationContract:
    metrics: tuple[str, ...] = (
        "IC", "Rank IC", "IC Stability", "Coverage", "Turnover", "Correlation",
        "Monotonicity", "Decay", "Regime Split", "Concentration",
    )
    performance_selection_allowed: bool = False

    def empty_result(self, factor_id: str) -> dict[str, Any]:
        return {"factor_id": factor_id, "status": "INTERFACE_ONLY", "metrics": list(self.metrics), "values": None}


@dataclass(frozen=True)
class FiveMinuteFactorInterface:
    """Phase 1 contract for intraday factors without building a market-wide engine."""

    factor_id: str
    required_fields: tuple[str, ...]
    lookback_bars: int
    available_at_rule: str = "COMPLETED_BAR"
    implementation_status: str = "DEFERRED_TO_INTRADAY_FACTORY"
    reason: str = "Requires market-wide incremental 5-minute state and PIT session assembly"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonical_5min_factor_interfaces() -> list[FiveMinuteFactorInterface]:
    return [
        FiveMinuteFactorInterface("FIRST_5M_RETURN", ("open", "close"), 1),
        FiveMinuteFactorInterface("OPEN_30M_RETURN", ("open", "close"), 6),
        FiveMinuteFactorInterface("INTRADAY_VOLUME_RATIO", ("volume",), 24),
        FiveMinuteFactorInterface("INTRADAY_RANGE", ("high", "low"), 1),
    ]


def dependency_graph(registry: UnifiedFactorRegistry) -> dict[str, list[str]]:
    graph = {definition.factor_id: dependency_ids(definition.operator_graph) for definition in registry.items()}
    for node, deps in graph.items():
        missing = [dep for dep in deps if dep not in graph]
        if missing:
            raise ValueError(f"dependency graph references missing factor(s) for {node}: {missing}")
    return graph


def validate_acyclic(graph: Mapping[str, Sequence[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError(f"factor dependency cycle at {node}")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, []):
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


def factor_sanity(result: pd.DataFrame) -> dict[str, Any]:
    if result.empty:
        return {"rows": 0, "coverage": 0.0, "missing_rate": 1.0, "finite": False}
    values = pd.to_numeric(result["value"], errors="coerce")
    finite = np.isfinite(values.to_numpy())
    return {
        "rows": int(len(result)),
        "coverage": float(finite.mean()),
        "missing_rate": float((~finite).mean()),
        "finite": bool(finite.all()),
        "min": float(values.min()) if values.notna().any() else None,
        "max": float(values.max()) if values.notna().any() else None,
        "median": float(values.median()) if values.notna().any() else None,
    }
