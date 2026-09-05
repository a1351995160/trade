"""回测引擎冒烟测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chanlun_trader.backtest import BacktestRunner
from chanlun_trader.config import load_config
from chanlun_trader.tdx_data import TdxData


def test_backtest_smoke():
    cfg = load_config()
    vipdoc = Path(cfg["tdx"]["vipdoc"])
    gbbq = Path(cfg["tdx"]["gbbq"])
    if not vipdoc.exists() or not gbbq.exists():
        pytest.skip("本机没有通达信数据，跳过")
    tdx = TdxData(str(vipdoc), str(gbbq), str(Path(__file__).resolve().parents[1] / "data" / "cache"))
    runner = BacktestRunner(tdx, cfg)
    result = runner.run(limit=5)
    assert "trades" in result
    assert "equity_curve" in result
    assert len(result["calendar"]) > 100
    assert result["cash"] >= 0
