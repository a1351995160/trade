"""The public strategy call must preserve the frozen 51-vote and account path."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
from chanlun_trader.research_factory.strategy_interface_v1 import Context, prepare, run
from scripts.probe_all_indicator_strategy_v1 import (
    BUY_VOTES, SYMBOLS, decision_trace, pilot_registry, run_chain,
)
from scripts.s1_public_entry_strategy_v1 import (
    Fixed51AccountBackend, Fixed51VoteStrategy, input_identity,
)


def _history(strategy, rising):
    columns = {}
    for item in strategy.definition["indicators"]:
        key = item["id"]
        columns[f"{key}__value"] = [1.0, 2.0 if key in rising else 0.0]
        columns[f"{key}__ready"] = [True, True]
    return pd.DataFrame(columns, index=pd.Index([20240102, 20240103], name="date"))


def test_each_of_51_indicators_can_flip_public_decision():
    strategy = Fixed51VoteStrategy()
    ids = [item["id"] for item in strategy.definition["indicators"]]
    for pivotal in ids:
        support = set(sorted(set(ids) - {pivotal})[:BUY_VOTES - 1])
        history = _history(strategy, support)
        sell = strategy.on_close(Context(history, (20240102, 20240103), 1, {}, {}))
        assert sell.reason == "SELL" and sell.intent.weight == 0
        history[f"{pivotal}__value"] = [1.0, 2.0]
        buy = strategy.on_close(Context(history, (20240102, 20240103), 1, {}, {}))
        assert buy.reason == "BUY" and buy.intent.weight == 0.5
        assert buy.metadata["rising_votes"] == BUY_VOTES


def test_public_strategy_rejects_incomplete_or_future_history():
    strategy = Fixed51VoteStrategy()
    history = _history(strategy, set())
    first = strategy.definition["indicators"][0]["id"]
    history.loc[20240103, f"{first}__ready"] = False
    assert strategy.on_close(Context(history, (20240102, 20240103), 1, {}, {})).intent is None
    with pytest.raises(ValueError, match="ALL_INDICATORS_REQUIRED"):
        strategy.on_close(Context(history.drop(columns=f"{first}__value"),
                                  (20240102, 20240103), 1, {}, {}))
    with pytest.raises(ValueError, match="PREFIX_ONLY"):
        strategy.on_close(Context(history, (20240102, 20240103), 0, {}, {}))


def test_synthetic_public_entry_matches_daily_votes_and_account(tmp_path):
    dates = pd.bdate_range("2023-10-09", "2024-07-31")
    bars, turns, states, historical = [], [], [], []
    for symbol in SYMBOLS:
        previous_close = 10.0
        for i, date in enumerate(dates):
            day = int(date.strftime("%Y%m%d"))
            close = 10 + i * 0.02 + 0.7 * np.sin(i / 6)
            volume = 1_000_000 + 100_000 * np.sin(i / 5)
            bars.append({"symbol": symbol, "date": day, "open": close * 0.995,
                         "high": close * 1.02, "low": close * 0.98,
                         "close": close, "volume": volume, "amount": close * volume,
                         "prev_close": previous_close, "adjustflag": "3"})
            turns.append({"symbol": symbol, "date": day, "volume": volume,
                          "turn": 0.5 + i * 0.002 + 0.1 * np.sin(i / 5),
                          "tradestatus": 1})
            previous_close = close
            if day >= 20240131:
                state = {"symbol": symbol, "trade_date": day, "listed": True,
                         "delisted": False, "universe_member": True,
                         "eligibility_status": "ELIGIBLE", "st_status": "NORMAL",
                         "suspension_status": "TRADING",
                         "board": "SZ_MAIN" if symbol.endswith("SZ") else "SH_MAIN"}
                next_open = date + pd.offsets.BDay(1)
                historical.append({**state, "available_at":
                                   f"{next_open:%Y-%m-%d}T09:30:00+08:00",
                                   "source_lineage": "SYNTHETIC"})
                if day >= 20240201:
                    states.append(state)
    bundle = {"daily": pd.DataFrame(bars), "turn": pd.DataFrame(turns),
              "states": pd.DataFrame(states), "historical": pd.DataFrame(historical),
              "source_hashes": {key: key for key in (
                  "daily_sha256", "turn_sha256", "states_sha256",
                  "historical_states_sha256", "corporate_actions_sha256")},
              "account_end_date": 20240731}
    strategy, backend = Fixed51VoteStrategy(), Fixed51AccountBackend()
    bundle["input_identity"] = input_identity(bundle, (), strategy.definition["catalog_sha256"])
    plan = prepare(strategy, backend)
    objective = "SYNTHETIC_S1_PUBLIC_ENTRY"
    budget_path = tmp_path / "budget.json"
    budget = SearchBudgetRegistryV1(objective, budget_path)
    budget.register_objective(1)
    budget.consume(budget.reserve("objective", objective))
    governance = StrategyBatchGovernanceV1(
        tmp_path / "governance", budget_path, objective, {strategy.strategy_id: plan})
    governance.confirm(
        {"origin": "USER_EXPLICIT_CURRENT_TASK", "statement": "SYNTHETIC_TEST_ONLY",
         "approved_plan_ids": {strategy.strategy_id: plan["plan_id"]},
         "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()},
        {"input_identity": bundle["input_identity"], "novelty": {strategy.strategy_id: {
            "allowed": True, "plan_id": plan["plan_id"],
            "reason": "FROZEN_REFERENCE_DIAGNOSTIC_NOT_SEARCH_CANDIDATE"}}},
    )
    governance.start(strategy.strategy_id)
    result = run(strategy, backend, frame=bundle, actions=(),
                 input_identity=bundle["input_identity"],
                 active_check=lambda: governance.active_execution(strategy.strategy_id))
    governance.settle(strategy.strategy_id, completed=True, seconds=1,
                      result_hash="SYNTHETIC")
    registry = pilot_registry()
    expected = {}
    for symbol in SYMBOLS:
        bars_for_symbol = bundle["daily"].loc[bundle["daily"].symbol == symbol]
        index = pd.Index(bars_for_symbol.date.to_numpy(dtype=int), name="date")
        series = {field: pd.Series(bars_for_symbol[field].to_numpy(dtype=float), index=index)
                  for field in ("open", "high", "low", "close", "volume", "amount", "prev_close")}
        vendor = bundle["turn"].loc[bundle["turn"].symbol == symbol]
        turn = pd.Series(vendor.turn.to_numpy(dtype=float), index=index)
        computed = {}
        for item in strategy.definition["indicators"]:
            computed[item["id"]] = registry.compute(
                item["id"], series["close"], version=item["version"],
                high=series["high"], low=series["low"], open_=series["open"],
                volume=series["volume"], amount=series["amount"],
                prev_close=series["prev_close"], params=item["params"],
                extra_data={"vendor_turn": turn})
        expected[symbol] = decision_trace(computed, strategy.definition)
    assert result["decisions"] == expected
    assert result["decision_state_rejections"] == []
    submitted = {symbol: [item for item in expected[symbol]
                          if 20240201 <= item["date"] <= 20240731]
                 for symbol in SYMBOLS}
    expected_account = run_chain(
        bundle["daily"], submitted, strategy.definition,
        "daily_sha256", "turn_sha256", "states_sha256", "corporate_actions_sha256",
        execution_states=bundle["states"], historical_states_hash="historical_states_sha256",
        account_end_date=20240731, corporate_events=(),
    )
    assert result["chain"]["issues"] == []
    assert result["chain"]["trades"] == expected_account["trades"]
    assert result["chain"]["independent_account_checks"] == expected_account["independent_account_checks"]
    assert result["report"]["qualification"] == "NOT_ASSESSED"
    changed = bundle["turn"].copy()
    changed.loc[0, "turn"] += 1
    assert input_identity({**bundle, "turn": changed}, (), strategy.definition["catalog_sha256"]) != bundle["input_identity"]
