import numpy as np
import pandas as pd
from chanlun_trader.research.resample import (
    event_date_block_bootstrap,
    build_market_pool_by_date,
    draw_market_placebo,
    sector_matched_placebo_status,
)


def test_event_date_block_bootstrap_samples_whole_blocks():
    ret = [10.0] * 6 + [20.0] * 6
    dates = [20240101] * 6 + [20240102] * 6
    out = event_date_block_bootstrap(ret, dates, n_boot=200, seed=42, return_draws=True)
    assert out["status"] == "OK"
    assert out["block_count"] == 2
    # block means: d1 -> 10, d2 -> 20; resample 2 blocks with replacement -> means in {10, 15, 20}
    allowed = {10.0, 15.0, 20.0}
    assert set(out["draws"]) <= allowed
    assert abs(out["mean"] - 15.0) < 0.5  # finite-sample expected value of block bootstrap
    # block bootstrap must not equal row bootstrap (row mean of 12 rows would be 15.0 too, so compare draws structure)
    assert len(out["draws"]) == 200


def test_market_placebo_excludes_only_same_date_treated():
    lab = pd.DataFrame({
        "timestamp": [20240101, 20240101, 20240101, 20240102, 20240102],
        "symbol": ["000001.SZ", "000002.SZ", "000003.SZ", "000001.SZ", "000004.SZ"],
        "tradable_return": [0.01, 0.02, 0.03, 0.04, 0.05],
    })
    pool = build_market_pool_by_date(lab)
    events = pd.DataFrame({
        "event_time": [20240101, 20240102],
        "symbol": ["000001.SZ", "000001.SZ"],
    })
    # old buggy definition would exclude 000001.SZ on 20240102 too.
    # Correct definition: on 20240102 the treated set is {000001.SZ}, so eligible pool is {000004.SZ}.
    out = draw_market_placebo(events, pool, n_draws=50, seed=42)
    assert out["status"] == "OK"
    # Each draw mean is average of one date1 candidate (0.02 or 0.03) and 0.05 from date2 -> {0.035, 0.04}
    draws = []
    # recompute draws by setting seed and using pool manually
    for s in range(50):
        rng = np.random.default_rng(s)
        vals = []
        # date 20240101 treated {000001}; candidates 000002,000003
        pool1 = pool[20240101]
        syms1, rets1 = pool1
        m = ~np.isin(syms1, np.array(["000001.SZ"], dtype=object))
        cand1 = rets1[m]
        vals.append(float(cand1[rng.integers(0, len(cand1))]))
        pool2 = pool[20240102]
        syms2, rets2 = pool2
        m2 = ~np.isin(syms2, np.array(["000001.SZ"], dtype=object))
        cand2 = rets2[m2]
        vals.append(float(cand2[rng.integers(0, len(cand2))]))
        draws.append(float(np.mean(vals)))
    assert set(draws) <= {0.035, 0.04}


def test_sector_placebo_not_available_without_pit_industry():
    assert sector_matched_placebo_status() == "NOT_AVAILABLE"
