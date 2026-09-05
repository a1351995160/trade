"""Frozen batch design and the adapter boundary for existing ResearchPlanner."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .common import copy_mapping, now_timestamp, stable_hash
from .objective import ResearchObjectiveV1
from .run_state import deterministic_batch_id
from chanlun_trader.research.validation_policy_v2 import ValidationDecisionPolicyV2, default_validation_decision_policy_v2


@dataclass(frozen=True)
class ResearchBatchPlanV1:
    batch_id: str
    objective_id: str
    parent_batch_id: str | None = None
    generation_seed: int = 0
    max_hypotheses: int = 0
    max_candidates: int = 0
    max_performance_trials: int = 0
    family_quotas: Mapping[str, int] = field(default_factory=dict)
    mechanism_quotas: Mapping[str, int] = field(default_factory=dict)
    complexity_budget: Mapping[str, int] = field(default_factory=dict)
    baseline_plan: Mapping[str, Any] = field(default_factory=dict)
    failure_knowledge_snapshot_id: str | None = None
    performance_blind_design: bool = True
    validation_policy_id: str = "VALIDATION_DECISION_POLICY_V2"
    validation_policy_version: str = "2.0.0"
    validation_policy_hash: str = ""
    decision_family_id: str = ""
    family_contract_hash: str = ""
    created_at: str = field(default_factory=now_timestamp)
    run_id: str | None = None
    batch_number: int = 1
    backend_type: str = "TEMPLATE"
    backend_version: str = "TemplateResearchAgentBackendV1"
    proposal_schema_version: str = "research-proposal-v1"
    prompt_template_version: str = "NONE"
    input_context_hash: str = ""
    proposal_batch_hash: str = ""
    candidate_neighborhood_hash: str = ""
    family_diversity_policy_hash: str = ""
    similarity_policy_id: str = "CandidateNoveltyGateV2"
    agent_governance_policy_id: str = "AGENT_MODEL_TOKEN_COST_GOVERNANCE_V1"
    agent_governance_version: str = "1.0.0"
    agent_governance_hash: str = ""

    def __post_init__(self) -> None:
        if not self.batch_id or not self.objective_id:
            raise ValueError("batch_id and objective_id are required")
        if self.batch_number < 1:
            raise ValueError("batch_number must be >= 1")
        if self.batch_number == 1 and "_B" in self.batch_id:
            try:
                inferred_batch_number = int(self.batch_id.rsplit("_B", 1)[-1])
            except ValueError:
                inferred_batch_number = 1
            if inferred_batch_number >= 1:
                object.__setattr__(self, "batch_number", inferred_batch_number)
        if min(self.max_hypotheses, self.max_candidates, self.max_performance_trials) <= 0:
            raise ValueError("batch budgets must be positive")
        if not self.performance_blind_design:
            raise ValueError("batch design must be performance blind")
        for name in ("family_quotas", "mechanism_quotas", "complexity_budget", "baseline_plan"):
            object.__setattr__(self, name, copy_mapping(getattr(self, name)))
        if self.validation_policy_id == "VALIDATION_DECISION_POLICY_V2":
            policy = default_validation_decision_policy_v2()
            if not self.validation_policy_hash:
                object.__setattr__(self, "validation_policy_hash", policy.policy_hash)
            if not self.family_contract_hash:
                object.__setattr__(self, "family_contract_hash", policy.multiple_testing_contract_hash)
            if not self.decision_family_id:
                object.__setattr__(self, "decision_family_id", policy.family_id(self.objective_id, self.batch_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "research-batch-plan-v1",
            "batch_id": self.batch_id,
            "objective_id": self.objective_id,
            "parent_batch_id": self.parent_batch_id,
            "generation_seed": self.generation_seed,
            "max_hypotheses": self.max_hypotheses,
            "max_candidates": self.max_candidates,
            "max_performance_trials": self.max_performance_trials,
            "family_quotas": dict(self.family_quotas),
            "mechanism_quotas": dict(self.mechanism_quotas),
            "complexity_budget": dict(self.complexity_budget),
            "baseline_plan": dict(self.baseline_plan),
            "failure_knowledge_snapshot_id": self.failure_knowledge_snapshot_id,
            "performance_blind_design": self.performance_blind_design,
            "validation_policy_id": self.validation_policy_id,
            "validation_policy_version": self.validation_policy_version,
            "validation_policy_hash": self.validation_policy_hash,
            "decision_family_id": self.decision_family_id,
            "family_contract_hash": self.family_contract_hash,
            "created_at": self.created_at,
            "run_id": self.run_id,
            "batch_number": self.batch_number,
            "backend_type": self.backend_type,
            "backend_version": self.backend_version,
            "proposal_schema_version": self.proposal_schema_version,
            "prompt_template_version": self.prompt_template_version,
            "input_context_hash": self.input_context_hash,
            "proposal_batch_hash": self.proposal_batch_hash,
            "candidate_neighborhood_hash": self.candidate_neighborhood_hash,
            "family_diversity_policy_hash": self.family_diversity_policy_hash,
            "similarity_policy_id": self.similarity_policy_id,
            "agent_governance_policy_id": self.agent_governance_policy_id,
            "agent_governance_version": self.agent_governance_version,
            "agent_governance_hash": self.agent_governance_hash,
        }

    @property
    def plan_hash(self) -> str:
        return stable_hash(self.to_dict())


class FactoryResearchPlannerAdapterV1:
    """Translate the legacy ResearchPlanner budget ideas into a frozen plan."""

    def __init__(self, planner: Any | None = None, validation_policy: ValidationDecisionPolicyV2 | None = None):
        if planner is None:
            from chanlun_trader.research.hypothesis import ResearchPlanner
            planner = ResearchPlanner()
        self.planner = planner
        self.validation_policy = validation_policy or default_validation_decision_policy_v2()
        self.validation_policy.assert_active()

    def create_plan(
        self,
        objective: ResearchObjectiveV1,
        *,
        batch_number: int = 1,
        parent_batch_id: str | None = None,
        failure_knowledge_snapshot_id: str | None = None,
        run_id: str | None = None,
        backend_type: str = "TEMPLATE",
        backend_version: str = "TemplateResearchAgentBackendV1",
        prompt_template_version: str = "NONE",
        input_context_hash: str = "",
        proposal_batch_hash: str = "",
        candidate_neighborhood_hash: str = "",
        family_diversity_policy_hash: str = "",
        agent_governance_policy_id: str = "AGENT_MODEL_TOKEN_COST_GOVERNANCE_V1",
        agent_governance_version: str = "1.0.0",
        agent_governance_hash: str = "",
    ) -> ResearchBatchPlanV1:
        if batch_number < 1 or batch_number > objective.max_batches:
            raise ValueError("batch number is outside the objective budget")
        max_trials = min(getattr(self.planner, "max_total", objective.max_total_trials), objective.max_total_trials)
        max_trials = max(1, max_trials)
        max_candidates = min(max_trials, 20)
        return ResearchBatchPlanV1(
            batch_id=deterministic_batch_id(objective.objective_id, run_id, batch_number) if run_id else f"{objective.objective_id}_B{batch_number:02d}",
            objective_id=objective.objective_id,
            parent_batch_id=parent_batch_id,
            generation_seed=objective.seed + batch_number - 1,
            max_hypotheses=min(max_candidates, 20),
            max_candidates=max_candidates,
            max_performance_trials=max_trials,
            family_quotas={family: min(getattr(self.planner, "max_per_family", max_trials), max_trials) for family in objective.mechanism_scope},
            mechanism_quotas={family: 1 for family in objective.mechanism_scope},
            complexity_budget={
                "factor_count": 4,
                "condition_count": 8,
                "free_parameter_count": 0,
                "event_count": 2,
                "regime_count": 2,
                "interaction_depth": 1,
            },
            baseline_plan={
                "registration": "PRE_PERFORMANCE_REQUIRED",
                "controls": ["mechanism_specific", "simple_mechanism", "random_pit"],
            },
            failure_knowledge_snapshot_id=failure_knowledge_snapshot_id,
            validation_policy_id=self.validation_policy.policy_id,
            validation_policy_version=self.validation_policy.policy_version,
            validation_policy_hash=self.validation_policy.policy_hash,
            run_id=run_id,
            batch_number=batch_number,
            backend_type=backend_type,
            backend_version=backend_version,
            prompt_template_version=prompt_template_version,
            input_context_hash=input_context_hash,
            proposal_batch_hash=proposal_batch_hash,
            candidate_neighborhood_hash=candidate_neighborhood_hash,
            family_diversity_policy_hash=family_diversity_policy_hash,
            agent_governance_policy_id=agent_governance_policy_id,
            agent_governance_version=agent_governance_version,
            agent_governance_hash=agent_governance_hash,
        )
