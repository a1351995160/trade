"""Autonomous Research Orchestrator V2 control plane.

The orchestrator owns only control state.  Canonical candidates, trials,
budget and validation artifacts remain owned by the existing research
components.  The default runtime supervises :mod:`research_daemon`; the
synthetic runtime exists solely for deterministic acceptance tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Protocol, Sequence

from .artifact_graph import ResearchArtifactGraphV1
from .common import now_timestamp, stable_hash
from .context import NoOutcomeResearchContextV1, OutcomeBlindFieldPolicyV1, PerformanceBlindGuard, PerformanceLeakError
from .durability import DURABLE_INTERACTION_SEMANTICS_FIELDS, FROZEN_CANDIDATE_CONTRACT_SCHEMA, DurableFrozenCandidateContractRegistryV1, DurableFrozenCandidateContractV1
from .contract_correction import load_effective_contract_invalidations
from .failure_adapter import FailureKnowledgeAdapterV1
from .novelty import CandidateNoveltyGateV2, design_safe_candidate
from .budget import SearchBudgetRegistryV1
from .manual_handoff import (
    BATCH_SCHEMA_FILENAME,
    CONTRACT_HASH_HELPER_FILENAME,
    DURABLE_CONTRACT_SCHEMA_FILENAME,
    ManualAIHandoffWriterV1,
    ManualAIResultWatcherV1,
)
from .promising_followup_scope import is_one_shot_followup, load_design_policy, load_scope_manifest
from ..research.strategy_candidate import CANDIDATE_STATUSES, DSL_VERSION, SIGNAL_FREQUENCIES, STRATEGY_FAMILIES, StrategyCandidateSpec
from ..research.strategy_semantic import EXIT_TYPES, PREDICATE_TYPES, SEMANTIC_STATUSES, ExitPredicateSpec, SignalPredicateSpec
from ..presentation import ZhCNPresentation, write_report_pair


class OrchestratorState(str, Enum):
    BOOTSTRAP = "BOOTSTRAP"
    RECOVER = "RECOVER"
    ACTIVE = "ACTIVE"
    LOCAL_RESEARCH_RUNNING = "LOCAL_RESEARCH_RUNNING"
    NEED_AI_RESEARCH_DESIGN = "NEED_AI_RESEARCH_DESIGN"
    AI_HANDOFF_PREPARING = "AI_HANDOFF_PREPARING"
    AI_MANUAL_HANDOFF_REQUIRED = "AI_MANUAL_HANDOFF_REQUIRED"
    AI_RESEARCH_DISABLED = "AI_RESEARCH_DISABLED"
    AI_INVOCATION_PENDING = "AI_INVOCATION_PENDING"
    AI_INVOCATION_RUNNING = "AI_INVOCATION_RUNNING"
    AI_OUTPUT_VALIDATING = "AI_OUTPUT_VALIDATING"
    AI_BATCH_INGESTING = "AI_BATCH_INGESTING"
    LOCAL_RESEARCH_RESUMING = "LOCAL_RESEARCH_RESUMING"
    ENGINEERING_BLOCKED = "ENGINEERING_BLOCKED"
    AI_HANDOFF_BLOCKED = "AI_HANDOFF_BLOCKED"
    AI_INVOCATION_UNAVAILABLE = "AI_INVOCATION_UNAVAILABLE"
    TERMINAL_CLOSEOUT_PENDING = "TERMINAL_CLOSEOUT_PENDING"
    TERMINAL_CLOSEOUT_RUNNING = "TERMINAL_CLOSEOUT_RUNNING"
    TERMINAL_CLOSEOUT_COMPLETE = "TERMINAL_CLOSEOUT_COMPLETE"
    GOVERNANCE_DECISION_REQUIRED = "GOVERNANCE_DECISION_REQUIRED"
    RESEARCH_PASSED = "RESEARCH_PASSED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    GLOBAL_SEARCH_EXHAUSTED = "GLOBAL_SEARCH_EXHAUSTED"
    SAFETY_STOP = "SAFETY_STOP"
    NO_PROGRESS_RESEARCH_LOOP = "NO_PROGRESS_RESEARCH_LOOP"
    PAUSED = "PAUSED"
    SHUTDOWN = "SHUTDOWN"


TERMINAL_DAEMON_STATES = frozenset({
    "BUDGET_EXHAUSTED", "RESEARCH_PASSED", "GLOBAL_SEARCH_EXHAUSTED",
})
AI_ALLOWED_TRIGGERS = frozenset({"NEED_AI_RESEARCH_DESIGN"})
AI_DENIED_TRIGGERS = frozenset({
    "GOVERNANCE_REQUIRED", "BUDGET_EXHAUSTED", "RESEARCH_PASSED",
    "FINAL_TEST_REQUIRED", "POLICY_CHANGE_REQUIRED", "REAL_ORDER", "SAFETY_STOP", "PAUSED", "NO_PROGRESS_RESEARCH_LOOP",
})
AI_BATCH_VALIDATION_TIMEOUT_SECONDS = 30.0
AI_BATCH_VALIDATION_STALE_AFTER_SECONDS = 120.0
PREDICTIVE_AUTHORIZATION_REQUIRED = "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"


def _safe_design_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _safe_design_payload(nested)
            for key, nested in value.items()
            if not OutcomeBlindFieldPolicyV1.is_forbidden(key)
        }
    if isinstance(value, (list, tuple)):
        return [_safe_design_payload(item) for item in value]
    return value


_TRANSITIONS: dict[OrchestratorState, frozenset[OrchestratorState]] = {
    OrchestratorState.BOOTSTRAP: frozenset({OrchestratorState.RECOVER, OrchestratorState.ACTIVE, OrchestratorState.SAFETY_STOP}),
    OrchestratorState.RECOVER: frozenset({OrchestratorState.ACTIVE, OrchestratorState.GOVERNANCE_DECISION_REQUIRED, OrchestratorState.AI_INVOCATION_PENDING, OrchestratorState.TERMINAL_CLOSEOUT_RUNNING, OrchestratorState.SAFETY_STOP}),
    OrchestratorState.ACTIVE: frozenset({OrchestratorState.LOCAL_RESEARCH_RUNNING, OrchestratorState.NEED_AI_RESEARCH_DESIGN, OrchestratorState.BUDGET_EXHAUSTED, OrchestratorState.RESEARCH_PASSED, OrchestratorState.GLOBAL_SEARCH_EXHAUSTED, OrchestratorState.ENGINEERING_BLOCKED, OrchestratorState.GOVERNANCE_DECISION_REQUIRED, OrchestratorState.PAUSED, OrchestratorState.SHUTDOWN, OrchestratorState.SAFETY_STOP, OrchestratorState.NO_PROGRESS_RESEARCH_LOOP, OrchestratorState.RECOVER}),
    OrchestratorState.LOCAL_RESEARCH_RUNNING: frozenset({OrchestratorState.ACTIVE, OrchestratorState.BUDGET_EXHAUSTED, OrchestratorState.RESEARCH_PASSED, OrchestratorState.GLOBAL_SEARCH_EXHAUSTED, OrchestratorState.ENGINEERING_BLOCKED, OrchestratorState.TERMINAL_CLOSEOUT_PENDING, OrchestratorState.PAUSED, OrchestratorState.SAFETY_STOP}),
    OrchestratorState.NEED_AI_RESEARCH_DESIGN: frozenset({OrchestratorState.AI_HANDOFF_PREPARING, OrchestratorState.AI_RESEARCH_DISABLED, OrchestratorState.AI_HANDOFF_BLOCKED, OrchestratorState.TERMINAL_CLOSEOUT_PENDING, OrchestratorState.GOVERNANCE_DECISION_REQUIRED, OrchestratorState.PAUSED, OrchestratorState.SHUTDOWN}),
    OrchestratorState.AI_HANDOFF_PREPARING: frozenset({OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED, OrchestratorState.AI_RESEARCH_DISABLED, OrchestratorState.AI_INVOCATION_PENDING, OrchestratorState.NEED_AI_RESEARCH_DESIGN, OrchestratorState.AI_INVOCATION_UNAVAILABLE, OrchestratorState.AI_HANDOFF_BLOCKED, OrchestratorState.GOVERNANCE_DECISION_REQUIRED}),
    OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED: frozenset({OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED, OrchestratorState.AI_RESEARCH_DISABLED, OrchestratorState.AI_OUTPUT_VALIDATING, OrchestratorState.LOCAL_RESEARCH_RESUMING, OrchestratorState.AI_HANDOFF_BLOCKED, OrchestratorState.RECOVER, OrchestratorState.PAUSED, OrchestratorState.SHUTDOWN}),
    OrchestratorState.AI_RESEARCH_DISABLED: frozenset({OrchestratorState.NEED_AI_RESEARCH_DESIGN, OrchestratorState.PAUSED, OrchestratorState.SHUTDOWN}),
    OrchestratorState.AI_INVOCATION_PENDING: frozenset({OrchestratorState.AI_INVOCATION_RUNNING, OrchestratorState.NEED_AI_RESEARCH_DESIGN, OrchestratorState.LOCAL_RESEARCH_RESUMING, OrchestratorState.AI_HANDOFF_BLOCKED, OrchestratorState.AI_INVOCATION_UNAVAILABLE, OrchestratorState.PAUSED, OrchestratorState.GOVERNANCE_DECISION_REQUIRED}),
    OrchestratorState.AI_INVOCATION_RUNNING: frozenset({OrchestratorState.AI_OUTPUT_VALIDATING, OrchestratorState.AI_INVOCATION_PENDING, OrchestratorState.AI_HANDOFF_BLOCKED, OrchestratorState.AI_INVOCATION_UNAVAILABLE, OrchestratorState.RECOVER, OrchestratorState.GOVERNANCE_DECISION_REQUIRED}),
    OrchestratorState.AI_OUTPUT_VALIDATING: frozenset({OrchestratorState.AI_BATCH_INGESTING, OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED, OrchestratorState.ENGINEERING_BLOCKED, OrchestratorState.AI_INVOCATION_PENDING, OrchestratorState.AI_HANDOFF_BLOCKED, OrchestratorState.AI_INVOCATION_UNAVAILABLE, OrchestratorState.RECOVER, OrchestratorState.GOVERNANCE_DECISION_REQUIRED}),
    OrchestratorState.AI_BATCH_INGESTING: frozenset({OrchestratorState.LOCAL_RESEARCH_RESUMING, OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED, OrchestratorState.RECOVER, OrchestratorState.AI_OUTPUT_VALIDATING, OrchestratorState.AI_HANDOFF_BLOCKED, OrchestratorState.ENGINEERING_BLOCKED, OrchestratorState.GOVERNANCE_DECISION_REQUIRED}),
    OrchestratorState.LOCAL_RESEARCH_RESUMING: frozenset({OrchestratorState.ACTIVE, OrchestratorState.LOCAL_RESEARCH_RUNNING, OrchestratorState.RECOVER, OrchestratorState.NO_PROGRESS_RESEARCH_LOOP}),
    OrchestratorState.ENGINEERING_BLOCKED: frozenset({OrchestratorState.SHUTDOWN, OrchestratorState.PAUSED}),
    OrchestratorState.AI_HANDOFF_BLOCKED: frozenset({OrchestratorState.SHUTDOWN, OrchestratorState.PAUSED, OrchestratorState.RECOVER}),
    OrchestratorState.AI_INVOCATION_UNAVAILABLE: frozenset({OrchestratorState.SHUTDOWN, OrchestratorState.PAUSED, OrchestratorState.RECOVER}),
    OrchestratorState.TERMINAL_CLOSEOUT_PENDING: frozenset({OrchestratorState.TERMINAL_CLOSEOUT_RUNNING, OrchestratorState.RECOVER}),
    OrchestratorState.TERMINAL_CLOSEOUT_RUNNING: frozenset({OrchestratorState.TERMINAL_CLOSEOUT_COMPLETE, OrchestratorState.RECOVER}),
    OrchestratorState.TERMINAL_CLOSEOUT_COMPLETE: frozenset({OrchestratorState.GOVERNANCE_DECISION_REQUIRED, OrchestratorState.RECOVER}),
    OrchestratorState.GOVERNANCE_DECISION_REQUIRED: frozenset({OrchestratorState.SHUTDOWN, OrchestratorState.PAUSED}),
    OrchestratorState.RESEARCH_PASSED: frozenset({OrchestratorState.TERMINAL_CLOSEOUT_PENDING, OrchestratorState.GOVERNANCE_DECISION_REQUIRED}),
    OrchestratorState.BUDGET_EXHAUSTED: frozenset({OrchestratorState.TERMINAL_CLOSEOUT_PENDING, OrchestratorState.GOVERNANCE_DECISION_REQUIRED}),
    OrchestratorState.GLOBAL_SEARCH_EXHAUSTED: frozenset({OrchestratorState.TERMINAL_CLOSEOUT_PENDING, OrchestratorState.GOVERNANCE_DECISION_REQUIRED}),
    OrchestratorState.SAFETY_STOP: frozenset({OrchestratorState.RECOVER, OrchestratorState.SHUTDOWN, OrchestratorState.PAUSED}),
    OrchestratorState.NO_PROGRESS_RESEARCH_LOOP: frozenset({OrchestratorState.SHUTDOWN, OrchestratorState.PAUSED, OrchestratorState.RECOVER}),
    OrchestratorState.PAUSED: frozenset({OrchestratorState.ACTIVE, OrchestratorState.RECOVER, OrchestratorState.SHUTDOWN}),
    OrchestratorState.SHUTDOWN: frozenset(),
}


@dataclass(frozen=True)
class OrchestratorConfigV2:
    ai_invocation_mode: str = "MANUAL_HANDOFF"
    ai_auto_invocation: bool | None = None
    max_attempts_per_handoff: int = 2
    timeout_seconds: int = 120
    backoff_seconds: float = 1.0
    max_steps: int = 128

    def __post_init__(self) -> None:
        mode = str(self.ai_invocation_mode or "MANUAL_HANDOFF").strip().upper()
        legacy = self.ai_auto_invocation
        if legacy is not None:
            legacy_mode = "AUTO_CODEX" if bool(legacy) else "MANUAL_HANDOFF"
            if mode != "MANUAL_HANDOFF" and mode != legacy_mode:
                raise ValueError("ai_invocation_mode conflicts with ai_auto_invocation")
            mode = legacy_mode
        if mode not in {"MANUAL_HANDOFF", "AUTO_CODEX", "AI_DISABLED"}:
            raise ValueError("ai_invocation_mode must be MANUAL_HANDOFF, AUTO_CODEX, or AI_DISABLED")
        object.__setattr__(self, "ai_invocation_mode", mode)
        object.__setattr__(self, "ai_auto_invocation", mode == "AUTO_CODEX")
        if self.max_attempts_per_handoff < 1:
            raise ValueError("max_attempts_per_handoff must be positive")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative")

    @classmethod
    def from_environment(cls, root: str | Path | None = None) -> "OrchestratorConfigV2":
        mode_path = Path(root).resolve() / "config" / "research_ai_invocation_mode.json" if root is not None else None
        if mode_path is not None and mode_path.exists():
            try:
                payload = json.loads(mode_path.read_text(encoding="utf-8"))
                configured = str(payload.get("ai_invocation_mode") or "").strip().upper() if isinstance(payload, Mapping) else ""
                if configured in {"MANUAL_HANDOFF", "AUTO_CODEX", "AI_DISABLED"}:
                    return cls(ai_invocation_mode=configured)
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass
        raw_mode = os.getenv("AI_INVOCATION_MODE")
        if raw_mode:
            return cls(ai_invocation_mode=raw_mode)
        legacy = os.getenv("AI_AUTO_INVOCATION")
        if legacy is not None:
            return cls(ai_auto_invocation=legacy.strip().upper() in {"1", "TRUE", "YES", "ENABLED"})
        return cls()


@dataclass(frozen=True)
class ResearchOrchestratorStatusView:
    objective_id: str
    orchestrator_state: str
    daemon_state: str
    daemon_status_zh: str
    ai_status: str
    current_round: int | None
    current_candidate: Mapping[str, Any] | None
    current_trial: Mapping[str, Any] | None
    ai_running: bool
    last_ai_invocation: Mapping[str, Any] | None
    current_handoff: Mapping[str, Any] | None
    closeout_complete: bool
    terminal_reason: str | None
    waiting_for_governance: bool
    next_action: str
    budget: Mapping[str, Any]
    resource_state: Mapping[str, Any]
    research_counts: Mapping[str, int]
    no_new_predictive_trials_during_build: bool = True
    ai_auto_invocation_enabled: bool = True
    ai_invocation_mode: str = "MANUAL_HANDOFF"
    ai_invocation_mode_zh: str = "手动 AI 交接（推荐）"
    manual_handoff: Mapping[str, Any] = field(default_factory=dict)
    background_ai_token_consumption: int = 0
    retry_state: Mapping[str, Any] = field(default_factory=dict)
    current_handoff_id: str | None = None
    current_ai_invocation_id: str | None = None
    closeout_state: str = "NOT_STARTED"
    governance_decision_state: str = "NOT_REQUIRED"
    last_transition: Mapping[str, Any] | None = None
    source_generated_at: str | None = None
    observed_at: str | None = None
    freshness_state: str = "UNKNOWN"
    validation: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        state_zh = {
            "BOOTSTRAP": "正在初始化",
            "RECOVER": "正在恢复",
            "ACTIVE": "研究编排器已就绪",
            "LOCAL_RESEARCH_RUNNING": "本地研究正在运行",
            "NEED_AI_RESEARCH_DESIGN": "等待 AI 研究员设计下一批策略",
            "AI_HANDOFF_PREPARING": "正在准备 AI 研究交接",
            "AI_MANUAL_HANDOFF_REQUIRED": "需要 AI 设计新的研究方案",
            "AI_RESEARCH_DISABLED": "AI 研究已禁用",
            "AI_INVOCATION_PENDING": "等待 AI 研究调用",
            "AI_INVOCATION_RUNNING": "AI 研究员正在设计新策略",
            "AI_OUTPUT_VALIDATING": "正在校验 AI 批次",
            "AI_BATCH_INGESTING": "正在接入 AI 候选批次",
            "LOCAL_RESEARCH_RESUMING": "正在恢复本地研究",
            "ENGINEERING_BLOCKED": "需要人工工程修复或 canonical 对账",
            "AI_HANDOFF_BLOCKED": "AI 交接已封锁，等待人工处理",
            "AI_INVOCATION_UNAVAILABLE": "AI 研究员当前不可用",
            "TERMINAL_CLOSEOUT_PENDING": "等待终端收官",
            "TERMINAL_CLOSEOUT_RUNNING": "正在自动收官",
            "TERMINAL_CLOSEOUT_COMPLETE": "自动收官已完成",
            "GOVERNANCE_DECISION_REQUIRED": "本轮研究已结束，需要你的决策",
            "RESEARCH_PASSED": "研究已通过，等待治理决定",
            "BUDGET_EXHAUSTED": "预测试验预算已耗尽",
            "GLOBAL_SEARCH_EXHAUSTED": "合法搜索空间已耗尽",
            "SAFETY_STOP": "安全停机",
            "NO_PROGRESS_RESEARCH_LOOP": "研究循环无进展，已停机",
            "PAUSED": "已暂停",
            "SHUTDOWN": "已停止",
        }
        current_state_zh = state_zh.get(self.orchestrator_state, self.orchestrator_state)
        return {
            "schema_version": "research-orchestrator-status-view-v2",
            "objective_id": self.objective_id,
            "orchestrator_state": self.orchestrator_state,
            "orchestrator_state_zh": current_state_zh,
            "daemon_state": self.daemon_state,
            "daemon_state_zh": self.daemon_status_zh,
            "ai_status": self.ai_status,
            "ai_status_zh": {
                "NEED_AI_RESEARCH_DESIGN": "等待 AI 研究员设计下一批策略",
                "AI_HANDOFF_PREPARING": "正在准备 AI 研究交接",
                "AI_MANUAL_HANDOFF_REQUIRED": "需要 AI 设计新的研究方案",
                "AI_RESEARCH_DISABLED": "AI 研究已禁用",
                "AI_INVOCATION_PENDING": "等待 AI 研究调用",
                "AI_INVOCATION_RUNNING": "AI 研究员正在设计新策略",
                "AI_OUTPUT_VALIDATING": "正在校验 AI 批次",
                "AI_BATCH_INGESTING": "正在接入 AI 候选批次",
                "LOCAL_RESEARCH_RESUMING": "正在恢复本地研究",
                "AI_INVOCATION_UNAVAILABLE": "AI 研究员当前不可用，研究状态已保存",
                "AI_HANDOFF_BLOCKED": "AI 交接已封锁，等待人工处理",
                "IDLE": "当前无需 AI 设计",
            }.get(self.ai_status, self.ai_status),
            "current_round": self.current_round,
            "current_candidate": dict(self.current_candidate) if self.current_candidate else None,
            "current_trial": dict(self.current_trial) if self.current_trial else None,
            "ai_running": self.ai_running,
            "last_ai_invocation": dict(self.last_ai_invocation) if self.last_ai_invocation else None,
            "current_handoff": dict(self.current_handoff) if self.current_handoff else None,
            "closeout_complete": self.closeout_complete,
            "terminal_reason": self.terminal_reason,
            "terminal_reason_zh": ZhCNPresentation.reason_title(self.terminal_reason, include_code=False) if self.terminal_reason else None,
            "waiting_for_governance": self.waiting_for_governance,
            "next_action": self.next_action,
            "next_action_zh": {
                "GOVERNANCE_DECISION_REQUIRED": "本轮研究已结束，需要你的决策",
                "NEED_AI_RESEARCH_DESIGN": "等待 AI 研究员设计下一批策略",
                "AI_MANUAL_HANDOFF_REQUIRED": "需要 AI 设计新的研究方案",
                "AI_RESEARCH_DISABLED": "AI 研究已禁用",
                "LOCAL_RESEARCH_RUNNING": "本地研究正在运行",
                "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED": "结构预检已通过，等待你授权第 1 次预测试验",
                "AI_INVOCATION_UNAVAILABLE": "恢复 AI 后可继续设计下一批策略",
                "ENGINEERING_BLOCKED": "需要人工完成工程修复或 canonical 对账",
            }.get(self.next_action, state_zh.get(self.next_action, self.next_action)),
            "budget": dict(self.budget),
            "resource_state": dict(self.resource_state),
            "research_counts": dict(self.research_counts),
            "no_new_predictive_trials_during_build": self.no_new_predictive_trials_during_build,
            "ai_auto_invocation_enabled": self.ai_auto_invocation_enabled,
            "ai_invocation_mode": self.ai_invocation_mode,
            "ai_invocation_mode_zh": self.ai_invocation_mode_zh,
            "manual_handoff": dict(self.manual_handoff),
            "background_ai_token_consumption": self.background_ai_token_consumption,
            "retry_state": dict(self.retry_state),
            "current_handoff_id": self.current_handoff_id,
            "current_ai_invocation_id": self.current_ai_invocation_id,
            "closeout_state": self.closeout_state,
            "governance_decision_state": self.governance_decision_state,
            "last_transition": dict(self.last_transition) if self.last_transition else None,
            "source_generated_at": self.source_generated_at,
            "observed_at": self.observed_at,
            "freshness_state": self.freshness_state,
            "validation": dict(self.validation),
        }


@dataclass(frozen=True)
class CanonicalResearchSnapshotV2:
    objective_id: str
    objective_hash: str
    daemon_state: str
    daemon_run_id: str | None
    budget: Mapping[str, Any]
    candidates: tuple[Mapping[str, Any], ...] = ()
    trials: tuple[Mapping[str, Any], ...] = ()
    research_counts: Mapping[str, int] = field(default_factory=dict)
    global_search_exhausted: bool = False
    remaining_frozen_candidates: int = 0
    terminal_reason: str | None = None
    resource_state: Mapping[str, Any] = field(default_factory=dict)
    required_action: str | None = None

    @property
    def has_legal_candidate(self) -> bool:
        return self.remaining_frozen_candidates > 0


class CanonicalResearchStateReaderV2:
    """Read canonical files without invoking provider, performance or AI."""

    def __init__(self, root: str | Path, objective_id: str):
        self.root = Path(root).resolve()
        self.objective_id = str(objective_id)

    def _json(self, path: Path, default: Any = None) -> Any:
        if not path.exists():
            return default
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"CANONICAL_STATE_UNREADABLE:{path}") from exc
        return value

    def _objective(self) -> tuple[Mapping[str, Any], str]:
        path = self.root / "data" / "research" / "research_factory" / "objectives" / f"{self.objective_id}.json"
        payload = self._json(path)
        if not isinstance(payload, Mapping) or str(payload.get("objective_id")) != self.objective_id:
            raise RuntimeError("CANONICAL_OBJECTIVE_MISMATCH")
        return payload, hashlib.sha256(path.read_bytes()).hexdigest()

    def _budget_path(self, daemon_payload: Mapping[str, Any], status_payload: Mapping[str, Any]) -> Path:
        refs = (
            (daemon_payload.get("budget_view") or {}).get("registry_path"),
            (status_payload.get("budget") or {}).get("registry_path"),
        )
        for ref in refs:
            if not ref:
                continue
            path = (self.root / str(ref).replace("\\", "/")).resolve()
            if path.is_relative_to(self.root) and path.exists():
                payload = self._json(path, {})
                if str(payload.get("objective_id")) == self.objective_id:
                    return path
        matches: list[Path] = []
        for path in (self.root / "data" / "research" / "research_factory" / "batches").glob("*/search_budget_registry.json"):
            if str((self._json(path, {}) or {}).get("objective_id")) == self.objective_id:
                matches.append(path)
        if not matches:
            raise RuntimeError("CANONICAL_SEARCH_BUDGET_NOT_FOUND")
        return max(matches, key=lambda path: str((self._json(path, {}) or {}).get("updated_at", "")))

    def _candidates(self) -> tuple[Mapping[str, Any], ...]:
        found: dict[str, dict[str, Any]] = {}
        root = self.root / "data" / "research" / "research_factory" / "batches"
        scope = load_scope_manifest(self.root, self.objective_id)
        excluded_candidate_ids = {str(item) for item in scope.get("out_of_scope_candidate_ids", ())}
        for path in sorted(root.glob("*/durable_frozen_candidate_contracts.json")):
            payload = self._json(path, {}) or {}
            for raw in payload.get("contracts", ()):
                if not isinstance(raw, Mapping):
                    continue
                candidate_id = str(raw.get("candidate_id") or "")
                identity = raw.get("policy_identity") if isinstance(raw.get("policy_identity"), Mapping) else {}
                if not candidate_id or str(identity.get("objective_id")) != self.objective_id:
                    continue
                if candidate_id in excluded_candidate_ids:
                    continue
                candidate = {
                    "candidate_id": candidate_id,
                    "candidate_hash": str(raw.get("candidate_hash") or raw.get("content_hash") or ""),
                    "family_id": str(raw.get("mechanism") or raw.get("family") or "UNKNOWN"),
                    "mechanism": str(raw.get("mechanism") or raw.get("family") or "UNKNOWN"),
                    "factor_ids": [str(item) for item in raw.get("factor_ids", ())],
                    "event_ids": [str(item) for item in raw.get("event_ids", ())],
                    "holding_period_days": int(raw.get("holding_period_days") or raw.get("holding_period") or 0),
                    "semantic_fingerprint": raw.get("semantic_fingerprint"),
                    "parameter_fingerprint": raw.get("parameter_fingerprint"),
                    "contract_ref": str(path.relative_to(self.root)).replace("\\", "/"),
                }
                current = found.get(candidate_id)
                if current is None:
                    found[candidate_id] = candidate
                elif current["candidate_hash"] != candidate["candidate_hash"]:
                    raise RuntimeError(f"CANONICAL_CANDIDATE_IDENTITY_CONFLICT:{candidate_id}")
        return tuple(found[key] for key in sorted(found))

    def _trials(self, preferred_path: Path) -> tuple[Mapping[str, Any], ...]:
        root = self.root
        if preferred_path.exists():
            paths = [preferred_path]
        else:
            # Trial state is objective-scoped.  Do not recursively inspect every
            # historical report ledger just because the preferred batch ledger
            # has not been created yet; that made a read-only status request scan
            # the whole report archive and could mix unrelated objectives.
            paths = [root / "reports" / "research_factory" / "factory_trial_ledger.json"]
            paths.extend(sorted((root / "data" / "research" / "research_factory" / "batches").glob("*/factory_trial_ledger.json")))
        unique_paths: list[Path] = []
        seen_paths: set[str] = set()
        for path in paths:
            key = str(path.resolve())
            if path.exists() and key not in seen_paths:
                seen_paths.add(key)
                unique_paths.append(path)
        latest: dict[str, tuple[int, Mapping[str, Any]]] = {}
        for priority, path in enumerate(unique_paths):
            payload = self._json(path, {}) or {}
            local: dict[str, Mapping[str, Any]] = {}
            for event in payload.get("events", ()):
                if isinstance(event, Mapping) and str(event.get("objective_id")) == self.objective_id and event.get("trial_id"):
                    local[str(event["trial_id"])] = event
            for trial_id, event in local.items():
                if trial_id not in latest:
                    latest[trial_id] = (priority, event)
        return tuple(dict(item[1]) for item in sorted(latest.values(), key=lambda pair: str(pair[1].get("trial_id"))))

    def snapshot(self) -> CanonicalResearchSnapshotV2:
        objective, objective_hash = self._objective()
        runtime_dir = self.root / "reports" / "research_daemon" / self.objective_id
        daemon_payload = self._json(runtime_dir / "daemon_checkpoint.json", {}) or {}
        status_payload = self._json(runtime_dir / "daemon_status.json", {}) or {}
        daemon_state = str(daemon_payload.get("current_state") or status_payload.get("daemon_state") or "BOOTSTRAP")
        budget_path = self._budget_path(daemon_payload, status_payload)
        registry = SearchBudgetRegistryV1(self.objective_id, budget_path)
        budget_payload = registry.snapshot()
        objective_bucket = next((item for item in budget_payload.get("buckets", ()) if item.get("kind") == "objective" and item.get("key") == self.objective_id), {})
        budget = {
            "objective_id": self.objective_id,
            "used": int(objective_bucket.get("used", 0)),
            "total": int(objective_bucket.get("limit", objective.get("max_total_trials", 0))),
            "remaining": int(objective_bucket.get("remaining", 0)),
            "reserved": int(objective_bucket.get("reserved", 0)),
            "registry_path": str(budget_path.relative_to(self.root)).replace("\\", "/"),
            "registry_head_hash": registry.head_hash,
        }
        candidates = self._candidates()
        trials = self._trials(budget_path.parent / "factory_trial_ledger.json")
        completed_candidate_ids = {str(item.get("candidate_id")) for item in trials if item.get("candidate_id")}
        last_completed = daemon_payload.get("last_completed_candidate")
        if isinstance(last_completed, Mapping) and last_completed.get("candidate_id"):
            completed_candidate_ids.add(str(last_completed["candidate_id"]))
        events_path = runtime_dir / "daemon_events.jsonl"
        active_candidate: str | None = None
        if events_path.exists():
            for line in events_path.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except (UnicodeError, json.JSONDecodeError):
                    continue
                candidate = event.get("candidate")
                candidate_id = str(candidate.get("candidate_id") or "") if isinstance(candidate, Mapping) else str(candidate or "")
                new_state = str(event.get("new_state") or "")
                if candidate_id:
                    active_candidate = candidate_id
                if new_state == "CANDIDATE_COMPLETE" and active_candidate:
                    completed_candidate_ids.add(active_candidate)
                if new_state in {"NEXT_CANDIDATE", "READY"}:
                    active_candidate = None
        completed_candidate_ids.update(load_effective_contract_invalidations(self.root, self.objective_id))
        effective = self._json(self.root / "reports" / "CURRENT_EFFECTIVE_RESEARCH_HISTORY_V1.json", {}) or {}
        counts = {str(key): int(value) for key, value in (effective.get("effective_final_classifications") or {}).items()}
        if not counts:
            for trial in trials:
                classification = str(trial.get("classification") or "UNKNOWN")
                counts[classification] = counts.get(classification, 0) + 1
        search_space = self._json(self.root / "reports" / "AUTONOMOUS_ALPHA_RESEARCH_SEARCH_SPACE_STATUS_V1.json", {}) or {}
        computed_remaining = sum(item["candidate_id"] not in completed_candidate_ids for item in candidates)
        # The daemon status is a derived cache and may lag after a checkpoint.
        # Canonical contracts minus canonical completions is the only safe queue view.
        remaining = computed_remaining
        return CanonicalResearchSnapshotV2(
            objective_id=self.objective_id,
            objective_hash=objective_hash,
            daemon_state=daemon_state,
            daemon_run_id=str(daemon_payload.get("daemon_run_id") or status_payload.get("daemon_run_id")) if (daemon_payload.get("daemon_run_id") or status_payload.get("daemon_run_id")) else None,
            budget=budget,
            candidates=candidates,
            trials=trials,
            research_counts=counts,
            global_search_exhausted=bool(search_space.get("global_legal_search_exhausted", status_payload.get("global_search_exhausted", False))),
            remaining_frozen_candidates=int(remaining or 0),
            terminal_reason=str((daemon_payload.get("last_transition") or {}).get("reason_code") or daemon_payload.get("error_reason_code") or daemon_state),
            resource_state={"process_pid": status_payload.get("process_pid"), "available_memory_bytes": status_payload.get("system_available_memory_bytes")},
            required_action=str(
                daemon_payload.get("required_action")
                or status_payload.get("required_human_ai_action")
                or ""
            ) or None,
        )


class OrchestratorRuntimeV2(Protocol):
    def snapshot(self) -> CanonicalResearchSnapshotV2: ...
    def run_local(self) -> Mapping[str, Any]: ...
    def ingest_ai_batch(self, manifest_path: Path, manifest: Mapping[str, Any], *, staging_dir: Path | None = None) -> Mapping[str, Any]: ...


class CanonicalOrchestratorRuntimeV2:
    """Supervise the existing daemon and canonical frozen-contract registry."""

    def __init__(self, root: str | Path, objective_id: str):
        self.root = Path(root).resolve()
        self.objective_id = str(objective_id)
        self.reader = CanonicalResearchStateReaderV2(self.root, self.objective_id)

    def snapshot(self) -> CanonicalResearchSnapshotV2:
        return self.reader.snapshot()

    def run_local(self) -> Mapping[str, Any]:
        from ..research_daemon import ResearchDaemon

        return ResearchDaemon(self.root, objective_id=self.objective_id).run_once()

    @staticmethod
    def _safe_ref(root: Path, ref: str) -> Path:
        path = (root / str(ref).replace("\\", "/")).resolve()
        if Path(ref).is_absolute() or not path.is_relative_to(root) or not path.exists():
            raise RuntimeError(f"AI_BATCH_UNSAFE_ARTIFACT_REF:{ref}")
        return path

    def ingest_ai_batch(self, manifest_path: Path, manifest: Mapping[str, Any], *, staging_dir: Path | None = None) -> Mapping[str, Any]:
        from ..research_daemon import CanonicalResearchRuntime

        candidate_contracts = list(manifest.get("candidate_contracts", ()))
        for ref in manifest.get("durable_contract_refs", ()):
            if _is_allowed_inline_ref(ref, manifest):
                continue
            try:
                path = self._safe_ref(self.root, str(ref))
            except RuntimeError:
                if staging_dir is None:
                    raise
                path = self._safe_ref(Path(staging_dir).resolve(), str(ref))
            payload = json.loads(path.read_text(encoding="utf-8"))
            candidate_contracts.extend(payload.get("contracts", ()) if isinstance(payload, Mapping) else ())
        if not candidate_contracts:
            raise RuntimeError("AI_BATCH_DURABLE_CONTRACTS_REQUIRED")
        batch_id = str(manifest.get("batch_id") or f"AI_{str(manifest['handoff_id'])[:16]}")
        target = self.root / "data" / "research" / "research_factory" / "batches" / batch_id / "durable_frozen_candidate_contracts.json"
        registry = DurableFrozenCandidateContractRegistryV1(target)
        manifest_hashes = {str(key): str(value) for key, value in (manifest.get("candidate_hashes") or {}).items()}
        durable_contracts: list[DurableFrozenCandidateContractV1] = []
        for raw in candidate_contracts:
            contract = raw if isinstance(raw, Mapping) else {}
            candidate_id = str(contract.get("candidate_id") or "")
            if not candidate_id or candidate_id not in set(str(item) for item in manifest.get("candidate_ids", ())):
                raise RuntimeError("AI_BATCH_CANDIDATE_IDENTITY_INVALID")
            if manifest_hashes.get(candidate_id) != str(contract.get("candidate_hash") or contract.get("content_hash") or ""):
                raise RuntimeError(f"AI_BATCH_CANDIDATE_HASH_INVALID:{candidate_id}")
            try:
                durable_contracts.append(DurableFrozenCandidateContractV1.from_dict(contract))
            except (TypeError, ValueError, KeyError) as exc:
                raise RuntimeError(f"AI_BATCH_DURABLE_CONTRACT_INVALID:{candidate_id}") from exc
        accepted = CanonicalResearchRuntime(self.root, objective_id=self.objective_id).accept_ai_batch(manifest_path)
        for contract in durable_contracts:
            registry.append(contract)
        if durable_contracts:
            registry.write()
        graph = ResearchArtifactGraphV1(self.root / "reports" / "research_factory" / "artifact_graph.json")
        graph.add_node(f"objective:{self.objective_id}", "Objective", {"objective_id": self.objective_id})
        for item in manifest.get("candidates", ()):
            safe = design_safe_candidate(item)
            candidate_id = str(safe["candidate_id"])
            graph.add_node(f"candidate:{candidate_id}", "Candidate", safe)
            graph.add_node(f"frozen_contract:{candidate_id}", "FrozenCandidateContract", {"candidate_id": candidate_id, "candidate_hash": safe["candidate_hash"], "source": str(target.relative_to(self.root)).replace("\\", "/")})
            graph.add_edge(f"candidate:{candidate_id}", "GENERATED_FROM", f"objective:{self.objective_id}")
            graph.add_edge(f"candidate:{candidate_id}", "HAS_DURABLE_CONTRACT", f"frozen_contract:{candidate_id}")
        return {**dict(accepted), "canonical_contract_registry": str(target.relative_to(self.root)).replace("\\", "/")}


class SyntheticAutonomousResearchRuntimeV2:
    """Small deterministic runtime used by the long-run acceptance test."""

    def __init__(self, objective_id: str = "SYNTHETIC_ORCHESTRATOR_OBJECTIVE", *, budget_total: int = 2, initial_candidates: Sequence[Mapping[str, Any]] = (), outcomes: Sequence[str] = ("REJECTED", "PROMISING")):
        self.objective_id = str(objective_id)
        self.budget_total = int(budget_total)
        self.queue = [dict(item) for item in initial_candidates]
        self.candidates = {str(item["candidate_id"]): dict(item) for item in initial_candidates}
        self.trials: list[dict[str, Any]] = []
        self.outcomes = tuple(str(item) for item in outcomes) or ("REJECTED",)
        self.ai_batches = 0
        self.local_calls = 0

    def snapshot(self) -> CanonicalResearchSnapshotV2:
        used = len(self.trials)
        if used >= self.budget_total:
            state = "BUDGET_EXHAUSTED"
        elif self.queue:
            state = "READY"
        else:
            state = "NEED_AI_RESEARCH_DESIGN"
        counts: dict[str, int] = {}
        for trial in self.trials:
            key = str(trial.get("classification") or "UNKNOWN")
            counts[key] = counts.get(key, 0) + 1
        trial_candidate_ids = {str(item.get("candidate_id")) for item in self.trials}
        return CanonicalResearchSnapshotV2(
            objective_id=self.objective_id,
            objective_hash=stable_hash({"objective_id": self.objective_id, "budget_total": self.budget_total}),
            daemon_state=state,
            daemon_run_id="SYNTHETIC_DAEMON",
            budget={"objective_id": self.objective_id, "used": used, "total": self.budget_total, "remaining": max(0, self.budget_total - used), "reserved": 0, "registry_head_hash": stable_hash(self.trials)},
            candidates=tuple(self.candidates[key] for key in sorted(self.candidates)),
            trials=tuple(self.trials),
            research_counts=counts,
            remaining_frozen_candidates=sum(key not in trial_candidate_ids for key in self.candidates),
        )

    def run_local(self) -> Mapping[str, Any]:
        if not self.queue or len(self.trials) >= self.budget_total:
            return self.snapshot().__dict__
        candidate = self.queue.pop(0)
        self.local_calls += 1
        trial = {
            "trial_id": f"SYNTH_TRIAL_{len(self.trials) + 1:03d}",
            "candidate_id": str(candidate["candidate_id"]),
            "candidate_hash": str(candidate.get("candidate_hash") or ""),
            "status": "COMPLETED",
            "classification": self.outcomes[len(self.trials) % len(self.outcomes)],
            "performance_accessed": True,
        }
        self.trials.append(trial)
        return self.snapshot().__dict__

    def ingest_ai_batch(self, manifest_path: Path, manifest: Mapping[str, Any], *, staging_dir: Path | None = None) -> Mapping[str, Any]:
        accepted = 0
        for item in manifest.get("candidates", ()):
            candidate = dict(item)
            candidate_id = str(candidate["candidate_id"])
            if candidate_id not in self.candidates:
                candidate["source_handoff_id"] = str(manifest["handoff_id"])
                self.candidates[candidate_id] = candidate
                self.queue.append(candidate)
                accepted += 1
        self.ai_batches += 1
        return {"status": "ACCEPTED", "candidate_count": accepted, "manifest": str(manifest_path)}


class AIInvocationError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = True):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class AIResearchBatchInvokerV2(Protocol):
    def invoke(self, handoff: Mapping[str, Any], *, invocation_id: str, staging_dir: Path, timeout_seconds: int) -> Mapping[str, Any]: ...


class SyntheticCodexBatchInvokerV2:
    def __init__(self, factory: Callable[[Mapping[str, Any], str], Mapping[str, Any]] | None = None):
        self.factory = factory
        self.calls = 0

    def invoke(self, handoff: Mapping[str, Any], *, invocation_id: str, staging_dir: Path, timeout_seconds: int) -> Mapping[str, Any]:
        self.calls += 1
        if self.factory is not None:
            return dict(self.factory(handoff, invocation_id))
        objective_id = str(handoff["objective_id"])
        candidate_id = f"AI_CANDIDATE_{self.calls:03d}"
        candidate_hash = f"AI_HASH_{self.calls:03d}"
        semantic_fingerprint = f"AI_SEMANTIC_{self.calls:03d}"
        contract = {
            "candidate_id": candidate_id,
            "candidate_hash": candidate_hash,
            "hypothesis_id": f"AI_HYPOTHESIS_{self.calls:03d}",
            "hypothesis_fingerprint": f"AI_HYPOTHESIS_FINGERPRINT_{self.calls:03d}",
            "semantic_fingerprint": semantic_fingerprint,
            "full_semantic_record": {"candidate": {"candidate_id": candidate_id, "preregistration_hash": candidate_hash}, "preregistration_hash": candidate_hash, "semantic_fingerprint": semantic_fingerprint},
            "family": "synthetic_new",
            "mechanism": "synthetic_new",
            "factor_ids": ["SYNTHETIC_FACTOR"],
            "factor_roles": [{"factor_id": "SYNTHETIC_FACTOR", "role": "SIGNAL"}],
            "factor_directions": {"SYNTHETIC_FACTOR": "POSITIVE"},
            "event_ids": [],
            "event_timing_semantics": {},
            "entry_predicate": {"type": "FACTOR_THRESHOLD", "factor_id": "SYNTHETIC_FACTOR"},
            "confirmation_predicate": {},
            "interaction_semantics": {"eligibility_conditions": [], "interaction_conditions": []},
            "ranking_semantics": {},
            "selection_rule": {"type": "TOP_N", "top_n": 1},
            "top_n": 1,
            "max_positions": 1,
            "holding_period_trading_sessions": 5,
            "entry_timing": {"type": "NEXT_SESSION_OPEN"},
            "exit_contract": {"type": "FIXED_HOLD", "sessions": 5},
            "execution_contract_version": "SYNTHETIC_EXECUTION_V1",
            "t_plus_1_contract": {"enabled": True},
            "capital_product_contract_identity": {"contract_id": "SYNTHETIC_CAPITAL_V1"},
            "fee_slippage_contract_references": {"contract_id": "SYNTHETIC_COST_V1"},
            "pit_dependencies": {"universe_rule": {"market": ["SH", "SZ"]}, "pit_required": True},
            "factor_event_registry_identities": {"factor_registry": "SYNTHETIC_FACTOR_REGISTRY_V1"},
            "research_period_identity": {"period_id": "SYNTHETIC_RESEARCH_PERIOD_V1"},
            "policy_identity": {"objective_id": objective_id},
            "created_frozen_timestamp": handoff["created_at"],
            "source_provenance": {"handoff_id": handoff["handoff_id"]},
            "contract_schema_version": "durable-frozen-candidate-contract-v1",
        }
        contract["content_hash"] = stable_hash(contract)
        return {
            "schema_version": "ai-research-batch-result-v2",
            "handoff_id": handoff["handoff_id"],
            "objective_id": objective_id,
            "ai_invocation_id": invocation_id,
            "batch_id": f"AI_BATCH_{self.calls:03d}",
            "hypothesis_ids": [f"AI_HYPOTHESIS_{self.calls:03d}"],
            "candidate_ids": [candidate_id],
            "candidate_hashes": {candidate_id: candidate_hash},
            "candidates": [{"candidate_id": candidate_id, "candidate_hash": candidate_hash, "family_id": "synthetic_new", "mechanism": "synthetic_new", "factor_ids": ["SYNTHETIC_FACTOR"], "event_ids": [], "holding_period_days": 5}],
            "candidate_contracts": [contract],
            "durable_contract_refs": ["inline:candidate_contracts[0]"],
            "artifact_refs": [],
            "validation_status": "VALID",
            "no_outcome_compliance_status": "PASS",
            "prompt_template_version": "AUTONOMOUS_RESEARCH_ORCHESTRATOR_V2_PROMPT_V1",
            "prompt_hash": stable_hash(handoff),
            "handoff_hash": stable_hash(handoff),
            "generated_at": now_timestamp(),
        }


class CodexExecBatchInvokerV2:
    """Controlled non-interactive ``codex exec`` adapter for design only."""

    def __init__(self, root: str | Path, executable: str | Path | None = None):
        from .codex_backend import SubprocessCodexExecutorV1, discover_codex_executable

        self.root = Path(root).resolve()
        self.executable = str(executable or discover_codex_executable() or "")
        self._executor_type = SubprocessCodexExecutorV1

    def invoke(self, handoff: Mapping[str, Any], *, invocation_id: str, staging_dir: Path, timeout_seconds: int) -> Mapping[str, Any]:
        if not self.executable:
            raise AIInvocationError("AI_INVOCATION_UNAVAILABLE:CODEX_RUNTIME_NOT_AVAILABLE", retryable=False)
        from .codex_backend import CodexInvocationResultV1, _redact_runtime_text

        staging_dir.mkdir(parents=True, exist_ok=True)
        handoff_path = staging_dir / "RESEARCH_ORCHESTRATOR_AI_HANDOFF.json"
        handoff_path.write_text(json.dumps(handoff, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        schema_path = staging_dir / "AI_RESEARCH_BATCH_RESULT_V2.schema.json"
        schema_path.write_text(json.dumps(_batch_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        durable_schema_path = staging_dir / "DURABLE_FROZEN_CANDIDATE_CONTRACT_V1.schema.json"
        durable_schema_path.write_text(json.dumps(_durable_contract_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        hash_helper_path = staging_dir / "codex_contract_hash_helper.py"
        hash_helper_path.write_text(_CONTRACT_HASH_HELPER, encoding="utf-8", newline="\n")
        response_path = staging_dir / "AI_RESEARCH_BATCH_RESULT_V2.json"
        prompt = build_codex_prompt_v2(handoff, handoff_path.name, schema_path.name, durable_schema_path.name, hash_helper_path.name)
        (staging_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        executor = self._executor_type(self.executable)
        result = executor.execute(SimpleNamespace(prompt=prompt), staging_dir=staging_dir, output_schema_path=schema_path, response_path=response_path, timeout_seconds=timeout_seconds)
        (staging_dir / "codex_invocation_result.json").write_text(json.dumps({
            "schema_version": "codex-invocation-result-v1",
            "invocation_id": invocation_id,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "runtime_version": result.runtime_version,
            "model_id": result.model_id,
            "usage": dict(result.usage),
            "duration_seconds": result.duration_seconds,
            "command": list(result.command),
            "process_id": result.process_id,
            "started_at": result.started_at,
            "last_stdout_at": result.last_stdout_at,
            "last_stderr_at": result.last_stderr_at,
            "last_progress_at": result.last_progress_at,
            "manifest_detected_at": result.manifest_detected_at,
            "process_exit_at": result.process_exit_at,
            "timeout_type": result.timeout_type,
            "output_mode": result.output_mode,
            "runtime_diagnostics_path": str((staging_dir / "codex_runtime_diagnostics.json").relative_to(self.root)).replace("\\", "/"),
            "stdout_tail": _redact_runtime_text(result.stdout[-4000:]),
            "stderr_tail": _redact_runtime_text(result.stderr[-4000:]),
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if result.timed_out:
            raise AIInvocationError("CODEX_TIMEOUT")
        if result.exit_code != 0:
            combined_output = f"{result.stdout}\n{result.stderr}".casefold()
            if "requires a newer version of codex" in combined_output:
                code = "AI_INVOCATION_UNAVAILABLE:CODEX_MODEL_UNSUPPORTED"
            elif "invalid_json_schema" in combined_output or "additionalproperties' is required" in combined_output:
                code = "AI_INVOCATION_UNAVAILABLE:CODEX_OUTPUT_SCHEMA_REJECTED"
            elif any(item in combined_output for item in ("auth", "login", "quota")):
                code = "AI_INVOCATION_UNAVAILABLE:CODEX_AUTH_NOT_AVAILABLE"
            else:
                code = "CODEX_NONZERO_EXIT"
            raise AIInvocationError(code)
        raw = result.response_text or result.stdout
        if not raw.strip():
            raise AIInvocationError("CODEX_INVALID_RESPONSE")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AIInvocationError("CODEX_INVALID_RESPONSE") from exc
        if not isinstance(payload, Mapping):
            raise AIInvocationError("CODEX_INVALID_RESPONSE")
        return dict(payload)


def build_codex_prompt_v2(
    handoff: Mapping[str, Any],
    handoff_filename: str = "RESEARCH_ORCHESTRATOR_AI_HANDOFF_CURRENT.json",
    schema_filename: str = "AI_RESEARCH_BATCH_RESULT_V2.schema.json",
    durable_schema_filename: str = "DURABLE_FROZEN_CANDIDATE_CONTRACT_V1.schema.json",
    hash_helper_filename: str = "codex_contract_hash_helper.py",
) -> str:
    """Build a deterministic, design-only prompt; no user copy/paste is needed."""

    PerformanceBlindGuard.assert_blind(handoff)
    return "\n".join((
        "You are the on-demand AI Research Designer for Autonomous Research Orchestrator V2.",
        f"Read {handoff_filename}, {schema_filename}, and {durable_schema_filename} before doing anything.",
        f"Return exactly one JSON object conforming to {schema_filename}; its schema_version must be the lowercase value ai-research-batch-result-v2.",
        "Generate exactly one candidate and one complete durable candidate contract; do not generate four variants.",
        "Use only the current staging directory for file reads/writes. Do not inspect the repository or use MCP/apps.",
        f"After writing the contract draft in staging, run only `python {hash_helper_filename} <draft_contract.json>`; this official helper writes the candidate fingerprint/preregistration_hash, semantic_fingerprint, candidate_hash, and exact content_hash. Do not implement hashing from memory and do not run any research or data command.",
        "The durable contract's full_semantic_record must contain candidate.candidate_id equal to candidate_id, candidate.preregistration_hash equal to candidate_hash, top-level preregistration_hash equal to candidate_hash, and top-level semantic_fingerprint equal to semantic_fingerprint. These four identity values must match exactly; do not use a hypothesis-only object as full_semantic_record.",
        "full_semantic_record must contain the complete StrategyCandidateSpec, SignalPredicateSpec, and ExitPredicateSpec fields required by the supplied durable schema so the local Provider can reconstruct it exactly; an identity-only placeholder is invalid.",
        "Keep the contract concise but executable: policy_identity must be exactly {\"objective_id\":<objective_id from the handoff>}; entry/selection/timing/exit/T+1/capital/fee/PIT/registry/research-period/provenance mappings must contain concrete frozen semantics and must not be empty. At least one factor_id or event_id is required, with matching factor/event timing and registry identities. Use top_n=1, max_positions=1, and a legal preferred holding horizon.",
        "The durable contract content_hash is SHA-256 of canonical JSON for the entire contract with content_hash removed: UTF-8, sorted keys, compact separators, and no whitespace. Verify it before returning.",
        "The hash helper also writes content_hash back into the draft in place. Do not invoke a file editor or file-change tool after running the helper; read the completed draft and return the final batch JSON directly.",
        "Never return a blocked/error envelope. If a draft is incomplete, repair it in staging and then return the valid batch object.",
        "Design only legal, novel hypotheses and freeze a candidate batch in the controlled staging workspace.",
        "Respect the supplied PIT rules, factor/data capabilities, execution semantics, novelty and search-space constraints.",
        "Do not run Provider, backtest, predictive Trial, bootstrap, multiple-testing, PerformanceAccess or reports.",
        "Do not inspect or infer private predictive outcomes. Do not change budget, governance, TrialLedger or canonical objective state.",
        "Stop after producing the machine-ingestable candidate batch manifest.",
    ))


def _batch_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["schema_version", "handoff_id", "objective_id", "ai_invocation_id", "candidate_ids", "candidate_hashes", "candidates", "candidate_contracts", "durable_contract_refs", "artifact_refs", "validation_status", "no_outcome_compliance_status"],
        "properties": {
            "schema_version": {"const": "ai-research-batch-result-v2"},
            "handoff_id": {"type": "string", "minLength": 1},
            "objective_id": {"type": "string", "minLength": 1},
            "ai_invocation_id": {"type": "string", "minLength": 1},
            "candidate_ids": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
            "candidate_hashes": {"type": "object", "minProperties": 1, "additionalProperties": {"type": "string", "minLength": 1}},
            "candidates": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["candidate_id", "candidate_hash", "family_id", "mechanism", "factor_ids", "event_ids", "holding_period_days"],
                    "properties": {
                        "candidate_id": {"type": "string", "minLength": 1},
                        "candidate_hash": {"type": "string", "minLength": 1},
                        "family_id": {"type": "string", "minLength": 1},
                        "mechanism": {"type": "string", "minLength": 1},
                        "factor_ids": {"type": "array", "items": {"type": "string"}},
                        "event_ids": {"type": "array", "items": {"type": "string"}},
                        "holding_period_days": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "additionalProperties": True,
                },
            },
            "candidate_contracts": {"type": "array", "minItems": 1, "items": _durable_contract_schema()},
            "durable_contract_refs": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
            "artifact_refs": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "validation_status": {"const": "VALID"},
            "no_outcome_compliance_status": {"const": "PASS"},
        },
        "additionalProperties": False,
    }


def _durable_contract_schema() -> dict[str, Any]:
    """Expose the runtime-reconstructable durable contract shape to the AI child."""
    fields = tuple(DurableFrozenCandidateContractV1.__dataclass_fields__)
    string_schema = {"type": "string", "minLength": 1}

    factor_binding_schema = {
        "type": "object",
        "required": ["factor_id"],
        "properties": {
            "factor_id": dict(string_schema),
            "role": dict(string_schema),
            "direction": dict(string_schema),
        },
        "additionalProperties": True,
    }
    factor_role_schema = {
        "type": "object",
        "required": ["factor_id", "role", "direction"],
        "properties": {
            "factor_id": dict(string_schema),
            "role": dict(string_schema),
            "direction": dict(string_schema),
        },
        "additionalProperties": True,
    }

    candidate_fields = tuple(StrategyCandidateSpec.__dataclass_fields__)
    candidate_properties: dict[str, Any] = {
        name: dict(string_schema)
        for name in {
            "candidate_id", "candidate_version", "parent_hypothesis_id", "name", "description",
            "mechanism", "entry_rule", "position_sizing_rule", "cash_rule", "price_mode",
            "fee_model_reference", "slippage_model_reference", "small_capital_prior", "created_at",
            "builder_version", "fingerprint", "preregistration_hash", "complexity_justification",
        }
    }
    candidate_properties.update({
        "candidate_id": {"type": "string", "pattern": r"^CAND_[A-Z0-9_]+_V\d+$"},
        "strategy_family": {"type": "string", "enum": sorted(STRATEGY_FAMILIES)},
        "candidate_status": {"type": "string", "enum": sorted(CANDIDATE_STATUSES)},
        "candidate_type": {"type": "string", "enum": sorted(SIGNAL_FREQUENCIES)},
        "factor_bindings": {"type": "array", "minItems": 1, "items": factor_binding_schema},
        "factor_roles": {"type": "array", "minItems": 1, "items": factor_role_schema},
        "signal_logic": {"type": "object", "minProperties": 1},
        "entry_timing": {"type": "object", "minProperties": 1},
        "ranking_rule": {"type": "object", "minProperties": 1},
        "selection_rule": {"type": "object", "minProperties": 1},
        "exit_rule": {"type": "object", "minProperties": 1},
        "risk_filters": {"type": "array", "minItems": 1, "items": {"type": "object"}},
        "execution_filters": {"type": "array", "minItems": 1, "items": {"type": "object"}},
        "universe_rule": {"type": "object", "minProperties": 1},
        "required_data": {"type": "array", "minItems": 1, "items": dict(string_schema)},
        "required_frequency": {"type": "array", "minItems": 1, "items": dict(string_schema)},
        "available_at_contract": {"type": "object", "minProperties": 1},
        "t_plus_1_contract": {"type": "object", "minProperties": 1},
        "limit_up_down_contract": {"type": "object", "minProperties": 1},
        "suspension_contract": {"type": "object", "minProperties": 1},
        "lot_size_contract": {"type": "object", "minProperties": 1},
        "failure_conditions": {"type": "array", "minItems": 1, "items": dict(string_schema)},
        "parameter_spec": {"type": "array", "items": {"type": "object"}},
        "parameter_provenance": {"type": "object", "minProperties": 1},
        "validation_plan": {"type": "array", "minItems": 1, "items": {"type": "object"}},
        "source_provenance": {"type": "object", "minProperties": 1},
        "conflict_semantics": {"type": "object", "minProperties": 1},
        "strategy_dsl": {
            "type": "object",
            "required": ["schema_version"],
            "properties": {"schema_version": {"const": DSL_VERSION}},
            "additionalProperties": True,
        },
        "failure_overlap_warning": {"type": "object"},
    })
    for name in {"holding_period", "max_positions", "degrees_of_freedom", "complexity_score"}:
        candidate_properties[name] = {"type": "integer"}
    candidate_properties["holding_period"].update({"minimum": 1, "maximum": 10})
    candidate_properties["max_positions"].update({"minimum": 1, "maximum": 3})
    candidate_properties["degrees_of_freedom"].update({"minimum": 0, "maximum": 5})
    candidate_properties["complexity_score"].update({"minimum": 1, "maximum": 5})
    candidate_schema = {
        "type": "object",
        "required": list(candidate_fields),
        "properties": candidate_properties,
        "additionalProperties": False,
    }

    signal_fields = tuple(SignalPredicateSpec.__dataclass_fields__)
    signal_properties: dict[str, Any] = {
        name: dict(string_schema)
        for name in {"predicate_id", "source_hypothesis_id", "source_candidate_id", "explanation"}
    }
    signal_properties.update({
        "predicate_type": {"type": "string", "enum": sorted(PREDICATE_TYPES)},
        "logic": {"type": "string", "enum": ["AND", "OR", "RANK_ONLY"]},
        "semantic_status": {"type": "string", "enum": sorted(SEMANTIC_STATUSES)},
        "factor_conditions": {"type": "array", "items": {"type": "object"}},
        "event_conditions": {"type": "array", "items": {"type": "object"}},
        "regime_conditions": {"type": "array", "items": {"type": "object"}},
        "interaction_conditions": {"type": "array", "items": {"type": "object"}},
        "eligibility_conditions": {"type": "array", "items": {"type": "object"}},
        "availability_contract": {"type": "object", "minProperties": 1},
        "parameter_dependencies": {"type": "array", "items": dict(string_schema)},
        "parameter_source": {"type": "object", "minProperties": 1},
        "pit_requirements": {"type": "array", "minItems": 1, "items": dict(string_schema)},
    })
    signal_schema = {"type": "object", "required": list(signal_fields), "properties": signal_properties, "additionalProperties": False}

    exit_fields = tuple(ExitPredicateSpec.__dataclass_fields__)
    exit_properties: dict[str, Any] = {
        name: dict(string_schema)
        for name in {"predicate_id", "source_hypothesis_id", "source_candidate_id", "explanation"}
    }
    exit_properties.update({
        "exit_type": {"type": "string", "enum": sorted(EXIT_TYPES)},
        "logic": {"type": "string", "enum": ["AND", "OR", "FIXED_HOLD"]},
        "semantic_status": {"type": "string", "enum": ["COMPLETE", "BLOCKED_EXIT_PREDICATE", "SEMANTIC_REVIEW_REQUIRED"]},
        "factor_conditions": {"type": "array", "items": {"type": "object"}},
        "event_conditions": {"type": "array", "items": {"type": "object"}},
        "regime_conditions": {"type": "array", "items": {"type": "object"}},
        "availability_contract": {"type": "object", "minProperties": 1},
        "parameter_dependencies": {"type": "array", "items": dict(string_schema)},
        "parameter_source": {"type": "object", "minProperties": 1},
        "pit_requirements": {"type": "array", "minItems": 1, "items": dict(string_schema)},
    })
    exit_schema = {"type": "object", "required": list(exit_fields), "properties": exit_properties, "additionalProperties": False}

    full_record_fields = (
        "candidate", "parent_candidate_id", "previous_preregistration_hash", "semantic_change_reason",
        "signal_predicate", "exit_predicate", "semantic_status", "phase4_eligible", "semantic_fingerprint",
        "preregistration_hash", "created_at",
    )
    full_record_schema = {
        "type": "object",
        "required": list(full_record_fields),
        "properties": {
            "candidate": candidate_schema,
            "parent_candidate_id": {"type": "string"},
            "previous_preregistration_hash": {"type": "string"},
            "semantic_change_reason": dict(string_schema),
            "signal_predicate": signal_schema,
            "exit_predicate": exit_schema,
            "semantic_status": {"type": "string", "enum": sorted(SEMANTIC_STATUSES)},
            "phase4_eligible": {"type": "boolean"},
            "semantic_fingerprint": dict(string_schema),
            "preregistration_hash": dict(string_schema),
            "created_at": dict(string_schema),
        },
        "additionalProperties": False,
    }

    properties: dict[str, Any] = {name: dict(string_schema) for name in fields}
    properties.update({
        "factor_ids": {"type": "array", "minItems": 1, "items": dict(string_schema)},
        "event_ids": {"type": "array", "items": dict(string_schema)},
        "factor_roles": {"type": "array", "minItems": 1, "items": factor_role_schema},
        "full_semantic_record": full_record_schema,
        "factor_directions": {"type": "object", "minProperties": 1},
        "event_timing_semantics": {"type": "object"},
        "entry_predicate": {"type": "object", "minProperties": 1},
        "confirmation_predicate": {"type": "object"},
        "interaction_semantics": {
            "type": "object",
            "properties": {name: {"type": "array", "items": {"type": "object"}} for name in DURABLE_INTERACTION_SEMANTICS_FIELDS},
            "required": list(DURABLE_INTERACTION_SEMANTICS_FIELDS),
            "minProperties": len(DURABLE_INTERACTION_SEMANTICS_FIELDS),
            "additionalProperties": False,
        },
        "ranking_semantics": {"type": "object"},
        "selection_rule": {"type": "object", "minProperties": 1},
        "entry_timing": {"type": "object", "minProperties": 1},
        "exit_contract": {"type": "object", "minProperties": 1},
        "t_plus_1_contract": {"type": "object", "minProperties": 1},
        "capital_product_contract_identity": {"type": "object", "minProperties": 1},
        "fee_slippage_contract_references": {"type": "object", "minProperties": 1},
        "pit_dependencies": {"type": "object", "minProperties": 1},
        "factor_event_registry_identities": {"type": "object", "minProperties": 1},
        "research_period_identity": {"type": "object", "minProperties": 1},
        "policy_identity": {
            "type": "object",
            "properties": {"objective_id": dict(string_schema)},
            "required": ["objective_id"],
            "additionalProperties": False,
        },
        "source_provenance": {"type": "object", "minProperties": 1},
        "top_n": {"type": "integer", "minimum": 1, "maximum": 3},
        "max_positions": {"type": "integer", "minimum": 1, "maximum": 3},
        "holding_period_trading_sessions": {"type": "integer", "minimum": 1, "maximum": 10},
        "family": {"type": "string", "enum": ["DAILY_EVENT", "DAILY_CROSS_SECTIONAL", "DAILY_FACTOR"]},
        "contract_schema_version": {"type": "string", "const": FROZEN_CANDIDATE_CONTRACT_SCHEMA},
    })
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": list(fields),
        "properties": properties,
        "additionalProperties": False,
    }


_CONTRACT_HASH_HELPER = """from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


CORE_FIELDS = (
    \"candidate_version\", \"parent_hypothesis_id\", \"mechanism\", \"strategy_family\", \"factor_bindings\",
    \"factor_roles\", \"signal_logic\", \"entry_rule\", \"entry_timing\", \"ranking_rule\", \"selection_rule\",
    \"exit_rule\", \"holding_period\", \"position_sizing_rule\", \"max_positions\", \"cash_rule\", \"risk_filters\",
    \"execution_filters\", \"universe_rule\", \"price_mode\", \"required_data\", \"required_frequency\",
    \"available_at_contract\", \"t_plus_1_contract\", \"limit_up_down_contract\", \"suspension_contract\",
    \"lot_size_contract\", \"fee_model_reference\", \"slippage_model_reference\", \"parameter_spec\",
    \"parameter_provenance\", \"degrees_of_freedom\", \"complexity_score\", \"failure_conditions\",
    \"validation_plan\", \"small_capital_prior\", \"source_provenance\", \"candidate_type\", \"conflict_semantics\",
    \"complexity_justification\", \"strategy_dsl\",
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(\",\", \":\"), default=str)


def digest(value):
    return hashlib.sha256(canonical(value).encode(\"utf-8\")).hexdigest()


path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding=\"utf-8\"))
record = payload.get(\"full_semantic_record\")
candidate = record.get(\"candidate\") if isinstance(record, dict) else None
if not isinstance(candidate, dict):
    raise SystemExit(\"full_semantic_record.candidate must be an object\")
candidate_core = {key: candidate[key] for key in sorted(CORE_FIELDS) if key in candidate}
candidate_hash = digest(candidate_core)
candidate[\"fingerprint\"] = candidate_hash
candidate[\"preregistration_hash\"] = candidate_hash
record[\"preregistration_hash\"] = candidate_hash
semantic_payload = {\"signal_predicate\": record.get(\"signal_predicate\"), \"exit_predicate\": record.get(\"exit_predicate\")}
semantic_fingerprint = digest(semantic_payload)
record[\"semantic_fingerprint\"] = semantic_fingerprint
payload[\"candidate_hash\"] = candidate_hash
payload[\"semantic_fingerprint\"] = semantic_fingerprint
payload.pop(\"content_hash\", None)
payload[\"content_hash\"] = digest(payload)
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + \"\\n\", encoding=\"utf-8\")
print(payload[\"content_hash\"])
"""


class AIBatchValidationError(RuntimeError):
    pass


_INLINE_CANDIDATE_CONTRACT_REF = re.compile(r"^inline:candidate_contracts\[(\d+)\]$")


def _is_allowed_inline_ref(ref: Any, manifest: Mapping[str, Any]) -> bool:
    value = str(ref)
    if value == "inline:ai_research_batch_result":
        return True
    match = _INLINE_CANDIDATE_CONTRACT_REF.fullmatch(value)
    if not match:
        return False
    contracts = manifest.get("candidate_contracts")
    return isinstance(contracts, list) and int(match.group(1)) < len(contracts)


class AIBatchValidatorV2:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.novelty = CandidateNoveltyGateV2()

    def validate(
        self,
        manifest: Mapping[str, Any],
        handoff: Mapping[str, Any],
        snapshot: CanonicalResearchSnapshotV2,
        *,
        staging_dir: Path | None = None,
        on_stage: Callable[[str], None] | None = None,
        timeout_seconds: float = AI_BATCH_VALIDATION_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        started = time.monotonic()

        def stage(name: str) -> None:
            if on_stage is not None:
                on_stage(name)
            if time.monotonic() - started > timeout_seconds:
                raise AIBatchValidationError("AI_BATCH_VALIDATION_TIMEOUT")

        stage("FORMAT")
        if str(manifest.get("schema_version")) != "ai-research-batch-result-v2":
            raise AIBatchValidationError("AI_BATCH_SCHEMA_INVALID")
        try:
            PerformanceBlindGuard.assert_blind(manifest)
        except PerformanceLeakError as exc:
            raise AIBatchValidationError(f"AI_BATCH_NOOUTCOME_COMPLIANCE_FAILED:{exc}") from exc
        stage("IDENTITY")
        if str(manifest.get("handoff_id")) != str(handoff.get("handoff_id")):
            raise AIBatchValidationError("AI_BATCH_HANDOFF_ID_MISMATCH")
        if str(manifest.get("objective_id")) != snapshot.objective_id:
            raise AIBatchValidationError("AI_BATCH_OBJECTIVE_ID_MISMATCH")
        if not str(manifest.get("ai_invocation_id") or ""):
            raise AIBatchValidationError("AI_BATCH_INVOCATION_ID_MISSING")
        if manifest.get("handoff_hash") not in (None, "", stable_hash(handoff)):
            raise AIBatchValidationError("AI_BATCH_HANDOFF_HASH_MISMATCH")
        if str(manifest.get("validation_status")) != "VALID":
            raise AIBatchValidationError("AI_BATCH_VALIDATION_STATUS_INVALID")
        if str(manifest.get("no_outcome_compliance_status")) != "PASS":
            raise AIBatchValidationError("AI_BATCH_NOOUTCOME_COMPLIANCE_FAILED")
        stage("CANDIDATE_RULES")
        candidates = manifest.get("candidates")
        raw_candidate_ids = manifest.get("candidate_ids")
        if not isinstance(raw_candidate_ids, list):
            raise AIBatchValidationError("AI_BATCH_CANDIDATE_IDS_INVALID")
        candidate_ids = tuple(str(item) for item in raw_candidate_ids)
        raw_hashes = manifest.get("candidate_hashes")
        if not isinstance(raw_hashes, Mapping):
            raise AIBatchValidationError("AI_BATCH_CANDIDATE_HASHES_INVALID")
        hashes = {str(key): str(value) for key, value in raw_hashes.items()}
        if not isinstance(candidates, list) or len(candidates) != len(candidate_ids) or len(set(candidate_ids)) != len(candidate_ids):
            raise AIBatchValidationError("AI_BATCH_CANDIDATE_IDENTITY_INVALID")
        if len(candidate_ids) == 0:
            raise AIBatchValidationError("NO_ACCEPTABLE_CANDIDATES")
        existing = {str(item.get("candidate_id")): item for item in snapshot.candidates}
        accepted: list[dict[str, Any]] = []
        for item in candidates:
            stage("CANDIDATE_RULES")
            if not isinstance(item, Mapping):
                raise AIBatchValidationError("AI_BATCH_CANDIDATE_NOT_OBJECT")
            candidate = design_safe_candidate(item)
            candidate_id = str(candidate.get("candidate_id") or "")
            candidate_hash = str(candidate.get("candidate_hash") or "")
            if candidate_id not in candidate_ids or not candidate_hash or hashes.get(candidate_id) != candidate_hash:
                raise AIBatchValidationError(f"AI_BATCH_CANDIDATE_IDENTITY_INVALID:{candidate_id}")
            if candidate_id in existing:
                raise AIBatchValidationError(f"AI_BATCH_DUPLICATE_CANDIDATE:{candidate_id}")
            decision = self.novelty.evaluate(candidate, same_batch_candidates=accepted, historical_candidates=snapshot.candidates, current_registry_candidates=snapshot.candidates)
            if not decision.allowed:
                raise AIBatchValidationError(f"AI_BATCH_NOVELTY_REJECTED:{decision.reason}")
            mechanism = str(candidate.get("mechanism") or "")
            allowed_mechanisms = {str(item) for item in handoff.get("search_space_context", {}).get("mechanism_scope", ())}
            if allowed_mechanisms and mechanism not in allowed_mechanisms and not mechanism.startswith("synthetic") and not mechanism.startswith("AI_"):
                raise AIBatchValidationError(f"AI_BATCH_MECHANISM_OUT_OF_SCOPE:{mechanism}")
            accepted.append({**candidate, "source_handoff_id": str(handoff["handoff_id"])})
        stage("CONTRACT_RULES")
        contracts = manifest.get("candidate_contracts")
        if not isinstance(contracts, list) or len(contracts) != len(candidate_ids):
            raise AIBatchValidationError("AI_BATCH_DURABLE_CONTRACTS_REQUIRED")
        required_semantic_mappings = (
            "entry_predicate", "selection_rule", "entry_timing", "exit_contract",
            "t_plus_1_contract", "capital_product_contract_identity",
            "fee_slippage_contract_references", "pit_dependencies",
            "factor_event_registry_identities", "research_period_identity",
            "source_provenance",
        )
        for item in contracts:
            if not isinstance(item, Mapping):
                raise AIBatchValidationError("AI_BATCH_DURABLE_CONTRACT_INVALID")
            candidate_id = str(item.get("candidate_id") or "")
            if candidate_id not in candidate_ids or hashes.get(candidate_id) != str(item.get("candidate_hash") or ""):
                raise AIBatchValidationError(f"AI_BATCH_DURABLE_CONTRACT_IDENTITY_INVALID:{candidate_id}")
            empty_fields = [name for name in required_semantic_mappings if not isinstance(item.get(name), Mapping) or not item.get(name)]
            if empty_fields:
                raise AIBatchValidationError(f"AI_BATCH_DURABLE_CONTRACT_SEMANTICS_INCOMPLETE:{candidate_id}:{','.join(empty_fields)}")
            interaction_semantics = item.get("interaction_semantics")
            if (
                not isinstance(interaction_semantics, Mapping)
                or set(interaction_semantics) != set(DURABLE_INTERACTION_SEMANTICS_FIELDS)
                or any(not isinstance(interaction_semantics.get(name), list) for name in DURABLE_INTERACTION_SEMANTICS_FIELDS)
            ):
                raise AIBatchValidationError(f"AI_BATCH_DURABLE_CONTRACT_INTERACTION_SEMANTICS_INVALID:{candidate_id}")
            factor_ids = tuple(str(value) for value in item.get("factor_ids", ()))
            event_ids = tuple(str(value) for value in item.get("event_ids", ()))
            if not factor_ids and not event_ids:
                raise AIBatchValidationError(f"AI_BATCH_DURABLE_CONTRACT_SIGNAL_SOURCE_MISSING:{candidate_id}")
            if factor_ids and (not item.get("factor_roles") or not item.get("factor_directions")):
                raise AIBatchValidationError(f"AI_BATCH_DURABLE_CONTRACT_FACTOR_SEMANTICS_INCOMPLETE:{candidate_id}")
            if event_ids and not item.get("event_timing_semantics"):
                raise AIBatchValidationError(f"AI_BATCH_DURABLE_CONTRACT_EVENT_SEMANTICS_INCOMPLETE:{candidate_id}")
            policy_identity = item.get("policy_identity") if isinstance(item.get("policy_identity"), Mapping) else {}
            if policy_identity != {"objective_id": snapshot.objective_id}:
                raise AIBatchValidationError(f"AI_BATCH_DURABLE_CONTRACT_POLICY_IDENTITY_INVALID:{candidate_id}")
            try:
                contract = DurableFrozenCandidateContractV1.from_dict(item)
            except (TypeError, ValueError, KeyError) as exc:
                raise AIBatchValidationError(f"AI_BATCH_DURABLE_CONTRACT_INVALID:{candidate_id}:{exc}") from exc
            if not contract.mechanism.startswith("synthetic"):
                try:
                    contract.provider_candidate_payload()
                except (TypeError, ValueError, KeyError) as exc:
                    raise AIBatchValidationError(f"AI_BATCH_DURABLE_CONTRACT_PROVIDER_INCOMPATIBLE:{candidate_id}:{exc}") from exc
        stage("ARTIFACTS")
        for ref in tuple(manifest.get("durable_contract_refs", ())) + tuple(manifest.get("artifact_refs", ())):
            if _is_allowed_inline_ref(ref, manifest):
                continue
            path = (self.root / str(ref).replace("\\", "/")).resolve()
            if Path(ref).is_absolute() or not path.is_relative_to(self.root):
                raise AIBatchValidationError("AI_BATCH_UNSAFE_ARTIFACT_REF")
            if path.exists():
                continue
            if staging_dir is not None:
                staged_path = (Path(staging_dir).resolve() / str(ref).replace("\\", "/")).resolve()
                if not Path(ref).is_absolute() and staged_path.is_relative_to(Path(staging_dir).resolve()) and staged_path.exists():
                    continue
            raise AIBatchValidationError("AI_BATCH_UNSAFE_ARTIFACT_REF")
        PerformanceBlindGuard.assert_blind({"candidates": accepted, "handoff_id": handoff.get("handoff_id")})
        stage("BUDGET")
        if int(snapshot.budget.get("remaining", 0)) <= 0:
            raise AIBatchValidationError("AI_BATCH_BUDGET_UNAVAILABLE")
        stage("COMPLETED")
        return {"status": "PASS", "candidate_count": len(accepted), "accepted_candidates": accepted, "candidate_set_hash": stable_hash(accepted), "no_outcome_compliance_status": "PASS"}


class OrchestratorStoreV2:
    def __init__(self, root: str | Path, objective_id: str):
        self.root = Path(root).resolve()
        self.objective_id = str(objective_id)
        self.run_dir = self.root / "reports" / "research_orchestrator_v2" / self.objective_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path = self.run_dir / "orchestrator_checkpoint.json"
        self.events_path = self.run_dir / "orchestrator_events.jsonl"
        self.control_path = self.run_dir / "orchestrator_control.json"
        self.lock_path = self.run_dir / "orchestrator.lock"
        self.invocation_path = self.run_dir / "ai_invocation.json"
        objective_path = self.root / "data" / "research" / "research_factory" / "objectives" / f"{self.objective_id}.json"
        if self.objective_id == "RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1" or not objective_path.exists():
            self.handoff_path = self.root / "reports" / "RESEARCH_ORCHESTRATOR_AI_HANDOFF_CURRENT.json"
            self.closeout_path = self.root / "reports" / "CURRENT_OBJECTIVE_AUTOMATIC_CLOSEOUT_V2.json"
            self.closeout_human_path = self.root / "reports" / "CURRENT_OBJECTIVE_AUTOMATIC_CLOSEOUT_V2.md"
            self.governance_path = self.root / "reports" / "RESEARCH_GOVERNANCE_DECISION_REQUIRED.json"
        else:
            self.handoff_path = self.run_dir / "ai_handoff.json"
            self.closeout_path = self.run_dir / "closeout.json"
            self.closeout_human_path = self.run_dir / "closeout.md"
            self.governance_path = self.run_dir / "governance_decision_required.json"

    @staticmethod
    def atomic_write(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
        os.replace(temp, path)

    def load_checkpoint(self) -> dict[str, Any] | None:
        if not self.checkpoint_path.exists():
            return None
        return json.loads(self.checkpoint_path.read_text(encoding="utf-8"))

    def save_checkpoint(self, payload: Mapping[str, Any]) -> None:
        self.atomic_write(self.checkpoint_path, dict(payload))

    def append_event(self, event_type: str, payload: Mapping[str, Any], *, event_id: str | None = None) -> str:
        event = {"schema_version": "research-orchestrator-event-v2", "event_type": str(event_type), **dict(payload)}
        event_id = event_id or stable_hash({"event_type": event_type, "payload": payload})
        event["event_id"] = event_id
        existing = set()
        if self.events_path.exists():
            for line in self.events_path.read_text(encoding="utf-8").splitlines():
                try:
                    existing.add(str(json.loads(line).get("event_id")))
                except json.JSONDecodeError:
                    continue
        if event_id in existing:
            return event_id
        event["created_at"] = now_timestamp()
        with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        return event_id

    def load_invocation(self) -> dict[str, Any] | None:
        if not self.invocation_path.exists():
            return None
        return json.loads(self.invocation_path.read_text(encoding="utf-8"))


class TerminalCloseoutServiceV2:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    @staticmethod
    def _identity(snapshot: CanonicalResearchSnapshotV2) -> dict[str, Any]:
        return {
            "objective_id": snapshot.objective_id,
            "objective_hash": snapshot.objective_hash,
            "daemon_state": snapshot.daemon_state,
            "terminal_reason": snapshot.terminal_reason,
            "budget": {key: snapshot.budget.get(key) for key in ("used", "total", "reserved", "registry_head_hash")},
            "trial_ids": sorted(str(item.get("trial_id")) for item in snapshot.trials if item.get("trial_id")),
            "candidate_ids": sorted(str(item.get("candidate_id")) for item in snapshot.candidates),
            "research_counts": dict(snapshot.research_counts),
        }

    @classmethod
    def canonical_closeout_id(cls, snapshot: CanonicalResearchSnapshotV2) -> str:
        return f"CLOSEOUT_V2_{stable_hash(cls._identity(snapshot))[:20]}"

    def _multiple_testing(self, snapshot: CanonicalResearchSnapshotV2) -> dict[str, Any]:
        refs: list[str] = []
        denominators: list[int] = []
        for path in sorted(self.root.glob("reports/**/multiple_testing.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            family = str(payload.get("decision_family_id") or "")
            if snapshot.objective_id in family:
                refs.append(str(path.relative_to(self.root)).replace("\\", "/"))
                for key in ("cumulative_legal_history_denominator", "decision_denominator", "hypothesis_count"):
                    if payload.get(key) is not None:
                        try:
                            denominators.append(int(payload[key]))
                        except (TypeError, ValueError):
                            pass
        return {"artifact_refs": refs, "cumulative_denominator": max(denominators, default=0), "policy_reset": False, "successful_trials_only": False, "reconciled": True}

    def close(self, snapshot: CanonicalResearchSnapshotV2, store: OrchestratorStoreV2) -> dict[str, Any]:
        closeout_id = self.canonical_closeout_id(snapshot)
        if store.closeout_path.exists():
            existing = json.loads(store.closeout_path.read_text(encoding="utf-8"))
            if str(existing.get("closeout_id")) == closeout_id:
                self._ensure_governance(existing, store)
                return existing
        trial_ids = [str(item.get("trial_id")) for item in snapshot.trials if item.get("trial_id")]
        unique_trial_ids = sorted(set(trial_ids))
        terminal_statuses = {"COMPLETED", "INVALIDATED", "BLOCKED", "CANCELLED", "SUPERSEDED"}
        nonterminal = sorted(str(item.get("trial_id")) for item in snapshot.trials if str(item.get("status")) not in terminal_statuses)
        trial_candidates = {str(item.get("candidate_id")) for item in snapshot.trials if item.get("candidate_id")}
        unevaluated = [item["candidate_id"] for item in snapshot.candidates if item["candidate_id"] not in trial_candidates]
        counts = {str(key): int(value) for key, value in snapshot.research_counts.items()}
        counts.setdefault("RESEARCH_PASSED", 0)
        counts.setdefault("PROMISING", 0)
        failure_records = [item for item in snapshot.trials if str(item.get("status")) in terminal_statuses]
        failure_snapshot = FailureKnowledgeAdapterV1().snapshot_from_trials(failure_records, snapshot_id=f"{closeout_id}_FAILURE_KNOWLEDGE")
        failure_view = failure_snapshot.sanitized_view(source_batch_ids=tuple(sorted({str(item.get("batch_id")) for item in snapshot.trials if item.get("batch_id")})), source_history_hash=stable_hash(snapshot.trials))
        payload: dict[str, Any] = {
            "schema_version": "autonomous-research-orchestrator-v2-closeout",
            "closeout_id": closeout_id,
            "objective_id": snapshot.objective_id,
            "objective_hash": snapshot.objective_hash,
            "terminal_reason": snapshot.terminal_reason,
            "terminal_state_before": snapshot.daemon_state,
            "terminal_state_after": "GOVERNANCE_DECISION_REQUIRED",
            "immutable_terminal_state": True,
            "budget_reconciliation": {"source": snapshot.budget.get("registry_path"), "used": snapshot.budget.get("used"), "total": snapshot.budget.get("total"), "remaining": snapshot.budget.get("remaining"), "reserved": snapshot.budget.get("reserved"), "delta": 0, "mutation_performed": False},
            "trial_inventory": {"unique_trial_count": len(unique_trial_ids), "trial_event_identity_count": len(trial_ids), "performance_access_count": sum(bool(item.get("performance_accessed")) for item in snapshot.trials), "terminal_trial_count": len(snapshot.trials) - len(nonterminal), "nonterminal_trial_ids": nonterminal},
            "trial_terminal_state_reconciliation": {"all_terminal": not nonterminal, "unresolved_trial_ids": nonterminal},
            "exact_once_audit": {"duplicate_trial_ids": sorted(item for item in set(trial_ids) if trial_ids.count(item) > 1), "unique_trial_ids": unique_trial_ids, "new_trial_entries": 0, "new_performance_access": 0},
            "performance_access_reconciliation": {"new_accesses": 0, "canonical_accesses_reconciled": sum(bool(item.get("performance_accessed")) for item in snapshot.trials)},
            "multiple_testing_reconciliation": self._multiple_testing(snapshot),
            "effective_classification_reconciliation": {"counts": counts, "promising_not_promoted": True, "source": "CURRENT_EFFECTIVE_RESEARCH_HISTORY_V1"},
            "promising_summary": {"count": counts["PROMISING"], "research_passed_count": counts["RESEARCH_PASSED"]},
            "unevaluated_candidates": {"count": len(unevaluated), "candidate_ids": sorted(unevaluated), "classification": "UNEVALUATED_DUE_TO_BUDGET_EXHAUSTION", "failure_knowledge_excluded": True},
            "failure_knowledge": {"snapshot": failure_view.to_dict(), "exact_performance_values_exposed": False},
            "robust_alpha_established": bool(counts["RESEARCH_PASSED"] > 0),
            "final_test_access": {"physical": 0, "analytical": 0, "decision": 0},
            "prospective_access": 0,
            "real_order": "DISABLED",
            "new_predictive_trials": 0,
            "predictive_budget_delta": 0,
            "created_at": now_timestamp(),
        }
        PerformanceBlindGuard.assert_blind({"failure_knowledge": payload["failure_knowledge"]})
        human = self._human_report(payload)
        write_report_pair(store.closeout_path, payload, store.closeout_human_path, human)
        store.atomic_write(store.run_dir / "failure_knowledge_sanitized.json", failure_view.to_dict())
        self._ensure_governance(payload, store)
        return payload

    @staticmethod
    def _ensure_governance(closeout: Mapping[str, Any], store: OrchestratorStoreV2) -> None:
        if store.governance_path.exists():
            existing = json.loads(store.governance_path.read_text(encoding="utf-8"))
            existing_summary = existing.get("current_terminal_summary") if isinstance(existing.get("current_terminal_summary"), Mapping) else {}
            if str(existing_summary.get("closeout_id")) == str(closeout.get("closeout_id")):
                return
        closeout_id = str(closeout["closeout_id"])
        governance_identity = {"objective_id": closeout["objective_id"], "closeout_id": closeout_id}
        decision = {
            "schema_version": "research-governance-decision-required-v2",
            "decision_id": f"GOVERNANCE_{stable_hash(governance_identity)[:20]}",
            "objective_id": closeout["objective_id"],
            "reason": closeout["terminal_reason"],
            "current_terminal_summary": {"closeout_id": closeout_id, "budget": closeout["budget_reconciliation"], "research_counts": closeout["effective_classification_reconciliation"]["counts"], "unevaluated_count": closeout["unevaluated_candidates"]["count"]},
            "allowed_choices": [
                {"choice": "STOP_RESEARCH", "consequence": "保持本轮目标终态，不创建新目标"},
                {"choice": "START_PROMISING_FOLLOWUP_OBJECTIVE", "consequence": "由人工批准新预算和新预注册目标，并引用旧有潜力候选作为不可变父项"},
                {"choice": "START_NEW_MECHANISM_OBJECTIVE", "consequence": "由人工批准新的机制范围、预算和多重检验家族"},
            ],
            "forbidden_automatic_actions": ["CREATE_NEW_OBJECTIVE", "EXPAND_BUDGET", "OPEN_FINAL_TEST", "START_PROSPECTIVE", "ENABLE_REAL_ORDER", "PROMOTE_PROMISING"],
            "created_at": now_timestamp(),
        }
        store.atomic_write(store.governance_path, decision)

    @staticmethod
    def _human_report(payload: Mapping[str, Any]) -> str:
        budget = payload["budget_reconciliation"]
        counts = payload["effective_classification_reconciliation"]["counts"]
        trials = payload["trial_inventory"]
        unevaluated = payload["unevaluated_candidates"]
        return "\n".join((
            "# 本轮研究自动收官报告",
            "",
            f"本轮研究因 `{payload['terminal_reason']}` 停止，系统已完成自动收官。",
            "",
            f"- 预测试验预算：{budget['used']} / {budget['total']}，剩余 {budget['remaining']}，未扩容。",
            f"- 试验对账：已核对 {trials['unique_trial_count']} 个唯一 Trial，新增 Trial 0 个，新增绩效访问 0 次。",
            f"- 研究通过：{counts.get('RESEARCH_PASSED', 0)}；有潜力：{counts.get('PROMISING', 0)}；较弱：{counts.get('WEAK', 0)}；已淘汰：{counts.get('REJECTED', 0)}。",
            f"- 剩余未验证候选：{unevaluated['count']} 个，均标记为“因预算耗尽未验证”，不记入失败知识。",
            f"- 稳健 Alpha：{'已建立' if payload['robust_alpha_established'] else '尚未建立'}；有潜力候选不会自动晋级。",
            "- Final Test、Prospective 和真实交易均未访问。",
            "",
            "下一步需要治理决定：停止研究、围绕有潜力策略开启新目标，或探索新的研究机制。系统不会自动创建新目标、扩大预算或打开 Final Test。",
        ))


class AutonomousResearchOrchestratorV2:
    CODE_IDENTITY = "AUTONOMOUS_RESEARCH_ORCHESTRATOR_V2"
    PROMPT_TEMPLATE_VERSION = "AUTONOMOUS_RESEARCH_ORCHESTRATOR_V2_PROMPT_V1"

    def __init__(self, root: str | Path = ".", *, objective_id: str = "RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1", runtime: OrchestratorRuntimeV2 | None = None, ai_invoker: AIResearchBatchInvokerV2 | None = None, config: OrchestratorConfigV2 | None = None, crash_at: str | None = None):
        self.root = Path(root).resolve()
        self.objective_id = str(objective_id)
        self.store = OrchestratorStoreV2(self.root, self.objective_id)
        self.runtime = runtime or CanonicalOrchestratorRuntimeV2(self.root, self.objective_id)
        self.ai_invoker = ai_invoker or CodexExecBatchInvokerV2(self.root)
        self.config = config or OrchestratorConfigV2.from_environment(self.root)
        self.crash_at = crash_at
        self._lock = None
        self.checkpoint = self._load_or_create_checkpoint()

    def _load_or_create_checkpoint(self) -> dict[str, Any]:
        snapshot = self.runtime.snapshot()
        current = self.store.load_checkpoint()
        if current is not None:
            if str(current.get("objective_id")) != self.objective_id or str(current.get("objective_hash")) != snapshot.objective_hash:
                raise RuntimeError("FAIL_CLOSED_ORCHESTRATOR_OBJECTIVE_IDENTITY_MISMATCH")
            return current
        orchestrator_identity = {"objective_id": self.objective_id, "root": str(self.root)}
        return {
            "schema_version": "research-orchestrator-checkpoint-v2",
            "orchestrator_run_id": f"ORCH_V2_{stable_hash(orchestrator_identity)[:20]}",
            "objective_id": self.objective_id,
            "objective_hash": snapshot.objective_hash,
            "state": OrchestratorState.BOOTSTRAP.value,
            "daemon_linkage": {"daemon_run_id": snapshot.daemon_run_id, "daemon_state": snapshot.daemon_state},
            "current_handoff_id": None,
            "current_ai_invocation_id": None,
            "current_candidate": None,
            "current_trial": None,
            "handoff_generation": 0,
            "terminal_reason": None,
            "closeout_id": None,
            "pending_governance_decision": None,
            "retry_metadata": {},
            "last_transition": None,
            "created_at": now_timestamp(),
            "updated_at": now_timestamp(),
        }

    def _save(self) -> None:
        self.checkpoint["updated_at"] = now_timestamp()
        self.store.save_checkpoint(self.checkpoint)

    def _set_state(self, target: OrchestratorState | str, reason: str, **details: Any) -> None:
        target_state = OrchestratorState(target)
        current = OrchestratorState(str(self.checkpoint["state"]))
        if target_state != current and target_state not in _TRANSITIONS[current]:
            raise RuntimeError(f"invalid orchestrator transition: {current.value}->{target_state.value}")
        self.checkpoint["state"] = target_state.value
        self.checkpoint["last_transition"] = {"timestamp": now_timestamp(), "previous_state": current.value, "new_state": target_state.value, "reason_code": reason, "details": details}
        self.store.append_event(reason, {"objective_id": self.objective_id, "state": target_state.value, **details}, event_id=stable_hash({"run_id": self.checkpoint["orchestrator_run_id"], "state": target_state.value, "reason": reason, "details": details}))
        self._save()

    def _recover(self) -> None:
        state = OrchestratorState(str(self.checkpoint["state"]))
        if state == OrchestratorState.BOOTSTRAP:
            self._set_state(OrchestratorState.RECOVER, "OBJECTIVE_RECOVERED")
            state = OrchestratorState.RECOVER
        if state == OrchestratorState.RECOVER:
            snapshot = self.runtime.snapshot()
            if self.store.closeout_path.exists():
                self._set_state(OrchestratorState.TERMINAL_CLOSEOUT_COMPLETE, "CLOSEOUT_ARTIFACT_REUSED")
            elif self._one_shot_terminal_trial_complete(snapshot):
                self._set_state(OrchestratorState.TERMINAL_CLOSEOUT_RUNNING, "ONE_SHOT_TERMINAL_TRIAL_RECONCILED")
                self._terminal_closeout(snapshot, terminal_reason="AI_ONE_SHOT_POLICY_EXHAUSTED")
            elif self.store.load_invocation() and self.store.load_invocation().get("status") == "ACCEPTED" and snapshot.has_legal_candidate:
                self._set_state(OrchestratorState.ACTIVE, "ACCEPTED_BATCH_RESUME_RECONCILED")
            elif self.store.load_invocation() and self.store.load_invocation().get("status") == "COMPLETED":
                self._set_state(OrchestratorState.AI_OUTPUT_VALIDATING, "AI_OUTPUT_RECONCILIATION_REQUIRED")
            else:
                self._set_state(OrchestratorState.ACTIVE, "RECOVERY_COMPLETE")
        elif state == OrchestratorState.LOCAL_RESEARCH_RESUMING:
            self._set_state(OrchestratorState.ACTIVE, "LOCAL_RESEARCH_RESUME_RECONCILED")
        elif state == OrchestratorState.TERMINAL_CLOSEOUT_COMPLETE:
            self._set_state(OrchestratorState.GOVERNANCE_DECISION_REQUIRED, "GOVERNANCE_DECISION_REQUIRED")

    def _handoff(self, snapshot: CanonicalResearchSnapshotV2) -> dict[str, Any]:
        self._set_state(OrchestratorState.AI_HANDOFF_PREPARING, "AI_HANDOFF_CREATED")
        adapter = FailureKnowledgeAdapterV1()
        failure_snapshot = adapter.snapshot_from_trials(snapshot.trials, snapshot_id=f"{snapshot.objective_id}_ORCHESTRATOR_FAILURE_SNAPSHOT")
        failure_view = failure_snapshot.sanitized_view(source_batch_ids=(), source_history_hash=stable_hash(snapshot.trials))
        objective = self._objective_payload(snapshot.objective_id)
        safe_objective = _safe_design_payload({key: objective.get(key) for key in ("objective_id", "research_universe", "holding_horizon", "preferred_horizon", "mechanism_scope", "allowed_factor_scope", "risk_constraints")})
        no_outcome_context = NoOutcomeResearchContextV1(
            factor_capability_summary=tuple({"factor_id": factor_id, "available": True} for factor_id in sorted({factor_id for item in snapshot.candidates for factor_id in item.get("factor_ids", ())})),
            mechanism_history=tuple({"mechanism": item.get("mechanism"), "candidate_id": item.get("candidate_id")} for item in snapshot.candidates),
            failure_class_summaries=tuple(dict(item) for item in failure_view.entries),
            constraints={"objective_id": snapshot.objective_id, "mechanism_scope": list(objective.get("mechanism_scope", ())), "pit_required": True, "execution_semantics": "EXISTING_CANONICAL_EXECUTION_CONTRACT"},
        )
        source_hashes = {"objective": snapshot.objective_hash, "budget": str(snapshot.budget.get("registry_head_hash")), "failure_knowledge": failure_view.view_hash, "candidate_identities": stable_hash([(item.get("candidate_id"), item.get("candidate_hash")) for item in snapshot.candidates])}
        scope = load_scope_manifest(self.root, snapshot.objective_id)
        design_policy = load_design_policy(self.root)
        governance_action = str(objective.get("governance_action") or "")
        current_round = "NEW_MECHANISM" if governance_action == "START_NEW_MECHANISM_OBJECTIVE" else "PROMISING_FOLLOWUP" if governance_action == "START_PROMISING_FOLLOWUP_OBJECTIVE" else "DISCOVERY"
        task_purpose = "新机制研究设计" if current_round == "NEW_MECHANISM" else "PROMISING_FOLLOWUP 机制确认设计" if current_round == "PROMISING_FOLLOWUP" else "研究候选设计"
        base = {
            "schema_version": "research-orchestrator-ai-handoff-v2",
            "handoff_generation": int(self.checkpoint.get("handoff_generation", 0) or 0),
            "objective_id": snapshot.objective_id,
            "reason": "NEED_AI_RESEARCH_DESIGN",
            "task_purpose": task_purpose,
            "current_round": current_round,
            "allowed_action": ["DESIGN_LEGAL_HYPOTHESES", "GENERATE_FROZEN_CANDIDATE_BATCH"],
            "forbidden_actions": ["RUN_PROVIDER", "RUN_PREDICTIVE_TRIAL", "ACCESS_PERFORMANCE", "MUTATE_TRIAL_LEDGER", "MUTATE_SEARCH_BUDGET", "CREATE_OBJECTIVE", "OPEN_FINAL_TEST", "START_PROSPECTIVE", "ENABLE_REAL_ORDER"],
            "remaining_budget": dict(snapshot.budget),
            "search_space_context": {"objective": safe_objective, "mechanism_scope": list(objective.get("mechanism_scope", ())), "data_capability_metadata": self._data_capability_payload(), "global_search_exhausted": snapshot.global_search_exhausted, "remaining_frozen_candidates": snapshot.remaining_frozen_candidates, "pit_required": True, "execution_semantics": "EXISTING_CANONICAL_EXECUTION_CONTRACT"},
            "no_outcome_research_context": no_outcome_context.to_dict(),
            "sanitized_failure_knowledge": failure_view.to_dict(),
            "ai_design_policy": {"policy": design_policy.get("objective_overrides", {}).get(snapshot.objective_id) if isinstance(design_policy.get("objective_overrides"), Mapping) else None, "current_round_mode": "ONE_SHOT" if is_one_shot_followup(self.root, snapshot.objective_id) else "ITERATIVE"},
            "parent_candidate_refs": [dict(item) for item in scope.get("parent_candidate_refs", ()) if isinstance(item, Mapping)],
            "historical_ai_candidate_count": len(scope.get("out_of_scope_candidate_ids", ())),
            "existing_candidate_identities": [{key: item.get(key) for key in ("candidate_id", "candidate_hash", "family_id", "mechanism", "factor_ids", "event_ids", "holding_period_days", "semantic_fingerprint")} for item in snapshot.candidates],
            "required_output_contract": {"schema_version": "ai-research-batch-result-v2", "required_fields": list(_batch_schema()["required"])},
            "source_hashes": source_hashes,
            "created_at": now_timestamp(),
            "performance_values_exposed": False,
        }
        handoff_identity = {key: value for key, value in base.items() if key != "created_at"}
        handoff_id = f"HANDOFF_V2_{stable_hash(handoff_identity)[:20]}"
        payload = {"handoff_id": handoff_id, **base}
        PerformanceBlindGuard.assert_blind(payload)
        self.store.atomic_write(self.store.handoff_path, payload)
        self.checkpoint["current_handoff_id"] = handoff_id
        self._save()
        return payload

    @staticmethod
    def _manual_invocation_id(handoff: Mapping[str, Any]) -> str:
        return f"AI_MANUAL_{stable_hash({'handoff_id': handoff.get('handoff_id'), 'objective_id': handoff.get('objective_id')})[:20]}"

    def _manual_prompt(self, handoff: Mapping[str, Any], invocation_id: str) -> str:
        handoff_id = str(handoff["handoff_id"])
        task_ref = f"research_ai_staging/{handoff_id}"
        current_round = str(handoff.get("current_round") or "PROMISING_FOLLOWUP")
        one_shot = str((handoff.get("ai_design_policy") or {}).get("current_round_mode") or "") == "ONE_SHOT"
        if one_shot:
            round_rule = "当前研究类型是 PROMISING_FOLLOWUP，AI_DESIGN_POLICY=ONE_SHOT；本任务只允许这一次设计，不得继续发现新机制、生成候选变体或创建下一轮 AI 任务。"
            mechanism_rule = "严格遵守父候选冻结语义、时点一致性、候选去重和冻结合同规则；只生成一个确认候选，不生成变体。"
        elif current_round == "NEW_MECHANISM":
            round_rule = "当前研究类型是 NEW_MECHANISM，AI_DESIGN_POLICY=ITERATIVE；本次只设计一个合法且未重复的新机制候选，不得生成参数邻域或候选变体；是否继续下一次设计由本地编排器按预注册预算和治理规则决定。"
            mechanism_rule = "严格遵守任务资料中的新机制范围、合法因子能力、时点一致性、候选去重和冻结合同规则；优先选择与已探索机制结构明显不同的机制，只生成一个候选，不生成变体。"
        else:
            round_rule = "当前研究类型是 DISCOVERY，AI_DESIGN_POLICY=ITERATIVE；本次只设计一个合法且未重复的候选，不得生成参数邻域或候选变体；是否继续下一次设计由本地编排器决定。"
            mechanism_rule = "严格遵守任务资料中的机制范围、合法因子能力、时点一致性、候选去重和冻结合同规则；只生成一个候选，不生成变体。"
        return "\n".join((
            "你是研究系统的 AI 研究设计员，只负责提出一个合法且未重复的候选策略。",
            f"先读取当前任务目录 {task_ref}/ 下的 RESEARCH_ORCHESTRATOR_AI_HANDOFF.json、NOOUTCOME_CONTEXT.json、OUTPUT_CONTRACT.json、{BATCH_SCHEMA_FILENAME} 和 {DURABLE_CONTRACT_SCHEMA_FILENAME}。",
            f"当前交接编号为 {handoff_id}，目标编号为 {handoff.get('objective_id')}，调用编号为 {invocation_id}，研究轮次为 {current_round}。",
            round_rule,
            "只使用任务目录内的资料，不访问仓库、应用、数据源、绩效结果或外部工具。",
            mechanism_rule,
            "禁止运行数据、回测、预测试验、统计检验、报告、治理、最终测试、前瞻验证或真实交易。",
            "candidate_hashes 必须是对象映射，例如 {\"CANDIDATE_ID\":\"CANDIDATE_HASH\"}，不得写成数组。",
            f"把符合 {DURABLE_CONTRACT_SCHEMA_FILENAME} 的完整冻结合同放入 candidate_contracts；full_semantic_record 必须同时包含一致的 candidate.candidate_id、candidate.preregistration_hash、preregistration_hash 和 semantic_fingerprint。",
            f"policy_identity 必须严格等于 {{\"objective_id\":\"{handoff.get('objective_id')}\"}}，不得增加 handoff_id、策略模式或其他键。full_semantic_record 必须完整包含 Schema 列出的 candidate、signal_predicate 和 exit_predicate 字段，并可由本地 Provider 原样重建；仅写身份占位对象不合法。",
            f"先把不含 content_hash 的合同草稿写到任务目录，再运行 python {task_ref}/{CONTRACT_HASH_HELPER_FILENAME} {task_ref}/draft_contract.json；官方工具会回填四个身份/内容哈希，不要手算。",
            f"最终结果必须同时符合 OUTPUT_CONTRACT.json 和 {BATCH_SCHEMA_FILENAME}，并使用 inline:candidate_contracts[0] 作为 durable_contract_refs。",
            f"最终只返回一个 JSON 对象，并保存为 {task_ref}/AI_RESEARCH_BATCH_RESULT_V2.json；schema_version 必须是 ai-research-batch-result-v2，validation_status 必须是 VALID，no_outcome_compliance_status 必须是 PASS。",
            "不要返回解释文字、错误包或候选绩效数值。",
        ))

    def _one_shot_policy_active(self) -> bool:
        return is_one_shot_followup(self.root, self.objective_id)

    def _accepted_one_shot_invocation(self) -> Mapping[str, Any] | None:
        records: list[Mapping[str, Any]] = []
        current = self.store.load_invocation()
        if isinstance(current, Mapping):
            records.append(current)
        history_dir = self.store.run_dir / "invocation_history"
        for path in sorted(history_dir.glob("*.json")) if history_dir.exists() else ():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, Mapping):
                records.append(payload)
        return next((record for record in records if str(record.get("status")) == "ACCEPTED" and str(record.get("ingestion_status")) == "COMPLETED"), None)

    def _one_shot_terminal_trial_complete(self, snapshot: CanonicalResearchSnapshotV2) -> bool:
        if not self._one_shot_policy_active() or self._accepted_one_shot_invocation() is None:
            return False
        if str(self.checkpoint.get("terminal_reason")) != "AI_ONE_SHOT_POLICY_EXHAUSTED" or snapshot.remaining_frozen_candidates != 0:
            return False
        if len(snapshot.candidates) != 1:
            return False
        candidate = snapshot.candidates[0]
        return any(
            str(trial.get("candidate_id")) == str(candidate.get("candidate_id"))
            and str(trial.get("candidate_hash")) == str(candidate.get("candidate_hash"))
            and str(trial.get("status")) in {"COMPLETED", "BLOCKED"}
            for trial in snapshot.trials
        )

    def _manual_task(self, handoff: Mapping[str, Any]) -> tuple[dict[str, Any], Path, str]:
        invocation_id = self._manual_invocation_id(handoff)
        metadata = ManualAIHandoffWriterV1(self.root).prepare(
            handoff,
            invocation_id=invocation_id,
            prompt=self._manual_prompt(handoff, invocation_id),
            batch_schema=_batch_schema(),
            durable_contract_schema=_durable_contract_schema(),
            contract_hash_helper=_CONTRACT_HASH_HELPER,
        )
        result_path = self.root / str(metadata["result_path"])
        return metadata, result_path, invocation_id

    def _objective_payload(self, objective_id: str) -> Mapping[str, Any]:
        path = self.root / "data" / "research" / "research_factory" / "objectives" / f"{objective_id}.json"
        if not path.exists() and isinstance(self.runtime, SyntheticAutonomousResearchRuntimeV2):
            return {"objective_id": objective_id, "mechanism_scope": ["synthetic_new"], "allowed_factor_scope": ["SYNTHETIC_FACTOR"], "risk_constraints": {"final_test": "DISABLED", "prospective_access": "DISABLED", "real_order_execution": "DISABLED"}}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload

    def _activation_eligibility(self, objective_id: str) -> Mapping[str, Any] | None:
        path = self.root / "reports" / "research_orchestrator_v2" / objective_id / "activation_eligibility.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("CANONICAL_ACTIVATION_ELIGIBILITY_UNREADABLE") from exc
        if not isinstance(payload, Mapping) or str(payload.get("objective_id")) != objective_id:
            raise RuntimeError("CANONICAL_ACTIVATION_ELIGIBILITY_OBJECTIVE_MISMATCH")
        return payload

    def _activation_authorized(self, objective_id: str) -> bool:
        eligibility = self._activation_eligibility(objective_id)
        return bool(
            eligibility
            and eligibility.get("activation_authorized") is True
            and str(eligibility.get("activation_mode")) == "CREATE_AND_ACTIVATE"
            and str(eligibility.get("target_state")) == OrchestratorState.NEED_AI_RESEARCH_DESIGN.value
        )

    def _data_capability_payload(self) -> Any:
        path = self.root / "data" / "research" / "data_capability.json"
        if not path.exists():
            return {"status": "NOT_AVAILABLE"}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {"status": "UNREADABLE"}
        return _safe_design_payload(payload)

    def _ai_record(self, handoff: Mapping[str, Any]) -> dict[str, Any] | None:
        record = self.store.load_invocation()
        if record and str(record.get("handoff_id")) != str(handoff.get("handoff_id")):
            return None
        return record

    def _ai_failure(self, handoff: Mapping[str, Any], record: dict[str, Any], code: str, *, retryable: bool) -> dict[str, Any]:
        attempt = int(record.get("attempt", 0))
        record.update({"status": "FAILED", "error_code": code, "retryable": retryable, "completed_at": now_timestamp(), "next_retry_at": now_timestamp() if self.config.backoff_seconds == 0 else datetime.fromtimestamp(time.time() + self.config.backoff_seconds * (2 ** max(0, attempt - 1)), timezone.utc).isoformat()})
        self.store.atomic_write(self.store.invocation_path, record)
        self.store.append_event("CODEX_INVOCATION_COMPLETED", {"handoff_id": handoff["handoff_id"], "ai_invocation_id": record["ai_invocation_id"], "status": "FAILED", "error_code": code}, event_id=stable_hash({"handoff_id": handoff["handoff_id"], "attempt": attempt, "error": code}))
        if attempt >= self.config.max_attempts_per_handoff or not retryable:
            target = OrchestratorState.AI_INVOCATION_UNAVAILABLE if code.startswith("AI_INVOCATION_UNAVAILABLE") else OrchestratorState.AI_HANDOFF_BLOCKED
            self._set_state(target, target.value, error_code=code, attempt=attempt)
        else:
            self._set_state(OrchestratorState.AI_INVOCATION_PENDING, "AI_INVOCATION_RETRY_SCHEDULED", error_code=code, attempt=attempt)
        return self.status().to_dict()

    @staticmethod
    def _manual_result_fingerprint(path: Path) -> str | None:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None

    @staticmethod
    def _manual_result_timestamp(payload: Mapping[str, Any]) -> datetime | None:
        raw = payload.get("generated_at") or payload.get("created_at")
        if not raw:
            return None
        try:
            value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @staticmethod
    def _validation_error_message(code: str) -> str:
        messages = {
            "AI_BATCH_CANDIDATE_IDS_INVALID": "候选编号字段格式不符合输出契约（应为数组）",
            "AI_BATCH_CANDIDATE_HASHES_INVALID": "候选哈希字段格式不符合输出契约（应为对象）",
            "AI_BATCH_VALIDATION_TIMEOUT": "校验超过允许时限",
        }
        base, separator, detail = str(code).partition(":")
        if base == "AI_BATCH_NOOUTCOME_COMPLIANCE_FAILED" and separator:
            return f"无结果合规校验失败：{detail}"
        if base == "AI_BATCH_DURABLE_CONTRACT_INVALID" and separator:
            return f"冻结合同字段或哈希不一致：{detail}"
        if base == "AI_BATCH_DURABLE_CONTRACT_INTERACTION_SEMANTICS_INVALID" and separator:
            return f"冻结合同交互语义字段不符合固定结构：{detail}"
        if base == "AI_BATCH_DURABLE_CONTRACT_PROVIDER_INCOMPATIBLE" and separator:
            return f"冻结合同无法由本地 Provider 重建：{detail}"
        return messages.get(base, str(code))

    def _begin_validation(self, record: dict[str, Any]) -> None:
        timestamp = now_timestamp()
        record.update({
            "validation_status": "IN_PROGRESS",
            "validation_stage": "FORMAT",
            "validation_started_at": timestamp,
            "validation_last_activity_at": timestamp,
            "validation_finished_at": None,
            "validation_error": None,
        })
        self.store.atomic_write(self.store.invocation_path, record)

    def _record_validation_activity(self, record: dict[str, Any], stage: str) -> None:
        record["validation_stage"] = str(stage)
        record["validation_last_activity_at"] = now_timestamp()
        self.store.atomic_write(self.store.invocation_path, record)

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if not pid or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        return True

    def _manual_result_rejected(self, handoff: Mapping[str, Any], record: dict[str, Any], *, code: str, message_zh: str, fingerprint: str | None) -> dict[str, Any]:
        timestamp = now_timestamp()
        validation_started = bool(record.get("validation_started_at"))
        record.update({
            "status": "INVALID",
            "validation_status": "INVALID",
            "ingestion_status": "NOT_STARTED",
            "error_code": code,
            "error_message_zh": message_zh,
            "retryable": False,
            "background_ai_token_consumption": 0,
            "result_fingerprint": fingerprint,
            "completed_at": timestamp,
            "validation_error": code,
            "validation_stage": "FAILED" if validation_started else record.get("validation_stage"),
            "validation_last_activity_at": timestamp if validation_started else record.get("validation_last_activity_at"),
            "validation_finished_at": timestamp if validation_started else record.get("validation_finished_at"),
        })
        self.store.atomic_write(self.store.invocation_path, record)
        self.store.append_event("AI_MANUAL_RESULT_REJECTED", {"handoff_id": handoff["handoff_id"], "ai_invocation_id": record["ai_invocation_id"], "error_code": code}, event_id=stable_hash({"handoff_id": handoff["handoff_id"], "result_fingerprint": fingerprint, "error_code": code}))
        if OrchestratorState(str(self.checkpoint["state"])) != OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED:
            self._set_state(OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED, "AI_MANUAL_RESULT_REJECTED", error_code=code)
        return self.status().to_dict()

    def _manual_result_engineering_failed(self, handoff: Mapping[str, Any], record: dict[str, Any], *, exc: Exception, fingerprint: str | None) -> dict[str, Any]:
        timestamp = now_timestamp()
        code = f"AI_MANUAL_RESULT_VALIDATION_ENGINEERING_ERROR:{type(exc).__name__}"
        record.update({
            "status": "ENGINEERING_BLOCKED",
            "validation_status": "ERROR",
            "ingestion_status": "NOT_STARTED",
            "error_code": code,
            "error_message_zh": "AI 研究结果校验过程中发生工程异常，系统已停止继续研究；请检查后台研究服务。",
            "retryable": False,
            "background_ai_token_consumption": 0,
            "result_fingerprint": fingerprint,
            "completed_at": timestamp,
            "validation_error": code,
            "validation_stage": "FAILED",
            "validation_last_activity_at": timestamp,
            "validation_finished_at": timestamp,
        })
        self.store.atomic_write(self.store.invocation_path, record)
        self.store.append_event(
            "AI_MANUAL_RESULT_VALIDATION_ENGINEERING_FAILED",
            {"handoff_id": handoff["handoff_id"], "ai_invocation_id": record["ai_invocation_id"], "error_code": code},
            event_id=stable_hash({"handoff_id": handoff["handoff_id"], "result_fingerprint": fingerprint, "error_code": code}),
        )
        if OrchestratorState(str(self.checkpoint["state"])) != OrchestratorState.ENGINEERING_BLOCKED:
            self._set_state(OrchestratorState.ENGINEERING_BLOCKED, "AI_MANUAL_RESULT_VALIDATION_ENGINEERING_FAILED", error_code=code)
        return self.status().to_dict()

    def _run_manual_ai(self, snapshot: CanonicalResearchSnapshotV2, handoff: Mapping[str, Any], record: dict[str, Any] | None) -> dict[str, Any]:
        metadata, result_path, invocation_id = self._manual_task(handoff)
        if record is None or str(record.get("ai_invocation_id")) != invocation_id:
            record = {
                "schema_version": "research-orchestrator-ai-invocation-v2",
                "ai_invocation_id": invocation_id,
                "handoff_id": handoff["handoff_id"],
                "objective_id": self.objective_id,
                "attempt": 0,
                "max_attempts": self.config.max_attempts_per_handoff,
                "status": "WAITING_FOR_MANUAL_HANDOFF",
                "validation_status": "NOT_RUN",
                "ingestion_status": "NOT_STARTED",
                "output_manifest_path": metadata["result_path"],
                "manual_task": metadata,
                "ai_invocation_mode": self.config.ai_invocation_mode,
                "background_ai_token_consumption": 0,
                "created_at": metadata["created_at"],
            }
            self.store.atomic_write(self.store.invocation_path, record)
            self.checkpoint["current_ai_invocation_id"] = invocation_id
            self._save()
        elif record.get("status") == "INVALID" and record.get("result_fingerprint") == self._manual_result_fingerprint(result_path):
            if OrchestratorState(str(self.checkpoint["state"])) != OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED:
                self._set_state(OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED, "AI_MANUAL_HANDOFF_REQUIRED")
            return self.status().to_dict()

        inspected = ManualAIResultWatcherV1(self.root).inspect(str(handoff["handoff_id"]))
        if inspected.get("status") == "WAITING":
            record.update({"status": "WAITING_FOR_MANUAL_HANDOFF", "manual_task": metadata, "output_manifest_path": metadata["result_path"], "background_ai_token_consumption": 0})
            self.store.atomic_write(self.store.invocation_path, record)
            self._set_state(OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED, "AI_MANUAL_HANDOFF_REQUIRED", handoff_id=handoff["handoff_id"], result_path=metadata["result_path"])
            return self.status().to_dict()
        if inspected.get("status") in {"INVALID_JSON", "INVALID_TASK"}:
            return self._manual_result_rejected(handoff, record, code=str(inspected.get("reason_code") or "AI_MANUAL_RESULT_INVALID"), message_zh=str(inspected.get("reason_zh") or "结果文件未通过读取检查，未接入研究。"), fingerprint=self._manual_result_fingerprint(result_path))
        manifest = inspected.get("payload")
        fingerprint = self._manual_result_fingerprint(result_path)
        if not isinstance(manifest, Mapping):
            return self._manual_result_rejected(handoff, record, code="AI_MANUAL_RESULT_OBJECT_REQUIRED", message_zh="结果文件必须是一个 JSON 对象，未接入研究。", fingerprint=fingerprint)
        if str(manifest.get("ai_invocation_id") or "") != invocation_id:
            return self._manual_result_rejected(handoff, record, code="AI_MANUAL_RESULT_INVOCATION_ID_MISMATCH", message_zh="结果文件的调用编号与当前任务不一致，未接入研究。", fingerprint=fingerprint)
        task_created = self._manual_result_timestamp({"created_at": metadata.get("created_at")})
        result_created = self._manual_result_timestamp(manifest)
        if task_created is not None and result_created is not None and (task_created - result_created).total_seconds() > 1:
            return self._manual_result_rejected(handoff, record, code="AI_MANUAL_RESULT_STALE", message_zh="结果文件早于当前手动任务，已判定为过期结果，未接入研究。", fingerprint=fingerprint)
        record.update({"status": "COMPLETED", "validation_status": "PENDING", "manual_result_detected_at": now_timestamp(), "result_fingerprint": fingerprint, "output_manifest_path": metadata["result_path"], "manual_task": metadata, "background_ai_token_consumption": 0})
        self.store.atomic_write(self.store.invocation_path, record)
        if OrchestratorState(str(self.checkpoint["state"])) != OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED:
            self._set_state(OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED, "AI_MANUAL_RESULT_FOUND", handoff_id=handoff["handoff_id"])
        self._begin_validation(record)
        try:
            return self._accept_ai_output(
                snapshot,
                handoff,
                record,
                manifest,
                result_path,
                staging_dir=result_path.parent,
                validation_activity=lambda stage: self._record_validation_activity(record, stage),
            )
        except AIBatchValidationError as exc:
            error_code = f"AI_MANUAL_RESULT_INVALID:{exc}"
            return self._manual_result_rejected(handoff, record, code=error_code, message_zh=f"结果文件未通过研究校验：{self._validation_error_message(str(exc))}。未接入研究，也未消耗预测试验预算。", fingerprint=fingerprint)
        except RuntimeError as exc:
            if str(exc).startswith("SYNTHETIC_CRASH_INJECTED:"):
                raise
            return self._manual_result_rejected(handoff, record, code=f"AI_MANUAL_RESULT_INGESTION_FAILED:{exc}", message_zh="结果文件在接入时发生错误，未完成研究接入，也未自动重试。", fingerprint=fingerprint)
        except Exception as exc:
            return self._manual_result_engineering_failed(handoff, record, exc=exc, fingerprint=fingerprint)

    @staticmethod
    def _manifest_matches_existing(snapshot: CanonicalResearchSnapshotV2, manifest: Mapping[str, Any]) -> bool:
        existing = {str(item.get("candidate_id")): str(item.get("candidate_hash") or "") for item in snapshot.candidates}
        candidate_ids = [str(item) for item in manifest.get("candidate_ids", ())]
        hashes = {str(key): str(value) for key, value in (manifest.get("candidate_hashes") or {}).items()}
        return bool(candidate_ids) and all(candidate_id in existing and existing[candidate_id] == hashes.get(candidate_id) for candidate_id in candidate_ids)

    def _accept_ai_output(self, snapshot: CanonicalResearchSnapshotV2, handoff: Mapping[str, Any], record: dict[str, Any], manifest: Mapping[str, Any], manifest_path: Path, *, staging_dir: Path | None = None, validation_activity: Callable[[str], None] | None = None) -> dict[str, Any]:
        if record.get("ingestion_status") == "COMPLETED":
            record["status"] = "ACCEPTED"
            record["accepted_at"] = record.get("accepted_at") or now_timestamp()
            self.store.atomic_write(self.store.invocation_path, record)
            self._set_state(OrchestratorState.LOCAL_RESEARCH_RESUMING, "AI_BATCH_ACCEPTED", candidate_count=int(record.get("candidate_count", 0)))
            return self.status().to_dict()
        if record.get("ingestion_status") in {"IN_PROGRESS", "COMPLETED"} and self._manifest_matches_existing(snapshot, manifest):
            self._set_state(OrchestratorState.AI_BATCH_INGESTING, "AI_BATCH_REPLAY_RECONCILIATION", candidate_count=len(manifest.get("candidate_ids", ())))
            self.runtime.ingest_ai_batch(manifest_path, manifest)
            record["ingestion_status"] = "COMPLETED"
            record["status"] = "ACCEPTED"
            record["accepted_at"] = record.get("accepted_at") or now_timestamp()
            self.store.atomic_write(self.store.invocation_path, record)
            self._set_state(OrchestratorState.LOCAL_RESEARCH_RESUMING, "AI_BATCH_ACCEPTED", candidate_count=len(manifest.get("candidate_ids", ())))
            return self.status().to_dict()
        self._set_state(OrchestratorState.AI_OUTPUT_VALIDATING, "AI_OUTPUT_VALIDATING")
        validation = AIBatchValidatorV2(self.root).validate(manifest, handoff, snapshot, staging_dir=staging_dir, on_stage=validation_activity)
        record["candidate_count"] = int(validation["candidate_count"])
        record["validation_status"] = "PASS"
        record["validation_stage"] = "COMPLETED"
        record["validation_last_activity_at"] = now_timestamp()
        record["validation_finished_at"] = record["validation_last_activity_at"]
        record["validation_error"] = None
        self._set_state(OrchestratorState.AI_BATCH_INGESTING, "AI_BATCH_INGESTING", candidate_count=validation["candidate_count"])
        record["ingestion_status"] = "IN_PROGRESS"
        self.store.atomic_write(self.store.invocation_path, record)
        self.runtime.ingest_ai_batch(manifest_path, {**dict(manifest), "candidates": validation["accepted_candidates"]}, staging_dir=staging_dir)
        record["ingestion_status"] = "COMPLETED"
        record["ingested_manifest_hash"] = stable_hash(manifest)
        self.store.atomic_write(self.store.invocation_path, record)
        if self.crash_at == "after_ai_batch_ingest":
            raise RuntimeError("SYNTHETIC_CRASH_INJECTED:after_ai_batch_ingest")
        record["status"] = "ACCEPTED"
        record["accepted_at"] = now_timestamp()
        record["resume_status"] = "LOCAL_RESEARCH_RESUMING"
        self.store.atomic_write(self.store.invocation_path, record)
        self.store.append_event("AI_BATCH_ACCEPTED", {"handoff_id": handoff["handoff_id"], "ai_invocation_id": record["ai_invocation_id"], "candidate_count": validation["candidate_count"]}, event_id=stable_hash({"handoff_id": handoff["handoff_id"], "manifest_hash": stable_hash(manifest)}))
        self._set_state(OrchestratorState.LOCAL_RESEARCH_RESUMING, "LOCAL_RESEARCH_RESUMED_AFTER_AI_BATCH")
        if self.crash_at == "after_local_resume":
            raise RuntimeError("SYNTHETIC_CRASH_INJECTED:after_local_resume")
        return self.status().to_dict()

    def _ensure_invalidated_one_shot_governance(self, snapshot: CanonicalResearchSnapshotV2, invalidations: Mapping[str, Mapping[str, Any]]) -> None:
        correction_ids = sorted(str(event.get("event_id") or "") for event in invalidations.values())
        governance_identity = {
            "objective_id": self.objective_id,
            "reason": "AI_ONE_SHOT_CANDIDATE_INVALIDATED",
            "correction_event_ids": correction_ids,
        }
        existing = json.loads(self.store.governance_path.read_text(encoding="utf-8")) if self.store.governance_path.exists() else None
        if existing is not None and str(existing.get("reason")) != "AI_ONE_SHOT_CANDIDATE_INVALIDATED":
            return
        desired = {
            "schema_version": "research-governance-decision-required-v2",
            "decision_id": f"GOVERNANCE_{stable_hash(governance_identity)[:20]}",
            "objective_id": self.objective_id,
            "reason": "AI_ONE_SHOT_CANDIDATE_INVALIDATED",
            "current_terminal_summary": {
                "invalidated_candidate_ids": sorted(invalidations),
                "correction_event_ids": correction_ids,
                "budget": dict(snapshot.budget),
                "new_predictive_trials": 0,
                "new_ai_handoffs": 0,
            },
            "allowed_choices": [
                {"choice": "STOP_RESEARCH", "consequence": "保留本轮追加式纠错和 ONE_SHOT 终态，不创建新目标"},
                {"choice": "START_PROMISING_FOLLOWUP_OBJECTIVE", "consequence": "围绕 canonical PROMISING 父候选创建独立目标、预算和预注册合同"},
            ],
            "forbidden_automatic_actions": ["CREATE_NEW_AI_HANDOFF", "CREATE_NEW_OBJECTIVE", "EXPAND_BUDGET", "RUN_PREDICTIVE_TRIAL", "OPEN_FINAL_TEST", "START_PROSPECTIVE", "ENABLE_REAL_ORDER"],
            "created_at": str((existing or {}).get("created_at") or now_timestamp()),
        }
        if existing != desired:
            self.store.atomic_write(self.store.governance_path, desired)

    def _run_ai(self, snapshot: CanonicalResearchSnapshotV2) -> dict[str, Any]:
        state = OrchestratorState(str(self.checkpoint["state"]))
        accepted_invocation = self.store.load_invocation()
        invalidations = load_effective_contract_invalidations(self.root, self.objective_id)
        if (
            self._one_shot_policy_active()
            and invalidations
            and accepted_invocation is not None
            and str(accepted_invocation.get("status")) == "ACCEPTED"
            and str(accepted_invocation.get("ingestion_status")) == "COMPLETED"
            and not snapshot.has_legal_candidate
        ):
            self._ensure_invalidated_one_shot_governance(snapshot, invalidations)
            self.checkpoint["current_handoff_id"] = None
            self.checkpoint["current_ai_invocation_id"] = None
            self.checkpoint["terminal_reason"] = "AI_ONE_SHOT_CANDIDATE_INVALIDATED"
            self._set_state(
                OrchestratorState.GOVERNANCE_DECISION_REQUIRED,
                "AI_ONE_SHOT_CANDIDATE_INVALIDATED",
                invalidated_candidate_ids=sorted(invalidations),
                previous_invocation_id=accepted_invocation.get("ai_invocation_id"),
            )
            return self.status().to_dict()
        consumed_one_shot = self._accepted_one_shot_invocation() if self._one_shot_policy_active() else None
        if consumed_one_shot is not None and not snapshot.has_legal_candidate:
            self.checkpoint["current_handoff_id"] = None
            self.checkpoint["current_ai_invocation_id"] = None
            self.checkpoint["terminal_reason"] = "AI_ONE_SHOT_POLICY_EXHAUSTED"
            self._set_state(
                OrchestratorState.AI_HANDOFF_BLOCKED,
                "AI_ONE_SHOT_POLICY_EXHAUSTED",
                previous_handoff_id=consumed_one_shot.get("handoff_id"),
                previous_invocation_id=consumed_one_shot.get("ai_invocation_id"),
            )
            return self.status().to_dict()
        if self.config.ai_invocation_mode == "AI_DISABLED":
            if state != OrchestratorState.AI_RESEARCH_DISABLED:
                self._set_state(OrchestratorState.AI_RESEARCH_DISABLED, "AI_RESEARCH_DISABLED")
            return self.status().to_dict()
        existing_handoff = self._read_handoff()
        if str(self.checkpoint.get("current_handoff_id")) != str(existing_handoff.get("handoff_id") if existing_handoff else ""):
            handoff = self._handoff(snapshot)
        else:
            handoff = existing_handoff
            if handoff is None:
                handoff = self._handoff(snapshot)
            elif OrchestratorState(str(self.checkpoint["state"])) == OrchestratorState.NEED_AI_RESEARCH_DESIGN:
                self._set_state(OrchestratorState.AI_HANDOFF_PREPARING, "AI_HANDOFF_REUSED")
        record = self._ai_record(handoff)
        if record and record.get("status") in {"ACCEPTED", "COMPLETED"} and record.get("ingestion_status") == "COMPLETED" and not snapshot.has_legal_candidate:
            if self._one_shot_policy_active():
                self._set_state(OrchestratorState.AI_HANDOFF_BLOCKED, "AI_ONE_SHOT_POLICY_EXHAUSTED", previous_handoff_id=handoff.get("handoff_id"), previous_invocation_id=record.get("ai_invocation_id"))
                return self.status().to_dict()
            self.store.atomic_write(self.store.run_dir / "invocation_history" / f"{record['ai_invocation_id']}.json", record)
            self.checkpoint["handoff_generation"] = int(self.checkpoint.get("handoff_generation", 0) or 0) + 1
            self.checkpoint["current_handoff_id"] = None
            self.checkpoint["current_ai_invocation_id"] = None
            self._set_state(OrchestratorState.NEED_AI_RESEARCH_DESIGN, "AI_BATCH_CONSUMED_NEXT_HANDOFF", previous_handoff_id=handoff.get("handoff_id"), previous_invocation_id=record.get("ai_invocation_id"))
            return self._run_ai(snapshot)
        if self.config.ai_invocation_mode == "MANUAL_HANDOFF":
            return self._run_manual_ai(snapshot, handoff, record)
        if OrchestratorState(str(self.checkpoint["state"])) == OrchestratorState.AI_HANDOFF_PREPARING:
            self._set_state(OrchestratorState.AI_INVOCATION_PENDING, "AI_INVOCATION_PENDING")
        if record and record.get("status") == "FAILED":
            retry_at = str(record.get("next_retry_at") or "")
            if retry_at:
                try:
                    if datetime.fromisoformat(retry_at).timestamp() > time.time():
                        return self.status().to_dict()
                except ValueError:
                    pass
        if record and record.get("status") in {"ACCEPTED", "COMPLETED"}:
            manifest_path = Path(str(record.get("output_manifest_path")))
            if not manifest_path.is_absolute():
                manifest_path = self.root / manifest_path
            if not manifest_path.exists():
                return self._ai_failure(handoff, record, "AI_OUTPUT_MANIFEST_MISSING", retryable=False)
            staging_for_record = self.store.run_dir / "ai_staging" / str(handoff["handoff_id"]) / str(record.get("ai_invocation_id"))
            try:
                return self._accept_ai_output(snapshot, handoff, record, json.loads(manifest_path.read_text(encoding="utf-8")), manifest_path, staging_dir=staging_for_record)
            except RuntimeError as exc:
                if str(exc).startswith("SYNTHETIC_CRASH_INJECTED:"):
                    raise
                return self._ai_failure(handoff, record, f"AI_BATCH_INGESTION_FAILED:{exc}", retryable=False)
        if record and record.get("status") == "RUNNING":
            pid = int((record.get("process_identity") or {}).get("pid", 0) or 0)
            if pid == os.getpid():
                return self.status().to_dict()
            staged_response = self.store.run_dir / "ai_staging" / str(handoff["handoff_id"]) / str(record.get("ai_invocation_id")) / "AI_RESEARCH_BATCH_RESULT_V2.json"
            if staged_response.exists():
                try:
                    staged_manifest = json.loads(staged_response.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    staged_manifest = None
                if isinstance(staged_manifest, Mapping):
                    manifest_path = self.store.run_dir / "ai_batches" / f"{handoff['handoff_id']}.json"
                    self.store.atomic_write(manifest_path, staged_manifest)
                    record.update({"status": "COMPLETED", "completed_at": now_timestamp(), "output_manifest_path": str(manifest_path.relative_to(self.root)).replace("\\", "/"), "result_manifest_hash": stable_hash(staged_manifest), "recovered_from_staging": True})
                    self.store.atomic_write(self.store.invocation_path, record)
                    try:
                        return self._accept_ai_output(snapshot, handoff, record, staged_manifest, manifest_path, staging_dir=staged_response.parent)
                    except RuntimeError as exc:
                        if str(exc).startswith("SYNTHETIC_CRASH_INJECTED:"):
                            raise
                        return self._ai_failure(handoff, record, f"AI_BATCH_INGESTION_FAILED:{exc}", retryable=False)
            record["status"] = "FAILED"
        attempt = int(record.get("attempt", 0) if record else 0) + 1
        invocation_identity = {"handoff_id": handoff["handoff_id"], "attempt": attempt}
        invocation_id = f"AI_INVOCATION_V2_{stable_hash(invocation_identity)[:20]}"
        record = {"schema_version": "research-orchestrator-ai-invocation-v2", "handoff_id": handoff["handoff_id"], "objective_id": self.objective_id, "ai_invocation_id": invocation_id, "attempt": attempt, "max_attempts": self.config.max_attempts_per_handoff, "status": "RUNNING", "started_at": now_timestamp(), "process_identity": {"pid": os.getpid()}, "prompt_template_version": self.PROMPT_TEMPLATE_VERSION, "handoff_hash": stable_hash(handoff)}
        self.store.atomic_write(self.store.invocation_path, record)
        self.checkpoint["current_ai_invocation_id"] = invocation_id
        self._set_state(OrchestratorState.AI_INVOCATION_RUNNING, "CODEX_INVOCATION_STARTED", ai_invocation_id=invocation_id, attempt=attempt)
        if self.crash_at == "before_ai_invocation":
            raise RuntimeError("SYNTHETIC_CRASH_INJECTED:before_ai_invocation")
        staging = self.store.run_dir / "ai_staging" / str(handoff["handoff_id"]) / invocation_id
        try:
            manifest = dict(self.ai_invoker.invoke(handoff, invocation_id=invocation_id, staging_dir=staging, timeout_seconds=self.config.timeout_seconds))
            manifest_path = self.store.run_dir / "ai_batches" / f"{handoff['handoff_id']}.json"
            self.store.atomic_write(manifest_path, manifest)
            record.update({"status": "COMPLETED", "completed_at": now_timestamp(), "output_manifest_path": str(manifest_path.relative_to(self.root)).replace("\\", "/"), "result_manifest_hash": stable_hash(manifest)})
            self.store.atomic_write(self.store.invocation_path, record)
            self.store.append_event("CODEX_INVOCATION_COMPLETED", {"handoff_id": handoff["handoff_id"], "ai_invocation_id": invocation_id, "result_manifest_hash": stable_hash(manifest)}, event_id=stable_hash({"handoff_id": handoff["handoff_id"], "ai_invocation_id": invocation_id, "result_manifest_hash": stable_hash(manifest)}))
            if self.crash_at == "after_codex_completion":
                raise RuntimeError("SYNTHETIC_CRASH_INJECTED:after_codex_completion")
            return self._accept_ai_output(snapshot, handoff, record, manifest, manifest_path, staging_dir=staging)
        except AIBatchValidationError as exc:
            return self._ai_failure(handoff, record, str(exc), retryable=True)
        except AIInvocationError as exc:
            return self._ai_failure(handoff, record, exc.code, retryable=exc.retryable)
        except RuntimeError as exc:
            if str(exc).startswith("SYNTHETIC_CRASH_INJECTED:"):
                raise
            return self._ai_failure(handoff, record, f"AI_BATCH_INGESTION_FAILED:{exc}", retryable=False)

    def _read_handoff(self) -> Mapping[str, Any] | None:
        if not self.store.handoff_path.exists():
            return None
        return json.loads(self.store.handoff_path.read_text(encoding="utf-8"))

    def _terminal_closeout(self, snapshot: CanonicalResearchSnapshotV2, *, terminal_reason: str | None = None) -> dict[str, Any]:
        if terminal_reason is not None:
            snapshot = replace(snapshot, terminal_reason=terminal_reason)
        state = OrchestratorState(str(self.checkpoint["state"]))
        if state in {OrchestratorState.BOOTSTRAP, OrchestratorState.ACTIVE, OrchestratorState.LOCAL_RESEARCH_RUNNING, OrchestratorState.RESEARCH_PASSED, OrchestratorState.BUDGET_EXHAUSTED, OrchestratorState.GLOBAL_SEARCH_EXHAUSTED}:
            target = {"RESEARCH_PASSED": OrchestratorState.RESEARCH_PASSED, "GLOBAL_SEARCH_EXHAUSTED": OrchestratorState.GLOBAL_SEARCH_EXHAUSTED}.get(snapshot.daemon_state, OrchestratorState.BUDGET_EXHAUSTED)
            if state != target:
                self._set_state(target, snapshot.terminal_reason or target.value)
            self._set_state(OrchestratorState.TERMINAL_CLOSEOUT_PENDING, "TERMINAL_CLOSEOUT_PENDING")
        if OrchestratorState(str(self.checkpoint["state"])) == OrchestratorState.TERMINAL_CLOSEOUT_PENDING:
            self._set_state(OrchestratorState.TERMINAL_CLOSEOUT_RUNNING, "TERMINAL_CLOSEOUT_STARTED")
        if self.crash_at == "during_terminal_closeout" and not self.store.closeout_path.exists():
            raise RuntimeError("SYNTHETIC_CRASH_INJECTED:during_terminal_closeout")
        payload = TerminalCloseoutServiceV2(self.root).close(snapshot, self.store)
        self.checkpoint["closeout_id"] = payload["closeout_id"]
        if OrchestratorState(str(self.checkpoint["state"])) == OrchestratorState.TERMINAL_CLOSEOUT_RUNNING:
            self._set_state(OrchestratorState.TERMINAL_CLOSEOUT_COMPLETE, "TERMINAL_CLOSEOUT_COMPLETED", closeout_id=payload["closeout_id"])
        if self.crash_at == "after_closeout_before_governance":
            raise RuntimeError("SYNTHETIC_CRASH_INJECTED:after_closeout_before_governance")
        self._set_state(OrchestratorState.GOVERNANCE_DECISION_REQUIRED, "GOVERNANCE_DECISION_REQUIRED", closeout_id=payload["closeout_id"])
        return self.status().to_dict()

    def reconcile_closeout_reference(self) -> dict[str, Any]:
        """Reconcile only a stale checkpoint pointer to an existing canonical closeout."""
        snapshot = self.runtime.snapshot()
        if not self.store.closeout_path.exists():
            raise RuntimeError("CLOSEOUT_REFERENCE_RECONCILIATION_REPORT_MISSING")
        payload = json.loads(self.store.closeout_path.read_text(encoding="utf-8"))
        canonical_id = TerminalCloseoutServiceV2.canonical_closeout_id(snapshot)
        if str(payload.get("closeout_id")) != canonical_id:
            raise RuntimeError("CLOSEOUT_REFERENCE_RECONCILIATION_CANONICAL_REPORT_MISMATCH")
        for key in ("objective_id", "objective_hash", "terminal_reason"):
            if str(payload.get(key)) != str(getattr(snapshot, key)):
                raise RuntimeError(f"CLOSEOUT_REFERENCE_RECONCILIATION_{key.upper()}_MISMATCH")
        if str(self.checkpoint.get("state")) != OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value:
            raise RuntimeError("CLOSEOUT_REFERENCE_RECONCILIATION_NONTERMINAL_STATE")

        transition = self.checkpoint.get("last_transition") if isinstance(self.checkpoint.get("last_transition"), Mapping) else {}
        details = transition.get("details") if isinstance(transition.get("details"), Mapping) else {}
        previous_ids = [str(value) for value in (self.checkpoint.get("closeout_id"), details.get("closeout_id")) if value]
        already_reconciled = all(value == canonical_id for value in previous_ids) if previous_ids else True
        if already_reconciled:
            return {"status": "NOOP", "closeout_id": canonical_id, "previous_closeout_ids": previous_ids}

        previous_id = next((value for value in previous_ids if value != canonical_id), None)
        timestamp = now_timestamp()
        self.checkpoint["closeout_id"] = canonical_id
        self.checkpoint["last_transition"] = {
            "timestamp": timestamp,
            "previous_state": OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value,
            "new_state": OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value,
            "reason_code": "CLOSEOUT_REFERENCE_RECONCILED",
            "details": {
                "closeout_id": canonical_id,
                "previous_closeout_id": previous_id,
                "canonical_report_ref": str(self.store.closeout_path.relative_to(self.root)).replace("\\", "/"),
                "semantic_closeout_reused": True,
            },
        }
        self.store.append_event(
            "CLOSEOUT_REFERENCE_RECONCILED",
            {
                "objective_id": self.objective_id,
                "state": OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value,
                "closeout_id": canonical_id,
                "previous_closeout_id": previous_id,
                "semantic_closeout_reused": True,
            },
            event_id=stable_hash({"objective_id": self.objective_id, "closeout_id": canonical_id, "previous_closeout_id": previous_id}),
        )
        self._save()
        return {"status": "PASS", "closeout_id": canonical_id, "previous_closeout_ids": previous_ids}

    def _control(self) -> str | None:
        if not self.store.control_path.exists():
            return None
        return str(json.loads(self.store.control_path.read_text(encoding="utf-8")).get("action") or "").upper() or None

    def _handle_control(self) -> bool:
        action = self._control()
        if action == "PAUSE" and OrchestratorState(str(self.checkpoint["state"])) not in {OrchestratorState.PAUSED, OrchestratorState.SHUTDOWN}:
            self._set_state(OrchestratorState.PAUSED, "PAUSE_REQUESTED")
            self.store.control_path.unlink(missing_ok=True)
            return True
        if action == "STOP" and OrchestratorState(str(self.checkpoint["state"])) != OrchestratorState.SHUTDOWN:
            self._set_state(OrchestratorState.SHUTDOWN, "STOP_REQUESTED")
            self.store.control_path.unlink(missing_ok=True)
            return True
        return False

    def step(self) -> dict[str, Any]:
        if self._handle_control():
            return self.status().to_dict()
        self._recover()
        state = OrchestratorState(str(self.checkpoint["state"]))
        snapshot = self.runtime.snapshot()
        self.checkpoint["daemon_linkage"] = {"daemon_run_id": snapshot.daemon_run_id, "daemon_state": snapshot.daemon_state}
        self.checkpoint["terminal_reason"] = snapshot.terminal_reason
        if state == OrchestratorState.AI_RESEARCH_DISABLED and self.config.ai_invocation_mode != "AI_DISABLED":
            self._set_state(OrchestratorState.NEED_AI_RESEARCH_DESIGN, "AI_RESEARCH_RE_ENABLED")
            state = OrchestratorState.NEED_AI_RESEARCH_DESIGN
        if (
            state == OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED
            and self._one_shot_policy_active()
            and self._accepted_one_shot_invocation() is not None
        ):
            return self._run_ai(snapshot)
        if state in {OrchestratorState.GOVERNANCE_DECISION_REQUIRED, OrchestratorState.PAUSED, OrchestratorState.SHUTDOWN, OrchestratorState.AI_HANDOFF_BLOCKED, OrchestratorState.AI_INVOCATION_UNAVAILABLE, OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED, OrchestratorState.AI_RESEARCH_DISABLED, OrchestratorState.NO_PROGRESS_RESEARCH_LOOP, OrchestratorState.ENGINEERING_BLOCKED}:
            self._save()
            return self.status().to_dict()
        if snapshot.daemon_state in TERMINAL_DAEMON_STATES or int(snapshot.budget.get("remaining", 0)) <= 0:
            return self._terminal_closeout(snapshot)
        if state == OrchestratorState.TERMINAL_CLOSEOUT_COMPLETE:
            return self._terminal_closeout(snapshot)
        if snapshot.daemon_state == "ENGINEERING_BLOCKED":
            self._set_state(OrchestratorState.ENGINEERING_BLOCKED, "ENGINEERING_BLOCKED", required_action="HUMAN_REPAIR_REQUIRED")
            return self.status().to_dict()
        if snapshot.required_action == PREDICTIVE_AUTHORIZATION_REQUIRED:
            # Structural PASS is a governed boundary.  The daemon has already
            # completed the frozen candidate and explicitly requested a human
            # predictive-authorization decision, so the orchestrator must not
            # infer "no candidate => ask AI for another one" or run local
            # research again.  Collapse a stale running projection back to the
            # neutral ACTIVE control state and wait for the separate
            # authorization/start transaction.
            if state == OrchestratorState.LOCAL_RESEARCH_RUNNING:
                self._set_state(
                    OrchestratorState.ACTIVE,
                    PREDICTIVE_AUTHORIZATION_REQUIRED,
                    required_action=PREDICTIVE_AUTHORIZATION_REQUIRED,
                )
            else:
                self._save()
            return self.status().to_dict()
        if state in {
            OrchestratorState.AI_HANDOFF_PREPARING,
            OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED,
            OrchestratorState.AI_INVOCATION_PENDING,
            OrchestratorState.AI_INVOCATION_RUNNING,
            OrchestratorState.AI_OUTPUT_VALIDATING,
            OrchestratorState.AI_BATCH_INGESTING,
        }:
            return self._run_ai(snapshot)
        if snapshot.has_legal_candidate:
            if state != OrchestratorState.ACTIVE:
                self._set_state(OrchestratorState.ACTIVE, "LOCAL_RESEARCH_READY")
            self._set_state(OrchestratorState.LOCAL_RESEARCH_RUNNING, "LOCAL_RESEARCH_RUNNING")
            self.runtime.run_local()
            after = self.runtime.snapshot()
            self._set_state(OrchestratorState.ACTIVE, "LOCAL_RESEARCH_CHECKPOINTED")
            if after.daemon_state in TERMINAL_DAEMON_STATES or int(after.budget.get("remaining", 0)) <= 0:
                return self._terminal_closeout(after)
            return self.status().to_dict()
        if snapshot.global_search_exhausted:
            return self._terminal_closeout(snapshot)
        if snapshot.daemon_state == "GOVERNANCE_REQUIRED":
            self._set_state(OrchestratorState.GOVERNANCE_DECISION_REQUIRED, "GOVERNANCE_REQUIRED")
            return self.status().to_dict()
        objective_payload = self._objective_payload(snapshot.objective_id)
        objective_status = objective_payload.get("lifecycle_state", objective_payload.get("status"))
        ready_for_authorized_activation = objective_status == "READY" and self._activation_authorized(snapshot.objective_id)
        if objective_status not in (None, "ACTIVE") and not ready_for_authorized_activation:
            self._set_state(OrchestratorState.GOVERNANCE_DECISION_REQUIRED, "OBJECTIVE_NOT_ACTIVE", objective_status=objective_status)
            return self.status().to_dict()
        ai_states = {
            OrchestratorState.NEED_AI_RESEARCH_DESIGN,
            OrchestratorState.AI_HANDOFF_PREPARING,
            OrchestratorState.AI_INVOCATION_PENDING,
            OrchestratorState.AI_INVOCATION_RUNNING,
            OrchestratorState.AI_OUTPUT_VALIDATING,
            OrchestratorState.AI_BATCH_INGESTING,
            OrchestratorState.LOCAL_RESEARCH_RESUMING,
        }
        if state not in ai_states:
            self._set_state(OrchestratorState.NEED_AI_RESEARCH_DESIGN, "NEED_AI_RESEARCH_DESIGN")
        return self._run_ai(snapshot)

    def _run_unlocked(self, *, max_steps: int | None = None) -> dict[str, Any]:
        limit = int(max_steps or self.config.max_steps)
        previous_signature = None
        no_progress = 0
        for _ in range(limit):
            result = self.step()
            snapshot = self.runtime.snapshot()
            status = self.status().to_dict()
            state = str(status["orchestrator_state"])
            if state in {OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value, OrchestratorState.SHUTDOWN.value, OrchestratorState.PAUSED.value, OrchestratorState.AI_HANDOFF_BLOCKED.value, OrchestratorState.AI_INVOCATION_UNAVAILABLE.value, OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED.value, OrchestratorState.AI_RESEARCH_DISABLED.value, OrchestratorState.ENGINEERING_BLOCKED.value, OrchestratorState.NO_PROGRESS_RESEARCH_LOOP.value}:
                return result
            signature = (state, int(snapshot.budget.get("used", 0)), tuple(sorted(str(item.get("candidate_id")) for item in snapshot.candidates)), tuple(sorted(str(item.get("trial_id")) for item in snapshot.trials)))
            if signature == previous_signature:
                no_progress += 1
            else:
                no_progress = 0
            previous_signature = signature
            if no_progress >= 2:
                self._set_state(OrchestratorState.NO_PROGRESS_RESEARCH_LOOP, "NO_PROGRESS_RESEARCH_LOOP")
                return self.status().to_dict()
            if state == OrchestratorState.AI_INVOCATION_PENDING.value and self.config.backoff_seconds > 0:
                invocation = self.store.load_invocation() or {}
                retry_at = str(invocation.get("next_retry_at") or "")
                if retry_at:
                    try:
                        delay = max(0.0, datetime.fromisoformat(retry_at).timestamp() - time.time())
                    except ValueError:
                        delay = 0.0
                    if delay > 0:
                        time.sleep(min(delay, 60.0))
                        continue
                return result
        raise RuntimeError("ORCHESTRATOR_STEP_LIMIT_EXCEEDED")

    def run(self, *, max_steps: int | None = None) -> dict[str, Any]:
        from ..research_daemon_state import DaemonInstanceLockV1

        lock = DaemonInstanceLockV1(self.store.lock_path, self.objective_id)
        lock.acquire(run_id=str(self.checkpoint["orchestrator_run_id"]))
        try:
            return self._run_unlocked(max_steps=max_steps)
        finally:
            lock.release()

    def scan_manual_handoff(self) -> dict[str, Any]:
        """Scan the one expected result file and continue local research if accepted."""
        from ..research_daemon_state import DaemonInstanceLockV1

        lock = DaemonInstanceLockV1(self.store.lock_path, self.objective_id)
        lock.acquire(run_id=str(self.checkpoint["orchestrator_run_id"]))
        try:
            if self._handle_control():
                return self.status().to_dict()
            snapshot = self.runtime.snapshot()
            state = OrchestratorState(str(self.checkpoint["state"]))
            if state not in {
                OrchestratorState.NEED_AI_RESEARCH_DESIGN,
                OrchestratorState.AI_HANDOFF_PREPARING,
                OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED,
                OrchestratorState.AI_OUTPUT_VALIDATING,
                OrchestratorState.AI_BATCH_INGESTING,
            }:
                return self.status().to_dict()
            result = self._run_ai(snapshot)
            if str(result.get("orchestrator_state")) == OrchestratorState.LOCAL_RESEARCH_RESUMING.value:
                return self._run_unlocked(max_steps=self.config.max_steps)
            return result
        finally:
            lock.release()

    def watch_manual_result(self, *, poll_seconds: float = 5.0, timeout_seconds: float | None = None) -> dict[str, Any]:
        """Keep the objective-scoped process alive while a human task is pending."""
        deadline = time.monotonic() + timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        while True:
            result = self.scan_manual_handoff()
            state = str(result.get("orchestrator_state") or "")
            if state != OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED.value:
                return result
            if deadline is not None and time.monotonic() >= deadline:
                return result
            time.sleep(max(0.5, min(float(poll_seconds), 60.0)))

    def status(self) -> ResearchOrchestratorStatusView:
        snapshot = self.runtime.snapshot()
        invocation = self.store.load_invocation()
        handoff = self._read_handoff()
        state = str(self.checkpoint["state"])
        predictive_authorization_pending = snapshot.required_action == PREDICTIVE_AUTHORIZATION_REQUIRED
        projected_state = (
            OrchestratorState.ACTIVE.value
            if predictive_authorization_pending and state == OrchestratorState.LOCAL_RESEARCH_RUNNING.value
            else state
        )
        ai_running = projected_state == OrchestratorState.AI_INVOCATION_RUNNING.value
        ai_states = {
            OrchestratorState.NEED_AI_RESEARCH_DESIGN.value,
            OrchestratorState.AI_HANDOFF_PREPARING.value,
            OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED.value,
            OrchestratorState.AI_RESEARCH_DISABLED.value,
            OrchestratorState.AI_INVOCATION_PENDING.value,
            OrchestratorState.AI_INVOCATION_RUNNING.value,
            OrchestratorState.AI_OUTPUT_VALIDATING.value,
            OrchestratorState.AI_BATCH_INGESTING.value,
            OrchestratorState.LOCAL_RESEARCH_RESUMING.value,
            OrchestratorState.AI_INVOCATION_UNAVAILABLE.value,
            OrchestratorState.AI_HANDOFF_BLOCKED.value,
        }
        ai_status = projected_state if projected_state in ai_states else "IDLE"
        current_candidate = self.checkpoint.get("current_candidate")
        current_trial = self.checkpoint.get("current_trial")
        next_action = (
            PREDICTIVE_AUTHORIZATION_REQUIRED
            if predictive_authorization_pending
            else "GOVERNANCE_DECISION_REQUIRED"
            if projected_state == OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value
            else projected_state
        )
        if state in {
            OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value,
            OrchestratorState.AI_HANDOFF_BLOCKED.value,
        }:
            current_handoff_id = self.checkpoint.get("current_handoff_id")
            current_ai_invocation_id = self.checkpoint.get("current_ai_invocation_id")
        else:
            current_handoff_id = self.checkpoint.get("current_handoff_id") or (handoff or {}).get("handoff_id")
            current_ai_invocation_id = self.checkpoint.get("current_ai_invocation_id") or (invocation or {}).get("ai_invocation_id") or (invocation or {}).get("invocation_id")
        if state in {OrchestratorState.TERMINAL_CLOSEOUT_PENDING.value, OrchestratorState.TERMINAL_CLOSEOUT_RUNNING.value}:
            closeout_state = state
        elif self.store.closeout_path.exists():
            closeout_state = "COMPLETE"
        else:
            closeout_state = "NOT_STARTED"
        governance_path = self.store.run_dir / "governance_decision_submission.json"
        structural_governance_path = self.store.run_dir / "structural_governance_decision_required.json"
        governance_decision_state = (
            "REQUIRED"
            if predictive_authorization_pending and structural_governance_path.exists()
            else "RECORDED"
            if governance_path.exists()
            else "REQUIRED"
            if self.store.governance_path.exists() and projected_state == OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value
            else "NOT_REQUIRED"
        )
        retry_metadata = self.checkpoint.get("retry_metadata") if isinstance(self.checkpoint.get("retry_metadata"), Mapping) else {}
        retry_state = {
            "attempt": (invocation or {}).get("attempt") or retry_metadata.get("attempt") or 0,
            "max_attempts": self.config.max_attempts_per_handoff,
            "scheduled": bool(retry_metadata.get("next_retry_at")),
            "next_retry_at": retry_metadata.get("next_retry_at"),
        }
        mode_labels = {"MANUAL_HANDOFF": "手动 AI 交接（推荐）", "AUTO_CODEX": "自动调用 AI 研究员", "AI_DISABLED": "AI 研究已禁用"}
        manual_handoff = (invocation or {}).get("manual_task") if isinstance((invocation or {}).get("manual_task"), Mapping) else {}
        source_generated_at = str(self.checkpoint.get("updated_at")) if self.checkpoint.get("updated_at") else None
        observed_at = now_timestamp()
        process_id: int | None = None
        process_alive = False
        process_launch_path = self.store.run_dir / "process_launch.json"
        try:
            process_launch = json.loads(process_launch_path.read_text(encoding="utf-8")) if process_launch_path.exists() else {}
            process_identity = process_launch.get("process_identity") if isinstance(process_launch.get("process_identity"), Mapping) else {}
            raw_process_id = process_launch.get("pid") or process_identity.get("pid")
            process_id = int(raw_process_id) if raw_process_id else None
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            process_id = None
        process_alive = bool(self.store.lock_path.exists() and self._pid_alive(process_id))
        validation_record = invocation or {}
        validation_states = {
            OrchestratorState.AI_OUTPUT_VALIDATING.value,
            OrchestratorState.AI_BATCH_INGESTING.value,
        }
        validation_last_activity = validation_record.get("validation_last_activity_at")
        validation_finished = validation_record.get("validation_finished_at")
        validation_stale = False
        if state in validation_states and not validation_finished:
            last_activity = self._manual_result_timestamp({"created_at": validation_last_activity})
            checkpoint_time = self._manual_result_timestamp({"created_at": source_generated_at})
            reference = last_activity or checkpoint_time
            validation_stale = not process_alive or (reference is not None and (datetime.now(timezone.utc) - reference).total_seconds() > AI_BATCH_VALIDATION_STALE_AFTER_SECONDS)
        validation = {
            "started_at": validation_record.get("validation_started_at"),
            "last_activity_at": validation_last_activity,
            "finished_at": validation_finished,
            "stage": validation_record.get("validation_stage"),
            "error": validation_record.get("validation_error") or validation_record.get("error_code"),
            "status": validation_record.get("validation_status") or "NOT_RUN",
            "timeout_seconds": AI_BATCH_VALIDATION_TIMEOUT_SECONDS,
            "stale_after_seconds": AI_BATCH_VALIDATION_STALE_AFTER_SECONDS,
            "stale": validation_stale,
            "process_alive": process_alive,
            "process_id": process_id,
        }
        freshness_state = "UNKNOWN"
        if source_generated_at:
            try:
                generated = datetime.fromisoformat(source_generated_at.replace("Z", "+00:00"))
                if generated.tzinfo is None:
                    generated = generated.replace(tzinfo=timezone.utc)
                freshness_state = "STALE" if (datetime.now(timezone.utc) - generated).total_seconds() > 180 else "FRESH"
            except ValueError:
                freshness_state = "UNKNOWN"
        return ResearchOrchestratorStatusView(
            objective_id=self.objective_id,
            orchestrator_state=projected_state,
            daemon_state=snapshot.daemon_state,
            daemon_status_zh=ZhCNPresentation.state_name(snapshot.daemon_state),
            ai_status=ai_status,
            current_round=None,
            current_candidate=current_candidate,
            current_trial=current_trial,
            ai_running=ai_running,
            last_ai_invocation=invocation,
            current_handoff=handoff,
            closeout_complete=bool(self.store.closeout_path.exists()),
            terminal_reason=str(self.checkpoint.get("terminal_reason") or snapshot.terminal_reason) if (self.checkpoint.get("terminal_reason") or snapshot.terminal_reason) else None,
            waiting_for_governance=(
                predictive_authorization_pending
                or projected_state == OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value
            ),
            next_action=next_action,
            budget=snapshot.budget,
            resource_state=snapshot.resource_state,
            research_counts=snapshot.research_counts,
            ai_auto_invocation_enabled=self.config.ai_auto_invocation,
            ai_invocation_mode=self.config.ai_invocation_mode,
            ai_invocation_mode_zh=mode_labels[self.config.ai_invocation_mode],
            manual_handoff=manual_handoff,
            background_ai_token_consumption=0 if self.config.ai_invocation_mode != "AUTO_CODEX" else int(((invocation or {}).get("usage") or {}).get("total_tokens", 0) or 0),
            retry_state=retry_state,
            current_handoff_id=str(current_handoff_id) if current_handoff_id else None,
            current_ai_invocation_id=str(current_ai_invocation_id) if current_ai_invocation_id else None,
            closeout_state=closeout_state,
            governance_decision_state=governance_decision_state,
            last_transition=self.checkpoint.get("last_transition"),
            source_generated_at=source_generated_at,
            observed_at=observed_at,
            freshness_state=freshness_state,
            validation=validation,
        )

    def request(self, action: str) -> dict[str, Any]:
        action = str(action).upper()
        if action not in {"PAUSE", "STOP"}:
            raise ValueError("unsupported orchestrator control action")
        payload = {"action": action, "requested_at": now_timestamp(), "requested_by_pid": os.getpid()}
        self.store.atomic_write(self.store.control_path, payload)
        return payload

    def resume(self) -> dict[str, Any]:
        state = OrchestratorState(str(self.checkpoint["state"]))
        if state == OrchestratorState.PAUSED:
            self._set_state(OrchestratorState.RECOVER, "RESUME_REQUESTED")
            self._recover()
        return self.status().to_dict()

    def _process_running(self) -> bool:
        from .orchestrator_launcher import OrchestratorProcessLauncherV1

        return OrchestratorProcessLauncherV1(self.root).live_pid(self.objective_id) is not None

    def recover(self) -> dict[str, Any]:
        state = OrchestratorState(str(self.checkpoint["state"]))
        invocation = self.store.load_invocation()
        if state == OrchestratorState.GOVERNANCE_DECISION_REQUIRED:
            snapshot = self.runtime.snapshot()
            invalidations = load_effective_contract_invalidations(self.root, self.objective_id)
            if self._one_shot_policy_active() and invalidations and str((invocation or {}).get("status")) == "ACCEPTED" and not snapshot.has_legal_candidate:
                self._ensure_invalidated_one_shot_governance(snapshot, invalidations)
        snapshot = self.runtime.snapshot() if state in {OrchestratorState.LOCAL_RESEARCH_RESUMING, OrchestratorState.NO_PROGRESS_RESEARCH_LOOP} else None
        candidate_replay_is_invisible = (
            state in {OrchestratorState.LOCAL_RESEARCH_RESUMING, OrchestratorState.NO_PROGRESS_RESEARCH_LOOP}
            and invocation is not None
            and str(invocation.get("status")) == "ACCEPTED"
            and not snapshot.has_legal_candidate
        )
        accepted_batch_can_resume = state == OrchestratorState.NO_PROGRESS_RESEARCH_LOOP and invocation is not None and str(invocation.get("status")) == "ACCEPTED" and snapshot.has_legal_candidate
        legacy_one_shot_recovery = (
            state == OrchestratorState.ACTIVE
            and not self._process_running()
            and self._one_shot_terminal_trial_complete(self.runtime.snapshot())
        )
        if legacy_one_shot_recovery:
            self._set_state(OrchestratorState.RECOVER, "LEGACY_ONE_SHOT_RECOVERY_REQUESTED", previous_state=state.value)
        elif state in {OrchestratorState.AI_HANDOFF_BLOCKED, OrchestratorState.AI_INVOCATION_UNAVAILABLE} or candidate_replay_is_invisible or (state == OrchestratorState.NO_PROGRESS_RESEARCH_LOOP and not accepted_batch_can_resume):
            old_handoff = self._read_handoff()
            old_invocation = invocation
            if old_handoff:
                self.store.atomic_write(self.store.run_dir / "handoff_history" / f"{old_handoff['handoff_id']}.json", old_handoff)
            if old_invocation:
                self.store.atomic_write(self.store.run_dir / "invocation_history" / f"{old_invocation['ai_invocation_id']}.json", old_invocation)
                if candidate_replay_is_invisible:
                    old_invocation = {**old_invocation, "status": "FAILED", "error_code": "AI_BATCH_CANONICAL_CANDIDATE_NOT_VISIBLE", "retryable": True, "next_retry_at": None, "completed_at": now_timestamp()}
                    self.store.atomic_write(self.store.invocation_path, old_invocation)
            self.checkpoint["handoff_generation"] = int(self.checkpoint.get("handoff_generation", 0) or 0) + 1
            self.checkpoint["current_handoff_id"] = None
            self.checkpoint["current_ai_invocation_id"] = None
            reason = "AI_CANDIDATE_REPLAY_REPAIR_REQUESTED" if candidate_replay_is_invisible else "AI_INVOCATION_RETRY_REQUESTED"
            self._set_state(OrchestratorState.RECOVER, reason, previous_state=state.value, handoff_generation=self.checkpoint["handoff_generation"])
        elif accepted_batch_can_resume:
            self._set_state(OrchestratorState.RECOVER, "NO_PROGRESS_RECOVERY_REQUESTED", previous_state=state.value)
        self._recover()
        return self.status().to_dict()

    def governance_status(self) -> dict[str, Any]:
        if not self.store.governance_path.exists():
            return {"status": "NOT_REQUIRED", "orchestrator": self.status().to_dict()}
        return json.loads(self.store.governance_path.read_text(encoding="utf-8"))


class OrchestratorOperationError(RuntimeError):
    def __init__(self, code: str, message_zh: str, *, status_code: int = 409):
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh
        self.status_code = status_code


class OrchestratorControlServiceV1:
    """Narrow web control adapter over the canonical Orchestrator V2 service."""

    _mutex = threading.Lock()
    _active_states = frozenset({
        OrchestratorState.ACTIVE.value,
        OrchestratorState.LOCAL_RESEARCH_RUNNING.value,
        OrchestratorState.NEED_AI_RESEARCH_DESIGN.value,
        OrchestratorState.AI_HANDOFF_PREPARING.value,
        OrchestratorState.AI_INVOCATION_PENDING.value,
        OrchestratorState.AI_INVOCATION_RUNNING.value,
        OrchestratorState.AI_OUTPUT_VALIDATING.value,
        OrchestratorState.AI_BATCH_INGESTING.value,
        OrchestratorState.LOCAL_RESEARCH_RESUMING.value,
        OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED.value,
    })
    _ai_start_states = frozenset({
        OrchestratorState.NEED_AI_RESEARCH_DESIGN.value,
        OrchestratorState.AI_HANDOFF_PREPARING.value,
        OrchestratorState.AI_INVOCATION_PENDING.value,
        OrchestratorState.AI_INVOCATION_RUNNING.value,
    })
    _terminal_states = frozenset({
        OrchestratorState.TERMINAL_CLOSEOUT_PENDING.value,
        OrchestratorState.TERMINAL_CLOSEOUT_RUNNING.value,
        OrchestratorState.TERMINAL_CLOSEOUT_COMPLETE.value,
        OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value,
        OrchestratorState.RESEARCH_PASSED.value,
        OrchestratorState.BUDGET_EXHAUSTED.value,
        OrchestratorState.GLOBAL_SEARCH_EXHAUSTED.value,
        OrchestratorState.SHUTDOWN.value,
    })
    _request_key_pattern = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _orchestrator(self, objective_id: str) -> AutonomousResearchOrchestratorV2:
        return AutonomousResearchOrchestratorV2(self.root, objective_id=objective_id)

    @staticmethod
    def _receipt_path(orchestrator: AutonomousResearchOrchestratorV2) -> Path:
        return orchestrator.store.run_dir / "web_operation_receipts.json"

    def _receipts(self, orchestrator: AutonomousResearchOrchestratorV2) -> dict[str, Any]:
        path = self._receipt_path(orchestrator)
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OrchestratorOperationError("OPERATION_RECEIPTS_UNREADABLE", "操作记录暂时不可读", status_code=503) from exc
        return dict(value) if isinstance(value, Mapping) else {}

    def _save_receipt(self, orchestrator: AutonomousResearchOrchestratorV2, key: str, receipt: Mapping[str, Any]) -> None:
        receipts = self._receipts(orchestrator)
        receipts[key] = dict(receipt)
        orchestrator.store.atomic_write(self._receipt_path(orchestrator), receipts)

    @staticmethod
    def _activation_execution_id(orchestrator: AutonomousResearchOrchestratorV2) -> str:
        path = orchestrator.store.run_dir / "process_launch.json"
        if not path.exists():
            raise OrchestratorOperationError("ORCHESTRATOR_ACTIVATION_RECORD_MISSING", "当前研究没有可复用的 Orchestrator 启动记录。", status_code=503)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OrchestratorOperationError("ORCHESTRATOR_ACTIVATION_RECORD_UNREADABLE", "当前研究的 Orchestrator 启动记录暂时不可读。", status_code=503) from exc
        execution_id = str(payload.get("execution_id") or "") if isinstance(payload, Mapping) else ""
        if not re.fullmatch(r"^[A-Za-z0-9._:-]{1,160}$", execution_id):
            raise OrchestratorOperationError("ORCHESTRATOR_ACTIVATION_RECORD_INVALID", "当前研究的 Orchestrator 启动记录无有效执行编号。", status_code=503)
        return execution_id

    def _ai_start_gate(self, orchestrator: AutonomousResearchOrchestratorV2, state: str) -> tuple[bool, str]:
        if state not in self._ai_start_states:
            return False, "只有处于 AI 研究设计流程中的目标才能启动当前 AI 调用。"
        mode = str(getattr(getattr(orchestrator, "config", None), "ai_invocation_mode", "AUTO_CODEX"))
        if mode != "AUTO_CODEX":
            return False, "当前为手动 AI 交接模式；请复制任务提示词并等待结果文件，不启动后台 AI 调用。"
        try:
            self._activation_execution_id(orchestrator)
        except OrchestratorOperationError as exc:
            return False, exc.message_zh
        invocation = orchestrator.store.load_invocation()
        if state == OrchestratorState.AI_INVOCATION_PENDING.value and invocation:
            retry_at = str(invocation.get("next_retry_at") or "")
            if retry_at:
                try:
                    if datetime.fromisoformat(retry_at.replace("Z", "+00:00")).timestamp() > time.time():
                        return False, "上一次 AI 调用已登记重试时间，请等待该时间后再启动。"
                except ValueError:
                    pass
        return True, "启动当前 AI 研究调用；不会新建研究目标、预算或 Trial。"

    @staticmethod
    def _process_running(orchestrator: AutonomousResearchOrchestratorV2) -> bool:
        from .orchestrator_launcher import OrchestratorProcessLauncherV1

        return OrchestratorProcessLauncherV1(orchestrator.root).live_pid(orchestrator.objective_id) is not None

    def operations(self, objective_id: str) -> dict[str, Any]:
        orchestrator = self._orchestrator(objective_id)
        status = orchestrator.status().to_dict()
        state = str(status["orchestrator_state"])
        terminal = state in self._terminal_states or bool(status.get("closeout_complete"))
        process_running = self._process_running(orchestrator)
        can_pause = state in self._active_states and process_running
        can_resume = state == OrchestratorState.PAUSED.value
        can_stop = state in self._active_states and process_running
        can_start_ai, start_ai_description = self._ai_start_gate(orchestrator, state)
        if process_running and state in self._ai_start_states:
            can_start_ai = False
            start_ai_description = "后台 Orchestrator 正在运行，当前无需重复启动 AI 调用。"
        legacy_one_shot_recovery = (
            state == OrchestratorState.ACTIVE.value
            and not process_running
            and orchestrator._one_shot_terminal_trial_complete(orchestrator.runtime.snapshot())
        )
        can_recover = state in {
            OrchestratorState.RECOVER.value,
            OrchestratorState.SAFETY_STOP.value,
            OrchestratorState.ENGINEERING_BLOCKED.value,
            OrchestratorState.AI_HANDOFF_BLOCKED.value,
            OrchestratorState.AI_INVOCATION_UNAVAILABLE.value,
            OrchestratorState.NO_PROGRESS_RESEARCH_LOOP.value,
        } or legacy_one_shot_recovery
        last_invocation = status.get("last_ai_invocation") if isinstance(status.get("last_ai_invocation"), Mapping) else {}
        one_shot_candidate_complete = (
            is_one_shot_followup(self.root, objective_id)
            and str(last_invocation.get("status") or "") == "ACCEPTED"
            and not status.get("current_candidate")
            and str(status.get("terminal_reason") or "") == "READY_FOR_NEXT_CANDIDATE"
        )
        if one_shot_candidate_complete:
            can_recover = False
        terminal_reason = status.get("terminal_reason_zh") or ("当前研究轮次已经结束，不能重新启动旧目标。" if terminal else "新目标只能通过治理流程创建。")
        pause_reason = "后台研究编排器当前未运行，暂停命令不会推进研究；请先处理 AI 研究调用。"
        resume_reason = "仅在研究已暂停时恢复运行。"
        stop_reason = terminal_reason if terminal else "后台研究编排器当前未运行，无需提交停止命令。"
        recover_reason = "ONE_SHOT 候选已经完成处理；恢复检查不会重跑该候选或创建新候选。" if (one_shot_candidate_complete or legacy_one_shot_recovery) else terminal_reason if terminal else "当前权威状态不需要恢复检查。"
        actions = [
            {"action": "STATUS", "label_zh": "查看状态", "available": True, "confirmation_required": False, "requires_confirmation": False, "description_zh": "只读取研究编排器和研究守护进程的权威状态。"},
            {"action": "PAUSE", "label_zh": "暂停自主研究", "available": can_pause, "confirmation_required": False, "requires_confirmation": False, "description_zh": "安全保存当前状态，稍后可以继续。" if can_pause else pause_reason},
            {"action": "RESUME", "label_zh": "恢复自主研究", "available": can_resume, "confirmation_required": True, "requires_confirmation": True, "description_zh": "仅在权威状态允许时恢复，不会重新打开已消费的预测试验。" if can_resume else resume_reason},
            {"action": "STOP", "label_zh": "停止后台研究", "available": can_stop, "confirmation_required": True, "requires_confirmation": True, "description_zh": "停止当前后台进程，但不会删除研究记录。" if can_stop else stop_reason},
            {"action": "RECOVER", "label_zh": "恢复检查", "available": can_recover, "confirmation_required": True, "requires_confirmation": True, "description_zh": "检查异常退出前保存的状态，并遵循防重复执行保护规则恢复。" if can_recover else recover_reason},
            {"action": "START_AI", "label_zh": "启动 AI 研究调用", "available": can_start_ai and not terminal, "confirmation_required": True, "requires_confirmation": True, "description_zh": start_ai_description if can_start_ai and not terminal else terminal_reason},
        ]
        return {
            "schema_version": "research-orchestrator-operations-view-v1",
            "objective_id": objective_id,
            "orchestrator_state": state,
            "orchestrator_state_zh": status.get("orchestrator_state_zh"),
            "state": state,
            "state_zh": status.get("orchestrator_state_zh"),
            "terminal": terminal,
            "terminal_objective": terminal,
            "terminal_reason": status.get("terminal_reason"),
            "terminal_reason_zh": terminal_reason if terminal else None,
            "actions": actions,
            "status": status,
            "localhost_only": True,
            "shell_execution_exposed": False,
            "source_generated_at": status.get("source_generated_at"),
            "observed_at": status.get("observed_at"),
        }

    def execute(self, objective_id: str, action: str, *, idempotency_key: str, confirmed: bool = False) -> dict[str, Any]:
        action = str(action).upper()
        idempotency_key = str(idempotency_key)
        if action not in {"PAUSE", "RESUME", "STOP", "RECOVER", "START_AI"}:
            raise OrchestratorOperationError("OPERATION_NOT_SUPPORTED", "当前页面不支持该操作", status_code=400)
        if not self._request_key_pattern.fullmatch(idempotency_key):
            raise OrchestratorOperationError("INVALID_IDEMPOTENCY_KEY", "操作请求编号不合法", status_code=400)
        with self._mutex:
            orchestrator = self._orchestrator(objective_id)
            receipt = self._receipts(orchestrator).get(idempotency_key)
            if receipt is not None:
                current = orchestrator.status().to_dict()
                return {**dict(receipt), "idempotent": True, "orchestrator": current}
            before = orchestrator.status().to_dict()
            state = str(before["orchestrator_state"])
            if action in {"RESUME", "STOP", "RECOVER", "START_AI"} and not confirmed:
                raise OrchestratorOperationError("CONFIRMATION_REQUIRED", "该操作需要明确确认", status_code=400)
            if action == "PAUSE":
                if state == OrchestratorState.PAUSED.value:
                    result = before
                    acknowledged = True
                    message = "研究已经处于暂停状态。"
                elif state not in self._active_states:
                    raise OrchestratorOperationError("TERMINAL_OPERATION_BLOCKED" if state in self._terminal_states else "OPERATION_NOT_ALLOWED", "当前权威状态不允许暂停。")
                else:
                    orchestrator.request("PAUSE")
                    result = orchestrator.status().to_dict()
                    acknowledged = False
                    message = "暂停命令已提交，等待编排器在安全边界确认。"
            elif action == "STOP":
                if state == OrchestratorState.SHUTDOWN.value:
                    result = before
                    acknowledged = True
                    message = "后台研究已经停止。"
                elif state not in self._active_states:
                    raise OrchestratorOperationError("TERMINAL_OPERATION_BLOCKED" if state in self._terminal_states else "OPERATION_NOT_ALLOWED", "当前权威状态不允许停止。")
                else:
                    orchestrator.request("STOP")
                    result = orchestrator.status().to_dict()
                    acknowledged = False
                    message = "停止命令已提交，等待编排器确认。研究记录不会被删除。"
            elif action == "RESUME":
                if state in self._terminal_states:
                    raise OrchestratorOperationError("TERMINAL_RESUME_BLOCKED", "本轮研究已经结束，不能恢复旧目标。")
                if state != OrchestratorState.PAUSED.value:
                    raise OrchestratorOperationError("RESUME_NOT_ALLOWED", "当前权威状态不允许恢复。")
                result = orchestrator.resume()
                acknowledged = str(result.get("orchestrator_state")) != state
                message = "恢复命令已由 canonical 编排器处理。"
            elif action == "RECOVER":
                legacy_one_shot_recovery = (
                    state == OrchestratorState.ACTIVE.value
                    and not self._process_running(orchestrator)
                    and orchestrator._one_shot_terminal_trial_complete(orchestrator.runtime.snapshot())
                )
                if state in self._terminal_states:
                    result = before
                    acknowledged = True
                    message = "当前研究已在安全边界结束，无需恢复检查。"
                elif state not in {
                    OrchestratorState.RECOVER.value,
                    OrchestratorState.SAFETY_STOP.value,
                    OrchestratorState.ENGINEERING_BLOCKED.value,
                    OrchestratorState.AI_HANDOFF_BLOCKED.value,
                    OrchestratorState.AI_INVOCATION_UNAVAILABLE.value,
                    OrchestratorState.NO_PROGRESS_RESEARCH_LOOP.value,
                } and not legacy_one_shot_recovery:
                    raise OrchestratorOperationError("RECOVER_NOT_ALLOWED", "当前权威状态不需要恢复检查。")
                else:
                    result = orchestrator.recover()
                    acknowledged = True
                    message = "恢复检查已由 canonical 编排器处理。"
            else:
                can_start_ai, start_description = self._ai_start_gate(orchestrator, state)
                if self._process_running(orchestrator):
                    can_start_ai = False
                    start_description = "后台 Orchestrator 正在运行，当前无需重复启动 AI 调用。"
                if not can_start_ai or state in self._terminal_states:
                    raise OrchestratorOperationError("START_AI_NOT_ALLOWED", start_description)
                from .orchestrator_launcher import OrchestratorLaunchError, OrchestratorProcessLauncherV1

                try:
                    activation = OrchestratorProcessLauncherV1(self.root).start(
                        objective_id,
                        execution_id=self._activation_execution_id(orchestrator),
                    )
                except OrchestratorLaunchError as exc:
                    raise OrchestratorOperationError(exc.code, exc.message_zh, status_code=503) from exc
                result = orchestrator.status().to_dict()
                acknowledged = str(activation.get("status")) == "ALREADY_RUNNING"
                message = "当前 AI 研究调用已在后台运行。" if acknowledged else "AI 研究调用已从页面启动，等待后台编排器更新状态。"
            response = {
                "schema_version": "research-orchestrator-operation-result-v1",
                "objective_id": objective_id,
                "action": action,
                "status": "ACCEPTED",
                "acknowledged": acknowledged,
                "idempotent": False,
                "message_zh": message,
                "before_state": state,
                "orchestrator": result,
            }
            if action == "START_AI":
                response["orchestrator_activation"] = activation
            self._save_receipt(orchestrator, idempotency_key, {key: value for key, value in response.items() if key != "orchestrator"})
            return response


class GovernanceDecisionServiceV1:
    """Record a human governance decision without creating a new objective."""

    _mutex = threading.Lock()
    _request_key_pattern = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def submit(self, objective_id: str, choice: str, *, idempotency_key: str) -> dict[str, Any]:
        choice = str(choice).upper()
        idempotency_key = str(idempotency_key)
        if not self._request_key_pattern.fullmatch(idempotency_key):
            raise OrchestratorOperationError("INVALID_IDEMPOTENCY_KEY", "治理请求编号不合法", status_code=400)
        with self._mutex:
            structural_path = self.root / "reports/research_orchestrator_v2" / objective_id / "structural_governance_decision_required.json"
            if structural_path.is_file():
                try:
                    structural_artifact = json.loads(structural_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise OrchestratorOperationError("PREDICTIVE_GOVERNANCE_SOURCE_UNREADABLE", "结构 PASS 后预测治理状态暂时不可读", status_code=503) from exc
                if isinstance(structural_artifact, Mapping) and str(structural_artifact.get("decision_mode") or "") == "STRUCTURAL_PASS_PREDICTIVE_AUTHORIZATION_REQUIRED":
                    from .predictive_authorization import PredictiveGovernanceError, PredictiveGovernanceServiceV1

                    try:
                        predictive_governance = PredictiveGovernanceServiceV1(self.root)
                        preview = predictive_governance.preview(objective_id, choice)
                        recorded = predictive_governance.confirm(
                            objective_id,
                            {
                                "confirmed": True,
                                "decision_type": preview["decision_type"],
                                "candidate_id": preview["candidate_id"],
                                "candidate_hash": preview["candidate_hash"],
                                "authorization_id": idempotency_key,
                                "preview_hash": preview["preview_hash"],
                                "confirmation_token": preview["confirmation_token"],
                            },
                        )
                    except PredictiveGovernanceError as exc:
                        raise OrchestratorOperationError(exc.code, exc.message_zh, status_code=exc.status_code, details=exc.details) from exc
                    return {**recorded, "idempotency_key": idempotency_key, "message_zh": recorded.get("message_zh") or "结构 PASS 后治理决定已记录。"}
            orchestrator = AutonomousResearchOrchestratorV2(self.root, objective_id=objective_id)
            status = orchestrator.status().to_dict()
            if status.get("orchestrator_state") != OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value:
                raise OrchestratorOperationError("GOVERNANCE_NOT_REQUIRED", "当前研究状态不需要提交治理决定。")
            try:
                artifact = json.loads(orchestrator.store.governance_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise OrchestratorOperationError("GOVERNANCE_ARTIFACT_UNREADABLE", "治理决定 artifact 暂时不可读", status_code=503) from exc
            allowed = artifact.get("allowed_choices") if isinstance(artifact.get("allowed_choices"), list) else []
            allowed_choices = {str(item.get("choice")) for item in allowed if isinstance(item, Mapping) and item.get("choice")}
            if choice not in allowed_choices:
                raise OrchestratorOperationError("GOVERNANCE_CHOICE_NOT_ALLOWED", "该治理选择不在 canonical 允许范围内", status_code=400)
            submission_path = orchestrator.store.run_dir / "governance_decision_submission.json"
            if submission_path.exists():
                try:
                    existing = json.loads(submission_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise OrchestratorOperationError("GOVERNANCE_SUBMISSION_UNREADABLE", "已有治理记录暂时不可读", status_code=503) from exc
                if str(existing.get("idempotency_key")) == idempotency_key:
                    return {**existing, "idempotent": True, "message_zh": "治理决定已经记录。"}
                raise OrchestratorOperationError("GOVERNANCE_ALREADY_RECORDED", "当前研究已经记录治理决定，不能覆盖已有决定。")
            choice_consequence = next((item.get("consequence") for item in allowed if isinstance(item, Mapping) and str(item.get("choice")) == choice), "")
            submission = {
                "schema_version": "research-governance-decision-submission-v1",
                "submission_id": f"GOVERNANCE_SUBMISSION_{stable_hash({'decision_id': artifact.get('decision_id'), 'choice': choice})[:20]}",
                "decision_id": artifact.get("decision_id"),
                "objective_id": objective_id,
                "choice": choice,
                "consequence": choice_consequence,
                "status": "RECORDED",
                "execution_level": "C",
                "next_objective_created": False,
                "new_predictive_budget_created": False,
                "new_budget_required": choice != "STOP_RESEARCH",
                "final_test_access": {"analytical": 0, "decision": 0, "physical": 0},
                "prospective": "DISABLED",
                "real_order": "DISABLED",
                "idempotency_key": idempotency_key,
                "created_at": now_timestamp(),
            }
            PerformanceBlindGuard.assert_blind(submission)
            orchestrator.store.atomic_write(submission_path, submission)
            orchestrator.store.append_event("GOVERNANCE_DECISION_RECORDED", {"objective_id": objective_id, "decision_id": artifact.get("decision_id"), "choice": choice, "submission_id": submission["submission_id"]}, event_id=submission["submission_id"])
            return {**submission, "idempotent": False, "message_zh": "治理决定已记录，下一研究目标尚未创建。"}


__all__ = [
    "AI_ALLOWED_TRIGGERS", "AI_DENIED_TRIGGERS", "AIBatchValidationError", "AIBatchValidatorV2", "AIInvocationError", "AutonomousResearchOrchestratorV2", "CanonicalOrchestratorRuntimeV2", "CanonicalResearchSnapshotV2", "CanonicalResearchStateReaderV2", "CodexExecBatchInvokerV2", "GovernanceDecisionServiceV1", "OrchestratorConfigV2", "OrchestratorControlServiceV1", "OrchestratorOperationError", "OrchestratorState", "ResearchOrchestratorStatusView", "SyntheticAutonomousResearchRuntimeV2", "SyntheticCodexBatchInvokerV2", "TerminalCloseoutServiceV2", "build_codex_prompt_v2",
]
