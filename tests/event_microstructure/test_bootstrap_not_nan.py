import sys
import numpy as np
import pandas as pd
sys.path.insert(0, "scripts")
import c1_event_microstructure as c

def test_bootstrap_returns_full_summary_for_trade_pnl():
    trades = pd.read_parquet("data/research/strategy_translation_results/trades_E_E_CONSEC_LIMIT_H5.parquet")
    sells = trades[trades["side"] == "SELL"].set_index("fill_time")["realized_pnl"]
    out = c.bootstrap_pnl(sells)
    assert out["bootstrap_status"] == "OK"
    for k in ["bootstrap_mean", "bootstrap_median", "bootstrap_ci_2_5", "bootstrap_ci_97_5", "probability_positive"]:
        assert out[k] is not None and not (isinstance(out[k], float) and np.isnan(out[k]))
