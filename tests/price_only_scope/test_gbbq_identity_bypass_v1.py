"""PR16-01：真实输入身份必须在**已有入口**真正被阻断。

对应复核的五类绕过：
1. 相对 ``data/cache/gbbq.csv`` 被拒，而同一缓存的**绝对路径**被打开；
2. ``scratch/../data/cache/gbbq.csv`` 与符号链接别名被打开；
3. 原缓存已放入显式禁止清单，链接别名仍被打开；
4. 显式禁止 ``archived-events.dat`` 因 basename 早退失效；
5. Windows 父目录别名/扩展路径写法在字符串检测中失效。

要求：
- 只用**合成哨兵**，禁止用真实 gbbq 做 red/green；
- 直接测试必须走**实际调用入口**（``TdxData._load_gbbq``），不能只调 guard helper；
- 保持临时合成 fixture 可读（正对照）；
- 主机不支持的别名（如 junction）明确标注测试范围，不伪称跨平台全部执行。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chanlun_trader.price_only_scope import (
    rebuild_frozen_denylist,  # noqa: E402
    ForbiddenDataAccess,
    _normalise,
    is_forbidden_gbbq_path,
)
from chanlun_trader.tdx_data import TdxData  # noqa: E402

REAL_GBBQ = "E:/new_tdx_mock/T0002/hq_cache/gbbq"
PROJECT_CACHE_ABS = str((Path.cwd() / "data" / "cache").resolve())


def _symlink_supported() -> bool:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "t"
        target.write_text("x", encoding="utf-8")
        link = Path(td) / "l"
        try:
            os.symlink(target, link)
            return True
        except (OSError, NotImplementedError):
            return False


SYMLINK_OK = _symlink_supported()


# ==========================================================================
# 1) 相对 vs 绝对：同一文件必须一致拒绝
# ==========================================================================
def test_relative_and_absolute_cache_path_are_both_rejected():
    """同一缓存的相对与绝对写法必须**一致拒绝**（此前绝对路径被放行）。"""
    relative = "data/cache/gbbq.csv"
    absolute = PROJECT_CACHE_ABS + "/gbbq.csv"
    assert is_forbidden_gbbq_path(relative), "相对写法未被拒"
    assert is_forbidden_gbbq_path(absolute), "绝对写法未被拒（绕过）"
    assert is_forbidden_gbbq_path(Path(absolute)), "Path 对象写法未被拒"


def test_entry_blocks_absolute_cache_path(tmp_path: Path):
    """实际入口：绝对路径的项目缓存必须在打开前被拒。"""
    tdx = TdxData(str(tmp_path / "vipdoc"), str(tmp_path / "gbbq"),
                  cache_dir=PROJECT_CACHE_ABS)
    with pytest.raises(ForbiddenDataAccess):
        tdx._load_gbbq()


# ==========================================================================
# 2) .. 与符号链接别名
# ==========================================================================
def test_parent_traversal_alias_is_rejected():
    """``scratch/../data/cache/gbbq.csv`` 必须被拒（此前被放行）。"""
    assert is_forbidden_gbbq_path("scratch/../data/cache/gbbq.csv")
    assert is_forbidden_gbbq_path("a/b/../../data/cache/gbbq.csv")
    assert is_forbidden_gbbq_path("./data/./cache/../cache/gbbq.csv")


@pytest.mark.skipif(not SYMLINK_OK, reason="本机不支持符号链接")
def test_symlink_alias_to_forbidden_cache_is_rejected(tmp_path: Path):
    """符号链接别名指向禁止目标时必须被拒（经身份解析）。"""
    real_dir = Path.cwd() / "data" / "cache"
    real_dir.mkdir(parents=True, exist_ok=True)
    sentinel = real_dir / "gbbq.csv"
    created = not sentinel.exists()
    if created:
        sentinel.write_text("code,datetime,category\n", encoding="utf-8")
    link = tmp_path / "alias.csv"
    try:
        os.symlink(sentinel, link)
    except (OSError, NotImplementedError):
        pytest.skip("本机不支持符号链接")
    try:
        assert is_forbidden_gbbq_path(link), "符号链接别名未被拒"
    finally:
        if created and sentinel.exists():
            sentinel.unlink()


def test_junction_alias_in_temp_dir(tmp_path: Path):
    """Windows 上在临时目录**原生创建 junction** 并检查入口拦截。

    任务要求：不能无条件 skip 并声称尝试失败；只有**实际权限或文件系统错误**
    才记录失败并 skip。POSIX 以"不适用"单列。
    """
    if os.name != "nt":
        pytest.skip("NOT_APPLICABLE_POSIX: junction 是 Windows 专有目录别名形式")

    junction = tmp_path / "junc"
    target = tmp_path / "target"
    target.mkdir()
    (target / "gbbq.csv").write_text("code,datetime,category\n", encoding="utf-8")

    # 原生创建：mklink /J（无需管理员，不同于符号链接）
    import subprocess

    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        pytest.skip(
            "JUNCTION_CREATION_FAILED_PRIVILEGE_OR_FS: "
            f"rc={completed.returncode} {completed.stderr.strip()[:120]}")

    try:
        # junction 是目录别名：其下的 gbbq.csv 与目标同一身份
        aliased = junction / "gbbq.csv"
        assert aliased.exists(), "junction 创建后目标不可见"
        # 该临时目标不在禁止根下，故应可读（正对照：junction 本身不触发拒绝）
        assert not is_forbidden_gbbq_path(aliased), "临时目录 junction 被误拒"
        # 真实 gbbq 经 junction 别名访问时必须被拒（身份解析生效）
        real = Path("E:/new_tdx_mock/T0002/hq_cache/gbbq")
        if real.exists():
            real_junction = tmp_path / "real_junc"
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(real_junction), str(real.parent)],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            if completed.returncode == 0:
                aliased_real = real_junction / "gbbq"
                assert is_forbidden_gbbq_path(aliased_real), \
                    "真实 gbbq 经 junction 别名未被拒"
    finally:
        try:
            os.rmdir(junction)
        except OSError:
            pass


# ==========================================================================
# 3) 显式清单 + 链接别名
# ==========================================================================
def test_explicit_list_matches_symlink_alias(tmp_path: Path, monkeypatch):
    """原目标已列入显式清单时，其符号链接别名同样被拒。"""
    target = tmp_path / "archived-events.dat"
    target.write_text("x", encoding="utf-8")
    monkeypatch.setenv("CHANLUN_FORBIDDEN_GBBQ_PATHS", str(target))
    rebuild_frozen_denylist()
    assert is_forbidden_gbbq_path(target), "清单目标本身未被拒"
    if SYMLINK_OK:
        link = tmp_path / "alias.dat"
        try:
            os.symlink(target, link)
            assert is_forbidden_gbbq_path(link), "清单目标的符号链接别名未被拒"
        except (OSError, NotImplementedError):
            pass


# ==========================================================================
# 4) 显式清单不受 basename 白名单限制
# ==========================================================================
def test_explicit_list_is_not_limited_by_basename_whitelist(tmp_path: Path, monkeypatch):
    """显式禁止的任意文件名必须生效（此前因 basename 早退失效）。"""
    for name in ("archived-events.dat", "events.bin", "snapshot.parquet"):
        target = tmp_path / name
        target.write_text("x", encoding="utf-8")
        monkeypatch.setenv("CHANLUN_FORBIDDEN_GBBQ_PATHS", str(target))
        rebuild_frozen_denylist()
        assert is_forbidden_gbbq_path(target), f"显式清单对 {name} 失效"


def test_explicit_list_accepts_relative_and_absolute(monkeypatch):
    """清单项的相对与绝对写法都应生效。"""
    monkeypatch.setenv("CHANLUN_FORBIDDEN_GBBQ_PATHS", "data/cache/archived-events.dat")
    rebuild_frozen_denylist()
    assert is_forbidden_gbbq_path("data/cache/archived-events.dat")
    assert is_forbidden_gbbq_path(str((Path.cwd() / "data/cache/archived-events.dat").resolve()))


# ==========================================================================
# 5) Windows 路径语义
# ==========================================================================
@pytest.mark.parametrize("raw", [
    "\\\\?\\E:\\new_tdx_mock\\T0002\\hq_cache\\gbbq",
    "\\\\.\\E:\\new_tdx_mock\\T0002\\hq_cache\\gbbq",
    "E:/new_tdx_mock/T0002/../T0002/hq_cache/gbbq",
    "E:/new_tdx_mock/T0002/hq_cache/./gbbq",
    "e:/NEW_TDX_MOCK/t0002/HQ_CACHE/GBBQ",
    "E:\\new_tdx_mock\\T0002\\hq_cache\\gbbq",
])
def test_windows_path_forms_are_rejected(raw):
    """Windows 扩展路径、父目录别名、大小写与分隔符变体都必须被拒。"""
    assert is_forbidden_gbbq_path(raw), f"Windows 路径形式未被拒：{raw}"


def test_normalise_does_not_treat_drive_path_as_relative():
    """盘符路径不得被当作 POSIX 相对路径（此前在 Linux 上失效）。"""
    normalised = _normalise("E:/new_tdx_mock/T0002/hq_cache/gbbq")
    assert normalised is not None
    assert normalised.startswith("e:/"), f"盘符被丢失：{normalised}"
    # 反斜杠与扩展前缀规范化后一致
    assert _normalise("\\\\?\\E:\\x\\gbbq") == _normalise("E:/x/gbbq")


# ==========================================================================
# 实际调用入口（不只 guard helper）
# ==========================================================================
def test_entry_blocks_real_gbbq_before_open(tmp_path: Path, monkeypatch):
    """实际入口：真实 gbbq 原件必须在 open 之前被拒（open 探针证明）。"""
    opened: list[str] = []
    real_open = open

    def probe_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", probe_open)
    tdx = TdxData(str(tmp_path / "vipdoc"), REAL_GBBQ, cache_dir=str(tmp_path / "cache"))
    with pytest.raises(ForbiddenDataAccess):
        tdx._load_gbbq()
    assert opened == [], f"拒绝发生在 open 之后：{opened}"


def test_entry_blocks_relative_cache_before_open(tmp_path: Path, monkeypatch):
    """实际入口：相对写法的缓存路径同样在 open 之前被拒。"""
    opened: list[str] = []
    real_open = open

    def probe_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", probe_open)
    tdx = TdxData(str(tmp_path / "vipdoc"), str(tmp_path / "gbbq"), cache_dir="data/cache")
    with pytest.raises(ForbiddenDataAccess):
        tdx._load_gbbq()
    assert opened == [], f"拒绝发生在 open 之后：{opened}"


def test_synthetic_fixture_in_temp_cache_is_readable(tmp_path: Path):
    """正对照：临时目录里的合成 gbbq.csv 必须可读（不得误伤）。"""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "gbbq.csv").write_text("code,datetime,category\n000001,20240101,1\n",
                                     encoding="utf-8")
    tdx = TdxData(str(tmp_path / "vipdoc"), str(tmp_path / "gbbq"), cache_dir=str(cache))
    frame = tdx._load_gbbq()
    assert len(frame) == 1
    assert frame.iloc[0]["code"] == "000001"


def test_unparseable_path_fails_closed():
    """无法解析的路径形式必须 fail closed（拒绝），不得静默放行。"""
    assert is_forbidden_gbbq_path("") is True
    assert is_forbidden_gbbq_path(None) is True
