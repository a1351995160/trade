"""Protected reauthorization and start path for one new predictive Trial.

The original ``START_PREDICTIVE_TRIAL_1`` and ``RESUME_PREDICTIVE_TRIAL_1``
paths intentionally remain single-Trial paths.  This module is the separate
human-decision boundary used only after an accessed Trial has reached the
engineering-invalidated terminal state and its failure review is complete.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import threading
import uuid
from typing import Any, Callable, Mapping

from ..research_daemon_state import DaemonAlreadyRunningError, ResearchDaemonState
from .budget import BudgetExhaustedError, SearchBudgetRegistryV1
from .common import stable_hash
from .predictive_authorization import DECISION_MODE, PredictiveGovernanceError, PredictiveGovernanceServiceV1
from .predictive_trial_start import (
    AUTHORIZE_FIRST_PREDICTIVE_TRIAL,
    MULTIPLE_TESTING_REGISTRATION_SCHEMA,
    MULTIPLE_TESTING_REGISTRATIONS_SUFFIX,
    START_INTENT_SCHEMA,
    START_RECEIPT_SCHEMA,
    IN_FLIGHT_STAGES,
    TERMINAL_TRIAL_STATUSES,
    MultipleTestingRegistrationLedgerV1,
    PredictiveTrialStartError,
    PredictiveTrialStartServiceV1,
    _StartSnapshot,
    _copy,
    _int,
)
from .predictive_executor import CanonicalPredictiveExecutorV1
from .trial_adapter import ResearchFactoryTrialLedgerFacadeV1


AUTHORIZE_NEW_PREDICTIVE_TRIAL = "AUTHORIZE_NEW_PREDICTIVE_TRIAL"
START_PREDICTIVE_TRIAL_2 = "START_PREDICTIVE_TRIAL_2"
AUTHORIZATION_PREVIEW_SCHEMA = "predictive-new-trial-authorization-preview-v1"
START_PREVIEW_SCHEMA = "predictive-new-trial-start-preview-v1"
ACTION_AUTHORIZATION_ZH = "重新授权新建预测 Trial"
ACTION_START_ZH = "启动第 2 次预测试验"
FAILURE_REVIEW_REQUIRED = "PREDICTIVE_TRIAL_FAILURE_REVIEW"
SEMANTIC_REPAIR_GLOB = "*SEMANTIC_REPAIR*.json"


def _safe_hash_record(payload: Mapping[str, Any]) -> str:
    return stable_hash({key: value for key, value in payload.items() if key not in {"decision_hash", "intent_hash"}})


class PredictiveTrialReauthorizationServiceV1(PredictiveTrialStartServiceV1):
    """Record one new-Trial authorization and wire it to the canonical runner."""

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], str] | None = None,
        runner: Any | None = None,
        auto_run: bool = True,
    ):
        super().__init__(root, clock=clock, runner=runner, auto_run=auto_run)
        self._governance = PredictiveGovernanceServiceV1(self.root, clock=self._clock)
        self._reauthorization_lock = threading.Lock()

    def _repair_report(self, objective_id: str, candidate_id: str, candidate_hash: str) -> tuple[Path | None, Mapping[str, Any] | None]:
        matches: list[tuple[Path, Mapping[str, Any]]] = []
        for path in sorted((self.root / "reports").glob(SEMANTIC_REPAIR_GLOB)):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, Mapping):
                continue
            scope = payload.get("scope") if isinstance(payload.get("scope"), Mapping) else {}
            repair = payload.get("repair") if isinstance(payload.get("repair"), Mapping) else {}
            if (
                str(payload.get("status")) == "PASS"
                and str(scope.get("objective_id")) == objective_id
                and str(scope.get("candidate_id")) == candidate_id
                and str(scope.get("candidate_hash")) == candidate_hash
                and str(repair.get("status")) == "PASS"
                and repair.get("candidate_contract_unchanged") is True
                and repair.get("factor_cache_unchanged") is True
            ):
                matches.append((path, payload))
        if len(matches) != 1:
            return None, None
        return matches[0]

    @staticmethod
    def _trial_number(trial_id: Any) -> int:
        text = str(trial_id or "")
        suffix = text.rsplit("_T", 1)[-1] if "_T" in text else ""
        return int(suffix) if suffix.isdigit() else 0

    @staticmethod
    def _reservation_id(objective_id: str, batch_id: str, family_id: str, candidate_id: str, trial_number: int) -> str:
        identity = {
            "objective_id": objective_id,
            "batch_id": batch_id,
            "family_id": family_id,
            "candidate_id": candidate_id,
            "trial_number": int(trial_number),
        }
        return f"TRIAL-{stable_hash(identity)[:20]}"

    def _registration_path(self, family_path: Path) -> Path:
        return family_path.with_name(family_path.stem + MULTIPLE_TESTING_REGISTRATIONS_SUFFIX)

    def _read_family_and_registrations(self, family_path: Path, registration_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        try:
            family = json.loads(family_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PredictiveTrialStartError("MULTIPLE_TESTING_FAMILY_UNREADABLE", "Multiple Testing Family 定义暂时不可读", status_code=503) from exc
        if not isinstance(family, Mapping):
            raise PredictiveTrialStartError("MULTIPLE_TESTING_FAMILY_INVALID", "Multiple Testing Family 定义格式不受支持", status_code=503)
        registrations: list[dict[str, Any]] = []
        if registration_path.is_file():
            try:
                payload = json.loads(registration_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise PredictiveTrialStartError("MULTIPLE_TESTING_LEDGER_INVALID", "Multiple Testing 登记账本无法安全读取", status_code=503) from exc
            if not isinstance(payload, Mapping) or payload.get("schema_version") != MULTIPLE_TESTING_REGISTRATION_SCHEMA or not isinstance(payload.get("registrations"), list):
                raise PredictiveTrialStartError("MULTIPLE_TESTING_LEDGER_INVALID", "Multiple Testing 登记账本格式不受支持", status_code=503)
            registrations = [dict(item) for item in payload["registrations"] if isinstance(item, Mapping)]
        return dict(family), registrations

    def _canonical_context(self, objective_id: str) -> dict[str, Any]:
        """Read the shared structural, contract, budget and failure-review facts."""
        try:
            raw = self._governance._snapshot(objective_id)
        except PredictiveGovernanceError as exc:
            raise PredictiveTrialStartError(exc.code, exc.message_zh, status_code=exc.status_code, details=exc.details) from exc
        checkpoint = raw["checkpoint"]
        candidate_id = str(raw.get("candidate_id") or "")
        candidate_hash = str(raw.get("candidate_hash") or "")
        contract, contract_ref, contract_reasons = self._load_contract(checkpoint, candidate_id, candidate_hash)
        policy = None
        policy_hash = ""
        policy_path: Path | None = None
        policy_reasons: list[str] = []
        if contract is not None:
            try:
                policy, policy_hash, policy_path = CanonicalPredictiveExecutorV1(self.root, objective_id)._load_policy(contract)
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                policy_reasons.append(f"Validation Policy 无法完成 canonical pin 校验：{type(exc).__name__}")

        budget_path = self._resolve_budget_path(objective_id, checkpoint)
        budget: dict[str, Any] = dict(raw.get("budget") or {})
        batch_id = ""
        family_id = str(raw.get("objective", {}).get("multiple_testing_family_id") or (contract.mechanism if contract else ""))
        decision_family_id = ""
        budget_reasons: list[str] = []
        if budget_path is None:
            budget_reasons.append("canonical SearchBudgetRegistry 不存在")
        else:
            try:
                registry = SearchBudgetRegistryV1(objective_id, budget_path)
                budget_snapshot = registry.snapshot()
                bucket = next((item for item in budget_snapshot.get("buckets", ()) if item.get("kind") == "objective" and item.get("key") == objective_id), None)
                if bucket is None:
                    budget_reasons.append("canonical objective 预算 bucket 不存在")
                else:
                    budget = {
                        "objective_id": objective_id,
                        "total": _int(bucket.get("limit")),
                        "used": _int(bucket.get("used")),
                        "reserved": _int(bucket.get("reserved")),
                        "remaining": _int(bucket.get("remaining")),
                        "registry_path": budget_path.relative_to(self.root).as_posix(),
                        "registry_head_hash": registry.head_hash,
                    }
                executor = CanonicalPredictiveExecutorV1(self.root, objective_id)
                batch_id = executor._budget_binding(budget_snapshot, "batch", str((checkpoint.last_completed_candidate or {}).get("batch_id") or (contract.source_provenance.get("batch_id") if contract else "")))
                family_id = executor._budget_binding(budget_snapshot, "family", family_id)
                if policy is not None:
                    decision_family_id = policy.family_id(objective_id, batch_id)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                budget_reasons.append(f"canonical SearchBudgetRegistry 无法安全绑定：{type(exc).__name__}")

        family_path = self._family_path(objective_id, family_id) if family_id else None
        registration_path = self._registration_path(family_path) if family_path is not None else None
        family_reasons: list[str] = []
        family: dict[str, Any] = {}
        registrations: list[dict[str, Any]] = []
        if family_path is None or not family_path.is_file():
            family_reasons.append("当前 Candidate 没有可绑定的 Multiple Testing Family")
        else:
            try:
                family, registrations = self._read_family_and_registrations(family_path, registration_path or family_path.with_name(".missing"))
                if str(family.get("objective_id")) != objective_id or str(family.get("family_id")) != family_id:
                    family_reasons.append("Multiple Testing Family 身份与当前 objective 不一致")
                if family.get("immutable_after_confirmation") is not True:
                    family_reasons.append("Multiple Testing Family 没有 immutable 约束")
            except PredictiveTrialStartError as exc:
                family_reasons.append(exc.message_zh)

        trial_records = self._trial_records(objective_id)
        prior = [
            item for item in trial_records.values()
            if str(item.get("candidate_id")) == candidate_id
            and str(item.get("candidate_hash")) == candidate_hash
            and self._trial_number(item.get("trial_id")) == 1
        ]
        prior_record = prior[0] if len(prior) == 1 else None
        repair_path, repair_report = self._repair_report(objective_id, candidate_id, candidate_hash)
        reasons: list[str] = []
        governance = raw.get("governance") if isinstance(raw.get("governance"), Mapping) else {}
        structural_result = raw.get("structural_result") if isinstance(raw.get("structural_result"), Mapping) else {}
        structural = raw.get("structural") if isinstance(raw.get("structural"), Mapping) else {}
        reconciliation = dict(raw.get("reconciliation") or {})
        if str(governance.get("decision_mode") or "") != DECISION_MODE:
            reasons.append("当前治理状态不是结构 PASS 后的预测授权模式")
        if str(governance.get("status") or "") not in {"PENDING_HUMAN_DECISION", "DEFERRED"}:
            reasons.append("当前结构治理状态不是可供新 Trial 重新授权的状态")
        if str(structural_result.get("status") or "").upper() != "PASS" or str(structural.get("status") or "").upper() != "PASS":
            reasons.append("结构预检不是 canonical PASS")
        if str(raw.get("reconciliation_id") or "") == "" or str(reconciliation.get("status") or "PASS").upper() not in {"PASS", ""}:
            reasons.append("结构 reconciliation 不完整")
        details = structural_result.get("details") if isinstance(structural_result.get("details"), Mapping) else {}
        integrity = details.get("lower_bound_integrity") if isinstance(details.get("lower_bound_integrity"), Mapping) else {}
        if str(integrity.get("status") or structural.get("lower_bound_integrity") or "").upper() != "PASS":
            reasons.append("lower-bound integrity 未通过")
        if not candidate_id or not candidate_hash:
            reasons.append("当前 Candidate 身份缺失")
        identity = details
        for key in ("v2_result", "v1_result"):
            nested = details.get(key) if isinstance(details.get(key), Mapping) else {}
            if nested.get("candidate_id"):
                identity = nested
                break
        if str(identity.get("candidate_id") or candidate_id) != candidate_id or str(identity.get("candidate_hash") or candidate_hash) != candidate_hash:
            reasons.append("结构证据与当前 Candidate 身份不一致")
        last = dict(checkpoint.last_completed_candidate or {})
        if str(last.get("candidate_id") or "") != candidate_id or str(last.get("candidate_hash") or "") != candidate_hash:
            reasons.append("checkpoint 中没有当前冻结 Candidate")
        reasons.extend(contract_reasons)
        reasons.extend(policy_reasons)
        if prior_record is None:
            reasons.append("没有恰好一个可作为失败审查来源的 T001")
        else:
            lineage = prior_record.get("lineage") if isinstance(prior_record.get("lineage"), Mapping) else {}
            if (
                str(prior_record.get("status") or "").upper() not in TERMINAL_TRIAL_STATUSES
                or str(prior_record.get("classification") or "") != "ENGINEERING_INVALIDATED"
                or not bool(prior_record.get("performance_accessed"))
                or bool(prior_record.get("performance_complete"))
                or bool(prior_record.get("final_adjudicated"))
                or bool(prior_record.get("registry_committed"))
                or lineage.get("engineering_interrupted") is not True
            ):
                reasons.append("T001 不是访问绩效后的工程失效终态")
        first_authorizations = [
            item for item in self._authorization_rows(objective_id)
            if str(item.get("decision_type")) == AUTHORIZE_FIRST_PREDICTIVE_TRIAL
            and str(item.get("decision_status")) == "AUTHORIZED"
            and str(item.get("candidate_id")) == candidate_id
            and str(item.get("candidate_hash")) == candidate_hash
        ]
        if not first_authorizations:
            reasons.append("缺少与当前 Candidate 匹配的原始第 1 次预测授权")
        if repair_path is None or repair_report is None:
            reasons.append("没有找到与当前 Candidate 精确匹配的 PASS 失败修复报告")
        if _int(budget.get("used")) != 1 or _int(budget.get("reserved")) != 0 or _int(budget.get("remaining")) < 1:
            reasons.append("新 Trial 预算要求当前为 used=1、reserved=0 且仍有可用槽位")
        if family and _int(family.get("hypothesis_slots")) < len(registrations) + 1:
            reasons.append("Multiple Testing Family 没有新的假设槽位")
        final_test = {key: _int((governance.get("final_test_access") or {}).get(key)) for key in ("analytical", "decision", "physical")}
        if final_test != {"analytical": 0, "decision": 0, "physical": 0}:
            reasons.append("Final Test 已不是关闭状态")
        prospective = str(governance.get("prospective") or raw.get("objective", {}).get("risk_constraints", {}).get("prospective_access") or "UNKNOWN")
        real_order = str(governance.get("real_order") or raw.get("objective", {}).get("risk_constraints", {}).get("real_order_execution") or "UNKNOWN")
        if prospective != "DISABLED":
            reasons.append("Prospective 不是 DISABLED")
        if real_order != "DISABLED":
            reasons.append("Real Order 不是 DISABLED")
        if str(checkpoint.ai_auto_invocation or "DISABLED") != "DISABLED":
            reasons.append("AI 自动调用不是 DISABLED")
        reasons.extend(budget_reasons)
        reasons.extend(family_reasons)
        return {
            "objective": raw["objective"],
            "governance": governance,
            "checkpoint": checkpoint,
            "store": raw["store"],
            "candidate_id": candidate_id,
            "candidate_hash": candidate_hash,
            "contract": contract,
            "contract_ref": contract_ref,
            "policy": policy,
            "policy_hash": policy_hash,
            "policy_path": policy_path,
            "reconciliation": reconciliation | {"reconciliation_id": raw.get("reconciliation_id")},
            "structural": structural,
            "budget_path": budget_path,
            "budget": budget,
            "batch_id": batch_id,
            "family_id": family_id,
            "decision_family_id": decision_family_id,
            "family_path": family_path,
            "family": family,
            "registration_path": registration_path,
            "registrations": registrations,
            "trial_records": trial_records,
            "prior_record": prior_record,
            "repair_path": repair_path,
            "repair_report": repair_report,
            "final_test_access": final_test,
            "prospective": prospective,
            "real_order": real_order,
            "reasons": list(dict.fromkeys(reasons)),
        }

    def _auth_reasons(self, context: Mapping[str, Any]) -> list[str]:
        checkpoint = context["checkpoint"]
        reasons = list(context["reasons"])
        if checkpoint.current_state != ResearchDaemonState.ENGINEERING_BLOCKED.value:
            reasons.append(f"daemon 当前状态不是 ENGINEERING_BLOCKED：{checkpoint.current_state}")
        if str(checkpoint.required_action or "") != FAILURE_REVIEW_REQUIRED:
            reasons.append(f"当前 required_action 不是 {FAILURE_REVIEW_REQUIRED}")
        current_trial = checkpoint.current_trial or {}
        if current_trial and str(current_trial.get("trial_id")) != str((context["prior_record"] or {}).get("trial_id")):
            reasons.append("checkpoint 当前 Trial 不是失败审查中的 T001")
        if self._governance._lock_active(context["store"]):
            reasons.append("当前 objective 存在活动运行锁")
        if any(self._trial_number(item.get("trial_id")) >= 2 for item in context["trial_records"].values()):
            reasons.append("当前新 Trial 授权入口已经存在后续 Trial")
        return list(dict.fromkeys(reasons))

    def _latest_new_authorization(self, objective_id: str) -> dict[str, Any] | None:
        rows = self._authorization_rows(objective_id)
        for row in reversed(rows):
            if str(row.get("decision_type")) == AUTHORIZE_NEW_PREDICTIVE_TRIAL:
                return row
        return None

    def _authorization_plan(self, context: Mapping[str, Any]) -> dict[str, Any]:
        prior = context["prior_record"] or {}
        repair = context["repair_report"] or {}
        return {
            "action": AUTHORIZE_NEW_PREDICTIVE_TRIAL,
            "decision_type": AUTHORIZE_NEW_PREDICTIVE_TRIAL,
            "next_action": START_PREDICTIVE_TRIAL_2,
            "objective_id": context["objective"].get("objective_id"),
            "objective_hash": context["objective"].get("objective_identity_hash") or context["objective"].get("objective_hash"),
            "candidate_id": context["candidate_id"],
            "candidate_hash": context["candidate_hash"],
            "candidate_state": "FROZEN",
            "structural_reconciliation_id": context["reconciliation"].get("reconciliation_id"),
            "structural": _copy(context["structural"]),
            "prior_trial_id": prior.get("trial_id"),
            "prior_trial_status": prior.get("status"),
            "prior_trial_classification": prior.get("classification"),
            "failure_review_ref": context["repair_path"].relative_to(self.root).as_posix() if context["repair_path"] else None,
            "failure_review_hash": stable_hash(repair) if repair else None,
            "candidate_contract_ref": context["contract_ref"],
            "candidate_contract_content_hash": context["contract"].content_hash if context["contract"] else None,
            "budget_snapshot": _copy(context["budget"]),
            "budget_reserved": 0,
            "budget_used_delta": 0,
            "multiple_testing_family_id": context["family_id"],
            "multiple_testing_registered_before_new_trial": len(context["registrations"]),
            "multiple_testing_slots": _int(context["family"].get("hypothesis_slots")),
            "trial_number": 2,
            "trial_created": False,
            "performance_access": 0,
            "final_test_access": _copy(context["final_test_access"]),
            "prospective": context["prospective"],
            "real_order": context["real_order"],
            "confirmation_required": True,
        }

    def preview_authorization(self, objective_id: str) -> dict[str, Any]:
        objective_id = self._safe_id(objective_id, "objective_id")
        context = self._canonical_context(objective_id)
        if self._latest_new_authorization(objective_id) is not None:
            raise PredictiveTrialStartError("PREDICTIVE_NEW_AUTHORIZATION_ALREADY_EXISTS", "当前 objective 已存在新 Trial 授权，请继续读取新 Trial 启动预览")
        reasons = self._auth_reasons(context)
        if reasons:
            raise PredictiveTrialStartError("PREDICTIVE_NEW_AUTHORIZATION_UNAVAILABLE", "当前不满足重新授权新 Trial 的全部条件", details={"reasons": reasons})
        plan = self._authorization_plan(context)
        preview_hash = stable_hash(plan)
        token = stable_hash({"action": AUTHORIZE_NEW_PREDICTIVE_TRIAL, "preview_hash": preview_hash, "candidate_hash": context["candidate_hash"], "prior_trial_id": context["prior_record"]["trial_id"]})
        return {
            "schema_version": AUTHORIZATION_PREVIEW_SCHEMA,
            "status": "READY",
            "status_zh": "等待明确确认重新授权新 Trial",
            "available": True,
            "action": AUTHORIZE_NEW_PREDICTIVE_TRIAL,
            "action_zh": ACTION_AUTHORIZATION_ZH,
            "objective_id": objective_id,
            "candidate_id": context["candidate_id"],
            "candidate_hash": context["candidate_hash"],
            "prior_trial_id": context["prior_record"]["trial_id"],
            "trial_number": 2,
            "preview": plan,
            "preview_hash": preview_hash,
            "confirmation_token": token,
            "confirmation_required": True,
            "reason_zh": "T001 的失败审查已完成且仅确认工程失效；确认后只记录新 Trial 授权，不创建 Trial、不预留预算、不访问绩效。",
            "reasons": [],
            "generated_at": self._clock(),
        }

    def _new_authorization_record(self, context: Mapping[str, Any], authorization_id: str, preview_hash: str, token: str) -> dict[str, Any]:
        prior = context["prior_record"] or {}
        repair = context["repair_report"] or {}
        timestamp = self._clock()
        record = {
            "schema_version": "predictive-governance-decision-v1",
            "decision_id": stable_hash({"objective_id": context["objective"].get("objective_id"), "candidate_id": context["candidate_id"], "candidate_hash": context["candidate_hash"], "decision_type": AUTHORIZE_NEW_PREDICTIVE_TRIAL, "authorization_id": authorization_id, "preview_hash": preview_hash}),
            "governance_decision_id": context["governance"].get("decision_id"),
            "objective_id": context["objective"].get("objective_id"),
            "objective_hash": context["objective"].get("objective_identity_hash") or context["objective"].get("objective_hash"),
            "candidate_id": context["candidate_id"],
            "candidate_hash": context["candidate_hash"],
            "structural_reconciliation_id": context["reconciliation"].get("reconciliation_id"),
            "structural_status": "PASS",
            "lower_bound": context["structural"].get("lower_bound"),
            "upper_bound": context["structural"].get("upper_bound"),
            "minimum_required": context["structural"].get("minimum_required"),
            "lower_bound_integrity": context["structural"].get("lower_bound_integrity"),
            "decision_type": AUTHORIZE_NEW_PREDICTIVE_TRIAL,
            "decision_status": "AUTHORIZED",
            "decision_timestamp": timestamp,
            "actor": "LOCAL_HUMAN",
            "source": "research_console_new_trial_reauthorization",
            "authorization_id": authorization_id,
            "preview_hash": preview_hash,
            "confirmation_token_hash": stable_hash(token),
            "budget_snapshot": _copy(context["budget"]),
            "budget_reserved": 0,
            "budget_used_delta": 0,
            "predictive_trials_before": len(context["trial_records"]),
            "predictive_trials_after": len(context["trial_records"]),
            "trial_created": False,
            "new_trial_authorized": True,
            "trial_number": 2,
            "prior_trial_id": prior.get("trial_id"),
            "prior_trial_status": prior.get("status"),
            "prior_trial_classification": prior.get("classification"),
            "failure_review_ref": context["repair_path"].relative_to(self.root).as_posix() if context["repair_path"] else None,
            "failure_review_hash": stable_hash(repair) if repair else None,
            "candidate_contract_ref": context["contract_ref"],
            "candidate_contract_content_hash": context["contract"].content_hash if context["contract"] else None,
            "performance_access": 0,
            "final_test_access": _copy(context["final_test_access"]),
            "prospective": context["prospective"],
            "real_order": context["real_order"],
            "final_test_closed": True,
            "next_action": START_PREDICTIVE_TRIAL_2,
            "candidate_semantics_unchanged": True,
        }
        record["decision_hash"] = _safe_hash_record(record)
        return record

    def confirm_authorization(self, objective_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        objective_id = self._safe_id(objective_id, "objective_id")
        if body.get("confirmed") is not True:
            raise PredictiveTrialStartError("CONFIRMATION_REQUIRED", "重新授权新 Trial 需要明确确认", status_code=400)
        if str(body.get("decision_type") or body.get("action") or "").strip().upper() != AUTHORIZE_NEW_PREDICTIVE_TRIAL:
            raise PredictiveTrialStartError("CANONICAL_NEW_AUTHORIZATION_REQUIRED", f"请求必须明确使用 {AUTHORIZE_NEW_PREDICTIVE_TRIAL}", status_code=400)
        authorization_id = self._safe_id(body.get("authorization_id") or body.get("idempotency_key"), "authorization_id")
        candidate_id = self._safe_id(body.get("candidate_id"), "candidate_id")
        candidate_hash = str(body.get("candidate_hash") or "")
        preview_hash = str(body.get("preview_hash") or "")
        token = str(body.get("confirmation_token") or "")
        if not candidate_hash or not preview_hash or not token:
            raise PredictiveTrialStartError("PREDICTIVE_NEW_AUTHORIZATION_PREVIEW_REQUIRED", "确认前必须先读取新 Trial 授权预览", status_code=400)
        context = self._canonical_context(objective_id)
        if context["candidate_id"] != candidate_id or context["candidate_hash"] != candidate_hash:
            raise PredictiveTrialStartError("PREDICTIVE_NEW_AUTHORIZATION_CANDIDATE_MISMATCH", "确认页中的 Candidate 身份已过期，请刷新预览后重试")
        ledger_path = self._governance._ledger_path(objective_id)
        rows = self._authorization_rows(objective_id)
        existing = next((row for row in reversed(rows) if str(row.get("authorization_id")) == authorization_id), None)
        if existing is not None:
            if str(existing.get("decision_type")) != AUTHORIZE_NEW_PREDICTIVE_TRIAL or str(existing.get("candidate_id")) != candidate_id or str(existing.get("candidate_hash")) != candidate_hash or str(existing.get("preview_hash")) != preview_hash or str(existing.get("confirmation_token_hash")) != stable_hash(token):
                raise PredictiveTrialStartError("PREDICTIVE_NEW_AUTHORIZATION_IDEMPOTENCY_CONFLICT", "相同授权编号对应了不同的新 Trial 预览")
            return {"schema_version": "predictive-governance-decision-v1", "status": "RECORDED", "idempotent": True, "decision": existing, "next_action": START_PREDICTIVE_TRIAL_2, "message_zh": "该新 Trial 授权已经记录，未重复创建 Trial 或修改预算。"}
        if self._latest_new_authorization(objective_id) is not None:
            raise PredictiveTrialStartError("PREDICTIVE_NEW_AUTHORIZATION_ALREADY_EXISTS", "当前 objective 已存在另一个新 Trial 授权")
        reasons = self._auth_reasons(context)
        if reasons:
            raise PredictiveTrialStartError("PREDICTIVE_NEW_AUTHORIZATION_STALE_STATE", "canonical 状态已变化，请重新读取新 Trial 授权预览", details={"reasons": reasons})
        plan = self._authorization_plan(context)
        expected_hash = stable_hash(plan)
        expected_token = stable_hash({"action": AUTHORIZE_NEW_PREDICTIVE_TRIAL, "preview_hash": expected_hash, "candidate_hash": candidate_hash, "prior_trial_id": context["prior_record"]["trial_id"]})
        if preview_hash != expected_hash or token != expected_token:
            raise PredictiveTrialStartError("STALE_PREDICTIVE_NEW_AUTHORIZATION_PREVIEW", "新 Trial 授权预览已失效，请刷新后重新确认")
        lock = self._governance._store(objective_id)
        instance_lock = self._new_lock(lock, objective_id, f"PREDICTIVE_NEW_AUTHORIZATION_{authorization_id}")
        try:
            instance_lock.acquire(run_id=f"PREDICTIVE_NEW_AUTHORIZATION_{authorization_id}")
        except DaemonAlreadyRunningError as exc:
            raise PredictiveTrialStartError("PREDICTIVE_NEW_AUTHORIZATION_LOCKED", "当前 objective 正在运行，重新授权暂不能写入") from exc
        try:
            current = self._canonical_context(objective_id)
            repeated = next((row for row in self._authorization_rows(objective_id) if str(row.get("authorization_id")) == authorization_id), None)
            if repeated is not None:
                return {"schema_version": "predictive-governance-decision-v1", "status": "RECORDED", "idempotent": True, "decision": repeated, "next_action": START_PREDICTIVE_TRIAL_2}
            current_reasons = self._auth_reasons(current)
            if current_reasons:
                raise PredictiveTrialStartError("PREDICTIVE_NEW_AUTHORIZATION_STALE_STATE", "canonical 状态在确认过程中发生变化，请重新读取授权预览", details={"reasons": current_reasons})
            current_plan = self._authorization_plan(current)
            current_hash = stable_hash(current_plan)
            current_token = stable_hash({"action": AUTHORIZE_NEW_PREDICTIVE_TRIAL, "preview_hash": current_hash, "candidate_hash": candidate_hash, "prior_trial_id": current["prior_record"]["trial_id"]})
            if preview_hash != current_hash or token != current_token:
                raise PredictiveTrialStartError("STALE_PREDICTIVE_NEW_AUTHORIZATION_PREVIEW", "新 Trial 授权预览已失效，请刷新后重新确认")
            record = self._new_authorization_record(current, authorization_id, preview_hash, token)
            self._governance._append_record(ledger_path, record)
            refs = {
                **dict(current["checkpoint"].canonical_refs or {}),
                "predictive_reauthorization": {
                    "schema_version": "predictive-governance-decision-v1",
                    "status": "AUTHORIZED",
                    "decision_id": record["decision_id"],
                    "decision_hash": record["decision_hash"],
                    "decision_type": AUTHORIZE_NEW_PREDICTIVE_TRIAL,
                    "authorization_id": authorization_id,
                    "candidate_id": candidate_id,
                    "candidate_hash": candidate_hash,
                    "prior_trial_id": record["prior_trial_id"],
                    "trial_number": 2,
                    "failure_review_ref": record["failure_review_ref"],
                    "failure_review_hash": record["failure_review_hash"],
                    "next_action": START_PREDICTIVE_TRIAL_2,
                    "trial_created": False,
                    "budget_reserved": 0,
                    "performance_access": 0,
                    "decision_ref": str(ledger_path.relative_to(self.root)).replace("\\", "/"),
                },
            }
            updated = current["checkpoint"].update(
                current_candidate=None,
                current_trial=None,
                required_action=START_PREDICTIVE_TRIAL_2,
                canonical_refs=refs,
                retry_safe=True,
                last_error=None,
                error_reason_code=None,
            )
            current["store"].save(updated)
            current["store"].append_event(
                "PREDICTIVE_NEW_TRIAL_AUTHORIZATION_RECORDED",
                {
                    "objective_id": objective_id,
                    "decision_id": record["decision_id"],
                    "decision_hash": record["decision_hash"],
                    "authorization_id": authorization_id,
                    "candidate_id": candidate_id,
                    "candidate_hash": candidate_hash,
                    "prior_trial_id": record["prior_trial_id"],
                    "trial_number": 2,
                    "next_action": START_PREDICTIVE_TRIAL_2,
                    "trial_created": False,
                    "budget_reserved": 0,
                },
                event_id=stable_hash({"event_type": "PREDICTIVE_NEW_TRIAL_AUTHORIZATION_RECORDED", "authorization_id": authorization_id}),
            )
            return {
                "schema_version": "predictive-governance-decision-v1",
                "status": "RECORDED",
                "idempotent": False,
                "decision": record,
                "next_action": START_PREDICTIVE_TRIAL_2,
                "message_zh": "已单独授权新建第 2 次预测试验；尚未创建 Trial、预留预算或访问绩效。",
                "safety_boundary": {"prior_trial_preserved": True, "trial_created": False, "budget_reserved": 0, "performance_access": 0, "final_test_access": record["final_test_access"]},
            }
        finally:
            instance_lock.release()

    @staticmethod
    def _new_lock(store: Any, objective_id: str, run_id: str) -> Any:
        from ..research_daemon_state import DaemonInstanceLockV1

        return DaemonInstanceLockV1(store.lock_path, objective_id)

    def _new_start_snapshot(self, objective_id: str, *, allow_inflight: bool = False) -> _StartSnapshot:
        context = self._canonical_context(objective_id)
        auth = self._latest_new_authorization(objective_id)
        reasons = list(context["reasons"])
        if auth is None:
            reasons.append("当前没有新 Trial 的 AUTHORIZED 治理决定")
        else:
            if str(auth.get("decision_status")) != "AUTHORIZED":
                reasons.append("新 Trial 治理决定不是 AUTHORIZED")
            if str(auth.get("decision_type")) != AUTHORIZE_NEW_PREDICTIVE_TRIAL:
                reasons.append("当前授权不是新 Trial 授权")
            if str(auth.get("next_action")) != START_PREDICTIVE_TRIAL_2:
                reasons.append(f"canonical next_action 不是 {START_PREDICTIVE_TRIAL_2}")
            if not auth.get("decision_hash") or stable_hash({key: value for key, value in auth.items() if key != "decision_hash"}) != str(auth.get("decision_hash")):
                reasons.append("新 Trial 授权 decision hash 校验失败")
            if str(auth.get("candidate_id")) != context["candidate_id"] or str(auth.get("candidate_hash")) != context["candidate_hash"]:
                reasons.append("新 Trial 授权与当前 Candidate 身份不一致")
            if str(auth.get("structural_reconciliation_id")) != str(context["reconciliation"].get("reconciliation_id")):
                reasons.append("新 Trial 授权与当前 structural reconciliation 不一致")
        checkpoint = context["checkpoint"]
        allowed_states = {ResearchDaemonState.ENGINEERING_BLOCKED.value}
        allowed_actions = {START_PREDICTIVE_TRIAL_2}
        if allow_inflight:
            allowed_states |= {ResearchDaemonState.PREDICTIVE_PENDING.value, ResearchDaemonState.PREDICTIVE_RUNNING.value}
            allowed_actions.add("PREDICTIVE_TRIAL_RUN_IN_PROGRESS")
        if checkpoint.current_state not in allowed_states:
            reasons.append(f"daemon 当前状态不允许新 Trial 启动：{checkpoint.current_state}")
        if str(checkpoint.required_action or "") not in allowed_actions:
            reasons.append(f"当前 required_action 不允许新 Trial 启动：{checkpoint.required_action}")
        prior = context["prior_record"] or {}
        t2_id = f"{context['batch_id']}_{context['candidate_hash']}_T002" if context["batch_id"] and context["candidate_hash"] else ""
        t2_record = context["trial_records"].get(t2_id)
        if t2_record is not None:
            if str(t2_record.get("candidate_id")) != context["candidate_id"] or str(t2_record.get("candidate_hash")) != context["candidate_hash"]:
                reasons.append("T002 记录与当前 Candidate 身份冲突")
            if checkpoint.current_trial and str(checkpoint.current_trial.get("trial_id")) != t2_id:
                reasons.append("checkpoint 当前 Trial 不是待运行的新 Trial")
        else:
            if len(context["trial_records"]) != 1:
                reasons.append("新 Trial 启动前必须只存在历史 T001")
            if checkpoint.current_trial is not None:
                reasons.append("新 Trial 启动前不应存在活动 Trial")
        if str(prior.get("trial_id")) == "" or self._trial_number(prior.get("trial_id")) != 1:
            reasons.append("失败审查来源不是 T001")
        if t2_record is None and (_int(context["budget"].get("remaining")) < 1 or _int(context["budget"].get("reserved")) != 0):
            reasons.append("新 Trial 启动预算不可用或已有预留")
        if context["family"] and _int(context["family"].get("hypothesis_slots")) < len(context["registrations"]) + (0 if t2_record else 1):
            reasons.append("Multiple Testing Family 没有新 Trial 槽位")
        return _StartSnapshot(
            objective=context["objective"],
            checkpoint=checkpoint,
            authorization=auth,
            candidate=context["contract"],
            candidate_id=context["candidate_id"],
            candidate_hash=context["candidate_hash"],
            contract_ref=context["contract_ref"],
            reconciliation=context["reconciliation"],
            structural=context["structural"],
            policy=context["policy"],
            policy_hash=context["policy_hash"],
            policy_path=context["policy_path"],
            budget_path=context["budget_path"],
            budget=context["budget"],
            batch_id=context["batch_id"],
            family_id=context["family_id"],
            decision_family_id=context["decision_family_id"],
            family_path=context["family_path"],
            trial_records=context["trial_records"],
            final_test_access=context["final_test_access"],
            final_test_boundary={"source": "ResearchDataAccessGuard + ValidationDecisionPolicyV2"},
            prospective=context["prospective"],
            real_order=context["real_order"],
            reasons=tuple(dict.fromkeys(reasons)),
        )

    def _snapshot(self, objective_id: str, *, allow_inflight: bool = False, allow_recovery: bool = False) -> _StartSnapshot:
        del allow_recovery
        return self._new_start_snapshot(objective_id, allow_inflight=allow_inflight)

    def _new_start_plan(self, snapshot: _StartSnapshot) -> dict[str, Any]:
        trial_id = f"{snapshot.batch_id}_{snapshot.candidate_hash}_T002" if snapshot.candidate and snapshot.batch_id else ""
        reservation_id = self._reservation_id(str(snapshot.objective["objective_id"]), snapshot.batch_id, snapshot.family_id, snapshot.candidate_id, 2) if snapshot.batch_id and snapshot.family_id else ""
        registration_path = self._multiple_testing_registration_path(snapshot)
        return {
            "action": START_PREDICTIVE_TRIAL_2,
            "objective_id": snapshot.objective.get("objective_id"),
            "objective_hash": snapshot.objective.get("objective_identity_hash") or snapshot.objective.get("objective_hash"),
            "candidate_id": snapshot.candidate_id,
            "candidate_hash": snapshot.candidate_hash,
            "candidate_state": "FROZEN",
            "candidate_contract_ref": snapshot.contract_ref,
            "candidate_contract_content_hash": snapshot.candidate.content_hash if snapshot.candidate else None,
            "authorization_decision_id": snapshot.authorization.get("decision_id") if snapshot.authorization else None,
            "authorization_decision_hash": snapshot.authorization.get("decision_hash") if snapshot.authorization else None,
            "authorization_id": snapshot.authorization.get("authorization_id") if snapshot.authorization else None,
            "structural_reconciliation_id": snapshot.reconciliation.get("reconciliation_id"),
            "structural": _copy(snapshot.structural),
            "prior_trial_id": snapshot.authorization.get("prior_trial_id") if snapshot.authorization else None,
            "trial_number": 2,
            "trial_id": trial_id,
            "budget_reservation_identity": reservation_id,
            "budget": _copy(snapshot.budget),
            "multiple_testing": {"family_id": snapshot.family_id, "decision_family_id": snapshot.decision_family_id, "trial_number": 2, "registration_ref": registration_path.relative_to(self.root).as_posix()},
            "validation_policy": {"policy_id": getattr(snapshot.policy, "policy_id", None), "policy_version": getattr(snapshot.policy, "policy_version", None), "policy_hash": snapshot.policy_hash},
            "execution_contract_version": snapshot.candidate.execution_contract_version if snapshot.candidate else None,
            "trial_count_before": len(snapshot.trial_records),
            "performance_access_before_start": 0,
            "final_test_access": _copy(snapshot.final_test_access),
            "prospective": snapshot.prospective,
            "real_order": snapshot.real_order,
            "new_trial_created": False,
            "previous_trial_preserved": True,
            "confirmation_required": True,
        }

    def preview_start_new_trial(self, objective_id: str) -> dict[str, Any]:
        objective_id = self._safe_id(objective_id, "objective_id")
        snapshot = self._new_start_snapshot(objective_id)
        if not snapshot.eligible:
            raise PredictiveTrialStartError("PREDICTIVE_NEW_TRIAL_START_UNAVAILABLE", "当前不满足安全启动新 Trial 的全部条件", details={"reasons": list(snapshot.reasons)})
        plan = self._new_start_plan(snapshot)
        preview_hash = stable_hash(plan)
        token = stable_hash({"action": START_PREDICTIVE_TRIAL_2, "preview_hash": preview_hash, "objective_id": objective_id, "candidate_hash": snapshot.candidate_hash, "trial_id": plan["trial_id"]})
        return {
            "schema_version": START_PREVIEW_SCHEMA,
            "status": "READY",
            "status_zh": "可以确认启动新 Trial",
            "available": True,
            "action": START_PREDICTIVE_TRIAL_2,
            "action_zh": ACTION_START_ZH,
            "objective_id": objective_id,
            "candidate_id": snapshot.candidate_id,
            "candidate_hash": snapshot.candidate_hash,
            "trial_number": 2,
            "trial_id": plan["trial_id"],
            "preview": plan,
            "preview_hash": preview_hash,
            "confirmation_token": token,
            "confirmation_required": True,
            "reason_zh": "已完成新 Trial 的独立授权；确认后预留第 2 个预算槽位、登记 Multiple Testing 和 Trial Ledger，再由 canonical executor 访问绩效。",
            "reasons": [],
            "generated_at": self._clock(),
        }

    def _contract_payload(self, snapshot: _StartSnapshot, intent: Mapping[str, Any]) -> dict[str, Any]:
        payload = super()._contract_payload(snapshot, intent)
        payload["trial_number"] = 2
        payload["multiple_testing"] = {**dict(payload.get("multiple_testing") or {}), "trial_number": 2}
        payload["deterministic_seed"] = int(getattr(snapshot.policy, "bootstrap_seed", 0) or 0) + 2
        payload["trial_contract_hash"] = stable_hash(payload)
        return payload

    def _candidate_work(self, snapshot: _StartSnapshot, intent: Mapping[str, Any]) -> Any:
        work = super()._candidate_work(snapshot, intent)
        return replace(work, metadata={**dict(work.metadata), "action": START_PREDICTIVE_TRIAL_2, "trial_number": 2, "prior_trial_id": intent.get("prior_trial_id")})

    def _trial_record(self, snapshot: _StartSnapshot, intent: Mapping[str, Any]) -> Any:
        record = super()._trial_record(snapshot, intent)
        return replace(
            record,
            lineage={
                **dict(record.lineage),
                "source": "FORMAL_PREDICTIVE_TRIAL_REAUTHORIZATION_V1",
                "canonical_start_action": START_PREDICTIVE_TRIAL_2,
                "trial_number": 2,
                "prior_trial_id": intent.get("prior_trial_id"),
                "reauthorization_id": intent.get("authorization_id"),
            },
        )

    def _running_checkpoint(self, snapshot: _StartSnapshot, intent: Mapping[str, Any], *, trial_status: str = "REGISTERED", stage: str = "TRIAL_RUNNING", performance_accessed: bool = False, recovery: bool = False) -> None:
        del recovery
        store = self._store(str(snapshot.objective["objective_id"]))
        checkpoint = store.load()
        if checkpoint is None:
            raise PredictiveTrialStartError("DAEMON_CHECKPOINT_NOT_FOUND", "当前 objective 缺少 canonical daemon checkpoint", status_code=503)
        if checkpoint.current_state == ResearchDaemonState.ENGINEERING_BLOCKED.value:
            checkpoint = checkpoint.transition(ResearchDaemonState.PREDICTIVE_PENDING, "PREDICTIVE_NEW_TRIAL_START_ACCEPTED", details={"start_intent_id": intent["intent_id"], "trial_id": intent["trial_id"], "trial_number": 2})
        trial_view = {
            "trial_id": intent["trial_id"],
            "candidate_id": intent["candidate_id"],
            "candidate_hash": intent["candidate_hash"],
            "trial_number": 2,
            "stage": stage,
            "status": trial_status,
            "performance_accessed": bool(performance_accessed),
            "created_at": intent["created_at"],
            "started_at": intent.get("started_at"),
            "last_activity_at": intent.get("last_activity_at") or self._clock(),
            "finished_at": intent.get("finished_at"),
            "error_code": intent.get("error_code"),
            "error_message": intent.get("error_message"),
            "recovery_status": intent.get("recovery_status") or "RUNNING",
            "budget_reservation_identity": intent["reservation_id"],
            "trial_contract_ref": intent["trial_contract_ref"],
            "trial_contract_hash": intent["trial_contract_hash"],
        }
        refs = {
            **dict(checkpoint.canonical_refs),
            "predictive_trial_reauthorization": {
                "action": START_PREDICTIVE_TRIAL_2,
                "start_intent_id": intent["intent_id"],
                "trial_id": intent["trial_id"],
                "trial_number": 2,
                "trial_contract_ref": intent["trial_contract_ref"],
                "trial_contract_hash": intent["trial_contract_hash"],
                "authorization_decision_id": intent.get("authorization_decision_id"),
                "authorization_decision_hash": intent.get("authorization_decision_hash"),
                "multiple_testing_family_id": snapshot.family_id,
                "prior_trial_id": intent.get("prior_trial_id"),
            },
        }
        checkpoint = checkpoint.update(
            current_candidate=self._candidate_work(snapshot, intent).to_dict(),
            current_trial=trial_view,
            required_action="PREDICTIVE_TRIAL_RUN_IN_PROGRESS",
            retry_safe=True,
            budget_view={**dict(checkpoint.budget_view), **_copy(snapshot.budget)},
            canonical_refs=refs,
        )
        if checkpoint.current_state == ResearchDaemonState.PREDICTIVE_PENDING.value:
            checkpoint = checkpoint.transition(ResearchDaemonState.PREDICTIVE_RUNNING, "PREDICTIVE_NEW_TRIAL_RUN_STARTED", details={"start_intent_id": intent["intent_id"], "trial_id": intent["trial_id"], "trial_number": 2})
        store.append_event(
            "PREDICTIVE_NEW_TRIAL_START_ACCEPTED",
            {"objective_id": snapshot.objective["objective_id"], "start_intent_id": intent["intent_id"], "trial_id": intent["trial_id"], "trial_number": 2, "candidate_id": snapshot.candidate_id, "candidate_hash": snapshot.candidate_hash, "performance_accessed": bool(performance_accessed)},
            event_id=stable_hash({"event_type": "PREDICTIVE_NEW_TRIAL_START_ACCEPTED", "start_intent_id": intent["intent_id"], "trial_id": intent["trial_id"]}),
        )
        store.save(checkpoint)

    def _materialize_locked(self, snapshot: _StartSnapshot, intents: dict[str, dict[str, Any]], intent: dict[str, Any]) -> dict[str, Any]:
        if snapshot.candidate is None or not snapshot.batch_id or not snapshot.family_id or snapshot.budget_path is None or snapshot.family_path is None:
            raise PredictiveTrialStartError("PREDICTIVE_NEW_TRIAL_START_UNAVAILABLE", "当前 Candidate 缺少完整的 canonical 新 Trial 绑定", details={"reasons": list(snapshot.reasons)})
        budget = SearchBudgetRegistryV1(str(snapshot.objective["objective_id"]), snapshot.budget_path)
        reservation_id = str(intent["reservation_id"])
        active = reservation_id in budget.snapshot().get("active_reservations", {})
        if not active:
            try:
                actual = budget.reserve_trial(batch_id=snapshot.batch_id, family_id=snapshot.family_id, candidate_id=snapshot.candidate_id, trial_number=2)
            except BudgetExhaustedError as exc:
                raise PredictiveTrialStartError("PREDICTIVE_BUDGET_EXHAUSTED", "当前预测预算不足，系统拒绝启动新 Trial", details={"budget": dict(snapshot.budget)}) from exc
            if str(actual) != reservation_id:
                raise PredictiveTrialStartError("BUDGET_RESERVATION_IDENTITY_CONFLICT", "canonical 新 Trial 预算预留身份不一致")
            intent = self._update_intent(str(snapshot.objective["objective_id"]), intents, str(intent["intent_id"]), stage="BUDGET_RESERVED", budget_after_reservation=budget.snapshot())
        contract_path = self._trial_contract_path(snapshot, str(intent["trial_id"]))
        contract_payload = self._contract_payload(snapshot, intent)
        self._write_immutable_contract(contract_path, contract_payload)
        intent = self._update_intent(str(snapshot.objective["objective_id"]), intents, str(intent["intent_id"]), stage="CONTRACT_FROZEN", trial_contract_ref=contract_path.relative_to(self.root).as_posix(), trial_contract_hash=contract_payload["trial_contract_hash"], data_snapshot_hash=contract_payload["data_snapshot"]["snapshot_hash"])
        registration = {
            "objective_id": str(snapshot.objective["objective_id"]),
            "family_id": snapshot.family_id,
            "decision_family_id": snapshot.decision_family_id,
            "trial_id": str(intent["trial_id"]),
            "trial_number": 2,
            "candidate_id": snapshot.candidate_id,
            "candidate_hash": snapshot.candidate_hash,
            "predictive_budget_total": _int(snapshot.budget.get("total")),
            "performance_accessed": False,
            "registered_at": intent["created_at"],
            "candidate_identity": {"candidate_id": snapshot.candidate_id, "candidate_hash": snapshot.candidate_hash},
        }
        registration_path = self._multiple_testing_registration_path(snapshot)
        MultipleTestingRegistrationLedgerV1(registration_path, family_path=snapshot.family_path).register(registration, allow_additional_trial=True)
        intent = self._update_intent(str(snapshot.objective["objective_id"]), intents, str(intent["intent_id"]), stage="MULTIPLE_TESTING_REGISTERED", multiple_testing_registration_ref=registration_path.relative_to(self.root).as_posix())
        ledger = self._trial_ledger(snapshot)
        existing = ledger.latest().get(str(intent["trial_id"]))
        if existing is None:
            ledger.register_before_performance(record=self._trial_record(snapshot, intent))
        else:
            expected = self._trial_record(snapshot, intent)
            if existing.candidate_id != expected.candidate_id or existing.candidate_hash != expected.candidate_hash or existing.budget_reservation_identity != expected.budget_reservation_identity or existing.lineage.get("trial_contract_hash") != expected.lineage.get("trial_contract_hash"):
                raise PredictiveTrialStartError("TRIAL_IDENTITY_CONFLICT", "同一新 Trial ID 已绑定不同的 Candidate、预算或 Trial Contract")
        intent = self._update_intent(str(snapshot.objective["objective_id"]), intents, str(intent["intent_id"]), stage="TRIAL_CREATED", started_at=self._clock(), last_activity_at=self._clock())
        current_record = ledger.latest().get(str(intent["trial_id"]))
        if current_record is not None and current_record.status == "REGISTERED" and current_record.stage != "TRIAL_RUNNING":
            ledger.mark_started(trial_id=current_record.trial_id, started_at=intent["started_at"], last_activity_at=intent["last_activity_at"])
        refreshed_snapshot = self._new_start_snapshot(str(snapshot.objective["objective_id"]), allow_inflight=True)
        self._running_checkpoint(refreshed_snapshot, intent)
        intent = self._update_intent(str(snapshot.objective["objective_id"]), intents, str(intent["intent_id"]), stage="TRIAL_RUNNING", last_activity_at=self._clock(), recovery_status="RUNNING")
        return self._new_receipt(refreshed_snapshot, intent, idempotent=False)

    def _new_receipt(self, snapshot: _StartSnapshot | None, intent: Mapping[str, Any], *, idempotent: bool) -> dict[str, Any]:
        receipt = super()._receipt(snapshot, intent, idempotent=idempotent)
        receipt.update(
            {
                "schema_version": START_RECEIPT_SCHEMA,
                "action": START_PREDICTIVE_TRIAL_2,
                "action_zh": ACTION_START_ZH,
                "trial_number": 2,
                "prior_trial_id": intent.get("prior_trial_id"),
                "message_zh": "第 2 次预测试验已按重新授权后的 canonical 流程创建并进入运行状态；T001 历史保持不变。" if receipt.get("stage") == "TRIAL_RUNNING" else "已返回新 Trial 的启动状态。",
            }
        )
        return receipt

    def _ensure_new_start_request(self, body: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
        if body.get("confirmed") is not True:
            raise PredictiveTrialStartError("CONFIRMATION_REQUIRED", "启动新 Trial 需要在确认页中明确确认", status_code=400)
        action = str(body.get("action") or "").strip().upper()
        if action != START_PREDICTIVE_TRIAL_2:
            raise PredictiveTrialStartError("CANONICAL_NEW_START_ACTION_REQUIRED", f"请求必须明确使用 {START_PREDICTIVE_TRIAL_2}", status_code=400)
        intent_id = self._safe_id(body.get("start_intent_id") or body.get("idempotency_key"), "start_intent_id")
        candidate_id = self._safe_id(body.get("candidate_id"), "candidate_id")
        candidate_hash = str(body.get("candidate_hash") or "")
        preview_hash = str(body.get("preview_hash") or "")
        token = str(body.get("confirmation_token") or "")
        if not candidate_hash or not preview_hash or not token:
            raise PredictiveTrialStartError("PREDICTIVE_NEW_START_PREVIEW_REQUIRED", "确认前必须先读取新 Trial 启动预览", status_code=400)
        return action, intent_id, candidate_id, candidate_hash, preview_hash, token

    def confirm_start_new_trial(self, objective_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        objective_id = self._safe_id(objective_id, "objective_id")
        action, intent_id, candidate_id, candidate_hash, preview_hash, token = self._ensure_new_start_request(body)
        store = self._store(objective_id)
        checkpoint = store.load()
        if checkpoint is None:
            raise PredictiveTrialStartError("DAEMON_CHECKPOINT_NOT_FOUND", "当前 objective 缺少 canonical daemon checkpoint", status_code=503)
        instance_lock = self._new_lock(store, objective_id, f"PREDICTIVE_NEW_TRIAL_START_{intent_id}")
        try:
            instance_lock.acquire(run_id=f"PREDICTIVE_NEW_TRIAL_START_{intent_id}")
        except DaemonAlreadyRunningError as exc:
            existing = self._load_intents(objective_id).get(intent_id)
            if existing is not None:
                self._validate_existing_intent(existing, action=action, candidate_id=candidate_id, candidate_hash=candidate_hash, preview_hash=preview_hash, token=token)
                return self._new_receipt(None, existing, idempotent=True)
            raise PredictiveTrialStartError("PREDICTIVE_NEW_TRIAL_START_LOCKED", "当前 objective 正在执行其他受保护操作，请稍后读取新 Trial 启动状态") from exc
        try:
            intents = self._load_intents(objective_id)
            existing = intents.get(intent_id)
            if existing is not None:
                self._validate_existing_intent(existing, action=action, candidate_id=candidate_id, candidate_hash=candidate_hash, preview_hash=preview_hash, token=token)
                return self._new_receipt(None, existing, idempotent=True)
            snapshot = self._new_start_snapshot(objective_id)
            if snapshot.candidate_id != candidate_id or snapshot.candidate_hash != candidate_hash:
                raise PredictiveTrialStartError("PREDICTIVE_NEW_TRIAL_START_CANDIDATE_MISMATCH", "确认页中的 Candidate 身份已过期，请刷新预览后重试")
            if not snapshot.eligible:
                raise PredictiveTrialStartError("PREDICTIVE_NEW_TRIAL_START_UNAVAILABLE", "当前不满足安全启动新 Trial 的全部条件", details={"reasons": list(snapshot.reasons)})
            plan = self._new_start_plan(snapshot)
            expected_hash = stable_hash(plan)
            expected_token = stable_hash({"action": START_PREDICTIVE_TRIAL_2, "preview_hash": expected_hash, "objective_id": objective_id, "candidate_hash": snapshot.candidate_hash, "trial_id": plan["trial_id"]})
            if preview_hash != expected_hash or token != expected_token:
                raise PredictiveTrialStartError("STALE_PREDICTIVE_NEW_TRIAL_START_PREVIEW", "新 Trial 启动预览已失效，请刷新后重新确认")
            intent = {
                "schema_version": START_INTENT_SCHEMA,
                "intent_id": intent_id,
                "objective_id": objective_id,
                "action": action,
                "candidate_id": candidate_id,
                "candidate_hash": candidate_hash,
                "preview_hash": preview_hash,
                "confirmation_token_hash": stable_hash(token),
                "authorization_decision_id": snapshot.authorization.get("decision_id") if snapshot.authorization else None,
                "authorization_decision_hash": snapshot.authorization.get("decision_hash") if snapshot.authorization else None,
                "authorization_id": snapshot.authorization.get("authorization_id") if snapshot.authorization else None,
                "prior_trial_id": snapshot.authorization.get("prior_trial_id") if snapshot.authorization else None,
                "trial_number": 2,
                "trial_id": plan["trial_id"],
                "reservation_id": plan["budget_reservation_identity"],
                "batch_id": snapshot.batch_id,
                "family_id": snapshot.family_id,
                "created_at": self._clock(),
                "updated_at": self._clock(),
                "stage": "REQUESTED",
                "recovery_status": "PENDING",
                "data_snapshot_hash": stable_hash(plan["final_test_access"]),
                "engine_hash": stable_hash({"runner": "CanonicalPredictiveExecutorV1", "execution_contract_version": snapshot.candidate.execution_contract_version if snapshot.candidate else None, "policy_hash": snapshot.policy_hash, "trial_number": 2}),
                "seed": int(getattr(snapshot.policy, "bootstrap_seed", 0) or 0) + 2,
            }
            intent = self._put_intent(objective_id, intents, intent)
            return self._materialize_locked(snapshot, intents, intent)
        finally:
            instance_lock.release()

    def run_confirmed_new_trial(self, objective_id: str, start_intent_id: str) -> None:
        objective_id = self._safe_id(objective_id, "objective_id")
        start_intent_id = self._safe_id(start_intent_id, "start_intent_id")
        self._run_intent(objective_id, start_intent_id)

    def recover_new_all(self) -> dict[str, Any]:
        """Recover only this module's confirmed new-Trial intents."""
        root = self.root / "reports" / "research_daemon"
        recovered: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        if not root.is_dir():
            return {"schema_version": START_RECEIPT_SCHEMA, "status": "NO_PENDING_TRIALS", "objectives": [], "errors": []}
        for path in sorted(root.glob(f"*/{self._intent_path('x').name}")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                objective_id = self._safe_id(payload.get("objective_id"), "objective_id")
                intents = payload.get("intents") if isinstance(payload, Mapping) else {}
                pending = isinstance(intents, Mapping) and any(
                    str(item.get("action")) == START_PREDICTIVE_TRIAL_2
                    and (str(item.get("stage")) in IN_FLIGHT_STAGES or bool(item.get("recovery_requested")))
                    for item in intents.values()
                    if isinstance(item, Mapping)
                )
                if pending:
                    recovered.append(self.recover(objective_id))
            except (OSError, UnicodeError, json.JSONDecodeError, PredictiveTrialStartError) as exc:
                errors.append({"path": path.relative_to(self.root).as_posix(), "code": exc.code if isinstance(exc, PredictiveTrialStartError) else "PREDICTIVE_NEW_TRIAL_RECOVERY_SOURCE_INVALID", "message_zh": exc.message_zh if isinstance(exc, PredictiveTrialStartError) else "新 Trial 启动恢复源暂时不可读"})
        return {"schema_version": START_RECEIPT_SCHEMA, "status": "RECOVERED" if recovered else "NO_PENDING_TRIALS", "objectives": recovered, "errors": errors}


__all__ = [
    "AUTHORIZE_NEW_PREDICTIVE_TRIAL",
    "START_PREDICTIVE_TRIAL_2",
    "PredictiveTrialReauthorizationServiceV1",
]
