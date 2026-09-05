"""Outcome-blind bridge from failure analysis to a human-reviewed proposal.

The bridge consumes derived failure-analysis artifacts and writes only a
research-direction proposal.  It never creates an objective or candidate,
starts a trial, invokes Codex, or touches a budget ledger.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
import json
import os
from pathlib import Path
import re
from typing import Any

from .common import now_timestamp, stable_hash
from .context import PerformanceBlindGuard
from .research_evolution_manager import FAILURE_CATEGORIES


PROPOSAL_FILENAME = "RESEARCH_EVOLUTION_PROPOSAL.json"
COVERAGE_FILENAME = "MECHANISM_COVERAGE_REGISTRY.json"
COVERAGE_SCHEMA_VERSION = "mechanism-coverage-registry-v1"
PROPOSAL_SCHEMA_VERSION = "research-evolution-proposal-v1"

EVOLUTION_ANALYZED = "EVOLUTION_ANALYZED"
PROPOSAL_CREATED = "PROPOSAL_CREATED"
CREATED = "CREATED"
HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
APPROVED = "APPROVED"
OBJECTIVE_CREATION_READY = "OBJECTIVE_CREATION_READY"
REJECTED = "REJECTED"
CLOSED = "CLOSED"
READY_FOR_CONFIRMATION = "READY_FOR_CONFIRMATION"
CREATE_OBJECTIVE = "CREATE_OBJECTIVE"
CREATE_NEW_OBJECTIVE = "CREATE_NEW_OBJECTIVE"

DEFAULT_RESEARCH_DIRECTIONS = (
    "event_driven",
    "volatility_structure",
    "capital_flow",
)
ALLOWED_RESEARCH_DIRECTIONS = frozenset({
    "event_driven",
    "event_structure",
    "volatility_anomaly",
    "volatility_structure",
    "market_regime",
    "capital_flow",
    "price_structure",
})
_DIRECTION_ALIASES = {
    "event driven": "event_driven",
    "event-driven": "event_driven",
    "event structure": "event_structure",
    "volatility anomaly": "volatility_anomaly",
    "volatility structure": "volatility_structure",
    "market regime": "market_regime",
    "capital flow": "capital_flow",
    "price structure": "price_structure",
}
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_MACHINE_TOKEN_RE = re.compile(r"[^A-Za-z0-9]+")


class ResearchEvolutionProposalError(RuntimeError):
    """Raised when a proposal cannot be built from a safe source boundary."""

    def __init__(self, code: str, message_zh: str):
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchEvolutionProposalError("SOURCE_UNREADABLE", f"研究演进输入文件暂时不可读：{path.name}") from exc


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(_json_value(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _normalize_name(value: Any) -> str:
    return _MACHINE_TOKEN_RE.sub("_", str(value or "").strip().casefold()).strip("_")


def _normalize_machine_token(value: Any) -> str:
    return _MACHINE_TOKEN_RE.sub("_", str(value or "").strip().upper()).strip("_")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


def _first_scalar(source: Any, keys: Iterable[str]) -> Any:
    if not isinstance(source, Mapping):
        return None
    for key in keys:
        value = source.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool) and str(value).strip():
            return value
    return None


def _values(source: Any, keys: Iterable[str]) -> list[Any]:
    if not isinstance(source, Mapping):
        return []
    wanted = {str(key).casefold() for key in keys}
    found: list[Any] = []
    for key, value in source.items():
        if str(key).casefold() in wanted:
            found.append(value)
    return found


def _safe_relative(root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.name


def _safe_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ResearchEvolutionProposalError("SOURCE_PATH_NOT_ALLOWED", "研究演进输入路径不在项目目录内")
    return resolved


def _read_source(root: Path, source: Mapping[str, Any] | str | Path | None) -> tuple[Mapping[str, Any] | None, Path | None]:
    if source is None:
        return None, None
    if isinstance(source, Mapping):
        return dict(source), None
    path = _safe_path(root, source)
    payload = _load_json(path)
    if not isinstance(payload, Mapping):
        raise ResearchEvolutionProposalError("SOURCE_INVALID", f"研究演进输入不是 JSON 对象：{path.name}")
    return dict(payload), path


def _lineage(report: Mapping[str, Any], candidate_contract: Mapping[str, Any] | None) -> dict[str, str]:
    report_lineage = report.get("lineage") if isinstance(report.get("lineage"), Mapping) else {}
    contract_lineage = candidate_contract.get("research_lineage") if isinstance(candidate_contract, Mapping) and isinstance(candidate_contract.get("research_lineage"), Mapping) else {}
    safe: dict[str, str] = {}
    keys = (
        "objective_id",
        "candidate_id",
        "trial_id",
        "batch_id",
        "run_id",
        "objective_ref",
        "candidate_contract_ref",
        "trial_contract_ref",
        "trial_ledger_ref",
        "validation_result_ref",
        "final_status_ref",
    )
    for key in keys:
        value = report_lineage.get(key, contract_lineage.get(key))
        if value not in (None, ""):
            safe[key] = str(value)
    for key in ("objective_id", "candidate_id", "trial_id"):
        if key not in safe and report.get(key) not in (None, ""):
            safe[key] = str(report[key])
    return safe


def _candidate_sources(report: Mapping[str, Any], candidate_contract: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    sources: list[Mapping[str, Any]] = []
    for source in (
        candidate_contract,
        report.get("candidate_contract") if isinstance(report.get("candidate_contract"), Mapping) else None,
        report.get("candidate") if isinstance(report.get("candidate"), Mapping) else None,
    ):
        if isinstance(source, Mapping):
            sources.append(source)
            identity = source.get("mechanism_identity")
            if isinstance(identity, Mapping):
                sources.append(identity)
            semantic = source.get("semantic_record")
            if isinstance(semantic, Mapping):
                sources.append(semantic)
    return sources


def _extract_factor_ids(sources: Iterable[Mapping[str, Any]]) -> list[str]:
    result: list[str] = []
    for source in sources:
        for value in _values(source, ("factor_ids", "factors", "factor_id")):
            for item in _as_list(value):
                if isinstance(item, Mapping):
                    item = _first_scalar(item, ("factor_id", "id", "name"))
                if item not in (None, ""):
                    token = _normalize_machine_token(item)
                    if token and token not in result:
                        result.append(token)
    return result


def _extract_mechanism(sources: Iterable[Mapping[str, Any]], factor_ids: list[str]) -> str:
    explicit: list[str] = []
    for source in sources:
        for value in _values(source, ("mechanism_identity", "mechanism", "mechanism_key", "mechanism_id", "hypothesis")):
            if isinstance(value, Mapping):
                value = _first_scalar(value, ("key", "mechanism", "mechanism_key", "id", "name"))
            if value not in (None, ""):
                token = _normalize_machine_token(value)
                if token and token not in explicit:
                    explicit.append(token)
    if factor_ids:
        return "+".join(factor_ids)
    if explicit:
        return "+".join(explicit)
    return "UNKNOWN_MECHANISM"


def _extract_families(sources: Iterable[Mapping[str, Any]], mechanism: str, factor_ids: list[str]) -> list[str]:
    specific_families: list[str] = []
    broad_families: list[str] = []
    for source in sources:
        for key in ("mechanism_family", "factor_family", "factor_family_id", "family_id", "family", "candidate_family"):
            for value in _values(source, (key,)):
                target = specific_families if key in {"mechanism_family", "factor_family", "factor_family_id"} else broad_families
                for item in _as_list(value):
                    if isinstance(item, Mapping):
                        item = _first_scalar(item, ("family_id", "family", "id", "name"))
                    normalized = _normalize_name(item)
                    if normalized and normalized not in target:
                        target.append(normalized)
    mechanism_tokens = {_normalize_name(item) for item in re.split(r"[+|/,]", mechanism) if item}
    combined = "_".join((*mechanism_tokens, *(_normalize_name(item) for item in factor_ids)))
    known_liquidity = any(token in combined for token in ("amount_accel", "liquidity", "illiq", "volume_participation"))
    if known_liquidity:
        families = ["liquidity_acceleration", *[item for item in specific_families if item != "liquidity_acceleration"]]
    else:
        families = [*specific_families, *[item for item in broad_families if item not in specific_families]]
    if not families:
        fallback = next((token for token in mechanism_tokens if token), "unknown_mechanism")
        families.append(fallback)
    return families


def _extract_categories(report: Mapping[str, Any], landscape: Mapping[str, Any] | None, candidate_id: str, trial_id: str) -> list[str]:
    categories: list[str] = []
    analysis = report.get("failure_analysis") if isinstance(report.get("failure_analysis"), Mapping) else {}
    raw_values: list[Any] = [analysis.get("primary_categories"), analysis.get("failure_categories")]
    counts = analysis.get("category_counts") if isinstance(analysis.get("category_counts"), Mapping) else {}
    raw_values.append([key for key, value in counts.items() if value])
    if landscape:
        entries = landscape.get("entries", [])
        if isinstance(entries, list):
            matching = [
                item for item in entries
                if isinstance(item, Mapping)
                and (not candidate_id or str(item.get("candidate_id") or "") == candidate_id)
                and (not trial_id or str(item.get("trial_id") or "") == trial_id)
            ]
            if not matching and landscape.get("objective_id") == report.get("lineage", {}).get("objective_id"):
                matching = [item for item in entries if isinstance(item, Mapping)]
            raw_values.extend(item.get("failure_categories") for item in matching)
    for value in raw_values:
        for item in _as_list(value):
            category = str(item or "").strip().upper()
            if category in FAILURE_CATEGORIES and category not in categories:
                categories.append(category)
    return categories or ["ENGINEERING_FAILURE"]


def _coverage_names(value: Any) -> list[str]:
    names: list[str] = []
    for item in _as_list(value):
        if isinstance(item, Mapping):
            item = _first_scalar(item, ("direction_id", "mechanism_family", "family_id", "family", "mechanism", "id", "name"))
        normalized = _normalize_name(item)
        if normalized and normalized not in names:
            names.append(normalized)
    return names


def _coverage_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = payload or {}
    covered: list[str] = []
    for key in ("covered", "covered_mechanisms", "covered_mechanism_families", "tested_mechanism_families"):
        for item in _coverage_names(source.get(key)):
            if item not in covered:
                covered.append(item)
    unexplored: list[str] = []
    for key in ("unexplored", "unexplored_directions", "open_directions"):
        for item in _coverage_names(source.get(key)):
            if item in ALLOWED_RESEARCH_DIRECTIONS and item not in unexplored:
                unexplored.append(item)
    if not unexplored:
        unexplored = list(DEFAULT_RESEARCH_DIRECTIONS)
    source_reports = [str(item) for item in _as_list(source.get("source_reports")) if item not in (None, "")]
    result = {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "coverage_id": str(source.get("coverage_id") or "MECHANISM_COVERAGE_REGISTRY_V1"),
        "covered": covered,
        "unexplored": unexplored,
        "source_reports": source_reports,
        "read_only": True,
        "outcome_blind": True,
    }
    return result


class MechanismCoverageRegistryV1:
    """Persist the small, outcome-blind research-space coverage registry."""

    filename = COVERAGE_FILENAME

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.path = self.root / "reports" / "research_evolution" / "proposals" / self.filename

    def _candidate_paths(self) -> tuple[Path, ...]:
        evolution_root = self.root / "reports" / "research_evolution"
        return (
            self.path,
            evolution_root / "mechanism_coverage_registry.json",
            evolution_root / "MECHANISM_COVERAGE_REGISTRY.json",
            evolution_root / "mechanism_coverage.json",
            self.root / "reports" / "MECHANISM_COVERAGE_REGISTRY.json",
        )

    def load(self, payload: Mapping[str, Any] | str | Path | None = None) -> dict[str, Any]:
        if payload is not None:
            source, _ = _read_source(self.root, payload)
            return _coverage_payload(source)
        for path in self._candidate_paths():
            if path.exists():
                source = _load_json(path)
                if not isinstance(source, Mapping):
                    raise ResearchEvolutionProposalError("COVERAGE_INVALID", "机制覆盖注册表格式不受支持")
                return _coverage_payload(source)
        return _coverage_payload(None)

    def record(self, mechanism_families: Iterable[str], *, source_report: str | None = None, payload: Mapping[str, Any] | None = None, objective_id: str | None = None) -> dict[str, Any]:
        current = self.load(payload)
        covered = list(current["covered"])
        for family in mechanism_families:
            normalized = _normalize_name(family)
            if normalized and normalized not in covered:
                covered.append(normalized)
        source_reports = list(current["source_reports"])
        if source_report and source_report not in source_reports:
            source_reports.append(source_report)
        updated = dict(current)
        updated["covered"] = sorted(covered)
        updated["source_reports"] = sorted(source_reports)
        updated["objective_id"] = objective_id or current.get("objective_id")
        updated["updated_at"] = current.get("updated_at") or now_timestamp()
        updated = {key: value for key, value in updated.items() if value is not None}
        PerformanceBlindGuard.assert_blind(updated)
        if not self.path.exists() or _load_json(self.path) != updated:
            _atomic_write_json(self.path, updated)
        return updated

    update = record
    register = record


MechanismCoverageRegistry = MechanismCoverageRegistryV1


def _safe_report_input(report: Mapping[str, Any], lineage: Mapping[str, str], mechanism: str, families: list[str], categories: list[str]) -> dict[str, Any]:
    candidate = report.get("candidate") if isinstance(report.get("candidate"), Mapping) else {}
    return {
        "report_id": str(report.get("report_id") or ""),
        "report_hash": str(report.get("report_hash") or ""),
        "lineage": dict(lineage),
        "candidate_id": str(lineage.get("candidate_id") or ""),
        "candidate_family": str(candidate.get("candidate_family") or ""),
        "failed_mechanism": mechanism,
        "avoid_mechanism_family": list(families),
        "failure_summary": list(categories),
    }


class ResearchEvolutionProposalManager:
    """Build and persist an explicit proposal, stopping at human review."""

    proposal_filename = PROPOSAL_FILENAME
    coverage_filename = COVERAGE_FILENAME

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.proposal_path = self.root / "reports" / "research_evolution" / "proposals" / self.proposal_filename
        self.coverage_registry = MechanismCoverageRegistryV1(self.root)

    def _validate_identifier(self, value: str | None, kind: str) -> str | None:
        if value in (None, ""):
            return None
        candidate = str(value)
        if not _IDENTIFIER_RE.fullmatch(candidate):
            raise ResearchEvolutionProposalError("INVALID_IDENTIFIER", f"{kind} 标识不合法")
        return candidate

    def _latest_report(self, objective_id: str | None, candidate_id: str | None, trial_id: str | None) -> tuple[Mapping[str, Any], Path]:
        root = self.root / "reports" / "research_evolution"
        if objective_id:
            root = root / objective_id
        matches: list[tuple[float, str, Mapping[str, Any], Path]] = []
        if root.exists():
            for path in sorted(root.rglob("research_evolution_report.json"), key=lambda item: item.as_posix()):
                try:
                    payload = _load_json(path)
                except ResearchEvolutionProposalError:
                    continue
                if not isinstance(payload, Mapping):
                    continue
                lineage = payload.get("lineage") if isinstance(payload.get("lineage"), Mapping) else {}
                if objective_id and str(lineage.get("objective_id") or payload.get("objective_id") or "") != objective_id:
                    continue
                if candidate_id and str(lineage.get("candidate_id") or "") != candidate_id:
                    continue
                if trial_id and str(lineage.get("trial_id") or "") != trial_id:
                    continue
                matches.append((path.stat().st_mtime, path.as_posix(), payload, path))
        if not matches:
            raise ResearchEvolutionProposalError("FAILURE_REPORT_NOT_FOUND", "未找到可用于生成研究演进建议的失败报告")
        _, _, payload, path = max(matches, key=lambda item: (item[0], item[1]))
        return payload, path

    def _load_landscape(self, objective_id: str | None) -> Mapping[str, Any] | None:
        candidates = []
        if objective_id:
            candidates.append(self.root / "reports" / "research_evolution" / objective_id / "failure_landscape.json")
        candidates.extend((
            self.root / "reports" / "research_evolution" / "failure_landscape.json",
            self.root / "reports" / "NEXT_RESEARCH_CYCLE_FAILURE_LANDSCAPE_V1.json",
        ))
        for path in candidates:
            if path.exists():
                payload = _load_json(path)
                if not isinstance(payload, Mapping):
                    raise ResearchEvolutionProposalError("LANDSCAPE_INVALID", "失败空间文件格式不受支持")
                return payload
        return None

    def _candidate_from_lineage(self, report: Mapping[str, Any], candidate_id: str | None) -> Mapping[str, Any] | None:
        lineage = report.get("lineage") if isinstance(report.get("lineage"), Mapping) else {}
        reference = lineage.get("candidate_contract_ref")
        if not reference:
            return None
        try:
            path = _safe_path(self.root, str(reference))
        except ResearchEvolutionProposalError:
            return None
        if not path.exists():
            return None
        payload = _load_json(path)
        contracts = payload.get("contracts") if isinstance(payload, Mapping) else payload
        if isinstance(contracts, list):
            for contract in contracts:
                if isinstance(contract, Mapping) and (not candidate_id or str(contract.get("candidate_id") or "") == candidate_id):
                    return contract
        return payload if isinstance(payload, Mapping) else None

    @staticmethod
    def _direction_id(value: Any) -> str | None:
        if isinstance(value, Mapping):
            value = _first_scalar(value, ("direction_id", "id", "name", "direction"))
        if value in (None, ""):
            return None
        raw = str(value).strip().casefold()
        normalized = _DIRECTION_ALIASES.get(raw, _normalize_name(raw))
        return normalized if normalized in ALLOWED_RESEARCH_DIRECTIONS else None

    @staticmethod
    def _avoid_tokens(mechanism: str, families: Iterable[str], factor_ids: Iterable[str]) -> set[str]:
        tokens = set()
        for value in (mechanism, *families, *factor_ids):
            for item in re.split(r"[+|/,]", str(value or "")):
                normalized = _normalize_name(item)
                if normalized:
                    tokens.add(normalized)
                    if "amount_accel" in normalized or "liquidity" in normalized or "illiq" in normalized:
                        tokens.add("liquidity_acceleration")
        return tokens

    @classmethod
    def _is_duplicate_direction(cls, direction: str, avoid_tokens: set[str]) -> bool:
        for token in avoid_tokens:
            if direction == token or direction.startswith(f"{token}_") or token.startswith(f"{direction}_"):
                return True
        return False

    @classmethod
    def _suggest_directions(cls, coverage: Mapping[str, Any], avoid_tokens: set[str]) -> list[str]:
        candidates: list[str] = []
        for value in (*_as_list(coverage.get("unexplored")), *DEFAULT_RESEARCH_DIRECTIONS):
            direction = cls._direction_id(value)
            if direction and direction not in candidates and not cls._is_duplicate_direction(direction, avoid_tokens):
                candidates.append(direction)
            if len(candidates) == 3:
                break
        return candidates

    def generate_proposal(
        self,
        report: Mapping[str, Any] | str | Path | None = None,
        failure_landscape: Mapping[str, Any] | str | Path | None = None,
        candidate_contract: Mapping[str, Any] | str | Path | None = None,
        mechanism_coverage: Mapping[str, Any] | str | Path | None = None,
        *,
        objective_id: str | None = None,
        candidate_id: str | None = None,
        trial_id: str | None = None,
        report_path: str | Path | None = None,
        evolution_report: Mapping[str, Any] | str | Path | None = None,
    ) -> dict[str, Any]:
        """Generate a Proposal from safe projections of the supplied sources.

        ``report`` may be an in-memory report, a path, or omitted to select the
        latest report for ``objective_id``.  No method in this class creates or
        starts a downstream research artifact.
        """
        # Support the natural ``generate_proposal(objective_id)`` call while
        # keeping the primary API useful for in-memory, outcome-blind tests.
        if isinstance(report, str) and objective_id is None and failure_landscape is None and candidate_contract is None and mechanism_coverage is None and report_path is None and evolution_report is None and _IDENTIFIER_RE.fullmatch(report):
            objective_id = report
            report = None
        # Also accept the existing manager's positional identity shape without
        # treating candidate/trial identifiers as JSON paths.
        if isinstance(report, str) and isinstance(failure_landscape, str) and isinstance(candidate_contract, str) and mechanism_coverage is None and report_path is None and evolution_report is None and _IDENTIFIER_RE.fullmatch(report) and _IDENTIFIER_RE.fullmatch(failure_landscape) and _IDENTIFIER_RE.fullmatch(candidate_contract):
            objective_id, candidate_id, trial_id = report, failure_landscape, candidate_contract
            report = None
            failure_landscape = None
            candidate_contract = None

        objective_id = self._validate_identifier(objective_id, "objective_id")
        candidate_id = self._validate_identifier(candidate_id, "candidate_id")
        trial_id = self._validate_identifier(trial_id, "trial_id")
        if evolution_report is not None:
            if report is not None:
                raise ResearchEvolutionProposalError("DUPLICATE_REPORT_INPUT", "研究演进报告输入重复")
            report = evolution_report
        if report_path is not None:
            if report is not None:
                raise ResearchEvolutionProposalError("DUPLICATE_REPORT_INPUT", "研究演进报告输入重复")
            report = report_path

        source_report, source_path = _read_source(self.root, report)
        if source_report is None:
            source_report, source_path = self._latest_report(objective_id, candidate_id, trial_id)
        report_lineage = source_report.get("lineage") if isinstance(source_report.get("lineage"), Mapping) else {}
        objective_id = self._validate_identifier(objective_id or str(report_lineage.get("objective_id") or source_report.get("objective_id") or ""), "objective_id")
        candidate_id = self._validate_identifier(candidate_id or str(report_lineage.get("candidate_id") or source_report.get("candidate_id") or ""), "candidate_id")
        trial_id = self._validate_identifier(trial_id or str(report_lineage.get("trial_id") or source_report.get("trial_id") or ""), "trial_id")
        if not objective_id:
            raise ResearchEvolutionProposalError("LINEAGE_INCOMPLETE", "失败报告缺少 parent objective 身份")

        landscape, _ = _read_source(self.root, failure_landscape)
        if landscape is None:
            landscape = self._load_landscape(objective_id)
        contract, _ = _read_source(self.root, candidate_contract)
        if contract is None:
            contract = self._candidate_from_lineage(source_report, candidate_id)
        sources = _candidate_sources(source_report, contract)
        factor_ids = _extract_factor_ids(sources)
        mechanism = _extract_mechanism(sources, factor_ids)
        families = _extract_families(sources, mechanism, factor_ids)
        categories = _extract_categories(source_report, landscape, candidate_id or "", trial_id or "")
        coverage = self.coverage_registry.load(mechanism_coverage)
        source_ref = _safe_relative(self.root, source_path) or str(source_report.get("report_id") or "in_memory:research_evolution_report")
        coverage = self.coverage_registry.record(families, source_report=source_ref, objective_id=objective_id, payload=coverage)
        avoid_tokens = self._avoid_tokens(mechanism, families, factor_ids)
        directions = self._suggest_directions(coverage, avoid_tokens)
        if not directions:
            raise ResearchEvolutionProposalError("NO_SAFE_RESEARCH_DIRECTION", "覆盖注册表没有可供人工审核的独立研究方向")

        safe_lineage = _lineage(source_report, contract)
        safe_lineage.update({key: value for key, value in (("objective_id", objective_id), ("candidate_id", candidate_id), ("trial_id", trial_id)) if value})
        safe_input = {
            "report": _safe_report_input(source_report, safe_lineage, mechanism, families, categories),
            "landscape": _coverage_payload(landscape) if landscape else {},
            "candidate": {"factor_ids": factor_ids, "mechanism": mechanism, "mechanism_families": families},
            "coverage": coverage,
            "suggested_research_directions": directions,
        }
        source_input_hash = stable_hash(safe_input)
        if self.proposal_path.exists():
            existing = _load_json(self.proposal_path)
            if isinstance(existing, Mapping) and existing.get("source_input_hash") == source_input_hash and existing.get("parent_objective_id") == objective_id:
                PerformanceBlindGuard.assert_blind(existing)
                return dict(existing)

        proposal_base: dict[str, Any] = {
            "schema_version": PROPOSAL_SCHEMA_VERSION,
            "proposal_type": "RESEARCH_EVOLUTION_PROPOSAL",
            "generated_at": now_timestamp(),
            "proposal_id": f"RESEARCH_EVOLUTION_PROPOSAL_{objective_id}_{source_input_hash[:12]}",
            "parent_objective_id": objective_id,
            "parent_candidate_id": candidate_id,
            "parent_trial_id": trial_id,
            "source_failure_report": source_ref,
            "source_failure_report_hash": str(source_report.get("report_hash") or ""),
            "failed_mechanism": mechanism,
            "failed_mechanism_family": families[0],
            "failure_summary": categories,
            "avoid_mechanism_family": families,
            "avoid_family": families,
            "suggested_research_directions": directions,
            "mechanism_coverage": coverage,
            "lineage": safe_lineage,
            "governance_state": HUMAN_REVIEW_REQUIRED,
            "state_history": [EVOLUTION_ANALYZED, PROPOSAL_CREATED, CREATED, HUMAN_REVIEW_REQUIRED],
            "human_approval_required": True,
            "outcome_blind": True,
            "governance": {
                "human_approval_required": True,
                "current_state": HUMAN_REVIEW_REQUIRED,
                "next_allowed_states": [APPROVED, CREATE_NEW_OBJECTIVE],
                "automatic_objective_created": False,
                "automatic_candidate_created": False,
                "automatic_trial_started": False,
                "codex_called": False,
                "budget_consumed": False,
                "stops_before_new_objective": True,
                "next_action": "HUMAN_REVIEW",
            },
            "source_input_hash": source_input_hash,
        }
        PerformanceBlindGuard.assert_blind(proposal_base)
        proposal = dict(proposal_base)
        proposal["proposal_hash"] = stable_hash(proposal_base)
        _atomic_write_json(self.proposal_path, proposal)
        return proposal

    generate = generate_proposal
    run = generate_proposal


ResearchEvolutionProposalManagerV1 = ResearchEvolutionProposalManager


def _main() -> int:
    parser = argparse.ArgumentParser(description="生成只到人工审核边界的研究演进建议")
    parser.add_argument("--root", default=".", help="项目根目录")
    parser.add_argument("--objective-id")
    parser.add_argument("--candidate-id")
    parser.add_argument("--trial-id")
    parser.add_argument("--report-path")
    args = parser.parse_args()
    proposal = ResearchEvolutionProposalManager(args.root).generate_proposal(
        objective_id=args.objective_id,
        candidate_id=args.candidate_id,
        trial_id=args.trial_id,
        report_path=args.report_path,
    )
    print(json.dumps({"状态": "已生成研究演进建议", "建议编号": proposal["proposal_id"], "治理状态": proposal["governance_state"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "APPROVED",
    "ALLOWED_RESEARCH_DIRECTIONS",
    "CLOSED",
    "CREATE_OBJECTIVE",
    "CREATE_NEW_OBJECTIVE",
    "CREATED",
    "DEFAULT_RESEARCH_DIRECTIONS",
    "EVOLUTION_ANALYZED",
    "HUMAN_REVIEW_REQUIRED",
    "MechanismCoverageRegistry",
    "MechanismCoverageRegistryV1",
    "OBJECTIVE_CREATION_READY",
    "PROPOSAL_FILENAME",
    "PROPOSAL_SCHEMA_VERSION",
    "PROPOSAL_CREATED",
    "READY_FOR_CONFIRMATION",
    "REJECTED",
    "ResearchEvolutionProposalError",
    "ResearchEvolutionProposalManager",
    "ResearchEvolutionProposalManagerV1",
]
