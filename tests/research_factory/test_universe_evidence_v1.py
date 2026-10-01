"""合成手算与篡改反例；这些测试不证明真实股票数据或策略盈利。"""
from copy import deepcopy

import pandas as pd
import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.formal_account_backend_v1 import BASE_COSTS
from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3
from chanlun_trader.research_factory.strategy_interface_v1 import prepare, run
from chanlun_trader.research_factory.universe_account_backend_v1 import UniverseAccountBackendV1
from chanlun_trader.research_factory.universe_account_inputs_v1 import universe_input_identity_v1
from chanlun_trader.research_factory.universe_evidence_v1 import reconstruct_universe_account
from universe_test_fixture_v1 import fixture, proposal


def case(*, bundle=None, window=None, value=None, prices=None, symbols=None, days_count=64):
    if bundle is None:
        window, bundle = fixture(symbols=symbols, prices=prices, days_count=days_count)
    value = value or proposal()
    strategy = ResearchRuleStrategyV3(value, strategy_id="universe")
    backend = UniverseAccountBackendV1(window, initial_cash=50000, max_positions=min(2, len(window["symbols"])),
                                      max_symbol_exposure_bps=5000)
    identity = universe_input_identity_v1(bundle, window)
    receipt = {"strategy_plans": {"universe": prepare(strategy, backend)}, "input_identity": identity,
        "novelty": {"universe": {"allowed": True}}, "execution_purpose": "universe", "execution_consumed": True}
    result = run(strategy, backend, frame=bundle, actions=bundle["events"], input_identity=identity,
                 active_check=lambda: receipt)
    return bundle, window, strategy.payload, result


def audit(bundle, window, value, result):
    return reconstruct_universe_account(bundle, window, result, initial_cash=50000,
                                        costs=BASE_COSTS, strategy_id="universe", rule=value)


def overnight_scope_case(field, value, *, removal_index=61, max_hold_sessions=5):
    window, bundle = fixture(symbols=["000001.SZ"], days_count=64)
    days = window["calendar"]
    original = bundle["states"].iloc[0].to_dict()
    before = {**original, "valid_to": days[removal_index - 1]}
    removed = {**original, "effective_date": days[removal_index], field: value}
    bundle["states"] = pd.DataFrame([before, removed])
    rule = proposal()
    rule["max_hold_sessions"] = max_hold_sessions
    return case(bundle=bundle, window=window, value=rule)


@pytest.mark.parametrize("field,value", [("universe_member", False), ("eligibility_status", "INELIGIBLE")])
def test_overnight_scope_removal_requires_explicit_skip_and_no_buy_order(field, value):
    bundle, window, rule, result = overnight_scope_case(field, value)
    assert not result["fills"]
    assert result["daily_accounts"][-1]["cash"] == 50000
    checkpoint = result["final_account_checkpoint"]
    assert not checkpoint["economic"]["orders"]
    assert len(checkpoint["skipped_intents"]) == 1
    assert checkpoint["skipped_intents"][0]["reason"] == "SECURITY_NOT_ELIGIBLE_AT_OPEN"
    assert audit(bundle, window, rule, result)["metrics"]["trade_count"] == 0


@pytest.mark.parametrize("mutation", ["missing", "wrong_reason", "duplicate"])
def test_missing_or_forged_overnight_entry_skip_cannot_pass_reconciliation(mutation):
    bundle, window, rule, result = overnight_scope_case("universe_member", False)
    forged = deepcopy(result)
    skips = forged["final_account_checkpoint"]["skipped_intents"]
    if mutation == "missing":
        skips.clear()
    elif mutation == "wrong_reason":
        skips[0]["reason"] = "NO_PERMITTED_QUANTITY"
    else:
        skips.append(deepcopy(skips[0]))
    forged["reconciliation"]["passed"] = True
    with pytest.raises(ValueError, match="ENTRY_ELIGIBILITY_SKIP_CONFLICT"):
        audit(bundle, window, rule, forged)


@pytest.mark.parametrize("mutation,reason", [
    ("order", "BUY_ORDER_WHILE_NOT_ELIGIBLE_AT_OPEN"),
    ("fill", "BUY_FILL_WHILE_NOT_ELIGIBLE_AT_OPEN"),
])
def test_overnight_entry_skip_cannot_hide_a_buy_order_or_fill(mutation, reason):
    bundle, window, rule, result = overnight_scope_case("eligibility_status", "INELIGIBLE")
    _, _, _, normal = case(symbols=["000001.SZ"])
    forged = deepcopy(result)
    economic = forged["final_account_checkpoint"]["economic"]
    if mutation == "order":
        order = deepcopy(next(iter(normal["final_account_checkpoint"]["economic"]["orders"].values())))
        order["intent_id"] = forged["final_account_checkpoint"]["skipped_intents"][0]["intent_id"]
        order["metadata"]["plan_id"] = forged["decisions"][1]["plan"]["plan_id"]
        economic["orders"][order["order_id"]] = order
    else:
        fill = deepcopy(normal["fills"][0])
        forged["fills"].append(fill)
        economic["trades"].append(deepcopy(fill))
    with pytest.raises(ValueError, match=reason):
        audit(bundle, window, rule, forged)


@pytest.mark.parametrize("field,value", [("universe_member", False), ("eligibility_status", "INELIGIBLE")])
def test_scope_removal_does_not_prevent_existing_position_sell(field, value):
    bundle, window, rule, result = overnight_scope_case(field, value, removal_index=63, max_hold_sessions=1)
    assert [fill["side"] for fill in result["fills"]] == ["BUY", "SELL"]
    assert [fill["quantity"] for fill in result["fills"]] == [1200, 1200]
    assert int(pd.Timestamp(result["fills"][1]["fill_time"]).strftime("%Y%m%d")) == window["calendar"][63]
    assert result["daily_accounts"][-1]["positions"][0]["quantity"] == 0
    assert audit(bundle, window, rule, result)["metrics"]["trade_count"] == 2


def test_fake_ineligible_skip_cannot_excuse_a_missing_eligible_buy_order():
    bundle, window, rule, result = case(symbols=["000001.SZ"])
    forged = deepcopy(result)
    orders = forged["final_account_checkpoint"]["economic"]["orders"]
    order = next(iter(orders.values()))
    forged["final_account_checkpoint"]["skipped_intents"].append({
        "intent_id": order["intent_id"], "reason": "SECURITY_NOT_ELIGIBLE_AT_OPEN"})
    del orders[order["order_id"]]
    with pytest.raises(ValueError, match="MISSING_OR_DUPLICATE_ORDER"):
        audit(bundle, window, rule, forged)


@pytest.mark.parametrize("intent_scope", ["filled_buy", "unrelated"])
def test_extra_ineligible_skip_cannot_conflict_with_a_completed_buy(intent_scope):
    bundle, window, rule, result = case(symbols=["000001.SZ"])
    assert len(result["fills"]) == 1 and result["fills"][0]["side"] == "BUY"
    forged = deepcopy(result)
    order = next(iter(forged["final_account_checkpoint"]["economic"]["orders"].values()))
    intent_id = order["intent_id"] if intent_scope == "filled_buy" else "unrelated_intent"
    forged["final_account_checkpoint"]["skipped_intents"].append({
        "intent_id": intent_id, "reason": "SECURITY_NOT_ELIGIBLE_AT_OPEN"})
    with pytest.raises(ValueError, match="UNEXPECTED_ENTRY_ELIGIBILITY_SKIP"):
        audit(bundle, window, rule, forged)


def test_valid_entry_eligibility_skip_does_not_allow_an_extra_unrelated_skip():
    bundle, window, rule, result = overnight_scope_case("universe_member", False)
    assert audit(bundle, window, rule, result)["metrics"]["trade_count"] == 0
    forged = deepcopy(result)
    forged["final_account_checkpoint"]["skipped_intents"].append({
        "intent_id": "unrelated_intent", "reason": "SECURITY_NOT_ELIGIBLE_AT_OPEN"})
    with pytest.raises(ValueError, match="UNEXPECTED_ENTRY_ELIGIBILITY_SKIP"):
        audit(bundle, window, rule, forged)


def test_hand_calculated_cash_equity_and_fifo_without_engine_or_scanner_oracles(monkeypatch):
    bundle, window, value, result = case(symbols=["000001.SZ"])
    bought = [trade for trade in result["fills"] if trade["side"] == "BUY"]
    assert len(bought) == 1
    # 50,000 * 30% / (12 * 1.001), 买入向下取整至100股；最低佣金5元。
    assert bought[0]["quantity"] == 1200
    assert bought[0]["price"] == 12.012
    assert bought[0]["fee"] == 5.
    assert result["daily_accounts"][-1]["cash"] == pytest.approx(35580.6)
    assert result["daily_accounts"][-1]["equity"] == pytest.approx(49980.6)
    # 切断系统执行/扫描/制度计算；只读核验仍必须能重建同一经济状态。
    from chanlun_trader.engine.broker import BrokerSimulator
    from chanlun_trader.engine.ledger import PortfolioLedger
    from chanlun_trader.engine.daily_exit_v2 import DailyExitEvaluatorV2
    from chanlun_trader.research_factory.board_execution_policy_v1 import BoardExecutionPolicyV1
    from chanlun_trader.research_factory.universe_signal_scan_v1 import UniverseSignalScanV1
    def forbidden(*args, **kwargs):
        raise AssertionError("AUDITOR_USED_ENGINE_AS_ORACLE")
    monkeypatch.setattr(BrokerSimulator, "_try_fill_order", forbidden)
    monkeypatch.setattr(PortfolioLedger, "apply_fill", forbidden)
    monkeypatch.setattr(DailyExitEvaluatorV2, "evaluate", forbidden)
    monkeypatch.setattr(BoardExecutionPolicyV1, "resolve", forbidden)
    monkeypatch.setattr(UniverseSignalScanV1, "at", forbidden)
    rebuilt = audit(bundle, window, value, result)
    assert rebuilt["metrics"]["net_return"] == pytest.approx(-19.4 / 50000)
    assert rebuilt["strategy_qualified"] is False
    assert rebuilt["method_scopes"]["signals"] == "RECOMPUTED_WITH_SHARED_INDICATOR_AND_DSL_FORMULAS"


@pytest.mark.parametrize("mutation,reason", [
    ("daily_cash", "DAILY_ACCOUNT_CONFLICT"),
    ("final_cash", "FINAL_CASH_CONFLICT"),
    ("board", "BOARD_POLICY_IDENTITY_CONFLICT"),
    ("price_policy", "EXIT_PRICE_POLICY_CONFLICT"),
    ("scanner", "SCANNER_IDENTITY_CONFLICT"),
    ("last_slice", "FULL_SCAN_COVERAGE_CONFLICT"),
    ("condition", "SCAN_CONDITIONS_OR_DECISIONS_CONFLICT"),
    ("consume", "CONSUMED_DECISIONS_CONFLICT"),
    ("lot_cost", "FINAL_LOTS_CONFLICT"),
    ("qualification", "UNSUPPORTED_QUALIFICATION"),
    ("metric", "METRICS_CONFLICT"),
    ("snapshot", "CLOSE_SNAPSHOT_CONFLICT"),
])
def test_self_reported_pass_never_overrides_independent_contradiction(mutation, reason):
    bundle, window, value, result = case()
    result = deepcopy(result)
    if mutation == "daily_cash":
        result["daily_accounts"][-1]["cash"] += .01
    elif mutation == "final_cash":
        result["final_account_checkpoint"]["economic"]["cash"] += .01
    elif mutation == "board":
        result["board_policy_identity"] = "0" * 64
    elif mutation == "price_policy":
        result["exit_price_policy"] = "RAW_PLUS_PAID_GROSS_CASH_V1"
    elif mutation == "scanner":
        result["scanner_identity"] = "0" * 64
    elif mutation == "last_slice":
        result["scan_days"][-1]["rows"].pop()
        result["scan_days"][-1]["identity"] = stable_hash(result["scan_days"][-1]["rows"])
    elif mutation == "condition":
        result["scan_days"][-1]["rows"][0]["conditions"]["buy"] = False
        result["scan_days"][-1]["identity"] = stable_hash(result["scan_days"][-1]["rows"])
    elif mutation == "consume":
        result["decisions"][1]["decisions"].pop()
    elif mutation == "lot_cost":
        next(iter(result["final_account_checkpoint"]["economic"]["lots"].values()))["cost"] += .01
    elif mutation == "qualification":
        result["strategy_qualified"] = True
    elif mutation == "metric":
        result["metrics"]["net_return"] += .001
    elif mutation == "snapshot":
        result["final_account_checkpoint"]["economic"]["snapshots"][-1]["equity"] += .01
    result["reconciliation"] = {"passed": True, "audit_identity": "a" * 64}
    with pytest.raises(ValueError, match=reason):
        audit(bundle, window, value, result)


def test_previous_exchange_volume_capacity_and_partial_sell_are_hand_checked():
    window, bundle = fixture(symbols=["000001.SZ"], days_count=66)
    # 信号日成交量1500 -> 次日开盘容量150股 -> 买入100股；当日一百万不可借用。
    bundle["daily"].loc[bundle["daily"].date.eq(window["calendar"][60]), "volume"] = 1500.
    # 买入100股后，SELL量按前日容量60股分两次成交，卖出余股允许零股。
    bundle["daily"].loc[bundle["daily"].date.eq(window["calendar"][62]), "volume"] = 600.
    value = proposal()
    value["max_hold_sessions"] = 1
    bundle, window, value, result = case(bundle=bundle, window=window, value=value)
    assert [trade["quantity"] for trade in result["fills"][:3]] == [100, 60, 40]
    assert [trade["side"] for trade in result["fills"][:3]] == ["BUY", "SELL", "SELL"]
    assert audit(bundle, window, value, result)["metrics"] == result["metrics"]
    orders = result["final_account_checkpoint"]["economic"]["orders"]
    partial = next(order for order in orders.values() if order["side"] == "SELL" and order["filled_quantity"] == 60)
    assert partial["quantity"] == 100 and partial["remaining_quantity"] == 40
    assert partial["status"] == "EXPIRED"
    forged = deepcopy(result)
    forged["final_account_checkpoint"]["economic"]["orders"][partial["order_id"]]["filled_quantity"] = 100
    with pytest.raises(ValueError, match="ORDER_FILL_TOTAL_CONFLICT"):
        audit(bundle, window, value, forged)


def test_t_plus_one_uses_frozen_calendar_not_next_natural_day():
    # 在合成日历里人为留出假期；不冒称此日期集合就是正式交易所日历。
    window, bundle = fixture(symbols=["000001.SZ"], days_count=64)
    days = window["calendar"]
    replacement = int((pd.Timestamp(str(days[61])) + pd.Timedelta(days=7)).strftime("%Y%m%d"))
    following = [int(d.strftime("%Y%m%d")) for d in pd.bdate_range(str(replacement), periods=2)]
    mapping = dict(zip(days[62:], following))
    bundle["daily"]["date"] = bundle["daily"].date.replace(mapping)
    bundle["turn"]["date"] = bundle["turn"].date.replace(mapping)
    bundle["states"]["valid_to"] = following[-1]
    bundle["calendar"] = days[:62] + following
    bundle["corporate_action_coverage"][0]["end"] = following[-1]
    window = {**window, "calendar": bundle["calendar"], "account_end": following[-1]}
    bundle, window, value, result = case(bundle=bundle, window=window)
    lot = next(iter(result["final_account_checkpoint"]["economic"]["lots"].values()))
    assert lot["sellable_from_session"] == following[0]
    assert pd.Timestamp(lot["sellable_from"]) == pd.Timestamp(str(following[0]), tz="Asia/Shanghai") + pd.Timedelta(hours=9, minutes=30)
    forged = deepcopy(result)
    entry = pd.Timestamp(lot["buy_time"]).normalize()
    next(iter(forged["final_account_checkpoint"]["economic"]["lots"].values()))["sellable_from"] = (entry + pd.Timedelta(days=1, hours=9, minutes=30)).isoformat()
    with pytest.raises(ValueError, match="FINAL_LOTS_CONFLICT"):
        audit(bundle, window, value, forged)


def test_independent_board_arithmetic_handles_historical_low_price_shanghai_st():
    from chanlun_trader.research_factory.universe_account_inputs_v1 import prepare_universe_account_inputs_v1
    from chanlun_trader.research_factory.universe_evidence_v1 import _opening_permission
    window, bundle = fixture(symbols=["600000.SH"], days_count=64)
    bundle["daily"].loc[:, ["open", "high", "low", "close", "prev_close"]] *= .09 / 12
    bundle["states"]["st_status"] = "ST"
    bundle["states"]["eligibility_status"] = "INELIGIBLE"
    day = window["calendar"][61]
    bundle["daily"].loc[bundle["daily"].date.eq(day), ["open", "low"]] = .08
    inputs = prepare_universe_account_inputs_v1(bundle, window)
    assert _opening_permission(inputs, "600000.SH", day, "SELL") == (False, "LIMIT_DOWN_OPEN_DAILY_CONSERVATIVE")
    assert _opening_permission(inputs, "600000.SH", window["calendar"][62], "SELL") == (True, "OK")


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_slippage_never_creates_a_fill_outside_legal_daily_prices(side):
    from chanlun_trader.research_factory.universe_account_inputs_v1 import prepare_universe_account_inputs_v1
    from chanlun_trader.research_factory.universe_evidence_v1 import _opening_permission
    if side == "BUY":
        window, bundle = fixture(symbols=["000001.SZ"], days_count=62)
        day = window["calendar"][61]
        bundle["daily"].loc[bundle["daily"].date.eq(day), ["open", "high", "low"]] = [13.19, 13.2, 12.]
        value, modeled_price = proposal(), 13.2032
    else:
        window, bundle = fixture(symbols=["000001.SZ"], prices=[12., 12., 11.5, 10.36], days_count=64)
        day = window["calendar"][63]
        bundle["daily"].loc[bundle["daily"].date.eq(day), ["high", "low"]] = [10.4, 10.35]
        value, modeled_price = proposal(), 10.3496
        value["max_hold_sessions"] = 1
    inputs = prepare_universe_account_inputs_v1(bundle, window)
    assert _opening_permission(inputs, "000001.SZ", day, side) == (True, "OK")
    assert _opening_permission(inputs, "000001.SZ", day, side, modeled_price=modeled_price) == (
        False, "MODELED_FILL_PRICE_OUTSIDE_DAILY_LIMIT")
    bundle, window, value, result = case(bundle=bundle, window=window, value=value)
    order = next(o for o in result["final_account_checkpoint"]["economic"]["orders"].values()
                 if o["side"] == side and int(pd.Timestamp(o["created_at"]).strftime("%Y%m%d")) == day)
    assert order["status"] == "REJECTED" and order["filled_quantity"] == 0
    assert order["metadata"]["broker_rejection_reason"] == "MODELED_FILL_PRICE_OUTSIDE_DAILY_LIMIT"
    assert not any(t["order_id"] == order["order_id"] for t in result["fills"])
    audit(bundle, window, value, result)


def test_delayed_dividend_sale_before_payment_preserves_rights_tax_and_exit_price():
    window, bundle = fixture(symbols=["000001.SZ"], days_count=67)
    days, symbol = window["calendar"], "000001.SZ"
    bundle["events"] = [{"event_id": "SYNTHETIC_DIVIDEND", "symbol": symbol, "event_type": "CASH_DIVIDEND",
        "record_date": days[62], "effective_date": days[63], "payment_date": days[66],
        "source": "synthetic", "source_published_at": str(days[60]), "units": "CNY_PER_SHARE",
        "terms": {"cash_per_share": .5, "tax_rule": {"kind": "DEFERRED_INDIVIDUAL_2015_101",
            "source": "https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html"}}}]
    changed = bundle["daily"].date.ge(days[63])
    bundle["daily"].loc[changed, ["open", "high", "low", "close", "prev_close"]] -= .5
    value = proposal({"stop_loss_pct": .03})
    value["max_hold_sessions"] = 2
    bundle, window, value, result = case(bundle=bundle, window=window, value=value)
    rebuilt = audit(bundle, window, value, result)
    assert rebuilt["dividend_tax"] == 120.
    traces = result["final_account_checkpoint"]["rule_exit_states"]["universe"]["evaluations"]
    ex_date = next(row for row in traces if row["date"] == days[63])
    assert ex_date["raw_close"] == 11.5 and ex_date["comparison_close"] == 12.
    assert ex_date["entitled_gross_cash_per_share"] == .5
    assert not ex_date["reasons"]  # 除息没有触发假止损；最大持有期另行产生卖出。
    daily = {row["date"]: row for row in rebuilt["daily_accounts"]}
    assert daily[days[64]]["cash"] == pytest.approx(49234.9069)
    assert daily[days[64]]["equity"] == pytest.approx(49834.9069)
    assert daily[days[66]]["cash"] == pytest.approx(49834.9069)
    taxes = [row for row in result["final_account_checkpoint"]["economic"]["action_audit"]
             if row["phase"] == "DEFERRED_INDIVIDUAL_TAX"]
    assert len(taxes) == 1 and taxes[0]["dividend_paid"] is False
    assert taxes[0]["policy"] == "SALE_FILL_MODELED_2015_101"
    assert rebuilt["receivables"] == {}
    forged = deepcopy(result)
    forged["final_account_checkpoint"]["rule_exit_states"]["universe"]["evaluations"][1]["comparison_close"] -= .5
    with pytest.raises(ValueError, match="EXIT_TRACE_OR_TRAILING_CONFLICT"):
        audit(bundle, window, value, forged)


def test_limit_down_pending_exit_retries_at_next_legal_open_and_cannot_omit_order():
    value = proposal({"stop_loss_pct": .05})
    bundle, window, value, result = case(symbols=["000001.SZ"], value=value,
        prices=[12., 12., 11.2, 10.08, 10.4], days_count=65)
    days = window["calendar"]
    orders = result["final_account_checkpoint"]["economic"]["orders"]
    rejected = next(order for order in orders.values() if order["side"] == "SELL" and order["status"] == "REJECTED")
    assert int(pd.Timestamp(rejected["created_at"]).strftime("%Y%m%d")) == days[63]
    sold = next(trade for trade in result["fills"] if trade["side"] == "SELL")
    assert int(pd.Timestamp(sold["fill_time"]).strftime("%Y%m%d")) == days[64]
    assert sold["price"] == 10.3896
    assert audit(bundle, window, value, result)["exit_behavior"] == "VERIFIED"
    forged = deepcopy(result)
    del forged["final_account_checkpoint"]["economic"]["orders"][sold["order_id"]]
    with pytest.raises(ValueError, match="MISSING_OR_DUPLICATE_ORDER"):
        audit(bundle, window, value, forged)


@pytest.mark.parametrize("zero_record", [False, True])
def test_known_suspension_retains_mark_and_resume_does_not_borrow_future_volume(zero_record):
    window, bundle = fixture(symbols=["000001.SZ"], days_count=66)
    days = window["calendar"]
    original = bundle["states"].iloc[0].to_dict()
    before = {**original, "valid_to": days[61]}
    halt = {**original, "effective_date": days[62], "valid_to": days[62], "suspension_status": "SUSPENDED"}
    after = {**original, "effective_date": days[63]}
    bundle["states"] = pd.DataFrame([before, halt, after])
    if zero_record:
        bundle["daily"].loc[bundle["daily"].date.eq(days[62]), ["volume", "amount"]] = 0.
    else:
        bundle["daily"] = bundle["daily"].loc[~bundle["daily"].date.eq(days[62])].reset_index(drop=True)
        bundle["turn"] = bundle["turn"].loc[~bundle["turn"].date.eq(days[62])].reset_index(drop=True)
    value = proposal({"stop_loss_pct": .05, "trailing_activate_pct": .05, "trailing_pct": .03})
    value["max_hold_sessions"] = 1
    bundle, window, value, result = case(bundle=bundle, window=window, value=value)
    audit(bundle, window, value, result)
    snapshots = {row["date"]: row for row in result["daily_accounts"]}
    assert snapshots[days[62]]["stale_valuations"] == [{"symbol": "000001.SZ", "status": "STALE_VERIFIED_SUSPENSION"}]
    assert snapshots[days[62]]["equity"] == snapshots[days[61]]["equity"]
    assert snapshots[days[63]]["positions"][0]["quantity"] == 1200  # 前一session没bar，复牌当日不能填。
    assert snapshots[days[64]]["positions"][0]["quantity"] == 0
    traces = result["final_account_checkpoint"]["rule_exit_states"]["universe"]["evaluations"]
    assert not any(row["date"] == days[62] for row in traces)
    forged = deepcopy(result)
    forged["final_account_checkpoint"]["rule_exit_states"]["universe"]["evaluations"].append({**traces[0], "date": days[62]})
    with pytest.raises(ValueError, match="EXIT_TRACE_OR_TRAILING_CONFLICT"):
        audit(bundle, window, value, forged)
