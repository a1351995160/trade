"""Durable reconstruction views shared by synthetic and production control paths."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .common import jsonable, now_timestamp, stable_hash


FROZEN_CONTRACT_PROVENANCE_FIELDS = frozenset({"created_frozen_timestamp", "source_provenance"})
FROZEN_CONTRACT_RUNTIME_FIELDS = frozenset({
    "attempt_id",
    "attempt_timestamp",
    "content_hash",
    "runtime_attempt_id",
    "runtime_path",
    "temporary_report_path",
})
FROZEN_CONTRACT_GOVERNANCE_FIELDS = frozenset({
    "contract_schema_version",
    "execution_contract_version",
    "factor_event_registry_identities",
    "policy_identity",
    "research_period_identity",
})


def _contract_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise TypeError("frozen contract identity requires a mapping or contract object")
    return jsonable(dict(value))


def canonical_frozen_contract_identity(payload: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Return the immutable identity payload for a frozen Candidate contract.

    Freeze timestamps, source provenance and runtime attempt metadata describe
    how an artifact was produced.  They must not create a second canonical
    identity for unchanged Candidate semantics and governance pins.
    """

    value = _contract_payload(payload)
    return {
        key: item
        for key, item in value.items()
        if key not in FROZEN_CONTRACT_PROVENANCE_FIELDS and key not in FROZEN_CONTRACT_RUNTIME_FIELDS
    }


def canonical_frozen_contract_identity_hash(payload: Mapping[str, Any] | Any) -> str:
    return stable_hash(canonical_frozen_contract_identity(payload))


def _contract_differences(left: Any, right: Any, path: str = "") -> list[dict[str, Any]]:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        result: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right), key=str):
            child_path = f"{path}/{key}" if path else str(key)
            if key not in left or key not in right:
                result.append({"path": child_path, "left": left.get(key), "right": right.get(key)})
            else:
                result.extend(_contract_differences(left[key], right[key], child_path))
        return result
    if isinstance(left, list) and isinstance(right, list):
        result = []
        if len(left) != len(right):
            result.append({"path": path, "left": left, "right": right})
            return result
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            result.extend(_contract_differences(left_item, right_item, f"{path}/{index}"))
        return result
    return [] if left == right else [{"path": path, "left": left, "right": right}]


def _difference_category(path: str) -> str:
    root = path.split("/", 1)[0]
    if root in FROZEN_CONTRACT_PROVENANCE_FIELDS:
        return "PROVENANCE_ONLY"
    if root in FROZEN_CONTRACT_RUNTIME_FIELDS:
        return "RUNTIME_ONLY"
    if root in FROZEN_CONTRACT_GOVERNANCE_FIELDS:
        return "GOVERNANCE_IDENTITY"
    return "SEMANTIC"


def classify_frozen_contract_identity(existing: Mapping[str, Any] | Any, proposed: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Classify an existing/proposed frozen contract pair without mutation."""

    left = _contract_payload(existing)
    right = _contract_payload(proposed)
    differences = _contract_differences(left, right)
    classified = [
        {**item, "category": _difference_category(str(item["path"]))}
        for item in differences
    ]
    same_candidate = left.get("candidate_id") == right.get("candidate_id")
    canonical_equal = canonical_frozen_contract_identity_hash(left) == canonical_frozen_contract_identity_hash(right)
    if not differences:
        classification = "IDENTICAL_ALREADY_PERSISTED"
    elif same_candidate and canonical_equal:
        classification = "NON_SEMANTIC_METADATA_CONFLICT"
    elif same_candidate:
        classification = "SAME_CANDIDATE_ID_DIFFERENT_SEMANTICS"
    else:
        classification = "OTHER_ENGINEERING_DEFECT"
    return {
        "classification": classification,
        "same_candidate_id": same_candidate,
        "canonical_identity_equal": canonical_equal,
        "candidate_id_existing": left.get("candidate_id"),
        "candidate_id_proposed": right.get("candidate_id"),
        "existing_content_hash": left.get("content_hash"),
        "proposed_content_hash": right.get("content_hash"),
        "differing_fields": classified,
    }


def _load_json(path: Path) -> Mapping[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, Mapping) else None


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


@dataclass(frozen=True)
class DurableTrialLedgerRecordV2:
    run_id: str
    batch_id: str
    trial_id: str
    candidate_id: str
    candidate_hash: str
    budget_reservation_identity: str | None = None
    performance_accessed: bool = False
    performance_complete: bool = False
    final_adjudicated: bool = False
    registry_committed: bool = False
    terminal_state: str = ""
    classification: str | None = None
    family_id: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "batch_id": self.batch_id,
            "trial_id": self.trial_id,
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "budget_reservation_identity": self.budget_reservation_identity,
            "performance_accessed": self.performance_accessed,
            "performance_complete": self.performance_complete,
            "final_adjudicated": self.final_adjudicated,
            "registry_committed": self.registry_committed,
            "terminal_state": self.terminal_state,
            "classification": self.classification,
            "family_id": self.family_id,
            "source": self.source,
        }


@dataclass(frozen=True)
class DurableTrialLedgerViewV2:
    """Canonical cumulative trial view for a whole run.

    The view is reconstructed from append-only trial events and every durable
    batch checkpoint.  Aggregate budget counters are deliberately not used as
    the source of truth.
    """

    run_id: str
    records: tuple[Mapping[str, Any], ...] = ()
    loaded_batch_ids: tuple[str, ...] = ()
    reconstructed_at: str = field(default_factory=now_timestamp)

    def __post_init__(self) -> None:
        normalized = tuple(dict(item) for item in self.records)
        object.__setattr__(self, "records", tuple(sorted(normalized, key=lambda item: str(item.get("trial_id", "")))))
        object.__setattr__(self, "loaded_batch_ids", tuple(sorted({str(item) for item in self.loaded_batch_ids})))

    @property
    def view_hash(self) -> str:
        return stable_hash({"run_id": self.run_id, "records": self.records, "loaded_batch_ids": self.loaded_batch_ids})

    @property
    def used_trial_ids(self) -> frozenset[str]:
        return frozenset(str(item["trial_id"]) for item in self.records if item.get("performance_accessed"))

    @property
    def completed_trial_ids(self) -> frozenset[str]:
        return frozenset(str(item["trial_id"]) for item in self.records if item.get("performance_complete") or item.get("final_adjudicated"))

    @property
    def final_adjudicated_trial_ids(self) -> frozenset[str]:
        return frozenset(str(item["trial_id"]) for item in self.records if item.get("final_adjudicated"))

    @property
    def reserved_trial_ids(self) -> frozenset[str]:
        return frozenset(str(item["trial_id"]) for item in self.records if item.get("budget_reserved") and not item.get("performance_complete"))

    def by_batch(self) -> dict[str, tuple[Mapping[str, Any], ...]]:
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for record in self.records:
            grouped.setdefault(str(record.get("batch_id", "")), []).append(record)
        return {key: tuple(value) for key, value in sorted(grouped.items())}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "durable-trial-ledger-view-v2",
            "run_id": self.run_id,
            "records": [dict(item) for item in self.records],
            "loaded_batch_ids": list(self.loaded_batch_ids),
            "used_trial_ids": sorted(self.used_trial_ids),
            "completed_trial_ids": sorted(self.completed_trial_ids),
            "final_adjudicated_trial_ids": sorted(self.final_adjudicated_trial_ids),
            "reserved_trial_ids": sorted(self.reserved_trial_ids),
            "view_hash": self.view_hash,
            "reconstructed_at": self.reconstructed_at,
        }

    def persist(self, path: str | Path) -> None:
        _atomic_write(Path(path), self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DurableTrialLedgerViewV2":
        return cls(
            run_id=str(payload.get("run_id", "")),
            records=tuple(dict(item) for item in payload.get("records", ())),
            loaded_batch_ids=tuple(str(item) for item in payload.get("loaded_batch_ids", ())),
            reconstructed_at=str(payload.get("reconstructed_at", "")),
        )

    @classmethod
    def from_run_dir(cls, run_dir: str | Path, *, run_id: str | None = None) -> "DurableTrialLedgerViewV2":
        root = Path(run_dir)
        resolved_run_id = str(run_id or root.name)
        latest: dict[str, dict[str, Any]] = {}
        loaded_batches: set[str] = set()

        ledger_path = root / "factory" / "factory_trial_ledger.json"
        ledger_payload = _load_json(ledger_path) or {}
        for event in ledger_payload.get("events", ()):
            if isinstance(event, Mapping) and event.get("trial_id") is not None:
                latest[str(event["trial_id"])] = dict(event)

        checkpoint_dir = root / "factory" / "checkpoints"
        if checkpoint_dir.exists():
            checkpoint_paths = sorted(checkpoint_dir.glob("*.json"))
            for checkpoint_path in checkpoint_paths:
                payload = _load_json(checkpoint_path) or {}
                batch_id = str(payload.get("batch_id") or checkpoint_path.stem)
                loaded_batches.add(batch_id)
                for item in payload.get("trial_records", ()):
                    if isinstance(item, Mapping) and item.get("trial_id") is not None:
                        latest.setdefault(str(item["trial_id"]), dict(item))

        persisted = _load_json(root / "durable_trial_ledger_view_v2.json")
        if persisted and not latest:
            return cls.from_dict(persisted)

        records: list[dict[str, Any]] = []
        for trial_id, raw in sorted(latest.items()):
            lineage = raw.get("lineage") if isinstance(raw.get("lineage"), Mapping) else {}
            status = str(raw.get("status") or "")
            final_decision_id = raw.get("final_decision_id") or lineage.get("final_decision_id")
            pending = bool(raw.get("final_adjudication_pending", lineage.get("final_adjudication_pending", False)))
            performance_accessed = bool(raw.get("performance_accessed"))
            performance_complete = bool(raw.get("performance_complete")) or bool(raw.get("performance_completed")) or status in {"PERFORMANCE_COMPLETE_PENDING_ADJUDICATION", "COMPLETED"}
            final_adjudicated = bool(raw.get("final_adjudicated")) or bool(final_decision_id) or (status == "COMPLETED" and not pending and performance_complete)
            registry_committed = bool(raw.get("registry_committed")) or bool(raw.get("registry_transition_committed")) or bool(final_decision_id)
            record = {
                "run_id": str(raw.get("run_id") or resolved_run_id),
                "objective_id": str(raw.get("objective_id") or ""),
                "batch_id": str(raw.get("batch_id") or ""),
                "trial_id": trial_id,
                "hypothesis_id": str(raw.get("hypothesis_id") or ""),
                "candidate_id": str(raw.get("candidate_id") or ""),
                "candidate_hash": str(raw.get("candidate_hash") or ""),
                "dataset_hash": str(raw.get("dataset_hash") or ""),
                "validation_policy_hash": str(raw.get("validation_policy_hash") or ""),
                "engine_hash": str(raw.get("engine_hash") or ""),
                "seed": raw.get("seed"),
                "budget_reservation_identity": raw.get("budget_reservation_identity") or raw.get("reservation_id") or lineage.get("budget_reservation_identity"),
                "budget_reserved": bool(raw.get("budget_reserved")) or bool(raw.get("budget_reservation_identity") or raw.get("reservation_id") or lineage.get("budget_reservation_identity")) or performance_accessed,
                "performance_accessed": performance_accessed,
                "performance_complete": performance_complete,
                "final_adjudicated": final_adjudicated,
                "registry_committed": registry_committed,
                "terminal_state": status,
                "classification": raw.get("classification"),
                "family_id": str(raw.get("family_id") or ""),
                "lineage": dict(lineage),
                "source": str(raw.get("source") or "factory_trial_ledger"),
            }
            records.append(record)
            if record["batch_id"]:
                loaded_batches.add(record["batch_id"])
        return cls(resolved_run_id, tuple(records), tuple(loaded_batches))


def reconstruct_cumulative_trial_view(run_dir: str | Path, *, run_id: str | None = None) -> DurableTrialLedgerViewV2:
    return DurableTrialLedgerViewV2.from_run_dir(run_dir, run_id=run_id)


FROZEN_CANDIDATE_CONTRACT_SCHEMA = "durable-frozen-candidate-contract-v1"
FROZEN_CANDIDATE_CONTRACT_REGISTRY_SCHEMA = "durable-frozen-candidate-contract-registry-v1"
DURABLE_INTERACTION_SEMANTICS_FIELDS = ("eligibility_conditions", "interaction_conditions")


@dataclass(frozen=True)
class DurableFrozenCandidateContractV1:
    """Authoritative, restart-safe contract for a newly frozen candidate.

    The semantic record is stored in full.  The duplicated index fields are
    deliberately explicit so later stages can validate identity without
    reconstructing anything from current defaults.
    """

    candidate_id: str
    candidate_hash: str
    hypothesis_id: str
    hypothesis_fingerprint: str
    semantic_fingerprint: str
    full_semantic_record: Mapping[str, Any]
    family: str
    mechanism: str
    factor_ids: tuple[str, ...]
    factor_roles: tuple[Mapping[str, Any], ...]
    factor_directions: Mapping[str, str]
    event_ids: tuple[str, ...]
    event_timing_semantics: Mapping[str, Any]
    entry_predicate: Mapping[str, Any]
    confirmation_predicate: Mapping[str, Any]
    interaction_semantics: Mapping[str, Any]
    ranking_semantics: Mapping[str, Any]
    selection_rule: Mapping[str, Any]
    top_n: int
    max_positions: int
    holding_period_trading_sessions: int
    entry_timing: Mapping[str, Any]
    exit_contract: Mapping[str, Any]
    execution_contract_version: str
    t_plus_1_contract: Mapping[str, Any]
    capital_product_contract_identity: Mapping[str, Any]
    fee_slippage_contract_references: Mapping[str, Any]
    pit_dependencies: Mapping[str, Any]
    factor_event_registry_identities: Mapping[str, Any]
    research_period_identity: Mapping[str, Any]
    policy_identity: Mapping[str, Any]
    created_frozen_timestamp: str
    source_provenance: Mapping[str, Any]
    contract_schema_version: str
    content_hash: str

    def _payload(self, *, include_content_hash: bool) -> dict[str, Any]:
        payload = {
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "hypothesis_id": self.hypothesis_id,
            "hypothesis_fingerprint": self.hypothesis_fingerprint,
            "semantic_fingerprint": self.semantic_fingerprint,
            "full_semantic_record": self.full_semantic_record,
            "family": self.family,
            "mechanism": self.mechanism,
            "factor_ids": self.factor_ids,
            "factor_roles": self.factor_roles,
            "factor_directions": self.factor_directions,
            "event_ids": self.event_ids,
            "event_timing_semantics": self.event_timing_semantics,
            "entry_predicate": self.entry_predicate,
            "confirmation_predicate": self.confirmation_predicate,
            "interaction_semantics": self.interaction_semantics,
            "ranking_semantics": self.ranking_semantics,
            "selection_rule": self.selection_rule,
            "top_n": self.top_n,
            "max_positions": self.max_positions,
            "holding_period_trading_sessions": self.holding_period_trading_sessions,
            "entry_timing": self.entry_timing,
            "exit_contract": self.exit_contract,
            "execution_contract_version": self.execution_contract_version,
            "t_plus_1_contract": self.t_plus_1_contract,
            "capital_product_contract_identity": self.capital_product_contract_identity,
            "fee_slippage_contract_references": self.fee_slippage_contract_references,
            "pit_dependencies": self.pit_dependencies,
            "factor_event_registry_identities": self.factor_event_registry_identities,
            "research_period_identity": self.research_period_identity,
            "policy_identity": self.policy_identity,
            "created_frozen_timestamp": self.created_frozen_timestamp,
            "source_provenance": self.source_provenance,
            "contract_schema_version": self.contract_schema_version,
        }
        if include_content_hash:
            payload["content_hash"] = self.content_hash
        return jsonable(payload)

    def __post_init__(self) -> None:
        if self.contract_schema_version != FROZEN_CANDIDATE_CONTRACT_SCHEMA:
            raise ValueError("unsupported durable frozen candidate contract schema")
        if not isinstance(self.interaction_semantics, Mapping):
            raise ValueError("durable interaction semantics must be an object")
        unknown_interaction_fields = sorted(set(self.interaction_semantics) - set(DURABLE_INTERACTION_SEMANTICS_FIELDS))
        if unknown_interaction_fields:
            raise ValueError(f"durable interaction semantics fields mismatch: {unknown_interaction_fields}")
        required = self._payload(include_content_hash=False)
        missing = sorted(key for key, value in required.items() if value is None or value == "")
        if missing:
            raise ValueError(f"durable frozen candidate contract missing fields: {missing}")
        if self.top_n < 1 or self.max_positions < 1 or self.top_n > self.max_positions:
            raise ValueError("durable candidate selection contract is invalid")
        if self.holding_period_trading_sessions < 1 or self.holding_period_trading_sessions > 10:
            raise ValueError("durable candidate holding period is invalid")
        if self.candidate_id != str(self.full_semantic_record.get("candidate", {}).get("candidate_id", "")):
            raise ValueError("durable candidate identity does not match full semantic record")
        if self.candidate_hash != str(self.full_semantic_record.get("preregistration_hash", "")):
            raise ValueError("durable candidate hash does not match full semantic record")
        if self.semantic_fingerprint != str(self.full_semantic_record.get("semantic_fingerprint", "")):
            raise ValueError("durable semantic fingerprint does not match full semantic record")
        if self.content_hash != stable_hash(required):
            raise ValueError("durable frozen candidate content hash mismatch")

    def to_dict(self) -> dict[str, Any]:
        return self._payload(include_content_hash=True)

    def canonical_identity_payload(self) -> dict[str, Any]:
        return canonical_frozen_contract_identity(self)

    @property
    def canonical_identity_hash(self) -> str:
        return canonical_frozen_contract_identity_hash(self)

    def identity_audit(self, other: "DurableFrozenCandidateContractV1") -> dict[str, Any]:
        return classify_frozen_contract_identity(self, other)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DurableFrozenCandidateContractV1":
        names = set(cls.__dataclass_fields__)
        unknown = set(payload) - names
        missing = names - set(payload)
        if unknown or missing:
            raise ValueError(f"durable frozen candidate contract fields mismatch: unknown={sorted(unknown)}, missing={sorted(missing)}")
        data = dict(payload)
        for key in ("factor_ids", "event_ids"):
            data[key] = tuple(data[key])
        data["factor_roles"] = tuple(dict(item) for item in data["factor_roles"])
        return cls(**data)

    @classmethod
    def from_semantic_record(
        cls,
        record: Any,
        hypothesis: Mapping[str, Any],
        *,
        factor_event_registry_identities: Mapping[str, Any],
        research_period_identity: Mapping[str, Any],
        policy_identity: Mapping[str, Any],
        source_provenance: Mapping[str, Any],
        created_frozen_timestamp: str | None = None,
    ) -> "DurableFrozenCandidateContractV1":
        candidate = record.candidate
        predicate = record.signal_predicate
        event_ids = tuple(str(item["event_id"]) for item in predicate.event_conditions)
        selection_rule = dict(candidate.selection_rule)
        top_n = selection_rule.get("top_n")
        execution_contract_version = candidate.strategy_dsl.get("semantic_contract_version")
        if not candidate.parent_hypothesis_id or not hypothesis.get("hypothesis_fingerprint"):
            raise ValueError("durable candidate freeze requires authoritative hypothesis lineage")
        if top_n is None or not execution_contract_version:
            raise ValueError("durable candidate freeze requires explicit selection and execution contracts")
        full_record = jsonable(record.to_dict())
        payload = {
            "candidate_id": candidate.candidate_id,
            "candidate_hash": record.preregistration_hash,
            "hypothesis_id": candidate.parent_hypothesis_id,
            "hypothesis_fingerprint": str(hypothesis["hypothesis_fingerprint"]),
            "semantic_fingerprint": record.semantic_fingerprint,
            "full_semantic_record": full_record,
            "family": candidate.strategy_family,
            "mechanism": candidate.mechanism,
            "factor_ids": tuple(str(item["factor_id"]) for item in candidate.factor_bindings),
            "factor_roles": tuple(dict(item) for item in candidate.factor_roles),
            "factor_directions": {str(item["factor_id"]): str(item["direction"]) for item in candidate.factor_roles},
            "event_ids": event_ids,
            "event_timing_semantics": {"conditions": list(predicate.event_conditions), "availability_contract": dict(predicate.availability_contract)},
            "entry_predicate": predicate.to_dict(),
            "confirmation_predicate": {"logic": predicate.logic, "conditions": list(predicate.interaction_conditions)},
            "interaction_semantics": {"interaction_conditions": list(predicate.interaction_conditions), "eligibility_conditions": list(predicate.eligibility_conditions)},
            "ranking_semantics": {"ranking_rule": dict(candidate.ranking_rule), "ranking_factor_id": candidate.signal_logic.get("ranking_factor_id"), "ranking_direction": candidate.signal_logic.get("ranking_direction")},
            "selection_rule": selection_rule,
            "top_n": int(top_n),
            "max_positions": int(candidate.max_positions),
            "holding_period_trading_sessions": int(candidate.holding_period),
            "entry_timing": dict(candidate.entry_timing),
            "exit_contract": {"candidate_exit_rule": dict(candidate.exit_rule), "exit_predicate": record.exit_predicate.to_dict()},
            "execution_contract_version": str(execution_contract_version),
            "t_plus_1_contract": dict(candidate.t_plus_1_contract),
            "capital_product_contract_identity": {"contract_id": "GENERIC_SMALL_CAPITAL_STRATEGY_CONTRACT_V1", "initial_cash": 10000, "max_positions": int(candidate.max_positions), "lot_size_contract": dict(candidate.lot_size_contract)},
            "fee_slippage_contract_references": {"fee_model_reference": candidate.fee_model_reference, "slippage_model_reference": candidate.slippage_model_reference},
            "pit_dependencies": {"required_data": list(candidate.required_data), "available_at_contract": dict(candidate.available_at_contract), "universe_rule": dict(candidate.universe_rule), "pit_requirements": list(predicate.pit_requirements)},
            "factor_event_registry_identities": dict(factor_event_registry_identities),
            "research_period_identity": dict(research_period_identity),
            "policy_identity": dict(policy_identity),
            "created_frozen_timestamp": created_frozen_timestamp or now_timestamp(),
            "source_provenance": dict(source_provenance),
            "contract_schema_version": FROZEN_CANDIDATE_CONTRACT_SCHEMA,
        }
        payload["content_hash"] = stable_hash(payload)
        return cls(**payload)

    def reconstruct_candidate(self) -> Any:
        from chanlun_trader.research.strategy_candidate import StrategyCandidateSpec
        from chanlun_trader.research.strategy_semantic import ExitPredicateSpec, SemanticCandidateRecord, SignalPredicateSpec

        record = self.full_semantic_record
        result = SemanticCandidateRecord(
            candidate=StrategyCandidateSpec.from_dict(record["candidate"]),
            parent_candidate_id=str(record["parent_candidate_id"]),
            previous_preregistration_hash=str(record["previous_preregistration_hash"]),
            semantic_change_reason=str(record["semantic_change_reason"]),
            signal_predicate=SignalPredicateSpec.from_dict(record["signal_predicate"]),
            exit_predicate=ExitPredicateSpec.from_dict(record["exit_predicate"]),
            semantic_status=str(record["semantic_status"]),
            phase4_eligible=bool(record["phase4_eligible"]),
            semantic_fingerprint=str(record["semantic_fingerprint"]),
            preregistration_hash=str(record["preregistration_hash"]),
            created_at=str(record["created_at"]),
        )
        if result.to_dict() != jsonable(record):
            raise ValueError("durable semantic record changed during reconstruction")
        if result.candidate.candidate_id != self.candidate_id or result.preregistration_hash != self.candidate_hash:
            raise ValueError("durable candidate identity changed during reconstruction")
        return result

    def provider_candidate_payload(self) -> dict[str, Any]:
        """Return the canonical executable payload consumed by structural providers.

        Durable contracts intentionally duplicate important execution fields.  This
        boundary reconstructs the authoritative semantic record and proves those
        duplicates still agree before any provider is allowed to read the candidate.
        """

        supported_families = {"DAILY_EVENT", "DAILY_CROSS_SECTIONAL", "DAILY_FACTOR"}
        if self.family not in supported_families:
            raise ValueError(f"candidate family is not a provider-supported DAILY family: {self.family}")

        record = self.reconstruct_candidate()
        candidate = record.candidate
        predicate = record.signal_predicate
        expected_factor_ids = tuple(str(item["factor_id"]) for item in candidate.factor_bindings)
        expected_event_ids = tuple(str(item["event_id"]) for item in predicate.event_conditions)
        expected_factor_roles = tuple(dict(item) for item in candidate.factor_roles)
        expected_factor_directions = {
            str(item["factor_id"]): str(item["direction"])
            for item in candidate.factor_roles
        }
        expected_exit_contract = {
            "candidate_exit_rule": dict(candidate.exit_rule),
            "exit_predicate": record.exit_predicate.to_dict(),
        }
        expected_event_timing = {
            "conditions": list(predicate.event_conditions),
            "availability_contract": dict(predicate.availability_contract),
        }
        expected_confirmation = {
            "logic": predicate.logic,
            "conditions": list(predicate.interaction_conditions),
        }
        expected_interactions = {
            "interaction_conditions": list(predicate.interaction_conditions),
            "eligibility_conditions": list(predicate.eligibility_conditions),
        }
        expected_ranking = {
            "ranking_rule": dict(candidate.ranking_rule),
            "ranking_factor_id": candidate.signal_logic.get("ranking_factor_id"),
            "ranking_direction": candidate.signal_logic.get("ranking_direction"),
        }
        expected_capital_contract = {
            "contract_id": "GENERIC_SMALL_CAPITAL_STRATEGY_CONTRACT_V1",
            "initial_cash": 10000,
            "max_positions": int(candidate.max_positions),
            "lot_size_contract": dict(candidate.lot_size_contract),
        }
        expected_cost_contract = {
            "fee_model_reference": candidate.fee_model_reference,
            "slippage_model_reference": candidate.slippage_model_reference,
        }
        expected_pit_dependencies = {
            "required_data": list(candidate.required_data),
            "available_at_contract": dict(candidate.available_at_contract),
            "universe_rule": dict(candidate.universe_rule),
            "pit_requirements": list(predicate.pit_requirements),
        }
        comparisons = {
            "family": (self.family, candidate.strategy_family),
            "mechanism": (self.mechanism, candidate.mechanism),
            "factor_ids": (self.factor_ids, expected_factor_ids),
            "factor_roles": (jsonable(self.factor_roles), jsonable(expected_factor_roles)),
            "factor_directions": (jsonable(self.factor_directions), jsonable(expected_factor_directions)),
            "event_ids": (self.event_ids, expected_event_ids),
            "event_timing_semantics": (jsonable(self.event_timing_semantics), jsonable(expected_event_timing)),
            "entry_predicate": (jsonable(self.entry_predicate), jsonable(predicate.to_dict())),
            "confirmation_predicate": (jsonable(self.confirmation_predicate), jsonable(expected_confirmation)),
            "interaction_semantics": (jsonable(self.interaction_semantics), jsonable(expected_interactions)),
            "ranking_semantics": (jsonable(self.ranking_semantics), jsonable(expected_ranking)),
            "selection_rule": (jsonable(self.selection_rule), jsonable(candidate.selection_rule)),
            "top_n": (self.top_n, int(candidate.selection_rule.get("top_n", 0))),
            "max_positions": (self.max_positions, int(candidate.max_positions)),
            "holding_period_trading_sessions": (self.holding_period_trading_sessions, int(candidate.holding_period)),
            "entry_timing": (jsonable(self.entry_timing), jsonable(candidate.entry_timing)),
            "exit_contract": (jsonable(self.exit_contract), jsonable(expected_exit_contract)),
            "execution_contract_version": (self.execution_contract_version, candidate.strategy_dsl.get("semantic_contract_version")),
            "t_plus_1_contract": (jsonable(self.t_plus_1_contract), jsonable(candidate.t_plus_1_contract)),
            "capital_product_contract_identity": (jsonable(self.capital_product_contract_identity), jsonable(expected_capital_contract)),
            "fee_slippage_contract_references": (jsonable(self.fee_slippage_contract_references), jsonable(expected_cost_contract)),
            "pit_dependencies": (jsonable(self.pit_dependencies), jsonable(expected_pit_dependencies)),
        }
        mismatches = sorted(name for name, (actual, expected) in comparisons.items() if actual != expected)
        if mismatches:
            raise ValueError(f"durable contract provider execution semantics mismatch: {mismatches}")

        payload = candidate.to_dict()
        payload.update({
            "candidate_hash": self.candidate_hash,
            "signal_predicate": predicate.to_dict(),
            "exit_predicate": record.exit_predicate.to_dict(),
            "factor_ids": list(self.factor_ids),
            "factor_conditions": list(predicate.factor_conditions),
            "event_conditions": list(predicate.event_conditions),
            "factor_event_registry_identities": dict(self.factor_event_registry_identities),
            "holding_period": self.holding_period_trading_sessions,
            "selection_rule": dict(self.selection_rule),
            "max_positions": self.max_positions,
        })
        return jsonable(payload)


class DurableFrozenCandidateContractRegistryV1:
    """Append-only persistence for contracts created at Candidate Freeze."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._contracts: dict[str, DurableFrozenCandidateContractV1] = {}
        self._load()

    def append(self, contract: DurableFrozenCandidateContractV1) -> bool:
        existing = self._contracts.get(contract.candidate_id)
        if existing is not None:
            if existing.to_dict() != contract.to_dict():
                audit = classify_frozen_contract_identity(existing, contract)
                if audit["classification"] == "NON_SEMANTIC_METADATA_CONFLICT":
                    return False
                raise ValueError(
                    f"durable candidate contract identity conflict: {contract.candidate_id}; "
                    f"classification={audit['classification']}"
                )
            return False
        self._contracts[contract.candidate_id] = contract
        return True

    def items(self) -> tuple[DurableFrozenCandidateContractV1, ...]:
        return tuple(self._contracts[key] for key in sorted(self._contracts))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": FROZEN_CANDIDATE_CONTRACT_REGISTRY_SCHEMA, "contracts": [item.to_dict() for item in self.items()], "registry_hash": stable_hash([item.to_dict() for item in self.items()])}

    def write(self) -> Path:
        existing = DurableFrozenCandidateContractRegistryV1(self.path) if self.path.exists() else None
        if existing is not None:
            for contract in existing.items():
                self.append(contract)
        _atomic_write(self.path, self.to_dict())
        return self.path

    @classmethod
    def read(cls, path: str | Path) -> "DurableFrozenCandidateContractRegistryV1":
        return cls(path)

    def _load(self) -> None:
        payload = _load_json(self.path)
        if payload is None:
            return
        if payload.get("schema_version") != FROZEN_CANDIDATE_CONTRACT_REGISTRY_SCHEMA:
            raise ValueError("durable frozen candidate contract registry schema mismatch")
        for item in payload.get("contracts", ()):
            contract = DurableFrozenCandidateContractV1.from_dict(item)
            self.append(contract)
