from __future__ import annotations

import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.contract_correction import (
    ContractCorrectionError,
    FrozenContractCorrectionServiceV1,
    load_effective_contract_invalidations,
)
from chanlun_trader.research_factory.durability import DurableFrozenCandidateContractV1
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.objective import ResearchObjectiveV1
from chanlun_trader.research_factory.autonomous_orchestrator_v2 import CanonicalResearchStateReaderV2


ROOT = Path(__file__).resolve().parents[2]


def _incompatible_contract(tmp_path: Path, objective_id: str = "OBJECTIVE_CONTRACT_CORRECTION") -> tuple[Path, dict]:
    source_path = ROOT / "data/research/research_factory/batches/RUN_AUTONOMOUS_ALPHA_RESEARCH_NEW_BATCH_V1_B10/durable_frozen_candidate_contracts.json"
    contract = next(
        dict(item)
        for item in json.loads(source_path.read_text(encoding="utf-8"))["contracts"]
        if item["candidate_id"] == "CAND_RELATIVE_STRENGTH_SLOPE_WITH_TRAILING_HIGH_STRUC_067151B6_V1_V2"
    )
    DurableFrozenCandidateContractV1.from_dict(contract).provider_candidate_payload()
    contract["family"] = "CORRECTED_V3_HISTORICAL"
    contract["policy_identity"] = {"objective_id": objective_id}
    contract["content_hash"] = stable_hash({key: value for key, value in contract.items() if key != "content_hash"})
    contract_path = tmp_path / "data/research/research_factory/batches/B01/durable_frozen_candidate_contracts.json"
    contract_path.parent.mkdir(parents=True)
    contract_path.write_text(json.dumps({"contracts": [contract]}), encoding="utf-8")
    budget_path = contract_path.parent / "search_budget_registry.json"
    budget = SearchBudgetRegistryV1(objective_id, budget_path)
    budget.register_objective(4)
    return contract_path, contract


def test_incompatible_frozen_contract_is_invalidated_append_only_and_idempotently(tmp_path: Path):
    objective_id = "OBJECTIVE_CONTRACT_CORRECTION"
    contract_path, contract = _incompatible_contract(tmp_path, objective_id)
    objective_path = tmp_path / "data/research/research_factory/objectives" / f"{objective_id}.json"
    objective_path.parent.mkdir(parents=True)
    objective_path.write_text(json.dumps(ResearchObjectiveV1.default(objective_id).to_dict()), encoding="utf-8")
    service = FrozenContractCorrectionServiceV1(tmp_path)

    first = service.invalidate_incompatible_contract(
        objective_id=objective_id,
        candidate_id=contract["candidate_id"],
        expected_candidate_hash=contract["candidate_hash"],
        contract_ref=contract_path.relative_to(tmp_path).as_posix(),
    )
    second = service.invalidate_incompatible_contract(
        objective_id=objective_id,
        candidate_id=contract["candidate_id"],
        expected_candidate_hash=contract["candidate_hash"],
        contract_ref=contract_path.relative_to(tmp_path).as_posix(),
    )

    correction_path = tmp_path / "reports/research_daemon" / objective_id / "frozen_contract_corrections.jsonl"
    assert first == second
    assert len(correction_path.read_text(encoding="utf-8").splitlines()) == 1
    assert first["action"] == "INVALIDATE_EXECUTION"
    assert first["reason_code"] == "FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE"
    assert first["safety_evidence"]["performance_accessed"] is False
    assert first["safety_evidence"]["trial_records"] == 0
    assert first["safety_evidence"]["budget_used"] == 0
    assert first["safety_evidence"]["budget_reserved"] == 0
    assert load_effective_contract_invalidations(tmp_path, objective_id)[contract["candidate_id"]]["event_id"] == first["event_id"]
    assert CanonicalResearchStateReaderV2(tmp_path, objective_id).snapshot().remaining_frozen_candidates == 0


def test_contract_correction_preview_is_stable_and_does_not_write(tmp_path: Path):
    objective_id = "OBJECTIVE_CONTRACT_CORRECTION_PREVIEW"
    contract_path, contract = _incompatible_contract(tmp_path, objective_id)
    service = FrozenContractCorrectionServiceV1(tmp_path)
    arguments = {
        "objective_id": objective_id,
        "candidate_id": contract["candidate_id"],
        "expected_candidate_hash": contract["candidate_hash"],
        "contract_ref": contract_path.relative_to(tmp_path).as_posix(),
    }

    first = service.preview_incompatible_contract(**arguments)
    second = service.preview_incompatible_contract(**arguments)

    assert first == second
    assert first["status"] == "READY_TO_CONFIRM"
    assert first["available"] is True
    assert first["requires_confirmation"] is True
    assert first["safety_evidence"]["trial_records"] == 0
    assert first["safety_evidence"]["performance_accessed"] is False
    assert first["safety_evidence"]["candidate_active_reservations"] == 0
    assert not (tmp_path / "reports/research_daemon" / objective_id / "frozen_contract_corrections.jsonl").exists()


def test_contract_correction_confirmation_requires_current_preview(tmp_path: Path):
    objective_id = "OBJECTIVE_CONTRACT_CORRECTION_CONFIRM"
    contract_path, contract = _incompatible_contract(tmp_path, objective_id)
    service = FrozenContractCorrectionServiceV1(tmp_path)
    arguments = {
        "objective_id": objective_id,
        "candidate_id": contract["candidate_id"],
        "expected_candidate_hash": contract["candidate_hash"],
        "contract_ref": contract_path.relative_to(tmp_path).as_posix(),
    }
    preview = service.preview_incompatible_contract(**arguments)

    with pytest.raises(ContractCorrectionError, match="STALE_CONTRACT_CORRECTION_PREVIEW"):
        service.confirm_incompatible_contract(
            **arguments,
            preview_hash="stale",
            confirmation_token=preview["confirmation_token"],
        )

    event = service.confirm_incompatible_contract(
        **arguments,
        preview_hash=preview["preview_hash"],
        confirmation_token=preview["confirmation_token"],
    )
    assert event["action"] == "INVALIDATE_EXECUTION"
    assert load_effective_contract_invalidations(tmp_path, objective_id)[contract["candidate_id"]]["event_id"] == event["event_id"]


def test_contract_invalidation_refuses_candidate_with_trial_history(tmp_path: Path):
    objective_id = "OBJECTIVE_CONTRACT_CORRECTION_TRIAL"
    contract_path, contract = _incompatible_contract(tmp_path, objective_id)
    ledger_path = contract_path.parent / "factory_trial_ledger.json"
    ledger_path.write_text(json.dumps({"events": [{"trial_id": "TRIAL_1", "candidate_id": contract["candidate_id"], "performance_accessed": False}]}), encoding="utf-8")

    with pytest.raises(ContractCorrectionError, match="TRIAL_HISTORY_PRESENT"):
        FrozenContractCorrectionServiceV1(tmp_path).preview_incompatible_contract(
            objective_id=objective_id,
            candidate_id=contract["candidate_id"],
            expected_candidate_hash=contract["candidate_hash"],
            contract_ref=contract_path.relative_to(tmp_path).as_posix(),
        )
