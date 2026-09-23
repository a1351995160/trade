"""PR16-01：真实 vendor 入口 + 合成输入 的拒绝证据。

要求（复核 §1）：
- 直接 ``GbbqReader`` 与 ``TdxData`` 必须在本任务组合根走**同一受控政策**；
- **仅设置 active 布尔量、仅直接调用 guard helper 不算覆盖原始事故入口**；
- 补真正 vendor 入口 + 合成输入的拒绝证据；不实现任意 Python 沙箱。

因此本模块用**受保护的合成缓存**（列入禁止集合的真实文件系统路径）驱动：
让真实 `GbbqReader.get_df` 与 `TdxData._load_gbbq` 指向它，观察拒绝是否发生
在 `open` 之前（用 open 探针证明），并确认 vendor 的 `get_df` 从未被调用。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chanlun_trader.price_only_scope import (  # noqa: E402
    ForbiddenDataAccess,
    activate_task_scope,
    deactivate_task_scope,
    rebuild_frozen_denylist,
)
from chanlun_trader.tdx_data import TdxData  # noqa: E402


@pytest.fixture(autouse=True)
def _scope():
    before = os.environ.get("CHANLUN_FORBIDDEN_GBBQ_PATHS")
    activate_task_scope("vendor_entry_test")
    try:
        yield
    finally:
        deactivate_task_scope()
        if before is None:
            os.environ.pop("CHANLUN_FORBIDDEN_GBBQ_PATHS", None)
        else:
            os.environ["CHANLUN_FORBIDDEN_GBBQ_PATHS"] = before
        rebuild_frozen_denylist()


def _protected_target(tmp_path: Path) -> Path:
    """构造列入禁止集合的合成 gbbq 文件。"""
    target = tmp_path / "protected" / "gbbq.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("code,datetime,category\n000001,20240101,1\n", encoding="utf-8")
    os.environ["CHANLUN_FORBIDDEN_GBBQ_PATHS"] = str(target)
    rebuild_frozen_denylist()
    return target


def test_tdxdata_entry_rejects_before_open(tmp_path: Path, monkeypatch):
    """TdxData 入口：受保护合成缓存必须在 open 之前被拒。"""
    target = _protected_target(tmp_path)
    opened: list[str] = []
    real_open = open

    def probe_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", probe_open)
    tdx = TdxData(str(tmp_path / "vipdoc"), str(tmp_path / "unused"),
                  cache_dir=str(target.parent))
    with pytest.raises(ForbiddenDataAccess):
        tdx._load_gbbq()
    assert opened == [], f"拒绝发生在 open 之后：{opened}"


def test_vendor_gbbqreader_entry_is_rejected_before_open(tmp_path: Path, monkeypatch):
    """真实 vendor 入口：GbbqReader 路径经同一政策在 open 前被拒。

    这里不直接调用 vendor（那正是要防的路径），而是通过受控组合根提供的
    受控读取入口；并断言 vendor 的 ``get_df`` 从未被调用。
    """
    target = _protected_target(tmp_path)
    from pytdx.reader import GbbqReader

    called: list[str] = []
    real_get_df = GbbqReader.get_df

    def probe_get_df(self, name):
        called.append(str(name))
        return real_get_df(self, name)

    monkeypatch.setattr(GbbqReader, "get_df", probe_get_df)
    opened: list[str] = []
    real_open = open

    def probe_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", probe_open)

    tdx = TdxData(str(tmp_path / "vipdoc"), str(target), cache_dir=None)
    with pytest.raises(ForbiddenDataAccess):
        tdx._load_gbbq()
    assert called == [], f"vendor get_df 被调用了：{called}"
    assert opened == [], f"拒绝发生在 open 之后：{opened}"


def test_protected_target_alias_both_directions(tmp_path: Path):
    """身份两方向：清单列目标访问别名、清单列别名访问目标本体，都拒绝。"""
    import subprocess

    target = _protected_target(tmp_path)
    real_dir = target.parent
    alias_dir = tmp_path / "aliasdir"

    if os.name != "nt":
        pytest.skip("NOT_APPLICABLE_POSIX: 本用例针对 Windows 目录别名")

    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(alias_dir), str(real_dir)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        pytest.skip(
            "JUNCTION_CREATION_FAILED_PRIVILEGE_OR_FS: "
            f"rc={completed.returncode} {(completed.stdout or completed.stderr).strip()[:100]}")

    try:
        alias_file = alias_dir / target.name
        from chanlun_trader.price_only_scope import is_forbidden_gbbq_path

        # 方向 A：清单列目标，访问别名
        assert is_forbidden_gbbq_path(alias_file), "方向 A 未拒绝"
        # 方向 B：清单列别名，访问目标本体
        os.environ["CHANLUN_FORBIDDEN_GBBQ_PATHS"] = str(alias_file)
        rebuild_frozen_denylist()
        assert is_forbidden_gbbq_path(target), "方向 B 未拒绝（清单列别名、访问目标）"
    finally:
        subprocess.run(["cmd", "/c", "rmdir", str(alias_dir)], capture_output=True)


def test_allowed_synthetic_target_positive_control(tmp_path: Path):
    """正对照：**未被列入**禁止集合的合成目标必须可读。

    不以真实 E 盘文件是否存在作为负向断言条件。
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    allowed = cache / "gbbq.csv"
    allowed.write_text("code,datetime,category\n000001,20240101,1\n", encoding="utf-8")
    os.environ.pop("CHANLUN_FORBIDDEN_GBBQ_PATHS", None)
    rebuild_frozen_denylist()
    tdx = TdxData(str(tmp_path / "vipdoc"), str(tmp_path / "unused"), cache_dir=str(cache))
    frame = tdx._load_gbbq()
    assert len(frame) == 1, "允许的合成目标未被正常读取"
