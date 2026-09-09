from __future__ import annotations

from pathlib import Path
from chanlun_trader.execution_policy import ExecutionPolicy

from chanlun_trader.research_factory.autonomous_control_plane import (
    ALLOW_MANUAL_ONLY,
    CANONICAL_CONFLICT_BLOCKED,
    AutonomousResearchControlPlaneV1,
    WAIT_FOR_CANDIDATE_GOVERNANCE_FREEZE,
)

from test_candidate_generation_governance_v1 import OBJECTIVE_ID, _prepare


def test_canonical_conflict_is_denied_before_any_action_plan(tmp_path: Path, monkeypatch) -> None:
    root = _prepare(tmp_path)
    plane = AutonomousResearchControlPlaneV1(root)
    original = plane.reconciliation.reconcile

    def conflicted(objective_id: str):
        report = dict(original(objective_id))
        report["conflict_level"] = "CANONICAL_CONFLICT"
        report["conflicts"] = ["CANDIDATE_IDENTITY_CONFLICT"]
        return report

    monkeypatch.setattr(plane.reconciliation, "reconcile", conflicted)
    result = plane.inspect(OBJECTIVE_ID)

    assert result["decision"]["selected_action"]["action_type"] == "BLOCKED"
    assert result["decision"]["permission"]["permission"] == "DENY"
    assert result["decision"]["permission"]["reason_code"] == CANONICAL_CONFLICT_BLOCKED
    assert result["execution"] is None


def test_governance_wait_is_manual_even_when_candidate_capability_exists(tmp_path: Path) -> None:
    root = _prepare(tmp_path)
    plane = AutonomousResearchControlPlaneV1(root, execution_policy=ExecutionPolicy(mode="GOVERNED", workspace_kind="SYNTHETIC"))
    first = plane.tick(OBJECTIVE_ID)
    assert first["execution"]["execution_status"] == "COMPLETED"

    result = plane.inspect(OBJECTIVE_ID)

    assert result["decision"]["selected_action"]["action_type"] == WAIT_FOR_CANDIDATE_GOVERNANCE_FREEZE
    assert result["decision"]["permission"]["permission"] == ALLOW_MANUAL_ONLY
    assert result["decision"]["automatic_execution"] is False
