"""Corporate Action Guard：跨越除权除息日的卖出必须标记 TRADE_REALITY_UNSUPPORTED。"""
import pandas as pd

from src.chanlun_trader.engine.asof import MarketDataStore
from src.chanlun_trader.engine.corporate_action import CorporateActionGuard
from src.chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from src.chanlun_trader.engine.signal import ExecutionPolicy, Side, Signal
from src.chanlun_trader.engine.time_types import tz_aware

from tests.golden._helpers import make_daily_df, CAL


def test_trade_crossing_corporate_action_is_flagged():
    store = MarketDataStore()
    df = make_daily_df(days=[CAL[0], CAL[1], CAL[2]], open_px=10.0, close_px=10.2,
                       high_px=10.5, low_px=9.8, volume=10_000_000.0)
    store.add_daily_raw("600000.SH", df)
    store.add_daily_qfq("600000.SH", df)

    guard = CorporateActionGuard()
    guard.add_event("600000.SH", CAL[1] + 1)  # 除权除息日在买入后、卖出前

    cfg = EngineConfig(initial_cash=1_000_000.0, max_positions=10, mode="DAILY",
                       corporate_action_guard=guard)
    eng = BacktestEngineV2(store, [CAL[0], CAL[1], CAL[2]], config=cfg, source_identity=("SYNTHETIC_EXECUTION_INPUT_REVIEW", False))
    eng.add_signal(Signal(strategy_id="S", signal_id="buy", symbol="600000.SH",
                          generated_at=tz_aware(2025, 1, 2, 15, 0), direction=Side.BUY,
                          execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN))
    eng.add_signal(Signal(strategy_id="S", signal_id="sell", symbol="600000.SH",
                          generated_at=tz_aware(2025, 1, 3, 15, 0), direction=Side.SELL,
                          execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN))
    res = eng.run()
    sells = [t for t in res.ledger.trades if t.side == Side.SELL]
    assert len(sells) == 1
    assert sells[0].reality_flag == "TRADE_REALITY_UNSUPPORTED"
    assert len(res.ledger.valid_trades) == 1  # 只有买入计入有效交易


def test_trade_without_corporate_action_stays_ok():
    store = MarketDataStore()
    df = make_daily_df(days=[CAL[0], CAL[1], CAL[2]], open_px=10.0, close_px=10.2,
                       high_px=10.5, low_px=9.8, volume=10_000_000.0)
    store.add_daily_raw("600000.SH", df)
    store.add_daily_qfq("600000.SH", df)
    cfg = EngineConfig(initial_cash=1_000_000.0, max_positions=10, mode="DAILY",
                       corporate_action_guard=CorporateActionGuard())
    eng = BacktestEngineV2(store, [CAL[0], CAL[1], CAL[2]], config=cfg, source_identity=("SYNTHETIC_EXECUTION_INPUT_REVIEW", False))
    eng.add_signal(Signal(strategy_id="S", signal_id="buy", symbol="600000.SH",
                          generated_at=tz_aware(2025, 1, 2, 15, 0), direction=Side.BUY,
                          execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN))
    eng.add_signal(Signal(strategy_id="S", signal_id="sell", symbol="600000.SH",
                          generated_at=tz_aware(2025, 1, 3, 15, 0), direction=Side.SELL,
                          execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN))
    res = eng.run()
    sells = [t for t in res.ledger.trades if t.side == Side.SELL]
    assert sells[0].reality_flag == "OK"
    assert len(res.ledger.valid_trades) == 2
