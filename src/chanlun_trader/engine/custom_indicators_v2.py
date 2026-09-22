"""BT_CUSTOM_INDICATORS_V2 — 通过注册与组合接入的自定义指标 fixture。

本模块证明附件要求的**扩展机制**：新增指标只需

1. 用已批准的算子/指标输出组合出表达式（``conditions_v2.Expr``）；
2. 在注册表里登记契约（``IndicatorSpec``）；
3. 由同一个公共服务调用并进入账户链；

**不需要**修改 Engine / Broker / Ledger 的指标名称白名单。

三个 fixture 均与 MACD / KDJ 无关：

- ``VOLUME_BREAKOUT_SCORE``（多输入）：价格突破前序区间 + 相对量，输出复合评分；
- ``TREND_STRENGTH_RATIO``（多输入）：ADX 与均线距离的组合强度；
- ``RSI_REGIME_FLAG``（**依赖另一个指标**）：以 ``RSI`` 为输入，输出状态标记。

``RSI_REGIME_FLAG`` 演示依赖链：注册表记录 ``dependencies``，执行时先算 RSI
再算本指标；依赖缺失即明确报错，不静默降级。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .conditions_v2 import (
    ConditionContext,
    ConditionError,
    ConditionEvaluator,
    Expr,
    field,
    ind,
    lit,
    op,
)
from .indicator_registry_v2 import (
    IndicatorRegistry,
    IndicatorRegistryError,
    IndicatorResult,
    IndicatorSpec,
    NEW_IN_V2,
    _adapter,
    _spec,
)
from .indicators_v2 import IndicatorFrameV2, PriceInput

CUSTOM_INDICATORS_VERSION = "BT_CUSTOM_INDICATORS_V2"


# --------------------------------------------------------------------------
# Fixture 1：多输入复合评分（价格结构 + 量价）
# --------------------------------------------------------------------------


def volume_breakout_score(data: PriceInput, *, window: int = 20,
                          volume_window: int = 20) -> IndicatorFrameV2:
    """成交量确认的突破评分（多输入）。

    定义（明确冻结）：

    ``break_up = (close > HHV(high, window) 的前一根)``  -> 1 或 0
    ``rvol     = volume / MA(volume, volume_window) 的前一根``
    ``score    = break_up * min(rvol, cap)``

    其中 ``cap = 3.0`` 防止极端放量主导。``break_up = 0`` 时 ``score = 0``。
    窗口一律**不含当前 bar**（用 shift(1)），因此不产生前视。
    """
    from .indicators_v2 import _rolling_in_segments, _shift_in_segments, segment_ids

    window = int(window)
    volume_window = int(volume_window)
    if window < 1 or volume_window < 1:
        raise IndicatorRegistryError("INVALID_PARAMETER:VOLUME_BREAKOUT_SCORE")

    if data.high is None:
        raise IndicatorRegistryError("DATA_DEPENDENCY_NOT_MET:VOLUME_BREAKOUT_SCORE:high")
    if data.volume is None:
        raise IndicatorRegistryError("DATA_DEPENDENCY_NOT_MET:VOLUME_BREAKOUT_SCORE:volume")

    valid = data.ohlc_valid() & np.isfinite(data.volume) & (data.volume >= 0)
    segments = segment_ids(valid)

    prior_high = _shift_in_segments(
        _rolling_in_segments(data.high, segments, window, window, "max"), segments, 1)
    volume_mean = _rolling_in_segments(data.volume, segments, volume_window, volume_window, "mean")
    prior_volume_mean = _shift_in_segments(volume_mean, segments, 1)

    with np.errstate(divide="ignore", invalid="ignore"):
        rvol = np.where(prior_volume_mean > 0, data.volume / prior_volume_mean, np.nan)
    break_up = np.where(np.isfinite(prior_high), (data.close > prior_high).astype(float), np.nan)
    capped = np.minimum(rvol, 3.0)
    score = np.where((break_up > 0) & np.isfinite(capped), capped, np.where(np.isfinite(break_up), 0.0, np.nan))

    ready = np.isfinite(prior_high) & np.isfinite(prior_volume_mean)
    return IndicatorFrameV2(
        indicator_id="VOLUME_BREAKOUT_SCORE", version="VOLUME_BREAKOUT_SCORE_V1",
        index=data.index,
        columns={
            "score": pd.Series(score, index=data.index),
            "break_up": pd.Series(break_up, index=data.index),
            "rvol": pd.Series(rvol, index=data.index),
        },
        ready=pd.Series(ready, index=data.index),
        segment=pd.Series(segments, index=data.index),
        warmup_bars=max(window, volume_window) + 1,
    )


# --------------------------------------------------------------------------
# Fixture 2：多输入趋势强度
# --------------------------------------------------------------------------


def trend_strength_ratio(data: PriceInput, *, window: int = 14,
                         ma_window: int = 20) -> IndicatorFrameV2:
    """趋势强度比值（多输入：ADX + 均线距离）。

    ``strength = (ADX/100) * (close/MA(close, ma_window) - 1) * 100``

    只有当 ``ADX`` 与均线**都 ready** 时才输出；否则 NOT_READY。
    ADX 使用 V2 ``dmi_adx`` 的 Wilder 口径。
    """
    from .indicators_v2 import dmi_adx, ma_arithmetic

    adx_frame = dmi_adx(data, window=int(window))
    ma_frame = ma_arithmetic(data, window=int(ma_window))
    adx = adx_frame.value("adx").to_numpy(dtype=float)
    ma = ma_frame.value("ma").to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        distance = np.where(ma > 0, data.close / ma - 1.0, np.nan)
    strength = (adx / 100.0) * distance * 100.0
    ready = np.isfinite(adx) & np.isfinite(ma) & np.isfinite(strength)
    return IndicatorFrameV2(
        indicator_id="TREND_STRENGTH_RATIO", version="TREND_STRENGTH_RATIO_V1",
        index=data.index,
        columns={
            "strength": pd.Series(strength, index=data.index),
            "adx": pd.Series(adx, index=data.index),
            "ma_distance": pd.Series(distance, index=data.index),
        },
        ready=pd.Series(ready, index=data.index),
        segment=adx_frame.segment,
        warmup_bars=max(2 * int(window), int(ma_window)),
    )


# --------------------------------------------------------------------------
# Fixture 3：依赖另一个指标的指标
# --------------------------------------------------------------------------


@dataclass
class RsiRegimeProvider:
    """把注册表里的 ``RSI`` 输出注入自定义指标，形成**依赖另一个指标**的链。

    ``RSI_REGIME_FLAG`` 本身不重算 RSI；它消费 ``RSI`` 的已注册输出，
    因此证明依赖链可以通过注册表解析，而无需在引擎里硬编码 RSI 名称。
    """

    registry: IndicatorRegistry

    def __call__(self, data: PriceInput, *, window: int = 14, low: float = 30.0,
                 high: float = 70.0) -> IndicatorFrameV2:
        close = pd.Series(data.close, index=data.index)
        # 依赖必须经 compute_dependency 取，使固定版本真实约束计算；
        # 直接 compute 会在依赖被固定时被拒绝（防静默绕过）。
        rsi_result = self.registry.compute_dependency(
            "RSI", close, params={"window": int(window)})
        rsi = rsi_result.output("rsi").to_numpy(dtype=float)
        rsi_ready = rsi_result.ready().to_numpy(dtype=bool)

        # -1 超卖区，0 中性区，1 超买区；UNKNOWN 保持 NaN（不是 0）
        known = np.isfinite(rsi) & rsi_ready
        regime = np.where(known, np.where(rsi <= float(low), -1.0,
                                          np.where(rsi >= float(high), 1.0, 0.0)), np.nan)
        return IndicatorFrameV2(
            indicator_id="RSI_REGIME_FLAG", version="RSI_REGIME_FLAG_V1",
            index=data.index,
            columns={
                "regime": pd.Series(regime, index=data.index),
                "rsi_input": pd.Series(rsi, index=data.index),
            },
            ready=pd.Series(known, index=data.index),
            segment=rsi_result.frame.segment,
            warmup_bars=int(window) + 1,
        )


def register_custom_indicators(registry: IndicatorRegistry) -> List[str]:
    """把三个自定义 fixture 注册进给定注册表。返回新注册的 id 列表。

    本函数**不修改** Engine / Broker / Ledger；只扩展指标注册表。
    """
    path = "src/chanlun_trader/engine/custom_indicators_v2.py"
    registered: List[str] = []

    registry.register(
        _spec(
            "VOLUME_BREAKOUT_SCORE", "VOLUME_BREAKOUT_SCORE_V1", "自定义组合",
            "成交量确认的突破评分", NEW_IN_V2,
            ["score", "break_up", "rvol"],
            {"window": 20, "volume_window": 20},
            ("close", "high", "volume"),
            warmup_bars=21, lookback=21, unit="RATIO",
            formula_note="break_up(前序HHV)*min(rvol,3)；窗口不含当前 bar，无前视",
            implementation_path=path,
            evidence={"IMPLEMENTED": True, "REGISTERED": True},
        ),
        _adapter(volume_breakout_score, ["score", "break_up", "rvol"]),
    )
    registered.append("VOLUME_BREAKOUT_SCORE")

    registry.register(
        _spec(
            "TREND_STRENGTH_RATIO", "TREND_STRENGTH_RATIO_V1", "自定义组合",
            "趋势强度比值（ADX × 均线距离）", NEW_IN_V2,
            ["strength", "adx", "ma_distance"],
            {"window": 14, "ma_window": 20},
            _OHLC_LOCAL,
            warmup_bars=28, lookback=28, unit="RATIO",
            formula_note="(ADX/100)*(close/MA-1)*100；两个输入都 ready 才输出",
            implementation_path=path,
            evidence={"IMPLEMENTED": True, "REGISTERED": True},
        ),
        _adapter(trend_strength_ratio, ["strength", "adx", "ma_distance"]),
    )
    registered.append("TREND_STRENGTH_RATIO")

    provider = RsiRegimeProvider(registry=registry)
    registry.register(
        _spec(
            "RSI_REGIME_FLAG", "RSI_REGIME_FLAG_V1", "自定义组合",
            "RSI 区间状态标记（依赖 RSI）", NEW_IN_V2,
            ["regime", "rsi_input"],
            {"window": 14, "low": 30.0, "high": 70.0},
            ("close",),
            warmup_bars=15, lookback=15, unit="STATE_CODE",
            formula_note="消费已注册 RSI 输出；-1 超卖 / 0 中性 / 1 超买；UNKNOWN 保持 NaN",
            implementation_path=path,
            evidence={"IMPLEMENTED": True, "REGISTERED": True},
        ),
        _adapter(provider, ["regime", "rsi_input"]),
        dependencies=("RSI",),
        pinned_versions={"RSI": "RSI_V1"},
    )
    registered.append("RSI_REGIME_FLAG")

    return registered


_OHLC_LOCAL = ("close", "high", "low")

# 自定义指标的依赖声明（用于依赖图校验，不进入引擎白名单）
CUSTOM_DEPENDENCIES: Mapping[str, Tuple[str, ...]] = {
    "VOLUME_BREAKOUT_SCORE": (),
    "TREND_STRENGTH_RATIO": (),
    "RSI_REGIME_FLAG": ("RSI",),
}


def custom_condition_fixtures() -> Dict[str, Expr]:
    """两个非 MACD/KDJ 的自定义条件组合 fixture。

    两者都只通过注册表的指标 id 与已批准算子组合，不修改引擎。
    """
    return {
        # 组合一：突破 + 放量确认（价格结构 × 量价）
        "BREAKOUT_WITH_VOLUME_CONFIRMATION": op(
            "and",
            op("gt", ind("VOLUME_BREAKOUT_SCORE", "break_up"), lit(0.5)),
            op("gt", ind("VOLUME_BREAKOUT_SCORE", "rvol"), lit(1.5)),
        ),
        # 组合二：强趋势 + RSI 未超买（多输入指标 × 依赖指标）
        "TREND_STRENGTH_NOT_OVERBOUGHT": op(
            "and",
            op("gt", ind("TREND_STRENGTH_RATIO", "strength"), lit(0.5)),
            op("lt", ind("RSI_REGIME_FLAG", "regime"), lit(1.0)),
        ),
    }


def custom_exit_condition_fixtures() -> Dict[str, Expr]:
    """自定义**退出**条件 fixture（用于 V2 指标条件退出/反向信号退出）。"""
    return {
        # 反向信号：RSI 进入超买区
        "REVERSE_OVERBOUGHT": op("gt", ind("RSI_REGIME_FLAG", "regime"), lit(0.5)),
        # 指标条件：趋势强度转负
        "TREND_STRENGTH_TURNED_NEGATIVE": op(
            "lt", ind("TREND_STRENGTH_RATIO", "strength"), lit(0.0)),
    }
