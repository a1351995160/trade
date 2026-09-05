"""Phase 3 strategy-candidate contracts, builder, and deterministic compiler.

This module turns a READY ``AlphaHypothesisSpec`` into a frozen candidate
specification.  It deliberately stops before historical validation: no market
performance, backtest result, broker, paper, or recommendation path is read.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from .hypothesis import (
    AlphaHypothesisSpec,
    FactorKnowledgeView,
    PITReadinessChecker,
    ResearchContext,
    ResearchContextBuilder,
)
from .unified_factor import UnifiedFactorDefinition, UnifiedFactorRegistry, canonical_json


SCHEMA_VERSION = "strategy-candidate-spec-v1"
BUILDER_VERSION = "STRATEGY_CANDIDATE_BUILDER_V1"
DSL_VERSION = "strategy-dsl-v1"
CREATED_AT = "2026-08-23"

CANDIDATE_STATUSES = {
    "DRAFT", "SPEC_READY", "COMPILE_READY", "VALIDATION_READY",
    "BLOCKED_FACTOR", "BLOCKED_DATA", "BLOCKED_PIT", "BLOCKED_EXECUTION",
    "DUPLICATE", "REJECTED_DESIGN",
}
FORBIDDEN_CANDIDATE_STATUSES = {"PROMISING", "ROBUST", "PAPER_READY", "PRODUCTION", "APPROVED"}
PARAMETER_CLASSES = {"STRUCTURAL", "HYPOTHESIS_RANGE", "EXECUTION_FIXED", "RISK_FIXED"}
SIGNAL_FREQUENCIES = {"DAILY_SIGNAL", "INTRADAY_SIGNAL", "EVENT_SIGNAL", "HYBRID"}
STRATEGY_FAMILIES = {"DAILY_FACTOR", "DAILY_CROSS_SECTIONAL", "DAILY_EVENT", "INTRADAY_FACTOR", "HYBRID_EVENT_FACTOR"}
FORBIDDEN_KEYS = {
    "return", "returns", "future_return", "future_returns", "gross_return", "net_return",
    "sharpe", "profit_factor", "win_rate", "pnl", "performance", "backtest", "backtest_result",
    "optimization", "grid_search", "bayesian", "genetic", "paper_execution", "recommendation",
}
ARBITRARY_DECIMAL_THRESHOLD = re.compile(r"(?:>|<|==|>=|<=)\s*-?\d+\.\d{2,}")


class CandidateValidationError(ValueError):
    """Candidate schema, timing, or deterministic guard failure."""


class BlockedHypothesisError(CandidateValidationError):
    """A non-READY hypothesis was presented to the candidate builder."""


class CandidateCompilationError(CandidateValidationError):
    """A candidate cannot produce a safe deterministic signal."""


def _jsonable(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _contains_forbidden_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                return str(key)
            found = _contains_forbidden_key(nested)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _contains_forbidden_key(nested)
            if found:
                return found
    return None


def _contains_arbitrary_threshold(value: Any) -> str | None:
    if isinstance(value, str):
        match = ARBITRARY_DECIMAL_THRESHOLD.search(value)
        return match.group(0) if match else None
    if isinstance(value, Mapping):
        for nested in value.values():
            found = _contains_arbitrary_threshold(nested)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _contains_arbitrary_threshold(nested)
            if found:
                return found
    return None


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(_jsonable(value)).encode("utf-8")).hexdigest()


def _as_tuple(value: Any) -> tuple:
    if value is None:
        return tuple()
    return tuple(value) if isinstance(value, (list, tuple)) else (value,)


def _timestamp(value: Any) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if result.tzinfo is None:
        result = result.tz_localize("Asia/Shanghai")
    else:
        result = result.tz_convert("Asia/Shanghai")
    return result


def _timestamp_text(value: Any) -> str:
    return _timestamp(value).isoformat()


def _horizon_range(horizon: str) -> tuple[int, int]:
    ranges = {"1_3D": (1, 3), "3_5D": (3, 5), "5_10D": (5, 10), "10_20D": (10, 20)}
    try:
        return ranges[horizon]
    except KeyError as exc:
        raise CandidateValidationError(f"unsupported hypothesis horizon: {horizon}") from exc


def _canonical_holding_days(horizon: str) -> int:
    start, end = _horizon_range(horizon)
    if end <= 3:
        return end
    if end <= 5:
        return end
    if end <= 10:
        return start + 2
    return start


@dataclass(frozen=True)
class StrategyParameterSpec:
    name: str
    parameter_class: str
    value: Any
    allowed_values: tuple[Any, ...]
    source: str
    rationale: str

    def __post_init__(self) -> None:
        if not self.name or self.parameter_class not in PARAMETER_CLASSES:
            raise CandidateValidationError(f"invalid parameter specification: {self.name}")
        if self.parameter_class == "HYPOTHESIS_RANGE" and not self.allowed_values:
            raise CandidateValidationError(f"hypothesis range is empty: {self.name}")
        if self.parameter_class != "HYPOTHESIS_RANGE" and self.allowed_values:
            raise CandidateValidationError(f"fixed parameter cannot have a search range: {self.name}")
        if not self.source or not self.rationale:
            raise CandidateValidationError(f"parameter provenance is required: {self.name}")

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StrategyParameterSpec":
        return cls(
            name=str(payload["name"]),
            parameter_class=str(payload["parameter_class"]),
            value=payload.get("value"),
            allowed_values=tuple(payload.get("allowed_values", ())),
            source=str(payload["source"]),
            rationale=str(payload["rationale"]),
        )


@dataclass(frozen=True)
class GenericSmallCapitalStrategyContractV1:
    """Product/execution constraints for a 10,000 RMB manual strategy."""

    initial_cash: int = 10_000
    max_positions: int = 3
    buy_lot_size: int = 100
    t_plus_1: bool = True
    price_limit_contract: str = "A_SHARE_PRICE_LIMIT_FAIL_CLOSED"
    suspension_contract: str = "SUSPENSION_FAIL_CLOSED"
    calendar_contract: str = "REAL_TRADING_CALENDAR_REQUIRED"
    fee_model_reference: str = "ENGINE_CURRENT_FEE_PROFILE"
    slippage_model_reference: str = "ENGINE_FIXED_BPS_SLIPPAGE_REFERENCE"
    cash_non_negative: bool = True
    leverage: bool = False
    short: bool = False
    margin: bool = False
    automatic_rebalance: bool = False
    real_order_execution: str = "DISABLED"

    def __post_init__(self) -> None:
        if self.initial_cash != 10_000 or self.max_positions != 3 or self.buy_lot_size != 100:
            raise CandidateValidationError("GenericSmallCapitalStrategyContractV1 values are fixed")
        if not self.t_plus_1 or not self.cash_non_negative:
            raise CandidateValidationError("T+1 and non-negative cash are mandatory")
        if self.leverage or self.short or self.margin or self.automatic_rebalance:
            raise CandidateValidationError("leverage, short, margin, and automatic rebalance are disabled")
        if self.real_order_execution != "DISABLED":
            raise CandidateValidationError("real order execution must remain disabled")

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


def _core_candidate_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    included = {
        "candidate_version", "parent_hypothesis_id", "mechanism", "strategy_family", "factor_bindings",
        "factor_roles", "signal_logic", "entry_rule", "entry_timing", "ranking_rule", "selection_rule",
        "exit_rule", "holding_period", "position_sizing_rule", "max_positions", "cash_rule", "risk_filters",
        "execution_filters", "universe_rule", "price_mode", "required_data", "required_frequency",
        "available_at_contract", "t_plus_1_contract", "limit_up_down_contract", "suspension_contract",
        "lot_size_contract", "fee_model_reference", "slippage_model_reference", "parameter_spec",
        "parameter_provenance", "degrees_of_freedom", "complexity_score", "failure_conditions",
        "validation_plan", "small_capital_prior", "source_provenance", "candidate_type", "conflict_semantics",
        "complexity_justification", "strategy_dsl",
    }
    return {key: _jsonable(data[key]) for key in sorted(included) if key in data}


def _spec_core_payload(spec: "StrategyCandidateSpec") -> dict[str, Any]:
    return _core_candidate_payload(asdict(spec))


def candidate_fingerprint(spec: "StrategyCandidateSpec") -> str:
    return _stable_hash(_spec_core_payload(spec))


def preregistration_hash(spec: "StrategyCandidateSpec") -> str:
    payload = _spec_core_payload(spec)
    payload["parent_hypothesis_id"] = spec.parent_hypothesis_id
    return _stable_hash(payload)


def semantic_candidate_fingerprint(spec: "StrategyCandidateSpec") -> str:
    """Ignore candidate identity while retaining executable rule semantics."""
    payload = _spec_core_payload(spec)
    payload.pop("parent_hypothesis_id", None)
    payload.pop("parameter_spec", None)
    payload.pop("parameter_provenance", None)
    return _stable_hash(payload)


def close_candidate_fingerprint(spec: "StrategyCandidateSpec") -> str:
    """Group candidates with the same mechanism/rule graph but different factors."""
    payload = {
        "mechanism": spec.mechanism,
        "strategy_family": spec.strategy_family,
        "factor_roles": sorted((item.get("role"), item.get("direction")) for item in spec.factor_roles),
        "signal_logic": spec.signal_logic,
        "entry_rule": spec.entry_rule,
        "entry_timing": spec.entry_timing,
        "ranking_rule": spec.ranking_rule,
        "selection_rule": spec.selection_rule,
        "exit_rule": spec.exit_rule,
        "holding_period": spec.holding_period,
    }
    return _stable_hash(payload)


@dataclass(frozen=True)
class StrategyCandidateSpec:
    candidate_id: str
    candidate_version: str
    parent_hypothesis_id: str
    name: str
    description: str
    mechanism: str
    strategy_family: str
    factor_bindings: tuple[dict[str, Any], ...]
    factor_roles: tuple[dict[str, Any], ...]
    signal_logic: dict[str, Any]
    entry_rule: str
    entry_timing: dict[str, Any]
    ranking_rule: dict[str, Any]
    selection_rule: dict[str, Any]
    exit_rule: dict[str, Any]
    holding_period: int
    position_sizing_rule: str
    max_positions: int
    cash_rule: str
    risk_filters: tuple[dict[str, Any], ...]
    execution_filters: tuple[dict[str, Any], ...]
    universe_rule: dict[str, Any]
    price_mode: str
    required_data: tuple[str, ...]
    required_frequency: tuple[str, ...]
    available_at_contract: dict[str, Any]
    t_plus_1_contract: dict[str, Any]
    limit_up_down_contract: dict[str, Any]
    suspension_contract: dict[str, Any]
    lot_size_contract: dict[str, Any]
    fee_model_reference: str
    slippage_model_reference: str
    parameter_spec: tuple[dict[str, Any], ...]
    parameter_provenance: dict[str, Any]
    degrees_of_freedom: int
    complexity_score: int
    failure_conditions: tuple[str, ...]
    validation_plan: tuple[dict[str, Any], ...]
    small_capital_prior: str
    source_provenance: dict[str, Any]
    created_at: str
    builder_version: str
    candidate_status: str
    fingerprint: str
    preregistration_hash: str
    candidate_type: str
    conflict_semantics: dict[str, Any]
    complexity_justification: str
    strategy_dsl: dict[str, Any]
    failure_overlap_warning: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"CAND_[A-Z0-9_]+_V\d+", self.candidate_id):
            raise CandidateValidationError(f"invalid candidate_id: {self.candidate_id}")
        if self.candidate_status not in CANDIDATE_STATUSES:
            raise CandidateValidationError(f"unsupported candidate status: {self.candidate_status}")
        if self.candidate_status in FORBIDDEN_CANDIDATE_STATUSES:
            raise CandidateValidationError(f"forbidden candidate status: {self.candidate_status}")
        if not self.name.strip() or not self.description.strip() or not self.parent_hypothesis_id:
            raise CandidateValidationError("candidate identity and lineage are required")
        if self.strategy_family not in STRATEGY_FAMILIES or self.candidate_type not in SIGNAL_FREQUENCIES:
            raise CandidateValidationError("unsupported strategy family or candidate type")
        if self.holding_period < 1 or self.holding_period > 10:
            raise CandidateValidationError("holding_period must be within 1~10 trading days")
        if self.max_positions < 1 or self.max_positions > 3:
            raise CandidateValidationError("max_positions must be within the generic 10K contract")
        if self.degrees_of_freedom < 0 or self.degrees_of_freedom > 5:
            raise CandidateValidationError("degrees_of_freedom must be <= 5")
        if self.complexity_score < 1 or self.complexity_score > 5:
            raise CandidateValidationError("complexity_score must be between 1 and 5")
        binding_ids = [str(item.get("factor_id", "")) for item in self.factor_bindings]
        role_ids = [str(item.get("factor_id", "")) for item in self.factor_roles]
        if not binding_ids or len(set(binding_ids)) != len(binding_ids):
            raise CandidateValidationError("factor_bindings must be non-empty and unique")
        if set(binding_ids) != set(role_ids) or len(role_ids) != len(set(role_ids)):
            raise CandidateValidationError("factor_roles must cover factor_bindings exactly once")
        if not self.entry_rule.strip() or not self.exit_rule or not self.position_sizing_rule.strip():
            raise CandidateValidationError("entry, exit, and position contracts are required")
        if not self.ranking_rule or not self.selection_rule or not self.validation_plan:
            raise CandidateValidationError("ranking, selection, and validation plan are required")
        if self.strategy_dsl.get("schema_version") != DSL_VERSION:
            raise CandidateValidationError("unsupported strategy DSL version")
        if _contains_forbidden_key(self.to_dict()):
            raise CandidateValidationError("performance/backtest field is forbidden in candidate spec")
        threshold = _contains_arbitrary_threshold(self.to_dict())
        if threshold:
            raise CandidateValidationError(f"arbitrary precision threshold is forbidden: {threshold}")
        if self.fingerprint != candidate_fingerprint(self):
            raise CandidateValidationError("candidate fingerprint mismatch")
        if self.preregistration_hash != preregistration_hash(self):
            raise CandidateValidationError("preregistration hash mismatch")

    @classmethod
    def create(cls, payload: Mapping[str, Any]) -> "StrategyCandidateSpec":
        data = dict(payload)
        data["fingerprint"] = _stable_hash(_core_candidate_payload(data))
        data["preregistration_hash"] = _stable_hash(_core_candidate_payload(data))
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StrategyCandidateSpec":
        names = {item.name for item in fields(cls)}
        unknown = set(payload) - names
        if unknown:
            raise CandidateValidationError(f"unknown candidate fields: {sorted(unknown)}")
        data = dict(payload)
        for key in {
            "required_data", "required_frequency", "failure_conditions", "risk_filters", "execution_filters",
            "factor_bindings", "factor_roles", "parameter_spec", "validation_plan",
        }:
            data[key] = tuple(data.get(key, ()))
        return cls(**data)


class HypothesisStrategyConsistencyGuard:
    """Ensures the builder does not silently rewrite a research hypothesis."""

    def validate(self, hypothesis: AlphaHypothesisSpec, candidate: StrategyCandidateSpec) -> list[str]:
        errors: list[str] = []
        if candidate.parent_hypothesis_id != hypothesis.hypothesis_id:
            errors.append("parent_hypothesis_id mismatch")
        if candidate.mechanism.lower() != hypothesis.mechanism.lower():
            errors.append("mechanism mismatch")
        candidate_factor_ids = {str(item["factor_id"]) for item in candidate.factor_bindings}
        if candidate_factor_ids != set(hypothesis.factor_ids):
            errors.append("factor binding mismatch")
        candidate_roles = {str(item["factor_id"]): str(item["role"]) for item in candidate.factor_roles}
        hypothesis_roles = {str(item["factor_id"]): str(item["role"]) for item in hypothesis.factor_roles}
        if candidate_roles != hypothesis_roles:
            errors.append("factor role mismatch")
        if tuple(candidate.signal_logic.get("market_regime", ())) != tuple(hypothesis.market_regime):
            errors.append("market regime mismatch")
        if candidate.signal_logic.get("hypothesis_entry_concept") != hypothesis.entry_concept:
            errors.append("entry concept was rewritten")
        if candidate.signal_logic.get("hypothesis_exit_concept") != hypothesis.exit_concept:
            errors.append("exit concept was rewritten")
        low, high = _horizon_range(hypothesis.target_horizon)
        if not low <= candidate.holding_period <= high:
            errors.append("holding period is outside hypothesis horizon")
        expected_type = "EVENT_SIGNAL" if hypothesis.event_dependencies else "DAILY_SIGNAL"
        if candidate.candidate_type != expected_type:
            errors.append("signal type does not match hypothesis inputs")
        return errors

    def assert_valid(self, hypothesis: AlphaHypothesisSpec, candidate: StrategyCandidateSpec) -> None:
        errors = self.validate(hypothesis, candidate)
        if errors:
            raise CandidateValidationError("; ".join(errors))


@dataclass(frozen=True)
class CompiledSignal:
    strategy_id: str
    candidate_id: str
    symbol: str
    action: str
    generated_at: str
    available_at: str
    eligible_at: str
    score: float
    rank: int | None
    reason: str
    source_factors: tuple[str, ...]
    metadata: dict[str, Any]

    def __post_init__(self) -> None:
        if self.action not in {"BUY", "SELL"}:
            raise CandidateCompilationError(f"unsupported signal action: {self.action}")
        if _timestamp(self.available_at) > _timestamp(self.generated_at):
            raise CandidateCompilationError("signal generated_at precedes available_at")
        if _timestamp(self.eligible_at) < _timestamp(self.generated_at):
            raise CandidateCompilationError("eligible_at precedes generated_at")

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass(frozen=True)
class CompiledStrategyCandidate:
    spec: StrategyCandidateSpec

    def _resolve_eligible_at(self, row: Mapping[str, Any], signal_frequency: str) -> str:
        if signal_frequency == "DAILY_SIGNAL" or signal_frequency == "EVENT_SIGNAL":
            value = row.get("next_session_open")
            if value is None:
                raise CandidateCompilationError("daily candidate requires next_session_open")
            return _timestamp_text(value)
        value = row.get("next_legal_bar")
        if value is None:
            raise CandidateCompilationError("intraday candidate requires next_legal_bar")
        return _timestamp_text(value)

    def _check_row(self, row: Mapping[str, Any]) -> tuple[str, str, str, dict[str, float]]:
        symbol = str(row.get("symbol", ""))
        if not symbol:
            raise CandidateCompilationError("symbol is required")
        generated = _timestamp_text(row.get("generated_at", row.get("available_at")))
        available = _timestamp_text(row.get("available_at", generated))
        if _timestamp(available) > _timestamp(generated):
            raise CandidateCompilationError("available_at is after generated_at")
        factor_values = row.get("factor_values", {})
        if not isinstance(factor_values, Mapping):
            raise CandidateCompilationError("factor_values must be a mapping")
        values: dict[str, float] = {}
        factor_available = row.get("factor_available_at", {})
        for binding in self.spec.factor_bindings:
            factor_id = str(binding["factor_id"])
            if factor_id not in factor_values:
                raise CandidateCompilationError(f"missing factor value: {factor_id}")
            value = float(factor_values[factor_id])
            if not math.isfinite(value):
                raise CandidateCompilationError(f"non-finite factor value: {factor_id}")
            if isinstance(factor_available, Mapping) and factor_id in factor_available:
                if _timestamp(factor_available[factor_id]) > _timestamp(generated):
                    raise CandidateCompilationError(f"factor available_at is in the future: {factor_id}")
            values[factor_id] = value
        for event_id in self.spec.signal_logic.get("event_dependencies", ()):
            if str(row.get("event_id", "")) != str(event_id):
                raise CandidateCompilationError(f"event dependency not satisfied: {event_id}")
            event_available = row.get("event_available_at")
            if event_available is None or _timestamp(event_available) > _timestamp(generated):
                raise CandidateCompilationError(f"event available_at is in the future: {event_id}")
        if row.get("universe_eligible") is not True:
            raise CandidateCompilationError("PIT universe eligibility is required")
        if row.get("regime_pass") is False:
            raise CandidateCompilationError("market regime gate failed")
        if row.get("tradable") is not True:
            raise CandidateCompilationError("execution tradability gate failed")
        if row.get("affordable") is False:
            raise CandidateCompilationError("affordability execution gate failed")
        return symbol, generated, available, values

    def _score(self, values: Mapping[str, float], role: str) -> float:
        scores: list[float] = []
        for binding in self.spec.factor_bindings:
            if binding.get("role") != role:
                continue
            direction = str(binding.get("direction", "POSITIVE"))
            value = float(values[binding["factor_id"]])
            scores.append(-value if direction == "NEGATIVE" else value)
        return sum(scores) / len(scores) if scores else 0.0

    def emit_signals(self, rows: Iterable[Mapping[str, Any]]) -> list[CompiledSignal]:
        prepared: list[tuple[Mapping[str, Any], str, str, str, dict[str, float], float, float]] = []
        for row in rows:
            symbol, generated, available, values = self._check_row(row)
            primary = self._score(values, "PRIMARY_ALPHA")
            secondary = self._score(values, "CONFIRMATION")
            prepared.append((row, symbol, generated, available, values, primary, secondary))
        prepared.sort(key=lambda item: (-item[5], -item[6], item[1]))
        top_n = int(self.spec.selection_rule.get("top_n", self.spec.max_positions))
        top_n = min(top_n, self.spec.max_positions)
        outputs: list[CompiledSignal] = []
        for position, (row, symbol, generated, available, values, primary, secondary) in enumerate(prepared, start=1):
            eligible = self._resolve_eligible_at(row, self.spec.candidate_type)
            position_open = bool(row.get("position_open", False))
            exit_due = bool(row.get("exit_due", False))
            invalidated = bool(row.get("primary_alpha_invalidated", False))
            if position_open and (exit_due or invalidated):
                outputs.append(CompiledSignal(
                    strategy_id=self.spec.candidate_id,
                    candidate_id=self.spec.candidate_id,
                    symbol=symbol,
                    action="SELL",
                    generated_at=generated,
                    available_at=available,
                    eligible_at=eligible,
                    score=primary,
                    rank=None,
                    reason="EXIT_RULE_SATISFIED",
                    source_factors=tuple(binding["factor_id"] for binding in self.spec.factor_bindings),
                    metadata={"exit_type": self.spec.exit_rule.get("type"), "conflict_policy": "EXIT_FAIL_CLOSED"},
                ))
                continue
            if position > top_n:
                continue
            outputs.append(CompiledSignal(
                strategy_id=self.spec.candidate_id,
                candidate_id=self.spec.candidate_id,
                symbol=symbol,
                action="BUY",
                generated_at=generated,
                available_at=available,
                eligible_at=eligible,
                score=primary,
                rank=position,
                reason="PRIMARY_ALPHA_RANK_WITH_DECLARED_ROLE_RULES",
                source_factors=tuple(binding["factor_id"] for binding in self.spec.factor_bindings),
                metadata={"primary_score": primary, "secondary_score": secondary, "ranking_rule": self.spec.ranking_rule},
            ))
        return outputs


class StrategyCandidateCompiler:
    VERSION = "STRATEGY_CANDIDATE_COMPILER_V1"

    def compile(self, spec: StrategyCandidateSpec) -> CompiledStrategyCandidate:
        if spec.candidate_status not in {"COMPILE_READY", "VALIDATION_READY"}:
            raise CandidateCompilationError(f"candidate is not compilable: {spec.candidate_status}")
        if spec.entry_timing.get("same_bar_entry"):
            raise CandidateCompilationError("same-bar entry is forbidden")
        if spec.entry_timing.get("signal_frequency") == "INTRADAY_SIGNAL" and not spec.entry_timing.get("completed_bar_required"):
            raise CandidateCompilationError("intraday candidate must require completed bars")
        return CompiledStrategyCandidate(spec)


class StrategyCandidateRegistry:
    def __init__(self, path: str | Path = "data/research/strategy_candidate_registry/registry.json"):
        self.path = Path(path)
        self._items: dict[tuple[str, str], StrategyCandidateSpec] = {}

    def append(self, spec: StrategyCandidateSpec) -> bool:
        key = (spec.candidate_id, spec.candidate_version)
        existing = self._items.get(key)
        if existing is not None:
            if existing.to_dict() != spec.to_dict():
                raise CandidateValidationError(f"append-only candidate version conflict: {key}")
            return False
        versions = [version for candidate_id, version in self._items if candidate_id == spec.candidate_id]
        if spec.candidate_version in versions:
            raise CandidateValidationError(f"duplicate candidate version: {key}")
        self._items[key] = spec
        return True

    def items(self) -> list[StrategyCandidateSpec]:
        return [self._items[key] for key in sorted(self._items)]

    def get(self, candidate_id: str, version: str = "v1") -> StrategyCandidateSpec | None:
        return self._items.get((candidate_id, version))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "candidates": [item.to_dict() for item in self.items()]}

    def write(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            existing = StrategyCandidateRegistry.read(self.path)
            for item in existing.items():
                self.append(item)
        self.path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return self.path

    @classmethod
    def read(cls, path: str | Path) -> "StrategyCandidateRegistry":
        registry = cls(path)
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise CandidateValidationError("candidate registry schema mismatch")
        for item in payload.get("candidates", []):
            registry.append(StrategyCandidateSpec.from_dict(item))
        return registry


def _factor_definition_map(context: ResearchContext) -> dict[str, UnifiedFactorDefinition]:
    return {item.factor_id: item for item in context.factor_registry.items()}


class StrategyCandidateBuilder:
    """Deterministically compose one canonical candidate per READY hypothesis."""

    def __init__(self, root: str | Path = ".") -> None:
        self.root = Path(root)
        self.context = ResearchContextBuilder(self.root).build()
        self.factor_view = FactorKnowledgeView(self.context)
        self.pit_checker = PITReadinessChecker(self.context)
        self.consistency = HypothesisStrategyConsistencyGuard()
        self.factor_definitions = _factor_definition_map(self.context)

    def load_hypotheses(self) -> list[AlphaHypothesisSpec]:
        path = self.root / "data/research/hypothesis_registry/registry.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [AlphaHypothesisSpec.from_dict(item) for item in payload.get("hypotheses", [])]

    @staticmethod
    def _strategy_family(hypothesis: AlphaHypothesisSpec) -> str:
        if hypothesis.event_dependencies:
            return "DAILY_EVENT"
        if hypothesis.hypothesis_type == "CROSS_SECTIONAL_RANK":
            return "DAILY_CROSS_SECTIONAL"
        return "DAILY_FACTOR"

    @staticmethod
    def _candidate_type(hypothesis: AlphaHypothesisSpec) -> str:
        if hypothesis.event_dependencies:
            return "EVENT_SIGNAL"
        if any(str(freq).upper() in {"5MIN", "INTRADAY"} for freq in hypothesis.required_frequency):
            return "INTRADAY_SIGNAL"
        return "DAILY_SIGNAL"

    @staticmethod
    def _factor_roles(hypothesis: AlphaHypothesisSpec) -> tuple[dict[str, Any], ...]:
        return tuple({
            "factor_id": item["factor_id"],
            "role": item["role"],
            "direction": hypothesis.expected_factor_direction[item["factor_id"]],
        } for item in hypothesis.factor_roles)

    def _failure_warning(self, hypothesis: AlphaHypothesisSpec) -> dict[str, Any]:
        classes = {str(item.get("failure_class")) for item in self.context.failure_knowledge}
        relevant = sorted(classes.intersection({
            "NO_ALPHA", "REGIME_DEPENDENT", "EXECUTION_SENSITIVE", "SMALL_CAPITAL_FAILURE",
            "HIGH_TURNOVER", "FEE_DESTROYED", "LOOKAHEAD", "WINNER_CONCENTRATION",
        }))
        return {
            "matched_failure_classes": relevant,
            "overlap": hypothesis.hypothesis_type in {
                "TREND_CONTINUATION", "MOMENTUM", "SHORT_TERM_REVERSAL", "BREAKOUT",
                "VOLATILITY_COMPRESSION", "VOLUME_PRICE_CONFIRMATION", "EVENT_CONTINUATION",
                "EVENT_REVERSAL", "MEAN_REVERSION", "CROSS_SECTIONAL_RANK",
            },
            "distinction": "Candidate structure, timing, role bindings, and validation gates are pre-registered; no historical result was used to modify the mechanism.",
        }

    def _compose(self, hypothesis: AlphaHypothesisSpec, evidence: Sequence[str]) -> StrategyCandidateSpec:
        candidate_type = self._candidate_type(hypothesis)
        family = self._strategy_family(hypothesis)
        holding = _canonical_holding_days(hypothesis.target_horizon)
        factor_roles = self._factor_roles(hypothesis)
        factor_bindings = tuple({
            **role,
            "version": "v1",
            "source": "UNIFIED_FACTOR_REGISTRY",
        } for role in factor_roles)
        primary = [item for item in factor_roles if item["role"] == "PRIMARY_ALPHA"]
        has_confirmation = any(item["role"] == "CONFIRMATION" for item in factor_roles)
        regime = tuple(hypothesis.market_regime)
        event_dependencies = tuple(hypothesis.event_dependencies)
        definitions = [self.factor_definitions[item["factor_id"]] for item in factor_roles]
        modes = {item.feature_price_mode for item in definitions}
        price_mode = "RETURN_ONLY" if modes == {"RETURN_ONLY"} else "PIT_QFQ_FEATURES_RAW_EXECUTION"
        required_fields = sorted({field for item in definitions for field in item.required_fields})
        required_data = tuple(sorted(set(hypothesis.required_data) | set(required_fields) | ({"PIT_EVENT_STORE"} if event_dependencies else set())))
        entry_timing = {
            "signal_frequency": candidate_type,
            "generated_at": "T_CLOSE_AFTER_ALL_REQUIRED_FACTORS_AVAILABLE",
            "available_at": "MAX_FACTOR_AND_EVENT_AVAILABLE_AT",
            "eligible_at": "NEXT_SESSION_OPEN",
            "order_created_at": "ELIGIBLE_AT",
            "fill_time": "DEFERRED_TO_BACKTEST_ENGINE_V2",
            "completed_bar_required": candidate_type == "INTRADAY_SIGNAL",
            "same_bar_entry": False,
        }
        if candidate_type == "INTRADAY_SIGNAL":
            entry_timing.update({
                "generated_at": "COMPLETED_BAR_CLOSE",
                "available_at": "COMPLETED_BAR_CLOSE",
                "eligible_at": "NEXT_LEGAL_BAR",
            })
        signal_logic = {
            "schema_version": DSL_VERSION,
            "hypothesis_entry_concept": hypothesis.entry_concept,
            "hypothesis_exit_concept": hypothesis.exit_concept,
            "event_dependencies": event_dependencies,
            "market_regime": regime,
            "pipeline": [
                {"stage": "eligibility", "rules": ["PIT_UNIVERSE_AS_OF_SIGNAL", "ALL_REQUIRED_FACTORS_FINITE"]},
                {"stage": "regime", "rules": ["DECLARED_REGIME_GATE", *regime]},
                {"stage": "primary_alpha", "rules": ["DIRECTIONAL_FACTOR_SCORE", *[item["factor_id"] for item in primary]]},
                {"stage": "confirmation", "rules": ["SECONDARY_SCORE_NO_ARBITRARY_THRESHOLD"] if has_confirmation else []},
                {"stage": "ranking", "rules": ["PRIMARY_SCORE_DESC", "SECONDARY_SCORE_DESC", "SYMBOL_ASC"]},
                {"stage": "selection", "rules": ["TOP_N_PRE_REGISTERED"]},
                {"stage": "risk", "rules": ["T_PLUS_1", "PRICE_LIMIT_FAIL_CLOSED", "SUSPENSION_FAIL_CLOSED"]},
                {"stage": "execution", "rules": ["NEXT_SESSION_OPEN", "AFFORDABILITY_AT_EXECUTION"]},
            ],
        }
        entry_rule = "Eligible only after declared factor/event availability at T close; execute no earlier than next session open."
        exit_type = "PRIMARY_ALPHA_INVALIDATION" if hypothesis.hypothesis_type in {"BREAKOUT", "PRICE_STRUCTURE", "TREND_CONTINUATION"} else "FIXED_HOLD"
        exit_rule = {
            "type": exit_type,
            "fixed_holding_days": holding,
            "invalidation": "PRIMARY_ALPHA_INVALIDATION" if exit_type == "PRIMARY_ALPHA_INVALIDATION" else "NONE",
            "stop_loss_policy": "NO_ALPHA_SPECIFIC_STOP",
            "execution": "NEXT_SESSION_OPEN_AFTER_EXIT_SIGNAL",
        }
        ranking_rule = {
            "status": "DETERMINISTIC",
            "primary_score": "directional_primary_factor_mean",
            "secondary_score": "directional_confirmation_factor_mean_or_zero",
            "tie_breaker": "SYMBOL_ASC",
        }
        selection_rule = {
            "type": "TOP_N",
            "top_n": 3,
            "source": "GENERIC_10K_CONTRACT",
            "parameter_search_status": "DISABLED",
        }
        parameter_specs = (
            StrategyParameterSpec(
                "holding_period_days", "HYPOTHESIS_RANGE", holding, _horizon_range(hypothesis.target_horizon),
                "parent_hypothesis.target_horizon", "Canonical representative of the declared horizon; no variant search.",
            ),
            StrategyParameterSpec("max_positions", "RISK_FIXED", 3, (), "GENERIC_10K_CONTRACT", "Product risk limit."),
            StrategyParameterSpec("selection_top_n", "RISK_FIXED", 3, (), "GENERIC_10K_CONTRACT", "Top-N is fixed by the 10K product contract."),
            StrategyParameterSpec("buy_lot_size", "EXECUTION_FIXED", 100, (), "A_SHARE_EXECUTION_CONTRACT", "A-share minimum lot contract."),
        )
        validation_plan = (
            {"stage": "sanity", "required": True, "scope": "synthetic deterministic signal fixture"},
            {"stage": "dataset", "required": True, "scope": "PIT daily factors/events and PIT universe"},
            {"stage": "period_split", "required": True, "scope": "existing research governance; no Phase 3 period selection"},
            {"stage": "robustness", "required": True, "tests": ["regime_split", "concentration", "fee_slippage", "execution_timing"]},
            {"stage": "small_capital", "required": True, "scope": "GenericSmallCapitalStrategyContractV1"},
            {"stage": "microstructure", "required": candidate_type in {"INTRADAY_SIGNAL", "EVENT_SIGNAL"}, "scope": "completed-bar/event availability when applicable"},
        )
        universe_rule = {
            "type": "PIT_UNIVERSE",
            "exchange": ["SH", "SZ", "BJ"],
            "board_policy": "SECURITY_MASTER_BOARD_RULES",
            "listing_age": "PIT_LISTING_AGE_IF_REQUIRED_ELSE_NO_INFERENCE",
            "st_handling": "NO_CURRENT_STATUS_BACKFILL; FAIL_CLOSED_IF_REQUIRED_PIT_MISSING",
            "suspension": "PIT_SUSPENSION_REQUIRED_AT_EXECUTION",
            "delisting": "PIT_DELISTING_FAIL_CLOSED",
            "liquidity": "DECLARED_FACTOR_OR_EXECUTION_FILTER_ONLY",
            "affordability": "CHECK_AT_EXECUTION_TIME",
        }
        payload: dict[str, Any] = {
            "candidate_id": "CAND_" + re.sub(r"[^A-Z0-9]+", "_", hypothesis.hypothesis_id.replace("HYP_", "")).strip("_") + "_V1",
            "candidate_version": "v1",
            "parent_hypothesis_id": hypothesis.hypothesis_id,
            "name": f"{hypothesis.title} | Candidate V1",
            "description": f"Deterministic candidate translation of hypothesis {hypothesis.hypothesis_id}; rules are frozen before future validation.",
            "mechanism": hypothesis.mechanism,
            "strategy_family": family,
            "factor_bindings": factor_bindings,
            "factor_roles": factor_roles,
            "signal_logic": signal_logic,
            "entry_rule": entry_rule,
            "entry_timing": entry_timing,
            "ranking_rule": ranking_rule,
            "selection_rule": selection_rule,
            "exit_rule": exit_rule,
            "holding_period": holding,
            "position_sizing_rule": "EQUAL_WEIGHT_UP_TO_MAX_POSITIONS; CASH_AND_LOT_ROUNDED; NO_LEVERAGE",
            "max_positions": 3,
            "cash_rule": "CASH_CANNOT_GO_NEGATIVE; NO_LEVERAGE; NO_SHORT; NO_MARGIN",
            "risk_filters": (
                {"name": "PIT_UNIVERSE", "rule": "UNIVERSE_AS_OF_SIGNAL"},
                {"name": "T_PLUS_1", "rule": "SELL_ONLY_AFTER_ENTRY_SESSION"},
                {"name": "PRICE_LIMIT", "rule": "FAIL_CLOSED_WHEN_LOCKED"},
                {"name": "SUSPENSION", "rule": "FAIL_CLOSED_WHEN_SUSPENDED"},
            ),
            "execution_filters": (
                {"name": "AFFORDABILITY", "rule": "CHECK_AVAILABLE_CASH_AND_LOT_AT_EXECUTION"},
                {"name": "NEXT_SESSION", "rule": "NO_SAME_BAR_EXECUTION"},
            ),
            "universe_rule": universe_rule,
            "price_mode": price_mode,
            "required_data": required_data,
            "required_frequency": tuple(hypothesis.required_frequency),
            "available_at_contract": {
                "factor_rule": "ALL_FACTOR_AVAILABLE_AT<=GENERATED_AT",
                "event_rule": "ALL_EVENT_AVAILABLE_AT<=GENERATED_AT",
                "universe_rule": "PIT_UNIVERSE_AS_OF_GENERATED_AT",
            },
            "t_plus_1_contract": {"enabled": True, "sellable": "NEXT_SESSION_AFTER_ENTRY", "source": "BACKTEST_ENGINE_V2_LEDGER"},
            "limit_up_down_contract": {"buy": "NO_FILL_WHEN_LIMIT_LOCKED", "sell": "NO_FILL_WHEN_LIMIT_LOCKED", "source": "BACKTEST_ENGINE_V2_BROKER"},
            "suspension_contract": {"status": "FAIL_CLOSED", "source": "BACKTEST_ENGINE_V2_BROKER"},
            "lot_size_contract": {"buy_lot_size": 100, "board_overrides": "SECURITY_MASTER_RULES", "rounding": "ROUND_DOWN"},
            "fee_model_reference": "ENGINE_CURRENT_FEE_PROFILE",
            "slippage_model_reference": "ENGINE_FIXED_BPS_SLIPPAGE_REFERENCE",
            "parameter_spec": tuple(item.to_dict() for item in parameter_specs),
            "parameter_provenance": {item.name: {"class": item.parameter_class, "source": item.source, "rationale": item.rationale} for item in parameter_specs},
            "degrees_of_freedom": sum(item.parameter_class == "HYPOTHESIS_RANGE" for item in parameter_specs),
            "complexity_score": min(5, max(1, 1 + len(factor_roles) + int(bool(event_dependencies)) + int(len(regime) > 1))),
            "failure_conditions": tuple(hypothesis.failure_conditions),
            "validation_plan": validation_plan,
            "small_capital_prior": hypothesis.small_capital_fit_prior,
            "source_provenance": {
                "parent_hypothesis_id": hypothesis.hypothesis_id,
                "hypothesis_fingerprint": hypothesis.hypothesis_fingerprint,
                "source": hypothesis.source_provenance,
                "external_inspiration_only": True,
                "performance_values_loaded": False,
            },
            "created_at": CREATED_AT,
            "builder_version": BUILDER_VERSION,
            "candidate_status": "VALIDATION_READY",
            "candidate_type": candidate_type,
            "conflict_semantics": {
                "same_candidate_buy_exit": "EXIT_FAIL_CLOSED",
                "different_candidates": "DEFERRED_TO_FUTURE_AGGREGATOR",
            },
            "complexity_justification": "One canonical candidate per hypothesis; holding range is inherited, positions and ranking are product-fixed.",
            "strategy_dsl": {
                "schema_version": DSL_VERSION,
                "hypothesis_id": hypothesis.hypothesis_id,
                "factor_graph": [item["factor_id"] for item in factor_roles],
                "rule_graph": signal_logic["pipeline"],
            },
            "failure_overlap_warning": self._failure_warning(hypothesis),
        }
        candidate = StrategyCandidateSpec.create(payload)
        self.consistency.assert_valid(hypothesis, candidate)
        if evidence and candidate.candidate_status != "VALIDATION_READY":
            raise CandidateValidationError("READY hypothesis did not produce a validation-ready candidate")
        return candidate

    def build_hypothesis(self, hypothesis: AlphaHypothesisSpec) -> StrategyCandidateSpec:
        if hypothesis.status != "READY_FOR_STRATEGY_BUILD":
            raise BlockedHypothesisError(f"only READY hypotheses may be built: {hypothesis.hypothesis_id}={hypothesis.status}")
        readiness, evidence, _ = self.pit_checker.resolve(hypothesis.factor_ids, hypothesis.event_dependencies)
        if readiness != "READY_FOR_STRATEGY_BUILD":
            raise BlockedHypothesisError(f"hypothesis is not executable for candidate construction: {hypothesis.hypothesis_id}={readiness}")
        return self._compose(hypothesis, evidence)

    def build_all(self) -> list[StrategyCandidateSpec]:
        result: list[StrategyCandidateSpec] = []
        for hypothesis in self.load_hypotheses():
            if hypothesis.status != "READY_FOR_STRATEGY_BUILD":
                continue
            result.append(self.build_hypothesis(hypothesis))
        if len(result) > 50:
            raise CandidateValidationError("MAX_PHASE3_CANDIDATES exceeded")
        return sorted(result, key=lambda item: item.candidate_id)


def generic_small_capital_contract() -> GenericSmallCapitalStrategyContractV1:
    return GenericSmallCapitalStrategyContractV1()
