from __future__ import annotations

import pytest

from chanlun_trader.research_factory import (
    CumulativeResearchHistoryV1,
    ResearchBatchState,
    ResearchBatchStateMachineV1,
    ResearchFactoryTrialLedgerFacadeV1,
    SearchBudgetRegistryV1,
)
from chanlun_trader.research_factory.budget import BudgetExhaustedError


def test_budget_rejects_trial_eleven_without_expansion():
    registry = SearchBudgetRegistryV1("OBJ")
    registry.register_objective(10)
    registry.register_batch("B1", 10)
    registry.register_family("event_reversal", 10)
    for index in range(10):
        reservation = registry.reserve_trial(batch_id="B1", family_id="event_reversal", candidate_id=f"C{index}")
        registry.consume(reservation)
    with pytest.raises(BudgetExhaustedError):
        registry.reserve_trial(batch_id="B1", family_id="event_reversal", candidate_id="C11")
    assert registry.used("objective", "OBJ") == 10


def test_state_machine_is_explicit_and_auditable(tmp_path):
    machine = ResearchBatchStateMachineV1("B1", path=tmp_path / "state.json")
    machine.transition(ResearchBatchState.BUDGET_RESERVED, "reserve")
    with pytest.raises(ValueError):
        machine.transition(ResearchBatchState.COMPLETED, "skip")
    machine.transition(ResearchBatchState.DESIGNING, "design")
    assert machine.audit()["transitions"][0]["to_state"] == "BUDGET_RESERVED"


def test_trial_facade_preserves_lineage_and_requires_preregistration(tmp_path):
    facade = ResearchFactoryTrialLedgerFacadeV1(path=tmp_path / "trials.json")
    facade.register_before_performance(
        trial_id="T1", objective_id="O", batch_id="B", family_id="event_reversal", hypothesis_id="H",
        candidate_id="C", candidate_hash="CH", dataset_hash="D", validation_policy_hash="P", engine_hash="E",
        seed=1, lineage={"source": "test"},
    )
    facade.mark_performance_accessed("T1")
    facade.mark_completed("T1", "REJECTED")
    record = facade.latest()["T1"]
    assert record.performance_accessed is True
    assert record.status == "COMPLETED"
    assert record.objective_id == "O"


def test_invalidated_engine_lineage_is_not_multiple_testing_evidence():
    history = CumulativeResearchHistoryV1("O")
    history = history.record_trial({"trial_id": "T1", "status": "COMPLETED", "performance_accessed": True, "classification": "REJECTED"})
    history = history.record_trial({"trial_id": "T2", "status": "INVALIDATED", "performance_accessed": True, "classification": "ENGINEERING_BLOCKED"})
    assert history.multiple_testing_denominator == 1
    assert len(history.invalidated_engine_lineage) == 1
