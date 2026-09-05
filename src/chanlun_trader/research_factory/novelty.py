"""Design-only candidate novelty and parameter-neighborhood controls."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
from typing import Any, Iterable, Mapping

from .common import copy_mapping, jsonable, now_timestamp, stable_hash
from .context import PerformanceBlindGuard


def _mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return dict(value)


def design_safe_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    item = _mapping(candidate)
    allowed = {
        "candidate_id", "candidate_hash", "family_id", "family", "mechanism", "factor_ids", "event_ids", "event_dependencies",
        "predicate_fingerprint", "semantic_fingerprint", "parameter_fingerprint", "complexity_fingerprint", "holding_contract_fingerprint",
        "holding_period_days", "candidate_complexity",
    }
    result = {key: item[key] for key in allowed if key in item}
    if "event_ids" not in result and "event_dependencies" in result:
        result["event_ids"] = result.pop("event_dependencies")
    if "family_id" not in result and "family" in result:
        result["family_id"] = result["family"]
    result.setdefault("candidate_hash", stable_hash({key: result[key] for key in sorted(result) if key not in {"candidate_id", "candidate_hash"}}))
    PerformanceBlindGuard.assert_blind(result)
    return jsonable(result)


@dataclass(frozen=True)
class NoveltyDecisionV2:
    candidate_id: str
    allowed: bool
    reason: str
    compared_candidate_ids: tuple[str, ...] = ()
    match_type: str = "NONE"
    comparison_set_hash: str = ""
    exact_duplicate: bool = False
    nearest_semantic_neighbor: str | None = None
    parameter_neighbor: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "candidate-novelty-decision-v2",
            "candidate_id": self.candidate_id,
            "allowed": self.allowed,
            "reason": self.reason,
            "compared_candidate_ids": list(self.compared_candidate_ids),
            "match_type": self.match_type,
            "comparison_set_hash": self.comparison_set_hash,
            "exact_duplicate": self.exact_duplicate,
            "nearest_semantic_neighbor": self.nearest_semantic_neighbor,
            "parameter_neighbor": self.parameter_neighbor,
        }


class CandidateNoveltyGateV2:
    policy_id = "CandidateNoveltyGateV2"
    policy_version = "2.0.0"

    @staticmethod
    def _parameter_neighbor(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        if str(left.get("mechanism")) != str(right.get("mechanism")):
            return False
        if set(left.get("factor_ids", ())) != set(right.get("factor_ids", ())):
            return False
        if set(left.get("event_ids", ())) != set(right.get("event_ids", ())):
            return False
        for key in ("predicate_fingerprint", "semantic_fingerprint"):
            if left.get(key) and right.get(key) and left.get(key) != right.get(key):
                return False
        left_params = left.get("parameter_fingerprint")
        right_params = right.get("parameter_fingerprint")
        if left_params is not None and right_params is not None:
            if left_params == right_params:
                return True
            if isinstance(left_params, Mapping) and isinstance(right_params, Mapping):
                keys = set(left_params) | set(right_params)
                for key in keys:
                    if left_params.get(key) == right_params.get(key):
                        continue
                    try:
                        if abs(float(left_params.get(key)) - float(right_params.get(key))) > 1:
                            return False
                    except (TypeError, ValueError):
                        return False
                return True
            return False
        try:
            return abs(int(left.get("holding_period_days")) - int(right.get("holding_period_days"))) <= 1
        except (TypeError, ValueError):
            return False

    def evaluate(self, candidate: Mapping[str, Any], *, same_batch_candidates: Iterable[Mapping[str, Any]] = (), historical_candidates: Iterable[Mapping[str, Any]] = (), current_registry_candidates: Iterable[Mapping[str, Any]] = (), historical_rejected_candidates: Iterable[Mapping[str, Any]] = (), historical_promising_candidates: Iterable[Mapping[str, Any]] = (), historical_passed_candidates: Iterable[Mapping[str, Any]] = ()) -> NoveltyDecisionV2:
        current = design_safe_candidate(candidate)
        compared: dict[str, dict[str, Any]] = {}
        groups = (
            same_batch_candidates,
            historical_candidates,
            current_registry_candidates,
            historical_rejected_candidates,
            historical_promising_candidates,
            historical_passed_candidates,
        )
        for group in groups:
            for item in group:
                safe = design_safe_candidate(item)
                compared[str(safe.get("candidate_id"))] = safe
        exact_ids = sorted(candidate_id for candidate_id, item in compared.items() if item.get("candidate_hash") == current.get("candidate_hash"))
        comparison_hash = stable_hash(compared)
        if exact_ids:
            return NoveltyDecisionV2(str(current.get("candidate_id")), False, "EXACT_DUPLICATE_CANDIDATE", tuple(exact_ids), "EXACT_HASH", comparison_hash, True, exact_ids[0], False)
        neighbors = sorted(candidate_id for candidate_id, item in compared.items() if self._parameter_neighbor(current, item))
        if neighbors:
            return NoveltyDecisionV2(str(current.get("candidate_id")), False, "PARAMETER_NEIGHBOR_CANDIDATE", tuple(neighbors), "PARAMETER_NEIGHBOR", comparison_hash, False, neighbors[0], True)
        nearest = sorted(compared)[0] if compared else None
        return NoveltyDecisionV2(str(current.get("candidate_id")), True, "NOVEL_CANDIDATE", tuple(sorted(compared)), "NONE", comparison_hash, False, nearest, False)


class CandidateNeighborhoodIndexV1:
    """Durable index containing only design-safe candidate identity fields."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.records: dict[str, dict[str, Any]] = {}
        self._load()

    def add(self, candidate: Mapping[str, Any]) -> dict[str, Any]:
        safe = design_safe_candidate(candidate)
        candidate_id = str(safe.get("candidate_id"))
        if not candidate_id or candidate_id == "None":
            raise ValueError("candidate_id is required")
        current = self.records.get(candidate_id)
        if current is not None and current.get("candidate_hash") != safe.get("candidate_hash"):
            raise ValueError("candidate hash changed in neighborhood index")
        self.records[candidate_id] = safe
        self._persist()
        return safe

    def add_many(self, candidates: Iterable[Mapping[str, Any]]) -> None:
        for candidate in candidates:
            self.add(candidate)

    def all(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.records[key] for key in sorted(self.records))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "candidate-neighborhood-index-v1",
            "records": list(self.all()),
            "design_safe_only": True,
            "index_hash": stable_hash(list(self.all())),
            "updated_at": now_timestamp(),
        }

    @property
    def index_hash(self) -> str:
        return stable_hash(list(self.all()))

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        for item in payload.get("records", ()):
            safe = design_safe_candidate(item)
            self.records[str(safe["candidate_id"])] = safe

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
