"""Phase 3.1 semantic contracts and compiler.

This module is intentionally separate from the frozen Phase 3 candidate
builder.  It turns the conceptual V1 candidate into an auditable signal
qualification contract without reading performance data or invoking any
backtest/paper/live execution path.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

from .strategy_candidate import (
    CompiledSignal,
    CandidateCompilationError,
    StrategyCandidateSpec,
    _contains_arbitrary_threshold,
    _jsonable,
    _stable_hash,
    _timestamp,
    _timestamp_text,
)


SEMANTIC_CONTRACT_VERSION = "strategy-semantic-execution-v1"
SEMANTIC_STATUSES = {
    "SEMANTIC_EXECUTABLE",
    "SEMANTIC_RANK_ONLY_VALID",
    "SEMANTIC_EVENT_VALID",
    "BLOCKED_ENTRY_PREDICATE",
    "BLOCKED_EXIT_PREDICATE",
    "BLOCKED_REGIME",
    "BLOCKED_INTERACTION",
    "SEMANTIC_REVIEW_REQUIRED",
}
PREDICATE_TYPES = {"RANK_ONLY", "THRESHOLD", "STRUCTURAL", "EVENT", "REGIME", "COMPOSITE"}
EXIT_TYPES = {"FIXED_HOLD", "PRIMARY_ALPHA_INVALIDATION", "STRUCTURE_INVALIDATION", "EVENT_INVALIDATION", "REGIME_EXIT"}
PHASE4_STATUSES = {"SEMANTIC_EXECUTABLE", "SEMANTIC_RANK_ONLY_VALID", "SEMANTIC_EVENT_VALID"}

# These are neutral points already implied by the canonical factor formulas.
# They are not tuned values: returns/distances/slopes/correlations are
# zero-centred, and ratios are neutral at one.
ZERO_CENTRED_FACTORS = {
    "GAP_SIZE", "MA_DISTANCE_20", "MA_DISTANCE_60", "MA_SLOPE_20",
    "MOM_ACCEL_5_20", "PRICE_VOLUME_CORR", "RETURN_3D", "RETURN_5D",
    "RETURN_10D", "RETURN_20D", "RETURN_60D", "VOLUME_ACCEL",
}
RATIO_FACTORS = {
    "RANGE_COMPRESSION_10", "RANGE_COMPRESSION_20", "VOL_RATIO_5_20",
    "VOLUME_RATIO_1_20", "VOLUME_RATIO_5_20",
}
STRUCTURAL_BOUNDARY_FACTORS = {"DONCHIAN_POSITION_20"}


class SemanticContractError(ValueError):
    """Raised when a semantic contract is malformed or unsafe."""


class PerformanceBlindGuard:
    """Reject performance-bearing inputs before semantic work starts."""

    FORBIDDEN_KEYS = {
        "return", "returns", "future_return", "future_returns", "gross_return", "net_return",
        "ic", "rank_ic", "sharpe", "profit_factor", "win_rate", "pnl", "drawdown",
        "backtest_result", "backtest_results", "trade_outcome", "performance", "optimization",
    }

    @classmethod
    def assert_blind(cls, value: Any) -> None:
        def visit(item: Any) -> None:
            if isinstance(item, Mapping):
                for key, nested in item.items():
                    key_text = str(key).lower()
                    if key_text in cls.FORBIDDEN_KEYS:
                        raise SemanticContractError(f"performance-bearing input is forbidden: {key}")
                    visit(nested)
            elif isinstance(item, (list, tuple)):
                for nested in item:
                    visit(nested)
        visit(value)


def _validate_contract(value: Any) -> None:
    PerformanceBlindGuard.assert_blind(value)
    threshold = _contains_arbitrary_threshold(value)
    if threshold:
        raise SemanticContractError(f"arbitrary threshold is forbidden: {threshold}")


@dataclass(frozen=True)
class SignalPredicateSpec:
    predicate_id: str
    predicate_type: str
    source_hypothesis_id: str
    source_candidate_id: str
    logic: str
    factor_conditions: tuple[dict[str, Any], ...]
    event_conditions: tuple[dict[str, Any], ...]
    regime_conditions: tuple[dict[str, Any], ...]
    interaction_conditions: tuple[dict[str, Any], ...]
    eligibility_conditions: tuple[dict[str, Any], ...]
    availability_contract: dict[str, Any]
    parameter_dependencies: tuple[str, ...]
    parameter_source: dict[str, Any]
    pit_requirements: tuple[str, ...]
    semantic_status: str
    explanation: str

    def __post_init__(self) -> None:
        if not self.predicate_id or not self.source_hypothesis_id or not self.source_candidate_id:
            raise SemanticContractError("predicate lineage is required")
        if self.predicate_type not in PREDICATE_TYPES:
            raise SemanticContractError(f"unsupported predicate type: {self.predicate_type}")
        if self.semantic_status not in SEMANTIC_STATUSES:
            raise SemanticContractError(f"unsupported semantic status: {self.semantic_status}")
        if self.logic not in {"AND", "OR", "RANK_ONLY"}:
            raise SemanticContractError(f"unsupported predicate logic: {self.logic}")
        if not self.explanation.strip():
            raise SemanticContractError("predicate explanation is required")
        _validate_contract(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SignalPredicateSpec":
        data = dict(payload)
        for key in (
            "factor_conditions", "event_conditions", "regime_conditions", "interaction_conditions",
            "eligibility_conditions", "parameter_dependencies", "pit_requirements",
        ):
            data[key] = tuple(data.get(key, ()))
        return cls(**data)


@dataclass(frozen=True)
class ExitPredicateSpec:
    predicate_id: str
    exit_type: str
    source_hypothesis_id: str
    source_candidate_id: str
    logic: str
    factor_conditions: tuple[dict[str, Any], ...]
    event_conditions: tuple[dict[str, Any], ...]
    regime_conditions: tuple[dict[str, Any], ...]
    availability_contract: dict[str, Any]
    parameter_dependencies: tuple[str, ...]
    parameter_source: dict[str, Any]
    pit_requirements: tuple[str, ...]
    semantic_status: str
    explanation: str

    def __post_init__(self) -> None:
        if not self.predicate_id or not self.source_hypothesis_id or not self.source_candidate_id:
            raise SemanticContractError("exit predicate lineage is required")
        if self.exit_type not in EXIT_TYPES:
            raise SemanticContractError(f"unsupported exit type: {self.exit_type}")
        if self.logic not in {"AND", "OR", "FIXED_HOLD"}:
            raise SemanticContractError(f"unsupported exit logic: {self.logic}")
        if self.semantic_status not in {"COMPLETE", "BLOCKED_EXIT_PREDICATE", "SEMANTIC_REVIEW_REQUIRED"}:
            raise SemanticContractError(f"unsupported exit status: {self.semantic_status}")
        if not self.explanation.strip():
            raise SemanticContractError("exit explanation is required")
        _validate_contract(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExitPredicateSpec":
        data = dict(payload)
        for key in (
            "factor_conditions", "event_conditions", "regime_conditions", "parameter_dependencies", "pit_requirements",
        ):
            data[key] = tuple(data.get(key, ()))
        return cls(**data)


@dataclass(frozen=True)
class QualifiedCandidateRow:
    symbol: str
    generated_at: str
    available_at: str
    eligible_at: str | None
    predicate_results: dict[str, Any]
    primary_score: float | None
    confirmation_score: float | None
    qualified: bool
    qualification_reason: str
    row: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("row", None)
        return _jsonable(data)


@dataclass(frozen=True)
class SemanticCandidateRecord:
    candidate: StrategyCandidateSpec
    parent_candidate_id: str
    previous_preregistration_hash: str
    semantic_change_reason: str
    signal_predicate: SignalPredicateSpec
    exit_predicate: ExitPredicateSpec
    semantic_status: str
    phase4_eligible: bool
    semantic_fingerprint: str
    preregistration_hash: str
    created_at: str

    def __post_init__(self) -> None:
        if self.semantic_status not in SEMANTIC_STATUSES:
            raise SemanticContractError(f"unsupported candidate semantic status: {self.semantic_status}")
        if self.phase4_eligible != (self.semantic_status in PHASE4_STATUSES):
            raise SemanticContractError("phase4 eligibility must derive from semantic status")
        if self.candidate.candidate_id == self.parent_candidate_id:
            raise SemanticContractError("semantic remediation must create a new candidate version")
        _validate_contract(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


def _condition(factor_id: str, direction: str) -> dict[str, Any] | None:
    direction = direction.upper()
    if factor_id in ZERO_CENTRED_FACTORS:
        return {
            "factor_id": factor_id,
            "operator": "GT" if direction == "POSITIVE" else "LT",
            "value": 0.0,
            "value_source": "FACTOR_DEFINED_ZERO_BOUNDARY",
        }
    if factor_id in RATIO_FACTORS:
        return {
            "factor_id": factor_id,
            "operator": "GT" if direction == "POSITIVE" else "LT",
            "value": 1.0,
            "value_source": "FACTOR_DEFINED_NEUTRAL_RATIO",
        }
    if factor_id in STRUCTURAL_BOUNDARY_FACTORS and direction == "POSITIVE":
        return {
            "factor_id": factor_id,
            "operator": "GE",
            "value": 1.0,
            "value_source": "FACTOR_DEFINED_TRAILING_HIGH_BOUNDARY",
        }
    return None


def _event_conditions(candidate: StrategyCandidateSpec) -> tuple[dict[str, Any], ...]:
    return tuple({
        "event_id": str(event_id),
        "event_time_semantics": "TRADE_DATE",
        "available_at_semantics": "T_CLOSE",
        "required": True,
        "trade_date_field": "event_trade_date",
    } for event_id in candidate.signal_logic.get("event_dependencies", ()))


def _regime_conditions(hypothesis: Mapping[str, Any], candidate: StrategyCandidateSpec) -> tuple[tuple[dict[str, Any], ...], str]:
    regimes = tuple(str(item) for item in hypothesis.get("market_regime", ()))
    # The frozen hypotheses describe regime context, not an executable entry
    # gate.  CROWDING is the one exception: it names the required regime but
    # no deterministic implementation exists in the current candidate layer.
    if str(hypothesis.get("hypothesis_type")) == "CROWDING" and regimes:
        return (tuple({
            "mode": "HARD_GATE",
            "regime_field": "regime_label",
            "allowed_values": list(regimes),
            "source": "FROZEN_HYPOTHESIS_REGIME_UNRESOLVED",
        } for _ in [0]), "BLOCKED_REGIME")
    return (({
        "mode": "DIAGNOSTIC_ONLY",
        "declared_regimes": list(regimes),
        "source": "HYPOTHESIS_MARKET_REGIME_CONTEXT_ONLY",
    },), "REGIME_DIAGNOSTIC_ONLY")


def _is_overreaction_mechanism(mechanism: str) -> bool:
    return mechanism.lower() in {"mean reversion", "short term reversal"}


def _predicate_type(candidate: StrategyCandidateSpec) -> str:
    if candidate.signal_logic.get("event_dependencies"):
        return "EVENT"
    if candidate.strategy_family == "DAILY_CROSS_SECTIONAL":
        return "RANK_ONLY"
    if candidate.mechanism.lower() in {"breakout", "price structure", "volatility compression"}:
        return "STRUCTURAL"
    return "COMPOSITE" if len(candidate.factor_bindings) > 1 else "THRESHOLD"


def _semantic_status(
    candidate: StrategyCandidateSpec,
    factor_conditions: Sequence[dict[str, Any]],
    interaction_conditions: Sequence[dict[str, Any]],
    regime_status: str,
) -> tuple[str, str]:
    if regime_status == "BLOCKED_REGIME":
        return "BLOCKED_REGIME", "The frozen hypothesis names a hard regime but no deterministic regime implementation is available."
    if candidate.strategy_family == "DAILY_CROSS_SECTIONAL":
        return "SEMANTIC_RANK_ONLY_VALID", "The source hypothesis is explicitly cross-sectional; PIT-universe qualification followed by factor ranking is the entry semantics."
    if candidate.signal_logic.get("event_dependencies"):
        return "SEMANTIC_EVENT_VALID", "The event occurrence and its PIT available_at/trade-date contract qualify rows before ranking."
    if _is_overreaction_mechanism(candidate.mechanism):
        return "BLOCKED_ENTRY_PREDICATE", "The hypothesis requires an overreaction magnitude, but the frozen factors provide no legal pre-registered threshold."
    if not factor_conditions:
        return "BLOCKED_ENTRY_PREDICATE", "The primary continuous factor has no factor-defined executable boundary."
    unresolved_context_roles = {
        "CONTEXT_ONLY", "FILTER", "RISK_FILTER", "LIQUIDITY_FILTER", "REGIME_FILTER",
        "EXECUTION_FILTER", "EXIT_INFORMATION",
    }
    if any(item.get("role") in unresolved_context_roles for item in candidate.factor_bindings) and not interaction_conditions:
        return "BLOCKED_INTERACTION", "A CONTEXT_ONLY factor is declared but no legal interaction predicate or gate is defined."
    if any(item.get("role") == "CONFIRMATION" for item in candidate.factor_bindings) and len(interaction_conditions) < sum(item.get("role") == "CONFIRMATION" for item in candidate.factor_bindings):
        return "BLOCKED_INTERACTION", "The confirmation role cannot be compiled into a gate without inventing a threshold."
    return "SEMANTIC_EXECUTABLE", "All entry conditions use factor-defined neutral/structural boundaries and are evaluated before ranking."


def _exit_spec(candidate: StrategyCandidateSpec, hypothesis: Mapping[str, Any]) -> ExitPredicateSpec:
    old_type = str(candidate.exit_rule.get("type", "FIXED_HOLD"))
    conditions: list[dict[str, Any]] = []
    exit_type = "FIXED_HOLD"
    logic = "FIXED_HOLD"
    if old_type == "PRIMARY_ALPHA_INVALIDATION":
        exit_type = "STRUCTURE_INVALIDATION"
        logic = "OR"
        for binding in candidate.factor_bindings:
            factor_id = str(binding["factor_id"])
            if factor_id == "DONCHIAN_POSITION_20":
                conditions.append({"factor_id": factor_id, "operator": "LT", "value": 1.0, "value_source": "FACTOR_DEFINED_TRAILING_HIGH_BOUNDARY"})
            elif factor_id == "GAP_SIZE":
                conditions.append({"factor_id": factor_id, "operator": "GE", "value": 0.0, "value_source": "FACTOR_DEFINED_ZERO_BOUNDARY"})
            elif factor_id == "MA_DISTANCE_20":
                conditions.append({"factor_id": factor_id, "operator": "LE", "value": 0.0, "value_source": "FACTOR_DEFINED_ZERO_BOUNDARY"})
            elif factor_id == "MA_SLOPE_20":
                conditions.append({"factor_id": factor_id, "operator": "LE", "value": 0.0, "value_source": "FACTOR_DEFINED_ZERO_BOUNDARY"})
        if not conditions:
            return ExitPredicateSpec(
                f"EXIT_{candidate.candidate_id}", "STRUCTURE_INVALIDATION", hypothesis["hypothesis_id"], candidate.candidate_id,
                "OR", tuple(), tuple(), tuple(), {"available_at": "FACTOR_AVAILABLE_AT<=GENERATED_AT"},
                ("holding_period_days",), {"holding_period_days": "candidate.parameter_spec"},
                ("PIT factor availability", "no future shift"), "BLOCKED_EXIT_PREDICATE",
                "The frozen invalidation label has no concrete factor predicate.",
            )
    return ExitPredicateSpec(
        predicate_id=f"EXIT_{candidate.candidate_id}",
        exit_type=exit_type,
        source_hypothesis_id=str(hypothesis["hypothesis_id"]),
        source_candidate_id=candidate.candidate_id,
        logic=logic,
        factor_conditions=tuple(conditions),
        event_conditions=tuple(),
        regime_conditions=tuple(),
        availability_contract={"available_at": "FACTOR_AVAILABLE_AT<=GENERATED_AT"},
        parameter_dependencies=("holding_period_days",),
        parameter_source={"holding_period_days": "candidate.parameter_spec"},
        pit_requirements=("PIT factor availability", "no future shift"),
        semantic_status="COMPLETE",
        explanation=(
            "Exit occurs at entry_session_index + holding_period_days using the trading calendar."
            if exit_type == "FIXED_HOLD" else
            "Exit occurs when the declared structural factor crosses its factor-defined invalidation boundary."
        ),
    )


def _semantic_fingerprint(predicate: SignalPredicateSpec, exit_predicate: ExitPredicateSpec) -> str:
    return _stable_hash({"signal_predicate": predicate.to_dict(), "exit_predicate": exit_predicate.to_dict()})


def build_semantic_record(candidate: StrategyCandidateSpec, hypothesis: Mapping[str, Any]) -> SemanticCandidateRecord:
    PerformanceBlindGuard.assert_blind(candidate.to_dict())
    PerformanceBlindGuard.assert_blind(hypothesis)
    primary = [item for item in candidate.factor_bindings if item.get("role") == "PRIMARY_ALPHA"]
    confirmations = [item for item in candidate.factor_bindings if item.get("role") == "CONFIRMATION"]
    factor_conditions: list[dict[str, Any]] = []
    for binding in primary:
        condition = _condition(str(binding["factor_id"]), str(binding.get("direction", "POSITIVE")))
        if condition is not None:
            factor_conditions.append(condition)
    interaction_conditions: list[dict[str, Any]] = []
    for binding in confirmations:
        condition = _condition(str(binding["factor_id"]), str(binding.get("direction", "POSITIVE")))
        if condition is not None:
            interaction_conditions.append({"type": "CONFIRMATION_GATE", **condition, "source": "FACTOR_DEFINED_BOUNDARY"})

    predicate_type = _predicate_type(candidate)
    regime_conditions, regime_status = _regime_conditions(hypothesis, candidate)
    status, explanation = _semantic_status(candidate, factor_conditions, interaction_conditions, regime_status)
    event_conditions = _event_conditions(candidate)
    ranking_factor = primary[0]["factor_id"] if primary else candidate.factor_bindings[0]["factor_id"]
    ranking_direction = primary[0].get("direction", "POSITIVE") if primary else candidate.factor_bindings[0].get("direction", "POSITIVE")
    predicate = SignalPredicateSpec(
        predicate_id=f"SIG_{candidate.candidate_id}",
        predicate_type=predicate_type,
        source_hypothesis_id=str(hypothesis["hypothesis_id"]),
        source_candidate_id=candidate.candidate_id,
        logic="RANK_ONLY" if predicate_type == "RANK_ONLY" else "AND",
        factor_conditions=tuple(factor_conditions),
        event_conditions=event_conditions,
        regime_conditions=regime_conditions,
        interaction_conditions=tuple(interaction_conditions),
        eligibility_conditions=({
            "type": "PIT_UNIVERSE",
            "universe_eligible": True,
            "universe_as_of_field": "universe_as_of",
        }, {
            "type": "FINITE_FACTORS",
            "factor_available_at": "<= generated_at",
        }, {
            "type": "EXECUTION",
            "tradable": True,
            "affordable": True,
        }),
        availability_contract={
            "generated_at": "T_CLOSE_AFTER_ALL_REQUIRED_FACTORS_AVAILABLE",
            "factor_rule": "ALL_FACTOR_AVAILABLE_AT<=GENERATED_AT",
            "event_rule": "ALL_EVENT_AVAILABLE_AT<=GENERATED_AT",
            "trade_date_rule": "EVENT_TRADE_DATE==GENERATED_TRADE_DATE",
        },
        parameter_dependencies=("selection_top_n",),
        parameter_source={"selection_top_n": "GENERIC_10K_CONTRACT"},
        pit_requirements=("PIT universe as-of generated_at", "factor available_at <= generated_at", "no future shift"),
        semantic_status=status,
        explanation=explanation,
    )
    signal_logic = dict(candidate.signal_logic)
    signal_logic.update({
        "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
        "semantic_status": status,
        "signal_semantics": "CROSS_SECTIONAL_ALWAYS_RANK" if predicate_type == "RANK_ONLY" else "QUALIFY_THEN_RANK",
        "predicate_id": predicate.predicate_id,
        "pipeline": [
            "PIT_UNIVERSE",
            "AVAILABILITY",
            "EVENT_ELIGIBILITY",
            "REGIME_ELIGIBILITY",
            "SIGNAL_PREDICATE",
            "FACTOR_INTERACTION",
            "RANKING",
            "TOP_N_SELECTION",
            "EXECUTION_ELIGIBILITY",
        ],
        "ranking_factor_id": ranking_factor,
        "ranking_direction": ranking_direction,
    })
    exit_predicate = _exit_spec(candidate, hypothesis)
    new_exit_rule = dict(candidate.exit_rule)
    new_exit_rule.update({
        "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
        "predicate_id": exit_predicate.predicate_id,
        "type": exit_predicate.exit_type,
        "factor_conditions": list(exit_predicate.factor_conditions),
        "fixed_hold_rule": "current_session_index - entry_session_index >= holding_period_days",
        "external_exit_boolean": "FORBIDDEN",
    })
    payload = candidate.to_dict()
    payload.update({
        "candidate_id": candidate.candidate_id.rsplit("_V1", 1)[0] + "_V2",
        "candidate_version": "v2",
        "name": candidate.name.replace("Candidate V1", "Candidate V2 Semantic Contract"),
        "description": candidate.description + " Semantic qualification is versioned before performance validation.",
        "signal_logic": signal_logic,
        "entry_rule": "Signal qualification at T close, then declared ranking and Top-N selection; execute no earlier than next session open.",
        "exit_rule": new_exit_rule,
        "strategy_dsl": {
            **candidate.strategy_dsl,
            "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
            "signal_predicate_id": predicate.predicate_id,
            "exit_predicate_id": exit_predicate.predicate_id,
            "rule_graph": signal_logic["pipeline"],
        },
    })
    new_candidate = StrategyCandidateSpec.create(payload)
    semantic_fp = _semantic_fingerprint(predicate, exit_predicate)
    return SemanticCandidateRecord(
        candidate=new_candidate,
        parent_candidate_id=candidate.candidate_id,
        previous_preregistration_hash=candidate.preregistration_hash,
        semantic_change_reason="Closed conceptual entry/exit gap with pre-performance predicate contracts; no performance input was read.",
        signal_predicate=predicate,
        exit_predicate=exit_predicate,
        semantic_status=status,
        phase4_eligible=status in PHASE4_STATUSES and exit_predicate.semantic_status == "COMPLETE",
        semantic_fingerprint=semantic_fp,
        preregistration_hash=new_candidate.preregistration_hash,
        created_at=candidate.created_at,
    )


class CompiledStrategyCandidateV2:
    def __init__(self, record: SemanticCandidateRecord):
        self.record = record
        self.spec = record.candidate
        self.signal_predicate = record.signal_predicate
        self.exit_predicate = record.exit_predicate

    @staticmethod
    def _compare(value: float, operator: str, target: float) -> bool:
        return {
            "GT": value > target,
            "GE": value >= target,
            "LT": value < target,
            "LE": value <= target,
            "EQ": value == target,
            "NE": value != target,
        }.get(operator, False)

    def _factor_condition(self, condition: Mapping[str, Any], values: Mapping[str, float]) -> bool:
        factor_id = str(condition["factor_id"])
        if factor_id not in values:
            return False
        return self._compare(float(values[factor_id]), str(condition["operator"]), float(condition["value"]))

    def _is_rank_only(self) -> bool:
        return self.signal_predicate.predicate_type == "RANK_ONLY" or self.signal_predicate.logic == "RANK_ONLY"

    @staticmethod
    def _directional_percentiles(indexed_values: Sequence[tuple[int, float, str]], direction: str) -> dict[int, float]:
        if not indexed_values:
            return {}
        direction = direction.upper()
        if direction in {"DESCENDING", "POSITIVE"}:
            direction = "DESC"
        elif direction in {"ASCENDING", "NEGATIVE"}:
            direction = "ASC"
        if direction not in {"ASC", "DESC"}:
            raise CandidateCompilationError(f"unsupported cross-sectional ranking direction: {direction}")
        ordered = sorted(indexed_values, key=lambda item: (item[1], item[2]))
        sample_size = len(ordered)
        if sample_size == 1:
            return {ordered[0][0]: 1.0}
        return {
            index: (position / (sample_size - 1) if direction == "DESC" else 1.0 - position / (sample_size - 1))
            for position, (index, _, _) in enumerate(ordered)
        }

    def _composite_ranking_scores(self, qualified: Sequence[QualifiedCandidateRow]) -> dict[int, float] | None:
        if not self._is_rank_only():
            return None
        ranking_rule = self.spec.ranking_rule
        if str(ranking_rule.get("type", "")).upper() != "COMPOSITE_PERCENTILE":
            return None
        if str(ranking_rule.get("weighting", "")).upper() != "EQUAL":
            raise CandidateCompilationError("cross-sectional composite ranking must use equal weighting")
        components = ranking_rule.get("components")
        if not isinstance(components, Sequence) or isinstance(components, (str, bytes)) or not components:
            raise CandidateCompilationError("cross-sectional composite ranking requires components")
        component_scores: list[dict[int, float]] = []
        for component in components:
            if not isinstance(component, Mapping):
                raise CandidateCompilationError("cross-sectional ranking component must be an object")
            factor_id = str(component.get("factor_id", ""))
            if not factor_id:
                raise CandidateCompilationError("cross-sectional ranking component requires factor_id")
            indexed_values: list[tuple[int, float, str]] = []
            for item in qualified:
                try:
                    value = float(item.row["factor_values"][factor_id])
                except (KeyError, TypeError, ValueError):
                    raise CandidateCompilationError(f"missing cross-sectional ranking factor: {factor_id}") from None
                if not math.isfinite(value):
                    raise CandidateCompilationError(f"non-finite cross-sectional ranking factor: {factor_id}")
                indexed_values.append((id(item), value, item.symbol))
            component_scores.append(self._directional_percentiles(
                indexed_values,
                str(component.get("direction", "")),
            ))
        return {
            id(item): sum(scores[id(item)] for scores in component_scores) / len(component_scores)
            for item in qualified
        }

    @staticmethod
    def _selection_score(item: QualifiedCandidateRow, ranking_scores: Mapping[int, float] | None) -> float:
        if ranking_scores is not None:
            return float(ranking_scores[id(item)])
        return float(item.primary_score or 0.0)

    def _base_values(self, row: Mapping[str, Any]) -> tuple[str, str, str, dict[str, float], str | None]:
        symbol = str(row.get("symbol", ""))
        if not symbol:
            return "", "", "", {}, "MISSING_SYMBOL"
        try:
            generated = _timestamp_text(row.get("generated_at", row.get("available_at")))
            available = _timestamp_text(row.get("available_at", generated))
        except Exception:
            return symbol, "", "", {}, "INVALID_TIMESTAMP"
        if _timestamp(available) > _timestamp(generated):
            return symbol, generated, available, {}, "AVAILABLE_AFTER_GENERATED"
        factor_values = row.get("factor_values", {})
        factor_available = row.get("factor_available_at", {})
        if not isinstance(factor_values, Mapping):
            return symbol, generated, available, {}, "INVALID_FACTOR_VALUES"
        if not isinstance(factor_available, Mapping):
            return symbol, generated, available, {}, "FACTOR_AVAILABLE_AT_MISSING"
        values: dict[str, float] = {}
        for binding in self.spec.factor_bindings:
            factor_id = str(binding["factor_id"])
            try:
                value = float(factor_values[factor_id])
            except (KeyError, TypeError, ValueError):
                return symbol, generated, available, {}, f"MISSING_FACTOR:{factor_id}"
            if not math.isfinite(value):
                return symbol, generated, available, {}, f"NON_FINITE_FACTOR:{factor_id}"
            if factor_id not in factor_available:
                return symbol, generated, available, {}, f"FACTOR_AVAILABLE_AT_MISSING:{factor_id}"
            try:
                if _timestamp(factor_available[factor_id]) > _timestamp(generated):
                    return symbol, generated, available, {}, f"FUTURE_FACTOR_AVAILABLE_AT:{factor_id}"
            except Exception:
                return symbol, generated, available, {}, f"INVALID_FACTOR_AVAILABLE_AT:{factor_id}"
            values[factor_id] = value
        if row.get("universe_eligible") is not True:
            return symbol, generated, available, values, "PIT_UNIVERSE_FALSE"
        universe_as_of = row.get("universe_as_of")
        if universe_as_of is None:
            return symbol, generated, available, values, "PIT_UNIVERSE_AS_OF_MISSING"
        try:
            if _timestamp(universe_as_of).date() != _timestamp(generated).date():
                return symbol, generated, available, values, "PIT_UNIVERSE_DATE_MISMATCH"
        except Exception:
            return symbol, generated, available, values, "INVALID_PIT_UNIVERSE_AS_OF"
        if row.get("tradable") is not True:
            return symbol, generated, available, values, "TRADABLE_FALSE"
        if row.get("affordable") is not True:
            return symbol, generated, available, values, "AFFORDABLE_FALSE"
        return symbol, generated, available, values, None

    def _event_passes(self, row: Mapping[str, Any], generated: str) -> tuple[bool, str]:
        generated_date = _timestamp(generated).date()
        for condition in self.signal_predicate.event_conditions:
            event_id = str(condition["event_id"])
            if str(row.get("event_id", "")) != event_id:
                return False, f"EVENT_MISSING:{event_id}"
            event_available = row.get("event_available_at")
            if event_available is None:
                return False, f"EVENT_AVAILABLE_AT_MISSING:{event_id}"
            try:
                if _timestamp(event_available) > _timestamp(generated):
                    return False, f"EVENT_AVAILABLE_AT_FUTURE:{event_id}"
            except Exception:
                return False, f"EVENT_AVAILABLE_AT_INVALID:{event_id}"
            event_date = row.get(str(condition.get("trade_date_field", "event_trade_date")))
            if event_date is None:
                return False, f"EVENT_TRADE_DATE_MISSING:{event_id}"
            try:
                if _timestamp(event_date).date() != generated_date:
                    return False, f"EVENT_TRADE_DATE_MISMATCH:{event_id}"
            except Exception:
                return False, f"EVENT_TRADE_DATE_INVALID:{event_id}"
        return True, "EVENT_ELIGIBLE" if self.signal_predicate.event_conditions else "NO_EVENT_REQUIRED"

    def _regime_passes(self, row: Mapping[str, Any]) -> tuple[bool, str]:
        for condition in self.signal_predicate.regime_conditions:
            if str(condition.get("mode")) == "DIAGNOSTIC_ONLY":
                continue
            if str(condition.get("mode")) != "HARD_GATE":
                return False, "REGIME_SPEC_UNRESOLVED"
            value = row.get(str(condition.get("regime_field", "regime_label")))
            if value not in condition.get("allowed_values", []):
                return False, "REGIME_GATE_FALSE"
        return True, "REGIME_ELIGIBLE"

    def qualify_rows(self, rows: Iterable[Mapping[str, Any]]) -> list[QualifiedCandidateRow]:
        qualified: list[QualifiedCandidateRow] = []
        for row in rows:
            PerformanceBlindGuard.assert_blind(row)
            symbol, generated, available, values, reason = self._base_values(row)
            eligible_at: str | None = None
            if reason is None:
                try:
                    eligible_at = _timestamp_text(row.get("next_session_open"))
                except Exception:
                    reason = "NEXT_SESSION_OPEN_MISSING"
            predicate_results: dict[str, Any] = {"base": reason is None}
            if reason is None:
                event_ok, event_reason = self._event_passes(row, generated)
                regime_ok, regime_reason = self._regime_passes(row)
                if self._is_rank_only():
                    factor_ok = True
                    interaction_ok = True
                else:
                    factor_ok = all(self._factor_condition(item, values) for item in self.signal_predicate.factor_conditions)
                    interaction_ok = all(self._factor_condition(item, values) for item in self.signal_predicate.interaction_conditions)
                predicate_results.update({"event": event_ok, "regime": regime_ok, "factor": factor_ok, "interaction": interaction_ok})
                if self.record.semantic_status not in PHASE4_STATUSES:
                    reason = f"BLOCKED_SEMANTIC:{self.record.semantic_status}"
                elif not event_ok:
                    reason = event_reason
                elif not regime_ok:
                    reason = regime_reason
                elif not factor_ok:
                    reason = "SIGNAL_FACTOR_CONDITION_FALSE"
                elif not interaction_ok:
                    reason = "CONFIRMATION_GATE_FALSE"
                else:
                    reason = "QUALIFIED"
            primary = self._score(values, "PRIMARY_ALPHA") if values else None
            confirmation = self._score(values, "CONFIRMATION") if values else None
            qualified.append(QualifiedCandidateRow(
                symbol=symbol,
                generated_at=generated,
                available_at=available,
                eligible_at=eligible_at,
                predicate_results=predicate_results,
                primary_score=primary,
                confirmation_score=confirmation,
                qualified=reason == "QUALIFIED",
                qualification_reason=reason or "QUALIFIED",
                row=row,
            ))
        return qualified

    def _score(self, values: Mapping[str, float], role: str) -> float:
        bindings = [item for item in self.spec.factor_bindings if item.get("role") == role]
        if not bindings and role == "PRIMARY_ALPHA":
            ranking_id = self.spec.signal_logic.get("ranking_factor_id")
            bindings = [item for item in self.spec.factor_bindings if item.get("factor_id") == ranking_id]
        scores = []
        for binding in bindings:
            value = float(values[binding["factor_id"]])
            scores.append(-value if str(binding.get("direction", "POSITIVE")) == "NEGATIVE" else value)
        return sum(scores) / len(scores) if scores else 0.0

    def _exit_passes(self, row: Mapping[str, Any], values: Mapping[str, float]) -> tuple[bool, str]:
        if not bool(row.get("position_open", False)):
            return False, "NO_OPEN_POSITION"
        if self.exit_predicate.semantic_status != "COMPLETE":
            return False, "BLOCKED_EXIT_PREDICATE"
        if self.exit_predicate.exit_type == "FIXED_HOLD":
            entry_index = row.get("entry_session_index")
            current_index = row.get("current_session_index")
            if entry_index is None or current_index is None:
                return False, "FIXED_HOLD_SESSION_INDEX_MISSING"
            try:
                due = int(current_index) - int(entry_index) >= int(self.spec.holding_period)
            except (TypeError, ValueError):
                return False, "FIXED_HOLD_SESSION_INDEX_INVALID"
            return due, "FIXED_HOLD_DUE" if due else "FIXED_HOLD_NOT_DUE"
        checks = [self._factor_condition(condition, values) for condition in self.exit_predicate.factor_conditions]
        due = any(checks) if self.exit_predicate.logic == "OR" else all(checks)
        return due, "STRUCTURE_INVALIDATION" if due else "STRUCTURE_INTACT"

    def emit_entry_signals(self, rows: Iterable[Mapping[str, Any]]) -> list[CompiledSignal]:
        """Emit only entry signals; exits are portfolio-lifecycle decisions."""
        qualified = self.qualify_rows(rows)
        remaining = [item for item in qualified if not item.row.get("position_open") and item.qualified]
        ranking_scores = self._composite_ranking_scores([item for item in qualified if item.qualified])
        remaining.sort(key=lambda item: (-self._selection_score(item, ranking_scores), item.symbol))
        top_n = min(int(self.spec.selection_rule.get("top_n", self.spec.max_positions)), self.spec.max_positions)
        outputs: list[CompiledSignal] = []
        for rank, item in enumerate(remaining[:top_n], start=1):
            selection_score = self._selection_score(item, ranking_scores)
            outputs.append(CompiledSignal(
                strategy_id=self.spec.candidate_id,
                candidate_id=self.spec.candidate_id,
                symbol=item.symbol,
                action="BUY",
                generated_at=item.generated_at,
                available_at=item.available_at,
                eligible_at=item.eligible_at or item.generated_at,
                score=selection_score,
                rank=rank,
                reason="QUALIFIED_SIGNAL_THEN_RANKED",
                source_factors=tuple(binding["factor_id"] for binding in self.spec.factor_bindings),
                metadata={
                    "qualified": True,
                    "qualification_reason": item.qualification_reason,
                    "predicate_id": self.signal_predicate.predicate_id,
                    "signal_semantics": self.spec.signal_logic.get("signal_semantics"),
                    "ranking_score": selection_score,
                },
            ))
        return outputs

    def emit_signals(self, rows: Iterable[Mapping[str, Any]]) -> list[CompiledSignal]:
        rows = list(rows)
        qualified = self.qualify_rows(rows)
        outputs: list[CompiledSignal] = []
        remaining: list[QualifiedCandidateRow] = []
        for item in qualified:
            values = item.row.get("factor_values", {})
            if item.row.get("position_open"):
                due, exit_reason = self._exit_passes(item.row, values)
                if due:
                    outputs.append(CompiledSignal(
                        strategy_id=self.spec.candidate_id,
                        candidate_id=self.spec.candidate_id,
                        symbol=item.symbol,
                        action="SELL",
                        generated_at=item.generated_at,
                        available_at=item.available_at,
                        eligible_at=item.eligible_at or item.generated_at,
                        score=float(item.primary_score or 0.0),
                        rank=None,
                        reason=exit_reason,
                        source_factors=tuple(binding["factor_id"] for binding in self.spec.factor_bindings),
                        metadata={"exit_type": self.exit_predicate.exit_type, "qualification_reason": item.qualification_reason},
                    ))
            elif item.qualified:
                remaining.append(item)
        ranking_scores = self._composite_ranking_scores([item for item in qualified if item.qualified])
        remaining.sort(key=lambda item: (-self._selection_score(item, ranking_scores), item.symbol))
        top_n = min(int(self.spec.selection_rule.get("top_n", self.spec.max_positions)), self.spec.max_positions)
        for rank, item in enumerate(remaining[:top_n], start=1):
            selection_score = self._selection_score(item, ranking_scores)
            outputs.append(CompiledSignal(
                strategy_id=self.spec.candidate_id,
                candidate_id=self.spec.candidate_id,
                symbol=item.symbol,
                action="BUY",
                generated_at=item.generated_at,
                available_at=item.available_at,
                eligible_at=item.eligible_at or item.generated_at,
                score=selection_score,
                rank=rank,
                reason="QUALIFIED_SIGNAL_THEN_RANKED",
                source_factors=tuple(binding["factor_id"] for binding in self.spec.factor_bindings),
                metadata={
                    "qualified": True,
                    "qualification_reason": item.qualification_reason,
                    "predicate_id": self.signal_predicate.predicate_id,
                    "signal_semantics": self.spec.signal_logic.get("signal_semantics"),
                    "ranking_score": selection_score,
                },
            ))
        return outputs


class StrategyCandidateCompilerV2:
    VERSION = "STRATEGY_CANDIDATE_COMPILER_V2"

    def compile(self, record: SemanticCandidateRecord) -> CompiledStrategyCandidateV2:
        PerformanceBlindGuard.assert_blind(record.to_dict())
        if record.candidate.entry_timing.get("same_bar_entry"):
            raise CandidateCompilationError("same-bar entry is forbidden")
        if record.exit_predicate.exit_type == "FIXED_HOLD" and record.exit_predicate.logic != "FIXED_HOLD":
            raise CandidateCompilationError("fixed-hold exit must use FIXED_HOLD logic")
        return CompiledStrategyCandidateV2(record)


def semantic_registry_hash(records: Sequence[SemanticCandidateRecord]) -> str:
    return hashlib.sha256(json.dumps([record.to_dict() for record in records], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
