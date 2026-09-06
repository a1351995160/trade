"""Bridge Candidate Governance Freeze evidence to the durable executable contract.

This module is deliberately narrower than Candidate Proposal governance.  It
does not generate a proposal, call an AI backend, reserve budget, start a
trial, or invoke Structural.  It only turns complete, already-frozen semantic
facts into the existing :class:`DurableFrozenCandidateContractV1` after a
second human confirmation.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import threading
from typing import Any

from ..research.strategy_candidate import StrategyCandidateSpec
from ..research.strategy_semantic import ExitPredicateSpec, SemanticCandidateRecord, SignalPredicateSpec
from .ai_design_approval import AIDesignApprovalServiceV1, APPROVED as AI_APPROVED
from .candidate_generation import (
    CANDIDATE_GOVERNANCE_FROZEN,
    CANDIDATE_FREEZE_RECEIPT_FILENAME,
    CANDIDATE_PROPOSAL_FILENAME,
    CANDIDATE_PROPOSAL_SCHEMA_VERSION,
    CANDIDATE_PROPOSAL_STATE_FILENAME,
    CANDIDATE_REGISTRY_SCHEMA_VERSION,
    CandidateGenerationManagerV1,
    CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW,
    EXECUTABLE_CANDIDATE_FROZEN,
    HUMAN_CONFIRM_EXECUTABLE_MATERIALIZATION,
    READY_FOR_STRUCTURAL_PREFLIGHT,
)
from .common import jsonable, now_timestamp, stable_hash
from .context import PerformanceBlindGuard, PerformanceLeakError
from .durability import (
    DurableFrozenCandidateContractRegistryV1,
    DurableFrozenCandidateContractV1,
    FROZEN_CANDIDATE_CONTRACT_REGISTRY_SCHEMA,
)


EXECUTABLE_MATERIALIZATION_SCHEMA_VERSION = "candidate-executable-materialization-bridge-v1"
EXECUTABLE_MATERIALIZATION_PREVIEW_SCHEMA_VERSION = "executable-materialization-preview-v1"
EXECUTABLE_MATERIALIZATION_CONFIRMATION_SCHEMA_VERSION = "executable-materialization-confirmation-v1"
EXECUTABLE_MATERIALIZATION_PREVIEW_FILENAME = "EXECUTABLE_MATERIALIZATION_PREVIEW.json"
EXECUTABLE_MATERIALIZATION_CONFIRMATION_FILENAME = "EXECUTABLE_MATERIALIZATION_CONFIRMATION.json"
EXECUTABLE_MATERIALIZATION_RECEIPT_FILENAME = EXECUTABLE_MATERIALIZATION_CONFIRMATION_FILENAME

EXECUTABLE_MATERIALIZATION_PREVIEW_READY = "EXECUTABLE_MATERIALIZATION_PREVIEW_READY"
CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION = "CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION"
EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED = "EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED"
EXECUTABLE_MATERIALIZATION_CONFIRMATION_MISSING = "EXECUTABLE_MATERIALIZATION_CONFIRMATION_MISSING"
EXECUTABLE_MATERIALIZATION_INCOMPLETE = "EXECUTABLE_MATERIALIZATION_INCOMPLETE"
EXECUTABLE_CONTRACT_INVALID = "EXECUTABLE_CONTRACT_INVALID"
CANDIDATE_SEMANTIC_DRIFT = "CANDIDATE_SEMANTIC_DRIFT"
CANONICAL_CANDIDATE_IDENTITY_CONFLICT = "CANONICAL_CANDIDATE_IDENTITY_CONFLICT"
STALE_EXECUTABLE_MATERIALIZATION_PREVIEW = "STALE_EXECUTABLE_MATERIALIZATION_PREVIEW"
MATERIALIZATION_IDEMPOTENCY_CONFLICT = "MATERIALIZATION_IDEMPOTENCY_CONFLICT"
INTEGRITY_FAILURE = "INTEGRITY_FAILURE"

RUN_STRUCTURAL_PREFLIGHT = "RUN_STRUCTURAL_PREFLIGHT"
RECOVER_EXECUTABLE_MATERIALIZATION = "RECOVER_EXECUTABLE_MATERIALIZATION"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_REVIEWER_MAX_LENGTH = 128
_REQUIRED_CONTRACT_FIELDS = tuple(DurableFrozenCandidateContractV1.__dataclass_fields__)
MATERIALIZATION_CONFIRMATION_REQUIRED_FIELDS = (
    "schema_version",
    "bridge_schema_version",
    "confirmation_id",
    "objective_id",
    "proposal_id",
    "preview_id",
    "preview_hash",
    "candidate_id",
    "candidate_hash",
    "durable_contract_hash",
    "ai_design_id",
    "ai_design_hash",
    "ai_design_approval_hash",
    "source_context_id",
    "source_context_hash",
    "reviewer",
    "confirmed_at",
    "idempotency_key",
    "receipt_hash",
)
_SEMANTIC_FIELDS = (
    "family",
    "mechanism",
    "factor_ids",
    "factor_roles",
    "factor_directions",
    "event_ids",
    "event_timing_semantics",
    "entry_predicate",
    "confirmation_predicate",
    "interaction_semantics",
    "ranking_semantics",
    "selection_rule",
    "top_n",
    "max_positions",
    "holding_period_trading_sessions",
    "entry_timing",
    "exit_contract",
    "execution_contract_version",
    "t_plus_1_contract",
    "pit_dependencies",
)


class CandidateExecutableMaterializationError(RuntimeError):
    """Fail-closed error at the executable Candidate materialization boundary."""

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


@dataclass(frozen=True)
class _MaterializationContext:
    objective_id: str
    proposal_path: Path
    proposal: Mapping[str, Any]
    receipt_path: Path
    receipt: Mapping[str, Any]
    registry_path: Path
    registry_entry: Mapping[str, Any]
    objective_path: Path
    objective: Mapping[str, Any]
    lineage_path: Path | None
    lineage: Mapping[str, Any]
    design_path: Path
    design: Mapping[str, Any]
    approval_path: Path
    approval_receipt: Mapping[str, Any]
    approval: Mapping[str, Any]
    candidate_id: str
    candidate_hash: str
    batch_id: str
    factor_event_registry_identities: Mapping[str, Any]
    research_period_identity: Mapping[str, Any]
    policy_identity: Mapping[str, Any]
    source_provenance: Mapping[str, Any]
    full_semantic_record: Mapping[str, Any] | None
    hypothesis: Mapping[str, Any] | None
    full_contract_payload: Mapping[str, Any] | None
    source_bindings: Mapping[str, Any]


def _safe_id(value: Any, *, kind: str) -> str:
    result = str(value or "").strip()
    if not _IDENTIFIER_RE.fullmatch(result):
        raise CandidateExecutableMaterializationError("INVALID_IDENTIFIER", f"{kind} 标识不合法", status_code=400)
    return result


def _safe_reviewer(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > _REVIEWER_MAX_LENGTH or "\n" in result or "\r" in result:
        raise CandidateExecutableMaterializationError("REVIEWER_REQUIRED", "必须提供合法的执行合同确认人", status_code=400)
    return result


def _safe_token(value: Any, *, kind: str) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 255 or "\n" in result or "\r" in result:
        raise CandidateExecutableMaterializationError("IDEMPOTENCY_KEY_REQUIRED", f"必须提供合法的 {kind}", status_code=400)
    return result


def _read_json(path: Path, *, code: str, required: bool = True) -> Mapping[str, Any] | None:
    if not path.exists():
        if required:
            raise CandidateExecutableMaterializationError(code, f"缺少必要研究资料：{path.name}", status_code=404)
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CandidateExecutableMaterializationError(INTEGRITY_FAILURE, f"研究资料不可读：{path.name}", status_code=503) from exc
    if not isinstance(payload, Mapping):
        raise CandidateExecutableMaterializationError(INTEGRITY_FAILURE, f"研究资料不是 JSON 对象：{path.name}", status_code=503)
    return payload


def _serialized(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_bytes(_serialized(payload))
    os.replace(temporary, path)


def _create_immutable_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_bytes(_serialized(payload))
    try:
        os.link(temporary, path)
    except FileExistsError:
        raise
    finally:
        temporary.unlink(missing_ok=True)


def _relative(root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise CandidateExecutableMaterializationError("UNSAFE_PATH", "执行合同资料路径不在项目目录内", status_code=503) from exc


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) and value else None


def _first_mapping(value: Any, keys: set[str]) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, Mapping) and candidate:
                return candidate
        for nested in value.values():
            found = _first_mapping(nested, keys)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _first_mapping(nested, keys)
            if found is not None:
                return found
    return None


def _first_value(value: Any, keys: set[str]) -> Any:
    if isinstance(value, Mapping):
        for key in keys:
            if value.get(key) not in (None, ""):
                return value[key]
        for nested in value.values():
            found = _first_value(nested, keys)
            if found not in (None, ""):
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _first_value(nested, keys)
            if found not in (None, ""):
                return found
    return None


def _contract_mapping(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    if set(_REQUIRED_CONTRACT_FIELDS).issubset(value):
        return value
    for key in ("durable_contract", "durable_contract_payload", "executable_contract", "frozen_contract", "contract_payload"):
        nested = value.get(key)
        if isinstance(nested, Mapping) and set(_REQUIRED_CONTRACT_FIELDS) & set(nested):
            return nested
    return None


def _record_mapping(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    for key in (
        "full_semantic_record",
        "semantic_candidate_record",
        "candidate_semantic_record",
        "semantic_record",
        "durable_contract_input",
    ):
        nested = value.get(key)
        if isinstance(nested, Mapping) and all(item in nested for item in ("candidate", "signal_predicate", "exit_predicate")):
            return nested
    if all(item in value for item in ("candidate", "signal_predicate", "exit_predicate")):
        return value
    for nested in value.values():
        found = _record_mapping(nested)
        if found is not None:
            return found
    return None


def _hypothesis_mapping(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("hypothesis", "authoritative_hypothesis", "alpha_hypothesis", "frozen_hypothesis"):
        nested = value.get(key)
        if isinstance(nested, Mapping) and nested:
            return nested
    for nested in value.values():
        if isinstance(nested, Mapping):
            found = _hypothesis_mapping(nested)
            if found is not None:
                return found
    return None


def _normalise_for_hash(value: Any) -> Any:
    return jsonable(value)


def _semantic_projection_from_contract(contract: DurableFrozenCandidateContractV1) -> dict[str, Any]:
    payload = contract.to_dict()
    return {key: _normalise_for_hash(payload.get(key)) for key in _SEMANTIC_FIELDS}


def _semantic_projection_from_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Read only explicitly frozen semantic keys; never insert execution defaults."""

    nested = value.get("semantic_contract") if isinstance(value.get("semantic_contract"), Mapping) else value
    projection: dict[str, Any] = {}
    direct_names = {
        "family": ("family", "strategy_family"),
        "mechanism": ("mechanism",),
        "factor_ids": ("factor_ids",),
        "factor_roles": ("factor_roles",),
        "factor_directions": ("factor_directions",),
        "event_ids": ("event_ids",),
        "event_timing_semantics": ("event_timing_semantics",),
        "entry_predicate": ("entry_predicate",),
        "confirmation_predicate": ("confirmation_predicate",),
        "interaction_semantics": ("interaction_semantics",),
        "ranking_semantics": ("ranking_semantics",),
        "selection_rule": ("selection_rule",),
        "top_n": ("top_n",),
        "max_positions": ("max_positions",),
        "holding_period_trading_sessions": ("holding_period_trading_sessions", "holding_period"),
        "entry_timing": ("entry_timing",),
        "exit_contract": ("exit_contract",),
        "execution_contract_version": ("execution_contract_version",),
        "t_plus_1_contract": ("t_plus_1_contract",),
        "pit_dependencies": ("pit_dependencies",),
    }
    for canonical, names in direct_names.items():
        for name in names:
            if nested.get(name) not in (None, ""):
                projection[canonical] = _normalise_for_hash(nested[name])
                break
    return projection


def _governance_semantic_projection(proposal: Mapping[str, Any], registry_entry: Mapping[str, Any]) -> dict[str, Any]:
    explicit = (
        _mapping(proposal.get("candidate_semantic_contract"))
        or _mapping(proposal.get("executable_semantic_contract"))
        or _mapping(proposal.get("durable_contract_semantics"))
        or _mapping(registry_entry.get("candidate_semantic_contract"))
    )
    if explicit:
        return _semantic_projection_from_mapping(explicit)
    nested_contract = _contract_mapping(proposal) or _contract_mapping(registry_entry)
    if nested_contract:
        return _semantic_projection_from_mapping(nested_contract)

    projection: dict[str, Any] = {}
    if proposal.get("mechanism") not in (None, ""):
        projection["mechanism"] = _normalise_for_hash(proposal["mechanism"])
    if proposal.get("strategy_family") not in (None, ""):
        projection["family"] = _normalise_for_hash(proposal["strategy_family"])
    execution = _mapping(proposal.get("execution_contract"))
    data_contract = _mapping(proposal.get("data_contract"))
    for source in (proposal, execution or {}, data_contract or {}):
        if source.get("factor_ids") not in (None, ""):
            projection["factor_ids"] = _normalise_for_hash(source["factor_ids"])
        if source.get("factor_roles") not in (None, ""):
            projection["factor_roles"] = _normalise_for_hash(source["factor_roles"])
        if source.get("factor_directions") not in (None, ""):
            projection["factor_directions"] = _normalise_for_hash(source["factor_directions"])
        if source.get("holding_period") not in (None, ""):
            projection["holding_period_trading_sessions"] = _normalise_for_hash(source["holding_period"])
        if source.get("max_positions") not in (None, ""):
            projection["max_positions"] = _normalise_for_hash(source["max_positions"])
        if source.get("entry_timing") not in (None, ""):
            projection["entry_timing"] = _normalise_for_hash(source["entry_timing"])
        if source.get("selection_rule") not in (None, ""):
            projection["selection_rule"] = _normalise_for_hash(source["selection_rule"])
        if source.get("top_n") not in (None, ""):
            projection["top_n"] = _normalise_for_hash(source["top_n"])
        if source.get("pit_dependencies") not in (None, ""):
            projection["pit_dependencies"] = _normalise_for_hash(source["pit_dependencies"])
    return projection


def _relative_source_hash(root: Path, path: Path) -> str:
    payload = _read_json(path, code=INTEGRITY_FAILURE)
    return stable_hash(payload or {})


def inspect_materialization_preview(preview: Any) -> dict[str, Any]:
    """Validate a Preview without reading or repairing any artifact."""

    result: dict[str, Any] = {
        "present": isinstance(preview, Mapping),
        "valid": False,
        "ready": False,
        "reason_code": None,
        "mismatched_fields": [],
    }
    if not isinstance(preview, Mapping):
        result.update({"reason_code": "EXECUTABLE_MATERIALIZATION_PREVIEW_REQUIRED"})
        return result
    if str(preview.get("schema_version") or "") != EXECUTABLE_MATERIALIZATION_PREVIEW_SCHEMA_VERSION:
        result.update({"reason_code": INTEGRITY_FAILURE})
        return result
    try:
        PerformanceBlindGuard.assert_blind(preview)
    except PerformanceLeakError:
        result.update({"reason_code": "OUTCOME_FIELD_BLOCKED"})
        return result
    expected_hash = stable_hash({key: value for key, value in preview.items() if key != "preview_hash"})
    if str(preview.get("preview_hash") or "") != expected_hash:
        result.update({"reason_code": INTEGRITY_FAILURE})
        return result
    result["valid"] = True
    result["ready"] = str(preview.get("status") or "") == EXECUTABLE_MATERIALIZATION_PREVIEW_READY
    if not result["ready"]:
        result["reason_code"] = EXECUTABLE_CONTRACT_INVALID
    return result


def _contract_payload_and_hash(contract: Any) -> tuple[Mapping[str, Any] | None, str | None]:
    if isinstance(contract, DurableFrozenCandidateContractV1):
        payload = contract.to_dict()
        return payload, str(contract.content_hash or "") or None
    if isinstance(contract, Mapping):
        payload = dict(contract)
        return payload, str(payload.get("content_hash") or "") or None
    return None, None


def inspect_materialization_confirmation(
    receipt: Any,
    *,
    preview: Mapping[str, Any] | None = None,
    contract: Any | None = None,
    objective_id: str | None = None,
    proposal_id: str | None = None,
) -> dict[str, Any]:
    """Validate human confirmation evidence and its immutable identity bindings.

    A receipt can be valid confirmation evidence while the Contract is still
    absent.  It is executable authority only when ``identity_match`` is true,
    which requires a matching Contract as well as a matching Preview.
    """

    result: dict[str, Any] = {
        "present": isinstance(receipt, Mapping),
        "valid": False,
        "identity_match": False,
        "preview_identity_match": False,
        "contract_identity_match": None,
        "reason_code": None,
        "missing_fields": [],
        "mismatched_fields": [],
        "receipt_hash": None,
    }
    if not isinstance(receipt, Mapping):
        result["reason_code"] = "EXECUTABLE_MATERIALIZATION_CONFIRMATION_MISSING"
        return result
    result["receipt_hash"] = receipt.get("receipt_hash")
    missing = [key for key in MATERIALIZATION_CONFIRMATION_REQUIRED_FIELDS if receipt.get(key) in (None, "")]
    result["missing_fields"] = missing
    if missing:
        result["reason_code"] = INTEGRITY_FAILURE
        return result
    if str(receipt.get("schema_version") or "") != EXECUTABLE_MATERIALIZATION_CONFIRMATION_SCHEMA_VERSION:
        result["reason_code"] = INTEGRITY_FAILURE
        return result
    if str(receipt.get("bridge_schema_version") or "") != EXECUTABLE_MATERIALIZATION_SCHEMA_VERSION:
        result["reason_code"] = INTEGRITY_FAILURE
        return result
    try:
        PerformanceBlindGuard.assert_blind(receipt)
    except PerformanceLeakError:
        result["reason_code"] = "OUTCOME_FIELD_BLOCKED"
        return result
    expected_hash = stable_hash({key: value for key, value in receipt.items() if key != "receipt_hash"})
    if str(receipt.get("receipt_hash") or "") != expected_hash:
        result["reason_code"] = INTEGRITY_FAILURE
        return result
    if receipt.get("automatic_structural_preflight") not in (None, False):
        result["reason_code"] = INTEGRITY_FAILURE
        return result
    if receipt.get("automatic_trial_started") not in (None, False):
        result["reason_code"] = INTEGRITY_FAILURE
        return result
    if receipt.get("ai_called") not in (None, False) or receipt.get("budget_consumed") not in (None, False):
        result["reason_code"] = INTEGRITY_FAILURE
        return result
    if str(receipt.get("next_action") or "") != RUN_STRUCTURAL_PREFLIGHT or receipt.get("structural_preflight_ready") is not True:
        result["reason_code"] = INTEGRITY_FAILURE
        return result
    if objective_id not in (None, "") and str(receipt.get("objective_id")) != str(objective_id):
        result["reason_code"] = CANONICAL_CANDIDATE_IDENTITY_CONFLICT
        result["mismatched_fields"] = ["objective_id"]
        return result
    if proposal_id not in (None, "") and str(receipt.get("proposal_id")) != str(proposal_id):
        result["reason_code"] = CANONICAL_CANDIDATE_IDENTITY_CONFLICT
        result["mismatched_fields"] = ["proposal_id"]
        return result

    preview_check = inspect_materialization_preview(preview)
    if preview is None or not preview_check["valid"]:
        result["reason_code"] = preview_check.get("reason_code") or "EXECUTABLE_MATERIALIZATION_PREVIEW_REQUIRED"
        return result
    preview_pairs = {
        "objective_id": "objective_id",
        "proposal_id": "proposal_id",
        "preview_id": "preview_id",
        "preview_hash": "preview_hash",
        "candidate_id": "candidate_id",
        "candidate_hash": "candidate_hash",
        "durable_contract_hash": "durable_contract_hash",
        "ai_design_id": "ai_design_id",
        "ai_design_hash": "ai_design_hash",
        "ai_design_approval_hash": "ai_design_approval_hash",
        "source_context_id": "source_context_id",
        "source_context_hash": "source_context_hash",
    }
    mismatched = [
        receipt_key
        for receipt_key, preview_key in preview_pairs.items()
        if str(receipt.get(receipt_key) or "") != str(preview.get(preview_key) or "")
    ]
    if mismatched:
        result["reason_code"] = CANONICAL_CANDIDATE_IDENTITY_CONFLICT
        result["mismatched_fields"] = mismatched
        return result
    result["preview_identity_match"] = True
    result["valid"] = True
    if contract is None:
        return result
    contract_payload, contract_hash = _contract_payload_and_hash(contract)
    if contract_payload is None or not contract_hash:
        result["reason_code"] = EXECUTABLE_CONTRACT_INVALID
        result["valid"] = False
        return result
    contract_pairs = {
        "candidate_id": "candidate_id",
        "candidate_hash": "candidate_hash",
    }
    contract_mismatched = [
        key
        for key in contract_pairs
        if str(receipt.get(key) or "") != str(contract_payload.get(key) or "")
    ]
    if str(receipt.get("durable_contract_hash") or "") != contract_hash:
        contract_mismatched.append("durable_contract_hash")
    if str(preview.get("durable_contract_hash") or "") != contract_hash:
        contract_mismatched.append("preview.durable_contract_hash")
    if contract_mismatched:
        result["reason_code"] = CANONICAL_CANDIDATE_IDENTITY_CONFLICT
        result["mismatched_fields"] = sorted(set(contract_mismatched))
        result["contract_identity_match"] = False
        result["valid"] = False
        return result
    result["contract_identity_match"] = True
    result["identity_match"] = True
    return result


class CandidateExecutableMaterializationManagerV1:
    """Materialize one governance-frozen Candidate into the existing durable store."""

    _mutex = threading.RLock()

    def __init__(self, root: str | Path, *, clock: Callable[[], str] = now_timestamp, crash_at: str | None = None) -> None:
        self.root = Path(root).resolve()
        self.proposal_root = self.root / "reports" / "research_candidates" / "proposals"
        self.design_root = self.root / "reports" / "research_evolution" / "ai_design"
        self.clock = clock
        self.crash_at = str(crash_at or "")
        self.ai_design_approval = AIDesignApprovalServiceV1(self.root)

    def _inject_crash(self, *points: str) -> None:
        if self.crash_at and self.crash_at in points:
            raise RuntimeError(f"SYNTHETIC_CRASH_INJECTED:{self.crash_at}")

    def _objective_path(self, objective_id: str) -> Path:
        return self.root / "data" / "research" / "research_factory" / "objectives" / f"{_safe_id(objective_id, kind='objective_id')}.json"

    def _lineage_path(self, objective_id: str) -> Path:
        return self.root / "data" / "research" / "research_factory" / "lineage" / f"{_safe_id(objective_id, kind='objective_id')}.json"

    def _registry_path(self, objective_id: str) -> Path:
        return self.root / "data" / "research" / "research_factory" / "candidates" / _safe_id(objective_id, kind="objective_id") / "CANDIDATE_REGISTRY.json"

    def _proposal_path(self, proposal_id: str) -> Path:
        proposal_id = _safe_id(proposal_id, kind="proposal_id")
        matches: list[Path] = []
        if self.proposal_root.exists():
            for path in sorted(self.proposal_root.rglob(CANDIDATE_PROPOSAL_FILENAME), key=lambda item: item.as_posix()):
                if not path.is_file() or not path.resolve().is_relative_to(self.root):
                    continue
                payload = _read_json(path, code=INTEGRITY_FAILURE)
                if payload is not None and str(payload.get("proposal_id") or "") == proposal_id:
                    matches.append(path.resolve())
        if not matches:
            raise CandidateExecutableMaterializationError("CANDIDATE_PROPOSAL_NOT_FOUND", "未找到请求的 Candidate Proposal", status_code=404)
        if len(matches) > 1:
            raise CandidateExecutableMaterializationError("CANDIDATE_PROPOSAL_IDENTITY_CONFLICT", "同一 Candidate Proposal 存在多个 canonical 文件", status_code=503)
        return matches[0]

    def _proposal_dir(self, proposal_id: str) -> Path:
        return self._proposal_path(proposal_id).parent

    def objective_id_for_proposal(self, proposal_id: str) -> str:
        """Resolve a proposal's objective without changing any artifact."""
        path = self._proposal_path(proposal_id)
        payload = _read_json(path, code=INTEGRITY_FAILURE) or {}
        objective_id = str(payload.get("objective_id") or path.parent.name)
        return _safe_id(objective_id, kind="objective_id")

    def _candidate_registry_entry(self, objective_id: str, candidate_id: str, candidate_hash: str) -> tuple[Path, Mapping[str, Any]]:
        path = self._registry_path(objective_id)
        raw = _read_json(path, code="CANDIDATE_REGISTRY_NOT_FOUND")
        if raw is None:
            raise CandidateExecutableMaterializationError("CANDIDATE_REGISTRY_NOT_FOUND", "Candidate Governance Freeze 缺少 Candidate Registry", status_code=409)
        if str(raw.get("schema_version") or "") != CANDIDATE_REGISTRY_SCHEMA_VERSION:
            raise CandidateExecutableMaterializationError(INTEGRITY_FAILURE, "Candidate Registry schema 无效", status_code=503)
        if str(raw.get("objective_id") or "") != objective_id:
            raise CandidateExecutableMaterializationError(CANONICAL_CANDIDATE_IDENTITY_CONFLICT, "Candidate Registry 与当前 Objective 不一致", status_code=409)
        entries = raw.get("candidates")
        if not isinstance(entries, list):
            raise CandidateExecutableMaterializationError(INTEGRITY_FAILURE, "Candidate Registry 格式无效", status_code=503)
        if str(raw.get("registry_hash") or "") != stable_hash(entries):
            raise CandidateExecutableMaterializationError(INTEGRITY_FAILURE, "Candidate Registry 哈希校验失败", status_code=503)
        try:
            PerformanceBlindGuard.assert_blind(raw)
        except PerformanceLeakError as exc:
            raise CandidateExecutableMaterializationError("OUTCOME_FIELD_BLOCKED", "Candidate Registry 包含被禁止的结果字段", status_code=503) from exc
        matches = [item for item in entries if isinstance(item, Mapping) and str(item.get("candidate_id") or "") == candidate_id]
        if not matches:
            raise CandidateExecutableMaterializationError("CANDIDATE_REGISTRY_ENTRY_MISSING", "Candidate Registry 缺少当前冻结 Candidate", status_code=409)
        matching_hashes = {str(item.get("candidate_hash") or "") for item in matches}
        if len(matching_hashes) > 1:
            raise CandidateExecutableMaterializationError(
                CANONICAL_CANDIDATE_IDENTITY_CONFLICT,
                "Candidate Registry 中同一 Candidate ID 对应多个 Candidate hash",
                status_code=409,
                details={"candidate_id": candidate_id, "registry_hashes": sorted(matching_hashes)},
            )
        entry = matches[-1]
        if str(entry.get("candidate_hash") or "") != candidate_hash:
            raise CandidateExecutableMaterializationError(
                CANONICAL_CANDIDATE_IDENTITY_CONFLICT,
                "Candidate Registry 与 Freeze Receipt 的 Candidate hash 不一致",
                status_code=409,
                details={"candidate_id": candidate_id, "registry_hash": entry.get("candidate_hash"), "candidate_hash": candidate_hash},
            )
        if entry.get("entry_hash") not in (None, "") and str(entry.get("entry_hash")) != stable_hash({key: value for key, value in entry.items() if key != "entry_hash"}):
            raise CandidateExecutableMaterializationError(INTEGRITY_FAILURE, "Candidate Registry entry 哈希校验失败", status_code=503)
        return path, entry

    @staticmethod
    def _validate_proposal_identity(proposal: Mapping[str, Any], proposal_id: str) -> None:
        if str(proposal.get("schema_version") or "") != CANDIDATE_PROPOSAL_SCHEMA_VERSION:
            return
        expected_hash = stable_hash(CandidateGenerationManagerV1._identity(proposal))
        if str(proposal.get("proposal_hash") or "") != expected_hash:
            raise CandidateExecutableMaterializationError(INTEGRITY_FAILURE, "Candidate Proposal 哈希校验失败", status_code=503)
        expected_id = f"CANDIDATE_PROPOSAL_{expected_hash[:24].upper()}"
        if str(proposal.get("proposal_id") or proposal_id) != expected_id:
            raise CandidateExecutableMaterializationError(INTEGRITY_FAILURE, "Candidate Proposal 编号校验失败", status_code=503)

    def _validate_safe_runtime_context(self, objective_id: str, proposal: Mapping[str, Any]) -> None:
        stored_hash = str(proposal.get("source_context_hash") or "")
        if not stored_hash:
            return
        from .safe_runtime_context import SafeRuntimeContextBuilderV1, SafeRuntimeContextError

        try:
            current = SafeRuntimeContextBuilderV1(self.root).build(objective_id, purpose="CANDIDATE_PROPOSAL")
        except SafeRuntimeContextError as exc:
            raise CandidateExecutableMaterializationError(
                exc.code,
                "安全运行时上下文不可用，执行物化已阻断",
                status_code=exc.status_code,
                details=exc.details,
            ) from exc
        if stored_hash == current.context_hash and str(proposal.get("input_context_hash") or stored_hash) == current.context_hash:
            return
        stored_budget_status = str(proposal.get("source_budget_authority_status") or "MISSING")
        current_budget = current.get("budget") if isinstance(current.get("budget"), Mapping) else {}
        empty_budget_established = (
            stored_budget_status == "MISSING"
            and str(current_budget.get("authority_status") or "") == "UNIQUE_CANONICAL"
            and not current_budget.get("active_reservations")
            and not current_budget.get("used")
            and not current_budget.get("reserved")
        )
        if empty_budget_established:
            return
        raise CandidateExecutableMaterializationError(
            "STALE_RUNTIME_CONTEXT",
            "候选 Proposal 绑定的安全运行时上下文已过期，执行物化已阻断",
            status_code=409,
            details={"source_context_hash": stored_hash, "current_context_hash": current.context_hash},
        )

    def _approval_context(self, objective_id: str) -> tuple[Path, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
        evaluation = self.ai_design_approval.evaluate(objective_id)
        if str(evaluation.get("approval_status") or "") != AI_APPROVED or evaluation.get("candidate_generation_allowed") is not True:
            reason = str(evaluation.get("reason_code") or "AI_DESIGN_APPROVAL_REQUIRED")
            raise CandidateExecutableMaterializationError(reason, "当前 AI 设计没有有效人工批准，执行合同物化已阻断", status_code=409, details={"approval": evaluation})
        receipt = evaluation.get("receipt") if isinstance(evaluation.get("receipt"), Mapping) else None
        receipt_path_text = evaluation.get("receipt_path")
        if receipt is None or not receipt_path_text:
            raise CandidateExecutableMaterializationError("AI_DESIGN_APPROVAL_INTEGRITY_FAILURE", "AI 设计批准回执不完整，执行合同物化已阻断", status_code=503)
        receipt_path = (self.root / str(receipt_path_text)).resolve()
        if not receipt_path.is_relative_to(self.root):
            raise CandidateExecutableMaterializationError("AI_DESIGN_APPROVAL_INTEGRITY_FAILURE", "AI 设计批准回执路径不受信任", status_code=503)
        design_path = self.design_root / objective_id / "AI_RESEARCH_DESIGN_PROPOSAL.json"
        design = _read_json(design_path, code="AI_DESIGN_NOT_FOUND")
        if design is None:
            raise CandidateExecutableMaterializationError("AI_DESIGN_NOT_FOUND", "当前 Objective 没有 AI 研究设计", status_code=404)
        return design_path, design, receipt_path, receipt

    def _required_mapping(
        self,
        sources: tuple[Mapping[str, Any] | None, ...],
        keys: tuple[str, ...],
        missing_name: str,
        missing: list[str],
    ) -> Mapping[str, Any] | None:
        for source in sources:
            if not isinstance(source, Mapping):
                continue
            for key in keys:
                value = source.get(key)
                if isinstance(value, Mapping) and value:
                    return value
        missing.append(missing_name)
        return None

    def _required_value(
        self,
        sources: tuple[Mapping[str, Any] | None, ...],
        keys: tuple[str, ...],
        missing_name: str,
        missing: list[str],
    ) -> str:
        for source in sources:
            if not isinstance(source, Mapping):
                continue
            for key in keys:
                value = source.get(key)
                if value not in (None, "") and not isinstance(value, Mapping):
                    return str(value)
        missing.append(missing_name)
        return ""

    def _load_context(self, objective_id: str, proposal_id: str) -> _MaterializationContext:
        objective_id = _safe_id(objective_id, kind="objective_id")
        proposal_path = self._proposal_path(proposal_id)
        proposal = _read_json(proposal_path, code="CANDIDATE_PROPOSAL_UNREADABLE")
        if proposal is None or str(proposal.get("objective_id") or proposal_path.parent.name) != objective_id:
            raise CandidateExecutableMaterializationError("CANDIDATE_PROPOSAL_OBJECTIVE_MISMATCH", "Candidate Proposal 与当前 Objective 不匹配", status_code=409)
        self._validate_proposal_identity(proposal, proposal_id)

        receipt_path = proposal_path.parent / CANDIDATE_FREEZE_RECEIPT_FILENAME
        receipt = _read_json(receipt_path, code="CANDIDATE_FREEZE_RECEIPT_NOT_FOUND")
        if receipt is None:
            raise CandidateExecutableMaterializationError("CANDIDATE_GOVERNANCE_FREEZE_REQUIRED", "必须先完成 Candidate Governance Freeze", status_code=409)
        candidate_id = str(receipt.get("candidate_id") or "")
        candidate_hash = str(receipt.get("candidate_hash") or "")
        missing: list[str] = []
        if not candidate_id:
            missing.append("candidate_id")
        if not candidate_hash:
            missing.append("candidate_hash")
        proposal_hash = str(proposal.get("proposal_hash") or "")
        if not proposal_hash:
            missing.append("source_candidate_proposal_hash")
        receipt_hash = str(receipt.get("receipt_hash") or "")
        if not receipt_hash:
            missing.append("freeze_receipt_hash")
        elif receipt_hash != stable_hash({key: value for key, value in receipt.items() if key != "receipt_hash"}):
            raise CandidateExecutableMaterializationError(INTEGRITY_FAILURE, "Candidate Freeze Receipt 哈希校验失败", status_code=503)
        if str(receipt.get("proposal_id") or proposal_id) != proposal_id or str(receipt.get("proposal_hash") or proposal_hash) != proposal_hash:
            raise CandidateExecutableMaterializationError(CANONICAL_CANDIDATE_IDENTITY_CONFLICT, "Candidate Freeze Receipt 与 Proposal 身份不一致", status_code=409)
        if missing:
            raise CandidateExecutableMaterializationError(
                EXECUTABLE_MATERIALIZATION_INCOMPLETE,
                "治理冻结来源缺少执行合同身份字段，系统保持阻断",
                status_code=409,
                details={"missing_required_fields": sorted(set(missing)), "safe_to_advance": False},
            )
        registry_path, registry_entry = self._candidate_registry_entry(objective_id, candidate_id, candidate_hash)
        objective_path = self._objective_path(objective_id)
        objective = _read_json(objective_path, code="OBJECTIVE_NOT_FOUND")
        if objective is None or str(objective.get("objective_id") or "") != objective_id:
            raise CandidateExecutableMaterializationError("OBJECTIVE_SOURCE_MISMATCH", "Objective 身份与请求不一致", status_code=503)
        lineage_path = self._lineage_path(objective_id)
        lineage = _read_json(lineage_path, code="OBJECTIVE_LINEAGE_NOT_FOUND", required=False)
        if lineage is None:
            lineage_value = _mapping(proposal.get("lineage")) or _mapping(objective.get("lineage"))
            if lineage_value is None:
                missing.append("objective_lineage")
                lineage = {}
                lineage_path = None
            else:
                lineage = lineage_value
                lineage_path = None
        elif str(lineage.get("objective_id") or objective_id) != objective_id:
            raise CandidateExecutableMaterializationError("OBJECTIVE_LINEAGE_MISMATCH", "Objective lineage 与当前目标不匹配", status_code=409)

        design_path, design, approval_path, approval_receipt = self._approval_context(objective_id)
        approval = {"approval_id": approval_receipt.get("approval_id"), "approval_status": approval_receipt.get("decision"), "receipt_hash": approval_receipt.get("receipt_hash")}
        if str(design.get("design_hash") or "") == "":
            missing.append("ai_design_hash")
        if not approval.get("receipt_hash"):
            missing.append("ai_design_approval_hash")

        full_contract = _contract_mapping(proposal) or _contract_mapping(registry_entry)
        sources = (proposal, registry_entry, objective, full_contract)
        batch_id = self._required_value(sources, ("batch_id", "canonical_batch_id", "batch_ref"), "batch_id", missing)
        source_provenance = self._required_mapping(sources, ("source_provenance", "provenance"), "source_provenance", missing)
        if source_provenance is None:
            source_provenance = {}
        if not batch_id and source_provenance.get("batch_id") not in (None, ""):
            batch_id = str(source_provenance["batch_id"])
            if "batch_id" in missing:
                missing.remove("batch_id")
        factor_event = self._required_mapping(sources, ("factor_event_registry_identities", "factor_event_registry_identity"), "factor_event_registry_identities", missing)
        if factor_event is None:
            factor_registry = _first_mapping(proposal, {"factor_registry_identity", "factor_registry"})
            event_registry = _first_mapping(proposal, {"event_registry_identity", "event_registry"})
            if factor_registry or event_registry:
                factor_event = {"factor_registry": dict(factor_registry or {}), "event_registry": dict(event_registry or {})}
            else:
                factor_event = {}
        research_period = self._required_mapping(sources, ("research_period_identity", "research_period"), "research_period_identity", missing)
        if research_period is None:
            research_period = {}
        policy = self._required_mapping(sources, ("policy_identity", "validation_policy_identity", "validation_policy"), "policy_identity", missing)
        if policy is None:
            policy = {}
        full_record = _record_mapping(proposal) or _record_mapping(registry_entry)
        hypothesis = _hypothesis_mapping(proposal) or _hypothesis_mapping(registry_entry)
        if full_contract is None and full_record is None:
            missing.extend(["full_semantic_record", "hypothesis"])
        elif full_contract is None:
            if hypothesis is None:
                missing.append("hypothesis")
        if not factor_event:
            missing.append("factor_event_registry_identities")
        if not research_period:
            missing.append("research_period_identity")
        if not policy:
            missing.append("policy_identity")
        if not source_provenance:
            missing.append("source_provenance")

        if batch_id and source_provenance.get("batch_id") not in (None, "", batch_id):
            raise CandidateExecutableMaterializationError(CANONICAL_CANDIDATE_IDENTITY_CONFLICT, "source_provenance 的 batch_id 与冻结来源不一致", status_code=409)
        if batch_id and source_provenance.get("objective_id") not in (None, "", objective_id):
            raise CandidateExecutableMaterializationError(CANONICAL_CANDIDATE_IDENTITY_CONFLICT, "source_provenance 的 objective_id 与当前目标不一致", status_code=409)
        if policy.get("objective_id") not in (None, "", objective_id):
            raise CandidateExecutableMaterializationError(CANONICAL_CANDIDATE_IDENTITY_CONFLICT, "policy_identity 与当前 Objective 不一致", status_code=409)

        if missing:
            unique_missing = sorted(set(missing))
            raise CandidateExecutableMaterializationError(
                EXECUTABLE_MATERIALIZATION_INCOMPLETE,
                "冻结候选缺少生成 DurableFrozenCandidateContractV1 所需字段，系统保持阻断",
                status_code=409,
                details={"missing_required_fields": unique_missing, "safe_to_advance": False},
            )

        try:
            PerformanceBlindGuard.assert_blind({"proposal": proposal, "registry_entry": registry_entry, "objective": objective, "design": design, "approval": approval_receipt, "lineage": lineage, "record": full_record, "hypothesis": hypothesis})
        except PerformanceLeakError as exc:
            raise CandidateExecutableMaterializationError("OUTCOME_FIELD_BLOCKED", "执行合同来源包含被禁止的结果字段", status_code=503) from exc

        source_bindings = {
            "proposal_document_hash": _relative_source_hash(self.root, proposal_path),
            "freeze_receipt_document_hash": _relative_source_hash(self.root, receipt_path),
            "candidate_registry_document_hash": _relative_source_hash(self.root, registry_path),
            "candidate_registry_entry_hash": stable_hash(registry_entry),
            "objective_document_hash": _relative_source_hash(self.root, objective_path),
            "lineage_document_hash": stable_hash(lineage) if lineage_path is None else _relative_source_hash(self.root, lineage_path),
            "ai_design_document_hash": _relative_source_hash(self.root, design_path),
            "ai_design_approval_document_hash": _relative_source_hash(self.root, approval_path),
            "source_context_id": str(design.get("source_context_id") or ""),
            "source_context_hash": str(design.get("source_context_hash") or design.get("input_context_hash") or ""),
        }
        return _MaterializationContext(
            objective_id=objective_id,
            proposal_path=proposal_path,
            proposal=proposal,
            receipt_path=receipt_path,
            receipt=receipt,
            registry_path=registry_path,
            registry_entry=registry_entry,
            objective_path=objective_path,
            objective=objective,
            lineage_path=lineage_path,
            lineage=lineage,
            design_path=design_path,
            design=design,
            approval_path=approval_path,
            approval_receipt=approval_receipt,
            approval=approval,
            candidate_id=candidate_id,
            candidate_hash=candidate_hash,
            batch_id=batch_id,
            factor_event_registry_identities=factor_event,
            research_period_identity=research_period,
            policy_identity=policy,
            source_provenance=source_provenance,
            full_semantic_record=full_record,
            hypothesis=hypothesis,
            full_contract_payload=full_contract,
            source_bindings=source_bindings,
        )

    def _contract_from_context(self, context: _MaterializationContext) -> tuple[DurableFrozenCandidateContractV1, dict[str, Any]]:
        try:
            if context.full_contract_payload is not None:
                contract = DurableFrozenCandidateContractV1.from_dict(context.full_contract_payload)
            else:
                if context.full_semantic_record is None or context.hypothesis is None:
                    raise CandidateExecutableMaterializationError(
                        EXECUTABLE_MATERIALIZATION_INCOMPLETE,
                        "冻结候选缺少完整语义记录或权威 hypothesis",
                        details={"missing_required_fields": ["full_semantic_record", "hypothesis"], "safe_to_advance": False},
                    )
                record = SemanticCandidateRecord(
                    candidate=StrategyCandidateSpec.from_dict(context.full_semantic_record["candidate"]),
                    parent_candidate_id=str(context.full_semantic_record["parent_candidate_id"]),
                    previous_preregistration_hash=str(context.full_semantic_record["previous_preregistration_hash"]),
                    semantic_change_reason=str(context.full_semantic_record["semantic_change_reason"]),
                    signal_predicate=SignalPredicateSpec.from_dict(context.full_semantic_record["signal_predicate"]),
                    exit_predicate=ExitPredicateSpec.from_dict(context.full_semantic_record["exit_predicate"]),
                    semantic_status=str(context.full_semantic_record["semantic_status"]),
                    phase4_eligible=bool(context.full_semantic_record["phase4_eligible"]),
                    semantic_fingerprint=str(context.full_semantic_record["semantic_fingerprint"]),
                    preregistration_hash=str(context.full_semantic_record["preregistration_hash"]),
                    created_at=str(context.full_semantic_record["created_at"]),
                )
                contract = DurableFrozenCandidateContractV1.from_semantic_record(
                    record,
                    context.hypothesis,
                    factor_event_registry_identities=context.factor_event_registry_identities,
                    research_period_identity=context.research_period_identity,
                    policy_identity=context.policy_identity,
                    source_provenance=context.source_provenance,
                    created_frozen_timestamp=self.clock(),
                )
                contract = DurableFrozenCandidateContractV1.from_dict(contract.to_dict())
        except CandidateExecutableMaterializationError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            missing = self._missing_contract_fields(context.full_contract_payload)
            if missing:
                raise CandidateExecutableMaterializationError(
                    EXECUTABLE_MATERIALIZATION_INCOMPLETE,
                    "冻结候选的 Durable Contract 字段不完整，系统保持阻断",
                    status_code=409,
                    details={"missing_required_fields": missing, "safe_to_advance": False},
                ) from exc
            raise CandidateExecutableMaterializationError(EXECUTABLE_CONTRACT_INVALID, "DurableFrozenCandidateContractV1 校验失败", status_code=409, details={"validation_error": str(exc), "safe_to_advance": False}) from exc

        if contract.candidate_id != context.candidate_id or contract.candidate_hash != context.candidate_hash:
            raise CandidateExecutableMaterializationError(
                CANONICAL_CANDIDATE_IDENTITY_CONFLICT,
                "Durable Contract 与 Governance Freeze 的 Candidate 身份不一致",
                status_code=409,
                details={"governance": {"candidate_id": context.candidate_id, "candidate_hash": context.candidate_hash}, "durable": {"candidate_id": contract.candidate_id, "candidate_hash": contract.candidate_hash}},
            )
        contract_objective = contract.policy_identity.get("objective_id") or contract.source_provenance.get("objective_id")
        if contract_objective not in (None, "", context.objective_id):
            raise CandidateExecutableMaterializationError(CANONICAL_CANDIDATE_IDENTITY_CONFLICT, "Durable Contract 未绑定当前 Objective", status_code=409)
        try:
            contract.provider_candidate_payload()
        except Exception as exc:
            return contract, {"status": "FAIL", "from_dict": "PASS", "provider_candidate_payload": "FAIL", "error": str(exc)}
        return contract, {"status": "PASS", "from_dict": "PASS", "provider_candidate_payload": "PASS", "error": None}

    @staticmethod
    def _missing_contract_fields(payload: Mapping[str, Any] | None) -> list[str]:
        if payload is None:
            return list(_REQUIRED_CONTRACT_FIELDS)
        return sorted(set(_REQUIRED_CONTRACT_FIELDS) - set(payload))

    def _assert_semantic_equivalence(self, context: _MaterializationContext, contract: DurableFrozenCandidateContractV1) -> str:
        executable = _semantic_projection_from_contract(contract)
        governance = _governance_semantic_projection(context.proposal, context.registry_entry)
        mismatches = sorted(key for key, value in governance.items() if key in executable and value != executable[key])
        if mismatches:
            raise CandidateExecutableMaterializationError(
                CANDIDATE_SEMANTIC_DRIFT,
                "Candidate Governance Contract 与 Durable Contract 的研究语义不一致，物化已阻断",
                status_code=409,
                details={"mismatched_fields": mismatches, "safe_to_advance": False},
            )
        return stable_hash(executable)

    def _preview_path(self, context: _MaterializationContext) -> Path:
        return context.proposal_path.parent / EXECUTABLE_MATERIALIZATION_PREVIEW_FILENAME

    def _confirmation_path(self, context: _MaterializationContext) -> Path:
        return context.proposal_path.parent / EXECUTABLE_MATERIALIZATION_CONFIRMATION_FILENAME

    def _validate_preview_integrity(self, preview: Mapping[str, Any]) -> None:
        check = inspect_materialization_preview(preview)
        if not check["valid"]:
            if check.get("reason_code") == "OUTCOME_FIELD_BLOCKED":
                raise CandidateExecutableMaterializationError("OUTCOME_FIELD_BLOCKED", "执行合同预览包含被禁止的结果字段", status_code=503)
            raise CandidateExecutableMaterializationError(INTEGRITY_FAILURE, "Executable Materialization Preview 哈希校验失败", status_code=503)

    def _assert_preview_current(self, context: _MaterializationContext, preview: Mapping[str, Any]) -> None:
        expected = preview.get("source_bindings") if isinstance(preview.get("source_bindings"), Mapping) else {}
        if dict(expected) != dict(context.source_bindings):
            raise CandidateExecutableMaterializationError(
                STALE_EXECUTABLE_MATERIALIZATION_PREVIEW,
                "冻结候选或其批准来源已变化，请重新建立治理事实后再物化",
                status_code=409,
                details={"safe_to_advance": False, "expected_source_bindings": dict(expected), "current_source_bindings": dict(context.source_bindings)},
            )
        if (
            str(preview.get("objective_id") or "") != context.objective_id
            or str(preview.get("proposal_id") or "") != str(context.proposal.get("proposal_id") or "")
            or str(preview.get("candidate_id") or "") != context.candidate_id
            or str(preview.get("candidate_hash") or "") != context.candidate_hash
            or str(preview.get("source_candidate_proposal_hash") or "") != str(context.proposal.get("proposal_hash") or "")
            or str(preview.get("source_context_id") or "") != str(context.design.get("source_context_id") or "")
            or str(preview.get("source_context_hash") or "") != str(context.design.get("source_context_hash") or context.design.get("input_context_hash") or "")
        ):
            raise CandidateExecutableMaterializationError(STALE_EXECUTABLE_MATERIALIZATION_PREVIEW, "Preview 与当前冻结 Candidate 身份不一致", status_code=409, details={"safe_to_advance": False})

    def _build_preview(self, context: _MaterializationContext, contract: DurableFrozenCandidateContractV1, provider: Mapping[str, Any], semantic_hash: str) -> dict[str, Any]:
        preview_id = f"EXECUTABLE_MATERIALIZATION_PREVIEW_{stable_hash({'objective_id': context.objective_id, 'candidate_id': context.candidate_id, 'candidate_hash': context.candidate_hash, 'proposal_hash': context.proposal.get('proposal_hash'), 'freeze_receipt_hash': context.receipt.get('receipt_hash')})[:24].upper()}"
        payload: dict[str, Any] = {
            "schema_version": EXECUTABLE_MATERIALIZATION_PREVIEW_SCHEMA_VERSION,
            "bridge_schema_version": EXECUTABLE_MATERIALIZATION_SCHEMA_VERSION,
            "preview_id": preview_id,
            "objective_id": context.objective_id,
            "proposal_id": context.proposal.get("proposal_id"),
            "candidate_id": context.candidate_id,
            "candidate_hash": context.candidate_hash,
            "source_candidate_proposal_hash": context.proposal.get("proposal_hash"),
            "freeze_receipt_hash": context.receipt.get("receipt_hash"),
            "ai_design_id": context.design.get("design_id"),
            "ai_design_hash": context.design.get("design_hash"),
            "ai_design_approval_hash": context.approval_receipt.get("receipt_hash"),
            "source_context_id": context.design.get("source_context_id"),
            "source_context_hash": context.design.get("source_context_hash") or context.design.get("input_context_hash"),
            "durable_contract_hash": contract.content_hash,
            "semantic_fingerprint": contract.semantic_fingerprint,
            "semantic_equivalence_hash": semantic_hash,
            "testing_family": context.proposal.get("multiple_testing_family_id") or context.objective.get("multiple_testing_family_id"),
            "research_period_identity": dict(context.research_period_identity),
            "provider_readiness": dict(provider),
            "missing_fields": [],
            "warnings": ["治理冻结与执行冻结是两个独立状态", "确认后仅进入 READY_FOR_STRUCTURAL_PREFLIGHT，不自动运行 Structural"],
            "human_confirmation_required": True,
            "source_bindings": dict(context.source_bindings),
            "batch_id": context.batch_id,
            "durable_contract_path": _relative(self.root, self.root / "data/research/research_factory/batches" / context.batch_id / "durable_frozen_candidate_contracts.json"),
            "durable_contract": contract.to_dict(),
            "governance_freeze_state": CANDIDATE_GOVERNANCE_FROZEN,
            "executable_candidate_frozen": False,
            "structural_preflight_ready": False,
            "safe_to_advance": provider.get("status") == "PASS",
            "next_action": HUMAN_CONFIRM_EXECUTABLE_MATERIALIZATION if provider.get("status") == "PASS" else None,
            "created_at": self.clock(),
        }
        try:
            PerformanceBlindGuard.assert_blind(payload)
        except PerformanceLeakError as exc:
            raise CandidateExecutableMaterializationError("OUTCOME_FIELD_BLOCKED", "执行合同预览包含被禁止的结果字段", status_code=503) from exc
        payload["preview_hash"] = stable_hash(payload)
        return payload

    def create_preview(self, objective_id: str, proposal_id: str | None = None) -> dict[str, Any]:
        """Create or return the immutable preview; never write the durable store."""
        with self._mutex:
            if proposal_id is None and str(objective_id).startswith(("CANDIDATE_PROPOSAL_", "PROPOSAL_")):
                proposal_id = str(objective_id)
                objective_id = self.objective_id_for_proposal(proposal_id)
            objective_id = _safe_id(objective_id, kind="objective_id")
            if not proposal_id:
                proposal_candidates = sorted((self.proposal_root / objective_id).glob(CANDIDATE_PROPOSAL_FILENAME)) if (self.proposal_root / objective_id).is_dir() else []
                if len(proposal_candidates) != 1:
                    raise CandidateExecutableMaterializationError("CANDIDATE_PROPOSAL_REQUIRED", "必须明确指定唯一 Candidate Proposal", status_code=409)
                proposal = _read_json(proposal_candidates[0], code="CANDIDATE_PROPOSAL_UNREADABLE")
                proposal_id = str((proposal or {}).get("proposal_id") or "")
            context = self._load_context(objective_id, proposal_id)
            self._validate_safe_runtime_context(objective_id, context.proposal)
            preview_path = self._preview_path(context)
            existing = _read_json(preview_path, code=INTEGRITY_FAILURE, required=False)
            if existing is not None:
                self._validate_preview_integrity(existing)
                self._assert_preview_current(context, existing)
                return {**dict(existing), "idempotent": True}
            contract, provider = self._contract_from_context(context)
            semantic_hash = self._assert_semantic_equivalence(context, contract)
            preview = self._build_preview(context, contract, provider, semantic_hash)
            if provider.get("status") != "PASS":
                preview["next_action"] = None
                preview["safe_to_advance"] = False
                preview["status"] = EXECUTABLE_CONTRACT_INVALID
                preview["preview_hash"] = stable_hash({key: value for key, value in preview.items() if key != "preview_hash"})
            else:
                preview["status"] = EXECUTABLE_MATERIALIZATION_PREVIEW_READY
                preview["preview_hash"] = stable_hash({key: value for key, value in preview.items() if key != "preview_hash"})
            try:
                _create_immutable_json(preview_path, preview)
            except FileExistsError:
                existing = _read_json(preview_path, code=INTEGRITY_FAILURE)
                if existing is None:
                    raise CandidateExecutableMaterializationError(INTEGRITY_FAILURE, "Preview 并发写入结果不可读", status_code=503)
                self._validate_preview_integrity(existing)
                self._assert_preview_current(context, existing)
                return {**dict(existing), "idempotent": True}
            return {**preview, "idempotent": False}

    materialize_preview = create_preview
    create_materialization_preview = create_preview
    preview = create_preview

    def _durable_store_paths(self) -> list[Path]:
        root = self.root / "data/research/research_factory/batches"
        return sorted(root.glob("*/durable_frozen_candidate_contracts.json")) if root.exists() else []

    def _durable_entries(self, objective_id: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for path in self._durable_store_paths():
            raw = _read_json(path, code=INTEGRITY_FAILURE)
            if raw is None or raw.get("schema_version") != FROZEN_CANDIDATE_CONTRACT_REGISTRY_SCHEMA:
                continue
            for item in raw.get("contracts", ()):
                if not isinstance(item, Mapping):
                    continue
                policy = item.get("policy_identity") if isinstance(item.get("policy_identity"), Mapping) else {}
                provenance = item.get("source_provenance") if isinstance(item.get("source_provenance"), Mapping) else {}
                if str(policy.get("objective_id") or provenance.get("objective_id") or "") == objective_id:
                    entries.append({"path": path, "raw": item})
        return entries

    def _state_model(
        self,
        context: _MaterializationContext,
        *,
        state: str,
        next_action: str | None,
        preview: Mapping[str, Any] | None = None,
        confirmation: Mapping[str, Any] | None = None,
        contract: DurableFrozenCandidateContractV1 | None = None,
        safe_to_advance: bool = False,
        reason_code: str | None = None,
        confirmation_valid: bool = False,
        identity_match: bool = False,
        recovery_required: bool = False,
    ) -> dict[str, Any]:
        contract_hash = contract.content_hash if contract is not None else (preview or {}).get("durable_contract_hash")
        model = {
            "schema_version": EXECUTABLE_MATERIALIZATION_SCHEMA_VERSION,
            "objective_id": context.objective_id,
            "proposal_id": context.proposal.get("proposal_id"),
            "candidate_id": context.candidate_id,
            "candidate_hash": context.candidate_hash,
            "governance_freeze": {
                "state": CANDIDATE_GOVERNANCE_FROZEN,
                "receipt_hash": context.receipt.get("receipt_hash"),
                "registry_path": _relative(self.root, context.registry_path),
            },
            "materialization_state": state,
            "required_action": next_action,
            "safe_to_advance": safe_to_advance,
            "structural_preflight_ready": state == READY_FOR_STRUCTURAL_PREFLIGHT,
            "executable_candidate_frozen": state == READY_FOR_STRUCTURAL_PREFLIGHT,
            "durable_contract_hash": contract_hash,
            "semantic_fingerprint": contract.semantic_fingerprint if contract is not None else (preview or {}).get("semantic_fingerprint"),
            "preview": dict(preview) if preview is not None else None,
            "confirmation": dict(confirmation) if confirmation is not None else None,
            "materialization_confirmation_present": confirmation is not None,
            "materialization_confirmation_valid": confirmation_valid,
            "materialization_confirmation_hash": (confirmation or {}).get("receipt_hash"),
            "materialization_contract_present": contract is not None,
            "materialization_identity_match": identity_match,
            "materialization_recovery_required": recovery_required,
            "contract_path": _relative(self.root, self.root / "data/research/research_factory/batches" / context.batch_id / "durable_frozen_candidate_contracts.json"),
            "reason_code": reason_code,
            "outcome_blind": True,
            "performance_data_loaded": False,
            "outcome_fields_available": False,
        }
        return model

    def read_state(self, objective_id: str, proposal_id: str | None = None) -> dict[str, Any]:
        """Read both freeze layers without repairing or creating artifacts."""
        with self._mutex:
            objective_id = _safe_id(objective_id, kind="objective_id")
            try:
                if not proposal_id:
                    candidates = sorted((self.proposal_root / objective_id).glob(CANDIDATE_PROPOSAL_FILENAME)) if (self.proposal_root / objective_id).is_dir() else []
                    if len(candidates) != 1:
                        return {"schema_version": EXECUTABLE_MATERIALIZATION_SCHEMA_VERSION, "objective_id": objective_id, "materialization_state": "NEED_CANDIDATE_PROPOSAL", "required_action": None, "structural_preflight_ready": False, "safe_to_advance": False, "outcome_blind": True, "performance_data_loaded": False, "outcome_fields_available": False}
                    proposal = _read_json(candidates[0], code=INTEGRITY_FAILURE)
                    proposal_id = str((proposal or {}).get("proposal_id") or "")
                context = self._load_context_for_read(objective_id, proposal_id)
            except CandidateExecutableMaterializationError as exc:
                return {"schema_version": EXECUTABLE_MATERIALIZATION_SCHEMA_VERSION, "objective_id": objective_id, "materialization_state": exc.code, "required_action": None, "structural_preflight_ready": False, "safe_to_advance": False, "reason_code": exc.code, "details": dict(exc.details), "outcome_blind": True, "performance_data_loaded": False, "outcome_fields_available": False}
            preview_path = self._preview_path(context)
            preview = _read_json(preview_path, code=INTEGRITY_FAILURE, required=False)
            if preview is not None:
                try:
                    self._validate_preview_integrity(preview)
                    self._assert_preview_current(context, preview)
                except CandidateExecutableMaterializationError as exc:
                    return self._state_model(context, state=exc.code, next_action=None, preview=preview, safe_to_advance=False, reason_code=exc.code)
                if str(preview.get("status") or "") == EXECUTABLE_CONTRACT_INVALID:
                    return self._state_model(context, state=EXECUTABLE_CONTRACT_INVALID, next_action=None, preview=preview, safe_to_advance=False, reason_code=EXECUTABLE_CONTRACT_INVALID)
                if str(preview.get("status") or "") != EXECUTABLE_MATERIALIZATION_PREVIEW_READY:
                    return self._state_model(context, state=INTEGRITY_FAILURE, next_action=None, preview=preview, safe_to_advance=False, reason_code=INTEGRITY_FAILURE)
            confirmation = _read_json(self._confirmation_path(context), code=INTEGRITY_FAILURE, required=False)
            confirmation_check = inspect_materialization_confirmation(
                confirmation,
                preview=preview,
                objective_id=objective_id,
                proposal_id=str(context.proposal.get("proposal_id") or proposal_id),
            )
            entries = self._durable_entries(objective_id)
            matching = [item for item in entries if str(item["raw"].get("candidate_id") or "") == context.candidate_id]
            hashes = sorted({str(item["raw"].get("candidate_hash") or "") for item in matching})
            if len(hashes) > 1 or (hashes and hashes[0] != context.candidate_hash):
                return self._state_model(context, state=CANONICAL_CANDIDATE_IDENTITY_CONFLICT, next_action=None, preview=preview, confirmation=confirmation, safe_to_advance=False, reason_code=CANONICAL_CANDIDATE_IDENTITY_CONFLICT)
            contract: DurableFrozenCandidateContractV1 | None = None
            invalid_contract = False
            for item in matching:
                raw = item["raw"]
                if str(raw.get("candidate_hash") or "") != context.candidate_hash:
                    continue
                try:
                    candidate_contract = DurableFrozenCandidateContractV1.from_dict(raw)
                    candidate_contract.provider_candidate_payload()
                except Exception as exc:
                    del exc
                    invalid_contract = True
                    continue
                if contract is not None and contract.content_hash != candidate_contract.content_hash:
                    return self._state_model(context, state=CANONICAL_CANDIDATE_IDENTITY_CONFLICT, next_action=None, preview=preview, confirmation=confirmation, contract=contract, safe_to_advance=False, reason_code=CANONICAL_CANDIDATE_IDENTITY_CONFLICT)
                contract = candidate_contract
            if invalid_contract:
                return self._state_model(context, state=EXECUTABLE_CONTRACT_INVALID, next_action=None, preview=preview, confirmation=confirmation, contract=contract, safe_to_advance=False, reason_code=EXECUTABLE_CONTRACT_INVALID)
            if confirmation is not None and not confirmation_check["valid"]:
                return self._state_model(
                    context,
                    state=str(confirmation_check.get("reason_code") or INTEGRITY_FAILURE),
                    next_action=None,
                    preview=preview,
                    confirmation=confirmation,
                    contract=contract,
                    safe_to_advance=False,
                    reason_code=str(confirmation_check.get("reason_code") or INTEGRITY_FAILURE),
                    confirmation_valid=False,
                )
            if contract is not None:
                if confirmation is None:
                    return self._state_model(
                        context,
                        state=EXECUTABLE_MATERIALIZATION_CONFIRMATION_MISSING,
                        next_action=HUMAN_CONFIRM_EXECUTABLE_MATERIALIZATION,
                        preview=preview,
                        contract=contract,
                        safe_to_advance=False,
                        reason_code=EXECUTABLE_MATERIALIZATION_CONFIRMATION_MISSING,
                    )
                final_check = inspect_materialization_confirmation(
                    confirmation,
                    preview=preview,
                    contract=contract,
                    objective_id=objective_id,
                    proposal_id=str(context.proposal.get("proposal_id") or proposal_id),
                )
                if not final_check["valid"] or not final_check["identity_match"]:
                    return self._state_model(
                        context,
                        state=str(final_check.get("reason_code") or CANONICAL_CANDIDATE_IDENTITY_CONFLICT),
                        next_action=None,
                        preview=preview,
                        confirmation=confirmation,
                        contract=contract,
                        safe_to_advance=False,
                        reason_code=str(final_check.get("reason_code") or CANONICAL_CANDIDATE_IDENTITY_CONFLICT),
                        confirmation_valid=bool(final_check.get("valid")),
                        identity_match=False,
                    )
                return self._state_model(
                    context,
                    state=READY_FOR_STRUCTURAL_PREFLIGHT,
                    next_action=RUN_STRUCTURAL_PREFLIGHT,
                    preview=preview,
                    confirmation=confirmation,
                    contract=contract,
                    safe_to_advance=True,
                    confirmation_valid=True,
                    identity_match=True,
                )
            if confirmation is not None:
                return self._state_model(
                    context,
                    state=EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED,
                    next_action=RECOVER_EXECUTABLE_MATERIALIZATION,
                    preview=preview,
                    confirmation=confirmation,
                    safe_to_advance=False,
                    reason_code=EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED,
                    confirmation_valid=True,
                    recovery_required=True,
                )
            if preview is not None:
                return self._state_model(context, state=EXECUTABLE_MATERIALIZATION_PREVIEW_READY, next_action=HUMAN_CONFIRM_EXECUTABLE_MATERIALIZATION, preview=preview, safe_to_advance=False)
            return self._state_model(context, state=CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION, next_action=CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW, safe_to_advance=True)

    def _load_context_for_read(self, objective_id: str, proposal_id: str) -> _MaterializationContext:
        """Read-state variant: preserve governance visibility when execution inputs are incomplete."""
        try:
            return self._load_context(objective_id, proposal_id)
        except CandidateExecutableMaterializationError as exc:
            if exc.code not in {
                EXECUTABLE_MATERIALIZATION_INCOMPLETE,
                "AI_DESIGN_NOT_FOUND",
                "AI_DESIGN_APPROVAL_REQUIRED",
            }:
                raise
            proposal_path = self._proposal_path(proposal_id)
            proposal = _read_json(proposal_path, code=INTEGRITY_FAILURE) or {}
            receipt_path = proposal_path.parent / CANDIDATE_FREEZE_RECEIPT_FILENAME
            receipt = _read_json(receipt_path, code=INTEGRITY_FAILURE, required=False) or {}
            candidate_id = str(receipt.get("candidate_id") or proposal.get("candidate_id") or "")
            candidate_hash = str(receipt.get("candidate_hash") or proposal.get("candidate_hash") or "")
            registry_path = self._registry_path(objective_id)
            registry = _read_json(registry_path, code=INTEGRITY_FAILURE, required=False) or {}
            entries = registry.get("candidates") if isinstance(registry.get("candidates"), list) else []
            entry = next((item for item in entries if isinstance(item, Mapping) and str(item.get("candidate_id") or "") == candidate_id), {})
            objective_path = self._objective_path(objective_id)
            objective = _read_json(objective_path, code=INTEGRITY_FAILURE, required=False) or {"objective_id": objective_id}
            lineage_path = self._lineage_path(objective_id)
            lineage = _read_json(lineage_path, code=INTEGRITY_FAILURE, required=False) or _mapping(proposal.get("lineage")) or {}
            design_path = self.design_root / objective_id / "AI_RESEARCH_DESIGN_PROPOSAL.json"
            design = _read_json(design_path, code=INTEGRITY_FAILURE, required=False) or {}
            approval_path = self.design_root / objective_id / "AI_DESIGN_APPROVAL_RECEIPT.json"
            approval = _read_json(approval_path, code=INTEGRITY_FAILURE, required=False) or {}
            return _MaterializationContext(
                objective_id=objective_id, proposal_path=proposal_path, proposal=proposal, receipt_path=receipt_path, receipt=receipt,
                registry_path=registry_path, registry_entry=entry, objective_path=objective_path, objective=objective,
                lineage_path=lineage_path if lineage_path.exists() else None, lineage=lineage,
                design_path=design_path, design=design, approval_path=approval_path, approval_receipt=approval,
                approval={}, candidate_id=candidate_id, candidate_hash=candidate_hash,
                batch_id=str(proposal.get("batch_id") or "READ_ONLY_UNKNOWN_BATCH"),
                factor_event_registry_identities={}, research_period_identity={}, policy_identity={}, source_provenance={},
                full_semantic_record=None, hypothesis=None, full_contract_payload=None, source_bindings={},
            )

    get_state = read_state
    read_for_objective = read_state
    materialization_state = read_state

    def _write_executable_state(self, context: _MaterializationContext) -> None:
        path = context.proposal_path.parent / CANDIDATE_PROPOSAL_STATE_FILENAME
        existing = _read_json(path, code=INTEGRITY_FAILURE, required=False) or {}
        history = list(existing.get("state_history") or [])
        if EXECUTABLE_CANDIDATE_FROZEN not in history:
            history.append(EXECUTABLE_CANDIDATE_FROZEN)
        payload = {
            "schema_version": "candidate-proposal-state-v1",
            "transition_id": f"CANDIDATE_PROPOSAL_TRANSITION_{stable_hash({'proposal_id': context.proposal.get('proposal_id'), 'state': EXECUTABLE_CANDIDATE_FROZEN, 'history': history})[:24].upper()}",
            "objective_id": context.objective_id,
            "proposal_id": context.proposal.get("proposal_id"),
            "proposal_hash": context.proposal.get("proposal_hash"),
            "from_state": str(existing.get("status") or CANDIDATE_GOVERNANCE_FROZEN),
            "to_state": EXECUTABLE_CANDIDATE_FROZEN,
            "status": EXECUTABLE_CANDIDATE_FROZEN,
            "state_history": history,
            "review_ids": list(existing.get("review_ids") or []),
            "requires_human_review": False,
            "next_action": RUN_STRUCTURAL_PREFLIGHT,
            "candidate_created": True,
            "candidate_frozen": True,
            "executable_candidate_frozen": True,
            "structural_preflight_ready": True,
            "structural_preflight_started": False,
            "trial_started": False,
            "ai_called": False,
            "budget_consumed": False,
            "updated_at": self.clock(),
        }
        _atomic_write_json(path, payload)

    def _existing_contract(self, context: _MaterializationContext) -> tuple[DurableFrozenCandidateContractV1 | None, Path | None]:
        entries = self._durable_entries(context.objective_id)
        same_id = [item for item in entries if str(item["raw"].get("candidate_id") or "") == context.candidate_id]
        if any(str(item["raw"].get("candidate_hash") or "") != context.candidate_hash for item in same_id):
            raise CandidateExecutableMaterializationError(CANONICAL_CANDIDATE_IDENTITY_CONFLICT, "同 Candidate ID 已存在不同 Candidate hash", status_code=409)
        valid: list[tuple[DurableFrozenCandidateContractV1, Path]] = []
        for item in same_id:
            if str(item["raw"].get("candidate_hash") or "") != context.candidate_hash:
                continue
            try:
                contract = DurableFrozenCandidateContractV1.from_dict(item["raw"])
                contract.provider_candidate_payload()
            except Exception as exc:
                raise CandidateExecutableMaterializationError(EXECUTABLE_CONTRACT_INVALID, "已存在的 Durable Contract 无法通过完整校验", status_code=409, details={"validation_error": str(exc), "safe_to_advance": False}) from exc
            valid.append((contract, Path(item["path"])))
        if not valid:
            return None, None
        identities = {(item[0].candidate_id, item[0].candidate_hash, item[0].content_hash) for item in valid}
        if len(identities) != 1 or len(valid) != 1:
            raise CandidateExecutableMaterializationError(CANONICAL_CANDIDATE_IDENTITY_CONFLICT, "同一 Candidate 存在多个 Durable Contract，不能自动选择或覆盖", status_code=409)
        return valid[0]

    def _build_confirmation(
        self,
        context: _MaterializationContext,
        preview: Mapping[str, Any],
        contract: DurableFrozenCandidateContractV1,
        *,
        reviewer: str,
        idempotency_key: str,
        target: Path,
    ) -> dict[str, Any]:
        confirmation_id = f"EXECUTABLE_MATERIALIZATION_CONFIRMATION_{stable_hash({'preview_id': preview.get('preview_id'), 'preview_hash': preview.get('preview_hash'), 'candidate_id': context.candidate_id, 'candidate_hash': context.candidate_hash, 'idempotency_key': idempotency_key})[:24].upper()}"
        receipt: dict[str, Any] = {
            "schema_version": EXECUTABLE_MATERIALIZATION_CONFIRMATION_SCHEMA_VERSION,
            "bridge_schema_version": EXECUTABLE_MATERIALIZATION_SCHEMA_VERSION,
            "confirmation_id": confirmation_id,
            "objective_id": context.objective_id,
            "proposal_id": context.proposal.get("proposal_id"),
            "preview_id": preview.get("preview_id"),
            "preview_hash": preview.get("preview_hash"),
            "candidate_id": context.candidate_id,
            "candidate_hash": context.candidate_hash,
            "durable_contract_hash": contract.content_hash,
            "ai_design_id": context.design.get("design_id"),
            "ai_design_hash": context.design.get("design_hash"),
            "ai_design_approval_hash": context.approval_receipt.get("receipt_hash"),
            "source_context_id": context.design.get("source_context_id"),
            "source_context_hash": context.design.get("source_context_hash") or context.design.get("input_context_hash"),
            "reviewer": reviewer,
            "confirmed_at": self.clock(),
            "idempotency_key": idempotency_key,
            "durable_contract_path": _relative(self.root, target),
            "resulting_state": EXECUTABLE_CANDIDATE_FROZEN,
            "next_action": RUN_STRUCTURAL_PREFLIGHT,
            "structural_preflight_ready": True,
            "automatic_structural_preflight": False,
            "automatic_trial_started": False,
            "ai_called": False,
            "budget_consumed": False,
        }
        try:
            PerformanceBlindGuard.assert_blind(receipt)
        except PerformanceLeakError as exc:
            raise CandidateExecutableMaterializationError("OUTCOME_FIELD_BLOCKED", "执行合同确认记录包含被禁止的结果字段", status_code=503) from exc
        receipt["receipt_hash"] = stable_hash(receipt)
        return receipt

    def _check_existing_confirmation(
        self,
        existing: Mapping[str, Any],
        preview: Mapping[str, Any],
        context: _MaterializationContext,
        *,
        contract: DurableFrozenCandidateContractV1 | None = None,
        idempotency_key: str,
        reviewer: str,
    ) -> None:
        check = inspect_materialization_confirmation(
            existing,
            preview=preview,
            contract=contract,
            objective_id=context.objective_id,
            proposal_id=str(context.proposal.get("proposal_id") or ""),
        )
        if not check["valid"]:
            raise CandidateExecutableMaterializationError(str(check.get("reason_code") or INTEGRITY_FAILURE), "已有执行合同确认记录未通过完整性或身份校验", status_code=503, details={"mismatched_fields": check.get("mismatched_fields", []), "missing_fields": check.get("missing_fields", [])})
        if str(existing.get("idempotency_key") or "") != idempotency_key:
            raise CandidateExecutableMaterializationError(MATERIALIZATION_IDEMPOTENCY_CONFLICT, "同一 Preview 已使用另一 idempotency_key", status_code=409)
        if str(existing.get("reviewer") or "") != reviewer:
            raise CandidateExecutableMaterializationError(MATERIALIZATION_IDEMPOTENCY_CONFLICT, "同一 Preview 的人工确认人不能被替换", status_code=409)

    def confirm(self, objective_id: str, proposal_id: str | Mapping[str, Any], payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Persist confirmation evidence first, then materialize one contract."""
        with self._mutex:
            if isinstance(proposal_id, Mapping):
                payload = proposal_id
                proposal_id = str(objective_id)
                objective_id = self.objective_id_for_proposal(proposal_id)
            body = dict(payload or {})
            if body.get("confirmed") is not True:
                raise CandidateExecutableMaterializationError("CONFIRMATION_REQUIRED", "物化执行合同需要明确的第二次人工确认", status_code=400)
            reviewer = _safe_reviewer(body.get("reviewer") or body.get("confirm_reviewer"))
            preview_hash = _safe_token(body.get("preview_hash"), kind="preview_hash")
            idempotency_key = _safe_token(body.get("idempotency_key") or body.get("confirmation_id"), kind="idempotency_key")
            context = self._load_context(objective_id, proposal_id)
            preview = _read_json(self._preview_path(context), code="EXECUTABLE_MATERIALIZATION_PREVIEW_REQUIRED", required=False)
            if preview is None:
                raise CandidateExecutableMaterializationError("EXECUTABLE_MATERIALIZATION_PREVIEW_REQUIRED", "必须先生成 Executable Materialization Preview", status_code=409)
            self._validate_preview_integrity(preview)
            if str(preview.get("preview_hash") or "") != preview_hash:
                raise CandidateExecutableMaterializationError(STALE_EXECUTABLE_MATERIALIZATION_PREVIEW, "确认的 Preview hash 不是当前 immutable Preview", status_code=409, details={"safe_to_advance": False})
            self._assert_preview_current(context, preview)
            if preview.get("provider_readiness", {}).get("status") != "PASS":
                raise CandidateExecutableMaterializationError(EXECUTABLE_CONTRACT_INVALID, "Preview 未通过 provider_candidate_payload gate", status_code=409, details={"safe_to_advance": False})
            raw_contract = preview.get("durable_contract")
            if not isinstance(raw_contract, Mapping):
                raise CandidateExecutableMaterializationError(EXECUTABLE_CONTRACT_INVALID, "Preview 缺少 Durable Contract payload", status_code=503)
            try:
                contract = DurableFrozenCandidateContractV1.from_dict(raw_contract)
                contract.provider_candidate_payload()
            except Exception as exc:
                raise CandidateExecutableMaterializationError(EXECUTABLE_CONTRACT_INVALID, "Durable Contract provider 校验失败，不能进入执行冻结", status_code=409, details={"validation_error": str(exc), "safe_to_advance": False}) from exc
            if contract.candidate_id != context.candidate_id or contract.candidate_hash != context.candidate_hash or str(preview.get("durable_contract_hash") or "") != contract.content_hash:
                raise CandidateExecutableMaterializationError(CANONICAL_CANDIDATE_IDENTITY_CONFLICT, "Preview Contract 与 Governance Freeze 身份不一致", status_code=409)
            existing_contract, existing_target = self._existing_contract(context)
            self._validate_safe_runtime_context(context.objective_id, context.proposal)
            target = existing_target or (self.root / "data/research/research_factory/batches" / context.batch_id / "durable_frozen_candidate_contracts.json")
            confirmation_path = self._confirmation_path(context)
            existing_receipt = _read_json(confirmation_path, code=INTEGRITY_FAILURE, required=False)
            idempotent = existing_receipt is not None
            if existing_receipt is None:
                receipt = self._build_confirmation(context, preview, contract, reviewer=reviewer, idempotency_key=idempotency_key, target=target)
                try:
                    _create_immutable_json(confirmation_path, receipt)
                except FileExistsError:
                    existing_receipt = _read_json(confirmation_path, code=INTEGRITY_FAILURE)
                    if existing_receipt is None:
                        raise CandidateExecutableMaterializationError(INTEGRITY_FAILURE, "执行合同确认记录并发写入结果不可读", status_code=503)
                    self._check_existing_confirmation(existing_receipt, preview, context, contract=contract, idempotency_key=idempotency_key, reviewer=reviewer)
                    receipt = dict(existing_receipt)
                    idempotent = True
                self._inject_crash("after_confirmation_receipt", "after_materialization_confirmation", "after_receipt_write")
            else:
                self._check_existing_confirmation(existing_receipt, preview, context, contract=existing_contract or contract, idempotency_key=idempotency_key, reviewer=reviewer)
                receipt = dict(existing_receipt)

            if existing_contract is None:
                try:
                    registry = DurableFrozenCandidateContractRegistryV1(target)
                    registry.append(contract)
                    registry.write()
                except ValueError as exc:
                    raise CandidateExecutableMaterializationError(CANONICAL_CANDIDATE_IDENTITY_CONFLICT, "canonical Durable Contract Store 拒绝了冲突 Candidate", status_code=409, details={"validation_error": str(exc)}) from exc
                self._inject_crash("after_durable_contract_append", "after_durable_contract")
            self._write_executable_state(context)
            return {
                "schema_version": EXECUTABLE_MATERIALIZATION_SCHEMA_VERSION,
                "confirmation": dict(receipt),
                "contract": contract.to_dict(),
                "durable_contract": contract.to_dict(),
                "proposal_id": context.proposal.get("proposal_id"),
                "candidate_id": context.candidate_id,
                "candidate_hash": context.candidate_hash,
                "effective_state": READY_FOR_STRUCTURAL_PREFLIGHT,
                "required_action": RUN_STRUCTURAL_PREFLIGHT,
                "structural_preflight_ready": True,
                "automatic_structural_preflight": False,
                "automatic_trial_started": False,
                "ai_called": False,
                "budget_consumed": False,
                "idempotent": idempotent and existing_contract is not None,
                "message_zh": "执行合同已由人工确认并进入 READY_FOR_STRUCTURAL_PREFLIGHT；系统未自动运行 Structural Preflight 或 Trial。",
            }

    confirm_materialization = confirm
    confirm_executable_materialization = confirm

    def recover(self, objective_id: str, proposal_id: str) -> dict[str, Any]:
        """Complete only the already-confirmed Preview -> Contract journal."""
        with self._mutex:
            context = self._load_context(objective_id, proposal_id)
            preview = _read_json(self._preview_path(context), code="EXECUTABLE_MATERIALIZATION_PREVIEW_REQUIRED", required=False)
            if preview is None:
                return self.read_state(objective_id, proposal_id)
            self._validate_preview_integrity(preview)
            self._assert_preview_current(context, preview)
            confirmation = _read_json(self._confirmation_path(context), code=INTEGRITY_FAILURE, required=False)
            if confirmation is None:
                return self.read_state(objective_id, proposal_id)
            receipt_check = inspect_materialization_confirmation(confirmation, preview=preview, objective_id=context.objective_id, proposal_id=str(context.proposal.get("proposal_id") or proposal_id))
            if not receipt_check["valid"]:
                return self.read_state(objective_id, proposal_id)
            raw_contract = preview.get("durable_contract")
            if not isinstance(raw_contract, Mapping):
                return self.read_state(objective_id, proposal_id)
            try:
                contract = DurableFrozenCandidateContractV1.from_dict(raw_contract)
                contract.provider_candidate_payload()
            except Exception:
                return self.read_state(objective_id, proposal_id)
            existing_contract, existing_target = self._existing_contract(context)
            target = existing_target or (self.root / "data/research/research_factory/batches" / context.batch_id / "durable_frozen_candidate_contracts.json")
            final_check = inspect_materialization_confirmation(confirmation, preview=preview, contract=existing_contract or contract, objective_id=context.objective_id, proposal_id=str(context.proposal.get("proposal_id") or proposal_id))
            if not final_check["valid"] or not final_check["identity_match"]:
                return self.read_state(objective_id, proposal_id)
            if existing_contract is None:
                try:
                    registry = DurableFrozenCandidateContractRegistryV1(target)
                    registry.append(contract)
                    registry.write()
                except ValueError:
                    return self.read_state(objective_id, proposal_id)
                self._inject_crash("after_durable_contract_append", "after_durable_contract")
            self._write_executable_state(context)
            return {
                **self._state_model(context, state=READY_FOR_STRUCTURAL_PREFLIGHT, next_action=RUN_STRUCTURAL_PREFLIGHT, preview=preview, confirmation=confirmation, contract=existing_contract or contract, safe_to_advance=True, confirmation_valid=True, identity_match=True),
                "recovered": True,
            }

    def recover_all(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if not self.proposal_root.exists():
            return results
        for path in sorted(self.proposal_root.glob("*/EXECUTABLE_MATERIALIZATION_PREVIEW.json")):
            proposal = _read_json(path.parent / CANDIDATE_PROPOSAL_FILENAME, code=INTEGRITY_FAILURE, required=False) or {}
            objective_id = str(proposal.get("objective_id") or path.parent.name)
            proposal_id = str(proposal.get("proposal_id") or "")
            if proposal_id:
                results.append(self.read_state(objective_id, proposal_id))
        return results


CandidateExecutableMaterializationServiceV1 = CandidateExecutableMaterializationManagerV1
CandidateExecutableMaterializationBridgeV1 = CandidateExecutableMaterializationManagerV1
CandidateExecutableMaterializationManager = CandidateExecutableMaterializationManagerV1


def _main() -> int:
    parser = argparse.ArgumentParser(description="创建或确认 Candidate Executable Materialization")
    parser.add_argument("--root", default=".")
    parser.add_argument("--objective-id", required=True)
    parser.add_argument("--proposal-id")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--preview-hash")
    parser.add_argument("--reviewer")
    parser.add_argument("--idempotency-key")
    args = parser.parse_args()
    manager = CandidateExecutableMaterializationManagerV1(args.root)
    if not args.confirm:
        result = manager.create_preview(args.objective_id, args.proposal_id)
    else:
        if not args.proposal_id:
            parser.error("--confirm requires --proposal-id")
        result = manager.confirm(args.objective_id, args.proposal_id, {"confirmed": True, "preview_hash": args.preview_hash, "reviewer": args.reviewer, "idempotency_key": args.idempotency_key})
    print(json.dumps({"状态": result.get("effective_state") or result.get("status"), "候选编号": result.get("candidate_id"), "执行合同哈希": result.get("durable_contract_hash") or result.get("confirmation", {}).get("durable_contract_hash"), "下一步": result.get("required_action") or result.get("next_action")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "CANONICAL_CANDIDATE_IDENTITY_CONFLICT",
    "CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION",
    "CANDIDATE_SEMANTIC_DRIFT",
    "CandidateExecutableMaterializationBridgeV1",
    "CandidateExecutableMaterializationError",
    "CandidateExecutableMaterializationManager",
    "CandidateExecutableMaterializationManagerV1",
    "CandidateExecutableMaterializationServiceV1",
    "EXECUTABLE_CONTRACT_INVALID",
    "EXECUTABLE_CANDIDATE_FROZEN",
    "EXECUTABLE_MATERIALIZATION_CONFIRMATION_FILENAME",
    "EXECUTABLE_MATERIALIZATION_CONFIRMATION_MISSING",
    "EXECUTABLE_MATERIALIZATION_CONFIRMATION_SCHEMA_VERSION",
    "EXECUTABLE_MATERIALIZATION_INCOMPLETE",
    "EXECUTABLE_MATERIALIZATION_PREVIEW_FILENAME",
    "EXECUTABLE_MATERIALIZATION_PREVIEW_READY",
    "EXECUTABLE_MATERIALIZATION_PREVIEW_SCHEMA_VERSION",
    "EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED",
    "EXECUTABLE_MATERIALIZATION_RECEIPT_FILENAME",
    "EXECUTABLE_MATERIALIZATION_SCHEMA_VERSION",
    "INTEGRITY_FAILURE",
    "MATERIALIZATION_IDEMPOTENCY_CONFLICT",
    "MATERIALIZATION_CONFIRMATION_REQUIRED_FIELDS",
    "RECOVER_EXECUTABLE_MATERIALIZATION",
    "READY_FOR_STRUCTURAL_PREFLIGHT",
    "RUN_STRUCTURAL_PREFLIGHT",
    "STALE_EXECUTABLE_MATERIALIZATION_PREVIEW",
    "inspect_materialization_confirmation",
    "inspect_materialization_preview",
]
