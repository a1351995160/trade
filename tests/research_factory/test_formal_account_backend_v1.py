from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.formal_account_backend_v1 import (
    BASE_COSTS, STRESS_COSTS, FormalWindowStrategyV1, normalized_window,
    prepare_formal_account, run_formal_account, window_input_identity,
)
from chanlun_trader.research_factory.bounded_candidate_v1 import validate_candidate
from scripts.probe_all_indicator_strategy_v1 import run_chain, ACCOUNT_END_DATE
from bounded_research_fixture import synthetic_bundle


PROPOSAL = {"hypothesis": "冻结趋势", "indicators": ["MACD"], "threshold": 1, "change_reason": "独立确认"}


def fixture():
    days = [int(d.strftime("%Y%m%d")) for d in pd.bdate_range("2025-01-02", periods=110)]
    symbols = ["000001.SZ", "600000.SH"]
    window = {"symbols": symbols, "feature_start": days[0], "account_start": days[80],
              "account_end": days[-1], "calendar": days}
    bars, turns, states = [], [], []
    for symbol in symbols:
        previous = 10.
        for i, day in enumerate(days):
            close = 10 + .02 * i + .4 * np.sin(i / 5)
            bars.append(dict(symbol=symbol, date=day, open=close * .999, high=close * 1.02,
                low=close * .98, close=close, prev_close=previous, volume=1_000_000., amount=close * 1_000_000,
                adjustflag="3"))
            turns.append(dict(symbol=symbol, date=day, volume=1_000_000., turn=1., tradestatus=1))
            previous = close
            if day >= days[80]:
                states.append(dict(symbol=symbol, trade_date=day, listed=True, delisted=False,
                    universe_member=True, eligibility_status="ELIGIBLE", st_status="NORMAL",
                    suspension_status="TRADING", board="SZ_MAIN" if symbol.endswith("SZ") else "SH_MAIN"))
    bundle = {"daily": pd.DataFrame(bars), "turn": pd.DataFrame(turns), "states": pd.DataFrame(states),
                    "events": [], "corporate_actions_complete": True, "calendar": days,
                    "source_hashes": {"fixture": "SYNTHETIC"}}
    snapshots(window, bundle)
    return window, bundle


def snapshots(window, bundle):
    closes, opens = [], []
    for day in window["calendar"]:
        date = str(pd.Timestamp(str(day)).date())
        rows = bundle["daily"].loc[bundle["daily"].date == day].to_dict("records")
        closes.append({"market_date": day, "phase": "CLOSE", "received_at": date + "T16:05:00+08:00",
                       "payload": {"bars": rows}})
        if day < window["account_start"]:
            continue
        opened = deepcopy(rows)
        for row in opened:
            price = row["open"] * 1.001
            row.update(open=price, high=price, low=price, close=price, volume=100_000.,
                       amount=price * 100_000, price_basis="OBSERVED_NOW")
        states = bundle["states"].loc[bundle["states"].trade_date == day].to_dict("records")
        for row in states:
            row.update(is_st=row["st_status"] == "ST", suspended=row["suspension_status"] == "SUSPENDED")
        stamp = date + "T09:31:00+08:00"
        bundle["states"].loc[bundle["states"].trade_date == day, "available_at"] = stamp
        opens.append({"market_date": day, "phase": "OPEN", "received_at": stamp,
                      "payload": {"bars": opened, "states": states}})
    bundle.update(open_snapshots=opens, close_snapshots=closes)


def execute(window, bundle, proposal=PROPOSAL, costs="BASE", receipt=None):
    identity = window_input_identity(bundle, window)
    plan = prepare_formal_account(proposal, strategy_id="candidate", window=window, costs=costs)
    if receipt is None:
        receipt = {"strategy_plans": {"candidate": plan}, "input_identity": identity,
                   "novelty": {"candidate": {"allowed": True}},
                   "execution_purpose": "candidate", "execution_consumed": True}
    return run_formal_account(proposal, strategy_id="candidate", bundle=bundle, window=window,
                              costs=costs, input_identity=identity, active_check=lambda: receipt)


def test_independent_window_rule_identity_and_pressure_reexecute(monkeypatch):
    window, bundle = fixture()
    import chanlun_trader.research_factory.formal_account_backend_v1 as module
    original, calls = module.run_chain, []
    def tracked(*args, **kwargs):
        calls.append(kwargs["execution_costs"])
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "run_chain", tracked)
    base, stress = execute(window, bundle), execute(window, bundle, costs="STRESS")
    assert calls == [BASE_COSTS, STRESS_COSTS]
    assert base["decisions"] == stress["decisions"]
    assert base["fills"] and stress["fills"]
    assert base["fills"][0]["price"] != stress["fills"][0]["price"]
    assert base["chain"]["issues"] == stress["chain"]["issues"] == []
    assert base["rule_identity"] == validate_candidate(PROPOSAL, strategy_id="old").rule_identity
    assert base["strategy_plan"]["plan_id"] != stress["strategy_plan"]["plan_id"]
    assert "account_start_date" not in base["strategy_plan"]["strategy"]["parameters"]
    assert [r["date"] for r in base["daily_returns"]] == window["calendar"][80:]
    assert base["chain"]["account_dates"] == [window["account_start"], window["account_end"]]


def test_benchmark_once_and_future_prices_do_not_change_earlier_decisions():
    window, bundle = fixture()
    benchmark = execute(window, bundle, None)
    assert len(benchmark["fills"]) == 2
    assert all(t["side"] == "BUY" for t in benchmark["fills"])
    assert all(t["date"] == window["calendar"][81] for t in benchmark["fills"])
    base = execute(window, bundle)
    changed = deepcopy(bundle)
    day = window["calendar"][-3]
    mask = changed["daily"].date >= day
    for column in ("open", "high", "low", "close", "prev_close", "amount"):
        changed["daily"].loc[mask, column] *= 1.02
    snapshots(window, changed)
    alternate = execute(window, changed)
    for symbol in window["symbols"]:
        assert [d for d in base["decisions"][symbol] if d["date"] < day] == [
            d for d in alternate["decisions"][symbol] if d["date"] < day]
    assert base["input_identity"] != alternate["input_identity"]
    assert base == execute(window, bundle)


def test_authorization_cost_and_input_changes_rejected():
    window, bundle = fixture()
    plan = prepare_formal_account(PROPOSAL, strategy_id="candidate", window=window)
    identity = window_input_identity(bundle, window)
    receipt = {"strategy_plans": {"candidate": plan}, "input_identity": identity,
        "novelty": {"candidate": {"allowed": True}}, "execution_purpose": "candidate", "execution_consumed": True}
    with pytest.raises(PermissionError, match="NOT_AUTHORIZED"):
        execute(window, bundle, costs="STRESS", receipt=receipt)
    changed = deepcopy(bundle)
    changed["turn"].loc[0, "turn"] += .01
    with pytest.raises(PermissionError, match="NOT_AUTHORIZED"):
        execute(window, changed, receipt=receipt)
    with pytest.raises(PermissionError, match="INPUT_CHANGED"):
        run_formal_account(PROPOSAL, strategy_id="candidate", bundle=changed, window=window,
                           input_identity=identity, active_check=lambda: receipt)


@pytest.mark.parametrize("kind", ["calendar", "warmup", "source_coverage", "suspension", "board"])
def test_scope_fail_closed(kind):
    window, bundle = fixture()
    if kind == "calendar":
        bundle["calendar"] = bundle["calendar"][:-1]
    elif kind == "warmup":
        window["account_start"] = window["calendar"][59]
    elif kind == "source_coverage":
        bundle["corporate_actions_complete"] = False
    elif kind == "suspension":
        bundle["turn"].loc[0, "tradestatus"] = 0
    else:
        bundle["states"].loc[0, "board"] = "STAR"
    with pytest.raises(ValueError):
        execute(window, bundle)


def test_old_run_chain_defaults_equal_explicit_scope_and_costs():
    strategy = validate_candidate(PROPOSAL, strategy_id="old")
    bundle = synthetic_bundle(strategy)
    symbols = tuple(bundle["daily"].symbol.unique())
    decisions = {s: [{"date": 20240201, "decision_at_close": "BUY", "rising_votes": 1}] for s in symbols}
    args = (bundle["daily"], decisions, strategy.definition, "daily", "turn", "states", "actions")
    options = {"execution_states": bundle["states"], "corporate_events": ()}
    old = run_chain(*args, **options)
    configured = run_chain(*args, **options, account_start_date=20240201, account_end_date=ACCOUNT_END_DATE,
        symbols=symbols, account_calendar=tuple(sorted(bundle["states"].loc[bundle["states"].trade_date <= ACCOUNT_END_DATE, "trade_date"].unique())), execution_costs=BASE_COSTS)
    assert old == configured


def test_cash_dividend_reuses_independent_account_and_warmup_events():
    window, bundle = fixture()
    events = []
    for index in (35, 90):
        record, effective = window["calendar"][index - 1:index + 1]
        symbol = window["symbols"][0]
        events.append({"event_id": f"cash_{index}", "symbol": symbol, "event_type": "CASH_DIVIDEND",
            "record_date": record, "effective_date": effective, "payment_date": effective,
            "source_published_at": str(pd.Timestamp(str(record)).date()) + "T08:00:00+08:00",
            "source": "SYNTHETIC_ANNOUNCEMENT", "units": "CNY_PER_SHARE",
            "terms": {"cash_per_share": .1, "tax_rule": {
                "kind": "DEFERRED_INDIVIDUAL_2015_101", "source": "SYNTHETIC_TAX"}}})
        exrow = (bundle["daily"].symbol == symbol) & (bundle["daily"].date == effective)
        bundle["daily"].loc[exrow, "prev_close"] -= .1
    bundle["events"] = events
    snapshots(window, bundle)
    result = execute(window, bundle, None)
    assert result["chain"]["issues"] == []
    account = result["chain"]["corporate_account"]
    assert [e["event_id"] for e in account["events"]] == ["cash_90"]
    assert account["dividend_income"] > 0
    assert "cash_35" not in account["independent_entitlements"]


def test_actual_receipt_clock_and_quote_are_used_for_execution():
    window, bundle = fixture()
    result = execute(window, bundle, None)
    assert result["chain"]["official_valuation"]["event"] == "OBSERVED_AFTER_CLOSE"
    assert all("T16:05:00" in p["timestamp"] for p in result["chain"]["official_valuation"]["points"])
    assert all("T09:31:00" in t["filled_at"] for t in result["fills"])
    for trade in result["fills"]:
        raw = bundle["daily"].loc[(bundle["daily"].symbol == trade["symbol"]) &
                                  (bundle["daily"].date == trade["date"])].iloc[0]
        assert trade["price"] == round(raw.open * 1.001 * 1.001, 4)
        assert trade["price"] != round(raw.open * 1.001, 4)
    assert all("16:05:00" in order["created_at"] for order in result["chain"]["orders"])
    assert all("16:05:00" in ds[0]["decision_at"] for ds in result["decisions"].values())
    bundle["states"].loc[0, "available_at"] = str(pd.Timestamp(str(window["account_start"])).date()) + "T09:32:00+08:00"
    with pytest.raises(ValueError, match="OPEN_STATE_TIME"):
        execute(window, bundle)


def test_saved_result_independent_reconciliation_rejects_modified_outputs():
    from chanlun_trader.research_factory.formal_account_backend_v1 import validate_formal_result
    window, bundle = fixture()
    result = execute(window, bundle)
    assert validate_formal_result(result, bundle=bundle, window=window)["status"] == "VERIFIED"
    mutations = [
        lambda r: r["metrics"].update(net_return=.9),
        lambda r: r["daily_returns"][3].update(net_return=.1),
        lambda r: r["chain"]["trades"][0].update(fee=0),
        lambda r: r["chain"]["independent_account_checks"][3]["position_quantities"].update({window["symbols"][0]: 999}),
        lambda r: r["chain"]["trades"][0].update(filled_at="2025-04-25T09:30:00+08:00"),
    ]
    for mutate in mutations:
        corrupt = deepcopy(result)
        mutate(corrupt)
        with pytest.raises(ValueError, match="FORMAL_SAVED_RESULT"):
            validate_formal_result(corrupt, bundle=bundle, window=window)
    # 即使同步改掉重复展示的成交副本，费用仍须由实际成交重新计算。
    corrupt = deepcopy(result)
    corrupt["chain"]["trades"][0]["fee"] = 0
    corrupt["fills"] = deepcopy(corrupt["chain"]["trades"])
    with pytest.raises(ValueError, match="FORMAL_SAVED_RESULT_FEE"):
        validate_formal_result(corrupt, bundle=bundle, window=window)


def test_saved_self_consistent_deleted_trajectory_cannot_bypass_replay(monkeypatch):
    from chanlun_trader.research_factory import formal_account_backend_v1 as account
    window, bundle = fixture()
    result = execute(window, bundle)
    account._RESULT_REPLAY_CACHE.clear()
    original, calls = account.run_formal_account, []
    def tracked(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(account, "run_formal_account", tracked)
    assert account.validate_formal_result(result, bundle=bundle, window=window)["status"] == "VERIFIED"
    assert account.validate_formal_result(result, bundle=bundle, window=window)["status"] == "VERIFIED"
    assert len(calls) == 1
    # 删除全部成交并同步重写现金、仓位、收益和展示指标，账务本身仍然自洽。
    corrupt = deepcopy(result)
    chain = corrupt["chain"]
    chain.update(trades=[], orders=[], n_trades=0, n_orders=0)
    corrupt["fills"] = []
    for row in chain["independent_account_checks"]:
        row.update(cash=1_000_000., market_value=0., equity=1_000_000., max_abs_ledger_delta=0.,
                   position_quantities=dict.fromkeys(window["symbols"], 0))
    corrupt["ledger"] = deepcopy(chain["independent_account_checks"])
    for row in corrupt["daily_returns"]:
        row["net_return"] = 0.
    for point in chain["official_valuation"]["points"]:
        point["equity"] = 1_000_000.
    corrupt["metrics"] = {"net_return": 0., "max_drawdown": 0., "total_fees": 0., "trade_count": 0}
    corrupt["report"]["metrics"] = deepcopy(corrupt["metrics"])
    with pytest.raises(ValueError, match="FROZEN_TRAJECTORY_MISMATCH"):
        account.validate_formal_result(corrupt, bundle=bundle, window=window)
    assert len(calls) == 2
    assert len(account._RESULT_REPLAY_CACHE) == 1
