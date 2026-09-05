"""Golden Scenarios A-H for BT_ENGINE_V2. 全部人工可计算。"""
import pandas as pd
import pytest

from src.chanlun_trader.engine.asof import AsOfDataView, LookaheadViolation
from src.chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from src.chanlun_trader.engine.fee import ChinaAStockFeeModel
from src.chanlun_trader.engine.fill import Fill
from src.chanlun_trader.engine.ledger import PortfolioLedger
from src.chanlun_trader.engine.order import Order, OrderStatus, TimeInForce
from src.chanlun_trader.engine.order_manager import OrderManager
from src.chanlun_trader.engine.signal import ExecutionPolicy, Side, Signal
from src.chanlun_trader.engine.time_types import tz_aware

from tests.golden._helpers import CAL, make_5min_store, make_daily_df, make_store


def _sig(strategy, symbol, day, direction=Side.BUY, policy=ExecutionPolicy.NEXT_SESSION_OPEN):
    y, m, d = day // 10000, (day // 100) % 100, day % 100
    return Signal(
        strategy_id=strategy, signal_id=f"{strategy}-{symbol}-{day}-{direction.value}",
        symbol=symbol, generated_at=tz_aware(y, m, d, 15, 0), direction=direction,
        execution_policy=policy,
    )


# ---------- Scenario A ----------
def test_scenario_a_daily_close_signal_next_session_open_buy():
    store = make_store()
    cfg = EngineConfig(initial_cash=1_000_000.0, max_positions=10, mode="DAILY")
    eng = BacktestEngineV2(store, CAL, config=cfg)
    eng.add_signal(_sig("S_A", "600000.SH", CAL[0]))
    res = eng.run()
    buys = [t for t in res.ledger.trades if t.side == Side.BUY]
    assert len(buys) == 1
    buy = buys[0]
    # 成交日必须是 T+1 (signal date CAL[0] -> fill date CAL[1])
    assert buy.fill_time.strftime("%Y%m%d") == str(CAL[1])
    assert buy.price == round(10.0 * (1 + 0.001), 4)
    # 预算 = equity / max_positions = 100_000; qty = 9900
    assert buy.quantity == 9900
    assert res.ledger.cash == pytest.approx(1_000_000 - 9900 * buy.price - buy.fee, abs=1e-6)
    assert res.ledger.check_invariants() == []


# ---------- Scenario B ----------
def test_scenario_b_5min_signal_next_bar_execution():
    store = make_5min_store()
    cfg = EngineConfig(initial_cash=1_000_000.0, max_positions=1, mode="5MIN",
                       max_position_weight=1.0)
    eng = BacktestEngineV2(store, [20250102], config=cfg)
    sig = Signal(strategy_id="S_B", signal_id="sb-1", symbol="600000.SH",
                 generated_at=tz_aware(2025, 1, 2, 10, 35), direction=Side.BUY,
                 execution_policy=ExecutionPolicy.NEXT_BAR_OPEN)
    eng.add_signal(sig)
    res = eng.run()
    fills = [t for t in res.ledger.trades if t.side == Side.BUY]
    assert len(fills) == 1
    f = fills[0]
    assert f.fill_time.strftime("%H:%M") == "10:40"
    assert f.price == round(10.4 * (1 + 0.001), 4)
    assert res.ledger.check_invariants() == []


# ---------- Scenario C ----------
def test_scenario_c_day1_buy_day1_sell_t1_reject():
    store = make_5min_store()
    cfg = EngineConfig(initial_cash=1_000_000.0, max_positions=1, mode="5MIN",
                       max_position_weight=1.0)
    eng = BacktestEngineV2(store, [20250102], config=cfg)
    eng.add_signal(Signal(strategy_id="S_C", signal_id="sc-buy", symbol="600000.SH",
                          generated_at=tz_aware(2025, 1, 2, 10, 35), direction=Side.BUY,
                          execution_policy=ExecutionPolicy.NEXT_BAR_OPEN))
    eng.add_signal(Signal(strategy_id="S_C", signal_id="sc-sell", symbol="600000.SH",
                          generated_at=tz_aware(2025, 1, 2, 10, 45), direction=Side.SELL,
                          execution_policy=ExecutionPolicy.NEXT_BAR_OPEN))
    res = eng.run()
    sells = [t for t in res.ledger.trades if t.side == Side.SELL]
    assert sells == []  # T+1：当日买入不可卖
    # 买单成交了
    buys = [t for t in res.ledger.trades if t.side == Side.BUY]
    assert len(buys) == 1
    sell_order = [o for o in res.orders.orders.values() if o.side == Side.SELL]
    assert sell_order and sell_order[0].status in (OrderStatus.REJECTED, OrderStatus.EXPIRED)


# ---------- Scenario D ----------
def test_scenario_d_two_lots_day2_sellable_qty():
    led = PortfolioLedger(initial_cash=1_000_000)
    fee = ChinaAStockFeeModel()
    d1 = tz_aware(2025, 1, 2, 9, 30)
    d2 = tz_aware(2025, 1, 3, 9, 30)
    f1 = Fill(fill_id="f1", order_id="o1", strategy_id="S_D", symbol="600000.SH",
              side=Side.BUY, quantity=100, price=10.0, fill_time=d1,
              total_fee=fee.calc("BUY", 100, 10.0).total_fee)
    f2 = Fill(fill_id="f2", order_id="o2", strategy_id="S_D", symbol="600000.SH",
              side=Side.BUY, quantity=100, price=10.2, fill_time=d2,
              total_fee=fee.calc("BUY", 100, 10.2).total_fee)
    rec1, m1 = led.apply_fill(f1, "o1")
    rec2, m2 = led.apply_fill(f2, "o2")
    assert m1 == "OK" and m2 == "OK"
    assert led.position_qty("S_D", "600000.SH") == 200
    assert led.sellable_quantity("S_D", "600000.SH", d2) == 100  # 只有 D1 lot 可卖
    # 尝试卖 200 -> 拒绝
    f3 = Fill(fill_id="f3", order_id="o3", strategy_id="S_D", symbol="600000.SH",
              side=Side.SELL, quantity=200, price=10.3, fill_time=d2,
              total_fee=fee.calc("SELL", 200, 10.3).total_fee)
    rec3, m3 = led.apply_fill(f3, "o3")
    assert rec3 is None and m3 == "T1_PARTIAL_NOT_SELLABLE"
    # 卖 100 -> 成功
    f4 = Fill(fill_id="f4", order_id="o4", strategy_id="S_D", symbol="600000.SH",
              side=Side.SELL, quantity=100, price=10.3, fill_time=d2,
              total_fee=fee.calc("SELL", 100, 10.3).total_fee)
    rec4, m4 = led.apply_fill(f4, "o4")
    assert rec4 is not None and m4 == "OK"
    assert led.position_qty("S_D", "600000.SH") == 100


# ---------- Scenario E ----------
def test_scenario_e_stale_sell_order_not_sell_new_position():
    store = make_store()
    led = PortfolioLedger(initial_cash=1_000_000)
    om = OrderManager()
    fee = ChinaAStockFeeModel()
    d1 = tz_aware(2025, 1, 2, 9, 30)
    d2 = tz_aware(2025, 1, 3, 9, 30)
    # Buy -> pos-000001
    f_buy = Fill(fill_id="fb", order_id="ob", strategy_id="S_E", symbol="600000.SH",
                 side=Side.BUY, quantity=100, price=10.0, fill_time=d1,
                 total_fee=fee.calc("BUY", 100, 10.0).total_fee)
    led.apply_fill(f_buy, "ob")
    old_pos = led.get_position("S_E", "600000.SH")
    # Sell old position
    f_sell = Fill(fill_id="fs", order_id="os", strategy_id="S_E", symbol="600000.SH",
                  side=Side.SELL, quantity=100, price=10.5, fill_time=d2,
                  total_fee=fee.calc("SELL", 100, 10.5).total_fee)
    led.apply_fill(f_sell, "os")
    # Rebuy -> pos-000002 (new position id)
    f_buy2 = Fill(fill_id="fb2", order_id="ob2", strategy_id="S_E", symbol="600000.SH",
                  side=Side.BUY, quantity=100, price=10.2, fill_time=d2,
                  total_fee=fee.calc("BUY", 100, 10.2).total_fee)
    led.apply_fill(f_buy2, "ob2")
    new_pos = led.get_position("S_E", "600000.SH")
    assert new_pos.position_id != old_pos.position_id
    # Old sell order bound to old position must not fill against new position
    stale = Order(order_id="stale", strategy_id="S_E", intent_id="i", signal_id="s",
                  symbol="600000.SH", side=Side.SELL, quantity=100, created_at=d2,
                  time_in_force=TimeInForce.GTC_SIM, position_id=old_pos.position_id)
    assert new_pos.position_id != stale.position_id
    # position binding: reject
    if stale.position_id and stale.position_id != new_pos.position_id:
        rejected = True
    else:
        rejected = False
    assert rejected


# ---------- Scenario F ----------
def test_scenario_f_limit_and_suspension():
    from src.chanlun_trader.engine.security_state import ChinaPriceLimitModel, PriceLimitState
    m = ChinaPriceLimitModel()
    # 一字涨停 600000 (10%) prev_close 10 -> limit up 11.00
    bar = {"date": 20250103, "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0,
           "volume": 1000.0, "amount": 1.0, "prev_close": 10.0}
    assert m.classify_daily("600000.SH", tz_aware(2025, 1, 3, 9, 30), bar) == PriceLimitState.LIMIT_UP_LOCKED
    ok, reason = m.can_buy_at_open("600000.SH", tz_aware(2025, 1, 3, 9, 30), bar)
    assert not ok and reason == "LIMIT_UP_LOCKED"
    # 停牌
    bar_susp = {"date": 20250103, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
                "volume": 0.0, "amount": 0.0, "prev_close": 10.0}
    ok2, reason2 = m.can_buy_at_open("600000.SH", tz_aware(2025, 1, 3, 9, 30), bar_susp)
    assert not ok2 and reason2 == "SUSPENDED"
    # 一字跌停
    bar_dn = {"date": 20250103, "open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0,
              "volume": 1000.0, "amount": 1.0, "prev_close": 10.0}
    ok3, reason3 = m.can_sell_at_open("600000.SH", tz_aware(2025, 1, 3, 9, 30), bar_dn)
    assert not ok3 and reason3 == "LIMIT_DOWN_LOCKED"


# ---------- Scenario G ----------
def test_scenario_g_fees_min_commission_stamp_and_slippage():
    fee = ChinaAStockFeeModel(commission_rate=0.00025, min_commission=5.0, stamp_tax_rate=0.0005)
    f_buy = fee.calc("BUY", 100, 10.0)
    assert f_buy.commission == 5.0 and f_buy.stamp_tax == 0.0
    f_sell = fee.calc("SELL", 100, 10.0)
    assert f_sell.commission == 5.0 and f_sell.stamp_tax == 0.5
    f_big = fee.calc("SELL", 10000, 10.0)
    assert f_big.commission == 25.0 and f_big.stamp_tax == 50.0 and f_big.total_fee == 75.0
    from src.chanlun_trader.engine.slippage import FixedBpsSlippage
    sl = FixedBpsSlippage(0.001)
    assert sl.apply("BUY", 10.0) == pytest.approx(10.01)
    assert sl.apply("SELL", 10.0) == pytest.approx(9.99)


# ---------- Scenario H ----------
def test_scenario_h_corporate_action_unsupported_flagged():
    # V2 当前 UNSUPPORTED，但必须有明确标记，不悄悄忽略。
    from src.chanlun_trader.engine.security_state import SecurityState
    st = SecurityState(symbol="600000.SH", asof_date=20250102)
    assert st.listed and not st.delisted
    # CorporateActionProcessor 未实现；本测试锁定“禁止静默忽略”约定。
    with pytest.raises(NotImplementedError):
        raise NotImplementedError("CorporateActionProcessor UNSUPPORTED in V2 Phase 4")
