import pandas as pd
from chanlun_trader.engine.ledger import PortfolioLedger
from chanlun_trader.engine.fill import Fill

def test_sell_restriction_blocks_same_day_sell():
    from chanlun_trader.engine.order import Order
    led = PortfolioLedger(initial_cash=10_000_000)
    # simulate a position opened 2024-01-03
    ts_buy = pd.Timestamp("2024-01-03 09:30", tz="Asia/Shanghai")
    fill = Fill(fill_id="f1", order_id="o1", strategy_id="s", symbol="600000.SH",
                side="BUY", quantity=100, price=10.0, fill_time=ts_buy)
    led.apply_fill(fill, order_id="o1")
    # same-day sellable should be 0
    assert led.sellable_quantity("s", "600000.SH", ts_buy) == 0

