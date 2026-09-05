from __future__ import annotations

import json

import pandas as pd
import pytest

from chanlun_trader.research.validation_policy_v2 import default_validation_decision_policy_v2
from chanlun_trader.research_factory import (
    AIResearchFactoryOrchestratorV1,
    CandidateSampleFeasibilityPreflightV1,
    RealSampleFeasibilityProviderV1,
    ResearchObjectiveV1,
)
from chanlun_trader.research_factory.real_sample_feasibility import BenchmarkCoverageError, _RawPitSource


def _fixture_provider(*, pit_rows=None, available_at="2022-08-01T15:00:00+08:00", event_id="E_TEST", root="."):
    days = [20220801 + index for index in range(36)]
    symbol = "600000.SH"
    daily = pd.DataFrame([
        {"symbol": symbol, "date": day, "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "volume": 1000, "amount": 10000.0, "prev_close": 9.9}
        for day in days
    ])
    factors = {"F_TEST": pd.DataFrame([
        {"symbol": symbol, "date": day, "value": 1.0, "available_at": day}
        for day in days
    ])}
    events = {event_id: [
        {
            "symbol": symbol,
            "event_id": event_id,
            "event_version": "v1",
            "event_time": day,
            "available_at_ts": (
                f"{str(day + 1)[:4]}-{str(day + 1)[4:6]}-{str(day + 1)[6:]}T09:30:00+08:00"
                if available_at == "NEXT" else available_at
            ),
        }
        for day in days[:30]
    ]}
    if pit_rows is None:
        pit_rows = {
            day: {symbol: {"pit_eligible": True, "universe_eligible": True, "active_security": True, "st_allowed": True, "suspension_clear": True, "tradability_status": "TRADING"}}
            for day in days
        }
    return RealSampleFeasibilityProviderV1(
        calendar_sessions=days,
        root=root,
        daily_frame=daily,
        factor_frames=factors,
        event_rows=events,
        pit_rows=pit_rows,
    ), days


def _candidate(start=20220801, end=20220905, event_id="E_TEST"):
    return {
        "candidate_id": "C_REAL_SAMPLE_V1",
        "candidate_hash": "FROZEN_HASH_V1",
        "strategy_family": "DAILY_EVENT",
        "mechanism": "event_plus_factor",
        "factor_ids": ["F_TEST"],
        "signal_logic": {"event_dependencies": [event_id]},
        "holding_period": 1,
        "max_positions": 3,
        "selection_rule": {"top_n": 1},
        "sample_feasibility": {"research_start": start, "research_end": end, "top_n": 1},
    }


def test_real_provider_materializes_legal_event_factor_rows_and_preserves_preflight():
    provider, _ = _fixture_provider()
    policy = default_validation_decision_policy_v2()
    sample = provider(_candidate(), policy)
    result = CandidateSampleFeasibilityPreflightV1(policy).run(sample)

    assert sample.backend_type == "REAL_FACTORY"
    assert len(sample.observations) == 30
    assert all(row["event_id"] == "E_TEST" for row in sample.observations)
    assert all(row["event_available_at"] <= "2022-08-01T15:00:00+08:00" for row in sample.observations)
    assert result.status == "PASS"
    assert result.lower_bound_count >= 30
    assert sample.data_provenance["source_read_audit"]["performance_files_read"] == []


def test_future_event_available_at_never_qualifies():
    provider, _ = _fixture_provider(available_at="NEXT")
    policy = default_validation_decision_policy_v2()
    sample = provider(_candidate(), policy)
    result = CandidateSampleFeasibilityPreflightV1(policy).run(sample)

    assert all(row["signal_qualified"] is False for row in sample.observations)
    assert result.status == "BLOCKED_INSUFFICIENT_FEASIBILITY"
    assert result.lower_bound_count == 0


def _benchmark_fixture_frame() -> tuple[pd.DataFrame, list[int]]:
    days = [int(value.strftime("%Y%m%d")) for value in pd.bdate_range("2022-08-01", "2025-07-31")]
    frame = pd.DataFrame({
        "date": days,
        "close": [100.0 + index for index, _ in enumerate(days)],
        "volume": [1000.0 + index for index, _ in enumerate(days)],
    })
    return frame, days


def _range_fixture_provider(tmp_path, monkeypatch):
    tmp_path.mkdir(parents=True, exist_ok=True)
    frame, days = _benchmark_fixture_frame()
    path = tmp_path / "benchmark_index_daily.parquet"
    path.write_bytes(b"deterministic structural benchmark fixture")
    calls = []

    provider = RealSampleFeasibilityProviderV1(calendar_sessions=days)
    provider._calendar = tuple(days)
    provider._benchmark_path = lambda: path

    def read_parquet(_path, *, columns, date_column, start_date, end_date, **_kwargs):
        calls.append((int(start_date), int(end_date)))
        return frame[(frame["date"] >= int(start_date)) & (frame["date"] <= int(end_date))].copy()

    monkeypatch.setattr(provider.reader, "read_parquet", read_parquet)
    return provider, frame, days, calls


def test_benchmark_cache_expands_after_first_partition_and_keeps_later_partitions_nonempty(tmp_path, monkeypatch):
    provider, frame, days, calls = _range_fixture_provider(tmp_path, monkeypatch)
    first_end = 20221128
    first_index = days.index(first_end)
    ranges = [
        (days[0], days[first_index]),
        (days[first_index + 1], days[first_index + 2]),
        (days[first_index + 3], days[first_index + 4]),
    ]

    loaded_rows = [len(provider._load_benchmark(end=end, start=start)) for start, end in ranges]

    assert all(count > 0 for count in loaded_rows)
    assert calls[0][1] == first_end
    assert calls[1][1] == ranges[1][1]
    assert provider.benchmark_coverage["covered_end"] == ranges[-1][1]
    assert provider.benchmark_coverage["coverage_status"] == "COMPLETE"
    assert len(provider._benchmark) == len(frame[frame["date"] <= ranges[-1][1]])
    assert [item["action"] for item in provider._benchmark_cache_requests] == [
        "INITIAL_LOAD", "RELOAD_EXPANDED_RANGE", "RELOAD_EXPANDED_RANGE"
    ]


def test_benchmark_cache_range_extension_and_disjoint_range_are_not_cache_hits(tmp_path, monkeypatch):
    provider, _frame, _days, calls = _range_fixture_provider(tmp_path, monkeypatch)

    provider._load_benchmark(end=20221128, start=20220801)
    provider._load_benchmark(end=20250731, start=20220801)

    assert calls[0][1] == 20221128
    assert calls[1][1] == 20250731
    assert len(calls) == 2
    assert provider.benchmark_coverage["covered_end"] == 20250731

    disjoint_provider, _frame, _days, disjoint_calls = _range_fixture_provider(tmp_path / "disjoint", monkeypatch)
    disjoint_provider._load_benchmark(end=20221128, start=20220801)
    disjoint_provider._load_benchmark(end=20250731, start=20240101)

    assert len(disjoint_calls) == 2
    assert disjoint_provider._benchmark_cache_requests[1]["action"] == "RELOAD_EXPANDED_RANGE"
    assert disjoint_provider.benchmark_coverage["requested_start"] == 20240101
    assert disjoint_provider.benchmark_coverage["covered_end"] == 20250731


def test_benchmark_cache_order_and_restart_parity(tmp_path, monkeypatch):
    frame, days = _benchmark_fixture_frame()

    def make_provider():
        path = tmp_path / "benchmark_index_daily.parquet"
        if not path.exists():
            path.write_bytes(b"deterministic structural benchmark fixture")
        provider = RealSampleFeasibilityProviderV1(calendar_sessions=days)
        provider._calendar = tuple(days)
        provider._benchmark_path = lambda: path

        def read_parquet(_path, *, columns, date_column, start_date, end_date, **_kwargs):
            return frame[(frame["date"] >= int(start_date)) & (frame["date"] <= int(end_date))].copy()

        monkeypatch.setattr(provider.reader, "read_parquet", read_parquet)
        return provider

    first_end = 20221128
    first_index = days.index(first_end)
    ranges = [
        (days[0], days[first_index]),
        (days[first_index + 1], days[first_index + 40]),
        (days[first_index + 41], days[first_index + 80]),
    ]

    ordered = make_provider()
    for start, end in ranges:
        ordered._load_benchmark(end=end, start=start)
    reverse = make_provider()
    for start, end in reversed(ranges):
        reverse._load_benchmark(end=end, start=start)
    direct = make_provider()
    direct._load_benchmark(end=ranges[-1][1], start=ranges[0][0])
    restarted = make_provider()
    restarted._load_benchmark(end=ranges[-1][1], start=ranges[0][0])

    columns = ["date", "close", "volume"]
    pd.testing.assert_frame_equal(ordered._benchmark[columns], reverse._benchmark[columns], check_dtype=True)
    pd.testing.assert_frame_equal(ordered._benchmark[columns], direct._benchmark[columns], check_dtype=True)
    pd.testing.assert_frame_equal(direct._benchmark[columns], restarted._benchmark[columns], check_dtype=True)
    assert ordered.benchmark_coverage["coverage_status"] == "COMPLETE"
    assert reverse.benchmark_coverage["coverage_status"] == "COMPLETE"
    assert restarted.benchmark_coverage["coverage_status"] == "COMPLETE"


def test_partial_benchmark_fixture_fails_closed_instead_of_being_returned_as_complete():
    frame, days = _benchmark_fixture_frame()
    partial = frame[frame["date"] <= 20221128].copy()
    provider = RealSampleFeasibilityProviderV1(
        calendar_sessions=days,
        benchmark_frame=partial,
    )
    provider._calendar = tuple(days)

    with pytest.raises(BenchmarkCoverageError, match="coverage"):
        provider._load_benchmark(end=20250731, start=20220801)
    assert provider.benchmark_coverage["coverage_status"] == "PARTIAL"
    assert provider.benchmark_coverage["coverage_complete"] is False


def test_e_limitup_sent_close_confirmed_timestamp_is_legal_but_future_timestamp_is_not():
    provider, _ = _fixture_provider(event_id="E_LIMITUP_SENT", available_at="2022-08-01T15:00:01+08:00")
    rows = provider._load_events_partition("E_LIMITUP_SENT", 20220801, 20220801)

    assert rows[0]["event_trade_date"] == 20220801
    assert rows[0]["event_available_at"] == "2022-08-01T15:00:01+08:00"
    assert rows[0]["event_available_at"] > "2022-08-01T15:00:00+08:00"
    assert provider._load_events_partition("E_LIMITUP_SENT", 20220801, 20220801)


def test_event_partition_boundary_reads_next_available_date_bucket_without_future_rows():
    provider = RealSampleFeasibilityProviderV1(root=".", streaming=True, partition_session_count=80)
    provider._load_calendar(20220801, 20250731)

    rows = provider._load_events_partition("E_LIMITUP_SENT", 20241121, 20241121)

    assert len(rows) == 12
    assert {row["event_trade_date"] for row in rows} == {20241121}
    assert {row["event_available_at"] for row in rows} == {"2024-11-21T15:00:01+08:00"}
    assert all(row["event_available_at_date"] == 20241122 for row in rows)


def test_inline_frozen_daily_event_and_ranking_factor_materialize_without_global_registry():
    days = [int(value.strftime("%Y%m%d")) for value in pd.bdate_range("2022-08-01", periods=60)]
    symbols = ["600000.SH", "000001.SZ"]
    daily = pd.DataFrame([
        {
            "symbol": symbol,
            "date": day,
            "open": 9.7,
            "high": 10.1,
            "low": 9.4,
            "close": 9.95,
            "volume": 1000,
            "amount": 10000.0,
            "prev_close": 10.0,
        }
        for day in days
        for symbol in symbols
    ])
    pit_rows = {
        day: {
            symbol: {
                "pit_eligible": True,
                "universe_eligible": True,
                "active_security": True,
                "st_allowed": True,
                "suspension_clear": True,
                "tradability_status": "TRADING",
            }
            for symbol in symbols
        }
        for day in days
    }
    candidate = {
        "candidate_id": "C_INLINE_EVENT_V1",
        "candidate_hash": "HASH_INLINE_EVENT_V1",
        "strategy_family": "DAILY_EVENT",
        "mechanism": "event reversal",
        "factor_bindings": [{
            "factor_id": "SELL_OFF_RECLAIM_STRENGTH",
            "formula": "(close-low)/max(high-low,epsilon)",
            "available_at": "T_CLOSE",
        }],
        "signal_predicate": {
            "factor_conditions": [],
            "event_conditions": [{
                "event_id": "DAILY_SELL_OFF_CLOSE_RECLAIM",
                "available_at_semantics": "T_CLOSE",
                "definition": {
                    "low_to_previous_close_operator": "LE",
                    "low_to_previous_close_value": 0.95,
                    "close_to_previous_close_operator": "GE",
                    "close_to_previous_close_value": 0.99,
                    "close_above_open": True,
                    "positive_range_required": True,
                },
            }],
        },
        "holding_period": 5,
        "max_positions": 3,
        "selection_rule": {"type": "TOP_N", "top_n": 3},
        "ranking_rule": {
            "factor_id": "SELL_OFF_RECLAIM_STRENGTH",
            "direction": "DESCENDING",
            "tie_breakers": ["amount_descending", "symbol_ascending"],
        },
        "sample_feasibility": {
            "research_start": days[0],
            "research_end": days[-1],
            "top_n": 3,
        },
    }
    provider = RealSampleFeasibilityProviderV1(
        daily_frame=daily,
        calendar_sessions=days,
        pit_rows=pit_rows,
    )

    sample = provider(candidate, default_validation_decision_policy_v2())
    result = CandidateSampleFeasibilityPreflightV1(default_validation_decision_policy_v2()).run(sample)

    assert len(sample.observations) == 120
    assert all(row["signal_qualified"] is True for row in sample.observations)
    assert all(row["factor_values"]["SELL_OFF_RECLAIM_STRENGTH"] > 0 for row in sample.observations)
    assert sample.data_provenance["event_source"]["mode"] == "INLINE_FROZEN_DEFINITION"
    assert result.raw_event_or_signal_count == 120
    assert result.lower_bound_count >= 30


def test_rank_only_composite_percentile_candidate_qualifies_then_ranks_deterministically():
    days = [int(value.strftime("%Y%m%d")) for value in pd.bdate_range("2022-08-01", periods=45)]
    symbols = ["600001.SH", "600002.SH", "600003.SH"]
    daily = pd.DataFrame([
        {
            "symbol": symbol,
            "date": day,
            "open": 10.0,
            "high": 10.2,
            "low": 9.8,
            "close": 10.0,
            "volume": 1000.0,
            "amount": 10000.0,
            "prev_close": 10.0,
        }
        for day in days
        for symbol in symbols
    ])
    accel = {"600001.SH": 3.0, "600002.SH": 2.0, "600003.SH": 1.0}
    illiq = {"600001.SH": 3.0, "600002.SH": 1.0, "600003.SH": 2.0}
    factors = {
        "AMOUNT_ACCEL": pd.DataFrame([
            {"symbol": symbol, "date": day, "value": accel[symbol], "available_at": day}
            for day in days
            for symbol in symbols
        ]),
        "ILLIQUIDITY_AMIHUD": pd.DataFrame([
            {"symbol": symbol, "date": day, "value": illiq[symbol], "available_at": day}
            for day in days
            for symbol in symbols
        ]),
    }
    pit_rows = {
        day: {
            symbol: {
                "pit_eligible": True,
                "universe_eligible": True,
                "active_security": True,
                "st_allowed": True,
                "suspension_clear": True,
                "tradability_status": "TRADING",
            }
            for symbol in symbols
        }
        for day in days
    }
    candidate = {
        "candidate_id": "C_RANK_ONLY_COMPOSITE_V1",
        "candidate_hash": "HASH_RANK_ONLY_COMPOSITE_V1",
        "strategy_family": "DAILY_CROSS_SECTIONAL",
        "mechanism": "liquidity_amount_participation",
        "factor_bindings": [
            {"factor_id": "AMOUNT_ACCEL", "role": "PRIMARY_ALPHA", "direction": "POSITIVE"},
            {"factor_id": "ILLIQUIDITY_AMIHUD", "role": "CONFIRMATION", "direction": "NEGATIVE"},
        ],
        "signal_predicate": {
            "factor_conditions": [
                {
                    "factor_id": "AMOUNT_ACCEL",
                    "role": "PRIMARY_ALPHA",
                    "direction": "POSITIVE",
                    "transform": "CROSS_SECTIONAL_PERCENTILE",
                },
                {
                    "factor_id": "ILLIQUIDITY_AMIHUD",
                    "role": "CONFIRMATION",
                    "direction": "NEGATIVE",
                    "transform": "CROSS_SECTIONAL_PERCENTILE",
                },
            ],
            "event_conditions": [],
        },
        "signal_logic": {
            "type": "CROSS_SECTIONAL_COMPOSITE_RANK",
            "combination": "EQUAL_WEIGHTED_PERCENTILE",
        },
        "ranking_rule": {
            "type": "COMPOSITE_PERCENTILE",
            "weighting": "EQUAL",
            "components": [
                {"factor_id": "AMOUNT_ACCEL", "direction": "DESC"},
                {"factor_id": "ILLIQUIDITY_AMIHUD", "direction": "ASC"},
            ],
        },
        "selection_rule": {"type": "TOP_N", "top_n": 1, "tie_break": "STABLE_SYMBOL_ASC"},
        "holding_period": 1,
        "max_positions": 1,
        "sample_feasibility": {
            "research_start": days[0],
            "research_end": days[-1],
            "top_n": 1,
        },
    }
    provider = RealSampleFeasibilityProviderV1(
        daily_frame=daily,
        calendar_sessions=days,
        factor_frames=factors,
        pit_rows=pit_rows,
    )

    sample = provider(candidate, default_validation_decision_policy_v2())
    result = CandidateSampleFeasibilityPreflightV1(default_validation_decision_policy_v2()).run(sample)

    assert all(row["signal_qualified"] is True for row in sample.observations)
    first_day = [row for row in sample.observations if row["signal_date"] == days[0]]
    rank_by_symbol = {row["symbol"]: row["rank"] for row in first_day}
    assert rank_by_symbol == {"600001.SH": 2, "600002.SH": 1, "600003.SH": 3}
    assert result.selected_opportunity_count >= 30
    assert result.lower_bound_count >= 30


def test_raw_fixture_date_path_and_symbol_normalization_are_deterministic(tmp_path):
    raw = tmp_path / "data/research/security_state/raw"
    raw.mkdir(parents=True)
    (raw / "stock_basic.json").write_text(json.dumps({"rows": [{
        "code": "sh.600000", "type": "1", "ipoDate": "2020-01-01", "outDate": ""
    }]}), encoding="utf-8")
    (raw / "all_stock").mkdir()
    (raw / "all_stock/trade_date=2024-01-02.json").write_text(json.dumps({"rows": [{
        "code": "600000", "tradeStatus": "1"
    }]}), encoding="utf-8")
    (raw / "history").mkdir()
    (raw / "history/symbol=600000_SH.json").write_text(json.dumps({"rows": [{
        "code": "sh.600000", "date": "2024-01-02", "tradestatus": "1", "isST": "0"
    }]}), encoding="utf-8")

    source = _RawPitSource(tmp_path, (20240102,), [])
    state = source.state("600000", 20240102)

    assert state["pit_eligible"] is True
    assert state["st_allowed"] is True
    assert state["suspension_clear"] is True


def test_canonical_normalized_pit_source_is_preferred_over_raw(tmp_path):
    normalized = tmp_path / "data/research/security_state/normalized"
    (normalized / "pit_universe_v2").mkdir(parents=True)
    (normalized / "manifest.json").write_text(json.dumps({
        "dataset_version": "PIT_UNIVERSE_DATASET_V2",
        "coverage_start": "2022-08-01",
        "coverage_end": "2022-08-01",
    }), encoding="utf-8")
    (normalized / "pit_universe_v2/trade_date=2022-08-01.jsonl").write_text(json.dumps({
        "symbol": "600000.SH", "exists": True, "listed": True, "delisted": False,
        "universe_member": True, "st_status": "NORMAL", "tradability_status": "TRADING",
        "eligibility_status": "ELIGIBLE", "available_at": "2022-08-02T09:30:00+08:00",
        "reason_codes": [],
    }) + "\n", encoding="utf-8")

    audit = []
    source = _RawPitSource(tmp_path, (20220801,), audit)
    state = source.state("sh.600000", 20220801)

    assert source.uses_canonical_store is True
    assert state["pit_eligible"] is True
    assert state["pit_state_source"] == "CANONICAL_NORMALIZED_PIT_UNIVERSE_V2"
    assert state["pit_available_at"] == "2022-08-02T09:30:00+08:00"


def test_lhb_same_day_contract_is_legal_only_when_explicitly_supported(tmp_path):
    registry = tmp_path / "data/research/event_registry"
    registry.mkdir(parents=True)
    (registry / "registry_v2.json").write_text(json.dumps({"events": [{
        "event_id": "E_LHB_NETPOS", "version": "v2", "PIT_safe": True,
        "available_at_semantics": "T_CLOSE_SAME_DAY", "same_day_supported": True,
        "contract_id": "TEST_LHB_SAME_DAY_V2",
    }]}), encoding="utf-8")
    provider, _ = _fixture_provider(root=tmp_path, event_id="E_LHB_NETPOS")
    policy = default_validation_decision_policy_v2()
    sample = provider(_candidate(event_id="E_LHB_NETPOS"), policy)
    result = CandidateSampleFeasibilityPreflightV1(policy).run(sample)

    assert result.status == "PASS"
    assert all(row["event_semantic_version"] == "v2" for row in sample.observations)


def test_lhb_future_contract_never_qualifies_same_day(tmp_path):
    registry = tmp_path / "data/research/event_registry"
    registry.mkdir(parents=True)
    (registry / "registry_v2.json").write_text(json.dumps({"events": [{
        "event_id": "E_LHB_NETPOS", "version": "v2", "PIT_safe": True,
        "available_at_semantics": "NEXT_SESSION_OR_LATER", "same_day_supported": False,
        "contract_id": "TEST_LHB_NEXT_SESSION_V2",
    }]}), encoding="utf-8")
    provider, _ = _fixture_provider(root=tmp_path, event_id="E_LHB_NETPOS")
    policy = default_validation_decision_policy_v2()
    sample = provider(_candidate(event_id="E_LHB_NETPOS"), policy)
    result = CandidateSampleFeasibilityPreflightV1(policy).run(sample)

    assert all(row["signal_qualified"] is False for row in sample.observations)
    assert result.status == "BLOCKED_INSUFFICIENT_FEASIBILITY"


def test_missing_pit_state_is_unknown_not_eligible():
    provider, _ = _fixture_provider(pit_rows={})
    policy = default_validation_decision_policy_v2()
    sample = provider(_candidate(), policy)
    result = CandidateSampleFeasibilityPreflightV1(policy).run(sample)

    assert all(row["pit_eligible"] is None for row in sample.observations)
    assert result.status == "UNKNOWN"
    assert "PIT_STATE_UNKNOWN" in result.reason_codes


def test_execution_state_cache_reuses_structural_limit_result(monkeypatch):
    provider = RealSampleFeasibilityProviderV1()
    provider._reset_execution_partition_cache()
    calls = {"pit": 0}

    def pit_state(symbol, day):
        calls["pit"] += 1
        return {"active_security": True, "suspension_clear": True, "st_allowed": True}

    monkeypatch.setattr(provider, "_pit_state", pit_state)
    daily = {(20220802, "600000.SH"): {
        "open": 10.0, "high": 10.5, "low": 9.5, "volume": 1000.0,
        "prev_close": 9.9,
    }}

    first = provider._execution_state("600000.SH", 20220801, 20220802, daily)
    second = provider._execution_state("600000.SH", 20220801, 20220802, daily)

    assert first == second
    assert calls["pit"] == 1
    assert len(provider._execution_cache) == 1
    assert provider._execution_master is not None
    assert provider._execution_master._states == {}


def test_provider_rejects_outcome_bearing_structural_fixture():
    policy = default_validation_decision_policy_v2()
    provider = RealSampleFeasibilityProviderV1(
        calendar_sessions=[20220801, 20220802],
        structural_observations=[{"signal_id": "S1", "realized_pnl": 1.0}],
    )
    with pytest.raises(Exception, match="performance|outcome|pnl|return"):
        provider(_candidate(end=20220802), policy)


def test_orchestrator_defaults_real_provider_only_for_real_backend(tmp_path):
    objective = ResearchObjectiveV1.default(
        objective_id="OBJ_REAL_PROVIDER_WIRING",
        created_at="2026-08-25T00:00:00+08:00",
        max_batches=1,
        max_total_trials=1,
    )
    orchestrator = AIResearchFactoryOrchestratorV1(objective, root=tmp_path, output_dir=tmp_path / "reports")
    assert isinstance(orchestrator.sample_feasibility_provider, RealSampleFeasibilityProviderV1)
    candidate = {
        "candidate_id": "C_UNKNOWN_SOURCE",
        "candidate_hash": "H_UNKNOWN_SOURCE",
        "strategy_family": "UNSUPPORTED_FAMILY",
        "mechanism": "unsupported",
        "holding_period": 5,
    }
    synthetic_input = orchestrator.sample_feasibility_input(candidate)
    real_input = orchestrator.sample_feasibility_input(candidate, backend_type="REAL_FACTORY")
    assert synthetic_input.backend_type == "UNKNOWN"
    assert real_input.backend_type == "REAL_FACTORY"
    assert real_input.data_provenance["provider"] == "RealSampleFeasibilityProviderV1"


def test_contract_defined_event_materialization_is_shared_by_structural_and_predictive_paths():
    days = [int(value.strftime("%Y%m%d")) for value in pd.bdate_range("2021-08-02", periods=30)]
    benchmark = pd.DataFrame({
        "date": days,
        "close": [100.0] * 25 + [101.0] + [100.0] * 4,
        "volume": [1000.0] * 30,
    })
    daily = pd.DataFrame([
        {
            "symbol": "600000.SH",
            "date": day,
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.0,
            "volume": 1000.0,
            "amount": 10000.0,
            "prev_close": 9.9,
        }
        for day in days
    ])
    pit_rows = {
        day: {"600000.SH": {
            "pit_eligible": True,
            "universe_eligible": True,
            "active_security": True,
            "st_allowed": True,
            "suspension_clear": True,
            "tradability_status": "TRADING",
        }}
        for day in days
    }
    identity = {
        "registry_kind": "CONTRACT_DEFINED_DERIVED_EVENT",
        "identity_version": "V1",
        "dataset_id": "benchmark_index_daily",
        "pit_safe": True,
        "formula": (
            "on signal date t, close_t is greater than the maximum close of the preceding five completed sessions "
            "and volume_t is at least the median volume of the preceding twenty completed sessions"
        ),
    }
    condition = {
        "event_id": "BENCHMARK_SENTIMENT_BREAKOUT_EVENT_V1",
        "available_at_semantics": "T_CLOSE",
        "required": True,
    }
    candidate = {
        "candidate_id": "C_CONTRACT_EVENT_PARITY_V1",
        "candidate_hash": "HASH_CONTRACT_EVENT_PARITY_V1",
        "strategy_family": "DAILY_EVENT",
        "mechanism": "contract-defined-event",
        "signal_predicate": {"factor_conditions": [], "event_conditions": [condition]},
        "factor_event_registry_identities": {condition["event_id"]: identity},
        "holding_period": 1,
        "max_positions": 1,
        "selection_rule": {"top_n": 1},
        "sample_feasibility": {"research_start": days[20], "research_end": days[-1], "top_n": 1},
    }
    provider = RealSampleFeasibilityProviderV1(
        calendar_sessions=days,
        daily_frame=daily,
        benchmark_frame=benchmark,
        pit_rows=pit_rows,
        streaming=False,
    )

    direct_values, direct_resolution = provider.materialize_event_rows(
        [condition], daily, days[0], days[-1], record=candidate
    )
    sample = provider.build(candidate, default_validation_decision_policy_v2())
    observed = {
        (int(row["signal_date"]), str(row["symbol"]), row["event_available_at"])
        for row in sample.observations
        if row["event_data_complete"] is True
    }
    direct = {
        (int(day), str(symbol), str(rows[0]["event_available_at"]))
        for (day, symbol), rows in direct_values.items()
    }

    assert direct_resolution[condition["event_id"]]["mode"] == "CONTRACT_DEFINED_BENCHMARK_DERIVATION"
    assert provider.event_source_identity()["mode"] == "CONTRACT_DEFINED_BENCHMARK_DERIVATION"
    assert direct == observed
    assert len({day for day, _symbol, _available_at in direct}) == 1
    assert next(iter(direct))[0] == days[25]
    assert next(iter(direct))[2].endswith("T15:00:01+08:00")
    materialized_row = next(iter(direct_values.values()))[0]
    assert materialized_row["event_type"] == "DERIVED_BENCHMARK_EVENT"
    assert materialized_row["source_id"] == "benchmark_index_daily:sh000300"
    assert materialized_row["schema_version"] == "event-materialization-row-v1"
    assert materialized_row["materializer_name"].endswith("._materialize_contract_defined_event")
    assert materialized_row["provider"] == "RealSampleFeasibilityProviderV1"
    assert materialized_row["event_time"] == materialized_row["event_trade_date"] == days[25]
    assert materialized_row["available_at_ts"] == materialized_row["eligible_at"] == materialized_row["event_available_at"]
    assert materialized_row["available_at"] == days[25]
    assert all(row["event_semantic_contract"] == "CONTRACT_DEFINED_DERIVED_EVENT" for row in sample.observations)
