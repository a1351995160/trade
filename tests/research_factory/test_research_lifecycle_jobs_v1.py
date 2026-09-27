"""调度合成测试；fake 服务回执不表示正式研究或策略资格。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from chanlun_trader.research_factory.bounded_research_v1 import _put, _read
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.mutation_boundary import MutationBusyError
from chanlun_trader.research_factory.research_lifecycle_jobs_v1 import LifecycleJobsV1
import chanlun_trader.research_factory.research_lifecycle_jobs_v1 as jobs_module


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 28, 1, 30, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


class Adapter:
    def __init__(self):
        self.reads = 0
        self.calls = []
        self.reconciles = []
        self.ready = "READY"
        self.fail = False
        self.receipts = {}

    def readiness(self, config, stage):
        assert config["profile"] == "SYNTHETIC"
        self.reads += 1
        return {"status": self.ready, "reason": "synthetic test"}

    def execute(self, config, stage, operation_id):
        self.calls.append(operation_id)
        result = {"status": "COMPLETED", "receipt_ref": {"synthetic": True, "operation_id": operation_id}, "reason": "test only"}
        self.receipts[operation_id] = result
        if self.fail:
            raise RuntimeError("simulated crash after service commit")
        return result

    def reconcile(self, config, stage, operation_id):
        self.reconciles.append(operation_id)
        return self.receipts.get(operation_id, {"status": "UNRESOLVED", "receipt_ref": None, "reason": "no canonical receipt"})


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "source_identity", lambda: "f" * 64)
    clock, adapter = Clock(), Adapter()
    service = LifecycleJobsV1(tmp_path / "jobs", dispatchers={"CAPTURE_OPEN": adapter, "RESEARCH": adapter}, synthetic_clock=clock)
    return service, clock, adapter


def config(clock, *, count=1, max_calls=4):
    stages = [{"key": f"OPEN_{index}", "trade_date": 20260928,
               "not_before": (clock.now + timedelta(minutes=index * 2)).isoformat(),
               "not_after": (clock.now + timedelta(minutes=index * 2 + 1)).isoformat()}
              for index in range(count)]
    return {"scope_ref": {"objective_id": "objective", "session_id": "session", "scope_hash": "a" * 64},
            "kind": "CAPTURE_OPEN", "config_hash": "b" * 64,
            "expires_at": (clock.now + timedelta(hours=1)).isoformat(), "max_calls": max_calls,
            "stages": stages, "profile": "SYNTHETIC", "trading_calendar": [20260928]}


def test_start_is_not_background_execution_and_completed_tick_is_idempotent(runtime):
    service, clock, adapter = runtime
    assert service.create("job", **config(clock))["status"] == "CREATED"
    assert service.start("job")["status"] == "READY" and not adapter.calls
    result = service.tick("job")
    assert result["status"] == "COMPLETED" and result["calls_started"] == 1
    assert not result["grants_qualification"] and not result["background_enabled"]
    assert service.tick("job") == result and len(adapter.calls) == 1


def test_waiting_does_not_consume_calls_or_busy_poll(runtime):
    service, clock, adapter = runtime
    service.create("job", **config(clock))
    service.start("job")
    adapter.ready = "WAITING_DATA"
    result = service.tick("job")
    assert result["status"] == "WAITING_DATA" and result["calls_started"] == 0
    for _ in range(5):
        assert service.tick("job")["status"] == "WAITING_DATA"
    assert adapter.reads == 1 and not adapter.calls
    assert service.status("job")["reason"] == "synthetic test"


@pytest.mark.parametrize("waiting", ["WAITING_QUALIFICATION", "BUDGET_EXHAUSTED"])
def test_original_service_wait_states_are_preserved(runtime, waiting):
    service, clock, adapter = runtime
    service.create("job", **config(clock))
    service.start("job")
    adapter.ready = waiting
    assert service.tick("job")["status"] == waiting
    assert not adapter.calls


def test_missed_window_never_backfills_observation(runtime):
    service, clock, adapter = runtime
    service.create("job", **config(clock))
    service.start("job")
    clock.now += timedelta(minutes=2)
    result = service.tick("job")
    assert result["status"] == "COMPLETED_WITH_ISSUES"
    assert result["stage_results"]["OPEN_0"]["status"] == "MISSED_WINDOW"
    assert result["calls_started"] == 0 and not adapter.calls


def test_pause_resume_expiry_and_limit_remain_distinct(runtime):
    service, clock, adapter = runtime
    service.create("job", **config(clock, count=2, max_calls=1))
    service.start("job")
    assert service.pause("job")["status"] == "PAUSED"
    assert service.tick("job")["status"] == "PAUSED" and not adapter.calls
    service.resume("job")
    assert service.tick("job")["status"] == "CALL_LIMIT_EXHAUSTED"
    clock.now += timedelta(hours=2)
    assert service.tick("job")["status"] == "EXPIRED" and len(adapter.calls) == 1


def test_committed_service_result_reconciles_after_crash_without_execute_retry(runtime):
    service, clock, adapter = runtime
    service.create("job", **config(clock))
    service.start("job")
    adapter.fail = True
    with pytest.raises(RuntimeError, match="crash"):
        service.tick("job")
    assert service.status("job")["status"] == "RECOVERY_REQUIRED"
    clock.now += timedelta(hours=2)
    recovered = LifecycleJobsV1(service.root, dispatchers={"CAPTURE_OPEN": adapter}, synthetic_clock=clock)
    assert recovered.tick("job")["status"] == "COMPLETED"
    assert len(adapter.calls) == 1 and adapter.reconciles == adapter.calls


def test_missing_service_receipt_never_receives_free_retry(runtime):
    service, clock, adapter = runtime
    service.create("job", **config(clock))
    service.start("job")
    adapter.fail = True
    with pytest.raises(RuntimeError):
        service.tick("job")
    adapter.receipts.clear()
    assert service.tick("job")["status"] == "RECOVERY_REQUIRED"
    assert service.tick("job")["calls_started"] == 1 and len(adapter.calls) == 1


def test_crash_after_state_prepare_promotes_exact_transition_only_on_mutation(runtime):
    service, clock, _ = runtime
    service.create("job", **config(clock))
    path = service.root / "job"
    old = _read(path / "STATE.json")
    new = {**old, "revision": 1, "previous_state_hash": stable_hash(old), "started": True}
    _put(path / "STATE.next.json", new)
    assert service.status("job")["status"] == "RECOVERY_REQUIRED"
    assert _read(path / "STATE.json") == old
    service.start("job")
    assert _read(path / "STATE.json") == new and not (path / "STATE.next.json").exists()


def test_source_upgrade_is_not_silent_migration(runtime, monkeypatch):
    service, clock, adapter = runtime
    service.create("job", **config(clock))
    service.start("job")
    monkeypatch.setattr(jobs_module, "source_identity", lambda: "e" * 64)
    assert not service.status("job")["source_matches"]
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        service.tick("job")
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        service.resume("job")
    assert service.pause("job")["status"] == "PAUSED" and not adapter.calls


def test_concurrent_trigger_does_not_dispatch_twice(runtime):
    service, clock, adapter = runtime
    service.create("job", **config(clock))
    service.start("job")
    entered, released = threading.Event(), threading.Event()
    original = adapter.execute
    def blocking(*args):
        entered.set()
        assert released.wait(timeout=5)
        return original(*args)
    adapter.execute = blocking
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.tick, "job")
        assert entered.wait(timeout=5)
        try:
            assert service.status("job")["status"] == "RUNNING"
            with pytest.raises(MutationBusyError):
                service.tick("job")
        finally:
            released.set()
        assert pending.result()["status"] == "COMPLETED"
    assert len(adapter.calls) == 1


def test_production_clock_cannot_be_injected_and_nontrading_stage_rejected(runtime):
    service, clock, _ = runtime
    args = config(clock)
    args["profile"] = "REAL"
    with pytest.raises(ValueError, match="REAL_CLOCK"):
        service.create("job", **args)
    args = config(clock)
    args["stages"][0]["trade_date"] = 20260927
    with pytest.raises(ValueError, match="NOT_TRADING"):
        service.create("job", **args)
    assert not service.root.exists()


@pytest.mark.parametrize("mutation", ["shell", "duplicate", "bad_limit", "bad_scope", "future_capture_day"])
def test_configuration_is_bounded_and_has_no_executable_payload(runtime, mutation):
    service, clock, _ = runtime
    args = config(clock)
    if mutation == "shell":
        args["kind"] = "powershell"
    elif mutation == "duplicate":
        args["stages"] *= 2
    elif mutation == "bad_limit":
        args["max_calls"] = True
    elif mutation == "bad_scope":
        args["scope_ref"]["scope_hash"] = "not authority"
    else:
        args["stages"][0]["not_before"] = (clock.now + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError):
        service.create("job", **args)


def test_readonly_status_does_not_create_root(runtime):
    service, _, _ = runtime
    with pytest.raises(FileNotFoundError):
        service.status("unknown")
    assert not service.root.exists()


def test_service_failure_is_engineering_result_not_strategy_rejection(runtime):
    service, clock, adapter = runtime
    service.create("job", **config(clock))
    service.start("job")
    adapter.execute = lambda *_: {"status": "FAILED", "receipt_ref": {"synthetic": True}, "reason": "ACCOUNT_ENGINE_FAILURE"}
    result = service.tick("job")
    assert result["status"] == "COMPLETED_WITH_ISSUES"
    assert result["stage_results"]["OPEN_0"]["reason"] == "ACCOUNT_ENGINE_FAILURE"
    assert not result["grants_qualification"]
