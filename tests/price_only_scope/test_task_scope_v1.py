"""PR16-02：显式任务作用域与冻结禁止身份。

要求：
- 报告文件被提交后会长期存在，**不能**作为"当前正在执行 price-only"的判据；
- 生产 `_load_gbbq` 与真实测试分类消费**同一个**作用域；
- 激活时限制本任务，退出时返回原有权限，不创造真实数据授权；
- 不能要求删除报告、切到旧版本或设置 allow 环境变量才能退出限制；
- 在合成环境测试：有/无历史报告不改变作用域；激活/退出/异常后恢复；
  cwd 改变不改变禁止身份；两个任务上下文不会串；`test_tdx_data` 分类一致。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chanlun_trader.price_only_scope import (  # noqa: E402
    COMPOSITION_ROOT,
    restore_task_scope,
    snapshot_task_scope,
    ForbiddenDataAccess,
    activate_task_scope,
    deactivate_task_scope,
    frozen_denylist,
    is_forbidden_gbbq_path,
    rebuild_frozen_denylist,
    task_scope_active,
    task_scope_reason,
)
from chanlun_trader.tdx_data import TdxData  # noqa: E402

REAL_GBBQ = "E:/new_tdx_mock/T0002/hq_cache/gbbq"


@pytest.fixture(autouse=True)
def _restore_scope():
    """保存进入前的**完整政策**并在退出时恢复，不无条件关闭外层任务。

    缺陷（PR16 复核）：原实现 ``yield`` 后无条件 ``deactivate_task_scope()``，
    会关闭外层仍在运行的任务（把别人的激活状态清成 False）。
    统一实现：保存进入前政策 → 激活内部作用域 → finally 恢复原政策。
    恢复对象包含 active、reason、受保护身份与环境清单。
    """
    snapshot = snapshot_task_scope()
    try:
        yield
    finally:
        restore_task_scope(snapshot)


# ==========================================================================
# 上层任务不能被测试清理关闭（PR16-02 复核的最低验收序列）
# ==========================================================================
def _protected_synthetic_cache(tmp_path: Path) -> Path:
    """构造一个受保护的合成缓存（列入禁止集合）。"""
    cache = tmp_path / "external" / "cache"
    cache.mkdir(parents=True)
    target = cache / "gbbq.csv"
    target.write_text("code,datetime,category\n", encoding="utf-8")
    os.environ["CHANLUN_FORBIDDEN_GBBQ_PATHS"] = str(target)
    rebuild_frozen_denylist()
    return target


def test_outer_activation_survives_inner_scope_cleanup(tmp_path: Path):
    """验收序列：外层激活 → 受保护缓存拒绝 → 内部清理 → 仍拒绝。

    防止 `_restore_scope` 这类清理把外层任务的作用域关掉。
    """
    os.environ.pop("CHANLUN_FORBIDDEN_GBBQ_PATHS", None)
    rebuild_frozen_denylist()
    try:
        target = _protected_synthetic_cache(tmp_path)
        activate_task_scope("outer_task")
        assert task_scope_active()
        assert is_forbidden_gbbq_path(target), "外层激活下受保护缓存未被拒"

        # 模拟一次内部作用域测试 / 异常清理
        before = task_scope_active()
        before_reason = task_scope_reason()
        try:
            raise RuntimeError("inner scope failure")
        except RuntimeError:
            # 正确的清理：恢复进入前状态（而不是无条件 deactivate）
            if before:
                activate_task_scope(before_reason)
            else:
                deactivate_task_scope()

        # 外层仍必须处于激活，且同一缓存仍被拒
        assert task_scope_active(), "内部清理关闭了外层任务作用域"
        assert is_forbidden_gbbq_path(target), "内部清理后受保护缓存不再被拒"
    finally:
        os.environ.pop("CHANLUN_FORBIDDEN_GBBQ_PATHS", None)
        rebuild_frozen_denylist()


def test_outer_inactive_restores_previous_state(tmp_path: Path):
    """外层未激活时，局部任务正常退出/异常后都返回此前状态。"""
    os.environ.pop("CHANLUN_FORBIDDEN_GBBQ_PATHS", None)
    rebuild_frozen_denylist()
    deactivate_task_scope()
    assert not task_scope_active()

    # 正常退出
    activate_task_scope("local")
    deactivate_task_scope()
    assert not task_scope_active(), "正常退出后未恢复此前状态"

    # 异常退出
    try:
        activate_task_scope("local")
        raise RuntimeError("boom")
    except RuntimeError:
        deactivate_task_scope()
    assert not task_scope_active(), "异常退出后未恢复此前状态"


def test_inner_scope_does_not_grant_real_permissions(tmp_path: Path):
    """局部任务退出后不得新授予真实权限（真实 gbbq 仍被拒）。"""
    os.environ.pop("CHANLUN_FORBIDDEN_GBBQ_PATHS", None)
    rebuild_frozen_denylist()
    try:
        activate_task_scope("inner")
        deactivate_task_scope()
        # 即使作用域未激活，真实 gbbq 根身份仍在冻结集合中
        assert is_forbidden_gbbq_path(REAL_GBBQ), "局部任务退出后真实 gbbq 不再被拒"
    finally:
        rebuild_frozen_denylist()


# ==========================================================================
# 作用域由显式激活驱动，不由报告文件推断
# ==========================================================================
def test_report_file_does_not_activate_scope(tmp_path: Path, monkeypatch):
    """有/无历史报告不改变作用域（报告存在不等于任务在跑）。"""
    report_dir = tmp_path / "reports" / "price_only_validation_v1"
    report_dir.mkdir(parents=True)
    (report_dir / "PRICE_ONLY_CAPABILITY_MATRIX_V1.json").write_text("{}", encoding="utf-8")
    deactivate_task_scope()
    assert not task_scope_active(), "报告文件不应激活作用域"
    # 真实项目里报告确实存在，作用域仍由显式激活决定
    assert (COMPOSITION_ROOT / "reports" / "price_only_validation_v1").exists()
    assert not task_scope_active()


def test_activate_and_deactivate_restores_permissions():
    """激活时限制，退出时恢复原有权限。"""
    deactivate_task_scope()
    assert not task_scope_active()
    activate_task_scope("unit_test")
    assert task_scope_active()
    assert task_scope_reason() == "unit_test"
    deactivate_task_scope()
    assert not task_scope_active()
    assert task_scope_reason() == ""


def test_exception_path_resets_scope():
    """异常路径后必须能复位（不残留限制）。"""
    activate_task_scope("exception_test")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        deactivate_task_scope()
    assert not task_scope_active()


def test_scope_not_activated_means_no_restriction(tmp_path: Path):
    """作用域未激活时不限制：既有语义不被永久改变。"""
    deactivate_task_scope()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "gbbq.csv").write_text("code,datetime,category\n000001,20240101,1\n",
                                     encoding="utf-8")
    tdx = TdxData(str(tmp_path / "vipdoc"), str(tmp_path / "gbbq"), cache_dir=str(cache))
    # 未激活 -> 不触发守卫（正常读取临时夹具）
    frame = tdx._load_gbbq()
    assert len(frame) == 1


def test_scope_activated_blocks_real_gbbq(tmp_path: Path):
    """作用域激活时真实 gbbq 被拒（生产路径消费同一作用域）。"""
    activate_task_scope("unit_test")
    tdx = TdxData(str(tmp_path / "vipdoc"), REAL_GBBQ, cache_dir=str(tmp_path / "c"))
    with pytest.raises(ForbiddenDataAccess):
        tdx._load_gbbq()


def test_two_task_contexts_do_not_bleed(tmp_path: Path):
    """两个任务上下文不串：激活-退出-再激活语义独立。"""
    deactivate_task_scope()
    assert not task_scope_active()
    activate_task_scope("task_a")
    assert task_scope_reason() == "task_a"
    deactivate_task_scope()
    activate_task_scope("task_b")
    assert task_scope_reason() == "task_b"
    deactivate_task_scope()
    assert not task_scope_active()


# ==========================================================================
# 冻结禁止身份：cwd 不改变判定
# ==========================================================================
def test_cwd_change_does_not_change_forbidden_identity(tmp_path: Path):
    """cwd 改变不得改变禁止身份（复现并关闭 cwd 反例）。

    反例：相对清单 + 同一绝对路径，cwd 变化使判定由拒绝翻转为放行。
    """
    origin = os.getcwd()
    external = COMPOSITION_ROOT / "external" / "cache"
    external.mkdir(parents=True, exist_ok=True)
    target = external / "gbbq.csv"
    created = not target.exists()
    if created:
        target.write_text("x", encoding="utf-8")
    try:
        os.environ["CHANLUN_FORBIDDEN_GBBQ_PATHS"] = "external/cache/gbbq.csv"
        rebuild_frozen_denylist()
        abs_target = str(target.resolve())
        os.chdir(COMPOSITION_ROOT)
        first = is_forbidden_gbbq_path(abs_target)
        other = COMPOSITION_ROOT / "tmp" / "cwd_scope_test"
        other.mkdir(parents=True, exist_ok=True)
        os.chdir(other)
        second = is_forbidden_gbbq_path(abs_target)
        assert first is True and second is True, \
            f"cwd 改变了禁止身份：{first} -> {second}"
    finally:
        os.chdir(origin)
        os.environ.pop("CHANLUN_FORBIDDEN_GBBQ_PATHS", None)
        rebuild_frozen_denylist()
        if created and target.exists():
            target.unlink()
        if external.exists() and not any(external.iterdir()):
            external.rmdir()
        shutil.rmtree(COMPOSITION_ROOT / "tmp" / "cwd_scope_test", ignore_errors=True)


def test_frozen_denylist_keeps_real_roots_under_env_override():
    """环境变量至多追加约束：真实 gbbq 根身份始终保留。"""
    os.environ["CHANLUN_FORBIDDEN_GBBQ_PATHS"] = "/tmp/unrelated.dat"
    try:
        rebuild_frozen_denylist()
        deny = frozen_denylist()
        assert any("new_tdx_mock" in entry for entry in deny), \
            "环境变量移除了真实 gbbq 根身份"
    finally:
        os.environ.pop("CHANLUN_FORBIDDEN_GBBQ_PATHS", None)
        rebuild_frozen_denylist()


def test_allow_env_var_does_not_lift_restriction(monkeypatch, tmp_path: Path):
    """不允许通过环境变量放开真实 gbbq。"""
    activate_task_scope("unit_test")
    monkeypatch.setenv("CHANLUN_ALLOW_GBBQ_READ", "1")
    tdx = TdxData(str(tmp_path / "vipdoc"), REAL_GBBQ, cache_dir=str(tmp_path / "c"))
    with pytest.raises(ForbiddenDataAccess):
        tdx._load_gbbq()


def test_composition_root_is_stable():
    """组合根固定为源码根，不随 cwd 变化。"""
    origin = os.getcwd()
    first = COMPOSITION_ROOT
    try:
        os.chdir(COMPOSITION_ROOT)
        assert COMPOSITION_ROOT == first
    finally:
        os.chdir(origin)
    assert first == Path(__file__).resolve().parents[2]
