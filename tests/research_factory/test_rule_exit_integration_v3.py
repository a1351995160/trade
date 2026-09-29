"""真实账户退出：风险覆盖最短持有、分笔选择、分红口径及重放。"""
from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from chanlun_trader.engine.daily_exit_v2 import DailyExitRuleSetV2
from chanlun_trader.engine.position import PositionLot
from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3
from chanlun_trader.research_factory.rule_account_backend_v2 import RuleAccountBackendV2, rule_input_identity
from chanlun_trader.research_factory.rule_exit_adapter_v3 import RuleExitAdapterV3, validate_exit_actions
from chanlun_trader.research_factory.strategy_interface_v1 import prepare, run
from test_historical_process_v1 import historical_fixture
from test_research_rule_strategy_v2 import payload


def proposal(exits):
    value = payload()
    configured = {"execution_mode": "CLOSE_CONFIRM_NEXT_SESSION_OPEN", "stop_loss_pct": None,
                  "take_profit_pct": None, "trailing_activate_pct": None, "trailing_pct": None, **exits}
    value.update(version="RESEARCH_RULE_STRATEGY_V3", indicator_instances=[], exits=configured,
                 min_hold_sessions=20, max_hold_sessions=40)
    return value


def account(exits, prices, suspended=False, cash_dividend=False):
    window, bundle = historical_fixture()
    days = window["calendar"]
    for symbol in window["symbols"]:
        previous = 20.0
        for index, day in enumerate(days):
            close = prices[min(max(index - 80, 0), len(prices) - 1)]
            mask = (bundle["daily"].symbol == symbol) & (bundle["daily"].date == day)
            bundle["daily"].loc[mask, ["open", "high", "low", "close", "prev_close"]] = [close, close*1.02, close*.98, close, previous]
            previous = close
    if suspended:
        bundle["states"].loc[bundle["states"].trade_date == days[83], "suspension_status"] = "SUSPENDED"
    if cash_dividend:
        record, effective = days[82:84]
        bundle["events"] = [{"event_id": "cash", "symbol": window["symbols"][0], "event_type": "CASH_DIVIDEND",
            "record_date": record, "effective_date": effective, "payment_date": effective,
            "source_published_at": str(pd.Timestamp(str(record)).date()) + "T08:00:00+08:00",
            "source": "SYNTHETIC_ANNOUNCEMENT", "units": "CNY_PER_SHARE",
            "terms": {"cash_per_share": 1., "tax_rule": {"kind": "DEFERRED_INDIVIDUAL_2015_101", "source": "SYNTHETIC_TAX"}}}]
        mask = (bundle["daily"].symbol == window["symbols"][0]) & (bundle["daily"].date == effective)
        bundle["daily"].loc[mask, "prev_close"] -= 1.
    strategy = ResearchRuleStrategyV3(proposal(exits), strategy_id="risk")
    backend = RuleAccountBackendV2(window, initial_cash=50000)
    identity = rule_input_identity(bundle, window)
    receipt = {"strategy_plans": {"risk": prepare(strategy, backend)}, "input_identity": identity,
               "novelty": {"risk": {"allowed": True}}, "execution_purpose": "risk", "execution_consumed": True}
    result = run(strategy, backend, frame=bundle, actions=bundle["events"], input_identity=identity, active_check=lambda: receipt)
    return days, result


@pytest.mark.parametrize("exits,prices,reason", [
    ({"stop_loss_pct": .05}, [20, 20, 18.5, 19], "EXIT_FIXED_COST_STOP"),
    ({"take_profit_pct": .05}, [20, 20, 21.5, 21], "EXIT_FIXED_TAKE_PROFIT"),
    ({"trailing_activate_pct": .05, "trailing_pct": .03}, [20, 20, 21.5, 20.5, 20.5], "EXIT_TRAILING_STOP"),
])
def test_risk_exit_fills_before_min_hold_and_replays(exits, prices, reason):
    days, result = account(exits, prices)
    sold = [trade for trade in result["fills"] if trade["side"] == "SELL"]
    assert sold and all(trade["lot_id"] for trade in sold)
    assert int(pd.Timestamp(sold[0]["fill_time"]).strftime("%Y%m%d")) < days[100]
    traces = result["final_account_checkpoint"]["rule_exit_states"]["risk"]["evaluations"]
    hit = next(row for row in traces if reason in row["reasons"])
    assert int(pd.Timestamp(sold[0]["fill_time"]).strftime("%Y%m%d")) > hit["date"]
    assert result["reconciliation"]["passed"]
    assert account(exits, prices)[1] == result


def test_pending_exit_survives_suspension_and_rebound(monkeypatch):
    from chanlun_trader.research_factory.forward_paper_engine_v1 import ForwardPaperEngineV1
    from chanlun_trader.research_factory.portfolio_execution_v1 import build_portfolio_plan
    from chanlun_trader.research_factory.rule_account_backend_v2 import _stamp
    captured = captured_run(monkeypatch, {"stop_loss_pct": .05}, [20, 20, 18.5, 20, 20])
    engine = ForwardPaperEngineV1(captured["header"])
    decisions, opened_count = [], 0
    for step in captured["steps"][:10]:
        snapshot = deepcopy(step["snapshot"])
        if step["phase"] == "open":
            if opened_count == 3:
                for state in snapshot["payload"]["states"]:
                    state["suspended"] = True
            plan = build_portfolio_plan(policy=engine.portfolio, decisions=decisions,
                ledger=engine.engine.ledger, admissions=step["admissions"], input_identity=engine.header["header_id"],
                decision_at=_stamp(engine.header["calendar"][opened_count], 15, 30), next_session=snapshot["market_date"])
            engine.open(snapshot, plan, step["admissions"])
            opened_count += 1
        else:
            decisions = engine.close(snapshot)
    sold = [trade for trade in engine.engine.ledger.trades if trade.side.value == "SELL"]
    assert sold
    assert int(sold[0].fill_time.strftime("%Y%m%d")) == engine.header["calendar"][5]
    assert any(row["reason"] == "SECURITY_NOT_TRADABLE" for row in engine.skips)


def lot(key, price):
    result = PositionLot(key, "position", "000001.SZ", "risk", pd.Timestamp("2025-01-02", tz="Asia/Shanghai"),
                         100, 100, price*100, pd.Timestamp("2025-01-03", tz="Asia/Shanghai"))
    result.entry_price, result.entry_session_index = price, 0
    return result


class Store:
    def __init__(self, close):
        self.close = close
    def get_daily_bar(self, *args, **kwargs):
        return {"close": self.close}


def test_only_triggered_lot_exits_and_partial_quantity_keeps_pending():
    first, second = lot("cheap", 10), lot("expensive", 12)
    ledger = SimpleNamespace(lots={first.lot_id: first, second.lot_id: second}, events=[])
    adapter = RuleExitAdapterV3("risk", DailyExitRuleSetV2(stop_loss_pct=.05))
    exits = adapter.evaluate(ledger, Store(11), [20250102, 20250103], 20250103)
    assert [item.lot_id for item in exits] == ["expensive"]
    assert first.exit_state == "OPEN"
    second.remaining_quantity = 50
    second.exit_state = "PARTIALLY_FILLED"
    retried = adapter.evaluate(ledger, Store(13), [20250102, 20250103, 20250106], 20250106)
    assert [item.lot_id for item in retried] == ["expensive"]


def test_paid_dividend_neutralizes_mechanical_ex_date_drop():
    held = lot("held", 10)
    event = {"event_id": "cash", "event_type": "CASH_DIVIDEND", "symbol": held.symbol,
             "effective_date": 20250103, "payment_date": 20250103, "terms": {"cash_per_share": 1}}
    ledger = SimpleNamespace(lots={held.lot_id: held}, events=[event], payments={"cash"},
                             dividend_lots={"cash": {held.lot_id: 100}})
    adapter = RuleExitAdapterV3("risk", DailyExitRuleSetV2(stop_loss_pct=.05))
    assert not adapter.evaluate(ledger, Store(9), [20250102, 20250103], 20250103)
    assert adapter.trace[-1]["comparison_close"] == 10
    held.remaining_quantity = 50
    ledger.dividend_lots["cash"][held.lot_id] = 50
    assert not adapter.evaluate(ledger, Store(9), [20250102, 20250103, 20250106], 20250106)
    assert held.entry_price == 10
    ledger.payments.clear()
    with pytest.raises(ValueError, match="PAYMENT_REQUIRED"):
        adapter.evaluate(ledger, Store(9), [20250102, 20250103, 20250106], 20250106)
    with pytest.raises(ValueError, match="CORPORATE_ACTION_UNSUPPORTED"):
        validate_exit_actions([{**event, "payment_date": 20250106}])


def test_trailing_state_and_pending_are_reconstructed_from_prefix():
    def replay():
        held = lot("held", 10)
        ledger = SimpleNamespace(lots={held.lot_id: held}, events=[])
        adapter = RuleExitAdapterV3("risk", DailyExitRuleSetV2(trailing_activate_pct=.05, trailing_pct=.03))
        days = [20250102, 20250103, 20250106, 20250107]
        for day, price in zip(days[1:], [11, 10.5, 12]):
            adapter.evaluate(ledger, Store(price), days, day)
        return held, adapter.state()
    held, state = replay()
    assert held.exit_state == "EXIT_DUE"
    assert state["trailing"]["held"]["activated"]
    assert state == replay()[1]


def captured_run(monkeypatch, exits, prices):
    from chanlun_trader.research_factory import rule_account_backend_v2 as backend_module
    from chanlun_trader.research_factory.forward_paper_engine_v1 import ForwardPaperEngineV1
    captured = {"steps": []}
    class RecordingEngine(ForwardPaperEngineV1):
        def __init__(self, header):
            super().__init__(header)
            captured["header"] = deepcopy(header)
        def open(self, snapshot, plan, admissions, **kwargs):
            super().open(snapshot, plan, admissions, **kwargs)
            captured["steps"].append({"phase": "open", "snapshot": deepcopy(snapshot),
                                       "plan": deepcopy(plan), "admissions": deepcopy(admissions), "state": self.state()})
        def close(self, snapshot):
            decisions = super().close(snapshot)
            captured["steps"].append({"phase": "close", "snapshot": deepcopy(snapshot), "state": self.state()})
            return decisions
    monkeypatch.setattr(backend_module, "ForwardPaperEngineV1", RecordingEngine)
    account(exits, prices)
    return captured


def multi_lot_journal(monkeypatch, observation_reason=None):
    from chanlun_trader.research_factory.forward_paper_engine_v1 import ForwardPaperEngineV1
    from chanlun_trader.research_factory.portfolio_execution_v1 import build_portfolio_plan
    from chanlun_trader.research_factory.rule_account_backend_v2 import _independent_reconcile, _stamp
    captured = captured_run(monkeypatch, {"stop_loss_pct": .05}, [20, 20, 21.4, 20.2, 20.2, 21.4])
    if observation_reason:
        captured["header"]["observation_policy"] = {"max_drawdown_bps": 10000, "review_after": 99}
    engine = ForwardPaperEngineV1(captured["header"])
    decisions, journal = [], []
    opens = [row for row in captured["steps"] if row["phase"] == "open"]
    closes = [row for row in captured["steps"] if row["phase"] == "close"]
    admissions = opens[0]["admissions"]
    symbol = engine.symbols[0]
    for index in range(6):
        opened, closed = deepcopy(opens[index]["snapshot"]), deepcopy(closes[index]["snapshot"])
        day = opened["market_date"]
        if index == 1:
            # 先只开一只股票，保留既有 RiskManager 的第二持仓槽位用于后续增仓。
            # 原风控达到 max_positions 时连已有股票的增仓也拒绝，此处不改变它。
            decisions = [row for row in decisions if row["symbol"] == symbol]
        if index == 2:
            # 公共账户可接收同一策略的后续增仓；两次真实成交的成本不同。
            decisions = [{"strategy_id": "risk", "symbol": symbol, "side": "BUY", "target_weight": .5}]
        if index == 3:
            for bar in closed["payload"]["bars"]:
                bar["volume"] = 2000.0
        if index == 4:
            for bar in opened["payload"]["bars"]:
                bar["volume"] = 2000.0
        previous = engine.header["calendar"][index]
        plan = build_portfolio_plan(policy=engine.portfolio, decisions=decisions,
            ledger=engine.engine.ledger, admissions=admissions, input_identity=engine.header["header_id"],
            decision_at=_stamp(previous, 15, 30), next_session=day)
        engine.open(opened, plan, admissions)
        journal.append({"phase": "open", "snapshot": opened, "plan": plan,
                        "admissions": admissions, "state": engine.state()})
        decisions = engine.close(closed)
        if observation_reason and index == 3:
            assert any(row.get("exit_lot_ids") for row in decisions)
            engine.observation.update(buy_blocked=True, reason_codes=[observation_reason])
            decisions = engine._observation_decisions(decisions)
            assert all("exit_lot_ids" not in row for row in decisions)
        journal.append({"phase": "close", "snapshot": closed, "state": engine.state()})
        assert _independent_reconcile(engine, 50000, day)["passed"]
    return engine, {"header": captured["header"], "steps": journal}


def test_two_actual_entry_lots_sell_only_expensive_lot_and_retry_partial(monkeypatch):
    engine, journal = multi_lot_journal(monkeypatch)
    lots = [lot for lot in engine.engine.ledger.lots.values() if lot.symbol == engine.symbols[0]]
    assert len(lots) == 2
    cheap, expensive = sorted(lots, key=lambda item: item.entry_price)
    assert cheap.remaining_quantity == cheap.quantity
    sold = [trade for trade in engine.engine.ledger.trades if trade.side.value == "SELL"]
    assert sold and all(trade.lot_id == expensive.lot_id for trade in sold)
    assert len(sold) >= 2
    partial = journal["steps"][8]["state"]["economic"]["lots"][expensive.lot_id]
    assert 0 < partial["remaining_quantity"] < partial["quantity"]
    assert partial["exit_state"] == "PARTIALLY_FILLED"
    assert expensive.remaining_quantity == 0


@pytest.mark.parametrize("scenario,boundary", [("trailing", 6), ("multi_lot", 8), ("multi_lot", 9)])
def test_exit_journal_process_restart_keeps_peak_pending_and_partial(monkeypatch, tmp_path, scenario, boundary):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    from chanlun_trader.research_factory.common import canonical_json
    if scenario == "trailing":
        journal = captured_run(monkeypatch, {"trailing_activate_pct": .05, "trailing_pct": .03},
                               [20, 20, 21.5, 20.5, 20.5])
        states = journal["steps"][boundary - 1]["state"]["rule_exit_states"]["risk"]["trailing"]
        assert any(value["activated"] for value in states.values())
    else:
        _, journal = multi_lot_journal(monkeypatch)
    source = tmp_path / "journal.json"
    checkpoint = tmp_path / "checkpoint.json"
    output = tmp_path / "resumed.json"
    source.write_text(canonical_json(journal), encoding="utf-8")
    program = r"""
import json, sys
from pathlib import Path
from chanlun_trader.research_factory.forward_paper_engine_v1 import ForwardPaperEngineV1
from chanlun_trader.research_factory.common import canonical_json
journal = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
engine = ForwardPaperEngineV1(journal['header'])
boundary = int(sys.argv[4])
limit = boundary if sys.argv[5] == 'checkpoint' else len(journal['steps'])
checkpoint = None if sys.argv[5] == 'checkpoint' else json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
for index, step in enumerate(journal['steps'][:limit]):
    if step['phase'] == 'open':
        engine.open(step['snapshot'], step['plan'], step['admissions'])
    else:
        engine.close(step['snapshot'])
    if engine.state() != step['state']:
        raise ValueError('REPLAY_STATE_DIVERGED')
    if checkpoint is not None and index + 1 == boundary and engine.state() != checkpoint:
        raise ValueError('RESTART_CHECKPOINT_DIVERGED')
Path(sys.argv[2] if sys.argv[5] == 'checkpoint' else sys.argv[3]).write_text(canonical_json(engine.state()), encoding='utf-8')
"""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(Path(__file__).resolve().parents[2] / "src"),
                                                         str(Path(__file__).resolve().parents[2])])}
    arguments = [sys.executable, "-c", program, str(source), str(checkpoint), str(output), str(boundary)]
    subprocess.run([*arguments, "checkpoint"], check=True, env=env, capture_output=True, text=True)
    subprocess.run([*arguments, "resume"], check=True, env=env, capture_output=True, text=True)
    assert json.loads(output.read_text(encoding="utf-8")) == journal["steps"][-1]["state"]


def test_exit_plan_rejects_unknown_duplicate_or_other_owner_lots(monkeypatch):
    from chanlun_trader.research_factory.portfolio_execution_v1 import build_portfolio_plan
    from chanlun_trader.research_factory.rule_account_backend_v2 import _stamp
    engine, journal = multi_lot_journal(monkeypatch)
    held = next(lot for lot in engine.engine.ledger.lots.values() if lot.remaining_quantity)
    common = dict(policy=engine.portfolio, ledger=engine.engine.ledger,
                  admissions=journal["steps"][0]["admissions"], input_identity=engine.header["header_id"],
                  decision_at=_stamp(engine.header["calendar"][6], 15, 30), next_session=engine.header["calendar"][7])
    for ids in (["unknown"], [held.lot_id, held.lot_id], []):
        with pytest.raises(ValueError, match="PORTFOLIO_EXIT_LOT"):
            build_portfolio_plan(decisions=[{"strategy_id": "risk", "symbol": held.symbol,
                                            "side": "SELL", "exit_lot_ids": ids}], **common)
    other_symbol = next(symbol for symbol in engine.symbols if symbol != held.symbol)
    with pytest.raises(ValueError, match="PORTFOLIO_EXIT_LOT_OWNERSHIP"):
        build_portfolio_plan(decisions=[{"strategy_id": "risk", "symbol": other_symbol,
                                        "side": "SELL", "exit_lot_ids": [held.lot_id]}], **common)


def test_actual_cash_dividend_account_does_not_trigger_mechanical_stop():
    _, result = account({"stop_loss_pct": .05}, [20, 20, 20, 19, 19], cash_dividend=True)
    state = result["final_account_checkpoint"]
    assert "cash" in state["economic"]["payments"]
    # 另一只股票没有红利，仍按原始价格触发；不能全账户加同一笔红利。
    sold_symbols = {trade["symbol"] for trade in result["fills"] if trade["side"] == "SELL"}
    assert "000001.SZ" not in sold_symbols
    assert "600000.SH" in sold_symbols
    traces = state["rule_exit_states"]["risk"]["evaluations"]
    assert any(row["symbol"] == "000001.SZ" and row["paid_gross_cash_per_share"] == 1 for row in traces)
    assert result["reconciliation"]["passed"]


@pytest.mark.parametrize('reason', ['OBSERVATION_DRAWDOWN_LIMIT', 'OBSERVATION_REVIEW_DUE'])
def test_observation_full_exit_overrides_risk_lot_filter(monkeypatch, reason):
    engine, journal = multi_lot_journal(monkeypatch, observation_reason=reason)
    lots = [lot for lot in engine.engine.ledger.lots.values() if lot.symbol == engine.symbols[0]]
    assert len(lots) == 2
    sold = [trade for trade in engine.engine.ledger.trades
            if trade.side.value == 'SELL' and trade.symbol == engine.symbols[0]]
    assert sum(trade.quantity for trade in sold) == sum(lot.quantity for lot in lots)
    assert all(lot.remaining_quantity == 0 for lot in lots)
