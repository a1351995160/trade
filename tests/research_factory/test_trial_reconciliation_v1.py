from __future__ import annotations

import json
from pathlib import Path

import pytest

from chanlun_trader.research.strategy_validation import TrialRegistryV1
from chanlun_trader.research_daemon_state import DaemonCheckpointStoreV1, DaemonCheckpointV1
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.trial_adapter import ResearchFactoryTrialLedgerFacadeV1
from chanlun_trader.research_factory.trial_reconciliation import CanonicalTrialReconciliationServiceV1, TrialReconciliationError


OBJECTIVE_ID = "OBJECTIVE_TRIAL_RECONCILIATION"
TRIAL_ID = "TRIAL_INTERRUPTED_1"
CANDIDATE_ID = "CANDIDATE_1"


def _interrupted_trial(tmp_path: Path) -> tuple[Path, Path, DaemonCheckpointStoreV1]:
    batch_dir = tmp_path / "data/research/research_factory/batches/B01"
    ledger_path = batch_dir / "factory_trial_ledger.json"
    trial_registry_path = batch_dir / "trial_registry.json"
    budget_path = batch_dir / "search_budget_registry.json"
    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, budget_path)
    budget.register_objective(4)
    budget.register_batch("B01", 4)
    budget.register_family("FAMILY_1", 4)
    budget.register_candidate(CANDIDATE_ID)
    reservation_id = budget.reserve_trial(batch_id="B01", family_id="FAMILY_1", candidate_id=CANDIDATE_ID)
    ledger = ResearchFactoryTrialLedgerFacadeV1(trial_registry=TrialRegistryV1(trial_registry_path), path=ledger_path)
    ledger.register_before_performance(
        trial_id=TRIAL_ID,
        objective_id=OBJECTIVE_ID,
        batch_id="B01",
        family_id="FAMILY_1",
        hypothesis_id="HYPOTHESIS_1",
        candidate_id=CANDIDATE_ID,
        candidate_hash="HASH_1",
        dataset_hash="DATASET_1",
        validation_policy_hash="POLICY_1",
        engine_hash="ENGINE_1",
        seed=1,
        lineage={},
        budget_reservation_identity=reservation_id,
    )
    ledger.mark_performance_accessed(TRIAL_ID)
    store = DaemonCheckpointStoreV1(tmp_path, OBJECTIVE_ID)
    store.save(DaemonCheckpointV1(
        objective_id=OBJECTIVE_ID,
        current_state="ENGINEERING_BLOCKED",
        current_candidate={"candidate_id": CANDIDATE_ID, "candidate_hash": "HASH_1"},
        current_trial={"trial_id": TRIAL_ID, "performance_accessed": True, "status": "PERFORMANCE_ACCESSED"},
        required_action="CANONICAL_TRIAL_RECONCILIATION_REQUIRED",
        error_reason_code="PREDICTIVE_RUNTIME_ERROR",
        last_error="synthetic interruption",
        retry_safe=False,
    ))
    return ledger_path, budget_path, store


def test_trial_reconciliation_preview_is_read_only_and_stable(tmp_path: Path):
    ledger_path, budget_path, store = _interrupted_trial(tmp_path)
    service = CanonicalTrialReconciliationServiceV1(tmp_path)
    before = {path: path.read_bytes() for path in (ledger_path, budget_path, store.checkpoint_path)}

    first = service.preview(objective_id=OBJECTIVE_ID, trial_id=TRIAL_ID)
    second = service.preview(objective_id=OBJECTIVE_ID, trial_id=TRIAL_ID)

    assert first == second
    assert first["available"] is True
    assert first["performance_accessed"] is True
    assert first["performance_complete"] is False
    assert first["budget_reservation_status"] == "ACTIVE"
    assert first["safety_evidence"]["performance_rerun"] is False
    assert {path: path.read_bytes() for path in before} == before


def test_trial_reconciliation_terminalizes_without_rerun_and_is_idempotent(tmp_path: Path):
    ledger_path, budget_path, store = _interrupted_trial(tmp_path)
    service = CanonicalTrialReconciliationServiceV1(tmp_path)
    preview = service.preview(objective_id=OBJECTIVE_ID, trial_id=TRIAL_ID)

    result = service.confirm(
        objective_id=OBJECTIVE_ID,
        trial_id=TRIAL_ID,
        preview_hash=preview["preview_hash"],
        confirmation_token=preview["confirmation_token"],
    )
    repeated = service.confirm(
        objective_id=OBJECTIVE_ID,
        trial_id=TRIAL_ID,
        preview_hash=preview["preview_hash"],
        confirmation_token=preview["confirmation_token"],
    )

    latest = ResearchFactoryTrialLedgerFacadeV1(path=ledger_path).latest()[TRIAL_ID]
    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, budget_path).snapshot()
    checkpoint = store.load()
    assert repeated == result
    assert latest.status == "INVALIDATED"
    assert latest.classification == "ENGINEERING_INVALIDATED"
    assert latest.performance_accessed is True
    assert latest.performance_complete is False
    assert budget["settled_reservations"][latest.budget_reservation_identity] == "CONSUMED"
    assert checkpoint.current_state == "READY"
    assert checkpoint.current_trial is None
    assert checkpoint.required_action is None
    assert result["performance_rerun"] is False
    assert result["new_trial"] is False


def test_trial_reconciliation_rejects_stale_preview_and_existing_evidence(tmp_path: Path):
    ledger_path, _, _ = _interrupted_trial(tmp_path)
    service = CanonicalTrialReconciliationServiceV1(tmp_path)
    preview = service.preview(objective_id=OBJECTIVE_ID, trial_id=TRIAL_ID)
    with pytest.raises(TrialReconciliationError, match="STALE_CANONICAL_TRIAL_RECONCILIATION_PREVIEW"):
        service.confirm(
            objective_id=OBJECTIVE_ID,
            trial_id=TRIAL_ID,
            preview_hash="stale",
            confirmation_token=preview["confirmation_token"],
        )

    record = ResearchFactoryTrialLedgerFacadeV1(path=ledger_path).latest()[TRIAL_ID]
    evidence_path = tmp_path / "reports/research_daemon" / OBJECTIVE_ID / "predictive" / record.batch_id / record.candidate_id / "provisional_validation_evidence" / f"{TRIAL_ID}.json"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text(json.dumps({"trial_id": TRIAL_ID}), encoding="utf-8")
    with pytest.raises(TrialReconciliationError, match="CANONICAL_TRIAL_EVIDENCE_RECOVERY_REQUIRED"):
        service.preview(objective_id=OBJECTIVE_ID, trial_id=TRIAL_ID)


def test_trial_reconciliation_rejects_missing_trial_registry(tmp_path: Path):
    ledger_path, _, _ = _interrupted_trial(tmp_path)
    ledger_path.with_name("trial_registry.json").unlink()

    with pytest.raises(TrialReconciliationError, match="CANONICAL_TRIAL_REGISTRY_MISSING"):
        CanonicalTrialReconciliationServiceV1(tmp_path).preview(
            objective_id=OBJECTIVE_ID,
            trial_id=TRIAL_ID,
        )
