"""合法生命周期前缀上的语义、身份与授权负向边界。"""
import json

import pytest

from p3c_scenario import Scenario
from chanlun_trader.research_factory.research_evolution_ai_design import ResearchEvolutionAIDesignError
from chanlun_trader.research_factory.candidate_generation import CandidateGenerationManagerV1, CandidateGenerationError
from chanlun_trader.research_factory.common import stable_hash


@pytest.mark.parametrize("field", ["decision_hash", "authorization_id", "preview_hash", "confirmation_token_hash", "structural_reconciliation_id", "objective_id", "candidate_id", "candidate_hash"])
def test_authorization_tamper_is_not_valid_even_when_trial_disabled(tmp_path, field):
    scenario = Scenario(tmp_path).initialize().ready()
    scenario.structural()
    scenario.authorize()
    assert scenario.plane.inspect(scenario.objective_id)["authorization"]["authorized"] is True
    path = tmp_path / f"reports/research_orchestrator_v2/{scenario.objective_id}/predictive_governance_decisions.jsonl"
    row = json.loads(path.read_text())
    row[field] = "TAMPERED"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert scenario.plane.inspect(scenario.objective_id)["authorization"]["authorized"] is False


def test_authorization_bound_to_original_budget_snapshot(tmp_path):
    from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
    scenario = Scenario(tmp_path).initialize().ready()
    scenario.structural()
    scenario.authorize()
    assert scenario.plane.inspect(scenario.objective_id)["authorization"]["authorized"] is True
    registry = SearchBudgetRegistryV1(scenario.objective_id, scenario.budget_path)
    registry.register_candidate("OTHER_SYNTHETIC_CANDIDATE", 1)
    assert scenario.plane.inspect(scenario.objective_id)["authorization"]["authorized"] is False


@pytest.mark.parametrize("field", ["schema_version", "decision_id", "confirmation_token_hash"])
def test_rehashing_authorization_does_not_replace_its_protocol_identity(tmp_path, field):
    scenario = Scenario(tmp_path).initialize().ready()
    scenario.structural()
    scenario.authorize()
    path = tmp_path / f"reports/research_orchestrator_v2/{scenario.objective_id}/predictive_governance_decisions.jsonl"
    row = json.loads(path.read_bytes())
    row[field] = "FORGED"
    row["decision_hash"] = stable_hash({key: value for key, value in row.items() if key != "decision_hash"})
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert scenario.plane.inspect(scenario.objective_id)["authorization"]["authorized"] is False


def test_new_explicit_authorization_after_defer_uses_current_budget(tmp_path):
    from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
    from chanlun_trader.research_factory.predictive_authorization import PredictiveGovernanceServiceV1
    scenario = Scenario(tmp_path).initialize().ready()
    scenario.structural()
    scenario.authorize("DEFER_PREDICTIVE_TRIAL")
    SearchBudgetRegistryV1(scenario.objective_id, scenario.budget_path).register_candidate("OTHER_SYNTHETIC_CANDIDATE", 1)
    service = PredictiveGovernanceServiceV1(tmp_path)
    preview = service.preview(scenario.objective_id)
    contract = scenario.proposal["durable_contract"]
    result = service.confirm(scenario.objective_id, {"confirmed": True, "decision_type": "AUTHORIZE_FIRST_PREDICTIVE_TRIAL", "candidate_id": contract["candidate_id"], "candidate_hash": contract["candidate_hash"], "authorization_id": "NEW_EXPLICIT_APPROVAL", "preview_hash": preview["preview_hash"], "confirmation_token": preview["confirmation_token"]})
    assert result["decision"]["decision_status"] == "AUTHORIZED"
    assert scenario.plane.inspect(scenario.objective_id)["authorization"]["authorized"] is True


@pytest.mark.parametrize("change", ["missing", "holding", "hypothesis", "mechanism", "factor", "objective", "version", "nested_outcome"])
def test_invalid_semantics_rejected_before_approval(tmp_path, change):
    scenario = Scenario(tmp_path).initialize()
    payload = scenario.design_input()
    if change == "missing":
        del payload["durable_contract"]
    elif change == "holding":
        payload["durable_contract"]["holding_period_trading_sessions"] = 9
    elif change == "hypothesis":
        payload["hypothesis"]["hypothesis_id"] = "WRONG"
    elif change == "mechanism":
        payload["research_hypothesis"] = "与结构声明冲突"
    elif change == "factor":
        payload["allowed_factors"] = ["ATR_14"]
    elif change == "objective":
        payload["durable_contract"]["policy_identity"]["objective_id"] = "WRONG"
    elif change == "version":
        payload["schema_version"] = "research-evolution-ai-design-v1"
    else:
        payload["durable_contract"]["source_provenance"]["nested"] = {"收益率": 0.5}
    with pytest.raises(ResearchEvolutionAIDesignError) as error:
        scenario.design(payload)
    assert error.value.code in {"AI_DESIGN_EXECUTABLE_SEMANTICS_INVALID", "AI_DESIGN_SEMANTIC_VERSION_REQUIRED", "OUTCOME_FIELD_BLOCKED"}
    assert not list(tmp_path.rglob("AI_DESIGN_APPROVAL_RECEIPT.json"))
    assert not list(tmp_path.rglob("CANDIDATE_PROPOSAL.json"))


@pytest.mark.parametrize("container", ["hypothesis", "source_provenance", "full_semantic_record"])
@pytest.mark.parametrize("field", ["win_rate", "sharpe", "net_return", "收益率"])
def test_nested_result_aliases_never_enter_approvable_design(tmp_path, container, field):
    scenario = Scenario(tmp_path).initialize()
    payload = scenario.design_input()
    target = payload["hypothesis"] if container == "hypothesis" else payload["durable_contract"][container]
    target["nested"] = [{field: "P3C_OUTCOME_SENTINEL_7488"}]
    with pytest.raises(ResearchEvolutionAIDesignError) as error:
        scenario.design(payload)
    assert error.value.code == "OUTCOME_FIELD_BLOCKED"
    result = scenario.plane.inspect(scenario.objective_id)
    assert "P3C_OUTCOME_SENTINEL_7488" not in json.dumps(result)
    assert not list(tmp_path.rglob("AI_RESEARCH_DESIGN_PROPOSAL.json"))


def test_legacy_lightweight_contract_keeps_identity_and_remains_non_executable(tmp_path):
    from chanlun_trader.research_factory.candidate_executable_materialization import CandidateExecutableMaterializationManagerV1, CandidateExecutableMaterializationError
    from test_candidate_generation_governance_v1 import _prepare
    scenario = Scenario(_prepare(tmp_path))
    scenario.plane.tick(scenario.objective_id)
    proposal = scenario.proposal
    expected = stable_hash(CandidateGenerationManagerV1._candidate_identity(proposal))
    assert CandidateGenerationManagerV1._candidate_hash_for_proposal(proposal) == expected
    original = next(tmp_path.rglob("CANDIDATE_PROPOSAL.json")).read_bytes()
    scenario.freeze()
    with pytest.raises(CandidateExecutableMaterializationError) as error:
        CandidateExecutableMaterializationManagerV1(tmp_path).create_preview(scenario.objective_id, proposal["proposal_id"])
    assert error.value.code == "EXECUTABLE_MATERIALIZATION_INCOMPLETE"
    assert next(tmp_path.rglob("CANDIDATE_PROPOSAL.json")).read_bytes() == original


@pytest.mark.parametrize("choice", ["DEFER_PREDICTIVE_TRIAL", "END_CANDIDATE_RESEARCH_DIRECTION"])
def test_explicit_non_authorizing_governance_never_authorizes_trial(tmp_path, choice):
    scenario = Scenario(tmp_path).initialize().ready()
    scenario.structural()
    before = scenario.budget_path.read_bytes()
    result = scenario.authorize(choice)
    assert result["decision"]["decision_status"] in {"DEFERRED", "ENDED"}
    assert scenario.plane.inspect(scenario.objective_id)["authorization"]["authorized"] is False
    assert scenario.budget_path.read_bytes() == before
    assert not list(tmp_path.rglob("factory_trial_ledger.json"))


@pytest.mark.parametrize("body", [{"confirmed": False}, {"candidate_id": "WRONG_CANDIDATE"}])
def test_structural_human_and_candidate_gates_precede_provider(tmp_path, body):
    from chanlun_trader.research_factory.structural_entry import StructuralEntryError
    scenario = Scenario(tmp_path).initialize().ready()
    budget = scenario.budget_path.read_bytes()
    with pytest.raises(StructuralEntryError):
        scenario.structural(**body)
    events = (tmp_path / "p3c-test-events.jsonl").read_text()
    assert "synthetic_structural_provider" not in events
    assert scenario.budget_path.read_bytes() == budget


@pytest.mark.parametrize("status", ["UNKNOWN", "INSUFFICIENT_SAMPLE"])
def test_non_pass_structural_evidence_cannot_reach_authorization(tmp_path, status):
    from chanlun_trader.research_factory.predictive_authorization import PredictiveGovernanceServiceV1, PredictiveGovernanceError
    scenario = Scenario(tmp_path).initialize().ready()
    scenario.structural(status)
    with pytest.raises(PredictiveGovernanceError) as error:
        PredictiveGovernanceServiceV1(tmp_path).readiness(scenario.objective_id)
    assert error.value.code == "PREDICTIVE_GOVERNANCE_NOT_FOUND"
    assert scenario.plane.inspect(scenario.objective_id)["authorization"]["authorized"] is False


@pytest.mark.parametrize("field", ["execution_contract", "factor_contract", "research_hypothesis", "mechanism_family", "hypothesis", "durable_contract"])
def test_rehashed_proposal_cannot_replace_approved_semantics(tmp_path, field):
    scenario = Scenario(tmp_path).initialize()
    scenario.design()
    scenario.approve()
    scenario.plane.tick(scenario.objective_id)
    proposal = scenario.proposal
    if field in {"research_hypothesis", "mechanism_family"}:
        proposal[field] = "CONFLICT"
    else:
        proposal[field] = {"conflict": True}
    proposal["proposal_hash"] = stable_hash(CandidateGenerationManagerV1._identity(proposal))
    proposal["proposal_id"] = "CANDIDATE_PROPOSAL_" + proposal["proposal_hash"][:24].upper()
    path = next(tmp_path.rglob("CANDIDATE_PROPOSAL.json"))
    path.write_text(json.dumps(proposal), encoding="utf-8")
    with pytest.raises(CandidateGenerationError):
        CandidateGenerationManagerV1(tmp_path).generate_proposal(scenario.objective_id)


@pytest.mark.parametrize("field", ["holding_period_trading_sessions", "full_semantic_record", "entry_predicate", "exit_contract", "fee_slippage_contract_references", "max_positions", "hypothesis_fingerprint"])
def test_approved_design_semantic_tamper_invalidates_receipt(tmp_path, field):
    from chanlun_trader.research_factory.ai_design_approval import AIDesignApprovalServiceV1
    from chanlun_trader.research_factory.research_evolution_ai_design import ai_design_identity
    scenario = Scenario(tmp_path).initialize()
    scenario.design()
    scenario.approve()
    path = next(tmp_path.rglob("AI_RESEARCH_DESIGN_PROPOSAL.json"))
    design = json.loads(path.read_bytes())
    old_hash = design["design_hash"]
    assert field in design["durable_contract"]
    design["durable_contract"][field] = "CHANGED_AFTER_APPROVAL"
    design["design_hash"] = stable_hash(ai_design_identity(design))
    assert design["design_hash"] != old_hash
    path.write_text(json.dumps(design), encoding="utf-8")
    assert AIDesignApprovalServiceV1(tmp_path).evaluate(scenario.objective_id)["candidate_generation_allowed"] is False
    assert not list(tmp_path.rglob("CANDIDATE_PROPOSAL.json"))
