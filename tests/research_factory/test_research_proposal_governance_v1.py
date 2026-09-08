import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.research_evolution_proposal import (
    APPROVED,
    CLOSED,
    CREATED,
    CREATE_OBJECTIVE,
    EVOLUTION_ANALYZED,
    HUMAN_REVIEW_REQUIRED,
    OBJECTIVE_CREATION_READY,
    PROPOSAL_CREATED,
    READY_FOR_CONFIRMATION,
    REJECTED,
)
from chanlun_trader.research_factory.research_proposal_governance import (
    ResearchProposalGovernanceError,
    ResearchProposalGovernanceServiceV1,
)


OBJECTIVE_ID = "OBJECTIVE_PROPOSAL_GOVERNANCE_TEST_V1"
PROPOSAL_ID = "RESEARCH_EVOLUTION_PROPOSAL_OBJECTIVE_PROPOSAL_GOVERNANCE_TEST_V1"
PARENT_BATCH_ID = f"{OBJECTIVE_ID}_B01"
PARENT_FAMILY_ID = "PARENT_FAMILY_V1"
FIXED_NOW = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)


def _write_json(root: Path, relative_path: str, payload: dict) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _parent_objective() -> dict:
    return {
        "schema_version": "research-objective-v1",
        "objective_id": OBJECTIVE_ID,
        "objective_name": "父研究目标",
        "research_universe": ["SH", "SZ"],
        "capital_reference": 10000.0,
        "holding_horizon": [2, 10],
        "preferred_horizon": [5, 8],
        "mechanism_scope": ["cross_sectional", "event_reversal"],
        "allowed_factor_scope": [],
        "risk_constraints": {
            "pit_required": True,
            "no_lookahead": True,
            "t_plus_1": True,
            "price_limit_fail_closed": True,
            "suspension_fail_closed": True,
            "final_test_access": "DISABLED",
            "prospective_access": "DISABLED",
            "recommendation": "DISABLED",
            "real_order_execution": "DISABLED",
        },
        "research_priority": [
            "PIT_NO_LOOKAHEAD",
            "ANTI_OVERFIT",
            "ROBUSTNESS",
            "RISK_ADJUSTED_AND_ABSOLUTE_RETURN",
            "WIN_RATE",
        ],
        "max_batches": 5,
        "max_total_trials": 8,
        "seed": 20260824,
        "governance_policy_hash": "parent-policy-hash",
        "lifecycle_state": "ACTIVE",
    }


def _proposal() -> dict:
    payload = {
        "schema_version": "research-evolution-proposal-v1",
        "proposal_type": "RESEARCH_EVOLUTION_PROPOSAL",
        "proposal_id": PROPOSAL_ID,
        "parent_objective_id": OBJECTIVE_ID,
        "parent_candidate_id": "PARENT_CANDIDATE_V1",
        "parent_trial_id": "PARENT_TRIAL_V1",
        "source_failure_report": "reports/research_evolution/PARENT_FAILURE_REPORT.json",
        "source_failure_report_hash": "failure-report-hash",
        "failed_mechanism": "AMOUNT_ACCEL+ILLIQUIDITY",
        "failed_mechanism_family": "liquidity_acceleration",
        "failure_summary": ["STATISTICAL_FAILURE", "RISK_FAILURE"],
        "avoid_mechanism_family": ["liquidity_acceleration"],
        "suggested_research_directions": ["event_driven", "volatility_structure", "capital_flow"],
        "mechanism_coverage": {
            "coverage_id": "MECHANISM_COVERAGE_REGISTRY_V1",
            "covered": ["daily_cross_sectional", "liquidity_acceleration"],
            "unexplored": ["event_driven", "volatility_structure", "capital_flow"],
            "outcome_blind": True,
        },
        "lineage": {
            "objective_id": OBJECTIVE_ID,
            "candidate_id": "PARENT_CANDIDATE_V1",
            "trial_id": "PARENT_TRIAL_V1",
            "candidate_hash": "parent-candidate-hash",
            "trial_hash": "parent-trial-hash",
            "objective_hash": "parent-objective-hash",
        },
        "outcome_blind": True,
        "human_approval_required": True,
        "governance_state": HUMAN_REVIEW_REQUIRED,
        "state_history": [EVOLUTION_ANALYZED, PROPOSAL_CREATED, CREATED, HUMAN_REVIEW_REQUIRED],
        "governance": {
            "current_state": HUMAN_REVIEW_REQUIRED,
            "human_approval_required": True,
            "automatic_candidate_created": False,
            "automatic_objective_created": False,
            "automatic_trial_started": False,
            "budget_consumed": False,
            "codex_called": False,
        },
    }
    payload["proposal_hash"] = stable_hash(payload)
    return payload


def _fixture(tmp_path: Path, *, crash_at: str | None = None) -> dict[str, object]:
    objective_path = _write_json(
        tmp_path,
        f"data/research/research_factory/objectives/{OBJECTIVE_ID}.json",
        _parent_objective(),
    )
    proposal_path = _write_json(
        tmp_path,
        "reports/research_evolution/proposals/RESEARCH_EVOLUTION_PROPOSAL.json",
        _proposal(),
    )
    failure_path = _write_json(
        tmp_path,
        "reports/research_evolution/PARENT_FAILURE_REPORT.json",
        {"report_id": "PARENT_FAILURE_REPORT", "lineage": {"objective_id": OBJECTIVE_ID}},
    )
    parent_budget_path = tmp_path / f"data/research/research_factory/batches/{PARENT_BATCH_ID}/search_budget_registry.json"
    parent_budget = SearchBudgetRegistryV1(OBJECTIVE_ID, parent_budget_path)
    parent_budget.register_objective(8)
    parent_budget.register_batch(PARENT_BATCH_ID, 8)
    parent_budget.register_family(PARENT_FAMILY_ID, 8)
    service = ResearchProposalGovernanceServiceV1(
        tmp_path,
        clock=lambda: FIXED_NOW,
        crash_at=crash_at,
    )
    return {
        "service": service,
        "proposal_path": proposal_path,
        "objective_path": objective_path,
        "failure_path": failure_path,
        "parent_budget_path": parent_budget_path,
    }


def _approve(service: ResearchProposalGovernanceServiceV1) -> dict:
    return service.review(PROPOSAL_ID, {"action": "approve", "reviewer": "alice"})


def _confirm_payload(preview: dict, *, confirmer: str = "alice") -> dict:
    return {
        "confirmed": True,
        "action": CREATE_OBJECTIVE,
        "confirmer": confirmer,
        "proposal_hash": preview["proposal_hash"],
        "preview_hash": preview["preview_hash"],
        "confirmation_token": preview["confirmation_token"],
        "idempotency_key": "CONFIRM_PROPOSAL_GOVERNANCE_TEST_V1",
    }


def test_proposal_read_and_status_flow_are_human_governed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    proposal_path = fixture["proposal_path"]
    before = proposal_path.read_bytes()

    initial = service.get_proposal(PROPOSAL_ID)
    assert initial["governance_state"] == HUMAN_REVIEW_REQUIRED
    assert initial["state_history"][-2:] == [CREATED, HUMAN_REVIEW_REQUIRED]
    assert initial["governance"]["next_allowed_states"] == ["APPROVED", "REJECTED"]
    assert initial["preview_available"] is False
    assert proposal_path.read_bytes() == before

    pending = service.list_proposals(objective_id=OBJECTIVE_ID, status="PENDING")
    assert pending["total"] == 1
    assert pending["items"][0]["status"] == HUMAN_REVIEW_REQUIRED

    approved = _approve(service)
    review = approved["review"]
    assert {"review_id", "reviewer", "timestamp", "proposal_hash"}.issubset(review)
    assert review["action"] == "approve"
    assert review["resulting_state"] == APPROVED
    assert approved["proposal"]["governance_state"] == OBJECTIVE_CREATION_READY
    assert approved["proposal"]["state_history"][-2:] == [APPROVED, OBJECTIVE_CREATION_READY]
    assert approved["objective_creation_preview"]["status"] == READY_FOR_CONFIRMATION
    approved_list = service.list_proposals(objective_id=OBJECTIVE_ID, status="APPROVED")
    assert approved_list["total"] == 1
    assert approved_list["items"][0]["status"] == OBJECTIVE_CREATION_READY


def test_approve_never_creates_objective_candidate_trial_or_ai_artifact(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    approved = _approve(fixture["service"])
    preview = approved["objective_creation_preview"]

    target_id = preview["target_objective_id"]
    assert not (tmp_path / f"data/research/research_factory/objectives/{target_id}.json").exists()
    assert not (tmp_path / "data/research/research_factory/candidates").exists()
    assert not (tmp_path / "reports/research_daemon").exists()
    assert preview["automatic_objective_created"] is False
    assert preview["automatic_candidate_created"] is False
    assert preview["automatic_trial_started"] is False
    assert preview["ai_called"] is False


def test_reject_preserves_research_history_and_closes_without_downstream_change(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    proposal_path = fixture["proposal_path"]
    failure_path = fixture["failure_path"]
    objective_path = fixture["objective_path"]
    before_proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    before_failure = failure_path.read_bytes()
    before_objective = objective_path.read_bytes()

    rejected = service.review(PROPOSAL_ID, {"action": "reject", "reviewer": "alice", "reason": "机制边界暂不满足"})
    assert rejected["proposal"]["governance_state"] == REJECTED
    assert rejected["proposal"]["state_history"][:4] == before_proposal["state_history"]
    assert rejected["objective_creation_preview"] is None
    assert failure_path.read_bytes() == before_failure
    assert objective_path.read_bytes() == before_objective
    assert not (tmp_path / "data/research/research_factory/candidates").exists()

    closed = service.close(PROPOSAL_ID, reviewer="alice")
    assert closed["governance_state"] == CLOSED
    assert closed["state_history"][-2:] == [REJECTED, CLOSED]
    rejected_list = service.list_proposals(objective_id=OBJECTIVE_ID, status="REJECTED")
    assert rejected_list["total"] == 1
    assert rejected_list["items"][0]["status"] == CLOSED


def test_objective_preview_is_ready_for_confirmation_without_budget_consumption(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    parent_budget_path = fixture["parent_budget_path"]
    before_budget = parent_budget_path.read_bytes()

    _approve(service)
    preview = service.get_objective_creation_preview(PROPOSAL_ID)
    assert preview["status"] == READY_FOR_CONFIRMATION
    assert preview["requires_human_confirmation"] is True
    assert preview["can_create_objective"] is False
    assert preview["budget_consumed"] is False
    assert preview["budget_suggestion"]["reservation_created"] is False
    assert preview["parent_proposal"]["proposal_id"] == PROPOSAL_ID
    assert preview["parent_lineage"]
    assert preview["research_direction"] in {"event_driven", "volatility_structure", "capital_flow"}
    assert preview["multiple_testing_family_suggestion"]["family_id"]
    assert not (tmp_path / f"data/research/research_factory/batches/{preview['target_objective_id']}_B01").exists()
    assert parent_budget_path.read_bytes() == before_budget


def test_preview_get_is_read_only_when_preview_artifact_is_missing(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    _approve(service)
    preview_path = tmp_path / "reports/research_evolution/proposals/governance" / PROPOSAL_ID / "objective_creation_preview.json"
    assert preview_path.exists()
    preview_path.unlink()
    before_proposal = fixture["proposal_path"].read_bytes()

    with pytest.raises(ResearchProposalGovernanceError) as error:
        service.get_objective_creation_preview(PROPOSAL_ID)

    assert error.value.code == "OBJECTIVE_PREVIEW_NOT_FOUND"
    assert not preview_path.exists()
    assert fixture["proposal_path"].read_bytes() == before_proposal


def test_proposal_content_tampering_is_rejected_before_downstream_creation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    _approve(service)
    proposal_path = fixture["proposal_path"]
    tampered = json.loads(proposal_path.read_text(encoding="utf-8"))
    tampered["suggested_research_directions"] = ["tampered_direction"]
    proposal_path.write_text(json.dumps(tampered, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ResearchProposalGovernanceError) as error:
        service.get_proposal(PROPOSAL_ID)

    assert error.value.code == "PROPOSAL_HASH_INVALID"
    assert not list((tmp_path / "data/research/research_factory/objectives").glob("RESEARCH_EVOLUTION_PROPOSAL_*.json"))


def test_only_final_confirm_creates_objective_lineage_budget_and_governance_record(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    _approve(service)
    preview = service.get_objective_creation_preview(PROPOSAL_ID)
    receipt = service.confirm(PROPOSAL_ID, _confirm_payload(preview))

    target_id = preview["target_objective_id"]
    objective_path = tmp_path / f"data/research/research_factory/objectives/{target_id}.json"
    budget_path = tmp_path / f"data/research/research_factory/batches/{target_id}_B01/search_budget_registry.json"
    family_id = preview["multiple_testing_family_suggestion"]["family_id"]
    family_path = tmp_path / f"data/research/research_factory/multiple_testing/{target_id}/{family_id}.json"
    lineage_path = tmp_path / f"data/research/research_factory/lineage/{target_id}.json"
    governance_path = tmp_path / f"reports/research_evolution/proposals/governance/{PROPOSAL_ID}/objective_creation_governance.json"

    assert receipt["result_state"] == "OBJECTIVE_CREATED"
    assert receipt["immutable_objective_id"] is True
    assert receipt["approval_review_id"] == json.loads(
        (tmp_path / "reports/research_evolution/proposals/governance" / PROPOSAL_ID / "reviews.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )["review_id"]
    assert receipt["confirmation_id"].startswith("CONFIRMATION_")
    assert receipt["confirmation_id"] != receipt["approval_review_id"]
    assert receipt["confirmer"] == "alice"
    assert objective_path.exists()
    assert budget_path.exists()
    assert family_path.exists()
    assert lineage_path.exists()
    assert governance_path.exists()
    updated_proposal = json.loads(fixture["proposal_path"].read_text(encoding="utf-8"))
    assert updated_proposal["objective_created"] is True
    assert updated_proposal["created_objective_id"] == target_id
    assert not (tmp_path / "RESEARCH_EVOLUTION_PROPOSAL.json").exists()
    objective = json.loads(objective_path.read_text(encoding="utf-8"))
    assert objective["objective_id"] == target_id
    assert objective["lifecycle_state"] == "CREATED"
    assert objective["activation_policy"] == "MANUAL_ONLY"
    assert objective["activation_authorized"] is False
    budget = json.loads(budget_path.read_text(encoding="utf-8"))
    assert all(item["used"] == 0 and item["reserved"] == 0 for item in budget["buckets"])
    governance = json.loads(governance_path.read_text(encoding="utf-8"))
    assert governance["action"] == CREATE_OBJECTIVE
    assert governance["approval_review_id"] == receipt["approval_review_id"]
    assert governance["confirmation_id"] == receipt["confirmation_id"]
    assert governance["confirmer"] == receipt["confirmer"]
    assert governance["candidate_created"] is False
    assert governance["trial_started"] is False
    assert governance["ai_called"] is False
    assert not (tmp_path / "data/research/research_factory/candidates").exists()
    assert not (tmp_path / "reports/research_daemon").exists()
    created_view = service.get_proposal(PROPOSAL_ID)
    assert created_view["display_state"] == "OBJECTIVE_CREATED"
    assert created_view["governance"]["next_action"] == "STOPPED_AFTER_OBJECTIVE_CREATION"
    assert created_view["governance_flow"]["requires_human_confirmation"] is False


def test_confirm_requires_an_explicit_confirmer(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    _approve(service)
    preview = service.get_objective_creation_preview(PROPOSAL_ID)
    payload = _confirm_payload(preview)
    payload.pop("confirmer")

    with pytest.raises(ResearchProposalGovernanceError) as error:
        service.confirm(PROPOSAL_ID, payload)

    assert error.value.code == "REVIEWER_REQUIRED"
    assert not (tmp_path / f"data/research/research_factory/objectives/{preview['target_objective_id']}.json").exists()

    missing_proposal_hash = _confirm_payload(preview)
    missing_proposal_hash.pop("proposal_hash")
    with pytest.raises(ResearchProposalGovernanceError) as hash_error:
        service.confirm(PROPOSAL_ID, missing_proposal_hash)
    assert hash_error.value.code == "STALE_PROPOSAL"


def test_confirm_is_exactly_once_for_same_preview(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    _approve(service)
    preview = service.get_objective_creation_preview(PROPOSAL_ID)
    first = service.confirm(PROPOSAL_ID, _confirm_payload(preview))
    second = service.confirm(PROPOSAL_ID, {**_confirm_payload(preview), "idempotency_key": "CONFIRM_RETRY_WITH_NEW_KEY"})

    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert second["execution_id"] == first["execution_id"]
    assert second["objective_id"] == first["objective_id"]
    receipt_paths = list((tmp_path / "reports/research_evolution/proposals/governance" / PROPOSAL_ID).glob("objective_creation_receipt.json"))
    objective_paths = list((tmp_path / "data/research/research_factory/objectives").glob("*.json"))
    assert len(receipt_paths) == 1
    assert len(objective_paths) == 2  # parent plus exactly one immutable child


def test_confirm_transaction_recovers_after_restart(tmp_path: Path) -> None:
    initial = _fixture(tmp_path, crash_at="after_budget_registry_write")
    service = initial["service"]
    _approve(service)
    preview = service.get_objective_creation_preview(PROPOSAL_ID)
    with pytest.raises(RuntimeError, match="SYNTHETIC_PROPOSAL_GOVERNANCE_CRASH"):
        service.confirm(PROPOSAL_ID, _confirm_payload(preview))

    restarted = ResearchProposalGovernanceServiceV1(tmp_path, clock=lambda: FIXED_NOW)
    recovered = restarted.recover_all()
    assert len(recovered) == 1
    assert recovered[0]["result_state"] == "OBJECTIVE_CREATED"
    assert restarted.get_execution(PROPOSAL_ID, recovered[0]["execution_id"])["objective_id"] == preview["target_objective_id"]
    retry = restarted.confirm(PROPOSAL_ID, _confirm_payload(preview))
    assert retry["idempotent"] is True
    assert retry["execution_id"] == recovered[0]["execution_id"]


def test_approval_preview_recovers_after_restart_without_duplicate_review(tmp_path: Path) -> None:
    initial = _fixture(tmp_path, crash_at="after_preview")
    with pytest.raises(RuntimeError, match="SYNTHETIC_PROPOSAL_GOVERNANCE_CRASH"):
        _approve(initial["service"])

    restarted = ResearchProposalGovernanceServiceV1(tmp_path, clock=lambda: datetime(2026, 1, 3, 12, 0, tzinfo=timezone.utc))
    resumed = _approve(restarted)
    assert resumed["idempotent"] is True
    assert resumed["proposal"]["governance_state"] == OBJECTIVE_CREATION_READY
    assert len((tmp_path / "reports/research_evolution/proposals/governance" / PROPOSAL_ID / "reviews.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_web_console_exposes_read_review_preview_and_confirm_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    import chanlun_trader.webapp as webapp
    application = webapp.create_app(tmp_path, webapp.ExecutionPolicy("GOVERNED", "SYNTHETIC"))

    fixture = _fixture(tmp_path)
    service = fixture["service"]
    monkeypatch.setattr(application.state.services, "research_proposal_governance_service", service)

    with TestClient(application) as client:
        listed = client.get("/api/research/evolution/proposals", params={"objective_id": OBJECTIVE_ID, "status": "PENDING"})
        assert listed.status_code == 200
        assert listed.json()["items"][0]["proposal_id"] == PROPOSAL_ID

        detail = client.get(f"/research/evolution/proposals/{PROPOSAL_ID}")
        assert detail.status_code == 200
        assert detail.json()["governance_state"] == HUMAN_REVIEW_REQUIRED

        reviewed = client.post(
            f"/api/research/evolution/proposals/{PROPOSAL_ID}/review",
            json={"action": "approve", "reviewer": "console-user"},
        )
        assert reviewed.status_code == 200
        preview = reviewed.json()["objective_creation_preview"]
        assert preview["status"] == READY_FOR_CONFIRMATION

        preview_response = client.get(f"/api/research/evolution/proposals/{PROPOSAL_ID}/preview")
        assert preview_response.status_code == 200
        assert preview_response.json()["preview_hash"] == preview["preview_hash"]

        not_confirmed = client.post(
            f"/api/research/evolution/proposals/{PROPOSAL_ID}/confirm",
            json={"confirmed": False},
        )
        assert not_confirmed.status_code == 400
        assert not (tmp_path / f"data/research/research_factory/objectives/{preview['target_objective_id']}.json").exists()
