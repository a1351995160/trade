"""日线退出规则（BT_DAILY_EXIT_V1）验收：成本锚、触发时点、订单与账本影响。

关键断言（对应验收要求）：
- 成本止损基于**实际成交价**，不是信号结构低点；
- 固定止盈 / 移动止损**真的改变订单与账本**（对比关闭时）；
- 触发在收盘、执行在下一 session 开盘；
- 未支持类型（盘中触价等）明确拒绝；
- 跳空按真实执行价记账，不把亏损封顶在设定比例。
"""
from __future__ import annotations

import pandas as pd
import pytest

from chanlun_trader.engine.asof import MarketDataStore
from chanlun_trader.engine.behavior_service_v1 import (
    BehaviorRequestError,
    BehaviorRequestV1,
    run_behavior_backtest_v1,
)
from chanlun_trader.engine.daily_exit_v1 import (
    DailyExitEvaluatorV1,
    DailyExitRuleSetV1,
    ExitConfigError,
)
from chanlun_trader.engine.position import PositionLot
from chanlun_trader.engine.time_types import tz_aware

from tests.behavior._fixtures import (
    CAL,
    ENTRY_INDEX,
    ENTRY_OPEN,
    FEE_CONTRACT,
    SIGNAL_INDEX,
    SYMBOL,
    entry_bars,
    flat_bars,
    slippage,
)

ENTRY_FILL = slippage("BUY", ENTRY_OPEN)   # 10.01
SIGNAL_DAY = CAL[SIGNAL_INDEX]
ENTRY_DAY = CAL[ENTRY_INDEX]
NEXT_DAY = CAL[ENTRY_INDEX + 1]


def _request(**overrides) -> BehaviorRequestV1:
    payload = {
        "calendar": list(CAL),
        "symbols": [SYMBOL],
        "bars": {SYMBOL: entry_bars()},
        "entry_conditions": ["ABOVE_ZERO_GOLDEN_CROSS"],
        "initial_cash": 100_000.0,
        "max_positions": 1,
        "max_position_weight": 1.0,
        "exit_rules": {},
        **FEE_CONTRACT,
    }
    payload.update(overrides)
    return BehaviorRequestV1.from_mapping(payload)


def _run(rules: dict, **overrides):
    return run_behavior_backtest_v1(_request(exit_rules=rules, **overrides))


def _fills(result, side=None):
    return [row for row in result.fills if side is None or row["side"] == side]


def _exit_events(result):
    return [row for row in result.exit_evaluations if row.get("state") == "EXIT_DUE"]


def _lot_events(result, lot_id: str = "lot-000001"):
    return [row for row in result.exit_evaluations if row.get("lot_id") == lot_id]


# --------------------------------------------------------------------------
# 0. 正对照：正常成交 + 入场时点
# --------------------------------------------------------------------------
def test_positive_control_signal_then_next_session_open_fill():
    result = _run({})
    assert [s["signal_type"] for s in result.signals] == ["ABOVE_ZERO_GOLDEN_CROSS"]
    assert result.signals[0]["generated_at"].startswith("2024-10-17")
    buys = _fills(result, "BUY")
    assert len(buys) == 1
    assert buys[0]["fill_time"].startswith("2024-10-18 09:30")
    assert buys[0]["price"] == pytest.approx(ENTRY_FILL, abs=1e-9)
    assert buys[0]["quantity"] == 9900
    lot = result.lots[0]
    assert lot["entry_session"] == ENTRY_DAY
    assert lot["entry_session_index"] == ENTRY_INDEX
    assert lot["sellable_from"].startswith("2024-10-19")


# --------------------------------------------------------------------------
# 1. 成本锚 = 实际成交价（不是信号结构低点）
# --------------------------------------------------------------------------
def test_cost_stop_anchor_is_actual_fill_price_not_structure_low():
    """成交价 10.01、成本止损 3% -> 线 9.7097；结构低点远低于此，不得被当作锚。"""
    stop_line = ENTRY_FILL * 0.97
    closes = {ENTRY_INDEX: ENTRY_OPEN, ENTRY_INDEX + 1: 9.69, ENTRY_INDEX + 2: 9.60}
    result = _run({"stop_loss_pct": 0.03}, bars={SYMBOL: entry_bars(closes)})

    events = _exit_events(result)
    assert len(events) == 1
    event = events[0]
    assert event["trade_session"] == NEXT_DAY
    assert event["anchor"] == pytest.approx(ENTRY_FILL, abs=1e-12)
    assert event["close"] <= stop_line
    assert event["primary_reason"] == "EXIT_FIXED_COST_STOP"
    # 锚必须等于实际成交价，绝不等于任何结构价。
    assert event["anchor"] > 9.9
    sells = _fills(result, "SELL")
    assert len(sells) == 1
    assert sells[0]["fill_time"].startswith("2024-10-22 09:30")


def test_cost_stop_does_not_trigger_when_close_stays_above_line():
    """反例：收盘始终高于止损线时不得产生退出意图。"""
    result = _run({"stop_loss_pct": 0.03},
                  bars={SYMBOL: entry_bars({ENTRY_INDEX: ENTRY_OPEN})})
    assert _fills(result, "BUY")
    assert _fills(result, "SELL") == []
    assert _exit_events(result) == []
    assert all(event["state"] == "OPEN" for event in _lot_events(result))


def test_anchor_uses_lot_entry_price_not_latest_average():
    result = _run({"stop_loss_pct": 0.03},
                  bars={SYMBOL: entry_bars({ENTRY_INDEX: ENTRY_OPEN})})
    lot = result.lots[0]
    assert lot["entry_price"] == pytest.approx(ENTRY_FILL, abs=1e-12)
    events = _lot_events(result)
    assert events
    assert all(event["anchor"] == pytest.approx(lot["entry_price"], abs=1e-12) for event in events)


# --------------------------------------------------------------------------
# 2. 固定止盈真的影响订单与账本
# --------------------------------------------------------------------------
def test_take_profit_changes_orders_and_ledger():
    closes = {ENTRY_INDEX: ENTRY_OPEN, ENTRY_INDEX + 1: 10.30, ENTRY_INDEX + 2: 10.40}
    baseline = _run({}, bars={SYMBOL: entry_bars(closes)})
    with_tp = _run({"take_profit_pct": 0.02}, bars={SYMBOL: entry_bars(closes)})

    assert _fills(baseline, "SELL") == []
    sells = _fills(with_tp, "SELL")
    assert len(sells) == 1
    events = _exit_events(with_tp)
    assert events[0]["primary_reason"] == "EXIT_FIXED_TAKE_PROFIT"
    assert events[0]["close"] >= ENTRY_FILL * 1.02
    assert events[0]["trade_session"] == NEXT_DAY
    assert sells[0]["fill_time"].startswith("2024-10-22 09:30")
    # 账本真的不同（不只是字段存在）。
    assert with_tp.cash != baseline.cash
    assert with_tp.final_equity != baseline.final_equity
    assert len(with_tp.orders) > len(baseline.orders)


def test_take_profit_is_price_percentage_not_net_profit_threshold():
    contract = DailyExitRuleSetV1(take_profit_pct=0.02).to_dict()
    assert contract["take_profit_pct"] == 0.02
    assert contract["cost_anchor"] == "ACTUAL_LOT_ENTRY_FILL_PRICE_EXCLUDING_COMMISSION"
    assert contract["trigger_price_field"] == "COMPLETED_DAILY_CLOSE_RAW"
    assert contract["earliest_execution"] == "NEXT_SESSION_OPEN"
    assert contract["execution_mode"] == "CLOSE_CONFIRM_NEXT_SESSION_OPEN"


# --------------------------------------------------------------------------
# 3. 移动止损：未激活 / 激活 / 抬线 / 回撤
# --------------------------------------------------------------------------
def test_trailing_stop_inactive_then_active_then_tightens():
    closes = {
        ENTRY_INDEX: ENTRY_OPEN, ENTRY_INDEX + 1: 10.2, ENTRY_INDEX + 2: 10.8,
        ENTRY_INDEX + 3: 11.4, ENTRY_INDEX + 4: 12.0, ENTRY_INDEX + 5: 11.9,
    }
    result = _run({"trailing_activate_pct": 0.05, "trailing_pct": 0.08},
                  bars={SYMBOL: entry_bars(closes)})
    events = _lot_events(result)
    assert len(events) >= 4

    first = events[0]
    assert first["trailing_activated"] is False
    assert first["trailing_line"] == 0.0
    assert "EXIT_TRAILING_STOP" not in first.get("triggered", [])

    activated = [event for event in events if event.get("trailing_activated")]
    assert activated, "移动止损从未激活"
    lines = [event["trailing_line"] for event in activated]
    assert lines == sorted(lines), "移动线出现放松（违反同一 lot 只收紧不放松）"
    for event in activated:
        assert event["trailing_line"] == pytest.approx(event["peak_close"] * 0.92, abs=1e-6)
    for event in events:
        if not event.get("trailing_activated"):
            assert "EXIT_TRAILING_STOP" not in event.get("triggered", [])


def test_trailing_stop_triggers_on_close_below_line():
    closes = {
        ENTRY_INDEX: ENTRY_OPEN, ENTRY_INDEX + 1: 11.0, ENTRY_INDEX + 2: 12.0,
        ENTRY_INDEX + 3: 12.5, ENTRY_INDEX + 4: 11.0,
    }
    result = _run({"trailing_activate_pct": 0.05, "trailing_pct": 0.08},
                  bars={SYMBOL: entry_bars(closes)})
    events = _exit_events(result)
    assert events, "回撤跌破移动线时必须触发"
    assert events[0]["primary_reason"] == "EXIT_TRAILING_STOP"
    assert events[0]["close"] <= events[0]["trailing_line"]
    assert _fills(result, "SELL"), "移动止损必须真的产生卖出订单"


def test_trailing_stop_disabled_has_no_effect():
    closes = {
        ENTRY_INDEX: ENTRY_OPEN, ENTRY_INDEX + 1: 11.0, ENTRY_INDEX + 2: 12.0,
        ENTRY_INDEX + 3: 12.5, ENTRY_INDEX + 4: 11.0,
    }
    result = _run({}, bars={SYMBOL: entry_bars(closes)})
    assert not [event for event in result.exit_evaluations if event.get("trailing_activated")]
    assert _fills(result, "SELL") == []


def test_trailing_partial_sell_does_not_reset_peak():
    """部分卖出不重置剩余 lot 的峰值或激活状态。"""
    days = CAL[:4]
    rows = flat_bars(days, open_px=10.0, close_px=10.0, overrides={
        days[1]: {"open": 10.0, "close": 11.0, "high": 11.1, "low": 9.9},
        days[2]: {"open": 11.0, "close": 12.0, "high": 12.1, "low": 10.9},
        days[3]: {"open": 12.0, "close": 11.0, "high": 12.1, "low": 10.9},
    })
    store = MarketDataStore(feature_price_mode="raw")
    store.add_daily_raw(SYMBOL, pd.DataFrame(rows).set_index("date"))

    evaluator = DailyExitEvaluatorV1("C", "C", DailyExitRuleSetV1(
        trailing_activate_pct=0.05, trailing_pct=0.08))
    lot = PositionLot(
        lot_id="lot-x", position_id="pos-x", symbol=SYMBOL, strategy_id="C",
        buy_time=tz_aware(2025, 1, 2, 9, 30), quantity=1000, remaining_quantity=1000,
        cost=10.0 * 1000, sellable_from=tz_aware(2025, 1, 3, 9, 30),
        entry_session=days[0], entry_session_index=0, entry_price=10.0,
    )
    index_of = {day: i for i, day in enumerate(days)}
    evaluator.evaluate([lot], days[1], 1, store, session_index_of=index_of)
    assert evaluator.trailing["lot-x"].peak_close == pytest.approx(11.0)

    lot.remaining_quantity = 600
    evaluator.evaluate([lot], days[2], 2, store, session_index_of=index_of)
    state = evaluator.trailing["lot-x"]
    assert state.peak_close == pytest.approx(12.0)
    assert state.activated is True
    assert state.line == pytest.approx(12.0 * 0.92, abs=1e-9)


# --------------------------------------------------------------------------
# 4. 固定持有：按独立交易日历的 entry_session_index
# --------------------------------------------------------------------------
def test_fixed_hold_uses_trading_session_index():
    result = _run({"fixed_holding_sessions": 3},
                  bars={SYMBOL: entry_bars({ENTRY_INDEX: ENTRY_OPEN})})
    buys = _fills(result, "BUY")
    assert buys and buys[0]["fill_time"].startswith("2024-10-18")
    events = _exit_events(result)
    assert events, "固定持有未触发"
    assert events[0]["primary_reason"] == "EXIT_FIXED_HOLD"
    assert events[0]["trade_session"] == CAL[ENTRY_INDEX + 3]
    assert _fills(result, "SELL"), "到期后必须真实卖出"


def test_fixed_hold_zero_sessions_exits_next_session():
    result = _run({"fixed_holding_sessions": 0},
                  bars={SYMBOL: entry_bars({ENTRY_INDEX: ENTRY_OPEN})})
    events = _exit_events(result)
    assert events
    assert events[0]["trade_session"] == ENTRY_DAY
    sells = _fills(result, "SELL")
    assert sells and sells[0]["fill_time"].startswith("2024-10-21 09:30")


# --------------------------------------------------------------------------
# 5. 明确拒绝未支持类型
# --------------------------------------------------------------------------
@pytest.mark.parametrize("exit_type", [
    "INTRADAY_TOUCH_STOP", "TICK_LEVEL_EXIT", "MULTI_TIMEFRAME_EXIT",
    "STRUCTURE_STOP", "INDICATOR_EXIT",
])
def test_unsupported_exit_types_are_rejected(exit_type):
    with pytest.raises(ExitConfigError):
        DailyExitRuleSetV1(exit_types=(exit_type,))


def test_unknown_exit_type_is_rejected():
    with pytest.raises(ExitConfigError):
        DailyExitRuleSetV1(exit_types=("SOMETHING_ELSE",))


def test_invalid_exit_parameters_are_rejected():
    with pytest.raises(ExitConfigError):
        DailyExitRuleSetV1(stop_loss_pct=0.0)
    with pytest.raises(ExitConfigError):
        DailyExitRuleSetV1(stop_loss_pct=1.0)
    with pytest.raises(ExitConfigError):
        DailyExitRuleSetV1(trailing_pct=0.05)
    with pytest.raises(ExitConfigError):
        DailyExitRuleSetV1(fixed_holding_sessions=-1)


def test_unknown_request_field_is_rejected_not_silently_ignored():
    with pytest.raises(BehaviorRequestError):
        BehaviorRequestV1.from_mapping({
            "calendar": list(CAL), "symbols": [SYMBOL],
            "bars": {SYMBOL: entry_bars()},
            "exit_rules": {},
            "intraday_touch_stop_pct": 0.03,
        })


def test_unsupported_mode_is_rejected_not_silently_downgraded():
    for mode in ("BT_BEHAVIOR_INTRADAY_V1", "BT_BEHAVIOR_TICK_V1", "BT_BEHAVIOR_MULTI_TIMEFRAME_V1"):
        with pytest.raises(BehaviorRequestError):
            run_behavior_backtest_v1(BehaviorRequestV1.from_mapping({
                "mode": mode, "calendar": list(CAL), "symbols": [SYMBOL],
                "bars": {SYMBOL: entry_bars()}, "exit_rules": {},
            }))


# --------------------------------------------------------------------------
# 6. 跳空 / 盘中触价
# --------------------------------------------------------------------------
def test_gap_down_executes_at_real_next_open_not_capped_at_stop_pct():
    """收盘跌破止损线 -> 次日跳空低开：按真实开盘价成交，不封顶在 3%。"""
    stop_line = ENTRY_FILL * 0.97
    closes = {ENTRY_INDEX: ENTRY_OPEN, ENTRY_INDEX + 1: 9.60}
    bars = entry_bars(closes, opens={ENTRY_INDEX + 2: 9.20})
    result = _run({"stop_loss_pct": 0.03}, bars={SYMBOL: bars})
    buys = _fills(result, "BUY")
    sells = _fills(result, "SELL")
    assert buys and sells
    sell_price = sells[0]["price"]
    assert sell_price == pytest.approx(slippage("SELL", 9.20), abs=1e-9)
    assert sell_price < stop_line
    assert sell_price / buys[0]["price"] - 1 < -0.03


def test_high_low_touch_without_close_trigger_produces_no_exit():
    """高低价触及但收盘未触及：CLOSE_CONFIRM 模式不得伪造触价成交。"""
    closes = {ENTRY_INDEX: ENTRY_OPEN, ENTRY_INDEX + 1: 10.60}
    bars = entry_bars(closes, lows={ENTRY_INDEX + 1: 9.0})
    result = _run({"stop_loss_pct": 0.03}, bars={SYMBOL: bars})
    assert _fills(result, "SELL") == []
    assert _exit_events(result) == []
    # 盘中低点确实已跌破止损线：证明"未触发"来自 CLOSE_CONFIRM 口径。
    event = _lot_events(result)[0]
    assert event["close"] > ENTRY_FILL * 0.97
    assert bars[ENTRY_INDEX + 1]["low"] < ENTRY_FILL * 0.97


# --------------------------------------------------------------------------
# 7. 组合：多条件命中时保留全部原因，主展示原因按固定优先级
# --------------------------------------------------------------------------
def test_multiple_hits_preserve_all_reasons_with_fixed_primary_priority():
    closes = {ENTRY_INDEX: ENTRY_OPEN, ENTRY_INDEX + 1: 9.0}
    result = _run({
        "stop_loss_pct": 0.03, "take_profit_pct": 0.02,
        "trailing_activate_pct": 0.05, "trailing_pct": 0.08,
        "fixed_holding_sessions": 1,
    }, bars={SYMBOL: entry_bars(closes)})
    events = _exit_events(result)
    assert events
    triggered = events[0]["triggered"]
    assert "EXIT_FIXED_COST_STOP" in triggered
    assert "EXIT_FIXED_HOLD" in triggered
    assert events[0]["primary_reason"] == "EXIT_FIXED_COST_STOP"
    assert events[0]["trade_session"] == NEXT_DAY
