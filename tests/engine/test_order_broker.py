"""Order lifecycle + Broker + Fill + Fee + Slippage tests."""
import pandas as pd
import pytest

from src.chanlun_trader.engine.asof import MarketDataStore
from src.chanlun_trader.engine.broker import BrokerSimulator
from src.chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from src.chanlun_trader.engine.fill import DailyBarFillModel
from src.chanlun_trader.engine.ledger import PortfolioLedger
from src.chanlun_trader.engine.order import Order, OrderStatus, TimeInForce
from src.chanlun_trader.engine.order_manager import OrderManager
from src.chanlun_trader.engine.security_state import ChinaPriceLimitModel, SuspensionModel
from src.chanlun_trader.engine.signal import ExecutionPolicy, Side, Signal
from src.chanlun_trader.engine.time_types import TradingCalendar, TradingClock, tz_aware

from tests.golden._helpers import CAL, make_daily_df, make_store


def _store_with_day(symbol="600000.SH", day=20250103, open_px=10.0, close_px=10.2, high_px=10.5, low_px=9.8, volume=1_000_000.0):
    store = MarketDataStore()
    df = make_daily_df(days=CAL, open_px=open_px, close_px=close_px, high_px=high_px, low_px=low_px, volume=volume)
    # 只把 day 的 OHLC 覆盖为给定值
    import numpy as np
    df.loc[day, ["open", "high", "low", "close", "volume", "prev_close"]] = [open_px, high_px, low_px, close_px, volume, 10.0]
    store.add_daily_raw(symbol, df)
    store.add_daily_qfq(symbol, df)
    return store


def test_order_lifecycle_transitions():
    om = OrderManager()
    d = tz_aware(2025, 1, 2, 15, 0)
    o = Order(order_id="", strategy_id="S", intent_id="i", signal_id="s", symbol="600000.SH",
              side=Side.BUY, quantity=100, created_at=d, time_in_force=TimeInForce.DAY)
    om.create_order(o, d)
    assert o.status == OrderStatus.CREATED
    om.submit(o, d)
    assert o.status == OrderStatus.ACCEPTED
    om.apply_fill(o, 40, 10.0, d, 5.0)
    assert o.status == OrderStatus.PARTIALLY_FILLED
    om.apply_fill(o, 60, 10.1, d, 0.5)
    assert o.status == OrderStatus.FILLED
    assert o.filled_quantity == 100 and o.remaining_quantity == 0


def test_daily_fill_model_partial_fill():
    m = DailyBarFillModel(max_participation_rate=0.1)
    o = Order(order_id="o", strategy_id="S", intent_id="i", signal_id="s", symbol="600000.SH",
              side=Side.BUY, quantity=1000, created_at=tz_aware(2025, 1, 2, 15, 0),
              time_in_force=TimeInForce.DAY)
    bar = {"open": 10.0, "volume": 5000.0}
    px, qty, reason = m.try_fill(o, tz_aware(2025, 1, 3, 9, 30), bar)
    assert qty == 500 and px == 10.0 and reason == "OK"


def test_limit_up_reject_in_broker():
    store = _store_with_day(day=CAL[1], open_px=11.0, high_px=11.0, low_px=11.0, close_px=11.0, volume=10000.0)
    cfg = EngineConfig(initial_cash=1_000_000.0, max_positions=10, mode="DAILY", max_position_weight=1.0)
    cfg.persist_run_manifest = False
    eng = BacktestEngineV2(store, CAL, config=cfg, source_identity=("UNKNOWN", True))
    sig = Signal(strategy_id="S", signal_id="s1", symbol="600000.SH",
                 generated_at=tz_aware(2025, 1, 2, 15, 0), direction=Side.BUY,
                 execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN)
    eng.add_signal(sig)
    res = eng.run()
    assert res.ledger.trades == []
    orders = list(res.orders.orders.values())
    assert orders and orders[0].status == OrderStatus.REJECTED


def test_suspension_day_order_rejected():
    store = _store_with_day(day=CAL[1], open_px=10.0, high_px=10.0, low_px=10.0, close_px=10.0, volume=0.0)
    cfg = EngineConfig(initial_cash=1_000_000.0, max_positions=10, mode="DAILY", max_position_weight=1.0)
    cfg.persist_run_manifest = False
    eng = BacktestEngineV2(store, CAL, config=cfg, source_identity=("UNKNOWN", True))
    eng.add_signal(Signal(strategy_id="S", signal_id="s1", symbol="600000.SH",
                          generated_at=tz_aware(2025, 1, 2, 15, 0), direction=Side.BUY,
                          execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN))
    res = eng.run()
    assert res.ledger.trades == []
    orders = list(res.orders.orders.values())
    assert orders and orders[0].status == OrderStatus.REJECTED
