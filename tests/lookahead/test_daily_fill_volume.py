"""Daily Fill Timing: NEXT_SESSION_OPEN 成交不得依赖当日全天 Volume。"""
import pandas as pd

from src.chanlun_trader.engine.asof import MarketDataStore
from src.chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from src.chanlun_trader.engine.signal import ExecutionPolicy, Side, Signal
from src.chanlun_trader.engine.time_types import tz_aware

from tests.golden._helpers import CAL, make_daily_df


def _store(t_day_volume):
    store = MarketDataStore()
    days = [CAL[0], CAL[1]]
    df = make_daily_df(days=days, open_px=10.0, close_px=10.2, high_px=10.5, low_px=9.8,
                       volume=100_000.0)
    # 前一日 volume 固定 5000；T 日 volume 人为变化
    df.loc[CAL[0], "volume"] = 5000.0
    df.loc[CAL[1], "volume"] = t_day_volume
    df.loc[CAL[0], "prev_close"] = 10.0
    df.loc[CAL[1], "prev_close"] = 10.2
    store.add_daily_raw("600000.SH", df)
    store.add_daily_qfq("600000.SH", df)
    return store


def test_daily_open_fill_does_not_depend_on_future_daily_volume():
    cfg = EngineConfig(initial_cash=1_000_000.0, max_positions=1, max_position_weight=1.0,
                       mode="DAILY", slippage_bps=0.0)
    results = []
    for t_vol in (1_000_000.0, 10.0):
        store = _store(t_vol)
        cfg.persist_run_manifest = False
        eng = BacktestEngineV2(store, [CAL[0], CAL[1]], config=cfg, source_identity=("UNKNOWN", True))
        eng.add_signal(Signal(strategy_id="S", signal_id="s1", symbol="600000.SH",
                              generated_at=tz_aware(2025, 1, 2, 15, 0), direction=Side.BUY,
                              execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN))
        res = eng.run()
        buys = [t for t in res.ledger.trades if t.side == Side.BUY]
        assert len(buys) == 1
        results.append((buys[0].quantity, buys[0].price, buys[0].fill_time))
    # 未来全天 volume 不同，但 09:30 成交的数量/价格/时间必须完全一致
    assert results[0] == results[1]
    # 数量应受前一日 volume 5000 * 10% = 500 约束
    assert results[0][0] == 500
