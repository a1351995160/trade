"""AIResearchFactoryOrchestratorV1 and synthetic-only factory runtime."""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections import Counter
from pathlib import Path
from typing import Any, Mapping
import json

from .artifact_graph import ResearchArtifactGraphV1
from .agent_backend import AgentCallAuditLedgerV1, AgentCallBudgetV1, AgentGovernancePolicyV1, GovernedResearchAgentBackendV1
from .batch import FactoryResearchPlannerAdapterV1, ResearchBatchPlanV1
from .baseline import BaselineControlRegistryV1
from .budget import BudgetExhaustedError, SearchBudgetRegistryV1
from .common import now_timestamp, stable_hash
from .context import NoOutcomeResearchContextV1, PerformanceBlindGuard
from .diversity import FamilyDiversityEnforcerV1, FamilyDiversityPolicyV1
from .failure_adapter import FailureKnowledgeAdapterV1, FailureKnowledgeSnapshotV1
from .history import CumulativeResearchHistoryV1
from .objective import ResearchObjectiveV1
from .state_machine import ResearchBatchState, ResearchBatchStateMachineV1
from .status import ResearchFactoryStatusV1
from .strategy_adapter import ResearchStrategyRegistryFacadeV1
from .trial_adapter import ResearchFactoryTrialLedgerFacadeV1
from .commit import DurableCommitLedgerV1
from .history import CumulativeResearchHistoryStoreV1
from .novelty import CandidateNeighborhoodIndexV1, CandidateNoveltyGateV2
from .sample_feasibility import (
    BLOCKED_INSUFFICIENT_FEASIBILITY,
    CandidateSampleFeasibilityInputV1,
    CandidateSampleFeasibilityPreflightV1,
    PASS,
    UNKNOWN,
)
from .real_sample_feasibility import RealSampleFeasibilityProviderV1
from chanlun_trader.research.validation_policy_v2 import (
    ValidationPolicyV2Error,
    default_validation_decision_policy_v2,
    load_validation_decision_policy_v2,
)


class CanonicalDependencyViolation(RuntimeError):
    pass


def canonical_dependency_manifest() -> dict[str, Any]:
    dependencies = {
        "factor_engine": ("chanlun_trader.research.unified_factor", "UnifiedFactorRegistry", "UNIFIED_FACTOR_LIBRARY_V1"),
        "hypothesis_generator": ("chanlun_trader.research.hypothesis", "AIHypothesisGenerator", "AI_HYPOTHESIS_GENERATOR_V1"),
        "candidate_builder": ("chanlun_trader.research.strategy_candidate", "StrategyCandidateBuilder", "STRATEGY_CANDIDATE_BUILDER_V1"),
        "compiler": ("chanlun_trader.research.strategy_semantic", "StrategyCandidateCompilerV2", "STRATEGY_CANDIDATE_COMPILER_V2"),
        "data_router": ("chanlun_trader.research.data_router", "ResearchDataRouter", "RESEARCH_DATA_ROUTING_POLICY_V1"),
        "backtest_engine": ("chanlun_trader.engine.engine", "BacktestEngineV2", "BACKTEST_ENGINE_V2"),
        "exit_engine": ("chanlun_trader.engine.portfolio_exit", "PortfolioExitEvaluatorV1", "PORTFOLIO_EXIT_EVALUATOR_V1"),
        "validation_pipeline": ("chanlun_trader.research.strategy_validation", "run_engine_research_trial", "AUTOMATED_STRATEGY_VALIDATION_CORRECTED_V3"),
        "trial_registry_adapter": ("chanlun_trader.research.strategy_validation", "TrialRegistryV1", "TRIAL_REGISTRY_V1"),
        "failure_library_adapter": ("chanlun_trader.research.experiment", "FailureLibrary", "FAILURE_LIBRARY_V1"),
        "strategy_registry_adapter": ("chanlun_trader.research.strategy_candidate", "StrategyCandidateRegistry", "STRATEGY_CANDIDATE_REGISTRY_V1"),
    }
    items = {
        key: {"module": module, "component": component, "version": version, "identity_hash": stable_hash([module, component, version])}
        for key, (module, component, version) in dependencies.items()
    }
    return {
        "schema_version": "research-factory-canonical-dependencies-v1",
        "manifest_id": "RESEARCH_FACTORY_CANONICAL_DEPENDENCIES_V1",
        "dependencies": items,
        "legacy_paths_fail_closed": True,
        "legacy_forbidden_components": ["StrategyCandidateCompiler", "BacktestRunner", "LegacyFactorRegistry"],
        "manifest_hash": stable_hash(items),
    }


RESEARCH_FACTORY_CANONICAL_DEPENDENCIES_V1 = canonical_dependency_manifest()


def assert_canonical_dependency(name: str, component: Any) -> None:
    expected = RESEARCH_FACTORY_CANONICAL_DEPENDENCIES_V1["dependencies"].get(name)
    if expected is None:
        raise CanonicalDependencyViolation(f"unknown canonical dependency: {name}")
    actual = getattr(component, "__name__", component.__class__.__name__)
    if actual != expected["component"]:
        raise CanonicalDependencyViolation(f"legacy or non-canonical dependency for {name}: {actual}")


class SyntheticFactoryRuntimeV1:
    """Deterministic mock runtime. It never reads price, return, or validation evidence."""

    def __init__(self, *, outcomes: tuple[str, ...] = ("REJECTED", "RESEARCH_PASSED"), engine_error: bool = False):
        self.outcomes = outcomes
        self.engine_error = engine_error
        self.generator_calls = 0
        self.builder_calls = 0
        self.compiler_calls = 0
        self.validator_calls = 0

    def generate_hypotheses(self, context: NoOutcomeResearchContextV1, plan: ResearchBatchPlanV1) -> list[dict[str, Any]]:
        PerformanceBlindGuard.assert_blind(context.to_dict())
        self.generator_calls += 1
        return [
            {"hypothesis_id": f"{plan.batch_id}_H01", "family_id": "event_reversal", "mechanism": "event_reversal", "factor_ids": [f"SYNTHETIC_FACTOR_A_B{plan.batch_number:02d}"], "status": "READY_FOR_STRATEGY_BUILD"},
            {"hypothesis_id": f"{plan.batch_id}_H02", "family_id": "mean_reversion", "mechanism": "mean_reversion", "factor_ids": [f"SYNTHETIC_FACTOR_B_B{plan.batch_number:02d}"], "status": "READY_FOR_STRATEGY_BUILD"},
        ][:plan.max_hypotheses]

    def build_candidates(self, hypotheses: list[Mapping[str, Any]], context: NoOutcomeResearchContextV1, plan: ResearchBatchPlanV1) -> list[dict[str, Any]]:
        PerformanceBlindGuard.assert_blind(hypotheses)
        self.builder_calls += 1
        candidates = []
        synthetic_calendar = tuple(f"SYNTHETIC_SESSION_{index:03d}" for index in range(1, 181))
        for hypothesis in hypotheses[:plan.max_candidates]:
            candidate = {
                "candidate_id": str(hypothesis["hypothesis_id"]).replace("_H", "_C"),
                "hypothesis_id": hypothesis["hypothesis_id"],
                "family_id": hypothesis["family_id"],
                "mechanism": hypothesis["mechanism"],
                "factor_ids": tuple(hypothesis["factor_ids"]),
                "holding_period_days": 5,
                "semantic_status": "SEMANTIC_EXECUTABLE",
                "sample_feasibility": {
                    "holding_horizon": 5,
                    "top_n": 1,
                    "max_positions": 3,
                    "calendar_identity": "SYNTHETIC_SESSION_CALENDAR_V1",
                    "calendar_sessions": synthetic_calendar,
                    "observations": tuple({
                        "signal_id": f"{hypothesis['hypothesis_id']}:S{i:03d}",
                        "signal_date": f"SYNTHETIC_SESSION_{1 + (i - 1) * 6:03d}",
                        "symbol": f"SYN{i:03d}",
                        "rank": 1,
                        "signal_qualified": True,
                        "pit_eligible": True,
                        "data_complete": True,
                        "execution_eligible": True,
                        "next_session_eligible": True,
                        "t1_eligible": True,
                        "holding_complete": True,
                        "base_affordable": True,
                        "small_capital_affordable": True,
                        "portfolio_feasible": True,
                        "prior_holdings_known": True,
                    } for i in range(1, 31)),
                },
            }
            candidate["candidate_hash"] = stable_hash(candidate)
            candidates.append(candidate)
        return candidates

    def compile_candidate(self, candidate: Mapping[str, Any], context: NoOutcomeResearchContextV1) -> dict[str, Any]:
        PerformanceBlindGuard.assert_blind(candidate)
        self.compiler_calls += 1
        return dict(candidate)

    def stage1(self, candidate: Mapping[str, Any], context: NoOutcomeResearchContextV1) -> dict[str, Any]:
        PerformanceBlindGuard.assert_blind(candidate)
        return {"status": "PASS", "performance_accessed": False, "reason_codes": []}

    def validate(self, candidate: Mapping[str, Any], context: NoOutcomeResearchContextV1, index: int) -> dict[str, Any]:
        PerformanceBlindGuard.assert_blind(candidate)
        self.validator_calls += 1
        if self.engine_error:
            return {"classification": "ENGINE_ERROR", "reason_codes": ["ENGINE_ERROR"]}
        return {"classification": self.outcomes[index % len(self.outcomes)], "reason_codes": []}


@dataclass(frozen=True)
class FactoryRunResultV1:
    objective: ResearchObjectiveV1
    batch_plan: ResearchBatchPlanV1
    state: str
    hypotheses: tuple[Mapping[str, Any], ...]
    candidates: tuple[Mapping[str, Any], ...]
    trial_records: tuple[Mapping[str, Any], ...]
    failure_snapshot: FailureKnowledgeSnapshotV1
    status: ResearchFactoryStatusV1
    checkpoint_path: str | None
    next_batch_plan: ResearchBatchPlanV1 | None = None
    real_performance_trial_executed: bool = False
    prospective_observation_executed: bool = False
    final_test_access: Mapping[str, int] = None  # type: ignore[assignment]
    recommendation: str = "DISABLED"
    real_order_execution: str = "DISABLED"

    def __post_init__(self) -> None:
        object.__setattr__(self, "final_test_access", dict(self.final_test_access or {"physical": 0, "analytical": 0, "decision": 0}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "factory-run-result-v1",
            "objective_id": self.objective.objective_id,
            "batch_id": self.batch_plan.batch_id,
            "state": self.state,
            "hypotheses": [dict(item) for item in self.hypotheses],
            "candidates": [dict(item) for item in self.candidates],
            "trial_records": [dict(item) for item in self.trial_records],
            "failure_snapshot": self.failure_snapshot.to_dict(),
            "status": self.status.to_dict(),
            "checkpoint_path": self.checkpoint_path,
            "next_batch_plan": self.next_batch_plan.to_dict() if self.next_batch_plan else None,
            "real_performance_trial_executed": self.real_performance_trial_executed,
            "prospective_observation_executed": self.prospective_observation_executed,
            "final_test_access": dict(self.final_test_access),
            "recommendation": self.recommendation,
            "real_order_execution": self.real_order_execution,
        }


class AIResearchFactoryOrchestratorV1:
    CODE_IDENTITY = "AI_RESEARCH_FACTORY_ORCHESTRATOR_V1"

    def __init__(self, objective: ResearchObjectiveV1, *, root: str | Path = ".", output_dir: str | Path | None = None, runtime: SyntheticFactoryRuntimeV1 | None = None, planner: FactoryResearchPlannerAdapterV1 | None = None, family_diversity_policy: FamilyDiversityPolicyV1 | None = None, agent_backend: Any | None = None, sample_feasibility_provider: Any | None = None):
        self.root = Path(root)
        self.output_dir = Path(output_dir) if output_dir else self.root / "reports" / "research_factory"
        self.runtime = runtime or SyntheticFactoryRuntimeV1()
        self.agent_backend = agent_backend
        self.agent_governance_policy = AgentGovernancePolicyV1(max_agent_calls=max(3, int(objective.max_batches)))
        if planner is None:
            policy_path = self.root / "data/research/strategy_validation/validation_decision_policy_v2.json"
            if policy_path.exists():
                policy, _ = load_validation_decision_policy_v2(policy_path)
            else:
                policy = default_validation_decision_policy_v2()
            self.planner = FactoryResearchPlannerAdapterV1(validation_policy=policy)
        else:
            self.planner = planner
        self.validation_policy = self.planner.validation_policy
        self.objective = objective
        self.budget = SearchBudgetRegistryV1(objective.objective_id, self.output_dir / "search_budget_registry.json")
        trial_registry_path = self.output_dir / "trial_registry.json"
        try:
            from chanlun_trader.research.strategy_validation import TrialRegistryV1
            canonical_trial_registry = TrialRegistryV1(trial_registry_path)
        except Exception:
            canonical_trial_registry = None
        self.trial_ledger = ResearchFactoryTrialLedgerFacadeV1(trial_registry=canonical_trial_registry, path=self.output_dir / "factory_trial_ledger.json")
        self.failure_adapter = FailureKnowledgeAdapterV1()
        self.strategy_registry = ResearchStrategyRegistryFacadeV1(path=self.output_dir / "strategy_registry.json")
        initial_history = CumulativeResearchHistoryV1(
            objective.objective_id,
            policy_id=self.validation_policy.policy_id,
            policy_version=self.validation_policy.policy_version,
            policy_hash=self.validation_policy.policy_hash,
            source_run_id=None,
        )
        self.history_store = CumulativeResearchHistoryStoreV1(self.output_dir / "cumulative_history.json", initial_history)
        self.history = self.history_store.current
        self.artifact_graph = ResearchArtifactGraphV1(self.output_dir / "artifact_graph.json")
        self.commit_ledger = DurableCommitLedgerV1(self.output_dir / "commit_markers.json")
        self.agent_budget = AgentCallBudgetV1(self.output_dir / "agent_call_budget.json", self.agent_governance_policy)
        self.agent_audit = AgentCallAuditLedgerV1(self.output_dir / "agent_call_audit.json")
        self.baseline_registry = BaselineControlRegistryV1()
        self.novelty_gate = CandidateNoveltyGateV2()
        self.neighborhood_index = CandidateNeighborhoodIndexV1(self.output_dir / "candidate_neighborhood_index.json")
        self.family_diversity_policy = family_diversity_policy or FamilyDiversityPolicyV1(2, 1, min(2, max(1, objective.max_batches)), 0, {"source": "PLAN_FAMILY_QUOTAS"})
        self.family_diversity_enforcer = FamilyDiversityEnforcerV1(self.family_diversity_policy)
        self.sample_feasibility = CandidateSampleFeasibilityPreflightV1(self.validation_policy)
        self._custom_sample_feasibility_provider = sample_feasibility_provider
        self.sample_feasibility_provider = sample_feasibility_provider or RealSampleFeasibilityProviderV1(self.root)
        self._default_real_sample_feasibility_provider = sample_feasibility_provider is None
        self._sample_feasibility_rows: list[dict[str, Any]] = []
        self._sample_blocked_records: list[dict[str, Any]] = []
        self._budget_registered = False

    def sample_feasibility_input(self, candidate: Mapping[str, Any] | Any, *, backend_type: str = "UNKNOWN") -> CandidateSampleFeasibilityInputV1:
        use_provider = self.sample_feasibility_provider is not None and (
            not self._default_real_sample_feasibility_provider or backend_type == "REAL_FACTORY"
        )
        if use_provider:
            provided = self.sample_feasibility_provider(candidate, self.validation_policy)
            if isinstance(provided, CandidateSampleFeasibilityInputV1):
                return provided
            if isinstance(provided, Mapping):
                return CandidateSampleFeasibilityInputV1(**dict(provided))
            raise TypeError("sample_feasibility_provider must return CandidateSampleFeasibilityInputV1 or a mapping")
        return CandidateSampleFeasibilityInputV1.from_candidate(candidate, self.validation_policy, backend_type=backend_type)

    def run_sample_feasibility(self, candidate: Mapping[str, Any] | Any, *, backend_type: str = "UNKNOWN") -> dict[str, Any]:
        result = self.sample_feasibility.run(self.sample_feasibility_input(candidate, backend_type=backend_type))
        row = result.to_dict()
        self._sample_feasibility_rows.append(row)
        return row

    def _register_budget(self, plan: ResearchBatchPlanV1, candidates: list[Mapping[str, Any]] = ()) -> None:
        if not self._budget_registered:
            self.budget.register_objective(self.objective.max_total_trials)
            self._budget_registered = True
        if ("batch", plan.batch_id) not in self.budget._buckets:
            self.budget.register_batch(plan.batch_id, plan.max_performance_trials)
        for family_id, limit in plan.family_quotas.items():
            if ("family", family_id) not in self.budget._buckets:
                self.budget.register_family(family_id, limit)
        for candidate in candidates:
            candidate_id = str(candidate["candidate_id"])
            if ("candidate", candidate_id) not in self.budget._buckets:
                self.budget.register_candidate(candidate_id, 1)

    def _checkpoint(self, machine: ResearchBatchStateMachineV1, plan: ResearchBatchPlanV1, hypotheses: list[Mapping[str, Any]], candidates: list[Mapping[str, Any]], failure_snapshot: FailureKnowledgeSnapshotV1 | None = None, *, trial_records: list[Mapping[str, Any]] = (), stop_reason: str | None = None) -> Path:
        path = self.output_dir / "checkpoints" / f"{plan.batch_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "research-factory-checkpoint-v1",
            "objective_hash": self.objective.objective_hash,
            "batch_plan_hash": plan.plan_hash,
            "hypothesis_set_hash": stable_hash(hypotheses),
            "candidate_set_hash": stable_hash(candidates),
            "budget_registry_head_hash": self.budget.head_hash,
            "trial_ledger_head_hash": self.trial_ledger.head_hash,
            "failure_snapshot_hash": failure_snapshot.snapshot_hash if failure_snapshot else None,
            "history_hash": self.history.history_hash,
            "history_policy_id": self.history.policy_id,
            "history_policy_version": self.history.policy_version,
            "history_policy_hash": self.history.policy_hash,
            "validation_policy_id": plan.validation_policy_id,
            "validation_policy_version": plan.validation_policy_version,
            "validation_policy_hash": plan.validation_policy_hash,
            "decision_family_id": plan.decision_family_id,
            "family_contract_hash": plan.family_contract_hash,
            "dataset_hash": stable_hash("SYNTHETIC_DATASET_ONLY"),
            "code_identity": self.CODE_IDENTITY,
            "state": machine.state.value,
            "batch_id": plan.batch_id,
            "plan": plan.to_dict(),
            "hypotheses": [dict(item) for item in hypotheses],
            "candidates": [dict(item) for item in candidates],
            "trial_records": [dict(item) for item in trial_records],
            "completed_trial_ids": [str(item.get("trial_id")) for item in trial_records if item.get("status") == "COMPLETED"],
            "performance_complete_trial_ids": [str(item.get("trial_id")) for item in trial_records if item.get("performance_accessed")],
            "pending_adjudication_trial_ids": [str(item.get("trial_id")) for item in trial_records if item.get("final_adjudication_pending")],
            "final_adjudication_pending": any(bool(item.get("final_adjudication_pending")) for item in trial_records),
            "final_adjudicated_trial_ids": [str(item.get("trial_id")) for item in trial_records if item.get("classification") and item.get("status") == "COMPLETED"],
            "registry_committed_trial_ids": [str(item.get("trial_id")) for item in trial_records if item.get("final_decision_id")],
            "failure_snapshot": failure_snapshot.to_dict() if failure_snapshot else None,
            "sample_feasibility_rows": [dict(item) for item in self._sample_feasibility_rows],
            "sample_blocked_records": [dict(item) for item in self._sample_blocked_records],
            "stop_reason": stop_reason,
            "state_machine": machine.audit(),
            "created_at": now_timestamp(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def _empty_failure_snapshot(self, plan: ResearchBatchPlanV1) -> FailureKnowledgeSnapshotV1:
        return FailureKnowledgeSnapshotV1(snapshot_id=f"{plan.batch_id}_FAILURE_SNAPSHOT", parent_snapshot_id=plan.failure_knowledge_snapshot_id)

    def _materialize_terminal_checkpoint(self, payload: Mapping[str, Any], plan: ResearchBatchPlanV1) -> FactoryRunResultV1:
        from .failure_adapter import FailureKnowledgeEntryV1
        failure_payload = payload.get("failure_snapshot") or {"snapshot_id": f"{plan.batch_id}_FAILURE_SNAPSHOT", "entries": ()}
        if not payload.get("failure_snapshot"):
            for artifact_path in (
                self.output_dir / plan.batch_id / "failure_extraction.json",
                self.output_dir / plan.batch_id / "resume_correction_v1" / "failure_extraction.json",
            ):
                if artifact_path.exists():
                    failure_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
                    checkpoint_path = self.output_dir / "checkpoints" / f"{plan.batch_id}.json"
                    upgraded = dict(payload)
                    upgraded["failure_snapshot"] = failure_payload
                    checkpoint_path.write_text(json.dumps(upgraded, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                    break
        failure_snapshot = FailureKnowledgeSnapshotV1(
            snapshot_id=str(failure_payload.get("snapshot_id")),
            entries=tuple(FailureKnowledgeEntryV1(**{key: value for key, value in item.items() if key in {"category", "mechanism", "high_level_reason", "constraints", "source_trial_ids", "reason_code"}}) for item in failure_payload.get("entries", ())),
            parent_snapshot_id=failure_payload.get("parent_snapshot_id"),
            created_at=str(failure_payload.get("created_at", "")),
        )
        trial_records = tuple(dict(item) for item in payload.get("trial_records", ()))
        counts = Counter(str(item.get("classification")) for item in trial_records)
        sample_counts = Counter(str(item.get("status")) for item in payload.get("sample_feasibility_rows", ()))
        status_payload = payload.get("status") or {}
        status_fields = {field for field in ResearchFactoryStatusV1.__dataclass_fields__}
        status = ResearchFactoryStatusV1(**{key: status_payload[key] for key in status_fields if key in status_payload}) if status_payload else ResearchFactoryStatusV1(
            objective_id=self.objective.objective_id,
            current_batch_id=plan.batch_id,
            batch_state=str(payload.get("state")),
            batch_number=plan.batch_number,
            max_batches=self.objective.max_batches,
            trial_budget_total=self.objective.max_total_trials,
            trial_budget_used=sum(1 for item in trial_records if item.get("performance_accessed")),
            hypotheses_count=len(payload.get("hypotheses", ())),
            candidate_count=len(payload.get("candidates", ())),
            trials_started=len(trial_records),
            trials_completed=sum(1 for item in trial_records if item.get("status") == "COMPLETED"),
            research_passed_count=counts.get("RESEARCH_PASSED", 0),
            promising_count=counts.get("PROMISING", 0),
            weak_count=counts.get("WEAK", 0),
            rejected_count=counts.get("REJECTED", 0),
            blocked_count=counts.get("ENGINEERING_BLOCKED", 0) + counts.get("BLOCKED", 0),
            candidates_sample_feasibility_passed=sample_counts.get(PASS, 0),
            candidates_sample_feasibility_blocked=sample_counts.get(BLOCKED_INSUFFICIENT_FEASIBILITY, 0),
            candidates_sample_feasibility_unknown=sample_counts.get(UNKNOWN, 0),
            failure_class_counts=dict(Counter(item.category for item in failure_snapshot.entries)),
            stop_reason=payload.get("stop_reason"),
        )
        return FactoryRunResultV1(self.objective, plan, str(payload.get("state")), tuple(payload.get("hypotheses", ())), tuple(payload.get("candidates", ())), trial_records, failure_snapshot, status, str(self.output_dir / "checkpoints" / f"{plan.batch_id}.json"), None, False, False, {"physical": 0, "analytical": 0, "decision": 0}, "DISABLED", "DISABLED")

    def run_synthetic(self, *, batch_number: int = 1, plan: ResearchBatchPlanV1 | None = None, stop_after: str | None = None, crash_at: str | None = None) -> FactoryRunResultV1:
        from chanlun_trader.research.strategy_validation import FinalResearchAdjudicatorV1, benjamini_hochberg

        def maybe_crash(boundary: str) -> None:
            if crash_at == boundary:
                raise RuntimeError(f"SYNTHETIC_CRASH_INJECTED:{boundary}")

        plan = plan or self.planner.create_plan(self.objective, batch_number=batch_number)
        existing_checkpoint_path = self.output_dir / "checkpoints" / f"{plan.batch_id}.json"
        resume_payload: Mapping[str, Any] | None = None
        if existing_checkpoint_path.exists():
            existing_checkpoint = json.loads(existing_checkpoint_path.read_text(encoding="utf-8"))
            persisted_plan = existing_checkpoint.get("plan")
            if isinstance(persisted_plan, Mapping) and str(persisted_plan.get("batch_id")) == plan.batch_id:
                plan = ResearchBatchPlanV1(**{key: value for key, value in persisted_plan.items() if key in ResearchBatchPlanV1.__dataclass_fields__})
            if existing_checkpoint.get("state") in {ResearchBatchState.COMPLETED.value, ResearchBatchState.BLOCKED.value, ResearchBatchState.ENGINEERING_BLOCKED.value, ResearchBatchState.BUDGET_EXHAUSTED.value}:
                return self._materialize_terminal_checkpoint(existing_checkpoint, plan)
            if existing_checkpoint.get("state") == ResearchBatchState.PERFORMANCE_VALIDATING.value and existing_checkpoint.get("final_adjudication_pending") and len(existing_checkpoint.get("trial_records", ())) >= len(existing_checkpoint.get("candidates", ())):
                return self._resume_synthetic_pending_checkpoint(existing_checkpoint, plan)
            if existing_checkpoint.get("state") in {ResearchBatchState.CANDIDATES_FROZEN.value, ResearchBatchState.STAGE1_VALIDATING.value, ResearchBatchState.PERFORMANCE_VALIDATING.value} and existing_checkpoint.get("candidates"):
                resume_payload = existing_checkpoint
        policy = self.validation_policy
        self.artifact_graph.add_node(f"objective:{self.objective.objective_id}", "Objective", self.objective.to_dict())
        self.artifact_graph.add_node(f"batch:{plan.batch_id}", "Batch", plan.to_dict())
        self.artifact_graph.add_edge(f"batch:{plan.batch_id}", "PLANNED_BY", f"objective:{self.objective.objective_id}")
        self._register_budget(plan)
        context = NoOutcomeResearchContextV1(
            factor_capability_summary=({"factor_id": "SYNTHETIC_FACTOR_A", "pit_status": "PIT_VERIFIED", "data_status": "AVAILABLE"}, {"factor_id": "SYNTHETIC_FACTOR_B", "pit_status": "PIT_VERIFIED", "data_status": "AVAILABLE"}),
            mechanism_history=tuple({"mechanism": item, "history_available": True} for item in self.objective.mechanism_scope),
            failure_class_summaries=tuple({"failure_category": key, "count": value} for key, value in self.history.classification_counts().items()),
            constraints={"holding_horizon": list(self.objective.holding_horizon), "preferred_horizon": list(self.objective.preferred_horizon), "max_complexity": dict(plan.complexity_budget)},
        )
        if resume_payload is not None:
            machine = ResearchBatchStateMachineV1(plan.batch_id, initial_state=ResearchBatchState(resume_payload["state"]), path=self.output_dir / "state" / f"{plan.batch_id}.json")
            hypotheses = [dict(item) for item in resume_payload.get("hypotheses", ())]
            compiled_candidates = [dict(item) for item in resume_payload.get("candidates", ())]
            trial_records: list[Mapping[str, Any]] = [dict(item) for item in resume_payload.get("trial_records", ())]
            self._sample_feasibility_rows = [dict(item) for item in resume_payload.get("sample_feasibility_rows", ())]
            self._sample_blocked_records = [dict(item) for item in resume_payload.get("sample_blocked_records", ())]
            checkpoint_path = existing_checkpoint_path
            blocked_count = 0
        else:
            machine = ResearchBatchStateMachineV1(plan.batch_id, path=self.output_dir / "state" / f"{plan.batch_id}.json")
            self._sample_feasibility_rows = []
            self._sample_blocked_records = []
            blocked_count = 0
            machine.transition(ResearchBatchState.BUDGET_RESERVED, "objective, batch and family budgets registered")
            machine.transition(ResearchBatchState.DESIGNING, "performance-blind design context created")
            self.baseline_registry.register_plan(plan.batch_id, plan.baseline_plan, tuple(self.objective.mechanism_scope))
            hypotheses = self.runtime.generate_hypotheses(context, plan)
            PerformanceBlindGuard.assert_blind(hypotheses)
            hypothesis_hash = stable_hash(hypotheses)
            machine.transition(ResearchBatchState.HYPOTHESES_FROZEN, f"hypothesis set frozen: {hypothesis_hash}")
            for hypothesis in hypotheses:
                self.artifact_graph.add_node(f"hypothesis:{hypothesis['hypothesis_id']}", "Hypothesis", hypothesis)
                self.artifact_graph.add_edge(f"hypothesis:{hypothesis['hypothesis_id']}", "GENERATED_FROM", f"batch:{plan.batch_id}")
            checkpoint_path = self._checkpoint(machine, plan, hypotheses, [])
            maybe_crash("after_hypotheses_freeze")
            if stop_after == ResearchBatchState.HYPOTHESES_FROZEN.value:
                snapshot = self._empty_failure_snapshot(plan)
                return self._result(plan, machine, hypotheses, [], [], snapshot, checkpoint_path, stop_reason="PAUSED_AT_HYPOTHESES_FROZEN")
            candidates = self.runtime.build_candidates(hypotheses, context, plan)
            PerformanceBlindGuard.assert_blind(candidates)
            candidate_hash = stable_hash(candidates)
            compiled_candidates = [self.runtime.compile_candidate(candidate, context) for candidate in candidates]
            if stable_hash(candidates) != candidate_hash:
                raise RuntimeError("candidate set changed during compile")
            accepted_candidates: list[Mapping[str, Any]] = []
            for candidate in compiled_candidates:
                novelty = self.novelty_gate.evaluate(candidate, same_batch_candidates=accepted_candidates, historical_candidates=self.neighborhood_index.all())
                if novelty.allowed:
                    accepted_candidates.append(candidate)
            diversity = self.family_diversity_enforcer.freeze(accepted_candidates, required_count=None)
            compiled_candidates = list(diversity.accepted_candidates)
            self.neighborhood_index.add_many(compiled_candidates)
            candidate_hash = stable_hash(compiled_candidates)
            self._register_budget(plan, compiled_candidates)
            for candidate in compiled_candidates:
                candidate_id = str(candidate["candidate_id"])
                candidate_reservation = self.budget.reserve("candidate", candidate_id, 1)
                self.budget.consume(candidate_reservation)
                self.artifact_graph.add_node(f"candidate:{candidate_id}", "Candidate", candidate)
                self.artifact_graph.add_edge(f"candidate:{candidate_id}", "GENERATED_FROM", f"hypothesis:{candidate['hypothesis_id']}")
                self.strategy_registry.register(candidate_id, str(candidate["candidate_hash"]), str(candidate["family_id"]))
                self.strategy_registry.transition(candidate_id, "SEMANTIC_READY")
                self.strategy_registry.transition(candidate_id, "VALIDATION_ELIGIBLE")
            machine.transition(ResearchBatchState.CANDIDATES_FROZEN, f"candidate set frozen: {candidate_hash}")
            checkpoint_path = self._checkpoint(machine, plan, hypotheses, compiled_candidates)
            maybe_crash("after_candidate_freeze")
            if stop_after == ResearchBatchState.CANDIDATES_FROZEN.value:
                snapshot = self._empty_failure_snapshot(plan)
                return self._result(plan, machine, hypotheses, compiled_candidates, [], snapshot, checkpoint_path, stop_reason="PAUSED_AT_CANDIDATES_FROZEN")
        if resume_payload is not None and machine.state == ResearchBatchState.PERFORMANCE_VALIDATING and self._sample_feasibility_rows:
            sample_rows_by_id = {str(item.get("candidate_id")): item for item in self._sample_feasibility_rows}
            eligible = [candidate for candidate in compiled_candidates if sample_rows_by_id.get(str(candidate.get("candidate_id")), {}).get("status") == PASS]
        else:
            machine.transition(ResearchBatchState.STAGE1_VALIDATING, "candidate freeze complete; sample-feasibility preflight begins")
            sample_blocked_records: list[dict[str, Any]] = []
            sample_eligible: list[Mapping[str, Any]] = []
            for candidate in compiled_candidates:
                row = self.run_sample_feasibility(candidate, backend_type="SYNTHETIC")
                candidate_id = str(candidate["candidate_id"])
                if row.get("status") != PASS:
                    blocked_count += 1
                    self.strategy_registry.transition(candidate_id, "VALIDATION_BLOCKED", evidence_ref="SAMPLE_FEASIBILITY_PREFLIGHT")
                    sample_blocked_records.append({
                        "trial_id": f"{plan.batch_id}:SAMPLE_FEASIBILITY:{candidate_id}",
                        "candidate_id": candidate_id,
                        "family_id": str(candidate.get("family_id", candidate.get("mechanism", "UNKNOWN"))),
                        "mechanism": str(candidate.get("mechanism", "UNKNOWN")),
                        "status": "BLOCKED",
                        "classification": "BLOCKED",
                        "failure_category": "SAMPLE_FEASIBILITY_FAILURE",
                        "performance_accessed": False,
                        "reason_codes": tuple(row.get("reason_codes", ())),
                        "sample_feasibility_status": row.get("status"),
                    })
                else:
                    sample_eligible.append(candidate)
            self._sample_blocked_records = sample_blocked_records
            eligible = list(sample_eligible)
            if not eligible:
                machine.transition(ResearchBatchState.BLOCKED, "sample-feasibility preflight blocked all frozen candidates; predictive gate remains closed")
                failure_snapshot = self.failure_adapter.snapshot_from_trials(sample_blocked_records, snapshot_id=f"{plan.batch_id}_FAILURE_SNAPSHOT", parent_snapshot_id=plan.failure_knowledge_snapshot_id)
                checkpoint_path = self._checkpoint(machine, plan, hypotheses, compiled_candidates, failure_snapshot, trial_records=(), stop_reason="SAMPLE_FEASIBILITY_BLOCKED")
                return self._result(plan, machine, hypotheses, compiled_candidates, [], failure_snapshot, checkpoint_path, stop_reason="SAMPLE_FEASIBILITY_BLOCKED", blocked_count=blocked_count)
            stage1_eligible: list[Mapping[str, Any]] = []
            for candidate in eligible:
                stage1 = self.runtime.stage1(candidate, context)
                if stage1.get("status") != "PASS":
                    blocked_count += 1
                    candidate_id = str(candidate["candidate_id"])
                    self.strategy_registry.transition(candidate_id, "VALIDATION_BLOCKED", evidence_ref="STAGE1_BLOCKED")
                else:
                    stage1_eligible.append(candidate)
            eligible = stage1_eligible
            if not eligible:
                machine.transition(ResearchBatchState.BLOCKED, "Stage1 blocked all sample-feasible candidates; performance gate remains closed")
                stage1_failure_records = [
                    {
                        "trial_id": f"{plan.batch_id}:STAGE1:{candidate['candidate_id']}",
                        "candidate_id": str(candidate["candidate_id"]),
                        "family_id": str(candidate.get("family_id", candidate.get("mechanism", "UNKNOWN"))),
                        "status": "BLOCKED",
                        "classification": "BLOCKED",
                        "failure_category": "PIT_FAILURE",
                        "performance_accessed": False,
                        "reason_codes": ("STAGE1_BLOCKED",),
                    }
                    for candidate in sample_eligible
                ]
                failure_snapshot = self.failure_adapter.snapshot_from_trials([*self._sample_blocked_records, *stage1_failure_records], snapshot_id=f"{plan.batch_id}_FAILURE_SNAPSHOT", parent_snapshot_id=plan.failure_knowledge_snapshot_id)
                checkpoint_path = self._checkpoint(machine, plan, hypotheses, compiled_candidates, failure_snapshot, trial_records=(), stop_reason="STAGE1_BLOCKED")
                return self._result(plan, machine, hypotheses, compiled_candidates, [], failure_snapshot, checkpoint_path, stop_reason="STAGE1_BLOCKED", blocked_count=blocked_count)
            machine.transition(ResearchBatchState.PERFORMANCE_VALIDATING, "sample feasibility and Stage1 passed; mock validation is now permitted")
        if resume_payload is None:
            trial_records = []
        engine_blocked = False
        synthetic_p_values: dict[str, float] = {}
        for index, candidate in enumerate(eligible):
            candidate_id = str(candidate["candidate_id"])
            family_id = str(candidate["family_id"])
            trial_id = f"{plan.batch_id}_T{index + 1:03d}"
            existing_record = next((item for item in trial_records if str(item.get("trial_id")) == trial_id), None)
            if existing_record is not None and existing_record.get("performance_complete"):
                synthetic_p_values[candidate_id] = 0.01 if str(existing_record.get("local_classification")) in {"RESEARCH_PASSED", "PROMISING"} else 1.0
                reservation_id = str(existing_record.get("budget_reservation_identity") or "")
                if reservation_id:
                    self.budget.consume(reservation_id)
                self.artifact_graph.add_node(f"trial:{trial_id}", "Trial", existing_record)
                self.artifact_graph.add_edge(f"trial:{trial_id}", "VALIDATED_BY", f"candidate:{candidate_id}")
                self.artifact_graph.add_node(f"validation:{trial_id}", "ValidationResult", {"trial_id": trial_id, "local_classification": existing_record.get("local_classification"), "final_adjudication_pending": True, "reason_codes": list(existing_record.get("reason_codes", ()))})
                self.artifact_graph.add_edge(f"validation:{trial_id}", "CLASSIFIED_AS", f"candidate:{candidate_id}")
                continue
            try:
                reservation_id = str(existing_record.get("budget_reservation_identity")) if existing_record is not None else self.budget.reserve_trial(batch_id=plan.batch_id, family_id=family_id, candidate_id=candidate_id)
            except BudgetExhaustedError:
                machine.transition(ResearchBatchState.BUDGET_EXHAUSTED, "trial reservation denied; budget cannot expand")
                break
            if existing_record is None:
                self.trial_ledger.register_before_performance(
                    trial_id=trial_id,
                    objective_id=self.objective.objective_id,
                    batch_id=plan.batch_id,
                    family_id=family_id,
                    hypothesis_id=str(candidate["hypothesis_id"]),
                    candidate_id=candidate_id,
                    candidate_hash=str(candidate["candidate_hash"]),
                    dataset_hash=stable_hash("SYNTHETIC_DATASET_ONLY"),
                    validation_policy_hash=policy.policy_hash,
                    engine_hash=RESEARCH_FACTORY_CANONICAL_DEPENDENCIES_V1["dependencies"]["backtest_engine"]["identity_hash"],
                    seed=plan.generation_seed + index,
                    budget_reservation_identity=reservation_id,
                    lineage={"source": "SYNTHETIC_MOCK", "real_performance": False, "budget_reservation_identity": reservation_id},
                )
                existing_record = self.trial_ledger.latest()[trial_id].to_dict()
                existing_record["budget_reservation_identity"] = reservation_id
                trial_records.append(existing_record)
                self._checkpoint(machine, plan, hypotheses, compiled_candidates, trial_records=trial_records)
                maybe_crash("after_budget_reservation")
            self.trial_ledger.mark_performance_accessed(trial_id)
            validation = self.runtime.validate(candidate, context, index)
            classification = str(validation.get("classification"))
            reasons = tuple(str(item) for item in validation.get("reason_codes", ()))
            if classification == "ENGINE_ERROR":
                classification = "ENGINEERING_BLOCKED"
                reasons = reasons + ("ENGINE_FAILURE",)
                engine_blocked = True
            synthetic_p_values[candidate_id] = float(validation.get("p_value", 0.01 if classification in {"RESEARCH_PASSED", "PROMISING"} else 1.0))
            self.trial_ledger.mark_provisional(
                trial_id,
                local_classification=classification,
                evidence_ref=f"SYNTHETIC_PROVISIONAL:{trial_id}",
                result={"local_classification": classification, "performance_completed": True},
            )
            record = next(item for item in trial_records if str(item.get("trial_id")) == trial_id)
            record.update(self.trial_ledger.latest()[trial_id].to_dict())
            record["local_classification"] = classification
            record["performance_complete"] = True
            record["final_adjudication_pending"] = True
            record["budget_reservation_identity"] = reservation_id
            self._checkpoint(machine, plan, hypotheses, compiled_candidates, trial_records=trial_records)
            maybe_crash("after_performance_completion_marker")
            self.budget.consume(reservation_id)
            self.artifact_graph.add_node(f"trial:{trial_id}", "Trial", record)
            self.artifact_graph.add_edge(f"trial:{trial_id}", "VALIDATED_BY", f"candidate:{candidate_id}")
            self.artifact_graph.add_node(f"validation:{trial_id}", "ValidationResult", {"trial_id": trial_id, "local_classification": classification, "final_adjudication_pending": True, "reason_codes": list(reasons)})
            self.artifact_graph.add_edge(f"validation:{trial_id}", "CLASSIFIED_AS", f"candidate:{candidate_id}")
        synthetic_multiple_testing = benjamini_hochberg(synthetic_p_values, q=policy.fdr_q)
        synthetic_multiple_testing.update({
            "decision_family_id": plan.decision_family_id,
            "decision_denominator": int(synthetic_multiple_testing["hypothesis_count"]),
            "family_contract_hash": policy.multiple_testing_contract_hash,
        })
        multiple_testing_commit = {
            "decision_family_id": plan.decision_family_id,
            "q": policy.fdr_q,
            "p_values": dict(sorted(synthetic_p_values.items())),
            "family_contract_hash": policy.multiple_testing_contract_hash,
        }
        self._checkpoint(machine, plan, hypotheses, compiled_candidates, trial_records=trial_records)
        maybe_crash("before_bh")
        self.commit_ledger.ensure("multiple_testing_committed", plan.decision_family_id, {"decision_family_id": plan.decision_family_id, "result_hash": stable_hash(multiple_testing_commit)})
        maybe_crash("after_bh")
        synthetic_adjudicator = FinalResearchAdjudicatorV1(policy)
        for record in trial_records:
            decision = synthetic_adjudicator.adjudicate(
                candidate_id=str(record["candidate_id"]),
                candidate_hash=str(record["candidate_hash"]),
                trial_id=str(record["trial_id"]),
                local_classification=str(record["local_classification"]),
                multiple_testing=synthetic_multiple_testing,
                decision_family_id=plan.decision_family_id,
                decision_denominator=int(synthetic_multiple_testing["hypothesis_count"]),
                history_snapshot_hash=stable_hash(synthetic_multiple_testing),
                metrics_ref="SYNTHETIC_ONLY",
                evidence={
                    "engine_integrity": "PASS",
                    "gates": {
                        gate_id: ({"passed": False} if record["local_classification"] == "ENGINEERING_BLOCKED" and gate_id == "engine_integrity" else {"passed": False} if record["local_classification"] == "REJECTED" and gate_id == "local_base_return" else {"passed": True})
                        for gate_id in policy.hard_gates
                        if gate_id != "multiple_testing_adjusted_support"
                    },
                    "gate_roles": {gate_id: item["role"] for gate_id, item in policy.gates.items()},
                    "small_capital_evidence_status": "SYNTHETIC_EXECUTION_VALID",
                    "small_capital_contract_valid": True,
                },
            )
            record["classification"] = decision.effective_classification
            record["status"] = "COMPLETED"
            record["final_decision_id"] = decision.decision_id
            record["policy_id"] = plan.validation_policy_id
            record["policy_version"] = plan.validation_policy_version
            record["run_id"] = plan.run_id
            self.trial_ledger.mark_final_adjudication(str(record["trial_id"]), decision.effective_classification, decision_id=decision.decision_id, reason_codes=tuple(record.get("reason_codes", ())) + (decision.reason,))
            self.commit_ledger.ensure("final_decision_committed", str(decision.decision_id), {"decision_id": decision.decision_id, "trial_id": record["trial_id"], "decision_hash": stable_hash(decision.to_dict())})
            maybe_crash("after_final_adjudication")
            self.commit_ledger.ensure("trial_final_state_committed", str(record["trial_id"]), {"trial_id": record["trial_id"], "decision_id": decision.decision_id, "classification": decision.effective_classification})
            maybe_crash("after_trial_final_commit")
            if decision.effective_classification in {"REJECTED", "WEAK", "PROMISING", "RESEARCH_PASSED"}:
                self.strategy_registry.apply_classification(str(record["candidate_id"]), decision.effective_classification, evidence_ref=decision.decision_id)
            else:
                self.strategy_registry.transition(str(record["candidate_id"]), "VALIDATION_BLOCKED", evidence_ref=decision.decision_id)
            self.commit_ledger.ensure("registry_transition_committed", str(record["candidate_id"]), {"candidate_id": record["candidate_id"], "decision_id": decision.decision_id, "classification": decision.effective_classification})
            self.trial_ledger.mark_registry_committed(str(record["trial_id"]))
            maybe_crash("after_registry_commit")
        if engine_blocked:
            machine.transition(ResearchBatchState.ENGINEERING_BLOCKED, "validator returned ENGINE_ERROR; stop without alpha rejection")
        elif machine.state != ResearchBatchState.BUDGET_EXHAUSTED:
            machine.transition(ResearchBatchState.CLASSIFYING, "validator classification is authoritative")
            machine.transition(ResearchBatchState.FAILURE_EXTRACTING, "extract high-level failure knowledge")
            machine.transition(ResearchBatchState.COMPLETED, "batch artifacts and ledgers committed")
        failure_snapshot = self.failure_adapter.snapshot_from_trials([*self._sample_blocked_records, *trial_records], snapshot_id=f"{plan.batch_id}_FAILURE_SNAPSHOT", parent_snapshot_id=plan.failure_knowledge_snapshot_id)
        self.commit_ledger.ensure("failure_knowledge_committed", failure_snapshot.snapshot_id, {"snapshot_id": failure_snapshot.snapshot_id, "snapshot_hash": stable_hash({"snapshot_id": failure_snapshot.snapshot_id, "parent_snapshot_id": failure_snapshot.parent_snapshot_id, "entries": [item.to_dict() for item in failure_snapshot.entries]})})
        maybe_crash("after_failure_knowledge_commit")
        self.artifact_graph.add_node(f"failure:{failure_snapshot.snapshot_id}", "FailureKnowledge", failure_snapshot.to_dict())
        for record in trial_records:
            if record.get("classification") in {"REJECTED", "WEAK", "ENGINEERING_BLOCKED"}:
                self.artifact_graph.add_edge(f"trial:{record['trial_id']}", "FAILED_BECAUSE", f"failure:{failure_snapshot.snapshot_id}")
            candidate_id = str(record["candidate_id"])
            self.artifact_graph.add_node(f"strategy:{candidate_id}", "Strategy", self.strategy_registry._records[candidate_id].to_dict())
            self.artifact_graph.add_edge(f"strategy:{candidate_id}", "PROMOTED_TO", f"candidate:{candidate_id}")
        self.commit_ledger.ensure("artifact_graph_committed", plan.batch_id, {"batch_id": plan.batch_id, "graph_hash": self.artifact_graph.graph_hash})
        passed = sum(record.get("classification") == "RESEARCH_PASSED" for record in trial_records)
        target = self.objective.stop_on_research_passed_count
        next_plan = None
        stop_reason = None
        if machine.state == ResearchBatchState.ENGINEERING_BLOCKED:
            stop_reason = "ENGINEERING_BLOCKED"
        elif machine.state == ResearchBatchState.BUDGET_EXHAUSTED:
            stop_reason = "BUDGET_EXHAUSTED"
        elif target is not None and passed >= target:
            stop_reason = "OBJECTIVE_SATISFIED"
        elif batch_number < self.objective.max_batches:
            next_plan = self.planner.create_plan(self.objective, batch_number=batch_number + 1, parent_batch_id=plan.batch_id, failure_knowledge_snapshot_id=failure_snapshot.snapshot_id)
            stop_reason = "CONTINUE_NEXT_BATCH"
        else:
            stop_reason = "MAX_BATCHES_REACHED"
        for record in trial_records:
            record["run_id"] = plan.run_id or ""
            self.history = self.history_store.record_trial(record)
            self.commit_ledger.ensure("history_update_committed", str(record["trial_id"]), {"trial_id": record["trial_id"], "final_decision_id": record.get("final_decision_id")})
        maybe_crash("after_history_update")
        checkpoint_path = self._checkpoint(machine, plan, hypotheses, compiled_candidates, failure_snapshot, trial_records=trial_records, stop_reason=stop_reason)
        self.commit_ledger.ensure("batch_terminal_committed", plan.batch_id, {"batch_id": plan.batch_id, "state": machine.state.value, "checkpoint": str(checkpoint_path)})
        maybe_crash("after_batch_terminal")
        return self._result(plan, machine, hypotheses, compiled_candidates, trial_records, failure_snapshot, checkpoint_path, next_plan=next_plan, stop_reason=stop_reason, blocked_count=blocked_count)

    def _resume_synthetic_pending_checkpoint(self, payload: Mapping[str, Any], plan: ResearchBatchPlanV1) -> FactoryRunResultV1:
        """Replay the post-performance synthetic pipeline without validation access."""
        from chanlun_trader.research.strategy_validation import FinalResearchAdjudicatorV1, benjamini_hochberg

        hypotheses = [dict(item) for item in payload.get("hypotheses", ())]
        candidates = [dict(item) for item in payload.get("candidates", ())]
        trial_records = [dict(item) for item in payload.get("trial_records", ())]
        policy = self.validation_policy
        p_values = {str(item.get("candidate_id")): (0.01 if str(item.get("local_classification")) in {"RESEARCH_PASSED", "PROMISING"} else 1.0) for item in trial_records}
        multiple_testing = benjamini_hochberg(p_values, q=policy.fdr_q)
        multiple_testing.update({"decision_family_id": plan.decision_family_id, "decision_denominator": int(multiple_testing["hypothesis_count"]), "family_contract_hash": policy.multiple_testing_contract_hash})
        multiple_testing_commit = {
            "decision_family_id": plan.decision_family_id,
            "q": policy.fdr_q,
            "p_values": dict(sorted(p_values.items())),
            "family_contract_hash": policy.multiple_testing_contract_hash,
        }
        self.commit_ledger.ensure("multiple_testing_committed", plan.decision_family_id, {"decision_family_id": plan.decision_family_id, "result_hash": stable_hash(multiple_testing_commit)})
        adjudicator = FinalResearchAdjudicatorV1(policy)
        for record in trial_records:
            trial_id = str(record["trial_id"])
            candidate_id = str(record["candidate_id"])
            current = self.trial_ledger.latest().get(trial_id)
            if current is not None and current.status == ResearchFactoryTrialLedgerFacadeV1.PERFORMANCE_COMPLETE_PENDING_ADJUDICATION:
                decision = adjudicator.adjudicate(candidate_id=candidate_id, candidate_hash=str(record["candidate_hash"]), trial_id=trial_id, local_classification=str(record.get("local_classification")), multiple_testing=multiple_testing, decision_family_id=plan.decision_family_id, decision_denominator=int(multiple_testing["hypothesis_count"]), history_snapshot_hash=stable_hash(multiple_testing), metrics_ref="SYNTHETIC_ONLY", evidence={"engine_integrity": "PASS", "gates": {gate_id: {"passed": not (record.get("local_classification") == "REJECTED" and gate_id == "local_base_return")} for gate_id in policy.hard_gates if gate_id != "multiple_testing_adjusted_support"}, "gate_roles": {gate_id: item["role"] for gate_id, item in policy.gates.items()}, "small_capital_evidence_status": "SYNTHETIC_EXECUTION_VALID", "small_capital_contract_valid": True})
                self.trial_ledger.mark_final_adjudication(trial_id, decision.effective_classification, decision_id=decision.decision_id, reason_codes=tuple(record.get("reason_codes", ())) + (decision.reason,))
                record.update({"classification": decision.effective_classification, "status": "COMPLETED", "final_decision_id": decision.decision_id, "final_adjudication_pending": False, "run_id": plan.run_id})
                self.commit_ledger.ensure("final_decision_committed", decision.decision_id, {"decision_id": decision.decision_id, "trial_id": trial_id, "decision_hash": stable_hash(decision.to_dict())})
                self.commit_ledger.ensure("trial_final_state_committed", trial_id, {"trial_id": trial_id, "decision_id": decision.decision_id, "classification": decision.effective_classification})
                if decision.effective_classification in {"REJECTED", "WEAK", "PROMISING", "RESEARCH_PASSED"}:
                    self.strategy_registry.apply_classification(candidate_id, decision.effective_classification, evidence_ref=decision.decision_id)
                else:
                    self.strategy_registry.transition(candidate_id, "VALIDATION_BLOCKED", evidence_ref=decision.decision_id)
                self.commit_ledger.ensure("registry_transition_committed", candidate_id, {"candidate_id": candidate_id, "decision_id": decision.decision_id, "classification": decision.effective_classification})
                self.trial_ledger.mark_registry_committed(trial_id)
            elif current is not None and current.status in ResearchFactoryTrialLedgerFacadeV1.TERMINAL:
                decision_id = str(current.lineage.get("final_decision_id") or record.get("final_decision_id") or "")
                record.update({"classification": current.classification, "status": current.status, "final_decision_id": decision_id or None, "final_adjudication_pending": False, "run_id": plan.run_id})
                if decision_id:
                    if current.classification in {"REJECTED", "WEAK", "PROMISING", "RESEARCH_PASSED"}:
                        self.strategy_registry.apply_classification(candidate_id, str(current.classification), evidence_ref=decision_id)
                    else:
                        self.strategy_registry.transition(candidate_id, "VALIDATION_BLOCKED", evidence_ref=decision_id)
                    self.commit_ledger.ensure("registry_transition_committed", candidate_id, {"candidate_id": candidate_id, "decision_id": decision_id, "classification": current.classification})
                    self.trial_ledger.mark_registry_committed(trial_id)
        failure_snapshot = self.failure_adapter.snapshot_from_trials(trial_records, snapshot_id=f"{plan.batch_id}_FAILURE_SNAPSHOT", parent_snapshot_id=plan.failure_knowledge_snapshot_id)
        self.commit_ledger.ensure("failure_knowledge_committed", failure_snapshot.snapshot_id, {"snapshot_id": failure_snapshot.snapshot_id, "snapshot_hash": stable_hash({"snapshot_id": failure_snapshot.snapshot_id, "parent_snapshot_id": failure_snapshot.parent_snapshot_id, "entries": [item.to_dict() for item in failure_snapshot.entries]})})
        for record in trial_records:
            self.history = self.history_store.record_trial(record)
            self.commit_ledger.ensure("history_update_committed", str(record["trial_id"]), {"trial_id": record["trial_id"], "final_decision_id": record.get("final_decision_id")})
        machine = ResearchBatchStateMachineV1(plan.batch_id, initial_state=ResearchBatchState.PERFORMANCE_VALIDATING, path=self.output_dir / "state" / f"{plan.batch_id}_resume.json")
        machine.transition(ResearchBatchState.CLASSIFYING, "synthetic final decision replay")
        machine.transition(ResearchBatchState.FAILURE_EXTRACTING, "synthetic failure knowledge replay")
        machine.transition(ResearchBatchState.COMPLETED, "synthetic commit replay complete")
        checkpoint_path = self._checkpoint(machine, plan, hypotheses, candidates, failure_snapshot, trial_records=trial_records, stop_reason="RESUMED_FINAL_ADJUDICATION")
        self.commit_ledger.ensure("batch_terminal_committed", plan.batch_id, {"batch_id": plan.batch_id, "state": machine.state.value, "checkpoint": str(checkpoint_path)})
        return self._result(plan, machine, hypotheses, candidates, trial_records, failure_snapshot, checkpoint_path, stop_reason="RESUMED_FINAL_ADJUDICATION")

    def _result(self, plan: ResearchBatchPlanV1, machine: ResearchBatchStateMachineV1, hypotheses: list[Mapping[str, Any]], candidates: list[Mapping[str, Any]], trials: list[Mapping[str, Any]], failure_snapshot: FailureKnowledgeSnapshotV1, checkpoint_path: Path, *, next_plan: ResearchBatchPlanV1 | None = None, stop_reason: str | None = None, blocked_count: int = 0) -> FactoryRunResultV1:
        counts = {str(record.get("classification")): sum(1 for item in trials if item.get("classification") == record.get("classification")) for record in trials}
        sample_counts = Counter(str(item.get("status")) for item in self._sample_feasibility_rows)
        status = ResearchFactoryStatusV1(
            objective_id=self.objective.objective_id,
            current_batch_id=plan.batch_id,
            batch_state=machine.state.value,
            batch_number=int(plan.batch_id.rsplit("B", 1)[-1]),
            max_batches=self.objective.max_batches,
            trial_budget_total=self.objective.max_total_trials,
            trial_budget_used=self.budget.used("objective", self.objective.objective_id),
            hypotheses_count=len(hypotheses),
            candidate_count=len(candidates),
            trials_started=len(trials),
            trials_completed=sum(1 for item in trials if item.get("status") == "COMPLETED"),
            research_passed_count=counts.get("RESEARCH_PASSED", 0),
            promising_count=counts.get("PROMISING", 0),
            weak_count=counts.get("WEAK", 0),
            rejected_count=counts.get("REJECTED", 0),
            blocked_count=blocked_count + counts.get("ENGINEERING_BLOCKED", 0),
            candidates_sample_feasibility_passed=sample_counts.get(PASS, 0),
            candidates_sample_feasibility_blocked=sample_counts.get(BLOCKED_INSUFFICIENT_FEASIBILITY, 0),
            candidates_sample_feasibility_unknown=sample_counts.get(UNKNOWN, 0),
            failure_class_counts={item.category: sum(1 for entry in failure_snapshot.entries if entry.category == item.category) for item in failure_snapshot.entries},
            strategy_registry_counts=self.strategy_registry.counts(),
            stop_reason=stop_reason,
        )
        return FactoryRunResultV1(self.objective, plan, machine.state.value, tuple(hypotheses), tuple(candidates), tuple(trials), failure_snapshot, status, str(checkpoint_path), next_plan)

    def resume_from_checkpoint(self, path: str | Path) -> dict[str, Any]:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("validation_policy_id") == self.validation_policy.policy_id:
            if payload.get("validation_policy_hash") != self.validation_policy.policy_hash or payload.get("family_contract_hash") != self.validation_policy.multiple_testing_contract_hash:
                raise ValidationPolicyV2Error("resume policy or multiple-testing family hash mismatch")
        candidates = payload.get("candidates", [])
        expected_hash = stable_hash(candidates)
        if expected_hash != payload.get("candidate_set_hash"):
            raise ValueError("checkpoint candidate hash mismatch")
        if payload.get("state") in {
            ResearchBatchState.COMPLETED.value,
            ResearchBatchState.ENGINEERING_BLOCKED.value,
            ResearchBatchState.BUDGET_EXHAUSTED.value,
        }:
            completed_trial_ids = tuple(str(item) for item in payload.get("completed_trial_ids", ()))
            return {
                "resumed": True,
                "resumed_from_state": payload["state"],
                "next_stage": payload["state"],
                "candidate_set_hash": expected_hash,
                "generator_calls": 0,
                "candidate_builder_calls": 0,
                "candidate_count": len(candidates),
                "completed_trial_ids": list(completed_trial_ids),
                "completed_trial_count": len(completed_trial_ids),
                "real_performance_trial_executed": bool(completed_trial_ids),
                "performance_rerun": False,
            }
        if payload.get("state") == ResearchBatchState.PERFORMANCE_VALIDATING.value and payload.get("final_adjudication_pending"):
            pending_trial_ids = tuple(str(item) for item in payload.get("pending_trial_ids", ()))
            return {
                "resumed": True,
                "resumed_from_state": payload["state"],
                "next_stage": "MULTIPLE_TESTING_AND_FINAL_ADJUDICATION",
                "candidate_set_hash": expected_hash,
                "generator_calls": 0,
                "candidate_builder_calls": 0,
                "candidate_count": len(candidates),
                "pending_trial_ids": list(pending_trial_ids),
                "pending_trial_count": len(pending_trial_ids),
                "real_performance_trial_executed": True,
                "performance_rerun": False,
                "multiple_testing": "RUN_ON_RESUME",
                "final_adjudication": "RUN_ON_RESUME",
                "strategy_registry_commit": "ONCE_AFTER_FINAL_ADJUDICATION",
            }
        if payload.get("state") != ResearchBatchState.CANDIDATES_FROZEN.value:
            raise ValueError("resume golden path requires CANDIDATES_FROZEN or terminal checkpoint")
        return {
            "resumed": True,
            "resumed_from_state": payload["state"],
            "next_stage": ResearchBatchState.STAGE1_VALIDATING.value,
            "candidate_set_hash": expected_hash,
            "generator_calls": 0,
            "candidate_builder_calls": 0,
            "candidate_count": len(candidates),
            "real_performance_trial_executed": False,
            "performance_rerun": False,
        }

    def run(self, **kwargs: Any) -> FactoryRunResultV1:
        if kwargs.pop("synthetic", True) is False:
            return self.run_real(**kwargs)
        return self.run_synthetic(**kwargs)

    def run_real(self, *, batch_number: int = 1, plan: ResearchBatchPlanV1 | None = None) -> FactoryRunResultV1:
        from .real_runtime import RealFactoryRuntimeV1
        active_plan = plan or self.planner.create_plan(self.objective, batch_number=batch_number)
        active_plan = replace(active_plan, agent_governance_policy_id=self.agent_governance_policy.policy_id, agent_governance_version=self.agent_governance_policy.version, agent_governance_hash=self.agent_governance_policy.policy_hash)
        return RealFactoryRuntimeV1().run(self, plan=active_plan)

    def resume(self, path: str | Path) -> dict[str, Any]:
        return self.resume_from_checkpoint(path)

    def migrate_current_corrected_v3(self, report_path: str | Path) -> dict[str, Any]:
        payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
        blocked_ids = tuple(str(item) for item in payload.get("semantic_blocked_candidate_ids", ()))
        return self.strategy_registry.migrate_corrected_v3(payload, semantic_blocked_ids=blocked_ids)
