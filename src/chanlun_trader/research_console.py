"""Canonical, read-only research console boundary.

This module deliberately owns projection and source reconciliation only.  It
does not advance daemon state, reserve budget, access performance, or repair
any canonical artifact.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping

from .presentation import (
    display_action,
    display_classification,
    display_reason,
    display_state,
    display_status,
    format_reason,
)
from .research_factory.budget import SearchBudgetRegistryV1
from .research_factory.context import PerformanceBlindGuard, PerformanceLeakError
from .research_factory.durability import canonical_frozen_contract_identity_hash
from .research_factory.contract_correction import CORRECTION_FILENAME, load_effective_contract_invalidations
from .research_factory.artifact_graph import ResearchArtifactGraphV1
from .research_factory.failure_adapter import FailureKnowledgeSnapshotV1
from .research_factory.predictive_authorization import PredictiveGovernanceError, PredictiveGovernanceServiceV1
from .research_factory.predictive_trial_start import PredictiveTrialStartError, PredictiveTrialStartServiceV1
from .research_factory.research_evolution_ai_design import ResearchEvolutionAIDesignError, ResearchEvolutionAIDesignServiceV1
from .research_factory.candidate_generation import CandidateGenerationError, CandidateGenerationManagerV1
from .research_factory.promising_followup_scope import candidate_scope_info, load_scope_manifest
from .research_factory.research_evolution_proposal import COVERAGE_FILENAME, PROPOSAL_FILENAME
from .research_factory.research_proposal_governance import ResearchProposalGovernanceError, ResearchProposalGovernanceServiceV1


class FreshnessState(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


class ResearchConsoleReadError(RuntimeError):
    """Safe error raised at the browser/read boundary."""

    def __init__(self, code: str, message_zh: str, *, status_code: int = 500, details: Mapping[str, Any] | None = None):
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh
        self.status_code = status_code
        self.details = dict(details or {})

    def envelope(self) -> dict[str, Any]:
        return {"code": self.code, "message_zh": self.message_zh, "details": dict(self.details)}


@dataclass(frozen=True)
class ProvenanceView:
    source_id: str
    source_generated_at: str | None
    observed_at: str
    freshness_state: str
    stale: bool
    conflict: bool
    source_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReadModel:
    provenance: ProvenanceView

    @property
    def source_id(self) -> str:
        return self.provenance.source_id

    @property
    def source_generated_at(self) -> str | None:
        return self.provenance.source_generated_at

    @property
    def observed_at(self) -> str:
        return self.provenance.observed_at

    @property
    def freshness_state(self) -> str:
        return self.provenance.freshness_state

    @property
    def stale(self) -> bool:
        return self.provenance.stale

    @property
    def conflict(self) -> bool:
        return self.provenance.conflict

    def to_dict(self) -> dict[str, Any]:
        result = _jsonable(asdict(self))
        provenance = result.pop("provenance", {})
        result.update(provenance)
        return result


@dataclass(frozen=True)
class ResearchDashboardView(ReadModel):
    objective_id: str = ""
    research_running: bool = False
    daemon_state: str = "UNKNOWN"
    orchestrator_state: str = "UNKNOWN"
    orchestrator_state_zh: str = "未知状态（未提供）"
    stage: str = "UNKNOWN"
    current_candidate_id: str | None = None
    remaining_frozen_candidates: int | None = None
    budget_used: int | None = None
    budget_total: int | None = None
    budget_remaining: int | None = None
    budget_conflict: bool = False
    research_passed_count: int = 0
    promising_count: int = 0
    required_human_action: str | None = None
    engineering_blocked: bool = False
    resource_health: str = "UNKNOWN"
    shadow_latest_state: str = "UNKNOWN"
    data_health_state: str = "UNKNOWN"
    predictive_trial_count: int = 0
    safety_counters: Mapping[str, Any] = field(default_factory=dict)
    display: Mapping[str, Any] = field(default_factory=dict)
    structural: Mapping[str, Any] = field(default_factory=dict)
    governance_readiness: Mapping[str, Any] = field(default_factory=dict)
    predictive_trial_recovery: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResearchStatusView(ReadModel):
    objective_id: str = ""
    objective_hash: str = ""
    research_state: str = "UNKNOWN"
    stage: str = "UNKNOWN"
    counts: Mapping[str, int] = field(default_factory=dict)
    safety_counters: Mapping[str, Any] = field(default_factory=dict)
    display: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DaemonStatusView(ReadModel):
    objective_id: str = ""
    daemon_state: str = "UNKNOWN"
    stage: str = "UNKNOWN"
    current_candidate_id: str | None = None
    remaining_frozen_candidates: int | None = None
    required_human_ai_action: str | None = None
    process_pid: int | None = None
    process_rss_bytes: int | None = None
    system_available_memory_bytes: int | None = None
    last_checkpoint_time: str | None = None
    last_error: str | None = None
    retry_safe: bool = True
    daemon_run_id: str | None = None
    state_display_zh: str = "未知状态（未提供）"
    action_display_zh: str = "无"
    budget: Mapping[str, Any] = field(default_factory=dict)
    handoff_stale: bool = False
    handoff_conflict: bool = False


@dataclass(frozen=True)
class DaemonHealthView(ReadModel):
    objective_id: str = ""
    health_state: str = "UNKNOWN"
    health_display_zh: str = "未知状态（未提供）"
    daemon_state: str = "UNKNOWN"
    process_alive_observed: bool | None = None
    process_pid: int | None = None
    process_rss_bytes: int | None = None
    system_available_memory_bytes: int | None = None
    checkpoint_age_seconds: int | None = None
    telemetry_tail: tuple[Mapping[str, Any], ...] = ()
    last_error: str | None = None


@dataclass(frozen=True)
class ResearchPipelineView(ReadModel):
    objective_id: str = ""
    current_stage: str = "UNKNOWN"
    current_state: str = "UNKNOWN"
    current_state_zh: str = "未知状态（未提供）"
    next_action: str = "UNKNOWN"
    next_action_zh: str = "未知状态（未提供）"
    current_candidate_id: str | None = None
    remaining_frozen_candidates: int | None = None
    last_error: str | None = None
    state_source: str = "UNKNOWN"
    stages: tuple[Mapping[str, Any], ...] = ()
    batch_ids: tuple[str, ...] = ()
    candidate_count: int = 0
    trial_count: int = 0
    recent_events: tuple[Mapping[str, Any], ...] = ()
    execution: Mapping[str, Any] = field(default_factory=dict)
    structural_reconciliation: Mapping[str, Any] = field(default_factory=dict)
    predictive_authorization: Mapping[str, Any] = field(default_factory=dict)
    predictive_trial_start: Mapping[str, Any] = field(default_factory=dict)
    predictive_trial_recovery: Mapping[str, Any] = field(default_factory=dict)
    governance_readiness: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResearchObjectiveSummaryView:
    objective_id: str = ""
    objective_name: str | None = None
    lifecycle_state: str = "UNKNOWN"
    lifecycle_state_zh: str = "未知状态（未提供）"
    orchestrator_state: str = "UNKNOWN"
    orchestrator_state_zh: str = "未知状态（未提供）"
    state_source: str = "UNKNOWN"
    orchestrator_source_available: bool = False
    orchestrator_error_code: str | None = None
    orchestrator_error_message_zh: str | None = None
    orchestrator_source_generated_at: str | None = None
    governance_pending: bool = False
    terminal: bool = False
    created_at: str | None = None
    updated_at: str | None = None
    max_total_trials: int | None = None
    max_batches: int | None = None


@dataclass(frozen=True)
class ResearchObjectiveListView(ReadModel):
    objectives: tuple[ResearchObjectiveSummaryView, ...] = ()


@dataclass(frozen=True)
class CandidateSummaryView(ReadModel):
    candidate_id: str = ""
    candidate_hash: str = ""
    explanation_zh: str = ""
    mechanism_family: str = ""
    frozen_state: str = "UNKNOWN"
    pipeline_state: str = "UNKNOWN"
    structural_status: str = "UNKNOWN"
    predictive_trial_status: str = "NOT_RUN"
    effective_classification: str | None = None
    holding_horizon: int | None = None
    factor_ids: tuple[str, ...] = ()
    created_batch_id: str | None = None
    created_run_id: str | None = None
    budget_consumed: bool = False
    reasons: tuple[Mapping[str, Any], ...] = ()
    scope_status: str = "CURRENT_FOLLOWUP_CONFIRMATION"
    scope_status_zh: str = "当前 Follow-up 确认候选"
    scope_reason_zh: str = "该 Candidate 可在当前 Follow-up 范围内继续校验。"
    is_current_followup: bool = True


@dataclass(frozen=True)
class CandidateDetailView(ReadModel):
    candidate_id: str = ""
    identity: Mapping[str, Any] = field(default_factory=dict)
    semantics: Mapping[str, Any] = field(default_factory=dict)
    structural: Mapping[str, Any] = field(default_factory=dict)
    predictive: Mapping[str, Any] = field(default_factory=dict)
    governance: Mapping[str, Any] = field(default_factory=dict)
    artifact_lineage: Mapping[str, Any] = field(default_factory=dict)
    shadow: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StructuralPreflightView(ReadModel):
    candidate_id: str = ""
    candidate_hash: str = ""
    status: str = "UNKNOWN"
    qualified_observations: int | None = None
    selected_opportunities: int | None = None
    portfolio_feasible_count: int | None = None
    lower_bound: int | None = None
    upper_bound: int | None = None
    minimum_required: int | None = None
    reason_codes: tuple[str, ...] = ()
    provider_completeness: Mapping[str, Any] = field(default_factory=dict)
    research_period: Mapping[str, Any] = field(default_factory=dict)
    pit_status: str = "UNKNOWN"
    execution_feasibility: str = "UNKNOWN"
    outcome_blind: bool = True
    performance_data_loaded: bool = False


@dataclass(frozen=True)
class TrialSummaryView(ReadModel):
    trial_id: str = ""
    objective_id: str = ""
    batch_id: str = ""
    candidate_id: str = ""
    candidate_hash: str = ""
    family_id: str = ""
    status: str = "UNKNOWN"
    lifecycle_state: str = "UNKNOWN"
    registered: bool = False
    reserved: bool = False
    performance_accessed: bool = False
    performance_complete: bool = False
    completed: bool = False
    reconciled: bool = False
    blocked: bool = False
    failed_or_rejected: bool = False
    classification: str | None = None
    reason_codes: tuple[str, ...] = ()
    budget_consumed: bool = False
    budget_reservation_identity: str | None = None
    state_display_zh: str = "未知状态（未提供）"
    classification_display_zh: str | None = None
    stage: str = ""
    created_at: str | None = None
    started_at: str | None = None
    last_activity_at: str | None = None
    finished_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    recovery_status: str | None = None
    start_intent_id: str | None = None


@dataclass(frozen=True)
class TrialDetailView(ReadModel):
    trial_id: str = ""
    summary: Mapping[str, Any] = field(default_factory=dict)
    human_authorized: bool = True
    performance: Mapping[str, Any] = field(default_factory=dict)
    statistical: Mapping[str, Any] = field(default_factory=dict)
    effective_decision: Mapping[str, Any] = field(default_factory=dict)
    artifact_lineage: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchBudgetView(ReadModel):
    objective_id: str = ""
    used: int = 0
    total: int = 0
    remaining: int = 0
    reserved: int = 0
    registry_identity: str = ""
    registry_head_hash: str = ""
    buckets: tuple[Mapping[str, Any], ...] = ()
    trial_consumption: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class ShadowDailyView(ReadModel):
    objective_id: str = ""
    available: bool = False
    objective_scoped: bool = False
    availability_reason_zh: str = "当前研究目标没有可用的 Shadow 数据。"
    trade_date: str | int | None = None
    readiness: str = "UNKNOWN"
    scan_status: str = "UNKNOWN"
    strategies_scanned: int = 0
    raw_signal_count: int = 0
    unique_candidate_count: int = 0
    observation_pool: tuple[Mapping[str, Any], ...] = ()
    pit_state: str = "UNKNOWN"
    tradability_state: str = "UNKNOWN"
    next_legal_session: str | int | None = None
    machine_marker: str = "SHADOW_RESEARCH_ONLY"
    warning_zh: str = "仅供研究观察，不代表买入建议。"


@dataclass(frozen=True)
class DataHealthView(ReadModel):
    objective_id: str = ""
    overall_status: str = "UNKNOWN"
    sources: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class ReportIndexView(ReadModel):
    objective_id: str = ""
    reports: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class ResearchEvolutionView(ReadModel):
    objective_id: str = ""
    available: bool = False
    report: Mapping[str, Any] | None = None
    context: Mapping[str, Any] | None = None
    landscape: Mapping[str, Any] | None = None
    display: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResearchEvolutionProposalView(ReadModel):
    objective_id: str = ""
    available: bool = False
    proposal: Mapping[str, Any] | None = None
    coverage: Mapping[str, Any] | None = None
    proposals: tuple[Mapping[str, Any], ...] = ()
    display: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResearchEvolutionAIDesignView(ReadModel):
    objective_id: str = ""
    available: bool = False
    status: str = "NEED_AI_RESEARCH_DESIGN"
    input: Mapping[str, Any] = field(default_factory=dict)
    design: Mapping[str, Any] | None = None
    governance: Mapping[str, Any] = field(default_factory=dict)
    source_refs: Mapping[str, Any] = field(default_factory=dict)
    display: Mapping[str, Any] = field(default_factory=dict)
    output_path: str | None = None
    outcome_blind: bool = True
    performance_data_loaded: bool = False
    outcome_fields_available: bool = False


@dataclass(frozen=True)
class CandidateProposalView(ReadModel):
    objective_id: str = ""
    available: bool = False
    status: str = "NEED_CANDIDATE_PROPOSAL"
    proposal: Mapping[str, Any] | None = None
    proposals: tuple[Mapping[str, Any], ...] = ()
    governance: Mapping[str, Any] = field(default_factory=dict)
    freeze_preview: Mapping[str, Any] | None = None
    display: Mapping[str, Any] = field(default_factory=dict)
    output_path: str | None = None
    outcome_blind: bool = True
    performance_data_loaded: bool = False
    outcome_fields_available: bool = False


@dataclass(frozen=True)
class GovernanceView(ReadModel):
    objective_id: str = ""
    research_period: Mapping[str, Any] = field(default_factory=dict)
    final_test_boundary: Mapping[str, Any] = field(default_factory=dict)
    sample_feasibility_policy: Mapping[str, Any] = field(default_factory=dict)
    validation_decision_policy: Mapping[str, Any] = field(default_factory=dict)
    search_budget_policy: Mapping[str, Any] = field(default_factory=dict)
    pit_rules: Mapping[str, Any] = field(default_factory=dict)
    execution_contract_version: str | None = None
    final_test_access: Mapping[str, Any] = field(default_factory=dict)
    prospective_state: Any = "DISABLED"
    real_order_state: str = "DISABLED"


@dataclass(frozen=True)
class NoOutcomeHandoffView(ReadModel):
    objective_id: str = ""
    handoff_type: str = "UNKNOWN"
    current_state: str = "UNKNOWN"
    reason_code: str | None = None
    reason_display_zh: str = "未提供原因"
    artifact_refs: tuple[str, ...] = ()
    allowed_next_actions: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    current_candidate: Mapping[str, Any] = field(default_factory=dict)
    remaining_frozen_candidates: int | None = None
    global_search_exhausted: bool = False
    budget: Mapping[str, Any] = field(default_factory=dict)
    performance_values_exposed: bool = False
    outcome_blind: bool = True
    display: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ManualAIHandoffView(ReadModel):
    objective_id: str = ""
    mode: str = "MANUAL_HANDOFF"
    mode_zh: str = "手动 AI 交接（推荐）"
    state: str = "UNKNOWN"
    state_zh: str = "未知状态（未提供）"
    handoff_id: str | None = None
    invocation_id: str | None = None
    task_dir: str | None = None
    prompt_path: str | None = None
    instructions_path: str | None = None
    allowed_info_path: str | None = None
    result_path: str | None = None
    expected_filename: str = "AI_RESEARCH_BATCH_RESULT_V2.json"
    task_created_at: str | None = None
    prompt_character_count: int = 0
    prompt_token_estimate: int = 0
    context_mode: str = "REFERENCES_ONLY"
    result_status: str = "WAITING"
    result_status_zh: str = "等待结果文件"
    result_reason_zh: str = "尚未发现 AI 研究结果文件。"
    background_ai_token_consumption: int = 0
    output_contract: Mapping[str, Any] = field(default_factory=dict)
    allowed_actions_zh: tuple[str, ...] = ()
    forbidden_actions_zh: tuple[str, ...] = ()
    prompt_text: str = ""
    instructions_text: str = ""
    allowed_info: Mapping[str, Any] = field(default_factory=dict)
    validation_status: str = "NOT_RUN"
    validation_status_zh: str = "尚未开始校验"
    validation_stage: str | None = None
    validation_stage_zh: str = "暂无校验步骤"
    validation_started_at: str | None = None
    validation_last_activity_at: str | None = None
    validation_finished_at: str | None = None
    validation_error: str | None = None
    validation_stale: bool = False
    validation_process_alive: bool = False


class AIInvocationModeServiceV1:
    """Read and intentionally change the global AI invocation mode."""

    _allowed = {"MANUAL_HANDOFF", "AUTO_CODEX", "AI_DISABLED"}

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.path = self.root / "config" / "research_ai_invocation_mode.json"

    def get(self) -> dict[str, Any]:
        from .research_factory.autonomous_orchestrator_v2 import OrchestratorConfigV2

        config = OrchestratorConfigV2.from_environment(self.root)
        return {"schema_version": "research-ai-invocation-mode-view-v1", "ai_invocation_mode": config.ai_invocation_mode, "ai_invocation_mode_zh": {"MANUAL_HANDOFF": "手动 AI 交接（推荐）", "AUTO_CODEX": "自动调用 AI 研究员", "AI_DISABLED": "AI 研究已禁用"}[config.ai_invocation_mode], "source": "配置文件" if self.path.exists() else "默认配置或环境变量", "config_path": self.path.relative_to(self.root).as_posix(), "background_ai_token_consumption": 0 if config.ai_invocation_mode != "AUTO_CODEX" else None}

    def set(self, mode: str) -> dict[str, Any]:
        mode = str(mode).strip().upper()
        if mode not in self._allowed:
            raise ResearchConsoleReadError("INVALID_AI_INVOCATION_MODE", "AI 研究调用模式不受支持", status_code=400)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps({"schema_version": "research-ai-invocation-mode-config-v1", "ai_invocation_mode": mode, "updated_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, self.path)
        return {**self.get(), "message_zh": "AI 研究调用模式已保存；正在运行的旧任务不会被强行中断，新一轮安全边界会使用该模式。"}


def _closeout_candidate_name(candidate_id: str) -> str:
    if candidate_id.startswith("CAND_EVENT_CONTINUATION"):
        return "事件延续 · 情绪延续"
    if candidate_id.startswith("CAND_EVENT_REVERSAL"):
        return "事件反转 · 情绪衰竭反转"
    return "候选策略"


@dataclass(frozen=True)
class _CacheEntry:
    signature: Any
    expires_at: float
    value: Any


class BoundedReadCache:
    """Small signature/TTL cache used by read paths; never grows unbounded."""

    def __init__(self, max_entries: int = 128):
        self.max_entries = max(1, int(max_entries))
        self._items: OrderedDict[str, _CacheEntry] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str, signature: Any, loader: Callable[[], Any], *, ttl_seconds: float) -> Any:
        now = datetime.now(timezone.utc).timestamp()
        entry = self._items.get(key)
        if entry is not None and entry.signature == signature and entry.expires_at >= now:
            self.hits += 1
            self._items.move_to_end(key)
            return entry.value
        self.misses += 1
        value = loader()
        self._items[key] = _CacheEntry(signature, now + max(0.0, ttl_seconds), value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)
        return value

    @property
    def size(self) -> int:
        return len(self._items)

    def stats(self) -> dict[str, int]:
        return {"size": self.size, "max_entries": self.max_entries, "hits": self.hits, "misses": self.misses}


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TRIAL_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_PERFORMANCE_KEYS = frozenset({
    "return", "returns", "net_return", "exact_return", "pf", "profit_factor", "dd", "drawdown",
    "max_drawdown", "win_rate", "p_value", "pvalue", "bootstrap_p_value", "adjusted_p", "sharpe",
    "pnl", "realized_pnl", "trade_pnl", "annualized_return", "average_trade_pnl", "average_exposure",
    "closed_trade_count", "trade_count", "bootstrap", "cost_stress", "subperiod_metrics", "regime_metrics",
    "multiple_testing", "small_capital", "concentration", "execution_timing_stress", "base_metrics",
})
_PERFORMANCE_CONTAINER_KEYS = frozenset({
    "base_metrics", "bootstrap", "cost_stress", "subperiod_metrics", "regime_metrics", "multiple_testing",
    "small_capital", "concentration", "execution_timing_stress", "metrics", "performance", "final_decision",
})


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        result = value
    else:
        raw = str(value).strip()
        if len(raw) == 8 and raw.isdigit():
            raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _timestamp_text(value: Any) -> str | None:
    parsed = _parse_timestamp(value)
    return parsed.isoformat() if parsed is not None else (str(value) if value not in (None, "") else None)


def _now_utc(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _source_timestamp(payload: Mapping[str, Any], path: Path) -> datetime:
    for key in ("generated_at", "updated_at", "checkpoint_at", "last_checkpoint_time", "created_at", "timestamp"):
        parsed = _parse_timestamp(payload.get(key))
        if parsed is not None:
            return parsed
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _performance_projection(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
            if normalized in {"evidence_dir", "evidence_path", "raw_trades", "trades", "equity", "fills", "orders", "signals"}:
                continue
            if normalized in _PERFORMANCE_KEYS or normalized in _PERFORMANCE_CONTAINER_KEYS:
                item = _performance_projection(nested, depth=depth + 1)
                if item is not None:
                    projected[str(key)] = item
            elif isinstance(nested, Mapping) and normalized in {"result", "validation", "final", "summary"}:
                item = _performance_projection(nested, depth=depth + 1)
                if item:
                    projected[str(key)] = item
        return projected
    if isinstance(value, (list, tuple)):
        return [_performance_projection(item, depth=depth + 1) for item in list(value)[:20]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


_PIPELINE_STAGES = ("HYPOTHESIS", "CANDIDATE", "FREEZE", "STRUCTURAL", "PREDICTIVE", "STATISTICAL", "FINAL_CLASSIFICATION")
_AI_DESIGN_STATES = {
    "NEED_AI_RESEARCH_DESIGN",
    "AI_HANDOFF_PREPARING",
    "AI_INVOCATION_PENDING",
    "AI_INVOCATION_RUNNING",
}
_AI_BATCH_STATES = {"AI_OUTPUT_VALIDATING", "AI_BATCH_INGESTING", "LOCAL_RESEARCH_RESUMING"}
_TERMINAL_ORCHESTRATOR_STATES = {
    "TERMINAL_CLOSEOUT_PENDING",
    "TERMINAL_CLOSEOUT_RUNNING",
    "TERMINAL_CLOSEOUT_COMPLETE",
    "GOVERNANCE_DECISION_REQUIRED",
    "RESEARCH_PASSED",
    "BUDGET_EXHAUSTED",
    "GLOBAL_SEARCH_EXHAUSTED",
    "SHUTDOWN",
    "SAFETY_STOP",
    "NO_PROGRESS_RESEARCH_LOOP",
}


def _lifecycle_stage(*, orchestrator_state: str, daemon_stage: str, candidate_count: int, trial_count: int) -> str:
    """Map the canonical runtime state onto the human lifecycle step.

    The exact Orchestrator state remains the source of truth.  This mapping is
    only for positioning the lifecycle stepper and must never replace it.
    """

    if orchestrator_state in _AI_DESIGN_STATES:
        return "HYPOTHESIS"
    if orchestrator_state in _AI_BATCH_STATES:
        return "CANDIDATE"
    normalized_daemon_stage = str(daemon_stage or "").upper()
    for stage in reversed(_PIPELINE_STAGES):
        if normalized_daemon_stage == stage or normalized_daemon_stage.startswith(f"{stage}_"):
            return stage
    if trial_count:
        return "PREDICTIVE"
    if candidate_count:
        return "CANDIDATE"
    return "HYPOTHESIS"


class ResearchConsoleReadService:
    """Build objective-scoped, human-safe read models from canonical sources."""

    daemon_ttl_seconds = 180.0
    handoff_ttl_seconds = 300.0
    report_ttl_seconds = 60.0

    def __init__(self, root: str | Path, *, clock: Callable[[], datetime] | None = None, cache_max_entries: int = 128):
        self.root = Path(root).resolve()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.cache = BoundedReadCache(cache_max_entries)
        self.predictive_governance = PredictiveGovernanceServiceV1(self.root)
        self.predictive_trial_start = PredictiveTrialStartServiceV1(self.root, auto_run=False)
        self.evolution_ai_design = ResearchEvolutionAIDesignServiceV1(self.root)
        self.candidate_generation = CandidateGenerationManagerV1(self.root)

    @property
    def cache_stats(self) -> dict[str, int]:
        return self.cache.stats()

    def _observed_at(self) -> str:
        return _now_utc(self._clock).isoformat()

    def _safe_identifier(self, value: str, *, kind: str, pattern: re.Pattern[str] = _IDENTIFIER_RE) -> str:
        candidate = str(value)
        if not pattern.fullmatch(candidate):
            raise ResearchConsoleReadError("INVALID_IDENTIFIER", f"{kind} 标识不合法", status_code=400)
        return candidate

    def _safe_path(self, relative: str | Path, *, code: str = "SOURCE_NOT_FOUND") -> Path:
        raw = str(relative).replace("\\", "/")
        candidate = Path(raw)
        if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
            raise ResearchConsoleReadError("UNSAFE_PATH", "请求的资源路径不在允许范围内", status_code=400)
        resolved = (self.root / candidate).resolve()
        if not resolved.is_relative_to(self.root):
            raise ResearchConsoleReadError("UNSAFE_PATH", "请求的资源路径不在允许范围内", status_code=400)
        if not resolved.exists():
            raise ResearchConsoleReadError(code, "请求的 canonical 资源不存在", status_code=404)
        return resolved

    def _read_json(self, path: Path, *, ttl_seconds: float = 15.0) -> Mapping[str, Any]:
        signature = _file_signature(path)
        if signature is None:
            raise ResearchConsoleReadError("SOURCE_NOT_FOUND", "canonical 数据源不存在", status_code=404)

        def load() -> Mapping[str, Any]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ResearchConsoleReadError("SOURCE_UNREADABLE", "canonical 数据源暂时不可读", status_code=503) from exc
            if not isinstance(payload, Mapping):
                raise ResearchConsoleReadError("SOURCE_INVALID", "canonical 数据源格式不受支持", status_code=503)
            return payload

        return self.cache.get(f"json:{path.as_posix()}", signature, load, ttl_seconds=ttl_seconds)

    def _read_jsonl_tail(self, path: Path, *, limit: int = 32) -> tuple[Mapping[str, Any], ...]:
        signature = _file_signature(path)
        if signature is None:
            return ()

        def load() -> tuple[Mapping[str, Any], ...]:
            try:
                with path.open("rb") as handle:
                    handle.seek(0, 2)
                    size = handle.tell()
                    handle.seek(max(0, size - 128 * 1024))
                    text = handle.read().decode("utf-8", errors="replace")
            except OSError as exc:
                raise ResearchConsoleReadError("SOURCE_UNREADABLE", "canonical 事件数据暂时不可读", status_code=503) from exc
            rows: list[Mapping[str, Any]] = []
            for line in text.splitlines()[-limit:]:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, Mapping):
                    rows.append(item)
            return tuple(rows[-limit:])

        return self.cache.get(f"jsonl-tail:{path.as_posix()}:{limit}", signature, load, ttl_seconds=5.0)

    def _provenance(self, *, source_id: str, path: Path | None, payload: Mapping[str, Any] | None, freshness: FreshnessState, stale: bool, conflict: bool, source_generated_at: datetime | None = None) -> ProvenanceView:
        version = None
        if path is not None:
            signature = _file_signature(path)
            if signature is not None:
                version = f"mtime_ns={signature[0]};size={signature[1]}"
        if source_generated_at is None and payload is not None and path is not None:
            source_generated_at = _source_timestamp(payload, path)
        return ProvenanceView(
            source_id=source_id,
            source_generated_at=source_generated_at.isoformat() if source_generated_at else None,
            observed_at=self._observed_at(),
            freshness_state=freshness.value,
            stale=bool(stale),
            conflict=bool(conflict),
            source_version=version,
        )

    def list_objectives(self) -> ResearchObjectiveListView:
        """List registered objectives without selecting one implicitly."""

        objective_dir = self.root / "data" / "research" / "research_factory" / "objectives"
        try:
            paths = sorted(objective_dir.glob("*.json"))
        except OSError as exc:
            raise ResearchConsoleReadError("OBJECTIVE_REGISTRY_UNAVAILABLE", "研究目标登记目录暂时不可读", status_code=503) from exc
        if not paths:
            raise ResearchConsoleReadError("OBJECTIVE_REGISTRY_EMPTY", "当前没有已登记的研究目标", status_code=404)

        rows: list[tuple[datetime, ResearchObjectiveSummaryView]] = []
        for path in paths:
            payload = self._read_json(path, ttl_seconds=60.0)
            objective_id = str(payload.get("objective_id") or "")
            if objective_id != path.stem or not _IDENTIFIER_RE.fullmatch(objective_id):
                raise ResearchConsoleReadError(
                    "OBJECTIVE_SOURCE_MISMATCH",
                    "研究目标登记文件的身份与文件名不一致",
                    status_code=503,
                    details={"source": path.relative_to(self.root).as_posix()},
                )

            lifecycle_state = str(payload.get("lifecycle_state") or payload.get("status") or "UNKNOWN")
            created_at = _timestamp_text(payload.get("created_at"))
            updated_at = _timestamp_text(payload.get("updated_at"))
            objective_timestamp = _source_timestamp(payload, path)
            state = lifecycle_state
            state_zh = display_state(state)
            state_source = "objective registry"
            orchestrator_available = False
            orchestrator_error_code: str | None = None
            orchestrator_error_message_zh: str | None = None
            orchestrator_source_generated_at: str | None = None
            waiting_for_governance = False

            try:
                orchestrator = self.get_orchestrator(objective_id)
            except ResearchConsoleReadError as exc:
                orchestrator_error_code = exc.code
                orchestrator_error_message_zh = exc.message_zh
            else:
                orchestrator_available = True
                state = str(orchestrator.get("orchestrator_state") or state)
                state_zh = str(orchestrator.get("orchestrator_state_zh") or display_state(state))
                state_source = "canonical Orchestrator V2"
                orchestrator_source_generated_at = _timestamp_text(orchestrator.get("source_generated_at"))
                waiting_for_governance = bool(orchestrator.get("waiting_for_governance"))

            structural_governance = self._structural_governance(objective_id)
            predictive_governance = None
            if structural_governance:
                try:
                    predictive_governance = self.predictive_governance.readiness(objective_id)
                except PredictiveGovernanceError:
                    predictive_governance = None
            structural_governance_pending = bool(predictive_governance and predictive_governance.get("status") in {"PENDING_HUMAN_DECISION", "DEFERRED"}) or bool(structural_governance and str(structural_governance[0].get("status") or "") == "PENDING_HUMAN_DECISION" and predictive_governance is None)
            if predictive_governance and predictive_governance.get("status") == "AUTHORIZED":
                state_zh = "已授权进入第 1 次预测试验，等待启动预测试验"
                state_source = f"{state_source} + predictive governance decision"
            elif predictive_governance and predictive_governance.get("status") == "ENDED":
                state_zh = "当前 Candidate 研究路径已结束"
                state_source = f"{state_source} + predictive governance decision"
            elif structural_governance_pending:
                state_zh = "结构预检已通过，等待研究治理决定"
                state_source = f"{state_source} + structural governance projection"
            governance_pending = waiting_for_governance or state == "GOVERNANCE_DECISION_REQUIRED" or structural_governance_pending
            terminal = governance_pending or state in _TERMINAL_ORCHESTRATOR_STATES
            rows.append((
                objective_timestamp,
                ResearchObjectiveSummaryView(
                    objective_id=objective_id,
                    objective_name=str(payload.get("objective_name")) if payload.get("objective_name") else None,
                    lifecycle_state=lifecycle_state,
                    lifecycle_state_zh=display_state(lifecycle_state),
                    orchestrator_state=state,
                    orchestrator_state_zh=state_zh,
                    state_source=state_source,
                    orchestrator_source_available=orchestrator_available,
                    orchestrator_error_code=orchestrator_error_code,
                    orchestrator_error_message_zh=orchestrator_error_message_zh,
                    orchestrator_source_generated_at=orchestrator_source_generated_at,
                    governance_pending=governance_pending,
                    terminal=terminal,
                    created_at=created_at,
                    updated_at=updated_at,
                    max_total_trials=_int_or_none(payload.get("max_total_trials")),
                    max_batches=_int_or_none(payload.get("max_batches")),
                ),
            ))

        rows.sort(key=lambda item: (item[0], item[1].objective_id), reverse=True)
        latest_timestamp = rows[0][0] if rows else None
        return ResearchObjectiveListView(
            provenance=self._provenance(
                source_id="research_objective_registry",
                path=objective_dir,
                payload=None,
                freshness=FreshnessState.FRESH,
                stale=False,
                conflict=False,
                source_generated_at=latest_timestamp,
            ),
            objectives=tuple(item[1] for item in rows),
        )

    def _objective(self, objective_id: str) -> tuple[Mapping[str, Any], Path]:
        objective_id = self._safe_identifier(objective_id, kind="objective_id")
        path = self.root / "data" / "research" / "research_factory" / "objectives" / f"{objective_id}.json"
        if not path.exists():
            raise ResearchConsoleReadError("UNKNOWN_OBJECTIVE", "未找到请求的研究 objective", status_code=404)
        payload = self._read_json(path, ttl_seconds=60.0)
        if str(payload.get("objective_id")) != objective_id:
            raise ResearchConsoleReadError("OBJECTIVE_SOURCE_MISMATCH", "研究 objective 身份校验失败", status_code=503)
        return payload, path

    def _runtime_paths(self, objective_id: str) -> dict[str, Path]:
        objective_id = self._safe_identifier(objective_id, kind="objective_id")
        base = self.root / "reports" / "research_daemon" / objective_id
        return {name: base / name for name in ("daemon_status.json", "daemon_checkpoint.json", "daemon_events.jsonl", "daemon_telemetry.jsonl", "daemon.lock")}

    def _orchestrator_artifact_path(self, objective_id: str, filename: str) -> Path:
        objective_id = self._safe_identifier(objective_id, kind="objective_id")
        return self.root / "reports" / "research_orchestrator_v2" / objective_id / filename

    def _daemon_evidence(self, objective_id: str) -> tuple[Mapping[str, Any], Path, FreshnessState, bool, bool, tuple[Mapping[str, Any], ...]]:
        paths = self._runtime_paths(objective_id)
        records: list[tuple[datetime, str, Mapping[str, Any], Path]] = []
        for source_id, key in (("daemon_status", "daemon_status.json"), ("daemon_checkpoint", "daemon_checkpoint.json")):
            path = paths[key]
            if not path.exists():
                continue
            payload = self._read_json(path, ttl_seconds=5.0)
            timestamp = _source_timestamp(payload, path)
            records.append((timestamp, source_id, payload, path))
        events = self._read_jsonl_tail(paths["daemon_events.jsonl"], limit=32)
        for event in events:
            timestamp = _parse_timestamp(event.get("timestamp") or event.get("created_at"))
            if timestamp is None and paths["daemon_events.jsonl"].exists():
                timestamp = datetime.fromtimestamp(paths["daemon_events.jsonl"].stat().st_mtime, timezone.utc)
            if timestamp is not None:
                records.append((timestamp, "daemon_events", event, paths["daemon_events.jsonl"]))
        if not records:
            eligibility_path = self._orchestrator_artifact_path(objective_id, "activation_eligibility.json")
            if not eligibility_path.exists():
                raise ResearchConsoleReadError("DAEMON_SOURCE_UNAVAILABLE", "后台研究守护进程的权威状态暂时不可用", status_code=503)
            eligibility = self._read_json(eligibility_path, ttl_seconds=10.0)
            if str(eligibility.get("objective_id")) != objective_id:
                raise ResearchConsoleReadError("OBJECTIVE_SOURCE_MISMATCH", "Orchestrator 激活资格不属于请求的 objective", status_code=409)
            if eligibility.get("activation_authorized") is not True:
                raise ResearchConsoleReadError("DAEMON_SOURCE_UNAVAILABLE", "该 objective 尚未获得 Orchestrator 激活资格", status_code=503)
            generated_at = _source_timestamp(eligibility, eligibility_path)
            age = (_now_utc(self._clock) - generated_at).total_seconds()
            stale = age > self.daemon_ttl_seconds
            payload = {
                "schema_version": "research-daemon-status-v1",
                "objective_id": objective_id,
                "daemon_state": "READY",
                "stage": "WAITING_FOR_ORCHESTRATOR",
                "remaining_frozen_candidates": 0,
                "required_human_ai_action": None,
                "process_pid": None,
                "last_checkpoint_time": None,
                "last_error": None,
                "retry_safe": True,
                "daemon_run_id": None,
                "budget": {},
                "activation_eligibility": "READY",
                "activation_target_state": eligibility.get("target_state"),
            }
            return payload, eligibility_path, FreshnessState.STALE if stale else FreshnessState.FRESH, stale, False, events
        records.sort(key=lambda item: (item[0], item[1]))
        selected_time, selected_id, selected, selected_path = records[-1]
        latest = [item for item in records if abs((item[0] - selected_time).total_seconds()) <= 0.001]
        identities = {(str(item[2].get("daemon_state") or item[2].get("current_state") or item[2].get("new_state") or ""), str((item[2].get("current_candidate") or {}).get("candidate_id") or item[2].get("candidate") or "")) for item in latest}
        conflict = len(identities) > 1
        age = (_now_utc(self._clock) - selected_time).total_seconds()
        stale = age > self.daemon_ttl_seconds
        freshness = FreshnessState.CONFLICT if conflict else FreshnessState.STALE if stale else FreshnessState.FRESH
        return selected, selected_path, freshness, stale, conflict, events

    def _handoff_payload(self, objective_id: str | None = None) -> tuple[Mapping[str, Any] | None, Path | None]:
        # The console's handoff endpoint is the daemon NoOutcome handoff.  An
        # objective-scoped ai_handoff.json belongs to the Orchestrator AI
        # contract and has a different schema; treating it as a daemon handoff
        # causes false OBJECTIVE_SOURCE_MISMATCH errors and exposes the wrong
        # progress source.
        paths: list[Path] = [self.root / "reports" / "RESEARCH_DAEMON_HANDOFF_CURRENT.json"]
        for path in paths:
            if not path.exists():
                continue
            payload = self._read_json(path, ttl_seconds=5.0)
            schema_version = str(payload.get("schema_version") or "")
            if schema_version and schema_version != "research-daemon-handoff-v1":
                continue
            if not schema_version and not (isinstance(payload.get("objective"), Mapping) and payload.get("handoff_type")):
                continue
            if objective_id:
                objective = payload.get("objective") if isinstance(payload.get("objective"), Mapping) else {}
                if str(objective.get("objective_id") or payload.get("objective_id") or "") != objective_id:
                    continue
            return payload, path
        return None, None

    @staticmethod
    def _candidate_from_record(record: Mapping[str, Any]) -> Mapping[str, Any]:
        candidate = record.get("current_candidate")
        if isinstance(candidate, Mapping):
            return candidate
        if isinstance(record.get("current_candidate_id"), str):
            return {"candidate_id": record["current_candidate_id"]}
        return {}

    def get_daemon(self, objective_id: str) -> DaemonStatusView:
        objective, _ = self._objective(objective_id)
        selected, path, freshness, stale, conflict, _ = self._daemon_evidence(objective_id)
        paths = self._runtime_paths(objective_id)
        status_payload: Mapping[str, Any] = selected
        if path.name == "daemon_events.jsonl":
            status_path = paths["daemon_status.json"]
            if status_path.exists():
                status_payload = self._read_json(status_path, ttl_seconds=5.0)
        state = str(selected.get("daemon_state") or selected.get("current_state") or selected.get("new_state") or status_payload.get("daemon_state") or status_payload.get("current_state") or "UNKNOWN")
        candidate = self._candidate_from_record(status_payload) or self._candidate_from_record(selected)
        counts = selected.get("research_counts") if isinstance(selected.get("research_counts"), Mapping) else status_payload.get("research_counts", {})
        if not isinstance(counts, Mapping):
            counts = {}
        remaining = status_payload.get("remaining_frozen_candidates", counts.get("remaining_frozen_candidates"))
        required_action = status_payload.get("required_human_ai_action") or status_payload.get("required_action") or selected.get("required_action")
        handoff, handoff_path = self._handoff_payload(objective_id)
        handoff_stale = False
        handoff_conflict = False
        if handoff is not None and handoff_path is not None:
            handoff_time = _source_timestamp(handoff, handoff_path)
            handoff_stale = handoff_time < _source_timestamp(status_payload, paths["daemon_status.json"]) if paths["daemon_status.json"].exists() else False
            handoff_conflict = str(handoff.get("current_state") or "") not in {"", state}
        provenance = self._provenance(source_id=path.name.replace(".jsonl", ""), path=path, payload=selected, freshness=freshness, stale=stale, conflict=conflict, source_generated_at=_source_timestamp(selected, path))
        return DaemonStatusView(
            provenance=provenance,
            objective_id=objective_id,
            daemon_state=state,
            stage=str(selected.get("stage") or status_payload.get("stage") or state),
            current_candidate_id=str(candidate.get("candidate_id")) if candidate.get("candidate_id") else None,
            remaining_frozen_candidates=int(remaining) if remaining is not None else None,
            required_human_ai_action=str(required_action) if required_action else None,
            process_pid=int(status_payload.get("process_pid")) if status_payload.get("process_pid") is not None else None,
            process_rss_bytes=int(status_payload.get("process_rss_bytes")) if status_payload.get("process_rss_bytes") is not None else None,
            system_available_memory_bytes=int(status_payload.get("system_available_memory_bytes")) if status_payload.get("system_available_memory_bytes") is not None else None,
            last_checkpoint_time=str(status_payload.get("last_checkpoint_time") or selected.get("checkpoint_at") or selected.get("last_checkpoint_time")) if (status_payload.get("last_checkpoint_time") or selected.get("checkpoint_at") or selected.get("last_checkpoint_time")) else None,
            last_error=str(status_payload.get("last_error") or selected.get("last_error")) if (status_payload.get("last_error") or selected.get("last_error")) else None,
            retry_safe=bool(status_payload.get("retry_safe", selected.get("retry_safe", True))),
            daemon_run_id=str(status_payload.get("daemon_run_id") or selected.get("daemon_run_id")) if (status_payload.get("daemon_run_id") or selected.get("daemon_run_id")) else None,
            state_display_zh=display_state(state),
            action_display_zh=display_action(required_action),
            budget=dict(status_payload.get("budget") or status_payload.get("budget_view") or selected.get("budget") or selected.get("budget_view") or {}),
            handoff_stale=handoff_stale,
            handoff_conflict=handoff_conflict,
        )

    def get_research_status(self, objective_id: str) -> ResearchStatusView:
        objective, objective_path = self._objective(objective_id)
        daemon = self.get_daemon(objective_id)
        strategy = self._strategy_records(objective_id)
        counts: dict[str, int] = {}
        for record in strategy.values():
            classification = str(record.get("current_effective_classification") or record.get("effective_classification") or "UNKNOWN")
            counts[classification] = counts.get(classification, 0) + 1
        try:
            orchestrator = self.get_orchestrator(objective_id)
        except ResearchConsoleReadError:
            orchestrator = None
        if isinstance(orchestrator, Mapping) and isinstance(orchestrator.get("research_counts"), Mapping):
            counts = {str(key): int(value) for key, value in orchestrator["research_counts"].items() if value is not None}
        canonical_state = str(orchestrator.get("orchestrator_state") if isinstance(orchestrator, Mapping) else daemon.daemon_state)
        lifecycle_stage = _lifecycle_stage(orchestrator_state=canonical_state, daemon_stage=daemon.stage, candidate_count=len(self._current_contract_candidates(objective_id)), trial_count=len(self._trial_records(objective_id)))
        return ResearchStatusView(
            provenance=self._provenance(source_id="objective+daemon+effective_strategy_view", path=objective_path, payload=objective, freshness=FreshnessState.CONFLICT if daemon.conflict else FreshnessState.STALE if daemon.stale else FreshnessState.FRESH, stale=daemon.stale, conflict=daemon.conflict),
            objective_id=objective_id,
            objective_hash=hashlib.sha256(objective_path.read_bytes()).hexdigest(),
            research_state=canonical_state,
            stage=lifecycle_stage,
            counts=counts,
            safety_counters=self._safety_counters(),
            display={"state_zh": str(orchestrator.get("orchestrator_state_zh") if isinstance(orchestrator, Mapping) else daemon.state_display_zh), "stage_zh": display_state(lifecycle_stage)},
        )

    def get_daemon_health(self, objective_id: str) -> DaemonHealthView:
        daemon = self.get_daemon(objective_id)
        paths = self._runtime_paths(objective_id)
        telemetry = self._read_jsonl_tail(paths["daemon_telemetry.jsonl"], limit=20)
        checkpoint_age = None
        checkpoint_time = _parse_timestamp(daemon.last_checkpoint_time)
        if checkpoint_time is not None:
            checkpoint_age = max(0, int((_now_utc(self._clock) - checkpoint_time).total_seconds()))
        process_alive = None
        if daemon.process_pid:
            try:
                import psutil  # type: ignore
                process_alive = bool(psutil.pid_exists(daemon.process_pid))
            except ImportError:
                process_alive = None
        if daemon.provenance.source_id == "activation_eligibility.json":
            health_state = "WAITING_FOR_ORCHESTRATOR"
            health_display = "等待 Orchestrator 接管"
        elif daemon.daemon_state in {"BUDGET_EXHAUSTED", "RESEARCH_PASSED", "GLOBAL_SEARCH_EXHAUSTED"}:
            health_state = "SAFE_TERMINAL"
            health_display = "已在安全边界结束"
        elif daemon.required_human_ai_action:
            health_state = "WAITING_HUMAN_ACTION"
            health_display = "等待人工操作"
        elif daemon.daemon_state == "ENGINEERING_BLOCKED":
            health_state = "ENGINEERING_BLOCKED"
            health_display = "工程阻断"
        elif daemon.stale:
            health_state = "STALE"
            health_display = "状态过期"
        elif daemon.system_available_memory_bytes is not None and daemon.system_available_memory_bytes < 512 * 1024 * 1024:
            health_state = "RESOURCE_PRESSURE"
            health_display = "资源偏高"
        else:
            health_state = "HEALTHY"
            health_display = "正常运行"
        return DaemonHealthView(
            provenance=daemon.provenance,
            objective_id=objective_id,
            health_state=health_state,
            health_display_zh=health_display,
            daemon_state=daemon.daemon_state,
            process_alive_observed=process_alive,
            process_pid=daemon.process_pid,
            process_rss_bytes=daemon.process_rss_bytes,
            system_available_memory_bytes=daemon.system_available_memory_bytes,
            checkpoint_age_seconds=checkpoint_age,
            telemetry_tail=telemetry,
            last_error=daemon.last_error,
        )

    def _contract_candidates(self, objective_id: str) -> dict[str, Mapping[str, Any]]:
        self._objective(objective_id)
        root = self.root / "data" / "research" / "research_factory" / "batches"
        candidates: dict[str, tuple[datetime, Mapping[str, Any], Path, set[str]]] = {}
        if not root.exists():
            return {}
        for path in sorted(root.glob("*/durable_frozen_candidate_contracts.json")):
            payload = self._read_json(path, ttl_seconds=30.0)
            contracts = payload.get("contracts", ())
            if not isinstance(contracts, list):
                continue
            for contract in contracts:
                if not isinstance(contract, Mapping):
                    continue
                policy = contract.get("policy_identity")
                policy_objective = policy.get("objective_id") if isinstance(policy, Mapping) else contract.get("objective_id")
                if str(policy_objective) != objective_id or not contract.get("candidate_id"):
                    continue
                candidate_id = str(contract["candidate_id"])
                created = _parse_timestamp(contract.get("created_frozen_timestamp")) or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                previous = candidates.get(candidate_id)
                hashes = set(previous[3]) if previous else set()
                hashes.add(str(contract.get("candidate_hash", "")))
                if previous is None or created >= previous[0]:
                    candidates[candidate_id] = (created, dict(contract), path, hashes)
                else:
                    candidates[candidate_id] = (previous[0], previous[1], previous[2], hashes)
        result: dict[str, Mapping[str, Any]] = {}
        for candidate_id, (_, contract, path, hashes) in candidates.items():
            item = dict(contract)
            item["_source_path"] = path
            item["_identity_conflict"] = len(hashes) > 1
            result[candidate_id] = item
        return result

    def _strategy_records(self, objective_id: str) -> dict[str, Mapping[str, Any]]:
        candidates = self._contract_candidates(objective_id)
        path = self.root / "reports" / "CURRENT_EFFECTIVE_STRATEGY_REGISTRY_V1.json"
        if not path.exists():
            return {}
        payload = self._read_json(path, ttl_seconds=30.0)
        result: dict[str, Mapping[str, Any]] = {}
        for record in payload.get("records", ()):
            if not isinstance(record, Mapping):
                continue
            candidate_id = str(record.get("candidate_id", ""))
            if candidate_id in candidates:
                result[candidate_id] = record
        return result

    def _trial_records(self, objective_id: str) -> dict[str, Mapping[str, Any]]:
        candidates = self._contract_candidates(objective_id)
        root = self.root / "data" / "research" / "research_factory" / "batches"
        result: dict[str, Mapping[str, Any]] = {}
        timestamps: dict[str, tuple[datetime, str, int]] = {}
        for path in sorted(root.glob("*/factory_trial_ledger.json")):
            payload = self._read_json(path, ttl_seconds=15.0)
            events = payload.get("events", ())
            if not isinstance(events, list):
                continue
            for event_index, event in enumerate(events):
                if not isinstance(event, Mapping) or not event.get("trial_id"):
                    continue
                if str(event.get("objective_id")) != objective_id:
                    continue
                candidate_id = str(event.get("candidate_id", ""))
                if candidate_id and candidates and candidate_id not in candidates:
                    continue
                trial_id = str(event["trial_id"])
                event_timestamp = _parse_timestamp(event.get("updated_at") or event.get("created_at") or event.get("timestamp")) or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                version = (event_timestamp, path.as_posix(), event_index)
                if trial_id not in timestamps or version >= timestamps[trial_id]:
                    item = dict(event)
                    item["_source_path"] = path
                    result[trial_id] = item
                    timestamps[trial_id] = version
        return result

    @staticmethod
    def _trial_order(record: Mapping[str, Any]) -> tuple[int, str]:
        explicit_number = _int_or_none(record.get("trial_number"))
        if explicit_number is not None:
            return explicit_number, str(record.get("trial_id") or "")
        match = re.search(r"_T(\d+)$", str(record.get("trial_id") or ""))
        return (int(match.group(1)) if match else 0), str(record.get("trial_id") or "")

    @classmethod
    def _latest_trial_record(cls, records: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
        return max(records.values(), key=cls._trial_order, default={})

    def _structural_payloads(self, root: Path) -> Iterable[tuple[Path, Mapping[str, Any]]]:
        if not root.exists():
            return ()
        paths = [root / "CURRENT_CANDIDATE_SAMPLE_FEASIBILITY_V2.json"]
        paths.extend(sorted(root.glob("*SAMPLE_FEASIBILITY*.json")))
        paths.extend(sorted(root.glob("*STRUCTURAL*.json")))
        seen: set[Path] = set()
        result: list[tuple[Path, Mapping[str, Any]]] = []
        for path in paths:
            if path in seen or not path.exists():
                continue
            seen.add(path)
            try:
                payload = self._read_json(path, ttl_seconds=30.0)
            except ResearchConsoleReadError:
                continue
            result.append((path, payload))
        return result

    def _structural_for(self, objective_id: str, candidate_id: str, candidate_hash: str) -> tuple[Mapping[str, Any], Path] | None:
        correction = load_effective_contract_invalidations(self.root, objective_id).get(candidate_id)
        if correction and str(correction.get("candidate_hash") or "") in {"", candidate_hash}:
            return {
                "candidate_id": candidate_id,
                "candidate_hash": candidate_hash,
                "status": "ENGINEERING_BLOCKED",
                "reason_code": str(correction.get("reason_code") or "FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE"),
                "blocking_reason_codes": [str(correction.get("reason_code") or "FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE")],
                "correction_event_id": correction.get("event_id"),
                "generated_at": correction.get("created_at"),
            }, self.root / "reports" / "research_daemon" / objective_id / CORRECTION_FILENAME
        checkpoint_path = self._runtime_paths(objective_id)["daemon_checkpoint.json"]
        if checkpoint_path.exists():
            checkpoint = self._read_json(checkpoint_path, ttl_seconds=5.0)
            canonical_refs = checkpoint.get("canonical_refs") if isinstance(checkpoint.get("canonical_refs"), Mapping) else {}
            reconciliation = canonical_refs.get("structural_reconciliation") if isinstance(canonical_refs.get("structural_reconciliation"), Mapping) else {}
            report_ref = str(reconciliation.get("report_ref") or "")
            if report_ref and _safe_relative_ref(report_ref):
                report_path = self.root / report_ref
                if report_path.is_file():
                    report = self._read_json(report_path, ttl_seconds=5.0)
                    if str(report.get("objective_id") or objective_id) == objective_id and str(report.get("candidate_id") or "") == candidate_id and (not candidate_hash or str(report.get("candidate_hash") or "") in {"", candidate_hash}):
                        return report, report_path
            structural = canonical_refs.get("last_structural_result") if isinstance(canonical_refs.get("last_structural_result"), Mapping) else {}
            details = structural.get("details") if isinstance(structural.get("details"), Mapping) else {}
            identity = details
            for nested_key in ("v2_result", "v1_result"):
                nested = details.get(nested_key) if isinstance(details.get(nested_key), Mapping) else {}
                if nested.get("candidate_id"):
                    identity = nested
                    break
            if str(identity.get("candidate_id") or "") == candidate_id and (not candidate_hash or str(identity.get("candidate_hash") or "") in {"", candidate_hash}):
                return {**dict(details), "status": structural.get("status"), "reason_code": structural.get("reason_code")}, checkpoint_path
        for path, payload in self._structural_payloads(self.root / "reports"):
            payload_objective = payload.get("objective") if isinstance(payload.get("objective"), Mapping) else {}
            declared_objective_id = str(payload_objective.get("objective_id") or payload.get("objective_id") or "")
            if declared_objective_id and declared_objective_id != objective_id:
                continue
            identity = payload.get("candidate") if isinstance(payload.get("candidate"), Mapping) else payload
            if str(identity.get("candidate_id", "")) == candidate_id and (not candidate_hash or str(identity.get("candidate_hash", "")) in {"", candidate_hash}):
                return payload, path
            for nested_key in ("v1_result", "v2_result", "result"):
                nested = payload.get(nested_key)
                if isinstance(nested, Mapping) and str(nested.get("candidate_id", "")) == candidate_id and (not candidate_hash or str(nested.get("candidate_hash", "")) in {"", candidate_hash}):
                    return payload, path
        return None

    def _structural_governance(self, objective_id: str) -> tuple[Mapping[str, Any], Path] | None:
        path = self.root / "reports" / "research_orchestrator_v2" / objective_id / "structural_governance_decision_required.json"
        if not path.is_file():
            return None
        payload = self._read_json(path, ttl_seconds=5.0)
        if str(payload.get("objective_id") or objective_id) != objective_id:
            raise ResearchConsoleReadError("OBJECTIVE_SOURCE_MISMATCH", "结构治理状态不属于请求的 objective", status_code=409)
        return payload, path

    def _current_structural(self, objective_id: str) -> StructuralPreflightView | None:
        candidates = self._contract_candidates(objective_id)
        checkpoint_path = self._runtime_paths(objective_id)["daemon_checkpoint.json"]
        candidate_id = ""
        candidate_hash = ""
        if checkpoint_path.exists():
            checkpoint = self._read_json(checkpoint_path, ttl_seconds=5.0)
            refs = checkpoint.get("canonical_refs") if isinstance(checkpoint.get("canonical_refs"), Mapping) else {}
            structural = refs.get("last_structural_result") if isinstance(refs.get("last_structural_result"), Mapping) else {}
            details = structural.get("details") if isinstance(structural.get("details"), Mapping) else {}
            for key in ("v2_result", "v1_result"):
                nested = details.get(key) if isinstance(details.get(key), Mapping) else {}
                if nested.get("candidate_id"):
                    candidate_id = str(nested.get("candidate_id"))
                    candidate_hash = str(nested.get("candidate_hash") or "")
                    break
        if not candidate_id:
            governance = self._structural_governance(objective_id)
            if governance:
                candidate_id = str(governance[0].get("candidate_id") or "")
                candidate_hash = str(governance[0].get("candidate_hash") or "")
        candidate = candidates.get(candidate_id)
        if candidate is None:
            return None
        return self._build_structural(candidate, objective_id)

    def _find_candidate_artifacts(self, objective_id: str, candidate_id: str) -> list[tuple[Path, Mapping[str, Any]]]:
        root = self.root / "reports" / "research_daemon" / objective_id / "predictive"
        if not root.exists():
            return []
        matches: list[tuple[Path, Mapping[str, Any]]] = []
        for path in sorted(root.rglob("*.json")):
            if path.name not in {"artifact_graph.json", "failure_extraction.json"}:
                continue
            payload = self._read_json(path, ttl_seconds=30.0)
            if candidate_id in json.dumps(payload, ensure_ascii=False) or candidate_id in path.as_posix():
                matches.append((path, payload))
        return matches

    def _candidate_lineage(self, objective_id: str, candidate_id: str) -> dict[str, Any]:
        graphs: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for path, payload in self._find_candidate_artifacts(objective_id, candidate_id):
            if path.name == "artifact_graph.json":
                try:
                    graph = ResearchArtifactGraphV1(path)
                    graphs.append({
                        "source_ref": _relative_safe(path, self.root),
                        "schema_version": "research-artifact-graph-v1",
                        "graph_hash": graph.graph_hash,
                        "integrity": graph.integrity(),
                        "related_nodes": [node.to_dict() for node in graph.nodes.values() if candidate_id in node.node_id][:20],
                        "related_edges": [edge.to_dict() for edge in graph.edges if candidate_id in edge.source_id or candidate_id in edge.target_id][:20],
                    })
                except (OSError, ValueError, KeyError, TypeError):
                    graphs.append({"source_ref": _relative_safe(path, self.root), "status": "UNREADABLE"})
            else:
                try:
                    snapshot = FailureKnowledgeSnapshotV1.from_dict(payload)
                    safe = snapshot.sanitized_view().to_dict()
                    safe["source_ref"] = _relative_safe(path, self.root)
                    failures.append(safe)
                except (ValueError, KeyError, TypeError):
                    failures.append({"source_ref": _relative_safe(path, self.root), "status": "UNREADABLE", "exact_performance_values_exposed": False})
        return {"artifact_graphs": graphs, "failure_knowledge": failures}

    def _build_structural(self, candidate: Mapping[str, Any], objective_id: str) -> StructuralPreflightView:
        candidate_id = str(candidate.get("candidate_id", ""))
        candidate_hash = str(candidate.get("candidate_hash", ""))
        found = self._structural_for(objective_id, candidate_id, candidate_hash)
        payload: Mapping[str, Any] = found[0] if found else {}
        path = found[1] if found else None
        repaired = payload.get("repaired_structural_result") if isinstance(payload.get("repaired_structural_result"), Mapping) else payload
        details = repaired.get("details") if isinstance(repaired.get("details"), Mapping) else repaired
        v2 = details.get("v2_result") if isinstance(details.get("v2_result"), Mapping) else repaired.get("v2_result") if isinstance(repaired.get("v2_result"), Mapping) else {}
        v1 = details.get("v1_result") if isinstance(details.get("v1_result"), Mapping) else repaired.get("v1_result") if isinstance(repaired.get("v1_result"), Mapping) else {}
        integrity = details.get("lower_bound_integrity") if isinstance(details.get("lower_bound_integrity"), Mapping) else repaired.get("lower_bound_integrity") if isinstance(repaired.get("lower_bound_integrity"), Mapping) else payload.get("lower_bound_integrity") if isinstance(payload.get("lower_bound_integrity"), Mapping) else {}
        explicit_status = str(payload.get("status") or "")
        status = explicit_status or str(repaired.get("status") or v2.get("status") or v1.get("status") or "UNKNOWN")
        reason_source = (
            payload.get("blocking_reason_codes") or ()
            if explicit_status == "PASS"
            else v2.get("reason_codes") or v1.get("reason_codes") or v2.get("blocking_reason_codes") or payload.get("blocking_reason_codes") or ([payload.get("reason_code")] if payload.get("reason_code") else ())
        )
        reason_codes = tuple(str(item) for item in reason_source)
        data_provenance = v1.get("data_pit_provenance") if isinstance(v1.get("data_pit_provenance"), Mapping) else payload.get("data_pit_provenance") if isinstance(payload.get("data_pit_provenance"), Mapping) else {}
        return StructuralPreflightView(
            provenance=self._provenance(source_id=path.name if path else "structural_preflight_unavailable", path=path, payload=payload if payload else None, freshness=FreshnessState.FRESH if path else FreshnessState.UNKNOWN, stale=False, conflict=False) if path else self._provenance(source_id="structural_preflight_unavailable", path=None, payload=None, freshness=FreshnessState.UNKNOWN, stale=False, conflict=False),
            candidate_id=candidate_id,
            candidate_hash=candidate_hash,
            status=status,
            qualified_observations=_int_or_none(v1.get("qualified_signal_count")),
            selected_opportunities=_int_or_none(v1.get("selected_opportunity_count")),
            portfolio_feasible_count=_int_or_none(v1.get("portfolio_feasible_opportunity_count")),
            lower_bound=_int_or_none(v2.get("lower_bound_count") or v1.get("lower_bound_count") or integrity.get("lower_bound_count")),
            upper_bound=_int_or_none(v2.get("upper_bound_count") or v1.get("upper_bound_count") or integrity.get("upper_bound_count")),
            minimum_required=_int_or_none(v2.get("minimum_required_count") or v1.get("minimum_required_count")),
            reason_codes=reason_codes,
            provider_completeness={"data_complete_count": _int_or_none(v1.get("data_complete_count")), "execution_eligible_count": _int_or_none(v1.get("execution_eligible_count")), "pit_eligible_count": _int_or_none(v1.get("pit_eligible_count"))},
            research_period=dict(candidate.get("research_period_identity") or {}),
            pit_status="PIT_VERIFIED" if data_provenance.get("pit") else "UNKNOWN",
            execution_feasibility="FEASIBLE" if _int_or_none(v1.get("execution_eligible_count")) is not None else "UNKNOWN",
            outcome_blind=bool(v2.get("outcome_blind", v1.get("outcome_blind", integrity.get("outcome_blind", True)))),
            performance_data_loaded=bool(v2.get("performance_data_loaded", v1.get("performance_data_loaded", False))),
        )

    def _candidate_summary(self, candidate: Mapping[str, Any], objective_id: str) -> CandidateSummaryView:
        candidate_id = str(candidate.get("candidate_id", ""))
        scope_info = candidate_scope_info(self.root, objective_id, candidate_id)
        strategy = self._strategy_records(objective_id).get(candidate_id, {})
        trials = [item for item in self._trial_records(objective_id).values() if str(item.get("candidate_id")) == candidate_id]
        trial = sorted(trials, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""))[-1] if trials else {}
        structural = self._build_structural(candidate, objective_id)
        classification = strategy.get("current_effective_classification")
        if classification is None:
            classification = trial.get("classification")
        reasons = tuple({"reason_code": str(code), "reason_display_zh": display_reason(code), "explanation_zh": display_reason(code, include_code=False)} for code in (trial.get("reason_codes") or structural.reason_codes))
        provenance = self._provenance(source_id="durable_frozen_candidate_contract", path=candidate.get("_source_path"), payload=candidate, freshness=FreshnessState.CONFLICT if candidate.get("_identity_conflict") else FreshnessState.FRESH, stale=False, conflict=bool(candidate.get("_identity_conflict")))
        source_provenance = candidate.get("source_provenance") if isinstance(candidate.get("source_provenance"), Mapping) else {}
        pipeline_state = str(trial.get("status") or structural.status or "FROZEN")
        return CandidateSummaryView(
            provenance=provenance,
            candidate_id=candidate_id,
            candidate_hash=str(candidate.get("candidate_hash", "")),
            explanation_zh=str((candidate.get("full_semantic_record") or {}).get("candidate", {}).get("description") or "冻结 Candidate 的 canonical 语义合同已载入。"),
            mechanism_family=str(candidate.get("family", "")),
            frozen_state="FROZEN",
            pipeline_state=pipeline_state,
            structural_status=structural.status,
            predictive_trial_status=str(trial.get("status") or "NOT_RUN"),
            effective_classification=str(classification) if classification else None,
            holding_horizon=_int_or_none(candidate.get("holding_period_trading_sessions")),
            factor_ids=tuple(str(item) for item in candidate.get("factor_ids", ())),
            created_batch_id=str(source_provenance.get("batch_id")) if source_provenance.get("batch_id") else None,
            created_run_id=str(source_provenance.get("run_id")) if source_provenance.get("run_id") else None,
            budget_consumed=bool(trial.get("performance_accessed") or trial.get("performance_complete")),
            reasons=reasons,
            scope_status=str(scope_info["scope_status"]),
            scope_status_zh=str(scope_info["scope_status_zh"]),
            scope_reason_zh=str(scope_info["scope_reason_zh"]),
            is_current_followup=bool(scope_info["is_current_followup"]),
        )

    def _current_contract_candidates(self, objective_id: str) -> dict[str, Mapping[str, Any]]:
        return {
            candidate_id: candidate
            for candidate_id, candidate in self._contract_candidates(objective_id).items()
            if candidate_scope_info(self.root, objective_id, candidate_id)["is_current_followup"]
        }

    def _candidate_scope_summary(self, objective_id: str, candidates: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        manifest = load_scope_manifest(self.root, objective_id)
        historical_ids = [str(item) for item in manifest.get("out_of_scope_candidate_ids", ())]
        parent_refs = [dict(item) for item in manifest.get("parent_candidate_refs", ()) if isinstance(item, Mapping)]
        current_count = sum(candidate_scope_info(self.root, objective_id, candidate_id)["is_current_followup"] for candidate_id in candidates)
        return {
            "current_followup_candidate_count": int(current_count),
            "historical_ai_record_count": len(historical_ids),
            "parent_promising_reference_count": len(parent_refs),
            "planned_confirmation_candidate_limit": int(manifest.get("planned_confirmation_candidate_limit", 0) or 0),
            "historical_ai_candidate_ids": historical_ids,
            "parent_candidate_refs": parent_refs,
        }

    def list_candidates(self, objective_id: str, *, page: int = 1, page_size: int = 50, search: str = "", status: str = "ALL", mechanism: str = "ALL", sort: str = "candidate_id", direction: str = "asc") -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ResearchConsoleReadError("INVALID_PAGE_SIZE", "分页参数超出允许范围", status_code=400)
        candidates = self._contract_candidates(objective_id)
        views = [self._candidate_summary(candidates[key], objective_id) for key in sorted(candidates)]
        query = str(search).strip().casefold()
        if query:
            views = [item for item in views if query in " ".join((item.candidate_id, item.candidate_hash, item.explanation_zh, item.mechanism_family, *item.factor_ids)).casefold()]
        status = str(status).strip().upper()
        if status != "ALL":
            views = [item for item in views if status in {item.structural_status, item.predictive_trial_status, item.effective_classification or "", item.pipeline_state}]
        if mechanism and str(mechanism).upper() != "ALL":
            views = [item for item in views if item.mechanism_family == mechanism]
        sort_fields = {"candidate_id": lambda item: item.candidate_id, "mechanism": lambda item: item.mechanism_family, "pipeline_state": lambda item: item.pipeline_state, "classification": lambda item: item.effective_classification or "", "holding_horizon": lambda item: item.holding_horizon or 0}
        views.sort(key=sort_fields.get(str(sort), sort_fields["candidate_id"]), reverse=str(direction).lower() == "desc")
        start = (page - 1) * page_size
        return {"objective_id": objective_id, "page": page, "page_size": page_size, "total": len(views), "items": [item.to_dict() for item in views[start:start + page_size]], "scope_summary": self._candidate_scope_summary(objective_id, candidates)}

    def get_candidate(self, objective_id: str, candidate_id: str) -> CandidateDetailView:
        candidate_id = self._safe_identifier(candidate_id, kind="candidate_id")
        candidates = self._contract_candidates(objective_id)
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise ResearchConsoleReadError("CANDIDATE_NOT_FOUND", "未找到请求的 canonical Candidate", status_code=404)
        structural = self._build_structural(candidate, objective_id)
        trials = [self._trial_summary(item, objective_id) for item in self._trial_records(objective_id).values() if str(item.get("candidate_id")) == candidate_id]
        source_provenance = candidate.get("source_provenance") if isinstance(candidate.get("source_provenance"), Mapping) else {}
        policy = candidate.get("policy_identity") if isinstance(candidate.get("policy_identity"), Mapping) else {}
        identity = {"candidate_id": candidate_id, "candidate_hash": candidate.get("candidate_hash"), "content_hash": candidate.get("content_hash"), "contract_schema_version": candidate.get("contract_schema_version"), "canonical_identity_hash": canonical_frozen_contract_identity_hash({key: value for key, value in candidate.items() if not str(key).startswith("_")})}
        semantics = {"hypothesis_id": candidate.get("hypothesis_id"), "family": candidate.get("family"), "mechanism": candidate.get("mechanism"), "factor_ids": list(candidate.get("factor_ids", ())), "factor_directions": dict(candidate.get("factor_directions") or {}), "entry_timing": dict(candidate.get("entry_timing") or {}), "selection_rule": dict(candidate.get("selection_rule") or {}), "holding_period_trading_sessions": candidate.get("holding_period_trading_sessions"), "execution_contract_version": candidate.get("execution_contract_version")}
        governance = {"policy_identity": dict(policy), "research_period_identity": dict(candidate.get("research_period_identity") or {}), "pit_dependencies": {"required_data": list((candidate.get("pit_dependencies") or {}).get("required_data", ())), "pit_requirements": list((candidate.get("pit_dependencies") or {}).get("pit_requirements", ()))}, "source_provenance": dict(source_provenance)}
        lineage = {"contract_ref": _relative_safe(candidate.get("_source_path"), self.root), "hypothesis_id": candidate.get("hypothesis_id"), "artifact_refs": list(source_provenance.get("artifact_refs", ())) if isinstance(source_provenance.get("artifact_refs"), list) else []}
        lineage.update(self._candidate_lineage(objective_id, candidate_id))
        lineage["scope"] = candidate_scope_info(self.root, objective_id, candidate_id)
        predictive = {"trial_count": len(trials), "trials": [item.to_dict() for item in trials]}
        shadow = {"available": False, "marker": "SHADOW_RESEARCH_ONLY", "warning_zh": "仅供研究观察，不代表买入建议。"}
        provenance = self._provenance(source_id="durable_frozen_candidate_contract", path=candidate.get("_source_path"), payload=candidate, freshness=FreshnessState.CONFLICT if candidate.get("_identity_conflict") else FreshnessState.FRESH, stale=False, conflict=bool(candidate.get("_identity_conflict")))
        return CandidateDetailView(provenance=provenance, candidate_id=candidate_id, identity=identity, semantics=semantics, structural=structural.to_dict(), predictive=predictive, governance=governance, artifact_lineage=lineage, shadow=shadow)

    def get_structural(self, objective_id: str, candidate_id: str) -> StructuralPreflightView:
        candidate_id = self._safe_identifier(candidate_id, kind="candidate_id")
        candidate = self._contract_candidates(objective_id).get(candidate_id)
        if candidate is None:
            raise ResearchConsoleReadError("CANDIDATE_NOT_FOUND", "未找到请求的 canonical Candidate", status_code=404)
        return self._build_structural(candidate, objective_id)

    def _trial_summary(self, record: Mapping[str, Any], objective_id: str) -> TrialSummaryView:
        status = str(record.get("status") or "UNKNOWN")
        stage = str(record.get("stage") or {"REGISTERED": "TRIAL_CREATED", "PERFORMANCE_ACCESSED": "TRIAL_RUNNING", "PERFORMANCE_COMPLETE_PENDING_ADJUDICATION": "TRIAL_COMPLETING", "COMPLETED": "TRIAL_COMPLETED"}.get(status, status))
        registered = bool(record.get("registered") or record.get("registered_at") or status not in {"", "UNKNOWN"})
        reserved = bool(record.get("reserved") or record.get("reserved_at") or record.get("budget_reservation_identity"))
        performance_accessed = bool(record.get("performance_accessed"))
        performance_complete = bool(record.get("performance_complete") or record.get("performance_completed"))
        completed = performance_complete or bool(record.get("final_adjudicated")) or status == "COMPLETED"
        blocked = status == "BLOCKED" or str(record.get("classification")) == "BLOCKED"
        failed = status in {"INVALIDATED", "REJECTED", "FAILED"} or str(record.get("classification")) in {"REJECTED", "WEAK"}
        source_path = record.get("_source_path") if isinstance(record.get("_source_path"), Path) else None
        return TrialSummaryView(
            provenance=self._provenance(source_id="factory_trial_ledger", path=source_path, payload=record, freshness=FreshnessState.FRESH, stale=False, conflict=False),
            trial_id=str(record.get("trial_id", "")), objective_id=objective_id, batch_id=str(record.get("batch_id", "")), candidate_id=str(record.get("candidate_id", "")), candidate_hash=str(record.get("candidate_hash", "")), family_id=str(record.get("family_id", "")), status=status, lifecycle_state=status, registered=registered, reserved=reserved, performance_accessed=performance_accessed, performance_complete=performance_complete, completed=completed, reconciled=bool(record.get("final_adjudicated") or record.get("registry_committed")), blocked=blocked, failed_or_rejected=failed, classification=str(record.get("classification")) if record.get("classification") is not None else None, reason_codes=tuple(str(item) for item in record.get("reason_codes", ())), budget_consumed=bool(performance_accessed or performance_complete), budget_reservation_identity=str(record.get("budget_reservation_identity")) if record.get("budget_reservation_identity") else None, state_display_zh=display_status(status), classification_display_zh=display_classification(record.get("classification")) if record.get("classification") else None, stage=stage, created_at=str(record.get("created_at")) if record.get("created_at") else None, started_at=str(record.get("started_at")) if record.get("started_at") else None, last_activity_at=str(record.get("last_activity_at")) if record.get("last_activity_at") else None, finished_at=str(record.get("finished_at")) if record.get("finished_at") else None, error_code=str(record.get("error_code")) if record.get("error_code") else None, error_message=str(record.get("error_message")) if record.get("error_message") else None, recovery_status=str(record.get("recovery_status")) if record.get("recovery_status") else None, start_intent_id=str(record.get("start_intent_id")) if record.get("start_intent_id") else None,
        )

    def list_trials(self, objective_id: str, *, page: int = 1, page_size: int = 50, search: str = "", status: str = "ALL", classification: str = "ALL", sort: str = "trial_id", direction: str = "asc") -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ResearchConsoleReadError("INVALID_PAGE_SIZE", "分页参数超出允许范围", status_code=400)
        records = self._trial_records(objective_id)
        views = [self._trial_summary(records[key], objective_id) for key in sorted(records)]
        query = str(search).strip().casefold()
        if query:
            views = [item for item in views if query in " ".join((item.trial_id, item.candidate_id, item.candidate_hash, item.family_id)).casefold()]
        status = str(status).strip().upper()
        if status != "ALL":
            views = [item for item in views if status in {item.status, item.lifecycle_state}]
        classification = str(classification).strip().upper()
        if classification != "ALL":
            views = [item for item in views if (item.classification or "") == classification]
        sort_fields = {"trial_id": lambda item: item.trial_id, "candidate_id": lambda item: item.candidate_id, "status": lambda item: item.status, "classification": lambda item: item.classification or ""}
        views.sort(key=sort_fields.get(str(sort), sort_fields["trial_id"]), reverse=str(direction).lower() == "desc")
        start = (page - 1) * page_size
        return {"objective_id": objective_id, "page": page, "page_size": page_size, "total": len(views), "items": [item.to_dict() for item in views[start:start + page_size]]}

    def _find_trial_artifacts(self, objective_id: str, trial_id: str) -> list[tuple[Path, Mapping[str, Any]]]:
        root = self.root / "reports" / "research_daemon" / objective_id / "predictive"
        if not root.exists():
            return []
        matches: list[tuple[Path, Mapping[str, Any]]] = []
        for path in sorted(root.rglob("*.json")):
            if path.name not in {"trial_manifest.json", "final_status.json", "validation_results.json", "multiple_testing.json", "performance_access_gate.json", "artifact_graph.json", "failure_extraction.json"}:
                continue
            payload = self._read_json(path, ttl_seconds=30.0)
            encoded = json.dumps(payload, ensure_ascii=False)
            if trial_id in encoded:
                matches.append((path, payload))
        return matches

    def get_trial(self, objective_id: str, trial_id: str) -> TrialDetailView:
        trial_id = self._safe_identifier(trial_id, kind="trial_id", pattern=_TRIAL_IDENTIFIER_RE)
        record = self._trial_records(objective_id).get(trial_id)
        if record is None:
            raise ResearchConsoleReadError("TRIAL_NOT_FOUND", "未找到请求的 canonical Trial", status_code=404)
        summary = self._trial_summary(record, objective_id)
        artifacts = self._find_trial_artifacts(objective_id, trial_id)
        performance: dict[str, Any] = {}
        statistical: dict[str, Any] = {}
        effective: dict[str, Any] = {"classification": record.get("classification"), "reason_codes": list(record.get("reason_codes", ())), "final_adjudicated": bool(record.get("final_adjudicated"))}
        refs: list[str] = []
        for path, payload in artifacts:
            refs.append(_relative_safe(path, self.root))
            if path.name == "validation_results.json":
                rows = payload.get("rows") if isinstance(payload.get("rows"), list) else [payload]
                performance = _performance_projection(rows[0] if rows else payload)
            elif path.name == "multiple_testing.json":
                statistical = _performance_projection(payload)
            elif path.name == "final_status.json":
                effective.update({key: payload.get(key) for key in ("status", "classification", "final_test_access", "prospective", "real_order") if key in payload})
        lineage = {"artifact_refs": refs, "budget_reservation_identity": record.get("budget_reservation_identity")}
        return TrialDetailView(provenance=summary.provenance, trial_id=trial_id, summary=summary.to_dict(), human_authorized=True, performance=performance, statistical=statistical, effective_decision=effective, artifact_lineage=lineage)

    def get_budget(self, objective_id: str, *, include_trial_consumption: bool = True) -> SearchBudgetView:
        daemon = self.get_daemon(objective_id)
        ref = daemon.budget.get("registry_path") if isinstance(daemon.budget, Mapping) else None
        path = None
        if ref:
            try:
                candidate_path = self._safe_path(str(ref))
                payload = self._read_json(candidate_path, ttl_seconds=10.0)
                if str(payload.get("objective_id")) == objective_id:
                    path = candidate_path
            except ResearchConsoleReadError:
                path = None
        if path is None:
            root = self.root / "data" / "research" / "research_factory" / "batches"
            options: list[tuple[datetime, Path]] = []
            for option in sorted(root.glob("*/search_budget_registry.json")):
                payload = self._read_json(option, ttl_seconds=10.0)
                if str(payload.get("objective_id")) == objective_id:
                    options.append((_source_timestamp(payload, option), option))
            if options:
                path = sorted(options, key=lambda item: item[0])[-1][1]
        if path is None:
            raise ResearchConsoleReadError("BUDGET_SOURCE_UNAVAILABLE", "canonical SearchBudgetRegistry 暂时不可用", status_code=503)
        raw_budget = self._read_json(path, ttl_seconds=10.0)
        registry = SearchBudgetRegistryV1(objective_id, path)
        snapshot = registry.snapshot()
        bucket = next((item for item in snapshot.get("buckets", ()) if item.get("kind") == "objective" and item.get("key") == objective_id), {})
        daemon_bucket = daemon.budget if isinstance(daemon.budget, Mapping) else {}
        budget_conflict = any(
            daemon_bucket.get(key) is not None and int(daemon_bucket.get(key)) != int(bucket.get(registry_key, 0))
            for key, registry_key in (("used", "used"), ("total", "limit"), ("remaining", "remaining"), ("reserved", "reserved"))
        )
        trials = self._trial_records(objective_id) if include_trial_consumption else {}
        consumption = tuple({"trial_id": item.get("trial_id"), "candidate_id": item.get("candidate_id"), "status": item.get("status"), "performance_accessed": bool(item.get("performance_accessed")), "budget_reservation_identity": item.get("budget_reservation_identity")} for item in sorted(trials.values(), key=lambda value: str(value.get("trial_id", ""))))
        return SearchBudgetView(provenance=self._provenance(source_id="SearchBudgetRegistryV1", path=path, payload=raw_budget, freshness=FreshnessState.CONFLICT if budget_conflict else FreshnessState.FRESH, stale=False, conflict=budget_conflict), objective_id=objective_id, used=int(bucket.get("used", 0)), total=int(bucket.get("limit", 0)), remaining=int(bucket.get("remaining", 0)), reserved=int(bucket.get("reserved", 0)), registry_identity=path.relative_to(self.root).as_posix(), registry_head_hash=registry.head_hash, buckets=tuple(snapshot.get("buckets", ())), trial_consumption=consumption)

    def get_handoff(self, objective_id: str) -> NoOutcomeHandoffView:
        self._objective(objective_id)
        handoff, path = self._handoff_payload(objective_id)
        if handoff is None or path is None:
            return NoOutcomeHandoffView(
                provenance=self._provenance(source_id="handoff_unavailable", path=None, payload=None, freshness=FreshnessState.UNKNOWN, stale=False, conflict=False),
                objective_id=objective_id,
                handoff_type="UNAVAILABLE",
                current_state="UNAVAILABLE",
                reason_code="DAEMON_HANDOFF_NOT_AVAILABLE",
                reason_display_zh="当前没有独立的 daemon NoOutcome handoff；研究进度请以 Orchestrator 状态为准。",
                allowed_next_actions=(),
                forbidden_actions=("把 AI handoff 当作 daemon handoff", "根据缺失 handoff 推算研究进度"),
                display={"handoff_type_zh": "当前没有守护进程交接", "availability_zh": "当前没有独立 NoOutcome handoff"},
            )
        objective = handoff.get("objective") if isinstance(handoff.get("objective"), Mapping) else {}
        if str(objective.get("objective_id")) != objective_id:
            raise ResearchConsoleReadError("OBJECTIVE_SOURCE_MISMATCH", "handoff 不属于请求的 objective", status_code=409)
        daemon = self.get_daemon(objective_id)
        handoff_time = _source_timestamp(handoff, path)
        daemon_time = _parse_timestamp(daemon.source_generated_at)
        stale = bool(daemon_time and handoff_time < daemon_time)
        conflict = bool(str(handoff.get("current_state") or "") not in {"", daemon.daemon_state})
        freshness = FreshnessState.CONFLICT if conflict and not stale else FreshnessState.STALE if stale else FreshnessState.FRESH
        blocking = handoff.get("blocking_reason") if isinstance(handoff.get("blocking_reason"), Mapping) else {}
        remaining = handoff.get("remaining_candidate_state") if isinstance(handoff.get("remaining_candidate_state"), Mapping) else {}
        current = remaining.get("current_candidate") if isinstance(remaining.get("current_candidate"), Mapping) else {}
        current_safe = {key: current.get(key) for key in ("candidate_id", "candidate_hash", "batch_id", "mechanism") if current.get(key) is not None}
        reason_code = str(blocking.get("reason_code")) if blocking.get("reason_code") else None
        view = NoOutcomeHandoffView(provenance=self._provenance(source_id="RESEARCH_DAEMON_HANDOFF_CURRENT", path=path, payload=handoff, freshness=freshness, stale=stale, conflict=conflict), objective_id=objective_id, handoff_type=str(handoff.get("handoff_type") or "UNKNOWN"), current_state=str(handoff.get("current_state") or "UNKNOWN"), reason_code=reason_code, reason_display_zh=display_reason(reason_code) if reason_code else "未提供原因", artifact_refs=tuple(str(item) for item in blocking.get("artifact_refs", ()) if _safe_relative_ref(str(item))), allowed_next_actions=tuple(str(item) for item in handoff.get("allowed_next_actions", ())), forbidden_actions=tuple(str(item) for item in handoff.get("forbidden_actions", ())), current_candidate=current_safe, remaining_frozen_candidates=_int_or_none(remaining.get("remaining_frozen_candidates")), global_search_exhausted=bool(remaining.get("global_search_exhausted", False)), budget=dict(handoff.get("budget") or {}), performance_values_exposed=False, outcome_blind=True, display={"handoff_type_zh": display_state(handoff.get("handoff_type")), "stale_zh": "已过期" if stale else "当前"})
        try:
            PerformanceBlindGuard.assert_blind(view.to_dict())
        except (PerformanceLeakError, ValueError) as exc:
            raise ResearchConsoleReadError("OUTCOME_FIELD_BLOCKED", "handoff 含有禁止暴露的绩效字段", status_code=503) from exc
        return view

    def _manual_task_paths(self, handoff_id: str) -> tuple[Path, Path]:
        from .research_factory.manual_handoff import HANDOFF_ID_RE, RESULT_FILENAME, STAGING_ROOT_NAME

        if not HANDOFF_ID_RE.fullmatch(str(handoff_id)):
            raise ResearchConsoleReadError("INVALID_HANDOFF_ID", "手动交接编号不合法", status_code=400)
        task_dir = self.root / STAGING_ROOT_NAME / str(handoff_id)
        if not task_dir.resolve().is_relative_to((self.root / STAGING_ROOT_NAME).resolve()):
            raise ResearchConsoleReadError("UNSAFE_PATH", "手动交接目录不在允许范围内", status_code=400)
        return task_dir, task_dir / RESULT_FILENAME

    def get_manual_ai_handoff(self, objective_id: str) -> dict[str, Any]:
        self._objective(objective_id)
        from .research_factory.manual_handoff import ManualAIResultWatcherV1

        status = self.get_orchestrator(objective_id)
        mode = str(status.get("ai_invocation_mode") or "MANUAL_HANDOFF")
        mode_zh = str(status.get("ai_invocation_mode_zh") or {"MANUAL_HANDOFF": "手动 AI 交接（推荐）", "AUTO_CODEX": "自动调用 AI 研究员", "AI_DISABLED": "AI 研究已禁用"}.get(mode, "当前模式无法确认"))
        manual = status.get("manual_handoff") if isinstance(status.get("manual_handoff"), Mapping) else {}
        handoff_id = str(status.get("current_handoff_id") or "")
        invocation = status.get("last_ai_invocation") if isinstance(status.get("last_ai_invocation"), Mapping) else {}
        invocation_id = str(status.get("current_ai_invocation_id") or invocation.get("ai_invocation_id") or "") or None
        invocation_by_handoff = self._manual_invocation_records(objective_id)
        task_dir = None
        prompt_path = None
        instructions_path = None
        allowed_info_path = None
        result_path = None
        task_created_at = None
        prompt_text = ""
        instructions_text = ""
        allowed_info: Mapping[str, Any] = {}
        output_contract: Mapping[str, Any] = {}
        result_status = "WAITING"
        result_status_zh = "等待结果文件"
        result_reason_zh = "尚未发现 AI 研究结果文件。"
        validation = status.get("validation") if isinstance(status.get("validation"), Mapping) else {}
        ready_metadata: Mapping[str, Any] = {}
        if handoff_id:
            task_dir_path, _ = self._manual_task_paths(handoff_id)
            ready_path = task_dir_path / "HANDOFF_READY.json"
            task_path = task_dir_path / "AI研究提示词.md"
            instruction_path = task_dir_path / "AI任务说明.md"
            context_path = task_dir_path / "NOOUTCOME_CONTEXT.json"
            contract_path = task_dir_path / "OUTPUT_CONTRACT.json"
            if ready_path.exists():
                ready = self._read_json(ready_path, ttl_seconds=2.0)
                ready_metadata = ready
                if str(ready.get("objective_id")) != objective_id:
                    raise ResearchConsoleReadError("OBJECTIVE_SOURCE_MISMATCH", "手动交接任务不属于请求的研究目标", status_code=409)
                task_dir = str(ready.get("task_dir") or task_dir_path.relative_to(self.root).as_posix())
                result_path = str(ready.get("result_path") or (task_dir_path / "AI_RESEARCH_BATCH_RESULT_V2.json").relative_to(self.root).as_posix())
                prompt_path = f"{task_dir}/AI研究提示词.md"
                instructions_path = f"{task_dir}/AI任务说明.md"
                allowed_info_path = f"{task_dir}/NOOUTCOME_CONTEXT.json"
                task_created_at = ready.get("created_at")
                prompt_text = task_path.read_text(encoding="utf-8") if task_path.exists() else ""
                instructions_text = instruction_path.read_text(encoding="utf-8") if instruction_path.exists() else ""
                allowed_info = dict(self._read_json(context_path, ttl_seconds=2.0)) if context_path.exists() else {}
                output_contract = dict(self._read_json(contract_path, ttl_seconds=2.0)) if contract_path.exists() else {}
                inspected = ManualAIResultWatcherV1(self.root).inspect(handoff_id)
                result_status = str(inspected.get("status") or "WAITING")
                result_status_zh = {"WAITING": "等待结果文件", "FOUND": "已发现，等待校验", "INVALID_JSON": "结果格式无效", "INVALID_TASK": "任务资料不可读"}.get(result_status, "结果状态待确认")
                result_reason_zh = str(inspected.get("reason_zh") or result_reason_zh)
                historical_invocation = invocation_by_handoff.get(handoff_id)
                if historical_invocation:
                    result_status, result_status_zh, result_reason_zh = self._manual_result_presentation(historical_invocation, result_status, result_status_zh, result_reason_zh)
        if mode == "AUTO_CODEX":
            result_reason_zh = "当前选择自动调用模式，手动交接目录只保留历史记录。"
        elif mode == "AI_DISABLED":
            result_reason_zh = "AI 研究已禁用，不会创建新的手动交接任务。"
        payload = ManualAIHandoffView(
            provenance=self._provenance(source_id="research_ai_staging", path=(self.root / "research_ai_staging") if task_dir else None, payload=None, freshness=FreshnessState.FRESH if task_dir else FreshnessState.UNKNOWN, stale=False, conflict=False),
            objective_id=objective_id,
            mode=mode,
            mode_zh=mode_zh,
            state=str(status.get("orchestrator_state") or "UNKNOWN"),
            state_zh=str(status.get("orchestrator_state_zh") or "未知状态（未提供）"),
            handoff_id=handoff_id or None,
            invocation_id=invocation_id,
            task_dir=task_dir,
            prompt_path=prompt_path,
            instructions_path=instructions_path,
            allowed_info_path=allowed_info_path,
            result_path=result_path,
            task_created_at=task_created_at,
            prompt_character_count=int(manual.get("prompt_character_count", 0) or 0),
            prompt_token_estimate=int(manual.get("prompt_token_estimate", 0) or 0),
            context_mode=str(manual.get("context_mode") or "REFERENCES_ONLY"),
            result_status=result_status,
            result_status_zh=result_status_zh,
            result_reason_zh=result_reason_zh,
            background_ai_token_consumption=0,
            output_contract=output_contract,
            allowed_actions_zh=("复制提示词", "打开任务目录", "查看任务说明", "查看允许信息", "检查结果文件", "重新扫描结果"),
            forbidden_actions_zh=("手动导入候选", "手动编辑清单", "手动恢复研究", "修改预算或试验记录"),
            prompt_text=prompt_text,
            instructions_text=instructions_text,
            allowed_info=allowed_info,
            validation_status=str(validation.get("status") or "NOT_RUN"),
            validation_status_zh={"IN_PROGRESS": "正在校验", "PASS": "校验通过", "INVALID": "校验未通过", "ERROR": "校验发生工程异常"}.get(str(validation.get("status") or "NOT_RUN"), "尚未开始校验"),
            validation_stage=str(validation.get("stage")) if validation.get("stage") else None,
            validation_stage_zh={"FORMAT": "检查结果格式", "IDENTITY": "核对任务身份", "CANDIDATE_RULES": "检查候选策略契约", "ARTIFACTS": "检查引用资料", "BUDGET": "核对研究预算", "COMPLETED": "校验完成", "FAILED": "校验失败"}.get(str(validation.get("stage") or ""), "暂无校验步骤"),
            validation_started_at=validation.get("started_at"),
            validation_last_activity_at=validation.get("last_activity_at"),
            validation_finished_at=validation.get("finished_at"),
            validation_error=validation.get("error"),
            validation_stale=bool(validation.get("stale")),
            validation_process_alive=bool(validation.get("process_alive")),
        )
        result = payload.to_dict()
        result["manual_handoff"] = dict(manual)
        result["manual_handoff_id"] = str(ready_metadata.get("manual_handoff_id") or handoff_id) or None
        result["task_purpose"] = str(ready_metadata.get("task_purpose") or manual.get("task_purpose") or "PROMISING_FOLLOWUP 机制确认设计")
        result["current_round"] = str(ready_metadata.get("current_round") or manual.get("current_round") or "PROMISING_FOLLOWUP")
        result["ai_design_policy"] = dict(ready_metadata.get("ai_design_policy") or manual.get("ai_design_policy") or {})
        result["planned_confirmation_candidate_limit"] = int(ready_metadata.get("planned_confirmation_candidate_limit", manual.get("planned_confirmation_candidate_limit", 0)) or 0)
        result["required_human_action"] = "现在需要你操作：复制 AI 研究提示词并交给一次人工 AI 任务"
        PerformanceBlindGuard.assert_blind(result)
        return result

    def _manual_invocation_records(self, objective_id: str) -> dict[str, Mapping[str, Any]]:
        """Read manual handoff invocation history without exposing raw stores."""

        run_dir = self.root / "reports" / "research_orchestrator_v2" / objective_id
        paths = [run_dir / "ai_invocation.json"]
        history_dir = run_dir / "invocation_history"
        if history_dir.exists():
            paths.extend(sorted(history_dir.glob("*.json")))
        records: dict[str, Mapping[str, Any]] = {}
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, Mapping):
                continue
            task = payload.get("manual_task") if isinstance(payload.get("manual_task"), Mapping) else {}
            handoff_id = str(payload.get("handoff_id") or task.get("handoff_id") or "")
            if handoff_id:
                records[handoff_id] = payload
        return records

    @staticmethod
    def _manual_result_presentation(record: Mapping[str, Any], fallback_status: str, fallback_status_zh: str, fallback_reason_zh: str) -> tuple[str, str, str]:
        status = str(record.get("status") or "")
        if status == "ACCEPTED" and str(record.get("ingestion_status")) == "COMPLETED":
            count = int(record.get("candidate_count", 0) or 0)
            return "ACCEPTED", "已通过检查，候选策略已自动接入", f"AI 研究结果已通过检查，并自动接入 {count} 个候选策略。"
        if status == "INVALID":
            return "INVALID", "未通过检查，等待重新执行", str(record.get("error_message_zh") or "AI 研究结果未通过检查，需要重新执行 AI 研究任务。")
        if status == "ENGINEERING_BLOCKED":
            return "ENGINEERING_BLOCKED", "校验发生工程异常，研究已暂停", str(record.get("error_message_zh") or "AI 研究结果校验发生工程异常，研究已暂停，需要人工检查后台服务。")
        if status in {"COMPLETED", "VALIDATING"}:
            return "CHECKING", "正在检查 AI 研究结果", "已发现 AI 研究结果，系统正在执行研究校验。"
        return fallback_status, fallback_status_zh, fallback_reason_zh

    def _manual_task_rows(self, objective_id: str) -> list[dict[str, Any]]:
        self._objective(objective_id)
        from .research_factory.manual_handoff import HANDOFF_ID_RE, ManualAIResultWatcherV1, STAGING_ROOT_NAME

        root = self.root / STAGING_ROOT_NAME
        if not root.exists():
            return []
        rows: list[dict[str, Any]] = []
        for task_dir in sorted(root.iterdir()):
            if not task_dir.is_dir() or not HANDOFF_ID_RE.fullmatch(task_dir.name):
                continue
            ready_path = task_dir / "HANDOFF_READY.json"
            if not ready_path.exists():
                continue
            try:
                ready = self._read_json(ready_path, ttl_seconds=2.0)
            except ResearchConsoleReadError:
                continue
            if str(ready.get("objective_id")) != objective_id:
                continue
            inspected = ManualAIResultWatcherV1(self.root).inspect(task_dir.name)
            historical_invocation = self._manual_invocation_records(objective_id).get(task_dir.name)
            result_status = str(inspected.get("status") or "WAITING")
            result_status_zh = {"WAITING": "等待结果文件", "FOUND": "已发现，等待校验", "INVALID_JSON": "结果格式无效", "INVALID_TASK": "任务资料不可读"}.get(result_status, "结果状态待确认")
            result_reason_zh = str(inspected.get("reason_zh") or "尚未发现 AI 研究结果文件。")
            if historical_invocation:
                result_status, result_status_zh, result_reason_zh = self._manual_result_presentation(historical_invocation, result_status, result_status_zh, result_reason_zh)
            invocation_mode = str((historical_invocation or {}).get("ai_invocation_mode") or "MANUAL_HANDOFF")
            ingestion_status = str((historical_invocation or {}).get("ingestion_status") or "NOT_STARTED")
            row = {
                "handoff_id": task_dir.name,
                "objective_id": objective_id,
                "invocation_id": ready.get("ai_invocation_id"),
                "task_type": "AI 研究方案设计",
                "mode": invocation_mode,
                "mode_zh": {"MANUAL_HANDOFF": "手动 AI 交接（推荐）", "AUTO_CODEX": "自动调用 AI 研究员", "AI_DISABLED": "AI 研究已禁用"}.get(invocation_mode, "当前模式无法确认"),
                "task_dir": ready.get("task_dir"),
                "result_path": ready.get("result_path"),
                "expected_filename": ready.get("expected_filename"),
                "created_at": ready.get("created_at"),
                "updated_at": (historical_invocation or {}).get("accepted_at") or (historical_invocation or {}).get("completed_at") or ready.get("created_at"),
                "prompt_character_count": ready.get("prompt_character_count", 0),
                "prompt_token_estimate": ready.get("prompt_token_estimate", 0),
                "candidate_count": int((historical_invocation or {}).get("candidate_count", 0) or 0),
                "result_status": result_status,
                "result_status_zh": result_status_zh,
                "result_reason_zh": result_reason_zh,
                "ingestion_status": ingestion_status,
                "ingestion_status_zh": "已自动接入" if ingestion_status == "COMPLETED" else "尚未自动接入",
                "background_ai_token_consumption": 0,
                "manual_handoff_id": ready.get("manual_handoff_id") or task_dir.name,
                "task_purpose": ready.get("task_purpose") or "PROMISING_FOLLOWUP 机制确认设计",
                "current_round": ready.get("current_round") or "PROMISING_FOLLOWUP",
                "ai_design_policy": ready.get("ai_design_policy") or {},
                "planned_confirmation_candidate_limit": int(ready.get("planned_confirmation_candidate_limit", 0) or 0),
                "validation_status": (historical_invocation or {}).get("validation_status") or "NOT_RUN",
                "validation_stage": (historical_invocation or {}).get("validation_stage"),
                "validation_started_at": (historical_invocation or {}).get("validation_started_at"),
                "validation_last_activity_at": (historical_invocation or {}).get("validation_last_activity_at"),
                "validation_finished_at": (historical_invocation or {}).get("validation_finished_at"),
                "validation_error": (historical_invocation or {}).get("validation_error") or (historical_invocation or {}).get("error_code"),
                "validation_stale": False,
            }
            rows.append(row)
        return rows

    def list_ai_tasks(self, objective_id: str, *, page: int = 1, page_size: int = 20, search: str = "", status: str = "ALL", mode: str = "ALL", sort: str = "created_at", direction: str = "desc") -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ResearchConsoleReadError("INVALID_PAGE_SIZE", "分页参数超出允许范围", status_code=400)
        rows = self._manual_task_rows(objective_id)
        query = str(search).strip().casefold()
        if query:
            rows = [row for row in rows if query in json.dumps(row, ensure_ascii=False).casefold()]
        status_filter = str(status).strip().upper()
        if status_filter != "ALL":
            rows = [row for row in rows if str(row.get("result_status") or "").upper() == status_filter]
        mode_filter = str(mode).strip().upper()
        if mode_filter != "ALL":
            rows = [row for row in rows if str(row.get("mode") or "").upper() == mode_filter]
        sort_key = sort if sort in {"created_at", "result_status", "handoff_id"} else "created_at"
        rows.sort(key=lambda row: str(row.get(sort_key) or ""), reverse=str(direction).lower() != "asc")
        start = (page - 1) * page_size
        return {"schema_version": "research-console-ai-task-list-v1", "objective_id": objective_id, "page": page, "page_size": page_size, "total": len(rows), "items": rows[start:start + page_size], "source_generated_at": self._observed_at(), "observed_at": self._observed_at(), "freshness_state": FreshnessState.FRESH.value, "stale": False, "conflict": False}

    def list_ai_results(self, objective_id: str, *, page: int = 1, page_size: int = 20, search: str = "", sort: str = "created_at", direction: str = "desc") -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ResearchConsoleReadError("INVALID_PAGE_SIZE", "分页参数超出允许范围", status_code=400)
        rows = self._manual_task_rows(objective_id)
        query = str(search).strip().casefold()
        if query:
            rows = [row for row in rows if query in json.dumps(row, ensure_ascii=False).casefold()]
        rows = [row for row in rows if row.get("result_status") != "WAITING"]
        sort_key = sort if sort in {"created_at", "result_status", "handoff_id"} else "created_at"
        rows.sort(key=lambda row: str(row.get(sort_key) or ""), reverse=str(direction).lower() != "asc")
        start = (page - 1) * page_size
        return {"schema_version": "research-console-ai-result-list-v1", "objective_id": objective_id, "page": page, "page_size": page_size, "total": len(rows), "items": rows[start:start + page_size], "source_generated_at": self._observed_at(), "observed_at": self._observed_at(), "freshness_state": FreshnessState.FRESH.value, "stale": False, "conflict": False}

    def get_orchestrator(self, objective_id: str) -> Mapping[str, Any]:
        """Return the read-only Orchestrator V2 status projection."""

        self._objective(objective_id)
        try:
            from .research_factory.autonomous_orchestrator_v2 import AutonomousResearchOrchestratorV2

            payload = AutonomousResearchOrchestratorV2(self.root, objective_id=objective_id).status().to_dict()
            PerformanceBlindGuard.assert_blind(payload)
            return payload
        except (OSError, RuntimeError, ValueError, PerformanceLeakError) as exc:
            raise ResearchConsoleReadError("ORCHESTRATOR_SOURCE_UNAVAILABLE", "编排器 V2 状态暂时不可读", status_code=503) from exc

    def get_orchestrator_events(self, objective_id: str, *, limit: int = 50) -> dict[str, Any]:
        self._objective(objective_id)
        if limit < 1 or limit > 100:
            raise ResearchConsoleReadError("INVALID_EVENT_LIMIT", "事件读取数量超出允许范围", status_code=400)
        paths = [
            (self.root / "reports" / "research_orchestrator_v2" / objective_id / "orchestrator_events.jsonl", "orchestrator"),
            (self._runtime_paths(objective_id)["daemon_events.jsonl"], "daemon"),
        ]
        events: list[dict[str, Any]] = []
        latest_timestamp: datetime | None = None
        for path, source in paths:
            for item in self._read_jsonl_tail(path, limit=limit):
                event = {key: item.get(key) for key in ("event_id", "event_type", "timestamp", "created_at", "state", "new_state", "previous_state", "reason_code", "error_code", "candidate", "trial", "closeout_id", "submission_id") if item.get(key) is not None}
                event["source"] = source
                event["objective_id"] = objective_id
                event["timestamp"] = event.get("timestamp") or event.get("created_at")
                parsed = _parse_timestamp(event.get("timestamp"))
                if parsed is not None and (latest_timestamp is None or parsed > latest_timestamp):
                    latest_timestamp = parsed
                events.append(event)
        closeout_path = self._orchestrator_artifact_path(objective_id, "closeout.json")
        if not closeout_path.exists() and objective_id == "RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1":
            closeout_path = self.root / "reports" / "CURRENT_OBJECTIVE_AUTOMATIC_CLOSEOUT_V2.json"
        if closeout_path.exists():
            closeout = self._read_json(closeout_path, ttl_seconds=30.0)
            if str(closeout.get("objective_id") or objective_id) == objective_id:
                events.append({"event_id": f"closeout:{closeout.get('closeout_id')}", "event_type": "TERMINAL_CLOSEOUT_COMPLETED", "state": "TERMINAL_CLOSEOUT_COMPLETE", "reason_code": closeout.get("terminal_reason"), "timestamp": closeout.get("created_at"), "source": "closeout", "objective_id": objective_id})
        governance_path = self._orchestrator_artifact_path(objective_id, "governance_decision_required.json")
        if not governance_path.exists() and objective_id == "RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1":
            governance_path = self.root / "reports" / "RESEARCH_GOVERNANCE_DECISION_REQUIRED.json"
        if governance_path.exists():
            governance = self._read_json(governance_path, ttl_seconds=30.0)
            if str(governance.get("objective_id") or objective_id) == objective_id:
                events.append({"event_id": f"governance:{governance.get('decision_id')}", "event_type": "GOVERNANCE_DECISION_REQUIRED", "state": "GOVERNANCE_DECISION_REQUIRED", "reason_code": governance.get("reason"), "timestamp": governance.get("created_at"), "source": "governance", "objective_id": objective_id})
        submission_path = self.root / "reports" / "research_orchestrator_v2" / objective_id / "governance_decision_submission.json"
        if submission_path.exists():
            submission = self._read_json(submission_path, ttl_seconds=30.0)
            events.append({"event_id": f"submission:{submission.get('submission_id')}", "event_type": "GOVERNANCE_DECISION_RECORDED", "state": "RECORDED", "submission_id": submission.get("submission_id"), "timestamp": submission.get("created_at"), "source": "governance", "objective_id": objective_id})
        deduplicated = {str(item.get("event_id")): item for item in events if item.get("event_id")}
        ordered = sorted(deduplicated.values(), key=lambda item: str(item.get("timestamp") or ""))[-limit:]
        freshness = FreshnessState.UNKNOWN
        stale = False
        if latest_timestamp is not None:
            stale = (_now_utc(self._clock) - latest_timestamp).total_seconds() > 300
            freshness = FreshnessState.STALE if stale else FreshnessState.FRESH
        return {
            "schema_version": "research-orchestrator-events-view-v1",
            "objective_id": objective_id,
            "events": ordered,
            "limit": limit,
            "source_generated_at": latest_timestamp.isoformat() if latest_timestamp else None,
            "observed_at": self._observed_at(),
            "freshness_state": freshness.value,
            "stale": stale,
            "conflict": False,
        }

    def _ai_runtime_progress(self, objective_id: str, status: Mapping[str, Any], invocation: Mapping[str, Any] | None) -> dict[str, Any]:
        """Project objective-scoped runtime diagnostics without raw prompt/output."""

        invocation = invocation or {}
        handoff_id = str(status.get("current_handoff_id") or invocation.get("handoff_id") or "")
        invocation_id = str(status.get("current_ai_invocation_id") or invocation.get("ai_invocation_id") or "")
        diagnostics: Mapping[str, Any] = {}
        if _IDENTIFIER_RE.fullmatch(handoff_id) and _IDENTIFIER_RE.fullmatch(invocation_id):
            path = self.root / "reports" / "research_orchestrator_v2" / objective_id / "ai_staging" / handoff_id / invocation_id / "codex_runtime_diagnostics.json"
            if path.exists():
                diagnostics = self._read_json(path, ttl_seconds=2.0)

        runtime_status = str(diagnostics.get("status") or invocation.get("status") or "UNKNOWN")
        started_at = _timestamp_text(diagnostics.get("started_at") or invocation.get("started_at"))
        started = _parse_timestamp(started_at)
        elapsed_seconds = None
        if started is not None and runtime_status in {"STARTING", "RUNNING"}:
            elapsed_seconds = max(0, int((_now_utc(self._clock) - started).total_seconds()))
        status_labels = {
            "STARTING": "AI 进程正在启动",
            "RUNNING": "AI 进程运行中",
            "EXITED": "AI 进程已退出，等待结果处理",
            "TIMED_OUT": "AI 调用已超时",
            "ACCEPTED": "AI 结果已通过并接入",
            "INVALID": "AI 结果未通过校验",
            "ENGINEERING_BLOCKED": "AI 结果处理遇到工程阻断",
            "COMPLETED": "AI 结果生成已完成",
            "UNKNOWN": "暂未取得 AI 运行诊断",
        }
        manifest_detected_at = diagnostics.get("manifest_detected_at") or invocation.get("manual_result_detected_at")
        if runtime_status == "ACCEPTED":
            activity_zh = "AI 研究结果已通过检查并接入候选策略"
        elif runtime_status == "INVALID":
            activity_zh = "AI 研究结果未通过校验，未接入候选策略"
        elif runtime_status == "ENGINEERING_BLOCKED":
            activity_zh = "AI 研究结果处理遇到工程阻断"
        elif manifest_detected_at:
            activity_zh = "AI 已生成结果文件，等待 Orchestrator 校验"
        elif diagnostics.get("last_progress_at"):
            activity_zh = "AI 最近仍有输出，正在继续生成研究方案"
        elif runtime_status == "RUNNING":
            activity_zh = "AI 进程已运行，暂未收到可展示的输出"
        elif runtime_status == "STARTING":
            activity_zh = "AI 进程正在启动"
        else:
            activity_zh = status_labels.get(runtime_status, "AI 运行状态暂时无法确认")
        return {
            "status": runtime_status,
            "status_zh": status_labels.get(runtime_status, f"未知运行状态（{runtime_status}）"),
            "activity_zh": activity_zh,
            "process_id": diagnostics.get("process_id"),
            "started_at": started_at,
            "last_stdout_at": _timestamp_text(diagnostics.get("last_stdout_at")),
            "last_stderr_at": _timestamp_text(diagnostics.get("last_stderr_at")),
            "last_progress_at": _timestamp_text(diagnostics.get("last_progress_at")),
            "manifest_detected_at": _timestamp_text(manifest_detected_at),
            "process_exit_at": _timestamp_text(diagnostics.get("process_exit_at")),
            "timeout_type": diagnostics.get("timeout_type"),
            "output_mode": diagnostics.get("output_mode"),
            "elapsed_seconds": elapsed_seconds,
            "diagnostics_available": bool(diagnostics),
        }

    def get_ai_status(self, objective_id: str) -> dict[str, Any]:
        status = self.get_orchestrator(objective_id)
        invocation = status.get("last_ai_invocation") if isinstance(status.get("last_ai_invocation"), Mapping) else None
        handoff = status.get("current_handoff") if isinstance(status.get("current_handoff"), Mapping) else None
        acceptance_path = self.root / "reports" / "AUTONOMOUS_RESEARCH_ORCHESTRATOR_V2_CODEX_INVOCATION_ACCEPTANCE.json"
        acceptance = self._read_json(acceptance_path, ttl_seconds=60.0) if acceptance_path.exists() else {}
        real_status = str(acceptance.get("real_current_objective_smoke_test") or "NOT_RUN")
        if ";" in real_status:
            real_status = real_status.split(";", 1)[0].strip()
        result = {
            "schema_version": "research-console-ai-status-view-v1",
            "objective_id": objective_id,
            "automatic_invocation_enabled": bool(status.get("ai_auto_invocation_enabled", True)),
            "ai_invocation_mode": status.get("ai_invocation_mode", "MANUAL_HANDOFF"),
            "ai_invocation_mode_zh": status.get("ai_invocation_mode_zh", "手动 AI 交接（推荐）"),
            "manual_handoff": dict(status.get("manual_handoff") or {}),
            "background_ai_token_consumption": int(status.get("background_ai_token_consumption", 0) or 0),
            "state": status.get("ai_status", "IDLE"),
            "state_zh": status.get("ai_status_zh", "当前无需 AI 设计"),
            "last_invocation": dict(invocation) if invocation else None,
            "current_handoff": dict(handoff) if handoff else None,
            "invocation_id": status.get("current_ai_invocation_id"),
            "handoff_id": status.get("current_handoff_id"),
            "attempt": (status.get("retry_state") or {}).get("attempt", 0),
            "max_attempts": (status.get("retry_state") or {}).get("max_attempts", 0),
            "retry_state": dict(status.get("retry_state") or {}),
            "recent_result": (invocation or {}).get("status") if invocation else "NOT_RUN",
            "candidate_count": (invocation or {}).get("candidate_count") if invocation else 0,
            "batch_validation_status": (invocation or {}).get("validation_status") if invocation else "NOT_RUN",
            "validation": dict(status.get("validation") or {}),
            "auto_resume_status": (invocation or {}).get("resume_status") if invocation else "NOT_RUN",
            "runtime_progress": self._ai_runtime_progress(objective_id, status, invocation),
            "exact_once": {"status": "PASS", "message_zh": "同一研究任务不会并发启动多个 AI 研究员。"},
            "no_outcome_isolation": {"status": "PASS", "message_zh": "AI 设计阶段不会读取候选策略的具体收益、胜率、回撤或其他私有绩效结果。"},
            "real_codex_validation": {"scope": "PLATFORM", "functional_status": "已实现", "synthetic_flow_status": "已通过", "real_smoke_test": real_status},
            "freshness_state": status.get("freshness_state", "UNKNOWN"),
            "source_generated_at": status.get("source_generated_at"),
            "observed_at": status.get("observed_at"),
        }
        PerformanceBlindGuard.assert_blind(result)
        return result

    def _promising_history(self, objective_id: str) -> list[dict[str, Any]]:
        path = self.root / "reports" / "CURRENT_EFFECTIVE_RESEARCH_HISTORY_V1.json"
        if not path.exists():
            return []
        history = self._read_json(path, ttl_seconds=60.0)
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        scoped_candidate_ids = set(self._contract_candidates(objective_id))
        rows = history.get("rows") if isinstance(history.get("rows"), list) else []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            row_objective_id = str(row.get("objective_id") or (row.get("objective") or {}).get("objective_id") or "") if isinstance(row.get("objective"), Mapping) else str(row.get("objective_id") or "")
            if row_objective_id and row_objective_id != objective_id:
                continue
            decision = row.get("final_decision") if isinstance(row.get("final_decision"), Mapping) else {}
            classification = str(decision.get("effective_classification") or row.get("effective_classification") or "")
            if classification != "PROMISING":
                continue
            candidate_id = str(row.get("candidate_id") or decision.get("candidate_id") or "")
            if not row_objective_id and scoped_candidate_ids and candidate_id not in scoped_candidate_ids:
                continue
            if not candidate_id or candidate_id in seen:
                continue
            seen.add(candidate_id)
            result.append({
                "candidate_id": candidate_id,
                "trial_id": row.get("trial_id") or decision.get("original_trial_id"),
                "display_name_zh": _closeout_candidate_name(candidate_id),
                "classification": "PROMISING",
                "why_promising_zh": "该候选在本轮有效研究历史中保留了继续分析价值。",
                "why_not_research_passed_zh": "多重检验后的正式研究标准尚未满足，不能视为研究通过。",
                "risk_zh": "证据仍受研究轮次、统计校正和样本范围限制，不代表买入建议。",
            })
        return result

    def get_closeout(self, objective_id: str) -> dict[str, Any]:
        self._objective(objective_id)
        path = self._orchestrator_artifact_path(objective_id, "closeout.json")
        if not path.exists() and objective_id == "RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1":
            path = self.root / "reports" / "CURRENT_OBJECTIVE_AUTOMATIC_CLOSEOUT_V2.json"
        if not path.exists():
            raise ResearchConsoleReadError("CLOSEOUT_NOT_FOUND", "当前 objective 尚未生成自动收官报告", status_code=404)
        payload = self._read_json(path, ttl_seconds=30.0)
        if str(payload.get("objective_id") or objective_id) != objective_id:
            raise ResearchConsoleReadError("OBJECTIVE_SOURCE_MISMATCH", "收官报告不属于请求的 objective", status_code=409)
        budget = payload.get("budget_reconciliation") if isinstance(payload.get("budget_reconciliation"), Mapping) else {}
        counts = payload.get("effective_classification_reconciliation", {}).get("counts", {}) if isinstance(payload.get("effective_classification_reconciliation"), Mapping) else {}
        unevaluated = payload.get("unevaluated_candidates") if isinstance(payload.get("unevaluated_candidates"), Mapping) else {}
        source_time = _source_timestamp(payload, path)
        observed_time = _now_utc(self._clock)
        stale = (observed_time - source_time).total_seconds() > 180
        checkpoint_closeout_id = None
        try:
            status = self.get_orchestrator(objective_id)
            transition = status.get("last_transition") if isinstance(status.get("last_transition"), Mapping) else {}
            details = transition.get("details") if isinstance(transition.get("details"), Mapping) else {}
            checkpoint_closeout_id = details.get("closeout_id")
        except ResearchConsoleReadError:
            status = None
        conflict = bool(checkpoint_closeout_id and str(checkpoint_closeout_id) != str(payload.get("closeout_id")))
        freshness = FreshnessState.CONFLICT if conflict else FreshnessState.STALE if stale else FreshnessState.FRESH
        result = {
            "schema_version": "research-console-closeout-view-v1",
            "objective_id": objective_id,
            "title_zh": str(payload.get("title_zh") or payload.get("title") or f"{objective_id} 自动收官"),
            "closeout_id": payload.get("closeout_id"),
            "terminal_reason": payload.get("terminal_reason"),
            "terminal_reason_zh": display_reason(payload.get("terminal_reason"), include_code=False),
            "terminal_state_before": payload.get("terminal_state_before"),
            "terminal_state_after": payload.get("terminal_state_after"),
            "budget": {"used": budget.get("used", 0), "total": budget.get("total", 0), "remaining": budget.get("remaining", 0), "reserved": budget.get("reserved", 0), "delta": budget.get("delta", 0)},
            "trial_inventory": dict(payload.get("trial_inventory") or {}),
            "research_counts": {str(key): int(value) for key, value in counts.items()},
            "promising_candidates": self._promising_history(objective_id),
            "unevaluated_candidates": {"count": int(unevaluated.get("count", 0)), "candidate_ids": list(unevaluated.get("candidate_ids") or []), "classification": "UNEVALUATED_DUE_TO_BUDGET_EXHAUSTION", "explanation_zh": "这些候选策略不是研究失败，而是在本轮预测试验预算耗尽前尚未轮到正式验证。"},
            "robust_alpha_established": bool(payload.get("robust_alpha_established", False)),
            "robust_alpha_display_zh": "已建立稳健 Alpha 证据" if payload.get("robust_alpha_established") else "尚未建立稳健 Alpha 证据",
            "multiple_testing": dict(payload.get("multiple_testing_reconciliation") or {}),
            "exact_once": dict(payload.get("exact_once_audit") or {}),
            "automatic_reconciliation": {"status": "PASS", "message_zh": "自动收官已完成，预算、Trial 和终态记录已对账。"},
            "final_test_access": dict(payload.get("final_test_access") or {"analytical": 0, "decision": 0, "physical": 0}),
            "prospective": "DISABLED",
            "real_order": "DISABLED",
            "governance_required": True,
            "report": {"human_report_id": "AUTONOMOUS_RESEARCH__CURRENT_OBJECTIVE_AUTOMATIC_CLOSEOUT_V2.md", "machine_report_id": "AUTONOMOUS_RESEARCH__CURRENT_OBJECTIVE_AUTOMATIC_CLOSEOUT_V2.json"},
            "source_generated_at": source_time.isoformat(),
            "observed_at": observed_time.isoformat(),
            "freshness_state": freshness.value,
            "stale": stale,
            "conflict": conflict,
        }
        return result

    def get_governance_decision(self, objective_id: str) -> dict[str, Any]:
        self._objective(objective_id)
        path = self._orchestrator_artifact_path(objective_id, "governance_decision_required.json")
        if not path.exists() and objective_id == "RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1":
            path = self.root / "reports" / "RESEARCH_GOVERNANCE_DECISION_REQUIRED.json"
        structural_source = self._structural_governance(objective_id)
        if not path.exists() and structural_source:
            artifact, structural_path = structural_source
            try:
                governance_view = self.predictive_governance.readiness(objective_id)
            except PredictiveGovernanceError:
                governance_view = dict(artifact)
            options = [dict(item) for item in governance_view.get("choices", ()) if isinstance(item, Mapping)]
            structural_view = {
                "status": governance_view.get("status", artifact.get("status")),
                "status_zh": governance_view.get("status_zh") or "等待研究治理决定",
                "decision_mode": governance_view.get("decision_mode") or artifact.get("decision_mode"),
                "candidate_id": governance_view.get("candidate_id") or artifact.get("candidate_id"),
                "candidate_hash": governance_view.get("candidate_hash") or artifact.get("candidate_hash"),
                "structural": dict(governance_view.get("structural") or artifact.get("structural") or {}),
                "choices": options,
                "next_action": governance_view.get("next_action") or "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED",
                "next_action_zh": governance_view.get("next_action_zh") or artifact.get("next_action_zh"),
                "human_explanation_zh": governance_view.get("human_explanation_zh") or artifact.get("human_explanation_zh"),
                "final_test_access": dict(governance_view.get("final_test_access") or artifact.get("final_test_access") or {}),
                "prospective": governance_view.get("prospective") or artifact.get("prospective", "DISABLED"),
                "real_order": governance_view.get("real_order") or artifact.get("real_order", "DISABLED"),
                "predictive_trials_created": governance_view.get("predictive_trials_created", artifact.get("predictive_trials_created", 0)),
                "performance_access": governance_view.get("performance_access", artifact.get("performance_access", 0)),
                "reconciliation_ref": artifact.get("reconciliation_ref"),
                "reconciliation_id": governance_view.get("reconciliation_id") or artifact.get("reconciliation_id"),
                "available": governance_view.get("available", False),
                "latest_decision": governance_view.get("latest_decision"),
            }
            summary_status = {
                "AUTHORIZED": "STRUCTURAL_PASS_AUTHORIZED_WAITING_TO_START",
                "ENDED": "STRUCTURAL_PASS_RESEARCH_DIRECTION_ENDED",
                "DEFERRED": "STRUCTURAL_PASS_PREDICTIVE_AUTHORIZATION_DEFERRED",
            }.get(str(structural_view.get("status") or ""), "STRUCTURAL_PASS_WAITING_FOR_GOVERNANCE")
            return {
                "schema_version": "research-console-governance-decision-view-v1",
                "objective_id": objective_id,
                "decision_id": artifact.get("decision_id"),
                "decision_mode": artifact.get("decision_mode"),
                "status": structural_view.get("status"),
                "reason": structural_view.get("human_explanation_zh"),
                "choices": [],
                "structural_governance": structural_view,
                "current_terminal_summary": {"status": summary_status, "summary_zh": structural_view.get("human_explanation_zh") or structural_view.get("status_zh")},
                "forbidden_automatic_actions": ["自动启动预测试验", "访问预测绩效", "打开最终测试集", "启用真实订单"],
                "backend_level": "C",
                "execution_capability_zh": "当前只记录结构 PASS 后的治理决定；授权不会创建 Trial 或消耗预算。",
                "submission": None,
                "new_objective_created": False,
                "new_budget_created": False,
                "final_test_access": dict(structural_view.get("final_test_access") or {"analytical": 0, "decision": 0, "physical": 0}),
                "prospective": structural_view.get("prospective", "DISABLED"),
                "real_order": structural_view.get("real_order", "DISABLED"),
                "source_generated_at": _source_timestamp(artifact, structural_path).isoformat(),
                "observed_at": self._observed_at(),
                "freshness_state": FreshnessState.FRESH.value,
                "stale": False,
                "conflict": False,
            }
        if not path.exists():
            raise ResearchConsoleReadError("GOVERNANCE_NOT_FOUND", "当前 objective 没有待处理的治理决定", status_code=404)
        artifact = self._read_json(path, ttl_seconds=30.0)
        if str(artifact.get("objective_id") or objective_id) != objective_id:
            raise ResearchConsoleReadError("OBJECTIVE_SOURCE_MISMATCH", "治理决定不属于请求的 objective", status_code=409)
        allowed = artifact.get("allowed_choices") if isinstance(artifact.get("allowed_choices"), list) else []
        labels = {"STOP_RESEARCH": "结束当前研究", "START_PROMISING_FOLLOWUP_OBJECTIVE": "围绕有潜力策略继续研究", "START_NEW_MECHANISM_OBJECTIVE": "探索新的策略机制"}
        choices = []
        for item in allowed:
            if not isinstance(item, Mapping) or not item.get("choice"):
                continue
            choice = str(item["choice"])
            choices.append({"choice": choice, "label_zh": labels.get(choice, "研究治理选择"), "consequence_zh": item.get("consequence") or "由人工确认后按 canonical 治理流程处理。", "creates_new_objective": choice != "STOP_RESEARCH", "requires_new_budget": choice != "STOP_RESEARCH", "final_test_access_zh": "保持未访问", "real_order_zh": "保持禁用"})
        submission_path = self.root / "reports" / "research_orchestrator_v2" / objective_id / "governance_decision_submission.json"
        submission = self._read_json(submission_path, ttl_seconds=30.0) if submission_path.exists() else None
        result = {
            "schema_version": "research-console-governance-decision-view-v1",
            "objective_id": objective_id,
            "decision_id": artifact.get("decision_id"),
            "decision_mode": "STANDARD_TERMINAL_GOVERNANCE",
            "reason": artifact.get("reason"),
            "choices": choices,
            "structural_governance": None,
            "current_terminal_summary": dict(artifact.get("current_terminal_summary") or {}),
            "forbidden_automatic_actions": list(artifact.get("forbidden_automatic_actions") or []),
            "backend_level": str(artifact.get("backend_level") or "C"),
            "execution_capability_zh": "当前支持记录治理决定；下一研究目标尚未创建。",
            "submission": dict(submission) if submission else None,
            "new_objective_created": False,
            "new_budget_created": False,
            "final_test_access": {"analytical": 0, "decision": 0, "physical": 0},
            "prospective": "DISABLED",
            "real_order": "DISABLED",
            "source_generated_at": _source_timestamp(artifact, path).isoformat(),
            "observed_at": self._observed_at(),
            "freshness_state": FreshnessState.FRESH.value,
            "stale": False,
            "conflict": False,
        }
        return result

    def get_operations(self, objective_id: str) -> dict[str, Any]:
        self._objective(objective_id)
        from .research_factory.autonomous_orchestrator_v2 import OrchestratorControlServiceV1

        return OrchestratorControlServiceV1(self.root).operations(objective_id)

    def get_shadow_latest(self, objective_id: str) -> ShadowDailyView:
        self._objective(objective_id)
        reports = [path for path in (self.root / "reports").glob("EOD_RESEARCH_STOCK_SCAN_*.json") if re.fullmatch(r"EOD_RESEARCH_STOCK_SCAN_\d{8}\.json", path.name)]
        reports.sort(key=lambda path: (path.stem.rsplit("_", 1)[-1], path.stat().st_mtime_ns))
        if not reports:
            return ShadowDailyView(provenance=self._provenance(source_id="shadow_unavailable", path=None, payload=None, freshness=FreshnessState.UNKNOWN, stale=False, conflict=False), objective_id=objective_id, availability_reason_zh="当前没有找到正式的 Shadow 日扫描报告。")
        path = reports[-1]
        payload = self._read_json(path, ttl_seconds=60.0)
        trade_date = payload.get("SCAN_DATE") or payload.get("trade_date")
        readiness_path = self.root / "reports" / f"SHADOW_SCAN_READINESS_{trade_date.replace('-', '') if isinstance(trade_date, str) else trade_date}.json"
        readiness = self._read_json(readiness_path, ttl_seconds=60.0) if readiness_path.exists() else {}
        declared_objective_ids = payload.get("objective_ids") if isinstance(payload.get("objective_ids"), (list, tuple, set)) else ()
        declared_objective_id = str(payload.get("objective_id") or "")
        objective_scoped = declared_objective_id == objective_id or objective_id in {str(item) for item in declared_objective_ids}
        if not objective_scoped:
            return ShadowDailyView(
                provenance=self._provenance(source_id=path.name, path=path, payload=payload, freshness=FreshnessState.UNKNOWN, stale=False, conflict=False),
                objective_id=objective_id,
                available=False,
                objective_scoped=False,
                trade_date=trade_date,
                readiness="UNAVAILABLE",
                scan_status=str(payload.get("SCAN_STATUS") or payload.get("scan_status") or "UNSCOPED"),
                machine_marker="SHADOW_RESEARCH_ONLY",
                warning_zh="仅供研究观察，不代表买入建议。",
                availability_reason_zh="当前研究目标没有绑定的每日研究观察数据；已找到的报告仅属于平台级或其他研究范围。",
            )
        candidates = payload.get("TOP_OBSERVATION_3") or payload.get("top_observation_3") or payload.get("candidates") or ()
        pool = []
        for item in list(candidates)[:10]:
            if not isinstance(item, Mapping):
                continue
            pool.append({key: item.get(key) for key in ("symbol", "name", "matched_strategy", "rank", "qualification_reason", "PIT_status", "ST_status", "earliest_legal_execution_date") if key in item})
        execution = payload.get("execution_semantics") if isinstance(payload.get("execution_semantics"), Mapping) else {}
        return ShadowDailyView(provenance=self._provenance(source_id=path.name, path=path, payload=payload, freshness=FreshnessState.FRESH, stale=False, conflict=False), objective_id=objective_id, available=True, objective_scoped=True, availability_reason_zh="当前 Shadow 报告已明确绑定此研究目标。", trade_date=trade_date, readiness=str(payload.get("shadow_readiness") or readiness.get("readiness") or "UNKNOWN"), scan_status=str(payload.get("SCAN_STATUS") or payload.get("scan_status") or "UNKNOWN"), strategies_scanned=int(payload.get("STRATEGIES_SCANNED", 0) or 0), raw_signal_count=int(payload.get("RAW_SIGNAL_COUNT", 0) or 0), unique_candidate_count=int(payload.get("UNIQUE_CANDIDATE_COUNT", 0) or 0), observation_pool=tuple(pool), pit_state=str(readiness.get("reason") or "UNKNOWN"), tradability_state=str(payload.get("RECOMMENDATION_STATUS") or "SHADOW_RESEARCH_ONLY"), next_legal_session=execution.get("earliest_legal_execution_date"), machine_marker="SHADOW_RESEARCH_ONLY", warning_zh="仅供研究观察，不代表买入建议。")

    def get_data_health(self, objective_id: str) -> DataHealthView:
        self._objective(objective_id)
        sources: list[dict[str, Any]] = []
        capability_path = self.root / "data" / "research" / "data_capability.json"
        if capability_path.exists():
            capability = self._read_json(capability_path, ttl_seconds=60.0)
            for dataset in capability.get("datasets", ()):
                if isinstance(dataset, Mapping):
                    sources.append({"source_id": dataset.get("dataset_id"), "role": dataset.get("provider") or dataset.get("source"), "status": dataset.get("status", "UNKNOWN"), "coverage": dataset.get("coverage") or {"earliest_date": dataset.get("earliest_date"), "latest_date": dataset.get("latest_date")}, "last_update": dataset.get("latest_date") or capability.get("generated_at"), "missing": dataset.get("missing"), "unknown": dataset.get("unknown"), "partial": dataset.get("partial"), "canonical": True, "fallback": False})
        matrix_path = self.root / "reports" / "PLATFORM_DATA_SOURCE_CAPABILITY_MATRIX_V2.json"
        if matrix_path.exists():
            matrix = self._read_json(matrix_path, ttl_seconds=60.0)
            for source in matrix.get("sources", ()):
                if isinstance(source, Mapping):
                    sources.append({"source_id": source.get("source"), "role": source.get("role"), "status": source.get("status", "UNKNOWN"), "coverage": source.get("coverage"), "last_update": matrix.get("generated_at"), "missing": source.get("missing"), "unknown": source.get("unknown"), "partial": source.get("partial"), "canonical": bool(source.get("canonical", False)), "fallback": bool(source.get("fallback", False))})
        pit_manifest_path = self.root / "data" / "research" / "security_state" / "normalized" / "manifest.json"
        if pit_manifest_path.exists():
            pit_manifest = self._read_json(pit_manifest_path, ttl_seconds=120.0)
            coverage = pit_manifest.get("coverage") if isinstance(pit_manifest.get("coverage"), Mapping) else {}
            sources.append({"source_id": "PIT_UNIVERSE_DATASET_V2", "role": "PIT security state", "status": "READY" if pit_manifest.get("dataset_version") else "UNKNOWN", "coverage": {"start": pit_manifest.get("coverage_start"), "end": pit_manifest.get("coverage_end"), "trade_day_count": pit_manifest.get("trade_day_count"), "security_count": pit_manifest.get("security_count"), "coverage_summary": {"active_symbol_days": coverage.get("active_symbol_days"), "unknown_symbol_days": coverage.get("st_unknown_symbol_days")}}, "last_update": pit_manifest.get("build_timestamp"), "missing": None, "unknown": bool(coverage.get("universe_unknown_symbol_days", 0)), "partial": bool(coverage.get("st_unknown_symbol_days", 0) or coverage.get("suspension_unknown_symbol_days", 0)), "canonical": True, "fallback": False})
        shadow_security_path = self.root / "reports" / "SHADOW_SECURITY_STATE_20260825.json"
        if shadow_security_path.exists():
            shadow_security = self._read_json(shadow_security_path, ttl_seconds=120.0)
            sources.append({"source_id": "SHADOW_SECURITY_STATE", "role": "Shadow security state", "status": "READY" if shadow_security.get("historical_research_pit_modified") is False else "UNKNOWN", "coverage": shadow_security.get("counts"), "last_update": shadow_security.get("generated_at"), "missing": None, "unknown": bool((shadow_security.get("counts") or {}).get("unknown", 0)), "partial": False, "canonical": True, "fallback": False})
        trade_calendar_path = self.root / "data" / "research" / "security_state" / "raw" / "trade_calendar.json"
        if trade_calendar_path.exists():
            sources.append({"source_id": "trade_calendar", "role": "trade calendar", "status": "READY", "coverage": "manifest-scoped", "last_update": _timestamp_text(datetime.fromtimestamp(trade_calendar_path.stat().st_mtime, timezone.utc)), "missing": None, "unknown": False, "partial": False, "canonical": True, "fallback": False})
        minute_manifest_path = self.root / "data" / "research" / "market_5m" / "manifest_v1.jsonl"
        if minute_manifest_path.exists():
            sources.append({"source_id": "minute_5_ohlcva", "role": "5-minute research capability", "status": "READY", "coverage": "manifest-scoped", "last_update": _timestamp_text(datetime.fromtimestamp(minute_manifest_path.stat().st_mtime, timezone.utc)), "missing": None, "unknown": False, "partial": False, "canonical": True, "fallback": False})
        shadow = self.get_shadow_latest(objective_id)
        sources.extend([{ "source_id": "current_shadow_data", "role": "current Shadow data", "status": shadow.readiness, "coverage": {"trade_date": shadow.trade_date, "raw_signal_count": shadow.raw_signal_count}, "last_update": shadow.source_generated_at, "missing": shadow.availability_reason_zh if not shadow.available else None, "unknown": not shadow.available or shadow.readiness == "UNKNOWN", "partial": shadow.readiness.startswith("PARTIAL") or not shadow.available, "canonical": shadow.objective_scoped, "fallback": False, "scope": "OBJECTIVE" if shadow.objective_scoped else "UNSCOPED" }])
        for index, source in enumerate(sources, start=1):
            source["source_key"] = f"{source.get('source_id') or 'source'}#{index}"
            source.setdefault("scope", "PLATFORM")
        degraded_statuses = {"UNKNOWN", "UNAVAILABLE", "UNSCOPED", "PARTIAL", "PARTIAL_SAFE", "ONLINE_ONLY", "CONTRACT_GATED", "READY_WITH_UNKNOWN_FAIL_CLOSED"}
        degraded = any(bool(item.get("unknown") or item.get("partial") or str(item.get("status") or "").upper() in degraded_statuses) for item in sources)
        overall = "PARTIAL" if sources and degraded else "READY" if sources else "UNKNOWN"
        path = capability_path if capability_path.exists() else matrix_path if matrix_path.exists() else None
        return DataHealthView(provenance=self._provenance(source_id="data_capability+platform_matrix+shadow", path=path, payload=None, freshness=FreshnessState.FRESH if path else FreshnessState.UNKNOWN, stale=False, conflict=False), objective_id=objective_id, overall_status=overall, sources=tuple(sources))

    def _safety_counters(self) -> dict[str, Any]:
        for name in ("PLATFORM_ARCHITECTURE_MANIFEST_V2.json", "EOD_RESEARCH_STOCK_SCAN_20260825.json", "DATA_ACQUISITION_AND_DOWNLOAD_CAPABILITY_AUDIT_V1.json"):
            path = self.root / "reports" / name
            if not path.exists():
                continue
            payload = self._read_json(path, ttl_seconds=60.0)
            safety = payload.get("safety") if isinstance(payload.get("safety"), Mapping) else payload.get("audit") if isinstance(payload.get("audit"), Mapping) else {}
            if safety:
                return {key: safety.get(key) for key in ("NEW_PREDICTIVE_TRIALS", "PREDICTIVE_TRIALS", "PREDICTIVE_BUDGET_DELTA", "PERFORMANCE_ACCESS", "FINAL_TEST_ACCESS", "PROSPECTIVE", "PROSPECTIVE_ACCESS", "REAL_ORDER") if key in safety}
        return {"NEW_PREDICTIVE_TRIALS": 0, "PREDICTIVE_BUDGET_DELTA": 0, "PERFORMANCE_ACCESS": 0, "FINAL_TEST_ACCESS": {"analytical": 0, "decision": 0, "physical": 0}, "PROSPECTIVE": 0, "REAL_ORDER": "DISABLED"}

    def get_dashboard(self, objective_id: str) -> ResearchDashboardView:
        self._objective(objective_id)
        daemon = self.get_daemon(objective_id)
        budget = self.get_budget(objective_id, include_trial_consumption=False)
        status = self.get_research_status(objective_id)
        try:
            orchestrator = self.get_orchestrator(objective_id)
        except ResearchConsoleReadError:
            orchestrator = None
        health = self.get_daemon_health(objective_id)
        shadow = self.get_shadow_latest(objective_id)
        data = self.get_data_health(objective_id)
        safety = self._safety_counters()
        snapshot_conflict = daemon.conflict or budget.conflict
        snapshot_stale = daemon.stale or budget.stale
        snapshot_freshness = FreshnessState.CONFLICT if snapshot_conflict else FreshnessState.STALE if snapshot_stale else FreshnessState.FRESH
        snapshot_provenance = ProvenanceView(source_id="research_console_dashboard", source_generated_at=daemon.source_generated_at, observed_at=self._observed_at(), freshness_state=snapshot_freshness.value, stale=snapshot_stale, conflict=snapshot_conflict)
        orchestrator_state = str(orchestrator.get("orchestrator_state")) if isinstance(orchestrator, Mapping) else None
        orchestrator_counts = orchestrator.get("research_counts", {}) if isinstance(orchestrator, Mapping) and isinstance(orchestrator.get("research_counts"), Mapping) else {}
        orchestrator_state_zh = str(orchestrator.get("orchestrator_state_zh") or display_state(orchestrator_state or "UNKNOWN")) if isinstance(orchestrator, Mapping) else "未知状态（未提供）"
        structural_view = self._current_structural(objective_id)
        trial_records = self._trial_records(objective_id)
        latest_trial = self._latest_trial_record(trial_records)
        latest_trial_completed = str(latest_trial.get("status") or "").upper() == "COMPLETED" or bool(latest_trial.get("performance_complete") or latest_trial.get("performance_completed"))
        engineering_failure = (
            str(latest_trial.get("status") or "").upper() == "INVALIDATED"
            and str(latest_trial.get("classification") or "").upper() == "ENGINEERING_INVALIDATED"
        )
        try:
            predictive_trial_recovery = self.predictive_trial_start.recovery_readiness(objective_id)
        except (PredictiveTrialStartError, ValueError, OSError, KeyError, TypeError) as exc:
            predictive_trial_recovery = {
                "status": "UNAVAILABLE",
                "status_zh": "当前不可恢复",
                "available": False,
                "objective_id": objective_id,
                "action": "RESUME_PREDICTIVE_TRIAL_1",
                "action_zh": "恢复第 1 次预测试验",
                "confirmation_required": True,
                "reason_code": exc.code if isinstance(exc, PredictiveTrialStartError) else "PREDICTIVE_TRIAL_RESUME_SOURCE_UNAVAILABLE",
                "reason_zh": exc.message_zh if isinstance(exc, PredictiveTrialStartError) else "当前 canonical 恢复状态暂时不可读取",
            }
        structural_governance = self._structural_governance(objective_id)
        predictive_governance = None
        if structural_governance:
            try:
                predictive_governance = self.predictive_governance.readiness(objective_id)
            except PredictiveGovernanceError:
                predictive_governance = None
        governance_payload = dict(predictive_governance or (structural_governance[0] if structural_governance else {}))
        governance_pending = str(governance_payload.get("status") or "") in {"PENDING_HUMAN_DECISION", "DEFERRED"}
        predictive_authorization_boundary = (
            not latest_trial_completed
            and daemon.required_human_ai_action == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
            or governance_pending
            or (
                str(governance_payload.get("status") or "") == "AUTHORIZED"
                and not trial_records
            )
        )
        if latest_trial_completed:
            orchestrator_state_zh = f"预测验证已完成，最终分类：{display_classification(latest_trial.get('classification'))}"
        elif governance_payload.get("status") == "AUTHORIZED":
            orchestrator_state_zh = "已授权进入第 1 次预测试验，等待启动预测试验"
        elif governance_payload.get("status") == "ENDED":
            orchestrator_state_zh = "当前 Candidate 研究路径已结束"
        elif governance_pending:
            orchestrator_state_zh = "结构预检已通过，等待你授权第 1 次预测试验"
        elif predictive_authorization_boundary:
            orchestrator_state_zh = "结构预检已通过，等待你授权第 1 次预测试验"
        if engineering_failure:
            orchestrator_state_zh = "第 1 次预测试验因工程问题未完成。"
        lifecycle_stage = _lifecycle_stage(orchestrator_state=orchestrator_state or daemon.daemon_state, daemon_stage=daemon.stage, candidate_count=len(self._current_contract_candidates(objective_id)), trial_count=len(trial_records))
        if latest_trial_completed:
            lifecycle_stage = "FINAL_CLASSIFICATION"
        elif structural_view and structural_view.status == "PASS" and not trial_records:
            lifecycle_stage = "PREDICTIVE"
        elif engineering_failure:
            lifecycle_stage = "PREDICTIVE"
        current_candidate_id = None
        if isinstance(orchestrator, Mapping) and isinstance(orchestrator.get("current_candidate"), Mapping):
            current_candidate_id = str(orchestrator["current_candidate"].get("candidate_id")) if orchestrator["current_candidate"].get("candidate_id") else None
        running_states = {"ACTIVE", "LOCAL_RESEARCH_RUNNING", "NEED_AI_RESEARCH_DESIGN", "AI_HANDOFF_PREPARING", "AI_INVOCATION_PENDING", "AI_INVOCATION_RUNNING", "AI_OUTPUT_VALIDATING", "AI_BATCH_INGESTING", "LOCAL_RESEARCH_RESUMING"}
        research_running = orchestrator_state in running_states if orchestrator_state else daemon.daemon_state in {"STRUCTURAL_RUNNING", "PREDICTIVE_RUNNING", "STRUCTURAL_PENDING", "PREDICTIVE_PENDING"}
        if predictive_authorization_boundary:
            research_running = False
        dashboard_candidate_id = current_candidate_id if orchestrator is not None else daemon.current_candidate_id
        dashboard_candidate_id = dashboard_candidate_id or (structural_view.candidate_id if structural_view else None)
        required_action = "CANDIDATE_PREDICTIVE_VALIDATION_COMPLETE" if latest_trial_completed else "RESUME_PREDICTIVE_TRIAL_1" if predictive_trial_recovery.get("available") else governance_payload.get("next_action") if predictive_governance and governance_payload.get("status") in {"PENDING_HUMAN_DECISION", "DEFERRED", "AUTHORIZED", "ENDED"} else "GOVERNANCE_DECISION_REQUIRED" if orchestrator_state == "GOVERNANCE_DECISION_REQUIRED" else "AI_MANUAL_HANDOFF_REQUIRED" if orchestrator_state == "AI_MANUAL_HANDOFF_REQUIRED" else daemon.required_human_ai_action
        governance_readiness = {
            "status": "COMPLETED" if latest_trial_completed else governance_payload.get("status", "NOT_AVAILABLE"),
            "status_zh": "本候选已完成预测验证" if latest_trial_completed else governance_payload.get("status_zh") or ("等待研究治理决定" if governance_pending else "当前没有结构治理待决状态"),
            "available": False if latest_trial_completed else bool(governance_payload.get("available", governance_pending)),
            "decision_mode": governance_payload.get("decision_mode"),
            "candidate_id": governance_payload.get("candidate_id") or (structural_view.candidate_id if structural_view else None),
            "candidate_hash": governance_payload.get("candidate_hash") or (structural_view.candidate_hash if structural_view else None),
            "structural": dict(governance_payload.get("structural") or (structural_view.to_dict() if structural_view else {})),
            "choices": [dict(item) for item in governance_payload.get("choices", ()) if isinstance(item, Mapping)],
            "next_action": "CANDIDATE_PREDICTIVE_VALIDATION_COMPLETE" if latest_trial_completed else governance_payload.get("next_action") or "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED",
            "next_action_zh": f"预测验证已完成，最终分类：{display_classification(latest_trial.get('classification'))}" if latest_trial_completed else governance_payload.get("next_action_zh") or "等待研究治理决定是否启动第 1 次预测试验。",
            "human_explanation_zh": "本候选已形成正式预测验证结果，不能重复授权或重跑。" if latest_trial_completed else governance_payload.get("human_explanation_zh") or "结构预检已通过，等待决定是否进入正式预测验证。",
            "final_test_access": dict(governance_payload.get("final_test_access") or {"analytical": 0, "decision": 0, "physical": 0}),
            "prospective": governance_payload.get("prospective", "DISABLED"),
            "real_order": governance_payload.get("real_order", "DISABLED"),
            "predictive_trials_created": len(trial_records),
            "performance_access": int(governance_payload.get("performance_access", 0)),
            "reconciliation_ref": governance_payload.get("reconciliation_ref"),
            "reconciliation_id": governance_payload.get("reconciliation_id"),
            "budget_snapshot": dict(governance_payload.get("budget_snapshot") or {}),
            "trial_created": False if latest_trial_completed else bool(governance_payload.get("trial_created", False)),
            "budget_reserved": 0 if latest_trial_completed else int(governance_payload.get("budget_reserved", 0) or 0),
            "budget_used_delta": 0 if latest_trial_completed else int(governance_payload.get("budget_used_delta", 0) or 0),
        }
        return ResearchDashboardView(provenance=snapshot_provenance, objective_id=objective_id, research_running=research_running, daemon_state=daemon.daemon_state, orchestrator_state=orchestrator_state or "UNKNOWN", orchestrator_state_zh=orchestrator_state_zh, stage=lifecycle_stage, current_candidate_id=dashboard_candidate_id, remaining_frozen_candidates=daemon.remaining_frozen_candidates, budget_used=budget.used, budget_total=budget.total, budget_remaining=budget.remaining, budget_conflict=budget.conflict, research_passed_count=int(orchestrator_counts.get("RESEARCH_PASSED", status.counts.get("RESEARCH_PASSED", 0))), promising_count=int(orchestrator_counts.get("PROMISING", status.counts.get("PROMISING", 0))), required_human_action=required_action, engineering_blocked=daemon.daemon_state == "ENGINEERING_BLOCKED" or engineering_failure, resource_health=health.health_state, shadow_latest_state=shadow.readiness, data_health_state=data.overall_status, predictive_trial_count=len(trial_records), safety_counters=safety, display={"daemon_state_zh": daemon.state_display_zh, "orchestrator_state_zh": orchestrator_state_zh, "stage_zh": display_state(lifecycle_stage), "resource_health_zh": health.health_display_zh, "shadow_warning_zh": shadow.warning_zh}, structural=structural_view.to_dict() if structural_view else {}, governance_readiness=governance_readiness, predictive_trial_recovery=predictive_trial_recovery)

    def get_pipeline(self, objective_id: str) -> ResearchPipelineView:
        daemon = self.get_daemon(objective_id)
        candidates = self._contract_candidates(objective_id)
        current_candidates = self._current_contract_candidates(objective_id)
        trials = self._trial_records(objective_id)
        try:
            orchestrator = self.get_orchestrator(objective_id)
        except ResearchConsoleReadError:
            orchestrator = {}
        orchestrator_state = str(orchestrator.get("orchestrator_state") or daemon.daemon_state)
        orchestrator_state_zh = str(orchestrator.get("orchestrator_state_zh") or display_state(orchestrator_state))
        checkpoint_path = self._runtime_paths(objective_id)["daemon_checkpoint.json"]
        checkpoint = self._read_json(checkpoint_path, ttl_seconds=5.0) if checkpoint_path.exists() else {}
        canonical_refs = checkpoint.get("canonical_refs") if isinstance(checkpoint.get("canonical_refs"), Mapping) else {}
        structural_governance_source = self._structural_governance(objective_id)
        predictive_governance = None
        if structural_governance_source:
            try:
                predictive_governance = self.predictive_governance.readiness(objective_id)
            except PredictiveGovernanceError:
                predictive_governance = None
        structural_governance_payload = dict(predictive_governance or (structural_governance_source[0] if structural_governance_source else {}))
        last_structural = canonical_refs.get("last_structural_result") if isinstance(canonical_refs.get("last_structural_result"), Mapping) else {}
        structural_details = last_structural.get("details") if isinstance(last_structural.get("details"), Mapping) else {}
        last_candidate = checkpoint.get("last_completed_candidate") if isinstance(checkpoint.get("last_completed_candidate"), Mapping) else {}
        correction_candidate_id = str(last_candidate.get("candidate_id") or structural_details.get("candidate_id") or "")
        correction = load_effective_contract_invalidations(self.root, objective_id).get(correction_candidate_id)
        if correction:
            correction_reason = str(correction.get("reason_code") or "FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE")
            structural_details = {
                "candidate_id": correction_candidate_id,
                "candidate_hash": correction.get("candidate_hash"),
                "blocking_reason_codes": [correction_reason],
                "generated_at": correction.get("created_at"),
                "correction_event_id": correction.get("event_id"),
            }
            last_structural = {"status": "ENGINEERING_BLOCKED", "reason_code": correction_reason, "details": structural_details}
        current_stage = _lifecycle_stage(orchestrator_state=orchestrator_state, daemon_stage=daemon.stage, candidate_count=len(current_candidates), trial_count=len(trials))
        failed_structural_statuses = {"UNKNOWN", "BLOCKED", "ENGINEERING_BLOCKED"}
        if last_structural and not trials and str(last_structural.get("status") or "") in failed_structural_statuses:
            current_stage = "STRUCTURAL"
        elif last_structural and not trials and str(last_structural.get("status") or "") == "PASS":
            current_stage = "PREDICTIVE"
        completed_trials = [item for item in trials.values() if str(item.get("status") or "").upper() == "COMPLETED"]
        latest_objective_trial = self._latest_trial_record(trials)
        engineering_failure = (
            str(latest_objective_trial.get("status") or "").upper() == "INVALIDATED"
            and str(latest_objective_trial.get("classification") or "").upper() == "ENGINEERING_INVALIDATED"
        )
        if completed_trials:
            current_stage = "FINAL_CLASSIFICATION"
        elif engineering_failure:
            current_stage = "PREDICTIVE"
            orchestrator_state_zh = "第 1 次预测试验因工程问题未完成。"
        structural_pass_waiting_prediction = bool(last_structural and not trials and str(last_structural.get("status") or "") == "PASS")
        if structural_pass_waiting_prediction and predictive_governance:
            governance_projection_status = str(structural_governance_payload.get("status") or "")
            if governance_projection_status == "AUTHORIZED":
                orchestrator_state_zh = "已授权进入第 1 次预测试验，等待启动预测试验"
            elif governance_projection_status == "ENDED":
                orchestrator_state_zh = "当前 Candidate 研究路径已结束"
            elif governance_projection_status in {"PENDING_HUMAN_DECISION", "DEFERRED"}:
                orchestrator_state_zh = "结构预检已通过，等待人工决定是否授权进入第 1 次预测试验"
        final_classification = str(completed_trials[-1].get("classification") or "") if completed_trials else ""
        current_index = _PIPELINE_STAGES.index(current_stage)
        stages = tuple(
            {
                "stage": name,
                "state_display_zh": "结构预检结果仍无法确认" if name == "STRUCTURAL" and last_structural and str(last_structural.get("status") or "") == "UNKNOWN" else "未通过" if name == "STRUCTURAL" and last_structural and str(last_structural.get("status") or "") in failed_structural_statuses else "第 1 次预测试验因工程问题未完成。" if name == "PREDICTIVE" and engineering_failure else "等待单独授权预测验证" if name == "PREDICTIVE" and structural_pass_waiting_prediction else display_classification(final_classification) if name == "FINAL_CLASSIFICATION" and completed_trials else orchestrator_state_zh if name == current_stage else "已完成" if index < current_index else "未到达",
                "status": "FAILED" if (name == "PREDICTIVE" and engineering_failure) or (name == "STRUCTURAL" and last_structural and str(last_structural.get("status") or "") in failed_structural_statuses) else "DONE" if completed_trials and index <= current_index else "CURRENT" if name == current_stage else "DONE" if index < current_index else "PENDING",
            }
            for index, name in enumerate(_PIPELINE_STAGES)
        )
        batches = tuple(sorted({str(item.get("source_provenance", {}).get("batch_id")) for item in current_candidates.values() if isinstance(item.get("source_provenance"), Mapping) and item.get("source_provenance", {}).get("batch_id")}))
        try:
            event_view = self.get_orchestrator_events(objective_id, limit=20)
            events = event_view.get("events", ())
        except ResearchConsoleReadError:
            events = ()
        recent = tuple(dict(item) for item in events[-20:] if isinstance(item, Mapping))
        invocation = orchestrator.get("last_ai_invocation") if isinstance(orchestrator.get("last_ai_invocation"), Mapping) else {}
        invocation_status = str(invocation.get("status") or "").upper()
        invocation_error = str(invocation.get("error_code")) if invocation_status in {"FAILED", "INVALID", "ENGINEERING_BLOCKED"} and invocation.get("error_code") else None
        last_error = invocation_error or daemon.last_error
        current_candidate = orchestrator.get("current_candidate") if isinstance(orchestrator.get("current_candidate"), Mapping) else {}
        current_candidate_id = str(current_candidate.get("candidate_id")) if current_candidate.get("candidate_id") else (daemon.current_candidate_id if not orchestrator else None)
        source_times = [
            _parse_timestamp(orchestrator.get("source_generated_at")),
            _parse_timestamp(daemon.source_generated_at),
            _parse_timestamp(checkpoint.get("checkpoint_at")),
        ]
        source_generated_at = max((item for item in source_times if item is not None), default=None)
        stale = bool(orchestrator.get("stale", False) or daemon.stale)
        conflict = bool(orchestrator.get("conflict", False) or daemon.conflict)
        freshness = FreshnessState.CONFLICT if conflict else FreshnessState.STALE if stale else FreshnessState.FRESH if source_generated_at else FreshnessState.UNKNOWN
        provenance = ProvenanceView(source_id="orchestrator+daemon+canonical_candidates", source_generated_at=source_generated_at.isoformat() if source_generated_at else daemon.source_generated_at, observed_at=self._observed_at(), freshness_state=freshness.value, stale=stale, conflict=conflict)
        validation = orchestrator.get("validation") if isinstance(orchestrator.get("validation"), Mapping) else {}
        process_pid = _int_or_none(validation.get("process_id"))
        process_alive = bool(validation.get("process_alive"))
        structural_status = str(last_structural.get("status") or "NOT_RUN")
        structural_reason = str(last_structural.get("reason_code") or structural_details.get("decision_rule") or "")
        completed_candidate_id = str(last_candidate.get("candidate_id") or structural_details.get("candidate_id") or "") or None
        candidate_trials = [item for item in trials.values() if completed_candidate_id and str(item.get("candidate_id") or "") == completed_candidate_id]
        latest_trial = self._latest_trial_record({str(item.get("trial_id") or index): item for index, item in enumerate(candidate_trials)}) if candidate_trials else {}
        candidate_trial_completed = str(latest_trial.get("status") or "").upper() == "COMPLETED" or bool(latest_trial.get("performance_complete") or latest_trial.get("performance_completed"))
        predictive_status = str(latest_trial.get("status") or "UNKNOWN") if latest_trial else "NOT_RUN"
        predictive_classification = str(latest_trial.get("classification") or "") or None
        predictive_reason_codes = [str(item) for item in latest_trial.get("reason_codes", ())]
        if process_alive:
            execution_status = "RUNNING"
            execution_status_zh = "后台执行进程正在运行"
        elif structural_status == "UNKNOWN":
            execution_status = "STRUCTURAL_FAILED"
            execution_status_zh = "结构预检结果仍无法确认，未进入预测试验"
        elif structural_status in failed_structural_statuses:
            execution_status = "STRUCTURAL_FAILED"
            execution_status_zh = "结构预检未通过，未进入预测试验"
        elif completed_candidate_id and predictive_status != "NOT_RUN":
            execution_status = "COMPLETED"
            execution_status_zh = "候选执行已完成"
        elif engineering_failure:
            execution_status = "ENGINEERING_BLOCKED"
            execution_status_zh = "第 1 次预测试验因工程问题未完成"
        elif completed_candidate_id and structural_status == "PASS":
            execution_status = "PREDICTIVE_NOT_RUN"
            execution_status_zh = orchestrator_state_zh if predictive_governance else "结构预检已通过，等待预测验证授权"
        elif completed_candidate_id:
            execution_status = "PREDICTIVE_NOT_RUN"
            execution_status_zh = "候选已处理，但预测试验未运行"
        else:
            execution_status = "NOT_RUN"
            execution_status_zh = "尚未运行候选"
        execution = {
            "status": execution_status,
            "status_zh": execution_status_zh,
            "process_alive": process_alive,
            "process_pid": process_pid,
            "process_state": "RUNNING" if process_alive else "STOPPED" if process_pid else "NOT_STARTED",
            "process_state_zh": "运行中" if process_alive else "已停止" if process_pid else "未启动",
            "candidate_id": current_candidate_id or completed_candidate_id,
            "candidate_completed": bool(completed_candidate_id),
            "structural_status": structural_status,
            "structural_status_zh": "结构预检结果仍无法确认" if structural_status == "UNKNOWN" else "未通过" if structural_status in failed_structural_statuses else "已通过" if structural_status == "PASS" else "未运行",
            "structural_reason_code": structural_reason or None,
            "structural_reason_zh": "结构预检结果仍无法确认" if structural_status == "UNKNOWN" else display_reason(structural_reason, include_code=False) if structural_reason else None,
            "structural_reason_codes": [str(item) for item in structural_details.get("blocking_reason_codes", ())],
            "predictive_status": predictive_status,
            "predictive_status_zh": "第 1 次预测试验因工程问题未完成" if engineering_failure else "未运行" if predictive_status == "NOT_RUN" else display_status(predictive_status),
            "predictive_classification": predictive_classification,
            "predictive_classification_zh": display_classification(predictive_classification) if predictive_classification else None,
            "predictive_reason_codes": predictive_reason_codes,
            "predictive_reason_zh": format_reason(predictive_reason_codes[0], include_code=False) if predictive_reason_codes else None,
            "completed_at": latest_trial.get("finished_at") or latest_trial.get("last_activity_at") or structural_details.get("generated_at") or checkpoint.get("checkpoint_at"),
        }
        predictive_authorization_pending = structural_status == "PASS" and predictive_status == "NOT_RUN"
        governance_status = str(structural_governance_payload.get("status") or "")
        governance_pending = bool(predictive_governance and governance_status in {"PENDING_HUMAN_DECISION", "DEFERRED"} and predictive_authorization_pending)
        governance_authorized = bool(predictive_governance and governance_status == "AUTHORIZED" and predictive_authorization_pending)
        governance_ended = bool(predictive_governance and governance_status == "ENDED" and predictive_authorization_pending)
        next_action = "CANDIDATE_PREDICTIVE_VALIDATION_COMPLETE" if completed_trials else "RESUME_PREDICTIVE_TRIAL_1" if engineering_failure else str(structural_governance_payload.get("next_action") or "") if predictive_governance and governance_status in {"PENDING_HUMAN_DECISION", "DEFERRED", "AUTHORIZED", "ENDED"} else "STRUCTURAL_PASS_PREDICTIVE_AUTHORIZATION_REQUIRED" if governance_pending else "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED" if predictive_authorization_pending else str(orchestrator.get("next_action") or orchestrator_state)
        next_action_zh = f"预测验证已完成，最终分类：{display_classification(final_classification)}" if completed_trials else "工程问题已修复，等待人工确认恢复第 1 次预测试验" if engineering_failure else str(structural_governance_payload.get("next_action_zh") or "等待研究治理决定是否启动第 1 次预测试验。") if predictive_governance and governance_status in {"PENDING_HUMAN_DECISION", "DEFERRED", "AUTHORIZED", "ENDED"} else "结构预检已通过，等待单独授权预测验证" if predictive_authorization_pending else str(orchestrator.get("next_action_zh") or orchestrator_state_zh)
        authorization_candidate_id = current_candidate_id or completed_candidate_id
        structural_reconciliation_available = (
            structural_status in failed_structural_statuses
            and predictive_status == "NOT_RUN"
            and not candidate_trials
            and bool(completed_candidate_id)
            and str(checkpoint.get("current_state") or "") == "READY"
            and checkpoint.get("current_trial") is None
        )
        structural_reconciliation = {
            "status": "AVAILABLE" if structural_reconciliation_available else "PASS_CONFIRMED" if structural_status == "PASS" else "COMPLETED" if completed_trials else "UNAVAILABLE",
            "status_zh": "可以重新检查" if structural_reconciliation_available else "结构结果已确认" if structural_status == "PASS" else "候选已完成正式验证" if completed_trials else "当前不可重新检查",
            "available": structural_reconciliation_available,
            "candidate_id": authorization_candidate_id,
            "requires_confirmation": True,
            "scope": "STRUCTURAL_ONLY_NO_PREDICTIVE_RUN",
            "reason_zh": "结构证据仍无法确认；重新检查只会运行结构预检并对账结果，不会进入预测试验。" if structural_reconciliation_available and structural_status == "UNKNOWN" else "结构证据曾未通过；重新检查只会运行结构预检并对账结果，不会进入预测试验。" if structural_reconciliation_available else "结构预检已经通过，无需重复执行。" if structural_status == "PASS" else "该候选已经形成正式预测试验记录，不允许重新打开结构阶段。" if completed_trials else "只有处于 READY、没有 Trial 且结构证据未通过的候选才能重新检查。",
            "action_zh": "重新进行结构预检" if structural_reconciliation_available else "无需重新检查" if structural_status == "PASS" else "本候选不可重新检查",
        }
        predictive_authorization_available = bool(predictive_governance and structural_status == "PASS" and predictive_status == "NOT_RUN" and structural_governance_payload.get("available")) if predictive_governance else predictive_authorization_pending and not governance_pending
        predictive_authorization_status = "AUTHORIZED" if governance_authorized else "ENDED" if governance_ended else "AVAILABLE" if predictive_authorization_available else "WAITING_FOR_GOVERNANCE" if governance_pending else "COMPLETED" if completed_trials else "UNAVAILABLE"
        predictive_authorization_status_zh = {
            "AUTHORIZED": "已授权进入第 1 次预测试验",
            "ENDED": "当前 Candidate 研究路径已结束",
            "AVAILABLE": "可以进行治理确认" if predictive_governance else "可以人工授权",
            "WAITING_FOR_GOVERNANCE": "等待人工治理决定",
            "COMPLETED": "本候选已完成预测验证",
            "UNAVAILABLE": "当前不可人工授权",
        }[predictive_authorization_status]
        predictive_authorization_reason = str(structural_governance_payload.get("reason_zh") or "") if predictive_governance else ""
        predictive_authorization_action = str(structural_governance_payload.get("next_action_zh") or "") if predictive_governance else ""
        if predictive_authorization_available:
            predictive_authorization_action = "查看并决定是否授权第 1 次预测试验"
        elif governance_authorized:
            predictive_authorization_action = "等待启动预测试验"
        elif governance_ended:
            predictive_authorization_action = "当前 Candidate 不再进入预测试验"
        elif governance_pending:
            predictive_authorization_action = "打开治理决定"
        if not predictive_authorization_reason:
            predictive_authorization_reason = "结构预检已通过；确认只记录治理决定，不创建 Trial、不预留预算、不访问绩效。" if predictive_authorization_available else "结构预检已通过；提交后端仍会重新核对候选身份、预算、完整性和运行锁。" if predictive_authorization_pending else "该候选已经形成正式预测试验记录，不允许重复授权或重跑。" if completed_trials else "只有结构预检通过且预测试验尚未运行时，才能人工授权。"
        if not predictive_authorization_action:
            predictive_authorization_action = "查看并决定是否授权第 1 次预测试验" if predictive_authorization_available else "等待启动预测试验" if governance_authorized else "当前 Candidate 不再进入预测试验" if governance_ended else "授权并执行一次预测验证" if predictive_authorization_pending else "等待满足授权条件"
        if not predictive_governance and predictive_authorization_available:
            predictive_authorization_reason = "结构预检已通过；提交后端仍会重新核对候选身份、预算、完整性和运行锁。"
            predictive_authorization_action = "授权并执行一次预测验证"
        elif not predictive_governance and completed_trials:
            predictive_authorization_reason = "该候选已经形成正式预测试验记录，不允许重复授权或重跑。"
            predictive_authorization_action = "本候选不可再次执行"
        predictive_authorization = {
            "status": predictive_authorization_status,
            "status_zh": predictive_authorization_status_zh,
            "available": predictive_authorization_available,
            "candidate_id": authorization_candidate_id,
            "candidate_hash": structural_governance_payload.get("candidate_hash") or structural_details.get("candidate_hash"),
            "requires_confirmation": True,
            "scope": "ONE_FROZEN_CANDIDATE_GOVERNANCE_ONLY" if predictive_governance else "ONE_CANDIDATE_ONE_PREDICTIVE_TRIAL",
            "reason_zh": predictive_authorization_reason,
            "action_zh": predictive_authorization_action,
            "next_action": next_action if candidate_trial_completed else str(structural_governance_payload.get("next_action") or next_action) if predictive_governance else next_action,
            "trial_created": bool(structural_governance_payload.get("trial_created", False)) if predictive_governance else False,
            "budget_reserved": int(structural_governance_payload.get("budget_reserved", 0) or 0) if predictive_governance else 0,
            "budget_used_delta": int(structural_governance_payload.get("budget_used_delta", 0) or 0) if predictive_governance else 0,
            "decision_id": structural_governance_payload.get("decision_id") if predictive_governance else None,
            "reconciliation_id": structural_governance_payload.get("reconciliation_id") if predictive_governance else None,
            "preview_endpoint": f"/api/research-console/{objective_id}/predictive/authorization/preview" if predictive_governance else None,
        }
        if not predictive_governance:
            predictive_authorization = {
                "status": predictive_authorization_status,
                "status_zh": predictive_authorization_status_zh,
                "available": predictive_authorization_available,
                "candidate_id": authorization_candidate_id,
                "requires_confirmation": True,
                "scope": "ONE_CANDIDATE_ONE_PREDICTIVE_TRIAL",
                "reason_zh": predictive_authorization_reason,
                "action_zh": predictive_authorization_action,
            }
        try:
            predictive_trial_start = self.predictive_trial_start.readiness(objective_id)
        except (PredictiveTrialStartError, ValueError, OSError, KeyError, TypeError) as exc:
            predictive_trial_start = {
                "status": "UNAVAILABLE",
                "status_zh": "当前不可启动",
                "available": False,
                "objective_id": objective_id,
                "action": "START_PREDICTIVE_TRIAL_1",
                "action_zh": "启动第 1 次预测试验",
                "confirmation_required": True,
                "reason_code": exc.code if isinstance(exc, PredictiveTrialStartError) else "PREDICTIVE_TRIAL_START_SOURCE_UNAVAILABLE",
                "reason_zh": exc.message_zh if isinstance(exc, PredictiveTrialStartError) else "当前 canonical 启动状态暂时不可读取",
                "reasons": [exc.message_zh if isinstance(exc, PredictiveTrialStartError) else type(exc).__name__],
            }
        try:
            predictive_trial_recovery = self.predictive_trial_start.recovery_readiness(objective_id)
        except (PredictiveTrialStartError, ValueError, OSError, KeyError, TypeError) as exc:
            predictive_trial_recovery = {
                "status": "UNAVAILABLE",
                "status_zh": "当前不可恢复",
                "available": False,
                "objective_id": objective_id,
                "action": "RESUME_PREDICTIVE_TRIAL_1",
                "action_zh": "恢复第 1 次预测试验",
                "confirmation_required": True,
                "reason_code": exc.code if isinstance(exc, PredictiveTrialStartError) else "PREDICTIVE_TRIAL_RESUME_SOURCE_UNAVAILABLE",
                "reason_zh": exc.message_zh if isinstance(exc, PredictiveTrialStartError) else "当前 canonical 恢复状态暂时不可读取",
                "reasons": [exc.message_zh if isinstance(exc, PredictiveTrialStartError) else type(exc).__name__],
            }
        governance_readiness = {
            "status": "COMPLETED" if completed_trials else structural_governance_payload.get("status", "NOT_AVAILABLE"),
            "status_zh": "本候选已完成预测验证" if completed_trials else structural_governance_payload.get("status_zh") or ("等待研究治理决定" if governance_pending else "当前没有结构治理待决状态"),
            "available": False if completed_trials else bool(structural_governance_payload.get("available", governance_pending)),
            "decision_mode": structural_governance_payload.get("decision_mode"),
            "candidate_id": structural_governance_payload.get("candidate_id") or completed_candidate_id,
            "candidate_hash": structural_governance_payload.get("candidate_hash"),
            "structural": dict(structural_governance_payload.get("structural") or {}),
            "choices": [dict(item) for item in structural_governance_payload.get("choices", ()) if isinstance(item, Mapping)],
            "next_action_zh": next_action_zh if completed_trials else str(structural_governance_payload.get("next_action_zh") or "等待研究治理决定是否启动第 1 次预测试验。"),
            "human_explanation_zh": "本候选已形成正式预测验证结果，不能重复授权或重跑。" if completed_trials else str(structural_governance_payload.get("human_explanation_zh") or "结构预检已通过，等待决定是否进入正式预测验证。"),
            "final_test_access": dict(structural_governance_payload.get("final_test_access") or {"analytical": 0, "decision": 0, "physical": 0}),
            "prospective": str(structural_governance_payload.get("prospective") or "DISABLED"),
            "real_order": str(structural_governance_payload.get("real_order") or "DISABLED"),
            "predictive_trials_created": len(trials),
            "performance_access": int(structural_governance_payload.get("performance_access", 0)),
            "reconciliation_ref": structural_governance_payload.get("reconciliation_ref"),
            "reconciliation_id": structural_governance_payload.get("reconciliation_id"),
            "next_action": next_action if completed_trials else structural_governance_payload.get("next_action") or next_action,
            "budget_snapshot": dict(structural_governance_payload.get("budget_snapshot") or {}),
            "trial_created": bool(structural_governance_payload.get("trial_created", False)),
            "budget_reserved": int(structural_governance_payload.get("budget_reserved", 0) or 0),
            "budget_used_delta": int(structural_governance_payload.get("budget_used_delta", 0) or 0),
            "latest_decision": structural_governance_payload.get("latest_decision"),
        }
        return ResearchPipelineView(provenance=provenance, objective_id=objective_id, current_stage=current_stage, current_state=orchestrator_state, current_state_zh=orchestrator_state_zh, next_action=next_action, next_action_zh=next_action_zh, current_candidate_id=current_candidate_id or completed_candidate_id, remaining_frozen_candidates=daemon.remaining_frozen_candidates, last_error=last_error, state_source="canonical Orchestrator checkpoint + structural reconciliation" if structural_status == "PASS" else "canonical Orchestrator checkpoint" if orchestrator else "daemon canonical status", stages=stages, batch_ids=batches, candidate_count=len(current_candidates), trial_count=len(trials), recent_events=recent, execution=execution, structural_reconciliation=structural_reconciliation, predictive_authorization=predictive_authorization, predictive_trial_start=predictive_trial_start, predictive_trial_recovery=predictive_trial_recovery, governance_readiness=governance_readiness)

    def get_reports(self, objective_id: str) -> ReportIndexView:
        self._objective(objective_id)
        objective_report_root = f"reports/research_orchestrator_v2/{objective_id}"
        objective_daemon_root = f"reports/research_daemon/{objective_id}"
        rules = (
            ("Autonomous Research", "reports/CURRENT_OBJECTIVE_AUTOMATIC_CLOSEOUT_V2.md", "第一轮短周期 A 股 Alpha 自主研究总结"),
            ("Autonomous Research", "reports/CURRENT_OBJECTIVE_AUTOMATIC_CLOSEOUT_V2.json", "第一轮自主研究自动收官机器报告"),
            ("Governance", "reports/RESEARCH_GOVERNANCE_DECISION_REQUIRED.json", "当前研究治理决定"),
            ("AI", "reports/AUTONOMOUS_RESEARCH_ORCHESTRATOR_V2_CODEX_INVOCATION_ACCEPTANCE.json", "AI 自动调用验收状态"),
            ("Architecture", "reports/PLATFORM_ARCHITECTURE_MANIFEST_V2.json", "平台架构清单"),
            ("Architecture", "reports/PLATFORM_DATA_SOURCE_CAPABILITY_MATRIX_V2.json", "平台数据源能力矩阵"),
            ("Daemon", "reports/RESEARCH_DAEMON_STATE_MACHINE_V1.json", "Research Daemon 状态机"),
            ("Daemon", "reports/RESEARCH_DAEMON_HANDOFF_CURRENT.json", "Daemon 当前 Handoff"),
            ("Candidate", "reports/CURRENT_CANDIDATE_SAMPLE_FEASIBILITY_V2.json", "当前 Candidate 结构预检"),
            ("Structural", "reports/STRUCTURAL_PASS_CANONICAL_SYNC_V1.json", "结构预检 canonical 同步验收"),
            ("Structural", "reports/STRUCTURAL_PASS_LINEAGE_ACCEPTANCE_V1.json", "结构预检 lineage 保留验收"),
            ("Structural", "reports/STRUCTURAL_PASS_59_SAMPLE_REPRODUCIBILITY_V1.json", "结构预检 59 样本复现验收"),
            ("Structural", "reports/STRUCTURAL_PASS_NON_BINDING_1307_AUDIT_V1.json", "结构预检 1307 非绑定审计"),
            ("Structural", "reports/STRUCTURAL_PASS_WEB_READ_MODEL_ACCEPTANCE_V1.json", "结构预检 Web read model 验收"),
            ("Governance", "reports/PREDICTIVE_GOVERNANCE_READINESS_V1.json", "预测验证治理准备"),
            ("Structural", "reports/FINAL_STATUS_SYNC_STRUCTURAL_PREFLIGHT_PASS_TO_CANONICAL_AND_PREPARE_GOVERNANCE_V1.json", "结构预检 PASS 同步与治理准备最终状态"),
            ("Trial", "reports/CURRENT_EFFECTIVE_RESEARCH_HISTORY_V1.json", "有效研究历史"),
            ("Shadow", "reports/SHADOW_SCAN_READINESS_20260825.json", "Shadow 扫描就绪状态"),
            ("Shadow", "reports/EOD_RESEARCH_STOCK_SCAN_20260825.json", "Shadow 每日扫描"),
            ("Data", "data/research/data_capability.json", "数据能力注册表"),
            ("Governance", "RESEARCH_PLATFORM_V1_FREEZE.json", "研究平台冻结边界"),
            ("Governance", "data/research/strategy_validation/validation_decision_policy_v2.lock.json", "ValidationDecisionPolicy 锁"),
            ("Governance", "docs/项目人类可读输出规范_V1.md", "项目人类可读输出规范"),
            ("Architecture", "docs/量化研究Web控制台产品设计_V1.md", "量化研究 Web 控制台产品设计"),
            ("Architecture", "docs/量化研究Web控制台页面信息架构_V1.md", "量化研究 Web 控制台页面信息架构"),
            ("Architecture", "reports/WEB_CONSOLE_V1_C_ARCHITECTURE.json", "Web 控制台 V1-C 架构验收"),
            ("Orchestrator", "reports/WEB_CONSOLE_V1_C_ORCHESTRATOR_INTEGRATION.json", "Orchestrator V2 接入验收"),
            ("AI", "reports/WEB_CONSOLE_V1_C_AI_RESEARCHER_ACCEPTANCE.json", "AI 研究员页面验收"),
            ("Operations", "reports/WEB_CONSOLE_V1_C_OPERATIONS_SAFETY_ACCEPTANCE.json", "研究运行控制安全验收"),
            ("Governance", "reports/WEB_CONSOLE_V1_C_GOVERNANCE_UI_ACCEPTANCE.json", "治理决策页面验收"),
            ("Autonomous Research", "reports/WEB_CONSOLE_V1_C_CLOSEOUT_ACCEPTANCE.json", "自动收官页面验收"),
            ("Autonomous Research", "reports/WEB_CONSOLE_V1_C_NOOUTCOME_ACCEPTANCE.json", "NoOutcome 安全验收"),
            ("Shadow", "reports/WEB_CONSOLE_V1_C_SHADOW_ACCEPTANCE.json", "Shadow 页面验收"),
            ("Data", "reports/WEB_CONSOLE_V1_C_DATA_HEALTH_ACCEPTANCE.json", "Data Health 页面验收"),
            ("Report Center", "reports/WEB_CONSOLE_V1_C_REPORT_CENTER_ACCEPTANCE.json", "Report Center 页面验收"),
            ("Responsive", "reports/WEB_CONSOLE_V1_C_RESPONSIVE_ACCEPTANCE.json", "响应式页面验收"),
            ("Release", "reports/FINAL_STATUS_IMPLEMENT_WEB_CONSOLE_V1_C.json", "Web 控制台 V1-C 实施最终状态"),
        )
        objective_rules = (
            ("Orchestrator", f"{objective_report_root}/orchestrator_checkpoint.json", "当前 Orchestrator 状态快照"),
            ("Orchestrator", f"{objective_report_root}/orchestrator_events.jsonl", "当前 Orchestrator 事件"),
            ("AI", f"{objective_report_root}/ai_handoff.json", "当前 AI 研究交接"),
            ("AI", f"{objective_report_root}/ai_invocation.json", "当前 AI 调用记录"),
            ("Daemon", f"{objective_daemon_root}/daemon_status.json", "当前 daemon 状态"),
            ("Daemon", f"{objective_daemon_root}/daemon_checkpoint.json", "当前 daemon 检查点"),
            ("Daemon", f"{objective_daemon_root}/structural_preflight_reconciliation_canonical_v1.json", "结构预检 canonical PASS"),
            ("Daemon", f"{objective_daemon_root}/structural_preflight_reconciliation_history.json", "结构预检历史代际"),
            ("Governance", f"{objective_report_root}/structural_governance_decision_required.json", "结构预检后的预测验证治理准备"),
            ("Governance", f"{objective_report_root}/predictive_governance_decisions.jsonl", "结构 PASS 后预测试验治理决定账本"),
        )
        entries: list[dict[str, Any]] = []
        for category, relative, title in (*objective_rules, *rules):
            if objective_id != "RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1" and relative.startswith("reports/CURRENT_OBJECTIVE_"):
                continue
            path = self.root / relative
            if not path.exists():
                continue
            scope = "OBJECTIVE" if relative.startswith(f"{objective_report_root}/") or relative.startswith(f"{objective_daemon_root}/") else "PLATFORM"
            payload: Mapping[str, Any] | None = None
            if path.suffix.lower() in {".json", ".jsonl"}:
                try:
                    payload = self._read_json(path, ttl_seconds=self.report_ttl_seconds) if path.suffix.lower() == ".json" else None
                except ResearchConsoleReadError:
                    continue
                identity = payload.get("objective") if isinstance(payload, Mapping) and isinstance(payload.get("objective"), Mapping) else {}
                payload_objective_id = str(identity.get("objective_id") or payload.get("objective_id") or "") if isinstance(payload, Mapping) else ""
                if payload_objective_id and payload_objective_id != objective_id:
                    continue
                if payload_objective_id == objective_id:
                    scope = "OBJECTIVE"
                elif category in {"Candidate", "Trial", "Shadow", "Governance", "Autonomous Research"}:
                    # A global snapshot without objective identity cannot be
                    # safely presented as the current objective's evidence.
                    continue
            report_id = f"{category.upper().replace(' ', '_')}__{path.name}"
            entries.append({"report_id": report_id, "category": category, "title_zh": title, "relative_path": relative, "format": path.suffix.lstrip(".").upper(), "scope": scope, "objective_match": scope == "OBJECTIVE", "source_generated_at": _source_timestamp(payload, path).isoformat() if payload is not None else datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "source_version": f"mtime_ns={path.stat().st_mtime_ns};size={path.stat().st_size}"})
        return ReportIndexView(provenance=self._provenance(source_id="allowlisted_report_manifest", path=None, payload=None, freshness=FreshnessState.FRESH, stale=False, conflict=False), objective_id=objective_id, reports=tuple(entries))

    def get_evolution(self, objective_id: str) -> ResearchEvolutionView:
        """Read the latest explicit evolution report without generating one."""
        self._objective(objective_id)
        report_root = self.root / "reports" / "research_evolution" / objective_id
        matches: list[tuple[datetime, Path, Mapping[str, Any]]] = []
        if report_root.exists():
            for path in sorted(report_root.rglob("research_evolution_report.json"), key=lambda item: item.as_posix()):
                try:
                    payload = self._read_json(path, ttl_seconds=self.report_ttl_seconds)
                except ResearchConsoleReadError:
                    continue
                lineage = payload.get("lineage") if isinstance(payload.get("lineage"), Mapping) else {}
                if str(lineage.get("objective_id") or payload.get("objective_id") or "") != objective_id:
                    continue
                timestamp = _parse_timestamp(payload.get("generated_at")) or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                matches.append((timestamp, path, payload))
        if not matches:
            return ResearchEvolutionView(
                provenance=self._provenance(source_id="research_evolution_report_v1", path=None, payload=None, freshness=FreshnessState.UNKNOWN, stale=False, conflict=False),
                objective_id=objective_id,
                available=False,
                report=None,
                context=None,
                landscape=None,
                display={"title_zh": "尚未生成研究演进分析", "message_zh": "请在明确绑定的研究结果上显式生成分析；页面不会自动创建候选或启动试验。"},
            )
        _, report_path, report = max(matches, key=lambda item: (item[0], item[1].as_posix()))
        context_path = report_path.parent / "AI_RESEARCH_EVOLUTION_CONTEXT.json"
        context: Mapping[str, Any] | None = None
        if context_path.exists():
            try:
                context = self._read_json(context_path, ttl_seconds=self.report_ttl_seconds)
                PerformanceBlindGuard.assert_blind(context)
            except PerformanceLeakError as exc:
                raise ResearchConsoleReadError("OUTCOME_LEAK_DETECTED", "研究演进上下文包含被禁止的绩效字段", status_code=503) from exc
        landscape_path = report_root / "failure_landscape.json"
        landscape = self._read_json(landscape_path, ttl_seconds=self.report_ttl_seconds) if landscape_path.exists() else None
        generated_at = _parse_timestamp(report.get("generated_at"))
        return ResearchEvolutionView(
            provenance=self._provenance(source_id="research_evolution_report_v1", path=report_path, payload=report, freshness=FreshnessState.FRESH, stale=False, conflict=False, source_generated_at=generated_at),
            objective_id=objective_id,
            available=True,
            report=report,
            context=context,
            landscape=landscape,
            display={"title_zh": "研究演进分析已生成", "message_zh": "本页只展示已完成结果的失败分类、研究空间覆盖和人工审核用上下文。"},
        )

    def get_evolution_proposals(self, objective_id: str) -> ResearchEvolutionProposalView:
        """Read Proposal governance state without performing a governance action."""
        self._objective(objective_id)
        proposal_path = self.root / "reports" / "research_evolution" / "proposals" / PROPOSAL_FILENAME
        if not proposal_path.exists():
            return ResearchEvolutionProposalView(
                provenance=self._provenance(source_id="research_evolution_proposal_v1", path=None, payload=None, freshness=FreshnessState.UNKNOWN, stale=False, conflict=False),
                objective_id=objective_id,
                available=False,
                proposal=None,
                coverage=None,
                proposals=(),
                display={"title_zh": "尚未生成研究演进建议", "message_zh": "请先由明确的失败分析生成 Proposal；页面不会自动创建研究目标或启动试验。"},
            )
        try:
            proposal = self._read_json(proposal_path, ttl_seconds=self.report_ttl_seconds)
            PerformanceBlindGuard.assert_blind(proposal)
        except PerformanceLeakError as exc:
            raise ResearchConsoleReadError("OUTCOME_LEAK_DETECTED", "研究演进建议包含被禁止的绩效字段", status_code=503) from exc
        if not isinstance(proposal, Mapping) or str(proposal.get("parent_objective_id") or "") != objective_id:
            return ResearchEvolutionProposalView(
                provenance=self._provenance(source_id="research_evolution_proposal_v1", path=None, payload=None, freshness=FreshnessState.UNKNOWN, stale=False, conflict=False),
                objective_id=objective_id,
                available=False,
                proposal=None,
                coverage=None,
                proposals=(),
                display={"title_zh": "当前目标暂无研究演进建议", "message_zh": "已有 Proposal 未绑定当前研究目标，页面不会跨目标展示。"},
            )
        proposal_items: tuple[Mapping[str, Any], ...] = ()
        try:
            governance_service = ResearchProposalGovernanceServiceV1(self.root)
            governed = governance_service.get_proposal(str(proposal.get("proposal_id") or ""))
            proposal = governed
            proposal_items = tuple(governance_service.list_proposals(objective_id=objective_id).get("items") or ())
        except ResearchProposalGovernanceError as exc:
            raise ResearchConsoleReadError("PROPOSAL_GOVERNANCE_UNAVAILABLE", exc.message_zh, status_code=exc.status_code, details=exc.details) from exc
        coverage_path = proposal_path.with_name(COVERAGE_FILENAME)
        coverage: Mapping[str, Any] | None = None
        if coverage_path.exists():
            try:
                candidate_coverage = self._read_json(coverage_path, ttl_seconds=self.report_ttl_seconds)
                PerformanceBlindGuard.assert_blind(candidate_coverage)
                coverage = candidate_coverage if isinstance(candidate_coverage, Mapping) else None
            except PerformanceLeakError as exc:
                raise ResearchConsoleReadError("OUTCOME_LEAK_DETECTED", "机制覆盖注册表包含被禁止的绩效字段", status_code=503) from exc
        generated_at = _parse_timestamp(proposal.get("generated_at"))
        return ResearchEvolutionProposalView(
            provenance=self._provenance(source_id="research_evolution_proposal_v1", path=proposal_path, payload=proposal, freshness=FreshnessState.FRESH, stale=False, conflict=False, source_generated_at=generated_at),
            objective_id=objective_id,
            available=True,
            proposal=proposal,
            coverage=coverage,
            proposals=proposal_items or (proposal,),
            display={"title_zh": "研究演进建议已生成", "message_zh": "本页展示 Proposal 来源、治理历史和创建方案；只有第二次人工确认才会创建新 Objective。"},
        )

    get_evolution_proposal = get_evolution_proposals

    def get_evolution_ai_design(self, objective_id: str) -> ResearchEvolutionAIDesignView:
        """Read the outcome-blind AI design handoff for one Objective."""
        self._objective(objective_id)
        try:
            model = self.evolution_ai_design.get_design(objective_id)
            PerformanceBlindGuard.assert_blind(model)
        except ResearchEvolutionAIDesignError as exc:
            raise ResearchConsoleReadError(exc.code, exc.message_zh, status_code=exc.status_code, details=exc.details) from exc
        except PerformanceLeakError as exc:
            raise ResearchConsoleReadError("OUTCOME_LEAK_DETECTED", "AI 研究设计视图包含被禁止的结果字段", status_code=503) from exc
        generated_at = None
        design = model.get("design") if isinstance(model.get("design"), Mapping) else None
        if isinstance(design, Mapping):
            generated_at = _parse_timestamp(design.get("generated_at"))
        return ResearchEvolutionAIDesignView(
            provenance=self._provenance(
                source_id="research_evolution_ai_design_v1",
                path=self.root / str(model.get("output_path")) if model.get("output_path") else None,
                payload=model,
                freshness=FreshnessState.FRESH,
                stale=False,
                conflict=False,
                source_generated_at=generated_at,
            ),
            objective_id=objective_id,
            available=bool(model.get("available")),
            status=str(model.get("status") or "NEED_AI_RESEARCH_DESIGN"),
            input=model.get("input") if isinstance(model.get("input"), Mapping) else {},
            design=design,
            governance=model.get("governance") if isinstance(model.get("governance"), Mapping) else {},
            source_refs=model.get("source_refs") if isinstance(model.get("source_refs"), Mapping) else {},
            display={
                "title_zh": "AI 研究设计已生成" if model.get("available") else "等待 AI 研究设计",
                "message_zh": "设计提案已停在人工确认边界；不会自动创建 Candidate、启动 Trial 或消耗预算。" if model.get("available") else "当前目标已创建，正在等待一次明确的 AI 研究设计生成；页面只展示脱敏研究上下文。",
            },
            output_path=str(model.get("output_path")) if model.get("output_path") else None,
            outcome_blind=bool(model.get("outcome_blind", True)),
            performance_data_loaded=bool(model.get("performance_data_loaded", False)),
            outcome_fields_available=bool(model.get("outcome_fields_available", False)),
        )

    def get_candidate_proposals(self, objective_id: str) -> CandidateProposalView:
        """Read Candidate Proposal governance without generating or freezing anything."""
        self._objective(objective_id)
        try:
            result = self.candidate_generation.list_proposals(objective_id=objective_id)
            PerformanceBlindGuard.assert_blind(result)
        except CandidateGenerationError as exc:
            raise ResearchConsoleReadError(exc.code, exc.message_zh, status_code=exc.status_code, details=exc.details) from exc
        except PerformanceLeakError as exc:
            raise ResearchConsoleReadError("OUTCOME_LEAK_DETECTED", "候选建议视图包含被禁止的结果字段", status_code=503) from exc
        items = tuple(item for item in result.get("items", ()) if isinstance(item, Mapping))
        proposal = items[0] if items else None
        preview = proposal.get("freeze_preview") if isinstance(proposal, Mapping) and isinstance(proposal.get("freeze_preview"), Mapping) else None
        output_path = str(proposal.get("output_path")) if isinstance(proposal, Mapping) and proposal.get("output_path") else None
        status = str(proposal.get("status") or "NEED_CANDIDATE_PROPOSAL") if isinstance(proposal, Mapping) else "NEED_CANDIDATE_PROPOSAL"
        source_path = self.root / output_path if output_path else None
        generated_at = _parse_timestamp(proposal.get("generated_at")) if isinstance(proposal, Mapping) else None
        return CandidateProposalView(
            provenance=self._provenance(source_id="candidate_proposal_governance_v1", path=source_path, payload=proposal, freshness=FreshnessState.FRESH if proposal else FreshnessState.UNKNOWN, stale=False, conflict=False, source_generated_at=generated_at),
            objective_id=objective_id,
            available=proposal is not None,
            status=status,
            proposal=proposal,
            proposals=items,
            governance=proposal.get("governance") if isinstance(proposal, Mapping) and isinstance(proposal.get("governance"), Mapping) else {},
            freeze_preview=preview,
            display={
                "title_zh": "Candidate 已冻结" if status in {"FROZEN", "READY_FOR_STRUCTURAL_PREFLIGHT"} else "候选策略建议已生成" if proposal else "等待候选策略建议",
                "message_zh": "Candidate 已由人工确认冻结；当前仅允许进入独立 Structural Preflight 人工入口，不会自动启动 Structural Preflight 或 Trial。" if status in {"FROZEN", "READY_FOR_STRUCTURAL_PREFLIGHT"} else "候选建议已停在人工审核边界；批准后只生成 immutable 冻结预览，仍需第二次人工确认。" if proposal else "请先显式生成 Candidate Proposal；页面不会自动调用 AI、创建 Candidate 或消耗预算。",
            },
            output_path=output_path,
            outcome_blind=True,
            performance_data_loaded=False,
            outcome_fields_available=False,
        )

    def read_report(self, objective_id: str, report_id: str) -> dict[str, Any]:
        index = self.get_reports(objective_id)
        if not _IDENTIFIER_RE.fullmatch(str(report_id)):
            raise ResearchConsoleReadError("REPORT_NOT_ALLOWED", "报告标识不在 allowlist 中", status_code=400)
        entry = next((item for item in index.reports if item.get("report_id") == report_id), None)
        if entry is None:
            raise ResearchConsoleReadError("REPORT_NOT_ALLOWED", "报告标识不在 allowlist 中", status_code=404)
        path = self._safe_path(str(entry["relative_path"]))
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ResearchConsoleReadError("SOURCE_UNREADABLE", "报告暂时不可读", status_code=503) from exc
        if len(content.encode("utf-8")) > 2 * 1024 * 1024:
            raise ResearchConsoleReadError("REPORT_TOO_LARGE", "报告超过单次安全读取大小", status_code=413)
        return {"objective_id": objective_id, "report_id": report_id, "category": entry["category"], "title_zh": entry["title_zh"], "format": entry["format"], "scope": entry.get("scope"), "objective_match": entry.get("objective_match"), "source_generated_at": entry.get("source_generated_at"), "content": content, "relative_path": entry["relative_path"]}

    def get_governance(self, objective_id: str) -> GovernanceView:
        objective, objective_path = self._objective(objective_id)
        candidates = self._contract_candidates(objective_id)
        candidate = next(iter(candidates.values()), {})
        policy_path = self.root / "data/research/strategy_validation/validation_decision_policy_v2.lock.json"
        policy = self._read_json(policy_path, ttl_seconds=60.0) if policy_path.exists() else {}
        sample_path = self.root / "reports/SAMPLE_FEASIBILITY_POLICY_V2.json"
        sample = self._read_json(sample_path, ttl_seconds=60.0) if sample_path.exists() else {}
        freeze_path = self.root / "RESEARCH_PLATFORM_V1_FREEZE.json"
        freeze = self._read_json(freeze_path, ttl_seconds=60.0) if freeze_path.exists() else {}
        history_path = self.root / "reports/CURRENT_EFFECTIVE_RESEARCH_HISTORY_V1.json"
        history = self._read_json(history_path, ttl_seconds=60.0) if history_path.exists() else {}
        final_test = history.get("final_test_access") if isinstance(history.get("final_test_access"), Mapping) else {"analytical": 0, "decision": 0, "physical": 0}
        risk = objective.get("risk_constraints") if isinstance(objective.get("risk_constraints"), Mapping) else {}
        research_period = dict(candidate.get("research_period_identity") or {"id": "UNKNOWN", "start": None, "end": None})
        return GovernanceView(provenance=self._provenance(source_id="objective+policy_locks+platform_freeze", path=objective_path, payload=objective, freshness=FreshnessState.FRESH, stale=False, conflict=False), objective_id=objective_id, research_period=research_period, final_test_boundary={"state": "SEALED", "source": "RESEARCH_PLATFORM_V1_FREEZE", "freeze_id": freeze.get("freeze_id")}, sample_feasibility_policy={key: sample.get("policy_identity", {}).get(key) for key in ("policy_id", "policy_version", "policy_hash") if isinstance(sample.get("policy_identity"), Mapping) and sample.get("policy_identity", {}).get(key) is not None}, validation_decision_policy={key: policy.get(key) for key in ("policy_id", "policy_version", "policy_hash", "policy_file_sha256") if policy.get(key) is not None}, search_budget_policy={"max_total_trials": objective.get("max_total_trials"), "max_batches": objective.get("max_batches"), "read_only": True}, pit_rules=dict((candidate.get("pit_dependencies") or {}).get("universe_rule", {})), execution_contract_version=str(candidate.get("execution_contract_version")) if candidate.get("execution_contract_version") else None, final_test_access=dict(final_test), prospective_state=risk.get("prospective_access", "DISABLED"), real_order_state=str(risk.get("real_order_execution", "DISABLED")))


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_relative_ref(value: str) -> bool:
    path = Path(str(value).replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts


def _relative_safe(path: Any, root: Path) -> str:
    if isinstance(path, Path):
        try:
            return path.resolve().relative_to(root).as_posix()
        except ValueError:
            return ""
    value = str(path or "")
    return value.replace("\\", "/") if _safe_relative_ref(value) else ""
