"""账户身份回归：独立经济核账必须兼容冻结计划的精确身份。"""
from copy import deepcopy
import hashlib
from math import inf, nextafter
from pathlib import Path

import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.formal_account_backend_v1 import BASE_COSTS, STRESS_COSTS
from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3
from chanlun_trader.research_factory.strategy_interface_v1 import prepare, run
from chanlun_trader.research_factory.universe_account_backend_v1 import UniverseAccountBackendV1
from chanlun_trader.research_factory.universe_account_inputs_v1 import (
    prepare_universe_account_inputs_v1, universe_input_identity_v1,
)
from chanlun_trader.research_factory.universe_evidence_v1 import _Reconstruction, reconstruct_universe_account
from universe_test_fixture_v1 import fixture, proposal


SYMBOL = "000001.SZ"
STRATEGY_ID = "identity_regression"
INITIAL_CASH = 50000.


def _inputs(*, max_hold_sessions=2, days_count=67, sell_capacity=None,
            entry_price=13.45, sell_price=None):
    window, bundle = fixture(symbols=[SYMBOL], days_count=days_count)
    # 全历史参考价同步设置，避免引入首个账户日跳空或价格参考冲突。
    frame = bundle["daily"]
    frame.loc[:, ["open", "close", "prev_close"]] = entry_price
    if sell_price is not None:
        frame.loc[frame["date"].ge(window["calendar"][64]), ["open", "close"]] = sell_price
        frame.loc[frame["date"].ge(window["calendar"][65]), "prev_close"] = sell_price
    frame.loc[:, "high"] = frame["close"] * 1.01
    frame.loc[:, "low"] = frame["close"] * .99
    frame.loc[:, "amount"] = frame["close"] * frame["volume"]
    if sell_capacity is not None:
        frame.loc[frame["date"].eq(window["calendar"][63]), "volume"] = sell_capacity * 10.
    value = proposal()
    value.update(target_weight=.25, max_hold_sessions=max_hold_sessions)
    strategy = ResearchRuleStrategyV3(value, strategy_id=STRATEGY_ID)
    return window, bundle, strategy


def _case(costs, **options):
    window, bundle, strategy = _inputs(**options)
    backend = UniverseAccountBackendV1(window, costs=costs, initial_cash=INITIAL_CASH,
                                      max_positions=1, max_symbol_exposure_bps=5000)
    identity = universe_input_identity_v1(bundle, window)
    receipt = {"strategy_plans": {STRATEGY_ID: prepare(strategy, backend)}, "input_identity": identity,
               "novelty": {STRATEGY_ID: {"allowed": True}}, "execution_purpose": STRATEGY_ID,
               "execution_consumed": True}
    result = run(strategy, backend, frame=bundle, actions=bundle["events"], input_identity=identity,
                 active_check=lambda: receipt)
    return bundle, window, strategy.payload, result


def _audit(bundle, window, rule, result, costs):
    return reconstruct_universe_account(bundle, window, result, initial_cash=INITIAL_CASH,
                                        costs=costs, strategy_id=STRATEGY_ID, rule=rule)


@pytest.fixture(scope="module", params=[BASE_COSTS, STRESS_COSTS], ids=["base", "stress"])
def completed_case(request):
    return request.param, _case(request.param)


def test_fractional_fill_price_passes_later_plan_account_identity(completed_case):
    costs, (bundle, window, rule, result) = completed_case
    assert [fill["side"] for fill in result["fills"]] == ["BUY", "SELL"]
    buy, sell = result["fills"]
    assert buy["quantity"] == sell["quantity"] == 900
    assert buy["price"] == (13.4634 if costs == BASE_COSTS else 13.4769)
    assert buy["fee"] == costs["min_commission"]
    assert window["calendar"].index(result["decisions"][-1]["date"]) > 64
    assert result["reconciliation"]["passed"]
    rebuilt = _audit(bundle, window, rule, result, costs)
    assert rebuilt["metrics"]["trade_count"] == 2
    assert rebuilt["daily_accounts"] == result["daily_accounts"]


@pytest.mark.parametrize("costs,quantity", [(BASE_COSTS, 168), (STRESS_COSTS, 210)], ids=["base", "stress"])
def test_partial_sell_passes_later_plan_account_identity(costs, quantity):
    bundle, window, rule, result = _case(costs, sell_capacity=quantity)
    assert [fill["side"] for fill in result["fills"]] == ["BUY", "SELL", "SELL"]
    assert [fill["quantity"] for fill in result["fills"]] == [900, quantity, 900 - quantity]
    assert result["reconciliation"]["passed"]
    assert _audit(bundle, window, rule, result, costs)["metrics"]["trade_count"] == 3


def test_observed_stress_full_sell_preserves_next_plan_identity():
    # 冻结实际观察到的数值；仅用合成行情，CI不依赖任何真实账户文件。
    bundle, window, rule, result = _case(STRESS_COSTS, entry_price=10.97, sell_price=10.24)
    assert [fill["side"] for fill in result["fills"]] == ["BUY", "SELL"]
    buy, sell = result["fills"]
    assert buy["quantity"] == sell["quantity"] == 1100
    assert (buy["price"], buy["fee"]) == (10.9919, 10.)
    assert (sell["price"], sell["fee"]) == (10.2195, 15.6207)
    cost = buy["quantity"] * buy["price"] + buy["fee"]
    gross_pnl = (sell["price"] - cost / buy["quantity"]) * sell["quantity"]
    old_realized = gross_pnl - sell["fee"] * sell["quantity"] / sell["quantity"]
    exact_realized = gross_pnl - sell["fee"] * (sell["quantity"] / sell["quantity"])
    assert old_realized == -875.2606999999999
    assert exact_realized == -875.2606999999998 == sell["realized_pnl"]
    assert old_realized != exact_realized

    economic = result["final_account_checkpoint"]["economic"]
    state = {"initial_cash": INITIAL_CASH, "prices": {SYMBOL: sell["price"]},
             **{key: deepcopy(economic[key]) for key in ("cash", "reserved_cash", "positions", "lots")}}
    next_plan = next(row["plan"] for row in result["decisions"]
                     if row["plan"]["next_session"] == window["calendar"][65])
    assert next_plan["account_identity"] == stable_hash(state)
    state["positions"][STRATEGY_ID + ":" + SYMBOL]["realized_pnl"] = old_realized
    assert next_plan["account_identity"] != stable_hash(state)
    assert result["reconciliation"]["passed"]
    rebuilt = _audit(bundle, window, rule, result, STRESS_COSTS)
    assert rebuilt["metrics"]["trade_count"] == 2
    assert rebuilt["daily_accounts"] == result["daily_accounts"]


@pytest.mark.parametrize("mutation,reason", [
    ("cash", "FINAL_CASH_CONFLICT"),
    ("position", "FINAL_POSITIONS_CONFLICT"),
    ("lot", "FINAL_LOTS_CONFLICT"),
])
def test_self_reported_reconciliation_does_not_allow_forged_economic_state(completed_case, mutation, reason):
    costs, (bundle, window, rule, result) = completed_case
    forged = deepcopy(result)
    economic = forged["final_account_checkpoint"]["economic"]
    if mutation == "cash":
        economic["cash"] += .01
    elif mutation == "position":
        economic["positions"][STRATEGY_ID + ":" + SYMBOL]["quantity"] += 100
    else:
        economic["lots"]["lot-000001"]["cost"] += .01
    forged["reconciliation"] = {"passed": True}
    with pytest.raises(ValueError, match=reason):
        _audit(bundle, window, rule, forged, costs)


def test_economic_comparison_keeps_subcent_tolerance(completed_case):
    costs, (bundle, window, rule, result) = completed_case
    perturbed = deepcopy(result)
    perturbed["final_account_checkpoint"]["economic"]["cash"] += 5e-7
    rebuilt = _audit(bundle, window, rule, perturbed, costs)
    assert rebuilt["daily_accounts"] == result["daily_accounts"]


def _initial_economic_state():
    return {"initial_cash": INITIAL_CASH, "cash": INITIAL_CASH, "reserved_cash": 0.,
            "prices": {}, "positions": {}, "lots": {}}


def _rehash_plan(plan):
    plan["plan_id"] = "PORTFOLIO_PAPER_" + stable_hash({key: value for key, value in plan.items()
                                                    if key != "plan_id"})


def test_plan_account_hash_stays_exact_below_economic_tolerance(completed_case):
    costs, (bundle, window, rule, result) = completed_case
    forged = deepcopy(result)
    plan = forged["decisions"][0]["plan"]
    initial = _initial_economic_state()
    assert plan["account_identity"] == stable_hash(initial)
    altered = deepcopy(initial)
    altered["cash"] = nextafter(altered["cash"], inf)
    assert 0 < altered["cash"] - initial["cash"] < 1e-6
    plan["account_identity"] = stable_hash(altered)
    _rehash_plan(plan)
    with pytest.raises(ValueError, match="PLAN_ACCOUNT_IDENTITY_CONFLICT") as caught:
        _audit(bundle, window, rule, forged, costs)
    evidence = caught.value.evidence
    assert evidence["account_date"] == window["account_start"]
    assert evidence["plan_account_identity"] == stable_hash(altered)
    assert evidence["reconstructed_account_identity"] == stable_hash(initial)
    assert evidence["reconstructed_account"] == initial


def test_auditor_does_not_consult_engine_or_scanner_as_answer(completed_case, monkeypatch):
    from chanlun_trader.engine.engine import BacktestEngineV2
    from chanlun_trader.research_factory.universe_dividend_accounting_v1 import UniverseDividendAccountingV1
    from chanlun_trader.research_factory.universe_signal_scan_v1 import UniverseSignalScanV1

    def forbidden(*args, **kwargs):
        raise AssertionError("AUDITOR_USED_ENGINE_OR_SCANNER_AS_ANSWER")

    monkeypatch.setattr(BacktestEngineV2, "__init__", forbidden)
    monkeypatch.setattr(UniverseDividendAccountingV1, "apply_fill", forbidden)
    monkeypatch.setattr(UniverseSignalScanV1, "__init__", forbidden)
    monkeypatch.setattr(UniverseSignalScanV1, "at", forbidden)
    costs, (bundle, window, rule, result) = completed_case
    assert _audit(bundle, window, rule, result, costs)["metrics"]["trade_count"] == 2


def test_frozen_backend_hashes_account_identity_source_dependencies(completed_case):
    _, (_, _, _, result) = completed_case
    sources = result["execution_description"]["source_hashes"]
    for name in ("daily_plan.py", "common.py"):
        paths = [Path(path) for path in sources if Path(path).name == name]
        assert len(paths) == 1
        path = paths[0]
        assert sources[str(path)] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["strategy_plan"]["backend"]["source_hashes"] == sources


def test_backend_failure_evidence_labels_observed_account_as_final_state(monkeypatch):
    import chanlun_trader.research_factory.universe_evidence_v1 as evidence_module

    original = evidence_module.reconstruct_universe_account

    def reject_forged_audit_input(bundle, window, result, **kwargs):
        # 仅在独立核验入口注入错误，原引擎仍完整执行并保留自己的期末账务。
        forged = deepcopy(result)
        plan = forged["decisions"][0]["plan"]
        plan["account_identity"] = "0" * 64
        _rehash_plan(plan)
        return original(bundle, window, forged, **kwargs)

    monkeypatch.setattr(evidence_module, "reconstruct_universe_account", reject_forged_audit_input)
    with pytest.raises(ValueError, match="PLAN_ACCOUNT_IDENTITY_CONFLICT") as caught:
        _case(STRESS_COSTS)
    evidence = caught.value.evidence
    assert evidence["account_date"] == 20220328
    assert evidence["observed_final_date"] == 20220405
    assert evidence["observed_final_date"] > evidence["account_date"]
    assert evidence["reconstructed_account"] == _initial_economic_state()
    final = evidence["observed_final_economic"]
    assert [trade["side"] for trade in final["trades"]] == ["BUY", "SELL"]
    assert final["lots"]["lot-000001"]["exit_state"] == "CLOSED"
    assert final["cash"] != evidence["reconstructed_account"]["cash"]


@pytest.mark.parametrize("costs,buy_price,sell_price,sell_fee", [
    (BASE_COSTS, 13.4634, 13.4365, 8.0232),
    (STRESS_COSTS, 13.4769, 13.4231, 13.0202),
], ids=["base", "stress"])
def test_fifo_multiple_buys_partial_sale_close_and_reentry_use_exact_arithmetic(
        costs, buy_price, sell_price, sell_fee):
    window, bundle, strategy = _inputs(days_count=70)
    inputs = prepare_universe_account_inputs_v1(bundle, window, stage="ACCOUNT",
        required_fields=strategy.requirements.fields, warmup_bars=strategy.requirements.warmup_sessions)
    account = _Reconstruction(inputs, strategy, INITIAL_CASH, costs, {"initial_cash": INITIAL_CASH})
    days = window["calendar"]
    buy_fee = costs["min_commission"]

    def fill(side, quantity, price, fee, day_index, lot_id, position_id, realized=0.):
        trade = {"trade_id": f"tr-{account.trade_count + 1:06d}", "symbol": SYMBOL,
                 "side": side, "quantity": quantity, "price": price, "gross_value": quantity * price,
                 "fee": fee, "lot_id": lot_id, "position_id": position_id, "realized_pnl": realized}
        account.fill(trade, days[day_index])

    first_cost, second_cost = 300 * buy_price + buy_fee, 600 * buy_price + buy_fee
    fill("BUY", 300, buy_price, buy_fee, 61, "lot-000001", "pos-000001")
    expected_cash = INITIAL_CASH - first_cost
    assert account.cash == expected_cash
    assert account.positions[SYMBOL]["average_cost"] == first_cost * 300 / 300 / 300
    fill("BUY", 600, buy_price, buy_fee, 62, "lot-000002", "pos-000001")
    expected_cash -= second_cost
    expected_average = (first_cost * 300 / 300 + second_cost * 600 / 600) / 900
    assert account.positions[SYMBOL]["average_cost"] == expected_average

    # 一次SELL跨两个FIFO lot；手续费先算份额再乘，与冻结身份采用同一经济语义。
    realized = (((sell_price - first_cost / 300) * 300 - sell_fee * (300 / 450))
                + ((sell_price - second_cost / 600) * 150 - sell_fee * (150 / 450)))
    fill("SELL", 450, sell_price, sell_fee, 64, None, "pos-000001", realized)
    expected_cash += 450 * sell_price - sell_fee
    assert account.cash == expected_cash
    assert account.positions[SYMBOL]["quantity"] == 450
    assert account.positions[SYMBOL]["realized_pnl"] == realized
    assert account.positions[SYMBOL]["average_cost"] == second_cost * 450 / 600 / 450
    assert account.lots["lot-000001"]["exit_state"] == "CLOSED"
    assert account.lots["lot-000002"]["remaining_quantity"] == 450

    final_realized = (sell_price - second_cost / 600) * 450 - sell_fee
    fill("SELL", 450, sell_price, sell_fee, 65, "lot-000002", "pos-000001", final_realized)
    expected_cash += 450 * sell_price - sell_fee
    assert account.cash == expected_cash
    assert account.positions[SYMBOL]["quantity"] == 0
    assert account.positions[SYMBOL]["average_cost"] == 0.
    assert account.positions[SYMBOL]["realized_pnl"] == realized + final_realized

    fill("BUY", 100, buy_price, buy_fee, 66, "lot-000003", "pos-000002")
    expected_cash -= 100 * buy_price + buy_fee
    assert account.cash == expected_cash
    assert account.positions[SYMBOL]["position_id"] == "pos-000002"
    assert account.positions[SYMBOL]["quantity"] == 100
    assert account.positions[SYMBOL]["realized_pnl"] == 0.
    assert account.positions[SYMBOL]["average_cost"] == (100 * buy_price + buy_fee) * 100 / 100 / 100
    assert account.economic_identity() == stable_hash(account.economic_state())
