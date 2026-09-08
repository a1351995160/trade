from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from chanlun_trader.research_factory.ai_design_approval import AIDesignApprovalServiceV1
from chanlun_trader.research_factory.candidate_executable_materialization import (
    CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION,
    CANDIDATE_SEMANTIC_DRIFT,
    CandidateExecutableMaterializationError,
    CandidateExecutableMaterializationManagerV1,
    EXECUTABLE_MATERIALIZATION_CONFIRMATION_MISSING,
    EXECUTABLE_MATERIALIZATION_CONFIRMATION_SCHEMA_VERSION,
    EXECUTABLE_CONTRACT_INVALID,
    EXECUTABLE_MATERIALIZATION_INCOMPLETE,
    EXECUTABLE_MATERIALIZATION_PREVIEW_READY,
    EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED,
    INTEGRITY_FAILURE,
    MATERIALIZATION_IDEMPOTENCY_CONFLICT,
    READY_FOR_STRUCTURAL_PREFLIGHT,
    RECOVER_EXECUTABLE_MATERIALIZATION,
    RUN_STRUCTURAL_PREFLIGHT,
    STALE_EXECUTABLE_MATERIALIZATION_PREVIEW,
)
from chanlun_trader.research_factory.candidate_generation import (
    CANDIDATE_FREEZE_RECEIPT_FILENAME,
    CANDIDATE_GOVERNANCE_FROZEN,
    CandidateGenerationManagerV1,
)
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.durability import (
    DurableFrozenCandidateContractRegistryV1,
    DurableFrozenCandidateContractV1,
    FROZEN_CANDIDATE_CONTRACT_REGISTRY_SCHEMA,
)
from chanlun_trader.research_factory.objective_reconciliation import ObjectiveReconciliationServiceV1
from chanlun_trader.research_factory.structural_entry import StructuralEntryError, StructuralEntryServiceV1
from chanlun_trader.research_factory.research_evolution_ai_design import ResearchEvolutionAIDesignServiceV1
from chanlun_trader.research.strategy_candidate import StrategyCandidateSpec
from chanlun_trader.research.strategy_semantic import build_semantic_record

from test_candidate_generation_governance_v1 import OBJECTIVE_ID, _fixture_root


BATCH_ID = "BATCH_EXECUTABLE_MATERIALIZATION_V1"
HYPOTHESIS_ID = "HYP_EXECUTABLE_MATERIALIZATION_V1"
_SEMANTIC_FIELDS = (
    "family",
    "mechanism",
    "factor_ids",
    "factor_roles",
    "factor_directions",
    "event_ids",
    "event_timing_semantics",
    "entry_predicate",
    "confirmation_predicate",
    "interaction_semantics",
    "ranking_semantics",
    "selection_rule",
    "top_n",
    "max_positions",
    "holding_period_trading_sessions",
    "entry_timing",
    "exit_contract",
    "execution_contract_version",
    "t_plus_1_contract",
    "pit_dependencies",
)


def _write_json(root: Path, relative_path: str, payload: dict) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _base_candidate() -> StrategyCandidateSpec:
    return StrategyCandidateSpec.create({
        "candidate_id": "CAND_EXECUTABLE_MATERIALIZATION_V1",
        "candidate_version": "V1",
        "parent_hypothesis_id": HYPOTHESIS_ID,
        "name": "Executable materialization fixture",
        "description": "Complete no-outcome candidate used by the materialization boundary tests.",
        "mechanism": "momentum",
        "strategy_family": "DAILY_FACTOR",
        "factor_bindings": ({"factor_id": "RETURN_5D", "role": "PRIMARY_ALPHA", "direction": "POSITIVE"},),
        "factor_roles": ({"factor_id": "RETURN_5D", "role": "PRIMARY_ALPHA", "direction": "POSITIVE"},),
        "signal_logic": {
            "hypothesis_entry_concept": "factor-defined momentum",
            "hypothesis_exit_concept": "fixed holding",
            "market_regime": (),
            "ranking_factor_id": "RETURN_5D",
            "ranking_direction": "POSITIVE",
            "event_dependencies": (),
        },
        "entry_rule": "RETURN_5D uses its factor-defined zero boundary",
        "entry_timing": {"type": "NEXT_SESSION_OPEN", "signal_time": "T_CLOSE"},
        "ranking_rule": {"type": "DESCENDING_FACTOR", "factor_id": "RETURN_5D", "direction": "POSITIVE"},
        "selection_rule": {"type": "TOP_N", "top_n": 1},
        "exit_rule": {"type": "FIXED_HOLD", "sessions": 5},
        "holding_period": 5,
        "position_sizing_rule": "EQUAL_WEIGHT",
        "max_positions": 1,
        "cash_rule": "NO_LEVERAGE",
        "risk_filters": (),
        "execution_filters": (),
        "universe_rule": {"market": ["SH", "SZ"], "pit_required": True},
        "price_mode": "RAW",
        "required_data": ("daily_ohlcva_raw",),
        "required_frequency": ("DAILY",),
        "available_at_contract": {"signal_available_at": "T_CLOSE", "factor_available_at": "<=T_CLOSE"},
        "t_plus_1_contract": {"enabled": True},
        "limit_up_down_contract": {"fail_closed": True},
        "suspension_contract": {"fail_closed": True},
        "lot_size_contract": {"size": 100},
        "fee_model_reference": "ENGINE_CURRENT_FEE_PROFILE",
        "slippage_model_reference": "ENGINE_FIXED_BPS_SLIPPAGE_REFERENCE",
        "parameter_spec": (),
        "parameter_provenance": {},
        "degrees_of_freedom": 0,
        "complexity_score": 1,
        "failure_conditions": ("PIT failure",),
        "validation_plan": ({"stage": "structural", "source": "human"},),
        "small_capital_prior": "GENERIC_10K",
        "source_provenance": {"source": "synthetic"},
        "created_at": "2026-09-05T00:00:00+08:00",
        "builder_version": "STRATEGY_CANDIDATE_BUILDER_V1",
        "candidate_status": "SPEC_READY",
        "candidate_type": "DAILY_SIGNAL",
        "conflict_semantics": {"duplicate_policy": "reject"},
        "complexity_justification": "single factor",
        "strategy_dsl": {"schema_version": "strategy-dsl-v1", "semantic_contract_version": "strategy-semantic-execution-v1"},
        "failure_overlap_warning": {},
    })


def _semantic_contract_fixture() -> DurableFrozenCandidateContractV1:
    candidate = _base_candidate()
    hypothesis = {
        "hypothesis_id": HYPOTHESIS_ID,
        "hypothesis_fingerprint": "HYPOTHESIS_FINGERPRINT_EXECUTABLE_MATERIALIZATION_V1",
        "hypothesis_type": "CONTINUATION",
        "market_regime": [],
    }
    record = build_semantic_record(candidate, hypothesis)
    return DurableFrozenCandidateContractV1.from_semantic_record(
        record,
        hypothesis,
        factor_event_registry_identities={
            "factor_registry": {"id": "FACTOR_REGISTRY_EXECUTABLE_MATERIALIZATION_V1"},
            "event_registry": {"id": "EVENT_REGISTRY_EXECUTABLE_MATERIALIZATION_V1"},
        },
        research_period_identity={"id": "RESEARCH_PERIOD_EXECUTABLE_MATERIALIZATION_V1", "start": 20200101, "end": 20251231},
        policy_identity={"objective_id": OBJECTIVE_ID, "policy_id": "POLICY_EXECUTABLE_MATERIALIZATION_V1"},
        source_provenance={"objective_id": OBJECTIVE_ID, "batch_id": BATCH_ID, "source": "synthetic"},
        created_frozen_timestamp="2026-09-05T00:00:00+08:00",
    )


def _bridge_fixture(tmp_path: Path) -> tuple[Path, dict, DurableFrozenCandidateContractV1]:
    root = _fixture_root(tmp_path)
    _write_json(root, f"data/research/research_factory/batches/{BATCH_ID}/search_budget_registry.json", {
        "objective_id": OBJECTIVE_ID,
        "used": 0,
        "reserved": 0,
        "total": 4,
    })
    ResearchEvolutionAIDesignServiceV1(root).generate_design(OBJECTIVE_ID)
    AIDesignApprovalServiceV1(root).approve(OBJECTIVE_ID, "bridge-reviewer", idempotency_key="BRIDGE_AI_APPROVAL_V1")
    proposal = CandidateGenerationManagerV1(root).generate_proposal(OBJECTIVE_ID)
    contract = _semantic_contract_fixture()

    proposal_path = root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "CANDIDATE_PROPOSAL.json"
    proposal_payload = json.loads(proposal_path.read_text(encoding="utf-8"))
    contract_payload = contract.to_dict()
    proposal_payload.update({
        "durable_contract": contract_payload,
        "candidate_semantic_contract": {key: contract_payload[key] for key in _SEMANTIC_FIELDS},
        "batch_id": BATCH_ID,
        "factor_event_registry_identities": contract.factor_event_registry_identities,
        "research_period_identity": contract.research_period_identity,
        "policy_identity": contract.policy_identity,
        "source_provenance": contract.source_provenance,
    })
    proposal_path.write_text(json.dumps(proposal_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt_base = {
        "schema_version": "candidate-freeze-v1",
        "freeze_id": "FREEZE_EXECUTABLE_MATERIALIZATION_V1",
        "proposal_id": proposal["proposal_id"],
        "proposal_hash": proposal["proposal_hash"],
        "candidate_id": contract.candidate_id,
        "candidate_hash": contract.candidate_hash,
        "reviewer": "bridge-reviewer",
        "timestamp": "2026-09-05T00:00:00+08:00",
        "preview_hash": "CANDIDATE_GOVERNANCE_PREVIEW_HASH_V1",
        "result_state": CANDIDATE_GOVERNANCE_FROZEN,
        "governance_state": CANDIDATE_GOVERNANCE_FROZEN,
        "next_action": "CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW",
        "candidate_created": True,
        "candidate_frozen": True,
        "executable_candidate_frozen": False,
        "structural_preflight_ready": False,
        "trial_started": False,
        "ai_called": False,
        "budget_consumed": False,
    }
    receipt = {**receipt_base, "receipt_hash": stable_hash(receipt_base)}
    _write_json(
        root,
        f"reports/research_candidates/proposals/{OBJECTIVE_ID}/{CANDIDATE_FREEZE_RECEIPT_FILENAME}",
        receipt,
    )

    registry_entry_base = {
        "candidate_id": contract.candidate_id,
        "candidate_hash": contract.candidate_hash,
        "objective_id": OBJECTIVE_ID,
        "proposal_id": proposal["proposal_id"],
        "proposal_hash": proposal["proposal_hash"],
        "contract": {"factor_contract": ["RETURN_5D"], "mechanism": contract.mechanism},
        "lineage": proposal_payload["lineage"],
        "state": "FROZEN",
        "governance_state": CANDIDATE_GOVERNANCE_FROZEN,
        "executable_candidate_frozen": False,
        "structural_preflight_ready": False,
        "freeze_id": receipt["freeze_id"],
        "frozen_at": receipt["timestamp"],
        "multiple_testing_family_id": proposal_payload["multiple_testing_family_id"],
        "performance_data_loaded": False,
        "budget_consumed": False,
    }
    registry_entry = {**registry_entry_base, "entry_hash": stable_hash(registry_entry_base)}
    _write_json(root, f"data/research/research_factory/candidates/{OBJECTIVE_ID}/CANDIDATE_REGISTRY.json", {
        "schema_version": "candidate-registry-v1",
        "objective_id": OBJECTIVE_ID,
        "candidates": [registry_entry],
        "registry_hash": stable_hash([registry_entry]),
        "append_only": True,
        "performance_data_loaded": False,
        "budget_consumed": False,
    })
    _write_json(root, f"data/research/research_factory/batches/{BATCH_ID}/search_budget_registry.json", {
        "objective_id": OBJECTIVE_ID,
        "used": 0,
        "reserved": 0,
        "total": 4,
    })
    return root, proposal, contract


def _durable_paths(root: Path) -> list[Path]:
    return sorted((root / "data/research/research_factory/batches").glob("*/durable_frozen_candidate_contracts.json"))


def test_governance_freeze_is_pending_and_not_structural_ready(tmp_path: Path) -> None:
    root, proposal, _ = _bridge_fixture(tmp_path)
    state = CandidateExecutableMaterializationManagerV1(root).read_state(OBJECTIVE_ID, proposal["proposal_id"])

    assert state["materialization_state"] == CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION
    assert state["required_action"] == "CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW"
    assert state["safe_to_advance"] is True
    assert state["structural_preflight_ready"] is False
    assert state["executable_candidate_frozen"] is False
    assert not _durable_paths(root)


def test_preview_is_immutable_complete_and_does_not_write_durable_store(tmp_path: Path) -> None:
    root, proposal, contract = _bridge_fixture(tmp_path)
    manager = CandidateExecutableMaterializationManagerV1(root)
    budget_path = root / "data/research/research_factory/batches" / BATCH_ID / "search_budget_registry.json"
    budget_before = budget_path.read_bytes()

    preview = manager.create_preview(OBJECTIVE_ID, proposal["proposal_id"])
    preview_path = root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "EXECUTABLE_MATERIALIZATION_PREVIEW.json"
    preview_before = preview_path.read_bytes()

    assert preview["status"] == EXECUTABLE_MATERIALIZATION_PREVIEW_READY
    assert preview["human_confirmation_required"] is True
    assert preview["candidate_id"] == contract.candidate_id
    assert preview["candidate_hash"] == contract.candidate_hash
    assert preview["durable_contract_hash"] == contract.content_hash
    assert preview["semantic_fingerprint"] == contract.semantic_fingerprint
    assert preview["source_candidate_proposal_hash"] == proposal["proposal_hash"]
    assert preview["freeze_receipt_hash"]
    assert preview["ai_design_hash"]
    assert preview["ai_design_approval_hash"]
    assert preview["research_period_identity"] == contract.research_period_identity
    assert preview["provider_readiness"]["status"] == "PASS"
    assert not _durable_paths(root)
    assert budget_path.read_bytes() == budget_before

    repeated = manager.create_preview(OBJECTIVE_ID, proposal["proposal_id"])
    assert repeated["idempotent"] is True
    assert preview_path.read_bytes() == preview_before
    assert not _durable_paths(root)


def test_preview_source_change_and_hash_tamper_are_fail_closed(tmp_path: Path) -> None:
    root, proposal, _ = _bridge_fixture(tmp_path / "stale")
    manager = CandidateExecutableMaterializationManagerV1(root)
    preview = manager.create_preview(OBJECTIVE_ID, proposal["proposal_id"])
    proposal_path = root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "CANDIDATE_PROPOSAL.json"
    changed = json.loads(proposal_path.read_text(encoding="utf-8"))
    changed["candidate_semantic_contract"]["mechanism"] = "changed-frozen-mechanism"
    proposal_path.write_text(json.dumps(changed, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    stale = manager.read_state(OBJECTIVE_ID, proposal["proposal_id"])
    assert stale["materialization_state"] == STALE_EXECUTABLE_MATERIALIZATION_PREVIEW
    with pytest.raises(CandidateExecutableMaterializationError) as error:
        manager.confirm(OBJECTIVE_ID, proposal["proposal_id"], {
            "confirmed": True,
            "reviewer": "bridge-reviewer",
            "preview_hash": preview["preview_hash"],
            "idempotency_key": "STALE_PREVIEW_CONFIRM",
        })
    assert error.value.code == STALE_EXECUTABLE_MATERIALIZATION_PREVIEW
    assert not _durable_paths(root)

    root2, proposal2, _ = _bridge_fixture(tmp_path / "tamper")
    manager2 = CandidateExecutableMaterializationManagerV1(root2)
    manager2.create_preview(OBJECTIVE_ID, proposal2["proposal_id"])
    preview_path2 = root2 / "reports/research_candidates/proposals" / OBJECTIVE_ID / "EXECUTABLE_MATERIALIZATION_PREVIEW.json"
    tampered = json.loads(preview_path2.read_text(encoding="utf-8"))
    tampered["preview_hash"] = "TAMPERED_PREVIEW_HASH"
    preview_path2.write_text(json.dumps(tampered, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    state = manager2.read_state(OBJECTIVE_ID, proposal2["proposal_id"])
    assert state["materialization_state"] == INTEGRITY_FAILURE


def test_missing_contract_fields_never_receive_defaults_and_semantic_drift_blocks(tmp_path: Path) -> None:
    root, proposal, _ = _bridge_fixture(tmp_path / "missing")
    proposal_path = root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "CANDIDATE_PROPOSAL.json"
    changed = json.loads(proposal_path.read_text(encoding="utf-8"))
    changed["durable_contract"].pop("top_n")
    proposal_path.write_text(json.dumps(changed, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(CandidateExecutableMaterializationError) as error:
        CandidateExecutableMaterializationManagerV1(root).create_preview(OBJECTIVE_ID, proposal["proposal_id"])
    assert error.value.code == EXECUTABLE_MATERIALIZATION_INCOMPLETE
    assert "top_n" in error.value.details["missing_required_fields"]
    assert error.value.details["safe_to_advance"] is False
    assert not _durable_paths(root)

    root2, proposal2, _ = _bridge_fixture(tmp_path / "drift")
    proposal_path2 = root2 / "reports/research_candidates/proposals" / OBJECTIVE_ID / "CANDIDATE_PROPOSAL.json"
    changed2 = json.loads(proposal_path2.read_text(encoding="utf-8"))
    changed2["candidate_semantic_contract"]["mechanism"] = "different-frozen-mechanism"
    proposal_path2.write_text(json.dumps(changed2, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(CandidateExecutableMaterializationError) as error2:
        CandidateExecutableMaterializationManagerV1(root2).create_preview(OBJECTIVE_ID, proposal2["proposal_id"])
    assert error2.value.code == CANDIDATE_SEMANTIC_DRIFT
    assert not _durable_paths(root2)


def test_confirm_writes_one_valid_contract_without_budget_trial_structural_or_ai_side_effects(tmp_path: Path) -> None:
    root, proposal, contract = _bridge_fixture(tmp_path)
    manager = CandidateExecutableMaterializationManagerV1(root)
    preview = manager.create_preview(OBJECTIVE_ID, proposal["proposal_id"])
    budget_path = root / "data/research/research_factory/batches" / BATCH_ID / "search_budget_registry.json"
    budget_before = budget_path.read_bytes()
    source_before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())

    result = manager.confirm(OBJECTIVE_ID, proposal["proposal_id"], {
        "confirmed": True,
        "reviewer": "bridge-reviewer",
        "preview_hash": preview["preview_hash"],
        "idempotency_key": "EXECUTABLE_CONFIRM_V1",
    })
    paths = _durable_paths(root)
    assert result["effective_state"] == READY_FOR_STRUCTURAL_PREFLIGHT
    assert result["required_action"] == RUN_STRUCTURAL_PREFLIGHT
    assert result["structural_preflight_ready"] is True
    assert result["automatic_structural_preflight"] is False
    assert result["automatic_trial_started"] is False
    assert result["ai_called"] is False
    assert result["budget_consumed"] is False
    assert len(paths) == 1
    stored = json.loads(paths[0].read_text(encoding="utf-8"))["contracts"]
    assert len(stored) == 1
    loaded = DurableFrozenCandidateContractV1.from_dict(stored[0])
    assert loaded.content_hash == contract.content_hash
    assert loaded.provider_candidate_payload()["candidate_id"] == contract.candidate_id
    assert budget_path.read_bytes() == budget_before
    new_files = {path for path in root.rglob("*") if path.is_file()} - {root / item for item in source_before}
    assert not any("structural" in path.name.casefold() or "trial" in path.name.casefold() for path in new_files)
    assert not (root / "reports/research_daemon").exists()

    restarted = CandidateExecutableMaterializationManagerV1(root)
    assert restarted.read_state(OBJECTIVE_ID, proposal["proposal_id"])["materialization_state"] == READY_FOR_STRUCTURAL_PREFLIGHT
    repeated = restarted.confirm(OBJECTIVE_ID, proposal["proposal_id"], {
        "confirmed": True,
        "reviewer": "bridge-reviewer",
        "preview_hash": preview["preview_hash"],
        "idempotency_key": "EXECUTABLE_CONFIRM_V1",
    })
    assert repeated["idempotent"] is True
    assert len(json.loads(paths[0].read_text(encoding="utf-8"))["contracts"]) == 1
    confirmation = json.loads((root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "EXECUTABLE_MATERIALIZATION_CONFIRMATION.json").read_text(encoding="utf-8"))
    assert {
        "confirmation_id", "objective_id", "proposal_id", "preview_id", "preview_hash",
        "candidate_id", "candidate_hash", "durable_contract_hash", "ai_design_id",
        "ai_design_hash", "ai_design_approval_hash", "source_context_id", "source_context_hash",
        "reviewer", "confirmed_at", "idempotency_key", "receipt_hash",
    } <= set(confirmation)


def test_crash_after_confirmation_receipt_is_recoverable_without_contract_or_side_effects(tmp_path: Path) -> None:
    root, proposal, _ = _bridge_fixture(tmp_path / "receipt-first")
    manager = CandidateExecutableMaterializationManagerV1(root, crash_at="after_confirmation_receipt")
    preview = manager.create_preview(OBJECTIVE_ID, proposal["proposal_id"])
    budget_path = root / "data/research/research_factory/batches" / BATCH_ID / "search_budget_registry.json"
    budget_before = budget_path.read_bytes()

    with pytest.raises(RuntimeError, match="after_confirmation_receipt"):
        manager.confirm(OBJECTIVE_ID, proposal["proposal_id"], {
            "confirmed": True,
            "reviewer": "bridge-reviewer",
            "preview_hash": preview["preview_hash"],
            "idempotency_key": "CRASH_AFTER_RECEIPT_V1",
        })

    confirmation_path = root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "EXECUTABLE_MATERIALIZATION_CONFIRMATION.json"
    assert confirmation_path.exists()
    assert not _durable_paths(root)
    after_restart = CandidateExecutableMaterializationManagerV1(root).read_state(OBJECTIVE_ID, proposal["proposal_id"])
    assert after_restart["materialization_state"] == EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED
    assert after_restart["materialization_confirmation_present"] is True
    assert after_restart["materialization_confirmation_valid"] is True
    assert after_restart["materialization_contract_present"] is False
    assert after_restart["structural_preflight_ready"] is False
    assert after_restart["safe_to_advance"] is False
    reconciled = ObjectiveReconciliationServiceV1(root).reconcile(OBJECTIVE_ID)
    assert reconciled["effective_state"] == EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED
    assert reconciled["required_action"] == "RECOVER_EXECUTABLE_MATERIALIZATION"
    assert reconciled["structural_preflight_ready"] is False

    recovered = CandidateExecutableMaterializationManagerV1(root).recover(OBJECTIVE_ID, proposal["proposal_id"])
    assert recovered["materialization_state"] == READY_FOR_STRUCTURAL_PREFLIGHT
    assert recovered["recovered"] is True
    assert len(_durable_paths(root)) == 1
    assert len(json.loads(_durable_paths(root)[0].read_text(encoding="utf-8"))["contracts"]) == 1
    assert budget_path.read_bytes() == budget_before


def test_receipt_only_is_not_structural_ready_and_structural_entry_rejects(tmp_path: Path) -> None:
    root, proposal, _ = _bridge_fixture(tmp_path / "receipt-semantics")
    manager = CandidateExecutableMaterializationManagerV1(root, crash_at="after_confirmation_receipt")
    preview = manager.create_preview(OBJECTIVE_ID, proposal["proposal_id"])

    with pytest.raises(RuntimeError, match="after_confirmation_receipt"):
        manager.confirm(OBJECTIVE_ID, proposal["proposal_id"], {
            "confirmed": True,
            "reviewer": "bridge-reviewer",
            "preview_hash": preview["preview_hash"],
            "idempotency_key": "RECEIPT_SEMANTICS_V1",
        })

    confirmation_path = root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "EXECUTABLE_MATERIALIZATION_CONFIRMATION.json"
    receipt = json.loads(confirmation_path.read_text(encoding="utf-8"))
    assert receipt["schema_version"] == EXECUTABLE_MATERIALIZATION_CONFIRMATION_SCHEMA_VERSION
    assert receipt["confirmation_status"] == "CONFIRMED"
    assert receipt["materialization_complete"] is False
    assert receipt["contract_materialization_required"] is True
    assert receipt["resulting_state"] == EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED
    assert receipt["next_action"] == RECOVER_EXECUTABLE_MATERIALIZATION
    assert receipt["automatic_structural_preflight"] is False
    assert receipt.get("structural_preflight_ready") is not True

    state = CandidateExecutableMaterializationManagerV1(root).read_state(OBJECTIVE_ID, proposal["proposal_id"])
    assert state["materialization_state"] == EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED
    assert state["structural_preflight_ready"] is False
    assert state["executable_candidate_frozen"] is False
    assert state["safe_to_advance"] is False
    assert state["materialization_contract_present"] is False

    report = ObjectiveReconciliationServiceV1(root).reconcile(OBJECTIVE_ID)
    assert report["effective_state"] == EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED
    assert report["structural_preflight_ready"] is False
    assert report["candidate_reconciliation"]["executable_frozen_candidate"] is False

    class CountingRuntime:
        structural_calls = 0

        def structural_preflight(self, *args: object, **kwargs: object) -> object:
            self.structural_calls += 1
            return None

    runtime = CountingRuntime()
    with pytest.raises(StructuralEntryError):
        StructuralEntryServiceV1(root, runtime=runtime).start(OBJECTIVE_ID, candidate_id=preview["candidate_id"])
    assert runtime.structural_calls == 0


def test_existing_contract_same_candidate_identity_but_different_content_hash_fails_before_receipt(tmp_path: Path) -> None:
    root, proposal, contract = _bridge_fixture(tmp_path / "existing-contract-mismatch")
    manager = CandidateExecutableMaterializationManagerV1(root)
    preview = manager.create_preview(OBJECTIVE_ID, proposal["proposal_id"])
    target = root / "data/research/research_factory/batches" / BATCH_ID / "durable_frozen_candidate_contracts.json"
    conflicting_payload = contract.to_dict()
    conflicting_payload["created_frozen_timestamp"] = "2026-09-06T00:00:00+08:00"
    conflicting_payload["content_hash"] = stable_hash({key: value for key, value in conflicting_payload.items() if key != "content_hash"})
    conflicting = DurableFrozenCandidateContractV1.from_dict(conflicting_payload)
    registry = DurableFrozenCandidateContractRegistryV1(target)
    registry.append(conflicting)
    registry.write()
    budget_path = root / "data/research/research_factory/batches" / BATCH_ID / "search_budget_registry.json"
    budget_before = budget_path.read_bytes()
    confirmation_path = root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "EXECUTABLE_MATERIALIZATION_CONFIRMATION.json"

    with pytest.raises(CandidateExecutableMaterializationError) as error:
        manager.confirm(OBJECTIVE_ID, proposal["proposal_id"], {
            "confirmed": True,
            "reviewer": "bridge-reviewer",
            "preview_hash": preview["preview_hash"],
            "idempotency_key": "EXISTING_CONTRACT_MISMATCH_V1",
        })

    assert error.value.code == "CANONICAL_CANDIDATE_IDENTITY_CONFLICT"
    assert error.value.details["preview_contract_hash"] == preview["durable_contract_hash"]
    assert error.value.details["existing_contract_hash"] == conflicting.content_hash
    assert not confirmation_path.exists()
    assert not (root / "reports/research_daemon").exists()
    assert budget_path.read_bytes() == budget_before

    state = manager.read_state(OBJECTIVE_ID, proposal["proposal_id"])
    assert state["materialization_state"] == "CANONICAL_CANDIDATE_IDENTITY_CONFLICT"
    assert state["structural_preflight_ready"] is False
    assert state["executable_candidate_frozen"] is False
    assert state["materialization_confirmation_present"] is False
    assert state["materialization_contract_present"] is True
    assert state["materialization_identity_match"] is False
    assert len(json.loads(target.read_text(encoding="utf-8"))["contracts"]) == 1

    report = ObjectiveReconciliationServiceV1(root).reconcile(OBJECTIVE_ID)
    assert report["conflict_level"] == "CANONICAL_CONFLICT"
    assert "CANONICAL_CANDIDATE_IDENTITY_CONFLICT" in report["conflicts"]
    assert report["structural_preflight_ready"] is False
    assert report["candidate_reconciliation"]["executable_frozen_candidate"] is False

    class CountingRuntime:
        structural_calls = 0

        def structural_preflight(self, *args: object, **kwargs: object) -> object:
            self.structural_calls += 1
            return None

    runtime = CountingRuntime()
    with pytest.raises(StructuralEntryError):
        StructuralEntryServiceV1(root, runtime=runtime).start(OBJECTIVE_ID, candidate_id=contract.candidate_id)
    assert runtime.structural_calls == 0


def test_existing_contract_exact_match_reuses_immutable_contract_and_receipt(tmp_path: Path) -> None:
    root, proposal, contract = _bridge_fixture(tmp_path / "existing-contract-exact-match")
    manager = CandidateExecutableMaterializationManagerV1(root)
    preview = manager.create_preview(OBJECTIVE_ID, proposal["proposal_id"])
    target = root / "data/research/research_factory/batches" / BATCH_ID / "durable_frozen_candidate_contracts.json"
    registry = DurableFrozenCandidateContractRegistryV1(target)
    registry.append(contract)
    registry.write()
    contract_before = target.read_bytes()
    request = {
        "confirmed": True,
        "reviewer": "bridge-reviewer",
        "preview_hash": preview["preview_hash"],
        "idempotency_key": "EXISTING_CONTRACT_EXACT_MATCH_V1",
    }

    first = manager.confirm(OBJECTIVE_ID, proposal["proposal_id"], request)
    confirmation_path = root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "EXECUTABLE_MATERIALIZATION_CONFIRMATION.json"
    receipt_before = confirmation_path.read_bytes()
    assert first["idempotent"] is False
    assert first["effective_state"] == READY_FOR_STRUCTURAL_PREFLIGHT
    assert target.read_bytes() == contract_before
    assert len(json.loads(target.read_text(encoding="utf-8"))["contracts"]) == 1

    second = manager.confirm(OBJECTIVE_ID, proposal["proposal_id"], request)
    assert second["idempotent"] is True
    assert second["effective_state"] == READY_FOR_STRUCTURAL_PREFLIGHT
    assert target.read_bytes() == contract_before
    assert confirmation_path.read_bytes() == receipt_before
    assert len(json.loads(target.read_text(encoding="utf-8"))["contracts"]) == 1
    assert confirmation_path.exists()


def test_crash_after_contract_append_restarts_to_ready_and_retries_exactly_once(tmp_path: Path) -> None:
    root, proposal, _ = _bridge_fixture(tmp_path / "contract-first-restart")
    manager = CandidateExecutableMaterializationManagerV1(root, crash_at="after_durable_contract_append")
    preview = manager.create_preview(OBJECTIVE_ID, proposal["proposal_id"])
    request = {
        "confirmed": True,
        "reviewer": "bridge-reviewer",
        "preview_hash": preview["preview_hash"],
        "idempotency_key": "CRASH_AFTER_CONTRACT_V1",
    }
    with pytest.raises(RuntimeError, match="after_durable_contract_append"):
        manager.confirm(OBJECTIVE_ID, proposal["proposal_id"], request)

    restarted = CandidateExecutableMaterializationManagerV1(root)
    state = restarted.read_state(OBJECTIVE_ID, proposal["proposal_id"])
    assert state["materialization_state"] == READY_FOR_STRUCTURAL_PREFLIGHT
    assert state["materialization_confirmation_valid"] is True
    assert state["materialization_identity_match"] is True
    assert state["required_action"] == RUN_STRUCTURAL_PREFLIGHT
    reconciled = ObjectiveReconciliationServiceV1(root).reconcile(OBJECTIVE_ID)
    assert reconciled["effective_state"] == READY_FOR_STRUCTURAL_PREFLIGHT
    assert reconciled["required_action"] == RUN_STRUCTURAL_PREFLIGHT
    assert len(_durable_paths(root)) == 1
    assert len(json.loads(_durable_paths(root)[0].read_text(encoding="utf-8"))["contracts"]) == 1

    recovered = restarted.recover(OBJECTIVE_ID, proposal["proposal_id"])
    assert recovered["materialization_state"] == READY_FOR_STRUCTURAL_PREFLIGHT
    repeated = restarted.confirm(OBJECTIVE_ID, proposal["proposal_id"], request)
    assert repeated["idempotent"] is True
    assert len(json.loads(_durable_paths(root)[0].read_text(encoding="utf-8"))["contracts"]) == 1


def test_different_materialization_idempotency_key_conflicts_without_overwrite(tmp_path: Path) -> None:
    root, proposal, _ = _bridge_fixture(tmp_path)
    manager = CandidateExecutableMaterializationManagerV1(root)
    preview = manager.create_preview(OBJECTIVE_ID, proposal["proposal_id"])
    request = {
        "confirmed": True,
        "reviewer": "bridge-reviewer",
        "preview_hash": preview["preview_hash"],
        "idempotency_key": "IDEMPOTENCY_ORIGINAL_V1",
    }
    manager.confirm(OBJECTIVE_ID, proposal["proposal_id"], request)
    confirmation_path = root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "EXECUTABLE_MATERIALIZATION_CONFIRMATION.json"
    contract_path = _durable_paths(root)[0]
    confirmation_before = confirmation_path.read_bytes()
    contract_before = contract_path.read_bytes()

    with pytest.raises(CandidateExecutableMaterializationError) as error:
        manager.confirm(OBJECTIVE_ID, proposal["proposal_id"], {**request, "idempotency_key": "IDEMPOTENCY_OTHER_V1"})
    assert error.value.code == MATERIALIZATION_IDEMPOTENCY_CONFLICT
    assert confirmation_path.read_bytes() == confirmation_before
    assert contract_path.read_bytes() == contract_before
    assert manager.read_state(OBJECTIVE_ID, proposal["proposal_id"])["materialization_state"] == READY_FOR_STRUCTURAL_PREFLIGHT


def test_contract_only_crash_state_is_fail_closed_at_reconciliation_and_structural_entry(tmp_path: Path) -> None:
    root, proposal, contract = _bridge_fixture(tmp_path / "contract-only")
    manager = CandidateExecutableMaterializationManagerV1(root)
    preview = manager.create_preview(OBJECTIVE_ID, proposal["proposal_id"])
    target = root / "data/research/research_factory/batches" / BATCH_ID / "durable_frozen_candidate_contracts.json"
    registry = DurableFrozenCandidateContractRegistryV1(target)
    registry.append(contract)
    registry.write()

    state = manager.read_state(OBJECTIVE_ID, proposal["proposal_id"])
    assert state["materialization_state"] == EXECUTABLE_MATERIALIZATION_CONFIRMATION_MISSING
    assert state["structural_preflight_ready"] is False
    assert state["safe_to_advance"] is False
    report = ObjectiveReconciliationServiceV1(root).reconcile(OBJECTIVE_ID)
    assert report["effective_state"] == EXECUTABLE_MATERIALIZATION_CONFIRMATION_MISSING
    assert report["required_action"] == "HUMAN_CONFIRM_EXECUTABLE_MATERIALIZATION"
    assert report["candidate_reconciliation"]["executable_frozen_candidate"] is False
    assert report["candidate_reconciliation"]["materialization_confirmation_present"] is False

    class CountingRuntime:
        structural_calls = 0

        def structural_preflight(self, *args: object, **kwargs: object) -> object:
            self.structural_calls += 1
            return None

    runtime = CountingRuntime()
    with pytest.raises(StructuralEntryError) as error:
        StructuralEntryServiceV1(root, runtime=runtime).start(OBJECTIVE_ID, candidate_id=contract.candidate_id)
    assert error.value.code == "STRUCTURAL_ENTRY_GATE_BLOCKED"
    assert runtime.structural_calls == 0
    assert preview["durable_contract_hash"] == contract.content_hash


def test_confirmation_and_contract_tamper_or_identity_mismatch_never_become_ready(tmp_path: Path) -> None:
    root, proposal, _ = _bridge_fixture(tmp_path / "receipt-tamper")
    manager = CandidateExecutableMaterializationManagerV1(root)
    preview = manager.create_preview(OBJECTIVE_ID, proposal["proposal_id"])
    manager.confirm(OBJECTIVE_ID, proposal["proposal_id"], {
        "confirmed": True,
        "reviewer": "bridge-reviewer",
        "preview_hash": preview["preview_hash"],
        "idempotency_key": "TAMPER_RECEIPT_V1",
    })
    receipt_path = root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "EXECUTABLE_MATERIALIZATION_CONFIRMATION.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reviewer"] = "attacker"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tampered = CandidateExecutableMaterializationManagerV1(root).read_state(OBJECTIVE_ID, proposal["proposal_id"])
    assert tampered["materialization_state"] == INTEGRITY_FAILURE
    assert tampered["structural_preflight_ready"] is False

    root2, proposal2, contract2 = _bridge_fixture(tmp_path / "contract-tamper")
    manager2 = CandidateExecutableMaterializationManagerV1(root2)
    preview2 = manager2.create_preview(OBJECTIVE_ID, proposal2["proposal_id"])
    manager2.confirm(OBJECTIVE_ID, proposal2["proposal_id"], {
        "confirmed": True,
        "reviewer": "bridge-reviewer",
        "preview_hash": preview2["preview_hash"],
        "idempotency_key": "TAMPER_CONTRACT_V1",
    })
    contract_path = _durable_paths(root2)[0]
    stored = json.loads(contract_path.read_text(encoding="utf-8"))
    stored["contracts"][0]["mechanism"] = "tampered-mechanism"
    contract_path.write_text(json.dumps(stored, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    invalid = CandidateExecutableMaterializationManagerV1(root2).read_state(OBJECTIVE_ID, proposal2["proposal_id"])
    assert invalid["materialization_state"] == EXECUTABLE_CONTRACT_INVALID
    assert invalid["structural_preflight_ready"] is False

    root3, proposal3, contract3 = _bridge_fixture(tmp_path / "identity-mismatch")
    manager3 = CandidateExecutableMaterializationManagerV1(root3)
    preview3 = manager3.create_preview(OBJECTIVE_ID, proposal3["proposal_id"])
    manager3.confirm(OBJECTIVE_ID, proposal3["proposal_id"], {
        "confirmed": True,
        "reviewer": "bridge-reviewer",
        "preview_hash": preview3["preview_hash"],
        "idempotency_key": "MISMATCH_CONTRACT_V1",
    })
    contract_path3 = _durable_paths(root3)[0]
    stored3 = json.loads(contract_path3.read_text(encoding="utf-8"))
    stored3["contracts"][0]["candidate_hash"] = "OTHER_HASH"
    stored3["contracts"][0]["content_hash"] = stable_hash({key: value for key, value in stored3["contracts"][0].items() if key != "content_hash"})
    contract_path3.write_text(json.dumps(stored3, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    mismatch = CandidateExecutableMaterializationManagerV1(root3).read_state(OBJECTIVE_ID, proposal3["proposal_id"])
    assert mismatch["materialization_state"] == "CANONICAL_CANDIDATE_IDENTITY_CONFLICT"
    assert mismatch["structural_preflight_ready"] is False
    assert contract3.candidate_hash != "OTHER_HASH"


def test_identity_conflict_and_invalid_provider_never_become_structural_ready(tmp_path: Path) -> None:
    root, proposal, contract = _bridge_fixture(tmp_path / "identity")
    manager = CandidateExecutableMaterializationManagerV1(root)
    preview = manager.create_preview(OBJECTIVE_ID, proposal["proposal_id"])
    conflicting = contract.to_dict()
    conflicting["candidate_hash"] = "DIFFERENT_CANDIDATE_HASH"
    conflicting["content_hash"] = stable_hash({key: value for key, value in conflicting.items() if key != "content_hash"})
    _write_json(root, f"data/research/research_factory/batches/{BATCH_ID}/durable_frozen_candidate_contracts.json", {
        "schema_version": FROZEN_CANDIDATE_CONTRACT_REGISTRY_SCHEMA,
        "contracts": [conflicting],
        "registry_hash": stable_hash([conflicting]),
    })
    with pytest.raises(CandidateExecutableMaterializationError) as conflict:
        manager.confirm(OBJECTIVE_ID, proposal["proposal_id"], {
            "confirmed": True,
            "reviewer": "bridge-reviewer",
            "preview_hash": preview["preview_hash"],
            "idempotency_key": "IDENTITY_CONFLICT_CONFIRM",
        })
    assert conflict.value.code == "CANONICAL_CANDIDATE_IDENTITY_CONFLICT"

    root2, proposal2, _ = _bridge_fixture(tmp_path / "provider")
    proposal_path = root2 / "reports/research_candidates/proposals" / OBJECTIVE_ID / "CANDIDATE_PROPOSAL.json"
    changed = json.loads(proposal_path.read_text(encoding="utf-8"))
    changed["durable_contract"]["family"] = "UNSUPPORTED_PROVIDER_FAMILY"
    changed["candidate_semantic_contract"]["family"] = "UNSUPPORTED_PROVIDER_FAMILY"
    changed["durable_contract"]["content_hash"] = stable_hash({key: value for key, value in changed["durable_contract"].items() if key != "content_hash"})
    proposal_path.write_text(json.dumps(changed, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    invalid_preview = CandidateExecutableMaterializationManagerV1(root2).create_preview(OBJECTIVE_ID, proposal2["proposal_id"])
    assert invalid_preview["status"] == EXECUTABLE_CONTRACT_INVALID
    assert invalid_preview["safe_to_advance"] is False
    assert CandidateExecutableMaterializationManagerV1(root2).read_state(OBJECTIVE_ID, proposal2["proposal_id"])["materialization_state"] == EXECUTABLE_CONTRACT_INVALID
    assert not _durable_paths(root2)

    root3, proposal3, _ = _bridge_fixture(tmp_path / "registry-conflict")
    registry_path = root3 / "data/research/research_factory/candidates" / OBJECTIVE_ID / "CANDIDATE_REGISTRY.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    conflicting_entry = dict(registry["candidates"][0])
    conflicting_entry["candidate_hash"] = "REGISTRY_HASH_CONFLICT"
    conflicting_entry["entry_hash"] = stable_hash({key: value for key, value in conflicting_entry.items() if key != "entry_hash"})
    registry["candidates"].append(conflicting_entry)
    registry["registry_hash"] = stable_hash(registry["candidates"])
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(CandidateExecutableMaterializationError) as registry_conflict:
        CandidateExecutableMaterializationManagerV1(root3).create_preview(OBJECTIVE_ID, proposal3["proposal_id"])
    assert registry_conflict.value.code == "CANONICAL_CANDIDATE_IDENTITY_CONFLICT"
    assert not _durable_paths(root3)


def test_reconciliation_exposes_governance_preview_and_executable_layers(tmp_path: Path) -> None:
    root, proposal, _ = _bridge_fixture(tmp_path)
    service = ObjectiveReconciliationServiceV1(root)
    pending = service.reconcile(OBJECTIVE_ID)
    assert pending["effective_state"] == CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION
    assert pending["required_action"] == "CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW"
    assert pending["safe_to_advance"] is True
    assert pending["structural_preflight_ready"] is False

    manager = CandidateExecutableMaterializationManagerV1(root)
    preview = manager.create_preview(OBJECTIVE_ID, proposal["proposal_id"])
    preview_state = service.reconcile(OBJECTIVE_ID)
    assert preview_state["effective_state"] == EXECUTABLE_MATERIALIZATION_PREVIEW_READY
    assert preview_state["required_action"] == "HUMAN_CONFIRM_EXECUTABLE_MATERIALIZATION"
    assert preview_state["structural_preflight_ready"] is False
    assert preview_state["candidate_reconciliation"]["materialization"]["preview_ready"] is True

    manager.confirm(OBJECTIVE_ID, proposal["proposal_id"], {
        "confirmed": True,
        "reviewer": "bridge-reviewer",
        "preview_hash": preview["preview_hash"],
        "idempotency_key": "RECONCILIATION_CONFIRM_V1",
    })
    ready = service.reconcile(OBJECTIVE_ID)
    assert ready["effective_state"] == READY_FOR_STRUCTURAL_PREFLIGHT
    assert ready["required_action"] == RUN_STRUCTURAL_PREFLIGHT
    assert ready["structural_preflight_ready"] is True


def test_reconciliation_registry_durable_hash_mismatch_is_canonical_conflict(tmp_path: Path) -> None:
    root, _, contract = _bridge_fixture(tmp_path)
    raw = contract.to_dict()
    raw["candidate_hash"] = "DURABLE_HASH_DOES_NOT_MATCH_REGISTRY"
    raw["content_hash"] = stable_hash({key: value for key, value in raw.items() if key != "content_hash"})
    _write_json(root, f"data/research/research_factory/batches/{BATCH_ID}/durable_frozen_candidate_contracts.json", {
        "schema_version": FROZEN_CANDIDATE_CONTRACT_REGISTRY_SCHEMA,
        "contracts": [raw],
        "registry_hash": stable_hash([raw]),
    })
    report = ObjectiveReconciliationServiceV1(root).reconcile(OBJECTIVE_ID)
    assert report["conflict_level"] == "CANONICAL_CONFLICT"
    assert "CANONICAL_CANDIDATE_IDENTITY_CONFLICT" in report["conflicts"]
    assert report["structural_preflight_ready"] is False


def test_web_and_cli_require_preview_then_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    import chanlun_trader.webapp as webapp
    application = webapp.create_app(tmp_path, webapp.ExecutionPolicy("GOVERNED", "SYNTHETIC"))

    root, proposal, _ = _bridge_fixture(tmp_path / "web")
    manager = CandidateExecutableMaterializationManagerV1(root)
    monkeypatch.setattr(application.state.services, "candidate_materialization_service", manager)
    route = f"/api/research/candidates/proposals/{proposal['proposal_id']}/materialization"
    with TestClient(application) as client:
        missing_preview = client.post(f"{route}/confirm", json={"confirmed": True, "reviewer": "bridge-reviewer", "preview_hash": "NO_PREVIEW", "idempotency_key": "WEB_CONFIRM_WITHOUT_PREVIEW"})
        assert missing_preview.status_code == 409
        assert missing_preview.json()["code"] == "EXECUTABLE_MATERIALIZATION_PREVIEW_REQUIRED"
        created = client.post(f"{route}/preview")
        assert created.status_code == 200
        assert created.json()["status"] == EXECUTABLE_MATERIALIZATION_PREVIEW_READY
        assert not _durable_paths(root)
        confirmed = client.post(f"{route}/confirm", json={
            "confirmed": True,
            "reviewer": "bridge-reviewer",
            "preview_hash": created.json()["preview_hash"],
            "idempotency_key": "WEB_CONFIRM_V1",
        })
        assert confirmed.status_code == 200
        assert confirmed.json()["effective_state"] == READY_FOR_STRUCTURAL_PREFLIGHT

    root2, proposal2, _ = _bridge_fixture(tmp_path / "cli")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, [os.environ.get("PYTHONPATH"), str(Path("src").resolve())]))
    command = [
        sys.executable,
        "-m",
        "chanlun_trader.research_factory.candidate_executable_materialization",
        "--root",
        str(root2),
        "--objective-id",
        OBJECTIVE_ID,
        "--proposal-id",
        proposal2["proposal_id"],
        "--confirm",
        "--preview-hash",
        "NO_PREVIEW",
        "--reviewer",
        "bridge-reviewer",
        "--idempotency-key",
        "CLI_CONFIRM_WITHOUT_PREVIEW",
    ]
    completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], env=environment, capture_output=True, text=True)
    assert completed.returncode != 0
    assert "EXECUTABLE_MATERIALIZATION_PREVIEW_REQUIRED" in completed.stderr or "EXECUTABLE_MATERIALIZATION_PREVIEW_REQUIRED" in completed.stdout
    assert not _durable_paths(root2)


def test_daemon_contract_loader_has_no_light_registry_fallback() -> None:
    daemon_source = (Path(__file__).resolve().parents[2] / "src/chanlun_trader/research_daemon.py").read_text(encoding="utf-8")
    assert "durable_frozen_candidate_contracts.json" in daemon_source
    assert "DurableFrozenCandidateContractV1.from_dict" in daemon_source
    assert "provider_candidate_payload" in daemon_source
    assert "CANDIDATE_REGISTRY.json" not in daemon_source
