from __future__ import annotations

import json
from pathlib import Path

import pytest

from chanlun_trader.research.strategy_validation import TrialRegistryV1
from chanlun_trader.research_factory.artifact_graph import ResearchArtifactGraphV1
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.history import CumulativeResearchHistoryV1
from chanlun_trader.research_factory.orphan_resolution import (
    BATCH_ID,
    CANDIDATE_HASH,
    CANDIDATE_ID,
    OBJECTIVE_ID,
    RESERVATION_ID,
    TRIAL_ID,
    OrphanResolutionPathsV1,
    OrphanTrialResolutionV1,
    RecoverableExistingEvidence,
    SyntheticCrashAfterTerminalEvent,
)
from chanlun_trader.research_factory.strategy_adapter import ResearchStrategyRegistryFacadeV1
from chanlun_trader.research_factory.trial_adapter import ResearchFactoryTrialLedgerFacadeV1


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fixture_root(tmp_path: Path) -> Path:
    root = tmp_path
    paths = OrphanResolutionPathsV1(root)
    paths.r4.mkdir(parents=True)
    paths.data_b02.mkdir(parents=True)
    _write(paths.trial_registry, {"schema_version": "trial-registry-v1-append-only", "events": []})
    _write(paths.artifact_graph, {"schema_version": "research-artifact-graph-v1", "nodes": [], "edges": []})
    _write(paths.commit_markers, {"schema_version": "durable-commit-ledger-v1", "markers": []})
    _write(paths.checkpoint, {"schema_version": "checkpoint-v1", "completed_trial_ids": []})
    _write(paths.cumulative_history, CumulativeResearchHistoryV1(OBJECTIVE_ID).to_dict())
    _write(paths.pilot_summary, {"TOTAL_PREDICTIVE_TRIALS": 12, "TRIAL_BUDGET_USED": 12})

    ledger = ResearchFactoryTrialLedgerFacadeV1(
        trial_registry=TrialRegistryV1(paths.trial_registry),
        path=paths.factory_ledger,
    )
    ledger.register_before_performance(
        trial_id=TRIAL_ID,
        objective_id=OBJECTIVE_ID,
        batch_id=BATCH_ID,
        family_id="opening_count_trend_filter",
        hypothesis_id="HYP_EVENT_REVERSAL_OPENING_COUNT_TREND_FILTER_0408CD11_V1",
        candidate_id=CANDIDATE_ID,
        candidate_hash=CANDIDATE_HASH,
        dataset_hash="DATA",
        validation_policy_hash="93c97ef9d8b88f204d179a01301276d4142f13046e72192134d18db3a4834744",
        engine_hash="ENGINE",
        seed=1,
    )
    ledger.mark_performance_accessed(TRIAL_ID)

    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, paths.budget_registry)
    budget.register_objective(16)
    budget.register_batch(BATCH_ID, 8)
    budget.register_family("opening_count_trend_filter", 1)
    budget.register_candidate(CANDIDATE_ID, 1)
    assert budget.reserve_trial(batch_id=BATCH_ID, family_id="opening_count_trend_filter", candidate_id=CANDIDATE_ID) == RESERVATION_ID

    strategy = ResearchStrategyRegistryFacadeV1(path=paths.strategy_registry)
    strategy.register(CANDIDATE_ID, CANDIDATE_HASH, "opening_count_trend_filter")
    strategy.transition(CANDIDATE_ID, "SEMANTIC_READY")
    strategy.transition(CANDIDATE_ID, "VALIDATION_ELIGIBLE")
    return root


def test_existing_evidence_branch_is_detected_without_engine_access(tmp_path):
    root = _fixture_root(tmp_path)
    paths = OrphanResolutionPathsV1(root)
    _write(paths.provisional_evidence, {
        "trial_id": TRIAL_ID,
        "candidate_id": CANDIDATE_ID,
        "base_metrics": {"trial_id": TRIAL_ID, "candidate_preregistration_hash": CANDIDATE_HASH, "engine_version": "2.0.0"},
        "bootstrap": {"status": "COMPLETE", "p_value": 0.4},
        "gates": {"engine_integrity": {"passed": True}},
        "performance_completed": True,
    })
    engine_dir = paths.engine_evidence / "BASE_RESEARCH"
    engine_dir.mkdir(parents=True)
    (engine_dir / "trades.csv").write_text("realized_pnl\n1.0\n", encoding="utf-8")
    _write(engine_dir / "metrics.json", {"trial_id": TRIAL_ID, "realized_pnl": 1.0})

    audit = OrphanTrialResolutionV1(paths).audit_recovery()
    assert audit["authoritative_existing_evidence_found"] is True
    assert audit["resolution_branch"] == "RECOVERABLE_EXISTING_EVIDENCE"
    with pytest.raises(RecoverableExistingEvidence):
        OrphanTrialResolutionV1(paths).resolve()
    assert not any(item.get("event_type") == "ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS" for item in json.loads(paths.factory_ledger.read_text(encoding="utf-8")).get("events", []))


def test_irrecoverable_resolution_is_consumed_exactly_once_and_crash_safe(tmp_path):
    root = _fixture_root(tmp_path)
    paths = OrphanResolutionPathsV1(root)
    resolver = OrphanTrialResolutionV1(paths)

    with pytest.raises(SyntheticCrashAfterTerminalEvent):
        resolver.resolve(fault_after_terminal_event=True)

    first = resolver.resolve()
    ledger_after_first = json.loads(paths.factory_ledger.read_text(encoding="utf-8"))
    terminal_events = [item for item in ledger_after_first["events"] if item.get("event_type") == "ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS"]
    assert len(terminal_events) == 1
    assert terminal_events[0]["trial_id"] == TRIAL_ID
    assert terminal_events[0]["performance_accessed"] is True
    assert terminal_events[0]["performance_complete"] is False
    assert terminal_events[0]["registry_committed"] is False

    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, paths.budget_registry)
    assert budget.used("objective", OBJECTIVE_ID) == 1
    assert budget.reserved("objective", OBJECTIVE_ID) == 0
    assert budget.snapshot()["settled_reservations"][RESERVATION_ID] == "CONSUMED"

    checkpoint = json.loads(paths.checkpoint.read_text(encoding="utf-8"))
    assert checkpoint["invalidated_trial_ids"] == [TRIAL_ID]
    assert checkpoint["engineering_interrupted_trial_ids"] == [TRIAL_ID]
    assert TRIAL_ID not in checkpoint["completed_trial_ids"]

    summary = json.loads(paths.pilot_summary.read_text(encoding="utf-8"))
    assert summary["COMPLETED_VALID_TRIALS"] == 12
    assert summary["ENGINEERING_INVALID_ORPHAN_TRIALS"] == 1
    assert summary["TOTAL_PERFORMANCE_ACCESS_IDENTITIES"] == 13

    second = resolver.resolve()
    ledger_after_second = json.loads(paths.factory_ledger.read_text(encoding="utf-8"))
    terminal_events_after_second = [item for item in ledger_after_second["events"] if item.get("event_type") == "ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS"]
    assert len(terminal_events_after_second) == 1
    assert second["resolution_status"] == first["resolution_status"] == "COMPLETE"
    assert second["performance_rerun_count"] == 0
    assert second["bh_denominator_before"] == second["bh_denominator_after"] == 4
    assert second["bh_recomputed"] is False
    assert second["failure_knowledge_alpha_negative_entry_created"] is False
    assert second["novelty_exclusion_preserved"] is True
