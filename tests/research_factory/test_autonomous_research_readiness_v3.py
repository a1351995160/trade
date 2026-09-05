from __future__ import annotations

import json
import pytest

from chanlun_trader.research_factory import (
    AIResearchFactoryOrchestratorV1,
    AgentBackendError,
    AgentCallAuditLedgerV1,
    AgentCallBudgetV1,
    AgentGovernancePolicyV1,
    AutonomousResearchRunnerV2,
    CandidateNoveltyGateV2,
    FamilyDiversityEnforcerV1,
    FamilyDiversityPolicyV1,
    GovernedResearchAgentBackendV1,
    NoOutcomeResearchContextV1,
    ResearchAgentInputBuilderV1,
    ResearchObjectiveV1,
    SyntheticFactoryRuntimeV1,
    TemplateResearchAgentBackendV1,
)


POLICY_HASH = "93c97ef9d8b88f204d179a01301276d4142f13046e72192134d18db3a4834744"


def make_objective(**overrides):
    values = {"objective_id": "OBJ_READINESS_V3_TEST", "created_at": "2026-08-24T00:00:00+08:00", "max_batches": 3, "max_total_trials": 10}
    values.update(overrides)
    return ResearchObjectiveV1.default(**values)


CRASH_BOUNDARIES = (
    "before_backend_proposal",
    "after_backend_proposal",
    "after_candidate_freeze",
    "after_budget_reservation",
    "after_performance_completion_marker",
    "before_bh",
    "after_bh",
    "after_final_adjudication",
    "after_trial_final_commit",
    "after_registry_commit",
    "after_failure_knowledge_commit",
    "after_history_update",
    "after_batch_terminal",
    "between_batches",
)


@pytest.mark.parametrize("boundary", CRASH_BOUNDARIES)
def test_enable_critical_crash_boundary_replays_to_terminal(tmp_path, boundary):
    max_batches = 2 if boundary == "between_batches" else 1
    with pytest.raises(RuntimeError, match="SYNTHETIC_CRASH_INJECTED"):
        AutonomousResearchRunnerV2(
            make_objective(max_batches=max_batches),
            root=tmp_path,
            run_id="RUN_CRASH_MATRIX",
            policy_hash=POLICY_HASH,
            enabled=True,
            crash_at=boundary,
        ).run_synthetic()

    resumed = AutonomousResearchRunnerV2(
        make_objective(max_batches=max_batches),
        root=tmp_path,
        run_id="RUN_CRASH_MATRIX",
        policy_hash=POLICY_HASH,
        enabled=True,
    ).run_synthetic()
    assert resumed.run.state == "COMPLETED"
    assert resumed.status.predictive_trials_used == (4 if boundary == "between_batches" else 2)


def test_between_batch_restart_restores_sanitized_failure_context_and_call_budget(tmp_path):
    objective = make_objective()
    with pytest.raises(RuntimeError, match="between_batches"):
        AutonomousResearchRunnerV2(objective, root=tmp_path, run_id="RUN_RECOVERY", policy_hash=POLICY_HASH, enabled=True, crash_at="between_batches").run_synthetic()

    resumed = AutonomousResearchRunnerV2(objective, root=tmp_path, run_id="RUN_RECOVERY", policy_hash=POLICY_HASH, enabled=True).run_synthetic()
    assert resumed.run.state == "COMPLETED"
    assert resumed.status.predictive_trials_used == 6
    run_dir = tmp_path / "reports" / "research_factory" / "autonomous_runs" / "RUN_RECOVERY"
    context = json.loads((run_dir / "batches" / "OBJ_READINESS_V3_TEST_ARUN_RUN_RECOVERY_B02" / "research_agent_input.json").read_text(encoding="utf-8"))
    failure_view = json.dumps(context["failure_knowledge_view"], ensure_ascii=False).lower()
    assert context["failure_knowledge_view"]["entries"]
    for forbidden in ("return", "pf", "dd", "win_rate", "p_value", "adjusted_p", "classification", "recommendation"):
        assert f'"{forbidden}"' not in failure_view
    audit = json.loads((run_dir / "agent_call_audit.json").read_text(encoding="utf-8"))
    assert len(audit["records"]) == 3
    assert len({item["agent_call_id"] for item in audit["records"]}) == 3


def test_final_decision_replay_is_identity_safe(tmp_path):
    factory = AIResearchFactoryOrchestratorV1(make_objective(max_batches=1), output_dir=tmp_path, runtime=SyntheticFactoryRuntimeV1())
    result = factory.run_synthetic()
    record = result.trial_records[0]
    latest = factory.trial_ledger.latest()[record["trial_id"]]
    decision_id = str(latest.lineage["final_decision_id"])
    event_count = len(factory.trial_ledger._events)
    factory.trial_ledger.mark_final_adjudication(record["trial_id"], latest.classification, decision_id=decision_id, reason_codes=latest.reason_codes)
    assert len(factory.trial_ledger._events) == event_count
    with pytest.raises(ValueError, match="FINAL_DECISION_IDENTITY_CONFLICT"):
        factory.trial_ledger.mark_final_adjudication(record["trial_id"], "RESEARCH_PASSED", decision_id=decision_id, reason_codes=("CONFLICT",))


def test_agent_governance_is_durable_bounded_and_outcome_blind(tmp_path):
    objective = make_objective(max_batches=1)
    agent_input = ResearchAgentInputBuilderV1().build(
        run_id="RUN_AGENT",
        batch_id="B01",
        objective=objective,
        policy_identity={"policy_id": "VALIDATION_DECISION_POLICY_V2", "policy_hash": POLICY_HASH},
        no_outcome_context=NoOutcomeResearchContextV1(),
        proposal_budget_view={"max_proposals": 1},
    )
    policy = AgentGovernancePolicyV1(max_agent_calls=1, retry_limit=0, timeout_seconds=1)
    budget = AgentCallBudgetV1(tmp_path / "agent_budget.json", policy)
    audit = AgentCallAuditLedgerV1(tmp_path / "agent_audit.json")
    governed = GovernedResearchAgentBackendV1(TemplateResearchAgentBackendV1(), policy=policy, budget=budget, audit=audit)
    governed.invoke(agent_input, run_id="RUN_AGENT", batch_id="B01", agent_call_id="CALL_1")
    with pytest.raises(AgentBackendError, match="AGENT_CALL_BUDGET_EXHAUSTED"):
        governed.invoke(agent_input, run_id="RUN_AGENT", batch_id="B01", agent_call_id="CALL_2")
    reloaded = AgentCallBudgetV1(tmp_path / "agent_budget.json", policy)
    assert reloaded.calls_reserved == 1
    assert reloaded.calls_completed == 1
    assert reloaded.calls_remaining == 0
    audit_payload = json.dumps(audit.records()).lower()
    assert "return" not in audit_payload
    assert "classification" not in audit_payload
    assert audit.records()[0]["agent_governance_hash"] == policy.policy_hash


def test_canonical_novelty_and_diversity_block_before_trial_budget():
    old = {"candidate_id": "OLD", "candidate_hash": "H1", "family_id": "F", "mechanism": "M", "factor_ids": ["F1"], "event_ids": [], "parameter_fingerprint": {"threshold": 10}}
    gate = CandidateNoveltyGateV2()
    assert gate.evaluate({**old, "candidate_id": "DUP"}, historical_candidates=[old]).reason == "EXACT_DUPLICATE_CANDIDATE"
    assert gate.evaluate({**old, "candidate_id": "NEIGHBOR", "candidate_hash": "H2", "parameter_fingerprint": {"threshold": 11}}, historical_candidates=[old]).reason == "PARAMETER_NEIGHBOR_CANDIDATE"
    policy = FamilyDiversityPolicyV1(1, 1, 2, 0, {"source": "TEST_PREDECLARED"})
    frozen = FamilyDiversityEnforcerV1(policy).freeze([
        {**old, "candidate_id": "C1", "candidate_hash": "C1H", "mechanism": "M1"},
        {**old, "candidate_id": "C2", "candidate_hash": "C2H", "mechanism": "M2", "family_id": "F2"},
    ], required_count=2)
    assert len(frozen.accepted_candidates) == 2
