"""Phase 2 AI Hypothesis Generator contracts and deterministic guards.

The generator produces research hypotheses only.  It has no backtest,
performance-ranking, broker, strategy-code, or recommendation dependency.
LLM output can enter through ``parse_llm_payload`` but every material status
is resolved again from the unified factor/event registries.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import uuid

import pandas as pd

from .event import EventRegistry
from .unified_factor import UnifiedFactorDefinition, UnifiedFactorRegistry, canonical_json


SCHEMA_VERSION = "alpha-hypothesis-spec-v1"
GENERATOR_VERSION = "AI_HYPOTHESIS_GENERATOR_V1"
CREATED_AT = "2026-08-23"
VIBE_COMMIT = "e86eb283b409f5521a39b855cef23aad4eb542a5"

HYPOTHESIS_STATUSES = {
    "DRAFT", "READY_FOR_STRATEGY_BUILD", "BLOCKED_DATA", "BLOCKED_PIT",
    "BLOCKED_FACTOR", "DUPLICATE", "REJECTED_DESIGN",
}
HYPOTHESIS_TYPES = {
    "TREND_CONTINUATION", "MOMENTUM", "SHORT_TERM_REVERSAL", "BREAKOUT",
    "VOLATILITY_COMPRESSION", "VOLATILITY_EXPANSION", "VOLUME_PRICE_CONFIRMATION",
    "LIQUIDITY_PREMIUM", "CROWDING", "EVENT_CONTINUATION", "EVENT_REVERSAL",
    "MARKET_REGIME_CONDITIONAL", "RELATIVE_STRENGTH", "CROSS_SECTIONAL_RANK",
    "PRICE_STRUCTURE", "MEAN_REVERSION", "COMPOSITE",
}
HORIZONS = {"1_3D", "3_5D", "5_10D", "10_20D"}
ROLES = {
    "PRIMARY_ALPHA", "CONFIRMATION", "FILTER", "REGIME_FILTER", "RISK_FILTER",
    "LIQUIDITY_FILTER", "EXECUTION_FILTER", "EXIT_INFORMATION",
}
DIRECTIONS = {"POSITIVE", "NEGATIVE", "CONTEXT_ONLY", "NEUTRAL"}
NOVELTY_CLASSES = {"NOVEL", "CLOSE_VARIANT", "SEMANTIC_DUPLICATE", "EXACT_DUPLICATE"}
READINESS = {"READY_FOR_STRATEGY_BUILD", "BLOCKED_DATA", "BLOCKED_PIT", "BLOCKED_FACTOR"}
RESEARCH_RISKS = {"LOW", "MEDIUM", "HIGH"}
SMALL_CAPITAL_PRIORS = {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}
VALIDATION_COSTS = {"LOW", "MEDIUM", "HIGH"}
FORBIDDEN_PERFORMANCE_KEYS = {
    "return", "returns", "future_return", "future_returns", "strategy_return",
    "strategy_returns", "ic", "rank_ic", "historical_ranking", "test_results",
    "performance_score", "sharpe", "profit_factor", "pnl", "backtest_result",
}
PARAMETER_VARIANT_PATTERNS = (
    re.compile(r"(?:^|\s)(?:>|<|==|>=|<=)\s*\d+\.\d{2,}"),
    re.compile(r"\b(?:best|optimal|optimum|winner|tuned)\b", re.IGNORECASE),
)


class HypothesisValidationError(ValueError):
    """Structured hypothesis payload or deterministic guard failure."""


class UnknownFactorError(HypothesisValidationError):
    """A hypothesis referenced a factor absent from the canonical registry."""


# Backward-compatible ledger contracts retained for the pre-Phase-2 governance
# tests.  The AI generator uses AlphaHypothesisSpec below; these classes keep
# the existing historical hypothesis ledger API intact without widening the
# generator's input surface.
@dataclass
class Hypothesis:
    hypothesis_id: str
    statement: str
    market_logic: str
    expected_direction: str
    factor_dependencies: list = field(default_factory=list)
    event_dependencies: list = field(default_factory=list)
    expected_horizon: str = "3~7D"
    expected_regime: str = "ANY"
    falsification_condition: str = ""
    status: str = "REGISTERED"
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Hypothesis":
        return cls(**dict(payload))


class HypothesisEngine:
    def __init__(self, path: Path = Path("data/research/hypothesis_ledger.jsonl")) -> None:
        self.path = Path(path)

    def register(self, hypothesis: Hypothesis) -> Hypothesis:
        if not hypothesis.hypothesis_id:
            hypothesis.hypothesis_id = f"H-{uuid.uuid4().hex[:8].upper()}"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(hypothesis.to_dict(), ensure_ascii=False) + "\n")
        return hypothesis

    def load(self) -> list[Hypothesis]:
        if not self.path.exists():
            return []
        return [
            Hypothesis.from_dict(json.loads(line))
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def mark(self, hypothesis_id: str, status: str) -> None:
        rows = self.load()
        for hypothesis in rows:
            if hypothesis.hypothesis_id == hypothesis_id:
                hypothesis.status = status
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            for hypothesis in rows:
                handle.write(json.dumps(hypothesis.to_dict(), ensure_ascii=False) + "\n")


@dataclass
class ResearchPlanner:
    """Legacy research budget helper retained for existing governance callers."""

    max_per_family: int = 8
    max_total: int = 90
    planned_families: dict = field(default_factory=lambda: {
        "LHB": 0.15, "LimitUp": 0.15, "MoneyFlow": 0.15, "Sector": 0.15,
        "Sentiment": 0.10, "Volume": 0.10, "PriceVol": 0.10,
        "DynamicGroup": 0.05, "Other": 0.05,
    })

    def family_quota(self, family: str) -> int:
        return self.max_per_family

    def check_budget(self, family: str, used_family: Mapping[str, int], used_total: int) -> tuple[bool, str]:
        if used_total >= self.max_total:
            return False, "TOTAL_BUDGET_EXCEEDED"
        if used_family.get(family, 0) >= self.max_per_family:
            return False, "FAMILY_BUDGET_EXCEEDED"
        return True, "OK"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _tuple(value: Any) -> tuple:
    if value is None:
        return tuple()
    return tuple(value) if isinstance(value, (list, tuple)) else (value,)


def _slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()
    return value[:48] or "HYPOTHESIS"


def _contains_forbidden_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).lower() in FORBIDDEN_PERFORMANCE_KEYS:
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


@dataclass(frozen=True)
class AlphaHypothesisSpec:
    hypothesis_id: str
    version: str
    title: str
    description: str
    mechanism: str
    economic_rationale: str
    behavioral_rationale: str
    market_microstructure_rationale: str
    factor_ids: tuple[str, ...]
    factor_roles: tuple[dict[str, str], ...]
    expected_factor_direction: dict[str, str]
    interaction_logic: str
    target_horizon: str
    expected_holding_period: str
    signal_frequency: str
    market_regime: tuple[str, ...]
    universe_assumptions: tuple[str, ...]
    entry_concept: str
    exit_concept: str
    risk_hypothesis: str
    failure_conditions: tuple[str, ...]
    falsification_tests: tuple[dict[str, str], ...]
    required_data: tuple[str, ...]
    required_frequency: tuple[str, ...]
    pit_requirements: tuple[str, ...]
    implementation_readiness: str
    novelty_class: str
    source_provenance: dict[str, Any]
    inspiration_sources: tuple[str, ...]
    complexity_score: int
    created_at: str
    generator_version: str
    status: str
    hypothesis_type: str
    factor_families: tuple[str, ...]
    event_dependencies: tuple[str, ...]
    parameter_ranges: dict[str, str]
    validation_plan: tuple[str, ...]
    research_risk: str
    small_capital_fit_prior: str
    estimated_validation_cost: str
    failure_awareness: bool
    failure_awareness_explanation: str
    complexity_justification: str
    hypothesis_fingerprint: str
    generator_mode: str
    related_factor_warning: tuple[str, ...] = tuple()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"HYP_[A-Z0-9_]+_V\d+", self.hypothesis_id):
            raise HypothesisValidationError(f"invalid hypothesis_id: {self.hypothesis_id}")
        if not self.version or not self.title.strip() or not self.description.strip():
            raise HypothesisValidationError("hypothesis identity and description are required")
        if self.hypothesis_type not in HYPOTHESIS_TYPES:
            raise HypothesisValidationError(f"unsupported hypothesis type: {self.hypothesis_type}")
        if self.status not in HYPOTHESIS_STATUSES:
            raise HypothesisValidationError(f"unsupported hypothesis status: {self.status}")
        if self.implementation_readiness not in READINESS:
            raise HypothesisValidationError(f"unsupported implementation readiness: {self.implementation_readiness}")
        if self.implementation_readiness != self.status and self.status not in {"DRAFT", "DUPLICATE", "REJECTED_DESIGN"}:
            raise HypothesisValidationError("status must reflect deterministic implementation readiness")
        if self.target_horizon not in HORIZONS or self.expected_holding_period not in HORIZONS:
            raise HypothesisValidationError("unsupported short-horizon value")
        if not self.factor_ids or len(set(self.factor_ids)) != len(self.factor_ids):
            raise HypothesisValidationError("factor_ids must be non-empty and unique")
        if len(self.factor_ids) > 4 and not self.complexity_justification.strip():
            raise HypothesisValidationError("more than four factors requires a complexity justification")
        if self.complexity_score < 1 or self.complexity_score > 5:
            raise HypothesisValidationError("complexity_score must be between 1 and 5")
        role_ids = [str(item.get("factor_id", "")) for item in self.factor_roles]
        if set(role_ids) != set(self.factor_ids) or len(role_ids) != len(set(role_ids)):
            raise HypothesisValidationError("factor_roles must cover each factor exactly once")
        if any(item.get("role") not in ROLES for item in self.factor_roles):
            raise HypothesisValidationError("unknown factor role")
        if not any(item.get("role") == "PRIMARY_ALPHA" for item in self.factor_roles):
            raise HypothesisValidationError("at least one PRIMARY_ALPHA factor is required")
        if set(self.expected_factor_direction) != set(self.factor_ids):
            raise HypothesisValidationError("expected_factor_direction must cover each factor")
        if any(value not in DIRECTIONS for value in self.expected_factor_direction.values()):
            raise HypothesisValidationError("unknown factor direction")
        if self.novelty_class not in NOVELTY_CLASSES:
            raise HypothesisValidationError(f"unknown novelty class: {self.novelty_class}")
        if self.research_risk not in RESEARCH_RISKS or self.small_capital_fit_prior not in SMALL_CAPITAL_PRIORS:
            raise HypothesisValidationError("invalid research risk or small-capital prior")
        if self.estimated_validation_cost not in VALIDATION_COSTS:
            raise HypothesisValidationError("invalid estimated validation cost")
        if not self.failure_conditions or not self.falsification_tests or not self.validation_plan:
            raise HypothesisValidationError("failure conditions, falsification tests, and validation plan are required")
        if not self.required_data or not self.required_frequency or not self.pit_requirements:
            raise HypothesisValidationError("required data/frequency/PIT requirements are required")
        if not self.hypothesis_fingerprint:
            raise HypothesisValidationError("hypothesis fingerprint is required")
        for value in self.parameter_ranges.values():
            if any(pattern.search(str(value)) for pattern in PARAMETER_VARIANT_PATTERNS):
                raise HypothesisValidationError("parameter ranges cannot contain tuned threshold soup")
        forbidden = _contains_forbidden_key(self.to_dict())
        if forbidden:
            raise HypothesisValidationError(f"performance field is forbidden in hypothesis payload: {forbidden}")
        if self.source_provenance.get("source_commit") and self.source_provenance.get("source_type") == "VIBE_TRADING":
            if self.source_provenance["source_commit"] != VIBE_COMMIT:
                raise HypothesisValidationError("Vibe provenance commit mismatch")

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], strict: bool = True) -> "AlphaHypothesisSpec":
        names = {item.name for item in fields(cls)}
        unknown = set(payload) - names
        if strict and unknown:
            raise HypothesisValidationError(f"unknown hypothesis fields: {sorted(unknown)}")
        data = {key: value for key, value in payload.items() if key in names}
        for key in {
            "factor_ids", "market_regime", "universe_assumptions", "failure_conditions", "required_data",
            "required_frequency", "pit_requirements", "inspiration_sources", "event_dependencies",
            "validation_plan", "related_factor_warning",
        }:
            if key in data:
                data[key] = _tuple(data[key])
        if "factor_roles" in data:
            data["factor_roles"] = tuple(dict(item) for item in data["factor_roles"])
        if "falsification_tests" in data:
            data["falsification_tests"] = tuple(dict(item) for item in data["falsification_tests"])
        return cls(**data)


def _fingerprint_payload(spec: AlphaHypothesisSpec, include_parameters: bool = True, include_factor_ids: bool = True) -> dict[str, Any]:
    roles = sorted((item["role"] for item in spec.factor_roles))
    payload = {
        "hypothesis_type": spec.hypothesis_type,
        "mechanism": spec.mechanism,
        "factor_families": sorted(spec.factor_families),
        "roles": roles,
        "target_horizon": spec.target_horizon,
        "market_regime": sorted(spec.market_regime),
        "interaction_logic": spec.interaction_logic,
    }
    if include_factor_ids:
        payload["factor_ids"] = sorted(spec.factor_ids)
    if include_parameters:
        payload["parameter_ranges"] = spec.parameter_ranges
    return payload


def hypothesis_fingerprint(spec: AlphaHypothesisSpec) -> str:
    return hashlib.sha256(canonical_json(_fingerprint_payload(spec)).encode("utf-8")).hexdigest()


def structural_hypothesis_fingerprint(spec: AlphaHypothesisSpec) -> str:
    return hashlib.sha256(canonical_json(_fingerprint_payload(spec, include_parameters=False)).encode("utf-8")).hexdigest()


def family_hypothesis_fingerprint(spec: AlphaHypothesisSpec) -> str:
    return hashlib.sha256(canonical_json(_fingerprint_payload(spec, include_parameters=False, include_factor_ids=False)).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FactorResolution:
    factor_id: str
    exists: bool
    implementation_status: str
    pit_status: str
    data_support_status: str
    family: str
    source_type: str
    source_family: str
    is_external_composite: bool
    eligibility: str


@dataclass(frozen=True)
class ResearchContext:
    factor_registry: UnifiedFactorRegistry
    external_prior: tuple[dict[str, Any], ...]
    event_metadata: tuple[dict[str, Any], ...]
    market_regime_features: tuple[dict[str, Any], ...]
    historical_hypotheses: tuple[dict[str, Any], ...]
    failure_knowledge: tuple[dict[str, Any], ...]
    mechanism_map: tuple[dict[str, Any], ...]
    strategy_mechanism_priors: tuple[dict[str, Any], ...]
    context_manifest: dict[str, Any]


class ResearchContextBuilder:
    """Read research knowledge without loading future returns or performance fields."""

    def __init__(self, root: str | Path = "."):
        self.root = Path(root)

    def _path(self, relative: str) -> Path:
        return self.root / relative

    def _selected_hypotheses(self) -> tuple[dict[str, Any], ...]:
        path = self._path("data/research/hypothesis_ledger.jsonl")
        allowed = {
            "hypothesis_id", "statement", "market_logic", "expected_direction", "factor_dependencies",
            "event_dependencies", "expected_horizon", "expected_regime", "falsification_condition", "status", "created_at",
        }
        rows = []
        if not path.exists():
            return tuple()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            rows.append({key: raw.get(key) for key in sorted(allowed) if key in raw})
        return tuple(rows)

    def _failure_knowledge(self) -> tuple[dict[str, Any], ...]:
        path = self._path("data/research/failure_library.parquet")
        counts: Counter[str] = Counter()
        if path.exists():
            frame = pd.read_parquet(path, columns=["failure_class", "experiment_id"])
            counts.update(str(value) for value in frame["failure_class"].dropna())
        required = [
            ("NO_ALPHA", "Historical experiments explicitly failed to establish a stable alpha; use as a design constraint."),
            ("REGIME_DEPENDENT", "Reject mechanisms that only make sense in one unverified regime unless the regime is explicit."),
            ("WINNER_CONCENTRATION", "Require concentration and leave-one-winner checks in the validation plan."),
            ("EXECUTION_SENSITIVE", "Require availability, price-limit, suspension, and execution-latency checks."),
            ("SMALL_CAPITAL_FAILURE", "Require lot-size, capacity, and affordability checks for the 10K objective."),
            ("HIGH_TURNOVER", "Require holding-period and turnover diagnostics before strategy construction."),
            ("FEE_DESTROYED", "Require cost sensitivity in the future validation plan."),
            ("LOOKAHEAD", "Reject any proposal whose available_at or universe evidence is not explicit."),
            ("PIT_BLOCKED", "Keep incomplete PIT dependencies blocked."),
            ("DATA_BLOCKED", "Keep missing historical data blocked."),
            ("OVERFIT", "Use conceptual parameter ranges and avoid threshold soup."),
            ("DUPLICATE", "Collapse parameter variants and semantic duplicates."),
            ("INSUFFICIENT_TRADES", "Require a predeclared minimum-evidence rule in validation."),
        ]
        records = []
        for failure_class, rule in required:
            records.append({
                "failure_class": failure_class,
                "knowledge_rule": rule,
                "source_count": int(counts.get(failure_class, 0)),
                "source": "data/research/failure_library.parquet" if counts.get(failure_class, 0) else "phase2_failure_taxonomy",
                "performance_values_loaded": False,
            })
        return tuple(records)

    def build(self) -> ResearchContext:
        factor_path = self._path("data/research/unified_factor_registry/registry.json")
        external_path = self._path("data/research/unified_factor_registry/external_mapping.json")
        event_path = self._path("data/research/event_registry/registry.json")
        mechanism_path = self._path("reports/FACTOR_MECHANISM_MAP.csv")
        factor_registry = UnifiedFactorRegistry.read(factor_path)
        external_payload = json.loads(external_path.read_text(encoding="utf-8")) if external_path.exists() else {"mappings": []}
        event_payload = json.loads(event_path.read_text(encoding="utf-8")) if event_path.exists() else {"events": []}
        mechanism_rows = []
        if mechanism_path.exists():
            with mechanism_path.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    mechanism_rows.append({"factor_id": row.get("factor_id"), "primary_mechanism_id": row.get("primary_mechanism_id")})
        market_features = (
            {"feature_id": "INDEX_RETURN_5", "pit_status": "PIT_LIMITED", "available_at": "T_CLOSE"},
            {"feature_id": "INDEX_RETURN_20", "pit_status": "PIT_LIMITED", "available_at": "T_CLOSE"},
            {"feature_id": "INDEX_MA20_DISTANCE", "pit_status": "PIT_LIMITED", "available_at": "T_CLOSE"},
            {"feature_id": "INDEX_MA60_DISTANCE", "pit_status": "PIT_LIMITED", "available_at": "T_CLOSE"},
            {"feature_id": "MARKET_BREADTH", "pit_status": "PIT_LIMITED", "available_at": "T_CLOSE"},
            {"feature_id": "ADVANCE_DECLINE_RATIO", "pit_status": "PIT_LIMITED", "available_at": "T_CLOSE"},
            {"feature_id": "MARKET_VOLATILITY", "pit_status": "PIT_LIMITED", "available_at": "T_CLOSE"},
        )
        strategy_mechanism_priors = (
            {"name": "E_CONSEC_LIMIT", "mechanism": "event continuation / crowding / execution sensitivity", "source": "event registry metadata"},
            {"name": "TightBreakout", "mechanism": "price structure breakout with participation confirmation", "source": "existing strategy mechanism vocabulary"},
            {"name": "LowVol", "mechanism": "volatility and liquidity conditioning", "source": "existing strategy mechanism vocabulary"},
        )
        paths = [factor_path, external_path, event_path, self._path("data/research/hypothesis_ledger.jsonl"), self._path("data/research/failure_library.parquet")]
        manifest = {
            "generator_version": GENERATOR_VERSION,
            "factor_registry_path": str(factor_path),
            "external_prior_path": str(external_path),
            "event_registry_path": str(event_path),
            "historical_hypothesis_path": str(self._path("data/research/hypothesis_ledger.jsonl")),
            "failure_library_path": str(self._path("data/research/failure_library.parquet")),
            "input_paths_exist": {str(path): path.exists() for path in paths},
            "performance_data_used_for_generation": False,
            "future_return_labels_loaded": False,
            "strategy_returns_loaded": False,
            "historical_ranking_loaded": False,
            "test_results_loaded": False,
            "final_test_new_physical_access": 0,
            "final_test_new_analytical_exposure": 0,
            "final_test_new_decision_exposure": 0,
        }
        return ResearchContext(
            factor_registry=factor_registry,
            external_prior=tuple(external_payload.get("mappings", [])),
            event_metadata=tuple(event_payload.get("events", [])),
            market_regime_features=market_features,
            historical_hypotheses=self._selected_hypotheses(),
            failure_knowledge=self._failure_knowledge(),
            mechanism_map=tuple(mechanism_rows),
            strategy_mechanism_priors=strategy_mechanism_priors,
            context_manifest=manifest,
        )


class FactorKnowledgeView:
    def __init__(self, context: ResearchContext):
        self.context = context

    def resolve(self, factor_id: str) -> FactorResolution:
        definition = self.context.factor_registry.get(factor_id)
        if definition is None:
            return FactorResolution(factor_id, False, "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", False, "BLOCKED_FACTOR")
        is_composite = definition.source_type in {"VIBE_TRADING", "ACADEMIC_EXTERNAL"} and definition.source_family in {"alpha101", "gtja191"}
        if definition.data_support_status != "FULL":
            eligibility = "BLOCKED_DATA"
        elif definition.pit_status != "PIT_VERIFIED":
            eligibility = "BLOCKED_PIT"
        elif definition.implementation_status != "EXECUTABLE" or is_composite:
            eligibility = "BLOCKED_FACTOR"
        else:
            eligibility = "EXECUTABLE_FACTOR"
        return FactorResolution(
            factor_id=factor_id,
            exists=True,
            implementation_status=definition.implementation_status,
            pit_status=definition.pit_status,
            data_support_status=definition.data_support_status,
            family=definition.family,
            source_type=definition.source_type,
            source_family=definition.source_family,
            is_external_composite=is_composite,
            eligibility=eligibility,
        )

    def resolve_many(self, factor_ids: Sequence[str]) -> tuple[FactorResolution, ...]:
        return tuple(self.resolve(factor_id) for factor_id in factor_ids)


class ExternalAlphaKnowledgeView:
    def __init__(self, context: ResearchContext):
        self.context = context

    def inspiration(self, external_id: str) -> dict[str, Any] | None:
        for item in self.context.external_prior:
            if item.get("external_alpha_id") == external_id:
                return dict(item)
        return None

    def composite_inspirations(self) -> list[str]:
        return sorted(item["external_alpha_id"] for item in self.context.external_prior if item.get("classification") == "COMPOSITE_ALPHA_EXPRESSION")


class FailureKnowledgeView:
    FAILED_MECHANISM_TYPES = {
        "TREND_CONTINUATION", "MOMENTUM", "SHORT_TERM_REVERSAL", "BREAKOUT",
        "VOLATILITY_COMPRESSION", "VOLUME_PRICE_CONFIRMATION", "EVENT_CONTINUATION",
        "EVENT_REVERSAL", "MEAN_REVERSION", "CROSS_SECTIONAL_RANK",
    }

    def __init__(self, context: ResearchContext):
        self.context = context
        self.classes = {item["failure_class"] for item in context.failure_knowledge}

    def awareness(self, hypothesis_type: str) -> tuple[bool, str]:
        if hypothesis_type in self.FAILED_MECHANISM_TYPES and "NO_ALPHA" in self.classes:
            return True, "Historical failure knowledge was consulted; this design changes the conditioning, role structure, or execution assumption and must pass the declared falsification tests."
        return False, "No directly matching failure class was found; generic failure guards still apply."


class PITReadinessChecker:
    def __init__(self, context: ResearchContext):
        self.context = context
        self.factor_view = FactorKnowledgeView(context)

    def resolve(self, factor_ids: Sequence[str], event_dependencies: Sequence[str] = ()) -> tuple[str, list[str], tuple[FactorResolution, ...]]:
        resolutions = self.factor_view.resolve_many(factor_ids)
        evidence = []
        if any(not item.exists for item in resolutions):
            return "BLOCKED_FACTOR", ["Unknown factor ID is not permitted"], resolutions
        if any(item.data_support_status == "DATA_LIMITED" for item in resolutions):
            return "BLOCKED_DATA", [f"{item.factor_id}: DATA_LIMITED" for item in resolutions if item.data_support_status == "DATA_LIMITED"], resolutions
        if any(item.pit_status != "PIT_VERIFIED" for item in resolutions):
            return "BLOCKED_PIT", [f"{item.factor_id}: {item.pit_status}" for item in resolutions if item.pit_status != "PIT_VERIFIED"], resolutions
        if any(item.eligibility != "EXECUTABLE_FACTOR" for item in resolutions):
            return "BLOCKED_FACTOR", [f"{item.factor_id}: {item.implementation_status}" for item in resolutions if item.eligibility != "EXECUTABLE_FACTOR"], resolutions
        events = {item["event_id"]: item for item in self.context.event_metadata}
        for event_id in event_dependencies:
            event = events.get(event_id)
            if event is None:
                return "BLOCKED_FACTOR", [f"Unknown event ID: {event_id}"], resolutions
            if not event.get("PIT_safe", False):
                return "BLOCKED_PIT", [f"{event_id}: PIT_safe=false"], resolutions
            evidence.append(f"{event_id}: available_at={event.get('available_at_semantics')}")
        evidence.extend(f"{item.factor_id}: PIT_VERIFIED/DATA_FULL/EXECUTABLE" for item in resolutions)
        return "READY_FOR_STRATEGY_BUILD", evidence, resolutions


class ComplexityGuard:
    def validate(self, spec: AlphaHypothesisSpec) -> list[str]:
        errors = []
        primary = sum(item["role"] == "PRIMARY_ALPHA" for item in spec.factor_roles)
        auxiliary = len(spec.factor_roles) - primary
        if primary > 2:
            errors.append("more than two PRIMARY_ALPHA factors")
        if auxiliary > 2:
            errors.append("more than two auxiliary factors")
        if len(spec.factor_ids) > 4 and not spec.complexity_justification.strip():
            errors.append("more than four factors without complexity explanation")
        for key, value in spec.parameter_ranges.items():
            if any(pattern.search(str(value)) for pattern in PARAMETER_VARIANT_PATTERNS):
                errors.append(f"parameter threshold soup: {key}")
        if spec.complexity_score >= 4 and len(spec.factor_ids) <= 1 and not spec.complexity_justification:
            errors.append("complexity score is disproportionate without explanation")
        return errors


class NoveltyChecker:
    def classify(self, spec: AlphaHypothesisSpec, existing: Iterable[AlphaHypothesisSpec]) -> tuple[str, str | None]:
        exact = hypothesis_fingerprint(spec)
        structural = structural_hypothesis_fingerprint(spec)
        family = family_hypothesis_fingerprint(spec)
        for candidate in existing:
            if candidate.hypothesis_fingerprint == exact or hypothesis_fingerprint(candidate) == exact:
                return "EXACT_DUPLICATE", candidate.hypothesis_id
            if structural_hypothesis_fingerprint(candidate) == structural:
                return "SEMANTIC_DUPLICATE", candidate.hypothesis_id
            if family_hypothesis_fingerprint(candidate) == family:
                return "CLOSE_VARIANT", candidate.hypothesis_id
        return "NOVEL", None


class DiversityAudit:
    REQUIRED_TYPES = {
        "TREND_CONTINUATION", "MOMENTUM", "SHORT_TERM_REVERSAL", "BREAKOUT",
        "VOLATILITY_COMPRESSION", "VOLATILITY_EXPANSION", "VOLUME_PRICE_CONFIRMATION",
        "LIQUIDITY_PREMIUM", "EVENT_CONTINUATION", "EVENT_REVERSAL",
        "MARKET_REGIME_CONDITIONAL", "CROSS_SECTIONAL_RANK",
    }

    def audit(self, specs: Sequence[AlphaHypothesisSpec]) -> dict[str, Any]:
        distribution = Counter(spec.hypothesis_type for spec in specs)
        missing = sorted(self.REQUIRED_TYPES - set(distribution))
        dominant = []
        if specs:
            dominant = [key for key, value in distribution.items() if value / len(specs) > 0.5]
        return {
            "hypothesis_count": len(specs),
            "mechanism_distribution": dict(sorted(distribution.items())),
            "required_types_missing": missing,
            "dominant_mechanisms": sorted(dominant),
            "diversity_pass": not missing and not dominant,
        }


class HypothesisDeterministicGuards:
    def __init__(self, context: ResearchContext):
        self.context = context
        self.factor_view = FactorKnowledgeView(context)
        self.pit_checker = PITReadinessChecker(context)
        self.complexity = ComplexityGuard()
        self.novelty = NoveltyChecker()

    def validate(self, spec: AlphaHypothesisSpec, existing: Iterable[AlphaHypothesisSpec] = ()) -> list[str]:
        errors = []
        if spec.hypothesis_fingerprint != hypothesis_fingerprint(spec):
            errors.append("hypothesis fingerprint mismatch")
        forbidden = _contains_forbidden_key(spec.to_dict())
        if forbidden:
            errors.append(f"performance field present: {forbidden}")
        resolutions = self.factor_view.resolve_many(spec.factor_ids)
        if any(not item.exists for item in resolutions):
            errors.append("unknown factor ID")
        if any(item.is_external_composite for item in resolutions):
            errors.append("external composite alpha cannot be a direct hypothesis factor")
        expected, _, _ = self.pit_checker.resolve(spec.factor_ids, spec.event_dependencies)
        if expected != spec.implementation_readiness:
            errors.append(f"implementation readiness mismatch: expected {expected}, received {spec.implementation_readiness}")
        errors.extend(self.complexity.validate(spec))
        if spec.novelty_class in {"EXACT_DUPLICATE", "SEMANTIC_DUPLICATE"} and spec.status not in {"DUPLICATE", "REJECTED_DESIGN"}:
            errors.append("duplicate novelty must not be READY")
        if spec.status == "READY_FOR_STRATEGY_BUILD" and spec.implementation_readiness != "READY_FOR_STRATEGY_BUILD":
            errors.append("blocked hypothesis cannot be READY_FOR_STRATEGY_BUILD")
        return errors

    def assert_valid(self, spec: AlphaHypothesisSpec, existing: Iterable[AlphaHypothesisSpec] = ()) -> None:
        errors = self.validate(spec, existing)
        if errors:
            raise HypothesisValidationError("; ".join(errors))


def parse_llm_payload(payload: str | Mapping[str, Any]) -> AlphaHypothesisSpec:
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise HypothesisValidationError("LLM output is not valid JSON") from exc
    else:
        parsed = dict(payload)
    if not isinstance(parsed, dict):
        raise HypothesisValidationError("LLM output must be a JSON object")
    return AlphaHypothesisSpec.from_dict(parsed, strict=True)


def _roles(*items: tuple[str, str]) -> list[dict[str, str]]:
    return [{"factor_id": factor_id, "role": role} for factor_id, role in items]


def _template(hypothesis_type: str, title: str, factors: list[str], roles: list[dict[str, str]], direction: dict[str, str], *, event_dependencies: list[str] | None = None, horizon: str = "3_5D", regime: list[str] | None = None, mode: str = "MECHANISM_DRIVEN", inspiration: list[str] | None = None, parameter_ranges: dict[str, str] | None = None, description: str | None = None) -> dict[str, Any]:
    return {
        "hypothesis_type": hypothesis_type,
        "title": title,
        "description": description or title,
        "factor_ids": factors,
        "factor_roles": roles,
        "expected_factor_direction": direction,
        "event_dependencies": event_dependencies or [],
        "target_horizon": horizon,
        "expected_holding_period": horizon,
        "market_regime": regime or ["NON_CRASH_OR_EXPLICIT_REGIME"],
        "generator_mode": mode,
        "inspiration_sources": inspiration or [],
        "parameter_ranges": parameter_ranges or {"lookback": "short=5-10 days; medium=20-60 days", "holding_period": horizon},
    }


class MechanismPlanner:
    def plan_acceptance(self) -> list[dict[str, Any]]:
        return [
            _template("TREND_CONTINUATION", "Orderly trend persistence with slope confirmation", ["MA_DISTANCE_20", "MA_SLOPE_20"], _roles(("MA_DISTANCE_20", "PRIMARY_ALPHA"), ("MA_SLOPE_20", "CONFIRMATION")), {"MA_DISTANCE_20": "POSITIVE", "MA_SLOPE_20": "POSITIVE"}),
            _template("MOMENTUM", "Medium-horizon momentum with volatility discipline", ["RETURN_20D", "VOL_20D"], _roles(("RETURN_20D", "PRIMARY_ALPHA"), ("VOL_20D", "RISK_FILTER")), {"RETURN_20D": "POSITIVE", "VOL_20D": "NEGATIVE"}, horizon="5_10D"),
            _template("SHORT_TERM_REVERSAL", "Liquidity-shock reversal outside a crash regime", ["RETURN_5D", "ILLIQUIDITY_AMIHUD"], _roles(("RETURN_5D", "PRIMARY_ALPHA"), ("ILLIQUIDITY_AMIHUD", "LIQUIDITY_FILTER")), {"RETURN_5D": "NEGATIVE", "ILLIQUIDITY_AMIHUD": "CONTEXT_ONLY"}, regime=["NON_CRASH", "LIQUIDITY_SHOCK"], horizon="1_3D"),
            _template("BREAKOUT", "Range exit with independent participation confirmation", ["DONCHIAN_POSITION_20", "VOLUME_RATIO_5_20"], _roles(("DONCHIAN_POSITION_20", "PRIMARY_ALPHA"), ("VOLUME_RATIO_5_20", "CONFIRMATION")), {"DONCHIAN_POSITION_20": "POSITIVE", "VOLUME_RATIO_5_20": "POSITIVE"}),
            _template("VOLATILITY_COMPRESSION", "Compression release with volume expansion", ["RANGE_COMPRESSION_10", "VOLUME_ACCEL"], _roles(("RANGE_COMPRESSION_10", "PRIMARY_ALPHA"), ("VOLUME_ACCEL", "CONFIRMATION")), {"RANGE_COMPRESSION_10": "NEGATIVE", "VOLUME_ACCEL": "POSITIVE"}),
            _template("VOLATILITY_EXPANSION", "Volatility expansion as an information-arrival signal", ["VOL_RATIO_5_20", "VOLUME_RATIO_1_20"], _roles(("VOL_RATIO_5_20", "PRIMARY_ALPHA"), ("VOLUME_RATIO_1_20", "CONFIRMATION")), {"VOL_RATIO_5_20": "POSITIVE", "VOLUME_RATIO_1_20": "POSITIVE"}, horizon="1_3D"),
            _template("VOLUME_PRICE_CONFIRMATION", "Price-volume agreement after a directional move", ["PRICE_VOLUME_CORR", "RETURN_5D"], _roles(("PRICE_VOLUME_CORR", "CONFIRMATION"), ("RETURN_5D", "PRIMARY_ALPHA")), {"PRICE_VOLUME_CORR": "POSITIVE", "RETURN_5D": "POSITIVE"}, horizon="3_5D"),
            _template("LIQUIDITY_PREMIUM", "Illiquidity premium bounded by executable capacity", ["ILLIQUIDITY_AMIHUD", "AVG_AMOUNT_20"], _roles(("ILLIQUIDITY_AMIHUD", "PRIMARY_ALPHA"), ("AVG_AMOUNT_20", "LIQUIDITY_FILTER")), {"ILLIQUIDITY_AMIHUD": "POSITIVE", "AVG_AMOUNT_20": "POSITIVE"}, horizon="5_10D"),
            _template("CROWDING", "Crowding unwind proxy with volume participation", ["VOL_20D", "VOLUME_RATIO_1_20"], _roles(("VOL_20D", "PRIMARY_ALPHA"), ("VOLUME_RATIO_1_20", "RISK_FILTER")), {"VOL_20D": "POSITIVE", "VOLUME_RATIO_1_20": "CONTEXT_ONLY"}, regime=["CROWDED_OR_UNSTABLE"], horizon="1_3D"),
            _template("EVENT_CONTINUATION", "Limit-up continuation only when participation remains liquid", ["VOLUME_RATIO_5_20"], _roles(("VOLUME_RATIO_5_20", "PRIMARY_ALPHA")), {"VOLUME_RATIO_5_20": "POSITIVE"}, event_dependencies=["E_LIMITUP"], horizon="1_3D"),
            _template("EVENT_REVERSAL", "Failed-limit event followed by short-term exhaustion", ["RETURN_3D"], _roles(("RETURN_3D", "PRIMARY_ALPHA")), {"RETURN_3D": "NEGATIVE"}, event_dependencies=["E_FAILEDLIMIT"], regime=["NON_CRASH", "EVENT_REVERSAL"], horizon="1_3D"),
            _template("MARKET_REGIME_CONDITIONAL", "Reversal hypothesis conditional on a PIT market regime", ["RETURN_10D", "INDEX_RETURN_20"], _roles(("RETURN_10D", "PRIMARY_ALPHA"), ("INDEX_RETURN_20", "REGIME_FILTER")), {"RETURN_10D": "NEGATIVE", "INDEX_RETURN_20": "CONTEXT_ONLY"}, mode="FAILURE_AWARE", regime=["NON_CRASH"], horizon="3_5D"),
            _template("RELATIVE_STRENGTH", "Relative strength persistence across a medium horizon", ["RETURN_60D", "MA_DISTANCE_60"], _roles(("RETURN_60D", "PRIMARY_ALPHA"), ("MA_DISTANCE_60", "CONFIRMATION")), {"RETURN_60D": "POSITIVE", "MA_DISTANCE_60": "POSITIVE"}, horizon="5_10D"),
            _template("CROSS_SECTIONAL_RANK", "Cross-sectional illiquidity rank as a research selection lens", ["EXT_ACADEMIC_ACADEMIC_ILLIQ_V1"], _roles(("EXT_ACADEMIC_ACADEMIC_ILLIQ_V1", "PRIMARY_ALPHA")), {"EXT_ACADEMIC_ACADEMIC_ILLIQ_V1": "CONTEXT_ONLY"}, horizon="3_5D", mode="FACTOR_DRIVEN"),
            _template("PRICE_STRUCTURE", "Opening gap interpreted through candle structure", ["GAP_SIZE", "BODY_RATIO"], _roles(("GAP_SIZE", "PRIMARY_ALPHA"), ("BODY_RATIO", "CONFIRMATION")), {"GAP_SIZE": "NEGATIVE", "BODY_RATIO": "CONTEXT_ONLY"}, horizon="1_3D"),
            _template("MEAN_REVERSION", "Range-normalized overreaction mean reversion", ["RETURN_10D", "RANGE_COMPRESSION_20"], _roles(("RETURN_10D", "PRIMARY_ALPHA"), ("RANGE_COMPRESSION_20", "FILTER")), {"RETURN_10D": "NEGATIVE", "RANGE_COMPRESSION_20": "CONTEXT_ONLY"}, horizon="3_5D"),
            _template("COMPOSITE", "Acceleration confirmed by changing participation", ["MOM_ACCEL_5_20", "VOLUME_ACCEL"], _roles(("MOM_ACCEL_5_20", "PRIMARY_ALPHA"), ("VOLUME_ACCEL", "CONFIRMATION")), {"MOM_ACCEL_5_20": "POSITIVE", "VOLUME_ACCEL": "POSITIVE"}, inspiration=["alpha101_010"], horizon="3_5D", mode="EXTERNAL_PRIOR_ADAPTATION"),
            _template("EVENT_REVERSAL", "Sentiment event exhaustion with price reversal", ["RETURN_5D"], _roles(("RETURN_5D", "PRIMARY_ALPHA")), {"RETURN_5D": "NEGATIVE"}, event_dependencies=["E_LIMITUP_SENT"], regime=["NON_CRASH", "SENTIMENT_EXHAUSTION"], horizon="1_3D", mode="FAILURE_AWARE"),
            _template("MOMENTUM", "Fundamental quality confirmation as a blocked research prior", ["RETURN_20D", "ROE"], _roles(("RETURN_20D", "PRIMARY_ALPHA"), ("ROE", "CONFIRMATION")), {"RETURN_20D": "POSITIVE", "ROE": "POSITIVE"}, horizon="5_10D", mode="FACTOR_DRIVEN"),
            _template("COMPOSITE", "Deferred external formula as mechanism inspiration only", ["RETURN_5D", "EXT_QLIB158_QLIB158_QTLD5_V1"], _roles(("RETURN_5D", "PRIMARY_ALPHA"), ("EXT_QLIB158_QLIB158_QTLD5_V1", "CONFIRMATION")), {"RETURN_5D": "POSITIVE", "EXT_QLIB158_QLIB158_QTLD5_V1": "CONTEXT_ONLY"}, horizon="1_3D", mode="EXTERNAL_PRIOR_ADAPTATION", inspiration=["qlib158_qtld5"]),
        ]


class HypothesisComposer:
    def __init__(self, context: ResearchContext):
        self.context = context
        self.factor_view = FactorKnowledgeView(context)
        self.pit_checker = PITReadinessChecker(context)
        self.failure_view = FailureKnowledgeView(context)

    def compose(self, template: Mapping[str, Any], existing: Sequence[AlphaHypothesisSpec] = ()) -> AlphaHypothesisSpec:
        hypothesis_type = str(template["hypothesis_type"]).upper()
        title = str(template["title"])
        factor_ids = tuple(str(item) for item in template["factor_ids"])
        event_dependencies = tuple(str(item) for item in template.get("event_dependencies", []))
        readiness, pit_evidence, resolutions = self.pit_checker.resolve(factor_ids, event_dependencies)
        factor_families = tuple(self.factor_view.resolve(item).family for item in factor_ids)
        family_counts = Counter(factor_families)
        related_factor_warning = tuple(
            f"{family}: multiple factors share one family; do not count as independent confirmation"
            for family, count in sorted(family_counts.items()) if count > 1
        )
        failure_awareness, failure_explanation = self.failure_view.awareness(hypothesis_type)
        if template.get("failure_awareness") is True:
            failure_awareness = True
            failure_explanation = str(template.get("failure_awareness_explanation", failure_explanation))
        source_provenance = dict(template.get("source_provenance", {"source_type": "INTERNAL_RESEARCH", "source_ids": []}))
        if template.get("inspiration_sources"):
            source_provenance = {
                "source_type": "VIBE_TRADING",
                "source_commit": VIBE_COMMIT,
                "source_ids": list(template.get("inspiration_sources", [])),
                "inspiration_only": True,
                "direct_strategy_import": False,
            }
        mode = str(template.get("generator_mode", "MECHANISM_DRIVEN"))
        base = {
            "hypothesis_id": "HYP_" + _slug(hypothesis_type + "_" + title) + "_" + hashlib.sha256(canonical_json({"title": title, "factors": factor_ids, "events": event_dependencies}).encode()).hexdigest()[:8].upper() + "_V1",
            "version": "v1",
            "title": title,
            "description": str(template.get("description", title)),
            "mechanism": str(template.get("mechanism", hypothesis_type.lower().replace("_", " "))),
            "economic_rationale": str(template.get("economic_rationale", "A temporary supply-demand imbalance may persist over the declared short horizon.")),
            "behavioral_rationale": str(template.get("behavioral_rationale", "A-share retail participation, attention cycles, and delayed information processing may create the proposed behavior.")),
            "market_microstructure_rationale": str(template.get("market_microstructure_rationale", "T+1, price limits, opening auction gaps, lot-size constraints, and liquidity variation can affect signal realization.")),
            "factor_ids": factor_ids,
            "factor_roles": tuple(dict(item) for item in template["factor_roles"]),
            "expected_factor_direction": dict(template["expected_factor_direction"]),
            "interaction_logic": str(template.get("interaction_logic", "Primary factor expresses the mechanism; confirmation/filter factors constrain the context without creating a strategy rule.")),
            "target_horizon": str(template.get("target_horizon", "3_5D")),
            "expected_holding_period": str(template.get("expected_holding_period", template.get("target_horizon", "3_5D"))),
            "signal_frequency": str(template.get("signal_frequency", "DAILY_CLOSE")),
            "market_regime": tuple(template.get("market_regime", ["NON_CRASH_OR_EXPLICIT_REGIME"])),
            "universe_assumptions": tuple(template.get("universe_assumptions", ["CHINA_A_SHARE", "PIT_TRADING_CALENDAR", "T_PLUS_1", "PRICE_LIMITS_AND_SUSPENSION_PIT", "100_SHARE_LOT", "SMALL_CAPITAL_10000_RMB"])),
            "entry_concept": str(template.get("entry_concept", "Evaluate the declared factor relationship after the decision timestamp; no executable order rule is generated.")),
            "exit_concept": str(template.get("exit_concept", "Evaluate the declared horizon or falsification event in a later validation phase; no exit code is generated.")),
            "risk_hypothesis": str(template.get("risk_hypothesis", "The relationship may be regime-dependent, crowded, capacity-limited, or altered by T+1 and price-limit execution constraints.")),
            "failure_conditions": tuple(template.get("failure_conditions", ["The relationship is absent across time splits or only appears in a narrow regime.", "The effect disappears after explicit PIT, capacity, cost, and concentration checks."])),
            "falsification_tests": tuple(template.get("falsification_tests", [{"test": "TIME_SPLIT", "rule": "Reject if the directional relationship is not reproducible across predeclared time splits."}, {"test": "REGIME_SPLIT", "rule": "Reject if the relationship is only present in an unannounced regime."}, {"test": "WINNER_CONCENTRATION", "rule": "Reject if removing a small number of extreme observations removes the mechanism."}])),
            "required_data": tuple(sorted(set(template.get("required_data", ["PIT daily OHLCV", "PIT A-share universe", *event_dependencies])))),
            "required_frequency": tuple(template.get("required_frequency", ["DAILY"])),
            "pit_requirements": tuple(sorted(set(template.get("pit_requirements", ["available_at <= decision_time", "PIT universe as-of decision date", "no future shift", *pit_evidence])))),
            "implementation_readiness": readiness,
            "novelty_class": "NOVEL",
            "source_provenance": source_provenance,
            "inspiration_sources": tuple(template.get("inspiration_sources", [])),
            "complexity_score": int(template.get("complexity_score", min(5, 1 + len(factor_ids)))),
            "created_at": CREATED_AT,
            "generator_version": GENERATOR_VERSION,
            "status": readiness,
            "hypothesis_type": hypothesis_type,
            "factor_families": factor_families,
            "event_dependencies": event_dependencies,
            "parameter_ranges": dict(template.get("parameter_ranges", {"lookback": "short=5-10 days; medium=20-60 days", "holding_period": "1-10 trading days"})),
            "validation_plan": tuple(template.get("validation_plan", ["FACTOR_SANITY", "FORWARD_RETURN_HORIZON", "TIME_SPLIT", "REGIME_SPLIT", "BOOTSTRAP", "WINNER_CONCENTRATION", "EXECUTION_TEST", "SMALL_CAPITAL_TEST"])),
            "research_risk": str(template.get("research_risk", "HIGH" if readiness != "READY_FOR_STRATEGY_BUILD" else "MEDIUM")),
            "small_capital_fit_prior": str(template.get("small_capital_fit_prior", "MEDIUM")),
            "estimated_validation_cost": str(template.get("estimated_validation_cost", "MEDIUM")),
            "failure_awareness": failure_awareness,
            "failure_awareness_explanation": failure_explanation,
            "complexity_justification": str(template.get("complexity_justification", "")),
            "hypothesis_fingerprint": "pending",
            "generator_mode": mode,
            "related_factor_warning": tuple(template.get("related_factor_warning", related_factor_warning)),
        }
        provisional = AlphaHypothesisSpec(**{**base, "hypothesis_fingerprint": "0" * 64})
        fingerprint = hypothesis_fingerprint(provisional)
        spec = AlphaHypothesisSpec(**{**base, "hypothesis_fingerprint": fingerprint})
        novelty, duplicate_of = NoveltyChecker().classify(spec, existing)
        if duplicate_of:
            spec = AlphaHypothesisSpec(**{**spec.to_dict(), "novelty_class": novelty, "status": "DUPLICATE", "implementation_readiness": readiness})
        return spec


class HypothesisRegistry:
    def __init__(self, path: str | Path = "data/research/hypothesis_registry/registry.json"):
        self.path = Path(path)
        self._items: dict[tuple[str, str], AlphaHypothesisSpec] = {}

    def append(self, spec: AlphaHypothesisSpec) -> bool:
        key = (spec.hypothesis_id, spec.version)
        existing = self._items.get(key)
        if existing is not None:
            if existing.to_dict() != spec.to_dict():
                raise HypothesisValidationError(f"append-only registry conflict: {key}")
            return False
        versions = [version for (hypothesis_id, version) in self._items if hypothesis_id == spec.hypothesis_id]
        if versions and spec.version in versions:
            raise HypothesisValidationError(f"duplicate hypothesis version: {key}")
        self._items[key] = spec
        return True

    def items(self) -> list[AlphaHypothesisSpec]:
        return [self._items[key] for key in sorted(self._items)]

    def get(self, hypothesis_id: str, version: str = "v1") -> AlphaHypothesisSpec | None:
        return self._items.get((hypothesis_id, version))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "hypotheses": [item.to_dict() for item in self.items()]}

    def write(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return self.path

    @classmethod
    def read(cls, path: str | Path) -> "HypothesisRegistry":
        registry = cls(path)
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        for item in payload.get("hypotheses", []):
            registry.append(AlphaHypothesisSpec.from_dict(item))
        return registry


class AIHypothesisGenerator:
    """Structured generation facade with deterministic status resolution."""

    MODES = {"MECHANISM_DRIVEN", "FACTOR_DRIVEN", "EXTERNAL_PRIOR_ADAPTATION", "FAILURE_AWARE"}

    def __init__(self, context: ResearchContext, version: str = GENERATOR_VERSION):
        self.context = context
        self.version = version
        self.planner = MechanismPlanner()
        self.composer = HypothesisComposer(context)
        self.guards = HypothesisDeterministicGuards(context)

    def generate_from_template(self, template: Mapping[str, Any], existing: Sequence[AlphaHypothesisSpec] = ()) -> AlphaHypothesisSpec:
        mode = str(template.get("generator_mode", "MECHANISM_DRIVEN")).upper()
        if mode not in self.MODES:
            raise HypothesisValidationError(f"unsupported generator mode: {mode}")
        spec = self.composer.compose(template, existing)
        self.guards.assert_valid(spec, existing)
        return spec

    def generate_acceptance_batch(self) -> list[AlphaHypothesisSpec]:
        result: list[AlphaHypothesisSpec] = []
        for template in self.planner.plan_acceptance():
            spec = self.generate_from_template(template, result)
            result.append(spec)
        return result

    def generate(self, mode: str, *, factor_id: str | None = None, external_id: str | None = None, failure_class: str | None = None) -> AlphaHypothesisSpec:
        mode = mode.upper()
        if mode not in self.MODES:
            raise HypothesisValidationError(f"unsupported generator mode: {mode}")
        templates = self.planner.plan_acceptance()
        if mode == "FACTOR_DRIVEN" and factor_id:
            candidates = [item for item in templates if factor_id in item["factor_ids"]]
            if not candidates:
                raise HypothesisValidationError(f"no mechanism template for factor: {factor_id}")
            return self.generate_from_template({**candidates[0], "generator_mode": mode})
        if mode == "EXTERNAL_PRIOR_ADAPTATION" and external_id:
            candidates = [item for item in templates if external_id in item.get("inspiration_sources", []) or external_id in item.get("factor_ids", [])]
            if not candidates:
                raise HypothesisValidationError(f"external prior is not mapped: {external_id}")
            return self.generate_from_template({**candidates[0], "generator_mode": mode})
        if mode == "FAILURE_AWARE" and failure_class:
            candidates = [item for item in templates if item["hypothesis_type"] in FailureKnowledgeView.FAILED_MECHANISM_TYPES]
            if not candidates:
                raise HypothesisValidationError(f"no failure-aware mechanism for: {failure_class}")
            return self.generate_from_template({**candidates[0], "generator_mode": mode, "failure_awareness": True, "failure_awareness_explanation": f"Failure class {failure_class} is an explicit design constraint; this proposal changes the conditioning and must pass falsification tests."})
        return self.generate_from_template({**templates[0], "generator_mode": mode})
