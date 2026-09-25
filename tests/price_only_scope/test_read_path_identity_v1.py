"""PR16-A：守卫与实际打开必须使用**同一个文件身份**。

复核给定反例：
    组合根 = T/source，cwd = T/outside
    禁止清单 = T/outside/cache/gbbq.csv（绝对）
    TdxData(cache_dir='T/outside/cache') -> 拒绝
    TdxData(cache_dir='cache')           -> 真实 pd.read_csv 读出同一个哨兵

根因：守卫把 ``cache/gbbq.csv`` 按组合根解释成 ``T/source/cache/gbbq.csv``，
读取却按 cwd 打开 ``T/outside/cache/gbbq.csv``。禁止集合/文件/active 都没变，
是最后的读入参数没有绑定。

修复：在**输入边界解析一次**，守卫、exists 与实际读取消费同一个已解析绝对对象
（``resolve_input_path``）；``controlled_gbbq_reader`` 同样绑定。

本模块覆盖任务要求的组合，全部使用临时合成哨兵：
- 绝对 / 相对 cache_dir；
- cwd 等于 / 不等于组合根；
- 冻结后再改变 cwd；
- 拒绝必须发生在真实打开之前（open 探针）；
- 允许的合成输入保留正对照。
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
    activate_task_scope,
    deactivate_task_scope,
    rebuild_frozen_denylist,
    restore_task_scope,
    snapshot_task_scope,
)
from chanlun_trader.tdx_data import TdxData  # noqa: E402


@pytest.fixture
def layout(tmp_path: Path, monkeypatch):
    """构造 T/source（组合根）与 T/outside/cache/gbbq.csv（哨兵）。"""
    import chanlun_trader.price_only_scope as scope

    source = tmp_path / "source"
    outside = tmp_path / "outside"
    cache = outside / "cache"
    cache.mkdir(parents=True)
    source.mkdir(parents=True)
    sentinel = cache / "gbbq.csv"
    sentinel.write_text("code,datetime,category\n000001,20240101,1\n", encoding="utf-8")

    origin_root = scope.COMPOSITION_ROOT
    origin_cwd = os.getcwd()
    snapshot = snapshot_task_scope()
    monkeypatch.setattr(scope, "COMPOSITION_ROOT", source)
    activate_task_scope("read_path_identity_test")
    try:
        yield {"source": source, "outside": outside, "cache": cache,
               "sentinel": sentinel}
    finally:
        os.chdir(origin_cwd)
        deactivate_task_scope()
        monkeypatch.setattr(scope, "COMPOSITION_ROOT", origin_root)
        restore_task_scope(snapshot)


def _protect(sentinel: Path, monkeypatch) -> None:
    monkeypatch.setenv("CHANLUN_FORBIDDEN_GBBQ_PATHS", str(sentinel))
    rebuild_frozen_denylist()


# ==========================================================================
# 核心反例：相对 cache_dir 不得被解释成与守卫不同的文件
# ==========================================================================
def test_relative_cache_dir_is_rejected_not_silently_read(layout, monkeypatch):
    """cache_dir='cache'（相对）：必须拒绝，不得真实读出同一哨兵。"""
    _protect(layout["sentinel"], monkeypatch)
    os.chdir(layout["outside"])
    tdx = TdxData(str(layout["source"] / "vipdoc"),
                  str(layout["source"] / "unused"), cache_dir="cache")
    with pytest.raises((ForbiddenDataAccess, FileNotFoundError)):
        tdx._load_gbbq()


def test_absolute_cache_dir_is_rejected(layout, monkeypatch):
    """cache_dir=绝对路径：拒绝（正对照基线）。"""
    _protect(layout["sentinel"], monkeypatch)
    os.chdir(layout["outside"])
    tdx = TdxData(str(layout["source"] / "vipdoc"),
                  str(layout["source"] / "unused"), cache_dir=str(layout["cache"]))
    with pytest.raises(ForbiddenDataAccess):
        tdx._load_gbbq()


@pytest.mark.parametrize("cwd_kind", ["outside", "source"])
def test_rejection_consistent_regardless_of_cwd(layout, monkeypatch, cwd_kind):
    """cwd 等于/不等于组合根，判定必须一致。"""
    _protect(layout["sentinel"], monkeypatch)
    os.chdir(layout["outside"] if cwd_kind == "outside" else layout["source"])
    tdx = TdxData(str(layout["source"] / "vipdoc"),
                  str(layout["source"] / "unused"), cache_dir=str(layout["cache"]))
    with pytest.raises(ForbiddenDataAccess):
        tdx._load_gbbq()


def test_freeze_then_change_cwd_still_rejects(layout, monkeypatch):
    """冻结后再改变 cwd，拒绝结论不变。"""
    _protect(layout["sentinel"], monkeypatch)
    os.chdir(layout["source"])
    tdx = TdxData(str(layout["source"] / "vipdoc"),
                  str(layout["source"] / "unused"), cache_dir=str(layout["cache"]))
    other = layout["outside"] / "elsewhere"
    other.mkdir()
    os.chdir(other)
    with pytest.raises(ForbiddenDataAccess):
        tdx._load_gbbq()


# ==========================================================================
# 拒绝发生在真实打开之前
# ==========================================================================
def test_rejection_happens_before_any_open(layout, monkeypatch):
    """open 探针：拒绝时该哨兵从未被打开。"""
    _protect(layout["sentinel"], monkeypatch)
    os.chdir(layout["outside"])
    opened: list = []
    real_open = open

    def probe_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", probe_open)
    tdx = TdxData(str(layout["source"] / "vipdoc"),
                  str(layout["source"] / "unused"), cache_dir="cache")
    with pytest.raises((ForbiddenDataAccess, FileNotFoundError)):
        tdx._load_gbbq()
    assert not any(str(layout["sentinel"]) in p for p in opened), \
        f"受保护哨兵被打开：{opened}"


def test_controlled_reader_binds_same_identity(layout, monkeypatch):
    """controlled_gbbq_reader 同样绑定：守卫与 vendor 打开用同一对象。"""
    from chanlun_trader.price_only_scope import controlled_gbbq_reader

    _protect(layout["sentinel"], monkeypatch)
    os.chdir(layout["outside"])
    decoded: list = []
    from pytdx.reader import GbbqReader
    real_get_df = GbbqReader.get_df

    def probe_get_df(self, name):
        decoded.append(str(name))
        return real_get_df(self, name)

    monkeypatch.setattr(GbbqReader, "get_df", probe_get_df)
    with pytest.raises(ForbiddenDataAccess):
        controlled_gbbq_reader(str(layout["sentinel"]))
    assert decoded == [], f"vendor 解码被执行：{decoded}"


# ==========================================================================
# 正对照：允许的合成输入仍可读
# ==========================================================================
def test_allowed_synthetic_cache_is_readable(layout, monkeypatch):
    """正对照：未被列入的合成缓存必须可读（修复未造成无差别拒绝）。"""
    allowed_dir = layout["outside"] / "allowed_cache"
    allowed_dir.mkdir()
    allowed = allowed_dir / "gbbq.csv"
    allowed.write_text("code,datetime,category\n000001,20240101,1\n", encoding="utf-8")
    _protect(layout["sentinel"], monkeypatch)
    os.chdir(layout["outside"])
    tdx = TdxData(str(layout["source"] / "vipdoc"),
                  str(layout["source"] / "unused"), cache_dir=str(allowed_dir))
    frame = tdx._load_gbbq()
    assert len(frame) == 1


def test_relative_allowed_cache_uses_composition_root(layout, monkeypatch):
    """相对 cache_dir 按组合根解释：组合根下的允许缓存可读。"""
    _protect(layout["sentinel"], monkeypatch)
    allowed_dir = layout["source"] / "cache"
    allowed_dir.mkdir(parents=True)
    (allowed_dir / "gbbq.csv").write_text(
        "code,datetime,category\n000001,20240101,1\n", encoding="utf-8")
    os.chdir(layout["outside"])
    tdx = TdxData(str(layout["source"] / "vipdoc"),
                  str(layout["source"] / "unused"), cache_dir="cache")
    frame = tdx._load_gbbq()
    assert len(frame) == 1