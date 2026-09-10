"""真实runner成交的时序核验；错误注入只发生于返回证据副本。"""
from copy import deepcopy

import pandas as pd
import pytest

from r1_caller_fixture import fixture
from test_r1_batch_caller_inputs import prepare
from chanlun_trader.research_factory.execution_evidence import audit_microstructure


@pytest.fixture
def execution(tmp_path):
    case = fixture(tmp_path)
    caller, policy, _, record, _ = case
    inputs = prepare(case)
    result = caller._invoke_runner(policy, record, "MICROSTRUCTURE_TEST", inputs, portfolio_name="BASE_RESEARCH")
    return result["engine"], inputs


def test_actual_runner_execution_has_valid_timing(execution):
    result, inputs = execution
    assert result.ledger.trades
    assert audit_microstructure(result, inputs)["passed"] is True


@pytest.mark.parametrize("defect", ["same_bar", "nat", "t1", "future_factor", "suspension", "outside_open"])
def test_actual_execution_with_invalid_evidence_never_passes(execution, defect):
    original, original_inputs = execution
    result, inputs = deepcopy(original), deepcopy(original_inputs)
    trade = result.ledger.trades[0]
    if defect == "same_bar":
        signal = next(item for item in result.signals if item.signal_id == result.orders.orders[trade.order_id].signal_id)
        trade.fill_time = signal.generated_at
    elif defect == "nat":
        trade.fill_time = pd.NaT
    elif defect == "t1":
        sell = next(item for item in result.ledger.trades if item.side.value == "SELL")
        result.ledger.lots[sell.lot_id].sellable_from = sell.fill_time + pd.Timedelta(days=1)
    elif defect == "future_factor":
        inputs["factor_values"]["available_at"] = "2030-01-01T15:00:00+08:00"
    elif defect == "outside_open":
        trade.fill_time += pd.Timedelta(hours=1)
    else:
        # 修改实际返回store的对应交易日记录，检查成交不再具备可交易证据。
        day = int(trade.fill_time.strftime("%Y%m%d"))
        inputs["store"].daily_raw[trade.symbol].loc[day, "volume"] = 0
    assert audit_microstructure(result, inputs)["passed"] is False
