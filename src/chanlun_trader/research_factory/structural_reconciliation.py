"""Safely reconcile a repaired structural PASS without entering prediction."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from ..research_daemon import CandidateWork, CanonicalResearchRuntime, ResearchDaemon, StructuralResult
from ..research_daemon_state import DaemonCheckpointStoreV1, DaemonInstanceLockV1, ResearchDaemonState
from .artifact_graph import ResearchArtifactGraphV1
from .common import now_timestamp, stable_hash


RECONCILIATION_SCHEMA_VERSION = "structural-preflight-reconciliation-v1"
RECONCILIATION_REASON = "STRUCTURAL_PASS_RECONCILED_WITHOUT_PREDICTIVE_RUN"
CANONICAL_RECONCILIATION_FILENAME = "structural_preflight_reconciliation_canonical_v1.json"
RECONCILIATION_HISTORY_FILENAME = "structural_preflight_reconciliation_history.json"
STRUCTURAL_GOVERNANCE_FILENAME = "structural_governance_decision_required.json"
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate(runtime: Any, candidate_id: str) -> CandidateWork:
    contracts = getattr(runtime, "_contracts", {})
    if candidate_id in contracts:
        return contracts[candidate_id]
    for candidate in getattr(runtime, "candidates", ()):
        if candidate.candidate_id == candidate_id:
            return candidate
    raise RuntimeError(f"canonical frozen candidate not found: {candidate_id}")


def _trial_events(root: Path, objective_id: str, candidate_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in (root / "data/research/research_factory/batches").glob("*/factory_trial_ledger.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for event in payload.get("events", ()):
            if str(event.get("objective_id")) == objective_id and str(event.get("candidate_id")) == candidate_id:
                events.append(dict(event))
    return events


def _structural_payload(result: StructuralResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "reason_code": result.reason_code,
        "artifact_refs": list(result.artifact_refs),
        "provider_checkpoint": result.provider_checkpoint,
        "partition_index": result.partition_index,
        "details": dict(result.details),
    }


def _relative_ref(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _report_summary(root: Path, path: Path) -> dict[str, Any]:
    """Read only structural summary metadata for a preserved generation."""

    item: dict[str, Any] = {
        "report_ref": _relative_ref(root, path),
        "exists": path.is_file(),
    }
    if not path.is_file():
        return item
    item["sha256"] = _sha256(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        item["readable"] = False
        return item
    item["readable"] = True
    item["schema_version"] = payload.get("schema_version")
    item["status"] = payload.get("status")
    item["objective_id"] = payload.get("objective_id")
    item["candidate_id"] = payload.get("candidate_id")
    item["candidate_hash"] = payload.get("candidate_hash")
    repaired = payload.get("repaired_structural_result") if isinstance(payload.get("repaired_structural_result"), dict) else payload
    details = repaired.get("details") if isinstance(repaired.get("details"), dict) else repaired
    v1 = details.get("v1_result") if isinstance(details.get("v1_result"), dict) else payload.get("v1_result") if isinstance(payload.get("v1_result"), dict) else {}
    integrity = details.get("lower_bound_integrity") if isinstance(details.get("lower_bound_integrity"), dict) else payload.get("lower_bound_integrity") if isinstance(payload.get("lower_bound_integrity"), dict) else {}
    item.update({
        "lower_bound": v1.get("lower_bound_count", payload.get("lower_bound")),
        "upper_bound": v1.get("upper_bound_count", payload.get("upper_bound")),
        "minimum_required": v1.get("minimum_required_count", payload.get("minimum_required")),
        "lower_bound_integrity": integrity.get("status"),
    })
    return item


def _summary_matches_candidate_scope(
    summary: Mapping[str, Any],
    *,
    objective_id: str,
    candidate: CandidateWork,
) -> bool:
    """Return whether structural lineage evidence belongs to this candidate.

    Global historical reports are intentionally *not* accepted merely because
    they exist.  Candidate identity is the minimum lineage key; when a report
    also carries objective/hash identity those values must agree as well.
    This prevents an older governed round from being attached to a newer
    candidate's canonical reconciliation.
    """

    if str(summary.get("candidate_id") or "") != candidate.candidate_id:
        return False
    report_hash = str(summary.get("candidate_hash") or "")
    if report_hash and report_hash != candidate.candidate_hash:
        return False
    report_objective = str(summary.get("objective_id") or "")
    if report_objective and report_objective != objective_id:
        return False
    return True


def _sanitize_existing_reconciliation_lineage(
    root: Path,
    report_path: Path,
    *,
    objective_id: str,
    candidate: CandidateWork,
) -> dict[str, Any]:
    """Remove lineage entries that do not belong to the current candidate.

    This is deliberately metadata-only.  It preserves the already-canonical
    reconciliation identity and structural counts, and it never re-enters the
    Provider or predictive paths.  Older builds appended a fixed set of global
    structural reports, so an otherwise-correct PASS can contain evidence from
    a previous governed objective/candidate.
    """

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if str(payload.get("objective_id") or "") != objective_id:
        raise RuntimeError("canonical structural reconciliation objective mismatch")
    if str(payload.get("candidate_id") or "") != candidate.candidate_id:
        raise RuntimeError("canonical structural reconciliation candidate mismatch")
    if str(payload.get("candidate_hash") or "") != candidate.candidate_hash:
        raise RuntimeError("canonical structural reconciliation candidate hash mismatch")

    lineage = dict(payload.get("lineage") or {})
    evidence_before = [dict(item) for item in lineage.get("evidence_chain", ()) if isinstance(item, Mapping)]
    historical_before = [
        dict(item)
        for item in lineage.get("historical_reconciliation_generations", ())
        if isinstance(item, Mapping)
    ]
    evidence_after = [
        item
        for item in evidence_before
        if _summary_matches_candidate_scope(item, objective_id=objective_id, candidate=candidate)
    ]
    historical_after = [
        item
        for item in historical_before
        if _summary_matches_candidate_scope(item, objective_id=objective_id, candidate=candidate)
    ]
    if evidence_after == evidence_before and historical_after == historical_before:
        return payload

    removed_refs = sorted({
        str(item.get("report_ref"))
        for item in [*evidence_before, *historical_before]
        if item.get("report_ref")
        and not _summary_matches_candidate_scope(item, objective_id=objective_id, candidate=candidate)
    })
    lineage["evidence_chain"] = evidence_after
    lineage["historical_reconciliation_generations"] = historical_after
    lineage["scope_reconciliation"] = {
        "status": "PASS",
        "policy": "CURRENT_OBJECTIVE_AND_CANDIDATE_IDENTITY_ONLY",
        "removed_report_refs": removed_refs,
        "structural_result_changed": False,
        "predictive_run_started": False,
        "reconciled_at": now_timestamp(),
    }
    payload["lineage"] = lineage
    DaemonCheckpointStoreV1._atomic_write(report_path, payload)

    history_path = report_path.parent / RECONCILIATION_HISTORY_FILENAME
    if history_path.is_file():
        history = json.loads(history_path.read_text(encoding="utf-8"))
        report_ref = _relative_ref(root, report_path)
        changed = False
        for generation in history.get("generations", ()):
            if not isinstance(generation, dict):
                continue
            if (
                str(generation.get("report_ref") or "") == report_ref
                and str(generation.get("reconciliation_id") or "") == str(payload.get("reconciliation_id") or "")
            ):
                generation["sha256"] = _sha256(report_path)
                changed = True
        if changed:
            history["updated_at"] = now_timestamp()
            DaemonCheckpointStoreV1._atomic_write(history_path, history)
    return payload


def _preserved_generation_summaries(root: Path, runtime_dir: Path) -> list[dict[str, Any]]:
    paths = [
        runtime_dir / "structural_preflight_reconciliation.json",
        runtime_dir / "structural_preflight_reconciliation_repaired_v1.json",
    ]
    generations: list[dict[str, Any]] = []
    labels = ("INITIAL_ORIGINAL", "REPAIRED_UNKNOWN_BOUND")
    for label, path in zip(labels, paths):
        if not path.is_file():
            continue
        summary = _report_summary(root, path)
        summary["generation"] = label
        generations.append(summary)
    return generations


def _update_artifact_graph(
    root: Path,
    *,
    objective_id: str,
    candidate: CandidateWork,
    reconciliation_id: str,
    structural: Mapping[str, Any],
) -> dict[str, Any]:
    """Append the current structural result to the existing graph idempotently."""

    graph_path = root / "data/research/research_factory/artifact_graph" / f"{objective_id}.json"
    if not graph_path.is_file():
        return {"status": "NOT_AVAILABLE", "graph_ref": _relative_ref(root, graph_path)}
    graph = ResearchArtifactGraphV1(graph_path)
    candidate_node_id = f"candidate:{candidate.candidate_id}"
    contract_node_id = f"frozen_contract:{candidate.candidate_id}:{candidate.candidate_hash}"
    structural_node_id = f"structural_preflight:{reconciliation_id}"
    graph.add_node(candidate_node_id, "Candidate", {
        "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate.candidate_hash,
        "contract_ref": candidate.contract_ref,
        "mechanism": candidate.mechanism,
    })
    raw_contract = dict(candidate.metadata.get("raw_contract") or {})
    graph.add_node(contract_node_id, "FrozenCandidateContract", {
        "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate.candidate_hash,
        "contract_ref": candidate.contract_ref,
        "content_hash": raw_contract.get("content_hash"),
        "contract_schema_version": raw_contract.get("contract_schema_version"),
    })
    graph.add_edge(candidate_node_id, "HAS_DURABLE_CONTRACT", contract_node_id)
    graph.add_node(structural_node_id, "StructuralPreflight", {
        "objective_id": objective_id,
        "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate.candidate_hash,
        "reconciliation_id": reconciliation_id,
        "status": structural.get("status"),
        "lower_bound": structural.get("lower_bound"),
        "upper_bound": structural.get("upper_bound"),
        "minimum_required": structural.get("minimum_required"),
        "lower_bound_integrity": structural.get("lower_bound_integrity"),
    })
    objective_node_id = f"objective:{objective_id}"
    if objective_node_id in graph.nodes:
        graph.add_edge(objective_node_id, "VALIDATED_BY", structural_node_id)
    graph.add_edge(structural_node_id, "RECONCILED_AS", candidate_node_id)
    return {
        "status": "PASS",
        "graph_ref": _relative_ref(root, graph_path),
        "graph_hash": graph.graph_hash,
        "integrity": graph.integrity(),
        "node_ids": [candidate_node_id, contract_node_id, structural_node_id],
        "edge_refs": [
            {"source_id": candidate_node_id, "edge_type": "HAS_DURABLE_CONTRACT", "target_id": contract_node_id},
            {"source_id": objective_node_id, "edge_type": "VALIDATED_BY", "target_id": structural_node_id},
            {"source_id": structural_node_id, "edge_type": "RECONCILED_AS", "target_id": candidate_node_id},
        ],
    }


def _write_structural_governance(
    root: Path,
    *,
    objective_id: str,
    candidate: CandidateWork,
    report_ref: str,
    reconciliation_id: str,
    structural: Mapping[str, Any],
) -> dict[str, Any]:
    """Publish a read-only governance readiness projection; never authorize a Trial."""

    path = root / "reports/research_orchestrator_v2" / objective_id / STRUCTURAL_GOVERNANCE_FILENAME
    payload = {
        "schema_version": "structural-governance-decision-required-v1",
        "objective_id": objective_id,
        "decision_mode": "STRUCTURAL_PASS_PREDICTIVE_AUTHORIZATION_REQUIRED",
        "status": "PENDING_HUMAN_DECISION",
        "decision_id": stable_hash({"objective_id": objective_id, "reconciliation_id": reconciliation_id, "mode": "STRUCTURAL_PASS_PREDICTIVE_AUTHORIZATION_REQUIRED"}),
        "reconciliation_id": reconciliation_id,
        "reconciliation_ref": report_ref,
        "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate.candidate_hash,
        "structural": {
            "status": structural.get("status"),
            "lower_bound": structural.get("lower_bound"),
            "upper_bound": structural.get("upper_bound"),
            "minimum_required": structural.get("minimum_required"),
            "lower_bound_integrity": structural.get("lower_bound_integrity"),
        },
        "choices": [
            {
                "choice": "AUTHORIZE_FIRST_PREDICTIVE_TRIAL",
                "label_zh": "授权进入第 1 次预测试验",
                "consequence_zh": "只记录进入第 1 次预测试验的授权，不创建 Trial、不预留预算；之后仍需单独启动第 1 次预测试验。",
                "creates_trial": False,
                "creates_authorization": True,
                "requires_explicit_confirmation": True,
            },
            {
                "choice": "DEFER_PREDICTIVE_TRIAL",
                "label_zh": "暂不启动预测试验",
                "consequence_zh": "保持结构 PASS、Trial 数为 0，等待后续治理决定。",
                "creates_trial": False,
                "requires_explicit_confirmation": True,
            },
            {
                "choice": "END_CANDIDATE_RESEARCH_DIRECTION",
                "label_zh": "结束当前 Candidate / research direction",
                "consequence_zh": "结束当前方向，不创建预测 Trial。",
                "creates_trial": False,
                "requires_explicit_confirmation": True,
            },
        ],
        "next_action_zh": "等待研究治理决定是否启动第 1 次预测试验。",
        "human_explanation_zh": "结构预检已通过，等待决定是否进入正式预测验证。",
        "final_test_access": {"analytical": 0, "decision": 0, "physical": 0},
        "prospective": "DISABLED",
        "real_order": "DISABLED",
        "predictive_trials_created": 0,
        "performance_access": 0,
        "created_at": now_timestamp(),
    }
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        existing_structural = existing.get("structural") if isinstance(existing.get("structural"), dict) else {}
        if existing_structural.get("status") is None and existing.get("decision_mode") == payload["decision_mode"] and existing.get("reconciliation_id") == payload["reconciliation_id"]:
            existing["structural"] = {**existing_structural, "status": payload["structural"]["status"]}
            DaemonCheckpointStoreV1._atomic_write(path, existing)
            return existing
        if stable_hash({key: existing.get(key) for key in payload if key not in {"created_at"}}) != stable_hash({key: payload.get(key) for key in payload if key not in {"created_at"}}):
            raise RuntimeError("structural governance projection identity conflict")
        return existing
    DaemonCheckpointStoreV1._atomic_write(path, payload)
    return payload


def persist_governed_structural_pass_boundary(
    root: str | Path,
    *,
    objective_id: str,
    candidate: CandidateWork,
    result: StructuralResult,
    budget_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist the first canonical structural PASS for a governed objective.

    Unlike :func:`reconcile_structural_pass`, this helper consumes the
    structural result that the daemon has *just* computed.  It therefore does
    not re-run the provider and cannot accidentally turn the governance
    boundary into a second structural experiment.  The helper is deliberately
    performance blind and creates only the reconciliation/report projection
    required for a later human predictive-authorization decision.
    """

    if not SAFE_ID.fullmatch(objective_id) or not SAFE_ID.fullmatch(candidate.candidate_id):
        raise ValueError("objective_id and candidate_id must be canonical safe identifiers")
    if result.status != "PASS":
        raise RuntimeError(f"governed structural boundary requires PASS, got {result.status}")

    root_path = Path(root).resolve()
    structural = _structural_payload(result)
    details = dict(structural.get("details") or {})
    v1 = dict(details.get("v1_result") or {})
    integrity = dict(details.get("lower_bound_integrity") or {})
    if v1.get("outcome_blind") is not True or v1.get("performance_data_loaded") is not False:
        raise RuntimeError("structural result crossed the outcome-blind boundary")
    if integrity.get("status") != "PASS" or integrity.get("failure_codes"):
        raise RuntimeError("lower-bound integrity is not established")
    lower_bound = int(v1.get("lower_bound_count", 0) or 0)
    upper_bound = int(v1.get("upper_bound_count", 0) or 0)
    minimum_required = int(v1.get("minimum_required_count", 0) or 0)
    if lower_bound < minimum_required:
        raise RuntimeError("structural PASS lower bound is below the frozen minimum")

    trial_events = _trial_events(root_path, objective_id, candidate.candidate_id)
    if trial_events:
        raise RuntimeError("candidate already has TrialLedger events; first-pass governance publication is forbidden")

    budget = dict(budget_snapshot)
    budget_ref = str(budget.get("registry_path") or "")
    budget_path = (root_path / budget_ref).resolve() if budget_ref else None
    budget_hash_before = None
    if budget_path is not None:
        if not budget_path.is_relative_to(root_path) or not budget_path.is_file():
            raise RuntimeError("canonical search budget path is unavailable for governed structural boundary")
        budget_hash_before = _sha256(budget_path)

    canonical_values = {
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
        "minimum_required": minimum_required,
        "lower_bound_integrity": integrity.get("status"),
        "classification": "PASS",
    }
    reconciliation_id = stable_hash({
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "mode": "DAEMON_FIRST_GOVERNED_STRUCTURAL_PASS",
        "objective_id": objective_id,
        "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate.candidate_hash,
        "structural_result_hash": stable_hash(structural),
    })
    runtime_dir = root_path / "reports" / "research_daemon" / objective_id
    report_path = runtime_dir / CANONICAL_RECONCILIATION_FILENAME
    report_ref = _relative_ref(root_path, report_path)

    if report_path.is_file():
        existing = json.loads(report_path.read_text(encoding="utf-8"))
        if (
            str(existing.get("reconciliation_id")) != reconciliation_id
            or str(existing.get("candidate_id")) != candidate.candidate_id
            or str(existing.get("candidate_hash")) != candidate.candidate_hash
            or str(existing.get("status")) != "PASS"
        ):
            raise RuntimeError("canonical structural governance report identity conflict")
        governance = _write_structural_governance(
            root_path,
            objective_id=objective_id,
            candidate=candidate,
            report_ref=report_ref,
            reconciliation_id=reconciliation_id,
            structural={**canonical_values, "status": "PASS"},
        )
        return {
            "status": "PASS",
            "reconciliation_id": reconciliation_id,
            "report_ref": report_ref,
            "history_ref": existing.get("history_ref"),
            "governance_ref": _relative_ref(
                root_path,
                root_path / "reports" / "research_orchestrator_v2" / objective_id / STRUCTURAL_GOVERNANCE_FILENAME,
            ),
            "governance": governance,
            "canonical_values": canonical_values,
            "idempotent": True,
        }

    graph_summary = _update_artifact_graph(
        root_path,
        objective_id=objective_id,
        candidate=candidate,
        reconciliation_id=reconciliation_id,
        structural={"status": "PASS", **canonical_values},
    )
    governance = _write_structural_governance(
        root_path,
        objective_id=objective_id,
        candidate=candidate,
        report_ref=report_ref,
        reconciliation_id=reconciliation_id,
        structural={**canonical_values, "status": "PASS"},
    )
    governance_path = root_path / "reports" / "research_orchestrator_v2" / objective_id / STRUCTURAL_GOVERNANCE_FILENAME
    governance_ref = _relative_ref(root_path, governance_path)
    history_path = runtime_dir / RECONCILIATION_HISTORY_FILENAME
    history_ref = _relative_ref(root_path, history_path)

    report = {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "reconciliation_id": reconciliation_id,
        "status": "PASS",
        "objective_id": objective_id,
        "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate.candidate_hash,
        "previous_structural_status": None,
        "previous_reconciliation_id": None,
        "reconciliation_source": "DAEMON_CURRENT_CANONICAL_PROVIDER_RESULT",
        "fresh_provider_recheck": structural,
        "repaired_structural_result": structural,
        **canonical_values,
        "lineage": {
            "lineage_status": "FIRST_CANONICAL_GOVERNED_STRUCTURAL_PASS",
            "previous_reconciliation": None,
            "evidence_chain": [],
            "historical_reconciliation_generations": [],
            "current_generation": {
                "generation": "CANONICAL_STRUCTURAL_PASS",
                "report_ref": report_ref,
                "reconciliation_id": reconciliation_id,
                "status": "PASS",
                **canonical_values,
                "governance_ref": governance_ref,
            },
            "artifact_graph": graph_summary,
        },
        "governance_ref": governance_ref,
        "safety_evidence": {
            "budget": budget,
            "budget_file_sha256_before": budget_hash_before,
            "trial_ledger_event_count": 0,
            "predictive_executor_invoked": False,
            "performance_data_loaded": False,
            "new_predictive_trials": 0,
            "new_performance_access": 0,
            "final_test_access": {"analytical": 0, "decision": 0, "physical": 0},
            "prospective": "DISABLED",
            "real_order": "DISABLED",
        },
        "next_boundary": "PREDICTIVE_VALIDATION_REQUIRES_SEPARATE_AUTHORIZATION",
        "created_at": now_timestamp(),
    }
    DaemonCheckpointStoreV1._atomic_write(report_path, report)
    history = {
        "schema_version": "structural-preflight-reconciliation-history-v1",
        "objective_id": objective_id,
        "latest_reconciliation_id": reconciliation_id,
        "generations": [{
            "generation": "CANONICAL_STRUCTURAL_PASS",
            "report_ref": report_ref,
            "sha256": _sha256(report_path),
            "reconciliation_id": reconciliation_id,
            "status": "PASS",
            **canonical_values,
        }],
        "history_preserved": True,
        "updated_at": now_timestamp(),
    }
    DaemonCheckpointStoreV1._atomic_write(history_path, history)

    if budget_path is not None and _sha256(budget_path) != budget_hash_before:
        raise RuntimeError("budget changed while publishing governed structural boundary")
    if _trial_events(root_path, objective_id, candidate.candidate_id):
        raise RuntimeError("TrialLedger changed while publishing governed structural boundary")

    return {
        "status": "PASS",
        "reconciliation_id": reconciliation_id,
        "report_ref": report_ref,
        "history_ref": history_ref,
        "governance_ref": governance_ref,
        "governance": governance,
        "canonical_values": canonical_values,
        "idempotent": False,
    }


def _load_json(root: Path, relative_ref: str) -> dict[str, Any]:
    path = root / relative_ref
    if not path.is_file():
        raise RuntimeError(f"audited structural artifact is missing: {relative_ref}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"audited structural artifact is not an object: {relative_ref}")
    return payload


def _validated_audited_closure_result(root: Path, *, objective_id: str, candidate: CandidateWork) -> StructuralResult | None:
    """Promote an already audited closure only after validating its contracts.

    The current Provider can legitimately remain on the old interval-bound
    result while the completed closure artifacts contain the repaired,
    outcome-blind evidence.  This adapter keeps that observation visible to
    the caller and refuses promotion unless the closure artifacts agree on
    identity, policy, boundary and safety.
    """

    final_ref = "reports/FINAL_STATUS_CLOSE_STRUCTURAL_PREFLIGHT_UNKNOWN_BOUND_V1.json"
    sample_ref = "reports/STRUCTURAL_SAMPLE_BOUND_RECALCULATION_V1.json"
    breakdown_ref = "reports/STRUCTURAL_UPPER_ONLY_39_BREAKDOWN_V1.json"
    tail_ref = "reports/STRUCTURAL_TAIL_WINDOW_AUDIT_V1.json"
    unknown_ref = "reports/STRUCTURAL_UNKNOWN_RESOLUTION_V1.json"
    final = _load_json(root, final_ref)
    sample = _load_json(root, sample_ref)
    breakdown = _load_json(root, breakdown_ref)
    tail = _load_json(root, tail_ref)
    unknown = _load_json(root, unknown_ref)
    refs = [final_ref, sample_ref, breakdown_ref, tail_ref, unknown_ref]

    identities = [
        (final.get("objective_id"), final.get("candidate", {}).get("candidate_id"), final.get("candidate", {}).get("candidate_hash")),
        (sample.get("objective_id"), sample.get("candidate_id"), sample.get("candidate_hash")),
        (breakdown.get("objective_id"), breakdown.get("candidate_id"), breakdown.get("candidate_hash")),
        (tail.get("objective_id"), tail.get("candidate_id"), tail.get("candidate_hash")),
        (unknown.get("objective_id"), unknown.get("candidate_id"), unknown.get("candidate_hash")),
    ]
    if any(str(item[0]) != objective_id for item in identities):
        raise RuntimeError("audited structural closure objective does not match canonical objective")
    if any(str(item[1]) != candidate.candidate_id or str(item[2]) != candidate.candidate_hash for item in identities):
        raise RuntimeError("audited structural closure candidate identity mismatch")
    if any(str(item[0]) != str(final.get("objective_id")) for item in identities):
        raise RuntimeError("audited structural closure objective identity mismatch")

    conclusion = dict(final.get("business_conclusion") or {})
    after = dict(sample.get("after_legal_tail_exclusion") or {})
    policy = dict(breakdown.get("policy") or {})
    policy_application = dict(final.get("policy_application") or {})
    if {
        "audited_lower_bound": conclusion.get("audited_lower_bound"),
        "audited_upper_bound": conclusion.get("audited_upper_bound"),
        "minimum_required": conclusion.get("minimum_required"),
        "lower_bound_integrity": conclusion.get("lower_bound_integrity"),
        "audited_structural_status": conclusion.get("audited_structural_status"),
    } != {
        "audited_lower_bound": after.get("lower_bound"),
        "audited_upper_bound": after.get("upper_bound"),
        "minimum_required": after.get("minimum_required"),
        "lower_bound_integrity": after.get("lower_bound_integrity"),
        "audited_structural_status": after.get("status"),
    }:
        raise RuntimeError("audited structural closure count fields disagree")
    if not (
        conclusion.get("audited_lower_bound") is not None
        and int(conclusion["audited_lower_bound"]) >= int(conclusion["minimum_required"])
        and conclusion.get("audited_structural_status") == "PASS"
        and conclusion.get("lower_bound_integrity") == "PASS"
        and policy.get("minimum_required") == conclusion.get("minimum_required")
        and policy.get("policy_id") == "VALIDATION_DECISION_POLICY_V2"
        and policy_application.get("policy_id") == "VALIDATION_DECISION_POLICY_V2"
        and policy_application.get("policy_hash") == policy.get("policy_hash")
    ):
        raise RuntimeError("audited structural closure policy gate is not proven")

    steps = sample.get("recalculation_steps") if isinstance(sample.get("recalculation_steps"), list) else []
    replay = next((item for item in steps if isinstance(item, dict) and item.get("step") == 1), {})
    corrected = next((item.get("corrected_structural_path") for item in steps if isinstance(item, dict) and item.get("step") == 2), {})
    factor_reconciliation = dict(unknown.get("factor_reconciliation") or {})
    replay_counts = [
        replay.get("materialized_observations"),
        corrected.get("raw") if isinstance(corrected, dict) else None,
        next(
            (
                item.get("raw_factor_keys")
                for item in steps
                if isinstance(item, dict) and item.get("step") == 2
            ),
            None,
        ),
        factor_reconciliation.get("raw_event_stock_day_rows"),
    ]
    if not isinstance(corrected, dict) or not (
        replay.get("provider") == "RealSampleFeasibilityProviderV1"
        and replay.get("streaming") is True
        and replay.get("partition_session_count") == 80
        and all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in replay_counts)
        and len(set(replay_counts)) == 1
        and isinstance(corrected.get("lower_bound"), int)
        and isinstance(corrected.get("upper_bound"), int)
    ):
        raise RuntimeError("audited structural closure provider replay contract is incomplete")

    category_summary = breakdown.get("category_summary") if isinstance(breakdown.get("category_summary"), list) else []
    category_counts: dict[str, int] = {}
    for item in category_summary:
        if not isinstance(item, dict) or not item.get("classification"):
            raise RuntimeError("audited structural closure category evidence is incomplete")
        classification = str(item["classification"])
        count = item.get("count")
        if classification in category_counts or not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise RuntimeError("audited structural closure category evidence is invalid")
        category_counts[classification] = count
    tail_summary = dict(tail.get("tail_summary") or {})
    tail_entries = tail.get("late_event_days") if isinstance(tail.get("late_event_days"), list) else []
    tail_excluded = breakdown.get("tail_excluded_from_39") if isinstance(breakdown.get("tail_excluded_from_39"), dict) else {}
    tail_event_date = tail_excluded.get("event_date")
    tail_entry = next((item for item in tail_entries if isinstance(item, dict) and item.get("event_date") == tail_event_date), {})
    tail_result = dict(final.get("tail_result") or {})
    tail_removed = tail_summary.get("upper_bound_count_removed")
    corrected_lower = corrected.get("lower_bound")
    corrected_upper = corrected.get("upper_bound")
    after_lower = after.get("lower_bound")
    after_upper = after.get("upper_bound")
    if not (
        category_counts
        and set(category_counts).issubset({"A", "D_FOR_CURRENT_WITNESS_ONLY"})
        and category_counts.get("A", 0) >= 0
        and category_counts.get("D_FOR_CURRENT_WITNESS_ONLY", 0) >= 0
        and isinstance(tail_event_date, int)
        and tail_entry.get("classification") == "B"
        and isinstance(tail_removed, int)
        and not isinstance(tail_removed, bool)
        and tail_removed > 0
        and corrected_lower == after_lower
        and isinstance(corrected_upper, int)
        and isinstance(after_upper, int)
        and corrected_upper == after_upper + tail_removed
        and tail_excluded.get("final_test_accessed") is False
        and tail_summary.get("final_test_access_would_be_required") is True
        and tail_entry.get("final_test_access_performed") is False
        and tail_result.get("final_test_access") == 0
        and tail_result.get("removed_from_legal_upper") == tail_removed
    ):
        raise RuntimeError("audited structural closure A/B/C/D or tail evidence is incomplete")

    remaining = dict(final.get("remaining_unknowns") or {})
    remaining_unknown_counts = [
        remaining.get("factor_source_unavailable_observations"),
        factor_reconciliation.get("remaining_factor_source_unknown_rows"),
        factor_reconciliation.get("source_unavailable_factor_keys"),
    ]
    if not (
        all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in remaining_unknown_counts)
        and len(set(remaining_unknown_counts)) == 1
        and remaining.get("binding_to_final_decision") == 0
    ):
        raise RuntimeError("audited non-binding factor-source-unavailable count is not proven")

    safety_records = [
        dict(final.get("freeze_and_safety") or {}),
        dict(sample.get("safety") or {}),
        dict(breakdown.get("safety") or {}),
        dict(tail.get("safety") or {}),
        dict(unknown.get("safety") or {}),
    ]
    if any(record.get("outcome_blind") is not True or record.get("performance_data_loaded") is not False or record.get("NEW_PREDICTIVE_TRIALS", record.get("NEW_TRIALS", 0)) != 0 or record.get("NEW_PERFORMANCE_ACCESS", 0) != 0 for record in safety_records):
        raise RuntimeError("audited structural closure crossed the outcome-blind boundary")
    v1 = {
        "schema_version": "candidate-sample-feasibility-result-v1",
        "preflight_version": "CandidateSampleFeasibilityPreflightV1",
        "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate.candidate_hash,
        "raw_event_or_signal_count": corrected.get("raw"),
        "data_complete_count": corrected.get("data"),
        "pit_eligible_count": corrected.get("pit"),
        "execution_eligible_count": corrected.get("execution"),
        "qualified_signal_count": corrected.get("qualified"),
        "selected_opportunity_count": corrected.get("selected"),
        "portfolio_feasible_opportunity_count": after.get("lower_bound"),
        "lower_bound_count": after.get("lower_bound"),
        "upper_bound_count": after.get("upper_bound"),
        "minimum_required_count": after.get("minimum_required"),
        "status": after.get("status"),
        "reason_codes": [],
        "policy_id": policy.get("policy_id"),
        "policy_version": policy.get("policy_version"),
        "policy_hash": policy.get("policy_hash"),
        "outcome_blind": True,
        "performance_data_loaded": False,
        "performance_files_read": [],
        "data_pit_provenance": {"pit": True, "source": "audited_structural_closure_artifacts"},
    }
    integrity = {
        "status": after.get("lower_bound_integrity"),
        "failure_codes": [],
        "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate.candidate_hash,
        "proof_scope": "structural fields, calendar, PIT, tradability, execution and capacity only; no outcome fields",
        "audit_ref": sample_ref,
    }
    v2 = {
        **v1,
        "schema_version": "candidate-sample-feasibility-result-v2",
        "status": "PASS",
        "decision_rule": policy_application.get("rule_applied") or "lower >= minimum_required and lower_bound_integrity == PASS -> PASS",
        "lower_bound_integrity_status": "PASS",
        "lower_bound_integrity": integrity,
        "exact_count_unknown": False,
    }
    return StructuralResult(
        "PASS",
        "PASS",
        tuple(refs),
        replay.get("observation_hash"),
        None,
        details={
            "v1_result": v1,
            "v2_result": v2,
            "lower_bound_integrity": integrity,
            "audited_closure": {
                "source": final_ref,
                "sample_recalculation_ref": sample_ref,
                "upper_only_breakdown_ref": breakdown_ref,
                "tail_audit_ref": tail_ref,
                "unknown_resolution_ref": unknown_ref,
                "a_b_c_d": {
                    "A": category_counts.get("A", 0),
                    "B": tail_removed,
                    "C": category_counts.get("C", 0),
                    "D": category_counts.get("D_FOR_CURRENT_WITNESS_ONLY", 0),
                },
                "non_binding_factor_source_unavailable": remaining_unknown_counts[0],
            },
        },
    )


def reconcile_structural_pass(
    root: str | Path,
    *,
    objective_id: str,
    candidate_id: str,
    runtime: Any | None = None,
) -> dict[str, Any]:
    """Re-run and persist a structural PASS while preserving predictive boundaries."""

    if not SAFE_ID.fullmatch(objective_id) or not SAFE_ID.fullmatch(candidate_id):
        raise ValueError("objective_id and candidate_id must be canonical safe identifiers")
    root_path = Path(root).resolve()
    runtime = runtime or CanonicalResearchRuntime(root_path, objective_id=objective_id)
    candidate = _candidate(runtime, candidate_id)
    store = DaemonCheckpointStoreV1(root_path, objective_id)
    checkpoint = store.load()
    if checkpoint is None:
        raise RuntimeError("daemon checkpoint is required for structural reconciliation")
    if checkpoint.current_state != ResearchDaemonState.READY.value:
        raise RuntimeError(f"structural reconciliation requires READY checkpoint, got {checkpoint.current_state}")
    if checkpoint.current_trial is not None:
        raise RuntimeError("structural reconciliation is forbidden while a trial is active")
    previous = dict(checkpoint.canonical_refs.get("last_structural_result") or {})
    previous_details = dict(previous.get("details") or {})
    previous_identity = previous_details
    for nested_key in ("v2_result", "v1_result"):
        nested = previous_details.get(nested_key) if isinstance(previous_details.get(nested_key), Mapping) else {}
        if nested.get("candidate_id"):
            previous_identity = nested
            break
    if str(previous_identity.get("candidate_id")) != candidate.candidate_id:
        raise RuntimeError("previous structural candidate identity does not match")
    if str(previous_identity.get("candidate_hash")) != candidate.candidate_hash:
        raise RuntimeError("previous structural candidate hash does not match")
    marker = dict(checkpoint.canonical_refs.get("structural_reconciliation") or {})
    report_path = store.runtime_dir / CANONICAL_RECONCILIATION_FILENAME
    if marker.get("status") in {"PASS", "BLOCKED"} and previous.get("status") == marker.get("status") and report_path.exists():
        existing_report = _sanitize_existing_reconciliation_lineage(
            root_path,
            report_path,
            objective_id=objective_id,
            candidate=candidate,
        )
        if existing_report.get("status") == "PASS" and marker.get("status") == "PASS":
            governance_path = root_path / str(marker.get("governance_ref") or "")
            governance_payload: Mapping[str, Any] = {}
            if governance_path.is_file():
                try:
                    loaded_governance = json.loads(governance_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    loaded_governance = {}
                if isinstance(loaded_governance, Mapping):
                    governance_payload = loaded_governance
            governance_structural = governance_payload.get("structural")
            governance_structural_status = governance_structural.get("status") if isinstance(governance_structural, Mapping) else None
            if governance_structural_status != "PASS":
                canonical_values = {
                    "status": "PASS",
                    "lower_bound": existing_report.get("lower_bound"),
                    "upper_bound": existing_report.get("upper_bound"),
                    "minimum_required": existing_report.get("minimum_required"),
                    "lower_bound_integrity": existing_report.get("lower_bound_integrity"),
                }
                _write_structural_governance(
                    root_path,
                    objective_id=objective_id,
                    candidate=candidate,
                    report_ref=str(marker.get("report_ref") or _relative_ref(root_path, report_path)),
                    reconciliation_id=str(marker.get("reconciliation_id") or existing_report.get("reconciliation_id")),
                    structural=canonical_values,
                )
            return existing_report
        return existing_report

    trial_events_before = _trial_events(root_path, objective_id, candidate_id)
    if trial_events_before:
        raise RuntimeError("candidate already has TrialLedger events; structural-only reconciliation is forbidden")
    context_before = runtime.load_context()
    budget_path = (root_path / str(context_before.budget.get("registry_path") or "")).resolve()
    if budget_path != root_path and root_path not in budget_path.parents:
        raise RuntimeError("canonical search budget path escapes the repository root")
    if not budget_path.is_file():
        raise RuntimeError("canonical search budget is required for structural reconciliation")
    budget_hash_before = _sha256(budget_path)
    budget_before = dict(context_before.budget)
    if int(budget_before.get("used", 0)) != 0 or int(budget_before.get("reserved", 0)) != 0:
        raise RuntimeError("structural-only reconciliation requires zero used and reserved candidate budget")

    lock = DaemonInstanceLockV1(store.lock_path, objective_id)
    lock.acquire(run_id=f"STRUCTURAL_RECONCILIATION_{candidate_id}")
    try:
        fresh_result = runtime.structural_preflight(candidate)
        fresh_provider_recheck = _structural_payload(fresh_result)
        result = fresh_result
        reconciliation_source = "CURRENT_CANONICAL_PROVIDER"
        if fresh_result.status == "UNKNOWN" and fresh_result.reason_code == "C_LOWER_UPPER_INTERVAL_CROSSES_MINIMUM":
            result = _validated_audited_closure_result(root_path, objective_id=objective_id, candidate=candidate)
            reconciliation_source = "AUDITED_CLOSURE_ARTIFACTS_AFTER_CURRENT_PROVIDER_RECHECK"
        if result.status not in {"PASS", "BLOCKED"}:
            raise RuntimeError(f"repaired structural preflight did not pass: {result.status}:{result.reason_code}")
        details = dict(result.details)
        v1 = dict(details.get("v1_result") or {})
        integrity = dict(details.get("lower_bound_integrity") or {})
        if v1.get("outcome_blind") is not True or v1.get("performance_data_loaded") is not False:
            raise RuntimeError("structural result crossed the outcome-blind boundary")
        if integrity.get("status") != "PASS" or integrity.get("failure_codes"):
            raise RuntimeError("lower-bound integrity is not established")
        if result.status == "PASS" and int(v1.get("lower_bound_count", 0)) < int(v1.get("minimum_required_count", 0)):
            raise RuntimeError("structural lower bound remains below the frozen minimum")
        if result.status == "BLOCKED" and (
            result.reason_code != "A_UPPER_BOUND_BELOW_MINIMUM"
            or int(v1.get("upper_bound_count", 0)) >= int(v1.get("minimum_required_count", 0))
        ):
            raise RuntimeError("blocked structural result is not the frozen sample gate")

        context_after = runtime.load_context()
        budget_after = dict(context_after.budget)
        budget_hash_after = _sha256(budget_path)
        trial_events_after = _trial_events(root_path, objective_id, candidate_id)
        if budget_hash_after != budget_hash_before or budget_after != budget_before:
            raise RuntimeError("budget changed during structural-only reconciliation")
        if trial_events_after != trial_events_before:
            raise RuntimeError("TrialLedger changed during structural-only reconciliation")

        structural = _structural_payload(result)
        structural_v1 = dict(structural.get("details", {}).get("v1_result") or {})
        structural_integrity = dict(structural.get("details", {}).get("lower_bound_integrity") or {})
        canonical_values = {
            "lower_bound": structural_v1.get("lower_bound_count"),
            "upper_bound": structural_v1.get("upper_bound_count"),
            "minimum_required": structural_v1.get("minimum_required_count"),
            "lower_bound_integrity": structural_integrity.get("status"),
            "classification": "PASS" if result.status == "PASS" else "BLOCKED",
        }
        reconciliation_id = stable_hash({
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "objective_id": objective_id,
            "candidate_id": candidate.candidate_id,
            "candidate_hash": candidate.candidate_hash,
            "previous_result_hash": stable_hash(previous),
            "repaired_result_hash": stable_hash(structural),
        })
        previous_report_ref = str(marker.get("report_ref") or "")
        previous_report_path = root_path / previous_report_ref if previous_report_ref and not Path(previous_report_ref).is_absolute() else None
        preserved_generations = [
            item
            for item in _preserved_generation_summaries(root_path, store.runtime_dir)
            if _summary_matches_candidate_scope(item, objective_id=objective_id, candidate=candidate)
        ]
        if previous_report_path and previous_report_path.is_file() and all(item.get("report_ref") != previous_report_ref for item in preserved_generations):
            previous_summary = _report_summary(root_path, previous_report_path)
            if _summary_matches_candidate_scope(previous_summary, objective_id=objective_id, candidate=candidate):
                previous_summary["generation"] = "PREVIOUS_CANONICAL"
                preserved_generations.append(previous_summary)
        evidence_chain_names = (
            "reports/INITIAL_STRUCTURAL_PREFLIGHT_V1.json",
            "reports/research_daemon/{objective_id}/structural_preflight_reconciliation.json",
            "reports/FOLLOWUP_STRUCTURAL_PREFLIGHT_REPAIRED_V1.json",
            "reports/STRUCTURAL_UPPER_ONLY_39_BREAKDOWN_V1.json",
            "reports/STRUCTURAL_TAIL_WINDOW_AUDIT_V1.json",
            "reports/STRUCTURAL_UNKNOWN_RESOLUTION_V1.json",
            "reports/FINAL_STATUS_CLOSE_STRUCTURAL_PREFLIGHT_UNKNOWN_BOUND_V1.json",
        )
        evidence_chain: list[dict[str, Any]] = []
        for template in evidence_chain_names:
            ref = template.format(objective_id=objective_id)
            path = root_path / ref
            if path.is_file():
                summary = _report_summary(root_path, path)
                if _summary_matches_candidate_scope(summary, objective_id=objective_id, candidate=candidate):
                    evidence_chain.append(summary | {"lineage_role": "STRUCTURAL_EVIDENCE"})
        graph_summary = _update_artifact_graph(
            root_path,
            objective_id=objective_id,
            candidate=candidate,
            reconciliation_id=reconciliation_id,
            structural=canonical_values,
        )
        report = {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "reconciliation_id": reconciliation_id,
            "status": result.status,
            "objective_id": objective_id,
            "candidate_id": candidate.candidate_id,
            "candidate_hash": candidate.candidate_hash,
            "previous_structural_status": previous.get("status"),
            "previous_reconciliation_id": marker.get("reconciliation_id"),
            "reconciliation_source": reconciliation_source,
            "fresh_provider_recheck": fresh_provider_recheck,
            "repaired_structural_result": structural,
            **canonical_values,
            "lineage": {
                "lineage_status": "APPENDED_PRESERVING_HISTORY",
                "previous_reconciliation": {
                    "reconciliation_id": marker.get("reconciliation_id"),
                    "report_ref": previous_report_ref or None,
                    "report_sha256": _sha256(previous_report_path) if previous_report_path and previous_report_path.is_file() else None,
                    "status": previous.get("status"),
                },
                "evidence_chain": evidence_chain,
                "historical_reconciliation_generations": preserved_generations,
                "current_generation": {
                    "generation": "CANONICAL_STRUCTURAL_PASS",
                    "report_ref": _relative_ref(root_path, report_path),
                    "reconciliation_id": reconciliation_id,
                    "status": result.status,
                    **canonical_values,
                },
                "artifact_graph": graph_summary,
            },
            "safety_evidence": {
                "budget_before": budget_before,
                "budget_after": budget_after,
                "budget_file_sha256_before": budget_hash_before,
                "budget_file_sha256_after": budget_hash_after,
                "trial_ledger_event_count_before": len(trial_events_before),
                "trial_ledger_event_count_after": len(trial_events_after),
                "predictive_executor_invoked": False,
                "performance_data_loaded": False,
                "new_predictive_trials": 0,
                "new_performance_access": 0,
                "final_test_access": {"analytical": 0, "decision": 0, "physical": 0},
                "prospective": "DISABLED",
                "real_order": "DISABLED",
            },
            "next_boundary": "PREDICTIVE_VALIDATION_REQUIRES_SEPARATE_AUTHORIZATION" if result.status == "PASS" else "PREDICTIVE_VALIDATION_BLOCKED_BY_SAMPLE_GATE",
            "created_at": now_timestamp(),
        }
        DaemonCheckpointStoreV1._atomic_write(report_path, report)
        report_ref = _relative_ref(root_path, report_path)
        if result.status == "PASS":
            governance = _write_structural_governance(
                root_path,
                objective_id=objective_id,
                candidate=candidate,
                report_ref=report_ref,
                reconciliation_id=reconciliation_id,
                structural={**canonical_values, "status": result.status},
            )
        else:
            governance = None
        report["governance_ref"] = _relative_ref(root_path, store.runtime_dir.parent.parent / "research_orchestrator_v2" / objective_id / STRUCTURAL_GOVERNANCE_FILENAME) if governance else None
        report["lineage"]["current_generation"]["governance_ref"] = report["governance_ref"]
        DaemonCheckpointStoreV1._atomic_write(report_path, report)
        history_path = store.runtime_dir / RECONCILIATION_HISTORY_FILENAME
        history_generations = [*preserved_generations, {
            "generation": "CANONICAL_STRUCTURAL_PASS" if result.status == "PASS" else "CANONICAL_RECONCILIATION",
            "report_ref": report_ref,
            "sha256": _sha256(report_path),
            "reconciliation_id": reconciliation_id,
            "status": result.status,
            **canonical_values,
        }]
        history = {
            "schema_version": "structural-preflight-reconciliation-history-v1",
            "objective_id": objective_id,
            "latest_reconciliation_id": reconciliation_id,
            "generations": history_generations,
            "history_preserved": True,
            "updated_at": now_timestamp(),
        }
        DaemonCheckpointStoreV1._atomic_write(history_path, history)
        refs = {
            **dict(checkpoint.canonical_refs),
            "last_structural_result": structural,
            "structural_reconciliation": {
                "status": result.status,
                "reconciliation_id": reconciliation_id,
                "report_ref": report_ref,
                "history_ref": _relative_ref(root_path, history_path),
                "previous_reconciliation_id": marker.get("reconciliation_id"),
                "lineage_status": "APPENDED_PRESERVING_HISTORY",
                "governance_ref": report["governance_ref"],
                "predictive_run_started": False,
            },
        }
        updated = checkpoint.update(
            canonical_refs=refs,
            budget_view=budget_after,
            last_completed_candidate=candidate.to_dict(),
            current_candidate=None,
            current_trial=None,
            required_action="PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED" if result.status == "PASS" else None,
            last_error=None,
            error_reason_code=None,
            retry_safe=True,
        ).transition(
            ResearchDaemonState.READY,
            RECONCILIATION_REASON if result.status == "PASS" else "STRUCTURAL_RESULT_RECONCILED_WITHOUT_PREDICTIVE_RUN",
            details={
                "candidate_id": candidate.candidate_id,
                "candidate_hash": candidate.candidate_hash,
                "reconciliation_id": reconciliation_id,
                "previous_reconciliation_id": marker.get("reconciliation_id"),
                "predictive_run_started": False,
            },
        )
        store.save(updated)
        store.append_event(
            "STRUCTURAL_RESULT_RECONCILED",
            {
                **dict(updated.last_transition or {}),
                "objective_id": objective_id,
                "reconciliation_id": reconciliation_id,
            },
            event_id=stable_hash({"event_type": "STRUCTURAL_RESULT_RECONCILED", "reconciliation_id": reconciliation_id}),
        )
        daemon = ResearchDaemon(root_path, objective_id=objective_id, runtime=runtime)
        daemon.checkpoint = updated
        status = daemon.status_payload()
        status["process_pid"] = None
        store.save_status(status)
        return report
    finally:
        lock.release()


__all__ = ["reconcile_structural_pass"]
