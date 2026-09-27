"""受限表达式、账户状态及恢复语义；成交集成由共同账户测试覆盖。"""
from copy import deepcopy

import pandas as pd
import pytest

from chanlun_trader.research_factory.research_rule_strategy_v2 import (
    CAPABILITY, ResearchRuleStrategyV2, rule_capabilities, validate_rule_payload,
)
from chanlun_trader.research_factory.strategy_interface_v1 import Context, validate_decision


def node(op, *args, **params):
    return {"op": op, "args": list(args), "params": params}


def payload():
    return {"version": CAPABILITY, "hypothesis": "低频趋势假设", "change_reason": "降低反复交易成本",
            "buy": node("gt", node("field", "close"), node("const", value=10)),
            "sell": node("lt", node("field", "close"), node("const", value=5)),
            "market_filter": None, "min_hold_sessions": 1, "max_hold_sessions": 5,
            "cooldown_sessions": 2, "target_weight": 0.3}


def strategy(value=None):
    return ResearchRuleStrategyV2(value or payload(), strategy_id="RULE_TEST")


def context(prices, *, quantity=0, sellable=None, entry=None, last_exit=None, state=None, columns=None):
    days = tuple(range(20260101, 20260101 + len(prices)))
    history = pd.DataFrame({"close": prices, **(columns or {})}, index=days)
    return Context(history, days, len(days) - 1,
                   {"quantity": quantity, "sellable_quantity": quantity if sellable is None else sellable,
                    "entry_session_index": entry, "last_exit_session_index": last_exit}, state or {})


def test_independent_buy_sell_and_held_position_is_not_rebalanced():
    rule = strategy()
    buy = rule.on_close(context([12]))
    assert buy.reason == "BUY" and buy.intent.weight == 0.3 and not buy.intent.increase_existing
    validate_decision(buy, rule.requirements)
    held = rule.on_close(context([12, 9], quantity=100, entry=1, state=buy.state))
    assert held.reason == "HOLD" and held.intent is None
    sold = rule.on_close(context([12, 9, 4], quantity=100, entry=1, state=held.state))
    assert sold.reason == "SELL" and sold.intent.weight == 0


def test_exit_wins_buy_conflict():
    value = payload()
    value["sell"] = deepcopy(value["buy"])
    rule = strategy(value)
    assert rule.on_close(context([12])).reason == "EXIT_PRIORITY"
    assert rule.on_close(context([12, 12], quantity=100, entry=0)).intent.weight == 0


def test_holding_uses_actual_entry_not_signal_and_forced_exit_survives_missing_features():
    rule = strategy()
    assert rule.on_close(context([12, 4], quantity=100, entry=1, sellable=0)).reason == "HOLD"
    due = rule.on_close(context([12] * 5 + [float("nan")], quantity=100, entry=0))
    assert due.reason == "SELL" and due.state["exit_pending"]


def test_pending_exit_survives_recovery_partial_fills_and_changed_signal():
    rule = strategy()
    first = rule.on_close(context([12, 4], quantity=200, entry=0))
    reopened = strategy(deepcopy(rule.payload))
    resumed = reopened.on_close(context([12, 4, 12], quantity=100, entry=0, state=deepcopy(first.state)))
    continuous = rule.on_close(context([12, 4, 12], quantity=100, entry=0, state=first.state))
    assert resumed == continuous and resumed.reason == "EXIT_PENDING" and resumed.intent.weight == 0
    flat = reopened.on_close(context([12, 4, 12, 12], last_exit=3, state=resumed.state))
    assert flat.reason == "COOLDOWN" and not flat.state["exit_pending"]


def test_cooldown_counts_full_sessions_after_actual_exit():
    rule = strategy()
    assert rule.on_close(context([12] * 3, last_exit=0)).reason == "COOLDOWN"
    assert rule.on_close(context([12] * 4, last_exit=0)).reason == "BUY"
    value = payload()
    value["cooldown_sessions"] = 0
    assert strategy(value).on_close(context([12], last_exit=0)).reason == "COOLDOWN"
    assert strategy(value).on_close(context([12, 12], last_exit=0)).reason == "BUY"


def test_market_filter_and_unknown_do_not_sell_or_buy():
    value = payload()
    value["market_filter"] = node("gt", node("field", "volume"), node("const", value=100))
    rule = strategy(value)
    assert rule.on_close(context([12])).reason == "CONDITION_UNKNOWN"
    assert rule.on_close(context([12], columns={"volume": [50]})).reason == "CONDITION_FALSE"
    assert rule.on_close(context([12], columns={"volume": [101]})).reason == "BUY"
    assert rule.on_close(context([12], quantity=100, entry=0)).reason == "HOLD"


def test_cross_uses_second_registered_output_and_does_not_cross_gaps():
    spec = next(item for item in rule_capabilities()["indicators"] if len(item["outputs"]) > 1)
    output = spec["outputs"][1]
    value = payload()
    value["buy"] = node("cross_up", node("indicator", spec["id"], output=output, version=spec["version"]), node("const", value=0))
    rule = strategy(value)
    key = f"{spec['id']}.{output}"
    assert rule.on_close(context([12, 12], columns={key: [-1, 1], key + "__ready": [True, True]})).reason == "BUY"
    assert rule.on_close(context([12, 12], columns={key: [-1, 1], key + "__ready": [False, True]})).reason == "CONDITION_UNKNOWN"
    assert rule.on_close(context([12, 12])).reason == "CONDITION_UNKNOWN"
    assert rule.on_close(context([12, 12, 12], columns={key: [-1, 1, 2], key + "__ready": [True] * 3})).reason == "CONDITION_FALSE"


def test_or_true_cannot_hide_missing_selected_indicator():
    spec = rule_capabilities()["indicators"][0]
    value = payload()
    value["buy"] = node("or", value["buy"], node("gt", node("indicator", spec["id"], output=spec["outputs"][0], version=spec["version"]), node("const", value=0)))
    assert strategy(value).on_close(context([12])).reason == "CONDITION_UNKNOWN"


@pytest.mark.parametrize("field,value", [
    ("version", "V1"), ("target_weight", True), ("target_weight", float("nan")),
    ("target_weight", 1.01), ("target_weight", 0), ("max_hold_sessions", 0),
    ("max_hold_sessions", 253), ("min_hold_sessions", -1), ("cooldown_sessions", 61),
    ("cooldown_sessions", 1.0), ("hypothesis", ""),
])
def test_invalid_payload_boundaries(field, value):
    data = payload()
    data[field] = value
    with pytest.raises(ValueError):
        validate_rule_payload(data)


@pytest.mark.parametrize("expr", [
    node("python", "import os"), node("gt", node("field", "tomorrow"), node("const", value=0)),
    node("gt", node("ref", node("field", "close"), periods=-1), node("const", value=0)),
    node("gt", node("ref", node("field", "close"), periods=1.5), node("const", value=0)),
    node("gt", node("field", "close"), node("const", value=float("inf"))),
    node("gt", node("field", "close")), node("gt", node("field", "close"), node("const", value=0), center=True),
    node("const", value=1), node("not", node("field", "close")),
    node("gt", node("indicator", "MA", output="NO_OUTPUT", version="NO_VERSION"), node("const", value=0)),
])
def test_invalid_expression(expr):
    data = payload()
    data["buy"] = expr
    with pytest.raises(ValueError):
        strategy(data)


def test_expression_depth_is_bounded_and_parameters_are_not_arbitrary():
    data = payload()
    for _ in range(7):
        data["buy"] = node("not", data["buy"])
    with pytest.raises(ValueError, match="LIMIT"):
        strategy(data)
    data = payload()
    data["indicator_params"] = {"MA": {"window": 999999}}
    with pytest.raises(ValueError):
        strategy(data)


def test_rule_identity_ignores_prose_but_not_execution_semantics():
    data = payload()
    first = strategy(data)
    data["hypothesis"] = "另一个说明"
    assert strategy(data).rule_identity == first.rule_identity
    data["cooldown_sessions"] += 1
    assert strategy(data).rule_identity != first.rule_identity
    first.parameters["candidate_payload"]["target_weight"] = 1
    with pytest.raises(ValueError, match="CHANGED"):
        first.validate()


def test_prefix_only_and_strict_account_projection():
    rule = strategy()
    ctx = context([12, 12])
    with pytest.raises(ValueError, match="PREFIX"):
        rule.on_close(Context(ctx.history, ctx.calendar, 0, ctx.account, {}))
    with pytest.raises(ValueError, match="PROJECTION"):
        rule.on_close(Context(ctx.history, ctx.calendar, 1, {}, {}))
    ctx.account["entry_session_index"] = 0
    with pytest.raises(ValueError, match="FLAT"):
        rule.on_close(ctx)


@pytest.mark.parametrize("mutation", ["wrong_rule", "repeated_session", "future_session", "bool_session", "extra"])
def test_state_is_bound_and_cannot_skip_or_repeat_sessions(mutation):
    rule = strategy()
    state = rule.on_close(context([12])).state
    if mutation == "wrong_rule":
        state["rule_identity"] = "other"
    elif mutation == "repeated_session":
        state["last_session_index"] = -1
    elif mutation == "future_session":
        state["last_session_index"] = 3
    elif mutation == "bool_session":
        state["last_session_index"] = False
    else:
        state["extra"] = True
    with pytest.raises(ValueError, match="CONTINUITY"):
        rule.on_close(context([12, 12], state=state))


def test_feature_builder_exposes_all_referenced_outputs_from_real_registry():
    spec = next(item for item in rule_capabilities()["indicators"] if len(item["outputs"]) > 1 and item["id"] == "MACD")
    value = payload()
    value["buy"] = node("gt", *(node("indicator", spec["id"], output=output, version=spec["version"]) for output in spec["outputs"][:2]))
    rule = strategy(value)
    bars = pd.DataFrame({"close": [10 + index / 100 for index in range(100)]})
    for key in ("open", "high", "low", "prev_close"):
        bars[key] = bars.close
    bars["volume"], bars["amount"] = 10000.0, 100000.0
    matrix = rule.build_feature_matrix(bars)
    for output in spec["outputs"][:2]:
        assert f"MACD.{output}" in matrix and f"MACD.{output}__ready" in matrix
    assert len(matrix) == len(bars) and matrix.index.equals(bars.index)
