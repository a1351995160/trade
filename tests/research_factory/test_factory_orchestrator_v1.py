from __future__ import annotations

import json

import pytest

from chanlun_trader.research_factory import (
    AIResearchFactoryOrchestratorV1,
    NoOutcomeResearchContextV1,
    PerformanceLeakError,
    ResearchObjectiveV1,
    SyntheticFactoryRuntimeV1,
)


def objective(**overrides):
    return ResearchObjectiveV1.default(
        objective_id="OBJ_TEST_FACTORY",
        created_at="2026-08-24T00:00:00+08:00",
        max_batches=2,
        max_total_trials=10,
        stop_on_research_passed_count=2,
        **overrides,
    )


def test_objective_is_immutable_and_priority_is_fixed():
    item = objective()
    with pytest.raises(AttributeError):
        item.max_total_trials = 11
    with pytest.raises(ValueError):
        ResearchObjectiveV1(objective_id="bad", research_priority=("WIN_RATE",))


def test_synthetic_factory_classifies_and_plans_next_batch(tmp_path):
    result = AIResearchFactoryOrchestratorV1(objective(), output_dir=tmp_path).run_synthetic()
    assert result.state == "COMPLETED"
    assert result.status.rejected_count == 1
    assert result.status.research_passed_count == 1
    assert result.next_batch_plan is not None
    assert result.real_performance_trial_executed is False
    assert result.final_test_access == {"physical": 0, "analytical": 0, "decision": 0}


def test_engine_error_is_not_alpha_failure(tmp_path):
    result = AIResearchFactoryOrchestratorV1(objective(), output_dir=tmp_path, runtime=SyntheticFactoryRuntimeV1(engine_error=True)).run_synthetic()
    assert result.state == "ENGINEERING_BLOCKED"
    assert result.status.rejected_count == 0
    assert result.failure_snapshot.entries[0].category == "ENGINE_FAILURE"


def test_resume_does_not_regenerate_candidates(tmp_path):
    factory = AIResearchFactoryOrchestratorV1(objective(), output_dir=tmp_path, runtime=SyntheticFactoryRuntimeV1())
    partial = factory.run_synthetic(stop_after="CANDIDATES_FROZEN")
    resumed = factory.resume_from_checkpoint(partial.checkpoint_path)
    checkpoint = json.loads(open(partial.checkpoint_path, encoding="utf-8").read())
    assert resumed["next_stage"] == "STAGE1_VALIDATING"
    assert resumed["generator_calls"] == 0
    assert resumed["candidate_builder_calls"] == 0
    assert resumed["candidate_set_hash"] == checkpoint["candidate_set_hash"]


def test_performance_blind_guard_rejects_outcomes():
    context = NoOutcomeResearchContextV1()
    with pytest.raises(PerformanceLeakError):
        context.get("return")
    with pytest.raises(PerformanceLeakError):
        context.assert_payload_blind({"p_value": 0.01})
