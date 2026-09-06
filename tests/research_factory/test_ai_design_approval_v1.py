from __future__ import annotations

import json
from pathlib import Path

import pytest

from chanlun_trader.research_console import ResearchConsoleReadService
from chanlun_trader.research_factory.ai_design_approval import (
    AI_DESIGN_APPROVAL_RECEIPT_FILENAME,
    AIDesignApprovalError,
    AIDesignApprovalServiceV1,
)
from chanlun_trader.research_factory.candidate_generation import CandidateGenerationError, CandidateGenerationManagerV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.objective_reconciliation import (
    AI_DESIGN_APPROVED,
    AI_DESIGN_AWAITING_CONFIRMATION,
    ObjectiveReconciliationServiceV1,
)
from chanlun_trader.research_factory.research_evolution_ai_design import (
    AI_RESEARCH_DESIGN_FILENAME,
    ResearchEvolutionAIDesignServiceV1,
    ai_design_identity_hash,
)

from test_research_evolution_ai_design_v1 import OBJECTIVE_ID, _fixture_root


def _design_path(root: Path) -> Path:
    return root / "reports" / "research_evolution" / "ai_design" / OBJECTIVE_ID / AI_RESEARCH_DESIGN_FILENAME


def _receipt_path(root: Path) -> Path:
    return _design_path(root).with_name(AI_DESIGN_APPROVAL_RECEIPT_FILENAME)


def _prepared(tmp_path: Path) -> tuple[Path, dict]:
    root = _fixture_root(tmp_path)
    design = ResearchEvolutionAIDesignServiceV1(root).generate_design(OBJECTIVE_ID)
    return root, design


def test_missing_approval_blocks_candidate_generation_with_explicit_gate_code(tmp_path: Path) -> None:
    root, _ = _prepared(tmp_path)

    with pytest.raises(CandidateGenerationError) as error:
        CandidateGenerationManagerV1(root).generate_proposal(OBJECTIVE_ID)

    assert error.value.code == "AI_DESIGN_APPROVAL_REQUIRED"
    assert not (root / "reports" / "research_candidates").exists()


def test_pending_and_approved_reconciliation_states_are_explicit_and_safe(tmp_path: Path) -> None:
    root, design = _prepared(tmp_path)
    before = ObjectiveReconciliationServiceV1(root).reconcile(OBJECTIVE_ID)
    assert before["effective_state"] == AI_DESIGN_AWAITING_CONFIRMATION
    assert before["required_action"] == "HUMAN_CONFIRM_AI_RESEARCH_DESIGN"
    assert before["required_action_supported"] is True
    assert before["safe_to_advance"] is False

    approval = AIDesignApprovalServiceV1(root).approve(
        OBJECTIVE_ID,
        "alice",
        idempotency_key="AI_DESIGN_APPROVAL_T02",
        expected_ai_design_hash=design["design_hash"],
    )
    after = ObjectiveReconciliationServiceV1(root).reconcile(OBJECTIVE_ID)
    assert approval["decision"] == "APPROVED"
    assert after["effective_state"] == AI_DESIGN_APPROVED
    assert after["required_action"] == "GENERATE_CANDIDATE_PROPOSAL"
    assert after["required_action_supported"] is True
    assert after["safe_to_advance"] is True
    assert after["structural_preflight_ready"] is False
    assert after["ai_design_reconciliation"]["approval"]["approval_status"] == "APPROVED"


def test_valid_approval_allows_only_candidate_proposal_generation(tmp_path: Path) -> None:
    root, design = _prepared(tmp_path)
    approval = AIDesignApprovalServiceV1(root).approve(OBJECTIVE_ID, "alice", idempotency_key="AI_DESIGN_APPROVAL_T03")

    proposal = CandidateGenerationManagerV1(root).generate_proposal(OBJECTIVE_ID)

    assert proposal["status"] == "CANDIDATE_PROPOSAL_READY"
    assert proposal["ai_design_approval_id"] == approval["approval_id"]
    assert proposal["ai_design_approval_receipt_hash"] == approval["receipt"]["receipt_hash"]
    assert proposal["source_refs"]["ai_design_approval"].endswith(AI_DESIGN_APPROVAL_RECEIPT_FILENAME)
    assert proposal["governance"]["candidate_generation_allowed"] is True
    assert proposal["governance"]["candidate_created"] is False
    assert proposal["governance"]["structural_preflight_started"] is False
    assert proposal["governance"]["trial_started"] is False
    assert proposal["governance"]["budget_consumed"] is False
    assert design["design_hash"] == proposal["source_hashes"]["ai_research_design"]
    assert not (root / "data" / "research" / "research_factory" / "candidates").exists()
    assert not (root / "reports" / "research_daemon").exists()


def test_rejected_design_fail_closes_candidate_generation_and_cannot_be_reversed(tmp_path: Path) -> None:
    root, _ = _prepared(tmp_path)
    service = AIDesignApprovalServiceV1(root)
    rejected = service.reject(OBJECTIVE_ID, "bob", idempotency_key="AI_DESIGN_REJECTION_T04", reason="机制独立性不足")

    with pytest.raises(CandidateGenerationError) as error:
        CandidateGenerationManagerV1(root).generate_proposal(OBJECTIVE_ID)
    assert error.value.code == "AI_DESIGN_REJECTED"
    assert rejected["effective_state"] == "AI_DESIGN_REJECTED"
    with pytest.raises(AIDesignApprovalError) as conflict:
        service.approve(OBJECTIVE_ID, "alice", idempotency_key="AI_DESIGN_APPROVAL_T04")
    assert conflict.value.code == "APPROVAL_IDEMPOTENCY_CONFLICT"
    assert not (root / "reports" / "research_candidates").exists()


def test_stale_design_hash_and_objective_mismatch_are_distinct_gate_failures(tmp_path: Path) -> None:
    root, design = _prepared(tmp_path)
    service = AIDesignApprovalServiceV1(root)
    service.approve(OBJECTIVE_ID, "alice", idempotency_key="AI_DESIGN_APPROVAL_T05")
    design_path = _design_path(root)
    current = json.loads(design_path.read_text(encoding="utf-8"))
    current["research_hypothesis"] = f"{current['research_hypothesis']}；新设计身份"
    current["design_hash"] = ai_design_identity_hash(current)
    design_path.write_text(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(CandidateGenerationError) as stale:
        CandidateGenerationManagerV1(root).generate_proposal(OBJECTIVE_ID)
    assert stale.value.code == "STALE_AI_DESIGN_APPROVAL"

    root2, _ = _prepared(tmp_path / "objective-mismatch")
    service2 = AIDesignApprovalServiceV1(root2)
    service2.approve(OBJECTIVE_ID, "alice", idempotency_key="AI_DESIGN_APPROVAL_T05B")
    receipt_path = _receipt_path(root2)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["objective_id"] = "OTHER_OBJECTIVE"
    receipt["receipt_hash"] = stable_hash({key: value for key, value in receipt.items() if key != "receipt_hash"})
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(CandidateGenerationError) as mismatch:
        CandidateGenerationManagerV1(root2).generate_proposal(OBJECTIVE_ID)
    assert mismatch.value.code == "AI_DESIGN_APPROVAL_OBJECTIVE_MISMATCH"


def test_modified_receipt_is_integrity_failure_and_never_overwritten(tmp_path: Path) -> None:
    root, _ = _prepared(tmp_path)
    service = AIDesignApprovalServiceV1(root)
    service.approve(OBJECTIVE_ID, "alice", idempotency_key="AI_DESIGN_APPROVAL_T06")
    receipt_path = _receipt_path(root)
    original = receipt_path.read_bytes()
    receipt = json.loads(original)
    receipt["decision"] = "REJECTED"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(CandidateGenerationError) as error:
        CandidateGenerationManagerV1(root).generate_proposal(OBJECTIVE_ID)
    assert error.value.code == "AI_DESIGN_APPROVAL_INTEGRITY_FAILURE"
    assert receipt_path.read_bytes() != original
    assert AIDesignApprovalServiceV1(root).evaluate(OBJECTIVE_ID)["reason_code"] == "AI_DESIGN_APPROVAL_INTEGRITY_FAILURE"


def test_duplicate_approval_is_exactly_once_and_different_decision_conflicts(tmp_path: Path) -> None:
    root, _ = _prepared(tmp_path)
    service = AIDesignApprovalServiceV1(root)
    first = service.approve(OBJECTIVE_ID, "alice", idempotency_key="AI_DESIGN_APPROVAL_T07")
    receipt_before = _receipt_path(root).read_bytes()
    second = service.approve(OBJECTIVE_ID, "alice", idempotency_key="AI_DESIGN_APPROVAL_T07")
    assert second["idempotent"] is True
    assert second["approval_id"] == first["approval_id"]
    assert _receipt_path(root).read_bytes() == receipt_before
    assert len(list(_receipt_path(root).parent.glob(AI_DESIGN_APPROVAL_RECEIPT_FILENAME))) == 1
    with pytest.raises(AIDesignApprovalError) as conflict:
        service.reject(OBJECTIVE_ID, "bob", idempotency_key="AI_DESIGN_APPROVAL_T07")
    assert conflict.value.code == "APPROVAL_IDEMPOTENCY_CONFLICT"


def test_restart_reload_and_design_replacement_cannot_reuse_old_receipt(tmp_path: Path) -> None:
    root, _ = _prepared(tmp_path)
    first = AIDesignApprovalServiceV1(root).approve(OBJECTIVE_ID, "alice", idempotency_key="AI_DESIGN_APPROVAL_T08")
    restarted = AIDesignApprovalServiceV1(root).evaluate(OBJECTIVE_ID)
    assert restarted["approval_status"] == "APPROVED"
    assert restarted["receipt"]["receipt_hash"] == first["receipt"]["receipt_hash"]

    design_path = _design_path(root)
    design = json.loads(design_path.read_text(encoding="utf-8"))
    design["design_id"] = "AI_RESEARCH_DESIGN_REPLACED"
    design["mechanism_family"] = "volatility_structure"
    design["design_hash"] = ai_design_identity_hash(design)
    design_path.write_text(json.dumps(design, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(CandidateGenerationError) as error:
        CandidateGenerationManagerV1(root).generate_proposal(OBJECTIVE_ID)
    assert error.value.code == "STALE_AI_DESIGN_APPROVAL"


def test_web_approval_is_loopback_protected_and_generation_remains_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    import chanlun_trader.webapp as webapp

    root, design = _prepared(tmp_path)
    approval_service = AIDesignApprovalServiceV1(root)
    monkeypatch.setattr(webapp, "research_console_service", ResearchConsoleReadService(root))
    monkeypatch.setattr(webapp, "research_evolution_ai_design_approval_service", approval_service)
    monkeypatch.setattr(webapp, "candidate_generation_service", CandidateGenerationManagerV1(root))
    route = f"/api/research-console/{OBJECTIVE_ID}/evolution/ai-design"
    with TestClient(webapp.app) as client:
        before = client.get(route)
        assert before.status_code == 200
        assert before.json()["status"] == AI_DESIGN_AWAITING_CONFIRMATION
        assert before.json()["approval"]["approval_status"] == "PENDING"
        missing = client.post(f"{route}/approval", json={"decision": "APPROVED", "reviewer": "alice"})
        assert missing.status_code == 400
        assert missing.json()["code"] == "CONFIRMATION_REQUIRED"
        approved = client.post(f"{route}/approval", json={
            "confirmed": True,
            "decision": "APPROVED",
            "reviewer": "alice",
            "idempotency_key": "AI_DESIGN_APPROVAL_T09",
            "ai_design_hash": design["design_hash"],
        })
        assert approved.status_code == 200
        assert approved.json()["candidate_generation_allowed"] is True
        after = client.get(route)
        assert after.status_code == 200
        assert after.json()["status"] == AI_DESIGN_APPROVED
        assert after.json()["approval"]["receipt"]["reviewer"] == "alice"
        assert not (root / "reports" / "research_candidates").exists()
        generated = client.post(f"/api/research-console/{OBJECTIVE_ID}/candidate-proposals/generate")
        assert generated.status_code == 200
        assert generated.json()["status"] == "CANDIDATE_PROPOSAL_READY"
