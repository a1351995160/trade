from copy import deepcopy

import pytest

from chanlun_trader.research_factory.sample_feasibility_v2 import (
    BLOCKED_INSUFFICIENT_FEASIBILITY,
    CandidateSampleFeasibilityPolicyV2,
    LowerBoundIntegrityAuditV2,
    PASS,
    UNKNOWN,
    audit_lower_bound_integrity_v2,
)


def _integrity(status=PASS):
    return LowerBoundIntegrityAuditV2(
        candidate_id="CANDIDATE",
        candidate_hash="HASH",
        status=status,
        lower_bound_count=30,
        upper_bound_count=40,
        unknown_rows_excluded_from_lower_bound=status == PASS,
        checks=(),
        failure_codes=() if status == PASS else ("LOWER_BOUND_INTEGRITY_UNVERIFIED",),
    )


def _result(lower, upper, minimum=30):
    return {
        "schema_version": "candidate-sample-feasibility-result-v1",
        "candidate_id": "CANDIDATE",
        "candidate_hash": "HASH",
        "minimum_required_count": minimum,
        "lower_bound_count": lower,
        "upper_bound_count": upper,
        "status": UNKNOWN,
        "reason_codes": ["END_OF_WINDOW_TRUNCATION", "INSUFFICIENT_EXECUTABLE_OPPORTUNITIES", "SAMPLE_FEASIBILITY_UNKNOWN"],
        "result_hash": "V1_RESULT_HASH",
    }


@pytest.mark.parametrize(
    ("lower", "upper", "expected"),
    [
        (0, 20, BLOCKED_INSUFFICIENT_FEASIBILITY),
        (20, 40, UNKNOWN),
        (29, 30, UNKNOWN),
        (30, 30, PASS),
        (30, 40, PASS),
        (292, 312, PASS),
    ],
)
def test_v2_decision_matrix_uses_proven_lower_bound(lower, upper, expected):
    policy = CandidateSampleFeasibilityPolicyV2(
        minimum_required_count=30,
        activation_time="2026-08-26T06:28:31+08:00",
        governance_reason="test",
    )

    result = policy.decide(_result(lower, upper), _integrity())

    assert result["status"] == expected
    assert result["policy_id"] == "CandidateSampleFeasibilityPolicyV2"
    assert result["exact_count_unknown"] is (lower != upper)


def test_integrity_failure_fails_closed_even_when_lower_reaches_minimum():
    policy = CandidateSampleFeasibilityPolicyV2(
        minimum_required_count=30,
        activation_time="2026-08-26T06:28:31+08:00",
        governance_reason="test",
    )

    result = policy.decide(_result(292, 312), _integrity(status="FAIL"))

    assert result["status"] == UNKNOWN
    assert result["decision_rule"] == "D_LOWER_BOUND_INTEGRITY_NOT_ESTABLISHED_FAIL_CLOSED"
    assert result["blocking_reason_codes"] == ["LOWER_BOUND_INTEGRITY_UNVERIFIED"]


def test_tail_truncation_is_non_blocking_only_after_integrity_passes():
    policy = CandidateSampleFeasibilityPolicyV2(
        minimum_required_count=30,
        activation_time="2026-08-26T06:28:31+08:00",
        governance_reason="test",
    )

    result = policy.decide(_result(30, 40), _integrity())

    assert result["status"] == PASS
    assert result["reason_classification"]["NON_BLOCKING_UNCERTAINTY"] == [
        "END_OF_WINDOW_TRUNCATION",
        "INSUFFICIENT_EXECUTABLE_OPPORTUNITIES",
        "SAMPLE_FEASIBILITY_UNKNOWN",
    ]
    assert result["reason_classification"]["BLOCKING"] == []


def test_v1_result_is_not_mutated_and_policy_identity_is_persistable():
    policy = CandidateSampleFeasibilityPolicyV2(
        minimum_required_count=30,
        activation_time="2026-08-26T06:28:31+08:00",
        governance_reason="test",
    )
    original = _result(292, 312)
    snapshot = deepcopy(original)

    result = policy.decide(original, _integrity())

    assert original == snapshot
    identity = policy.to_dict()
    assert identity["policy_id"] == "CandidateSampleFeasibilityPolicyV2"
    assert identity["predecessor_policy_id"] == "CandidateSampleFeasibilityPreflightV1"
    assert identity["predecessor_policy_hash"]
    assert identity["lower_bound_integrity_version"] == "LOWER_BOUND_INTEGRITY_V2"
    assert result["v1_result_hash"] == "V1_RESULT_HASH"
    assert result["v1_result_immutable"] is True


def test_integrity_audit_uses_event_candidate_frozen_parameters_instead_of_legacy_constants():
    contract = {
        "candidate_id": "CAND_EVENT_TEST_V1",
        "candidate_hash": "HASH_EVENT_TEST",
        "factor_ids": [],
        "event_ids": ["E_TEST"],
        "event_timing_semantics": {"conditions": [{"event_id": "E_TEST", "required": True}]},
        "top_n": 1,
        "max_positions": 1,
        "holding_period_trading_sessions": 5,
        "entry_timing": {"eligible_at": "NEXT_SESSION_OPEN"},
        "selection_rule": {"type": "TOP_N", "top_n": 1},
        "ranking_semantics": {"ranking_rule": {"status": "DETERMINISTIC", "tie_breaker": "SYMBOL_ASC"}},
        "exit_contract": {"candidate_exit_rule": {"type": "FIXED_HOLD", "fixed_holding_days": 5}},
        "t_plus_1_contract": {"enabled": True, "sellable": "NEXT_SESSION_AFTER_ENTRY"},
        "capital_product_contract_identity": {"max_positions": 1},
        "pit_dependencies": {"universe_rule": {
            "type": "PIT_UNIVERSE",
            "delisting": "PIT_DELISTING_FAIL_CLOSED",
            "st_handling": "NO_CURRENT_STATUS_BACKFILL; FAIL_CLOSED_IF_REQUIRED_PIT_MISSING",
            "suspension": "PIT_SUSPENSION_REQUIRED_AT_EXECUTION",
        }},
        "full_semantic_record": {"candidate": {"signal_logic": {
            "signal_semantics": "QUALIFY_THEN_RANK",
            "pipeline": ["SIGNAL_PREDICATE", "RANKING", "SELECTION"],
        }}},
    }
    counts = {
        "candidate_id": contract["candidate_id"],
        "candidate_hash": contract["candidate_hash"],
        "status": UNKNOWN,
        "outcome_blind": True,
        "performance_data_loaded": False,
        "minimum_required_count": 30,
        "raw_event_or_signal_count": 40,
        "qualified_signal_count": 40,
        "pit_eligible_count": 40,
        "data_complete_count": 40,
        "execution_eligible_count": 40,
        "selected_opportunity_count": 40,
        "portfolio_feasible_opportunity_count": 30,
        "lower_bound_count": 30,
        "upper_bound_count": 40,
        "reason_codes": ["END_OF_WINDOW_TRUNCATION"],
        "data_pit_provenance": {
            "pit": {"unknown_state_fail_closed": True},
            "data": {
                "pit_source": "PIT_UNIVERSE_DATASET_V2",
                "pit_source_provenance": {"raw_fallback_used": False},
                "event_ids": ["E_TEST"],
                "event_source": {"exists": True},
                "factor_ids": [],
                "factor_warmup_contracts": [],
                "execution_contract": "A_SHARE_NEXT_SESSION_OPEN_T1_V1",
                "source_read_audit": {"performance_files_read": [], "forbidden_performance_sources_read": False},
            },
        },
    }
    proof = [
        "END_OF_WINDOW_TRUNCATION is a frozen research-window tail boundary, not permission to invent completed exits.",
        "The selected DAILY_FACTOR rows have known structural fields; selection_unknown is false because ranks are materialized, and capacity_unknown is false for the non-cross-sectional provider contract.",
        "Therefore unknown potential rows expand the upper bound but do not erase the already accepted known subset from the lower-bound proof.",
    ]
    safety = {
        "FINAL_TEST_ACCESS": {"analytical": 0, "decision": 0, "physical": 0},
        "NEW_PREDICTIVE_TRIALS": 0,
        "PERFORMANCE_ACCESS": 0,
        "PROSPECTIVE": 0,
        "REAL_ORDER": "DISABLED",
        "outcome_blind": True,
    }

    audit = audit_lower_bound_integrity_v2(
        candidate_contract=contract,
        provider_report={"status": "COMPLETE", "result_counts": counts},
        architecture_manifest={"safety": safety},
        gap_analysis={"preflight_unknown_root_cause": {"proof": proof}},
    )

    assert audit.status == PASS
    assert audit.failure_codes == ()


def test_integrity_audit_accepts_current_canonical_contract_field_dialect():
    contract = {
        "candidate_id": "CAND_CURRENT_EVENT_V1",
        "candidate_hash": "HASH_CURRENT_EVENT",
        "factor_ids": ["SELL_OFF_RECLAIM_STRENGTH"],
        "event_ids": ["DAILY_SELL_OFF_CLOSE_RECLAIM"],
        "top_n": 3,
        "max_positions": 3,
        "holding_period_trading_sessions": 5,
        "entry_timing": {"signal_time": "T_CLOSE", "execution_time": "NEXT_LEGAL_SESSION_OPEN"},
        "selection_rule": {"type": "TOP_N", "top_n": 3, "max_positions": 3},
        "ranking_semantics": {"ranking_rule": {
            "factor_id": "SELL_OFF_RECLAIM_STRENGTH",
            "direction": "DESCENDING",
            "tie_breakers": ["amount_descending", "symbol_ascending"],
        }},
        "exit_contract": {"candidate_exit_rule": {
            "type": "FIXED_HOLD",
            "holding_period_trading_sessions": 5,
        }},
        "t_plus_1_contract": {"enabled": True, "same_session_sell_forbidden": True},
        "capital_product_contract_identity": {"max_positions": 3},
        "pit_dependencies": {"universe_rule": {
            "markets": ["SH", "SZ"],
            "as_of": "T_CLOSE",
            "pit_required": True,
        }},
        "full_semantic_record": {"candidate": {
            "signal_logic": {
                "signal_semantics": "QUALIFY_THEN_RANK",
                "pipeline": ["SIGNAL_PREDICATE", "RANKING", "TOP_N_SELECTION"],
            },
            "risk_filters": [{"type": "PIT_UNIVERSE", "rule": "SH_SZ_ELIGIBLE_AT_T_CLOSE"}],
            "execution_filters": [{"type": "TRADABILITY", "price_limit": "FAIL_CLOSED", "suspension": "FAIL_CLOSED"}],
        }},
    }
    counts = {
        "candidate_id": contract["candidate_id"],
        "candidate_hash": contract["candidate_hash"],
        "status": PASS,
        "outcome_blind": True,
        "performance_data_loaded": False,
        "minimum_required_count": 30,
        "raw_event_or_signal_count": 80,
        "qualified_signal_count": 80,
        "pit_eligible_count": 80,
        "data_complete_count": 80,
        "execution_eligible_count": 80,
        "selected_opportunity_count": 40,
        "portfolio_feasible_opportunity_count": 30,
        "lower_bound_count": 30,
        "upper_bound_count": 30,
        "reason_codes": [],
        "data_pit_provenance": {
            "pit": {"unknown_state_fail_closed": True},
            "data": {
                "pit_source": "PIT_UNIVERSE_DATASET_V2",
                "pit_source_provenance": {"raw_fallback_used": False},
                "event_ids": ["DAILY_SELL_OFF_CLOSE_RECLAIM"],
                "event_source": {"exists": True, "mode": "INLINE_FROZEN_DEFINITION"},
                "factor_ids": ["SELL_OFF_RECLAIM_STRENGTH"],
                "factor_registry_source": {"exists": True, "mode": "INLINE_FROZEN_DEFINITION"},
                "factor_warmup_contracts": [{
                    "factor_id": "SELL_OFF_RECLAIM_STRENGTH",
                    "cross_sectional": False,
                    "warmup_truncation": "NONE",
                }],
                "execution_contract": "A_SHARE_NEXT_SESSION_OPEN_T1_V1",
                "source_read_audit": {"performance_files_read": [], "forbidden_performance_sources_read": False},
            },
        },
    }
    proof = [
        "The selected DAILY_FACTOR rows have known structural fields; selection_unknown is false because ranks are materialized, and capacity_unknown is false for the non-cross-sectional provider contract.",
    ]
    safety = {
        "FINAL_TEST_ACCESS": {"analytical": 0, "decision": 0, "physical": 0},
        "NEW_PREDICTIVE_TRIALS": 0,
        "PERFORMANCE_ACCESS": 0,
        "PROSPECTIVE": 0,
        "REAL_ORDER": "DISABLED",
        "outcome_blind": True,
    }

    audit = audit_lower_bound_integrity_v2(
        candidate_contract=contract,
        provider_report={"status": "COMPLETE", "result_counts": counts},
        architecture_manifest={"safety": safety},
        gap_analysis={"preflight_unknown_root_cause": {"proof": proof}},
    )

    assert audit.status == PASS
    assert audit.failure_codes == ()


def test_integrity_audit_accepts_rank_only_composite_current_contract_dialect():
    contract = {
        "candidate_id": "CAND_RANK_ONLY_CURRENT_V1",
        "candidate_hash": "HASH_RANK_ONLY_CURRENT",
        "factor_ids": ["AMOUNT_ACCEL", "ILLIQUIDITY_AMIHUD"],
        "event_ids": [],
        "top_n": 3,
        "max_positions": 3,
        "holding_period_trading_sessions": 5,
        "entry_timing": {"signal": "T_CLOSE", "entry": "NEXT_SESSION_OPEN", "same_session_entry": False},
        "selection_rule": {"type": "TOP_N", "top_n": 3, "tie_break": "STABLE_SYMBOL_ASC"},
        "ranking_semantics": {"ranking_rule": {
            "type": "COMPOSITE_PERCENTILE",
            "weighting": "EQUAL",
            "components": [
                {"factor_id": "AMOUNT_ACCEL", "direction": "DESC"},
                {"factor_id": "ILLIQUIDITY_AMIHUD", "direction": "ASC"},
            ],
        }},
        "exit_contract": {"candidate_exit_rule": {
            "type": "FIXED_HOLD",
            "holding_period_trading_sessions": 5,
        }},
        "t_plus_1_contract": {"enabled": True, "sell_not_before": "NEXT_TRADING_SESSION"},
        "capital_product_contract_identity": {"max_positions": 3},
        "pit_dependencies": {"universe_rule": {
            "markets": ["SH", "SZ"],
            "membership": "PIT_CANONICAL_UNIVERSE",
            "exclude_nontradable": True,
        }},
        "full_semantic_record": {"candidate": {
            "signal_logic": {
                "type": "CROSS_SECTIONAL_COMPOSITE_RANK",
                "combination": "EQUAL_WEIGHTED_PERCENTILE",
            },
            "risk_filters": [
                {"type": "PIT_ELIGIBLE_UNIVERSE_REQUIRED"},
                {"type": "NO_ST_OR_DELISTING_RISK_IF_PIT_STATE_AVAILABLE"},
            ],
            "execution_filters": [
                {"type": "SUSPENSION_FAIL_CLOSED"},
                {"type": "PRICE_LIMIT_FAIL_CLOSED"},
                {"type": "T_PLUS_1_ENFORCED"},
            ],
        }},
    }
    counts = {
        "candidate_id": contract["candidate_id"],
        "candidate_hash": contract["candidate_hash"],
        "status": UNKNOWN,
        "outcome_blind": True,
        "performance_data_loaded": False,
        "minimum_required_count": 30,
        "raw_event_or_signal_count": 300,
        "qualified_signal_count": 240,
        "pit_eligible_count": 240,
        "data_complete_count": 240,
        "execution_eligible_count": 220,
        "selected_opportunity_count": 90,
        "portfolio_feasible_opportunity_count": 60,
        "lower_bound_count": 60,
        "upper_bound_count": 75,
        "reason_codes": ["PIT_STATE_UNKNOWN"],
        "data_pit_provenance": {
            "pit": {"unknown_state_fail_closed": True},
            "data": {
                "pit_source": "PIT_UNIVERSE_DATASET_V2",
                "pit_source_provenance": {"raw_fallback_used": False},
                "event_ids": [],
                "event_source": {"exists": True},
                "factor_ids": ["AMOUNT_ACCEL", "ILLIQUIDITY_AMIHUD"],
                "factor_registry_source": {"exists": True},
                "factor_warmup_contracts": [
                    {"factor_id": "AMOUNT_ACCEL", "cross_sectional": False, "warmup_truncation": "NONE"},
                    {"factor_id": "ILLIQUIDITY_AMIHUD", "cross_sectional": False, "warmup_truncation": "NONE"},
                ],
                "execution_contract": "A_SHARE_NEXT_SESSION_OPEN_T1_V1",
                "source_read_audit": {"performance_files_read": [], "forbidden_performance_sources_read": False},
            },
        },
    }
    proof = [
        "The selected DAILY_FACTOR rows have known structural fields; selection_unknown is false because ranks are materialized, and capacity_unknown is false for the non-cross-sectional provider contract.",
    ]
    safety = {
        "FINAL_TEST_ACCESS": {"analytical": 0, "decision": 0, "physical": 0},
        "NEW_PREDICTIVE_TRIALS": 0,
        "PERFORMANCE_ACCESS": 0,
        "PROSPECTIVE": 0,
        "REAL_ORDER": "DISABLED",
        "outcome_blind": True,
    }

    audit = audit_lower_bound_integrity_v2(
        candidate_contract=contract,
        provider_report={"status": "COMPLETE", "result_counts": counts},
        architecture_manifest={"safety": safety},
        gap_analysis={"preflight_unknown_root_cause": {"proof": proof}},
    )

    assert audit.status == PASS
    assert audit.failure_codes == ()
