from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from chanlun_trader.engine.asof import MarketDataStore
from chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from chanlun_trader.engine.signal import ExecutionPolicy, Side, Signal
from chanlun_trader.research.strategy_candidate import CompiledSignal, StrategyCandidateSpec
from chanlun_trader.research.strategy_semantic import (
    ExitPredicateSpec,
    SemanticCandidateRecord,
    SignalPredicateSpec,
    StrategyCandidateCompilerV2,
)
from chanlun_trader.research.strategy_validation import (
    PerformanceAccessGate,
    TrialEvent,
    TrialRegistryV1,
    ValidationGovernanceError,
    ValidationPolicyV1,
    benjamini_hochberg,
    compiled_signals_to_engine_signals,
    ensure_no_future_access,
    load_frozen_policy,
    performance_metrics,
    policy_payload,
    stable_hash,
    load_semantic_records,
)


def test_policy_hash_freeze_and_gate(tmp_path: Path):
    policy_path = tmp_path / "validation_policy.json"
    gate_path = tmp_path / "gate.json"
    policy = ValidationPolicyV1()
    policy_path.write_text(json.dumps(policy_payload(policy)), encoding="utf-8")
    loaded, policy_hash = load_frozen_policy(policy_path)
    assert loaded.hash() == policy_hash
    gate = PerformanceAccessGate(gate_path)
    with pytest.raises(ValidationGovernanceError):
        gate.assert_enabled(policy_hash)
    gate.enable(policy_path)
    gate.assert_enabled(policy_hash)
    tampered = json.loads(policy_path.read_text(encoding="utf-8"))
    tampered["policy"]["max_positions"] = 4
    policy_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValidationGovernanceError):
        load_frozen_policy(policy_path)


def test_trial_registry_is_append_only(tmp_path: Path):
    registry = TrialRegistryV1(tmp_path / "trial_registry.json")
    event = TrialEvent(
        event_type="TRIAL_REGISTERED",
        trial_id="T1",
        candidate_id="C1",
        candidate_preregistration_hash="h1",
        status="REGISTERED",
        classification=None,
        created_at="2026-08-23",
    )
    registry.append(event)
    registry.append(event)
    registry.append(TrialEvent(
        event_type="TRIAL_COMPLETED",
        trial_id="T1",
        candidate_id="C1",
        candidate_preregistration_hash="h1",
        status="BLOCKED",
        classification="BLOCKED",
        reason_codes=("PIT_BLOCK",),
        created_at="2026-08-23",
    ))
    payload = json.loads((tmp_path / "trial_registry.json").read_text(encoding="utf-8"))
    assert len(payload["events"]) == 2
    assert registry.latest()["T1"]["classification"] == "BLOCKED"


def test_bh_reports_raw_and_adjusted_support():
    result = benjamini_hochberg({"a": 0.001, "b": 0.04, "c": 0.8}, q=0.05)
    assert result["hypothesis_count"] == 3
    assert set(result["raw_p_values"]) == {"a", "b", "c"}
    assert set(result["adjusted_p_values"]) == {"a", "b", "c"}
    assert result["adjusted_p_values"]["a"] <= result["adjusted_p_values"]["b"]


def test_metric_reducer_is_deterministic_and_nan_safe():
    trades = [{"realized_pnl": 10.0}, {"realized_pnl": -5.0}]
    first = performance_metrics([100.0, 105.0, 102.0], trades, initial_cash=100.0)
    second = performance_metrics([100.0, 105.0, 102.0], trades, initial_cash=100.0)
    assert first == second
    assert first["trades"] == 2
    assert first["profit_factor"] == 2.0


def test_final_test_guard_is_fail_closed():
    with pytest.raises(Exception):
        ensure_no_future_access(Path("."), {"date": 20250801})


def test_compiled_signal_adapter_preserves_timing_and_action():
    compiled = [CompiledSignal(
        strategy_id="CAND",
        candidate_id="CAND",
        symbol="000001.SZ",
        action="BUY",
        generated_at="2024-01-02T15:00:00+08:00",
        available_at="2024-01-02T15:00:00+08:00",
        eligible_at="2024-01-03T09:30:00+08:00",
        score=1.2,
        rank=1,
        reason="QUALIFIED",
        source_factors=("RETURN_5D",),
        metadata={"source_event_ids": []},
    )]
    signals = compiled_signals_to_engine_signals(compiled)
    assert signals[0].direction == Side.BUY
    assert signals[0].execution_policy == ExecutionPolicy.NEXT_SESSION_OPEN
    assert signals[0].metadata["available_at"].startswith("2024-01-02")


def test_phase4_uses_frozen_v2_semantic_compiler():
    registry_path = Path("data/research/strategy_candidate_registry/registry_v2.json")
    _, records = load_semantic_records(registry_path)
    record = next(item for item in records if item.phase4_eligible)
    compiled = StrategyCandidateCompilerV2().compile(record)
    factor_values = {
        binding["factor_id"]: (2.0 if binding["factor_id"] == "VOLUME_RATIO_5_20" else (1.0 if binding["direction"] == "POSITIVE" else -1.0))
        for binding in record.candidate.factor_bindings
    }
    row = {
        "symbol": "000001.SZ",
        "generated_at": "2024-01-02T15:00:00+08:00",
        "available_at": "2024-01-02T15:00:00+08:00",
        "next_session_open": "2024-01-03T09:30:00+08:00",
        "universe_as_of": "2024-01-02T15:00:00+08:00",
        "universe_eligible": True,
        "tradable": True,
        "affordable": True,
        "factor_values": factor_values,
        "factor_available_at": {key: "2024-01-02T15:00:00+08:00" for key in factor_values},
    }
    outputs = compiled.emit_signals([row])
    assert outputs and outputs[0].action == "BUY"
    assert outputs[0].candidate_id == record.candidate.candidate_id


def test_backtest_engine_v2_contract_executes_research_signals(tmp_path: Path):
    dates = [20240102, 20240103, 20240104]
    frame = pd.DataFrame({
        "open": [10.0, 10.5, 11.0],
        "high": [10.2, 10.7, 11.2],
        "low": [9.8, 10.3, 10.8],
        "close": [10.1, 10.6, 11.1],
        "volume": [100000, 100000, 100000],
        "amount": [1000000, 1050000, 1100000],
        "prev_close": [9.9, 10.1, 10.6],
    }, index=dates)
    store = MarketDataStore()
    store.add_daily_raw("000001.SZ", frame)
    config = EngineConfig(
        initial_cash=100000.0,
        max_positions=1,
        max_position_weight=1.0,
        commission_rate=0.0,
        min_commission=0.0,
        stamp_tax_rate=0.0,
        slippage_bps=0.0,
        enable_index_filter=False,
        index_filter_enabled=False,
        max_holding_days=20,
        persist_run_manifest=False,
        run_manifest_root=str(tmp_path / "runs"),
    )
    engine = BacktestEngineV2(store, dates, config=config)
    engine.add_signals([
        Signal("CAND", "BUY-1", "000001.SZ", pd.Timestamp("2024-01-02 15:00", tz="Asia/Shanghai"), Side.BUY, execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN),
        Signal("CAND", "SELL-1", "000001.SZ", pd.Timestamp("2024-01-03 15:00", tz="Asia/Shanghai"), Side.SELL, execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN),
    ])
    result = engine.run()
    assert result.context.mode == "DAILY"
    assert result.summary()["n_events"] >= 1
