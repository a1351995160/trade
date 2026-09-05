"""Phase 4 automated strategy validation governance and fail-closed runner.

The module deliberately separates policy/trial governance from performance
execution.  A candidate cannot produce a performance row until policy freeze,
candidate-hash verification, and Stage 1 PIT/data checks have passed.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .guard import FINAL_TEST_START, RESEARCH_END, ResearchDataAccessGuard
from .strategy_candidate import StrategyCandidateSpec
from .strategy_semantic import (
    PHASE4_STATUSES,
    ExitPredicateSpec,
    SemanticCandidateRecord,
    SignalPredicateSpec,
    semantic_registry_hash,
)
from .validation_policy_v2 import ValidationDecisionPolicyV2, ValidationPolicyV2Error


SCHEMA_VERSION = "automated-strategy-validation-pipeline-v1"
POLICY_VERSION = "STRATEGY_VALIDATION_POLICY_V1"
TRIAL_SCHEMA_VERSION = "trial-registry-v1-append-only"
ALLOWED_CLASSIFICATIONS = {
    "BLOCKED",
    "INVALID",
    "INSUFFICIENT_EVIDENCE",
    "REJECTED",
    "WEAK",
    "PROMISING",
    "RESEARCH_PASSED",
}
FINAL_TEST_ZERO = {
    "final_test_new_physical_access": 0,
    "final_test_new_analytical_exposure": 0,
    "final_test_new_decision_exposure": 0,
}


class ValidationGovernanceError(RuntimeError):
    """Raised when a Phase 4 governance boundary is violated."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class ValidationPolicyV1:
    """All parameters that may affect Phase 4 evidence, frozen before results."""

    policy_version: str = POLICY_VERSION
    research_start: int = 20220801
    research_end: int = RESEARCH_END
    subperiods: tuple[dict[str, int], ...] = (
        ("P1", {"start": 20220801, "end": 20230731}),
        ("P2", {"start": 20230801, "end": 20240731}),
        ("P3", {"start": 20240801, "end": RESEARCH_END}),
    )
    regime_definition: str = "market_state_v1_pit_asof"
    transaction_model: str = "BacktestEngineV2_DailyBarFillModel"
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    slippage_bps: float = 0.001
    max_participation_rate: float = 0.10
    initial_cash: float = 1_000_000.0
    max_positions: int = 3
    lot_size: int = 100
    small_capital_cash: float = 10_000.0
    small_capital_slots: int = 3
    small_capital_lot_size: int = 100
    bootstrap_iterations: int = 10_000
    bootstrap_seed: int = 20260823
    fdr_method: str = "BENJAMINI_HOCHBERG"
    fdr_q: float = 0.05
    no_parameter_optimization: bool = True
    no_threshold_changes: bool = True
    no_final_test_access: bool = True
    no_historical_forward: bool = True
    no_paper: bool = True
    no_recommendation: bool = True
    no_broker: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "subperiods": [{"name": name, **period} for name, period in self.subperiods],
            "final_test_start": FINAL_TEST_START,
            "research_end_guard": RESEARCH_END,
        }

    def hash(self) -> str:
        return stable_hash(self.to_dict())

    def validate(self) -> None:
        if self.policy_version != POLICY_VERSION:
            raise ValidationGovernanceError("unsupported policy version")
        if self.research_end != RESEARCH_END or self.no_final_test_access is not True:
            raise ValidationGovernanceError("policy must use the sealed research boundary")
        if self.bootstrap_iterations != 10_000 or self.fdr_method != "BENJAMINI_HOCHBERG":
            raise ValidationGovernanceError("Phase 4 fixed inference contract changed")
        if not all((period["start"] <= period["end"] <= RESEARCH_END) for _, period in self.subperiods):
            raise ValidationGovernanceError("subperiod crosses research boundary")
        if not all((self.no_parameter_optimization, self.no_threshold_changes, self.no_broker)):
            raise ValidationGovernanceError("forbidden Phase 4 capability enabled")


def policy_payload(policy: ValidationPolicyV1, *, frozen: bool = True) -> dict[str, Any]:
    policy.validate()
    return {
        "schema_version": SCHEMA_VERSION,
        "policy": policy.to_dict(),
        "policy_hash": policy.hash(),
        "freeze_status": "FROZEN_PRE_PERFORMANCE" if frozen else "DRAFT",
        "performance_data_used": False,
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def load_frozen_policy(path: Path) -> tuple[ValidationPolicyV1, str]:
    payload = read_json(path)
    if payload.get("freeze_status") != "FROZEN_PRE_PERFORMANCE":
        raise ValidationGovernanceError("ValidationPolicyV1 is not frozen")
    raw = dict(payload["policy"])
    raw.pop("final_test_start", None)
    raw.pop("research_end_guard", None)
    raw["subperiods"] = tuple((item["name"], {"start": int(item["start"]), "end": int(item["end"])}) for item in raw["subperiods"])
    policy = ValidationPolicyV1(**raw)
    policy.validate()
    expected = policy.hash()
    if payload.get("policy_hash") != expected:
        raise ValidationGovernanceError("ValidationPolicyV1 hash mismatch")
    return policy, expected


class PerformanceAccessGate:
    """Explicit gate for any result-bearing read."""

    def __init__(self, marker_path: Path):
        self.marker_path = marker_path

    def enable(self, policy_path: Path) -> str:
        policy, policy_hash = load_frozen_policy(policy_path)
        marker = {
            "schema_version": "performance-access-gate-v1",
            "enabled": True,
            "policy_hash": policy_hash,
            "enabled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "reason": "Stage 0 policy freeze completed; Stage 1 PIT/data preflight passed for at least one trial",
        }
        write_json(self.marker_path, marker)
        return policy_hash

    def assert_enabled(self, policy_hash: str) -> None:
        if not self.marker_path.exists():
            raise ValidationGovernanceError("performance access is not enabled")
        marker = read_json(self.marker_path)
        if marker.get("enabled") is not True or marker.get("policy_hash") != policy_hash:
            raise ValidationGovernanceError("performance access gate hash mismatch")

    def assert_disabled(self) -> None:
        if self.marker_path.exists() and read_json(self.marker_path).get("enabled") is True:
            raise ValidationGovernanceError("performance access gate unexpectedly enabled")


@dataclass(frozen=True)
class TrialEvent:
    event_type: str
    trial_id: str
    candidate_id: str
    candidate_preregistration_hash: str
    status: str
    classification: str | None
    reason_codes: tuple[str, ...] = ()
    performance_accessed: bool = False
    payload_hash: str = ""
    created_at: str = ""

    def materialize(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reason_codes"] = list(self.reason_codes)
        payload["payload_hash"] = stable_hash(payload | {"payload_hash": ""})
        return payload


class TrialRegistryV1:
    """Append-only event log; completed trials never overwrite registration."""

    def __init__(self, path: Path):
        self.path = path

    def _payload(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": TRIAL_SCHEMA_VERSION, "events": []}
        payload = read_json(self.path)
        if payload.get("schema_version") != TRIAL_SCHEMA_VERSION:
            raise ValidationGovernanceError("trial registry schema mismatch")
        if not isinstance(payload.get("events"), list):
            raise ValidationGovernanceError("trial registry events must be a list")
        return payload

    def append(self, event: TrialEvent) -> None:
        payload = self._payload()
        item = event.materialize()
        if any(old.get("payload_hash") == item["payload_hash"] for old in payload["events"]):
            return
        payload["events"].append(item)
        payload["event_count"] = len(payload["events"])
        payload["trial_count"] = len({str(old["trial_id"]) for old in payload["events"]})
        payload["registry_hash"] = stable_hash(payload["events"])
        write_json(self.path, payload)

    def events(self) -> list[dict[str, Any]]:
        return list(self._payload()["events"])

    def latest(self) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for item in self.events():
            latest[str(item["trial_id"])] = item
        return latest


class ValidationCheckpointStore:
    def __init__(self, path: Path):
        self.path = path

    def write(self, phase: str, status: str, *, trial_id: str | None = None, payload: Mapping[str, Any] | None = None) -> None:
        current = read_json(self.path) if self.path.exists() else {"schema_version": "validation-checkpoint-v1", "events": []}
        current.setdefault("events", []).append({
            "phase": phase,
            "status": status,
            "trial_id": trial_id,
            "payload": dict(payload or {}),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        current["last_phase"] = phase
        current["last_status"] = status
        write_json(self.path, current)


def load_semantic_records(path: Path) -> tuple[dict[str, Any], list[SemanticCandidateRecord]]:
    payload = read_json(path)
    records: list[SemanticCandidateRecord] = []
    for item in payload.get("records", []):
        records.append(SemanticCandidateRecord(
            candidate=StrategyCandidateSpec.from_dict(item["candidate"]),
            parent_candidate_id=str(item["parent_candidate_id"]),
            previous_preregistration_hash=str(item["previous_preregistration_hash"]),
            semantic_change_reason=str(item["semantic_change_reason"]),
            signal_predicate=SignalPredicateSpec.from_dict(item["signal_predicate"]),
            exit_predicate=ExitPredicateSpec.from_dict(item["exit_predicate"]),
            semantic_status=str(item["semantic_status"]),
            phase4_eligible=bool(item["phase4_eligible"]),
            semantic_fingerprint=str(item["semantic_fingerprint"]),
            preregistration_hash=str(item["preregistration_hash"]),
            created_at=str(item["created_at"]),
        ))
    return payload, records


def recompute_preregistration_freeze(path: Path) -> dict[str, Any]:
    payload, records = load_semantic_records(path)
    eligible = [record for record in records if record.phase4_eligible]
    blocked = [record for record in records if not record.phase4_eligible]
    recomputed_registry_hash = semantic_registry_hash(records)
    mismatches = [
        record.candidate.candidate_id for record in records
        if record.preregistration_hash != record.candidate.preregistration_hash
    ]
    if mismatches or payload.get("registry_hash") != recomputed_registry_hash:
        raise ValidationGovernanceError("V2 preregistration/registry hash mismatch")
    return {
        "registry_path": str(path),
        "registry_hash": recomputed_registry_hash,
        "candidate_count": len(records),
        "phase4_eligible_count": len(eligible),
        "semantic_blocked_count": len(blocked),
        "eligible_candidate_ids": [record.candidate.candidate_id for record in eligible],
        "blocked_candidate_ids": [record.candidate.candidate_id for record in blocked],
        "preregistration_hashes": {record.candidate.candidate_id: record.preregistration_hash for record in records},
        "freeze_status": "FROZEN_PRE_PERFORMANCE",
        "performance_data_used": False,
    }


def _phase4_1_pit_contract(repo_root: Path, research_end: int) -> dict[str, Any] | None:
    manifest_path = repo_root / "data" / "research" / "security_state" / "normalized" / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = read_json(manifest_path)
    coverage = dict(manifest.get("coverage", {}))
    gate_path = repo_root / "reports" / "PIT_DATA_QUALITY_GATE_V1.json"
    gate = read_json(gate_path) if gate_path.exists() else {}
    coverage_end = int(str(manifest.get("coverage_end", "0")).replace("-", ""))
    return {
        "manifest": manifest,
        "coverage": coverage,
        "gate": gate,
        "coverage_end": coverage_end,
        "pit_st_status": "PASS" if coverage.get("st_unknown_symbol_days", 0) == 0 else "PARTIAL",
        "pit_suspension_status": "PASS" if coverage.get("suspension_unknown_symbol_days", 0) == 0 else "PARTIAL",
        "pit_universe_status": "PASS" if coverage_end >= research_end and gate.get("status") == "PASS" else "PARTIAL",
    }


def stage1_pit_data_preflight(repo_root: Path, candidate: SemanticCandidateRecord, *, research_end: int = RESEARCH_END) -> dict[str, Any]:
    """Return a fail-closed Stage 1 decision without reading any performance output."""
    reasons: list[str] = []
    contract = _phase4_1_pit_contract(repo_root, research_end)
    requirements = {
        "requires_listing_state": True,
        "requires_st_status": True,
        "requires_suspension_at_execution": True,
        "requires_event_state": candidate.candidate.candidate_type == "EVENT_SIGNAL",
    }
    if contract is not None:
        if contract["coverage_end"] < research_end:
            reasons.append("PIT_UNIVERSE_COVERAGE_INCOMPLETE")
        if contract["pit_universe_status"] != "PASS":
            reasons.append("PIT_DATA_QUALITY_GATE_NOT_PASS")
        result = {
            "candidate_id": candidate.candidate.candidate_id,
            "stage": "STAGE_1_PIT_DATA_PREFLIGHT",
            "status": "BLOCKED" if reasons else "PASS",
            "reason_codes": reasons,
            "performance_accessed": False,
            "pit_st_status": contract["pit_st_status"],
            "pit_suspension_status": contract["pit_suspension_status"],
            "pit_universe_status": contract["pit_universe_status"],
            "pit_universe_manifest_end": contract["coverage_end"],
            "research_end": research_end,
            "required_security_fields": requirements,
            "coverage": contract["coverage"],
            "coverage_bias_status": contract["gate"].get("coverage_bias_status", "UNKNOWN"),
            "data_quality_gate": contract["gate"].get("status", "UNKNOWN"),
        }
        if candidate.semantic_status not in PHASE4_STATUSES or not candidate.phase4_eligible:
            result["reason_codes"].append("SEMANTIC_NOT_PHASE4_ELIGIBLE")
            result["status"] = "BLOCKED"
        return result
    pit_audit_path = repo_root / "reports" / "PIT_SECURITY_MASTER_AUDIT.json"
    pit_audit = read_json(pit_audit_path) if pit_audit_path.exists() else {}
    statuses = pit_audit.get("statuses", pit_audit)
    if statuses.get("PIT_ST_STATUS") != "PASS":
        reasons.append("PIT_ST_STATUS_UNKNOWN")
    if statuses.get("PIT_SUSPENSION_STATUS") != "PASS":
        reasons.append("PIT_SUSPENSION_STATUS_PARTIAL")
    manifest_path = repo_root / "data" / "research" / "stage3_universe_manifest.json"
    manifest_end = None
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        manifest_end = int(str(manifest.get("scope", {}).get("end", "0")).replace("-", ""))
    if manifest_end is None or manifest_end < research_end:
        reasons.append("PIT_UNIVERSE_COVERAGE_INCOMPLETE")
    if candidate.semantic_status not in PHASE4_STATUSES or not candidate.phase4_eligible:
        reasons.append("SEMANTIC_NOT_PHASE4_ELIGIBLE")
    return {
        "candidate_id": candidate.candidate.candidate_id,
        "stage": "STAGE_1_PIT_DATA_PREFLIGHT",
        "status": "BLOCKED" if reasons else "PASS",
        "reason_codes": reasons,
        "performance_accessed": False,
        "pit_st_status": statuses.get("PIT_ST_STATUS", "UNKNOWN"),
        "pit_suspension_status": statuses.get("PIT_SUSPENSION_STATUS", "UNKNOWN"),
        "pit_universe_manifest_end": manifest_end,
        "research_end": research_end,
        "required_security_fields": requirements,
    }


def ensure_no_future_access(repo_root: Path, payload: Mapping[str, Any]) -> None:
    guard = ResearchDataAccessGuard()
    for key in ("research_start", "research_end", "date", "start_date", "end_date"):
        if key in payload and payload[key] is not None:
            guard.check_date(int(payload[key]), f"validation payload {key}")
    if int(payload.get("final_test_new_physical_access", 0)) != 0:
        raise ValidationGovernanceError("Final Test physical access was recorded")


def _as_float_list(values: Iterable[Any]) -> list[float]:
    return [float(x) for x in values if x is not None and math.isfinite(float(x))]


def performance_metrics(equity: Sequence[float], trades: Sequence[Mapping[str, Any]], *, initial_cash: float) -> dict[str, Any]:
    """Pure metric reducer used only after the performance gate."""
    series = _as_float_list(equity)
    if not series or initial_cash <= 0:
        return {"net_return": None, "max_drawdown": None, "profit_factor": None, "trades": 0, "win_rate": None}
    peak = series[0]
    max_dd = 0.0
    for value in series:
        peak = max(peak, value)
        max_dd = min(max_dd, value / peak - 1.0 if peak else 0.0)
    pnl = [float(row.get("realized_pnl", 0.0)) for row in trades]
    wins = [x for x in pnl if x > 0]
    losses = [-x for x in pnl if x < 0]
    return {
        "net_return": series[-1] / initial_cash - 1.0,
        "max_drawdown": max_dd,
        "profit_factor": (sum(wins) / sum(losses)) if losses else (None if not wins else float("inf")),
        "trades": len(trades),
        "win_rate": len(wins) / len(pnl) if pnl else None,
    }


def benjamini_hochberg(p_values: Mapping[str, float], q: float = 0.05) -> dict[str, Any]:
    if not 0 < q < 1:
        raise ValueError("q must be between 0 and 1")
    clean = [(key, float(value)) for key, value in p_values.items() if math.isfinite(float(value))]
    clean.sort(key=lambda pair: (pair[1], pair[0]))
    n = len(clean)
    adjusted: dict[str, float] = {}
    running = 1.0
    for rank, (key, p_value) in reversed(list(enumerate(clean, start=1))):
        running = min(running, p_value * n / rank)
        adjusted[key] = min(1.0, running)
    raw_support = {key: value <= q for key, value in clean}
    adjusted_support = {key: adjusted[key] <= q for key, _ in clean}
    return {
        "method": "BENJAMINI_HOCHBERG",
        "q": q,
        "hypothesis_count": n,
        "raw_p_values": dict(clean),
        "adjusted_p_values": adjusted,
        "raw_support": raw_support,
        "adjusted_support": adjusted_support,
    }


FINAL_RESEARCH_CLASSIFICATION_CONTRACT_V1 = "FINAL_RESEARCH_CLASSIFICATION_CONTRACT_V1"
FINAL_CLASSIFICATIONS = {"REJECTED", "WEAK", "PROMISING", "RESEARCH_PASSED", "INVALID", "BLOCKED", "ENGINEERING_BLOCKED"}
FROZEN_V1_GATE_ROLES = {
    "engine_integrity": "HARD_GATE",
    "sample_adequacy": "HARD_GATE",
    "cost_stress": "HARD_GATE",
    "baseline": "REPORT_ONLY",
    "small_capital_10k": "REPORT_ONLY_OR_FEASIBILITY_ONLY",
    "concentration": "REPORT_ONLY",
    "subperiod": "REPORT_ONLY",
    "regime": "REPORT_ONLY",
    "microstructure": "REPORT_ONLY",
}


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _local_classification(value: Any) -> str:
    """Normalize the legacy candidate-local enum without changing its behavior."""
    normalized = str(value or "").upper()
    if normalized in {"RESEARCH_PASSED", "RESEARCH_PASSED_LOCAL"}:
        return "RESEARCH_PASSED_LOCAL"
    if normalized == "PROMISING":
        return "PROMISING"
    if normalized in {"INSUFFICIENT_EVIDENCE", "WEAK"}:
        return "WEAK"
    if normalized in {"REJECTED"}:
        return "REJECTED"
    if normalized in {"INVALID", "BLOCKED", "ENGINEERING_BLOCKED", "ENGINE_ERROR"}:
        return "INVALID"
    return "INVALID"


@dataclass(frozen=True)
class FinalResearchDecisionV1:
    """The only decision record allowed to drive a final strategy state."""

    candidate_id: str
    candidate_hash: str
    original_trial_id: str
    local_classification: str
    effective_classification: str
    decision_family_id: str
    decision_denominator: int
    fdr_method: str
    fdr_q: float
    adjusted_p: float | None
    adjusted_support: bool
    decision_timestamp: str
    history_snapshot_hash: str
    validation_policy_hash: str
    small_capital_evidence_status: str
    small_capital_contract_valid: bool
    engine_integrity: str
    metrics_ref: str
    gate_roles: Mapping[str, str] = field(default_factory=dict)
    hard_gate_failures: tuple[str, ...] = ()
    reason: str = ""
    contract_version: str = FINAL_RESEARCH_CLASSIFICATION_CONTRACT_V1
    validation_policy_id: str = "VALIDATION_POLICY_V1"
    validation_policy_version: str = "STRATEGY_VALIDATION_POLICY_V1"
    failure_category: str | None = None
    concentration_evidence_status: str = "NOT_PROVIDED"
    concentration_metrics_ref: str = ""
    concentration_warning: str = "NONE"
    subperiod_consistency_status: str = "NOT_PROVIDED"
    regime_dependency_warning: str = "NONE"
    baseline_comparison_warning: str = "NONE"
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.effective_classification not in FINAL_CLASSIFICATIONS:
            raise ValueError(f"unsupported final classification: {self.effective_classification}")
        if self.decision_denominator <= 0:
            raise ValueError("decision denominator must be positive")
        if self.fdr_method != "BENJAMINI_HOCHBERG":
            raise ValidationGovernanceError("final decision must use the frozen BH method")
        if not 0 < float(self.fdr_q) < 1:
            raise ValidationGovernanceError("final decision q must be between 0 and 1")
        object.__setattr__(self, "gate_roles", dict(self.gate_roles))
        object.__setattr__(self, "hard_gate_failures", tuple(self.hard_gate_failures))
        object.__setattr__(self, "warnings", tuple(self.warnings))

    @property
    def decision_id(self) -> str:
        return f"FINAL_RESEARCH_DECISION:{self.original_trial_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "final-research-decision-v2" if self.validation_policy_id == "VALIDATION_DECISION_POLICY_V2" else "final-research-decision-v1",
            "contract_version": self.contract_version,
            "decision_id": self.decision_id,
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "original_trial_id": self.original_trial_id,
            "local_classification": self.local_classification,
            "effective_classification": self.effective_classification,
            "decision_family_id": self.decision_family_id,
            "decision_denominator": self.decision_denominator,
            "fdr_method": self.fdr_method,
            "fdr_q": self.fdr_q,
            "adjusted_p": self.adjusted_p,
            "adjusted_support": self.adjusted_support,
            "decision_timestamp": self.decision_timestamp,
            "history_snapshot_hash": self.history_snapshot_hash,
            "validation_policy_hash": self.validation_policy_hash,
            "small_capital_evidence_status": self.small_capital_evidence_status,
            "small_capital_contract_valid": self.small_capital_contract_valid,
            "engine_integrity": self.engine_integrity,
            "metrics_ref": self.metrics_ref,
            "gate_roles": dict(self.gate_roles),
            "hard_gate_failures": list(self.hard_gate_failures),
            "reason": self.reason,
            "validation_policy_id": self.validation_policy_id,
            "validation_policy_version": self.validation_policy_version,
            "failure_category": self.failure_category,
            "concentration_evidence_status": self.concentration_evidence_status,
            "concentration_metrics_ref": self.concentration_metrics_ref,
            "concentration_warning": self.concentration_warning,
            "subperiod_consistency_status": self.subperiod_consistency_status,
            "regime_dependency_warning": self.regime_dependency_warning,
            "baseline_comparison_warning": self.baseline_comparison_warning,
            "warnings": list(self.warnings),
        }


class FinalResearchAdjudicatorV1:
    """Adjudicate local evidence only after the complete decision-family BH run."""

    def __init__(self, policy: ValidationPolicyV1 | ValidationDecisionPolicyV2, *, policy_hash: str | None = None):
        policy.validate()
        if isinstance(policy, ValidationDecisionPolicyV2):
            policy.assert_active()
            if policy_hash is not None and policy_hash != policy.policy_hash:
                raise ValidationGovernanceError("ValidationDecisionPolicyV2 hash mismatch in final adjudicator")
            self.policy = policy
            self.policy_hash = policy_hash or policy.policy_hash
            return
        if policy.fdr_method != "BENJAMINI_HOCHBERG":
            raise ValidationGovernanceError("Frozen ValidationPolicyV1 FDR method is not BH")
        if policy_hash is not None and policy_hash != policy.hash():
            raise ValidationGovernanceError("ValidationPolicyV1 hash mismatch in final adjudicator")
        self.policy = policy
        self.policy_hash = policy_hash or policy.hash()

    @staticmethod
    def _engine_ok(value: Any) -> bool:
        if isinstance(value, Mapping):
            return not value.get("invariant_errors") and str(value.get("certification_status", "VALID")).upper() not in {"INVALID", "FAILED"}
        return str(value or "VALID").upper() in {"PASS", "VALID", "CHECKED", "OK"}

    @staticmethod
    def _hard_gate_failures(evidence: Mapping[str, Any]) -> tuple[str, ...]:
        failures: list[str] = []
        gates = evidence.get("gates", {})
        for name, value in gates.items() if isinstance(gates, Mapping) else ():
            role = value.get("role") if isinstance(value, Mapping) else FROZEN_V1_GATE_ROLES.get(str(name))
            passed = value.get("passed") if isinstance(value, Mapping) else value
            if role == "HARD_GATE" and passed is False:
                failures.append(str(name))
        return tuple(sorted(failures))

    def adjudicate(
        self,
        *,
        candidate_id: str,
        candidate_hash: str,
        trial_id: str,
        local_classification: str,
        multiple_testing: Mapping[str, Any],
        decision_family_id: str,
        decision_denominator: int,
        history_snapshot_hash: str,
        metrics_ref: str,
        evidence: Mapping[str, Any] | None = None,
        decision_timestamp: str | None = None,
    ) -> FinalResearchDecisionV1:
        if isinstance(self.policy, ValidationDecisionPolicyV2):
            return self._adjudicate_v2(
                candidate_id=candidate_id,
                candidate_hash=candidate_hash,
                trial_id=trial_id,
                local_classification=local_classification,
                multiple_testing=multiple_testing,
                decision_family_id=decision_family_id,
                decision_denominator=decision_denominator,
                history_snapshot_hash=history_snapshot_hash,
                metrics_ref=metrics_ref,
                evidence=evidence,
                decision_timestamp=decision_timestamp,
            )
        evidence = dict(evidence or {})
        if not decision_family_id or decision_denominator <= 0:
            raise ValidationGovernanceError("final decision family metadata is required")
        if str(multiple_testing.get("method")) != self.policy.fdr_method:
            raise ValidationGovernanceError("multiple-testing method does not match Frozen ValidationPolicyV1")
        if float(multiple_testing.get("q")) != float(self.policy.fdr_q):
            raise ValidationGovernanceError("multiple-testing q does not match Frozen ValidationPolicyV1")
        hypothesis_count = int(multiple_testing.get("hypothesis_count", decision_denominator))
        if hypothesis_count != int(decision_denominator):
            raise ValidationGovernanceError("decision denominator does not match the frozen testing family")

        candidate_key = str(candidate_id)
        adjusted_values = multiple_testing.get("adjusted_p_values", {})
        support_values = multiple_testing.get("adjusted_support", {})
        adjusted_p = adjusted_values.get(candidate_key) if isinstance(adjusted_values, Mapping) else None
        adjusted_support = bool(support_values.get(candidate_key, False)) if isinstance(support_values, Mapping) else False
        local = _local_classification(local_classification)
        engine_ok = self._engine_ok(evidence.get("engine_integrity", "VALID"))
        hard_failures = self._hard_gate_failures(evidence)
        if not engine_ok:
            effective = "INVALID"
            reason = "ENGINE_INTEGRITY_HARD_GATE_FAILED"
        elif hard_failures:
            effective = "REJECTED"
            reason = "FROZEN_HARD_GATE_FAILED"
        elif local == "INVALID":
            effective = "INVALID"
            reason = "LOCAL_VALIDATION_INVALID"
        elif local == "REJECTED":
            effective = "REJECTED"
            reason = "LOCAL_ALPHA_OR_COST_GATE_FAILED"
        elif local == "WEAK":
            effective = "WEAK"
            reason = "LOCAL_EVIDENCE_NOT_STATISTICALLY_SUPPORTED"
        elif adjusted_support:
            effective = "RESEARCH_PASSED"
            reason = "LOCAL_PASS_AND_BH_ADJUSTED_SUPPORT"
        else:
            effective = "PROMISING"
            reason = "LOCAL_PASS_BUT_BH_ADJUSTED_SUPPORT_FALSE"
        return FinalResearchDecisionV1(
            candidate_id=candidate_key,
            candidate_hash=str(candidate_hash),
            original_trial_id=str(trial_id),
            local_classification=local,
            effective_classification=effective,
            decision_family_id=str(decision_family_id),
            decision_denominator=int(decision_denominator),
            fdr_method=self.policy.fdr_method,
            fdr_q=float(self.policy.fdr_q),
            adjusted_p=float(adjusted_p) if adjusted_p is not None else None,
            adjusted_support=adjusted_support,
            decision_timestamp=decision_timestamp or _utc_timestamp(),
            history_snapshot_hash=str(history_snapshot_hash),
            validation_policy_hash=self.policy_hash,
            small_capital_evidence_status=str(evidence.get("small_capital_evidence_status", "NOT_PROVIDED")),
            small_capital_contract_valid=bool(evidence.get("small_capital_contract_valid", False)),
            engine_integrity="PASS" if engine_ok else "FAIL",
            metrics_ref=str(metrics_ref),
            gate_roles=dict(evidence.get("gate_roles", FROZEN_V1_GATE_ROLES)),
            hard_gate_failures=hard_failures,
            reason=reason,
        )

    def _adjudicate_v2(
        self,
        *,
        candidate_id: str,
        candidate_hash: str,
        trial_id: str,
        local_classification: str,
        multiple_testing: Mapping[str, Any],
        decision_family_id: str,
        decision_denominator: int,
        history_snapshot_hash: str,
        metrics_ref: str,
        evidence: Mapping[str, Any] | None,
        decision_timestamp: str | None,
    ) -> FinalResearchDecisionV1:
        policy = self.policy
        assert isinstance(policy, ValidationDecisionPolicyV2)
        evidence = dict(evidence or {})
        if not decision_family_id or decision_denominator <= 0:
            raise ValidationGovernanceError("V2 final decision family metadata is required")
        if str(multiple_testing.get("method")) != policy.fdr_method or float(multiple_testing.get("q", -1)) != float(policy.fdr_q):
            raise ValidationGovernanceError("multiple-testing method/q does not match ValidationDecisionPolicyV2")
        if int(multiple_testing.get("hypothesis_count", 0)) != int(decision_denominator):
            raise ValidationGovernanceError("V2 decision denominator does not match the frozen testing family")
        expected_family_id = multiple_testing.get("decision_family_id")
        if expected_family_id is not None and str(expected_family_id) != str(decision_family_id):
            raise ValidationGovernanceError("V2 decision family id mismatch")
        if multiple_testing.get("family_contract_hash") is None:
            raise ValidationGovernanceError("V2 multiple-testing family contract hash is required")
        adjusted_values = multiple_testing.get("adjusted_p_values", {})
        support_values = multiple_testing.get("adjusted_support", {})
        key = str(candidate_id)
        if not isinstance(support_values, Mapping) or key not in support_values:
            raise ValidationGovernanceError("V2 adjusted_support is missing for candidate")
        adjusted_support = bool(support_values[key])
        adjusted_p = adjusted_values.get(key) if isinstance(adjusted_values, Mapping) else None
        local = _local_classification(local_classification)
        result = policy.classify(
            local_classification=local,
            adjusted_support=adjusted_support,
            evidence=evidence,
            multiple_testing=multiple_testing,
        )
        small_capital = evidence.get("gates", {}).get("small_capital_execution_feasibility") if isinstance(evidence.get("gates"), Mapping) else None
        small_capital_valid = bool(small_capital.get("passed")) if isinstance(small_capital, Mapping) else bool(small_capital)
        engine_ok = result["effective_classification"] != "ENGINEERING_BLOCKED"
        return FinalResearchDecisionV1(
            candidate_id=key,
            candidate_hash=str(candidate_hash),
            original_trial_id=str(trial_id),
            local_classification=local,
            effective_classification=result["effective_classification"],
            decision_family_id=str(decision_family_id),
            decision_denominator=int(decision_denominator),
            fdr_method=policy.fdr_method,
            fdr_q=float(policy.fdr_q),
            adjusted_p=float(adjusted_p) if adjusted_p is not None else None,
            adjusted_support=adjusted_support,
            decision_timestamp=decision_timestamp or _utc_timestamp(),
            history_snapshot_hash=str(history_snapshot_hash),
            validation_policy_hash=self.policy_hash,
            small_capital_evidence_status=str(evidence.get("small_capital_evidence_status", "NOT_PROVIDED")),
            small_capital_contract_valid=small_capital_valid,
            engine_integrity="PASS" if engine_ok else "FAIL",
            metrics_ref=str(metrics_ref),
            gate_roles={key: value.get("role", "") for key, value in policy.gates.items()},
            hard_gate_failures=tuple(result["hard_gate_failures"]),
            reason=str(result["reason"]),
            contract_version="FINAL_RESEARCH_DECISION_SEMANTICS_V2",
            validation_policy_id=policy.policy_id,
            validation_policy_version=policy.policy_version,
            failure_category=result.get("failure_category"),
            concentration_evidence_status=str(result.get("concentration_evidence_status", "NOT_PROVIDED")),
            concentration_metrics_ref=str(result.get("concentration_metrics_ref", "")),
            concentration_warning=str(result.get("concentration_warning", "NONE")),
            subperiod_consistency_status=str(result.get("subperiod_consistency_status", "NOT_PROVIDED")),
            regime_dependency_warning=str(result.get("regime_dependency_warning", "NONE")),
            baseline_comparison_warning=str(result.get("baseline_comparison_warning", "NONE")),
            warnings=tuple(result.get("warnings", ())),
        )


def validation_decision_policy_v2_draft() -> dict[str, Any]:
    """Return a non-active contract skeleton; unresolved thresholds stay explicit."""
    gates = {
        "local_alpha": {"role": "HARD_GATE", "metric": "legacy_local_classifier", "direction": "PASS", "threshold_source": "FROZEN_V1_LEGACY_CLASSIFIER", "pre_registration_required": True, "failure_classification_mapping": "ALPHA_FAILURE"},
        "multiple_testing": {"role": "HARD_GATE", "metric": "adjusted_support", "direction": "TRUE", "threshold_source": "FROZEN_V1_BH_POLICY", "pre_registration_required": True, "failure_classification_mapping": "ALPHA_FAILURE"},
        "10k": {"role": "REPORT_ONLY_OR_FEASIBILITY_ONLY", "metric": "small_capital_evidence_status", "direction": "REPORT", "threshold_source": "THRESHOLD_UNRESOLVED", "pre_registration_required": True, "failure_classification_mapping": "ROBUSTNESS_FAILURE"},
        "concentration": {"role": "REPORT_ONLY", "metric": "top_n_and_remove_top_n", "direction": "REPORT", "threshold_source": "THRESHOLD_UNRESOLVED", "pre_registration_required": True, "failure_classification_mapping": "ROBUSTNESS_FAILURE"},
        "subperiod": {"role": "REPORT_ONLY", "metric": "subperiod_results", "direction": "REPORT", "threshold_source": "THRESHOLD_UNRESOLVED", "pre_registration_required": True, "failure_classification_mapping": "ROBUSTNESS_FAILURE"},
        "regime": {"role": "REPORT_ONLY", "metric": "regime_split", "direction": "REPORT", "threshold_source": "THRESHOLD_UNRESOLVED", "pre_registration_required": True, "failure_classification_mapping": "ROBUSTNESS_FAILURE"},
        "cost_stress": {"role": "HARD_GATE", "metric": "COMBINED_X2.net_return", "direction": "> 0", "threshold_source": "FROZEN_V1_LEGACY_CLASSIFIER", "pre_registration_required": True, "failure_classification_mapping": "ROBUSTNESS_FAILURE"},
        "sample_adequacy": {"role": "HARD_GATE", "metric": "closed_trade_count/bootstrap_status", "direction": "ADEQUATE", "threshold_source": "FROZEN_V1_LEGACY_CLASSIFIER", "pre_registration_required": True, "failure_classification_mapping": "SAMPLE_FAILURE"},
        "microstructure": {"role": "REPORT_ONLY", "metric": "microstructure_evidence", "direction": "REPORT", "threshold_source": "THRESHOLD_UNRESOLVED", "pre_registration_required": True, "failure_classification_mapping": "ROBUSTNESS_FAILURE"},
        "baseline": {"role": "REPORT_ONLY", "metric": "baseline_result", "direction": "REPORT", "threshold_source": "BASELINE_RESULT_MISSING_FOR_V1", "pre_registration_required": True, "failure_classification_mapping": "ALPHA_FAILURE"},
    }
    return {
        "schema_version": "validation-decision-policy-v2-draft",
        "policy_id": "VALIDATION_DECISION_POLICY_V2",
        "activation_status": "DRAFT_NOT_ACTIVE",
        "autonomous_research_ready": "NO",
        "gates": gates,
        "unresolved_thresholds": sorted(name for name, gate in gates.items() if gate["threshold_source"] == "THRESHOLD_UNRESOLVED"),
        "outcome_derived_thresholds": False,
        "baseline_result_status": "BASELINE_RESULT_MISSING_FOR_V1",
    }


def small_capital_contract(policy: ValidationPolicyV1) -> dict[str, Any]:
    return {
        "cash": policy.small_capital_cash,
        "slots": policy.small_capital_slots,
        "lot_size": policy.small_capital_lot_size,
        "no_leverage": True,
        "no_margin": True,
        "fixed_contract": True,
        "parameter_optimization": False,
    }


def write_empty_evidence(trial_dir: Path, *, trial_id: str, reason_codes: Sequence[str], manifest: Mapping[str, Any]) -> None:
    trial_dir.mkdir(parents=True, exist_ok=True)
    for name, fields in {
        "signals.csv": ["generated_at", "symbol", "action", "reason"],
        "orders.csv": ["order_id", "symbol", "side", "status", "reason"],
        "fills.csv": ["fill_id", "symbol", "side", "quantity", "price", "fee"],
        "trades.csv": ["trade_id", "symbol", "side", "quantity", "price", "realized_pnl"],
        "equity.csv": ["timestamp", "equity", "cash", "market_value"],
        "daily_snapshots.csv": ["timestamp", "equity", "positions", "turnover"],
        "rejection_reasons.csv": ["stage", "reason_code"],
        "fee_ledger.csv": ["timestamp", "fee_type", "amount"],
    }.items():
        with (trial_dir / name).open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(fields)
    write_json(trial_dir / "manifest.json", dict(manifest) | {
        "trial_id": trial_id,
        "evidence_status": "NO_PERFORMANCE_EVIDENCE_STAGE1_BLOCKED",
        "reason_codes": list(reason_codes),
    })


def compiled_signals_to_engine_signals(compiled_signals: Iterable[Any]) -> list[Any]:
    """Adapt StrategyCandidateCompilerV2 output to the existing engine Signal."""
    from ..engine.signal import ExecutionPolicy, Side, Signal

    converted: list[Signal] = []
    for item in compiled_signals:
        metadata = dict(item.metadata)
        metadata.update({
            "available_at": item.available_at,
            "eligible_at": item.eligible_at,
            "compiled_reason": item.reason,
            "source_factors": list(item.source_factors),
        })
        converted.append(Signal(
            strategy_id=item.strategy_id,
            signal_id=f"{item.strategy_id}:{item.generated_at}:{item.symbol}:{item.action}",
            symbol=item.symbol,
            generated_at=item.generated_at,
            direction=Side.BUY if item.action == "BUY" else Side.SELL,
            score=float(item.score),
            signal_type="PHASE4_COMPILED_CANDIDATE",
            execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
            metadata=metadata,
            source_event_ids=list(metadata.get("source_event_ids", [])),
        ))
    return converted


def run_engine_research_trial(engine: Any, compiled_signals: Iterable[Any], gate: PerformanceAccessGate, policy_hash: str) -> Any:
    """Run the only permitted performance path after the explicit access gate."""
    gate.assert_enabled(policy_hash)
    engine.add_signals(compiled_signals_to_engine_signals(compiled_signals))
    return engine.run()
