"""Factory status projection."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ResearchFactoryStatusV1:
    objective_id: str
    current_batch_id: str | None
    batch_state: str
    batch_number: int
    max_batches: int
    trial_budget_total: int
    trial_budget_used: int
    hypotheses_count: int = 0
    candidate_count: int = 0
    trials_started: int = 0
    trials_completed: int = 0
    research_passed_count: int = 0
    promising_count: int = 0
    weak_count: int = 0
    rejected_count: int = 0
    blocked_count: int = 0
    candidates_sample_feasibility_passed: int = 0
    candidates_sample_feasibility_blocked: int = 0
    candidates_sample_feasibility_unknown: int = 0
    failure_class_counts: Mapping[str, int] = field(default_factory=dict)
    strategy_registry_counts: Mapping[str, int] = field(default_factory=dict)
    stop_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "research-factory-status-v1",
            "objective_id": self.objective_id,
            "current_batch_id": self.current_batch_id,
            "batch_state": self.batch_state,
            "batch_number": self.batch_number,
            "max_batches": self.max_batches,
            "trial_budget_total": self.trial_budget_total,
            "trial_budget_used": self.trial_budget_used,
            "hypotheses_count": self.hypotheses_count,
            "candidate_count": self.candidate_count,
            "trials_started": self.trials_started,
            "trials_completed": self.trials_completed,
            "research_passed_count": self.research_passed_count,
            "promising_count": self.promising_count,
            "weak_count": self.weak_count,
            "rejected_count": self.rejected_count,
            "blocked_count": self.blocked_count,
            "candidates_sample_feasibility_passed": self.candidates_sample_feasibility_passed,
            "candidates_sample_feasibility_blocked": self.candidates_sample_feasibility_blocked,
            "candidates_sample_feasibility_unknown": self.candidates_sample_feasibility_unknown,
            "failure_class_counts": dict(self.failure_class_counts),
            "strategy_registry_counts": dict(self.strategy_registry_counts),
            "stop_reason": self.stop_reason,
        }


@dataclass(frozen=True)
class AutonomousResearchRunStatusV2:
    run_id: str
    objective_id: str
    policy_hash: str
    run_state: str
    current_batch_id: str | None
    batches_completed: int
    max_batches: int
    hypotheses_generated_cumulative: int = 0
    candidates_frozen_cumulative: int = 0
    predictive_trials_used: int = 0
    max_predictive_trials: int = 0
    trials_pending_adjudication: int = 0
    research_passed_count: int = 0
    promising_count: int = 0
    weak_count: int = 0
    rejected_count: int = 0
    blocked_count: int = 0
    candidates_sample_feasibility_passed: int = 0
    candidates_sample_feasibility_blocked: int = 0
    candidates_sample_feasibility_unknown: int = 0
    failure_category_counts: Mapping[str, int] = field(default_factory=dict)
    engineering_block: str | None = None
    governance_block: str | None = None
    remaining_trial_budget: int = 0
    stop_reason: str | None = None
    last_checkpoint_time: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "autonomous-research-run-status-v2",
            "run_id": self.run_id,
            "objective_id": self.objective_id,
            "policy_hash": self.policy_hash,
            "run_state": self.run_state,
            "current_batch_id": self.current_batch_id,
            "batches_completed": self.batches_completed,
            "max_batches": self.max_batches,
            "hypotheses_generated_cumulative": self.hypotheses_generated_cumulative,
            "candidates_frozen_cumulative": self.candidates_frozen_cumulative,
            "predictive_trials_used": self.predictive_trials_used,
            "max_predictive_trials": self.max_predictive_trials,
            "trials_pending_adjudication": self.trials_pending_adjudication,
            "RESEARCH_PASSED": self.research_passed_count,
            "PROMISING": self.promising_count,
            "WEAK": self.weak_count,
            "REJECTED": self.rejected_count,
            "BLOCKED": self.blocked_count,
            "candidates_sample_feasibility_passed": self.candidates_sample_feasibility_passed,
            "candidates_sample_feasibility_blocked": self.candidates_sample_feasibility_blocked,
            "candidates_sample_feasibility_unknown": self.candidates_sample_feasibility_unknown,
            "failure_category_counts": dict(self.failure_category_counts),
            "engineering_block": self.engineering_block,
            "governance_block": self.governance_block,
            "remaining_trial_budget": self.remaining_trial_budget,
            "stop_reason": self.stop_reason,
            "last_checkpoint_time": self.last_checkpoint_time,
        }
