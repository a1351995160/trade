"""UniverseService + RiskManager universe gate tests."""
from src.chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from src.chanlun_trader.engine.signal import ExecutionPolicy, Side, Signal
from src.chanlun_trader.engine.time_types import tz_aware
from src.chanlun_trader.engine.universe import UniverseService

from tests.golden._helpers import CAL, make_store


def test_universe_is_eligible_snapshot():
    u = UniverseService()
    u.set_universe(CAL[0], {"600000.SH"})
    u.set_universe(CAL[2], {"000001.SZ"})
    assert u.is_eligible("600000.SH", tz_aware(2025, 1, 2, 10, 0))
    assert u.is_eligible("000001.SZ", tz_aware(2025, 1, 6, 10, 0))
    assert not u.is_eligible("600000.SH", tz_aware(2025, 1, 6, 10, 0))
    assert u.snapshot(tz_aware(2025, 1, 6, 10, 0)) == {"000001.SZ"}


def test_risk_universe_gate_rejects_non_member():
    u = UniverseService()
    u.set_universe(CAL[0], {"000001.SZ"})
    store = make_store()
    cfg = EngineConfig(initial_cash=1_000_000.0, max_positions=10, mode="DAILY")
    eng = BacktestEngineV2(store, CAL, config=cfg, universe=u)
    eng.add_signal(Signal(strategy_id="S", signal_id="s1", symbol="600000.SH",
                          generated_at=tz_aware(2025, 1, 2, 15, 0), direction=Side.BUY,
                          execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN))
    res = eng.run()
    assert res.ledger.trades == []
    orders = list(res.orders.orders.values())
    assert orders and orders[0].status.value in ("REJECTED",)
