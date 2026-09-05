from __future__ import annotations

from collections import Counter
import json

import pytest

from chanlun_trader.research_factory import (
    AgentBackendError,
    AgentCallAuditLedgerV1,
    AgentCallBudgetV1,
    AgentGovernancePolicyV1,
    AutonomousResearchRunnerV2,
    NoOutcomeResearchContextV1,
    OutcomeBlindFieldPolicyV1,
    PerformanceLeakError,
    ResearchAgentInputBuilderV1,
    ResearchObjectiveV1,
    ResearchProposalBatchV1,
    SyntheticResearchAgentBackendV1,
)
from chanlun_trader.research_factory.agent_backend import AGENT_FORBIDDEN_FIELDS, GovernedResearchAgentBackendV1
from chanlun_trader.research_factory.durability import DurableTrialLedgerViewV2


POLICY_HASH = "93c97ef9d8b88f204d179a01301276d4142f13046e72192134d18db3a4834744"


def make_objective(**overrides):
    values = {
        "objective_id": "OBJ_FINAL_BLOCKERS_V4",
        "created_at": "2026-08-24T00:00:00+08:00",
        "max_batches": 5,
        "max_total_trials": 10,
    }
    values.update(overrides)
    return ResearchObjectiveV1.default(**values)


def make_input():
    return ResearchAgentInputBuilderV1().build(
        run_id="RUN_AGENT",
        batch_id="B01",
        objective={"objective_id": "OBJ", "mechanism_scope": ["m"]},
        policy_identity={},
        no_outcome_context=NoOutcomeResearchContextV1(),
        proposal_budget_view={"max_proposals": 1},
    )


def test_nooutcome_deep_guard_uses_one_canonical_policy():
    assert AGENT_FORBIDDEN_FIELDS is OutcomeBlindFieldPolicyV1.FORBIDDEN_FIELDS
    malicious = (
        {"classification": "REJECTED"},
        {"nested": {"return": -1}},
        {"nested": {"p_value": 0.1}},
        [{"recommendation": "BUY"}],
        ({"final_classification": "REJECTED"},),
        {"metadata": [{"prospective_outcome": "x"}]},
    )
    for payload in malicious:
        with pytest.raises(PerformanceLeakError):
            NoOutcomeResearchContextV1().assert_payload_blind(payload)
    with pytest.raises(PerformanceLeakError):
        NoOutcomeResearchContextV1(failure_class_summaries=({"nested": {"classification": "REJECTED"}},))
    with pytest.raises(PerformanceLeakError):
        NoOutcomeResearchContextV1(mechanism_history=({"metadata": {"final_test_outcome": "x"}},))
    legal = NoOutcomeResearchContextV1(
        failure_class_summaries=({
            "failure_category": "ALPHA_FAILURE",
            "reason_code": "BASE_ALPHA_FAILURE",
            "mechanism": "event_reversal",
            "family_id": "F1",
        },),
    )
    assert legal.to_dict()["outcome_fields_available"] is False


def test_partial_batch_recovery_materializes_third_trial_by_identity(tmp_path):
    objective = make_objective()
    root = tmp_path / "restart"
    with pytest.raises(RuntimeError, match="between_batches"):
        AutonomousResearchRunnerV2(objective, root=root, run_id="RUN_V4", policy_hash=POLICY_HASH, enabled=True, crash_at="between_batches").run_synthetic()
    with pytest.raises(RuntimeError, match="after_performance_completion_marker"):
        AutonomousResearchRunnerV2(objective, root=root, run_id="RUN_V4", policy_hash=POLICY_HASH, enabled=True, crash_at="after_performance_completion_marker").run_synthetic()

    run_dir = root / "reports" / "research_factory" / "autonomous_runs" / "RUN_V4"
    before_resume = AutonomousResearchRunnerV2(objective, root=root, run_id="RUN_V4", policy_hash=POLICY_HASH, enabled=True)
    assert len(before_resume.trial_view.used_trial_ids) == 3
    assert before_resume.run_budget.used_predictive_trials == 3
    assert before_resume.run_budget.remaining_predictive_trials == 7
    recovery_events = [event for event in before_resume.run_budget._events if event["event_type"] == "RUN_BUDGET_RECOVERY_APPLIED"]
    assert recovery_events and recovery_events[-1]["payload"]["recovered_trial_ids"] == [f"{objective.objective_id}_ARUN_RUN_V4_B02_T001"]

    resumed = before_resume.run_synthetic()
    assert resumed.run.state == "COMPLETED"
    assert resumed.status.predictive_trials_used == 10
    ledger = json.loads((run_dir / "factory" / "factory_trial_ledger.json").read_text(encoding="utf-8"))
    accessed_events = [event for event in ledger["events"] if event.get("event_type") == "PERFORMANCE_ACCESSED"]
    assert len(accessed_events) == len({event["trial_id"] for event in accessed_events}) == 10
    assert before_resume.run_budget.used_predictive_trials == 10
    assert before_resume.run_budget.remaining_predictive_trials == 0


@pytest.mark.parametrize("boundary", ("between_batches", "after_performance_completion_marker", "after_bh", "after_registry_commit", "after_batch_terminal"))
def test_five_batch_crash_matrix_reaches_terminal_after_restart(tmp_path, boundary):
    objective = make_objective(objective_id=f"OBJ_MATRIX_{boundary}")
    with pytest.raises(RuntimeError, match="SYNTHETIC_CRASH_INJECTED"):
        AutonomousResearchRunnerV2(objective, root=tmp_path, run_id="RUN_MATRIX", policy_hash=POLICY_HASH, enabled=True, crash_at=boundary).run_synthetic()
    resumed = AutonomousResearchRunnerV2(objective, root=tmp_path, run_id="RUN_MATRIX", policy_hash=POLICY_HASH, enabled=True).run_synthetic()
    assert resumed.run.state == "COMPLETED"
    assert resumed.status.predictive_trials_used == 10


def test_clean_and_restart_five_batch_runs_have_equivalent_logical_state(tmp_path):
    objective = make_objective(objective_id="OBJ_EQUIVALENCE")
    clean = AutonomousResearchRunnerV2(objective, root=tmp_path / "clean", run_id="RUN_EQ", policy_hash=POLICY_HASH, enabled=True).run_synthetic()
    with pytest.raises(RuntimeError, match="after_performance_completion_marker"):
        AutonomousResearchRunnerV2(objective, root=tmp_path / "restart", run_id="RUN_EQ", policy_hash=POLICY_HASH, enabled=True, crash_at="after_performance_completion_marker").run_synthetic()
    restarted = AutonomousResearchRunnerV2(objective, root=tmp_path / "restart", run_id="RUN_EQ", policy_hash=POLICY_HASH, enabled=True).run_synthetic()

    def logical(execution):
        return sorted((
            int(str(record["batch_id"]).rsplit("B", 1)[-1].split("_", 1)[0]),
            str(record["candidate_hash"]),
            str(record.get("classification")),
            str(record.get("status")),
        ) for result in execution.results for record in result.trial_records)

    assert logical(clean) == logical(restarted)
    assert clean.status.predictive_trials_used == restarted.status.predictive_trials_used == 10


def test_run_budget_rejects_outer_identity_not_in_durable_ledger(tmp_path):
    from chanlun_trader.research_factory import AutonomousRunBudgetV1
    from chanlun_trader.research_factory.budget import BudgetLedgerMismatchError

    path = tmp_path / "run_budget.json"
    budget = AutonomousRunBudgetV1(run_id="R", objective_id="O", path=path, max_batches=2, max_total_predictive_trials=2, max_trials_per_batch=2, max_hypotheses_per_batch=2, max_candidates_per_batch=2)
    budget.reserve_trial(trial_id="T1", batch_id="B01", candidate_id="C1", candidate_hash="H1", family_id="F1")
    budget.complete_trial("T1")
    reloaded = AutonomousRunBudgetV1(run_id="R", objective_id="O", path=path, max_batches=2, max_total_predictive_trials=2, max_trials_per_batch=2, max_hypotheses_per_batch=2, max_candidates_per_batch=2)
    with pytest.raises(BudgetLedgerMismatchError, match="not present in durable ledger"):
        reloaded.reconcile_against(trial_records=({"run_id": "R", "trial_id": "T2", "batch_id": "B01", "candidate_id": "C2", "candidate_hash": "H2", "budget_reserved": True, "performance_accessed": True},))


@pytest.mark.parametrize(
    ("policy_kwargs", "estimate_kwargs", "expected"),
    (
        ({"max_agent_tokens": 3, "agent_cost_budget": 10, "model_route": "SYNTHETIC"}, {"estimated_input_tokens": 2, "reserved_output_tokens": 2}, "AGENT_TOKEN_BUDGET_EXHAUSTED"),
        ({"max_agent_tokens": 10, "agent_cost_budget": 1, "model_route": "SYNTHETIC"}, {"estimated_cost_upper_bound": 2.0, "currency": "USD"}, "AGENT_COST_BUDGET_EXHAUSTED"),
    ),
)
def test_agent_pre_call_budget_fail_closed(tmp_path, policy_kwargs, estimate_kwargs, expected):
    policy = AgentGovernancePolicyV1(max_agent_calls=1, retry_limit=0, **policy_kwargs)
    backend = SyntheticResearchAgentBackendV1(**estimate_kwargs)
    budget = AgentCallBudgetV1(tmp_path / "budget.json", policy)
    governed = GovernedResearchAgentBackendV1(backend, policy=policy, budget=budget, audit=AgentCallAuditLedgerV1(tmp_path / "audit.json"))
    with pytest.raises(AgentBackendError, match=expected):
        governed.invoke(make_input(), run_id="RUN_AGENT", batch_id="B01", agent_call_id="CALL_1")
    assert backend.calls == 0
    assert budget.calls_reserved == 0
    assert budget.tokens_used == 0
    assert budget.cost_used == 0


def test_agent_call_budget_and_estimate_are_durable_before_allowed_call(tmp_path):
    policy = AgentGovernancePolicyV1(max_agent_calls=1, max_agent_tokens=10, agent_cost_budget=2, model_route="SYNTHETIC", retry_limit=0)
    backend = SyntheticResearchAgentBackendV1(estimated_input_tokens=2, reserved_output_tokens=3, estimated_cost_upper_bound=1, currency="USD")
    budget = AgentCallBudgetV1(tmp_path / "budget.json", policy)
    audit = AgentCallAuditLedgerV1(tmp_path / "audit.json")
    governed = GovernedResearchAgentBackendV1(backend, policy=policy, budget=budget, audit=audit)
    governed.invoke(make_input(), run_id="RUN_AGENT", batch_id="B01", agent_call_id="CALL_1")
    assert backend.calls == 1
    assert budget.calls_reserved == budget.calls_completed == 1
    assert budget.tokens_reserved == 0
    assert budget.cost_reserved == 0
    with pytest.raises(AgentBackendError, match="AGENT_CALL_BUDGET_EXHAUSTED"):
        governed.invoke(make_input(), run_id="RUN_AGENT", batch_id="B01", agent_call_id="CALL_2")
    assert backend.calls == 1
    assert audit.records()[0]["estimate"]["estimate_hash"]


def test_unbounded_estimate_fails_closed_before_backend_call(tmp_path):
    policy = AgentGovernancePolicyV1(max_agent_calls=1, model_route="SYNTHETIC")
    backend = SyntheticResearchAgentBackendV1(estimate_status="UNBOUNDED")
    budget = AgentCallBudgetV1(tmp_path / "budget.json", policy)
    with pytest.raises(AgentBackendError, match="AGENT_USAGE_ESTIMATE_UNBOUNDED"):
        GovernedResearchAgentBackendV1(backend, policy=policy, budget=budget, audit=AgentCallAuditLedgerV1(tmp_path / "audit.json")).invoke(make_input(), run_id="R", batch_id="B", agent_call_id="C")
    assert backend.calls == 0


class BoundViolationBackend(SyntheticResearchAgentBackendV1):
    def generate_proposals(self, input):
        result = super().generate_proposals(input)
        return ResearchProposalBatchV1(
            run_id=result.run_id,
            batch_id=result.batch_id,
            proposals=result.proposals,
            backend_type=result.backend_type,
            backend_version=result.backend_version,
            prompt_template_version=result.prompt_template_version,
            input_context_hash=result.input_context_hash,
            provenance={**result.provenance, "usage": {"input_tokens": 2, "output_tokens": 2, "total_tokens": 4, "usage_status": "AVAILABLE"}, "cost": {"estimated_cost": 0.1, "actual_cost": 0.2, "currency": "USD", "pricing_source": "TEST", "pricing_version": "1"}},
        )


def test_usage_bound_violation_fails_closed_after_call(tmp_path):
    policy = AgentGovernancePolicyV1(max_agent_calls=1, max_agent_tokens=10, agent_cost_budget=1, model_route="SYNTHETIC", retry_limit=0)
    backend = BoundViolationBackend(estimated_input_tokens=1, reserved_output_tokens=1, estimated_cost_upper_bound=0.1, currency="USD")
    budget = AgentCallBudgetV1(tmp_path / "budget.json", policy)
    with pytest.raises(AgentBackendError, match="AGENT_USAGE_BOUND_VIOLATION"):
        GovernedResearchAgentBackendV1(backend, policy=policy, budget=budget, audit=AgentCallAuditLedgerV1(tmp_path / "audit.json")).invoke(make_input(), run_id="R", batch_id="B", agent_call_id="C")
    assert backend.calls == 1
    assert budget.call("C")["status"] == "FAILED"
