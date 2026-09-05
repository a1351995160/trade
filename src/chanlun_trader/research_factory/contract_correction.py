"""Append-only invalidation for frozen contracts that cannot reach a provider.

The original contract remains immutable.  A correction event only removes the
candidate from executable work after proving that no Trial or performance
access exists for that candidate.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .budget import SearchBudgetRegistryV1
from .common import jsonable, now_timestamp, stable_hash
from .durability import DurableFrozenCandidateContractV1


CORRECTION_SCHEMA = "frozen-contract-correction-v1"
CORRECTION_FILENAME = "frozen_contract_corrections.jsonl"
CORRECTION_PREVIEW_SCHEMA = "frozen-contract-correction-preview-v1"


class ContractCorrectionError(RuntimeError):
    pass


def _correction_path(root: Path, objective_id: str) -> Path:
    return root / "reports" / "research_daemon" / objective_id / CORRECTION_FILENAME


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    previous_hash = ""
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContractCorrectionError(f"CORRECTION_LOG_INVALID_JSON:{line_number}") from exc
        if not isinstance(event, Mapping) or event.get("schema_version") != CORRECTION_SCHEMA:
            raise ContractCorrectionError(f"CORRECTION_LOG_SCHEMA_INVALID:{line_number}")
        payload = dict(event)
        event_hash = str(payload.pop("event_hash", ""))
        if str(payload.get("previous_event_hash", "")) != previous_hash or event_hash != stable_hash(payload):
            raise ContractCorrectionError(f"CORRECTION_LOG_HASH_CHAIN_INVALID:{line_number}")
        events.append(dict(event))
        previous_hash = event_hash
    return events


def load_effective_contract_invalidations(root: str | Path, objective_id: str) -> dict[str, dict[str, Any]]:
    events = _read_events(_correction_path(Path(root).resolve(), str(objective_id)))
    return {
        str(event["candidate_id"]): event
        for event in events
        if event.get("action") == "INVALIDATE_EXECUTION" and str(event.get("objective_id")) == str(objective_id)
    }


class FrozenContractCorrectionServiceV1:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _contract(self, contract_ref: str, objective_id: str, candidate_id: str) -> tuple[Path, dict[str, Any]]:
        path = (self.root / str(contract_ref).replace("\\", "/")).resolve()
        if Path(contract_ref).is_absolute() or not path.is_relative_to(self.root) or not path.exists():
            raise ContractCorrectionError("CONTRACT_REF_UNSAFE_OR_MISSING")
        payload = json.loads(path.read_text(encoding="utf-8"))
        contracts = payload.get("contracts", ()) if isinstance(payload, Mapping) else ()
        matches = [dict(item) for item in contracts if isinstance(item, Mapping) and str(item.get("candidate_id")) == candidate_id]
        if len(matches) != 1:
            raise ContractCorrectionError("FROZEN_CONTRACT_IDENTITY_NOT_UNIQUE")
        contract = matches[0]
        if str((contract.get("policy_identity") or {}).get("objective_id")) != objective_id:
            raise ContractCorrectionError("FROZEN_CONTRACT_OBJECTIVE_MISMATCH")
        return path, contract

    def _trial_events(self, candidate_id: str) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for path in (self.root / "data/research/research_factory/batches").glob("*/factory_trial_ledger.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for event in payload.get("events", ()) if isinstance(payload, Mapping) else ():
                if isinstance(event, Mapping) and str(event.get("candidate_id")) == candidate_id:
                    matches.append(dict(event))
        return matches

    def _budget_evidence(self, objective_id: str, candidate_id: str) -> dict[str, Any]:
        registries: list[tuple[str, Path]] = []
        for path in (self.root / "data/research/research_factory/batches").glob("*/search_budget_registry.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, Mapping) and str(payload.get("objective_id")) == objective_id:
                registries.append((str(payload.get("updated_at") or ""), path))
        if not registries:
            raise ContractCorrectionError("SEARCH_BUDGET_REGISTRY_MISSING")
        path = max(registries, key=lambda item: item[0])[1]
        snapshot = SearchBudgetRegistryV1(objective_id, path).snapshot()
        candidate_reservations = [
            dict(value)
            for value in snapshot.get("active_reservations", {}).values()
            if isinstance(value, Mapping) and str(value.get("candidate_id")) == candidate_id
        ]
        if candidate_reservations:
            raise ContractCorrectionError("ACTIVE_CANDIDATE_BUDGET_RESERVATION_PRESENT")
        bucket = next((item for item in snapshot.get("buckets", ()) if item.get("kind") == "objective" and item.get("key") == objective_id), {})
        return {
            "budget_registry_ref": path.relative_to(self.root).as_posix(),
            "budget_used": int(bucket.get("used", 0)),
            "budget_reserved": int(bucket.get("reserved", 0)),
            "candidate_active_reservations": 0,
        }

    def preview_incompatible_contract(
        self,
        *,
        objective_id: str,
        candidate_id: str,
        expected_candidate_hash: str,
        contract_ref: str,
    ) -> dict[str, Any]:
        objective_id = str(objective_id)
        candidate_id = str(candidate_id)
        path, contract = self._contract(contract_ref, objective_id, candidate_id)
        if str(contract.get("candidate_hash")) != str(expected_candidate_hash):
            raise ContractCorrectionError("FROZEN_CONTRACT_HASH_MISMATCH")

        relative_contract_ref = path.relative_to(self.root).as_posix()
        for event in _read_events(_correction_path(self.root, objective_id)):
            if str(event.get("candidate_id")) != candidate_id:
                continue
            if str(event.get("candidate_hash")) != str(expected_candidate_hash) or str(event.get("contract_ref")) != relative_contract_ref:
                raise ContractCorrectionError("CONTRACT_CORRECTION_IDENTITY_CONFLICT")
            raise ContractCorrectionError("CONTRACT_CORRECTION_ALREADY_APPLIED")

        if self._trial_events(candidate_id):
            raise ContractCorrectionError("TRIAL_HISTORY_PRESENT")
        try:
            DurableFrozenCandidateContractV1.from_dict(contract).provider_candidate_payload()
        except (KeyError, TypeError, ValueError) as exc:
            compatibility_error = f"{type(exc).__name__}:{exc}"
        else:
            raise ContractCorrectionError("FROZEN_CONTRACT_IS_PROVIDER_COMPATIBLE")

        preview_payload = jsonable({
            "schema_version": CORRECTION_PREVIEW_SCHEMA,
            "status": "READY_TO_CONFIRM",
            "available": True,
            "objective_id": objective_id,
            "candidate_id": candidate_id,
            "candidate_hash": str(expected_candidate_hash),
            "contract_ref": relative_contract_ref,
            "contract_content_hash": str(contract.get("content_hash") or ""),
            "action": "INVALIDATE_EXECUTION",
            "reason_code": "FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE",
            "provider_compatibility_error": compatibility_error,
            "safety_evidence": {
                "trial_records": 0,
                "performance_accessed": False,
                **self._budget_evidence(objective_id, candidate_id),
            },
            "requires_confirmation": True,
        })
        preview_hash = stable_hash(preview_payload)
        confirmation_token = stable_hash({
            "preview_hash": preview_hash,
            "action": "CONFIRM_CONTRACT_INVALIDATION",
        })
        return {**preview_payload, "preview_hash": preview_hash, "confirmation_token": confirmation_token}

    def confirm_incompatible_contract(
        self,
        *,
        objective_id: str,
        candidate_id: str,
        expected_candidate_hash: str,
        contract_ref: str,
        preview_hash: str,
        confirmation_token: str,
    ) -> dict[str, Any]:
        preview = self.preview_incompatible_contract(
            objective_id=objective_id,
            candidate_id=candidate_id,
            expected_candidate_hash=expected_candidate_hash,
            contract_ref=contract_ref,
        )
        if str(preview_hash) != preview["preview_hash"] or str(confirmation_token) != preview["confirmation_token"]:
            raise ContractCorrectionError("STALE_CONTRACT_CORRECTION_PREVIEW")
        return self.invalidate_incompatible_contract(
            objective_id=objective_id,
            candidate_id=candidate_id,
            expected_candidate_hash=expected_candidate_hash,
            contract_ref=contract_ref,
        )

    def invalidate_incompatible_contract(
        self,
        *,
        objective_id: str,
        candidate_id: str,
        expected_candidate_hash: str,
        contract_ref: str,
    ) -> dict[str, Any]:
        objective_id = str(objective_id)
        candidate_id = str(candidate_id)
        path, contract = self._contract(contract_ref, objective_id, candidate_id)
        if str(contract.get("candidate_hash")) != str(expected_candidate_hash):
            raise ContractCorrectionError("FROZEN_CONTRACT_HASH_MISMATCH")
        try:
            DurableFrozenCandidateContractV1.from_dict(contract).provider_candidate_payload()
        except (KeyError, TypeError, ValueError) as exc:
            compatibility_error = f"{type(exc).__name__}:{exc}"
        else:
            raise ContractCorrectionError("FROZEN_CONTRACT_IS_PROVIDER_COMPATIBLE")

        trial_events = self._trial_events(candidate_id)
        if trial_events:
            raise ContractCorrectionError("TRIAL_HISTORY_PRESENT")
        budget_evidence = self._budget_evidence(objective_id, candidate_id)
        correction_path = _correction_path(self.root, objective_id)
        existing = _read_events(correction_path)
        for event in existing:
            if str(event.get("candidate_id")) == candidate_id:
                if str(event.get("candidate_hash")) != str(expected_candidate_hash) or str(event.get("contract_ref")) != path.relative_to(self.root).as_posix():
                    raise ContractCorrectionError("CONTRACT_CORRECTION_IDENTITY_CONFLICT")
                return event

        event_payload = {
            "schema_version": CORRECTION_SCHEMA,
            "event_id": stable_hash({
                "objective_id": objective_id,
                "candidate_id": candidate_id,
                "candidate_hash": expected_candidate_hash,
                "contract_content_hash": contract.get("content_hash"),
                "action": "INVALIDATE_EXECUTION",
                "reason_code": "FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE",
            }),
            "objective_id": objective_id,
            "candidate_id": candidate_id,
            "candidate_hash": str(expected_candidate_hash),
            "contract_ref": path.relative_to(self.root).as_posix(),
            "contract_content_hash": str(contract.get("content_hash") or ""),
            "action": "INVALIDATE_EXECUTION",
            "reason_code": "FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE",
            "provider_compatibility_error": compatibility_error,
            "safety_evidence": {
                "trial_records": 0,
                "performance_accessed": False,
                **budget_evidence,
            },
            "created_at": now_timestamp(),
            "previous_event_hash": str(existing[-1].get("event_hash") or "") if existing else "",
        }
        event_payload = jsonable(event_payload)
        event = {**event_payload, "event_hash": stable_hash(event_payload)}
        correction_path.parent.mkdir(parents=True, exist_ok=True)
        with correction_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event
