"""读取链窗口约束合同回归（语义移植自 V5 已审阅修复）。

覆盖合同 C：实际 RAW/QFQ/基准 reader 调用链接入明确窗口。
用**合成二进制 .day + 合成公司行动**构造窗内/窗外哨兵，
验证有界路径不物化窗外记录、且不应用窗外公司行动。
**不使用真实无界对照来证明限窗**，不读取真实行情或封存期。

未物理限窗的公司行动源（TdxData._load_gbbq 仍整体读取）保持 OPEN，
不在本测试中声称已修好。
"""
import struct
import sys

import pandas as pd
import pytest

from src.chanlun_trader.tdx_data import TdxData

WIN_END = 20250731
IN_DATES = [20250728, 20250729, 20250730, 20250731]
OUT_DATES = [20250801, 20250804, 20250805]
IN_PRICE = 10.00
OUT_PRICE = 99.00


def _write_day(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = bytearray()
    for d, price in rows:
        p = int(round(price * 100))
        buf += struct.pack("<IIIIIfII", int(d), p, p, p, p,
                           float(p * 100), p * 100, 0)
    path.write_bytes(bytes(buf))


@pytest.fixture()
def synthetic_tdx(tmp_path):
    """合成 vipdoc + 合成公司行动（除权日在窗外）。"""
    vip = tmp_path / "vipdoc"
    _write_day(vip / "sh" / "lday" / "sh600900.day",
               [(d, IN_PRICE) for d in IN_DATES] + [(d, OUT_PRICE) for d in OUT_DATES])
    _write_day(vip / "sh" / "lday" / "sh000001.day",
               [(d, 3000.0) for d in IN_DATES + OUT_DATES])
    gbbq = pd.DataFrame([{
        "market": 1, "code": "600900", "datetime": 20250804, "category": 1,
        "hongli_panqianliutong": 0.0, "peigu_houzongguben": 0.0,
        "songgu_qianzongguben": 10.0, "peigujia_qianzongguben": 0.0,
    }])
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    gbbq.to_csv(cache / "gbbq.csv", index=False)
    return TdxData(str(vip), str(tmp_path / "gbbq.csv"), str(cache))


def test_get_day_bounded_excludes_out_of_window(synthetic_tdx):
    d = synthetic_tdx.get_day("600900", 1, start_date=0, end_date=WIN_END)
    assert int(d["date"].max()) == WIN_END
    assert not (d["date"] > WIN_END).any()
    assert not (d["close"] >= OUT_PRICE - 0.01).any()


def test_get_benchmark_bounded_excludes_out_of_window(synthetic_tdx):
    b = synthetic_tdx.get_benchmark("sh000001", start_date=0, end_date=WIN_END)
    assert int(b["date"].max()) == WIN_END
    assert not (b["date"] > WIN_END).any()


def test_get_qfq_day_bounded_excludes_out_of_window_rows(synthetic_tdx):
    q = synthetic_tdx.get_qfq_day("600900", 1, start_date=0, end_date=WIN_END)
    assert int(q["date"].max()) == WIN_END
    assert not (q["date"] > WIN_END).any()


def test_get_qfq_day_bounded_does_not_apply_out_of_window_corporate_action(synthetic_tdx):
    """窗外除权事件不得回改窗内价格（有界路径只应用 ex_date <= end 的事件）。"""
    q = synthetic_tdx.get_qfq_day("600900", 1, start_date=0, end_date=WIN_END)
    assert abs(float(q["qfq_close"].iloc[0]) - IN_PRICE) < 1e-9


def test_bounded_path_does_not_pollute_full_cache(synthetic_tdx):
    """有界路径不得写入全量缓存（避免把窗口切片当全量）。"""
    synthetic_tdx.get_qfq_day("600900", 1, start_date=0, end_date=WIN_END)
    assert synthetic_tdx._qfq_cache == {}


def test_unbounded_legacy_path_still_available_for_compatibility(synthetic_tdx):
    """无界兼容入口保留（研究/认证入口不得误用）。"""
    d = synthetic_tdx.get_day("600900", 1)
    assert int(d["date"].max()) > WIN_END