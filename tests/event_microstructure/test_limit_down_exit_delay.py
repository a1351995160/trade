import pandas as pd
from chanlun_trader.engine.security_state import ChinaPriceLimitModel

def test_limit_down_locked_blocks_sell():
    m = ChinaPriceLimitModel()
    bar = dict(open=9.0, high=9.0, low=9.0, close=9.0, volume=1e6, prev_close=10.0)
    ok, reason = m.can_sell_at_open("600000.SH", pd.Timestamp("2024-01-02 09:30", tz="Asia/Shanghai"), bar)
    assert not ok
    assert reason == "LIMIT_DOWN_LOCKED"

