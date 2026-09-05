"""受控终结已访问绩效但未完成的 canonical Trial。"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping

from chanlun_trader.research.strategy_validation import TrialEvent, TrialRegistryV1
from chanlun_trader.research_daemon_state import DaemonCheckpointStoreV1, DaemonInstanceLockV1, ResearchDaemonState

from .budget import SearchBudgetRegistryV1
from .common import jsonable, now_timestamp, stable_hash
from .trial_adapter import FactoryTrialRecordV1, ResearchFactoryTrialLedgerFacadeV1


PREVIEW_SCHEMA = "canonical-trial-reconciliation-preview-v1"
RESULT_SCHEMA = "canonical-trial-reconciliation-result-v1"
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class TrialReconciliationError(RuntimeError):
    pass


class CanonicalTrialReconciliationServiceV1:
    """只允许把不可恢复的绩效访问中断追加终结为工程失效。"""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    @staticmethod
    def _safe_id(value: str) -> str:
        value = str(value)
        if not SAFE_ID.fullmatch(value):
            raise TrialReconciliationError("UNSAFE_TRIAL_RECONCILIATION_IDENTITY")
        return value

    def _ledger(self, objective_id: str, trial_id: str) -> tuple[Path, ResearchFactoryTrialLedgerFacadeV1, FactoryTrialRecordV1]:
        matches: list[tuple[Path, FactoryTrialRecordV1]] = []
        for path in (self.root / "data/research/research_factory/batches").glob("*/factory_trial_ledger.json"):
            ledger = ResearchFactoryTrialLedgerFacadeV1(path=path)
            record = ledger.latest().get(trial_id)
            if record is not None and record.objective_id == objective_id:
                matches.append((path, record))
        if len(matches) != 1:
            raise TrialReconciliationError("CANONICAL_TRIAL_IDENTITY_NOT_UNIQUE")
        path, record = matches[0]
        registry_path = path.parent / "trial_registry.json"
        if not registry_path.exists():
            raise TrialReconciliationError("CANONICAL_TRIAL_REGISTRY_MISSING")
        return path, ResearchFactoryTrialLedgerFacadeV1(trial_registry=TrialRegistryV1(registry_path), path=path), record

    def _budget(self, objective_id: str, ledger_path: Path, record: FactoryTrialRecordV1) -> tuple[Path, SearchBudgetRegistryV1, str]:
        path = ledger_path.parent / "search_budget_registry.json"
        if not path.exists():
            raise TrialReconciliationError("CANONICAL_TRIAL_BUDGET_REGISTRY_MISSING")
        reservation_id = str(record.budget_reservation_identity or "")
        if not reservation_id:
            raise TrialReconciliationError("CANONICAL_TRIAL_BUDGET_RESERVATION_MISSING")
        budget = SearchBudgetRegistryV1(objective_id, path)
        snapshot = budget.snapshot()
        active = reservation_id in snapshot.get("active_reservations", {})
        settled = str(snapshot.get("settled_reservations", {}).get(reservation_id) or "")
        if not active and settled != "CONSUMED":
            raise TrialReconciliationError("CANONICAL_TRIAL_BUDGET_IDENTITY_UNRECONCILABLE")
        return path, budget, "ACTIVE" if active else "CONSUMED"

    def _checkpoint(self, objective_id: str, record: FactoryTrialRecordV1) -> tuple[DaemonCheckpointStoreV1, Any]:
        store = DaemonCheckpointStoreV1(self.root, objective_id)
        checkpoint = store.load()
        if checkpoint is None:
            raise TrialReconciliationError("CANONICAL_TRIAL_CHECKPOINT_MISSING")
        current_trial_id = str((checkpoint.current_trial or {}).get("trial_id") or "")
        current_candidate_id = str((checkpoint.current_candidate or {}).get("candidate_id") or record.candidate_id)
        if current_trial_id != record.trial_id or current_candidate_id != record.candidate_id:
            raise TrialReconciliationError("CANONICAL_TRIAL_CHECKPOINT_IDENTITY_MISMATCH")
        if checkpoint.current_state != ResearchDaemonState.ENGINEERING_BLOCKED.value or checkpoint.required_action != "CANONICAL_TRIAL_RECONCILIATION_REQUIRED":
            raise TrialReconciliationError("CANONICAL_TRIAL_RECONCILIATION_NOT_REQUIRED")
        return store, checkpoint

    def _provisional_evidence_exists(self, objective_id: str, record: FactoryTrialRecordV1) -> bool:
        path = (
            self.root
            / "reports/research_daemon"
            / objective_id
            / "predictive"
            / record.batch_id
            / record.candidate_id
            / "provisional_validation_evidence"
            / f"{record.trial_id}.json"
        )
        return path.exists()

    def preview(self, *, objective_id: str, trial_id: str) -> dict[str, Any]:
        objective_id = self._safe_id(objective_id)
        trial_id = self._safe_id(trial_id)
        ledger_path, ledger, record = self._ledger(objective_id, trial_id)
        expected_terminal_lineage = bool(
            record.status == "INVALIDATED"
            and record.classification == "ENGINEERING_INVALIDATED"
            and record.lineage.get("engineering_interrupted") is True
            and record.lineage.get("terminal_non_adjudicated") is True
        )
        if record.status in ResearchFactoryTrialLedgerFacadeV1.TERMINAL and not expected_terminal_lineage:
            raise TrialReconciliationError("CANONICAL_TRIAL_ALREADY_TERMINAL")
        if self._provisional_evidence_exists(objective_id, record) or record.performance_complete or record.final_adjudicated or record.registry_committed:
            raise TrialReconciliationError("CANONICAL_TRIAL_EVIDENCE_RECOVERY_REQUIRED")
        if not expected_terminal_lineage and (record.status != "PERFORMANCE_ACCESSED" or not record.performance_accessed):
            raise TrialReconciliationError("CANONICAL_TRIAL_NOT_ACCESSED_INCOMPLETE")
        _, checkpoint = self._checkpoint(objective_id, record)
        budget_path, budget, reservation_status = self._budget(objective_id, ledger_path, record)
        payload = jsonable({
            "schema_version": PREVIEW_SCHEMA,
            "status": "READY_TO_CONFIRM",
            "available": True,
            "objective_id": objective_id,
            "trial_id": trial_id,
            "candidate_id": record.candidate_id,
            "candidate_hash": record.candidate_hash,
            "batch_id": record.batch_id,
            "trial_status": record.status,
            "performance_accessed": record.performance_accessed,
            "performance_complete": record.performance_complete,
            "final_adjudicated": record.final_adjudicated,
            "registry_committed": record.registry_committed,
            "budget_reservation_identity": record.budget_reservation_identity,
            "budget_reservation_status": reservation_status,
            "action": "TERMINALIZE_ENGINEERING_INTERRUPTION",
            "reason_code": "PERFORMANCE_ACCESSED_INCOMPLETE_TRIAL",
            "requires_confirmation": True,
            "safety_evidence": {
                "performance_rerun": False,
                "new_trial": False,
                "provisional_evidence_present": False,
                "ledger_head_hash": ledger.head_hash,
                "budget_head_hash": budget.head_hash,
                "checkpoint_hash": checkpoint.checkpoint_hash,
                "ledger_ref": ledger_path.relative_to(self.root).as_posix(),
                "budget_ref": budget_path.relative_to(self.root).as_posix(),
            },
        })
        preview_hash = stable_hash(payload)
        confirmation_token = stable_hash({"preview_hash": preview_hash, "action": payload["action"]})
        return {**payload, "preview_hash": preview_hash, "confirmation_token": confirmation_token}

    def confirm(self, *, objective_id: str, trial_id: str, preview_hash: str, confirmation_token: str) -> dict[str, Any]:
        objective_id = self._safe_id(objective_id)
        trial_id = self._safe_id(trial_id)
        report_path = DaemonCheckpointStoreV1(self.root, objective_id).runtime_dir / "trial_reconciliations" / f"{trial_id}.json"
        if report_path.exists():
            existing = json.loads(report_path.read_text(encoding="utf-8"))
            if existing.get("preview_hash") == preview_hash and existing.get("confirmation_token") == confirmation_token:
                return existing
        preview = self.preview(objective_id=objective_id, trial_id=trial_id)
        if preview_hash != preview["preview_hash"] or confirmation_token != preview["confirmation_token"]:
            raise TrialReconciliationError("STALE_CANONICAL_TRIAL_RECONCILIATION_PREVIEW")

        store = DaemonCheckpointStoreV1(self.root, objective_id)
        lock = DaemonInstanceLockV1(store.lock_path, objective_id)
        lock.acquire(run_id=f"TRIAL_RECONCILIATION_{preview_hash[:16]}")
        try:
            current_preview = self.preview(objective_id=objective_id, trial_id=trial_id)
            if current_preview["preview_hash"] != preview_hash or current_preview["confirmation_token"] != confirmation_token:
                raise TrialReconciliationError("STALE_CANONICAL_TRIAL_RECONCILIATION_PREVIEW")
            ledger_path, ledger, record = self._ledger(objective_id, trial_id)
            budget_path, budget, _ = self._budget(objective_id, ledger_path, record)
            terminal = ledger.mark_engineering_interrupted(
                trial_id,
                budget_reservation_identity=str(record.budget_reservation_identity),
            )
            registry_path = ledger_path.parent / "trial_registry.json"
            if registry_path.exists():
                TrialRegistryV1(registry_path).append(TrialEvent(
                    event_type="ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS",
                    trial_id=terminal.trial_id,
                    candidate_id=terminal.candidate_id,
                    candidate_preregistration_hash=terminal.candidate_hash,
                    status=terminal.status,
                    classification=terminal.classification,
                    reason_codes=terminal.reason_codes,
                    performance_accessed=terminal.performance_accessed,
                ))
            budget.consume(str(record.budget_reservation_identity))
            budget_snapshot = budget.snapshot()
            objective_bucket = next(
                (item for item in budget_snapshot.get("buckets", ()) if item.get("kind") == "objective" and item.get("key") == objective_id),
                {},
            )
            report = jsonable({
                "schema_version": RESULT_SCHEMA,
                "status": "COMPLETE",
                "reconciliation_id": stable_hash({"trial_id": trial_id, "candidate_hash": terminal.candidate_hash, "action": "TERMINALIZE_ENGINEERING_INTERRUPTION"}),
                "objective_id": objective_id,
                "trial_id": trial_id,
                "candidate_id": terminal.candidate_id,
                "candidate_hash": terminal.candidate_hash,
                "terminal_status": terminal.status,
                "classification": terminal.classification,
                "reason_codes": list(terminal.reason_codes),
                "budget_reservation_identity": terminal.budget_reservation_identity,
                "budget_reservation_status": "CONSUMED",
                "performance_rerun": False,
                "new_trial": False,
                "final_test_access": {"physical": 0, "analytical": 0, "decision": 0},
                "prospective_validation": "NOT_RUN",
                "real_order": "DISABLED",
                "preview_hash": preview_hash,
                "confirmation_token": confirmation_token,
                "ledger_ref": ledger_path.relative_to(self.root).as_posix(),
                "budget_ref": budget_path.relative_to(self.root).as_posix(),
                "completed_at": now_timestamp(),
            })
            DaemonCheckpointStoreV1._atomic_write(report_path, report)

            checkpoint = store.load()
            if checkpoint is None:
                raise TrialReconciliationError("CANONICAL_TRIAL_CHECKPOINT_MISSING")
            completed_candidate = dict(checkpoint.current_candidate or {"candidate_id": terminal.candidate_id, "candidate_hash": terminal.candidate_hash})
            checkpoint = checkpoint.transition(
                ResearchDaemonState.READY,
                "CANONICAL_TRIAL_RECONCILED_AFTER_PERFORMANCE_INTERRUPTION",
                details={"trial_id": trial_id, "reconciliation_id": report["reconciliation_id"], "performance_rerun": False},
            )
            checkpoint = checkpoint.update(
                current_candidate=None,
                current_trial=None,
                last_completed_candidate=completed_candidate,
                required_action=None,
                last_error=None,
                error_reason_code=None,
                retry_safe=True,
                budget_view={
                    "used": int(objective_bucket.get("used", 0)),
                    "total": int(objective_bucket.get("limit", 0)),
                    "remaining": int(objective_bucket.get("remaining", 0)),
                    "reserved": int(objective_bucket.get("reserved", 0)),
                    "registry_path": budget_path.relative_to(self.root).as_posix(),
                    "registry_head_hash": budget.head_hash,
                },
                canonical_refs={
                    **dict(checkpoint.canonical_refs),
                    "last_predictive_result": terminal.to_dict(),
                    "trial_reconciliation": {
                        "status": "COMPLETE",
                        "reconciliation_id": report["reconciliation_id"],
                        "report_ref": report_path.relative_to(self.root).as_posix(),
                    },
                },
            )
            store.save(checkpoint)
            store.append_event("STATE_TRANSITION", checkpoint.last_transition or {})
            store.append_event(
                "CANONICAL_TRIAL_RECONCILIATION_COMPLETE",
                {"objective_id": objective_id, "trial_id": trial_id, "candidate_id": terminal.candidate_id, "reconciliation_id": report["reconciliation_id"]},
                event_id=str(report["reconciliation_id"]),
            )
            status = store.load_status()
            if status is not None:
                status.update({
                    "daemon_state": ResearchDaemonState.READY.value,
                    "stage": ResearchDaemonState.READY.value,
                    "current_candidate_id": None,
                    "current_trial": None,
                    "required_human_ai_action": None,
                    "last_error": None,
                    "retry_safe": True,
                    "budget": dict(checkpoint.budget_view),
                    "last_checkpoint_time": checkpoint.checkpoint_at,
                    "process_pid": None,
                })
                store.save_status(status)
            return report
        finally:
            lock.release()


__all__ = ["CanonicalTrialReconciliationServiceV1", "TrialReconciliationError"]
