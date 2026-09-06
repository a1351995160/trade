"""Durable human approval boundary for one outcome-blind AI research design.

The approval receipt is the only authority that can unlock Candidate Proposal
generation for an AI Design.  This module deliberately owns no Candidate,
Trial, Structural, Predictive, AI-runtime, or Budget mutation.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Callable

from .common import jsonable, now_timestamp, stable_hash
from .context import PerformanceBlindGuard, PerformanceLeakError
from .research_evolution_ai_design import (
    AI_DESIGN_READY,
    HUMAN_CONFIRM_AI_RESEARCH_DESIGN,
    NEED_AI_RESEARCH_DESIGN,
    ai_design_identity_hash,
)


AI_DESIGN_APPROVED = "AI_DESIGN_APPROVED"
AI_DESIGN_AWAITING_CONFIRMATION = "AI_DESIGN_AWAITING_CONFIRMATION"
AI_DESIGN_REJECTED = "AI_DESIGN_REJECTED"
CANDIDATE_GENERATION_ALLOWED = "CANDIDATE_GENERATION_ALLOWED"
GENERATE_CANDIDATE_PROPOSAL = "GENERATE_CANDIDATE_PROPOSAL"
GENERATE_AI_RESEARCH_DESIGN = "GENERATE_AI_RESEARCH_DESIGN"

APPROVED = "APPROVED"
REJECTED = "REJECTED"
AI_DESIGN_APPROVAL_APPROVED = APPROVED
AI_DESIGN_APPROVAL_REJECTED = REJECTED
PENDING = "PENDING"
STALE = "STALE"
INVALID = "INVALID"

AI_DESIGN_APPROVAL_SCHEMA_VERSION = "ai-design-approval-v1"
AI_DESIGN_APPROVAL_RESULT_SCHEMA_VERSION = "ai-design-approval-result-v1"
AI_DESIGN_APPROVAL_RECEIPT_FILENAME = "AI_DESIGN_APPROVAL_RECEIPT.json"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_REVIEWER_MAX_LENGTH = 128
_RECEIPT_REQUIRED_FIELDS = (
    "schema_version",
    "approval_id",
    "objective_id",
    "ai_design_id",
    "ai_design_hash",
    "decision",
    "reviewer",
    "reviewed_at",
    "idempotency_key",
    "receipt_hash",
)


class AIDesignApprovalError(RuntimeError):
    """Fail-closed error at the AI Design approval boundary."""

    def __init__(
        self,
        code: str,
        message_zh: str,
        *,
        status_code: int = 409,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message_zh)
        self.code = str(code)
        self.message_zh = str(message_zh)
        self.status_code = int(status_code)
        self.details = dict(details or {})

    def envelope(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message_zh": self.message_zh,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class AIDesignApprovalReceiptV1:
    """Immutable approval evidence bound to one exact AI Design identity."""

    approval_id: str
    objective_id: str
    ai_design_id: str
    ai_design_hash: str
    decision: str
    reviewer: str
    reviewed_at: str
    idempotency_key: str
    receipt_hash: str
    source_context_hash: str | None = None
    source_context_id: str | None = None
    proposal_id: str | None = None
    lineage: Mapping[str, Any] | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": AI_DESIGN_APPROVAL_SCHEMA_VERSION,
            "approval_id": self.approval_id,
            "objective_id": self.objective_id,
            "ai_design_id": self.ai_design_id,
            "ai_design_hash": self.ai_design_hash,
        }
        if self.source_context_hash not in (None, ""):
            payload["source_context_hash"] = self.source_context_hash
        if self.source_context_id not in (None, ""):
            payload["source_context_id"] = self.source_context_id
        if self.proposal_id not in (None, ""):
            payload["proposal_id"] = self.proposal_id
        if self.lineage is not None:
            payload["lineage"] = dict(self.lineage)
        payload.update({
            "decision": self.decision,
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
        })
        if self.reason not in (None, ""):
            payload["reason"] = self.reason
        payload.update({
            "idempotency_key": self.idempotency_key,
            "resulting_state": AI_DESIGN_APPROVED if self.decision == APPROVED else AI_DESIGN_REJECTED,
            "next_action": GENERATE_CANDIDATE_PROPOSAL if self.decision == APPROVED else GENERATE_AI_RESEARCH_DESIGN,
            "candidate_generation_allowed": self.decision == APPROVED,
            "structural_preflight_ready": False,
            "candidate_created": False,
            "candidate_materialized": False,
            "trial_started": False,
            "ai_called": False,
            "budget_consumed": False,
            "automatic_execution": False,
            "receipt_hash": self.receipt_hash,
        })
        return payload


def _safe_id(value: Any, *, kind: str) -> str:
    result = str(value or "").strip()
    if not _IDENTIFIER_RE.fullmatch(result):
        raise AIDesignApprovalError("INVALID_IDENTIFIER", f"{kind} 标识不合法", status_code=400)
    return result


def _safe_reviewer(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > _REVIEWER_MAX_LENGTH or "\n" in result or "\r" in result:
        raise AIDesignApprovalError("REVIEWER_REQUIRED", "必须提供合法的人工审核人", status_code=400)
    return result


def _safe_token(value: Any, *, kind: str) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 255 or "\n" in result or "\r" in result:
        raise AIDesignApprovalError("IDEMPOTENCY_KEY_REQUIRED", f"必须提供合法的 {kind}", status_code=400)
    return result


def _read_json(path: Path, *, required: bool = True) -> Mapping[str, Any] | None:
    if not path.exists():
        if required:
            raise AIDesignApprovalError("AI_DESIGN_APPROVAL_NOT_FOUND", f"未找到 AI 设计批准回执：{path.name}", status_code=404)
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AIDesignApprovalError("AI_DESIGN_APPROVAL_INTEGRITY_FAILURE", "AI 设计批准回执不可读，系统保持阻断", status_code=503) from exc
    if not isinstance(payload, Mapping):
        raise AIDesignApprovalError("AI_DESIGN_APPROVAL_INTEGRITY_FAILURE", "AI 设计批准回执不是 JSON 对象，系统保持阻断", status_code=503)
    return payload


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise AIDesignApprovalError("UNSAFE_PATH", "AI 设计批准回执路径不在项目目录内", status_code=503) from exc


def _serialized(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")


def _create_immutable_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Create the receipt without ever replacing an existing target."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_bytes(_serialized(payload))
    try:
        os.link(temporary, path)
    except FileExistsError:
        raise
    finally:
        temporary.unlink(missing_ok=True)


class AIDesignApprovalServiceV1:
    """Persist and verify one exact-once human decision for an AI Design."""

    _mutex = threading.RLock()

    def __init__(self, root: str | Path, *, clock: Callable[[], str] = now_timestamp) -> None:
        self.root = Path(root).resolve()
        self.design_root = self.root / "reports" / "research_evolution" / "ai_design"
        self.clock = clock

    def _design_dir(self, objective_id: str) -> Path:
        path = (self.design_root / _safe_id(objective_id, kind="objective_id")).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise AIDesignApprovalError("UNSAFE_PATH", "AI 设计目录不在项目目录内", status_code=503) from exc
        return path

    def _design_path(self, objective_id: str) -> Path:
        return self._design_dir(objective_id) / "AI_RESEARCH_DESIGN_PROPOSAL.json"

    def _receipt_path(self, objective_id: str) -> Path:
        return self._design_dir(objective_id) / AI_DESIGN_APPROVAL_RECEIPT_FILENAME

    def _load_design(self, objective_id: str, *, required: bool = True) -> Mapping[str, Any] | None:
        path = self._design_path(objective_id)
        if not path.exists():
            if required:
                raise AIDesignApprovalError("AI_DESIGN_NOT_FOUND", "当前 Objective 没有 AI 研究设计", status_code=404)
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AIDesignApprovalError("AI_DESIGN_APPROVAL_INTEGRITY_FAILURE", "AI 研究设计不可读，批准边界保持阻断", status_code=503) from exc
        if not isinstance(payload, Mapping):
            raise AIDesignApprovalError("AI_DESIGN_APPROVAL_INTEGRITY_FAILURE", "AI 研究设计不是 JSON 对象，批准边界保持阻断", status_code=503)
        try:
            PerformanceBlindGuard.assert_blind(payload)
        except PerformanceLeakError as exc:
            raise AIDesignApprovalError("AI_DESIGN_APPROVAL_INTEGRITY_FAILURE", "AI 研究设计包含结果字段，批准边界保持阻断", status_code=503) from exc
        return payload

    @staticmethod
    def _design_identity_error(design: Mapping[str, Any]) -> AIDesignApprovalError | None:
        design_hash = str(design.get("design_hash") or "")
        if not design_hash:
            return AIDesignApprovalError("AI_DESIGN_APPROVAL_INTEGRITY_FAILURE", "AI 研究设计缺少 design_hash，批准边界保持阻断", status_code=503)
        expected = ai_design_identity_hash(design)
        if design_hash != expected:
            return AIDesignApprovalError(
                "AI_DESIGN_APPROVAL_INTEGRITY_FAILURE",
                "AI 研究设计哈希校验失败，批准边界保持阻断",
                status_code=503,
                details={"current_design_hash": design_hash, "expected_design_hash": expected},
            )
        return None

    @staticmethod
    def _receipt_error(receipt: Mapping[str, Any]) -> AIDesignApprovalError | None:
        missing = [key for key in _RECEIPT_REQUIRED_FIELDS if receipt.get(key) in (None, "")]
        if missing:
            return AIDesignApprovalError(
                "AI_DESIGN_APPROVAL_INTEGRITY_FAILURE",
                "AI 设计批准回执缺少必要字段，系统保持阻断",
                status_code=503,
                details={"missing": missing},
            )
        if str(receipt.get("schema_version")) != AI_DESIGN_APPROVAL_SCHEMA_VERSION:
            return AIDesignApprovalError("AI_DESIGN_APPROVAL_INTEGRITY_FAILURE", "AI 设计批准回执版本不受支持，系统保持阻断", status_code=503)
        decision = str(receipt.get("decision") or "").upper()
        if decision not in {APPROVED, REJECTED}:
            return AIDesignApprovalError("AI_DESIGN_APPROVAL_INTEGRITY_FAILURE", "AI 设计批准回执决策无效，系统保持阻断", status_code=503)
        try:
            PerformanceBlindGuard.assert_blind(receipt)
        except PerformanceLeakError:
            return AIDesignApprovalError("AI_DESIGN_APPROVAL_INTEGRITY_FAILURE", "AI 设计批准回执包含结果字段，系统保持阻断", status_code=503)
        expected_hash = stable_hash({key: value for key, value in receipt.items() if key != "receipt_hash"})
        if str(receipt.get("receipt_hash") or "") != expected_hash:
            return AIDesignApprovalError(
                "AI_DESIGN_APPROVAL_INTEGRITY_FAILURE",
                "AI 设计批准回执哈希校验失败，系统保持阻断",
                status_code=503,
                details={"receipt_path": AI_DESIGN_APPROVAL_RECEIPT_FILENAME},
            )
        return None

    def _read_receipt(self, objective_id: str) -> Mapping[str, Any] | None:
        return _read_json(self._receipt_path(objective_id), required=False)

    @staticmethod
    def _evaluation_base(objective_id: str, design: Mapping[str, Any] | None, receipt_path: Path | None, root: Path) -> dict[str, Any]:
        design_hash = str((design or {}).get("design_hash") or "") or None
        design_id = str((design or {}).get("design_id") or "") or None
        return {
            "schema_version": AI_DESIGN_APPROVAL_RESULT_SCHEMA_VERSION,
            "objective_id": objective_id,
            "available": False,
            "approval_status": "NOT_AVAILABLE" if design is None else PENDING,
            "decision": None,
            "approval_id": None,
            "ai_design_id": design_id,
            "ai_design_hash": design_hash,
            "source_context_hash": str((design or {}).get("input_context_hash") or (design or {}).get("source_context_hash") or "") or None,
            "source_context_id": str((design or {}).get("source_context_id") or "") or None,
            "receipt": None,
            "receipt_path": _relative(root, receipt_path) if receipt_path and receipt_path.exists() else None,
            "reason_code": None if design is None else "AI_DESIGN_APPROVAL_REQUIRED",
            "effective_state": NEED_AI_RESEARCH_DESIGN if design is None else AI_DESIGN_AWAITING_CONFIRMATION,
            "required_action": NEED_AI_RESEARCH_DESIGN if design is None else HUMAN_CONFIRM_AI_RESEARCH_DESIGN,
            "required_action_supported": False if design is None else True,
            "candidate_generation_allowed": False,
            "structural_preflight_ready": False,
            "safe_to_advance": False,
            "approval_evidence_missing": design is not None,
        }

    def evaluate(self, objective_id: str) -> dict[str, Any]:
        """Read approval evidence and return a fail-closed projection."""

        objective_id = _safe_id(objective_id, kind="objective_id")
        with self._mutex:
            receipt_path = self._receipt_path(objective_id)
            try:
                design = self._load_design(objective_id, required=False)
            except AIDesignApprovalError as exc:
                result = self._evaluation_base(objective_id, None, receipt_path, self.root)
                result.update({
                    "approval_status": INVALID,
                    "reason_code": exc.code,
                    "required_action_supported": False,
                    "approval_evidence_missing": False,
                })
                return result
            result = self._evaluation_base(objective_id, design, receipt_path, self.root)
            if design is None:
                return result
            if str(design.get("objective_id") or "") != objective_id:
                result.update({
                    "approval_status": INVALID,
                    "reason_code": "AI_DESIGN_APPROVAL_OBJECTIVE_MISMATCH",
                    "required_action_supported": False,
                })
                return result
            if str(design.get("status") or "") != AI_DESIGN_READY:
                result.update({
                    "approval_status": INVALID,
                    "reason_code": "AI_DESIGN_APPROVAL_REQUIRED",
                    "required_action_supported": False,
                })
                return result
            try:
                receipt = self._read_receipt(objective_id)
            except AIDesignApprovalError as exc:
                result.update({
                    "approval_status": INVALID,
                    "reason_code": exc.code,
                    "required_action_supported": False,
                    "approval_evidence_missing": False,
                })
                return result
            if receipt is None:
                return result
            receipt_error = self._receipt_error(receipt)
            if receipt_error is not None:
                result.update({
                    "approval_status": INVALID,
                    "reason_code": receipt_error.code,
                    "required_action_supported": False,
                })
                return result
            if str(receipt.get("objective_id") or "") != objective_id:
                result.update({
                    "approval_status": INVALID,
                    "reason_code": "AI_DESIGN_APPROVAL_OBJECTIVE_MISMATCH",
                    "required_action_supported": False,
                })
                return result
            current_design_hash = str(design.get("design_hash") or "")
            if str(receipt.get("ai_design_hash") or "") != current_design_hash or str(receipt.get("ai_design_id") or "") != str(design.get("design_id") or ""):
                result.update({
                    "approval_status": STALE,
                    "reason_code": "STALE_AI_DESIGN_APPROVAL",
                    "required_action_supported": False,
                })
                return result
            current_context_hash = str(design.get("input_context_hash") or design.get("source_context_hash") or "")
            receipt_context_hash = str(receipt.get("source_context_hash") or "")
            if current_context_hash and receipt_context_hash != current_context_hash:
                result.update({
                    "approval_status": STALE,
                    "reason_code": "STALE_AI_DESIGN_APPROVAL",
                    "required_action_supported": False,
                })
                return result
            current_context_id = str(design.get("source_context_id") or "")
            receipt_context_id = str(receipt.get("source_context_id") or "")
            if current_context_id and receipt_context_id != current_context_id:
                result.update({
                    "approval_status": STALE,
                    "reason_code": "STALE_AI_DESIGN_APPROVAL",
                    "required_action_supported": False,
                })
                return result
            design_identity_error = self._design_identity_error(design)
            if design_identity_error is not None:
                result.update({
                    "approval_status": INVALID,
                    "reason_code": design_identity_error.code,
                    "required_action_supported": False,
                })
                return result
            decision = str(receipt.get("decision") or "").upper()
            result.update({
                "available": True,
                "approval_status": decision,
                "decision": decision,
                "approval_id": receipt.get("approval_id"),
                "receipt": dict(receipt),
                "reason_code": None,
                "effective_state": AI_DESIGN_APPROVED if decision == APPROVED else AI_DESIGN_REJECTED,
                "required_action": GENERATE_CANDIDATE_PROPOSAL if decision == APPROVED else GENERATE_AI_RESEARCH_DESIGN,
                "required_action_supported": decision == APPROVED,
                "candidate_generation_allowed": decision == APPROVED,
                "safe_to_advance": decision == APPROVED,
                "approval_evidence_missing": False,
            })
            return result

    read = evaluate
    get = evaluate
    get_approval = evaluate

    def assert_candidate_generation_allowed(self, objective_id: str) -> dict[str, Any]:
        """Raise an explicit gate error unless a valid APPROVED receipt exists."""

        evaluation = self.evaluate(objective_id)
        reason_code = str(evaluation.get("reason_code") or "")
        status = str(evaluation.get("approval_status") or "")
        if status == APPROVED and evaluation.get("candidate_generation_allowed") is True:
            return evaluation
        if reason_code == "AI_DESIGN_APPROVAL_OBJECTIVE_MISMATCH":
            raise AIDesignApprovalError(reason_code, "AI 设计批准回执与当前 Objective 不匹配", details={"objective_id": objective_id})
        if reason_code == "STALE_AI_DESIGN_APPROVAL":
            raise AIDesignApprovalError(reason_code, "AI 设计已变化，原批准回执失效；请对当前设计重新审核", details={"objective_id": objective_id})
        if reason_code == "AI_DESIGN_APPROVAL_INTEGRITY_FAILURE":
            raise AIDesignApprovalError(reason_code, "AI 设计批准回执完整性校验失败，候选建议生成已阻断", status_code=503, details={"objective_id": objective_id})
        if status == REJECTED:
            raise AIDesignApprovalError("AI_DESIGN_REJECTED", "AI 研究设计已被拒绝，不能生成 Candidate Proposal", details={"objective_id": objective_id})
        raise AIDesignApprovalError("AI_DESIGN_APPROVAL_REQUIRED", "AI 研究设计尚未取得有效人工批准，不能生成 Candidate Proposal", details={"objective_id": objective_id})

    def _record(
        self,
        objective_id: str,
        *,
        decision: str,
        reviewer: Any,
        idempotency_key: Any,
        reason: Any,
        confirmed: Any,
        expected_ai_design_hash: Any,
    ) -> dict[str, Any]:
        objective_id = _safe_id(objective_id, kind="objective_id")
        if confirmed is not True:
            raise AIDesignApprovalError("CONFIRMATION_REQUIRED", "提交 AI 设计批准需要明确人工确认", status_code=400)
        decision = str(decision or "").strip().upper()
        if decision in {"APPROVE", "ACCEPT", "CONFIRM"}:
            decision = APPROVED
        elif decision in {"REJECT", "DENY"}:
            decision = REJECTED
        if decision not in {APPROVED, REJECTED}:
            raise AIDesignApprovalError("APPROVAL_DECISION_NOT_SUPPORTED", "AI 设计决策只支持 APPROVED 或 REJECTED", status_code=400)
        reviewer_value = _safe_reviewer(reviewer)
        design = self._load_design(objective_id, required=True)
        if design is None:
            raise AIDesignApprovalError("AI_DESIGN_NOT_FOUND", "当前 Objective 没有 AI 研究设计", status_code=404)
        if str(design.get("objective_id") or "") != objective_id:
            raise AIDesignApprovalError("AI_DESIGN_APPROVAL_OBJECTIVE_MISMATCH", "AI 研究设计与当前 Objective 不匹配", details={"objective_id": objective_id})
        if str(design.get("status") or "") != AI_DESIGN_READY:
            raise AIDesignApprovalError("AI_DESIGN_APPROVAL_REQUIRED", "当前 AI 研究设计不处于 AI_DESIGN_READY", details={"objective_id": objective_id})
        design_identity_error = self._design_identity_error(design)
        if design_identity_error is not None:
            raise design_identity_error
        design_hash = str(design.get("design_hash") or "")
        if expected_ai_design_hash not in (None, "") and str(expected_ai_design_hash) != design_hash:
            raise AIDesignApprovalError("STALE_AI_DESIGN_APPROVAL", "当前 AI 设计已变化，请重新读取后确认", details={"expected_ai_design_hash": str(expected_ai_design_hash), "current_ai_design_hash": design_hash})
        key = _safe_token(idempotency_key or f"AI_DESIGN_{decision}_{design_hash[:24].upper()}", kind="idempotency_key")
        receipt_path = self._receipt_path(objective_id)
        existing = self._read_receipt(objective_id)
        if existing is not None:
            existing_error = self._receipt_error(existing)
            if existing_error is not None:
                raise existing_error
            if str(existing.get("objective_id") or "") != objective_id:
                raise AIDesignApprovalError("AI_DESIGN_APPROVAL_OBJECTIVE_MISMATCH", "已有批准回执不属于当前 Objective，系统拒绝复用", details={"objective_id": objective_id})
            if str(existing.get("ai_design_hash") or "") != design_hash or str(existing.get("ai_design_id") or "") != str(design.get("design_id") or ""):
                raise AIDesignApprovalError("STALE_AI_DESIGN_APPROVAL", "已有批准回执绑定旧 AI 设计，不能复用", details={"objective_id": objective_id})
            existing_decision = str(existing.get("decision") or "").upper()
            if existing_decision == decision:
                return self._result_from_receipt(existing, idempotent=True)
            raise AIDesignApprovalError(
                "APPROVAL_IDEMPOTENCY_CONFLICT",
                "同一 AI 设计已经存在不同决策，不能覆盖原批准回执",
                details={"approval_id": existing.get("approval_id"), "existing_decision": existing_decision, "requested_decision": decision},
            )

        source_context_hash = str(design.get("input_context_hash") or design.get("source_context_hash") or "") or None
        source_context_id = str(design.get("source_context_id") or "") or None
        proposal_id = str(design.get("proposal_id") or design.get("parent_proposal_id") or "") or None
        lineage = design.get("lineage") if isinstance(design.get("lineage"), Mapping) else None
        approval_id = f"AI_DESIGN_APPROVAL_{stable_hash({'objective_id': objective_id, 'ai_design_id': design.get('design_id'), 'ai_design_hash': design_hash, 'decision': decision, 'idempotency_key': key})[:24].upper()}"
        base: dict[str, Any] = {
            "schema_version": AI_DESIGN_APPROVAL_SCHEMA_VERSION,
            "approval_id": approval_id,
            "objective_id": objective_id,
            "ai_design_id": design.get("design_id"),
            "ai_design_hash": design_hash,
        }
        if source_context_hash:
            base["source_context_hash"] = source_context_hash
        if source_context_id:
            base["source_context_id"] = source_context_id
        if proposal_id:
            base["proposal_id"] = proposal_id
        if lineage is not None:
            base["lineage"] = dict(lineage)
        base.update({
            "decision": decision,
            "reviewer": reviewer_value,
            "reviewed_at": str(self.clock()),
        })
        reason_value = str(reason or "").strip()
        if reason_value:
            base["reason"] = reason_value
        base.update({
            "idempotency_key": key,
            "resulting_state": AI_DESIGN_APPROVED if decision == APPROVED else AI_DESIGN_REJECTED,
            "next_action": GENERATE_CANDIDATE_PROPOSAL if decision == APPROVED else GENERATE_AI_RESEARCH_DESIGN,
            "candidate_generation_allowed": decision == APPROVED,
            "structural_preflight_ready": False,
            "candidate_created": False,
            "candidate_materialized": False,
            "trial_started": False,
            "ai_called": False,
            "budget_consumed": False,
            "automatic_execution": False,
        })
        try:
            PerformanceBlindGuard.assert_blind(base)
        except PerformanceLeakError as exc:
            raise AIDesignApprovalError("AI_DESIGN_APPROVAL_INTEGRITY_FAILURE", "批准回执包含结果字段，系统保持阻断", status_code=503) from exc
        base["receipt_hash"] = stable_hash(base)
        try:
            _create_immutable_json(receipt_path, base)
        except FileExistsError:
            # A concurrent writer won the one-receipt race.  Re-read it and
            # apply the same exact-once/conflict rules without overwriting.
            existing = self._read_receipt(objective_id)
            if existing is None:
                raise AIDesignApprovalError("AI_DESIGN_APPROVAL_INTEGRITY_FAILURE", "批准回执并发写入结果不可读，系统保持阻断", status_code=503)
            existing_error = self._receipt_error(existing)
            if existing_error is not None:
                raise existing_error
            if str(existing.get("ai_design_hash") or "") != design_hash:
                raise AIDesignApprovalError("STALE_AI_DESIGN_APPROVAL", "已有批准回执绑定旧 AI 设计，不能复用")
            if str(existing.get("decision") or "").upper() == decision:
                return self._result_from_receipt(existing, idempotent=True)
            raise AIDesignApprovalError("APPROVAL_IDEMPOTENCY_CONFLICT", "同一 AI 设计已经存在不同决策，不能覆盖原批准回执")
        return self._result_from_receipt(base, idempotent=False)

    def _result_from_receipt(self, receipt: Mapping[str, Any], *, idempotent: bool) -> dict[str, Any]:
        decision = str(receipt.get("decision") or "").upper()
        result = {
            "schema_version": AI_DESIGN_APPROVAL_RESULT_SCHEMA_VERSION,
            "approval_id": receipt.get("approval_id"),
            "objective_id": receipt.get("objective_id"),
            "ai_design_hash": receipt.get("ai_design_hash"),
            "source_context_id": receipt.get("source_context_id"),
            "source_context_hash": receipt.get("source_context_hash"),
            "decision": decision,
            "status": AI_DESIGN_APPROVED if decision == APPROVED else AI_DESIGN_REJECTED,
            "effective_state": AI_DESIGN_APPROVED if decision == APPROVED else AI_DESIGN_REJECTED,
            "candidate_generation_allowed": decision == APPROVED,
            "structural_preflight_ready": False,
            "receipt": dict(receipt),
            "idempotent": idempotent,
            "message_zh": "AI 研究设计已批准；现在只允许显式生成 Candidate Proposal。" if decision == APPROVED else "AI 研究设计已拒绝；请生成具有新身份和新哈希的设计后再审核。",
        }
        return result

    def confirm(
        self,
        objective_id: str,
        action: str | Mapping[str, Any] | None = None,
        reviewer: str | None = None,
        *,
        decision: str | None = None,
        idempotency_key: str | None = None,
        reason: str | None = None,
        confirmed: bool = False,
        expected_ai_design_hash: str | None = None,
    ) -> dict[str, Any]:
        body = dict(action) if isinstance(action, Mapping) else {}
        if body:
            decision = body.get("decision") or body.get("action") or decision
            reviewer = body.get("reviewer") or body.get("reviewer_id") or reviewer
            idempotency_key = body.get("idempotency_key") or body.get("approval_idempotency_key") or idempotency_key
            reason = body.get("reason") or reason
            confirmed = body.get("confirmed", confirmed)
            expected_ai_design_hash = body.get("ai_design_hash") or body.get("expected_ai_design_hash") or expected_ai_design_hash
        elif action not in (None, ""):
            decision = decision or str(action)
        return self._record(
            objective_id,
            decision=str(decision or ""),
            reviewer=reviewer,
            idempotency_key=idempotency_key,
            reason=reason,
            confirmed=confirmed,
            expected_ai_design_hash=expected_ai_design_hash,
        )

    review = confirm

    def approve(
        self,
        objective_id: str,
        reviewer: str,
        *,
        idempotency_key: str | None = None,
        reason: str | None = None,
        expected_ai_design_hash: str | None = None,
    ) -> dict[str, Any]:
        return self.confirm(
            objective_id,
            decision=APPROVED,
            reviewer=reviewer,
            idempotency_key=idempotency_key,
            reason=reason,
            confirmed=True,
            expected_ai_design_hash=expected_ai_design_hash,
        )

    def reject(
        self,
        objective_id: str,
        reviewer: str,
        *,
        idempotency_key: str | None = None,
        reason: str | None = None,
        expected_ai_design_hash: str | None = None,
    ) -> dict[str, Any]:
        return self.confirm(
            objective_id,
            decision=REJECTED,
            reviewer=reviewer,
            idempotency_key=idempotency_key,
            reason=reason,
            confirmed=True,
            expected_ai_design_hash=expected_ai_design_hash,
        )

    def recover(self, objective_id: str) -> dict[str, Any]:
        """Verify the durable receipt without writing or changing state."""

        return self.evaluate(objective_id)

    def recover_all(self) -> list[dict[str, Any]]:
        if not self.design_root.exists():
            return []
        results: list[dict[str, Any]] = []
        for directory in sorted(self.design_root.iterdir(), key=lambda item: item.name):
            if directory.is_dir() and _IDENTIFIER_RE.fullmatch(directory.name):
                results.append(self.evaluate(directory.name))
        return results


AIDesignApprovalService = AIDesignApprovalServiceV1
AIDesignApprovalManagerV1 = AIDesignApprovalServiceV1


__all__ = [
    "AI_DESIGN_APPROVED",
    "AI_DESIGN_APPROVAL_RECEIPT_FILENAME",
    "AI_DESIGN_APPROVAL_APPROVED",
    "AI_DESIGN_APPROVAL_RESULT_SCHEMA_VERSION",
    "AI_DESIGN_APPROVAL_SCHEMA_VERSION",
    "AI_DESIGN_AWAITING_CONFIRMATION",
    "AI_DESIGN_REJECTED",
    "APPROVED",
    "AI_DESIGN_APPROVAL_REJECTED",
    "AIDesignApprovalError",
    "AIDesignApprovalManagerV1",
    "AIDesignApprovalReceiptV1",
    "AIDesignApprovalService",
    "AIDesignApprovalServiceV1",
    "CANDIDATE_GENERATION_ALLOWED",
    "GENERATE_AI_RESEARCH_DESIGN",
    "GENERATE_CANDIDATE_PROPOSAL",
    "HUMAN_CONFIRM_AI_RESEARCH_DESIGN",
    "INVALID",
    "NEED_AI_RESEARCH_DESIGN",
    "PENDING",
    "REJECTED",
    "STALE",
]
