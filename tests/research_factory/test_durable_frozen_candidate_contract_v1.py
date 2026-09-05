from __future__ import annotations

import json
from pathlib import Path

import pytest

from chanlun_trader.research.strategy_candidate import StrategyCandidateSpec
from chanlun_trader.research.strategy_semantic import ExitPredicateSpec, SemanticCandidateRecord, SignalPredicateSpec
from chanlun_trader.research_factory.durability import (
    DurableFrozenCandidateContractRegistryV1,
    DurableFrozenCandidateContractV1,
)
from chanlun_trader.research_factory.common import stable_hash


ROOT = Path(__file__).resolve().parents[2]


def _record() -> tuple[SemanticCandidateRecord, dict]:
    payload = json.loads((ROOT / "data/research/strategy_candidate_registry/registry_v2.json").read_text(encoding="utf-8"))
    item = payload["records"][0]
    record = SemanticCandidateRecord(
        candidate=StrategyCandidateSpec.from_dict(item["candidate"]),
        parent_candidate_id=item["parent_candidate_id"],
        previous_preregistration_hash=item["previous_preregistration_hash"],
        semantic_change_reason=item["semantic_change_reason"],
        signal_predicate=SignalPredicateSpec.from_dict(item["signal_predicate"]),
        exit_predicate=ExitPredicateSpec.from_dict(item["exit_predicate"]),
        semantic_status=item["semantic_status"],
        phase4_eligible=item["phase4_eligible"],
        semantic_fingerprint=item["semantic_fingerprint"],
        preregistration_hash=item["preregistration_hash"],
        created_at=item["created_at"],
    )
    hypotheses = json.loads((ROOT / "data/research/hypothesis_registry/registry.json").read_text(encoding="utf-8"))["hypotheses"]
    hypothesis = next(item for item in hypotheses if item["hypothesis_id"] == record.signal_predicate.source_hypothesis_id)
    return record, hypothesis


def _contract(record: SemanticCandidateRecord, hypothesis: dict) -> DurableFrozenCandidateContractV1:
    return DurableFrozenCandidateContractV1.from_semantic_record(
        record,
        hypothesis,
        factor_event_registry_identities={"factor_registry": {"id": "FACTOR_REGISTRY_TEST"}, "event_registry": {"id": "EVENT_REGISTRY_TEST"}},
        research_period_identity={"id": "RESEARCH_PERIOD_TEST", "start": 20220801, "end": 20250731},
        policy_identity={"policy_id": "POLICY_TEST", "policy_hash": "POLICY_HASH_TEST"},
        source_provenance={"run_id": "RUN_TEST", "batch_id": "BATCH_TEST"},
        created_frozen_timestamp="2026-08-25T00:00:00+00:00",
    )


def test_freeze_persist_restart_reload_reconstructs_exact_candidate(tmp_path: Path):
    record, hypothesis = _record()
    contract = _contract(record, hypothesis)
    path = tmp_path / "durable_frozen_candidate_contracts.json"

    registry = DurableFrozenCandidateContractRegistryV1(path)
    assert registry.append(contract) is True
    registry.write()

    restarted = DurableFrozenCandidateContractRegistryV1.read(path)
    loaded = restarted.items()[0]
    rebuilt = loaded.reconstruct_candidate()

    assert loaded.candidate_hash == record.preregistration_hash
    assert loaded.semantic_fingerprint == record.semantic_fingerprint
    assert loaded.entry_predicate == record.signal_predicate.to_dict()
    assert loaded.holding_period_trading_sessions == record.candidate.holding_period
    assert loaded.ranking_semantics["ranking_rule"] == record.candidate.ranking_rule
    assert loaded.top_n == int(record.candidate.selection_rule["top_n"])
    assert loaded.max_positions == record.candidate.max_positions
    assert loaded.execution_contract_version == record.candidate.strategy_dsl["semantic_contract_version"]
    assert loaded.factor_ids == tuple(item["factor_id"] for item in record.candidate.factor_bindings)
    assert loaded.event_ids == tuple(item["event_id"] for item in record.signal_predicate.event_conditions)
    assert rebuilt.to_dict() == record.to_dict()


def test_missing_frozen_field_fails_closed():
    record, hypothesis = _record()
    payload = _contract(record, hypothesis).to_dict()
    payload.pop("holding_period_trading_sessions")
    with pytest.raises(ValueError, match="fields mismatch"):
        DurableFrozenCandidateContractV1.from_dict(payload)


def test_provider_candidate_payload_preserves_frozen_execution_semantics():
    record, hypothesis = _record()
    contract = _contract(record, hypothesis)

    payload = contract.provider_candidate_payload()

    assert payload["candidate_id"] == contract.candidate_id
    assert payload["candidate_hash"] == contract.candidate_hash
    assert payload["strategy_family"] == contract.family
    assert payload["factor_ids"] == list(contract.factor_ids)
    assert payload["event_conditions"] == list(record.signal_predicate.event_conditions)
    assert payload["holding_period"] == contract.holding_period_trading_sessions
    assert payload["selection_rule"] == contract.selection_rule


def test_provider_candidate_payload_rejects_unsupported_family_before_runtime():
    record, hypothesis = _record()
    payload = _contract(record, hypothesis).to_dict()
    payload["family"] = "CORRECTED_V3_HISTORICAL"
    payload["content_hash"] = stable_hash({key: value for key, value in payload.items() if key != "content_hash"})
    contract = DurableFrozenCandidateContractV1.from_dict(payload)

    with pytest.raises(ValueError, match="provider-supported DAILY family"):
        contract.provider_candidate_payload()


def test_unknown_interaction_semantics_field_fails_closed():
    record, hypothesis = _record()
    payload = _contract(record, hypothesis).to_dict()
    payload["interaction_semantics"] = {"order": "event_gate_then_stock_confirmation"}
    payload["content_hash"] = stable_hash({key: value for key, value in payload.items() if key != "content_hash"})

    with pytest.raises(ValueError, match="interaction semantics fields mismatch"):
        DurableFrozenCandidateContractV1.from_dict(payload)
