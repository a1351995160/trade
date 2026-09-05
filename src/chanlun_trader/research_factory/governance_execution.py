"""Canonical, human-confirmed governance execution for research objectives.

The service deliberately owns only the governance transaction.  Existing
budget, artifact-graph and no-outcome contracts remain the source of truth;
the service prepares a new objective in a staging area and commits it through
an application-level journal so a browser retry or process interruption
converges to one result.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Callable, Mapping

from .artifact_graph import ResearchArtifactGraphV1
from .budget import SearchBudgetRegistryV1
from .common import now_timestamp, stable_hash
from .context import NoOutcomeResearchContextV1, PerformanceBlindGuard


SUPPORTED_GOVERNANCE_ACTIONS = frozenset({
    "STOP_RESEARCH",
    "START_PROMISING_FOLLOWUP_OBJECTIVE",
    "START_NEW_MECHANISM_OBJECTIVE",
})
TERMINAL_SOURCE_STATES = frozenset({
    "GOVERNANCE_DECISION_REQUIRED",
    "TERMINAL_CLOSEOUT_COMPLETE",
    "BUDGET_EXHAUSTED",
    "RESEARCH_PASSED",
    "GLOBAL_SEARCH_EXHAUSTED",
    "SHUTDOWN",
})
EXECUTION_MODES = frozenset({"CREATE_ONLY", "CREATE_AND_ACTIVATE"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_TIMESTAMP_FIELDS = frozenset({"created_at", "updated_at", "generated_at", "observed_at", "frozen_at"})


class GovernanceExecutionError(RuntimeError):
    """Safe error raised at the localhost governance application boundary."""

    def __init__(self, code: str, message_zh: str, *, status_code: int = 409, details: Mapping[str, Any] | None = None):
        super().__init__(message_zh)
        self.code = str(code)
        self.message_zh = str(message_zh)
        self.status_code = int(status_code)
        self.details = dict(details or {})

    def envelope(self) -> dict[str, Any]:
        return {"code": self.code, "message_zh": self.message_zh, "details": dict(self.details)}


def _without_timestamps(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _without_timestamps(nested) for key, nested in value.items() if str(key) not in _TIMESTAMP_FIELDS}
    if isinstance(value, (list, tuple)):
        return [_without_timestamps(item) for item in value]
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_id(value: Any, *, kind: str) -> str:
    result = str(value or "")
    if not _IDENTIFIER.fullmatch(result):
        raise GovernanceExecutionError("INVALID_IDENTIFIER", f"{kind} 不合法", status_code=400)
    return result


@dataclass(frozen=True)
class ResearchGovernanceExecutionPreviewV1:
    decision_id: str
    source_objective_id: str
    source_objective_hash: str
    source_governance_hash: str
    source_budget_head_hash: str
    source_state: str
    governance_action: str
    execution_mode: str
    selected_parent_candidate_id: str | None
    selected_parent_candidate_hash: str | None
    proposed_new_objective_id: str | None
    proposed_new_objective_identity: Mapping[str, Any]
    parent_lineage: tuple[Mapping[str, Any], ...]
    parent_candidate_identity_refs: tuple[Mapping[str, Any], ...]
    research_purpose: str
    mechanism_scope: tuple[str, ...]
    allowed_factor_scope: tuple[str, ...]
    no_outcome_policy: Mapping[str, Any]
    candidate_eligibility: Mapping[str, Any]
    budget_proposal: Mapping[str, Any]
    multiple_testing_family: Mapping[str, Any]
    stop_conditions: Mapping[str, Any]
    final_test_status: Mapping[str, Any]
    prospective_status: str
    real_order_status: str
    generated_at: str

    @property
    def identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("generated_at", None)
        payload["parent_lineage"] = [dict(item) for item in self.parent_lineage]
        payload["parent_candidate_identity_refs"] = [dict(item) for item in self.parent_candidate_identity_refs]
        payload["mechanism_scope"] = list(self.mechanism_scope)
        payload["allowed_factor_scope"] = list(self.allowed_factor_scope)
        payload["proposed_new_objective_identity"] = dict(self.proposed_new_objective_identity)
        return _without_timestamps(payload)

    @property
    def preview_hash(self) -> str:
        return stable_hash(self.identity_payload)

    @property
    def confirmation_token(self) -> str:
        return stable_hash({"decision_id": self.decision_id, "preview_hash": self.preview_hash, "contract": "GOVERNANCE_CONFIRMATION_V1"})[:40]

    def to_dict(self) -> dict[str, Any]:
        payload = _jsonable(asdict(self))
        payload.update({"schema_version": "research-governance-execution-preview-v1", "preview_hash": self.preview_hash, "confirmation_token": self.confirmation_token})
        return payload


@dataclass(frozen=True)
class ResearchGovernanceExecutionReceiptV1:
    execution_id: str
    decision_id: str
    preview_hash: str
    source_objective_id: str
    new_objective_id: str | None
    new_budget_id: str | None
    multiple_testing_family_id: str | None
    lineage_refs: tuple[str, ...]
    artifact_graph_refs: tuple[str, ...]
    created_at: str
    result_state: str
    execution_mode: str
    idempotency_key: str

    def to_dict(self) -> dict[str, Any]:
        payload = _jsonable(asdict(self))
        payload["schema_version"] = "research-governance-execution-receipt-v1"
        return payload


class ResearchGovernanceExecutionServiceV1:
    """Execute only an explicitly confirmed canonical governance preview."""

    _mutex = threading.RLock()

    def __init__(self, root: str | Path, *, clock: Callable[[], datetime] | None = None, crash_at: str | None = None):
        self.root = Path(root).resolve()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.crash_at = crash_at

    def _now(self) -> str:
        return self.clock().astimezone(timezone.utc).isoformat(timespec="seconds")

    def _read_json(self, path: Path, *, required: bool = True) -> Mapping[str, Any] | None:
        if not path.exists():
            if required:
                raise GovernanceExecutionError("CANONICAL_ARTIFACT_MISSING", f"canonical 文件不存在：{path.name}", status_code=503)
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise GovernanceExecutionError("CANONICAL_ARTIFACT_UNREADABLE", f"canonical 文件暂时不可读：{path.name}", status_code=503) from exc
        if not isinstance(payload, Mapping):
            raise GovernanceExecutionError("CANONICAL_ARTIFACT_INVALID", f"canonical 文件不是对象：{path.name}", status_code=503)
        return payload

    def _atomic_write(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, path)

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def _objective_path(self, objective_id: str) -> Path:
        return self.root / "data" / "research" / "research_factory" / "objectives" / f"{objective_id}.json"

    def _decision_path(self, objective_id: str) -> Path:
        candidates = (
            self.root / "reports" / "research_orchestrator_v2" / objective_id / "governance_decision_required.json",
            self.root / "reports" / "RESEARCH_GOVERNANCE_DECISION_REQUIRED.json",
            self.root / "reports" / "research_governance" / objective_id / "decision.json",
            self.root / "reports" / f"RESEARCH_GOVERNANCE_DECISION_REQUIRED_{objective_id}.json",
        )
        for path in candidates:
            if not path.exists():
                continue
            payload = self._read_json(path)
            if str(payload.get("objective_id") or objective_id) == objective_id:
                return path
        raise GovernanceExecutionError("GOVERNANCE_NOT_REQUIRED", "当前 objective 没有可执行的 canonical 治理决定", status_code=404)

    def _source_state(self, objective_id: str, objective: Mapping[str, Any]) -> str:
        paths = (
            self.root / "reports" / "research_orchestrator_v2" / objective_id / "orchestrator_checkpoint.json",
            self.root / "reports" / "research_daemon" / objective_id / "daemon_checkpoint.json",
            self.root / "reports" / "research_daemon" / objective_id / "daemon_status.json",
        )
        for path in paths:
            payload = self._read_json(path, required=False)
            if payload:
                state = payload.get("state") or payload.get("current_state") or payload.get("daemon_state")
                if state:
                    return str(state)
        return str(objective.get("lifecycle_state") or objective.get("status") or "UNKNOWN")

    def _budget_path(self, objective_id: str, decision: Mapping[str, Any], objective: Mapping[str, Any]) -> Path | None:
        refs = [((decision.get("current_terminal_summary") or {}).get("budget") or {}).get("source")]
        for ref in refs:
            if not ref:
                continue
            path = (self.root / str(ref).replace("\\", "/")).resolve()
            if path.is_relative_to(self.root):
                payload = self._read_json(path, required=False)
                if payload and str(payload.get("objective_id")) == objective_id:
                    return path
        matches: list[Path] = []
        for path in (self.root / "data" / "research" / "research_factory" / "batches").glob("*/search_budget_registry.json"):
            payload = self._read_json(path, required=False)
            if payload and str(payload.get("objective_id")) == objective_id:
                matches.append(path)
        if matches:
            return max(matches, key=lambda item: str((self._read_json(item, required=False) or {}).get("updated_at", "")))
        if objective.get("max_total_trials") is not None:
            return None
        raise GovernanceExecutionError("SEARCH_BUDGET_NOT_FOUND", "无法找到 source objective 的 canonical 预算", status_code=503)

    def _source_budget(self, objective_id: str, decision: Mapping[str, Any], objective: Mapping[str, Any]) -> dict[str, Any]:
        path = self._budget_path(objective_id, decision, objective)
        if path is None:
            total = int(objective.get("max_total_trials", 0) or 0)
            return {"objective_id": objective_id, "used": 0, "reserved": 0, "remaining": total, "total": total, "head_hash": stable_hash({"objective_id": objective_id, "total": total}), "registry_path": None}
        registry = SearchBudgetRegistryV1(objective_id, path)
        snapshot = registry.snapshot()
        bucket = next((item for item in snapshot.get("buckets", ()) if item.get("kind") == "objective" and item.get("key") == objective_id), {})
        return {"objective_id": objective_id, "used": int(bucket.get("used", 0)), "reserved": int(bucket.get("reserved", 0)), "remaining": int(bucket.get("remaining", 0)), "total": int(bucket.get("limit", objective.get("max_total_trials", 0))), "head_hash": registry.head_hash, "registry_path": self._relative(path)}

    def _candidate_from_registry(self, candidate_id: str) -> Mapping[str, Any] | None:
        roots = (
            self.root / "data" / "research" / "strategy_candidate_registry",
            self.root / "data" / "research" / "research_factory" / "batches",
        )
        for base in roots:
            if not base.exists():
                continue
            paths = base.glob("*.json") if base.is_dir() else base.rglob("*.json")
            for path in paths:
                payload = self._read_json(path, required=False)
                if not payload:
                    continue
                records = payload.get("records") or payload.get("candidates") or payload.get("contracts") or ()
                for record in records:
                    if not isinstance(record, Mapping):
                        continue
                    candidate = record.get("candidate") if isinstance(record.get("candidate"), Mapping) else record
                    if str(candidate.get("candidate_id") or record.get("candidate_id")) == candidate_id:
                        return candidate
        return None

    @staticmethod
    def _safe_candidate(candidate_id: str, candidate_hash: str, family_id: str, candidate: Mapping[str, Any] | None) -> dict[str, Any]:
        raw = candidate or {}
        safe = {
            "candidate_id": candidate_id,
            "candidate_hash": candidate_hash,
            "family_id": family_id,
            "mechanism": str(raw.get("mechanism") or raw.get("family") or family_id),
        }
        PerformanceBlindGuard.assert_blind(safe)
        return safe

    def _promising_candidates(self, objective_id: str, decision: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
        records: list[Mapping[str, Any]] = []
        registry_path = self.root / "reports" / "CURRENT_EFFECTIVE_STRATEGY_REGISTRY_V1.json"
        registry = self._read_json(registry_path, required=False)
        if registry:
            records.extend(item for item in (registry.get("records") or ()) if isinstance(item, Mapping) and str(item.get("current_effective_classification") or item.get("research_state")) == "PROMISING")
        else:
            records.extend(item for item in (decision.get("promising_candidates") or ()) if isinstance(item, Mapping))
        result: list[dict[str, Any]] = []
        for record in records:
            candidate_id = str(record.get("candidate_id") or "")
            if not candidate_id:
                continue
            candidate_hash = str(record.get("candidate_hash") or record.get("fingerprint") or "")
            family_id = str(record.get("family_id") or record.get("mechanism") or "UNKNOWN")
            candidate = self._candidate_from_registry(candidate_id)
            result.append(self._safe_candidate(candidate_id, candidate_hash, family_id, candidate))
        return tuple(sorted(result, key=lambda item: item["candidate_id"]))

    @staticmethod
    def _selected_parent_candidate(
        candidates: tuple[Mapping[str, Any], ...],
        selected_parent_candidate_id: Any,
        selected_parent_candidate_hash: Any,
    ) -> dict[str, Any]:
        candidate_id = str(selected_parent_candidate_id or "")
        candidate_hash = str(selected_parent_candidate_hash or "")
        if not candidate_id or not candidate_hash:
            raise GovernanceExecutionError("PARENT_CANDIDATE_SELECTION_REQUIRED", "围绕 PROMISING 开启新目标时必须明确选择父候选及其 hash", status_code=400)
        selected = next((dict(item) for item in candidates if str(item.get("candidate_id")) == candidate_id), None)
        if selected is None:
            raise GovernanceExecutionError("PARENT_CANDIDATE_NOT_PROMISING", "所选父候选不是当前 canonical PROMISING 候选", status_code=409)
        if str(selected.get("candidate_hash")) != candidate_hash:
            raise GovernanceExecutionError("PARENT_CANDIDATE_HASH_MISMATCH", "所选父候选 hash 与当前 canonical 登记不一致", status_code=409)
        return selected

    def _canonical_inputs(self, objective_id: str) -> dict[str, Any]:
        objective_id = _safe_id(objective_id, kind="objective_id")
        objective_path = self._objective_path(objective_id)
        objective = self._read_json(objective_path)
        if str(objective.get("objective_id")) != objective_id:
            raise GovernanceExecutionError("OBJECTIVE_SOURCE_MISMATCH", "研究 objective 身份校验失败", status_code=503)
        decision_path = self._decision_path(objective_id)
        decision = self._read_json(decision_path)
        source_state = self._source_state(objective_id, objective)
        if source_state not in TERMINAL_SOURCE_STATES:
            raise GovernanceExecutionError("SOURCE_OBJECTIVE_NOT_TERMINAL", "只有已经终止并进入治理边界的研究目标才能执行治理决定")
        budget = self._source_budget(objective_id, decision, objective)
        allowed = decision.get("allowed_choices") if isinstance(decision.get("allowed_choices"), list) else []
        allowed_actions = {str(item.get("choice")) for item in allowed if isinstance(item, Mapping) and item.get("choice")}
        if not allowed_actions:
            allowed_actions = set(SUPPORTED_GOVERNANCE_ACTIONS)
        return {
            "objective": objective,
            "objective_path": objective_path,
            "decision": decision,
            "decision_path": decision_path,
            "source_state": source_state,
            "budget": budget,
            "allowed_actions": allowed_actions & SUPPORTED_GOVERNANCE_ACTIONS,
            "promising_candidates": self._promising_candidates(objective_id, decision),
        }

    def _executable_factor_records(self) -> tuple[Mapping[str, Any], ...]:
        registry_path = self.root / "data" / "research" / "unified_factor_registry" / "registry.json"
        registry = self._read_json(registry_path, required=False)
        if not registry:
            return ()
        return tuple(
            item
            for item in (registry.get("factors") or ())
            if isinstance(item, Mapping)
            and item.get("factor_id")
            and str(item.get("implementation_status")) == "EXECUTABLE"
            and str(item.get("pit_status")) == "PIT_VERIFIED"
            and str(item.get("a_share_compatibility")) == "NATIVE_COMPATIBLE"
        )

    def _new_mechanism_scope(self, objective: Mapping[str, Any]) -> tuple[str, ...]:
        coverage_path = self.root / "reports" / "CODEX_GUIDED_PILOT_MECHANISM_COVERAGE_V1.json"
        coverage = self._read_json(coverage_path, required=False)
        factor_families = {str(item.get("family") or "").upper() for item in self._executable_factor_records() if item.get("family")}
        data_capability = self._read_json(self.root / "data" / "research" / "data_capability.json", required=False) or {}
        dataset_status = {
            str(item.get("dataset_id")): str(item.get("status") or "")
            for item in (data_capability.get("datasets") or ())
            if isinstance(item, Mapping) and item.get("dataset_id")
        }
        directions: list[str] = []
        if coverage:
            review_scope = coverage.get("review_scope") if isinstance(coverage.get("review_scope"), Mapping) else {}
            if review_scope.get("outcome_values_used_for_coverage") is False:
                decision = coverage.get("mechanism_area_decision") if isinstance(coverage.get("mechanism_area_decision"), Mapping) else {}
                for raw in decision.get("STILL_OPEN_FOR_NEW_MECHANISMS") or ():
                    direction = str(raw).strip()
                    normalized = direction.casefold()
                    if not direction:
                        continue
                    if "lhb" in normalized or "flow event" in normalized:
                        if dataset_status.get("lhb_professional") == "READY":
                            directions.append(direction)
                        continue
                    if "cross-sectional" in normalized or "sector-relative" in normalized or "industry" in normalized:
                        if "CROSS_SECTIONAL" in factor_families:
                            directions.append(direction)
                        continue
                    if "liquidity" in normalized or "amount participation" in normalized:
                        if "LIQUIDITY" in factor_families:
                            directions.append(direction)
                        continue
                    if "regime" in normalized:
                        if "MARKET_REGIME" in factor_families:
                            directions.append(direction)
                        continue
                    if "unused factor families" in normalized:
                        used = coverage.get("factor_catalog") if isinstance(coverage.get("factor_catalog"), Mapping) else {}
                        used_families = {str(name).upper() for name in (used.get("used_factor_families") or {})}
                        novel_executable = sorted(factor_families - used_families)
                        already_named = {
                            family
                            for family in novel_executable
                            if any(family.casefold().replace("_", "-") in item.casefold() or family.casefold().replace("_", " ") in item.casefold() for item in directions)
                        }
                        remaining_families = [family for family in novel_executable if family not in already_named]
                        if remaining_families:
                            directions.append(f"unused executable factor families: {', '.join(remaining_families)}")
                        continue
        source_scope = {str(item).strip().casefold() for item in (objective.get("mechanism_scope") or ()) if str(item).strip()}
        filtered = tuple(dict.fromkeys(item for item in directions if item.casefold() not in source_scope))
        if filtered:
            PerformanceBlindGuard.assert_blind({"mechanism_scope": filtered})
            return filtered
        if factor_families:
            return ("novel interactions among executable PIT-safe factor families",)
        raise GovernanceExecutionError("NEW_MECHANISM_SCOPE_UNAVAILABLE", "当前没有可证明可执行且 PIT 安全的新机制研究范围", status_code=503)

    def _new_mechanism_factor_scope(self, objective: Mapping[str, Any]) -> tuple[str, ...]:
        allowed = sorted({str(item.get("factor_id")) for item in self._executable_factor_records()})
        if allowed:
            PerformanceBlindGuard.assert_blind({"factor_capability_summary": [{"factor_id": factor_id, "available": True} for factor_id in allowed]})
            return tuple(allowed)
        inherited = tuple(sorted({str(item) for item in (objective.get("allowed_factor_scope") or ()) if str(item)}))
        if inherited:
            return inherited
        raise GovernanceExecutionError("NEW_MECHANISM_FACTOR_SCOPE_UNAVAILABLE", "无法从 canonical 因子能力中建立新机制研究范围", status_code=503)

    @staticmethod
    def _action_label(action: str) -> str:
        return {"STOP_RESEARCH": "结束研究", "START_PROMISING_FOLLOWUP_OBJECTIVE": "围绕有潜力策略开启新的验证轮次", "START_NEW_MECHANISM_OBJECTIVE": "探索新的策略机制"}.get(action, action)

    def catalog(self, objective_id: str) -> dict[str, Any]:
        with self._mutex:
            inputs = self._canonical_inputs(objective_id)
            decision = inputs["decision"]
            choices = []
            action_order = {name: index for index, name in enumerate(("STOP_RESEARCH", "START_PROMISING_FOLLOWUP_OBJECTIVE", "START_NEW_MECHANISM_OBJECTIVE"))}
            for action in sorted(inputs["allowed_actions"], key=lambda item: action_order.get(item, 99)):
                creates = action != "STOP_RESEARCH"
                choices.append({
                    "action": action,
                    "choice": action,
                    "label_zh": self._action_label(action),
                    "creates_new_objective": creates,
                    "requires_new_budget": creates,
                    "requires_explicit_confirmation": True,
                    "description_zh": {"STOP_RESEARCH": "保留本轮终态，不创建新目标、不增加预算。", "START_PROMISING_FOLLOWUP_OBJECTIVE": "以父候选的冻结语义为证据，创建独立验证轮次。", "START_NEW_MECHANISM_OBJECTIVE": "使用脱敏的机制覆盖与失败知识边界，创建新的研究机制轮次。"}[action],
                })
            return {
                "schema_version": "research-governance-preview-catalog-v1",
                "objective_id": objective_id,
                "decision_id": decision.get("decision_id"),
                "governance_execution_level": "FULL",
                "backend_level": "FULL",
                "execution_capability_zh": "已支持安全执行治理决定；执行前必须先查看不可变方案并明确确认。",
                "source_state": inputs["source_state"],
                "source_budget": inputs["budget"],
                "eligible_parent_candidates": [dict(item) for item in inputs["promising_candidates"]],
                "choices": choices,
                "current_terminal_summary": dict(decision.get("current_terminal_summary") or {}),
                "final_test_status": {"state": "SEALED", "access": {"analytical": 0, "decision": 0, "physical": 0}},
                "prospective_status": "DISABLED",
                "real_order_status": "DISABLED",
                "current_real_objective_untouched": True,
                "created_at": self._now(),
            }

    def _budget_proposal(self, action: str, candidates: tuple[Mapping[str, Any], ...], objective: Mapping[str, Any]) -> dict[str, Any]:
        if action == "STOP_RESEARCH":
            return {"policy_version": "GOVERNANCE_BUDGET_POLICY_V1", "proposed_total_predictive_budget": 0, "candidate_slots": 0, "preregistered_family_count": 0, "max_candidates_per_family": 0, "expected_structural_screen_allowance": 0, "reason_zh": "结束研究不创建新预算。", "outcome_derived_expansion": False, "parent_budget_inherited": False}
        if action == "START_PROMISING_FOLLOWUP_OBJECTIVE":
            selected_parent = dict(candidates[0])
            return {
                "policy_version": "GOVERNANCE_BUDGET_POLICY_V1",
                "proposed_total_predictive_budget": 4,
                "candidate_slots": 1,
                "preregistered_family_count": 1,
                "max_candidates_per_family": 1,
                "expected_structural_screen_allowance": 1,
                "reason_zh": "预测试验预算最多 4 次，仅用于所选父候选的一次主确认与事前定义的稳健性验证；这是上限而非必须消费，不允许据此生成 4 个候选或任何候选变体。",
                "basis": ["selected_parent_candidate_identity", "one_shot_candidate_limit", "preregistered_robustness_validation", "compute_cost_units"],
                "selected_parent_candidate": selected_parent,
                "candidate_generation_policy": "ONE_SHOT",
                "candidate_variants_allowed": False,
                "compute_cost_units": 4,
                "outcome_derived_expansion": False,
                "parent_budget_inherited": False,
                "parent_budget_limit_reference": int(objective.get("max_total_trials", 0) or 0),
            }
        else:
            family_count = 3
            max_per_family = 2
            purpose = "在既有数据能力和合法因子范围内探索未覆盖机制"
        slots = family_count * max_per_family
        return {
            "policy_version": "GOVERNANCE_BUDGET_POLICY_V1",
            "proposed_total_predictive_budget": slots,
            "candidate_slots": slots,
            "preregistered_family_count": family_count,
            "max_candidates_per_family": max_per_family,
            "expected_structural_screen_allowance": slots,
            "reason_zh": f"预算由预注册家族数、每家族候选上限和结构筛选配额决定：{purpose}；不读取历史收益、回撤或排名。",
            "basis": ["preregistered_family_count", "max_candidates_per_family", "performance_blind_structural_allowance", "compute_cost_units"],
            "compute_cost_units": slots,
            "outcome_derived_expansion": False,
            "parent_budget_inherited": False,
            "parent_budget_limit_reference": int(objective.get("max_total_trials", 0) or 0),
        }

    def _build_preview(
        self,
        objective_id: str,
        action: str,
        execution_mode: str,
        inputs: Mapping[str, Any],
        *,
        selected_parent_candidate_id: Any = None,
        selected_parent_candidate_hash: Any = None,
    ) -> ResearchGovernanceExecutionPreviewV1:
        action = str(action).upper()
        execution_mode = str(execution_mode or "CREATE_AND_ACTIVATE").upper()
        if action not in SUPPORTED_GOVERNANCE_ACTIONS:
            raise GovernanceExecutionError("GOVERNANCE_ACTION_NOT_SUPPORTED", "该治理动作不受支持", status_code=400)
        if action not in inputs["allowed_actions"]:
            raise GovernanceExecutionError("GOVERNANCE_CHOICE_NOT_ALLOWED", "该治理选择不在 canonical 允许范围内", status_code=400)
        if execution_mode not in EXECUTION_MODES:
            raise GovernanceExecutionError("INVALID_EXECUTION_MODE", "治理执行模式不合法", status_code=400)
        objective = inputs["objective"]
        candidates: tuple[Mapping[str, Any], ...] = ()
        if action == "START_PROMISING_FOLLOWUP_OBJECTIVE":
            selected = self._selected_parent_candidate(inputs["promising_candidates"], selected_parent_candidate_id, selected_parent_candidate_hash)
            candidates = (selected,)
        source_hash = stable_hash(_without_timestamps(objective))
        decision = inputs["decision"]
        decision_hash = stable_hash(_without_timestamps(decision))
        budget = self._budget_proposal(action, candidates, objective)
        if action == "START_PROMISING_FOLLOWUP_OBJECTIVE":
            mechanism_scope = sorted({str(item.get("mechanism")) for item in candidates if item.get("mechanism")})
            allowed_factor_scope = tuple(sorted({str(item) for item in (objective.get("allowed_factor_scope") or ()) if str(item)}))
        elif action == "START_NEW_MECHANISM_OBJECTIVE":
            mechanism_scope = list(self._new_mechanism_scope(objective))
            allowed_factor_scope = self._new_mechanism_factor_scope(objective)
        else:
            mechanism_scope = sorted({str(item) for item in (objective.get("mechanism_scope") or ())})[:3]
            allowed_factor_scope = tuple(sorted({str(item) for item in (objective.get("allowed_factor_scope") or ()) if str(item)}))
        if not mechanism_scope:
            mechanism_scope = ["new_mechanism"]
        lineage: list[dict[str, Any]] = [{"relation": "PARENT_OBJECTIVE", "parent_type": "Objective", "parent_id": objective_id, "parent_hash": source_hash, "immutable": True}]
        if action == "START_PROMISING_FOLLOWUP_OBJECTIVE":
            lineage.extend({"relation": "PARENT_PROMISING_CANDIDATE", "parent_type": "Candidate", "parent_id": item["candidate_id"], "parent_hash": item["candidate_hash"], "family_id": item["family_id"], "mechanism": item["mechanism"], "immutable": True, "evidence_ref": "canonical_effective_strategy_registry"} for item in candidates)
        family_identity = {"source_objective_id": objective_id, "action": action, "scope": mechanism_scope, "budget_policy_version": "GOVERNANCE_BUDGET_POLICY_V1", "candidate_slots": budget["candidate_slots"], "parent_candidate_identity_refs": [dict(item) for item in candidates]}
        family_id = None if action == "STOP_RESEARCH" else f"MTF_{action.replace('START_', '').replace('_OBJECTIVE', '')}_V1_{stable_hash(family_identity)[:20].upper()}"
        family = {} if family_id is None else {
            "schema_version": "research-multiple-testing-family-v1",
            "family_id": family_id,
            "family_scope": "所选父候选对应的新 Objective 预注册预测确认" if action == "START_PROMISING_FOLLOWUP_OBJECTIVE" else "本新 Objective 产生的全部预注册预测候选及其独立变体",
            "candidate_membership_policy": "ONE_SHOT：仅允许一个预注册候选，不得生成变体；预测试验预算仅用于主确认与事前定义的稳健性验证。" if action == "START_PROMISING_FOLLOWUP_OBJECTIVE" else "所有在 predictive results 产生前冻结并登记的候选均进入同一 family；不得按结果缩小分母。",
            "adjustment_method": "BENJAMINI_HOCHBERG",
            "q": 0.05,
            "relationship_to_parent": "NEW_INDEPENDENT_FAMILY_WITH_PARENT_LINEAGE",
            "parent_family_inherited": False,
            "hypothesis_slots": budget["candidate_slots"],
            "pre_registered_before_predictive_results": True,
            "immutable_after_confirmation": True,
        }
        no_outcome = NoOutcomeResearchContextV1(
            factor_capability_summary=tuple({"factor_id": str(item), "available": True} for item in allowed_factor_scope),
            mechanism_history=tuple(dict(item) for item in candidates) if candidates else tuple({"mechanism": str(item), "coverage": "canonical mechanism scope"} for item in mechanism_scope),
            failure_class_summaries=({"near_miss_category": "D_MULTIPLE_TESTING_NEAR_MISS", "qualitative_failure_class": "multiple_testing_adjustment_boundary", "source": "sanitized governance evidence"},) if action == "START_PROMISING_FOLLOWUP_OBJECTIVE" else ({"qualitative_failure_class": "sanitized historical failure knowledge", "source": "performance-blind mechanism coverage"},),
            constraints={"parent_objective_id": objective_id, "research_scope_version": "GOVERNANCE_PROMISING_FOLLOWUP_SCOPE_V1" if action == "START_PROMISING_FOLLOWUP_OBJECTIVE" else "GOVERNANCE_NEW_MECHANISM_SCOPE_V1", "no_parameter_retuning": True, "candidate_generation_policy": "ONE_SHOT" if action == "START_PROMISING_FOLLOWUP_OBJECTIVE" else "PREREGISTERED", "candidate_variants_allowed": action != "START_PROMISING_FOLLOWUP_OBJECTIVE", "final_test_access": "DISABLED", "prospective_access": "DISABLED", "real_order_execution": "DISABLED"},
        )
        no_outcome_payload = no_outcome.to_dict()
        PerformanceBlindGuard.assert_blind(no_outcome_payload)
        identity = {
            "parent_objective_id": objective_id,
            "parent_objective_hash": source_hash,
            "governance_action": action,
            "research_scope_version": no_outcome.constraints["research_scope_version"],
            "budget_policy_version": budget["policy_version"],
            "multiple_testing_family_definition": _without_timestamps(family),
            "no_outcome_policy_version": "no-outcome-research-context-v1",
            "candidate_eligibility_policy": {"frozen_contract_required": True, "performance_blind_design": True},
            "allowed_factor_scope": list(allowed_factor_scope),
            "research_window_policy": {"source": "parent_objective", "holding_horizon": objective.get("holding_horizon", [2, 10]), "preferred_horizon": objective.get("preferred_horizon", [5, 8])},
            "execution_semantics_version": "GOVERNANCE_EXECUTION_SEMANTICS_V1",
            "parent_candidate_identity_refs": [
                {
                    "candidate_id": item["candidate_id"],
                    "candidate_hash": item["candidate_hash"],
                    "family_id": item["family_id"],
                    "mechanism": item["mechanism"],
                }
                for item in candidates
            ],
        }
        identity_hash = stable_hash(identity)
        short_name = "PROMISING_FOLLOWUP" if action == "START_PROMISING_FOLLOWUP_OBJECTIVE" else "NEW_MECHANISM"
        new_objective_id = None if action == "STOP_RESEARCH" else f"RESEARCH_OBJECTIVE_GOVERNED_{short_name}_V1_{identity_hash[:20].upper()}"
        objective_identity = {"identity_hash": identity_hash, "semantic_inputs": identity, "immutable": True} if new_objective_id else {}
        return ResearchGovernanceExecutionPreviewV1(
            decision_id=str(inputs["decision"].get("decision_id") or ""),
            source_objective_id=objective_id,
            source_objective_hash=source_hash,
            source_governance_hash=decision_hash,
            source_budget_head_hash=str(inputs["budget"]["head_hash"]),
            source_state=str(inputs["source_state"]),
            governance_action=action,
            execution_mode=execution_mode,
            selected_parent_candidate_id=str(candidates[0]["candidate_id"]) if candidates else None,
            selected_parent_candidate_hash=str(candidates[0]["candidate_hash"]) if candidates else None,
            proposed_new_objective_id=new_objective_id,
            proposed_new_objective_identity=objective_identity,
            parent_lineage=tuple(lineage),
            parent_candidate_identity_refs=tuple(
                {
                    "candidate_id": item["candidate_id"],
                    "candidate_hash": item["candidate_hash"],
                    "family_id": item["family_id"],
                    "mechanism": item["mechanism"],
                }
                for item in candidates
            ),
            research_purpose="保持当前研究终态不变，记录人工治理结论。" if action == "STOP_RESEARCH" else "验证机制是否能在新的预注册范围、独立多重检验家族和有效研究分区下保持可重复结构；不以历史结果调参。",
            mechanism_scope=tuple(mechanism_scope),
            allowed_factor_scope=allowed_factor_scope,
            no_outcome_policy={"schema_version": "no-outcome-research-context-v1", "policy_version": "no-outcome-research-context-v1", "context_preview": no_outcome_payload, "forbidden": ["return", "pnl", "profit_factor", "win_rate", "drawdown", "private_p_value", "performance_ranking"]},
            candidate_eligibility={"frozen_contract_required": action != "STOP_RESEARCH", "performance_blind_design": True, "new_candidate_identity_required": True, "new_trial_identity_required": True, "candidate_generation_policy": "ONE_SHOT" if action == "START_PROMISING_FOLLOWUP_OBJECTIVE" else "PREREGISTERED", "candidate_variants_allowed": action != "START_PROMISING_FOLLOWUP_OBJECTIVE"},
            budget_proposal=budget,
            multiple_testing_family=family,
            stop_conditions={"final_test": "SEALED", "prospective": "DISABLED", "real_order": "DISABLED", "no_auto_budget_expansion": True, "no_parent_mutation": True},
            final_test_status={"state": "SEALED", "access": {"analytical": 0, "decision": 0, "physical": 0}},
            prospective_status="DISABLED",
            real_order_status="DISABLED",
            generated_at=self._now(),
        )

    def preview(
        self,
        objective_id: str,
        action: str,
        *,
        execution_mode: str = "CREATE_AND_ACTIVATE",
        selected_parent_candidate_id: Any = None,
        selected_parent_candidate_hash: Any = None,
    ) -> dict[str, Any]:
        with self._mutex:
            inputs = self._canonical_inputs(objective_id)
            return self._build_preview(
                objective_id,
                action,
                execution_mode,
                inputs,
                selected_parent_candidate_id=selected_parent_candidate_id,
                selected_parent_candidate_hash=selected_parent_candidate_hash,
            ).to_dict()

    def _transaction_paths(self, source_objective_id: str, execution_id: str) -> tuple[Path, Path, Path]:
        base = self.root / "reports" / "research_governance_execution" / source_objective_id
        return base / "transactions" / f"{execution_id}.json", base / execution_id / "receipt.json", self.root / "reports" / ".governance_staging" / execution_id

    def _new_objective_payload(self, preview: ResearchGovernanceExecutionPreviewV1, objective: Mapping[str, Any]) -> dict[str, Any]:
        risk = deepcopy(dict(objective.get("risk_constraints") or {}))
        risk.update({"final_test_access": "DISABLED", "prospective_access": "DISABLED", "real_order_execution": "DISABLED", "recommendation": "DISABLED"})
        budget = preview.budget_proposal
        return {
            "schema_version": "research-objective-v1",
            "objective_id": preview.proposed_new_objective_id,
            "created_at": preview.generated_at,
            "lifecycle_state": "READY",
            "activation_policy": preview.execution_mode,
            "activation_authorized": preview.execution_mode == "CREATE_AND_ACTIVATE",
            "parent_objective_id": preview.source_objective_id,
            "governance_action": preview.governance_action,
            "objective_identity_hash": preview.proposed_new_objective_identity["identity_hash"],
            "parent_lineage": list(preview.parent_lineage),
            "research_universe": list(objective.get("research_universe") or ["SH", "SZ"]),
            "capital_reference": float(objective.get("capital_reference", 10000.0)),
            "holding_horizon": list(objective.get("holding_horizon") or [2, 10]),
            "preferred_horizon": list(objective.get("preferred_horizon") or [5, 8]),
            "mechanism_scope": list(preview.mechanism_scope),
            "allowed_factor_scope": list(preview.allowed_factor_scope),
            "risk_constraints": risk,
            "research_priority": list(objective.get("research_priority") or []),
            "max_batches": 1,
            "max_total_trials": int(budget.get("proposed_total_predictive_budget", 0)),
            "seed": int(objective.get("seed", 20260824)),
            "governance_policy_hash": str(objective.get("governance_policy_hash") or ""),
            "research_scope_version": preview.proposed_new_objective_identity["semantic_inputs"]["research_scope_version"],
            "budget_policy_version": budget["policy_version"],
            "multiple_testing_family_id": preview.multiple_testing_family.get("family_id"),
            "no_outcome_context_ref": f"data/research/research_factory/no_outcome_context/{preview.proposed_new_objective_id}.json",
            "candidate_eligibility_policy": dict(preview.candidate_eligibility),
            "research_window_policy": dict(preview.proposed_new_objective_identity["semantic_inputs"]["research_window_policy"]),
            "execution_semantics_version": "GOVERNANCE_EXECUTION_SEMANTICS_V1",
            "final_test_access": {"analytical": 0, "decision": 0, "physical": 0},
            "prospective": "DISABLED",
            "real_order": "DISABLED",
        }

    def _prepare_transaction(self, preview: ResearchGovernanceExecutionPreviewV1, inputs: Mapping[str, Any], execution_id: str, idempotency_key: str) -> dict[str, Any]:
        transaction_path, receipt_path, staging_root = self._transaction_paths(preview.source_objective_id, execution_id)
        files: list[dict[str, Any]] = []

        def stage_json(relative_target: str, payload: Any, checkpoint: str) -> None:
            staged = staging_root / relative_target
            self._atomic_write(staged, payload)
            files.append({"target": relative_target, "staged": self._relative(staged), "sha256": _sha256_bytes(staged.read_bytes()), "checkpoint": checkpoint, "receipt": False})

        if preview.proposed_new_objective_id:
            new_id = preview.proposed_new_objective_id
            objective_payload = self._new_objective_payload(preview, inputs["objective"])
            stage_json(f"data/research/research_factory/objectives/{new_id}.json", objective_payload, "after_objective_write")
            batch_id = f"{new_id}_B01"
            budget_stage = staging_root / f"data/research/research_factory/batches/{batch_id}/search_budget_registry.json"
            registry = SearchBudgetRegistryV1(new_id, budget_stage)
            registry.register_objective(int(preview.budget_proposal["proposed_total_predictive_budget"]))
            registry.register_batch(batch_id, int(preview.budget_proposal["proposed_total_predictive_budget"]))
            registry.register_family(str(preview.multiple_testing_family["family_id"]), int(preview.budget_proposal["candidate_slots"]))
            files.append({"target": f"data/research/research_factory/batches/{batch_id}/search_budget_registry.json", "staged": self._relative(budget_stage), "sha256": _sha256_bytes(budget_stage.read_bytes()), "checkpoint": "after_budget_write", "receipt": False})
            family_relative = f"data/research/research_factory/multiple_testing/{new_id}/{preview.multiple_testing_family['family_id']}.json"
            stage_json(family_relative, {**dict(preview.multiple_testing_family), "objective_id": new_id}, "after_family_write")
            context_relative = f"data/research/research_factory/no_outcome_context/{new_id}.json"
            stage_json(context_relative, preview.no_outcome_policy["context_preview"], "after_context_write")
            stage_json(f"data/research/research_factory/batches/{batch_id}/batch_plan.json", {"schema_version": "research-batch-plan-v1", "batch_id": batch_id, "objective_id": new_id, "max_hypotheses": preview.budget_proposal["candidate_slots"], "max_candidates": preview.budget_proposal["candidate_slots"], "max_performance_trials": preview.budget_proposal["proposed_total_predictive_budget"], "decision_family_id": preview.multiple_testing_family["family_id"], "performance_blind_design": True, "parent_objective_id": preview.source_objective_id, "created_at": preview.generated_at}, "after_batch_plan_write")
            graph_relative = f"data/research/research_factory/artifact_graph/{new_id}.json"
            graph_stage = staging_root / graph_relative
            graph = ResearchArtifactGraphV1(graph_stage)
            graph.add_node(f"objective:{preview.source_objective_id}", "Objective", {"objective_id": preview.source_objective_id, "objective_hash": preview.source_objective_hash})
            graph.add_node(f"objective:{new_id}", "Objective", {"objective_id": new_id, "objective_identity_hash": preview.proposed_new_objective_identity["identity_hash"]})
            graph.add_edge(f"objective:{new_id}", "GENERATED_FROM", f"objective:{preview.source_objective_id}")
            for item in preview.parent_lineage:
                if item.get("parent_type") != "Candidate":
                    continue
                node_id = f"candidate:{item['parent_id']}"
                graph.add_node(node_id, "Candidate", {"candidate_id": item.get("parent_id"), "candidate_hash": item.get("parent_hash"), "family_id": item.get("family_id"), "mechanism": item.get("mechanism")})
                graph.add_edge(f"objective:{new_id}", "GENERATED_FROM", node_id)
            files.append({"target": graph_relative, "staged": self._relative(graph_stage), "sha256": _sha256_bytes(graph_stage.read_bytes()), "checkpoint": "after_lineage_write", "receipt": False})
            eligibility_relative = f"reports/research_orchestrator_v2/{new_id}/activation_eligibility.json"
            stage_json(eligibility_relative, {"schema_version": "research-orchestrator-activation-eligibility-v1", "objective_id": new_id, "eligibility": "READY", "activation_mode": preview.execution_mode, "activation_authorized": preview.execution_mode == "CREATE_AND_ACTIVATE", "target_state": "NEED_AI_RESEARCH_DESIGN", "no_codex_invoked": True, "final_test": "SEALED", "prospective": "DISABLED", "real_order": "DISABLED"}, "after_eligibility_write")
            lineage_relative = f"data/research/research_factory/lineage/{new_id}.json"
            stage_json(lineage_relative, {"schema_version": "research-parent-lineage-v1", "lineage_status": "IMMUTABLE", "objective_id": new_id, "parent_lineage": list(preview.parent_lineage), "artifact_graph_ref": graph_relative}, "after_lineage_manifest_write")

        receipt = ResearchGovernanceExecutionReceiptV1(
            execution_id=execution_id,
            decision_id=preview.decision_id,
            preview_hash=preview.preview_hash,
            source_objective_id=preview.source_objective_id,
            new_objective_id=preview.proposed_new_objective_id,
            new_budget_id=f"SEARCH_BUDGET_{preview.proposed_new_objective_id}" if preview.proposed_new_objective_id else None,
            multiple_testing_family_id=str(preview.multiple_testing_family.get("family_id")) if preview.multiple_testing_family.get("family_id") else None,
            lineage_refs=tuple(f"{item['relation']}:{item['parent_id']}" for item in preview.parent_lineage),
            artifact_graph_refs=(f"data/research/research_factory/artifact_graph/{preview.proposed_new_objective_id}.json",) if preview.proposed_new_objective_id else (),
            created_at=self._now(),
            result_state="STOP_RESEARCH_RECORDED" if preview.governance_action == "STOP_RESEARCH" else "OBJECTIVE_CREATED_READY_FOR_ORCHESTRATOR",
            execution_mode=preview.execution_mode,
            idempotency_key=idempotency_key,
        )
        receipt_stage = staging_root / "receipt.json"
        self._atomic_write(receipt_stage, receipt.to_dict())
        journal = {
            "schema_version": "research-governance-execution-transaction-v1",
            "status": "PREPARED",
            "execution_id": execution_id,
            "decision_id": preview.decision_id,
            "preview_hash": preview.preview_hash,
            "governance_action": preview.governance_action,
            "source_objective_id": preview.source_objective_id,
            "files": files,
            "receipt": {"target": self._relative(receipt_path), "staged": self._relative(receipt_stage), "sha256": _sha256_bytes(receipt_stage.read_bytes()), "checkpoint": "after_receipt", "receipt": True},
            "receipt_payload": receipt.to_dict(),
            "created_at": self._now(),
        }
        self._atomic_write(transaction_path, journal)
        return journal

    def _verify_or_move(self, entry: Mapping[str, Any]) -> None:
        target = self.root / str(entry["target"])
        staged = self.root / str(entry["staged"])
        expected = str(entry["sha256"])
        if target.exists():
            if _sha256_bytes(target.read_bytes()) != expected:
                raise GovernanceExecutionError("GOVERNANCE_RECOVERY_CONFLICT", f"治理恢复发现目标文件冲突：{entry['target']}", status_code=503)
            return
        if not staged.exists():
            raise GovernanceExecutionError("GOVERNANCE_STAGING_MISSING", f"治理恢复缺少暂存文件：{entry['staged']}", status_code=503)
        if _sha256_bytes(staged.read_bytes()) != expected:
            raise GovernanceExecutionError("GOVERNANCE_STAGING_HASH_MISMATCH", f"治理暂存文件校验失败：{entry['staged']}", status_code=503)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, target)

    def _commit_transaction(self, journal_path: Path, journal: dict[str, Any]) -> dict[str, Any]:
        journal["status"] = "COMMITTING"
        self._atomic_write(journal_path, journal)
        for entry in journal.get("files", ()):
            self._verify_or_move(entry)
            checkpoint = str(entry.get("checkpoint") or "")
            if checkpoint and self.crash_at == checkpoint:
                raise RuntimeError(f"SYNTHETIC_GOVERNANCE_CRASH:{checkpoint}")
        receipt = journal["receipt"]
        if self.crash_at == "before_execution_receipt":
            raise RuntimeError("SYNTHETIC_GOVERNANCE_CRASH:before_execution_receipt")
        self._verify_or_move(receipt)
        if self.crash_at == "after_receipt":
            raise RuntimeError("SYNTHETIC_GOVERNANCE_CRASH:after_receipt")
        journal["status"] = "COMPLETED"
        journal["completed_at"] = self._now()
        self._atomic_write(journal_path, journal)
        return dict(journal["receipt_payload"])

    def _recover_transaction(self, journal_path: Path, journal: Mapping[str, Any]) -> dict[str, Any]:
        receipt_path = self.root / str(journal["receipt"]["target"])
        if receipt_path.exists():
            for entry in journal.get("files", ()):
                self._verify_or_move(entry)
            payload = self._read_json(receipt_path)
            if payload is None:
                raise GovernanceExecutionError("GOVERNANCE_RECEIPT_UNREADABLE", "治理执行回执不可读", status_code=503)
            completed = dict(journal)
            completed["status"] = "COMPLETED"
            completed["completed_at"] = completed.get("completed_at") or self._now()
            self._atomic_write(journal_path, completed)
            return dict(payload)
        return self._commit_transaction(journal_path, dict(journal))

    def _execution_id(self, preview: ResearchGovernanceExecutionPreviewV1) -> str:
        return f"GOVERNANCE_EXECUTION_{stable_hash({'decision_id': preview.decision_id, 'preview_hash': preview.preview_hash, 'action': preview.governance_action})[:20]}"

    def confirm(self, objective_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._mutex:
            objective_id = _safe_id(objective_id, kind="objective_id")
            if payload.get("confirmed") is not True:
                raise GovernanceExecutionError("CONFIRMATION_REQUIRED", "执行治理决定需要明确确认", status_code=400)
            decision_id = _safe_id(payload.get("decision_id"), kind="decision_id")
            preview_hash = _safe_id(payload.get("preview_hash"), kind="preview_hash")
            confirmation_token = _safe_id(payload.get("confirmation_token"), kind="confirmation_token")
            idempotency_key = _safe_id(payload.get("idempotency_key"), kind="idempotency_key")
            action = str(payload.get("governance_action") or payload.get("action") or "").upper()
            execution_mode = str(payload.get("execution_mode") or "CREATE_AND_ACTIVATE").upper()
            inputs = self._canonical_inputs(objective_id)
            try:
                preview = self._build_preview(
                    objective_id,
                    action,
                    execution_mode,
                    inputs,
                    selected_parent_candidate_id=payload.get("selected_parent_candidate_id"),
                    selected_parent_candidate_hash=payload.get("selected_parent_candidate_hash"),
                )
            except GovernanceExecutionError as exc:
                if action == "START_PROMISING_FOLLOWUP_OBJECTIVE" and exc.code in {"PARENT_CANDIDATE_NOT_PROMISING", "PARENT_CANDIDATE_HASH_MISMATCH"}:
                    raise GovernanceExecutionError("STALE_GOVERNANCE_PREVIEW", "研究状态已经变化，请重新确认下一轮研究方案。", status_code=409) from exc
                raise
            if preview.decision_id != decision_id or preview.preview_hash != preview_hash or preview.confirmation_token != confirmation_token:
                raise GovernanceExecutionError("STALE_GOVERNANCE_PREVIEW", "研究状态已经变化，请重新确认下一轮研究方案。", status_code=409, details={"expected_preview_hash": preview.preview_hash})
            execution_id = self._execution_id(preview)
            transaction_path, receipt_path, _ = self._transaction_paths(objective_id, execution_id)
            if receipt_path.exists():
                receipt = self._read_json(receipt_path)
                if receipt and str(receipt.get("preview_hash")) == preview.preview_hash:
                    return {**dict(receipt), "idempotent": True, "message_zh": "治理执行已经完成，本次重复提交未创建重复目标。"}
            existing = self._read_json(transaction_path, required=False)
            if existing:
                if str(existing.get("preview_hash")) != preview.preview_hash or str(existing.get("governance_action")) != action:
                    raise GovernanceExecutionError("GOVERNANCE_EXECUTION_CONFLICT", "同一治理决定已有不一致的执行记录", status_code=503)
                if self.crash_at == "after_preview_confirmation":
                    raise RuntimeError("SYNTHETIC_GOVERNANCE_CRASH:after_preview_confirmation")
                receipt = self._recover_transaction(transaction_path, existing)
                return {**receipt, "idempotent": True, "message_zh": "治理执行已恢复并完成，未创建重复目标。"}
            if self.crash_at == "after_preview_confirmation":
                journal = self._prepare_transaction(preview, inputs, execution_id, idempotency_key)
                raise RuntimeError("SYNTHETIC_GOVERNANCE_CRASH:after_preview_confirmation")
            journal = self._prepare_transaction(preview, inputs, execution_id, idempotency_key)
            receipt = self._commit_transaction(transaction_path, journal)
            return {**receipt, "idempotent": False, "message_zh": "治理决定已安全执行。"}

    def get_execution(self, objective_id: str, execution_id: str) -> dict[str, Any]:
        objective_id = _safe_id(objective_id, kind="objective_id")
        execution_id = _safe_id(execution_id, kind="execution_id")
        _, receipt_path, _ = self._transaction_paths(objective_id, execution_id)
        payload = self._read_json(receipt_path, required=False)
        if payload is None:
            raise GovernanceExecutionError("GOVERNANCE_EXECUTION_NOT_FOUND", "未找到治理执行回执", status_code=404)
        return dict(payload)

    def recover(self, objective_id: str, execution_id: str) -> dict[str, Any]:
        """Complete a prepared or partially committed journal after a process restart."""
        with self._mutex:
            objective_id = _safe_id(objective_id, kind="objective_id")
            execution_id = _safe_id(execution_id, kind="execution_id")
            transaction_path, receipt_path, _ = self._transaction_paths(objective_id, execution_id)
            if receipt_path.exists():
                return self.get_execution(objective_id, execution_id)
            journal = self._read_json(transaction_path)
            return {**self._recover_transaction(transaction_path, journal), "idempotent": True, "message_zh": "治理执行已恢复并完成，未创建重复目标。"}

    def execute_confirmation(self, objective_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.confirm(objective_id, payload)


__all__ = [
    "EXECUTION_MODES",
    "GovernanceExecutionError",
    "ResearchGovernanceExecutionPreviewV1",
    "ResearchGovernanceExecutionReceiptV1",
    "ResearchGovernanceExecutionServiceV1",
    "SUPPORTED_GOVERNANCE_ACTIONS",
]
