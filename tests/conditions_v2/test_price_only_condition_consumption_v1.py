"""A0：条件层对**本轮新增验收指标**输出的实际消费。

要求（任务第 5 节）：
- 条件必须有 TRUE/FALSE/UNKNOWN 正反例；不得以全部拒单制造通过；
- 入口测试实际消费该指标输出；不以 list-indicators 或 schema 枚举作证。

覆盖上一轮「有实现但缺入口消费」的指标：CCI / NATR / PSY / DEMA / TEMA /
TRIX / DONCHIAN / KELTNER / HISTORICAL_RETURN / ROLLING_VOLATILITY /
PRICE_EXTREMES / PRIOR_BREAKOUT / VOLUME_MA / RVOL_PRIOR / MFI 等。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chanlun_trader.engine.behavior_service_v2 import (  # noqa: E402
    BEHAVIOR_MODE_V2,
    run_behavior_backtest_v2,
)
from chanlun_trader.engine.conditions_v2 import (  # noqa: E402
    ConditionContext,
    ConditionEvaluator,
    evaluate_condition,
    ind,
    lit,
    op,
)

FALSE = 0.0
TRUE = 1.0


def _days(n: int) -> list:
    out = []
    i = 0
    while len(out) < n:
        d = pd.Timestamp("2024-01-02") + pd.Timedelta(days=i)
        if d.weekday() < 5:
            out.append(int(d.strftime("%Y%m%d")))
        i += 1
    return out


def _context(n: int = 60, kind: str = "osc") -> ConditionContext:
    days = _days(n)
    if kind == "osc":
        closes = [10.0 + 2.0 * np.sin(i / 5.0) for i in range(n)]
    elif kind == "up":
        closes = [10.0 + 0.1 * i for i in range(n)]
    else:
        closes = [20.0 - 0.1 * i for i in range(n)]
    return ConditionContext(
        indicator_values={
            "A.x": pd.Series(closes, index=days),
            "B.y": pd.Series([11.0] * n, index=days),
            "V.v": pd.Series([1_000_000.0] * n, index=days),
        },
        fields={"close": pd.Series(closes, index=days)},
        index=pd.Index(days, name="date"),
    )


def _bars(n: int, kind: str = "osc") -> list:
    days = _days(n)
    if kind == "osc":
        closes = [10.0 + 2.0 * np.sin(i / 5.0) for i in range(n)]
    elif kind == "up":
        closes = [10.0 + 0.08 * i for i in range(n)]
    else:
        closes = [20.0 - 0.08 * i for i in range(n)]
    return [{"date": days[i], "open": closes[i] * 0.999, "high": closes[i] * 1.02,
             "low": closes[i] * 0.98, "close": closes[i],
             "volume": 1_000_000.0 * (1.0 + 0.4 * np.sin(i / 4.0)),
             "amount": closes[i] * 1_000_000.0} for i in range(n)]


def _payload(indicator_id: str, params: dict, condition: dict, *, n: int = 80,
             kind: str = "osc", exit_rules: dict | None = None) -> dict:
    days = _days(n)
    return {
        "mode": BEHAVIOR_MODE_V2,
        "calendar": days,
        "symbols": ["600000.SH"],
        "bars": {"600000.SH": _bars(n, kind)},
        "indicators": [{"indicator_id": indicator_id, "params": params}],
        "entry_condition": condition,
        "exit_rules": exit_rules or {"fixed_holding_sessions": 5},
        "initial_cash": 100_000.0,
        "max_positions": 1,
        "max_position_weight": 1.0,
    }


# ==========================================================================
# 条件层三值语义（对新增指标输出）
# ==========================================================================
def test_condition_layer_consumes_cci_output_with_true_false_unknown():
    """CCI 输出经条件消费，必须同时出现 TRUE / FALSE / UNKNOWN。"""
    ctx = _context(60)
    ctx.indicator_values["CCI.cci"] = pd.Series(
        [np.nan] * 10 + [150.0] * 10 + [-150.0] * 10 + [0.0] * 30, index=ctx.index)
    result = evaluate_condition(op("gt", ind("CCI", "cci"), lit(100.0)), ctx)
    assert (result == TRUE).any(), "缺少 TRUE 正例"
    assert (result == FALSE).any(), "缺少 FALSE 反例"
    assert result.isna().any(), "缺少 UNKNOWN 反例（NaN 应保持 UNKNOWN）"


def test_condition_layer_consumes_natr_output():
    ctx = _context(40)
    ctx.indicator_values["NATR.natr"] = pd.Series(
        [np.nan] * 5 + [3.0] * 15 + [0.5] * 20, index=ctx.index)
    result = evaluate_condition(op("gt", ind("NATR", "natr"), lit(1.0)), ctx)
    assert (result == TRUE).any()
    assert (result == FALSE).any()
    assert result.isna().any()


def test_condition_layer_consumes_psy_output():
    ctx = _context(40)
    ctx.indicator_values["PSY.psy"] = pd.Series(
        [np.nan] * 5 + [80.0] * 15 + [20.0] * 20, index=ctx.index)
    result = evaluate_condition(op("gt", ind("PSY", "psy"), lit(50.0)), ctx)
    assert (result == TRUE).any()
    assert (result == FALSE).any()
    assert result.isna().any()


@pytest.mark.parametrize("indicator_id,output,threshold", [
    ("DEMA", "dema", 10.0),
    ("TEMA", "tema", 10.0),
    ("TRIX", "trix", 0.5),
    ("KELTNER", "upper", 10.0),
    ("DONCHIAN", "upper", 10.0),
    ("PRICE_EXTREMES", "hhv", 10.0),
    ("HISTORICAL_RETURN", "return", 0.5),
    ("ROLLING_VOLATILITY", "volatility", 0.01),
    ("VOLUME_MA", "volume_ma", 500_000.0),
    ("RVOL_PRIOR", "rvol", 1.0),
    ("MFI", "mfi", 50.0),
])
def test_condition_layer_consumes_each_new_indicator(indicator_id, output, threshold):
    """每个本轮验收指标都必须能被条件层真实消费（TRUE/FALSE/UNKNOWN 齐备）。

    夹具刻意构造三段：NaN（UNKNOWN）、高于阈值（TRUE）、低于阈值（FALSE）。
    """
    ctx = _context(40)
    key = f"{indicator_id}.{output}"
    # 阈值本身非零，保证 TRUE/FALSE 两侧都真实存在
    above = threshold + abs(threshold) * 1.0 + 1.0
    below = threshold - abs(threshold) * 1.0 - 1.0
    ctx.indicator_values[key] = pd.Series(
        [np.nan] * 5 + [above] * 15 + [below] * 20, index=ctx.index)
    result = evaluate_condition(op("gt", ind(indicator_id, output), lit(threshold)), ctx)
    assert (result == TRUE).any(), f"{indicator_id} 缺少 TRUE"
    assert (result == FALSE).any(), f"{indicator_id} 缺少 FALSE"
    assert result.isna().any(), f"{indicator_id} 缺少 UNKNOWN"


# ==========================================================================
# 端到端：指标经公开服务进入条件与账户
# ==========================================================================
@pytest.mark.parametrize("indicator_id,params,condition", [
    ("CCI", {"window": 14},
     {"op": "gt", "args": [{"op": "indicator", "args": ["CCI"], "params": {"output": "cci"}},
                           {"op": "const", "params": {"value": -500.0}}]}),
    ("NATR", {"window": 14},
     {"op": "gt", "args": [{"op": "indicator", "args": ["NATR"], "params": {"output": "natr"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
    ("PSY", {"window": 12},
     {"op": "gt", "args": [{"op": "indicator", "args": ["PSY"], "params": {"output": "psy"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
    ("DEMA", {"window": 20},
     {"op": "gt", "args": [{"op": "indicator", "args": ["DEMA"], "params": {"output": "dema"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
    ("TEMA", {"window": 20},
     {"op": "gt", "args": [{"op": "indicator", "args": ["TEMA"], "params": {"output": "tema"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
    ("TRIX", {"window": 12, "signal": 9},
     {"op": "gt", "args": [{"op": "indicator", "args": ["TRIX"], "params": {"output": "trix"}},
                           {"op": "const", "params": {"value": -100.0}}]}),
    ("DONCHIAN", {"window": 20},
     {"op": "gt", "args": [{"op": "indicator", "args": ["DONCHIAN"],
                            "params": {"output": "upper"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
    ("KELTNER", {"window": 20, "atr_window": 10},
     {"op": "gt", "args": [{"op": "indicator", "args": ["KELTNER"],
                            "params": {"output": "upper"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
    ("HISTORICAL_RETURN", {"window": 20},
     {"op": "gt", "args": [{"op": "indicator", "args": ["HISTORICAL_RETURN"],
                            "params": {"output": "return"}},
                           {"op": "const", "params": {"value": -1.0}}]}),
    ("ROLLING_VOLATILITY", {"window": 20},
     {"op": "gt", "args": [{"op": "indicator", "args": ["ROLLING_VOLATILITY"],
                            "params": {"output": "volatility"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
    ("PRICE_EXTREMES", {"window": 20},
     {"op": "gt", "args": [{"op": "indicator", "args": ["PRICE_EXTREMES"],
                            "params": {"output": "hhv"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
    ("PRIOR_BREAKOUT", {"window": 20},
     {"op": "gt", "args": [{"op": "indicator", "args": ["PRIOR_BREAKOUT"],
                            "params": {"output": "prior_high"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
    ("VOLUME_MA", {"window": 20},
     {"op": "gt", "args": [{"op": "indicator", "args": ["VOLUME_MA"],
                            "params": {"output": "volume_ma"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
    ("RVOL_PRIOR", {"window": 20},
     {"op": "gt", "args": [{"op": "indicator", "args": ["RVOL_PRIOR"],
                            "params": {"output": "rvol"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
    ("MFI", {"window": 14},
     {"op": "gt", "args": [{"op": "indicator", "args": ["MFI"], "params": {"output": "mfi"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
    ("ACCUMULATION_DISTRIBUTION", {},
     {"op": "gt", "args": [{"op": "indicator", "args": ["ACCUMULATION_DISTRIBUTION"],
                            "params": {"output": "ad_line"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
    ("CHAIKIN_MONEY_FLOW", {"window": 20},
     {"op": "gt", "args": [{"op": "indicator", "args": ["CHAIKIN_MONEY_FLOW"],
                            "params": {"output": "cmf"}},
                           {"op": "const", "params": {"value": -1.0}}]}),
    ("PVT", {},
     {"op": "gt", "args": [{"op": "indicator", "args": ["PVT"], "params": {"output": "pvt"}},
                           {"op": "const", "params": {"value": -1e18}}]}),
    ("VWAP_SESSION_PROXY", {},
     {"op": "gt", "args": [{"op": "indicator", "args": ["VWAP_SESSION_PROXY"],
                            "params": {"output": "vwap_session_proxy"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
    ("MACD_HIST_RAW", {"fast": 12, "slow": 26, "signal": 9},
     {"op": "gt", "args": [{"op": "indicator", "args": ["MACD_HIST_RAW"],
                            "params": {"output": "hist_raw"}},
                           {"op": "const", "params": {"value": -100.0}}]}),
    ("AMOUNT_MA", {"window": 20},
     {"op": "gt", "args": [{"op": "indicator", "args": ["AMOUNT_MA"],
                            "params": {"output": "amount_ma"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
    ("DRAWDOWN_FROM_PEAK", {"window": 20},
     {"op": "gt", "args": [{"op": "indicator", "args": ["DRAWDOWN_FROM_PEAK"],
                            "params": {"output": "drawdown"}},
                           {"op": "const", "params": {"value": -1.0}}]}),
    ("ROLLING_SLOPE", {"window": 20},
     {"op": "gt", "args": [{"op": "indicator", "args": ["ROLLING_SLOPE"],
                            "params": {"output": "slope"}},
                           {"op": "const", "params": {"value": -100.0}}]}),
    ("RVOL_INCL_CURRENT", {"window": 20},
     {"op": "gt", "args": [{"op": "indicator", "args": ["RVOL_INCL_CURRENT"],
                            "params": {"output": "rvol"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
    ("TRUE_RANGE", {},
     {"op": "gt", "args": [{"op": "indicator", "args": ["TRUE_RANGE"],
                            "params": {"output": "tr"}},
                           {"op": "const", "params": {"value": 0.0}}]}),
])
def test_indicator_reaches_account_chain_through_public_entry(
        indicator_id, params, condition):
    """指标必须经**公开服务**进入条件并产生真实成交（不是 schema 枚举作证）。"""
    payload = _payload(indicator_id, params, condition, n=80, kind="up")
    result = run_behavior_backtest_v2(payload)
    trace = result["resolved_config"]["condition_trace"]["600000.SH"]
    assert trace["condition_true"] > 0, f"{indicator_id} 无 TRUE"
    buys = [f for f in result["fills"] if f["side"] == "BUY"]
    assert buys, f"{indicator_id} 未产生任何成交"
    # 账户链真实走通
    assert result["official_valuation"]["n_days"] > 0
    assert result["final_equity"] != 100_000.0


def test_condition_true_false_unknown_counts_are_recorded():
    """条件 trace 必须分别记录 TRUE/FALSE/UNKNOWN，不得只记总数。"""
    payload = _payload(
        "PSY", {"window": 12},
        {"op": "gt", "args": [{"op": "indicator", "args": ["PSY"], "params": {"output": "psy"}},
                              {"op": "const", "params": {"value": 50.0}}]},
        n=80, kind="osc")
    trace = run_behavior_backtest_v2(payload)["resolved_config"]["condition_trace"]["600000.SH"]
    assert set(trace) >= {"condition_true", "condition_false", "condition_unknown"}
    assert trace["condition_unknown"] > 0, "预热期应产生 UNKNOWN"
    assert trace["condition_false"] > 0, "应存在 FALSE"
    assert trace["condition_true"] > 0, "应存在 TRUE"


def test_impossible_condition_produces_no_trade_not_false_pass():
    """反例：不可能满足的条件不得产生成交（防止"全部拒单制造通过"）。"""
    payload = _payload(
        "PSY", {"window": 12},
        {"op": "gt", "args": [{"op": "indicator", "args": ["PSY"], "params": {"output": "psy"}},
                              {"op": "const", "params": {"value": 1000.0}}]},
        n=60, kind="osc")
    result = run_behavior_backtest_v2(payload)
    assert not [f for f in result["fills"] if f["side"] == "BUY"], "不可能条件产生了成交"
    trace = result["resolved_config"]["condition_trace"]["600000.SH"]
    assert trace["condition_true"] == 0
    assert trace["condition_false"] > 0
