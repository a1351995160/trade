from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "scripts")

pytest.importorskip(
    "run_codex_guided_autonomous_research_pilot_v1",
    reason="legacy autonomous pilot integration is outside the self-contained source baseline",
)

from run_codex_guided_autonomous_research_pilot_v1 import (
    _materialize_failure_view_from_artifacts,
    _report_classification,
)
from chanlun_trader.research_factory.failure_adapter import FailureKnowledgeAdapterV1


def test_failure_adapter_reads_nested_final_decision_category():
    snapshot = FailureKnowledgeAdapterV1().snapshot_from_trials(
        [{
            "trial_id": "T1",
            "family_id": "F",
            "classification": "BLOCKED",
            "final_decision": {
                "effective_classification": "BLOCKED",
                "failure_category": "SAMPLE_FAILURE",
                "reason": "RAW_BOOTSTRAP_NOT_SUPPORTED",
            },
        }],
        snapshot_id="S",
    )

    assert len(snapshot.entries) == 1
    assert snapshot.entries[0].category == "SAMPLE_FAILURE"
    assert snapshot.entries[0].reason_code == "RAW_BOOTSTRAP_NOT_SUPPORTED"


def test_pilot_report_keeps_blocked_as_policy_classification():
    assert _report_classification(
        {"classification": "BLOCKED"},
        {"final_decision": {"effective_classification": "BLOCKED", "failure_category": "SAMPLE_FAILURE"}},
    ) == "BLOCKED"


def test_failure_view_materialization_uses_final_decision_without_engine_access(tmp_path):
    output_dir = tmp_path / "pilot"
    batch_id = "PILOT_B01"
    report_dir = output_dir / batch_id
    report_dir.mkdir(parents=True)
    trial_id = f"{batch_id}_T001"
    decision = {
        "effective_classification": "BLOCKED",
        "failure_category": "SAMPLE_FAILURE",
        "reason": "RAW_BOOTSTRAP_NOT_SUPPORTED",
    }
    (report_dir / "trial_manifest.json").write_text(
        json.dumps({"trials": [{"trial_id": trial_id, "candidate_id": "C1", "family_id": "F1", "classification": "BLOCKED"}]}),
        encoding="utf-8",
    )
    (report_dir / "validation_results.json").write_text(
        json.dumps({"rows": [{"trial_id": trial_id, "candidate_id": "C1", "classification": "BLOCKED", "final_decision": decision}]}),
        encoding="utf-8",
    )

    view = _materialize_failure_view_from_artifacts(output_dir, batch_id)

    assert view is not None
    assert view.entries[0]["category"] == "SAMPLE_FAILURE"
    assert view.entries[0]["reason_code"] == "RAW_BOOTSTRAP_NOT_SUPPORTED"
    assert view.to_dict()["exact_performance_values_exposed"] is False
