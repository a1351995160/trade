"""独立手算账本核对（不使用被测账本自身的输出作为预期值）。

样例（附件第 10 节）：初始 10000，买 100 股 @10、买佣金 5；另一合法 session
卖 100 股 @11、卖佣金 5、合成印花税 0.0005、无滑点、无其他费用。

预期：买后现金 8995；卖出净回款 1094.45；期末现金 10089.45；净利润 89.45。
该费率只是合成测试合同，不作跨历史真实税率声明。
"""
from __future__ import annotations

import pytest

from chanlun_trader.engine.behavior_service_v1 import (
    BehaviorRequestV1,
    run_behavior_backtest_v1,
)

from tests.behavior._fixtures import (
    CAL,
    ENTRY_INDEX,
    ENTRY_OPEN,
    SYMBOL,
    entry_bars,
    fee,
    manual_account_case,
    slippage,
)

FEE_CONTRACT_FULL = {
    "commission_rate": 0.00025,
    "min_commission": 5.0,
    "stamp_tax_rate": 0.0005,
    "slippage_bps": 0.001,
}


def _run(rules: dict, closes: dict = None, *, opens=None, initial_cash=100_000.0):
    return run_behavior_backtest_v1(BehaviorRequestV1.from_mapping({
        "calendar": list(CAL), "symbols": [SYMBOL],
        "bars": {SYMBOL: entry_bars(closes or {ENTRY_INDEX: ENTRY_OPEN}, opens=opens)},
        "entry_conditions": ["ABOVE_ZERO_GOLDEN_CROSS"],
        "initial_cash": initial_cash, "max_positions": 1, "max_position_weight": 1.0,
        "exit_rules": rules,
        **FEE_CONTRACT_FULL,
    }))


def test_manual_account_case_expected_values():
    case = manual_account_case()
    assert case["expected_cash_after_buy"] == pytest.approx(8995.0, abs=1e-9)
    assert case["expected_sell_proceeds"] == pytest.approx(1094.45, abs=1e-9)
    assert case["expected_final_cash"] == pytest.approx(10089.45, abs=1e-9)
    assert case["expected_net_profit"] == pytest.approx(89.45, abs=1e-9)


def test_fee_model_matches_independent_formula():
    """逐项核对费用：佣金（含最低 5 元）与卖出印花税。"""
    buy = fee("BUY", 100, 10.0, {"commission_rate": 0.0, "min_commission": 5.0})
    assert buy["commission"] == 5.0
    assert buy["stamp_tax"] == 0.0
    assert buy["total_fee"] == 5.0

    sell = fee("SELL", 100, 11.0,
               {"commission_rate": 0.0, "min_commission": 5.0, "stamp_tax_rate": 0.0005})
    assert sell["commission"] == 5.0
    assert sell["stamp_tax"] == pytest.approx(0.55, abs=1e-9)
    assert sell["total_fee"] == pytest.approx(5.55, abs=1e-9)

    big = fee("SELL", 10_000, 10.0, {"commission_rate": 0.00025, "min_commission": 5.0,
                                     "stamp_tax_rate": 0.0005})
    assert big["commission"] == 25.0
    assert big["stamp_tax"] == 50.0
    assert big["total_fee"] == 75.0


def test_slippage_matches_independent_formula():
    assert slippage("BUY", 10.0) == pytest.approx(10.01, abs=1e-9)
    assert slippage("SELL", 10.0) == pytest.approx(9.99, abs=1e-9)


def test_end_to_end_account_matches_independent_expectation():
    """端到端单一 lot：现金 / 持仓 / 费用 / 收益全部与独立手算一致。"""
    buy_price = slippage("BUY", ENTRY_OPEN)          # 10.01
    sell_reference = 11.0
    sell_price = slippage("SELL", sell_reference)    # 10.989
    closes = {ENTRY_INDEX: ENTRY_OPEN, ENTRY_INDEX + 1: sell_reference}
    result = _run({"take_profit_pct": 0.05}, closes, opens={ENTRY_INDEX + 2: sell_reference})

    buys = [f for f in result.fills if f["side"] == "BUY"]
    sells = [f for f in result.fills if f["side"] == "SELL"]
    assert len(buys) == 1 and len(sells) == 1

    quantity = buys[0]["quantity"]
    buy_gross = quantity * buy_price
    buy_fee = round(max(buy_gross * 0.00025, 5.0), 4)
    sell_gross = quantity * sell_price
    sell_fee = round(max(sell_gross * 0.00025, 5.0) + sell_gross * 0.0005, 4)
    expected_cash = 100_000.0 - buy_gross - buy_fee + sell_gross - sell_fee

    assert buys[0]["price"] == pytest.approx(buy_price, abs=1e-9)
    assert buys[0]["fee"] == pytest.approx(buy_fee, abs=1e-4)
    assert sells[0]["price"] == pytest.approx(sell_price, abs=1e-9)
    assert sells[0]["fee"] == pytest.approx(sell_fee, abs=1e-4)
    assert result.cash == pytest.approx(expected_cash, abs=1e-6)
    assert result.lots[0]["remaining_quantity"] == 0
    assert result.lots[0]["exit_state"] == "CLOSED"

    # 已实现盈亏：卖出净回款 - lot 总成本（含买入费用）。
    lot_cost = buy_gross + buy_fee
    realized_expected = sell_gross - sell_fee - lot_cost
    assert result.run_summary["realized_pnl"] == pytest.approx(realized_expected, abs=1e-4)
    # 手续费总额 = 买入 + 卖出费用。
    assert result.run_summary["total_fees"] == pytest.approx(buy_fee + sell_fee, abs=1e-4)


def test_cash_and_positions_are_reported_consistently():
    """现金、lot 剩余数量、逐日 equity 必须自洽（现金 + 持仓市值 = 权益）。"""
    result = _run({})
    buys = [f for f in result.fills if f["side"] == "BUY"]
    quantity = buys[0]["quantity"]
    expected_cash = 100_000.0 - quantity * buys[0]["price"] - buys[0]["fee"]
    assert result.cash == pytest.approx(expected_cash, abs=1e-6)
    assert sum(lot["remaining_quantity"] for lot in result.lots) == quantity
    last = result.equity_curve[-1]
    assert last["equity"] == pytest.approx(last["cash"] + last["market_value"], abs=1e-6)
    assert result.final_equity == pytest.approx(last["equity"], abs=1e-6)


def test_zero_fee_contract_matches_pure_arithmetic():
    """零费用确定型案例：期末现金必须严格等于买入成本 + 卖出毛收入的算术结果。"""
    result = run_behavior_backtest_v1(BehaviorRequestV1.from_mapping({
        "calendar": list(CAL), "symbols": [SYMBOL],
        "bars": {SYMBOL: entry_bars(
            {ENTRY_INDEX: ENTRY_OPEN, ENTRY_INDEX + 1: 11.0},
            opens={ENTRY_INDEX + 2: 11.0})},
        "entry_conditions": ["ABOVE_ZERO_GOLDEN_CROSS"],
        "initial_cash": 10_000.0, "max_positions": 1, "max_position_weight": 1.0,
        "exit_rules": {"take_profit_pct": 0.05},
        "commission_rate": 0.0, "min_commission": 0.0,
        "stamp_tax_rate": 0.0, "slippage_bps": 0.0,
    }))
    buys = [f for f in result.fills if f["side"] == "BUY"]
    sells = [f for f in result.fills if f["side"] == "SELL"]
    assert buys[0]["price"] == pytest.approx(10.0, abs=1e-9)
    assert sells[0]["price"] == pytest.approx(11.0, abs=1e-9)   # 零滑点
    quantity = buys[0]["quantity"]
    assert quantity == 1000
    assert result.cash == pytest.approx(10_000.0 - quantity * 10.0 + quantity * 11.0, abs=1e-6)
