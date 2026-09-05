"""ResearchObjectiveV1: the immutable research problem definition."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .common import copy_mapping, now_timestamp, stable_hash


RESEARCH_PRIORITY = (
    "PIT_NO_LOOKAHEAD",
    "ANTI_OVERFIT",
    "ROBUSTNESS",
    "RISK_ADJUSTED_AND_ABSOLUTE_RETURN",
    "WIN_RATE",
)

DEFAULT_MECHANISMS = (
    "event_reversal", "event_continuation", "breakout", "mean_reversion",
    "volatility", "relative_strength", "trend", "cross_sectional", "liquidity", "composite",
)


@dataclass(frozen=True)
class ResearchObjectiveV1:
    objective_id: str
    created_at: str = field(default_factory=now_timestamp)
    research_universe: tuple[str, ...] = ("SH", "SZ")
    capital_reference: float = 10000.0
    holding_horizon: tuple[int, int] = (2, 10)
    preferred_horizon: tuple[int, int] = (5, 8)
    mechanism_scope: tuple[str, ...] = DEFAULT_MECHANISMS
    allowed_factor_scope: tuple[str, ...] = ()
    risk_constraints: Mapping[str, Any] = field(default_factory=lambda: {
        "pit_required": True,
        "no_lookahead": True,
        "t_plus_1": True,
        "price_limit_fail_closed": True,
        "suspension_fail_closed": True,
        "final_test_access": "DISABLED",
        "prospective_access": "DISABLED",
        "recommendation": "DISABLED",
        "real_order_execution": "DISABLED",
    })
    research_priority: tuple[str, ...] = RESEARCH_PRIORITY
    max_batches: int = 5
    max_total_trials: int = 100
    seed: int = 20260824
    governance_policy_hash: str = ""
    stop_on_research_passed_count: int | None = None

    def __post_init__(self) -> None:
        if not self.objective_id.strip():
            raise ValueError("objective_id is required")
        if tuple(self.research_universe) != ("SH", "SZ"):
            raise ValueError("ResearchObjectiveV1 product default is the SH + SZ universe")
        if tuple(self.research_priority) != RESEARCH_PRIORITY:
            raise ValueError("research priority is a fixed governance policy")
        if self.capital_reference <= 0:
            raise ValueError("capital_reference must be positive")
        if self.holding_horizon != (2, 10):
            raise ValueError("holding_horizon must be 2-10 trading sessions")
        if self.preferred_horizon != (5, 8):
            raise ValueError("preferred_horizon must be 5-8 trading sessions")
        if self.max_batches < 1 or self.max_total_trials < 1:
            raise ValueError("research budgets must be positive")
        if self.stop_on_research_passed_count is not None and self.stop_on_research_passed_count < 1:
            raise ValueError("stop_on_research_passed_count must be positive")
        object.__setattr__(self, "risk_constraints", copy_mapping(self.risk_constraints))
        if not self.governance_policy_hash:
            policy = {
                "research_priority": self.research_priority,
                "risk_constraints": self.risk_constraints,
                "final_test_access": "DISABLED",
                "prospective_access": "DISABLED",
                "recommendation": "DISABLED",
                "real_order_execution": "DISABLED",
            }
            object.__setattr__(self, "governance_policy_hash", stable_hash(policy))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "research-objective-v1",
            "objective_id": self.objective_id,
            "created_at": self.created_at,
            "research_universe": list(self.research_universe),
            "capital_reference": self.capital_reference,
            "holding_horizon": list(self.holding_horizon),
            "preferred_horizon": list(self.preferred_horizon),
            "mechanism_scope": list(self.mechanism_scope),
            "allowed_factor_scope": list(self.allowed_factor_scope),
            "risk_constraints": dict(self.risk_constraints),
            "research_priority": list(self.research_priority),
            "max_batches": self.max_batches,
            "max_total_trials": self.max_total_trials,
            "seed": self.seed,
            "governance_policy_hash": self.governance_policy_hash,
            "stop_on_research_passed_count": self.stop_on_research_passed_count,
        }

    @property
    def objective_hash(self) -> str:
        return stable_hash(self.to_dict())

    @classmethod
    def default(cls, objective_id: str = "OBJ_AI_RESEARCH_FACTORY_V1", **overrides: Any) -> "ResearchObjectiveV1":
        return cls(objective_id=objective_id, **overrides)
