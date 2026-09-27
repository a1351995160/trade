"""有限业务调度；只引用原会话和回执，不授予研究、数据或策略资格。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re

from .bounded_research_v1 import _put, _read, source_identity
from .common import stable_hash
from .mutation_boundary import MutationBusyError, ObjectiveMutationLock


KINDS = {"CAPTURE_OPEN", "CAPTURE_CLOSE", "RESEARCH", "VALIDATION", "PAPER", "PORTFOLIO", "DAILY_PLAN"}
WAITING = {"WAITING_DATA", "WAITING_QUALIFICATION", "BUDGET_EXHAUSTED"}
TERMINAL = {"COMPLETED", "FAILED", "MISSED_WINDOW"}


def _stamp(value):
    if not isinstance(value, str):
        raise ValueError("JOB_AWARE_TIME_REQUIRED")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("JOB_AWARE_TIME_REQUIRED") from exc
    if stamp.tzinfo is None:
        raise ValueError("JOB_AWARE_TIME_REQUIRED")
    return stamp.astimezone(timezone.utc)


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", value):
        raise ValueError("JOB_ID_INVALID")
    return value


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("JOB_HASH_INVALID")
    return value


class LifecycleJobsV1:
    """dispatcher 来自固定部署代码；绝不从任务 JSON 加载模块、命令或 callable。"""

    def __init__(self, root, *, dispatchers=None, synthetic_clock=None):
        self.root = Path(root).absolute()
        if self.root.resolve() != self.root:
            raise ValueError("JOB_PATH_REDIRECTED")
        self.dispatchers = dict(dispatchers or {})
        if not set(self.dispatchers) <= KINDS:
            raise ValueError("JOB_DISPATCHER_KIND_INVALID")
        self.synthetic_clock = synthetic_clock

    def _root(self, job_id):
        path = self.root / _identity(job_id)
        if path.resolve() != path:
            raise ValueError("JOB_PATH_REDIRECTED")
        return path

    def _now(self, profile):
        if self.synthetic_clock is not None:
            if profile != "SYNTHETIC":
                raise ValueError("JOB_REAL_CLOCK_CANNOT_BE_INJECTED")
            value = self.synthetic_clock()
            return _stamp(value.isoformat())
        return datetime.now(timezone.utc)

    def create(self, job_id, *, scope_ref, kind, config_hash, expires_at, max_calls, stages,
               profile="REAL", trading_calendar=()):
        root = self._root(job_id)
        if profile not in {"REAL", "SYNTHETIC"} or kind not in KINDS:
            raise ValueError("JOB_PROFILE_OR_KIND_INVALID")
        now = self._now(profile)
        expiry = _stamp(expires_at)
        if expiry <= now or type(max_calls) is not int or not 1 <= max_calls <= 10000:
            raise ValueError("JOB_EXPIRY_OR_LIMIT_INVALID")
        if not isinstance(scope_ref, dict) or set(scope_ref) != {"objective_id", "session_id", "scope_hash"}:
            raise ValueError("JOB_EXISTING_SCOPE_REQUIRED")
        _identity(scope_ref["objective_id"])
        _identity(scope_ref["session_id"])
        _hash(scope_ref["scope_hash"])
        _hash(config_hash)
        if not isinstance(stages, list) or not stages or len(stages) > 10000:
            raise ValueError("JOB_STAGES_INVALID")
        calendar = tuple(trading_calendar)
        if any(type(day) is not int for day in calendar) or tuple(sorted(set(calendar))) != calendar:
            raise ValueError("JOB_CALENDAR_INVALID")
        for day in calendar:
            datetime.strptime(str(day), "%Y%m%d")
        keys = []
        previous = None
        for stage in stages:
            if not isinstance(stage, dict) or set(stage) != {"key", "trade_date", "not_before", "not_after"}:
                raise ValueError("JOB_STAGE_FIELDS_INVALID")
            keys.append(_identity(stage["key"]))
            start, end = _stamp(stage["not_before"]), _stamp(stage["not_after"])
            if start >= end or end > expiry or (previous is not None and start < previous):
                raise ValueError("JOB_STAGE_WINDOW_INVALID")
            previous = end
            day = stage["trade_date"]
            if day is not None and (type(day) is not int or day not in calendar):
                raise ValueError("JOB_STAGE_NOT_TRADING_SESSION")
            if kind in {"CAPTURE_OPEN", "CAPTURE_CLOSE", "PAPER", "DAILY_PLAN"} and day is None:
                raise ValueError("JOB_TRADING_SESSION_REQUIRED")
            if kind.startswith("CAPTURE_"):
                local = timezone(timedelta(hours=8))
                if any(int(stamp.astimezone(local).strftime("%Y%m%d")) != day for stamp in (start, end)):
                    raise ValueError("JOB_CAPTURE_WINDOW_NOT_SAME_DAY")
        if len(keys) != len(set(keys)):
            raise ValueError("JOB_STAGE_KEY_DUPLICATE")
        config = {"version": "RESEARCH_LIFECYCLE_JOBS_V1", "job_id": job_id,
                  "scope_ref": deepcopy(scope_ref), "kind": kind, "config_hash": config_hash,
                  "profile": profile, "source_identity": source_identity(), "expires_at": expiry.isoformat(),
                  "max_calls": max_calls, "stages": deepcopy(stages), "trading_calendar": list(calendar),
                  "created_at": now.isoformat(), "authorization": "EXISTING_SERVICE_MUST_REVALIDATE"}
        with ObjectiveMutationLock.for_resource(root):
            if (root / "CONFIG.json").exists():
                raise ValueError("JOB_ALREADY_EXISTS")
            _put(root / "CONFIG.json", config)
            _put(root / "STATE.json", {"config_identity": stable_hash(config), "revision": 0,
                                       "previous_state_hash": None, "started": False, "paused": False,
                                       "calls_started": 0, "stages": {}, "waiting": None, "waiting_reason": None,
                                       "next_check_at": None})
        return self.status(job_id)

    def _load(self, job_id, *, repair=False):
        root = self._root(job_id)
        config, state = _read(root / "CONFIG.json"), _read(root / "STATE.json")
        if state["config_identity"] != stable_hash(config) or config["job_id"] != job_id:
            raise ValueError("JOB_CONFIG_CHANGED")
        pending = root / "STATE.next.json"
        if pending.exists():
            value = _read(pending)
            if (value["config_identity"] != state["config_identity"] or value["revision"] != state["revision"] + 1
                    or value["previous_state_hash"] != stable_hash(state)):
                raise ValueError("JOB_STATE_COMMIT_CONFLICT")
            if not repair:
                return config, state, True
            os.replace(pending, root / "STATE.json")
            state = value
        return config, state, False

    def _save(self, job_id, old, new):
        root = self._root(job_id)
        value = {**new, "revision": old["revision"] + 1, "previous_state_hash": stable_hash(old)}
        _put(root / "STATE.next.json", value)
        os.replace(root / "STATE.next.json", root / "STATE.json")
        return value

    def _summary(self, config, state, pending=False):
        now = self._now(config["profile"])
        results = state["stages"]
        unresolved = [key for key, value in results.items() if value["status"] == "STARTED"]
        completed = len(results) == len(config["stages"]) and all(value["status"] in TERMINAL for value in results.values())
        if pending or unresolved:
            status = "RECOVERY_REQUIRED"
        elif completed:
            status = "COMPLETED" if all(value["status"] == "COMPLETED" for value in results.values()) else "COMPLETED_WITH_ISSUES"
        elif state["paused"]:
            status = "PAUSED"
        elif now >= _stamp(config["expires_at"]):
            status = "EXPIRED"
        elif not state["started"]:
            status = "CREATED"
        elif state["calls_started"] >= config["max_calls"]:
            status = "CALL_LIMIT_EXHAUSTED"
        else:
            status = state["waiting"] or "READY"
        return {"job_id": config["job_id"], "status": status, "profile": config["profile"],
                "kind": config["kind"], "scope_ref": config["scope_ref"],
                "calls_started": state["calls_started"], "max_calls": config["max_calls"],
                "next_check_at": state["next_check_at"], "unresolved_stages": unresolved,
                "reason": state["waiting_reason"],
                "stage_results": deepcopy(results), "source_matches": config["source_identity"] == source_identity(),
                "grants_qualification": False, "background_enabled": False}

    def status(self, job_id):
        # 读取不会创建目录、锁或修复任务；有提交中间态则明确等待恢复。
        try:
            ObjectiveMutationLock.for_resource(self._root(job_id)).probe()
        except MutationBusyError:
            return {"job_id": job_id, "status": "RUNNING", "background_enabled": False}
        config, state, pending = self._load(job_id)
        return self._summary(config, state, pending)

    def _control(self, job_id, action):
        with ObjectiveMutationLock.for_resource(self._root(job_id)):
            config, state, _ = self._load(job_id, repair=True)
            if action != "pause" and config["source_identity"] != source_identity():
                raise ValueError("JOB_SOURCE_CHANGED")
            new = deepcopy(state)
            if action == "start":
                new["started"] = True
            new["paused"] = action == "pause"
            if new != state:
                state = self._save(job_id, state, new)
            return self._summary(config, state)

    def start(self, job_id):
        return self._control(job_id, "start")

    def pause(self, job_id):
        return self._control(job_id, "pause")

    def resume(self, job_id):
        return self._control(job_id, "resume")

    def _settle(self, job_id, state, key, result):
        if (not isinstance(result, dict) or set(result) != {"status", "receipt_ref", "reason"}
                or result["status"] not in {"COMPLETED", "FAILED", "UNRESOLVED"}
                or not isinstance(result["reason"], str) or len(result["reason"]) > 2000):
            raise ValueError("JOB_SERVICE_RESULT_INVALID")
        if result["status"] == "UNRESOLVED":
            if state['waiting_reason'] == result['reason']:
                return state
            new = deepcopy(state)
            new['waiting_reason'] = result['reason']
            return self._save(job_id, state, new)
        if not isinstance(result["receipt_ref"], dict) or not result["receipt_ref"]:
            raise ValueError("JOB_CANONICAL_RECEIPT_REQUIRED")
        new = deepcopy(state)
        new["stages"][key].update(deepcopy(result))
        new["waiting"], new["next_check_at"], new["waiting_reason"] = None, None, None
        return self._save(job_id, state, new)

    def tick(self, job_id):
        with ObjectiveMutationLock.for_resource(self._root(job_id)):
            config, state, _ = self._load(job_id, repair=True)
            if config["source_identity"] != source_identity():
                raise ValueError("JOB_SOURCE_CHANGED")
            now = self._now(config["profile"])
            if state["paused"] or not state["started"]:
                return self._summary(config, state)
            dispatcher = self.dispatchers.get(config["kind"])
            if dispatcher is None:
                return {**self._summary(config, state), "status": "ADAPTER_NOT_BOUND"}
            stages = {stage["key"]: stage for stage in config["stages"]}
            # 恢复仅对账原服务；即使到期或额度耗尽也可以保存已经发生的结果。
            for key, record in state["stages"].items():
                if record["status"] == "STARTED":
                    result = dispatcher.reconcile(deepcopy(config), deepcopy(stages[key]), record["operation_id"])
                    state = self._settle(job_id, state, key, result)
                    return self._summary(config, state)
            if now >= _stamp(config["expires_at"]) or state["calls_started"] >= config["max_calls"]:
                return self._summary(config, state)
            if state["next_check_at"] is not None and now < _stamp(state["next_check_at"]):
                return self._summary(config, state)
            for stage in config["stages"]:
                key = stage["key"]
                if key in state["stages"]:
                    continue
                if now < _stamp(stage["not_before"]):
                    new = {**state, "waiting": "WAITING_WINDOW", "next_check_at": stage["not_before"],
                           "waiting_reason": "STAGE_WINDOW_NOT_OPEN"}
                    state = self._save(job_id, state, new)
                    return self._summary(config, state)
                if now >= _stamp(stage["not_after"]):
                    new = deepcopy(state)
                    new["stages"][key] = {"status": "MISSED_WINDOW", "reason": "NO_BACKFILLED_OBSERVATION",
                                          "operation_id": stable_hash([stable_hash(config), key])}
                    new["waiting"], new["next_check_at"], new["waiting_reason"] = None, None, None
                    state = self._save(job_id, state, new)
                    return self._summary(config, state)
                readiness = dispatcher.readiness(deepcopy(config), deepcopy(stage))
                if (not isinstance(readiness, dict) or set(readiness) != {"status", "reason"}
                        or readiness["status"] not in WAITING | {"READY"} or not isinstance(readiness["reason"], str)):
                    raise ValueError("JOB_READINESS_INVALID")
                if readiness["status"] != "READY":
                    new = {**state, "waiting": readiness["status"],
                           "next_check_at": min(now + timedelta(seconds=60), _stamp(stage["not_after"])).isoformat(),
                           "waiting_reason": readiness["reason"]}
                    state = self._save(job_id, state, new)
                    return {**self._summary(config, state), "reason": readiness["reason"]}
                operation = stable_hash([stable_hash(config), key])
                new = deepcopy(state)
                new["calls_started"] += 1
                new["stages"][key] = {"status": "STARTED", "operation_id": operation, "started_at": now.isoformat()}
                new["waiting"], new["next_check_at"], new["waiting_reason"] = None, None, None
                state = self._save(job_id, state, new)
                # 执行异常保留 START；下一 tick 只能 reconcile，不能免费再次 execute。
                result = dispatcher.execute(deepcopy(config), deepcopy(stage), operation)
                state = self._settle(job_id, state, key, result)
                return self._summary(config, state)
            return self._summary(config, state)
