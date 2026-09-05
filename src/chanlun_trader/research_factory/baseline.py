"""BaselineControlRegistryV1: pre-performance control registration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .common import now_timestamp, stable_hash


@dataclass(frozen=True)
class BaselineRegistrationV1:
    baseline_id: str
    batch_id: str
    mechanism: str
    controls: tuple[str, ...]
    registered_at: str
    performance_accessed: bool = False

    @property
    def registration_hash(self) -> str:
        return stable_hash({
            "schema_version": "baseline-registration-v1",
            "baseline_id": self.baseline_id,
            "batch_id": self.batch_id,
            "mechanism": self.mechanism,
            "controls": list(self.controls),
            "registered_at": self.registered_at,
            "performance_accessed": self.performance_accessed,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "baseline-registration-v1",
            "baseline_id": self.baseline_id,
            "batch_id": self.batch_id,
            "mechanism": self.mechanism,
            "controls": list(self.controls),
            "registered_at": self.registered_at,
            "performance_accessed": self.performance_accessed,
            "registration_hash": self.registration_hash,
        }


class BaselineControlRegistryV1:
    """Append-only baseline registry; there is no post-performance registration."""

    def __init__(self):
        self._items: dict[str, BaselineRegistrationV1] = {}

    def register(self, *, batch_id: str, mechanism: str, controls: tuple[str, ...] | list[str]) -> BaselineRegistrationV1:
        baseline_id = f"{batch_id}:{mechanism}"
        if baseline_id in self._items:
            return self._items[baseline_id]
        item = BaselineRegistrationV1(baseline_id, batch_id, mechanism, tuple(controls), now_timestamp())
        self._items[baseline_id] = item
        return item

    def register_plan(self, batch_id: str, baseline_plan: Mapping[str, Any], mechanisms: tuple[str, ...]) -> tuple[BaselineRegistrationV1, ...]:
        controls = tuple(str(item) for item in baseline_plan.get("controls", ()))
        return tuple(self.register(batch_id=batch_id, mechanism=mechanism, controls=controls) for mechanism in mechanisms)

    def items(self) -> tuple[BaselineRegistrationV1, ...]:
        return tuple(self._items[key] for key in sorted(self._items))

    @property
    def head_hash(self) -> str:
        return stable_hash([item.to_dict() for item in self.items()])
