"""QFQ PIT 正确性：合成可证性质（本任务下**实际执行**）。

PR16-02 要求把「本任务禁止真实访问」与「既有 QFQ 正确性验证」拆开：
可用**合成已知事件**证明的前缀/未来修改性质应保留实际执行测试；
真实本机数据集成测试独立明确标记不执行，不当成通过。

本模块用合成事件与合成行情证明 qfq 的 PIT 性质，不读真实 gbbq。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chanlun_trader.research.qfq import qfq_columns_asof  # noqa: E402


def _raw_frame(dates, closes) -> pd.DataFrame:
    return pd.DataFrame({
        "date": dates,
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": [1_000_000] * len(dates),
        "amount": [c * 1_000_000 for c in closes],
    })


def _events(code: str, rows) -> pd.DataFrame:
    """合成除权事件：category=1（分红送转），字段与 gbbq 输出同构。

    空事件也返回带完整列的 DataFrame（避免下游按列名取用时 KeyError）。
    """
    if not rows:
        return pd.DataFrame(columns=["code", "datetime", "category",
                                     "hongli_panqianliutong", "peigujia_qianzongguben",
                                     "songgu_qianzongguben", "peigu_houzongguben"])
    return pd.DataFrame([
        {"code": code, "datetime": dt, "category": 1,
         "hongli_panqianliutong": hongli, "peigujia_qianzongguben": 0.0,
         "songgu_qianzongguben": songgu, "peigu_houzongguben": 0.0}
        for dt, hongli, songgu in rows
    ])


def test_future_event_does_not_change_asof_qfq():
    """合成证明：as_of=t1 的 qfq 不得受 t2>t1 的未来事件影响。"""
    dates = [20240102, 20240103, 20240104, 20240105, 20240108, 20240109, 20240110]
    closes = [10.0, 10.5, 11.0, 10.8, 11.2, 11.5, 11.0]
    raw = _raw_frame(dates, closes)
    code = "000001"
    # t2 之后有一个未来事件（送转）
    events = _events(code, [(20240115, 0.0, 5.0)])
    t1 = 20240109

    asof = qfq_columns_asof(raw, events[events["datetime"] <= t1], t1)
    # 用「含未来事件」的同一批事件计算，但只取 t1 之前的结果
    with_future = qfq_columns_asof(raw, events, t1)
    a = asof[asof["date"] <= t1].reset_index(drop=True)
    b = with_future[with_future["date"] <= t1].reset_index(drop=True)
    np.testing.assert_allclose(a["qfq_close"].to_numpy(), b["qfq_close"].to_numpy(),
                               rtol=0, atol=1e-12)


def test_asof_qfq_is_sensitive_to_past_event_only():
    """合成证明：t1 之前的事件必须改变 qfq；t1 之后的不得改变。"""
    dates = [20240102, 20240103, 20240104, 20240105, 20240108, 20240109]
    closes = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0]
    raw = _raw_frame(dates, closes)
    code = "000001"
    t1 = 20240109

    no_event = qfq_columns_asof(raw, _events(code, []), t1)
    past_event = qfq_columns_asof(raw, _events(code, [(20240104, 0.0, 10.0)]), t1)
    future_event = qfq_columns_asof(raw, _events(code, [(20240115, 0.0, 10.0)]), t1)

    baseline = no_event["qfq_close"].to_numpy()
    assert not np.allclose(past_event["qfq_close"].to_numpy(), baseline, rtol=0, atol=1e-12), \
        "t1 之前的事件未改变 qfq"
    np.testing.assert_allclose(future_event["qfq_close"].to_numpy(), baseline,
                               rtol=0, atol=1e-12)


def test_appending_future_event_does_not_change_past_prefix():
    """合成证明：追加未来事件不得改变已完成的 as_of 前缀（逐值）。"""
    dates = [20240102, 20240103, 20240104, 20240105, 20240108, 20240109, 20240110]
    closes = [10.0, 10.2, 10.4, 10.1, 10.6, 10.9, 11.1]
    raw = _raw_frame(dates, closes)
    code = "000001"
    t1 = 20240109

    base = qfq_columns_asof(raw, _events(code, [(20240104, 0.0, 10.0)]), t1)
    extended = qfq_columns_asof(
        raw, _events(code, [(20240104, 0.0, 10.0), (20240115, 0.0, 5.0)]), t1)
    np.testing.assert_allclose(
        extended["qfq_close"].to_numpy(), base["qfq_close"].to_numpy(),
        rtol=0, atol=1e-12, err_msg="追加未来事件改变了 as_of 前缀")


def test_synthetic_qfq_prefix_is_identity_without_events():
    """无事件时 qfq 必须等于 RAW（正对照）。"""
    dates = [20240102, 20240103, 20240104]
    closes = [10.0, 10.5, 11.0]
    raw = _raw_frame(dates, closes)
    result = qfq_columns_asof(raw, _events("000001", []), 20240104)
    np.testing.assert_allclose(result["qfq_close"].to_numpy(), closes, rtol=0, atol=1e-12)
