"""访问边界防复发验收：受保护的合成 gbbq 必须在 open 之前被拒绝。

对应上一轮盘点的事故（`READ_ACCESS_LOG.json` X1）：整文件读取真实 gbbq，
物化至 datetime=20260930，超出 RESEARCH_END。本模块验证最小防复发措施。

证明要点（不依赖 mocked provider）：
- 拒绝发生在合成文件 open **之前**：用临时哨兵 + open 探针证明；
- 覆盖 guard helper 与 `TdxData._load_gbbq`；
- 未受保护的合成 gbbq 路径保持可读（正对照）；
- 临时目录里的合成 gbbq.csv 夹具不被误伤（否则会逼出假证据）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chanlun_trader.price_only_scope import (
    ForbiddenDataAccess,
    activate_task_scope,
    assert_gbbq_read_disabled,
    guard_gbbq_path,
    is_forbidden_gbbq_path,
    rebuild_frozen_denylist,
    restore_task_scope,
    snapshot_task_scope,
)


@pytest.fixture(autouse=True)
def _scope():
    """测试独立激活访问限制，并在退出时恢复外层完整政策。"""
    snapshot = snapshot_task_scope()
    activate_task_scope("gbbq_access_guard_test")
    try:
        yield
    finally:
        restore_task_scope(snapshot)


def _protected_target(tmp_path: Path, monkeypatch, name: str = "gbbq") -> Path:
    target = tmp_path / "protected" / name
    target.parent.mkdir(exist_ok=True)
    target.write_text("code,datetime,category\n000001,20240101,1\n", encoding="utf-8")
    monkeypatch.setenv("CHANLUN_FORBIDDEN_GBBQ_PATHS", str(target))
    rebuild_frozen_denylist()
    return target


def test_protected_synthetic_paths_are_classified_forbidden(tmp_path: Path, monkeypatch):
    """配置保护根下的合成原件、映射与缓存必须被判为禁止。"""
    vendor_root = tmp_path / "vendor" / "hq_cache"
    cache_root = tmp_path / "cache"
    vendor_root.mkdir(parents=True)
    cache_root.mkdir()
    monkeypatch.setattr("chanlun_trader.price_only_scope.REAL_GBBQ_ROOT", vendor_root)
    monkeypatch.setattr("chanlun_trader.price_only_scope.REAL_CACHE_ROOT", cache_root)
    paths = [vendor_root / "gbbq", vendor_root / "gbbq.map", cache_root / "gbbq.csv"]
    for path in paths:
        path.write_text("synthetic", encoding="utf-8")
    rebuild_frozen_denylist()
    for path in paths:
        assert is_forbidden_gbbq_path(path)
        assert is_forbidden_gbbq_path(str(path))


def test_synthetic_fixture_paths_are_not_forbidden(tmp_path: Path):
    """临时目录里的合成 gbbq.csv 是正常夹具，不得被误伤。"""
    fixture = tmp_path / "gbbq.csv"
    fixture.write_text("code,datetime,category\n000001,20240101,1\n", encoding="utf-8")
    assert not is_forbidden_gbbq_path(fixture)
    assert not is_forbidden_gbbq_path(tmp_path / "gbbq")
    # 无关文件名也不禁止
    assert not is_forbidden_gbbq_path(tmp_path / "tdxhy.cfg")


def test_guard_rejects_before_synthetic_open(tmp_path: Path, monkeypatch):
    """拒绝必须发生在合成文件 open 之前（用 open 探针证明）。"""
    target = _protected_target(tmp_path, monkeypatch)
    opened: list[str] = []

    def probe_open(file, *args, **kwargs):
        opened.append(str(file))
        raise AssertionError(f"守卫前调用 open：{file}")

    monkeypatch.setattr("builtins.open", probe_open)
    with pytest.raises(ForbiddenDataAccess) as excinfo:
        guard_gbbq_path(target, label="test")
    assert "FORBIDDEN_GBBQ_ACCESS" in str(excinfo.value)
    assert opened == [], f"守卫在 open 之后才拒绝：{opened}"


def test_load_gbbq_rejects_synthetic_path_without_reading(tmp_path: Path, monkeypatch):
    """TdxData._load_gbbq 走受保护合成路径时，必须在读取前拒绝。"""
    from chanlun_trader.tdx_data import TdxData

    target = _protected_target(tmp_path, monkeypatch)
    opened: list[str] = []

    def probe_open(file, *args, **kwargs):
        opened.append(str(file))
        raise AssertionError(f"守卫前调用 open：{file}")

    monkeypatch.setattr("builtins.open", probe_open)
    tdx = TdxData(str(tmp_path / "vipdoc"), str(target), cache_dir=str(tmp_path / "cache"))
    with pytest.raises(ForbiddenDataAccess):
        tdx._load_gbbq()
    assert opened == [], f"_load_gbbq 在拒绝前已打开文件：{opened}"


def test_load_gbbq_rejects_protected_cache_csv(tmp_path: Path, monkeypatch):
    """显式保护的合成缓存 gbbq.csv 同样在读取前禁止。"""
    from chanlun_trader.tdx_data import TdxData

    cache_file = _protected_target(tmp_path, monkeypatch, "gbbq.csv")
    tdx = TdxData(str(tmp_path / "vipdoc"), str(tmp_path / "gbbq"),
                  cache_dir=str(cache_file.parent))
    with pytest.raises(ForbiddenDataAccess) as excinfo:
        tdx._load_gbbq()
    assert "gbbq_cache" in str(excinfo.value)


def test_env_override_is_refused(monkeypatch):
    """不接受通过环境变量放开 gbbq 读取。"""
    monkeypatch.setenv("CHANLUN_ALLOW_GBBQ_READ", "1")
    with pytest.raises(ForbiddenDataAccess) as excinfo:
        assert_gbbq_read_disabled()
    assert "GBBQ_READ_NOT_PERMITTED_IN_THIS_TASK" in str(excinfo.value)


def test_normal_price_path_still_works(tmp_path: Path):
    """正对照：正常价格路径（合成夹具）不受守卫影响。"""
    from chanlun_trader.tdx_data import TdxData

    fixture = tmp_path / "gbbq.csv"
    fixture.write_text("code,datetime,category\n000001,20240101,1\n", encoding="utf-8")
    tdx = TdxData(str(tmp_path / "vipdoc"), str(fixture), cache_dir=str(tmp_path))
    frame = tdx._load_gbbq()
    assert len(frame) == 1
    assert frame.iloc[0]["code"] == "000001"


def test_extra_forbidden_paths_env(tmp_path: Path, monkeypatch):
    """显式清单可追加禁止路径（用于 CI 中的等价真实路径）。"""
    decoy = tmp_path / "gbbq"
    decoy.write_bytes(b"")
    assert not is_forbidden_gbbq_path(decoy)
    monkeypatch.setenv("CHANLUN_FORBIDDEN_GBBQ_PATHS", str(decoy))
    rebuild_frozen_denylist()
    assert is_forbidden_gbbq_path(decoy)
    with pytest.raises(ForbiddenDataAccess):
        guard_gbbq_path(decoy)
