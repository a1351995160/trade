from __future__ import annotations

import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory.candidate_generation import (
    CANDIDATE_FREEZE_READY,
    CANDIDATE_FREEZE_RECEIPT_FILENAME,
    CANDIDATE_PROPOSAL_FILENAME,
    CANDIDATE_PROPOSAL_READY,
    CANDIDATE_PROPOSAL_STATE_FILENAME,
    DUPLICATE_MECHANISM_REJECTED,
    HUMAN_REVIEW_REQUIRED,
    FROZEN,
    NEW_CANDIDATE,
    READY_FOR_STRUCTURAL_PREFLIGHT,
    CandidateGenerationError,
    CandidateGenerationManagerV1,
)
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.context import PerformanceBlindGuard
from chanlun_trader.research_factory.ai_design_approval import AIDesignApprovalServiceV1
from chanlun_trader.research_factory.research_evolution_ai_design import ResearchEvolutionAIDesignServiceV1


OBJECTIVE_ID = "OBJECTIVE_CANDIDATE_GENERATION_GOVERNANCE_V1"
PARENT_OBJECTIVE_ID = "OBJECTIVE_CANDIDATE_GENERATION_PARENT_V1"
PROPOSAL_ID = "PROPOSAL_CANDIDATE_GENERATION_V1"


def _write_json(root: Path, relative_path: str, payload: dict) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _fixture_root(tmp_path: Path) -> Path:
    proposal = {
        "schema_version": "research-evolution-proposal-v1",
        "proposal_type": "RESEARCH_EVOLUTION_PROPOSAL",
        "proposal_id": PROPOSAL_ID,
        "parent_objective_id": PARENT_OBJECTIVE_ID,
        "created_objective_id": OBJECTIVE_ID,
        "objective_created": True,
        "governance_state": "OBJECTIVE_CREATION_READY",
        "state_history": ["CREATED", "HUMAN_REVIEW_REQUIRED", "APPROVED", "OBJECTIVE_CREATION_READY"],
        "failed_mechanism": "AMOUNT_ACCEL+ILLIQUIDITY",
        "failed_mechanism_family": "liquidity_acceleration",
        "failure_summary": ["STATISTICAL_FAILURE", "RISK_FAILURE"],
        "avoid_mechanism_family": ["liquidity_acceleration"],
        "suggested_research_directions": ["event_driven", "volatility_structure"],
        "outcome_blind": True,
        "governance": {
            "human_approval_required": False,
            "automatic_candidate_created": False,
            "automatic_trial_started": False,
            "budget_consumed": False,
            "ai_called": False,
        },
    }
    proposal["proposal_hash"] = stable_hash(proposal)
    _write_json(tmp_path, "reports/research_evolution/proposals/RESEARCH_EVOLUTION_PROPOSAL.json", proposal)
    _write_json(tmp_path, f"reports/research_evolution/{PARENT_OBJECTIVE_ID}/failure_landscape.json", {
        "schema_version": "research-failure-landscape-v1",
        "objective_id": PARENT_OBJECTIVE_ID,
        "entries": [{
            "candidate_id": "PARENT_FAILED_CANDIDATE",
            "candidate_family": "DAILY_CROSS_SECTIONAL",
            "mechanism": "AMOUNT_ACCEL+ILLIQUIDITY",
            "failure_categories": ["STATISTICAL_FAILURE", "RISK_FAILURE"],
            "trial_id": "PARENT_FAILED_TRIAL",
        }],
        "category_totals": {"STATISTICAL_FAILURE": 1, "RISK_FAILURE": 1},
        "read_only": True,
    })
    _write_json(tmp_path, "reports/research_evolution/proposals/MECHANISM_COVERAGE_REGISTRY.json", {
        "schema_version": "mechanism-coverage-registry-v1",
        "coverage_id": "MECHANISM_COVERAGE_REGISTRY_V1",
        "covered": ["daily_cross_sectional", "liquidity_acceleration"],
        "unexplored": ["event_driven", "volatility_structure"],
        "read_only": True,
        "outcome_blind": True,
    })
    _write_json(tmp_path, "data/research/data_capability.json", {
        "generated_at": "2026-09-04T00:00:00+00:00",
        "datasets": [{
            "dataset_id": "daily_ohlcva_raw",
            "source": "fixture",
            "provider": "fixture-provider",
            "fields": ["date", "open", "high", "low", "close", "volume", "amount"],
            "frequency": "DAILY",
            "earliest_date": "2020-01-01",
            "latest_date": "2026-09-03",
            "event_time_semantics": "TRADE_DATE",
            "available_at_semantics": "T_CLOSE",
            "PIT_safe": True,
            "data_version": "fixture-v1",
            "status": "READY",
        }],
    })
    _write_json(tmp_path, f"data/research/research_factory/objectives/{OBJECTIVE_ID}.json", {
        "schema_version": "research-objective-v1",
        "objective_id": OBJECTIVE_ID,
        "objective_name": "事件驱动波动结构研究目标",
        "lifecycle_state": "CREATED",
        "parent_objective_id": PARENT_OBJECTIVE_ID,
        "parent_proposal_id": PROPOSAL_ID,
        "parent_proposal_hash": proposal["proposal_hash"],
        "research_direction": "event_driven",
        "allowed_factor_scope": ["EVENT_FLAG", "ATR_14", "BODY_RATIO", "RETURN_5D", "VOLUME_ACCEL"],
        "holding_horizon": [2, 10],
        "preferred_horizon": [5, 8],
        "max_batches": 3,
        "max_total_trials": 4,
        "budget_policy_version": "RESEARCH_FACTORY_BUDGET_V1",
        "multiple_testing_family_id": "MT_EVOLUTION_CANDIDATE_V1",
        "risk_constraints": {
            "pit_required": True,
            "no_lookahead": True,
            "t_plus_1": True,
            "price_limit_fail_closed": True,
            "suspension_fail_closed": True,
        },
    })
    _write_json(tmp_path, f"data/research/research_factory/lineage/{OBJECTIVE_ID}.json", {
        "schema_version": "research-proposal-objective-lineage-v1",
        "lineage_id": "LINEAGE_CANDIDATE_GENERATION_V1",
        "lineage_status": "IMMUTABLE",
        "objective_id": OBJECTIVE_ID,
        "parent_objective_id": PARENT_OBJECTIVE_ID,
        "proposal_id": PROPOSAL_ID,
        "proposal_hash": proposal["proposal_hash"],
        "research_direction": "event_driven",
        "immutable": True,
        "parent_lineage": [{
            "relation": "GENERATED_FROM_PROPOSAL",
            "parent_type": "ResearchProposal",
            "parent_id": PROPOSAL_ID,
            "parent_hash": proposal["proposal_hash"],
            "immutable": True,
        }],
    })
    return tmp_path


def _prepare(tmp_path: Path) -> Path:
    root = _fixture_root(tmp_path)
    ResearchEvolutionAIDesignServiceV1(root).generate_design(OBJECTIVE_ID)
    AIDesignApprovalServiceV1(root).approve(OBJECTIVE_ID, "stage-b-test-reviewer", idempotency_key="AI_DESIGN_APPROVAL_TEST")
    return root


def _proposal_path(root: Path) -> Path:
    return root / "reports" / "research_candidates" / "proposals" / OBJECTIVE_ID / CANDIDATE_PROPOSAL_FILENAME


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(str(key) for key in value)
        for nested in value.values():
            keys.update(_all_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.update(_all_keys(nested))
    return keys


def test_ai_design_converts_to_complete_candidate_proposal_without_side_effects(tmp_path: Path) -> None:
    root = _prepare(tmp_path)
    budget_path = _write_json(root, f"data/research/research_factory/batches/{OBJECTIVE_ID}_B01/search_budget_registry.json", {
        "objective_id": OBJECTIVE_ID,
        "used": 0,
        "reserved": 0,
        "total": 4,
    })
    budget_before = budget_path.read_bytes()

    proposal = CandidateGenerationManagerV1(root).generate_proposal(OBJECTIVE_ID)

    assert proposal["status"] == CANDIDATE_PROPOSAL_READY
    assert proposal["human_review_required"] is True
    assert proposal["ai_research_design_id"]
    assert proposal["research_hypothesis"]
    assert proposal["mechanism_family"] == "event_driven"
    assert proposal["candidate_name"] == "EVENT_DRIVEN_VOLATILITY_STRUCTURE_V1"
    assert proposal["factor_contract"]
    assert all("return" not in factor.casefold() for factor in proposal["factor_contract"])
    assert proposal["execution_contract"] == {
        "entry": "NEXT_SESSION_OPEN",
        "holding_period": 5,
        "holding_period_unit": "TRADING_SESSIONS",
        "risk_constraints": {
            "no_lookahead": True,
            "pit_required": True,
            "price_limit_fail_closed": True,
            "suspension_fail_closed": True,
            "t_plus_1": True,
        },
        "same_session_sell_forbidden": True,
        "signal_time": "T_CLOSE",
    }
    assert proposal["data_contract"]["required_dataset_ids"] == ["daily_ohlcva_raw"]
    assert proposal["multiple_testing_family_id"] == "MT_EVOLUTION_CANDIDATE_V1"
    assert proposal["lineage"]["lineage_id"] == "LINEAGE_CANDIDATE_GENERATION_V1"
    assert proposal["governance"]["next_action"] == HUMAN_REVIEW_REQUIRED
    assert proposal["governance"]["candidate_created"] is False
    assert proposal["governance"]["candidate_frozen"] is False
    assert proposal["governance"]["structural_preflight_started"] is False
    assert proposal["governance"]["trial_started"] is False
    assert proposal["governance"]["ai_called"] is False
    assert proposal["governance"]["budget_consumed"] is False
    assert not (root / "data/research/strategy_candidate_registry").exists()
    assert not (root / "reports/research_factory").exists()
    assert budget_path.read_bytes() == budget_before
    PerformanceBlindGuard.assert_blind(proposal)
    forbidden = {"return", "win_rate", "drawdown", "p_value", "pnl", "performance", "trades", "equity", "stock_list", "symbols"}
    assert not {key.casefold() for key in _all_keys(proposal)} & forbidden


def test_candidate_input_never_reads_performance_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _prepare(tmp_path)
    performance_path = root / "reports" / "research_daemon" / OBJECTIVE_ID / "performance.json"
    performance_path.parent.mkdir(parents=True, exist_ok=True)
    performance_path.write_text('{"win_rate": 0.99}', encoding="utf-8")
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):
        if "performance" in str(path).casefold():
            raise AssertionError("Candidate generation must not read performance artifacts")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    proposal = CandidateGenerationManagerV1(root).generate_proposal(OBJECTIVE_ID)
    assert proposal["outcome_blind"] is True
    assert proposal["performance_data_loaded"] is False
    assert proposal["outcome_fields_available"] is False


def test_candidate_input_rejects_chinese_outcome_field(tmp_path: Path) -> None:
    root = _prepare(tmp_path)
    design_path = root / "reports" / "research_evolution" / "ai_design" / OBJECTIVE_ID / "AI_RESEARCH_DESIGN_PROPOSAL.json"
    design = json.loads(design_path.read_text(encoding="utf-8"))
    design["胜率"] = 0.5
    design_path.write_text(json.dumps(design, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(CandidateGenerationError) as error:
        CandidateGenerationManagerV1(root).generate_proposal(OBJECTIVE_ID)

    assert error.value.code == "OUTCOME_FIELD_BLOCKED"
    assert not (root / "reports" / "research_candidates").exists()


@pytest.mark.parametrize("alias", ["liquidity_acceleration_v2", "amount_acceleration", "volume_acceleration"])
def test_duplicate_mechanism_alias_is_rejected_before_persisting_proposal(tmp_path: Path, alias: str) -> None:
    root = _prepare(tmp_path)
    design_path = root / "reports" / "research_evolution" / "ai_design" / OBJECTIVE_ID / "AI_RESEARCH_DESIGN_PROPOSAL.json"
    design = json.loads(design_path.read_text(encoding="utf-8"))
    design["mechanism_family"] = alias
    design_path.write_text(json.dumps(design, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(CandidateGenerationError) as error:
        CandidateGenerationManagerV1(root).generate_proposal(OBJECTIVE_ID)

    assert error.value.code == "AI_DESIGN_APPROVAL_INTEGRITY_FAILURE"
    assert not (root / "reports" / "research_candidates").exists()


def test_approve_only_materializes_freeze_preview_and_reject_preserves_parent_history(tmp_path: Path) -> None:
    root = _prepare(tmp_path)
    manager = CandidateGenerationManagerV1(root)
    proposal = manager.generate_proposal(OBJECTIVE_ID)
    parent_path = root / "reports" / "research_evolution" / "proposals" / "RESEARCH_EVOLUTION_PROPOSAL.json"
    parent_before = parent_path.read_bytes()
    response = manager.review(proposal["proposal_id"], {
        "action": "approve",
        "reviewer": "alice",
        "proposal_hash": proposal["proposal_hash"],
        "reason": "确认机制独立且执行契约完整",
    })

    assert response["proposal"]["governance_state"] == CANDIDATE_FREEZE_READY
    assert response["proposal"]["state_history"][-2:] == ["APPROVED", CANDIDATE_FREEZE_READY]
    assert response["review"]["review_id"]
    assert response["review"]["reviewer"] == "alice"
    assert response["review"]["timestamp"]
    assert response["review"]["proposal_hash"] == proposal["proposal_hash"]
    assert response["freeze_preview"]["candidate_identity_status"] == "PREVIEW_ONLY_NOT_REGISTERED"
    assert response["freeze_preview"]["requires_human_confirmation"] is True
    assert not (root / "data/research/strategy_candidate_registry").exists()
    assert not (root / "data/research/research_factory/candidates").exists()
    assert not (root / "reports/research_daemon").exists()
    assert not (root / "reports/research_factory").exists()
    assert parent_path.read_bytes() == parent_before

    reject_root = _prepare(tmp_path / "reject")
    reject_manager = CandidateGenerationManagerV1(reject_root)
    reject_proposal = reject_manager.generate_proposal(OBJECTIVE_ID)
    reject_parent = reject_root / "reports" / "research_evolution" / "proposals" / "RESEARCH_EVOLUTION_PROPOSAL.json"
    reject_before = reject_parent.read_bytes()
    rejected = reject_manager.review(reject_proposal["proposal_id"], {"action": "reject", "reviewer": "bob"})
    assert rejected["proposal"]["governance_state"] == "REJECTED"
    assert rejected["freeze_preview"] is None
    assert reject_parent.read_bytes() == reject_before


def test_freeze_preview_is_not_available_before_approval(tmp_path: Path) -> None:
    root = _prepare(tmp_path)
    proposal = CandidateGenerationManagerV1(root).generate_proposal(OBJECTIVE_ID)
    with pytest.raises(CandidateGenerationError) as error:
        CandidateGenerationManagerV1(root).get_freeze_preview(proposal["proposal_id"])
    assert error.value.code == "CANDIDATE_FREEZE_NOT_READY"


def test_candidate_proposal_is_exactly_once_and_restart_recovers_durable_output(tmp_path: Path) -> None:
    root = _prepare(tmp_path)
    with pytest.raises(RuntimeError, match="after Candidate Proposal output"):
        CandidateGenerationManagerV1(root, crash_at="after_output").generate_proposal(OBJECTIVE_ID)
    output_path = _proposal_path(root)
    state_path = output_path.parent / CANDIDATE_PROPOSAL_STATE_FILENAME
    assert output_path.exists()
    assert not state_path.exists()

    recovered = CandidateGenerationManagerV1(root).recover(OBJECTIVE_ID)
    assert recovered["recovered"] is True
    assert recovered["status"] == CANDIDATE_PROPOSAL_READY
    assert state_path.exists()
    first_bytes = output_path.read_bytes()
    second = CandidateGenerationManagerV1(root).generate_proposal(OBJECTIVE_ID)
    assert second["idempotent"] is True
    assert second["proposal_id"] == recovered["proposal_id"]
    assert output_path.read_bytes() == first_bytes
    assert len(list(output_path.parent.glob(CANDIDATE_PROPOSAL_FILENAME))) == 1


def _approved_candidate(tmp_path: Path) -> tuple[Path, CandidateGenerationManagerV1, dict, dict]:
    root = _prepare(tmp_path)
    manager = CandidateGenerationManagerV1(root)
    proposal = manager.generate_proposal(OBJECTIVE_ID)
    approved = manager.review(proposal["proposal_id"], {
        "action": "approve",
        "reviewer": "alice",
        "proposal_hash": proposal["proposal_hash"],
    })
    return root, manager, proposal, approved["freeze_preview"]


def test_confirmed_freeze_creates_registry_and_stops_before_structural_or_trial(tmp_path: Path) -> None:
    root, manager, proposal, preview = _approved_candidate(tmp_path)
    budget_path = _write_json(root, f"data/research/research_factory/batches/{OBJECTIVE_ID}_B01/search_budget_registry.json", {"objective_id": OBJECTIVE_ID, "used": 0, "reserved": 0, "total": 4})
    budget_before = budget_path.read_bytes()
    preview_path = _proposal_path(root).parent / "CANDIDATE_FREEZE_PREVIEW.json"
    preview_before = preview_path.read_bytes()

    with pytest.raises(CandidateGenerationError) as missing_confirmation:
        manager.freeze(proposal["proposal_id"], {"reviewer": "alice"})
    assert missing_confirmation.value.code == "CONFIRMATION_REQUIRED"
    assert not (root / "data/research/research_factory/candidates").exists()

    result = manager.freeze(proposal["proposal_id"], {
        "action": "FREEZE_CANDIDATE",
        "confirmed": True,
        "freeze_id": "FREEZE_CANDIDATE_TEST_V1",
        "reviewer": "alice",
        "proposal_hash": proposal["proposal_hash"],
        "candidate_hash": preview["candidate_hash"],
    })

    view = result["proposal"]
    assert view["governance_state"] == READY_FOR_STRUCTURAL_PREFLIGHT
    assert view["state_history"][-2:] == [FROZEN, READY_FOR_STRUCTURAL_PREFLIGHT]
    assert view["candidate_frozen"] is True
    assert view["governance"]["structural_preflight_started"] is False
    assert view["governance"]["trial_started"] is False
    assert view["governance"]["budget_consumed"] is False
    assert result["freeze"]["freeze_id"] == "FREEZE_CANDIDATE_TEST_V1"
    assert result["freeze"]["reviewer"] == "alice"
    assert result["freeze"]["proposal_hash"] == proposal["proposal_hash"]
    assert result["freeze"]["candidate_hash"] == preview["candidate_hash"]
    assert result["candidate"]["state"] == FROZEN
    assert result["candidate"]["candidate_id"] == preview["candidate_id"]
    assert result["candidate"]["lineage"] == proposal["lineage"]
    assert result["candidate"]["contract"]["factor_contract"] == proposal["factor_contract"]
    assert preview_path.read_bytes() == preview_before
    assert budget_path.read_bytes() == budget_before
    assert not (root / "reports/research_daemon").exists()
    assert not (root / "reports/research_factory").exists()
    registry_path = root / "data/research/research_factory/candidates" / OBJECTIVE_ID / "CANDIDATE_REGISTRY.json"
    assert registry_path.exists()
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert len(registry["candidates"]) == 1
    assert registry["candidates"][0]["candidate_hash"] == preview["candidate_hash"]


def test_freeze_hash_consistency_and_exact_once_restart_recovery(tmp_path: Path) -> None:
    root, _, proposal, preview = _approved_candidate(tmp_path)
    crashing = CandidateGenerationManagerV1(root, crash_at="after_candidate_registry")
    with pytest.raises(RuntimeError, match="after_candidate_registry"):
        crashing.freeze(proposal["proposal_id"], {
            "confirmed": True,
            "reviewer": "alice",
            "proposal_hash": proposal["proposal_hash"],
            "candidate_hash": preview["candidate_hash"],
        })
    registry_path = root / "data/research/research_factory/candidates" / OBJECTIVE_ID / "CANDIDATE_REGISTRY.json"
    assert registry_path.exists()
    recovered = CandidateGenerationManagerV1(root).recover(OBJECTIVE_ID)
    assert recovered["recovered"] is True
    assert recovered["governance_state"] == READY_FOR_STRUCTURAL_PREFLIGHT
    first_registry = registry_path.read_bytes()
    first_reviews = (_proposal_path(root).parent / "reviews.jsonl").read_bytes()
    repeated = CandidateGenerationManagerV1(root).freeze(proposal["proposal_id"], {
        "confirmed": True,
        "reviewer": "alice",
        "proposal_hash": proposal["proposal_hash"],
        "candidate_hash": preview["candidate_hash"],
    })
    assert repeated["idempotent"] is True
    assert repeated["candidate"]["candidate_hash"] == preview["candidate_hash"]
    assert registry_path.read_bytes() == first_registry
    assert (_proposal_path(root).parent / "reviews.jsonl").read_bytes() == first_reviews
    assert repeated["freeze"]["proposal_hash"] == proposal["proposal_hash"]


def test_frozen_contract_edit_requires_new_candidate_and_never_overwrites_old(tmp_path: Path) -> None:
    root, manager, proposal, preview = _approved_candidate(tmp_path)
    frozen = manager.freeze(proposal["proposal_id"], {
        "confirmed": True,
        "reviewer": "alice",
        "proposal_hash": proposal["proposal_hash"],
        "candidate_hash": preview["candidate_hash"],
    })
    registry_path = root / "data/research/research_factory/candidates" / OBJECTIVE_ID / "CANDIDATE_REGISTRY.json"
    registry_before = registry_path.read_bytes()
    proposal_path = _proposal_path(root)
    changed = json.loads(proposal_path.read_text(encoding="utf-8"))
    changed["factor_contract"] = ["MUTATED_FACTOR"]
    proposal_path.write_text(json.dumps(changed, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    modification = manager.detect_frozen_modification(proposal["proposal_id"])
    assert modification["status"] == NEW_CANDIDATE
    assert modification["old_candidate_id"] == frozen["candidate"]["candidate_id"]
    assert modification["new_candidate_id"] != modification["old_candidate_id"]
    assert "factor_contract" in modification["changed_contracts"]
    with pytest.raises(CandidateGenerationError) as error:
        manager.freeze(proposal["proposal_id"], {"confirmed": True, "reviewer": "alice"})
    assert error.value.code == "NEW_CANDIDATE_REQUIRED"
    assert error.value.details["overwrite_forbidden"] is True
    assert registry_path.read_bytes() == registry_before


def test_freeze_http_endpoint_requires_local_confirmation_and_is_exactly_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, manager, proposal, preview = _approved_candidate(tmp_path)
    from fastapi.testclient import TestClient

    import chanlun_trader.webapp as webapp

    monkeypatch.setattr(webapp, "candidate_generation_service", manager)
    route = f"/api/research/candidates/proposals/{proposal['proposal_id']}/freeze"
    with TestClient(webapp.app) as client:
        missing = client.post(route, json={"reviewer": "alice"})
        assert missing.status_code == 400
        first = client.post(route, json={
            "action": "FREEZE_CANDIDATE",
            "confirmed": True,
            "reviewer": "alice",
            "proposal_hash": proposal["proposal_hash"],
            "candidate_hash": preview["candidate_hash"],
        })
        second = client.post(route, json={
            "action": "FREEZE_CANDIDATE",
            "confirmed": True,
            "reviewer": "alice",
            "proposal_hash": proposal["proposal_hash"],
            "candidate_hash": preview["candidate_hash"],
        })
    assert first.status_code == 200
    assert first.json()["proposal"]["governance_state"] == READY_FOR_STRUCTURAL_PREFLIGHT
    assert second.status_code == 200
    assert second.json()["idempotent"] is True
