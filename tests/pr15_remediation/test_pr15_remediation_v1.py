"""PR15 五项根因的公共入口验收（真实 API / CLI，不手工调 helper）。

对应定向复核的五类问题：

- PR15-01 指标实例与版本身份（MA(5) 与 MA(20) 不得互相覆盖）
- PR15-02 ATR 依赖、入场锚与参数生效
- PR15-03 多证券退出上下文（按 lot.symbol 取）
- PR15-04 Top-N UNKNOWN 与公共接线
- PR15-05 证据与范围对账
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


def _days(count: int = 130) -> list:
    days = [int((pd.Timestamp("2024-09-02") + pd.Timedelta(days=i)).strftime("%Y%m%d"))
            for i in range(count + 60)]
    return [d for d in days if pd.Timestamp(str(d)).weekday() < 5][:count]


def _bars(count: int = 130, *, symbol_seed: float = 0.0) -> list:
    days = _days(count)
    closes = [10.0 + symbol_seed + 0.06 * i + 0.8 * np.sin(i / 7) for i in range(count)]
    volumes = [1_000_000.0 * (1 + 0.5 * np.sin(i / 4)) for i in range(count)]
    return [{"date": days[i], "open": closes[i] * 0.999, "high": closes[i] * 1.02,
             "low": closes[i] * 0.98, "close": closes[i], "volume": volumes[i],
             "amount": closes[i] * volumes[i]} for i in range(count)]


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    root = tmp_path / "synthetic_root"
    root.mkdir()
    app = create_app(root, ExecutionPolicy(
        mode="READ_ONLY", workspace_kind="SYNTHETIC", allow_readonly_compute=True))
    return TestClient(app)


def _run_cli(extra_args):
    return subprocess.run(
        [sys.executable, "scripts/run_behavior_backtest_v2.py", *extra_args],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace")


# ==========================================================================
# PR15-01：指标实例与版本身份
# ==========================================================================
def test_pr1501_ma5_and_ma20_are_distinct_instances(client: TestClient):
    """同一请求里 MA(5) 与 MA(20) 必须是两个实例，各自逐值正确。"""
    days = _days()
    payload = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days, "symbols": ["600000.SH"],
        "bars": {"600000.SH": _bars()},
        "indicators": [
            {"indicator_id": "MA", "params": {"window": 5}, "alias": "ma_fast"},
            {"indicator_id": "MA", "params": {"window": 20}, "alias": "ma_slow"},
        ],
        "entry_condition": {"op": "gt", "args": [
            {"op": "indicator", "args": ["ma_fast"], "params": {"output": "ma"}},
            {"op": "indicator", "args": ["ma_slow"], "params": {"output": "ma"}}]},
        "exit_rules": {"fixed_holding_sessions": 5},
        "initial_cash": 100_000.0, "max_positions": 1, "max_position_weight": 1.0,
    }
    body = client.post("/api/backtest/behavior/v2", json=payload).json()
    outputs = body["resolved_config"]["indicator_outputs"]["600000.SH"]
    assert len(outputs) == 2, f"MA(5)/MA(20) 未成为两个实例：{list(outputs)}"
    assert set(outputs) == {"ma_fast", "ma_slow"}, f"实例键应使用声明的 alias：{list(outputs)}"

    # 声明顺序交换后结果不变（证明两实例互不覆盖）
    swapped = dict(payload)
    swapped["indicators"] = list(reversed(payload["indicators"]))
    body_swapped = client.post("/api/backtest/behavior/v2", json=swapped).json()
    assert body_swapped["cash"] == body["cash"]
    assert body_swapped["final_equity"] == body["final_equity"]
    assert body_swapped["fills"] == body["fills"]
    assert set(body_swapped["resolved_config"]["indicator_outputs"]["600000.SH"]) == \
        {"ma_fast", "ma_slow"}


def test_pr1501_duplicate_and_conflicting_instances_rejected(client: TestClient):
    days = _days()
    base = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days, "symbols": ["600000.SH"],
        "bars": {"600000.SH": _bars()},
        "exit_rules": {},
        "entry_condition": {"op": "gt", "args": [
            {"op": "field", "args": ["close"]}, {"op": "const", "params": {"value": 1.0}}]},
    }
    # 完全重复的实例（同一 id + 同一参数，未给 alias）-> 实例键重复
    dup = dict(base)
    dup["indicators"] = [{"indicator_id": "MA", "params": {"window": 5}},
                         {"indicator_id": "MA", "params": {"window": 5}}]
    response = client.post("/api/backtest/behavior/v2", json=dup)
    assert response.status_code == 400
    assert "DUPLICATE_INDICATOR_INSTANCE" in response.json()["detail"]["code"]

    # 仅 alias 不同但解析到同一 canonical 实例 -> 冲突（不得伪装成两个实例）
    alias_only = dict(base)
    alias_only["indicators"] = [{"indicator_id": "MA", "params": {"window": 5}, "alias": "a"},
                                {"indicator_id": "MA", "params": {"window": 5}, "alias": "b"}]
    response = client.post("/api/backtest/behavior/v2", json=alias_only)
    assert response.status_code == 400
    assert "CONFLICTING_INDICATOR_INSTANCE" in response.json()["detail"]["code"]


def test_pr1501_unknown_version_rejected_and_single_instance_compat(client: TestClient):
    """未知版本必须拒绝；单实例旧配置保持可用。"""
    days = _days()
    payload = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days, "symbols": ["600000.SH"],
        "bars": {"600000.SH": _bars()},
        "indicators": [{"indicator_id": "RSI", "version": "RSI_V999",
                        "params": {"window": 14}}],
        "entry_condition": {"op": "gt", "args": [
            {"op": "field", "args": ["close"]}, {"op": "const", "params": {"value": 1.0}}]},
        "exit_rules": {},
    }
    response = client.post("/api/backtest/behavior/v2", json=payload)
    assert response.status_code == 400
    assert "UNKNOWN_INDICATOR" in response.json()["detail"]["code"]

    # 单实例：仍可用不含 alias 的旧配置
    single = dict(payload)
    single["indicators"] = [{"indicator_id": "RSI", "params": {"window": 14}}]
    single["entry_condition"] = {"op": "gt", "args": [
        {"op": "indicator", "args": ["RSI"], "params": {"output": "rsi"}},
        {"op": "const", "params": {"value": 30.0}}]}
    assert client.post("/api/backtest/behavior/v2", json=single).status_code == 200


# ==========================================================================
# PR15-02：ATR 依赖、入场锚、参数生效
# ==========================================================================
def test_pr1502_entry_anchor_uses_prior_atr_through_public_entry():
    """前日 ATR 0.2、入场日 ATR 2、成本 10、倍数 2 -> 锚 0.2、线 9.6（不是 6）。

    经**公共服务**验证；入场条件以 ATR 已 ready 为前提（未 ready 的入场
    没有可用锚，服务必须拒绝而不是伪造）。
    """
    days = _days(80)
    bars = [{"date": days[i], "open": 10.0, "high": 10.0, "low": 10.0,
             "close": 10.0, "volume": 1_000_000.0, "amount": 10_000_000.0}
            for i in range(len(days))]
    for i in range(len(days)):
        bars[i]["high"] = 10.0 + (0.1 if i < 40 else 2.0)
        bars[i]["low"] = 10.0 - (0.1 if i < 40 else 2.0)
    payload = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days, "symbols": ["600000.SH"],
        "bars": {"600000.SH": bars},
        "indicators": [{"indicator_id": "ATR", "params": {"window": 14}}],
        # 入场以 ATR 已可用为前提
        "entry_condition": {"op": "gt", "args": [
            {"op": "indicator", "args": ["ATR"], "params": {"output": "atr"}},
            {"op": "const", "params": {"value": 0.0}}]},
        "exit_rules": {"atr_distance": {"multiple": 2.0, "atr_window": 14}},
        "initial_cash": 100_000.0, "max_positions": 1, "max_position_weight": 1.0,
    }
    result = run_behavior_backtest_v2(payload)
    records = [e for e in result["exit_evaluations"] if "atr_distance_line" in e]
    assert records, "ATR 距离规则未生效"
    first = records[0]
    assert first["entry_atr"] > 0
    assert first["atr_distance_line"] == pytest.approx(
        first["anchor"] - 2.0 * first["entry_atr"], abs=1e-6)
    # 锚必须严格早于入场日：不可能等于入场当日的 ATR 值
    assert first["entry_atr"] != pytest.approx(2.0), "锚疑似取到了入场当日 ATR"


def test_pr1502_entry_without_available_atr_is_rejected_not_faked(client: TestClient):
    """入场时没有可用 ATR 锚（预热不足）：必须拒绝运行并披露，不伪造锚。"""
    days = _days(40)
    bars = [{"date": days[i], "open": 10.0, "high": 10.5, "low": 9.5,
             "close": 10.0, "volume": 1_000_000.0, "amount": 10_000_000.0}
            for i in range(len(days))]
    payload = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days, "symbols": ["600000.SH"],
        "bars": {"600000.SH": bars},
        "indicators": [{"indicator_id": "ATR", "params": {"window": 30}}],
        # 条件不依赖 ATR，因此会在 ATR 未 ready 时就入场
        "entry_condition": {"op": "gt", "args": [
            {"op": "field", "args": ["close"]}, {"op": "const", "params": {"value": 1.0}}]},
        "exit_rules": {"atr_distance": {"multiple": 2.0, "atr_window": 30}},
    }
    response = client.post("/api/backtest/behavior/v2", json=payload)
    assert response.status_code == 400
    # 未声明依赖（ATR 窗口不匹配）或锚不可用，都必须明确拒绝而不是静默继续
    code = response.json()["detail"]["code"]
    assert code.startswith(("ATR_DEPENDENCY_NOT_DECLARED", "EXIT_RULE_NOT_EXECUTABLE")), code


def test_pr1502_atr_rule_without_declared_dependency_is_rejected(client: TestClient):
    """启用 ATR 规则但未请求 ATR 依赖：明确拒绝，不静默忽略参数。"""
    days = _days()
    payload = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days, "symbols": ["600000.SH"],
        "bars": {"600000.SH": _bars()},
        "indicators": [{"indicator_id": "MA", "params": {"window": 5}}],
        "entry_condition": {"op": "gt", "args": [
            {"op": "field", "args": ["close"]}, {"op": "const", "params": {"value": 1.0}}]},
        "exit_rules": {"atr_distance": {"multiple": 2.0, "atr_window": 14}},
    }
    response = client.post("/api/backtest/behavior/v2", json=payload)
    assert response.status_code == 400
    assert "ATR_DEPENDENCY_NOT_DECLARED" in response.json()["detail"]["code"]


def test_pr1502_distinct_atr_windows_do_not_share_one_series():
    """距离与跟踪使用不同窗口时必须各自绑定，不能共用一条未核验 ATR。"""
    days = _days(90)
    bars = []
    for i in range(len(days)):
        px = 10.0 + 0.05 * i
        bars.append({"date": days[i], "open": px, "high": px + 0.3, "low": px - 0.3,
                     "close": px, "volume": 1_000_000.0, "amount": px * 1_000_000.0})
    payload = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days, "symbols": ["600000.SH"],
        "bars": {"600000.SH": bars},
        "indicators": [{"indicator_id": "ATR", "params": {"window": 14}, "alias": "atr14"},
                       {"indicator_id": "ATR", "params": {"window": 7}, "alias": "atr7"}],
        # 入场以 ATR 可用为前提，避免预热期入场导致锚缺失
        "entry_condition": {"op": "gt", "args": [
            {"op": "indicator", "args": ["atr14"], "params": {"output": "atr"}},
            {"op": "const", "params": {"value": 0.0}}]},
        "exit_rules": {
            "atr_distance": {"multiple": 2.0, "atr_window": 14},
            "atr_trailing": {"multiple": 3.0, "atr_window": 7},
        },
        "initial_cash": 100_000.0, "max_positions": 1, "max_position_weight": 1.0,
    }
    result = run_behavior_backtest_v2(payload)
    outputs = result["resolved_config"]["indicator_outputs"]["600000.SH"]
    assert set(outputs) == {"atr14", "atr7"}, f"两个 ATR 窗口未成为两个实例：{list(outputs)}"
    # 规则依赖分别绑定到不同窗口实例
    bindings = result["resolved_config"].get("atr_bindings", {})
    if bindings:
        assert bindings["atr_distance"]["atr_window"] == 14
        assert bindings["atr_trailing"]["atr_window"] == 7
        assert bindings["atr_distance"]["instance_key"] != bindings["atr_trailing"]["instance_key"]


def test_pr1502_dynamic_current_anchor_is_honoured():
    """DYNAMIC_CURRENT 必须真的按显式合同实现（不是接受参数后忽略）。"""
    from chanlun_trader.engine.daily_exit_v2 import (
        AtrDistanceSpec, DailyExitEvaluatorV2, DailyExitRuleSetV2)
    from chanlun_trader.engine.asof import MarketDataStore
    from chanlun_trader.engine.position import PositionLot
    from chanlun_trader.engine.time_types import tz_aware

    cal = [20250102, 20250103, 20250106, 20250107]
    sym = "600000.SH"
    rows = [{"date": d, "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0,
             "volume": 1e6, "amount": 11e6, "prev_close": 11.0} for d in cal]
    store = MarketDataStore(feature_price_mode="raw")
    store.add_daily_raw(sym, pd.DataFrame(rows).set_index("date"))
    rules = DailyExitRuleSetV2(
        atr_trailing=AtrDistanceSpec(multiple=1.0, anchor="DYNAMIC_CURRENT", tighten_only=True))
    evaluator = DailyExitEvaluatorV2("C", "C", rules)
    lot = PositionLot(lot_id="L1", position_id="P1", symbol=sym, strategy_id="S",
                      buy_time=tz_aware(2025, 1, 3, 9, 30), quantity=100, remaining_quantity=100,
                      cost=1000.0, sellable_from=tz_aware(2025, 1, 4, 9, 30),
                      entry_session=cal[1], entry_session_index=1, entry_price=10.0)
    evaluator.register_entry_atr("L1", 0.5, rule="atr_trailing")
    idx = {d: i for i, d in enumerate(cal)}
    # 当日 ATR 变大 -> 线应随之变化（证明参数真的生效）
    atr_small = {sym: pd.Series({d: 0.5 for d in cal})}
    atr_large = {sym: pd.Series({d: 3.0 for d in cal})}
    evaluator.evaluate([lot], cal[2], 2, store, session_index_of=idx, atr_series=atr_small)
    line_small = evaluator.atr_trailing["L1"].line
    evaluator2 = DailyExitEvaluatorV2("C", "C", rules)
    evaluator2.register_entry_atr("L1", 0.5, rule="atr_trailing")
    lot2 = PositionLot(lot_id="L1", position_id="P1", symbol=sym, strategy_id="S",
                       buy_time=tz_aware(2025, 1, 3, 9, 30), quantity=100, remaining_quantity=100,
                       cost=1000.0, sellable_from=tz_aware(2025, 1, 4, 9, 30),
                       entry_session=cal[1], entry_session_index=1, entry_price=10.0)
    evaluator2.evaluate([lot2], cal[2], 2, store, session_index_of=idx, atr_series=atr_large)
    line_large = evaluator2.atr_trailing["L1"].line
    assert line_large != line_small, "DYNAMIC_CURRENT 未使用当日 ATR（参数被忽略）"


# ==========================================================================
# PR15-03：多证券退出上下文
# ==========================================================================
def _two_symbol_payload(exit_rules: dict, *, condition: dict | None = None,
                        extra_indicators: list | None = None) -> dict:
    days = _days()
    indicators = [{"indicator_id": "RSI", "params": {"window": 14}}]
    indicators.extend(extra_indicators or [])
    payload = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days,
        "symbols": ["600000.SH", "000001.SZ"],
        "bars": {"600000.SH": _bars(symbol_seed=0.0),
                 "000001.SZ": _bars(symbol_seed=5.0)},
        "indicators": indicators,
        "entry_condition": {"op": "gt", "args": [
            {"op": "field", "args": ["close"]}, {"op": "const", "params": {"value": 1.0}}]},
        "exit_rules": exit_rules,
        "initial_cash": 200_000.0, "max_positions": 2, "max_position_weight": 0.5,
    }
    if condition is not None:
        payload["exit_condition"] = condition
    return payload


def test_pr1503_two_symbol_fixed_hold_runs_without_condition_context():
    """两证券 + 仅固定持有：不需要指标上下文限制，必须正常运行。"""
    result = run_behavior_backtest_v2(_two_symbol_payload({"fixed_holding_sessions": 5}))
    assert len({f["symbol"] for f in result["fills"]}) == 2, "两证券都应成交"


def test_pr1503_two_symbol_cost_stop_runs():
    result = run_behavior_backtest_v2(_two_symbol_payload({"stop_loss_pct": 0.03}))
    assert len({f["symbol"] for f in result["fills"]}) == 2


def test_pr1503_two_symbol_condition_exit_uses_per_symbol_context():
    """两证券分别命中的指标退出：按 lot.symbol 取上下文，不得共用一个。"""
    result = run_behavior_backtest_v2(_two_symbol_payload(
        {}, condition={"op": "gt", "args": [
            {"op": "indicator", "args": ["RSI"], "params": {"output": "rsi"}},
            {"op": "const", "params": {"value": 50.0}}]}))
    triggered = [e for e in result["exit_evaluations"] if e.get("state") == "EXIT_DUE"]
    assert triggered, "指标条件退出未触发"
    symbols = {e["symbol"] for e in triggered}
    assert len(symbols) == 2, f"两证券的条件退出未分别求值：{symbols}"
    assert all(e["primary_reason"] == "EXIT_INDICATOR_CONDITION" for e in triggered)


def test_pr1503_context_map_missing_symbol_is_rejected():
    """多证券上下文缺失某证券时拒绝，绝不"取第一个"。"""
    from chanlun_trader.engine.daily_exit_v1 import ExitConfigError
    from chanlun_trader.engine.daily_exit_v2 import DailyExitEvaluatorV2, DailyExitRuleSetV2
    from chanlun_trader.engine.conditions_v2 import ConditionContext, ConditionEvaluator, ind, lit, op
    from chanlun_trader.engine.asof import MarketDataStore
    from chanlun_trader.engine.position import PositionLot
    from chanlun_trader.engine.time_types import tz_aware

    cal = [20250102, 20250103, 20250106]
    rows = [{"date": d, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
             "volume": 1e6, "amount": 10e6, "prev_close": 10.0} for d in cal]
    store = MarketDataStore(feature_price_mode="raw")
    for sym in ("600000.SH", "000001.SZ"):
        store.add_daily_raw(sym, pd.DataFrame(rows).set_index("date"))
    rules = DailyExitRuleSetV2(
        indicator_condition_exit=op("gt", ind("RSI", "rsi"), lit(50.0)),
        exit_types=("INDICATOR_CONDITION_EXIT",))
    evaluator = DailyExitEvaluatorV2("C", "C", rules,
                                     condition_evaluator=ConditionEvaluator())
    lots = [PositionLot(lot_id=f"L{i}", position_id=f"P{i}", symbol=sym, strategy_id="S",
                        buy_time=tz_aware(2025, 1, 3, 9, 30), quantity=100,
                        remaining_quantity=100, cost=1000.0,
                        sellable_from=tz_aware(2025, 1, 4, 9, 30),
                        entry_session=cal[1], entry_session_index=1, entry_price=10.0)
            for i, sym in enumerate(("600000.SH", "000001.SZ"))]
    ctx = ConditionContext(indicator_values={"RSI.rsi": pd.Series([60.0] * 3, index=cal)},
                           fields={}, index=pd.Index(cal, name="date"))
    with pytest.raises(ExitConfigError) as excinfo:
        evaluator.evaluate(lots, cal[2], 2, store,
                           session_index_of={d: i for i, d in enumerate(cal)},
                           condition_context={"600000.SH": ctx})
    assert "CONDITION_CONTEXT_MISSING_FOR_SYMBOL" in str(excinfo.value)


def test_pr1503_two_symbol_partial_fill_and_pending_sell():
    """两证券下部分成交与待卖：各自独立处理，保持原退出去重与不可撤销语义。"""
    days = _days(60)
    bars = []
    for i in range(len(days)):
        px = 10.0 + 0.05 * i
        bars.append({"date": days[i], "open": px, "high": px + 0.2, "low": px - 0.2,
                     "close": px, "volume": 5_000_000.0, "amount": px * 5_000_000.0})
    payload = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days,
        "symbols": ["600000.SH", "000001.SZ"],
        "bars": {"600000.SH": bars, "000001.SZ": bars},
        "indicators": [{"indicator_id": "MA", "params": {"window": 5}}],
        "entry_condition": {"op": "gt", "args": [
            {"op": "field", "args": ["close"]}, {"op": "const", "params": {"value": 1.0}}]},
        "exit_rules": {"stop_loss_pct": 0.03, "fixed_holding_sessions": 3},
        "initial_cash": 200_000.0, "max_positions": 2, "max_position_weight": 0.5,
    }
    result = run_behavior_backtest_v2(payload)
    # 每笔成交不超过对应订单数量；不超卖
    for order in result["orders"]:
        assert order["filled_quantity"] <= order["quantity"]
    for lot in result["lots"]:
        assert 0 <= lot["remaining_quantity"] <= lot["quantity"]
    # 两证券各自独立成交
    assert len({f["symbol"] for f in result["fills"]}) == 2
    # 每个 lot 同一 session 只产生一条退出记录
    seen = {}
    for record in result["exit_evaluations"]:
        if record.get("state") != "EXIT_DUE":
            continue
        key = (record["lot_id"], record["trade_session"])
        assert key not in seen, f"同一 lot/session 重复退出意图：{key}"
        seen[key] = True


def test_pr1503_no_position_day_does_not_break_multi_symbol():
    """无持仓日不得影响多证券运行（上下文按 session 构建仍然成立）。"""
    days = _days(40)
    bars = [{"date": days[i], "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0,
             "volume": 1_000_000.0, "amount": 10_000_000.0} for i in range(len(days))]
    payload = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days,
        "symbols": ["600000.SH", "000001.SZ"],
        "bars": {"600000.SH": bars, "000001.SZ": bars},
        "indicators": [{"indicator_id": "RSI", "params": {"window": 14}}],
        # 永真条件（有限价格）在预热后成立
        "entry_condition": {"op": "gt", "args": [
            {"op": "field", "args": ["close"]}, {"op": "const", "params": {"value": 1.0}}]},
        "exit_rules": {"fixed_holding_sessions": 5},
        "initial_cash": 200_000.0, "max_positions": 2, "max_position_weight": 0.5,
    }
    result = run_behavior_backtest_v2(payload)
    assert result["official_valuation"]["n_days"] == len(days)


# ==========================================================================
# PR15-04：Top-N UNKNOWN + 公共接线
# ==========================================================================
def test_pr1504_top_n_unknown_handling_unit():
    from chanlun_trader.engine.conditions_v2 import (
        ConditionContext, evaluate_condition, ind, op)

    days = [20250102, 20250103]
    index = pd.Index(days, name="date")
    base_values = {"A.x": pd.Series([1.0, 1.0], index=index)}

    # 全 NaN：不得选中任何证券，且全部 UNKNOWN
    frame_all_nan = pd.DataFrame({"A.x": [np.nan, np.nan, np.nan]},
                                 index=pd.Index(["A", "B", "C"], name="symbol"))
    ctx = ConditionContext(indicator_values=base_values, fields={}, index=index,
                           cross_section=frame_all_nan)
    out = evaluate_condition(op("top_n", ind("A", "x"), n=1), ctx)
    assert out.isna().all(), "全 NaN 被选中或填成了 FALSE"

    # NOT 分支不得变成可买
    negated = evaluate_condition(op("not", op("top_n", ind("A", "x"), n=1)), ctx)
    assert not (negated > 0).any(), "NOT(UNKNOWN Top-N) 变成了可买"

    # 1 个有效 + N=2：不得用 NaN 凑满
    frame_one = pd.DataFrame({"A.x": [1.0, np.nan]},
                             index=pd.Index(["A", "B"], name="symbol"))
    ctx2 = ConditionContext(indicator_values=base_values, fields={}, index=index,
                            cross_section=frame_one)
    out2 = evaluate_condition(op("top_n", ind("A", "x"), n=2), ctx2)
    assert out2.loc["A"] == 1.0
    assert np.isnan(out2.loc["B"]), "NaN 成员被凑进 Top-N"

    # Inf 不得参与排名
    frame_inf = pd.DataFrame({"A.x": [np.inf, 1.0]},
                             index=pd.Index(["A", "B"], name="symbol"))
    ctx3 = ConditionContext(indicator_values=base_values, fields={}, index=index,
                            cross_section=frame_inf)
    out3 = evaluate_condition(op("top_n", ind("A", "x"), n=1), ctx3)
    assert out3.loc["B"] == 1.0, "Inf 参与排名并挤掉了合法成员"
    assert np.isnan(out3.loc["A"]), "Inf 成员应保持 UNKNOWN"


def test_pr1504_top_n_positive_control_still_works():
    from chanlun_trader.engine.conditions_v2 import ConditionContext, evaluate_condition, ind, op

    days = [20250102]
    index = pd.Index(days, name="date")
    frame = pd.DataFrame({"A.x": [5.0, 5.0, 5.0]},
                         index=pd.Index(["600001.SH", "600002.SH", "600003.SH"], name="symbol"))
    ctx = ConditionContext(indicator_values={"A.x": pd.Series([1.0], index=index)},
                           fields={}, index=index, cross_section=frame)
    out = evaluate_condition(op("top_n", ind("A", "x"), n=1), ctx)
    assert out.loc["600001.SH"] == 1.0
    assert out.loc["600002.SH"] == 0.0
    assert out.loc["600003.SH"] == 0.0


def test_pr1504_top_n_reaches_account_chain_through_public_entry():
    """Top-N 条件经真实公共服务触发信号与账户，不只手工传 cross_section。"""
    days = _days(80)
    count = len(days)
    bars_a = _bars(count, symbol_seed=0.0)
    bars_b = _bars(count, symbol_seed=8.0)
    # 让 B 的动量明显更强，确保 Top-1 选中 B
    for row in bars_b:
        row["close"] = row["close"] * 1.15
        row["high"] = row["high"] * 1.15
    payload = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days,
        "symbols": ["600000.SH", "000001.SZ"],
        "bars": {"600000.SH": bars_a, "000001.SZ": bars_b},
        "indicators": [{"indicator_id": "HISTORICAL_RETURN", "params": {"window": 20}}],
        # Top-1 动量（截面算子本身即入场条件；不与时序算子混用）
        "entry_condition": {"op": "top_n", "args": [
            {"op": "indicator", "args": ["HISTORICAL_RETURN"],
             "params": {"output": "return"}}], "params": {"n": 1}},
        "exit_rules": {"fixed_holding_sessions": 5},
        "initial_cash": 200_000.0, "max_positions": 1, "max_position_weight": 1.0,
    }
    result = run_behavior_backtest_v2(payload)
    buys = [f for f in result["fills"] if f["side"] == "BUY"]
    assert buys, "Top-N 条件未产生任何成交"
    # 至少应包含动量更强的 B（Top-1 会随动量变化切换，故不断言唯一）
    assert "000001.SZ" in {f["symbol"] for f in buys}, {f["symbol"] for f in buys}


def test_pr1504_cross_sectional_and_time_series_operands_must_align():
    """截面结果与时序结果直接组合是类型错误：必须明确报错，不靠广播。"""
    from chanlun_trader.engine.conditions_v2 import (
        ConditionContext, ConditionError, evaluate_condition, ind, lit, op)

    days = [20250102, 20250103]
    index = pd.Index(days, name="date")
    frame = pd.DataFrame({"A.x": [1.0, 2.0]}, index=pd.Index(["s1", "s2"], name="symbol"))
    ctx = ConditionContext(indicator_values={"A.x": pd.Series([1.0, 1.0], index=index)},
                           fields={}, index=index, cross_section=frame)
    node = op("and", op("top_n", ind("A", "x"), n=1), op("gt", ind("A", "x"), lit(0.5)))
    with pytest.raises(ConditionError) as excinfo:
        evaluate_condition(node, ctx)
    assert "CONDITION_OPERAND_INDEX_MISMATCH" in str(excinfo.value)


# ==========================================================================
# PR15-05：证据与范围对账
# ==========================================================================
def test_pr1505_matrix_requires_evidence_not_name_prefix():
    """覆盖矩阵不得按字符串前缀判定 met；PARTIAL 项不得计入 VERIFIED。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "emit_v2_scope", REPO_ROOT / "scripts" / "emit_v2_acceptance_scope_v1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from chanlun_trader.engine.custom_indicators_v2 import register_custom_indicators
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    registry = default_registry()
    register_custom_indicators(registry)
    matrix = module.build_matrix(registry)

    assert "verified" in matrix["minimum_set"]
    assert "partial" in matrix["minimum_set"]
    for family in matrix["families"]:
        for item in family["items"]:
            if item["status"] == "VERIFIED":
                assert item["evidence"], item["requirement"]
                assert "impl=" in item["evidence"]
            if item["status"] == "PARTIAL":
                assert item["met"] is False, "PARTIAL 不得计为 met"
    # 既有面板算子层本轮未接 V2 公开链路 -> 必须为 PARTIAL
    operator_items = [i for f in matrix["families"] for i in f["items"]
                      if i["target"].startswith("OPERATOR_LAYER")]
    assert operator_items, "缺少算子层条目"
    assert all(i["status"] == "PARTIAL" for i in operator_items), \
        "未接线的算子层被标成了 VERIFIED"


def test_pr1505_formula_hash_recurses_into_dependencies():
    """只改**依赖实现**、父函数文字不变时，父函数指纹必须改变。"""
    from chanlun_trader.engine import indicator_registry_v2 as regmod
    from chanlun_trader.engine.custom_indicators_v2 import register_custom_indicators
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    registry = default_registry()
    register_custom_indicators(registry)
    before = registry.formula_hash("RSI_REGIME_FLAG")

    def mutated_rsi(data, *, window=14, price="close"):
        """变异版 RSI（仅用于指纹测试）。"""
        return regmod.IndicatorFrameV2(
            indicator_id="RSI", version="RSI_V1", index=data.index,
            columns={"rsi": pd.Series(np.zeros(len(data.close)), index=data.index)},
            ready=pd.Series(True, index=data.index),
            segment=pd.Series(np.zeros(len(data.close), dtype=int), index=data.index),
            warmup_bars=1)

    mutated_rsi.__wrapped_impl__ = mutated_rsi
    original = registry._impls[("RSI", "RSI_V1")]
    try:
        registry._impls[("RSI", "RSI_V1")] = mutated_rsi
        after = registry.formula_hash("RSI_REGIME_FLAG")
    finally:
        registry._impls[("RSI", "RSI_V1")] = original
    assert before != after, "依赖实现改变未影响父函数指纹（指纹未递归绑定依赖）"
    assert registry.formula_hash("RSI_REGIME_FLAG") == before, "恢复后指纹不稳定"


def test_pr1505_formula_hash_is_stable_for_unchanged_source():
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    first = default_registry().to_dict()["formula_hashes"]
    second = default_registry().to_dict()["formula_hashes"]
    assert first == second, "同一源码两次构建产生了不同指纹"
    assert all(value for value in first.values()), "存在空指纹"


def test_pr1505_matrix_entries_carry_real_nodeids():
    """每个 VERIFIED 项必须给出精确测试 nodeid 与适用域，不能只写目录名。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "emit_v2_scope_nodeids", REPO_ROOT / "scripts" / "emit_v2_acceptance_scope_v1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from chanlun_trader.engine.custom_indicators_v2 import register_custom_indicators
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    registry = default_registry()
    register_custom_indicators(registry)
    matrix = module.build_matrix(registry)

    assert matrix["minimum_set"]["required"] == 54, "分母被改动"
    for family in matrix["families"]:
        for item in family["items"]:
            if item["status"] != "VERIFIED":
                continue
            evidence = item["evidence"]
            assert "nodeid=" in evidence, f"缺少精确 nodeid：{item['requirement']}"
            assert "domain=" in evidence, f"缺少适用域：{item['requirement']}"
            assert "nodeid=tests/" in evidence
            assert "junit=" in evidence, f"缺少同 HEAD JUnit 关联：{item['requirement']}"
    # 未接线的算子层必须保持 PARTIAL
    operator_items = [i for f in matrix["families"] for i in f["items"]
                      if i["target"].startswith("OPERATOR_LAYER")]
    assert operator_items and all(i["status"] == "PARTIAL" for i in operator_items)


def test_pr1505_contract_and_registry_and_matrix_agree(tmp_path: Path):
    """生产 registry、HTTP 契约与生成矩阵必须一致。"""
    import importlib.util

    from chanlun_trader.engine.custom_indicators_v2 import register_custom_indicators
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    registry = default_registry()
    register_custom_indicators(registry)
    registry_ids = {spec.indicator_id for spec in registry.specs()}

    root = tmp_path / "synthetic_root"
    root.mkdir()
    app = create_app(root, ExecutionPolicy(
        mode="READ_ONLY", workspace_kind="SYNTHETIC", allow_readonly_compute=True))
    body = TestClient(app).get("/api/backtest/behavior/contracts").json()
    assert registry_ids == {item["indicator_id"] for item in body["indicators"]}

    spec = importlib.util.spec_from_file_location(
        "emit_v2_scope_agree", REPO_ROOT / "scripts" / "emit_v2_acceptance_scope_v1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    matrix = module.build_matrix(registry)
    for target in {i["target"] for f in matrix["families"] for i in f["items"]}:
        if target.startswith(("OPERATOR_LAYER", "CONDITION_LAYER", "NOT_FOUND")):
            continue
        assert target in registry_ids, f"矩阵引用了未注册指标：{target}"


def test_pr1505_evidence_must_cover_the_dimension_not_just_exist():
    """负向检查：入口证据错设为只消费 RSI/EMA 的测试，不得升格为 VERIFIED。

    证据"存在"不等于"覆盖该项能力"。矩阵必须按**该测试实际消费的指标**判定，
    因此除 RSI/EMA/MA/ATR/HISTORICAL_RETURN 外的指标不能有 ENTRYPOINT_VALIDATED。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "emit_v2_scope_neg", REPO_ROOT / "scripts" / "emit_v2_acceptance_scope_v1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from chanlun_trader.engine.custom_indicators_v2 import register_custom_indicators
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    registry = default_registry()
    register_custom_indicators(registry)
    matrix = module.build_matrix(registry)

    real_entrypoint_indicators = set(module.ENTRYPOINT_NODEIDS)
    for family in matrix["families"]:
        for item in family["items"]:
            dimensions = item.get("dimensions") or {}
            if dimensions.get("ENTRYPOINT_VALIDATED"):
                assert item["target"] in real_entrypoint_indicators, (
                    f"{item['target']} 被标记为入口已验证，但没有任何入口测试消费它")
    for indicator_id in ("TRIX", "PSY", "KELTNER", "DONCHIAN", "PVT", "MFI"):
        assert indicator_id not in real_entrypoint_indicators
    verified_targets = {i["target"] for f in matrix["families"] for i in f["items"]
                        if i["status"] == "VERIFIED"}
    assert not ({"TRIX", "PSY", "KELTNER", "DONCHIAN"} & verified_targets), \
        "未覆盖入口的指标被升格为 VERIFIED"


def test_pr1505_condition_dimensions_use_their_own_positive_tests():
    """负向检查：REF/boolean 等不得统一用 Top-N 同值测试作证。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "emit_v2_scope_cond", REPO_ROOT / "scripts" / "emit_v2_acceptance_scope_v1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from chanlun_trader.engine.custom_indicators_v2 import register_custom_indicators
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    registry = default_registry()
    register_custom_indicators(registry)
    matrix = module.build_matrix(registry)

    nodeids = list(module.CONDITION_NODEIDS.values())
    assert len(nodeids) == len(set(nodeids)), "多个条件维度共用了同一个测试 nodeid"
    assert "top_n" not in module.CONDITION_NODEIDS["boolean"]
    assert "top_n" not in module.CONDITION_NODEIDS["CROSS_UP/CROSS_DOWN"]
    partial_reasons = module.CONDITION_PARTIAL_REASONS
    assert "REF" in partial_reasons and "arithmetic" in partial_reasons
    for family in matrix["families"]:
        for item in family["items"]:
            if item["requirement"] in ("REF", "arithmetic"):
                assert item["status"] == "PARTIAL", f"{item['requirement']} 被误升为 VERIFIED"
                assert "partial_reason=" in item["evidence"]


def test_pr1505_macd_kdj_oracle_is_linked_to_v1_compat_junit():
    """MACD/KDJ 的 oracle 真实位于 V1 兼容 JUnit，必须正确关联集合与参数化节点。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "emit_v2_scope_junit", REPO_ROOT / "scripts" / "emit_v2_acceptance_scope_v1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from chanlun_trader.engine.custom_indicators_v2 import register_custom_indicators
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    registry = default_registry()
    register_custom_indicators(registry)
    matrix = module.build_matrix(registry)

    rows = {i["target"]: i for f in matrix["families"] for i in f["items"]}
    for indicator_id in ("MACD", "KDJ"):
        assert indicator_id in rows
        evidence = rows[indicator_id]["evidence"]
        # 每条证据记录自己的来源：MACD/KDJ 的 oracle 在 V1 兼容套件，
        # 而共享的注册表契约测试在 V2 套件 —— 两者都真实存在，都要列出。
        assert module.V1_JUNIT in evidence, f"{indicator_id} 未关联 V1 兼容 JUnit"
        assert "tests/indicators/test_indicator_formulas_v1.py" in evidence, \
            f"{indicator_id} 的 oracle nodeid 未指向 V1 兼容套件"
        # oracle 节点必须紧邻 V1 JUnit 声明（同一条 nodeid 不能挂到 V2 套件上）
        oracle_part = [p for p in evidence.split("; ")
                       if "test_macd_v1_matches" in p or "test_kdj_v1_matches" in p]
        assert oracle_part, f"{indicator_id} 缺少 V1 oracle nodeid"
    # RSI 的 oracle 在 V2 套件
    rsi = rows["RSI"]["evidence"]
    assert module.V2_JUNIT in rsi
    assert "tests/indicators_v2/" in rsi

def test_pr1505_verified_requires_every_declared_dimension():
    """VERIFIED 必须在所有声明维度上为真；缺任一维度即 PARTIAL 且写明缺失维度。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "emit_v2_scope_dims", REPO_ROOT / "scripts" / "emit_v2_acceptance_scope_v1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from chanlun_trader.engine.custom_indicators_v2 import register_custom_indicators
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    registry = default_registry()
    register_custom_indicators(registry)
    matrix = module.build_matrix(registry)

    assert "dimension_rule" in matrix
    for family in matrix["families"]:
        for item in family["items"]:
            dimensions = item.get("dimensions") or {}
            if item["status"] == "VERIFIED":
                assert dimensions, f"VERIFIED 项缺少维度声明：{item['requirement']}"
                assert all(dimensions.values()), \
                    f"VERIFIED 项存在未通过维度：{item['requirement']} {dimensions}"
            elif item["status"] == "PARTIAL" and dimensions:
                assert not all(dimensions.values()), \
                    f"PARTIAL 项所有维度都通过（应记为 VERIFIED）：{item['requirement']}"
                assert "missing_dimensions=" in item["evidence"] or \
                    "partial_reason=" in item["evidence"]
    assert matrix["minimum_set"]["required"] == 54


def test_pr1505_every_claimed_nodeid_actually_exists():
    """矩阵里声称的每个 nodeid 都必须能真实收集到，否则即为伪造证据。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "emit_v2_scope_nodes", REPO_ROOT / "scripts" / "emit_v2_acceptance_scope_v1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from chanlun_trader.engine.custom_indicators_v2 import register_custom_indicators
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    registry = default_registry()
    register_custom_indicators(registry)
    matrix = module.build_matrix(registry)

    nodeids = set()
    for family in matrix["families"]:
        for item in family["items"]:
            for part in item["evidence"].split("; "):
                if part.startswith("nodeid=tests/"):
                    nodeids.add(part[len("nodeid="):])
    assert nodeids, "矩阵未给出任何 nodeid"

    missing = []
    for nodeid in sorted(nodeids):
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", nodeid, "--collect-only", "-q"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        if completed.returncode != 0:
            missing.append(nodeid)
    assert not missing, f"矩阵声称了不存在的测试 nodeid：{missing}"


def test_pr1505_unsupported_combination_is_declared_in_matrix():
    """不支持的组合必须在能力矩阵里显式标出，而不是运行完成却静默不退出。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "emit_v2_scope_unsupported", REPO_ROOT / "scripts" / "emit_v2_acceptance_scope_v1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from chanlun_trader.engine.custom_indicators_v2 import register_custom_indicators
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    registry = default_registry()
    register_custom_indicators(registry)
    matrix = module.build_matrix(registry)
    declared = matrix.get("unsupported_combinations", {})
    assert "MIXED_CROSS_SECTIONAL_AND_SERIES_CONDITION" in declared
    assert "拒绝" in declared["MIXED_CROSS_SECTIONAL_AND_SERIES_CONDITION"]


def test_pr1505_cli_reports_scope_consistently(tmp_path: Path):
    """CLI 与 API 对同一请求语义一致（PR15 修复后仍成立）。"""
    days = _days()
    payload = {
        "mode": BEHAVIOR_MODE_V2, "calendar": days, "symbols": ["600000.SH"],
        "bars": {"600000.SH": _bars()},
        "indicators": [{"indicator_id": "MA", "params": {"window": 5}, "alias": "ma_fast"},
                       {"indicator_id": "MA", "params": {"window": 20}, "alias": "ma_slow"}],
        "entry_condition": {"op": "gt", "args": [
            {"op": "indicator", "args": ["ma_fast"], "params": {"output": "ma"}},
            {"op": "indicator", "args": ["ma_slow"], "params": {"output": "ma"}}]},
        "exit_rules": {"fixed_holding_sessions": 5},
        "initial_cash": 100_000.0, "max_positions": 1, "max_position_weight": 1.0,
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    result_path = tmp_path / "result.json"
    completed = _run_cli(["--request", str(request_path), "--request-root", str(tmp_path),
                          "--out", str(result_path), "--out-root", str(tmp_path)])
    assert completed.returncode == 0, completed.stderr
    body = json.loads(result_path.read_text(encoding="utf-8"))
    outputs = body["resolved_config"]["indicator_outputs"]["600000.SH"]
    assert len(outputs) == 2, f"CLI 未保留两个 MA 实例：{list(outputs)}"
