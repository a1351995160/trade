"""Durable, identity-based run-level budget accounting for autonomous research."""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path
import json
import hashlib
import math
import os
from typing import Any, Iterable, Mapping

from .budget import BudgetExhaustedError, BudgetLedgerMismatchError
from .common import now_timestamp, stable_hash


@dataclass(frozen=True)
class RunBudgetContractV1:
    """Immutable limits pinned for one autonomous run."""

    run_id: str
    objective_id: str
    max_batches: int
    max_total_predictive_trials: int
    max_trials_per_batch: int
    max_hypotheses_per_batch: int
    max_candidates_per_batch: int
    policy_hash: str = ""
    objective_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @property
    def contract_hash(self) -> str:
        return stable_hash(self.to_dict())


@dataclass(frozen=True)
class RunBudgetUsageStateV1:
    """Materialized usage that can be reconstructed from durable identities."""

    reserved_trial_ids: tuple[str, ...] = ()
    performance_accessed_trial_ids: tuple[str, ...] = ()
    completed_trial_ids: tuple[str, ...] = ()
    remaining_capacity: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "reserved_trial_ids": list(self.reserved_trial_ids),
            "performance_accessed_trial_ids": list(self.performance_accessed_trial_ids),
            "completed_trial_ids": list(self.completed_trial_ids),
            "remaining_capacity": self.remaining_capacity,
        }


RunBudgetContract = RunBudgetContractV1
RunBudgetUsageState = RunBudgetUsageStateV1


def trial_budget_identity(*, run_id: str, batch_id: str, trial_id: str, candidate_id: str, candidate_hash: str) -> str:
    """Return the deterministic, single-consumption identity for one trial."""
    payload = {
        "run_id": str(run_id),
        "batch_id": str(batch_id),
        "trial_id": str(trial_id),
        "candidate_id": str(candidate_id),
        "candidate_hash": str(candidate_hash),
    }
    digest = stable_hash(payload)[:24]
    return f"RUN-TRIAL-{digest}"


class AutonomousRunBudgetV1:
    """A restart-safe budget whose authoritative usage is a set of trial identities."""

    schema_version = "autonomous-run-budget-v2"

    def __init__(self, *, run_id: str, objective_id: str, path: str | Path | None = None, max_batches: int, max_total_predictive_trials: int, max_trials_per_batch: int, max_hypotheses_per_batch: int, max_candidates_per_batch: int, policy_hash: str = "", objective_hash: str = "", readonly: bool = False):
        self.readonly = readonly
        self.run_id = str(run_id)
        self.objective_id = str(objective_id)
        self.path = Path(path) if path else None
        self.contract = RunBudgetContractV1(
            run_id=self.run_id,
            objective_id=self.objective_id,
            max_batches=int(max_batches),
            max_total_predictive_trials=int(max_total_predictive_trials),
            max_trials_per_batch=int(max_trials_per_batch),
            max_hypotheses_per_batch=int(max_hypotheses_per_batch),
            max_candidates_per_batch=int(max_candidates_per_batch),
            policy_hash=str(policy_hash),
            objective_hash=str(objective_hash),
        )
        for name in ("max_batches", "max_total_predictive_trials", "max_trials_per_batch", "max_hypotheses_per_batch", "max_candidates_per_batch"):
            if getattr(self.contract, name) < 1:
                raise ValueError(f"{name} must be positive")
        self.max_batches = self.contract.max_batches
        self.max_total_predictive_trials = self.contract.max_total_predictive_trials
        self.max_trials_per_batch = self.contract.max_trials_per_batch
        self.max_hypotheses_per_batch = self.contract.max_hypotheses_per_batch
        self.max_candidates_per_batch = self.contract.max_candidates_per_batch
        self.used_predictive_trials = 0
        self.reserved_predictive_trials = 0
        self.completed_predictive_trials = 0
        self.batch_count_used = 0
        self._reservations: dict[str, dict[str, Any]] = {}
        self._completed_trial_ids: set[str] = set()
        self._used_trial_ids: set[str] = set()
        self._trial_identities: dict[str, dict[str, Any]] = {}
        self._started_batch_ids: set[str] = set()
        self._completed_batch_ids: set[str] = set()
        self._legacy_used_count = 0
        self._events: list[dict[str, Any]] = []
        self._event_ids: set[str] = set()
        self._load()
        self._load_events()
        self._replay_events_if_state_is_empty()
        self._sync_materialized_counts()
        self._persist()

    @property
    def remaining_predictive_trials(self) -> int:
        return max(0, self.max_total_predictive_trials - self.used_predictive_trials - self.reserved_predictive_trials)

    @property
    def budget_hash(self) -> str:
        return stable_hash(self.to_dict())

    @property
    def usage_state(self) -> RunBudgetUsageStateV1:
        return RunBudgetUsageStateV1(
            reserved_trial_ids=tuple(sorted(self._reservations)),
            performance_accessed_trial_ids=tuple(sorted(self._used_trial_ids)),
            completed_trial_ids=tuple(sorted(self._completed_trial_ids)),
            remaining_capacity=self.remaining_predictive_trials,
        )

    def start_batch(self, batch_id: str) -> bool:
        batch_id = str(batch_id)
        if batch_id in self._started_batch_ids:
            return False
        if self.batch_count_used >= self.max_batches:
            raise BudgetExhaustedError("MAX_BATCHES_REACHED")
        self._started_batch_ids.add(batch_id)
        self.batch_count_used += 1
        self._append_event("RUN_BATCH_STARTED", {"batch_id": batch_id})
        self._persist()
        return True

    def complete_batch(self, batch_id: str) -> None:
        batch_id = str(batch_id)
        if batch_id not in self._started_batch_ids:
            raise BudgetLedgerMismatchError(f"batch was not started: {batch_id}")
        self._completed_batch_ids.add(batch_id)
        self._append_event("RUN_BATCH_COMPLETED", {"batch_id": batch_id})
        self._persist()

    def reserve_trial(self, *, trial_id: str, batch_id: str, candidate_id: str, family_id: str, candidate_hash: str = "") -> str:
        trial_id = str(trial_id)
        batch_id = str(batch_id)
        candidate_id = str(candidate_id)
        candidate_hash = str(candidate_hash or candidate_id)
        identity = trial_budget_identity(run_id=self.run_id, batch_id=batch_id, trial_id=trial_id, candidate_id=candidate_id, candidate_hash=candidate_hash)
        payload = {"batch_id": batch_id, "candidate_id": candidate_id, "candidate_hash": candidate_hash, "family_id": str(family_id)}
        self._assert_trial_identity(trial_id, identity, payload)
        if trial_id in self._used_trial_ids or trial_id in self._completed_trial_ids:
            return identity
        existing = self._reservations.get(trial_id)
        if existing is not None:
            return str(existing["reservation_id"])
        batch_reserved = sum(1 for item in self._reservations.values() if item["batch_id"] == batch_id)
        if batch_reserved >= self.max_trials_per_batch:
            raise BudgetExhaustedError("MAX_TRIALS_PER_BATCH")
        if self.remaining_predictive_trials < 1:
            raise BudgetExhaustedError("TOTAL_TRIAL_BUDGET_EXHAUSTED")
        reservation = {
            "reservation_id": identity,
            "trial_id": trial_id,
            "batch_id": batch_id,
            "candidate_id": candidate_id,
            "candidate_hash": candidate_hash,
            "family_id": str(family_id),
            "reserved_at": now_timestamp(),
        }
        self._reservations[trial_id] = reservation
        self._trial_identities[trial_id] = dict(reservation)
        self._append_event("RUN_TRIAL_RESERVED", reservation)
        self._sync_materialized_counts()
        self._persist()
        return identity

    def complete_trial(self, trial_id: str) -> None:
        trial_id = str(trial_id)
        if trial_id in self._completed_trial_ids:
            return
        if trial_id in self._used_trial_ids:
            self._completed_trial_ids.add(trial_id)
            self._append_event("RUN_TRIAL_COMPLETED", self._trial_identities.get(trial_id, {"trial_id": trial_id}))
            self._sync_materialized_counts()
            self._persist()
            return
        reservation = self._reservations.pop(trial_id, None)
        if reservation is None:
            raise BudgetLedgerMismatchError(f"trial reservation missing: {trial_id}")
        self._used_trial_ids.add(trial_id)
        self._completed_trial_ids.add(trial_id)
        self._append_event("RUN_TRIAL_PERFORMANCE_ACCESSED", reservation)
        self._append_event("RUN_TRIAL_COMPLETED", reservation)
        self._sync_materialized_counts()
        self._persist()

    def mark_performance_started(self, trial_id: str) -> None:
        trial_id = str(trial_id)
        reservation = self._reservations.get(trial_id)
        if reservation is None and trial_id not in self._used_trial_ids:
            raise BudgetLedgerMismatchError(f"trial was not reserved: {trial_id}")
        if trial_id not in self._used_trial_ids:
            self._used_trial_ids.add(trial_id)
            self._reservations.pop(trial_id, None)
            self._append_event("RUN_TRIAL_PERFORMANCE_ACCESSED", reservation or self._trial_identities[trial_id])
            self._sync_materialized_counts()
            self._persist()

    def release_trial(self, trial_id: str) -> None:
        trial_id = str(trial_id)
        if trial_id in self._used_trial_ids or trial_id in self._completed_trial_ids:
            return
        reservation = self._reservations.pop(trial_id, None)
        if reservation is None:
            raise BudgetLedgerMismatchError(f"trial reservation missing: {trial_id}")
        self._append_event("RUN_TRIAL_RELEASED", reservation)
        self._sync_materialized_counts()
        self._persist()

    def recover_from_ledger_view(self, ledger_view: Any) -> bool:
        before = self.used_predictive_trials, self.reserved_predictive_trials, self.completed_predictive_trials
        self.reconcile_against(ledger_view=ledger_view)
        return before != (self.used_predictive_trials, self.reserved_predictive_trials, self.completed_predictive_trials)

    def reconcile_against(self, *, trial_records: Iterable[Mapping[str, Any]] = (), ledger_view: Any | None = None, batch_checkpoint: Mapping[str, Any] | None = None, run_checkpoint: Mapping[str, Any] | None = None) -> None:
        """Reconcile durable evidence by identity and fail closed on illegal drift."""
        raw_records = list(getattr(ledger_view, "records", ()) if ledger_view is not None else trial_records)
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_records:
            item = dict(raw)
            trial_id = str(item.get("trial_id") or "")
            if not trial_id or trial_id in seen:
                raise BudgetLedgerMismatchError("duplicate trial identities in ledger")
            seen.add(trial_id)
            prior = self._trial_identities.get(trial_id, {})
            for key in ("batch_id", "candidate_id", "candidate_hash", "family_id", "budget_reservation_identity"):
                if not item.get(key) and prior.get(key):
                    item[key] = prior[key]
            if item.get("run_id") and str(item["run_id"]) != self.run_id:
                raise BudgetLedgerMismatchError("ledger trial belongs to another run")
            if item.get("objective_id") and str(item["objective_id"]) != self.objective_id:
                raise BudgetLedgerMismatchError("ledger trial objective mismatch")
            if self.contract.policy_hash and item.get("validation_policy_hash") and str(item["validation_policy_hash"]) != self.contract.policy_hash:
                raise BudgetLedgerMismatchError("policy hash mismatch")
            if not item.get("candidate_hash") and item.get("candidate_id"):
                item["candidate_hash"] = str(item["candidate_id"])
            batch_id = str(item.get("batch_id") or "")
            candidate_id = str(item.get("candidate_id") or "")
            candidate_hash = str(item.get("candidate_hash") or "")
            identity = trial_budget_identity(run_id=self.run_id, batch_id=batch_id, trial_id=trial_id, candidate_id=candidate_id, candidate_hash=candidate_hash)
            prior_identity = str(prior.get("reservation_id") or "")
            if prior_identity and prior_identity != identity:
                raise BudgetLedgerMismatchError("conflicting reservation identity")
            item["run_budget_identity"] = identity
            legally_reserved = bool(item.get("budget_reserved") or item.get("budget_reservation_identity") or item.get("reservation_id") or prior_identity)
            if (item.get("performance_accessed") or item.get("performance_complete") or item.get("final_adjudicated")) and not legally_reserved:
                raise BudgetLedgerMismatchError("performance-accessed trial was not legally reserved")
            item["budget_reserved"] = legally_reserved
            item["performance_complete"] = bool(item.get("performance_complete") or item.get("performance_completed") or item.get("final_adjudicated") or item.get("status") == "COMPLETED")
            item["final_adjudicated"] = bool(item.get("final_adjudicated") or item.get("status") == "COMPLETED")
            records.append(item)

        ledger_by_id = {str(item["trial_id"]): item for item in records}
        accessed = {trial_id for trial_id, item in ledger_by_id.items() if item.get("performance_accessed")}
        completed = {trial_id for trial_id, item in ledger_by_id.items() if item.get("performance_complete") or item.get("final_adjudicated")}
        active = {trial_id for trial_id, item in ledger_by_id.items() if item.get("budget_reserved") and trial_id not in completed and trial_id not in accessed}
        if len(accessed) > self.max_total_predictive_trials or len(accessed) + len(active) > self.max_total_predictive_trials:
            raise BudgetLedgerMismatchError("usage exceeds frozen run limit")
        batch_counts: dict[str, int] = {}
        for item in records:
            batch_id = str(item.get("batch_id") or "")
            if batch_id:
                batch_counts[batch_id] = batch_counts.get(batch_id, 0) + 1
        if any(count > self.max_trials_per_batch for count in batch_counts.values()):
            raise BudgetLedgerMismatchError("batch trial usage exceeds frozen run limit")

        stored_used = set(self._used_trial_ids)
        stored_completed = set(self._completed_trial_ids)
        stored_reserved = set(self._reservations)
        if stored_used - accessed:
            raise BudgetLedgerMismatchError("outer budget contains trial identity not present in durable ledger")
        if stored_completed - completed:
            raise BudgetLedgerMismatchError("outer completed trial identity not present in durable ledger")
        if stored_reserved - (active | accessed):
            raise BudgetLedgerMismatchError("outer reservation identity not present in durable ledger")
        if self._legacy_used_count and self._legacy_used_count != len(accessed):
            raise BudgetLedgerMismatchError("legacy aggregate budget cannot be reconciled by identity")

        previous_used = len(self._used_trial_ids)
        changed = False
        for trial_id, item in ledger_by_id.items():
            identity = str(item["run_budget_identity"])
            metadata = {
                "reservation_id": identity,
                "trial_id": trial_id,
                "batch_id": str(item.get("batch_id") or ""),
                "candidate_id": str(item.get("candidate_id") or ""),
                "candidate_hash": str(item.get("candidate_hash") or ""),
                "family_id": str(item.get("family_id") or ""),
                "reserved_at": str(item.get("reserved_at") or now_timestamp()),
            }
            self._assert_trial_identity(trial_id, identity, metadata)
            self._trial_identities[trial_id] = metadata
            if trial_id in accessed:
                if trial_id not in self._used_trial_ids:
                    self._used_trial_ids.add(trial_id)
                    changed = True
                    self._append_event("RUN_TRIAL_PERFORMANCE_ACCESSED", metadata)
                self._reservations.pop(trial_id, None)
            elif trial_id in active and trial_id not in self._reservations:
                self._reservations[trial_id] = metadata
                changed = True
                self._append_event("RUN_TRIAL_RESERVED", metadata)
            if trial_id in completed and trial_id not in self._completed_trial_ids:
                self._completed_trial_ids.add(trial_id)
                changed = True
                self._append_event("RUN_TRIAL_COMPLETED", metadata)

        ledger_batches = {str(item.get("batch_id")) for item in records if item.get("batch_id")}
        if not ledger_batches.issubset(self._started_batch_ids):
            new_batches = ledger_batches - self._started_batch_ids
            if len(self._started_batch_ids) + len(new_batches) > self.max_batches:
                raise BudgetLedgerMismatchError("batch usage exceeds frozen run limit")
            self._started_batch_ids.update(new_batches)
            self.batch_count_used = len(self._started_batch_ids)
            changed = True
        self._completed_batch_ids.update(str(item.get("batch_id")) for item in records if item.get("batch_id") and str(item["trial_id"]) in completed)
        self._sync_materialized_counts()

        if changed:
            recovery_batch = str((batch_checkpoint or {}).get("batch_id") or ",".join(sorted(ledger_batches)))
            recovery_payload = {
                "run_id": self.run_id,
                "batch_id": recovery_batch,
                "recovered_trial_ids": sorted(accessed - stored_used),
                "previous_materialized_used": previous_used,
                "new_materialized_used": self.used_predictive_trials,
                "ledger_view_hash": str(getattr(ledger_view, "view_hash", stable_hash(records))),
                "run_budget_contract_hash": self.contract.contract_hash,
                "reason": "PARTIAL_BATCH_CRASH_WINDOW",
                "timestamp": now_timestamp(),
            }
            self._append_event("RUN_BUDGET_RECOVERY_APPLIED", recovery_payload)
            self._persist()

        if run_checkpoint:
            checks = {
                "total_trials_reserved": self.reserved_predictive_trials,
                "total_trials_started": self.used_predictive_trials + self.reserved_predictive_trials,
                "total_trials_completed": self.completed_predictive_trials,
            }
            for key, actual in checks.items():
                if key in run_checkpoint and int(run_checkpoint[key]) != int(actual):
                    raise BudgetLedgerMismatchError(f"run checkpoint mismatch: {key}")
        if batch_checkpoint and "completed_trial_ids" in batch_checkpoint:
            checkpoint_ids = {str(item) for item in batch_checkpoint.get("completed_trial_ids", ())}
            batch_id = batch_checkpoint.get("batch_id")
            expected = {trial_id for trial_id in completed if not batch_id or str(ledger_by_id[trial_id].get("batch_id")) == str(batch_id)}
            if checkpoint_ids != (expected if batch_id else completed):
                raise BudgetLedgerMismatchError("batch checkpoint completed_trial_ids mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "objective_id": self.objective_id,
            "contract": self.contract.to_dict(),
            "contract_hash": self.contract.contract_hash,
            "max_batches": self.max_batches,
            "max_total_predictive_trials": self.max_total_predictive_trials,
            "max_trials_per_batch": self.max_trials_per_batch,
            "max_hypotheses_per_batch": self.max_hypotheses_per_batch,
            "max_candidates_per_batch": self.max_candidates_per_batch,
            "used_predictive_trials": self.used_predictive_trials,
            "reserved_predictive_trials": self.reserved_predictive_trials,
            "completed_predictive_trials": self.completed_predictive_trials,
            "remaining_predictive_trials": self.remaining_predictive_trials,
            "batch_count_used": self.batch_count_used,
            "started_batch_ids": sorted(self._started_batch_ids),
            "completed_batch_ids": sorted(self._completed_batch_ids),
            "active_reservations": {key: dict(value) for key, value in sorted(self._reservations.items())},
            "used_trial_ids": sorted(self._used_trial_ids),
            "completed_trial_ids": sorted(self._completed_trial_ids),
            "trial_identities": {key: dict(value) for key, value in sorted(self._trial_identities.items())},
            "usage_state": self.usage_state.to_dict(),
            "updated_at": now_timestamp(),
        }

    def _assert_trial_identity(self, trial_id: str, identity: str, payload: Mapping[str, Any]) -> None:
        prior = self._trial_identities.get(str(trial_id))
        if prior is not None:
            if str(prior.get("reservation_id")) != identity:
                raise BudgetLedgerMismatchError("same trial identity has conflicting payload")
            for key in ("batch_id", "candidate_id", "candidate_hash", "family_id"):
                if prior.get(key) and payload.get(key) and str(prior[key]) != str(payload[key]):
                    raise BudgetLedgerMismatchError("same trial identity has conflicting payload")

    def _sync_materialized_counts(self) -> None:
        self.used_predictive_trials = len(self._used_trial_ids) if self._used_trial_ids or not self._legacy_used_count else self._legacy_used_count
        self.reserved_predictive_trials = len(self._reservations)
        self.completed_predictive_trials = len(self._completed_trial_ids)
        self.batch_count_used = len(self._started_batch_ids)

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BudgetLedgerMismatchError("cannot load autonomous run budget") from exc
        if payload.get("schema_version") == "autonomous-run-budget-v3" and self.schema_version != "autonomous-run-budget-v3":
            raise BudgetLedgerMismatchError("CAMPAIGN_REQUIRES_RESOURCE_AWARE_ENTRY")
        persisted_contract = payload.get("contract") if isinstance(payload.get("contract"), Mapping) else payload
        for key in ("run_id", "objective_id", "max_batches", "max_total_predictive_trials", "max_trials_per_batch", "max_hypotheses_per_batch", "max_candidates_per_batch"):
            if key in persisted_contract and str(persisted_contract[key]) != str(getattr(self.contract, key)):
                raise BudgetLedgerMismatchError(f"run budget pin mismatch: {key}")
        if self.contract.policy_hash and persisted_contract.get("policy_hash") and str(persisted_contract["policy_hash"]) != self.contract.policy_hash:
            raise BudgetLedgerMismatchError("run budget pin mismatch: policy_hash")
        if self.contract.objective_hash and persisted_contract.get("objective_hash") and str(persisted_contract["objective_hash"]) != self.contract.objective_hash:
            raise BudgetLedgerMismatchError("run budget pin mismatch: objective_hash")
        self.used_predictive_trials = int(payload.get("used_predictive_trials", 0))
        self.reserved_predictive_trials = int(payload.get("reserved_predictive_trials", 0))
        self.completed_predictive_trials = int(payload.get("completed_predictive_trials", 0))
        self.batch_count_used = int(payload.get("batch_count_used", 0))
        self._started_batch_ids = {str(item) for item in payload.get("started_batch_ids", ())}
        self._completed_batch_ids = {str(item) for item in payload.get("completed_batch_ids", ())}
        self._used_trial_ids = {str(item) for item in payload.get("used_trial_ids", ())}
        self._completed_trial_ids = {str(item) for item in payload.get("completed_trial_ids", ())}
        self._reservations = {str(key): dict(value) for key, value in payload.get("active_reservations", {}).items()}
        self._trial_identities = {str(key): dict(value) for key, value in payload.get("trial_identities", {}).items()}
        if not self._used_trial_ids and self.used_predictive_trials:
            self._legacy_used_count = self.used_predictive_trials
        elif self.used_predictive_trials != len(self._used_trial_ids):
            raise BudgetLedgerMismatchError("persisted run budget aggregate mismatch")
        if self.reserved_predictive_trials != len(self._reservations) or self.completed_predictive_trials != len(self._completed_trial_ids):
            raise BudgetLedgerMismatchError("persisted run budget aggregate mismatch")
        if min(self.used_predictive_trials, self.reserved_predictive_trials, self.completed_predictive_trials, self.batch_count_used) < 0 or self.used_predictive_trials + self.reserved_predictive_trials > self.max_total_predictive_trials:
            raise BudgetLedgerMismatchError("invalid persisted run budget usage")

    @property
    def _events_path(self) -> Path | None:
        return self.path.with_name("run_budget_events.jsonl") if self.path is not None else None

    def _load_events(self) -> None:
        path = self._events_path
        if path is None or not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if str(event.get("run_id")) != self.run_id:
                raise BudgetLedgerMismatchError("run budget event run_id mismatch")
            event_id = str(event.get("event_id") or "")
            if event_id and event_id not in self._event_ids:
                self._event_ids.add(event_id)
                self._events.append(event)

    def _replay_events_if_state_is_empty(self) -> None:
        if self._used_trial_ids or self._reservations or self._completed_trial_ids or self._started_batch_ids or self._legacy_used_count:
            return
        for event in self._events:
            event_type = str(event.get("event_type") or "")
            payload = dict(event.get("payload") or {})
            trial_id = str(payload.get("trial_id") or "")
            if trial_id:
                identity = str(payload.get("reservation_id") or payload.get("run_budget_identity") or "")
                if identity:
                    self._trial_identities[trial_id] = payload
                if event_type == "RUN_TRIAL_RESERVED":
                    self._reservations[trial_id] = payload
                elif event_type == "RUN_TRIAL_PERFORMANCE_ACCESSED":
                    self._used_trial_ids.add(trial_id)
                    self._reservations.pop(trial_id, None)
                elif event_type == "RUN_TRIAL_COMPLETED":
                    self._used_trial_ids.add(trial_id)
                    self._completed_trial_ids.add(trial_id)
                    self._reservations.pop(trial_id, None)
            if event_type == "RUN_BATCH_STARTED" and payload.get("batch_id"):
                self._started_batch_ids.add(str(payload["batch_id"]))
            if event_type == "RUN_BATCH_COMPLETED" and payload.get("batch_id"):
                self._completed_batch_ids.add(str(payload["batch_id"]))

    def _append_event(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if self.readonly:
            raise PermissionError('RUN_BUDGET_READ_ONLY')
        event_payload = dict(payload)
        event_id = stable_hash({"run_id": self.run_id, "event_type": event_type, "payload": event_payload})
        if event_id in self._event_ids:
            return
        event = {"schema_version": "autonomous-run-budget-event-v1", "event_id": event_id, "event_type": event_type, "run_id": self.run_id, "payload": event_payload, "timestamp": now_timestamp()}
        self._event_ids.add(event_id)
        self._events.append(event)
        path = self._events_path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in self._events), encoding="utf-8")
            os.replace(temporary, path)

    def _persist(self) -> None:
        if self.path is None or self.readonly:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)


class AutonomousRunBudgetV2(AutonomousRunBudgetV1):
    """同一权威事件流上的多资源版本；旧缺失维度不能被推断成零。"""

    schema_version = "autonomous-run-budget-v3"
    resource_names = ("candidate_attempts", "data_experiments", "account_jobs", "model_calls",
                      "model_tokens", "model_cost_microunits", "verification_jobs", "wall_seconds")

    def _load(self):
        if self.path is not None and self.path.exists():
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            if stored.get("schema_version") != self.schema_version:
                raise BudgetLedgerMismatchError("LEGACY_RESOURCE_USAGE_UNKNOWN")
        # 增量可令累计使用超过最初合同；先校验快照身份，随后由权威事件重建有效上限。
        original = self.max_total_predictive_trials
        try:
            self.max_total_predictive_trials = 2**63 - 1
            super()._load()
        finally:
            self.max_total_predictive_trials = original

    def to_dict(self):
        result = super().to_dict()
        result['campaign_event_head'] = self._events[-1]['event_id'] if self._events else None
        return result

    def _load_events(self):
        super()._load_events()
        if self.path is not None and self.path.exists():
            snapshot = json.loads(self.path.read_text(encoding="utf-8"))
            head = snapshot.get('campaign_event_head')
            if head and head not in self._event_ids:
                raise BudgetLedgerMismatchError('CAMPAIGN_EVENT_HISTORY_TRUNCATED')
        for event in self._events:
            expected = stable_hash({"run_id": self.run_id, "event_type": event["event_type"], "payload": event["payload"]})
            if event.get("event_id") != expected:
                raise BudgetLedgerMismatchError("CAMPAIGN_EVENT_HASH_CONFLICT")
        if self.path is not None and self.path.exists() and not self._events:
            raise BudgetLedgerMismatchError("CAMPAIGN_EVENT_HISTORY_MISSING")
        view = self.campaign_view()
        if view['authorization'] is not None:
            self.max_batches = view['authorization']['max_batches']
            self.max_total_predictive_trials = view['authorization']['max_total_predictive_trials']

    def _replay_events_if_state_is_empty(self):
        # V2 的事件是权威；快照可落后于最后一次持久化事件。
        self._reservations, self._trial_identities = {}, {}
        self._completed_trial_ids, self._used_trial_ids = set(), set()
        self._started_batch_ids, self._completed_batch_ids = set(), set()
        self._legacy_used_count = 0
        super()._replay_events_if_state_is_empty()
        for event in self._events:
            if event['event_type'] == 'RUN_TRIAL_RELEASED':
                trial_id = event['payload']['trial_id']
                if trial_id not in self._used_trial_ids:
                    self._reservations.pop(trial_id, None)
        if len(self._used_trial_ids) + len(self._reservations) > self.max_total_predictive_trials:
            raise BudgetLedgerMismatchError('CAMPAIGN_TRIAL_USAGE_EXCEEDS_EFFECTIVE_GRANT')

    def campaign_view(self):
        authorization, base_authorization, operations, stages = None, None, {}, {}
        paused, revoked, grants = False, False, {}
        for event in self._events:
            kind, item = event['event_type'], event['payload']
            if kind == 'CAMPAIGN_AUTHORIZED':
                if authorization is not None:
                    raise BudgetLedgerMismatchError('CAMPAIGN_AUTHORIZATION_DUPLICATED')
                authorization = deepcopy(item)
                base_authorization = deepcopy(item)
                if authorization.get('scope_policy'):
                    from .campaign_scope_v1 import validate_scope_policy
                    validate_scope_policy(authorization['root'], authorization)
            elif kind == 'CAMPAIGN_GRANT_ADDED':
                if authorization is None or not authorization.get('scope_policy') or revoked:
                    raise BudgetLedgerMismatchError('CAMPAIGN_GRANT_EVENT_ORDER')
                grant_id = item['grant']['grant_id']
                if grant_id in grants:
                    raise BudgetLedgerMismatchError('CAMPAIGN_GRANT_DUPLICATED')
                from .campaign_scope_v1 import replay_grant
                authorization = replay_grant(base_authorization, authorization, item)
                grants[grant_id] = deepcopy(item)
            elif kind == 'CAMPAIGN_REVOKED':
                revoked = True
            elif kind == 'CAMPAIGN_PAUSED':
                paused = True
            elif kind == 'CAMPAIGN_RESUMED':
                paused = False
            elif kind == 'CAMPAIGN_STAGE_CHANGED':
                stages[item['stage']] = dict(item)
            elif kind == 'CAMPAIGN_OPERATION_RESERVED':
                if item['operation_id'] in operations:
                    raise BudgetLedgerMismatchError('CAMPAIGN_OPERATION_DUPLICATED')
                operations[item['operation_id']] = {**item, 'status': 'RESERVED'}
            elif kind in ('CAMPAIGN_OPERATION_STARTED', 'CAMPAIGN_OPERATION_UNKNOWN', 'CAMPAIGN_OPERATION_SETTLED'):
                operation = operations.get(item['operation_id'])
                if operation is None or operation['status'] in ('COMPLETED', 'FAILED'):
                    raise BudgetLedgerMismatchError('CAMPAIGN_OPERATION_EVENT_ORDER')
                operation.update(item)
                operation['status'] = {'CAMPAIGN_OPERATION_STARTED': 'RUNNING', 'CAMPAIGN_OPERATION_UNKNOWN': 'UNKNOWN',
                                       'CAMPAIGN_OPERATION_SETTLED': item.get('outcome')}[kind]
            elif kind == 'CAMPAIGN_MODEL_RESOURCE_OVERRUN_SETTLED':
                operation = operations.get(item['operation_id'])
                actual = item.get('actual', {})
                receipt_path = Path(item.get('usage_receipt', '')).absolute()
                if (operation is None or operation['kind'] != 'MODEL' or operation['status'] not in ('RUNNING', 'UNKNOWN')
                        or not receipt_path.is_relative_to(Path(authorization['root']) / 'reports' / 'research_campaigns' / stable_hash(authorization['authorization_id']) / 'diagnosis_v4')
                        or receipt_path.resolve() != receipt_path or not receipt_path.is_file()
                        or receipt_path.name != 'INVOCATION.json'
                        or hashlib.sha256(receipt_path.read_bytes()).hexdigest() != item.get('evidence_identity')):
                    raise BudgetLedgerMismatchError('CAMPAIGN_MODEL_OVERRUN_PROOF_INVALID')
                usage = json.loads(receipt_path.read_text(encoding='utf-8'))['usage']
                from .campaign_model_receipt_v1 import model_receipt_proof
                try:
                    model_receipt_proof(receipt_path.parent.parent.parent.parent, operation, receipt_path, operations)
                except (PermissionError, ValueError, KeyError) as exc:
                    raise BudgetLedgerMismatchError('CAMPAIGN_MODEL_OVERRUN_PROOF_INVALID') from exc
                expected = dict.fromkeys(self.resource_names, 0)
                expected.update(model_calls=max(1, usage['model_calls']), model_tokens=usage['total_tokens'],
                    model_cost_microunits=usage['cost_microunits'], wall_seconds=operation['upper_bounds']['wall_seconds'])
                if (actual != expected or item.get('outcome') != 'FAILED'
                        or item.get('resource_overrun') != {name: max(0, actual[name]-operation['upper_bounds'][name]) for name in actual}
                        or not any(item['resource_overrun'].values())):
                    raise BudgetLedgerMismatchError('CAMPAIGN_MODEL_OVERRUN_PROOF_INVALID')
                operation.update(item)
                operation['status'] = 'FAILED'
            elif kind=='CAMPAIGN_OPERATION_RESOURCE_OVERRUN_SETTLED':
                operation=operations.get(item['operation_id']);segments=operation.get('segments',[]) if operation else []
                actual=item.get('actual',{})
                if (operation is None or operation['status'] not in ('RUNNING','UNKNOWN')
                        or operation.get('execution_profile') is None or operation.get('active_segment') is not None
                        or not segments or segments[-1].get('outcome')!='FAILED'
                        or segments[-1].get('basis')!='MEASURED_FAILED_RESOURCE_BOUND_VIOLATION'
                        or item.get('outcome')!='FAILED' or not item.get('evidence_identity')
                        or set(actual)!=set(self.resource_names)
                        or any(type(value) is not int or value<0 for value in actual.values())
                        or actual['wall_seconds']!=max(1,math.ceil(operation['active_wall_seconds']))
                        or any(actual[name]!=operation['upper_bounds'][name] for name in actual if name!='wall_seconds')
                        or item.get('resource_overrun')!={name:max(0,actual[name]-operation['upper_bounds'][name]) for name in actual}
                        or not any(item['resource_overrun'].values())):
                    raise BudgetLedgerMismatchError('CAMPAIGN_RESOURCE_OVERRUN_PROOF_INVALID')
                operation.update(item);operation['status']='FAILED'
            elif kind == 'CAMPAIGN_EXECUTION_SEGMENT_DISPATCHED':
                operation=operations.get(item['operation_id'])
                if (operation is None or operation['status'] != 'RUNNING'
                        or operation.get('execution_profile') is None or operation.get('active_segment') is not None):
                    raise BudgetLedgerMismatchError('CAMPAIGN_EXECUTION_SEGMENT_EVENT_ORDER')
                from .universe_execution_profile_v1 import validate_execution_profile
                profile=validate_execution_profile(operation['execution_profile'])
                segments=operation.setdefault('segments',[])
                charged=operation.get('active_wall_seconds',0)
                upper=item.get('upper_bound_seconds')
                if (type(item.get('segment_number')) is not int or item['segment_number'] != len(segments)+1
                        or item.get('profile_hash') != profile['profile_hash']
                        or type(upper) not in (int,float) or not 0 < upper <= profile['worker_seconds']
                        or upper > profile['total_seconds']-charged
                        or (segments and segments[-1].get('outcome') in ('COMPLETED','FAILED'))):
                    raise BudgetLedgerMismatchError('CAMPAIGN_EXECUTION_SEGMENT_IDENTITY_CONFLICT')
                segment=dict(item); segments.append(segment); operation['active_segment']=segment
            elif kind == 'CAMPAIGN_EXECUTION_SEGMENT_CHARGED':
                operation=operations.get(item['operation_id'])
                active=operation.get('active_segment') if operation else None
                if (operation is None or operation['status'] not in ('RUNNING','UNKNOWN') or active is None
                        or active['segment_number'] != item.get('segment_number')):
                    raise BudgetLedgerMismatchError('CAMPAIGN_EXECUTION_SEGMENT_EVENT_ORDER')
                from .universe_execution_profile_v1 import segment_charge
                seconds,basis=segment_charge(item.get('measured_seconds'),active['upper_bound_seconds'],
                                             item.get('evidence_identity'),outcome=item.get('outcome'))
                if (item.get('seconds') != seconds or item.get('basis') != basis
                        or item.get('outcome') not in ('CONTINUE','PAUSED','COMPLETED','FAILED')):
                    raise BudgetLedgerMismatchError('CAMPAIGN_EXECUTION_SEGMENT_CHARGE_CONFLICT')
                active.update(item)
                operation['active_wall_seconds']=operation.get('active_wall_seconds',0)+seconds
                operation['active_segment']=None
        if authorization is None:
            return {'authorization': None, 'operations': {}, 'stages': {}, 'paused': False}
        used = {name: 0 for name in self.resource_names}
        reserved = dict(used)
        for operation in operations.values():
            settled = operation['status'] in ('COMPLETED', 'FAILED')
            destination = used if settled else reserved
            for name, amount in operation.get('actual' if settled else 'upper_bounds', {}).items():
                destination[name] += amount
        remaining = {name: authorization['resource_limits'][name] - used[name] - reserved[name] for name in used}
        violations=[op for op in operations.values() if op.get('resource_overrun')]
        if min(remaining.values()) < 0 and not violations:
            raise BudgetLedgerMismatchError('CAMPAIGN_RESOURCE_USAGE_EXCEEDS_LIMIT')
        result={'authorization': authorization, 'base_authorization': base_authorization,
                'grants': grants, 'revoked': revoked, 'operations': operations, 'stages': stages, 'paused': paused,
                'used': used, 'reserved': reserved, 'remaining': remaining}
        if authorization.get('scope_policy'):
            from .campaign_scope_v1 import stage_remaining
            result['stage_remaining'] = stage_remaining(result)
            if any(value < 0 for units in result['stage_remaining'].values() for value in units.values()) and not violations:
                raise BudgetLedgerMismatchError('CAMPAIGN_STAGE_USAGE_EXCEEDS_LIMIT')
        if violations:
            # 精确披露债务；冻结授权不变，后续派发一直暂停。
            result.update(paused=True,resource_overrun={name:max(0,-remaining[name]) for name in remaining},
                resource_violation=True)
        if any(operation.get('execution_profile') is not None for operation in operations.values()):
            # 计算进度包含在原用途整份预留中，不再次从 remaining 扣除。
            result['active_wall_seconds']=sum(operation.get('active_wall_seconds',0) for operation in operations.values()
                                              if operation['status'] not in ('COMPLETED','FAILED'))
        return result

    def campaign_event(self, kind, payload):
        if not kind.startswith('CAMPAIGN_'):
            raise ValueError('CAMPAIGN_EVENT_KIND_INVALID')
        self._append_event(kind, payload)
        self._persist()
