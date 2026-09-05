"""Legacy V1 -> V2 适配与确定性回归。"""
import pandas as pd

from src.chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from src.chanlun_trader.engine.legacy import LegacySignalAdapter
from src.chanlun_trader.engine.signal import ExecutionPolicy, Side

from tests.golden._helpers import CAL, make_store


def test_legacy_signal_adapter_maps_to_next_session_open():
    class V1Sig:
        code = "600000.SH"
        signal_date = 20250102
        signal_type = "LEGACY"
        direction = "BUY"
        stop_low = 9.5
        score = 0.7
    s = LegacySignalAdapter.from_v1(V1Sig())
    assert s.symbol == "600000.SH"
    assert s.generated_at.strftime("%Y%m%d %H:%M") == "20250102 15:00"
    assert s.execution_policy == ExecutionPolicy.NEXT_SESSION_OPEN
    assert s.direction == Side.BUY
    assert s.metadata["legacy_signal_date"] == 20250102


def test_engine_determinism_same_inputs_same_outputs():
    def run_once():
        store = make_store()
        cfg = EngineConfig(initial_cash=1_000_000.0, max_positions=10, mode="DAILY")
        eng = BacktestEngineV2(store, CAL, config=cfg, seed=42)
        from src.chanlun_trader.engine.signal import Signal
        from src.chanlun_trader.engine.time_types import tz_aware
        eng.add_signals([
            Signal(strategy_id="REG", signal_id=f"r{i}", symbol="600000.SH",
                   generated_at=tz_aware(2025, 1, 2, 15, 0), direction=Side.BUY,
                   execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN)
            for i in range(2)
        ])
        res = eng.run()
        return (res.summary()["final_equity"], [t.fill_time for t in res.ledger.trades],
                [t.price for t in res.ledger.trades])
    a = run_once()
    b = run_once()
    assert a == b
