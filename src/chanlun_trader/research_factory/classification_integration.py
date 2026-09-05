"""Append-only correction and current-view helpers for validation decisions."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
import json

from .common import now_timestamp, stable_hash


@dataclass(frozen=True)
class ClassificationCorrectionEventV1:
    candidate_id: str
    candidate_hash: str
    original_trial_id: str
    original_classification: str
    local_classification: str
    multiple_testing_family: str
    decision_denominator: int
    adjusted_p: float | None
    adjusted_support: bool
    corrected_effective_classification: str
    reason: str
    source_artifact_hashes: Mapping[str, str]
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            object.__setattr__(self, "created_at", now_timestamp())
        object.__setattr__(self, "source_artifact_hashes", dict(self.source_artifact_hashes))

    @property
    def event_id(self) -> str:
        return stable_hash({
            "candidate_id": self.candidate_id,
            "original_trial_id": self.original_trial_id,
            "original_classification": self.original_classification,
            "corrected_effective_classification": self.corrected_effective_classification,
            "reason": self.reason,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "classification-correction-event-v1",
            "event_id": self.event_id,
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "original_trial_id": self.original_trial_id,
            "original_classification": self.original_classification,
            "local_classification": self.local_classification,
            "multiple_testing_family": self.multiple_testing_family,
            "decision_denominator": self.decision_denominator,
            "adjusted_p": self.adjusted_p,
            "adjusted_support": self.adjusted_support,
            "corrected_effective_classification": self.corrected_effective_classification,
            "reason": self.reason,
            "source_artifact_hashes": dict(self.source_artifact_hashes),
            "created_at": self.created_at,
        }


def append_correction_events(path: Path, events: Iterable[ClassificationCorrectionEventV1]) -> list[dict[str, Any]]:
    """Append new events while preserving existing correction history."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_ids: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                existing_ids.add(str(json.loads(line).get("event_id")))
    appended: list[dict[str, Any]] = []
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            payload = event.to_dict()
            if payload["event_id"] in existing_ids:
                continue
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            existing_ids.add(payload["event_id"])
            appended.append(payload)
    return appended


def materialize_current_effective_strategy_registry(
    historical_records: Iterable[Mapping[str, Any]],
    corrections: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    correction_by_candidate = {str(item["candidate_id"]): dict(item) for item in corrections}
    records: list[dict[str, Any]] = []
    for original in historical_records:
        item = dict(original)
        candidate_id = str(item.get("candidate_id"))
        historical = str(item.get("research_state") or item.get("classification") or "UNKNOWN")
        correction = correction_by_candidate.get(candidate_id)
        effective = str(correction["corrected_effective_classification"]) if correction else historical
        item["historical_recorded_classification"] = historical
        item["current_effective_classification"] = effective
        if "research_state" in item:
            item["research_state"] = effective
        else:
            item["classification"] = effective
        if correction:
            item["applied_correction_event_id"] = correction["event_id"]
        records.append(item)
    counts: dict[str, int] = {}
    for item in records:
        key = str(item["current_effective_classification"])
        counts[key] = counts.get(key, 0) + 1
    return {
        "schema_version": "current-effective-strategy-registry-v1",
        "historical_records_immutable": True,
        "records": sorted(records, key=lambda item: str(item.get("candidate_id"))),
        "effective_classification_counts": dict(sorted(counts.items())),
        "correction_event_count": len(correction_by_candidate),
    }


def materialize_effective_research_history(
    trial_records: Iterable[Mapping[str, Any]],
    decisions: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    decision_by_trial = {str(item["original_trial_id"]): dict(item) for item in decisions}
    rows: list[dict[str, Any]] = []
    for original in trial_records:
        item = dict(original)
        trial_id = str(item.get("trial_id"))
        decision = decision_by_trial.get(trial_id)
        item["original_trial_outcome"] = str(item.get("classification") or "UNKNOWN")
        item["validator_local_outcome"] = str((decision or {}).get("local_classification") or item.get("validator_classification") or "UNKNOWN")
        item["final_adjudicated_outcome"] = str((decision or {}).get("effective_classification") or item.get("classification") or "UNKNOWN")
        if decision:
            item["final_decision_id"] = decision["decision_id"]
        rows.append(item)
    return {
        "schema_version": "current-effective-research-history-v1",
        "performance_rerun": False,
        "rows": rows,
        "effective_final_classifications": {
            key: sum(row["final_adjudicated_outcome"] == key for row in rows)
            for key in sorted({row["final_adjudicated_outcome"] for row in rows})
        },
    }
