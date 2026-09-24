"""PR16-01：受保护合成输入身份必须在**已有入口**真正被阻断。

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
    activate_task_scope,
    restore_task_scope,
    snapshot_task_scope,
)
from chanlun_trader.tdx_data import TdxData  # noqa: E402


@pytest.fixture(autouse=True)
def _scope():
    """在每个测试中激活访问限制，退出时恢复外层完整政策。"""
    snapshot = snapshot_task_scope()
    activate_task_scope("gbbq_identity_bypass_test")
    try:
        yield
    finally:
        restore_task_scope(snapshot)


def _protected_target(tmp_path: Path, monkeypatch, name: str = "gbbq.csv") -> Path:
    target = tmp_path / "protected" / name
    target.parent.mkdir(exist_ok=True)
    target.write_text("code,datetime,category\n000001,20240101,1\n", encoding="utf-8")
    monkeypatch.setenv("CHANLUN_FORBIDDEN_GBBQ_PATHS", str(target))
    rebuild_frozen_denylist()
    return target


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
def test_relative_and_absolute_cache_path_are_both_rejected(tmp_path: Path, monkeypatch):
    """同一合成缓存的相对与绝对写法必须一致拒绝。"""
    monkeypatch.setattr("chanlun_trader.price_only_scope.COMPOSITION_ROOT", tmp_path)
    absolute = str(_protected_target(tmp_path, monkeypatch))
    relative = "protected/gbbq.csv"
    assert is_forbidden_gbbq_path(relative), "相对写法未被拒"
    assert is_forbidden_gbbq_path(absolute), "绝对写法未被拒（绕过）"
    assert is_forbidden_gbbq_path(Path(absolute)), "Path 对象写法未被拒"


def test_entry_blocks_absolute_cache_path(tmp_path: Path, monkeypatch):
    """实际入口：绝对路径的合成缓存必须在打开前被拒。"""
    target = _protected_target(tmp_path, monkeypatch)
    tdx = TdxData(str(tmp_path / "vipdoc"), str(tmp_path / "gbbq"),
                  cache_dir=str(target.parent))
    with pytest.raises(ForbiddenDataAccess):
        tdx._load_gbbq()


# ==========================================================================
# 2) .. 与符号链接别名
# ==========================================================================
def test_parent_traversal_alias_is_rejected(tmp_path: Path, monkeypatch):
    """合成缓存经 ``..`` 与 ``.`` 形成的别名必须被拒。"""
    monkeypatch.setattr("chanlun_trader.price_only_scope.COMPOSITION_ROOT", tmp_path)
    _protected_target(tmp_path, monkeypatch)
    assert is_forbidden_gbbq_path("scratch/../protected/gbbq.csv")
    assert is_forbidden_gbbq_path("a/b/../../protected/gbbq.csv")
    assert is_forbidden_gbbq_path("./protected/./gbbq.csv")


@pytest.mark.skipif(not SYMLINK_OK, reason="本机不支持符号链接")
def test_symlink_alias_to_forbidden_cache_is_rejected(tmp_path: Path, monkeypatch):
    """符号链接别名指向禁止目标时必须被拒（经身份解析）。"""
    target = tmp_path / "protected" / "gbbq.csv"
    target.parent.mkdir()
    target.write_text("code,datetime,category\n", encoding="utf-8")
    monkeypatch.setenv("CHANLUN_FORBIDDEN_GBBQ_PATHS", str(target))
    rebuild_frozen_denylist()
    link = tmp_path / "alias.csv"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("本机不支持符号链接")
    assert is_forbidden_gbbq_path(link), "符号链接别名未被拒"


def test_junction_alias_in_temp_dir(tmp_path: Path, monkeypatch):
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
        # 将合成目标列入清单后，同一个 junction 别名必须被拒。
        monkeypatch.setenv("CHANLUN_FORBIDDEN_GBBQ_PATHS", str(target / "gbbq.csv"))
        rebuild_frozen_denylist()
        assert is_forbidden_gbbq_path(aliased), "受保护合成目标经 junction 别名未被拒"
        tdx = TdxData(str(tmp_path / "vipdoc"), str(aliased))
        with pytest.raises(ForbiddenDataAccess):
            tdx._load_gbbq()
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


def test_explicit_list_accepts_relative_and_absolute(tmp_path: Path, monkeypatch):
    """清单项的相对与绝对写法都应生效。"""
    monkeypatch.setattr("chanlun_trader.price_only_scope.COMPOSITION_ROOT", tmp_path)
    monkeypatch.setenv("CHANLUN_FORBIDDEN_GBBQ_PATHS", "cache/archived-events.dat")
    rebuild_frozen_denylist()
    assert is_forbidden_gbbq_path("cache/archived-events.dat")
    assert is_forbidden_gbbq_path(tmp_path / "cache" / "archived-events.dat")


# ==========================================================================
# 5) Windows 路径语义
# ==========================================================================
@pytest.mark.parametrize("raw", [
    "\\\\?\\Z:\\synthetic_scope\\hq_cache\\gbbq",
    "\\\\.\\Z:\\synthetic_scope\\hq_cache\\gbbq",
    "Z:/synthetic_scope/sub/../hq_cache/gbbq",
    "Z:/synthetic_scope/hq_cache/./gbbq",
    "z:/SYNTHETIC_SCOPE/HQ_CACHE/GBBQ",
    "Z:\\synthetic_scope\\hq_cache\\gbbq",
])
def test_windows_path_forms_are_rejected(raw, monkeypatch):
    """合成盘符路径的扩展形式、父目录别名、大小写与分隔符变体都必须被拒。"""
    monkeypatch.setenv("CHANLUN_FORBIDDEN_GBBQ_PATHS", "Z:/synthetic_scope/hq_cache/gbbq")
    rebuild_frozen_denylist()
    assert is_forbidden_gbbq_path(raw), f"Windows 路径形式未被拒：{raw}"


def test_normalise_does_not_treat_drive_path_as_relative():
    """盘符路径不得被当作 POSIX 相对路径（此前在 Linux 上失效）。"""
    normalised = _normalise("Z:/synthetic_scope/hq_cache/gbbq")
    assert normalised is not None
    assert normalised.startswith("z:/"), f"盘符被丢失：{normalised}"
    # 反斜杠与扩展前缀规范化后一致
    assert _normalise("\\\\?\\Z:\\x\\gbbq") == _normalise("Z:/x/gbbq")


# ==========================================================================
# 实际调用入口（不只 guard helper）
# ==========================================================================
def test_entry_blocks_synthetic_gbbq_before_open(tmp_path: Path, monkeypatch):
    """实际入口：受保护合成 gbbq 必须在 open 之前被拒。"""
    target = _protected_target(tmp_path, monkeypatch, "gbbq")
    opened: list[str] = []

    def probe_open(file, *args, **kwargs):
        opened.append(str(file))
        raise AssertionError(f"守卫前调用 open：{file}")

    monkeypatch.setattr("builtins.open", probe_open)
    tdx = TdxData(str(tmp_path / "vipdoc"), str(target), cache_dir=str(tmp_path / "cache"))
    with pytest.raises(ForbiddenDataAccess):
        tdx._load_gbbq()
    assert opened == [], f"拒绝发生在 open 之后：{opened}"


def test_entry_blocks_relative_cache_before_open(tmp_path: Path, monkeypatch):
    """实际入口：相对写法的缓存路径同样在 open 之前被拒。"""
    monkeypatch.setattr("chanlun_trader.price_only_scope.COMPOSITION_ROOT", tmp_path)
    _protected_target(tmp_path, monkeypatch)
    opened: list[str] = []

    def probe_open(file, *args, **kwargs):
        opened.append(str(file))
        raise AssertionError(f"守卫前调用 open：{file}")

    monkeypatch.setattr("builtins.open", probe_open)
    tdx = TdxData(str(tmp_path / "vipdoc"), str(tmp_path / "gbbq"), cache_dir="protected")
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
