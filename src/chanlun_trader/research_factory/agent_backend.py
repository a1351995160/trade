"""Outcome-blind ResearchAgentBackendV1 contracts and deterministic adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
import json
import os
import time

from .common import copy_mapping, jsonable, now_timestamp, stable_hash
from .context import NoOutcomeResearchContextV1, OutcomeBlindFieldPolicyV1, PerformanceBlindGuard, PerformanceLeakError


AGENT_FORBIDDEN_FIELDS = OutcomeBlindFieldPolicyV1.FORBIDDEN_FIELDS


class AgentBackendError(RuntimeError):
    code = "AGENT_BACKEND_ERROR"


@dataclass(frozen=True)
class AgentCallEstimateV1:
    """Outcome-blind, conservative resource bound produced before invocation."""

    estimated_input_tokens: int = 0
    reserved_output_tokens: int = 0
    reserved_total_tokens: int = 0
    estimated_cost_upper_bound: float = 0.0
    currency: str = "NONE"
    model_route: str = "NONE"
    estimate_status: str = "UNBOUNDED"
    estimate_method: str = "NONE"
    estimate_method_version: str = "1.0.0"
    token_estimator_id: str = "NONE"
    token_estimator_version: str = "NONE"
    pricing_source: str = "NONE"
    pricing_version: str = "NONE"
    max_output_tokens: int = 0
    estimate_hash: str = ""

    def __post_init__(self) -> None:
        if min(self.estimated_input_tokens, self.reserved_output_tokens, self.reserved_total_tokens, self.max_output_tokens) < 0 or self.estimated_cost_upper_bound < 0:
            raise ValueError("agent call estimate cannot be negative")
        total = int(self.estimated_input_tokens) + int(self.reserved_output_tokens)
        if int(self.reserved_total_tokens) == 0 and total:
            object.__setattr__(self, "reserved_total_tokens", total)
        if not self.estimate_hash:
            object.__setattr__(self, "estimate_hash", stable_hash({key: value for key, value in self.to_dict().items() if key != "estimate_hash"}))

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def assert_agent_design_time(value: Any) -> None:
    OutcomeBlindFieldPolicyV1.assert_blind(
        value,
        allowed_paths=frozenset({("objective", "risk_constraints", "recommendation")}),
    )


@dataclass(frozen=True)
class ResearchAgentInputV1:
    run_id: str
    objective: Mapping[str, Any]
    policy_identity: Mapping[str, Any]
    no_outcome_context: NoOutcomeResearchContextV1 | Mapping[str, Any]
    batch_id: str = ""
    failure_knowledge_view: Mapping[str, Any] | Sequence[Mapping[str, Any]] = field(default_factory=dict)
    factor_event_catalog: Mapping[str, Any] | Sequence[Mapping[str, Any]] = field(default_factory=dict)
    candidate_novelty_neighborhood: Mapping[str, Any] | Sequence[Mapping[str, Any]] = field(default_factory=dict)
    family_mechanism_constraints: Mapping[str, Any] = field(default_factory=dict)
    complexity_constraints: Mapping[str, Any] = field(default_factory=dict)
    proposal_budget_view: Mapping[str, Any] = field(default_factory=dict)
    input_context_hash: str = ""

    def __post_init__(self) -> None:
        context = self.no_outcome_context.to_dict() if isinstance(self.no_outcome_context, NoOutcomeResearchContextV1) else copy_mapping(self.no_outcome_context)
        payload = {
            "run_id": self.run_id,
            "objective": copy_mapping(self.objective),
            "batch_id": self.batch_id,
            "policy_identity": copy_mapping(self.policy_identity),
            "no_outcome_context": context,
            "failure_knowledge_view": jsonable(self.failure_knowledge_view),
            "factor_event_catalog": jsonable(self.factor_event_catalog),
            "candidate_novelty_neighborhood": jsonable(self.candidate_novelty_neighborhood),
            "family_mechanism_constraints": copy_mapping(self.family_mechanism_constraints),
            "complexity_constraints": copy_mapping(self.complexity_constraints),
            "proposal_budget_view": copy_mapping(self.proposal_budget_view),
        }
        PerformanceBlindGuard.assert_blind(payload)
        assert_agent_design_time(payload)
        object.__setattr__(self, "objective", copy_mapping(self.objective))
        object.__setattr__(self, "policy_identity", copy_mapping(self.policy_identity))
        object.__setattr__(self, "no_outcome_context", context)
        object.__setattr__(self, "family_mechanism_constraints", copy_mapping(self.family_mechanism_constraints))
        object.__setattr__(self, "complexity_constraints", copy_mapping(self.complexity_constraints))
        object.__setattr__(self, "proposal_budget_view", copy_mapping(self.proposal_budget_view))
        if not self.input_context_hash:
            object.__setattr__(self, "input_context_hash", stable_hash(payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "research-agent-input-v1",
            "run_id": self.run_id,
            "objective": jsonable(self.objective),
            "batch_id": self.batch_id,
            "policy_identity": jsonable(self.policy_identity),
            "no_outcome_context": jsonable(self.no_outcome_context),
            "failure_knowledge_view": jsonable(self.failure_knowledge_view),
            "factor_event_catalog": jsonable(self.factor_event_catalog),
            "candidate_novelty_neighborhood": jsonable(self.candidate_novelty_neighborhood),
            "family_mechanism_constraints": jsonable(self.family_mechanism_constraints),
            "complexity_constraints": jsonable(self.complexity_constraints),
            "proposal_budget_view": jsonable(self.proposal_budget_view),
            "input_context_hash": self.input_context_hash,
            "performance_data_loaded": False,
            "outcome_fields_available": False,
        }


@dataclass(frozen=True)
class ResearchProposalV1:
    proposal_id: str
    hypothesis_id: str
    family_id: str
    mechanism: str
    economic_rationale: str
    factor_dependencies: tuple[str, ...] = ()
    event_dependencies: tuple[str, ...] = ()
    expected_holding_horizon: str = "5_8D"
    candidate_complexity: Mapping[str, Any] = field(default_factory=dict)
    required_data: tuple[str, ...] = ()
    novelty_claim: str = ""
    provenance: Mapping[str, Any] = field(default_factory=dict)
    observable_conditions: tuple[str, ...] = ()
    falsification_conditions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.proposal_id, self.hypothesis_id, self.family_id, self.mechanism)):
            raise ValueError("proposal identity fields are required")
        payload = self.to_dict()
        assert_agent_design_time(payload)
        object.__setattr__(self, "factor_dependencies", tuple(str(item) for item in self.factor_dependencies))
        object.__setattr__(self, "event_dependencies", tuple(str(item) for item in self.event_dependencies))
        object.__setattr__(self, "required_data", tuple(str(item) for item in self.required_data))
        object.__setattr__(self, "candidate_complexity", copy_mapping(self.candidate_complexity))
        object.__setattr__(self, "provenance", copy_mapping(self.provenance))
        object.__setattr__(self, "observable_conditions", tuple(str(item) for item in self.observable_conditions))
        object.__setattr__(self, "falsification_conditions", tuple(str(item) for item in self.falsification_conditions))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "research-proposal-v1",
            "proposal_id": self.proposal_id,
            "hypothesis_id": self.hypothesis_id,
            "family_id": self.family_id,
            "mechanism": self.mechanism,
            "economic_rationale": self.economic_rationale,
            "factor_dependencies": list(self.factor_dependencies),
            "event_dependencies": list(self.event_dependencies),
            "expected_holding_horizon": self.expected_holding_horizon,
            "candidate_complexity": jsonable(self.candidate_complexity),
            "required_data": list(self.required_data),
            "novelty_claim": self.novelty_claim,
            "provenance": jsonable(self.provenance),
            "observable_conditions": list(self.observable_conditions),
            "falsification_conditions": list(self.falsification_conditions),
        }


@dataclass(frozen=True)
class ResearchProposalBatchV1:
    run_id: str
    batch_id: str
    proposals: tuple[ResearchProposalV1, ...]
    backend_type: str
    backend_version: str
    proposal_schema_version: str = "research-proposal-v1"
    prompt_template_version: str = "NONE"
    model_id: str = "NONE"
    input_context_hash: str = ""
    generated_at: str = field(default_factory=now_timestamp)
    proposal_batch_hash: str = ""
    provenance: Mapping[str, Any] = field(default_factory=dict)
    empty_reason: str = ""

    def __post_init__(self) -> None:
        proposals = tuple(self.proposals)
        if not proposals and self.empty_reason != "NO_LEGAL_RESEARCH_PROPOSAL":
            raise AgentBackendError("empty proposal output requires NO_LEGAL_RESEARCH_PROPOSAL")
        if len({item.proposal_id for item in proposals}) != len(proposals):
            raise AgentBackendError("duplicate proposal_id")
        assert_agent_design_time({"proposals": [item.to_dict() for item in proposals], "provenance": self.provenance})
        object.__setattr__(self, "proposals", proposals)
        object.__setattr__(self, "provenance", copy_mapping(self.provenance))
        if not self.proposal_batch_hash:
            object.__setattr__(self, "proposal_batch_hash", stable_hash([item.to_dict() for item in proposals]))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "research-proposal-batch-v1",
            "run_id": self.run_id,
            "batch_id": self.batch_id,
            "proposals": [item.to_dict() for item in self.proposals],
            "backend_type": self.backend_type,
            "backend_version": self.backend_version,
            "proposal_schema_version": self.proposal_schema_version,
            "prompt_template_version": self.prompt_template_version,
            "model_id": self.model_id,
            "input_context_hash": self.input_context_hash,
            "generated_at": self.generated_at,
            "proposal_batch_hash": self.proposal_batch_hash,
            "provenance": jsonable(self.provenance),
            "empty_reason": self.empty_reason,
            "performance_data_loaded": False,
            "outcome_fields_available": False,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResearchProposalBatchV1":
        proposals = tuple(ResearchProposalV1(**{key: value for key, value in item.items() if key in ResearchProposalV1.__dataclass_fields__}) for item in payload.get("proposals", ()))
        return cls(
            run_id=str(payload.get("run_id", "")),
            batch_id=str(payload.get("batch_id", "")),
            proposals=proposals,
            backend_type=str(payload.get("backend_type", "TEMPLATE")),
            backend_version=str(payload.get("backend_version", "")),
            proposal_schema_version=str(payload.get("proposal_schema_version", "research-proposal-v1")),
            prompt_template_version=str(payload.get("prompt_template_version", "NONE")),
            model_id=str(payload.get("model_id", "NONE")),
            input_context_hash=str(payload.get("input_context_hash", "")),
            generated_at=str(payload.get("generated_at", "")),
            proposal_batch_hash=str(payload.get("proposal_batch_hash", "")),
            provenance=dict(payload.get("provenance", {})),
            empty_reason=str(payload.get("empty_reason", "")),
        )


class ResearchAgentBackendV1(ABC):
    backend_type = "ABSTRACT"
    backend_version = "1.0.0"

    def invoke(self, input: ResearchAgentInputV1) -> ResearchProposalBatchV1:
        try:
            assert_agent_design_time(input.to_dict())
            result = self.generate_proposals(input)
            if not isinstance(result, ResearchProposalBatchV1):
                raise AgentBackendError("invalid proposal batch type")
            return result
        except AgentBackendError:
            raise
        except Exception as exc:
            raise AgentBackendError(f"AGENT_BACKEND_ERROR: {exc}") from exc

    def estimate_call(self, input: ResearchAgentInputV1) -> AgentCallEstimateV1:
        """Return a conservative bound without invoking a model or backend call."""
        return AgentCallEstimateV1(model_route=self.backend_type, estimate_status="UNBOUNDED", estimate_method="BACKEND_REQUIRED")

    @abstractmethod
    def generate_proposals(self, input: ResearchAgentInputV1) -> ResearchProposalBatchV1:
        raise NotImplementedError


class TemplateResearchAgentBackendV1(ResearchAgentBackendV1):
    backend_type = "TEMPLATE"
    backend_version = "TemplateResearchAgentBackendV1"

    def __init__(self, templates: Sequence[Mapping[str, Any]] | None = None, *, prompt_template_version: str = "TEMPLATE_RULES_V1"):
        self.templates = tuple(dict(item) for item in (templates or ()))
        self.prompt_template_version = prompt_template_version
        self.calls = 0

    def estimate_call(self, input: ResearchAgentInputV1) -> AgentCallEstimateV1:
        return AgentCallEstimateV1(
            model_route="TEMPLATE",
            estimate_status="NOT_APPLICABLE",
            estimate_method="TEMPLATE_NO_MODEL",
            estimate_method_version="1.0.0",
            token_estimator_id="NOT_APPLICABLE",
            token_estimator_version="NOT_APPLICABLE",
            pricing_source="NOT_APPLICABLE",
            pricing_version="NOT_APPLICABLE",
        )

    def generate_proposals(self, input: ResearchAgentInputV1) -> ResearchProposalBatchV1:
        self.calls += 1
        objective = input.objective
        mechanisms = tuple(str(item) for item in objective.get("mechanism_scope", ())) or ("composite",)
        max_count = int(input.proposal_budget_view.get("max_proposals", input.proposal_budget_view.get("max_hypotheses", len(self.templates) or len(mechanisms)) or 1))
        source = self.templates or tuple({"mechanism": mechanism, "family_id": mechanism} for mechanism in mechanisms)
        proposals: list[ResearchProposalV1] = []
        for index, template in enumerate(source[:max_count], start=1):
            mechanism = str(template.get("mechanism") or mechanisms[(index - 1) % len(mechanisms)])
            family_id = str(template.get("family_id") or mechanism)
            factor_ids = tuple(str(item) for item in template.get("factor_ids", ()))
            if not factor_ids:
                catalog = input.factor_event_catalog
                rows = catalog.values() if isinstance(catalog, Mapping) else catalog
                factor_ids = tuple(str(item.get("factor_id")) for item in rows if isinstance(item, Mapping) and item.get("factor_id"))[:2]
            event_ids = tuple(str(item) for item in template.get("event_dependencies", ()))
            proposals.append(ResearchProposalV1(
                proposal_id=f"{input.run_id}_{input.objective.get('objective_id', 'OBJ')}_P{index:03d}",
                hypothesis_id=f"{input.batch_id or input.run_id + '_B01'}_H{index:03d}",
                family_id=family_id,
                mechanism=mechanism,
                economic_rationale=str(template.get("economic_rationale") or f"Design-time mechanism proposal: {mechanism}"),
                factor_dependencies=factor_ids,
                event_dependencies=event_ids,
                expected_holding_horizon=str(template.get("expected_holding_horizon") or template.get("target_horizon") or "5_8D"),
                candidate_complexity=template.get("candidate_complexity") or {"factor_count": len(factor_ids), "event_count": len(event_ids), "interaction_depth": 1},
                required_data=tuple(str(item) for item in template.get("required_data", factor_ids)),
                novelty_claim=str(template.get("novelty_claim") or "structural mechanism differs from neighborhood"),
                provenance={"backend_type": self.backend_type, "backend_version": self.backend_version, "prompt_template_version": self.prompt_template_version, "input_context_hash": input.input_context_hash, "generation_timestamp": now_timestamp(), "model_id": "NONE"},
            ))
        return ResearchProposalBatchV1(input.run_id, input.batch_id or input.run_id + "_B01", tuple(proposals), self.backend_type, self.backend_version, prompt_template_version=self.prompt_template_version, input_context_hash=input.input_context_hash, provenance={"backend_type": self.backend_type, "backend_version": self.backend_version, "model_id": "NONE", "input_context_hash": input.input_context_hash})


class SyntheticResearchAgentBackendV1(TemplateResearchAgentBackendV1):
    backend_type = "SYNTHETIC"
    backend_version = "SyntheticResearchAgentBackendV1"

    def __init__(self, templates: Sequence[Mapping[str, Any]] | None = None, *, prompt_template_version: str = "TEMPLATE_RULES_V1", pre_call_estimate: AgentCallEstimateV1 | Mapping[str, Any] | None = None, estimated_input_tokens: int = 0, reserved_output_tokens: int = 0, max_output_tokens: int = 0, estimated_cost_upper_bound: float = 0.0, estimated_cost: float | None = None, currency: str = "NONE", estimate_status: str | None = None, model_route: str | None = None):
        super().__init__(templates, prompt_template_version=prompt_template_version)
        if pre_call_estimate is not None:
            self._pre_call_estimate = pre_call_estimate if isinstance(pre_call_estimate, AgentCallEstimateV1) else AgentCallEstimateV1(**dict(pre_call_estimate))
        else:
            output_cap = int(max_output_tokens or reserved_output_tokens)
            cost = float(estimated_cost_upper_bound if estimated_cost is None else estimated_cost)
            status = estimate_status or ("READY" if estimated_input_tokens or output_cap or cost else "NOT_APPLICABLE")
            self._pre_call_estimate = AgentCallEstimateV1(
                estimated_input_tokens=int(estimated_input_tokens),
                reserved_output_tokens=output_cap,
                reserved_total_tokens=int(estimated_input_tokens) + output_cap,
                estimated_cost_upper_bound=cost,
                currency=currency,
                model_route=model_route or self.backend_type,
                estimate_status=status,
                estimate_method="SYNTHETIC_CONFIGURED",
                estimate_method_version="1.0.0",
                token_estimator_id="SYNTHETIC_CONFIGURED",
                token_estimator_version="1.0.0",
                pricing_source="SYNTHETIC_CONFIGURED",
                pricing_version="1.0.0",
                max_output_tokens=output_cap,
            )

    def estimate_call(self, input: ResearchAgentInputV1) -> AgentCallEstimateV1:
        return self._pre_call_estimate

    @property
    def backend_call_count(self) -> int:
        return self.calls


@dataclass(frozen=True)
class AgentCostGovernanceV1:
    status: str = "READY"
    max_agent_calls: int = 3
    max_agent_tokens: int = 0
    timeout_seconds: int = 30
    retry_limit: int = 1
    concurrency: int = 1
    model_route: str = "TEMPLATE"
    agent_cost_budget: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "agent-cost-governance-v1", **self.__dict__}

    @property
    def policy_hash(self) -> str:
        return stable_hash(self.to_dict())


@dataclass(frozen=True)
class AgentGovernancePolicyV1:
    """Active generic governance contract; it never selects or calls Codex."""

    policy_id: str = "AGENT_MODEL_TOKEN_COST_GOVERNANCE_V1"
    version: str = "1.0.0"
    max_agent_calls: int = 3
    max_agent_tokens: int = 0
    timeout_seconds: int = 30
    retry_limit: int = 1
    concurrency: int = 1
    model_route: str = "TEMPLATE"
    agent_cost_budget: float = 0.0
    max_output_tokens: int = 0
    usage_unavailable_behavior: str = "ALLOW_WITH_UNAVAILABLE_STATUS"
    status: str = "READY"

    def __post_init__(self) -> None:
        if min(self.max_agent_calls, self.timeout_seconds, self.concurrency) < 1 or self.max_agent_tokens < 0 or self.max_output_tokens < 0 or self.retry_limit < 0 or self.agent_cost_budget < 0:
            raise ValueError("invalid agent governance policy")
        if not self.model_route or self.usage_unavailable_behavior not in {"ALLOW_WITH_UNAVAILABLE_STATUS", "FAIL_CLOSED"}:
            raise ValueError("invalid agent governance route or usage behavior")
        if self.status != "READY":
            raise ValueError("agent governance policy must be READY before backend invocation")

    @property
    def policy_hash(self) -> str:
        return stable_hash({"schema_version": "agent-governance-policy-v1", **self.__dict__})

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "agent-governance-policy-v1", **self.__dict__, "policy_hash": self.policy_hash}


@dataclass(frozen=True)
class AgentUsageMetadataV1:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    usage_status: str = "NOT_APPLICABLE"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class AgentCostMetadataV1:
    estimated_cost: float = 0.0
    actual_cost: float = 0.0
    currency: str = "NONE"
    pricing_source: str = "NOT_APPLICABLE"
    pricing_version: str = "NONE"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class AgentCallBudgetV1:
    """Durable logical-call, token and cost budget."""

    def __init__(self, path: str | Path, policy: AgentGovernancePolicyV1):
        self.path = Path(path)
        self.policy = policy
        self.calls_reserved = 0
        self.calls_started = 0
        self.calls_completed = 0
        self.calls_failed = 0
        self.tokens_reserved = 0
        self.tokens_used = 0
        self.cost_reserved = 0.0
        self.cost_used = 0.0
        self.active_calls = 0
        self._calls: dict[str, dict[str, Any]] = {}
        self._load()
        self._persist()

    @property
    def calls_remaining(self) -> int:
        return max(0, self.policy.max_agent_calls - self.calls_reserved)

    @property
    def tokens_remaining(self) -> int:
        if self.policy.max_agent_tokens == 0:
            return 0
        return max(0, self.policy.max_agent_tokens - self.tokens_used - self.tokens_reserved)

    @property
    def cost_remaining(self) -> float:
        return max(0.0, float(self.policy.agent_cost_budget) - self.cost_used - self.cost_reserved)

    def preflight(self, *, estimated_tokens: int = 0, estimated_cost: float = 0.0) -> None:
        if self.calls_remaining <= 0:
            raise AgentBackendError("AGENT_CALL_BUDGET_EXHAUSTED")
        if self.active_calls >= self.policy.concurrency:
            raise AgentBackendError("AGENT_CONCURRENCY_EXHAUSTED")
        if self.policy.max_agent_tokens and self.tokens_remaining < estimated_tokens:
            raise AgentBackendError("AGENT_TOKEN_BUDGET_EXHAUSTED")
        if self.cost_remaining < float(estimated_cost):
            raise AgentBackendError("AGENT_COST_BUDGET_EXHAUSTED")

    def reserve(self, agent_call_id: str, *, estimated_tokens: int = 0, estimated_cost: float = 0.0, estimate: AgentCallEstimateV1 | None = None) -> dict[str, Any]:
        existing = self._calls.get(str(agent_call_id))
        if existing is not None:
            return dict(existing)
        self.preflight(estimated_tokens=estimated_tokens, estimated_cost=estimated_cost)
        item = {"agent_call_id": str(agent_call_id), "retry_count": 0, "status": "RESERVED", "estimated_tokens": int(estimated_tokens), "estimated_cost": float(estimated_cost)}
        if estimate is not None:
            item["estimate"] = estimate.to_dict()
        self._calls[str(agent_call_id)] = item
        self.calls_reserved += 1
        self.tokens_reserved += int(estimated_tokens)
        self.cost_reserved += float(estimated_cost)
        self._persist()
        return dict(item)

    def start(self, agent_call_id: str) -> None:
        item = self._calls[str(agent_call_id)]
        if item.get("status") == "STARTED":
            return
        item["status"] = "STARTED"
        self.calls_started += 1
        self.active_calls += 1
        self._persist()

    def complete(self, agent_call_id: str, usage: AgentUsageMetadataV1, cost: AgentCostMetadataV1) -> None:
        item = self._calls[str(agent_call_id)]
        if item.get("status") == "COMPLETED":
            return
        actual_tokens = int(usage.total_tokens)
        actual_cost = float(cost.actual_cost)
        reserved_tokens = int(item.get("estimated_tokens", 0))
        reserved_cost = float(item.get("estimated_cost", 0.0))
        if actual_tokens > reserved_tokens or actual_cost > reserved_cost:
            raise AgentBackendError("AGENT_USAGE_BOUND_VIOLATION")
        if self.policy.max_agent_tokens and self.tokens_used + actual_tokens > self.policy.max_agent_tokens:
            raise AgentBackendError("AGENT_TOKEN_BUDGET_EXHAUSTED")
        if self.cost_used + actual_cost > float(self.policy.agent_cost_budget):
            raise AgentBackendError("AGENT_COST_BUDGET_EXHAUSTED")
        if item.get("status") == "STARTED":
            self.active_calls = max(0, self.active_calls - 1)
        self.tokens_reserved = max(0, self.tokens_reserved - reserved_tokens)
        self.cost_reserved = max(0.0, self.cost_reserved - reserved_cost)
        item.update({"status": "COMPLETED", "usage": usage.to_dict(), "cost": cost.to_dict()})
        self.calls_completed += 1
        self.tokens_used += actual_tokens
        self.cost_used += actual_cost
        self._persist()

    def fail(self, agent_call_id: str, *, error_class: str, retryable: bool) -> int:
        item = self._calls[str(agent_call_id)]
        if item.get("status") == "STARTED":
            self.active_calls = max(0, self.active_calls - 1)
        item["status"] = "FAILED"
        item["error_class"] = str(error_class)
        item["retry_count"] = int(item.get("retry_count", 0)) + 1
        item["retryable"] = bool(retryable)
        self.calls_failed += 1
        self._persist()
        return int(item["retry_count"])

    def call(self, agent_call_id: str) -> Mapping[str, Any] | None:
        item = self._calls.get(str(agent_call_id))
        return dict(item) if item else None

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "agent-call-budget-v1", "policy_id": self.policy.policy_id, "policy_hash": self.policy.policy_hash, "calls_reserved": self.calls_reserved, "calls_started": self.calls_started, "calls_completed": self.calls_completed, "calls_failed": self.calls_failed, "calls_remaining": self.calls_remaining, "tokens_reserved": self.tokens_reserved, "tokens_used": self.tokens_used, "tokens_remaining": self.tokens_remaining, "cost_reserved": self.cost_reserved, "cost_used": self.cost_used, "cost_remaining": self.cost_remaining, "active_calls": self.active_calls, "calls": {key: dict(value) for key, value in sorted(self._calls.items())}}

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("policy_hash") not in {None, self.policy.policy_hash}:
            raise AgentBackendError("AGENT_GOVERNANCE_POLICY_HASH_MISMATCH")
        for name in ("calls_reserved", "calls_started", "calls_completed", "calls_failed", "tokens_reserved", "tokens_used", "active_calls"):
            setattr(self, name, int(payload.get(name, 0)))
        for name in ("cost_reserved", "cost_used"):
            setattr(self, name, float(payload.get(name, 0.0)))
        self._calls = {str(key): dict(value) for key, value in payload.get("calls", {}).items()}
        restarted = 0
        for item in self._calls.values():
            if item.get("status") == "STARTED":
                item["status"] = "FAILED"
                item["error_class"] = "PROCESS_RESTART"
                item["retryable"] = True
                item["retry_count"] = int(item.get("retry_count", 0)) + 1
                restarted += 1
        if restarted:
            self.active_calls = 0
            self.calls_failed += restarted

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)


class AgentCallAuditLedgerV1:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._records: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self._records = {str(item.get("audit_record_id") or item["agent_call_id"]): dict(item) for item in payload.get("records", ()) if item.get("agent_call_id")}

    def append(self, record: Mapping[str, Any]) -> str:
        item = dict(record)
        call_id = str(item["agent_call_id"])
        item.setdefault("audit_record_id", f"{call_id}:{item.get('retry_count', 0)}:{item.get('status', '')}")
        item.setdefault("record_hash", stable_hash(item))
        key = str(item["audit_record_id"])
        existing = self._records.get(key)
        if existing is not None:
            if existing.get("record_hash") != item.get("record_hash"):
                raise AgentBackendError("AGENT_CALL_AUDIT_IDENTITY_CONFLICT")
            return "ALREADY_COMMITTED"
        self._records[key] = item
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps({"schema_version": "agent-call-audit-ledger-v1", "records": [self._records[key] for key in sorted(self._records)]}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
        return "COMMITTED"

    def records(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._records[key] for key in sorted(self._records))


class GovernedResearchAgentBackendV1:
    """Apply durable governance around any outcome-blind backend."""

    def __init__(self, backend: ResearchAgentBackendV1, *, policy: AgentGovernancePolicyV1, budget: AgentCallBudgetV1, audit: AgentCallAuditLedgerV1):
        self.backend = backend
        self.policy = policy
        self.budget = budget
        self.audit = audit

    def invoke(self, input: ResearchAgentInputV1, *, run_id: str, batch_id: str, agent_call_id: str) -> ResearchProposalBatchV1:
        call_id = str(agent_call_id)
        call_state = self.budget.call(call_id) or {}
        audit_base = {
            "agent_call_id": call_id,
            "run_id": str(run_id),
            "objective_id": str(input.objective.get("objective_id", "")),
            "batch_id": str(batch_id),
            "backend": self.backend.backend_type,
            "backend_version": self.backend.backend_version,
            "model_route": self.policy.model_route,
            "agent_governance_policy_id": self.policy.policy_id,
            "agent_governance_version": self.policy.version,
            "agent_governance_hash": self.policy.policy_hash,
            "context_hash": input.input_context_hash,
            "request_hash": stable_hash(input.to_dict()),
        }

        try:
            estimate = self.backend.estimate_call(input)
            if not isinstance(estimate, AgentCallEstimateV1):
                raise AgentBackendError("AGENT_ESTIMATE_SCHEMA_INVALID")
        except AgentBackendError as exc:
            estimate = None
            preflight_estimate_error = exc
        except Exception as exc:
            estimate = None
            preflight_estimate_error = AgentBackendError(f"AGENT_ESTIMATE_ERROR:{type(exc).__name__}")

        if estimate is not None:
            audit_base["model_route"] = estimate.model_route
            audit_base["estimate"] = estimate.to_dict()

        def record_preflight_failure(error: AgentBackendError) -> None:
            self.audit.append({
                **audit_base,
                "prompt_template_version": "UNKNOWN",
                "response_hash": None,
                "start_time": None,
                "end_time": now_timestamp(),
                "duration_seconds": 0.0,
                "retry_count": 0,
                "usage": AgentUsageMetadataV1(usage_status="UNAVAILABLE").to_dict(),
                "cost": AgentCostMetadataV1().to_dict(),
                "status": "FAILED",
                "error_class": str(error),
                "retryable": False,
            })

        if estimate is None:
            error = preflight_estimate_error
            record_preflight_failure(error)
            raise error

        if estimate.model_route != self.policy.model_route:
            error = AgentBackendError("AGENT_MODEL_ROUTE_NOT_ALLOWED")
            record_preflight_failure(error)
            raise error
        if estimate.estimate_status not in {"READY", "NOT_APPLICABLE"}:
            error_code = "CODEX_RESOURCE_GOVERNANCE_BLOCKED" if self.backend.backend_type == "CODEX" else "AGENT_USAGE_ESTIMATE_UNBOUNDED"
            error = AgentBackendError(error_code)
            record_preflight_failure(error)
            raise error
        if estimate.estimate_status == "NOT_APPLICABLE" and (estimate.reserved_total_tokens or estimate.estimated_cost_upper_bound):
            error = AgentBackendError("AGENT_ESTIMATE_SCHEMA_INVALID")
            record_preflight_failure(error)
            raise error
        if estimate.reserved_total_tokens < estimate.estimated_input_tokens + estimate.reserved_output_tokens:
            error = AgentBackendError("AGENT_ESTIMATE_NOT_CONSERVATIVE")
            record_preflight_failure(error)
            raise error
        if self.policy.max_output_tokens and estimate.reserved_output_tokens > self.policy.max_output_tokens:
            error = AgentBackendError("AGENT_TOKEN_BUDGET_EXHAUSTED")
            record_preflight_failure(error)
            raise error

        if call_state.get("status") == "COMPLETED":
            replay = getattr(self.backend, "replay_completed", None)
            if callable(replay):
                replayed = replay(input, run_id=str(run_id), batch_id=str(batch_id), agent_call_id=call_id)
                if isinstance(replayed, ResearchProposalBatchV1):
                    self.audit.append({
                        **audit_base,
                        "prompt_template_version": replayed.prompt_template_version,
                        "response_hash": replayed.proposal_batch_hash,
                        "start_time": None,
                        "end_time": now_timestamp(),
                        "duration_seconds": 0.0,
                        "retry_count": int(call_state.get("retry_count", 0)),
                        "usage": AgentUsageMetadataV1(usage_status="REPLAYED").to_dict(),
                        "cost": AgentCostMetadataV1().to_dict(),
                        "status": "REPLAYED",
                        "error_class": None,
                        "retryable": False,
                    })
                    return replayed
            error = AgentBackendError("AGENT_CALL_ALREADY_COMPLETED")
            record_preflight_failure(error)
            raise error
        if call_state and call_state.get("estimate", {}).get("estimate_hash") not in {None, estimate.estimate_hash}:
            error = AgentBackendError("AGENT_ESTIMATE_IDENTITY_CONFLICT")
            record_preflight_failure(error)
            raise error
        retry_count = int(call_state.get("retry_count", 0))
        if call_state.get("status") == "FAILED" and retry_count > self.policy.retry_limit:
            error = AgentBackendError("AGENT_RETRY_LIMIT_EXHAUSTED")
            record_preflight_failure(error)
            raise error
        try:
            if not call_state:
                self.budget.preflight(estimated_tokens=estimate.reserved_total_tokens, estimated_cost=estimate.estimated_cost_upper_bound)
                self.budget.reserve(call_id, estimated_tokens=estimate.reserved_total_tokens, estimated_cost=estimate.estimated_cost_upper_bound, estimate=estimate)
            elif self.budget.active_calls >= self.policy.concurrency:
                raise AgentBackendError("AGENT_CONCURRENCY_EXHAUSTED")
        except AgentBackendError as exc:
            record_preflight_failure(exc)
            raise
        call_state = self.budget.call(call_id) or {}
        retry_count = int(call_state.get("retry_count", 0))
        set_identity = getattr(self.backend, "set_call_identity", None)
        if callable(set_identity):
            set_identity(run_id=str(run_id), batch_id=str(batch_id), agent_call_id=call_id)
        set_reservation = getattr(self.backend, "set_resource_governance_reservation", None)
        if callable(set_reservation):
            set_reservation(call_state)
        set_timeout = getattr(self.backend, "set_timeout_seconds", None)
        if callable(set_timeout):
            set_timeout(self.policy.timeout_seconds)
        while True:
            start = now_timestamp()
            started = time.monotonic()
            self.budget.start(call_id)
            executor = ThreadPoolExecutor(max_workers=1)
            try:
                future = executor.submit(self.backend.invoke, input)
                result = future.result(timeout=self.policy.timeout_seconds)
                if not isinstance(result, ResearchProposalBatchV1):
                    raise AgentBackendError("INVALID_SCHEMA")
                if not result.proposals and not result.empty_reason:
                    raise AgentBackendError("EMPTY_PROPOSAL_BATCH")
                usage_payload = result.provenance.get("usage") if isinstance(result.provenance, Mapping) else None
                cost_payload = result.provenance.get("cost") if isinstance(result.provenance, Mapping) else None
                usage = AgentUsageMetadataV1(**dict(usage_payload)) if isinstance(usage_payload, Mapping) else AgentUsageMetadataV1(usage_status="NOT_APPLICABLE")
                cost = AgentCostMetadataV1(**dict(cost_payload)) if isinstance(cost_payload, Mapping) else AgentCostMetadataV1()
                self.budget.complete(call_id, usage, cost)
                self.audit.append({**audit_base, "prompt_template_version": result.prompt_template_version, "response_hash": result.proposal_batch_hash, "start_time": start, "end_time": now_timestamp(), "duration_seconds": round(time.monotonic() - started, 6), "retry_count": retry_count, "usage": usage.to_dict(), "cost": cost.to_dict(), "status": "COMPLETED", "error_class": None, "retryable": False})
                return result
            except FutureTimeoutError as exc:
                error = AgentBackendError("TIMEOUT")
                retryable = True
                error.__cause__ = exc
            except AgentBackendError as exc:
                error = exc
                retryable = str(exc) in {"TIMEOUT", "AGENT_BACKEND_ERROR"} or str(exc).startswith(("AGENT_BACKEND_ERROR", "BACKEND_EXCEPTION", "CODEX_TIMEOUT"))
            except Exception as exc:
                error = AgentBackendError(f"BACKEND_EXCEPTION:{type(exc).__name__}")
                retryable = True
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            retry_count = self.budget.fail(call_id, error_class=type(error).__name__, retryable=retryable)
            self.audit.append({**audit_base, "prompt_template_version": "UNKNOWN", "response_hash": None, "start_time": start, "end_time": now_timestamp(), "duration_seconds": round(time.monotonic() - started, 6), "retry_count": retry_count, "usage": AgentUsageMetadataV1(usage_status="UNAVAILABLE").to_dict(), "cost": AgentCostMetadataV1().to_dict(), "status": "FAILED", "error_class": str(error), "retryable": retryable})
            if not retryable or retry_count > self.policy.retry_limit:
                raise error


class ResearchAgentInputBuilderV1:
    """Build the design-time-only input passed to a proposal backend."""

    def build(self, *, run_id: str, batch_id: str, objective: Any, policy_identity: Mapping[str, Any], no_outcome_context: NoOutcomeResearchContextV1, failure_knowledge: Any = None, candidate_neighborhood: Any = None, family_mechanism_constraints: Mapping[str, Any] | None = None, complexity_constraints: Mapping[str, Any] | None = None, factor_event_catalog: Mapping[str, Any] | Sequence[Mapping[str, Any]] = (), proposal_budget_view: Mapping[str, Any] | None = None) -> ResearchAgentInputV1:
        objective_payload = objective.to_dict() if hasattr(objective, "to_dict") else dict(objective)
        objective_payload = self._strip_outcome_fields(objective_payload)
        failure_payload = failure_knowledge.to_dict() if hasattr(failure_knowledge, "to_dict") else failure_knowledge or {}
        neighborhood_payload = candidate_neighborhood.to_dict() if hasattr(candidate_neighborhood, "to_dict") else candidate_neighborhood or {}
        input = ResearchAgentInputV1(
            run_id=run_id,
            batch_id=batch_id,
            objective=objective_payload,
            policy_identity=dict(policy_identity),
            no_outcome_context=no_outcome_context,
            failure_knowledge_view=failure_payload,
            factor_event_catalog=factor_event_catalog,
            candidate_novelty_neighborhood=neighborhood_payload,
            family_mechanism_constraints=dict(family_mechanism_constraints or {}),
            complexity_constraints=dict(complexity_constraints or {}),
            proposal_budget_view=dict(proposal_budget_view or {}),
        )
        assert_agent_design_time(input.to_dict())
        return input

    @staticmethod
    def _strip_outcome_fields(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): ResearchAgentInputBuilderV1._strip_outcome_fields(nested) for key, nested in value.items() if str(key).lower() not in AGENT_FORBIDDEN_FIELDS}
        if isinstance(value, (list, tuple)):
            return [ResearchAgentInputBuilderV1._strip_outcome_fields(item) for item in value]
        return value
