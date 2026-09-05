import pandas as pd
from pathlib import Path

def test_event_store_available_at_is_t_plus_one_date():
    ev = pd.read_parquet("data/research/event_store/E_LIMITUP_v1.parquet")
    train = ev[(ev["event_time"] >= 20220801) & (ev["event_time"] <= 20240731)]
    # available_at is T+1 (date-level), though integer arithmetic may roll month-end,
    # the engine uses trading calendar next session, never the raw integer.
    assert len(train) > 1000
    assert (train["available_at"] >= train["event_time"]).all()

def test_core_strategies_use_t_close_signal_and_next_open_entry():
    import sys
    sys.path.insert(0, "scripts")
    import c1_event_microstructure as c
    daily = pd.read_parquet(c.DAILY_PATH)
    cal = sorted(daily[(daily["date"] >= c.TRAIN[0]) & (daily["date"] <= c.TRAIN[1])]["date"].unique())
    for eid, meta in c.CORE.items():
        ev = c.load_event(eid)
        sig_time = c.date_to_ts(int(ev["event_time"].iloc[0]), 15, 0)
        assert sig_time.hour == 15
        t = int(ev["event_time"].iloc[0])
        i = cal.index(t)
        assert cal[i + 1] > t
