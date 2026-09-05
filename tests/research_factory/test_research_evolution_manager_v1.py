import json
from pathlib import Path

from chanlun_trader.research_factory.context import PerformanceBlindGuard
from chanlun_trader.research_factory.research_evolution_manager import (
    FAILURE_CATEGORIES,
    ResearchEvolutionManager,
    classify_failures,
)


OBJECTIVE_ID = "OBJECTIVE_EVOLUTION_TEST_V1"
CANDIDATE_ID = "CANDIDATE_EVOLUTION_TEST_V1"
TRIAL_ID = "OBJECTIVE_EVOLUTION_TEST_V1_B01_CANDIDATE_EVOLUTION_TEST_V1_T001"
BATCH_ID = f"{OBJECTIVE_ID}_B01"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fixture_root(tmp_path: Path) -> dict[str, Path | str]:
    objective_path = tmp_path / "data/research/research_factory/objectives" / f"{OBJECTIVE_ID}.json"
    candidate_path = tmp_path / "data/research/research_factory/batches/fixture" / "durable_frozen_candidate_contracts.json"
    ledger_path = tmp_path / "data/research/research_factory/batches" / BATCH_ID / "factory_trial_ledger.json"
    artifact_root = tmp_path / "reports/research_daemon" / OBJECTIVE_ID / "predictive" / BATCH_ID / CANDIDATE_ID / TRIAL_ID
    trial_contract_path = tmp_path / "reports/research_daemon" / OBJECTIVE_ID / "predictive/trial_contracts" / f"{TRIAL_ID}.json"
    _write_json(objective_path, {"objective_id": OBJECTIVE_ID, "objective_name": "演进测试目标", "max_total_trials": 1})
    _write_json(candidate_path, {"contracts": [{"candidate_id": CANDIDATE_ID, "candidate_hash": "candidate-hash", "family": "TEST_FAMILY", "mechanism": "event participation mechanism", "factor_ids": ["factor_a"], "policy_identity": {"objective_id": OBJECTIVE_ID}}]})
    _write_json(ledger_path, [{"event_type": "REGISTRY_TRANSITION_COMMITTED", "objective_id": OBJECTIVE_ID, "candidate_id": CANDIDATE_ID, "trial_id": TRIAL_ID, "classification": "BLOCKED", "reason_code": "RAW_BOOTSTRAP_NOT_SUPPORTED"}])
    _write_json(trial_contract_path, {"objective_id": OBJECTIVE_ID, "candidate_id": CANDIDATE_ID, "trial_id": TRIAL_ID, "contract_hash": "trial-contract-hash"})
    _write_json(artifact_root / "validation_results.json", {"rows": [{"objective_id": OBJECTIVE_ID, "candidate_id": CANDIDATE_ID, "trial_id": TRIAL_ID, "base_metrics": {"net_return": -0.1, "profit_factor": 0.9, "closed_trade_count": 3}, "bootstrap": {"p_value": 0.7, "status": "COMPLETE"}, "cost_stress": {"COMBINED_X2": {"net_return": -0.2}}, "engine_integrity": {"certification_status": "VALID", "invariant_errors": []}, "sample_feasibility": {"sample_count": 3, "minimum_required": 10}, "gates": {"local_base_return": False, "local_profit_factor": False, "cost_stress_combined_x2": False}}]})
    _write_json(artifact_root / "final_status.json", {"objective_id": OBJECTIVE_ID, "candidate_id": CANDIDATE_ID, "trial_id": TRIAL_ID, "status": "BLOCKED", "classification": "BLOCKED", "budget_consumed": True})
    _write_json(artifact_root / "multiple_testing.json", {"q": 0.05, "adjusted_p_values": {CANDIDATE_ID: 0.9}, "adjusted_support": {CANDIDATE_ID: False}, "raw_p_values": {CANDIDATE_ID: 0.7}, "raw_support": {CANDIDATE_ID: False}})
    return {"objective_path": objective_path, "candidate_path": candidate_path, "ledger_path": ledger_path, "artifact_root": artifact_root, "trial_contract_path": trial_contract_path}


def test_failure_classification_covers_v1_categories() -> None:
    validation = {
        "candidate_id": CANDIDATE_ID,
        "base_metrics": {"net_return": -0.1, "profit_factor": 0.8},
        "bootstrap": {"p_value": 0.7},
        "cost_stress": {"COMBINED_X2": {"net_return": -0.2}},
        "sample_feasibility": {"sample_count": 2, "minimum_required": 10},
        "engine_integrity": {"certification_status": "INVALID"},
        "concentration": {"warning": True},
    }
    findings = classify_failures(validation, final_status={"status": "BLOCKED"}, multiple_testing={"adjusted_support": False, "adjusted_p": 0.9, "q": 0.05})
    assert {item["category"] for item in findings} == set(FAILURE_CATEGORIES)
    assert all(item["category_zh"] for item in findings)


def test_ai_context_is_outcome_blind(tmp_path: Path) -> None:
    fixture = _fixture_root(tmp_path)
    report = ResearchEvolutionManager(tmp_path).generate_report(OBJECTIVE_ID, CANDIDATE_ID, TRIAL_ID)
    context_path = tmp_path / "reports/research_evolution" / OBJECTIVE_ID / CANDIDATE_ID / TRIAL_ID / "AI_RESEARCH_EVOLUTION_CONTEXT.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    PerformanceBlindGuard.assert_blind(context)
    serialized = json.dumps(context, ensure_ascii=False).casefold()
    for forbidden in ("net_return", "profit_factor", "win_rate", "drawdown", "trade_pnl", "p_value", "adjusted_p"):
        assert forbidden not in serialized
    assert report["governance"]["automatic_candidate_created"] is False
    assert fixture["artifact_root"].exists()


def test_lineage_and_governance_are_preserved(tmp_path: Path) -> None:
    fixture = _fixture_root(tmp_path)
    source_paths = [path for path in fixture.values() if isinstance(path, Path) and path.is_file()]
    before = {path: path.read_bytes() for path in source_paths}
    report = ResearchEvolutionManager(tmp_path).generate_report(OBJECTIVE_ID, CANDIDATE_ID, TRIAL_ID)
    assert report["lineage"]["objective_id"] == OBJECTIVE_ID
    assert report["lineage"]["candidate_id"] == CANDIDATE_ID
    assert report["lineage"]["trial_id"] == TRIAL_ID
    assert report["lineage"]["validation_result_ref"].endswith("validation_results.json")
    assert report["lineage"]["final_status_ref"].endswith("final_status.json")
    assert report["governance"]["read_only"] is True
    assert report["governance"]["budget_modified"] is False
    assert report["governance"]["multiple_testing_modified"] is False
    assert {path: path.read_bytes() for path in source_paths} == before
    output_root = tmp_path / "reports/research_evolution"
    assert all(path.is_relative_to(output_root) for path in output_root.rglob("*") if path.is_file())


def test_restart_is_idempotent_and_does_not_duplicate_landscape(tmp_path: Path) -> None:
    _fixture_root(tmp_path)
    manager = ResearchEvolutionManager(tmp_path)
    first = manager.generate_report(OBJECTIVE_ID, CANDIDATE_ID, TRIAL_ID)
    output_root = tmp_path / "reports/research_evolution" / OBJECTIVE_ID
    report_path = output_root / CANDIDATE_ID / TRIAL_ID / "research_evolution_report.json"
    context_path = output_root / CANDIDATE_ID / TRIAL_ID / "AI_RESEARCH_EVOLUTION_CONTEXT.json"
    landscape_path = output_root / "failure_landscape.json"
    before = {path: path.read_bytes() for path in (report_path, context_path, landscape_path)}
    second = manager.generate_report(OBJECTIVE_ID, CANDIDATE_ID, TRIAL_ID)
    assert second == first
    assert {path: path.read_bytes() for path in (report_path, context_path, landscape_path)} == before
    landscape = json.loads(landscape_path.read_text(encoding="utf-8"))
    assert landscape["trial_count"] == 1
    assert len(landscape["entries"]) == 1
