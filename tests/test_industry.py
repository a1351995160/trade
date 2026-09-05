"""行业映射与每日行业去重测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chanlun_trader.chan import Signal, top_per_day
from chanlun_trader.industry import load_industry_map


def test_top_per_day_industry_dedup():
    industry_map = {"600000": "银行", "600001": "银行", "600002": "电子"}
    signals = [
        Signal("600000", 20210101, "B1", "buy", 0, 10, 9, score=5),
        Signal("600001", 20210101, "B2", "buy", 0, 10, 9, score=4),
        Signal("600002", 20210101, "B3", "buy", 0, 10, 9, score=3),
    ]
    picked = top_per_day(signals, 5, industry_map=industry_map, max_per_industry=1)
    assert len(picked) == 2
    assert picked[0].code == "600000"
    assert picked[1].code == "600002"


def test_load_industry_map_from_cfg():
    # 配置了 tdxhy.cfg 时，应能返回非空映射
    from chanlun_trader.config import load_config

    cfg = load_config()
    mapping = load_industry_map(cfg["tdx"])
    assert isinstance(mapping, dict)
    assert len(mapping) > 0
