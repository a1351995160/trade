"""Multiple Testing Controls: BH-FDR / Permutation / Placebo。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def benjamini_hochberg(pvals, alpha=0.05):
    """Benjamini-Hochberg FDR。"""
    p = pd.Series(pvals, dtype=float).dropna().sort_values().reset_index(drop=True)
    n = len(p)
    if n == 0:
        return pd.DataFrame(columns=["pvalue", "rank", "threshold", "reject"])
    thresholds = [(i + 1) / n * alpha for i in range(n)]
    reject = p.to_numpy() <= np.array(thresholds)
    return pd.DataFrame({
        "pvalue": p.to_numpy(),
        "rank": np.arange(1, n + 1),
        "threshold": thresholds,
        "reject": reject,
    })


def permutation_test(observed, metric_fn, factor_df, labels, n_perm=100, seed=0, horizon=5):
    """Permutation test：随机打乱 factor 横截面，估计 metric 的零分布。"""
    rng = np.random.default_rng(seed)
    nulls = []
    lab = labels[labels["horizon"] == horizon]
    for _ in range(n_perm):
        f = factor_df.copy()
        f["value"] = rng.permutation(f["value"].to_numpy())
        nulls.append(metric_fn(f, lab))
    nulls = np.array(nulls)
    p = float((np.abs(nulls) >= np.abs(observed)).mean())
    return p, float(nulls.mean())


def placebo_signal_shuffle(metric_fn, factor_df, labels, n=100, seed=0, horizon=5):
    """Placebo：随机打乱 signal 行，返回 null metric 分布。"""
    rng = np.random.default_rng(seed)
    nulls = []
    lab = labels[labels["horizon"] == horizon]
    for _ in range(n):
        f = factor_df.copy()
        f["value"] = rng.permutation(f["value"].to_numpy())
        nulls.append(metric_fn(f, lab))
    return np.array(nulls)
