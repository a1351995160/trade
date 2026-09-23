"""PR16-01：相对符号链接必须按**组合根**解析真实指向。

复核给定缺陷：``_freeze_identity_closure`` 的词法绝对路径基于 COMPOSITION_ROOT，
但 ``realpath(raw)`` 与 ``realpath(Path(raw).parent)`` 仍基于 **cwd**。
结果：建立政策时 cwd 不是组合根，则相对链接指向的真实目标未被纳入闭包，
通过缓存入口访问目标本体可读（绕过）。

本模块交叉覆盖（任务要求）：
- 绝对 / 相对配置；
- 清单列别名 / 清单列目标；
- cwd 等于 / 不同于组合根；
- 冻结后再改变 cwd。

受保护情况经受控 wrapper 或 TdxData 入口，并验证 open=0；
允许的合成夹具保留正对照。全部使用临时合成哨兵，禁止真实数据。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chanlun_trader.price_only_scope import (  # noqa: E402
    ForbiddenDataAccess,
    controlled_gbbq_reader,
    is_forbidden_gbbq_path,
    rebuild_frozen_denylist,
    restore_task_scope,
    snapshot_task_scope,
)


@pytest.fixture
def layout(tmp_path: Path, monkeypatch):
    """构造 source_root/aliases/events.dat -> external/cache/gbbq.csv 布局。

    组合根临时指向 source_root；退出时恢复原组合根与 cwd。
    """
    import chanlun_trader.price_only_scope as scope

    source_root = tmp_path / "source_root"
    external = tmp_path / "external" / "cache"
    aliases = source_root / "aliases"
    external.mkdir(parents=True)
    aliases.mkdir(parents=True)
    target = external / "gbbq.csv"
    target.write_text("code,datetime,category\n000001,20240101,1\n", encoding="utf-8")
    link = aliases / "events.dat"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("NOT_APPLICABLE: 本机不支持符号链接")

    origin_root = scope.COMPOSITION_ROOT
    origin_cwd = os.getcwd()
    snapshot = snapshot_task_scope()
    monkeypatch.setattr(scope, "COMPOSITION_ROOT", source_root)
    try:
        yield {"source_root": source_root, "target": target, "link": link,
               "external": external}
    finally:
        os.chdir(origin_cwd)
        monkeypatch.setattr(scope, "COMPOSITION_ROOT", origin_root)
        restore_task_scope(snapshot)


def _freeze_with_cwd(monkeypatch, cwd: Path, config: str) -> None:
    monkeypatch.setenv("CHANLUN_FORBIDDEN_GBBQ_PATHS", config)
    os.chdir(cwd)
    rebuild_frozen_denylist()


# ==========================================================================
# 核心反例：cwd 不是组合根时，相对链接的真实目标也必须被纳入闭包
# ==========================================================================
def test_relative_symlink_resolved_against_composition_root(layout, monkeypatch, tmp_path):
    """cwd != 组合根：清单写相对链接，访问目标本体必须被拒。"""
    _freeze_with_cwd(monkeypatch, tmp_path, "aliases/events.dat")
    assert is_forbidden_gbbq_path(layout["target"]), \
        "cwd 不是组合根时，相对链接的真实目标未被纳入闭包（绕过）"


def test_relative_symlink_same_result_regardless_of_cwd(layout, monkeypatch, tmp_path):
    """cwd 等于/不同于组合根，判定必须一致。"""
    _freeze_with_cwd(monkeypatch, layout["source_root"], "aliases/events.dat")
    at_root = is_forbidden_gbbq_path(layout["target"])
    _freeze_with_cwd(monkeypatch, tmp_path, "aliases/events.dat")
    away_from_root = is_forbidden_gbbq_path(layout["target"])
    assert at_root is True
    assert away_from_root is True, "cwd 改变了判定结果"


def test_freeze_then_change_cwd_keeps_identity(layout, monkeypatch, tmp_path):
    """冻结后再改变 cwd，禁止身份不得变化。"""
    _freeze_with_cwd(monkeypatch, layout["source_root"], "aliases/events.dat")
    first = is_forbidden_gbbq_path(layout["target"])
    other = tmp_path / "elsewhere"
    other.mkdir()
    os.chdir(other)
    second = is_forbidden_gbbq_path(layout["target"])
    assert first is True
    assert second is True, "冻结后改变 cwd 使判定翻转"


# ==========================================================================
# 四种组合：绝对/相对 x 清单列别名/列目标
# ==========================================================================
@pytest.mark.parametrize("config_kind", ["relative_alias", "absolute_alias",
                                         "absolute_target"])
def test_all_config_kinds_reject_target_and_alias(layout, monkeypatch, tmp_path, config_kind):
    """相对别名 / 绝对别名 / 绝对目标 三种配置都要同时拒绝别名与目标本体。"""
    if config_kind == "relative_alias":
        config = "aliases/events.dat"
    elif config_kind == "absolute_alias":
        config = str(layout["link"])
    else:
        config = str(layout["target"])
    _freeze_with_cwd(monkeypatch, tmp_path, config)
    assert is_forbidden_gbbq_path(layout["link"]), f"{config_kind}: 别名未被拒"
    assert is_forbidden_gbbq_path(layout["target"]), f"{config_kind}: 目标本体未被拒"


# ==========================================================================
# 受保护情况经真实入口，open=0
# ==========================================================================
def test_controlled_reader_blocks_target_with_zero_open(layout, monkeypatch, tmp_path):
    """经受控 wrapper 访问受保护目标：拒绝且 open=0。"""
    from chanlun_trader.price_only_scope import activate_task_scope, deactivate_task_scope

    _freeze_with_cwd(monkeypatch, tmp_path, "aliases/events.dat")
    activate_task_scope("relative_link_test")
    opened: list = []
    real_open = open

    def probe_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", probe_open)
    try:
        with pytest.raises(ForbiddenDataAccess):
            controlled_gbbq_reader(str(layout["target"]))
        assert opened == [], f"拒绝发生在 open 之后：{opened}"
    finally:
        deactivate_task_scope()


def test_tdxdata_cache_entry_blocks_target(layout, monkeypatch, tmp_path):
    """经原 TdxData 缓存入口访问受保护目标：拒绝且 open=0。"""
    from chanlun_trader.price_only_scope import activate_task_scope, deactivate_task_scope
    from chanlun_trader.tdx_data import TdxData

    _freeze_with_cwd(monkeypatch, tmp_path, "aliases/events.dat")
    activate_task_scope("relative_link_tdx_test")
    opened: list = []
    real_open = open

    def probe_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", probe_open)
    try:
        tdx = TdxData(str(tmp_path / "vipdoc"), str(tmp_path / "unused"),
                      cache_dir=str(layout["target"].parent))
        with pytest.raises(ForbiddenDataAccess):
            tdx._load_gbbq()
        assert opened == [], f"拒绝发生在 open 之后：{opened}"
    finally:
        deactivate_task_scope()


# ==========================================================================
# 正对照：允许的合成夹具仍可读
# ==========================================================================
def test_allowed_synthetic_fixture_still_readable(layout, monkeypatch, tmp_path):
    """正对照：未被列入的合成夹具必须可读（修复未造成无差别拒绝）。"""
    from chanlun_trader.price_only_scope import activate_task_scope, deactivate_task_scope

    allowed_dir = tmp_path / "allowed_cache"
    allowed_dir.mkdir()
    allowed = allowed_dir / "gbbq.csv"
    allowed.write_text("code,datetime,category\n000001,20240101,1\n", encoding="utf-8")
    _freeze_with_cwd(monkeypatch, tmp_path, "aliases/events.dat")
    assert not is_forbidden_gbbq_path(allowed), "允许的合成夹具被误拒"

    activate_task_scope("relative_link_positive")
    from chanlun_trader.tdx_data import TdxData
    try:
        tdx = TdxData(str(tmp_path / "vipdoc"), str(tmp_path / "unused"),
                      cache_dir=str(allowed_dir))
        frame = tdx._load_gbbq()
        assert len(frame) == 1
    finally:
        deactivate_task_scope()