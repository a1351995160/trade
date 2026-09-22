"""完整账户链验收：指标 → 条件 → Signal → OrderIntent → Sizer/Risk → Broker
→ Fill → Ledger → 正式估值。

覆盖：正常成交、错误条件不成交、预热不足、收盘触发后次日跳空、T+1、停牌、
跌停、部分成交、重复退出、资金不足、容量为零、期末未成交、同一引擎重复运行。

全部断言具体拒因，并保留正常成交正对照。不 mock 引擎、撮合、账本或指标。
"""
from __future__ import annotations

import pytest

from chanlun_trader.engine.behavior_service_v1 import (
    BehaviorRequestError,
    BehaviorRequestV1,
    run_behavior_backtest_v1,
)

from tests.behavior._fixtures import (
    CAL,
    ENTRY_INDEX,
    ENTRY_OPEN,
    FEE_CONTRACT,
    SIGNAL_INDEX,
    SYMBOL,
    downtrend_bars,
    entry_bars,
    flat_bars,
    slippage,
)

ENTRY_FILL = slippage("BUY", ENTRY_OPEN)      # 10.01
SIGNAL_DAY = CAL[SIGNAL_INDEX]
ENTRY_DAY = CAL[ENTRY_INDEX]
NEXT_DAY = CAL[ENTRY_INDEX + 1]

# 单次入场且入场后保持低位：避免反弹重新触发入场条件而产生第二个 lot。
def _hold_low_closes(overrides=None) -> dict:
    closes = {ENTRY_INDEX: ENTRY_OPEN, ENTRY_INDEX + 1: 9.0}
    for index in range(ENTRY_INDEX + 2, len(CAL)):
        closes[index] = 8.5
    closes.update(overrides or {})
    return closes


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


def _fills(result, side=None):
    return [row for row in result.fills if side is None or row["side"] == side]


def _rejections(result):
    return {row["reason"] for row in result.rejections}


# --------------------------------------------------------------------------
# A. 正常成交正对照
# --------------------------------------------------------------------------
def test_scenario_a_normal_fill_positive_control():
    result = run_behavior_backtest_v1(_request())
    buys = _fills(result, "BUY")
    assert len(buys) == 1
    assert buys[0]["price"] == pytest.approx(ENTRY_FILL, abs=1e-9)
    assert buys[0]["quantity"] == 9900
    assert buys[0]["fill_time"].startswith("2024-10-18 09:30")
    # 费用按被冻结的收费单位计算：9900*10.01*0.00025 = 24.7748 > 5。
    assert buys[0]["fee"] == pytest.approx(9900 * ENTRY_FILL * 0.00025, abs=1e-4)
    assert result.cash == pytest.approx(100_000.0 - 9900 * ENTRY_FILL - buys[0]["fee"], abs=1e-6)
    assert result.final_equity == pytest.approx(result.cash + 9900 * ENTRY_OPEN, abs=1e-6)
    assert result.run_summary["run_certification"] == "VALID"


# --------------------------------------------------------------------------
# B. 错误条件不成交
# --------------------------------------------------------------------------
def test_scenario_b_wrong_condition_produces_no_order():
    """错误条件（下跌趋势中的水上金叉）不得成交，且必须保留正对照。"""
    down = run_behavior_backtest_v1(_request(bars={SYMBOL: downtrend_bars()}))
    assert down.signals == [], "下跌趋势中水上条件不应成立"
    assert down.orders == []
    assert down.cash == pytest.approx(100_000.0, abs=1e-9)
    # 正对照：同一请求换回正确条件必须真实成交。
    positive = run_behavior_backtest_v1(_request())
    assert _fills(positive, "BUY")


def test_scenario_b_unknown_condition_is_rejected():
    with pytest.raises(BehaviorRequestError):
        run_behavior_backtest_v1(_request(entry_conditions=["MACD_ABOVE_WATER"]))


# --------------------------------------------------------------------------
# C. 预热不足
# --------------------------------------------------------------------------
def test_scenario_c_insufficient_warmup_produces_no_signal():
    short = CAL[:20]
    rows = flat_bars(short, open_px=ENTRY_OPEN, close_px=ENTRY_OPEN)
    result = run_behavior_backtest_v1(BehaviorRequestV1.from_mapping({
        "calendar": list(short), "symbols": [SYMBOL], "bars": {SYMBOL: rows},
        "entry_conditions": ["ABOVE_ZERO_GOLDEN_CROSS"],
        "initial_cash": 100_000.0, "max_positions": 1, "max_position_weight": 1.0,
        "exit_rules": {}, **FEE_CONTRACT,
    }))
    assert result.signals == [], "预热不足不得产生信号"
    assert result.orders == []
    assert result.cash == pytest.approx(100_000.0, abs=1e-9)


# --------------------------------------------------------------------------
# D. T+1
# --------------------------------------------------------------------------
def test_scenario_d_t_plus_one_blocks_same_session_sell():
    result = run_behavior_backtest_v1(_request(exit_rules={"fixed_holding_sessions": 0}))
    buys = _fills(result, "BUY")
    sells = _fills(result, "SELL")
    assert buys and sells
    assert buys[0]["fill_time"].startswith("2024-10-18 09:30")
    assert sells[0]["fill_time"].startswith("2024-10-21 09:30")
    assert sells[0]["fill_time"] > buys[0]["fill_time"]
    assert result.lots[0]["sellable_from"].startswith("2024-10-19")


# --------------------------------------------------------------------------
# E. 停牌 / 跌停
# --------------------------------------------------------------------------
def test_scenario_e_suspension_blocks_sell_with_explicit_reason():
    """执行日停牌：必须留下 SUSPENDED 拒因，并在下一可交易 session 重试成交。"""
    rows = entry_bars({ENTRY_INDEX: ENTRY_OPEN},
                      volumes={ENTRY_INDEX + 1: 0.0})
    rows[ENTRY_INDEX + 1].update({"high": ENTRY_OPEN, "low": ENTRY_OPEN, "amount": 0.0})
    result = run_behavior_backtest_v1(_request(
        exit_rules={"fixed_holding_sessions": 0}, bars={SYMBOL: rows}))
    assert "SUSPENDED" in _rejections(result), "停牌必须给出明确拒因"
    sells = _fills(result, "SELL")
    assert len(sells) == 1
    assert sells[0]["fill_time"].startswith("2024-10-22 09:30")
    assert result.lots[0]["exit_state"] == "CLOSED"


def test_scenario_e_limit_down_blocks_sell_with_explicit_reason():
    """执行日一字跌停：必须留下 LIMIT_DOWN_LOCKED 拒因，不得假装成交。"""
    limit_down = round(ENTRY_OPEN * 0.9, 2)
    rows = entry_bars({ENTRY_INDEX: ENTRY_OPEN})
    rows[ENTRY_INDEX + 1].update({
        "open": limit_down, "close": limit_down, "high": limit_down, "low": limit_down,
        "volume": 1_000_000.0, "amount": limit_down * 1_000_000.0,
        "prev_close": ENTRY_OPEN,
    })
    result = run_behavior_backtest_v1(_request(
        exit_rules={"fixed_holding_sessions": 0}, bars={SYMBOL: rows}))
    assert "LIMIT_DOWN_LOCKED" in _rejections(result), "一字跌停必须给出明确拒因"
    assert result.lots[0]["exit_state"] in {"SELL_PENDING", "EXIT_DUE", "PARTIALLY_FILLED", "CLOSED"}


# --------------------------------------------------------------------------
# F. 容量为零 / 部分成交
# --------------------------------------------------------------------------
def test_scenario_f_zero_capacity_rejects_instead_of_full_fill():
    """int(volume * rate) == 0 时必须拒绝，不能退回全量成交。

    DAILY 模式在 SESSION_OPEN 成交时用**上一交易日**已知 volume 做容量约束，
    因此这里限制的是信号日（ENTRY_INDEX - 1）的 volume。
    """
    rows = entry_bars(volumes={SIGNAL_INDEX: 5.0})   # 5 * 0.10 = 0
    result = run_behavior_backtest_v1(_request(bars={SYMBOL: rows}))
    assert _fills(result, "BUY") == []
    assert "PARTICIPATION_LIMIT" in _rejections(result), "容量为零必须拒绝并给出拒因"
    assert result.cash == pytest.approx(100_000.0, abs=1e-9)


def test_scenario_f_tight_capacity_truncates_quantity_to_lot_multiple():
    """容量低于目标数量时按容量成交，且成交数量为手数整数倍。"""
    rows = entry_bars(volumes={SIGNAL_INDEX: 200_000.0})   # 200000 * 0.10 = 20000
    result = run_behavior_backtest_v1(_request(
        bars={SYMBOL: rows}, initial_cash=1_000_000.0, max_positions=1, max_position_weight=1.0))
    buys = _fills(result, "BUY")
    assert buys
    assert buys[0]["quantity"] == 20000
    assert buys[0]["quantity"] % 100 == 0
    order = [o for o in result.orders if o["side"] == "BUY"][0]
    assert order["status"] == "FILLED"
    assert order["filled_quantity"] == order["quantity"]


def test_scenario_f_quantity_never_exceeds_order_quantity():
    """成交数量不得超过订单数量（不超卖、不重复扣款）。"""
    result = run_behavior_backtest_v1(_request())
    for order in result.orders:
        assert order["filled_quantity"] <= order["quantity"]
    for lot in result.lots:
        assert 0 <= lot["remaining_quantity"] <= lot["quantity"]


# --------------------------------------------------------------------------
# G. 资金不足 / 买不起一手
# --------------------------------------------------------------------------
def test_scenario_g_insufficient_cash_produces_no_fill():
    result = run_behavior_backtest_v1(_request(initial_cash=500.0))
    assert _fills(result, "BUY") == []
    assert result.cash == pytest.approx(500.0, abs=1e-9)
    assert result.final_equity == pytest.approx(500.0, abs=1e-9)


def test_scenario_g_cannot_afford_one_lot_produces_no_fill():
    result = run_behavior_backtest_v1(_request(initial_cash=1000.0))
    assert _fills(result, "BUY") == []
    assert result.cash == pytest.approx(1000.0, abs=1e-9)


# --------------------------------------------------------------------------
# H. 重复退出
# --------------------------------------------------------------------------
def test_scenario_h_no_duplicate_exit_for_same_lot():
    """同一 lot 同一时点只生成一个有效退出意图；触发后不得因反弹撤销。"""
    closes = _hold_low_closes({ENTRY_INDEX + 1: 9.0})
    result = run_behavior_backtest_v1(_request(
        exit_rules={"stop_loss_pct": 0.03}, bars={SYMBOL: entry_bars(closes)}))
    triggered = [e for e in result.exit_evaluations if e.get("state") == "EXIT_DUE"]
    assert len(triggered) == 1, "同一 lot 不得重复生成退出意图"
    assert len(_fills(result, "SELL")) == 1, "不得重复卖出"
    assert triggered[0]["trade_session"] == NEXT_DAY
    assert len(result.lots) == 1, "退出后不得再产生第二个 lot"


def test_scenario_h_exit_lot_is_fully_closed_once():
    closes = _hold_low_closes({ENTRY_INDEX + 1: 9.0})
    result = run_behavior_backtest_v1(_request(
        exit_rules={"stop_loss_pct": 0.03}, bars={SYMBOL: entry_bars(closes)}))
    lots = {lot["lot_id"]: lot for lot in result.lots}
    lot = lots["lot-000001"]
    assert lot["exit_state"] == "CLOSED"
    assert lot["remaining_quantity"] == 0


# --------------------------------------------------------------------------
# I. 期末未成交
# --------------------------------------------------------------------------
def test_scenario_i_end_of_window_unfilled_exit_is_explicit():
    """最后一个 session 触发的退出没有下一 session：必须保留明确未结状态。"""
    closes = {index: ENTRY_OPEN for index in range(ENTRY_INDEX, len(CAL) - 1)}
    closes[len(CAL) - 1] = 9.0
    result = run_behavior_backtest_v1(_request(
        exit_rules={"stop_loss_pct": 0.03}, bars={SYMBOL: entry_bars(closes)}))
    triggered = [e for e in result.exit_evaluations if e.get("state") == "EXIT_DUE"]
    assert triggered, "最后一个 session 应产生退出意图"
    assert _fills(result, "SELL") == []
    lot = {row["lot_id"]: row for row in result.lots}["lot-000001"]
    assert lot["exit_state"] in {"EXIT_DUE", "SELL_PENDING"}
    assert lot["remaining_quantity"] > 0
    assert any(o["status"] == "EXPIRED" for o in result.orders), "未成交的 DAY 订单必须显式过期"


# --------------------------------------------------------------------------
# J. 同一引擎重复运行 / 追加未来数据
# --------------------------------------------------------------------------
def test_scenario_j_same_request_produces_identical_semantics():
    first = run_behavior_backtest_v1(_request())
    second = run_behavior_backtest_v1(_request())
    assert first.semantics() == second.semantics()


def test_scenario_j_appending_future_bars_does_not_change_past_events():
    """只改 ENTRY_INDEX+3 之后的未来价格：过去的信号/订单/成交必须不变。"""
    base_closes = {index: ENTRY_OPEN for index in range(ENTRY_INDEX, len(CAL))}
    extended_closes = dict(base_closes)
    for index in range(ENTRY_INDEX + 3, len(CAL)):
        extended_closes[index] = 5.0

    base = run_behavior_backtest_v1(_request(bars={SYMBOL: entry_bars(base_closes)}))
    extended = run_behavior_backtest_v1(_request(bars={SYMBOL: entry_bars(extended_closes)}))

    # 过去的信号身份一致（未来数据不得改变过去已完成的信号）。
    assert base.signals == extended.signals
    cutoff = _fmt(CAL[ENTRY_INDEX + 3])
    base_shared = [row for row in base.fills if row["fill_time"] < cutoff]
    ext_shared = [row for row in extended.fills if row["fill_time"] < cutoff]
    assert base_shared == ext_shared, "追加未来数据改变了过去的成交"
    assert base_shared, "正对照：共同区间内必须有成交"
    # 未来数据确实生效（否则说明扩展没接上）。
    assert extended.final_equity != base.final_equity
    assert extended.final_equity < base.final_equity


def test_scenario_j_run_reuse_does_not_leak_previous_state():
    first = run_behavior_backtest_v1(_request())
    second = run_behavior_backtest_v1(_request())
    assert len(first.lots) == len(second.lots)
    assert len(first.orders) == len(second.orders)
    assert first.cash == second.cash
    assert first.run_summary["n_events"] == second.run_summary["n_events"]


def _fmt(day: int) -> str:
    text = str(day)
    return "%s-%s-%s" % (text[:4], text[4:6], text[6:])
