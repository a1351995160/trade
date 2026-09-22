"""依赖执行与指纹一致性的定点验收。

父公式**真实消费**依赖（经 ``registry.compute_dependency`` 并把结果写入自身输出），
不使用常数或手工伪造 trace。合成容器，不读真实行情。

对应外审 B1（固定版本必须真实约束计算）与 B2（共享依赖不得误报循环）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from chanlun_trader.engine.indicator_registry_v2 import (
    DependencyResolutionError,
    IndicatorFrameV2,
    IndicatorRegistry,
    _adapter,
    _spec,
    NEW_IN_V2,
)

DAYS = [20250102, 20250103, 20250104]
CLOSE = pd.Series([1.0, 2.0, 3.0], index=DAYS)


def _leaf_source(version: str, value: float):
    """构造**真实源码文本**不同的叶子实现。

    指纹取自源码，因此变异必须改变源码文本（改闭包常量不会改变源码，
    那只能证明指纹对闭包不敏感，不能证明它对实现敏感）。
    """
    namespace: dict = {}
    source = (
        "import numpy as np\n"
        "import pandas as pd\n"
        "from chanlun_trader.engine.indicators_v2 import IndicatorFrameV2\n"
        "def impl(data, *, window=3):\n"
        f"    value = {float(value)}  # leaf value for {version}\n"
        "    return IndicatorFrameV2(\n"
        f"        indicator_id='LEAF_{version}', version='{version}', index=data.index,\n"
        "        columns={'x': pd.Series(np.full(len(data.close), value), index=data.index)},\n"
        "        ready=pd.Series(True, index=data.index),\n"
        "        segment=pd.Series(np.zeros(len(data.close), dtype=int), index=data.index),\n"
        "        warmup_bars=1)\n")
    exec(compile(source, f"<leaf_{version}>", "exec"), namespace)  # noqa: S102 - 受控合成源码
    fn = namespace["impl"]
    fn.__wrapped_impl__ = fn
    fn.__source_override__ = source
    return fn


def _consuming_parent(registry: IndicatorRegistry, dependency: str,
                      indicator_id: str = "PARENT", extra: str | None = None):
    """父实现**真实消费**依赖：调用 compute_dependency 并把值写入自身输出。

    读取依赖的**首个声明输出名**（不同依赖输出名可能不同），
    输出自身统一为 ``y``。
    """
    def impl(data, **kwargs):
        close = pd.Series(data.close, index=data.index)
        first = registry.compute_dependency(dependency, close)
        values = first.output(first.output_names[0]).copy()
        if extra is not None:
            other = registry.compute_dependency(extra, close)
            values = values + other.output(other.output_names[0])
        return IndicatorFrameV2(
            indicator_id=indicator_id, version=f"{indicator_id}_V1", index=data.index,
            columns={"y": values}, ready=pd.Series(True, index=data.index),
            segment=pd.Series(np.zeros(len(data.close), dtype=int), index=data.index),
            warmup_bars=1)
    impl.__wrapped_impl__ = impl
    return impl


def _registry(*, with_v2: bool = False, v2_value: float = 2.0,
              pinned: dict | None = None) -> IndicatorRegistry:
    registry = IndicatorRegistry()
    registry.register(_spec("DEP", "DEP_V1", "f", "dep", "NEW_IN_V2", ["x"],
                            {"window": 3}, ("close",), warmup_bars=1),
                      _adapter(_leaf_source("DEP_V1", 1.0), ["x"]))
    if with_v2:
        registry.register(_spec("DEP", "DEP_V2", "f", "dep", "NEW_IN_V2", ["x"],
                                {"window": 3}, ("close",), warmup_bars=1),
                          _adapter(_leaf_source("DEP_V2", v2_value), ["x"]))
    registry.register(_spec("PARENT", "PARENT_V1", "f", "parent", "NEW_IN_V2", ["y"],
                            {}, ("close",), warmup_bars=1),
                      _adapter(_consuming_parent(registry, "DEP"), ["y"]),
                      dependencies=("DEP",), pinned_versions=pinned)
    return registry


def _parent_value(registry: IndicatorRegistry) -> float:
    return float(registry.compute("PARENT", CLOSE).output("y").iloc[-1])


# ==========================================================================
# B1：固定版本必须真实约束计算（不只是指纹）
# ==========================================================================
def test_pr15b1_pinned_version_constrains_actual_computation():
    """固定 DEP_V1 后，加入/修改 DEP_V2 都不得改变父公式的**实际输出**。"""
    only_v1 = _registry(with_v2=False, pinned={"DEP": "DEP_V1"})
    with_v2 = _registry(with_v2=True, v2_value=2.0, pinned={"DEP": "DEP_V1"})
    changed_v2 = _registry(with_v2=True, v2_value=99.0, pinned={"DEP": "DEP_V1"})

    assert _parent_value(only_v1) == pytest.approx(1.0)
    assert _parent_value(with_v2) == pytest.approx(1.0), \
        "固定 DEP_V1 却算出了 DEP_V2 的值（pin 未约束计算）"
    assert _parent_value(changed_v2) == pytest.approx(1.0), \
        "修改未选中的 DEP_V2 影响了父输出"

    hash_only = only_v1.formula_hash("PARENT")
    assert with_v2.formula_hash("PARENT") == hash_only
    assert changed_v2.formula_hash("PARENT") == hash_only


def test_pr15b1_default_resolution_drives_both_output_and_hash():
    """未固定版本时，默认解析（最大版本）必须同时决定父输出与父 hash。"""
    only_v1 = _registry(with_v2=False)
    assert _parent_value(only_v1) == pytest.approx(1.0)
    hash_before = only_v1.formula_hash("PARENT")

    with_v2 = _registry(with_v2=True, v2_value=2.0)
    assert _parent_value(with_v2) == pytest.approx(2.0), "默认解析未切到 DEP_V2"
    hash_after = with_v2.formula_hash("PARENT")
    assert hash_after != hash_before, "父输出变了但指纹没变（两者未共享解析）"

    changed = _registry(with_v2=True, v2_value=99.0)
    assert _parent_value(changed) == pytest.approx(99.0)
    assert changed.formula_hash("PARENT") != hash_after, \
        "选中版本内容变化未改变指纹"


def test_pr15b1_changing_selected_version_changes_both():
    """固定到 V2 后，修改 V2 必须同时改变父输出与父 hash。"""
    before = _registry(with_v2=True, v2_value=2.0, pinned={"DEP": "DEP_V2"})
    after = _registry(with_v2=True, v2_value=99.0, pinned={"DEP": "DEP_V2"})
    assert _parent_value(before) == pytest.approx(2.0)
    assert _parent_value(after) == pytest.approx(99.0)
    assert before.formula_hash("PARENT") != after.formula_hash("PARENT")


def test_pr15b1_pinned_to_v1_equals_v1_only_hash():
    """固定 V1 的指纹必须等于"只有 V1"时的指纹。"""
    pinned = _registry(with_v2=True, pinned={"DEP": "DEP_V1"})
    only_v1 = _registry(with_v2=False)
    assert pinned.formula_hash("PARENT") == only_v1.formula_hash("PARENT")


def test_pr15b1_nested_pinned_not_overridden_by_other_node_defaults():
    """父与嵌套依赖的固定版本不得被其他节点的默认值覆盖。"""
    registry = IndicatorRegistry()
    # A: V1=1, V2=2
    registry.register(_spec("A", "A_V1", "f", "a", "NEW_IN_V2", ["x"],
                            {"window": 3}, ("close",), warmup_bars=1),
                      _adapter(_leaf_source("A_V1", 1.0), ["x"]))
    registry.register(_spec("A", "A_V2", "f", "a", "NEW_IN_V2", ["x"],
                            {"window": 3}, ("close",), warmup_bars=1),
                      _adapter(_leaf_source("A_V2", 2.0), ["x"]))
    # MID 消费 A，并固定 A 到 V1
    registry.register(_spec("MID", "MID_V1", "f", "mid", "NEW_IN_V2", ["y"],
                            {}, ("close",), warmup_bars=1),
                      _adapter(_consuming_parent(registry, "A", "MID"), ["y"]),
                      dependencies=("A",), pinned_versions={"A": "A_V1"})
    # PARENT 消费 MID（不固定 A），默认 A 会解析到 V2，但 MID 内部固定应生效
    registry.register(_spec("PARENT", "PARENT_V1", "f", "parent", "NEW_IN_V2", ["y"],
                            {}, ("close",), warmup_bars=1),
                      _adapter(_consuming_parent(registry, "MID"), ["y"]),
                      dependencies=("MID",))
    # MID 内部固定 A_V1 -> 输出 1.0；若被默认 V2 覆盖则为 2.0
    assert float(registry.compute("MID", CLOSE).output("y").iloc[-1]) == pytest.approx(1.0), \
        "嵌套固定版本被默认最新版覆盖"
    assert float(registry.compute("PARENT", CLOSE).output("y").iloc[-1]) == pytest.approx(1.0)


def test_pr15b1_unsupported_calls_are_rejected():
    """缺版本、缺实现、作用域外调用都必须明确拒绝，不回退。"""
    # 作用域外调用
    with pytest.raises(DependencyResolutionError) as excinfo:
        IndicatorRegistry().compute_dependency("DEP", CLOSE)
    assert "DEPENDENCY_CALL_OUTSIDE_COMPUTE" in str(excinfo.value)

    # 固定到不存在的版本
    bad = _registry(with_v2=False, pinned={"DEP": "DEP_V9"})
    with pytest.raises(Exception) as excinfo:
        bad.formula_hash("PARENT")
    assert "DEP_V9" in str(excinfo.value)

    # 声明依赖但未注册实现
    registry = IndicatorRegistry()
    registry.register(_spec("PARENT", "PARENT_V1", "f", "parent", "NEW_IN_V2", ["y"],
                            {}, ("close",), warmup_bars=1),
                      _adapter(_consuming_parent(registry, "GHOST"), ["y"]),
                      dependencies=("GHOST",))
    with pytest.raises(Exception) as excinfo:
        registry.formula_hash("PARENT")
    assert "GHOST" in str(excinfo.value)


# ==========================================================================
# B2：共享依赖是合法 DAG，不得误报循环
# ==========================================================================
def _shared_dag_registry():
    """合法图：PARENT→A，PARENT→B，B→A。"""
    registry = IndicatorRegistry()
    registry.register(_spec("A", "A_V1", "f", "a", "NEW_IN_V2", ["x"],
                            {"window": 3}, ("close",), warmup_bars=1),
                      _adapter(_leaf_source("A_V1", 1.0), ["x"]))
    registry.register(_spec("B", "B_V1", "f", "b", "NEW_IN_V2", ["y"],
                            {}, ("close",), warmup_bars=1),
                      _adapter(_consuming_parent(registry, "A", "B"), ["y"]),
                      dependencies=("A",))
    registry.register(_spec("PARENT", "PARENT_V1", "f", "parent", "NEW_IN_V2", ["y"],
                            {}, ("close",), warmup_bars=1),
                      _adapter(_consuming_parent(registry, "A", "PARENT", extra="B"), ["y"]),
                      dependencies=("A", "B"))
    return registry


def test_pr15b2_shared_dependency_dag_is_accepted():
    """共享依赖（PARENT→A、PARENT→B、B→A）必须通过，不得抛 CIRCULAR_DEPENDENCY。"""
    registry = _shared_dag_registry()
    digest = registry.formula_hash("PARENT")          # 不得抛异常
    assert digest
    # A 输出 1.0，B 输出 A=1.0，PARENT 输出 A+B=2.0
    assert float(registry.compute("PARENT", CLOSE).output("y").iloc[-1]) == pytest.approx(2.0)


def test_dependency_scope_rejects_direct_registry_compute():
    """防误用：依赖被固定时，父实现直接 ``registry.compute`` 必须失败。

    模拟一个自定义指标在实现内部直接调用 ``registry.compute("DEP")``：
    它会静默走默认最新版并绕过固定版本。有 pinned scope 时必须拒绝，
    而不是静默给出 DEP_V2 的值。
    """
    registry = IndicatorRegistry()
    registry.register(_spec("DEP", "DEP_V1", "f", "dep", "NEW_IN_V2", ["x"],
                            {"window": 3}, ("close",), warmup_bars=1),
                      _adapter(_leaf_source("DEP_V1", 1.0), ["x"]))
    registry.register(_spec("DEP", "DEP_V2", "f", "dep", "NEW_IN_V2", ["x"],
                            {"window": 3}, ("close",), warmup_bars=1),
                      _adapter(_leaf_source("DEP_V2", 2.0), ["x"]))

    def bypassing_parent(data, **kwargs):
        """错误示范：直接 registry.compute，绕过依赖作用域。"""
        dep = registry.compute("DEP", pd.Series(data.close, index=data.index))
        return IndicatorFrameV2(
            indicator_id="PARENT", version="PARENT_V1", index=data.index,
            columns={"y": dep.output("x")}, ready=pd.Series(True, index=data.index),
            segment=pd.Series(np.zeros(len(data.close), dtype=int), index=data.index),
            warmup_bars=1)

    bypassing_parent.__wrapped_impl__ = bypassing_parent
    registry.register(_spec("PARENT", "PARENT_V1", "f", "parent", "NEW_IN_V2", ["y"],
                            {}, ("close",), warmup_bars=1),
                      _adapter(bypassing_parent, ["y"]),
                      dependencies=("DEP",), pinned_versions={"DEP": "DEP_V1"})

    with pytest.raises(DependencyResolutionError) as excinfo:
        registry.compute("PARENT", CLOSE)
    message = str(excinfo.value)
    assert "DIRECT_COMPUTE_BYPASSES_PINNED_DEPENDENCY" in message
    assert "DEP" in message and "DEP_V1" in message
    assert "compute_dependency" in message, "错误信息未指出正确做法"


def test_dependency_scope_allows_direct_compute_when_not_pinned():
    """未固定该依赖时，直接 compute 不构成绕过（不误报）。"""
    registry = IndicatorRegistry()
    registry.register(_spec("DEP", "DEP_V1", "f", "dep", "NEW_IN_V2", ["x"],
                            {"window": 3}, ("close",), warmup_bars=1),
                      _adapter(_leaf_source("DEP_V1", 1.0), ["x"]))

    def direct_parent(data, **kwargs):
        dep = registry.compute("DEP", pd.Series(data.close, index=data.index))
        return IndicatorFrameV2(
            indicator_id="PARENT", version="PARENT_V1", index=data.index,
            columns={"y": dep.output("x")}, ready=pd.Series(True, index=data.index),
            segment=pd.Series(np.zeros(len(data.close), dtype=int), index=data.index),
            warmup_bars=1)

    direct_parent.__wrapped_impl__ = direct_parent
    registry.register(_spec("PARENT", "PARENT_V1", "f", "parent", "NEW_IN_V2", ["y"],
                            {}, ("close",), warmup_bars=1),
                      _adapter(direct_parent, ["y"]), dependencies=("DEP",))
    # 没有 pinned -> 不拒绝
    assert float(registry.compute("PARENT", CLOSE).output("y").iloc[-1]) == pytest.approx(1.0)


def test_dependency_scope_allows_explicit_version_in_direct_compute():
    """显式传 version 时不算绕过（调用方已明确指定版本）。"""
    registry = IndicatorRegistry()
    registry.register(_spec("DEP", "DEP_V1", "f", "dep", "NEW_IN_V2", ["x"],
                            {"window": 3}, ("close",), warmup_bars=1),
                      _adapter(_leaf_source("DEP_V1", 1.0), ["x"]))
    registry.register(_spec("DEP", "DEP_V2", "f", "dep", "NEW_IN_V2", ["x"],
                            {"window": 3}, ("close",), warmup_bars=1),
                      _adapter(_leaf_source("DEP_V2", 2.0), ["x"]))

    def explicit_parent(data, **kwargs):
        dep = registry.compute("DEP", pd.Series(data.close, index=data.index),
                               version="DEP_V1")
        return IndicatorFrameV2(
            indicator_id="PARENT", version="PARENT_V1", index=data.index,
            columns={"y": dep.output("x")}, ready=pd.Series(True, index=data.index),
            segment=pd.Series(np.zeros(len(data.close), dtype=int), index=data.index),
            warmup_bars=1)

    explicit_parent.__wrapped_impl__ = explicit_parent
    registry.register(_spec("PARENT", "PARENT_V1", "f", "parent", "NEW_IN_V2", ["y"],
                            {}, ("close",), warmup_bars=1),
                      _adapter(explicit_parent, ["y"]),
                      dependencies=("DEP",), pinned_versions={"DEP": "DEP_V1"})
    assert float(registry.compute("PARENT", CLOSE).output("y").iloc[-1]) == pytest.approx(1.0)


def test_builtin_rsi_regime_flag_uses_dependency_path():
    """内置的依赖型指标必须走 compute_dependency，且其固定版本生效。"""
    from chanlun_trader.engine.custom_indicators_v2 import register_custom_indicators
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    registry = default_registry()
    register_custom_indicators(registry)
    assert "RSI" in registry._dependencies.get("RSI_REGIME_FLAG", ()), \
        "RSI_REGIME_FLAG 未声明 RSI 依赖"
    # 固定 RSI_V1 后，计算必须成功（若内部仍用直接 compute 会被拒绝）
    result = registry.compute("RSI_REGIME_FLAG", CLOSE)
    assert result.output_names == ["regime", "rsi_input"]
    # 指纹与计算都解析到同一版本
    assert registry.formula_hash("RSI_REGIME_FLAG")


def test_pr15b2_shared_dependency_hash_is_deterministic():
    """共享依赖的指纹必须确定（独立构建的等价注册表给出相同结果）。"""
    first = _shared_dag_registry().formula_hash("PARENT")
    second = _shared_dag_registry().formula_hash("PARENT")
    third = _shared_dag_registry().formula_hash("PARENT")
    assert first == second == third, "同一图重复构建产生了不同指纹"
    # 指纹必须真实非空且为 hex 摘要
    assert len(first) == 16 and all(ch in "0123456789abcdef" for ch in first)


def test_pr15b2_real_cycle_still_rejected():
    """真正的自循环与回边继续拒绝。"""
    self_loop = IndicatorRegistry()
    self_loop.register(_spec("LOOP", "LOOP_V1", "f", "loop", "NEW_IN_V2", ["x"],
                             {}, ("close",), warmup_bars=1),
                       _adapter(_leaf_source("LOOP_V1", 1.0), ["x"]),
                       dependencies=("LOOP",))
    with pytest.raises(DependencyResolutionError) as excinfo:
        self_loop.formula_hash("LOOP")
    assert "CIRCULAR_DEPENDENCY" in str(excinfo.value)

    back_edge = IndicatorRegistry()
    back_edge.register(_spec("A", "A_V1", "f", "a", "NEW_IN_V2", ["x"],
                             {}, ("close",), warmup_bars=1),
                       _adapter(_consuming_parent(back_edge, "B", "A"), ["y"]),
                       dependencies=("B",))
    back_edge.register(_spec("B", "B_V1", "f", "b", "NEW_IN_V2", ["x"],
                             {}, ("close",), warmup_bars=1),
                       _adapter(_consuming_parent(back_edge, "A", "B"), ["y"]),
                       dependencies=("A",))
    with pytest.raises(DependencyResolutionError) as excinfo:
        back_edge.formula_hash("A")
    assert "CIRCULAR_DEPENDENCY" in str(excinfo.value)


def test_pr15b2_registration_order_does_not_change_result():
    """注册顺序不改变确定结果（输出与指纹一致）。"""
    def build(order):
        registry = IndicatorRegistry()
        specs = {
            "DEP_V1": 1.0,
            "DEP_V2": 2.0,
        }
        for version in order:
            registry.register(_spec("DEP", version, "f", "dep", "NEW_IN_V2", ["x"],
                                    {"window": 3}, ("close",), warmup_bars=1),
                              _adapter(_leaf_source(version, specs[version]), ["x"]))
        registry.register(_spec("PARENT", "PARENT_V1", "f", "parent", "NEW_IN_V2", ["y"],
                                {}, ("close",), warmup_bars=1),
                          _adapter(_consuming_parent(registry, "DEP"), ["y"]),
                          dependencies=("DEP",))
        return registry

    forward = build(["DEP_V1", "DEP_V2"])
    backward = build(["DEP_V2", "DEP_V1"])
    assert forward.resolve("DEP").version == backward.resolve("DEP").version == "DEP_V2"
    assert _parent_value(forward) == _parent_value(backward) == pytest.approx(2.0)
    assert forward.formula_hash("PARENT") == backward.formula_hash("PARENT")
