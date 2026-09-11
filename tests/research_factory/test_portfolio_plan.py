"""真实冻结策略/计划/账本上的组合预览，不伪造使用资格。"""
import pandas as pd
import pytest

from r1_caller_fixture import fixture
from test_r1_batch_caller_inputs import prepare
from test_daily_plan import timestamp
from chanlun_trader.engine.fill import Fill
from chanlun_trader.engine.ledger import PortfolioLedger
from chanlun_trader.engine.signal import Side
from chanlun_trader.research_factory.daily_plan import account_identity
from chanlun_trader.research_factory.portfolio_plan import (
    PortfolioPreviewPolicyV1, StrategyAllocationV1, preview_portfolio_plan,
)


def setup(root, count=2):
    sources = {}
    for index in range(count):
        case = fixture(root / str(index), objective_id=f"M1_SYNTHETIC_{index}")
        _, execution, contract, record, _ = case
        # 输入由同一真实caller准备，不接受拼接的计划结果。
        inputs = prepare(case)
        sources[record.candidate.candidate_id] = dict(record=record, contract=contract, policy=execution, inputs=inputs)
    allocations = tuple(StrategyAllocationV1(candidate_id=key, contract_hash=value["contract"].content_hash,
        cash_bps=10000 // count, priority=index) for index, (key, value) in enumerate(sources.items()))
    policy = PortfolioPreviewPolicyV1(policy_id="M1_SYNTHETIC_TEST", version="1", allocations=allocations,
        overlap="ONE_STRATEGY_PER_SYMBOL", buy_sell_conflict="BLOCK_BUY_WHEN_EXIT_DUE",
        max_positions=20, max_symbol_exposure_bps=10000, max_buy_turnover_bps=10000,
        valid_until="2025-08-01T15:00:00+08:00")
    return sources, policy, PortfolioLedger(1000000.)


def preview(sources, policy, ledger, index=0):
    day = next(iter(sources.values()))["inputs"]["exec_calendar"][index]
    return preview_portfolio_plan(policy, sources, ledger, plan_at=timestamp(day))


def test_two_frozen_strategies_have_disjoint_cash_and_priority_ownership(tmp_path):
    sources, policy, ledger = setup(tmp_path)
    before = account_identity(ledger)
    result = preview(sources, policy, ledger)
    assert result["strategy_count"] == 2
    assert result["execution_ready"] is False
    assert result["usage_qualification"] == "NOT_VERIFIED"
    assert len(result["source_plans"]) == 2
    buys = [item for item in result["entries"] if item["quantity"]]
    assert buys
    assert len({item["symbol"] for item in buys}) == len(buys)
    assert {item["candidate_id"] for item in buys} == {policy.allocations[0].candidate_id}
    assert any(item["reason"] == "SYMBOL_OWNED_BY_OTHER_STRATEGY" for item in result["entries"])
    assert sum(item["quantity"] * item["reference_price"] + item["estimated_fee"] for item in buys) <= ledger.cash
    assert result == preview(sources, policy, ledger)
    assert account_identity(ledger) == before


def test_single_and_empty_strategy_states(tmp_path):
    sources, policy, ledger = setup(tmp_path, count=1)
    assert preview(sources, policy, ledger)["strategy_count"] == 1
    empty = policy.model_copy(update={"allocations": ()})
    result = preview_portfolio_plan(empty, {}, ledger, plan_at="2025-07-17T15:00:00+08:00")
    assert result["status"] == "NO_STRATEGIES"
    assert not result["entries"] and not result["holdings"]
    assert result["execution_ready"] is False


def test_version_or_expiry_excludes_and_changes_plan(tmp_path):
    sources, policy, ledger = setup(tmp_path, count=1)
    initial = preview(sources, policy, ledger)
    expired = policy.model_copy(update={"valid_until": "2025-07-16T15:00:00+08:00"})
    result = preview(sources, expired, ledger)
    assert result["status"] == "NOT_READY" and result["excluded"][0]["reason"] == "POLICY_EXPIRED"
    assert result["plan_id"] != initial["plan_id"]
    changed = policy.model_copy(update={"allocations": (policy.allocations[0].model_copy(update={"contract_hash": "0" * 64}),)})
    result = preview(sources, changed, ledger)
    assert result["excluded"][0]["reason"] == "STRATEGY_VERSION_CHANGED"
    assert not result["entries"]


def test_exposure_turnover_and_position_caps(tmp_path):
    sources, policy, ledger = setup(tmp_path)
    policy = policy.model_copy(update={"overlap": "SEPARATE_STRATEGY_LOTS", "max_buy_turnover_bps": 300,
        "max_symbol_exposure_bps": 200, "max_positions": 1})
    result = preview(sources, policy, ledger)
    buys = [item for item in result["entries"] if item["quantity"]]
    assert len(buys) == 1
    gross = sum(item["quantity"] * item["reference_price"] for item in buys)
    assert 0 < gross <= ledger.current_equity() * .02
    assert result["unallocated_cash"] >= 0


def test_exit_conflict_keeps_real_lot_owner_and_t_plus_one(tmp_path):
    sources, policy, ledger = setup(tmp_path)
    first = policy.allocations[0].candidate_id
    days = sources[first]["inputs"]["exec_calendar"]
    stamp = timestamp(days[0]).normalize() + pd.Timedelta(hours=9, minutes=30)
    trade, reason = ledger.apply_fill(Fill("BUY", "ORDER", first, "000001.SZ", Side.BUY, 100, 10., stamp))
    assert trade is not None, reason
    initial = preview(sources, policy, ledger)
    assert initial["holdings"][0]["sellable_quantity"] == 0
    due_index = sources[first]["record"].candidate.holding_period
    due = preview(sources, policy, ledger, index=due_index)
    exit_plan = next(item for item in due["holdings"] if item["action"] == "EXIT")
    assert exit_plan["candidate_id"] == first
    assert exit_plan["lot_id"] == trade.lot_id
    assert exit_plan["quantity"] == 100 and exit_plan["sellable_quantity"] == 100
    second = policy.allocations[1].candidate_id
    conflict = next(item for item in due["entries"] if item["candidate_id"] == second and item["symbol"] == "000001.SZ")
    assert conflict["quantity"] == 0 and conflict["reason"] == "EXIT_BUY_CONFLICT"


def test_unknown_holdings_rejected_and_input_not_ready_is_not_empty_success(tmp_path):
    sources, policy, ledger = setup(tmp_path, count=1)
    source = next(iter(sources.values()))
    inputs = source["inputs"]
    inputs["factor_values"]["available_at"] = "2030-01-01T15:00:00+08:00"
    result = preview(sources, policy, ledger)
    assert result["status"] == "NOT_READY"
    assert all(item["quantity"] == 0 for item in result["entries"])
    ledger.apply_fill(Fill("BUY", "ORDER", "UNKNOWN", "000001.SZ", Side.BUY, 100, 10., timestamp(inputs["exec_calendar"][0])))
    with pytest.raises(ValueError, match="PORTFOLIO_UNASSIGNED_HOLDINGS"):
        preview(sources, policy, ledger)


def test_policy_rejects_implicit_or_overallocated_cash(tmp_path):
    _, policy, _ = setup(tmp_path, count=1)
    data = policy.model_dump()
    data["allocations"] = (policy.allocations[0], policy.allocations[0])
    with pytest.raises(ValueError, match="PORTFOLIO_DUPLICATE_STRATEGY"):
        PortfolioPreviewPolicyV1(**data)
    data["allocations"] = (policy.allocations[0], policy.allocations[0].model_copy(update={"candidate_id": "OTHER"}))
    with pytest.raises(ValueError, match="PORTFOLIO_CASH_OVERALLOCATED"):
        PortfolioPreviewPolicyV1(**data)


def test_missing_member_blocks_new_cash_and_retains_unresolved_holdings(tmp_path):
    sources, policy, ledger = setup(tmp_path)
    first = policy.allocations[0].candidate_id
    day = next(iter(sources.values()))["inputs"]["exec_calendar"][0]
    ledger.apply_fill(Fill("BUY", "ORDER", first, "000001.SZ", Side.BUY, 100, 10., timestamp(day)))
    del sources[first]
    result = preview(sources, policy, ledger)
    assert result["status"] == "NOT_READY"
    assert result["holdings"][0]["action"] == "NOT_READY"
    assert result["holdings"][0]["candidate_id"] == first
    assert all(item["quantity"] == 0 for item in result["entries"])


@pytest.mark.parametrize("cash,reserved", [(float("nan"), 0), (100, 200), (100, float("inf"))])
def test_invalid_account_cannot_be_sanitized_by_subview(tmp_path, cash, reserved):
    sources, policy, ledger = setup(tmp_path, count=1)
    ledger.cash, ledger.reserved_cash = cash, reserved
    with pytest.raises(ValueError, match="PORTFOLIO_ACCOUNT_INVALID"):
        preview(sources, policy, ledger)
