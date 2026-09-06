from __future__ import annotations

from pathlib import Path

from chanlun_trader.research_factory.autonomous_action_journal import (
    EXECUTION_COMPLETED,
    EXECUTION_STARTED,
    AutonomousActionExecutionJournalV1,
)
from chanlun_trader.research_factory.autonomous_control_plane import ResearchActionV1


def _action(objective_id: str = "OBJ_JOURNAL") -> ResearchActionV1:
    return ResearchActionV1.create(
        action_type="GENERATE_CANDIDATE_PROPOSAL",
        objective_id=objective_id,
        required_state="AI_DESIGN_APPROVED",
        source_state="AI_DESIGN_APPROVED",
        source_context_id="SAFE_RUNTIME_CONTEXT_TEST",
        source_context_hash="CONTEXT_HASH_TEST",
        source_reconciliation_hash="RECONCILIATION_HASH_TEST",
        required_capabilities=("CAN_GENERATE_CANDIDATE_PROPOSAL",),
        automatic_execution_allowed=True,
        created_at="2026-09-06T00:00:00+08:00",
    )


def test_journal_reuses_completed_action_without_second_execution(tmp_path: Path) -> None:
    journal = AutonomousActionExecutionJournalV1(tmp_path, "OBJ_JOURNAL", clock=lambda: "2026-09-06T00:00:00+08:00")
    action = _action()

    status, started = journal.begin(action)
    assert status == EXECUTION_STARTED
    completed = journal.complete(
        action,
        resulting_reconciliation_hash="RESULT_HASH",
        resulting_state="CANDIDATE_PROPOSAL_READY",
        side_effect_refs=("reports/research_candidates/proposals/OBJ_JOURNAL/CANDIDATE_PROPOSAL.json",),
    )

    repeated_status, repeated = journal.begin(action)
    assert started.execution_status == EXECUTION_STARTED
    assert completed.execution_status == EXECUTION_COMPLETED
    assert repeated_status == EXECUTION_COMPLETED
    assert repeated.receipt_hash == completed.receipt_hash
    assert len(journal.read()) == 2


def test_journal_supports_safe_retry_after_started_without_domain_side_effect(tmp_path: Path) -> None:
    journal = AutonomousActionExecutionJournalV1(tmp_path, "OBJ_JOURNAL", clock=lambda: "2026-09-06T00:00:00+08:00")
    action = _action()

    journal.begin(action)
    journal.allow_retry(action)
    status, retried = journal.begin(action, allow_retry=True)
    completed = journal.complete(action, resulting_reconciliation_hash="RESULT_HASH", resulting_state="READY")

    assert status == EXECUTION_STARTED
    assert retried.attempt == 2
    assert completed.attempt == 2
    assert [item.execution_status for item in journal.read()] == [
        EXECUTION_STARTED,
        "RECOVERY_RETRY_ALLOWED",
        EXECUTION_STARTED,
        EXECUTION_COMPLETED,
    ]
