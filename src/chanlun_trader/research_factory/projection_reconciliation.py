"""Auditable, one-way repair of runtime projections.

Only ObjectiveReconciliationServiceV1 and canonical facts determine the new
projection.  A canonical conflict returns a blocked receipt and never writes
Daemon or Orchestrator state.
"""
from __future__ import annotations

from .mutation_boundary import mutation_boundary

import json
from pathlib import Path
from typing import Any, Mapping

from ..research_daemon_state import DaemonCheckpointV1
from .common import now_timestamp, stable_hash
from .objective_reconciliation import (
    CANONICAL_CONFLICT,
    ENGINEERING_BLOCKED,
    ObjectiveReconciliationServiceV1,
    PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED,
    READY_FOR_STRUCTURAL_PREFLIGHT,
    STRUCTURAL_BLOCKED,
    STRUCTURAL_RUNNING,
)


RECEIPTS_FILENAME = "projection_reconciliation_receipts.jsonl"


class ProjectionReconciliationError(RuntimeError):
    def __init__(self, code: str, message_zh: str, *, status_code: int = 409, details: Mapping[str, Any] | None = None):
        super().__init__(message_zh)
        self.code = str(code)
        self.message_zh = message_zh
        self.status_code = int(status_code)
        self.details = dict(details or {})

    def envelope(self) -> dict[str, Any]:
        return {"code": self.code, "message_zh": self.message_zh, "details": dict(self.details)}


def _read(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ProjectionReconciliationError("PROJECTION_SOURCE_UNREADABLE", "运行态 projection 暂时不可读。", status_code=503, details={"path": str(path)})
    return dict(payload) if isinstance(payload, Mapping) else None


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{__import__('os').getpid()}.tmp")
    temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def _canonical_effective_state_hash(report: Mapping[str, Any]) -> str:
    """Hash canonical facts only; projection drift must not change the hash."""

    effective = report.get("effective_objective_state") if isinstance(report.get("effective_objective_state"), Mapping) else {}
    basis = {
        key: value
        for key, value in effective.items()
        if key not in {"conflicts", "warnings", "projection_refs"}
    }
    basis["structural_result_reconciliation"] = report.get("structural_result_reconciliation", {})
    return stable_hash(basis or report)


class ProjectionReconciliationServiceV1:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def read(self, objective_id: str) -> dict[str, Any]:
        report = ObjectiveReconciliationServiceV1(self.root).reconcile(objective_id)
        return self._view(report)

    @staticmethod
    def _view(report: Mapping[str, Any]) -> dict[str, Any]:
        effective = report.get("effective_objective_state") if isinstance(report.get("effective_objective_state"), Mapping) else {}
        return {
            "schema_version": "projection-reconciliation-view-v1",
            "objective_id": report.get("objective_id"),
            "effective_state": effective.get("effective_state") or report.get("effective_state"),
            "required_action": effective.get("required_action") or report.get("required_action"),
            "safe_to_resume": effective.get("safe_to_resume", report.get("safe_to_resume")),
            "safe_to_advance": effective.get("safe_to_advance", report.get("safe_to_advance")),
            "conflict_level": report.get("conflict_level"),
            "conflicts": list(report.get("conflicts", ())),
            "canonical_effective_state_hash": _canonical_effective_state_hash(report),
            "projection_reconciliation": report.get("projection_reconciliation", {}),
            "canonical_refs": report.get("canonical_refs", {}),
            "read_only": True,
        }

    @staticmethod
    def _desired(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        effective = report.get("effective_objective_state") if isinstance(report.get("effective_objective_state"), Mapping) else {}
        state = str(effective.get("effective_state") or report.get("effective_state") or "")
        candidate = effective.get("current_candidate_id")
        candidate_hash = effective.get("current_candidate_hash")
        common = {
            "objective_id": report.get("objective_id"),
            "canonical_effective_state": state,
            "canonical_effective_state_hash": _canonical_effective_state_hash(report),
            "current_candidate_id": candidate,
            "current_candidate_hash": candidate_hash,
        }
        if state == PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED:
            return {
                "daemon": {**common, "state": "STRUCTURAL_PASS", "required_action": PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED},
                "orchestrator": {**common, "state": "ACTIVE", "required_action": PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED},
            }
        if state == STRUCTURAL_RUNNING:
            return {
                "daemon": {**common, "state": "STRUCTURAL_RUNNING", "required_action": "STRUCTURAL_RUN_IN_PROGRESS"},
                "orchestrator": {**common, "state": "LOCAL_RESEARCH_RUNNING", "required_action": "STRUCTURAL_RUN_IN_PROGRESS"},
            }
        if state == STRUCTURAL_BLOCKED:
            return {
                "daemon": {**common, "state": "STRUCTURAL_BLOCKED", "required_action": "RECONCILE_STRUCTURAL"},
                "orchestrator": {**common, "state": "ACTIVE", "required_action": "RECONCILE_STRUCTURAL"},
            }
        if state == ENGINEERING_BLOCKED:
            return {
                "daemon": {**common, "state": ENGINEERING_BLOCKED, "required_action": "ENGINEERING_REVIEW_REQUIRED"},
                "orchestrator": {**common, "state": ENGINEERING_BLOCKED, "required_action": "ENGINEERING_REVIEW_REQUIRED"},
            }
        if state == READY_FOR_STRUCTURAL_PREFLIGHT:
            return {
                "daemon": {**common, "state": "READY", "required_action": "RUN_STRUCTURAL_PREFLIGHT"},
                "orchestrator": {**common, "state": "ACTIVE", "required_action": "RUN_STRUCTURAL_PREFLIGHT"},
            }
        return {}

    def _receipt_rows(self, path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                payload = json.loads(line)
                if isinstance(payload, Mapping):
                    rows.append(dict(payload))
        return rows

    @mutation_boundary()
    def reconcile(self, objective_id: str, *, apply: bool = True, reason: str = "CANONICAL_FACT_REPROJECTED") -> dict[str, Any]:
        report = ObjectiveReconciliationServiceV1(self.root).reconcile(objective_id)
        if CANONICAL_CONFLICT in set(str(item) for item in report.get("conflicts", ())) or str(report.get("conflict_level")) == CANONICAL_CONFLICT:
            return {
                **self._view(report),
                "status": "BLOCKED",
                "available": False,
                "safe_to_resume": False,
                "human_action_required": True,
                "reason_code": CANONICAL_CONFLICT,
                "reason_zh": "Canonical 事实存在冲突，不能自动修复运行态 projection。",
                "receipts": [],
            }
        desired = self._desired(report)
        if not desired:
            return {**self._view(report), "status": "NOOP", "available": False, "receipts": []}
        canonical_effective_hash = _canonical_effective_state_hash(report)
        receipt_path = self.root / "reports" / "research_reconciliation" / objective_id / RECEIPTS_FILENAME
        receipts = self._receipt_rows(receipt_path)
        emitted: list[dict[str, Any]] = []
        for projection_type, target in desired.items():
            if projection_type == "daemon":
                paths = [
                    self.root / "reports" / "research_daemon" / objective_id / "daemon_checkpoint.json",
                    self.root / "reports" / "research_daemon" / objective_id / "daemon_status.json",
                ]
            else:
                paths = [
                    self.root / "reports" / "research_orchestrator_v2" / objective_id / "orchestrator_checkpoint.json",
                    self.root / "reports" / "research_orchestrator_v2" / objective_id / "orchestrator_status.json",
                ]
            for path in paths:
                old = _read(path)
                if old is None:
                    continue
                old_hash = stable_hash(old)
                if projection_type == "daemon" and path.name == "daemon_checkpoint.json":
                    old_refs = old.get("canonical_refs") if isinstance(old.get("canonical_refs"), Mapping) else {}
                    old_projection = old_refs.get("projection_reconciliation") if isinstance(old_refs.get("projection_reconciliation"), Mapping) else {}
                    already_projected = (
                        str(old.get("current_state") or "") == str(target["state"])
                        and str(old.get("required_action") or "") == str(target["required_action"])
                        and str(old_projection.get("canonical_effective_state_hash") or "") == canonical_effective_hash
                    )
                else:
                    projected_state = old.get("daemon_state") if projection_type == "daemon" else old.get("orchestrator_state")
                    fallback_state = old.get("current_state") if projection_type == "daemon" else old.get("state")
                    already_projected = (
                        str(projected_state or fallback_state or "") == str(target["state"])
                        and str(old.get("required_action") or "") == str(target["required_action"])
                        and str(old.get("canonical_effective_state_hash") or "") == canonical_effective_hash
                    )
                if already_projected:
                    continue
                updated = dict(old)
                if projection_type == "daemon" and path.name == "daemon_checkpoint.json":
                    checkpoint = DaemonCheckpointV1.from_dict(old)
                    canonical_refs = dict(checkpoint.canonical_refs)
                    canonical_refs["projection_reconciliation"] = {
                        "projection_type": projection_type,
                        "canonical_effective_state_hash": canonical_effective_hash,
                        "projection_only": True,
                    }
                    updated = checkpoint.update(
                        current_state=target["state"],
                        required_action=target["required_action"],
                        canonical_refs=canonical_refs,
                    ).to_dict()
                else:
                    updated.update({
                        "current_state" if projection_type == "daemon" else "orchestrator_state": target["state"],
                        "daemon_state" if projection_type == "daemon" else "state": target["state"],
                        "required_action": target["required_action"],
                        "next_action": target["required_action"],
                        "projection_only": True,
                        "canonical_effective_state_hash": canonical_effective_hash,
                        "updated_at": now_timestamp(),
                    })
                new_hash = stable_hash(updated)
                if old_hash == new_hash:
                    continue
                receipt_base = {
                    "schema_version": "projection-reconciliation-receipt-v1",
                    "objective_id": objective_id,
                    "projection_type": projection_type,
                    "projection_ref": path.relative_to(self.root).as_posix(),
                    "old_projection_hash": old_hash,
                    "canonical_effective_state_hash": canonical_effective_hash,
                    "new_projection_hash": new_hash,
                    "reason": reason,
                    "timestamp": now_timestamp(),
                }
                receipt = {**receipt_base, "receipt_hash": stable_hash(receipt_base)}
                if any(str(item.get("receipt_hash")) == receipt["receipt_hash"] for item in receipts):
                    continue
                if apply:
                    _atomic(path, updated)
                    receipt_path.parent.mkdir(parents=True, exist_ok=True)
                    with receipt_path.open("a", encoding="utf-8", newline="\n") as handle:
                        handle.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
                    receipts.append(receipt)
                emitted.append(receipt)
        return {
            **self._view(report),
            "status": "PASS" if emitted else "NOOP",
            "available": bool(emitted),
            "applied": bool(apply and emitted),
            "receipts": emitted,
            "reason": reason,
        }


__all__ = ["ProjectionReconciliationError", "ProjectionReconciliationServiceV1", "RECEIPTS_FILENAME"]
