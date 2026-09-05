"""Research strategy registry facade and similarity guard."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping
import json
import os

from .common import now_timestamp, stable_hash


STRATEGY_STATES = {
    "DRAFT", "SEMANTIC_READY", "VALIDATION_ELIGIBLE", "VALIDATION_BLOCKED", "REJECTED", "WEAK",
    "PROMISING", "RESEARCH_PASSED", "FORWARD_BLOCKED", "PROSPECTIVE_READY", "PROSPECTIVE_OBSERVING",
    "PROSPECTIVE_SUPPORTED", "RETIRED", "INVALIDATED",
}


@dataclass(frozen=True)
class CandidateSimilarityV1:
    candidate_id: str
    compared_candidate_id: str
    similarity_score: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "compared_candidate_id": self.compared_candidate_id,
            "similarity_score": self.similarity_score,
            "reasons": list(self.reasons),
        }


def candidate_similarity(left: Mapping[str, Any], right: Mapping[str, Any]) -> CandidateSimilarityV1:
    reasons: list[str] = []
    score = 0.0
    if left.get("family_id") == right.get("family_id"):
        score += 0.35
        reasons.append("same_family")
    if left.get("mechanism") == right.get("mechanism"):
        score += 0.30
        reasons.append("same_mechanism")
    if left.get("holding_period_days") == right.get("holding_period_days"):
        score += 0.15
        reasons.append("same_holding")
    elif {left.get("holding_period_days"), right.get("holding_period_days")} <= {4, 5, 6}:
        score += 0.10
        reasons.append("holding_parameter_neighbour")
    left_factors = set(left.get("factor_ids", ()))
    right_factors = set(right.get("factor_ids", ()))
    if left_factors and right_factors:
        overlap = len(left_factors & right_factors) / max(1, len(left_factors | right_factors))
        score += 0.20 * overlap
        if overlap:
            reasons.append("factor_overlap")
    return CandidateSimilarityV1(str(left.get("candidate_id")), str(right.get("candidate_id")), min(1.0, round(score, 6)), tuple(reasons))


@dataclass(frozen=True)
class ResearchStrategyRecordV1:
    candidate_id: str
    candidate_hash: str
    family_id: str
    research_state: str = "DRAFT"
    evidence_refs: tuple[str, ...] = ()
    latest_valid_validation: str | None = None
    promotion_state: str = "DISABLED"
    invalidated_evidence: tuple[str, ...] = ()
    durable_contract_hash: str = ""
    durable_contract_ref: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if self.research_state not in STRATEGY_STATES:
            raise ValueError(f"unsupported strategy state: {self.research_state}")
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "invalidated_evidence", tuple(self.invalidated_evidence))
        if not self.created_at:
            object.__setattr__(self, "created_at", now_timestamp())
        if not self.updated_at:
            object.__setattr__(self, "updated_at", self.created_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "research-strategy-record-v1",
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "family_id": self.family_id,
            "research_state": self.research_state,
            "evidence_refs": list(self.evidence_refs),
            "latest_valid_validation": self.latest_valid_validation,
            "promotion_state": self.promotion_state,
            "invalidated_evidence": list(self.invalidated_evidence),
            "durable_contract_hash": self.durable_contract_hash,
            "durable_contract_ref": self.durable_contract_ref,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ResearchStrategyRegistryFacadeV1:
    """Use existing registries when supplied; keep factory state as metadata."""

    ALLOWED_TRANSITIONS = {
        "DRAFT": {"SEMANTIC_READY", "VALIDATION_BLOCKED", "INVALIDATED"},
        "SEMANTIC_READY": {"VALIDATION_ELIGIBLE", "VALIDATION_BLOCKED", "INVALIDATED"},
        "VALIDATION_ELIGIBLE": {"REJECTED", "WEAK", "PROMISING", "RESEARCH_PASSED", "VALIDATION_BLOCKED", "INVALIDATED"},
        "VALIDATION_BLOCKED": {"RETIRED", "INVALIDATED"},
        "REJECTED": {"RETIRED", "INVALIDATED"},
        "WEAK": {"RETIRED", "FORWARD_BLOCKED", "INVALIDATED"},
        "PROMISING": {"FORWARD_BLOCKED", "PROSPECTIVE_READY", "RETIRED", "INVALIDATED"},
        "RESEARCH_PASSED": {"FORWARD_BLOCKED", "PROSPECTIVE_READY", "RETIRED", "INVALIDATED"},
        "FORWARD_BLOCKED": {"PROSPECTIVE_READY", "RETIRED", "INVALIDATED"},
        "PROSPECTIVE_READY": {"PROSPECTIVE_OBSERVING", "RETIRED", "INVALIDATED"},
        "PROSPECTIVE_OBSERVING": {"PROSPECTIVE_SUPPORTED", "RETIRED", "INVALIDATED"},
        "PROSPECTIVE_SUPPORTED": {"RETIRED", "INVALIDATED"},
        "RETIRED": set(),
        "INVALIDATED": set(),
    }

    def __init__(self, *, candidate_registry: Any | None = None, strategy_library: Any | None = None, promotion_ledger: Any | None = None, path: str | Path | None = None):
        self.candidate_registry = candidate_registry
        self.strategy_library = strategy_library
        self.promotion_ledger = promotion_ledger
        self.path = Path(path) if path else None
        self._records: dict[str, ResearchStrategyRecordV1] = {}
        self._events: dict[str, dict[str, Any]] = {}
        self._load()

    def register(self, candidate_id: str, candidate_hash: str, family_id: str, *, evidence_ref: str | None = None, durable_contract_hash: str = "", durable_contract_ref: str = "") -> ResearchStrategyRecordV1:
        current = self._records.get(candidate_id)
        if current is not None:
            if current.candidate_hash != candidate_hash:
                raise ValueError("candidate hash changed after freeze")
            if durable_contract_hash and current.durable_contract_hash not in {"", durable_contract_hash}:
                raise ValueError("durable candidate contract hash changed after freeze")
            if durable_contract_ref and current.durable_contract_ref not in {"", durable_contract_ref}:
                raise ValueError("durable candidate contract reference changed after freeze")
            if durable_contract_hash or durable_contract_ref:
                current = replace(current, durable_contract_hash=durable_contract_hash or current.durable_contract_hash, durable_contract_ref=durable_contract_ref or current.durable_contract_ref)
                self._records[candidate_id] = current
                self._persist()
            return current
        record = ResearchStrategyRecordV1(candidate_id, candidate_hash, family_id, evidence_refs=(evidence_ref,) if evidence_ref else (), durable_contract_hash=durable_contract_hash, durable_contract_ref=durable_contract_ref)
        self._records[candidate_id] = record
        self._persist()
        return record

    def transition(self, candidate_id: str, target: str, *, evidence_ref: str | None = None, invalidated_evidence: str | None = None) -> ResearchStrategyRecordV1:
        current = self._records[candidate_id]
        event_id = stable_hash({"candidate_id": candidate_id, "from": current.research_state, "target": target, "evidence_ref": evidence_ref, "invalidated_evidence": invalidated_evidence})
        existing_event = self._events.get(event_id)
        if existing_event is not None:
            if existing_event.get("payload_hash") != stable_hash({"candidate_id": candidate_id, "target": target, "evidence_ref": evidence_ref, "invalidated_evidence": invalidated_evidence}):
                raise ValueError("REGISTRY_EVENT_IDENTITY_CONFLICT")
            return current
        if current.research_state == target:
            if evidence_ref and evidence_ref not in current.evidence_refs:
                raise ValueError("REGISTRY_EVENT_IDENTITY_CONFLICT")
            return current
        if target not in STRATEGY_STATES or target not in self.ALLOWED_TRANSITIONS[current.research_state]:
            raise ValueError(f"invalid strategy transition: {current.research_state}->{target}")
        refs = current.evidence_refs + ((evidence_ref,) if evidence_ref else ())
        invalidated = current.invalidated_evidence + ((invalidated_evidence,) if invalidated_evidence else ())
        next_record = replace(current, research_state=target, evidence_refs=refs, invalidated_evidence=invalidated, updated_at=now_timestamp())
        self._records[candidate_id] = next_record
        self._events[event_id] = {"event_id": event_id, "candidate_id": candidate_id, "target": target, "payload_hash": stable_hash({"candidate_id": candidate_id, "target": target, "evidence_ref": evidence_ref, "invalidated_evidence": invalidated_evidence})}
        self._persist()
        return next_record

    def apply_classification(self, candidate_id: str, classification: str, *, evidence_ref: str) -> ResearchStrategyRecordV1:
        target = {
            "RESEARCH_PASSED": "RESEARCH_PASSED",
            "PROMISING": "PROMISING",
            "WEAK": "WEAK",
            "REJECTED": "REJECTED",
            "ENGINEERING_BLOCKED": "VALIDATION_BLOCKED",
        }.get(classification)
        if target is None:
            raise ValueError(f"unsupported validator classification: {classification}")
        if classification == "ENGINEERING_BLOCKED":
            return self.transition(candidate_id, target, evidence_ref=evidence_ref)
        return self.transition(candidate_id, target, evidence_ref=evidence_ref)

    def records(self) -> tuple[ResearchStrategyRecordV1, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self._records.values():
            counts[record.research_state] = counts.get(record.research_state, 0) + 1
        return dict(sorted(counts.items()))

    def migrate_corrected_v3(self, result_payload: Mapping[str, Any], *, semantic_blocked_ids: tuple[str, ...] = ()) -> dict[str, Any]:
        migrated = 0
        for item in result_payload.get("results", ()):
            candidate_id = str(item["candidate_id"])
            classification = str(item.get("classification", "REJECTED"))
            candidate_hash = str(item.get("candidate_preregistration_hash", "UNKNOWN"))
            self.register(candidate_id, candidate_hash, family_id=candidate_id.split("_", 2)[1].lower() if "_" in candidate_id else "UNKNOWN")
            self.transition(candidate_id, "SEMANTIC_READY")
            self.transition(candidate_id, "VALIDATION_ELIGIBLE")
            self.apply_classification(candidate_id, classification, evidence_ref="ENGINE_CORRECTED_V3_METADATA")
            migrated += 1
        for candidate_id in semantic_blocked_ids:
            self.register(candidate_id, stable_hash({"candidate_id": candidate_id}), family_id="semantic_blocked")
            self.transition(candidate_id, "SEMANTIC_READY")
            self.transition(candidate_id, "VALIDATION_BLOCKED", evidence_ref="ENGINE_CORRECTED_V3_SEMANTIC_BLOCK")
        return {"migrated": migrated, "semantic_blocked": len(semantic_blocked_ids), "performance_rerun": False}

    @property
    def head_hash(self) -> str:
        return stable_hash([record.to_dict() for record in self.records()])

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        for item in payload.get("records", ()):
            record = ResearchStrategyRecordV1(**{key: value for key, value in item.items() if key in ResearchStrategyRecordV1.__dataclass_fields__})
            self._records[record.candidate_id] = record
        self._events = {str(item["event_id"]): dict(item) for item in payload.get("events", ()) if item.get("event_id")}

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps({"schema_version": "research-strategy-registry-v2", "records": [item.to_dict() for item in self.records()], "events": [self._events[key] for key in sorted(self._events)]}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
