import pandas as pd
from chanlun_trader.engine.fill import EventOpenFillModel
from chanlun_trader.engine.order import Order

def test_participation_caps_quantity():
    m = EventOpenFillModel(participation_rate=0.05)
    o = Order(order_id="o", strategy_id="s", intent_id="i", signal_id="sg", symbol="600000.SH", side="BUY", quantity=100000, created_at=pd.Timestamp("2024-01-02 15:00", tz="Asia/Shanghai"), reason="x")
    bar = dict(open=10.0, volume=1_000_000)
    price, qty, reason = m.try_fill(o, pd.Timestamp("2024-01-03 09:30", tz="Asia/Shanghai"), bar)
    assert price == 10.0
    assert qty == int(1_000_000 * 0.05) == 50000
    assert reason == "OK"

def test_zero_volume_no_fill():
    m = EventOpenFillModel(0.05)
    o = Order(order_id="o", strategy_id="s", intent_id="i", signal_id="sg", symbol="600000.SH", side="BUY", quantity=100, created_at=pd.Timestamp("2024-01-02 15:00", tz="Asia/Shanghai"), reason="x")
    price, qty, reason = m.try_fill(o, pd.Timestamp("2024-01-03 09:30", tz="Asia/Shanghai"), dict(open=10.0, volume=0))
    assert qty == 0
    assert reason in ("NO_TRADABLE_BAR", "PARTICIPATION_LIMIT")
