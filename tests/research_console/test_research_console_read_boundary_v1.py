from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from chanlun_trader.presentation import display_state
from chanlun_trader.research_console import (
    FreshnessState,
    ResearchConsoleReadError,
    ResearchConsoleReadService,
)
from chanlun_trader.research_factory.context import PerformanceBlindGuard
from chanlun_trader.research_factory.common import stable_hash


OBJECTIVE = "RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1"
OTHER_OBJECTIVE = "CODEX_GUIDED_AUTONOMOUS_RESEARCH_PILOT_V1_OBJECTIVE"
CANDIDATE_ID = "CAND_TEST_001"
TRIAL_ID = "TRIAL_TEST_001"


def _write(root: Path, relative: str, value: object) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def console_fixture(tmp_path: Path) -> ResearchConsoleReadService:
    _write(
        tmp_path,
        f"data/research/research_factory/objectives/{OBJECTIVE}.json",
        {
            "objective_id": OBJECTIVE,
            "objective_name": "测试 objective",
            "max_total_trials": 3,
            "max_batches": 1,
            "risk_constraints": {"prospective_access": "DISABLED", "real_order_execution": "DISABLED"},
        },
    )
    _write(
        tmp_path,
        f"data/research/research_factory/objectives/{OTHER_OBJECTIVE}.json",
        {"objective_id": OTHER_OBJECTIVE, "max_total_trials": 9, "max_batches": 2},
    )
    budget_path = _write(
        tmp_path,
        "data/research/research_factory/batches/B1/search_budget_registry.json",
        {
            "schema_version": "search-budget-registry-v1",
            "objective_id": OBJECTIVE,
            "buckets": [
                {"kind": "objective", "key": OBJECTIVE, "limit": 3, "used": 1, "reserved": 0},
                {"kind": "batch", "key": "B1", "limit": 3, "used": 1, "reserved": 0},
            ],
            "active_reservations": {},
            "settled_reservations": {"TRIAL-TEST": "CONSUMED"},
            "reservation_counter": 1,
            "updated_at": "2026-01-02T11:59:00+00:00",
        },
    )
    contract = {
        "candidate_id": CANDIDATE_ID,
        "candidate_hash": "HASH_TEST_001",
        "family": "RELATIVE_STRENGTH",
        "mechanism": "TREND_LOCATION",
        "factor_ids": ["F_TEST"],
        "factor_directions": {"F_TEST": "LONG"},
        "hypothesis_id": "H_TEST_001",
        "holding_period_trading_sessions": 5,
        "execution_contract_version": "execution-v1",
        "entry_timing": {"kind": "CLOSE"},
        "selection_rule": {"name": "top"},
        "research_period_identity": {"id": "RP_TEST", "start": "2020-01-01", "end": "2024-12-31"},
        "pit_dependencies": {"required_data": ["TDX"], "pit_requirements": ["trade_calendar"]},
        "policy_identity": {"objective_id": OBJECTIVE, "policy_hash": "POLICY_TEST"},
        "source_provenance": {"batch_id": "B1", "run_id": "R1", "artifact_refs": ["reports/test.json"]},
        "created_frozen_timestamp": "2026-01-01T12:00:00+00:00",
        "full_semantic_record": {"candidate": {"description": "测试候选说明"}},
    }
    _write(tmp_path, "data/research/research_factory/batches/B1/durable_frozen_candidate_contracts.json", {"contracts": [contract]})
    _write(
        tmp_path,
        "data/research/research_factory/batches/B1/factory_trial_ledger.json",
        {
            "events": [
                {"trial_id": TRIAL_ID, "objective_id": OBJECTIVE, "candidate_id": CANDIDATE_ID, "status": "REGISTERED", "created_at": "2026-01-01T12:01:00+00:00"},
                {
                    "trial_id": TRIAL_ID,
                    "objective_id": OBJECTIVE,
                    "batch_id": "B1",
                    "candidate_id": CANDIDATE_ID,
                    "candidate_hash": "HASH_TEST_001",
                    "family_id": "RELATIVE_STRENGTH",
                    "status": "COMPLETED",
                    "classification": "PROMISING",
                    "reason_codes": ["TEST_COMPLETED"],
                    "performance_accessed": True,
                    "performance_complete": True,
                    "final_adjudicated": True,
                    "registry_committed": True,
                    "budget_reservation_identity": "RES_TEST_001",
                    "created_at": "2026-01-02T11:00:00+00:00",
                },
            ]
        },
    )
    daemon_dir = f"reports/research_daemon/{OBJECTIVE}"
    _write(
        tmp_path,
        f"{daemon_dir}/daemon_status.json",
        {
            "generated_at": "2026-01-02T12:00:00+00:00",
            "objective_id": OBJECTIVE,
            "daemon_state": "STRUCTURAL_RUNNING",
            "stage": "STRUCTURAL_RUNNING",
            "current_candidate_id": CANDIDATE_ID,
            "remaining_frozen_candidates": 2,
            "required_human_ai_action": None,
            "process_pid": 1234,
            "process_rss_bytes": 100,
            "system_available_memory_bytes": 2_000_000_000,
            "last_checkpoint_time": "2026-01-02T12:00:00+00:00",
            "last_error": None,
            "retry_safe": True,
            "daemon_run_id": "RUN_TEST_001",
            "budget": {"registry_path": budget_path.relative_to(tmp_path).as_posix()},
            "research_counts": {"remaining_frozen_candidates": 2},
        },
    )
    _write(
        tmp_path,
        f"{daemon_dir}/daemon_checkpoint.json",
        {"checkpoint_at": "2026-01-02T12:00:00+00:00", "daemon_state": "STRUCTURAL_RUNNING", "stage": "STRUCTURAL_RUNNING"},
    )
    _write(
        tmp_path,
        f"{daemon_dir}/daemon_events.jsonl",
        {"timestamp": "2026-01-02T11:59:00+00:00", "event_type": "CHECKPOINT_WRITTEN", "new_state": "STRUCTURAL_RUNNING"},
    )
    _write(tmp_path, f"{daemon_dir}/daemon_telemetry.jsonl", {"timestamp": "2026-01-02T11:59:30+00:00", "rss_bytes": 100})
    _write(
        tmp_path,
        "reports/RESEARCH_DAEMON_HANDOFF_CURRENT.json",
        {
            "generated_at": "2026-01-01T10:00:00+00:00",
            "objective": {"objective_id": OBJECTIVE},
            "handoff_type": "ENGINEERING_BLOCKED",
            "current_state": "ENGINEERING_BLOCKED",
            "blocking_reason": {"reason_code": "WAITING_ENGINEERING_FIX", "artifact_refs": ["reports/test.json"]},
            "remaining_candidate_state": {"current_candidate": {"candidate_id": "STALE_CANDIDATE", "candidate_hash": "STALE_HASH"}, "remaining_frozen_candidates": 4},
            "allowed_next_actions": ["INSPECT"],
            "forbidden_actions": ["START_NEW_RUN"],
            "budget": {"used": 1, "remaining": 2},
        },
    )
    _write(tmp_path, "reports/CURRENT_EFFECTIVE_STRATEGY_REGISTRY_V1.json", {"records": [{"candidate_id": CANDIDATE_ID, "current_effective_classification": "PROMISING"}]})
    _write(
        tmp_path,
        "reports/CURRENT_CANDIDATE_SAMPLE_FEASIBILITY_V2.json",
        {
            "candidate": {"candidate_id": CANDIDATE_ID, "candidate_hash": "HASH_TEST_001"},
            "v1_result": {"qualified_signal_count": 10, "selected_opportunity_count": 4, "portfolio_feasible_opportunity_count": 3, "lower_bound_count": 3, "upper_bound_count": 5, "minimum_required_count": 2, "data_complete_count": 10, "execution_eligible_count": 3, "outcome_blind": True, "performance_data_loaded": False},
            "v2_result": {"status": "QUALIFIED", "outcome_blind": True, "performance_data_loaded": False, "reason_codes": ["STRUCTURAL_OK"]},
        },
    )
    _write(tmp_path, "reports/CURRENT_EFFECTIVE_RESEARCH_HISTORY_V1.json", {"final_test_access": {"analytical": 0, "decision": 0, "physical": 0}, "rows": []})
    _write(tmp_path, "reports/research_daemon/RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1/predictive/R1/CAND_TEST_001/artifact_graph.json", {"nodes": [{"node_id": f"candidate:{CANDIDATE_ID}", "node_type": "Candidate", "payload_hash": "GRAPH_HASH", "created_at": "2026-01-02T11:00:00+00:00"}], "edges": []})
    _write(tmp_path, "reports/research_daemon/RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1/predictive/R1/CAND_TEST_001/failure_extraction.json", {"schema_version": "failure-knowledge-snapshot-v1", "snapshot_id": "FAILURE_TEST", "created_at": "2026-01-02T11:00:00+00:00", "entries": [{"category": "SAMPLE_FAILURE", "mechanism": "test", "high_level_reason": "TEST_REASON", "reason_code": "TEST_REASON", "constraints": ["do_not_retune_same_batch"], "source_trial_ids": [TRIAL_ID]}]})
    _write(
        tmp_path,
        "reports/EOD_RESEARCH_STOCK_SCAN_20260102.json",
        {"SCAN_DATE": 20260102, "SCAN_STATUS": "READY", "STRATEGIES_SCANNED": 1, "RAW_SIGNAL_COUNT": 2, "UNIQUE_CANDIDATE_COUNT": 1, "TOP_OBSERVATION_3": [{"symbol": "000001", "name": "测试", "matched_strategy": "CAND_TEST_001"}], "RECOMMENDATION_STATUS": "SHADOW_RESEARCH_ONLY"},
    )
    _write(tmp_path, "reports/SHADOW_SCAN_READINESS_20260102.json", {"readiness": "READY", "reason": "PIT_VERIFIED"})
    _write(tmp_path, "data/research/data_capability.json", {"generated_at": "2026-01-02T11:00:00+00:00", "datasets": [{"dataset_id": "TDX", "provider": "TDX", "status": "READY", "latest_date": 20260102}]})
    _write(tmp_path, "reports/PLATFORM_DATA_SOURCE_CAPABILITY_MATRIX_V2.json", {"generated_at": "2026-01-02T11:00:00+00:00", "sources": [{"source": "BaoStock", "role": "historical research data", "status": "PARTIAL", "canonical": False, "fallback": True}]})
    _write(tmp_path, "reports/PLATFORM_ARCHITECTURE_MANIFEST_V2.json", {"safety": {"NEW_PREDICTIVE_TRIALS": 0, "PREDICTIVE_BUDGET_DELTA": 0, "PERFORMANCE_ACCESS": 0, "FINAL_TEST_ACCESS": {"analytical": 0, "decision": 0, "physical": 0}, "PROSPECTIVE": 0, "REAL_ORDER": "DISABLED"}})
    _write(tmp_path, "reports/SAMPLE_FEASIBILITY_POLICY_V2.json", {"policy_identity": {"policy_id": "SAMPLE_V2", "policy_version": "2", "policy_hash": "SAMPLE_HASH"}})
    _write(tmp_path, "data/research/strategy_validation/validation_decision_policy_v2.lock.json", {"policy_id": "VALIDATION_V2", "policy_version": "2", "policy_hash": "VALIDATION_HASH"})
    _write(tmp_path, "RESEARCH_PLATFORM_V1_FREEZE.json", {"freeze_id": "FREEZE_TEST"})
    _write(tmp_path, "docs/项目人类可读输出规范_V1.md", "测试报告")
    _write(tmp_path, "reports/research_daemon/RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1/predictive/TRIAL_TEST_001/validation_results.json", {"trial_id": TRIAL_ID, "rows": [{"return": 0.1, "profit_factor": 1.2, "win_rate": 0.6, "drawdown": 0.05, "bootstrap_p_value": 0.01}]})
    _write(tmp_path, "reports/research_daemon/RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1/predictive/TRIAL_TEST_001/multiple_testing.json", {"trial_id": TRIAL_ID, "adjusted_p": 0.02})
    _write(tmp_path, "reports/research_daemon/RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1/predictive/TRIAL_TEST_001/final_status.json", {"trial_id": TRIAL_ID, "status": "COMPLETED", "classification": "PROMISING"})
    return ResearchConsoleReadService(tmp_path, clock=lambda: datetime(2026, 1, 2, 12, 1, tzinfo=timezone.utc), cache_max_entries=16)


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {str(key).casefold() for key in value} | {item for child in value.values() for item in _keys(child)}
    if isinstance(value, (list, tuple)):
        return {item for child in value for item in _keys(child)}
    return set()


def test_latest_daemon_evidence_wins_over_stale_handoff(console_fixture: ResearchConsoleReadService) -> None:
    daemon = console_fixture.get_daemon(OBJECTIVE)
    handoff = console_fixture.get_handoff(OBJECTIVE)
    assert daemon.daemon_state == "STRUCTURAL_RUNNING"
    assert daemon.current_candidate_id == CANDIDATE_ID
    assert daemon.handoff_stale is True
    assert handoff.current_state == "ENGINEERING_BLOCKED"
    assert handoff.stale is True
    assert handoff.conflict is True
    assert handoff.freshness_state == FreshnessState.STALE.value


def test_objective_scope_and_unknown_objective(console_fixture: ResearchConsoleReadService) -> None:
    assert console_fixture.list_candidates(OBJECTIVE)["total"] == 1
    assert console_fixture.list_trials(OBJECTIVE)["total"] == 1
    with pytest.raises(ResearchConsoleReadError) as error:
        console_fixture.get_daemon("UNKNOWN_OBJECTIVE")
    assert error.value.code == "UNKNOWN_OBJECTIVE"
    assert error.value.status_code == 404


def test_candidate_and_trial_identity_and_completed_visibility(console_fixture: ResearchConsoleReadService) -> None:
    candidate = console_fixture.get_candidate(OBJECTIVE, CANDIDATE_ID).to_dict()
    trial = console_fixture.get_trial(OBJECTIVE, TRIAL_ID).to_dict()
    trial_summary = console_fixture.list_trials(OBJECTIVE)["items"][0]
    assert candidate["candidate_id"] == CANDIDATE_ID
    assert candidate["identity"]["candidate_id"] == CANDIDATE_ID
    assert candidate["artifact_lineage"]["artifact_graphs"][0]["integrity"]["status"] == "PASS"
    assert candidate["artifact_lineage"]["failure_knowledge"][0]["exact_performance_values_exposed"] is False
    assert trial["trial_id"] == TRIAL_ID
    assert trial_summary["trial_id"] == TRIAL_ID
    assert trial_summary["registered"] is True
    assert trial_summary["reserved"] is True
    assert trial_summary["completed"] is True
    assert trial_summary["reconciled"] is True


def test_canonical_generated_long_trial_id_remains_readable(console_fixture: ResearchConsoleReadService) -> None:
    ledger_path = console_fixture.root / "data/research/research_factory/batches/B1/factory_trial_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    long_trial_id = f"{OBJECTIVE}_B01_{'a' * 96}_T001"
    assert 128 < len(long_trial_id) <= 255
    for event in ledger["events"]:
        event["trial_id"] = long_trial_id
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")

    fresh_console = ResearchConsoleReadService(console_fixture.root)
    assert fresh_console.get_trial(OBJECTIVE, long_trial_id).trial_id == long_trial_id


def test_budget_is_canonical_and_dashboard_is_read_only(console_fixture: ResearchConsoleReadService) -> None:
    path = console_fixture.root / "data/research/research_factory/batches/B1/search_budget_registry.json"
    before = path.read_bytes()
    budget = console_fixture.get_budget(OBJECTIVE)
    dashboard = console_fixture.get_dashboard(OBJECTIVE)
    assert (budget.used, budget.total, budget.remaining, budget.reserved) == (1, 3, 2, 0)
    assert dashboard.budget_used == 1
    assert path.read_bytes() == before


def test_structural_and_no_outcome_views_are_performance_blind(console_fixture: ResearchConsoleReadService) -> None:
    structural = console_fixture.get_structural(OBJECTIVE, CANDIDATE_ID).to_dict()
    handoff = console_fixture.get_handoff(OBJECTIVE).to_dict()
    forbidden = {"return", "pnl", "profit_factor", "win_rate", "drawdown", "bootstrap_p_value", "adjusted_p"}
    assert not (_keys(structural) & forbidden)
    assert structural["performance_data_loaded"] is False
    PerformanceBlindGuard.assert_blind(handoff)
    assert not (_keys(handoff) & forbidden)


def test_no_outcome_source_leak_fails_closed_with_safe_error(console_fixture: ResearchConsoleReadService) -> None:
    path = console_fixture.root / "reports/RESEARCH_DAEMON_HANDOFF_CURRENT.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["budget"]["return"] = 0.1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ResearchConsoleReadError) as error:
        console_fixture.get_handoff(OBJECTIVE)
    assert error.value.code == "OUTCOME_FIELD_BLOCKED"
    assert error.value.status_code == 503


def test_human_trial_read_does_not_mutate_performance_access(console_fixture: ResearchConsoleReadService) -> None:
    ledger = console_fixture.root / "data/research/research_factory/batches/B1/factory_trial_ledger.json"
    before = ledger.read_bytes()
    detail = console_fixture.get_trial(OBJECTIVE, TRIAL_ID)
    assert detail.performance["return"] == 0.1
    assert ledger.read_bytes() == before


def test_shadow_marker_and_chinese_projection(console_fixture: ResearchConsoleReadService) -> None:
    _write(
        console_fixture.root,
        "reports/EOD_RESEARCH_STOCK_SCAN_20260102_STRATEGY_BREAKDOWN.json",
        {"SCAN_DATE": 20260102, "SCAN_STATUS": "FAKE", "RAW_SIGNAL_COUNT": 999},
    )
    shadow = console_fixture.get_shadow_latest(OBJECTIVE).to_dict()
    daemon = console_fixture.get_daemon(OBJECTIVE).to_dict()
    assert shadow["source_id"] == "EOD_RESEARCH_STOCK_SCAN_20260102.json"
    assert shadow["machine_marker"] == "SHADOW_RESEARCH_ONLY"
    assert shadow["warning_zh"] == "仅供研究观察，不代表买入建议。"
    assert shadow["objective_id"] == OBJECTIVE
    assert shadow["available"] is False
    assert shadow["objective_scoped"] is False
    assert daemon["daemon_state"] == "STRUCTURAL_RUNNING"
    assert daemon["state_display_zh"] == display_state("STRUCTURAL_RUNNING")


def test_reports_do_not_expose_mismatched_global_objective(console_fixture: ResearchConsoleReadService) -> None:
    _write(
        console_fixture.root,
        "reports/RESEARCH_GOVERNANCE_DECISION_REQUIRED.json",
        {"objective_id": OTHER_OBJECTIVE, "decision_id": "OTHER_DECISION"},
    )

    reports = console_fixture.get_reports(OBJECTIVE).to_dict()["reports"]

    assert all(item["relative_path"] != "reports/RESEARCH_GOVERNANCE_DECISION_REQUIRED.json" for item in reports)


def test_ai_handoff_is_not_exposed_as_daemon_handoff(tmp_path: Path) -> None:
    objective_id = "OBJECTIVE_AI_HANDOFF_SCOPE_TEST"
    _write(tmp_path, f"data/research/research_factory/objectives/{objective_id}.json", {"objective_id": objective_id})
    _write(
        tmp_path,
        f"reports/research_orchestrator_v2/{objective_id}/ai_handoff.json",
        {"schema_version": "research-orchestrator-ai-handoff-v2", "objective_id": objective_id, "handoff_id": "AI_HANDOFF_TEST"},
    )

    handoff = ResearchConsoleReadService(tmp_path).get_handoff(objective_id).to_dict()

    assert handoff["handoff_type"] == "UNAVAILABLE"
    assert handoff["current_state"] == "UNAVAILABLE"
    assert handoff["reason_code"] == "DAEMON_HANDOFF_NOT_AVAILABLE"
    assert handoff["performance_values_exposed"] is False


def test_pipeline_uses_canonical_orchestrator_state(tmp_path: Path) -> None:
    objective_id = "OBJECTIVE_PIPELINE_STATE_SCOPE_TEST"
    objective_path = _write(tmp_path, f"data/research/research_factory/objectives/{objective_id}.json", {"objective_id": objective_id, "max_total_trials": 1})
    _write(
        tmp_path,
        f"data/research/research_factory/batches/{objective_id}_B01/search_budget_registry.json",
        {"objective_id": objective_id, "buckets": [{"kind": "objective", "key": objective_id, "limit": 1, "used": 0, "reserved": 0}], "active_reservations": {}, "settled_reservations": {}},
    )
    _write(
        tmp_path,
        f"reports/research_daemon/{objective_id}/daemon_status.json",
        {"objective_id": objective_id, "daemon_state": "READY", "stage": "READY", "current_candidate_id": "STALE_DAEMON_CANDIDATE", "generated_at": "2026-01-02T12:00:00+00:00"},
    )
    _write(
        tmp_path,
        f"reports/research_orchestrator_v2/{objective_id}/orchestrator_checkpoint.json",
        {"objective_id": objective_id, "objective_hash": hashlib.sha256(objective_path.read_bytes()).hexdigest(), "state": "AI_INVOCATION_PENDING", "updated_at": "2026-01-02T12:00:00+00:00"},
    )

    pipeline = ResearchConsoleReadService(tmp_path).get_pipeline(objective_id).to_dict()

    assert pipeline["current_state"] == "AI_INVOCATION_PENDING"
    assert pipeline["current_stage"] == "HYPOTHESIS"
    assert pipeline["state_source"] == "canonical Orchestrator checkpoint"
    assert pipeline["current_candidate_id"] is None


def test_pipeline_projects_completed_structural_failure_and_stopped_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    objective_id = "OBJECTIVE_PIPELINE_STRUCTURAL_FAILURE"
    candidate_id = "CANDIDATE_STRUCTURAL_FAILURE"
    _write(tmp_path, f"data/research/research_factory/objectives/{objective_id}.json", {"objective_id": objective_id})
    _write(
        tmp_path,
        f"data/research/research_factory/batches/B1/durable_frozen_candidate_contracts.json",
        {"contracts": [{"candidate_id": candidate_id, "candidate_hash": "HASH_FAILURE", "policy_identity": {"objective_id": objective_id}, "source_provenance": {"batch_id": "B1"}}]},
    )
    _write(
        tmp_path,
        f"reports/research_daemon/{objective_id}/daemon_status.json",
        {"objective_id": objective_id, "daemon_state": "READY", "stage": "READY", "generated_at": "2026-01-02T12:00:00+00:00"},
    )
    _write(
        tmp_path,
        f"reports/research_daemon/{objective_id}/daemon_checkpoint.json",
        {
            "objective_id": objective_id,
            "current_state": "READY",
            "checkpoint_at": "2026-01-02T12:01:00+00:00",
            "last_completed_candidate": {"candidate_id": candidate_id, "candidate_hash": "HASH_FAILURE"},
            "canonical_refs": {
                "last_structural_result": {
                    "status": "UNKNOWN",
                    "reason_code": "D_LOWER_BOUND_INTEGRITY_NOT_ESTABLISHED_FAIL_CLOSED",
                    "details": {
                        "candidate_id": candidate_id,
                        "candidate_hash": "HASH_FAILURE",
                        "blocking_reason_codes": ["LOWER_BOUND_INTEGRITY_UNVERIFIED"],
                        "generated_at": "2026-01-02T12:01:00+00:00",
                    },
                }
            },
        },
    )
    service = ResearchConsoleReadService(tmp_path)
    monkeypatch.setattr(
        service,
        "get_orchestrator",
        lambda _objective_id: {
            "orchestrator_state": "ACTIVE",
            "orchestrator_state_zh": "研究编排器已就绪",
            "terminal_reason": "READY_FOR_NEXT_CANDIDATE",
            "validation": {"process_alive": False, "process_id": 999999},
            "source_generated_at": "2026-01-02T12:01:00+00:00",
        },
    )

    pipeline = service.get_pipeline(objective_id).to_dict()
    candidate = service.list_candidates(objective_id)["items"][0]

    assert pipeline["current_stage"] != "STRUCTURAL"
    assert pipeline["current_stage"] != "PREDICTIVE"
    assert pipeline["structural_reconciliation"]["available"] is False
    assert pipeline["predictive_authorization"]["available"] is False
    assert candidate["structural_status"] == "UNKNOWN"
    assert candidate["pipeline_state"] == "UNKNOWN"
    assert candidate["reasons"] == []

    _write(
        tmp_path,
        f"reports/research_daemon/{objective_id}/daemon_checkpoint.json",
        {
            "objective_id": objective_id,
            "current_state": "READY",
            "checkpoint_at": "2026-01-02T12:02:00+00:00",
            "required_action": "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED",
            "last_completed_candidate": {"candidate_id": candidate_id, "candidate_hash": "HASH_FAILURE"},
            "canonical_refs": {
                "last_structural_result": {
                    "status": "PASS",
                    "reason_code": "B_VALID_LOWER_BOUND_AT_OR_ABOVE_MINIMUM",
                    "details": {
                        "candidate_id": candidate_id,
                        "candidate_hash": "HASH_FAILURE",
                        "blocking_reason_codes": [],
                        "lower_bound_count": 40,
                        "minimum_required_count": 30,
                        "generated_at": "2026-01-02T12:02:00+00:00",
                    },
                }
            },
        },
    )
    passed_service = ResearchConsoleReadService(tmp_path)
    monkeypatch.setattr(passed_service, "get_orchestrator", service.get_orchestrator)
    passed_pipeline = passed_service.get_pipeline(objective_id).to_dict()

    # A daemon checkpoint can be stale or forged; it cannot promote a
    # Structural PASS without the canonical reconciled result artifact.
    assert passed_pipeline["current_stage"] != "PREDICTIVE"
    assert passed_pipeline["execution"]["structural_status"] == "NOT_RUN"
    assert passed_pipeline["execution"]["predictive_status"] == "NOT_RUN"
    assert passed_pipeline["next_action"] != "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
    assert passed_pipeline["structural_reconciliation"]["status"] == "UNAVAILABLE"
    assert passed_pipeline["structural_reconciliation"]["available"] is False
    assert passed_pipeline["predictive_authorization"]["available"] is False

    completed_trial_path = _write(
        tmp_path,
        "data/research/research_factory/batches/B1/factory_trial_ledger.json",
        {"events": [{
            "trial_id": "TRIAL_COMPLETE", "objective_id": objective_id, "batch_id": "B1",
            "candidate_id": candidate_id, "candidate_hash": "HASH_FAILURE", "family_id": "FAMILY_1",
            "status": "COMPLETED", "performance_accessed": True, "performance_completed": True,
            "final_adjudicated": True, "registry_committed": True, "classification": "BLOCKED",
            "reason_codes": ["RAW_BOOTSTRAP_NOT_SUPPORTED"], "updated_at": "2026-01-02T12:03:00+00:00",
        }]},
    )
    completed_service = ResearchConsoleReadService(tmp_path)
    monkeypatch.setattr(completed_service, "get_orchestrator", service.get_orchestrator)
    completed_pipeline = completed_service.get_pipeline(objective_id).to_dict()

    assert completed_pipeline["current_stage"] == "FINAL_CLASSIFICATION"
    assert completed_pipeline["execution"]["predictive_status_zh"] == "已完成"
    assert completed_pipeline["execution"]["predictive_classification_zh"] == "预测验证未通过"
    assert completed_pipeline["execution"]["predictive_reason_zh"] == "原始 Bootstrap 支持不足：当前预测样本未达到冻结统计政策要求的原始 Bootstrap 支持门槛。"
    assert completed_pipeline["next_action"] == "CANDIDATE_PREDICTIVE_VALIDATION_COMPLETE"
    assert completed_pipeline["next_action_zh"] == "预测验证已完成，最终分类：预测验证未通过"
    assert completed_pipeline["predictive_authorization"]["status"] == "COMPLETED"
    assert completed_pipeline["predictive_authorization"]["available"] is False
    assert completed_pipeline["predictive_authorization"]["action_zh"] == "本候选不可再次执行"
    assert next(item for item in completed_pipeline["stages"] if item["stage"] == "FINAL_CLASSIFICATION")["status"] == "DONE"
    completed_trial_path.unlink()

    correction_payload = {
        "schema_version": "frozen-contract-correction-v1",
        "event_id": "CORRECTION_EVENT",
        "objective_id": objective_id,
        "candidate_id": candidate_id,
        "candidate_hash": "HASH_FAILURE",
        "contract_ref": "data/research/research_factory/batches/B1/durable_frozen_candidate_contracts.json",
        "contract_content_hash": "CONTENT_HASH_FAILURE",
        "action": "INVALIDATE_EXECUTION",
        "reason_code": "FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE",
        "provider_compatibility_error": "ValueError:unsupported family",
        "safety_evidence": {"trial_records": 0, "performance_accessed": False, "budget_used": 0, "budget_reserved": 0},
        "created_at": "2026-01-02T12:02:00+00:00",
        "previous_event_hash": "",
    }
    correction = {**correction_payload, "event_hash": stable_hash(correction_payload)}
    correction_path = tmp_path / f"reports/research_daemon/{objective_id}/frozen_contract_corrections.jsonl"
    correction_path.write_text(json.dumps(correction, ensure_ascii=False) + "\n", encoding="utf-8")

    corrected_pipeline = service.get_pipeline(objective_id).to_dict()
    corrected_candidate = service.list_candidates(objective_id)["items"][0]

    assert corrected_pipeline["execution"]["status"] == "STRUCTURAL_FAILED"
    assert corrected_pipeline["execution"]["structural_status"] == "ENGINEERING_BLOCKED"
    assert corrected_pipeline["execution"]["structural_reason_code"] == "FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE"
    assert corrected_candidate["structural_status"] == "ENGINEERING_BLOCKED"
    assert corrected_candidate["reasons"][0]["reason_code"] == "FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE"


def test_ai_status_projects_objective_scoped_runtime_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    objective_id = "OBJECTIVE_AI_RUNTIME_TEST"
    handoff_id = "HANDOFF_TEST"
    invocation_id = "AI_INVOCATION_TEST"
    diagnostics_path = tmp_path / f"reports/research_orchestrator_v2/{objective_id}/ai_staging/{handoff_id}/{invocation_id}/codex_runtime_diagnostics.json"
    diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostics_path.write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "process_id": 123,
                "started_at": "2026-01-02T11:59:00+00:00",
                "last_stdout_at": "2026-01-02T12:00:30+00:00",
                "last_stderr_at": "2026-01-02T12:00:20+00:00",
                "last_progress_at": "2026-01-02T12:00:30+00:00",
                "output_mode": "OUTPUT_LAST_MESSAGE_FILE",
                "stdout_tail": "private raw output must not be projected",
            }
        ),
        encoding="utf-8",
    )
    service = ResearchConsoleReadService(tmp_path, clock=lambda: datetime(2026, 1, 2, 12, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(
        service,
        "get_orchestrator",
        lambda _objective_id: {
            "ai_auto_invocation_enabled": True,
            "ai_status": "AI_INVOCATION_RUNNING",
            "ai_status_zh": "AI 研究员正在设计新策略",
            "current_handoff_id": handoff_id,
            "current_ai_invocation_id": invocation_id,
            "last_ai_invocation": {"handoff_id": handoff_id, "ai_invocation_id": invocation_id, "status": "RUNNING"},
            "retry_state": {"attempt": 1, "max_attempts": 2},
            "freshness_state": "FRESH",
            "source_generated_at": "2026-01-02T12:00:30+00:00",
            "observed_at": "2026-01-02T12:01:00+00:00",
        },
    )

    result = service.get_ai_status(objective_id)

    progress = result["runtime_progress"]
    assert progress["status"] == "RUNNING"
    assert progress["activity_zh"] == "AI 最近仍有输出，正在继续生成研究方案"
    assert progress["elapsed_seconds"] == 120
    assert progress["last_progress_at"] == "2026-01-02T12:00:30+00:00"
    assert progress["diagnostics_available"] is True
    assert "stdout_tail" not in progress
    assert "private raw output must not be projected" not in json.dumps(result, ensure_ascii=False)


def test_report_allowlist_rejects_path_traversal(console_fixture: ResearchConsoleReadService) -> None:
    with pytest.raises(ResearchConsoleReadError) as error:
        console_fixture.read_report(OBJECTIVE, "../secret.json")
    assert error.value.code == "REPORT_NOT_ALLOWED"
    assert error.value.status_code == 400


def test_objective_list_is_registry_scoped_and_keeps_canonical_state(console_fixture: ResearchConsoleReadService) -> None:
    result = console_fixture.list_objectives().to_dict()
    items = {item["objective_id"]: item for item in result["objectives"]}

    assert result["source_id"] == "research_objective_registry"
    assert set(items) == {OBJECTIVE, OTHER_OBJECTIVE}
    assert items[OBJECTIVE]["orchestrator_state"] == "CANONICAL_STATE_CONFLICT"
    assert items[OBJECTIVE]["state_source"] == "ObjectiveReconciliationServiceV1 / canonical facts"
    assert items[OTHER_OBJECTIVE]["objective_id"] == OTHER_OBJECTIVE


def test_cache_is_bounded(console_fixture: ResearchConsoleReadService) -> None:
    service = ResearchConsoleReadService(console_fixture.root, clock=lambda: datetime(2026, 1, 2, 12, 1, tzinfo=timezone.utc), cache_max_entries=2)
    for index in range(5):
        path = _write(console_fixture.root, f"cache/{index}.json", {"index": index})
        service._read_json(path)
    assert service.cache.size <= 2
    assert service.cache_stats["max_entries"] == 2


def test_read_boundary_does_not_start_provider_or_predictive_executor() -> None:
    source = inspect.getsource(ResearchConsoleReadService)
    assert "subprocess" not in source
    assert "Provider(" not in source
    assert "execute_predictive" not in source


def test_fastapi_console_routes_keep_reads_separate_from_protected_writes(tmp_path, console_fixture: ResearchConsoleReadService, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import chanlun_trader.webapp as webapp
    application = webapp.create_app(console_fixture.root, webapp.ExecutionPolicy("GOVERNED", "SYNTHETIC"))

    monkeypatch.setattr(application.state.services, "research_console_service", console_fixture)
    monkeypatch.setattr(application.state.services, "governance_execution_service", SimpleNamespace(catalog=lambda _objective_id: {"backend_level": "FULL"}))
    console_routes = [route for route in application.routes if getattr(route, "path", "").startswith("/api/research-console/")]
    read_routes = [route for route in console_routes if getattr(route, "methods", set()) <= {"GET"}]
    write_routes = [route for route in console_routes if "POST" in getattr(route, "methods", set())]
    assert len(read_routes) == 47
    assert any(route.path == "/api/research-console/{objective_id}/batch-scope-request" for route in read_routes)
    assert len(write_routes) == 25
    assert all(getattr(route, "methods", set()) <= {"GET"} for route in read_routes)
    assert all(getattr(route, "methods", set()) <= {"POST"} for route in write_routes)
    assert all("shell" not in getattr(route, "path", "").lower() for route in console_routes)
    assert any(route.path.endswith("/predictive/trial/resume/preview") for route in read_routes)
    assert any(route.path.endswith("/predictive/trial/new/authorization/preview") for route in read_routes)
    assert any(route.path.endswith("/predictive/trial/new/start/preview") for route in read_routes)
    assert any(route.path.endswith("/evolution/proposals") for route in read_routes)
    assert any(route.path.endswith("/evolution/ai-design") for route in read_routes)
    assert any(route.path.endswith("/evolution/ai-design/approval") for route in read_routes)
    assert any(route.path.endswith("/candidate-proposals") for route in read_routes)
    assert any(route.path.endswith("/candidate-proposals/materialization") for route in read_routes)
    assert any(route.path.endswith("/autonomous-control-plane") for route in read_routes)
    assert any(route.path.endswith("/candidate-proposals/generate") for route in write_routes)
    assert any(route.path.endswith("/candidate-proposals/materialization/preview") for route in write_routes)
    assert any(route.path.endswith("/candidate-proposals/materialization/confirm") for route in write_routes)
    assert any(route.path.endswith("/autonomous-control-plane/tick") for route in write_routes)
    with TestClient(application) as client:
        objectives = client.get("/api/research-console/objectives")
        assert objectives.status_code == 200
        assert {item["objective_id"] for item in objectives.json()["objectives"]} == {OBJECTIVE, OTHER_OBJECTIVE}
        assert client.get(f"/api/research-console/{OBJECTIVE}/dashboard").status_code == 200
        assert client.get(f"/api/research-console/{OBJECTIVE}/evolution").status_code == 200
        assert client.get(f"/api/research-console/{OBJECTIVE}/evolution/proposals").status_code == 200
        assert client.get(f"/api/research-console/{OBJECTIVE}/evolution/ai-design/approval").status_code == 200
        assert client.get(f"/api/research-console/{OBJECTIVE}/candidate-proposals").status_code == 200
        assert client.get(f"/api/research-console/{OBJECTIVE}/daemon/health").status_code == 200
        assert client.get(f"/api/research-console/{OBJECTIVE}/candidates/{CANDIDATE_ID}/structural").status_code == 200
        preview_catalog = client.get(f"/api/research-console/{OBJECTIVE}/governance/preview")
        assert preview_catalog.status_code == 200
        assert preview_catalog.json()["backend_level"] == "FULL"
        assert all("POST" not in getattr(route, "methods", set()) for route in read_routes)
        response = client.get(f"/api/research-console/{OBJECTIVE}/handoff")
        assert response.status_code == 200
        assert response.json()["outcome_blind"] is True
        unknown = client.get("/api/research-console/UNKNOWN_OBJECTIVE/dashboard")
        assert unknown.status_code == 404
        assert unknown.json()["code"] == "UNKNOWN_OBJECTIVE"


def test_fastapi_predictive_authorization_requires_confirmation_and_current_candidate(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import chanlun_trader.webapp as webapp
    application = webapp.create_app(tmp_path, webapp.ExecutionPolicy("GOVERNED", "SYNTHETIC"))

    calls: list[dict[str, object]] = []

    def fake_confirm(objective_id: str, body: dict[str, object]) -> dict[str, object]:
        if body.get("confirmed") is not True:
            raise webapp.PredictiveGovernanceError("CONFIRMATION_REQUIRED", "需要明确确认", status_code=400)
        if body.get("candidate_id") != CANDIDATE_ID:
            raise webapp.PredictiveGovernanceError("PREDICTIVE_GOVERNANCE_CANDIDATE_MISMATCH", "Candidate 不一致")
        calls.append({"objective_id": objective_id, **body})
        return {"status": "RECORDED", "idempotent": False, "next_action": "START_PREDICTIVE_TRIAL_1"}

    monkeypatch.setattr(application.state.services, "predictive_governance_service", SimpleNamespace(confirm=fake_confirm))
    with TestClient(application) as client:
        missing_confirmation = client.post(f"/api/research-console/{OBJECTIVE}/predictive/authorize", json={"candidate_id": CANDIDATE_ID, "authorization_id": "AUTH_1"})
        assert missing_confirmation.status_code == 400
        mismatch = client.post(f"/api/research-console/{OBJECTIVE}/predictive/authorize", json={"confirmed": True, "candidate_id": "OTHER", "authorization_id": "AUTH_1"})
        assert mismatch.status_code == 409
        response = client.post(f"/api/research-console/{OBJECTIVE}/predictive/authorize", json={"confirmed": True, "candidate_id": CANDIDATE_ID, "authorization_id": "AUTH_1", "decision_type": "AUTHORIZE_FIRST_PREDICTIVE_TRIAL"})

    assert response.status_code == 200
    assert response.json() == {"status": "RECORDED", "idempotent": False, "next_action": "START_PREDICTIVE_TRIAL_1"}
    assert calls == [{"objective_id": OBJECTIVE, "confirmed": True, "candidate_id": CANDIDATE_ID, "authorization_id": "AUTH_1", "decision_type": "AUTHORIZE_FIRST_PREDICTIVE_TRIAL"}]


def test_fastapi_structural_reconciliation_requires_confirmation_and_current_candidate(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import chanlun_trader.webapp as webapp
    application = webapp.create_app(tmp_path, webapp.ExecutionPolicy("GOVERNED", "SYNTHETIC", allow_structural=True))

    calls: list[dict[str, str]] = []

    def fake_start(objective_id: str, *, candidate_id: str | None, confirmed: bool, action: str):
        if candidate_id != CANDIDATE_ID:
            raise webapp.StructuralEntryError("STRUCTURAL_ENTRY_CANDIDATE_MISMATCH", "Candidate 不一致", status_code=409)
        calls.append({"objective_id": objective_id, "candidate_id": str(candidate_id), "confirmed": str(confirmed), "action": action})
        return {"status": "PASS", "candidate_id": candidate_id}

    monkeypatch.setattr(application.state.services, "structural_entry_service", SimpleNamespace(start=fake_start))
    with TestClient(application) as client:
        missing_confirmation = client.post(f"/api/research-console/{OBJECTIVE}/structural/reconcile", json={"candidate_id": CANDIDATE_ID})
        assert missing_confirmation.status_code == 400
        mismatch = client.post(f"/api/research-console/{OBJECTIVE}/structural/reconcile", json={"confirmed": True, "candidate_id": "OTHER"})
        assert mismatch.status_code == 409
        response = client.post(f"/api/research-console/{OBJECTIVE}/structural/reconcile", json={"confirmed": True, "candidate_id": CANDIDATE_ID, "action": "RUN_STRUCTURAL_PREFLIGHT"})

    assert response.status_code == 200
    assert response.json() == {"status": "PASS", "candidate_id": CANDIDATE_ID}
    assert calls == [{"objective_id": OBJECTIVE, "candidate_id": CANDIDATE_ID, "confirmed": "True", "action": "RUN_STRUCTURAL_PREFLIGHT"}]


def test_fastapi_contract_correction_requires_preview_and_explicit_confirmation(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import chanlun_trader.webapp as webapp
    application = webapp.create_app(tmp_path, webapp.ExecutionPolicy("GOVERNED", "SYNTHETIC"))

    candidate_hash = "candidate-hash"
    contract_ref = "data/research/contracts.json"
    detail = SimpleNamespace(to_dict=lambda: {
        "identity": {"candidate_hash": candidate_hash},
        "artifact_lineage": {"contract_ref": contract_ref},
    })
    monkeypatch.setattr(application.state.services, "research_console_service", SimpleNamespace(get_candidate=lambda _objective_id, _candidate_id: detail))
    calls: list[dict[str, str]] = []

    class FakeCorrectionService:
        def preview_incompatible_contract(self, **kwargs):
            calls.append({"method": "preview", **kwargs})
            return {
                "schema_version": "frozen-contract-correction-preview-v1",
                "status": "READY_TO_CONFIRM",
                "available": True,
                "objective_id": OBJECTIVE,
                "candidate_id": CANDIDATE_ID,
                "candidate_hash": candidate_hash,
                "contract_ref": contract_ref,
                "reason_code": "FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE",
                "reason_zh": "冻结合同无法由当前 Provider 执行。",
                "safety_evidence": {"trial_records": 0, "performance_accessed": False, "candidate_active_reservations": 0},
                "requires_confirmation": True,
                "preview_hash": "preview-hash",
                "confirmation_token": "confirmation-token",
            }

        def confirm_incompatible_contract(self, **kwargs):
            calls.append({"method": "confirm", **kwargs})
            return {"action": "INVALIDATE_EXECUTION", "candidate_id": CANDIDATE_ID}

    monkeypatch.setattr(application.state.services, "frozen_contract_correction_service", FakeCorrectionService())
    route = f"/api/research-console/{OBJECTIVE}/candidates/{CANDIDATE_ID}/contract-correction"
    with TestClient(application) as client:
        preview = client.get(f"{route}/preview")
        missing_confirmation = client.post(f"{route}/confirm", json={"preview_hash": "preview-hash", "confirmation_token": "confirmation-token"})
        response = client.post(f"{route}/confirm", json={"confirmed": True, "preview_hash": "preview-hash", "confirmation_token": "confirmation-token"})

    assert preview.status_code == 200
    assert preview.json()["available"] is True
    assert missing_confirmation.status_code == 400
    assert response.status_code == 200
    assert response.json()["action"] == "INVALIDATE_EXECUTION"
    assert calls == [
        {"method": "preview", "objective_id": OBJECTIVE, "candidate_id": CANDIDATE_ID, "expected_candidate_hash": candidate_hash, "contract_ref": contract_ref},
        {"method": "confirm", "objective_id": OBJECTIVE, "candidate_id": CANDIDATE_ID, "expected_candidate_hash": candidate_hash, "contract_ref": contract_ref, "preview_hash": "preview-hash", "confirmation_token": "confirmation-token"},
    ]


def test_fastapi_trial_reconciliation_requires_preview_and_explicit_confirmation(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    import chanlun_trader.webapp as webapp
    application = webapp.create_app(tmp_path, webapp.ExecutionPolicy("GOVERNED", "SYNTHETIC"))

    calls: list[dict[str, str]] = []

    class FakeTrialReconciliationService:
        def preview(self, **kwargs):
            calls.append({"method": "preview", **kwargs})
            return {
                "schema_version": "canonical-trial-reconciliation-preview-v1",
                "status": "READY_TO_CONFIRM",
                "available": True,
                "objective_id": OBJECTIVE,
                "trial_id": TRIAL_ID,
                "candidate_id": CANDIDATE_ID,
                "reason_code": "PERFORMANCE_ACCESSED_INCOMPLETE_TRIAL",
                "requires_confirmation": True,
                "preview_hash": "preview-hash",
                "confirmation_token": "confirmation-token",
            }

        def confirm(self, **kwargs):
            calls.append({"method": "confirm", **kwargs})
            return {"status": "COMPLETE", "terminal_status": "INVALIDATED", "trial_id": TRIAL_ID}

    monkeypatch.setattr(application.state.services, "canonical_trial_reconciliation_service", FakeTrialReconciliationService())
    route = f"/api/research-console/{OBJECTIVE}/trials/{TRIAL_ID}/reconciliation"
    with TestClient(application) as client:
        preview = client.get(f"{route}/preview")
        missing_confirmation = client.post(f"{route}/confirm", json={"preview_hash": "preview-hash", "confirmation_token": "confirmation-token"})
        response = client.post(f"{route}/confirm", json={"confirmed": True, "preview_hash": "preview-hash", "confirmation_token": "confirmation-token"})

    assert preview.status_code == 200
    assert preview.json()["available"] is True
    assert missing_confirmation.status_code == 400
    assert response.status_code == 200
    assert response.json()["terminal_status"] == "INVALIDATED"
    assert calls == [
        {"method": "preview", "objective_id": OBJECTIVE, "trial_id": TRIAL_ID},
        {"method": "confirm", "objective_id": OBJECTIVE, "trial_id": TRIAL_ID, "preview_hash": "preview-hash", "confirmation_token": "confirmation-token"},
    ]


def test_safety_counters_are_zero_and_read_api_does_not_write(console_fixture: ResearchConsoleReadService) -> None:
    files = [path for path in console_fixture.root.rglob("*") if path.is_file() and path.suffix in {".json", ".jsonl"}]
    before = {path: path.read_bytes() for path in files}
    console_fixture.list_objectives()
    dashboard = console_fixture.get_dashboard(OBJECTIVE).to_dict()
    console_fixture.get_candidate(OBJECTIVE, CANDIDATE_ID)
    console_fixture.get_trial(OBJECTIVE, TRIAL_ID)
    console_fixture.get_reports(OBJECTIVE)
    assert dashboard["safety_counters"]["NEW_PREDICTIVE_TRIALS"] == 0
    assert dashboard["safety_counters"]["PREDICTIVE_BUDGET_DELTA"] == 0
    assert dashboard["safety_counters"]["PERFORMANCE_ACCESS"] == 0
    assert dashboard["safety_counters"]["REAL_ORDER"] == "DISABLED"
    assert {path: path.read_bytes() for path in files} == before


def test_activation_eligibility_is_read_as_waiting_for_orchestrator(tmp_path: Path) -> None:
    objective_id = "RESEARCH_OBJECTIVE_GOVERNED_FOLLOWUP_TEST"
    _write(tmp_path, f"data/research/research_factory/objectives/{objective_id}.json", {"objective_id": objective_id, "lifecycle_state": "READY", "max_total_trials": 4})
    _write(
        tmp_path,
        f"data/research/research_factory/batches/{objective_id}_B01/search_budget_registry.json",
        {
            "schema_version": "search-budget-registry-v1",
            "objective_id": objective_id,
            "buckets": [{"kind": "objective", "key": objective_id, "limit": 4, "used": 0, "reserved": 0}],
            "active_reservations": {},
            "settled_reservations": {},
            "reservation_counter": 0,
            "updated_at": "2026-01-02T12:00:00+00:00",
        },
    )
    _write(
        tmp_path,
        f"reports/research_orchestrator_v2/{objective_id}/activation_eligibility.json",
        {
            "objective_id": objective_id,
            "eligibility": "READY",
            "activation_authorized": True,
            "activation_mode": "CREATE_AND_ACTIVATE",
            "target_state": "NEED_AI_RESEARCH_DESIGN",
            "created_at": "2026-01-02T12:00:00+00:00",
        },
    )

    daemon = ResearchConsoleReadService(tmp_path).get_daemon(objective_id)

    assert daemon.daemon_state == "READY"
    assert daemon.stage == "WAITING_FOR_ORCHESTRATOR"
    assert daemon.provenance.source_id == "activation_eligibility.json"
    assert daemon.state_display_zh == "已就绪"



def test_v1c_current_objective_read_models_preserve_terminal_safety() -> None:
    service = ResearchConsoleReadService(Path.cwd())
    status = service.get_orchestrator(OBJECTIVE)
    ai = service.get_ai_status(OBJECTIVE)
    closeout = service.get_closeout(OBJECTIVE)
    governance = service.get_governance_decision(OBJECTIVE)
    operations = service.get_operations(OBJECTIVE)

    assert status["orchestrator_state"] == "GOVERNANCE_DECISION_REQUIRED"
    assert status["terminal_reason"] == "PREDICTIVE_BUDGET_EXHAUSTED_BEFORE_CANDIDATE_SELECTION"
    assert status["budget"]["used"] == status["budget"]["total"] == 12
    assert status["budget"]["remaining"] == 0
    assert ai["real_codex_validation"]["real_smoke_test"] == "NOT_RUN"
    assert ai["real_codex_validation"]["scope"] == "PLATFORM"
    assert ai["exact_once"]["status"] == ai["no_outcome_isolation"]["status"] == "PASS"
    assert closeout["research_counts"]["RESEARCH_PASSED"] == 0
    assert closeout["research_counts"]["PROMISING"] == 2
    assert closeout["closeout_id"] == governance["current_terminal_summary"]["closeout_id"]
    assert closeout["conflict"] is False
    assert closeout["unevaluated_candidates"]["count"] == 11
    assert closeout["robust_alpha_established"] is False
    assert closeout["final_test_access"] == {"analytical": 0, "decision": 0, "physical": 0}
    assert closeout["prospective"] == "DISABLED"
    assert closeout["real_order"] == "DISABLED"
    assert governance["backend_level"] == "C"
    assert governance["new_objective_created"] is False
    assert governance["new_budget_created"] is False
    assert operations["terminal"] is True
    assert all(not item["available"] for item in operations["actions"] if item["action"] != "STATUS")
    assert not (_keys(ai) & {"return", "pnl", "profit_factor", "win_rate", "drawdown", "bootstrap_p_value", "adjusted_p"})
