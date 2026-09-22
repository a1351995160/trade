"""BT_INDICATORS_V1 公式验收：手算值 + 独立 oracle + 边界与因果性。

不 mock 被测指标；oracle 为 tests/indicators/_oracle.py 的朴素逐元素实现。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from chanlun_trader.engine.conditions_v1 import (
    ConditionError,
    KdjParams,
    MacdParams,
    evaluate_kdj_conditions,
    evaluate_macd_conditions,
)
from chanlun_trader.engine.indicators_v1 import (
    IndicatorInputError,
    cross_down,
    cross_up,
    ema_recursive,
    kdj_v1,
    macd_v1,
    moving_average,
    segment_ids,
)

from tests.indicators._oracle import naive_cross_down, naive_cross_up, naive_kdj, naive_macd

TOL = 1e-9


def _days(count: int) -> list:
    """从 20250101 起按自然日生成 count 个严格递增的整数交易日键。"""
    start = pd.Timestamp("2025-01-01")
    return [int((start + pd.Timedelta(days=i)).strftime("%Y%m%d")) for i in range(count)]


def _series(values, days=None):
    values = [float(v) for v in values]
    days = days or _days(len(values))
    return pd.Series(values, index=pd.Index(days, name="date"))


# --------------------------------------------------------------------------
# 1. 手算短序列
# --------------------------------------------------------------------------
def test_macd_v1_hand_computed_short_sequence():
    close = _series([10, 11, 12, 13])
    frame = macd_v1(close, fast=2, slow=3, signal=2)
    # warmup = slow + signal - 1 = 4
    assert list(frame.ready) == [False, False, False, True]
    assert frame.value("dif").iloc[3] == pytest.approx(0.3935185185185185, abs=1e-12)
    assert frame.value("dea").iloc[3] == pytest.approx(0.3425925925925926, abs=1e-12)
    assert frame.value("hist").iloc[3] == pytest.approx(0.1018518518518519, abs=1e-12)
    # DIF 首值 = 0（两条 EMA 同初值）
    assert frame.value("dif").iloc[0] == pytest.approx(0.0, abs=TOL)
    assert frame.value("dea").iloc[0] == pytest.approx(0.0, abs=TOL)


def test_kdj_v1_hand_computed_short_sequence():
    high = _series([10, 11, 12, 13])
    low = _series([9, 10, 11, 12])
    close = _series([9.5, 10.5, 11.5, 12.5])
    frame = kdj_v1(high, low, close, n=3)
    assert list(frame.ready) == [False, False, True, True]
    assert frame.value("rsv").iloc[2] == pytest.approx(83.33333333333333, abs=1e-9)
    assert frame.value("k").iloc[2] == pytest.approx(61.11111111111111, abs=1e-9)
    assert frame.value("d").iloc[2] == pytest.approx(53.70370370370370, abs=1e-9)
    assert frame.value("j").iloc[2] == pytest.approx(75.92592592592592, abs=1e-9)
    assert frame.value("k").iloc[3] == pytest.approx(68.51851851851852, abs=1e-9)
    assert frame.value("d").iloc[3] == pytest.approx(58.64197530864197, abs=1e-9)
    assert frame.value("j").iloc[3] == pytest.approx(88.27160493827160, abs=1e-9)


# --------------------------------------------------------------------------
# 2. 与独立 oracle 逐值一致
# --------------------------------------------------------------------------
@pytest.mark.parametrize("shape", ["flat", "up", "down", "roundtrip"])
def test_macd_v1_matches_independent_oracle(shape):
    closes = _shape_closes(shape)
    frame = macd_v1(_series(closes))
    expected = naive_macd(closes)
    for name, key in (("dif", "dif"), ("dea", "dea"), ("hist", "hist")):
        np.testing.assert_allclose(frame.value(name).to_numpy(), expected[key], rtol=0, atol=1e-12)
    assert list(frame.ready) == expected["ready"]


@pytest.mark.parametrize("shape", ["flat", "up", "down", "roundtrip"])
def test_kdj_v1_matches_independent_oracle(shape):
    closes = _shape_closes(shape)
    highs = [c * 1.02 for c in closes]
    lows = [c * 0.98 for c in closes]
    frame = kdj_v1(_series(highs), _series(lows), _series(closes))
    expected = naive_kdj(highs, lows, closes)
    for name in ("rsv", "k", "d", "j"):
        np.testing.assert_allclose(
            frame.value(name).to_numpy(), _none_to_nan(expected[name]), rtol=0, atol=1e-12
        )
    assert list(frame.ready) == expected["ready"]


def _none_to_nan(values):
    return np.array([np.nan if v is None else float(v) for v in values], dtype=float)


def _shape_closes(shape: str) -> list:
    if shape == "flat":
        return [10.0] * 40
    if shape == "up":
        return [10.0 + 0.1 * i for i in range(40)]
    if shape == "down":
        return [14.0 - 0.1 * i for i in range(40)]
    out = []
    for i in range(40):
        out.append(10.0 + (1.5 if i % 8 < 4 else -1.5) * (1 + i / 40.0))
    return out


def test_ema_recursive_matches_pandas_adjust_false():
    values = [10.0, 11.0, 10.5, 12.0, 11.0, 13.0]
    ours = ema_recursive(values, 3)
    theirs = pd.Series(values).ewm(span=3, adjust=False).mean().to_numpy()
    np.testing.assert_allclose(ours, theirs, rtol=0, atol=1e-12)


def test_cross_helpers_match_oracle():
    left = [1.0, 2.0, 3.0, 2.0, 2.0, 4.0]
    right = [2.0, 2.0, 2.0, 2.0, 2.0, 2.0]
    assert list(cross_up(left, right)) == naive_cross_up(left, right)
    assert list(cross_down(left, right)) == naive_cross_down(left, right)


def test_moving_average_matches_manual_mean():
    values = [float(i) for i in range(1, 11)]
    frame = moving_average(_series(values), window=3)
    out = frame.value("ma3").to_numpy()
    assert np.isnan(out[0]) and np.isnan(out[1])
    assert out[2] == pytest.approx(2.0)
    assert out[9] == pytest.approx(9.0)


# --------------------------------------------------------------------------
# 3. 五类 MACD 条件分别成立、分别可证伪
# --------------------------------------------------------------------------
def _macd_case_frame():
    """构造覆盖"水下金叉 / 水上金叉 / 死叉 / 只有 DIF 在水上"的确定性序列。

    分段：长期下跌 → 反弹（水下金叉）→ 深跌 → 长涨（进入水上）→ 回踩 → 再涨
    （水上金叉）→ 下跌（死叉）。数值由固定乘数递推，可复现。
    """
    closes: list = []
    value = 20.0
    for _ in range(40):
        value *= 0.985
        closes.append(value)
    for _ in range(14):
        value *= 1.012
        closes.append(value)
    for _ in range(25):
        value *= 0.992
        closes.append(value)
    for _ in range(55):
        value *= 1.011
        closes.append(value)
    for _ in range(16):
        value *= 0.991
        closes.append(value)
    for _ in range(30):
        value *= 1.009
        closes.append(value)
    for _ in range(20):
        value *= 0.986
        closes.append(value)
    close = _series(closes)
    return close, macd_v1(close)


def test_macd_conditions_are_distinct_not_a_single_label():
    close, frame = _macd_case_frame()
    names = ["DIF_ABOVE_ZERO", "BOTH_LINES_ABOVE_ZERO", "GOLDEN_CROSS",
             "DEATH_CROSS", "ABOVE_ZERO_GOLDEN_CROSS"]
    matrix = evaluate_macd_conditions(close, names, frame=frame)
    dif = frame.value("dif").to_numpy()
    dea = frame.value("dea").to_numpy()
    golden = naive_cross_up(list(dif), list(dea))
    death = naive_cross_down(list(dif), list(dea))

    for i in range(len(close)):
        ready = bool(frame.ready.iloc[i])
        assert bool(matrix["DIF_ABOVE_ZERO"].iloc[i]) == (ready and dif[i] > 0)
        assert bool(matrix["BOTH_LINES_ABOVE_ZERO"].iloc[i]) == (ready and dif[i] > 0 and dea[i] > 0)
        assert bool(matrix["GOLDEN_CROSS"].iloc[i]) == (ready and golden[i])
        assert bool(matrix["DEATH_CROSS"].iloc[i]) == (ready and death[i])
        assert bool(matrix["ABOVE_ZERO_GOLDEN_CROSS"].iloc[i]) == (
            ready and dif[i] > 0 and dea[i] > 0 and golden[i]
        )
    # 至少存在一行只有一条线在水上：DIF 水上但双线未水上。
    only_dif = [
        i for i in range(len(close))
        if matrix["DIF_ABOVE_ZERO"].iloc[i] and not matrix["BOTH_LINES_ABOVE_ZERO"].iloc[i]
    ]
    assert only_dif, "缺少『只有 DIF 在水上』的反例行"
    # 金叉与双线水上金叉必须是不同的集合。
    assert matrix["GOLDEN_CROSS"].sum() >= matrix["ABOVE_ZERO_GOLDEN_CROSS"].sum()
    assert matrix["GOLDEN_CROSS"].sum() > matrix["ABOVE_ZERO_GOLDEN_CROSS"].sum(), (
        "缺少『水下金叉不算双线水上金叉』的反例"
    )


def test_macd_exact_equality_and_near_zero_are_not_golden_cross():
    """恰好相等不得算金叉；正负零附近不得误判。"""
    left = [1.0, 1.0, 1.0000000001]
    right = [1.0, 1.0, 1.0]
    assert list(cross_up(left, right)) == [False, False, True]
    assert list(cross_up([1.0, 1.0], [1.0, 1.0])) == [False, False]
    assert list(cross_up([0.0, 0.0], [0.0, 0.0])) == [False, False]


def test_macd_sustained_above_without_new_cross_has_no_cross_signal():
    """持续位于零轴上方但无新交叉：DIF_ABOVE_ZERO 大量成立，GOLDEN_CROSS 不增加。"""
    close = _series([10.0 + 0.15 * i for i in range(120)])
    matrix = evaluate_macd_conditions(close, ["GOLDEN_CROSS", "DIF_ABOVE_ZERO"])
    assert matrix["DIF_ABOVE_ZERO"].sum() > 0
    assert matrix["GOLDEN_CROSS"].sum() <= 1
    assert matrix["DIF_ABOVE_ZERO"].sum() > matrix["GOLDEN_CROSS"].sum()


def test_unknown_condition_is_rejected():
    close = _series([10.0] * 30)
    with pytest.raises(ConditionError):
        evaluate_macd_conditions(close, ["MACD_WATER_ABOVE"])
    with pytest.raises(ConditionError):
        evaluate_kdj_conditions(close, close, close, ["KDJ_MAYBE"])


def test_parameters_must_be_passed_and_appear_in_result():
    close = _series([10.0 + 0.1 * i for i in range(40)])
    custom = macd_v1(close, fast=5, slow=10, signal=4)
    default = macd_v1(close)
    assert not np.allclose(custom.value("dif").to_numpy()[20:], default.value("dif").to_numpy()[20:])
    assert custom.ready.iloc[10 + 4 - 2] and not default.ready.iloc[10 + 4 - 2]
    with pytest.raises(IndicatorInputError):
        macd_v1(close, fast=26, slow=12)


# --------------------------------------------------------------------------
# 4. KDJ 边界
# --------------------------------------------------------------------------
def test_kdj_hhv_equals_llv_ends_segment_and_does_not_fake_signal():
    """一字板（HHV == LLV）不得把 RSV 补 0，必须结束该段并重新预热。"""
    high = _series([10.0] * 12)
    low = _series([10.0] * 12)
    close = _series([10.0] * 12)
    frame = kdj_v1(high, low, close, n=9)
    assert not frame.ready.any()
    assert frame.value("rsv").isna().all()
    assert frame.value("k").isna().all()


def test_kdj_recovers_after_gap_with_fresh_warmup():
    """HHV == LLV（整窗一字）后必须重新预热 N 根，不得跨缺口延续 K/D。"""
    highs = [10.0 + 0.5 * i for i in range(12)]
    lows = [h - 1.0 for h in highs]
    closes = [h - 0.4 for h in highs]
    # 连续 12 根一字 bar：窗口完全落入其中时 HHV == LLV，属于无效输入。
    highs += [20.0] * 12
    lows += [20.0] * 12
    closes += [20.0] * 12
    highs += [20.0 + 0.5 * i for i in range(1, 13)]
    lows += [h - 1.0 for h in highs[-12:]]
    closes += [h - 0.4 for h in highs[-12:]]

    frame = kdj_v1(_series(highs), _series(lows), _series(closes), n=9)
    assert bool(frame.ready.iloc[11]) is True
    # 整窗一字的区间必须 NOT_READY，且不得把 RSV 补 0 制造信号。
    assert not frame.ready.iloc[20:24].any()
    assert frame.value("rsv").iloc[20:24].isna().all()
    assert frame.value("k").iloc[20:24].isna().all()
    # 缺口后重新预热：从第 24 根起需要 9 根合格 bar，第 24..31 不 ready。
    assert not frame.ready.iloc[24:32].any()
    assert bool(frame.ready.iloc[32]) is True
    assert frame.segment.iloc[11] == 0
    assert frame.segment.iloc[32] == 1


def test_kdj_nan_input_ends_segment_without_crossing_gap():
    highs = [10.0 + 0.2 * i for i in range(24)]
    lows = [h - 1.0 for h in highs]
    closes = [h - 0.4 for h in highs]
    highs[12] = float("nan")
    frame = kdj_v1(_series(highs), _series(lows), _series(closes), n=9)
    assert bool(frame.ready.iloc[11]) is True
    assert bool(frame.ready.iloc[12]) is False
    # 缺口后重新预热：需要 9 根合格 bar。
    assert bool(frame.ready.iloc[20]) is False
    assert bool(frame.ready.iloc[21]) is True


def test_kdj_negative_or_zero_price_is_invalid_input():
    highs = [10.0] * 12
    lows = [9.0] * 12
    closes = [9.5] * 12
    closes[10] = 0.0
    frame = kdj_v1(_series(highs), _series(lows), _series(closes), n=9)
    assert bool(frame.ready.iloc[9]) is True
    assert bool(frame.ready.iloc[10]) is False


def test_kdj_j_is_not_clamped():
    highs = [10.0 + 0.5 * i for i in range(20)]
    lows = [h - 0.1 for h in highs]
    closes = [h for h in highs]
    frame = kdj_v1(_series(highs), _series(lows), _series(closes), n=9)
    assert frame.value("j").max() > 100.0


# --------------------------------------------------------------------------
# 5. 时间轴与因果性
# --------------------------------------------------------------------------
def test_duplicate_or_unsorted_time_index_is_rejected():
    close = pd.Series([10.0, 11.0, 12.0], index=[20250102, 20250102, 20250103])
    with pytest.raises(IndicatorInputError):
        macd_v1(close)
    close = pd.Series([10.0, 11.0, 12.0], index=[20250103, 20250102, 20250104])
    with pytest.raises(IndicatorInputError):
        macd_v1(close)


def test_prefix_computation_matches_batch_on_completed_part():
    closes = _shape_closes("roundtrip") + [10.5, 11.0, 11.5]
    batch = macd_v1(_series(closes))
    cutoff = 35
    prefix = macd_v1(_series(closes[:cutoff]))
    np.testing.assert_allclose(
        prefix.value("dif").to_numpy(), batch.value("dif").to_numpy()[:cutoff], rtol=0, atol=1e-12
    )
    assert list(prefix.ready) == list(batch.ready[:cutoff])


def test_appending_future_prices_does_not_change_past_indicators():
    closes = _shape_closes("roundtrip")
    first = macd_v1(_series(closes))
    appended = macd_v1(_series(closes + [99.0, 88.0, 77.0]))
    np.testing.assert_allclose(
        first.value("dif").to_numpy(), appended.value("dif").to_numpy()[: len(closes)], rtol=0, atol=1e-12
    )
    assert list(first.ready) == list(appended.ready[: len(closes)])


def test_appending_future_prices_does_not_change_past_kdj():
    closes = _shape_closes("roundtrip")
    highs = [c * 1.02 for c in closes]
    lows = [c * 0.98 for c in closes]
    first = kdj_v1(_series(highs), _series(lows), _series(closes))
    highs2 = highs + [200.0, 190.0, 180.0]
    lows2 = lows + [1.0, 1.0, 1.0]
    closes2 = closes + [150.0, 100.0, 50.0]
    appended = kdj_v1(_series(highs2), _series(lows2), _series(closes2))
    np.testing.assert_allclose(
        first.value("k").to_numpy(), appended.value("k").to_numpy()[: len(closes)], rtol=0, atol=1e-12
    )
    assert list(first.ready) == list(appended.ready[: len(closes)])


def test_segment_ids_helper_contract():
    assert list(segment_ids(np.array([True, True, False, True]))) == [0, 0, -1, 1]
    assert list(segment_ids(np.array([False, False]))) == [-1, -1]
