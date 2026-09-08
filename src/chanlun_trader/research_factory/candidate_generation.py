"""Outcome-blind Candidate Proposal generation and governance boundary.

This module converts an already generated AI research design into a
human-reviewable Candidate Proposal.  A proposal is a design artifact, not a
registered Candidate: generation never touches the Candidate registry, a
frozen contract, Structural Preflight, a Trial, an AI backend or a budget
ledger.
"""
from __future__ import annotations

from .mutation_boundary import mutation_boundary

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import argparse
import json
import os
from pathlib import Path
import re
import threading
from typing import Any

from .common import jsonable, now_timestamp, stable_hash
from .context import PerformanceBlindGuard, PerformanceLeakError
from .ai_design_approval import (
    AIDesignApprovalError,
    AIDesignApprovalServiceV1,
    AI_DESIGN_APPROVAL_RECEIPT_FILENAME,
)
from .research_evolution_ai_design import (
    AI_DESIGN_READY,
    AI_RESEARCH_DESIGN_FILENAME,
    AI_RESEARCH_DESIGN_INPUT_FILENAME,
)
from .research_evolution_proposal import MechanismCoverageRegistryV1
from .research_proposal_governance import (
    ResearchProposalGovernanceError,
    ResearchProposalGovernanceServiceV1,
)


GENERATED = "GENERATED"
CANDIDATE_PROPOSAL_READY = "CANDIDATE_PROPOSAL_READY"
HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
APPROVED = "APPROVED"
FREEZE_PREVIEW_READY = "FREEZE_PREVIEW_READY"
# Backwards-compatible public name retained for callers of Candidate Generation
# Governance V1.  The persisted V1 state now uses the clearer preview state.
CANDIDATE_FREEZE_READY = FREEZE_PREVIEW_READY
_LEGACY_CANDIDATE_FREEZE_READY = "CANDIDATE_FREEZE_READY"
FROZEN = "FROZEN"
CANDIDATE_GOVERNANCE_FROZEN = "CANDIDATE_GOVERNANCE_FROZEN"
EXECUTABLE_CANDIDATE_FROZEN = "EXECUTABLE_CANDIDATE_FROZEN"
READY_FOR_STRUCTURAL_PREFLIGHT = "READY_FOR_STRUCTURAL_PREFLIGHT"
NEW_CANDIDATE = "NEW_CANDIDATE"
REJECTED = "REJECTED"
CLOSED = "CLOSED"
DUPLICATE_MECHANISM_REJECTED = "DUPLICATE_MECHANISM_REJECTED"
NEED_CANDIDATE_PROPOSAL = "NEED_CANDIDATE_PROPOSAL"
HUMAN_CONFIRM_CANDIDATE_FREEZE = "HUMAN_CONFIRM_CANDIDATE_FREEZE"
CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW = "CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW"
HUMAN_CONFIRM_EXECUTABLE_MATERIALIZATION = "HUMAN_CONFIRM_EXECUTABLE_MATERIALIZATION"
RUN_STRUCTURAL_PREFLIGHT = "RUN_STRUCTURAL_PREFLIGHT"

CANDIDATE_PROPOSAL_FILENAME = "CANDIDATE_PROPOSAL.json"
CANDIDATE_PROPOSAL_INPUT_FILENAME = "CANDIDATE_PROPOSAL_INPUT.json"
CANDIDATE_PROPOSAL_STATE_FILENAME = "CANDIDATE_PROPOSAL_STATE.json"
CANDIDATE_FREEZE_PREVIEW_FILENAME = "CANDIDATE_FREEZE_PREVIEW.json"
CANDIDATE_FREEZE_GOVERNANCE_FILENAME = "CANDIDATE_FREEZE_GOVERNANCE.json"
CANDIDATE_FREEZE_RECEIPT_FILENAME = "CANDIDATE_FREEZE_RECEIPT.json"
CANDIDATE_REGISTRY_FILENAME = "CANDIDATE_REGISTRY.json"
CANDIDATE_REVIEWS_FILENAME = "reviews.jsonl"

CANDIDATE_PROPOSAL_SCHEMA_VERSION = "candidate-proposal-governance-v1"
CANDIDATE_PROPOSAL_INPUT_SCHEMA_VERSION = "candidate-proposal-input-v1"
CANDIDATE_PROPOSAL_STATE_SCHEMA_VERSION = "candidate-proposal-state-v1"
CANDIDATE_FREEZE_PREVIEW_SCHEMA_VERSION = "candidate-freeze-preview-v1"
CANDIDATE_FREEZE_SCHEMA_VERSION = "candidate-review-freeze-v1"
CANDIDATE_REGISTRY_SCHEMA_VERSION = "candidate-registry-v1"
CANDIDATE_REVIEW_SCHEMA_VERSION = "candidate-proposal-review-v1"
CANDIDATE_PROPOSAL_VIEW_SCHEMA_VERSION = "candidate-proposal-view-v1"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_MACHINE_TOKEN_RE = re.compile(r"[^A-Za-z0-9]+")
_REVIEWER_MAX_LENGTH = 128
_SAFE_RISK_KEYS = (
    "pit_required",
    "no_lookahead",
    "t_plus_1",
    "price_limit_fail_closed",
    "suspension_fail_closed",
)
_OUTCOME_FACTOR_TOKENS = (
    "return",
    "pnl",
    "win_rate",
    "drawdown",
    "sharpe",
    "p_value",
    "pvalue",
    "nav",
    "performance",
    "outcome",
    "profit",
    "收益",
    "胜率",
    "回撤",
    "净值",
)
_LOCAL_OUTCOME_TOKENS = (
    "收益",
    "收益率",
    "胜率",
    "回撤",
    "p-value",
    "p value",
    "单笔交易",
    "历史绩效",
    "历史表现",
    "历史净值",
)
_OUTCOME_FIELD_KEYS = frozenset({
    "return",
    "returns",
    "net_return",
    "exact_return",
    "win_rate",
    "drawdown",
    "p_value",
    "pvalue",
    "sharpe",
    "pnl",
    "performance",
    "historical_performance",
    "stock_list",
    "symbols",
    "universe",
})
_MECHANISM_ALIASES = {
    "liquidity_acceleration": "liquidity_acceleration",
    "amount_acceleration": "liquidity_acceleration",
    "amount_accel": "liquidity_acceleration",
    "volume_acceleration": "liquidity_acceleration",
    "volume_accel": "liquidity_acceleration",
    "volume_participation": "liquidity_acceleration",
    "daily_cross_sectional": "daily_cross_sectional",
}


class CandidateGenerationError(RuntimeError):
    """Fail-closed error at the Candidate Proposal boundary."""

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


def _safe_id(value: Any, *, kind: str) -> str:
    result = str(value or "")
    if not _IDENTIFIER_RE.fullmatch(result):
        raise CandidateGenerationError("INVALID_IDENTIFIER", f"{kind} 标识不合法", status_code=400)
    return result


def _read_json(path: Path, *, code: str, required: bool = True) -> Mapping[str, Any] | None:
    if not path.exists():
        if required:
            raise CandidateGenerationError(code, f"缺少必要研究资料：{path.name}", status_code=404)
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CandidateGenerationError("SOURCE_UNREADABLE", f"研究资料暂时不可读：{path.name}", status_code=503) from exc
    if not isinstance(payload, Mapping):
        raise CandidateGenerationError("SOURCE_INVALID", f"研究资料不是 JSON 对象：{path.name}", status_code=503)
    return payload


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(
        json.dumps(jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(jsonable(payload), ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _strings(value: Any) -> list[str]:
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple, set, frozenset)):
        return []
    result: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _machine_token(value: Any) -> str:
    return _MACHINE_TOKEN_RE.sub("_", str(value or "").strip().casefold()).strip("_")


def _normalise_mechanism(value: Any) -> str:
    token = _machine_token(value)
    if not token:
        return ""
    for alias, canonical in _MECHANISM_ALIASES.items():
        if token == alias or token.startswith(f"{alias}_v"):
            return canonical
    if any(part in token for part in ("liquidity", "illiq", "amount_accel", "volume_accel", "volume_participation")):
        return "liquidity_acceleration"
    return token


def _is_outcome_factor(value: Any) -> bool:
    token = _machine_token(value)
    raw = str(value or "").strip().casefold()
    return any(part in token for part in _OUTCOME_FACTOR_TOKENS) or any(part in raw for part in _LOCAL_OUTCOME_TOKENS)


def _assert_outcome_blind(value: Any) -> None:
    try:
        PerformanceBlindGuard.assert_blind(value)

        def visit(item: Any) -> None:
            if isinstance(item, Mapping):
                for key, nested in item.items():
                    raw = str(key).strip().casefold()
                    normalized = _machine_token(key)
                    if normalized in _OUTCOME_FIELD_KEYS or any(part in raw for part in _LOCAL_OUTCOME_TOKENS):
                        raise PerformanceLeakError(f"outcome-bearing field is forbidden: {key}")
                    visit(nested)
            elif isinstance(item, (list, tuple, set, frozenset)):
                for nested in item:
                    visit(nested)

        visit(value)
    except PerformanceLeakError:
        raise


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise CandidateGenerationError("UNSAFE_PATH", "候选建议资料路径不在项目目录内", status_code=503) from exc


def _safe_lineage(payload: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "schema_version",
        "lineage_id",
        "lineage_status",
        "objective_id",
        "parent_objective_id",
        "proposal_id",
        "proposal_hash",
        "research_direction",
        "immutable",
        "created_at",
    )
    result = {key: payload[key] for key in keys if key in payload}
    result["parent_lineage"] = []
    for item in payload.get("parent_lineage") or ():
        if not isinstance(item, Mapping):
            continue
        result["parent_lineage"].append({
            key: item[key]
            for key in ("relation", "parent_type", "parent_id", "parent_hash", "immutable")
            if key in item
        })
    return result


def _safe_capabilities(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = payload or {}
    datasets: list[dict[str, Any]] = []
    for item in source.get("datasets") or ():
        if not isinstance(item, Mapping):
            continue
        safe: dict[str, Any] = {}
        for key in (
            "dataset_id",
            "source",
            "provider",
            "fields",
            "frequency",
            "earliest_date",
            "latest_date",
            "event_time_semantics",
            "available_at_semantics",
            "PIT_safe",
            "data_version",
            "status",
        ):
            if key not in item:
                continue
            if key == "fields":
                safe[key] = _strings(item[key])
            elif isinstance(item[key], (str, int, float, bool)) or item[key] is None:
                safe[key] = item[key]
        if safe.get("dataset_id"):
            datasets.append(safe)
    return {
        "schema_version": "data-capability-safe-view-v1",
        "generated_at": str(source.get("generated_at") or "") or None,
        "datasets": datasets,
        "ready_dataset_ids": [item["dataset_id"] for item in datasets if item.get("status") == "READY"],
        "online_only_dataset_ids": [item["dataset_id"] for item in datasets if item.get("status") == "ONLINE_ONLY"],
    }


def _safe_proposal(payload: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in (
        "proposal_id",
        "proposal_hash",
        "parent_objective_id",
        "created_objective_id",
        "failed_mechanism",
        "failed_mechanism_family",
        "failure_summary",
        "avoid_mechanism_family",
        "avoid_mechanisms",
        "suggested_research_directions",
        "outcome_blind",
        "objective_created",
    ):
        if key in payload:
            result[key] = payload[key]
    for key in ("failure_summary", "avoid_mechanism_family", "avoid_mechanisms", "suggested_research_directions"):
        result[key] = _strings(result.get(key))
    return result


def _safe_coverage(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": str(payload.get("schema_version") or "mechanism-coverage-registry-v1"),
        "coverage_id": str(payload.get("coverage_id") or "MECHANISM_COVERAGE_REGISTRY_V1"),
        "covered": _strings(payload.get("covered")),
        "unexplored": _strings(payload.get("unexplored")),
        "read_only": True,
        "outcome_blind": True,
    }


def _safe_objective(payload: Mapping[str, Any], lineage: Mapping[str, Any]) -> dict[str, Any]:
    objective_id = str(payload.get("objective_id") or "")
    preferred = payload.get("preferred_horizon")
    if not isinstance(preferred, (list, tuple)):
        preferred = payload.get("holding_horizon")
    horizons = [int(item) for item in preferred or () if isinstance(item, (int, float)) and not isinstance(item, bool)]
    if not horizons:
        horizons = [5]
    risk_source = payload.get("risk_constraints") if isinstance(payload.get("risk_constraints"), Mapping) else {}
    risk = {key: risk_source[key] for key in _SAFE_RISK_KEYS if key in risk_source}
    budget = {
        "max_total_trials": int(payload.get("max_total_trials") or 0),
        "max_batches": int(payload.get("max_batches") or 0),
        "budget_policy_version": str(payload.get("budget_policy_version") or ""),
    }
    result = {
        "objective_id": objective_id,
        "objective_name": str(payload.get("objective_name") or ""),
        "parent_objective_id": str(payload.get("parent_objective_id") or ""),
        "parent_proposal_id": str(payload.get("parent_proposal_id") or ""),
        "parent_proposal_hash": str(payload.get("parent_proposal_hash") or ""),
        "research_direction": str(payload.get("research_direction") or ""),
        "mechanism_scope": _strings(payload.get("mechanism_scope")),
        "preferred_horizon": horizons,
        "risk_constraints": risk,
        "budget": budget,
        "multiple_testing_family_id": str(payload.get("multiple_testing_family_id") or ""),
        "lineage_id": str(lineage.get("lineage_id") or ""),
    }
    return result


@dataclass(frozen=True)
class CandidateGenerationInputV1:
    """Safe, complete context used to derive a Candidate Proposal."""

    objective_id: str
    ai_research_design: Mapping[str, Any]
    research_evolution_proposal: Mapping[str, Any]
    objective: Mapping[str, Any]
    objective_lineage: Mapping[str, Any]
    mechanism_coverage_registry: Mapping[str, Any]
    available_data_capabilities: Mapping[str, Any] = field(default_factory=dict)
    source_refs: Mapping[str, str] = field(default_factory=dict)
    source_hashes: Mapping[str, str] = field(default_factory=dict)
    source_context_id: str = ""
    source_context_hash: str = ""
    source_budget_authority_status: str = "MISSING"
    runtime_budget: Mapping[str, Any] = field(default_factory=dict)
    input_context_hash: str = ""
    ai_design_approval_id: str = ""

    def __post_init__(self) -> None:
        for name in (
            "ai_research_design",
            "research_evolution_proposal",
            "objective",
            "objective_lineage",
            "mechanism_coverage_registry",
            "available_data_capabilities",
            "source_refs",
            "source_hashes",
            "runtime_budget",
        ):
            object.__setattr__(self, name, jsonable(getattr(self, name)))
        base = self._payload(include_hash=False)
        try:
            _assert_outcome_blind(base)
        except PerformanceLeakError as exc:
            raise CandidateGenerationError("OUTCOME_FIELD_BLOCKED", "候选建议输入包含被禁止的结果字段", status_code=503) from exc
        if not self.input_context_hash:
            object.__setattr__(self, "input_context_hash", stable_hash(base))

    def _payload(self, *, include_hash: bool) -> dict[str, Any]:
        payload = {
            "schema_version": CANDIDATE_PROPOSAL_INPUT_SCHEMA_VERSION,
            "objective_id": self.objective_id,
            "ai_research_design": self.ai_research_design,
            "research_evolution_proposal": self.research_evolution_proposal,
            "objective": self.objective,
            "objective_lineage": self.objective_lineage,
            "mechanism_coverage_registry": self.mechanism_coverage_registry,
            "available_data_capabilities": self.available_data_capabilities,
            "source_refs": self.source_refs,
            "source_hashes": self.source_hashes,
            "source_context_id": self.source_context_id,
            "source_context_hash": self.source_context_hash,
            "source_budget_authority_status": self.source_budget_authority_status,
            "runtime_budget": self.runtime_budget,
            "ai_design_approval_id": self.ai_design_approval_id,
            "outcome_blind": True,
            "performance_data_loaded": False,
            "outcome_fields_available": False,
        }
        if include_hash:
            payload["input_context_hash"] = self.input_context_hash
        return payload

    def to_dict(self) -> dict[str, Any]:
        return jsonable(self._payload(include_hash=True))


@dataclass(frozen=True)
class CandidateFreezePreview:
    """Immutable, non-consuming plan shown before Candidate Freeze."""

    candidate_id: str
    candidate_hash: str
    objective_id: str
    proposal_id: str
    proposal_hash: str
    research_lineage: Mapping[str, Any]
    mechanism_family: str
    factor_contract: tuple[str, ...]
    execution_contract: Mapping[str, Any]
    data_contract: Mapping[str, Any]
    multiple_testing_family: str
    generated_at: str
    preview_hash: str
    status: str = FREEZE_PREVIEW_READY
    requires_human_confirmation: bool = True
    candidate_name: str | None = None
    budget: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CANDIDATE_FREEZE_PREVIEW_SCHEMA_VERSION,
            "preview_id": f"CANDIDATE_FREEZE_PREVIEW_{self.candidate_hash[:24].upper()}",
            "status": self.status,
            "objective_id": self.objective_id,
            "proposal_id": self.proposal_id,
            "proposal_hash": self.proposal_hash,
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "candidate_identity_status": "PREVIEW_ONLY_NOT_REGISTERED",
            "candidate_name": self.candidate_name,
            "mechanism_family": self.mechanism_family,
            "factor_contract": list(self.factor_contract),
            "execution_contract": jsonable(self.execution_contract),
            "data_contract": jsonable(self.data_contract),
            "multiple_testing_family_id": self.multiple_testing_family,
            "budget": jsonable(self.budget),
            "lineage": jsonable(self.research_lineage),
            "research_lineage": jsonable(self.research_lineage),
            "requires_human_confirmation": self.requires_human_confirmation,
            "next_action": HUMAN_CONFIRM_CANDIDATE_FREEZE,
            "candidate_created": False,
            "candidate_frozen": False,
            "structural_preflight_started": False,
            "trial_started": False,
            "ai_called": False,
            "budget_consumed": False,
            "generated_at": self.generated_at,
            "preview_hash": self.preview_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CandidateFreezePreview":
        return cls(
            candidate_id=str(payload.get("candidate_id") or ""),
            candidate_hash=str(payload.get("candidate_hash") or ""),
            objective_id=str(payload.get("objective_id") or ""),
            proposal_id=str(payload.get("proposal_id") or ""),
            proposal_hash=str(payload.get("proposal_hash") or ""),
            research_lineage=dict(payload.get("research_lineage") or payload.get("lineage") or {}),
            mechanism_family=str(payload.get("mechanism_family") or ""),
            factor_contract=tuple(str(item) for item in payload.get("factor_contract") or ()),
            execution_contract=dict(payload.get("execution_contract") or {}),
            data_contract=dict(payload.get("data_contract") or {}),
            multiple_testing_family=str(payload.get("multiple_testing_family_id") or payload.get("multiple_testing_family") or ""),
            generated_at=str(payload.get("generated_at") or ""),
            preview_hash=str(payload.get("preview_hash") or ""),
            status=str(payload.get("status") or FREEZE_PREVIEW_READY),
            requires_human_confirmation=bool(payload.get("requires_human_confirmation", True)),
            candidate_name=str(payload.get("candidate_name") or "") or None,
            budget=dict(payload.get("budget") or {}) if isinstance(payload.get("budget"), Mapping) else None,
        )


CandidateFreezePreviewV1 = CandidateFreezePreview


class CandidateGenerationManagerV1:
    """Generate and govern one result-blind Candidate Proposal per Objective."""

    _mutex = threading.RLock()

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], str] = now_timestamp,
        crash_at: str | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.proposal_root = self.root / "reports" / "research_candidates" / "proposals"
        self.clock = clock
        self.crash_at = crash_at
        self.ai_design_approval = AIDesignApprovalServiceV1(self.root)
        self.ai_design_approval_service = self.ai_design_approval
        from .safe_runtime_context import SafeRuntimeContextBuilderV1

        self.safe_runtime_context_builder = SafeRuntimeContextBuilderV1(self.root, clock=clock)
        self.safe_context_builder = self.safe_runtime_context_builder

    def _objective_path(self, objective_id: str) -> Path:
        return self.root / "data" / "research" / "research_factory" / "objectives" / f"{_safe_id(objective_id, kind='objective_id')}.json"

    def _lineage_path(self, objective_id: str) -> Path:
        return self.root / "data" / "research" / "research_factory" / "lineage" / f"{_safe_id(objective_id, kind='objective_id')}.json"

    def _candidate_registry_path(self, objective_id: str) -> Path:
        objective_id = _safe_id(objective_id, kind="objective_id")
        path = (self.root / "data" / "research" / "research_factory" / "candidates" / objective_id / CANDIDATE_REGISTRY_FILENAME).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise CandidateGenerationError("UNSAFE_PATH", "Candidate Registry 路径不在项目目录内", status_code=503) from exc
        return path

    def _proposal_dir(self, objective_id: str) -> Path:
        path = (self.proposal_root / _safe_id(objective_id, kind="objective_id")).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise CandidateGenerationError("UNSAFE_PATH", "候选建议目录不在项目目录内", status_code=503) from exc
        return path

    def _paths(self, objective_id: str) -> tuple[Path, Path, Path, Path, Path]:
        directory = self._proposal_dir(objective_id)
        return (
            directory / CANDIDATE_PROPOSAL_INPUT_FILENAME,
            directory / CANDIDATE_PROPOSAL_FILENAME,
            directory / CANDIDATE_PROPOSAL_STATE_FILENAME,
            directory / CANDIDATE_FREEZE_PREVIEW_FILENAME,
            directory / CANDIDATE_REVIEWS_FILENAME,
        )

    def _now(self) -> str:
        value = self.clock()
        return str(value)

    def _find_proposal_path(self, proposal_id: str) -> Path:
        proposal_id = _safe_id(proposal_id, kind="proposal_id")
        if not self.proposal_root.exists():
            raise CandidateGenerationError("CANDIDATE_PROPOSAL_NOT_FOUND", "未找到请求的 Candidate Proposal", status_code=404)
        paths = [
            path
            for path in self.proposal_root.rglob(CANDIDATE_PROPOSAL_FILENAME)
            if path.is_file() and path.resolve().is_relative_to(self.root)
        ]
        matches: list[Path] = []
        for path in sorted(paths, key=lambda item: item.as_posix()):
            payload = _read_json(path, code="CANDIDATE_PROPOSAL_UNREADABLE")
            if payload is not None and str(payload.get("proposal_id") or "") == proposal_id:
                matches.append(path.resolve())
        if not matches:
            raise CandidateGenerationError("CANDIDATE_PROPOSAL_NOT_FOUND", "未找到请求的 Candidate Proposal", status_code=404)
        if len(matches) > 1:
            raise CandidateGenerationError("CANDIDATE_PROPOSAL_IDENTITY_CONFLICT", "同一 Candidate Proposal 存在多个 canonical 文件", status_code=503)
        return matches[0]

    def _paths_for_proposal(self, proposal_id: str) -> tuple[Path, Path, Path, Path, Path]:
        output = self._find_proposal_path(proposal_id)
        directory = output.parent
        return (
            directory / CANDIDATE_PROPOSAL_INPUT_FILENAME,
            output,
            directory / CANDIDATE_PROPOSAL_STATE_FILENAME,
            directory / CANDIDATE_FREEZE_PREVIEW_FILENAME,
            directory / CANDIDATE_REVIEWS_FILENAME,
        )

    def _find_coverage_path(self) -> Path:
        registry = MechanismCoverageRegistryV1(self.root)
        for path in registry._candidate_paths():  # noqa: SLF001 - read-only source resolution
            if path.exists():
                resolved = path.resolve()
                try:
                    resolved.relative_to(self.root)
                except ValueError:
                    continue
                return resolved
        raise CandidateGenerationError("MECHANISM_COVERAGE_NOT_FOUND", "未找到 Mechanism Coverage Registry", status_code=404)

    def _require_ai_design_approval(self, objective_id: str) -> dict[str, Any]:
        try:
            return self.ai_design_approval.assert_candidate_generation_allowed(objective_id)
        except AIDesignApprovalError as exc:
            raise CandidateGenerationError(exc.code, exc.message_zh, status_code=exc.status_code, details=exc.details) from exc

    def _validate_ai_design_live_context(
        self,
        objective_id: str,
        objective: Mapping[str, Any],
        design: Mapping[str, Any],
        approval_receipt: Mapping[str, Any],
    ) -> None:
        """Rebuild the AI_DESIGN context at the consumer boundary.

        Approval evaluation intentionally stays a read-only, acyclic check.
        Candidate generation is the consumer gate that compares the current
        context with both the design and its approval evidence.
        """

        from .safe_runtime_context import SafeRuntimeContextError

        context_id = str(design.get("source_context_id") or "")
        context_hash = str(design.get("source_context_hash") or design.get("input_context_hash") or "")
        governed = bool(objective.get("objective_id") or objective.get("lifecycle_state") or objective.get("parent_proposal_id"))
        if not governed:
            return
        approval_id = str(approval_receipt.get("source_context_id") or "")
        approval_hash = str(approval_receipt.get("source_context_hash") or "")
        if not context_id or not context_hash or not approval_id or not approval_hash:
            raise CandidateGenerationError(
                "STALE_RUNTIME_CONTEXT",
                "AI Design 或批准回执缺少当前 SafeRuntimeContext 绑定，候选建议生成已 fail closed",
                status_code=409,
            )
        try:
            current = self.safe_runtime_context_builder.build(objective_id, purpose="AI_DESIGN")
        except SafeRuntimeContextError as exc:
            raise CandidateGenerationError(exc.code, "当前 AI_DESIGN SafeRuntimeContext 不可用，候选建议生成已阻断", status_code=exc.status_code, details=exc.details) from exc
        mismatches: list[str] = []
        if current.context_id != context_id:
            mismatches.append("design.source_context_id")
        if current.context_hash != context_hash:
            mismatches.append("design.source_context_hash")
        if approval_id != context_id:
            mismatches.append("approval.source_context_id")
        if approval_hash != context_hash:
            mismatches.append("approval.source_context_hash")
        if mismatches:
            raise CandidateGenerationError(
                "STALE_RUNTIME_CONTEXT",
                "AI Design 绑定的 SafeRuntimeContext 已过期，候选建议生成已阻断",
                status_code=409,
                details={"changed_bindings": mismatches, "current_context_id": current.context_id, "current_context_hash": current.context_hash},
            )

    def _load_sources(self, objective_id: str) -> tuple[CandidateGenerationInputV1, dict[str, Path]]:
        safe_context = self.safe_runtime_context_builder.build(objective_id, purpose="CANDIDATE_PROPOSAL")
        safe_context_input = safe_context.to_ai_design_input()
        objective_path = self._objective_path(objective_id)
        objective_raw = _read_json(objective_path, code="OBJECTIVE_NOT_FOUND")
        if objective_raw is None or str(objective_raw.get("objective_id") or "") != objective_id:
            raise CandidateGenerationError("OBJECTIVE_SOURCE_MISMATCH", "Objective 身份与请求不一致", status_code=503)

        design_dir = self.root / "reports" / "research_evolution" / "ai_design" / objective_id
        design_path = design_dir / AI_RESEARCH_DESIGN_FILENAME
        design_raw = _read_json(design_path, code="AI_DESIGN_NOT_FOUND")
        if design_raw is None or str(design_raw.get("objective_id") or "") != objective_id:
            raise CandidateGenerationError("AI_DESIGN_OBJECTIVE_MISMATCH", "AI 研究设计与当前 Objective 不匹配", status_code=409)
        if str(design_raw.get("status") or "") != AI_DESIGN_READY:
            raise CandidateGenerationError("AI_DESIGN_NOT_READY", "AI 研究设计尚未到达 AI_DESIGN_READY", status_code=409)
        if design_raw.get("outcome_blind") is not True or design_raw.get("performance_data_loaded") is not False or design_raw.get("outcome_fields_available") is not False:
            raise CandidateGenerationError("AI_DESIGN_NOT_OUTCOME_BLIND", "AI 研究设计未满足结果盲化边界", status_code=503)
        try:
            _assert_outcome_blind(design_raw)
        except PerformanceLeakError as exc:
            raise CandidateGenerationError("OUTCOME_FIELD_BLOCKED", "AI 研究设计包含被禁止的结果字段", status_code=503) from exc

        design_input_path = design_dir / AI_RESEARCH_DESIGN_INPUT_FILENAME
        design_input_raw = _read_json(design_input_path, code="AI_DESIGN_INPUT_UNREADABLE", required=False) or {}
        try:
            _assert_outcome_blind(design_input_raw)
        except PerformanceLeakError as exc:
            raise CandidateGenerationError("OUTCOME_FIELD_BLOCKED", "AI 研究设计输入包含被禁止的结果字段", status_code=503) from exc

        approval = self._require_ai_design_approval(objective_id)
        approval_receipt = approval.get("receipt") if isinstance(approval.get("receipt"), Mapping) else None
        approval_path_value = approval.get("receipt_path")
        if approval_receipt is None or not approval_path_value:
            raise CandidateGenerationError("AI_DESIGN_APPROVAL_INTEGRITY_FAILURE", "AI 设计批准回执不完整，候选建议生成已阻断", status_code=503)
        approval_path = (self.root / str(approval_path_value)).resolve()
        if not approval_path.is_relative_to(self.root) or approval_path.name != AI_DESIGN_APPROVAL_RECEIPT_FILENAME:
            raise CandidateGenerationError("AI_DESIGN_APPROVAL_INTEGRITY_FAILURE", "AI 设计批准回执路径不受信任，候选建议生成已阻断", status_code=503)
        self._validate_ai_design_live_context(objective_id, objective_raw, design_raw, approval_receipt)

        proposal_id = str(objective_raw.get("parent_proposal_id") or design_raw.get("parent_proposal_id") or "")
        if not proposal_id:
            raise CandidateGenerationError("PARENT_PROPOSAL_REQUIRED", "当前 Objective 没有绑定父 Proposal", status_code=409)
        governance = ResearchProposalGovernanceServiceV1(self.root)
        try:
            proposal_raw = governance.get_proposal(proposal_id)
            proposal_path = governance._proposal_path(proposal_id)  # noqa: SLF001 - read-only source resolution
        except ResearchProposalGovernanceError as exc:
            raise CandidateGenerationError(exc.code, exc.message_zh, status_code=exc.status_code, details=exc.details) from exc
        if str(proposal_raw.get("created_objective_id") or objective_id) != objective_id:
            raise CandidateGenerationError("PROPOSAL_OBJECTIVE_MISMATCH", "父 Proposal 未绑定当前 Objective", status_code=409)
        if str(design_raw.get("parent_proposal_id") or proposal_id) != proposal_id:
            raise CandidateGenerationError("AI_DESIGN_PROPOSAL_MISMATCH", "AI 研究设计的父 Proposal 身份不一致", status_code=409)

        lineage_path = self._lineage_path(objective_id)
        lineage_raw = _read_json(lineage_path, code="OBJECTIVE_LINEAGE_NOT_FOUND")
        if lineage_raw is None or str(lineage_raw.get("objective_id") or "") != objective_id:
            raise CandidateGenerationError("OBJECTIVE_LINEAGE_MISMATCH", "Objective lineage 与当前目标不匹配", status_code=409)
        if lineage_raw.get("immutable") is not True:
            raise CandidateGenerationError("OBJECTIVE_LINEAGE_NOT_IMMUTABLE", "Objective lineage 不是不可变记录", status_code=409)
        design_lineage = design_raw.get("lineage") if isinstance(design_raw.get("lineage"), Mapping) else {}
        if design_lineage.get("objective_lineage_id") and str(design_lineage.get("objective_lineage_id")) != str(lineage_raw.get("lineage_id") or ""):
            raise CandidateGenerationError("AI_DESIGN_LINEAGE_MISMATCH", "AI 研究设计与 Objective lineage 不一致", status_code=409)

        coverage_path = self._find_coverage_path()
        coverage_raw = _read_json(coverage_path, code="MECHANISM_COVERAGE_UNREADABLE")
        if coverage_raw is None:
            raise CandidateGenerationError("MECHANISM_COVERAGE_NOT_FOUND", "未找到 Mechanism Coverage Registry", status_code=404)
        try:
            _assert_outcome_blind(coverage_raw)
            coverage = MechanismCoverageRegistryV1(self.root).load(coverage_path)
        except PerformanceLeakError as exc:
            raise CandidateGenerationError("OUTCOME_FIELD_BLOCKED", "Mechanism Coverage Registry 包含被禁止的结果字段", status_code=503) from exc
        except Exception as exc:
            if isinstance(exc, CandidateGenerationError):
                raise
            raise CandidateGenerationError("MECHANISM_COVERAGE_INVALID", "Mechanism Coverage Registry 无法安全读取", status_code=503) from exc

        capability_raw = design_input_raw.get("available_data_capabilities") if isinstance(design_input_raw.get("available_data_capabilities"), Mapping) else None
        if capability_raw is None:
            capability_path = self.root / "data" / "research" / "data_capability.json"
            capability_raw = _read_json(capability_path, code="DATA_CAPABILITY_UNREADABLE", required=False) or {}
        if not capability_raw:
            capability_raw = safe_context_input.get("available_data_capabilities") or {}
        capabilities = _safe_capabilities(capability_raw)

        safe_design = {
            "design_id": str(design_raw.get("design_id") or ""),
            "design_hash": str(design_raw.get("design_hash") or ""),
            "source_context_id": str(design_raw.get("source_context_id") or ""),
            "source_context_hash": str(design_raw.get("source_context_hash") or ""),
            "input_context_hash": str(design_raw.get("input_context_hash") or ""),
            "research_hypothesis": str(design_raw.get("research_hypothesis") or "").strip(),
            "mechanism_family": str(design_raw.get("mechanism_family") or "").strip(),
            "candidate_design_intention": str(design_raw.get("candidate_design_intention") or "").strip(),
            "allowed_factors": [item for item in _strings(design_raw.get("allowed_factors")) if not _is_outcome_factor(item)],
            "excluded_mechanisms": _strings(design_raw.get("excluded_mechanisms")),
            "validation_expectation": _strings(design_raw.get("validation_expectation")),
        }
        if not safe_design["research_hypothesis"] or not safe_design["mechanism_family"] or not safe_design["allowed_factors"]:
            raise CandidateGenerationError("AI_DESIGN_INCOMPLETE", "AI 研究设计缺少候选建议所需字段", status_code=409)
        safe_proposal = _safe_proposal(proposal_raw)
        safe_lineage = _safe_lineage(lineage_raw)
        safe_objective = _safe_objective(objective_raw, safe_lineage)
        safe_coverage = _safe_coverage(coverage)
        source_refs = {
            "ai_research_design": _relative(self.root, design_path),
            "ai_design_approval": _relative(self.root, approval_path),
            "research_evolution_proposal": _relative(self.root, proposal_path),
            "objective": _relative(self.root, objective_path),
            "objective_lineage": _relative(self.root, lineage_path),
            "mechanism_coverage_registry": _relative(self.root, coverage_path),
        }
        if design_input_path.exists():
            source_refs["ai_research_design_input"] = _relative(self.root, design_input_path)
        source_hashes = {
            "ai_research_design": str(design_raw.get("design_hash") or stable_hash(safe_design)),
            "ai_design_approval": str(approval_receipt.get("receipt_hash") or ""),
            "ai_research_design_input": str(design_raw.get("input_context_hash") or stable_hash(design_input_raw)),
            "research_evolution_proposal": str(safe_proposal.get("proposal_hash") or stable_hash(safe_proposal)),
            "objective": stable_hash(safe_objective),
            "objective_lineage": stable_hash(safe_lineage),
            "mechanism_coverage_registry": stable_hash(safe_coverage),
            "safe_runtime_context": safe_context.context_hash,
        }
        context = CandidateGenerationInputV1(
            objective_id=objective_id,
            ai_research_design=safe_design,
            research_evolution_proposal=safe_proposal,
            objective=safe_objective,
            objective_lineage=safe_lineage,
            mechanism_coverage_registry=safe_coverage,
            available_data_capabilities=capabilities,
            source_refs=source_refs,
            source_hashes=source_hashes,
            source_context_id=safe_context.context_id,
            source_context_hash=safe_context.context_hash,
            source_budget_authority_status=str((safe_context.get("budget") or {}).get("authority_status") or "MISSING"),
            runtime_budget=safe_context.get("budget") or {},
            input_context_hash=safe_context.context_hash,
            ai_design_approval_id=str(approval.get("approval_id") or ""),
        )
        return context, {
            "ai_research_design": design_path,
            "ai_research_design_input": design_input_path,
            "research_evolution_proposal": proposal_path,
            "objective": objective_path,
            "objective_lineage": lineage_path,
            "mechanism_coverage_registry": coverage_path,
        }

    def build_input(self, objective_id: str) -> CandidateGenerationInputV1:
        objective_id = _safe_id(objective_id, kind="objective_id")
        with self._mutex:
            from .safe_runtime_context import SafeRuntimeContextError

            try:
                return self._load_sources(objective_id)[0]
            except SafeRuntimeContextError as exc:
                raise CandidateGenerationError(exc.code, exc.message_zh, status_code=503, details=exc.details) from exc

    prepare = build_input

    @staticmethod
    def _mechanism_sources(context: CandidateGenerationInputV1) -> list[tuple[str, str, str]]:
        proposal = context.research_evolution_proposal
        coverage = context.mechanism_coverage_registry
        design = context.ai_research_design
        values: list[tuple[str, str]] = []
        values.extend((item, "mechanism coverage") for item in _strings(coverage.get("covered")))
        for key in ("failed_mechanism", "failed_mechanism_family", "avoid_mechanism_family", "avoid_mechanisms"):
            values.extend((item, "failed/avoid Proposal mechanism") for item in _strings(proposal.get(key)))
        values.extend((item, "AI design exclusion") for item in _strings(design.get("excluded_mechanisms")))
        return [(raw, _normalise_mechanism(raw), source) for raw, source in values if _normalise_mechanism(raw)]

    def _duplicate_match(self, context: CandidateGenerationInputV1, mechanism_family: str) -> tuple[str, str, str] | None:
        candidate = _normalise_mechanism(mechanism_family)
        if not candidate:
            raise CandidateGenerationError("MECHANISM_FAMILY_REQUIRED", "候选建议缺少机制族", status_code=503)
        for raw, normalized, source in self._mechanism_sources(context):
            if candidate == normalized:
                return raw, normalized, source
        return None

    def check_similarity(self, objective_id: str, mechanism_family: str | None = None) -> dict[str, Any]:
        """Return the read-only duplicate mechanism decision."""
        context = self.build_input(objective_id)
        family = str(mechanism_family or context.ai_research_design.get("mechanism_family") or "")
        match = self._duplicate_match(context, family)
        return {
            "schema_version": "candidate-similarity-check-v1",
            "objective_id": context.objective_id,
            "mechanism_family": family,
            "normalized_mechanism_family": _normalise_mechanism(family),
            "status": DUPLICATE_MECHANISM_REJECTED if match else "NOVEL_MECHANISM_ALLOWED",
            "duplicate": match is not None,
            "matched_mechanism": match[0] if match else None,
            "matched_normalized_mechanism": match[1] if match else None,
            "matched_source": match[2] if match else None,
            "read_only": True,
        }

    similarity_check = check_similarity
    check_candidate_similarity = check_similarity

    @staticmethod
    def _select_factors(context: CandidateGenerationInputV1, mechanism_family: str) -> list[str]:
        available = [item for item in _strings(context.ai_research_design.get("allowed_factors")) if not _is_outcome_factor(item)]
        excluded = {_normalise_mechanism(item) for item, _, _ in CandidateGenerationManagerV1._mechanism_sources(context)}
        filtered: list[str] = []
        for factor in available:
            normalized = _machine_token(factor)
            if "liquidity_acceleration" in excluded and any(token in normalized for token in ("amount", "illiq", "volume_accel", "turnover")):
                continue
            filtered.append(factor)
        if not filtered:
            raise CandidateGenerationError("FACTOR_CONTRACT_EMPTY", "没有可供候选建议使用的结果盲化因子", status_code=409)
        family_token = _machine_token(mechanism_family)
        preferred_tokens = ("gap", "range", "atr", "body", "shadow", "vol") if "event" in family_token or "volatility" in family_token else ()
        preferred = [item for item in filtered if any(token in _machine_token(item) for token in preferred_tokens)]
        selected = preferred[:3] if preferred else filtered[:3]
        return selected or filtered[:1]

    @staticmethod
    def _candidate_name(mechanism_family: str, directions: list[str]) -> str:
        family = _machine_token(mechanism_family).upper() or "RESEARCH"
        secondary = next((
            _machine_token(item).upper()
            for item in directions
            if _normalise_mechanism(item) not in {"", _normalise_mechanism(mechanism_family)}
            and _machine_token(item)
        ), "")
        raw = f"{family}_{secondary}_V1" if secondary else f"{family}_V1"
        return raw[:255]

    @staticmethod
    def _data_contract(context: CandidateGenerationInputV1, factors: list[str], mechanism_family: str) -> dict[str, Any]:
        capabilities = context.available_data_capabilities
        raw_datasets = capabilities.get("datasets")
        available_ids = [
            str(item.get("dataset_id"))
            for item in raw_datasets
            if isinstance(item, Mapping) and item.get("dataset_id")
        ] if isinstance(raw_datasets, list) else _strings(raw_datasets)
        ready_ids = _strings(capabilities.get("ready_dataset_ids"))
        required = ["daily_ohlcva_raw"]
        if "event" in _machine_token(mechanism_family) and "corporate_action_gbbq" in available_ids:
            required.append("corporate_action_gbbq")
        return {
            "required_dataset_ids": required,
            "available_dataset_ids": available_ids,
            "ready_dataset_ids": ready_ids,
            "missing_required_dataset_ids": [item for item in required if item not in available_ids],
            "required_data_semantics": ["PIT-safe", "trade-date aligned", "signal available at T_CLOSE"],
            "factor_contract": list(factors),
            "PIT_safe_required": True,
            "available_at_semantics": "T_CLOSE",
            "data_capabilities": capabilities,
        }

    def _build_proposal(self, context: CandidateGenerationInputV1) -> dict[str, Any]:
        self._require_ai_design_approval(context.objective_id)
        design = context.ai_research_design
        proposal = context.research_evolution_proposal
        mechanism_family = str(design.get("mechanism_family") or "").strip()
        duplicate = self._duplicate_match(context, mechanism_family)
        if duplicate:
            raise CandidateGenerationError(
                DUPLICATE_MECHANISM_REJECTED,
                "候选建议重复了已覆盖或已失败的机制，系统拒绝生成",
                status_code=409,
                details={"mechanism_family": mechanism_family, "matched_mechanism": duplicate[0], "normalized_mechanism": duplicate[1], "source": duplicate[2]},
            )
        factors = self._select_factors(context, mechanism_family)
        directions = _strings(proposal.get("suggested_research_directions"))
        directions.extend(item for item in _strings(context.mechanism_coverage_registry.get("unexplored")) if item not in directions)
        candidate_name = self._candidate_name(mechanism_family, directions)
        horizon = list(context.objective.get("preferred_horizon") or [5])
        holding_period = int(horizon[0])
        risk_constraints = dict(context.objective.get("risk_constraints") or {})
        execution = {
            "signal_time": "T_CLOSE",
            "entry": "NEXT_SESSION_OPEN",
            "holding_period": holding_period,
            "holding_period_unit": "TRADING_SESSIONS",
            "risk_constraints": risk_constraints,
            "same_session_sell_forbidden": True,
        }
        data_contract = self._data_contract(context, factors, mechanism_family)
        excluded = _strings(proposal.get("avoid_mechanism_family"))
        excluded.extend(item for item in _strings(proposal.get("avoid_mechanisms")) if item not in excluded)
        excluded.extend(item for item in _strings(design.get("excluded_mechanisms")) if item not in excluded)
        excluded = [item for item in excluded if item]
        parent_proposal = {
            key: context.research_evolution_proposal.get(key)
            for key in (
                "proposal_id",
                "proposal_hash",
                "parent_objective_id",
                "failed_mechanism",
                "failed_mechanism_family",
                "failure_summary",
                "suggested_research_directions",
            )
            if key in context.research_evolution_proposal
        }
        base: dict[str, Any] = {
            "schema_version": CANDIDATE_PROPOSAL_SCHEMA_VERSION,
            "proposal_id": "",
            "proposal_hash": "",
            "objective_id": context.objective_id,
            "candidate_name": candidate_name,
            "ai_research_design_id": design.get("design_id"),
            "ai_design_approval_id": context.ai_design_approval_id,
            "ai_design_approval_receipt_hash": context.source_hashes.get("ai_design_approval"),
            "mechanism_family": mechanism_family,
            "research_hypothesis": str(design.get("research_hypothesis") or ""),
            "candidate_design_intention": str(design.get("candidate_design_intention") or ""),
            "validation_expectation": _strings(design.get("validation_expectation")),
            "factor_contract": factors,
            "execution_contract": execution,
            "data_contract": data_contract,
            "excluded_family": excluded,
            "excluded_mechanisms": _strings(design.get("excluded_mechanisms")),
            "parent_proposal": parent_proposal,
            "objective": context.objective,
            "lineage": {
                **dict(context.objective_lineage),
                "objective_id": context.objective_id,
                "parent_proposal_id": context.research_evolution_proposal.get("proposal_id"),
                "parent_proposal_hash": context.research_evolution_proposal.get("proposal_hash"),
                "relation": "CANDIDATE_PROPOSAL_FOR_OBJECTIVE",
                "immutable": True,
            },
            "multiple_testing_family_id": context.objective.get("multiple_testing_family_id"),
            "budget": context.objective.get("budget"),
            "source_refs": context.source_refs,
            "source_hashes": context.source_hashes,
            "source_context_id": context.source_context_id,
            "source_context_hash": context.source_context_hash,
            "source_budget_authority_status": context.source_budget_authority_status,
            "source_budget_snapshot": context.runtime_budget,
            "input_context_hash": context.input_context_hash,
            "status": CANDIDATE_PROPOSAL_READY,
            "human_review_required": True,
            "outcome_blind": True,
            "performance_data_loaded": False,
            "outcome_fields_available": False,
            "governance": {
                "from_state": GENERATED,
                "current_state": CANDIDATE_PROPOSAL_READY,
                "status": CANDIDATE_PROPOSAL_READY,
                "next_action": HUMAN_REVIEW_REQUIRED,
                "requires_human_review": True,
                "ai_design_approval_required": True,
                "candidate_generation_allowed": True,
                "candidate_created": False,
                "candidate_frozen": False,
                "structural_preflight_started": False,
                "trial_started": False,
                "ai_called": False,
                "budget_consumed": False,
                "automatic_execution": False,
            },
            "state_history": [GENERATED, CANDIDATE_PROPOSAL_READY],
            "generated_at": self._now(),
        }
        identity = {
            key: base[key]
            for key in (
                "objective_id",
                "candidate_name",
                "ai_research_design_id",
                "ai_design_approval_id",
                "ai_design_approval_receipt_hash",
                "mechanism_family",
                "research_hypothesis",
                "candidate_design_intention",
                "validation_expectation",
                "factor_contract",
                "execution_contract",
                "data_contract",
                "excluded_family",
                "excluded_mechanisms",
                "parent_proposal",
                "objective",
                "lineage",
                "multiple_testing_family_id",
                "budget",
                "source_hashes",
                "source_context_id",
                "source_context_hash",
                "source_budget_authority_status",
                "source_budget_snapshot",
                "input_context_hash",
            )
        }
        proposal_hash = stable_hash(identity)
        base["proposal_hash"] = proposal_hash
        base["proposal_id"] = f"CANDIDATE_PROPOSAL_{proposal_hash[:24].upper()}"
        try:
            _assert_outcome_blind(base)
        except PerformanceLeakError as exc:
            raise CandidateGenerationError("OUTCOME_FIELD_BLOCKED", "候选建议输出包含被禁止的结果字段", status_code=503) from exc
        return base

    @staticmethod
    def _identity(payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: payload.get(key)
            for key in (
                "objective_id",
                "candidate_name",
                "ai_research_design_id",
                "ai_design_approval_id",
                "ai_design_approval_receipt_hash",
                "mechanism_family",
                "research_hypothesis",
                "candidate_design_intention",
                "validation_expectation",
                "factor_contract",
                "execution_contract",
                "data_contract",
                "excluded_family",
                "excluded_mechanisms",
                "parent_proposal",
                "objective",
                "lineage",
                "multiple_testing_family_id",
                "budget",
                "source_hashes",
                "source_context_id",
                "source_context_hash",
                "source_budget_authority_status",
                "source_budget_snapshot",
                "input_context_hash",
            )
        }

    def _validate_persisted(self, proposal: Mapping[str, Any], context: CandidateGenerationInputV1) -> None:
        if str(proposal.get("objective_id") or "") != context.objective_id:
            raise CandidateGenerationError("CANDIDATE_PROPOSAL_CONTEXT_CONFLICT", "已存在的 Candidate Proposal 不属于当前 Objective", status_code=409)
        context_changed = str(proposal.get("input_context_hash") or "") != context.input_context_hash
        source_context_changed = str(proposal.get("source_context_hash") or "") != context.source_context_hash
        if context_changed or source_context_changed:
            stored_budget_status = str(proposal.get("source_budget_authority_status") or "MISSING")
            current_budget = context.source_budget_authority_status
            current_budget_view = context.runtime_budget if isinstance(context.runtime_budget, Mapping) else {}
            # Older fixtures and early Objective setup may create the empty
            # zero-use registry after the proposal.  Establishing that single
            # authority does not alter the research decision; any later
            # mutation of an already-known authority remains stale.
            empty_budget_established = (
                stored_budget_status == "MISSING"
                and current_budget == "UNIQUE_CANONICAL"
                and not context.runtime_budget.get("active_reservations")
                and not current_budget_view.get("used")
                and not current_budget_view.get("reserved")
            )
            if not empty_budget_established:
                code = "STALE_RUNTIME_CONTEXT" if source_context_changed else "CANDIDATE_PROPOSAL_CONTEXT_CHANGED"
                message = "候选建议绑定的安全运行时上下文已过期，请重新生成" if source_context_changed else "候选建议输入上下文已变化，请重新生成并人工复核"
                raise CandidateGenerationError(code, message, status_code=409)
        expected_hash = stable_hash(self._identity(proposal))
        if str(proposal.get("proposal_hash") or "") != expected_hash:
            raise CandidateGenerationError("CANDIDATE_PROPOSAL_HASH_INVALID", "Candidate Proposal 哈希校验失败", status_code=503)
        if str(proposal.get("proposal_id") or "") != f"CANDIDATE_PROPOSAL_{expected_hash[:24].upper()}":
            raise CandidateGenerationError("CANDIDATE_PROPOSAL_ID_INVALID", "Candidate Proposal 编号校验失败", status_code=503)
        if str(proposal.get("status") or "") != CANDIDATE_PROPOSAL_READY:
            raise CandidateGenerationError("CANDIDATE_PROPOSAL_STATUS_INVALID", "Candidate Proposal 原始状态无效", status_code=503)
        required = ("research_hypothesis", "mechanism_family", "factor_contract", "execution_contract", "data_contract", "excluded_family")
        if any(key not in proposal for key in required):
            raise CandidateGenerationError("CANDIDATE_PROPOSAL_INCOMPLETE", "Candidate Proposal 缺少必要字段", status_code=503)
        if proposal.get("human_review_required") is not True:
            raise CandidateGenerationError("CANDIDATE_PROPOSAL_GOVERNANCE_INVALID", "Candidate Proposal 未停在人审边界", status_code=503)
        try:
            _assert_outcome_blind(proposal)
        except PerformanceLeakError as exc:
            raise CandidateGenerationError("OUTCOME_FIELD_BLOCKED", "Candidate Proposal 包含被禁止的结果字段", status_code=503) from exc
        governance = proposal.get("governance") if isinstance(proposal.get("governance"), Mapping) else {}
        for key in ("candidate_created", "candidate_frozen", "structural_preflight_started", "trial_started", "ai_called", "budget_consumed"):
            if governance.get(key) is not False:
                raise CandidateGenerationError("CANDIDATE_PROPOSAL_GOVERNANCE_INVALID", "Candidate Proposal 违反零副作用边界", status_code=503)
        match = self._duplicate_match(context, str(proposal.get("mechanism_family") or ""))
        if match:
            raise CandidateGenerationError(DUPLICATE_MECHANISM_REJECTED, "已存在相同机制，Candidate Proposal 不能复用", status_code=409, details={"matched_mechanism": match[0], "normalized_mechanism": match[1]})

    @staticmethod
    def _state_for(proposal: Mapping[str, Any], *, state: str, history: list[str], review_ids: list[str] | None = None) -> dict[str, Any]:
        previous = history[-2] if len(history) > 1 else GENERATED
        next_action = {
            CANDIDATE_PROPOSAL_READY: HUMAN_REVIEW_REQUIRED,
            HUMAN_REVIEW_REQUIRED: HUMAN_REVIEW_REQUIRED,
            APPROVED: FREEZE_PREVIEW_READY,
            FREEZE_PREVIEW_READY: HUMAN_CONFIRM_CANDIDATE_FREEZE,
            _LEGACY_CANDIDATE_FREEZE_READY: HUMAN_CONFIRM_CANDIDATE_FREEZE,
            FROZEN: CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW,
            CANDIDATE_GOVERNANCE_FROZEN: CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW,
            EXECUTABLE_CANDIDATE_FROZEN: RUN_STRUCTURAL_PREFLIGHT,
            READY_FOR_STRUCTURAL_PREFLIGHT: RUN_STRUCTURAL_PREFLIGHT,
            REJECTED: CLOSED,
            CLOSED: "STOPPED",
        }.get(state, HUMAN_REVIEW_REQUIRED)
        governance_frozen = state in {FROZEN, CANDIDATE_GOVERNANCE_FROZEN, EXECUTABLE_CANDIDATE_FROZEN, READY_FOR_STRUCTURAL_PREFLIGHT}
        executable_frozen = state in {EXECUTABLE_CANDIDATE_FROZEN, READY_FOR_STRUCTURAL_PREFLIGHT}
        return {
            "schema_version": CANDIDATE_PROPOSAL_STATE_SCHEMA_VERSION,
            "transition_id": f"CANDIDATE_PROPOSAL_TRANSITION_{stable_hash({'proposal_id': proposal.get('proposal_id'), 'state': state, 'history': history})[:24].upper()}",
            "objective_id": proposal.get("objective_id"),
            "proposal_id": proposal.get("proposal_id"),
            "proposal_hash": proposal.get("proposal_hash"),
            "from_state": previous,
            "to_state": state,
            "status": state,
            "state_history": history,
            "review_ids": review_ids or [],
            "requires_human_review": state in {CANDIDATE_PROPOSAL_READY, HUMAN_REVIEW_REQUIRED, FREEZE_PREVIEW_READY},
            "next_action": next_action,
            "candidate_created": governance_frozen,
            "candidate_frozen": governance_frozen,
            "executable_candidate_frozen": executable_frozen,
            "structural_preflight_ready": executable_frozen,
            "structural_preflight_started": False,
            "trial_started": False,
            "ai_called": False,
            "budget_consumed": False,
            "updated_at": proposal.get("generated_at"),
        }

    @staticmethod
    def _read_reviews(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise CandidateGenerationError("CANDIDATE_REVIEW_HISTORY_UNREADABLE", "候选建议治理历史暂时不可读", status_code=503) from exc
        records: list[dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CandidateGenerationError("CANDIDATE_REVIEW_HISTORY_INVALID", "候选建议治理历史格式无效", status_code=503) from exc
            if not isinstance(item, Mapping):
                raise CandidateGenerationError("CANDIDATE_REVIEW_HISTORY_INVALID", "候选建议治理历史格式无效", status_code=503)
            records.append(dict(item))
        return records

    @staticmethod
    def _safe_reviewer(value: Any) -> str:
        result = str(value or "").strip()
        if not result or len(result) > _REVIEWER_MAX_LENGTH or any(ord(char) < 32 for char in result):
            raise CandidateGenerationError("REVIEWER_REQUIRED", "必须提供有效的人工审核人", status_code=400)
        return result

    @staticmethod
    def _candidate_identity(proposal: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: proposal.get(key)
            for key in (
                "objective_id",
                "proposal_id",
                "proposal_hash",
                "candidate_name",
                "mechanism_family",
                "factor_contract",
                "execution_contract",
                "data_contract",
                "multiple_testing_family_id",
            )
        }

    @classmethod
    def _candidate_hash_for_proposal(cls, proposal: Mapping[str, Any]) -> str:
        return stable_hash(cls._candidate_identity(proposal))

    @staticmethod
    def _candidate_id_for_hash(candidate_hash: str) -> str:
        return f"CANDIDATE_{str(candidate_hash)[:24].upper()}"

    @staticmethod
    def _candidate_contract(proposal: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "factor_contract": proposal.get("factor_contract"),
            "execution_contract": proposal.get("execution_contract"),
            "data_contract": proposal.get("data_contract"),
            "multiple_testing_family_id": proposal.get("multiple_testing_family_id"),
        }

    def _read_candidate_registry(self, objective_id: str) -> dict[str, Any] | None:
        path = self._candidate_registry_path(objective_id)
        payload = _read_json(path, code="CANDIDATE_REGISTRY_UNREADABLE", required=False)
        if payload is None:
            return None
        if str(payload.get("schema_version") or "") != CANDIDATE_REGISTRY_SCHEMA_VERSION:
            raise CandidateGenerationError("CANDIDATE_REGISTRY_SCHEMA_INVALID", "Candidate Registry schema 无效", status_code=503)
        if str(payload.get("objective_id") or "") != objective_id:
            raise CandidateGenerationError("CANDIDATE_REGISTRY_OBJECTIVE_MISMATCH", "Candidate Registry 与 Objective 不一致", status_code=503)
        entries = payload.get("candidates")
        if not isinstance(entries, list):
            raise CandidateGenerationError("CANDIDATE_REGISTRY_INVALID", "Candidate Registry 缺少候选记录", status_code=503)
        if str(payload.get("registry_hash") or "") != stable_hash(entries):
            raise CandidateGenerationError("CANDIDATE_REGISTRY_HASH_INVALID", "Candidate Registry 哈希校验失败", status_code=503)
        for entry in entries:
            if not isinstance(entry, Mapping) or not entry.get("candidate_id"):
                raise CandidateGenerationError("CANDIDATE_REGISTRY_INVALID", "Candidate Registry 中存在无效候选记录", status_code=503)
            if str(entry.get("entry_hash") or "") != stable_hash({key: value for key, value in entry.items() if key != "entry_hash"}):
                raise CandidateGenerationError("CANDIDATE_REGISTRY_ENTRY_HASH_INVALID", "Candidate Registry 候选记录哈希校验失败", status_code=503)
        try:
            _assert_outcome_blind(payload)
        except PerformanceLeakError as exc:
            raise CandidateGenerationError("OUTCOME_FIELD_BLOCKED", "Candidate Registry 包含被禁止的结果字段", status_code=503) from exc
        return dict(payload)

    def _append_candidate_registry(self, objective_id: str, entry: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        existing = self._read_candidate_registry(objective_id)
        entries = list(existing.get("candidates", ())) if existing else []
        candidate_id = str(entry.get("candidate_id") or "")
        current = next((item for item in entries if isinstance(item, Mapping) and str(item.get("candidate_id") or "") == candidate_id), None)
        if current is not None:
            identity_keys = ("candidate_id", "candidate_hash", "contract", "lineage", "state")
            if any(current.get(key) != entry.get(key) for key in identity_keys):
                raise CandidateGenerationError(
                    "FROZEN_CANDIDATE_IDENTITY_CONFLICT",
                    "冻结 Candidate 身份或合同已变化，禁止覆盖旧 Candidate；必须生成 NEW_CANDIDATE",
                    status_code=409,
                    details={"status": NEW_CANDIDATE, "candidate_id": candidate_id, "overwrite_forbidden": True},
                )
            return dict(existing), False
        entries.append(dict(entry))
        payload = {
            "schema_version": CANDIDATE_REGISTRY_SCHEMA_VERSION,
            "objective_id": objective_id,
            "candidates": entries,
            "registry_hash": stable_hash(entries),
            "append_only": True,
            "performance_data_loaded": False,
            "budget_consumed": False,
        }
        try:
            _assert_outcome_blind(payload)
        except PerformanceLeakError as exc:
            raise CandidateGenerationError("OUTCOME_FIELD_BLOCKED", "Candidate Registry 包含被禁止的结果字段", status_code=503) from exc
        _atomic_write_json(self._candidate_registry_path(objective_id), payload)
        return payload, True

    def _validate_freeze_preview(self, proposal: Mapping[str, Any], preview: Mapping[str, Any]) -> None:
        if str(preview.get("schema_version") or "") != CANDIDATE_FREEZE_PREVIEW_SCHEMA_VERSION:
            raise CandidateGenerationError("CANDIDATE_FREEZE_PREVIEW_INVALID", "Candidate Freeze Preview schema 无效", status_code=503)
        if str(preview.get("proposal_id") or "") != str(proposal.get("proposal_id") or "") or str(preview.get("proposal_hash") or "") != str(proposal.get("proposal_hash") or ""):
            raise CandidateGenerationError("STALE_CANDIDATE_FREEZE_PREVIEW", "Candidate Freeze Preview 与 Proposal hash 不一致", status_code=409)
        candidate_hash = self._candidate_hash_for_proposal(proposal)
        if str(preview.get("candidate_hash") or "") != candidate_hash:
            raise CandidateGenerationError("CANDIDATE_FREEZE_HASH_INVALID", "Candidate Freeze Preview 的 Candidate hash 校验失败", status_code=503)
        expected_id = self._candidate_id_for_hash(candidate_hash)
        if str(preview.get("candidate_id") or "") not in {expected_id, f"CANDIDATE_PREVIEW_{candidate_hash[:24].upper()}"}:
            raise CandidateGenerationError("CANDIDATE_FREEZE_ID_INVALID", "Candidate Freeze Preview 的 Candidate ID 校验失败", status_code=503)
        expected_preview_hash = stable_hash({key: value for key, value in preview.items() if key != "preview_hash"})
        if str(preview.get("preview_hash") or "") != expected_preview_hash:
            raise CandidateGenerationError("CANDIDATE_FREEZE_PREVIEW_HASH_INVALID", "Candidate Freeze Preview 哈希校验失败", status_code=503)
        if str(preview.get("status") or "") not in {FREEZE_PREVIEW_READY, _LEGACY_CANDIDATE_FREEZE_READY}:
            raise CandidateGenerationError("CANDIDATE_FREEZE_PREVIEW_NOT_READY", "Candidate Freeze Preview 尚未进入确认状态", status_code=409)
        for key in ("candidate_created", "candidate_frozen", "structural_preflight_started", "trial_started", "ai_called", "budget_consumed"):
            if preview.get(key) is not False:
                raise CandidateGenerationError("CANDIDATE_FREEZE_PREVIEW_MUTATED", "Candidate Freeze Preview 已出现非法副作用标记", status_code=503)
        if preview.get("lineage") != proposal.get("lineage") or ("research_lineage" in preview and preview.get("research_lineage") != proposal.get("lineage")):
            raise CandidateGenerationError("CANDIDATE_FREEZE_LINEAGE_INVALID", "Candidate Freeze Preview 的 lineage 已变化", status_code=503)
        try:
            _assert_outcome_blind(preview)
        except PerformanceLeakError as exc:
            raise CandidateGenerationError("OUTCOME_FIELD_BLOCKED", "Candidate Freeze Preview 包含被禁止的结果字段", status_code=503) from exc

    def _freeze_entry(self, proposal: Mapping[str, Any], preview: Mapping[str, Any], *, freeze_id: str, reviewer: str, timestamp: str) -> dict[str, Any]:
        candidate_hash = str(preview.get("candidate_hash") or "")
        base = {
            "schema_version": CANDIDATE_FREEZE_SCHEMA_VERSION,
            "freeze_id": freeze_id,
            "proposal_id": proposal.get("proposal_id"),
            "proposal_hash": proposal.get("proposal_hash"),
            "candidate_id": self._candidate_id_for_hash(candidate_hash),
            "candidate_hash": candidate_hash,
            "reviewer": reviewer,
            "timestamp": timestamp,
            "preview_hash": preview.get("preview_hash"),
            "resulting_state": FROZEN,
            "structural_preflight_started": False,
            "trial_started": False,
            "ai_called": False,
            "budget_consumed": False,
            "automatic_execution": False,
        }
        base["record_hash"] = stable_hash(base)
        return base

    @staticmethod
    def _freeze_record_matches(existing: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
        return all(existing.get(key) == expected.get(key) for key in ("freeze_id", "proposal_id", "proposal_hash", "candidate_id", "candidate_hash", "preview_hash"))

    def _view(self, proposal: Mapping[str, Any], *, state: Mapping[str, Any] | None, reviews: list[Mapping[str, Any]], freeze_preview: Mapping[str, Any] | None, output_path: Path) -> dict[str, Any]:
        current_state = str((state or {}).get("status") or CANDIDATE_PROPOSAL_READY)
        history = list((state or {}).get("state_history") or proposal.get("state_history") or [GENERATED, CANDIDATE_PROPOSAL_READY])
        objective_id = str(proposal.get("objective_id") or output_path.parent.name)
        freeze_receipt = _read_json(output_path.parent / CANDIDATE_FREEZE_RECEIPT_FILENAME, code="CANDIDATE_FREEZE_RECEIPT_UNREADABLE", required=False)
        candidate_registry = self._read_candidate_registry(objective_id)
        candidate_id = str(freeze_receipt.get("candidate_id") or "") if freeze_receipt else ""
        registry_entry = None
        if candidate_registry is not None and candidate_id:
            registry_entry = next((dict(item) for item in candidate_registry.get("candidates", ()) if isinstance(item, Mapping) and str(item.get("candidate_id") or "") == candidate_id), None)
        governance_frozen = current_state in {FROZEN, CANDIDATE_GOVERNANCE_FROZEN, EXECUTABLE_CANDIDATE_FROZEN, READY_FOR_STRUCTURAL_PREFLIGHT}
        executable_frozen = current_state in {EXECUTABLE_CANDIDATE_FROZEN, READY_FOR_STRUCTURAL_PREFLIGHT}
        result = dict(proposal)
        result["proposal_status"] = CANDIDATE_PROPOSAL_READY
        result["governance_state"] = current_state
        result["status"] = current_state
        result["state_history"] = history
        result["governance_history"] = [dict(item) for item in reviews]
        result["freeze_preview"] = dict(freeze_preview) if freeze_preview is not None else None
        result["freeze_record"] = dict(freeze_receipt) if freeze_receipt is not None else None
        result["candidate_registry"] = registry_entry
        result["candidate_id"] = candidate_id or (str(freeze_preview.get("candidate_id") or "") if isinstance(freeze_preview, Mapping) else None)
        result["candidate_hash"] = str((freeze_receipt or {}).get("candidate_hash") or freeze_preview.get("candidate_hash") or "") if isinstance(freeze_preview, Mapping) else (str(freeze_receipt.get("candidate_hash") or "") if freeze_receipt else None)
        result["output_path"] = _relative(self.root, output_path)
        result["governance"] = {
            **dict(proposal.get("governance") or {}),
            "schema_version": CANDIDATE_PROPOSAL_SCHEMA_VERSION,
            "current_state": current_state,
            "next_action": (state or {}).get("next_action") or HUMAN_REVIEW_REQUIRED,
            "requires_human_review": current_state in {CANDIDATE_PROPOSAL_READY, HUMAN_REVIEW_REQUIRED, FREEZE_PREVIEW_READY},
            "candidate_created": governance_frozen,
            "candidate_frozen": governance_frozen,
            "executable_candidate_frozen": executable_frozen,
            "structural_preflight_ready": executable_frozen,
            "structural_preflight_started": False,
            "trial_started": False,
            "ai_called": False,
            "budget_consumed": False,
            "freeze_confirmed": governance_frozen,
            "freeze_id": freeze_receipt.get("freeze_id") if freeze_receipt else None,
            "candidate_registry_ref": _relative(self.root, self._candidate_registry_path(objective_id)) if governance_frozen else None,
            "automatic_structural_preflight": False,
            "automatic_trial_started": False,
        }
        result["human_review_required"] = current_state in {CANDIDATE_PROPOSAL_READY, HUMAN_REVIEW_REQUIRED, FREEZE_PREVIEW_READY}
        result["candidate_created"] = governance_frozen
        result["candidate_frozen"] = governance_frozen
        result["executable_candidate_frozen"] = executable_frozen
        result["structural_preflight_ready"] = executable_frozen
        result["structural_preflight_started"] = False
        result["trial_started"] = False
        result["budget_consumed"] = False
        result["outcome_blind"] = True
        result["performance_data_loaded"] = False
        result["outcome_fields_available"] = False
        return result

    def _load_for_objective(self, objective_id: str) -> tuple[CandidateGenerationInputV1, dict[str, Any] | None, Path, Mapping[str, Any] | None, Mapping[str, Any] | None, list[dict[str, Any]], tuple[Path, Path, Path, Path, Path]]:
        context, _ = self._load_sources(objective_id)
        _, output_path, state_path, preview_path, reviews_path = self._paths(objective_id)
        proposal = _read_json(output_path, code="CANDIDATE_PROPOSAL_UNREADABLE", required=False)
        state = _read_json(state_path, code="CANDIDATE_PROPOSAL_STATE_UNREADABLE", required=False)
        freeze_preview = _read_json(preview_path, code="CANDIDATE_FREEZE_PREVIEW_UNREADABLE", required=False)
        reviews = self._read_reviews(reviews_path)
        if proposal is not None:
            self._validate_persisted(proposal, context)
            if state is not None:
                self._validate_state(state, proposal)
        return context, dict(proposal) if proposal is not None else None, output_path, state, freeze_preview, reviews, (self._paths(objective_id))

    def _validate_state(self, state: Mapping[str, Any], proposal: Mapping[str, Any]) -> None:
        if str(state.get("proposal_id") or "") != str(proposal.get("proposal_id") or "") or str(state.get("proposal_hash") or "") != str(proposal.get("proposal_hash") or ""):
            raise CandidateGenerationError("CANDIDATE_PROPOSAL_STATE_INVALID", "候选建议状态记录与 Proposal 身份不一致", status_code=503)
        valid = {CANDIDATE_PROPOSAL_READY, HUMAN_REVIEW_REQUIRED, APPROVED, FREEZE_PREVIEW_READY, _LEGACY_CANDIDATE_FREEZE_READY, FROZEN, CANDIDATE_GOVERNANCE_FROZEN, EXECUTABLE_CANDIDATE_FROZEN, READY_FOR_STRUCTURAL_PREFLIGHT, REJECTED, CLOSED}
        if str(state.get("status") or "") not in valid:
            raise CandidateGenerationError("CANDIDATE_PROPOSAL_STATE_INVALID", "候选建议状态不受支持", status_code=503)
        frozen = str(state.get("status") or "") in {FROZEN, CANDIDATE_GOVERNANCE_FROZEN, EXECUTABLE_CANDIDATE_FROZEN, READY_FOR_STRUCTURAL_PREFLIGHT}
        executable = str(state.get("status") or "") in {EXECUTABLE_CANDIDATE_FROZEN, READY_FOR_STRUCTURAL_PREFLIGHT}
        for key in ("candidate_created", "candidate_frozen", "structural_preflight_started", "trial_started", "ai_called", "budget_consumed"):
            expected = frozen if key in {"candidate_created", "candidate_frozen"} else False
            if state.get(key) is not expected:
                raise CandidateGenerationError("CANDIDATE_PROPOSAL_STATE_INVALID", "候选建议状态违反零副作用边界", status_code=503)
        if ("executable_candidate_frozen" in state and state.get("executable_candidate_frozen") is not executable) or ("structural_preflight_ready" in state and state.get("structural_preflight_ready") is not executable):
            raise CandidateGenerationError("CANDIDATE_PROPOSAL_STATE_INVALID", "候选建议状态的执行冻结标记不一致", status_code=503)
        try:
            _assert_outcome_blind(state)
        except PerformanceLeakError as exc:
            raise CandidateGenerationError("OUTCOME_FIELD_BLOCKED", "候选建议状态包含被禁止的结果字段", status_code=503) from exc

    def _load_view(self, proposal_id: str) -> dict[str, Any]:
        output_path = self._find_proposal_path(proposal_id)
        objective_id = output_path.parent.name
        context = self.build_input(objective_id)
        proposal = _read_json(output_path, code="CANDIDATE_PROPOSAL_UNREADABLE")
        if proposal is None:
            raise CandidateGenerationError("CANDIDATE_PROPOSAL_NOT_FOUND", "未找到请求的 Candidate Proposal", status_code=404)
        try:
            self._validate_persisted(proposal, context)
        except CandidateGenerationError as exc:
            modification = self._freeze_modification_from_paths(output_path)
            if modification.get("status") == NEW_CANDIDATE:
                raise CandidateGenerationError("NEW_CANDIDATE_REQUIRED", str(modification.get("message_zh")), status_code=409, details=modification) from exc
            raise
        state_path = output_path.parent / CANDIDATE_PROPOSAL_STATE_FILENAME
        preview_path = output_path.parent / CANDIDATE_FREEZE_PREVIEW_FILENAME
        reviews_path = output_path.parent / CANDIDATE_REVIEWS_FILENAME
        state = _read_json(state_path, code="CANDIDATE_PROPOSAL_STATE_UNREADABLE", required=False)
        if state is not None:
            self._validate_state(state, proposal)
        preview = _read_json(preview_path, code="CANDIDATE_FREEZE_PREVIEW_UNREADABLE", required=False)
        if preview is not None:
            self._validate_freeze_preview(proposal, preview)
        if state is not None and str(state.get("status") or "") in {FROZEN, CANDIDATE_GOVERNANCE_FROZEN, EXECUTABLE_CANDIDATE_FROZEN, READY_FOR_STRUCTURAL_PREFLIGHT}:
            if preview is None:
                raise CandidateGenerationError("CANDIDATE_FREEZE_PREVIEW_MISSING", "已冻结 Candidate 缺少 Freeze Preview", status_code=503)
            receipt = _read_json(output_path.parent / CANDIDATE_FREEZE_RECEIPT_FILENAME, code="CANDIDATE_FREEZE_RECEIPT_UNREADABLE", required=False)
            if receipt is None:
                raise CandidateGenerationError("CANDIDATE_FREEZE_RECEIPT_MISSING", "已冻结 Candidate 缺少治理回执", status_code=503)
            registry = self._read_candidate_registry(objective_id)
            candidate_id = str(receipt.get("candidate_id") or "")
            registry_entry = next((item for item in (registry or {}).get("candidates", ()) if isinstance(item, Mapping) and str(item.get("candidate_id") or "") == candidate_id), None)
            if registry_entry is None:
                raise CandidateGenerationError("CANDIDATE_REGISTRY_ENTRY_MISSING", "已冻结 Candidate 缺少 Registry 登记", status_code=503)
            expected_entry = self._candidate_registry_entry(proposal, preview, freeze_id=str(receipt.get("freeze_id") or ""), timestamp=str(receipt.get("timestamp") or ""))
            if any(registry_entry.get(key) != expected_entry.get(key) for key in expected_entry):
                modification = self._freeze_modification_from_paths(output_path)
                raise CandidateGenerationError("NEW_CANDIDATE_REQUIRED", "冻结后的 Candidate Registry 合同已变化，旧 Candidate 不可覆盖", status_code=409, details=modification)
        reviews = self._read_reviews(reviews_path)
        result = self._view(proposal, state=state, reviews=reviews, freeze_preview=preview, output_path=output_path)
        try:
            _assert_outcome_blind(result)
        except PerformanceLeakError as exc:
            raise CandidateGenerationError("OUTCOME_FIELD_BLOCKED", "候选建议只读视图包含被禁止的结果字段", status_code=503) from exc
        return result

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        """Read one proposal without changing state or any research ledger."""
        with self._mutex:
            return self._load_view(proposal_id)

    read_proposal = get_proposal
    get_candidate_proposal = get_proposal

    @staticmethod
    def _status_matches(state: str, status: str | None) -> bool:
        value = str(status or "").upper()
        if not value or value in {"ALL", "全部"}:
            return True
        if value in {"PENDING", "待审核"}:
            return state in {CANDIDATE_PROPOSAL_READY, HUMAN_REVIEW_REQUIRED}
        if value in {"APPROVED", "已批准"}:
            return state in {APPROVED, FREEZE_PREVIEW_READY, _LEGACY_CANDIDATE_FREEZE_READY, FROZEN, CANDIDATE_GOVERNANCE_FROZEN, EXECUTABLE_CANDIDATE_FROZEN, READY_FOR_STRUCTURAL_PREFLIGHT}
        if value in {"FROZEN", "已冻结"}:
            return state in {FROZEN, CANDIDATE_GOVERNANCE_FROZEN, EXECUTABLE_CANDIDATE_FROZEN, READY_FOR_STRUCTURAL_PREFLIGHT}
        if value in {"REJECTED", "已拒绝"}:
            return state in {REJECTED, CLOSED}
        return state == value

    def list_proposals(self, *, objective_id: str | None = None, status: str | None = None) -> dict[str, Any]:
        with self._mutex:
            objective_filter = _safe_id(objective_id, kind="objective_id") if objective_id else None
            items: list[dict[str, Any]] = []
            if self.proposal_root.exists():
                for path in sorted(self.proposal_root.rglob(CANDIDATE_PROPOSAL_FILENAME), key=lambda item: item.as_posix()):
                    if not path.is_file():
                        continue
                    payload = _read_json(path, code="CANDIDATE_PROPOSAL_UNREADABLE")
                    if payload is None:
                        continue
                    path_objective = str(payload.get("objective_id") or path.parent.name)
                    if objective_filter and path_objective != objective_filter:
                        continue
                    try:
                        item = self._load_view(str(payload.get("proposal_id") or ""))
                    except CandidateGenerationError:
                        raise
                    if self._status_matches(str(item.get("governance_state") or item.get("status") or ""), status):
                        items.append(item)
            return {
                "schema_version": CANDIDATE_PROPOSAL_VIEW_SCHEMA_VERSION,
                "objective_id": objective_filter,
                "status_filter": status or "ALL",
                "items": items,
                "total": len(items),
                "outcome_blind": True,
                "performance_data_loaded": False,
                "outcome_fields_available": False,
            }

    def _validate_existing_or_create(self, objective_id: str, context: CandidateGenerationInputV1, output_path: Path, state_path: Path) -> dict[str, Any] | None:
        if not output_path.exists():
            return None
        existing = _read_json(output_path, code="CANDIDATE_PROPOSAL_UNREADABLE")
        if existing is None:
            return None
        self._validate_persisted(existing, context)
        state = _read_json(state_path, code="CANDIDATE_PROPOSAL_STATE_UNREADABLE", required=False)
        if state is None:
            _atomic_write_json(state_path, self._state_for(existing, state=CANDIDATE_PROPOSAL_READY, history=[GENERATED, CANDIDATE_PROPOSAL_READY]))
        else:
            self._validate_state(state, existing)
        return {**dict(existing), "idempotent": True}

    @mutation_boundary()
    def generate_proposal(self, objective_id: str) -> dict[str, Any]:
        """Explicitly derive one Candidate Proposal; never create a Candidate."""
        objective_id = _safe_id(objective_id, kind="objective_id")
        with self._mutex:
            context = self.build_input(objective_id)
            input_path, output_path, state_path, _, _ = self._paths(objective_id)
            existing = self._validate_existing_or_create(objective_id, context, output_path, state_path)
            if existing is not None:
                return existing
            # Validate novelty and assemble the immutable proposal before any
            # durable Candidate-generation artifact is written.  A rejected
            # duplicate therefore leaves no misleading proposal directory.
            proposal = self._build_proposal(context)
            _atomic_write_json(input_path, context.to_dict())
            if self.crash_at == "after_input":
                raise RuntimeError("simulated crash after Candidate Proposal input")
            _atomic_write_json(output_path, proposal)
            if self.crash_at == "after_output":
                raise RuntimeError("simulated crash after Candidate Proposal output")
            _atomic_write_json(state_path, self._state_for(proposal, state=CANDIDATE_PROPOSAL_READY, history=[GENERATED, CANDIDATE_PROPOSAL_READY]))
            return dict(proposal)

    generate = generate_proposal
    generate_candidate_proposal = generate_proposal
    run = generate_proposal
    create_proposal = generate_proposal
    create_candidate_proposal = generate_proposal

    def _build_freeze_preview(self, proposal: Mapping[str, Any]) -> dict[str, Any]:
        candidate_hash = self._candidate_hash_for_proposal(proposal)
        preview = {
            "schema_version": CANDIDATE_FREEZE_PREVIEW_SCHEMA_VERSION,
            "preview_id": f"CANDIDATE_FREEZE_PREVIEW_{candidate_hash[:24].upper()}",
            "status": FREEZE_PREVIEW_READY,
            "objective_id": proposal.get("objective_id"),
            "proposal_id": proposal.get("proposal_id"),
            "proposal_hash": proposal.get("proposal_hash"),
            "candidate_id": self._candidate_id_for_hash(candidate_hash),
            "candidate_hash": candidate_hash,
            "candidate_identity_status": "PREVIEW_ONLY_NOT_REGISTERED",
            "candidate_name": proposal.get("candidate_name"),
            "mechanism_family": proposal.get("mechanism_family"),
            "factor_contract": proposal.get("factor_contract"),
            "execution_contract": proposal.get("execution_contract"),
            "data_contract": proposal.get("data_contract"),
            "multiple_testing_family_id": proposal.get("multiple_testing_family_id"),
            "budget": proposal.get("budget"),
            "lineage": proposal.get("lineage"),
            "research_lineage": proposal.get("lineage"),
            "requires_human_confirmation": True,
            "next_action": HUMAN_CONFIRM_CANDIDATE_FREEZE,
            "candidate_created": False,
            "candidate_frozen": False,
            "structural_preflight_started": False,
            "trial_started": False,
            "ai_called": False,
            "budget_consumed": False,
            "generated_at": self._now(),
        }
        _assert_outcome_blind(preview)
        preview["preview_hash"] = stable_hash({key: value for key, value in preview.items() if key != "preview_hash"})
        return preview

    def get_freeze_preview(self, proposal_id: str) -> dict[str, Any]:
        """Read the already materialized freeze preview; never create it."""
        with self._mutex:
            view = self._load_view(proposal_id)
            if str(view.get("governance_state") or "") not in {APPROVED, FREEZE_PREVIEW_READY, _LEGACY_CANDIDATE_FREEZE_READY, FROZEN, CANDIDATE_GOVERNANCE_FROZEN, EXECUTABLE_CANDIDATE_FROZEN, READY_FOR_STRUCTURAL_PREFLIGHT}:
                raise CandidateGenerationError("CANDIDATE_FREEZE_NOT_READY", "Candidate Proposal 尚未通过人工审核，不能查看冻结预览", status_code=409)
            preview = view.get("freeze_preview")
            if not isinstance(preview, Mapping):
                raise CandidateGenerationError("CANDIDATE_FREEZE_PREVIEW_NOT_FOUND", "未找到 Candidate Freeze Preview", status_code=404)
            return dict(preview)

    freeze_preview = get_freeze_preview
    preview_freeze = get_freeze_preview
    get_candidate_freeze_preview = get_freeze_preview

    def _materialize_state(self, proposal: Mapping[str, Any], state: str, history: list[str], reviews: list[Mapping[str, Any]]) -> None:
        _, _, state_path, _, _ = self._paths_for_proposal(str(proposal.get("proposal_id") or ""))
        review_ids = [str(item.get("review_id") or item.get("record_id") or "") for item in reviews if item.get("review_id") or item.get("record_id")]
        _atomic_write_json(state_path, self._state_for(proposal, state=state, history=history, review_ids=review_ids))

    @mutation_boundary(proposal=True)
    def review(
        self,
        proposal_id: str,
        action: str | Mapping[str, Any],
        reviewer: str | None = None,
        *,
        review_id: str | None = None,
        expected_proposal_hash: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Apply explicit human approve/reject; approval only prepares Freeze Preview."""
        with self._mutex:
            body: dict[str, Any] = dict(action) if isinstance(action, Mapping) else {}
            if body:
                action = str(body.get("action") or body.get("decision") or "")
                reviewer = body.get("reviewer") or body.get("reviewer_id") or reviewer
                review_id = body.get("review_id") or review_id
                expected_proposal_hash = body.get("proposal_hash") or body.get("expected_proposal_hash") or expected_proposal_hash
                reason = body.get("reason") or reason
            normalized = str(action or "").strip().casefold()
            if normalized not in {"approve", "reject"}:
                raise CandidateGenerationError("REVIEW_ACTION_NOT_SUPPORTED", "审核动作只支持 approve 或 reject", status_code=400)
            reviewer_value = self._safe_reviewer(reviewer)
            output_path = self._find_proposal_path(proposal_id)
            objective_id = output_path.parent.name
            context = self.build_input(objective_id)
            proposal = _read_json(output_path, code="CANDIDATE_PROPOSAL_UNREADABLE")
            if proposal is None:
                raise CandidateGenerationError("CANDIDATE_PROPOSAL_NOT_FOUND", "未找到请求的 Candidate Proposal", status_code=404)
            self._validate_persisted(proposal, context)
            proposal_hash = str(proposal.get("proposal_hash") or "")
            if expected_proposal_hash not in (None, "") and str(expected_proposal_hash) != proposal_hash:
                raise CandidateGenerationError("STALE_CANDIDATE_PROPOSAL", "Candidate Proposal 已变化，请重新读取后审核", status_code=409)
            state_path = output_path.parent / CANDIDATE_PROPOSAL_STATE_FILENAME
            preview_path = output_path.parent / CANDIDATE_FREEZE_PREVIEW_FILENAME
            reviews_path = output_path.parent / CANDIDATE_REVIEWS_FILENAME
            state = _read_json(state_path, code="CANDIDATE_PROPOSAL_STATE_UNREADABLE", required=False)
            current = str(state.get("status") if state else CANDIDATE_PROPOSAL_READY)
            reviews = self._read_reviews(reviews_path)
            existing_action = next((item for item in reversed(reviews) if str(item.get("action") or "") == normalized), None)
            if normalized == "approve" and current in {APPROVED, FREEZE_PREVIEW_READY, _LEGACY_CANDIDATE_FREEZE_READY, FROZEN, CANDIDATE_GOVERNANCE_FROZEN, EXECUTABLE_CANDIDATE_FROZEN, READY_FOR_STRUCTURAL_PREFLIGHT}:
                return {"schema_version": CANDIDATE_REVIEW_SCHEMA_VERSION, "review": existing_action or {}, "proposal": self._load_view(str(proposal.get("proposal_id") or "")), "freeze_preview": self.get_freeze_preview(str(proposal.get("proposal_id") or "")), "idempotent": True}
            if normalized == "reject" and current in {REJECTED, CLOSED}:
                return {"schema_version": CANDIDATE_REVIEW_SCHEMA_VERSION, "review": existing_action or {}, "proposal": self._load_view(str(proposal.get("proposal_id") or "")), "freeze_preview": None, "idempotent": True}
            if current not in {CANDIDATE_PROPOSAL_READY, HUMAN_REVIEW_REQUIRED}:
                raise CandidateGenerationError("INVALID_CANDIDATE_PROPOSAL_STATE_TRANSITION", f"Candidate Proposal 当前状态 {current} 不允许审核", status_code=409)
            history = list((state or {}).get("state_history") or proposal.get("state_history") or [GENERATED, CANDIDATE_PROPOSAL_READY])
            if HUMAN_REVIEW_REQUIRED not in history:
                history.append(HUMAN_REVIEW_REQUIRED)
            if existing_action is not None:
                if review_id not in (None, "") and str(review_id) != str(existing_action.get("review_id") or ""):
                    raise CandidateGenerationError("REVIEW_IN_PROGRESS", "当前 Candidate Proposal 已存在另一条审核记录", status_code=409)
                record = dict(existing_action)
                added = False
            else:
                generated_id = f"REVIEW_{stable_hash({'proposal_id': proposal.get('proposal_id'), 'proposal_hash': proposal_hash, 'action': normalized, 'reviewer': reviewer_value, 'reason': str(reason or '')})[:24].upper()}"
                review_id_value = _safe_id(review_id or generated_id, kind="review_id")
                record = {
                    "schema_version": CANDIDATE_REVIEW_SCHEMA_VERSION,
                    "review_id": review_id_value,
                    "proposal_id": proposal.get("proposal_id"),
                    "action": normalized,
                    "reviewer": reviewer_value,
                    "timestamp": self._now(),
                    "proposal_hash": proposal_hash,
                    "previous_state": HUMAN_REVIEW_REQUIRED,
                    "resulting_state": APPROVED if normalized == "approve" else REJECTED,
                    "next_state": FREEZE_PREVIEW_READY if normalized == "approve" else CLOSED,
                }
                if reason not in (None, ""):
                    record["reason"] = str(reason)
                _append_jsonl(reviews_path, record)
                added = True
                reviews.append(record)
            if self.crash_at == "after_review_record":
                raise RuntimeError("SYNTHETIC_CANDIDATE_GOVERNANCE_CRASH:after_review_record")
            if normalized == "reject":
                history.append(REJECTED)
                self._materialize_state(proposal, REJECTED, history, reviews)
                return {"schema_version": CANDIDATE_REVIEW_SCHEMA_VERSION, "review": record, "proposal": self._load_view(str(proposal.get("proposal_id") or "")), "freeze_preview": None, "idempotent": not added, "message_zh": "Candidate Proposal 已拒绝，未创建 Candidate。"}
            preview = _read_json(preview_path, code="CANDIDATE_FREEZE_PREVIEW_UNREADABLE", required=False)
            if preview is None:
                preview = self._build_freeze_preview(proposal)
                _atomic_write_json(preview_path, preview)
            if self.crash_at == "after_freeze_preview":
                raise RuntimeError("SYNTHETIC_CANDIDATE_GOVERNANCE_CRASH:after_freeze_preview")
            if APPROVED not in history:
                history.append(APPROVED)
            if FREEZE_PREVIEW_READY not in history:
                history.append(FREEZE_PREVIEW_READY)
            self._materialize_state(proposal, FREEZE_PREVIEW_READY, history, reviews)
            return {"schema_version": CANDIDATE_REVIEW_SCHEMA_VERSION, "review": record, "proposal": self._load_view(str(proposal.get("proposal_id") or "")), "freeze_preview": dict(preview), "idempotent": not added, "message_zh": "Candidate Proposal 已批准；已生成冻结预览，等待第二次人工确认。"}

    review_proposal = review

    def _freeze_modification_from_paths(self, output_path: Path) -> dict[str, Any]:
        proposal = _read_json(output_path, code="CANDIDATE_PROPOSAL_UNREADABLE") or {}
        preview = _read_json(output_path.parent / CANDIDATE_FREEZE_PREVIEW_FILENAME, code="CANDIDATE_FREEZE_PREVIEW_UNREADABLE", required=False)
        receipt = _read_json(output_path.parent / CANDIDATE_FREEZE_RECEIPT_FILENAME, code="CANDIDATE_FREEZE_RECEIPT_UNREADABLE", required=False)
        objective_id = str(proposal.get("objective_id") or output_path.parent.name)
        registry_path = self._candidate_registry_path(objective_id)
        registry_raw = _read_json(registry_path, code="CANDIDATE_REGISTRY_UNREADABLE", required=False)
        frozen_hash = str((receipt or {}).get("candidate_hash") or "")
        frozen_id = str((receipt or {}).get("candidate_id") or "")
        registry_entry = None
        registry_error = None
        if registry_raw is not None:
            try:
                registry = self._read_candidate_registry(objective_id)
                registry_entry = next((item for item in (registry or {}).get("candidates", ()) if isinstance(item, Mapping) and str(item.get("candidate_id") or "") == frozen_id), None)
                if registry_entry is not None:
                    frozen_hash = frozen_hash or str(registry_entry.get("candidate_hash") or "")
                    frozen_id = frozen_id or str(registry_entry.get("candidate_id") or "")
            except CandidateGenerationError as exc:
                registry_error = exc.code
        if not receipt and registry_entry is None:
            return {"status": "NOT_FROZEN", "modified": False, "candidate_id": None, "candidate_hash": None}
        current_hash = self._candidate_hash_for_proposal(proposal)
        expected_id = self._candidate_id_for_hash(current_hash)
        changed_contracts = [
            key
            for key in ("factor_contract", "execution_contract", "data_contract")
            if isinstance(registry_entry, Mapping)
            and (registry_entry.get("contract") or {}).get(key) != proposal.get(key)
        ]
        preview_changed = bool(receipt and preview and (str(preview.get("candidate_hash") or "") != frozen_hash or str(preview.get("preview_hash") or "") != str(receipt.get("preview_hash") or "")))
        changed = bool(registry_error) or preview_changed or current_hash != frozen_hash or str(proposal.get("proposal_hash") or "") != str((receipt or {}).get("proposal_hash") or proposal.get("proposal_hash") or "") or bool(changed_contracts)
        if not changed:
            return {"status": FROZEN, "modified": False, "candidate_id": frozen_id, "candidate_hash": frozen_hash}
        return {
            "status": NEW_CANDIDATE,
            "modified": True,
            "old_candidate_id": frozen_id,
            "old_candidate_hash": frozen_hash,
            "new_candidate_id": expected_id,
            "new_candidate_hash": current_hash,
            "changed_contracts": changed_contracts,
            "preview_changed": preview_changed,
            "registry_error": registry_error,
            "overwrite_forbidden": True,
            "message_zh": "冻结后合同发生变化，旧 Candidate 保留；必须作为 NEW_CANDIDATE 重新审核冻结。",
        }

    def detect_frozen_modification(self, proposal_id: str) -> dict[str, Any]:
        """Detect post-freeze contract edits without mutating the frozen record."""
        with self._mutex:
            return self._freeze_modification_from_paths(self._find_proposal_path(proposal_id))

    verify_frozen_candidate = detect_frozen_modification
    check_frozen_modification = detect_frozen_modification

    def _validate_freeze_record(self, record: Mapping[str, Any], proposal: Mapping[str, Any], preview: Mapping[str, Any]) -> None:
        expected_hash = str(preview.get("candidate_hash") or "")
        if not self._freeze_record_matches(record, {
            "proposal_id": proposal.get("proposal_id"),
            "proposal_hash": proposal.get("proposal_hash"),
            "candidate_id": self._candidate_id_for_hash(expected_hash),
            "candidate_hash": expected_hash,
            "preview_hash": preview.get("preview_hash"),
            "freeze_id": record.get("freeze_id"),
        }):
            raise CandidateGenerationError("CANDIDATE_FREEZE_RECORD_CONFLICT", "Candidate Freeze 治理记录与当前 Preview 冲突", status_code=503)
        record_hash = str(record.get("record_hash") or "")
        if not record_hash or record_hash != stable_hash({key: value for key, value in record.items() if key != "record_hash" and key not in {"action", "record_id", "previous_state", "next_state", "freeze_state"}}):
            raise CandidateGenerationError("CANDIDATE_FREEZE_RECORD_HASH_INVALID", "Candidate Freeze 治理记录哈希校验失败", status_code=503)

    def _candidate_registry_entry(self, proposal: Mapping[str, Any], preview: Mapping[str, Any], *, freeze_id: str, timestamp: str) -> dict[str, Any]:
        base = {
            "candidate_id": self._candidate_id_for_hash(str(preview.get("candidate_hash") or "")),
            "candidate_hash": preview.get("candidate_hash"),
            "objective_id": proposal.get("objective_id"),
            "proposal_id": proposal.get("proposal_id"),
            "proposal_hash": proposal.get("proposal_hash"),
            "contract": self._candidate_contract(proposal),
            "lineage": proposal.get("lineage"),
            "creation_reason": "HUMAN_CONFIRMED_CANDIDATE_FREEZE",
            "state": FROZEN,
            "governance_state": CANDIDATE_GOVERNANCE_FROZEN,
            "executable_candidate_frozen": False,
            "structural_preflight_ready": False,
            "freeze_id": freeze_id,
            "frozen_at": timestamp,
            "multiple_testing_family_id": proposal.get("multiple_testing_family_id"),
            "performance_data_loaded": False,
            "budget_consumed": False,
        }
        base["entry_hash"] = stable_hash(base)
        return base

    def _materialize_freeze(self, proposal: Mapping[str, Any], preview: Mapping[str, Any], record: Mapping[str, Any], reviews: list[Mapping[str, Any]], *, state: Mapping[str, Any] | None, idempotent: bool) -> dict[str, Any]:
        proposal_id = str(proposal.get("proposal_id") or "")
        objective_id = str(proposal.get("objective_id") or "")
        output_path = self._find_proposal_path(proposal_id)
        freeze_id = str(record.get("freeze_id") or "")
        timestamp = str(record.get("timestamp") or self._now())
        governance_path = output_path.parent / CANDIDATE_FREEZE_GOVERNANCE_FILENAME
        receipt_path = output_path.parent / CANDIDATE_FREEZE_RECEIPT_FILENAME
        candidate_id = self._candidate_id_for_hash(str(preview.get("candidate_hash") or ""))
        governance_base = {
            **dict(record),
            "action": "FREEZE_CANDIDATE",
            "previous_state": FREEZE_PREVIEW_READY,
            "resulting_state": CANDIDATE_GOVERNANCE_FROZEN,
            "next_state": CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW,
            "candidate_registry_ref": _relative(self.root, self._candidate_registry_path(objective_id)),
            "lineage": proposal.get("lineage"),
            "creation_reason": "HUMAN_CONFIRMED_CANDIDATE_FREEZE",
            "automatic_structural_preflight": False,
            "automatic_trial_started": False,
        }
        governance = _read_json(governance_path, code="CANDIDATE_FREEZE_GOVERNANCE_UNREADABLE", required=False)
        if governance is None:
            governance = {**governance_base, "governance_hash": stable_hash(governance_base)}
            _atomic_write_json(governance_path, governance)
        elif not self._freeze_record_matches(governance, record) or str(governance.get("governance_hash") or "") != stable_hash({key: value for key, value in governance.items() if key != "governance_hash"}):
            raise CandidateGenerationError("CANDIDATE_FREEZE_GOVERNANCE_CONFLICT", "已有 Candidate Freeze 治理记录与当前确认冲突", status_code=503)
        if self.crash_at in {"after_freeze_record", "after_freeze_governance"}:
            raise RuntimeError("SYNTHETIC_CANDIDATE_GOVERNANCE_CRASH:after_freeze_record")

        receipt = _read_json(receipt_path, code="CANDIDATE_FREEZE_RECEIPT_UNREADABLE", required=False)
        receipt_base = {
            **dict(record),
            "receipt_status": "CONFIRMED",
            "result_state": CANDIDATE_GOVERNANCE_FROZEN,
            "governance_state": CANDIDATE_GOVERNANCE_FROZEN,
            "next_state": CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW,
            "next_action": CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW,
            "executable_candidate_frozen": False,
            "structural_preflight_ready": False,
            "candidate_registry_ref": _relative(self.root, self._candidate_registry_path(objective_id)),
            "lineage_ref": _relative(self.root, self._lineage_path(objective_id)),
            "creation_reason": "HUMAN_CONFIRMED_CANDIDATE_FREEZE",
            "automatic_structural_preflight": False,
            "automatic_trial_started": False,
            "candidate_created": True,
            "candidate_frozen": True,
            "candidate_id": candidate_id,
        }
        if receipt is None:
            receipt = {**receipt_base, "receipt_hash": stable_hash(receipt_base)}
            _atomic_write_json(receipt_path, receipt)
        elif not self._freeze_record_matches(receipt, record) or str(receipt.get("receipt_hash") or "") != stable_hash({key: value for key, value in receipt.items() if key != "receipt_hash"}):
            raise CandidateGenerationError("CANDIDATE_FREEZE_RECEIPT_CONFLICT", "已有 Candidate Freeze 回执与当前确认冲突", status_code=503)
        if self.crash_at in {"after_freeze_receipt", "after_receipt_write"}:
            raise RuntimeError("SYNTHETIC_CANDIDATE_GOVERNANCE_CRASH:after_freeze_receipt")

        entry = self._candidate_registry_entry(proposal, preview, freeze_id=freeze_id, timestamp=timestamp)
        registry, created = self._append_candidate_registry(objective_id, entry)
        if self.crash_at in {"after_candidate_registry", "after_candidate_registry_write"}:
            raise RuntimeError("SYNTHETIC_CANDIDATE_GOVERNANCE_CRASH:after_candidate_registry")
        current = str((state or {}).get("status") or FREEZE_PREVIEW_READY)
        history = list((state or {}).get("state_history") or proposal.get("state_history") or [GENERATED, CANDIDATE_PROPOSAL_READY, HUMAN_REVIEW_REQUIRED, FREEZE_PREVIEW_READY])
        if FROZEN not in history:
            history.append(FROZEN)
        if CANDIDATE_GOVERNANCE_FROZEN not in history:
            history.append(CANDIDATE_GOVERNANCE_FROZEN)
        if current not in {FROZEN, CANDIDATE_GOVERNANCE_FROZEN, EXECUTABLE_CANDIDATE_FROZEN, READY_FOR_STRUCTURAL_PREFLIGHT}:
            self._materialize_state(proposal, CANDIDATE_GOVERNANCE_FROZEN, history, reviews)
            if self.crash_at in {"after_frozen_state", "after_candidate_freeze"}:
                raise RuntimeError("SYNTHETIC_CANDIDATE_GOVERNANCE_CRASH:after_frozen_state")
        if current in {FROZEN, CANDIDATE_GOVERNANCE_FROZEN} and current != CANDIDATE_GOVERNANCE_FROZEN:
            self._materialize_state(proposal, CANDIDATE_GOVERNANCE_FROZEN, history, reviews)
        view = self._load_view(proposal_id)
        registry_entry = next((dict(item) for item in registry.get("candidates", ()) if isinstance(item, Mapping) and str(item.get("candidate_id") or "") == candidate_id), entry)
        return {
            "schema_version": CANDIDATE_FREEZE_SCHEMA_VERSION,
            "freeze": dict(receipt),
            "candidate": registry_entry,
            "proposal": view,
            "idempotent": bool(idempotent and not created),
            "message_zh": "Candidate Governance Freeze 已按人工确认完成；当前等待执行合同预览，未自动执行 Structural Preflight 或 Trial。",
        }

    @mutation_boundary(proposal=True)
    def freeze(
        self,
        proposal_id: str,
        payload: Mapping[str, Any] | None = None,
        reviewer: str | None = None,
        *,
        freeze_id: str | None = None,
        expected_proposal_hash: str | None = None,
        expected_candidate_hash: str | None = None,
        confirmed: bool | None = None,
        action: str | None = None,
    ) -> dict[str, Any]:
        """Confirm exactly one Candidate Freeze; this is the only registry write."""
        with self._mutex:
            body = dict(payload or {})
            if confirmed is not None and "confirmed" not in body:
                body["confirmed"] = confirmed
            if action is not None and "action" not in body:
                body["action"] = action
            reviewer = body.get("reviewer") or body.get("confirm_reviewer") or reviewer
            freeze_id = body.get("freeze_id") or freeze_id
            expected_proposal_hash = body.get("proposal_hash") or body.get("expected_proposal_hash") or expected_proposal_hash
            expected_candidate_hash = body.get("candidate_hash") or body.get("expected_candidate_hash") or expected_candidate_hash
            if body.get("confirmed") is not True:
                raise CandidateGenerationError("CONFIRMATION_REQUIRED", "冻结 Candidate 需要明确的第二次人工确认", status_code=400)
            action = str(body.get("action") or body.get("governance_action") or "FREEZE_CANDIDATE").upper()
            if action not in {"FREEZE_CANDIDATE", "CONFIRM_FREEZE", "FREEZE"}:
                raise CandidateGenerationError("FREEZE_ACTION_REQUIRED", "最终确认动作必须是 FREEZE_CANDIDATE", status_code=400)
            reviewer_value = self._safe_reviewer(reviewer)
            output_path = self._find_proposal_path(proposal_id)
            modification = self._freeze_modification_from_paths(output_path)
            if modification.get("status") == NEW_CANDIDATE:
                raise CandidateGenerationError("NEW_CANDIDATE_REQUIRED", str(modification.get("message_zh")), status_code=409, details=modification)
            objective_id = output_path.parent.name
            context = self.build_input(objective_id)
            proposal = _read_json(output_path, code="CANDIDATE_PROPOSAL_UNREADABLE")
            if proposal is None:
                raise CandidateGenerationError("CANDIDATE_PROPOSAL_NOT_FOUND", "未找到请求的 Candidate Proposal", status_code=404)
            self._validate_persisted(proposal, context)
            proposal_hash = str(proposal.get("proposal_hash") or "")
            if expected_proposal_hash not in (None, "") and str(expected_proposal_hash) != proposal_hash:
                raise CandidateGenerationError("STALE_CANDIDATE_PROPOSAL", "Candidate Proposal 已变化，请重新读取后冻结", status_code=409)
            preview_path = output_path.parent / CANDIDATE_FREEZE_PREVIEW_FILENAME
            preview = _read_json(preview_path, code="CANDIDATE_FREEZE_PREVIEW_NOT_FOUND", required=False)
            if preview is None:
                raise CandidateGenerationError("CANDIDATE_FREEZE_PREVIEW_NOT_FOUND", "必须先通过第一道人审生成 Freeze Preview", status_code=409)
            self._validate_freeze_preview(proposal, preview)
            candidate_hash = str(preview.get("candidate_hash") or "")
            if expected_candidate_hash not in (None, "") and str(expected_candidate_hash) != candidate_hash:
                raise CandidateGenerationError("STALE_CANDIDATE_FREEZE_PREVIEW", "Candidate hash 与当前 Freeze Preview 不一致", status_code=409)
            state_path = output_path.parent / CANDIDATE_PROPOSAL_STATE_FILENAME
            state = _read_json(state_path, code="CANDIDATE_PROPOSAL_STATE_UNREADABLE", required=False)
            current = str(state.get("status") if state else CANDIDATE_PROPOSAL_READY)
            if current in {FROZEN, CANDIDATE_GOVERNANCE_FROZEN, EXECUTABLE_CANDIDATE_FROZEN, READY_FOR_STRUCTURAL_PREFLIGHT}:
                receipt = _read_json(output_path.parent / CANDIDATE_FREEZE_RECEIPT_FILENAME, code="CANDIDATE_FREEZE_RECEIPT_UNREADABLE")
                existing_freeze_id = str(receipt.get("freeze_id") or "")
                if freeze_id not in (None, "") and str(freeze_id) != existing_freeze_id:
                    raise CandidateGenerationError("CANDIDATE_FREEZE_IDENTITY_CONFLICT", "同一 Candidate 已存在另一条冻结确认", status_code=409)
                record = dict(receipt)
                reviews = self._read_reviews(output_path.parent / CANDIDATE_REVIEWS_FILENAME)
                return self._materialize_freeze(proposal, preview, record, reviews, state=state, idempotent=True)
            if current not in {APPROVED, FREEZE_PREVIEW_READY, _LEGACY_CANDIDATE_FREEZE_READY}:
                raise CandidateGenerationError("CANDIDATE_FREEZE_NOT_READY", f"Candidate Proposal 当前状态 {current} 不允许冻结", status_code=409)
            generated_id = f"FREEZE_{stable_hash({'proposal_id': proposal.get('proposal_id'), 'proposal_hash': proposal_hash, 'candidate_hash': candidate_hash})[:24].upper()}"
            freeze_id_value = _safe_id(freeze_id or generated_id, kind="freeze_id")
            reviews_path = output_path.parent / CANDIDATE_REVIEWS_FILENAME
            reviews = self._read_reviews(reviews_path)
            existing = next((item for item in reversed(reviews) if str(item.get("action") or "") == "freeze"), None)
            if existing is not None:
                if str(existing.get("freeze_id") or existing.get("record_id") or "") != freeze_id_value:
                    raise CandidateGenerationError("CANDIDATE_FREEZE_IDENTITY_CONFLICT", "当前 Candidate Proposal 已存在另一条冻结确认", status_code=409)
                record = dict(existing)
                idempotent = True
            else:
                core = self._freeze_entry(proposal, preview, freeze_id=freeze_id_value, reviewer=reviewer_value, timestamp=self._now())
                record = {**core, "record_id": freeze_id_value, "action": "freeze", "previous_state": current, "freeze_state": FROZEN, "next_state": READY_FOR_STRUCTURAL_PREFLIGHT}
                _append_jsonl(reviews_path, record)
                reviews.append(record)
                idempotent = False
            self._validate_freeze_record(record, proposal, preview)
            if self.crash_at in {"after_freeze_record", "after_freeze_review_record"}:
                raise RuntimeError("SYNTHETIC_CANDIDATE_GOVERNANCE_CRASH:after_freeze_record")
            return self._materialize_freeze(proposal, preview, record, reviews, state=state, idempotent=idempotent)

    confirm_freeze = freeze
    freeze_candidate = freeze
    confirm_candidate_freeze = freeze

    @mutation_boundary(proposal=True)
    def close(self, proposal_id: str, *, reviewer: str, reason: str | None = None) -> dict[str, Any]:
        with self._mutex:
            output_path = self._find_proposal_path(proposal_id)
            proposal = _read_json(output_path, code="CANDIDATE_PROPOSAL_UNREADABLE")
            if proposal is None:
                raise CandidateGenerationError("CANDIDATE_PROPOSAL_NOT_FOUND", "未找到请求的 Candidate Proposal", status_code=404)
            state_path = output_path.parent / CANDIDATE_PROPOSAL_STATE_FILENAME
            state = _read_json(state_path, code="CANDIDATE_PROPOSAL_STATE_UNREADABLE", required=False)
            current = str(state.get("status") if state else CANDIDATE_PROPOSAL_READY)
            if current == CLOSED:
                return self._load_view(str(proposal.get("proposal_id") or ""))
            if current != REJECTED:
                raise CandidateGenerationError("INVALID_CANDIDATE_PROPOSAL_STATE_TRANSITION", "只有 REJECTED Candidate Proposal 可以关闭", status_code=409)
            reviews_path = output_path.parent / CANDIDATE_REVIEWS_FILENAME
            reviews = self._read_reviews(reviews_path)
            record = {
                "schema_version": CANDIDATE_REVIEW_SCHEMA_VERSION,
                "record_id": f"CLOSE_{stable_hash({'proposal_id': proposal.get('proposal_id'), 'proposal_hash': proposal.get('proposal_hash')})[:24].upper()}",
                "proposal_id": proposal.get("proposal_id"),
                "action": "close",
                "reviewer": self._safe_reviewer(reviewer),
                "timestamp": self._now(),
                "proposal_hash": proposal.get("proposal_hash"),
                "previous_state": REJECTED,
                "resulting_state": CLOSED,
            }
            if reason not in (None, ""):
                record["reason"] = str(reason)
            if not any(str(item.get("record_id") or "") == str(record["record_id"]) for item in reviews):
                _append_jsonl(reviews_path, record)
                reviews.append(record)
            history = list((state or {}).get("state_history") or proposal.get("state_history") or [GENERATED, CANDIDATE_PROPOSAL_READY, HUMAN_REVIEW_REQUIRED, REJECTED])
            if history[-1] != CLOSED:
                history.append(CLOSED)
            self._materialize_state(proposal, CLOSED, history, reviews)
            return self._load_view(str(proposal.get("proposal_id") or ""))

    @mutation_boundary()
    def recover(self, objective_id: str) -> dict[str, Any]:
        """Recover durable governance artifacts, including an already confirmed Freeze."""
        objective_id = _safe_id(objective_id, kind="objective_id")
        with self._mutex:
            context = self.build_input(objective_id)
            input_path, output_path, state_path, preview_path, reviews_path = self._paths(objective_id)
            if not output_path.exists():
                return {"objective_id": objective_id, "status": NEED_CANDIDATE_PROPOSAL, "available": False, "recovered": False}
            proposal = _read_json(output_path, code="CANDIDATE_PROPOSAL_UNREADABLE")
            if proposal is None:
                raise CandidateGenerationError("CANDIDATE_PROPOSAL_NOT_FOUND", "未找到 Candidate Proposal", status_code=404)
            self._validate_persisted(proposal, context)
            reviews = self._read_reviews(reviews_path)
            state = _read_json(state_path, code="CANDIDATE_PROPOSAL_STATE_UNREADABLE", required=False)
            recovered = False
            freeze_record = next((dict(item) for item in reversed(reviews) if str(item.get("action") or "") == "freeze"), None)
            receipt = _read_json(output_path.parent / CANDIDATE_FREEZE_RECEIPT_FILENAME, code="CANDIDATE_FREEZE_RECEIPT_UNREADABLE", required=False)
            if freeze_record is not None or receipt is not None:
                preview = _read_json(preview_path, code="CANDIDATE_FREEZE_PREVIEW_NOT_FOUND", required=False)
                if preview is None:
                    raise CandidateGenerationError("CANDIDATE_FREEZE_PREVIEW_NOT_FOUND", "冻结确认缺少 Freeze Preview，恢复已停止", status_code=503)
                self._validate_freeze_preview(proposal, preview)
                record = freeze_record or dict(receipt or {})
                if freeze_record is not None:
                    self._validate_freeze_record(freeze_record, proposal, preview)
                if receipt is not None and not self._freeze_record_matches(receipt, record):
                    raise CandidateGenerationError("CANDIDATE_FREEZE_RECEIPT_CONFLICT", "冻结回执与冻结审核记录冲突", status_code=503)
                if freeze_record is None:
                    core_keys = ("schema_version", "freeze_id", "proposal_id", "proposal_hash", "candidate_id", "candidate_hash", "reviewer", "timestamp", "preview_hash", "resulting_state", "structural_preflight_started", "trial_started", "ai_called", "budget_consumed", "automatic_execution", "record_hash")
                    record = {key: receipt[key] for key in core_keys if key in receipt}
                    record.update({"record_id": record.get("freeze_id"), "action": "freeze", "previous_state": FREEZE_PREVIEW_READY, "freeze_state": FROZEN, "next_state": READY_FOR_STRUCTURAL_PREFLIGHT})
                    _append_jsonl(reviews_path, record)
                    reviews.append(record)
                    recovered = True
                if state is None or str(state.get("status") or "") not in {CANDIDATE_GOVERNANCE_FROZEN, EXECUTABLE_CANDIDATE_FROZEN, READY_FOR_STRUCTURAL_PREFLIGHT}:
                    self._materialize_freeze(proposal, preview, record, reviews, state=state, idempotent=True)
                    recovered = True
            elif state is None:
                approved = next((item for item in reversed(reviews) if item.get("action") == "approve"), None)
                rejected = next((item for item in reversed(reviews) if item.get("action") == "reject"), None)
                if approved:
                    if not preview_path.exists():
                        _atomic_write_json(preview_path, self._build_freeze_preview(proposal))
                    history = [GENERATED, CANDIDATE_PROPOSAL_READY, HUMAN_REVIEW_REQUIRED, APPROVED, FREEZE_PREVIEW_READY]
                    state = self._state_for(proposal, state=FREEZE_PREVIEW_READY, history=history, review_ids=[str(item.get("review_id")) for item in reviews if item.get("review_id")])
                elif rejected:
                    state = self._state_for(proposal, state=REJECTED, history=[GENERATED, CANDIDATE_PROPOSAL_READY, HUMAN_REVIEW_REQUIRED, REJECTED], review_ids=[str(item.get("review_id")) for item in reviews if item.get("review_id")])
                else:
                    state = self._state_for(proposal, state=CANDIDATE_PROPOSAL_READY, history=[GENERATED, CANDIDATE_PROPOSAL_READY])
                _atomic_write_json(state_path, state)
                recovered = True
            else:
                self._validate_state(state, proposal)
                if str(state.get("status") or "") == FREEZE_PREVIEW_READY and not preview_path.exists():
                    _atomic_write_json(preview_path, self._build_freeze_preview(proposal))
                    recovered = True
                elif preview_path.exists():
                    preview = _read_json(preview_path, code="CANDIDATE_FREEZE_PREVIEW_UNREADABLE")
                    if preview is not None:
                        self._validate_freeze_preview(proposal, preview)
            if not input_path.exists():
                _atomic_write_json(input_path, context.to_dict())
                recovered = True
            return {**self._load_view(str(proposal.get("proposal_id") or "")), "recovered": recovered, "idempotent": True}

    def recover_all(self) -> list[dict[str, Any]]:
        if not self.proposal_root.exists():
            return []
        results: list[dict[str, Any]] = []
        for path in sorted(self.proposal_root.iterdir(), key=lambda item: item.name):
            if path.is_dir() and _IDENTIFIER_RE.fullmatch(path.name) and (path / CANDIDATE_PROPOSAL_FILENAME).exists():
                results.append(self.recover(path.name))
        return results


CandidateGenerationManager = CandidateGenerationManagerV1
CandidateGenerationServiceV1 = CandidateGenerationManagerV1
CandidateGenerationGovernanceServiceV1 = CandidateGenerationManagerV1
CandidateProposalInputV1 = CandidateGenerationInputV1


def _main() -> int:
    parser = argparse.ArgumentParser(description="显式生成停在人工审核边界的 Candidate Proposal")
    parser.add_argument("--root", default=".", help="项目根目录")
    parser.add_argument("--objective-id", required=True)
    args = parser.parse_args()
    proposal = CandidateGenerationManagerV1(args.root).generate_proposal(args.objective_id)
    print(json.dumps({"状态": proposal["status"], "建议编号": proposal["proposal_id"], "下一步": proposal["governance"]["next_action"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "APPROVED",
    "CANDIDATE_GOVERNANCE_FROZEN",
    "CANDIDATE_FREEZE_GOVERNANCE_FILENAME",
    "CANDIDATE_FREEZE_PREVIEW_FILENAME",
    "CANDIDATE_FREEZE_PREVIEW_SCHEMA_VERSION",
    "CANDIDATE_FREEZE_RECEIPT_FILENAME",
    "CANDIDATE_FREEZE_SCHEMA_VERSION",
    "CANDIDATE_FREEZE_READY",
    "CANDIDATE_REGISTRY_FILENAME",
    "CANDIDATE_REGISTRY_SCHEMA_VERSION",
    "CANDIDATE_PROPOSAL_FILENAME",
    "CANDIDATE_PROPOSAL_INPUT_FILENAME",
    "CANDIDATE_PROPOSAL_INPUT_SCHEMA_VERSION",
    "CANDIDATE_PROPOSAL_READY",
    "CANDIDATE_PROPOSAL_SCHEMA_VERSION",
    "CANDIDATE_PROPOSAL_STATE_FILENAME",
    "CANDIDATE_PROPOSAL_STATE_SCHEMA_VERSION",
    "CANDIDATE_PROPOSAL_VIEW_SCHEMA_VERSION",
    "CANDIDATE_REVIEW_SCHEMA_VERSION",
    "CANDIDATE_REVIEWS_FILENAME",
    "CLOSED",
    "FREEZE_PREVIEW_READY",
    "FROZEN",
    "CandidateGenerationError",
    "CandidateGenerationGovernanceServiceV1",
    "CandidateGenerationInputV1",
    "CandidateGenerationManager",
    "CandidateGenerationManagerV1",
    "CandidateGenerationServiceV1",
    "CandidateFreezePreview",
    "CandidateFreezePreviewV1",
    "CandidateProposalInputV1",
    "DUPLICATE_MECHANISM_REJECTED",
    "GENERATED",
    "HUMAN_CONFIRM_CANDIDATE_FREEZE",
    "CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW",
    "HUMAN_CONFIRM_EXECUTABLE_MATERIALIZATION",
    "HUMAN_REVIEW_REQUIRED",
    "NEED_CANDIDATE_PROPOSAL",
    "NEW_CANDIDATE",
    "EXECUTABLE_CANDIDATE_FROZEN",
    "READY_FOR_STRUCTURAL_PREFLIGHT",
    "RUN_STRUCTURAL_PREFLIGHT",
    "REJECTED",
]
