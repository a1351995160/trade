from __future__ import annotations

import hashlib
import json
from pathlib import Path

from chanlun_trader.research_console import ResearchConsoleReadService


ROOT = Path(__file__).resolve().parents[2]
OBJECTIVE = "RESEARCH_OBJECTIVE_GOVERNED_NEW_MECHANISM_V1_A8D9D0C881545ABC845C"
CANDIDATE = "CAND_LIQUIDITY_AMOUNT_ACCEL_LOW_ILLIQ_CROSS_SECTIONAL_V1"
CANDIDATE_HASH = "a1c2be3536e36bcb4eb430cde1781ddf8db869e01b0ab4fbbbba87ac9f9ee9fa"
DAEMON_ROOT = ROOT / "reports" / "research_daemon" / OBJECTIVE
ORCHESTRATOR_ROOT = ROOT / "reports" / "research_orchestrator_v2" / OBJECTIVE
CANONICAL = DAEMON_ROOT / "structural_preflight_reconciliation_canonical_v1.json"
HISTORY = DAEMON_ROOT / "structural_preflight_reconciliation_history.json"
CHECKPOINT = DAEMON_ROOT / "daemon_checkpoint.json"
GOVERNANCE = ORCHESTRATOR_ROOT / "structural_governance_decision_required.json"
BUDGET = ROOT / "data" / "research" / "research_factory" / "batches" / f"{OBJECTIVE}_B01" / "search_budget_registry.json"
CONTRACT = ROOT / "data" / "research" / "research_factory" / "batches" / "AI_HANDOFF_V2_31c72" / "durable_frozen_candidate_contracts.json"
GRAPH = ROOT / "data" / "research" / "research_factory" / "artifact_graph" / f"{OBJECTIVE}.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _trial_events() -> list[dict]:
    events: list[dict] = []
    for path in (ROOT / "data" / "research" / "research_factory" / "batches").glob("*/factory_trial_ledger.json"):
        payload = _read(path)
        events.extend(
            event
            for event in payload.get("events", ())
            if event.get("objective_id") == OBJECTIVE and event.get("candidate_id") == CANDIDATE
        )
    return events


def test_current_canonical_structural_pass_reconciliation_is_idempotent_after_predictive_trial_completion() -> None:
    canonical = _read(CANONICAL)
    history = _read(HISTORY)
    checkpoint = _read(CHECKPOINT)
    budget = _read(BUDGET)
    contract = next(item for item in _read(CONTRACT)["contracts"] if item.get("candidate_id") == CANDIDATE)
    graph = _read(GRAPH)

    assert checkpoint["current_state"] == "PREDICTIVE_COMPLETE"
    assert checkpoint["required_action"] == "PREDICTIVE_TRIAL_COMPLETED"
    assert checkpoint.get("current_trial", {}).get("trial_id", "").endswith("_T002")
    trial_events = _trial_events()
    assert len(trial_events) == 12
    assert trial_events[-1]["status"] == "COMPLETED"
    assert trial_events[-1]["classification"] == "BLOCKED"
    assert trial_events[-1]["performance_accessed"] is True
    assert trial_events[-1]["performance_complete"] is True
    assert trial_events[-1]["final_adjudicated"] is True
    assert trial_events[-1]["registry_committed"] is True
    assert canonical["status"] == "PASS"
    assert canonical["objective_id"] == OBJECTIVE
    assert canonical["candidate_id"] == CANDIDATE
    assert canonical["candidate_hash"] == CANDIDATE_HASH
    assert canonical["lower_bound"] == 158
    assert canonical["upper_bound"] == 298
    assert canonical["minimum_required"] == 30
    assert canonical["lower_bound_integrity"] == "PASS"
    assert canonical["safety_evidence"]["new_predictive_trials"] == 0
    assert canonical["safety_evidence"]["new_performance_access"] == 0
    assert canonical["safety_evidence"]["performance_data_loaded"] is False
    assert canonical["safety_evidence"]["final_test_access"] == {"analytical": 0, "decision": 0, "physical": 0}
    assert history["latest_reconciliation_id"] == canonical["reconciliation_id"]
    assert history["generations"][-1]["sha256"] == _sha256(CANONICAL)
    assert contract["candidate_hash"] == CANDIDATE_HASH
    assert contract["policy_identity"]["objective_id"] == OBJECTIVE
    assert budget["objective_id"] == OBJECTIVE
    assert all(bucket["used"] == 2 and bucket["reserved"] == 0 for bucket in budget["buckets"])
    assert {bucket["remaining"] for bucket in budget["buckets"]} == {4}
    assert len(graph["nodes"]) == 5
    assert len(graph["edges"]) == 4

def test_current_structural_pass_is_projected_to_the_predictive_governance_boundary() -> None:
    service = ResearchConsoleReadService(ROOT)
    structural = service.get_structural(OBJECTIVE, CANDIDATE).to_dict()
    dashboard = service.get_dashboard(OBJECTIVE).to_dict()
    pipeline = service.get_pipeline(OBJECTIVE).to_dict()
    governance = service.get_governance_decision(OBJECTIVE)
    reports = service.get_reports(OBJECTIVE).to_dict()

    assert structural["status"] == "PASS"
    assert structural["lower_bound"] == 158
    assert structural["upper_bound"] == 298
    assert structural["minimum_required"] == 30
    assert structural["performance_data_loaded"] is False

    assert dashboard["engineering_blocked"] is False
    assert dashboard["orchestrator_state"] == "ACTIVE"
    assert dashboard["orchestrator_state_zh"] == "预测验证已完成，最终分类：预测验证未通过"
    assert dashboard["stage"] == "FINAL_CLASSIFICATION"
    assert dashboard["required_human_action"] == "CANDIDATE_PREDICTIVE_VALIDATION_COMPLETE"
    assert dashboard["predictive_trial_count"] == 2
    assert dashboard["structural"]["lower_bound"] == 158
    assert dashboard["structural"]["upper_bound"] == 298
    assert dashboard["governance_readiness"]["status"] == "COMPLETED"
    assert dashboard["governance_readiness"]["available"] is False

    assert pipeline["current_stage"] == "FINAL_CLASSIFICATION"
    assert pipeline["current_state"] == "ACTIVE"
    assert pipeline["next_action"] == "CANDIDATE_PREDICTIVE_VALIDATION_COMPLETE"
    assert "最终分类" in pipeline["next_action_zh"]
    assert pipeline["trial_count"] == 2
    assert pipeline["execution"]["status"] == "COMPLETED"
    assert pipeline["execution"]["structural_status"] == "PASS"
    assert pipeline["execution"]["predictive_status"] == "COMPLETED"
    assert pipeline["execution"]["predictive_classification"] == "BLOCKED"
    assert pipeline["predictive_authorization"]["status"] == "COMPLETED"
    assert pipeline["predictive_authorization"]["available"] is False
    assert pipeline["predictive_authorization"]["scope"] == "ONE_FROZEN_CANDIDATE_GOVERNANCE_ONLY"
    assert pipeline["predictive_trial_start"]["available"] is False
    assert pipeline["predictive_trial_recovery"]["available"] is False
    assert pipeline["governance_readiness"]["status"] == "COMPLETED"

    assert governance["status"] == "AUTHORIZED"
    assert governance["structural_governance"]["candidate_id"] == CANDIDATE
    assert governance["structural_governance"]["candidate_hash"] == CANDIDATE_HASH
    assert governance["structural_governance"]["structural"] == {
        "lower_bound": 158,
        "lower_bound_integrity": "PASS",
        "minimum_required": 30,
        "status": "PASS",
        "upper_bound": 298,
    }
    assert governance["new_objective_created"] is False
    assert governance["new_budget_created"] is False
    assert governance["final_test_access"] == {"analytical": 0, "decision": 0, "physical": 0}
    assert governance["prospective"] == "DISABLED"
    assert governance["real_order"] == "DISABLED"

    report_paths = {item["relative_path"] for item in reports["reports"]}
    assert f"reports/research_daemon/{OBJECTIVE}/structural_preflight_reconciliation_canonical_v1.json" in report_paths
    assert f"reports/research_orchestrator_v2/{OBJECTIVE}/structural_governance_decision_required.json" in report_paths
    assert "reports/STRUCTURAL_PASS_59_SAMPLE_REPRODUCIBILITY_V1.json" not in report_paths
    assert "reports/STRUCTURAL_PASS_NON_BINDING_1307_AUDIT_V1.json" not in report_paths
