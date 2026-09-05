"""缠论核心模块单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chanlun_trader.chan import add_macd, analyze_stock, detect_bi, detect_fx, merge_containment


def make_zigzag_df(n: int = 60) -> pd.DataFrame:
    """构造无包含关系的锯齿 K 线，用于稳定触发分型和笔。"""
    closes = []
    base = 10.0
    amp = 1.0
    direction = 1
    for i in range(n):
        if i % 7 == 0:
            direction *= -1
        base += direction * 0.35
        closes.append(base)
    df = pd.DataFrame(
        {
            "date": [20210101 + i for i in range(n)],
            "open": closes,
            "high": [c + 0.3 for c in closes],
            "low": [c - 0.3 for c in closes],
            "close": closes,
            "volume": [1000000 + i * 100 for i in range(n)],
            "amount": [1e7] * n,
            "qfq_close": closes,
            "qfq_open": closes,
            "qfq_high": [c + 0.3 for c in closes],
            "qfq_low": [c - 0.3 for c in closes],
        }
    )
    return df


def test_merge_containment_up_direction():
    df = pd.DataFrame(
        {
            "date": [20210101, 20210102, 20210103],
            "open": [10, 11, 11.2],
            "high": [10.5, 11.5, 11.2],
            "low": [9.5, 10.5, 10.6],
            "close": [10.2, 11.1, 11.0],
        }
    )
    merged = merge_containment(df)
    # 第2根包含第3根，向上处理：高点取高者，低点取高者
    assert len(merged) == 2
    assert merged.iloc[-1]["high"] == 11.5
    assert merged.iloc[-1]["low"] == 10.6


def test_detect_bi_count():
    df = make_zigzag_df(60)
    merged = merge_containment(df)
    fx_list = detect_fx(merged)
    bis = detect_bi(merged, fx_list, bi_gap=4)
    assert len(fx_list) >= 4
    assert len(bis) >= 2
    # 笔的方向必须交替
    for a, b in zip(bis, bis[1:]):
        assert a.direction != b.direction


def test_add_macd_columns():
    df = make_zigzag_df(40)
    out = add_macd(df, "close")
    assert {"dif", "dea", "macd"}.issubset(out.columns)
    assert len(out) == len(df)


def test_analyze_stock_runs():
    df = make_zigzag_df(80)
    out, merged, bis, signals, segments = analyze_stock(df, code="TEST", bi_gap=4)
    assert len(segments) >= 0
    assert len(merged) > 0
    assert len(bis) > 0
    assert all(s.code == "TEST" for s in signals)
    assert isinstance(signals, list)


def test_detect_segments_runs():
    from chanlun_trader.chan import detect_segments

    df = make_zigzag_df(90)
    merged = merge_containment(df)
    fx_list = detect_fx(merged)
    bis = detect_bi(merged, fx_list, bi_gap=4)
    segs = detect_segments(bis)
    assert isinstance(segs, list)
    # 锯齿行情下应能识别出至少一个简化线段
    assert len(segs) >= 1
    for s in segs:
        assert s.end_bi >= s.start_bi


def test_score_signals_type_order_and_top_per_day():
    from chanlun_trader.chan import BI, Signal, score_signals, top_per_day

    bis = [
        BI(0, 0, 1, "down", 20210101, 20210102, 20210103, 10, 9, 0, 1, -3.0),
        BI(1, 1, 2, "up", 20210102, 20210103, 20210104, 11, 9.5, 1, 2, 4.0),
        BI(2, 2, 3, "down", 20210103, 20210104, 20210105, 10.5, 9.8, 2, 3, -1.0),
    ]
    df = pd.DataFrame(
        {
            "date": [20210101, 20210102, 20210103, 20210104, 20210105],
            "close": [10, 10.5, 11, 10.8, 10.2],
            "volume": [1000, 1200, 1000, 800, 700],
            "amount": [1e7, 1e7, 1e7, 1e7, 1e7],
            "amount_ma20": [5e7, 5e7, 5e7, 5e7, 5e7],
        }
    )
    signals = [
        Signal("T", 20210104, "B1", "buy", 0, 9.8, 9.0),
        Signal("T", 20210104, "B2", "buy", 1, 9.8, 9.5),
        Signal("T", 20210104, "B3", "buy", 2, 9.8, 9.8),
    ]
    scored = score_signals(df, signals, bis)
    assert scored[0].score > scored[1].score > scored[2].score

    picked = top_per_day(scored, 2)
    assert len(picked) == 2
    assert picked[0].signal_type == "B1"
