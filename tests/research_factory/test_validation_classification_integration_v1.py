from __future__ import annotations

import json
import pytest

from chanlun_trader.research.strategy_validation import (
    FinalResearchAdjudicatorV1,
    ValidationGovernanceError,
    ValidationPolicyV1,
    validation_decision_policy_v2_draft,
)
from chanlun_trader.research_factory.failure_adapter import FailureKnowledgeAdapterV1
from chanlun_trader.research_factory import AIResearchFactoryOrchestratorV1, ResearchObjectiveV1, SyntheticFactoryRuntimeV1
from chanlun_trader.research_factory.trial_adapter import ResearchFactoryTrialLedgerFacadeV1
from chanlun_trader.research_factory.real_runtime import _load_history


def multiple_testing(*, candidate_id: str = "C1", adjusted_support: bool = False) -> dict:
    return {
        "method": "BENJAMINI_HOCHBERG",
        "q": 0.05,
        "hypothesis_count": 2,
        "raw_p_values": {candidate_id: 0.0187, "C2": 0.2},
        "adjusted_p_values": {candidate_id: 0.17765, "C2": 1.0},
        "raw_support": {candidate_id: True, "C2": False},
        "adjusted_support": {candidate_id: adjusted_support, "C2": False},
    }


def decide(*, adjusted_support: bool = False, local: str = "RESEARCH_PASSED"):
    return FinalResearchAdjudicatorV1(ValidationPolicyV1()).adjudicate(
        candidate_id="C1",
        candidate_hash="HASH",
        trial_id="T1",
        local_classification=local,
        multiple_testing=multiple_testing(adjusted_support=adjusted_support),
        decision_family_id="TEST_FAMILY_V1",
        decision_denominator=2,
        history_snapshot_hash="HISTORY",
        metrics_ref="METRICS",
        evidence={
            "engine_integrity": "PASS",
            "small_capital_evidence_status": "AVAILABLE_REPORT_ONLY",
            "small_capital_contract_valid": True,
        },
    )


def test_bh_adjusted_support_is_required_for_final_pass():
    assert decide().effective_classification == "PROMISING"
    assert decide(adjusted_support=True).effective_classification == "RESEARCH_PASSED"
    assert decide().local_classification == "RESEARCH_PASSED_LOCAL"


def test_final_adjudicator_reads_fdr_q_from_frozen_policy():
    payload = multiple_testing()
    payload["q"] = 0.10
    with pytest.raises(ValidationGovernanceError):
        FinalResearchAdjudicatorV1(ValidationPolicyV1()).adjudicate(
            candidate_id="C1", candidate_hash="HASH", trial_id="T1", local_classification="RESEARCH_PASSED",
            multiple_testing=payload, decision_family_id="TEST_FAMILY_V1", decision_denominator=2,
            history_snapshot_hash="HISTORY", metrics_ref="METRICS",
        )


def test_provisional_trial_cannot_be_final_until_adjudication(tmp_path):
    ledger = ResearchFactoryTrialLedgerFacadeV1(path=tmp_path / "ledger.json")
    ledger.register_before_performance(
        trial_id="T1", objective_id="O", batch_id="B", family_id="F", hypothesis_id="H",
        candidate_id="C1", candidate_hash="HASH", dataset_hash="D", validation_policy_hash="P",
        engine_hash="E", seed=1,
    )
    ledger.mark_performance_accessed("T1")
    provisional = ledger.mark_provisional(trial_id="T1", local_classification="RESEARCH_PASSED", evidence_ref="provisional/T1.json")
    assert provisional.status == "PERFORMANCE_COMPLETE_PENDING_ADJUDICATION"
    assert provisional.classification is None
    assert provisional.performance_accessed is True
    final = ledger.mark_final_adjudication("T1", "PROMISING", decision_id="FINAL_RESEARCH_DECISION:T1")
    assert final.status == "COMPLETED"
    assert final.classification == "PROMISING"
    assert final.performance_accessed is True


def test_failure_knowledge_excludes_success_and_maps_reason_codes():
    adapter = FailureKnowledgeAdapterV1()
    snapshot = adapter.snapshot_from_trials([
        {"trial_id": "PASS", "family_id": "F", "classification": "RESEARCH_PASSED"},
        {"trial_id": "PROMISING", "family_id": "F", "classification": "PROMISING"},
        {"trial_id": "BASE", "family_id": "F", "classification": "REJECTED", "base_metrics": {"net_return": -0.1, "profit_factor": 0.8}},
        {"trial_id": "COST", "family_id": "F", "classification": "REJECTED", "base_metrics": {"net_return": 0.1, "profit_factor": 1.2}, "cost_stress": {"COMBINED_X2": {"net_return": -0.01}}},
        {"trial_id": "ENGINE", "family_id": "F", "classification": "ENGINEERING_BLOCKED", "reason_codes": ("ENGINE_FAILURE",)},
    ], snapshot_id="S")
    assert {item.category for item in snapshot.entries} == {"ALPHA_FAILURE", "ROBUSTNESS_FAILURE", "ENGINE_FAILURE"}
    assert all("PASS" not in item.source_trial_ids and "PROMISING" not in item.source_trial_ids for item in snapshot.entries)
    reasons = {item.high_level_reason for item in snapshot.entries}
    assert "BASE_RETURN_NONPOSITIVE" in reasons
    assert "COMBINED_COST_STRESS_FAILURE" in reasons
    assert "ENGINE_FAILURE" in reasons


def test_v2_policy_is_draft_with_unresolved_thresholds():
    draft = validation_decision_policy_v2_draft()
    assert draft["activation_status"] == "DRAFT_NOT_ACTIVE"
    assert draft["autonomous_research_ready"] == "NO"
    assert "concentration" in draft["unresolved_thresholds"]
    assert "10k" in draft["unresolved_thresholds"]


def test_pending_checkpoint_resume_never_reruns_performance(tmp_path):
    objective = ResearchObjectiveV1.default(
        objective_id="OBJ_RESUME", created_at="2026-08-24T00:00:00+08:00", max_batches=1,
        max_total_trials=2, stop_on_research_passed_count=None,
    )
    factory = AIResearchFactoryOrchestratorV1(objective, output_dir=tmp_path, runtime=SyntheticFactoryRuntimeV1())
    partial = factory.run_synthetic(stop_after="CANDIDATES_FROZEN")
    checkpoint_path = partial.checkpoint_path
    payload = json.loads(open(checkpoint_path, encoding="utf-8").read())
    payload.update({
        "state": "PERFORMANCE_VALIDATING",
        "pending_trial_ids": ["OBJ_RESUME_B01_T001"],
        "final_adjudication_pending": True,
    })
    with open(checkpoint_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    resumed = factory.resume_from_checkpoint(checkpoint_path)
    assert resumed["performance_rerun"] is False
    assert resumed["multiple_testing"] == "RUN_ON_RESUME"
    assert resumed["final_adjudication"] == "RUN_ON_RESUME"
    assert resumed["strategy_registry_commit"] == "ONCE_AFTER_FINAL_ADJUDICATION"


def test_future_history_loader_prefers_effective_final_view(tmp_path):
    path = tmp_path / "reports/CURRENT_EFFECTIVE_RESEARCH_HISTORY_V1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"rows": [
        {"trial_id": "T1", "candidate_id": "C1", "performance_accessed": True, "classification": "RESEARCH_PASSED", "final_adjudicated_outcome": "PROMISING"},
        {"trial_id": "T2", "candidate_id": "C2", "performance_accessed": True, "classification": "REJECTED", "final_adjudicated_outcome": "REJECTED"},
    ]}), encoding="utf-8")
    history = _load_history(tmp_path, "OBJ")
    assert history.classification_counts() == {"PROMISING": 1, "REJECTED": 1}
