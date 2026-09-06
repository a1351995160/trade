from __future__ import annotations

import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory.autonomous_control_plane import (
    ALLOW_AUTOMATIC,
    ALLOW_MANUAL_ONLY,
    AgentCapabilityRegistryV1,
    AutonomousResearchControlPlaneV1,
    CAPABILITY_MISSING,
    CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW,
    GENERATE_CANDIDATE_PROPOSAL,
    PHASE2_PREDICTIVE_EXECUTION_DISABLED,
    PREDICTIVE_AUTHORIZATION_REQUIRED,
    RECOVER_EXECUTABLE_MATERIALIZATION,
    ResearchActionV1,
    RUN_STRUCTURAL_PREFLIGHT,
    STALE_RESEARCH_ACTION,
    WAIT_FOR_AI_DESIGN_CONFIRMATION,
    WAIT_FOR_CANDIDATE_GOVERNANCE_FREEZE,
)
from chanlun_trader.research_factory.ai_design_approval import AIDesignApprovalServiceV1
from chanlun_trader.research_factory.candidate_generation import CandidateGenerationManagerV1
from chanlun_trader.research_factory.candidate_executable_materialization import CandidateExecutableMaterializationManagerV1
from chanlun_trader.research_factory.research_evolution_ai_design import ResearchEvolutionAIDesignServiceV1
from chanlun_trader.research_factory.context import PerformanceBlindGuard

from test_candidate_generation_governance_v1 import OBJECTIVE_ID, _fixture_root, _prepare
from test_candidate_executable_materialization_v1 import _bridge_fixture
from test_structural_entry_projection_reconciliation_v1 import _ready_fixture, _runtime
from chanlun_trader.research_factory.structural_entry import StructuralEntryServiceV1


def _write_json(root: Path, relative: str, payload: object) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _pending_design_root(tmp_path: Path) -> Path:
    root = _fixture_root(tmp_path)
    _write_json(root, f"data/research/research_factory/batches/{OBJECTIVE_ID}_B01/search_budget_registry.json", {
        "objective_id": OBJECTIVE_ID,
        "used": 0,
        "reserved": 0,
        "total": 4,
    })
    ResearchEvolutionAIDesignServiceV1(root).generate_design(OBJECTIVE_ID)
    return root


def test_reconciliation_is_first_and_design_confirmation_is_manual(tmp_path: Path, monkeypatch) -> None:
    root = _pending_design_root(tmp_path)
    plane = AutonomousResearchControlPlaneV1(root)
    order: list[str] = []
    original_reconcile = plane.reconciliation.reconcile
    original_build = plane.context_builder.build

    def reconcile(objective_id: str):
        order.append("reconcile")
        return original_reconcile(objective_id)

    def build(objective_id: str, *, purpose: str = "RUNTIME"):
        assert order
        order.append("context")
        return original_build(objective_id, purpose=purpose)

    monkeypatch.setattr(plane.reconciliation, "reconcile", reconcile)
    monkeypatch.setattr(plane.context_builder, "build", build)
    result = plane.tick(OBJECTIVE_ID, dry_run=True)

    assert order[0] == "reconcile"
    assert result["decision"]["selected_action"]["action_type"] == WAIT_FOR_AI_DESIGN_CONFIRMATION
    assert result["decision"]["permission"]["permission"] == ALLOW_MANUAL_ONLY
    assert result["decision"]["automatic_execution"] is False
    PerformanceBlindGuard.assert_blind(result)
    assert not (root / "reports/research_control_plane").exists()


def test_approved_design_executes_one_candidate_proposal_and_then_stops_at_human_gate(tmp_path: Path) -> None:
    root = _prepare(tmp_path)
    plane = AutonomousResearchControlPlaneV1(root)

    first = plane.tick(OBJECTIVE_ID)
    assert first["decision"]["selected_action"]["action_type"] == GENERATE_CANDIDATE_PROPOSAL
    assert first["decision"]["permission"]["permission"] == ALLOW_AUTOMATIC
    assert first["execution"]["execution_status"] == "COMPLETED"
    proposal_path = root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "CANDIDATE_PROPOSAL.json"
    assert proposal_path.exists()

    second = plane.tick(OBJECTIVE_ID)
    assert second["decision"]["selected_action"]["action_type"] == WAIT_FOR_CANDIDATE_GOVERNANCE_FREEZE
    assert second["decision"]["permission"]["permission"] == ALLOW_MANUAL_ONLY
    assert len(list(proposal_path.parent.glob("CANDIDATE_PROPOSAL.json"))) == 1
    assert len(plane._journal(OBJECTIVE_ID).read()) == 2


def test_duplicate_tick_does_not_execute_same_action_twice(tmp_path: Path) -> None:
    root = _prepare(tmp_path)
    plane = AutonomousResearchControlPlaneV1(root)
    first = plane.tick(OBJECTIVE_ID)
    assert first["execution"]["execution_status"] == "COMPLETED"

    # Replaying the same completed action directly must use its receipt.
    action = ResearchActionV1.from_dict(first["decision"]["selected_action"])
    repeated = plane.execute_action(action)
    assert repeated["execution_status"] == "COMPLETED"
    assert repeated["idempotent"] is True
    assert len(plane._journal(OBJECTIVE_ID).read()) == 2


def test_stale_action_is_denied_after_context_change(tmp_path: Path) -> None:
    root = _prepare(tmp_path)
    plane = AutonomousResearchControlPlaneV1(root)
    planned = plane.inspect(OBJECTIVE_ID)
    action = ResearchActionV1.from_dict(planned["decision"]["selected_action"])

    data_path = root / "data/research/data_capability.json"
    data_payload = json.loads(data_path.read_text(encoding="utf-8"))
    data_payload["datasets"][0]["data_version"] = "fixture-stale-v2"
    _write_json(root, "data/research/data_capability.json", data_payload)

    result = plane.execute_action(action)
    assert result["reason_code"] == STALE_RESEARCH_ACTION
    assert not (root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "CANDIDATE_PROPOSAL.json").exists()


def test_capability_missing_denies_even_when_canonical_state_allows(tmp_path: Path) -> None:
    root = _prepare(tmp_path)
    capabilities = tuple(item for item in AgentCapabilityRegistryV1.default_capabilities() if item.capability_id != "CAN_GENERATE_CANDIDATE_PROPOSAL")
    plane = AutonomousResearchControlPlaneV1(root, capability_registry=AgentCapabilityRegistryV1(capabilities))

    result = plane.inspect(OBJECTIVE_ID)
    assert result["decision"]["selected_action"]["action_type"] == GENERATE_CANDIDATE_PROPOSAL
    assert result["decision"]["permission"]["permission"] == "DENY"
    assert result["decision"]["permission"]["reason_code"] == CAPABILITY_MISSING
    assert not (root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "CANDIDATE_PROPOSAL.json").exists()


def test_dry_run_has_zero_control_plane_and_domain_writes(tmp_path: Path) -> None:
    root = _prepare(tmp_path)
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    result = AutonomousResearchControlPlaneV1(root).tick(OBJECTIVE_ID, dry_run=True)
    after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

    assert result["dry_run"] is True
    assert result["stop_reason"] == "DRY_RUN_NO_SIDE_EFFECT"
    assert before == after


def test_loop_enforces_max_ticks_and_stops_at_human_gate(tmp_path: Path) -> None:
    root = _prepare(tmp_path)
    result = AutonomousResearchControlPlaneV1(root).loop(OBJECTIVE_ID, max_ticks=10)

    assert result["ticks_executed"] == 2
    assert result["ticks_executed"] <= result["max_ticks"]
    assert result["results"][0]["execution"]["execution_status"] == "COMPLETED"
    assert result["results"][1]["decision"]["selected_action"]["action_type"] == WAIT_FOR_CANDIDATE_GOVERNANCE_FREEZE


def test_phase2_predictive_action_never_starts_trial_without_authorization(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    _write_json(root, f"data/research/research_factory/batches/{OBJECTIVE_ID}_B01/search_budget_registry.json", {
        "objective_id": OBJECTIVE_ID,
        "used": 0,
        "reserved": 0,
        "total": 4,
    })
    # A synthetic Structural PASS is only used to test the planner boundary;
    # it is not a provider invocation and carries no performance values.
    _write_json(root, f"reports/research_daemon/{OBJECTIVE_ID}/structural_preflight_reconciliation_canonical_v1.json", {
        "objective_id": OBJECTIVE_ID,
        "candidate_id": "CANDIDATE_SYNTHETIC",
        "candidate_hash": "HASH_SYNTHETIC",
        "status": "PASS",
        "structural_identity": {
            "objective_id": OBJECTIVE_ID,
            "candidate_id": "CANDIDATE_SYNTHETIC",
            "candidate_hash": "HASH_SYNTHETIC",
        },
    })
    # Without an executable canonical contract, reconciliation must fail
    # closed before it can infer Predictive authorization.
    result = AutonomousResearchControlPlaneV1(root).inspect(OBJECTIVE_ID)
    assert result["decision"]["permission"]["permission"] in {"DENY", ALLOW_MANUAL_ONLY}
    assert result["decision"]["selected_action"]["action_type"] != "START_PREDICTIVE_TRIAL" or result["decision"]["permission"]["reason_code"] == PREDICTIVE_AUTHORIZATION_REQUIRED


def test_materialization_preview_then_confirmation_recovery_keeps_structural_manual(tmp_path: Path) -> None:
    root, proposal, _ = _bridge_fixture(tmp_path)
    plane = AutonomousResearchControlPlaneV1(root)

    preview = plane.tick(OBJECTIVE_ID)
    assert preview["decision"]["selected_action"]["action_type"] == CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW
    assert preview["execution"]["execution_status"] == "COMPLETED"

    preview_path = root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "EXECUTABLE_MATERIALIZATION_PREVIEW.json"
    preview_payload = json.loads(preview_path.read_text(encoding="utf-8"))
    waiting = plane.tick(OBJECTIVE_ID)
    assert waiting["decision"]["permission"]["permission"] == ALLOW_MANUAL_ONLY
    assert waiting["decision"]["selected_action"]["required_confirmation"] == "HUMAN_CONFIRM_EXECUTABLE_MATERIALIZATION"

    with pytest.raises(RuntimeError, match="SYNTHETIC_CRASH_INJECTED"):
        CandidateExecutableMaterializationManagerV1(root, crash_at="after_confirmation_receipt").confirm(
            OBJECTIVE_ID,
            proposal["proposal_id"],
            {
                "confirmed": True,
                "reviewer": "control-plane-fixture-reviewer",
                "preview_hash": preview_payload["preview_hash"],
                "idempotency_key": "CONTROL_PLANE_RECOVERY_V1",
            },
        )

    recovery = plane.inspect(OBJECTIVE_ID)
    assert recovery["decision"]["current_effective_state"] == "EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED"
    assert recovery["decision"]["selected_action"]["action_type"] == RECOVER_EXECUTABLE_MATERIALIZATION
    assert recovery["decision"]["permission"]["permission"] == "ALLOW_AUTOMATIC"

    recovered = plane.tick(OBJECTIVE_ID)
    assert recovered["execution"]["execution_status"] == "COMPLETED"
    ready = plane.inspect(OBJECTIVE_ID)
    assert ready["decision"]["current_effective_state"] == "READY_FOR_STRUCTURAL_PREFLIGHT"
    assert ready["decision"]["selected_action"]["action_type"] == RUN_STRUCTURAL_PREFLIGHT
    assert ready["decision"]["permission"]["permission"] == ALLOW_MANUAL_ONLY


def test_structural_pass_stops_at_predictive_authorization_without_trial(tmp_path: Path) -> None:
    root, contract = _ready_fixture(tmp_path)
    runtime, calls = _runtime(root, "PASS")
    StructuralEntryServiceV1(root, runtime=runtime).start(
        OBJECTIVE_ID,
        candidate_id=contract.candidate_id,
        confirmed=True,
        action=RUN_STRUCTURAL_PREFLIGHT,
    )
    plane = AutonomousResearchControlPlaneV1(root)

    result = plane.inspect(OBJECTIVE_ID)

    assert calls == [contract.candidate_id]
    assert result["decision"]["current_effective_state"] == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
    assert result["decision"]["selected_action"]["action_type"] == "WAIT_FOR_PREDICTIVE_AUTHORIZATION"
    assert result["decision"]["permission"]["permission"] == ALLOW_MANUAL_ONLY
    assert result["decision"]["automatic_execution"] is False
    assert not list((root / "data/research/research_factory/batches").glob("*/factory_trial_ledger.json"))


def test_authorized_predictive_action_remains_disabled_in_phase2(tmp_path: Path) -> None:
    root, contract = _ready_fixture(tmp_path)
    runtime, _ = _runtime(root, "PASS")
    StructuralEntryServiceV1(root, runtime=runtime).start(
        OBJECTIVE_ID,
        candidate_id=contract.candidate_id,
        confirmed=True,
        action=RUN_STRUCTURAL_PREFLIGHT,
    )
    auth_path = root / "reports/research_orchestrator_v2" / OBJECTIVE_ID / "predictive_governance_decisions.jsonl"
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(json.dumps({
        "objective_id": OBJECTIVE_ID,
        "candidate_id": contract.candidate_id,
        "candidate_hash": contract.candidate_hash,
        "decision_status": "AUTHORIZED",
        "authorization_id": "AUTH_CONTROL_PLANE_V1",
        "decision_id": "DECISION_CONTROL_PLANE_V1",
    }) + "\n", encoding="utf-8")

    result = AutonomousResearchControlPlaneV1(root).inspect(OBJECTIVE_ID)

    assert result["decision"]["selected_action"]["action_type"] == "START_PREDICTIVE_TRIAL"
    assert result["decision"]["permission"]["permission"] == "DENY"
    assert result["decision"]["permission"]["reason_code"] == PHASE2_PREDICTIVE_EXECUTION_DISABLED
    assert not list((root / "data/research/research_factory/batches").glob("*/factory_trial_ledger.json"))
