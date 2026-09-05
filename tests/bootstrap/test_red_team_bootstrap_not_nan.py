import numpy as np
import pandas as pd
from chanlun_trader.research.red_team_utils import bootstrap_trade_pnl

def test_bootstrap_sufficient_sample_returns_full_summary():
    rng = np.random.default_rng(7)
    idx = pd.date_range("2023-01-05", periods=120, freq="7D")
    pnl = pd.Series(rng.normal(500, 2000, 120), index=idx)
    out = bootstrap_trade_pnl(pnl, n_boot=100)
    assert out["bootstrap_status"] == "OK"
    for k in ["bootstrap_mean", "bootstrap_median", "bootstrap_ci_2_5", "bootstrap_ci_97_5", "probability_positive"]:
        assert out[k] is not None and not (isinstance(out[k], float) and np.isnan(out[k]))
    assert 0.0 <= out["probability_positive"] <= 1.0

def test_bootstrap_small_sample_explicit_marker():
    pnl = pd.Series([1.0, 2.0])
    out = bootstrap_trade_pnl(pnl)
    assert out["bootstrap_status"] == "BOOTSTRAP_UNAVAILABLE_SAMPLE_TOO_SMALL"
    assert out["bootstrap_mean"] is None
