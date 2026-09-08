import hashlib
import json
from pathlib import Path

import pytest

from chanlun_trader.research_daemon import CanonicalResearchRuntime, ResearchDaemon, StructuralResult
from chanlun_trader.research_factory.autonomous_orchestrator_v2 import AutonomousResearchOrchestratorV2, CanonicalOrchestratorRuntimeV2
from chanlun_trader.research_factory.objective_reconciliation import ObjectiveReconciliationServiceV1
from chanlun_trader.research_factory.projection_reconciliation import ProjectionReconciliationServiceV1
from chanlun_trader.research_factory.structural_entry import (
    STRUCTURAL_IDEMPOTENCY_CONFLICT,
    StructuralEntryError,
    StructuralEntryServiceV1,
)
from chanlun_trader.research_factory.candidate_executable_materialization import CandidateExecutableMaterializationManagerV1
from chanlun_trader.research_console import ResearchConsoleReadService

from test_candidate_executable_materialization_v1 import OBJECTIVE_ID, _bridge_fixture


def _ready_fixture(tmp_path: Path):
    root, proposal, contract = _bridge_fixture(tmp_path)
    manager = CandidateExecutableMaterializationManagerV1(root)
    preview = manager.create_preview(OBJECTIVE_ID, proposal["proposal_id"])
    manager.confirm(
        OBJECTIVE_ID,
        proposal["proposal_id"],
        {
            "confirmed": True,
            "reviewer": "structural-boundary-reviewer",
            "preview_hash": preview["preview_hash"],
            "idempotency_key": "STRUCTURAL_BOUNDARY_FIXTURE",
        },
    )
    return root, contract


def _runtime(root: Path, status: str = "PASS"):
    runtime = CanonicalResearchRuntime(root, objective_id=OBJECTIVE_ID)
    calls: list[str] = []
    details = {
        "v1_result": {
            "lower_bound_count": 1,
            "upper_bound_count": 1,
            "minimum_required_count": 1,
            "outcome_blind": True,
            "performance_data_loaded": False,
        },
        "lower_bound_integrity": {"status": "PASS", "failure_codes": []},
    }
    result = StructuralResult(
        status,
        "SYNTHETIC_STRUCTURAL_RESULT",
        ("reports/structural/provider-evidence.json",),
        "STRUCTURAL_PROVIDER_CHECKPOINT",
        0,
        details,
    )

    def provider(candidate):
        calls.append(candidate.candidate_id)
        return result

    runtime.structural_preflight = provider
    return runtime, calls


def _budget_path(root: Path) -> Path:
    return next((root / "data" / "research" / "research_factory" / "batches").glob("*/search_budget_registry.json"))


def _canonical_path(root: Path) -> Path:
    return root / "reports" / "research_daemon" / OBJECTIVE_ID / "structural_preflight_reconciliation_canonical_v1.json"


def test_missing_durable_contract_blocks_structural_start(tmp_path: Path) -> None:
    root = _bridge_fixture(tmp_path)[0]
    service = StructuralEntryServiceV1(root)

    readiness = service.readiness(OBJECTIVE_ID)

    assert readiness["available"] is False
    assert readiness["reason_code"] == "DURABLE_FROZEN_CANDIDATE_REQUIRED"
    with pytest.raises(StructuralEntryError) as exc_info:
        service.start(OBJECTIVE_ID, confirmed=True)
    assert exc_info.value.code == "DURABLE_FROZEN_CANDIDATE_REQUIRED"


def test_ready_is_eligibility_only_and_daemon_does_not_auto_run(tmp_path: Path) -> None:
    root, contract = _ready_fixture(tmp_path)
    runtime, calls = _runtime(root)
    budget_before = _budget_path(root).read_bytes()

    readiness = StructuralEntryServiceV1(root, runtime=runtime).readiness(OBJECTIVE_ID)
    status = ResearchDaemon(root, objective_id=OBJECTIVE_ID, runtime=runtime, sleep_seconds=0).run_once()

    assert readiness["available"] is True
    assert readiness["candidate_id"] == contract.candidate_id
    assert status["daemon_state"] == "READY"
    assert status["required_human_ai_action"] == "RUN_STRUCTURAL_PREFLIGHT"
    assert calls == []
    assert _budget_path(root).read_bytes() == budget_before
    assert not _canonical_path(root).exists()


def test_structural_start_is_explicit_and_preserves_predictive_budget_and_trials(tmp_path: Path) -> None:
    root, contract = _ready_fixture(tmp_path)
    runtime, calls = _runtime(root, "PASS")
    budget_path = _budget_path(root)
    budget_before = budget_path.read_bytes()
    service = StructuralEntryServiceV1(root, runtime=runtime)

    with pytest.raises(StructuralEntryError) as exc_info:
        service.start(OBJECTIVE_ID, confirmed=False)
    assert exc_info.value.code == "STRUCTURAL_START_CONFIRMATION_REQUIRED"

    result = service.start(OBJECTIVE_ID, candidate_id=contract.candidate_id, confirmed=True, action="RUN_STRUCTURAL_PREFLIGHT")
    report = json.loads(_canonical_path(root).read_text(encoding="utf-8"))
    reconciled = ObjectiveReconciliationServiceV1(root).reconcile(OBJECTIVE_ID)

    assert result["status"] == "PASS"
    assert result["effective_state"] == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
    assert result["required_action"] == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
    assert result["predictive_authorized"] is False
    assert result["predictive_run_started"] is False
    assert len(calls) == 1
    assert budget_path.read_bytes() == budget_before
    assert report["authority_role"] == "STRUCTURAL_RESULT_AUTHORITY"
    assert report["safe_to_advance"] is False
    assert report["predictive_authorized"] is False
    assert report["safety_evidence"]["new_predictive_trials"] == 0
    assert report["safety_evidence"]["performance_data_loaded"] is False
    assert report["structural_identity"]["candidate_id"] == contract.candidate_id
    assert report["structural_identity"]["candidate_hash"] == contract.candidate_hash
    assert report["structural_identity"]["durable_contract_hash"] == contract.content_hash
    assert reconciled["effective_state"] == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
    assert reconciled["required_action"] == "AUTHORIZE_PREDICTIVE_TRIAL"
    assert reconciled["safe_to_advance"] is False
    assert reconciled["trial_reconciliation"]["terminal_trial_count"] == 0


def test_repeated_structural_start_is_exactly_once(tmp_path: Path) -> None:
    root, contract = _ready_fixture(tmp_path)
    runtime, calls = _runtime(root, "PASS")
    service = StructuralEntryServiceV1(root, runtime=runtime)

    first = service.start(OBJECTIVE_ID, candidate_id=contract.candidate_id, confirmed=True)
    second = service.start(OBJECTIVE_ID, candidate_id=contract.candidate_id, confirmed=True)

    assert first["provider_invoked"] is True
    assert second["idempotent"] is True
    assert second["provider_invoked"] is False
    assert len(calls) == 1


def test_restart_after_structural_pass_only_reprojects_and_never_restarts_provider(tmp_path: Path) -> None:
    root, contract = _ready_fixture(tmp_path)
    first_runtime, first_calls = _runtime(root, "PASS")
    StructuralEntryServiceV1(root, runtime=first_runtime).start(OBJECTIVE_ID, candidate_id=contract.candidate_id, confirmed=True)

    restarted_runtime, restarted_calls = _runtime(root, "PASS")
    status = ResearchDaemon(root, objective_id=OBJECTIVE_ID, runtime=restarted_runtime, sleep_seconds=0).run_once()

    assert first_calls == [contract.candidate_id]
    assert restarted_calls == []
    assert status["daemon_state"] == "STRUCTURAL_PASS"
    assert status["required_human_ai_action"] == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"


def test_console_uses_reconciled_state_when_daemon_projection_is_stale(tmp_path: Path) -> None:
    root, contract = _ready_fixture(tmp_path)
    runtime, _ = _runtime(root, "PASS")
    StructuralEntryServiceV1(root, runtime=runtime).start(OBJECTIVE_ID, candidate_id=contract.candidate_id, confirmed=True)
    checkpoint_path = root / "reports" / "research_daemon" / OBJECTIVE_ID / "daemon_checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["current_state"] = "STRUCTURAL_RUNNING"
    checkpoint["required_action"] = "STRUCTURAL_RUN_IN_PROGRESS"
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    pipeline = ResearchConsoleReadService(root).get_pipeline(OBJECTIVE_ID).to_dict()

    assert pipeline["current_state"] == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
    assert pipeline["current_stage"] == "PREDICTIVE"
    assert pipeline["next_action"] == "AUTHORIZE_PREDICTIVE_TRIAL"
    assert pipeline["state_source"] == "ObjectiveReconciliationServiceV1 / canonical Structural Result"
    assert pipeline["execution"]["status"] == "PREDICTIVE_NOT_RUN"


@pytest.mark.parametrize(
    ("provider_status", "effective_state", "required_action"),
    [
        ("UNKNOWN", "STRUCTURAL_BLOCKED", "RECONCILE_STRUCTURAL"),
        ("BLOCKED", "STRUCTURAL_BLOCKED", "RECONCILE_STRUCTURAL"),
        ("ENGINEERING_BLOCKED", "ENGINEERING_BLOCKED", "ENGINEERING_REVIEW_REQUIRED"),
    ],
)
def test_structural_non_pass_results_fail_closed(tmp_path: Path, provider_status: str, effective_state: str, required_action: str) -> None:
    root, contract = _ready_fixture(tmp_path)
    runtime, calls = _runtime(root, provider_status)

    result = StructuralEntryServiceV1(root, runtime=runtime).start(OBJECTIVE_ID, candidate_id=contract.candidate_id, confirmed=True)
    reconciled = ObjectiveReconciliationServiceV1(root).reconcile(OBJECTIVE_ID)

    assert result["effective_state"] == effective_state
    assert result["required_action"] == required_action
    assert reconciled["effective_state"] == effective_state
    assert reconciled["required_action"] == required_action
    assert reconciled["safe_to_advance"] is False
    assert len(calls) == 1


def test_terminal_structural_evidence_without_canonical_result_is_engineering_blocked(tmp_path: Path) -> None:
    root, contract = _ready_fixture(tmp_path)
    evidence_path = root / "reports" / "research_daemon" / OBJECTIVE_ID / "structural_execution_evidence.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": "structural-execution-evidence-v1",
                "authority_role": "STRUCTURAL_EXECUTION_EVIDENCE",
                "status": "COMPLETED",
                "objective_id": OBJECTIVE_ID,
                "candidate_id": contract.candidate_id,
                "candidate_hash": contract.candidate_hash,
                "durable_contract_hash": contract.content_hash,
                "result_status": "PASS",
                "result_hash": "TERMINAL_RESULT_WITHOUT_CANONICAL_FACT",
            }
        ),
        encoding="utf-8",
    )

    report = ObjectiveReconciliationServiceV1(root).reconcile(OBJECTIVE_ID)

    assert report["effective_state"] == "ENGINEERING_BLOCKED"
    assert report["required_action"] == "ENGINEERING_REVIEW_REQUIRED"
    assert report["structural_execution_evidence"]["terminal_unreconciled"] is True
    assert report["structural_result_reconciliation"]["present"] is False


def test_projection_repair_is_one_way_auditable_and_exactly_once(tmp_path: Path) -> None:
    root, contract = _ready_fixture(tmp_path)
    runtime, _ = _runtime(root, "PASS")
    StructuralEntryServiceV1(root, runtime=runtime).start(OBJECTIVE_ID, candidate_id=contract.candidate_id, confirmed=True)
    canonical_before = _canonical_path(root).read_bytes()
    objective_hash = hashlib.sha256((root / "data" / "research" / "research_factory" / "objectives" / f"{OBJECTIVE_ID}.json").read_bytes()).hexdigest()
    orchestrator_path = root / "reports" / "research_orchestrator_v2" / OBJECTIVE_ID / "orchestrator_checkpoint.json"
    orchestrator_path.parent.mkdir(parents=True, exist_ok=True)
    orchestrator_path.write_text(
        json.dumps({
            "schema_version": "research-orchestrator-checkpoint-v2",
            "objective_id": OBJECTIVE_ID,
            "objective_hash": objective_hash,
            "state": "LOCAL_RESEARCH_RUNNING",
            "orchestrator_state": "LOCAL_RESEARCH_RUNNING",
            "current_candidate": {"candidate_id": contract.candidate_id, "candidate_hash": contract.candidate_hash},
            "updated_at": "2026-09-06T00:00:00+00:00",
        }),
        encoding="utf-8",
    )

    service = ProjectionReconciliationServiceV1(root)
    repaired = service.reconcile(OBJECTIVE_ID, reason="TEST_PROJECTION_REPAIR")
    repeated = service.reconcile(OBJECTIVE_ID, reason="TEST_PROJECTION_REPAIR")
    receipt_path = root / "reports" / "research_reconciliation" / OBJECTIVE_ID / "projection_reconciliation_receipts.jsonl"

    assert repaired["status"] == "PASS"
    assert repaired["applied"] is True
    assert repaired["receipts"]
    assert repeated["status"] == "NOOP"
    assert repeated["receipts"] == []
    assert _canonical_path(root).read_bytes() == canonical_before
    updated_orchestrator = json.loads(orchestrator_path.read_text(encoding="utf-8"))
    assert updated_orchestrator["orchestrator_state"] == "ACTIVE"
    assert updated_orchestrator["required_action"] == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
    rows = [json.loads(line) for line in receipt_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == len({row["receipt_hash"] for row in rows})
    assert {"objective_id", "projection_type", "old_projection_hash", "canonical_effective_state_hash", "new_projection_hash", "reason", "timestamp", "receipt_hash"} <= set(rows[0])


def test_canonical_conflict_blocks_projection_repair(tmp_path: Path) -> None:
    root, contract = _ready_fixture(tmp_path)
    runtime, _ = _runtime(root, "PASS")
    StructuralEntryServiceV1(root, runtime=runtime).start(OBJECTIVE_ID, candidate_id=contract.candidate_id, confirmed=True)
    canonical_path = _canonical_path(root)
    payload = json.loads(canonical_path.read_text(encoding="utf-8"))
    payload["candidate_hash"] = "CONFLICTING_HASH"
    payload["structural_identity"]["candidate_hash"] = "CONFLICTING_HASH"
    canonical_path.write_text(json.dumps(payload), encoding="utf-8")

    result = ProjectionReconciliationServiceV1(root).reconcile(OBJECTIVE_ID)

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "CANONICAL_CONFLICT"
    assert result["human_action_required"] is True
    assert not (root / "reports" / "research_reconciliation" / OBJECTIVE_ID / "projection_reconciliation_receipts.jsonl").exists()


def test_structural_identity_conflict_is_fail_closed(tmp_path: Path) -> None:
    root, contract = _ready_fixture(tmp_path)
    runtime, _ = _runtime(root, "PASS")
    service = StructuralEntryServiceV1(root, runtime=runtime)
    service.start(OBJECTIVE_ID, candidate_id=contract.candidate_id, confirmed=True)
    report = json.loads(_canonical_path(root).read_text(encoding="utf-8"))
    report["canonical_result"]["details"]["identity_input"] = "CHANGED"
    report["canonical_result"]["details"]["provider_payload_identity"] = "CHANGED"
    _canonical_path(root).write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(StructuralEntryError) as exc_info:
        service.start(OBJECTIVE_ID, candidate_id=contract.candidate_id, confirmed=True)
    assert exc_info.value.code == STRUCTURAL_IDEMPOTENCY_CONFLICT


def test_orchestrator_status_projects_canonical_structural_pass_not_stale_checkpoint(tmp_path: Path) -> None:
    root, contract = _ready_fixture(tmp_path)
    runtime, _ = _runtime(root, "PASS")
    StructuralEntryServiceV1(root, runtime=runtime).start(OBJECTIVE_ID, candidate_id=contract.candidate_id, confirmed=True)
    checkpoint_path = root / "reports" / "research_orchestrator_v2" / OBJECTIVE_ID / "orchestrator_checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    objective_hash = hashlib.sha256((root / "data" / "research" / "research_factory" / "objectives" / f"{OBJECTIVE_ID}.json").read_bytes()).hexdigest()
    checkpoint_path.write_text(json.dumps({"objective_id": OBJECTIVE_ID, "objective_hash": objective_hash, "state": "LOCAL_RESEARCH_RUNNING"}), encoding="utf-8")

    status = AutonomousResearchOrchestratorV2(root, objective_id=OBJECTIVE_ID, runtime=CanonicalOrchestratorRuntimeV2(root, OBJECTIVE_ID)).status().to_dict()

    assert status["orchestrator_state"] == "ACTIVE"
    assert status["next_action"] == "AUTHORIZE_PREDICTIVE_TRIAL"
    assert status["waiting_for_governance"] is True


def test_cli_and_web_structural_entries_require_the_same_explicit_action(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import chanlun_trader.research_daemon as daemon_module
    import chanlun_trader.webapp as webapp
    application = webapp.create_app(tmp_path, webapp.ExecutionPolicy("GOVERNED", "SYNTHETIC", allow_structural=True))
    from fastapi.testclient import TestClient

    calls: list[dict] = []

    class FakeDaemon:
        def start_structural_preflight(self, **kwargs):
            calls.append({"source": "cli", **kwargs})
            return {"status": "PASS", "effective_state": "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"}

    class FakeEntryService:
        def start(self, objective_id, **kwargs):
            calls.append({"source": "web", "objective_id": objective_id, **kwargs})
            return {"status": "PASS", "effective_state": "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"}

    monkeypatch.setattr(daemon_module, "_daemon_for_cli", lambda _args: FakeDaemon())
    monkeypatch.setattr(application.state.services, "structural_entry_service", FakeEntryService())
    client = TestClient(application)

    missing_confirmation = client.post("/api/research-console/OBJECTIVE_X/structural/start", json={"action": "RUN_STRUCTURAL_PREFLIGHT"})
    legacy_missing_action = client.post("/api/research-console/OBJECTIVE_X/structural/reconcile", json={"confirmed": True})
    web_response = client.post("/api/research-console/OBJECTIVE_X/structural/start", json={"confirmed": True, "action": "RUN_STRUCTURAL_PREFLIGHT"})
    assert missing_confirmation.status_code == 400
    assert legacy_missing_action.status_code == 400
    assert web_response.status_code == 200

    assert daemon_module.main(["start-structural-preflight", "--root", ".", "--objective-id", "OBJECTIVE_X", "--confirmed", "--json"]) == 0
    assert [item["source"] for item in calls] == ["web", "cli"]
    assert all(item["action"] == "RUN_STRUCTURAL_PREFLIGHT" and item["confirmed"] is True for item in calls)
