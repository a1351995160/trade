"""FactorEvaluator: IC / RankIC / ICIR / Quantile / Coverage / Turnover。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .label import HORIZONS


@dataclass
class FactorEvalResult:
    factor_id: str
    version: str
    horizon: int
    n_obs: int
    coverage: float
    ic_mean: Optional[float]
    ic_std: Optional[float]
    icir: Optional[float]
    rank_ic_mean: Optional[float]
    rank_ic_std: Optional[float]
    rank_icir: Optional[float]
    positive_ic_ratio: Optional[float]
    q1_mean: Optional[float]
    q2_mean: Optional[float]
    q3_mean: Optional[float]
    q4_mean: Optional[float]
    q5_mean: Optional[float]
    top_decile_mean: Optional[float]
    top5pct_mean: Optional[float]
    top_quantile_win_rate: Optional[float]
    monotonic: bool
    factor_autocorr: Optional[float]
    factor_turnover: Optional[float]

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        return d


def _rank_corr(x: np.ndarray, y: np.ndarray) -> float:
    n = len(x)
    if n < 3:
        return float("nan")
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    c = np.corrcoef(rx, ry)[0, 1]
    return float(c) if np.isfinite(c) else float("nan")


class FactorEvaluator:
    """Factor 横截面评估。所有输入必须是 PIT-safe 长表。"""

    def evaluate(self, factor_id: str, version: str, factor_df: pd.DataFrame,
                 labels: pd.DataFrame, horizon: int = 5,
                 factor_col: str = "value", label_col: str = "future_return",
                 n_quantiles: int = 5) -> FactorEvalResult:
        """factor_df: symbol, timestamp, value。labels: symbol, timestamp, horizon, future_return。"""
        if factor_df.empty or labels.empty:
            return FactorEvalResult(
                factor_id=factor_id, version=version, horizon=horizon, n_obs=0,
                coverage=0.0, ic_mean=None, ic_std=None, icir=None,
                rank_ic_mean=None, rank_ic_std=None, rank_icir=None,
                positive_ic_ratio=None, q1_mean=None, q2_mean=None, q3_mean=None,
                q4_mean=None, q5_mean=None, top_decile_mean=None, top5pct_mean=None,
                top_quantile_win_rate=None, monotonic=False,
                factor_autocorr=None, factor_turnover=None,
            )
        lab = labels[labels["horizon"] == horizon]
        merged = factor_df.merge(lab, on=["symbol", "timestamp"], how="inner")
        if merged.empty:
            return FactorEvalResult(
                factor_id=factor_id, version=version, horizon=horizon, n_obs=0,
                coverage=0.0, ic_mean=None, ic_std=None, icir=None,
                rank_ic_mean=None, rank_ic_std=None, rank_icir=None,
                positive_ic_ratio=None, q1_mean=None, q2_mean=None, q3_mean=None,
                q4_mean=None, q5_mean=None, top_decile_mean=None, top5pct_mean=None,
                top_quantile_win_rate=None, monotonic=False,
                factor_autocorr=None, factor_turnover=None,
            )
        n_obs = len(merged)
        coverage = n_obs / max(1, len(factor_df))

        # cross-sectional IC by timestamp
        daily_ics, daily_rank_ics = [], []
        for ts, g in merged.groupby("timestamp"):
            if len(g) >= 3:
                daily_ics.append(np.corrcoef(g[factor_col], g[label_col])[0, 1])
                daily_rank_ics.append(_rank_corr(g[factor_col].to_numpy(), g[label_col].to_numpy()))
        daily_ics = np.array([x for x in daily_ics if np.isfinite(x)], dtype=float)
        daily_rank_ics = np.array([x for x in daily_rank_ics if np.isfinite(x)], dtype=float)

        def ic_stats(arr):
            if len(arr) == 0:
                return None, None, None, None
            return float(arr.mean()), float(arr.std(ddof=1)) if len(arr) > 1 else None, \
                float(arr.mean() / arr.std(ddof=1)) if len(arr) > 1 and arr.std(ddof=1) > 0 else None, \
                float((arr > 0).mean())

        ic_mean, ic_std, icir, pos_ratio = ic_stats(daily_ics)
        ric_mean, ric_std, ricir, _ = ic_stats(daily_rank_ics)

        # quantile analysis per timestamp
        merged["_q"] = merged.groupby("timestamp")[factor_col].transform(
            lambda s: pd.qcut(s, q=n_quantiles, labels=False, duplicates="drop")
        )
        qmeans = merged.groupby("_q")[label_col].mean()
        qdict = {int(k): float(v) for k, v in qmeans.items()}
        q1 = qdict.get(0); q2 = qdict.get(1); q3 = qdict.get(2); q4 = qdict.get(3); q5 = qdict.get(4)

        # long-only focus: top quantile (highest factor)
        top = merged[merged["_q"] == n_quantiles - 1]
        top_decile_mean = float(top[label_col].mean()) if len(top) else None
        top5 = merged.groupby("timestamp").apply(
            lambda g: g.nlargest(max(1, int(np.ceil(len(g) * 0.05))), factor_col), include_groups=False
        )
        top5pct_mean = float(top5[label_col].mean()) if len(top5) else None
        top_quantile_win_rate = float((top[label_col] > 0).mean()) if len(top) else None

        # monotonicity: sign of q1..q5 should be consistent
        vals = [q1, q2, q3, q4, q5]
        vals = [v for v in vals if v is not None]
        if len(vals) >= 3:
            diffs = np.diff(vals)
            monotonic = bool((diffs >= 0).all() or (diffs <= 0).all())
        else:
            monotonic = False

        # factor autocorrelation (daily mean) and turnover (rank autocorrelation)
        factor_autocorr = None
        factor_turnover = None
        try:
            pivot = merged.pivot_table(index="timestamp", columns="symbol", values=factor_col)
            if len(pivot) > 2:
                factor_autocorr = float(pivot.mean(axis=1).autocorr())
                prev = pivot.shift(1)
                rank_corrs = []
                for ts in pivot.index[1:]:
                    a = pivot.loc[ts]; b = prev.loc[ts]
                    m = a.notna() & b.notna()
                    if m.sum() >= 5:
                        rank_corrs.append(_rank_corr(a[m].to_numpy(), b[m].to_numpy()))
                if rank_corrs:
                    factor_turnover = float(1.0 - np.nanmean(rank_corrs))
        except Exception:
            pass

        return FactorEvalResult(
            factor_id=factor_id, version=version, horizon=horizon, n_obs=n_obs,
            coverage=float(coverage), ic_mean=ic_mean, ic_std=ic_std, icir=icir,
            rank_ic_mean=ric_mean, rank_ic_std=ric_std, rank_icir=ricir,
            positive_ic_ratio=pos_ratio,
            q1_mean=q1, q2_mean=q2, q3_mean=q3, q4_mean=q4, q5_mean=q5,
            top_decile_mean=top_decile_mean, top5pct_mean=top5pct_mean,
            top_quantile_win_rate=top_quantile_win_rate, monotonic=monotonic,
            factor_autocorr=factor_autocorr, factor_turnover=factor_turnover,
        )

    def evaluate_horizons(self, factor_id, version, factor_df, labels,
                          horizons=HORIZONS, factor_col="value") -> pd.DataFrame:
        rows = []
        for h in horizons:
            r = self.evaluate(factor_id, version, factor_df, labels, horizon=h, factor_col=factor_col)
            rows.append(r.to_dict())
        return pd.DataFrame(rows)
