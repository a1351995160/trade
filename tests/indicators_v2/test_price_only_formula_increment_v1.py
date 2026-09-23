"""A0：现有 OHLC(VA) 指标的公式增量验收（合成数据）。

覆盖上一轮矩阵中「有实现但缺独立数值 oracle」的指标，逐值对照
``_oracle_price_only_v1`` 的朴素参考实现。

要求（任务第 5 节）：
- 独立朴素参考，不用被测实现生成 expected；
- 初始化、预热、分段、缺失规则不静默改变；
- 至少两个参数设置；
- 追加未来尾段不得改变此前结果；
- 常数/递增/递减/振荡/跳变/缺口/零成交量/非有限值/短于预热。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))

from chanlun_trader.engine.indicators_v2 import (  # noqa: E402
    accumulation_distribution,
    amount_ma,
    cci,
    chaikin_money_flow,
    dema,
    donchian,
    drawdown_from_peak,
    historical_return,
    keltner,
    macd_hist_raw,
    make_price_input,
    mfi,
    natr,
    price_extremes,
    prior_breakout,
    psy,
    pvt,
    rolling_slope,
    rolling_volatility,
    rvol_incl_current,
    rvol_prior,
    tema,
    trix,
    true_range,
    volume_ma,
    vwap_session_proxy,
)
from tests.indicators_v2 import _oracle_price_only_v1 as oracle  # noqa: E402


def _days(n: int) -> list:
    start = pd.Timestamp("2024-01-02")
    days = []
    i = 0
    while len(days) < n:
        d = start + pd.Timedelta(days=i)
        if d.weekday() < 5:
            days.append(int(d.strftime("%Y%m%d")))
        i += 1
    return days


def _series(kind: str, n: int = 80) -> list:
    if kind == "flat":
        return [10.0] * n
    if kind == "up":
        return [10.0 + 0.1 * i for i in range(n)]
    if kind == "down":
        return [20.0 - 0.1 * i for i in range(n)]
    if kind == "osc":
        return [10.0 + 2.0 * np.sin(i / 5.0) for i in range(n)]
    if kind == "jump":
        return [10.0] * (n // 2) + [20.0] * (n - n // 2)
    if kind == "gap":
        values = [10.0 + 0.05 * i for i in range(n)]
        values[n // 2] = np.nan
        values[n // 2 + 1] = np.nan
        return values
    if kind == "nonfinite":
        values = [10.0 + 0.05 * i for i in range(n)]
        values[n // 3] = np.inf
        values[2 * n // 3] = -np.inf
        return values
    raise ValueError(kind)


def _frame(closes, *, seed: float = 1.0, volume_kind: str = "normal") -> pd.DataFrame:
    days = _days(len(closes))
    n = len(closes)
    if volume_kind == "zero":
        volumes = [0.0] * n
    else:
        volumes = [1_000_000.0 * (1.0 + 0.3 * np.sin(i / 4.0)) + seed for i in range(n)]
    highs = [c * 1.01 if np.isfinite(c) else c for c in closes]
    lows = [c * 0.99 if np.isfinite(c) else c for c in closes]
    opens = [c * 1.002 if np.isfinite(c) else c for c in closes]
    amounts = [c * v if np.isfinite(c) else np.nan for c, v in zip(closes, volumes)]
    prev = [np.nan] + list(closes[:-1])
    return pd.DataFrame({
        "date": days, "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": volumes, "amount": amounts, "prev_close": prev,
    })


def _input(frame: pd.DataFrame):
    return make_price_input(
        pd.Series(frame["close"].to_numpy(), index=frame["date"]),
        high=pd.Series(frame["high"].to_numpy(), index=frame["date"]),
        low=pd.Series(frame["low"].to_numpy(), index=frame["date"]),
        open_=pd.Series(frame["open"].to_numpy(), index=frame["date"]),
        volume=pd.Series(frame["volume"].to_numpy(), index=frame["date"]),
        amount=pd.Series(frame["amount"].to_numpy(), index=frame["date"]),
        prev_close=pd.Series(frame["prev_close"].to_numpy(), index=frame["date"]),
    )


def _nan(values) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in values], dtype=float)


SHAPES = ["flat", "up", "down", "osc", "jump"]


# ---------------------------------------------------------------- DEMA / TEMA
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("window", [5, 20])
def test_dema_matches_oracle(shape, window):
    frame = _frame(_series(shape))
    got = dema(_input(frame), window=window).value("dema").to_numpy()
    want = _nan(oracle.naive_dema(_series(shape), window))
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-9, equal_nan=True)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("window", [5, 20])
def test_tema_matches_oracle(shape, window):
    frame = _frame(_series(shape))
    got = tema(_input(frame), window=window).value("tema").to_numpy()
    want = _nan(oracle.naive_tema(_series(shape), window))
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-9, equal_nan=True)


# ---------------------------------------------------------------------- CCI
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("window", [14, 20])
def test_cci_matches_oracle(shape, window):
    closes = _series(shape)
    frame = _frame(closes)
    got = cci(_input(frame), window=window).value("cci").to_numpy()
    want = _nan(oracle.naive_cci(frame["high"], frame["low"], closes, window))
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-9, equal_nan=True)


# ------------------------------------------------------------- TRUE_RANGE/NATR
@pytest.mark.parametrize("shape", SHAPES)
def test_true_range_matches_oracle(shape):
    closes = _series(shape)
    frame = _frame(closes)
    got = true_range(_input(frame)).value("tr").to_numpy()
    want = np.array(oracle.naive_true_range(
        frame["high"], frame["low"], closes, frame["prev_close"]), dtype=float)
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-9, equal_nan=True)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("window", [14, 20])
def test_natr_matches_oracle(shape, window):
    closes = _series(shape)
    frame = _frame(closes)
    got = natr(_input(frame), window=window).value("natr").to_numpy()
    want = _nan(oracle.naive_natr(
        frame["high"], frame["low"], closes, frame["prev_close"], window))
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-9, equal_nan=True)


# ---------------------------------------------------------------------- PSY
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("window", [12, 6])
def test_psy_matches_oracle(shape, window):
    closes = _series(shape)
    frame = _frame(closes)
    got = psy(_input(frame), window=window).value("psy").to_numpy()
    want = _nan(oracle.naive_psy(closes, window))
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-9, equal_nan=True)


def test_psy_is_not_constant_one_hundred():
    """回归：交替涨跌序列下 PSY 必须约 50%，不能恒为 100%。

    缺陷根因（已修复）：`counts`（窗口内非 NaN 计数）与 `totals`
    （`isfinite` 的 1/NaN 之和）数学恒等，故 `counts/totals` 恒为 1.0，
    PSY 恒输出 100，丢失了"上涨根数占比"的语义。
    """
    n = 30
    # 明确交替：约一半上涨、一半下跌
    closes = [10.0 + (1.0 if i % 2 == 0 else -1.0) * 0.5 * i for i in range(n)]
    frame = _frame(closes)
    values = psy(_input(frame), window=6).value("psy").to_numpy()
    finite = values[np.isfinite(values)]
    assert finite.size > 0, "PSY 无有效值"
    # 交替序列的上涨占比应接近 50%，绝不应恒为 100
    assert finite.max() < 99.0, f"PSY 恒为 100（分母语义错误）：{sorted(set(np.round(finite, 4)))}"
    assert 30.0 < float(np.median(finite)) < 70.0, f"PSY 中位数偏离 50%：{np.median(finite)}"


def test_psy_monotonic_up_is_one_hundred():
    """正对照：严格单调上涨序列 PSY 必须为 100%（不能把修复做成永远不到 100）。"""
    closes = [10.0 + 0.1 * i for i in range(30)]
    frame = _frame(closes)
    values = psy(_input(frame), window=6).value("psy").to_numpy()
    finite = values[np.isfinite(values)]
    assert finite.size > 0
    np.testing.assert_allclose(finite, 100.0, rtol=0, atol=1e-9)


# ---------------------------------------------------------------- DONCHIAN
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("window", [20, 10])
def test_donchian_matches_oracle(shape, window):
    frame = _frame(_series(shape))
    result = donchian(_input(frame), window=window)
    want = oracle.naive_donchian(frame["high"], frame["low"], window, shift=0)
    np.testing.assert_allclose(result.value("upper").to_numpy(), _nan(want["upper"]),
                               rtol=0, atol=1e-9, equal_nan=True)
    np.testing.assert_allclose(result.value("lower").to_numpy(), _nan(want["lower"]),
                               rtol=0, atol=1e-9, equal_nan=True)


def test_donchian_shift_excludes_current_bar():
    """shift=1 参考前 N 根（不含当前），与 shift=0 必须不同。"""
    frame = _frame(_series("up"))
    shifted = donchian(_input(frame), window=10, shift=1)
    plain = donchian(_input(frame), window=10, shift=0)
    want = oracle.naive_donchian(frame["high"], frame["low"], 10, shift=1)
    np.testing.assert_allclose(shifted.value("upper").to_numpy(), _nan(want["upper"]),
                               rtol=0, atol=1e-9, equal_nan=True)
    assert not np.allclose(shifted.value("upper").to_numpy(),
                           plain.value("upper").to_numpy(), equal_nan=True)


# ------------------------------------------------------- ROLLING_VOLATILITY
@pytest.mark.parametrize("shape", ["osc", "jump", "up"])
@pytest.mark.parametrize("window", [20, 10])
def test_rolling_volatility_matches_oracle(shape, window):
    closes = _series(shape)
    frame = _frame(closes)
    got = rolling_volatility(_input(frame), window=window).value("volatility").to_numpy()
    want = _nan(oracle.naive_rolling_volatility(closes, window, ddof=1))
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-9, equal_nan=True)


# ------------------------------------------------------ HISTORICAL_RETURN
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("window", [20, 5])
def test_historical_return_matches_oracle(shape, window):
    closes = _series(shape)
    frame = _frame(closes)
    got = historical_return(_input(frame), window=window).value("return").to_numpy()
    want = _nan(oracle.naive_historical_return(closes, window))
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-9, equal_nan=True)


# ------------------------------------------------------- PRICE_EXTREMES / PRIOR_BREAKOUT
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("window", [20, 10])
def test_price_extremes_matches_oracle(shape, window):
    frame = _frame(_series(shape))
    result = price_extremes(_input(frame), window=window)
    want = oracle.naive_price_extremes(frame["high"], frame["low"], window)
    np.testing.assert_allclose(result.value("hhv").to_numpy(), _nan(want["hhv"]),
                               rtol=0, atol=1e-9, equal_nan=True)
    np.testing.assert_allclose(result.value("llv").to_numpy(), _nan(want["llv"]),
                               rtol=0, atol=1e-9, equal_nan=True)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("window", [20, 10])
def test_prior_breakout_matches_oracle(shape, window):
    closes = _series(shape)
    frame = _frame(closes)
    result = prior_breakout(_input(frame), window=window)
    want = oracle.naive_prior_breakout(frame["high"], frame["low"], closes, window)
    np.testing.assert_allclose(result.value("prior_high").to_numpy(), _nan(want["prior_high"]),
                               rtol=0, atol=1e-9, equal_nan=True)
    np.testing.assert_allclose(result.value("prior_low").to_numpy(), _nan(want["prior_low"]),
                               rtol=0, atol=1e-9, equal_nan=True)


# ------------------------------------------------------------ DRAWDOWN_FROM_PEAK
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("window", [60, 20])
def test_drawdown_from_peak_matches_oracle(shape, window):
    closes = _series(shape, n=120)
    frame = _frame(closes)
    result = drawdown_from_peak(_input(frame), window=window)
    want = oracle.naive_drawdown_from_peak(closes, window)
    np.testing.assert_allclose(result.value("drawdown").to_numpy(), _nan(want["drawdown"]),
                               rtol=0, atol=1e-9, equal_nan=True)
    np.testing.assert_allclose(result.value("peak").to_numpy(), _nan(want["peak"]),
                               rtol=0, atol=1e-9, equal_nan=True)


# ------------------------------------------------------------------ MACD_HIST_RAW
@pytest.mark.parametrize("shape", SHAPES)
def test_macd_hist_raw_matches_oracle(shape):
    closes = _series(shape, n=120)
    frame = _frame(closes)
    result = macd_hist_raw(_input(frame), fast=12, slow=26, signal=9)
    want = oracle.naive_macd_hist_raw(closes, 12, 26, 9)
    np.testing.assert_allclose(result.value("hist_raw").to_numpy(), _nan(want["hist"]),
                               rtol=0, atol=1e-9, equal_nan=True)


def test_macd_hist_raw_is_half_of_macd_hist():
    """MACD_HIST_RAW = DIF - DEA；MACD 的 HIST = 2*(DIF-DEA)。两者必须相差 2 倍。"""
    from chanlun_trader.engine.indicators_v2 import macd_hist_raw as raw_fn
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    closes = _series("osc", n=120)
    frame = _frame(closes)
    raw_hist = raw_fn(_input(frame), fast=12, slow=26, signal=9).value("hist_raw").to_numpy()
    registry = default_registry()
    v1 = registry.compute("MACD", pd.Series(closes, index=_days(len(closes))),
                          params={"fast": 12, "slow": 26, "signal": 9})
    v1_hist = v1.output("hist").to_numpy()
    np.testing.assert_allclose(raw_hist, v1_hist / 2.0, rtol=0, atol=1e-9, equal_nan=True)


# ---------------------------------------------------------------------- TRIX
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("window", [12, 6])
def test_trix_matches_oracle(shape, window):
    closes = _series(shape, n=120)
    frame = _frame(closes)
    result = trix(_input(frame), window=window, signal=9)
    want = oracle.naive_trix(closes, window, 9)
    np.testing.assert_allclose(result.value("trix").to_numpy(), _nan(want["trix"]),
                               rtol=0, atol=1e-9, equal_nan=True)


# ------------------------------------------------------------------ KELTNER
@pytest.mark.parametrize("shape", SHAPES)
def test_keltner_matches_oracle(shape):
    closes = _series(shape)
    frame = _frame(closes)
    result = keltner(_input(frame), window=20, atr_window=10, multiplier=2.0)
    want = oracle.naive_keltner(frame["high"], frame["low"], closes, frame["prev_close"],
                                20, 10, 2.0)
    np.testing.assert_allclose(result.value("upper").to_numpy(), _nan(want["upper"]),
                               rtol=0, atol=1e-9, equal_nan=True)
    np.testing.assert_allclose(result.value("lower").to_numpy(), _nan(want["lower"]),
                               rtol=0, atol=1e-9, equal_nan=True)


# ------------------------------------------------------------- 量价类
@pytest.mark.parametrize("window", [20, 5])
def test_volume_ma_and_amount_ma_match_oracle(window):
    closes = _series("osc")
    frame = _frame(closes)
    data = _input(frame)
    np.testing.assert_allclose(
        volume_ma(data, window=window).value("volume_ma").to_numpy(),
        _nan(oracle.naive_volume_ma(frame["volume"], window)),
        rtol=0, atol=1e-6, equal_nan=True)
    np.testing.assert_allclose(
        amount_ma(data, window=window).value("amount_ma").to_numpy(),
        _nan(oracle.naive_amount_ma(frame["amount"], window)),
        rtol=0, atol=1e-6, equal_nan=True)


@pytest.mark.parametrize("window", [20, 5])
def test_rvol_variants_match_oracle_and_differ(window):
    closes = _series("osc")
    frame = _frame(closes)
    data = _input(frame)
    prior = rvol_prior(data, window=window).value("rvol").to_numpy()
    incl = rvol_incl_current(data, window=window).value("rvol").to_numpy()
    np.testing.assert_allclose(prior, _nan(oracle.naive_rvol_prior(frame["volume"], window)),
                               rtol=0, atol=1e-6, equal_nan=True)
    np.testing.assert_allclose(
        incl, _nan(oracle.naive_rvol_incl_current(frame["volume"], window)),
        rtol=0, atol=1e-6, equal_nan=True)
    # 两种口径必须不同（否则命名区分无意义）
    assert not np.allclose(prior, incl, equal_nan=True)


def test_accumulation_distribution_and_chaikin_and_pvt_match_oracle():
    closes = _series("osc")
    frame = _frame(closes)
    data = _input(frame)
    np.testing.assert_allclose(
        accumulation_distribution(data).value("ad_line").to_numpy(),
        np.array(oracle.naive_accumulation_distribution(
            frame["high"], frame["low"], closes, frame["volume"]), dtype=float),
        rtol=0, atol=1e-6, equal_nan=True)
    np.testing.assert_allclose(
        chaikin_money_flow(data, window=20).value("cmf").to_numpy(),
        _nan(oracle.naive_chaikin_money_flow(frame["high"], frame["low"], closes,
                                             frame["volume"], 20)),
        rtol=0, atol=1e-6, equal_nan=True)
    np.testing.assert_allclose(
        pvt(data).value("pvt").to_numpy(),
        np.array(oracle.naive_pvt(closes, frame["volume"]), dtype=float),
        rtol=0, atol=1e-6, equal_nan=True)


def test_vwap_proxy_and_mfi_match_oracle():
    closes = _series("osc")
    frame = _frame(closes)
    data = _input(frame)
    np.testing.assert_allclose(
        vwap_session_proxy(data).value("vwap_session_proxy").to_numpy(),
        _nan(oracle.naive_vwap_session_proxy(frame["high"], frame["low"], closes,
                                             frame["volume"], frame["amount"])),
        rtol=0, atol=1e-6, equal_nan=True)
    np.testing.assert_allclose(
        mfi(data, window=14).value("mfi").to_numpy(),
        _nan(oracle.naive_mfi(frame["high"], frame["low"], closes, frame["volume"], 14)),
        rtol=0, atol=1e-6, equal_nan=True)


def test_rolling_slope_matches_oracle():
    closes = _series("up")
    frame = _frame(closes)
    np.testing.assert_allclose(
        rolling_slope(_input(frame), window=20).value("slope").to_numpy(),
        _nan(oracle.naive_rolling_slope(closes, 20)),
        rtol=0, atol=1e-9, equal_nan=True)


# ==========================================================================
# 边界与因果性
# ==========================================================================
@pytest.mark.parametrize("shape", ["flat", "up", "down", "osc", "jump", "gap", "nonfinite"])
def test_edge_shapes_do_not_raise_and_stay_finite_or_nan(shape):
    """常数/递增/递减/振荡/跳变/缺口/非有限值：不得抛异常，**主输出**只能有限或 NaN。

    说明：`typical_price` 等**诊断字段**会把非有限输入原样透传（Inf），
    这是可接受行为；主计算输出必须 fail closed（NaN + ready=False）。
    因此本断言只检查主输出，并对诊断字段单独断言其不进入 ready。
    """
    closes = _series(shape)
    frame = _frame(closes)
    data = _input(frame)
    diagnostic_outputs = {"typical_price", "ref_close", "atr", "return", "ema1", "ema2",
                          "ema3", "middle", "peak", "prior_high", "prior_low"}
    for frame_result in (dema(data, window=5), tema(data, window=5), cci(data, window=14),
                         natr(data, window=14), psy(data, window=12),
                         donchian(data, window=10), rolling_volatility(data, window=10),
                         historical_return(data, window=5), price_extremes(data, window=10),
                         prior_breakout(data, window=10), trix(data, window=6),
                         keltner(data, window=20, atr_window=10)):
        for name in frame_result.output_names:
            values = frame_result.value(name).to_numpy(dtype=float)
            if name in diagnostic_outputs:
                # 诊断字段不得被标记 ready
                ready = frame_result.ready.to_numpy()
                assert not (ready & ~np.isfinite(values)).any(), \
                    f"{shape}:{name} 诊断字段被标记 ready 但值非有限"
                continue
            assert np.all(np.isfinite(values) | np.isnan(values)), \
                f"{shape}:{name} 主输出出现 Inf"


def test_nonfinite_input_fails_closed():
    """非有限价格必须 fail closed：含 Inf 的段内主输出为 NaN 且不 ready。

    注意：Inf 会切断分段，其后是**新段**并合法重新预热（ready=True 是正确的）。
    因此只断言 Inf 所在段内不产出可用值，不要求全局 ready=False。
    """
    closes = _series("nonfinite")
    frame = _frame(closes)
    data = _input(frame)
    bad_positions = [i for i, c in enumerate(closes) if not np.isfinite(c)]
    assert bad_positions, "夹具未包含非有限值"
    for frame_result in (cci(data, window=14), mfi(data, window=14)):
        main = frame_result.value(frame_result.output_names[0]).to_numpy(dtype=float)
        assert not np.isinf(main).any(), "主输出出现 Inf"
        # Inf 所在位置本身必须是 NaN（不参与计算）
        for pos in bad_positions:
            assert np.isnan(main[pos]), f"非有限输入位置产出了值：{pos}"
        # Inf 所在位置不得 ready
        ready = frame_result.ready.to_numpy()
        for pos in bad_positions:
            assert not ready[pos], f"非有限输入位置被标记 ready：{pos}"


def test_zero_volume_does_not_produce_inf():
    """零成交量：量价类不得产生 Inf（分母为零须判 UNKNOWN）。"""
    closes = _series("osc")
    frame = _frame(closes, volume_kind="zero")
    data = _input(frame)
    for frame_result in (volume_ma(data, window=5), amount_ma(data, window=5),
                         rvol_prior(data, window=5), rvol_incl_current(data, window=5),
                         chaikin_money_flow(data, window=5), vwap_session_proxy(data),
                         mfi(data, window=5)):
        for name in frame_result.output_names:
            values = frame_result.value(name).to_numpy(dtype=float)
            assert not np.isinf(values).any(), f"零成交量导致 Inf：{name}"


def test_short_series_below_warmup_is_not_ready():
    """短于预热：ready 必须为 False 且值 NaN。"""
    closes = _series("up", n=3)
    frame = _frame(closes)
    data = _input(frame)
    result = donchian(data, window=20)
    assert not result.ready.to_numpy().any()
    assert np.isnan(result.value("upper").to_numpy()).all()


@pytest.mark.parametrize("shape", SHAPES)
def test_appending_future_does_not_change_past(shape):
    """追加未来尾段不得改变此前已完成的指标值。"""
    closes = _series(shape, n=80)
    frame = _frame(closes)
    extended_closes = list(closes) + [closes[-1] * 1.5 + i for i in range(10)]
    extended = _frame(extended_closes)
    for fn, kwargs in ((dema, {"window": 5}), (tema, {"window": 5}), (cci, {"window": 14}),
                       (natr, {"window": 14}), (psy, {"window": 12}),
                       (rolling_volatility, {"window": 10}),
                       (historical_return, {"window": 5}),
                       (rolling_slope, {"window": 10})):
        base = fn(_input(frame), **kwargs)
        grown = fn(_input(extended), **kwargs)
        for name in base.output_names:
            np.testing.assert_allclose(
                grown.value(name).to_numpy()[: len(closes)],
                base.value(name).to_numpy(),
                rtol=0, atol=1e-12, equal_nan=True,
                err_msg=f"{fn.__name__}:{name} 追加未来改变了历史值")


def test_wilder_rma_oracle_reseeds_after_gap():
    """参考实现必须按连续段重新播种，不得跨缺口继承旧段状态。

    反例（复核给定）：window=2，[1,1,NaN,10,10,10]
    应为 [NaN, 2, NaN, NaN, 20, 20]，不是 [NaN, 2, NaN, 11, 15.5, 17.75]。
    """
    values = [1.0, 1.0, float("nan"), 10.0, 10.0, 10.0]
    got = oracle.naive_wilder_rma(values, 2)
    assert got[0] is None and got[1] == pytest.approx(2.0)
    assert got[2] is None
    assert got[3] is None, "缺口后第一个位置应重新预热"
    assert got[4] == pytest.approx(20.0), "缺口后应按新段播种"
    assert got[5] == pytest.approx(20.0)
    # 明确排除旧的错误行为
    assert got[4] != pytest.approx(15.5), "仍跨缺口继承旧段状态"


@pytest.mark.parametrize("shape", ["gap", "nonfinite"])
def test_natr_and_keltner_match_oracle_across_gaps(shape):
    """NATR/Keltner 在缺口与非有限输入上必须与参考实现等值（分段一致）。"""
    closes = _series(shape, n=80)
    frame = _frame(closes)
    data = _input(frame)

    got_natr = natr(data, window=14).value("natr").to_numpy()
    want_natr = _nan(oracle.naive_natr(frame["high"], frame["low"], closes,
                                       frame["prev_close"], 14))
    np.testing.assert_allclose(got_natr, want_natr, rtol=0, atol=1e-9, equal_nan=True)

    got_keltner = keltner(data, window=20, atr_window=10).value("upper").to_numpy()
    want_keltner = _nan(oracle.naive_keltner(frame["high"], frame["low"], closes,
                                             frame["prev_close"], 20, 10, 2.0)["upper"])
    np.testing.assert_allclose(got_keltner, want_keltner, rtol=0, atol=1e-9, equal_nan=True)


def test_natr_normal_control_without_gaps():
    """正对照：无缺口序列上 NATR 仍与参考一致（修复未破坏正常路径）。"""
    closes = _series("osc", n=80)
    frame = _frame(closes)
    got = natr(_input(frame), window=14).value("natr").to_numpy()
    want = _nan(oracle.naive_natr(frame["high"], frame["low"], closes,
                                  frame["prev_close"], 14))
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-9, equal_nan=True)
    assert np.isfinite(got).sum() > 0, "正对照无有效值"


def test_gap_breaks_segment_and_does_not_backfill():
    """缺口处不得回填：缺口之后必须重新预热。"""
    closes = _series("gap")
    frame = _frame(closes)
    result = donchian(_input(frame), window=10)
    upper = result.value("upper").to_numpy()
    gap_at = len(closes) // 2
    # 缺口位置与紧邻位置应为 NaN（缺口切断段）
    assert np.isnan(upper[gap_at])
    assert np.isnan(upper[gap_at + 1])
    # 缺口前一段的最后值仍然有效
    assert np.isfinite(upper[gap_at - 1])
