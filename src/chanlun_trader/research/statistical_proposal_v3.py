"""待批准的统计提案原型；不接入正式runner、政策或资格裁决。"""
from __future__ import annotations

import numpy as np


def anchored_folds(session_count=486):
    """固定训练内诊断折；半开索引，20session隔离，不能冒充独立确认集。"""
    folds = []
    for start in (146, 229, 312, 395):
        if start+63 > session_count:
            raise ValueError("FROZEN_WALK_FORWARD_SESSIONS_MISSING")
        folds.append({"train": [0, start-20], "embargo": [start-20, start],
                      "evaluation": [start, start+63]})
    return folds


def purged_training_mask(signal_sessions, label_end_sessions, evaluation_start):
    signal, end = np.asarray(signal_sessions), np.asarray(label_end_sessions)
    if signal.shape != end.shape or not np.isfinite(end).all() or np.any(end < signal):
        raise ValueError("ACTUAL_LABEL_END_REQUIRED")
    return (signal < evaluation_start-20) & (end < evaluation_start-20)


def lagged_placebo(signals):
    """20session过去信号安慰剂；同日PIT资格仍须由原执行器重新检查。"""
    x = np.asarray(signals, dtype=float)
    if x.ndim != 2 or len(x) <= 20:
        raise ValueError("PLACEBO_TIME_PANEL_REQUIRED")
    out = np.full_like(x, np.nan)
    out[20:] = x[:-20]
    return out


def stationary_indices(n: int, draws: int, block_length: int, seed: int):
    if min(n, draws, block_length) < 1 or block_length > n:
        raise ValueError("INVALID_BOOTSTRAP_DIMENSIONS")
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, n, size=(draws, n))
    restart = rng.random((draws, n)) < 1.0 / block_length
    for t in range(1, n):
        indices[:, t] = np.where(restart[:, t], indices[:, t], (indices[:, t-1] + 1) % n)
    return indices


def centered_mean_test(values, *, block_length=20, draws=10000, seed=20260911):
    """共同时间索引、H0边界居中、单侧均值统计；只给提案结果。"""
    x = np.asarray(values, dtype=float)
    if x.ndim == 1:
        x = x[:, None]
    if x.ndim != 2 or len(x) < max(200, 10*block_length) or x.shape[1] == 0:
        raise ValueError("INSUFFICIENT_TIME_BLOCKS")
    if not np.isfinite(x).all():
        raise ValueError("MISSING_OR_NONFINITE_TIME_SERIES")
    if np.any(np.std(x, axis=0) == 0):
        raise ValueError("DEGENERATE_SERIES_NO_INFERENCE")
    observed = x.mean(axis=0)
    index = stationary_indices(len(x), draws, block_length, seed)
    centered = x - observed
    null_means = centered[index].mean(axis=1)
    p = (1 + (null_means >= observed).sum(axis=0)) / (draws + 1)
    return {"status": "PROPOSED_NOT_APPROVED", "p_values": p.tolist(),
            "observed_mean": observed.tolist(), "block_length": block_length,
            "draws": draws, "seed": seed, "samples": len(x),
            "null_centered": True, "common_time_indices": True}


def by_adjust(p_values, family_size: int):
    """固定完整家族的BY；无效/未执行成员由上游显式登记为1，不能默默丢弃。"""
    p = np.asarray(p_values, dtype=float)
    if p.ndim != 1 or len(p) != family_size or family_size < 1 or not np.isfinite(p).all() or np.any((p < 0) | (p > 1)):
        raise ValueError("COMPLETE_FROZEN_FAMILY_REQUIRED")
    order = np.argsort(p, kind="stable")
    harmonic = np.sum(1.0 / np.arange(1, family_size+1))
    ordered = p[order] * family_size * harmonic / np.arange(1, family_size+1)
    adjusted = np.minimum(1, np.minimum.accumulate(ordered[::-1])[::-1])
    out = np.empty_like(p)
    out[order] = adjusted
    return out
