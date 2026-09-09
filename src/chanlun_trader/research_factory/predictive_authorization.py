"""Structural-PASS governance entry for the first predictive Trial.

This module records the human governance decision only. Trial creation, budget
reservation, performance access and predictive execution remain a separate,
later boundary.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from ..research_daemon_state import (
    DaemonAlreadyRunningError,
    DaemonCheckpointStoreV1,
    DaemonInstanceLockV1,
    ResearchDaemonState,
)
from .budget import SearchBudgetRegistryV1
from .common import now_timestamp, stable_hash
from .structural_reconciliation import _trial_events


DECISION_MODE = "STRUCTURAL_PASS_PREDICTIVE_AUTHORIZATION_REQUIRED"
AUTHORIZE_FIRST_PREDICTIVE_TRIAL = "AUTHORIZE_FIRST_PREDICTIVE_TRIAL"
DEFER_PREDICTIVE_TRIAL = "DEFER_PREDICTIVE_TRIAL"
END_CANDIDATE_RESEARCH_DIRECTION = "END_CANDIDATE_RESEARCH_DIRECTION"
DECISION_TYPES = frozenset({
    AUTHORIZE_FIRST_PREDICTIVE_TRIAL,
    DEFER_PREDICTIVE_TRIAL,
    END_CANDIDATE_RESEARCH_DIRECTION,
})
DECISION_ALIASES = {
    "END_CURRENT_CANDIDATE": END_CANDIDATE_RESEARCH_DIRECTION,
    "END_CURRENT_CANDIDATE_RESEARCH_DIRECTION": END_CANDIDATE_RESEARCH_DIRECTION,
}
DECISION_STATUSES = {
    AUTHORIZE_FIRST_PREDICTIVE_TRIAL: "AUTHORIZED",
    DEFER_PREDICTIVE_TRIAL: "DEFERRED",
    END_CANDIDATE_RESEARCH_DIRECTION: "ENDED",
}
DECISION_NEXT_ACTIONS = {
    "AUTHORIZED": "START_PREDICTIVE_TRIAL_1",
    "DEFERRED": "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED",
    "ENDED": "CANDIDATE_RESEARCH_DIRECTION_ENDED",
}
AUTHORIZATION_SCHEMA_VERSION = "predictive-governance-decision-v1"
PREVIEW_SCHEMA_VERSION = "predictive-governance-preview-v1"
LEDGER_FILENAME = "predictive_governance_decisions.jsonl"
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


def predictive_decision_semantics_valid(record: Mapping[str, Any]) -> bool:
    """Validate recorded semantics without completing or rewriting history."""
    required = (
        "objective_id", "candidate_id", "candidate_hash", "decision_type",
        "authorization_id", "preview_hash", "structural_reconciliation_id",
        "governance_decision_id", "decision_id", "confirmation_token_hash",
    )
    if any(not isinstance(record.get(key), str) or not record[key] for key in required):
        return False
    status = DECISION_STATUSES.get(record["decision_type"])
    return (
        status is not None
        and record.get("decision_status") == status
        and record.get("next_action") == DECISION_NEXT_ACTIONS[status]
        and record.get("structural_status") == "PASS"
    )


class PredictiveGovernanceError(RuntimeError):
    """Safe, local error for the predictive governance boundary."""

    def __init__(self, code: str, message_zh: str, *, status_code: int = 409, details: Mapping[str, Any] | None = None):
        super().__init__(message_zh)
        self.code = str(code)
        self.message_zh = str(message_zh)
        self.status_code = int(status_code)
        self.details = dict(details or {})

    def envelope(self) -> dict[str, Any]:
        return {"code": self.code, "message_zh": self.message_zh, "details": dict(self.details)}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    if isinstance(value, tuple):
        return [_copy(item) for item in value]
    return value


class PredictiveGovernanceServiceV1:
    """Preview and record a structural-PASS human governance decision."""

    def __init__(self, root: str | Path, *, clock: Callable[[], str] | None = None):
        self.root = Path(root).resolve()
        self._clock = clock or now_timestamp

    @property
    def ledger_filename(self) -> str:
        return LEDGER_FILENAME

    def _safe_id(self, value: Any, name: str) -> str:
        text = str(value or "")
        if not SAFE_ID.fullmatch(text):
            raise PredictiveGovernanceError("INVALID_IDENTIFIER", f"{name} 标识不合法", status_code=400)
        return text

    def _store(self, objective_id: str) -> DaemonCheckpointStoreV1:
        return DaemonCheckpointStoreV1(self.root, objective_id)

    def _objective(self, objective_id: str) -> Mapping[str, Any]:
        path = self.root / "data/research/research_factory/objectives" / f"{objective_id}.json"
        if not path.is_file():
            raise PredictiveGovernanceError("UNKNOWN_OBJECTIVE", "未找到请求的研究 objective", status_code=404)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PredictiveGovernanceError("OBJECTIVE_SOURCE_UNREADABLE", "研究 objective 暂时不可读", status_code=503) from exc
        if not isinstance(payload, Mapping) or str(payload.get("objective_id")) != objective_id:
            raise PredictiveGovernanceError("OBJECTIVE_SOURCE_MISMATCH", "研究 objective 身份校验失败", status_code=503)
        return payload

    def _governance(self, objective_id: str) -> Mapping[str, Any]:
        path = self.root / "reports/research_orchestrator_v2" / objective_id / "structural_governance_decision_required.json"
        if not path.is_file():
            raise PredictiveGovernanceError("PREDICTIVE_GOVERNANCE_NOT_FOUND", "当前 objective 没有结构 PASS 后的预测治理入口", status_code=404)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PredictiveGovernanceError("PREDICTIVE_GOVERNANCE_SOURCE_UNREADABLE", "预测治理状态暂时不可读", status_code=503) from exc
        if not isinstance(payload, Mapping) or str(payload.get("objective_id") or objective_id) != objective_id:
            raise PredictiveGovernanceError("OBJECTIVE_SOURCE_MISMATCH", "预测治理状态不属于请求的 objective", status_code=409)
        return payload

    def _ledger_path(self, objective_id: str) -> Path:
        return self.root / "reports/research_orchestrator_v2" / objective_id / LEDGER_FILENAME

    def _ledger(self, objective_id: str) -> list[dict[str, Any]]:
        path = self._ledger_path(objective_id)
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise PredictiveGovernanceError("PREDICTIVE_GOVERNANCE_LEDGER_UNREADABLE", "预测治理决定账本暂时不可读", status_code=503) from exc
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PredictiveGovernanceError("PREDICTIVE_GOVERNANCE_LEDGER_INVALID", "预测治理决定账本存在无法解析的记录", status_code=503) from exc
            if not isinstance(item, Mapping):
                raise PredictiveGovernanceError("PREDICTIVE_GOVERNANCE_LEDGER_INVALID", "预测治理决定账本格式不受支持", status_code=503)
            rows.append(dict(item))
        return rows

    @staticmethod
    def normalize_decision(value: Any) -> str:
        choice = str(value or "").strip().upper()
        return DECISION_ALIASES.get(choice, choice)

    @staticmethod
    def choices() -> list[dict[str, Any]]:
        return [
            {
                "choice": AUTHORIZE_FIRST_PREDICTIVE_TRIAL,
                "label_zh": "授权进入第 1 次预测试验",
                "consequence_zh": "只记录进入第 1 次预测试验的授权，不创建 Trial、不预留预算；之后仍需单独启动第 1 次预测试验。",
                "creates_trial": False,
                "creates_authorization": True,
                "requires_explicit_confirmation": True,
            },
            {
                "choice": DEFER_PREDICTIVE_TRIAL,
                "label_zh": "暂不启动预测试验",
                "consequence_zh": "保持结构 PASS、Trial 数为 0 和预算原状，之后仍可再次进行治理确认。",
                "creates_trial": False,
                "creates_authorization": False,
                "requires_explicit_confirmation": True,
            },
            {
                "choice": END_CANDIDATE_RESEARCH_DIRECTION,
                "label_zh": "结束当前 Candidate / research direction",
                "consequence_zh": "关闭当前 Candidate 研究路径，不创建预测 Trial，不把结构 PASS 改写为绩效失败。",
                "creates_trial": False,
                "creates_authorization": False,
                "requires_explicit_confirmation": True,
            },
        ]

    def _budget(self, objective_id: str, checkpoint: Any, objective: Mapping[str, Any]) -> dict[str, Any]:
        ref = str((checkpoint.budget_view or {}).get("registry_path") or "")
        path = (self.root / ref).resolve() if ref else None
        if path is None or not path.is_file() or not path.is_relative_to(self.root):
            options = []
            for candidate in sorted((self.root / "data/research/research_factory/batches").glob("*/search_budget_registry.json")):
                try:
                    payload = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                if isinstance(payload, Mapping) and str(payload.get("objective_id")) == objective_id:
                    options.append(candidate)
            path = options[-1] if options else None
        if path is not None and path.is_file():
            registry = SearchBudgetRegistryV1(objective_id, path)
            snapshot = registry.snapshot()
            bucket = next((item for item in snapshot.get("buckets", ()) if item.get("kind") == "objective" and item.get("key") == objective_id), {})
            return {
                "objective_id": objective_id,
                "used": _int(bucket.get("used")),
                "reserved": _int(bucket.get("reserved")),
                "remaining": _int(bucket.get("remaining")),
                "total": _int(bucket.get("limit"), _int(objective.get("max_total_trials"))),
                "registry_path": path.relative_to(self.root).as_posix(),
                "registry_head_hash": registry.head_hash,
            }
        raw = dict(checkpoint.budget_view or {})
        return {
            "objective_id": objective_id,
            "used": _int(raw.get("used")),
            "reserved": _int(raw.get("reserved")),
            "remaining": _int(raw.get("remaining")),
            "total": _int(raw.get("total"), _int(objective.get("max_total_trials"))),
            "registry_path": ref or None,
            "registry_head_hash": raw.get("registry_head_hash"),
        }

    def _lock_active(self, store: DaemonCheckpointStoreV1) -> bool:
        if not store.lock_path.exists():
            return False
        try:
            payload = json.loads(store.lock_path.read_text(encoding="utf-8"))
            pid = _int(payload.get("pid")) if isinstance(payload, Mapping) else 0
        except (OSError, UnicodeError, json.JSONDecodeError):
            return True
        return pid != 0 and pid != os.getpid() and DaemonInstanceLockV1._pid_alive(pid)

    def _snapshot(self, objective_id: str) -> dict[str, Any]:
        objective = self._objective(objective_id)
        governance = self._governance(objective_id)
        store = self._store(objective_id)
        checkpoint = store.load()
        if checkpoint is None:
            raise PredictiveGovernanceError("DAEMON_CHECKPOINT_NOT_FOUND", "当前 objective 缺少 canonical daemon checkpoint", status_code=503)
        refs = dict(checkpoint.canonical_refs or {})
        structural_result = dict(refs.get("last_structural_result") or {})
        details = dict(structural_result.get("details") or {})
        identity_details = details
        for nested_key in ("v2_result", "v1_result"):
            nested = details.get(nested_key) if isinstance(details.get(nested_key), Mapping) else {}
            if nested.get("candidate_id"):
                identity_details = dict(nested)
                break
        structural = dict(governance.get("structural") or {})
        candidate_id = str(governance.get("candidate_id") or identity_details.get("candidate_id") or "")
        candidate_hash = str(governance.get("candidate_hash") or identity_details.get("candidate_hash") or "")
        reconciliation = dict(refs.get("structural_reconciliation") or {})
        reconciliation_id = str(governance.get("reconciliation_id") or reconciliation.get("reconciliation_id") or "")
        last_completed = dict(checkpoint.last_completed_candidate or {})
        trial_events = _trial_events(self.root, objective_id, candidate_id) if candidate_id else []
        budget = self._budget(objective_id, checkpoint, objective)
        final_test = dict(governance.get("final_test_access") or {})
        final_test = {key: _int(final_test.get(key)) for key in ("analytical", "decision", "physical")}
        rows = self._ledger(objective_id)
        latest = rows[-1] if rows else None
        reasons: list[str] = []
        if str(governance.get("decision_mode")) != DECISION_MODE:
            reasons.append("当前治理状态不是结构 PASS 后的预测授权模式")
        if str(governance.get("status")) not in {"PENDING_HUMAN_DECISION", "DEFERRED"}:
            reasons.append("当前结构治理状态不是可供人工决定的状态")
        if checkpoint.current_state not in {ResearchDaemonState.READY.value, ResearchDaemonState.STRUCTURAL_PASS.value}:
            reasons.append(f"daemon 当前不是结构 PASS 后的安全边界：{checkpoint.current_state}")
        if checkpoint.required_action not in {"PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED", "PREDICTIVE_GOVERNANCE_DEFERRED"}:
            reasons.append(f"当前 required_action 不允许治理入口：{checkpoint.required_action}")
        if structural_result.get("status") != "PASS" or structural.get("status") != "PASS":
            reasons.append("结构预检尚未形成 canonical PASS")
        if str(identity_details.get("candidate_id") or "") != candidate_id:
            reasons.append("结构结果与治理 Candidate 身份不一致")
        if str(identity_details.get("candidate_hash") or "") != candidate_hash:
            reasons.append("结构结果与治理 Candidate hash 不一致")
        if str(reconciliation.get("status") or "") != "PASS" or not reconciliation_id:
            reasons.append("结构结果尚未完成 canonical reconciliation")
        integrity = dict(details.get("lower_bound_integrity") or identity_details.get("lower_bound_integrity") or {})
        if integrity.get("status") != "PASS" or integrity.get("failure_codes"):
            reasons.append("lower-bound integrity 尚未通过")
        if not last_completed or str(last_completed.get("candidate_id")) != candidate_id or str(last_completed.get("candidate_hash")) != candidate_hash:
            reasons.append("当前 Candidate 不是 checkpoint 中冻结并完成结构阶段的 Candidate")
        contract_ref = str(last_completed.get("contract_ref") or "")
        contract_path = (self.root / contract_ref).resolve() if contract_ref else None
        if contract_path is None or not contract_path.is_file() or not contract_path.is_relative_to(self.root):
            reasons.append("当前 Candidate 缺少可核对的冻结合同")
        else:
            try:
                contract_payload = json.loads(contract_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                contract_payload = None
            contracts = contract_payload.get("contracts", ()) if isinstance(contract_payload, Mapping) else ()
            frozen_match = next(
                (
                    item
                    for item in contracts
                    if isinstance(item, Mapping)
                    and str(item.get("candidate_id")) == candidate_id
                    and str(item.get("candidate_hash")) == candidate_hash
                ),
                None,
            )
            if frozen_match is None:
                reasons.append("冻结合同与当前 Candidate 身份不一致")
        if checkpoint.current_trial is not None:
            reasons.append("当前存在活动 Trial")
        if trial_events:
            reasons.append("当前 Candidate 已存在 TrialLedger 记录")
        if budget["remaining"] < 1 or budget["reserved"] != 0:
            reasons.append("第 1 次预测试验预算不可用或已有预留")
        if final_test != {"analytical": 0, "decision": 0, "physical": 0}:
            reasons.append("Final Test 已不是关闭状态")
        if str(governance.get("prospective") or "") != "DISABLED":
            reasons.append("Prospective 不是关闭状态")
        if str(governance.get("real_order") or "") != "DISABLED":
            reasons.append("Real Order 不是禁用状态")
        if latest and (str(latest.get("candidate_id")) != candidate_id or str(latest.get("candidate_hash")) != candidate_hash):
            reasons.append("已有治理记录与当前 Candidate 身份冲突")
        if self._lock_active(store):
            reasons.append("当前 objective 存在活动运行锁")
        return {
            "objective": dict(objective),
            "objective_hash": str(objective.get("objective_identity_hash") or objective.get("objective_hash") or ""),
            "governance": dict(governance),
            "checkpoint": checkpoint,
            "store": store,
            "candidate_id": candidate_id,
            "candidate_hash": candidate_hash,
            "reconciliation_id": reconciliation_id,
            "structural": structural,
            "structural_result": structural_result,
            "last_completed_candidate": last_completed,
            "trial_count": len(trial_events),
            "budget": budget,
            "final_test_access": final_test,
            "prospective": str(governance.get("prospective") or "UNKNOWN"),
            "real_order": str(governance.get("real_order") or "UNKNOWN"),
            "latest_decision": latest,
            "reasons": reasons,
            "eligible": not reasons,
        }

    @staticmethod
    def _status(snapshot: Mapping[str, Any]) -> str:
        latest = snapshot.get("latest_decision")
        if isinstance(latest, Mapping):
            return str(latest.get("decision_status") or "PENDING_HUMAN_DECISION")
        return str((snapshot.get("governance") or {}).get("status") or "UNAVAILABLE")

    def _view(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        status = self._status(snapshot)
        status_zh = {
            "PENDING_HUMAN_DECISION": "等待人工治理决定",
            "DEFERRED": "已暂缓预测试验",
            "AUTHORIZED": "已授权进入第 1 次预测试验",
            "ENDED": "当前 Candidate 研究路径已结束",
        }.get(status, "当前不可进入预测治理")
        if status == "AUTHORIZED":
            next_action = "START_PREDICTIVE_TRIAL_1"
            next_action_zh = "已授权进入第 1 次预测试验，等待启动预测试验"
        elif status == "ENDED":
            next_action = "CANDIDATE_RESEARCH_DIRECTION_ENDED"
            next_action_zh = "当前 Candidate 研究路径已结束，不创建预测 Trial"
        else:
            next_action = "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
            next_action_zh = "等待人工决定是否授权进入第 1 次预测试验"
        available = bool(snapshot.get("eligible")) and status in {"PENDING_HUMAN_DECISION", "DEFERRED"}
        latest = snapshot.get("latest_decision") if isinstance(snapshot.get("latest_decision"), Mapping) else {}
        structural = _copy(snapshot.get("structural") or {})
        structural_boundary_zh = " / ".join(
            str(structural.get(key)) if structural.get(key) is not None else "未知"
            for key in ("lower_bound", "upper_bound", "minimum_required")
        )
        return {
            "schema_version": PREVIEW_SCHEMA_VERSION,
            "objective_id": str((snapshot.get("governance") or {}).get("objective_id")),
            "decision_id": (snapshot.get("governance") or {}).get("decision_id"),
            "decision_mode": DECISION_MODE,
            "status": status,
            "status_zh": status_zh,
            "available": available,
            "candidate_id": snapshot.get("candidate_id"),
            "candidate_hash": snapshot.get("candidate_hash"),
            "reconciliation_id": snapshot.get("reconciliation_id"),
            "structural": structural,
            "choices": self.choices(),
            "requires_confirmation": available,
            "scope": "ONE_FROZEN_CANDIDATE_GOVERNANCE_ONLY",
            "reason_zh": "结构预检已通过；确认只记录治理决定，不创建 Trial、不预留预算、不访问绩效。" if available else (snapshot.get("reasons") or ["当前不可进入治理入口"])[0],
            "next_action": next_action,
            "next_action_zh": next_action_zh,
            "human_explanation_zh": f"结构预检已通过，Candidate 身份与 {structural_boundary_zh} 样本边界保持冻结；预测试验仍需人工治理决定。" if status in {"PENDING_HUMAN_DECISION", "DEFERRED"} else next_action_zh,
            "final_test_access": _copy(snapshot.get("final_test_access") or {}),
            "prospective": snapshot.get("prospective"),
            "real_order": snapshot.get("real_order"),
            "predictive_trials_created": int(snapshot.get("trial_count") or 0),
            "performance_access": 0,
            "budget_snapshot": _copy(snapshot.get("budget") or {}),
            "latest_decision": _copy(latest) if latest else None,
            "decision_timestamp": latest.get("decision_timestamp") if latest else None,
            "trial_created": False,
            "budget_reserved": 0,
            "budget_used_delta": 0,
        }

    def readiness(self, objective_id: str) -> dict[str, Any]:
        objective_id = self._safe_id(objective_id, "objective_id")
        return self._view(self._snapshot(objective_id))

    def _plan(self, snapshot: Mapping[str, Any], decision_type: str) -> dict[str, Any]:
        return {
            "schema_version": PREVIEW_SCHEMA_VERSION,
            "objective_id": str((snapshot.get("governance") or {}).get("objective_id")),
            "objective_hash": snapshot.get("objective_hash"),
            "governance_decision_id": (snapshot.get("governance") or {}).get("decision_id"),
            "decision_mode": DECISION_MODE,
            "decision_type": decision_type,
            "candidate_id": snapshot.get("candidate_id"),
            "candidate_hash": snapshot.get("candidate_hash"),
            "reconciliation_id": snapshot.get("reconciliation_id"),
            "structural": _copy(snapshot.get("structural") or {}),
            "trial_count_before": int(snapshot.get("trial_count") or 0),
            "budget_snapshot": _copy(snapshot.get("budget") or {}),
            "final_test_access": _copy(snapshot.get("final_test_access") or {}),
            "prospective": snapshot.get("prospective"),
            "real_order": snapshot.get("real_order"),
            "candidate_state": "FROZEN",
            "trial_creation": "NOT_CREATED",
            "budget_mutation": "NONE",
            "performance_access": 0,
            "final_test_closed": True,
        }

    def preview(self, objective_id: str, decision_type: str = AUTHORIZE_FIRST_PREDICTIVE_TRIAL) -> dict[str, Any]:
        objective_id = self._safe_id(objective_id, "objective_id")
        decision_type = self.normalize_decision(decision_type)
        if decision_type not in DECISION_TYPES:
            raise PredictiveGovernanceError("PREDICTIVE_GOVERNANCE_CHOICE_INVALID", "预测治理选择不受支持", status_code=400, details={"allowed_choices": sorted(DECISION_TYPES)})
        snapshot = self._snapshot(objective_id)
        if not snapshot["eligible"] or self._status(snapshot) not in {"PENDING_HUMAN_DECISION", "DEFERRED"}:
            raise PredictiveGovernanceError("PREDICTIVE_GOVERNANCE_UNAVAILABLE", "当前不满足结构 PASS 后预测治理入口条件", details={"reasons": snapshot["reasons"]})
        plan = self._plan(snapshot, decision_type)
        preview_hash = stable_hash(plan)
        confirmation_token = stable_hash({"preview_hash": preview_hash, "decision_id": (snapshot["governance"] or {}).get("decision_id"), "decision_type": decision_type})
        return {
            **self._view(snapshot),
            "status": "READY",
            "status_zh": "等待明确确认",
            "decision_type": decision_type,
            "choice": next(item for item in self.choices() if item["choice"] == decision_type),
            "preview": plan,
            "preview_hash": preview_hash,
            "confirmation_token": confirmation_token,
            "generated_at": self._clock(),
        }

    def _ensure_request(self, body: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
        if body.get("confirmed") is not True:
            raise PredictiveGovernanceError("CONFIRMATION_REQUIRED", "提交预测治理决定需要明确确认", status_code=400)
        choice = self.normalize_decision(body.get("decision_type") or body.get("choice") or body.get("governance_action") or AUTHORIZE_FIRST_PREDICTIVE_TRIAL)
        if choice not in DECISION_TYPES:
            raise PredictiveGovernanceError("PREDICTIVE_GOVERNANCE_CHOICE_INVALID", "预测治理选择不受支持", status_code=400)
        request_id = self._safe_id(body.get("authorization_id") or body.get("idempotency_key"), "authorization_id")
        candidate_id = self._safe_id(body.get("candidate_id"), "candidate_id")
        candidate_hash = str(body.get("candidate_hash") or "")
        preview_hash = str(body.get("preview_hash") or "")
        confirmation_token = str(body.get("confirmation_token") or "")
        if not preview_hash or not confirmation_token:
            raise PredictiveGovernanceError("PREDICTIVE_GOVERNANCE_PREVIEW_REQUIRED", "确认前必须先读取当前治理预览", status_code=400)
        return choice, request_id, candidate_id, candidate_hash, preview_hash, confirmation_token

    @staticmethod
    def _existing_for_request(rows: list[dict[str, Any]], request_id: str) -> dict[str, Any] | None:
        return next((row for row in reversed(rows) if str(row.get("authorization_id") or row.get("request_id")) == request_id), None)

    def _append_record(self, path: Path, record: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True, default=str) + "\n")

    def _record(self, snapshot: Mapping[str, Any], choice: str, request_id: str, preview_hash: str, confirmation_token: str) -> dict[str, Any]:
        status = DECISION_STATUSES[choice]
        next_action = DECISION_NEXT_ACTIONS[status]
        timestamp = self._clock()
        identity = {
            "objective_id": (snapshot.get("governance") or {}).get("objective_id"),
            "candidate_id": snapshot.get("candidate_id"),
            "candidate_hash": snapshot.get("candidate_hash"),
            "reconciliation_id": snapshot.get("reconciliation_id"),
            "decision_type": choice,
            "authorization_id": request_id,
            "preview_hash": preview_hash,
        }
        decision_id = stable_hash(identity)
        record = {
            "schema_version": AUTHORIZATION_SCHEMA_VERSION,
            "decision_id": decision_id,
            "governance_decision_id": (snapshot.get("governance") or {}).get("decision_id"),
            "objective_id": (snapshot.get("governance") or {}).get("objective_id"),
            "objective_hash": snapshot.get("objective_hash"),
            "candidate_id": snapshot.get("candidate_id"),
            "candidate_hash": snapshot.get("candidate_hash"),
            "structural_reconciliation_id": snapshot.get("reconciliation_id"),
            "structural_status": "PASS",
            "lower_bound": (snapshot.get("structural") or {}).get("lower_bound"),
            "upper_bound": (snapshot.get("structural") or {}).get("upper_bound"),
            "minimum_required": (snapshot.get("structural") or {}).get("minimum_required"),
            "lower_bound_integrity": (snapshot.get("structural") or {}).get("lower_bound_integrity"),
            "decision_type": choice,
            "decision_status": status,
            "decision_timestamp": timestamp,
            "actor": "LOCAL_HUMAN",
            "source": "research_console",
            "authorization_id": request_id,
            "preview_hash": preview_hash,
            "confirmation_token_hash": stable_hash(confirmation_token),
            "budget_snapshot": _copy(snapshot.get("budget") or {}),
            "budget_reserved": 0,
            "budget_used_delta": 0,
            "predictive_trials_before": int(snapshot.get("trial_count") or 0),
            "predictive_trials_after": int(snapshot.get("trial_count") or 0),
            "trial_created": False,
            "performance_access": 0,
            "final_test_access": _copy(snapshot.get("final_test_access") or {}),
            "prospective": snapshot.get("prospective"),
            "real_order": snapshot.get("real_order"),
            "final_test_closed": True,
            "next_action": next_action,
            "candidate_semantics_unchanged": True,
        }
        record["decision_hash"] = stable_hash(record)
        return record

    @staticmethod
    def _idempotent_response(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": AUTHORIZATION_SCHEMA_VERSION,
            "status": "RECORDED",
            "idempotent": True,
            "decision": dict(record),
            "next_action": record.get("next_action"),
            "message_zh": "该治理决定已经记录，未重复创建 Trial 或修改预算。",
        }

    def confirm(self, objective_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        objective_id = self._safe_id(objective_id, "objective_id")
        choice, request_id, candidate_id, candidate_hash, preview_hash, confirmation_token = self._ensure_request(body)
        snapshot = self._snapshot(objective_id)
        rows = self._ledger(objective_id)
        existing = self._existing_for_request(rows, request_id)
        if existing is not None:
            if (
                str(existing.get("decision_type")) != choice
                or str(existing.get("candidate_id")) != candidate_id
                or (candidate_hash and str(existing.get("candidate_hash")) != candidate_hash)
                or str(existing.get("preview_hash")) != preview_hash
            ):
                raise PredictiveGovernanceError("PREDICTIVE_GOVERNANCE_IDEMPOTENCY_CONFLICT", "相同授权编号对应了不同的 Candidate 或治理预览", details={"authorization_id": request_id})
            if stable_hash(confirmation_token) != str(existing.get("confirmation_token_hash")):
                raise PredictiveGovernanceError("STALE_PREDICTIVE_GOVERNANCE_PREVIEW", "治理预览已失效，请刷新后重新确认", details={"authorization_id": request_id})
            return self._idempotent_response(existing)
        if str(snapshot.get("candidate_id")) != candidate_id or (candidate_hash and str(snapshot.get("candidate_hash")) != candidate_hash):
            raise PredictiveGovernanceError("PREDICTIVE_GOVERNANCE_CANDIDATE_MISMATCH", "页面 Candidate 与当前 canonical Candidate 不一致，请刷新后重试", details={"candidate_id": snapshot.get("candidate_id")})
        latest = snapshot.get("latest_decision")
        if isinstance(latest, Mapping) and str(latest.get("decision_status")) in {"AUTHORIZED", "ENDED"}:
            raise PredictiveGovernanceError("PREDICTIVE_GOVERNANCE_DECISION_CONFLICT", "当前 Candidate 已有不可重复覆盖的治理决定")
        if not snapshot["eligible"] or self._status(snapshot) not in {"PENDING_HUMAN_DECISION", "DEFERRED"}:
            raise PredictiveGovernanceError("PREDICTIVE_GOVERNANCE_UNAVAILABLE", "当前不满足结构 PASS 后预测治理入口条件", details={"reasons": snapshot["reasons"]})
        plan = self._plan(snapshot, choice)
        expected_hash = stable_hash(plan)
        expected_token = stable_hash({"preview_hash": expected_hash, "decision_id": (snapshot["governance"] or {}).get("decision_id"), "decision_type": choice})
        if preview_hash != expected_hash or confirmation_token != expected_token:
            raise PredictiveGovernanceError("STALE_PREDICTIVE_GOVERNANCE_PREVIEW", "治理预览已失效，请刷新后重新确认", details={"expected_preview_hash": expected_hash})

        store: DaemonCheckpointStoreV1 = snapshot["store"]
        lock = DaemonInstanceLockV1(store.lock_path, objective_id)
        try:
            lock.acquire(run_id=f"PREDICTIVE_GOVERNANCE_{request_id}")
        except DaemonAlreadyRunningError as exc:
            raise PredictiveGovernanceError("PREDICTIVE_GOVERNANCE_LOCKED", "当前 objective 正在运行，治理决定暂不能写入", status_code=409) from exc
        try:
            current = self._snapshot(objective_id)
            current_rows = self._ledger(objective_id)
            repeated = self._existing_for_request(current_rows, request_id)
            if repeated is not None:
                return self._idempotent_response(repeated)
            if not current["eligible"] or self._status(current) not in {"PENDING_HUMAN_DECISION", "DEFERRED"}:
                raise PredictiveGovernanceError("PREDICTIVE_GOVERNANCE_STALE_STATE", "canonical 状态在确认过程中发生变化，请重新读取治理预览")
            current_plan = self._plan(current, choice)
            current_hash = stable_hash(current_plan)
            current_token = stable_hash({"preview_hash": current_hash, "decision_id": (current["governance"] or {}).get("decision_id"), "decision_type": choice})
            if preview_hash != current_hash or confirmation_token != current_token:
                raise PredictiveGovernanceError("STALE_PREDICTIVE_GOVERNANCE_PREVIEW", "治理预览已失效，请刷新后重新确认", details={"expected_preview_hash": current_hash})
            record = self._record(current, choice, request_id, preview_hash, confirmation_token)
            self._append_record(self._ledger_path(objective_id), record)
            next_action = str(record["next_action"])
            governance_ref = {
                "schema_version": AUTHORIZATION_SCHEMA_VERSION,
                "status": record["decision_status"],
                "decision_id": record["decision_id"],
                "decision_type": choice,
                "candidate_id": record["candidate_id"],
                "candidate_hash": record["candidate_hash"],
                "structural_reconciliation_id": record["structural_reconciliation_id"],
                "trial_creation_authorized": choice == AUTHORIZE_FIRST_PREDICTIVE_TRIAL,
                "trial_created": False,
                "budget_reserved": 0,
                "budget_used_delta": 0,
                "performance_access": 0,
                "final_test_closed": True,
                "next_action": next_action,
                "decision_ref": str(self._ledger_path(objective_id).relative_to(self.root)).replace("\\", "/"),
            }
            refs = {
                **dict(current["checkpoint"].canonical_refs or {}),
                "predictive_governance": governance_ref,
                "predictive_authorization": governance_ref,
            }
            updated = current["checkpoint"].update(
                current_candidate=None,
                current_trial=None,
                required_action=next_action,
                canonical_refs=refs,
                retry_safe=True,
                last_error=None,
                error_reason_code=None,
            )
            store.save(updated)
            store.append_event(
                "PREDICTIVE_GOVERNANCE_DECISION_RECORDED",
                {
                    "objective_id": objective_id,
                    "candidate": {"candidate_id": record["candidate_id"], "candidate_hash": record["candidate_hash"]},
                    "decision_id": record["decision_id"],
                    "decision_type": choice,
                    "decision_status": record["decision_status"],
                    "next_action": next_action,
                    "trial_created": False,
                    "budget_reserved": 0,
                    "performance_access": 0,
                    "final_test_access": record["final_test_access"],
                },
                event_id=stable_hash({"event_type": "PREDICTIVE_GOVERNANCE_DECISION_RECORDED", "decision_id": record["decision_id"]}),
            )
            return {
                "schema_version": AUTHORIZATION_SCHEMA_VERSION,
                "status": "RECORDED",
                "idempotent": False,
                "decision": record,
                "next_action": next_action,
                "message_zh": {
                    "AUTHORIZED": "已授权进入第 1 次预测试验；尚未启动预测试验。",
                    "DEFERRED": "已暂缓预测试验；结构 PASS、Trial 数和预算保持不变。",
                    "ENDED": "已结束当前 Candidate 研究路径；未创建预测 Trial，结构 PASS 历史保持不变。",
                }[record["decision_status"]],
                "safety_boundary": {
                    "trial_created": False,
                    "budget_reserved": 0,
                    "budget_used_delta": 0,
                    "performance_access": 0,
                    "final_test_access": record["final_test_access"],
                    "prospective": record["prospective"],
                    "real_order": record["real_order"],
                },
            }
        finally:
            lock.release()


def run_authorized_predictive_validation(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Compatibility entry that now records governance only.

    The old function name used to call the predictive executor. Keeping the
    symbol avoids an import break while making this path execution-free.
    """

    root = kwargs.pop("root", args[0] if args else None)
    objective_id = kwargs.pop("objective_id", "")
    candidate_id = kwargs.pop("candidate_id", "")
    authorization_id = kwargs.pop("authorization_id", "")
    if kwargs.pop("runtime", None) is not None:
        raise PredictiveGovernanceError("PREDICTIVE_EXECUTOR_NOT_ALLOWED", "结构 PASS 授权入口不接受预测执行器")
    service = PredictiveGovernanceServiceV1(root)
    preview = service.preview(objective_id, AUTHORIZE_FIRST_PREDICTIVE_TRIAL)
    return service.confirm(objective_id, {
        "confirmed": True,
        "decision_type": AUTHORIZE_FIRST_PREDICTIVE_TRIAL,
        "candidate_id": candidate_id,
        "candidate_hash": preview.get("candidate_hash"),
        "authorization_id": authorization_id,
        "preview_hash": preview["preview_hash"],
        "confirmation_token": preview["confirmation_token"],
    })


__all__ = [
    "AUTHORIZE_FIRST_PREDICTIVE_TRIAL",
    "DEFER_PREDICTIVE_TRIAL",
    "END_CANDIDATE_RESEARCH_DIRECTION",
    "PredictiveGovernanceError",
    "PredictiveGovernanceServiceV1",
    "run_authorized_predictive_validation",
]
