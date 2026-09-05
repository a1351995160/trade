import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chanlun_trader.research.validation_policy_v2 import load_validation_decision_policy_v2
from chanlun_trader.research_factory.predictive_executor import CanonicalPredictiveExecutorV1
from chanlun_trader.research_factory.real_runtime import _dataset_hash


def test_objective_only_handoff_contract_uses_preperformance_structural_policy_pin(tmp_path: Path):
    source = Path("data/research/strategy_validation/validation_decision_policy_v2.json")
    target = tmp_path / source
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    target.with_name("validation_decision_policy_v2.lock.json").write_bytes(
        source.with_name("validation_decision_policy_v2.lock.json").read_bytes()
    )
    policy, policy_hash = load_validation_decision_policy_v2(target)
    report = tmp_path / "reports/research_daemon/OBJ_1/structural_preflight_reconciliation.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({
        "status": "PASS", "candidate_id": "CAND_1", "candidate_hash": "HASH_1",
        "repaired_structural_result": {"status": "PASS", "details": {"v1_result": {
            "policy_id": policy.policy_id, "policy_version": policy.policy_version, "policy_hash": policy_hash,
        }}},
    }), encoding="utf-8")
    contract = SimpleNamespace(candidate_id="CAND_1", candidate_hash="HASH_1", policy_identity={"objective_id": "OBJ_1"})

    loaded, loaded_hash, loaded_path = CanonicalPredictiveExecutorV1(tmp_path, "OBJ_1")._load_policy(contract)

    assert loaded.policy_id == "VALIDATION_DECISION_POLICY_V2"
    assert loaded_hash == policy_hash
    assert loaded_path == target


def test_objective_only_handoff_contract_fails_without_structural_policy_pin(tmp_path: Path):
    contract = SimpleNamespace(candidate_id="CAND_1", candidate_hash="HASH_1", policy_identity={"objective_id": "OBJ_1"})
    with pytest.raises(RuntimeError, match="CANONICAL_VALIDATION_POLICY_PIN_MISSING"):
        CanonicalPredictiveExecutorV1(tmp_path, "OBJ_1")._load_policy(contract)


def test_ai_handoff_candidate_uses_unique_canonical_budget_bindings():
    snapshot = {"buckets": [
        {"kind": "batch", "key": "OBJECTIVE_B01"},
        {"kind": "family", "key": "MTF_OBJECTIVE"},
    ]}
    executor = CanonicalPredictiveExecutorV1(".", "OBJ_1")

    assert executor._budget_binding(snapshot, "batch", "AI_HANDOFF_V2_123") == "OBJECTIVE_B01"
    assert executor._budget_binding(snapshot, "family", "event reversal") == "MTF_OBJECTIVE"


def test_dataset_identity_accepts_frozen_inline_event_without_event_store(tmp_path: Path):
    class _Helper:
        @staticmethod
        def sha256(path):
            return path.read_text(encoding="utf-8")

    paths = [
        "data/research/daily_all.parquet",
        "factor.parquet",
        "data/research/security_state/normalized/manifest.json",
        "data/research/data_routing/routing_policy.json",
        "data/research/universe_policies/A_SHARE_RESEARCH_UNIVERSE_POLICY_V2.json",
    ]
    for relative in paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")

    identity = _dataset_hash(
        tmp_path,
        tmp_path / "factor.parquet",
        ("INLINE_EVENT",),
        _Helper,
        inline_event_definitions={"INLINE_EVENT": {"event_id": "INLINE_EVENT", "definition": {"x": 1}}},
    )

    assert len(identity) == 64


def test_dataset_identity_binds_contract_defined_event_without_event_store(tmp_path: Path):
    class _Helper:
        @staticmethod
        def sha256(path):
            return path.read_text(encoding="utf-8")

    paths = [
        "data/research/daily_all.parquet",
        "factor.parquet",
        "data/research/security_state/normalized/manifest.json",
        "data/research/data_routing/routing_policy.json",
        "data/research/universe_policies/A_SHARE_RESEARCH_UNIVERSE_POLICY_V2.json",
    ]
    for relative in paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")

    event_id = "BENCHMARK_SENTIMENT_BREAKOUT_EVENT_V1"
    contract_identity = {
        "registry_kind": "CONTRACT_DEFINED_DERIVED_EVENT",
        "identity_version": "V1",
        "dataset_id": "benchmark_index_daily",
        "pit_safe": True,
    }
    contract_hash = _dataset_hash(
        tmp_path,
        tmp_path / "factor.parquet",
        (event_id,),
        _Helper,
        contract_event_identities={event_id: contract_identity},
    )
    changed_hash = _dataset_hash(
        tmp_path,
        tmp_path / "factor.parquet",
        (event_id,),
        _Helper,
        contract_event_identities={event_id: {**contract_identity, "identity_version": "V2"}},
    )

    assert len(contract_hash) == 64
    assert contract_hash != changed_hash


def test_final_status_payload_contains_terminal_audit_fields():
    budget = SimpleNamespace(snapshot=lambda: {
        "settled_reservations": {"RESERVATION_1": "CONSUMED"},
        "active_reservations": {},
    })
    payload = CanonicalPredictiveExecutorV1(".", "OBJ_1")._final_status_payload(
        trial_id="TRIAL_1",
        trial_contract_hash="TRIAL_CONTRACT_HASH_1",
        reservation_id="RESERVATION_1",
        budget=budget,
        status="TRIAL_FAILED",
        trial_status="INVALIDATED",
        stage="TRIAL_FAILED",
        finished_at="2026-09-02T00:00:00+08:00",
        performance_accessed=True,
        performance_completed=False,
        recovery=True,
        error=RuntimeError("CANONICAL_EVENT_NOT_MATERIALIZABLE:EVENT_1"),
    )

    assert payload["status"] == "TRIAL_FAILED"
    assert payload["finished_at"]
    assert payload["stage"] == "TRIAL_FAILED"
    assert payload["error_code"] == "CANONICAL_EVENT_NOT_MATERIALIZABLE"
    assert payload["error_message"].endswith("EVENT_1")
    assert payload["contract_hash"] == "TRIAL_CONTRACT_HASH_1"
    assert payload["trial_id"] == "TRIAL_1"
    assert payload["budget_settlement"] == {"reservation_id": "RESERVATION_1", "status": "CONSUMED"}
    assert payload["result_artifact_id"] is None
