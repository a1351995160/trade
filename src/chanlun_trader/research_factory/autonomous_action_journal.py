"""Durable exact-once receipts for the autonomous research control plane.

The journal is a runtime projection.  It records which control-plane action
was attempted and how that attempt was reconciled; it never becomes a source
of Objective, Candidate, Trial, Budget, Structural, or Predictive truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import threading
from typing import Any, Callable, Iterable, Mapping

from .common import now_timestamp, stable_hash


ACTION_JOURNAL_SCHEMA_VERSION = "autonomous-action-execution-receipt-v1"
ACTION_JOURNAL_FILENAME = "action_execution_receipts.jsonl"
EXECUTION_STARTED = "STARTED"
EXECUTION_COMPLETED = "COMPLETED"
EXECUTION_FAILED = "FAILED"
EXECUTION_RETRY_ALLOWED = "RECOVERY_RETRY_ALLOWED"


class AutonomousActionJournalError(RuntimeError):
    """Fail-closed journal error."""


@dataclass(frozen=True)
class AutonomousActionExecutionReceiptV1:
    action_id: str
    objective_id: str
    action_type: str
    input_context_hash: str
    idempotency_key: str
    execution_status: str
    started_at: str | None
    completed_at: str | None
    resulting_reconciliation_hash: str | None
    resulting_state: str | None
    side_effect_refs: tuple[str, ...]
    receipt_hash: str
    attempt: int = 1
    recovered: bool = False
    error_code: str | None = None
    error_message_zh: str | None = None
    result_summary: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": ACTION_JOURNAL_SCHEMA_VERSION,
            "action_id": self.action_id,
            "objective_id": self.objective_id,
            "action_type": self.action_type,
            "input_context_hash": self.input_context_hash,
            "idempotency_key": self.idempotency_key,
            "execution_status": self.execution_status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "resulting_reconciliation_hash": self.resulting_reconciliation_hash,
            "resulting_state": self.resulting_state,
            "side_effect_refs": list(self.side_effect_refs),
            "attempt": self.attempt,
            "recovered": self.recovered,
            "error_code": self.error_code,
            "error_message_zh": self.error_message_zh,
            "result_summary": dict(self.result_summary or {}),
        }
        payload["receipt_hash"] = self.receipt_hash or stable_hash(payload)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AutonomousActionExecutionReceiptV1":
        if str(payload.get("schema_version") or ACTION_JOURNAL_SCHEMA_VERSION) != ACTION_JOURNAL_SCHEMA_VERSION:
            raise AutonomousActionJournalError("action receipt schema mismatch")
        return cls(
            action_id=str(payload.get("action_id") or ""),
            objective_id=str(payload.get("objective_id") or ""),
            action_type=str(payload.get("action_type") or ""),
            input_context_hash=str(payload.get("input_context_hash") or ""),
            idempotency_key=str(payload.get("idempotency_key") or ""),
            execution_status=str(payload.get("execution_status") or ""),
            started_at=str(payload.get("started_at")) if payload.get("started_at") else None,
            completed_at=str(payload.get("completed_at")) if payload.get("completed_at") else None,
            resulting_reconciliation_hash=str(payload.get("resulting_reconciliation_hash")) if payload.get("resulting_reconciliation_hash") else None,
            resulting_state=str(payload.get("resulting_state")) if payload.get("resulting_state") else None,
            side_effect_refs=tuple(str(item) for item in payload.get("side_effect_refs", ()) if item),
            receipt_hash=str(payload.get("receipt_hash") or ""),
            attempt=int(payload.get("attempt") or 1),
            recovered=bool(payload.get("recovered")),
            error_code=str(payload.get("error_code")) if payload.get("error_code") else None,
            error_message_zh=str(payload.get("error_message_zh")) if payload.get("error_message_zh") else None,
            result_summary=dict(payload.get("result_summary") or {}) if isinstance(payload.get("result_summary"), Mapping) else {},
        )


def _action_payload(action: Any) -> dict[str, Any]:
    if hasattr(action, "to_dict"):
        value = action.to_dict()
    elif isinstance(action, Mapping):
        value = dict(action)
    else:
        raise AutonomousActionJournalError("unsupported action payload")
    return {str(key): value for key, value in value.items()}


def _identity(payload: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(payload.get("action_id") or ""),
        str(payload.get("idempotency_key") or ""),
        str(payload.get("source_context_hash") or payload.get("input_context_hash") or ""),
        str(payload.get("objective_id") or ""),
    )


class AutonomousActionExecutionJournalV1:
    """Append-only journal with restart-safe logical action identity."""

    def __init__(
        self,
        root: str | Path,
        objective_id: str,
        *,
        clock: Callable[[], str] = now_timestamp,
    ) -> None:
        self.root = Path(root).resolve()
        self.objective_id = str(objective_id)
        self.clock = clock
        self.directory = self.root / "reports" / "research_control_plane" / self.objective_id
        self.path = self.directory / ACTION_JOURNAL_FILENAME
        self.lock_path = self.directory / "control_plane.lock"
        self._mutex = threading.RLock()

    def _read_rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, Mapping):
                    raise ValueError("receipt must be an object")
                receipt = AutonomousActionExecutionReceiptV1.from_dict(payload)
                if receipt.objective_id != self.objective_id:
                    raise AutonomousActionJournalError("action receipt objective mismatch")
                if receipt.receipt_hash != stable_hash({key: value for key, value in receipt.to_dict().items() if key != "receipt_hash"}):
                    raise AutonomousActionJournalError("action receipt hash mismatch")
                rows.append(receipt.to_dict())
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise AutonomousActionJournalError("action receipt journal is unreadable") from exc
        return rows

    def read(self) -> tuple[AutonomousActionExecutionReceiptV1, ...]:
        with self._mutex:
            return tuple(AutonomousActionExecutionReceiptV1.from_dict(row) for row in self._read_rows())

    def _append(self, payload: Mapping[str, Any]) -> AutonomousActionExecutionReceiptV1:
        receipt = AutonomousActionExecutionReceiptV1.from_dict(payload)
        serialized = receipt.to_dict()
        serialized["receipt_hash"] = stable_hash({key: value for key, value in serialized.items() if key != "receipt_hash"})
        receipt = AutonomousActionExecutionReceiptV1.from_dict(serialized)
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(receipt.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        return receipt

    @staticmethod
    def _matches(receipt: AutonomousActionExecutionReceiptV1, action: Mapping[str, Any]) -> bool:
        return (
            receipt.action_id == str(action.get("action_id") or "")
            and receipt.objective_id == str(action.get("objective_id") or "")
            and receipt.action_type == str(action.get("action_type") or "")
            and receipt.idempotency_key == str(action.get("idempotency_key") or "")
            and receipt.input_context_hash == str(action.get("source_context_hash") or action.get("input_context_hash") or "")
        )

    def latest(self, action: Any) -> AutonomousActionExecutionReceiptV1 | None:
        payload = _action_payload(action)
        with self._mutex:
            rows = [receipt for receipt in self.read() if self._matches(receipt, payload)]
            return rows[-1] if rows else None

    def _assert_no_identity_conflict(self, action: Mapping[str, Any], rows: Iterable[AutonomousActionExecutionReceiptV1]) -> None:
        action_id = str(action.get("action_id") or "")
        for receipt in rows:
            if receipt.action_id != action_id:
                continue
            if not self._matches(receipt, action):
                raise AutonomousActionJournalError("same action_id has conflicting execution identity")

    def begin(self, action: Any, *, allow_retry: bool = False) -> tuple[str, AutonomousActionExecutionReceiptV1]:
        payload = _action_payload(action)
        if str(payload.get("objective_id") or "") != self.objective_id:
            raise AutonomousActionJournalError("action objective mismatch")
        with self._mutex:
            rows = list(self.read())
            self._assert_no_identity_conflict(payload, rows)
            existing = next((receipt for receipt in reversed(rows) if self._matches(receipt, payload)), None)
            if existing is not None:
                if existing.execution_status == EXECUTION_COMPLETED:
                    return "COMPLETED", existing
                if existing.execution_status == EXECUTION_STARTED and not allow_retry:
                    return "STARTED", existing
            attempts = max((receipt.attempt for receipt in rows if self._matches(receipt, payload)), default=0) + 1
            receipt = self._append({
                "schema_version": ACTION_JOURNAL_SCHEMA_VERSION,
                "action_id": str(payload.get("action_id") or ""),
                "objective_id": self.objective_id,
                "action_type": str(payload.get("action_type") or ""),
                "input_context_hash": str(payload.get("source_context_hash") or payload.get("input_context_hash") or ""),
                "idempotency_key": str(payload.get("idempotency_key") or ""),
                "execution_status": EXECUTION_STARTED,
                "started_at": str(self.clock()),
                "completed_at": None,
                "resulting_reconciliation_hash": None,
                "resulting_state": None,
                "side_effect_refs": [],
                "attempt": attempts,
                "recovered": False,
                "error_code": None,
                "error_message_zh": None,
                "result_summary": {},
                "receipt_hash": "",
            })
            return "STARTED", receipt

    def allow_retry(self, action: Any, *, reason_code: str = "DOMAIN_SIDE_EFFECT_NOT_FOUND") -> AutonomousActionExecutionReceiptV1:
        payload = _action_payload(action)
        with self._mutex:
            existing = self.latest(payload)
            if existing is None:
                raise AutonomousActionJournalError("cannot allow retry without an existing action attempt")
            return self._append({
                **existing.to_dict(),
                "execution_status": EXECUTION_RETRY_ALLOWED,
                "completed_at": str(self.clock()),
                "error_code": reason_code,
                "receipt_hash": "",
            })

    def complete(
        self,
        action: Any,
        *,
        resulting_reconciliation_hash: str,
        resulting_state: str,
        side_effect_refs: Iterable[str] = (),
        result_summary: Mapping[str, Any] | None = None,
        recovered: bool = False,
    ) -> AutonomousActionExecutionReceiptV1:
        payload = _action_payload(action)
        with self._mutex:
            existing = self.latest(payload)
            if existing is not None and existing.execution_status == EXECUTION_COMPLETED:
                return existing
            started_at = existing.started_at if existing is not None else str(self.clock())
            return self._append({
                "schema_version": ACTION_JOURNAL_SCHEMA_VERSION,
                "action_id": str(payload.get("action_id") or ""),
                "objective_id": self.objective_id,
                "action_type": str(payload.get("action_type") or ""),
                "input_context_hash": str(payload.get("source_context_hash") or payload.get("input_context_hash") or ""),
                "idempotency_key": str(payload.get("idempotency_key") or ""),
                "execution_status": EXECUTION_COMPLETED,
                "started_at": started_at,
                "completed_at": str(self.clock()),
                "resulting_reconciliation_hash": str(resulting_reconciliation_hash),
                "resulting_state": str(resulting_state),
                "side_effect_refs": sorted({str(item) for item in side_effect_refs if item}),
                "attempt": existing.attempt if existing is not None else 1,
                "recovered": bool(recovered),
                "error_code": None,
                "error_message_zh": None,
                "result_summary": dict(result_summary or {}),
                "receipt_hash": "",
            })

    def fail(self, action: Any, *, error_code: str, error_message_zh: str) -> AutonomousActionExecutionReceiptV1:
        payload = _action_payload(action)
        with self._mutex:
            existing = self.latest(payload)
            return self._append({
                "schema_version": ACTION_JOURNAL_SCHEMA_VERSION,
                "action_id": str(payload.get("action_id") or ""),
                "objective_id": self.objective_id,
                "action_type": str(payload.get("action_type") or ""),
                "input_context_hash": str(payload.get("source_context_hash") or payload.get("input_context_hash") or ""),
                "idempotency_key": str(payload.get("idempotency_key") or ""),
                "execution_status": EXECUTION_FAILED,
                "started_at": existing.started_at if existing is not None else str(self.clock()),
                "completed_at": str(self.clock()),
                "resulting_reconciliation_hash": None,
                "resulting_state": None,
                "side_effect_refs": [],
                "attempt": existing.attempt if existing is not None else 1,
                "recovered": False,
                "error_code": str(error_code),
                "error_message_zh": str(error_message_zh),
                "result_summary": {},
                "receipt_hash": "",
            })

    def snapshot_hash(self) -> str:
        return stable_hash([receipt.to_dict() for receipt in self.read()])


__all__ = [
    "ACTION_JOURNAL_FILENAME",
    "ACTION_JOURNAL_SCHEMA_VERSION",
    "EXECUTION_COMPLETED",
    "EXECUTION_FAILED",
    "EXECUTION_RETRY_ALLOWED",
    "EXECUTION_STARTED",
    "AutonomousActionExecutionJournalV1",
    "AutonomousActionExecutionReceiptV1",
    "AutonomousActionJournalError",
]
