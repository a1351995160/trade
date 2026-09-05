"""Factory facade over TrialRegistryV1 and ExperimentLedger."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping
import json

from .common import now_timestamp, stable_hash


@dataclass(frozen=True)
class FactoryTrialRecordV1:
    trial_id: str
    objective_id: str
    batch_id: str
    family_id: str
    hypothesis_id: str
    candidate_id: str
    candidate_hash: str
    dataset_hash: str
    validation_policy_hash: str
    engine_hash: str
    seed: int
    performance_accessed: bool = False
    status: str = "REGISTERED"
    classification: str | None = None
    reason_codes: tuple[str, ...] = ()
    lineage: Mapping[str, Any] = None  # type: ignore[assignment]
    created_at: str = ""
    updated_at: str = ""
    budget_reservation_identity: str | None = None
    performance_complete: bool = False
    final_adjudicated: bool = False
    registry_committed: bool = False
    stage: str = ""
    started_at: str | None = None
    last_activity_at: str | None = None
    finished_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    recovery_status: str | None = None
    start_intent_id: str | None = None

    def __post_init__(self) -> None:
        if not self.trial_id or not self.objective_id or not self.batch_id or not self.candidate_id:
            raise ValueError("trial lineage identifiers are required")
        if self.performance_accessed and self.status == "REGISTERED":
            raise ValueError("a registered trial cannot already have performance access")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        object.__setattr__(self, "lineage", dict(self.lineage or {}))
        if not self.created_at:
            object.__setattr__(self, "created_at", now_timestamp())
        if not self.updated_at:
            object.__setattr__(self, "updated_at", self.created_at)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": "factory-trial-record-v1",
            "trial_id": self.trial_id,
            "objective_id": self.objective_id,
            "batch_id": self.batch_id,
            "family_id": self.family_id,
            "hypothesis_id": self.hypothesis_id,
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "dataset_hash": self.dataset_hash,
            "validation_policy_hash": self.validation_policy_hash,
            "engine_hash": self.engine_hash,
            "seed": self.seed,
            "performance_accessed": self.performance_accessed,
            "status": self.status,
            "classification": self.classification,
            "reason_codes": list(self.reason_codes),
            "lineage": dict(self.lineage),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "budget_reservation_identity": self.budget_reservation_identity,
            "performance_complete": self.performance_complete,
            "final_adjudicated": self.final_adjudicated,
            "registry_committed": self.registry_committed,
        }
        observability = {
            "stage": self.stage,
            "started_at": self.started_at,
            "last_activity_at": self.last_activity_at,
            "finished_at": self.finished_at,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "recovery_status": self.recovery_status,
            "start_intent_id": self.start_intent_id,
        }
        payload.update({key: value for key, value in observability.items() if value not in (None, "")})
        return payload


class ResearchFactoryTrialLedgerFacadeV1:
    """Keep factory metadata while delegating canonical trial persistence."""

    TERMINAL = {"COMPLETED", "BLOCKED", "INVALIDATED", "SUPERSEDED"}
    PERFORMANCE_COMPLETE_PENDING_ADJUDICATION = "PERFORMANCE_COMPLETE_PENDING_ADJUDICATION"

    def __init__(self, *, trial_registry: Any | None = None, experiment_ledger: Any | None = None, path: str | Path | None = None):
        self.trial_registry = trial_registry
        self.experiment_ledger = experiment_ledger
        self.path = Path(path) if path else None
        self._events: list[dict[str, Any]] = []
        if self.path and self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self._events = list(payload.get("events", []))

    def _latest(self, trial_id: str) -> FactoryTrialRecordV1 | None:
        for event in reversed(self._events):
            if event.get("trial_id") == trial_id:
                return FactoryTrialRecordV1(**{key: value for key, value in event.items() if key not in {"schema_version", "event_type", "event_hash"}})
        return None

    def _append(self, record: FactoryTrialRecordV1, *, event_type: str) -> None:
        payload = record.to_dict()
        payload["event_type"] = event_type
        payload["event_hash"] = stable_hash(payload)
        self._events.append(payload)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"schema_version": "factory-trial-ledger-v1", "events": self._events}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if self.trial_registry is not None:
            from chanlun_trader.research.strategy_validation import TrialEvent
            self.trial_registry.append(TrialEvent(
                event_type=event_type,
                trial_id=record.trial_id,
                candidate_id=record.candidate_id,
                candidate_preregistration_hash=record.candidate_hash,
                status=record.status,
                classification=record.classification,
                reason_codes=record.reason_codes,
                performance_accessed=record.performance_accessed,
            ))

    def register_before_performance(self, record: FactoryTrialRecordV1 | None = None, **kwargs: Any) -> FactoryTrialRecordV1:
        if record is not None:
            kwargs = record.to_dict()
            kwargs.pop("schema_version", None)
            kwargs.pop("performance_accessed", None)
            kwargs.pop("status", None)
        trial_id = str(kwargs["trial_id"])
        if self._latest(trial_id) is not None:
            raise ValueError(f"trial is already registered: {trial_id}")
        record = FactoryTrialRecordV1(**kwargs, performance_accessed=False, status="REGISTERED")
        self._append(record, event_type="REGISTERED_BEFORE_PERFORMANCE")
        return record

    def mark_started(self, trial_id: str, *, started_at: str | None = None, last_activity_at: str | None = None) -> FactoryTrialRecordV1:
        current = self._latest(trial_id)
        if current is None or current.status != "REGISTERED":
            raise ValueError("a trial can only start from the registered state")
        return self._transition(
            trial_id,
            status="REGISTERED",
            event_type="TRIAL_STARTED",
            stage="TRIAL_RUNNING",
            started_at=started_at or now_timestamp(),
            last_activity_at=last_activity_at or now_timestamp(),
            recovery_status="RUNNING",
        )

    def _transition(self, trial_id: str, *, status: str, event_type: str, performance_accessed: bool | None = None, performance_complete: bool | None = None, final_adjudicated: bool | None = None, registry_committed: bool | None = None, classification: str | None = None, reason_codes: tuple[str, ...] = (), lineage_patch: Mapping[str, Any] | None = None, budget_reservation_identity: str | None = None, stage: str | None = None, started_at: str | None = None, last_activity_at: str | None = None, finished_at: str | None = None, error_code: str | None = None, error_message: str | None = None, recovery_status: str | None = None) -> FactoryTrialRecordV1:
        current = self._latest(trial_id)
        if current is None:
            raise KeyError(f"unknown trial: {trial_id}")
        if current.status in self.TERMINAL and status != "SUPERSEDED":
            raise ValueError(f"terminal trial cannot transition: {trial_id}={current.status}")
        record = replace(
            current,
            status=status,
            performance_accessed=current.performance_accessed if performance_accessed is None else performance_accessed,
            performance_complete=current.performance_complete if performance_complete is None else performance_complete,
            final_adjudicated=current.final_adjudicated if final_adjudicated is None else final_adjudicated,
            registry_committed=current.registry_committed if registry_committed is None else registry_committed,
            classification=classification if classification is not None else current.classification,
            reason_codes=tuple(reason_codes) or current.reason_codes,
            lineage={**current.lineage, **dict(lineage_patch or {})},
            updated_at=now_timestamp(),
            budget_reservation_identity=budget_reservation_identity or current.budget_reservation_identity,
            stage=current.stage if stage is None else stage,
            started_at=current.started_at if started_at is None else started_at,
            last_activity_at=current.last_activity_at if last_activity_at is None else last_activity_at,
            finished_at=current.finished_at if finished_at is None else finished_at,
            error_code=current.error_code if error_code is None else error_code,
            error_message=current.error_message if error_message is None else error_message,
            recovery_status=current.recovery_status if recovery_status is None else recovery_status,
        )
        self._append(record, event_type=event_type)
        return record

    def mark_performance_accessed(self, trial_id: str) -> FactoryTrialRecordV1:
        current = self._latest(trial_id)
        if current is None or current.status != "REGISTERED":
            raise ValueError("performance can only be accessed by a registered trial")
        started_at = current.started_at or now_timestamp()
        return self._transition(trial_id, status="PERFORMANCE_ACCESSED", event_type="PERFORMANCE_ACCESSED", performance_accessed=True, stage="TRIAL_RUNNING", started_at=started_at, last_activity_at=now_timestamp(), recovery_status="RUNNING")

    def mark_provisional(self, trial_id: str, *, local_classification: str, evidence_ref: str, result: Mapping[str, Any] | None = None) -> FactoryTrialRecordV1:
        current = self._latest(trial_id)
        if current is None or not current.performance_accessed or current.status != "PERFORMANCE_ACCESSED":
            raise ValueError("provisional evidence requires a performance-accessed trial")
        return self._transition(
            trial_id,
            status=self.PERFORMANCE_COMPLETE_PENDING_ADJUDICATION,
            event_type="PROVISIONAL_VALIDATION_EVIDENCE_PERSISTED",
            performance_complete=True,
            lineage_patch={
                "local_classification": str(local_classification),
                "provisional_evidence_ref": str(evidence_ref),
                "provisional_result": dict(result or {}),
                "final_adjudication_pending": True,
            },
            stage="TRIAL_COMPLETING",
            last_activity_at=now_timestamp(),
            recovery_status="COMPLETING",
        )

    def mark_completed(self, trial_id: str, classification: str, *, reason_codes: tuple[str, ...] = (), result: Mapping[str, Any] | None = None) -> FactoryTrialRecordV1:
        current = self._latest(trial_id)
        if current is None or not current.performance_accessed or current.status not in {"PERFORMANCE_ACCESSED", self.PERFORMANCE_COMPLETE_PENDING_ADJUDICATION}:
            raise ValueError("completed trial must have performance access")
        event_type = "FINAL_RESEARCH_ADJUDICATION_COMMITTED" if current.status == self.PERFORMANCE_COMPLETE_PENDING_ADJUDICATION else "COMPLETED"
        record = self._transition(trial_id, status="COMPLETED", event_type=event_type, performance_complete=True, final_adjudicated=True, registry_committed=classification != "ENGINEERING_BLOCKED", classification=classification, reason_codes=reason_codes, lineage_patch={"final_adjudication_pending": False}, stage="TRIAL_COMPLETED", last_activity_at=now_timestamp(), finished_at=now_timestamp(), recovery_status="COMPLETE")
        self._record_experiment(record, classification, result)
        return record

    def mark_final_adjudication(self, trial_id: str, classification: str, *, decision_id: str, reason_codes: tuple[str, ...] = (), result: Mapping[str, Any] | None = None) -> FactoryTrialRecordV1:
        current = self._latest(trial_id)
        if current is None:
            raise KeyError(f"unknown trial: {trial_id}")
        commit_payload = {
            "trial_id": str(trial_id),
            "decision_id": str(decision_id),
            "classification": str(classification),
            "reason_codes": list(reason_codes),
            "result": dict(result or {}),
        }
        commit_hash = stable_hash(commit_payload)
        if current.status in self.TERMINAL:
            existing_decision_id = str(current.lineage.get("final_decision_id") or "")
            existing_hash = str(current.lineage.get("final_decision_commit_hash") or "")
            if existing_decision_id == str(decision_id) and existing_hash == commit_hash:
                return current
            raise ValueError("FINAL_DECISION_IDENTITY_CONFLICT")
        if current.status != self.PERFORMANCE_COMPLETE_PENDING_ADJUDICATION:
            raise ValueError("final adjudication requires provisional performance evidence")
        record = self._transition(
            trial_id,
            status="COMPLETED",
            event_type="FINAL_RESEARCH_ADJUDICATION_COMMITTED",
            performance_complete=True,
            final_adjudicated=True,
            registry_committed=False,
            classification=classification,
            reason_codes=reason_codes,
            lineage_patch={"final_adjudication_pending": False, "final_decision_id": str(decision_id), "final_decision_commit_hash": commit_hash},
        )
        self._record_experiment(record, classification, result)
        return record

    def mark_registry_committed(self, trial_id: str) -> FactoryTrialRecordV1:
        current = self._latest(trial_id)
        if current is None:
            raise KeyError(f"unknown trial: {trial_id}")
        if current.registry_committed:
            return current
        if current.status not in self.TERMINAL:
            raise ValueError("registry commit requires a terminal trial")
        record = replace(current, registry_committed=True, updated_at=now_timestamp())
        self._append(record, event_type="REGISTRY_TRANSITION_COMMITTED")
        return record

    def _record_experiment(self, record: FactoryTrialRecordV1, classification: str, result: Mapping[str, Any] | None = None) -> None:
        if self.experiment_ledger is not None and hasattr(self.experiment_ledger, "record"):
            from chanlun_trader.research.experiment import ExperimentRecord
            self.experiment_ledger.record(ExperimentRecord(
                experiment_id=record.trial_id,
                hypothesis_id=record.hypothesis_id,
                data_version=record.dataset_hash,
                factor_versions=[],
                event_versions=[],
                engine_version=record.engine_hash,
                parameters={"batch_id": record.batch_id, "candidate_id": record.candidate_id},
                train_period="FACTORY",
                validation_access_count=1,
                sample_size=0,
                result=dict(result or {}),
                verdict=classification,
                failure_reason=";".join(record.reason_codes),
            ))

    def mark_blocked(self, trial_id: str, *, reason_codes: tuple[str, ...] = ()) -> FactoryTrialRecordV1:
        return self._transition(trial_id, status="BLOCKED", event_type="BLOCKED", reason_codes=reason_codes, stage="TRIAL_FAILED", last_activity_at=now_timestamp(), finished_at=now_timestamp(), recovery_status="FAILED")

    def mark_invalidated(self, trial_id: str, *, reason_codes: tuple[str, ...] = ()) -> FactoryTrialRecordV1:
        return self._transition(trial_id, status="INVALIDATED", event_type="INVALIDATED", reason_codes=reason_codes, stage="TRIAL_FAILED", last_activity_at=now_timestamp(), finished_at=now_timestamp(), recovery_status="FAILED")

    def mark_engineering_interrupted(
        self,
        trial_id: str,
        *,
        budget_reservation_identity: str,
        reason_codes: tuple[str, ...] = ("ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS", "EVIDENCE_INCOMPLETE_AFTER_PERFORMANCE_ACCESS"),
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> FactoryTrialRecordV1:
        """Terminalize an accessed trial without manufacturing a result."""
        current = self._latest(trial_id)
        if current is None:
            raise KeyError(f"unknown trial: {trial_id}")
        expected_lineage = {
            "engineering_interrupted": True,
            "terminal_non_adjudicated": True,
            "budget_consumption": "CONSUMED",
            "evidence_recovery": "IRRECOVERABLE_ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS",
        }
        if current.status == "INVALIDATED" and all(current.lineage.get(key) == value for key, value in expected_lineage.items()):
            return current
        if current.status != "PERFORMANCE_ACCESSED" or not current.performance_accessed:
            raise ValueError("engineering interruption requires an accessed, incomplete trial")
        if current.performance_complete or current.final_adjudicated or current.registry_committed:
            raise ValueError("engineering interruption cannot erase completed trial state")
        failure_history = list(current.lineage.get("engineering_failure_history") or [])
        failure_history.append({
            "attempt": len(failure_history) + 1,
            "recorded_at": now_timestamp(),
            "error_code": error_code or current.error_code or "ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS",
            "error_message": error_message or current.error_message,
        })
        return self._transition(
            trial_id,
            status="INVALIDATED",
            event_type="ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS",
            classification="ENGINEERING_INVALIDATED",
            reason_codes=reason_codes,
            lineage_patch={
                **expected_lineage,
                "engineering_attempt_count": len(failure_history),
                "engineering_failure_history": failure_history,
            },
            budget_reservation_identity=budget_reservation_identity,
            stage="TRIAL_FAILED",
            last_activity_at=now_timestamp(),
            finished_at=now_timestamp(),
            error_code=error_code,
            error_message=error_message,
            recovery_status="FAILED",
        )

    def mark_engineering_resume_started(
        self,
        trial_id: str,
        *,
        resume_request_id: str,
        preview_hash: str,
    ) -> FactoryTrialRecordV1:
        """Re-open one engineering-invalidated Trial without a new identity.

        This is deliberately narrower than a general terminal-state
        transition: only the non-adjudicated engineering interruption may be
        resumed, and only once.  The budget and Multiple Testing ledgers are
        not touched here because the original performance access already
        consumed the existing slot.
        """

        current = self._latest(trial_id)
        if current is None:
            raise KeyError(f"unknown trial: {trial_id}")
        request_id = str(resume_request_id)
        expected_hash = str(preview_hash)
        if (
            current.status == "PERFORMANCE_ACCESSED"
            and current.lineage.get("engineering_resume_started") is True
            and str(current.lineage.get("engineering_resume_request_id")) == request_id
            and str(current.lineage.get("engineering_resume_preview_hash")) == expected_hash
        ):
            return current
        if current.status != "INVALIDATED" or current.classification != "ENGINEERING_INVALIDATED":
            raise ValueError("engineering resume requires an engineering-invalidated trial")
        if not current.performance_accessed or current.performance_complete or current.final_adjudicated or current.registry_committed:
            raise ValueError("engineering resume requires incomplete accessed evidence")
        if current.lineage.get("engineering_resume_started") is True or int(current.lineage.get("engineering_resume_attempt_count", 0) or 0) > 0:
            raise ValueError("engineering resume is already attempted")
        if current.lineage.get("budget_consumption") != "CONSUMED":
            raise ValueError("engineering resume requires an already-consumed budget slot")
        record = replace(
            current,
            status="PERFORMANCE_ACCESSED",
            performance_accessed=True,
            performance_complete=False,
            final_adjudicated=False,
            registry_committed=False,
            lineage={
                **current.lineage,
                "engineering_resume_started": True,
                "engineering_resume_request_id": request_id,
                "engineering_resume_preview_hash": expected_hash,
                "engineering_resume_attempt_count": 1,
                "engineering_resume_of_status": "INVALIDATED",
            },
            updated_at=now_timestamp(),
            stage="TRIAL_RUNNING",
            last_activity_at=now_timestamp(),
            finished_at=None,
            error_code=None,
            error_message=None,
            recovery_status="RUNNING",
        )
        self._append(record, event_type="ENGINEERING_RESUME_STARTED")
        return record

    def supersede(self, trial_id: str, *, replacement_trial_id: str) -> FactoryTrialRecordV1:
        return self._transition(trial_id, status="SUPERSEDED", event_type="SUPERSEDED", reason_codes=(f"replacement:{replacement_trial_id}",))

    def latest(self) -> dict[str, FactoryTrialRecordV1]:
        result: dict[str, FactoryTrialRecordV1] = {}
        for event in self._events:
            record = FactoryTrialRecordV1(**{key: value for key, value in event.items() if key not in {"schema_version", "event_type", "event_hash"}})
            result[record.trial_id] = record
        return result

    @property
    def head_hash(self) -> str:
        return stable_hash(self._events)
