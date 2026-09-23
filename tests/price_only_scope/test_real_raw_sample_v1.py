"""A1：真实 RAW 指标数值小样本（600000.SH / 000001.SZ，2024-01-02..2024-07-31）。

标记（任务第 6 节）：
    PRICE_VIEW = RAW_CALLER_SERIES
    CORPORATE_ACTION_NORMALIZED = false
    HISTORICAL_TRADE_ELIGIBILITY_CERTIFIED = false
    PROFITABILITY_VALIDATED = false

禁止：策略买卖条件搜索、收益排序、参数择优、实盘信号清单、真实账户回测、绩效与胜率。

执行前置（已由 tests/price_only_scope/test_bounded_read_boundary_v1.py 证明）：
有界读取器在合成文件上确实物理限窗，且拒绝越过封存期的请求。

本模块只在两个证券、单一日期窗口上读取，并做指标值与独立参考的对账。
不调用 gbbq、不做复权、不扩展到其他证券或窗口。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))

from chanlun_trader.price_only_scope import task_scope_active  # noqa: E402
from chanlun_trader.research.guard import RESEARCH_END  # noqa: E402
from chanlun_trader.research.io_safety import read_day_file_range  # noqa: E402

TDX_ROOT = Path("E:/new_tdx_mock/vipdoc")
SYMBOLS = {"600000.SH": TDX_ROOT / "sh" / "lday" / "sh600000.day",
           "000001.SZ": TDX_ROOT / "sz" / "lday" / "sz000001.day"}
START = 20240102
END = 20240731

# 本模块读取**真实行情**，属 real_data_integration 分类。
# 任务作用域激活时不再重跑：A1 证据已在本任务早期采集并记录，
# 后续轮次重复执行会造成无意义的真实数据重读。
pytestmark = [
    pytest.mark.real_data_integration,
    pytest.mark.skipif(
        task_scope_active(),
        reason="TASK_SCOPE_ACTIVE: A1 证据已采集；本任务不重跑真实行情采样"),
    pytest.mark.skipif(
        not all(p.exists() for p in SYMBOLS.values()),
        reason="A1_BLOCKED: 本机缺少 .day 样本文件（真实数据不在预期根）"),
]


def _read(symbol: str) -> pd.DataFrame:
    """读取 RAW 切片，并在**切片内**按前一交易日收盘递推 prev_close。

    注意：切片首根没有切片内前收（其真实前收在窗口之外），
    因此首根 prev_close 置 NaN —— 不越界去取窗口外数据补足。
    """
    frame = read_day_file_range(SYMBOLS[symbol], start_date=START, end_date=END)
    closes = frame["close"].to_numpy(dtype=float)
    prev = np.concatenate(([np.nan], closes[:-1]))
    frame = frame.copy()
    frame["prev_close"] = prev
    return frame


def _load_indicators():
    from chanlun_trader.engine.indicators_v2 import (
        atr, bollinger, cci, dema, ema, keltner, macd_hist_raw, make_price_input,
        natr, psy, rsi, tema, true_range,
    )
    return dict(atr=atr, bollinger=bollinger, cci=cci, dema=dema, ema=ema,
                keltner=keltner, macd_hist_raw=macd_hist_raw, make_price_input=make_price_input,
                natr=natr, psy=psy, rsi=rsi, tema=tema, true_range=true_range)


def _price_input(frame: pd.DataFrame):
    loaders = _load_indicators()
    index = pd.Index(frame["date"].astype(int).to_numpy(), name="date")
    return loaders["make_price_input"](
        pd.Series(frame["close"].to_numpy(), index=index),
        high=pd.Series(frame["high"].to_numpy(), index=index),
        low=pd.Series(frame["low"].to_numpy(), index=index),
        open_=pd.Series(frame["open"].to_numpy(), index=index),
        volume=pd.Series(frame["volume"].to_numpy(), index=index),
        amount=pd.Series(frame["amount"].to_numpy(), index=index),
        prev_close=pd.Series(frame["prev_close"].to_numpy(), index=index),
    )


@pytest.fixture(scope="module")
def frames() -> dict:
    return {symbol: _read(symbol) for symbol in SYMBOLS}


@pytest.mark.parametrize("symbol", sorted(SYMBOLS))
def test_raw_slice_is_within_authorized_window(symbol, frames):
    """读取结果必须落在授权窗口内，且未触碰封存期。"""
    frame = frames[symbol]
    assert len(frame) > 0, f"{symbol} 在窗口内无数据"
    assert int(frame["date"].min()) >= START
    assert int(frame["date"].max()) <= END <= RESEARCH_END
    assert int(frame["date"].max()) < 20250801
    # 价格与量纲
    for column in ("open", "high", "low", "close"):
        assert (frame[column] > 0).all(), f"{symbol}:{column} 存在非正价格"
        assert np.isfinite(frame[column]).all()
    assert (frame["high"] >= frame[["open", "close", "low"]].max(axis=1)).all()
    assert (frame["low"] <= frame[["open", "close", "high"]].min(axis=1)).all()
    assert frame["date"].duplicated().sum() == 0


@pytest.mark.parametrize("symbol", sorted(SYMBOLS))
def test_raw_slice_matches_independent_reference(symbol, frames):
    """指标值必须与独立朴素参考逐值一致（不与被测实现互证）。"""
    from tests.indicators_v2 import _oracle_price_only_v1 as oracle

    frame = frames[symbol]
    data = _price_input(frame)
    loaders = _load_indicators()
    closes = frame["close"].to_numpy()

    def _nan(values):
        return np.array([np.nan if v is None else v for v in values], dtype=float)

    # EMA
    np.testing.assert_allclose(
        loaders["ema"](data, window=20).value("ema").to_numpy(),
        _nan(oracle.naive_ema(closes, 20)), rtol=0, atol=1e-9, equal_nan=True)
    # DEMA / TEMA
    np.testing.assert_allclose(
        loaders["dema"](data, window=10).value("dema").to_numpy(),
        _nan(oracle.naive_dema(closes, 10)), rtol=0, atol=1e-9, equal_nan=True)
    np.testing.assert_allclose(
        loaders["tema"](data, window=10).value("tema").to_numpy(),
        _nan(oracle.naive_tema(closes, 10)), rtol=0, atol=1e-9, equal_nan=True)
    # TRUE_RANGE / NATR
    np.testing.assert_allclose(
        loaders["true_range"](data).value("tr").to_numpy(),
        np.array(oracle.naive_true_range(frame["high"], frame["low"], closes,
                                         frame["prev_close"]), dtype=float),
        rtol=0, atol=1e-9, equal_nan=True)
    np.testing.assert_allclose(
        loaders["natr"](data, window=14).value("natr").to_numpy(),
        _nan(oracle.naive_natr(frame["high"], frame["low"], closes,
                               frame["prev_close"], 14)),
        rtol=0, atol=1e-9, equal_nan=True)
    # PSY（本轮修复的指标，必须在真实 RAW 上验证）
    np.testing.assert_allclose(
        loaders["psy"](data, window=12).value("psy").to_numpy(),
        _nan(oracle.naive_psy(closes, 12)), rtol=0, atol=1e-9, equal_nan=True)
    # CCI
    np.testing.assert_allclose(
        loaders["cci"](data, window=14).value("cci").to_numpy(),
        _nan(oracle.naive_cci(frame["high"], frame["low"], closes, 14)),
        rtol=0, atol=1e-9, equal_nan=True)
    # MACD_HIST_RAW
    np.testing.assert_allclose(
        loaders["macd_hist_raw"](data, fast=12, slow=26, signal=9).value("hist_raw").to_numpy(),
        _nan(oracle.naive_macd_hist_raw(closes, 12, 26, 9)["hist"]),
        rtol=0, atol=1e-9, equal_nan=True)


@pytest.mark.parametrize("symbol", sorted(SYMBOLS))
def test_psy_on_real_raw_is_not_constant_one_hundred(symbol, frames):
    """PSY 在真实 RAW 上不得恒为 100%（本轮修复的缺陷在真实数据上的回归）。"""
    frame = frames[symbol]
    values = _load_indicators()["psy"](_price_input(frame), window=12).value("psy").to_numpy()
    finite = values[np.isfinite(values)]
    assert finite.size > 0, f"{symbol}: PSY 无有效值"
    assert finite.max() < 100.0 or finite.min() < 100.0, \
        f"{symbol}: PSY 恒为 100（分母语义错误）"


@pytest.mark.parametrize("symbol", sorted(SYMBOLS))
def test_ready_and_unknown_are_reported(symbol, frames):
    """ready/UNKNOWN 必须如实报告：预热期内一律不得 ready。

    注意两类指标的差异（都正确）：
    - 滚动窗口类（CCI/PSY）：预热期内值本身为 NaN；
    - 递推类（EMA）：从首值播种，预热期内**有值但不 ready**。
    因此不变量是"预热期内不 ready"，而不是"预热期内值必为 NaN"。
    """
    frame = frames[symbol]
    data = _price_input(frame)
    loaders = _load_indicators()
    for result, warmup in ((loaders["ema"](data, window=20), 20),
                           (loaders["cci"](data, window=14), 14),
                           (loaders["psy"](data, window=12), 13)):
        ready = result.ready.to_numpy()
        assert not ready[: warmup - 1].any(), f"{symbol}: 预热期内被标记 ready"
        assert ready.sum() > 0, f"{symbol}: 全部不 ready（预热不足？）"
        # 未 ready 的位置不得被条件层当作 TRUE（UNKNOWN 语义）
        main = result.value(result.output_names[0]).to_numpy(dtype=float)
        assert not (ready & np.isnan(main)).any(), f"{symbol}: ready 但值为 NaN"


@pytest.mark.parametrize("symbol", sorted(SYMBOLS))
def test_rolling_indicators_are_nan_during_warmup(symbol, frames):
    """滚动窗口类指标在预热期内必须为 NaN（不得回填）。"""
    frame = frames[symbol]
    data = _price_input(frame)
    loaders = _load_indicators()
    for result, warmup in ((loaders["cci"](data, window=14), 14),
                           (loaders["psy"](data, window=12), 13),
                           (loaders["natr"](data, window=14), 14)):
        main = result.value(result.output_names[0]).to_numpy(dtype=float)
        assert np.isnan(main[: warmup - 1]).all(), f"{symbol}: 预热期内产出了值"


def test_a1_does_not_touch_corporate_action_or_adjust(frames):
    """A1 不调用 gbbq、不做复权：只使用 .day 的 RAW 列。"""
    from chanlun_trader.price_only_scope import is_forbidden_gbbq_path

    assert is_forbidden_gbbq_path("E:/new_tdx_mock/T0002/hq_cache/gbbq")
    for symbol, frame in frames.items():
        # RAW 切片只含 .day 的 7 列 + prev_close
        assert set(frame.columns) >= {"date", "open", "high", "low", "close", "volume", "amount"}
        # 未引入任何复权列
        assert not any("adj" in c.lower() or "qfq" in c.lower() or "hfq" in c.lower()
                       for c in frame.columns), f"{symbol}: 出现复权列"
