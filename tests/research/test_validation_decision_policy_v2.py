from __future__ import annotations

import json
from dataclasses import replace

import pytest

from chanlun_trader.research.strategy_validation import FinalResearchAdjudicatorV1, benjamini_hochberg
from chanlun_trader.research.validation_policy_v2 import (
    ValidationPolicyV2Error,
    default_validation_decision_policy_v2,
    load_validation_decision_policy_v2,
)
from chanlun_trader.research_factory import AIResearchFactoryOrchestratorV1, ResearchObjectiveV1


def evidence(policy, *, mutate: dict[str, bool] | None = None, soft: dict | None = None):
    gates = {gate_id: {"passed": True} for gate_id in policy.hard_gates if gate_id != "multiple_testing_adjusted_support"}
    for gate_id, passed in (mutate or {}).items():
        gates[gate_id] = {"passed": passed}
    return {"engine_integrity": "PASS", "gates": gates, "soft_evidence": soft or {}, "small_capital_evidence_status": "SYNTHETIC_ONLY"}


def multiple_testing(policy, p_value: float = 0.001):
    payload = benjamini_hochberg({"C1": p_value, "C2": 0.90}, q=policy.fdr_q)
    payload.update({
        "decision_family_id": "TEST:V2:FAMILY",
        "decision_denominator": 2,
        "family_contract_hash": policy.multiple_testing_contract_hash,
    })
    return payload


def adjudicate(policy, *, p_value: float = 0.001, local: str = "RESEARCH_PASSED", mutate: dict[str, bool] | None = None, soft: dict | None = None):
    return FinalResearchAdjudicatorV1(policy).adjudicate(
        candidate_id="C1",
        candidate_hash="SYNTHETIC_HASH",
        trial_id="SYNTHETIC_T1",
        local_classification=local,
        multiple_testing=multiple_testing(policy, p_value),
        decision_family_id="TEST:V2:FAMILY",
        decision_denominator=2,
        history_snapshot_hash="SYNTHETIC_HISTORY",
        metrics_ref="SYNTHETIC_ONLY",
        evidence=evidence(policy, mutate=mutate, soft=soft),
    )


def test_policy_schema_hash_roles_and_provenance():
    policy = default_validation_decision_policy_v2()
    policy.validate()
    assert policy.policy_hash == policy.computed_hash()
    assert len(policy.hard_gates) == 15
    assert len(policy.soft_evidence) == 5
    assert len(policy.report_only) == 5
    assert all(item["threshold_source"] != "NONE" for item in policy.hard_gates.values())


def test_unresolved_hard_gate_blocks_policy():
    policy = default_validation_decision_policy_v2()
    gates = dict(policy.gates)
    gates["local_base_return"] = dict(gates["local_base_return"], threshold=None, threshold_source="NONE")
    blocked = replace(policy, gates=gates, policy_hash="")
    with pytest.raises(ValidationPolicyV2Error):
        blocked.validate()


def test_v2_classification_matrix_and_soft_warning():
    policy = default_validation_decision_policy_v2()
    assert adjudicate(policy).effective_classification == "RESEARCH_PASSED"
    assert adjudicate(policy, p_value=0.20).effective_classification == "PROMISING"
    assert adjudicate(policy, mutate={"sample_adequacy": False}).effective_classification == "BLOCKED"
    assert adjudicate(policy, mutate={"engine_integrity": False}).effective_classification == "ENGINEERING_BLOCKED"
    cost = adjudicate(policy, mutate={"cost_stress_combined_x2": False})
    assert cost.effective_classification == "REJECTED"
    assert cost.failure_category == "ROBUSTNESS_FAILURE"
    concentration = adjudicate(policy, soft={"concentration_diagnostics": {"status": "HIGH", "warning": "WINNER_CONCENTRATION_WARNING"}})
    assert concentration.effective_classification == "RESEARCH_PASSED"
    assert "WINNER_CONCENTRATION_WARNING" in concentration.warnings
    ten_k = adjudicate(policy, soft={"small_capital_economic_performance": {"status": "WEAK", "warning": "SMALL_CAPITAL_DEGRADATION_WARNING"}})
    assert ten_k.effective_classification == "RESEARCH_PASSED"
    assert "SMALL_CAPITAL_DEGRADATION_WARNING" in ten_k.warnings


def test_v2_policy_artifact_and_lock_are_valid():
    policy, policy_hash = load_validation_decision_policy_v2("data/research/strategy_validation/validation_decision_policy_v2.json")
    assert policy.policy_hash == policy_hash
    assert policy.policy_status == "FROZEN_ACTIVE"


def test_factory_plan_pins_v2_and_resume_rejects_hash_mismatch(tmp_path):
    objective = ResearchObjectiveV1.default(objective_id="OBJ_V2_PIN", max_batches=1, max_total_trials=2, stop_on_research_passed_count=None)
    factory = AIResearchFactoryOrchestratorV1(objective, output_dir=tmp_path)
    partial = factory.run_synthetic(stop_after="CANDIDATES_FROZEN")
    assert partial.batch_plan.validation_policy_id == "VALIDATION_DECISION_POLICY_V2"
    payload = json.loads(open(partial.checkpoint_path, encoding="utf-8").read())
    payload["validation_policy_hash"] = "0" * 64
    with open(partial.checkpoint_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    with pytest.raises(ValidationPolicyV2Error):
        factory.resume_from_checkpoint(partial.checkpoint_path)
