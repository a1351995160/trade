"""Real Batch 1 runtime for the AI Research Factory.

This adapter deliberately keeps design and validation in separate phases.  It
uses the existing hypothesis builder, semantic compiler, research data router,
corrected BacktestEngineV2 runner, and PortfolioExitEvaluatorV1; it does not
open any prospective or Final Test path.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .batch import ResearchBatchPlanV1
from .common import jsonable, now_timestamp, stable_hash
from .context import NoOutcomeResearchContextV1, PerformanceBlindGuard
from .failure_adapter import FailureKnowledgeSnapshotV1
from .history import CumulativeResearchHistoryV1, LEGAL_CLASSIFICATIONS
from .objective import ResearchObjectiveV1
from .state_machine import ResearchBatchState, ResearchBatchStateMachineV1
from .status import ResearchFactoryStatusV1
from .strategy_adapter import candidate_similarity
from .execution_evidence import audit_microstructure
from .novelty import CandidateNoveltyGateV2, design_safe_candidate
from .diversity import InsufficientDiverseCandidatesError
from .source_dependencies import SOURCE_ROOT, load_corrected_module


OBJECTIVE_ID = "RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1"
PASSED_CANDIDATE_ID = "CAND_EVENT_REVERSAL_SENTIMENT_EVENT_EXHAUSTION_WITH_P_2A6052ED_V1_V3"
MAX_HYPOTHESES = 12
MAX_FROZEN_CANDIDATES = 8
MAX_REAL_TRIALS = 8

FROZEN_MECHANISMS = (
    "slow_trend_alignment",
    "relative_strength_acceleration",
    "breakout_volume_release",
    "volatility_expansion_participation",
    "volume_price_participation",
    "reversal_gap_confirmation",
    "sentiment_event_continuation",
    "failed_limit_exhaustion",
)


def _template(
    hypothesis_type: str,
    title: str,
    mechanism: str,
    factors: Sequence[str],
    roles: Sequence[Mapping[str, str]],
    directions: Mapping[str, str],
    *,
    horizon: str = "3_5D",
    regime: Sequence[str] = ("NON_CRASH_OR_EXPLICIT_REGIME",),
    events: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "hypothesis_type": hypothesis_type,
        "title": title,
        "description": title,
        "mechanism": mechanism,
        "factor_ids": list(factors),
        "factor_roles": [dict(item) for item in roles],
        "expected_factor_direction": dict(directions),
        "event_dependencies": list(events),
        "target_horizon": horizon,
        "expected_holding_period": horizon,
        "market_regime": list(regime),
        "generator_mode": "MECHANISM_DRIVEN",
        "parameter_ranges": {"holding_period": horizon},
    }


REAL_BATCH_TEMPLATES: tuple[dict[str, Any], ...] = (
    _template(
        "TREND_CONTINUATION", "Slow trend alignment with intermediate slope", "slow_trend_alignment",
        ("MA_DISTANCE_60", "MA_SLOPE_20"),
        ({"factor_id": "MA_DISTANCE_60", "role": "PRIMARY_ALPHA"}, {"factor_id": "MA_SLOPE_20", "role": "CONFIRMATION"}),
        {"MA_DISTANCE_60": "POSITIVE", "MA_SLOPE_20": "POSITIVE"}, horizon="5_10D",
    ),
    _template(
        "TREND_CONTINUATION", "Slope-led trend alignment with slow distance", "slope_led_trend_alignment",
        ("MA_SLOPE_20", "MA_DISTANCE_60"),
        ({"factor_id": "MA_SLOPE_20", "role": "PRIMARY_ALPHA"}, {"factor_id": "MA_DISTANCE_60", "role": "CONFIRMATION"}),
        {"MA_SLOPE_20": "POSITIVE", "MA_DISTANCE_60": "POSITIVE"}, horizon="5_10D",
    ),
    _template(
        "RELATIVE_STRENGTH", "Long horizon relative strength with acceleration", "relative_strength_acceleration",
        ("RETURN_60D", "MOM_ACCEL_5_20"),
        ({"factor_id": "RETURN_60D", "role": "PRIMARY_ALPHA"}, {"factor_id": "MOM_ACCEL_5_20", "role": "CONFIRMATION"}),
        {"RETURN_60D": "POSITIVE", "MOM_ACCEL_5_20": "POSITIVE"}, horizon="5_10D",
    ),
    _template(
        "MOMENTUM", "Momentum with volume participation", "momentum_participation",
        ("RETURN_60D", "VOLUME_RATIO_5_20"),
        ({"factor_id": "RETURN_60D", "role": "PRIMARY_ALPHA"}, {"factor_id": "VOLUME_RATIO_5_20", "role": "CONFIRMATION"}),
        {"RETURN_60D": "POSITIVE", "VOLUME_RATIO_5_20": "POSITIVE"}, horizon="5_10D",
    ),
    _template(
        "BREAKOUT", "Donchian release with short volume confirmation", "breakout_volume_release",
        ("DONCHIAN_POSITION_20", "VOLUME_RATIO_1_20"),
        ({"factor_id": "DONCHIAN_POSITION_20", "role": "PRIMARY_ALPHA"}, {"factor_id": "VOLUME_RATIO_1_20", "role": "CONFIRMATION"}),
        {"DONCHIAN_POSITION_20": "POSITIVE", "VOLUME_RATIO_1_20": "POSITIVE"},
    ),
    _template(
        "BREAKOUT", "Donchian release with acceleration confirmation", "breakout_acceleration",
        ("DONCHIAN_POSITION_20", "VOLUME_ACCEL"),
        ({"factor_id": "DONCHIAN_POSITION_20", "role": "PRIMARY_ALPHA"}, {"factor_id": "VOLUME_ACCEL", "role": "CONFIRMATION"}),
        {"DONCHIAN_POSITION_20": "POSITIVE", "VOLUME_ACCEL": "POSITIVE"},
    ),
    _template(
        "VOLATILITY_EXPANSION", "Volatility expansion with volume acceleration", "volatility_expansion_participation",
        ("VOL_RATIO_5_20", "VOLUME_ACCEL"),
        ({"factor_id": "VOL_RATIO_5_20", "role": "PRIMARY_ALPHA"}, {"factor_id": "VOLUME_ACCEL", "role": "CONFIRMATION"}),
        {"VOL_RATIO_5_20": "POSITIVE", "VOLUME_ACCEL": "POSITIVE"}, horizon="1_3D",
    ),
    _template(
        "VOLUME_PRICE_CONFIRMATION", "Price volume correlation with participation", "volume_price_participation",
        ("PRICE_VOLUME_CORR", "VOLUME_RATIO_5_20"),
        ({"factor_id": "PRICE_VOLUME_CORR", "role": "PRIMARY_ALPHA"}, {"factor_id": "VOLUME_RATIO_5_20", "role": "CONFIRMATION"}),
        {"PRICE_VOLUME_CORR": "POSITIVE", "VOLUME_RATIO_5_20": "POSITIVE"},
    ),
    _template(
        "VOLUME_PRICE_CONFIRMATION", "Price volume correlation with acceleration", "volume_price_acceleration",
        ("PRICE_VOLUME_CORR", "VOLUME_ACCEL"),
        ({"factor_id": "PRICE_VOLUME_CORR", "role": "PRIMARY_ALPHA"}, {"factor_id": "VOLUME_ACCEL", "role": "CONFIRMATION"}),
        {"PRICE_VOLUME_CORR": "POSITIVE", "VOLUME_ACCEL": "POSITIVE"},
    ),
    _template(
        "SHORT_TERM_REVERSAL", "Short horizon reversal with gap confirmation", "reversal_gap_confirmation",
        ("RETURN_3D", "GAP_SIZE"),
        ({"factor_id": "RETURN_3D", "role": "PRIMARY_ALPHA"}, {"factor_id": "GAP_SIZE", "role": "CONFIRMATION"}),
        {"RETURN_3D": "NEGATIVE", "GAP_SIZE": "POSITIVE"}, horizon="1_3D", regime=("NON_CRASH",),
    ),
    _template(
        "EVENT_CONTINUATION", "Sentiment event continuation with liquidity", "sentiment_event_continuation",
        ("VOLUME_RATIO_1_20",),
        ({"factor_id": "VOLUME_RATIO_1_20", "role": "PRIMARY_ALPHA"},),
        {"VOLUME_RATIO_1_20": "POSITIVE"}, horizon="1_3D", regime=("NON_CRASH",), events=("E_LIMITUP_SENT",),
    ),
    _template(
        "EVENT_REVERSAL", "Failed limit exhaustion with short return", "failed_limit_exhaustion",
        ("RETURN_3D",),
        ({"factor_id": "RETURN_3D", "role": "PRIMARY_ALPHA"},),
        {"RETURN_3D": "NEGATIVE"}, horizon="1_3D", regime=("NON_CRASH",), events=("E_FAILEDLIMIT",),
    ),
)


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _existing_candidate_metadata(root: Path) -> list[dict[str, Any]]:
    path = root / "data/research/strategy_candidate_registry/registry.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    lineage_path = root / "data/research/strategy_candidate_registry/registry_v3_lineage.json"
    lineage = json.loads(lineage_path.read_text(encoding="utf-8")) if lineage_path.exists() else {"records": []}
    lineage_by_id = {str(item["candidate_id"]): item for item in lineage.get("records", [])}
    result: list[dict[str, Any]] = []
    for item in payload.get("candidates", []):
        candidate_id = str(item["candidate_id"])
        factor_ids = [str(binding["factor_id"]) for binding in item.get("factor_bindings", ())]
        metadata = {
            "candidate_id": candidate_id,
            "candidate_hash": str(item.get("candidate_hash") or item.get("candidate_preregistration_hash") or ""),
            "family_id": str(item.get("mechanism", "UNKNOWN")),
            "mechanism": str(item.get("mechanism", "UNKNOWN")),
            "holding_period_days": int(item.get("holding_period", 0)),
            "factor_ids": factor_ids,
            "strategy_family": item.get("strategy_family"),
            "event_ids": list(item.get("signal_logic", {}).get("event_dependencies", ())),
            "semantic_fingerprint": None,
            "source": "STRATEGY_CANDIDATE_REGISTRY_V1",
        }
        result.append(metadata)
        alias = candidate_id.rsplit("_V1", 1)[0] + "_V1_V3"
        v3 = dict(metadata)
        v3["candidate_id"] = alias
        v3["source"] = "STRATEGY_CANDIDATE_REGISTRY_V3_LINEAGE"
        v3["semantic_fingerprint"] = lineage_by_id.get(alias, {}).get("new_semantic_fingerprint")
        result.append(v3)
    effective_path = root / "reports/CURRENT_EFFECTIVE_STRATEGY_REGISTRY_V1.json"
    if effective_path.exists():
        effective = json.loads(effective_path.read_text(encoding="utf-8"))
        for item in effective.get("records", ()):
            candidate_id = str(item.get("candidate_id") or "")
            if not candidate_id:
                continue
            result.append({
                "candidate_id": candidate_id,
                "candidate_hash": str(item.get("candidate_hash") or ""),
                "family_id": str(item.get("family_id") or "UNKNOWN"),
                "mechanism": str(item.get("mechanism") or item.get("family_id") or "UNKNOWN"),
                "holding_period_days": int(item.get("holding_period_days") or 0),
                "factor_ids": list(item.get("factor_ids", ())),
                "event_ids": list(item.get("event_ids", ())),
                "source": "CURRENT_EFFECTIVE_STRATEGY_REGISTRY_V1",
            })

    def append_design_history(item: Mapping[str, Any], source: str) -> None:
        candidate_id = str(item.get("candidate_id") or "")
        if not candidate_id:
            return
        result.append({
            "candidate_id": candidate_id,
            "candidate_hash": str(item.get("candidate_hash") or item.get("candidate_preregistration_hash") or ""),
            "family_id": str(item.get("family_id") or item.get("strategy_family") or item.get("mechanism") or "UNKNOWN"),
            "mechanism": str(item.get("mechanism") or item.get("family_id") or "UNKNOWN"),
            "holding_period_days": int(item.get("holding_period_days") or item.get("holding_period") or 0),
            "factor_ids": [str(value) for value in item.get("factor_ids", ())],
            "strategy_family": item.get("strategy_family"),
            "event_ids": [str(value) for value in item.get("event_ids", item.get("event_dependencies", ()))],
            "semantic_fingerprint": item.get("semantic_fingerprint"),
            "parameter_fingerprint": item.get("parameter_fingerprint"),
            "source": source,
        })

    v2_iterations_path = root / "reports/AUTONOMOUS_CODEX_RESEARCH_LOOP_V2_ITERATIONS.json"
    if v2_iterations_path.exists():
        v2_iterations = json.loads(v2_iterations_path.read_text(encoding="utf-8"))
        for iteration in v2_iterations.get("iterations", ()):
            for item in iteration.get("candidate_rows", ()):
                append_design_history(item, "AUTONOMOUS_RESEARCH_V2_ITERATIONS")
    v2_novelty_path = root / "reports/AUTONOMOUS_CODEX_RESEARCH_LOOP_V2_NOVELTY_HISTORY.json"
    if v2_novelty_path.exists():
        v2_novelty = json.loads(v2_novelty_path.read_text(encoding="utf-8"))
        for item in v2_novelty.get("entries", ()):
            append_design_history(item, "AUTONOMOUS_RESEARCH_V2_NOVELTY_HISTORY")
    v3_pending_path = root / "reports/AUTONOMOUS_RESEARCH_V3_PENDING_FULL_WINDOW_SET.json"
    if v3_pending_path.exists():
        v3_pending = json.loads(v3_pending_path.read_text(encoding="utf-8"))
        for item in v3_pending.get("candidates", ()):
            append_design_history(item, "AUTONOMOUS_RESEARCH_V3_LEGACY_PENDING_SET")
    v4_history_path = root / "reports/AUTONOMOUS_RESEARCH_V4_DESIGN_HISTORY.json"
    if v4_history_path.exists():
        v4_history = json.loads(v4_history_path.read_text(encoding="utf-8"))
        for item in v4_history.get("candidates", ()):
            append_design_history(item, "AUTONOMOUS_RESEARCH_V4_DESIGN_HISTORY")
    for novelty_path in sorted((root / "reports").glob("codex_guided_autonomous_research_pilot_v1_*/**/candidate_novelty_gate.json")):
        novelty_payload = json.loads(novelty_path.read_text(encoding="utf-8"))
        for item in novelty_payload.get("rows", ()):
            append_design_history(item, f"{novelty_path.relative_to(root).as_posix()}")

    dedup: dict[str, dict[str, Any]] = {}
    for item in result:
        candidate_id = str(item.get("candidate_id"))
        if candidate_id not in dedup:
            dedup[candidate_id] = item
        else:
            for key, value in item.items():
                if dedup[candidate_id].get(key) in (None, "", [], {}):
                    dedup[candidate_id][key] = value
    return list(dedup.values())


def _candidate_map(record: Any, family_id: str) -> dict[str, Any]:
    candidate = record.candidate
    return {
        "candidate_id": candidate.candidate_id,
        "candidate_hash": record.preregistration_hash,
        "family_id": family_id,
        "mechanism": candidate.mechanism,
        "holding_period_days": int(candidate.holding_period),
        "factor_ids": [str(item["factor_id"]) for item in candidate.factor_bindings],
        "strategy_family": candidate.strategy_family,
        "event_ids": list(candidate.signal_logic.get("event_dependencies", ())),
        "semantic_fingerprint": record.semantic_fingerprint,
        "parameter_fingerprint": getattr(candidate, "parameter_fingerprint", None),
    }


def _semantic_similarity(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    if left.get("semantic_fingerprint") and right.get("semantic_fingerprint"):
        return 1.0 if left["semantic_fingerprint"] == right["semantic_fingerprint"] else 0.0
    left_factors = set(left.get("factor_ids", ()))
    right_factors = set(right.get("factor_ids", ()))
    factor_score = len(left_factors & right_factors) / max(1, len(left_factors | right_factors))
    holding_score = 1.0 if left.get("holding_period_days") == right.get("holding_period_days") else 0.0
    family_score = 1.0 if left.get("strategy_family") == right.get("strategy_family") else 0.0
    score = 0.6 * factor_score + 0.2 * holding_score + 0.2 * family_score
    if left.get("mechanism") != right.get("mechanism"):
        score = min(score, 0.85)
    if set(left.get("event_ids", ())) != set(right.get("event_ids", ())):
        score = min(score, 0.70)
    return round(score, 6)


def _similarity_report(root: Path, records: Sequence[Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    existing = _existing_candidate_metadata(root)
    rows: list[dict[str, Any]] = []
    for record in records:
        family_id = str(record.candidate.mechanism)
        current = _candidate_map(record, family_id)
        comparisons = []
        for item in existing:
            score = candidate_similarity(current, item)
            comparisons.append((score.similarity_score, _semantic_similarity(current, item), item, score))
        comparisons.sort(key=lambda item: (-item[0], -item[1], str(item[2]["candidate_id"])))
        _, semantic_score, nearest, score = comparisons[0]
        same_mechanism = current["mechanism"] == nearest.get("mechanism")
        same_factors = set(current["factor_ids"]) == set(nearest.get("factor_ids", ()))
        parameter_neighbor = bool(same_mechanism and same_factors and int(current["holding_period_days"]) in {int(nearest.get("holding_period_days", 0)) - 1, int(nearest.get("holding_period_days", 0)), int(nearest.get("holding_period_days", 0)) + 1})
        rows.append({
            "candidate_id": current["candidate_id"],
            "nearest_candidate_id": nearest["candidate_id"],
            "family_similarity": 1.0 if current["family_id"] == nearest.get("family_id") else 0.0,
            "semantic_similarity": semantic_score,
            "structural_similarity": score.similarity_score,
            "similarity_reasons": list(score.reasons),
            "parameter_neighbor_status": "PARAMETER_NEIGHBOR" if parameter_neighbor else "DISTINCT",
            "decision": "DUPLICATE_NEIGHBOR_REJECTED" if parameter_neighbor or (current.get("semantic_fingerprint") and current.get("semantic_fingerprint") == nearest.get("semantic_fingerprint")) else "DISTINCT_ELIGIBLE",
            "existing_registry_count_checked": len(existing),
        })
    return rows, {"existing_candidate_count": len(existing), "existing_passed_candidate_guard": PASSED_CANDIDATE_ID, "candidate_set_untouched": True}


def _load_history(root: Path, objective_id: str) -> CumulativeResearchHistoryV1:
    effective_path = root / "reports/CURRENT_EFFECTIVE_RESEARCH_HISTORY_V1.json"
    if effective_path.exists():
        effective_payload = json.loads(effective_path.read_text(encoding="utf-8"))
        valid_effective: list[dict[str, Any]] = []
        for row in effective_payload.get("rows", ()):
            classification = str(row.get("final_adjudicated_outcome") or "")
            if row.get("performance_accessed") and classification in LEGAL_CLASSIFICATIONS:
                valid_effective.append({
                    "trial_id": str(row.get("trial_id")),
                    "candidate_id": str(row.get("candidate_id")),
                    "status": "COMPLETED",
                    "classification": classification,
                    "performance_accessed": True,
                    "source": "CURRENT_EFFECTIVE_RESEARCH_HISTORY_V1",
                })
        return CumulativeResearchHistoryV1(objective_id, tuple(valid_effective), ())
    path = root / "data/research/strategy_validation/trial_registry_v3_engine_corrected_v3.json"
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"events": []}
    valid: list[dict[str, Any]] = []
    for event in payload.get("events", []):
        classification = str(event.get("classification") or "")
        if event.get("performance_accessed") and classification in LEGAL_CLASSIFICATIONS:
            valid.append({
                "trial_id": str(event.get("trial_id")),
                "candidate_id": str(event.get("candidate_id")),
                "status": "COMPLETED",
                "classification": classification,
                "performance_accessed": True,
                "source": "CUMULATIVE_LEGAL_HISTORY_HIGH_LEVEL_ONLY",
            })
    invalidated = [{
        "trial_id": "PHASE4_RERUN_V2",
        "status": "INVALIDATED",
        "classification": "ENGINEERING_BLOCKED",
        "performance_accessed": False,
        "source": "INVALIDATED_ENGINE_LINEAGE_SEPARATE",
    }]
    return CumulativeResearchHistoryV1(objective_id, tuple(valid), tuple(invalidated))


def _load_cumulative_private_p_values(root: Path, output_dir: Path) -> dict[str, float]:
    """Load private cumulative p-values only after a predictive trial exists."""

    paths = [root / "reports/RUN_AUTONOMOUS_ALPHA_AFTER_SAMPLE_POLICY_V2/multiple_testing.json"]
    if output_dir.exists():
        paths.extend(sorted(output_dir.rglob("multiple_testing.json")))
    result: dict[str, float] = {}
    for path in paths:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload.get("raw_p_values", {})
        if not isinstance(values, Mapping):
            continue
        for candidate_id, value in values.items():
            try:
                result.setdefault(str(candidate_id), float(value))
            except (TypeError, ValueError):
                continue
    return result


def _engine_hash(root: Path, helper_path: Path) -> str:
    import hashlib

    files = [
        root / "src/chanlun_trader/engine/engine.py",
        root / "src/chanlun_trader/engine/ledger.py",
        root / "src/chanlun_trader/engine/broker.py",
        root / "src/chanlun_trader/engine/portfolio_exit.py",
        root / "src/chanlun_trader/research/strategy_semantic.py",
        helper_path,
    ]
    return stable_hash({str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files})


def _dataset_hash(
    root: Path,
    factor_cache: Path,
    event_ids: Sequence[str],
    helper: Any,
    *,
    inline_event_definitions: Mapping[str, Mapping[str, Any]] | None = None,
    contract_event_identities: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    paths = {
        "daily": root / "data/research/daily_all.parquet",
        "factor_cache": factor_cache,
        "pit_manifest": root / "data/research/security_state/normalized/manifest.json",
        "routing_policy": root / "data/research/data_routing/routing_policy.json",
        "universe_policy": root / "data/research/universe_policies/A_SHARE_RESEARCH_UNIVERSE_POLICY_V2.json",
    }
    identities = {key: helper.sha256(path) for key, path in paths.items()}
    for event_id in event_ids:
        path = root / "data/research/event_store_repaired" / f"{event_id}_v2.parquet"
        if path.exists():
            identities[f"event:{event_id}"] = helper.sha256(path)
            continue
        contract_identity = dict((contract_event_identities or {}).get(event_id) or {})
        if contract_identity:
            identities[f"contract_event:{event_id}"] = stable_hash(contract_identity)
            continue
        definition = dict((inline_event_definitions or {}).get(event_id) or {})
        if not definition:
            identities[f"event:{event_id}"] = helper.sha256(path)
            continue
        identities[f"inline_event:{event_id}"] = stable_hash(definition)
    return stable_hash(identities)


def _frozen_contract_registry_identities(root: Path) -> dict[str, Any]:
    paths = {
        "factor_registry": root / "data/research/unified_factor_registry/registry.json",
        "event_registry": root / "data/research/event_registry/registry.json",
    }
    identities: dict[str, Any] = {}
    for name, path in paths.items():
        if not path.exists():
            raise RuntimeError(f"durable candidate contract registry identity is unavailable: {path}")
        identities[name] = {
            "path": str(path.relative_to(root)).replace("\\", "/"),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return identities


def _existing_frozen_contract_payloads(root: Path, candidate_id: str) -> tuple[Mapping[str, Any], ...]:
    """Load persisted payloads that may establish a canonical contract node."""

    from .durability import DurableFrozenCandidateContractRegistryV1

    payloads: list[Mapping[str, Any]] = []
    contracts_root = root / "data/research/research_factory/batches"
    for path in sorted(contracts_root.glob("*/durable_frozen_candidate_contracts.json")):
        registry = DurableFrozenCandidateContractRegistryV1.read(path)
        for contract in registry.items():
            if contract.candidate_id == candidate_id:
                payloads.append(contract.to_dict())
    return tuple(payloads)


def _checkpoint(orchestrator: Any, machine: ResearchBatchStateMachineV1, plan: ResearchBatchPlanV1, hypotheses: Sequence[Any], candidates: Sequence[Any], *, policy_hash: str, dataset_hash: str, completed_trial_ids: Sequence[str], engine_hash: str, pending_trial_ids: Sequence[str] = (), trial_records: Sequence[Mapping[str, Any]] = (), failure_snapshot: Any | None = None, stop_reason: str | None = None) -> Path:
    path = orchestrator.output_dir / "checkpoints" / f"{plan.batch_id}.json"
    _write(path, {
        "schema_version": "research-factory-real-checkpoint-v1",
        "objective_hash": orchestrator.objective.objective_hash,
        "batch_plan_hash": plan.plan_hash,
        "hypothesis_set_hash": stable_hash(hypotheses),
        "candidate_set_hash": stable_hash(candidates),
        "budget_registry_head_hash": orchestrator.budget.head_hash,
        "trial_ledger_head_hash": orchestrator.trial_ledger.head_hash,
        "validation_policy_hash": policy_hash,
        "validation_policy_id": plan.validation_policy_id,
        "validation_policy_version": plan.validation_policy_version,
        "decision_family_id": plan.decision_family_id,
        "family_contract_hash": plan.family_contract_hash,
        "dataset_hash": dataset_hash,
        "engine_hash": engine_hash,
        "code_identity": orchestrator.CODE_IDENTITY,
        "state": machine.state.value,
        "batch_id": plan.batch_id,
        "hypotheses": [getattr(item, "to_dict", lambda: dict(item))() for item in hypotheses],
        "candidates": [getattr(item, "to_dict", lambda: dict(item))() for item in candidates],
        "completed_trial_ids": list(completed_trial_ids),
        "performance_complete_trial_ids": [str(item.get("trial_id")) for item in trial_records if item.get("performance_accessed")],
        "pending_adjudication_trial_ids": [str(item.get("trial_id")) for item in trial_records if item.get("final_adjudication_pending")],
        "final_adjudicated_trial_ids": [str(item.get("trial_id")) for item in trial_records if item.get("classification") and item.get("status") == "COMPLETED"],
        "registry_committed_trial_ids": [str(item.get("trial_id")) for item in trial_records if item.get("final_decision_id")],
        "pending_trial_ids": list(pending_trial_ids),
        "final_adjudication_pending": bool(pending_trial_ids),
        "trial_records": [dict(item) for item in trial_records],
        "sample_feasibility_rows": [dict(item) for item in getattr(orchestrator, "_sample_feasibility_rows", ())],
        "sample_blocked_records": [dict(item) for item in getattr(orchestrator, "_sample_blocked_records", ())],
        "failure_snapshot": failure_snapshot.to_dict() if failure_snapshot is not None else None,
        "stop_reason": stop_reason,
        "provisional_evidence_dir": "provisional_validation_evidence",
        "state_machine": machine.audit(),
        "created_at": now_timestamp(),
    })
    return path


class RealFactoryRuntimeV1:
    """Execute one fully governed real research batch."""

    @staticmethod
    def _prepare_candidate_inputs(caller, policy, records, contracts, factor_cache):
        """逐候选复用正式准备，冻结窗口和原始时间不在批次层重新推导。"""
        by_id = {item.candidate_id: item for item in contracts}
        corrected = caller._corrected_module()
        return {
            record.candidate.candidate_id: caller._prepare_inputs(
                policy, record, corrected, factor_cache, contract=by_id[record.candidate.candidate_id])
            for record in records
        }

    def run(self, orchestrator: Any, *, plan: ResearchBatchPlanV1):
        from .orchestrator import FactoryRunResultV1, RESEARCH_FACTORY_CANONICAL_DEPENDENCIES_V1
        from .artifact_graph import ResearchArtifactGraphV1, add_frozen_contract_node_idempotent
        from .durability import DurableFrozenCandidateContractRegistryV1, DurableFrozenCandidateContractV1
        from .strategy_adapter import ResearchStrategyRegistryFacadeV1
        from .trial_adapter import ResearchFactoryTrialLedgerFacadeV1
        from chanlun_trader.research.hypothesis import AIHypothesisGenerator, HypothesisRegistry, ResearchContextBuilder
        from chanlun_trader.research.strategy_candidate import StrategyCandidateBuilder
        from chanlun_trader.research.strategy_semantic import StrategyCandidateCompilerV2, build_semantic_record
        from chanlun_trader.research.strategy_validation import (
            FinalResearchAdjudicatorV1,
            PerformanceAccessGate,
            benjamini_hochberg,
            load_frozen_policy,
            small_capital_contract,
            stage1_pit_data_preflight,
        )
        from chanlun_trader.research.validation_policy_v2 import load_validation_decision_policy_v2
        from .sample_feasibility import PASS

        if plan.objective_id != orchestrator.objective.objective_id:
            raise ValueError("real runtime received a plan for another objective")
        try:
            batch_number = int(getattr(plan, "batch_number", 0) or plan.batch_id.rsplit("_B", 1)[-1])
        except (TypeError, ValueError) as exc:
            raise ValueError("real runtime requires a deterministic batch number") from exc
        if batch_number < 1:
            raise ValueError("batch_number must be >= 1")
        if min(plan.max_hypotheses, plan.max_candidates, plan.max_performance_trials) <= 0:
            raise ValueError("real runtime batch budgets must be positive")

        root = orchestrator.root.resolve()
        report_dir = orchestrator.output_dir / plan.batch_id
        data_dir = root / "data/research/research_factory/batches" / plan.batch_id
        report_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        policy_path = root / "data/research/strategy_validation/validation_policy.json"
        policy_path_v2 = root / "data/research/strategy_validation/validation_decision_policy_v2.json"
        policy_v2_active = plan.validation_policy_id == "VALIDATION_DECISION_POLICY_V2"
        if policy_v2_active:
            policy, policy_hash = load_validation_decision_policy_v2(policy_path_v2)
            if policy_hash != plan.validation_policy_hash or plan.validation_policy_version != policy.policy_version:
                raise RuntimeError("Batch plan is not pinned to the active ValidationDecisionPolicyV2")
        else:
            policy, policy_hash = load_frozen_policy(policy_path)
        if policy.research_end != 20250731:
            raise RuntimeError("canonical research boundary changed; real Batch 1 is fail-closed")
        pending_checkpoint_path = orchestrator.output_dir / "checkpoints" / f"{plan.batch_id}.json"
        if pending_checkpoint_path.exists():
            pending_checkpoint = json.loads(pending_checkpoint_path.read_text(encoding="utf-8"))
            if pending_checkpoint.get("validation_policy_hash") != policy_hash:
                raise RuntimeError("resume checkpoint validation policy hash mismatch")
            if pending_checkpoint.get("state") in {
                ResearchBatchState.COMPLETED.value,
                ResearchBatchState.BLOCKED.value,
                ResearchBatchState.ENGINEERING_BLOCKED.value,
                ResearchBatchState.BUDGET_EXHAUSTED.value,
            }:
                materialize = getattr(orchestrator, "_materialize_terminal_checkpoint", None)
                if materialize is not None:
                    return materialize(pending_checkpoint, plan)
                return orchestrator.resume_from_checkpoint(pending_checkpoint_path)
            if pending_checkpoint.get("final_adjudication_pending") and pending_checkpoint.get("pending_trial_ids"):
                return self._resume_pending_adjudication(
                    orchestrator,
                    plan,
                    pending_checkpoint,
                    policy=policy,
                    policy_hash=policy_hash,
                    report_dir=report_dir,
                    data_dir=data_dir,
                )
        orchestrator.history = _load_history(root, orchestrator.objective.objective_id)
        machine = ResearchBatchStateMachineV1(plan.batch_id, path=orchestrator.output_dir / "state" / f"{plan.batch_id}.json")
        orchestrator.artifact_graph.add_node(f"objective:{orchestrator.objective.objective_id}", "Objective", orchestrator.objective.to_dict())
        orchestrator.artifact_graph.add_node(f"batch:{plan.batch_id}", "Batch", plan.to_dict())
        orchestrator.artifact_graph.add_edge(f"batch:{plan.batch_id}", "PLANNED_BY", f"objective:{orchestrator.objective.objective_id}")
        orchestrator._register_budget(plan)
        machine.transition(ResearchBatchState.BUDGET_RESERVED, "objective, batch and family budgets registered; trial reservations pending Stage 1")
        _write(report_dir / "batch_plan.json", {**plan.to_dict(), "objective_hash": orchestrator.objective.objective_hash, "policy_hash": policy_hash})
        _write(data_dir / "batch_plan.json", plan.to_dict())

        research_context = ResearchContextBuilder(root).build()
        template_source = tuple(getattr(orchestrator, "real_batch_templates", REAL_BATCH_TEMPLATES))
        factor_ids = sorted({factor_id for template in template_source for factor_id in template["factor_ids"]})
        factor_summary = []
        for factor_id in factor_ids:
            definition = research_context.factor_registry.get(factor_id, "v1")
            if definition is None or definition.implementation_status != "EXECUTABLE" or definition.pit_status != "PIT_VERIFIED" or definition.data_support_status != "FULL":
                raise RuntimeError(f"factor is not executable/PIT/full: {factor_id}")
            factor_summary.append({
                "factor_id": factor_id,
                "version": "v1",
                "implementation_status": definition.implementation_status,
                "pit_status": definition.pit_status,
                "data_support_status": definition.data_support_status,
                "source_type": definition.source_type,
            })
        failure_knowledge_view = getattr(orchestrator, "real_failure_knowledge_view", None)
        failure_entries = tuple(getattr(failure_knowledge_view, "entries", ()))
        context = NoOutcomeResearchContextV1(
            factor_capability_summary=tuple(factor_summary),
            mechanism_history=tuple({"mechanism": mechanism, "history_available": True} for mechanism in orchestrator.objective.mechanism_scope),
            failure_class_summaries=tuple({"category": key, "count": value} for key, value in orchestrator.history.classification_counts().items()) + failure_entries,
            constraints={
                "universe": list(orchestrator.objective.research_universe),
                "holding_horizon_sessions": list(orchestrator.objective.holding_horizon),
                "preferred_horizon_sessions": list(orchestrator.objective.preferred_horizon),
                "complexity_budget": dict(plan.complexity_budget),
                "parameter_optimization": False,
                "research_start": policy.research_start,
                "research_end": policy.research_end,
            },
        )
        PerformanceBlindGuard.assert_blind(context.to_dict())
        machine.transition(ResearchBatchState.DESIGNING, "NoOutcomeResearchContextV1 created from factor capability and high-level history only")
        orchestrator.baseline_registry.register_plan(plan.batch_id, plan.baseline_plan, tuple(sorted(set(orchestrator.objective.mechanism_scope))))
        _write(report_dir / "baseline_preregistration.json", {
            "registrations": [item.to_dict() for item in orchestrator.baseline_registry.items()],
            "controls_registered_before_performance": True,
            "performance_data_loaded": False,
        })
        _write(report_dir / "context_blind_audit.json", context.to_dict())

        existing_hypotheses = HypothesisRegistry.read(root / "data/research/hypothesis_registry/registry.json").items()
        backend = getattr(orchestrator, "agent_backend", None)
        agent_templates: tuple[dict[str, Any], ...] | None = None
        if backend is not None:
            from .agent_backend import AgentBackendError, GovernedResearchAgentBackendV1, ResearchAgentInputBuilderV1, ResearchProposalBatchV1
            agent_input = ResearchAgentInputBuilderV1().build(
                run_id=str(plan.run_id or "REAL_RUNTIME"),
                batch_id=plan.batch_id,
                objective=orchestrator.objective,
                policy_identity={"policy_id": plan.validation_policy_id, "policy_version": plan.validation_policy_version, "policy_hash": policy_hash},
                no_outcome_context=context,
                failure_knowledge=failure_knowledge_view or FailureKnowledgeSnapshotV1(snapshot_id=str(plan.failure_knowledge_snapshot_id or "EMPTY")),
                candidate_neighborhood=orchestrator.neighborhood_index,
                family_mechanism_constraints={"family_quotas": dict(plan.family_quotas), "mechanism_quotas": dict(plan.mechanism_quotas)},
                complexity_constraints=plan.complexity_budget,
                factor_event_catalog=tuple(factor_summary),
                proposal_budget_view={"max_proposals": plan.max_hypotheses, "max_candidates": plan.max_candidates},
            )
            agent_call_id = stable_hash({"run_id": plan.run_id, "batch_id": plan.batch_id, "backend_type": backend.backend_type, "backend_version": backend.backend_version, "context_hash": agent_input.input_context_hash})
            orchestrator.commit_ledger.ensure("agent_call_planned", agent_call_id, {"agent_call_id": agent_call_id, "run_id": plan.run_id, "batch_id": plan.batch_id, "context_hash": agent_input.input_context_hash})
            try:
                proposal_path = report_dir / "research_proposal_batch.json"
                if orchestrator.commit_ledger.has("agent_call_completed", agent_call_id) and proposal_path.exists():
                    proposal_batch = ResearchProposalBatchV1.from_dict(json.loads(proposal_path.read_text(encoding="utf-8")))
                else:
                    proposal_batch = GovernedResearchAgentBackendV1(backend, policy=orchestrator.agent_governance_policy, budget=orchestrator.agent_budget, audit=orchestrator.agent_audit).invoke(agent_input, run_id=str(plan.run_id or "REAL_RUNTIME"), batch_id=plan.batch_id, agent_call_id=agent_call_id)
            except AgentBackendError as exc:
                call_state = orchestrator.agent_budget.call(agent_call_id) or {}
                reason = str(exc)
                stop_reason = reason if reason.startswith("AGENT_") else "AGENT_BACKEND_ERROR"
                error_payload = {"schema_version": "agent-backend-error-v1", "agent_call_id": agent_call_id, "run_id": plan.run_id, "batch_id": plan.batch_id, "backend": backend.backend_type, "backend_version": backend.backend_version, "context_hash": agent_input.input_context_hash, "error_class": type(exc).__name__, "reason": reason, "retryable": bool(call_state.get("retryable", False)), "retry_count": int(call_state.get("retry_count", 0)), "budget_impact": {"predictive_trials": 0, "agent_calls": 1 if call_state else 0}, "timestamp": now_timestamp()}
                _write(report_dir / "agent_backend_error.json", error_payload)
                orchestrator.commit_ledger.ensure("agent_backend_error_committed", agent_call_id, error_payload)
                machine.transition(ResearchBatchState.ENGINEERING_BLOCKED, stop_reason)
                failure_snapshot = FailureKnowledgeSnapshotV1(snapshot_id=f"{plan.batch_id}_AGENT_BACKEND_ERROR")
                checkpoint = _checkpoint(orchestrator, machine, plan, (), (), policy_hash=policy_hash, dataset_hash="AGENT_BACKEND_ERROR", completed_trial_ids=(), engine_hash="AGENT_BACKEND_ERROR", trial_records=(), stop_reason="AGENT_BACKEND_ERROR")
                return self._finish(orchestrator, plan, machine, (), (), (), failure_snapshot, checkpoint, report_dir, data_dir, stop_reason=stop_reason, real_performance=False, final_acceptance=False)
            _write(report_dir / "research_agent_input.json", agent_input.to_dict())
            _write(report_dir / "research_proposal_batch.json", proposal_batch.to_dict())
            orchestrator.commit_ledger.ensure("agent_call_completed", agent_call_id, {"agent_call_id": agent_call_id, "proposal_batch_hash": proposal_batch.proposal_batch_hash})
            agent_templates = tuple({
                "hypothesis_type": str(item.family_id).upper(),
                "title": str(item.economic_rationale),
                "description": str(item.economic_rationale),
                "mechanism": str(item.mechanism),
                "factor_ids": list(item.factor_dependencies),
                "factor_roles": [{"factor_id": factor_id, "role": "PRIMARY_ALPHA" if index == 0 else "CONFIRMATION"} for index, factor_id in enumerate(item.factor_dependencies)],
                "expected_factor_direction": {factor_id: "POSITIVE" for factor_id in item.factor_dependencies},
                "event_dependencies": list(item.event_dependencies),
                "target_horizon": str(item.expected_holding_horizon),
                "expected_holding_period": str(item.expected_holding_horizon),
                "market_regime": ["NON_CRASH_OR_EXPLICIT_REGIME"],
                "generator_mode": "RESEARCH_AGENT_BACKEND",
                "parameter_ranges": dict(item.candidate_complexity),
            } for item in proposal_batch.proposals)
            if not agent_templates:
                machine.transition(ResearchBatchState.BLOCKED, "NO_LEGAL_NEW_CANDIDATES")
                failure_snapshot = FailureKnowledgeSnapshotV1(snapshot_id=f"{plan.batch_id}_NO_LEGAL_RESEARCH_PROPOSAL")
                checkpoint = _checkpoint(orchestrator, machine, plan, (), (), policy_hash=policy_hash, dataset_hash="NO_LEGAL_NEW_CANDIDATES", completed_trial_ids=(), engine_hash="NO_LEGAL_NEW_CANDIDATES", trial_records=(), stop_reason="NO_LEGAL_NEW_CANDIDATES")
                return self._finish(orchestrator, plan, machine, (), (), (), failure_snapshot, checkpoint, report_dir, data_dir, stop_reason="NO_LEGAL_NEW_CANDIDATES", real_performance=False, final_acceptance=False)
        generator = AIHypothesisGenerator(research_context)
        hypotheses = []
        template_source = template_source if agent_templates is None else agent_templates
        expected_hypothesis_count = plan.max_hypotheses if agent_templates is None else len(agent_templates)
        for template in template_source[:expected_hypothesis_count]:
            hypothesis = generator.generate_from_template(template, tuple(existing_hypotheses) + tuple(hypotheses))
            PerformanceBlindGuard.assert_blind(hypothesis.to_dict())
            hypotheses.append(hypothesis)
            hypothesis_node = f"hypothesis:{hypothesis.hypothesis_id}"
            orchestrator.artifact_graph.add_node(hypothesis_node, "Hypothesis", hypothesis.to_dict())
            orchestrator.artifact_graph.add_edge(hypothesis_node, "GENERATED_FROM", f"batch:{plan.batch_id}")
        if len(hypotheses) != expected_hypothesis_count:
            raise RuntimeError("hypothesis cap did not produce the plan's hypothesis count")
        machine.transition(ResearchBatchState.HYPOTHESES_FROZEN, f"{len(hypotheses)} hypotheses frozen: {stable_hash([item.to_dict() for item in hypotheses])}")
        _write(report_dir / "hypothesis_generation.json", {
            "generator": "AIHypothesisGenerator V1",
            "hypothesis_count": len(hypotheses),
            "hypotheses": [item.to_dict() for item in hypotheses],
            "performance_data_loaded": False,
            "exact_prior_outcomes_loaded": False,
        })
        _write(data_dir / "hypotheses.json", [item.to_dict() for item in hypotheses])

        builder = StrategyCandidateBuilder(root)
        compiler = StrategyCandidateCompilerV2()
        all_records = []
        for hypothesis in hypotheses:
            candidate_v1 = builder.build_hypothesis(hypothesis)
            record = build_semantic_record(candidate_v1, hypothesis.to_dict())
            PerformanceBlindGuard.assert_blind(record.to_dict())
            compiler.compile(record)
            all_records.append(record)
            candidate_node = f"candidate:{record.candidate.candidate_id}"
            orchestrator.artifact_graph.add_node(candidate_node, "Candidate", record.to_dict())
            orchestrator.artifact_graph.add_edge(candidate_node, "GENERATED_FROM", f"hypothesis:{hypothesis.hypothesis_id}")
            for factor_id in sorted({str(item["factor_id"]) for item in record.candidate.factor_bindings}):
                factor_node = f"factor:{factor_id}"
                if factor_node not in orchestrator.artifact_graph.nodes:
                    orchestrator.artifact_graph.add_node(factor_node, "Factor", next(item for item in factor_summary if item["factor_id"] == factor_id))
                orchestrator.artifact_graph.add_edge(candidate_node, "USES_FACTOR", factor_node)
        existing_candidates = _existing_candidate_metadata(root)
        novelty_gate = CandidateNoveltyGateV2()
        novelty_rows: list[dict[str, Any]] = []
        novelty_accepted: list[Mapping[str, Any]] = []
        for record in all_records:
            current = _candidate_map(record, str(record.candidate.mechanism))
            decision = novelty_gate.evaluate(
                current,
                same_batch_candidates=novelty_accepted,
                historical_candidates=existing_candidates,
                current_registry_candidates=existing_candidates,
                historical_rejected_candidates=existing_candidates,
                historical_promising_candidates=existing_candidates,
                historical_passed_candidates=existing_candidates,
            )
            novelty_rows.append({
                "candidate_id": decision.candidate_id,
                "comparison_set_hash": decision.comparison_set_hash,
                "exact_duplicate": decision.exact_duplicate,
                "nearest_semantic_neighbor": decision.nearest_semantic_neighbor,
                "parameter_neighbor": decision.parameter_neighbor,
                "decision": "ALLOW" if decision.allowed else "BLOCK",
                "reason_code": decision.reason,
                "compared_candidate_ids": list(decision.compared_candidate_ids),
                "policy_id": novelty_gate.policy_id,
                "policy_version": novelty_gate.policy_version,
            })
            if decision.allowed:
                novelty_accepted.append(current)
        try:
            diversity = orchestrator.family_diversity_enforcer.freeze(
                [_candidate_map(record, str(record.candidate.mechanism)) for record in all_records if str(record.candidate.candidate_id) in {str(item.get("candidate_id")) for item in novelty_accepted}],
                required_count=plan.max_candidates,
            )
        except InsufficientDiverseCandidatesError:
            _write(report_dir / "candidate_novelty_gate.json", {"policy_id": novelty_gate.policy_id, "policy_version": novelty_gate.policy_version, "rows": novelty_rows, "comparison_set_hash": stable_hash(existing_candidates), "performance_data_loaded": False})
            raise
        accepted_ids = {str(item.get("candidate_id")) for item in diversity.accepted_candidates}
        frozen_records = [record for record in all_records if str(record.candidate.candidate_id) in accepted_ids]
        similarity_rows = novelty_rows
        similarity_meta = {"existing_candidate_count": len(existing_candidates), "comparison_set_hash": stable_hash(existing_candidates), "policy_id": novelty_gate.policy_id, "policy_version": novelty_gate.policy_version}
        _write(report_dir / "candidate_build.json", {
            "builder": "StrategyCandidateBuilder V1",
            "compiler": "StrategyCandidateCompilerV2",
            "candidate_count_built": len(all_records),
            "candidates": [item.to_dict() for item in all_records],
            "performance_data_loaded": False,
        })
        _write(report_dir / "candidate_similarity.json", {"rows": similarity_rows, **similarity_meta, "existing_passed_candidate_untouched": True})
        _write(report_dir / "semantic_gate.json", {
            "compiler": "StrategyCandidateCompilerV2",
            "phase1_semantic_eligible_count": sum(bool(item.phase4_eligible) for item in all_records),
            "blocked_candidate_ids": [item.candidate.candidate_id for item in all_records if not item.phase4_eligible],
            "semantic_status_counts": dict(Counter(item.semantic_status for item in all_records)),
            "performance_data_loaded": False,
        })

        if len(frozen_records) != plan.max_candidates:
            raise RuntimeError("INSUFFICIENT_DIVERSE_CANDIDATES")
        if any(not item.phase4_eligible for item in frozen_records):
            raise RuntimeError("a frozen candidate is not semantic Phase 4 eligible")
        frozen_ids = {item.candidate.candidate_id for item in frozen_records}
        not_frozen = [item.candidate.candidate_id for item in all_records if item.candidate.candidate_id not in frozen_ids]
        hypothesis_by_id = {str(item.hypothesis_id): item.to_dict() for item in hypotheses}
        freeze_timestamp = getattr(orchestrator, "real_freeze_timestamp", now_timestamp())
        contract_registry = DurableFrozenCandidateContractRegistryV1(data_dir / "durable_frozen_candidate_contracts.json")
        registry_identities = _frozen_contract_registry_identities(root)
        contracts = []
        for record in frozen_records:
            contract = DurableFrozenCandidateContractV1.from_semantic_record(
                record,
                hypothesis_by_id.get(record.candidate.parent_hypothesis_id, {}),
                factor_event_registry_identities=registry_identities,
                research_period_identity={
                    "id": "VALIDATION_RESEARCH_PERIOD_V1",
                    "start": int(policy.research_start),
                    "end": int(policy.research_end),
                    "calendar": "AUTHORITATIVE_TRADING_SESSION_CALENDAR",
                },
                policy_identity={
                    "policy_id": plan.validation_policy_id,
                    "policy_version": plan.validation_policy_version,
                    "policy_hash": policy_hash,
                    "objective_id": orchestrator.objective.objective_id,
                },
                source_provenance={
                    "run_id": plan.run_id or "REAL_RUNTIME",
                    "batch_id": plan.batch_id,
                    "builder": "StrategyCandidateBuilder V1",
                    "compiler": "StrategyCandidateCompilerV2",
                    "freeze_stage": "REAL_FACTORY_CANDIDATE_FREEZE",
                },
                created_frozen_timestamp=freeze_timestamp,
            )
            PerformanceBlindGuard.assert_blind(contract.to_dict())
            contract_registry.append(contract)
            contracts.append(contract)
        contract_registry.write()
        contract_ref = str((data_dir / "durable_frozen_candidate_contracts.json").relative_to(root)).replace("\\", "/")
        _write(report_dir / "durable_frozen_candidate_contracts.json", {
            **contract_registry.to_dict(),
            "durable_contract_ref": contract_ref,
            "round_trip_required": True,
            "performance_data_loaded": False,
        })
        for contract in contracts:
            contract_node = f"frozen_contract:{contract.candidate_id}"
            add_frozen_contract_node_idempotent(
                orchestrator.artifact_graph,
                contract_node,
                contract.to_dict(),
                _existing_frozen_contract_payloads(root, contract.candidate_id),
            )
            orchestrator.artifact_graph.add_edge(f"candidate:{contract.candidate_id}", "HAS_DURABLE_CONTRACT", contract_node)
        frozen_candidate_ids = {item.candidate.candidate_id for item in frozen_records}
        reloaded_contracts = DurableFrozenCandidateContractRegistryV1.read(data_dir / "durable_frozen_candidate_contracts.json").items()
        contract_by_id = {contract.candidate_id: contract for contract in reloaded_contracts}
        if not frozen_candidate_ids <= set(contract_by_id):
            raise RuntimeError("durable frozen candidate reload changed the frozen candidate set")
        frozen_records = [contract_by_id[candidate_id].reconstruct_candidate() for candidate_id in sorted(frozen_candidate_ids)]
        orchestrator._register_budget(plan, [{"candidate_id": item.candidate.candidate_id} for item in frozen_records])
        for record in frozen_records:
            contract = next(item for item in contracts if item.candidate_id == record.candidate.candidate_id)
            registered = orchestrator.strategy_registry.register(record.candidate.candidate_id, record.preregistration_hash, record.candidate.mechanism, durable_contract_hash=contract.content_hash, durable_contract_ref=contract_ref)
            if registered.research_state == "DRAFT":
                registered = orchestrator.strategy_registry.transition(record.candidate.candidate_id, "SEMANTIC_READY")
            if registered.research_state == "SEMANTIC_READY":
                orchestrator.strategy_registry.transition(record.candidate.candidate_id, "VALIDATION_ELIGIBLE")
        machine.transition(ResearchBatchState.CANDIDATES_FROZEN, f"{len(frozen_records)} candidates frozen; {len(not_frozen)} candidates left outside the performance cap")
        _write(report_dir / "candidate_freeze.json", {
            "frozen_candidate_count": len(frozen_records),
            "frozen_candidate_ids": [item.candidate.candidate_id for item in frozen_records],
            "not_frozen_candidate_ids": not_frozen,
            "mechanism_count": len({item.candidate.mechanism for item in frozen_records}),
            "family_diversity_policy": orchestrator.family_diversity_policy.to_dict(),
            "family_diversity_policy_hash": orchestrator.family_diversity_policy.policy_hash,
            "parameter_search_status": "DISABLED",
            "existing_passed_candidate_id": PASSED_CANDIDATE_ID,
        })
        _write(data_dir / "candidates.json", [item.to_dict() for item in frozen_records])
        _write(report_dir / "candidate_novelty_gate.json", {"policy_id": novelty_gate.policy_id, "policy_version": novelty_gate.policy_version, "rows": novelty_rows, "comparison_set_hash": similarity_meta["comparison_set_hash"], "performance_data_loaded": False})

        machine.transition(ResearchBatchState.STAGE1_VALIDATING, "candidate freeze complete; sample-feasibility preflight begins before predictive reservation")
        sample_rows: list[dict[str, Any]] = []
        sample_blocked_records: list[dict[str, Any]] = []
        sample_feasible_records = []
        for record in frozen_records:
            candidate_input = orchestrator.sample_feasibility_input(record, backend_type="REAL_FACTORY")
            sample_result = orchestrator.sample_feasibility.run(candidate_input)
            row = sample_result.to_dict()
            sample_rows.append(row)
            if row.get("status") == PASS:
                sample_feasible_records.append(record)
            else:
                candidate_id = record.candidate.candidate_id
                orchestrator.strategy_registry.transition(candidate_id, "VALIDATION_BLOCKED", evidence_ref="SAMPLE_FEASIBILITY_PREFLIGHT")
                sample_blocked_records.append({
                    "trial_id": f"{plan.batch_id}:SAMPLE_FEASIBILITY:{candidate_id}",
                    "candidate_id": candidate_id,
                    "family_id": record.candidate.mechanism,
                    "mechanism": record.candidate.mechanism,
                    "status": "BLOCKED",
                    "classification": "BLOCKED",
                    "failure_category": "SAMPLE_FEASIBILITY_FAILURE",
                    "performance_accessed": False,
                    "reason_codes": tuple(row.get("reason_codes", ())),
                    "sample_feasibility_status": row.get("status"),
                })
        orchestrator._sample_feasibility_rows = sample_rows
        orchestrator._sample_blocked_records = sample_blocked_records
        _write(report_dir / "sample_feasibility_preflight.json", {
            "preflight_version": "CandidateSampleFeasibilityPreflightV1",
            "rows": sample_rows,
            "predictive_budget_reserved": False,
            "performance_accessed": False,
            "performance_data_loaded": False,
        })
        if not sample_feasible_records:
            machine.transition(ResearchBatchState.BLOCKED, "sample-feasibility preflight blocked all frozen candidates; predictive gate remains closed")
            failure_snapshot = orchestrator.failure_adapter.snapshot_from_trials(sample_blocked_records, snapshot_id=f"{plan.batch_id}_FAILURE_SNAPSHOT", parent_snapshot_id=plan.failure_knowledge_snapshot_id)
            checkpoint = _checkpoint(orchestrator, machine, plan, hypotheses, frozen_records, policy_hash=policy_hash, dataset_hash="NOT_ACCESSED_SAMPLE_FEASIBILITY_BLOCKED", completed_trial_ids=(), engine_hash="NOT_ACCESSED_SAMPLE_FEASIBILITY_BLOCKED", trial_records=sample_blocked_records, stop_reason="SAMPLE_FEASIBILITY_BLOCKED")
            return self._finish(orchestrator, plan, machine, hypotheses, frozen_records, sample_blocked_records, failure_snapshot, checkpoint, report_dir, data_dir, stop_reason="SAMPLE_FEASIBILITY_BLOCKED", real_performance=False, final_acceptance=False)

        reservations: dict[str, str] = {}
        for record in sample_feasible_records:
            candidate_id = record.candidate.candidate_id
            reservations[candidate_id] = orchestrator.budget.reserve_trial(batch_id=plan.batch_id, family_id=record.candidate.mechanism, candidate_id=candidate_id)
        _write(report_dir / "budget_reservation.json", {
            "reservation_status": "ALL_TRIALS_RESERVED_BEFORE_PERFORMANCE",
            "reservation_count": len(reservations),
            "reservation_ids": reservations,
            "objective": orchestrator.budget.snapshot(),
            "trial_budget_expansion": False,
        })
        stage1_rows = [stage1_pit_data_preflight(root, item, research_end=policy.research_end) for item in sample_feasible_records]
        _write(report_dir / "stage1_pit_data_preflight.json", {"rows": stage1_rows, "performance_data_loaded": False})
        blocked_stage1 = [row for row in stage1_rows if row.get("status") != "PASS"]
        eligible_records = [item for item, row in zip(sample_feasible_records, stage1_rows) if row.get("status") == "PASS"]
        for item in sample_feasible_records:
            if item not in eligible_records:
                orchestrator.budget.release(reservations[item.candidate.candidate_id])
                orchestrator.strategy_registry.transition(item.candidate.candidate_id, "VALIDATION_BLOCKED", evidence_ref="STAGE1_PIT_DATA_PREFLIGHT")
        if not eligible_records:
            machine.transition(ResearchBatchState.BLOCKED, "all frozen candidates failed Stage 1; performance gate remains closed")
            trial_records = [*sample_blocked_records, *({"trial_id": f"{plan.batch_id}:BLOCKED:{item.candidate.candidate_id}", "candidate_id": item.candidate.candidate_id, "family_id": item.candidate.mechanism, "status": "BLOCKED", "classification": "ENGINEERING_BLOCKED", "performance_accessed": False, "reason_codes": tuple(row.get("reason_codes", ())) } for item, row in zip(sample_feasible_records, stage1_rows) if row.get("status") != "PASS")]
            failure_snapshot = orchestrator.failure_adapter.snapshot_from_trials(trial_records, snapshot_id=f"{plan.batch_id}_FAILURE_SNAPSHOT", parent_snapshot_id=plan.failure_knowledge_snapshot_id)
            checkpoint = _checkpoint(orchestrator, machine, plan, hypotheses, frozen_records, policy_hash=policy_hash, dataset_hash="NOT_ACCESSED_STAGE1_BLOCKED", completed_trial_ids=(), engine_hash="NOT_ACCESSED_STAGE1_BLOCKED")
            return self._finish(orchestrator, plan, machine, hypotheses, frozen_records, trial_records, failure_snapshot, checkpoint, report_dir, data_dir, stop_reason="STAGE1_BLOCKED", real_performance=False, final_acceptance=False)

        import dataclasses
        if blocked_stage1:
            _write(report_dir / "stage1_blocked_candidates.json", blocked_stage1)
        machine.transition(ResearchBatchState.PERFORMANCE_VALIDATING, "Stage 1 passed; pre-registered trials may now open the performance gate")

        corrected = load_corrected_module()
        helper_path = Path(corrected.__file__)
        engine_hash = _engine_hash(SOURCE_ROOT, helper_path)
        factor_cache = root / "data/research/strategy_validation/phase4_rerun_v2_factor_values.parquet"
        if not factor_cache.exists():
            for reservation_id in reservations.values():
                orchestrator.budget.release(reservation_id)
            raise RuntimeError("canonical factor cache is missing")
        event_ids = sorted({str(condition["event_id"]) for record in eligible_records for condition in record.signal_predicate.event_conditions})
        dataset_hash = _dataset_hash(root, factor_cache, event_ids, corrected.legacy)
        from .predictive_executor import CanonicalPredictiveExecutorV1

        caller = CanonicalPredictiveExecutorV1(root, orchestrator.objective.objective_id)
        try:
            inputs_by_candidate = self._prepare_candidate_inputs(caller, policy, eligible_records, reloaded_contracts, factor_cache)
        except Exception:
            for reservation_id in reservations.values():
                if reservation_id in orchestrator.budget.snapshot().get("active_reservations", {}):
                    orchestrator.budget.release(reservation_id)
            raise
        _write(report_dir / "factor_data_routing.json", {
            "candidates": {key: value["input_diagnostics"] for key, value in inputs_by_candidate.items()},
            "cache_mutated": False,
            "in_memory_registered_factor_rebuild": [],
        })
        small_policy = replace(policy, initial_cash=policy.small_capital_cash, max_positions=policy.small_capital_slots, lot_size=policy.small_capital_lot_size)
        gate_path = report_dir / "performance_access_gate.json"
        if policy_v2_active:
            _write(gate_path, {
                "schema_version": "performance-access-gate-v2",
                "enabled": True,
                "policy_id": policy.policy_id,
                "policy_version": policy.policy_version,
                "policy_hash": policy_hash,
                "final_test_access": {"physical": 0, "analytical": 0, "decision": 0},
            })
            gate_marker = json.loads(gate_path.read_text(encoding="utf-8"))
            if gate_marker.get("enabled") is not True or gate_marker.get("policy_hash") != policy_hash:
                raise RuntimeError("ValidationDecisionPolicyV2 performance gate hash mismatch")
        else:
            gate = PerformanceAccessGate(gate_path)
            gate.enable(policy_path)
            gate.assert_enabled(policy_hash)

        trial_records: list[dict[str, Any]] = []
        validation_rows: list[dict[str, Any]] = []
        completed_trial_ids: list[str] = []
        p_values: dict[str, float] = {}
        provisional_evidence_dir = data_dir / "provisional_validation_evidence"
        decision_family_id = plan.decision_family_id if policy_v2_active else f"{plan.batch_id}:CUMULATIVE_LEGAL_FAMILY_V1"
        for index, record in enumerate(eligible_records, start=1):
            candidate_id = record.candidate.candidate_id
            trial_id = f"{plan.batch_id}_T{index:03d}"
            orchestrator.trial_ledger.register_before_performance(
                trial_id=trial_id,
                objective_id=orchestrator.objective.objective_id,
                batch_id=plan.batch_id,
                family_id=record.candidate.mechanism,
                hypothesis_id=record.candidate.parent_hypothesis_id,
                candidate_id=candidate_id,
                candidate_hash=record.preregistration_hash,
                dataset_hash=dataset_hash,
                validation_policy_hash=policy_hash,
                engine_hash=engine_hash,
                seed=plan.generation_seed + index,
                lineage={"source": "AIResearchFactoryOrchestratorV1", "canonical_runner": "BacktestEngineV2+PortfolioExitEvaluatorV1", "final_test_access": {"physical": 0, "analytical": 0, "decision": 0}},
            )
            try:
                orchestrator.trial_ledger.mark_performance_accessed(trial_id)
                inputs = inputs_by_candidate[candidate_id]
                base_result = caller._invoke_runner(policy, record, trial_id, inputs, portfolio_name="BASE_RESEARCH")
                ten_result = caller._invoke_runner(small_policy, record, trial_id, inputs, portfolio_name="SMALL_CAPITAL_10K")
                base_engine, base_metrics = base_result["engine"], base_result["metrics"]
                ten_engine, ten_metrics = ten_result["engine"], ten_result["metrics"]
                if base_result["status"] != "DIAGNOSTIC_ONLY" or ten_result["status"] != "DIAGNOSTIC_ONLY":
                    raise ValueError("R1_CALLER_EXECUTION_EVIDENCE_INSUFFICIENT_OR_INVALID")
                pnl = [float(trade.realized_pnl) for trade in base_engine.ledger.valid_trades if trade.side == corrected.Side.SELL]
                bootstrap = corrected.bootstrap_result(pnl, int(policy.bootstrap_iterations), int(policy.bootstrap_seed) + index)
                concentration = corrected.concentration(base_engine, float(policy.initial_cash))
                robustness = {"subperiods": corrected.subperiods(base_engine, float(policy.initial_cash)), "regime_split": corrected.regime_split(base_engine, inputs["regimes"])}
                cost_stress = {}
                for name, multipliers in {"BASE": (1.0, 1.0, 1.0), "FEES_X2": (2.0, 2.0, 1.0), "SLIPPAGE_X2": (1.0, 1.0, 2.0), "COMBINED_X2": (2.0, 2.0, 2.0)}.items():
                    cost_stress[name] = corrected.recompute_cost_stress_metrics(base_engine, base_metrics, policy, float(policy.initial_cash), fee_mult=multipliers[0], stamp_mult=multipliers[1], slip_mult=multipliers[2])
                validator_classification = corrected.classification(base_metrics, bootstrap, cost_stress)
                local_classification = validator_classification
                reason_codes = ("VALIDATOR_INVALID_INVARIANT",) if validator_classification == "INVALID" else (() if validator_classification in {"REJECTED", "WEAK", "RESEARCH_PASSED", "PROMISING"} else ("VALIDATOR_INSUFFICIENT_EVIDENCE",))
                small_contract = small_capital_contract(policy)
                microstructure = audit_microstructure(base_engine, inputs)
                engine_integrity = {"certification_status": base_metrics.get("certification_status"), "invariant_errors": base_metrics.get("invariant_errors", [])}
                v2_gates = {
                    "engine_integrity": {"passed": not engine_integrity.get("invariant_errors") and engine_integrity.get("certification_status") != "INVALID"},
                    "data_validity": {"passed": inputs["input_diagnostics"]["data_validity"] == "VALIDATED_DAILY_RAW"},
                    "pit_validity": {"passed": inputs["input_diagnostics"]["pit_validity"] == "EXPLICIT_DUAL_SOURCE_NORMAL_TRADING"},
                    "sample_adequacy": {"passed": int(base_metrics.get("closed_trade_count", 0)) >= 30 and bootstrap.get("status") == "COMPLETE"},
                    "local_base_return": {"passed": float(base_metrics.get("net_return", 0.0)) > 0},
                    "local_profit_factor": {"passed": base_metrics.get("profit_factor") is None or float(base_metrics.get("profit_factor")) > 1.0},
                    "raw_bootstrap_support": {"passed": float(bootstrap.get("p_value", 1.0)) < 0.05},
                    "cost_stress_combined_x2": {"passed": cost_stress.get("COMBINED_X2", {}).get("net_return") is not None and float(cost_stress["COMBINED_X2"]["net_return"]) > 0},
                    "small_capital_execution_feasibility": {"passed": bool(small_contract.get("fixed_contract")) and not ten_metrics.get("invariant_errors") and ten_metrics.get("certification_status") != "INVALID"},
                    "baseline_preregistration": {"passed": bool(orchestrator.baseline_registry.items())},
                    "intended_holding_contract": {"passed": 2 <= int(record.candidate.holding_period) <= 10},
                    "microstructure_realism": microstructure,
                    "candidate_similarity_control": {"passed": True},
                    "search_budget_reservation": {"passed": True},
                }
                validation_row = {
                    "trial_id": trial_id,
                    "candidate_id": candidate_id,
                    "validator_classification": validator_classification,
                    "local_classification": local_classification,
                    "classification": None,
                    "final_adjudication_pending": True,
                    "base_metrics": base_metrics,
                    "small_capital_10k_metrics": ten_metrics,
                    "small_capital_contract": small_contract,
                    "bootstrap": bootstrap,
                    "concentration": concentration,
                    "robustness": robustness,
                    "cost_stress": cost_stress,
                    "microstructure": microstructure,
                    "engine_integrity": engine_integrity,
                    "gate_roles": {
                        "engine_integrity": "HARD_GATE",
                        "sample_adequacy": "HARD_GATE",
                        "cost_stress": "HARD_GATE",
                        "small_capital_10k": "REPORT_ONLY_OR_FEASIBILITY_ONLY",
                        "concentration": "REPORT_ONLY",
                        "subperiod": "REPORT_ONLY",
                        "regime": "REPORT_ONLY",
                    },
                    "gates": v2_gates if policy_v2_active else {
                        "engine_integrity": {"role": "HARD_GATE", "passed": not engine_integrity.get("invariant_errors") and engine_integrity.get("certification_status") != "INVALID"},
                        "sample_adequacy": {"role": "HARD_GATE", "passed": validator_classification != "INSUFFICIENT_EVIDENCE"},
                        "cost_stress": {"role": "HARD_GATE", "passed": validator_classification != "REJECTED" or not (cost_stress.get("COMBINED_X2", {}).get("net_return") is not None and float(cost_stress["COMBINED_X2"]["net_return"]) <= 0)},
                    },
                    "final_test_access": {"physical": 0, "analytical": 0, "decision": 0},
                }
                if policy_v2_active:
                    validation_row["soft_evidence"] = {
                        "small_capital_economic_performance": {"status": "REPORT_ONLY"},
                        "concentration_diagnostics": {"status": "AVAILABLE", "metrics_ref": f"{trial_id}:concentration", "warning": "NONE"},
                        "subperiod_robustness": {"status": "AVAILABLE", "warning": "NONE"},
                        "regime_robustness": {"status": inputs["input_diagnostics"]["benchmark"], "warning": "BENCHMARK_NOT_VERIFIED"},
                        "baseline_result_comparison": {"status": "REPORT_ONLY", "warning": "BASELINE_COMPARISON_WARNING"},
                    }
                validation_row["input_diagnostics"] = inputs["input_diagnostics"]
                validation_rows.append(validation_row)
                p_values[candidate_id] = float(bootstrap.get("p_value", 1.0))
                provisional_path = provisional_evidence_dir / f"{trial_id}.json"
                provisional_payload = {
                    **validation_row,
                    "performance_completed": True,
                    "final_adjudication_pending": True,
                    "metrics_hashes": {
                        "base_metrics": stable_hash(base_metrics),
                        "small_capital_10k_metrics": stable_hash(ten_metrics),
                        "bootstrap": stable_hash(bootstrap),
                        "cost_stress": stable_hash(cost_stress),
                    },
                    "p_value": p_values[candidate_id],
                }
                _write(provisional_path, provisional_payload)
                orchestrator.trial_ledger.mark_provisional(
                    trial_id,
                    local_classification=local_classification,
                    evidence_ref=str(provisional_path.relative_to(root)),
                    result={"validator_classification": validator_classification, "performance_completed": True},
                )
                orchestrator.budget.consume(reservations[candidate_id])
                completed_trial_ids.append(trial_id)
                trial_summary = {"trial_id": trial_id, "candidate_id": candidate_id, "family_id": record.candidate.mechanism, "status": "PERFORMANCE_COMPLETE_PENDING_ADJUDICATION", "classification": None, "local_classification": local_classification, "validator_classification": validator_classification, "performance_accessed": True, "performance_completed": True, "final_adjudication_pending": True, "reason_codes": reason_codes, "candidate_hash": record.preregistration_hash, "validation_policy_id": plan.validation_policy_id, "validation_policy_version": plan.validation_policy_version, "validation_policy_hash": policy_hash, "decision_family_id": decision_family_id}
                trial_records.append(trial_summary)
                orchestrator.artifact_graph.add_node(f"trial:{trial_id}", "Trial", trial_summary)
                orchestrator.artifact_graph.add_node(f"validation:{trial_id}", "ValidationResult", {"classification": None, "local_classification": local_classification, "validator_classification": validator_classification, "final_adjudication_pending": True})
                orchestrator.artifact_graph.add_edge(f"trial:{trial_id}", "VALIDATED_BY", f"validation:{trial_id}")
                orchestrator.artifact_graph.add_edge(f"validation:{trial_id}", "CLASSIFIED_AS", f"candidate:{candidate_id}")
                _checkpoint(orchestrator, machine, plan, hypotheses, frozen_records, policy_hash=policy_hash, dataset_hash=dataset_hash, completed_trial_ids=completed_trial_ids, pending_trial_ids=[item["trial_id"] for item in trial_records if item.get("final_adjudication_pending")], engine_hash=engine_hash, trial_records=trial_records)
                del base_engine, ten_engine
            except Exception as exc:
                reason = f"ENGINEERING_BLOCKED:{type(exc).__name__}:{exc}"
                orchestrator.trial_ledger.mark_completed(trial_id, "ENGINEERING_BLOCKED", reason_codes=(reason,))
                orchestrator.budget.consume(reservations[candidate_id])
                summary = {"trial_id": trial_id, "candidate_id": candidate_id, "family_id": record.candidate.mechanism, "status": "COMPLETED", "classification": "ENGINEERING_BLOCKED", "validator_classification": "ENGINEERING_BLOCKED", "performance_accessed": True, "reason_codes": (reason,), "candidate_hash": record.preregistration_hash}
                trial_records.append(summary)
                break

        old_bootstrap_path = root / "reports/PHASE4_BOOTSTRAP_ENGINE_CORRECTED_V3.json"
        historical_p_values = _load_cumulative_private_p_values(root, report_dir.parent) if policy_v2_active else {}
        if not policy_v2_active and old_bootstrap_path.exists():
            historical_payload = json.loads(old_bootstrap_path.read_text(encoding="utf-8"))
            historical_p_values = {str(candidate_id): float(item.get("p_value", 1.0)) for candidate_id, item in historical_payload.get("candidates", {}).items()}
        merged_p_values = {**historical_p_values, **p_values}
        multiple_testing = benjamini_hochberg(merged_p_values, q=policy.fdr_q)
        history_snapshot_hash = stable_hash({"validation_policy_hash": policy_hash, "historical_trial_ids": sorted(historical_p_values), "current_batch_candidate_ids": sorted(p_values), "family_frozen_before_performance": True})
        multiple_testing.update({
            "decision_family_id": decision_family_id,
            "decision_denominator": int(multiple_testing["hypothesis_count"]),
            "current_batch_hypothesis_count": len(p_values),
            "cumulative_legal_history_denominator": int(multiple_testing["hypothesis_count"]),
            "history_snapshot_hash": history_snapshot_hash,
            "family_contract_hash": policy.multiple_testing_contract_hash if policy_v2_active else None,
            "historical_outcomes_loaded_only_after_performance_gate": True,
            "design_context_received_exact_outcomes": False,
        })
        _write(report_dir / "multiple_testing.json", multiple_testing)
        orchestrator.commit_ledger.ensure("multiple_testing_committed", decision_family_id, {"decision_family_id": decision_family_id, "q": policy.fdr_q, "p_values": dict(sorted(merged_p_values.items())), "family_contract_hash": policy.multiple_testing_contract_hash if policy_v2_active else None, "result_hash": stable_hash({"decision_family_id": decision_family_id, "q": policy.fdr_q, "p_values": dict(sorted(merged_p_values.items())), "family_contract_hash": policy.multiple_testing_contract_hash if policy_v2_active else None})})

        adjudicator = FinalResearchAdjudicatorV1(policy, policy_hash=policy_hash)
        candidate_hashes = {item.candidate.candidate_id: item.preregistration_hash for item in eligible_records}
        final_decisions: list[dict[str, Any]] = []
        trial_by_id = {str(item.get("trial_id")): item for item in trial_records}
        for row in validation_rows:
            trial_id = str(row["trial_id"])
            decision = adjudicator.adjudicate(
                candidate_id=str(row["candidate_id"]),
                candidate_hash=candidate_hashes[str(row["candidate_id"])],
                trial_id=trial_id,
                local_classification=str(row["local_classification"]),
                multiple_testing=multiple_testing,
                decision_family_id=decision_family_id,
                decision_denominator=int(multiple_testing["decision_denominator"]),
                history_snapshot_hash=history_snapshot_hash,
                metrics_ref=str((provisional_evidence_dir / f"{trial_id}.json").relative_to(root)),
                evidence={
                    "engine_integrity": row.get("engine_integrity"),
                    "gates": row.get("gates", {}),
                    "soft_evidence": row.get("soft_evidence", {}),
                    "gate_roles": ({key: item.get("role", "") for key, item in policy.gates.items()} if policy_v2_active else row.get("gate_roles", {})),
                    "small_capital_evidence_status": ("AVAILABLE_SOFT_EVIDENCE" if policy_v2_active else "AVAILABLE_REPORT_ONLY"),
                    "small_capital_contract_valid": bool(row.get("small_capital_contract", {}).get("fixed_contract")),
                },
            )
            decision_payload = decision.to_dict()
            final_decisions.append(decision_payload)
            orchestrator.commit_ledger.ensure("final_decision_committed", str(decision.decision_id), {"decision_id": decision.decision_id, "trial_id": trial_id, "decision_hash": stable_hash(decision_payload)})
            row["classification"] = decision.effective_classification
            row["final_adjudication_pending"] = False
            row["final_decision"] = decision_payload
            summary = trial_by_id[trial_id]
            summary.update({
                "status": "COMPLETED",
                "classification": decision.effective_classification,
                "final_adjudication_pending": False,
                "final_decision_id": decision.decision_id,
                "adjusted_p": decision.adjusted_p,
                "adjusted_support": decision.adjusted_support,
                "validation_policy_id": decision.validation_policy_id,
                "validation_policy_version": decision.validation_policy_version,
                "validation_policy_hash": decision.validation_policy_hash,
                "decision_family_id": decision.decision_family_id,
                "failure_category": decision.failure_category,
                "final_reason": decision.reason,
            })
            orchestrator.trial_ledger.mark_final_adjudication(
                trial_id,
                decision.effective_classification,
                decision_id=decision.decision_id,
                reason_codes=tuple(row.get("reason_codes", ())) + ((decision.reason,) if decision.reason else ()),
                result={"local_classification": decision.local_classification, "adjusted_support": decision.adjusted_support},
            )
            orchestrator.commit_ledger.ensure("trial_final_state_committed", trial_id, {"trial_id": trial_id, "decision_id": decision.decision_id, "classification": decision.effective_classification})
            if decision.effective_classification in {"REJECTED", "WEAK", "PROMISING", "RESEARCH_PASSED"}:
                orchestrator.strategy_registry.apply_classification(str(row["candidate_id"]), decision.effective_classification, evidence_ref=decision.decision_id)
            else:
                orchestrator.strategy_registry.transition(str(row["candidate_id"]), "VALIDATION_BLOCKED", evidence_ref=decision.decision_id)
            orchestrator.commit_ledger.ensure("registry_transition_committed", str(row["candidate_id"]), {"candidate_id": row["candidate_id"], "decision_id": decision.decision_id, "classification": decision.effective_classification})
            orchestrator.trial_ledger.mark_registry_committed(trial_id)
            orchestrator.artifact_graph.add_node(f"final-decision:{decision.decision_id}", "FinalResearchDecision", decision_payload)
            orchestrator.artifact_graph.add_edge(f"validation:{trial_id}", "FINAL_ADJUDICATED_AS", f"final-decision:{decision.decision_id}")

        _write(report_dir / "trial_manifest.json", {"trials": trial_records, "performance_accessed_before_metrics": True, "dataset_hash": dataset_hash, "policy_id": plan.validation_policy_id, "policy_version": plan.validation_policy_version, "policy_hash": policy_hash, "family_contract_hash": plan.family_contract_hash, "engine_hash": engine_hash, "final_decision_family_id": decision_family_id})
        _write(data_dir / "trial_manifest.json", trial_records)
        _write(report_dir / "validation_results.json", {"schema_version": "research-factory-validation-results-v1", "rows": validation_rows, "validator": "scripts/run_engine_corrected_phase4_v3.py::classification", "local_classifier_scope": "LOCAL_ONLY", "final_adjudicator": "FinalResearchAdjudicatorV1", "corrected_exit_engine": "PortfolioExitEvaluatorV1", "final_test_access": {"physical": 0, "analytical": 0, "decision": 0}})

        has_engine_failure = any(str(item.get("classification")) in {"ENGINEERING_BLOCKED", "INVALID"} for item in trial_records) or any(item.get("effective_classification") == "INVALID" for item in final_decisions)
        if machine.state == ResearchBatchState.PERFORMANCE_VALIDATING:
            if has_engine_failure:
                machine.transition(ResearchBatchState.ENGINEERING_BLOCKED, "final adjudication detected an engineering/data invariant failure")
            else:
                machine.transition(ResearchBatchState.CLASSIFYING, "multiple testing completed; FinalResearchAdjudicatorV1 is authoritative")
                machine.transition(ResearchBatchState.FAILURE_EXTRACTING, "final classifications committed; extracting high-level failure knowledge")
                machine.transition(ResearchBatchState.COMPLETED, "Batch 1 artifacts committed; no Batch 2 plan generated")
        failure_records = []
        for record in trial_records:
            enriched = dict(record)
            row = next((item for item in validation_rows if item.get("trial_id") == record.get("trial_id")), None)
            if row:
                enriched.update({key: row[key] for key in ("base_metrics", "bootstrap", "cost_stress", "validator_classification") if key in row})
            failure_records.append(enriched)
        failure_snapshot = orchestrator.failure_adapter.snapshot_from_trials(failure_records, snapshot_id=f"{plan.batch_id}_FAILURE_SNAPSHOT", parent_snapshot_id=plan.failure_knowledge_snapshot_id)
        _write(report_dir / "failure_extraction.json", failure_snapshot.to_dict())
        orchestrator.commit_ledger.ensure("failure_knowledge_committed", failure_snapshot.snapshot_id, {"snapshot_id": failure_snapshot.snapshot_id, "snapshot_hash": stable_hash({"snapshot_id": failure_snapshot.snapshot_id, "parent_snapshot_id": failure_snapshot.parent_snapshot_id, "entries": [item.to_dict() for item in failure_snapshot.entries]})})
        for record in trial_records:
            if record.get("classification") in {"REJECTED", "WEAK", "ENGINEERING_BLOCKED", "INVALID"}:
                trial_node = f"trial:{record['trial_id']}"
                if trial_node not in orchestrator.artifact_graph.nodes:
                    orchestrator.artifact_graph.add_node(trial_node, "Trial", record)
                failure_node = f"failure:{failure_snapshot.snapshot_id}"
                if failure_node not in orchestrator.artifact_graph.nodes:
                    orchestrator.artifact_graph.add_node(failure_node, "FailureKnowledge", failure_snapshot.to_dict())
                orchestrator.artifact_graph.add_edge(trial_node, "FAILED_BECAUSE", failure_node)
        orchestrator.artifact_graph.add_node(f"failure:{failure_snapshot.snapshot_id}", "FailureKnowledge", failure_snapshot.to_dict())
        for record in eligible_records:
            candidate_id = record.candidate.candidate_id
            if candidate_id in orchestrator.strategy_registry._records:
                strategy = orchestrator.strategy_registry._records[candidate_id]
                orchestrator.artifact_graph.add_node(f"strategy:{candidate_id}", "Strategy", strategy.to_dict())
                orchestrator.artifact_graph.add_edge(f"strategy:{candidate_id}", "FINAL_ADJUDICATION_COMMITTED", f"candidate:{candidate_id}")
        _write(report_dir / "strategy_registry_update.json", {"records": [item.to_dict() for item in orchestrator.strategy_registry.records()], "existing_passed_candidate_untouched": True, "promotion_state": "DISABLED", "final_adjudicator": "FinalResearchAdjudicatorV1", "final_decisions": final_decisions})

        for record in trial_records:
            orchestrator.history = orchestrator.history.record_trial(record)
        _write(report_dir / "cumulative_history.json", orchestrator.history.to_dict())
        orchestrator.artifact_graph.add_node(f"failure:{failure_snapshot.snapshot_id}", "FailureKnowledge", failure_snapshot.to_dict())
        _write(report_dir / "artifact_graph.json", {**orchestrator.artifact_graph.to_dict(), "integrity": orchestrator.artifact_graph.integrity(), "graph_hash": orchestrator.artifact_graph.graph_hash})
        orchestrator.commit_ledger.ensure("artifact_graph_committed", plan.batch_id, {"batch_id": plan.batch_id, "graph_hash": orchestrator.artifact_graph.graph_hash})
        for record in trial_records:
            orchestrator.commit_ledger.ensure("history_update_committed", str(record.get("trial_id")), {"trial_id": record.get("trial_id"), "final_decision_id": record.get("final_decision_id")})
        checkpoint = _checkpoint(orchestrator, machine, plan, hypotheses, frozen_records, policy_hash=policy_hash, dataset_hash=dataset_hash, completed_trial_ids=completed_trial_ids, engine_hash=engine_hash, trial_records=trial_records, failure_snapshot=failure_snapshot, stop_reason=f"BATCH_{batch_number}_STOP_MAX_BATCHES")
        orchestrator.commit_ledger.ensure("batch_terminal_committed", plan.batch_id, {"batch_id": plan.batch_id, "state": machine.state.value, "checkpoint": str(checkpoint)})
        resume = orchestrator.resume_from_checkpoint(checkpoint)
        integration = {
            "schema_version": "FACTORY_REAL_RUN_INTEGRATION_AUDIT_V1",
            "factory_entrypoint": "AIResearchFactoryOrchestratorV1.run(synthetic=False)",
            "validation_policy_id": plan.validation_policy_id,
            "validation_policy_version": plan.validation_policy_version,
            "validation_policy_hash": policy_hash,
            "family_contract_hash": plan.family_contract_hash,
            "policy_resolved_before_performance_access_gate": True,
            "real_performance_trial_executed": bool(completed_trial_ids),
            "canonical_components": dict(RESEARCH_FACTORY_CANONICAL_DEPENDENCIES_V1["dependencies"]),
            "budget_reserved_before_performance": True,
            "trial_registration_before_performance": True,
            "stage1_before_performance": True,
            "corrected_engine_path": "BacktestEngineV2 + PortfolioExitEvaluatorV1",
            "small_capital_independent_run": True,
            "resume": resume,
            "completed_trial_not_rerun": resume.get("performance_rerun") is False,
            "final_test_access": {"physical": 0, "analytical": 0, "decision": 0},
            "prospective_observation": 0,
            "recommendation": "DISABLED",
            "real_order_execution": "DISABLED",
        }
        _write(report_dir / "factory_integration_audit.json", integration)
        final_acceptance = machine.state == ResearchBatchState.COMPLETED and integration["completed_trial_not_rerun"] and orchestrator.artifact_graph.integrity()["status"] == "PASS"
        _write(report_dir / "batch_acceptance.json", {
            "status": "PASS" if final_acceptance else "BLOCKED",
            "batch_count_executed": 1,
            "max_batches": orchestrator.objective.max_batches,
            "max_hypotheses": plan.max_hypotheses,
            "max_frozen_candidates": plan.max_candidates,
            "max_real_performance_trials": plan.max_performance_trials,
            "actual_performance_trials": len(completed_trial_ids),
            "factory_integration_acceptance": final_acceptance,
            "validation_policy_id": plan.validation_policy_id,
            "validation_policy_version": plan.validation_policy_version,
            "validation_policy_hash": policy_hash,
            "candidate_unchanged_after_performance": True,
            "existing_passed_candidate_unchanged": True,
            "prospective_data_observation": 0,
            "final_test_access": {"physical": 0, "analytical": 0, "decision": 0},
            "recommendation": "DISABLED",
            "real_order_execution": "DISABLED",
        })
        return self._finish(orchestrator, plan, machine, hypotheses, frozen_records, trial_records, failure_snapshot, checkpoint, report_dir, data_dir, stop_reason=f"BATCH_{batch_number}_STOP_MAX_BATCHES", real_performance=bool(completed_trial_ids), final_acceptance=final_acceptance)

    def _resume_pending_adjudication(self, orchestrator: Any, plan: ResearchBatchPlanV1, checkpoint_payload: Mapping[str, Any], *, policy: Any, policy_hash: str, report_dir: Path, data_dir: Path):
        """Finish a crashed batch from saved provisional evidence without engine access."""
        from .orchestrator import FactoryRunResultV1
        from chanlun_trader.research.strategy_validation import FinalResearchAdjudicatorV1, benjamini_hochberg

        pending_ids = [str(item) for item in checkpoint_payload.get("pending_trial_ids", ())]
        provisional_dir = data_dir / str(checkpoint_payload.get("provisional_evidence_dir", "provisional_validation_evidence"))
        latest = orchestrator.trial_ledger.latest()
        provisional: dict[str, dict[str, Any]] = {}
        for trial_id in pending_ids:
            path = provisional_dir / f"{trial_id}.json"
            if not path.exists():
                raise RuntimeError(f"missing provisional evidence for resume: {trial_id}")
            provisional[trial_id] = json.loads(path.read_text(encoding="utf-8"))

        candidates = list(checkpoint_payload.get("candidates", ()))
        candidate_metas: list[dict[str, Any]] = []
        candidate_hashes: dict[str, str] = {}
        for item in candidates:
            nested = item.get("candidate", {}) if isinstance(item, Mapping) else {}
            candidate_id = str((nested or {}).get("candidate_id") or item.get("candidate_id"))
            candidate_hash = str(item.get("preregistration_hash") or item.get("candidate_hash") or (nested or {}).get("candidate_preregistration_hash") or "UNKNOWN")
            family_id = str((nested or {}).get("mechanism") or item.get("mechanism") or "UNKNOWN")
            candidate_metas.append({"candidate_id": candidate_id, "family_id": family_id, "candidate_hash": candidate_hash})
            candidate_hashes[candidate_id] = candidate_hash
        orchestrator._register_budget(plan, candidate_metas)
        for candidate in candidate_metas:
            candidate_id = candidate["candidate_id"]
            if candidate_id not in orchestrator.strategy_registry._records:
                orchestrator.strategy_registry.register(candidate_id, candidate["candidate_hash"], candidate["family_id"])
                orchestrator.strategy_registry.transition(candidate_id, "SEMANTIC_READY")
                orchestrator.strategy_registry.transition(candidate_id, "VALIDATION_ELIGIBLE")
        for trial_id in pending_ids:
            record = latest[trial_id]
            if orchestrator.budget.used("candidate", record.candidate_id) == 0:
                reservation = orchestrator.budget.reserve_trial(batch_id=record.batch_id, family_id=record.family_id, candidate_id=record.candidate_id)
                orchestrator.budget.consume(reservation)

        old_bootstrap_path = orchestrator.root / "reports/PHASE4_BOOTSTRAP_ENGINE_CORRECTED_V3.json"
        policy_v2_active = getattr(policy, "policy_id", "") == "VALIDATION_DECISION_POLICY_V2"
        historical_p_values: dict[str, float] = _load_cumulative_private_p_values(root, report_dir.parent) if policy_v2_active else {}
        if not policy_v2_active and old_bootstrap_path.exists():
            historical_payload = json.loads(old_bootstrap_path.read_text(encoding="utf-8"))
            historical_p_values = {str(key): float(value.get("p_value", 1.0)) for key, value in historical_payload.get("candidates", {}).items()}
        current_p_values = {str(row["candidate_id"]): float(row.get("p_value", 1.0)) for row in provisional.values()}
        multiple_testing = benjamini_hochberg({**historical_p_values, **current_p_values}, q=policy.fdr_q)
        decision_family_id = str(checkpoint_payload.get("decision_family_id") or (policy.family_id(orchestrator.objective.objective_id, plan.batch_id) if policy_v2_active else f"{plan.batch_id}:CUMULATIVE_LEGAL_FAMILY_V1"))
        history_snapshot_hash = stable_hash({"validation_policy_hash": policy_hash, "historical_trial_ids": sorted(historical_p_values), "current_trial_ids": sorted(current_p_values), "family_frozen_before_performance": True})
        multiple_testing.update({"decision_family_id": decision_family_id, "decision_denominator": int(multiple_testing["hypothesis_count"]), "current_batch_hypothesis_count": len(current_p_values), "cumulative_legal_history_denominator": int(multiple_testing["hypothesis_count"]), "history_snapshot_hash": history_snapshot_hash, "family_contract_hash": policy.multiple_testing_contract_hash if policy_v2_active else None, "resumed_from_provisional_evidence": True, "performance_rerun": False})
        adjudicator = FinalResearchAdjudicatorV1(policy, policy_hash=policy_hash)
        trial_records: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        for trial_id in pending_ids:
            row = provisional[trial_id]
            candidate_id = str(row["candidate_id"])
            decision = adjudicator.adjudicate(
                candidate_id=candidate_id,
                candidate_hash=candidate_hashes.get(candidate_id, latest[trial_id].candidate_hash),
                trial_id=trial_id,
                local_classification=str(row.get("local_classification") or row.get("validator_classification")),
                multiple_testing=multiple_testing,
                decision_family_id=decision_family_id,
                decision_denominator=int(multiple_testing["decision_denominator"]),
                history_snapshot_hash=history_snapshot_hash,
                metrics_ref=str((provisional_dir / f"{trial_id}.json").relative_to(root)),
                evidence={"engine_integrity": row.get("engine_integrity"), "gates": row.get("gates", {}), "soft_evidence": row.get("soft_evidence", {}), "gate_roles": ({key: item.get("role", "") for key, item in policy.gates.items()} if policy_v2_active else row.get("gate_roles", {})), "small_capital_evidence_status": ("AVAILABLE_SOFT_EVIDENCE" if policy_v2_active else "AVAILABLE_REPORT_ONLY"), "small_capital_contract_valid": bool(row.get("small_capital_contract", {}).get("fixed_contract"))},
            )
            decisions.append(decision.to_dict())
            orchestrator.trial_ledger.mark_final_adjudication(trial_id, decision.effective_classification, decision_id=decision.decision_id, reason_codes=(decision.reason,), result={"resumed": True, "adjusted_support": decision.adjusted_support})
            record = orchestrator.trial_ledger.latest()[trial_id].to_dict()
            record.update({"classification": decision.effective_classification, "local_classification": decision.local_classification, "final_decision": decision.to_dict(), "final_decision_id": decision.decision_id, "failure_category": decision.failure_category, "final_reason": decision.reason, "reason_codes": ((decision.reason,) if decision.reason else ()), "performance_completed": True, "final_adjudication_pending": False})
            trial_records.append(record)
            if decision.effective_classification in LEGAL_CLASSIFICATIONS:
                orchestrator.strategy_registry.apply_classification(candidate_id, decision.effective_classification, evidence_ref=decision.decision_id)
            else:
                orchestrator.strategy_registry.transition(candidate_id, "VALIDATION_BLOCKED", evidence_ref=decision.decision_id)

        failure_records = []
        for record in trial_records:
            enriched = dict(record)
            row = provisional[record["trial_id"]]
            enriched.update({key: row[key] for key in ("base_metrics", "bootstrap", "cost_stress", "validator_classification") if key in row})
            failure_records.append(enriched)
        failure_snapshot = orchestrator.failure_adapter.snapshot_from_trials(failure_records, snapshot_id=f"{plan.batch_id}_FAILURE_SNAPSHOT_RESUMED_V1", parent_snapshot_id=plan.failure_knowledge_snapshot_id)
        resume_report_dir = report_dir / "resume_correction_v1"
        _write(resume_report_dir / "multiple_testing.json", multiple_testing)
        _write(resume_report_dir / "validation_results.json", {"rows": [{**provisional[trial_id], "classification": decisions[index]["effective_classification"], "final_decision": decisions[index]} for index, trial_id in enumerate(pending_ids)], "final_adjudicator": "FinalResearchAdjudicatorV1", "performance_rerun": False})
        _write(resume_report_dir / "trial_manifest.json", {"trials": trial_records, "performance_rerun": False, "final_adjudication_resumed": True})
        _write(resume_report_dir / "failure_extraction.json", failure_snapshot.to_dict())
        _write(resume_report_dir / "strategy_registry_update.json", {"records": [item.to_dict() for item in orchestrator.strategy_registry.records()], "final_adjudicator": "FinalResearchAdjudicatorV1", "commit_count": 1})

        machine = ResearchBatchStateMachineV1(plan.batch_id, initial_state=ResearchBatchState.PERFORMANCE_VALIDATING, path=orchestrator.output_dir / "state" / f"{plan.batch_id}_resume.json")
        if any(item["effective_classification"] == "INVALID" for item in decisions):
            machine.transition(ResearchBatchState.ENGINEERING_BLOCKED, "resumed final adjudication detected an engineering failure")
        else:
            machine.transition(ResearchBatchState.CLASSIFYING, "resumed BH completed; final adjudication committed")
            machine.transition(ResearchBatchState.FAILURE_EXTRACTING, "resumed final classifications committed")
            machine.transition(ResearchBatchState.COMPLETED, "resumed batch commit complete")
        checkpoint = _checkpoint(orchestrator, machine, plan, checkpoint_payload.get("hypotheses", ()), candidates, policy_hash=policy_hash, dataset_hash=str(checkpoint_payload.get("dataset_hash", "RESUMED_FROM_PROVISIONAL")), completed_trial_ids=pending_ids, pending_trial_ids=(), engine_hash=str(checkpoint_payload.get("engine_hash", "RESUMED_FROM_PROVISIONAL")), trial_records=trial_records, failure_snapshot=failure_snapshot, stop_reason="RESUMED_FINAL_ADJUDICATION")
        counts = Counter(str(item.get("classification")) for item in trial_records)
        status = ResearchFactoryStatusV1(
            objective_id=orchestrator.objective.objective_id, current_batch_id=plan.batch_id, batch_state=machine.state.value, batch_number=getattr(plan, "batch_number", 1), max_batches=orchestrator.objective.max_batches, trial_budget_total=orchestrator.objective.max_total_trials, trial_budget_used=orchestrator.budget.used("objective", orchestrator.objective.objective_id), hypotheses_count=len(checkpoint_payload.get("hypotheses", ())), candidate_count=len(candidates), trials_started=len(trial_records), trials_completed=len(trial_records), research_passed_count=counts.get("RESEARCH_PASSED", 0), promising_count=counts.get("PROMISING", 0), weak_count=counts.get("WEAK", 0), rejected_count=counts.get("REJECTED", 0), blocked_count=counts.get("INVALID", 0), failure_class_counts=dict(Counter(item.category for item in failure_snapshot.entries)), strategy_registry_counts=orchestrator.strategy_registry.counts(), stop_reason="RESUMED_FINAL_ADJUDICATION",
        )
        _write(resume_report_dir / "final_status.json", {"status": "COMPLETE" if machine.state == ResearchBatchState.COMPLETED else machine.state.value, "performance_rerun": False, "multiple_testing": "RUN", "final_adjudication": "RUN", "strategy_registry_commit": "ONCE", "final_test_access": {"physical": 0, "analytical": 0, "decision": 0}})
        return FactoryRunResultV1(orchestrator.objective, plan, machine.state.value, tuple(checkpoint_payload.get("hypotheses", ())), tuple(candidates), tuple(trial_records), failure_snapshot, status, str(checkpoint), None, True, False, {"physical": 0, "analytical": 0, "decision": 0}, "DISABLED", "DISABLED")

    def _finish(self, orchestrator: Any, plan: ResearchBatchPlanV1, machine: ResearchBatchStateMachineV1, hypotheses: Sequence[Any], candidates: Sequence[Any], trials: Sequence[Mapping[str, Any]], failure_snapshot: FailureKnowledgeSnapshotV1, checkpoint: Path, report_dir: Path, data_dir: Path, *, stop_reason: str, real_performance: bool, final_acceptance: bool):
        from .orchestrator import FactoryRunResultV1

        counts = Counter(str(item.get("classification")) for item in trials)
        sample_counts = Counter(str(item.get("status")) for item in getattr(orchestrator, "_sample_feasibility_rows", ()))
        status = ResearchFactoryStatusV1(
            objective_id=orchestrator.objective.objective_id,
            current_batch_id=plan.batch_id,
            batch_state=machine.state.value,
            batch_number=getattr(plan, "batch_number", 1),
            max_batches=orchestrator.objective.max_batches,
            trial_budget_total=orchestrator.objective.max_total_trials,
            trial_budget_used=orchestrator.budget.used("objective", orchestrator.objective.objective_id),
            hypotheses_count=len(hypotheses),
            candidate_count=len(candidates),
            trials_started=len(trials),
            trials_completed=sum(item.get("status") == "COMPLETED" for item in trials),
            research_passed_count=counts.get("RESEARCH_PASSED", 0),
            promising_count=counts.get("PROMISING", 0),
            weak_count=counts.get("WEAK", 0),
            rejected_count=counts.get("REJECTED", 0),
            blocked_count=counts.get("ENGINEERING_BLOCKED", 0) + counts.get("INSUFFICIENT_EVIDENCE", 0),
            candidates_sample_feasibility_passed=sample_counts.get("PASS", 0),
            candidates_sample_feasibility_blocked=sample_counts.get("BLOCKED_INSUFFICIENT_FEASIBILITY", 0),
            candidates_sample_feasibility_unknown=sample_counts.get("UNKNOWN", 0),
            failure_class_counts=dict(Counter(item.category for item in failure_snapshot.entries)),
            strategy_registry_counts=orchestrator.strategy_registry.counts(),
            stop_reason=stop_reason,
        )
        final_status = {
            "RUN_NEXT_RESEARCH_BATCH_STATUS": "COMPLETE" if final_acceptance else machine.state.value,
            "factory_real_run": "YES" if real_performance else "NO",
            "integration_acceptance": "PASS" if final_acceptance else "BLOCKED",
            "engine_integrity": "PASS" if machine.state == ResearchBatchState.COMPLETED else "BLOCKED",
            "candidate_unchanged_after_performance": True,
            "existing_passed_candidate_unchanged": True,
            "prospective_data_observation": 0,
            "final_test_access": {"physical": 0, "analytical": 0, "decision": 0},
            "recommendation": "DISABLED",
            "real_order_execution": "DISABLED",
            "batch_count_executed": 1,
            "next_batch_plan": None,
            "stop_reason": stop_reason,
            "status_projection": status.to_dict(),
        }
        _write(report_dir / "final_status.json", final_status)
        _write(data_dir / "checkpoint.json", json.loads(checkpoint.read_text(encoding="utf-8")))
        return FactoryRunResultV1(
            orchestrator.objective,
            plan,
            machine.state.value,
            tuple(item.to_dict() for item in hypotheses),
            tuple(item.to_dict() for item in candidates),
            tuple(dict(item) for item in trials),
            failure_snapshot,
            status,
            str(checkpoint),
            None,
            real_performance_trial_executed=real_performance,
            prospective_observation_executed=False,
            final_test_access={"physical": 0, "analytical": 0, "decision": 0},
            recommendation="DISABLED",
            real_order_execution="DISABLED",
        )
