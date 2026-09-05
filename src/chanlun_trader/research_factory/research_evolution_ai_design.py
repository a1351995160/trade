"""Outcome-blind AI research design for an evolution-created Objective.

The service is a narrow handoff boundary.  It reads the governed Proposal,
its failure landscape, mechanism coverage, the new Objective lineage and the
registered data capabilities.  It may persist one design artifact per
Objective, but it never creates a Candidate, starts a Trial, calls the
predictive executor or changes a budget ledger.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import argparse
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Protocol

from .common import jsonable, now_timestamp, stable_hash
from .context import PerformanceBlindGuard, PerformanceLeakError
from .research_evolution_proposal import MechanismCoverageRegistryV1
from .research_proposal_governance import (
    ResearchProposalGovernanceError,
    ResearchProposalGovernanceServiceV1,
)


OBJECTIVE_CREATED = "OBJECTIVE_CREATED"
AI_DESIGN_READY = "AI_DESIGN_READY"
NEED_AI_RESEARCH_DESIGN = "NEED_AI_RESEARCH_DESIGN"
HUMAN_CONFIRM_AI_RESEARCH_DESIGN = "HUMAN_CONFIRM_AI_RESEARCH_DESIGN"

AI_RESEARCH_DESIGN_FILENAME = "AI_RESEARCH_DESIGN_PROPOSAL.json"
AI_RESEARCH_DESIGN_INPUT_FILENAME = "AI_RESEARCH_DESIGN_INPUT.json"
AI_RESEARCH_DESIGN_STATE_FILENAME = "AI_RESEARCH_DESIGN_STATE.json"
AI_RESEARCH_DESIGN_SCHEMA_VERSION = "research-evolution-ai-design-v1"
AI_RESEARCH_DESIGN_INPUT_SCHEMA_VERSION = "research-evolution-ai-design-input-v1"
AI_RESEARCH_DESIGN_STATE_SCHEMA_VERSION = "research-evolution-ai-design-state-v1"
AI_RESEARCH_DESIGN_VIEW_SCHEMA_VERSION = "research-evolution-ai-design-view-v1"

AI_DESIGN_IDENTITY_FIELDS = (
    "objective_id",
    "parent_proposal_id",
    "parent_proposal_hash",
    "input_context_hash",
    "research_hypothesis",
    "mechanism_family",
    "candidate_design_intention",
    "allowed_factors",
    "excluded_mechanisms",
    "validation_expectation",
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_FAILURE_CONSTRAINTS_ZH = {
    "STATISTICAL_FAILURE": "保持假设族和验证边界独立，先完成研究设计与统计契约预检。",
    "RETURN_FAILURE": "不得重复原有机制组合，改从独立机制提出可证伪假设。",
    "RISK_FAILURE": "将风险约束前置为设计条件，并对不可验证的执行语义拒绝放行。",
    "OVERFITTING_RISK": "限制自由度，冻结研究设计后再进入后续验证。",
    "SAMPLE_FAILURE": "先检查样本覆盖、时点一致性和数据可用性，再考虑候选登记。",
    "ENGINEERING_FAILURE": "先完成数据与执行链路预检，不把工程问题当作研究结论。",
}
_DATASET_KEYS = (
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
)
_LINEAGE_KEYS = (
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
_LOCAL_OUTCOME_FIELDS = frozenset({
    "收益",
    "收益率",
    "胜率",
    "回撤",
    "p-value",
    "p value",
    "单笔交易",
    "历史绩效",
    "历史表现",
})


class ResearchEvolutionAIDesignError(RuntimeError):
    """Fail-closed error at the AI research-design boundary."""

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


class ResearchEvolutionAIDesignBackendV1(Protocol):
    backend_type: str
    backend_version: str

    def generate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return only the six high-level design fields."""


def _safe_id(value: Any, *, kind: str) -> str:
    result = str(value or "")
    if not _IDENTIFIER_RE.fullmatch(result):
        raise ResearchEvolutionAIDesignError("INVALID_IDENTIFIER", f"{kind} 标识不合法", status_code=400)
    return result


def _assert_outcome_blind(value: Any) -> None:
    """Apply the shared ASCII policy plus the user-facing Chinese aliases."""
    PerformanceBlindGuard.assert_blind(value)

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                raw = str(key).strip().casefold()
                if raw in _LOCAL_OUTCOME_FIELDS or any(token in raw for token in ("收益", "胜率", "回撤", "单笔交易", "历史绩效", "历史表现")):
                    raise PerformanceLeakError(f"outcome-bearing field is forbidden: {key}")
                visit(nested)
        elif isinstance(item, (list, tuple, set, frozenset)):
            for nested in item:
                visit(nested)

    visit(value)


def _read_json(path: Path, *, code: str, required: bool = True) -> Mapping[str, Any] | None:
    if not path.exists():
        if required:
            raise ResearchEvolutionAIDesignError(code, f"缺少必要研究资料：{path.name}", status_code=404)
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchEvolutionAIDesignError("SOURCE_UNREADABLE", f"研究资料暂时不可读：{path.name}", status_code=503) from exc
    if not isinstance(payload, Mapping):
        raise ResearchEvolutionAIDesignError("SOURCE_INVALID", f"研究资料不是 JSON 对象：{path.name}", status_code=503)
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


def _dedupe_strings(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set, frozenset)):
        return []
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in result:
            result.append(item)
    return result


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise ResearchEvolutionAIDesignError("UNSAFE_PATH", "研究设计资料路径不在项目目录内", status_code=503) from exc


def _safe_lineage(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: payload[key] for key in _LINEAGE_KEYS if key in payload}
    parent_lineage: list[dict[str, Any]] = []
    for item in payload.get("parent_lineage") or ():
        if not isinstance(item, Mapping):
            continue
        parent_lineage.append({
            key: item[key]
            for key in ("relation", "parent_type", "parent_id", "parent_hash", "immutable")
            if key in item
        })
    result["parent_lineage"] = parent_lineage
    return result


def _safe_dataset_capability(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = payload or {}
    datasets: list[dict[str, Any]] = []
    for item in source.get("datasets") or ():
        if not isinstance(item, Mapping):
            continue
        safe: dict[str, Any] = {}
        for key in _DATASET_KEYS:
            if key not in item:
                continue
            value = item[key]
            if key == "fields":
                safe[key] = _dedupe_strings(value)
            elif isinstance(value, (str, int, float, bool)) or value is None:
                safe[key] = value
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
    result["failure_summary"] = _dedupe_strings(result.get("failure_summary"))
    result["avoid_mechanism_family"] = _dedupe_strings(result.get("avoid_mechanism_family"))
    result["avoid_mechanisms"] = _dedupe_strings(result.get("avoid_mechanisms"))
    result["suggested_research_directions"] = _dedupe_strings(result.get("suggested_research_directions"))
    return result


def _safe_landscape(payload: Mapping[str, Any]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for item in payload.get("entries") or ():
        if not isinstance(item, Mapping):
            continue
        safe = {
            key: item[key]
            for key in ("candidate_id", "candidate_family", "mechanism", "failure_categories", "trial_id")
            if key in item
        }
        safe["failure_categories"] = _dedupe_strings(safe.get("failure_categories"))
        entries.append(safe)
    category_totals = payload.get("category_totals")
    safe_totals = {
        str(key): int(value)
        for key, value in (category_totals.items() if isinstance(category_totals, Mapping) else ())
        if str(key) in _FAILURE_CONSTRAINTS_ZH and isinstance(value, (int, float))
    }
    return {
        "schema_version": str(payload.get("schema_version") or "research-failure-landscape-v1"),
        "objective_id": str(payload.get("objective_id") or ""),
        "entries": entries,
        "category_totals": safe_totals,
        "read_only": True,
    }


def _source_hash(payload: Any) -> str:
    return stable_hash(payload)


def ai_design_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical identity payload used by the durable design hash."""

    return {key: payload.get(key) for key in AI_DESIGN_IDENTITY_FIELDS}


def ai_design_identity_hash(payload: Mapping[str, Any]) -> str:
    """Calculate the canonical hash of one AI Design identity."""

    return stable_hash(ai_design_identity(payload))


@dataclass(frozen=True)
class EvolutionAIDesignInputV1:
    """The complete, outcome-blind payload sent to the design backend."""

    objective_id: str
    research_evolution_proposal: Mapping[str, Any]
    failure_landscape: Mapping[str, Any]
    mechanism_coverage_registry: Mapping[str, Any]
    objective_lineage: Mapping[str, Any]
    failed_mechanisms: tuple[str, ...] = ()
    excluded_mechanisms: tuple[str, ...] = ()
    suggested_research_directions: tuple[str, ...] = ()
    allowed_factors: tuple[str, ...] = ()
    available_data_capabilities: Mapping[str, Any] = field(default_factory=dict)
    constraints: Mapping[str, Any] = field(default_factory=dict)
    source_refs: Mapping[str, str] = field(default_factory=dict)
    source_hashes: Mapping[str, str] = field(default_factory=dict)
    input_context_hash: str = ""

    def __post_init__(self) -> None:
        for name in (
            "research_evolution_proposal",
            "failure_landscape",
            "mechanism_coverage_registry",
            "objective_lineage",
            "available_data_capabilities",
            "constraints",
            "source_refs",
            "source_hashes",
        ):
            object.__setattr__(self, name, jsonable(getattr(self, name)))
        for name in (
            "failed_mechanisms",
            "excluded_mechanisms",
            "suggested_research_directions",
            "allowed_factors",
        ):
            object.__setattr__(self, name, tuple(_dedupe_strings(getattr(self, name))))
        base = self._payload(include_hash=False)
        try:
            _assert_outcome_blind(base)
        except PerformanceLeakError as exc:
            raise ResearchEvolutionAIDesignError("OUTCOME_FIELD_BLOCKED", "AI 研究设计输入包含被禁止的结果字段", status_code=503) from exc
        if not self.input_context_hash:
            object.__setattr__(self, "input_context_hash", stable_hash(base))

    def _payload(self, *, include_hash: bool) -> dict[str, Any]:
        result = {
            "schema_version": AI_RESEARCH_DESIGN_INPUT_SCHEMA_VERSION,
            "objective_id": self.objective_id,
            "research_evolution_proposal": self.research_evolution_proposal,
            "failure_landscape": self.failure_landscape,
            "mechanism_coverage_registry": self.mechanism_coverage_registry,
            "objective_lineage": self.objective_lineage,
            "failed_mechanisms": list(self.failed_mechanisms),
            "excluded_mechanisms": list(self.excluded_mechanisms),
            "suggested_research_directions": list(self.suggested_research_directions),
            "allowed_factors": list(self.allowed_factors),
            "available_data_capabilities": self.available_data_capabilities,
            "constraints": self.constraints,
            "source_refs": self.source_refs,
            "source_hashes": self.source_hashes,
            "outcome_blind": True,
            "performance_data_loaded": False,
            "outcome_fields_available": False,
        }
        if include_hash:
            result["input_context_hash"] = self.input_context_hash
        return result

    def to_dict(self) -> dict[str, Any]:
        return jsonable(self._payload(include_hash=True))


class TemplateEvolutionAIDesignBackendV1:
    """Deterministic local adapter used when no external AI adapter is injected."""

    backend_type = "TEMPLATE_EVOLUTION_AI_DESIGN"
    backend_version = "1"

    def generate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        directions = _dedupe_strings(payload.get("suggested_research_directions"))
        direction = directions[0] if directions else "event_driven"
        return {
            "research_hypothesis": f"围绕 {direction} 机制提出与既有失败机制独立、可由当前数据能力验证的研究假设。",
            "mechanism_family": direction,
            "candidate_design_intention": "仅定义候选研究设计意图；人工确认前不生成 Candidate。",
            "allowed_factors": _dedupe_strings(payload.get("allowed_factors")),
            "excluded_mechanisms": _dedupe_strings(payload.get("excluded_mechanisms")),
            "validation_expectation": [
                "先完成 PIT、样本覆盖、数据可用性和执行语义预检。",
                "在独立检验家族中预注册研究设计。",
                "人工确认前不登记 Candidate、不启动 Trial。",
            ],
        }


class ResearchEvolutionAIDesignServiceV1:
    """Build and persist one outcome-blind AI design per evolution Objective."""

    _mutex = threading.RLock()

    def __init__(
        self,
        root: str | Path,
        *,
        backend: ResearchEvolutionAIDesignBackendV1 | Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        clock: Callable[[], str] = now_timestamp,
        crash_at: str | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.design_root = self.root / "reports" / "research_evolution" / "ai_design"
        self.backend = backend or TemplateEvolutionAIDesignBackendV1()
        self.clock = clock
        self.crash_at = crash_at

    def _objective_path(self, objective_id: str) -> Path:
        return self.root / "data" / "research" / "research_factory" / "objectives" / f"{_safe_id(objective_id, kind='objective_id')}.json"

    def _lineage_path(self, objective_id: str) -> Path:
        return self.root / "data" / "research" / "research_factory" / "lineage" / f"{_safe_id(objective_id, kind='objective_id')}.json"

    def _design_dir(self, objective_id: str) -> Path:
        path = (self.design_root / _safe_id(objective_id, kind="objective_id")).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ResearchEvolutionAIDesignError("UNSAFE_PATH", "AI 研究设计目录不在项目目录内", status_code=503) from exc
        return path

    def _paths(self, objective_id: str) -> tuple[Path, Path, Path]:
        directory = self._design_dir(objective_id)
        return (
            directory / AI_RESEARCH_DESIGN_INPUT_FILENAME,
            directory / AI_RESEARCH_DESIGN_FILENAME,
            directory / AI_RESEARCH_DESIGN_STATE_FILENAME,
        )

    def _find_proposal_path(self, proposal_id: str) -> Path:
        governance = ResearchProposalGovernanceServiceV1(self.root)
        try:
            return governance._proposal_path(proposal_id)  # noqa: SLF001 - read-only source resolution
        except ResearchProposalGovernanceError as exc:
            raise ResearchEvolutionAIDesignError(exc.code, exc.message_zh, status_code=exc.status_code, details=exc.details) from exc

    def _find_failure_landscape(self, proposal: Mapping[str, Any]) -> tuple[Mapping[str, Any], Path]:
        parent_objective_id = str(proposal.get("parent_objective_id") or "")
        candidates: list[Path] = []
        explicit = str(proposal.get("failure_landscape_ref") or "")
        if explicit:
            candidate = Path(explicit)
            if not candidate.is_absolute() and all(part not in {".", ".."} for part in candidate.parts):
                candidates.append((self.root / candidate).resolve())
        if parent_objective_id:
            candidates.append(self.root / "reports" / "research_evolution" / parent_objective_id / "failure_landscape.json")
        candidates.extend((
            self.root / "reports" / "research_evolution" / "failure_landscape.json",
            self.root / "reports" / "FAILURE_LANDSCAPE.json",
        ))
        seen: set[Path] = set()
        for path in candidates:
            path = path.resolve()
            if path in seen:
                continue
            seen.add(path)
            try:
                path.relative_to(self.root)
            except ValueError:
                continue
            if path.exists():
                payload = _read_json(path, code="FAILURE_LANDSCAPE_UNREADABLE")
                if payload is not None:
                    if parent_objective_id and payload.get("objective_id") not in {None, parent_objective_id}:
                        raise ResearchEvolutionAIDesignError("FAILURE_LANDSCAPE_MISMATCH", "失败空间与父研究目标不匹配", status_code=409)
                    return payload, path
        raise ResearchEvolutionAIDesignError("FAILURE_LANDSCAPE_NOT_FOUND", "未找到父研究的 Failure Landscape", status_code=404)

    def _find_coverage_path(self) -> Path:
        registry = MechanismCoverageRegistryV1(self.root)
        for path in registry._candidate_paths():  # noqa: SLF001 - read-only source resolution
            if path.exists():
                return path.resolve()
        raise ResearchEvolutionAIDesignError("MECHANISM_COVERAGE_NOT_FOUND", "未找到 Mechanism Coverage Registry", status_code=404)

    def _load_sources(self, objective_id: str) -> tuple[dict[str, Any], dict[str, Path]]:
        objective_path = self._objective_path(objective_id)
        objective = _read_json(objective_path, code="OBJECTIVE_NOT_FOUND")
        if objective is None or str(objective.get("objective_id") or "") != objective_id:
            raise ResearchEvolutionAIDesignError("OBJECTIVE_SOURCE_MISMATCH", "Objective 身份与请求不一致", status_code=503)
        proposal_id = str(objective.get("parent_proposal_id") or "")
        if not proposal_id:
            raise ResearchEvolutionAIDesignError("PARENT_PROPOSAL_REQUIRED", "当前 Objective 没有绑定父 Proposal", status_code=409)
        governance = ResearchProposalGovernanceServiceV1(self.root)
        try:
            proposal = governance.get_proposal(proposal_id)
        except ResearchProposalGovernanceError as exc:
            raise ResearchEvolutionAIDesignError(exc.code, exc.message_zh, status_code=exc.status_code, details=exc.details) from exc
        if str(proposal.get("created_objective_id") or objective_id) != objective_id:
            raise ResearchEvolutionAIDesignError("PROPOSAL_OBJECTIVE_MISMATCH", "父 Proposal 未绑定当前 Objective", status_code=409)
        proposal_path = self._find_proposal_path(proposal_id)

        failure_landscape, failure_path = self._find_failure_landscape(proposal)
        try:
            _assert_outcome_blind(failure_landscape)
        except PerformanceLeakError as exc:
            raise ResearchEvolutionAIDesignError("OUTCOME_FIELD_BLOCKED", "Failure Landscape 包含被禁止的结果字段", status_code=503) from exc

        coverage_path = self._find_coverage_path()
        coverage_source = _read_json(coverage_path, code="MECHANISM_COVERAGE_UNREADABLE")
        if coverage_source is None:
            raise ResearchEvolutionAIDesignError("MECHANISM_COVERAGE_NOT_FOUND", "未找到 Mechanism Coverage Registry", status_code=404)
        try:
            _assert_outcome_blind(coverage_source)
            coverage = MechanismCoverageRegistryV1(self.root).load(coverage_path)
        except PerformanceLeakError as exc:
            raise ResearchEvolutionAIDesignError("OUTCOME_FIELD_BLOCKED", "Mechanism Coverage Registry 包含被禁止的结果字段", status_code=503) from exc
        except Exception as exc:
            if isinstance(exc, ResearchEvolutionAIDesignError):
                raise
            raise ResearchEvolutionAIDesignError("MECHANISM_COVERAGE_INVALID", "Mechanism Coverage Registry 无法安全读取", status_code=503) from exc

        lineage_path = self._lineage_path(objective_id)
        lineage = _read_json(lineage_path, code="OBJECTIVE_LINEAGE_NOT_FOUND")
        if lineage is None or str(lineage.get("objective_id") or "") != objective_id:
            raise ResearchEvolutionAIDesignError("OBJECTIVE_LINEAGE_MISMATCH", "Objective lineage 与当前目标不匹配", status_code=409)
        if lineage.get("immutable") is not True:
            raise ResearchEvolutionAIDesignError("OBJECTIVE_LINEAGE_NOT_IMMUTABLE", "Objective lineage 不是不可变记录", status_code=409)

        data_path = self.root / "data" / "research" / "data_capability.json"
        data_capability = _read_json(data_path, code="DATA_CAPABILITY_UNREADABLE", required=False) or {}
        try:
            _assert_outcome_blind(data_capability)
        except PerformanceLeakError as exc:
            raise ResearchEvolutionAIDesignError("OUTCOME_FIELD_BLOCKED", "数据能力注册表包含被禁止的结果字段", status_code=503) from exc
        sources = {
            "objective": objective_path,
            "research_evolution_proposal": proposal_path,
            "failure_landscape": failure_path,
            "mechanism_coverage_registry": coverage_path,
            "objective_lineage": lineage_path,
            "data_capability": data_path,
        }
        return {
            "objective": dict(objective),
            "proposal": dict(proposal),
            "failure_landscape": dict(failure_landscape),
            "coverage": dict(coverage),
            "coverage_source": dict(coverage_source),
            "lineage": dict(lineage),
            "data_capability": dict(data_capability),
        }, sources

    def build_input(self, objective_id: str) -> EvolutionAIDesignInputV1:
        objective_id = _safe_id(objective_id, kind="objective_id")
        with self._mutex:
            sources, paths = self._load_sources(objective_id)
            objective = sources["objective"]
            proposal = _safe_proposal(sources["proposal"])
            landscape = _safe_landscape(sources["failure_landscape"])
            raw_coverage = sources["coverage"]
            coverage = {
                key: raw_coverage[key]
                for key in ("schema_version", "coverage_id", "covered", "unexplored", "read_only", "outcome_blind")
                if key in raw_coverage
            }
            lineage = _safe_lineage(sources["lineage"])
            failed = _dedupe_strings([proposal.get("failed_mechanism"), proposal.get("failed_mechanism_family")])
            for item in landscape["entries"]:
                failed.extend(_dedupe_strings([item.get("mechanism"), item.get("candidate_family")]))
            failed = _dedupe_strings(failed)
            excluded = _dedupe_strings([
                *failed,
                *proposal.get("avoid_mechanism_family", []),
                *proposal.get("avoid_mechanisms", []),
                *coverage.get("covered", []),
            ])
            directions = _dedupe_strings([
                *proposal.get("suggested_research_directions", []),
                *coverage.get("unexplored", []),
            ])
            allowed_factors = _dedupe_strings(objective.get("allowed_factor_scope"))
            category_names = _dedupe_strings([
                category
                for item in landscape["entries"]
                for category in _dedupe_strings(item.get("failure_categories"))
            ])
            constraints = {
                key: objective.get("risk_constraints", {}).get(key)
                for key in ("pit_required", "no_lookahead", "t_plus_1", "price_limit_fail_closed", "suspension_fail_closed")
                if isinstance(objective.get("risk_constraints"), Mapping) and key in objective["risk_constraints"]
            }
            constraints["failure_category_constraints"] = {
                category: _FAILURE_CONSTRAINTS_ZH[category]
                for category in category_names
                if category in _FAILURE_CONSTRAINTS_ZH
            }
            safe_capabilities = _safe_dataset_capability(sources["data_capability"])
            source_refs = {key: _relative(self.root, path) for key, path in paths.items()}
            source_hashes = {
                "proposal": _source_hash(proposal),
                "failure_landscape": _source_hash(landscape),
                "mechanism_coverage_registry": _source_hash(coverage),
                "objective_lineage": _source_hash(lineage),
                "data_capability": _source_hash(safe_capabilities),
            }
            return EvolutionAIDesignInputV1(
                objective_id=objective_id,
                research_evolution_proposal=proposal,
                failure_landscape=landscape,
                mechanism_coverage_registry=coverage,
                objective_lineage=lineage,
                failed_mechanisms=tuple(failed),
                excluded_mechanisms=tuple(excluded),
                suggested_research_directions=tuple(directions),
                allowed_factors=tuple(allowed_factors),
                available_data_capabilities=safe_capabilities,
                constraints=constraints,
                source_refs=source_refs,
                source_hashes=source_hashes,
            )

    build_ai_input = build_input
    prepare = build_input

    def _invoke_backend(self, backend: Any, payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], str, str]:
        try:
            if hasattr(backend, "generate"):
                result = backend.generate(payload)
                backend_type = str(getattr(backend, "backend_type", backend.__class__.__name__))
                backend_version = str(getattr(backend, "backend_version", "1"))
            elif callable(backend):
                result = backend(payload)
                backend_type = getattr(backend, "__name__", backend.__class__.__name__)
                backend_version = "callable"
            else:
                raise TypeError("backend must expose generate(payload) or be callable")
        except ResearchEvolutionAIDesignError:
            raise
        except Exception as exc:
            raise ResearchEvolutionAIDesignError("AI_DESIGN_BACKEND_FAILED", "AI 研究设计生成失败，系统保持在人工边界", status_code=503) from exc
        if not isinstance(result, Mapping):
            raise ResearchEvolutionAIDesignError("AI_DESIGN_OUTPUT_INVALID", "AI 研究设计输出不是 JSON 对象", status_code=503)
        return result, backend_type, backend_version

    def _validate_design(self, raw: Mapping[str, Any], context: EvolutionAIDesignInputV1) -> dict[str, Any]:
        try:
            _assert_outcome_blind(raw)
        except PerformanceLeakError as exc:
            raise ResearchEvolutionAIDesignError("OUTCOME_FIELD_BLOCKED", "AI 研究设计输出包含被禁止的结果字段", status_code=503) from exc
        required = (
            "research_hypothesis",
            "mechanism_family",
            "candidate_design_intention",
            "allowed_factors",
            "excluded_mechanisms",
            "validation_expectation",
        )
        missing = [key for key in required if key not in raw]
        if missing:
            raise ResearchEvolutionAIDesignError("AI_DESIGN_OUTPUT_INCOMPLETE", "AI 研究设计输出缺少必要字段", status_code=503, details={"missing": missing})
        for key in ("research_hypothesis", "mechanism_family", "candidate_design_intention"):
            if not isinstance(raw[key], str) or not raw[key].strip():
                raise ResearchEvolutionAIDesignError("AI_DESIGN_OUTPUT_INVALID", f"AI 研究设计字段无效：{key}", status_code=503)
        allowed = _dedupe_strings(raw.get("allowed_factors"))
        excluded = _dedupe_strings(raw.get("excluded_mechanisms"))
        expectation = _dedupe_strings(raw.get("validation_expectation"))
        allowed_scope = set(context.allowed_factors)
        outside_scope = [item for item in allowed if allowed_scope and item not in allowed_scope]
        if outside_scope:
            raise ResearchEvolutionAIDesignError("AI_DESIGN_FACTOR_OUT_OF_SCOPE", "AI 研究设计使用了未授权因子", status_code=503, details={"factors": outside_scope})
        excluded_folded = {item.casefold() for item in context.excluded_mechanisms}
        missing_exclusions = [item for item in context.excluded_mechanisms if item.casefold() not in {value.casefold() for value in excluded}]
        if missing_exclusions:
            raise ResearchEvolutionAIDesignError("AI_DESIGN_EXCLUSION_INCOMPLETE", "AI 研究设计未完整保留禁止机制", status_code=503, details={"mechanisms": missing_exclusions})
        if str(raw["mechanism_family"]).casefold() in excluded_folded:
            raise ResearchEvolutionAIDesignError("AI_DESIGN_MECHANISM_REPEATED", "AI 研究设计重复了已禁止机制", status_code=503)
        if not expectation:
            raise ResearchEvolutionAIDesignError("AI_DESIGN_OUTPUT_INVALID", "AI 研究设计缺少验证预期", status_code=503)
        return {
            "research_hypothesis": raw["research_hypothesis"].strip(),
            "mechanism_family": raw["mechanism_family"].strip(),
            "candidate_design_intention": raw["candidate_design_intention"].strip(),
            "allowed_factors": allowed,
            "excluded_mechanisms": excluded,
            "validation_expectation": expectation,
        }

    def _design_identity(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return ai_design_identity(payload)

    def _state_for(self, design: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": AI_RESEARCH_DESIGN_STATE_SCHEMA_VERSION,
            "transition_id": f"AI_DESIGN_TRANSITION_{stable_hash({'objective_id': design.get('objective_id'), 'design_hash': design.get('design_hash')})[:24].upper()}",
            "objective_id": design.get("objective_id"),
            "from_state": OBJECTIVE_CREATED,
            "to_state": AI_DESIGN_READY,
            "status": AI_DESIGN_READY,
            "design_id": design.get("design_id"),
            "design_hash": design.get("design_hash"),
            "parent_proposal_id": design.get("parent_proposal_id"),
            "created_at": design.get("generated_at"),
            "requires_human_confirmation": True,
            "next_action": HUMAN_CONFIRM_AI_RESEARCH_DESIGN,
            "automatic_candidate_created": False,
            "automatic_trial_started": False,
            "budget_consumed": False,
        }

    def _validate_persisted(self, design: Mapping[str, Any], context: EvolutionAIDesignInputV1) -> None:
        if str(design.get("objective_id") or "") != context.objective_id:
            raise ResearchEvolutionAIDesignError("AI_DESIGN_IDENTITY_CONFLICT", "已存在的 AI 设计不属于当前 Objective", status_code=409)
        if str(design.get("input_context_hash") or "") != context.input_context_hash:
            raise ResearchEvolutionAIDesignError("AI_DESIGN_CONTEXT_CHANGED", "AI 设计输入上下文已变化，请人工重新确认", status_code=409)
        expected_hash = stable_hash(self._design_identity(design))
        if str(design.get("design_hash") or "") != expected_hash:
            raise ResearchEvolutionAIDesignError("AI_DESIGN_HASH_INVALID", "AI 设计哈希校验失败", status_code=503)
        if str(design.get("status") or "") != AI_DESIGN_READY:
            raise ResearchEvolutionAIDesignError("AI_DESIGN_STATE_INVALID", "AI 设计状态不是 AI_DESIGN_READY", status_code=503)
        self._validate_design(design, context)

    def _validate_state(self, state: Mapping[str, Any], design: Mapping[str, Any]) -> None:
        expected = {
            "objective_id": design.get("objective_id"),
            "from_state": OBJECTIVE_CREATED,
            "status": AI_DESIGN_READY,
            "to_state": AI_DESIGN_READY,
            "design_id": design.get("design_id"),
            "design_hash": design.get("design_hash"),
        }
        if any(state.get(key) != value for key, value in expected.items()):
            raise ResearchEvolutionAIDesignError("AI_DESIGN_STATE_INVALID", "AI 研究设计状态记录与设计提案不一致", status_code=503)
        if any(state.get(key) is not False for key in ("automatic_candidate_created", "automatic_trial_started", "budget_consumed")):
            raise ResearchEvolutionAIDesignError("AI_DESIGN_STATE_INVALID", "AI 研究设计状态违反零副作用边界", status_code=503)
        try:
            _assert_outcome_blind(state)
        except PerformanceLeakError as exc:
            raise ResearchEvolutionAIDesignError("OUTCOME_FIELD_BLOCKED", "AI 研究设计状态包含被禁止的结果字段", status_code=503) from exc

    def generate_design(
        self,
        objective_id: str,
        *,
        backend: ResearchEvolutionAIDesignBackendV1 | Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        ai_output: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        objective_id = _safe_id(objective_id, kind="objective_id")
        with self._mutex:
            context = self.build_input(objective_id)
            input_path, output_path, state_path = self._paths(objective_id)
            if output_path.exists():
                existing = _read_json(output_path, code="AI_DESIGN_UNREADABLE")
                if existing is None:
                    raise ResearchEvolutionAIDesignError("AI_DESIGN_NOT_FOUND", "AI 研究设计不存在", status_code=404)
                self._validate_persisted(existing, context)
                if state_path.exists():
                    state = _read_json(state_path, code="AI_DESIGN_STATE_UNREADABLE")
                    if state is not None:
                        self._validate_state(state, existing)
                else:
                    _atomic_write_json(state_path, self._state_for(existing))
                return {**dict(existing), "idempotent": True}

            _atomic_write_json(input_path, context.to_dict())
            if self.crash_at == "after_input":
                raise RuntimeError("simulated crash after AI design input")
            raw, backend_type, backend_version = self._invoke_backend(backend or self.backend, ai_output or context.to_dict()) if ai_output is None else (ai_output, "PRECOMPUTED_AI_OUTPUT", "1")
            fields = self._validate_design(raw, context)
            proposal = context.research_evolution_proposal
            design_base = {
                "schema_version": AI_RESEARCH_DESIGN_SCHEMA_VERSION,
                "design_id": "",
                "status": AI_DESIGN_READY,
                "objective_id": objective_id,
                "parent_proposal_id": proposal.get("proposal_id"),
                "parent_proposal_hash": proposal.get("proposal_hash"),
                "input_context_hash": context.input_context_hash,
                **fields,
                "lineage": {
                    "objective_id": objective_id,
                    "objective_lineage_id": context.objective_lineage.get("lineage_id"),
                    "parent_objective_id": proposal.get("parent_objective_id"),
                    "parent_proposal_id": proposal.get("proposal_id"),
                    "parent_proposal_hash": proposal.get("proposal_hash"),
                    "parent_lineage": context.objective_lineage.get("parent_lineage", []),
                    "relation": "AI_DESIGN_FOR_OBJECTIVE",
                    "immutable": True,
                },
                "source_refs": context.source_refs,
                "source_hashes": context.source_hashes,
                "governance": {
                    "from_state": OBJECTIVE_CREATED,
                    "status": AI_DESIGN_READY,
                    "requires_human_confirmation": True,
                    "next_action": HUMAN_CONFIRM_AI_RESEARCH_DESIGN,
                    "automatic_candidate_created": False,
                    "automatic_trial_started": False,
                    "budget_consumed": False,
                    "predictive_trial_allowed": False,
                },
                "outcome_blind": True,
                "performance_data_loaded": False,
                "outcome_fields_available": False,
                "backend_type": backend_type,
                "backend_version": backend_version,
                "generated_at": self.clock(),
            }
            design_base["design_hash"] = stable_hash(self._design_identity(design_base))
            design_base["design_id"] = f"AI_RESEARCH_DESIGN_{design_base['design_hash'][:24].upper()}"
            _assert_outcome_blind(design_base)
            _atomic_write_json(output_path, design_base)
            if self.crash_at == "after_output":
                raise RuntimeError("simulated crash after AI design output")
            _atomic_write_json(state_path, self._state_for(design_base))
            return dict(design_base)

    generate = generate_design
    run = generate_design

    def recover(self, objective_id: str) -> dict[str, Any]:
        """Reconcile a durable output/state pair without invoking AI again."""
        objective_id = _safe_id(objective_id, kind="objective_id")
        with self._mutex:
            context = self.build_input(objective_id)
            input_path, output_path, state_path = self._paths(objective_id)
            if not output_path.exists():
                return {
                    "objective_id": objective_id,
                    "status": NEED_AI_RESEARCH_DESIGN,
                    "available": False,
                    "recovered": False,
                }
            design = _read_json(output_path, code="AI_DESIGN_UNREADABLE")
            if design is None:
                raise ResearchEvolutionAIDesignError("AI_DESIGN_NOT_FOUND", "AI 研究设计不存在", status_code=404)
            self._validate_persisted(design, context)
            recovered = not state_path.exists()
            if recovered:
                _atomic_write_json(state_path, self._state_for(design))
            else:
                state = _read_json(state_path, code="AI_DESIGN_STATE_UNREADABLE")
                if state is not None:
                    self._validate_state(state, design)
            if not input_path.exists():
                _atomic_write_json(input_path, context.to_dict())
            return {**dict(design), "recovered": recovered, "idempotent": True}

    def recover_all(self) -> list[dict[str, Any]]:
        if not self.design_root.exists():
            return []
        results: list[dict[str, Any]] = []
        for path in sorted(self.design_root.iterdir()):
            if path.is_dir() and _IDENTIFIER_RE.fullmatch(path.name):
                results.append(self.recover(path.name))
        return results

    def get_design(self, objective_id: str) -> dict[str, Any]:
        """Read the design page model; this method never invokes AI or writes."""
        objective_id = _safe_id(objective_id, kind="objective_id")
        with self._mutex:
            context = self.build_input(objective_id)
            _, output_path, state_path = self._paths(objective_id)
            design = _read_json(output_path, code="AI_DESIGN_UNREADABLE", required=False)
            if design is not None:
                self._validate_persisted(design, context)
                if state_path.exists():
                    state = _read_json(state_path, code="AI_DESIGN_STATE_UNREADABLE")
                    if state is not None:
                        self._validate_state(state, design)
            result = {
                "schema_version": AI_RESEARCH_DESIGN_VIEW_SCHEMA_VERSION,
                "objective_id": objective_id,
                "available": design is not None,
                "status": AI_DESIGN_READY if design is not None else NEED_AI_RESEARCH_DESIGN,
                "input": context.to_dict(),
                "design": dict(design) if design is not None else None,
                "governance": {
                    "requires_human_confirmation": True,
                    "next_action": HUMAN_CONFIRM_AI_RESEARCH_DESIGN if design is not None else "GENERATE_AI_RESEARCH_DESIGN",
                    "automatic_candidate_created": False,
                    "automatic_trial_started": False,
                    "budget_consumed": False,
                    "state_artifact_exists": state_path.exists(),
                },
                "source_refs": context.source_refs,
                "source_hashes": context.source_hashes,
                "output_path": _relative(self.root, output_path) if design is not None else None,
                "outcome_blind": True,
                "performance_data_loaded": False,
                "outcome_fields_available": False,
            }
            try:
                _assert_outcome_blind(result)
            except PerformanceLeakError as exc:
                raise ResearchEvolutionAIDesignError("OUTCOME_FIELD_BLOCKED", "AI 研究设计只读视图包含被禁止的结果字段", status_code=503) from exc
            return result


ResearchEvolutionAIDesignService = ResearchEvolutionAIDesignServiceV1
ResearchEvolutionAIDesignManagerV1 = ResearchEvolutionAIDesignServiceV1
ResearchEvolutionAIDesignManager = ResearchEvolutionAIDesignServiceV1


def _main() -> int:
    parser = argparse.ArgumentParser(description="生成停在人工确认边界的 Evolution Objective AI 研究设计")
    parser.add_argument("--root", default=".", help="项目根目录")
    parser.add_argument("--objective-id", required=True)
    args = parser.parse_args()
    design = ResearchEvolutionAIDesignServiceV1(args.root).generate_design(args.objective_id)
    print(json.dumps({"状态": design["status"], "设计编号": design["design_id"], "下一步": design["governance"]["next_action"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "AI_DESIGN_READY",
    "AI_DESIGN_IDENTITY_FIELDS",
    "AI_RESEARCH_DESIGN_FILENAME",
    "AI_RESEARCH_DESIGN_INPUT_FILENAME",
    "AI_RESEARCH_DESIGN_INPUT_SCHEMA_VERSION",
    "AI_RESEARCH_DESIGN_SCHEMA_VERSION",
    "AI_RESEARCH_DESIGN_STATE_FILENAME",
    "AI_RESEARCH_DESIGN_STATE_SCHEMA_VERSION",
    "AI_RESEARCH_DESIGN_VIEW_SCHEMA_VERSION",
    "EvolutionAIDesignInputV1",
    "HUMAN_CONFIRM_AI_RESEARCH_DESIGN",
    "NEED_AI_RESEARCH_DESIGN",
    "OBJECTIVE_CREATED",
    "ResearchEvolutionAIDesignBackendV1",
    "ResearchEvolutionAIDesignError",
    "ResearchEvolutionAIDesignManager",
    "ResearchEvolutionAIDesignManagerV1",
    "ResearchEvolutionAIDesignService",
    "ResearchEvolutionAIDesignServiceV1",
    "TemplateEvolutionAIDesignBackendV1",
    "ai_design_identity",
    "ai_design_identity_hash",
]
