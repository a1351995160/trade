from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import inspect

import pytest

from chanlun_trader.research_factory import (
    AIResearchFactoryOrchestratorV1,
    BLOCKED_INSUFFICIENT_FEASIBILITY,
    CandidateSampleFeasibilityInputV1,
    CandidateSampleFeasibilityPreflightV1,
    ObservationPartitionStoreV1,
    PerformanceLeakError,
    PASS,
    ResearchObjectiveV1,
    UNKNOWN,
    minimum_required_sample_count,
)
from chanlun_trader.research_factory.sample_feasibility import _PathCounts
from chanlun_trader.research.validation_policy_v2 import default_validation_decision_policy_v2


def observation(index: int, *, date: str | None = None, **overrides):
    row = {
        "signal_id": f"S{index}",
        "signal_date": date or f"D{index:03d}",
        "symbol": f"600{index:03d}.SH",
        "rank": 1,
        "signal_qualified": True,
        "pit_eligible": True,
        "data_complete": True,
        "execution_eligible": True,
        "next_session_eligible": True,
        "t1_eligible": True,
        "holding_complete": True,
        "base_affordable": True,
        "small_capital_affordable": True,
        "portfolio_feasible": True,
        "prior_holdings_known": True,
    }
    row.update(overrides)
    return row


def make_input(rows, **overrides):
    policy = default_validation_decision_policy_v2()
    values = {
        "candidate_id": "CAND_SYNTHETIC_V1",
        "candidate_hash": "HASH_SYNTHETIC_V1",
        "family": "EVENT_SIGNAL",
        "mechanism": "rare_event",
        "holding_horizon": 5,
        "observations": tuple(rows),
        "data_provenance": {"manifest_id": "SYNTHETIC_DATA_MANIFEST"},
        "pit_provenance": {"status": "PIT_VERIFIED"},
        "calendar_identity": "SYNTHETIC_CALENDAR_V1",
    }
    values.update(overrides)
    return policy, CandidateSampleFeasibilityInputV1(**values)


def test_policy_sample_minimum_is_loaded_from_frozen_policy():
    assert minimum_required_sample_count(default_validation_decision_policy_v2()) == 30


def test_outcome_fields_are_rejected_before_counting():
    policy = default_validation_decision_policy_v2()
    with pytest.raises(PerformanceLeakError):
        CandidateSampleFeasibilityInputV1(
            candidate_id="C",
            candidate_hash="H",
            family="F",
            mechanism="M",
            observations=(observation(1, realized_pnl=1.0),),
        )
    with pytest.raises(PerformanceLeakError):
        CandidateSampleFeasibilityInputV1.from_candidate(
            {"candidate_id": "C", "candidate_hash": "H", "mechanism": "M", "forward_return": 0.2},
            policy,
        )


def test_preflight_does_not_read_performance_files(monkeypatch):
    policy, candidate = make_input([observation(index) for index in range(50)])

    def forbidden_read(*args, **kwargs):
        raise AssertionError("preflight attempted a filesystem read")

    monkeypatch.setattr(Path, "read_text", forbidden_read)
    result = CandidateSampleFeasibilityPreflightV1(policy).run(candidate)
    assert result.status == "PASS"
    assert result.to_dict()["performance_files_read"] == []


def test_upper_bound_hard_block_and_lower_bound_pass():
    policy, blocked = make_input([observation(index) for index in range(10)])
    blocked_result = CandidateSampleFeasibilityPreflightV1(policy).run(blocked)
    assert blocked_result.status == BLOCKED_INSUFFICIENT_FEASIBILITY
    assert blocked_result.lower_bound_count == 10
    assert blocked_result.upper_bound_count == 10

    policy, passed = make_input([observation(index) for index in range(50)])
    passed_result = CandidateSampleFeasibilityPreflightV1(policy).run(passed)
    assert passed_result.status == "PASS"
    assert passed_result.lower_bound_count == 50


def test_ambiguous_bounds_are_unknown_and_keep_upper_bound():
    rows = [observation(index) for index in range(20)] + [observation(index, pit_eligible=None) for index in range(20, 50)]
    policy, candidate = make_input(rows)
    result = CandidateSampleFeasibilityPreflightV1(policy).run(candidate)
    assert result.status == UNKNOWN
    assert result.lower_bound_count == 20
    assert result.upper_bound_count == 50
    assert "PIT_STATE_UNKNOWN" in result.reason_codes


@pytest.mark.parametrize(
    ("lower", "upper", "unknown", "expected"),
    [
        (0, 20, False, BLOCKED_INSUFFICIENT_FEASIBILITY),
        (20, 40, True, UNKNOWN),
        (30, 30, False, PASS),
        (30, 40, True, UNKNOWN),
        (292, 312, True, UNKNOWN),
    ],
    ids=("upper_below_minimum", "unknown_interval_below_lower", "exact_threshold", "unknown_interval_at_threshold", "current_case"),
)
def test_frozen_status_matrix_uses_unknown_fail_closed_semantics(lower, upper, unknown, expected):
    policy = default_validation_decision_policy_v2()
    preflight = CandidateSampleFeasibilityPreflightV1(policy)
    counts = _PathCounts(0, 0, 0, 0, 0, lower, upper, (), unknown)

    assert preflight._status(counts) == expected


def test_unknown_bounds_preserve_lower_bound_but_do_not_pass_at_threshold():
    rows = [observation(index) for index in range(292)] + [
        observation(index, pit_eligible=None) for index in range(292, 312)
    ]
    policy, candidate = make_input(rows)

    result = CandidateSampleFeasibilityPreflightV1(policy).run(candidate)

    assert (result.lower_bound_count, result.upper_bound_count) == (292, 312)
    assert result.minimum_required_count == 30
    assert result.status == UNKNOWN
    assert "PIT_STATE_UNKNOWN" in result.reason_codes


def test_end_of_window_truncation_stays_outside_proven_subset():
    rows = [observation(index) for index in range(30)] + [
        observation(index, holding_complete=False) for index in range(30, 40)
    ]
    policy, candidate = make_input(rows)

    result = CandidateSampleFeasibilityPreflightV1(policy).run(candidate)

    assert (result.lower_bound_count, result.upper_bound_count) == (30, 30)
    assert result.status == PASS
    assert "END_OF_WINDOW_TRUNCATION" in result.reason_codes


def test_no_end_of_window_truncation_has_no_truncation_reason():
    policy, candidate = make_input([observation(index) for index in range(30)])

    result = CandidateSampleFeasibilityPreflightV1(policy).run(candidate)

    assert (result.lower_bound_count, result.upper_bound_count) == (30, 30)
    assert result.status == PASS
    assert "END_OF_WINDOW_TRUNCATION" not in result.reason_codes


def test_insufficient_executable_reason_is_about_unknown_execution_not_count():
    rows = [observation(index) for index in range(30)] + [
        observation(30, next_session_eligible=None)
    ]
    policy, candidate = make_input(rows)

    result = CandidateSampleFeasibilityPreflightV1(policy).run(candidate)

    assert result.portfolio_feasible_opportunity_count == 30
    assert result.minimum_required_count == 30
    assert result.status == UNKNOWN
    assert "INSUFFICIENT_EXECUTABLE_OPPORTUNITIES" in result.reason_codes


def test_unknown_tail_preserves_proven_lower_bound_in_streaming_path(tmp_path):
    rows = [observation(index) for index in range(40)] + [observation(40, pit_eligible=None)]
    partition_path = tmp_path / "partition.jsonl"
    partition_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({
            "manifest_id": "SYNTHETIC_STREAMING_BOUND",
            "observation_count": len(rows),
            "observation_hash": "HASH",
            "partitions": [{"index": 0, "file": partition_path.name, "row_count": len(rows)}],
        }),
        encoding="utf-8",
    )
    store = ObservationPartitionStoreV1.open(manifest_path)
    policy, candidate = make_input(())
    candidate = replace(candidate, observations=(), observation_store=store)

    result = CandidateSampleFeasibilityPreflightV1(policy).run(candidate)

    assert result.status == UNKNOWN
    assert result.lower_bound_count == 40
    assert result.upper_bound_count == 41
    assert "PIT_STATE_UNKNOWN" in result.reason_codes


def test_top_n_prevents_raw_signal_overcount():
    rows = [observation(index, date=f"D{index // 10:02d}", rank=index % 10 + 1) for index in range(100)]
    policy, candidate = make_input(
        rows,
        top_n=3,
        max_positions=3,
        calendar_sessions=tuple(f"D{day:02d}" for day in range(10)),
    )
    result = CandidateSampleFeasibilityPreflightV1(policy).run(candidate)
    assert result.raw_event_or_signal_count == 100
    assert result.selected_opportunity_count == 30
    assert result.portfolio_feasible_opportunity_count == 6
    assert result.status == BLOCKED_INSUFFICIENT_FEASIBILITY


def test_multi_date_capacity_counts_opportunities_across_time():
    rows = [
        observation(index, date=f"D{day:03d}", rank=symbol_rank)
        for day in range(100)
        for symbol_rank in range(1, 11)
        for index in [day * 10 + symbol_rank]
    ]
    policy, candidate = make_input(
        rows,
        top_n=3,
        max_positions=3,
        calendar_sessions=tuple(f"D{day:03d}" for day in range(100)),
    )
    result = CandidateSampleFeasibilityPreflightV1(policy).run(candidate)
    assert result.selected_opportunity_count == 300
    assert result.portfolio_feasible_opportunity_count == 60
    assert result.lower_bound_count == result.upper_bound_count == 60
    assert result.status == "PASS"


def test_sparse_dates_match_hand_calculated_capacity_schedule():
    dates = [f"D{day:03d}" for day in range(0, 100, 10)]
    rows = [
        observation(index, date=date, rank=symbol_rank)
        for date_index, date in enumerate(dates)
        for symbol_rank in range(1, 11)
        for index in [date_index * 10 + symbol_rank]
    ]
    policy, candidate = make_input(
        rows,
        top_n=3,
        max_positions=3,
        calendar_sessions=tuple(f"D{day:03d}" for day in range(100)),
    )
    result = CandidateSampleFeasibilityPreflightV1(policy).run(candidate)
    assert result.selected_opportunity_count == 30
    assert result.portfolio_feasible_opportunity_count == 30
    assert result.status == "PASS"


def test_clustered_signals_limit_concurrent_entries_but_reuse_slots():
    rows = [
        observation(index, date=f"D{day:03d}", rank=symbol_rank)
        for day in range(10)
        for symbol_rank in range(1, 11)
        for index in [day * 10 + symbol_rank]
    ]
    policy, candidate = make_input(
        rows,
        top_n=3,
        max_positions=3,
        calendar_sessions=tuple(f"D{day:03d}" for day in range(10)),
    )
    result = CandidateSampleFeasibilityPreflightV1(policy).run(candidate)
    assert result.selected_opportunity_count == 30
    assert result.portfolio_feasible_opportunity_count == 6
    assert result.status == BLOCKED_INSUFFICIENT_FEASIBILITY


def test_non_overlapping_dates_do_not_hit_lifetime_position_cap():
    dates = [f"D{day:03d}" for day in range(0, 120, 6)]
    rows = [
        observation(index, date=date, rank=symbol_rank)
        for date_index, date in enumerate(dates)
        for symbol_rank in range(1, 4)
        for index in [date_index * 3 + symbol_rank]
    ]
    policy, candidate = make_input(
        rows,
        top_n=3,
        max_positions=3,
        calendar_sessions=tuple(f"D{day:03d}" for day in range(120)),
    )
    result = CandidateSampleFeasibilityPreflightV1(policy).run(candidate)
    assert result.selected_opportunity_count == 60
    assert result.portfolio_feasible_opportunity_count == 60
    assert result.status == "PASS"


def test_cross_sectional_ranking_resets_each_date_and_ties_break_by_symbol():
    rows = [
        observation(index, date="D001", rank=1, symbol=f"60000{symbol}.SH")
        for symbol, index in [(3, 1), (1, 2), (2, 3), (4, 4)]
    ] + [
        observation(index, date="D002", rank=1, symbol=f"60000{symbol}.SH")
        for symbol, index in [(4, 5), (2, 6), (3, 7), (1, 8)]
    ]
    policy, candidate = make_input(rows, top_n=3, max_positions=None)
    result = CandidateSampleFeasibilityPreflightV1(policy).run(candidate)
    selected = CandidateSampleFeasibilityPreflightV1(policy)._select(
        rows, candidate
    )[0]
    assert result.selected_opportunity_count == 6
    assert [row["symbol"] for row in selected[:3]] == ["600001.SH", "600002.SH", "600003.SH"]
    assert [row["symbol"] for row in selected[3:]] == ["600001.SH", "600002.SH", "600003.SH"]


def test_holding_window_truncation_is_counted_structurally():
    rows = [observation(index, holding_complete=index < 5) for index in range(40)]
    policy, candidate = make_input(rows)
    result = CandidateSampleFeasibilityPreflightV1(policy).run(candidate)
    assert result.upper_bound_count == 5
    assert result.status == BLOCKED_INSUFFICIENT_FEASIBILITY
    assert "END_OF_WINDOW_TRUNCATION" in result.reason_codes


def test_pit_unknown_and_t_plus_one_fail_closed():
    policy, pit_unknown = make_input([observation(index, pit_eligible=None) for index in range(40)])
    result = CandidateSampleFeasibilityPreflightV1(policy).run(pit_unknown)
    assert result.status == UNKNOWN

    rows = [observation(index, t1_eligible=index < 20) for index in range(40)]
    policy, t1_blocked = make_input(rows)
    result = CandidateSampleFeasibilityPreflightV1(policy).run(t1_blocked)
    assert result.status == BLOCKED_INSUFFICIENT_FEASIBILITY
    assert result.execution_eligible_count == 20


def test_same_input_is_deterministic_and_backend_independent():
    rows = [observation(index) for index in range(40)]
    policy, first = make_input(rows, backend_type="TEMPLATE")
    _, second = make_input(rows, backend_type="CODEX")
    preflight = CandidateSampleFeasibilityPreflightV1(policy)
    one = preflight.run(first)
    two = preflight.run(first)
    other = preflight.run(second)
    assert one.to_dict() == two.to_dict()
    assert (one.status, one.lower_bound_count, one.upper_bound_count) == (other.status, other.lower_bound_count, other.upper_bound_count)


def test_block_never_reserves_predictive_budget_or_calls_validator(tmp_path):
    policy = default_validation_decision_policy_v2()

    def provider(candidate, _policy):
        return CandidateSampleFeasibilityInputV1(
            candidate_id=str(candidate["candidate_id"]),
            candidate_hash=str(candidate["candidate_hash"]),
            family=str(candidate["family_id"]),
            mechanism=str(candidate["mechanism"]),
            holding_horizon=5,
            observations=tuple(observation(index) for index in range(10)),
            data_provenance={"manifest_id": "BLOCK_FIXTURE"},
            pit_provenance={"status": "PIT_VERIFIED"},
        )

    objective = ResearchObjectiveV1.default(
        objective_id="OBJ_SAMPLE_BLOCK",
        created_at="2026-08-24T00:00:00+08:00",
        max_batches=1,
        max_total_trials=2,
    )
    runtime = __import__("chanlun_trader.research_factory", fromlist=["SyntheticFactoryRuntimeV1"]).SyntheticFactoryRuntimeV1()
    result = AIResearchFactoryOrchestratorV1(
        objective,
        output_dir=tmp_path,
        runtime=runtime,
        sample_feasibility_provider=provider,
    ).run_synthetic()
    assert result.state == "BLOCKED"
    assert result.status.trial_budget_used == 0
    assert result.status.candidates_sample_feasibility_blocked == 2
    assert runtime.validator_calls == 0
    assert not result.trial_records


def test_real_factory_source_orders_preflight_before_predictive_reservation():
    from chanlun_trader.research_factory.real_runtime import RealFactoryRuntimeV1

    source = inspect.getsource(RealFactoryRuntimeV1.run)
    assert source.index("sample-feasibility preflight") < source.index("reserve_trial")
    assert source.index("sample_feasible_records") < source.index("reservations: dict[str, str]")
    assert source.index("sample_feasibility_preflight.json") < source.index("PerformanceAccessGate(gate_path)")


def test_failure_feedback_is_sanitized():
    policy, candidate = make_input([observation(index) for index in range(10)])
    result = CandidateSampleFeasibilityPreflightV1(policy).run(candidate)
    feedback = result.sanitized_failure_feedback()
    assert feedback is not None
    serialized = str(feedback).lower()
    assert feedback["category"] == "SAMPLE_FEASIBILITY_FAILURE"
    assert "sample_feasibility" in serialized
    assert "10" not in serialized
    assert "return" not in serialized
