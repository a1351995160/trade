import hashlib
import json
from pathlib import Path

import pytest

from chanlun_trader.research_daemon import CandidateWork, CanonicalPlatformContext, StructuralResult
from chanlun_trader.research_daemon_state import DaemonCheckpointStoreV1, DaemonCheckpointV1
from chanlun_trader.research_factory.structural_reconciliation import reconcile_structural_pass


class _Runtime:
    def __init__(self, root: Path, objective_id: str, candidate: CandidateWork, result: StructuralResult):
        self.root = root
        self.objective_id = objective_id
        self.candidates = [candidate]
        self.result = result
        self.structural_calls = 0
        self.predictive_calls = 0
        self.budget = {
            "objective_id": objective_id,
            "registry_path": "data/research/research_factory/batches/B1/search_budget_registry.json",
            "used": 0,
            "reserved": 0,
            "remaining": 4,
            "total": 4,
            "registry_head_hash": "HEAD",
        }

    def load_context(self):
        return CanonicalPlatformContext(
            objective_id=self.objective_id,
            objective_hash="OBJECTIVE_HASH",
            budget=dict(self.budget),
            capability_context_ref="reports/CAPABILITY.json",
            architecture_manifest_ref="reports/ARCHITECTURE.json",
            frozen_candidate_refs=(self.candidates[0].contract_ref,),
            checkpoint_refs=(),
        )

    def structural_preflight(self, candidate):
        self.structural_calls += 1
        return self.result

    def predictive_validate(self, candidate):
        self.predictive_calls += 1
        raise AssertionError("predictive validation must not run")

    def summary(self):
        return {
            "budget": dict(self.budget),
            "current_trial": None,
            "remaining_frozen_candidates": 0,
            "research_passed_count": 0,
            "promising_count": 0,
            "global_search_exhausted": False,
        }


def _fixture(tmp_path: Path, result_status: str = "PASS"):
    objective_id = "OBJECTIVE_STRUCTURAL_RECONCILIATION"
    candidate = CandidateWork("CANDIDATE_EVENT", "CANDIDATE_HASH", "data/research/research_factory/batches/B1/contracts.json")
    budget_path = tmp_path / "data/research/research_factory/batches/B1/search_budget_registry.json"
    budget_path.parent.mkdir(parents=True)
    budget_path.write_text(json.dumps({"objective_id": objective_id, "used": 0, "reserved": 0}), encoding="utf-8")
    details = {
        "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate.candidate_hash,
        "v1_result": {
            "candidate_id": candidate.candidate_id,
            "candidate_hash": candidate.candidate_hash,
            "lower_bound_count": 40,
            "upper_bound_count": 40,
            "minimum_required_count": 30,
            "outcome_blind": True,
            "performance_data_loaded": False,
        },
        "lower_bound_integrity": {"status": "PASS", "failure_codes": []},
    }
    result = StructuralResult(result_status, "B_VALID_LOWER_BOUND_AT_OR_ABOVE_MINIMUM", details=details)
    runtime = _Runtime(tmp_path, objective_id, candidate, result)
    store = DaemonCheckpointStoreV1(tmp_path, objective_id)
    checkpoint = DaemonCheckpointV1(
        objective_id=objective_id,
        current_state="READY",
        canonical_refs={"last_structural_result": {
            "status": "UNKNOWN",
            "reason_code": "D_LOWER_BOUND_INTEGRITY_NOT_ESTABLISHED_FAIL_CLOSED",
            "details": {"candidate_id": candidate.candidate_id, "candidate_hash": candidate.candidate_hash},
        }},
        budget_view=runtime.budget,
    )
    store.save(checkpoint)
    return objective_id, candidate, budget_path, runtime, store


def test_reconcile_structural_pass_updates_checkpoint_without_prediction_or_budget_delta(tmp_path: Path):
    objective_id, candidate, budget_path, runtime, store = _fixture(tmp_path)
    budget_hash = hashlib.sha256(budget_path.read_bytes()).hexdigest()

    report = reconcile_structural_pass(
        tmp_path,
        objective_id=objective_id,
        candidate_id=candidate.candidate_id,
        runtime=runtime,
    )
    checkpoint = store.load()
    status = store.load_status()

    assert report["status"] == "PASS"
    assert report["safety_evidence"]["predictive_executor_invoked"] is False
    assert report["safety_evidence"]["trial_ledger_event_count_after"] == 0
    assert hashlib.sha256(budget_path.read_bytes()).hexdigest() == budget_hash
    assert runtime.predictive_calls == 0
    assert checkpoint.current_state == "READY"
    assert checkpoint.canonical_refs["last_structural_result"]["status"] == "PASS"
    assert checkpoint.required_action == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
    assert checkpoint.current_trial is None
    assert checkpoint.last_completed_candidate["candidate_id"] == candidate.candidate_id
    assert status["canonical_refs"]["last_structural_result"]["status"] == "PASS"

    repeated = reconcile_structural_pass(
        tmp_path,
        objective_id=objective_id,
        candidate_id=candidate.candidate_id,
        runtime=runtime,
    )
    assert repeated["reconciliation_id"] == report["reconciliation_id"]
    assert runtime.structural_calls == 1


def test_reconcile_structural_pass_excludes_unrelated_global_lineage_evidence(tmp_path: Path):
    objective_id, candidate, _budget_path, runtime, store = _fixture(tmp_path)
    reports = tmp_path / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "FOLLOWUP_STRUCTURAL_PREFLIGHT_REPAIRED_V1.json").write_text(
        json.dumps({
            "candidate_id": "OTHER_CANDIDATE",
            "candidate_hash": "OTHER_HASH",
            "status": "PASS",
        }),
        encoding="utf-8",
    )
    (reports / "FINAL_STATUS_CLOSE_STRUCTURAL_PREFLIGHT_UNKNOWN_BOUND_V1.json").write_text(
        json.dumps({"status": "PASS"}),
        encoding="utf-8",
    )
    matching = store.runtime_dir / "structural_preflight_reconciliation.json"
    matching.write_text(
        json.dumps({
            "objective_id": objective_id,
            "candidate_id": candidate.candidate_id,
            "candidate_hash": candidate.candidate_hash,
            "status": "UNKNOWN",
            "v1_result": {
                "lower_bound_count": 0,
                "upper_bound_count": 40,
                "minimum_required_count": 30,
            },
        }),
        encoding="utf-8",
    )

    report = reconcile_structural_pass(
        tmp_path,
        objective_id=objective_id,
        candidate_id=candidate.candidate_id,
        runtime=runtime,
    )

    evidence_refs = [item["report_ref"] for item in report["lineage"]["evidence_chain"]]
    assert evidence_refs == [f"reports/research_daemon/{objective_id}/structural_preflight_reconciliation.json"]
    assert all(item.get("candidate_id") == candidate.candidate_id for item in report["lineage"]["evidence_chain"])
    historical = report["lineage"]["historical_reconciliation_generations"]
    assert [item["candidate_id"] for item in historical] == [candidate.candidate_id]


def test_existing_canonical_pass_sanitizes_lineage_without_provider_or_trial_rerun(tmp_path: Path):
    objective_id, candidate, budget_path, runtime, store = _fixture(tmp_path)
    first = reconcile_structural_pass(
        tmp_path,
        objective_id=objective_id,
        candidate_id=candidate.candidate_id,
        runtime=runtime,
    )
    assert runtime.structural_calls == 1
    report_path = store.runtime_dir / "structural_preflight_reconciliation_canonical_v1.json"
    history_path = store.runtime_dir / "structural_preflight_reconciliation_history.json"
    budget_hash_before = hashlib.sha256(budget_path.read_bytes()).hexdigest()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    reconciliation_id = report["reconciliation_id"]
    structural_identity = {
        key: report[key]
        for key in ("status", "candidate_id", "candidate_hash", "lower_bound", "upper_bound", "minimum_required")
    }
    report["lineage"]["evidence_chain"] = [
        {
            "report_ref": "reports/OLD_OTHER_CANDIDATE.json",
            "candidate_id": "OTHER_CANDIDATE",
            "candidate_hash": "OTHER_HASH",
            "status": "PASS",
        },
        {
            "report_ref": "reports/CURRENT_CANDIDATE.json",
            "objective_id": objective_id,
            "candidate_id": candidate.candidate_id,
            "candidate_hash": candidate.candidate_hash,
            "status": "UNKNOWN",
        },
    ]
    report["lineage"]["historical_reconciliation_generations"] = [
        {
            "generation": "OLD",
            "report_ref": "reports/OLD_OTHER_CANDIDATE_GENERATION.json",
            "candidate_id": "OTHER_CANDIDATE",
            "candidate_hash": "OTHER_HASH",
            "status": "PASS",
        }
    ]
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    stale_history_sha = json.loads(history_path.read_text(encoding="utf-8"))["generations"][0]["sha256"]

    cleaned = reconcile_structural_pass(
        tmp_path,
        objective_id=objective_id,
        candidate_id=candidate.candidate_id,
        runtime=runtime,
    )

    assert runtime.structural_calls == 1
    assert runtime.predictive_calls == 0
    assert cleaned["reconciliation_id"] == reconciliation_id == first["reconciliation_id"]
    assert {key: cleaned[key] for key in structural_identity} == structural_identity
    assert [item["report_ref"] for item in cleaned["lineage"]["evidence_chain"]] == ["reports/CURRENT_CANDIDATE.json"]
    assert cleaned["lineage"]["historical_reconciliation_generations"] == []
    scope = cleaned["lineage"]["scope_reconciliation"]
    assert scope["status"] == "PASS"
    assert scope["structural_result_changed"] is False
    assert scope["predictive_run_started"] is False
    assert scope["removed_report_refs"] == [
        "reports/OLD_OTHER_CANDIDATE.json",
        "reports/OLD_OTHER_CANDIDATE_GENERATION.json",
    ]
    current_report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert history["generations"][0]["sha256"] == current_report_sha
    assert history["generations"][0]["sha256"] != stale_history_sha
    assert hashlib.sha256(budget_path.read_bytes()).hexdigest() == budget_hash_before
    checkpoint = store.load()
    assert checkpoint.required_action == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
    assert checkpoint.current_trial is None


def test_reconcile_structural_pass_preserves_checkpoint_when_recheck_does_not_pass(tmp_path: Path):
    objective_id, candidate, _budget_path, runtime, store = _fixture(tmp_path, result_status="UNKNOWN")
    checkpoint_hash = store.load().checkpoint_hash

    with pytest.raises(RuntimeError, match="did not pass"):
        reconcile_structural_pass(
            tmp_path,
            objective_id=objective_id,
            candidate_id=candidate.candidate_id,
            runtime=runtime,
        )

    assert store.load().checkpoint_hash == checkpoint_hash
    assert not (store.runtime_dir / "structural_preflight_reconciliation.json").exists()
    assert runtime.predictive_calls == 0


def test_reconcile_structural_result_records_safe_sample_gate_block_without_prediction(tmp_path: Path):
    objective_id, candidate, budget_path, runtime, store = _fixture(tmp_path)
    details = dict(runtime.result.details)
    details["v1_result"] = {
        **details["v1_result"],
        "lower_bound_count": 4,
        "upper_bound_count": 4,
        "minimum_required_count": 30,
    }
    runtime.result = StructuralResult("BLOCKED", "A_UPPER_BOUND_BELOW_MINIMUM", details=details)
    budget_hash = hashlib.sha256(budget_path.read_bytes()).hexdigest()

    report = reconcile_structural_pass(
        tmp_path,
        objective_id=objective_id,
        candidate_id=candidate.candidate_id,
        runtime=runtime,
    )
    checkpoint = store.load()

    assert report["status"] == "BLOCKED"
    assert report["next_boundary"] == "PREDICTIVE_VALIDATION_BLOCKED_BY_SAMPLE_GATE"
    assert report["safety_evidence"]["predictive_executor_invoked"] is False
    assert hashlib.sha256(budget_path.read_bytes()).hexdigest() == budget_hash
    assert checkpoint.current_state == "READY"
    assert checkpoint.canonical_refs["last_structural_result"]["status"] == "BLOCKED"
    assert checkpoint.canonical_refs["structural_reconciliation"]["status"] == "BLOCKED"
    assert checkpoint.required_action is None
    assert checkpoint.current_trial is None
    assert runtime.predictive_calls == 0


def test_reconcile_structural_pass_rejects_unsafe_identifiers_before_filesystem_access(tmp_path: Path):
    with pytest.raises(ValueError, match="safe identifiers"):
        reconcile_structural_pass(
            tmp_path,
            objective_id="../OBJECTIVE_ESCAPE",
            candidate_id="CANDIDATE_EVENT",
        )
