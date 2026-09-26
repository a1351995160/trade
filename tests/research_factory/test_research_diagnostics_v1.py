"""探索诊断不能把工程失败、原路径费用或有限样本包装成策略资格。"""
from copy import deepcopy
import json

import pytest

from chanlun_trader.research_factory.context import PerformanceBlindGuard
from chanlun_trader.research_factory.research_diagnostics_v1 import (
    diagnose, feedback_view, render_markdown,
)


def account(*, final=99.0, fee=2.0, trades=True):
    return {
        "chain": {
            "status": "RECONCILED_DIAGNOSTIC", "issues": [],
            "n_account_days": 2, "n_trades": int(trades),
            "account_dates": [20240102, 20240103],
            "execution_assumptions": {"initial_cash": 100.0, "min_commission": 5},
            "independent_account_checks": [
                {"date": 20240102, "cash": 100.0, "market_value": 0.0,
                 "equity": 100.0, "max_abs_ledger_delta": 0.0},
                {"date": 20240103, "cash": final, "market_value": 0.0,
                 "equity": final, "max_abs_ledger_delta": 0.0},
            ],
            "trades": [{"fee": fee}] if trades else [], "orders": [],
        },
        "comparison_context": {"input_identity": "frozen-data"},
    }


def test_account_loss_cost_observation_and_blind_feedback():
    result = account(final=99.123456)
    diagnostic = diagnose(result, trial_id="trial-a")
    assert diagnostic["account_state"] == "COMPLETE"
    assert "COST_ERASES_ACCOUNT_GAIN" in diagnostic["reason_codes"]
    assert "EXPLORATORY_LOSS" in diagnostic["reason_codes"]
    assert diagnostic["metrics"]["max_drawdown"] == pytest.approx(.00876544)
    view = feedback_view(diagnostic)
    PerformanceBlindGuard.assert_blind(view)
    assert "99.123456" not in json.dumps(view)
    assert "metrics" not in view
    assert diagnostic["qualification"] == "NOT_ASSESSED"
    assert "尚未评审" in render_markdown(diagnostic)
    diagnostic["metrics"]["net_return"] = 999
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        feedback_view(diagnostic)


@pytest.mark.parametrize("change, expected", [
    (lambda x: x["chain"].update(issues=["CASH_MISMATCH"]), "ACCOUNT_UNRECONCILED"),
    (lambda x: x["chain"].update(n_account_days=3), "ACCOUNT_INCOMPLETE"),
    (lambda x: x["chain"]["independent_account_checks"][-1].update(equity=float("nan")), "ACCOUNT_INCOMPLETE"),
    (lambda x: x["chain"]["independent_account_checks"][-1].update(cash=90), "ACCOUNT_UNRECONCILED"),
    (lambda x: x["chain"]["trades"][0].update(fee=-1), "ACCOUNT_INCOMPLETE"),
])
def test_invalid_accounts_do_not_receive_economic_conclusions(change, expected):
    result = account()
    change(result)
    diagnostic = diagnose(result, trial_id="trial-b")
    assert diagnostic["account_state"] == expected
    assert diagnostic["metrics"] == {}
    assert "EXPLORATORY_LOSS" not in diagnostic["reason_codes"]


def test_no_trades_is_distinct_from_failure_and_limited_sample():
    diagnostic = diagnose(account(final=100, trades=False), trial_id="trial-c")
    assert diagnostic["account_state"] == "COMPLETE"
    assert "NO_TRADES" in diagnostic["reason_codes"]
    assert "LIMITED_SAMPLE" in diagnostic["reason_codes"]
    assert "EXPLORATORY_LOSS" not in diagnostic["reason_codes"]
    assert diagnose({}, trial_id="trial-d")["account_state"] == "ACCOUNT_INCOMPLETE"


def test_benchmark_requires_same_data_window_cost_and_complete_account():
    candidate, benchmark = account(final=103), account(final=101)
    comparison = diagnose(candidate, trial_id="trial-e", benchmark=benchmark)["benchmark"]
    assert comparison["status"] == "AVAILABLE"
    assert comparison["net_return_difference"] == pytest.approx(.02)
    for change in (
        lambda x: x.pop("comparison_context"),
        lambda x: x["comparison_context"].update(input_identity="other-data"),
        lambda x: x["chain"]["execution_assumptions"].update(min_commission=0),
        lambda x: x["chain"].update(issues=["BROKEN"]),
    ):
        altered = deepcopy(benchmark)
        change(altered)
        comparison = diagnose(candidate, trial_id="trial-e", benchmark=altered)["benchmark"]
        assert comparison["status"] == "NOT_COMPARABLE"
        assert "net_return_difference" not in comparison
