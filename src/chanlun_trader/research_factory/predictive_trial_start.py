"""Formal, user-triggered entry for ``START_PREDICTIVE_TRIAL_1``.

The predictive governance service deliberately stops at ``AUTHORIZED``.  This
module owns the next, explicit user action and only wires the existing budget,
trial, policy and executor components together.  It does not create a new
Candidate or a second Trial accounting system.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import inspect
from pathlib import Path
import re
import threading
import time
import uuid
from typing import Any, Callable, Mapping, Protocol

from ..research_daemon_state import (
    DaemonAlreadyRunningError,
    DaemonCheckpointStoreV1,
    DaemonInstanceLockV1,
    ResearchDaemonState,
)
from ..research.guard import ResearchDataAccessGuard
from ..research.strategy_validation import TrialRegistryV1
from .budget import BudgetExhaustedError, SearchBudgetRegistryV1
from .common import jsonable, now_timestamp, stable_hash
from .durability import DurableFrozenCandidateContractRegistryV1, DurableFrozenCandidateContractV1
from .predictive_authorization import AUTHORIZE_FIRST_PREDICTIVE_TRIAL
from .predictive_executor import CanonicalPredictiveExecutorV1
from .trial_adapter import FactoryTrialRecordV1, ResearchFactoryTrialLedgerFacadeV1


START_PREDICTIVE_TRIAL_1 = "START_PREDICTIVE_TRIAL_1"
START_ACTION_ZH = "启动第 1 次预测试验"
RESUME_PREDICTIVE_TRIAL_1 = "RESUME_PREDICTIVE_TRIAL_1"
RESUME_ACTION_ZH = "恢复第 1 次预测试验"
START_PREVIEW_SCHEMA = "predictive-trial-start-preview-v1"
RESUME_PREVIEW_SCHEMA = "predictive-trial-resume-preview-v1"
START_INTENT_SCHEMA = "predictive-trial-start-intent-v1"
START_RECEIPT_SCHEMA = "predictive-trial-start-receipt-v1"
TRIAL_CONTRACT_SCHEMA = "predictive-trial-contract-v1"
MULTIPLE_TESTING_REGISTRATION_SCHEMA = "predictive-multiple-testing-registration-v1"
START_INTENTS_FILENAME = "predictive_trial_start_intents.json"
MULTIPLE_TESTING_REGISTRATIONS_SUFFIX = ".trial_registrations.json"
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
TERMINAL_TRIAL_STATUSES = frozenset({"COMPLETED", "BLOCKED", "INVALIDATED", "SUPERSEDED"})
IN_FLIGHT_STAGES = frozenset({"REQUESTED", "CONTRACT_FROZEN", "BUDGET_RESERVED", "MULTIPLE_TESTING_REGISTERED", "TRIAL_CREATED", "TRIAL_RUNNING"})
RESUME_IN_FLIGHT_STAGES = frozenset({"TRIAL_RECOVERY_REQUESTED", "TRIAL_RECOVERY_RUNNING"})


class PredictiveTrialStartError(RuntimeError):
    """Safe, local error for the formal predictive Trial start boundary."""

    def __init__(self, code: str, message_zh: str, *, status_code: int = 409, details: Mapping[str, Any] | None = None):
        super().__init__(message_zh)
        self.code = str(code)
        self.message_zh = str(message_zh)
        self.status_code = int(status_code)
        self.details = dict(details or {})

    def envelope(self) -> dict[str, Any]:
        return {"code": self.code, "message_zh": self.message_zh, "details": dict(self.details)}


class PredictiveTrialRunner(Protocol):
    def run(self, candidate: Any, *, recovery: bool = False) -> Any: ...


class PredictiveTrialRunnerV1:
    """The only execution adapter used after the formal start is recorded."""

    def __init__(self, root: str | Path, objective_id: str, *, executor: Any | None = None):
        self.executor = executor or CanonicalPredictiveExecutorV1(root, objective_id)

    def run(self, candidate: Any, *, recovery: bool = False) -> Any:
        if callable(self.executor):
            return self.executor(candidate, recovery=recovery) if recovery else self.executor(candidate)
        return self.executor.execute(candidate, recovery=recovery) if recovery else self.executor.execute(candidate)


class MultipleTestingRegistrationLedgerV1:
    """Append-only membership ledger next to the existing frozen family.

    The family JSON remains the immutable research-design definition.  This
    companion ledger records execution membership before performance access;
    it is not a replacement TrialLedger and does not change the family rules.
    """

    def __init__(self, path: str | Path, *, family_path: str | Path):
        self.path = Path(path)
        self.family_path = Path(family_path)

    def _load(self) -> dict[str, Any]:
        if not self.family_path.is_file():
            raise PredictiveTrialStartError("MULTIPLE_TESTING_FAMILY_NOT_FOUND", "当前研究的 Multiple Testing Family 定义不存在")
        try:
            family = json.loads(self.family_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PredictiveTrialStartError("MULTIPLE_TESTING_FAMILY_UNREADABLE", "Multiple Testing Family 定义暂时不可读", status_code=503) from exc
        if not isinstance(family, Mapping):
            raise PredictiveTrialStartError("MULTIPLE_TESTING_FAMILY_INVALID", "Multiple Testing Family 定义格式不受支持", status_code=503)
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise PredictiveTrialStartError("MULTIPLE_TESTING_LEDGER_INVALID", "Multiple Testing 登记账本无法安全读取", status_code=503) from exc
            if not isinstance(payload, Mapping) or payload.get("schema_version") != MULTIPLE_TESTING_REGISTRATION_SCHEMA:
                raise PredictiveTrialStartError("MULTIPLE_TESTING_LEDGER_INVALID", "Multiple Testing 登记账本格式不受支持", status_code=503)
            registrations = payload.get("registrations", ())
            if not isinstance(registrations, list):
                raise PredictiveTrialStartError("MULTIPLE_TESTING_LEDGER_INVALID", "Multiple Testing 登记账本记录不是列表", status_code=503)
        else:
            payload = {"schema_version": MULTIPLE_TESTING_REGISTRATION_SCHEMA, "family_definition_ref": self.family_path.as_posix(), "registrations": []}
        payload = dict(payload)
        payload["family"] = dict(family)
        return payload

    def registrations(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(dict(item) for item in self._load().get("registrations", ()) if isinstance(item, Mapping))

    def register(self, registration: Mapping[str, Any], *, allow_additional_trial: bool = False) -> dict[str, Any]:
        payload = self._load()
        family = dict(payload["family"])
        if str(family.get("objective_id") or "") != str(registration.get("objective_id") or ""):
            raise PredictiveTrialStartError("MULTIPLE_TESTING_OBJECTIVE_MISMATCH", "Multiple Testing Family 与当前 objective 不一致")
        if str(family.get("family_id") or "") != str(registration.get("family_id") or ""):
            raise PredictiveTrialStartError("MULTIPLE_TESTING_FAMILY_MISMATCH", "当前 Trial 没有绑定当前 Multiple Testing Family")
        existing = next((item for item in payload.get("registrations", ()) if str(item.get("trial_id")) == str(registration.get("trial_id"))), None)
        if existing is not None:
            identity_keys = {"schema_version", "family_definition_hash", "registration_hash"}
            existing_identity = {key: value for key, value in existing.items() if key not in identity_keys}
            registration_identity = {key: value for key, value in registration.items() if key not in identity_keys}
            if stable_hash(existing_identity) != stable_hash(registration_identity):
                raise PredictiveTrialStartError("MULTIPLE_TESTING_IDENTITY_CONFLICT", "相同 Trial 身份对应了不同的 Multiple Testing 登记")
            return dict(existing)
        if payload.get("registrations") and not allow_additional_trial:
            raise PredictiveTrialStartError("MULTIPLE_TESTING_TRIAL_ALREADY_REGISTERED", "当前 Multiple Testing Family 已存在预测试验登记")
        if payload.get("registrations") and allow_additional_trial:
            trial_number = _int(registration.get("trial_number"), 0)
            existing_numbers = {_int(item.get("trial_number"), 0) for item in payload["registrations"] if isinstance(item, Mapping)}
            hypothesis_slots = _int(family.get("hypothesis_slots"), 0)
            if trial_number < 1 or trial_number in existing_numbers or (hypothesis_slots and len(payload["registrations"]) >= hypothesis_slots):
                raise PredictiveTrialStartError("MULTIPLE_TESTING_TRIAL_CAPACITY_EXCEEDED", "当前 Multiple Testing Family 不允许登记该新的 Trial")
        item = dict(registration)
        item["schema_version"] = MULTIPLE_TESTING_REGISTRATION_SCHEMA
        item["family_definition_hash"] = stable_hash(family)
        item["registration_hash"] = stable_hash(item)
        registrations = [*payload.get("registrations", ()), item]
        output = {
            "schema_version": MULTIPLE_TESTING_REGISTRATION_SCHEMA,
            "family_definition_ref": self.family_path.as_posix(),
            "family_definition_hash": stable_hash(family),
            "registrations": registrations,
            "ledger_hash": stable_hash(registrations),
        }
        _atomic_write(self.path, output)
        return item


@dataclass(frozen=True)
class _StartSnapshot:
    objective: Mapping[str, Any]
    checkpoint: Any
    authorization: Mapping[str, Any] | None
    candidate: DurableFrozenCandidateContractV1 | None
    candidate_id: str
    candidate_hash: str
    contract_ref: str
    reconciliation: Mapping[str, Any]
    structural: Mapping[str, Any]
    policy: Any | None
    policy_hash: str
    policy_path: Path | None
    budget_path: Path | None
    budget: Mapping[str, Any]
    batch_id: str
    family_id: str
    decision_family_id: str
    family_path: Path | None
    trial_records: Mapping[str, Mapping[str, Any]]
    final_test_access: Mapping[str, int]
    final_test_boundary: Mapping[str, Any]
    prospective: str
    real_order: str
    reasons: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return not self.reasons


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(jsonable(value), ensure_ascii=False, sort_keys=True, default=str))


class PredictiveTrialStartServiceV1:
    """Canonical start-action service with durable intent and exact-once wiring."""

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], str] | None = None,
        runner: Any | None = None,
        auto_run: bool = True,
    ):
        self.root = Path(root).resolve()
        self._clock = clock or now_timestamp
        self._runner = runner
        self.auto_run = bool(auto_run)
        self._threads: dict[str, threading.Thread] = {}
        self._threads_lock = threading.Lock()
        self._confirm_lock = threading.Lock()

    def _safe_id(self, value: Any, name: str) -> str:
        text = str(value or "")
        if not SAFE_ID.fullmatch(text):
            raise PredictiveTrialStartError("INVALID_IDENTIFIER", f"{name} 标识不合法", status_code=400)
        return text

    def _objective(self, objective_id: str) -> Mapping[str, Any]:
        path = self.root / "data/research/research_factory/objectives" / f"{objective_id}.json"
        if not path.is_file():
            raise PredictiveTrialStartError("UNKNOWN_OBJECTIVE", "未找到请求的研究 objective", status_code=404)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PredictiveTrialStartError("OBJECTIVE_SOURCE_UNREADABLE", "研究 objective 暂时不可读", status_code=503) from exc
        if not isinstance(payload, Mapping) or str(payload.get("objective_id")) != objective_id:
            raise PredictiveTrialStartError("OBJECTIVE_SOURCE_MISMATCH", "研究 objective 身份校验失败", status_code=503)
        return payload

    def _store(self, objective_id: str) -> DaemonCheckpointStoreV1:
        return DaemonCheckpointStoreV1(self.root, objective_id)

    def _authorization_rows(self, objective_id: str) -> list[dict[str, Any]]:
        path = self.root / "reports/research_orchestrator_v2" / objective_id / "predictive_governance_decisions.jsonl"
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise PredictiveTrialStartError("PREDICTIVE_GOVERNANCE_LEDGER_UNREADABLE", "预测治理授权账本暂时不可读", status_code=503) from exc
        rows: list[dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PredictiveTrialStartError("PREDICTIVE_GOVERNANCE_LEDGER_INVALID", "预测治理授权账本存在无法解析的记录", status_code=503) from exc
            if isinstance(item, Mapping):
                self._validate_authorization_record(item)
                rows.append(dict(item))
        return rows

    def _validate_authorization_record(self, record):
        if record.get("authorization_origin") == "BATCH_DELEGATED" or record.get("batch_delegation") is not None:
            raise PredictiveTrialStartError("BATCH_VERSIONED_START_REQUIRED", "批次委托必须通过核验当前父授权的新版本入口")

    def _trial_records(self, objective_id: str) -> dict[str, Mapping[str, Any]]:
        root = self.root / "data/research/research_factory/batches"
        latest: dict[str, tuple[str, int, Mapping[str, Any]]] = {}
        for path in sorted(root.glob("*/factory_trial_ledger.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise PredictiveTrialStartError("TRIAL_LEDGER_UNREADABLE", "canonical Trial ledger 暂时不可读", status_code=503) from exc
            for index, event in enumerate(payload.get("events", ()) if isinstance(payload, Mapping) else ()):
                if not isinstance(event, Mapping) or not event.get("trial_id") or str(event.get("objective_id")) != objective_id:
                    continue
                trial_id = str(event["trial_id"])
                version = (str(event.get("updated_at") or event.get("created_at") or ""), index)
                previous = latest.get(trial_id)
                if previous is None or version >= (previous[0], previous[1]):
                    latest[trial_id] = (version[0], version[1], dict(event))
        return {trial_id: item[2] for trial_id, item in latest.items()}

    @staticmethod
    def _json_read_checked(path: Path, code: str) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PredictiveTrialStartError(code, "canonical 恢复证据暂时不可读", status_code=503) from exc
        if not isinstance(payload, Mapping):
            raise PredictiveTrialStartError(code, "canonical 恢复证据格式不受支持", status_code=503)
        return dict(payload)

    def _load_recovery_context(self, objective_id: str) -> dict[str, Any]:
        """Read the one existing engineering-invalidated Trial for recovery."""

        snapshot = self._snapshot(objective_id, allow_inflight=True, allow_recovery=True)
        if not snapshot.eligible:
            raise PredictiveTrialStartError(
                "PREDICTIVE_TRIAL_RECOVERY_UNAVAILABLE",
                "当前 Trial 不满足安全恢复条件",
                details={"reasons": list(snapshot.reasons)},
            )
        if len(snapshot.trial_records) != 1:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_TRIAL_COUNT_INVALID", "当前 objective 不是恰好 1 个可恢复 Trial")
        record = next(iter(snapshot.trial_records.values()))
        lineage = record.get("lineage") if isinstance(record.get("lineage"), Mapping) else {}
        if (
            str(record.get("status") or "").upper() != "INVALIDATED"
            or str(record.get("classification") or "") != "ENGINEERING_INVALIDATED"
            or not bool(record.get("performance_accessed"))
            or bool(record.get("performance_complete"))
            or bool(record.get("final_adjudicated"))
            or bool(record.get("registry_committed"))
            or lineage.get("engineering_interrupted") is not True
        ):
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_NOT_ENGINEERING_INVALIDATED", "当前 Trial 不是可恢复的工程失效终态")
        if lineage.get("engineering_resume_started") is True or _int(lineage.get("engineering_resume_attempt_count")) > 0:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_ALREADY_ATTEMPTED", "该工程失效 Trial 已经尝试过恢复，不能再次访问预测绩效")
        trial_id = str(record.get("trial_id") or "")
        intent_id = str(record.get("start_intent_id") or lineage.get("start_intent_id") or "")
        intents = self._load_intents(objective_id)
        intent = intents.get(intent_id)
        if intent is None or str(intent.get("trial_id")) != trial_id:
            matches = [item for item in intents.values() if str(item.get("trial_id")) == trial_id]
            intent = matches[0] if len(matches) == 1 else None
            intent_id = str(intent.get("intent_id") or "") if intent else intent_id
        if intent is None:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_INTENT_MISSING", "可恢复 Trial 缺少原始启动意图")
        if str(intent.get("candidate_id")) != snapshot.candidate_id or str(intent.get("candidate_hash")) != snapshot.candidate_hash:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_CANDIDATE_MISMATCH", "恢复意图与冻结 Candidate 身份不一致")
        trial_contract_ref = str(intent.get("trial_contract_ref") or lineage.get("trial_contract_ref") or "")
        trial_contract_hash = str(intent.get("trial_contract_hash") or lineage.get("trial_contract_hash") or "")
        contract_path = (self.root / trial_contract_ref).resolve() if trial_contract_ref else self.root
        if not trial_contract_ref or not contract_path.is_relative_to(self.root) or not contract_path.is_file():
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_CONTRACT_MISSING", "原 Trial Contract 不存在或不在项目范围内")
        contract_payload = self._json_read_checked(contract_path, "PREDICTIVE_TRIAL_RECOVERY_CONTRACT_INVALID")
        if str(contract_payload.get("trial_contract_hash")) != trial_contract_hash:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_CONTRACT_HASH_MISMATCH", "原 Trial Contract hash 已变化")
        if str(contract_payload.get("candidate_id")) != snapshot.candidate_id or str(contract_payload.get("candidate_hash")) != snapshot.candidate_hash:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_CONTRACT_IDENTITY_MISMATCH", "原 Trial Contract 与 Candidate 身份不一致")
        reservation_id = str(record.get("budget_reservation_identity") or intent.get("reservation_id") or "")
        if not reservation_id or reservation_id != str(intent.get("reservation_id") or ""):
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_BUDGET_IDENTITY_MISMATCH", "原 Trial 的预算预留身份不一致")
        if snapshot.budget_path is None:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_BUDGET_MISSING", "canonical SearchBudgetRegistry 不存在")
        budget = SearchBudgetRegistryV1(objective_id, snapshot.budget_path)
        budget_snapshot = budget.snapshot()
        if (
            str(budget_snapshot.get("settled_reservations", {}).get(reservation_id)) != "CONSUMED"
            or reservation_id in budget_snapshot.get("active_reservations", {})
            or _int(snapshot.budget.get("used")) != 1
            or _int(snapshot.budget.get("reserved")) != 0
            or _int(snapshot.budget.get("remaining")) != _int(snapshot.budget.get("total")) - 1
        ):
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_BUDGET_NOT_STABLE", "原预算槽位不是 used=1、reserved=0 的已结算状态")
        registration_ref = str(intent.get("multiple_testing_registration_ref") or lineage.get("multiple_testing_registration_ref") or "")
        registration_path = (self.root / registration_ref).resolve() if registration_ref else self.root
        if not registration_ref or not registration_path.is_relative_to(self.root) or not registration_path.is_file():
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_MULTIPLE_TESTING_MISSING", "原 Multiple Testing 登记不存在")
        registration_payload = self._json_read_checked(registration_path, "PREDICTIVE_TRIAL_RECOVERY_MULTIPLE_TESTING_INVALID")
        registrations = [item for item in registration_payload.get("registrations", ()) if isinstance(item, Mapping)]
        matching_registrations = [item for item in registrations if str(item.get("trial_id")) == trial_id]
        if len(registrations) != 1 or len(matching_registrations) != 1:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_MULTIPLE_TESTING_COUNT_INVALID", "Multiple Testing 登记不是原 Trial 的唯一登记")
        registration = matching_registrations[0]
        if str(registration.get("candidate_id")) != snapshot.candidate_id or str(registration.get("candidate_hash")) != snapshot.candidate_hash or _int(registration.get("trial_number")) != 1:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_MULTIPLE_TESTING_IDENTITY_MISMATCH", "原 Multiple Testing 登记身份不一致")
        report_dir = self.root / "reports/research_daemon" / objective_id / "predictive" / str(record.get("batch_id")) / str(record.get("candidate_id"))
        final_status_path = report_dir / "final_status.json"
        final_status = self._json_read_checked(final_status_path, "PREDICTIVE_TRIAL_RECOVERY_FINAL_STATUS_INVALID") if final_status_path.is_file() else {}
        if final_status and (
            str(final_status.get("trial_id")) != trial_id
            or str(final_status.get("status") or "").upper() not in {"ENGINEERING_BLOCKED", "TRIAL_FAILED"}
            or bool(final_status.get("performance_completed"))
            or any(_int((final_status.get("final_test_access") or {}).get(key)) != 0 for key in ("physical", "analytical", "decision"))
        ):
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_FINAL_STATUS_INVALID", "原 Trial 终态报告不满足安全恢复边界")
        provisional_path = report_dir / "provisional_validation_evidence" / f"{trial_id}.json"
        validation_path = report_dir / "validation_results.json"
        if provisional_path.exists() or validation_path.exists():
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_EVIDENCE_EXISTS", "原 Trial 已存在预测证据，不能按工程恢复路径重开")
        return {
            "snapshot": snapshot,
            "record": record,
            "intent": intent,
            "intents": intents,
            "trial_contract_ref": trial_contract_ref,
            "trial_contract_hash": trial_contract_hash,
            "contract_path": contract_path,
            "contract_payload": contract_payload,
            "reservation_id": reservation_id,
            "budget": budget,
            "budget_snapshot": budget_snapshot,
            "registration_ref": registration_ref,
            "registration_path": registration_path,
            "registration": registration,
            "report_dir": report_dir,
            "final_status": final_status,
            "final_status_path": final_status_path,
        }

    def _event_materialization_audit(self, context: Mapping[str, Any]) -> dict[str, Any]:
        from .real_sample_feasibility import CANONICAL_DAILY_WARMUP_START, RealSampleFeasibilityProviderV1

        snapshot = context["snapshot"]
        assert snapshot.policy is not None and snapshot.candidate is not None
        provider = RealSampleFeasibilityProviderV1(self.root, streaming=False)
        daily = provider._load_daily(CANONICAL_DAILY_WARMUP_START, int(snapshot.policy.research_end), CANONICAL_DAILY_WARMUP_START)
        provider_candidate = snapshot.candidate.provider_candidate_payload()
        conditions = provider_candidate.get("event_conditions", ())
        event_values, resolutions = provider.materialize_event_rows(
            conditions,
            daily,
            CANONICAL_DAILY_WARMUP_START,
            int(snapshot.policy.research_end),
            record=provider_candidate,
        )
        unresolved = sorted(event_id for event_id, item in resolutions.items() if not bool(item.get("materialized")))
        event_days = sorted({int(day) for day, _symbol in event_values})
        return {
            "status": "PASS" if not unresolved and (resolutions or not conditions) else "BLOCKED",
            "event_ids": [str(item.get("event_id")) for item in conditions if isinstance(item, Mapping)],
            "resolutions": resolutions,
            "unresolved_event_ids": unresolved,
            "event_day_count": len(event_days),
            "event_days_hash": stable_hash(event_days),
            "event_source": provider.event_source_identity(),
            "benchmark_coverage": provider.benchmark_coverage,
            "daily_rows_read": int(len(daily)),
            "performance_data_loaded": False,
            "final_test_access": {"physical": 0, "analytical": 0, "decision": 0},
            "source_read_audit": sorted(set(provider._audit)),
        }

    def recovery_readiness(self, objective_id: str) -> dict[str, Any]:
        """Return a cheap read-only eligibility projection for the console."""

        objective_id = self._safe_id(objective_id, "objective_id")
        try:
            context = self._load_recovery_context(objective_id)
        except PredictiveTrialStartError as exc:
            return {
                "schema_version": RESUME_PREVIEW_SCHEMA,
                "status": "UNAVAILABLE",
                "status_zh": "当前不可恢复",
                "available": False,
                "objective_id": objective_id,
                "action": RESUME_PREDICTIVE_TRIAL_1,
                "action_zh": RESUME_ACTION_ZH,
                "reason_code": exc.code,
                "reason_zh": exc.message_zh,
                "reasons": list(exc.details.get("reasons", ())) or [exc.message_zh],
                "trial_count": len(self._trial_records(objective_id)),
            }
        snapshot = context["snapshot"]
        record = context["record"]
        return {
            "schema_version": RESUME_PREVIEW_SCHEMA,
            "status": "READY",
            "status_zh": "可以申请恢复",
            "available": True,
            "objective_id": objective_id,
            "action": RESUME_PREDICTIVE_TRIAL_1,
            "action_zh": RESUME_ACTION_ZH,
            "candidate_id": snapshot.candidate_id,
            "candidate_hash": snapshot.candidate_hash,
            "trial_id": record.get("trial_id"),
            "start_intent_id": context["intent"].get("intent_id"),
            "trial_number": 1,
            "trial_count": len(snapshot.trial_records),
            "candidate_contract_content_hash": snapshot.candidate.content_hash if snapshot.candidate else None,
            "trial_contract_ref": context["trial_contract_ref"],
            "trial_contract_hash": context["trial_contract_hash"],
            "budget": _copy(snapshot.budget),
            "performance_accessed": True,
            "final_test_access": {"physical": 0, "analytical": 0, "decision": 0},
            "confirmation_required": True,
            "reason_zh": "工程物化问题已修复；可在同一 Trial、同一合同和同一已消耗预算槽位上申请恢复。",
        }

    def resume_preview(self, objective_id: str) -> dict[str, Any]:
        """Build a full read-only, data-backed recovery preview."""

        objective_id = self._safe_id(objective_id, "objective_id")
        context = self._load_recovery_context(objective_id)
        snapshot = context["snapshot"]
        record = context["record"]
        materialization = self._event_materialization_audit(context)
        if materialization["status"] != "PASS":
            raise PredictiveTrialStartError(
                "PREDICTIVE_TRIAL_RECOVERY_EVENT_MATERIALIZATION_FAILED",
                "修复后的 canonical 事件仍未完成物化，恢复被拒绝",
                details={"event_materialization": materialization},
            )
        plan = {
            "action": RESUME_PREDICTIVE_TRIAL_1,
            "objective_id": objective_id,
            "trial_number": 1,
            "trial_id": record.get("trial_id"),
            "start_intent_id": context["intent"].get("intent_id"),
            "candidate_id": snapshot.candidate_id,
            "candidate_hash": snapshot.candidate_hash,
            "candidate_contract_content_hash": snapshot.candidate.content_hash if snapshot.candidate else None,
            "trial_contract_ref": context["trial_contract_ref"],
            "trial_contract_hash": context["trial_contract_hash"],
            "multiple_testing_registration_ref": context["registration_ref"],
            "multiple_testing_registration_hash": stable_hash(context["registration"]),
            "budget_reservation_identity": context["reservation_id"],
            "budget": _copy(snapshot.budget),
            "budget_after_resume": {"used": _int(snapshot.budget.get("used")), "reserved": _int(snapshot.budget.get("reserved")), "remaining": _int(snapshot.budget.get("remaining"))},
            "structural": _copy(snapshot.structural),
            "event_materialization": materialization,
            "failure_stage": "EVENT_MATERIALIZATION",
            "failure_object": "BENCHMARK_SENTIMENT_BREAKOUT_EVENT_V1",
            "contract_semantics_changed": False,
            "candidate_identity_changed": False,
            "new_trial_created": False,
            "budget_reconsumed": False,
            "multiple_testing_reregistered": False,
            "final_test_access": {"physical": 0, "analytical": 0, "decision": 0},
            "prospective": "DISABLED",
            "real_order": "DISABLED",
            "confirmation_required": True,
        }
        preview_hash = stable_hash(plan)
        token = stable_hash({"action": RESUME_PREDICTIVE_TRIAL_1, "preview_hash": preview_hash, "trial_id": record.get("trial_id"), "candidate_hash": snapshot.candidate_hash})
        return {
            "schema_version": RESUME_PREVIEW_SCHEMA,
            "status": "READY",
            "status_zh": "可以确认同一 Trial 恢复",
            "available": True,
            "objective_id": objective_id,
            "action": RESUME_PREDICTIVE_TRIAL_1,
            "action_zh": RESUME_ACTION_ZH,
            "candidate_id": snapshot.candidate_id,
            "candidate_hash": snapshot.candidate_hash,
            "trial_id": record.get("trial_id"),
            "start_intent_id": context["intent"].get("intent_id"),
            "trial_number": 1,
            "budget": _copy(snapshot.budget),
            "preview": plan,
            "preview_hash": preview_hash,
            "confirmation_token": token,
            "confirmation_required": True,
            "reason_zh": "第 1 次预测试验曾因工程事件物化问题未完成；策略表现没有被判定为失败。确认后只恢复该 Trial，不创建 Trial #2、不再次扣预算。",
            "reasons": [],
            "generated_at": self._clock(),
        }

    def _resolve_budget_path(self, objective_id: str, checkpoint: Any) -> Path | None:
        ref = str((checkpoint.budget_view or {}).get("registry_path") or "")
        if ref:
            candidate = (self.root / ref).resolve()
            if candidate.is_relative_to(self.root) and candidate.is_file():
                try:
                    payload = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    payload = {}
                if str(payload.get("objective_id")) == objective_id:
                    return candidate
        matches: list[Path] = []
        for candidate in sorted((self.root / "data/research/research_factory/batches").glob("*/search_budget_registry.json")):
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, Mapping) and str(payload.get("objective_id")) == objective_id:
                matches.append(candidate)
        return matches[-1] if matches else None

    def _load_contract(self, checkpoint: Any, candidate_id: str, candidate_hash: str) -> tuple[DurableFrozenCandidateContractV1 | None, str, list[str]]:
        last = dict(checkpoint.last_completed_candidate or {})
        contract_ref = str(last.get("contract_ref") or "")
        reasons: list[str] = []
        if not contract_ref:
            return None, contract_ref, ["当前 Candidate 缺少冻结合同引用"]
        contract_path = (self.root / contract_ref).resolve()
        if not contract_path.is_relative_to(self.root) or not contract_path.is_file():
            return None, contract_ref, ["当前 Candidate 的冻结合同不存在或不在项目范围内"]
        try:
            registry = DurableFrozenCandidateContractRegistryV1.read(contract_path)
            matches = [item for item in registry.items() if item.candidate_id == candidate_id and item.candidate_hash == candidate_hash]
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
            return None, contract_ref, [f"冻结合同无法完成 canonical 校验：{type(exc).__name__}"]
        if len(matches) != 1:
            reasons.append("冻结合同与当前 Candidate 身份不唯一匹配")
            return None, contract_ref, reasons
        try:
            record = matches[0].reconstruct_candidate()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            reasons.append(f"冻结 Candidate 无法安全重建：{type(exc).__name__}")
            return None, contract_ref, reasons
        if not record.phase4_eligible:
            reasons.append("冻结 Candidate 不满足预测验证资格")
        if str(last.get("candidate_id") or "") != candidate_id or str(last.get("candidate_hash") or "") != candidate_hash:
            reasons.append("checkpoint 中的当前 Candidate 身份与授权不一致")
        return matches[0], contract_ref, reasons

    @staticmethod
    def _reservation_id(objective_id: str, batch_id: str, family_id: str, candidate_id: str) -> str:
        identity = {
            "objective_id": objective_id,
            "batch_id": batch_id,
            "family_id": family_id,
            "candidate_id": candidate_id,
        }
        return f"TRIAL-{stable_hash(identity)[:20]}"

    def _family_path(self, objective_id: str, family_id: str) -> Path:
        return self.root / "data/research/research_factory/multiple_testing" / objective_id / f"{family_id}.json"

    def _snapshot(self, objective_id: str, *, allow_inflight: bool = False, allow_recovery: bool = False) -> _StartSnapshot:
        objective = self._objective(objective_id)
        store = self._store(objective_id)
        checkpoint = store.load()
        if checkpoint is None:
            raise PredictiveTrialStartError("DAEMON_CHECKPOINT_NOT_FOUND", "当前 objective 缺少 canonical daemon checkpoint", status_code=503)
        rows = self._authorization_rows(objective_id)
        authorization = rows[-1] if rows else None
        refs = dict(checkpoint.canonical_refs or {})
        structural_result = dict(refs.get("last_structural_result") or {})
        structural_details = dict(structural_result.get("details") or {})
        identity_details: Mapping[str, Any] = structural_details
        for key in ("v2_result", "v1_result"):
            nested = structural_details.get(key) if isinstance(structural_details.get(key), Mapping) else {}
            if nested.get("candidate_id"):
                identity_details = nested
                break
        governance_path = self.root / "reports/research_orchestrator_v2" / objective_id / "structural_governance_decision_required.json"
        governance: Mapping[str, Any] = {}
        if governance_path.is_file():
            try:
                loaded_governance = json.loads(governance_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise PredictiveTrialStartError("PREDICTIVE_GOVERNANCE_SOURCE_UNREADABLE", "预测治理状态暂时不可读", status_code=503) from exc
            if isinstance(loaded_governance, Mapping):
                governance = loaded_governance
        candidate_id = str((authorization or {}).get("candidate_id") or governance.get("candidate_id") or identity_details.get("candidate_id") or "")
        candidate_hash = str((authorization or {}).get("candidate_hash") or governance.get("candidate_hash") or identity_details.get("candidate_hash") or "")
        structural = dict((authorization or {}).get("structural") or governance.get("structural") or {})
        if not structural:
            structural = {
                "status": (authorization or {}).get("structural_status") or structural_result.get("status"),
                "lower_bound": (authorization or {}).get("lower_bound"),
                "upper_bound": (authorization or {}).get("upper_bound"),
                "minimum_required": (authorization or {}).get("minimum_required"),
                "lower_bound_integrity": (authorization or {}).get("lower_bound_integrity"),
            }
        reconciliation = dict(refs.get("structural_reconciliation") or {})
        reconciliation_id = str((authorization or {}).get("structural_reconciliation_id") or governance.get("reconciliation_id") or reconciliation.get("reconciliation_id") or "")
        reconciliation = {**reconciliation, "reconciliation_id": reconciliation_id}
        contract, contract_ref, contract_reasons = self._load_contract(checkpoint, candidate_id, candidate_hash)
        policy = None
        policy_hash = ""
        policy_path: Path | None = None
        policy_reasons: list[str] = []
        if contract is not None:
            try:
                executor = CanonicalPredictiveExecutorV1(self.root, objective_id)
                policy, policy_hash, policy_path = executor._load_policy(contract)
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                policy_reasons.append(f"Validation Policy 无法完成 canonical pin 校验：{type(exc).__name__}")
        budget_path = self._resolve_budget_path(objective_id, checkpoint)
        budget: dict[str, Any] = {"objective_id": objective_id, "total": 0, "used": 0, "reserved": 0, "remaining": 0, "registry_path": None, "registry_head_hash": None}
        budget_reasons: list[str] = []
        batch_id = ""
        family_id = str(objective.get("multiple_testing_family_id") or (contract.mechanism if contract else ""))
        decision_family_id = ""
        if budget_path is None:
            budget_reasons.append("canonical SearchBudgetRegistry 不存在")
        else:
            try:
                registry = SearchBudgetRegistryV1(objective_id, budget_path)
                snapshot = registry.snapshot()
                objective_bucket = next((item for item in snapshot.get("buckets", ()) if item.get("kind") == "objective" and item.get("key") == objective_id), None)
                if objective_bucket is None:
                    budget_reasons.append("canonical objective 预算 bucket 不存在")
                else:
                    budget = {"objective_id": objective_id, "total": _int(objective_bucket.get("limit")), "used": _int(objective_bucket.get("used")), "reserved": _int(objective_bucket.get("reserved")), "remaining": _int(objective_bucket.get("remaining")), "registry_path": budget_path.relative_to(self.root).as_posix(), "registry_head_hash": registry.head_hash}
                if snapshot.get("buckets"):
                    try:
                        executor = CanonicalPredictiveExecutorV1(self.root, objective_id)
                        batch_id = executor._budget_binding(snapshot, "batch", str((checkpoint.last_completed_candidate or {}).get("batch_id") or (contract.source_provenance.get("batch_id") if contract else "")))
                        family_id = executor._budget_binding(snapshot, "family", family_id)
                    except (RuntimeError, KeyError) as exc:
                        budget_reasons.append(f"预算 batch/family 绑定不唯一：{type(exc).__name__}")
                if policy is not None and batch_id:
                    decision_family_id = policy.family_id(objective_id, batch_id)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                budget_reasons.append(f"canonical SearchBudgetRegistry 无法安全读取：{type(exc).__name__}")
        family_path = self._family_path(objective_id, family_id) if family_id else None
        family_reasons: list[str] = []
        if family_path is None or not family_path.is_file():
            family_reasons.append("当前 Candidate 没有可绑定的 Multiple Testing Family")
        else:
            try:
                family_payload = json.loads(family_path.read_text(encoding="utf-8"))
                if str(family_payload.get("objective_id")) != objective_id or str(family_payload.get("family_id")) != family_id:
                    family_reasons.append("Multiple Testing Family 身份与当前 objective 不一致")
                if family_payload.get("immutable_after_confirmation") is not True:
                    family_reasons.append("Multiple Testing Family 没有 immutable 约束")
            except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
                family_reasons.append(f"Multiple Testing Family 无法安全读取：{type(exc).__name__}")
        trial_records = self._trial_records(objective_id)
        final_test_raw = (authorization or {}).get("final_test_access") or governance.get("final_test_access") or {}
        final_test_access = {key: _int(final_test_raw.get(key)) for key in ("analytical", "decision", "physical")}
        guard = ResearchDataAccessGuard()
        final_test_boundary = {"research_end": int(policy.research_end) if policy is not None else None, "final_test_start": int(guard.final_test_start), "source": "ResearchDataAccessGuard + ValidationDecisionPolicyV2"}
        prospective = str((authorization or {}).get("prospective") or governance.get("prospective") or (objective.get("risk_constraints") or {}).get("prospective_access") or "UNKNOWN")
        real_order = str((authorization or {}).get("real_order") or governance.get("real_order") or (objective.get("risk_constraints") or {}).get("real_order_execution") or "UNKNOWN")
        reasons: list[str] = []
        if authorization is None or str(authorization.get("decision_status")) != "AUTHORIZED":
            reasons.append("当前没有 AUTHORIZED 的预测治理授权")
        elif not authorization.get("decision_hash") or stable_hash({key: value for key, value in authorization.items() if key != "decision_hash"}) != str(authorization.get("decision_hash")):
            reasons.append("预测治理授权 decision hash 校验失败")
        if str((authorization or {}).get("decision_type") or "") != AUTHORIZE_FIRST_PREDICTIVE_TRIAL:
            reasons.append("当前授权不是第 1 次预测试验授权")
        if str((authorization or {}).get("next_action") or "") != START_PREDICTIVE_TRIAL_1:
            reasons.append("canonical next_action 不是 START_PREDICTIVE_TRIAL_1")
        if authorization is not None and str(authorization.get("structural_reconciliation_id") or "") != reconciliation_id:
            reasons.append("预测治理授权与当前结构 reconciliation 不一致")
        allowed_states = {ResearchDaemonState.READY.value, ResearchDaemonState.STRUCTURAL_PASS.value}
        if allow_inflight:
            allowed_states |= {ResearchDaemonState.PREDICTIVE_PENDING.value, ResearchDaemonState.PREDICTIVE_RUNNING.value}
        if allow_recovery:
            allowed_states |= {ResearchDaemonState.ENGINEERING_BLOCKED.value}
        if checkpoint.current_state not in allowed_states:
            reasons.append(f"daemon 当前状态不允许启动预测试验：{checkpoint.current_state}")
        allowed_actions = {START_PREDICTIVE_TRIAL_1, "PREDICTIVE_TRIAL_RUN_IN_PROGRESS"}
        if allow_recovery:
            allowed_actions.add("PREDICTIVE_TRIAL_FAILURE_REVIEW")
        if str(checkpoint.required_action or "") not in allowed_actions:
            reasons.append(f"当前 required_action 不允许启动预测试验：{checkpoint.required_action}")
        if str(structural.get("status") or "").upper() != "PASS" or str((authorization or {}).get("structural_status") or "PASS").upper() != "PASS":
            reasons.append("结构预检不是 PASS")
        lower = _int(structural.get("lower_bound"), -1)
        upper = _int(structural.get("upper_bound"), -1)
        minimum = _int(structural.get("minimum_required"), -1)
        integrity = structural.get("lower_bound_integrity")
        if isinstance(integrity, Mapping):
            integrity = integrity.get("status")
        if lower < 0 or upper < 0 or minimum < 0 or lower < minimum or str(integrity or "").upper() != "PASS":
            reasons.append("结构样本边界或 lower-bound integrity 未通过")
        if not candidate_id or not candidate_hash:
            reasons.append("当前授权缺少 Candidate 身份")
        if str(identity_details.get("candidate_id") or candidate_id) != candidate_id or str(identity_details.get("candidate_hash") or candidate_hash) != candidate_hash:
            reasons.append("结构证据与授权 Candidate 身份不一致")
        last_candidate = dict(checkpoint.last_completed_candidate or {})
        if str(last_candidate.get("candidate_id") or "") != candidate_id or str(last_candidate.get("candidate_hash") or "") != candidate_hash:
            reasons.append("checkpoint 中没有当前已冻结 Candidate")
        reasons.extend(contract_reasons)
        reasons.extend(policy_reasons)
        if checkpoint.current_candidate is not None and str((checkpoint.current_candidate or {}).get("candidate_hash") or candidate_hash) != candidate_hash:
            reasons.append("checkpoint 当前 Candidate hash 已变化")
        if trial_records and not allow_recovery:
            reasons.append("当前 objective 已存在 Trial 记录")
        if allow_recovery:
            if _int(budget.get("used")) < 1 or _int(budget.get("reserved")) != 0:
                reasons.append("工程恢复要求既有预算已消耗且没有活动预留")
        elif _int(budget.get("remaining")) < 1 or _int(budget.get("reserved")) != 0:
            reasons.append("当前预测预算不可用或已有预留")
        if final_test_access != {"analytical": 0, "decision": 0, "physical": 0}:
            reasons.append("Final Test 已不是关闭状态")
        if policy is not None and (not bool(policy.no_final_test_access) or int(policy.research_end) >= int(guard.final_test_start)):
            reasons.append("Validation Policy 的 Final Test 边界不安全")
        if prospective != "DISABLED":
            reasons.append("Prospective 不是 DISABLED")
        if real_order != "DISABLED":
            reasons.append("Real Order 不是 DISABLED")
        if str(checkpoint.ai_auto_invocation or "DISABLED") != "DISABLED":
            reasons.append("AI 自动调用不是 DISABLED")
        reasons.extend(budget_reasons)
        reasons.extend(family_reasons)
        if policy is not None and decision_family_id == "":
            reasons.append("Multiple Testing decision family 无法确定")
        return _StartSnapshot(
            objective=objective,
            checkpoint=checkpoint,
            authorization=authorization,
            candidate=contract,
            candidate_id=candidate_id,
            candidate_hash=candidate_hash,
            contract_ref=contract_ref,
            reconciliation=reconciliation,
            structural=structural,
            policy=policy,
            policy_hash=policy_hash,
            policy_path=policy_path,
            budget_path=budget_path,
            budget=budget,
            batch_id=batch_id,
            family_id=family_id,
            decision_family_id=decision_family_id,
            family_path=family_path,
            trial_records=trial_records,
            final_test_access=final_test_access,
            final_test_boundary=final_test_boundary,
            prospective=prospective,
            real_order=real_order,
            reasons=tuple(dict.fromkeys(reasons)),
        )

    def _intent_path(self, objective_id: str) -> Path:
        return self.root / "reports/research_daemon" / objective_id / START_INTENTS_FILENAME

    def _load_intents(self, objective_id: str) -> dict[str, dict[str, Any]]:
        path = self._intent_path(objective_id)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_START_INTENTS_INVALID", "预测试验启动意图账本无法安全读取", status_code=503) from exc
        if not isinstance(payload, Mapping) or payload.get("schema_version") != START_INTENT_SCHEMA or str(payload.get("objective_id")) != objective_id:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_START_INTENTS_INVALID", "预测试验启动意图账本身份不受支持", status_code=503)
        intents = payload.get("intents", {})
        if not isinstance(intents, Mapping):
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_START_INTENTS_INVALID", "预测试验启动意图账本格式不受支持", status_code=503)
        return {str(key): dict(value) for key, value in intents.items() if isinstance(value, Mapping)}

    def _save_intents(self, objective_id: str, intents: Mapping[str, Mapping[str, Any]]) -> None:
        path = self._intent_path(objective_id)
        _atomic_write(path, {"schema_version": START_INTENT_SCHEMA, "objective_id": objective_id, "intents": intents, "ledger_hash": stable_hash(intents)})

    def _put_intent(self, objective_id: str, intents: dict[str, dict[str, Any]], intent: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(intent)
        item["intent_hash"] = stable_hash({key: value for key, value in item.items() if key != "intent_hash"})
        intents[str(item["intent_id"])] = item
        self._save_intents(objective_id, intents)
        return item

    def _update_intent(self, objective_id: str, intents: dict[str, dict[str, Any]], intent_id: str, **changes: Any) -> dict[str, Any]:
        if intent_id not in intents:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_START_INTENT_NOT_FOUND", "未找到预测试验启动意图")
        item = {**intents[intent_id], **changes, "updated_at": self._clock()}
        return self._put_intent(objective_id, intents, item)

    def _contract_payload(self, snapshot: _StartSnapshot, intent: Mapping[str, Any]) -> dict[str, Any]:
        assert snapshot.candidate is not None
        contract = snapshot.candidate
        policy = snapshot.policy
        data_snapshot = {
            "source": "frozen_candidate_contract+structural_reconciliation",
            "candidate_contract_ref": snapshot.contract_ref,
            "candidate_contract_content_hash": contract.content_hash,
            "structural_reconciliation_ref": snapshot.reconciliation.get("report_ref"),
            "structural_reconciliation_id": snapshot.reconciliation.get("reconciliation_id"),
            "research_period_identity": _copy(contract.research_period_identity),
            "factor_event_registry_identities": _copy(contract.factor_event_registry_identities),
            "pit_dependencies": _copy(contract.pit_dependencies),
            "outcome_data_loaded": False,
            "performance_data_loaded": False,
            "performance_access": 0,
            "final_test_access": {"physical": 0, "analytical": 0, "decision": 0},
        }
        data_snapshot["snapshot_hash"] = stable_hash(data_snapshot)
        policy_payload = {
            "policy_id": getattr(policy, "policy_id", None),
            "policy_version": getattr(policy, "policy_version", None),
            "policy_hash": snapshot.policy_hash,
            "policy_ref": snapshot.policy_path.relative_to(self.root).as_posix() if snapshot.policy_path else None,
            "research_start": getattr(policy, "research_start", None),
            "research_end": getattr(policy, "research_end", None),
            "final_test_start": snapshot.final_test_boundary.get("final_test_start"),
            "bootstrap_iterations": getattr(policy, "bootstrap_iterations", None),
            "bootstrap_seed": getattr(policy, "bootstrap_seed", None),
            "fdr_method": getattr(policy, "fdr_method", None),
            "fdr_q": getattr(policy, "fdr_q", None),
            "multiple_testing_contract": _copy(getattr(policy, "multiple_testing_contract", {})),
            "gates": _copy(getattr(policy, "gates", {})),
            "no_final_test_access": getattr(policy, "no_final_test_access", None),
            "no_parameter_optimization": getattr(policy, "no_parameter_optimization", None),
            "no_threshold_changes": getattr(policy, "no_threshold_changes", None),
        }
        execution_contract = {
            "candidate_semantics": _copy(contract.full_semantic_record),
            "signal_semantics": _copy(contract.entry_predicate),
            "factor_semantics": {"factor_ids": list(contract.factor_ids), "factor_roles": _copy(contract.factor_roles), "factor_directions": _copy(contract.factor_directions)},
            "event_semantics": {"event_ids": list(contract.event_ids), "event_timing_semantics": _copy(contract.event_timing_semantics)},
            "ranking": _copy(contract.ranking_semantics),
            "selection": _copy(contract.selection_rule),
            "top_n": int(contract.top_n),
            "max_positions": int(contract.max_positions),
            "entry": _copy(contract.entry_timing),
            "exit": _copy(contract.exit_contract),
            "holding_period_trading_sessions": int(contract.holding_period_trading_sessions),
            "fees_and_slippage": _copy(contract.fee_slippage_contract_references),
            "execution_contract_version": contract.execution_contract_version,
            "t_plus_1": _copy(contract.t_plus_1_contract),
            "pit": _copy(contract.pit_dependencies),
            "capital_product": _copy(contract.capital_product_contract_identity),
        }
        benchmark = contract.full_semantic_record.get("benchmark") if isinstance(contract.full_semantic_record, Mapping) else None
        if not benchmark:
            benchmark = {
                "dataset_id": "benchmark_index_daily",
                "source": "frozen_candidate_factor_event_registry",
                "contract": _copy(contract.factor_event_registry_identities),
            }
        payload = {
            "schema_version": TRIAL_CONTRACT_SCHEMA,
            "immutable": True,
            "trial_id": intent["trial_id"],
            "trial_number": 1,
            "objective_id": snapshot.objective.get("objective_id"),
            "objective_hash": snapshot.objective.get("objective_identity_hash") or snapshot.objective.get("objective_hash"),
            "candidate_id": snapshot.candidate_id,
            "candidate_hash": snapshot.candidate_hash,
            "candidate_contract_ref": snapshot.contract_ref,
            "candidate_contract_content_hash": contract.content_hash,
            "authorization": {
                "authorization_decision_id": snapshot.authorization.get("decision_id") if snapshot.authorization else None,
                "authorization_decision_hash": snapshot.authorization.get("decision_hash") if snapshot.authorization else None,
                "authorization_id": snapshot.authorization.get("authorization_id") if snapshot.authorization else None,
                "governance_decision_id": snapshot.authorization.get("governance_decision_id") if snapshot.authorization else None,
                "decision_status": snapshot.authorization.get("decision_status") if snapshot.authorization else None,
            },
            "structural_reconciliation": {"reconciliation_id": snapshot.reconciliation.get("reconciliation_id"), "status": snapshot.reconciliation.get("status"), "report_ref": snapshot.reconciliation.get("report_ref")},
            "structural": _copy(snapshot.structural),
            "validation_policy": policy_payload,
            "multiple_testing": {
                "family_id": snapshot.family_id,
                "decision_family_id": snapshot.decision_family_id,
                "trial_number": 1,
                "family_definition_ref": snapshot.family_path.relative_to(self.root).as_posix() if snapshot.family_path else None,
                "registration_ref": self._multiple_testing_registration_path(snapshot).relative_to(self.root).as_posix(),
                "candidate_identity": {"candidate_id": snapshot.candidate_id, "candidate_hash": snapshot.candidate_hash},
            },
            "execution_contract": execution_contract,
            "benchmark": _copy(benchmark),
            "research_window": _copy(contract.research_period_identity),
            "data_snapshot": data_snapshot,
            "deterministic_seed": int(getattr(policy, "bootstrap_seed", 0) or 0) + 1,
            "start_intent_id": intent["intent_id"],
            "created_at": intent["created_at"],
        }
        payload["trial_contract_hash"] = stable_hash(payload)
        return payload

    def _trial_contract_path(self, snapshot: _StartSnapshot, trial_id: str) -> Path:
        self._safe_id(snapshot.batch_id, "batch_id")
        self._safe_id(snapshot.candidate_id, "candidate_id")
        return self.root / "reports/research_daemon" / str(snapshot.objective["objective_id"]) / "predictive" / "trial_contracts" / f"{trial_id}.json"

    def _multiple_testing_registration_path(self, snapshot: _StartSnapshot) -> Path:
        assert snapshot.family_path is not None
        return snapshot.family_path.with_name(snapshot.family_path.stem + MULTIPLE_TESTING_REGISTRATIONS_SUFFIX)

    def _write_immutable_contract(self, path: Path, payload: Mapping[str, Any]) -> None:
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise PredictiveTrialStartError("TRIAL_CONTRACT_UNREADABLE", "Trial Contract 无法安全读取", status_code=503) from exc
            if stable_hash(existing) != stable_hash(payload):
                raise PredictiveTrialStartError("TRIAL_CONTRACT_IDENTITY_CONFLICT", "同一 Trial ID 对应了不同的 immutable Trial Contract")
            return
        _atomic_write(path, payload)

    def _preview_plan(self, snapshot: _StartSnapshot) -> dict[str, Any]:
        trial_id = f"{snapshot.batch_id}_{snapshot.candidate_hash}_T001" if snapshot.batch_id else ""
        reservation_id = self._reservation_id(str(snapshot.objective.get("objective_id")), snapshot.batch_id, snapshot.family_id, snapshot.candidate_id) if snapshot.batch_id and snapshot.family_id else ""
        benchmark = snapshot.candidate.full_semantic_record.get("benchmark") if snapshot.candidate and isinstance(snapshot.candidate.full_semantic_record, Mapping) else None
        if not benchmark and snapshot.candidate:
            benchmark = {"dataset_id": "benchmark_index_daily", "source": "frozen_candidate_factor_event_registry", "contract": _copy(snapshot.candidate.factor_event_registry_identities)}
        return {
            "action": START_PREDICTIVE_TRIAL_1,
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
            "governance_decision_id": snapshot.authorization.get("governance_decision_id") if snapshot.authorization else None,
            "structural_reconciliation_id": snapshot.reconciliation.get("reconciliation_id"),
            "structural": _copy(snapshot.structural),
            "trial_number": 1,
            "trial_id": trial_id,
            "budget_reservation_identity": reservation_id,
            "budget": _copy(snapshot.budget),
            "multiple_testing": {"family_id": snapshot.family_id, "decision_family_id": snapshot.decision_family_id, "trial_number": 1},
            "validation_policy": {"policy_id": getattr(snapshot.policy, "policy_id", None), "policy_version": getattr(snapshot.policy, "policy_version", None), "policy_hash": snapshot.policy_hash},
            "execution_contract_version": snapshot.candidate.execution_contract_version if snapshot.candidate else None,
            "benchmark": _copy(benchmark or {}),
            "research_window": _copy(snapshot.candidate.research_period_identity if snapshot.candidate else {}),
            "data_snapshot": {"source": "frozen_candidate_contract+structural_reconciliation", "performance_data_loaded": False, "final_test_access": {"physical": 0, "analytical": 0, "decision": 0}},
            "final_test_access": _copy(snapshot.final_test_access),
            "final_test_boundary": _copy(snapshot.final_test_boundary),
            "prospective": snapshot.prospective,
            "real_order": snapshot.real_order,
            "trial_count_before": len(snapshot.trial_records),
            "performance_access_before_start": 0,
            "confirmation_required": True,
        }

    def _preview_payload(self, snapshot: _StartSnapshot, *, require_eligible: bool) -> dict[str, Any]:
        if require_eligible and not snapshot.eligible:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_START_UNAVAILABLE", "当前不满足安全启动第 1 次预测试验的全部条件", details={"reasons": list(snapshot.reasons)})
        plan = self._preview_plan(snapshot)
        preview_hash = stable_hash(plan)
        token = stable_hash({"action": START_PREDICTIVE_TRIAL_1, "preview_hash": preview_hash, "objective_id": snapshot.objective.get("objective_id"), "candidate_hash": snapshot.candidate_hash})
        return {
            "schema_version": START_PREVIEW_SCHEMA,
            "status": "READY" if snapshot.eligible else "UNAVAILABLE",
            "status_zh": "可以确认启动" if snapshot.eligible else "当前不可启动",
            "available": snapshot.eligible,
            "objective_id": snapshot.objective.get("objective_id"),
            "action": START_PREDICTIVE_TRIAL_1,
            "action_zh": START_ACTION_ZH,
            "candidate_id": snapshot.candidate_id,
            "candidate_hash": snapshot.candidate_hash,
            "authorization_decision_id": snapshot.authorization.get("decision_id") if snapshot.authorization else None,
            "authorization_decision_hash": snapshot.authorization.get("decision_hash") if snapshot.authorization else None,
            "structural_reconciliation_id": snapshot.reconciliation.get("reconciliation_id"),
            "structural": _copy(snapshot.structural),
            "budget": _copy(snapshot.budget),
            "trial_count": len(snapshot.trial_records),
            "candidate_state": "FROZEN",
            "confirmation_required": True,
            "preview": plan,
            "preview_hash": preview_hash,
            "confirmation_token": token,
            "final_test_access": _copy(snapshot.final_test_access),
            "final_test_boundary": _copy(snapshot.final_test_boundary),
            "prospective": snapshot.prospective,
            "real_order": snapshot.real_order,
            "reason_zh": "启动后系统将首次访问当前候选的预测验证结果，并预留 1 次预测试验预算。不会访问最终测试集，不会修改候选策略，不会进行真实交易。" if snapshot.eligible else (snapshot.reasons[0] if snapshot.reasons else "当前不可启动"),
            "reasons": list(snapshot.reasons),
            "generated_at": self._clock(),
        }

    def preview(self, objective_id: str) -> dict[str, Any]:
        objective_id = self._safe_id(objective_id, "objective_id")
        snapshot = self._snapshot(objective_id)
        return self._preview_payload(snapshot, require_eligible=True)

    def readiness(self, objective_id: str) -> dict[str, Any]:
        objective_id = self._safe_id(objective_id, "objective_id")
        try:
            snapshot = self._snapshot(objective_id)
            return self._preview_payload(snapshot, require_eligible=False)
        except PredictiveTrialStartError as exc:
            return {
                "schema_version": START_PREVIEW_SCHEMA,
                "status": "UNAVAILABLE",
                "status_zh": "当前不可启动",
                "available": False,
                "objective_id": objective_id,
                "action": START_PREDICTIVE_TRIAL_1,
                "action_zh": START_ACTION_ZH,
                "confirmation_required": True,
                "reason_zh": exc.message_zh,
                "reason_code": exc.code,
                "reasons": [exc.message_zh],
            }

    def _ensure_request(self, body: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
        if body.get("confirmed") is not True:
            raise PredictiveTrialStartError("CONFIRMATION_REQUIRED", "启动预测试验需要在确认页中明确确认", status_code=400)
        action = str(body.get("action") or "").strip().upper()
        if action != START_PREDICTIVE_TRIAL_1:
            raise PredictiveTrialStartError("CANONICAL_START_ACTION_REQUIRED", "请求必须明确使用 START_PREDICTIVE_TRIAL_1", status_code=400)
        intent_id = self._safe_id(body.get("start_intent_id") or body.get("idempotency_key"), "start_intent_id")
        candidate_id = self._safe_id(body.get("candidate_id"), "candidate_id")
        candidate_hash = str(body.get("candidate_hash") or "")
        preview_hash = str(body.get("preview_hash") or "")
        token = str(body.get("confirmation_token") or "")
        if not candidate_hash or not preview_hash or not token:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_START_PREVIEW_REQUIRED", "确认前必须先读取当前预测试验启动预览", status_code=400)
        return action, intent_id, candidate_id, candidate_hash, preview_hash, token

    def _ensure_resume_request(self, body: Mapping[str, Any]) -> tuple[str, str, str, str, str, str, str]:
        if body.get("confirmed") is not True:
            raise PredictiveTrialStartError("CONFIRMATION_REQUIRED", "恢复预测试验需要在确认页中明确确认", status_code=400)
        action = str(body.get("action") or "").strip().upper()
        if action != RESUME_PREDICTIVE_TRIAL_1:
            raise PredictiveTrialStartError("CANONICAL_RESUME_ACTION_REQUIRED", "请求必须明确使用 RESUME_PREDICTIVE_TRIAL_1", status_code=400)
        intent_id = self._safe_id(body.get("start_intent_id"), "start_intent_id")
        trial_id = self._safe_id(body.get("trial_id"), "trial_id")
        candidate_id = self._safe_id(body.get("candidate_id"), "candidate_id")
        candidate_hash = str(body.get("candidate_hash") or "")
        preview_hash = str(body.get("preview_hash") or "")
        token = str(body.get("confirmation_token") or "")
        if not candidate_hash or not preview_hash or not token:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RESUME_PREVIEW_REQUIRED", "确认前必须先读取当前 Trial 恢复预览", status_code=400)
        return action, intent_id, trial_id, candidate_id, candidate_hash, preview_hash, token

    def _validate_existing_intent(self, intent: Mapping[str, Any], *, action: str, candidate_id: str, candidate_hash: str, preview_hash: str, token: str) -> None:
        if str(intent.get("action")) != action or str(intent.get("candidate_id")) != candidate_id or str(intent.get("candidate_hash")) != candidate_hash or str(intent.get("preview_hash")) != preview_hash or str(intent.get("confirmation_token_hash")) != stable_hash(token):
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_START_IDEMPOTENCY_CONFLICT", "相同启动意图对应了不同的 Candidate、Hash 或预览", details={"start_intent_id": intent.get("intent_id")})

    def _trial_ledger(self, snapshot: _StartSnapshot) -> ResearchFactoryTrialLedgerFacadeV1:
        if snapshot.budget_path is None:
            raise PredictiveTrialStartError("TRIAL_LEDGER_PATH_UNAVAILABLE", "无法确定 canonical Trial ledger 路径", status_code=503)
        batch_dir = snapshot.budget_path.parent
        return ResearchFactoryTrialLedgerFacadeV1(trial_registry=TrialRegistryV1(batch_dir / "trial_registry.json"), path=batch_dir / "factory_trial_ledger.json")

    def _candidate_work(self, snapshot: _StartSnapshot, intent: Mapping[str, Any]) -> Any:
        from ..research_daemon import CandidateWork

        return CandidateWork(
            candidate_id=snapshot.candidate_id,
            candidate_hash=snapshot.candidate_hash,
            contract_ref=snapshot.contract_ref,
            batch_id=str((snapshot.checkpoint.last_completed_candidate or {}).get("batch_id") or ""),
            mechanism=snapshot.candidate.mechanism if snapshot.candidate else "",
            metadata={
                "raw_contract": snapshot.candidate.to_dict() if snapshot.candidate else {},
                "start_intent_id": intent["intent_id"],
                "trial_id": intent.get("trial_id"),
                "trial_contract_ref": intent.get("trial_contract_ref"),
                "trial_contract_hash": intent.get("trial_contract_hash"),
            },
        )

    def _trial_record(self, snapshot: _StartSnapshot, intent: Mapping[str, Any]) -> FactoryTrialRecordV1:
        assert snapshot.candidate is not None
        return FactoryTrialRecordV1(
            trial_id=str(intent["trial_id"]),
            objective_id=str(snapshot.objective["objective_id"]),
            batch_id=snapshot.batch_id,
            family_id=snapshot.family_id,
            hypothesis_id=snapshot.candidate.hypothesis_id,
            candidate_id=snapshot.candidate_id,
            candidate_hash=snapshot.candidate_hash,
            dataset_hash=str(intent["data_snapshot_hash"]),
            validation_policy_hash=snapshot.policy_hash,
            engine_hash=str(intent["engine_hash"]),
            seed=int(intent["seed"]),
            lineage={
                "source": "FORMAL_PREDICTIVE_TRIAL_START_ENTRY_V1",
                "canonical_start_action": START_PREDICTIVE_TRIAL_1,
                "start_intent_id": intent["intent_id"],
                "trial_contract_ref": intent["trial_contract_ref"],
                "trial_contract_hash": intent["trial_contract_hash"],
                "authorization_decision_id": snapshot.authorization.get("decision_id") if snapshot.authorization else None,
                "authorization_decision_hash": snapshot.authorization.get("decision_hash") if snapshot.authorization else None,
                "structural_reconciliation_id": snapshot.reconciliation.get("reconciliation_id"),
                "structural_status": "PASS",
                "lower_bound": snapshot.structural.get("lower_bound"),
                "upper_bound": snapshot.structural.get("upper_bound"),
                "minimum_required": snapshot.structural.get("minimum_required"),
                "validation_policy_version": getattr(snapshot.policy, "policy_version", None),
                "decision_family_id": snapshot.decision_family_id,
                "multiple_testing_family_id": snapshot.family_id,
                "multiple_testing_registration_ref": intent["multiple_testing_registration_ref"],
                "final_test_access": {"physical": 0, "analytical": 0, "decision": 0},
                "prospective": "DISABLED",
                "real_order": "DISABLED",
            },
            budget_reservation_identity=str(intent["reservation_id"]),
            stage="TRIAL_CREATED",
            started_at=None,
            last_activity_at=self._clock(),
            start_intent_id=str(intent["intent_id"]),
        )

    def _running_checkpoint(
        self,
        snapshot: _StartSnapshot,
        intent: Mapping[str, Any],
        *,
        trial_status: str = "REGISTERED",
        stage: str = "TRIAL_RUNNING",
        performance_accessed: bool = False,
        recovery: bool = False,
    ) -> None:
        store = self._store(str(snapshot.objective["objective_id"]))
        checkpoint = store.load()
        if checkpoint is None:
            raise PredictiveTrialStartError("DAEMON_CHECKPOINT_NOT_FOUND", "当前 objective 缺少 canonical daemon checkpoint", status_code=503)
        if checkpoint.current_state in {ResearchDaemonState.READY.value, ResearchDaemonState.STRUCTURAL_PASS.value}:
            checkpoint = checkpoint.transition(ResearchDaemonState.PREDICTIVE_PENDING, "START_PREDICTIVE_TRIAL_1_ACCEPTED", details={"start_intent_id": intent["intent_id"], "trial_id": intent["trial_id"]})
        elif recovery and checkpoint.current_state == ResearchDaemonState.ENGINEERING_BLOCKED.value:
            checkpoint = checkpoint.transition(ResearchDaemonState.PREDICTIVE_PENDING, "PREDICTIVE_TRIAL_ENGINEERING_RESUME_ACCEPTED", details={"start_intent_id": intent["intent_id"], "trial_id": intent["trial_id"]})
        trial_view = {
            "trial_id": intent["trial_id"],
            "candidate_id": intent["candidate_id"],
            "candidate_hash": intent["candidate_hash"],
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
        checkpoint = checkpoint.update(
            current_candidate=self._candidate_work(snapshot, intent).to_dict(),
            current_trial=trial_view,
            required_action="PREDICTIVE_TRIAL_RUN_IN_PROGRESS",
            retry_safe=True,
            budget_view={**dict(checkpoint.budget_view), **_copy(snapshot.budget)},
            canonical_refs={
                **dict(checkpoint.canonical_refs),
                "predictive_trial_start": {
                    "action": START_PREDICTIVE_TRIAL_1,
                    "start_intent_id": intent["intent_id"],
                    "trial_id": intent["trial_id"],
                    "trial_contract_ref": intent["trial_contract_ref"],
                    "trial_contract_hash": intent["trial_contract_hash"],
                    "authorization_decision_id": intent.get("authorization_decision_id"),
                    "authorization_decision_hash": intent.get("authorization_decision_hash"),
                    "multiple_testing_family_id": snapshot.family_id,
                },
            },
        )
        if checkpoint.current_state == ResearchDaemonState.PREDICTIVE_PENDING.value:
            checkpoint = checkpoint.transition(ResearchDaemonState.PREDICTIVE_RUNNING, "PREDICTIVE_TRIAL_RUN_STARTED", details={"start_intent_id": intent["intent_id"], "trial_id": intent["trial_id"]})
        event_type = "PREDICTIVE_TRIAL_ENGINEERING_RESUME_ACCEPTED" if recovery else "PREDICTIVE_TRIAL_START_ACCEPTED"
        store.append_event(event_type, {"objective_id": snapshot.objective["objective_id"], "start_intent_id": intent["intent_id"], "trial_id": intent["trial_id"], "candidate_id": snapshot.candidate_id, "candidate_hash": snapshot.candidate_hash, "performance_accessed": bool(performance_accessed), "recovery": bool(recovery)}, event_id=stable_hash({"event_type": event_type, "start_intent_id": intent["intent_id"], "trial_id": intent["trial_id"]}))
        store.save(checkpoint)

    def _materialize_locked(self, snapshot: _StartSnapshot, intents: dict[str, dict[str, Any]], intent: dict[str, Any]) -> dict[str, Any]:
        if snapshot.candidate is None or not snapshot.batch_id or not snapshot.family_id or snapshot.budget_path is None or snapshot.family_path is None:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_START_UNAVAILABLE", "当前 Candidate 缺少完整的 canonical 启动绑定", details={"reasons": list(snapshot.reasons)})
        budget = SearchBudgetRegistryV1(str(snapshot.objective["objective_id"]), snapshot.budget_path)
        reservation_id = str(intent["reservation_id"])
        active = reservation_id in budget.snapshot().get("active_reservations", {})
        if not active:
            try:
                actual = budget.reserve_trial(batch_id=snapshot.batch_id, family_id=snapshot.family_id, candidate_id=snapshot.candidate_id)
            except BudgetExhaustedError as exc:
                raise PredictiveTrialStartError("PREDICTIVE_BUDGET_EXHAUSTED", "当前预测预算不足，系统拒绝启动 Trial", details={"budget": dict(snapshot.budget)}) from exc
            if str(actual) != reservation_id:
                raise PredictiveTrialStartError("BUDGET_RESERVATION_IDENTITY_CONFLICT", "canonical 预算预留身份不一致")
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
            "trial_number": 1,
            "candidate_id": snapshot.candidate_id,
            "candidate_hash": snapshot.candidate_hash,
            "predictive_budget_total": _int(snapshot.budget.get("total")),
            "performance_accessed": False,
            "registered_at": intent["created_at"],
            "candidate_identity": {"candidate_id": snapshot.candidate_id, "candidate_hash": snapshot.candidate_hash},
        }
        registration_path = self._multiple_testing_registration_path(snapshot)
        MultipleTestingRegistrationLedgerV1(registration_path, family_path=snapshot.family_path).register(registration)
        intent = self._update_intent(str(snapshot.objective["objective_id"]), intents, str(intent["intent_id"]), stage="MULTIPLE_TESTING_REGISTERED", multiple_testing_registration_ref=registration_path.relative_to(self.root).as_posix())
        ledger = self._trial_ledger(snapshot)
        existing = ledger.latest().get(str(intent["trial_id"]))
        if existing is None:
            record = self._trial_record(snapshot, intent)
            ledger.register_before_performance(record=record)
        else:
            expected = self._trial_record(snapshot, intent)
            if existing.candidate_id != expected.candidate_id or existing.candidate_hash != expected.candidate_hash or existing.budget_reservation_identity != expected.budget_reservation_identity or existing.lineage.get("trial_contract_hash") != expected.lineage.get("trial_contract_hash"):
                raise PredictiveTrialStartError("TRIAL_IDENTITY_CONFLICT", "同一 Trial ID 已绑定不同的 Candidate、预算或 Trial Contract")
        intent = self._update_intent(str(snapshot.objective["objective_id"]), intents, str(intent["intent_id"]), stage="TRIAL_CREATED", started_at=self._clock(), last_activity_at=self._clock())
        current_record = ledger.latest().get(str(intent["trial_id"]))
        if current_record is not None and current_record.status == "REGISTERED" and current_record.stage != "TRIAL_RUNNING":
            ledger.mark_started(trial_id=current_record.trial_id, started_at=intent["started_at"], last_activity_at=intent["last_activity_at"])
        refreshed_snapshot = self._snapshot(str(snapshot.objective["objective_id"]), allow_inflight=True)
        self._running_checkpoint(refreshed_snapshot, intent)
        intent = self._update_intent(str(snapshot.objective["objective_id"]), intents, str(intent["intent_id"]), stage="TRIAL_RUNNING", last_activity_at=self._clock(), recovery_status="RUNNING")
        return self._receipt(refreshed_snapshot, intent, idempotent=False)

    def _receipt(self, snapshot: _StartSnapshot | None, intent: Mapping[str, Any], *, idempotent: bool) -> dict[str, Any]:
        objective_id = str(intent["objective_id"])
        if snapshot is None:
            try:
                snapshot = self._snapshot(objective_id, allow_inflight=True)
            except PredictiveTrialStartError:
                snapshot = None
        trial: Mapping[str, Any] | None = None
        if snapshot is not None:
            trial = snapshot.trial_records.get(str(intent["trial_id"]))
        budget = dict(snapshot.budget) if snapshot is not None else dict(intent.get("budget_after_reservation") or {})
        stage = str(intent.get("stage") or "REQUESTED")
        return {
            "schema_version": START_RECEIPT_SCHEMA,
            "status": "STARTED" if stage in {"TRIAL_CREATED", "TRIAL_RUNNING"} else stage,
            "idempotent": bool(idempotent),
            "action": START_PREDICTIVE_TRIAL_1,
            "objective_id": objective_id,
            "start_intent_id": intent.get("intent_id"),
            "trial_id": intent.get("trial_id"),
            "stage": stage,
            "trial": _copy(trial) if trial else None,
            "created_at": intent.get("created_at"),
            "started_at": intent.get("started_at"),
            "last_activity_at": intent.get("last_activity_at"),
            "finished_at": intent.get("finished_at"),
            "error_code": intent.get("error_code"),
            "error_message": intent.get("error_message"),
            "recovery_status": intent.get("recovery_status") or "RUNNING",
            "budget": budget,
            "budget_reservation_identity": intent.get("reservation_id"),
            "trial_contract_ref": intent.get("trial_contract_ref"),
            "trial_contract_hash": intent.get("trial_contract_hash"),
            "authorization_decision_id": intent.get("authorization_decision_id"),
            "authorization_decision_hash": intent.get("authorization_decision_hash"),
            "multiple_testing_family_id": intent.get("family_id"),
            "performance_access": 0,
            "final_test_access": {"physical": 0, "analytical": 0, "decision": 0},
            "prospective": "DISABLED",
            "real_order": "DISABLED",
            "next_action": "PREDICTIVE_TRIAL_RUN_IN_PROGRESS" if stage in IN_FLIGHT_STAGES else None,
            "message_zh": "第 1 次预测试验已按 canonical 流程创建并进入运行状态；系统不会创建新 Candidate。" if stage == "TRIAL_RUNNING" else "已返回现有的预测试验启动状态。",
        }

    def _launch(self, objective_id: str, intent_id: str) -> None:
        with self._threads_lock:
            current = self._threads.get(intent_id)
            if current is not None and current.is_alive():
                return
            thread = threading.Thread(target=self._run_intent, args=(objective_id, intent_id), name=f"predictive-trial-{intent_id}", daemon=True)
            self._threads[intent_id] = thread
            thread.start()

    @staticmethod
    def _supports_recovery_argument(callable_obj: Any) -> bool:
        try:
            parameters = inspect.signature(callable_obj).parameters.values()
        except (TypeError, ValueError):
            return False
        return any(parameter.kind == inspect.Parameter.VAR_KEYWORD or parameter.name == "recovery" for parameter in parameters)

    @staticmethod
    def _runner_error_code(exc: Exception) -> str:
        prefix = str(exc).split(":", 1)[0].strip()
        if prefix.startswith(("CANONICAL_", "PREDICTIVE_", "ENGINEERING_")):
            return prefix
        return "PREDICTIVE_TRIAL_RUNNER_ERROR"

    def _invoke_runner(self, candidate: Any, objective_id: str, *, recovery: bool = False) -> Any:
        runner = self._runner or PredictiveTrialRunnerV1(self.root, objective_id)
        callable_obj = runner.run if hasattr(runner, "run") else runner
        if recovery and self._supports_recovery_argument(callable_obj):
            return callable_obj(candidate, recovery=True)
        return callable_obj(candidate)

    def _reconcile_runner_failure(
        self,
        objective_id: str,
        intent: Mapping[str, Any],
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Apply the existing failure policy when the runner raises."""
        recovery = bool(intent.get("recovery_requested"))
        try:
            snapshot = self._snapshot(objective_id, allow_inflight=True, allow_recovery=recovery)
        except PredictiveTrialStartError:
            return False
        if snapshot.budget_path is None:
            return False
        ledger = self._trial_ledger(snapshot)
        current = ledger.latest().get(str(intent["trial_id"]))
        performance_accessed = bool(current and current.performance_accessed)
        budget = SearchBudgetRegistryV1(objective_id, snapshot.budget_path)
        reservation_id = str(intent["reservation_id"])
        active = reservation_id in budget.snapshot().get("active_reservations", {})
        if current is None:
            if active:
                budget.release(reservation_id)
            return False
        if current.status in ResearchFactoryTrialLedgerFacadeV1.TERMINAL:
            return performance_accessed
        if performance_accessed:
            if current.status == "PERFORMANCE_ACCESSED":
                ledger.mark_engineering_interrupted(
                    trial_id=current.trial_id,
                    budget_reservation_identity=reservation_id,
                    error_code=error_code,
                    error_message=error_message,
                )
            if active and not recovery:
                budget.consume(reservation_id)
            return True
        if current.status == "REGISTERED":
            ledger.mark_blocked(current.trial_id, reason_codes=("PREDICTIVE_TRIAL_RUNNER_ERROR",))
        if active:
            budget.release(reservation_id)
        return False

    def _sync_terminal_intent(self, objective_id: str, intents: dict[str, dict[str, Any]], intent: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
        status = str(record.get("status") or "").upper()
        performance_accessed = bool(record.get("performance_accessed"))
        completed = status == "COMPLETED"
        stage = "TRIAL_COMPLETED" if completed else "TRIAL_FAILED"
        recovery_status = "COMPLETE" if completed else "FAILED"
        updated = self._update_intent(
            objective_id,
            intents,
            str(intent["intent_id"]),
            stage=stage,
            finished_at=self._clock(),
            last_activity_at=self._clock(),
            recovery_status=recovery_status,
            performance_accessed=performance_accessed,
            result_status=status,
            result_classification=record.get("classification"),
            error_code=None if completed else str(record.get("error_code") or "PREDICTIVE_TRIAL_FAILED"),
            resume_status="COMPLETE" if completed and intent.get("recovery_requested") else "FAILED" if intent.get("recovery_requested") else intent.get("resume_status"),
        )
        store = self._store(objective_id)
        current = store.load()
        if current is not None and current.current_trial and str((current.current_trial or {}).get("trial_id")) == str(intent["trial_id"]):
            if current.current_state == ResearchDaemonState.PREDICTIVE_RUNNING.value:
                target = ResearchDaemonState.PREDICTIVE_COMPLETE if completed else ResearchDaemonState.ENGINEERING_BLOCKED
                current = current.transition(target, "PREDICTIVE_TRIAL_COMPLETED" if completed else "PREDICTIVE_TRIAL_FAILED", details={"start_intent_id": intent["intent_id"], "trial_id": intent["trial_id"]})
            current = current.update(
                required_action="PREDICTIVE_TRIAL_COMPLETED" if completed else "PREDICTIVE_TRIAL_FAILURE_REVIEW",
                retry_safe=not performance_accessed,
                current_trial={
                    **dict(current.current_trial or {}),
                    "stage": stage,
                    "status": status,
                    "performance_accessed": performance_accessed,
                    "finished_at": updated.get("finished_at"),
                    "recovery_status": recovery_status,
                },
            )
            store.append_event(
                "PREDICTIVE_TRIAL_RESULT_OBSERVED",
                {"objective_id": objective_id, "start_intent_id": intent["intent_id"], "trial_id": intent["trial_id"], "status": status, "performance_accessed": performance_accessed},
                event_id=stable_hash({"event_type": "PREDICTIVE_TRIAL_RESULT_OBSERVED", "start_intent_id": intent["intent_id"], "status": status}),
            )
            store.save(current)
        return updated

    def _run_intent(self, objective_id: str, intent_id: str) -> None:
        """Run one intent under the objective execution lease.

        The lease closes the gap between two backend processes both noticing
        the same durable ``TRIAL_RUNNING`` intent.  A crashed owner leaves a
        stale lease that the existing daemon-lock implementation can recover.
        """
        store = self._store(objective_id)
        checkpoint = store.load()
        if checkpoint is None:
            return
        execution_lock = DaemonInstanceLockV1(store.lock_path, objective_id)
        try:
            execution_lock.acquire(run_id=checkpoint.daemon_run_id)
        except DaemonAlreadyRunningError:
            return
        try:
            self._run_intent_body(objective_id, intent_id)
        finally:
            try:
                execution_lock.heartbeat()
            except DaemonAlreadyRunningError:
                pass
            execution_lock.release()

    def _run_intent_body(self, objective_id: str, intent_id: str) -> None:
        intents = self._load_intents(objective_id)
        intent = intents.get(intent_id)
        if intent is None:
            return
        try:
            recovery = bool(intent.get("recovery_requested"))
            snapshot = self._snapshot(objective_id, allow_inflight=True, allow_recovery=recovery)
            candidate = self._candidate_work(snapshot, intent)
            result = self._invoke_runner(candidate, objective_id, recovery=recovery)
            status = str(getattr(result, "status", "COMPLETED") or "COMPLETED").upper()
            performance_accessed = bool(getattr(result, "performance_accessed", False))
            intents = self._load_intents(objective_id)
            record = self._trial_records(objective_id).get(str(intent["trial_id"]))
            if record is None:
                record = {"status": status, "performance_accessed": performance_accessed, "classification": getattr(result, "classification", None)}
            self._sync_terminal_intent(objective_id, intents, intent, record)
        except Exception as exc:
            intents = self._load_intents(objective_id)
            intent = intents.get(intent_id)
            if intent is None:
                return
            error_code = self._runner_error_code(exc)
            error_message = str(exc)[:500]
            performance_accessed = self._reconcile_runner_failure(
                objective_id,
                intent,
                error_code=error_code,
                error_message=error_message,
            )
            recovery = bool(intent.get("recovery_requested"))
            snapshot = None
            try:
                snapshot = self._snapshot(objective_id, allow_inflight=True, allow_recovery=recovery)
            except PredictiveTrialStartError:
                pass
            self._update_intent(objective_id, intents, intent_id, stage="TRIAL_FAILED", finished_at=self._clock(), last_activity_at=self._clock(), recovery_status="FAILED", resume_status="FAILED" if recovery else intent.get("resume_status"), performance_accessed=performance_accessed, error_code=error_code, error_message=error_message)
            if snapshot is not None:
                store = self._store(objective_id)
                current = store.load()
                if current is not None and current.current_state == ResearchDaemonState.PREDICTIVE_RUNNING.value:
                    current = current.transition(ResearchDaemonState.ENGINEERING_BLOCKED, "PREDICTIVE_TRIAL_RUNNER_ERROR", details={"start_intent_id": intent_id, "trial_id": intent["trial_id"]})
                    current = current.update(required_action="PREDICTIVE_TRIAL_FAILURE_REVIEW", retry_safe=False, last_error=error_message, error_reason_code=error_code, current_trial={**dict(current.current_trial or {}), "stage": "TRIAL_FAILED", "error_code": error_code, "error_message": error_message, "recovery_status": "WAITING_RECOVERY"})
                    store.save(current)

    def _resume_existing_trial_locked(self, objective_id: str, intents: dict[str, dict[str, Any]], intent: Mapping[str, Any], record: Any, *, recovery: bool = False) -> tuple[dict[str, Any], bool]:
        """Recover one materialized trial without creating a second identity."""
        trial_id = str(intent.get("trial_id") or "")
        status = str(getattr(record, "status", record.get("status") if isinstance(record, Mapping) else "") or "").upper()
        if status in TERMINAL_TRIAL_STATUSES:
            updated = self._sync_terminal_intent(objective_id, intents, intent, record.to_dict() if hasattr(record, "to_dict") else record)
            return updated, False
        snapshot = self._snapshot(objective_id, allow_inflight=True, allow_recovery=recovery)
        if trial_id not in snapshot.trial_records:
            raise PredictiveTrialStartError("TRIAL_IDENTITY_CONFLICT", "启动意图引用的 Trial 记录无法完成 canonical 校验")
        record_intent_id = record.get("start_intent_id") if isinstance(record, Mapping) else getattr(record, "start_intent_id", None)
        if record_intent_id and str(record_intent_id) != str(intent.get("intent_id")):
            raise PredictiveTrialStartError("TRIAL_IDENTITY_CONFLICT", "已有 Trial 绑定了不同的启动意图")
        started_at = str(intent.get("started_at") or getattr(record, "started_at", None) or intent.get("created_at") or self._clock())
        intent = self._update_intent(
            objective_id,
            intents,
            str(intent["intent_id"]),
            stage="TRIAL_RECOVERY_RUNNING" if recovery else "TRIAL_RUNNING",
            started_at=started_at,
            last_activity_at=self._clock(),
            recovery_status="RECOVERING",
            recovery_requested=True if recovery else intent.get("recovery_requested"),
            resume_status="RUNNING" if recovery else intent.get("resume_status"),
        )
        self._running_checkpoint(snapshot, intent, trial_status=status or "REGISTERED", performance_accessed=bool(getattr(record, "performance_accessed", record.get("performance_accessed") if isinstance(record, Mapping) else False)), recovery=recovery)
        return intent, self.auto_run

    def _resume_receipt(self, objective_id: str, intent: Mapping[str, Any], *, idempotent: bool) -> dict[str, Any]:
        record = self._trial_records(objective_id).get(str(intent.get("trial_id")))
        status = str(record.get("status") if isinstance(record, Mapping) else "" or intent.get("result_status") or "TRIAL_RECOVERY_RUNNING").upper()
        stage = str(intent.get("stage") or "TRIAL_RECOVERY_REQUESTED")
        if status == "COMPLETED" or stage == "TRIAL_COMPLETED":
            receipt_status = "COMPLETED"
            next_action = "PREDICTIVE_TRIAL_COMPLETED"
        elif status == "INVALIDATED" or stage == "TRIAL_FAILED":
            receipt_status = "FAILED"
            next_action = "PREDICTIVE_TRIAL_FAILURE_REVIEW"
        else:
            receipt_status = "RESUMING"
            next_action = "PREDICTIVE_TRIAL_RUN_IN_PROGRESS"
        store = self._store(objective_id)
        checkpoint = store.load()
        budget: Mapping[str, Any] = {}
        if checkpoint is not None:
            budget_path = self._resolve_budget_path(objective_id, checkpoint)
            if budget_path is not None:
                try:
                    budget_snapshot = SearchBudgetRegistryV1(objective_id, budget_path).snapshot()
                    bucket = next((item for item in budget_snapshot.get("buckets", ()) if item.get("kind") == "objective" and item.get("key") == objective_id), {})
                    budget = {"objective_id": objective_id, "total": _int(bucket.get("limit")), "used": _int(bucket.get("used")), "reserved": _int(bucket.get("reserved")), "remaining": _int(bucket.get("remaining")), "registry_path": budget_path.relative_to(self.root).as_posix()}
                except (OSError, ValueError, KeyError, TypeError):
                    budget = dict(intent.get("budget_after_resume") or {})
        if not budget:
            budget = dict(intent.get("budget_after_resume") or intent.get("budget_after_reservation") or {})
        return {
            "schema_version": START_RECEIPT_SCHEMA,
            "status": receipt_status,
            "idempotent": bool(idempotent),
            "action": RESUME_PREDICTIVE_TRIAL_1,
            "action_zh": RESUME_ACTION_ZH,
            "objective_id": objective_id,
            "start_intent_id": intent.get("intent_id"),
            "trial_id": intent.get("trial_id"),
            "trial_number": 1,
            "stage": stage,
            "trial": _copy(record) if isinstance(record, Mapping) else None,
            "created_at": intent.get("created_at"),
            "started_at": intent.get("started_at"),
            "last_activity_at": intent.get("last_activity_at"),
            "finished_at": intent.get("finished_at"),
            "error_code": intent.get("error_code"),
            "error_message": intent.get("error_message"),
            "recovery_status": intent.get("recovery_status") or "RECOVERING",
            "resume_status": intent.get("resume_status") or "PENDING",
            "budget": budget,
            "budget_reservation_identity": intent.get("reservation_id"),
            "trial_contract_ref": intent.get("trial_contract_ref"),
            "trial_contract_hash": intent.get("trial_contract_hash"),
            "candidate_id": intent.get("candidate_id"),
            "candidate_hash": intent.get("candidate_hash"),
            "performance_access": 0,
            "final_test_access": {"physical": 0, "analytical": 0, "decision": 0},
            "prospective": "DISABLED",
            "real_order": "DISABLED",
            "next_action": next_action,
            "message_zh": "已锁定原第 1 次预测试验；等待后台在同一 Trial 和同一预算槽位上恢复。" if receipt_status == "RESUMING" else "已返回原第 1 次预测试验的恢复结果。",
        }

    def _resume_begin_locked(self, objective_id: str, context: Mapping[str, Any], intents: dict[str, dict[str, Any]], intent: Mapping[str, Any], preview: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = context["snapshot"]
        ledger = self._trial_ledger(snapshot)
        resume_request_id = stable_hash({"start_intent_id": intent["intent_id"], "trial_id": intent["trial_id"], "preview_hash": preview["preview_hash"]})
        updated = self._update_intent(
            objective_id,
            intents,
            str(intent["intent_id"]),
            recovery_requested=True,
            resume_request_id=resume_request_id,
            resume_preview_hash=preview["preview_hash"],
            resume_confirmation_token_hash=stable_hash(str(preview["confirmation_token"])),
            resume_status="REQUESTED",
            stage="TRIAL_RECOVERY_REQUESTED",
            last_activity_at=self._clock(),
            recovery_status="RECOVERING",
            performance_accessed=True,
            error_code=None,
            error_message=None,
        )
        ledger.mark_engineering_resume_started(
            str(intent["trial_id"]),
            resume_request_id=resume_request_id,
            preview_hash=str(preview["preview_hash"]),
        )
        context_snapshot = self._snapshot(objective_id, allow_inflight=True, allow_recovery=True)
        self._running_checkpoint(
            context_snapshot,
            updated,
            trial_status="PERFORMANCE_ACCESSED",
            stage="TRIAL_RUNNING",
            performance_accessed=True,
            recovery=True,
        )
        return self._update_intent(
            objective_id,
            intents,
            str(intent["intent_id"]),
            stage="TRIAL_RECOVERY_RUNNING",
            resume_status="RUNNING",
            recovery_status="RECOVERING",
            started_at=updated.get("started_at") or self._clock(),
            last_activity_at=self._clock(),
            performance_accessed=True,
            budget_after_resume=context_snapshot.budget,
        )

    def _resume_confirm_locked(self, objective_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        objective_id = self._safe_id(objective_id, "objective_id")
        action, intent_id, trial_id, candidate_id, candidate_hash, preview_hash, token = self._ensure_resume_request(body)
        store = self._store(objective_id)
        checkpoint = store.load()
        if checkpoint is None:
            raise PredictiveTrialStartError("DAEMON_CHECKPOINT_NOT_FOUND", "当前 objective 缺少 canonical daemon checkpoint", status_code=503)
        lock = DaemonInstanceLockV1(store.lock_path, objective_id)
        try:
            lock.acquire(run_id=checkpoint.daemon_run_id)
        except DaemonAlreadyRunningError as exc:
            existing = self._load_intents(objective_id).get(intent_id)
            if existing is not None and existing.get("recovery_requested"):
                if str(existing.get("trial_id")) != trial_id or str(existing.get("candidate_id")) != candidate_id or str(existing.get("candidate_hash")) != candidate_hash or str(existing.get("resume_preview_hash")) != preview_hash or str(existing.get("resume_confirmation_token_hash")) != stable_hash(token):
                    raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RESUME_IDEMPOTENCY_CONFLICT", "相同恢复意图对应了不同的 Trial、Candidate 或预览") from exc
                return self._resume_receipt(objective_id, existing, idempotent=True)
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RESUME_IN_PROGRESS", "当前 objective 正在执行其他受保护操作，请稍后读取恢复状态") from exc
        launch = False
        try:
            intents = self._load_intents(objective_id)
            existing = intents.get(intent_id)
            if existing is not None and existing.get("recovery_requested"):
                if str(existing.get("action")) != START_PREDICTIVE_TRIAL_1 or str(existing.get("trial_id")) != trial_id or str(existing.get("candidate_id")) != candidate_id or str(existing.get("candidate_hash")) != candidate_hash or str(existing.get("resume_preview_hash")) != preview_hash or str(existing.get("resume_confirmation_token_hash")) != stable_hash(token):
                    raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RESUME_IDEMPOTENCY_CONFLICT", "相同恢复意图对应了不同的 Trial、Candidate 或预览")
                record = self._trial_records(objective_id).get(trial_id)
                if record is None:
                    raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_TRIAL_MISSING", "恢复意图引用的 Trial 记录不存在")
                record_status = str(record.get("status") or "").upper()
                record_lineage = record.get("lineage") if isinstance(record.get("lineage"), Mapping) else {}
                resume_already_attempted = record_lineage.get("engineering_resume_started") is True or _int(record_lineage.get("engineering_resume_attempt_count")) > 0
                if record_status == "INVALIDATED" and not resume_already_attempted:
                    context = self._load_recovery_context(objective_id)
                    updated = self._resume_begin_locked(objective_id, context, intents, existing, {"preview_hash": preview_hash, "confirmation_token": token})
                    existing = updated
                    launch = self.auto_run
                elif record_status == "PERFORMANCE_ACCESSED":
                    existing, launch = self._resume_existing_trial_locked(objective_id, intents, existing, record, recovery=True)
                return_value = self._resume_receipt(objective_id, existing, idempotent=True)
            else:
                context = self._load_recovery_context(objective_id)
                if str(context["record"].get("trial_id")) != trial_id or context["snapshot"].candidate_id != candidate_id or context["snapshot"].candidate_hash != candidate_hash or str(context["intent"].get("intent_id")) != intent_id:
                    raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_IDENTITY_MISMATCH", "恢复确认中的 Trial、Candidate 或启动意图已变化")
                preview = self.resume_preview(objective_id)
                if preview["preview_hash"] != preview_hash or preview["confirmation_token"] != token:
                    raise PredictiveTrialStartError("STALE_PREDICTIVE_TRIAL_RESUME_PREVIEW", "恢复预览已失效，请刷新后重新确认")
                existing = self._resume_begin_locked(objective_id, context, intents, context["intent"], preview)
                return_value = self._resume_receipt(objective_id, existing, idempotent=False)
                launch = self.auto_run
            if launch:
                self._launch(objective_id, intent_id)
            return return_value
        finally:
            lock.heartbeat()
            lock.release()

    def _confirm_locked(self, objective_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        objective_id = self._safe_id(objective_id, "objective_id")
        action, intent_id, candidate_id, candidate_hash, preview_hash, token = self._ensure_request(body)
        store = self._store(objective_id)
        checkpoint = store.load()
        if checkpoint is None:
            raise PredictiveTrialStartError("DAEMON_CHECKPOINT_NOT_FOUND", "当前 objective 缺少 canonical daemon checkpoint", status_code=503)
        lock = DaemonInstanceLockV1(store.lock_path, objective_id)
        try:
            lock.acquire(run_id=checkpoint.daemon_run_id)
        except DaemonAlreadyRunningError as exc:
            existing = None
            for _ in range(20):
                existing = self._load_intents(objective_id).get(intent_id)
                if existing is not None:
                    break
                time.sleep(0.01)
            if existing is not None:
                self._validate_existing_intent(existing, action=action, candidate_id=candidate_id, candidate_hash=candidate_hash, preview_hash=preview_hash, token=token)
                return self._receipt(None, existing, idempotent=True)
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_START_IN_PROGRESS", "当前 objective 正在执行其他受保护操作，请稍后读取启动状态", details={"start_intent_id": intent_id}) from exc
        launch = False
        try:
            intents = self._load_intents(objective_id)
            existing = intents.get(intent_id)
            if existing is not None:
                self._validate_existing_intent(existing, action=action, candidate_id=candidate_id, candidate_hash=candidate_hash, preview_hash=preview_hash, token=token)
                trial_records = self._trial_records(objective_id)
                if str(existing.get("trial_id")) in trial_records:
                    existing, launch = self._resume_existing_trial_locked(objective_id, intents, existing, trial_records[str(existing["trial_id"])])
                    result = self._receipt(None, existing, idempotent=True)
                    if launch:
                        self._launch(objective_id, intent_id)
                    return result
                snapshot = self._snapshot(objective_id, allow_inflight=True)
                return_value = self._materialize_locked(snapshot, intents, existing)
                launch = self.auto_run and str(return_value.get("stage")) == "TRIAL_RUNNING"
                result = return_value
            else:
                snapshot = self._snapshot(objective_id)
                if not snapshot.eligible:
                    raise PredictiveTrialStartError("PREDICTIVE_TRIAL_START_UNAVAILABLE", "当前不满足安全启动第 1 次预测试验的全部条件", details={"reasons": list(snapshot.reasons)})
                if snapshot.candidate_id != candidate_id or snapshot.candidate_hash != candidate_hash:
                    raise PredictiveTrialStartError("PREDICTIVE_TRIAL_START_CANDIDATE_MISMATCH", "确认页中的 Candidate 身份已过期，请刷新预览后重试")
                preview = self._preview_payload(snapshot, require_eligible=True)
                if preview["preview_hash"] != preview_hash or preview["confirmation_token"] != token:
                    raise PredictiveTrialStartError("STALE_PREDICTIVE_TRIAL_START_PREVIEW", "启动预览已失效，请刷新后重新确认")
                trial_id = str(preview["preview"]["trial_id"])
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
                    "trial_id": trial_id,
                    "reservation_id": preview["preview"]["budget_reservation_identity"],
                    "batch_id": snapshot.batch_id,
                    "family_id": snapshot.family_id,
                    "created_at": self._clock(),
                    "updated_at": self._clock(),
                    "stage": "REQUESTED",
                    "recovery_status": "PENDING",
                    "data_snapshot_hash": stable_hash(preview["preview"]["data_snapshot"]),
                    "engine_hash": stable_hash({"runner": "CanonicalPredictiveExecutorV1", "execution_contract_version": snapshot.candidate.execution_contract_version if snapshot.candidate else None, "policy_hash": snapshot.policy_hash}),
                    "seed": int(getattr(snapshot.policy, "bootstrap_seed", 0) or 0) + 1,
                }
                intent = self._put_intent(objective_id, intents, intent)
                result = self._materialize_locked(snapshot, intents, intent)
                launch = self.auto_run
            if launch:
                self._launch(objective_id, intent_id)
            return result
        finally:
            lock.heartbeat()
            lock.release()

    def recover(self, objective_id: str) -> dict[str, Any]:
        """Reconcile pending start intents after a backend/daemon restart."""
        objective_id = self._safe_id(objective_id, "objective_id")
        store = self._store(objective_id)
        checkpoint = store.load()
        if checkpoint is None:
            raise PredictiveTrialStartError("DAEMON_CHECKPOINT_NOT_FOUND", "当前 objective 缺少 canonical daemon checkpoint", status_code=503)
        lock = DaemonInstanceLockV1(store.lock_path, objective_id)
        try:
            lock.acquire(run_id=checkpoint.daemon_run_id)
        except DaemonAlreadyRunningError as exc:
            raise PredictiveTrialStartError("PREDICTIVE_TRIAL_RECOVERY_IN_PROGRESS", "当前 objective 正在恢复，请稍后重试") from exc
        launches: list[str] = []
        reconciled: list[str] = []
        try:
            intents = self._load_intents(objective_id)
            trial_records = self._trial_records(objective_id)
            for intent_id, intent in sorted(intents.items()):
                stage = str(intent.get("stage"))
                record = trial_records.get(str(intent.get("trial_id")))
                record_status = str(record.get("status") if isinstance(record, Mapping) else getattr(record, "status", "") or "").upper()
                if stage in RESUME_IN_FLIGHT_STAGES or bool(intent.get("recovery_requested")):
                    if record is not None and record_status == "PERFORMANCE_ACCESSED":
                        intent, launch = self._resume_existing_trial_locked(objective_id, intents, intent, record, recovery=True)
                        if launch:
                            launches.append(intent_id)
                    elif record is not None and record_status in TERMINAL_TRIAL_STATUSES:
                        self._sync_terminal_intent(objective_id, intents, intent, record.to_dict() if hasattr(record, "to_dict") else record)
                        reconciled.append(intent_id)
                    continue
                if stage not in IN_FLIGHT_STAGES:
                    if record is not None and record_status in TERMINAL_TRIAL_STATUSES:
                        expected_stage = "TRIAL_COMPLETED" if record_status == "COMPLETED" else "TRIAL_FAILED"
                        current_checkpoint = self._store(objective_id).load()
                        current_trial = current_checkpoint.current_trial if current_checkpoint else None
                        checkpoint_stale = bool(current_trial and str(current_trial.get("trial_id")) == str(intent.get("trial_id")) and str(current_trial.get("stage")) != expected_stage)
                        if stage != expected_stage or checkpoint_stale:
                            self._sync_terminal_intent(objective_id, intents, intent, record.to_dict() if hasattr(record, "to_dict") else record)
                            reconciled.append(intent_id)
                    continue
                if record is not None:
                    intent, launch = self._resume_existing_trial_locked(objective_id, intents, intent, record)
                    if launch:
                        launches.append(intent_id)
                    continue
                snapshot = self._snapshot(objective_id, allow_inflight=True)
                self._materialize_locked(snapshot, intents, intent)
                if self.auto_run:
                    launches.append(intent_id)
            result = {"schema_version": START_RECEIPT_SCHEMA, "status": "RECOVERED", "objective_id": objective_id, "recovered_intents": launches, "reconciled_intents": reconciled, "trial_count": len(self._trial_records(objective_id))}
        finally:
            lock.heartbeat()
            lock.release()
        for intent_id in launches:
            self._launch(objective_id, intent_id)
        return result

    def recover_all(self) -> dict[str, Any]:
        """Recover confirmed formal starts after a backend process restart."""
        root = self.root / "reports" / "research_daemon"
        recovered: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        if not root.is_dir():
            return {"schema_version": START_RECEIPT_SCHEMA, "status": "NO_PENDING_TRIALS", "objectives": [], "errors": []}
        for path in sorted(root.glob(f"*/{START_INTENTS_FILENAME}")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                objective_id = self._safe_id(payload.get("objective_id"), "objective_id")
                intents = payload.get("intents") if isinstance(payload, Mapping) else {}
                pending = isinstance(intents, Mapping) and any(
                    str(item.get("action")) in {START_PREDICTIVE_TRIAL_1, RESUME_PREDICTIVE_TRIAL_1}
                    and (str(item.get("stage")) in (IN_FLIGHT_STAGES | RESUME_IN_FLIGHT_STAGES)
                    or bool(item.get("recovery_requested"))
                    )
                    for item in intents.values()
                    if isinstance(item, Mapping)
                )
                if not pending:
                    continue
                recovered.append(self.recover(objective_id))
            except (OSError, UnicodeError, json.JSONDecodeError, PredictiveTrialStartError) as exc:
                errors.append({"path": path.relative_to(self.root).as_posix(), "code": exc.code if isinstance(exc, PredictiveTrialStartError) else "PREDICTIVE_TRIAL_RECOVERY_SOURCE_INVALID", "message_zh": exc.message_zh if isinstance(exc, PredictiveTrialStartError) else "预测试验启动恢复源暂时不可读"})
        return {"schema_version": START_RECEIPT_SCHEMA, "status": "RECOVERED" if recovered else "NO_PENDING_TRIALS", "objectives": recovered, "errors": errors}

    def confirm(self, objective_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        with self._confirm_lock:
            return self._confirm_locked(objective_id, body)

    def confirm_resume(self, objective_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Confirm recovery of the existing engineering-invalidated Trial."""

        with self._confirm_lock:
            return self._resume_confirm_locked(objective_id, body)
