"""V3 实例身份、公式参数、动态依赖与退出声明；账户接线另测。"""
from copy import deepcopy

import pandas as pd
import pytest

from chanlun_trader.research_factory.research_rule_strategy_v3 import (
    CAPABILITY, EXECUTION_MODE, ResearchRuleStrategyV3, validate_rule_payload,
)
from chanlun_trader.research_factory.strategy_interface_v1 import Context


def node(op, *args, **params):
    return {"op": op, "args": list(args), "params": params}


def payload():
    return {"version": CAPABILITY, "hypothesis": "双周期均线", "change_reason": "参数语义核验",
            "buy": node("gt", node("indicator", "ma10", output="ma", version="MA_ARITHMETIC_V1"),
                        node("indicator", "ma20", output="ma", version="MA_ARITHMETIC_V1")),
            "sell": node("lt", node("field", "close"), node("const", value=0)),
            "market_filter": None, "min_hold_sessions": 5, "max_hold_sessions": 20,
            "cooldown_sessions": 2, "target_weight": .3,
            "indicator_instances": [{"instance_id": f"ma{window}", "id": "MA", "version": "MA_ARITHMETIC_V1",
                                     "params": {"window": window}} for window in (10, 20)],
            "exits": {"execution_mode": EXECUTION_MODE, "stop_loss_pct": None, "take_profit_pct": None,
                      "trailing_activate_pct": None, "trailing_pct": None}}


def strategy(value=None):
    return ResearchRuleStrategyV3(value or payload(), strategy_id="INSTANCE_TEST")


def context(frame, quantity=0, entry=None):
    return Context(frame, tuple(frame.index), len(frame)-1,
                   {"quantity": quantity, "sellable_quantity": quantity,
                    "entry_session_index": entry, "last_exit_session_index": None}, {})


def test_two_ma_instances_compute_actual_windows_and_dependencies():
    rule = strategy()
    bars = pd.DataFrame({"close": range(1, 31)})
    matrix = rule.build_feature_matrix(bars)
    assert matrix["ma10.ma"].iloc[-1] == 25.5
    assert matrix["ma20.ma"].iloc[-1] == 20.5
    assert rule.requirements.fields == ("close",)
    assert rule.requirements.warmup_sessions == 20
    assert rule.on_close(context(matrix)).reason == "BUY"
    assert rule.on_close(context(matrix.iloc[:9])).reason == "CONDITION_UNKNOWN"


def test_identity_parameters_and_mutation_detection():
    original = strategy()
    value = payload()
    value["indicator_instances"][0]["params"]["window"] = 11
    assert strategy(value).rule_identity != original.rule_identity
    original.definition["indicators"][0]["params"]["window"] = 12
    with pytest.raises(ValueError, match="RULE_CHANGED_AFTER_FREEZE"):
        original.validate()


@pytest.mark.parametrize("params", [{"window": 0}, {"window": True}, {"window": 10.5}, {"window": 253}, {"secret": 10}, {"price": "high"}])
def test_unsupported_parameters_reject_before_execution(params):
    value = payload()
    value["indicator_instances"][0]["params"] = params
    with pytest.raises(ValueError):
        validate_rule_payload(value)


def test_exit_identity_guard_and_minimum_holding_semantics():
    value = payload()
    value["exits"]["stop_loss_pct"] = .05
    rule = strategy(value)
    assert rule.rule_identity != strategy().rule_identity
    assert rule.exit_rules.stop_loss_pct == .05
    assert rule.exit_policy["risk_exit_min_hold_exempt"]
    assert not rule.exit_policy["signal_exit_min_hold_exempt"]
    frame = rule.build_feature_matrix(pd.DataFrame({"close": range(1, 31)}))
    with pytest.raises(ValueError, match="RULE_RISK_EXIT_BACKEND_REQUIRED"):
        rule.on_close(context(frame))
    assert rule.on_signal_close(context(frame)).reason == "BUY"
    assert rule.on_signal_close(context(frame, 100, 29)).reason == "HOLD"


@pytest.mark.parametrize("changes", [{"stop_loss_pct": 0}, {"stop_loss_pct": True}, {"take_profit_pct": float("nan")},
                                     {"trailing_pct": .1}, {"execution_mode": "INTRADAY"}, {"entry_price": 10}])
def test_exit_invalid_or_incomplete_rejected(changes):
    value = payload()
    value["exits"].update(changes)
    with pytest.raises(ValueError):
        strategy(value)


def test_nested_lags_and_cross_extend_warmup():
    value = payload()
    value["buy"] = node("cross_up", node("ref", value["buy"]["args"][0], periods=3), value["buy"]["args"][1])
    assert strategy(value).requirements.warmup_sessions == 21


def test_volatility_100_is_actual_instance_not_default():
    value = payload()
    value["indicator_instances"] = [{"instance_id": "vol100", "id": "ROLLING_VOLATILITY", "version": "ROLLING_VOLATILITY_V1", "params": {"window": 100}}]
    value["buy"] = node("gt", node("indicator", "vol100", output="volatility", version="ROLLING_VOLATILITY_V1"), node("const", value=0))
    rule = strategy(value)
    assert rule.requirements.warmup_sessions == 101
    prices = pd.Series([10 + i + (i % 3) for i in range(120)], dtype=float)
    result = rule.build_feature_matrix(pd.DataFrame({"close": prices}))
    expected = prices.pct_change().rolling(100).std(ddof=1)
    assert result["vol100.volatility"].iloc[-1] == pytest.approx(expected.iloc[-1])
    assert not result["vol100.volatility__ready"].iloc[99]


def test_duplicate_unused_and_unknown_instances_rejected():
    value = payload()
    value["indicator_instances"].append(deepcopy(value["indicator_instances"][0]))
    with pytest.raises(ValueError, match="INSTANCE_ID"):
        strategy(value)
    value = payload()
    value["indicator_instances"][0]["instance_id"] = "other"
    with pytest.raises(ValueError, match="INDICATOR_UNSUPPORTED"):
        strategy(value)


def test_exit_price_policy_is_frozen_and_not_raw_only():
    rule = strategy()
    policy = rule.definition["exit_policy"]
    assert policy["comparison_price_policy"] == "RAW_PLUS_PAID_GROSS_CASH_V1"
    assert policy["trailing_anchor"] == "HIGHEST_COMPLETED_RAW_PLUS_PAID_GROSS_CASH_SINCE_FILL"
    assert policy["supported_cash_payment"] == "PAYMENT_DATE_EQUALS_EFFECTIVE_DATE"
    assert policy["fill_price_policy"] == "RAW_EXECUTION_TAX_AND_FEES_SEPARATE"
    rule.exit_policy["comparison_price_policy"] = "RAW"
    with pytest.raises(ValueError, match="RULE_CHANGED_AFTER_FREEZE"):
        rule.validate()
