"""Outcome-blind Autonomous Research Control Plane V1.

This module is deliberately a runtime projection over the existing canonical
research services.  It can observe and plan the next governed action, but it
cannot create authority by itself.  A tick executes at most one automatic,
already-permitted side-effectful action and then stops for reconciliation.
"""
from __future__ import annotations

from ..execution_policy import ExecutionPolicy, validate_research_root
from .mutation_boundary import ObjectiveMutationLock, MutationBusyError

from dataclasses import dataclass, field, replace
import argparse
import json
from pathlib import Path
import re
import threading
import tempfile
from typing import Any, Callable, Iterable, Mapping, Sequence

from .candidate_executable_materialization import (
    CandidateExecutableMaterializationError,
    EXECUTABLE_MATERIALIZATION_CONFIRMATION_MISSING,
    EXECUTABLE_MATERIALIZATION_PREVIEW_READY,
    EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED,
    CandidateExecutableMaterializationManagerV1,
    inspect_materialization_preview,
)
from .candidate_generation import CandidateGenerationError, CandidateGenerationManagerV1
from .common import now_timestamp, stable_hash
from .context import PerformanceBlindGuard, PerformanceLeakError
from .objective_reconciliation import (
    AI_DESIGN_APPROVED,
    AI_DESIGN_AWAITING_CONFIRMATION,
    BUDGET_AUTHORITY_AMBIGUOUS,
    BUDGET_EXHAUSTED,
    CANONICAL_CONFLICT,
    CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION,
    CANDIDATE_PROPOSAL_READY,
    ENGINEERING_BLOCKED,
    EXECUTABLE_CONTRACT_INVALID,
    NEED_AI_RESEARCH_DESIGN,
    PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED,
    READY_FOR_STRUCTURAL_PREFLIGHT,
    STRUCTURAL_BLOCKED,
    STRUCTURAL_RUNNING,
    TRIAL_ACTIVE,
    TRIAL_TERMINAL,
    ObjectiveReconciliationServiceV1,
)
from .safe_runtime_context import (
    SafeRuntimeContextBuilderV1,
    SafeRuntimeContextError,
    SafeRuntimeContextV1,
    STALE_RUNTIME_CONTEXT,
)
from .autonomous_action_journal import (
    EXECUTION_COMPLETED,
    EXECUTION_STARTED,
    AutonomousActionExecutionJournalV1,
    AutonomousActionExecutionReceiptV1,
    AutonomousActionJournalError,
)


RECONCILE_OBJECTIVE = "RECONCILE_OBJECTIVE"
BUILD_SAFE_RUNTIME_CONTEXT = "BUILD_SAFE_RUNTIME_CONTEXT"
GENERATE_AI_DESIGN = "GENERATE_AI_DESIGN"
WAIT_FOR_AI_DESIGN_CONFIRMATION = "WAIT_FOR_AI_DESIGN_CONFIRMATION"
GENERATE_CANDIDATE_PROPOSAL = "GENERATE_CANDIDATE_PROPOSAL"
WAIT_FOR_CANDIDATE_GOVERNANCE_FREEZE = "WAIT_FOR_CANDIDATE_GOVERNANCE_FREEZE"
CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW = "CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW"
WAIT_FOR_EXECUTABLE_MATERIALIZATION_CONFIRMATION = "WAIT_FOR_EXECUTABLE_MATERIALIZATION_CONFIRMATION"
RECOVER_EXECUTABLE_MATERIALIZATION = "RECOVER_EXECUTABLE_MATERIALIZATION"
RUN_STRUCTURAL_PREFLIGHT = "RUN_STRUCTURAL_PREFLIGHT"
WAIT_FOR_PREDICTIVE_AUTHORIZATION = "WAIT_FOR_PREDICTIVE_AUTHORIZATION"
START_PREDICTIVE_TRIAL = "START_PREDICTIVE_TRIAL"
WAIT_FOR_TRIAL_RESULT = "WAIT_FOR_TRIAL_RESULT"
RECONCILE_TRIAL = "RECONCILE_TRIAL"
STOP_OBJECTIVE = "STOP_OBJECTIVE"
BLOCKED = "BLOCKED"

ALLOW_AUTOMATIC = "ALLOW_AUTOMATIC"
ALLOW_MANUAL_ONLY = "ALLOW_MANUAL_ONLY"
DENY = "DENY"

OBSERVE = "OBSERVE"
RECONCILE = "RECONCILE"
PLAN = "PLAN"
WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
READY_TO_EXECUTE = "READY_TO_EXECUTE"
EXECUTING = "EXECUTING"
RECONCILE_AFTER_EXECUTION = "RECONCILE_AFTER_EXECUTION"
CONTROL_BLOCKED = "BLOCKED"
STOPPED = "STOPPED"

STALE_RESEARCH_ACTION = "STALE_RESEARCH_ACTION"
PREDICTIVE_AUTHORIZATION_REQUIRED = "PREDICTIVE_AUTHORIZATION_REQUIRED"
PHASE2_PREDICTIVE_EXECUTION_DISABLED = "PHASE2_PREDICTIVE_EXECUTION_DISABLED"
CAPABILITY_MISSING = "CAPABILITY_MISSING"
CANONICAL_CONFLICT_BLOCKED = "CANONICAL_CONFLICT_BLOCKED"
CONTEXT_FRESH = "FRESH"
CONTEXT_UNAVAILABLE = "UNAVAILABLE"
DEFAULT_MAX_TICKS = 20

RECOVERY_SIDE_EFFECT_NOT_FOUND = "RECOVERY_SIDE_EFFECT_NOT_FOUND"
RECOVERY_SIDE_EFFECT_IDENTITY_MATCH = "RECOVERY_SIDE_EFFECT_IDENTITY_MATCH"
RECOVERY_SIDE_EFFECT_IDENTITY_MISMATCH = "RECOVERY_SIDE_EFFECT_IDENTITY_MISMATCH"
RECOVERY_SIDE_EFFECT_INTEGRITY_FAILURE = "RECOVERY_SIDE_EFFECT_INTEGRITY_FAILURE"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_ALLOWED_CONTEXT_PATHS = frozenset({
    ("trial", "trials", "performance_accessed"),
    ("candidate", "candidates", "performance_accessed"),
})


class AutonomousControlPlaneError(RuntimeError):
    """Fail-closed control-plane error."""

    def __init__(self, code: str, message_zh: str, *, status_code: int = 409, details: Mapping[str, Any] | None = None):
        super().__init__(message_zh)
        self.code = str(code)
        self.message_zh = str(message_zh)
        self.status_code = int(status_code)
        self.details = dict(details or {})

    def envelope(self) -> dict[str, Any]:
        return {"code": self.code, "message_zh": self.message_zh, "details": dict(self.details)}


def _tuple_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(str(item) for item in value if item not in (None, ""))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_identifier(value: Any, *, name: str) -> str:
    result = str(value or "")
    if not _IDENTIFIER_RE.fullmatch(result):
        raise AutonomousControlPlaneError("INVALID_IDENTIFIER", f"{name} 标识不合法", status_code=400)
    return result


def _clock_text(clock: Callable[[], Any]) -> str:
    value = clock()
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


@dataclass(frozen=True)
class ResearchActionV1:
    action_id: str
    action_type: str
    objective_id: str
    candidate_id: str
    candidate_hash: str
    trial_id: str
    required_state: str
    source_state: str
    source_context_id: str
    source_context_hash: str
    source_reconciliation_hash: str
    required_capabilities: tuple[str, ...]
    required_authorities: tuple[str, ...]
    human_confirmation_required: bool
    automatic_execution_allowed: bool
    budget_effect: Mapping[str, Any]
    performance_access_required: bool
    outcome_blind: bool
    idempotency_key: str
    created_at: str
    required_confirmation: str | None = None
    expected_domain_identity: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        action_type: str,
        objective_id: str,
        candidate_id: str = "",
        candidate_hash: str = "",
        trial_id: str = "",
        required_state: str = "",
        source_state: str = "",
        source_context_id: str = "",
        source_context_hash: str = "",
        source_reconciliation_hash: str = "",
        required_capabilities: Iterable[str] = (),
        required_authorities: Iterable[str] = (),
        human_confirmation_required: bool = False,
        automatic_execution_allowed: bool = False,
        budget_effect: Mapping[str, Any] | None = None,
        performance_access_required: bool = False,
        outcome_blind: bool = True,
        idempotency_key: str | None = None,
        created_at: str = "",
        required_confirmation: str | None = None,
        expected_domain_identity: Mapping[str, Any] | None = None,
    ) -> "ResearchActionV1":
        identity = {
            "action_type": str(action_type),
            "objective_id": str(objective_id),
            "candidate_id": str(candidate_id),
            "candidate_hash": str(candidate_hash),
            "trial_id": str(trial_id),
            "required_state": str(required_state),
            "source_state": str(source_state),
            "source_context_id": str(source_context_id),
            "source_context_hash": str(source_context_hash),
            "source_reconciliation_hash": str(source_reconciliation_hash),
            "required_capabilities": sorted(set(_tuple_strings(required_capabilities))),
            "required_authorities": sorted(set(_tuple_strings(required_authorities))),
            "expected_domain_identity": dict(expected_domain_identity or {}),
        }
        stable_id = f"RESEARCH_ACTION_{stable_hash(identity)[:24].upper()}"
        stable_key = idempotency_key or f"AUTONOMOUS_ACTION_{stable_hash(identity)[:32].upper()}"
        return cls(
            action_id=stable_id,
            action_type=str(action_type),
            objective_id=str(objective_id),
            candidate_id=str(candidate_id),
            candidate_hash=str(candidate_hash),
            trial_id=str(trial_id),
            required_state=str(required_state),
            source_state=str(source_state),
            source_context_id=str(source_context_id),
            source_context_hash=str(source_context_hash),
            source_reconciliation_hash=str(source_reconciliation_hash),
            required_capabilities=tuple(sorted(set(_tuple_strings(required_capabilities)))),
            required_authorities=tuple(sorted(set(_tuple_strings(required_authorities)))),
            human_confirmation_required=bool(human_confirmation_required),
            automatic_execution_allowed=bool(automatic_execution_allowed),
            budget_effect=dict(budget_effect or {"mode": "READ_ONLY", "reserved": 0, "consumed": 0}),
            performance_access_required=bool(performance_access_required),
            outcome_blind=bool(outcome_blind),
            idempotency_key=str(stable_key),
            created_at=str(created_at),
            required_confirmation=str(required_confirmation) if required_confirmation else None,
            expected_domain_identity=dict(expected_domain_identity or {}),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResearchActionV1":
        return cls(
            action_id=str(payload.get("action_id") or ""),
            action_type=str(payload.get("action_type") or ""),
            objective_id=str(payload.get("objective_id") or ""),
            candidate_id=str(payload.get("candidate_id") or ""),
            candidate_hash=str(payload.get("candidate_hash") or ""),
            trial_id=str(payload.get("trial_id") or ""),
            required_state=str(payload.get("required_state") or ""),
            source_state=str(payload.get("source_state") or ""),
            source_context_id=str(payload.get("source_context_id") or ""),
            source_context_hash=str(payload.get("source_context_hash") or ""),
            source_reconciliation_hash=str(payload.get("source_reconciliation_hash") or ""),
            required_capabilities=_tuple_strings(payload.get("required_capabilities")),
            required_authorities=_tuple_strings(payload.get("required_authorities")),
            human_confirmation_required=bool(payload.get("human_confirmation_required")),
            automatic_execution_allowed=bool(payload.get("automatic_execution_allowed")),
            budget_effect=dict(payload.get("budget_effect") or {}) if isinstance(payload.get("budget_effect"), Mapping) else {},
            performance_access_required=bool(payload.get("performance_access_required")),
            outcome_blind=bool(payload.get("outcome_blind", True)),
            idempotency_key=str(payload.get("idempotency_key") or ""),
            created_at=str(payload.get("created_at") or ""),
            required_confirmation=str(payload.get("required_confirmation")) if payload.get("required_confirmation") else None,
            expected_domain_identity=dict(payload.get("expected_domain_identity") or {}) if isinstance(payload.get("expected_domain_identity"), Mapping) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "research-action-v1",
            "action_id": self.action_id,
            "action_type": self.action_type,
            "objective_id": self.objective_id,
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "trial_id": self.trial_id,
            "required_state": self.required_state,
            "source_state": self.source_state,
            "source_context_id": self.source_context_id,
            "source_context_hash": self.source_context_hash,
            "source_reconciliation_hash": self.source_reconciliation_hash,
            "required_capabilities": list(self.required_capabilities),
            "required_authorities": list(self.required_authorities),
            "human_confirmation_required": self.human_confirmation_required,
            "automatic_execution_allowed": self.automatic_execution_allowed,
            "budget_effect": dict(self.budget_effect),
            "performance_access_required": self.performance_access_required,
            "outcome_blind": self.outcome_blind,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
            "required_confirmation": self.required_confirmation,
            "expected_domain_identity": dict(self.expected_domain_identity),
        }


@dataclass(frozen=True)
class SideEffectRecoveryEvidenceV1:
    """Canonical, outcome-blind proof used to close a prior STARTED action."""

    matched: bool
    reason_code: str
    action_id: str
    action_type: str
    objective_id: str
    candidate_id: str
    candidate_hash: str
    domain_identity: Mapping[str, Any]
    domain_hash: str
    canonical_refs: Mapping[str, Any]
    reconciliation_hash: str
    safe_to_mark_completed: bool
    side_effect_present: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "matched": self.matched,
            "reason_code": self.reason_code,
            "action_id": self.action_id,
            "action_type": self.action_type,
            "objective_id": self.objective_id,
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "domain_identity": dict(self.domain_identity),
            "domain_hash": self.domain_hash,
            "canonical_refs": dict(self.canonical_refs),
            "reconciliation_hash": self.reconciliation_hash,
            "safe_to_mark_completed": self.safe_to_mark_completed,
            "side_effect_present": self.side_effect_present,
            "outcome_blind": True,
            "performance_data_loaded": False,
            "outcome_fields_available": False,
        }
        PerformanceBlindGuard.assert_blind(payload)
        return payload


@dataclass(frozen=True)
class ResearchActionPermissionV1:
    permission: str
    reason_code: str
    reason_zh: str
    required_authority: str | None
    required_confirmation: str | None
    blocking_state: str | None
    blocking_conflicts: tuple[str, ...]
    budget_check: Mapping[str, Any]
    context_freshness: Mapping[str, Any]
    outcome_blind_check: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "permission": self.permission,
            "reason_code": self.reason_code,
            "reason_zh": self.reason_zh,
            "required_authority": self.required_authority,
            "required_confirmation": self.required_confirmation,
            "blocking_state": self.blocking_state,
            "blocking_conflicts": list(self.blocking_conflicts),
            "budget_check": dict(self.budget_check),
            "context_freshness": dict(self.context_freshness),
            "outcome_blind_check": dict(self.outcome_blind_check),
        }


@dataclass(frozen=True)
class AgentCapabilityV1:
    capability_id: str
    version: str
    description: str
    action_types: tuple[str, ...]
    side_effect_level: str
    requires_human_confirmation: bool
    requires_budget: bool
    requires_performance_access: bool
    outcome_blind_compatible: bool
    read_only: bool
    automatic_execution_allowed: bool
    provider: str
    implementation_ref: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "description": self.description,
            "action_types": list(self.action_types),
            "side_effect_level": self.side_effect_level,
            "requires_human_confirmation": self.requires_human_confirmation,
            "requires_budget": self.requires_budget,
            "requires_performance_access": self.requires_performance_access,
            "outcome_blind_compatible": self.outcome_blind_compatible,
            "read_only": self.read_only,
            "automatic_execution_allowed": self.automatic_execution_allowed,
            "provider": self.provider,
            "implementation_ref": self.implementation_ref,
        }


def _capability(
    capability_id: str,
    action_types: Iterable[str],
    *,
    description: str,
    side_effect_level: str,
    requires_human_confirmation: bool,
    requires_budget: bool,
    requires_performance_access: bool,
    outcome_blind_compatible: bool,
    read_only: bool,
    automatic_execution_allowed: bool,
    implementation_ref: str,
) -> AgentCapabilityV1:
    return AgentCapabilityV1(
        capability_id=capability_id,
        version="v1",
        description=description,
        action_types=tuple(action_types),
        side_effect_level=side_effect_level,
        requires_human_confirmation=requires_human_confirmation,
        requires_budget=requires_budget,
        requires_performance_access=requires_performance_access,
        outcome_blind_compatible=outcome_blind_compatible,
        read_only=read_only,
        automatic_execution_allowed=automatic_execution_allowed,
        provider="chanlun_trader.research_factory",
        implementation_ref=implementation_ref,
    )


class AgentCapabilityRegistryV1:
    """Static capability inventory; capability is never authorization."""

    def __init__(self, capabilities: Iterable[AgentCapabilityV1] | None = None) -> None:
        items = tuple(capabilities) if capabilities is not None else self.default_capabilities()
        self._items = {item.capability_id: item for item in items}

    @staticmethod
    def default_capabilities() -> tuple[AgentCapabilityV1, ...]:
        return (
            _capability("CAN_RECONCILE_OBJECTIVE", (RECONCILE_OBJECTIVE,), description="读取并对账 Objective canonical facts", side_effect_level="NONE", requires_human_confirmation=False, requires_budget=False, requires_performance_access=False, outcome_blind_compatible=True, read_only=True, automatic_execution_allowed=True, implementation_ref="ObjectiveReconciliationServiceV1.reconcile"),
            _capability("CAN_BUILD_SAFE_RUNTIME_CONTEXT", (BUILD_SAFE_RUNTIME_CONTEXT,), description="构建结果盲化 SafeRuntimeContext", side_effect_level="NONE", requires_human_confirmation=False, requires_budget=False, requires_performance_access=False, outcome_blind_compatible=True, read_only=True, automatic_execution_allowed=True, implementation_ref="SafeRuntimeContextBuilderV1.build"),
            _capability("CAN_GENERATE_AI_DESIGN", (GENERATE_AI_DESIGN,), description="描述 AI Design 生成能力，不自动调用 AI", side_effect_level="AI_ARTIFACT", requires_human_confirmation=True, requires_budget=False, requires_performance_access=False, outcome_blind_compatible=True, read_only=False, automatic_execution_allowed=False, implementation_ref="ResearchEvolutionAIDesignServiceV1"),
            _capability("CAN_GENERATE_CANDIDATE_PROPOSAL", (GENERATE_CANDIDATE_PROPOSAL,), description="生成单个 outcome-blind Candidate Proposal", side_effect_level="RESEARCH_ARTIFACT", requires_human_confirmation=False, requires_budget=False, requires_performance_access=False, outcome_blind_compatible=True, read_only=False, automatic_execution_allowed=True, implementation_ref="CandidateGenerationManagerV1.generate_proposal"),
            _capability("CAN_CREATE_MATERIALIZATION_PREVIEW", (CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW,), description="创建不可变 Executable Materialization Preview", side_effect_level="RUNTIME_PREVIEW", requires_human_confirmation=False, requires_budget=False, requires_performance_access=False, outcome_blind_compatible=True, read_only=False, automatic_execution_allowed=True, implementation_ref="CandidateExecutableMaterializationManagerV1.create_preview"),
            _capability("CAN_RECOVER_CONFIRMED_MATERIALIZATION", (RECOVER_EXECUTABLE_MATERIALIZATION,), description="恢复已人工确认的同一执行合同", side_effect_level="CANONICAL_CONTRACT", requires_human_confirmation=False, requires_budget=False, requires_performance_access=False, outcome_blind_compatible=True, read_only=False, automatic_execution_allowed=True, implementation_ref="CandidateExecutableMaterializationManagerV1.recover"),
            _capability("CAN_RUN_STRUCTURAL_PREFLIGHT", (RUN_STRUCTURAL_PREFLIGHT,), description="调用现有 Structural Entry Gate", side_effect_level="STRUCTURAL_RESULT", requires_human_confirmation=True, requires_budget=False, requires_performance_access=False, outcome_blind_compatible=True, read_only=False, automatic_execution_allowed=False, implementation_ref="StructuralEntryServiceV1.start"),
            _capability("CAN_START_PREDICTIVE_TRIAL", (START_PREDICTIVE_TRIAL,), description="描述 Predictive Trial 启动能力，本轮不自动调用", side_effect_level="TRIAL", requires_human_confirmation=True, requires_budget=True, requires_performance_access=True, outcome_blind_compatible=False, read_only=False, automatic_execution_allowed=False, implementation_ref="PredictiveTrialStartServiceV1"),
            _capability("CAN_ACCESS_PERFORMANCE", (WAIT_FOR_TRIAL_RESULT,), description="描述性能读取能力，本轮禁止调用", side_effect_level="PERFORMANCE", requires_human_confirmation=True, requires_budget=True, requires_performance_access=True, outcome_blind_compatible=False, read_only=False, automatic_execution_allowed=False, implementation_ref="PerformanceAccessGate"),
            _capability("CAN_RUN_FINAL_TEST", (), description="描述 Final Test 能力，本轮禁止调用", side_effect_level="FINAL", requires_human_confirmation=True, requires_budget=True, requires_performance_access=True, outcome_blind_compatible=False, read_only=False, automatic_execution_allowed=False, implementation_ref="FinalTestBoundary"),
            _capability("CAN_RUN_PROSPECTIVE", (), description="描述 Prospective 能力，本轮禁止调用", side_effect_level="PROSPECTIVE", requires_human_confirmation=True, requires_budget=True, requires_performance_access=True, outcome_blind_compatible=False, read_only=False, automatic_execution_allowed=False, implementation_ref="ProspectiveBoundary"),
            _capability("CAN_CREATE_REAL_ORDER", (), description="描述真实订单能力，本轮禁止调用", side_effect_level="REAL_ORDER", requires_human_confirmation=True, requires_budget=True, requires_performance_access=True, outcome_blind_compatible=False, read_only=False, automatic_execution_allowed=False, implementation_ref="RealOrderBoundary"),
        )

    def get(self, capability_id: str) -> AgentCapabilityV1 | None:
        return self._items.get(str(capability_id))

    def supports(self, action: ResearchActionV1) -> tuple[bool, tuple[str, ...]]:
        missing = tuple(capability_id for capability_id in action.required_capabilities if capability_id not in self._items)
        for capability_id in action.required_capabilities:
            capability = self._items.get(capability_id)
            if capability is not None and action.action_type not in capability.action_types:
                missing += (capability_id,)
        return not missing, tuple(sorted(set(missing)))

    def available_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "agent-capability-registry-v1",
            "capabilities": [self._items[key].to_dict() for key in sorted(self._items)],
            "read_only": True,
            "outcome_blind": True,
        }


@dataclass(frozen=True)
class AutonomousResearchDecisionV1:
    decision_id: str
    objective_id: str
    source_reconciliation_hash: str
    source_context_id: str
    source_context_hash: str
    current_effective_state: str
    available_capabilities: tuple[str, ...]
    candidate_actions: tuple[Mapping[str, Any], ...]
    selected_action: Mapping[str, Any]
    permission: Mapping[str, Any]
    blocking_reasons: tuple[str, ...]
    requires_human: bool
    automatic_execution: bool
    decision_timestamp: str
    outcome_blind: bool
    decision_hash: str
    control_state: str

    @classmethod
    def build(
        cls,
        *,
        objective_id: str,
        source_reconciliation_hash: str,
        source_context_id: str,
        source_context_hash: str,
        current_effective_state: str,
        available_capabilities: Iterable[str],
        candidate_actions: Iterable[Mapping[str, Any]],
        selected_action: Mapping[str, Any],
        permission: Mapping[str, Any],
        blocking_reasons: Iterable[str],
        requires_human: bool,
        automatic_execution: bool,
        decision_timestamp: str,
        control_state: str,
    ) -> "AutonomousResearchDecisionV1":
        base = {
            "schema_version": "autonomous-research-decision-v1",
            "objective_id": objective_id,
            "source_reconciliation_hash": source_reconciliation_hash,
            "source_context_id": source_context_id,
            "source_context_hash": source_context_hash,
            "current_effective_state": current_effective_state,
            "available_capabilities": sorted(set(_tuple_strings(available_capabilities))),
            "candidate_actions": [dict(item) for item in candidate_actions],
            "selected_action": dict(selected_action),
            "permission": dict(permission),
            "blocking_reasons": sorted(set(_tuple_strings(blocking_reasons))),
            "requires_human": bool(requires_human),
            "automatic_execution": bool(automatic_execution),
            "decision_timestamp": decision_timestamp,
            "outcome_blind": True,
            "control_state": control_state,
        }
        decision_id = f"RESEARCH_DECISION_{stable_hash(base)[:24].upper()}"
        payload = {**base, "decision_id": decision_id}
        return cls(
            decision_id=decision_id,
            objective_id=objective_id,
            source_reconciliation_hash=source_reconciliation_hash,
            source_context_id=source_context_id,
            source_context_hash=source_context_hash,
            current_effective_state=current_effective_state,
            available_capabilities=tuple(base["available_capabilities"]),
            candidate_actions=tuple(base["candidate_actions"]),
            selected_action=base["selected_action"],
            permission=base["permission"],
            blocking_reasons=tuple(base["blocking_reasons"]),
            requires_human=bool(requires_human),
            automatic_execution=bool(automatic_execution),
            decision_timestamp=decision_timestamp,
            outcome_blind=True,
            decision_hash=stable_hash(payload),
            control_state=control_state,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": "autonomous-research-decision-v1",
            "decision_id": self.decision_id,
            "objective_id": self.objective_id,
            "source_reconciliation_hash": self.source_reconciliation_hash,
            "source_context_id": self.source_context_id,
            "source_context_hash": self.source_context_hash,
            "current_effective_state": self.current_effective_state,
            "available_capabilities": list(self.available_capabilities),
            "candidate_actions": [dict(item) for item in self.candidate_actions],
            "selected_action": dict(self.selected_action),
            "permission": dict(self.permission),
            "blocking_reasons": list(self.blocking_reasons),
            "requires_human": self.requires_human,
            "automatic_execution": self.automatic_execution,
            "decision_timestamp": self.decision_timestamp,
            "outcome_blind": self.outcome_blind,
            "control_state": self.control_state,
        }
        payload["decision_hash"] = self.decision_hash
        return payload


_CAPABILITY_BY_ACTION: dict[str, tuple[str, ...]] = {
    RECONCILE_OBJECTIVE: ("CAN_RECONCILE_OBJECTIVE",),
    BUILD_SAFE_RUNTIME_CONTEXT: ("CAN_BUILD_SAFE_RUNTIME_CONTEXT",),
    GENERATE_AI_DESIGN: ("CAN_GENERATE_AI_DESIGN",),
    GENERATE_CANDIDATE_PROPOSAL: ("CAN_GENERATE_CANDIDATE_PROPOSAL",),
    CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW: ("CAN_CREATE_MATERIALIZATION_PREVIEW",),
    RECOVER_EXECUTABLE_MATERIALIZATION: ("CAN_RECOVER_CONFIRMED_MATERIALIZATION",),
    RUN_STRUCTURAL_PREFLIGHT: ("CAN_RUN_STRUCTURAL_PREFLIGHT",),
    START_PREDICTIVE_TRIAL: ("CAN_START_PREDICTIVE_TRIAL",),
}

_AUTHORITIES_BY_ACTION: dict[str, tuple[str, ...]] = {
    RECONCILE_OBJECTIVE: ("OBJECTIVE_RECONCILIATION_SERVICE",),
    BUILD_SAFE_RUNTIME_CONTEXT: ("SAFE_RUNTIME_CONTEXT",),
    GENERATE_AI_DESIGN: ("AI_DESIGN_FACT",),
    GENERATE_CANDIDATE_PROPOSAL: ("AI_DESIGN_APPROVAL_AUTHORITY", "CANDIDATE_PROPOSAL_FACT"),
    CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW: ("CANDIDATE_GOVERNANCE_AUTHORITY",),
    RECOVER_EXECUTABLE_MATERIALIZATION: ("HUMAN_EXECUTABLE_MATERIALIZATION_APPROVAL_AUTHORITY", "EXECUTABLE_CANDIDATE_AUTHORITY"),
    RUN_STRUCTURAL_PREFLIGHT: ("EXECUTABLE_CANDIDATE_AUTHORITY", "STRUCTURAL_ENTRY_GATE"),
    WAIT_FOR_PREDICTIVE_AUTHORIZATION: ("PREDICTIVE_AUTHORIZATION_AUTHORITY",),
    START_PREDICTIVE_TRIAL: ("PREDICTIVE_AUTHORIZATION_AUTHORITY", "TRIAL_LIFECYCLE_AUTHORITY", "SEARCH_BUDGET_AUTHORITY"),
}


class ActionPermissionResolverV1:
    """Resolve current permission after capability and canonical checks."""

    def __init__(self, registry: AgentCapabilityRegistryV1 | None = None) -> None:
        self.registry = registry or AgentCapabilityRegistryV1()

    @staticmethod
    def _context_check(context: SafeRuntimeContextV1 | None) -> tuple[dict[str, Any], bool]:
        if context is None:
            return {"status": CONTEXT_UNAVAILABLE, "valid": False, "changed_components": ["context"]}, False
        try:
            PerformanceBlindGuard.assert_blind(context.to_dict(), allowed_paths=_ALLOWED_CONTEXT_PATHS)
        except PerformanceLeakError:
            return {"status": "OUTCOME_FIELD_BLOCKED", "valid": False, "changed_components": ["outcome_blind"]}, False
        return {"status": CONTEXT_FRESH, "valid": True, "changed_components": []}, True

    def resolve(
        self,
        action: ResearchActionV1,
        report: Mapping[str, Any],
        context: SafeRuntimeContextV1 | None,
        *,
        predictive_authorized: bool = False,
    ) -> ResearchActionPermissionV1:
        state = str(report.get("effective_state") or "UNKNOWN")
        conflicts = tuple(sorted(str(item) for item in report.get("conflicts", ()) if item))
        budget = _mapping(context.get("budget") if context is not None else {})
        remaining = budget.get("remaining")
        budget_check: dict[str, Any] = {
            "authority_status": budget.get("authority_status", "UNAVAILABLE"),
            "total": budget.get("total"),
            "used": budget.get("used"),
            "reserved": budget.get("reserved"),
            "remaining": remaining,
            "plan_only": True,
            "side_effect_performed": False,
        }
        context_freshness, context_ok = self._context_check(context)
        outcome_check = {
            "planner_inputs_blind": context_ok,
            "performance_data_loaded": False,
            "performance_access_blocked": True,
        }
        required_authority = (_AUTHORITIES_BY_ACTION.get(action.action_type) or (None,))[0]
        required_confirmation = action.required_confirmation

        def decision(permission: str, code: str, message: str, *, blocking_state: str | None = state, blocking: Iterable[str] = conflicts, outcome_ok: bool | None = None) -> ResearchActionPermissionV1:
            if outcome_ok is not None:
                outcome_check["planner_inputs_blind"] = bool(outcome_ok)
            return ResearchActionPermissionV1(
                permission=permission,
                reason_code=code,
                reason_zh=message,
                required_authority=required_authority,
                required_confirmation=required_confirmation,
                blocking_state=blocking_state,
                blocking_conflicts=tuple(sorted(set(str(item) for item in blocking if item))),
                budget_check=dict(budget_check),
                context_freshness=dict(context_freshness),
                outcome_blind_check=dict(outcome_check),
            )

        canonical_conflict = str(report.get("conflict_level") or "") == CANONICAL_CONFLICT or CANONICAL_CONFLICT in conflicts
        if canonical_conflict:
            return decision(DENY, CANONICAL_CONFLICT_BLOCKED, "Canonical 事实存在冲突，控制平面停止且不执行任何动作。", blocking=(*conflicts, CANONICAL_CONFLICT))
        if action.action_type == BLOCKED:
            return decision(DENY, "NO_ALLOWED_ACTION", "当前没有满足治理边界的可执行动作。")
        if not context_ok and action.action_type not in {RECONCILE_OBJECTIVE, BUILD_SAFE_RUNTIME_CONTEXT}:
            code = STALE_RUNTIME_CONTEXT if context_freshness.get("status") == "OUTCOME_FIELD_BLOCKED" else "SAFE_RUNTIME_CONTEXT_REQUIRED"
            return decision(DENY, code, "SafeRuntimeContext 不可用或不新鲜，控制平面已 fail closed。", blocking=(code,))
        supported, missing = self.registry.supports(action)
        if not supported:
            return decision(DENY, CAPABILITY_MISSING, "Agent capability 不足；Capability 不等于 Authorization。", blocking=(*missing, CAPABILITY_MISSING))

        if action.action_type == GENERATE_AI_DESIGN:
            return decision(ALLOW_MANUAL_ONLY, "AI_DESIGN_GENERATION_MANUAL_ONLY", "AI Design 生成仍需独立治理边界，控制平面不会自动调用 AI。")
        if action.action_type in {
            WAIT_FOR_AI_DESIGN_CONFIRMATION,
            WAIT_FOR_CANDIDATE_GOVERNANCE_FREEZE,
            WAIT_FOR_EXECUTABLE_MATERIALIZATION_CONFIRMATION,
            WAIT_FOR_PREDICTIVE_AUTHORIZATION,
            WAIT_FOR_TRIAL_RESULT,
            RECONCILE_TRIAL,
        }:
            return decision(ALLOW_MANUAL_ONLY, "HUMAN_CONFIRMATION_REQUIRED", "当前动作必须等待人工治理确认，控制平面不会代替审批人。")
        if action.action_type == RUN_STRUCTURAL_PREFLIGHT:
            return decision(ALLOW_MANUAL_ONLY, "STRUCTURAL_START_CONFIRMATION_REQUIRED", "Structural Entry 需要现有显式确认边界，控制平面不会自我确认。")
        if action.action_type == START_PREDICTIVE_TRIAL:
            if not predictive_authorized:
                return decision(DENY, PREDICTIVE_AUTHORIZATION_REQUIRED, "缺少 canonical Predictive Authorization，不能启动 Trial。", blocking=(PREDICTIVE_AUTHORIZATION_REQUIRED,))
            return decision(DENY, PHASE2_PREDICTIVE_EXECUTION_DISABLED, "Phase 2 控制平面只呈现 Predictive Trial 动作，不执行真实 Trial。", blocking=(PHASE2_PREDICTIVE_EXECUTION_DISABLED,))
        if action.action_type == STOP_OBJECTIVE:
            return decision(DENY, "STOP_CONDITION", "当前 Objective 已达到停止条件，控制平面不会追加动作。")
        if action.action_type in {GENERATE_CANDIDATE_PROPOSAL, CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW, RECOVER_EXECUTABLE_MATERIALIZATION}:
            if not action.automatic_execution_allowed:
                return decision(DENY, "AUTOMATIC_EXECUTION_NOT_ALLOWED", "当前动作未被声明为可自动执行。")
            capability = self.registry.get(action.required_capabilities[0]) if action.required_capabilities else None
            if capability is None or not capability.automatic_execution_allowed:
                return decision(DENY, "CAPABILITY_AUTOMATION_NOT_ALLOWED", "Capability 存在但没有自动执行授权。")
            expected_states = {
                GENERATE_CANDIDATE_PROPOSAL: {AI_DESIGN_APPROVED},
                CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW: {CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION},
                RECOVER_EXECUTABLE_MATERIALIZATION: {EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED},
            }[action.action_type]
            if state not in expected_states:
                return decision(DENY, "CANONICAL_STATE_NOT_READY", "Canonical state 尚未允许当前自动动作。", blocking=(state,))
            return decision(ALLOW_AUTOMATIC, "PERMITTED_AUTOMATIC_ACTION", "当前 Canonical state、SafeRuntimeContext、Capability 和治理权限均满足。")
        if action.action_type in {RECONCILE_OBJECTIVE, BUILD_SAFE_RUNTIME_CONTEXT}:
            return decision(ALLOW_AUTOMATIC, "READ_ONLY_RECONCILIATION", "只读对账或上下文构建允许自动执行。", blocking_state=None)
        return decision(DENY, "UNSUPPORTED_ACTION", "控制平面不支持该动作，已停止。")


class AutonomousResearchControlPlaneV1:
    """One-action-per-tick, reconciliation-first control plane."""

    def __init__(
        self,
        root: str | Path,
        *,
        capability_registry: AgentCapabilityRegistryV1 | None = None,
        clock: Callable[[], Any] = now_timestamp,
        max_ticks: int = DEFAULT_MAX_TICKS,
        execution_policy: ExecutionPolicy | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.execution_policy = execution_policy or ExecutionPolicy()
        self.clock = clock
        self.max_ticks = int(max_ticks)
        if self.max_ticks <= 0:
            raise ValueError("max_ticks must be positive")
        self.capability_registry = capability_registry or AgentCapabilityRegistryV1()
        self.permission_resolver = ActionPermissionResolverV1(self.capability_registry)
        self.reconciliation = ObjectiveReconciliationServiceV1(self.root)
        self.context_builder = SafeRuntimeContextBuilderV1(self.root, clock=clock)
        self._mutex = threading.RLock()

    def _journal(self, objective_id: str) -> AutonomousActionExecutionJournalV1:
        return AutonomousActionExecutionJournalV1(self.root, objective_id, clock=lambda: _clock_text(self.clock))

    def _lock(self, objective_id: str) -> ObjectiveMutationLock:
        return ObjectiveMutationLock(self.root, "objective:" + objective_id)

    def _require_execution_policy(self) -> None:
        if not self.execution_policy.governance_allowed:
            raise AutonomousControlPlaneError("EXECUTION_POLICY_READ_ONLY", "恢复和执行需要显式 GOVERNED + SYNTHETIC 策略。")
        validate_research_root(self.root, self.execution_policy)
        temporary_root = Path(tempfile.gettempdir()).resolve()
        if self.root == temporary_root or not self.root.is_relative_to(temporary_root):
            raise AutonomousControlPlaneError("SYNTHETIC_TEMPORARY_WORKSPACE_REQUIRED", "本轮受管执行仅允许独立临时工作区。")

    def _read_only(self, objective_id: str, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        def unavailable(code: str) -> dict[str, Any]:
            return {"objective_id": objective_id, "execution_status": DENY, "reason_code": code, "stop_reason": code, "control_state": CONTROL_BLOCKED, "decision": {}, "context": {}, "budget": {}, "execution": None, "continue_loop": False, "outcome_blind": True, "safe_to_advance": False}
        try:
            lock = self._lock(objective_id)
            lock.probe()
            before = self.reconciliation.reconcile(objective_id).get("report_hash")
            journal_before = self._journal(objective_id).snapshot_hash()
            result = operation()
            lock.probe()
            if before != self.reconciliation.reconcile(objective_id).get("report_hash") or journal_before != self._journal(objective_id).snapshot_hash():
                return unavailable("READ_SNAPSHOT_STALE")
            return result
        except (MutationBusyError, AutonomousActionJournalError, AutonomousControlPlaneError) as exc:
            return unavailable("CONTROL_PLANE_CONCURRENT_RUN" if isinstance(exc, MutationBusyError) else getattr(exc, "code", str(exc)))

    @staticmethod
    def _action_semantics(action: ResearchActionV1) -> dict[str, Any]:
        return {key: value for key, value in action.to_dict().items() if key != "created_at"}

    def _validate_action(self, action: ResearchActionV1) -> None:
        PerformanceBlindGuard.assert_blind(action.to_dict())
        capabilities, authorities, automatic, manual, performance, confirmation = self._action_requirements(action.action_type)
        source_state = {
            GENERATE_CANDIDATE_PROPOSAL: AI_DESIGN_APPROVED,
            CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW: CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION,
            RECOVER_EXECUTABLE_MATERIALIZATION: EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED,
        }.get(action.action_type)
        expected = ResearchActionV1.create(
            **{key: value for key, value in action.to_dict().items() if key not in {"schema_version", "action_id", "idempotency_key"}}
        )
        if (
            self._action_semantics(expected) != self._action_semantics(action)
            or tuple(sorted(capabilities)) != action.required_capabilities
            or tuple(sorted(authorities)) != action.required_authorities
            or automatic != action.automatic_execution_allowed
            or manual != action.human_confirmation_required
            or performance != action.performance_access_required
            or confirmation != action.required_confirmation
            or not action.outcome_blind
            or (automatic and (action.trial_id or dict(action.budget_effect) != {"mode": "PLAN_ONLY", "reserved": 0, "consumed": 0, "execution_requires_canonical_budget": False}))
            or (automatic and (action.source_state != source_state or action.required_state != source_state or action.expected_domain_identity.get("objective_id") != action.objective_id))
            or (automatic and action.action_type != GENERATE_CANDIDATE_PROPOSAL and (action.candidate_id != action.expected_domain_identity.get("candidate_id") or action.candidate_hash != action.expected_domain_identity.get("candidate_hash")))
        ):
            raise AutonomousControlPlaneError("ACTION_IDENTITY_INVALID", "Action 身份或权限声明不符合受信规划规则。")

    def _build_context(self, objective_id: str) -> tuple[SafeRuntimeContextV1 | None, dict[str, Any]]:
        try:
            context = self.context_builder.build(objective_id, purpose="RUNTIME")
            PerformanceBlindGuard.assert_blind(context.to_dict(), allowed_paths=_ALLOWED_CONTEXT_PATHS)
            return context, {"status": CONTEXT_FRESH, "valid": True, "context_id": context.context_id, "context_hash": context.context_hash, "changed_components": []}
        except (SafeRuntimeContextError, PerformanceLeakError) as exc:
            code = exc.code if isinstance(exc, SafeRuntimeContextError) else "OUTCOME_FIELD_BLOCKED"
            details = dict(exc.details) if isinstance(exc, SafeRuntimeContextError) else {"changed_components": ["outcome_blind"]}
            return None, {"status": code, "valid": False, "context_id": "", "context_hash": "", **details}

    @staticmethod
    def _effective_state(report: Mapping[str, Any]) -> str:
        effective = report.get("effective_objective_state") if isinstance(report.get("effective_objective_state"), Mapping) else {}
        return str(effective.get("effective_state") or report.get("effective_state") or "UNKNOWN")

    @staticmethod
    def _candidate_identity(report: Mapping[str, Any], context: SafeRuntimeContextV1 | None) -> tuple[str, str]:
        candidate = _mapping(context.get("candidate") if context is not None else {})
        candidate_id = str(report.get("current_candidate_id") or candidate.get("current_candidate_id") or "")
        candidate_hash = str(report.get("current_candidate_hash") or candidate.get("current_candidate_hash") or "")
        return candidate_id, candidate_hash

    @staticmethod
    def _reference_values(report: Mapping[str, Any], key: str) -> tuple[str, ...]:
        refs = report.get("canonical_refs") if isinstance(report.get("canonical_refs"), Mapping) else {}
        value = refs.get(key)
        if isinstance(value, str):
            return (value,) if value else ()
        if isinstance(value, (list, tuple, set, frozenset)):
            return tuple(sorted({str(item) for item in value if item}))
        return ()

    def _canonical_payloads(
        self,
        objective_id: str,
        report: Mapping[str, Any],
        category: str,
        names: Iterable[str],
    ) -> tuple[tuple[str, Mapping[str, Any] | None], ...]:
        wanted = {str(name) for name in names}
        refs = list(self._reference_values(report, category))
        known = {
            "candidate_governance": (
                f"reports/research_candidates/proposals/{objective_id}/CANDIDATE_PROPOSAL.json",
                f"reports/research_candidates/proposals/{objective_id}/CANDIDATE_FREEZE_RECEIPT.json",
            ),
            "executable_materialization": (
                f"reports/research_candidates/proposals/{objective_id}/EXECUTABLE_MATERIALIZATION_PREVIEW.json",
                f"reports/research_candidates/proposals/{objective_id}/EXECUTABLE_MATERIALIZATION_CONFIRMATION.json",
            ),
            "ai_design": (f"reports/research_evolution/ai_design/{objective_id}/AI_RESEARCH_DESIGN_PROPOSAL.json",),
            "ai_design_approval": (f"reports/research_evolution/ai_design/{objective_id}/AI_DESIGN_APPROVAL_RECEIPT.json",),
        }.get(category, ())
        refs.extend(known)
        results: list[tuple[str, Mapping[str, Any] | None]] = []
        seen: set[str] = set()
        for ref in refs:
            path = (self.root / str(ref)).resolve()
            try:
                path.relative_to(self.root)
            except ValueError:
                continue
            if path.name not in wanted or str(path) in seen or not path.exists():
                continue
            seen.add(str(path))
            payload: Mapping[str, Any] | None = None
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, Mapping):
                    payload = raw
            except (OSError, UnicodeError, json.JSONDecodeError):
                payload = None
            results.append((path.relative_to(self.root).as_posix(), payload))
        return tuple(results)

    def _recovery_canonical_refs(self, report: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: list(self._reference_values(report, key))
            for key in (
                "ai_design",
                "ai_design_approval",
                "candidate_governance",
                "executable_materialization",
                "durable_frozen_contract",
            )
            if self._reference_values(report, key)
        }

    def _current_ai_design_identity(self, objective_id: str, report: Mapping[str, Any]) -> dict[str, str]:
        ai_view = _mapping(report.get("ai_design_reconciliation"))
        approval = _mapping(ai_view.get("approval"))
        receipt = _mapping(approval.get("receipt"))
        design_payload = next(
            (payload for _, payload in self._canonical_payloads(objective_id, report, "ai_design", {"AI_RESEARCH_DESIGN_PROPOSAL.json"}) if payload is not None),
            {},
        )
        approval_payload = next(
            (payload for _, payload in self._canonical_payloads(objective_id, report, "ai_design_approval", {"AI_DESIGN_APPROVAL_RECEIPT.json"}) if payload is not None),
            {},
        )
        return {
            "ai_design_id": str(ai_view.get("design_id") or design_payload.get("design_id") or ""),
            "ai_design_hash": str(ai_view.get("design_hash") or design_payload.get("design_hash") or ""),
            "ai_design_approval_id": str(approval.get("approval_id") or approval_payload.get("approval_id") or receipt.get("approval_id") or ""),
            "ai_design_approval_hash": str(receipt.get("receipt_hash") or approval_payload.get("receipt_hash") or ""),
            "design_source_context_id": str(design_payload.get("source_context_id") or receipt.get("source_context_id") or ""),
            "design_source_context_hash": str(design_payload.get("source_context_hash") or design_payload.get("input_context_hash") or receipt.get("source_context_hash") or ""),
        }

    def _proposal_payload(self, objective_id: str, report: Mapping[str, Any]) -> Mapping[str, Any] | None:
        return next(
            (payload for _, payload in self._canonical_payloads(objective_id, report, "candidate_governance", {"CANDIDATE_PROPOSAL.json"}) if payload is not None),
            None,
        )

    @staticmethod
    def _proposal_identity(proposal: Mapping[str, Any], ai_identity: Mapping[str, Any]) -> dict[str, Any]:
        source_hashes = _mapping(proposal.get("source_hashes"))
        lineage = _mapping(proposal.get("lineage"))
        candidate_hash = CandidateGenerationManagerV1._candidate_hash_for_proposal(proposal)  # noqa: SLF001 - derive the canonical preview identity
        return {
            "objective_id": str(proposal.get("objective_id") or ""),
            "candidate_id": CandidateGenerationManagerV1._candidate_id_for_hash(candidate_hash),  # noqa: SLF001 - derive the canonical preview identity
            "candidate_hash": candidate_hash,
            "proposal_id": str(proposal.get("proposal_id") or ""),
            "proposal_hash": str(proposal.get("proposal_hash") or ""),
            "ai_design_id": str(proposal.get("ai_research_design_id") or ""),
            "ai_design_hash": str(source_hashes.get("ai_research_design") or ""),
            "ai_design_approval_id": str(proposal.get("ai_design_approval_id") or ""),
            "ai_design_approval_hash": str(proposal.get("ai_design_approval_receipt_hash") or ""),
            "design_source_context_id": str(ai_identity.get("design_source_context_id") or ""),
            "design_source_context_hash": str(source_hashes.get("ai_research_design_input") or ai_identity.get("design_source_context_hash") or ""),
            "source_context_id": str(proposal.get("source_context_id") or ""),
            "source_context_hash": str(proposal.get("source_context_hash") or ""),
            "domain_source_context_id": str(proposal.get("source_context_id") or ""),
            "domain_source_context_hash": str(proposal.get("source_context_hash") or ""),
            "input_context_hash": str(proposal.get("input_context_hash") or ""),
            "lineage_id": str(lineage.get("lineage_id") or ""),
        }

    @staticmethod
    def _proposal_is_valid(proposal: Mapping[str, Any], objective_id: str) -> bool:
        try:
            PerformanceBlindGuard.assert_blind(proposal)
            identity = CandidateGenerationManagerV1._identity(proposal)  # noqa: SLF001 - canonical proposal identity validation
        except (PerformanceLeakError, AttributeError, TypeError):
            return False
        proposal_hash = str(proposal.get("proposal_hash") or "")
        proposal_id = str(proposal.get("proposal_id") or "")
        source_context_id = str(proposal.get("source_context_id") or "")
        source_context_hash = str(proposal.get("source_context_hash") or "")
        source_hashes = _mapping(proposal.get("source_hashes"))
        return bool(
            str(proposal.get("objective_id") or "") == objective_id
            and str(proposal.get("status") or "") == CANDIDATE_PROPOSAL_READY
            and proposal_hash
            and proposal_hash == stable_hash(identity)
            and proposal_id == f"CANDIDATE_PROPOSAL_{proposal_hash[:24].upper()}"
            and source_context_id
            and source_context_hash
            and str(proposal.get("input_context_hash") or source_context_hash) == source_context_hash
            and str(source_hashes.get("safe_runtime_context") or "") == source_context_hash
            and str(source_hashes.get("ai_research_design_input") or "")
        )

    @staticmethod
    def _identity_matches(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
        if not expected:
            return False
        for key, value in expected.items():
            if value in (None, "", (), [], {}, False) or actual.get(key) in (None, "", (), [], {}, False):
                return False
            if actual.get(key) != value:
                return False
        return True

    def _expected_domain_identity(self, objective_id: str, report: Mapping[str, Any], action_type: str) -> dict[str, Any]:
        ai_identity = self._current_ai_design_identity(objective_id, report)
        if action_type == GENERATE_CANDIDATE_PROPOSAL:
            try:
                proposal_context = self.context_builder.build(objective_id, purpose="CANDIDATE_PROPOSAL")
                proposal_context_identity = {
                    "domain_source_context_id": proposal_context.context_id,
                    "domain_source_context_hash": proposal_context.context_hash,
                }
            except SafeRuntimeContextError:
                proposal_context_identity = {
                    "domain_source_context_id": "",
                    "domain_source_context_hash": "",
                }
            return {"objective_id": objective_id, **ai_identity, **proposal_context_identity}

        proposal = self._proposal_payload(objective_id, report)
        candidate_id, candidate_hash = self._candidate_identity(report, None)
        base = {"objective_id": objective_id, **ai_identity, "candidate_id": candidate_id, "candidate_hash": candidate_hash}
        if isinstance(proposal, Mapping):
            base.update({
                "proposal_id": str(proposal.get("proposal_id") or ""),
                "proposal_hash": str(proposal.get("proposal_hash") or ""),
            })
        if action_type == CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW:
            freeze = next(
                (payload for _, payload in self._canonical_payloads(objective_id, report, "candidate_governance", {"CANDIDATE_FREEZE_RECEIPT.json"}) if payload is not None),
                {},
            )
            base["freeze_receipt_hash"] = str(freeze.get("receipt_hash") or "")
            base["source_context_id"] = ai_identity.get("design_source_context_id", "")
            base["source_context_hash"] = ai_identity.get("design_source_context_hash", "")
            return base
        if action_type == RECOVER_EXECUTABLE_MATERIALIZATION:
            materialization = _mapping(_mapping(report.get("candidate_reconciliation")).get("materialization"))
            preview = _mapping(materialization.get("preview"))
            confirmation = _mapping(materialization.get("confirmation"))
            base.update({
                "preview_id": str(preview.get("preview_id") or ""),
                "preview_hash": str(preview.get("preview_hash") or ""),
                "confirmation_id": str(confirmation.get("confirmation_id") or ""),
                "confirmation_hash": str(confirmation.get("receipt_hash") or ""),
                "durable_contract_hash": str(preview.get("durable_contract_hash") or ""),
                "source_context_id": str(preview.get("source_context_id") or ""),
                "source_context_hash": str(preview.get("source_context_hash") or ""),
                "ai_design_id": str(preview.get("ai_design_id") or ai_identity.get("ai_design_id") or ""),
                "ai_design_hash": str(preview.get("ai_design_hash") or ai_identity.get("ai_design_hash") or ""),
                "ai_design_approval_hash": str(preview.get("ai_design_approval_hash") or ai_identity.get("ai_design_approval_hash") or ""),
            })
            return base
        return {}

    def _predictive_authorization(self, objective_id: str, candidate_id: str, candidate_hash: str) -> dict[str, Any]:
        """Read only allowlisted authorization metadata after Structural PASS."""
        path = self.root / "reports" / "research_orchestrator_v2" / objective_id / "predictive_governance_decisions.jsonl"
        if not path.is_file():
            return {"authorized": False, "source_path": None, "authorization_id": None}
        latest: dict[str, Any] | None = None
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, Mapping):
                    continue
                if str(row.get("objective_id") or objective_id) != objective_id:
                    continue
                if str(row.get("candidate_id") or "") != candidate_id:
                    continue
                if candidate_hash and str(row.get("candidate_hash") or "") != candidate_hash:
                    continue
                status = str(row.get("decision_status") or row.get("status") or "")
                if status in {"AUTHORIZED", "DEFERRED", "ENDED"}:
                    latest = {
                        "authorized": status == "AUTHORIZED",
                        "decision_status": status,
                        "authorization_id": str(row.get("authorization_id") or "") or None,
                        "decision_id": str(row.get("decision_id") or "") or None,
                        "source_path": path.relative_to(self.root).as_posix(),
                    }
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {"authorized": False, "source_path": path.relative_to(self.root).as_posix(), "authorization_id": None, "read_error": True}
        return latest or {"authorized": False, "source_path": path.relative_to(self.root).as_posix(), "authorization_id": None}

    def _action_type(self, report: Mapping[str, Any], *, predictive_authorized: bool) -> str:
        if str(report.get("conflict_level") or "") == CANONICAL_CONFLICT or CANONICAL_CONFLICT in {str(item) for item in report.get("conflicts", ())}:
            return BLOCKED
        state = self._effective_state(report)
        if state in {BUDGET_AUTHORITY_AMBIGUOUS, BUDGET_EXHAUSTED, ENGINEERING_BLOCKED, STRUCTURAL_BLOCKED, EXECUTABLE_CONTRACT_INVALID}:
            return STOP_OBJECTIVE if state == BUDGET_EXHAUSTED else BLOCKED
        if state == NEED_AI_RESEARCH_DESIGN:
            return GENERATE_AI_DESIGN
        if state == AI_DESIGN_AWAITING_CONFIRMATION:
            return WAIT_FOR_AI_DESIGN_CONFIRMATION
        if state == AI_DESIGN_APPROVED:
            return GENERATE_CANDIDATE_PROPOSAL
        if state == CANDIDATE_PROPOSAL_READY:
            return WAIT_FOR_CANDIDATE_GOVERNANCE_FREEZE
        if state == CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION:
            return CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW
        if state in {EXECUTABLE_MATERIALIZATION_PREVIEW_READY, EXECUTABLE_MATERIALIZATION_CONFIRMATION_MISSING}:
            return WAIT_FOR_EXECUTABLE_MATERIALIZATION_CONFIRMATION
        if state == EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED:
            return RECOVER_EXECUTABLE_MATERIALIZATION
        if state == READY_FOR_STRUCTURAL_PREFLIGHT:
            return RUN_STRUCTURAL_PREFLIGHT
        if state == STRUCTURAL_RUNNING:
            return BLOCKED
        if state == PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED:
            return START_PREDICTIVE_TRIAL if predictive_authorized else WAIT_FOR_PREDICTIVE_AUTHORIZATION
        if state == TRIAL_ACTIVE:
            return WAIT_FOR_TRIAL_RESULT
        if state == TRIAL_TERMINAL:
            return RECONCILE_TRIAL
        if state in {"COMPLETED", "STOPPED", "INVALIDATED"}:
            return STOP_OBJECTIVE
        required = str(report.get("required_action") or "")
        if required == GENERATE_CANDIDATE_PROPOSAL:
            return GENERATE_CANDIDATE_PROPOSAL
        return BLOCKED

    @staticmethod
    def _action_requirements(action_type: str) -> tuple[tuple[str, ...], tuple[str, ...], bool, bool, bool, str | None]:
        capabilities = _CAPABILITY_BY_ACTION.get(action_type, ())
        authorities = _AUTHORITIES_BY_ACTION.get(action_type, ())
        automatic = action_type in {GENERATE_CANDIDATE_PROPOSAL, CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW, RECOVER_EXECUTABLE_MATERIALIZATION}
        manual = action_type in {
            GENERATE_AI_DESIGN,
            WAIT_FOR_AI_DESIGN_CONFIRMATION,
            WAIT_FOR_CANDIDATE_GOVERNANCE_FREEZE,
            WAIT_FOR_EXECUTABLE_MATERIALIZATION_CONFIRMATION,
            RUN_STRUCTURAL_PREFLIGHT,
            WAIT_FOR_PREDICTIVE_AUTHORIZATION,
            START_PREDICTIVE_TRIAL,
            WAIT_FOR_TRIAL_RESULT,
            RECONCILE_TRIAL,
        }
        performance = action_type in {START_PREDICTIVE_TRIAL, WAIT_FOR_TRIAL_RESULT}
        confirmation = {
            WAIT_FOR_AI_DESIGN_CONFIRMATION: "HUMAN_CONFIRM_AI_RESEARCH_DESIGN",
            WAIT_FOR_CANDIDATE_GOVERNANCE_FREEZE: "HUMAN_REVIEW_CANDIDATE_PROPOSAL",
            WAIT_FOR_EXECUTABLE_MATERIALIZATION_CONFIRMATION: "HUMAN_CONFIRM_EXECUTABLE_MATERIALIZATION",
            RUN_STRUCTURAL_PREFLIGHT: RUN_STRUCTURAL_PREFLIGHT,
            WAIT_FOR_PREDICTIVE_AUTHORIZATION: "AUTHORIZE_PREDICTIVE_TRIAL",
            START_PREDICTIVE_TRIAL: "AUTHORIZE_PREDICTIVE_TRIAL",
        }.get(action_type)
        return capabilities, authorities, automatic, manual, performance, confirmation

    def _make_action(self, objective_id: str, report: Mapping[str, Any], context: SafeRuntimeContextV1 | None, action_type: str, *, predictive_authorized: bool) -> ResearchActionV1:
        candidate_id, candidate_hash = self._candidate_identity(report, context)
        effective_state = self._effective_state(report)
        capabilities, authorities, automatic, manual, performance, confirmation = self._action_requirements(action_type)
        if action_type == START_PREDICTIVE_TRIAL and predictive_authorized:
            authorities = (*authorities, "CANONICAL_PREDICTIVE_AUTHORIZATION_PRESENT")
        return ResearchActionV1.create(
            action_type=action_type,
            objective_id=objective_id,
            candidate_id=candidate_id,
            candidate_hash=candidate_hash,
            trial_id=str(report.get("current_trial_id") or ""),
            required_state=effective_state,
            source_state=effective_state,
            source_context_id=context.context_id if context is not None else "",
            source_context_hash=context.context_hash if context is not None else "",
            source_reconciliation_hash=str(report.get("report_hash") or stable_hash(report)),
            required_capabilities=capabilities,
            required_authorities=authorities,
            human_confirmation_required=manual,
            automatic_execution_allowed=automatic,
            budget_effect={
                "mode": "PLAN_ONLY",
                "reserved": 0,
                "consumed": 0,
                "execution_requires_canonical_budget": action_type == START_PREDICTIVE_TRIAL,
            },
            performance_access_required=performance,
            outcome_blind=True,
            created_at=_clock_text(self.clock),
            required_confirmation=confirmation,
            expected_domain_identity=self._expected_domain_identity(objective_id, report, action_type),
        )

    def _plan(self, objective_id: str, report: Mapping[str, Any], context: SafeRuntimeContextV1 | None) -> tuple[ResearchActionV1, dict[str, Any]]:
        candidate_id, candidate_hash = self._candidate_identity(report, context)
        auth = self._predictive_authorization(objective_id, candidate_id, candidate_hash) if self._effective_state(report) == PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED else {"authorized": False}
        action_type = self._action_type(report, predictive_authorized=bool(auth.get("authorized")))
        action = self._make_action(objective_id, report, context, action_type, predictive_authorized=bool(auth.get("authorized")))
        return action, auth

    @staticmethod
    def _safe_budget(context: SafeRuntimeContextV1 | None) -> dict[str, Any]:
        budget = _mapping(context.get("budget") if context is not None else {})
        return {
            key: budget.get(key)
            for key in ("objective_id", "total", "used", "reserved", "remaining", "authority_status", "registry_head_hash", "testing_family")
            if key in budget
        }

    @staticmethod
    def _safe_reconciliation(report: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "report_hash": str(report.get("report_hash") or ""),
            "effective_state": str(report.get("effective_state") or ""),
            "effective_stage": str(report.get("effective_stage") or ""),
            "required_action": report.get("required_action"),
            "current_candidate_id": report.get("current_candidate_id"),
            "current_candidate_hash": report.get("current_candidate_hash"),
            "current_trial_id": report.get("current_trial_id"),
            "conflict_level": report.get("conflict_level"),
            "conflicts": sorted(str(item) for item in report.get("conflicts", ()) if item),
        }

    @staticmethod
    def _safe_context(context: SafeRuntimeContextV1 | None, freshness: Mapping[str, Any]) -> dict[str, Any]:
        if context is None:
            return {"context_id": "", "context_hash": "", "freshness": dict(freshness), "outcome_blind": True}
        return {
            "context_id": context.context_id,
            "context_hash": context.context_hash,
            "freshness": dict(freshness),
            "outcome_blind": True,
            "performance_data_loaded": False,
        }

    def _decision(
        self,
        objective_id: str,
        report: Mapping[str, Any],
        context: SafeRuntimeContextV1 | None,
        freshness: Mapping[str, Any],
        action: ResearchActionV1,
        permission: ResearchActionPermissionV1,
        *,
        control_state: str,
    ) -> AutonomousResearchDecisionV1:
        blocking = list(permission.blocking_conflicts)
        if permission.permission != ALLOW_AUTOMATIC and permission.reason_code not in blocking:
            blocking.append(permission.reason_code)
        return AutonomousResearchDecisionV1.build(
            objective_id=objective_id,
            source_reconciliation_hash=str(report.get("report_hash") or ""),
            source_context_id=context.context_id if context is not None else "",
            source_context_hash=context.context_hash if context is not None else "",
            current_effective_state=self._effective_state(report),
            available_capabilities=self.capability_registry.available_ids(),
            candidate_actions=(action.to_dict(),),
            selected_action=action.to_dict(),
            permission=permission.to_dict(),
            blocking_reasons=blocking,
            requires_human=permission.permission == ALLOW_MANUAL_ONLY,
            automatic_execution=permission.permission == ALLOW_AUTOMATIC,
            decision_timestamp=_clock_text(self.clock),
            control_state=control_state,
        )

    def _write_decision(self, decision: AutonomousResearchDecisionV1) -> None:
        directory = self.root / "reports" / "research_control_plane" / decision.objective_id
        directory.mkdir(parents=True, exist_ok=True)
        payload = decision.to_dict()
        temporary = directory / ".AUTONOMOUS_RESEARCH_DECISION_V1.json.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        temporary.replace(directory / "AUTONOMOUS_RESEARCH_DECISION_V1.json")
        with (directory / "decision_history.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def _proposal_id(self, objective_id: str) -> str:
        directory = self.root / "reports" / "research_candidates" / "proposals" / objective_id
        paths = sorted(directory.glob("CANDIDATE_PROPOSAL.json")) if directory.is_dir() else []
        if len(paths) != 1:
            raise AutonomousControlPlaneError("CANDIDATE_PROPOSAL_REQUIRED", "当前 Objective 没有唯一 Candidate Proposal，控制平面不执行恢复或预览动作。", details={"count": len(paths)})
        try:
            payload = json.loads(paths[0].read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AutonomousControlPlaneError("CANDIDATE_PROPOSAL_UNREADABLE", "Candidate Proposal 暂时不可读。", status_code=503) from exc
        proposal_id = str(payload.get("proposal_id") or "") if isinstance(payload, Mapping) else ""
        if not proposal_id:
            raise AutonomousControlPlaneError("CANDIDATE_PROPOSAL_INVALID", "Candidate Proposal 缺少稳定身份。", status_code=503)
        return proposal_id

    @staticmethod
    def _result_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
        keys = (
            "proposal_id", "proposal_hash", "preview_id", "preview_hash", "status", "effective_state",
            "candidate_id", "candidate_hash", "durable_contract_hash", "materialization_state",
            "required_action", "output_path", "recovered", "idempotent", "safe_to_advance",
        )
        result = {key: payload.get(key) for key in keys if payload.get(key) is not None}
        PerformanceBlindGuard.assert_blind(result)
        return result

    def _execute_domain_action(self, action: ResearchActionV1) -> dict[str, Any]:
        if action.action_type == GENERATE_CANDIDATE_PROPOSAL:
            result = CandidateGenerationManagerV1(self.root).generate_proposal(action.objective_id)
            return self._result_summary(result)
        if action.action_type == CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW:
            proposal_id = self._proposal_id(action.objective_id)
            result = CandidateExecutableMaterializationManagerV1(self.root).create_preview(action.objective_id, proposal_id)
            return self._result_summary(result)
        if action.action_type == RECOVER_EXECUTABLE_MATERIALIZATION:
            proposal_id = self._proposal_id(action.objective_id)
            result = CandidateExecutableMaterializationManagerV1(self.root).recover(action.objective_id, proposal_id)
            return self._result_summary(result)
        raise AutonomousControlPlaneError("ACTION_NOT_AUTOMATIC", "当前动作不能由控制平面自动执行。")

    @staticmethod
    def _preview_identity(preview: Mapping[str, Any], ai_identity: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "objective_id": str(preview.get("objective_id") or ""),
            "proposal_id": str(preview.get("proposal_id") or ""),
            "proposal_hash": str(preview.get("source_candidate_proposal_hash") or ""),
            "candidate_id": str(preview.get("candidate_id") or ""),
            "candidate_hash": str(preview.get("candidate_hash") or ""),
            "freeze_receipt_hash": str(preview.get("freeze_receipt_hash") or ""),
            "ai_design_id": str(preview.get("ai_design_id") or ""),
            "ai_design_hash": str(preview.get("ai_design_hash") or ""),
            "ai_design_approval_id": str(ai_identity.get("ai_design_approval_id") or ""),
            "ai_design_approval_hash": str(preview.get("ai_design_approval_hash") or ""),
            "source_context_id": str(preview.get("source_context_id") or ""),
            "source_context_hash": str(preview.get("source_context_hash") or ""),
            "design_source_context_id": str(preview.get("source_context_id") or ""),
            "design_source_context_hash": str(preview.get("source_context_hash") or ""),
            "preview_id": str(preview.get("preview_id") or ""),
            "preview_hash": str(preview.get("preview_hash") or ""),
            "durable_contract_hash": str(preview.get("durable_contract_hash") or ""),
        }

    def _recovery_evidence(
        self,
        action: ResearchActionV1,
        report: Mapping[str, Any],
        *,
        matched: bool,
        reason_code: str,
        domain_identity: Mapping[str, Any] | None = None,
        domain_hash: str = "",
        side_effect_present: bool = False,
    ) -> SideEffectRecoveryEvidenceV1:
        domain = dict(domain_identity or {})
        evidence = SideEffectRecoveryEvidenceV1(
            matched=bool(matched),
            reason_code=str(reason_code),
            action_id=action.action_id,
            action_type=action.action_type,
            objective_id=action.objective_id,
            candidate_id=str(domain.get("candidate_id") or action.candidate_id),
            candidate_hash=str(domain.get("candidate_hash") or action.candidate_hash),
            domain_identity=domain,
            domain_hash=str(domain_hash or ""),
            canonical_refs=self._recovery_canonical_refs(report),
            reconciliation_hash=str(report.get("report_hash") or stable_hash(report)),
            safe_to_mark_completed=bool(matched),
            side_effect_present=bool(side_effect_present),
        )
        evidence.to_dict()
        return evidence

    def _reconcile_started_action_side_effect(
        self,
        action: ResearchActionV1,
        report: Mapping[str, Any],
    ) -> SideEffectRecoveryEvidenceV1:
        """Resolve a prior STARTED action against current canonical evidence.

        This resolver deliberately runs before freshness rejection.  It only
        returns ``matched`` when the current domain artifact is bound to the
        action's expected identity and passes the existing canonical validators.
        """

        expected = dict(action.expected_domain_identity or {})
        candidate_view = _mapping(report.get("candidate_reconciliation"))
        materialization = _mapping(candidate_view.get("materialization"))
        ai_identity = self._current_ai_design_identity(action.objective_id, report)

        if action.action_type == GENERATE_CANDIDATE_PROPOSAL:
            entries = self._canonical_payloads(
                action.objective_id,
                report,
                "candidate_governance",
                {"CANDIDATE_PROPOSAL.json"},
            )
            present = bool(entries) or bool(candidate_view.get("proposal_present"))
            if not present:
                return self._recovery_evidence(action, report, matched=False, reason_code=RECOVERY_SIDE_EFFECT_NOT_FOUND)
            for _, proposal in entries:
                if proposal is None or not self._proposal_is_valid(proposal, action.objective_id):
                    continue
                actual = self._proposal_identity(proposal, ai_identity)
                if expected and self._identity_matches(expected, actual):
                    return self._recovery_evidence(
                        action,
                        report,
                        matched=True,
                        reason_code=RECOVERY_SIDE_EFFECT_IDENTITY_MATCH,
                        domain_identity=actual,
                        domain_hash=str(actual.get("proposal_hash") or ""),
                        side_effect_present=True,
                    )
            return self._recovery_evidence(
                action,
                report,
                matched=False,
                reason_code=RECOVERY_SIDE_EFFECT_IDENTITY_MISMATCH,
                side_effect_present=True,
            )

        if action.action_type == CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW:
            preview = materialization.get("preview")
            present = bool(materialization.get("preview_present")) or isinstance(preview, Mapping)
            if not present:
                return self._recovery_evidence(action, report, matched=False, reason_code=RECOVERY_SIDE_EFFECT_NOT_FOUND)
            if not isinstance(preview, Mapping):
                return self._recovery_evidence(action, report, matched=False, reason_code=RECOVERY_SIDE_EFFECT_INTEGRITY_FAILURE, side_effect_present=True)
            preview_check = inspect_materialization_preview(preview)
            actual = self._preview_identity(preview, ai_identity)
            current = materialization.get("preview_valid") is True and not materialization.get("preview_binding_mismatches")
            if preview_check.get("valid") and preview_check.get("ready") and current and expected and self._identity_matches(expected, actual):
                return self._recovery_evidence(
                    action,
                    report,
                    matched=True,
                    reason_code=RECOVERY_SIDE_EFFECT_IDENTITY_MATCH,
                    domain_identity=actual,
                    domain_hash=str(actual.get("preview_hash") or ""),
                    side_effect_present=True,
                )
            return self._recovery_evidence(
                action,
                report,
                matched=False,
                reason_code=RECOVERY_SIDE_EFFECT_IDENTITY_MISMATCH,
                domain_identity=actual,
                domain_hash=str(actual.get("preview_hash") or ""),
                side_effect_present=True,
            )

        if action.action_type == RECOVER_EXECUTABLE_MATERIALIZATION:
            preview = materialization.get("preview")
            confirmation = materialization.get("confirmation")
            contract_present = bool(materialization.get("contract_present"))
            if not contract_present:
                contract_entries = candidate_view.get("durable_contracts")
                if isinstance(contract_entries, (list, tuple)) and any(isinstance(item, Mapping) for item in contract_entries):
                    return self._recovery_evidence(action, report, matched=False, reason_code=RECOVERY_SIDE_EFFECT_IDENTITY_MISMATCH, side_effect_present=True)
                return self._recovery_evidence(action, report, matched=False, reason_code=RECOVERY_SIDE_EFFECT_NOT_FOUND)
            if not isinstance(preview, Mapping) or not isinstance(confirmation, Mapping):
                return self._recovery_evidence(action, report, matched=False, reason_code=RECOVERY_SIDE_EFFECT_INTEGRITY_FAILURE, side_effect_present=True)
            preview_check = inspect_materialization_preview(preview)
            proposal_id = str(expected.get("proposal_id") or preview.get("proposal_id") or "")
            if not proposal_id:
                return self._recovery_evidence(action, report, matched=False, reason_code=RECOVERY_SIDE_EFFECT_IDENTITY_MISMATCH, side_effect_present=True)
            try:
                state = CandidateExecutableMaterializationManagerV1(self.root).read_state(action.objective_id, proposal_id)
            except (CandidateExecutableMaterializationError, OSError, ValueError):
                state = {}
            actual = self._preview_identity(preview, ai_identity)
            actual.update({
                "confirmation_id": str(confirmation.get("confirmation_id") or ""),
                "confirmation_hash": str(confirmation.get("receipt_hash") or ""),
                "durable_contract_hash": str(state.get("durable_contract_hash") or preview.get("durable_contract_hash") or ""),
            })
            exact_state = (
                str(state.get("materialization_state") or "") == READY_FOR_STRUCTURAL_PREFLIGHT
                and state.get("materialization_confirmation_valid") is True
                and state.get("materialization_contract_present") is True
                and state.get("materialization_identity_match") is True
                and preview_check.get("valid") is True
                and preview_check.get("ready") is True
                and materialization.get("confirmation_valid") is True
            )
            if exact_state and expected and self._identity_matches(expected, actual):
                return self._recovery_evidence(
                    action,
                    report,
                    matched=True,
                    reason_code=RECOVERY_SIDE_EFFECT_IDENTITY_MATCH,
                    domain_identity=actual,
                    domain_hash=str(actual.get("durable_contract_hash") or ""),
                    side_effect_present=True,
                )
            return self._recovery_evidence(
                action,
                report,
                matched=False,
                reason_code=RECOVERY_SIDE_EFFECT_IDENTITY_MISMATCH,
                domain_identity=actual,
                domain_hash=str(actual.get("durable_contract_hash") or ""),
                side_effect_present=True,
            )

        return self._recovery_evidence(action, report, matched=False, reason_code=RECOVERY_SIDE_EFFECT_NOT_FOUND)

    @staticmethod
    def _side_effect_refs(report: Mapping[str, Any]) -> tuple[str, ...]:
        refs = report.get("canonical_refs") if isinstance(report.get("canonical_refs"), Mapping) else {}
        values: list[str] = []
        for key in ("candidate_governance", "executable_materialization", "durable_frozen_contract", "structural_result"):
            item = refs.get(key)
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, (list, tuple)):
                values.extend(str(value) for value in item if value)
        return tuple(sorted(set(values)))

    def _execute_unlocked(self, action: ResearchActionV1, *, dry_run: bool = False) -> dict[str, Any]:
        self._validate_action(action)
        journal = self._journal(action.objective_id)
        pending = journal.pending()
        persisted = journal.intent(action)
        if persisted is not None and self._action_semantics(ResearchActionV1.from_dict(persisted)) != self._action_semantics(action):
            raise AutonomousControlPlaneError("ACTION_INTENT_CONFLICT", "调用者 Action 与原始执行意图不一致。")
        existing_receipt = journal.latest(action)
        if existing_receipt is not None and existing_receipt.execution_status == EXECUTION_COMPLETED:
            return {
                "execution_status": EXECUTION_COMPLETED,
                "idempotent": True,
                "receipt": existing_receipt.to_dict(),
                "safe_to_advance": False,
            }
        if pending and pending[0]["action_id"] != action.action_id:
            raise AutonomousControlPlaneError("PENDING_ACTION_CONFLICT", "必须先处理原未完成动作。")
        report = self.reconciliation.reconcile(action.objective_id)
        if prior_identity := action.expected_domain_identity:
            shape = self._expected_domain_identity(action.objective_id, report, action.action_type)
            if set(prior_identity) != set(shape) or any(not value for value in prior_identity.values()):
                raise AutonomousControlPlaneError("ACTION_DOMAIN_IDENTITY_INVALID", "执行意图缺少必需的领域身份。")
        elif action.automatic_execution_allowed:
            raise AutonomousControlPlaneError("ACTION_DOMAIN_IDENTITY_INVALID", "执行意图缺少领域身份。")
        current_context, freshness = self._build_context(action.objective_id)
        current_report_hash = str(report.get("report_hash") or "")

        prior_receipt = existing_receipt
        if prior_receipt is not None and prior_receipt.execution_status != EXECUTION_COMPLETED:
            recovery_evidence = self._reconcile_started_action_side_effect(action, report)
            if recovery_evidence.matched and current_context is not None:
                if dry_run:
                    return {"execution_status": "DRY_RUN", "reason_code": "WOULD_RECOVER_COMPLETED", "recovery_evidence": recovery_evidence.to_dict(), "safe_to_advance": False}
                recovered = journal.complete(
                    action,
                    resulting_reconciliation_hash=recovery_evidence.reconciliation_hash,
                    resulting_state=self._effective_state(report),
                    side_effect_refs=self._side_effect_refs(report),
                    result_summary={
                        "recovered_from_started": True,
                        "recovery_reason_code": recovery_evidence.reason_code,
                        "domain_hash": recovery_evidence.domain_hash,
                    },
                    recovered=True,
                )
                return {
                    "execution_status": EXECUTION_COMPLETED,
                    "idempotent": True,
                    "recovered": True,
                    "recovery_evidence": recovery_evidence.to_dict(),
                    "receipt": recovered.to_dict(),
                    "safe_to_advance": False,
                }
            if recovery_evidence.matched:
                recovery_evidence = replace(
                    recovery_evidence,
                    matched=False,
                    reason_code=RECOVERY_SIDE_EFFECT_INTEGRITY_FAILURE,
                    safe_to_mark_completed=False,
                )
            if recovery_evidence.side_effect_present:
                return {
                    "execution_status": DENY,
                    "reason_code": recovery_evidence.reason_code,
                    "reason_zh": "当前存在与 STARTED Action 不匹配的领域副作用，控制平面拒绝自动恢复或覆盖。",
                    "recovery_evidence": recovery_evidence.to_dict(),
                    "safe_to_advance": False,
                }
        if current_report_hash != action.source_reconciliation_hash:
            return {
                "execution_status": DENY,
                "reason_code": STALE_RESEARCH_ACTION,
                "reason_zh": "Action 绑定的 canonical reconciliation 已变化，旧 Action 已丢弃。",
                "safe_to_advance": False,
                "current_reconciliation_hash": current_report_hash,
            }
        if current_context is None or current_context.context_id != action.source_context_id or current_context.context_hash != action.source_context_hash:
            return {
                "execution_status": DENY,
                "reason_code": STALE_RESEARCH_ACTION,
                "reason_zh": "Action 绑定的 SafeRuntimeContext 已变化或不可用，旧 Action 已丢弃。",
                "safe_to_advance": False,
                "context_freshness": freshness,
            }
        try:
            live_freshness = self.context_builder.validate_context_freshness(current_context)
        except SafeRuntimeContextError as exc:
            return {"execution_status": DENY, "reason_code": STALE_RUNTIME_CONTEXT, "reason_zh": exc.message_zh, "safe_to_advance": False, "details": dict(exc.details)}
        trusted_action, authorization = self._plan(action.objective_id, report, current_context)
        if self._action_semantics(trusted_action) != self._action_semantics(action):
            raise AutonomousControlPlaneError("ACTION_PLAN_MISMATCH", "Action 与当前 canonical 事实重新规划的执行语义不一致。")
        permission = self.permission_resolver.resolve(trusted_action, report, current_context, predictive_authorized=bool(authorization.get("authorized")))
        if permission.permission != ALLOW_AUTOMATIC:
            return {"execution_status": DENY, "reason_code": permission.reason_code, "reason_zh": permission.reason_zh, "permission": permission.to_dict(), "safe_to_advance": False}
        if dry_run:
            return {"execution_status": "DRY_RUN", "reason_code": "DRY_RUN_NO_SIDE_EFFECT", "reason_zh": "Dry-run 未执行动作。", "permission": permission.to_dict(), "safe_to_advance": False}

        try:
            status, receipt = journal.begin(action)
            if status == EXECUTION_COMPLETED:
                return {"execution_status": EXECUTION_COMPLETED, "idempotent": True, "receipt": receipt.to_dict(), "safe_to_advance": False}
            if prior_receipt is not None and prior_receipt.execution_status != EXECUTION_COMPLETED:
                journal.allow_retry(action)
                _, receipt = journal.begin(action, allow_retry=True)
            result = self._execute_domain_action(action)
            resulting_report = self.reconciliation.reconcile(action.objective_id)
            committed = self._reconcile_started_action_side_effect(action, resulting_report)
            if not committed.matched:
                raise AutonomousControlPlaneError(committed.reason_code, "领域调用返回后未形成匹配的 canonical 副作用，不写完成回执。")
            completed = journal.complete(
                action,
                resulting_reconciliation_hash=str(resulting_report.get("report_hash") or ""),
                resulting_state=self._effective_state(resulting_report),
                side_effect_refs=self._side_effect_refs(resulting_report),
                result_summary=result,
            )
            return {
                "execution_status": EXECUTION_COMPLETED,
                "idempotent": bool(result.get("idempotent")),
                "result": result,
                "receipt": completed.to_dict(),
                "resulting_reconciliation": self._safe_reconciliation(resulting_report),
                "safe_to_advance": False,
            }
        except (CandidateGenerationError, CandidateExecutableMaterializationError, AutonomousControlPlaneError) as exc:
            post_report = self.reconciliation.reconcile(action.objective_id)
            post_evidence = self._reconcile_started_action_side_effect(action, post_report)
            if post_evidence.matched:
                recovered = journal.complete(
                    action,
                    resulting_reconciliation_hash=post_evidence.reconciliation_hash,
                    resulting_state=self._effective_state(post_report),
                    side_effect_refs=self._side_effect_refs(post_report),
                    result_summary={"recovered_after_domain_error": True, "domain_hash": post_evidence.domain_hash},
                    recovered=True,
                )
                return {"execution_status": EXECUTION_COMPLETED, "idempotent": True, "recovered": True, "recovery_evidence": post_evidence.to_dict(), "receipt": recovered.to_dict(), "safe_to_advance": False}
            if post_evidence.side_effect_present:
                return {"execution_status": DENY, "reason_code": post_evidence.reason_code, "reason_zh": "领域副作用存在但未通过 Action identity 校验，控制平面保持 fail closed。", "recovery_evidence": post_evidence.to_dict(), "safe_to_advance": False}
            code = getattr(exc, "code", "AUTONOMOUS_ACTION_FAILED")
            message = getattr(exc, "message_zh", str(exc))
            failed = journal.fail(action, error_code=code, error_message_zh=message)
            return {"execution_status": "FAILED", "reason_code": code, "reason_zh": message, "receipt": failed.to_dict(), "safe_to_advance": False}
        except AutonomousActionJournalError as exc:
            return {"execution_status": "FAILED", "reason_code": "ACTION_JOURNAL_FAILURE", "reason_zh": str(exc), "safe_to_advance": False}
        except Exception as exc:
            post_report = self.reconciliation.reconcile(action.objective_id)
            post_evidence = self._reconcile_started_action_side_effect(action, post_report)
            if post_evidence.matched:
                recovered = journal.complete(
                    action,
                    resulting_reconciliation_hash=post_evidence.reconciliation_hash,
                    resulting_state=self._effective_state(post_report),
                    side_effect_refs=self._side_effect_refs(post_report),
                    result_summary={"recovered_after_unexpected_error": True, "domain_hash": post_evidence.domain_hash},
                    recovered=True,
                )
                return {"execution_status": EXECUTION_COMPLETED, "idempotent": True, "recovered": True, "recovery_evidence": post_evidence.to_dict(), "receipt": recovered.to_dict(), "safe_to_advance": False}
            if post_evidence.side_effect_present:
                return {"execution_status": DENY, "reason_code": post_evidence.reason_code, "reason_zh": "领域副作用存在但未通过 Action identity 校验，控制平面保持 fail closed。", "recovery_evidence": post_evidence.to_dict(), "safe_to_advance": False}
            return {"execution_status": "FAILED", "reason_code": type(exc).__name__, "reason_zh": str(exc), "safe_to_advance": False}

    def execute_action(self, action: ResearchActionV1 | Mapping[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        if isinstance(action, Mapping):
            PerformanceBlindGuard.assert_blind(action)
        parsed = action if isinstance(action, ResearchActionV1) else ResearchActionV1.from_dict(action)
        if isinstance(action, Mapping) and dict(action) != parsed.to_dict():
            return {"execution_status": DENY, "reason_code": "ACTION_SCHEMA_INVALID", "safe_to_advance": False}
        objective_id = _safe_identifier(parsed.objective_id, name="objective_id")
        if dry_run:
            return self._read_only(objective_id, lambda: self._execute_unlocked(parsed, dry_run=True))
        self._require_execution_policy()
        lock = self._lock(objective_id)
        try:
            lock.acquire(run_id=f"CONTROL_PLANE_ACTION_{parsed.action_id}")
        except MutationBusyError as exc:
            return {"execution_status": DENY, "reason_code": "CONTROL_PLANE_CONCURRENT_RUN", "reason_zh": "同一 Objective 已有控制平面 tick 运行，当前动作未执行。", "safe_to_advance": False, "details": {"error": str(exc)}}
        try:
            try:
                self._require_execution_policy()
                return self._execute_unlocked(parsed, dry_run=dry_run)
            except (AutonomousActionJournalError, AutonomousControlPlaneError) as exc:
                return {"execution_status": DENY, "reason_code": getattr(exc, "code", str(exc)), "safe_to_advance": False}
        finally:
            lock.release()

    def _tick_unlocked(self, objective_id: str, *, dry_run: bool, persist_decision: bool) -> dict[str, Any]:
        # Hard order: reconciliation is the first domain observation.
        report = self.reconciliation.reconcile(objective_id)
        try:
            pending = self._journal(objective_id).pending()
            if pending:
                execution = self._execute_unlocked(ResearchActionV1.from_dict(pending[0]), dry_run=dry_run)
                return {"objective_id": objective_id, "dry_run": dry_run, "execution": execution, "decision": {"current_effective_state": self._effective_state(report)}, "context": {}, "budget": {}, "control_state": RECONCILE_AFTER_EXECUTION if execution.get("execution_status") == EXECUTION_COMPLETED else CONTROL_BLOCKED, "stop_reason": execution.get("reason_code", "ONE_PENDING_ACTION_HANDLED"), "continue_loop": False, "outcome_blind": True}
        except (AutonomousActionJournalError, AutonomousControlPlaneError) as exc:
            return {"objective_id": objective_id, "dry_run": dry_run, "execution": None, "decision": {}, "context": {}, "budget": {}, "control_state": CONTROL_BLOCKED, "stop_reason": getattr(exc, "code", str(exc)), "continue_loop": False, "outcome_blind": True}
        context, freshness = self._build_context(objective_id)
        action, authorization = self._plan(objective_id, report, context)
        permission = self.permission_resolver.resolve(action, report, context, predictive_authorized=bool(authorization.get("authorized")))
        if permission.permission == ALLOW_MANUAL_ONLY:
            state = WAITING_FOR_HUMAN
        elif permission.permission == ALLOW_AUTOMATIC:
            state = READY_TO_EXECUTE
        else:
            state = STOPPED if action.action_type == STOP_OBJECTIVE else CONTROL_BLOCKED
        decision = self._decision(objective_id, report, context, freshness, action, permission, control_state=state)
        if persist_decision and not dry_run:
            self._write_decision(decision)
        result: dict[str, Any] = {
            "schema_version": "autonomous-control-plane-tick-v1",
            "objective_id": objective_id,
            "dry_run": bool(dry_run),
            "control_state": state,
            "stop_reason": permission.reason_code if permission.permission != ALLOW_AUTOMATIC else "ONE_ACTION_NOT_EXECUTED",
            "decision": decision.to_dict(),
            "reconciliation": self._safe_reconciliation(report),
            "context": self._safe_context(context, freshness),
            "budget": self._safe_budget(context),
            "capabilities": self.capability_registry.to_dict(),
            "authorization": dict(authorization),
            "execution": None,
            "continue_loop": False,
            "outcome_blind": True,
        }
        if permission.permission == ALLOW_AUTOMATIC:
            if dry_run:
                result["control_state"] = READY_TO_EXECUTE
                result["stop_reason"] = "DRY_RUN_NO_SIDE_EFFECT"
                result["continue_loop"] = True
                return result
            result["control_state"] = EXECUTING
            execution = self._execute_unlocked(action)
            result["execution"] = execution
            if execution.get("execution_status") == EXECUTION_COMPLETED:
                result["control_state"] = RECONCILE_AFTER_EXECUTION
                result["stop_reason"] = "ONE_ACTION_EXECUTED"
                result["continue_loop"] = True
            else:
                result["control_state"] = CONTROL_BLOCKED
                result["stop_reason"] = str(execution.get("reason_code") or "ACTION_EXECUTION_FAILED")
            return result
        result["continue_loop"] = False
        return result

    def tick(self, objective_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        objective_id = _safe_identifier(objective_id, name="objective_id")
        if dry_run:
            return self._read_only(objective_id, lambda: self._tick_unlocked(objective_id, dry_run=True, persist_decision=False))
        self._require_execution_policy()
        lock = self._lock(objective_id)
        try:
            lock.acquire(run_id=f"CONTROL_PLANE_TICK_{_clock_text(self.clock)}")
        except MutationBusyError as exc:
            return {
                "schema_version": "autonomous-control-plane-tick-v1",
                "objective_id": objective_id,
                "dry_run": False,
                "control_state": CONTROL_BLOCKED,
                "stop_reason": "CONTROL_PLANE_CONCURRENT_RUN",
                "decision": {},
                "context": {},
                "budget": {},
                "execution": None,
                "outcome_blind": True,
                "error": {"code": "CONTROL_PLANE_CONCURRENT_RUN", "message_zh": "同一 Objective 已有控制平面 tick 运行。", "details": {"error": str(exc)}},
            }
        try:
            self._require_execution_policy()
            return self._tick_unlocked(objective_id, dry_run=False, persist_decision=True)
        finally:
            lock.release()

    def inspect(self, objective_id: str) -> dict[str, Any]:
        return self.tick(objective_id, dry_run=True)

    def recover(self, objective_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        """显式恢复入口；无 pending 时只报告，不创建新领域动作。"""
        objective_id = _safe_identifier(objective_id, name="objective_id")
        if not dry_run:
            self._require_execution_policy()
        def recover_pending():
            try:
                pending = self._journal(objective_id).pending()
                if not pending:
                    return {"execution_status": "NO_PENDING_ACTION", "safe_to_advance": False}
                return self._execute_unlocked(ResearchActionV1.from_dict(pending[0]), dry_run=dry_run)
            except (AutonomousActionJournalError, AutonomousControlPlaneError) as exc:
                return {"execution_status": DENY, "reason_code": getattr(exc, "code", str(exc)), "safe_to_advance": False}
        if dry_run:
            return self._read_only(objective_id, recover_pending)
        try:
            with self._lock(objective_id):
                self._require_execution_policy()
                return recover_pending()
        except MutationBusyError:
            return {"execution_status": DENY, "reason_code": "CONTROL_PLANE_CONCURRENT_RUN", "safe_to_advance": False}

    def loop(self, objective_id: str, *, max_ticks: int | None = None, dry_run: bool = False) -> dict[str, Any]:
        limit = self.max_ticks if max_ticks is None else int(max_ticks)
        if limit <= 0:
            raise AutonomousControlPlaneError("INVALID_MAX_TICKS", "max_ticks 必须是正整数。", status_code=400)
        results: list[dict[str, Any]] = []
        for _ in range(limit):
            result = self.tick(objective_id, dry_run=dry_run)
            results.append(result)
            if not result.get("continue_loop"):
                break
        return {
            "schema_version": "autonomous-control-plane-loop-v1",
            "objective_id": str(objective_id),
            "max_ticks": limit,
            "ticks_executed": len(results),
            "stopped": len(results) < limit or not results[-1].get("continue_loop", False),
            "stop_reason": results[-1].get("stop_reason") if results else "NO_TICK",
            "results": results,
            "outcome_blind": True,
        }


def _render_human(payload: Mapping[str, Any]) -> str:
    decision = _mapping(payload.get("decision"))
    permission = _mapping(decision.get("permission"))
    action = _mapping(decision.get("selected_action"))
    lines = [
        f"Objective：{payload.get('objective_id')}",
        f"Canonical State：{decision.get('current_effective_state')}",
        f"Next Action：{action.get('action_type')}",
        f"Permission：{permission.get('permission')}（{permission.get('reason_zh')}）",
        f"Automatic：{decision.get('automatic_execution')}",
        f"Control State：{payload.get('control_state')}",
        f"Stop Reason：{payload.get('stop_reason')}",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="自主量化研究控制平面 V1")
    parser.add_argument("--root", default=".", help="项目根目录")
    parser.add_argument("--objective-id", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inspect", action="store_true", help="只读查看下一动作")
    mode.add_argument("--tick", action="store_true", help="执行最多一个允许的自动动作")
    mode.add_argument("--loop", action="store_true", help="按 max-ticks 执行有限循环")
    mode.add_argument("--recover", action="store_true", help="显式处理一个未完成执行意图")
    parser.add_argument("--governed-synthetic", action="store_true", help="显式选择合成治理策略，不替代人工确认")
    parser.add_argument("--max-ticks", type=int, default=DEFAULT_MAX_TICKS)
    parser.add_argument("--dry-run", action="store_true", help="只对账、构建上下文、计划和解析权限")
    parser.add_argument("--json", action="store_true", help="输出稳定机器 JSON")
    args = parser.parse_args(argv)
    try:
        policy = ExecutionPolicy(mode="GOVERNED", workspace_kind="SYNTHETIC") if args.governed_synthetic else ExecutionPolicy()
        service = AutonomousResearchControlPlaneV1(args.root, max_ticks=args.max_ticks, execution_policy=policy)
        if args.recover:
            payload = service.recover(args.objective_id, dry_run=args.dry_run)
        elif args.loop:
            payload = service.loop(args.objective_id, max_ticks=args.max_ticks, dry_run=args.dry_run)
        elif args.tick:
            payload = service.tick(args.objective_id, dry_run=args.dry_run)
        else:
            payload = service.inspect(args.objective_id)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        else:
            print(_render_human(payload))
        return 0
    except AutonomousControlPlaneError as exc:
        payload = {"code": exc.code, "message_zh": exc.message_zh, "details": dict(exc.details)}
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AgentCapabilityRegistryV1",
    "AgentCapabilityV1",
    "ActionPermissionResolverV1",
    "ALLOW_AUTOMATIC",
    "ALLOW_MANUAL_ONLY",
    "AutonomousControlPlaneError",
    "AutonomousResearchControlPlaneV1",
    "AutonomousResearchDecisionV1",
    "BLOCKED",
    "BUILD_SAFE_RUNTIME_CONTEXT",
    "CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW",
    "DENY",
    "GENERATE_AI_DESIGN",
    "GENERATE_CANDIDATE_PROPOSAL",
    "PREDICTIVE_AUTHORIZATION_REQUIRED",
    "RECOVER_EXECUTABLE_MATERIALIZATION",
    "RECOVERY_SIDE_EFFECT_IDENTITY_MATCH",
    "RECOVERY_SIDE_EFFECT_IDENTITY_MISMATCH",
    "RECOVERY_SIDE_EFFECT_INTEGRITY_FAILURE",
    "RECOVERY_SIDE_EFFECT_NOT_FOUND",
    "RECONCILE_OBJECTIVE",
    "RECONCILE_TRIAL",
    "ResearchActionPermissionV1",
    "ResearchActionV1",
    "SideEffectRecoveryEvidenceV1",
    "RUN_STRUCTURAL_PREFLIGHT",
    "START_PREDICTIVE_TRIAL",
    "WAIT_FOR_AI_DESIGN_CONFIRMATION",
    "WAIT_FOR_CANDIDATE_GOVERNANCE_FREEZE",
    "WAIT_FOR_EXECUTABLE_MATERIALIZATION_CONFIRMATION",
    "WAIT_FOR_PREDICTIVE_AUTHORIZATION",
    "WAIT_FOR_TRIAL_RESULT",
    "main",
]
