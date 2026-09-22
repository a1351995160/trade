"""BT_CONDITION_LAYER_V2 — 受限条件表达式层（三值逻辑 + 组合算子）。

为什么需要它
------------
指标不是买卖点。条件不应写死在策略名里，也不应要求为每个新指标修改引擎。

本层提供**受限、可验证**的结构化表达式树，把指标输出组合成条件：

- 比较：``GT/GE/LT/LE/EQ/NE``（指标 vs 常数 / 指标 vs 指标）
- 逻辑：``AND/OR/NOT``，遵循**三值逻辑**
- 交叉：``CROSS_UP/CROSS_DOWN``（要求前后两个合格且按合同相邻的观察点）
- 位置：``ABOVE/BELOW``（线上/线下）
- 区间：``BETWEEN``
- 滚动逻辑：``EVERY``（连续 N 根满足）、``EXIST``（N 根内满足）、``BARSLAST``（距上次满足的根数）
- 排序：``RANK``/``PERCENTILE``/``TOP_N``（在同一 bar 的截面内，需显式提供截面视图）

三值逻辑（本层核心约定）
------------------------
内部真值域为 ``{TRUE, FALSE, UNKNOWN}``：

- ``AND``：任一 ``FALSE`` -> ``FALSE``；否则任一 ``UNKNOWN`` -> ``UNKNOWN``；否则 ``TRUE``。
- ``OR``：任一 ``TRUE`` -> ``TRUE``；否则任一 ``UNKNOWN`` -> ``UNKNOWN``；否则 ``FALSE``。
- ``NOT``：``TRUE -> FALSE``、``FALSE -> TRUE``、``UNKNOWN -> UNKNOWN``。

**``NOT(UNKNOWN)`` 仍为 ``UNKNOWN``，绝不变成可买入资格。**
只有 ``TRUE`` 才可进入信号；``FALSE`` 与 ``UNKNOWN`` 都必须被拒绝，且拒绝原因可区分。

安全性
------
- 不使用 ``eval`` / ``exec`` / 动态 import / 文件或网络访问；
- 表达式深度与节点数有上限；循环依赖在注册层检测；
- 禁止未来引用：``REF``/``shift`` 的周期必须 >= 0；禁止居中窗口；
- 未完成 bar 不参与（由指标层的 ready/segment 语义保证）。
"""
from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

CONDITION_LAYER_VERSION = "BT_CONDITION_LAYER_V2"

MAX_EXPRESSION_DEPTH = 16
MAX_EXPRESSION_NODES = 256

# 三值逻辑真值域（用 float 承载以便与指标序列对齐：1.0/0.0/NaN）
TRUE = 1.0
FALSE = 0.0
UNKNOWN = float("nan")


class ConditionError(ValueError):
    """表达式非法、未注册算子、深度/节点超限、未来引用。必须显式失败。"""


# --------------------------------------------------------------------------
# 表达式节点
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Expr:
    """受限表达式节点。``op`` 必须在白名单内。"""

    op: str
    args: Tuple[Any, ...] = ()
    params: Mapping[str, Any] = _dc_field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        def render(value: Any) -> Any:
            if isinstance(value, Expr):
                return value.to_dict()
            if isinstance(value, (list, tuple)):
                return [render(item) for item in value]
            return value

        return {"op": self.op, "args": [render(a) for a in self.args],
                "params": {k: render(v) for k, v in self.params.items()}}


def lit(value: float) -> Expr:
    """常数节点。"""
    return Expr("const", (), {"value": float(value)})


def ind(indicator_id: str, output: str, version: Optional[str] = None) -> Expr:
    """指标输出引用。版本缺省由注册表解析最新。"""
    params = {"output": output}
    if version:
        params["version"] = version
    return Expr("indicator", (indicator_id,), params)


def field(name: str) -> Expr:
    """原始字段引用（close/high/low/open/volume/amount/prev_close）。"""
    return Expr("field", (name,))


def op(name: str, *args: Any, **params: Any) -> Expr:
    return Expr(name, tuple(args), params)


def node_count(node: Expr) -> int:
    total = 1
    for arg in node.args:
        if isinstance(arg, Expr):
            total += node_count(arg)
        elif isinstance(arg, (list, tuple)):
            total += sum(node_count(item) for item in arg if isinstance(item, Expr))
    return total


def depth(node: Expr, current: int = 1) -> int:
    best = current
    for arg in node.args:
        if isinstance(arg, Expr):
            best = max(best, depth(arg, current + 1))
        elif isinstance(arg, (list, tuple)):
            for item in arg:
                if isinstance(item, Expr):
                    best = max(best, depth(item, current + 1))
    return best


def validate_expression(node: Expr) -> None:
    """静态校验：白名单、深度、节点数、未来引用。"""
    if not isinstance(node, Expr):
        raise ConditionError(f"INVALID_EXPRESSION_NODE:{node!r}")
    if node.op not in _ALL_OPS:
        raise ConditionError(f"UNKNOWN_OPERATOR:{node.op}")
    if depth(node) > MAX_EXPRESSION_DEPTH:
        raise ConditionError(f"EXPRESSION_TOO_DEEP:{depth(node)}>{MAX_EXPRESSION_DEPTH}")
    if node_count(node) > MAX_EXPRESSION_NODES:
        raise ConditionError(f"EXPRESSION_TOO_LARGE:{node_count(node)}>{MAX_EXPRESSION_NODES}")
    if node.op in {"shift", "ref", "delta", "pct_change", "barslast"}:
        periods = node.params.get("periods", node.params.get("window"))
        if periods is not None and float(periods) < 0:
            raise ConditionError("NEGATIVE_LAG_FORBIDDEN")
    if node.params.get("center") is True:
        raise ConditionError("CENTERED_WINDOW_FORBIDDEN")
    for arg in node.args:
        if isinstance(arg, Expr):
            validate_expression(arg)
        elif isinstance(arg, (list, tuple)):
            for item in arg:
                if isinstance(item, Expr):
                    validate_expression(item)


_ALL_OPS = {
    "const", "indicator", "field",
    # 比较
    "gt", "ge", "lt", "le", "eq", "ne",
    # 逻辑（三值）
    "and", "or", "not",
    # 交叉与位置
    "cross_up", "cross_down", "above", "below", "between",
    # 滚动逻辑
    "every", "exist", "barslast",
    # 时序
    "shift", "ref", "delta", "pct_change",
    # 截面
    "rank", "percentile", "top_n",
    # 算术（供条件内部构造，如差值比较）
    "add", "sub", "mul", "div",
}

_BINARY_COMPARISONS = {"gt", "ge", "lt", "le", "eq", "ne"}
_LOGICAL = {"and", "or"}
_ARITHMETIC = {"add", "sub", "mul", "div"}
# 产生布尔（三值）真值的算子
_BOOLEAN_OPS = _BINARY_COMPARISONS | _LOGICAL | {"not", "cross_up", "cross_down",
                                                  "above", "below", "between",
                                                  "every", "exist", "top_n"}


# --------------------------------------------------------------------------
# 求值上下文
# --------------------------------------------------------------------------


@dataclass
class ConditionContext:
    """求值上下文。

    - ``indicator_values``：``{输出键: Series}``，输出键为 ``f"{indicator_id}.{output}"``；
    - ``fields``：原始字段 Series；
    - ``cross_section``：可选的截面视图（同 bar 的证券集合），用于 rank/percentile/top_n；
    - ``ready``：``{输出键: Series(bool)}``，指标未 ready 的位置为 UNKNOWN。
    """

    indicator_values: Mapping[str, pd.Series]
    fields: Mapping[str, pd.Series]
    index: pd.Index
    cross_section: Optional[pd.DataFrame] = None      # index=symbol, columns=输出键
    ready: Mapping[str, pd.Series] = _dc_field(default_factory=dict)
    unresolved: List[str] = _dc_field(default_factory=list)


def _as_series(value: Any, index: pd.Index) -> pd.Series:
    if isinstance(value, pd.Series):
        return value.reindex(index)
    return pd.Series(float(value), index=index, dtype=float)


def _boolean(series: pd.Series) -> pd.Series:
    """把数值序列映射到三值域：>0 -> TRUE，==0 -> FALSE，NaN -> UNKNOWN。"""
    values = pd.to_numeric(series, errors="coerce")
    out = pd.Series(np.where(values.isna(), np.nan,
                             np.where(values > 0, TRUE, FALSE)), index=series.index, dtype=float)
    return out


def _tri_not(series: pd.Series) -> pd.Series:
    """三值 NOT：UNKNOWN 保持 UNKNOWN。"""
    values = series.to_numpy(dtype=float)
    out = np.where(np.isnan(values), np.nan, np.where(values > 0, FALSE, TRUE))
    return pd.Series(out, index=series.index, dtype=float)


def _tri_and(left: pd.Series, right: pd.Series) -> pd.Series:
    """三值 AND：任一 FALSE -> FALSE；否则任一 UNKNOWN -> UNKNOWN。"""
    a = left.to_numpy(dtype=float)
    b = right.to_numpy(dtype=float)
    a_false = np.nan_to_num(a, nan=1.0) <= 0
    b_false = np.nan_to_num(b, nan=1.0) <= 0
    both_true = (a > 0) & (b > 0)
    out = np.where(a_false | b_false, FALSE, np.where(both_true, TRUE, np.nan))
    return pd.Series(out, index=left.index, dtype=float)


def _tri_or(left: pd.Series, right: pd.Series) -> pd.Series:
    """三值 OR：任一 TRUE -> TRUE；否则任一 UNKNOWN -> UNKNOWN。"""
    a = left.to_numpy(dtype=float)
    b = right.to_numpy(dtype=float)
    either_true = (a > 0) | (b > 0)
    a_false = np.nan_to_num(a, nan=1.0) <= 0
    b_false = np.nan_to_num(b, nan=1.0) <= 0
    out = np.where(either_true, TRUE, np.where(a_false & b_false, FALSE, np.nan))
    return pd.Series(out, index=left.index, dtype=float)


def _compare(left: pd.Series, right: pd.Series, operation: str) -> pd.Series:
    """比较：任一侧 UNKNOWN（NaN）-> UNKNOWN。"""
    a = left.to_numpy(dtype=float)
    b = right.to_numpy(dtype=float)
    known = np.isfinite(a) & np.isfinite(b)
    result = {
        "gt": a > b, "ge": a >= b, "lt": a < b, "le": a <= b,
        "eq": a == b, "ne": a != b,
    }[operation]
    out = np.where(known, np.where(result, TRUE, FALSE), np.nan)
    return pd.Series(out, index=left.index, dtype=float)


def _cross(left: pd.Series, right: pd.Series, upward: bool) -> pd.Series:
    """交叉：要求前后两个**合格且相邻**的观察点。

    不把"持续在线上"每天都算交叉；不跨缺口捏造交叉。
    """
    a = left.to_numpy(dtype=float)
    b = right.to_numpy(dtype=float)
    n = len(a)
    out = np.full(n, np.nan)
    for i in range(1, n):
        if not (np.isfinite(a[i]) and np.isfinite(b[i]) and np.isfinite(a[i - 1]) and np.isfinite(b[i - 1])):
            continue
        if upward:
            out[i] = TRUE if (a[i - 1] <= b[i - 1] and a[i] > b[i]) else FALSE
        else:
            out[i] = TRUE if (a[i - 1] >= b[i - 1] and a[i] < b[i]) else FALSE
    return pd.Series(out, index=left.index, dtype=float)


def _rolling_boolean(series: pd.Series, window: int, mode: str) -> pd.Series:
    """滚动逻辑。UNKNOWN 参与计数时必须保持 UNKNOWN 语义：

    - ``EVERY``：窗口内**全部** TRUE -> TRUE；存在 FALSE -> FALSE；
      无 FALSE 但存在 UNKNOWN -> UNKNOWN。
    - ``EXIST``：窗口内存在 TRUE -> TRUE；全部 FALSE -> FALSE；
      无 TRUE 但存在 UNKNOWN -> UNKNOWN。
    """
    if window <= 0:
        raise ConditionError("INVALID_WINDOW")
    values = series.to_numpy(dtype=float)
    n = len(values)
    out = np.full(n, np.nan)
    for i in range(n):
        start = i - window + 1
        if start < 0:
            continue
        chunk = values[start : i + 1]
        true_count = int(np.count_nonzero(chunk > 0))
        false_count = int(np.count_nonzero(chunk == 0))
        unknown_count = int(np.count_nonzero(np.isnan(chunk)))
        if mode == "every":
            if false_count:
                out[i] = FALSE
            elif unknown_count:
                out[i] = np.nan
            else:
                out[i] = TRUE
        else:
            if true_count:
                out[i] = TRUE
            elif unknown_count:
                out[i] = np.nan
            else:
                out[i] = FALSE
    return pd.Series(out, index=series.index, dtype=float)


def _barslast(series: pd.Series) -> pd.Series:
    """距上次 TRUE 的根数。从未出现 -> NaN；当前为 TRUE -> 0。"""
    values = series.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    last_true = None
    for i, value in enumerate(values):
        if value > 0:
            last_true = i
        if last_true is not None:
            out[i] = float(i - last_true)
    return pd.Series(out, index=series.index, dtype=float)


# --------------------------------------------------------------------------
# 求值器
# --------------------------------------------------------------------------


class ConditionEvaluator:
    """受限表达式求值器。不使用 eval/exec。"""

    def __init__(self, registry=None):
        self.registry = registry

    def evaluate(self, node: Expr, context: ConditionContext) -> pd.Series:
        validate_expression(node)
        return self._evaluate_node(node, context)

    def _evaluate_node(self, node: Expr, context: ConditionContext) -> pd.Series:
        index = context.index
        operation = node.op

        if operation == "const":
            return _as_series(node.params.get("value"), index)
        if operation == "field":
            name = str(node.args[0])
            if name not in context.fields:
                raise ConditionError(f"MISSING_FIELD:{name}")
            return context.fields[name].reindex(index)
        if operation == "indicator":
            indicator_id = str(node.args[0])
            output = str(node.params.get("output", ""))
            key = f"{indicator_id}.{output}"
            if key not in context.indicator_values:
                context.unresolved.append(key)
                raise ConditionError(f"MISSING_INDICATOR_OUTPUT:{key}")
            series = context.indicator_values[key].reindex(index)
            ready = context.ready.get(key)
            if ready is not None:
                # 未 ready 的位置在**布尔上下文**里必须为 UNKNOWN，
                # 不能因为 NaN 比较或布尔强转变成买入资格。
                series = series.where(ready.reindex(index).fillna(False))
            return series

        if operation in _LOGICAL:
            args = [self._evaluate_node(arg, context) for arg in node.args]
            if len(args) < 2:
                raise ConditionError(f"OPERATOR_REQUIRES_AT_LEAST_TWO_ARGS:{operation}")
            result = args[0]
            for other in args[1:]:
                result = _tri_and(result, other) if operation == "and" else _tri_or(result, other)
            return result

        if operation == "not":
            return _tri_not(self._evaluate_node(node.args[0], context))

        if operation in _BINARY_COMPARISONS:
            left = self._evaluate_node(node.args[0], context)
            right = self._evaluate_node(node.args[1], context)
            return _compare(left, right, operation)

        if operation in _ARITHMETIC:
            left = self._evaluate_node(node.args[0], context)
            right = self._evaluate_node(node.args[1], context)
            a = left.to_numpy(dtype=float)
            b = right.to_numpy(dtype=float)
            known = np.isfinite(a) & np.isfinite(b)
            with np.errstate(divide="ignore", invalid="ignore"):
                computed = {
                    "add": a + b, "sub": a - b, "mul": a * b,
                    "div": np.where(b != 0, a / np.where(b == 0, 1.0, b), np.nan),
                }[operation]
            return pd.Series(np.where(known, computed, np.nan), index=index, dtype=float)

        if operation in {"cross_up", "cross_down"}:
            left = self._evaluate_node(node.args[0], context)
            right = self._evaluate_node(node.args[1], context)
            return _cross(left, right, upward=(operation == "cross_up"))

        if operation in {"above", "below"}:
            left = self._evaluate_node(node.args[0], context)
            right = self._evaluate_node(node.args[1], context)
            return _compare(left, right, "gt" if operation == "above" else "lt")

        if operation == "between":
            value = self._evaluate_node(node.args[0], context)
            low = self._evaluate_node(node.args[1], context)
            high = self._evaluate_node(node.args[2], context)
            return _tri_and(_compare(value, low, "ge"), _compare(value, high, "le"))

        if operation in {"every", "exist"}:
            window = int(node.params.get("window", 1))
            return _rolling_boolean(self._evaluate_node(node.args[0], context), window, operation)

        if operation == "barslast":
            return _barslast(self._evaluate_node(node.args[0], context))

        if operation in {"shift", "ref", "delta", "pct_change"}:
            periods = int(node.params.get("periods", node.params.get("window", 1)))
            if periods < 0:
                raise ConditionError("NEGATIVE_LAG_FORBIDDEN")
            series = self._evaluate_node(node.args[0], context)
            lagged = series.shift(periods)
            if operation in {"shift", "ref"}:
                return lagged
            if operation == "delta":
                return series - lagged
            with np.errstate(divide="ignore", invalid="ignore"):
                return pd.Series(
                    np.where(lagged.to_numpy(dtype=float) != 0,
                             series.to_numpy(dtype=float) / lagged.to_numpy(dtype=float) - 1.0, np.nan),
                    index=index, dtype=float)

        if operation in {"rank", "percentile", "top_n"}:
            return self._cross_sectional(node, context)

        raise ConditionError(f"UNSUPPORTED_OPERATOR:{operation}")

    def _cross_sectional(self, node: Expr, context: ConditionContext) -> pd.Series:
        """截面算子：只作用于**同一 bar** 的证券集合，绝不做全样本排名。"""
        if context.cross_section is None:
            raise ConditionError("CROSS_SECTION_REQUIRED")
        key = node.args[0]
        if isinstance(key, Expr):
            if key.op != "indicator":
                raise ConditionError("CROSS_SECTION_REQUIRES_INDICATOR_OUTPUT")
            column = f"{key.args[0]}.{key.params.get('output', '')}"
        else:
            column = str(key)
        frame = context.cross_section
        if column not in frame.columns:
            raise ConditionError(f"MISSING_CROSS_SECTION_COLUMN:{column}")
        values = pd.to_numeric(frame[column], errors="coerce")
        if node.op == "rank":
            return values.rank(method="average", pct=False)
        if node.op == "percentile":
            return values.rank(method="average", pct=True)
        top_n = int(node.params.get("n", 1))
        if top_n <= 0:
            raise ConditionError("INVALID_TOP_N")
        # 同值顺序固定：按 (值降序, 证券代码升序) 稳定排序。
        # 用位置数组而非名为 "symbol" 的列，避免与同名索引层级冲突。
        order = pd.DataFrame({
            "value": values.to_numpy(dtype=float),
            "label": [str(item) for item in values.index],
        })
        order = order.sort_values(["value", "label"], ascending=[False, True], na_position="last")
        selected_positions = set(order.head(top_n).index)
        selected = values.index.isin(values.index[sorted(selected_positions)])
        return pd.Series(np.where(selected, TRUE, FALSE), index=values.index, dtype=float)


def evaluate_condition(node: Expr, context: ConditionContext, *, registry=None) -> pd.Series:
    """便捷入口。返回三值序列（1.0/0.0/NaN）。"""
    return ConditionEvaluator(registry=registry).evaluate(node, context)


def is_eligible(signal: pd.Series) -> pd.Series:
    """只有 TRUE 才算可执行资格；FALSE 与 UNKNOWN 都不是。"""
    values = signal.to_numpy(dtype=float)
    return pd.Series(values > 0, index=signal.index)


def rejection_reason(signal: pd.Series) -> pd.Series:
    """逐行给出 ``TRUE`` / ``CONDITION_FALSE`` / ``CONDITION_UNKNOWN``。

    使"不成交"的原因可区分，而不是笼统的"没有信号"。
    """
    values = signal.to_numpy(dtype=float)
    labels = np.where(
        values > 0, "TRUE",
        np.where(np.isnan(values), "CONDITION_UNKNOWN", "CONDITION_FALSE"),
    )
    return pd.Series(labels, index=signal.index, dtype=object)
