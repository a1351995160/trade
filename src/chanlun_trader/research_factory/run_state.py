"""Durable autonomous research run identity, state and checkpoint contracts."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .common import now_timestamp, stable_hash


class AutonomousRunState(str, Enum):
    CREATED = "CREATED"
    READY = "READY"
    BATCH_PLANNING = "BATCH_PLANNING"
    BATCH_RUNNING = "BATCH_RUNNING"
    BATCH_ADJUDICATING = "BATCH_ADJUDICATING"
    BATCH_COMMITTING = "BATCH_COMMITTING"
    BETWEEN_BATCHES = "BETWEEN_BATCHES"
    COMPLETED = "COMPLETED"
    BLOCKED_ENGINEERING = "BLOCKED_ENGINEERING"
    BLOCKED_GOVERNANCE = "BLOCKED_GOVERNANCE"
    STOPPED_BUDGET = "STOPPED_BUDGET"
    STOPPED_NO_CANDIDATES = "STOPPED_NO_CANDIDATES"
    STOPPED_HUMAN = "STOPPED_HUMAN"


_TERMINAL_RUN_STATES = {
    AutonomousRunState.COMPLETED,
    AutonomousRunState.BLOCKED_ENGINEERING,
    AutonomousRunState.BLOCKED_GOVERNANCE,
    AutonomousRunState.STOPPED_BUDGET,
    AutonomousRunState.STOPPED_NO_CANDIDATES,
    AutonomousRunState.STOPPED_HUMAN,
}

_RUN_TRANSITIONS = {
    AutonomousRunState.CREATED: {AutonomousRunState.READY},
    AutonomousRunState.READY: {AutonomousRunState.BATCH_PLANNING, AutonomousRunState.STOPPED_HUMAN},
    AutonomousRunState.BATCH_PLANNING: {AutonomousRunState.BATCH_RUNNING, AutonomousRunState.STOPPED_NO_CANDIDATES, AutonomousRunState.BLOCKED_ENGINEERING, AutonomousRunState.BLOCKED_GOVERNANCE, AutonomousRunState.STOPPED_BUDGET},
    AutonomousRunState.BATCH_RUNNING: {AutonomousRunState.BATCH_ADJUDICATING, AutonomousRunState.BLOCKED_ENGINEERING, AutonomousRunState.BLOCKED_GOVERNANCE, AutonomousRunState.STOPPED_BUDGET},
    AutonomousRunState.BATCH_ADJUDICATING: {AutonomousRunState.BATCH_COMMITTING, AutonomousRunState.BLOCKED_ENGINEERING, AutonomousRunState.BLOCKED_GOVERNANCE},
    AutonomousRunState.BATCH_COMMITTING: {AutonomousRunState.BETWEEN_BATCHES, AutonomousRunState.COMPLETED, AutonomousRunState.BLOCKED_ENGINEERING, AutonomousRunState.BLOCKED_GOVERNANCE, AutonomousRunState.STOPPED_BUDGET, AutonomousRunState.STOPPED_NO_CANDIDATES},
    AutonomousRunState.BETWEEN_BATCHES: {AutonomousRunState.BATCH_PLANNING, AutonomousRunState.COMPLETED, AutonomousRunState.STOPPED_BUDGET, AutonomousRunState.STOPPED_HUMAN},
}


class AutonomousRunTransitionError(RuntimeError):
    pass


def deterministic_batch_id(objective_id: str, run_id: str, batch_number: int) -> str:
    if int(batch_number) < 1:
        raise ValueError("batch_number must be >= 1")
    if not objective_id or not run_id:
        raise ValueError("objective_id and run_id are required")
    return f"{objective_id}_ARUN_{run_id}_B{int(batch_number):02d}"


@dataclass(frozen=True)
class AutonomousResearchRunV2:
    run_id: str
    objective_id: str
    state: str = AutonomousRunState.CREATED.value
    created_at: str = field(default_factory=now_timestamp)
    updated_at: str = field(default_factory=now_timestamp)
    policy_id: str = "VALIDATION_DECISION_POLICY_V2"
    policy_version: str = "2.0.0"
    policy_hash: str = ""
    max_batches: int = 1
    max_total_predictive_trials: int = 1
    max_trials_per_batch: int = 1
    max_hypotheses_per_batch: int = 1
    max_candidates_per_batch: int = 1
    batches_started: int = 0
    batches_completed: int = 0
    current_batch_id: str | None = None
    total_trials_reserved: int = 0
    total_trials_started: int = 0
    total_trials_completed: int = 0
    total_trials_final_adjudicated: int = 0
    stop_reason: str | None = None
    checkpoint_version: str = "autonomous-research-run-v2"
    run_hash: str = ""
    objective_hash: str = ""
    family_diversity_policy_hash: str = ""
    similarity_policy_id: str = "CandidateNoveltyGateV2"
    no_outcome_policy_id: str = "NoOutcomeResearchContextV1"
    backend_type: str = "TEMPLATE"
    backend_version: str = "TemplateResearchAgentBackendV1"
    started_batch_ids: tuple[str, ...] = ()
    completed_batch_ids: tuple[str, ...] = ()
    agent_governance_policy_id: str = "AGENT_MODEL_TOKEN_COST_GOVERNANCE_V1"
    agent_governance_version: str = "1.0.0"
    agent_governance_hash: str = ""

    def __post_init__(self) -> None:
        if not self.run_id or not self.objective_id:
            raise ValueError("run_id and objective_id are required")
        state = AutonomousRunState(self.state)
        object.__setattr__(self, "state", state.value)
        for name in ("max_batches", "max_total_predictive_trials", "max_trials_per_batch", "max_hypotheses_per_batch", "max_candidates_per_batch"):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be positive")
        if min(self.batches_started, self.batches_completed, self.total_trials_reserved, self.total_trials_started, self.total_trials_completed, self.total_trials_final_adjudicated) < 0:
            raise ValueError("run counters cannot be negative")
        object.__setattr__(self, "started_batch_ids", tuple(str(item) for item in self.started_batch_ids))
        object.__setattr__(self, "completed_batch_ids", tuple(str(item) for item in self.completed_batch_ids))
        if self.run_hash == "":
            object.__setattr__(self, "run_hash", stable_hash(self._hash_payload()))

    @classmethod
    def create(cls, *, run_id: str, objective_id: str, policy_id: str, policy_version: str, policy_hash: str, max_batches: int, max_total_predictive_trials: int, max_trials_per_batch: int, max_hypotheses_per_batch: int, max_candidates_per_batch: int, objective_hash: str = "", family_diversity_policy_hash: str = "", backend_type: str = "TEMPLATE", backend_version: str = "TemplateResearchAgentBackendV1", agent_governance_policy_id: str = "AGENT_MODEL_TOKEN_COST_GOVERNANCE_V1", agent_governance_version: str = "1.0.0", agent_governance_hash: str = "") -> "AutonomousResearchRunV2":
        return cls(
            run_id=run_id,
            objective_id=objective_id,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_hash=policy_hash,
            max_batches=max_batches,
            max_total_predictive_trials=max_total_predictive_trials,
            max_trials_per_batch=max_trials_per_batch,
            max_hypotheses_per_batch=max_hypotheses_per_batch,
            max_candidates_per_batch=max_candidates_per_batch,
            objective_hash=objective_hash,
            family_diversity_policy_hash=family_diversity_policy_hash,
            backend_type=backend_type,
            backend_version=backend_version,
            agent_governance_policy_id=agent_governance_policy_id,
            agent_governance_version=agent_governance_version,
            agent_governance_hash=agent_governance_hash,
        )

    def _hash_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("run_hash", None)
        return payload

    @property
    def contract_hash(self) -> str:
        return stable_hash({
            "run_id": self.run_id,
            "objective_id": self.objective_id,
            "objective_hash": self.objective_hash,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "max_batches": self.max_batches,
            "max_total_predictive_trials": self.max_total_predictive_trials,
            "max_trials_per_batch": self.max_trials_per_batch,
            "max_hypotheses_per_batch": self.max_hypotheses_per_batch,
            "max_candidates_per_batch": self.max_candidates_per_batch,
            "family_diversity_policy_hash": self.family_diversity_policy_hash,
            "similarity_policy_id": self.similarity_policy_id,
            "no_outcome_policy_id": self.no_outcome_policy_id,
            "backend_type": self.backend_type,
            "backend_version": self.backend_version,
            "agent_governance_policy_id": self.agent_governance_policy_id,
            "agent_governance_version": self.agent_governance_version,
            "agent_governance_hash": self.agent_governance_hash,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "autonomous-research-run-v2",
            "run_id": self.run_id,
            "objective_id": self.objective_id,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "max_batches": self.max_batches,
            "max_total_predictive_trials": self.max_total_predictive_trials,
            "max_trials_per_batch": self.max_trials_per_batch,
            "max_hypotheses_per_batch": self.max_hypotheses_per_batch,
            "max_candidates_per_batch": self.max_candidates_per_batch,
            "batches_started": self.batches_started,
            "batches_completed": self.batches_completed,
            "current_batch_id": self.current_batch_id,
            "total_trials_reserved": self.total_trials_reserved,
            "total_trials_started": self.total_trials_started,
            "total_trials_completed": self.total_trials_completed,
            "total_trials_final_adjudicated": self.total_trials_final_adjudicated,
            "stop_reason": self.stop_reason,
            "checkpoint_version": self.checkpoint_version,
            "run_hash": self.run_hash,
            "objective_hash": self.objective_hash,
            "family_diversity_policy_hash": self.family_diversity_policy_hash,
            "similarity_policy_id": self.similarity_policy_id,
            "no_outcome_policy_id": self.no_outcome_policy_id,
            "backend_type": self.backend_type,
            "backend_version": self.backend_version,
            "started_batch_ids": list(self.started_batch_ids),
            "completed_batch_ids": list(self.completed_batch_ids),
            "agent_governance_policy_id": self.agent_governance_policy_id,
            "agent_governance_version": self.agent_governance_version,
            "agent_governance_hash": self.agent_governance_hash,
            "contract_hash": self.contract_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AutonomousResearchRunV2":
        fields = {field_name for field_name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in payload.items() if key in fields})

    def transition(self, target: AutonomousRunState | str, reason: str) -> "AutonomousResearchRunV2":
        target_state = AutonomousRunState(target)
        current = AutonomousRunState(self.state)
        if current in _TERMINAL_RUN_STATES:
            if target_state == current:
                return self
            raise AutonomousRunTransitionError(f"terminal run cannot transition: {current.value}")
        if target_state not in _RUN_TRANSITIONS.get(current, set()):
            raise AutonomousRunTransitionError(f"invalid run transition: {current.value}->{target_state.value}")
        return replace(self, state=target_state.value, stop_reason=reason if target_state in _TERMINAL_RUN_STATES else self.stop_reason, updated_at=now_timestamp(), run_hash="")

    def update(self, **changes: Any) -> "AutonomousResearchRunV2":
        if "state" in changes:
            changes["state"] = AutonomousRunState(changes["state"]).value
        changes["updated_at"] = now_timestamp()
        changes["run_hash"] = ""
        return replace(self, **changes)

    def assert_resume_pins(self, expected: Mapping[str, Any]) -> None:
        checks = {
            "objective_id": self.objective_id,
            "objective_hash": self.objective_hash,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "max_batches": self.max_batches,
            "max_total_predictive_trials": self.max_total_predictive_trials,
            "max_trials_per_batch": self.max_trials_per_batch,
            "family_diversity_policy_hash": self.family_diversity_policy_hash,
            "backend_type": self.backend_type,
            "backend_version": self.backend_version,
            "no_outcome_policy_id": self.no_outcome_policy_id,
            "similarity_policy_id": self.similarity_policy_id,
            "agent_governance_policy_id": self.agent_governance_policy_id,
            "agent_governance_version": self.agent_governance_version,
            "agent_governance_hash": self.agent_governance_hash,
        }
        for key, actual in checks.items():
            if key in expected and expected[key] != actual:
                code = "FAIL_CLOSED_POLICY_HASH_MISMATCH" if key.startswith("policy_") else "RUN_CONTRACT_IMMUTABLE_MISMATCH"
                raise RuntimeError(f"{code}: {key}")


class AutonomousRunStoreV1:
    """Atomic checkpoints plus append-only run events."""

    def __init__(self, root: str | Path, run_id: str):
        self.run_dir = Path(root) / "reports" / "research_factory" / "autonomous_runs" / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.contract_path = self.run_dir / "run_contract.json"
        self.checkpoint_path = self.run_dir / "run_checkpoint.json"
        self.events_path = self.run_dir / "run_events.jsonl"
        self.status_path = self.run_dir / "run_status.json"

    @classmethod
    def for_path(cls, run_dir: str | Path) -> "AutonomousRunStoreV1":
        item = Path(run_dir)
        instance = cls.__new__(cls)
        instance.run_dir = item
        instance.contract_path = item / "run_contract.json"
        instance.checkpoint_path = item / "run_checkpoint.json"
        instance.events_path = item / "run_events.jsonl"
        instance.status_path = item / "run_status.json"
        return instance

    @staticmethod
    def _atomic_write(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    def initialize(self, run: AutonomousResearchRunV2) -> AutonomousResearchRunV2:
        if self.contract_path.exists():
            existing = json.loads(self.contract_path.read_text(encoding="utf-8"))
            if existing.get("contract_hash") != run.contract_hash:
                raise RuntimeError("RUN_CONTRACT_IMMUTABLE_MISMATCH")
        else:
            self._atomic_write(self.contract_path, {"schema_version": "autonomous-run-contract-v2", "contract_hash": run.contract_hash, "contract": run.to_dict()})
        self.save(run)
        return run

    def save(self, run: AutonomousResearchRunV2, *, status: Mapping[str, Any] | None = None) -> None:
        self._atomic_write(self.checkpoint_path, run.to_dict())
        self._atomic_write(self.status_path, dict(status or run.to_dict()))

    def append_event(self, event_type: str, payload: Mapping[str, Any], *, event_id: str | None = None) -> str:
        event_id = event_id or stable_hash({"run_id": payload.get("run_id"), "event_type": event_type, "payload": dict(payload)})
        existing_ids: set[str] = set()
        if self.events_path.exists():
            for line in self.events_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    existing_ids.add(str(json.loads(line).get("event_id")))
        if event_id in existing_ids:
            return event_id
        event = {"schema_version": "autonomous-run-event-v1", "event_id": event_id, "event_type": event_type, "created_at": now_timestamp(), **dict(payload)}
        with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return event_id

    def load(self) -> AutonomousResearchRunV2:
        path = self.checkpoint_path if self.checkpoint_path.exists() else self.contract_path
        payload = json.loads(path.read_text(encoding="utf-8"))
        if "contract" in payload:
            payload = payload["contract"]
        return AutonomousResearchRunV2.from_dict(payload)

    def load_status(self) -> dict[str, Any]:
        if not self.status_path.exists():
            return self.load().to_dict()
        return json.loads(self.status_path.read_text(encoding="utf-8"))

    def load_events(self) -> tuple[dict[str, Any], ...]:
        if not self.events_path.exists():
            return ()
        return tuple(json.loads(line) for line in self.events_path.read_text(encoding="utf-8").splitlines() if line.strip())
