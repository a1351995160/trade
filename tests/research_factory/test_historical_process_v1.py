from copy import deepcopy

import pandas as pd
import pytest

from chanlun_trader.research_factory.formal_account_backend_v1 import (
    BASE_COSTS, STRESS_COSTS,
)
from chanlun_trader.research_factory.historical_process_v1 import (
    PROFILE, historical_input_identity, prepare_historical_account, run_historical_account,
)
from test_formal_account_backend_v1 import PROPOSAL, execute as execute_formal, fixture


def historical_fixture():
    window, bundle = fixture()
    bundle.update(profile=PROFILE, open_snapshots=[], close_snapshots=[])
    bundle["source_hashes"]["execution_profile"] = PROFILE
    # 删除测试正式快照留下的时刻；历史入口不冒充实际采集时间。
    bundle["states"] = bundle["states"].drop(columns="available_at")
    return window, bundle


def execute(window, bundle, proposal=PROPOSAL, costs="BASE"):
    identity = historical_input_identity(bundle, window)
    plan = prepare_historical_account(proposal, strategy_id="historical", window=window, costs=costs)
    receipt = {"strategy_plans": {"historical": plan}, "input_identity": identity,
               "novelty": {"historical": {"allowed": True}},
               "execution_purpose": "historical", "execution_consumed": True}
    return run_historical_account(proposal, strategy_id="historical", bundle=bundle, window=window,
                                  costs=costs, input_identity=identity, active_check=lambda: receipt)


def test_historical_base_stress_and_benchmark_use_same_account_chain(monkeypatch):
    from chanlun_trader.research_factory import formal_account_backend_v1 as account
    window, bundle = historical_fixture()
    original, calls = account.run_chain, []

    def tracked(*args, **kwargs):
        calls.append((kwargs["observed_sessions"], kwargs["execution_costs"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(account, "run_chain", tracked)
    base = execute(window, bundle)
    stress = execute(window, bundle, costs="STRESS")
    benchmark = execute(window, bundle, proposal=None)
    assert calls == [(None, BASE_COSTS), (None, STRESS_COSTS), (None, BASE_COSTS)]
    assert base["decisions"] == stress["decisions"]
    assert base["fills"] and stress["fills"]
    assert base["fills"][0]["price"] != stress["fills"][0]["price"]
    assert len(benchmark["fills"]) == 2
    assert all(t["side"] == "BUY" for t in benchmark["fills"])
    for result in (base, stress, benchmark):
        assert result["profile"] == PROFILE and result["research_only"] is True
        assert result["strategy_qualified"] is False
        assert result["independent_confirmation_eligible"] is False
        assert result["report"]["profile"] == PROFILE
        assert result["report"]["qualification"] == "NOT_ASSESSED"
        assert result["report"]["strategy_qualified"] is False
        assert result["chain"]["issues"] == []
        assert result["chain"]["execution_assumptions"]["fill_reference"] == "NEXT_SESSION_OPEN_DAILY_BAR"
        assert len(result["ledger"]) == 30
        assert all(row["max_abs_ledger_delta"] <= .001 for row in result["ledger"])
        assert [row["date"] for row in result["daily_returns"]] == window["calendar"][80:]
        assert all("decision_at" not in row for rows in result["decisions"].values() for row in rows)
        assert any(path.endswith("historical_process_v1.py")
                   for path in result["strategy_plan"]["backend"]["source_hashes"])
    assert base == execute(window, bundle)


def test_historical_dividend_uses_independent_entitlements_and_warmup_adjustment():
    window, bundle = historical_fixture()
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
        mask = (bundle["daily"].symbol == symbol) & (bundle["daily"].date == effective)
        bundle["daily"].loc[mask, "prev_close"] -= .1
    bundle["events"] = events
    result = execute(window, bundle, proposal=None)
    assert result["chain"]["issues"] == []
    account = result["chain"]["corporate_account"]
    assert [row["event_id"] for row in account["events"]] == ["cash_90"]
    assert account["dividend_income"] > 0
    assert "cash_35" not in account["independent_entitlements"]


@pytest.mark.parametrize("mutation,code", [
    (lambda b: b.pop("profile"), "PROFILE_REQUIRED"),
    (lambda b: b.update(profile="OBSERVED"), "PROFILE_REQUIRED"),
    (lambda b: b["source_hashes"].pop("execution_profile"), "PROFILE_IDENTITY_REQUIRED"),
    (lambda b: b.update(open_snapshots=[{}]), "SNAPSHOTS_FORBIDDEN"),
    (lambda b: b.update(close_snapshots=[{}]), "SNAPSHOTS_FORBIDDEN"),
])
def test_historical_rejects_wrong_profile_or_observed_snapshots(mutation, code):
    window, bundle = historical_fixture()
    mutation(bundle)
    with pytest.raises(ValueError, match=code):
        execute(window, bundle)


def test_formal_entry_still_requires_observed_snapshots():
    window, bundle = historical_fixture()
    with pytest.raises(ValueError, match="FORMAL_OBSERVED_SESSION_COVERAGE_INVALID"):
        execute_formal(window, bundle)


def test_historical_identity_and_authorization_cannot_be_reused_for_changed_input():
    window, bundle = historical_fixture()
    identity = historical_input_identity(bundle, window)
    plan = prepare_historical_account(PROPOSAL, strategy_id="historical", window=window)
    receipt = {"strategy_plans": {"historical": plan}, "input_identity": identity,
               "novelty": {"historical": {"allowed": True}},
               "execution_purpose": "historical", "execution_consumed": True}
    changed = deepcopy(bundle)
    changed["turn"].loc[0, "turn"] += .1
    assert historical_input_identity(changed, window) != identity
    with pytest.raises(PermissionError, match="FORMAL_INPUT_CHANGED"):
        run_historical_account(PROPOSAL, strategy_id="historical", bundle=changed, window=window,
                               input_identity=identity, active_check=lambda: receipt)
