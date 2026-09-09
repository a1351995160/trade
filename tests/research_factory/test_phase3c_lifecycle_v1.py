"""P3-C：通过真实领域服务验证冻结到物化的衔接，不补写成功工件。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.ai_design_approval import AIDesignApprovalServiceV1
from chanlun_trader.research_factory.autonomous_control_plane import AutonomousResearchControlPlaneV1
from chanlun_trader.research_factory.candidate_generation import CandidateGenerationManagerV1
from chanlun_trader.research_factory.candidate_executable_materialization import CandidateExecutableMaterializationError, CandidateExecutableMaterializationManagerV1
from chanlun_trader.research_factory.research_evolution_ai_design import ResearchEvolutionAIDesignServiceV1

from test_candidate_generation_governance_v1 import OBJECTIVE_ID, _fixture_root, _write_json


@pytest.mark.parametrize("initialize_materialization_metadata", [False, True])
def test_real_governance_freeze_can_reach_materialization_preview(tmp_path: Path, initialize_materialization_metadata: bool) -> None:
    # 仅复用没有 Design/Candidate/Freeze/Contract 的自包含初始数据。
    root = _fixture_root(tmp_path)
    if initialize_materialization_metadata:
        objective_path = root / "data/research/research_factory/objectives" / f"{OBJECTIVE_ID}.json"
        objective = json.loads(objective_path.read_bytes())
        objective.update({
            "batch_id": f"{OBJECTIVE_ID}_B01",
            "source_provenance": {"objective_id": OBJECTIVE_ID, "batch_id": f"{OBJECTIVE_ID}_B01", "source": "synthetic"},
            "factor_event_registry_identities": {"factor_registry": {"id": "P3C_SYNTHETIC_FACTORS"}, "event_registry": {"id": "P3C_SYNTHETIC_EVENTS"}},
            "research_period_identity": {"id": "P3C_SYNTHETIC_PERIOD", "start": 20200101, "end": 20251231},
            "policy_identity": {"objective_id": OBJECTIVE_ID, "policy_id": "P3C_SYNTHETIC_POLICY"},
        })
        _write_json(root, objective_path.relative_to(root).as_posix(), objective)
    budget_path = _write_json(root, f"data/research/research_factory/batches/{OBJECTIVE_ID}_B01/search_budget_registry.json", {
        "objective_id": OBJECTIVE_ID, "used": 0, "reserved": 0, "total": 4,
    })
    budget_before = budget_path.read_bytes()
    assert not (root / "reports/research_candidates").exists()
    plane = AutonomousResearchControlPlaneV1(root, execution_policy=ExecutionPolicy("GOVERNED", "SYNTHETIC"))
    ResearchEvolutionAIDesignServiceV1(root).generate_design(OBJECTIVE_ID)
    for _ in range(2):
        waiting = plane.tick(OBJECTIVE_ID)
        assert waiting["decision"]["selected_action"]["action_type"] == "WAIT_FOR_AI_DESIGN_CONFIRMATION"
        assert waiting["decision"]["permission"]["permission"] == "ALLOW_MANUAL_ONLY"
    AIDesignApprovalServiceV1(root).approve(OBJECTIVE_ID, "p3c-reviewer", idempotency_key="P3C_APPROVAL")
    generated = plane.tick(OBJECTIVE_ID)
    assert generated["execution"]["execution_status"] == "COMPLETED"
    proposal_path = root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "CANDIDATE_PROPOSAL.json"
    proposal_bytes = proposal_path.read_bytes()
    proposal = json.loads(proposal_bytes)
    manager = CandidateGenerationManagerV1(root)
    manager.review(proposal["proposal_id"], "approve", reviewer="p3c-reviewer", expected_proposal_hash=proposal["proposal_hash"])
    preview = manager.get_freeze_preview(proposal["proposal_id"])
    frozen = manager.freeze(proposal["proposal_id"], {
        "confirmed": True, "reviewer": "p3c-reviewer",
        "proposal_hash": proposal["proposal_hash"], "candidate_hash": preview["candidate_hash"],
    })
    assert frozen["proposal"]["status"] == "CANDIDATE_GOVERNANCE_FROZEN"
    print("P3C_REAL_FREEZE=" + json.dumps({key: frozen["candidate"][key] for key in ("candidate_id", "candidate_hash", "state")}, sort_keys=True))
    assert proposal_path.read_bytes() == proposal_bytes
    assert budget_path.read_bytes() == budget_before
    # 这是正向验收断言；禁止改成预期拒绝或 xfail 掩盖链路缺口。
    try:
        materialization = CandidateExecutableMaterializationManagerV1(root).create_preview(OBJECTIVE_ID, proposal["proposal_id"])
    except CandidateExecutableMaterializationError as exc:
        print("P3C_MATERIALIZATION_FAILURE=" + json.dumps({"code": exc.code, "details": exc.details}, sort_keys=True))
        raise
    assert materialization["status"] == "EXECUTABLE_MATERIALIZATION_PREVIEW_READY"
