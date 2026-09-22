"""V2 多家族指标公式验收：手算值 + 独立 oracle + 因果性 + 边界。

不 mock 被测指标。oracle 见 ``tests/indicators_v2/_oracle_v2.py``。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from chanlun_trader.engine.indicator_registry_v2 import (
    IndicatorRegistryError,
    default_registry,
)
from chanlun_trader.engine.indicators_v1 import IndicatorInputError
from chanlun_trader.engine.indicators_v2 import (
    accumulation_distribution,
    atr,
    bar_shape,
    bias,
    bollinger,
    cci,
    chaikin_money_flow,
    dema,
    dmi_adx,
    donchian,
    drawdown_from_peak,
    ema,
    hlc3,
    historical_return,
    keltner,
    ma_arithmetic,
    make_price_input,
    mfi,
    mtm,
    natr,
    obv,
    prior_breakout,
    psy,
    price_extremes,
    pvt,
    rma_wilder,
    roc,
    rolling_slope,
    rolling_volatility,
    rolling_vwap,
    rsi,
    rvol_incl_current,
    rvol_prior,
    sar,
    sma_tdx,
    streak,
    tema,
    time_series_zscore,
    trix,
    true_range,
    turnover_rate,
    volume_ma,
    amount_ma,
    vwap_session_proxy,
    williams_r,
    wma,
)

from tests.indicators_v2._oracle_v2 import (
    naive_atr,
    naive_bar_shape,
    naive_bias,
    naive_bollinger,
    naive_ema,
    naive_hlc3,
    naive_mtm,
    naive_obv,
    naive_rma_wilder,
    naive_roc,
    naive_rolling_slope,
    naive_rsi,
    naive_sma,
    naive_streak,
    naive_ts_zscore,
    naive_williams_r,
    naive_wma,
)

TOL = 1e-9


def _days(count: int) -> list:
    start = pd.Timestamp("2025-01-01")
    return [int((start + pd.Timedelta(days=i)).strftime("%Y%m%d")) for i in range(count)]


def _frame(closes, *, highs=None, lows=None, opens=None, volumes=None, amounts=None):
    closes = [float(c) for c in closes]
    n = len(closes)
    days = _days(n)
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    opens = opens or [c * 1.002 for c in closes]
    volumes = volumes or [1_000_000.0] * n
    amounts = amounts or [c * v for c, v in zip(closes, volumes)]
    return make_price_input(
        pd.Series(closes, index=days), high=pd.Series(highs, index=days),
        low=pd.Series(lows, index=days), open_=pd.Series(opens, index=days),
        volume=pd.Series(volumes, index=days), amount=pd.Series(amounts, index=days),
    )


def _nan(seq):
    return np.array([np.nan if v is None else float(v) for v in seq], dtype=float)


def _shape(kind: str, n: int = 90) -> list:
    if kind == "flat":
        return [10.0] * n
    if kind == "up":
        return [10.0 + 0.1 * i for i in range(n)]
    if kind == "down":
        return [20.0 - 0.1 * i for i in range(n)]
    if kind == "roundtrip":
        return [10.0 + (2.0 if i % 10 < 5 else -2.0) * (1 + i / n) for i in range(n)]
    if kind == "jump":
        return [10.0] * (n // 2) + [20.0] * (n - n // 2)
    return [10.0 + 0.05 * i for i in range(n)]


# --------------------------------------------------------------------------
# 1. 手算短序列
# --------------------------------------------------------------------------
def test_ma_hand_computed():
    data = _frame([10, 11, 12, 13, 14])
    frame = ma_arithmetic(data, window=3)
    out = frame.value("ma").to_numpy()
    assert np.isnan(out[0]) and np.isnan(out[1])
    assert out[2] == pytest.approx(11.0)
    assert out[3] == pytest.approx(12.0)
    assert out[4] == pytest.approx(13.0)


def test_sma_tdx_hand_computed_and_differs_from_arithmetic():
    """通达信 SMA(X,N,M)：Y=(M*X+(N-M)*Y_prev)/N。N=3,M=1, 序列 10,11,12,13。"""
    data = _frame([10, 11, 12, 13])
    tdx = sma_tdx(data, window=3, weight=1).value("sma_tdx").to_numpy()
    arithmetic = ma_arithmetic(data, window=3).value("ma").to_numpy()
    # y1 = 10（段首取首值）；y2 = (1*11 + 2*10)/3 = 10.3333; y3 = (1*12+2*10.3333)/3 = 10.8889
    assert tdx[1] == pytest.approx(10.333333333333334, abs=1e-9)
    assert tdx[2] == pytest.approx(10.888888888888889, abs=1e-9)
    # 与算术均值必须不同（防止把两种公式混为一谈）
    assert tdx[2] != pytest.approx(arithmetic[2], abs=1e-9)
    assert arithmetic[2] == pytest.approx(11.0)


def test_ema_and_rma_have_different_initialisation():
    closes = [10.0, 11.0, 12.0, 13.0, 14.0]
    data = _frame(closes)
    e = ema(data, window=3).value("ema").to_numpy()
    r = rma_wilder(data, window=3).value("rma").to_numpy()
    # EMA: alpha=2/4=0.5, 段首=10 -> 10, 10.5, 11.25, 12.125, 13.0625
    assert e[0] == pytest.approx(10.0)
    assert e[1] == pytest.approx(10.5)
    assert e[2] == pytest.approx(11.25)
    # RMA: 段首=前 3 根均值 = 11
    assert r[2] == pytest.approx(11.0)
    assert e[2] != pytest.approx(r[2])


def test_bollinger_hand_computed_ddof_zero_default():
    data = _frame([10.0, 11.0, 12.0, 13.0, 14.0])
    frame = bollinger(data, window=3, multiplier=2.0, ddof=0)
    middle = frame.value("middle").to_numpy()
    upper = frame.value("upper").to_numpy()
    assert middle[2] == pytest.approx(11.0)
    # 总体标准差 = sqrt(2/3) = 0.816496580927726
    assert upper[2] == pytest.approx(11.0 + 2 * np.sqrt(2 / 3), abs=1e-9)
    # 样本标准差口径必须不同
    sample = bollinger(data, window=3, multiplier=2.0, ddof=1).value("upper").to_numpy()
    assert sample[2] != pytest.approx(upper[2])


def test_williams_r_zero_to_hundred_and_not_negative():
    """WR 使用 0~100 表达；收在窗口最低 -> 100，收在最高 -> 0。"""
    # 窗口内高点 12.2、低点 9.8；末根收 10.0 -> (12.2-10.0)/(12.2-9.8)*100 = 91.667
    data = _frame([10.0, 11.0, 12.0, 11.0, 10.0],
                  highs=[10.2, 11.2, 12.2, 11.2, 10.2],
                  lows=[9.8, 10.8, 11.8, 10.8, 9.8])
    out = williams_r(data, window=3).value("wr").to_numpy()
    assert np.nanmin(out) >= 0.0 and np.nanmax(out) <= 100.0
    assert out[4] == pytest.approx((12.2 - 10.0) / (12.2 - 9.8) * 100.0, abs=1e-9)
    # 收在窗口最低点 -> 100（最弱）：末根 close == 窗口最低
    bottom = _frame([10.0, 11.0, 9.8], highs=[10.2, 11.2, 10.2], lows=[9.8, 10.8, 9.8])
    assert williams_r(bottom, window=3).value("wr").to_numpy()[2] == pytest.approx(100.0)
    # 收在窗口最高点 -> 0（最强）：末根 close == 窗口最高
    top = _frame([10.0, 11.0, 12.2], highs=[10.2, 11.2, 12.2], lows=[9.8, 10.8, 11.8])
    assert williams_r(top, window=3).value("wr").to_numpy()[2] == pytest.approx(0.0)


def test_obv_hand_computed():
    closes = [10.0, 11.0, 10.5, 10.5, 12.0]
    volumes = [100.0, 200.0, 300.0, 400.0, 500.0]
    data = _frame(closes, volumes=volumes)
    out = obv(data).value("obv").to_numpy()
    assert list(out) == [0.0, 200.0, -100.0, -100.0, 400.0]


def test_streak_hand_computed():
    closes = [10.0, 11.0, 12.0, 12.0, 11.0, 10.0, 9.0]
    data = _frame(closes)
    frame = streak(data)
    assert list(frame.value("up_streak").to_numpy()) == [0, 1, 2, 0, 0, 0, 0]
    assert list(frame.value("down_streak").to_numpy()) == [0, 0, 0, 0, 1, 2, 3]


def test_hlc3_hand_computed():
    data = _frame([10.0, 11.0], highs=[12.0, 13.0], lows=[9.0, 10.0])
    out = hlc3(data).value("hlc3").to_numpy()
    assert out[0] == pytest.approx((12.0 + 9.0 + 10.0) / 3.0)
    assert out[1] == pytest.approx((13.0 + 10.0 + 11.0) / 3.0)


# --------------------------------------------------------------------------
# 2. 与独立 oracle 逐值一致
# --------------------------------------------------------------------------
@pytest.mark.parametrize("shape", ["flat", "up", "down", "roundtrip", "jump"])
def test_ma_matches_oracle(shape):
    closes = _shape(shape)
    data = _frame(closes)
    np.testing.assert_allclose(ma_arithmetic(data, window=20).value("ma").to_numpy(),
                               _nan(naive_sma(closes, 20)), rtol=0, atol=1e-12)


@pytest.mark.parametrize("shape", ["flat", "up", "down", "roundtrip"])
def test_ema_wma_rma_match_oracle(shape):
    closes = _shape(shape)
    data = _frame(closes)
    np.testing.assert_allclose(ema(data, window=12).value("ema").to_numpy(),
                               _nan(naive_ema(closes, 12)), rtol=0, atol=1e-12)
    np.testing.assert_allclose(wma(data, window=20).value("wma").to_numpy(),
                               _nan(naive_wma(closes, 20)), rtol=0, atol=1e-12)
    np.testing.assert_allclose(rma_wilder(data, window=14).value("rma").to_numpy(),
                               _nan(naive_rma_wilder(closes, 14)), rtol=0, atol=1e-12)


@pytest.mark.parametrize("shape", ["flat", "up", "down", "roundtrip"])
def test_rsi_matches_oracle(shape):
    closes = _shape(shape)
    data = _frame(closes)
    np.testing.assert_allclose(rsi(data, window=14).value("rsi").to_numpy(),
                               _nan(naive_rsi(closes, 14)), rtol=0, atol=1e-12)


@pytest.mark.parametrize("shape", ["up", "down", "roundtrip"])
def test_williams_r_matches_oracle(shape):
    closes = _shape(shape)
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    data = _frame(closes, highs=highs, lows=lows)
    np.testing.assert_allclose(williams_r(data, window=14).value("wr").to_numpy(),
                               _nan(naive_williams_r(highs, lows, closes, 14)), rtol=0, atol=1e-12)


@pytest.mark.parametrize("ddof", [0, 1])
def test_bollinger_matches_oracle(ddof):
    closes = _shape("roundtrip")
    data = _frame(closes)
    frame = bollinger(data, window=20, multiplier=2.0, ddof=ddof)
    m, u, l = naive_bollinger(closes, 20, 2.0, ddof)
    np.testing.assert_allclose(frame.value("middle").to_numpy(), _nan(m), rtol=0, atol=1e-12)
    np.testing.assert_allclose(frame.value("upper").to_numpy(), _nan(u), rtol=0, atol=1e-12)
    np.testing.assert_allclose(frame.value("lower").to_numpy(), _nan(l), rtol=0, atol=1e-12)


def test_atr_matches_oracle():
    closes = _shape("roundtrip")
    highs = [c * 1.02 for c in closes]
    lows = [c * 0.98 for c in closes]
    data = _frame(closes, highs=highs, lows=lows)
    prev = [None] + closes[:-1]
    np.testing.assert_allclose(atr(data, window=14).value("atr").to_numpy(),
                               _nan(naive_atr(highs, lows, closes, prev, 14)), rtol=0, atol=1e-9)


def test_obv_roc_mtm_bias_match_oracle():
    closes = _shape("roundtrip")
    volumes = [1_000_000.0 + 1000 * i for i in range(len(closes))]
    data = _frame(closes, volumes=volumes)
    np.testing.assert_allclose(obv(data).value("obv").to_numpy(), _nan(naive_obv(closes, volumes)),
                               rtol=0, atol=1e-9)
    np.testing.assert_allclose(roc(data, window=12).value("roc").to_numpy(),
                               _nan(naive_roc(closes, 12)), rtol=0, atol=1e-12)
    np.testing.assert_allclose(mtm(data, window=12).value("mtm").to_numpy(),
                               _nan(naive_mtm(closes, 12)), rtol=0, atol=1e-12)
    np.testing.assert_allclose(bias(data, window=6).value("bias").to_numpy(),
                               _nan(naive_bias(closes, 6)), rtol=0, atol=1e-12)


def test_hlc3_slope_zscore_shape_match_oracle():
    closes = _shape("roundtrip")
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    opens = [c * 1.003 for c in closes]
    data = _frame(closes, highs=highs, lows=lows, opens=opens)
    np.testing.assert_allclose(hlc3(data).value("hlc3").to_numpy(), _nan(naive_hlc3(highs, lows, closes)),
                               rtol=0, atol=1e-12)
    np.testing.assert_allclose(rolling_slope(data, window=20).value("slope").to_numpy(),
                               _nan(naive_rolling_slope(closes, 20)), rtol=0, atol=1e-12)
    np.testing.assert_allclose(time_series_zscore(data, window=60, ddof=1).value("ts_zscore").to_numpy(),
                               _nan(naive_ts_zscore(closes, 60, 1)), rtol=0, atol=1e-12)
    body, upper, lower = naive_bar_shape(opens, highs, lows, closes, [None] + closes[:-1])
    frame = bar_shape(data)
    np.testing.assert_allclose(frame.value("body_ratio").to_numpy(), _nan(body), rtol=0, atol=1e-12)
    np.testing.assert_allclose(frame.value("upper_shadow_ratio").to_numpy(), _nan(upper), rtol=0, atol=1e-12)
    np.testing.assert_allclose(frame.value("lower_shadow_ratio").to_numpy(), _nan(lower), rtol=0, atol=1e-12)


def test_streak_matches_oracle():
    closes = _shape("roundtrip")
    data = _frame(closes)
    up, down = naive_streak(closes)
    frame = streak(data)
    np.testing.assert_allclose(frame.value("up_streak").to_numpy(), np.array(up, dtype=float))
    np.testing.assert_allclose(frame.value("down_streak").to_numpy(), np.array(down, dtype=float))


def test_dmi_and_sar_match_independent_oracle():
    """DMI/ADX 与 SAR 必须有独立数值 oracle，不能只测参数错误。"""
    from tests.indicators_v2._oracle_v2 import naive_dmi, naive_sar

    closes = _shape("roundtrip", 120)
    highs = [c * 1.02 for c in closes]
    lows = [c * 0.98 for c in closes]
    data = _frame(closes, highs=highs, lows=lows)
    prev = [None] + closes[:-1]

    expected = naive_dmi(highs, lows, closes, prev, 14)
    frame = dmi_adx(data, window=14)
    for name in ("plus_di", "minus_di", "adx"):
        np.testing.assert_allclose(
            frame.value(name).to_numpy(), _nan(expected[name]), rtol=0, atol=1e-9,
            equal_nan=True)

    expected_sar = naive_sar(highs, lows, 0.02, 0.2)
    sar_frame = sar(data, step=0.02, max_step=0.2)
    np.testing.assert_allclose(
        sar_frame.value("sar").to_numpy(), _nan(expected_sar["sar"]),
        rtol=0, atol=1e-9, equal_nan=True)
    np.testing.assert_allclose(
        sar_frame.value("sar_trend").to_numpy(),
        np.array([float(v) for v in expected_sar["trend"]]), rtol=0, atol=1e-9,
        equal_nan=True)


def test_dmi_adx_reacts_to_trend_strength():
    """性质检查（补充，不替代 oracle）：强趋势的 ADX 应高于横盘。"""
    trending = [10.0 + 0.2 * i for i in range(80)]
    flat = [10.0 + 0.05 * (i % 2) for i in range(80)]
    trend_frame = dmi_adx(_frame(trending, highs=[c * 1.01 for c in trending],
                                 lows=[c * 0.99 for c in trending]), window=14)
    flat_frame = dmi_adx(_frame(flat, highs=[c * 1.01 for c in flat],
                                lows=[c * 0.99 for c in flat]), window=14)
    assert float(trend_frame.value("adx").max()) > float(flat_frame.value("adx").max())
    closes = _shape("roundtrip")
    data = _frame(closes)
    up, down = naive_streak(closes)
    frame = streak(data)
    np.testing.assert_allclose(frame.value("up_streak").to_numpy(), np.array(up, dtype=float))
    np.testing.assert_allclose(frame.value("down_streak").to_numpy(), np.array(down, dtype=float))


# --------------------------------------------------------------------------
# 3. 参数实际生效 + 边界 + 非法值
# --------------------------------------------------------------------------
def test_parameters_actually_take_effect():
    closes = _shape("roundtrip")
    data = _frame(closes)
    a = ma_arithmetic(data, window=5).value("ma").to_numpy()
    b = ma_arithmetic(data, window=20).value("ma").to_numpy()
    assert not np.allclose(a[30:], b[30:], equal_nan=True)
    r14 = rsi(data, window=14).value("rsi").to_numpy()
    r7 = rsi(data, window=7).value("rsi").to_numpy()
    assert not np.allclose(r14[30:], r7[30:], equal_nan=True)
    # ddof 进入参数身份
    d0 = bollinger(data, window=20, ddof=0).value("std").to_numpy()
    d1 = bollinger(data, window=20, ddof=1).value("std").to_numpy()
    assert not np.allclose(d0[30:], d1[30:], equal_nan=True)


def test_invalid_parameters_rejected():
    data = _frame(_shape("up"))
    with pytest.raises(IndicatorInputError):
        ma_arithmetic(data, window=0)
    with pytest.raises(IndicatorInputError):
        bollinger(data, window=20, ddof=2)
    with pytest.raises(IndicatorInputError):
        bollinger(data, window=20, multiplier=0.0)
    with pytest.raises(IndicatorInputError):
        sma_tdx(data, window=3, weight=5)
    with pytest.raises(IndicatorInputError):
        donchian(data, window=20, shift=-1)
    with pytest.raises(IndicatorInputError):
        sar(data, step=0.5, max_step=0.1)


def test_constant_series_edge_cases():
    data = _frame([10.0] * 60)
    # 完全无波动：RSI 定义为中性 50（明确声明，不返回 NaN）
    rsi_frame = rsi(data, window=14)
    out = rsi_frame.value("rsi").to_numpy()
    ready = rsi_frame.ready.to_numpy()
    assert ready.any()
    assert np.all(out[ready] == 50.0)
    # 零分母：WR 在窗口内高低相同 -> NaN，不补 0 制造信号
    # 注意：常数收盘时 high/low 仍按 1%/0.99% 展开，故窗口内 HHV>LLV；
    # 这里改用真正一字（H==L==C）来构造零分母。
    flat = _frame([10.0] * 60, highs=[10.0] * 60, lows=[10.0] * 60)
    wr_frame = williams_r(flat, window=14)
    assert not wr_frame.ready.any()
    assert np.isnan(wr_frame.value("wr").to_numpy()).all()
    # 零标准差（真一字 H=L=C）：BOLL 带宽为 0，%B 因跨度为 0 而为 NaN，
    # 中轨正常（中轨不依赖离散度）。这里明确记录该约定。
    boll = bollinger(flat, window=20)
    boll_ready = boll.ready.to_numpy()
    assert boll_ready.any()
    assert np.all(boll.value("bandwidth").to_numpy()[boll_ready] == 0.0)
    assert np.isnan(boll.value("percent_b").to_numpy()[boll_ready]).all()
    assert np.all(boll.value("middle").to_numpy()[boll_ready] == 10.0)


def test_short_data_and_insufficient_warmup():
    data = _frame([10.0, 11.0, 12.0])
    assert not ma_arithmetic(data, window=20).ready.any()
    assert not rsi(data, window=14).ready.any()
    assert not bollinger(data, window=20).ready.any()
    assert not atr(data, window=14).ready.any()


def test_nan_and_invalid_price_break_segment():
    closes = [10.0 + 0.1 * i for i in range(60)]
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    closes[30] = float("nan")
    highs[30] = float("nan")
    data = _frame(closes, highs=highs, lows=lows)
    frame = ma_arithmetic(data, window=5)
    assert bool(frame.ready.iloc[29]) is True
    assert bool(frame.ready.iloc[30]) is False
    # 缺口后重新预热：第 30 根为段首，需再满 5 根 -> 第 35 根（0-based）才 ready
    assert not frame.ready.iloc[30:35].any()
    assert bool(frame.ready.iloc[35]) is True


def test_zero_or_negative_price_is_invalid():
    closes = [10.0] * 30
    closes[20] = 0.0
    data = _frame(closes)
    frame = ema(data, window=5)
    assert bool(frame.ready.iloc[19]) is True
    assert bool(frame.ready.iloc[20]) is False


def test_negative_inf_not_accepted():
    closes = [10.0] * 30
    closes[15] = float("-inf")
    data = _frame(closes)
    frame = ma_arithmetic(data, window=5)
    assert bool(frame.ready.iloc[15]) is False


# --------------------------------------------------------------------------
# 4. 因果性：逐前缀一致 + 追加未来不改过去
# --------------------------------------------------------------------------
@pytest.mark.parametrize("factory,params", [
    (ma_arithmetic, {"window": 20}),
    (ema, {"window": 12}),
    (rsi, {"window": 14}),
    (bollinger, {"window": 20}),
    (atr, {"window": 14}),
    (obv, {}),
])
def test_prefix_matches_batch(factory, params):
    closes = _shape("roundtrip", 90)
    data = _frame(closes)
    batch = factory(data, **params)
    cutoff = 70
    prefix = factory(_frame(closes[:cutoff]), **params)
    for name in batch.output_names:
        np.testing.assert_allclose(
            prefix.value(name).to_numpy(), batch.value(name).to_numpy()[:cutoff],
            rtol=0, atol=1e-12, equal_nan=True)


@pytest.mark.parametrize("factory,params", [
    (ma_arithmetic, {"window": 20}),
    (rsi, {"window": 14}),
    (bollinger, {"window": 20}),
    (atr, {"window": 14}),
    (obv, {}),
    (dmi_adx, {"window": 14}),
])
def test_appending_future_does_not_change_past(factory, params):
    closes = _shape("roundtrip", 90)
    first = factory(_frame(closes), **params)
    extended_closes = closes + [99.0, 5.0, 88.0]
    extended = factory(_frame(extended_closes), **params)
    for name in first.output_names:
        np.testing.assert_allclose(
            first.value(name).to_numpy(), extended.value(name).to_numpy()[: len(closes)],
            rtol=0, atol=1e-12, equal_nan=True)


def test_multi_symbol_isolation_through_registry():
    """跨证券隔离：两个证券的数据不串值。"""
    reg = default_registry()
    days = _days(80)
    a = pd.Series([10.0 + 0.1 * i for i in range(80)], index=days)
    b = pd.Series([50.0 - 0.2 * i for i in range(80)], index=days)
    ra = reg.compute("MA", a, params={"window": 20}).output("ma")
    rb = reg.compute("MA", b, params={"window": 20}).output("ma")
    assert ra.iloc[-1] == pytest.approx(10.0 + 0.1 * (79 - 9.5))
    assert rb.iloc[-1] == pytest.approx(50.0 - 0.2 * (79 - 9.5))
    assert not np.allclose(ra.dropna().to_numpy(), rb.dropna().to_numpy())


# --------------------------------------------------------------------------
# 5. 多输出名稳定 + 依赖额外数据明确拒绝
# --------------------------------------------------------------------------
def test_multi_output_names_are_stable_and_explicit():
    reg = default_registry()
    days = _days(80)
    close = pd.Series([10.0 + 0.1 * i for i in range(80)], index=days)
    high = pd.Series([c * 1.01 for c in close], index=days)
    low = pd.Series([c * 0.99 for c in close], index=days)
    for indicator_id, expected in [
        ("MACD", ["ema_fast", "ema_slow", "dif", "dea", "hist"]),
        ("KDJ", ["rsv", "k", "d", "j"]),
        ("BOLLINGER", ["middle", "upper", "lower", "bandwidth", "percent_b", "std"]),
        ("DMI", ["plus_di", "minus_di", "adx", "atr"]),
    ]:
        result = reg.compute(indicator_id, close, high=high, low=low)
        assert list(result.output_names) == expected
        # 未声明的输出名必须报错，不得静默返回 NaN
        with pytest.raises(IndicatorRegistryError):
            result.output("not_an_output")


def test_turnover_rate_requires_extra_data_and_refuses_substitutes():
    reg = default_registry()
    days = _days(60)
    close = pd.Series([10.0] * 60, index=days)
    volume = pd.Series([1_000_000.0] * 60, index=days)
    with pytest.raises(IndicatorRegistryError) as excinfo:
        reg.compute("TURNOVER_RATE", close, volume=volume)
    assert "DATA_DEPENDENCY_NOT_MET" in str(excinfo.value)
    assert "float_shares" in str(excinfo.value)
    # 提供同口径流通股本后可用
    shares = pd.Series([500_000_000.0] * 60, index=days)
    result = reg.compute("TURNOVER_RATE", close, volume=volume,
                         extra_data={"float_shares": shares})
    assert result.output("turnover_rate").notna().any()


def test_registry_rejects_unknown_and_invalid_requests():
    reg = default_registry()
    days = _days(60)
    close = pd.Series([10.0] * 60, index=days)
    with pytest.raises(IndicatorRegistryError):
        reg.compute("NO_SUCH_INDICATOR", close)
    with pytest.raises(IndicatorRegistryError):
        reg.compute("RSI", close, params={"bogus": 1})
    with pytest.raises(IndicatorRegistryError):
        reg.compute("RSI", close, params={"window": -1})
    with pytest.raises(IndicatorRegistryError):
        reg.compute("ATR", close)     # 缺 high/low
    with pytest.raises(IndicatorRegistryError):
        reg.compute("OBV", close)     # 缺 volume


def test_registry_aliases_resolve_to_same_contract():
    reg = default_registry()
    for alias, canonical in [("SMA", "MA"), ("WR", "WILLIAMS_R"), ("BOLL", "BOLLINGER"),
                             ("TR", "TRUE_RANGE"), ("MOM", "MTM")]:
        assert reg.resolve(alias).indicator_id == canonical
    # 未注册别名不得静默回退到别的指标
    with pytest.raises(IndicatorRegistryError):
        reg.resolve("TOTALLY_UNKNOWN_ALIAS")


def test_explicit_version_is_honoured_and_unknown_version_rejected():
    """显式版本必须被使用；未知版本必须报错，不得被静默忽略。"""
    reg = default_registry()
    days = _days(60)
    close = pd.Series([10.0 + 0.1 * i for i in range(60)], index=days)
    result = reg.compute("RSI", close, version="RSI_V1")
    assert result.version == "RSI_V1"
    with pytest.raises(IndicatorRegistryError):
        reg.compute("RSI", close, version="RSI_V999")


def test_registry_snapshot_has_required_contract_fields():
    """每个注册项都必须带齐机器可读契约字段（不能只有名字）。"""
    reg = default_registry()
    required = {"indicator_id", "version", "family", "outputs", "params", "inputs",
                "price_mode", "frequency", "available_at_rule", "warmup_bars",
                "lookback", "missing_policy", "unit", "evidence"}
    for spec in reg.specs():
        payload = spec.to_dict()
        assert required <= set(payload), spec.indicator_id
        assert payload["outputs"], spec.indicator_id
        assert payload["version"], spec.indicator_id
        assert payload["family"], spec.indicator_id
        assert isinstance(payload["warmup_bars"], int)
        # 证据状态必须分别记录，不能由一个推导
        assert set(payload["evidence"]) >= {
            "IMPLEMENTED", "FORMULA_VALIDATED", "REGISTERED",
            "ENTRYPOINT_WIRED", "ACCOUNTING_VALIDATED",
        }
