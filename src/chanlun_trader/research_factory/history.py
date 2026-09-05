"""Cumulative legal predictive-trial history for multiple-testing governance."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
import json
import os

from .common import now_timestamp, stable_hash


LEGAL_CLASSIFICATIONS = {"REJECTED", "WEAK", "PROMISING", "RESEARCH_PASSED"}


@dataclass(frozen=True)
class CumulativeResearchHistoryV1:
    objective_id: str
    valid_predictive_trials: tuple[Mapping[str, Any], ...] = ()
    invalidated_engine_lineage: tuple[Mapping[str, Any], ...] = ()
    created_at: str = field(default_factory=now_timestamp)
    policy_id: str = ""
    policy_version: str = ""
    policy_hash: str = ""
    history_version: str = "2.0.0"
    as_of_batch_id: str | None = None
    source_run_id: str | None = None
    batch_policy_pins: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "valid_predictive_trials", tuple(dict(item) for item in self.valid_predictive_trials))
        object.__setattr__(self, "invalidated_engine_lineage", tuple(dict(item) for item in self.invalidated_engine_lineage))
        object.__setattr__(self, "batch_policy_pins", tuple(dict(item) for item in self.batch_policy_pins))

    def record_trial(self, trial: Mapping[str, Any]) -> "CumulativeResearchHistoryV1":
        classification = str(trial.get("classification") or "")
        if trial.get("status") == "INVALIDATED" or classification in {"ENGINE_ERROR", "ENGINEERING_BLOCKED"}:
            if any(item.get("trial_id") == trial.get("trial_id") for item in self.invalidated_engine_lineage):
                return self
            lineage = self.invalidated_engine_lineage + (dict(trial),)
            return CumulativeResearchHistoryV1(self.objective_id, self.valid_predictive_trials, lineage, self.created_at, self.policy_id, self.policy_version, self.policy_hash, self.history_version, self.as_of_batch_id, self.source_run_id, self.batch_policy_pins)
        if not trial.get("performance_accessed") or classification not in LEGAL_CLASSIFICATIONS:
            return self
        if any(item.get("trial_id") == trial.get("trial_id") for item in self.valid_predictive_trials):
            return self
        pins = list(self.batch_policy_pins)
        batch_id = trial.get("batch_id")
        if batch_id and not any(item.get("batch_id") == batch_id for item in pins):
            pins.append({
                "batch_id": str(batch_id),
                "policy_id": str(trial.get("policy_id") or self.policy_id),
                "policy_version": str(trial.get("policy_version") or self.policy_version),
                "policy_hash": str(trial.get("validation_policy_hash") or self.policy_hash),
            })
        return CumulativeResearchHistoryV1(self.objective_id, self.valid_predictive_trials + (dict(trial),), self.invalidated_engine_lineage, self.created_at, self.policy_id, self.policy_version, self.policy_hash, self.history_version, str(batch_id or self.as_of_batch_id) if batch_id else self.as_of_batch_id, str(trial.get("run_id") or self.source_run_id) if trial.get("run_id") else self.source_run_id, tuple(pins))

    def with_policy_lineage(self, *, policy_id: str, policy_version: str, policy_hash: str, as_of_batch_id: str | None = None, source_run_id: str | None = None) -> "CumulativeResearchHistoryV1":
        return CumulativeResearchHistoryV1(self.objective_id, self.valid_predictive_trials, self.invalidated_engine_lineage, self.created_at, policy_id, policy_version, policy_hash, self.history_version, as_of_batch_id or self.as_of_batch_id, source_run_id or self.source_run_id, self.batch_policy_pins)

    @property
    def multiple_testing_denominator(self) -> int:
        return len(self.valid_predictive_trials)

    def classification_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.valid_predictive_trials:
            key = str(item.get("classification"))
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "cumulative-research-history-v2",
            "objective_id": self.objective_id,
            "valid_predictive_trials": [dict(item) for item in self.valid_predictive_trials],
            "invalidated_engine_lineage": [dict(item) for item in self.invalidated_engine_lineage],
            "multiple_testing_denominator": self.multiple_testing_denominator,
            "classification_counts": self.classification_counts(),
            "created_at": self.created_at,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "history_version": self.history_version,
            "history_hash": self.history_hash,
            "as_of_batch_id": self.as_of_batch_id,
            "source_run_id": self.source_run_id,
            "batch_policy_pins": [dict(item) for item in self.batch_policy_pins],
        }

    @property
    def history_hash(self) -> str:
        return stable_hash({
            "objective_id": self.objective_id,
            "valid_predictive_trials": self.valid_predictive_trials,
            "invalidated_engine_lineage": self.invalidated_engine_lineage,
            "created_at": self.created_at,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "history_version": self.history_version,
            "as_of_batch_id": self.as_of_batch_id,
            "source_run_id": self.source_run_id,
            "batch_policy_pins": self.batch_policy_pins,
        })

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CumulativeResearchHistoryV1":
        return cls(
            objective_id=str(payload.get("objective_id", "")),
            valid_predictive_trials=tuple(payload.get("valid_predictive_trials", ())),
            invalidated_engine_lineage=tuple(payload.get("invalidated_engine_lineage", ())),
            created_at=str(payload.get("created_at", "")),
            policy_id=str(payload.get("policy_id", "")),
            policy_version=str(payload.get("policy_version", "")),
            policy_hash=str(payload.get("policy_hash", "")),
            history_version=str(payload.get("history_version", "2.0.0")),
            as_of_batch_id=payload.get("as_of_batch_id"),
            source_run_id=payload.get("source_run_id"),
            batch_policy_pins=tuple(payload.get("batch_policy_pins", ())),
        )


class CumulativeResearchHistoryStoreV1:
    """Atomic durable history view with trial-lineage idempotency."""

    def __init__(self, path: str | Path, initial: CumulativeResearchHistoryV1):
        self.path = Path(path)
        if self.path.exists():
            self.current = CumulativeResearchHistoryV1.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        else:
            self.current = initial
            self._persist()

    def record_trial(self, trial: Mapping[str, Any]) -> CumulativeResearchHistoryV1:
        self.current = self.current.record_trial(trial)
        self._persist()
        return self.current

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(self.current.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
