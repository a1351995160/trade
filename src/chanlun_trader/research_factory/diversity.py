"""Outcome-blind family/mechanism diversity enforcement at candidate freeze."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .common import copy_mapping, stable_hash
from .novelty import CandidateNoveltyGateV2, design_safe_candidate


class InsufficientDiverseCandidatesError(RuntimeError):
    code = "INSUFFICIENT_DIVERSE_CANDIDATES"


@dataclass(frozen=True)
class FamilyDiversityPolicyV1:
    max_candidates_per_family: int
    max_candidates_per_mechanism: int
    minimum_distinct_mechanisms_when_feasible: int = 1
    max_parameter_neighbor_count: int = 0
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_candidates_per_family < 1 or self.max_candidates_per_mechanism < 1:
            raise ValueError("family and mechanism quotas must be positive")
        if self.minimum_distinct_mechanisms_when_feasible < 1 or self.max_parameter_neighbor_count < 0:
            raise ValueError("diversity limits are invalid")
        object.__setattr__(self, "provenance", copy_mapping(self.provenance))

    @property
    def policy_hash(self) -> str:
        return stable_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "family-diversity-policy-v1",
            "max_candidates_per_family": self.max_candidates_per_family,
            "max_candidates_per_mechanism": self.max_candidates_per_mechanism,
            "minimum_distinct_mechanisms_when_feasible": self.minimum_distinct_mechanisms_when_feasible,
            "max_parameter_neighbor_count": self.max_parameter_neighbor_count,
            "provenance": dict(self.provenance),
            "outcome_blind": True,
        }


@dataclass(frozen=True)
class DiversityFreezeResultV1:
    accepted_candidates: tuple[dict[str, Any], ...]
    rejected_candidates: tuple[dict[str, Any], ...]
    family_counts: Mapping[str, int]
    mechanism_counts: Mapping[str, int]
    distinct_mechanisms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "diversity-freeze-result-v1",
            "accepted_candidates": [dict(item) for item in self.accepted_candidates],
            "rejected_candidates": [dict(item) for item in self.rejected_candidates],
            "family_counts": dict(self.family_counts),
            "mechanism_counts": dict(self.mechanism_counts),
            "distinct_mechanisms": self.distinct_mechanisms,
        }


class FamilyDiversityEnforcerV1:
    def __init__(self, policy: FamilyDiversityPolicyV1):
        self.policy = policy
        self.novelty = CandidateNoveltyGateV2()

    def freeze(self, candidates: Iterable[Mapping[str, Any]], *, required_count: int | None = None) -> DiversityFreezeResultV1:
        source = [dict(item) for item in candidates]
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        family_counts: dict[str, int] = {}
        mechanism_counts: dict[str, int] = {}
        neighbor_counts: dict[str, int] = {}

        def choose(item: dict[str, Any]) -> bool:
            safe_item = design_safe_candidate(item)
            family = str(safe_item.get("family_id", "UNKNOWN"))
            mechanism = str(safe_item.get("mechanism", "UNKNOWN"))
            if family_counts.get(family, 0) >= self.policy.max_candidates_per_family:
                return False
            if mechanism_counts.get(mechanism, 0) >= self.policy.max_candidates_per_mechanism:
                return False
            for prior in accepted:
                if self.novelty._parameter_neighbor(safe_item, design_safe_candidate(prior)):
                    key = str(item.get("candidate_id"))
                    neighbor_counts[key] = neighbor_counts.get(key, 0) + 1
            if neighbor_counts.get(str(item.get("candidate_id")), 0) > self.policy.max_parameter_neighbor_count:
                return False
            return True

        # Reserve the first slot for distinct mechanisms when the proposal supply makes it feasible.
        mechanisms = []
        for item in source:
            mechanism = str(design_safe_candidate(item).get("mechanism", "UNKNOWN"))
            if mechanism not in mechanisms:
                mechanisms.append(mechanism)
        minimum = min(self.policy.minimum_distinct_mechanisms_when_feasible, len(mechanisms))
        for mechanism in mechanisms[:minimum]:
            for item in source:
                if str(design_safe_candidate(item).get("mechanism", "UNKNOWN")) == mechanism and choose(item):
                    accepted.append(item)
                    family = str(design_safe_candidate(item).get("family_id", "UNKNOWN"))
                    family_counts[family] = family_counts.get(family, 0) + 1
                    mechanism_counts[mechanism] = mechanism_counts.get(mechanism, 0) + 1
                    break
        for item in source:
            if item in accepted:
                continue
            if choose(item) and (required_count is None or len(accepted) < required_count):
                accepted.append(item)
                safe_item = design_safe_candidate(item)
                family = str(safe_item.get("family_id", "UNKNOWN"))
                mechanism = str(safe_item.get("mechanism", "UNKNOWN"))
                family_counts[family] = family_counts.get(family, 0) + 1
                mechanism_counts[mechanism] = mechanism_counts.get(mechanism, 0) + 1
            else:
                rejected.append({"candidate_id": item.get("candidate_id"), "reason": "DIVERSITY_QUOTA"})
        if required_count is not None and len(accepted) < required_count:
            raise InsufficientDiverseCandidatesError("INSUFFICIENT_DIVERSE_CANDIDATES")
        return DiversityFreezeResultV1(tuple(accepted), tuple(rejected), dict(sorted(family_counts.items())), dict(sorted(mechanism_counts.items())), len(mechanism_counts))
