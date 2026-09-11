"""正式服务创建 R2 目标，禁止复用旧 fixture 的目标补字段初始化。"""
import hashlib
import json

from chanlun_trader.research.validation_policy_v2 import load_validation_decision_policy_v2
from chanlun_trader.research_factory.objective_execution_binding import POLICY_PATH
from p3c_scenario import Scenario
from r1_caller_fixture import synthetic_factor_registry, materialized_fixture
from test_objective_execution_binding import setup_binding, approve, confirm
from test_research_proposal_governance_v1 import _write_json, OBJECTIVE_ID


def additional_formal_candidate(root, sessions, *, holding_period):
    """第二个明确预先设计的合成候选；新目标仍通过正式创建，不修改原目标。"""
    from chanlun_trader.execution_policy import ExecutionPolicy
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.objective_execution_binding import ResearchProposalGovernanceServiceV2
    from test_research_proposal_governance_v1 import _proposal, _confirm_payload, PROPOSAL_ID

    service = ResearchProposalGovernanceServiceV2(root, execution_policy=ExecutionPolicy("GOVERNED", "SYNTHETIC"))
    binding = service._load_preview(PROPOSAL_ID)["binding_evidence"]["execution_binding"]
    proposal = _proposal()
    proposal["proposal_id"] += "_SECOND"
    proposal["proposal_hash"] = stable_hash({k: v for k, v in proposal.items() if k != "proposal_hash"})
    _write_json(root, "reports/research_evolution/proposals/R3_SECOND_PROPOSAL.json", proposal)
    preview = service.review(proposal["proposal_id"], "approve", "synthetic-test-driver", execution_binding=binding)["objective_creation_preview"]
    receipt = service.confirm(proposal["proposal_id"], {**_confirm_payload(preview, confirmer="synthetic-test-driver"),
        "test_confirmation": True, "idempotency_key": "R3_SECOND_OBJECTIVE"})
    policy, _ = load_validation_decision_policy_v2(root / POLICY_PATH)
    return materialized_fixture(root, Scenario(root, receipt["objective_id"]), policy, sessions,
        validation_ready=True, holding_period=holding_period)


def formal_fixture(root, sessions):
    fixture, service, binding = setup_binding(root)
    parent = json.loads(fixture["objective_path"].read_bytes())
    parent["allowed_factor_scope"] = ["VOLUME_ACCEL"]
    _write_json(root, fixture["objective_path"].relative_to(root).as_posix(), parent)
    _write_json(root, f"reports/research_evolution/{OBJECTIVE_ID}/failure_landscape.json",
        {"schema_version": "research-failure-landscape-v1", "objective_id": OBJECTIVE_ID,
         "entries": [], "category_totals": {}, "read_only": True})
    _write_json(root, "reports/research_evolution/proposals/MECHANISM_COVERAGE_REGISTRY.json",
        {"schema_version": "mechanism-coverage-registry-v1", "coverage_id": "SYNTHETIC_COVERAGE",
         "covered": [], "unexplored": ["momentum"], "outcome_blind": True, "read_only": True})
    _write_json(root, "data/research/data_capability.json", {"datasets": [
        {"dataset_id": "daily_ohlcva_raw", "source": "SYNTHETIC_TEST_ONLY", "provider": "test-driver",
         "fields": ["date", "open", "high", "low", "close", "volume"], "frequency": "DAILY",
         "earliest_date": sessions[0], "latest_date": sessions[-1], "PIT_safe": True, "status": "READY",
         "data_version": "synthetic-v1", "event_time_semantics": "TRADE_DATE", "available_at_semantics": "T_CLOSE"}]})
    path = synthetic_factor_registry().write(root / "data/research/factor_library_v1/registry.json")
    binding["factor_event_registry_identities"]["factor_registry"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    binding["research_period_identity"] = {"id": "R2_FORMAL_SYNTHETIC", "start": sessions[2], "end": sessions[-1]}
    receipt = confirm(service, approve(service, binding))
    objective_id = receipt["objective_id"]
    objective_path = root / f"data/research/research_factory/objectives/{objective_id}.json"
    before = {path: path.read_bytes() for path in (objective_path, root / receipt["multiple_testing_family_ref"], root / receipt["budget_registry_ref"])}
    policy, _ = load_validation_decision_policy_v2(root / POLICY_PATH)
    result = materialized_fixture(root, Scenario(root, objective_id), policy, sessions, validation_ready=True)
    _write_json(root, "data/research/universe_policies/A_SHARE_RESEARCH_UNIVERSE_POLICY_V2.json",
        {"policy_id": "A_SHARE_RESEARCH_UNIVERSE_POLICY_V2", "domain": "SYNTHETIC_TEST_ONLY",
         "universe_rule": result[3].candidate.universe_rule,
         "sources": ["data/research/security_state/normalized/security_master_v2/records.json",
                     "data/research/security_state/raw/trade_calendar.json"]})
    assert all(path.read_bytes() == data for path, data in before.items())
    _write_json(root, "r2-formal-objective-creation-evidence.json", {"receipt": receipt,
        "unchanged_after_materialization": {path.relative_to(root).as_posix(): hashlib.sha256(data).hexdigest() for path, data in before.items()}})
    return result
