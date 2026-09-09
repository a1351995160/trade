"""P3-C 合法完整输入必须经过真实审批、冻结与物化。"""
import json

import pytest

from p3c_scenario import Scenario, OBJECTIVE_ID


@pytest.mark.parametrize("objective_id", [OBJECTIVE_ID, "P3C_SECOND_OBJECTIVE"])
def test_real_governance_freeze_can_reach_materialization_preview(tmp_path, objective_id):
    scenario = Scenario(tmp_path, objective_id).initialize()
    before = scenario.budget_path.read_bytes()
    scenario.ready()
    state = scenario.plane.inspect(objective_id)
    assert state["decision"]["selected_action"]["action_type"] == "RUN_STRUCTURAL_PREFLIGHT", state
    assert state["decision"]["permission"]["permission"] == "ALLOW_MANUAL_ONLY"
    assert scenario.budget_path.read_bytes() == before
    from chanlun_trader.research_factory.durability import DurableFrozenCandidateContractV1
    design = json.loads(next(tmp_path.rglob("AI_RESEARCH_DESIGN_PROPOSAL.json")).read_bytes())
    approval = json.loads(next(tmp_path.rglob("AI_DESIGN_APPROVAL_RECEIPT.json")).read_bytes())
    freeze = json.loads(next(tmp_path.rglob("CANDIDATE_FREEZE_RECEIPT.json")).read_bytes())
    contracts = json.loads(next(tmp_path.rglob("durable_frozen_candidate_contracts.json")).read_bytes())["contracts"]
    assert len(contracts) == 1
    contract = DurableFrozenCandidateContractV1.from_dict(contracts[0])
    contract.provider_candidate_payload()
    assert contract.full_semantic_record == design["durable_contract"]["full_semantic_record"] == scenario.proposal["durable_contract"]["full_semantic_record"]
    assert contract.candidate_hash == freeze["candidate_hash"] == contract.reconstruct_candidate().preregistration_hash
    hashes = {"design_hash": design["design_hash"], "approval_receipt_hash": approval["receipt_hash"], "proposal_hash": scenario.proposal["proposal_hash"], "candidate_hash": contract.candidate_hash, "durable_content_hash": contract.content_hash}
    assert len(set(hashes.values())) == len(hashes)
    print(json.dumps(hashes, sort_keys=True))


def test_full_lifecycle_reaches_valid_authorization_and_denies_trial(tmp_path):
    scenario = Scenario(tmp_path).initialize().ready()
    before = scenario.budget_path.read_bytes()
    structural = scenario.structural()
    assert structural["status"] == "PASS", structural
    waiting = scenario.plane.tick(scenario.objective_id)
    assert waiting["decision"]["selected_action"]["action_type"] == "WAIT_FOR_PREDICTIVE_AUTHORIZATION", waiting["decision"]
    authorized = scenario.authorize()
    assert authorized["decision"]["decision_status"] == "AUTHORIZED", authorized
    for _ in range(2):
        result = scenario.plane.tick(scenario.objective_id)
        assert result["decision"]["permission"]["reason_code"] == "PHASE2_PREDICTIVE_EXECUTION_DISABLED", result["decision"]
    assert scenario.budget_path.read_bytes() == before
