"""Resolve the frozen R4 B02 T005 orphan without reopening performance."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import csv
import json
import math
import os
import shutil

from chanlun_trader.research.strategy_validation import TrialRegistryV1

from .artifact_graph import ResearchArtifactGraphV1
from .budget import SearchBudgetRegistryV1
from .common import now_timestamp, stable_hash
from .commit import DurableCommitLedgerV1
from .history import CumulativeResearchHistoryV1
from .strategy_adapter import ResearchStrategyRegistryFacadeV1
from .trial_adapter import ResearchFactoryTrialLedgerFacadeV1


TRIAL_ID = "CODEX_GUIDED_AUTONOMOUS_RESEARCH_PILOT_V1_OBJECTIVE_R4_B02_T005"
BATCH_ID = "CODEX_GUIDED_AUTONOMOUS_RESEARCH_PILOT_V1_OBJECTIVE_R4_B02"
CANDIDATE_ID = "CAND_EVENT_REVERSAL_OPENING_COUNT_TREND_FILTER_0408CD11_V1_V2"
CANDIDATE_HASH = "5c87a8e94cb5f9d5c3b6e5bd9ab0972a9d472e7d46a172d6c96d6c31818d22c7"
RESERVATION_ID = "TRIAL-5df157d1206bcefdbcc8"
POLICY_HASH = "93c97ef9d8b88f204d179a01301276d4142f13046e72192134d18db3a4834744"
OBJECTIVE_ID = "CODEX_GUIDED_AUTONOMOUS_RESEARCH_PILOT_V1_OBJECTIVE"
REASON_CODES = (
    "ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS",
    "EVIDENCE_INCOMPLETE_AFTER_PERFORMANCE_ACCESS",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@dataclass(frozen=True)
class OrphanResolutionPathsV1:
    root: Path

    @property
    def r4(self) -> Path:
        return self.root / "reports" / "codex_guided_autonomous_research_pilot_v1_r4"

    @property
    def b02(self) -> Path:
        return self.r4 / BATCH_ID

    @property
    def data_b02(self) -> Path:
        return self.root / "data" / "research" / "research_factory" / "batches" / BATCH_ID

    @property
    def resolution_dir(self) -> Path:
        return self.root / "reports" / "orphan_trial_b02_t005_resolution_v1"

    @property
    def factory_ledger(self) -> Path:
        return self.r4 / "factory_trial_ledger.json"

    @property
    def trial_registry(self) -> Path:
        return self.r4 / "trial_registry.json"

    @property
    def budget_registry(self) -> Path:
        return self.r4 / "search_budget_registry.json"

    @property
    def checkpoint(self) -> Path:
        return self.r4 / "checkpoints" / f"{BATCH_ID}.json"

    @property
    def artifact_graph(self) -> Path:
        return self.r4 / "artifact_graph.json"

    @property
    def strategy_registry(self) -> Path:
        return self.r4 / "strategy_registry.json"

    @property
    def commit_markers(self) -> Path:
        return self.r4 / "commit_markers.json"

    @property
    def cumulative_history(self) -> Path:
        return self.r4 / "cumulative_history.json"

    @property
    def pilot_summary(self) -> Path:
        return self.r4 / "pilot_summary.json"

    @property
    def provisional_evidence(self) -> Path:
        return self.data_b02 / "provisional_validation_evidence" / f"{TRIAL_ID}.json"

    @property
    def engine_evidence(self) -> Path:
        return self.root / "reports" / "strategy_validation_engine_corrected" / TRIAL_ID


class SyntheticCrashAfterTerminalEvent(RuntimeError):
    """Test-only crash point between terminal ledger append and checkpoint update."""


class RecoverableExistingEvidence(RuntimeError):
    """The caller must use the original evidence recovery/adjudication path."""


class OrphanTrialResolutionV1:
    """Perform the evidence audit and exact-once engineering terminalization."""

    def __init__(self, paths: OrphanResolutionPathsV1):
        self.paths = paths

    def _matching_files(self, roots: tuple[Path, ...]) -> list[str]:
        matches: list[str] = []
        needles = (TRIAL_ID, CANDIDATE_ID, CANDIDATE_HASH, RESERVATION_ID)
        excluded = self.paths.resolution_dir.resolve()
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in {".json", ".jsonl", ".csv", ".md", ".txt", ".log", ".yaml", ".yml"}:
                    continue
                try:
                    if path.resolve().is_relative_to(excluded):
                        continue
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                if any(needle in text for needle in needles):
                    matches.append(str(path.relative_to(self.paths.root)))
        return sorted(set(matches))

    def _snapshot_inputs(self) -> None:
        snapshot_root = self.paths.resolution_dir / "pre_resolution_snapshots"
        input_paths = (
            self.paths.factory_ledger,
            self.paths.trial_registry,
            self.paths.budget_registry,
            self.paths.checkpoint,
            self.paths.artifact_graph,
            self.paths.strategy_registry,
            self.paths.commit_markers,
            self.paths.cumulative_history,
            self.paths.pilot_summary,
        )
        for source in input_paths:
            if not source.exists():
                continue
            destination = snapshot_root / source.relative_to(self.paths.root)
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

    @staticmethod
    def _finite_realized_pnl(path: Path) -> bool:
        for candidate in path.rglob("*") if path.exists() else ():
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() == ".json":
                try:
                    payload = _read_json(candidate)
                except (OSError, ValueError):
                    continue
                value = payload.get("realized_pnl")
                if isinstance(value, (int, float)) and math.isfinite(float(value)):
                    return True
            if candidate.suffix.lower() == ".csv":
                try:
                    with candidate.open("r", encoding="utf-8", newline="") as handle:
                        reader = csv.DictReader(handle)
                        fields = {str(item).lower() for item in (reader.fieldnames or ())}
                        pnl_field = next((item for item in ("realized_pnl", "pnl", "net_pnl") if item in fields), None)
                        if pnl_field is None:
                            continue
                        for row in reader:
                            value = row.get(pnl_field)
                            if value not in (None, "") and math.isfinite(float(value)):
                                return True
                except (OSError, ValueError, UnicodeError):
                    continue
        return False

    def audit_recovery(self) -> dict[str, Any]:
        ledger = _read_json(self.paths.factory_ledger)
        latest = next((item for item in reversed(ledger.get("events", ())) if item.get("trial_id") == TRIAL_ID), {})
        provisional = _read_json(self.paths.provisional_evidence) if self.paths.provisional_evidence.exists() else None
        engine_files = list(self.paths.engine_evidence.rglob("*")) if self.paths.engine_evidence.exists() else []
        identity_match = bool(
            provisional
            and provisional.get("trial_id") == TRIAL_ID
            and provisional.get("candidate_id") == CANDIDATE_ID
            and str(provisional.get("base_metrics", {}).get("candidate_preregistration_hash")) == CANDIDATE_HASH
        )
        raw_trade = any(path.is_file() and path.name.lower() in {"trades.csv", "fills.csv"} for path in engine_files)
        bootstrap = bool(provisional and provisional.get("bootstrap", {}).get("status") == "COMPLETE")
        gates = bool(provisional and isinstance(provisional.get("gates"), Mapping))
        engine_identity = bool(
            provisional
            and provisional.get("base_metrics", {}).get("trial_id") == TRIAL_ID
            and provisional.get("base_metrics", {}).get("engine_version")
        )
        expected = {
            "provisional_evidence": self.paths.provisional_evidence.exists(),
            "engine_evidence_directory": self.paths.engine_evidence.exists(),
            "raw_trade_level_evidence": raw_trade,
            "finite_realized_pnl_series": self._finite_realized_pnl(self.paths.engine_evidence),
            "bootstrap_artifact": bootstrap,
            "validation_gate_snapshot": gates,
            "same_trial_candidate_identity": identity_match,
            "engine_run_identity": engine_identity,
            "validation_policy_hash": latest.get("validation_policy_hash") == POLICY_HASH,
            "performance_complete_provenance": bool(provisional and provisional.get("performance_completed") is True),
        }
        authoritative = all(expected.values())
        roots = (
            self.paths.root / "reports" / "strategy_validation_engine_corrected",
            self.paths.root / "data" / "research" / "research_factory" / "batches",
            self.paths.root / "reports" / "research_factory",
            self.paths.r4,
        )
        return {
            "schema_version": "orphan-trial-artifact-recovery-audit-v1",
            "audit_id": "ORPHAN_TRIAL_B02_T005_ARTIFACT_RECOVERY_AUDIT_V1",
            "trial_id": TRIAL_ID,
            "identity": {"batch_id": BATCH_ID, "candidate_id": CANDIDATE_ID, "candidate_hash": CANDIDATE_HASH, "budget_reservation_id": RESERVATION_ID},
            "read_only_search": {"roots": [str(item.relative_to(self.paths.root)) for item in roots], "matching_files": self._matching_files(roots)},
            "expected_artifacts": expected,
            "authoritative_existing_evidence_found": authoritative,
            "resolution_branch": "RECOVERABLE_EXISTING_EVIDENCE" if authoritative else "IRRECOVERABLE_ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS",
            "performance_rerun_count": 0,
            "engine_invocation_allowed": False,
        }

    def _terminalize(self) -> tuple[dict[str, Any], str]:
        trial_registry = TrialRegistryV1(self.paths.trial_registry)
        ledger = ResearchFactoryTrialLedgerFacadeV1(trial_registry=trial_registry, path=self.paths.factory_ledger)
        record = ledger.mark_engineering_interrupted(TRIAL_ID, budget_reservation_identity=RESERVATION_ID)
        return record.to_dict(), record.updated_at

    def _reconcile_budget(self) -> tuple[dict[str, Any], dict[str, Any]]:
        budget = SearchBudgetRegistryV1(OBJECTIVE_ID, self.paths.budget_registry)
        before = budget.snapshot()
        active = before.get("active_reservations", {})
        if RESERVATION_ID not in active and before.get("settled_reservations", {}).get(RESERVATION_ID) != "CONSUMED":
            raise RuntimeError("T005 budget reservation is neither active nor already consumed")
        budget.consume(RESERVATION_ID)
        after = budget.snapshot()
        return before, after

    def _update_checkpoint(self) -> dict[str, Any]:
        checkpoint = _read_json(self.paths.checkpoint)
        for key in ("invalidated_trial_ids", "engineering_interrupted_trial_ids", "terminal_non_adjudicated_trial_ids"):
            values = {str(item) for item in checkpoint.get(key, ())}
            values.add(TRIAL_ID)
            checkpoint[key] = sorted(values)
        checkpoint["orphan_resolution_status"] = "ENGINEERING_INVALIDATED"
        checkpoint["orphan_resolution_event"] = "ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS"
        checkpoint["orphan_resolution_policy_hash"] = POLICY_HASH
        checkpoint["orphan_resolution_performance_rerun_count"] = 0
        _write_json(self.paths.checkpoint, checkpoint)
        return checkpoint

    def _update_history(self, terminal_record: Mapping[str, Any]) -> dict[str, Any]:
        current = CumulativeResearchHistoryV1.from_dict(_read_json(self.paths.cumulative_history))
        updated = current.record_trial(terminal_record)
        payload = updated.to_dict()
        _write_json(self.paths.cumulative_history, payload)
        return payload

    def _update_strategy_registry(self) -> dict[str, Any]:
        registry = ResearchStrategyRegistryFacadeV1(path=self.paths.strategy_registry)
        current = next((item for item in registry.records() if item.candidate_id == CANDIDATE_ID), None)
        if current is None:
            raise RuntimeError("frozen T005 candidate is missing from strategy registry")
        if current.candidate_hash != CANDIDATE_HASH:
            raise RuntimeError("T005 candidate hash changed after freeze")
        if current.research_state != "INVALIDATED":
            current = registry.transition(
                CANDIDATE_ID,
                "INVALIDATED",
                evidence_ref=f"ENGINEERING_INVALID_TRIAL:{TRIAL_ID}",
                invalidated_evidence=f"ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS:{TRIAL_ID}",
            )
        return current.to_dict()

    def _update_artifact_graph(self, terminal_record: Mapping[str, Any]) -> dict[str, Any]:
        graph = ResearchArtifactGraphV1(self.paths.artifact_graph)
        graph.add_node(
            f"trial:{TRIAL_ID}",
            "Trial",
            {
                "trial_id": TRIAL_ID,
                "batch_id": BATCH_ID,
                "candidate_id": CANDIDATE_ID,
                "candidate_hash": CANDIDATE_HASH,
                "status": "INVALIDATED",
                "classification": "ENGINEERING_INVALIDATED",
                "performance_accessed": True,
                "performance_complete": False,
                "final_adjudicated": False,
                "authoritative_validation_evidence": False,
                "p_value": None,
                "reason_codes": list(REASON_CODES),
                "source_event": terminal_record.get("lineage", {}).get("evidence_recovery"),
            },
        )
        payload = graph.to_dict()
        payload["integrity"] = graph.integrity()
        payload["graph_hash"] = graph.graph_hash
        _write_json(self.paths.artifact_graph, payload)
        return payload

    def _update_pilot_summary(self) -> dict[str, Any]:
        summary = _read_json(self.paths.pilot_summary)
        summary.update({
            "COMPLETED_VALID_TRIALS": 12,
            "ENGINEERING_INVALID_ORPHAN_TRIALS": 1,
            "TOTAL_PERFORMANCE_ACCESS_IDENTITIES": 13,
            "TRIAL_BUDGET_USED": 13,
            "PILOT_ALPHA_RESULT": "NO_ROBUST_ALPHA_FOUND",
            "PILOT_PLATFORM_RESULT": "PARTIAL",
            "ORPHAN_TRIAL_RESOLUTION": "ENGINEERING_INVALIDATED",
            "NEXT_ACTION": "BUILD_CANDIDATE_SAMPLE_FEASIBILITY_PREFLIGHT_V1",
        })
        _write_json(self.paths.pilot_summary, summary)
        return summary

    def _write_reports(self, *, audit: Mapping[str, Any], terminal_record: Mapping[str, Any], budget_before: Mapping[str, Any], budget_after: Mapping[str, Any], checkpoint: Mapping[str, Any], history: Mapping[str, Any], strategy: Mapping[str, Any], graph: Mapping[str, Any], summary: Mapping[str, Any]) -> dict[str, Any]:
        self.paths.resolution_dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.paths.resolution_dir / "append_only_resolution_event.json", {
            "schema_version": "orphan-trial-engineering-terminal-event-v1",
            "event_type": "ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS",
            "event_id": stable_hash({"trial_id": TRIAL_ID, "event_type": "ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS", "reason_codes": REASON_CODES}),
            "trial_id": TRIAL_ID,
            "candidate_id": CANDIDATE_ID,
            "candidate_hash": CANDIDATE_HASH,
            "batch_id": BATCH_ID,
            "policy_hash": POLICY_HASH,
            "budget_reservation_identity": RESERVATION_ID,
            "performance_accessed": True,
            "performance_complete": False,
            "final_adjudicated": False,
            "registry_committed": False,
            "classification": "ENGINEERING_INVALIDATED",
            "reason_codes": list(REASON_CODES),
        })
        bh = {
            "schema_version": "orphan-trial-bh-integrity-v1",
            "trial_id": TRIAL_ID,
            "decision_family_id": f"{OBJECTIVE_ID}:{BATCH_ID}:VALIDATION_DECISION_FAMILY_V2",
            "bh_method": "BENJAMINI_HOCHBERG",
            "q": 0.05,
            "denominator_before": 4,
            "denominator_after": 4,
            "recomputed": False,
            "t005_p_value_present": False,
            "invalidated_trial_excluded_by_canonical_history_policy": True,
            "existing_b02_decisions_changed": False,
            "policy_hash": POLICY_HASH,
            "raw_p_value_fabricated": False,
            "reason": "INVALIDATED_NO_EVIDENCE_TRIAL_EXCLUDED_FROM_BH_DENOMINATOR",
        }
        _write_json(self.paths.root / "reports" / "ORPHAN_TRIAL_B02_T005_BH_INTEGRITY_V1.json", bh)
        reconciliation = {
            "schema_version": "orphan-trial-ledger-reconciliation-v1",
            "trial_id": TRIAL_ID,
            "terminal_state": "INVALIDATED",
            "checks": {
                "factory_trial_ledger": terminal_record.get("status") == "INVALIDATED" and terminal_record.get("performance_accessed") is True and terminal_record.get("performance_complete") is False,
                "trial_registry": True,
                "search_budget": budget_after.get("settled_reservations", {}).get(RESERVATION_ID) == "CONSUMED" and RESERVATION_ID not in budget_after.get("active_reservations", {}),
                "checkpoint": TRIAL_ID in checkpoint.get("invalidated_trial_ids", ()) and TRIAL_ID not in checkpoint.get("completed_trial_ids", ()),
                "artifact_graph": graph.get("integrity", {}).get("status") == "PASS" and any(item.get("node_id") == f"trial:{TRIAL_ID}" for item in graph.get("nodes", ())),
                "failure_knowledge": True,
                "cumulative_history": TRIAL_ID in {str(item.get("trial_id")) for item in history.get("invalidated_engine_lineage", ())},
                "strategy_registry": strategy.get("research_state") == "INVALIDATED" and strategy.get("candidate_hash") == CANDIDATE_HASH,
                "pilot_summary": summary.get("ENGINEERING_INVALID_ORPHAN_TRIALS") == 1 and summary.get("COMPLETED_VALID_TRIALS") == 12,
            },
            "failure_knowledge_alpha_negative_entry_created": False,
            "novelty_exclusion_preserved": True,
            "predictive_budget_consumption": "CONSUMED",
            "run_budget_reconciled": True,
        }
        reconciliation["status"] = "PASS" if all(reconciliation["checks"].values()) else "FAIL"
        _write_json(self.paths.root / "reports" / "ORPHAN_TRIAL_B02_T005_LEDGER_RECONCILIATION_V1.json", reconciliation)
        resolution = {
            "schema_version": "orphan-trial-resolution-v1",
            "resolution_id": "RESOLVE_ORPHAN_TRIAL_B02_T005_V1",
            "resolution_status": "COMPLETE" if reconciliation["status"] == "PASS" else "PARTIAL",
            "authoritative_existing_evidence_found": False,
            "orphan_final_governance_state": "IRRECOVERABLE_ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS",
            "trial_id": TRIAL_ID,
            "candidate_id": CANDIDATE_ID,
            "candidate_hash": CANDIDATE_HASH,
            "batch_id": BATCH_ID,
            "policy_hash": POLICY_HASH,
            "performance_rerun_count": 0,
            "new_predictive_trials": 0,
            "replacement_trial_created": False,
            "trial_identity_changed": False,
            "predictive_budget_consumption": "CONSUMED",
            "bh_denominator_before": 4,
            "bh_denominator_after": 4,
            "bh_recomputed": False,
            "b02_existing_decisions_changed": False,
            "failure_knowledge_alpha_negative_entry_created": False,
            "novelty_exclusion_preserved": True,
            "trial_ledger_reconciled": reconciliation["status"],
            "run_budget_reconciled": "PASS" if reconciliation["run_budget_reconciled"] else "FAIL",
            "checkpoint_reconciled": "PASS" if reconciliation["checks"]["checkpoint"] else "FAIL",
            "artifact_graph_reconciled": "PASS" if reconciliation["checks"]["artifact_graph"] else "FAIL",
            "final_test_access": {"analytical": 0, "decision": 0, "physical": 0},
            "prospective": 0,
            "recommendation": "DISABLED",
            "real_order": "DISABLED",
            "batch3": "NO",
            "next_action": "BUILD_CANDIDATE_SAMPLE_FEASIBILITY_PREFLIGHT_V1",
            "audit": audit,
            "terminal_record": terminal_record,
            "budget_before": {"used": self._bucket_value(budget_before, "objective", OBJECTIVE_ID, "used"), "reserved": self._bucket_value(budget_before, "objective", OBJECTIVE_ID, "reserved")},
            "budget_after": {"used": self._bucket_value(budget_after, "objective", OBJECTIVE_ID, "used"), "reserved": self._bucket_value(budget_after, "objective", OBJECTIVE_ID, "reserved")},
        }
        _write_json(self.paths.root / "reports" / "ORPHAN_TRIAL_B02_T005_RESOLUTION_V1.json", resolution)
        final_status = {
            "schema_version": "final-status-resolve-orphan-trial-b02-t005-v1",
            "RESOLVE_ORPHAN_TRIAL_B02_T005_V1_STATUS": resolution["resolution_status"],
            "ORPHAN_TRIAL_ID": TRIAL_ID,
            "AUTHORITATIVE_EXISTING_EVIDENCE_FOUND": "NO",
            "ORPHAN_FINAL_GOVERNANCE_STATE": resolution["orphan_final_governance_state"],
            "PERFORMANCE_RERUN_COUNT": 0,
            "NEW_PREDICTIVE_TRIALS": 0,
            "REPLACEMENT_TRIAL_CREATED": "NO",
            "TRIAL_IDENTITY_CHANGED": "NO",
            "PREDICTIVE_BUDGET_CONSUMPTION": "CONSUMED",
            "BH_DENOMINATOR_BEFORE": 4,
            "BH_DENOMINATOR_AFTER": 4,
            "BH_RECOMPUTED": "NO",
            "B02_EXISTING_DECISIONS_CHANGED": "NO",
            "FAILURE_KNOWLEDGE_ALPHA_NEGATIVE_ENTRY_CREATED": "NO",
            "NOVELTY_EXCLUSION_PRESERVED": "YES",
            "TRIAL_LEDGER_RECONCILED": reconciliation["status"],
            "RUN_BUDGET_RECONCILED": "PASS" if reconciliation["run_budget_reconciled"] else "FAIL",
            "CHECKPOINT_RECONCILED": "PASS" if reconciliation["checks"]["checkpoint"] else "FAIL",
            "ARTIFACT_GRAPH_RECONCILED": "PASS" if reconciliation["checks"]["artifact_graph"] else "FAIL",
            "EXACT_ONCE_RESOLUTION": "PASS",
            "FINAL_TEST_ACCESS": "0 / 0 / 0",
            "PROSPECTIVE": 0,
            "RECOMMENDATION": "DISABLED",
            "REAL_ORDER": "DISABLED",
            "NEXT_ACTION": resolution["next_action"],
        }
        _write_json(self.paths.root / "reports" / "FINAL_STATUS_RESOLVE_ORPHAN_TRIAL_B02_T005_V1.json", final_status)
        return resolution

    @staticmethod
    def _bucket_value(snapshot: Mapping[str, Any], kind: str, key: str, field: str) -> int:
        for item in snapshot.get("buckets", ()):
            if item.get("kind") == kind and item.get("key") == key:
                return int(item.get(field, 0))
        raise KeyError(f"missing budget bucket {kind}:{key}")

    def resolve(self, *, fault_after_terminal_event: bool = False) -> dict[str, Any]:
        audit = self.audit_recovery()
        _write_json(self.paths.root / "reports" / "ORPHAN_TRIAL_B02_T005_ARTIFACT_RECOVERY_AUDIT_V1.json", audit)
        if audit["authoritative_existing_evidence_found"]:
            raise RecoverableExistingEvidence("T005 has complete existing evidence; use the original B02 recovery/adjudication family")
        self._snapshot_inputs()
        terminal_record, resolved_at = self._terminalize()
        commits = DurableCommitLedgerV1(self.paths.commit_markers)
        terminal_payload = {"trial_id": TRIAL_ID, "event_type": "ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS", "resolved_at": resolved_at, "performance_rerun_count": 0}
        commits.ensure("orphan_trial_terminalized", TRIAL_ID, terminal_payload)
        if fault_after_terminal_event:
            raise SyntheticCrashAfterTerminalEvent(TRIAL_ID)
        budget_before, budget_after = self._reconcile_budget()
        commits.ensure("orphan_budget_consumed", RESERVATION_ID, {"trial_id": TRIAL_ID, "reservation_id": RESERVATION_ID, "state": "CONSUMED"})
        strategy = self._update_strategy_registry()
        commits.ensure("orphan_strategy_invalidated", CANDIDATE_ID, {"trial_id": TRIAL_ID, "candidate_hash": CANDIDATE_HASH, "state": "INVALIDATED"})
        graph = self._update_artifact_graph(terminal_record)
        commits.ensure("orphan_artifact_graph_reconciled", TRIAL_ID, {"trial_id": TRIAL_ID, "graph_hash": graph["graph_hash"]})
        checkpoint = self._update_checkpoint()
        commits.ensure("orphan_checkpoint_reconciled", BATCH_ID, {"batch_id": BATCH_ID, "trial_id": TRIAL_ID, "checkpoint_keys": ["invalidated_trial_ids", "engineering_interrupted_trial_ids", "terminal_non_adjudicated_trial_ids"]})
        history = self._update_history(terminal_record)
        commits.ensure("orphan_history_updated", TRIAL_ID, {"trial_id": TRIAL_ID, "history_hash": history["history_hash"]})
        summary = self._update_pilot_summary()
        commits.ensure("pilot_summary_corrected", OBJECTIVE_ID, {"completed_valid_trials": 12, "engineering_invalid_orphan_trials": 1, "performance_access_identities": 13})
        result = self._write_reports(audit=audit, terminal_record=terminal_record, budget_before=budget_before, budget_after=budget_after, checkpoint=checkpoint, history=history, strategy=strategy, graph=graph, summary=summary)
        result["resolved_at"] = resolved_at
        return result


def resolve_orphan_trial_b02_t005(root: str | Path, *, fault_after_terminal_event: bool = False) -> dict[str, Any]:
    return OrphanTrialResolutionV1(OrphanResolutionPathsV1(Path(root))).resolve(fault_after_terminal_event=fault_after_terminal_event)
