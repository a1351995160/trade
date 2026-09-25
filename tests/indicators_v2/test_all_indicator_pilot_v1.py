"""The fixed pilot must not silently drop an indicator from its decision."""

import json
import numpy as np
import pandas as pd
import pytest

from scripts.probe_all_indicator_strategy_v1 import (
    BUY_VOTES,
    INDICATOR_COUNT,
    decision_trace,
    known_bool,
    pilot_registry,
    probe,
    run_chain,
    sha256_file,
    strategy_definition,
)
from chanlun_trader.engine.engine import BacktestEngineV2


class IndicatorStub:
    def __init__(self, output, rising):
        self.index = pd.Index([20240102, 20240103], name="date")
        self._output = output
        self._value = pd.Series([1.0, 2.0 if rising else 0.0], index=self.index)

    def output(self, name):
        assert name == self._output
        return self._value

    def ready(self):
        return pd.Series([True, True], index=self.index)


def test_every_registered_indicator_can_change_the_fixed_decision():
    registry = pilot_registry()
    definition = strategy_definition(registry)
    items = definition["indicators"]
    assert len(items) == INDICATOR_COUNT
    assert {item["id"] for item in items} == {spec.indicator_id for spec in registry.specs()}
    assert next(item["version"] for item in items if item["id"] == "TURNOVER_RATE") == "TURNOVER_RATE_BAOSTOCK_TURN_V1"

    for pivotal in range(INDICATOR_COUNT):
        supporting = {item["id"] for index, item in enumerate(items)
                      if index != pivotal}
        supporting = set(sorted(supporting)[:BUY_VOTES - 1])
        results = {
            item["id"]: IndicatorStub(item["primary_output"], item["id"] in supporting)
            for item in items
        }
        assert decision_trace(results, definition)[0]["decision_at_close"] == "SELL"
        results[items[pivotal]["id"]] = IndicatorStub(
            items[pivotal]["primary_output"], True)
        assert decision_trace(results, definition)[0]["decision_at_close"] == "BUY"


def test_missing_indicator_blocks_decision():
    registry = pilot_registry()
    definition = strategy_definition(registry)
    items = definition["indicators"]
    results = {item["id"]: IndicatorStub(item["primary_output"], True)
               for item in items[1:]}
    with pytest.raises(ValueError, match="ALL_INDICATORS_REQUIRED_FOR_DECISION"):
        decision_trace(results, definition)


def test_vendor_turnover_uses_reported_percentage_without_float_shares():
    registry = pilot_registry()
    index = pd.Index([20240102, 20240103], name="date")
    result = registry.compute(
        "TURNOVER_RATE", pd.Series([10.0, 10.1], index=index),
        version="TURNOVER_RATE_BAOSTOCK_TURN_V1",
        extra_data={"vendor_turn": pd.Series([0.6921, 1.2345], index=index)},
    )
    assert result.output("turnover_rate").tolist() == [0.6921, 1.2345]
    assert result.ready().tolist() == [True, True]


def test_unknown_execution_state_flag_never_qualifies():
    assert known_bool(True, True)
    assert known_bool(np.bool_(False), False)
    assert not known_bool(pd.NA, True)
    assert not known_bool("True", True)


def test_file_hash_rejects_paths_outside_pilot_roots(tmp_path):
    sample = tmp_path / "sample.json"
    sample.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="PILOT_INPUT_PATH_NOT_ALLOWED"):
        sha256_file(sample)
    assert len(sha256_file(sample, fixture_root=tmp_path)) == 64


def test_full_catalog_reaches_reconciled_account_with_explicit_vendor_turn(tmp_path, monkeypatch):
    dates = pd.bdate_range("2023-10-09", "2024-05-31")
    bars = []
    turns = []
    states = []
    for symbol in ("000001.SZ", "600000.SH"):
        for i, date in enumerate(dates):
            close = 10.0 + 0.02 * i + 0.7 * np.sin(i / 6)
            volume = 1_000_000 + 100_000 * np.sin(i / 5)
            day = int(date.strftime("%Y%m%d"))
            bars.append({"symbol": symbol, "date": day, "open": close * 0.995,
                         "high": close * 1.02, "low": close * 0.98, "close": close,
                         "volume": volume, "amount": close * volume,
                         "prev_close": 10.0 if i == 0 else bars[-1]["close"],
                         "adjustflag": "3"})
            turns.append({"symbol": symbol, "date": day, "volume": volume,
                          "turn": 0.5 + 0.002 * i + 0.1 * np.sin(i / 5),
                          "tradestatus": 1})
            if 20240201 <= day <= 20240531:
                states.append({"symbol": symbol, "trade_date": day, "listed": True,
                               "delisted": False, "universe_member": True,
                               "eligibility_status": "ELIGIBLE", "st_status": "NORMAL",
                               "suspension_status": "TRADING"})
    daily_path = tmp_path / "daily.parquet"
    turn_path = tmp_path / "turn.parquet"
    states_path = tmp_path / "states.parquet"
    actions_path = tmp_path / "actions.json"
    pd.DataFrame(bars).to_parquet(daily_path)
    pd.DataFrame(turns).to_parquet(turn_path)
    pd.DataFrame(states).to_parquet(states_path)
    actions_path.write_text(json.dumps({
        "provider": "BaoStock", "api": "query_dividend_data",
        "queried_report_years": [2022, 2023, 2024],
        "symbols": ["000001.SZ", "600000.SH"],
        "account_dates": [20240201, 20240531],
        "account_window_events": [],
    }), encoding="utf-8")

    result = probe(daily_path, turn_path, states_path, actions_path, fixture_root=tmp_path)

    assert result["blockers"] == []
    assert result["signal_status"] == "FIXED_RULE_SIGNALS_SUBMITTED"
    assert result["account_status"] == "RECONCILED_DIAGNOSTIC"
    assert result["chain"]["n_account_days"] == len(states) // 2
    assert result["chain"]["n_trades"] > 0
    assert result["chain"]["issues"] == []
    assert result["prefix_causality_checks"]["000001.SZ"]["n_indicator_cut_checks"] == 153
    assert all(len(result["decision_trace"][symbol]) > 0
               for symbol in ("000001.SZ", "600000.SH"))
    assert all(sum(item["status"] == "COMPUTABLE"
                   for item in sample["indicators"].values()) == INDICATOR_COUNT
               for sample in result["samples"].values())

    execution_states = pd.DataFrame(states)
    execution_states["board"] = execution_states.symbol.map(
        {"000001.SZ": "SZ_MAIN", "600000.SH": "SH_MAIN"})
    gated = run_chain(pd.DataFrame(bars), result["decision_trace"], result["strategy"],
                      "daily", "turn", "states", "actions",
                      execution_states=execution_states, historical_states_hash="historical")
    assert gated["trades"] == result["chain"]["trades"]
    assert gated["independent_account_checks"] == result["chain"]["independent_account_checks"]
    assert gated["execution_assumptions"]["historical_execution_state_gate"] is True
    assert gated["n_no_trade_days"] > 0
    assert gated["n_independent_t1_lot_checks"] > 0

    first_buy = next(trade for trade in gated["trades"] if trade["side"] == "BUY")
    blocked_states = execution_states.copy()
    blocked_states.loc[(blocked_states.symbol == first_buy["symbol"])
                       & (blocked_states.trade_date == first_buy["date"]), "st_status"] = "ST"
    blocked = run_chain(pd.DataFrame(bars), result["decision_trace"], result["strategy"],
                        "daily", "turn", "states", "actions",
                        execution_states=blocked_states, historical_states_hash="historical")
    assert not any(trade["symbol"] == first_buy["symbol"]
                   and trade["date"] == first_buy["date"] and trade["side"] == "BUY"
                   for trade in blocked["trades"])
    assert any("KNOWN_INELIGIBLE" in order["last_event_message"]
               for order in blocked["orders"])

    suspended_states = execution_states.copy()
    suspended_states.loc[(suspended_states.symbol == first_buy["symbol"])
                         & (suspended_states.trade_date == first_buy["date"]),
                         "suspension_status"] = "SUSPENDED"
    suspended = run_chain(pd.DataFrame(bars), result["decision_trace"], result["strategy"],
                          "daily", "turn", "states", "actions",
                          execution_states=suspended_states, historical_states_hash="historical")
    assert not any(trade["symbol"] == first_buy["symbol"]
                   and trade["date"] == first_buy["date"] and trade["side"] == "BUY"
                   for trade in suspended["trades"])
    assert any("SUSPENDED" in order["last_event_message"] for order in suspended["orders"])

    missing_states = execution_states.loc[~(
        (execution_states.symbol == first_buy["symbol"])
        & (execution_states.trade_date == first_buy["date"]))]
    missing = run_chain(pd.DataFrame(bars), result["decision_trace"], result["strategy"],
                        "daily", "turn", "states", "actions",
                        execution_states=missing_states, historical_states_hash="historical")
    assert not any(trade["symbol"] == first_buy["symbol"]
                   and trade["date"] == first_buy["date"] and trade["side"] == "BUY"
                   for trade in missing["trades"])
    assert any("EXECUTION_STATE_UNKNOWN" in order["last_event_message"]
               for order in missing["orders"])

    no_signals = run_chain(pd.DataFrame(bars), {symbol: [] for symbol in result["decision_trace"]},
                           result["strategy"], "daily", "turn", "states", "actions",
                           execution_states=execution_states, historical_states_hash="historical")
    assert no_signals["issues"] == []
    assert no_signals["n_no_trade_days"] == no_signals["n_account_days"]
    assert no_signals["n_trades"] == 0

    missing_bars = pd.DataFrame(bars)
    missing_bars = missing_bars.loc[~((missing_bars.symbol == first_buy["symbol"])
                                      & (missing_bars.date == first_buy["date"]))]
    with pytest.raises(ValueError, match="ACCOUNT_DAILY_COVERAGE_INCOMPLETE"):
        run_chain(missing_bars, result["decision_trace"], result["strategy"],
                  "daily", "turn", "states", "actions",
                  execution_states=execution_states, historical_states_hash="historical")

    first_buy = next(trade for trade in gated["trades"] if trade["side"] == "BUY"
                     and any(later["symbol"] == trade["symbol"] and later["side"] == "SELL"
                             and later["date"] > trade["date"] for later in gated["trades"]))
    account_days = [item["date"] for item in gated["independent_account_checks"]]
    record_date = first_buy["date"]
    payment_date = account_days[account_days.index(record_date) + 1]
    corporate = run_chain(pd.DataFrame(bars), result["decision_trace"], result["strategy"],
                          "daily", "turn", "states", "actions",
                          execution_states=execution_states, historical_states_hash="historical",
                          corporate_events=({
                              "event_id": "SYNTHETIC_DIVIDEND", "symbol": first_buy["symbol"],
                              "event_type": "CASH_DIVIDEND", "record_date": record_date,
                              "effective_date": payment_date, "payment_date": payment_date,
                              "terms": {"cash_per_share": 0.1,
                                        "tax_rule": {"kind": "DEFERRED_INDIVIDUAL_2015_101",
                                                     "source": "SYNTHETIC_TAX_RULE"}},
                              "units": "CNY_PER_SHARE", "source": "SYNTHETIC_ACTION",
                              "source_published_at": "SYNTHETIC_BEFORE_RECORD",
                          },))
    assert corporate["issues"] == []
    assert corporate["corporate_account"]["dividend_income"] > 0
    assert corporate["corporate_account"]["dividend_tax_withheld"] > 0
    assert any(item["phase"] == "DEFERRED_INDIVIDUAL_TAX"
               for item in corporate["corporate_account"]["event_audit"])

    missing_turn = probe(daily_path, states_path=states_path, actions_path=actions_path,
                         fixture_root=tmp_path)
    assert any("TURNOVER_RATE:vendor_turn" in reason for reason in missing_turn["blockers"])
    assert missing_turn["chain"] is None

    bad_turn = pd.DataFrame(turns)
    bad_turn.loc[0, "volume"] += 1
    bad_turn.to_parquet(turn_path)
    mismatch = probe(daily_path, turn_path, states_path, actions_path, fixture_root=tmp_path)
    assert "BAOSTOCK_TURN_VOLUME_MISMATCH:000001.SZ" in mismatch["blockers"]
    assert mismatch["chain"] is None

    pd.DataFrame(turns).to_parquet(turn_path)
    actions_path.write_text(json.dumps({
        "provider": "BaoStock", "api": "query_dividend_data",
        "queried_report_years": [2022, 2023, 2024],
        "symbols": ["000001.SZ", "600000.SH"],
        "account_dates": [20240201, 20240531],
        "account_window_events": [{"symbol": "000001.SZ", "ex_date": "2024-03-01"}],
    }), encoding="utf-8")
    action_blocked = probe(daily_path, turn_path, states_path, actions_path,
                           fixture_root=tmp_path)
    assert "ACCOUNT_CORPORATE_ACTION_SCREEN_INVALID_OR_EVENT_PRESENT" in action_blocked["blockers"]
    assert action_blocked["chain"] is None

    actions_path.write_text(json.dumps({
        "provider": "BaoStock", "api": "query_dividend_data",
        "queried_report_years": [2022, 2023, 2024],
        "symbols": ["000001.SZ", "600000.SH"],
        "account_dates": [20240201, 20240531],
        "account_window_events": [],
    }), encoding="utf-8")
    original_run = BacktestEngineV2.run

    def corrupted_run(self):
        engine_result = original_run(self)
        engine_result.ledger.snapshots[-1].cash += 1.0
        engine_result.ledger.snapshots[-1].equity += 1.0
        return engine_result

    monkeypatch.setattr(BacktestEngineV2, "run", corrupted_run)
    corrupted = probe(daily_path, turn_path, states_path, actions_path,
                      fixture_root=tmp_path)
    assert "ACCOUNT_RECONCILIATION_FAILED" in corrupted["blockers"]
    assert any(issue.startswith("DAILY_ACCOUNT_MISMATCH:") for issue in corrupted["chain"]["issues"])
