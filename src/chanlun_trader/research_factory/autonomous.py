"""AutonomousResearchModeV1 loop contract."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from collections import Counter
from pathlib import Path
import json

from .agent_backend import AgentBackendError, AgentCallAuditLedgerV1, AgentCallBudgetV1, AgentGovernancePolicyV1, GovernedResearchAgentBackendV1, ResearchAgentBackendV1, ResearchAgentInputBuilderV1, TemplateResearchAgentBackendV1
from .context import NoOutcomeResearchContextV1
from .diversity import FamilyDiversityPolicyV1
from .run_budget import AutonomousRunBudgetV1
from .run_state import AutonomousResearchRunV2, AutonomousRunState, AutonomousRunStoreV1
from .status import AutonomousResearchRunStatusV2
from .common import now_timestamp, stable_hash
from .durability import DurableTrialLedgerViewV2
from .failure_adapter import FailureKnowledgeSnapshotV1, FailureKnowledgeViewV1
from .history import CumulativeResearchHistoryV1, CumulativeResearchHistoryStoreV1


AUTONOMOUS_RESEARCH_ENABLED = False


@dataclass(frozen=True)
class AutonomousResearchModeV1:
    max_batches: int
    max_total_trials: int
    stop_on_research_passed_count: int | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.max_batches < 1 or self.max_total_trials < 1:
            raise ValueError("autonomous budgets must be positive")

    def run(self, run_batch: Callable[[int, Any], Any], *, initial_failure_snapshot: Any = None) -> list[Any]:
        """Run only the supplied factory batch callback; governance stays outside the loop."""
        if not self.enabled:
            return []
        results: list[Any] = []
        failure_snapshot = initial_failure_snapshot
        used_trials = 0
        for batch_number in range(1, self.max_batches + 1):
            result = run_batch(batch_number, failure_snapshot)
            results.append(result)
            used_trials += int(getattr(result, "trials_completed", 0) or 0)
            failure_snapshot = getattr(result, "failure_snapshot", failure_snapshot)
            if used_trials >= self.max_total_trials:
                break
            status = getattr(result, "state", getattr(result, "status", None))
            passed = int(getattr(getattr(result, "status", None), "research_passed_count", getattr(result, "research_passed_count", 0)) or 0)
            if status in {"ENGINEERING_BLOCKED", "BLOCKED", "BUDGET_EXHAUSTED"}:
                break
            if self.stop_on_research_passed_count is not None and passed >= self.stop_on_research_passed_count:
                break
        return results


@dataclass(frozen=True)
class AutonomousRunExecutionV2:
    run: AutonomousResearchRunV2
    results: tuple[Any, ...]
    status: AutonomousResearchRunStatusV2

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "autonomous-run-execution-v2",
            "run": self.run.to_dict(),
            "results": [item.to_dict() if hasattr(item, "to_dict") else dict(item) for item in self.results],
            "status": self.status.to_dict(),
        }


class AutonomousResearchRunnerV2:
    """Synthetic-only multi-batch control-plane runner.

    This class deliberately has no real-performance entry point.  It proves the
    durable run protocol with the existing Factory synthetic validator while the
    autonomous feature gate remains disabled by default.
    """

    def __init__(self, objective: Any, *, root: str | Path = ".", run_id: str, policy_hash: str, policy_id: str = "VALIDATION_DECISION_POLICY_V2", policy_version: str = "2.0.0", enabled: bool = False, runtime: Any | None = None, backend: ResearchAgentBackendV1 | None = None, family_diversity_policy: FamilyDiversityPolicyV1 | None = None, agent_governance_policy: AgentGovernancePolicyV1 | None = None, crash_at: str | None = None):
        self.objective = objective
        self.root = Path(root)
        self.run_id = str(run_id)
        self.enabled = bool(enabled)
        self.runtime = runtime
        self.backend = backend or TemplateResearchAgentBackendV1()
        self.family_diversity_policy = family_diversity_policy or FamilyDiversityPolicyV1(2, 2, 2, 0, {"source": "RUN_CONTRACT_REQUIRED_DEFAULT_FOR_SYNTHETIC"})
        self.agent_governance_policy = agent_governance_policy or AgentGovernancePolicyV1(max_agent_calls=max(3, int(objective.max_batches)))
        self.crash_at = crash_at
        self.store = AutonomousRunStoreV1(self.root, self.run_id)
        self.run = self._load_or_create(policy_id, policy_version, policy_hash)
        self.run_budget = AutonomousRunBudgetV1(
            run_id=self.run_id,
            objective_id=objective.objective_id,
            path=self.store.run_dir / "run_budget.json",
            max_batches=self.run.max_batches,
            max_total_predictive_trials=self.run.max_total_predictive_trials,
            max_trials_per_batch=self.run.max_trials_per_batch,
            max_hypotheses_per_batch=self.run.max_hypotheses_per_batch,
            max_candidates_per_batch=self.run.max_candidates_per_batch,
            policy_hash=self.run.policy_hash,
            objective_hash=self.run.objective_hash,
        )
        self.agent_budget = AgentCallBudgetV1(self.store.run_dir / "agent_call_budget.json", self.agent_governance_policy)
        self.agent_audit = AgentCallAuditLedgerV1(self.store.run_dir / "agent_call_audit.json")
        self.trial_view = DurableTrialLedgerViewV2.from_run_dir(self.store.run_dir, run_id=self.run_id)
        self.history_store = CumulativeResearchHistoryStoreV1(self.store.run_dir / "cumulative_history.json", CumulativeResearchHistoryV1(objective.objective_id, policy_id=policy_id, policy_version=policy_version, policy_hash=policy_hash, source_run_id=self.run_id))
        self._results: list[Any] = []
        self._failure_view: Any = self._load_failure_view()
        if self.trial_view.records:
            self.run_budget.reconcile_against(ledger_view=self.trial_view)

    def _load_or_create(self, policy_id: str, policy_version: str, policy_hash: str) -> AutonomousResearchRunV2:
        if self.store.checkpoint_path.exists():
            run = self.store.load()
            run.assert_resume_pins({"objective_id": self.objective.objective_id, "objective_hash": self.objective.objective_hash, "policy_id": policy_id, "policy_version": policy_version, "policy_hash": policy_hash, "max_batches": self.objective.max_batches, "max_total_predictive_trials": self.objective.max_total_trials, "backend_type": self.backend.backend_type, "backend_version": self.backend.backend_version, "family_diversity_policy_hash": self.family_diversity_policy.policy_hash, "agent_governance_policy_id": self.agent_governance_policy.policy_id, "agent_governance_version": self.agent_governance_policy.version, "agent_governance_hash": self.agent_governance_policy.policy_hash})
            return run
        run = AutonomousResearchRunV2.create(
            run_id=self.run_id,
            objective_id=self.objective.objective_id,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_hash=policy_hash,
            max_batches=self.objective.max_batches,
            max_total_predictive_trials=self.objective.max_total_trials,
            max_trials_per_batch=min(self.objective.max_total_trials, 20),
            max_hypotheses_per_batch=min(self.objective.max_total_trials, 20),
            max_candidates_per_batch=min(self.objective.max_total_trials, 20),
            objective_hash=self.objective.objective_hash,
            family_diversity_policy_hash=self.family_diversity_policy.policy_hash,
            backend_type=self.backend.backend_type,
            backend_version=self.backend.backend_version,
            agent_governance_policy_id=self.agent_governance_policy.policy_id,
            agent_governance_version=self.agent_governance_policy.version,
            agent_governance_hash=self.agent_governance_policy.policy_hash,
        )
        return self.store.initialize(run)

    def _load_failure_view(self) -> Any:
        persisted_path = self.store.run_dir / "failure_knowledge_view.json"
        if persisted_path.exists():
            return FailureKnowledgeViewV1.from_dict(json.loads(persisted_path.read_text(encoding="utf-8")))
        entries: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        source_batches: set[str] = set()
        checkpoint_dir = self.store.run_dir / "factory" / "checkpoints"
        if checkpoint_dir.exists():
            for checkpoint_path in sorted(checkpoint_dir.glob("*.json")):
                payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                snapshot = payload.get("failure_snapshot")
                if not snapshot:
                    continue
                batch_id = str(payload.get("batch_id") or checkpoint_path.stem)
                source_batches.add(batch_id)
                for entry in snapshot.get("entries", ()):
                    key = (str(entry.get("category", "")), str(entry.get("mechanism", "")), str(entry.get("reason_code", "")), json.dumps(entry.get("constraints", ()), sort_keys=True))
                    entries[key] = {"category": key[0], "mechanism": key[1], "high_level_reason": str(entry.get("high_level_reason", "")), "reason_code": key[2], "constraints": list(entry.get("constraints", ())) }
        view = FailureKnowledgeViewV1(failure_view_version="1.0.0", entries=tuple(entries.values()), source_batch_ids=tuple(sorted(source_batches)), source_history_hash=self.trial_view.view_hash)
        if source_batches:
            persisted_path.parent.mkdir(parents=True, exist_ok=True)
            persisted_path.write_text(json.dumps(view.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return view

    def _status(self, run: AutonomousResearchRunV2, results: list[Any]) -> AutonomousResearchRunStatusV2:
        if not results and self.store.status_path.exists():
            payload = self.store.load_status()
            if payload.get("schema_version") == "autonomous-research-run-status-v2":
                return AutonomousResearchRunStatusV2(
                    run_id=str(payload["run_id"]), objective_id=str(payload["objective_id"]), policy_hash=str(payload["policy_hash"]), run_state=str(payload["run_state"]), current_batch_id=payload.get("current_batch_id"), batches_completed=int(payload["batches_completed"]), max_batches=int(payload["max_batches"]), hypotheses_generated_cumulative=int(payload.get("hypotheses_generated_cumulative", 0)), candidates_frozen_cumulative=int(payload.get("candidates_frozen_cumulative", 0)), predictive_trials_used=int(payload.get("predictive_trials_used", 0)), max_predictive_trials=int(payload.get("max_predictive_trials", 0)), trials_pending_adjudication=int(payload.get("trials_pending_adjudication", 0)), research_passed_count=int(payload.get("RESEARCH_PASSED", 0)), promising_count=int(payload.get("PROMISING", 0)), weak_count=int(payload.get("WEAK", 0)), rejected_count=int(payload.get("REJECTED", 0)), blocked_count=int(payload.get("BLOCKED", 0)), failure_category_counts=dict(payload.get("failure_category_counts", {})), engineering_block=payload.get("engineering_block"), governance_block=payload.get("governance_block"), remaining_trial_budget=int(payload.get("remaining_trial_budget", 0)), stop_reason=payload.get("stop_reason"), last_checkpoint_time=payload.get("last_checkpoint_time"),
                )
        records = [record for result in results for record in getattr(result, "trial_records", ())]
        failure_counts = Counter(entry.category for result in results for entry in getattr(getattr(result, "failure_snapshot", None), "entries", ()))
        counts = Counter(str(record.get("classification")) for record in records)
        sample_counts = Counter()
        for result in results:
            result_status = getattr(result, "status", None)
            sample_counts["PASS"] += int(getattr(result_status, "candidates_sample_feasibility_passed", 0) or 0)
            sample_counts["BLOCKED_INSUFFICIENT_FEASIBILITY"] += int(getattr(result_status, "candidates_sample_feasibility_blocked", 0) or 0)
            sample_counts["UNKNOWN"] += int(getattr(result_status, "candidates_sample_feasibility_unknown", 0) or 0)
        pending = sum(1 for record in records if record.get("final_adjudication_pending"))
        status = AutonomousResearchRunStatusV2(
            run_id=run.run_id,
            objective_id=run.objective_id,
            policy_hash=run.policy_hash,
            run_state=run.state,
            current_batch_id=run.current_batch_id,
            batches_completed=run.batches_completed,
            max_batches=run.max_batches,
            hypotheses_generated_cumulative=sum(len(getattr(result, "hypotheses", ())) for result in results),
            candidates_frozen_cumulative=sum(len(getattr(result, "candidates", ())) for result in results),
            predictive_trials_used=self.run_budget.used_predictive_trials,
            max_predictive_trials=self.run_budget.max_total_predictive_trials,
            trials_pending_adjudication=pending,
            research_passed_count=counts.get("RESEARCH_PASSED", 0),
            promising_count=counts.get("PROMISING", 0),
            weak_count=counts.get("WEAK", 0),
            rejected_count=counts.get("REJECTED", 0),
            blocked_count=counts.get("ENGINEERING_BLOCKED", 0) + counts.get("BLOCKED", 0),
            candidates_sample_feasibility_passed=sample_counts["PASS"],
            candidates_sample_feasibility_blocked=sample_counts["BLOCKED_INSUFFICIENT_FEASIBILITY"],
            candidates_sample_feasibility_unknown=sample_counts["UNKNOWN"],
            failure_category_counts=dict(sorted(failure_counts.items())),
            engineering_block="ENGINEERING_BLOCKED" if run.state == AutonomousRunState.BLOCKED_ENGINEERING.value else None,
            governance_block="BLOCKED_GOVERNANCE" if run.state == AutonomousRunState.BLOCKED_GOVERNANCE.value else None,
            remaining_trial_budget=self.run_budget.remaining_predictive_trials,
            stop_reason=run.stop_reason,
            last_checkpoint_time=run.updated_at,
        )
        self.store.save(run, status=status.to_dict())
        return status

    def run_synthetic(self) -> AutonomousRunExecutionV2:
        if not self.enabled:
            raise RuntimeError("AUTONOMOUS_MODE_DISABLED")
        if self.run.state in {item.value for item in (AutonomousRunState.COMPLETED, AutonomousRunState.BLOCKED_ENGINEERING, AutonomousRunState.BLOCKED_GOVERNANCE, AutonomousRunState.STOPPED_BUDGET, AutonomousRunState.STOPPED_NO_CANDIDATES, AutonomousRunState.STOPPED_HUMAN)}:
            return AutonomousRunExecutionV2(self.run, tuple(self._results), self._status(self.run, self._results))
        from .batch import FactoryResearchPlannerAdapterV1
        from .orchestrator import AIResearchFactoryOrchestratorV1

        if self.run.state == AutonomousRunState.CREATED.value:
            self.run = self.run.transition(AutonomousRunState.READY, "run contract validated")
            self.store.append_event("RUN_READY", self.run.to_dict(), event_id=stable_event_id(self.run.run_id, "RUN_READY"))
            self.store.save(self.run)
        while self.run.batches_completed < self.run.max_batches:
            batch_number = self.run.batches_completed + 1
            planner = FactoryResearchPlannerAdapterV1()
            parent_failure_id = getattr(self._failure_view, "source_batch_ids", ())[-1] if self._failure_view and getattr(self._failure_view, "source_batch_ids", ()) else None
            plan = planner.create_plan(self.objective, batch_number=batch_number, run_id=self.run.run_id, failure_knowledge_snapshot_id=parent_failure_id, backend_type=self.backend.backend_type, backend_version=self.backend.backend_version, family_diversity_policy_hash=self.family_diversity_policy.policy_hash, agent_governance_policy_id=self.agent_governance_policy.policy_id, agent_governance_version=self.agent_governance_policy.version, agent_governance_hash=self.agent_governance_policy.policy_hash)
            durable_plan_path = self.store.run_dir / "factory" / "checkpoints" / f"{plan.batch_id}.json"
            plan_recovered = False
            if durable_plan_path.exists():
                durable_payload = json.loads(durable_plan_path.read_text(encoding="utf-8"))
                durable_plan = durable_payload.get("plan")
                if isinstance(durable_plan, dict):
                    from .batch import ResearchBatchPlanV1
                    plan = ResearchBatchPlanV1(**{key: value for key, value in durable_plan.items() if key in ResearchBatchPlanV1.__dataclass_fields__})
                    plan_recovered = True
            if plan.batch_id in self.run.completed_batch_ids:
                self.run = self.run.update(current_batch_id=plan.batch_id, batches_completed=batch_number, state=AutonomousRunState.BETWEEN_BATCHES.value)
                continue
            self.run_budget.start_batch(plan.batch_id)
            if self.run.state in {AutonomousRunState.READY.value, AutonomousRunState.BETWEEN_BATCHES.value}:
                self.run = self.run.transition(AutonomousRunState.BATCH_PLANNING, f"plan batch {batch_number}")
            self.run = self.run.update(current_batch_id=plan.batch_id, batches_started=max(self.run.batches_started, self.run_budget.batch_count_used), started_batch_ids=tuple(sorted(set(self.run.started_batch_ids) | {plan.batch_id})))
            no_outcome = NoOutcomeResearchContextV1(
                factor_capability_summary=(
                    {"factor_id": "SYNTHETIC_FACTOR_A", "pit_status": "PIT_VERIFIED", "data_status": "AVAILABLE"},
                    {"factor_id": "SYNTHETIC_FACTOR_B", "pit_status": "PIT_VERIFIED", "data_status": "AVAILABLE"},
                ),
                mechanism_history=tuple({"mechanism": item, "history_available": True} for item in self.objective.mechanism_scope),
                failure_class_summaries=tuple(getattr(self._failure_view, "entries", ())),
                constraints={"holding_horizon": list(self.objective.holding_horizon), "preferred_horizon": list(self.objective.preferred_horizon), "complexity_budget": dict(plan.complexity_budget)},
            )
            try:
                agent_input = ResearchAgentInputBuilderV1().build(
                    run_id=self.run.run_id,
                    batch_id=plan.batch_id,
                    objective=self.objective,
                    policy_identity={"policy_id": plan.validation_policy_id, "policy_version": plan.validation_policy_version, "policy_hash": plan.validation_policy_hash},
                    no_outcome_context=no_outcome,
                    failure_knowledge=self._failure_view,
                    candidate_neighborhood={},
                    family_mechanism_constraints={"family_quotas": dict(plan.family_quotas), "mechanism_quotas": dict(plan.mechanism_quotas)},
                    complexity_constraints=plan.complexity_budget,
                    factor_event_catalog=(
                        {"factor_id": "SYNTHETIC_FACTOR_A", "pit_status": "PIT_VERIFIED", "data_status": "AVAILABLE"},
                        {"factor_id": "SYNTHETIC_FACTOR_B", "pit_status": "PIT_VERIFIED", "data_status": "AVAILABLE"},
                    ),
                    proposal_budget_view={"max_proposals": plan.max_hypotheses, "max_candidates": plan.max_candidates},
                )
                proposal_dir = self.store.run_dir / "batches" / plan.batch_id
                proposal_dir.mkdir(parents=True, exist_ok=True)
                proposal_path = proposal_dir / "research_proposal_batch.json"
                agent_call_id = stable_hash({"run_id": self.run.run_id, "batch_id": plan.batch_id, "backend_type": self.backend.backend_type, "backend_version": self.backend.backend_version, "input_context_hash": agent_input.input_context_hash})
                batch_call_events = [event for event in self.store.load_events() if event.get("batch_id") == plan.batch_id and event.get("agent_call_id")]
                if plan_recovered and batch_call_events:
                    agent_call_id = str(batch_call_events[0]["agent_call_id"])
                completed_marker = self.store.events_path.exists() and any(event.get("event_type") == "AGENT_CALL_COMPLETED" and event.get("agent_call_id") == agent_call_id for event in self.store.load_events())
                completed_marker = completed_marker or (plan_recovered and proposal_path.exists())
                if completed_marker and proposal_path.exists():
                    from .agent_backend import ResearchProposalBatchV1
                    proposal_batch = ResearchProposalBatchV1.from_dict(json.loads(proposal_path.read_text(encoding="utf-8")))
                else:
                    self.store.append_event("AGENT_CALL_PLANNED", {"run_id": self.run.run_id, "batch_id": plan.batch_id, "agent_call_id": agent_call_id, "context_hash": agent_input.input_context_hash}, event_id=stable_event_id(self.run.run_id, "AGENT_CALL_PLANNED", agent_call_id))
                    if self.crash_at == "before_backend_proposal":
                        raise RuntimeError("SYNTHETIC_CRASH_INJECTED:before_backend_proposal")
                    governed = GovernedResearchAgentBackendV1(self.backend, policy=self.agent_governance_policy, budget=self.agent_budget, audit=self.agent_audit)
                    proposal_batch = governed.invoke(agent_input, run_id=self.run.run_id, batch_id=plan.batch_id, agent_call_id=agent_call_id)
                    proposal_path.write_text(json.dumps(proposal_batch.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                    self.store.append_event("AGENT_CALL_COMPLETED", {"run_id": self.run.run_id, "batch_id": plan.batch_id, "agent_call_id": agent_call_id, "proposal_batch_hash": proposal_batch.proposal_batch_hash}, event_id=stable_event_id(self.run.run_id, "AGENT_CALL_COMPLETED", agent_call_id))
                    if self.crash_at == "after_backend_proposal":
                        raise RuntimeError("SYNTHETIC_CRASH_INJECTED:after_backend_proposal")
            except AgentBackendError as exc:
                reason = str(exc)
                stop_reason = reason if reason.startswith("AGENT_") else "AGENT_BACKEND_ERROR"
                call_state = self.agent_budget.call(agent_call_id) if "agent_call_id" in locals() else None
                self.run = self.run.transition(AutonomousRunState.BLOCKED_ENGINEERING, stop_reason)
                self.store.append_event("AGENT_BACKEND_ERROR", {"run_id": self.run.run_id, "batch_id": plan.batch_id, "agent_call_id": agent_call_id if "agent_call_id" in locals() else None, "backend": self.backend.backend_type, "backend_version": self.backend.backend_version, "context_hash": agent_input.input_context_hash if "agent_input" in locals() else None, "error_class": type(exc).__name__, "reason": reason, "retryable": bool((call_state or {}).get("retryable", False)), "retry_count": int((call_state or {}).get("retry_count", 0)), "timestamp": now_timestamp(), "budget_impact": {"predictive_trials": 0, "agent_calls": 1 if call_state else 0}}, event_id=stable_event_id(self.run.run_id, "AGENT_BACKEND_ERROR", plan.batch_id))
                self.store.save(self.run)
                break
            if not plan_recovered:
                plan = replace(plan, input_context_hash=agent_input.input_context_hash, proposal_batch_hash=proposal_batch.proposal_batch_hash, prompt_template_version=proposal_batch.prompt_template_version)
            proposal_dir = self.store.run_dir / "batches" / plan.batch_id
            proposal_dir.mkdir(parents=True, exist_ok=True)
            if not plan_recovered or not (proposal_dir / "research_agent_input.json").exists():
                (proposal_dir / "research_agent_input.json").write_text(json.dumps(agent_input.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            if not (proposal_dir / "research_proposal_batch.json").exists():
                (proposal_dir / "research_proposal_batch.json").write_text(json.dumps(proposal_batch.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            self.store.append_event("BATCH_PLANNED", {"run_id": self.run.run_id, "batch_id": plan.batch_id, "batch_number": batch_number}, event_id=stable_event_id(self.run.run_id, "BATCH_PLANNED", plan.batch_id))
            if self.run.state != AutonomousRunState.BATCH_RUNNING.value:
                self.run = self.run.transition(AutonomousRunState.BATCH_RUNNING, f"synthetic control path starts {plan.batch_id}")
            self.store.save(self.run)
            factory = AIResearchFactoryOrchestratorV1(self.objective, root=self.root, output_dir=self.store.run_dir / "factory", runtime=self.runtime)
            factory.history = self.history_store.current
            result = factory.run_synthetic(plan=plan, crash_at=self.crash_at)
            self._results.append(result)
            previous_failure_batches = tuple(getattr(self._failure_view, "source_batch_ids", ()))
            result_failure_view = result.failure_snapshot.sanitized_view(source_batch_ids=(plan.batch_id,), source_history_hash=stable_hash(result.trial_records))
            self._failure_view = FailureKnowledgeViewV1(
                failure_view_version=result_failure_view.failure_view_version,
                entries=tuple(result_failure_view.entries) + tuple(getattr(self._failure_view, "entries", ())),
                source_batch_ids=tuple(sorted(set(previous_failure_batches) | {plan.batch_id})),
                source_history_hash=self.trial_view.view_hash,
                sanitization_policy_id=result_failure_view.sanitization_policy_id,
            )
            (self.store.run_dir / "failure_knowledge_view.json").write_text(json.dumps(self._failure_view.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            for record in result.trial_records:
                trial_id = str(record.get("trial_id"))
                if record.get("performance_accessed"):
                    self.run_budget.reserve_trial(trial_id=trial_id, batch_id=plan.batch_id, candidate_id=str(record.get("candidate_id")), candidate_hash=str(record.get("candidate_hash")), family_id=str(record.get("family_id")))
                    self.run_budget.complete_trial(trial_id)
            self.run_budget.complete_batch(plan.batch_id)
            self.trial_view = DurableTrialLedgerViewV2.from_run_dir(self.store.run_dir, run_id=self.run_id)
            self.trial_view.persist(self.store.run_dir / "durable_trial_ledger_view_v2.json")
            self.run_budget.reconcile_against(
                ledger_view=self.trial_view,
                batch_checkpoint={"batch_id": plan.batch_id, "completed_trial_ids": [str(record.get("trial_id")) for record in result.trial_records if record.get("status") == "COMPLETED"]},
                run_checkpoint={"total_trials_reserved": self.run_budget.reserved_predictive_trials, "total_trials_started": self.run_budget.used_predictive_trials + self.run_budget.reserved_predictive_trials, "total_trials_completed": self.run_budget.completed_predictive_trials},
            )
            for record in result.trial_records:
                self.history_store.record_trial({**dict(record), "run_id": self.run_id})
            self.run = self.run.transition(AutonomousRunState.BATCH_ADJUDICATING, "synthetic validator completed")
            self.run = self.run.transition(AutonomousRunState.BATCH_COMMITTING, "final decision and failure view committed")
            run_state = self.run
            if result.state == "ENGINEERING_BLOCKED":
                run_state = run_state.transition(AutonomousRunState.BLOCKED_ENGINEERING, "ENGINEERING_BLOCKED")
            elif result.state == "BLOCKED":
                run_state = run_state.transition(AutonomousRunState.BLOCKED_GOVERNANCE, "BLOCKED_GOVERNANCE")
            else:
                completed_ids = tuple(sorted(set(run_state.completed_batch_ids) | {plan.batch_id}))
                if batch_number >= run_state.max_batches:
                    run_state = run_state.transition(AutonomousRunState.COMPLETED, "MAX_BATCHES_REACHED")
                elif self.run_budget.remaining_predictive_trials <= 0:
                    run_state = run_state.transition(AutonomousRunState.STOPPED_BUDGET, "TOTAL_TRIAL_BUDGET_EXHAUSTED")
                else:
                    run_state = run_state.transition(AutonomousRunState.BETWEEN_BATCHES, "BETWEEN_BATCHES")
                run_state = run_state.update(batches_completed=batch_number, completed_batch_ids=completed_ids)
            self.run = run_state.update(total_trials_reserved=self.run_budget.reserved_predictive_trials, total_trials_started=self.run_budget.used_predictive_trials + self.run_budget.reserved_predictive_trials, total_trials_completed=self.run_budget.completed_predictive_trials, total_trials_final_adjudicated=self.run_budget.completed_predictive_trials)
            self.store.append_event("BATCH_COMMITTED", {"run_id": self.run.run_id, "batch_id": plan.batch_id, "state": self.run.state, "run_hash": self.run.run_hash}, event_id=stable_event_id(self.run.run_id, "BATCH_COMMITTED", plan.batch_id))
            self.store.save(self.run)
            if self.crash_at == "between_batches" and self.run.state == AutonomousRunState.BETWEEN_BATCHES.value:
                raise RuntimeError("SYNTHETIC_CRASH_INJECTED:between_batches")
            if self.run.state in {item.value for item in (AutonomousRunState.COMPLETED, AutonomousRunState.BLOCKED_ENGINEERING, AutonomousRunState.BLOCKED_GOVERNANCE, AutonomousRunState.STOPPED_BUDGET, AutonomousRunState.STOPPED_NO_CANDIDATES, AutonomousRunState.STOPPED_HUMAN)}:
                break
        return AutonomousRunExecutionV2(self.run, tuple(self._results), self._status(self.run, self._results))


def stable_event_id(run_id: str, event_type: str, key: str = "") -> str:
    from .common import stable_hash
    return stable_hash({"run_id": run_id, "event_type": event_type, "key": key})
