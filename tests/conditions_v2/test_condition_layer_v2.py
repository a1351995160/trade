"""V2 条件表达式层验收：三值逻辑、组合算子、安全边界、截面算子。

核心约定：``NOT(UNKNOWN)`` 仍为 ``UNKNOWN``，绝不变成买入资格。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from chanlun_trader.engine.conditions_v2 import (
    FALSE,
    MAX_EXPRESSION_DEPTH,
    MAX_EXPRESSION_NODES,
    TRUE,
    ConditionContext,
    ConditionError,
    ConditionEvaluator,
    Expr,
    _tri_and,
    _tri_not,
    _tri_or,
    evaluate_condition,
    field,
    ind,
    is_eligible,
    lit,
    op,
    rejection_reason,
    validate_expression,
)
from chanlun_trader.engine.indicator_registry_v2 import default_registry


def _days(count: int) -> list:
    start = pd.Timestamp("2025-01-01")
    return [int((start + pd.Timedelta(days=i)).strftime("%Y%m%d")) for i in range(count)]


def _context(n: int = 60, *, ready_from: int = 0):
    days = _days(n)
    a = pd.Series([10.0 + 0.1 * i for i in range(n)], index=days)
    b = pd.Series([10.0] * n, index=days)
    ready = pd.Series([i >= ready_from for i in range(n)], index=days)
    return ConditionContext(
        indicator_values={"A.x": a, "B.y": b},
        fields={"close": a},
        index=pd.Index(days, name="date"),
        ready={"A.x": ready, "B.y": ready},
    )


# --------------------------------------------------------------------------
# 1. 三值逻辑真值表（核心）
# --------------------------------------------------------------------------
def test_not_unknown_stays_unknown():
    """NOT(UNKNOWN) 必须仍为 UNKNOWN —— 这是本层最关键的安全约定。"""
    days = _days(1)
    assert np.isnan(_tri_not(pd.Series([np.nan], index=days)).iloc[0])
    assert _tri_not(pd.Series([TRUE], index=days)).iloc[0] == FALSE
    assert _tri_not(pd.Series([FALSE], index=days)).iloc[0] == TRUE


def test_and_truth_table():
    days = _days(1)

    def value(a, b):
        return _tri_and(pd.Series([a], index=days), pd.Series([b], index=days)).iloc[0]

    assert value(TRUE, TRUE) == TRUE
    assert value(TRUE, FALSE) == FALSE
    assert value(FALSE, TRUE) == FALSE
    assert value(FALSE, FALSE) == FALSE
    # 任一 FALSE -> FALSE（即使另一侧 UNKNOWN）
    assert value(FALSE, np.nan) == FALSE
    assert value(np.nan, FALSE) == FALSE
    # 无 FALSE 但存在 UNKNOWN -> UNKNOWN
    assert np.isnan(value(TRUE, np.nan))
    assert np.isnan(value(np.nan, np.nan))


def test_or_truth_table():
    days = _days(1)

    def value(a, b):
        return _tri_or(pd.Series([a], index=days), pd.Series([b], index=days)).iloc[0]

    assert value(TRUE, TRUE) == TRUE
    assert value(TRUE, FALSE) == TRUE
    assert value(FALSE, TRUE) == TRUE
    assert value(FALSE, FALSE) == FALSE
    # 任一 TRUE -> TRUE（即使另一侧 UNKNOWN）
    assert value(TRUE, np.nan) == TRUE
    assert value(np.nan, TRUE) == TRUE
    # 无 TRUE 但存在 UNKNOWN -> UNKNOWN
    assert np.isnan(value(FALSE, np.nan))
    assert np.isnan(value(np.nan, np.nan))


def test_unknown_never_becomes_eligible():
    """UNKNOWN 与 FALSE 都不是可执行资格，且原因可区分。"""
    days = _days(3)
    signal = pd.Series([TRUE, FALSE, np.nan], index=days)
    eligible = is_eligible(signal)
    assert list(eligible) == [True, False, False]
    reasons = rejection_reason(signal)
    assert list(reasons) == ["TRUE", "CONDITION_FALSE", "CONDITION_UNKNOWN"]


def test_nan_comparison_yields_unknown_not_true():
    """NaN 比较不得因 Python 真值或 NE 语义产生 TRUE。"""
    context = _context(30)
    values = context.indicator_values["A.x"].copy()
    values.iloc[10] = np.nan
    context.indicator_values["A.x"] = values
    for operation in ("gt", "ge", "lt", "le", "eq", "ne"):
        node = op(operation, ind("A", "x"), lit(5.0))
        result = evaluate_condition(node, context)
        assert np.isnan(result.iloc[10]), f"{operation} 在 NaN 上产生了非 UNKNOWN"


# --------------------------------------------------------------------------
# 2. 比较 / 交叉 / 位置 / 区间
# --------------------------------------------------------------------------
def test_comparison_and_logic_combination():
    context = _context(30)
    node = op("and", op("gt", ind("A", "x"), lit(11.0)), op("gt", field("close"), lit(11.0)))
    result = evaluate_condition(node, context)
    # A.x = 10 + 0.1*i；i=9 时为 10.9（不 > 11），i=10 时为 11.0（仍不 > 11），i=11 起 > 11
    assert result.iloc[9] == FALSE
    assert result.iloc[10] == FALSE
    assert result.iloc[11] == TRUE


def test_cross_requires_adjacent_valid_observations():
    days = _days(6)
    a = pd.Series([1.0, 2.0, 3.0, 2.0, 2.0, 4.0], index=days)
    b = pd.Series([2.0, 2.0, 2.0, 2.0, 2.0, 2.0], index=days)
    context = ConditionContext(indicator_values={"A.x": a, "B.y": b}, fields={},
                               index=pd.Index(days, name="date"))
    up = evaluate_condition(op("cross_up", ind("A", "x"), ind("B", "y")), context)
    # 第 0 根无前一根 -> UNKNOWN（不判定）
    assert np.isnan(up.iloc[0])
    # 第 1 根：1<=2 且 2>2 不成立 -> FALSE
    assert up.iloc[1] == FALSE
    # 第 2 根：2<=2 且 3>2 -> 上穿 TRUE
    assert up.iloc[2] == TRUE
    # 第 3 根：3<=2 不成立 -> FALSE（持续在线上不得每天都算金叉）
    assert up.iloc[3] == FALSE
    # 第 5 根：2<=2 且 4>2 -> 上穿 TRUE
    assert up.iloc[5] == TRUE


def test_cross_down_positive_case():
    """cross_down 的正向取值（与 cross_up 方向相反）。

    此前 CROSS_UP/CROSS_DOWN 维度只有 cross_up 的正向测试；
    下穿方向必须有独立正例，不能靠标题声称双向均验收。
    """
    days = _days(6)
    a = pd.Series([3.0, 2.0, 1.0, 1.0, 2.0, 0.5], index=days)
    b = pd.Series([2.0, 2.0, 2.0, 2.0, 2.0, 2.0], index=days)
    context = ConditionContext(indicator_values={"A.x": a, "B.y": b}, fields={},
                               index=pd.Index(days, name="date"))
    down = evaluate_condition(op("cross_down", ind("A", "x"), ind("B", "y")), context)
    # 第 0 根无前一根 -> UNKNOWN
    assert np.isnan(down.iloc[0])
    # 第 1 根：3>=2 且 2<2 不成立 -> FALSE
    assert down.iloc[1] == FALSE
    # 第 2 根：2>=2 且 1<2 -> 下穿 TRUE
    assert down.iloc[2] == TRUE
    # 第 3 根：2>=2 不成立（1<2）-> FALSE
    assert down.iloc[3] == FALSE
    # 第 5 根：2>=2 且 0.5<2 -> 下穿 TRUE
    assert down.iloc[5] == TRUE
    # 方向不得混淆：同输入下 cross_up 不应在第 2 根为 TRUE
    up = evaluate_condition(op("cross_up", ind("A", "x"), ind("B", "y")), context)
    assert up.iloc[2] == FALSE


def test_cross_does_not_bridge_gap():
    """缺口（NaN）处不得捏造交叉。"""
    days = _days(4)
    a = pd.Series([1.0, np.nan, 3.0, 4.0], index=days)
    b = pd.Series([2.0, 2.0, 2.0, 2.0], index=days)
    context = ConditionContext(indicator_values={"A.x": a, "B.y": b}, fields={},
                               index=pd.Index(days, name="date"))
    up = evaluate_condition(op("cross_up", ind("A", "x"), ind("B", "y")), context)
    assert np.isnan(up.iloc[2]), "跨缺口产生了交叉"


def test_above_below_and_between():
    context = _context(30)
    above = evaluate_condition(op("above", ind("A", "x"), lit(11.0)), context)
    below = evaluate_condition(op("below", ind("A", "x"), lit(11.0)), context)
    between = evaluate_condition(op("between", ind("A", "x"), lit(10.5), lit(11.5)), context)
    # A.x = 10 + 0.1*i -> i=11 时 11.1 > 11
    assert above.iloc[11] == TRUE and below.iloc[11] == FALSE
    assert above.iloc[5] == FALSE and below.iloc[5] == TRUE
    # i=5 -> 10.5 在 [10.5, 11.5] 内；i=20 -> 12.0 超出
    assert between.iloc[5] == TRUE and between.iloc[20] == FALSE
    # above 与 below 互斥
    assert not ((above == TRUE) & (below == TRUE)).any()


# --------------------------------------------------------------------------
# 3. 滚动逻辑：EVERY / EXIST / BARSLAST
# --------------------------------------------------------------------------
def test_every_and_exist_semantics():
    context = _context(30)
    condition = op("gt", ind("A", "x"), lit(10.5))
    every = evaluate_condition(op("every", condition, window=3), context)
    exist = evaluate_condition(op("exist", condition, window=3), context)
    # i=5 时 A=10.5，不 > 10.5 -> FALSE；i=6,7,8 都 > 10.5
    assert every.iloc[7] == FALSE
    assert every.iloc[8] == TRUE
    assert exist.iloc[5] == FALSE
    assert exist.iloc[6] == TRUE


def test_every_unknown_when_no_false_but_unknown_present():
    days = _days(3)
    signal = pd.Series([TRUE, np.nan, TRUE], index=days)
    context = ConditionContext(
        indicator_values={"S.x": signal}, fields={}, index=pd.Index(days, name="date"))
    every = evaluate_condition(op("every", ind("S", "x"), window=3), context)
    assert np.isnan(every.iloc[2]), "无 FALSE 但含 UNKNOWN 时 EVERY 应为 UNKNOWN"
    exist = evaluate_condition(op("exist", ind("S", "x"), window=3), context)
    assert exist.iloc[2] == TRUE


def test_barslast_counts_since_last_true():
    days = _days(6)
    signal = pd.Series([FALSE, TRUE, FALSE, FALSE, TRUE, FALSE], index=days)
    context = ConditionContext(
        indicator_values={"S.x": signal}, fields={}, index=pd.Index(days, name="date"))
    out = evaluate_condition(op("barslast", ind("S", "x")), context)
    assert np.isnan(out.iloc[0])
    assert out.iloc[1] == 0.0
    assert out.iloc[3] == 2.0
    assert out.iloc[4] == 0.0
    assert out.iloc[5] == 1.0


# --------------------------------------------------------------------------
# 4. 安全边界：白名单、深度、未来引用
# --------------------------------------------------------------------------
def test_unknown_operator_rejected():
    with pytest.raises(ConditionError):
        validate_expression(Expr("os_system", ()))


def test_negative_lag_rejected():
    with pytest.raises(ConditionError):
        validate_expression(op("shift", ind("A", "x"), periods=-1))
    with pytest.raises(ConditionError):
        validate_expression(op("ref", ind("A", "x"), periods=-5))


def test_centered_window_rejected():
    with pytest.raises(ConditionError):
        validate_expression(Expr("ts_mean", (ind("A", "x"),), {"window": 5, "center": True}))


def test_depth_limit_enforced():
    node = ind("A", "x")
    for _ in range(MAX_EXPRESSION_DEPTH + 2):
        node = op("not", node)
    with pytest.raises(ConditionError):
        validate_expression(node)


def test_node_limit_enforced():
    args = tuple(ind("A", "x") for _ in range(MAX_EXPRESSION_NODES))
    node = Expr("and", args, {})
    with pytest.raises(ConditionError):
        validate_expression(node)


def test_expression_layer_does_not_use_eval_or_exec():
    """静态检查：条件层源码不得出现 eval/exec/动态导入等任意代码执行构造。"""
    import ast
    from pathlib import Path

    source = Path("src/chanlun_trader/engine/conditions_v2.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned_names = {"eval", "exec", "compile", "__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in banned_names, f"出现禁用调用：{node.func.id}"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", None) or ""
            names = [alias.name for alias in node.names]
            for name in [module, *names]:
                assert name != "importlib", "出现动态导入 importlib"
                assert name not in {"os", "subprocess", "socket", "requests"}, f"出现外部访问模块：{name}"


def test_missing_indicator_output_rejected():
    context = _context(10)
    with pytest.raises(ConditionError) as excinfo:
        evaluate_condition(op("gt", ind("NOT", "there"), lit(1.0)), context)
    assert "MISSING_INDICATOR_OUTPUT" in str(excinfo.value)


def test_missing_field_rejected():
    context = _context(10)
    with pytest.raises(ConditionError):
        evaluate_condition(op("gt", field("nonexistent"), lit(1.0)), context)


def test_unready_indicator_output_is_unknown():
    """未 ready 的输出在布尔上下文中必须为 UNKNOWN，不得变成 TRUE。"""
    context = _context(30, ready_from=10)
    result = evaluate_condition(op("gt", ind("A", "x"), lit(0.0)), context)
    assert result.iloc[:10].isna().all(), "未 ready 行变成了可判定值"
    assert result.iloc[10] == TRUE


# --------------------------------------------------------------------------
# 5. 截面算子：PIT + 同值顺序固定
# --------------------------------------------------------------------------
def test_cross_sectional_requires_view():
    context = _context(10)
    with pytest.raises(ConditionError):
        evaluate_condition(op("rank", ind("A", "x")), context)


def test_cross_sectional_top_n_stable_tie_break():
    days = _days(3)
    frame = pd.DataFrame(
        {"A.x": [5.0, 5.0, 5.0]},
        index=pd.Index(["600001.SH", "600002.SH", "600003.SH"], name="symbol"),
    )
    context = ConditionContext(
        indicator_values={"A.x": pd.Series([1.0] * 3, index=days)},
        fields={}, index=pd.Index(days, name="date"), cross_section=frame,
    )
    top1 = evaluate_condition(op("top_n", ind("A", "x"), n=1), context)
    # 同值时按证券代码升序稳定选择
    assert top1.loc["600001.SH"] == TRUE
    assert top1.loc["600002.SH"] == FALSE
    top2 = evaluate_condition(op("top_n", ind("A", "x"), n=2), context)
    assert top2.loc["600001.SH"] == TRUE and top2.loc["600002.SH"] == TRUE
    assert top2.loc["600003.SH"] == FALSE


def test_cross_sectional_percentile_ranks_within_bar_only():
    days = _days(2)
    frame = pd.DataFrame({"A.x": [1.0, 2.0, 3.0, 4.0]},
                         index=pd.Index(["a", "b", "c", "d"], name="symbol"))
    context = ConditionContext(
        indicator_values={"A.x": pd.Series([1.0, 2.0], index=days)},
        fields={}, index=pd.Index(days, name="date"), cross_section=frame,
    )
    pct = evaluate_condition(op("percentile", ind("A", "x")), context)
    # 同 bar 内 4 个证券的百分位：1/4, 2/4, 3/4, 4/4
    assert list(pct.values) == [0.25, 0.5, 0.75, 1.0]


# --------------------------------------------------------------------------
# 6. 与真实注册表联调
# --------------------------------------------------------------------------
def test_condition_consumes_registry_outputs_end_to_end():
    """条件直接消费注册表输出（不 mock 指标）。"""
    reg = default_registry()
    days = _days(80)
    close = pd.Series([10.0 + 0.1 * i for i in range(80)], index=days)
    high = pd.Series([c * 1.01 for c in close], index=days)
    low = pd.Series([c * 0.99 for c in close], index=days)
    rsi_result = reg.compute("RSI", close, high=high, low=low, params={"window": 14})
    ema_result = reg.compute("EMA", close, high=high, low=low, params={"window": 20})
    context = ConditionContext(
        indicator_values={
            "RSI.rsi": rsi_result.output("rsi"),
            "EMA.ema": ema_result.output("ema"),
        },
        fields={"close": close},
        index=pd.Index(days, name="date"),
        ready={"RSI.rsi": rsi_result.ready(), "EMA.ema": ema_result.ready()},
    )
    node = op("and", op("gt", ind("RSI", "rsi"), lit(30)),
              op("gt", field("close"), ind("EMA", "ema")))
    result = evaluate_condition(node, context)
    assert (result == TRUE).any()
    assert result.isna().any()          # 预热期为 UNKNOWN
    # 单调上涨时该组合恒真，故用反向条件构造 FALSE 以证明三值区分有效
    negated = evaluate_condition(op("not", node), context)
    assert (negated == FALSE).any()
    assert negated.isna().any()         # NOT(UNKNOWN) 仍为 UNKNOWN
    assert not (negated == TRUE).any()
