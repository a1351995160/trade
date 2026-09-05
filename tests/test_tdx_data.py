"""通达信数据读取测试。"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chanlun_trader.tdx_data import TdxData, list_a_stocks, read_day_file


def _write_day(path: Path, rows):
    with open(path, "wb") as f:
        for date, o, h, l, c, vol, amount in rows:
            f.write(
                struct.pack(
                    "<IIIIIfI4s",
                    date,
                    int(o * 100),
                    int(h * 100),
                    int(l * 100),
                    int(c * 100),
                    float(amount),
                    int(vol),
                    b"\x00" * 4,
                )
            )


def test_read_day_file(tmp_path):
    p = tmp_path / "sh600000.day"
    _write_day(
        p,
        [
            (20210101, 10.0, 10.5, 9.8, 10.2, 1000, 1e6),
            (20210102, 10.2, 10.6, 10.0, 10.5, 1200, 1.1e6),
        ],
    )
    df = read_day_file(p)
    assert len(df) == 2
    assert df.iloc[0]["open"] == 10.0
    assert df.iloc[1]["close"] == 10.5
    assert df.iloc[0]["date"] == 20210101


def test_tdx_real_data_optional():
    vipdoc = Path(r"E:\new_tdx_mock\vipdoc")
    gbbq = Path(r"E:\new_tdx_mock\T0002\hq_cache\gbbq")
    if not vipdoc.exists() or not gbbq.exists():
        pytest.skip("本机没有通达信数据，跳过")
    tdx = TdxData(str(vipdoc), str(gbbq), str(Path(__file__).resolve().parents[1] / "data" / "cache"))
    stocks = list_a_stocks(tdx.vipdoc)
    assert len(stocks) > 0
    df = tdx.get_qfq_day(stocks[0]["code"], stocks[0]["market"])
    assert "qfq_close" in df.columns
    assert len(df) > 0


def test_read_lc5_and_aggregate(tmp_path):
    from chanlun_trader.tdx_data import aggregate_minutes, read_lc5_file

    p = tmp_path / "sh600000.lc5"

    def enc_date(y, m, d):
        return (y - 2004) * 2048 + m * 100 + d

    def enc_time(h, m):
        return h * 60 + m

    rows = []
    for i in range(8):
        total_min = 10 * 60 + i * 5
        h, m = total_min // 60, total_min % 60
        o = 10.0 + i * 0.1
        c = o + 0.05
        rows.append(
            struct.pack(
                "<HHfffffII",
                enc_date(2026, 1, 5),
                enc_time(h, m),
                float(o),
                float(o + 0.1),
                float(o - 0.05),
                float(c),
                1e6,
                1000,
                0,
            )
        )
    with open(p, "wb") as f:
        f.write(b"".join(rows))

    df = read_lc5_file(p)
    assert len(df) == 8
    agg = aggregate_minutes(df, period=30)
    assert len(agg) == 2
    assert agg.iloc[0]["open"] == pytest.approx(10.0)
    assert agg.iloc[0]["close"] == pytest.approx(10.55)
