"""Safe, deterministic and outcome-blind runtime context for research tools.

``SafeRuntimeContextV1`` is a read model.  It is deliberately built behind
one entry point so that AI design, manual handoff and later research tools all
receive the same reconciled facts.  The builder never starts research,
accesses performance data, reserves budget or writes a canonical artifact.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import argparse
import json
from pathlib import Path
import re
from typing import Any

from .common import canonical_json, now_timestamp, stable_hash
from .context import PerformanceBlindGuard, PerformanceLeakError
from .objective_reconciliation import CANONICAL_CONFLICT, ObjectiveReconciliationServiceV1


SAFE_RUNTIME_CONTEXT_SCHEMA_VERSION = "safe-runtime-context-v1"
SAFE_RUNTIME_CONTEXT_VERSION = "SAFE_RUNTIME_CONTEXT_V1"
CONTEXT_BUILD_BLOCKED = "CONTEXT_BUILD_BLOCKED"
STALE_RUNTIME_CONTEXT = "STALE_RUNTIME_CONTEXT"
BUDGET_AUTHORITY_AMBIGUOUS = "BUDGET_AUTHORITY_AMBIGUOUS"
OUTCOME_FIELD_BLOCKED = "OUTCOME_FIELD_BLOCKED"
DEFAULT_FRESHNESS_TTL_SECONDS = 3600

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$")
_FAILURE_TAXONOMY = {
    "STATISTICAL_FAILURE",
    "RISK_FAILURE",
    "DATA_FAILURE",
    "ENGINEERING_FAILURE",
    "SAMPLE_FAILURE",
    "PIT_FAILURE",
    "ROBUSTNESS_FAILURE",
    "OVERFITTING_RISK",
    "GOVERNANCE_FAILURE",
    "UNKNOWN_FAILURE",
}
_SAFE_RISK_KEYS = (
    "pit_required",
    "no_lookahead",
    "t_plus_1",
    "price_limit_fail_closed",
    "suspension_fail_closed",
    "final_test_access",
    "prospective_access",
    "real_order_execution",
)
_DATASET_FIELDS = (
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
_FACTOR_REGISTRY_PATHS = (
    "data/research/factor_registry/registry.json",
    "data/research/unified_factor_registry/registry.json",
    "data/research/unified_factor_registry/factor_specs.json",
    "data/research/factor_library_v1/registry.json",
)
_EVENT_REGISTRY_PATHS = (
    "data/research/event_registry/registry_v2.json",
    "data/research/event_registry/registry.json",
)


def _stable(value: Any) -> Any:
    """Return recursively canonical data with stable list ordering."""

    if isinstance(value, Mapping):
        return {str(key): _stable(value[key]) for key in sorted(value, key=lambda item: str(item))}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_stable(item) for item in value]
        return sorted(items, key=canonical_json)
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


def _first(mapping: Mapping[str, Any] | None, *keys: str, default: Any = None) -> Any:
    source = mapping if isinstance(mapping, Mapping) else {}
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return default


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _safe_relative(root: Path, relative: str | Path) -> Path | None:
    raw = str(relative).replace("\\", "/")
    candidate = Path(raw)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return None
    path = (root / candidate).resolve()
    return path if path.is_relative_to(root) else None


def _read_json(root: Path, relative: str | Path, *, required: bool = False) -> tuple[dict[str, Any] | None, str | None, str | None]:
    path = _safe_relative(root, relative)
    if path is None or not path.is_file():
        if required:
            raise SafeRuntimeContextError(CONTEXT_BUILD_BLOCKED, "安全运行时上下文所需资料不存在。", status_code=404, details={"path": str(relative)})
        return None, None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SafeRuntimeContextError(CONTEXT_BUILD_BLOCKED, "安全运行时上下文资料暂时不可读。", status_code=503, details={"path": path.relative_to(root).as_posix()}) from exc
    if not isinstance(payload, Mapping):
        raise SafeRuntimeContextError(CONTEXT_BUILD_BLOCKED, "安全运行时上下文资料格式无效。", status_code=503, details={"path": path.relative_to(root).as_posix()})
    data = dict(payload)
    return data, path.relative_to(root).as_posix(), stable_hash(data)


def _path_from_evidence(root: Path, report: Mapping[str, Any], categories: Sequence[str], names: Sequence[str] = ()) -> list[str]:
    result: list[str] = []
    evidence = report.get("evidence_inventory") if isinstance(report.get("evidence_inventory"), Mapping) else {}
    for category in categories:
        entries = evidence.get(category)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping) or not entry.get("path"):
                continue
            path = str(entry["path"])
            if not names or Path(path).name in names:
                if _safe_relative(root, path) is not None:
                    result.append(path)
    return sorted(set(result))


def _taxonomy(value: Any) -> str:
    token = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if token in _FAILURE_TAXONOMY:
        return token
    if any(part in token for part in ("RETURN", "ALPHA", "P_VALUE", "PVAL", "SHARPE", "PERFORMANCE", "STAT")):
        return "STATISTICAL_FAILURE"
    if any(part in token for part in ("RISK", "DRAWDOWN", "VOLATILITY")):
        return "RISK_FAILURE"
    if any(part in token for part in ("PIT", "LOOKAHEAD", "LEAK", "TIMING")):
        return "PIT_FAILURE"
    if any(part in token for part in ("SAMPLE", "COVERAGE", "WARMUP", "INSUFFICIENT")):
        return "SAMPLE_FAILURE"
    if any(part in token for part in ("DATA", "MISSING", "AVAILABILITY")):
        return "DATA_FAILURE"
    if any(part in token for part in ("ENGINEERING", "EXECUTION", "PROVIDER", "INTEGRITY")):
        return "ENGINEERING_FAILURE"
    if any(part in token for part in ("OVERFIT", "MULTIPLE_TEST")):
        return "OVERFITTING_RISK"
    if any(part in token for part in ("GOVERNANCE", "APPROVAL", "AUTHORITY")):
        return "GOVERNANCE_FAILURE"
    return "UNKNOWN_FAILURE"


def _safe_risk(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    return {key: source[key] for key in _SAFE_RISK_KEYS if key in source}


def _safe_dataset(payload: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in _DATASET_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        if key == "fields":
            result[key] = _strings(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
    return result


def _safe_lineage(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        key: payload[key]
        for key in (
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
        if key in payload
    }
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


def _safe_proposal(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        key: payload[key]
        for key in (
            "proposal_id",
            "proposal_hash",
            "parent_objective_id",
            "created_objective_id",
            "failed_mechanism",
            "failed_mechanism_family",
            "avoid_mechanism_family",
            "avoid_mechanisms",
            "suggested_research_directions",
            "outcome_blind",
            "objective_created",
        )
        if key in payload
    }
    raw_categories = []
    raw_categories.extend(_strings(payload.get("failure_summary")))
    raw_categories.extend(_strings(payload.get("failure_categories")))
    result["failure_taxonomy"] = sorted({_taxonomy(item) for item in raw_categories})
    for key in ("avoid_mechanism_family", "avoid_mechanisms", "suggested_research_directions"):
        result[key] = _strings(result.get(key))
    return result


def _safe_coverage(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = payload if isinstance(payload, Mapping) else {}
    result = {
        "schema_version": str(source.get("schema_version") or "mechanism-coverage-registry-v1"),
        "coverage_id": str(source.get("coverage_id") or "MECHANISM_COVERAGE_REGISTRY_V1"),
        "covered": _strings(source.get("covered")),
        "unexplored": _strings(source.get("unexplored")),
        "forbidden_mechanisms": _strings(source.get("forbidden_mechanisms") or source.get("excluded_mechanisms")),
        "read_only": True,
        "outcome_blind": True,
    }
    return result


def _safe_design(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = payload if isinstance(payload, Mapping) else {}
    result = {
        key: source[key]
        for key in (
            "design_id",
            "design_hash",
            "status",
            "objective_id",
            "parent_proposal_id",
            "parent_proposal_hash",
            "input_context_hash",
            "source_context_id",
            "source_context_hash",
            "research_hypothesis",
            "mechanism_family",
            "candidate_design_intention",
            "allowed_factors",
            "excluded_mechanisms",
            "validation_expectation",
        )
        if key in source
    }
    for key in ("allowed_factors", "excluded_mechanisms", "validation_expectation"):
        result[key] = _strings(result.get(key))
    return result


def _safe_landscape(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = payload if isinstance(payload, Mapping) else {}
    entries: list[dict[str, Any]] = []
    for item in source.get("entries") or ():
        if not isinstance(item, Mapping):
            continue
        safe = {
            key: item[key]
            for key in ("candidate_id", "candidate_family", "mechanism", "trial_id")
            if key in item
        }
        safe["failure_categories"] = sorted({_taxonomy(value) for value in _strings(item.get("failure_categories"))})
        entries.append(safe)
    category_totals: dict[str, int] = {}
    raw_totals = source.get("category_totals")
    if isinstance(raw_totals, Mapping):
        for key, value in raw_totals.items():
            if _taxonomy(key) == str(key).upper() and isinstance(value, (int, float)):
                category_totals[str(key).upper()] = int(value)
    return {
        "schema_version": str(source.get("schema_version") or "research-failure-landscape-v1"),
        "objective_id": str(source.get("objective_id") or ""),
        "entries": entries,
        "category_totals": category_totals,
        "read_only": True,
    }


def _safe_contract(raw: Mapping[str, Any], *, structural_ready: bool = False) -> dict[str, Any]:
    policy = raw.get("policy_identity") if isinstance(raw.get("policy_identity"), Mapping) else {}
    execution = raw.get("execution_contract") if isinstance(raw.get("execution_contract"), Mapping) else raw.get("execution")
    if not isinstance(execution, Mapping):
        execution = {
            key: raw[key]
            for key in (
                "entry",
                "entry_predicate",
                "signal_time",
                "holding_period",
                "holding_period_trading_sessions",
                "holding_period_unit",
                "same_session_sell_forbidden",
                "entry_timing",
                "exit_contract",
                "execution_contract_version",
                "t_plus_1_contract",
                "pit_dependencies",
                "selection_rule",
                "ranking_semantics",
                "interaction_semantics",
                "top_n",
                "max_positions",
            )
            if key in raw
        }
        if "risk_constraints" in raw:
            execution["risk_constraints"] = raw["risk_constraints"]
    risk = _safe_risk(execution.get("risk_constraints"))
    execution_safe = {
        key: execution[key]
        for key in (
            "entry",
            "entry_predicate",
            "signal_time",
            "holding_period",
            "holding_period_trading_sessions",
            "holding_period_unit",
            "same_session_sell_forbidden",
            "entry_timing",
            "exit_contract",
            "execution_contract_version",
            "t_plus_1_contract",
            "pit_dependencies",
            "selection_rule",
            "ranking_semantics",
            "interaction_semantics",
            "top_n",
            "max_positions",
        )
        if key in execution
    }
    execution_safe["risk_constraints"] = risk
    data = raw.get("data_contract") if isinstance(raw.get("data_contract"), Mapping) else {}
    data_safe = {
        key: data[key]
        for key in ("required_dataset_ids", "available_dataset_ids", "ready_dataset_ids", "missing_required_dataset_ids", "required_data_semantics", "PIT_safe_required", "available_at_semantics")
        if key in data
    }
    factor_ids = _strings(raw.get("factor_ids") or raw.get("factors") or (raw.get("factor_contract") if isinstance(raw.get("factor_contract"), list) else ()))
    event_ids = _strings(raw.get("event_ids") or raw.get("events"))
    return {
        "candidate_id": str(raw.get("candidate_id") or ""),
        "candidate_hash": str(raw.get("candidate_hash") or raw.get("candidate_preregistration_hash") or ""),
        "objective_id": str(policy.get("objective_id") or raw.get("objective_id") or ""),
        "mechanism": str(raw.get("mechanism") or raw.get("family") or raw.get("mechanism_family") or ""),
        "factor_ids": factor_ids,
        "event_ids": event_ids,
        "execution_contract": execution_safe,
        "data_contract": data_safe,
        "semantic_fingerprint": _first(raw, "semantic_fingerprint", "semantic_hash"),
        "parameter_fingerprint": _first(raw, "parameter_fingerprint"),
        "durable_contract_hash": str(raw.get("content_hash") or raw.get("contract_hash") or raw.get("durable_contract_hash") or ""),
        "structural_readiness": "READY_FOR_STRUCTURAL_PREFLIGHT" if structural_ready else "NOT_READY",
    }


def _contract_payloads(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping) and isinstance(payload.get("contracts"), list):
        return [item for item in payload["contracts"] if isinstance(item, Mapping)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping) and item.get("candidate_id")]
    if isinstance(payload, Mapping) and payload.get("candidate_id"):
        return [payload]
    return []


def _safe_trial(raw: Mapping[str, Any]) -> dict[str, Any]:
    status = str(raw.get("terminal_status") or raw.get("status") or raw.get("state") or "")
    return {
        "trial_id": str(raw.get("trial_id") or ""),
        "status": status,
        "terminal": bool(raw.get("terminal_status") or status in {"COMPLETED", "BLOCKED", "INVALIDATED", "SUPERSEDED", "CANCELLED"}),
        "candidate_ids": _strings(raw.get("candidate_ids") or raw.get("candidate_id")),
        "candidate_hashes": _strings(raw.get("candidate_hashes") or raw.get("candidate_hash")),
        "batch_ids": _strings(raw.get("batch_ids") or raw.get("batch_id")),
        "event_types": _strings(raw.get("event_types") or raw.get("event_type")),
        "event_count": _number(raw.get("event_count")) or 0,
        "performance_accessed": bool(raw.get("performance_accessed")),
        "budget_reservation_identities": _strings(raw.get("budget_reservation_identities") or raw.get("budget_reservation_identity")),
        "lifecycle_complete": bool(raw.get("terminal_status") or status in {"COMPLETED", "BLOCKED", "INVALIDATED", "SUPERSEDED", "CANCELLED"}),
    }


def _safe_budget(view: Mapping[str, Any], objective_id: str) -> dict[str, Any]:
    canonical = view.get("canonical") if isinstance(view.get("canonical"), Mapping) else {}
    sources = view.get("sources") if isinstance(view.get("sources"), list) else []
    selected = next((item for item in sources if isinstance(item, Mapping) and item.get("source_classification") == "CURRENT_CANONICAL"), None)
    if selected is None and len(sources) == 1 and isinstance(sources[0], Mapping):
        selected = sources[0]
    source_path = str((selected or {}).get("path") or "")
    source_hash = str((selected or {}).get("source_hash") or "")
    buckets: list[dict[str, Any]] = []
    for item in canonical.get("buckets", ()) if isinstance(canonical, Mapping) else ():
        if not isinstance(item, Mapping):
            continue
        buckets.append({
            key: item[key]
            for key in ("kind", "key", "limit", "used", "reserved", "remaining")
            if key in item
        })
    active = []
    for item in canonical.get("active_reservations", ()) if isinstance(canonical, Mapping) else ():
        if isinstance(item, Mapping):
            active.append({key: item[key] for key in ("reservation_id", "status", "objective_id", "testing_family") if key in item})
        elif item not in (None, ""):
            active.append({"reservation_id": str(item), "status": "ACTIVE"})
    return {
        "authority_status": str(view.get("authority_status") or "MISSING"),
        "budget_identity": {
            "objective_id": objective_id,
            "registry_path": source_path or None,
            "registry_source_hash": source_hash or None,
            "registry_head_hash": canonical.get("registry_head_hash") if isinstance(canonical, Mapping) else None,
        },
        "total": _number(canonical.get("total")) if isinstance(canonical, Mapping) else None,
        "used": _number(canonical.get("used")) if isinstance(canonical, Mapping) else None,
        "reserved": _number(canonical.get("reserved")) if isinstance(canonical, Mapping) else None,
        "remaining": _number(canonical.get("remaining")) if isinstance(canonical, Mapping) else None,
        "testing_family": None,
        "buckets": buckets,
        "active_reservations": active,
        "settled_reservation_ids": sorted(str(key) for key in (canonical.get("settled_reservations") or {}) if isinstance(canonical, Mapping)),
        "read_only": True,
    }


def _safe_structural(view: Mapping[str, Any] | None, objective_id: str) -> dict[str, Any]:
    source = view if isinstance(view, Mapping) else {}
    canonical = source.get("canonical") if isinstance(source.get("canonical"), Mapping) else {}
    identity = canonical.get("identity") if isinstance(canonical.get("identity"), Mapping) else {}
    raw_payload = canonical.get("payload") if isinstance(canonical.get("payload"), Mapping) else {}
    nested = raw_payload.get("canonical_result") if isinstance(raw_payload.get("canonical_result"), Mapping) else raw_payload
    lower = nested.get("v1_result") if isinstance(nested.get("v1_result"), Mapping) else nested.get("sample_feasibility")
    lower = lower if isinstance(lower, Mapping) else {}
    integrity = nested.get("lower_bound_integrity") if isinstance(nested.get("lower_bound_integrity"), Mapping) else {}
    provider = {
        key: identity[key]
        for key in ("provider_payload_identity", "data_identity", "manifest_identity", "policy_identity", "structural_contract_hash")
        if key in identity
    }
    return {
        "present": bool(source.get("present")),
        "status": str(source.get("status") or "NOT_AVAILABLE"),
        "objective_id": str(identity.get("objective_id") or objective_id),
        "candidate_id": identity.get("candidate_id"),
        "candidate_hash": identity.get("candidate_hash"),
        "result_hash": identity.get("result_hash") or canonical.get("source_hash"),
        "provider_identity": provider,
        "sample_feasibility": {
            key: _number(lower.get(key))
            for key in ("lower_bound_count", "upper_bound_count", "minimum_required_count")
            if lower.get(key) is not None
        },
        "integrity": {
            "status": str(integrity.get("status") or "UNKNOWN"),
            "failure_taxonomy": sorted({_taxonomy(item) for item in _strings(integrity.get("failure_codes"))}),
        },
        "read_only": True,
    }


def _safe_timestamp(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clock_timestamp(clock: Callable[[], Any]) -> str:
    value = clock()
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    return str(value)


class SafeRuntimeContextError(RuntimeError):
    """Fail-closed error for context construction or freshness validation."""

    def __init__(self, code: str, message_zh: str, *, status_code: int = 409, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message_zh)
        self.code = str(code)
        self.message_zh = str(message_zh)
        self.status_code = int(status_code)
        self.details = dict(details or {})

    def envelope(self) -> dict[str, Any]:
        return {"code": self.code, "message_zh": self.message_zh, "details": dict(self.details)}


@dataclass(frozen=True)
class SafeRuntimeContextV1:
    """Immutable wrapper around the safe runtime context read model."""

    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        normalized = _stable(self.payload)
        try:
            PerformanceBlindGuard.assert_blind(
                normalized,
                allowed_paths=frozenset({
                    ("trial", "trials", "performance_accessed"),
                    ("candidate", "candidates", "performance_accessed"),
                }),
            )
        except PerformanceLeakError as exc:
            raise SafeRuntimeContextError(OUTCOME_FIELD_BLOCKED, "安全运行时上下文包含被禁止的结果字段。", status_code=503) from exc
        object.__setattr__(self, "payload", normalized)

    @property
    def identity(self) -> Mapping[str, Any]:
        return self.payload.get("identity", {})

    @property
    def context_id(self) -> str:
        return str(self.identity.get("context_id") or "")

    @property
    def context_hash(self) -> str:
        return str(self.identity.get("context_hash") or "")

    @property
    def objective_id(self) -> str:
        return str(self.payload.get("objective", {}).get("objective_id") or self.identity.get("objective_id") or "")

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.payload[key]

    def to_dict(self) -> dict[str, Any]:
        return _stable(self.payload)

    def to_ai_design_input(self) -> dict[str, Any]:
        """Adapt the single context to the legacy AI design input shape."""

        objective = self.payload.get("objective", {})
        governance = self.payload.get("governance", {})
        failure = self.payload.get("failure_landscape", {})
        data = self.payload.get("data_capabilities", [])
        datasets = [
            item.get("dataset")
            for item in data
            if isinstance(item, Mapping) and isinstance(item.get("dataset"), Mapping)
        ]
        safe_data = {
            "schema_version": "data-capability-safe-view-v1",
            "generated_at": self.payload.get("freshness", {}).get("generated_at"),
            "datasets": datasets,
            "ready_dataset_ids": sorted(str(item.get("dataset_id")) for item in datasets if item.get("status") == "READY"),
            "online_only_dataset_ids": sorted(str(item.get("dataset_id")) for item in datasets if item.get("status") == "ONLINE_ONLY"),
        }
        proposal = dict(governance.get("proposal", {})) if isinstance(governance.get("proposal"), Mapping) else {}
        landscape = dict(failure) if isinstance(failure, Mapping) else {}
        coverage = dict(governance.get("mechanism_coverage", {})) if isinstance(governance.get("mechanism_coverage"), Mapping) else {}
        lineage = dict(governance.get("objective_lineage", {})) if isinstance(governance.get("objective_lineage"), Mapping) else {}
        excluded = _strings([
            *proposal.get("avoid_mechanism_family", []),
            *proposal.get("avoid_mechanisms", []),
            *coverage.get("covered", []),
            *self.payload.get("mechanism_constraints", {}).get("excluded_mechanisms", []),
        ])
        failed = _strings([
            proposal.get("failed_mechanism"),
            proposal.get("failed_mechanism_family"),
            *[item.get("mechanism") for item in landscape.get("entries", []) if isinstance(item, Mapping)],
            *[item.get("candidate_family") for item in landscape.get("entries", []) if isinstance(item, Mapping)],
        ])
        directions = _strings([*proposal.get("suggested_research_directions", []), *coverage.get("unexplored", [])])
        allowed_factors = _strings(objective.get("allowed_factor_scope"))
        category_constraints = {
            item: f"仅使用 {item} 抽象失败分类；不携带原始结果值。"
            for item in self.payload.get("failure_taxonomy", [])
        }
        constraints = dict(objective.get("risk_constraints", {})) if isinstance(objective.get("risk_constraints"), Mapping) else {}
        constraints["failure_category_constraints"] = category_constraints
        source_refs = {
            key: value.get("path")
            for key, value in self.payload.get("source_bindings", {}).items()
            if isinstance(value, Mapping) and value.get("path")
        }
        source_hashes = {
            key: value.get("source_hash")
            for key, value in self.payload.get("source_bindings", {}).items()
            if isinstance(value, Mapping) and value.get("source_hash")
        }
        return {
            "schema_version": "research-evolution-ai-design-input-v1",
            "objective_id": self.objective_id,
            "research_evolution_proposal": proposal,
            "failure_landscape": landscape,
            "mechanism_coverage_registry": coverage,
            "objective_lineage": lineage,
            "failed_mechanisms": failed,
            "excluded_mechanisms": excluded,
            "suggested_research_directions": directions,
            "allowed_factors": allowed_factors,
            "available_data_capabilities": safe_data,
            "constraints": constraints,
            "source_refs": source_refs,
            "source_hashes": source_hashes,
            "source_context_id": self.context_id,
            "source_context_hash": self.context_hash,
            "input_context_hash": self.context_hash,
            "outcome_blind": True,
            "performance_data_loaded": False,
            "outcome_fields_available": False,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SafeRuntimeContextV1":
        if not isinstance(payload, Mapping):
            raise SafeRuntimeContextError(CONTEXT_BUILD_BLOCKED, "安全运行时上下文格式无效。", status_code=503)
        return cls(payload=dict(payload))


class SafeRuntimeContextBuilderV1:
    """Build and validate one objective-scoped safe context."""

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], Any] = now_timestamp,
        freshness_ttl_seconds: int = DEFAULT_FRESHNESS_TTL_SECONDS,
        reconciliation: ObjectiveReconciliationServiceV1 | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.clock = clock
        self.freshness_ttl_seconds = int(freshness_ttl_seconds)
        if self.freshness_ttl_seconds <= 0:
            raise ValueError("freshness_ttl_seconds must be positive")
        self.reconciliation = reconciliation or ObjectiveReconciliationServiceV1(self.root)

    def _validate_objective_id(self, objective_id: str) -> str:
        value = str(objective_id or "")
        if not _IDENTIFIER_RE.fullmatch(value) or "/" in value or "\\" in value:
            raise SafeRuntimeContextError("INVALID_IDENTIFIER", "Objective 标识不合法。", status_code=400)
        return value

    def _reconcile(self, objective_id: str) -> Mapping[str, Any]:
        try:
            report = self.reconciliation.reconcile(objective_id)
        except SafeRuntimeContextError:
            raise
        except Exception as exc:
            raise SafeRuntimeContextError(CONTEXT_BUILD_BLOCKED, "Objective 对账资料暂时不可用，安全上下文构建已阻断。", status_code=503, details={"reason": type(exc).__name__}) from exc
        if not isinstance(report, Mapping):
            raise SafeRuntimeContextError(CONTEXT_BUILD_BLOCKED, "Objective 对账结果格式无效，安全上下文构建已阻断。", status_code=503)
        conflicts = {str(item) for item in report.get("conflicts", ())}
        conflict_level = str(report.get("conflict_level") or "")
        budget = report.get("budget") if isinstance(report.get("budget"), Mapping) else {}
        if str(budget.get("authority_status") or "") == "AMBIGUOUS":
            raise SafeRuntimeContextError(
                BUDGET_AUTHORITY_AMBIGUOUS,
                "预算权威来源不唯一，安全运行时上下文拒绝构建。",
                status_code=409,
                details={"source_count": budget.get("source_count", 0)},
            )
        if conflict_level == CANONICAL_CONFLICT or CANONICAL_CONFLICT in conflicts:
            raise SafeRuntimeContextError(
                CONTEXT_BUILD_BLOCKED,
                "Canonical 事实存在冲突，安全运行时上下文拒绝构建。",
                status_code=409,
                details={"conflicts": sorted(conflicts), "conflict_level": conflict_level},
            )
        effective = report.get("effective_objective_state") if isinstance(report.get("effective_objective_state"), Mapping) else {}
        if effective.get("safe_to_resume") is False and CANONICAL_CONFLICT in {str(item) for item in effective.get("conflicts", ())}:
            raise SafeRuntimeContextError(CONTEXT_BUILD_BLOCKED, "当前 Objective 无法安全恢复，安全上下文拒绝构建。", status_code=409)
        return report

    def _load_objective(self, objective_id: str) -> tuple[dict[str, Any], str, str]:
        payload, path, source_hash = _read_json(self.root, f"data/research/research_factory/objectives/{objective_id}.json", required=True)
        if payload is None or str(payload.get("objective_id") or "") != objective_id:
            raise SafeRuntimeContextError(CONTEXT_BUILD_BLOCKED, "Objective 身份与请求不一致。", status_code=503)
        return payload, str(path), str(source_hash)

    def _find_payload(
        self,
        report: Mapping[str, Any],
        objective_id: str,
        *,
        categories: Sequence[str] = (),
        names: Sequence[str] = (),
        fallback: Sequence[str] = (),
        predicate: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None, str | None]:
        paths = _path_from_evidence(self.root, report, categories, names)
        paths.extend(str(item) for item in fallback)
        seen: set[str] = set()
        for relative in paths:
            if relative in seen:
                continue
            seen.add(relative)
            payload, path, source_hash = _read_json(self.root, relative)
            if payload is None:
                continue
            if predicate is not None and not predicate(payload):
                continue
            if predicate is None and payload.get("objective_id") not in (None, "", objective_id):
                continue
            return payload, path, source_hash
        return None, None, None

    def _load_governance_sources(self, report: Mapping[str, Any], objective_id: str, objective: Mapping[str, Any]) -> dict[str, Any]:
        parent_objective_id = str(objective.get("parent_objective_id") or "")
        proposal_id = str(objective.get("parent_proposal_id") or "")
        proposal_fallback = [
            "reports/research_evolution/proposals/RESEARCH_EVOLUTION_PROPOSAL.json",
            "reports/research_evolution/proposals/PROPOSAL.json",
        ]
        proposal, proposal_path, proposal_hash = self._find_payload(
            report,
            objective_id,
            categories=("proposal_governance",),
            fallback=proposal_fallback,
            predicate=lambda item: (
                (not proposal_id or str(item.get("proposal_id") or "") == proposal_id)
                and str(item.get("created_objective_id") or objective_id) == objective_id
            ),
        )
        parent_objective_id = str((proposal or {}).get("parent_objective_id") or parent_objective_id)
        lineage, lineage_path, lineage_hash = self._find_payload(
            report,
            objective_id,
            categories=("lineage",),
            fallback=(f"data/research/research_factory/lineage/{objective_id}.json",),
            predicate=lambda item: str(item.get("objective_id") or objective_id) == objective_id,
        )
        coverage, coverage_path, coverage_hash = self._find_payload(
            report,
            objective_id,
            categories=("proposal_governance",),
            names=("MECHANISM_COVERAGE_REGISTRY.json", "mechanism_coverage_registry.json"),
            fallback=(
                "reports/research_evolution/proposals/MECHANISM_COVERAGE_REGISTRY.json",
                "data/research/research_factory/mechanism_coverage_registry.json",
            ),
            predicate=lambda item: "covered" in item or "unexplored" in item or "coverage_id" in item,
        )
        failure_fallback = []
        explicit_failure = str((proposal or {}).get("failure_landscape_ref") or "")
        if explicit_failure:
            failure_fallback.append(explicit_failure)
        if parent_objective_id:
            failure_fallback.append(f"reports/research_evolution/{parent_objective_id}/failure_landscape.json")
        failure_fallback.extend((
            f"reports/research_evolution/{objective_id}/failure_landscape.json",
            "reports/research_evolution/failure_landscape.json",
            "reports/FAILURE_LANDSCAPE.json",
        ))
        landscape, landscape_path, landscape_hash = self._find_payload(
            report,
            objective_id,
            categories=(),
            fallback=tuple(failure_fallback),
            predicate=lambda item: isinstance(item.get("entries"), list),
        )
        design, design_path, design_hash = self._find_payload(
            report,
            objective_id,
            categories=("ai_design",),
            names=("AI_RESEARCH_DESIGN_PROPOSAL.json",),
            fallback=(f"reports/research_evolution/ai_design/{objective_id}/AI_RESEARCH_DESIGN_PROPOSAL.json",),
            predicate=lambda item: str(item.get("objective_id") or objective_id) == objective_id,
        )
        approval_paths = _path_from_evidence(self.root, report, ("ai_design_approval",))
        approval_path = approval_paths[0] if approval_paths else None
        approval_hash = None
        if approval_path:
            _, _, approval_hash = _read_json(self.root, approval_path)
        return {
            "proposal": proposal or {},
            "proposal_path": proposal_path,
            "proposal_hash": proposal_hash,
            "lineage": lineage or {},
            "lineage_path": lineage_path,
            "lineage_hash": lineage_hash,
            "coverage": coverage or {},
            "coverage_path": coverage_path,
            "coverage_hash": coverage_hash,
            "failure_landscape": landscape or {},
            "failure_landscape_path": landscape_path,
            "failure_landscape_hash": landscape_hash,
            "design": design or {},
            "design_path": design_path,
            "design_hash": design_hash,
            "approval_path": approval_path,
            "approval_hash": approval_hash,
        }

    def _load_data(self, objective: Mapping[str, Any]) -> tuple[dict[str, Any], str | None, str | None]:
        del objective
        payload, path, source_hash = _read_json(self.root, "data/research/data_capability.json")
        return payload or {}, path, source_hash

    def _factor_capabilities(self, objective: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, tuple[str, str]]]:
        scope = set(_strings(objective.get("allowed_factor_scope")))
        rows: dict[str, dict[str, Any]] = {}
        sources: dict[str, tuple[str, str]] = {}
        for relative in _FACTOR_REGISTRY_PATHS:
            payload, path, source_hash = _read_json(self.root, relative)
            if payload is None or path is None or source_hash is None:
                continue
            raw_rows = payload.get("factors") if isinstance(payload.get("factors"), list) else []
            if isinstance(payload.get("factors"), Mapping):
                raw_rows = [{"factor_id": key, "status": value} for key, value in payload["factors"].items()]
            for raw in raw_rows:
                if not isinstance(raw, Mapping):
                    continue
                factor_id = str(raw.get("factor_id") or raw.get("name") or "")
                if not factor_id:
                    continue
                existing = rows.get(factor_id)
                if existing is not None and existing.get("provider") == "canonical_unified_factor_registry":
                    continue
                implementation = str(raw.get("implementation_status") or raw.get("lifecycle_status") or "")
                pit_status = str(raw.get("pit_status") or "")
                pit_safe = raw.get("PIT_safe")
                if pit_safe is True and not pit_status:
                    pit_status = "PIT_VERIFIED"
                availability = "AVAILABLE" if implementation not in {"DEFERRED", "SPEC_ONLY", "NOT_IMPLEMENTED"} else "REGISTERED_NOT_EXECUTABLE"
                if not implementation:
                    availability = "REGISTERED"
                item = {
                    "factor_id": factor_id,
                    "availability": availability,
                    "pit_class": pit_status or ("PIT_SAFE" if pit_safe is True else "PIT_UNKNOWN"),
                    "required_sources": _strings(raw.get("inputs") or raw.get("data_dependencies") or raw.get("required_sources")),
                    "minimum_warmup": _number(_first(raw, "min_warmup_bars", "lookback_bars", "lookback")),
                    "time_semantics": str(_first(raw, "available_at_rule", "available_at_semantics", "frequency", default="UNKNOWN")),
                    "usable_for_current_objective": factor_id in scope and (pit_safe is True or pit_status in {"PIT_VERIFIED", "PIT_SAFE"}) and availability == "AVAILABLE",
                    "provider": "canonical_unified_factor_registry" if "unified_factor_registry" in path else "canonical_factor_registry",
                }
                rows[factor_id] = item
                sources[factor_id] = (path, source_hash)
        for factor_id in sorted(scope - set(rows)):
            rows[factor_id] = {
                "factor_id": factor_id,
                "availability": "NOT_REGISTERED",
                "pit_class": "PIT_UNKNOWN",
                "required_sources": [],
                "minimum_warmup": None,
                "time_semantics": "UNKNOWN",
                "usable_for_current_objective": False,
                "provider": None,
            }
        return [_stable(rows[key]) for key in sorted(rows)], sources

    def _event_capabilities(self, objective: Mapping[str, Any], data_payload: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, tuple[str, str]]]:
        rows: dict[str, dict[str, Any]] = {}
        sources: dict[str, tuple[str, str]] = {}
        datasets = [item for item in data_payload.get("datasets", ()) if isinstance(item, Mapping)]
        event_scope = set(_strings(objective.get("allowed_event_scope") or objective.get("event_scope") or objective.get("event_ids")))
        for relative in _EVENT_REGISTRY_PATHS:
            payload, path, source_hash = _read_json(self.root, relative)
            if payload is None or path is None or source_hash is None:
                continue
            event_rows = payload.get("events") if isinstance(payload.get("events"), list) else []
            for raw in event_rows:
                if not isinstance(raw, Mapping):
                    continue
                event_id = str(raw.get("event_id") or raw.get("name") or "")
                if not event_id or event_id in rows:
                    continue
                event_store = str(raw.get("event_store") or "")
                store_path = _safe_relative(self.root, event_store) if event_store else None
                available = bool(store_path and store_path.exists()) or bool(raw.get("historical_evidence_immutable"))
                matching = [item for item in datasets if str(item.get("dataset_id") or "") in _strings(raw.get("inputs"))]
                coverage = {
                    "earliest": _first(raw, "earliest_date", "research_window_start"),
                    "latest": _first(raw, "latest_date", "research_window_end"),
                }
                if not coverage["earliest"] and matching:
                    coverage["earliest"] = min((item.get("earliest_date") for item in matching if item.get("earliest_date") is not None), default=None)
                if not coverage["latest"] and matching:
                    coverage["latest"] = max((item.get("latest_date") for item in matching if item.get("latest_date") is not None), default=None)
                usable = available and raw.get("PIT_safe") is True and (not event_scope or event_id in event_scope)
                rows[event_id] = {
                    "event_id": event_id,
                    "availability": "AVAILABLE" if available else "REGISTERED_NO_STORE",
                    "available_at_semantics": str(_first(raw, "available_at_semantics", "availability_rule", default="UNKNOWN")),
                    "provider": str(_first(raw, "provider", "source_lineage", default=path)),
                    "PIT_semantics": "PIT_SAFE" if raw.get("PIT_safe") is True else "PIT_UNKNOWN",
                    "research_window_coverage": coverage,
                    "usable_for_current_objective": usable,
                    "blocked_reason": None if usable else "EVENT_SCOPE_NOT_ALLOWED" if event_scope and event_id not in event_scope else "EVENT_STORE_OR_PIT_UNAVAILABLE",
                }
                sources[event_id] = (path, source_hash)
        return [_stable(rows[key]) for key in sorted(rows)], sources

    def _data_capabilities(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        datasets = [_safe_dataset(item) for item in payload.get("datasets", ()) if isinstance(item, Mapping) and item.get("dataset_id")]
        datasets = [_stable(item) for item in datasets]
        specs = (
            ("daily", lambda item: str(item.get("frequency", "")).upper() == "DAILY"),
            ("5m", lambda item: "5" in str(item.get("frequency", "")).lower() or "5m" in str(item.get("dataset_id", "")).lower()),
            ("1m", lambda item: any(token in str(item.get("frequency", "")).lower() for token in ("1m", "1min", "minute_1")) or "1m" in str(item.get("dataset_id", "")).lower()),
            ("benchmark", lambda item: any(token in str(item.get("dataset_id", "")).lower() for token in ("benchmark", "index"))),
            ("PIT", lambda item: item.get("PIT_safe") is True),
            ("event_sources", lambda item: str(item.get("frequency", "")).upper() in {"EVENT", "IRREGULAR"} or "event" in str(item.get("dataset_id", "")).lower()),
            ("financial_data", lambda item: "financial" in str(item.get("dataset_id", "")).lower()),
            ("tradability", lambda item: any(token in str(item.get("dataset_id", "")).lower() for token in ("trad", "limit", "suspension", "universe"))),
            ("factor_store", lambda item: "factor" in str(item.get("dataset_id", "")).lower()),
        )
        result: list[dict[str, Any]] = []
        for category, matcher in specs:
            matched = [item for item in datasets if matcher(item)]
            ready = [item for item in matched if item.get("status") == "READY"]
            online = [item for item in matched if item.get("status") == "ONLINE_ONLY"]
            available = bool(matched)
            availability = "READY" if ready else "ONLINE_ONLY" if online else "REGISTERED" if matched else "NOT_REGISTERED"
            result.append({
                "capability_id": category,
                "category": category,
                "available": available,
                "availability": availability,
                "provider": sorted({str(item.get("provider")) for item in matched if item.get("provider")}),
                "source_dataset_ids": sorted(str(item.get("dataset_id")) for item in matched),
                "research_window": {
                    "earliest": min((item.get("earliest_date") for item in matched if item.get("earliest_date") is not None), default=None),
                    "latest": max((item.get("latest_date") for item in matched if item.get("latest_date") is not None), default=None),
                },
                "blocked_reason": None if available else "NO_REGISTERED_DATASET",
                "dataset": None,
            })
        result.extend({"capability_id": f"dataset:{item['dataset_id']}", "category": "dataset", "available": item.get("status") in {"READY", "ONLINE_ONLY"}, "availability": item.get("status") or "UNKNOWN", "provider": [str(item.get("provider"))] if item.get("provider") else [], "source_dataset_ids": [item["dataset_id"]], "research_window": {"earliest": item.get("earliest_date"), "latest": item.get("latest_date")}, "blocked_reason": None if item.get("status") in {"READY", "ONLINE_ONLY"} else "DATASET_NOT_READY", "dataset": item} for item in datasets)
        return _stable(result)

    def _collect_snapshot(self, objective_id: str, *, purpose: str = "RUNTIME") -> dict[str, Any]:
        report = self._reconcile(objective_id)
        if purpose == "AI_DESIGN":
            # AI Design is the artifact being produced from this context.  It
            # must not become an input to its own source hash on the next
            # idempotent read.  Keep the reconciler as the first source of
            # truth, but remove only this downstream artifact from the design
            # purpose's read model.
            report = dict(report)
            evidence = report.get("evidence_inventory") if isinstance(report.get("evidence_inventory"), Mapping) else {}
            report["evidence_inventory"] = {
                key: list(value) if isinstance(value, list) else value
                for key, value in evidence.items()
                if key not in {"ai_design", "ai_design_approval"}
            }
            report["ai_design_reconciliation"] = {
                "approval_status": "NOT_AVAILABLE",
                "approval_evidence_present": False,
                "approval_evidence_missing": False,
            }
            effective = report.get("effective_objective_state") if isinstance(report.get("effective_objective_state"), Mapping) else {}
            candidate = report.get("candidate_reconciliation") if isinstance(report.get("candidate_reconciliation"), Mapping) else {}
            trial = report.get("trial_reconciliation") if isinstance(report.get("trial_reconciliation"), Mapping) else {}
            budget = report.get("budget") if isinstance(report.get("budget"), Mapping) else {}
            canonical_budget = budget.get("canonical") if isinstance(budget.get("canonical"), Mapping) else {}
            if (
                not candidate.get("current_candidate_id")
                and not trial.get("active_trial_count")
                and not canonical_budget.get("remaining") == 0
                and str(budget.get("authority_status") or "") != "AMBIGUOUS"
                and CANONICAL_CONFLICT not in {str(item) for item in report.get("conflicts", ())}
            ):
                normalized_effective = dict(effective)
                normalized_effective.update({
                    "effective_stage": "AI_RESEARCH_DESIGN",
                    "effective_state": "NEED_AI_RESEARCH_DESIGN",
                    "required_action": "NEED_AI_RESEARCH_DESIGN",
                    "required_action_supported": True,
                    "safe_to_resume": True,
                    "safe_to_advance": False,
                })
                report["effective_objective_state"] = normalized_effective
                report["effective_stage"] = "AI_RESEARCH_DESIGN"
                report["effective_state"] = "NEED_AI_RESEARCH_DESIGN"
                report["required_action"] = "NEED_AI_RESEARCH_DESIGN"
                report["required_action_supported"] = True
                report["safe_to_resume"] = True
                report["safe_to_advance"] = False
        elif purpose == "CANDIDATE_PROPOSAL":
            # Candidate Proposal is also a derived artifact.  Its own
            # governance record must not alter the source context used to
            # create or re-open that proposal.  The approved AI Design,
            # objective, budget, capabilities and structural facts remain
            # visible and hash-bound.
            report = dict(report)
            evidence = report.get("evidence_inventory") if isinstance(report.get("evidence_inventory"), Mapping) else {}
            report["evidence_inventory"] = {
                key: list(value) if isinstance(value, list) else value
                for key, value in evidence.items()
                if key not in {"candidate_governance", "durable_frozen_contract", "executable_materialization"}
            }
            report["candidate_reconciliation"] = {
                "current_candidate_id": None,
                "current_candidate_hash": None,
                "candidates": [],
                "durable_contracts": [],
                "executable_frozen_candidate": False,
                "structural_preflight_ready": False,
                "governance_freeze_present": False,
                "proposal_present": False,
                "materialization": {},
            }
            report["current_candidate_id"] = None
            report["current_candidate_hash"] = None
            ai_view = report.get("ai_design_reconciliation") if isinstance(report.get("ai_design_reconciliation"), Mapping) else {}
            approval_status = str(ai_view.get("approval_status") or "PENDING")
            effective = report.get("effective_objective_state") if isinstance(report.get("effective_objective_state"), Mapping) else {}
            normalized_effective = dict(effective)
            if approval_status == "APPROVED":
                normalized_effective.update({
                    "effective_stage": "AI_DESIGN_APPROVAL",
                    "effective_state": "AI_DESIGN_APPROVED",
                    "required_action": "GENERATE_CANDIDATE_PROPOSAL",
                    "required_action_supported": True,
                    "safe_to_resume": True,
                    "safe_to_advance": True,
                })
            elif ai_view.get("design_exists"):
                normalized_effective.update({
                    "effective_stage": "AI_DESIGN_APPROVAL",
                    "effective_state": "AI_DESIGN_AWAITING_CONFIRMATION",
                    "required_action": "HUMAN_CONFIRM_AI_RESEARCH_DESIGN",
                    "required_action_supported": True,
                    "safe_to_resume": True,
                    "safe_to_advance": False,
                })
            else:
                normalized_effective.update({
                    "effective_stage": "AI_RESEARCH_DESIGN",
                    "effective_state": "NEED_AI_RESEARCH_DESIGN",
                    "required_action": "NEED_AI_RESEARCH_DESIGN",
                    "required_action_supported": True,
                    "safe_to_resume": True,
                    "safe_to_advance": False,
                })
            report["effective_objective_state"] = normalized_effective
            for key in ("effective_stage", "effective_state", "required_action", "required_action_supported", "safe_to_resume", "safe_to_advance"):
                report[key] = normalized_effective.get(key)
        objective, objective_path, objective_source_hash = self._load_objective(objective_id)
        governance = self._load_governance_sources(report, objective_id, objective)
        if purpose == "AI_DESIGN":
            governance = dict(governance)
            governance.update({
                "design": {},
                "design_path": None,
                "design_hash": None,
                "approval_path": None,
                "approval_hash": None,
            })
        data_payload, data_path, data_source_hash = self._load_data(objective)
        data_capabilities = self._data_capabilities(data_payload)
        factor_capabilities, factor_sources = self._factor_capabilities(objective)
        event_capabilities, event_sources = self._event_capabilities(objective, data_payload)

        dialect = report.get("objective_dialect") if isinstance(report.get("objective_dialect"), Mapping) else {}
        effective = report.get("effective_objective_state") if isinstance(report.get("effective_objective_state"), Mapping) else {}
        objective_safe = {
            "objective_id": objective_id,
            "objective_dialect": str(dialect.get("dialect") or "UNKNOWN"),
            "effective_stage": str(effective.get("effective_stage") or report.get("effective_stage") or "UNKNOWN"),
            "effective_state": str(effective.get("effective_state") or report.get("effective_state") or "UNKNOWN"),
            "required_action": effective.get("required_action", report.get("required_action")),
            "required_action_supported": bool(effective.get("required_action_supported", report.get("required_action_supported", False))),
            "safe_to_resume": bool(effective.get("safe_to_resume", report.get("safe_to_resume", False))),
            "safe_to_advance": bool(effective.get("safe_to_advance", report.get("safe_to_advance", False))),
            "research_direction": str(objective.get("research_direction") or ""),
            "mechanism_scope": _strings(objective.get("mechanism_scope")),
            "allowed_factor_scope": _strings(objective.get("allowed_factor_scope")),
            "preferred_horizon": [_number(item) for item in objective.get("preferred_horizon", ()) if _number(item) is not None],
            "risk_constraints": _safe_risk(objective.get("risk_constraints")),
            "multiple_testing_family_id": str(objective.get("multiple_testing_family_id") or ""),
            "lineage_id": str(governance["lineage"].get("lineage_id") or ""),
        }
        safe_proposal = _safe_proposal(governance["proposal"])
        safe_lineage = _safe_lineage(governance["lineage"])
        safe_coverage = _safe_coverage(governance["coverage"])
        safe_design = _safe_design(governance["design"])
        landscape = _safe_landscape(governance["failure_landscape"])
        failure_taxonomy = sorted({
            *[_taxonomy(item) for item in safe_proposal.get("failure_taxonomy", ())],
            *[_taxonomy(item) for item in landscape.get("category_totals", {})],
            *[category for item in landscape.get("entries", ()) if isinstance(item, Mapping) for category in item.get("failure_categories", ())],
        })

        reconciliation_candidate = report.get("candidate_reconciliation") if isinstance(report.get("candidate_reconciliation"), Mapping) else {}
        durable_raw_by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
        for raw_entry in reconciliation_candidate.get("durable_contracts", ()) if isinstance(reconciliation_candidate, Mapping) else ():
            if not isinstance(raw_entry, Mapping):
                continue
            source_path = str(raw_entry.get("source_path") or "")
            if not source_path:
                continue
            source_payload, _, _ = _read_json(self.root, source_path)
            for raw_contract in _contract_payloads(source_payload):
                candidate_id = str(raw_contract.get("candidate_id") or "")
                candidate_hash = str(raw_contract.get("candidate_hash") or raw_contract.get("candidate_preregistration_hash") or "")
                if candidate_id and candidate_hash:
                    durable_raw_by_key[(candidate_id, candidate_hash)] = raw_contract
        durable_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for raw in reconciliation_candidate.get("durable_contracts", ()) if isinstance(reconciliation_candidate, Mapping) else ():
            if not isinstance(raw, Mapping):
                continue
            candidate_id = str(raw.get("candidate_id") or "")
            candidate_hash = str(raw.get("candidate_hash") or "")
            if candidate_id and candidate_hash:
                source_contract = durable_raw_by_key.get((candidate_id, candidate_hash), {})
                merged_contract = {**dict(source_contract), **dict(raw)}
                durable_by_key[(candidate_id, candidate_hash)] = _safe_contract(merged_contract, structural_ready=raw.get("from_dict") == "PASS" and raw.get("provider_candidate_payload") == "PASS")
        candidates: list[dict[str, Any]] = []
        for raw in reconciliation_candidate.get("candidates", ()) if isinstance(reconciliation_candidate, Mapping) else ():
            if not isinstance(raw, Mapping):
                continue
            candidate_id = str(raw.get("candidate_id") or "")
            hashes = _strings(raw.get("candidate_hashes"))
            contract = next((value for (key, _), value in durable_by_key.items() if key == candidate_id), None)
            item = {
                "candidate_id": candidate_id,
                "candidate_hashes": hashes,
                "sources": _strings(raw.get("sources")),
                "freeze_evidence": bool(raw.get("freeze_evidence")),
                "states": _strings(raw.get("states")),
                "mechanism": contract.get("mechanism") if contract else None,
                "factor_ids": contract.get("factor_ids", []) if contract else [],
                "event_ids": contract.get("event_ids", []) if contract else [],
                "execution_contract": contract.get("execution_contract", {}) if contract else {},
                "data_contract": contract.get("data_contract", {}) if contract else {},
                "semantic_fingerprint": contract.get("semantic_fingerprint") if contract else None,
                "durable_contract_hash": contract.get("durable_contract_hash") if contract else None,
                "structural_readiness": "READY_FOR_STRUCTURAL_PREFLIGHT" if reconciliation_candidate.get("executable_frozen_candidate") and candidate_id == reconciliation_candidate.get("current_candidate_id") else "NOT_READY",
            }
            candidates.append(item)
        for contract in durable_by_key.values():
            if not any(item.get("candidate_id") == contract.get("candidate_id") and contract.get("candidate_hash") in item.get("candidate_hashes", []) for item in candidates):
                candidates.append(contract)
        candidates = _stable(candidates)
        current_candidate_hash = report.get("current_candidate_hash") or reconciliation_candidate.get("current_candidate_hash")
        candidate_identity_hash = str(current_candidate_hash or (stable_hash(candidates) if candidates else "")) or None
        candidate_safe = {
            "current_candidate_id": report.get("current_candidate_id") or reconciliation_candidate.get("current_candidate_id"),
            "current_candidate_hash": current_candidate_hash,
            "candidates": candidates,
            "durable_contracts": _stable(list(durable_by_key.values())),
            "structural_readiness": bool(reconciliation_candidate.get("structural_preflight_ready")),
            "read_only": True,
        }

        trial_report = report.get("trial_reconciliation") if isinstance(report.get("trial_reconciliation"), Mapping) else {}
        trials = [_safe_trial(item) for item in trial_report.get("trials", ()) if isinstance(item, Mapping)]
        trials = _stable(trials)
        trial_safe = {
            "current_trial_id": trial_report.get("current_trial_id"),
            "trial_count": _number(trial_report.get("trial_count")) or len(trials),
            "trials": trials,
            "read_only": True,
        }
        budget_safe = _safe_budget(report.get("budget") if isinstance(report.get("budget"), Mapping) else {}, objective_id)
        budget_safe["testing_family"] = objective_safe["multiple_testing_family_id"] or None
        structural_safe = _safe_structural(report.get("structural_result_reconciliation"), objective_id)
        structural_hash = stable_hash(structural_safe)
        budget_hash = stable_hash(budget_safe)
        candidate_component_hash = candidate_identity_hash or None
        factor_hash = stable_hash({"capabilities": factor_capabilities, "source_hashes": factor_sources})
        event_hash = stable_hash({"capabilities": event_capabilities, "source_hashes": event_sources})
        data_manifest_hash = data_source_hash or stable_hash(data_capabilities)
        effective_state_hash = stable_hash({
            key: objective_safe.get(key)
            for key in ("objective_id", "objective_dialect", "effective_stage", "effective_state", "required_action", "safe_to_resume", "safe_to_advance", "lineage_id")
        })
        projections = report.get("projection_reconciliation") if isinstance(report.get("projection_reconciliation"), Mapping) else {}
        projection_conflicts = sorted(item for item in report.get("conflicts", ()) if str(item).startswith("PROJECTION_"))
        runtime_health = {
            "runtime_projection": True,
            "source_kind": "daemon_orchestrator_projection",
            "projection_drift_detected": bool(projection_conflicts),
            "projection_conflict_codes": projection_conflicts,
            "projection_count": (
                _number(projections.get("projection_count"))
                or (
                    len(report.get("projection_refs", {}).get("daemon", ()))
                    + len(report.get("projection_refs", {}).get("orchestrator", ()))
                    if isinstance(report.get("projection_refs"), Mapping)
                    else 0
                )
            ),
            "canonical_state_used_for_truth": True,
        }

        source_bindings: dict[str, dict[str, Any]] = {}
        def bind(key: str, path: str | None, source_hash: str | None, role: str) -> None:
            if path and source_hash:
                source_bindings[key] = {"path": path, "source_hash": source_hash, "authority_role": role}
        bind("objective", objective_path, objective_source_hash, "OBJECTIVE_DEFINITION_FACT")
        bind("proposal", governance.get("proposal_path"), governance.get("proposal_hash"), "RESEARCH_PROPOSAL_AUTHORITY")
        bind("lineage", governance.get("lineage_path"), governance.get("lineage_hash"), "LINEAGE_INDEX_EVIDENCE")
        bind("mechanism_coverage", governance.get("coverage_path"), governance.get("coverage_hash"), "MECHANISM_COVERAGE_AUTHORITY")
        bind("failure_landscape", governance.get("failure_landscape_path"), governance.get("failure_landscape_hash"), "ABSTRACT_FAILURE_TAXONOMY")
        bind("ai_design", governance.get("design_path"), governance.get("design_hash"), "AI_DESIGN_FACT")
        bind("ai_design_approval", governance.get("approval_path"), governance.get("approval_hash"), "AI_DESIGN_APPROVAL_AUTHORITY")
        bind("data_manifest", data_path, data_source_hash, "DATA_CAPABILITY_REGISTRY")
        for factor_id, (path, source_hash) in factor_sources.items():
            bind(f"factor_registry:{factor_id}", path, source_hash, "FACTOR_CAPABILITY_REGISTRY")
        for event_id, (path, source_hash) in event_sources.items():
            bind(f"event_registry:{event_id}", path, source_hash, "EVENT_CAPABILITY_REGISTRY")
        budget_view = report.get("budget") if isinstance(report.get("budget"), Mapping) else {}
        budget_sources = budget_view.get("sources") if isinstance(budget_view.get("sources"), list) else []
        for item in budget_sources:
            if isinstance(item, Mapping) and item.get("source_classification") == "CURRENT_CANONICAL":
                bind("budget", str(item.get("path") or ""), str(item.get("source_hash") or ""), "SEARCH_BUDGET_AUTHORITY")
        for category, role in (("candidate_governance", "CANDIDATE_GOVERNANCE_AUTHORITY"), ("durable_frozen_contract", "EXECUTABLE_FROZEN_CANDIDATE_AUTHORITY"), ("trial_ledger", "TRIAL_LIFECYCLE_AUTHORITY"), ("structural_result", "STRUCTURAL_RESULT_AUTHORITY")):
            entries = report.get("evidence_inventory", {}).get(category, ()) if isinstance(report.get("evidence_inventory"), Mapping) else ()
            for index, item in enumerate(entries if isinstance(entries, list) else ()):
                if isinstance(item, Mapping):
                    bind(f"{category}:{index}", str(item.get("path") or ""), str(item.get("source_hash") or ""), role)

        created_at = _clock_timestamp(self.clock)
        parsed_created = _parse_timestamp(created_at)
        fresh_until = (parsed_created + timedelta(seconds=self.freshness_ttl_seconds)).isoformat() if parsed_created else created_at
        latest_candidates = [
            _safe_timestamp(value.get("generated_at"))
            for value in (objective, governance["proposal"], governance["lineage"], data_payload)
            if isinstance(value, Mapping) and value.get("generated_at")
        ]
        freshness = {
            "data_cutoff": objective.get("research_end") or data_payload.get("research_end") or max((item.get("latest_date") for item in data_payload.get("datasets", ()) if isinstance(item, Mapping) and item.get("latest_date") is not None), default=None),
            "manifest_identity": {"path": data_path, "source_hash": data_source_hash},
            "latest_known_source_timestamp": max(latest_candidates, default=None),
            "generated_at": created_at,
            "fresh_until": fresh_until,
            "ttl_seconds": self.freshness_ttl_seconds,
        }
        canonical_source_hashes = {key: value.get("source_hash") for key, value in source_bindings.items() if value.get("authority_role") != "RUNTIME_PROJECTION"}
        identity_seed = {
            "objective_id": objective_id,
            "context_version": SAFE_RUNTIME_CONTEXT_VERSION,
            "effective_state_hash": effective_state_hash,
            "canonical_source_hashes": canonical_source_hashes,
            "budget_hash": budget_hash,
            "candidate_hash": candidate_component_hash,
            "structural_hash": structural_hash,
            "data_manifest_hash": data_manifest_hash,
            "factor_capability_hash": factor_hash,
            "event_capability_hash": event_hash,
            "created_at": created_at,
            "fresh_until": fresh_until,
        }
        # 时间字段描述本次构建的有效窗口，但不应让同一组 canonical 事实在
        # 重新读取时变成“上下文已变化”。上下文身份因此只绑定事实组件；
        # freshness 仍单独校验有效期与事实组件哈希。
        identity_seed.pop("created_at", None)
        identity_seed.pop("fresh_until", None)
        context_id = f"SAFE_RUNTIME_CONTEXT_{stable_hash(identity_seed)[:24].upper()}"
        identity = {
            "context_id": context_id,
            "context_version": SAFE_RUNTIME_CONTEXT_VERSION,
            "objective_id": objective_id,
            "effective_state_hash": effective_state_hash,
            "canonical_source_hashes": canonical_source_hashes,
            "budget_hash": budget_hash,
            "candidate_hash": candidate_component_hash,
            "structural_hash": structural_hash,
            "data_manifest_hash": data_manifest_hash,
            "factor_capability_hash": factor_hash,
            "event_capability_hash": event_hash,
            "created_at": created_at,
            "fresh_until": fresh_until,
        }
        payload: dict[str, Any] = {
            "schema_version": SAFE_RUNTIME_CONTEXT_SCHEMA_VERSION,
            "context_version": SAFE_RUNTIME_CONTEXT_VERSION,
            "context_purpose": purpose,
            "read_only": True,
            "outcome_blind": True,
            "performance_data_loaded": False,
            "outcome_fields_available": False,
            "identity": identity,
            "objective": objective_safe,
            "governance": {
                "proposal": safe_proposal,
                "objective_lineage": safe_lineage,
                "mechanism_coverage": safe_coverage,
                "ai_design": safe_design,
                "ai_design_approval": {
                    "status": str((report.get("ai_design_reconciliation") or {}).get("approval_status") or "NOT_AVAILABLE") if isinstance(report.get("ai_design_reconciliation"), Mapping) else "NOT_AVAILABLE",
                    "approval_evidence_present": bool(governance.get("approval_path")),
                    "source_context_id": safe_design.get("source_context_id"),
                    "source_context_hash": safe_design.get("source_context_hash") or safe_design.get("input_context_hash"),
                },
            },
            "candidate": candidate_safe,
            "trial": trial_safe,
            "budget": budget_safe,
            "structural": structural_safe,
            "data_capabilities": data_capabilities,
            "factor_capabilities": factor_capabilities,
            "event_capabilities": event_capabilities,
            "failure_taxonomy": failure_taxonomy,
            "failure_landscape": landscape,
            "mechanism_constraints": {
                "covered_mechanisms": safe_coverage.get("covered", []),
                "unexplored_mechanisms": safe_coverage.get("unexplored", []),
                "excluded_mechanisms": _strings([*safe_coverage.get("forbidden_mechanisms", []), *safe_proposal.get("avoid_mechanism_family", []), *safe_proposal.get("avoid_mechanisms", [])]),
                "registry_identity": safe_coverage.get("coverage_id"),
                "read_only": True,
            },
            "runtime_health": runtime_health,
            "freshness": freshness,
            "source_bindings": source_bindings,
        }
        hash_payload = _stable(payload)
        hash_freshness = hash_payload.get("freshness") if isinstance(hash_payload, Mapping) else None
        if isinstance(hash_freshness, Mapping):
            hash_freshness = dict(hash_freshness)
            hash_freshness["generated_at"] = None
            hash_freshness["fresh_until"] = None
            hash_payload["freshness"] = hash_freshness
        hash_identity = {
            key: value
            for key, value in identity.items()
            if key not in {"created_at", "fresh_until", "context_hash"}
        }
        hash_identity["context_hash"] = None
        context_hash = stable_hash({**hash_payload, "identity": hash_identity})
        identity["context_hash"] = context_hash
        return {
            "payload": _stable(payload),
            "component_hashes": {
                "effective_state_hash": effective_state_hash,
                "candidate_hash": candidate_component_hash,
                "budget_hash": budget_hash,
                "structural_hash": structural_hash,
                "data_manifest_hash": data_manifest_hash,
                "factor_capability_hash": factor_hash,
                "event_capability_hash": event_hash,
                "context_hash": context_hash,
            },
        }

    def build(self, objective_id: str, *, purpose: str = "RUNTIME") -> SafeRuntimeContextV1:
        objective_id = self._validate_objective_id(objective_id)
        purpose = str(purpose or "RUNTIME").upper()
        if purpose not in {"RUNTIME", "AI_DESIGN", "CANDIDATE_PROPOSAL", "MANUAL_HANDOFF"}:
            raise SafeRuntimeContextError(CONTEXT_BUILD_BLOCKED, "安全运行时上下文用途不受支持。", status_code=400)
        snapshot = self._collect_snapshot(objective_id, purpose=purpose)
        return SafeRuntimeContextV1.from_dict(snapshot["payload"])

    build_context = build
    prepare = build

    def validate_context_freshness(self, context: SafeRuntimeContextV1 | Mapping[str, Any]) -> dict[str, Any]:
        current_context = context if isinstance(context, SafeRuntimeContextV1) else SafeRuntimeContextV1.from_dict(context)
        objective_id = current_context.objective_id
        if not objective_id:
            raise SafeRuntimeContextError(STALE_RUNTIME_CONTEXT, "安全运行时上下文缺少 Objective 身份。", status_code=409)
        now = _parse_timestamp(_clock_timestamp(self.clock))
        fresh_until = _parse_timestamp(current_context.identity.get("fresh_until"))
        if now is not None and fresh_until is not None and now > fresh_until:
            raise SafeRuntimeContextError(STALE_RUNTIME_CONTEXT, "安全运行时上下文已过期，请重新构建。", status_code=409, details={"context_id": current_context.context_id, "fresh_until": current_context.identity.get("fresh_until")})
        purpose = str(current_context.payload.get("context_purpose") or "RUNTIME").upper()
        current = self._collect_snapshot(objective_id, purpose=purpose)
        expected = current_context.identity
        actual = current["component_hashes"]
        changed = [
            key
            for key in ("effective_state_hash", "candidate_hash", "budget_hash", "structural_hash", "data_manifest_hash", "factor_capability_hash", "event_capability_hash")
            if (expected.get(key) or None) != (actual.get(key) or None)
        ]
        if not changed and str(expected.get("context_hash") or "") != str(actual.get("context_hash") or ""):
            changed.append("context_hash")
        if changed:
            raise SafeRuntimeContextError(STALE_RUNTIME_CONTEXT, "安全运行时上下文对应的 canonical 事实已变化，请重新构建。", status_code=409, details={"context_id": current_context.context_id, "changed_components": changed})
        return {
            "status": "FRESH",
            "valid": True,
            "context_id": current_context.context_id,
            "context_hash": current_context.context_hash,
            "checked_at": _clock_timestamp(self.clock),
            "changed_components": [],
        }


def validate_context_freshness(root: str | Path, context: SafeRuntimeContextV1 | Mapping[str, Any], *, clock: Callable[[], Any] = now_timestamp) -> dict[str, Any]:
    return SafeRuntimeContextBuilderV1(root, clock=clock).validate_context_freshness(context)


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建安全、只读、结果盲化的研究运行时上下文")
    parser.add_argument("--root", default=".", help="项目根目录")
    parser.add_argument("--objective-id", required=True)
    parser.add_argument("--json", action="store_true", help="输出完整机器 JSON")
    parser.add_argument("--output", help="将派生只读上下文写入项目内相对路径")
    args = parser.parse_args(argv)
    try:
        context = SafeRuntimeContextBuilderV1(args.root).build(args.objective_id)
        payload = context.to_dict()
        if args.output:
            root = Path(args.root).resolve()
            target = _safe_relative(root, args.output)
            if target is None:
                raise SafeRuntimeContextError("UNSAFE_PATH", "输出路径必须位于项目目录内。", status_code=400)
            target.parent.mkdir(parents=True, exist_ok=True)
            artifact = {"artifact_kind": "DERIVED_READ_MODEL", **payload}
            temporary = target.with_name(f".{target.name}.safe-runtime-context.tmp")
            temporary.write_text(json.dumps(_stable(artifact), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
            temporary.replace(target)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            objective = payload["objective"]
            budget = payload["budget"]
            candidate = payload["candidate"]
            trial = payload["trial"]
            print("安全运行时上下文已构建")
            print(f"Objective：{objective['objective_id']}")
            print(f"状态：{objective['effective_state']}；下一步：{objective.get('required_action')}")
            print(f"上下文：{context.context_id}；有效至：{payload['freshness']['fresh_until']}")
            print(f"Candidate：{len(candidate.get('candidates', []))}；Trial：{trial.get('trial_count', 0)}；预算：{budget.get('used')}/{budget.get('total')}，剩余 {budget.get('remaining')}")
        return 0
    except SafeRuntimeContextError as exc:
        print(json.dumps(exc.envelope(), ensure_ascii=False, indent=2), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "BUDGET_AUTHORITY_AMBIGUOUS",
    "CONTEXT_BUILD_BLOCKED",
    "DEFAULT_FRESHNESS_TTL_SECONDS",
    "OUTCOME_FIELD_BLOCKED",
    "SAFE_RUNTIME_CONTEXT_SCHEMA_VERSION",
    "SAFE_RUNTIME_CONTEXT_VERSION",
    "STALE_RUNTIME_CONTEXT",
    "SafeRuntimeContextBuilderV1",
    "SafeRuntimeContextError",
    "SafeRuntimeContextV1",
    "validate_context_freshness",
]
