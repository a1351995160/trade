import sys
import pandas as pd
sys.path.insert(0, "scripts")
import c1_event_microstructure as c

def test_executable_engine_runs_and_fills():
    daily = pd.read_parquet(c.DAILY_PATH)
    cal = sorted(daily[(daily["date"] >= c.TRAIN[0]) & (daily["date"] <= c.TRAIN[1])]["date"].unique())
    ev = c.load_event("E_CONSEC_LIMIT").head(200)
    res, m = c.run_event_engine(daily, cal, ev, "E_CONSEC_LIMIT", 5, participation=0.05,
                                strategy_id="E_E_CONSEC_LIMIT_H5_TEST")
    assert m["n_signals"] == len(ev)
    assert m["trade_count"] > 20
    assert m["participation"] == 0.05
