"""Performance-blind design context for hypothesis and candidate construction."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping

from .common import copy_mapping, jsonable, stable_hash


class PerformanceLeakError(RuntimeError):
    pass


class OutcomeBlindFieldPolicyV1:
    """Canonical recursive policy for design-time outcome-blind payloads."""

    policy_id = "OutcomeBlindFieldPolicyV1"
    FORBIDDEN_FIELDS = frozenset({
        "return", "returns", "net_return", "exact_return", "pf", "profit_factor", "dd", "drawdown",
        "win_rate", "p_value", "pvalue", "bootstrap_p_value", "adjusted_p", "sharpe", "pnl", "realized_pnl", "trade_pnl", "performance",
        "forward_return", "strategy_return", "portfolio_return", "historical_performance_rank", "performance_metrics",
        "backtest_result", "backtest_results", "forward_result", "future_return", "future_outcome",
        "prospective_result", "prospective_outcome", "exact_previous_candidate_result", "optimization",
        "classification", "final_classification", "effective_classification", "research_classification",
        "recommendation", "order", "trades", "equity", "fills", "bootstrap_report", "multiple_testing", "final_decision", "performance_accessed", "final_test", "final_test_result", "final_test_outcome", "prospective", "outcome",
    })

    @staticmethod
    def normalize_field(key: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")

    @classmethod
    def is_forbidden(cls, key: Any) -> bool:
        return cls.normalize_field(key) in cls.FORBIDDEN_FIELDS

    @classmethod
    def assert_blind(cls, value: Any, *, allowed_paths: frozenset[tuple[str, ...]] = frozenset()) -> None:
        def is_allowed(field_path: tuple[str, ...]) -> bool:
            if field_path in allowed_paths:
                return True
            without_indexes = tuple(part for part in field_path if not part.isdecimal())
            return without_indexes in allowed_paths

        def visit(item: Any, path: tuple[str, ...] = ()) -> None:
            if isinstance(item, Mapping):
                for key, nested in item.items():
                    normalized = cls.normalize_field(key)
                    field_path = path + (normalized,)
                    if normalized in cls.FORBIDDEN_FIELDS and not is_allowed(field_path):
                        location = ".".join(field_path)
                        raise PerformanceLeakError(f"performance-bearing field is forbidden at {location}: {key}")
                    visit(nested, field_path)
            elif isinstance(item, (list, tuple)):
                for index, nested in enumerate(item):
                    visit(nested, path + (str(index),))
            elif isinstance(item, (set, frozenset)):
                for nested in item:
                    visit(nested, path)
        visit(value)


class PerformanceBlindGuard:
    FORBIDDEN_KEYS = OutcomeBlindFieldPolicyV1.FORBIDDEN_FIELDS

    @classmethod
    def assert_blind(
        cls,
        value: Any,
        *,
        allowed_paths: frozenset[tuple[str, ...]] = frozenset(),
    ) -> None:
        OutcomeBlindFieldPolicyV1.assert_blind(value, allowed_paths=allowed_paths)


@dataclass(frozen=True)
class NoOutcomeResearchContextV1:
    factor_capability_summary: tuple[Mapping[str, Any], ...] = ()
    mechanism_history: tuple[Mapping[str, Any], ...] = ()
    failure_class_summaries: tuple[Mapping[str, Any], ...] = ()
    constraints: Mapping[str, Any] = field(default_factory=dict)
    context_id: str = ""

    def __post_init__(self) -> None:
        constraints = copy_mapping(self.constraints)
        for name in ("factor_capability_summary", "mechanism_history", "failure_class_summaries"):
            values = tuple(copy_mapping(item) for item in getattr(self, name))
            object.__setattr__(self, name, values)
        object.__setattr__(self, "constraints", constraints)
        payload = {
            "factor_capability_summary": self.factor_capability_summary,
            "mechanism_history": self.mechanism_history,
            "failure_class_summaries": self.failure_class_summaries,
            "constraints": self.constraints,
        }
        PerformanceBlindGuard.assert_blind(payload)
        if not self.context_id:
            object.__setattr__(self, "context_id", stable_hash(payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "no-outcome-research-context-v1",
            "context_id": self.context_id,
            "factor_capability_summary": jsonable(self.factor_capability_summary),
            "mechanism_history": jsonable(self.mechanism_history),
            "failure_class_summaries": jsonable(self.failure_class_summaries),
            "constraints": jsonable(self.constraints),
            "performance_data_loaded": False,
            "outcome_fields_available": False,
        }

    def get(self, key: str) -> Any:
        if OutcomeBlindFieldPolicyV1.is_forbidden(key):
            raise PerformanceLeakError(f"performance-bearing field is forbidden: {key}")
        allowed = {
            "factor_capability_summary": self.factor_capability_summary,
            "mechanism_history": self.mechanism_history,
            "failure_class_summaries": self.failure_class_summaries,
            "constraints": self.constraints,
        }
        if key not in allowed:
            raise KeyError(key)
        return allowed[key]

    def assert_payload_blind(self, payload: Any) -> None:
        PerformanceBlindGuard.assert_blind(payload)


@dataclass(frozen=True)
class NoOutcomePolicyDesignContextV1:
    """Policy-design inputs that cannot expose candidate performance."""

    product_constraints: Mapping[str, Any] = field(default_factory=dict)
    frozen_contract_refs: tuple[str, ...] = ()
    statistical_method_contract: Mapping[str, Any] = field(default_factory=dict)
    engine_capabilities: Mapping[str, Any] = field(default_factory=dict)
    failure_taxonomy: tuple[str, ...] = ()
    context_id: str = ""

    def __post_init__(self) -> None:
        product_constraints = copy_mapping(self.product_constraints)
        statistical_method_contract = copy_mapping(self.statistical_method_contract)
        engine_capabilities = copy_mapping(self.engine_capabilities)
        payload = {
            "product_constraints": product_constraints,
            "frozen_contract_refs": tuple(self.frozen_contract_refs),
            "statistical_method_contract": statistical_method_contract,
            "engine_capabilities": engine_capabilities,
            "failure_taxonomy": tuple(self.failure_taxonomy),
        }
        PerformanceBlindGuard.assert_blind(payload)
        object.__setattr__(self, "product_constraints", product_constraints)
        object.__setattr__(self, "statistical_method_contract", statistical_method_contract)
        object.__setattr__(self, "engine_capabilities", engine_capabilities)
        object.__setattr__(self, "frozen_contract_refs", tuple(self.frozen_contract_refs))
        object.__setattr__(self, "failure_taxonomy", tuple(self.failure_taxonomy))
        if not self.context_id:
            object.__setattr__(self, "context_id", stable_hash(payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "no-outcome-policy-design-context-v1",
            "context_id": self.context_id,
            "product_constraints": jsonable(self.product_constraints),
            "frozen_contract_refs": list(self.frozen_contract_refs),
            "statistical_method_contract": jsonable(self.statistical_method_contract),
            "engine_capabilities": jsonable(self.engine_capabilities),
            "failure_taxonomy": list(self.failure_taxonomy),
            "performance_data_loaded": False,
            "outcome_fields_available": False,
        }
