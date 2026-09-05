"""Resampling and placebo helpers for post-remediation clean reruns.

All draw loops operate on pre-indexed numpy/dict structures; no pandas filtering
inside the inner loop.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def event_date_block_bootstrap(returns, event_dates, n_boot: int = 200, seed: int = 42,
                                  return_draws: bool = False) -> dict:
    """True event-date block bootstrap.

    The sampling unit is the event date (block). A sampled date contributes ALL
    of its observations, preserving within-date dependence. Dates are sampled
    with replacement. 200 replicates, fixed seed.
    """
    s = pd.Series(returns, dtype="float64").dropna()
    d = pd.Series(event_dates).loc[s.index].to_numpy()
    if len(s) < 10:
        return dict(status="UNAVAILABLE", mean=None, median=None, ci2_5=None, ci97_5=None,
                    probability_positive=None, n_boot=n_boot, block_count=0)
    rng = np.random.default_rng(seed)
    order = np.argsort(d, kind="stable")
    sorted_dates = d[order]
    sorted_vals = s.to_numpy()[order]
    bounds = np.flatnonzero(np.r_[True, sorted_dates[1:] != sorted_dates[:-1], True])
    blocks = [np.arange(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    n_blocks = len(blocks)
    means = np.empty(n_boot, dtype="float64")
    for b in range(n_boot):
        chosen = rng.integers(0, n_blocks, size=n_blocks)
        idx = np.concatenate([blocks[i] for i in chosen])
        means[b] = float(sorted_vals[idx].mean())
    out = dict(status="OK", mean=float(means.mean()), median=float(np.median(means)),
               ci2_5=float(np.percentile(means, 2.5)), ci97_5=float(np.percentile(means, 97.5)),
               probability_positive=float((means > 0).mean()), n_boot=n_boot, block_count=n_blocks)
    if return_draws:
        out["draws"] = [float(x) for x in means]
    return out


def build_lab_index(lab_ts: pd.DataFrame):
    """date -> DataFrame for label rows (already filtered to allowed TRAIN range)."""
    return {int(d): g for d, g in lab_ts.groupby("timestamp", sort=True)}


def build_market_pool_by_date(lab_ts: pd.DataFrame):
    """date -> (symbols array, tradable_return array)."""
    out = {}
    for dt, g in lab_ts.groupby("timestamp", sort=True):
        out[int(dt)] = (g["symbol"].to_numpy(), g["tradable_return"].to_numpy(dtype="float64"))
    return out


def build_return_map_by_date(lab_ts: pd.DataFrame):
    """date -> dict(symbol -> tradable_return float)."""
    out = {}
    for dt, g in lab_ts.groupby("timestamp", sort=True):
        out[int(dt)] = dict(zip(g["symbol"], g["tradable_return"]))
    return out


def build_liquidity_rank(daily: pd.DataFrame):
    """Pre-compute (date, symbol)->amount_rank and date -> sorted arrays.

    Returns (rank_map, by_date) where by_date[date] = (symbols, codes, ranks)
    sorted by amount_rank ascending.
    """
    d = daily[["symbol", "date", "amount"]].copy()
    d["amount_rank"] = d.groupby("date")["amount"].rank(pct=True)
    d["symbol_code"] = d["symbol"].str[:6]
    rank_map = {}
    by_date = {}
    for dt, g in d.groupby("date", sort=True):
        dt = int(dt)
        rank_map[dt] = dict(zip(g["symbol"], g["amount_rank"]))
        g = g.sort_values("amount_rank")
        by_date[dt] = (g["symbol"].to_numpy(), g["symbol_code"].to_numpy(),
                       g["amount_rank"].to_numpy(dtype="float64"))
    return rank_map, by_date


def draw_market_placebo(events, market_pool, n_draws=50, seed=42):
    """Market-matched placebo.

    Definition: for each treated event on date T, sample one non-treated symbol
    from the same date's label universe. Exclusion set is the treated symbols ON
    THAT DATE (not all event symbols ever). If no eligible pool exists on a date,
    that event contributes nothing for that draw.
    """
    rng = np.random.default_rng(seed)
    ev_dates = events["event_time"].to_numpy(dtype="int64")
    ev_syms = events["symbol"].to_numpy()
    # treated symbol set by date
    treated_by_date = {}
    for d, s in zip(ev_dates, ev_syms):
        treated_by_date.setdefault(int(d), set()).add(s)
    treated_by_date = {d: np.array(sorted(s), dtype=object) for d, s in treated_by_date.items()}
    means = np.empty(n_draws, dtype="float64")
    for draw in range(n_draws):
        vals = []
        for i in range(len(events)):
            d = int(ev_dates[i])
            pool = market_pool.get(d)
            if pool is None:
                continue
            syms, rets = pool
            treated = treated_by_date.get(d)
            if treated is not None and len(treated):
                mask = ~np.isin(syms, treated)
                syms = syms[mask]
                rets = rets[mask]
            if len(syms) == 0:
                continue
            j = rng.integers(0, len(syms))
            vals.append(float(rets[j]))
        means[draw] = float(np.mean(vals)) if vals else np.nan
    valid = means[~np.isnan(means)]
    if len(valid) == 0:
        return dict(status="NOT_AVAILABLE", mean=None, ci2_5=None, ci97_5=None,
                    n_draws=n_draws, n_valid=0)
    return dict(status="OK", mean=float(valid.mean()), ci2_5=float(np.percentile(valid, 2.5)),
                ci97_5=float(np.percentile(valid, 97.5)), n_draws=n_draws, n_valid=len(valid))


def draw_liquidity_placebo(events, rank_map, by_date, return_map, n_draws=50, seed=42):
    """Liquidity-matched placebo.

    Candidate pool: same-date symbols with amount rank within +/- 0.05 of the
    treated symbol's rank, excluding the treated symbol itself. Uses pre-indexed
    sorted rank arrays + date return maps; no DataFrame filtering in the loop.
    """
    rng = np.random.default_rng(seed)
    ev_dates = events["event_time"].to_numpy(dtype="int64")
    ev_syms = events["symbol"].to_numpy()
    means = np.empty(n_draws, dtype="float64")
    for draw in range(n_draws):
        vals = []
        for i in range(len(events)):
            d = int(ev_dates[i])
            sym = ev_syms[i]
            arr = by_date.get(d)
            rm = rank_map.get(d)
            rmap = return_map.get(d)
            if arr is None or rm is None or rmap is None:
                continue
            syms, _codes, ranks = arr
            r = rm.get(sym)
            if r is None:
                continue
            lo = max(0.0, r - 0.05)
            hi = min(1.0, r + 0.05)
            left = int(np.searchsorted(ranks, lo, side="left"))
            right = int(np.searchsorted(ranks, hi, side="right"))
            if right <= left:
                continue
            cand_syms = syms[left:right]
            cand_syms = cand_syms[cand_syms != sym]
            if len(cand_syms) == 0:
                continue
            j = rng.integers(0, len(cand_syms))
            sym_j = cand_syms[j]
            ret = rmap.get(sym_j)
            if ret is not None:
                vals.append(float(ret))
        means[draw] = float(np.mean(vals)) if vals else np.nan
    valid = means[~np.isnan(means)]
    if len(valid) == 0:
        return dict(status="NOT_AVAILABLE", mean=None, ci2_5=None, ci97_5=None,
                    n_draws=n_draws, n_valid=0)
    return dict(status="OK", mean=float(valid.mean()), ci2_5=float(np.percentile(valid, 2.5)),
                ci97_5=float(np.percentile(valid, 97.5)), n_draws=n_draws, n_valid=len(valid))


def sector_matched_placebo_status() -> str:
    """Sector-matched placebo requires historical PIT industry membership.

    The project only has a single unverified offline snapshot (tdxhy.cfg,
    valid_from=None, valid_to=None, semantic_status='unverified'), so a PIT-safe
    sector-matched placebo is NOT_AVAILABLE.
    """
    return "NOT_AVAILABLE"
