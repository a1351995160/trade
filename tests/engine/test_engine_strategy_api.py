"""Strategy callback API + full buy->sell flow."""
from src.chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from src.chanlun_trader.engine.signal import ExecutionPolicy, Side, Signal
from src.chanlun_trader.engine.time_types import date_key, tz_aware

from tests.golden._helpers import CAL, make_store


def test_strategy_fn_full_buy_sell_flow():
    store = make_store()
    cfg = EngineConfig(initial_cash=1_000_000.0, max_positions=10, mode="DAILY")
    cfg.persist_run_manifest = False
    eng = BacktestEngineV2(store, CAL, config=cfg, source_identity=("UNKNOWN", True))

    def strat(view, ts, d):
        sigs = []
        if ts.strftime("%H:%M") != "15:00":
            return sigs
        # CAL[0] 收盘后生成买入信号 -> CAL[1] open 成交
        if d == CAL[0]:
            sigs.append(Signal(strategy_id="CB", signal_id="cb-buy", symbol="600000.SH",
                               generated_at=ts, direction=Side.BUY,
                               execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN))
        # CAL[1] 收盘后生成卖出信号 -> CAL[2] open 成交（T+1 满足：CAL[1] 买入，CAL[2] 卖出）
        if d == CAL[1]:
            sigs.append(Signal(strategy_id="CB", signal_id="cb-sell", symbol="600000.SH",
                               generated_at=ts, direction=Side.SELL,
                               execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN))
        return sigs

    res = eng.run(strategy_fn=strat)
    assert len(res.ledger.trades) == 2
    buy = res.ledger.trades[0]
    sell = res.ledger.trades[1]
    assert buy.side == Side.BUY and sell.side == Side.SELL
    assert buy.fill_time.strftime("%Y%m%d") == str(CAL[1])
    assert sell.fill_time.strftime("%Y%m%d") == str(CAL[2])
    assert sell.quantity == buy.quantity
    assert res.ledger.check_invariants() == []
    # final cash = initial - buy gross - buy fee + sell proceeds
    expected_cash = 1_000_000 - buy.gross_value - buy.fee + (sell.gross_value - sell.fee)
    assert res.ledger.cash == round(expected_cash, 4)
