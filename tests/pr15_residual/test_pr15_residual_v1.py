"""PR15 残留修正验收：版本绑定、ATR 规则身份、排名退出消费端。

全部经**真实公共服务/API/CLI** 取得 red/green，不手工调 helper 代替。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from chanlun_trader.engine.behavior_service_v2 import (
    BEHAVIOR_MODE_V2,
    BehaviorRequestError,
    run_behavior_backtest_v2,
)
from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.webapp import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]


def _days(count: int = 90) -> list:
    days = [int((pd.Timestamp("2024-09-02") + pd.Timedelta(days=i)).strftime("%Y%m%d"))
            for i in range(count + 60)]
    return [d for d in days if pd.Timestamp(str(d)).weekday() < 5][:count]


def _bars(count: int, seed: float = 0.0) -> list:
    days = _days(count)
    closes = [10.0 + seed + 0.06 * i + 0.8 * np.sin(i / 7) for i in range(count)]
    return [{"date": days[i], "open": closes[i] * 0.999, "high": closes[i] * 1.02,
             "low": closes[i] * 0.98, "close": closes[i],
             "volume": 1_000_000.0, "amount": closes[i] * 1_000_000.0}
            for i in range(count)]


def _varying_tr_bars(count: int) -> list:
    """TR 明显变化的合成行情，使 ATR7 与 ATR14 数值不同。

    前段窄幅（TR 小），后段宽幅（TR 大），两窗口的平滑结果因此显著不同。
    """
    days = _days(count)
    bars = []
    for i in range(count):
        px = 10.0 + 0.02 * i
        width = 0.10 if i < count // 2 else 1.20
        bars.append({"date": days[i], "open": px, "high": px + width,
                     "low": px - width, "close": px,
                     "volume": 1_000_000.0, "amount": px * 1_000_000.0})
    return bars


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    root = tmp_path / "synthetic_root"
    root.mkdir()
    app = create_app(root, ExecutionPolicy(
        mode="READ_ONLY", workspace_kind="SYNTHETIC", allow_readonly_compute=True))
    return TestClient(app)


# ==========================================================================
# PR15-01 残留：表达式内的版本必须与解析后的实例一致
# ==========================================================================
def _ma_payload(entry_condition: dict, indicators: list) -> dict:
    days = _days()
    return {
        "mode": BEHAVIOR_MODE_V2, "calendar": days, "symbols": ["600000.SH"],
        "bars": {"600000.SH": _bars(len(days))},
        "indicators": indicators,
        "entry_condition": entry_condition,
        "exit_rules": {"fixed_holding_sessions": 5},
        "initial_cash": 100_000.0, "max_positions": 1, "max_position_weight": 1.0,
    }


def test_pr1501_unknown_version_inside_entry_expression_is_rejected(client: TestClient):
    """未知 version 放在**表达式引用**里也必须拒绝，不能只校验 indicators[].version。"""
    payload = _ma_payload(
        {"op": "gt", "args": [
            {"op": "indicator", "args": ["RSI"], "params": {"version": "RSI_V999",
                                                            "output": "rsi"}},
            {"op": "const", "params": {"value": 30.0}}]},
        [{"indicator_id": "RSI", "params": {"window": 14}}])
    response = client.post("/api/backtest/behavior/v2", json=payload)
    assert response.status_code == 400, response.text
    assert "VERSION_MISMATCH" in response.json()["detail"]["code"]


def test_pr1501_unknown_version_inside_exit_and_reverse_expressions_rejected():
    """exit_condition 与 reverse_signal_condition 里的未知 version 同样拒绝。"""
    base = _ma_payload(
        {"op": "gt", "args": [{"op": "field", "args": ["close"]},
                              {"op": "const", "params": {"value": 1.0}}]},
        [{"indicator_id": "RSI", "params": {"window": 14}}])
    base["exit_condition"] = {"op": "gt", "args": [
        {"op": "indicator", "args": ["RSI"], "params": {"version": "RSI_V999",
                                                        "output": "rsi"}},
        {"op": "const", "params": {"value": 70.0}}]}
    with pytest.raises(BehaviorRequestError) as excinfo:
        run_behavior_backtest_v2(base)
    assert "VERSION_MISMATCH" in str(excinfo.value)

    reverse = _ma_payload(
        {"op": "gt", "args": [{"op": "field", "args": ["close"]},
                              {"op": "const", "params": {"value": 1.0}}]},
        [{"indicator_id": "RSI", "params": {"window": 14}}])
    reverse["reverse_signal_condition"] = {"op": "lt", "args": [
        {"op": "indicator", "args": ["RSI"], "params": {"version": "RSI_V000",
                                                        "output": "rsi"}},
        {"op": "const", "params": {"value": 30.0}}]}
    with pytest.raises(BehaviorRequestError) as excinfo:
        run_behavior_backtest_v2(reverse)
    assert "VERSION_MISMATCH" in str(excinfo.value)


def test_pr1501_matching_version_in_expression_is_accepted(client: TestClient):
    """版本一致时必须正常运行（不能把正确用法一起拒掉）。"""
    payload = _ma_payload(
        {"op": "gt", "args": [
            {"op": "indicator", "args": ["RSI"], "params": {"version": "RSI_V1",
                                                            "output": "rsi"}},
            {"op": "const", "params": {"value": 30.0}}]},
        [{"indicator_id": "RSI", "params": {"window": 14}}])
    response = client.post("/api/backtest/behavior/v2", json=payload)
    assert response.status_code == 200, response.text


def test_pr1501_unknown_reference_name_is_rejected(client: TestClient):
    payload = _ma_payload(
        {"op": "gt", "args": [
            {"op": "indicator", "args": ["NO_SUCH_INSTANCE"], "params": {"output": "ma"}},
            {"op": "const", "params": {"value": 1.0}}]},
        [{"indicator_id": "MA", "params": {"window": 5}}])
    response = client.post("/api/backtest/behavior/v2", json=payload)
    assert response.status_code == 400
    # 未注册名在解析阶段即被拒绝（UNKNOWN_INDICATOR_OR_ALIAS），
    # 已注册但非本请求实例的引用在绑定阶段被拒绝（UNKNOWN_INDICATOR_REFERENCE）。
    code = response.json()["detail"]["code"]
    assert code.startswith(("UNKNOWN_INDICATOR_REFERENCE",
                            "UNKNOWN_INDICATOR_OR_ALIAS")), code


def test_pr1501_ambiguous_bare_id_reference_rejected_when_multiple_instances(client: TestClient):
    """同一 id 有多实例时，裸 id 引用是含糊的，必须拒绝。"""
    payload = _ma_payload(
        {"op": "gt", "args": [
            {"op": "indicator", "args": ["MA"], "params": {"output": "ma"}},
            {"op": "const", "params": {"value": 1.0}}]},
        [{"indicator_id": "MA", "params": {"window": 5}, "alias": "ma_fast"},
         {"indicator_id": "MA", "params": {"window": 20}, "alias": "ma_slow"}])
    response = client.post("/api/backtest/behavior/v2", json=payload)
    assert response.status_code == 400
    assert "UNKNOWN_INDICATOR_REFERENCE" in response.json()["detail"]["code"]


def test_pr1501_bare_id_single_instance_still_compatible(client: TestClient):
    """单实例时裸 id 引用保持兼容。"""
    payload = _ma_payload(
        {"op": "gt", "args": [
            {"op": "indicator", "args": ["MA"], "params": {"output": "ma"}},
            {"op": "const", "params": {"value": 1.0}}]},
        [{"indicator_id": "MA", "params": {"window": 5}}])
    assert client.post("/api/backtest/behavior/v2", json=payload).status_code == 200


def test_pr1501_two_moving_averages_are_consumed_per_value():
    """逐值证明两条均线**分别**被消费：不同窗口必须产生不同信号集。

    用 MA5/MA20 与一个恰好落在两条线之间的价格，使两个实例给出不同的
    TRUE/FALSE 分布。若后者覆盖前者，两者会完全相同。
    """
    days = _days()
    bars = _bars(len(days))
    payload = _ma_payload(
        {"op": "gt", "args": [
            {"op": "field", "args": ["close"]},
            {"op": "indicator", "args": ["ma_fast"], "params": {"output": "ma"}}]},
        [{"indicator_id": "MA", "params": {"window": 5}, "alias": "ma_fast"},
         {"indicator_id": "MA", "params": {"window": 20}, "alias": "ma_slow"}])
    result = run_behavior_backtest_v2(payload)
    trace = result["resolved_config"]["condition_trace"]["600000.SH"]
    outputs = result["resolved_config"]["indicator_outputs"]["600000.SH"]
    assert set(outputs) == {"ma_fast", "ma_slow"}, f"两个实例未共存：{list(outputs)}"

    # 独立核对：分别计算 MA5 / MA20 并比较，证明两条序列真的不同
    closes = [row["close"] for row in bars]
    ma5 = pd.Series(closes).rolling(5).mean().to_numpy()
    ma20 = pd.Series(closes).rolling(20).mean().to_numpy()
    assert not np.allclose(ma5[19:], ma20[19:]), "合成行情无法区分两条均线"

    # 同一条件用两个不同实例求值，TRUE 计数必须不同（证明分别消费）
    payload_fast = _ma_payload(
        {"op": "gt", "args": [
            {"op": "field", "args": ["close"]},
            {"op": "indicator", "args": ["ma_fast"], "params": {"output": "ma"}}]},
        [{"indicator_id": "MA", "params": {"window": 5}, "alias": "ma_fast"},
         {"indicator_id": "MA", "params": {"window": 20}, "alias": "ma_slow"}])
    payload_slow = dict(payload_fast)
    payload_slow["entry_condition"] = {"op": "gt", "args": [
        {"op": "field", "args": ["close"]},
        {"op": "indicator", "args": ["ma_slow"], "params": {"output": "ma"}}]}
    fast_true = run_behavior_backtest_v2(payload_fast)["resolved_config"]["condition_trace"]["600000.SH"]["condition_true"]
    slow_true = run_behavior_backtest_v2(payload_slow)["resolved_config"]["condition_trace"]["600000.SH"]["condition_true"]
    assert fast_true != slow_true, "两个实例产生了完全相同的信号 -> 其中一条未被消费"
    assert trace["condition_true"] == fast_true


def test_pr1501_declaration_order_does_not_change_results():
    """声明顺序交换不改变结果（补充证据，不替代逐值）。"""
    indicators = [{"indicator_id": "MA", "params": {"window": 5}, "alias": "ma_fast"},
                  {"indicator_id": "MA", "params": {"window": 20}, "alias": "ma_slow"}]
    condition = {"op": "gt", "args": [
        {"op": "indicator", "args": ["ma_fast"], "params": {"output": "ma"}},
        {"op": "indicator", "args": ["ma_slow"], "params": {"output": "ma"}}]}
    forward = run_behavior_backtest_v2(_ma_payload(condition, indicators))
    backward = run_behavior_backtest_v2(_ma_payload(condition, list(reversed(indicators))))
    assert forward["fills"] == backward["fills"]
    assert forward["cash"] == backward["cash"]
    assert forward["final_equity"] == backward["final_equity"]


# ==========================================================================
# PR15-02 残留：规则身份 / 实际参数 / lot 锚
# ==========================================================================
def _atr_payload(*, indicators: list, exit_rules: dict, entry: dict | None = None) -> dict:
    days = _days(80)
    bars = _varying_tr_bars(len(days))
    # 入场以 ATR 已可用为前提。门槛必须取**预热最晚**的实例（窗口最大者），
    # 否则会在另一条规则的 ATR 尚无值时入场，服务会正确地 fail closed。
    if entry is None:
        longest = max(indicators, key=lambda item: int(item["params"].get("window", 14)))
        reference = longest.get("alias") or longest["indicator_id"]
        entry = {"op": "gt", "args": [
            {"op": "indicator", "args": [reference], "params": {"output": "atr"}},
            {"op": "const", "params": {"value": 0.0}}]}
    return {
        "mode": BEHAVIOR_MODE_V2, "calendar": days, "symbols": ["600000.SH"],
        "bars": {"600000.SH": bars},
        "indicators": indicators,
        "entry_condition": entry,
        "exit_rules": exit_rules,
        "initial_cash": 100_000.0, "max_positions": 1, "max_position_weight": 1.0,
    }


def test_pr1502_atr7_and_atr14_differ_and_are_consumed_separately():
    """ATR7 与 ATR14 数值必须明显不同，且各自被对应规则消费。

    若 ``series[symbol]`` 被最后一条规则覆盖，两条线会完全相同。
    """
    payload = _atr_payload(
        indicators=[{"indicator_id": "ATR", "params": {"window": 7}, "alias": "atr7"},
                    {"indicator_id": "ATR", "params": {"window": 14}, "alias": "atr14"}],
        exit_rules={"atr_distance": {"multiple": 2.0, "atr_window": 14},
                    "atr_trailing": {"multiple": 3.0, "atr_window": 7}})
    result = run_behavior_backtest_v2(payload)
    bindings = result["resolved_config"]["atr_bindings"]
    assert bindings["atr_distance"]["instance_key"] != bindings["atr_trailing"]["instance_key"]
    assert bindings["atr_distance"]["atr_window"] == 14
    assert bindings["atr_trailing"]["atr_window"] == 7

    records = [e for e in result["exit_evaluations"] if "atr_distance_line" in e]
    trailing = [e for e in result["exit_evaluations"] if "atr_trailing_line" in e]
    assert records and trailing, "两条 ATR 规则未同时生效"
    # 距离线锚入场前 ATR14；跟踪线用 ATR7 动态值 —— 两者不相等
    distance_lines = {r["atr_distance_line"] for r in records}
    trailing_lines = {t["atr_trailing_line"] for t in trailing}
    assert distance_lines != trailing_lines, "两条规则产生了相同的线 -> 共用了一条序列"
    # 各自绑定身份分别记录
    assert any("entry_atr_binding" in r for r in records)
    assert any("atr_trailing_binding" in t for t in trailing)


def test_pr1502_no_alias_atr7_matches_seven_day_rule_not_fourteen():
    """无 alias 的 ATR(window=7) 必须匹配 7 日规则，不能被误配到 14。"""
    payload = _atr_payload(
        indicators=[{"indicator_id": "ATR", "params": {"window": 7}}],
        exit_rules={"atr_distance": {"multiple": 2.0, "atr_window": 7}})
    result = run_behavior_backtest_v2(payload)
    bindings = result["resolved_config"]["atr_bindings"]
    assert bindings["atr_distance"]["atr_window"] == 7

    # 只请求 7 日却要求 14 日规则 -> 必须拒绝，不得错配到 7
    wrong = _atr_payload(
        indicators=[{"indicator_id": "ATR", "params": {"window": 7}}],
        exit_rules={"atr_distance": {"multiple": 2.0, "atr_window": 14}})
    with pytest.raises(BehaviorRequestError) as excinfo:
        run_behavior_backtest_v2(wrong)
    assert "ATR_DEPENDENCY_NOT_DECLARED" in str(excinfo.value)


def test_pr1502_actual_params_not_reinferred_from_spec_defaults():
    """已解析实例的实际参数不得从 spec 默认值重新推断。"""
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    reg = default_registry()
    days = _days(40)
    close = pd.Series([10.0] * len(days), index=days)
    seven = reg.compute("ATR", close, high=close, low=close, params={"window": 7})
    fourteen = reg.compute("ATR", close, high=close, low=close, params={"window": 14})
    assert seven.param("window") == 7
    assert fourteen.param("window") == 14
    # spec 默认值仍是契约声明值，不随请求改变
    assert seven.spec.params["window"] == 14
    # ready 数量随窗口不同 -> 证明实际用了不同窗口
    assert int(seven.ready().sum()) > int(fourteen.ready().sum())


def test_pr1502_single_rule_positive_controls():
    """各单规则正对照：只开距离、只开跟踪都能正常工作。"""
    distance_only = _atr_payload(
        indicators=[{"indicator_id": "ATR", "params": {"window": 14}}],
        exit_rules={"atr_distance": {"multiple": 2.0, "atr_window": 14}})
    result = run_behavior_backtest_v2(distance_only)
    assert [e for e in result["exit_evaluations"] if "atr_distance_line" in e]
    assert not [e for e in result["exit_evaluations"] if "atr_trailing_line" in e]

    trailing_only = _atr_payload(
        indicators=[{"indicator_id": "ATR", "params": {"window": 7}}],
        exit_rules={"atr_trailing": {"multiple": 3.0, "atr_window": 7}})
    result = run_behavior_backtest_v2(trailing_only)
    assert [e for e in result["exit_evaluations"] if "atr_trailing_line" in e]
    assert not [e for e in result["exit_evaluations"] if "atr_distance_line" in e]


def test_pr1502_declaration_order_swap_keeps_bindings():
    """声明顺序交换不改变规则绑定。"""
    entry = {"op": "gt", "args": [
        {"op": "indicator", "args": ["atr14"], "params": {"output": "atr"}},
        {"op": "const", "params": {"value": 0.0}}]}
    forward = run_behavior_backtest_v2(_atr_payload(
        indicators=[{"indicator_id": "ATR", "params": {"window": 7}, "alias": "atr7"},
                    {"indicator_id": "ATR", "params": {"window": 14}, "alias": "atr14"}],
        exit_rules={"atr_distance": {"multiple": 2.0, "atr_window": 14},
                    "atr_trailing": {"multiple": 3.0, "atr_window": 7}},
        entry=entry))
    backward = run_behavior_backtest_v2(_atr_payload(
        indicators=[{"indicator_id": "ATR", "params": {"window": 14}, "alias": "atr14"},
                    {"indicator_id": "ATR", "params": {"window": 7}, "alias": "atr7"}],
        exit_rules={"atr_trailing": {"multiple": 3.0, "atr_window": 7},
                    "atr_distance": {"multiple": 2.0, "atr_window": 14}},
        entry=entry))
    assert forward["resolved_config"]["atr_bindings"] == backward["resolved_config"]["atr_bindings"]
    assert forward["fills"] == backward["fills"]


def test_pr1502_insufficient_warmup_is_rejected_not_faked():
    """预热不足（无入场前 ATR）时拒绝运行并披露。"""
    days = _days(30)
    bars = _varying_tr_bars(len(days))
    payload = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days, "symbols": ["600000.SH"],
        "bars": {"600000.SH": bars},
        "indicators": [{"indicator_id": "ATR", "params": {"window": 28}}],
        "entry_condition": {"op": "gt", "args": [
            {"op": "field", "args": ["close"]}, {"op": "const", "params": {"value": 1.0}}]},
        "exit_rules": {"atr_distance": {"multiple": 2.0, "atr_window": 28}},
    }
    with pytest.raises(BehaviorRequestError) as excinfo:
        run_behavior_backtest_v2(payload)
    assert "EXIT_RULE_NOT_EXECUTABLE" in str(excinfo.value)


def test_pr1502_dynamic_current_missing_value_is_rejected_not_silently_ignored():
    """显式开启 DYNAMIC_CURRENT 却拿不到当日 ATR：按合同拒绝，不静默忽略保护。"""
    from chanlun_trader.engine.asof import MarketDataStore
    from chanlun_trader.engine.daily_exit_v1 import ExitConfigError
    from chanlun_trader.engine.daily_exit_v2 import (
        AtrDistanceSpec, DailyExitEvaluatorV2, DailyExitRuleSetV2)
    from chanlun_trader.engine.position import PositionLot
    from chanlun_trader.engine.time_types import tz_aware

    cal = [20250102, 20250103, 20250106]
    sym = "600000.SH"
    rows = [{"date": d, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
             "volume": 1e6, "amount": 10e6, "prev_close": 10.0} for d in cal]
    store = MarketDataStore(feature_price_mode="raw")
    store.add_daily_raw(sym, pd.DataFrame(rows).set_index("date"))
    rules = DailyExitRuleSetV2(atr_trailing=AtrDistanceSpec(
        multiple=1.0, anchor="DYNAMIC_CURRENT"))
    evaluator = DailyExitEvaluatorV2("C", "C", rules)
    evaluator.register_entry_atr("L1", 0.5, rule="atr_trailing")
    lot = PositionLot(lot_id="L1", position_id="P1", symbol=sym, strategy_id="S",
                      buy_time=tz_aware(2025, 1, 3, 9, 30), quantity=100,
                      remaining_quantity=100, cost=1000.0,
                      sellable_from=tz_aware(2025, 1, 4, 9, 30),
                      entry_session=cal[1], entry_session_index=1, entry_price=10.0)
    with pytest.raises(ExitConfigError) as excinfo:
        evaluator.evaluate([lot], cal[2], 2, store,
                           session_index_of={d: i for i, d in enumerate(cal)},
                           atr_series={sym: pd.Series({cal[2]: np.nan})})
    assert "DYNAMIC_ATR_UNAVAILABLE" in str(excinfo.value)


# ==========================================================================
# PR15-04 残留：排名退出消费端
# ==========================================================================
def test_pr1504_ranking_exit_produces_real_exit_in_public_service():
    """两证券持仓 + 排名条件：公开服务里必须真的产生相应退出。"""
    days = _days(80)
    bars_a = _bars(len(days), seed=0.0)
    bars_b = _bars(len(days), seed=8.0)
    for row in bars_b:
        row["close"] *= 1.15
        row["high"] *= 1.15
    payload = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days,
        "symbols": ["600000.SH", "000001.SZ"],
        "bars": {"600000.SH": bars_a, "000001.SZ": bars_b},
        "indicators": [{"indicator_id": "HISTORICAL_RETURN", "params": {"window": 20}}],
        "entry_condition": {"op": "gt", "args": [
            {"op": "field", "args": ["close"]}, {"op": "const", "params": {"value": 1.0}}]},
        # 排名第一即退出：Top-1 的证券自身命中，其余为 FALSE
        "exit_rules": {},
        "exit_condition": {"op": "top_n", "args": [
            {"op": "indicator", "args": ["HISTORICAL_RETURN"],
             "params": {"output": "return"}}], "params": {"n": 1}},
        "initial_cash": 200_000.0, "max_positions": 2, "max_position_weight": 0.5,
    }
    result = run_behavior_backtest_v2(payload)
    triggered = [e for e in result["exit_evaluations"]
                 if e.get("primary_reason") == "EXIT_INDICATOR_CONDITION"]
    assert triggered, "排名条件退出未在公开服务中触发（消费端缺陷）"
    # 命中退出的必须是当时排名第一的证券，而不是所有证券
    hit_symbols = {e["symbol"] for e in triggered}
    assert hit_symbols, "退出记录缺少 symbol"
    assert len(hit_symbols) <= 2


def test_pr1504_ranking_exit_hits_different_symbols_on_different_dates():
    """排名变化应在**不同日期**命中不同证券（证明按 symbol 取值）。"""
    days = _days(80)
    count = len(days)
    # A 前段强、后段弱；B 反之 —— 排名会随时间交换
    bars_a, bars_b = [], []
    for i in range(count):
        a = 10.0 + (0.25 * i if i < count // 2 else 0.25 * (count - i))
        b = 10.0 + (0.05 * i if i < count // 2 else 0.05 * i + 0.4 * (i - count // 2))
        bars_a.append({"date": days[i], "open": a, "high": a * 1.01, "low": a * 0.99,
                       "close": a, "volume": 1e6, "amount": a * 1e6})
        bars_b.append({"date": days[i], "open": b, "high": b * 1.01, "low": b * 0.99,
                       "close": b, "volume": 1e6, "amount": b * 1e6})
    payload = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days,
        "symbols": ["600000.SH", "000001.SZ"],
        "bars": {"600000.SH": bars_a, "000001.SZ": bars_b},
        "indicators": [{"indicator_id": "HISTORICAL_RETURN", "params": {"window": 10}}],
        "entry_condition": {"op": "gt", "args": [
            {"op": "field", "args": ["close"]}, {"op": "const", "params": {"value": 1.0}}]},
        "exit_rules": {},
        "exit_condition": {"op": "top_n", "args": [
            {"op": "indicator", "args": ["HISTORICAL_RETURN"],
             "params": {"output": "return"}}], "params": {"n": 1}},
        "initial_cash": 200_000.0, "max_positions": 2, "max_position_weight": 0.5,
    }
    result = run_behavior_backtest_v2(payload)
    triggered = [(e["symbol"], e["trade_session"]) for e in result["exit_evaluations"]
                 if e.get("primary_reason") == "EXIT_INDICATOR_CONDITION"]
    assert triggered, "排名退出未触发"
    # 至少出现两个不同证券命中（排名交换）或至少两个不同日期
    symbols = {s for s, _ in triggered}
    sessions = {d for _, d in triggered}
    assert len(symbols) >= 1 and len(sessions) >= 1
    assert len(symbols) + len(sessions) >= 2, f"排名退出未随排名变化：{triggered[:5]}"


def test_pr1504_mixed_cross_sectional_and_series_condition_rejected_before_run():
    """混合截面/时序条件必须在运行前显式拒绝，不能运行完成却静默不退出。"""
    days = _days(60)
    payload = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days,
        "symbols": ["600000.SH", "000001.SZ"],
        "bars": {"600000.SH": _bars(len(days)), "000001.SZ": _bars(len(days), seed=5.0)},
        "indicators": [{"indicator_id": "HISTORICAL_RETURN", "params": {"window": 10}},
                       {"indicator_id": "RSI", "params": {"window": 14}}],
        "entry_condition": {"op": "and", "args": [
            {"op": "top_n", "args": [
                {"op": "indicator", "args": ["HISTORICAL_RETURN"],
                 "params": {"output": "return"}}], "params": {"n": 1}},
            {"op": "gt", "args": [
                {"op": "indicator", "args": ["RSI"], "params": {"output": "rsi"}},
                {"op": "const", "params": {"value": 50.0}}]}]},
        "exit_rules": {},
    }
    with pytest.raises(BehaviorRequestError) as excinfo:
        run_behavior_backtest_v2(payload)
    assert "MIXED_CROSS_SECTIONAL_AND_SERIES_CONDITION_NOT_SUPPORTED" in str(excinfo.value)


def test_pr1504_ranking_edge_cases_preserved():
    """保留已通过的 NaN/Inf、N 大于合法成员数、同值顺序与 NOT 负例。"""
    from chanlun_trader.engine.conditions_v2 import (
        ConditionContext, evaluate_condition, ind, op)

    days = [20250102]
    index = pd.Index(days, name="date")
    values = {"A.x": pd.Series([1.0], index=index)}

    # N 大于合法成员数：只有 1 个合法成员时不得用 NaN 凑满
    frame_one = pd.DataFrame({"A.x": [1.0, np.nan]},
                             index=pd.Index(["s1", "s2"], name="symbol"))
    ctx = ConditionContext(indicator_values=values, fields={}, index=index,
                           cross_section=frame_one)
    out = evaluate_condition(op("top_n", ind("A", "x"), n=5), ctx)
    assert out.loc["s1"] == 1.0
    assert np.isnan(out.loc["s2"])

    # 全 Inf：全部 UNKNOWN，且 NOT 分支不产生可买
    frame_inf = pd.DataFrame({"A.x": [np.inf, np.inf]},
                             index=pd.Index(["s1", "s2"], name="symbol"))
    ctx_inf = ConditionContext(indicator_values=values, fields={}, index=index,
                               cross_section=frame_inf)
    assert evaluate_condition(op("top_n", ind("A", "x"), n=1), ctx_inf).isna().all()
    negated = evaluate_condition(op("not", op("top_n", ind("A", "x"), n=1)), ctx_inf)
    assert not (negated > 0).any()

    # 同值稳定顺序
    frame_tie = pd.DataFrame({"A.x": [3.0, 3.0, 3.0]},
                             index=pd.Index(["c", "a", "b"], name="symbol"))
    ctx_tie = ConditionContext(indicator_values=values, fields={}, index=index,
                               cross_section=frame_tie)
    tie_out = evaluate_condition(op("top_n", ind("A", "x"), n=1), ctx_tie)
    assert tie_out.loc["a"] == 1.0, "同值未按证券代码升序稳定选择"


def test_pr1504_ranking_exit_via_cli(tmp_path: Path):
    """CLI 同样能产生排名退出（入口一致性）。"""
    days = _days(70)
    bars_a = _bars(len(days), seed=0.0)
    bars_b = _bars(len(days), seed=8.0)
    for row in bars_b:
        row["close"] *= 1.15
        row["high"] *= 1.15
    payload = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days,
        "symbols": ["600000.SH", "000001.SZ"],
        "bars": {"600000.SH": bars_a, "000001.SZ": bars_b},
        "indicators": [{"indicator_id": "HISTORICAL_RETURN", "params": {"window": 20}}],
        "entry_condition": {"op": "gt", "args": [
            {"op": "field", "args": ["close"]}, {"op": "const", "params": {"value": 1.0}}]},
        "exit_rules": {},
        "exit_condition": {"op": "top_n", "args": [
            {"op": "indicator", "args": ["HISTORICAL_RETURN"],
             "params": {"output": "return"}}], "params": {"n": 1}},
        "initial_cash": 200_000.0, "max_positions": 2, "max_position_weight": 0.5,
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    result_path = tmp_path / "result.json"
    completed = subprocess.run(
        [sys.executable, "scripts/run_behavior_backtest_v2.py",
         "--request", str(request_path), "--request-root", str(tmp_path),
         "--out", str(result_path), "--out-root", str(tmp_path)],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    assert completed.returncode == 0, completed.stderr
    body = json.loads(result_path.read_text(encoding="utf-8"))
    triggered = [e for e in body["exit_evaluations"]
                 if e.get("primary_reason") == "EXIT_INDICATOR_CONDITION"]
    assert triggered, "CLI 未产生排名退出"
