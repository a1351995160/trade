"""访问边界防复发验收：真实 gbbq 必须在 open 之前被拒绝。

对应上一轮盘点的事故（`READ_ACCESS_LOG.json` X1）：整文件读取真实 gbbq，
物化至 datetime=20260930，超出 RESEARCH_END。本模块验证最小防复发措施。

证明要点（不依赖 mocked provider）：
- 拒绝发生在真实文件 open **之前**：用不存在的路径 + open 探针证明；
- 覆盖直接 `GbbqReader` 调用与间接 `TdxData._load_gbbq`；
- 正常价格路径（.day 有界读取）保持可用（正对照）；
- 临时目录里的合成 gbbq.csv 夹具不被误伤（否则会逼出假证据）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chanlun_trader.price_only_scope import (
    rebuild_frozen_denylist,  # noqa: E402
    ForbiddenDataAccess,
    assert_gbbq_read_disabled,
    guard_gbbq_path,
    is_forbidden_gbbq_path,
)

REAL_GBBQ = Path("E:/new_tdx_mock/T0002/hq_cache/gbbq")
REAL_GBBQ_MAP = Path("E:/new_tdx_mock/T0002/hq_cache/gbbq.map")
REAL_CACHE_GBBQ_CSV = Path("data/cache/gbbq.csv")


def test_real_gbbq_paths_are_classified_forbidden():
    """真实 gbbq 原件与全量缓存必须被判为禁止。"""
    assert is_forbidden_gbbq_path(REAL_GBBQ)
    assert is_forbidden_gbbq_path(REAL_GBBQ_MAP)
    assert is_forbidden_gbbq_path(REAL_CACHE_GBBQ_CSV)
    # 相对与绝对写法都要识别
    assert is_forbidden_gbbq_path("E:/new_tdx_mock/T0002/hq_cache/gbbq")
    assert is_forbidden_gbbq_path("E:\\new_tdx_mock\\T0002\\hq_cache\\gbbq")


def test_synthetic_fixture_paths_are_not_forbidden(tmp_path: Path):
    """临时目录里的合成 gbbq.csv 是正常夹具，不得被误伤。"""
    fixture = tmp_path / "gbbq.csv"
    fixture.write_text("code,datetime,category\n000001,20240101,1\n", encoding="utf-8")
    assert not is_forbidden_gbbq_path(fixture)
    assert not is_forbidden_gbbq_path(tmp_path / "gbbq")
    # 无关文件名也不禁止
    assert not is_forbidden_gbbq_path(REAL_GBBQ.parent / "tdxhy.cfg")


def test_guard_rejects_before_real_open(monkeypatch):
    """拒绝必须发生在真实文件 open 之前（用 open 探针证明）。"""
    opened: list[str] = []
    real_open = open

    def probe_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", probe_open)
    with pytest.raises(ForbiddenDataAccess) as excinfo:
        guard_gbbq_path(REAL_GBBQ, label="test")
    assert "FORBIDDEN_GBBQ_ACCESS" in str(excinfo.value)
    assert opened == [], f"守卫在 open 之后才拒绝：{opened}"


def test_load_gbbq_rejects_real_path_without_reading(tmp_path: Path, monkeypatch):
    """TdxData._load_gbbq 走真实 gbbq 路径时，必须在读取前拒绝。"""
    from chanlun_trader.tdx_data import TdxData

    opened: list[str] = []
    real_open = open

    def probe_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", probe_open)
    tdx = TdxData(str(tmp_path / "vipdoc"), str(REAL_GBBQ), cache_dir=str(tmp_path / "cache"))
    with pytest.raises(ForbiddenDataAccess):
        tdx._load_gbbq()
    assert opened == [], f"_load_gbbq 在拒绝前已打开文件：{opened}"


def test_load_gbbq_rejects_real_cache_csv(tmp_path: Path):
    """全量缓存 gbbq.csv 同样禁止（它是整文件物化的副本）。"""
    from chanlun_trader.tdx_data import TdxData

    tdx = TdxData(str(tmp_path / "vipdoc"), str(tmp_path / "gbbq"),
                  cache_dir="data/cache")
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
