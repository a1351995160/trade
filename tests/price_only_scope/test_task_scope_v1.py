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
    """每个用例后复位作用域，避免测试间串扰。"""
    yield
    deactivate_task_scope()


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
