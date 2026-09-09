"""合法生命周期前缀上的语义、身份与授权负向边界。"""
import json

import pytest

from p3c_scenario import Scenario
from chanlun_trader.research_factory.research_evolution_ai_design import ResearchEvolutionAIDesignError
from chanlun_trader.research_factory.candidate_generation import CandidateGenerationManagerV1, CandidateGenerationError
from chanlun_trader.research_factory.common import stable_hash


@pytest.mark.parametrize("choice", ["DEFER_PREDICTIVE_TRIAL", "END_CANDIDATE_RESEARCH_DIRECTION"])
def test_non_authorizing_status_rehash_fails_closed_in_real_chain(tmp_path, choice):
    from test_phase3c_restart_v1 import finish, launch, selected
    from test_restart_recovery_v1 import snapshot

    scenario = Scenario(tmp_path).initialize().ready()
    scenario.structural()
    scenario.authorize(choice)
    assert scenario.plane.inspect(scenario.objective_id)["authorization"]["authorized"] is False
    path = tmp_path / f"reports/research_orchestrator_v2/{scenario.objective_id}/predictive_governance_decisions.jsonl"
    original = json.loads(path.read_bytes())
    row = dict(original)
    row["decision_status"] = "AUTHORIZED"
    row["decision_hash"] = stable_hash({key: value for key, value in row.items() if key != "decision_hash"})
    assert {key for key in row if row[key] != original[key]} == {"decision_status", "decision_hash"}
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    before = snapshot(tmp_path)
    result = scenario.plane.inspect(scenario.objective_id)
    assert result["authorization"]["authorized"] is False, result["authorization"]
    assert result["authorization"]["reason_code"] == "PREDICTIVE_AUTHORIZATION_INVALID"
    assert selected(result) != "START_PREDICTIVE_TRIAL"
    assert scenario.plane.tick(scenario.objective_id, dry_run=True)["authorization"] == result["authorization"]
    assert snapshot(tmp_path) == before
    for operation in ("inspect", "dry_run"):
        restarted = finish(launch(scenario, operation))
        assert restarted["authorization"] == result["authorization"]
        assert selected(restarted) == selected(result)
        assert snapshot(tmp_path) == before
    canonical = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file() and p.suffix in {".json", ".jsonl"} and "research_control_plane" not in p.parts}
    ticked = scenario.plane.tick(scenario.objective_id)
    assert ticked["authorization"] == result["authorization"]
    assert selected(ticked) != "START_PREDICTIVE_TRIAL"
    assert ticked["execution"] is None
    assert all(p.read_bytes() == contents for p, contents in canonical.items())
    assert not list(tmp_path.rglob("factory_trial_ledger.json"))


@pytest.mark.parametrize("field,value", [
    ("decision_status", "DEFERRED"), ("decision_status", "ENDED"),
    ("decision_status", "UNKNOWN"), ("decision_status", None),
    ("decision_type", "DEFER_PREDICTIVE_TRIAL"),
    ("decision_type", "END_CANDIDATE_RESEARCH_DIRECTION"),
    ("decision_type", "UNKNOWN"), ("decision_type", None),
    ("decision_type", []),
    ("next_action", "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"),
    ("next_action", None), ("structural_status", "FAIL"),
    ("structural_status", None), ("objective_id", None),
    ("structural_reconciliation_id", None), ("governance_decision_id", None),
    ("authorization_id", None), ("preview_hash", None), ("candidate_hash", None),
])
def test_inconsistent_latest_decision_never_falls_back_to_authorization(tmp_path, field, value):
    from test_phase3c_restart_v1 import selected
    from test_restart_recovery_v1 import snapshot

    scenario = Scenario(tmp_path).initialize().ready()
    scenario.structural()
    scenario.authorize()
    valid = scenario.plane.inspect(scenario.objective_id)
    assert valid["authorization"]["authorized"] is True
    assert selected(valid) == "START_PREDICTIVE_TRIAL"
    assert valid["decision"]["permission"]["reason_code"] == "PHASE2_PREDICTIVE_EXECUTION_DISABLED"
    path = tmp_path / f"reports/research_orchestrator_v2/{scenario.objective_id}/predictive_governance_decisions.jsonl"
    history = path.read_bytes()
    row = json.loads(history)
    if value is None:
        del row[field]
    else:
        row[field] = value
    row["decision_hash"] = stable_hash({key: value for key, value in row.items() if key != "decision_hash"})
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    before = snapshot(tmp_path)
    result = scenario.plane.inspect(scenario.objective_id)
    assert result["authorization"]["authorized"] is False
    assert result["authorization"]["reason_code"] == "PREDICTIVE_AUTHORIZATION_INVALID"
    assert selected(result) != "START_PREDICTIVE_TRIAL"
    assert snapshot(tmp_path) == before
    assert path.read_bytes().startswith(history)
    assert not list(tmp_path.rglob("factory_trial_ledger.json"))


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
