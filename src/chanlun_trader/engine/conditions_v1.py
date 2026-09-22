"""BT_MACD_CONDITIONS_V1 — 明确命名、分别测试的 MACD / KDJ 条件。

本模块只负责"在已完成 bar 序列上判定条件是否成立"，不产生订单、
不读取未来数据。条件名与语义一一对应，禁止用含糊的"MACD 水上"代替。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Sequence

import numpy as np
import pandas as pd

from .indicators_v1 import (
    IndicatorFrameV1,
    IndicatorInputError,
    cross_down,
    cross_up,
    kdj_v1,
    macd_v1,
    _require_time_index,
)

CONDITION_CONTRACT_VERSION = "BT_MACD_CONDITIONS_V1"

# 条件名 -> (所需指标列, 语义说明)。语义说明进入最终配置，便于审计。
MACD_CONDITIONS: Dict[str, str] = {
    "DIF_ABOVE_ZERO": "DIF > 0（仅 DIF 站上零轴，DEA 不作要求）",
    "BOTH_LINES_ABOVE_ZERO": "DIF > 0 且 DEA > 0（双线水上）",
    "GOLDEN_CROSS": "前一有效相邻 bar DIF <= DEA，当前 DIF > DEA",
    "DEATH_CROSS": "前一有效相邻 bar DIF >= DEA，当前 DIF < DEA",
    "ABOVE_ZERO_GOLDEN_CROSS": "BOTH_LINES_ABOVE_ZERO 且 GOLDEN_CROSS",
}

KDJ_CONDITIONS: Dict[str, str] = {
    "KDJ_GOLDEN_CROSS": "前一有效相邻 bar K <= D，当前 K > D",
    "KDJ_DEATH_CROSS": "前一有效相邻 bar K >= D，当前 K < D",
    "KDJ_K_ABOVE_D": "K > D（持续位于线上，无新交叉要求）",
    "KDJ_OVERSOLD_GOLDEN_CROSS": "KDJ_GOLDEN_CROSS 且前一有效 bar K < 20",
}


class ConditionError(ValueError):
    """未知条件名或参数非法。"""


@dataclass(frozen=True)
class ConditionSpec:
    """条件规格。参数进入最终配置，不允许被静默丢弃。"""

    name: str
    params: Mapping[str, float] = field(default_factory=dict)

    def resolved(self, defaults: Mapping[str, float]) -> Dict[str, float]:
        merged = dict(defaults)
        merged.update(self.params)
        return merged


@dataclass(frozen=True)
class MacdParams:
    fast: int = 12
    slow: int = 26
    signal: int = 9

    def to_dict(self) -> Dict[str, int]:
        return {"fast": int(self.fast), "slow": int(self.slow), "signal": int(self.signal)}


@dataclass(frozen=True)
class KdjParams:
    n: int = 9
    k_period: int = 3
    d_period: int = 3

    def to_dict(self) -> Dict[str, int]:
        return {"n": int(self.n), "k_period": int(self.k_period), "d_period": int(self.d_period)}


def evaluate_macd_conditions(
    close: pd.Series,
    conditions: Iterable[str],
    *,
    params: MacdParams = MacdParams(),
    frame: IndicatorFrameV1 | None = None,
) -> pd.DataFrame:
    """返回布尔矩阵：index 同 close，columns 为条件名。

    行 ready 语义：MACD 未 ready 的行全部为 False（不是 True，也不抛错），
    调用方必须显式检查 ready 才能把 False 解释为"条件不成立"。
    """
    names = list(conditions)
    unknown = [name for name in names if name not in MACD_CONDITIONS]
    if unknown:
        raise ConditionError(f"UNKNOWN_MACD_CONDITION:{','.join(sorted(unknown))}")
    index = _require_time_index(close.index)
    if frame is None:
        frame = macd_v1(close, fast=params.fast, slow=params.slow, signal=params.signal)
    dif = frame.value("dif").to_numpy(dtype=float)
    dea = frame.value("dea").to_numpy(dtype=float)
    ready = frame.ready.to_numpy(dtype=bool)

    golden = cross_up(dif, dea)
    death = cross_down(dif, dea)
    dif_above = np.isfinite(dif) & (dif > 0)
    both_above = dif_above & np.isfinite(dea) & (dea > 0)

    mapping = {
        "DIF_ABOVE_ZERO": dif_above,
        "BOTH_LINES_ABOVE_ZERO": both_above,
        "GOLDEN_CROSS": golden,
        "DEATH_CROSS": death,
        "ABOVE_ZERO_GOLDEN_CROSS": both_above & golden,
    }
    out = {}
    for name in names:
        out[name] = mapping[name] & ready
    return pd.DataFrame(out, index=index)


def evaluate_kdj_conditions(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    conditions: Iterable[str],
    *,
    params: KdjParams = KdjParams(),
    frame: IndicatorFrameV1 | None = None,
) -> pd.DataFrame:
    names = list(conditions)
    unknown = [name for name in names if name not in KDJ_CONDITIONS]
    if unknown:
        raise ConditionError(f"UNKNOWN_KDJ_CONDITION:{','.join(sorted(unknown))}")
    index = _require_time_index(close.index)
    if frame is None:
        frame = kdj_v1(high, low, close, n=params.n, k_period=params.k_period, d_period=params.d_period)
    k = frame.value("k").to_numpy(dtype=float)
    d = frame.value("d").to_numpy(dtype=float)
    ready = frame.ready.to_numpy(dtype=bool)

    golden = cross_up(k, d)
    death = cross_down(k, d)
    above = np.isfinite(k) & np.isfinite(d) & (k > d)
    previous_k = np.concatenate(([np.nan], k[:-1])) if len(k) else np.zeros(0)
    oversold_cross = golden & np.isfinite(previous_k) & (previous_k < 20.0)

    mapping = {
        "KDJ_GOLDEN_CROSS": golden,
        "KDJ_DEATH_CROSS": death,
        "KDJ_K_ABOVE_D": above,
        "KDJ_OVERSOLD_GOLDEN_CROSS": oversold_cross,
    }
    out = {}
    for name in names:
        out[name] = mapping[name] & ready
    return pd.DataFrame(out, index=index)


def condition_registry() -> Dict[str, Dict[str, str]]:
    """供 CAPABILITY_MATRIX / 文档生成使用。"""
    return {
        "MACD": dict(MACD_CONDITIONS),
        "KDJ": dict(KDJ_CONDITIONS),
    }


def assert_supported(conditions: Sequence[str]) -> None:
    """未知条件必须明确拒绝，不允许静默忽略。"""
    unknown = [name for name in conditions if name not in MACD_CONDITIONS and name not in KDJ_CONDITIONS]
    if unknown:
        raise ConditionError(f"UNSUPPORTED_CONDITION:{','.join(sorted(unknown))}")
