"""Human governance boundary between a research Proposal and a new Objective.

This module owns the Proposal review, the non-mutating Objective creation
preview, and the explicitly confirmed Objective creation transaction.  It is
deliberately independent from the autonomous Orchestrator: approval never
creates a Candidate, starts a Trial, invokes AI, or reserves budget.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Callable

from .budget import SearchBudgetRegistryV1
from .common import stable_hash
from .context import PerformanceBlindGuard
from .objective import RESEARCH_PRIORITY
from .research_evolution_proposal import (
    APPROVED,
    CLOSED,
    CREATED,
    CREATE_OBJECTIVE,
    HUMAN_REVIEW_REQUIRED,
    OBJECTIVE_CREATION_READY,
    PROPOSAL_FILENAME,
    READY_FOR_CONFIRMATION,
    REJECTED,
)


GOVERNANCE_DIRECTORY = "reports/research_evolution/proposals/governance"
PREVIEW_FILENAME = "objective_creation_preview.json"
REVIEWS_FILENAME = "reviews.jsonl"
RECEIPT_FILENAME = "objective_creation_receipt.json"
GOVERNANCE_RECORD_FILENAME = "objective_creation_governance.json"
TRANSACTION_DIRECTORY = "transactions"
STAGING_DIRECTORY = "reports/.research_proposal_governance_staging"
GOVERNANCE_SCHEMA_VERSION = "research-proposal-governance-flow-v1"
PREVIEW_SCHEMA_VERSION = "objective-creation-preview-v1"
REVIEW_SCHEMA_VERSION = "research-proposal-review-v1"
RECEIPT_SCHEMA_VERSION = "research-proposal-objective-creation-receipt-v1"
LINEAGE_SCHEMA_VERSION = "research-proposal-objective-lineage-v1"
BUDGET_POLICY_VERSION = "RESEARCH_PROPOSAL_GOVERNANCE_BUDGET_V1"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_REVIEWER_MAX_LENGTH = 128
_MUTABLE_PROPOSAL_FIELDS = frozenset({
    "proposal_hash",
    "proposal_identity_snapshot",
    "proposal_identity_hash",
    "proposal_content_hash",
    "governance",
    "governance_history",
    "governance_state",
    "state_history",
    "objective_creation",
    "objective_creation_preview",
    "objective_created",
    "created_objective_id",
    "creation_execution_id",
    "approval_state",
    "approval_record_id",
    "rejection_state",
    "rejection_record_id",
    "preview_available",
    "updated_at",
})

REVIEW_ACTIONS = frozenset({"approve", "reject"})
PROPOSAL_GOVERNANCE_STATES = frozenset({
    CREATED,
    HUMAN_REVIEW_REQUIRED,
    APPROVED,
    OBJECTIVE_CREATION_READY,
    REJECTED,
    CLOSED,
})
STATE_LABELS_ZH = {
    CREATED: "已创建",
    HUMAN_REVIEW_REQUIRED: "待人工审核",
    APPROVED: "已批准",
    OBJECTIVE_CREATION_READY: "待确认创建目标",
    REJECTED: "已拒绝",
    CLOSED: "已关闭",
}
STATUS_FILTERS = {
    "PENDING": frozenset({CREATED, HUMAN_REVIEW_REQUIRED}),
    "待审核": frozenset({CREATED, HUMAN_REVIEW_REQUIRED}),
    "APPROVED": frozenset({APPROVED, OBJECTIVE_CREATION_READY}),
    "已批准": frozenset({APPROVED, OBJECTIVE_CREATION_READY}),
    "REJECTED": frozenset({REJECTED, CLOSED}),
    "已拒绝": frozenset({REJECTED, CLOSED}),
}


class ResearchProposalGovernanceError(RuntimeError):
    """Safe error raised at the local Proposal governance boundary."""

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
        return {"code": self.code, "message_zh": self.message_zh, "details": dict(self.details)}


ProposalGovernanceError = ResearchProposalGovernanceError


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _read_json(path: Path, *, required: bool = True) -> Mapping[str, Any] | None:
    if not path.exists():
        if required:
            raise ResearchProposalGovernanceError("PROPOSAL_NOT_FOUND", "未找到请求的 Proposal", status_code=404)
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchProposalGovernanceError("PROPOSAL_UNREADABLE", f"Proposal 文件暂时不可读：{path.name}", status_code=503) from exc
    if not isinstance(payload, Mapping):
        raise ResearchProposalGovernanceError("PROPOSAL_INVALID", f"Proposal 文件不是 JSON 对象：{path.name}", status_code=503)
    return payload


def _safe_id(value: Any, *, kind: str) -> str:
    result = str(value or "")
    if not _IDENTIFIER_RE.fullmatch(result):
        raise ResearchProposalGovernanceError("INVALID_IDENTIFIER", f"{kind} 标识不合法", status_code=400)
    return result


def _safe_reviewer(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > _REVIEWER_MAX_LENGTH or any(ord(char) < 32 for char in result):
        raise ResearchProposalGovernanceError("REVIEWER_REQUIRED", "必须提供有效的人工审核人", status_code=400)
    return result


def _timestamp(clock: Callable[[], datetime]) -> str:
    value = clock()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _append_state(history: list[str], state: str) -> list[str]:
    if not history or history[-1] != state:
        history.append(state)
    return history


def _normalized_history(proposal: Mapping[str, Any]) -> list[str]:
    raw = proposal.get("state_history")
    history = [str(item) for item in raw] if isinstance(raw, (list, tuple)) else []
    if CREATED not in history:
        try:
            index = history.index(HUMAN_REVIEW_REQUIRED)
        except ValueError:
            history.append(CREATED)
        else:
            history.insert(index, CREATED)
    return history


def _proposal_identity_hash(proposal: Mapping[str, Any]) -> str:
    existing = str(proposal.get("proposal_hash") or "")
    snapshot = proposal.get("proposal_identity_snapshot")
    content_hash = str(proposal.get("proposal_content_hash") or "")
    if isinstance(snapshot, Mapping) and content_hash:
        if stable_hash(snapshot) != content_hash:
            raise ResearchProposalGovernanceError("PROPOSAL_HASH_INVALID", "Proposal 身份快照校验失败", status_code=503)
        current_content = {
            str(key): value
            for key, value in proposal.items()
            if str(key) not in _MUTABLE_PROPOSAL_FIELDS
        }
        if stable_hash(current_content) != content_hash:
            raise ResearchProposalGovernanceError("PROPOSAL_HASH_INVALID", "Proposal 内容与审核时身份不一致", status_code=409)
        identity_hash = str(proposal.get("proposal_identity_hash") or existing)
        if existing and identity_hash != existing:
            raise ResearchProposalGovernanceError("PROPOSAL_HASH_INVALID", "Proposal identity hash 已被修改", status_code=503)
        return identity_hash or stable_hash(current_content)

    legacy_payload = {
        str(key): value
        for key, value in proposal.items()
        if str(key) != "proposal_hash"
    }
    calculated = stable_hash(legacy_payload)
    if existing and existing != calculated:
        raise ResearchProposalGovernanceError("PROPOSAL_HASH_INVALID", "Proposal 内容与 proposal_hash 不一致", status_code=409)
    return existing or calculated


def _review_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ResearchProposalGovernanceError("GOVERNANCE_HISTORY_UNREADABLE", "Proposal 治理历史暂时不可读", status_code=503) from exc
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ResearchProposalGovernanceError("GOVERNANCE_HISTORY_INVALID", "Proposal 治理历史格式不受支持", status_code=503) from exc
        if not isinstance(payload, Mapping):
            raise ResearchProposalGovernanceError("GOVERNANCE_HISTORY_INVALID", "Proposal 治理历史格式不受支持", status_code=503)
        records.append(dict(payload))
    return records


def _merge_history(proposal: Mapping[str, Any], records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in (
        proposal.get("governance_history") if isinstance(proposal.get("governance_history"), list) else (),
        records,
    ):
        for item in source:
            if not isinstance(item, Mapping):
                continue
            key = str(item.get("review_id") or item.get("record_id") or stable_hash(item))
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(item))
    return merged


def _without_dynamic_preview_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in payload.items()
        if str(key) not in {"generated_at", "preview_hash", "confirmation_token", "objective_id"}
    }


@dataclass(frozen=True)
class ObjectiveCreationPreview:
    """Immutable, non-consuming plan shown before Objective confirmation."""

    proposal_id: str
    proposal_hash: str
    status: str
    target_objective_id: str
    target_name: str
    research_direction: str
    parent_proposal: Mapping[str, Any]
    parent_lineage: tuple[Mapping[str, Any], ...]
    mechanism_coverage: Mapping[str, Any]
    budget_suggestion: Mapping[str, Any]
    multiple_testing_family_suggestion: Mapping[str, Any]
    generated_at: str
    preview_hash: str
    confirmation_token: str
    budget_consumed: bool = False
    can_create_objective: bool = False
    requires_human_confirmation: bool = True

    def identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("generated_at", None)
        payload.pop("preview_hash", None)
        payload.pop("confirmation_token", None)
        return _jsonable(payload)

    def to_dict(self) -> dict[str, Any]:
        payload = _jsonable(asdict(self))
        payload.update({
            "schema_version": PREVIEW_SCHEMA_VERSION,
            "objective_id": self.target_objective_id,
            "preview_state": self.status,
            "automatic_objective_created": False,
            "automatic_candidate_created": False,
            "automatic_trial_started": False,
            "ai_called": False,
            "next_action": "HUMAN_CONFIRM_CREATE_OBJECTIVE",
        })
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ObjectiveCreationPreview":
        return cls(
            proposal_id=str(payload.get("proposal_id") or ""),
            proposal_hash=str(payload.get("proposal_hash") or ""),
            status=str(payload.get("status") or payload.get("preview_state") or ""),
            target_objective_id=str(payload.get("target_objective_id") or payload.get("objective_id") or ""),
            target_name=str(payload.get("target_name") or ""),
            research_direction=str(payload.get("research_direction") or ""),
            parent_proposal=dict(payload.get("parent_proposal") or {}),
            parent_lineage=tuple(dict(item) for item in (payload.get("parent_lineage") or ()) if isinstance(item, Mapping)),
            mechanism_coverage=dict(payload.get("mechanism_coverage") or {}),
            budget_suggestion=dict(payload.get("budget_suggestion") or {}),
            multiple_testing_family_suggestion=dict(payload.get("multiple_testing_family_suggestion") or {}),
            generated_at=str(payload.get("generated_at") or ""),
            preview_hash=str(payload.get("preview_hash") or ""),
            confirmation_token=str(payload.get("confirmation_token") or ""),
            budget_consumed=bool(payload.get("budget_consumed", False)),
            can_create_objective=bool(payload.get("can_create_objective", False)),
            requires_human_confirmation=bool(payload.get("requires_human_confirmation", True)),
        )


ObjectiveCreationPreviewV1 = ObjectiveCreationPreview


class ResearchProposalGovernanceServiceV1:
    """Govern a Proposal through review, preview, and explicit creation."""

    _mutex = threading.RLock()

    def __init__(self, root: str | Path, *, clock: Callable[[], datetime] | None = None, crash_at: str | None = None):
        self.root = Path(root).resolve()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.crash_at = crash_at

    def _now(self) -> str:
        return _timestamp(self.clock)

    def _proposal_root(self) -> Path:
        return self.root / "reports" / "research_evolution" / "proposals"

    def _proposal_candidates(self) -> tuple[Path, ...]:
        root = self._proposal_root()
        candidates = [root / PROPOSAL_FILENAME]
        if root.exists():
            candidates.extend(sorted(path for path in root.glob("*.json") if path.name not in {"MECHANISM_COVERAGE_REGISTRY.json"}))
        result: list[Path] = []
        seen: set[Path] = set()
        for path in candidates:
            resolved = path.resolve()
            try:
                resolved.relative_to(self.root)
            except ValueError:
                continue
            if resolved not in seen:
                result.append(resolved)
                seen.add(resolved)
        return tuple(result)

    def _proposal_path(self, proposal_id: str) -> Path:
        proposal_id = _safe_id(proposal_id, kind="proposal_id")
        matches: list[Path] = []
        for path in self._proposal_candidates():
            if not path.exists():
                continue
            payload = _read_json(path)
            if str(payload.get("proposal_id") or "") == proposal_id or path.stem == proposal_id:
                matches.append(path)
        if not matches:
            raise ResearchProposalGovernanceError("PROPOSAL_NOT_FOUND", "未找到请求的 Proposal", status_code=404)
        if len(matches) > 1:
            raise ResearchProposalGovernanceError("PROPOSAL_IDENTITY_CONFLICT", "同一 Proposal 存在多个不一致的 canonical 文件", status_code=503)
        return matches[0]

    def _load_proposal(self, proposal_id: str) -> tuple[dict[str, Any], Path]:
        path = self._proposal_path(proposal_id)
        payload = _read_json(path)
        if payload is None:
            raise ResearchProposalGovernanceError("PROPOSAL_NOT_FOUND", "未找到请求的 Proposal", status_code=404)
        actual_id = str(payload.get("proposal_id") or "")
        if actual_id != proposal_id:
            raise ResearchProposalGovernanceError("PROPOSAL_SOURCE_MISMATCH", "Proposal 身份与请求不一致", status_code=503)
        return dict(payload), path

    def _governance_dir(self, proposal_id: str) -> Path:
        path = (self.root / GOVERNANCE_DIRECTORY / _safe_id(proposal_id, kind="proposal_id")).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ResearchProposalGovernanceError("UNSAFE_PATH", "Proposal 治理目录不在项目目录内", status_code=503) from exc
        return path

    def _preview_path(self, proposal_id: str) -> Path:
        return self._governance_dir(proposal_id) / PREVIEW_FILENAME

    def _reviews_path(self, proposal_id: str) -> Path:
        return self._governance_dir(proposal_id) / REVIEWS_FILENAME

    def _receipt_path(self, proposal_id: str) -> Path:
        return self._governance_dir(proposal_id) / RECEIPT_FILENAME

    def _transaction_path(self, proposal_id: str, execution_id: str) -> Path:
        return self._governance_dir(proposal_id) / TRANSACTION_DIRECTORY / f"{_safe_id(execution_id, kind='execution_id')}.json"

    def _relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError as exc:
            raise ResearchProposalGovernanceError("UNSAFE_PATH", "治理文件路径不在项目目录内", status_code=503) from exc

    def _resolve_relative(self, value: Any, *, kind: str) -> Path:
        raw = str(value or "")
        candidate = Path(raw)
        if not raw or candidate.is_absolute() or any(part in {".", ".."} for part in candidate.parts):
            raise ResearchProposalGovernanceError("UNSAFE_PATH", f"{kind} 路径不在项目目录内", status_code=503)
        resolved = (self.root / candidate).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ResearchProposalGovernanceError("UNSAFE_PATH", f"{kind} 路径不在项目目录内", status_code=503) from exc
        return resolved

    def _proposal_view(self, proposal: Mapping[str, Any]) -> dict[str, Any]:
        proposal_id = str(proposal.get("proposal_id") or "")
        records = _review_records(self._reviews_path(proposal_id))
        history = _merge_history(proposal, records)
        state = str(proposal.get("governance_state") or HUMAN_REVIEW_REQUIRED)
        if state not in PROPOSAL_GOVERNANCE_STATES:
            raise ResearchProposalGovernanceError("PROPOSAL_STATE_INVALID", "Proposal 治理状态不受支持", status_code=503)
        view = dict(proposal)
        view["proposal_hash"] = _proposal_identity_hash(proposal)
        view["state_history"] = _normalized_history(proposal)
        view["governance_state"] = state
        view["status"] = state
        objective_created = bool(proposal.get("objective_created", False))
        view["objective_created"] = objective_created
        view["display_state"] = "OBJECTIVE_CREATED" if objective_created else state
        view["status_zh"] = "已创建新 Objective" if objective_created else STATE_LABELS_ZH.get(state, "未知状态")
        view["governance_history"] = history
        view["approval_state"] = APPROVED if any(item.get("action") == "approve" for item in history) else None
        view["preview_available"] = self._preview_path(proposal_id).exists()
        preview = _read_json(self._preview_path(proposal_id), required=False)
        if preview is not None:
            view["objective_creation_preview"] = dict(preview)
            view["preview_status"] = str(preview.get("status") or preview.get("preview_state") or "")
        next_actions = {
            CREATED: ("HUMAN_REVIEW", [APPROVED, REJECTED]),
            HUMAN_REVIEW_REQUIRED: ("HUMAN_REVIEW", [APPROVED, REJECTED]),
            APPROVED: ("OBJECTIVE_CREATION_PREVIEW", [OBJECTIVE_CREATION_READY]),
            OBJECTIVE_CREATION_READY: ("HUMAN_CONFIRM_CREATE_OBJECTIVE", [CREATE_OBJECTIVE]),
            REJECTED: ("CLOSE_PROPOSAL", [CLOSED]),
            CLOSED: ("STOPPED", []),
        }
        next_action, allowed_states = next_actions[state]
        if objective_created:
            next_action, allowed_states = "STOPPED_AFTER_OBJECTIVE_CREATION", []
        view["governance"] = {
            **dict(view.get("governance") or {}),
            "schema_version": GOVERNANCE_SCHEMA_VERSION,
            "current_state": state,
            "human_approval_required": state in {CREATED, HUMAN_REVIEW_REQUIRED},
            "review_required": state in {CREATED, HUMAN_REVIEW_REQUIRED},
            "objective_creation_ready": state == OBJECTIVE_CREATION_READY and not objective_created,
            "automatic_objective_created": False,
            "automatic_candidate_created": False,
            "automatic_trial_started": False,
            "ai_called": False,
            "budget_consumed": False,
            "next_action": next_action,
            "next_allowed_states": allowed_states,
        }
        view["governance_flow"] = {
            "schema_version": GOVERNANCE_SCHEMA_VERSION,
            "current_state": state,
            "state_labels_zh": dict(STATE_LABELS_ZH),
            "review_actions": sorted(REVIEW_ACTIONS),
            "requires_human_confirmation": state == OBJECTIVE_CREATION_READY and not objective_created,
            "automatic_execution": False,
        }
        return view

    @staticmethod
    def _status_matches(state: str, status: str | None) -> bool:
        if not status or str(status).upper() in {"ALL", "全部"}:
            return True
        accepted = STATUS_FILTERS.get(str(status).upper()) or STATUS_FILTERS.get(str(status))
        return accepted is not None and state in accepted

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        """Read one Proposal without changing its state or any ledger."""
        with self._mutex:
            proposal, _ = self._load_proposal(proposal_id)
            result = self._proposal_view(proposal)
            PerformanceBlindGuard.assert_blind(result)
            return result

    read_proposal = get_proposal

    def list_proposals(self, *, objective_id: str | None = None, status: str | None = None) -> dict[str, Any]:
        """List Proposal summaries for the status-filtered Console view."""
        with self._mutex:
            if objective_id:
                objective_id = _safe_id(objective_id, kind="objective_id")
            items: list[dict[str, Any]] = []
            for path in self._proposal_candidates():
                if not path.exists():
                    continue
                payload = _read_json(path)
                proposal_id = str(payload.get("proposal_id") or "")
                if not proposal_id:
                    continue
                view = self._proposal_view(payload)
                if objective_id and str(view.get("parent_objective_id") or "") != objective_id:
                    continue
                state = str(view.get("governance_state") or "")
                if not self._status_matches(state, status):
                    continue
                item = {
                    "proposal_id": proposal_id,
                    "proposal_hash": view["proposal_hash"],
                    "parent_objective_id": view.get("parent_objective_id"),
                    "source_failure_report": view.get("source_failure_report"),
                    "failed_mechanism": view.get("failed_mechanism"),
                    "suggested_research_directions": list(view.get("suggested_research_directions") or ()),
                    "governance_state": state,
                    "status": state,
                    "status_zh": view.get("status_zh"),
                    "approval_state": view.get("approval_state"),
                    "preview_available": bool(view.get("preview_available")),
                    "objective_created": bool(view.get("objective_created", False)),
                    "created_objective_id": view.get("created_objective_id"),
                    "governance_history": list(view.get("governance_history") or ()),
                    "updated_at": view.get("updated_at") or view.get("generated_at"),
                }
                PerformanceBlindGuard.assert_blind(item)
                items.append(item)
            items.sort(key=lambda item: (str(item.get("updated_at") or ""), item["proposal_id"]), reverse=True)
            return {
                "schema_version": "research-proposal-governance-list-v1",
                "objective_id": objective_id,
                "status_filter": status or "ALL",
                "items": items,
                "total": len(items),
                "status_options": [
                    {"value": "PENDING", "label_zh": "待审核"},
                    {"value": "APPROVED", "label_zh": "已批准"},
                    {"value": "REJECTED", "label_zh": "已拒绝"},
                ],
                "automatic_execution": False,
            }

    list = list_proposals

    def _append_review_record(self, proposal_id: str, record: Mapping[str, Any]) -> bool:
        path = self._reviews_path(proposal_id)
        records = _review_records(path)
        review_id = str(record.get("review_id") or record.get("record_id") or "")
        for existing in records:
            existing_id = str(existing.get("review_id") or existing.get("record_id") or "")
            if existing_id != review_id:
                continue
            if dict(existing) != dict(record):
                raise ResearchProposalGovernanceError("REVIEW_ID_CONFLICT", "review_id 已绑定到不同的审核记录", status_code=409)
            return False
        records.append(dict(record))
        content = "".join(json.dumps(_jsonable(item), ensure_ascii=False, sort_keys=True) + "\n" for item in records)
        _atomic_write_text(path, content)
        return True

    @staticmethod
    def _last_action(history: list[Mapping[str, Any]], action: str) -> dict[str, Any] | None:
        for item in reversed(history):
            if str(item.get("action") or "") == action:
                return dict(item)
        return None

    def _safe_direction(self, proposal: Mapping[str, Any], selected: Any = None) -> str:
        directions = [str(item).strip() for item in (proposal.get("suggested_research_directions") or ()) if str(item).strip()]
        if not directions:
            raise ResearchProposalGovernanceError("RESEARCH_DIRECTION_MISSING", "Proposal 没有可供治理确认的研究方向", status_code=503)
        if selected not in (None, ""):
            value = str(selected).strip()
            if value not in directions:
                raise ResearchProposalGovernanceError("RESEARCH_DIRECTION_NOT_ALLOWED", "所选研究方向不在 Proposal 建议范围内", status_code=400)
            return value
        return directions[0]

    @staticmethod
    def _safe_coverage(proposal: Mapping[str, Any]) -> dict[str, Any]:
        source = proposal.get("mechanism_coverage") if isinstance(proposal.get("mechanism_coverage"), Mapping) else {}
        result = {
            "coverage_id": str(source.get("coverage_id") or "MECHANISM_COVERAGE_REGISTRY_V1"),
            "covered": [str(item) for item in (source.get("covered") or ()) if str(item)],
            "unexplored": [str(item) for item in (source.get("unexplored") or ()) if str(item)],
            "failed_mechanism_family": str(proposal.get("failed_mechanism_family") or ""),
            "avoided_families": [str(item) for item in (proposal.get("avoid_mechanism_family") or ()) if str(item)],
            "outcome_blind": True,
        }
        PerformanceBlindGuard.assert_blind(result)
        return result

    @staticmethod
    def _safe_parent_lineage(proposal: Mapping[str, Any], proposal_hash: str) -> list[dict[str, Any]]:
        proposal_id = str(proposal.get("proposal_id") or "")
        parent_objective_id = str(proposal.get("parent_objective_id") or "")
        lineage: list[dict[str, Any]] = [{
            "relation": "GENERATED_FROM_PROPOSAL",
            "parent_type": "ResearchProposal",
            "parent_id": proposal_id,
            "parent_hash": proposal_hash,
            "immutable": True,
        }]
        if parent_objective_id:
            lineage.append({
                "relation": "PARENT_OBJECTIVE",
                "parent_type": "Objective",
                "parent_id": parent_objective_id,
                "parent_hash": str((proposal.get("lineage") or {}).get("objective_hash") or ""),
                "immutable": True,
            })
        source_lineage = proposal.get("lineage") if isinstance(proposal.get("lineage"), Mapping) else {}
        for key, parent_type, relation in (
            ("candidate_id", "Candidate", "PARENT_CANDIDATE"),
            ("trial_id", "Trial", "PARENT_TRIAL"),
        ):
            parent_id = str(source_lineage.get(key) or proposal.get(f"parent_{key}") or "")
            if not parent_id:
                continue
            lineage.append({
                "relation": relation,
                "parent_type": parent_type,
                "parent_id": parent_id,
                "parent_hash": str(source_lineage.get(f"{key[:-3]}_hash") or ""),
                "immutable": True,
            })
        PerformanceBlindGuard.assert_blind(lineage)
        return lineage

    def _build_preview(self, proposal: Mapping[str, Any], *, direction: Any = None, generated_at: str | None = None) -> dict[str, Any]:
        proposal_id = _safe_id(proposal.get("proposal_id"), kind="proposal_id")
        proposal_hash = _proposal_identity_hash(proposal)
        research_direction = self._safe_direction(proposal, direction)
        coverage = self._safe_coverage(proposal)
        parent_lineage = self._safe_parent_lineage(proposal, proposal_hash)
        budget = {
            "policy_version": BUDGET_POLICY_VERSION,
            "proposed_total_predictive_budget": 4,
            "candidate_slots": 1,
            "preregistered_family_count": 1,
            "expected_structural_screen_allowance": 1,
            "budget_consumed": False,
            "reservation_created": False,
            "outcome_derived_expansion": False,
            "reason_zh": "预算是待确认的新 Objective 的预注册上限；Preview 阶段不创建预算登记、不预留、不消耗。",
        }
        family_identity = {
            "proposal_id": proposal_id,
            "proposal_hash": proposal_hash,
            "research_direction": research_direction,
            "mechanism_coverage": coverage,
            "budget_policy_version": budget["policy_version"],
        }
        family_id = f"MTF_PROPOSAL_EVOLUTION_V1_{stable_hash(family_identity)[:20].upper()}"
        family = {
            "schema_version": "research-multiple-testing-family-v1",
            "family_id": family_id,
            "family_scope": "由本 Proposal 确认的新 Objective 的全部预注册候选",
            "adjustment_method": "BENJAMINI_HOCHBERG",
            "q": 0.05,
            "hypothesis_slots": 1,
            "pre_registered_before_predictive_results": True,
            "immutable_after_confirmation": True,
            "relationship_to_parent": "NEW_INDEPENDENT_FAMILY_WITH_PROPOSAL_LINEAGE",
        }
        objective_identity = {
            "proposal_id": proposal_id,
            "proposal_hash": proposal_hash,
            "research_direction": research_direction,
            "parent_lineage": parent_lineage,
            "mechanism_coverage": coverage,
            "budget_suggestion": budget,
            "family_id": family_id,
        }
        objective_identity_hash = stable_hash(objective_identity)
        target_objective_id = f"RESEARCH_OBJECTIVE_EVOLUTION_V1_{objective_identity_hash[:24].upper()}"
        preview_base = {
            "schema_version": PREVIEW_SCHEMA_VERSION,
            "governance_schema_version": GOVERNANCE_SCHEMA_VERSION,
            "proposal_id": proposal_id,
            "proposal_hash": proposal_hash,
            "status": READY_FOR_CONFIRMATION,
            "target_objective_id": target_objective_id,
            "target_name": f"研究演进 · {research_direction}",
            "research_direction": research_direction,
            "parent_proposal": {
                "proposal_id": proposal_id,
                "proposal_hash": proposal_hash,
                "parent_objective_id": str(proposal.get("parent_objective_id") or ""),
                "source_failure_report": str(proposal.get("source_failure_report") or ""),
                "immutable": True,
            },
            "parent_lineage": parent_lineage,
            "mechanism_coverage": coverage,
            "budget_suggestion": budget,
            "multiple_testing_family_suggestion": family,
            "objective_identity_hash": objective_identity_hash,
            "budget_consumed": False,
            "can_create_objective": False,
            "requires_human_confirmation": True,
            "automatic_objective_created": False,
            "automatic_candidate_created": False,
            "automatic_trial_started": False,
            "ai_called": False,
            "final_test_access": "DISABLED",
            "prospective_access": "DISABLED",
            "real_order_execution": "DISABLED",
            "next_action": "HUMAN_CONFIRM_CREATE_OBJECTIVE",
        }
        preview_hash = stable_hash(preview_base)
        payload = {
            **preview_base,
            "generated_at": generated_at or self._now(),
            "preview_hash": preview_hash,
            "confirmation_token": stable_hash({
                "proposal_id": proposal_id,
                "proposal_hash": proposal_hash,
                "preview_hash": preview_hash,
                "contract": "OBJECTIVE_CREATION_CONFIRMATION_V1",
            })[:40],
        }
        PerformanceBlindGuard.assert_blind(payload)
        return payload

    def _load_preview(self, proposal_id: str) -> dict[str, Any]:
        payload = _read_json(self._preview_path(proposal_id), required=False)
        if payload is None:
            raise ResearchProposalGovernanceError("OBJECTIVE_PREVIEW_NOT_FOUND", "当前 Proposal 尚未生成 Objective Creation Preview", status_code=404)
        preview = dict(payload)
        expected_hash = stable_hash(_without_dynamic_preview_fields(preview))
        if str(preview.get("preview_hash") or "") != expected_hash:
            raise ResearchProposalGovernanceError("OBJECTIVE_PREVIEW_HASH_INVALID", "Objective Creation Preview 校验失败", status_code=503)
        if str(preview.get("status") or preview.get("preview_state") or "") != READY_FOR_CONFIRMATION:
            raise ResearchProposalGovernanceError("OBJECTIVE_PREVIEW_NOT_READY", "Objective Creation Preview 尚未进入确认状态", status_code=409)
        if preview.get("budget_consumed") is not False or (preview.get("budget_suggestion") or {}).get("budget_consumed") is not False:
            raise ResearchProposalGovernanceError("OBJECTIVE_PREVIEW_BUDGET_MUTATED", "Objective Creation Preview 已出现预算消费标记", status_code=503)
        PerformanceBlindGuard.assert_blind(preview)
        return preview

    def _write_preview_if_same(self, proposal_id: str, preview: Mapping[str, Any]) -> None:
        path = self._preview_path(proposal_id)
        existing = _read_json(path, required=False)
        if existing is not None:
            if dict(existing) != dict(preview):
                raise ResearchProposalGovernanceError("OBJECTIVE_PREVIEW_CONFLICT", "同一 Proposal 已存在不一致的 Objective Creation Preview", status_code=503)
            return
        _atomic_write(path, preview)

    def _materialize_review(
        self,
        proposal: Mapping[str, Any],
        record: Mapping[str, Any],
        *,
        resulting_state: str,
        preview: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        updated = dict(proposal)
        history = _normalized_history(proposal)
        if record.get("action") == "approve":
            _append_state(history, APPROVED)
        _append_state(history, resulting_state)
        existing_records = _merge_history(proposal, [])
        if not any(str(item.get("review_id") or item.get("record_id") or "") == str(record.get("review_id") or record.get("record_id") or "") for item in existing_records):
            existing_records.append(dict(record))
        updated["proposal_hash"] = _proposal_identity_hash(proposal)
        content_snapshot = proposal.get("proposal_identity_snapshot")
        if not isinstance(content_snapshot, Mapping):
            content_snapshot = {
                str(key): value
                for key, value in proposal.items()
                if str(key) not in _MUTABLE_PROPOSAL_FIELDS
            }
        updated["proposal_identity_snapshot"] = dict(content_snapshot)
        updated["proposal_content_hash"] = stable_hash(content_snapshot)
        updated["proposal_identity_hash"] = updated["proposal_hash"]
        updated["state_history"] = history
        updated["governance_state"] = resulting_state
        updated["governance_history"] = existing_records
        updated["updated_at"] = self._now()
        governance = dict(proposal.get("governance") or {})
        governance.update({
            "schema_version": GOVERNANCE_SCHEMA_VERSION,
            "current_state": resulting_state,
            "human_approval_required": False,
            "review_required": False,
            "automatic_objective_created": False,
            "automatic_candidate_created": False,
            "automatic_trial_started": False,
            "ai_called": False,
            "budget_consumed": False,
            "objective_creation_ready": resulting_state == OBJECTIVE_CREATION_READY,
            "next_action": "HUMAN_CONFIRM_CREATE_OBJECTIVE" if resulting_state == OBJECTIVE_CREATION_READY else "CLOSED",
            "next_allowed_states": ["CREATE_OBJECTIVE"] if resulting_state == OBJECTIVE_CREATION_READY else [CLOSED],
        })
        updated["governance"] = governance
        if record.get("action") == "approve":
            updated["approval_state"] = APPROVED
            updated["approval_record_id"] = record.get("review_id")
        else:
            updated["rejection_state"] = REJECTED
            updated["rejection_record_id"] = record.get("review_id")
        if preview is not None:
            updated["objective_creation_preview"] = dict(preview)
            updated["preview_available"] = True
        return updated

    def review(
        self,
        proposal_id: str,
        action: str | Mapping[str, Any],
        reviewer: str | None = None,
        *,
        review_id: str | None = None,
        expected_proposal_hash: str | None = None,
        research_direction: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Apply one human approve/reject decision and persist its evidence."""
        with self._mutex:
            body: dict[str, Any] = dict(action) if isinstance(action, Mapping) else {}
            if body:
                action = str(body.get("action") or body.get("decision") or "")
                reviewer = body.get("reviewer") or body.get("reviewer_id") or reviewer
                review_id = body.get("review_id") or review_id
                expected_proposal_hash = body.get("proposal_hash") or body.get("expected_proposal_hash") or expected_proposal_hash
                research_direction = body.get("research_direction") or body.get("selected_research_direction") or research_direction
                reason = body.get("reason") or reason
            normalized_action = str(action or "").strip().casefold()
            if normalized_action not in REVIEW_ACTIONS:
                raise ResearchProposalGovernanceError("REVIEW_ACTION_NOT_SUPPORTED", "审核动作只支持 approve 或 reject", status_code=400)
            reviewer_value = _safe_reviewer(reviewer)
            proposal, proposal_path = self._load_proposal(proposal_id)
            proposal_id = str(proposal.get("proposal_id") or "")
            proposal_hash = _proposal_identity_hash(proposal)
            if expected_proposal_hash not in (None, "") and str(expected_proposal_hash) != proposal_hash:
                raise ResearchProposalGovernanceError("STALE_PROPOSAL", "Proposal 已变化，请重新读取后再审核", status_code=409)
            current_state = str(proposal.get("governance_state") or HUMAN_REVIEW_REQUIRED)
            history = _merge_history(proposal, _review_records(self._reviews_path(proposal_id)))
            existing_action = self._last_action(history, normalized_action)
            if current_state in {APPROVED, OBJECTIVE_CREATION_READY} and normalized_action == "approve":
                preview = self._load_preview(proposal_id)
                return {
                    "schema_version": REVIEW_SCHEMA_VERSION,
                    "review": existing_action or {"action": "approve", "proposal_hash": proposal_hash},
                    "proposal": self._proposal_view(proposal),
                    "objective_creation_preview": preview,
                    "idempotent": True,
                    "message_zh": "Proposal 已批准，重复审核未创建新的研究目标。",
                }
            if current_state in {REJECTED, CLOSED} and normalized_action == "reject":
                return {
                    "schema_version": REVIEW_SCHEMA_VERSION,
                    "review": existing_action or {"action": "reject", "proposal_hash": proposal_hash},
                    "proposal": self._proposal_view(proposal),
                    "objective_creation_preview": None,
                    "idempotent": True,
                    "message_zh": "Proposal 已拒绝，重复审核未改变治理历史。",
                }
            if current_state == CREATED:
                state_history = _normalized_history(proposal)
                _append_state(state_history, HUMAN_REVIEW_REQUIRED)
                proposal["state_history"] = state_history
                proposal["governance_state"] = HUMAN_REVIEW_REQUIRED
                current_state = HUMAN_REVIEW_REQUIRED
            if current_state != HUMAN_REVIEW_REQUIRED:
                raise ResearchProposalGovernanceError("INVALID_PROPOSAL_STATE_TRANSITION", f"Proposal 当前状态 {current_state} 不允许审核", status_code=409)
            resolved_direction = self._safe_direction(proposal, research_direction) if normalized_action == "approve" else None
            if existing_action is not None:
                if review_id not in (None, "") and str(review_id) != str(existing_action.get("review_id") or ""):
                    raise ResearchProposalGovernanceError("REVIEW_IN_PROGRESS", "当前 Proposal 已存在另一条未完成的审核事务", status_code=409)
                record = dict(existing_action)
                added = False
            else:
                generated_review_id = f"REVIEW_{stable_hash({'proposal_id': proposal_id, 'proposal_hash': proposal_hash, 'action': normalized_action, 'reviewer': reviewer_value, 'research_direction': resolved_direction or ''})[:24].upper()}"
                review_id_value = _safe_id(review_id or generated_review_id, kind="review_id")
                record = {
                    "schema_version": REVIEW_SCHEMA_VERSION,
                    "review_id": review_id_value,
                    "proposal_id": proposal_id,
                    "action": normalized_action,
                    "reviewer": reviewer_value,
                    "timestamp": self._now(),
                    "proposal_hash": proposal_hash,
                    "previous_state": current_state,
                    "resulting_state": APPROVED if normalized_action == "approve" else REJECTED,
                    "next_state": OBJECTIVE_CREATION_READY if normalized_action == "approve" else CLOSED,
                    "reason": str(reason) if reason not in (None, "") else None,
                }
                if resolved_direction is not None:
                    record["research_direction"] = resolved_direction
                record = {key: value for key, value in record.items() if value is not None}
                added = self._append_review_record(proposal_id, record)
            if self.crash_at == "after_review_record":
                raise RuntimeError("SYNTHETIC_PROPOSAL_GOVERNANCE_CRASH:after_review_record")
            if normalized_action == "reject":
                updated = self._materialize_review(proposal, record, resulting_state=REJECTED)
                _atomic_write(proposal_path, updated)
                if self.crash_at == "after_proposal_review":
                    raise RuntimeError("SYNTHETIC_PROPOSAL_GOVERNANCE_CRASH:after_proposal_review")
                return {
                    "schema_version": REVIEW_SCHEMA_VERSION,
                    "review": record,
                    "proposal": self._proposal_view(updated),
                    "objective_creation_preview": None,
                    "idempotent": not added,
                    "message_zh": "Proposal 已拒绝并保留原有研究历史；未创建 Objective。",
                }
            preview = self._build_preview(
                proposal,
                direction=record.get("research_direction"),
                generated_at=str(record.get("timestamp") or "") or None,
            )
            self._write_preview_if_same(proposal_id, preview)
            if self.crash_at == "after_preview":
                raise RuntimeError("SYNTHETIC_PROPOSAL_GOVERNANCE_CRASH:after_preview")
            updated = self._materialize_review(proposal, record, resulting_state=OBJECTIVE_CREATION_READY, preview=preview)
            _atomic_write(proposal_path, updated)
            if self.crash_at == "after_proposal_review":
                raise RuntimeError("SYNTHETIC_PROPOSAL_GOVERNANCE_CRASH:after_proposal_review")
            return {
                "schema_version": REVIEW_SCHEMA_VERSION,
                "review": record,
                "proposal": self._proposal_view(updated),
                "objective_creation_preview": preview,
                "idempotent": not added,
                "message_zh": "Proposal 已批准；Objective Creation Preview 已生成，等待第二次人工确认。",
            }

    review_proposal = review

    def preview(self, proposal_id: str) -> dict[str, Any]:
        """Return the already generated preview; it never creates budget state."""
        with self._mutex:
            proposal, _ = self._load_proposal(proposal_id)
            state = str(proposal.get("governance_state") or HUMAN_REVIEW_REQUIRED)
            if state not in {APPROVED, OBJECTIVE_CREATION_READY}:
                raise ResearchProposalGovernanceError("PROPOSAL_NOT_APPROVED", "Proposal 尚未通过人工审核，不能查看 Objective Creation Preview", status_code=409)
            return self._load_preview(str(proposal.get("proposal_id") or ""))

    get_preview = preview
    get_objective_creation_preview = preview

    def close(self, proposal_id: str, *, reviewer: str, reason: str | None = None) -> dict[str, Any]:
        """Close a rejected Proposal without creating any downstream artifact."""
        with self._mutex:
            proposal, proposal_path = self._load_proposal(proposal_id)
            current_state = str(proposal.get("governance_state") or HUMAN_REVIEW_REQUIRED)
            if current_state == CLOSED:
                return self._proposal_view(proposal)
            if current_state != REJECTED:
                raise ResearchProposalGovernanceError("INVALID_PROPOSAL_STATE_TRANSITION", "只有 REJECTED Proposal 可以关闭", status_code=409)
            record = {
                "schema_version": REVIEW_SCHEMA_VERSION,
                "record_id": f"CLOSE_{stable_hash({'proposal_id': proposal_id, 'proposal_hash': _proposal_identity_hash(proposal)})[:24].upper()}",
                "proposal_id": str(proposal.get("proposal_id") or ""),
                "action": "close",
                "reviewer": _safe_reviewer(reviewer),
                "timestamp": self._now(),
                "proposal_hash": _proposal_identity_hash(proposal),
                "previous_state": REJECTED,
                "resulting_state": CLOSED,
                "reason": str(reason) if reason not in (None, "") else None,
            }
            record = {key: value for key, value in record.items() if value is not None}
            self._append_review_record(str(proposal.get("proposal_id") or ""), record)
            updated = self._materialize_review(proposal, record, resulting_state=CLOSED)
            _atomic_write(proposal_path, updated)
            return self._proposal_view(updated)

    def _parent_objective(self, proposal: Mapping[str, Any]) -> dict[str, Any]:
        parent_id = str(proposal.get("parent_objective_id") or "")
        if not parent_id:
            return {}
        parent_id = _safe_id(parent_id, kind="parent_objective_id")
        path = self._resolve_relative(
            f"data/research/research_factory/objectives/{parent_id}.json",
            kind="parent objective",
        )
        payload = _read_json(path, required=False)
        if payload is None:
            return {}
        if str(payload.get("objective_id") or "") != parent_id:
            raise ResearchProposalGovernanceError("PARENT_OBJECTIVE_MISMATCH", "Proposal 的 parent objective 身份校验失败", status_code=503)
        return dict(payload)

    def _objective_payload(self, preview: Mapping[str, Any], *, created_at: str) -> dict[str, Any]:
        parent = self._parent_objective({"parent_objective_id": (preview.get("parent_proposal") or {}).get("parent_objective_id")})
        risk = deepcopy(dict(parent.get("risk_constraints") or {}))
        risk.update({
            "final_test_access": "DISABLED",
            "prospective_access": "DISABLED",
            "recommendation": "DISABLED",
            "real_order_execution": "DISABLED",
        })
        budget = dict(preview.get("budget_suggestion") or {})
        family = dict(preview.get("multiple_testing_family_suggestion") or {})
        family_id = _safe_id(family.get("family_id"), kind="multiple_testing_family_id")
        target_id = _safe_id(preview.get("target_objective_id") or preview.get("objective_id"), kind="objective_id")
        objective_identity_hash = str(preview.get("objective_identity_hash") or "")
        direction = str(preview.get("research_direction") or "")
        return {
            "schema_version": "research-objective-v1",
            "objective_id": target_id,
            "objective_name": str(preview.get("target_name") or f"研究演进 · {direction}"),
            "created_at": created_at,
            "lifecycle_state": "CREATED",
            "activation_policy": "MANUAL_ONLY",
            "activation_authorized": False,
            "parent_objective_id": str((preview.get("parent_proposal") or {}).get("parent_objective_id") or ""),
            "parent_proposal_id": str(preview.get("proposal_id") or ""),
            "parent_proposal_hash": str(preview.get("proposal_hash") or ""),
            "objective_identity_hash": objective_identity_hash,
            "immutable_objective_id": True,
            "parent_lineage": list(preview.get("parent_lineage") or ()),
            "research_direction": direction,
            "research_universe": list(parent.get("research_universe") or ["SH", "SZ"]),
            "capital_reference": float(parent.get("capital_reference", 10000.0)),
            "holding_horizon": list(parent.get("holding_horizon") or [2, 10]),
            "preferred_horizon": list(parent.get("preferred_horizon") or [5, 8]),
            "mechanism_scope": [direction],
            "allowed_factor_scope": list(parent.get("allowed_factor_scope") or []),
            "risk_constraints": risk,
            "research_priority": list(parent.get("research_priority") or RESEARCH_PRIORITY),
            "max_batches": 1,
            "max_total_trials": int(budget.get("proposed_total_predictive_budget", 0) or 0),
            "seed": int(parent.get("seed", 20260824)),
            "governance_policy_hash": str(parent.get("governance_policy_hash") or stable_hash({"objective_id": target_id, "policy": "MANUAL_ONLY"})),
            "research_scope_version": "RESEARCH_PROPOSAL_GOVERNANCE_FLOW_V1",
            "budget_policy_version": str(budget.get("policy_version") or BUDGET_POLICY_VERSION),
            "multiple_testing_family_id": family_id,
            "candidate_eligibility_policy": {
                "frozen_contract_required": True,
                "performance_blind_design": True,
                "candidate_generation_policy": "PREREGISTERED",
            },
            "final_test_access": {"analytical": 0, "decision": 0, "physical": 0},
            "prospective_access": "DISABLED",
            "real_order_execution": "DISABLED",
            "ai_invocation": "DISABLED",
            "next_action": "HUMAN_REVIEW_REQUIRED",
        }

    def _prepare_transaction(
        self,
        proposal: Mapping[str, Any],
        preview: Mapping[str, Any],
        execution_id: str,
        idempotency_key: str,
        confirmer: str,
        proposal_path: Path,
    ) -> dict[str, Any]:
        proposal_id = str(proposal.get("proposal_id") or "")
        transaction_path = self._transaction_path(proposal_id, execution_id)
        receipt_path = self._receipt_path(proposal_id)
        staging_root = self.root / STAGING_DIRECTORY / execution_id
        files: list[dict[str, Any]] = []

        def stage_json(relative_target: str, payload: Any, checkpoint: str, *, replace: bool = False) -> None:
            target_path = self._resolve_relative(relative_target, kind="治理目标")
            safe_target = self._relative(target_path)
            staged = staging_root / safe_target
            _atomic_write(staged, payload)
            files.append({
                "target": safe_target,
                "staged": self._relative(staged),
                "sha256": hashlib.sha256(staged.read_bytes()).hexdigest(),
                "checkpoint": checkpoint,
                "replace": replace,
            })

        created_at = self._now()
        target_objective_id = _safe_id(preview.get("target_objective_id") or preview.get("objective_id"), kind="objective_id")
        batch_id = f"{target_objective_id}_B01"
        family = dict(preview.get("multiple_testing_family_suggestion") or {})
        family_id = _safe_id(family.get("family_id"), kind="multiple_testing_family_id")
        budget_suggestion = dict(preview.get("budget_suggestion") or {})
        proposal_hash = str(preview.get("proposal_hash") or "")
        preview_hash = str(preview.get("preview_hash") or "")
        approval_review_id = str(next((item.get("review_id") for item in reversed(_merge_history(proposal, _review_records(self._reviews_path(proposal_id)))) if item.get("action") == "approve"), ""))
        if not approval_review_id:
            raise ResearchProposalGovernanceError("APPROVAL_RECORD_MISSING", "Objective 创建缺少可追溯的批准审核记录", status_code=503)
        confirmation_id = f"CONFIRMATION_{stable_hash({'proposal_id': proposal_id, 'preview_hash': preview_hash})[:24].upper()}"

        stage_json(
            f"data/research/research_factory/objectives/{target_objective_id}.json",
            self._objective_payload(preview, created_at=created_at),
            "after_objective_write",
        )

        budget_stage = staging_root / f"data/research/research_factory/batches/{batch_id}/search_budget_registry.json"
        budget_registry = SearchBudgetRegistryV1(target_objective_id, budget_stage)
        total_budget = int(budget_suggestion.get("proposed_total_predictive_budget", 0) or 0)
        family_slots = int(budget_suggestion.get("candidate_slots", 0) or 0)
        if total_budget <= 0 or family_slots <= 0:
            raise ResearchProposalGovernanceError("BUDGET_SUGGESTION_INVALID", "Preview 的预算建议不合法", status_code=503)
        budget_registry.register_objective(total_budget)
        budget_registry.register_batch(batch_id, total_budget)
        budget_registry.register_family(family_id, family_slots)
        files.append({
            "target": f"data/research/research_factory/batches/{batch_id}/search_budget_registry.json",
            "staged": self._relative(budget_stage),
            "sha256": hashlib.sha256(budget_stage.read_bytes()).hexdigest(),
            "checkpoint": "after_budget_registry_write",
            "replace": False,
        })

        stage_json(
            f"data/research/research_factory/multiple_testing/{target_objective_id}/{family_id}.json",
            {**family, "objective_id": target_objective_id, "created_at": created_at, "status": "PREREGISTERED"},
            "after_family_write",
        )

        lineage = {
            "schema_version": LINEAGE_SCHEMA_VERSION,
            "lineage_id": f"LINEAGE_{stable_hash({'proposal_id': proposal_id, 'preview_hash': preview_hash})[:24].upper()}",
            "lineage_status": "IMMUTABLE",
            "objective_id": target_objective_id,
            "proposal_id": proposal_id,
            "proposal_hash": proposal_hash,
            "parent_objective_id": str((preview.get("parent_proposal") or {}).get("parent_objective_id") or ""),
            "parent_lineage": list(preview.get("parent_lineage") or ()),
            "research_direction": str(preview.get("research_direction") or ""),
            "immutable": True,
            "created_at": created_at,
        }
        stage_json(f"data/research/research_factory/lineage/{target_objective_id}.json", lineage, "after_lineage_write")

        governance_base = {
            "schema_version": "research-proposal-objective-creation-governance-v1",
            "record_id": f"GOVERNANCE_CREATE_OBJECTIVE_{stable_hash({'proposal_id': proposal_id, 'preview_hash': preview_hash})[:24].upper()}",
            "action": CREATE_OBJECTIVE,
            "proposal_id": proposal_id,
            "proposal_hash": proposal_hash,
            "preview_hash": preview_hash,
            "approval_review_id": approval_review_id,
            "confirmation_id": confirmation_id,
            "confirmer": confirmer,
            "confirmed_at": created_at,
            "objective_id": target_objective_id,
            "objective_identity_hash": str(preview.get("objective_identity_hash") or ""),
            "lineage_ref": f"data/research/research_factory/lineage/{target_objective_id}.json",
            "budget_registry_ref": f"data/research/research_factory/batches/{batch_id}/search_budget_registry.json",
            "multiple_testing_family_id": family_id,
            "status": "OBJECTIVE_CREATED",
            "immutable_objective_id": True,
            "automatic_execution": False,
            "candidate_created": False,
            "trial_started": False,
            "ai_called": False,
            "budget_consumed": False,
            "final_test_access": "DISABLED",
            "prospective_access": "DISABLED",
            "real_order_execution": "DISABLED",
        }
        governance_record = {**governance_base, "record_hash": stable_hash(governance_base)}
        governance_relative = f"{GOVERNANCE_DIRECTORY}/{proposal_id}/{GOVERNANCE_RECORD_FILENAME}"
        stage_json(governance_relative, governance_record, "after_governance_record_write")

        receipt = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "execution_id": execution_id,
            "action": CREATE_OBJECTIVE,
            "proposal_id": proposal_id,
            "proposal_hash": proposal_hash,
            "preview_hash": preview_hash,
            "approval_review_id": approval_review_id,
            "confirmation_id": confirmation_id,
            "confirmer": confirmer,
            "confirmed_at": created_at,
            "objective_id": target_objective_id,
            "new_objective_id": target_objective_id,
            "objective_identity_hash": str(preview.get("objective_identity_hash") or ""),
            "lineage_ref": f"data/research/research_factory/lineage/{target_objective_id}.json",
            "budget_registry_ref": f"data/research/research_factory/batches/{batch_id}/search_budget_registry.json",
            "multiple_testing_family_ref": f"data/research/research_factory/multiple_testing/{target_objective_id}/{family_id}.json",
            "governance_record_ref": governance_relative,
            "created_at": created_at,
            "result_state": "OBJECTIVE_CREATED",
            "idempotency_key": idempotency_key,
            "immutable_objective_id": True,
            "automatic_execution": False,
            "candidate_created": False,
            "trial_started": False,
            "ai_called": False,
            "budget_consumed": False,
            "message_zh": "已按人工确认创建新 Objective；系统停在人工研究设计边界，未自动启动研究。",
        }
        updated_proposal = dict(proposal)
        updated_proposal["governance_state"] = OBJECTIVE_CREATION_READY
        updated_proposal["objective_created"] = True
        updated_proposal["created_objective_id"] = target_objective_id
        updated_proposal["creation_execution_id"] = execution_id
        updated_proposal["objective_creation"] = {
            "status": "OBJECTIVE_CREATED",
            "action": CREATE_OBJECTIVE,
            "confirmation_id": confirmation_id,
            "approval_review_id": approval_review_id,
            "confirmer": confirmer,
            "objective_id": target_objective_id,
            "execution_id": execution_id,
            "governance_record_ref": governance_relative,
            "budget_consumed": False,
            "automatic_execution": False,
            "next_action": "STOPPED_AFTER_OBJECTIVE_CREATION",
        }
        updated_proposal["governance"] = {
            **dict(proposal.get("governance") or {}),
            "schema_version": GOVERNANCE_SCHEMA_VERSION,
            "current_state": OBJECTIVE_CREATION_READY,
            "objective_creation_ready": True,
            "objective_created": True,
            "automatic_objective_created": False,
            "automatic_candidate_created": False,
            "automatic_trial_started": False,
            "ai_called": False,
            "budget_consumed": False,
            "next_action": "STOPPED_AFTER_OBJECTIVE_CREATION",
        }
        updated_proposal["governance_history"] = [*_merge_history(proposal, []), governance_record]
        stage_json(
            self._relative(proposal_path),
            updated_proposal,
            "after_proposal_creation_record_write",
            replace=True,
        )

        receipt_stage = staging_root / "receipt.json"
        _atomic_write(receipt_stage, receipt)
        journal = {
            "schema_version": "research-proposal-objective-creation-transaction-v1",
            "status": "PREPARED",
            "execution_id": execution_id,
            "action": CREATE_OBJECTIVE,
            "proposal_id": proposal_id,
            "proposal_hash": proposal_hash,
            "preview_hash": preview_hash,
            "files": files,
            "receipt": {
                "target": self._relative(receipt_path),
                "staged": self._relative(receipt_stage),
                "sha256": hashlib.sha256(receipt_stage.read_bytes()).hexdigest(),
                "checkpoint": "after_receipt_write",
                "replace": False,
            },
            "receipt_payload": receipt,
            "created_at": created_at,
        }
        _atomic_write(transaction_path, journal)
        return journal

    def _verify_or_move(self, entry: Mapping[str, Any]) -> None:
        target = self._resolve_relative(entry.get("target"), kind="治理目标")
        staged = self._resolve_relative(entry.get("staged"), kind="治理暂存")
        expected = str(entry.get("sha256") or "")
        if target.exists():
            try:
                current_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            except OSError as exc:
                raise ResearchProposalGovernanceError("GOVERNANCE_TARGET_UNREADABLE", f"治理目标文件暂时不可读：{target.name}", status_code=503) from exc
            if current_hash == expected:
                return
            if not entry.get("replace"):
                raise ResearchProposalGovernanceError("GOVERNANCE_RECOVERY_CONFLICT", f"治理恢复发现目标文件冲突：{entry.get('target')}", status_code=503)
        if not staged.exists():
            raise ResearchProposalGovernanceError("GOVERNANCE_STAGING_MISSING", f"治理恢复缺少暂存文件：{entry.get('staged')}", status_code=503)
        try:
            staged_hash = hashlib.sha256(staged.read_bytes()).hexdigest()
        except OSError as exc:
            raise ResearchProposalGovernanceError("GOVERNANCE_STAGING_UNREADABLE", f"治理暂存文件暂时不可读：{entry.get('staged')}", status_code=503) from exc
        if staged_hash != expected:
            raise ResearchProposalGovernanceError("GOVERNANCE_STAGING_HASH_MISMATCH", f"治理暂存文件校验失败：{entry.get('staged')}", status_code=503)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, target)

    def _commit_transaction(self, transaction_path: Path, journal: Mapping[str, Any]) -> dict[str, Any]:
        current = dict(journal)
        current["status"] = "COMMITTING"
        _atomic_write(transaction_path, current)
        for entry in current.get("files", ()):
            self._verify_or_move(entry)
            if self.crash_at == entry.get("checkpoint"):
                raise RuntimeError(f"SYNTHETIC_PROPOSAL_GOVERNANCE_CRASH:{entry.get('checkpoint')}")
        receipt = current["receipt"]
        self._verify_or_move(receipt)
        if self.crash_at == "after_receipt_write":
            raise RuntimeError("SYNTHETIC_PROPOSAL_GOVERNANCE_CRASH:after_receipt_write")
        current["status"] = "COMPLETED"
        current["completed_at"] = self._now()
        _atomic_write(transaction_path, current)
        return dict(current["receipt_payload"])

    def _recover_transaction(self, transaction_path: Path, journal: Mapping[str, Any]) -> dict[str, Any]:
        receipt_entry = journal.get("receipt") if isinstance(journal.get("receipt"), Mapping) else {}
        receipt_target = self._resolve_relative(receipt_entry.get("target"), kind="治理回执")
        if receipt_target.exists():
            for entry in journal.get("files", ()):
                self._verify_or_move(entry)
            payload = _read_json(receipt_target)
            if payload is None:
                raise ResearchProposalGovernanceError("GOVERNANCE_RECEIPT_UNREADABLE", "Objective 创建回执不可读", status_code=503)
            completed = dict(journal)
            completed["status"] = "COMPLETED"
            completed["completed_at"] = completed.get("completed_at") or self._now()
            _atomic_write(transaction_path, completed)
            return dict(payload)
        return self._commit_transaction(transaction_path, journal)

    def _execution_id(self, proposal_id: str, preview: Mapping[str, Any]) -> str:
        return f"CREATE_OBJECTIVE_{stable_hash({'proposal_id': proposal_id, 'proposal_hash': preview.get('proposal_hash'), 'preview_hash': preview.get('preview_hash')})[:24].upper()}"

    def confirm(self, proposal_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Create exactly one Objective after the explicit second confirmation."""
        with self._mutex:
            body = dict(payload or {})
            if body.get("confirmed") is not True:
                raise ResearchProposalGovernanceError("CONFIRMATION_REQUIRED", "创建新 Objective 需要明确的第二次人工确认", status_code=400)
            proposal, proposal_path = self._load_proposal(proposal_id)
            proposal_id = str(proposal.get("proposal_id") or "")
            if str(proposal.get("governance_state") or "") != OBJECTIVE_CREATION_READY:
                raise ResearchProposalGovernanceError("OBJECTIVE_CREATION_NOT_READY", "当前 Proposal 尚未进入 Objective Creation Preview 确认状态", status_code=409)
            preview = self._load_preview(proposal_id)
            proposal_hash = _proposal_identity_hash(proposal)
            if str(preview.get("proposal_hash") or "") != proposal_hash:
                raise ResearchProposalGovernanceError("STALE_PROPOSAL", "Proposal 与 Preview 的 hash 不一致，请重新审核", status_code=409)
            expected_preview_hash = str(body.get("preview_hash") or "")
            expected_token = str(body.get("confirmation_token") or "")
            if expected_preview_hash != str(preview.get("preview_hash") or "") or expected_token != str(preview.get("confirmation_token") or ""):
                raise ResearchProposalGovernanceError("STALE_OBJECTIVE_PREVIEW", "Objective Creation Preview 已变化，请重新查看方案", status_code=409)
            if str(body.get("proposal_hash") or "") != proposal_hash:
                raise ResearchProposalGovernanceError("STALE_PROPOSAL", "Proposal hash 与当前版本不一致", status_code=409)
            action = str(body.get("action") or body.get("governance_action") or CREATE_OBJECTIVE).upper()
            if action != CREATE_OBJECTIVE:
                raise ResearchProposalGovernanceError("CREATE_ACTION_REQUIRED", "最终确认动作必须是 CREATE_OBJECTIVE", status_code=400)
            confirmer = _safe_reviewer(body.get("confirmer") or body.get("confirm_reviewer") or body.get("reviewer"))
            idempotency_key = _safe_id(body.get("idempotency_key") or f"CONFIRM_{preview['preview_hash'][:24].upper()}", kind="idempotency_key")
            execution_id = self._execution_id(proposal_id, preview)
            receipt_path = self._receipt_path(proposal_id)
            existing_receipt = _read_json(receipt_path, required=False)
            if existing_receipt is not None:
                if str(existing_receipt.get("preview_hash") or "") != str(preview.get("preview_hash") or ""):
                    raise ResearchProposalGovernanceError("GOVERNANCE_RECEIPT_CONFLICT", "已有 Objective 创建回执与当前 Preview 冲突", status_code=503)
                return {**dict(existing_receipt), "idempotent": True, "message_zh": "Objective 已创建，重复确认未创建重复目标。"}
            transaction_path = self._transaction_path(proposal_id, execution_id)
            existing_journal = _read_json(transaction_path, required=False)
            if existing_journal is not None:
                if str(existing_journal.get("preview_hash") or "") != str(preview.get("preview_hash") or ""):
                    raise ResearchProposalGovernanceError("GOVERNANCE_EXECUTION_CONFLICT", "同一 Proposal 已存在不一致的创建事务", status_code=503)
                receipt = self._recover_transaction(transaction_path, existing_journal)
                return {**receipt, "idempotent": True, "message_zh": "已从确认事务恢复 Objective 创建，未创建重复目标。"}
            target_objective_id = _safe_id(preview.get("target_objective_id") or preview.get("objective_id"), kind="objective_id")
            target_path = self._resolve_relative(
                f"data/research/research_factory/objectives/{target_objective_id}.json",
                kind="objective",
            )
            if target_path.exists():
                raise ResearchProposalGovernanceError("OBJECTIVE_ID_COLLISION", "待创建的 immutable objective id 已被其他对象占用", status_code=503)
            journal = self._prepare_transaction(proposal, preview, execution_id, idempotency_key, confirmer, proposal_path)
            receipt = self._commit_transaction(transaction_path, journal)
            return {**receipt, "idempotent": False}

    confirm_objective_creation = confirm
    create_objective_after_confirmation = confirm

    def get_execution(self, proposal_id: str, execution_id: str) -> dict[str, Any]:
        path = self._receipt_path(proposal_id)
        payload = _read_json(path, required=False)
        if payload is None or str(payload.get("execution_id") or "") != _safe_id(execution_id, kind="execution_id"):
            raise ResearchProposalGovernanceError("GOVERNANCE_EXECUTION_NOT_FOUND", "未找到 Objective 创建回执", status_code=404)
        return dict(payload)

    def recover(self, proposal_id: str, execution_id: str | None = None) -> dict[str, Any]:
        """Recover a transaction that was already confirmed before a restart."""
        with self._mutex:
            proposal_id = _safe_id(proposal_id, kind="proposal_id")
            if execution_id:
                transaction_paths = [self._transaction_path(proposal_id, execution_id)]
            else:
                transaction_dir = self._governance_dir(proposal_id) / TRANSACTION_DIRECTORY
                transaction_paths = sorted(transaction_dir.glob("*.json")) if transaction_dir.exists() else []
            if not transaction_paths:
                raise ResearchProposalGovernanceError("GOVERNANCE_TRANSACTION_NOT_FOUND", "未找到待恢复的 Objective 创建事务", status_code=404)
            for path in transaction_paths:
                journal = _read_json(path)
                if journal is None:
                    continue
                if str(journal.get("status") or "") == "COMPLETED" and isinstance(journal.get("receipt_payload"), Mapping):
                    return {**dict(journal["receipt_payload"]), "idempotent": True}
                receipt = self._recover_transaction(path, journal)
                return {**receipt, "idempotent": True, "message_zh": "Objective 创建事务已恢复完成，未创建重复目标。"}
            raise ResearchProposalGovernanceError("GOVERNANCE_TRANSACTION_NOT_FOUND", "未找到待恢复的 Objective 创建事务", status_code=404)

    def recover_all(self) -> list[dict[str, Any]]:
        """Recover only transactions that already contain an explicit confirm journal."""
        recovered: list[dict[str, Any]] = []
        root = self.root / GOVERNANCE_DIRECTORY
        if not root.exists():
            return recovered
        with self._mutex:
            for transaction_path in sorted(root.glob(f"*/{TRANSACTION_DIRECTORY}/*.json")):
                proposal_id = transaction_path.parent.parent.name
                try:
                    recovered.append(self.recover(proposal_id, transaction_path.stem))
                except ResearchProposalGovernanceError:
                    continue
        return recovered


ResearchProposalGovernanceFlowV1 = ResearchProposalGovernanceServiceV1
ProposalGovernanceServiceV1 = ResearchProposalGovernanceServiceV1


__all__ = [
    "APPROVED",
    "BUDGET_POLICY_VERSION",
    "CLOSED",
    "CREATED",
    "CREATE_OBJECTIVE",
    "GOVERNANCE_DIRECTORY",
    "GOVERNANCE_RECORD_FILENAME",
    "HUMAN_REVIEW_REQUIRED",
    "OBJECTIVE_CREATION_READY",
    "ObjectiveCreationPreview",
    "ObjectiveCreationPreviewV1",
    "PREVIEW_FILENAME",
    "PREVIEW_SCHEMA_VERSION",
    "ProposalGovernanceError",
    "ProposalGovernanceServiceV1",
    "READY_FOR_CONFIRMATION",
    "REJECTED",
    "RECEIPT_FILENAME",
    "ResearchProposalGovernanceError",
    "ResearchProposalGovernanceFlowV1",
    "ResearchProposalGovernanceServiceV1",
    "REVIEW_ACTIONS",
    "REVIEW_SCHEMA_VERSION",
    "STATE_LABELS_ZH",
]
