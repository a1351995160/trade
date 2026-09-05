"""Governed sample-feasibility adjudication layered on immutable V1 results.

V1 remains the historical structural preflight.  This module only audits the
proven lower-bound evidence and applies the V2 threshold semantics; it never
rebuilds observations or reads performance artifacts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .common import jsonable, stable_hash


POLICY_ID = "CandidateSampleFeasibilityPolicyV2"
POLICY_VERSION = "2.0.0"
LOWER_BOUND_INTEGRITY_VERSION = "LOWER_BOUND_INTEGRITY_V2"
PASS = "PASS"
BLOCKED_INSUFFICIENT_FEASIBILITY = "BLOCKED_INSUFFICIENT_FEASIBILITY"
UNKNOWN = "UNKNOWN"

BLOCKING = "BLOCKING"
NON_BLOCKING_UNCERTAINTY = "NON_BLOCKING_UNCERTAINTY"
INFORMATIONAL = "INFORMATIONAL"

V1_PREDECESSOR_ID = "CandidateSampleFeasibilityPreflightV1"
V1_PREDECESSOR_HASH = stable_hash({
    "preflight_version": V1_PREDECESSOR_ID,
    "decision_semantics": "UNKNOWN_WHEN_ANY_UNKNOWN_OR_LOWER_BELOW_MINIMUM",
    "minimum_source": "ValidationDecisionPolicyV2.sample_adequacy",
})


class SampleFeasibilityPolicyV2ContractError(ValueError):
    """Raised when a V2 governance input violates the frozen contract."""


def _mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise SampleFeasibilityPolicyV2ContractError("governance input must be a mapping")
    return {str(key): jsonable(item) for key, item in value.items()}


@dataclass(frozen=True)
class LowerBoundIntegrityAuditV2:
    """Evidence that the lower bound contains only structurally proven rows."""

    candidate_id: str
    candidate_hash: str
    status: str
    lower_bound_count: int
    upper_bound_count: int
    unknown_rows_excluded_from_lower_bound: bool
    checks: tuple[Mapping[str, Any], ...]
    failure_codes: tuple[str, ...] = ()
    generated_at: str = ""
    audit_hash: str = ""

    def __post_init__(self) -> None:
        if self.status not in {PASS, "FAIL"}:
            raise SampleFeasibilityPolicyV2ContractError("invalid lower-bound audit status")
        object.__setattr__(self, "checks", tuple(_mapping(item) for item in self.checks))
        object.__setattr__(self, "failure_codes", tuple(sorted(set(self.failure_codes))))
        if not self.audit_hash:
            object.__setattr__(self, "audit_hash", stable_hash(self._hash_payload()))

    def _hash_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("audit_hash", None)
        payload.pop("generated_at", None)
        return payload

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "lower-bound-integrity-audit-v2",
            "audit_version": LOWER_BOUND_INTEGRITY_VERSION,
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "status": self.status,
            "lower_bound_count": self.lower_bound_count,
            "upper_bound_count": self.upper_bound_count,
            "unknown_rows_excluded_from_lower_bound": self.unknown_rows_excluded_from_lower_bound,
            "checks": list(self.checks),
            "failure_codes": list(self.failure_codes),
            "generated_at": self.generated_at,
            "audit_hash": self.audit_hash,
            "outcome_blind": True,
            "performance_data_loaded": False,
            "performance_files_read": [],
        }


@dataclass(frozen=True)
class CandidateSampleFeasibilityPolicyV2:
    """Fail-closed threshold policy for structurally proven sample supply."""

    minimum_required_count: int
    activation_time: str
    governance_reason: str
    predecessor_policy_id: str = V1_PREDECESSOR_ID
    predecessor_policy_hash: str = V1_PREDECESSOR_HASH
    lower_bound_integrity_version: str = LOWER_BOUND_INTEGRITY_VERSION
    policy_id: str = POLICY_ID
    policy_version: str = POLICY_VERSION
    decision_semantics: str = "UPPER_BOUND_BLOCKS;VALID_LOWER_BOUND_PASSES;INTERVAL_UNKNOWN;INTEGRITY_FAIL_CLOSED"

    def __post_init__(self) -> None:
        if self.minimum_required_count <= 0:
            raise SampleFeasibilityPolicyV2ContractError("minimum_required_count must be positive")
        if not self.activation_time:
            raise SampleFeasibilityPolicyV2ContractError("activation_time is required")

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "minimum_required_count": self.minimum_required_count,
            "decision_semantics": self.decision_semantics,
            "predecessor_policy_id": self.predecessor_policy_id,
            "predecessor_policy_hash": self.predecessor_policy_hash,
            "activation_time": self.activation_time,
            "governance_reason": self.governance_reason,
            "lower_bound_integrity_version": self.lower_bound_integrity_version,
        }

    @property
    def policy_hash(self) -> str:
        return stable_hash(self._identity_payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self._identity_payload(), "policy_hash": self.policy_hash}

    def decide(
        self,
        v1_result: Mapping[str, Any],
        lower_bound_integrity: LowerBoundIntegrityAuditV2 | Mapping[str, Any],
    ) -> dict[str, Any]:
        """Adjudicate a V1 result without mutating or rewriting it."""

        result = _mapping(v1_result)
        integrity = _mapping(lower_bound_integrity)
        lower = int(result.get("lower_bound_count", 0))
        upper = int(result.get("upper_bound_count", 0))
        minimum = int(result.get("minimum_required_count", self.minimum_required_count))
        integrity_pass = integrity.get("status") == PASS and bool(integrity.get("unknown_rows_excluded_from_lower_bound"))

        if not integrity_pass:
            status = UNKNOWN
            decision_rule = "D_LOWER_BOUND_INTEGRITY_NOT_ESTABLISHED_FAIL_CLOSED"
            blocking_reason_codes = ["LOWER_BOUND_INTEGRITY_UNVERIFIED"]
        elif upper < minimum:
            status = BLOCKED_INSUFFICIENT_FEASIBILITY
            decision_rule = "A_UPPER_BOUND_BELOW_MINIMUM"
            blocking_reason_codes = ["INSUFFICIENT_STRUCTURALLY_PROVEN_OR_POSSIBLE_OPPORTUNITIES"]
        elif lower >= minimum:
            status = PASS
            decision_rule = "B_VALID_LOWER_BOUND_AT_OR_ABOVE_MINIMUM"
            blocking_reason_codes = []
        else:
            status = UNKNOWN
            decision_rule = "C_LOWER_UPPER_INTERVAL_CROSSES_MINIMUM"
            blocking_reason_codes = ["EXACT_SAMPLE_COUNT_UNRESOLVED"]

        reason_codes = tuple(sorted(set(str(item) for item in result.get("reason_codes", ()))))
        classifications = classify_reason_codes(
            reason_codes,
            lower_bound_integrity_pass=integrity_pass,
            lower_bound_count=lower,
            minimum_required_count=minimum,
        )
        exact_count_unknown = lower != upper
        return {
            "schema_version": "candidate-sample-feasibility-result-v2",
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "decision_semantics": self.decision_semantics,
            "decision_rule": decision_rule,
            "status": status,
            "minimum_required_count": minimum,
            "lower_bound_count": lower,
            "upper_bound_count": upper,
            "exact_count_unknown": exact_count_unknown,
            "reason_codes": list(reason_codes),
            "reason_classification": classifications,
            "blocking_reason_codes": blocking_reason_codes,
            "lower_bound_integrity_status": integrity.get("status"),
            "lower_bound_integrity_version": self.lower_bound_integrity_version,
            "lower_bound_integrity_hash": integrity.get("audit_hash"),
            "v1_result_hash": result.get("result_hash"),
            "v1_status_preserved": result.get("status"),
            "v1_result_immutable": True,
            "outcome_blind": True,
            "performance_data_loaded": False,
            "performance_files_read": [],
        }


def classify_reason_codes(
    reason_codes: tuple[str, ...] | list[str],
    *,
    lower_bound_integrity_pass: bool,
    lower_bound_count: int,
    minimum_required_count: int,
) -> dict[str, list[str]]:
    """Classify V1 explanations without silently weakening a true blocker."""

    result = {BLOCKING: [], NON_BLOCKING_UNCERTAINTY: [], INFORMATIONAL: []}
    for code in sorted(set(reason_codes)):
        if code == "END_OF_WINDOW_TRUNCATION":
            target = NON_BLOCKING_UNCERTAINTY if lower_bound_integrity_pass else BLOCKING
        elif code == "INSUFFICIENT_EXECUTABLE_OPPORTUNITIES":
            target = NON_BLOCKING_UNCERTAINTY if lower_bound_integrity_pass and lower_bound_count >= minimum_required_count else BLOCKING
        elif code == "SAMPLE_FEASIBILITY_UNKNOWN":
            target = NON_BLOCKING_UNCERTAINTY if lower_bound_integrity_pass else BLOCKING
        elif code in {"PIT_STATE_UNKNOWN", "DATA_COVERAGE_INSUFFICIENT", "STRUCTURE_EXIT_COUNT_UNKNOWN", "PORTFOLIO_CAPACITY_BOUND"}:
            target = BLOCKING
        else:
            target = INFORMATIONAL
        result[target].append(code)
    return result


def audit_lower_bound_integrity_v2(
    *,
    candidate_contract: Mapping[str, Any],
    provider_report: Mapping[str, Any],
    architecture_manifest: Mapping[str, Any],
    gap_analysis: Mapping[str, Any],
    historical_v1_report: Mapping[str, Any] | None = None,
    generated_at: str = "",
) -> LowerBoundIntegrityAuditV2:
    """Audit the current lower bound against the frozen structural contracts.

    The provider report is a structural aggregate generated by V1.  The audit
    accepts its lower bound only when the candidate, PIT, factor, execution,
    ranking, capacity, and window-boundary contracts all line up.  Any missing
    evidence fails closed.
    """

    contract = _mapping(candidate_contract)
    report = _mapping(provider_report)
    counts = _mapping(report.get("result_counts", report))
    provenance = _mapping(counts.get("data_pit_provenance", {}))
    data = _mapping(provenance.get("data", {}))
    semantic = _mapping(_mapping(contract.get("full_semantic_record", {})).get("candidate", {}))
    pit_dependencies = _mapping(contract.get("pit_dependencies", {}))
    universe_rule = _mapping(pit_dependencies.get("universe_rule", semantic.get("universe_rule", {})))
    risk_filters = tuple(_mapping(item) for item in semantic.get("risk_filters", ()) if isinstance(item, Mapping))
    execution_filters = tuple(_mapping(item) for item in semantic.get("execution_filters", ()) if isinstance(item, Mapping))
    entry_timing = _mapping(contract.get("entry_timing", semantic.get("entry_timing", {})))
    exit_contract = _mapping(contract.get("exit_contract", {}))
    exit_rule = _mapping(exit_contract.get("candidate_exit_rule", {}))
    t1_contract = _mapping(contract.get("t_plus_1_contract", semantic.get("t_plus_1_contract", {})))
    ranking_semantics = _mapping(contract.get("ranking_semantics", {}))
    ranking_rule = _mapping(ranking_semantics.get("ranking_rule", semantic.get("ranking_rule", {})))
    selection_rule = _mapping(contract.get("selection_rule", semantic.get("selection_rule", {})))
    capital = _mapping(contract.get("capital_product_contract_identity", {}))
    warmups = data.get("factor_warmup_contracts", ())
    if not isinstance(warmups, (list, tuple)):
        warmups = ()
    proof = _mapping(_mapping(gap_analysis).get("preflight_unknown_root_cause", {}))
    proof_lines = {str(item) for item in proof.get("proof", ())}
    safety = _mapping(_mapping(architecture_manifest).get("safety", {}))

    checks: list[dict[str, Any]] = []
    failures: list[str] = []

    def check(name: str, passed: bool, evidence: Any, failure_code: str) -> None:
        checks.append({"check": name, "status": PASS if passed else "FAIL", "evidence": jsonable(evidence)})
        if not passed:
            failures.append(failure_code)

    candidate_id = str(contract.get("candidate_id", ""))
    candidate_hash = str(contract.get("candidate_hash", ""))
    report_id = str(counts.get("candidate_id", ""))
    report_hash = str(counts.get("candidate_hash", ""))
    lower = int(counts.get("lower_bound_count", 0))
    upper = int(counts.get("upper_bound_count", 0))
    minimum = int(counts.get("minimum_required_count", 0))
    selected = int(counts.get("selected_opportunity_count", 0))
    portfolio = int(counts.get("portfolio_feasible_opportunity_count", 0))
    qualified = int(counts.get("qualified_signal_count", 0))
    raw = int(counts.get("raw_event_or_signal_count", 0))
    pit_count = int(counts.get("pit_eligible_count", 0))
    data_count = int(counts.get("data_complete_count", 0))
    execution_count = int(counts.get("execution_eligible_count", 0))

    check("candidate_identity", candidate_id and candidate_id == report_id and candidate_hash == report_hash, {"candidate_id": candidate_id, "candidate_hash": candidate_hash, "report_id": report_id, "report_hash": report_hash}, "CANDIDATE_IDENTITY_MISMATCH")
    stage_counts_are_bounded = all(
        0 <= value <= raw
        for value in (qualified, pit_count, data_count, execution_count, selected, portfolio, lower, upper)
    )
    known_subset_is_ordered = 0 <= portfolio <= selected <= qualified <= raw and lower == portfolio <= upper
    check(
        "count_sanity_and_bound",
        stage_counts_are_bounded and known_subset_is_ordered,
        {
            "raw": raw,
            "qualified": qualified,
            "pit": pit_count,
            "data": data_count,
            "execution": execution_count,
            "selected": selected,
            "portfolio": portfolio,
            "lower": lower,
            "upper": upper,
            "stage_count_semantics": "pit/data/execution are independent diagnostic counts over signal-accepted rows; qualified/selected/portfolio form the proven subset chain",
        },
        "COUNT_SANITY_OR_BOUND_NOT_PROVABLE",
    )
    check("lower_bound_is_known_portfolio_subset", lower == portfolio and lower <= upper, {"lower_bound_count": lower, "portfolio_feasible_opportunity_count": portfolio, "upper_bound_count": upper}, "LOWER_BOUND_NOT_TIED_TO_KNOWN_PORTFOLIO")
    structural_status = counts.get("status") in {PASS, UNKNOWN, BLOCKED_INSUFFICIENT_FEASIBILITY}
    check("provider_completed_structural_only", report.get("status") == "COMPLETE" and structural_status and counts.get("outcome_blind") is True and counts.get("performance_data_loaded") is False, {"provider_status": report.get("status"), "result_status": counts.get("status"), "outcome_blind": counts.get("outcome_blind"), "performance_data_loaded": counts.get("performance_data_loaded")}, "PROVIDER_RESULT_NOT_STRUCTURAL_ONLY")
    check("pit_is_canonical_and_fail_closed", data.get("pit_source") == "PIT_UNIVERSE_DATASET_V2" and _mapping(data.get("pit_source_provenance", {})).get("raw_fallback_used") is False and _mapping(provenance.get("pit", {})).get("unknown_state_fail_closed") is True, {"pit_source": data.get("pit_source"), "pit_source_provenance": data.get("pit_source_provenance"), "unknown_state_fail_closed": _mapping(provenance.get("pit", {})).get("unknown_state_fail_closed")}, "PIT_PROVENANCE_NOT_FAIL_CLOSED")
    legacy_pit_contract = (
        universe_rule.get("type") == "PIT_UNIVERSE"
        and universe_rule.get("delisting") == "PIT_DELISTING_FAIL_CLOSED"
        and universe_rule.get("st_handling") == "NO_CURRENT_STATUS_BACKFILL; FAIL_CLOSED_IF_REQUIRED_PIT_MISSING"
        and universe_rule.get("suspension") == "PIT_SUSPENSION_REQUIRED_AT_EXECUTION"
    )
    canonical_pit_contract = (
        (
            universe_rule.get("pit_required") is True
            or (
                universe_rule.get("as_of") in {"signal_close", "T_CLOSE", "generated_at"}
                and universe_rule.get("security_type") == "ordinary_listed_equities"
            )
            or (
                universe_rule.get("membership") == "PIT_CANONICAL_UNIVERSE"
                and universe_rule.get("exclude_nontradable") is True
            )
        )
        and any(
            item.get("type") in {"PIT_UNIVERSE", "PIT_ELIGIBLE_UNIVERSE_REQUIRED"}
            for item in risk_filters
        )
        and (
            any(
                item.get("type") == "TRADABILITY"
                and item.get("price_limit") == "FAIL_CLOSED"
                and item.get("suspension") == "FAIL_CLOSED"
                for item in execution_filters
            )
            or {
                str(item.get("type")) for item in execution_filters
            } >= {"SUSPENSION_FAIL_CLOSED", "PRICE_LIMIT_FAIL_CLOSED", "T_PLUS_1_ENFORCED"}
            or (
                {str(item.get("type")) for item in risk_filters} >= {"SUSPENSION", "PRICE_LIMIT"}
                and _mapping(semantic.get("suspension_contract", {})).get("unknown_state") == "FAIL_CLOSED"
                and _mapping(semantic.get("limit_up_down_contract", {})).get("unknown_state") == "FAIL_CLOSED"
            )
        )
    )
    check("listing_st_suspension_tradability_are_contractual", legacy_pit_contract or canonical_pit_contract, {"universe_rule": universe_rule, "risk_filters": risk_filters, "execution_filters": execution_filters}, "PIT_LISTING_STATUS_CONTRACT_MISSING")
    factor_ids = {str(item) for item in contract.get("factor_ids", semantic.get("factor_graph", ())) }
    event_ids = {str(item) for item in contract.get("event_ids", ())}
    observed_factor_ids = {str(item) for item in data.get("factor_ids", ())}
    observed_event_ids = {str(item) for item in data.get("event_ids", ())}
    warmup_ok = bool(warmups) and all(_mapping(item).get("cross_sectional") is False and _mapping(item).get("warmup_truncation") == "NONE" for item in warmups)
    factor_ok = not factor_ids or (factor_ids == observed_factor_ids and data.get("factor_registry_source", {}).get("exists") is True and warmup_ok)
    event_ok = not event_ids or (event_ids == observed_event_ids and data.get("event_source", {}).get("exists") is True)
    check("factor_availability_is_pit_and_complete", bool(factor_ids or event_ids) and factor_ok and event_ok, {"candidate_factor_ids": sorted(factor_ids), "observed_factor_ids": sorted(observed_factor_ids), "warmup_contracts": warmups, "candidate_event_ids": sorted(event_ids), "observed_event_ids": sorted(observed_event_ids), "event_source": data.get("event_source", {})}, "FACTOR_AVAILABILITY_NOT_PROVABLE")
    signal_logic = _mapping(semantic.get("signal_logic", {}))
    explicit_qualify_then_rank = (
        signal_logic.get("signal_semantics") == "QUALIFY_THEN_RANK"
        and "SIGNAL_PREDICATE" in signal_logic.get("pipeline", ())
        and "RANKING" in signal_logic.get("pipeline", ())
    )
    rank_only_qualify_then_rank = str(signal_logic.get("type") or "").upper() in {
        "CROSS_SECTIONAL_COMPOSITE_RANK",
        "RANK_ONLY",
    }
    check(
        "signal_qualification_is_before_selection",
        explicit_qualify_then_rank or rank_only_qualify_then_rank,
        {"signal_logic": signal_logic},
        "SIGNAL_QUALIFICATION_ORDER_NOT_PROVABLE",
    )
    selection_proven = any("selection_unknown is false because ranks are materialized" in line for line in proof_lines)
    capacity_proven = any("capacity_unknown is false for the non-cross-sectional provider contract" in line for line in proof_lines)
    expected_top_n = int(contract.get("top_n", 0))
    tie_breakers = {str(item).upper() for item in ranking_rule.get("tie_breakers", ())}
    if ranking_rule.get("tie_breaker"):
        tie_breakers.add(str(ranking_rule.get("tie_breaker")).upper())
    deterministic_ranking = (
        (ranking_rule.get("status") == "DETERMINISTIC" and ranking_rule.get("tie_breaker") == "SYMBOL_ASC")
        or (
            ranking_rule.get("factor_id")
            and ranking_rule.get("direction") in {"ASCENDING", "DESCENDING"}
            and bool(tie_breakers & {"SYMBOL_ASC", "SYMBOL_ASCENDING"})
        )
        or (
            str(ranking_rule.get("type") or "").upper() == "COMPOSITE_PERCENTILE"
            and str(ranking_rule.get("weighting") or "").upper() == "EQUAL"
            and bool(ranking_rule.get("components"))
            and all(
                isinstance(item, Mapping)
                and item.get("factor_id")
                and str(item.get("direction") or "").upper() in {"ASC", "DESC", "ASCENDING", "DESCENDING"}
                for item in ranking_rule.get("components", ())
            )
            and str(selection_rule.get("tie_break") or "").upper() in {
                "STABLE_SYMBOL_ASC",
                "SYMBOL_ASC",
                "SYMBOL_ASCENDING",
            }
        )
    )
    selection_type = selection_rule.get("type", selection_rule.get("method"))
    check("ranking_and_top_n_are_deterministic", expected_top_n >= 1 and deterministic_ranking and selection_type == "TOP_N" and int(selection_rule.get("top_n", 0)) == expected_top_n and selection_proven, {"ranking_rule": ranking_rule, "selection_rule": selection_rule, "frozen_top_n": expected_top_n, "selection_proven": selection_proven, "proof": sorted(proof_lines)}, "RANKING_TOP_N_NOT_PROVABLE")
    entry_is_next_session = (
        entry_timing.get("eligible_at") == "NEXT_SESSION_OPEN"
        or entry_timing.get("execution_time") in {"NEXT_SESSION_OPEN", "NEXT_LEGAL_SESSION_OPEN"}
        or (
            entry_timing.get("entry") in {"NEXT_SESSION_OPEN", "NEXT_LEGAL_SESSION_OPEN"}
            and entry_timing.get("same_session_entry") is False
        )
        or (
            entry_timing.get("decision_bar") == "signal_session_close"
            and entry_timing.get("entry_bar") == "next_eligible_session_open"
            and entry_timing.get("same_bar_execution") is False
        )
    )
    t1_is_frozen = (
        t1_contract.get("sellable") == "NEXT_SESSION_AFTER_ENTRY"
        or t1_contract.get("same_session_sell_forbidden") is True
        or (
            t1_contract.get("enabled") is True
            and t1_contract.get("sell_not_before") in {"NEXT_TRADING_SESSION", "NEXT_SESSION_AFTER_ENTRY"}
        )
        or (
            t1_contract.get("enabled") is True
            and t1_contract.get("entry") == "NEXT_ELIGIBLE_SESSION_OPEN"
            and t1_contract.get("signal_day_execution") is False
        )
    )
    check("entry_execution_and_t_plus_1_are_frozen", entry_is_next_session and data.get("execution_contract") == "A_SHARE_NEXT_SESSION_OPEN_T1_V1" and t1_contract.get("enabled") is True and t1_is_frozen, {"entry_timing": entry_timing, "execution_contract": data.get("execution_contract"), "t_plus_1_contract": t1_contract}, "ENTRY_EXECUTION_CONTRACT_NOT_PROVABLE")
    expected_max_positions = int(contract.get("max_positions", 0))
    expected_holding_days = int(contract.get("holding_period_trading_sessions", 0))
    exit_holding_days = int(exit_rule.get("fixed_holding_days", exit_rule.get("holding_period_trading_sessions", exit_rule.get("holding_sessions", 0))))
    check("affordability_holding_overlap_and_capacity_are_known", expected_max_positions >= 1 and expected_holding_days >= 1 and int(capital.get("max_positions", 0)) == expected_max_positions and exit_holding_days == expected_holding_days and exit_rule.get("type") == "FIXED_HOLD" and capacity_proven, {"capital_contract": capital, "exit_rule": exit_rule, "frozen_max_positions": expected_max_positions, "frozen_holding_days": expected_holding_days, "capacity_proven": capacity_proven, "proof": sorted(proof_lines)}, "PORTFOLIO_CAPACITY_NOT_PROVABLE")
    reason_codes = {str(item) for item in counts.get("reason_codes", ())}
    boundary_proof = "END_OF_WINDOW_TRUNCATION is a frozen research-window tail boundary, not permission to invent completed exits."
    unknown_tail_proof = "Therefore unknown potential rows expand the upper bound but do not erase the already accepted known subset from the lower-bound proof."
    has_unknown_tail = "END_OF_WINDOW_TRUNCATION" in reason_codes
    boundary_is_safe = (
        has_unknown_tail and boundary_proof in proof_lines and unknown_tail_proof in proof_lines and upper >= lower
    ) or not has_unknown_tail
    check("window_boundary_does_not_enter_lower_bound", boundary_is_safe, {"reason_codes": sorted(reason_codes), "lower_bound_count": lower, "upper_bound_count": upper, "proof": sorted(proof_lines)}, "WINDOW_TAIL_CONTAMINATES_LOWER_BOUND")
    source_audit = _mapping(data.get("source_read_audit", {}))
    check("performance_sources_not_read", source_audit.get("performance_files_read") == [] and source_audit.get("forbidden_performance_sources_read") is False and _mapping(architecture_manifest).get("safety", {}).get("outcome_blind") is True, {"source_read_audit": source_audit, "architecture_safety": safety}, "PERFORMANCE_LEAKAGE_OR_SAFETY_DRIFT")
    check("architecture_safety_unchanged", safety.get("FINAL_TEST_ACCESS") == {"analytical": 0, "decision": 0, "physical": 0} and safety.get("NEW_PREDICTIVE_TRIALS") == 0 and safety.get("PERFORMANCE_ACCESS") == 0 and safety.get("PROSPECTIVE") == 0 and safety.get("REAL_ORDER") == "DISABLED", {"safety": safety}, "ARCHITECTURE_SAFETY_DRIFT")
    if historical_v1_report is not None:
        old_counts = _mapping(_mapping(historical_v1_report).get("result_counts", historical_v1_report))
        check("v1_historical_identity_and_status_preserved", old_counts.get("candidate_id") == report_id and old_counts.get("candidate_hash") == report_hash and old_counts.get("status") == "UNKNOWN", {"historical_v1": {"candidate_id": old_counts.get("candidate_id"), "candidate_hash": old_counts.get("candidate_hash"), "status": old_counts.get("status"), "lower_bound_count": old_counts.get("lower_bound_count")}}, "V1_HISTORY_NOT_PRESERVED")

    return LowerBoundIntegrityAuditV2(
        candidate_id=candidate_id,
        candidate_hash=candidate_hash,
        status=PASS if not failures else "FAIL",
        lower_bound_count=lower,
        upper_bound_count=upper,
        unknown_rows_excluded_from_lower_bound=not failures,
        checks=tuple(checks),
        failure_codes=tuple(failures),
        generated_at=generated_at,
    )


__all__ = [
    "BLOCKING",
    "BLOCKED_INSUFFICIENT_FEASIBILITY",
    "CandidateSampleFeasibilityPolicyV2",
    "INFORMATIONAL",
    "LOWER_BOUND_INTEGRITY_VERSION",
    "LowerBoundIntegrityAuditV2",
    "NON_BLOCKING_UNCERTAINTY",
    "PASS",
    "POLICY_ID",
    "POLICY_VERSION",
    "SampleFeasibilityPolicyV2ContractError",
    "UNKNOWN",
    "V1_PREDECESSOR_HASH",
    "V1_PREDECESSOR_ID",
    "audit_lower_bound_integrity_v2",
    "classify_reason_codes",
]
