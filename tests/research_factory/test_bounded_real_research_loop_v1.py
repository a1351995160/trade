"""有界研究真实账户集成；输入和模型均明确为合成，不替代真实验收。"""
from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from bounded_research_fixture import synthetic_bundle
from chanlun_trader.research_factory import bounded_research_v1 as research
from chanlun_trader.research_factory import research_diagnostics_v1 as diagnostics
from chanlun_trader.research_factory.context import PerformanceBlindGuard
from scripts.s1_causal_price_strategy_v1 import Causal51VoteStrategy


def proposal(indicators=None, threshold=1):
    return {"hypothesis": "趋势延续的受限验证", "indicators": indicators or ["MACD"],
            "threshold": threshold, "change_reason": "依据允许反馈检验另一指标"}


class FakeInvoker:
    def __init__(self, proposals=None, crash=None):
        self.proposals = proposals or [proposal(), proposal(["RSI"])]
        self.contexts = []
        self.calls = 0
        self.crash = crash

    def invoke(self, context, *, staging_dir, timeout_seconds):
        PerformanceBlindGuard.assert_blind(context)
        artifact = staging_dir / "INVOCATION.json"
        if artifact.exists():
            return research._read(artifact)["proposal"]
        self.contexts.append(deepcopy(context))
        result = deepcopy(self.proposals[self.calls])
        self.calls += 1
        if self.crash == "before_result":
            raise RuntimeError("SIMULATED_MODEL_INTERRUPTION")
        research._put(artifact, {"proposal": result})
        if self.crash == "after_result":
            raise RuntimeError("SIMULATED_MODEL_INTERRUPTION")
        return result


def make_session(tmp_path, *, attempts=2, actions=research.ACTIONS):
    bundle = synthetic_bundle(Causal51VoteStrategy())
    manifest = {"profile": "SYNTHETIC", "input_identity": bundle["input_identity"], "events": []}
    session = research.BoundedResearchSessionV1.create(
        tmp_path / "bounded", objective_id="SYNTHETIC_BOUNDED_LOOP", input_manifest=manifest,
        approval_statement="合成闭环测试授权", max_attempts=attempts, actions=actions)
    calls = []
    def loader(recorded, strategy):
        assert recorded == manifest
        calls.append(strategy.strategy_id)
        return deepcopy(bundle), ()
    return session, loader, calls


def used(status, kind="objective"):
    return sum(row["used"] for row in status["budget"]["buckets"] if row["kind"] == kind)


def no_call(*args, **kwargs):
    raise AssertionError("已完成阶段不应重跑")


def assert_no_numeric_outcomes(value):
    if isinstance(value, dict):
        for nested in value.values():
            assert_no_numeric_outcomes(nested)
    elif isinstance(value, list):
        for nested in value:
            assert_no_numeric_outcomes(nested)
    else:
        assert not isinstance(value, (int, float)) or isinstance(value, bool)


def test_two_candidate_loop_records_parent_feedback_and_exact_budget(tmp_path):
    session, loader, calls = make_session(tmp_path)
    invoker = FakeInvoker()
    status = session.run(loader=loader, invoker=invoker)
    assert status["status"] == "ATTEMPT_BUDGET_EXHAUSTED"
    assert status["qualification"] == "NOT_ASSESSED"
    assert status["real_data"] is False and status["real_observation_days"] == 0
    assert used(status) == 3
    assert calls == ["REFERENCE", "CANDIDATE_001", "CANDIDATE_002"]
    assert invoker.calls == 2
    first, second = status["candidates"][1:]
    assert first["account_completed"] and second["account_completed"]
    assert first["parent"] is None and second["parent"] == "CANDIDATE_001"
    assert first["plan_id"] != second["plan_id"]
    assert invoker.contexts[1]["parent_candidate_id"] == "CANDIDATE_001"
    feedback = invoker.contexts[1]["failure_knowledge"]
    assert feedback and feedback[0]["source_history_hash"]
    assert_no_numeric_outcomes(feedback)
    for context in invoker.contexts:
        PerformanceBlindGuard.assert_blind(context)
    resumed = research.BoundedResearchSessionV1(session.root)
    class NoInvoker:
        invoke = staticmethod(no_call)
    assert resumed.run(loader=no_call, invoker=NoInvoker())["status"] == status["status"]
    assert used(resumed.status()) == 3


def test_completed_account_recovery_does_not_reload_or_regenerate(tmp_path, monkeypatch):
    session, loader, calls = make_session(tmp_path, attempts=1)
    invoker = FakeInvoker()
    session.tick(loader=loader, invoker=invoker)
    original = diagnostics.diagnose
    def interrupted(*args, **kwargs):
        raise RuntimeError("SIMULATED_AFTER_ACCOUNT")
    monkeypatch.setattr(diagnostics, "diagnose", interrupted)
    with pytest.raises(RuntimeError, match="SIMULATED_AFTER_ACCOUNT"):
        session.tick(loader=loader, invoker=invoker)
    assert session.path("CANDIDATE_001", "RESULT.json").exists()
    assert not session.path("CANDIDATE_001", "DECISION.json").exists()
    monkeypatch.setattr(diagnostics, "diagnose", original)
    invoker.invoke = no_call
    resumed = research.BoundedResearchSessionV1(session.root)
    result = resumed.run(loader=no_call, invoker=invoker)
    assert result["status"] == "ATTEMPT_BUDGET_EXHAUSTED"
    assert used(result) == 2 and len(calls) == 2


def test_missing_backtest_authorization_consumes_nothing(tmp_path):
    session, _, _ = make_session(tmp_path, actions=("GENERATE", "MATERIALIZE", "FEEDBACK"))
    result = session.run(loader=no_call, invoker=FakeInvoker())
    assert result["status"] == "BLOCKED"
    assert "WAITING_AUTHORIZATION:BACKTEST" in result["reason"]
    assert used(result) == 0


def test_unsupported_and_duplicate_candidates_are_charged_without_execution(tmp_path):
    session, loader, calls = make_session(tmp_path, attempts=3)
    invoker = FakeInvoker([proposal(), proposal(), proposal(["NOT_SUPPORTED"])])
    result = session.run(loader=loader, invoker=invoker)
    assert result["status"] == "ATTEMPT_BUDGET_EXHAUSTED"
    assert used(result) == 4
    assert calls == ["REFERENCE", "CANDIDATE_001"]
    assert [row.get("reason") for row in result["candidates"][1:]] == [
        "EXPLORATION_RECORDED", "DUPLICATE_RULE", "UNSUPPORTED_CANDIDATE"]


def test_interrupted_model_is_charged_and_cannot_be_reissued(tmp_path):
    session, loader, _ = make_session(tmp_path, attempts=1)
    invoker = FakeInvoker(crash="before_result")
    session.tick(loader=loader, invoker=invoker)
    with pytest.raises(RuntimeError, match="SIMULATED_MODEL_INTERRUPTION"):
        session.tick(loader=loader, invoker=invoker)
    assert invoker.calls == 1 and used(session.status()) == 2
    invoker.invoke = no_call
    result = research.BoundedResearchSessionV1(session.root).run(loader=no_call, invoker=invoker)
    assert "INTERRUPTED_MODEL_NO_AUTOMATIC_RETRY" in result["reason"]
    assert used(result) == 2


def test_persisted_model_response_recovers_without_another_request(tmp_path):
    session, loader, _ = make_session(tmp_path, attempts=1)
    invoker = FakeInvoker(crash="after_result")
    session.tick(loader=loader, invoker=invoker)
    with pytest.raises(RuntimeError, match="SIMULATED_MODEL_INTERRUPTION"):
        session.tick(loader=loader, invoker=invoker)
    result = research.BoundedResearchSessionV1(session.root).run(loader=loader, invoker=invoker)
    assert result["status"] == "ATTEMPT_BUDGET_EXHAUSTED"
    assert invoker.calls == 1 and used(result) == 2


def test_interrupted_account_is_not_retried_or_refunded(tmp_path):
    session, _, _ = make_session(tmp_path, attempts=1)
    calls = []
    def broken_loader(*args):
        calls.append(1)
        raise RuntimeError("SIMULATED_ACCOUNT_INTERRUPTION")
    first = session.run(loader=broken_loader, invoker=FakeInvoker())
    assert first["status"] == "BLOCKED" and used(first) == 1
    resumed = research.BoundedResearchSessionV1(session.root).run(loader=no_call, invoker=FakeInvoker())
    assert "INTERRUPTED_ACCOUNT_NO_AUTOMATIC_RETRY" in resumed["reason"]
    assert used(resumed) == 1 and len(calls) == 1


@pytest.mark.parametrize("boundary", ["revoked", "expired", "source_changed", "plan_scope"])
def test_scope_boundaries_reject_before_loading(tmp_path, monkeypatch, boundary):
    session, _, _ = make_session(tmp_path)
    if boundary == "revoked":
        session.revoke("用户停止")
        expected = "REVOKED"
    elif boundary == "expired":
        expiration = datetime.fromisoformat(session.scope()["expires_at"])
        monkeypatch.setattr(research, "_now", lambda: expiration + timedelta(seconds=1))
        expected = "DEADLINE_REACHED"
    elif boundary == "source_changed":
        monkeypatch.setattr(research, "source_identity", lambda: "changed")
        expected = "SOURCE_CHANGED"
    else:
        with pytest.raises(ValueError, match="PATH_REDIRECTED"):
            session.path("..", "unapproved.json")
        return
    result = session.run(loader=no_call, invoker=FakeInvoker())
    assert result["status"] == "BLOCKED" and expected in result["reason"]
    assert used(result) == 0


def test_corrupted_session_is_rejected(tmp_path):
    session, _, _ = make_session(tmp_path)
    path = session.path("SESSION.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    value["max_attempts"] = 5
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="ARTIFACT_CORRUPT"):
        session.tick(loader=no_call, invoker=FakeInvoker())


def test_loader_input_change_is_charged_but_cannot_execute(tmp_path):
    session, loader, _ = make_session(tmp_path)
    def changed_loader(manifest, strategy):
        bundle, events = loader(manifest, strategy)
        bundle["input_identity"] = "changed"
        return bundle, events
    result = session.run(loader=changed_loader, invoker=FakeInvoker())
    assert result["status"] == "BLOCKED" and "DATA_CHANGED" in result["reason"]
    assert used(result) == 1
    assert not session.path("REFERENCE", "RESULT.json").exists()


def test_revoke_during_running_tick_is_persisted_and_observed(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    session, loader, calls = make_session(tmp_path)
    entered, released = Event(), Event()
    def waiting_loader(*args):
        entered.set()
        assert released.wait(20)
        return loader(*args)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(session.run, loader=waiting_loader, invoker=FakeInvoker())
        try:
            assert entered.wait(20)
            research.BoundedResearchSessionV1(session.root).revoke("用户停止当前运行")
            assert session.path("REVOKED.json").exists()
            assert session.status()["reason"] == "BOUNDED_REVOKED"
        finally:
            released.set()
        result = future.result(timeout=30)
    assert result["status"] == "BLOCKED" and result["reason"] == "BOUNDED_REVOKED"
    assert used(result) == 1 and calls == ["REFERENCE"]
    assert not session.path("REFERENCE", "RESULT.json").exists()
    assert research.BoundedResearchSessionV1(session.root).status()["reason"] == "BOUNDED_REVOKED"


@pytest.mark.parametrize("failed_name", ["REFERENCE", "CANDIDATE_001"])
@pytest.mark.parametrize("fault, state", [("unreconciled", "ACCOUNT_UNRECONCILED"),
                                         ("incomplete", "ACCOUNT_INCOMPLETE")])
def test_invalid_account_stops_search_and_remains_blocked_after_restart(tmp_path, monkeypatch, failed_name, fault, state):
    from chanlun_trader.research_factory import strategy_interface_v1 as interface

    session, loader, calls = make_session(tmp_path)
    original = interface.run
    def invalid_account(strategy, *args, **kwargs):
        result = original(strategy, *args, **kwargs)
        if strategy.strategy_id == failed_name:
            if fault == "unreconciled":
                result["chain"]["issues"] = ["CASH_MISMATCH"]
                result["chain"]["status"] = "RECONCILIATION_FAILED"
            else:
                result["chain"]["n_account_days"] += 1
        return result
    monkeypatch.setattr(interface, "run", invalid_account)
    invoker = FakeInvoker()
    result = session.run(loader=loader, invoker=invoker)
    expected = "BOUNDED_" + state + ":" + failed_name
    assert result["status"] == "BLOCKED" and result["reason"] == expected
    assert calls == (["REFERENCE"] if failed_name == "REFERENCE" else ["REFERENCE", "CANDIDATE_001"])
    assert invoker.calls == (0 if failed_name == "REFERENCE" else 1)
    assert research._read(session.path(failed_name, "DIAGNOSTIC.json"))["account_state"] == state
    assert not session.path(failed_name, "DECISION.json").exists()
    assert not session.path("STOP.json").exists()
    resumed = research.BoundedResearchSessionV1(session.root)
    assert resumed.status()["reason"] == expected
    invoker.invoke = no_call
    assert resumed.run(loader=no_call, invoker=invoker)["reason"] == expected
    assert used(resumed.status()) == len(calls)


def test_confirmation_without_account_budget_recovers_same_receipt(tmp_path, monkeypatch):
    from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1

    session, loader, calls = make_session(tmp_path, attempts=1)
    original = research.SearchBudgetRegistryV1.register
    def interrupted(self, kind, key, limit):
        if kind == StrategyBatchGovernanceV1.budget_kind:
            raise RuntimeError("CRASH_AFTER_CONFIRMATION")
        return original(self, kind, key, limit)
    monkeypatch.setattr(research.SearchBudgetRegistryV1, "register", interrupted)
    with pytest.raises(RuntimeError, match="CRASH_AFTER_CONFIRMATION"):
        session.tick(loader=loader, invoker=FakeInvoker())
    receipt_path = session.path("REFERENCE", "governance", "CONFIRMATION.json")
    receipt_before = receipt_path.read_bytes()
    assert not session.path("REFERENCE", "governance", "REFERENCE_START.json").exists()
    assert calls == [] and used(session.status()) == 1
    monkeypatch.setattr(research.SearchBudgetRegistryV1, "register", original)
    resumed = research.BoundedResearchSessionV1(session.root)
    result = resumed.run(loader=loader, invoker=FakeInvoker())
    assert result["status"] == "ATTEMPT_BUDGET_EXHAUSTED"
    assert calls == ["REFERENCE", "CANDIDATE_001"] and used(result) == 2
    assert receipt_path.read_bytes() == receipt_before
    account_buckets = [row for row in result["budget"]["buckets"] if row["kind"] == StrategyBatchGovernanceV1.budget_kind]
    assert len(account_buckets) == 2 and all(row["used"] == row["limit"] == 1 for row in account_buckets)


@pytest.mark.parametrize("failure", ["model", "account", "expired"])
def test_blocked_status_is_derived_after_restart(tmp_path, monkeypatch, failure):
    session, loader, _ = make_session(tmp_path, attempts=1)
    invoker = FakeInvoker(crash="before_result" if failure == "model" else None)
    if failure == "account":
        def loader(*args):
            raise RuntimeError("ACCOUNT_FAILURE")
    elif failure == "expired":
        expiration = datetime.fromisoformat(session.scope()["expires_at"])
        monkeypatch.setattr(research, "_now", lambda: expiration + timedelta(seconds=1))
    result = session.run(loader=loader, invoker=invoker)
    assert result["status"] == "BLOCKED"
    restarted = research.BoundedResearchSessionV1(session.root).status()
    assert restarted["status"] == "BLOCKED" and restarted["reason"] == result["reason"]
    assert not session.path("STOP.json").exists()


def test_governance_start_without_outer_marker_is_reported_interrupted(tmp_path, monkeypatch):
    session, loader, calls = make_session(tmp_path, attempts=1)
    original = research._put
    def interrupted(path, payload):
        if path.name == "EXECUTION_STARTED.json":
            raise RuntimeError("CRASH_AFTER_GOVERNANCE_START")
        return original(path, payload)
    monkeypatch.setattr(research, "_put", interrupted)
    with pytest.raises(RuntimeError, match="CRASH_AFTER_GOVERNANCE_START"):
        session.tick(loader=loader, invoker=FakeInvoker())
    assert session.path("REFERENCE", "governance", "REFERENCE_START.json").exists()
    assert not session.path("REFERENCE", "EXECUTION_STARTED.json").exists()
    assert calls == [] and used(session.status()) == 1
    monkeypatch.setattr(research, "_put", original)
    resumed = research.BoundedResearchSessionV1(session.root)
    assert resumed.status()["status"] == "BLOCKED"
    assert resumed.status()["reason"] == "BOUNDED_INTERRUPTED_ACCOUNT_NO_AUTOMATIC_RETRY"
    assert resumed.run(loader=no_call, invoker=FakeInvoker())["reason"] == resumed.status()["reason"]
    assert used(resumed.status()) == 1


def test_invalid_durable_model_success_is_still_blocked_after_restart(tmp_path):
    session, loader, _ = make_session(tmp_path, attempts=1)
    class InvalidDurableModel:
        def invoke(self, context, *, staging_dir, timeout_seconds):
            research._put(staging_dir / "SUCCESS.json", {"response_text": "not JSON"})
            raise RuntimeError("BOUNDED_MODEL_INVALID_JSON")
    status = session.run(loader=loader, invoker=InvalidDurableModel())
    assert status["status"] == "BLOCKED" and status["reason"] == "BOUNDED_MODEL_INVALID_JSON"
    resumed = research.BoundedResearchSessionV1(session.root)
    assert resumed.status()["reason"] == status["reason"]
    assert not session.path("STOP.json").exists()
