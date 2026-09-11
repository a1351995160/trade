"""Engine-corrected portfolio exit golden tests."""
from __future__ import annotations

import pandas as pd

from src.chanlun_trader.engine.asof import MarketDataStore
from src.chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from src.chanlun_trader.engine.portfolio_exit import PortfolioExitEvaluatorV1
from src.chanlun_trader.engine.signal import ExecutionPolicy, Side, Signal
from src.chanlun_trader.engine.time_types import tz_aware
from src.chanlun_trader.engine.position import PositionLot


CAL = [20250102, 20250103, 20250106, 20250107, 20250108, 20250109, 20250110, 20250113, 20250114, 20250115]


def _store(*, blocked_days: set[int] | None = None, limit_down_days: set[int] | None = None) -> MarketDataStore:
    blocked_days = blocked_days or set()
    limit_down_days = limit_down_days or set()
    rows = []
    for day in CAL:
        is_blocked = day in blocked_days
        is_limit_down = day in limit_down_days
        rows.append({
            "open": 9.0 if is_limit_down else 10.0,
            "high": 9.0 if is_limit_down else 10.2,
            "low": 9.0 if is_limit_down else 9.8,
            "close": 9.0 if is_limit_down else 10.1,
            "volume": 0.0 if is_blocked else 1_000_000.0,
            "amount": 0.0 if is_blocked else 10_000_000.0,
            "prev_close": 10.0,
        })
    frame = pd.DataFrame(rows, index=pd.Index(CAL, name="date"))
    store = MarketDataStore(feature_price_mode="raw")
    store.add_daily_raw("600000.SH", frame)
    store.add_daily_qfq("600000.SH", frame)
    return store


def _buy(day: int, strategy: str = "CAND") -> Signal:
    text = str(day)
    return Signal(
        strategy_id=strategy,
        signal_id=f"BUY-{day}",
        symbol="600000.SH",
        generated_at=tz_aware(int(text[:4]), int(text[4:6]), int(text[6:]), 15, 0),
        direction=Side.BUY,
        execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
    )


def _fixed_exit_fn(calendar: list[int], fixed: int, *, structure_value: dict[int, float] | None = None):
    evaluator = PortfolioExitEvaluatorV1("CAND", "CAND", {
        "exit_type": "FIXED_HOLD",
        "fixed_holding_sessions": fixed,
        "factor_conditions": [],
        "logic": "OR",
    })
    positions = {day: index for index, day in enumerate(calendar)}

    def fn(_view, ts, day, ledger):
        if ts.hour != 15:
            return []
        return evaluator.evaluate(
            ledger.lots.values(), day, positions[day],
            {"600000.SH": {"values": {"TRIGGER": (structure_value or {}).get(day, 1.0)}, "available_at": ts}}, ts,
        )

    return fn


def _engine(store: MarketDataStore, *, max_positions: int = 2) -> BacktestEngineV2:
    return BacktestEngineV2(store, CAL, config=EngineConfig(
        initial_cash=100_000.0,
        max_positions=max_positions,
        max_position_weight=1.0 / max_positions,
        mode="DAILY",
        enable_index_filter=False,
        index_filter_enabled=False,
        max_holding_days=0,
        persist_run_manifest=False,
    ), source_identity=("UNKNOWN", True))


def test_fixed_hold_due_does_not_require_future_alpha_signal():
    engine = _engine(_store(), max_positions=1)
    engine.add_signal(_buy(CAL[0]))
    result = engine.run(exit_fn=_fixed_exit_fn(CAL, 3))
    sells = [trade for trade in result.ledger.valid_trades if trade.side == Side.SELL]
    assert len(sells) == 1
    assert sells[0].fill_time.strftime("%Y%m%d") == str(CAL[5])
    assert result.ledger.lots[sells[0].lot_id].exit_state == "CLOSED"


def test_suspension_sell_remains_pending_and_retries_until_fill():
    engine = _engine(_store(blocked_days={CAL[5], CAL[6]}), max_positions=1)
    engine.add_signal(_buy(CAL[0]))
    result = engine.run(exit_fn=_fixed_exit_fn(CAL, 3))
    sells = [trade for trade in result.ledger.valid_trades if trade.side == Side.SELL]
    rejected = [event for event in result.event_log if event.event_type == "ORDER_EVENT" and getattr(event.order_status, "value", "") == "REJECTED" and event.symbol == "600000.SH"]
    assert len(sells) == 1
    assert sells[0].fill_time.strftime("%Y%m%d") == str(CAL[7])
    assert len(rejected) == 2
    assert result.ledger.lots[sells[0].lot_id].exit_state == "CLOSED"


def test_limit_down_sell_remains_pending_and_retries_until_fill():
    engine = _engine(_store(limit_down_days={CAL[5], CAL[6]}), max_positions=1)
    engine.add_signal(_buy(CAL[0]))
    result = engine.run(exit_fn=_fixed_exit_fn(CAL, 3))
    sells = [trade for trade in result.ledger.valid_trades if trade.side == Side.SELL]
    rejected = [event for event in result.event_log if event.event_type == "ORDER_EVENT" and getattr(event.order_status, "value", "") == "REJECTED" and event.symbol == "600000.SH"]
    assert len(sells) == 1
    assert sells[0].fill_time.strftime("%Y%m%d") == str(CAL[7])
    assert len(rejected) == 2
    assert all("LIMIT_DOWN" in event.message for event in rejected)


def test_structure_invalidation_is_early_but_ceiling_is_preserved():
    calendar_index = {day: index for index, day in enumerate(CAL)}
    evaluator = PortfolioExitEvaluatorV1("CAND", "CAND", {
        "exit_type": "STRUCTURE_INVALIDATION",
        "fixed_holding_sessions": 5,
        "factor_conditions": [{"factor_id": "TRIGGER", "operator": "LT", "value": 0.0}],
        "logic": "OR",
    })
    early = PositionLot("lot-early", "pos", "600000.SH", "CAND", tz_aware(2025, 1, 3, 9, 30), 100, 100, 1005.0, tz_aware(2025, 1, 4, 9, 30), entry_session_index=1)
    decisions = evaluator.evaluate([early], CAL[3], calendar_index[CAL[3]], {"600000.SH": {"values": {"TRIGGER": -1.0}, "available_at": tz_aware(2025, 1, 7, 15, 0)}}, tz_aware(2025, 1, 7, 15, 0))
    assert decisions[0].reason_code == "EXIT_DUE_STRUCTURE_INVALIDATION"
    intact = PositionLot("lot-intact", "pos", "600000.SH", "CAND", tz_aware(2025, 1, 3, 9, 30), 100, 100, 1005.0, tz_aware(2025, 1, 4, 9, 30), entry_session_index=1)
    decisions = evaluator.evaluate([intact], CAL[6], calendar_index[CAL[6]], {"600000.SH": {"values": {"TRIGGER": 1.0}, "available_at": tz_aware(2025, 1, 10, 15, 0)}}, tz_aware(2025, 1, 10, 15, 0))
    assert decisions[0].reason_code == "EXIT_DUE_FIXED_HOLD"


def test_fifo_reentry_keeps_each_lot_due_session():
    engine = _engine(_store(), max_positions=2)
    engine.add_signal(_buy(CAL[0]))
    engine.add_signal(_buy(CAL[1]))
    result = engine.run(exit_fn=_fixed_exit_fn(CAL, 3))
    sells = sorted((trade for trade in result.ledger.valid_trades if trade.side == Side.SELL), key=lambda trade: trade.fill_time)
    assert len(sells) == 2
    assert sells[0].lot_id != sells[1].lot_id
    assert sells[0].fill_time.strftime("%Y%m%d") == str(CAL[5])
    assert sells[1].fill_time.strftime("%Y%m%d") == str(CAL[6])


def test_t_plus_one_blocks_same_session_exit_but_allows_next_session():
    engine = _engine(_store(), max_positions=1)
    engine.add_signal(_buy(CAL[0]))
    result = engine.run(exit_fn=_fixed_exit_fn(CAL, 0))
    buys = [trade for trade in result.ledger.valid_trades if trade.side == Side.BUY]
    sells = [trade for trade in result.ledger.valid_trades if trade.side == Side.SELL]
    assert buys and sells
    assert sells[0].fill_time > buys[0].fill_time
    assert sells[0].fill_time.strftime("%Y%m%d") == str(CAL[2])


def test_exit_state_is_portfolio_local():
    first = _engine(_store(), max_positions=1)
    second = _engine(_store(), max_positions=1)
    first.add_signal(_buy(CAL[0], "CAND"))
    first_result = first.run(exit_fn=_fixed_exit_fn(CAL, 3))
    second_result = second.run(exit_fn=_fixed_exit_fn(CAL, 3))
    assert any(trade.side == Side.SELL for trade in first_result.ledger.valid_trades)
    assert not second_result.ledger.trades
