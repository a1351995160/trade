"""Explicit Structural Entry Gate and execution service.

The module is the single domain entry point for Structural preflight.  Reads
use ObjectiveReconciliationServiceV1; execution reuses the existing
CanonicalResearchRuntime.structural_preflight provider.  No Predictive,
Performance, Trial, Budget reservation, Candidate, or AI service is called
from this boundary.
"""
from __future__ import annotations

from .mutation_boundary import mutation_boundary

import json
from pathlib import Path
from typing import Any, Mapping

from ..research_daemon import CandidateWork, CanonicalResearchRuntime, ResearchDaemon, StructuralResult
from ..research_daemon_state import DaemonCheckpointStoreV1, DaemonInstanceLockV1, ResearchDaemonState
from .common import now_timestamp, stable_hash
from .candidate_executable_materialization import (
    EXECUTABLE_MATERIALIZATION_CONFIRMATION_MISSING,
    EXECUTABLE_MATERIALIZATION_PREVIEW_READY,
    INTEGRITY_FAILURE,
    inspect_materialization_confirmation,
    inspect_materialization_preview,
)
from .durability import DurableFrozenCandidateContractV1
from .objective_reconciliation import (
    CANONICAL_CONFLICT,
    ENGINEERING_BLOCKED,
    ObjectiveReconciliationServiceV1,
    READY_FOR_STRUCTURAL_PREFLIGHT,
    RUN_STRUCTURAL_PREFLIGHT,
    STRUCTURAL_BLOCKED,
    STRUCTURAL_RUNNING,
    PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED,
)
from .structural_reconciliation import (
    _structural_identity,
    _structural_result_hash,
    _trial_event_signature,
    persist_canonical_structural_result,
)


STRUCTURAL_EXECUTION_EVIDENCE_FILENAME = "structural_execution_evidence.json"
STRUCTURAL_START_CONFIRMATION_REQUIRED = "STRUCTURAL_START_CONFIRMATION_REQUIRED"
STRUCTURAL_ENTRY_GATE_BLOCKED = "STRUCTURAL_ENTRY_GATE_BLOCKED"
STRUCTURAL_IDEMPOTENCY_CONFLICT = "STRUCTURAL_IDEMPOTENCY_CONFLICT"


class StructuralEntryError(RuntimeError):
    """Fail-closed error raised before or during explicit Structural entry."""

    def __init__(self, code: str, message_zh: str, *, status_code: int = 409, details: Mapping[str, Any] | None = None):
        super().__init__(message_zh)
        self.code = str(code)
        self.message_zh = message_zh
        self.status_code = int(status_code)
        self.details = dict(details or {})

    def envelope(self) -> dict[str, Any]:
        return {"code": self.code, "message_zh": self.message_zh, "details": dict(self.details)}


def _safe_identifier(value: str) -> bool:
    return bool(value) and value.replace("_", "a").replace("-", "a").replace(".", "a").isalnum()


def _read_json(path: Path) -> Mapping[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _candidate_from_contract_path(root: Path, objective_id: str, candidate_id: str, expected_hash: str | None = None) -> tuple[CandidateWork, DurableFrozenCandidateContractV1, dict[str, Any]]:
    matches: list[tuple[Path, Mapping[str, Any]]] = []
    directory = root / "data" / "research" / "research_factory" / "batches"
    for path in sorted(directory.glob("*/durable_frozen_candidate_contracts.json")):
        payload = _read_json(path) or {}
        for raw in payload.get("contracts", ()) if isinstance(payload.get("contracts"), list) else ():
            if not isinstance(raw, Mapping) or str(raw.get("candidate_id") or "") != candidate_id:
                continue
            policy = raw.get("policy_identity") if isinstance(raw.get("policy_identity"), Mapping) else {}
            if str(policy.get("objective_id") or raw.get("objective_id") or "") != objective_id:
                continue
            if expected_hash and str(raw.get("candidate_hash") or "") != expected_hash:
                continue
            matches.append((path, raw))
    if not matches:
        raise StructuralEntryError(
            "DURABLE_FROZEN_CANDIDATE_REQUIRED",
            "只有 DurableFrozenCandidateContractV1 可以进入 Structural；当前未找到唯一有效合同。",
            details={"objective_id": objective_id, "candidate_id": candidate_id},
        )
    valid: list[tuple[Path, DurableFrozenCandidateContractV1, dict[str, Any]]] = []
    for path, raw in matches:
        try:
            contract = DurableFrozenCandidateContractV1.from_dict(raw)
            provider_payload = contract.provider_candidate_payload()
        except Exception as exc:
            raise StructuralEntryError(
                "DURABLE_CONTRACT_INVALID",
                "DurableFrozenCandidateContractV1 校验失败，Structural 已阻断。",
                details={"source": path.relative_to(root).as_posix(), "error": str(exc)},
            ) from exc
        valid.append((path, contract, dict(provider_payload)))
    identities = {(contract.candidate_id, contract.candidate_hash, contract.content_hash) for _, contract, _ in valid}
    if len(valid) != 1 or len(identities) != 1:
        raise StructuralEntryError(
            "DURABLE_CONTRACT_IDENTITY_CONFLICT",
            "当前 Objective 没有唯一的 DurableFrozenCandidateContractV1，Structural 已阻断。",
            details={"candidate_id": candidate_id, "sources": [path.relative_to(root).as_posix() for path, _, _ in valid]},
        )
    path, contract, provider_payload = valid[0]
    candidate = CandidateWork(
        contract.candidate_id,
        contract.candidate_hash,
        path.relative_to(root).as_posix(),
        path.parent.name,
        contract.mechanism,
        {"raw_contract": contract.to_dict()},
    )
    return candidate, contract, provider_payload


class StructuralEntryGateV1:
    """Common gate used by CLI, Web, daemon and direct domain callers."""

    def __init__(self, root: str | Path, objective_id: str, *, runtime: Any | None = None):
        self.root = Path(root).resolve()
        self.objective_id = str(objective_id)
        self.runtime = runtime

    def inspect(self, candidate_id: str | None = None) -> dict[str, Any]:
        if not _safe_identifier(self.objective_id) or (candidate_id is not None and not _safe_identifier(str(candidate_id))):
            raise StructuralEntryError("INVALID_STRUCTURAL_IDENTITY", "Structural Entry 的 Objective/Candidate 标识不合法。", status_code=400)
        report = ObjectiveReconciliationServiceV1(self.root).reconcile(self.objective_id)
        effective = report.get("effective_objective_state") if isinstance(report.get("effective_objective_state"), Mapping) else {}
        conflicts = set(str(item) for item in report.get("conflicts", ()))
        if CANONICAL_CONFLICT in conflicts or str(report.get("conflict_level") or "") == CANONICAL_CONFLICT:
            raise StructuralEntryError(
                CANONICAL_CONFLICT,
                "Canonical 事实存在冲突，Structural 不得启动。",
                details={"objective_id": self.objective_id, "conflicts": sorted(conflicts)},
            )
        candidate_view = report.get("candidate_reconciliation") if isinstance(report.get("candidate_reconciliation"), Mapping) else {}
        if str(effective.get("effective_state") or report.get("effective_state") or "") != READY_FOR_STRUCTURAL_PREFLIGHT:
            valid_contracts = [
                item for item in candidate_view.get("durable_contracts", ())
                if isinstance(item, Mapping)
                and item.get("from_dict") == "PASS"
                and item.get("provider_candidate_payload") == "PASS"
            ]
            if not valid_contracts:
                raise StructuralEntryError(
                    "DURABLE_FROZEN_CANDIDATE_REQUIRED",
                    "只有 DurableFrozenCandidateContractV1 可以进入 Structural；当前未找到有效合同。",
                    details={"effective_state": effective.get("effective_state") or report.get("effective_state")},
                )
            raise StructuralEntryError(
                STRUCTURAL_ENTRY_GATE_BLOCKED,
                "当前 Objective 尚未处于 READY_FOR_STRUCTURAL_PREFLIGHT，Structural 不得启动。",
                details={"effective_state": effective.get("effective_state") or report.get("effective_state"), "required_action": report.get("required_action")},
            )
        if report.get("structural_preflight_ready") is not True or effective.get("structural_preflight_ready") is not True:
            raise StructuralEntryError("DURABLE_FROZEN_CANDIDATE_REQUIRED", "当前没有通过完整 Durable Contract gate 的 Candidate。", details={"structural_preflight_ready": False})
        canonical_candidate_id = str(candidate_view.get("current_candidate_id") or "")
        if candidate_id and canonical_candidate_id and str(candidate_id) != canonical_candidate_id:
            raise StructuralEntryError("CANDIDATE_IDENTITY_CONFLICT", "请求的 Candidate 与 canonical reconciliation 不一致。")
        resolved_candidate_id = canonical_candidate_id or str(candidate_id or "")
        resolved_candidate_hash = str(candidate_view.get("current_candidate_hash") or "")
        if not resolved_candidate_id:
            raise StructuralEntryError("DURABLE_FROZEN_CANDIDATE_REQUIRED", "Structural Entry 缺少唯一 Candidate identity。")
        valid_entries = [
            item for item in candidate_view.get("durable_contracts", ())
            if isinstance(item, Mapping)
            and str(item.get("candidate_id") or "") == resolved_candidate_id
            and item.get("from_dict") == "PASS"
            and item.get("provider_candidate_payload") == "PASS"
        ]
        if len(valid_entries) != 1:
            raise StructuralEntryError(
                "DURABLE_CONTRACT_NOT_UNIQUE",
                "Structural Entry 要求唯一且完整的 DurableFrozenCandidateContractV1。",
                details={"candidate_id": resolved_candidate_id, "valid_contract_count": len(valid_entries)},
            )
        candidate, contract, provider_payload = _candidate_from_contract_path(
            self.root,
            self.objective_id,
            resolved_candidate_id,
            resolved_candidate_hash or None,
        )
        if resolved_candidate_hash and candidate.candidate_hash != resolved_candidate_hash:
            raise StructuralEntryError("CANDIDATE_IDENTITY_CONFLICT", "Candidate hash 与 Durable Contract 不一致。")
        materialization = candidate_view.get("materialization") if isinstance(candidate_view.get("materialization"), Mapping) else {}
        source_paths = [str(item) for item in materialization.get("source_paths", ()) if item]
        preview_path_text = str(materialization.get("preview_path") or next((item for item in source_paths if item.endswith("EXECUTABLE_MATERIALIZATION_PREVIEW.json")), ""))
        confirmation_path_text = str(materialization.get("confirmation_path") or next((item for item in source_paths if item.endswith("EXECUTABLE_MATERIALIZATION_CONFIRMATION.json")), ""))
        preview = _read_json(self.root / preview_path_text) if preview_path_text else None
        preview_check = inspect_materialization_preview(preview)
        if not preview_check["valid"] or not preview_check["ready"] or str((preview or {}).get("status") or "") != EXECUTABLE_MATERIALIZATION_PREVIEW_READY:
            raise StructuralEntryError(
                str(preview_check.get("reason_code") or "STALE_EXECUTABLE_MATERIALIZATION"),
                "Executable Materialization Preview 缺失、过期或完整性失败，Structural 不得启动。",
                details={"preview_path": preview_path_text, "safe_to_advance": False},
            )
        if str(preview.get("objective_id") or "") != self.objective_id or str(preview.get("candidate_id") or "") != contract.candidate_id or str(preview.get("candidate_hash") or "") != contract.candidate_hash or str(preview.get("durable_contract_hash") or "") != contract.content_hash:
            raise StructuralEntryError(
                "CANONICAL_CONFLICT",
                "Executable Materialization Preview 与当前 Durable Contract 身份不一致，Structural 不得启动。",
                details={"preview_path": preview_path_text, "safe_to_advance": False},
            )
        confirmation = _read_json(self.root / confirmation_path_text) if confirmation_path_text else None
        if confirmation is None:
            raise StructuralEntryError(
                EXECUTABLE_MATERIALIZATION_CONFIRMATION_MISSING,
                "缺少人工 Executable Materialization Confirmation Receipt，Structural 不得启动。",
                details={"confirmation_path": confirmation_path_text, "safe_to_advance": False},
            )
        confirmation_check = inspect_materialization_confirmation(
            confirmation,
            preview=preview,
            contract=contract,
            objective_id=self.objective_id,
            proposal_id=str(preview.get("proposal_id") or "") or None,
        )
        if not confirmation_check["valid"] or not confirmation_check["identity_match"]:
            raise StructuralEntryError(
                str(confirmation_check.get("reason_code") or INTEGRITY_FAILURE),
                "人工 Executable Materialization Confirmation Receipt 缺失、篡改或身份不匹配，Structural 不得启动。",
                details={"confirmation_path": confirmation_path_text, "mismatched_fields": confirmation_check.get("mismatched_fields", []), "safe_to_advance": False},
            )
        return {
            "report": report,
            "effective_state": READY_FOR_STRUCTURAL_PREFLIGHT,
            "objective_id": self.objective_id,
            "candidate": candidate,
            "candidate_id": candidate.candidate_id,
            "candidate_hash": candidate.candidate_hash,
            "contract": contract,
            "provider_payload": provider_payload,
            "durable_contract_hash": contract.content_hash,
            "materialization_confirmation_hash": confirmation.get("receipt_hash"),
            "valid_contract_source": valid_entries[0].get("source_path"),
        }


class StructuralEntryServiceV1:
    """Execute exactly one explicit Structural preflight through the gate."""

    def __init__(self, root: str | Path, *, runtime: Any | None = None):
        self.root = Path(root).resolve()
        self.runtime = runtime

    def readiness(self, objective_id: str) -> dict[str, Any]:
        try:
            result = StructuralEntryGateV1(self.root, objective_id, runtime=self.runtime).inspect()
        except StructuralEntryError as exc:
            return {
                "schema_version": "structural-readiness-v1",
                "objective_id": str(objective_id),
                "status": "BLOCKED",
                "available": False,
                "required_action": RUN_STRUCTURAL_PREFLIGHT,
                "reason_code": exc.code,
                "reason_zh": exc.message_zh,
                "details": dict(exc.details),
            }
        return {
            "schema_version": "structural-readiness-v1",
            "objective_id": str(objective_id),
            "status": "READY",
            "available": True,
            "required_action": RUN_STRUCTURAL_PREFLIGHT,
            "candidate_id": result["candidate_id"],
            "candidate_hash": result["candidate_hash"],
            "durable_contract_hash": result["durable_contract_hash"],
            "reason_zh": "唯一 DurableFrozenCandidateContractV1 已通过 Structural Entry gate，等待显式启动。",
        }

    def _existing_terminal(self, objective_id: str, candidate_id: str | None) -> dict[str, Any] | None:
        path = self.root / "reports" / "research_daemon" / objective_id / "structural_preflight_reconciliation_canonical_v1.json"
        payload = _read_json(path)
        if payload is None or str(payload.get("status") or "").upper() not in {"PASS", "UNKNOWN", "BLOCKED", "ENGINEERING_BLOCKED"}:
            return None
        if candidate_id and str(payload.get("candidate_id") or "") != candidate_id:
            return None
        return dict(payload)

    @mutation_boundary()
    def start(
        self,
        objective_id: str,
        *,
        candidate_id: str | None = None,
        confirmed: bool = True,
        action: str = RUN_STRUCTURAL_PREFLIGHT,
        runtime: Any | None = None,
    ) -> dict[str, Any]:
        if not _safe_identifier(str(objective_id)) or (candidate_id is not None and not _safe_identifier(str(candidate_id))):
            raise StructuralEntryError("INVALID_STRUCTURAL_IDENTITY", "Structural Entry 的 Objective/Candidate 标识不合法。", status_code=400)
        if confirmed is not True or str(action).upper() != RUN_STRUCTURAL_PREFLIGHT:
            raise StructuralEntryError(STRUCTURAL_START_CONFIRMATION_REQUIRED, "Structural 启动必须通过明确的 RUN_STRUCTURAL_PREFLIGHT 操作确认。", status_code=400)

        # A terminal canonical result is checked before the READY gate so a
        # repeated start returns the existing fact instead of calling Provider.
        existing = self._existing_terminal(objective_id, candidate_id)
        if existing is not None:
            reconciliation = ObjectiveReconciliationServiceV1(self.root).reconcile(objective_id)
            if CANONICAL_CONFLICT in set(str(item) for item in reconciliation.get("conflicts", ())) or str(reconciliation.get("conflict_level") or "") == CANONICAL_CONFLICT:
                raise StructuralEntryError(
                    CANONICAL_CONFLICT,
                    "Canonical 事实存在冲突，不能复用 Structural 终态。",
                    details={"objective_id": objective_id, "conflicts": list(reconciliation.get("conflicts", ()))},
                )
            effective = reconciliation.get("effective_objective_state") if isinstance(reconciliation.get("effective_objective_state"), Mapping) else {}
            reconciled_candidate_id = str(effective.get("current_candidate_id") or reconciliation.get("current_candidate_id") or "")
            reconciled_candidate_hash = str(effective.get("current_candidate_hash") or reconciliation.get("current_candidate_hash") or "")
            existing_candidate_id = str(existing.get("candidate_id") or candidate_id or "")
            existing_candidate_hash = str(existing.get("candidate_hash") or "")
            if reconciled_candidate_id and existing_candidate_id != reconciled_candidate_id:
                raise StructuralEntryError(STRUCTURAL_IDEMPOTENCY_CONFLICT, "已有 Structural 终态与当前 Candidate identity 不一致。")
            if reconciled_candidate_hash and existing_candidate_hash and existing_candidate_hash != reconciled_candidate_hash:
                raise StructuralEntryError(STRUCTURAL_IDEMPOTENCY_CONFLICT, "已有 Structural 终态与当前 Candidate hash 不一致。")
            try:
                candidate, contract, provider_payload = _candidate_from_contract_path(
                    self.root,
                    objective_id,
                    existing_candidate_id,
                )
            except StructuralEntryError:
                raise
            if existing_candidate_hash and candidate.candidate_hash != existing_candidate_hash:
                raise StructuralEntryError(STRUCTURAL_IDEMPOTENCY_CONFLICT, "已有 Structural 终态与当前 Durable Contract candidate hash 不一致。")
            existing_identity = existing.get("structural_identity") if isinstance(existing.get("structural_identity"), Mapping) else {}
            if str(existing_identity.get("durable_contract_hash") or existing.get("durable_contract_hash") or "") not in {"", contract.content_hash}:
                raise StructuralEntryError(STRUCTURAL_IDEMPOTENCY_CONFLICT, "已有 Structural 终态与当前 Durable Contract identity 不一致。")
            canonical_result = existing.get("canonical_result") if isinstance(existing.get("canonical_result"), Mapping) else {}
            if existing.get("result_hash") and existing_identity.get("execution_identity_hash") and canonical_result:
                try:
                    existing_result = StructuralResult(
                        str(canonical_result.get("status") or existing.get("status") or ""),
                        str(canonical_result.get("reason_code") or ""),
                        tuple(canonical_result.get("artifact_refs") or ()),
                        canonical_result.get("provider_checkpoint"),
                        canonical_result.get("partition_index"),
                        details=dict(canonical_result.get("details") or {}),
                    )
                    recomputed_identity = _structural_identity(
                        self.root,
                        objective_id=objective_id,
                        candidate=candidate,
                        result=existing_result,
                        provider_payload=provider_payload,
                    )
                    recomputed_result_hash = _structural_result_hash(existing_result, recomputed_identity)
                except Exception as exc:
                    raise StructuralEntryError(STRUCTURAL_IDEMPOTENCY_CONFLICT, "已有 Structural 终态无法重新验证 identity，系统已 fail closed。", details={"error": str(exc)}) from exc
                if (
                    recomputed_identity.get("execution_identity_hash") != existing_identity.get("execution_identity_hash")
                    or recomputed_result_hash != str(existing.get("result_hash"))
                ):
                    raise StructuralEntryError(STRUCTURAL_IDEMPOTENCY_CONFLICT, "已有 Structural 终态与当前执行 identity 不一致，系统已 fail closed。")
            return {
                "status": str(existing.get("status")),
                "effective_state": str(existing.get("effective_state") or (PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED if str(existing.get("status")) == "PASS" else STRUCTURAL_BLOCKED)),
                "required_action": existing.get("required_action"),
                "reconciliation_id": existing.get("reconciliation_id"),
                "report_ref": "reports/research_daemon/%s/structural_preflight_reconciliation_canonical_v1.json" % objective_id,
                "candidate_id": candidate.candidate_id,
                "candidate_hash": candidate.candidate_hash,
                "idempotent": True,
                "provider_invoked": False,
                "predictive_authorized": False,
            }

        active_runtime = runtime or self.runtime or CanonicalResearchRuntime(self.root, objective_id=objective_id)
        gate = StructuralEntryGateV1(self.root, objective_id, runtime=active_runtime).inspect(candidate_id)
        candidate = gate["candidate"]
        provider_payload = gate["provider_payload"]
        store = DaemonCheckpointStoreV1(self.root, objective_id)
        lock = DaemonInstanceLockV1(store.lock_path, objective_id)
        lock.acquire(run_id=f"STRUCTURAL_ENTRY_{stable_hash({'objective_id': objective_id, 'candidate_id': candidate.candidate_id})[:20]}")
        try:
            # Re-check under the objective lock so two explicit callers cannot
            # race past the same canonical terminal result.
            existing = self._existing_terminal(objective_id, candidate.candidate_id)
            if existing is not None:
                return {
                    "status": str(existing.get("status")),
                    "effective_state": existing.get("effective_state"),
                    "required_action": existing.get("required_action"),
                    "reconciliation_id": existing.get("reconciliation_id"),
                    "report_ref": f"reports/research_daemon/{objective_id}/structural_preflight_reconciliation_canonical_v1.json",
                    "candidate_id": candidate.candidate_id,
                    "candidate_hash": candidate.candidate_hash,
                    "idempotent": True,
                    "provider_invoked": False,
                    "predictive_authorized": False,
                }
            daemon = ResearchDaemon(self.root, objective_id=objective_id, runtime=active_runtime, sleep_seconds=0)
            daemon._ensure_bootstrap()
            if daemon.checkpoint is None:
                raise StructuralEntryError("DAEMON_CHECKPOINT_UNAVAILABLE", "Structural Entry 无法建立 daemon projection。", status_code=503)
            if daemon.checkpoint.current_state != ResearchDaemonState.READY.value:
                raise StructuralEntryError("STRUCTURAL_RUNTIME_NOT_READY", "Daemon 当前不在可启动 Structural 的安全边界。", details={"daemon_state": daemon.checkpoint.current_state})
            budget_before = dict(active_runtime.load_context().budget)
            trial_before = _trial_event_signature(self.root, objective_id, candidate.candidate_id)
            contract_hash = gate["durable_contract_hash"]
            refs = dict(daemon.checkpoint.canonical_refs)
            refs["structural_entry"] = {
                "action": RUN_STRUCTURAL_PREFLIGHT,
                "candidate_id": candidate.candidate_id,
                "candidate_hash": candidate.candidate_hash,
                "durable_contract_hash": contract_hash,
                "started_at": now_timestamp(),
            }
            daemon.checkpoint = daemon.checkpoint.update(current_candidate=candidate.to_dict(), required_action=RUN_STRUCTURAL_PREFLIGHT, canonical_refs=refs, retry_safe=True)
            daemon._transition(ResearchDaemonState.STRUCTURAL_PENDING, "EXPLICIT_STRUCTURAL_START")
            evidence_path = store.runtime_dir / STRUCTURAL_EXECUTION_EVIDENCE_FILENAME
            running_evidence = {
                "schema_version": "structural-execution-evidence-v1",
                "authority_role": "STRUCTURAL_EXECUTION_EVIDENCE",
                "status": STRUCTURAL_RUNNING,
                "execution_action": RUN_STRUCTURAL_PREFLIGHT,
                "objective_id": objective_id,
                "candidate_id": candidate.candidate_id,
                "candidate_hash": candidate.candidate_hash,
                "durable_contract_hash": contract_hash,
                "provider_payload_identity": stable_hash(provider_payload),
                "started_at": now_timestamp(),
                "outcome_blind": True,
                "performance_data_loaded": False,
                "predictive_authorized": False,
            }
            DaemonCheckpointStoreV1._atomic_write(evidence_path, running_evidence)
            daemon._transition(ResearchDaemonState.STRUCTURAL_RUNNING, "STRUCTURAL_PROVIDER_EXECUTION_STARTED")
            try:
                result = active_runtime.structural_preflight(candidate)
                if not isinstance(result, StructuralResult):
                    raise TypeError("canonical Structural provider must return StructuralResult")
            except Exception as exc:
                result = StructuralResult(
                    ENGINEERING_BLOCKED,
                    "STRUCTURAL_PROVIDER_EXECUTION_FAILURE",
                    (candidate.contract_ref,),
                    details={"error_type": type(exc).__name__, "error": str(exc), "stage": "structural_provider"},
                )
            terminal_evidence = {
                **running_evidence,
                "status": "COMPLETED",
                "result_status": result.status,
                "result_reason_code": result.reason_code,
                "result_hash": stable_hash({"status": result.status, "reason_code": result.reason_code, "details": dict(result.details)}),
                "completed_at": now_timestamp(),
                "provider_checkpoint": result.provider_checkpoint,
                "partition_index": result.partition_index,
            }
            DaemonCheckpointStoreV1._atomic_write(evidence_path, terminal_evidence)
            budget_after_provider = dict(active_runtime.load_context().budget)
            trial_after_provider = _trial_event_signature(self.root, objective_id, candidate.candidate_id)
            if budget_after_provider != budget_before or trial_after_provider != trial_before:
                raise StructuralEntryError("STRUCTURAL_SIDE_EFFECT_DETECTED", "Structural Provider 改变了 Predictive Budget 或 TrialLedger，系统已阻断。", status_code=503)
            try:
                persisted = persist_canonical_structural_result(
                    self.root,
                    objective_id=objective_id,
                    candidate=candidate,
                    result=result,
                    provider_payload=provider_payload,
                    budget_before=budget_before,
                    trial_signature_before=trial_before,
                    execution_evidence=terminal_evidence,
                )
            except RuntimeError as exc:
                if str(exc) in {STRUCTURAL_IDEMPOTENCY_CONFLICT, "CANONICAL_STRUCTURAL_RESULT_IDENTITY_CONFLICT"}:
                    raise StructuralEntryError(str(exc), "Structural identity 冲突，系统已 fail closed。", details={"candidate_id": candidate.candidate_id}) from exc
                engineering = StructuralResult(
                    ENGINEERING_BLOCKED,
                    "STRUCTURAL_CANONICAL_RESULT_PERSISTENCE_FAILURE",
                    (candidate.contract_ref,),
                    details={"error_type": type(exc).__name__, "error": str(exc), "stage": "canonical_result_persistence"},
                )
                persisted = persist_canonical_structural_result(
                    self.root,
                    objective_id=objective_id,
                    candidate=candidate,
                    result=engineering,
                    provider_payload=provider_payload,
                    budget_before=budget_before,
                    trial_signature_before=trial_before,
                    execution_evidence=terminal_evidence,
                )
                result = engineering
            budget_after = dict(active_runtime.load_context().budget)
            trial_after = _trial_event_signature(self.root, objective_id, candidate.candidate_id)
            if budget_after != budget_before or trial_after != trial_before:
                raise StructuralEntryError("STRUCTURAL_SIDE_EFFECT_DETECTED", "Structural 执行改变了 Predictive Budget 或 TrialLedger，系统已阻断。", status_code=503)
            canonical_result = dict(persisted.get("report") or {})
            refs = {
                **dict(daemon.checkpoint.canonical_refs),
                "last_structural_result": dict(canonical_result.get("canonical_result") or {"status": result.status, "reason_code": result.reason_code, "details": dict(result.details)}),
                "structural_result_authority": {
                    "report_ref": persisted.get("report_ref"),
                    "reconciliation_id": persisted.get("reconciliation_id"),
                    "result_hash": canonical_result.get("result_hash"),
                    "structural_identity": canonical_result.get("structural_identity"),
                },
                "structural_reconciliation": {
                    "status": result.status,
                    "reconciliation_id": persisted.get("reconciliation_id"),
                    "report_ref": persisted.get("report_ref"),
                    "history_ref": persisted.get("history_ref"),
                    "governance_ref": persisted.get("governance_ref"),
                    "predictive_run_started": False,
                },
            }
            if result.status == "PASS":
                target_state = ResearchDaemonState.STRUCTURAL_PASS
                required_action = PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED
                reason = "STRUCTURAL_PASS_AWAITING_HUMAN_PREDICTIVE_AUTHORIZATION"
                current_candidate = None
                last_completed = candidate.to_dict()
            elif str(result.status).upper() in {"UNKNOWN", "STRUCTURAL_UNKNOWN", "BLOCKED", "STRUCTURAL_BLOCKED", "INSUFFICIENT_SAMPLE", "BOUND_INCOMPLETE"}:
                target_state = ResearchDaemonState.STRUCTURAL_BLOCKED
                required_action = "RECONCILE_STRUCTURAL"
                reason = "STRUCTURAL_BLOCKED_FAIL_CLOSED"
                current_candidate = candidate.to_dict()
                last_completed = None
            else:
                target_state = ResearchDaemonState.ENGINEERING_BLOCKED
                required_action = "ENGINEERING_REVIEW_REQUIRED"
                reason = result.reason_code or "ENGINEERING_BLOCKED"
                current_candidate = candidate.to_dict()
                last_completed = None
            daemon.checkpoint = daemon.checkpoint.update(
                canonical_refs=refs,
                current_candidate=current_candidate,
                last_completed_candidate=last_completed,
                current_trial=None,
                required_action=required_action,
                last_error=None,
                error_reason_code=None,
                retry_safe=True,
            )
            daemon._transition(target_state, reason, reconciliation_id=persisted.get("reconciliation_id"), result_hash=canonical_result.get("result_hash"), predictive_run_started=False)
            return {
                "status": result.status,
                "effective_state": canonical_result.get("effective_state") or (PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED if result.status == "PASS" else STRUCTURAL_BLOCKED),
                "required_action": required_action,
                "reconciliation_id": persisted.get("reconciliation_id"),
                "report_ref": persisted.get("report_ref"),
                "history_ref": persisted.get("history_ref"),
                "governance_ref": persisted.get("governance_ref"),
                "candidate_id": candidate.candidate_id,
                "candidate_hash": candidate.candidate_hash,
                "idempotent": bool(persisted.get("idempotent")),
                "provider_invoked": True,
                "predictive_authorized": False,
                "predictive_run_started": False,
                "budget_before": budget_before,
                "budget_after": budget_after,
                "trial_signature_before": trial_before,
                "trial_signature_after": trial_after,
            }
        finally:
            lock.heartbeat()
            lock.release()


__all__ = [
    "STRUCTURAL_EXECUTION_EVIDENCE_FILENAME",
    "STRUCTURAL_ENTRY_GATE_BLOCKED",
    "STRUCTURAL_IDEMPOTENCY_CONFLICT",
    "STRUCTURAL_START_CONFIRMATION_REQUIRED",
    "StructuralEntryError",
    "StructuralEntryGateV1",
    "StructuralEntryServiceV1",
]
