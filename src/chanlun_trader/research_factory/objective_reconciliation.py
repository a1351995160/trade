"""Read-only reconciliation of Research Factory objective state.

The service in this module is intentionally a read model.  It never invokes
the daemon, orchestrator, provider, AI backend, structural preflight, trial
executor, or any mutating factory service.  Its only writes are the two
explicit report files requested by the CLI.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..presentation import ZhCNPresentation, write_report_pair
from .canonical_authority import CANONICAL_AUTHORITY_CONTRACT_V1
from .common import stable_hash
from .durability import DurableFrozenCandidateContractV1


RECONCILIATION_SCHEMA_VERSION = "objective-reconciliation-v1"
GLOBAL_INDEX_SCHEMA_VERSION = "current-objective-reconciliation-index-v1"
OBJECTIVE_DIALECT_DEFINITION_ONLY = "DEFINITION_ONLY"
OBJECTIVE_DIALECT_EVOLUTION_MANUAL_CREATED = "EVOLUTION_MANUAL_CREATED"
OBJECTIVE_DIALECT_GOVERNANCE_EXECUTION_READY = "GOVERNANCE_EXECUTION_READY"
OBJECTIVE_DIALECT_LEGACY_UNKNOWN = "LEGACY_UNKNOWN"

CONSISTENT = "CONSISTENT"
PROJECTION_DRIFT = "PROJECTION_DRIFT"
REPAIRABLE_INDEX_DRIFT = "REPAIRABLE_INDEX_DRIFT"
CANONICAL_CONFLICT = "CANONICAL_CONFLICT"

AI_DESIGN_READY = "AI_DESIGN_READY"
AI_DESIGN_APPROVED = "AI_DESIGN_APPROVED"
AI_DESIGN_AWAITING_CONFIRMATION = "AI_DESIGN_AWAITING_CONFIRMATION"
NEED_AI_RESEARCH_DESIGN = "NEED_AI_RESEARCH_DESIGN"
CANDIDATE_PROPOSAL_READY = "CANDIDATE_PROPOSAL_READY"
CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION = "CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION"
READY_FOR_STRUCTURAL_PREFLIGHT = "READY_FOR_STRUCTURAL_PREFLIGHT"
TRIAL_ACTIVE = "TRIAL_ACTIVE"
TRIAL_TERMINAL = "TRIAL_TERMINAL"
BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
CANONICAL_STATE_CONFLICT = "CANONICAL_STATE_CONFLICT"
BUDGET_AUTHORITY_AMBIGUOUS = "BUDGET_AUTHORITY_AMBIGUOUS"

HUMAN_CONFIRM_AI_RESEARCH_DESIGN = "HUMAN_CONFIRM_AI_RESEARCH_DESIGN"
HUMAN_REVIEW_CANDIDATE_PROPOSAL = "HUMAN_REVIEW_CANDIDATE_PROPOSAL"
RUN_STRUCTURAL_PREFLIGHT = "RUN_STRUCTURAL_PREFLIGHT"
STOP_RESEARCH = "STOP_RESEARCH"

TRIAL_TERMINAL_STATUSES = {"COMPLETED", "BLOCKED", "INVALIDATED", "SUPERSEDED"}
ACTIVE_PROJECTION_STATES = {
    "STRUCTURAL_RUNNING",
    "PREDICTIVE_PENDING",
    "PREDICTIVE_RUNNING",
    "TRIAL_RUNNING",
    "TRIAL_ACTIVE",
    "RUNNING",
    "ACTIVE",
}
FREEZE_STATES = {
    "FROZEN",
    "READY_FOR_STRUCTURAL_PREFLIGHT",
    "CANDIDATE_FROZEN",
    "FREEZE_PREVIEW_READY",
}


def _walk_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for nested in value.values():
            yield from _walk_mappings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_mappings(nested)


def _values_for_keys(value: Any, keys: set[str]) -> list[Any]:
    values: list[Any] = []
    for item in _walk_mappings(value):
        for key in keys:
            if key in item and item[key] not in (None, ""):
                values.append(item[key])
    return values


def _first_value(value: Any, keys: set[str], default: Any = None) -> Any:
    values = _values_for_keys(value, keys)
    return values[0] if values else default


def _string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "yes", "1", "approved", "authorized", "pass", "passed"}
    return bool(value)


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _safe_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _safe_document(path: Path) -> Any:
    if path.suffix.casefold() == ".jsonl":
        lines: list[Any] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {line_number}: {exc.msg}") from exc
        return lines
    return json.loads(path.read_text(encoding="utf-8"))


def _document_mentions_objective(value: Any, objective_id: str) -> bool:
    for item in _walk_mappings(value):
        for key in ("objective_id", "new_objective_id", "created_objective_id", "target_objective_id"):
            if str(item.get(key, "")) == objective_id:
                return True
    return any(str(item) == objective_id for item in _values_for_keys(value, {"objective", "objective_ref", "target"}))


def _identity_values(value: Any) -> dict[str, list[str]]:
    def values(keys: set[str]) -> list[str]:
        result = []
        for item in _values_for_keys(value, keys):
            if isinstance(item, (str, int, float)):
                result.append(str(item))
        return sorted(set(result))

    return {
        "objective_ids": values({"objective_id", "new_objective_id", "created_objective_id"}),
        "candidate_ids": values({"candidate_id"}),
        "candidate_hashes": values({"candidate_hash", "candidate_preregistration_hash"}),
        "trial_ids": values({"trial_id"}),
        "reservation_ids": values({"budget_reservation_identity", "reservation_id"}),
    }


def _identity_scalar(values: Mapping[str, list[str]]) -> dict[str, Any]:
    return {
        "objective_id": values["objective_ids"][0] if len(values["objective_ids"]) == 1 else None,
        "candidate_id": values["candidate_ids"][0] if len(values["candidate_ids"]) == 1 else None,
        "candidate_hash": values["candidate_hashes"][0] if len(values["candidate_hashes"]) == 1 else None,
        "trial_id": values["trial_ids"][0] if len(values["trial_ids"]) == 1 else None,
        "reservation_id": values["reservation_ids"][0] if len(values["reservation_ids"]) == 1 else None,
    }


@dataclass(frozen=True)
class EffectiveObjectiveStateV1:
    """Derived objective state. Constructing this record never persists it."""

    objective_id: str
    objective_dialect: str
    effective_stage: str
    effective_state: str
    required_action: str | None
    required_action_supported: bool
    safe_to_resume: bool
    safe_to_advance: bool
    current_candidate_id: str | None = None
    current_candidate_hash: str | None = None
    current_trial_id: str | None = None
    budget: Mapping[str, Any] = field(default_factory=dict)
    canonical_refs: Mapping[str, Any] = field(default_factory=dict)
    projection_refs: Mapping[str, Any] = field(default_factory=dict)
    conflicts: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    structural_preflight_ready: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective_id": self.objective_id,
            "objective_dialect": self.objective_dialect,
            "effective_stage": self.effective_stage,
            "effective_state": self.effective_state,
            "required_action": self.required_action,
            "required_action_supported": self.required_action_supported,
            "safe_to_resume": self.safe_to_resume,
            "safe_to_advance": self.safe_to_advance,
            "current_candidate_id": self.current_candidate_id,
            "current_candidate_hash": self.current_candidate_hash,
            "current_trial_id": self.current_trial_id,
            "budget": dict(self.budget),
            "canonical_refs": dict(self.canonical_refs),
            "projection_refs": dict(self.projection_refs),
            "conflicts": list(self.conflicts),
            "warnings": list(self.warnings),
            "structural_preflight_ready": self.structural_preflight_ready,
        }


class ObjectiveDialectClassifierV1:
    """Classify creation dialect without treating dynamic fields as truth."""

    def classify(self, objective: Mapping[str, Any], objective_id: str | None = None) -> dict[str, Any]:
        payload = dict(objective or {})
        actual_id = _string(payload.get("objective_id"))
        expected_id = objective_id or actual_id
        identity_valid = bool(actual_id and expected_id and actual_id == expected_id)
        lifecycle = _string(payload.get("lifecycle_state"))
        activation_authorized = payload.get("activation_authorized")
        governance_action = _string(payload.get("governance_action"))
        has_parent = bool(payload.get("parent_objective_id") or payload.get("parent_proposal_id") or payload.get("parent_proposal_hash"))
        has_execution_semantics = bool(payload.get("execution_semantics_version") or payload.get("no_outcome_context_ref"))

        if not identity_valid:
            dialect = OBJECTIVE_DIALECT_LEGACY_UNKNOWN
        elif lifecycle is None and not has_parent and activation_authorized is None and governance_action is None:
            dialect = OBJECTIVE_DIALECT_DEFINITION_ONLY
        elif lifecycle == "CREATED" and (has_parent or activation_authorized is False or payload.get("activation_policy") == "MANUAL_ONLY"):
            dialect = OBJECTIVE_DIALECT_EVOLUTION_MANUAL_CREATED
        elif lifecycle == "READY" and _bool(activation_authorized) and bool(governance_action) and has_execution_semantics:
            dialect = OBJECTIVE_DIALECT_GOVERNANCE_EXECUTION_READY
        else:
            dialect = OBJECTIVE_DIALECT_LEGACY_UNKNOWN

        return {
            "dialect": dialect,
            "objective_identity_valid": identity_valid,
            "creation_state": lifecycle or "UNSPECIFIED",
            "activation_metadata": {
                "activation_authorized": activation_authorized,
                "activation_policy": payload.get("activation_policy"),
                "governance_action": governance_action,
                "execution_semantics_version": payload.get("execution_semantics_version"),
                "no_outcome_context_ref": payload.get("no_outcome_context_ref"),
            },
            "dynamic_lifecycle_authoritative": False,
            "reason_code": None if dialect != OBJECTIVE_DIALECT_LEGACY_UNKNOWN else "OBJECTIVE_DIALECT_UNKNOWN",
        }

    classify_objective = classify


class _ReconciliationContext:
    def __init__(self, root: Path, objective_id: str) -> None:
        self.root = root
        self.objective_id = objective_id
        self.evidence: dict[str, list[dict[str, Any]]] = {}
        self.sources: dict[str, list[dict[str, Any]]] = {}
        self.warnings: list[str] = []
        self.conflicts: list[str] = []
        self.conflict_details: list[dict[str, Any]] = []
        self.conflict_level = CONSISTENT
        self._seen_sources: set[tuple[str, str]] = set()

    def warning(self, code: str) -> None:
        if code not in self.warnings:
            self.warnings.append(code)

    def conflict(self, code: str, level: str, **detail: Any) -> None:
        if code not in self.conflicts:
            self.conflicts.append(code)
        if level not in self.conflicts and level != CONSISTENT:
            self.conflicts.append(level)
        rank = {CONSISTENT: 0, PROJECTION_DRIFT: 1, REPAIRABLE_INDEX_DRIFT: 2, CANONICAL_CONFLICT: 3}
        if rank.get(level, 3) > rank.get(self.conflict_level, 0):
            self.conflict_level = level
        if detail:
            self.conflict_details.append({"code": code, "level": level, **detail})

    def add_source(self, category: str, path: Path, role: str, payload: Any | None = None, error: str | None = None) -> dict[str, Any]:
        relative = _relative_path(self.root, path)
        key = (category, relative)
        if key in self._seen_sources:
            return next(item for item in self.sources[category] if item["meta"]["path"] == relative)
        self._seen_sources.add(key)
        if error:
            meta = {
                "path": relative,
                "authority_role": role,
                "read_status": "ERROR",
                "read_error": error,
                "source_hash": None,
                "source_classification": "UNRESOLVED",
                "identity": {"objective_id": None, "candidate_id": None, "candidate_hash": None, "trial_id": None, "reservation_id": None},
            }
        else:
            values = _identity_values(payload)
            scalar = _identity_scalar(values)
            meta = {
                "path": relative,
                "authority_role": role,
                "read_status": "PASS",
                "source_hash": stable_hash(payload),
                "source_classification": "UNRESOLVED",
                "identity": {
                    **scalar,
                    "objective_ids": values["objective_ids"],
                    "candidate_ids": values["candidate_ids"],
                    "candidate_hashes": values["candidate_hashes"],
                    "trial_ids": values["trial_ids"],
                    "reservation_ids": values["reservation_ids"],
                },
                "schema_version": _first_value(payload, {"schema_version"}),
            }
        self.sources.setdefault(category, []).append({"meta": meta, "payload": payload, "path": path})
        self.evidence.setdefault(category, []).append(meta)
        return self.sources[category][-1]


def _source_meta(source: Mapping[str, Any]) -> dict[str, Any]:
    return dict(source["meta"])


def _events_from_payload(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping) and isinstance(payload.get("events"), list):
        return [item for item in payload["events"] if isinstance(item, Mapping)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping) and payload.get("trial_id"):
        return [payload]
    return []


def _contract_payloads(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping) and isinstance(payload.get("contracts"), list):
        return [item for item in payload["contracts"] if isinstance(item, Mapping)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping) and item.get("candidate_id")]
    if isinstance(payload, Mapping) and payload.get("candidate_id"):
        return [payload]
    return []


def _candidate_records(payload: Any) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in _walk_mappings(payload):
        candidate = item.get("candidate")
        if isinstance(candidate, Mapping) and candidate.get("candidate_id"):
            record = candidate
        elif item.get("candidate_id"):
            record = item
        else:
            continue
        candidate_id = str(record.get("candidate_id"))
        candidate_hash = str(record.get("candidate_hash") or record.get("candidate_preregistration_hash") or "")
        key = (candidate_id, candidate_hash)
        if key not in seen:
            seen.add(key)
            result.append(record)
    return result


def _status_values(payload: Any) -> list[str]:
    values: list[str] = []
    for item in _walk_mappings(payload):
        for key in ("status", "state", "current_state", "governance_state", "terminal_state"):
            value = item.get(key)
            if value not in (None, ""):
                values.append(str(value))
    return sorted(set(values))


def _budget_reservations(payload: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    active: dict[str, dict[str, Any]] = {}
    raw_active = payload.get("active_reservations", ())
    if isinstance(raw_active, Mapping):
        raw_active = [{"reservation_id": key, "status": value} for key, value in raw_active.items()]
    for item in raw_active if isinstance(raw_active, list) else ():
        if isinstance(item, Mapping):
            reservation_id = item.get("reservation_id") or item.get("id")
            if reservation_id:
                active[str(reservation_id)] = dict(item)
        elif item not in (None, ""):
            active[str(item)] = {"reservation_id": str(item), "status": "ACTIVE"}
    settled: dict[str, str] = {}
    raw_settled = payload.get("settled_reservations", {})
    if isinstance(raw_settled, Mapping):
        settled = {str(key): str(value.get("status") if isinstance(value, Mapping) else value) for key, value in raw_settled.items()}
    elif isinstance(raw_settled, list):
        for item in raw_settled:
            if isinstance(item, Mapping) and (item.get("reservation_id") or item.get("id")):
                settled[str(item.get("reservation_id") or item.get("id"))] = str(item.get("status") or "SETTLED")
    return active, settled


def _budget_summary(payload: Mapping[str, Any], objective_id: str) -> dict[str, Any]:
    buckets = [item for item in payload.get("buckets", ()) if isinstance(item, Mapping)]
    objective_buckets = [
        item for item in buckets
        if str(item.get("key", "")) == objective_id or str(item.get("kind", "")).casefold() == "objective"
    ]
    source = objective_buckets[0] if objective_buckets else {}
    total = _safe_number(payload.get("total"))
    used = _safe_number(payload.get("used"))
    reserved = _safe_number(payload.get("reserved"))
    if total is None:
        total = _safe_number(source.get("limit"))
    if total is None:
        total = _safe_number(payload.get("limit") or payload.get("max_total_trials"))
    if used is None:
        used = _safe_number(source.get("used"))
    if reserved is None:
        reserved = _safe_number(source.get("reserved"))
    if reserved is None and (total is not None or used is not None):
        reserved = 0
    remaining = _safe_number(payload.get("remaining"))
    if remaining is None:
        remaining = _safe_number(source.get("remaining"))
    if remaining is None and total is not None and used is not None and reserved is not None:
        remaining = total - used - reserved
    active, settled = _budget_reservations(payload)
    deterministic = {
        "objective_id": str(payload.get("objective_id") or objective_id),
        "buckets": [dict(item) for item in buckets],
        "active_reservations": sorted(active),
        "settled_reservations": dict(sorted(settled.items())),
    }
    return {
        "objective_id": str(payload.get("objective_id") or objective_id),
        "total": total,
        "used": used,
        "reserved": reserved,
        "remaining": remaining,
        "buckets": [dict(item) for item in buckets],
        "active_reservations": [dict(item) for item in active.values()],
        "settled_reservations": dict(sorted(settled.items())),
        "registry_head_hash": str(payload.get("head_hash") or payload.get("registry_head_hash") or payload.get("registry_hash") or stable_hash(deterministic)),
    }


def _iter_reference_strings(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            lowered = str(key).casefold()
            if isinstance(nested, str) and (
                "search_budget_registry" in nested.casefold()
                or ("budget" in lowered and any(token in lowered for token in ("ref", "path", "registry", "authority", "canonical")))
            ):
                yield nested
            else:
                yield from _iter_reference_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_reference_strings(nested)


def _resolve_reference(root: Path, reference: str) -> Path:
    raw = reference.replace("\\", "/")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / raw
    return candidate


class ObjectiveReconciliationServiceV1:
    """Build objective-scoped read-only reconciliation reports."""

    def __init__(self, root: str | Path = ".", objective_id: str | None = None) -> None:
        self.root = Path(root).resolve()
        self.objective_id = str(objective_id) if objective_id else None
        self.classifier = ObjectiveDialectClassifierV1()

    def discover_objective_ids(self) -> list[str]:
        directory = self.root / "data/research/research_factory/objectives"
        result: list[str] = []
        if not directory.exists():
            return result
        for path in sorted(directory.glob("*.json")):
            try:
                payload = _safe_document(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(payload, Mapping) and payload.get("objective_id"):
                result.append(str(payload["objective_id"]))
        return sorted(set(result))

    def reconcile_objective(self, objective_id: str | None = None) -> dict[str, Any]:
        return self.reconcile(objective_id)

    def reconcile(self, objective_id: str | None = None) -> dict[str, Any]:
        resolved_objective_id = objective_id or self.objective_id
        if not resolved_objective_id:
            raise ValueError("objective_id is required for reconciliation")
        objective_id = str(resolved_objective_id)
        if Path(objective_id).name != objective_id or "/" in objective_id or "\\" in objective_id:
            raise ValueError("objective_id must be a single path component")
        ctx = _ReconciliationContext(self.root, objective_id)
        objective_path = self.root / "data/research/research_factory/objectives" / f"{objective_id}.json"
        objective_payload: Mapping[str, Any] = {}
        if objective_path.is_file():
            try:
                loaded = _safe_document(objective_path)
                if isinstance(loaded, Mapping):
                    objective_payload = loaded
                    ctx.add_source("objective_definition", objective_path, "OBJECTIVE_DEFINITION_FACT", loaded)
                else:
                    ctx.add_source("objective_definition", objective_path, "OBJECTIVE_DEFINITION_FACT", error="objective JSON must be an object")
                    ctx.warning("OBJECTIVE_DEFINITION_INVALID")
            except Exception as exc:
                ctx.add_source("objective_definition", objective_path, "OBJECTIVE_DEFINITION_FACT", error=str(exc))
                ctx.warning("OBJECTIVE_DEFINITION_READ_FAILED")
        else:
            ctx.warning("OBJECTIVE_DEFINITION_MISSING")

        dialect = self.classifier.classify(objective_payload, objective_id=objective_id)
        if dialect["reason_code"]:
            ctx.warning(dialect["reason_code"])

        self._collect_governance(ctx)
        self._collect_ai_design(ctx)
        self._collect_candidate_governance(ctx)
        self._collect_batch_authorities(ctx)
        self._collect_structural_predictive_and_projections(ctx)
        self._collect_validation_and_final(ctx)

        ai_view = self._reconcile_ai_design(ctx)
        candidate_view = self._reconcile_candidates(ctx)
        self._reconcile_contracts(ctx, candidate_view)
        trial_view = self._reconcile_trials(ctx)
        budget_view = self._reconcile_budget(ctx, objective_payload, trial_view)
        graph_view = self._reconcile_graph(ctx, candidate_view)
        projection_view = self._reconcile_projections(ctx, budget_view, trial_view)
        self._classify_artifacts(ctx, candidate_view, trial_view, graph_view)

        effective = self._derive_effective_state(
            objective_id,
            dialect,
            ctx,
            candidate_view,
            trial_view,
            budget_view,
            graph_view,
        )
        report: dict[str, Any] = {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "report_kind": "OBJECTIVE_RECONCILIATION_V1",
            "read_only": True,
            "objective_id": objective_id,
            "canonical_authority_contract": CANONICAL_AUTHORITY_CONTRACT_V1.to_dict(),
            "objective_definition": dict(objective_payload),
            "objective_dialect": dialect,
            "ai_design_reconciliation": ai_view,
            "effective_objective_state": effective.to_dict(),
            "effective_state": effective.effective_state,
            "effective_stage": effective.effective_stage,
            "required_action": effective.required_action,
            "required_action_supported": effective.required_action_supported,
            "safe_to_resume": effective.safe_to_resume,
            "safe_to_advance": effective.safe_to_advance,
            "structural_preflight_ready": effective.structural_preflight_ready,
            "current_candidate_id": effective.current_candidate_id,
            "current_candidate_hash": effective.current_candidate_hash,
            "current_trial_id": effective.current_trial_id,
            "budget": budget_view,
            "candidate_reconciliation": candidate_view,
            "trial_reconciliation": trial_view,
            "artifact_graph_reconciliation": graph_view,
            "projection_reconciliation": projection_view,
            "evidence_inventory": {key: list(value) for key, value in sorted(ctx.evidence.items())},
            "canonical_refs": dict(effective.canonical_refs),
            "projection_refs": dict(effective.projection_refs),
            "conflict_level": ctx.conflict_level,
            "conflicts": sorted(ctx.conflicts),
            "conflict_details": list(ctx.conflict_details),
            "warnings": sorted(ctx.warnings),
            "classification_vocabulary": [
                "CURRENT_CANONICAL",
                "HISTORICAL_CANONICAL",
                "LEGACY",
                "ORPHAN",
                "UNRESOLVED",
            ],
            "artifact_classification": getattr(ctx, "artifact_classification", {"candidates": [], "trials": [], "artifacts": []}),
        }
        report["report_hash"] = stable_hash(report)
        return report

    def reconcile_all(self) -> list[dict[str, Any]]:
        return [self.reconcile(objective_id) for objective_id in self.discover_objective_ids()]

    def write_report(self, report: Mapping[str, Any]) -> tuple[Path, Path]:
        objective_id = str(report["objective_id"])
        directory = self.root / "reports/research_reconciliation" / objective_id
        machine_path = directory / "OBJECTIVE_RECONCILIATION_V1.json"
        human_path = directory / "OBJECTIVE_RECONCILIATION_V1.md"
        write_report_pair(machine_path, report, human_path, self._render_markdown(report))
        return machine_path, human_path

    def write_all_reports(self) -> dict[str, Any]:
        reports = self.reconcile_all()
        for report in reports:
            self.write_report(report)
        index = self._build_index(reports)
        index_directory = self.root / "reports/research_reconciliation"
        write_report_pair(
            index_directory / "CURRENT_OBJECTIVE_RECONCILIATION_INDEX_V1.json",
            index,
            index_directory / "CURRENT_OBJECTIVE_RECONCILIATION_INDEX_V1.md",
            self._render_index_markdown(index),
        )
        return index

    def _add_if_objective(self, ctx: _ReconciliationContext, category: str, path: Path, role: str, *, path_scoped: bool = False) -> None:
        if not path.is_file():
            return
        try:
            payload = _safe_document(path)
        except Exception as exc:
            ctx.add_source(category, path, role, error=str(exc))
            ctx.warning("SOURCE_READ_FAILED")
            return
        if path_scoped or _document_mentions_objective(payload, ctx.objective_id):
            ctx.add_source(category, path, role, payload)

    def _collect_governance(self, ctx: _ReconciliationContext) -> None:
        proposal_root = self.root / "reports/research_evolution/proposals"
        self._add_if_objective(
            ctx,
            "proposal_governance",
            proposal_root / "RESEARCH_EVOLUTION_PROPOSAL.json",
            "CANDIDATE_PROPOSAL_GOVERNANCE_FACT",
        )
        governance_root = proposal_root / "governance"
        if governance_root.exists():
            for directory in sorted(item for item in governance_root.iterdir() if item.is_dir()):
                candidate_paths = [
                    directory / "objective_creation_preview.json",
                    directory / "objective_creation_receipt.json",
                    directory / "objective_creation_governance.json",
                    directory / "reviews.jsonl",
                ]
                loaded: list[tuple[Path, Any]] = []
                directory_matches = ctx.objective_id in directory.name
                for path in candidate_paths:
                    if not path.is_file():
                        continue
                    try:
                        payload = _safe_document(path)
                    except Exception as exc:
                        ctx.add_source("proposal_governance", path, "CANDIDATE_PROPOSAL_GOVERNANCE_FACT", error=str(exc))
                        ctx.warning("SOURCE_READ_FAILED")
                        continue
                    loaded.append((path, payload))
                    directory_matches = directory_matches or _document_mentions_objective(payload, ctx.objective_id)
                if directory_matches:
                    for path, payload in loaded:
                        ctx.add_source("proposal_governance", path, "CANDIDATE_PROPOSAL_GOVERNANCE_FACT", payload)

        for base in (
            self.root / "reports/research_governance" / ctx.objective_id,
            self.root / "reports/research_governance_execution" / ctx.objective_id,
        ):
            if base.is_dir():
                for path in sorted(base.iterdir()):
                    if path.suffix.casefold() in {".json", ".jsonl"}:
                        self._add_if_objective(ctx, "proposal_governance", path, "CANDIDATE_PROPOSAL_GOVERNANCE_FACT", path_scoped=True)

        lineage_path = self.root / "data/research/research_factory/lineage" / f"{ctx.objective_id}.json"
        self._add_if_objective(ctx, "lineage", lineage_path, "LINEAGE_INDEX_EVIDENCE", path_scoped=True)

    def _collect_ai_design(self, ctx: _ReconciliationContext) -> None:
        directory = self.root / "reports/research_evolution/ai_design" / ctx.objective_id
        if not directory.is_dir():
            return
        for path in sorted(directory.iterdir()):
            if path.suffix.casefold() in {".json", ".jsonl"}:
                self._add_if_objective(ctx, "ai_design", path, "AI_DESIGN_FACT", path_scoped=True)

    def _reconcile_ai_design(self, ctx: _ReconciliationContext) -> dict[str, Any]:
        sources = ctx.sources.get("ai_design", ())
        proposal_source = next(
            (
                source
                for source in sources
                if source["path"].name == "AI_RESEARCH_DESIGN_PROPOSAL.json"
            ),
            None,
        )
        state_source = next(
            (
                source
                for source in sources
                if source["path"].name == "AI_RESEARCH_DESIGN_STATE.json"
            ),
            None,
        )
        proposal = (
            proposal_source.get("payload")
            if proposal_source and isinstance(proposal_source.get("payload"), Mapping)
            else {}
        )
        state = (
            state_source.get("payload")
            if state_source and isinstance(state_source.get("payload"), Mapping)
            else {}
        )
        approval_sources = [
            source
            for source in sources
            if any(
                token in source["path"].name.casefold()
                for token in ("approval", "approved", "confirmation")
            )
            or (
                isinstance(source.get("payload"), Mapping)
                and (
                    source["payload"].get("approval_id")
                    or source["payload"].get("approval_status")
                    or str(source["payload"].get("status") or "").upper()
                    in {AI_DESIGN_APPROVED, "APPROVED", "CONFIRMED"}
                )
            )
        ]
        approvals = [
            source["meta"]["path"]
            for source in approval_sources
            if self._is_ai_approval(source.get("payload"))
        ]
        design_exists = bool(proposal_source)
        status = str(proposal.get("status") or state.get("status") or "")
        governance = proposal.get("governance") if isinstance(proposal.get("governance"), Mapping) else {}
        requires_confirmation = _bool(
            state.get("requires_human_confirmation")
            or state.get("human_confirmation_required")
            or governance.get("requires_human_confirmation")
            or governance.get("human_confirmation_required")
        )
        approved = bool(approvals)
        approval_status = (
            "APPROVED"
            if approved
            else "MISSING"
            if design_exists and requires_confirmation
            else "NOT_REQUIRED"
            if design_exists
            else "NOT_AVAILABLE"
        )
        if approval_status == "MISSING":
            ctx.warning("AI_DESIGN_APPROVAL_EVIDENCE_MISSING")
        return {
            "design_exists": design_exists,
            "design_status": status or None,
            "design_id": proposal.get("design_id") or state.get("design_id"),
            "design_hash": proposal.get("design_hash") or state.get("design_hash"),
            "requires_human_confirmation": requires_confirmation,
            "approval_status": approval_status,
            "approval_evidence": approvals,
            "approval_evidence_missing": approval_status == "MISSING",
            "state_path": state_source["meta"]["path"] if state_source else None,
            "proposal_path": proposal_source["meta"]["path"] if proposal_source else None,
        }

    def _collect_candidate_governance(self, ctx: _ReconciliationContext) -> None:
        directory = self.root / "reports/research_candidates/proposals" / ctx.objective_id
        if directory.is_dir():
            for path in sorted(directory.iterdir()):
                if path.suffix.casefold() in {".json", ".jsonl"}:
                    self._add_if_objective(ctx, "candidate_governance", path, "CANDIDATE_PROPOSAL_GOVERNANCE_FACT", path_scoped=True)
        registry = self.root / "data/research/research_factory/candidates" / ctx.objective_id / "CANDIDATE_REGISTRY.json"
        self._add_if_objective(ctx, "candidate_governance", registry, "CANDIDATE_GOVERNANCE_FREEZE_FACT", path_scoped=True)

    def _collect_batch_authorities(self, ctx: _ReconciliationContext) -> None:
        batch_root = self.root / "data/research/research_factory/batches"
        if batch_root.exists():
            for path in sorted(batch_root.glob("*/search_budget_registry.json")):
                self._add_if_objective(ctx, "budget_registry", path, "SEARCH_BUDGET_AUTHORITY")
            for path in sorted(batch_root.glob("*/factory_trial_ledger.json")):
                self._add_if_objective(ctx, "trial_ledger", path, "TRIAL_LIFECYCLE_AUTHORITY")
            for path in sorted(batch_root.glob("*/durable_frozen_candidate_contracts.json")):
                self._add_if_objective(ctx, "durable_frozen_contract", path, "EXECUTABLE_FROZEN_CANDIDATE_AUTHORITY")

        reference_payloads = [
            source["payload"]
            for category in ("objective_definition", "proposal_governance", "lineage")
            for source in ctx.sources.get(category, ())
            if source.get("payload") is not None
        ]
        for reference in sorted(set(ref for payload in reference_payloads for ref in _iter_reference_strings(payload))):
            path = _resolve_reference(self.root, reference)
            if path.name == "search_budget_registry.json" and path.is_file():
                self._add_if_objective(ctx, "budget_registry", path, "SEARCH_BUDGET_AUTHORITY")

    def _collect_structural_predictive_and_projections(self, ctx: _ReconciliationContext) -> None:
        daemon_directory = self.root / "reports/research_daemon" / ctx.objective_id
        orchestrator_directory = self.root / "reports/research_orchestrator_v2" / ctx.objective_id

        if daemon_directory.is_dir():
            for path in sorted(daemon_directory.iterdir()):
                if path.suffix.casefold() not in {".json", ".jsonl"}:
                    continue
                name = path.name.casefold()
                if name in {"daemon_checkpoint.json", "daemon_status.json"}:
                    self._add_if_objective(ctx, "daemon_projection", path, "RUNTIME_PROJECTION", path_scoped=True)
                elif any(token in name for token in ("structural", "preflight")):
                    self._add_if_objective(ctx, "structural_preflight", path, "STRUCTURAL_PREFLIGHT_FACT", path_scoped=True)
                elif any(token in name for token in ("predictive", "authorization", "authorisation")):
                    self._add_if_objective(ctx, "predictive_authorization", path, "PREDICTIVE_AUTHORIZATION_FACT", path_scoped=True)
                elif any(token in name for token in ("validation", "adjudication", "final")):
                    self._add_if_objective(ctx, "validation_final_adjudication", path, "VALIDATION_FINAL_ADJUDICATION_FACT", path_scoped=True)

        if orchestrator_directory.is_dir():
            for path in sorted(orchestrator_directory.iterdir()):
                if path.suffix.casefold() not in {".json", ".jsonl"}:
                    continue
                name = path.name.casefold()
                if name in {"orchestrator_checkpoint.json", "orchestrator_status.json"}:
                    self._add_if_objective(ctx, "orchestrator_projection", path, "RUNTIME_PROJECTION", path_scoped=True)
                elif any(token in name for token in ("validation", "adjudication", "final")):
                    self._add_if_objective(ctx, "validation_final_adjudication", path, "VALIDATION_FINAL_ADJUDICATION_FACT", path_scoped=True)
                elif any(token in name for token in ("structural", "preflight")):
                    self._add_if_objective(ctx, "structural_preflight", path, "STRUCTURAL_PREFLIGHT_FACT", path_scoped=True)
                elif any(token in name for token in ("predictive", "authorization", "authorisation")):
                    self._add_if_objective(ctx, "predictive_authorization", path, "PREDICTIVE_AUTHORIZATION_FACT", path_scoped=True)

    def _collect_validation_and_final(self, ctx: _ReconciliationContext) -> None:
        for path in sorted((self.root / "data/research/research_factory/batches").glob("*/trial_registry.json")):
            self._add_if_objective(ctx, "validation_final_adjudication", path, "VALIDATION_FINAL_ADJUDICATION_FACT")
        for path in sorted((self.root / "data/research/research_factory/batches").glob("*/strategy_registry.json")):
            self._add_if_objective(ctx, "validation_final_adjudication", path, "VALIDATION_FINAL_ADJUDICATION_FACT")

        directory = self.root / "reports/research_factory" / ctx.objective_id
        if directory.is_dir():
            for path in sorted(directory.iterdir()):
                if path.suffix.casefold() in {".json", ".jsonl"} and any(token in path.name.casefold() for token in ("validation", "adjudication", "final")):
                    self._add_if_objective(ctx, "validation_final_adjudication", path, "VALIDATION_FINAL_ADJUDICATION_FACT", path_scoped=True)

    def _reconcile_candidates(self, ctx: _ReconciliationContext) -> dict[str, Any]:
        candidates: dict[str, dict[str, Any]] = {}
        proposal_present = False
        registry_present = False
        freeze_receipt_present = False
        for source in ctx.sources.get("candidate_governance", ()):
            path_name = source["path"].name.casefold()
            proposal_present = proposal_present or "candidate_proposal" in path_name
            registry_present = registry_present or path_name == "candidate_registry.json"
            freeze_receipt_present = freeze_receipt_present or "freeze_receipt" in path_name or "freeze_governance" in path_name
            for record in _candidate_records(source.get("payload")):
                candidate_id = str(record.get("candidate_id"))
                candidate_hash = str(record.get("candidate_hash") or record.get("candidate_preregistration_hash") or "")
                item = candidates.setdefault(candidate_id, {
                    "candidate_id": candidate_id,
                    "candidate_hashes": [],
                    "sources": [],
                    "freeze_evidence": False,
                    "states": [],
                })
                if candidate_hash and candidate_hash not in item["candidate_hashes"]:
                    item["candidate_hashes"].append(candidate_hash)
                item["sources"].append(source["meta"]["path"])
                states = _status_values(record)
                item["states"] = sorted(set(item["states"]) | set(states))
                item["freeze_evidence"] = (
                    item["freeze_evidence"]
                    or registry_present
                    or freeze_receipt_present
                    or _bool(record.get("candidate_frozen"))
                    or bool(set(states) & FREEZE_STATES)
                )

        registry_candidates = sorted(candidates)
        freeze_present = bool(
            registry_present
            or freeze_receipt_present
            or any(item["freeze_evidence"] for item in candidates.values())
        )
        only_candidate = registry_candidates[0] if len(registry_candidates) == 1 else None
        only_hashes = candidates[only_candidate]["candidate_hashes"] if only_candidate else []
        return {
            "proposal_present": proposal_present,
            "registry_present": registry_present,
            "freeze_receipt_present": freeze_receipt_present,
            "governance_freeze_present": freeze_present,
            "candidates": [
                {
                    **item,
                    "candidate_hashes": sorted(item["candidate_hashes"]),
                    "sources": sorted(set(item["sources"])),
                }
                for _, item in sorted(candidates.items())
            ],
            "registry_candidate_ids": registry_candidates,
            "durable_contracts": [],
            "executable_frozen_candidate": False,
            "structural_preflight_ready": False,
            "current_candidate_id": only_candidate,
            "current_candidate_hash": only_hashes[0] if len(only_hashes) == 1 else None,
        }

    def _reconcile_contracts(self, ctx: _ReconciliationContext, candidate_view: dict[str, Any]) -> None:
        entries: list[dict[str, Any]] = []
        by_candidate: dict[str, set[str]] = {}
        for source in ctx.sources.get("durable_frozen_contract", ()):
            for raw in _contract_payloads(source.get("payload")):
                policy = raw.get("policy_identity") if isinstance(raw.get("policy_identity"), Mapping) else {}
                raw_objective_id = str(policy.get("objective_id") or raw.get("objective_id") or "")
                candidate_id = str(raw.get("candidate_id") or "")
                candidate_hash = str(raw.get("candidate_hash") or "")
                if raw_objective_id != ctx.objective_id:
                    continue
                entry: dict[str, Any] = {
                    "source_path": source["meta"]["path"],
                    "objective_id": raw_objective_id,
                    "candidate_id": candidate_id,
                    "candidate_hash": candidate_hash,
                    "content_hash": raw.get("content_hash"),
                    "identity_match": bool(candidate_id and candidate_hash and raw_objective_id == ctx.objective_id),
                    "from_dict": "NOT_RUN",
                    "provider_candidate_payload": "NOT_RUN",
                    "validation_error": None,
                }
                if entry["identity_match"]:
                    by_candidate.setdefault(candidate_id, set()).add(candidate_hash)
                    try:
                        contract = DurableFrozenCandidateContractV1.from_dict(raw)
                        entry["from_dict"] = "PASS"
                    except Exception as exc:
                        entry["from_dict"] = "FAIL"
                        entry["validation_error"] = str(exc)
                        entries.append(entry)
                        continue
                    try:
                        contract.provider_candidate_payload()
                        entry["provider_candidate_payload"] = "PASS"
                    except Exception as exc:
                        entry["provider_candidate_payload"] = "FAIL"
                        entry["validation_error"] = str(exc)
                entries.append(entry)

        registry_hashes = {
            item["candidate_id"]: set(item.get("candidate_hashes", ()))
            for item in candidate_view["candidates"]
        }
        for candidate_id, contract_hashes in sorted(by_candidate.items()):
            known_hashes = registry_hashes.get(candidate_id, set())
            if known_hashes and contract_hashes and not (known_hashes & contract_hashes):
                ctx.conflict(
                    "CANONICAL_CANDIDATE_IDENTITY_CONFLICT",
                    CANONICAL_CONFLICT,
                    candidate_id=candidate_id,
                    registry_hashes=sorted(known_hashes),
                    durable_contract_hashes=sorted(contract_hashes),
                )
        for candidate_id, hashes in sorted(by_candidate.items()):
            if len(hashes) > 1:
                ctx.conflict(
                    "CANONICAL_CANDIDATE_IDENTITY_CONFLICT",
                    CANONICAL_CONFLICT,
                    candidate_id=candidate_id,
                    durable_contract_hashes=sorted(hashes),
                )

        contract_source_paths = sorted({item["meta"]["path"] for item in ctx.sources.get("durable_frozen_contract", ())})
        valid_source_paths = sorted({
            item["source_path"]
            for item in entries
            if item["from_dict"] == "PASS" and item["provider_candidate_payload"] == "PASS"
        })
        for path in contract_source_paths:
            meta = next(item["meta"] for item in ctx.sources["durable_frozen_contract"] if item["meta"]["path"] == path)
            if path in valid_source_paths:
                meta["source_classification"] = "CURRENT_CANONICAL"
            elif any(item["source_path"] == path and item["identity_match"] for item in entries):
                meta["source_classification"] = "IDENTITY_CONFLICT"
            else:
                meta["source_classification"] = "HISTORICAL_CANONICAL"

        target_id = candidate_view.get("current_candidate_id")
        target_hash = candidate_view.get("current_candidate_hash")
        if not target_id and len(by_candidate) == 1:
            target_id = next(iter(by_candidate))
            if len(by_candidate[target_id]) == 1:
                target_hash = next(iter(by_candidate[target_id]))
        unique_passes = {
            (item["objective_id"], item["candidate_id"], item["candidate_hash"])
            for item in entries
            if item["identity_match"]
            and item["from_dict"] == "PASS"
            and item["provider_candidate_payload"] == "PASS"
            and (not target_id or item["candidate_id"] == target_id)
            and (not target_hash or item["candidate_hash"] == target_hash)
        }
        executable = len(unique_passes) == 1
        candidate_view["durable_contracts"] = entries
        candidate_view["executable_frozen_candidate"] = executable
        candidate_view["structural_preflight_ready"] = executable
        candidate_view["current_candidate_id"] = target_id
        candidate_view["current_candidate_hash"] = target_hash
        candidate_view["contract_identity_count"] = len(by_candidate)
        candidate_view["unique_executable_identity_count"] = len(unique_passes)

    def _reconcile_trials(self, ctx: _ReconciliationContext) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        source_counts: dict[str, int] = {}
        for source in ctx.sources.get("trial_ledger", ()):
            events = _events_from_payload(source.get("payload"))
            source_counts[source["meta"]["path"]] = 0
            for index, event in enumerate(events):
                if str(event.get("objective_id") or "") != ctx.objective_id or not event.get("trial_id"):
                    continue
                source_counts[source["meta"]["path"]] += 1
                grouped.setdefault(str(event["trial_id"]), []).append({
                    "event": event,
                    "source_path": source["meta"]["path"],
                    "index": index,
                })

        trials: list[dict[str, Any]] = []
        for trial_id, items in sorted(grouped.items()):
            candidate_ids = sorted({str(item["event"].get("candidate_id") or "") for item in items})
            candidate_hashes = sorted({
                str(item["event"].get("candidate_hash") or item["event"].get("candidate_preregistration_hash") or "")
                for item in items
            })
            batch_ids = sorted({str(item["event"].get("batch_id") or "") for item in items})
            statuses = sorted({
                str(item["event"].get("status") or item["event"].get("terminal_state") or "")
                for item in items
                if item["event"].get("status") or item["event"].get("terminal_state")
            })
            terminal_statuses = sorted(set(statuses) & TRIAL_TERMINAL_STATUSES)
            event_types = sorted({
                str(item["event"].get("event_type") or "")
                for item in items
                if item["event"].get("event_type")
            })
            reservations = sorted({
                str(
                    item["event"].get("budget_reservation_identity")
                    or (item["event"].get("lineage") or {}).get("budget_reservation_identity")
                    or ""
                )
                for item in items
            })
            reservations = [item for item in reservations if item]
            performance_accessed = any(_bool(item["event"].get("performance_accessed")) for item in items)
            performance_complete = any(_bool(item["event"].get("performance_complete")) for item in items)
            final_adjudicated = any(_bool(item["event"].get("final_adjudicated")) for item in items)
            latest = sorted(items, key=lambda item: (str(item["source_path"]), item["index"]))[-1]["event"]
            if len(candidate_ids) > 1 or len(candidate_hashes) > 1 or len(batch_ids) > 1:
                ctx.conflict(
                    "CANONICAL_TRIAL_IDENTITY_CONFLICT",
                    CANONICAL_CONFLICT,
                    trial_id=trial_id,
                    candidate_ids=candidate_ids,
                    candidate_hashes=candidate_hashes,
                    batch_ids=batch_ids,
                )
            if len(terminal_statuses) > 1:
                ctx.conflict(
                    "CANONICAL_TRIAL_TERMINAL_CONFLICT",
                    CANONICAL_CONFLICT,
                    trial_id=trial_id,
                    terminal_statuses=terminal_statuses,
                )
            trials.append({
                "trial_id": trial_id,
                "objective_id": ctx.objective_id,
                "batch_ids": batch_ids,
                "candidate_ids": candidate_ids,
                "candidate_hashes": candidate_hashes,
                "status": str(latest.get("status") or latest.get("terminal_state") or ""),
                "terminal_status": terminal_statuses[0] if len(terminal_statuses) == 1 else None,
                "terminal_statuses": terminal_statuses,
                "event_types": event_types,
                "event_count": len(items),
                "performance_accessed": performance_accessed,
                "performance_complete": performance_complete,
                "final_adjudicated": final_adjudicated,
                "budget_reservation_identities": reservations,
                "source_paths": sorted({item["source_path"] for item in items}),
                "canonical_source_count": len({item["source_path"] for item in items}),
            })

        active = [item for item in trials if not item["terminal_status"]]
        current = active[0] if active else (trials[-1] if trials else None)
        ledger_paths = sorted(source_counts)
        for index, path in enumerate(ledger_paths):
            meta = next(item["meta"] for item in ctx.sources["trial_ledger"] if item["meta"]["path"] == path)
            meta["source_classification"] = "CURRENT_CANONICAL" if index == 0 else "DUPLICATE"
        return {
            "source_count": len(ctx.sources.get("trial_ledger", ())),
            "source_event_counts": source_counts,
            "trial_count": len(trials),
            "performance_accessed_trial_count": sum(1 for item in trials if item["performance_accessed"]),
            "active_trial_count": len(active),
            "terminal_trial_count": sum(1 for item in trials if item["terminal_status"]),
            "trials": trials,
            "current_trial_id": current["trial_id"] if current else None,
            "canonical_refs": sorted(source_counts),
        }

    def _reconcile_budget(
        self,
        ctx: _ReconciliationContext,
        objective_payload: Mapping[str, Any],
        trial_view: Mapping[str, Any],
    ) -> dict[str, Any]:
        del objective_payload
        sources = []
        for source in ctx.sources.get("budget_registry", ()):
            payload = source.get("payload")
            if not isinstance(payload, Mapping):
                continue
            if str(payload.get("objective_id") or "") != ctx.objective_id:
                continue
            sources.append({"source": source, "summary": _budget_summary(payload, ctx.objective_id)})

        immutable_payloads = [
            source.get("payload")
            for category in ("objective_definition", "proposal_governance", "lineage")
            for source in ctx.sources.get(category, ())
            if source.get("payload") is not None
        ]
        explicit_refs = sorted(set(
            reference
            for payload in immutable_payloads
            for reference in _iter_reference_strings(payload)
        ))
        source_by_path = {
            _relative_path(self.root, item["source"]["path"]): item
            for item in sources
        }
        resolved_paths = sorted(set(
            path
            for reference in explicit_refs
            for path in [_relative_path(self.root, _resolve_reference(self.root, reference))]
            if path in source_by_path
        ))

        authority_status = "MISSING"
        selected: dict[str, Any] | None = None
        if len(resolved_paths) == 1:
            authority_status = "UNIQUE_CANONICAL"
            selected = source_by_path[resolved_paths[0]]
        elif len(sources) == 1:
            authority_status = "UNIQUE_CANONICAL"
            selected = sources[0]
        elif len(sources) > 1:
            authority_status = "AMBIGUOUS"
            ctx.conflict(
                BUDGET_AUTHORITY_AMBIGUOUS,
                CANONICAL_CONFLICT,
                source_paths=sorted(source_by_path),
            )
        elif explicit_refs:
            ctx.warning("BUDGET_AUTHORITY_REFERENCE_UNRESOLVED")
        else:
            ctx.warning("BUDGET_AUTHORITY_MISSING")

        for item in sources:
            meta = item["source"]["meta"]
            if selected is not None and item["source"] is selected["source"]:
                meta["source_classification"] = "CURRENT_CANONICAL"
            elif authority_status == "AMBIGUOUS":
                meta["source_classification"] = "UNRESOLVED"
            else:
                meta["source_classification"] = "HISTORICAL_CANONICAL"

        canonical = dict(selected["summary"]) if selected else None
        if selected:
            self._reconcile_budget_with_trials(ctx, selected["summary"], trial_view)
        return {
            "authority_status": authority_status,
            "explicit_references": explicit_refs,
            "resolved_references": resolved_paths,
            "source_count": len(sources),
            "sources": [
                {**_source_meta(item["source"]), "budget": dict(item["summary"])}
                for item in sources
            ],
            "canonical": canonical,
            "trial_reconciliation": {
                "performance_accessed_trial_count": trial_view.get("performance_accessed_trial_count", 0),
                "checked": selected is not None,
            },
        }

    def _reconcile_budget_with_trials(
        self,
        ctx: _ReconciliationContext,
        budget: Mapping[str, Any],
        trial_view: Mapping[str, Any],
    ) -> None:
        trials = list(trial_view.get("trials", ()))
        if not trials:
            return
        used = budget.get("used")
        accessed_count = sum(1 for trial in trials if trial.get("performance_accessed"))
        if used is not None and int(used) != accessed_count:
            ctx.conflict(
                "CANONICAL_BUDGET_TRIAL_COUNT_MISMATCH",
                CANONICAL_CONFLICT,
                budget_used=used,
                performance_accessed_trial_count=accessed_count,
            )

        active, settled = _budget_reservations(budget)
        reservation_status = {
            key: str(value.get("status") or "ACTIVE")
            for key, value in active.items()
        }
        reservation_status.update(settled)
        trial_reservations: dict[str, list[str]] = {}
        for trial in trials:
            ids = list(trial.get("budget_reservation_identities", ()))
            trial_reservations[trial["trial_id"]] = ids
            if trial.get("performance_accessed") and not ids:
                ctx.conflict("BUDGET_RESERVATION_MISSING", CANONICAL_CONFLICT, trial_id=trial["trial_id"])
            if len(ids) > 1:
                ctx.conflict(
                    "BUDGET_TRIAL_MULTIPLE_RESERVATIONS",
                    CANONICAL_CONFLICT,
                    trial_id=trial["trial_id"],
                    reservation_ids=ids,
                )
            for reservation_id in ids:
                if reservation_id not in reservation_status:
                    ctx.conflict(
                        "BUDGET_RESERVATION_MISSING",
                        CANONICAL_CONFLICT,
                        trial_id=trial["trial_id"],
                        reservation_id=reservation_id,
                    )
                if trial.get("terminal_status") and reservation_status.get(reservation_id) == "ACTIVE":
                    ctx.conflict(
                        "BUDGET_HANGING_RESERVATION",
                        CANONICAL_CONFLICT,
                        trial_id=trial["trial_id"],
                        reservation_id=reservation_id,
                    )

        trial_reservation_ids = {
            reservation_id
            for ids in trial_reservations.values()
            for reservation_id in ids
        }
        for reservation_id, status in reservation_status.items():
            if status == "CONSUMED" and reservation_id not in trial_reservation_ids:
                ctx.conflict(
                    "BUDGET_CONSUMED_WITHOUT_TRIAL",
                    CANONICAL_CONFLICT,
                    reservation_id=reservation_id,
                )
            if status == "ACTIVE" and reservation_id not in trial_reservation_ids:
                ctx.conflict(
                    "BUDGET_HANGING_RESERVATION",
                    CANONICAL_CONFLICT,
                    reservation_id=reservation_id,
                )

    def _reconcile_graph(
        self,
        ctx: _ReconciliationContext,
        candidate_view: Mapping[str, Any],
    ) -> dict[str, Any]:
        path = self.root / "data/research/research_factory/artifact_graph" / f"{ctx.objective_id}.json"
        if not path.is_file():
            return {
                "source_count": 0,
                "graphs": [],
                "missing_edges": [],
                "identity_conflicts": [],
            }
        try:
            payload = _safe_document(path)
        except Exception as exc:
            ctx.add_source("artifact_graph", path, "LINEAGE_INDEX_EVIDENCE", error=str(exc))
            ctx.warning("ARTIFACT_GRAPH_READ_FAILED")
            return {
                "source_count": 1,
                "graphs": [],
                "missing_edges": [],
                "identity_conflicts": [],
            }
        ctx.add_source("artifact_graph", path, "LINEAGE_INDEX_EVIDENCE", payload)
        ctx.evidence["artifact_graph"][-1]["source_classification"] = "CURRENT_CANONICAL"
        nodes = [
            item for item in payload.get("nodes", ())
            if isinstance(item, Mapping)
        ] if isinstance(payload, Mapping) else []
        edges = [
            item for item in payload.get("edges", ())
            if isinstance(item, Mapping)
        ] if isinstance(payload, Mapping) else []
        node_infos: list[dict[str, Any]] = []
        for node in nodes:
            node_id = str(node.get("node_id") or "")
            node_type = str(node.get("node_type") or "")
            info: dict[str, Any] = {
                "node_id": node_id,
                "node_type": node_type,
                "payload_hash": node.get("payload_hash"),
            }
            if node_type == "Objective" and node_id.startswith("objective:"):
                value = node_id[len("objective:"):]
                info["objective_id"] = value[:-len(":Objective")] if value.endswith(":Objective") else value
            elif node_type == "Candidate" and node_id.startswith("candidate:"):
                value = node_id[len("candidate:"):]
                info["candidate_id"] = value[:-len(":Candidate")] if value.endswith(":Candidate") else value
            elif node_type == "FrozenCandidateContract" and node_id.startswith("frozen_contract:"):
                value = node_id[len("frozen_contract:"):]
                value = value[:-len(":FrozenCandidateContract")] if value.endswith(":FrozenCandidateContract") else value
                candidate_id, separator, candidate_hash = value.rpartition(":")
                info["candidate_id"] = candidate_id if separator else value
                info["candidate_hash"] = candidate_hash if separator else None
            for key in ("objective_id", "candidate_id", "candidate_hash", "trial_id"):
                if key in node and node[key] not in (None, ""):
                    info[key] = str(node[key])
            node_infos.append(info)

        edge_keys = {
            (
                str(item.get("source_id") or item.get("source_node_id") or ""),
                str(item.get("edge_type") or ""),
                str(item.get("target_id") or item.get("target_node_id") or ""),
            )
            for item in edges
        }
        current_id = candidate_view.get("current_candidate_id")
        current_hash = candidate_view.get("current_candidate_hash")
        identity_conflicts: list[dict[str, Any]] = []
        candidate_nodes = [
            item for item in node_infos
            if item.get("node_type") == "Candidate"
            and (not current_id or item.get("candidate_id") == current_id)
        ]
        contract_nodes = [
            item for item in node_infos
            if item.get("node_type") == "FrozenCandidateContract"
            and (not current_id or item.get("candidate_id") == current_id)
        ]
        for item in contract_nodes:
            if current_hash and item.get("candidate_hash") and item.get("candidate_hash") != current_hash:
                detail = {
                    "node_id": item["node_id"],
                    "graph_candidate_hash": item.get("candidate_hash"),
                    "canonical_candidate_hash": current_hash,
                }
                identity_conflicts.append(detail)
                ctx.conflict(
                    "CANONICAL_ARTIFACT_GRAPH_IDENTITY_CONFLICT",
                    CANONICAL_CONFLICT,
                    **detail,
                )
        missing_edges: list[dict[str, Any]] = []
        for candidate in candidate_nodes:
            for contract in contract_nodes:
                expected = (
                    candidate["node_id"],
                    "HAS_DURABLE_CONTRACT",
                    contract["node_id"],
                )
                if expected not in edge_keys:
                    missing_edges.append({
                        "source_id": candidate["node_id"],
                        "edge_type": "HAS_DURABLE_CONTRACT",
                        "target_id": contract["node_id"],
                    })
        if missing_edges:
            ctx.conflict(
                "ARTIFACT_GRAPH_MISSING_EDGE",
                REPAIRABLE_INDEX_DRIFT,
                missing_edges=missing_edges,
            )
        return {
            "source_count": 1,
            "graphs": [{
                "path": _relative_path(self.root, path),
                "source_hash": stable_hash(payload),
                "node_count": len(nodes),
                "edge_count": len(edges),
                "nodes": node_infos,
                "edges": [
                    {
                        "source_id": item[0],
                        "edge_type": item[1],
                        "target_id": item[2],
                    }
                    for item in sorted(edge_keys)
                ],
            }],
            "missing_edges": missing_edges,
            "identity_conflicts": identity_conflicts,
        }

    def _projection_view(self, source: Mapping[str, Any]) -> dict[str, Any]:
        payload = source.get("payload")
        states = _status_values(payload)
        action = _first_value(payload, {"required_action", "next_action"})
        trial_id = _first_value(payload, {"current_trial_id"})
        candidate_id = _first_value(payload, {"current_candidate_id"})
        budget_views: list[dict[str, Any]] = []
        for item in _walk_mappings(payload):
            if not ("used" in item and ("total" in item or "remaining" in item or "limit" in item)):
                continue
            objective = item.get("objective_id")
            if objective is not None and str(objective) != self._objective_id_for_projection(source):
                continue
            budget_views.append({
                "objective_id": str(objective) if objective is not None else None,
                "used": _safe_number(item.get("used")),
                "total": _safe_number(item.get("total") if item.get("total") is not None else item.get("limit")),
                "remaining": _safe_number(item.get("remaining")),
                "reserved": _safe_number(item.get("reserved")),
            })
        return {
            "path": source["meta"]["path"],
            "source_hash": source["meta"]["source_hash"],
            "states": states,
            "required_action": str(action) if action not in (None, "") else None,
            "current_trial_id": str(trial_id) if trial_id not in (None, "") else None,
            "current_candidate_id": str(candidate_id) if candidate_id not in (None, "") else None,
            "budget_views": budget_views,
            "canonical_refs": (
                dict(payload.get("canonical_refs"))
                if isinstance(payload, Mapping) and isinstance(payload.get("canonical_refs"), Mapping)
                else None
            ),
        }

    def _objective_id_for_projection(self, source: Mapping[str, Any]) -> str:
        identity = source["meta"].get("identity", {})
        return str(identity.get("objective_id") or getattr(self, "_objective_id", ""))

    def _reconcile_projections(
        self,
        ctx: _ReconciliationContext,
        budget_view: Mapping[str, Any],
        trial_view: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._objective_id = ctx.objective_id
        projections = [
            self._projection_view(source)
            for category in ("daemon_projection", "orchestrator_projection")
            for source in ctx.sources.get(category, ())
        ]
        canonical_budget = budget_view.get("canonical") if isinstance(budget_view, Mapping) else None
        canonical_budget_path = self._canonical_budget_path(budget_view)
        for projection in projections:
            for view in projection["budget_views"]:
                if canonical_budget is None:
                    continue
                comparable = (
                    view.get("used"),
                    view.get("total"),
                    view.get("remaining"),
                    view.get("reserved"),
                )
                canonical_comparable = (
                    canonical_budget.get("used"),
                    canonical_budget.get("total"),
                    canonical_budget.get("remaining"),
                    canonical_budget.get("reserved"),
                )
                if any(value is not None for value in comparable) and comparable != canonical_comparable:
                    ctx.conflict(
                        "PROJECTION_BUDGET_COUNTER_DRIFT",
                        PROJECTION_DRIFT,
                        projection_path=projection["path"],
                        projection_budget=view,
                        canonical_budget=canonical_budget,
                    )
            projection_budget_ref = projection.get("canonical_refs")
            if isinstance(projection_budget_ref, Mapping):
                referenced_budget = projection_budget_ref.get("budget")
                if isinstance(referenced_budget, Mapping):
                    referenced_budget = referenced_budget.get("registry_path")
                if (
                    canonical_budget_path
                    and referenced_budget
                    and _relative_path(
                        self.root,
                        _resolve_reference(self.root, str(referenced_budget)),
                    ) != canonical_budget_path
                ):
                    ctx.conflict(
                        "PROJECTION_BUDGET_REFERENCE_DRIFT",
                        PROJECTION_DRIFT,
                        projection_path=projection["path"],
                        projection_budget_reference=str(referenced_budget),
                        canonical_budget_path=canonical_budget_path,
                    )
            states = set(projection["states"])
            if states & ACTIVE_PROJECTION_STATES and trial_view.get("terminal_trial_count", 0):
                ctx.conflict(
                    "PROJECTION_ACTIVE_AFTER_CANONICAL_TRIAL_TERMINAL",
                    PROJECTION_DRIFT,
                    projection_path=projection["path"],
                    canonical_trial_count=trial_view.get("terminal_trial_count"),
                )
            if states & ACTIVE_PROJECTION_STATES and canonical_budget and canonical_budget.get("remaining") is not None and canonical_budget.get("remaining") <= 0:
                ctx.conflict(
                    "PROJECTION_ACTIVE_AFTER_CANONICAL_BUDGET_EXHAUSTED",
                    PROJECTION_DRIFT,
                    projection_path=projection["path"],
                    canonical_remaining=canonical_budget.get("remaining"),
                )
            if projection.get("current_trial_id") and any(
                item.get("trial_id") == projection["current_trial_id"] and item.get("terminal_status")
                for item in trial_view.get("trials", ())
            ):
                ctx.conflict(
                    "PROJECTION_CURRENT_TRIAL_TERMINAL",
                    PROJECTION_DRIFT,
                    projection_path=projection["path"],
                    trial_id=projection["current_trial_id"],
                )
        return {
            "projection_count": len(projections),
            "projections": projections,
            "projection_drift_detected": PROJECTION_DRIFT in ctx.conflicts,
        }

    def _classify_artifacts(
        self,
        ctx: _ReconciliationContext,
        candidate_view: Mapping[str, Any],
        trial_view: Mapping[str, Any],
        graph_view: Mapping[str, Any],
    ) -> None:
        current_candidate_id = candidate_view.get("current_candidate_id")
        current_candidate_hash = candidate_view.get("current_candidate_hash")
        candidates: list[dict[str, Any]] = []
        for item in candidate_view.get("candidates", ()):
            hashes = set(item.get("candidate_hashes", ()))
            if current_candidate_id == item.get("candidate_id") and (
                not current_candidate_hash or current_candidate_hash in hashes
            ):
                classification = "CURRENT_CANONICAL"
            elif hashes:
                classification = "HISTORICAL_CANONICAL"
            else:
                classification = "UNRESOLVED"
            candidates.append({
                "candidate_id": item.get("candidate_id"),
                "candidate_hashes": sorted(hashes),
                "classification": classification,
            })
        trials = [
            {
                "trial_id": item["trial_id"],
                "candidate_id": item["candidate_ids"][0] if len(item["candidate_ids"]) == 1 else None,
                "candidate_hash": item["candidate_hashes"][0] if len(item["candidate_hashes"]) == 1 else None,
                "classification": "CURRENT_CANONICAL",
            }
            for item in trial_view.get("trials", ())
        ]
        artifacts: list[dict[str, Any]] = []
        for graph in graph_view.get("graphs", ()):
            for node in graph.get("nodes", ()):
                objective = node.get("objective_id")
                classification = "CURRENT_CANONICAL" if objective in (None, ctx.objective_id) else "HISTORICAL_CANONICAL"
                artifacts.append({
                    "node_id": node.get("node_id"),
                    "node_type": node.get("node_type"),
                    "classification": classification,
                })
        ctx.artifact_classification = {
            "candidates": candidates,
            "trials": trials,
            "artifacts": artifacts,
        }

    def _derive_effective_state(
        self,
        objective_id: str,
        dialect: Mapping[str, Any],
        ctx: _ReconciliationContext,
        candidate_view: Mapping[str, Any],
        trial_view: Mapping[str, Any],
        budget_view: Mapping[str, Any],
        graph_view: Mapping[str, Any],
    ) -> EffectiveObjectiveStateV1:
        del graph_view
        ai_sources = ctx.sources.get("ai_design", ())
        proposal = next(
            (
                source.get("payload")
                for source in ai_sources
                if source["path"].name == "AI_RESEARCH_DESIGN_PROPOSAL.json"
                and isinstance(source.get("payload"), Mapping)
            ),
            None,
        )
        state_payload = next(
            (
                source.get("payload")
                for source in ai_sources
                if source["path"].name == "AI_RESEARCH_DESIGN_STATE.json"
                and isinstance(source.get("payload"), Mapping)
            ),
            None,
        )
        design_exists = proposal is not None
        approval_sources = [
            source
            for source in ai_sources
            if any(token in source["path"].name.casefold() for token in ("approval", "approved", "confirmation"))
            or (
                isinstance(source.get("payload"), Mapping)
                and str(
                    source["payload"].get("status")
                    or source["payload"].get("approval_status")
                    or ""
                ).upper() in {AI_DESIGN_APPROVED, "APPROVED", "CONFIRMED"}
            )
            or (
                isinstance(source.get("payload"), Mapping)
                and source["payload"].get("approval_id")
            )
        ]
        design_approved = any(
            self._is_ai_approval(source.get("payload"))
            for source in approval_sources
        )
        requires_confirmation = (
            _bool(_first_value(state_payload, {"requires_human_confirmation", "human_confirmation_required"}))
            if state_payload
            else False
        )
        if not requires_confirmation and proposal:
            governance = (
                proposal.get("governance")
                if isinstance(proposal.get("governance"), Mapping)
                else {}
            )
            requires_confirmation = _bool(
                governance.get("requires_human_confirmation")
                or governance.get("human_confirmation_required")
            )
        if design_exists and requires_confirmation and not design_approved:
            ctx.warning("AI_DESIGN_APPROVAL_EVIDENCE_MISSING")

        candidate_id = candidate_view.get("current_candidate_id")
        candidate_hash = candidate_view.get("current_candidate_hash")
        current_trial = trial_view.get("current_trial_id")
        canonical_budget = budget_view.get("canonical") if isinstance(budget_view, Mapping) else None
        budget_ambiguous = budget_view.get("authority_status") == "AMBIGUOUS"
        budget_exhausted = bool(
            canonical_budget
            and canonical_budget.get("remaining") is not None
            and canonical_budget.get("remaining") <= 0
        )
        active_trial = bool(trial_view.get("active_trial_count"))
        terminal_trial = bool(trial_view.get("terminal_trial_count"))
        executable = bool(candidate_view.get("executable_frozen_candidate"))
        governance_freeze = bool(candidate_view.get("governance_freeze_present"))
        proposal_ready = bool(candidate_view.get("proposal_present")) and not governance_freeze

        if budget_ambiguous:
            stage, state, action = (
                "BUDGET",
                BUDGET_AUTHORITY_AMBIGUOUS,
                "RESOLVE_BUDGET_AUTHORITY_AMBIGUITY",
            )
        elif CANONICAL_CONFLICT in ctx.conflicts:
            stage, state, action = (
                "CANONICAL_RECONCILIATION",
                CANONICAL_STATE_CONFLICT,
                "STOP_AND_RECONCILE_CANONICAL_CONFLICT",
            )
        elif active_trial:
            stage, state, action = "TRIAL_LIFECYCLE", TRIAL_ACTIVE, None
        elif budget_exhausted:
            stage, state, action = "BUDGET", BUDGET_EXHAUSTED, STOP_RESEARCH
        elif terminal_trial:
            stage, state, action = "TRIAL_LIFECYCLE", TRIAL_TERMINAL, STOP_RESEARCH
        elif executable:
            structural_status = self._canonical_structural_status(ctx)
            predictive_authorized = self._canonical_predictive_authorized(ctx)
            if structural_status == "PASS" and not predictive_authorized:
                stage, state, action = (
                    "PREDICTIVE_AUTHORIZATION",
                    "PREDICTIVE_AUTHORIZATION_REQUIRED",
                    "AUTHORIZE_PREDICTIVE_TRIAL",
                )
            else:
                stage, state, action = (
                    "STRUCTURAL_PREFLIGHT",
                    READY_FOR_STRUCTURAL_PREFLIGHT,
                    RUN_STRUCTURAL_PREFLIGHT,
                )
        elif governance_freeze:
            stage, state, action = (
                "CANDIDATE_GOVERNANCE_FREEZE",
                CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION,
                "MATERIALIZE_EXECUTABLE_FROZEN_CANDIDATE_CONTRACT",
            )
        elif proposal_ready:
            stage, state, action = (
                "CANDIDATE_PROPOSAL_GOVERNANCE",
                CANDIDATE_PROPOSAL_READY,
                HUMAN_REVIEW_CANDIDATE_PROPOSAL,
            )
        elif design_exists:
            if requires_confirmation and not design_approved:
                stage, state, action = (
                    "AI_DESIGN_APPROVAL",
                    AI_DESIGN_AWAITING_CONFIRMATION,
                    HUMAN_CONFIRM_AI_RESEARCH_DESIGN,
                )
            else:
                stage, state, action = (
                    "AI_RESEARCH_DESIGN",
                    AI_DESIGN_READY,
                    "GENERATE_CANDIDATE_PROPOSAL",
                )
        else:
            stage, state, action = (
                "AI_RESEARCH_DESIGN",
                NEED_AI_RESEARCH_DESIGN,
                NEED_AI_RESEARCH_DESIGN,
            )

        supported = action not in {
            HUMAN_CONFIRM_AI_RESEARCH_DESIGN,
            HUMAN_REVIEW_CANDIDATE_PROPOSAL,
            "RESOLVE_BUDGET_AUTHORITY_AMBIGUITY",
            "STOP_AND_RECONCILE_CANONICAL_CONFLICT",
        }
        safe_to_resume = CANONICAL_CONFLICT not in ctx.conflicts and not budget_ambiguous
        safe_to_advance = (
            safe_to_resume
            and state in {AI_DESIGN_READY, READY_FOR_STRUCTURAL_PREFLIGHT}
            and action not in {None, STOP_RESEARCH}
        )
        if state in {
            AI_DESIGN_AWAITING_CONFIRMATION,
            CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION,
            BUDGET_EXHAUSTED,
            TRIAL_TERMINAL,
            TRIAL_ACTIVE,
            CANONICAL_STATE_CONFLICT,
            BUDGET_AUTHORITY_AMBIGUOUS,
            NEED_AI_RESEARCH_DESIGN,
            CANDIDATE_PROPOSAL_READY,
        }:
            safe_to_advance = False

        canonical_refs = {
            "objective": next(
                (
                    item["path"]
                    for item in ctx.evidence.get("objective_definition", ())
                ),
                None,
            ),
            "ai_design": [item["path"] for item in ctx.evidence.get("ai_design", ())],
            "candidate_governance": [
                item["path"] for item in ctx.evidence.get("candidate_governance", ())
            ],
            "durable_frozen_contract": [
                item["path"]
                for item in ctx.evidence.get("durable_frozen_contract", ())
            ],
            "trial_ledger": [
                item["path"] for item in ctx.evidence.get("trial_ledger", ())
            ],
            "budget_registry": (
                canonical_budget.get("registry_head_hash")
                if isinstance(canonical_budget, Mapping)
                else None
            ),
            "budget_registry_path": self._canonical_budget_path(budget_view),
            "artifact_graph": [
                item["path"] for item in ctx.evidence.get("artifact_graph", ())
            ],
        }
        projection_refs = {
            "daemon": [item["path"] for item in ctx.evidence.get("daemon_projection", ())],
            "orchestrator": [
                item["path"]
                for item in ctx.evidence.get("orchestrator_projection", ())
            ],
        }
        return EffectiveObjectiveStateV1(
            objective_id=objective_id,
            objective_dialect=str(dialect.get("dialect")),
            effective_stage=stage,
            effective_state=state,
            required_action=action,
            required_action_supported=supported,
            safe_to_resume=safe_to_resume,
            safe_to_advance=safe_to_advance,
            current_candidate_id=str(candidate_id) if candidate_id else None,
            current_candidate_hash=str(candidate_hash) if candidate_hash else None,
            current_trial_id=str(current_trial) if current_trial else None,
            budget=dict(budget_view),
            canonical_refs=canonical_refs,
            projection_refs=projection_refs,
            conflicts=tuple(sorted(ctx.conflicts)),
            warnings=tuple(sorted(ctx.warnings)),
            structural_preflight_ready=bool(candidate_view.get("structural_preflight_ready")),
        )

    @staticmethod
    def _is_ai_approval(payload: Any) -> bool:
        if not isinstance(payload, Mapping):
            return False
        status = str(
            payload.get("status")
            or payload.get("approval_status")
            or payload.get("decision")
            or ""
        ).upper()
        return (
            status in {"APPROVED", "AI_DESIGN_APPROVED", "CONFIRMED", "ACCEPTED"}
            or _bool(payload.get("approved"))
            or _bool(payload.get("confirmed"))
        )

    @staticmethod
    def _canonical_structural_status(ctx: _ReconciliationContext) -> str | None:
        statuses: set[str] = set()
        for source in ctx.sources.get("structural_preflight", ()):
            for item in _walk_mappings(source.get("payload")):
                for key in ("status", "state", "result"):
                    if item.get(key) not in (None, ""):
                        statuses.add(str(item[key]).upper())
        if "PASS" in statuses or "PASSED" in statuses:
            return "PASS"
        return sorted(statuses)[0] if statuses else None

    @staticmethod
    def _canonical_predictive_authorized(ctx: _ReconciliationContext) -> bool:
        return any(
            ObjectiveReconciliationServiceV1._is_ai_approval(source.get("payload"))
            or _bool(_first_value(source.get("payload"), {"authorized", "authorization_granted"}))
            for source in ctx.sources.get("predictive_authorization", ())
        )

    @staticmethod
    def _canonical_budget_path(budget_view: Mapping[str, Any]) -> str | None:
        sources = budget_view.get("sources")
        if budget_view.get("authority_status") == "UNIQUE_CANONICAL" and isinstance(sources, list):
            for item in sources:
                if item.get("source_classification") == "CURRENT_CANONICAL":
                    return item.get("path")
            return sources[0].get("path") if sources else None
        return None

    def _build_index(self, reports: list[Mapping[str, Any]]) -> dict[str, Any]:
        entries = [
            {
                "objective_id": report["objective_id"],
                "objective_dialect": report["objective_dialect"]["dialect"],
                "effective_stage": report["effective_stage"],
                "effective_state": report["effective_state"],
                "conflict_level": report["conflict_level"],
                "conflicts": list(report["conflicts"]),
                "safe_to_resume": report["safe_to_resume"],
                "safe_to_advance": report["safe_to_advance"],
                "report_json": (
                    "reports/research_reconciliation/"
                    f"{report['objective_id']}/OBJECTIVE_RECONCILIATION_V1.json"
                ),
                "report_markdown": (
                    "reports/research_reconciliation/"
                    f"{report['objective_id']}/OBJECTIVE_RECONCILIATION_V1.md"
                ),
            }
            for report in sorted(reports, key=lambda item: str(item["objective_id"]))
        ]
        summary: dict[str, int] = {}
        for item in entries:
            summary[item["effective_state"]] = summary.get(item["effective_state"], 0) + 1
        return {
            "schema_version": GLOBAL_INDEX_SCHEMA_VERSION,
            "report_kind": "CURRENT_OBJECTIVE_RECONCILIATION_INDEX_V1",
            "read_only": True,
            "objective_count": len(entries),
            "state_counts": dict(sorted(summary.items())),
            "objectives": entries,
            "index_hash": stable_hash(entries),
        }

    @staticmethod
    def _render_markdown(report: Mapping[str, Any]) -> str:
        dialect = report["objective_dialect"]
        effective = report["effective_objective_state"]
        budget = report.get("budget", {})
        candidate = report.get("candidate_reconciliation", {})
        trial = report.get("trial_reconciliation", {})
        conflicts = report.get("conflicts", [])
        warnings = report.get("warnings", [])
        term = ZhCNPresentation.term
        canonical_budget = budget.get("canonical")
        lines = [
            "# Objective Reconciliation V1",
            "",
            f"- Objective ID：{report['objective_id']}",
            f"- Objective 方言：{term(str(dialect.get('dialect')))}（{dialect.get('dialect')}）",
            f"- 有效阶段：{term(str(effective.get('effective_stage')))}（{effective.get('effective_stage')}）",
            f"- 有效状态：{term(str(effective.get('effective_state')))}（{effective.get('effective_state')}）",
            f"- 是否可安全推进：{effective.get('safe_to_advance')}",
            f"- 是否可安全恢复：{effective.get('safe_to_resume')}",
            "",
            "## 结论",
            "",
            "本报告只读重建 Objective 的事实、治理证据、执行契约、TrialLedger、预算和运行投影；没有修复或推进任何 canonical artifact。",
            "",
            "## Canonical Authority",
            "",
            "Objective JSON 负责定义与创建事实；AI 设计提案、候选治理冻结、DurableFrozenCandidateContractV1、factory_trial_ledger.json 和 SearchBudgetRegistryV1 分别承担后续事实源。Daemon/Orchestrator 仅作为运行投影。",
            "",
            "## AI Design 与 Candidate",
            "",
            f"- AI Design：{len(report.get('evidence_inventory', {}).get('ai_design', []))} 个证据源。",
            f"- AI Design 批准证据：{report.get('ai_design_reconciliation', {}).get('approval_status')}。",
            f"- Candidate 治理冻结：{candidate.get('governance_freeze_present')}；Durable executable freeze：{candidate.get('executable_frozen_candidate')}。",
            f"- Candidate 结构预检入口准备度：{candidate.get('structural_preflight_ready')}。",
            "",
            "## Budget 与 Trial 对账",
            "",
            f"- Budget authority：{budget.get('authority_status')}；来源数：{budget.get('source_count')}。",
            f"- Canonical Budget：{json.dumps(canonical_budget, ensure_ascii=False, sort_keys=True) if canonical_budget else '未唯一确定'}",
            f"- Trial 数量：{trial.get('trial_count')}；已访问 performance 的 Trial：{trial.get('performance_accessed_trial_count')}；终态 Trial：{trial.get('terminal_trial_count')}。",
            "",
            "## Projection Drift 与冲突",
            "",
            f"- 冲突等级：{report.get('conflict_level')}",
            f"- 冲突代码：{', '.join(conflicts) if conflicts else '无'}",
            f"- 警告代码：{', '.join(warnings) if warnings else '无'}",
            "",
            "## Artifact 分类",
            "",
            "本报告只分类 CURRENT_CANONICAL、HISTORICAL_CANONICAL、LEGACY、ORPHAN、UNRESOLVED，不删除或清理任何对象。",
            "",
            "## 下一步",
            "",
            f"当前要求动作：{effective.get('required_action') or '无'}；动作支持度：{effective.get('required_action_supported')}",
        ]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_index_markdown(index: Mapping[str, Any]) -> str:
        lines = [
            "# Current Objective Reconciliation Index V1",
            "",
            f"本索引只读汇总 {index.get('objective_count', 0)} 个 Objective；不会把运行投影提升为 canonical authority。",
            "",
            "| Objective | 方言 | 有效状态 | 冲突等级 | 可安全推进 |",
            "|---|---|---|---|---|",
        ]
        for item in index.get("objectives", ()):
            lines.append(
                f"| {item['objective_id']} | {item['objective_dialect']} | "
                f"{item['effective_state']} | {item['conflict_level']} | "
                f"{item['safe_to_advance']} |"
            )
        lines.extend(("", "报告目录：reports/research_reconciliation/<objective_id>/。"))
        return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Research Factory objective reconciliation"
    )
    parser.add_argument("--root", default=".", help="repository root")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--objective-id")
    group.add_argument("--all", action="store_true", dest="all_objectives")
    args = parser.parse_args(argv)
    service = ObjectiveReconciliationServiceV1(args.root)
    if args.all_objectives:
        index = service.write_all_reports()
        print(json.dumps({
            "objective_count": index["objective_count"],
            "index": "reports/research_reconciliation/CURRENT_OBJECTIVE_RECONCILIATION_INDEX_V1.json",
        }, ensure_ascii=False))
    else:
        report = service.reconcile(str(args.objective_id))
        machine, human = service.write_report(report)
        print(json.dumps({
            "objective_id": args.objective_id,
            "effective_state": report["effective_state"],
            "conflict_level": report["conflict_level"],
            "json": _relative_path(service.root, machine),
            "markdown": _relative_path(service.root, human),
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


ObjectiveReconciliationService = ObjectiveReconciliationServiceV1


__all__ = [
    "AI_DESIGN_AWAITING_CONFIRMATION",
    "AI_DESIGN_READY",
    "BUDGET_AUTHORITY_AMBIGUOUS",
    "BUDGET_EXHAUSTED",
    "CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION",
    "CANONICAL_CONFLICT",
    "CONSISTENT",
    "EffectiveObjectiveStateV1",
    "ObjectiveDialectClassifierV1",
    "ObjectiveReconciliationService",
    "ObjectiveReconciliationServiceV1",
    "OBJECTIVE_DIALECT_DEFINITION_ONLY",
    "OBJECTIVE_DIALECT_EVOLUTION_MANUAL_CREATED",
    "OBJECTIVE_DIALECT_GOVERNANCE_EXECUTION_READY",
    "OBJECTIVE_DIALECT_LEGACY_UNKNOWN",
    "PROJECTION_DRIFT",
    "READY_FOR_STRUCTURAL_PREFLIGHT",
    "RECONCILIATION_SCHEMA_VERSION",
    "REPAIRABLE_INDEX_DRIFT",
    "main",
]
