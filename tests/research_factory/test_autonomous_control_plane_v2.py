from __future__ import annotations

import json

import pytest

from chanlun_trader.research_factory import (
    AgentBackendError,
    AUTONOMOUS_RESEARCH_ENABLED,
    AutonomousResearchRunV2,
    AutonomousResearchRunnerV2,
    AutonomousRunBudgetV1,
    AutonomousRunState,
    CandidateNeighborhoodIndexV1,
    CandidateNoveltyGateV2,
    FamilyDiversityEnforcerV1,
    FamilyDiversityPolicyV1,
    NoOutcomeResearchContextV1,
    PerformanceLeakError,
    ResearchAgentInputV1,
    ResearchAgentInputBuilderV1,
    ResearchObjectiveV1,
    SearchBudgetRegistryV1,
    TemplateResearchAgentBackendV1,
    deterministic_batch_id,
)
from chanlun_trader.research_factory.budget import BudgetLedgerMismatchError


POLICY_HASH = "93c97ef9d8b88f204d179a01301276d4142f13046e72192134d18db3a4834744"


def make_objective(**overrides):
    values = {"objective_id": "OBJ_AUTONOMOUS_V2_TEST", "created_at": "2026-08-24T00:00:00+08:00", "max_batches": 3, "max_total_trials": 10}
    values.update(overrides)
    return ResearchObjectiveV1.default(**values)


def candidate(candidate_id: str, candidate_hash: str, *, mechanism: str = "event_reversal", family_id: str = "event_reversal", holding: int = 5, factors=("F1",), events=("E1",), params=None):
    return {
        "candidate_id": candidate_id,
        "candidate_hash": candidate_hash,
        "family_id": family_id,
        "mechanism": mechanism,
        "factor_ids": list(factors),
        "event_ids": list(events),
        "holding_period_days": holding,
        "parameter_fingerprint": params or {"threshold": 10},
        "hypothesis_id": f"{candidate_id}_H",
    }


def test_batch_identity_and_autonomous_gate_are_safe():
    assert deterministic_batch_id("OBJ", "RUN", 2) == "OBJ_ARUN_RUN_B02"
    assert AUTONOMOUS_RESEARCH_ENABLED is False
    run = AutonomousResearchRunV2.create(
        run_id="RUN",
        objective_id="OBJ",
        policy_id="P",
        policy_version="1",
        policy_hash="H",
        max_batches=2,
        max_total_predictive_trials=4,
        max_trials_per_batch=2,
        max_hypotheses_per_batch=2,
        max_candidates_per_batch=2,
    )
    assert run.transition(AutonomousRunState.READY, "ready").state == "READY"


def test_search_budget_reloads_buckets_and_active_candidate_reservation(tmp_path):
    path = tmp_path / "search_budget.json"
    first = SearchBudgetRegistryV1("OBJ", path)
    first.register_objective(2)
    first.register_batch("B01", 2)
    first.register_family("F", 2)
    first.register_candidate("C1", 1)
    reservation = first.reserve_trial(batch_id="B01", family_id="F", candidate_id="C1")

    second = SearchBudgetRegistryV1("OBJ", path)
    assert second.reserved("objective", "OBJ") == 1
    assert second.reserved("candidate", "C1") == 1
    second.consume(reservation)
    third = SearchBudgetRegistryV1("OBJ", path)
    assert third.used("objective", "OBJ") == 1
    assert third.used("candidate", "C1") == 1


def test_released_trial_reservation_can_be_reactivated_for_safe_retry(tmp_path):
    path = tmp_path / "search_budget.json"
    first = SearchBudgetRegistryV1("OBJ", path)
    first.register_objective(2)
    first.register_batch("B01", 2)
    first.register_family("F", 2)
    first.register_candidate("C1", 1)
    reservation = first.reserve_trial(batch_id="B01", family_id="F", candidate_id="C1")
    first.release(reservation)

    retry = SearchBudgetRegistryV1("OBJ", path)
    assert retry.reserve_trial(batch_id="B01", family_id="F", candidate_id="C1") == reservation
    assert retry.reserved("objective", "OBJ") == 1
    assert retry.reserved("candidate", "C1") == 1


def test_run_budget_reloads_and_reconciles(tmp_path):
    path = tmp_path / "run_budget.json"
    first = AutonomousRunBudgetV1(run_id="R", objective_id="O", path=path, max_batches=2, max_total_predictive_trials=4, max_trials_per_batch=2, max_hypotheses_per_batch=2, max_candidates_per_batch=2)
    first.start_batch("B01")
    first.reserve_trial(trial_id="T1", batch_id="B01", candidate_id="C1", family_id="F1")
    second = AutonomousRunBudgetV1(run_id="R", objective_id="O", path=path, max_batches=2, max_total_predictive_trials=4, max_trials_per_batch=2, max_hypotheses_per_batch=2, max_candidates_per_batch=2)
    assert second.reserved_predictive_trials == 1
    second.complete_trial("T1")
    second.complete_batch("B01")
    third = AutonomousRunBudgetV1(run_id="R", objective_id="O", path=path, max_batches=2, max_total_predictive_trials=4, max_trials_per_batch=2, max_hypotheses_per_batch=2, max_candidates_per_batch=2)
    records = ({"trial_id": "T1", "performance_accessed": True, "status": "COMPLETED"},)
    third.reconcile_against(trial_records=records, batch_checkpoint={"completed_trial_ids": ["T1"]}, run_checkpoint={"total_trials_reserved": 0, "total_trials_started": 1, "total_trials_completed": 1})
    assert third.remaining_predictive_trials == 3


def test_terminal_synthetic_checkpoint_materializes_without_runtime_calls(tmp_path):
    objective = make_objective(max_batches=1, max_total_trials=4)
    from chanlun_trader.research_factory import AIResearchFactoryOrchestratorV1, SyntheticFactoryRuntimeV1

    runtime = SyntheticFactoryRuntimeV1()
    first = AIResearchFactoryOrchestratorV1(objective, output_dir=tmp_path, runtime=runtime).run_synthetic()
    calls = (runtime.generator_calls, runtime.builder_calls, runtime.validator_calls)
    second = AIResearchFactoryOrchestratorV1(objective, output_dir=tmp_path, runtime=runtime).run_synthetic()
    assert second.state == "COMPLETED"
    assert len(second.trial_records) == len(first.trial_records)
    assert (runtime.generator_calls, runtime.builder_calls, runtime.validator_calls) == calls
    assert second.status.trials_completed == first.status.trials_completed


def test_three_batch_runner_persists_run_and_failure_feedback(tmp_path):
    runner = AutonomousResearchRunnerV2(make_objective(), root=tmp_path, run_id="RUN_3", policy_hash=POLICY_HASH, enabled=True)
    execution = runner.run_synthetic()
    assert execution.run.state == "COMPLETED"
    assert execution.run.batches_completed == 3
    assert [result.batch_plan.batch_id for result in execution.results] == [
        "OBJ_AUTONOMOUS_V2_TEST_ARUN_RUN_3_B01",
        "OBJ_AUTONOMOUS_V2_TEST_ARUN_RUN_3_B02",
        "OBJ_AUTONOMOUS_V2_TEST_ARUN_RUN_3_B03",
    ]
    assert execution.status.predictive_trials_used == 6
    assert execution.status.stop_reason == "MAX_BATCHES_REACHED"
    context_path = tmp_path / "reports" / "research_factory" / "autonomous_runs" / "RUN_3" / "batches" / "OBJ_AUTONOMOUS_V2_TEST_ARUN_RUN_3_B02" / "research_agent_input.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    assert context["failure_knowledge_view"]["entries"]
    failure_payload = json.dumps(context["failure_knowledge_view"]).lower()
    assert '"return"' not in failure_payload
    assert '"p_value"' not in failure_payload
    assert '"recommendation"' not in failure_payload
    assert (tmp_path / "reports" / "research_factory" / "autonomous_runs" / "RUN_3" / "run_contract.json").exists()
    assert (tmp_path / "reports" / "research_factory" / "autonomous_runs" / "RUN_3" / "run_checkpoint.json").exists()


def test_resume_policy_hash_mismatch_fails_closed(tmp_path):
    runner = AutonomousResearchRunnerV2(make_objective(max_batches=1), root=tmp_path, run_id="RUN_POLICY", policy_hash=POLICY_HASH, enabled=True)
    runner.run_synthetic()
    with pytest.raises(RuntimeError, match="FAIL_CLOSED_POLICY_HASH_MISMATCH"):
        AutonomousResearchRunnerV2(make_objective(max_batches=1), root=tmp_path, run_id="RUN_POLICY", policy_hash="different", enabled=True)


def test_terminal_run_restart_reads_factory_status_without_new_backend_or_trial_budget(tmp_path):
    objective = make_objective(max_batches=2)
    first = AutonomousResearchRunnerV2(objective, root=tmp_path, run_id="RUN_RESTART", policy_hash=POLICY_HASH, enabled=True)
    completed = first.run_synthetic()
    second = AutonomousResearchRunnerV2(objective, root=tmp_path, run_id="RUN_RESTART", policy_hash=POLICY_HASH, enabled=True)
    resumed = second.run_synthetic()
    assert resumed.run.state == "COMPLETED"
    assert resumed.status.predictive_trials_used == completed.status.predictive_trials_used
    assert resumed.status.rejected_count == completed.status.rejected_count
    assert second.run_budget.used_predictive_trials == completed.status.predictive_trials_used


class FailingBackend(TemplateResearchAgentBackendV1):
    backend_type = "FAILING"
    backend_version = "FailingBackendV1"

    def generate_proposals(self, input):
        raise AgentBackendError("timeout")


def test_agent_backend_failure_stops_without_predictive_budget_consumption(tmp_path):
    runner = AutonomousResearchRunnerV2(make_objective(max_batches=2), root=tmp_path, run_id="RUN_AGENT_ERROR", policy_hash=POLICY_HASH, enabled=True, backend=FailingBackend())
    execution = runner.run_synthetic()
    assert execution.run.state == "BLOCKED_ENGINEERING"
    assert execution.run.stop_reason == "AGENT_BACKEND_ERROR"
    assert execution.status.predictive_trials_used == 0


def test_novelty_gate_checks_exact_hash_same_batch_and_parameter_neighbor(tmp_path):
    index = CandidateNeighborhoodIndexV1(tmp_path / "neighborhood.json")
    historical = candidate("OLD", "HASH_OLD")
    index.add(historical)
    gate = CandidateNoveltyGateV2()
    assert gate.evaluate(candidate("NEW", "HASH_OLD"), historical_candidates=index.all()).reason == "EXACT_DUPLICATE_CANDIDATE"
    assert gate.evaluate(candidate("NEW2", "HASH_NEW"), same_batch_candidates=[historical]).reason == "PARAMETER_NEIGHBOR_CANDIDATE"
    assert gate.evaluate(candidate("NEW3", "HASH_NEW3", mechanism="breakout", factors=("F2",)), historical_candidates=index.all()).allowed


def test_family_diversity_rejects_excess_before_budget():
    policy = FamilyDiversityPolicyV1(2, 1, 2, 0, {"source": "TEST_PREDECLARED"})
    result = FamilyDiversityEnforcerV1(policy).freeze([
        candidate("C1", "H1", mechanism="m1", family_id="F"),
        candidate("C2", "H2", mechanism="m2", family_id="F", factors=("F2",)),
        candidate("C3", "H3", mechanism="m3", family_id="F", factors=("F3",)),
    ])
    assert len(result.accepted_candidates) == 2
    assert len(result.rejected_candidates) == 1


def test_agent_input_and_proposal_are_outcome_blind():
    context = NoOutcomeResearchContextV1()
    valid = ResearchAgentInputV1(run_id="R", batch_id="B", objective={"objective_id": "O", "mechanism_scope": ["m"]}, policy_identity={"policy_hash": "H"}, no_outcome_context=context, proposal_budget_view={"max_proposals": 1})
    assert TemplateResearchAgentBackendV1().invoke(valid).proposals[0].mechanism == "m"
    with pytest.raises(PerformanceLeakError):
        ResearchAgentInputV1(run_id="R", batch_id="B", objective={"objective_id": "O", "recommendation": "BUY"}, policy_identity={}, no_outcome_context=context)
    with pytest.raises(PerformanceLeakError):
        ResearchAgentInputBuilderV1().build(run_id="R", batch_id="B", objective={"objective_id": "O", "mechanism_scope": ["m"]}, policy_identity={}, no_outcome_context=context, failure_knowledge={"return": 1})


def test_failure_view_has_provenance_and_no_exact_outcomes():
    from chanlun_trader.research_factory import FailureKnowledgeAdapterV1

    snapshot = FailureKnowledgeAdapterV1().snapshot_from_trials(({"trial_id": "T1", "classification": "REJECTED", "family_id": "F", "reason_codes": ["COST_FAILURE"], "base_metrics": {"net_return": -1}},), snapshot_id="B01_F")
    view = snapshot.sanitized_view(source_batch_ids=("B01",), source_history_hash="H")
    payload = view.to_dict()
    assert payload["source_batch_ids"] == ["B01"]
    assert payload["exact_performance_values_exposed"] is False
    assert "net_return" not in json.dumps(payload)
