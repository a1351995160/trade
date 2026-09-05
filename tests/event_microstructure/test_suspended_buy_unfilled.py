import pandas as pd
from chanlun_trader.engine.security_state import ChinaPriceLimitModel

def test_suspended_buy_unfilled():
    m = ChinaPriceLimitModel()
    ok, reason = m.can_buy_at_open("600000.SH", pd.Timestamp("2024-01-02 09:30", tz="Asia/Shanghai"), None)
    assert not ok
    assert reason == "SUSPENDED"

