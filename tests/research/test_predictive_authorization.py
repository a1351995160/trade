from __future__ import annotations

import json
from pathlib import Path

import pytest

from chanlun_trader.research_daemon import CandidateWork, CanonicalPlatformContext, ResearchDaemon, StructuralResult
from chanlun_trader.research_daemon_state import DaemonCheckpointStoreV1, DaemonCheckpointV1
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.predictive_authorization import (
    AUTHORIZE_FIRST_PREDICTIVE_TRIAL,
    DEFER_PREDICTIVE_TRIAL,
    END_CANDIDATE_RESEARCH_DIRECTION,
    PredictiveGovernanceError,
    PredictiveGovernanceServiceV1,
)


OBJECTIVE_ID = "OBJECTIVE_GOVERNANCE_FIX"
CANDIDATE_ID = "CANDIDATE_FROZEN_1"
CANDIDATE_HASH = "HASH_FROZEN_1"
RECONCILIATION_ID = "RECONCILIATION_1"


def _write(root: Path, relative: str, value: object) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _fixture(root: Path) -> tuple[PredictiveGovernanceServiceV1, DaemonCheckpointStoreV1, Path]:
    _write(
        root,
        f"data/research/research_factory/objectives/{OBJECTIVE_ID}.json",
        {
            "objective_id": OBJECTIVE_ID,
            "objective_identity_hash": "OBJECTIVE_HASH_1",
            "max_total_trials": 4,
            "risk_constraints": {"prospective_access": "DISABLED", "real_order_execution": "DISABLED"},
        },
    )
    budget_path = _write(
        root,
        f"data/research/research_factory/batches/{OBJECTIVE_ID}_B01/search_budget_registry.json",
        {"schema_version": "search-budget-registry-v1", "objective_id": OBJECTIVE_ID, "buckets": [], "active_reservations": {}, "settled_reservations": {}, "reservation_counter": 0},
    )
    registry = SearchBudgetRegistryV1(OBJECTIVE_ID, budget_path)
    registry.register_objective(4)
    registry.register_batch("B01", 4)
    registry.register_family("FAMILY_1", 4)
    registry.register_candidate(CANDIDATE_ID, 1)
    contract_ref = f"data/research/research_factory/batches/{OBJECTIVE_ID}_B01/durable_frozen_candidate_contracts.json"
    _write(root, contract_ref, {"contracts": [{"candidate_id": CANDIDATE_ID, "candidate_hash": CANDIDATE_HASH, "policy_identity": {"objective_id": OBJECTIVE_ID}}]})

    _write(
        root,
        f"reports/research_orchestrator_v2/{OBJECTIVE_ID}/structural_governance_decision_required.json",
        {
            "schema_version": "structural-governance-decision-required-v1",
            "objective_id": OBJECTIVE_ID,
            "decision_id": "STRUCTURAL_GOVERNANCE_1",
            "status": "PENDING_HUMAN_DECISION",
            "decision_mode": "STRUCTURAL_PASS_PREDICTIVE_AUTHORIZATION_REQUIRED",
            "candidate_id": CANDIDATE_ID,
            "candidate_hash": CANDIDATE_HASH,
            "reconciliation_id": RECONCILIATION_ID,
            "structural": {"status": "PASS", "lower_bound": 59, "upper_bound": 59, "minimum_required": 30, "lower_bound_integrity": "PASS"},
            "predictive_trials_created": 0,
            "performance_access": 0,
            "final_test_access": {"analytical": 0, "decision": 0, "physical": 0},
            "prospective": "DISABLED",
            "real_order": "DISABLED",
        },
    )
    store = DaemonCheckpointStoreV1(root, OBJECTIVE_ID)
    store.save(
        DaemonCheckpointV1(
            objective_id=OBJECTIVE_ID,
            current_state="READY",
            required_action="PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED",
            last_completed_candidate={"candidate_id": CANDIDATE_ID, "candidate_hash": CANDIDATE_HASH, "contract_ref": contract_ref, "frozen_state": "FROZEN"},
            canonical_refs={
                "last_structural_result": {
                    "status": "PASS",
                    "details": {
                        "v2_result": {"candidate_id": CANDIDATE_ID, "candidate_hash": CANDIDATE_HASH},
                        "lower_bound_integrity": {"status": "PASS", "failure_codes": []},
                    },
                },
                "structural_reconciliation": {"status": "PASS", "reconciliation_id": RECONCILIATION_ID},
            },
            budget_view={
                "registry_path": budget_path.relative_to(root).as_posix(),
                "total": 4,
                "used": 0,
                "reserved": 0,
                "remaining": 4,
            },
        )
    )
    return PredictiveGovernanceServiceV1(root, clock=lambda: "2026-09-02T10:00:00+00:00"), store, budget_path


def _confirm(service: PredictiveGovernanceServiceV1, decision_type: str, authorization_id: str) -> dict:
    preview = service.preview(OBJECTIVE_ID, decision_type)
    return service.confirm(
        OBJECTIVE_ID,
        {
            "confirmed": True,
            "decision_type": decision_type,
            "candidate_id": CANDIDATE_ID,
            "candidate_hash": CANDIDATE_HASH,
            "authorization_id": authorization_id,
            "preview_hash": preview["preview_hash"],
            "confirmation_token": preview["confirmation_token"],
        },
    )


def test_authorization_records_governance_only_and_is_exactly_once(tmp_path: Path) -> None:
    service, store, budget_path = _fixture(tmp_path)
    before_budget = budget_path.read_bytes()

    readiness = service.readiness(OBJECTIVE_ID)
    assert readiness["available"] is True
    assert readiness["candidate_hash"] == CANDIDATE_HASH
    assert len(readiness["choices"]) == 3
    assert all(item["creates_trial"] is False for item in readiness["choices"])

    preview = service.preview(OBJECTIVE_ID)
    assert preview["preview"]["trial_creation"] == "NOT_CREATED"
    assert preview["preview"]["budget_mutation"] == "NONE"
    report = service.confirm(
        OBJECTIVE_ID,
        {
            "confirmed": True,
            "decision_type": AUTHORIZE_FIRST_PREDICTIVE_TRIAL,
            "candidate_id": CANDIDATE_ID,
            "candidate_hash": CANDIDATE_HASH,
            "authorization_id": "AUTHORIZATION_1",
            "preview_hash": preview["preview_hash"],
            "confirmation_token": preview["confirmation_token"],
        },
    )

    assert report["status"] == "RECORDED"
    assert report["idempotent"] is False
    assert report["decision"]["decision_status"] == "AUTHORIZED"
    assert report["next_action"] == "START_PREDICTIVE_TRIAL_1"
    assert report["safety_boundary"]["trial_created"] is False
    assert report["safety_boundary"]["budget_used_delta"] == 0
    assert report["safety_boundary"]["performance_access"] == 0
    assert store.load().required_action == "START_PREDICTIVE_TRIAL_1"
    assert store.load().current_state == "READY"
    assert store.load().current_trial is None
    assert store.load().last_completed_candidate["candidate_hash"] == CANDIDATE_HASH
    assert budget_path.read_bytes() == before_budget
    ledger = tmp_path / "reports/research_orchestrator_v2" / OBJECTIVE_ID / "predictive_governance_decisions.jsonl"
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1
    assert not list((tmp_path / "data/research/research_factory/batches").glob("*/factory_trial_ledger.json"))

    repeated = service.confirm(
        OBJECTIVE_ID,
        {
            "confirmed": True,
            "decision_type": AUTHORIZE_FIRST_PREDICTIVE_TRIAL,
            "candidate_id": CANDIDATE_ID,
            "candidate_hash": CANDIDATE_HASH,
            "authorization_id": "AUTHORIZATION_1",
            "preview_hash": preview["preview_hash"],
            "confirmation_token": preview["confirmation_token"],
        },
    )
    assert repeated["idempotent"] is True
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1
    assert budget_path.read_bytes() == before_budget


def test_readiness_human_explanation_uses_current_structural_boundary(tmp_path: Path) -> None:
    service, _, _ = _fixture(tmp_path)
    governance_path = tmp_path / "reports/research_orchestrator_v2" / OBJECTIVE_ID / "structural_governance_decision_required.json"
    governance = json.loads(governance_path.read_text(encoding="utf-8"))
    governance["structural"] = {
        "status": "PASS",
        "lower_bound": 158,
        "upper_bound": 298,
        "minimum_required": 30,
        "lower_bound_integrity": "PASS",
    }
    governance_path.write_text(json.dumps(governance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    readiness = service.readiness(OBJECTIVE_ID)

    assert readiness["structural"]["lower_bound"] == 158
    assert readiness["structural"]["upper_bound"] == 298
    assert readiness["human_explanation_zh"] == "结构预检已通过，Candidate 身份与 158 / 298 / 30 样本边界保持冻结；预测试验仍需人工治理决定。"


def test_defer_keeps_entry_open_and_can_be_authorized_later(tmp_path: Path) -> None:
    service, store, budget_path = _fixture(tmp_path)
    before_budget = budget_path.read_bytes()

    deferred = _confirm(service, DEFER_PREDICTIVE_TRIAL, "AUTHORIZATION_DEFER_1")
    assert deferred["decision"]["decision_status"] == "DEFERRED"
    assert deferred["next_action"] == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
    assert store.load().required_action == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
    assert service.readiness(OBJECTIVE_ID)["status"] == "DEFERRED"
    assert budget_path.read_bytes() == before_budget

    authorized = _confirm(service, AUTHORIZE_FIRST_PREDICTIVE_TRIAL, "AUTHORIZATION_AFTER_DEFER_1")
    assert authorized["decision"]["decision_status"] == "AUTHORIZED"
    assert store.load().required_action == "START_PREDICTIVE_TRIAL_1"
    assert budget_path.read_bytes() == before_budget


def test_end_closes_candidate_path_without_performance_failure_or_trial(tmp_path: Path) -> None:
    service, store, budget_path = _fixture(tmp_path)
    before_budget = budget_path.read_bytes()

    ended = _confirm(service, END_CANDIDATE_RESEARCH_DIRECTION, "AUTHORIZATION_END_1")
    checkpoint = store.load()
    assert ended["decision"]["decision_status"] == "ENDED"
    assert ended["next_action"] == "CANDIDATE_RESEARCH_DIRECTION_ENDED"
    assert checkpoint.required_action == "CANDIDATE_RESEARCH_DIRECTION_ENDED"
    assert checkpoint.last_completed_candidate["candidate_id"] == CANDIDATE_ID
    assert checkpoint.current_trial is None
    assert budget_path.read_bytes() == before_budget
    assert not list((tmp_path / "data/research/research_factory/batches").glob("*/factory_trial_ledger.json"))


def test_stale_preview_is_rejected_without_writing_decision(tmp_path: Path) -> None:
    service, _, budget_path = _fixture(tmp_path)
    preview = service.preview(OBJECTIVE_ID)
    budget = json.loads(budget_path.read_text(encoding="utf-8"))
    objective_bucket = next(item for item in budget["buckets"] if item["kind"] == "objective")
    objective_bucket["used"] = 1
    objective_bucket["remaining"] = 3
    budget_path.write_text(json.dumps(budget, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(PredictiveGovernanceError) as error:
        service.confirm(
            OBJECTIVE_ID,
            {
                "confirmed": True,
                "decision_type": AUTHORIZE_FIRST_PREDICTIVE_TRIAL,
                "candidate_id": CANDIDATE_ID,
                "candidate_hash": CANDIDATE_HASH,
                "authorization_id": "AUTHORIZATION_STALE_1",
                "preview_hash": preview["preview_hash"],
                "confirmation_token": preview["confirmation_token"],
            },
        )
    assert error.value.code == "STALE_PREDICTIVE_GOVERNANCE_PREVIEW"
    ledger = tmp_path / "reports/research_orchestrator_v2" / OBJECTIVE_ID / "predictive_governance_decisions.jsonl"
    assert not ledger.exists()


class _NoPredictiveRuntime:
    def __init__(self) -> None:
        self.next_candidate_called = False

    def load_context(self) -> CanonicalPlatformContext:
        return CanonicalPlatformContext(OBJECTIVE_ID, "OBJECTIVE_HASH_1", {"used": 0, "reserved": 0, "remaining": 4, "total": 4}, "capability.json", "architecture.json", (), ())

    def summary(self) -> dict:
        return {"budget": {"used": 0, "reserved": 0, "remaining": 4, "total": 4}, "remaining_frozen_candidates": 0}

    def next_candidate(self):
        self.next_candidate_called = True
        raise AssertionError("restart must not enter candidate selection at the governance boundary")


class _FreshStructuralPassRuntime(_NoPredictiveRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.candidate = CandidateWork(CANDIDATE_ID, CANDIDATE_HASH, "contracts.json", batch_id="B01", mechanism="EVENT")
        self.predictive_called = False

    def load_context(self) -> CanonicalPlatformContext:
        return CanonicalPlatformContext(OBJECTIVE_ID, "OBJECTIVE_HASH_1", {"used": 0, "reserved": 0, "remaining": 4, "total": 4}, "capability.json", "architecture.json", ("contracts.json",), ())

    def next_candidate(self):
        return self.candidate

    def structural_preflight(self, candidate: CandidateWork) -> StructuralResult:
        return StructuralResult(
            "PASS",
            "STRUCTURAL_PASS",
            details={
                "v1_result": {"candidate_id": candidate.candidate_id, "candidate_hash": candidate.candidate_hash, "lower_bound_count": 59, "upper_bound_count": 59, "minimum_required_count": 30, "outcome_blind": True, "performance_data_loaded": False},
                "lower_bound_integrity": {"status": "PASS", "failure_codes": []},
            },
        )

    def predictive_validate(self, candidate: CandidateWork):
        self.predictive_called = True
        raise AssertionError("structural PASS must not enter predictive execution automatically")

    def mark_candidate_complete(self, candidate: CandidateWork, *, result) -> None:
        raise AssertionError("structural PASS boundary must wait for governance before completion")


def test_fresh_structural_pass_stops_before_predictive_execution(tmp_path: Path) -> None:
    runtime = _FreshStructuralPassRuntime()
    _write(
        tmp_path,
        f"reports/research_orchestrator_v2/{OBJECTIVE_ID}/structural_governance_decision_required.json",
        {"objective_id": OBJECTIVE_ID, "decision_mode": "STRUCTURAL_PASS_PREDICTIVE_AUTHORIZATION_REQUIRED"},
    )

    daemon = ResearchDaemon(tmp_path, objective_id=OBJECTIVE_ID, runtime=runtime, sleep_seconds=0)
    daemon.monitor.snapshot = lambda **_: {"memory_pressure": False}
    status = daemon.run_once()

    assert runtime.predictive_called is False
    assert status["required_human_ai_action"] == "RUN_STRUCTURAL_PREFLIGHT"
    checkpoint = DaemonCheckpointStoreV1(tmp_path, OBJECTIVE_ID).load()
    assert checkpoint.current_state == "READY"
    assert checkpoint.current_candidate is None
    assert checkpoint.last_completed_candidate is None
    assert checkpoint.current_trial is None


def test_restart_observes_authorized_boundary_without_starting_trial(tmp_path: Path) -> None:
    service, store, _ = _fixture(tmp_path)
    _confirm(service, AUTHORIZE_FIRST_PREDICTIVE_TRIAL, "AUTHORIZATION_RESTART_1")
    runtime = _NoPredictiveRuntime()

    status = ResearchDaemon(tmp_path, objective_id=OBJECTIVE_ID, runtime=runtime, sleep_seconds=0).run_once()

    assert runtime.next_candidate_called is False
    assert status["required_human_ai_action"] == "START_PREDICTIVE_TRIAL_1"
    assert store.load().current_trial is None


def test_fastapi_authorization_api_records_without_running_trial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    service, store, budget_path = _fixture(tmp_path)
    import chanlun_trader.webapp as webapp
    application = webapp.create_app(tmp_path, webapp.ExecutionPolicy("GOVERNED", "SYNTHETIC"))

    monkeypatch.setattr(application.state.services, "predictive_governance_service", service)
    before_budget = budget_path.read_bytes()
    with TestClient(application) as client:
        preview_response = client.get(f"/api/research-console/{OBJECTIVE_ID}/predictive/authorization/preview")
        assert preview_response.status_code == 200
        preview = preview_response.json()
        response = client.post(
            f"/api/research-console/{OBJECTIVE_ID}/predictive/authorize",
            json={
                "confirmed": True,
                "decision_type": AUTHORIZE_FIRST_PREDICTIVE_TRIAL,
                "candidate_id": CANDIDATE_ID,
                "candidate_hash": CANDIDATE_HASH,
                "authorization_id": "AUTHORIZATION_API_1",
                "preview_hash": preview["preview_hash"],
                "confirmation_token": preview["confirmation_token"],
            },
        )

    assert response.status_code == 200
    assert response.json()["decision"]["decision_status"] == "AUTHORIZED"
    assert response.json()["next_action"] == "START_PREDICTIVE_TRIAL_1"
    assert store.load().current_trial is None
    assert budget_path.read_bytes() == before_budget


def test_legacy_governance_action_reaches_structural_predictive_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    service, store, budget_path = _fixture(tmp_path)
    import chanlun_trader.webapp as webapp
    application = webapp.create_app(tmp_path, webapp.ExecutionPolicy("GOVERNED", "SYNTHETIC"))
    from chanlun_trader.research_factory.autonomous_orchestrator_v2 import GovernanceDecisionServiceV1

    monkeypatch.setattr(application.state.services, "governance_decision_service", GovernanceDecisionServiceV1(tmp_path))
    before_budget = budget_path.read_bytes()
    with TestClient(application) as client:
        response = client.post(
            f"/api/research-console/{OBJECTIVE_ID}/governance-decision",
            json={"confirmed": True, "choice": AUTHORIZE_FIRST_PREDICTIVE_TRIAL, "idempotency_key": "LEGACY_GOVERNANCE_1"},
        )

    assert response.status_code == 200
    assert response.json()["decision"]["decision_status"] == "AUTHORIZED"
    assert response.json()["next_action"] == "START_PREDICTIVE_TRIAL_1"
    assert store.load().current_trial is None
    assert budget_path.read_bytes() == before_budget
