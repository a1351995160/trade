import pandas as pd
from chanlun_trader.engine.security_state import ChinaPriceLimitModel

def test_limit_up_locked_rejected():
    m = ChinaPriceLimitModel()
    bar = dict(open=11.0, high=11.0, low=11.0, close=11.0, volume=1e6, prev_close=10.0)
    ok, reason = m.can_buy_at_open("600000.SH", pd.Timestamp("2024-01-02 09:30", tz="Asia/Shanghai"), bar)
    assert not ok
    assert reason == "LIMIT_UP_LOCKED"

def test_limit_up_opened_daily_conservative_rejected():
    m = ChinaPriceLimitModel()
    bar = dict(open=11.0, high=11.3, low=10.9, close=11.1, volume=1e6, prev_close=10.0)
    ok, reason = m.can_buy_at_open("600000.SH", pd.Timestamp("2024-01-02 09:30", tz="Asia/Shanghai"), bar)
    assert not ok
    assert "LIMIT_UP_OPEN" in reason


def test_one_price_limit_unfilled():
    m = ChinaPriceLimitModel()
    bar = dict(open=11.0, high=11.0, low=11.0, close=11.0, volume=1e6, prev_close=10.0)
    ok, reason = m.can_buy_at_open("600000.SH", pd.Timestamp("2024-01-02 09:30", tz="Asia/Shanghai"), bar)
    assert not ok
    assert reason == "LIMIT_UP_LOCKED"
