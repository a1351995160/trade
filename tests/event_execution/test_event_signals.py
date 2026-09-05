import pandas as pd
from pathlib import Path
import sys
sys.path.insert(0, "scripts")
import b1_strategy_translation as b

def test_event_signal_generated_at_event_close_next_session_open():
    events = pd.DataFrame([dict(symbol="000001.SZ", event_time=20240207, event_id="E_LIMITUP_SEAL")])
    sigs = b.build_event_signals(events, "E_TEST_H5", 5)
    assert len(sigs) == 1
    s = sigs[0]
    assert s.generated_at.strftime("%Y%m%d") == "20240207"
    assert s.generated_at.hour == 15
    assert s.execution_policy.value == "NEXT_SESSION_OPEN"

def test_event_store_available_at_is_next_session():
    ev = pd.read_parquet("data/research/event_store/E_LIMITUP_SEAL_v1.parquet")
    train = ev[(ev["event_time"] >= 20220801) & (ev["event_time"] <= 20240731)]
    assert len(train) > 500
    assert (train["available_at"] > train["event_time"]).all()
