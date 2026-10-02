"""对照器不能以忽略经济差异、跳过证据或伪造授权来得到通过。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts import run_baostock_universe_parity_v1 as parity


def _result(prefix="OLD"):
    intent_id, plan_id = prefix + "_INTENT", prefix + "_PLAN"
    strategy_id = "FIXED_BASE"
    intent = {"intent_id": intent_id, "strategy_id": strategy_id, "symbol": "000001.SZ",
              "side": "BUY", "priority": 0, "target_weight": .45, "reason": "BUY"}
    decision = {key: value for key, value in intent.items() if key not in {"intent_id", "priority"}}
    plan = {"plan_id": plan_id, "decision_at": "2022-07-06T15:30:00+08:00", "next_session": 20220707,
            "intents": [intent], "excluded": [], "holdings": [], "status": "PLANNED",
            "usage_qualified": False, "real_execution_authorized": False, "limits": ["SIMULATED_EXECUTION_ONLY"]}
    trade = {"symbol": "000001.SZ", "strategy_id": strategy_id, "side": "BUY", "quantity": 100,
             "price": 10., "fee": 5., "fill_time": "2022-07-07T09:30:00+08:00"}
    order = {"intent_id": intent_id, "signal_id": intent_id,
             "metadata": {"plan_id": plan_id, "target_weight": .45}, "symbol": "000001.SZ",
             "quantity": 100, "reason": "FORWARD_OBSERVED_OPEN", "status": "FILLED"}
    economic = {"orders": {"ord-1": order}, "cash": 48995., "trades": [trade], "payments": [],
                "events": [{"intent_id": intent_id, "signal_id": intent_id, "order_status": "FILLED",
                            "timestamp": trade["fill_time"], "message": "filled 100 @ 10.0"}],
                "receivables": {}, "dividend_tax_withheld": 0., "action_income": 0.,
                "snapshots": [{"timestamp": "2022-07-07T15:00:00+08:00", "cash": 48995., "equity": 49995.}]}
    return {"strategy_qualified": False, "independent_confirmation_eligible": False,
            "reconciliation": {"passed": True}, "decisions": [{"date": 20220706, "decisions": [decision], "plan": plan}],
            "daily_accounts": [{"date": 20220707, "cash": 48995., "equity": 49995.,
                "positions": [{"strategy_id": strategy_id, "symbol": "000001.SZ", "quantity": 100}]}],
            "fills": [trade], "final_account_checkpoint": {"economic": economic, "skipped_intents": [], "invariant_errors": []},
            "metrics": {"net_return": -.0001, "total_fees": 5., "trade_count": 1, "max_drawdown": .0001}}


def _inputs():
    daily = pd.DataFrame([{"symbol": "000001.SZ", "date": date, "open": 10., "high": 11., "low": 9.,
                           "close": 10., "prev_close": 10., "volume": 1000., "amount": 10000.}
                          for date in (20220705, 20220706, 20220707)])
    turn = pd.DataFrame([{"symbol": "000001.SZ", "date": date, "volume": 1000., "tradestatus": 1, "turn": .1}
                         for date in (20220705, 20220706, 20220707)])
    states = pd.DataFrame([{"symbol": "000001.SZ", "trade_date": date, "listed": True, "delisted": False,
                           "universe_member": True, "eligibility_status": "ELIGIBLE", "st_status": "NORMAL",
                           "suspension_status": "TRADING", "board": "SZ_MAIN"}
                          for date in (20220706, 20220707)])
    return {"daily": daily, "turn": turn, "states": states, "events": [],
            "calendar": [20220705, 20220706, 20220707], "source_hashes": {"daily.json": "a" * 64,
                                                                         "execution_profile": "HISTORICAL_MODELED"}}


def _preview(new=False):
    request = {"strategy_id": "FIXED", "rule": {"version": "RESEARCH_RULE_STRATEGY_V3"},
               "symbols": ["000001.SZ"], "feature_start": 20220407, "account_start": 20220706,
               "account_end": 20221230, "initial_cash": 50000, "max_positions": 1,
               "max_symbol_exposure_bps": 5000, "costs": ["BASE", "STRESS"], "benchmark": "NONE",
               "purpose": "EXPLORATORY", "authorization_ref": "new" if new else "old"}
    if new:
        request["version"] = "FULL_UNIVERSE_SUBMISSION_V1"
    return {"request": request, "rule_identity": "a" * 64, "preview_identity": "b" * 64 if new else "c" * 64}


def test_only_plan_and_intent_binding_hashes_are_normalized():
    old, new = _result(), _result("NEW")
    old["daily_accounts"][0].update(passed=True, cash_difference=0., equity_difference=0.)
    new["daily_accounts"][0]["stale_valuations"] = []
    result = parity.compare_parity_results_v1(old, new)
    assert result["passed"] is True
    assert result["old_economic_identity"] == result["new_economic_identity"]
    assert result["strategy_qualified"] is False
    assert result["independent_validation"] == "NOT_RUN"


@pytest.mark.parametrize("change,expected", [
    (lambda value: value["decisions"][0]["decisions"][0].update(reason="SECURITY_NOT_ELIGIBLE"), "decisions[0].decisions[0].reason"),
    (lambda value: value["decisions"][0]["decisions"][0].update(target_weight=.4), "decisions[0].decisions[0].target_weight"),
    (lambda value: value["daily_accounts"][0].update(cash=48994.999), "daily_accounts[0].cash"),
    (lambda value: value["fills"][0].update(fee=6.), "fills[0].fee"),
    (lambda value: value["final_account_checkpoint"]["economic"].update(dividend_tax_withheld=10.), "economic.dividend_tax_withheld"),
    (lambda value: value["final_account_checkpoint"]["economic"]["snapshots"][0].update(equity=49994.), "economic.snapshots[0].equity"),
    (lambda value: value["final_account_checkpoint"]["economic"]["orders"]["ord-1"].update(status="REJECTED", reason="LIMIT_UP_LOCKED"), "economic.orders.ord-1.status"),
    (lambda value: value["final_account_checkpoint"]["economic"]["events"][0].update(message="rejected: LIMIT_UP_LOCKED"), "economic.events[0].message"),
])
def test_economic_and_reason_differences_are_reported(change, expected):
    old, new = _result(), _result("NEW")
    change(new)
    result = parity.compare_parity_results_v1(old, new)
    assert result["status"] == "MISMATCH"
    assert expected in result["mismatch_paths"]


def test_rejections_are_bound_to_real_decision_and_reasons_preserved():
    old, new = _result(), _result("NEW")
    old["final_account_checkpoint"]["skipped_intents"] = [{"intent_id": "OLD_INTENT", "reason": "NO_PERMITTED_QUANTITY"}]
    new["final_account_checkpoint"]["skipped_intents"] = [{"intent_id": "NEW_INTENT", "reason": "NO_PERMITTED_QUANTITY"}]
    assert parity.compare_parity_results_v1(old, new)["passed"]
    new["final_account_checkpoint"]["skipped_intents"][0]["reason"] = "SECURITY_NOT_TRADABLE"
    assert "skipped_intents[0].reason" in parity.compare_parity_results_v1(old, new)["mismatch_paths"]
    new["final_account_checkpoint"]["skipped_intents"][0]["intent_id"] = "invented"
    with pytest.raises(ValueError, match="UNBOUND_SKIPPED_INTENT"):
        parity.compare_parity_results_v1(old, new)


@pytest.mark.parametrize("field,value", [("strategy_qualified", True), ("independent_confirmation_eligible", True),
                                         ("reconciliation", {"passed": False})])
def test_results_must_be_reconciled_and_cannot_grant_qualification(field, value):
    result = _result()
    result[field] = value
    with pytest.raises(ValueError, match="UNQUALIFIED_RECONCILED"):
        parity.compare_parity_results_v1(_result(), result)


def test_preview_scope_rejects_different_rule_dates_funds_or_pool():
    for key, value in (("rule", {"version": "different"}), ("symbols", ["600000.SH"]),
                       ("account_end", 20221229), ("initial_cash", 100000), ("max_positions", 2)):
        old, new = _preview(), _preview(True)
        new["request"][key] = value
        with pytest.raises(ValueError, match="REQUESTS_DIFFER"):
            parity.validate_parity_previews_v1(old, new)


def test_inputs_require_original_bytes_not_just_same_values():
    old, new = _inputs(), _inputs()
    new["source_hashes"] = {"copied_vendor_original": "a" * 64, "listing": "b" * 64}
    new["states"] = pd.concat([new["states"], pd.DataFrame([{**new["states"].iloc[0].to_dict(), "trade_date": 20220705}])])
    result = parity.compare_parity_inputs_v1(old, new, {"account_start": 20220706})
    assert result["passed"] and result["original_source_count"] == 1 and result["additional_source_count"] == 1
    new["source_hashes"]["copied_vendor_original"] = "c" * 64
    result = parity.compare_parity_inputs_v1(old, new, {"account_start": 20220706})
    assert not result["passed"]
    assert result["mismatches"][0]["path"] == "source_hashes.original_bytes"


def test_inputs_detect_changed_price_state_event_and_turn():
    for part, change, path in (
        ("daily", lambda frame: frame.loc.__setitem__((0, "open"), 10.1), "daily[0].open"),
        ("states", lambda frame: frame.loc.__setitem__((0, "st_status"), "ST"), "states[0].st_status"),
        ("turn", lambda frame: frame.loc.__setitem__((0, "turn"), .2), "turn[0].turn"),
    ):
        old, new = _inputs(), _inputs()
        change(new[part])
        result = parity.compare_parity_inputs_v1(old, new, {"account_start": 20220706})
        assert path in [row["path"] for row in result["mismatches"]]


class _Service:
    """仅用于合成测试，记录公共调用，不生成任何真实研究授权或回执。"""
    def __init__(self, root, calls, *, new=False, blocked=False):
        self.root, self.calls, self.new, self.blocked = root, calls, new, blocked
        self.preview_value = _preview(new)

    def preview(self, request):
        return self.preview_value

    def authority(self, reference):
        return {"objective_id": "synthetic_engineering"}

    def freeze(self, request, identity):
        side = "new" if self.new else "old"
        self.calls.append("freeze_" + side)
        return {"task_id": side, "job_path": side}

    def approve(self, task_id, identity):
        self.calls.append("approve_" + task_id)

    def start(self, task_id):
        self.calls.append("start_" + task_id)
        return {"status": "EVIDENCE_BLOCKED" if self.blocked else "ACCOUNT_VERIFIED", "verification": {
            "advance_allowed": not self.blocked, "items": {
                "BASE": {"advance_allowed": not self.blocked, "status": "FAIL" if self.blocked else "PASS"},
                "STRESS": {"advance_allowed": not self.blocked, "status": "FAIL" if self.blocked else "PASS"}}}}


def test_preview_does_not_freeze_approve_or_execute(tmp_path):
    calls = []
    result = parity.run_baostock_universe_parity_v1(_Service(tmp_path / "old", calls),
        _Service(tmp_path / "new", calls, new=True), {}, {}, tmp_path / "comparison")
    assert result["status"] == "PREVIEW_ONLY"
    assert result["budget_created"] is False and calls == []


def test_input_mismatch_stops_before_public_approval(tmp_path, monkeypatch):
    calls = []
    old, new = _inputs(), _inputs()
    new["daily"].loc[0, "open"] = 10.1
    monkeypatch.setattr(parity, "_input_for_task", lambda task: old if task["task_id"] == "old" else new)
    result = parity.run_baostock_universe_parity_v1(_Service(tmp_path / "old", calls),
        _Service(tmp_path / "new", calls, new=True), {}, {}, tmp_path / "comparison", execute=True)
    assert result["status"] == "INPUT_MISMATCH"
    assert calls == ["freeze_old", "freeze_new"]


def test_all_real_service_evidence_required_before_result_comparison(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(parity, "_input_for_task", lambda task: _inputs())
    monkeypatch.setattr(parity, "_results", lambda task: pytest.fail("不能读取证据未通过的绩效"))
    result = parity.run_baostock_universe_parity_v1(_Service(tmp_path / "old", calls, blocked=True),
        _Service(tmp_path / "new", calls, new=True), {}, {}, tmp_path / "comparison", execute=True)
    assert result["status"] == "EVIDENCE_BLOCKED"
    assert calls == ["freeze_old", "freeze_new", "approve_old", "start_old"]


def test_two_scenarios_must_both_pass(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(parity, "_input_for_task", lambda task: _inputs())
    def results(task):
        values = {cost: _result("NEW" if task["task_id"] == "new" else "OLD") for cost in ("BASE", "STRESS")}
        if task["task_id"] == "new":
            values["STRESS"]["daily_accounts"][0]["cash"] -= .001
        return values
    monkeypatch.setattr(parity, "_results", results)
    result = parity.run_baostock_universe_parity_v1(_Service(tmp_path / "old", calls),
        _Service(tmp_path / "new", calls, new=True), {}, {}, tmp_path / "comparison", execute=True)
    assert result["status"] == "PARITY_MISMATCH"
    assert result["comparisons"]["BASE"]["passed"] and not result["comparisons"]["STRESS"]["passed"]
    assert calls == ["freeze_old", "freeze_new", "approve_old", "start_old", "approve_new", "start_new"]
    assert json.loads((tmp_path / "comparison/PARITY_RESULT.json").read_text(encoding="utf-8"))["strategy_qualified"] is False


def test_result_loader_uses_registered_cost_model_and_three_way_hash(tmp_path):
    from chanlun_trader.research_factory.formal_account_backend_v1 import normalized_costs
    from chanlun_trader.research_factory.exploration_governance import immutable
    plans, items = {}, {}
    for cost in ("BASE", "STRESS"):
        name = "FIXED_" + cost
        path = tmp_path / (name + "_RESULT.json")
        immutable(path, _result(cost))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        settled = tmp_path / (name + "_SETTLEMENT.json")
        immutable(settled, {"result_sha256": digest})
        plans[name] = {"backend": {"costs": normalized_costs(cost)}}
        items[name] = {"result": str(path), "settlement": str(settled), "sha256": digest}
    immutable(tmp_path / "JOB.json", {"root": str(tmp_path), "plans": plans})
    immutable(tmp_path / "RESULTS_INDEX.json", {"items": items})
    assert set(parity._results({"job_path": str(tmp_path / "JOB.json")})) == {"BASE", "STRESS"}
    (tmp_path / "FIXED_STRESS_RESULT.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="SETTLEMENT_CONFLICT"):
        parity._results({"job_path": str(tmp_path / "JOB.json")})


def _tax_audit_pair():
    from chanlun_trader.research_factory.universe_dividend_accounting_v1 import TAX_TIMING_POLICY, TAX_TIMING_SOURCE
    old, new = _result(), _result("NEW")
    event = {"event_id": "000001.SZ:20220708:CASH", "symbol": "000001.SZ", "event_type": "CASH_DIVIDEND",
             "record_date": 20220707, "effective_date": 20220708, "payment_date": 20220708,
             "terms": {"cash_per_share": .1, "tax_rule": {"kind": "DEFERRED_INDIVIDUAL_2015_101", "source": TAX_TIMING_SOURCE}}}
    payment = {"event_id": event["event_id"], "phase": "PAYMENT", "timestamp": "2022-07-08T09:30:00+08:00", "amount": 10.}
    tax = {"event_id": event["event_id"], "phase": "DEFERRED_INDIVIDUAL_TAX",
           "timestamp": "2022-07-20T09:30:00+08:00", "lot_id": "lot-1", "quantity": 100, "amount": 2., "rate": .2}
    for result in (old, new):
        economic = result["final_account_checkpoint"]["economic"]
        economic["action_audit"] = [deepcopy(payment), deepcopy(tax)]
        economic["payments"] = [event["event_id"]]
    new["final_account_checkpoint"]["economic"]["action_audit"][1].update(
        broker_collection_time_verified=False, dividend_paid=True, payment_date=20220708,
        policy=TAX_TIMING_POLICY, source=TAX_TIMING_SOURCE)
    return old, new, [event]


def test_registered_additive_tax_evidence_is_reported_and_verified_separately():
    old, new, events = _tax_audit_pair()
    original = deepcopy(new)
    result = parity.compare_parity_results_v1(old, new, events=events)
    assert result["passed"]
    evidence = result["version_additive_audit_evidence"]
    assert evidence["status"] == "VERIFIED" and evidence["errors"] == []
    assert evidence["rows"][0]["added_fields"]["broker_collection_time_verified"] is False
    assert evidence["rows"][0]["proof"]["payment_date"] == 20220708
    assert evidence["rows"][0]["proof"]["tax_audit_at"] == "2022-07-20T09:30:00+08:00"
    assert new == original


@pytest.mark.parametrize("field,value", [
    ("broker_collection_time_verified", True), ("dividend_paid", False),
    ("payment_date", 20220709), ("policy", "VERIFIED_BROKER_COLLECTION"),
    ("source", "invented"), ("unknown_field", "must_not_be_ignored"),
    ("timestamp", "2022-07-07T09:30:00+08:00"),
])
def test_additive_evidence_bad_fields_or_payment_chronology_fail(field, value):
    old, new, events = _tax_audit_pair()
    new["final_account_checkpoint"]["economic"]["action_audit"][1][field] = value
    result = parity.compare_parity_results_v1(old, new, events=events)
    assert not result["passed"]
    assert result["version_additive_audit_evidence"]["status"] == "INVALID"


@pytest.mark.parametrize("change", [
    lambda new, events: new["final_account_checkpoint"]["economic"].update(payments=[]),
    lambda new, events: new["final_account_checkpoint"]["economic"]["action_audit"][0].update(timestamp="2022-07-21T09:30:00+08:00"),
    lambda new, events: new["final_account_checkpoint"]["economic"]["action_audit"][0].update(timestamp="2022-07-08T09:30:00"),
    lambda new, events: events[0]["terms"]["tax_rule"].update(source="wrong_frozen_source"),
    lambda new, events: events[0]["terms"]["tax_rule"].update(kind="UNKNOWN"),
    lambda new, events: events[0].update(payment_date=20220709),
])
def test_additive_evidence_requires_paid_ledger_original_payment_and_frozen_terms(change):
    old, new, events = _tax_audit_pair()
    change(new, events)
    result = parity.compare_parity_results_v1(old, new, events=events)
    assert not result["passed"] and result["version_additive_audit_evidence"]["errors"]


def test_additive_fields_cannot_hide_changed_amount_or_replace_frozen_evidence():
    old, new, events = _tax_audit_pair()
    missing = parity.compare_parity_results_v1(old, new)
    assert not missing["passed"] and missing["version_additive_audit_evidence"]["status"] == "INVALID"
    new["final_account_checkpoint"]["economic"]["action_audit"][1]["amount"] = 2.01
    result = parity.compare_parity_results_v1(old, new, events=events)
    assert result["version_additive_audit_evidence"]["status"] == "VERIFIED"
    assert not result["passed"] and "economic.action_audit[1].amount" in result["mismatch_paths"]


def _existing_parity_record(root):
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.exploration_governance import immutable
    root.mkdir()
    budget = root / "BUDGET.json"
    immutable(budget, {"consumed_accounts": 4})
    tasks = {}
    for side in ("old", "new"):
        path = root / (side + "_JOB.json")
        plans = {"FIXED_" + cost: {"plan_id": side + "_" + cost} for cost in ("BASE", "STRESS")}
        immutable(path, {"plans": plans, "budget_path": str(budget)})
        tasks[side] = {"job_path": str(path), "job_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "plan_ids": {name: value["plan_id"] for name, value in plans.items()}}
    plan = {"version": parity.VERSION, "scope": {"account_start": 20220706}}
    plan["parity_identity"] = stable_hash(plan)
    immutable(root / "FROZEN_PARITY_PLAN.json", plan)
    result_path = root / "PARITY_RESULT.json"
    immutable(result_path, {"version": parity.VERSION, "account_executed": True,
                            "status": "PARITY_MISMATCH", "plan": plan, "tasks": tasks})
    return result_path, budget


def test_reverify_uses_existing_four_accounts_and_does_not_change_original_or_budget(tmp_path, monkeypatch):
    import chanlun_trader.research_factory.research_evidence_v1 as evidence
    path, budget = _existing_parity_record(tmp_path / "record")
    original_bytes, budget_bytes = path.read_bytes(), budget.read_bytes()
    calls = []
    def verify(job_path, *, name):
        calls.append((job_path, name))
        return {"status": "PASS", "advance_allowed": True}
    monkeypatch.setattr(evidence, "verify_job_evidence", verify)
    monkeypatch.setattr(parity, "_input_for_task", lambda task: _inputs())
    monkeypatch.setattr(parity, "_results", lambda task: {cost: _result("NEW" if "new_JOB" in task["job_path"] else "OLD")
                                                       for cost in ("BASE", "STRESS")})
    result = parity.reverify_baostock_universe_parity_v1(path)
    assert result["status"] == "PARITY_VERIFIED" and len(calls) == 4
    assert result["budget_proof"]["unchanged"]
    assert result["new_accounts_executed"] is False and result["new_budget_registered"] is False
    assert path.read_bytes() == original_bytes and budget.read_bytes() == budget_bytes
    assert Path(result["report_path"]).name.startswith("VERIFIED_PARITY_")


def test_reverify_fails_if_any_budget_changes_during_the_check(tmp_path, monkeypatch):
    import chanlun_trader.research_factory.research_evidence_v1 as evidence
    path, budget = _existing_parity_record(tmp_path / "record")
    def verify(job_path, *, name):
        budget.write_text('{"consumed_accounts":5}', encoding="utf-8")
        return {"status": "PASS", "advance_allowed": True}
    monkeypatch.setattr(evidence, "verify_job_evidence", verify)
    monkeypatch.setattr(parity, "_input_for_task", lambda task: _inputs())
    monkeypatch.setattr(parity, "_results", lambda task: {cost: _result() for cost in ("BASE", "STRESS")})
    with pytest.raises(ValueError, match="BUDGET_CHANGED_DURING_READ_ONLY_REVERIFY"):
        parity.reverify_baostock_universe_parity_v1(path)
    assert not list(path.parent.glob("VERIFIED_PARITY_*.json"))


def test_reverify_cli_cannot_execute_or_use_new_deployments(tmp_path):
    with pytest.raises(SystemExit) as error:
        parity.main(["--reverify", str(tmp_path / "old_result.json"), "--execute"])
    assert error.value.code == 2


def test_reverify_does_not_compare_results_with_failed_original_evidence(tmp_path, monkeypatch):
    import chanlun_trader.research_factory.research_evidence_v1 as evidence
    path, budget = _existing_parity_record(tmp_path / "record")
    original_budget = budget.read_bytes()
    monkeypatch.setattr(evidence, "verify_job_evidence", lambda *args, **kwargs: {"status": "FAIL", "advance_allowed": False})
    monkeypatch.setattr(parity, "_results", lambda task: pytest.fail("证据失败不得比较收益"))
    with pytest.raises(ValueError, match="ORIGINAL_EVIDENCE_NOT_PASS"):
        parity.reverify_baostock_universe_parity_v1(path)
    assert budget.read_bytes() == original_budget
    assert not list(path.parent.glob("VERIFIED_PARITY_*.json"))
