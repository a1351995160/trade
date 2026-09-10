"""批次调用方复用正式文件输入与双组合结果；不启动批次研究。"""
from dataclasses import replace
import json

import pandas as pd
import pytest

from r1_caller_fixture import fixture
from chanlun_trader.research_factory.real_runtime import RealFactoryRuntimeV1


def prepare(case):
    caller, policy, contract, record, cache = case
    return RealFactoryRuntimeV1._prepare_candidate_inputs(caller, policy, [record], [contract], cache)[contract.candidate_id]


def test_batch_file_inputs_and_independent_portfolios(tmp_path):
    case = fixture(tmp_path, file_registry_identity=True)
    caller, policy, contract, record, cache = case
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    inputs = prepare(case)
    cached = pd.read_parquet(cache)
    pd.testing.assert_series_equal(inputs["factor_values"].available_at, cached.available_at)
    assert inputs["input_diagnostics"]["contract_hash"] == contract.content_hash
    outputs = []
    for name, variant in (("BASE_RESEARCH", policy), ("SMALL_CAPITAL_10K", replace(policy,
            initial_cash=policy.small_capital_cash, max_positions=policy.small_capital_slots,
            lot_size=policy.small_capital_lot_size))):
        result = caller._invoke_runner(variant, record, "BATCH_INPUT_SYNTHETIC", inputs, portfolio_name=name)
        assert result["status"] == "DIAGNOSTIC_ONLY"
        assert result["metrics"]["filled_order_count"] > 0
        assert result["metrics_ref"] is None
        outputs.append(result)
    assert outputs[0]["engine"].ledger is not outputs[1]["engine"].ledger
    assert before == {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_batch_frozen_registry_file_change_is_rejected(tmp_path):
    case = fixture(tmp_path, file_registry_identity=True)
    path = tmp_path / case[2].factor_event_registry_identities["factor_registry"]["path"]
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="REGISTRY_IDENTITY_MISMATCH"):
        prepare(case)


@pytest.mark.parametrize("damage,reason", [
    ("time", "INVALID_FACTOR_AVAILABLE_AT"),
    ("calendar", "CALENDAR_CONFLICT"),
    ("column", "FACTOR_CACHE_EVIDENCE_MISSING"),
])
def test_batch_input_errors_do_not_become_no_signal(tmp_path, damage, reason):
    case = fixture(tmp_path)
    caller, policy, contract, record, cache = case
    if damage == "calendar":
        path = tmp_path / "data/research/security_state/raw/trade_calendar.json"
        value = json.loads(path.read_bytes())
        value["trade_dates"].remove(contract.research_period_identity["start"])
        path.write_text(json.dumps(value), encoding="utf-8")
    else:
        factors = pd.read_parquet(cache)
        if damage == "time":
            factors.loc[0, "available_at"] = ""
        else:
            factors = factors.drop(columns="available_at")
        factors.to_parquet(cache, index=False)
    with pytest.raises(ValueError, match=reason):
        prepare(case)
