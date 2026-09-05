"""Explicit auditable ResearchBatchStateMachineV1."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
import json

from .common import now_timestamp, stable_hash


class ResearchBatchState(str, Enum):
    CREATED = "CREATED"
    BUDGET_RESERVED = "BUDGET_RESERVED"
    DESIGNING = "DESIGNING"
    HYPOTHESES_FROZEN = "HYPOTHESES_FROZEN"
    CANDIDATES_FROZEN = "CANDIDATES_FROZEN"
    STAGE1_VALIDATING = "STAGE1_VALIDATING"
    PERFORMANCE_VALIDATING = "PERFORMANCE_VALIDATING"
    CLASSIFYING = "CLASSIFYING"
    FAILURE_EXTRACTING = "FAILURE_EXTRACTING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    ENGINEERING_BLOCKED = "ENGINEERING_BLOCKED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    INVALIDATED = "INVALIDATED"


_NORMAL = {
    ResearchBatchState.CREATED: {ResearchBatchState.BUDGET_RESERVED},
    ResearchBatchState.BUDGET_RESERVED: {ResearchBatchState.DESIGNING},
    ResearchBatchState.DESIGNING: {ResearchBatchState.HYPOTHESES_FROZEN},
    ResearchBatchState.HYPOTHESES_FROZEN: {ResearchBatchState.CANDIDATES_FROZEN},
    ResearchBatchState.CANDIDATES_FROZEN: {ResearchBatchState.STAGE1_VALIDATING},
    ResearchBatchState.STAGE1_VALIDATING: {ResearchBatchState.PERFORMANCE_VALIDATING, ResearchBatchState.BLOCKED},
    ResearchBatchState.PERFORMANCE_VALIDATING: {ResearchBatchState.CLASSIFYING, ResearchBatchState.ENGINEERING_BLOCKED, ResearchBatchState.BUDGET_EXHAUSTED},
    ResearchBatchState.CLASSIFYING: {ResearchBatchState.FAILURE_EXTRACTING},
    ResearchBatchState.FAILURE_EXTRACTING: {ResearchBatchState.COMPLETED},
}
_CONTROL = {
    ResearchBatchState.DESIGNING: {ResearchBatchState.ENGINEERING_BLOCKED},
    ResearchBatchState.STAGE1_VALIDATING: {ResearchBatchState.BLOCKED},
    ResearchBatchState.PERFORMANCE_VALIDATING: {ResearchBatchState.ENGINEERING_BLOCKED, ResearchBatchState.BUDGET_EXHAUSTED},
}
_TERMINAL = {ResearchBatchState.COMPLETED, ResearchBatchState.BLOCKED, ResearchBatchState.ENGINEERING_BLOCKED, ResearchBatchState.BUDGET_EXHAUSTED, ResearchBatchState.INVALIDATED}


@dataclass(frozen=True)
class StateTransitionV1:
    batch_id: str
    from_state: str
    to_state: str
    reason: str
    transitioned_at: str

    def to_dict(self) -> dict[str, str]:
        return self.__dict__.copy()


class ResearchBatchStateMachineV1:
    def __init__(self, batch_id: str, *, initial_state: ResearchBatchState = ResearchBatchState.CREATED, path: str | Path | None = None):
        self.batch_id = batch_id
        self.state = initial_state
        self.path = Path(path) if path else None
        self.transitions: list[StateTransitionV1] = []
        self._persist()

    def transition(self, target: ResearchBatchState | str, reason: str, *, at: str | None = None) -> StateTransitionV1:
        target_state = ResearchBatchState(target)
        allowed = _NORMAL.get(self.state, set())
        if target_state not in allowed and target_state not in _CONTROL.get(self.state, set()) and target_state != ResearchBatchState.INVALIDATED:
            raise ValueError(f"invalid batch transition: {self.state.value}->{target_state.value}")
        if self.state in _TERMINAL:
            raise ValueError(f"terminal batch cannot transition: {self.state.value}")
        item = StateTransitionV1(self.batch_id, self.state.value, target_state.value, reason, at or now_timestamp())
        self.transitions.append(item)
        self.state = target_state
        self._persist()
        return item

    def audit(self) -> dict[str, Any]:
        return {
            "schema_version": "research-batch-state-machine-v1",
            "batch_id": self.batch_id,
            "state": self.state.value,
            "transitions": [item.to_dict() for item in self.transitions],
            "audit_hash": stable_hash([item.to_dict() for item in self.transitions]),
        }

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.audit(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
