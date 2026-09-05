"""Factory-level failure snapshot adapter over FailureLibrary/FailureKnowledgeView."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .common import now_timestamp, stable_hash
from .context import PerformanceBlindGuard


FAILURE_CATEGORIES = {
    "ALPHA_FAILURE", "ROBUSTNESS_FAILURE", "EXECUTION_FAILURE", "DATA_FAILURE",
    "PIT_FAILURE", "SAMPLE_FAILURE", "SAMPLE_FEASIBILITY_FAILURE", "ENGINE_FAILURE", "GOVERNANCE_FAILURE",
}


@dataclass(frozen=True)
class FailureKnowledgeEntryV1:
    category: str
    mechanism: str
    high_level_reason: str
    constraints: tuple[str, ...] = ()
    source_trial_ids: tuple[str, ...] = ()
    reason_code: str = ""

    def __post_init__(self) -> None:
        if self.category not in FAILURE_CATEGORIES:
            raise ValueError(f"unsupported failure category: {self.category}")
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "source_trial_ids", tuple(self.source_trial_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "mechanism": self.mechanism,
            "high_level_reason": self.high_level_reason,
            "reason_code": self.reason_code or self.high_level_reason,
            "constraints": list(self.constraints),
            "source_trial_ids": list(self.source_trial_ids),
        }


@dataclass(frozen=True)
class FailureKnowledgeSnapshotV1:
    snapshot_id: str
    entries: tuple[FailureKnowledgeEntryV1, ...] = ()
    parent_snapshot_id: str | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))
        if not self.created_at:
            object.__setattr__(self, "created_at", now_timestamp())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "failure-knowledge-snapshot-v1",
            "snapshot_id": self.snapshot_id,
            "parent_snapshot_id": self.parent_snapshot_id,
            "created_at": self.created_at,
            "entries": [item.to_dict() for item in self.entries],
            "exact_performance_values_exposed": False,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FailureKnowledgeSnapshotV1":
        entries = tuple(FailureKnowledgeEntryV1(**{key: value for key, value in item.items() if key in {"category", "mechanism", "high_level_reason", "constraints", "source_trial_ids", "reason_code"}}) for item in payload.get("entries", ()))
        return cls(
            snapshot_id=str(payload.get("snapshot_id", "")),
            entries=entries,
            parent_snapshot_id=payload.get("parent_snapshot_id"),
            created_at=str(payload.get("created_at", "")),
        )

    @property
    def snapshot_hash(self) -> str:
        return stable_hash(self.to_dict())

    def next_batch_view(self) -> tuple[dict[str, Any], ...]:
        return tuple({
            "category": item.category,
            "mechanism": item.mechanism,
            "high_level_reason": item.high_level_reason,
            "reason_code": item.reason_code or item.high_level_reason,
            "constraints": list(item.constraints),
        } for item in self.entries)

    def sanitized_view(self, *, source_batch_ids: tuple[str, ...] = (), source_history_hash: str = "", sanitization_policy_id: str = "FAILURE_KNOWLEDGE_SANITIZATION_V1") -> "FailureKnowledgeViewV1":
        return FailureKnowledgeViewV1(
            failure_view_version="1.0.0",
            entries=self.next_batch_view(),
            source_batch_ids=tuple(source_batch_ids),
            source_history_hash=source_history_hash,
            sanitization_policy_id=sanitization_policy_id,
        )


@dataclass(frozen=True)
class FailureKnowledgeViewV1:
    """Only the legal, qualitative failure view allowed in next-batch design."""

    failure_view_version: str
    entries: tuple[Mapping[str, Any], ...] = ()
    source_batch_ids: tuple[str, ...] = ()
    source_history_hash: str = ""
    sanitization_policy_id: str = "FAILURE_KNOWLEDGE_SANITIZATION_V1"
    context_hash: str = ""

    def __post_init__(self) -> None:
        safe_entries = tuple({
            "category": str(item.get("category", "")),
            "mechanism": str(item.get("mechanism", "")),
            "high_level_reason": str(item.get("high_level_reason", "")),
            "reason_code": str(item.get("reason_code", "")),
            "constraints": [str(value) for value in item.get("constraints", ())],
        } for item in self.entries)
        payload = {
            "failure_view_version": self.failure_view_version,
            "entries": safe_entries,
            "source_batch_ids": tuple(self.source_batch_ids),
            "source_history_hash": self.source_history_hash,
            "sanitization_policy_id": self.sanitization_policy_id,
        }
        PerformanceBlindGuard.assert_blind(payload)
        object.__setattr__(self, "entries", safe_entries)
        object.__setattr__(self, "source_batch_ids", tuple(str(item) for item in self.source_batch_ids))
        if not self.context_hash:
            object.__setattr__(self, "context_hash", stable_hash(payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "failure-knowledge-view-v1",
            "failure_view_version": self.failure_view_version,
            "entries": [dict(item) for item in self.entries],
            "source_batch_ids": list(self.source_batch_ids),
            "source_history_hash": self.source_history_hash,
            "sanitization_policy_id": self.sanitization_policy_id,
            "context_hash": self.context_hash,
            "exact_performance_values_exposed": False,
            "view_hash": self.view_hash,
        }

    @property
    def view_hash(self) -> str:
        return stable_hash({
            "failure_view_version": self.failure_view_version,
            "entries": self.entries,
            "source_batch_ids": self.source_batch_ids,
            "source_history_hash": self.source_history_hash,
            "sanitization_policy_id": self.sanitization_policy_id,
        })

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FailureKnowledgeViewV1":
        return cls(
            failure_view_version=str(payload.get("failure_view_version", "1.0.0")),
            entries=tuple(payload.get("entries", ())),
            source_batch_ids=tuple(str(item) for item in payload.get("source_batch_ids", ())),
            source_history_hash=str(payload.get("source_history_hash", "")),
            sanitization_policy_id=str(payload.get("sanitization_policy_id", "FAILURE_KNOWLEDGE_SANITIZATION_V1")),
            context_hash=str(payload.get("context_hash", "")),
        )


class FailureKnowledgeAdapterV1:
    """Map validation outcomes without turning engineering errors into alpha failures."""

    def __init__(self, failure_library: Any | None = None, failure_view: Any | None = None):
        self.failure_library = failure_library
        self.failure_view = failure_view

    @staticmethod
    def _category(record: Mapping[str, Any]) -> tuple[str, str, tuple[str, ...]] | None:
        decision = record.get("final_decision")
        decision = decision if isinstance(decision, Mapping) else {}
        classification = str(record.get("classification") or decision.get("effective_classification") or "")
        reason = " ".join(str(item) for item in record.get("reason_codes", ()))
        if not reason and decision.get("reason"):
            reason = str(decision["reason"])
        if classification in {"RESEARCH_PASSED", "PROMISING"}:
            return None
        local_classification = str(record.get("local_classification") or "")
        failure_category = str(
            record.get("failure_category")
            or record.get("final_failure_category")
            or decision.get("failure_category")
            or ""
        )
        if classification == "BLOCKED" and failure_category in FAILURE_CATEGORIES:
            category_constraints = {
                "ENGINE_FAILURE": ("ENGINE_FAILURE", ("stop_factory", "do_not_classify_as_rejected")),
                "PIT_FAILURE": ("PIT_BLOCKED", ("require_pit_evidence",)),
                "DATA_FAILURE": ("DATA_BLOCKED", ("require_data_capability",)),
                "SAMPLE_FAILURE": ("RAW_BOOTSTRAP_NOT_SUPPORTED", ("do_not_retune_same_batch",)),
                "SAMPLE_FEASIBILITY_FAILURE": ("SAMPLE_FEASIBILITY_FAILURE", ("do_not_retune_same_batch", "preserve_frozen_candidate")),
                "GOVERNANCE_FAILURE": ("GOVERNANCE_BLOCKED", ("review_governance",)),
                "EXECUTION_FAILURE": ("EXECUTION_BLOCKED", ("review_execution_contract",)),
            }
            reason_code, constraints = category_constraints.get(failure_category, ("GOVERNANCE_BLOCKED", ("review_governance",)))
            return failure_category, reason_code, constraints
        if classification in {"ENGINE_ERROR", "ENGINEERING_BLOCKED"} or local_classification in {"ENGINE_ERROR", "ENGINEERING_BLOCKED"} or "ENGINE" in reason or "EXECUTION_INTEGRITY" in reason or "DATA_PIPELINE" in reason:
            return "ENGINE_FAILURE", "ENGINE_FAILURE", ("stop_factory", "do_not_classify_as_rejected")
        if "PIT" in reason:
            return "PIT_FAILURE", "PIT_BLOCKED", ("require_pit_evidence",)
        if "DATA" in reason:
            return "DATA_FAILURE", "DATA_BLOCKED", ("require_data_capability",)
        if "SAMPLE" in reason or "INSUFFICIENT" in reason or record.get("validator_classification") == "INSUFFICIENT_EVIDENCE":
            return "SAMPLE_FAILURE", "INSUFFICIENT_SAMPLE", ("collect_pre_registered_sample",)
        bootstrap = record.get("bootstrap")
        if classification == "WEAK" or (isinstance(bootstrap, Mapping) and bootstrap.get("status") != "COMPLETE"):
            return "SAMPLE_FAILURE", "RAW_BOOTSTRAP_NOT_SUPPORTED", ("do_not_retune_same_batch",)
        if classification == "REJECTED":
            metrics = record.get("base_metrics") or record.get("metrics") or {}
            if isinstance(metrics, Mapping) and metrics.get("net_return") is not None and float(metrics.get("net_return")) <= 0:
                return "ALPHA_FAILURE", "BASE_RETURN_NONPOSITIVE", ("do_not_retune_same_batch",)
            if isinstance(metrics, Mapping) and metrics.get("profit_factor") is not None and float(metrics.get("profit_factor")) <= 1.0:
                return "ALPHA_FAILURE", "BASE_PROFIT_FACTOR_NOT_SUPPORTED", ("do_not_retune_same_batch",)
            cost = record.get("cost_stress")
            combined = cost.get("COMBINED_X2", {}) if isinstance(cost, Mapping) else {}
            if "COST" in reason or "COMBINED_X2" in reason or (isinstance(combined, Mapping) and combined.get("net_return") is not None and float(combined.get("net_return")) <= 0):
                return "ROBUSTNESS_FAILURE", "COMBINED_COST_STRESS_FAILURE", ("preserve_frozen_cost_model",)
            return "ALPHA_FAILURE", "ALPHA_EVIDENCE_NOT_SUPPORTED", ("do_not_retune_same_batch",)
        return "GOVERNANCE_FAILURE", "ILLEGAL_OR_MISSING_CLASSIFICATION", ("review_governance",)

    def snapshot_from_trials(self, records: Iterable[Mapping[str, Any]], *, snapshot_id: str, parent_snapshot_id: str | None = None) -> FailureKnowledgeSnapshotV1:
        grouped: dict[tuple[str, str, str, tuple[str, ...]], list[str]] = {}
        for record in records:
            classified = self._category(record)
            if classified is None:
                continue
            category, reason, constraints = classified
            if category == "ENGINE_FAILURE":
                # Keep lineage in the snapshot but never forward it as alpha failure knowledge.
                mechanism = str(record.get("family_id") or "UNKNOWN")
            else:
                mechanism = str(record.get("family_id") or record.get("mechanism") or "UNKNOWN")
            key = (category, mechanism, reason, constraints)
            grouped.setdefault(key, []).append(str(record.get("trial_id", "")))
        entries = tuple(FailureKnowledgeEntryV1(category, mechanism, reason, constraints, tuple(ids), reason) for (category, mechanism, reason, constraints), ids in sorted(grouped.items()))
        return FailureKnowledgeSnapshotV1(snapshot_id=snapshot_id, entries=entries, parent_snapshot_id=parent_snapshot_id)

    def build_next_batch_view(self, snapshot: FailureKnowledgeSnapshotV1, *, source_batch_ids: tuple[str, ...] = (), source_history_hash: str = "") -> FailureKnowledgeViewV1:
        return snapshot.sanitized_view(source_batch_ids=source_batch_ids, source_history_hash=source_history_hash)

    def record_legacy_failure(self, experiment_id: str, category: str, note: str = "") -> None:
        if category == "ENGINE_FAILURE":
            return
        if self.failure_library is not None and hasattr(self.failure_library, "add"):
            from chanlun_trader.research.experiment import FailureClass
            mapping = {"ALPHA_FAILURE": FailureClass.NO_ALPHA, "PIT_FAILURE": FailureClass.PIT_UNSAFE, "DATA_FAILURE": FailureClass.DATA_UNAVAILABLE}
            if category in mapping:
                self.failure_library.add(experiment_id, mapping[category], note)
