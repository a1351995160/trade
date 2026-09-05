"""Outcome-blind ValidationDecisionPolicyV2 contract and verifier.

This module is deliberately independent from candidate/result artifacts.  It
contains only product constraints, pre-existing governance contracts,
execution invariants and statistical-method contracts that are allowed to be
known before a future performance trial.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping
import hashlib
import json


POLICY_ID = "VALIDATION_DECISION_POLICY_V2"
SCHEMA_VERSION = "validation-decision-policy-v2"
POLICY_VERSION = "2.0.0"
PARENT_POLICY_ID = "STRATEGY_VALIDATION_POLICY_V1"
ENGINE_CONTRACT_VERSION = "BACKTEST_ENGINE_V2"
MULTIPLE_TESTING_CONTRACT_VERSION = "STRATEGY_MULTIPLE_TESTING_POLICY_V2"

ROLES = {"HARD_GATE", "SOFT_EVIDENCE", "REPORT_ONLY"}
STAGES = {"PRE_PERFORMANCE", "LOCAL", "BATCH_INFERENCE", "FINAL_ADJUDICATION"}
THRESHOLD_SOURCES = {
    "PRE_EXISTING_FROZEN_POLICY",
    "PRODUCT_CONSTRAINT",
    "ENGINE_INVARIANT",
    "MARKET_MICROSTRUCTURE_RULE",
    "STATISTICAL_METHOD_CONTRACT",
    "INDEPENDENT_PRE_OUTCOME_METHOD",
    "NONE",
}
CLASSIFICATIONS = {"RESEARCH_PASSED", "PROMISING", "WEAK", "REJECTED", "BLOCKED", "ENGINEERING_BLOCKED"}

REQUIRED_HARD_GATE_IDS = (
    "engine_integrity",
    "data_validity",
    "pit_validity",
    "sample_adequacy",
    "local_base_return",
    "local_profit_factor",
    "raw_bootstrap_support",
    "cost_stress_combined_x2",
    "small_capital_execution_feasibility",
    "baseline_preregistration",
    "intended_holding_contract",
    "microstructure_realism",
    "candidate_similarity_control",
    "search_budget_reservation",
    "multiple_testing_adjusted_support",
)


class ValidationPolicyV2Error(ValueError):
    """Raised when a policy, evidence contract or lock is invalid."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _gate(
    gate_id: str,
    *,
    role: str,
    stage: str,
    metric: str,
    direction: str,
    threshold: Any,
    threshold_source: str,
    failure_code: str,
    failure_category: str,
    required: bool,
    missing_behavior: str,
    preregistration_required: bool,
    rationale: str,
) -> dict[str, Any]:
    return {
        "evidence_id": gate_id,
        "role": role,
        "stage": stage,
        "metric": metric,
        "direction": direction,
        "threshold": threshold,
        "threshold_source": threshold_source,
        "failure_code": failure_code,
        "failure_category": failure_category,
        "required": required,
        "missing_behavior": missing_behavior,
        "preregistration_required": preregistration_required,
        "rationale": rationale,
    }


def _default_gates() -> dict[str, dict[str, Any]]:
    return {
        "engine_integrity": _gate(
            "engine_integrity", role="HARD_GATE", stage="LOCAL", metric="engine_invariant_set",
            direction="ALL_VALID", threshold="ALL_REQUIRED_INVARIANTS_VALID", threshold_source="ENGINE_INVARIANT",
            failure_code="ENGINE_INVARIANT_VIOLATION", failure_category="ENGINE_FAILURE", required=True,
            missing_behavior="FAIL_CLOSED_ENGINEERING_BLOCKED", preregistration_required=False,
            rationale="Engine validity is an execution invariant, not an alpha preference.",
        ),
        "data_validity": _gate(
            "data_validity", role="HARD_GATE", stage="LOCAL", metric="data_lineage_and_availability",
            direction="VALID", threshold="NO_MISSING_REQUIRED_DATA_OR_LINEAGE_BREAK", threshold_source="PRE_EXISTING_FROZEN_POLICY",
            failure_code="DATA_LINEAGE_INVALID", failure_category="DATA_FAILURE", required=True,
            missing_behavior="FAIL_CLOSED_BLOCKED", preregistration_required=False,
            rationale="Required data must be available with an auditable lineage before evidence is interpreted.",
        ),
        "pit_validity": _gate(
            "pit_validity", role="HARD_GATE", stage="LOCAL", metric="asof_pit_universe_availability_event_factor_timestamps",
            direction="VALID", threshold="ASOF_CORRECT_PIT_NO_FUTURE_DATA_NO_ILLEGAL_FINAL_TEST_ACCESS", threshold_source="PRE_EXISTING_FROZEN_POLICY",
            failure_code="PIT_OR_FUTURE_DATA_VIOLATION", failure_category="PIT_FAILURE", required=True,
            missing_behavior="SYMBOL_LOCAL_FAIL_CLOSED", preregistration_required=False,
            rationale="PIT and available_at contracts are mandatory data validity boundaries.",
        ),
        "sample_adequacy": _gate(
            "sample_adequacy", role="HARD_GATE", stage="LOCAL", metric="closed_trade_count_and_bootstrap_status",
            direction="COUNT_GE_30_AND_COMPLETE", threshold="closed_trade_count >= 30 AND bootstrap_status == COMPLETE", threshold_source="PRE_EXISTING_FROZEN_POLICY",
            failure_code="INSUFFICIENT_SAMPLE", failure_category="SAMPLE_FAILURE", required=True,
            missing_behavior="FAIL_CLOSED_BLOCKED", preregistration_required=True,
            rationale="The minimum sample rule is inherited from the pre-existing frozen V1 local classifier.",
        ),
        "local_base_return": _gate(
            "local_base_return", role="HARD_GATE", stage="LOCAL", metric="base_net_return",
            direction="> 0", threshold="> 0", threshold_source="PRE_EXISTING_FROZEN_POLICY",
            failure_code="BASE_RETURN_NONPOSITIVE", failure_category="ALPHA_FAILURE", required=True,
            missing_behavior="FAIL_CLOSED_REJECTED", preregistration_required=True,
            rationale="Preserves the pre-existing V1 local alpha gate without tightening it.",
        ),
        "local_profit_factor": _gate(
            "local_profit_factor", role="HARD_GATE", stage="LOCAL", metric="base_profit_factor",
            direction="NONE_OR_GT_1", threshold="profit_factor is None OR profit_factor > 1.0", threshold_source="PRE_EXISTING_FROZEN_POLICY",
            failure_code="BASE_PROFIT_FACTOR_NOT_SUPPORTED", failure_category="ALPHA_FAILURE", required=True,
            missing_behavior="FAIL_CLOSED_REJECTED", preregistration_required=True,
            rationale="Preserves the pre-existing V1 conditional profit-factor gate.",
        ),
        "raw_bootstrap_support": _gate(
            "raw_bootstrap_support", role="HARD_GATE", stage="LOCAL", metric="raw_bootstrap_p_value",
            direction="< 0.05", threshold="p_value < 0.05", threshold_source="PRE_EXISTING_FROZEN_POLICY",
            failure_code="RAW_BOOTSTRAP_NOT_SUPPORTED", failure_category="SAMPLE_FAILURE", required=True,
            missing_behavior="CLASSIFY_WEAK", preregistration_required=True,
            rationale="Raw bootstrap support remains candidate-local evidence and cannot grant final pass.",
        ),
        "cost_stress_base": _gate(
            "cost_stress_base", role="REPORT_ONLY", stage="LOCAL", metric="BASE_cost_scenario",
            direction="REPORT", threshold=None, threshold_source="NONE",
            failure_code="BASE_COST_SCENARIO_REPORTED", failure_category="NONE", required=False,
            missing_behavior="REPORT_UNKNOWN", preregistration_required=True,
            rationale="BASE is already represented by the local economic gates; this item is a diagnostic record.",
        ),
        "cost_stress_fees_x2": _gate(
            "cost_stress_fees_x2", role="REPORT_ONLY", stage="LOCAL", metric="FEES_X2_cost_scenario",
            direction="REPORT", threshold=None, threshold_source="NONE",
            failure_code="FEES_X2_SCENARIO_REPORTED", failure_category="NONE", required=False,
            missing_behavior="REPORT_UNKNOWN", preregistration_required=True,
            rationale="The frozen scenario is preserved without adding a new economic cutoff.",
        ),
        "cost_stress_slippage_x2": _gate(
            "cost_stress_slippage_x2", role="REPORT_ONLY", stage="LOCAL", metric="SLIPPAGE_X2_cost_scenario",
            direction="REPORT", threshold=None, threshold_source="NONE",
            failure_code="SLIPPAGE_X2_SCENARIO_REPORTED", failure_category="NONE", required=False,
            missing_behavior="REPORT_UNKNOWN", preregistration_required=True,
            rationale="The frozen scenario is preserved without adding a new economic cutoff.",
        ),
        "cost_stress_combined_x2": _gate(
            "cost_stress_combined_x2", role="HARD_GATE", stage="LOCAL", metric="COMBINED_X2.net_return",
            direction="> 0", threshold="COMBINED_X2 net_return > 0", threshold_source="PRE_EXISTING_FROZEN_POLICY",
            failure_code="COMBINED_COST_STRESS_FAILURE", failure_category="ROBUSTNESS_FAILURE", required=True,
            missing_behavior="FAIL_CLOSED_REJECTED", preregistration_required=True,
            rationale="Preserves the already frozen combined-cost viability gate.",
        ),
        "small_capital_execution_feasibility": _gate(
            "small_capital_execution_feasibility", role="HARD_GATE", stage="LOCAL", metric="small_capital_execution_contract",
            direction="ALL_VALID", threshold="cash=10000; max_positions=3; lot=100; no_leverage; no_negative_cash; fees; T+1", threshold_source="PRODUCT_CONSTRAINT",
            failure_code="SMALL_CAPITAL_EXECUTION_INVALID", failure_category="EXECUTION_FAILURE", required=True,
            missing_behavior="FAIL_CLOSED_BLOCKED", preregistration_required=True,
            rationale="This is a product/execution feasibility gate, not a 10K return threshold.",
        ),
        "small_capital_economic_performance": _gate(
            "small_capital_economic_performance", role="SOFT_EVIDENCE", stage="FINAL_ADJUDICATION", metric="small_capital_economic_metrics",
            direction="REPORT_WITH_WARNING", threshold=None, threshold_source="NONE",
            failure_code="SMALL_CAPITAL_DEGRADATION_WARNING", failure_category="NONE", required=False,
            missing_behavior="WARN_ONLY", preregistration_required=True,
            rationale="No outcome-blind economic cutoff for 10K is currently justified.",
        ),
        "baseline_preregistration": _gate(
            "baseline_preregistration", role="HARD_GATE", stage="PRE_PERFORMANCE", metric="baseline_control_registration",
            direction="REGISTERED_BEFORE_PERFORMANCE", threshold="baseline registration exists before performance access", threshold_source="PRE_EXISTING_FROZEN_POLICY",
            failure_code="BASELINE_NOT_PREREGISTERED", failure_category="GOVERNANCE_FAILURE", required=True,
            missing_behavior="FAIL_CLOSED_BLOCKED", preregistration_required=True,
            rationale="Baseline identity and rationale must be frozen before outcome access.",
        ),
        "baseline_result_comparison": _gate(
            "baseline_result_comparison", role="SOFT_EVIDENCE", stage="FINAL_ADJUDICATION", metric="baseline_result_comparison",
            direction="REPORT_WITH_METHOD_STATUS", threshold=None, threshold_source="NONE",
            failure_code="BASELINE_COMPARISON_WARNING", failure_category="NONE", required=False,
            missing_behavior="WARN_ONLY", preregistration_required=True,
            rationale="The registration gate is hard; no retrospective performance superiority cutoff is invented here.",
        ),
        "intended_holding_contract": _gate(
            "intended_holding_contract", role="HARD_GATE", stage="PRE_PERFORMANCE", metric="candidate_intended_holding_sessions",
            direction="2_TO_10_SESSIONS", threshold="2 <= intended holding sessions <= 10", threshold_source="PRODUCT_CONSTRAINT",
            failure_code="INTENDED_HOLDING_OUT_OF_SCOPE", failure_category="GOVERNANCE_FAILURE", required=True,
            missing_behavior="FAIL_CLOSED_BLOCKED", preregistration_required=True,
            rationale="The target product horizon is a product scope constraint.",
        ),
        "realized_execution_delay": _gate(
            "realized_execution_delay", role="REPORT_ONLY", stage="FINAL_ADJUDICATION", metric="realized_holding_delay_reason",
            direction="REPORT_LEGAL_DELAY_SEPARATELY", threshold=None, threshold_source="NONE",
            failure_code="LEGAL_EXECUTION_DELAY", failure_category="NONE", required=False,
            missing_behavior="REPORT_UNKNOWN", preregistration_required=True,
            rationale="Suspension, limit-lock, SELL_PENDING and legal T+1 delays are not alpha failures.",
        ),
        "microstructure_realism": _gate(
            "microstructure_realism", role="HARD_GATE", stage="LOCAL", metric="legal_signal_entry_fill_and_exit_timing",
            direction="ALL_VALID", threshold="available_at; next eligible session; T+1; limit/suspension; no impossible same-bar fill", threshold_source="MARKET_MICROSTRUCTURE_RULE",
            failure_code="MICROSTRUCTURE_INVALID", failure_category="EXECUTION_FAILURE", required=True,
            missing_behavior="FAIL_CLOSED_BLOCKED", preregistration_required=True,
            rationale="Execution realism is a validity condition and is not a performance preference.",
        ),
        "candidate_similarity_control": _gate(
            "candidate_similarity_control", role="HARD_GATE", stage="PRE_PERFORMANCE", metric="candidate_duplicate_and_near_search_check",
            direction="NO_DUPLICATE_OR_UNAUTHORIZED_NEAR_SEARCH", threshold="exact duplicate, same hash, parameter-neighbor and unauthorized near-search rejected", threshold_source="PRE_EXISTING_FROZEN_POLICY",
            failure_code="DUPLICATE_OR_UNAUTHORIZED_NEAR_SEARCH", failure_category="GOVERNANCE_FAILURE", required=True,
            missing_behavior="FAIL_CLOSED_BLOCKED", preregistration_required=True,
            rationale="CandidateSimilarityV1 controls trial consumption before performance access.",
        ),
        "search_budget_reservation": _gate(
            "search_budget_reservation", role="HARD_GATE", stage="PRE_PERFORMANCE", metric="search_budget_and_trial_ledger",
            direction="RESERVED_AND_REGISTERED_BEFORE_PERFORMANCE", threshold="budget reservation and trial ledger entry exist before performance access", threshold_source="PRE_EXISTING_FROZEN_POLICY",
            failure_code="TRIAL_NOT_PREREGISTERED_OR_BUDGET_VIOLATION", failure_category="GOVERNANCE_FAILURE", required=True,
            missing_behavior="FAIL_CLOSED_BLOCKED", preregistration_required=True,
            rationale="Search budget and append-only trial registration are pre-performance governance contracts.",
        ),
        "concentration_diagnostics": _gate(
            "concentration_diagnostics", role="SOFT_EVIDENCE", stage="FINAL_ADJUDICATION", metric="top1_top5_top10_remove_topn_best_month_event_cluster",
            direction="REPORT_QUALITATIVE_WARNING_ONLY", threshold=None, threshold_source="NONE",
            failure_code="CONCENTRATION_WARNING", failure_category="NONE", required=False,
            missing_behavior="WARN_ONLY", preregistration_required=True,
            rationale="No outcome-blind numerical concentration cutoff is currently justified.",
        ),
        "subperiod_robustness": _gate(
            "subperiod_robustness", role="SOFT_EVIDENCE", stage="FINAL_ADJUDICATION", metric="canonical_chronological_subperiods",
            direction="REPORT_CONSISTENCY_STATUS", threshold=None, threshold_source="NONE",
            failure_code="SUBPERIOD_DEPENDENCY_WARNING", failure_category="NONE", required=False,
            missing_behavior="WARN_ONLY", preregistration_required=True,
            rationale="No arbitrary all-period positivity rule is introduced.",
        ),
        "regime_robustness": _gate(
            "regime_robustness", role="SOFT_EVIDENCE", stage="FINAL_ADJUDICATION", metric="bull_bear_sideways_regime_split",
            direction="REPORT_DEPENDENCY_STATUS", threshold=None, threshold_source="NONE",
            failure_code="REGIME_DEPENDENCY_WARNING", failure_category="NONE", required=False,
            missing_behavior="WARN_ONLY", preregistration_required=True,
            rationale="No arbitrary positive-performance-in-every-regime rule is introduced.",
        ),
        "multiple_testing_adjusted_support": _gate(
            "multiple_testing_adjusted_support", role="HARD_GATE", stage="BATCH_INFERENCE", metric="BH_adjusted_support",
            direction="TRUE_FOR_RESEARCH_PASSED", threshold="adjusted_support == true; FDR q=0.05", threshold_source="PRE_EXISTING_FROZEN_POLICY",
            failure_code="MULTIPLE_TESTING_UNSUPPORTED", failure_category="ALPHA_FAILURE", required=True,
            missing_behavior="FAIL_CLOSED_PROMISING_OR_BLOCKED", preregistration_required=True,
            rationale="Final pass requires the frozen BH result inside the frozen decision family; false support yields PROMISING when local evidence passes.",
        ),
        "raw_evidence_artifact_completeness": _gate(
            "raw_evidence_artifact_completeness", role="REPORT_ONLY", stage="FINAL_ADJUDICATION", metric="required_evidence_artifact_manifest",
            direction="REPORT_COMPLETENESS", threshold=None, threshold_source="NONE",
            failure_code="EVIDENCE_ARTIFACT_COMPLETENESS_REPORTED", failure_category="NONE", required=False,
            missing_behavior="REPORT_UNKNOWN", preregistration_required=True,
            rationale="Artifact completeness is reported separately from alpha classification.",
        ),
    }


def _default_multiple_testing_contract() -> dict[str, Any]:
    return {
        "method": "BENJAMINI_HOCHBERG",
        "fdr_q": 0.05,
        "q_source": "PRE_EXISTING_FROZEN_POLICY",
        "decision_family_id_template": "{objective_id}:{batch_id}:VALIDATION_DECISION_FAMILY_V2",
        "family_scope": "policy_activation_epoch",
        "family_composition_rules": [
            "include every preregistered candidate/control trial created on or after effective_from with the same policy_id and policy_hash",
            "include only finite p-values from valid predictive trials in the frozen family snapshot",
            "freeze family membership and family snapshot hash before performance access",
        ],
        "history_inclusion_rules": [
            "exclude all V1 and pre-effective_from historical trials from the V2 denominator",
            "include prior V2 valid predictive trials only when the immutable policy hash matches",
            "historical classifications and trial records are read-only and never reclassified by V2",
        ],
        "legal_denominator": "count of finite p-values for frozen family members after invalidated-trial exclusion",
        "invalidated_trial_exclusion": "exclude engine, data, PIT, governance and execution-invalidated trials; preserve their lineage outside the denominator",
        "duplicate_trial_handling": "exact duplicate or same candidate hash is rejected before performance and never enters the denominator",
        "parameter_neighbor_handling": "unauthorized parameter-neighbor reuse is rejected before performance and never enters the denominator",
        "control_handling": "meaningful preregistered controls use an explicit CONTROL role and the same family contract; meaningless controls are not forced",
        "cumulative_history_handling": "carry forward only the frozen V2 family snapshot; no post-hoc denominator expansion or removal",
        "denominator_immutable": True,
        "preregistration_required": True,
    }


def _default_classification_contract() -> dict[str, Any]:
    return {
        "RESEARCH_PASSED": {
            "requires": ["all_pre_performance_hard_gates", "all_local_hard_gates", "adjusted_support_true", "all_final_hard_gates"],
            "soft_warning_allowed": True,
            "failure_entry": False,
        },
        "PROMISING": {
            "requires": ["all_validity_and_governance_hard_gates", "local_pass", "adjusted_support_false"],
            "soft_warning_allowed": True,
            "failure_entry": False,
        },
        "WEAK": {
            "requires": ["valid_trial", "local_evidence_below_local_support"],
            "soft_warning_allowed": True,
            "failure_entry": True,
        },
        "REJECTED": {
            "requires": ["valid_trial", "frozen_local_alpha_or_robustness_hard_gate_failed"],
            "soft_warning_allowed": True,
            "failure_entry": True,
        },
        "BLOCKED": {
            "requires": ["data_pit_execution_governance_sample_or_contract_invalidity"],
            "soft_warning_allowed": False,
            "failure_entry": True,
        },
        "ENGINEERING_BLOCKED": {
            "requires": ["engine_invariant_failed"],
            "soft_warning_allowed": False,
            "failure_entry": True,
        },
    }


@dataclass(frozen=True)
class ValidationDecisionPolicyV2:
    """Immutable, outcome-blind validation decision policy."""

    policy_id: str = POLICY_ID
    schema_version: str = SCHEMA_VERSION
    policy_version: str = POLICY_VERSION
    created_at: str = "2026-08-24T00:00:00+08:00"
    frozen_at: str = "2026-08-24T00:00:00+08:00"
    effective_from: str = "2026-08-24T00:00:00+08:00"
    policy_hash: str = ""
    parent_policy_id: str = PARENT_POLICY_ID
    supersedes: tuple[str, ...] = ("VALIDATION_DECISION_POLICY_V2_DRAFT",)
    research_objective_scope: str = "RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1"
    universe_scope: tuple[str, ...] = ("SH", "SZ")
    holding_horizon_scope: tuple[int, int] = (2, 10)
    capital_scope: Mapping[str, Any] = field(default_factory=lambda: {"reference_cash": 10000.0, "max_positions": 3, "lot_size": 100})
    engine_contract_version: str = ENGINE_CONTRACT_VERSION
    multiple_testing_contract_version: str = MULTIPLE_TESTING_CONTRACT_VERSION
    policy_status: str = "FROZEN_ACTIVE"
    eligible_for_future_research: bool = True
    policy_ready_for_autonomous_readiness_review: str = "YES"
    autonomous_research_enabled: bool = False
    research_start: int = 20220801
    research_end: int = 20250731
    subperiods: tuple[dict[str, Any], ...] = (
        {"name": "P1", "start": 20220801, "end": 20230731},
        {"name": "P2", "start": 20230801, "end": 20240731},
        {"name": "P3", "start": 20240801, "end": 20250731},
    )
    regime_definition: str = "market_state_v1_pit_asof"
    transaction_model: str = "BacktestEngineV2_DailyBarFillModel"
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    slippage_bps: float = 0.001
    max_participation_rate: float = 0.10
    initial_cash: float = 1_000_000.0
    max_positions: int = 3
    lot_size: int = 100
    small_capital_cash: float = 10_000.0
    small_capital_slots: int = 3
    small_capital_lot_size: int = 100
    bootstrap_iterations: int = 10_000
    bootstrap_seed: int = 20260823
    fdr_method: str = "BENJAMINI_HOCHBERG"
    fdr_q: float = 0.05
    no_final_test_access: bool = True
    no_parameter_optimization: bool = True
    no_threshold_changes: bool = True
    no_historical_forward: bool = True
    no_paper: bool = True
    no_recommendation: bool = True
    no_broker: bool = True
    gates: Mapping[str, Mapping[str, Any]] = field(default_factory=_default_gates)
    multiple_testing_contract: Mapping[str, Any] = field(default_factory=_default_multiple_testing_contract)
    classification_contract: Mapping[str, Any] = field(default_factory=_default_classification_contract)
    failure_taxonomy: Mapping[str, Any] = field(default_factory=lambda: {
        "ALPHA_FAILURE": ["local_base_return", "local_profit_factor"],
        "ROBUSTNESS_FAILURE": ["cost_stress_combined_x2"],
        "SAMPLE_FAILURE": ["sample_adequacy", "raw_bootstrap_support"],
        "DATA_FAILURE": ["data_validity"],
        "PIT_FAILURE": ["pit_validity"],
        "EXECUTION_FAILURE": ["small_capital_execution_feasibility", "microstructure_realism"],
        "GOVERNANCE_FAILURE": ["baseline_preregistration", "intended_holding_contract", "candidate_similarity_control", "search_budget_reservation"],
        "ENGINE_FAILURE": ["engine_integrity"],
    })
    warning_codes: tuple[str, ...] = (
        "CONCENTRATION_WARNING",
        "REGIME_DEPENDENCY_WARNING",
        "SUBPERIOD_DEPENDENCY_WARNING",
        "SMALL_CAPITAL_DEGRADATION_WARNING",
        "BASELINE_COMPARISON_WARNING",
        "LOW_EVENT_DIVERSITY_WARNING",
    )
    final_test_access: Mapping[str, int] = field(default_factory=lambda: {"physical": 0, "analytical": 0, "decision": 0})
    restrictions: Mapping[str, Any] = field(default_factory=lambda: {
        "historical_reclassification": False,
        "new_performance_trials_during_policy_design": 0,
        "batch2_executed": False,
        "prospective_executed": False,
        "recommendation": "DISABLED",
        "real_order_execution": "DISABLED",
        "no_policy_hot_reload": True,
    })
    outcome_derived_thresholds: int = 0

    def _payload_without_hash(self) -> dict[str, Any]:
        payload = {
            "policy_id": self.policy_id,
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "created_at": self.created_at,
            "frozen_at": self.frozen_at,
            "effective_from": self.effective_from,
            "parent_policy_id": self.parent_policy_id,
            "supersedes": list(self.supersedes),
            "research_objective_scope": self.research_objective_scope,
            "universe_scope": list(self.universe_scope),
            "holding_horizon_scope": list(self.holding_horizon_scope),
            "capital_scope": _copy(self.capital_scope),
            "engine_contract_version": self.engine_contract_version,
            "multiple_testing_contract_version": self.multiple_testing_contract_version,
            "policy_status": self.policy_status,
            "eligible_for_future_research": self.eligible_for_future_research,
            "policy_ready_for_autonomous_readiness_review": self.policy_ready_for_autonomous_readiness_review,
            "autonomous_research_enabled": self.autonomous_research_enabled,
            "research_start": self.research_start,
            "research_end": self.research_end,
            "subperiods": _copy(self.subperiods),
            "regime_definition": self.regime_definition,
            "transaction_model": self.transaction_model,
            "commission_rate": self.commission_rate,
            "min_commission": self.min_commission,
            "stamp_tax_rate": self.stamp_tax_rate,
            "slippage_bps": self.slippage_bps,
            "max_participation_rate": self.max_participation_rate,
            "initial_cash": self.initial_cash,
            "max_positions": self.max_positions,
            "lot_size": self.lot_size,
            "small_capital_cash": self.small_capital_cash,
            "small_capital_slots": self.small_capital_slots,
            "small_capital_lot_size": self.small_capital_lot_size,
            "bootstrap_iterations": self.bootstrap_iterations,
            "bootstrap_seed": self.bootstrap_seed,
            "fdr_method": self.fdr_method,
            "fdr_q": self.fdr_q,
            "no_final_test_access": self.no_final_test_access,
            "no_parameter_optimization": self.no_parameter_optimization,
            "no_threshold_changes": self.no_threshold_changes,
            "no_historical_forward": self.no_historical_forward,
            "no_paper": self.no_paper,
            "no_recommendation": self.no_recommendation,
            "no_broker": self.no_broker,
            "gates": _copy(self.gates),
            "multiple_testing_contract": _copy(self.multiple_testing_contract),
            "classification_contract": _copy(self.classification_contract),
            "failure_taxonomy": _copy(self.failure_taxonomy),
            "warning_codes": list(self.warning_codes),
            "final_test_access": _copy(self.final_test_access),
            "restrictions": _copy(self.restrictions),
            "outcome_derived_thresholds": self.outcome_derived_thresholds,
        }
        return payload

    def computed_hash(self) -> str:
        return stable_hash(self._payload_without_hash())

    def to_dict(self) -> dict[str, Any]:
        return self._payload_without_hash() | {"policy_hash": self.policy_hash or self.computed_hash()}

    @property
    def hard_gates(self) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in self.gates.items() if value.get("role") == "HARD_GATE"}

    @property
    def soft_evidence(self) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in self.gates.items() if value.get("role") == "SOFT_EVIDENCE"}

    @property
    def report_only(self) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in self.gates.items() if value.get("role") == "REPORT_ONLY"}

    @property
    def multiple_testing_contract_hash(self) -> str:
        return stable_hash(self.multiple_testing_contract)

    def validate(self) -> None:
        if self.policy_id != POLICY_ID or self.schema_version != SCHEMA_VERSION or self.policy_version != POLICY_VERSION:
            raise ValidationPolicyV2Error("unsupported ValidationDecisionPolicyV2 identity")
        if tuple(self.universe_scope) != ("SH", "SZ"):
            raise ValidationPolicyV2Error("V2 universe must be SH + SZ; BJ is out of scope")
        if tuple(self.holding_horizon_scope) != (2, 10) or self.small_capital_cash != 10000.0 or self.small_capital_slots != 3 or self.small_capital_lot_size != 100:
            raise ValidationPolicyV2Error("product constraints changed")
        if self.research_end != 20250731 or self.no_final_test_access is not True:
            raise ValidationPolicyV2Error("research boundary or Final Test seal changed")
        if self.bootstrap_iterations != 10_000 or self.fdr_method != "BENJAMINI_HOCHBERG" or not 0 < float(self.fdr_q) < 1:
            raise ValidationPolicyV2Error("statistical method contract is invalid")
        if self.outcome_derived_thresholds != 0:
            raise ValidationPolicyV2Error("outcome-derived thresholds are forbidden")
        if any(int(value) != 0 for value in self.final_test_access.values()) or self.autonomous_research_enabled:
            raise ValidationPolicyV2Error("Final Test or autonomous execution is enabled")
        evidence_ids = [str(item.get("evidence_id")) for item in self.gates.values()]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValidationPolicyV2Error("duplicate gate ids")
        if self.policy_hash and self.policy_hash != self.computed_hash():
            raise ValidationPolicyV2Error("policy hash mismatch")
        for gate_id, gate in self.gates.items():
            required = {"evidence_id", "role", "stage", "metric", "direction", "threshold", "threshold_source", "failure_code", "failure_category", "required", "missing_behavior", "preregistration_required"}
            missing = required - set(gate)
            if missing:
                raise ValidationPolicyV2Error(f"gate {gate_id} missing fields: {sorted(missing)}")
            if gate_id != gate["evidence_id"] or gate["role"] not in ROLES or gate["stage"] not in STAGES:
                raise ValidationPolicyV2Error(f"invalid evidence identity/role/stage: {gate_id}")
            source = str(gate["threshold_source"])
            if source not in THRESHOLD_SOURCES:
                raise ValidationPolicyV2Error(f"invalid threshold source for {gate_id}: {source}")
            threshold = gate["threshold"]
            if gate["role"] == "HARD_GATE":
                if source in {"NONE"} or threshold in (None, "", "NONE", "THRESHOLD_UNRESOLVED"):
                    raise ValidationPolicyV2Error(f"unresolved HARD_GATE threshold: {gate_id}")
            elif gate["role"] == "REPORT_ONLY" and threshold not in (None, "", "NONE"):
                raise ValidationPolicyV2Error(f"REPORT_ONLY gate cannot control classification: {gate_id}")
        missing_hard = set(REQUIRED_HARD_GATE_IDS) - set(self.hard_gates)
        if missing_hard:
            raise ValidationPolicyV2Error(f"required hard gates missing: {sorted(missing_hard)}")
        contract = self.multiple_testing_contract
        if contract.get("method") != "BENJAMINI_HOCHBERG" or float(contract.get("fdr_q", -1)) != float(self.fdr_q):
            raise ValidationPolicyV2Error("multiple-testing contract does not match policy FDR")
        for key in ("decision_family_id_template", "family_composition_rules", "history_inclusion_rules", "legal_denominator", "invalidated_trial_exclusion", "duplicate_trial_handling", "parameter_neighbor_handling", "control_handling", "cumulative_history_handling"):
            if not contract.get(key):
                raise ValidationPolicyV2Error(f"multiple-testing contract missing {key}")
        if set(self.classification_contract) != CLASSIFICATIONS:
            raise ValidationPolicyV2Error("classification contract is incomplete or contradictory")
        if "adjusted_support_true" not in self.classification_contract["RESEARCH_PASSED"].get("requires", ()) or "adjusted_support_false" not in self.classification_contract["PROMISING"].get("requires", ()):
            raise ValidationPolicyV2Error("classification contract contradicts BH final-pass semantics")
        if self.policy_status == "FROZEN_ACTIVE":
            if not self.eligible_for_future_research or self.policy_hash != self.computed_hash():
                raise ValidationPolicyV2Error("active policy is not hash-complete or not future-eligible")
        elif self.policy_status != "DRAFT_BLOCKED":
            raise ValidationPolicyV2Error("policy status must be FROZEN_ACTIVE or DRAFT_BLOCKED")

    def assert_active(self) -> None:
        self.validate()
        if self.policy_status != "FROZEN_ACTIVE":
            raise ValidationPolicyV2Error("ValidationDecisionPolicyV2 is not active")

    def family_id(self, objective_id: str, batch_id: str) -> str:
        return f"{objective_id}:{batch_id}:VALIDATION_DECISION_FAMILY_V2"

    def classify(self, *, local_classification: str, adjusted_support: bool, evidence: Mapping[str, Any], multiple_testing: Mapping[str, Any]) -> dict[str, Any]:
        """Evaluate synthetic or future evidence without reading result artifacts."""
        gates = evidence.get("gates", {}) if isinstance(evidence, Mapping) else {}
        failures: list[dict[str, Any]] = []
        for gate_id, contract in self.hard_gates.items():
            if gate_id == "multiple_testing_adjusted_support":
                continue
            value = gates.get(gate_id) if isinstance(gates, Mapping) else None
            passed = value.get("passed") if isinstance(value, Mapping) else value
            if passed is not True:
                failures.append({
                    "evidence_id": gate_id,
                    "failure_code": contract["failure_code"] if passed is False else f"MISSING_{contract['failure_code']}",
                    "failure_category": contract["failure_category"],
                })
        family_contract_hash = multiple_testing.get("family_contract_hash")
        if family_contract_hash is not None and family_contract_hash != self.multiple_testing_contract_hash:
            failures.append({"evidence_id": "multiple_testing_family", "failure_code": "MULTIPLE_TESTING_FAMILY_CONTRACT_MISMATCH", "failure_category": "GOVERNANCE_FAILURE"})
        if str(multiple_testing.get("method")) != self.fdr_method or float(multiple_testing.get("q", -1)) != float(self.fdr_q):
            failures.append({"evidence_id": "multiple_testing", "failure_code": "MULTIPLE_TESTING_CONTRACT_MISMATCH", "failure_category": "GOVERNANCE_FAILURE"})
        if int(multiple_testing.get("hypothesis_count", 0)) <= 0 or int(multiple_testing.get("decision_denominator", multiple_testing.get("hypothesis_count", 0))) != int(multiple_testing.get("hypothesis_count", 0)):
            failures.append({"evidence_id": "multiple_testing", "failure_code": "INVALID_DECISION_DENOMINATOR", "failure_category": "GOVERNANCE_FAILURE"})
        engine_failed = any(item["failure_category"] == "ENGINE_FAILURE" for item in failures)
        if engine_failed:
            effective = "ENGINEERING_BLOCKED"
            reason = "ENGINE_INTEGRITY_HARD_GATE_FAILED"
            category = "ENGINE_FAILURE"
        else:
            blocking_categories = {"DATA_FAILURE", "PIT_FAILURE", "EXECUTION_FAILURE", "GOVERNANCE_FAILURE", "SAMPLE_FAILURE"}
            blocking = [item for item in failures if item["failure_category"] in blocking_categories]
            alpha_or_robustness = [item for item in failures if item["failure_category"] in {"ALPHA_FAILURE", "ROBUSTNESS_FAILURE"}]
            bootstrap_failed = any(item["evidence_id"] == "raw_bootstrap_support" for item in failures)
            if blocking:
                effective = "BLOCKED"
                reason = blocking[0]["failure_code"]
                category = blocking[0]["failure_category"]
            elif alpha_or_robustness:
                effective = "REJECTED"
                reason = alpha_or_robustness[0]["failure_code"]
                category = alpha_or_robustness[0]["failure_category"]
            elif str(local_classification).upper() in {"WEAK", "INSUFFICIENT_EVIDENCE"} or bootstrap_failed:
                effective = "WEAK"
                reason = "LOCAL_EVIDENCE_NOT_STATISTICALLY_SUPPORTED"
                category = "SAMPLE_FAILURE"
            elif str(local_classification).upper() == "REJECTED":
                effective = "REJECTED"
                reason = "LOCAL_ALPHA_OR_COST_GATE_FAILED"
                category = "ALPHA_FAILURE"
            elif bool(adjusted_support):
                effective = "RESEARCH_PASSED"
                reason = "LOCAL_PASS_AND_BH_ADJUSTED_SUPPORT"
                category = None
            else:
                effective = "PROMISING"
                reason = "LOCAL_PASS_BUT_BH_ADJUSTED_SUPPORT_FALSE"
                category = None
        soft = evidence.get("soft_evidence", {}) if isinstance(evidence, Mapping) else {}
        warnings: list[str] = []
        for gate_id, value in (soft.items() if isinstance(soft, Mapping) else ()):
            if isinstance(value, Mapping):
                warning = value.get("warning")
                if warning:
                    warnings.append(str(warning))
        return {
            "effective_classification": effective,
            "reason": reason,
            "failure_category": category,
            "hard_gate_failures": tuple(item["evidence_id"] for item in failures),
            "failure_codes": tuple(item["failure_code"] for item in failures),
            "warnings": tuple(sorted(set(warnings))),
            "concentration_evidence_status": str((soft.get("concentration_diagnostics") or {}).get("status", "NOT_PROVIDED")) if isinstance(soft, Mapping) else "NOT_PROVIDED",
            "concentration_metrics_ref": str((soft.get("concentration_diagnostics") or {}).get("metrics_ref", "")) if isinstance(soft, Mapping) else "",
            "concentration_warning": str((soft.get("concentration_diagnostics") or {}).get("warning", "NONE")) if isinstance(soft, Mapping) else "NONE",
            "subperiod_consistency_status": str((soft.get("subperiod_robustness") or {}).get("status", "NOT_PROVIDED")) if isinstance(soft, Mapping) else "NOT_PROVIDED",
            "regime_dependency_warning": str((soft.get("regime_robustness") or {}).get("warning", "NONE")) if isinstance(soft, Mapping) else "NONE",
            "baseline_comparison_warning": str((soft.get("baseline_result_comparison") or {}).get("warning", "NONE")) if isinstance(soft, Mapping) else "NONE",
        }

    @classmethod
    def default(cls) -> "ValidationDecisionPolicyV2":
        draft = cls()
        return replace(draft, policy_hash=draft.computed_hash())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ValidationDecisionPolicyV2":
        payload = value.get("policy", value) if isinstance(value, Mapping) else value
        if not isinstance(payload, Mapping):
            raise ValidationPolicyV2Error("policy payload must be an object")
        raw = dict(payload)
        for key in ("universe_scope", "holding_horizon_scope", "supersedes", "warning_codes"):
            if key in raw:
                raw[key] = tuple(raw[key])
        if "subperiods" in raw:
            raw["subperiods"] = tuple(dict(item) for item in raw["subperiods"])
        return cls(**raw)


def default_validation_decision_policy_v2() -> ValidationDecisionPolicyV2:
    return ValidationDecisionPolicyV2.default()


def policy_payload(policy: ValidationDecisionPolicyV2) -> dict[str, Any]:
    policy.assert_active()
    return {
        "schema_version": SCHEMA_VERSION,
        "policy": policy.to_dict(),
        "policy_hash": policy.policy_hash,
        "freeze_status": "FROZEN_ACTIVE",
        "performance_data_used": False,
        "effective_from": policy.effective_from,
    }


def lock_payload(policy: ValidationDecisionPolicyV2, policy_file: Path) -> dict[str, Any]:
    policy.assert_active()
    return {
        "schema_version": "validation-decision-policy-v2-lock",
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "policy_hash": policy.policy_hash,
        "policy_file_sha256": sha256_file(policy_file),
        "immutable": True,
        "no_policy_hot_reload": True,
        "effective_from": policy.effective_from,
        "frozen_at": policy.frozen_at,
    }


def load_validation_decision_policy_v2(policy_path: str | Path, lock_path: str | Path | None = None, *, require_active: bool = True) -> tuple[ValidationDecisionPolicyV2, str]:
    path = Path(policy_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    policy = ValidationDecisionPolicyV2.from_dict(payload)
    declared = str(payload.get("policy_hash") or policy.policy_hash)
    if policy.policy_hash != declared or declared != policy.computed_hash():
        raise ValidationPolicyV2Error("ValidationDecisionPolicyV2 policy hash mismatch")
    policy.validate()
    if require_active:
        policy.assert_active()
    lock = Path(lock_path) if lock_path else path.with_name("validation_decision_policy_v2.lock.json")
    if not lock.exists():
        raise ValidationPolicyV2Error("ValidationDecisionPolicyV2 lock file is missing")
    lock_payload_value = json.loads(lock.read_text(encoding="utf-8"))
    if lock_payload_value.get("policy_hash") != declared or lock_payload_value.get("policy_file_sha256") != sha256_file(path) or lock_payload_value.get("immutable") is not True:
        raise ValidationPolicyV2Error("ValidationDecisionPolicyV2 lock mismatch")
    return policy, declared
