from __future__ import annotations

import json
from pathlib import Path

import pytest

import chanlun_trader.research_factory.objective_reconciliation as reconciliation
from chanlun_trader.research_factory.objective_reconciliation import (
    AI_DESIGN_AWAITING_CONFIRMATION,
    BUDGET_AUTHORITY_AMBIGUOUS,
    BUDGET_EXHAUSTED,
    CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION,
    CANONICAL_CONFLICT,
    ObjectiveDialectClassifierV1,
    ObjectiveReconciliationServiceV1,
    PROJECTION_DRIFT,
    READY_FOR_STRUCTURAL_PREFLIGHT,
    REPAIRABLE_INDEX_DRIFT,
)


def _write_json(root: Path, relative: str, payload: object) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _objective(root: Path, objective_id: str, **extra: object) -> None:
    payload = {
        "schema_version": "research-objective-v1",
        "objective_id": objective_id,
        **extra,
    }
    _write_json(
        root,
        f"data/research/research_factory/objectives/{objective_id}.json",
        payload,
    )


def _budget(
    root: Path,
    objective_id: str,
    batch_id: str,
    *,
    used: int,
    limit: int,
    remaining: int | None = None,
) -> Path:
    remaining = limit - used if remaining is None else remaining
    return _write_json(
        root,
        f"data/research/research_factory/batches/{batch_id}/search_budget_registry.json",
        {
            "schema_version": "search-budget-registry-v1",
            "objective_id": objective_id,
            "buckets": [
                {
                    "kind": "objective",
                    "key": objective_id,
                    "limit": limit,
                    "used": used,
                    "reserved": 0,
                    "remaining": remaining,
                }
            ],
            "active_reservations": [],
            "settled_reservations": {},
        },
    )


def _candidate_registry(root: Path, objective_id: str, candidate_hash: str = "H1") -> Path:
    return _write_json(
        root,
        f"data/research/research_factory/candidates/{objective_id}/CANDIDATE_REGISTRY.json",
        {
            "schema_version": "candidate-registry-v1",
            "objective_id": objective_id,
            "candidates": [{"candidate_id": "C1", "candidate_hash": candidate_hash}],
        },
    )


def _contract_registry(root: Path, objective_id: str, candidate_hash: str = "H1") -> Path:
    return _write_json(
        root,
        "data/research/research_factory/batches/B01/durable_frozen_candidate_contracts.json",
        {
            "schema_version": "durable-frozen-candidate-contract-registry-v1",
            "contracts": [
                {
                    "candidate_id": "C1",
                    "candidate_hash": candidate_hash,
                    "policy_identity": {"objective_id": objective_id},
                }
            ],
        },
    )


def _ai_design(root: Path, objective_id: str, *, requires_confirmation: bool = True) -> None:
    directory = root / "reports/research_evolution/ai_design" / objective_id
    _write_json(
        root,
        f"reports/research_evolution/ai_design/{objective_id}/AI_RESEARCH_DESIGN_PROPOSAL.json",
        {
            "schema_version": "ai-research-design-v1",
            "objective_id": objective_id,
            "design_id": "D1",
            "design_hash": "DH1",
            "status": "AI_DESIGN_READY",
        },
    )
    _write_json(
        root,
        f"reports/research_evolution/ai_design/{objective_id}/AI_RESEARCH_DESIGN_STATE.json",
        {
            "schema_version": "ai-research-design-state-v1",
            "objective_id": objective_id,
            "design_id": "D1",
            "design_hash": "DH1",
            "status": "AI_DESIGN_READY",
            "requires_human_confirmation": requires_confirmation,
            "next_action": "HUMAN_CONFIRM_AI_RESEARCH_DESIGN",
        },
    )
    assert directory.is_dir()


def _trial_ledger(root: Path, objective_id: str, *, status: str = "COMPLETED") -> Path:
    return _write_json(
        root,
        "data/research/research_factory/batches/B01/factory_trial_ledger.json",
        {
            "schema_version": "factory-trial-ledger-v1",
            "events": [
                {
                    "schema_version": "factory-trial-record-v1",
                    "event_type": "COMPLETED",
                    "objective_id": objective_id,
                    "trial_id": "T1",
                    "batch_id": "B01",
                    "candidate_id": "C1",
                    "candidate_hash": "H1",
                    "status": status,
                    "performance_accessed": True,
                    "performance_complete": status == "COMPLETED",
                    "final_adjudicated": status == "COMPLETED",
                    "budget_reservation_identity": "R1",
                }
            ],
        },
    )


class _PassingContract:
    @classmethod
    def from_dict(cls, payload: dict) -> "_PassingContract":
        return cls()

    def provider_candidate_payload(self) -> dict:
        return {"candidate_id": "C1"}


def _graph(root: Path, objective_id: str, *, contract_hash: str = "H1", with_edge: bool = True) -> Path:
    edges = []
    if with_edge:
        edges.append({
            "source_id": "candidate:C1:Candidate",
            "edge_type": "HAS_DURABLE_CONTRACT",
            "target_id": f"frozen_contract:C1:{contract_hash}:FrozenCandidateContract",
            "created_at": "2026-09-01T00:00:00+00:00",
        })
    return _write_json(
        root,
        f"data/research/research_factory/artifact_graph/{objective_id}.json",
        {
            "schema_version": "research-artifact-graph-v1",
            "nodes": [
                {
                    "node_id": "objective:" + objective_id + ":Objective",
                    "node_type": "Objective",
                    "payload_hash": "OBJECTIVE_NODE",
                    "created_at": "2026-09-01T00:00:00+00:00",
                },
                {
                    "node_id": "candidate:C1:Candidate",
                    "node_type": "Candidate",
                    "payload_hash": "CANDIDATE_NODE",
                    "created_at": "2026-09-01T00:00:00+00:00",
                },
                {
                    "node_id": f"frozen_contract:C1:{contract_hash}:FrozenCandidateContract",
                    "node_type": "FrozenCandidateContract",
                    "payload_hash": "CONTRACT_NODE",
                    "created_at": "2026-09-01T00:00:00+00:00",
                },
            ],
            "edges": edges,
        },
    )


def test_objective_dialect_classifier_has_four_fail_closed_dialects() -> None:
    classifier = ObjectiveDialectClassifierV1()
    assert classifier.classify({"objective_id": "A"}, objective_id="A")["dialect"] == "DEFINITION_ONLY"
    assert classifier.classify(
        {
            "objective_id": "B",
            "lifecycle_state": "CREATED",
            "parent_objective_id": "P",
            "activation_authorized": False,
        },
        objective_id="B",
    )["dialect"] == "EVOLUTION_MANUAL_CREATED"
    assert classifier.classify(
        {
            "objective_id": "C",
            "lifecycle_state": "READY",
            "activation_authorized": True,
            "governance_action": "START_NEW_MECHANISM_OBJECTIVE",
            "execution_semantics_version": "v1",
        },
        objective_id="C",
    )["dialect"] == "GOVERNANCE_EXECUTION_READY"
    unknown = classifier.classify({"objective_id": "D", "lifecycle_state": "???", "activation_authorized": "maybe"}, objective_id="D")
    assert unknown["dialect"] == "LEGACY_UNKNOWN"
    assert unknown["reason_code"] == "OBJECTIVE_DIALECT_UNKNOWN"
    assert unknown["dynamic_lifecycle_authoritative"] is False


def test_matrix_a_ai_design_ready_without_durable_approval_is_awaiting_confirmation(tmp_path: Path) -> None:
    objective_id = "OBJ_A"
    _objective(root=tmp_path, objective_id=objective_id, lifecycle_state="CREATED", parent_objective_id="P", activation_authorized=False)
    _ai_design(tmp_path, objective_id)
    report = ObjectiveReconciliationServiceV1(tmp_path).reconcile(objective_id)
    assert report["effective_state"] == AI_DESIGN_AWAITING_CONFIRMATION
    assert report["required_action"] == "HUMAN_CONFIRM_AI_RESEARCH_DESIGN"
    assert report["required_action_supported"] is False
    assert report["safe_to_advance"] is False
    assert "AI_DESIGN_APPROVAL_EVIDENCE_MISSING" in report["warnings"]


def test_matrix_b_governance_freeze_without_contract_is_not_structural_ready(tmp_path: Path) -> None:
    objective_id = "OBJ_B"
    _objective(tmp_path, objective_id)
    _candidate_registry(tmp_path, objective_id)
    _write_json(
        tmp_path,
        f"reports/research_candidates/proposals/{objective_id}/CANDIDATE_FREEZE_RECEIPT.json",
        {"objective_id": objective_id, "candidate_id": "C1", "candidate_hash": "H1", "freeze_id": "F1"},
    )
    report = ObjectiveReconciliationServiceV1(tmp_path).reconcile(objective_id)
    assert report["effective_state"] == CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION
    assert report["structural_preflight_ready"] is False
    assert report["candidate_reconciliation"]["executable_frozen_candidate"] is False
    assert report["safe_to_advance"] is False


def test_matrix_c_registry_and_contract_hash_mismatch_is_canonical_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    objective_id = "OBJ_C"
    _objective(tmp_path, objective_id)
    _candidate_registry(tmp_path, objective_id, "H1")
    _contract_registry(tmp_path, objective_id, "H2")
    monkeypatch.setattr(reconciliation, "DurableFrozenCandidateContractV1", _PassingContract)
    report = ObjectiveReconciliationServiceV1(tmp_path).reconcile(objective_id)
    assert report["conflict_level"] == CANONICAL_CONFLICT
    assert "CANONICAL_CANDIDATE_IDENTITY_CONFLICT" in report["conflicts"]
    assert report["safe_to_resume"] is False


def test_matrix_d_unique_full_contract_is_ready_for_structural_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    objective_id = "OBJ_D"
    _objective(tmp_path, objective_id)
    _contract_registry(tmp_path, objective_id, "H1")
    monkeypatch.setattr(reconciliation, "DurableFrozenCandidateContractV1", _PassingContract)
    report = ObjectiveReconciliationServiceV1(tmp_path).reconcile(objective_id)
    contract = report["candidate_reconciliation"]["durable_contracts"][0]
    assert contract["from_dict"] == "PASS"
    assert contract["provider_candidate_payload"] == "PASS"
    assert report["effective_state"] == READY_FOR_STRUCTURAL_PREFLIGHT
    assert report["structural_preflight_ready"] is True


def test_matrix_e_canonical_budget_wins_over_stale_daemon_budget_projection(tmp_path: Path) -> None:
    objective_id = "OBJ_E"
    _objective(tmp_path, objective_id)
    _budget(tmp_path, objective_id, "B01", used=2, limit=6)
    _write_json(
        tmp_path,
        f"reports/research_daemon/{objective_id}/daemon_checkpoint.json",
        {
            "objective_id": objective_id,
            "current_state": "READY",
            "budget_view": {"objective_id": objective_id, "total": 6, "used": 0, "remaining": 6, "reserved": 0},
        },
    )
    report = ObjectiveReconciliationServiceV1(tmp_path).reconcile(objective_id)
    assert report["conflict_level"] == PROJECTION_DRIFT
    assert report["budget"]["canonical"]["used"] == 2
    assert "PROJECTION_BUDGET_COUNTER_DRIFT" in report["conflicts"]


def test_matrix_f_two_budget_sources_without_immutable_reference_fail_closed(tmp_path: Path) -> None:
    objective_id = "OBJ_F"
    _objective(tmp_path, objective_id)
    _budget(tmp_path, objective_id, "B01", used=1, limit=6)
    _budget(tmp_path, objective_id, "B02", used=2, limit=6)
    report = ObjectiveReconciliationServiceV1(tmp_path).reconcile(objective_id)
    assert report["budget"]["authority_status"] == "AMBIGUOUS"
    assert report["effective_state"] == BUDGET_AUTHORITY_AMBIGUOUS
    assert "BUDGET_AUTHORITY_AMBIGUOUS" in report["conflicts"]
    assert report["safe_to_resume"] is False


def test_matrix_g_terminal_trial_wins_over_running_daemon_projection(tmp_path: Path) -> None:
    objective_id = "OBJ_G"
    _objective(tmp_path, objective_id)
    _budget(tmp_path, objective_id, "B01", used=1, limit=2)
    budget_path = tmp_path / "data/research/research_factory/batches/B01/search_budget_registry.json"
    budget = json.loads(budget_path.read_text(encoding="utf-8"))
    budget["settled_reservations"] = {"R1": "CONSUMED"}
    budget_path.write_text(json.dumps(budget, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _trial_ledger(tmp_path, objective_id)
    _write_json(
        tmp_path,
        f"reports/research_daemon/{objective_id}/daemon_checkpoint.json",
        {"objective_id": objective_id, "current_state": "PREDICTIVE_RUNNING", "current_trial_id": "T1"},
    )
    report = ObjectiveReconciliationServiceV1(tmp_path).reconcile(objective_id)
    assert report["effective_state"] == "TRIAL_TERMINAL"
    assert report["trial_reconciliation"]["trials"][0]["terminal_status"] == "COMPLETED"
    assert report["conflict_level"] == PROJECTION_DRIFT
    assert "PROJECTION_ACTIVE_AFTER_CANONICAL_TRIAL_TERMINAL" in report["conflicts"]


def test_matrix_h_missing_artifact_graph_edge_is_repairable_index_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    objective_id = "OBJ_H"
    _objective(tmp_path, objective_id)
    _candidate_registry(tmp_path, objective_id, "H1")
    _contract_registry(tmp_path, objective_id, "H1")
    _graph(tmp_path, objective_id, contract_hash="H1", with_edge=False)
    monkeypatch.setattr(reconciliation, "DurableFrozenCandidateContractV1", _PassingContract)
    report = ObjectiveReconciliationServiceV1(tmp_path).reconcile(objective_id)
    assert report["conflict_level"] == REPAIRABLE_INDEX_DRIFT
    assert "ARTIFACT_GRAPH_MISSING_EDGE" in report["conflicts"]
    assert report["safe_to_resume"] is True


def test_matrix_i_artifact_graph_identity_hash_conflict_is_canonical_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    objective_id = "OBJ_I"
    _objective(tmp_path, objective_id)
    _candidate_registry(tmp_path, objective_id, "H1")
    _contract_registry(tmp_path, objective_id, "H1")
    _graph(tmp_path, objective_id, contract_hash="H2")
    monkeypatch.setattr(reconciliation, "DurableFrozenCandidateContractV1", _PassingContract)
    report = ObjectiveReconciliationServiceV1(tmp_path).reconcile(objective_id)
    assert report["conflict_level"] == CANONICAL_CONFLICT
    assert "CANONICAL_ARTIFACT_GRAPH_IDENTITY_CONFLICT" in report["conflicts"]
    assert report["safe_to_resume"] is False


def test_matrix_j_exhausted_short_horizon_cannot_be_effective_active(tmp_path: Path) -> None:
    objective_id = "OBJ_J"
    _objective(tmp_path, objective_id)
    _budget(tmp_path, objective_id, "B01", used=2, limit=2, remaining=0)
    _write_json(
        tmp_path,
        f"reports/research_daemon/{objective_id}/daemon_checkpoint.json",
        {
            "objective_id": objective_id,
            "current_state": "ACTIVE",
            "budget_view": {"objective_id": objective_id, "total": 2, "used": 0, "remaining": 2, "reserved": 0},
        },
    )
    report = ObjectiveReconciliationServiceV1(tmp_path).reconcile(objective_id)
    assert report["effective_state"] == BUDGET_EXHAUSTED
    assert report["effective_state"] != "ACTIVE"
    assert report["budget"]["canonical"]["remaining"] == 0
    assert report["safe_to_advance"] is False
    assert "PROJECTION_ACTIVE_AFTER_CANONICAL_BUDGET_EXHAUSTED" in report["conflicts"]


def test_service_is_read_only_and_cli_writes_only_reconciliation_reports(tmp_path: Path) -> None:
    objective_id = "OBJ_READ_ONLY"
    _objective(tmp_path, objective_id)
    source_path = tmp_path / "data/research/research_factory/objectives" / f"{objective_id}.json"
    before = source_path.read_bytes()
    report = ObjectiveReconciliationServiceV1(tmp_path).reconcile(objective_id)
    assert report["read_only"] is True
    assert source_path.read_bytes() == before
    service = ObjectiveReconciliationServiceV1(tmp_path)
    service.write_report(report)
    output = tmp_path / "reports/research_reconciliation" / objective_id
    assert (output / "OBJECTIVE_RECONCILIATION_V1.json").exists()
    assert (output / "OBJECTIVE_RECONCILIATION_V1.md").exists()
    assert source_path.read_bytes() == before

