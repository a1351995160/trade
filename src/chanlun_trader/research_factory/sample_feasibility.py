"""Outcome-blind structural sample-feasibility preflight.

This module only counts legally possible research opportunities.  It never
loads result-bearing artifacts and never evaluates a candidate's outcome.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
import re
from typing import Any, Iterable, Iterator, Mapping

from .common import jsonable, stable_hash
from .context import PerformanceBlindGuard
from chanlun_trader.research.validation_policy_v2 import ValidationDecisionPolicyV2


PREFLIGHT_VERSION = "CandidateSampleFeasibilityPreflightV1"
PASS = "PASS"
BLOCKED_INSUFFICIENT_FEASIBILITY = "BLOCKED_INSUFFICIENT_FEASIBILITY"
UNKNOWN = "UNKNOWN"
VALID_STATUSES = frozenset({PASS, BLOCKED_INSUFFICIENT_FEASIBILITY, UNKNOWN})

REASON_CODES = frozenset({
    "INSUFFICIENT_EVENT_OCCURRENCES",
    "INSUFFICIENT_ELIGIBLE_DATES",
    "INSUFFICIENT_EXECUTABLE_OPPORTUNITIES",
    "END_OF_WINDOW_TRUNCATION",
    "DATA_COVERAGE_INSUFFICIENT",
    "PIT_STATE_UNKNOWN",
    "PORTFOLIO_CAPACITY_BOUND",
    "STRUCTURE_EXIT_COUNT_UNKNOWN",
    "SAMPLE_FEASIBILITY_UNKNOWN",
})

FORBIDDEN_PERFORMANCE_SOURCES = (
    "trades.csv",
    "equity.csv",
    "performance_metrics",
    "bootstrap_reports",
    "multiple_testing.json",
    "final_decisions",
    "Final Test",
    "Prospective",
)


class SampleFeasibilityContractError(ValueError):
    """The frozen candidate or structural observation contract is invalid."""


class ObservationPartitionStoreV1:
    """Bounded-memory, run-scoped structural observation store.

    The provider writes deterministic date partitions and the preflight reads
    one partition at a time.  Only structural fields are persisted; the
    store never contains returns, PnL, fills, or validation outcomes.
    """

    version = "ObservationPartitionStoreV1"

    def __init__(self, manifest_path: str | Path, manifest: Mapping[str, Any]):
        self.manifest_path = Path(manifest_path)
        self._manifest = dict(manifest)
        self.checkpoint_path = self.manifest_path.with_name("preflight_checkpoint.json")

    @classmethod
    def open(cls, manifest_path: str | Path) -> "ObservationPartitionStoreV1":
        path = Path(manifest_path)
        return cls(path, json.loads(path.read_text(encoding="utf-8")))

    @property
    def observation_count(self) -> int:
        return int(self._manifest.get("observation_count", 0))

    @property
    def observation_hash(self) -> str:
        return str(self._manifest.get("observation_hash", "UNKNOWN"))

    @property
    def partitions(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._manifest.get("partitions", ()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "store_version": self.version,
            "manifest_id": self._manifest.get("manifest_id", "UNKNOWN"),
            "partition_count": len(self.partitions),
            "observation_count": self.observation_count,
            "observation_hash": self.observation_hash,
            "partition_policy": self._manifest.get("partition_policy", {}),
            "cache_lifecycle": "run_scoped_partition_cache",
            "outcome_blind": True,
            "performance_data_loaded": False,
        }

    def iter_partitions(self) -> Iterator[tuple[Mapping[str, Any], list[dict[str, Any]]]]:
        for descriptor in self.partitions:
            path = self.manifest_path.parent / str(descriptor["file"])
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            yield descriptor, rows

    def checkpoint(self, payload: Mapping[str, Any]) -> None:
        self.checkpoint_path.write_text(
            json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def load_checkpoint(self) -> dict[str, Any] | None:
        if not self.checkpoint_path.exists():
            return None
        return json.loads(self.checkpoint_path.read_text(encoding="utf-8"))

    def clear_checkpoint(self) -> None:
        if self.checkpoint_path.exists():
            self.checkpoint_path.unlink()

    def cleanup(self) -> None:
        for descriptor in self.partitions:
            path = self.manifest_path.parent / str(descriptor["file"])
            if path.exists():
                path.unlink()
        self.clear_checkpoint()
        if self.manifest_path.exists():
            self.manifest_path.unlink()

    @staticmethod
    def row_digest(row: Mapping[str, Any]) -> str:
        encoded = json.dumps(jsonable(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def minimum_required_sample_count(policy: ValidationDecisionPolicyV2) -> int:
    """Extract the sample minimum from the active frozen policy contract."""

    policy.assert_active()
    gate = policy.hard_gates.get("sample_adequacy")
    if not isinstance(gate, Mapping):
        raise SampleFeasibilityContractError("ValidationDecisionPolicyV2 sample_adequacy gate is missing")
    if str(gate.get("threshold_source")) != "PRE_EXISTING_FROZEN_POLICY":
        raise SampleFeasibilityContractError("sample threshold is not sourced from the frozen policy")
    match = re.fullmatch(r"closed_trade_count\s*>=\s*(\d+).*", str(gate.get("threshold", "")))
    if match is None:
        raise SampleFeasibilityContractError("frozen sample threshold is not machine-readable")
    value = int(match.group(1))
    if value <= 0:
        raise SampleFeasibilityContractError("frozen sample threshold must be positive")
    return value


def performance_file_access_audit() -> dict[str, Any]:
    """Return the explicit source audit for this in-memory preflight component."""

    return {
        "component": PREFLIGHT_VERSION,
        "input_mode": "IN_MEMORY_STRUCTURAL_CONTRACTS_ONLY",
        "performance_files_read": [],
        "forbidden_performance_sources": list(FORBIDDEN_PERFORMANCE_SOURCES),
        "outcome_blind": True,
        "performance_data_loaded": False,
    }


def _as_mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise SampleFeasibilityContractError("candidate or observation must be a mapping")
    return {str(key): jsonable(item) for key, item in value.items()}


def _bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in {"TRUE", "PASS", "ELIGIBLE", "AVAILABLE", "CLEAR", "OPEN", "KNOWN"}:
            return True
        if normalized in {"FALSE", "FAIL", "BLOCKED", "INELIGIBLE", "MISSING", "UNKNOWN", "SUSPENDED", "LOCKED"}:
            return False if normalized != "UNKNOWN" else None
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    return None


def _canonical_date(value: Any) -> str:
    if value is None:
        return "UNKNOWN_DATE"
    text = str(value)
    digits = text.replace("-", "")
    return digits if digits.isdigit() else text


def _signal_key(observation: Mapping[str, Any]) -> str:
    signal_id = observation.get("signal_id") or observation.get("opportunity_id")
    if signal_id is not None:
        return f"ID:{signal_id}"
    identity = {
        "signal_date": observation.get("signal_date", observation.get("date")),
        "symbol": observation.get("symbol"),
        "event_id": observation.get("event_id"),
        "factor_id": observation.get("factor_id"),
        "candidate_id": observation.get("candidate_id"),
    }
    if any(value is not None for value in identity.values()):
        return f"STRUCT:{stable_hash(identity)}"
    return f"ROW:{stable_hash(observation)}"


def _state(observation: Mapping[str, Any], *names: str, default: bool | None = None) -> bool | None:
    for name in names:
        if name in observation:
            return _bool_value(observation[name])
    return default


def _holding_complete(observation: Mapping[str, Any], candidate: "CandidateSampleFeasibilityInputV1") -> bool | None:
    explicit = _state(observation, "holding_complete", "holding_sessions_complete")
    if explicit is not None:
        return explicit
    available = observation.get("remaining_sessions", observation.get("holding_sessions_available"))
    if available is not None:
        value = _bool_value(available)
        if value is not None and not isinstance(available, (int, float)):
            return value
        try:
            return int(available) >= int(candidate.holding_horizon or 0) + 1
        except (TypeError, ValueError):
            return None
    session_index = observation.get("signal_session_index")
    if session_index is not None and candidate.calendar_sessions and candidate.holding_horizon is not None:
        try:
            index = int(session_index)
            return index + int(candidate.holding_horizon) + 1 < len(candidate.calendar_sessions)
        except (TypeError, ValueError):
            return None
    return None


def _affordable(observation: Mapping[str, Any], candidate: "CandidateSampleFeasibilityInputV1", *, small_capital: bool) -> bool | None:
    field = "small_capital_affordable" if small_capital else "base_affordable"
    explicit = _state(observation, field)
    if explicit is not None:
        return explicit
    price = observation.get("entry_price")
    cash = candidate.small_capital_cash if small_capital else candidate.base_cash
    lot_size = candidate.small_capital_lot_size if small_capital else candidate.base_lot_size
    if price is not None and cash is not None and lot_size is not None:
        try:
            return float(price) * int(lot_size) <= float(cash)
        except (TypeError, ValueError):
            return None
    required = _state(observation, "small_capital_affordability_required" if small_capital else "affordability_required", default=False)
    return None if required else True


@dataclass(frozen=True)
class CandidateSampleFeasibilityInputV1:
    candidate_id: str
    candidate_hash: str
    family: str
    mechanism: str
    candidate_type: str = "UNKNOWN"
    research_start: int | str | None = None
    research_end: int | str | None = None
    holding_horizon: int | None = None
    observations: tuple[Mapping[str, Any], ...] = ()
    observation_store: ObservationPartitionStoreV1 | None = None
    calendar_sessions: tuple[Any, ...] = ()
    top_n: int | None = None
    max_positions: int | None = None
    base_cash: float | None = None
    base_lot_size: int | None = 100
    small_capital_cash: float | None = 10_000.0
    small_capital_lot_size: int | None = 100
    data_provenance: Mapping[str, Any] = field(default_factory=dict)
    pit_provenance: Mapping[str, Any] = field(default_factory=dict)
    calendar_identity: str = "UNKNOWN"
    backend_type: str = "UNKNOWN"
    generated_at: str = ""

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.candidate_hash:
            raise SampleFeasibilityContractError("candidate_id and candidate_hash are required")
        if self.holding_horizon is not None and int(self.holding_horizon) < 1:
            raise SampleFeasibilityContractError("holding_horizon must be positive when present")
        if self.top_n is not None and int(self.top_n) < 1:
            raise SampleFeasibilityContractError("top_n must be positive when present")
        if self.max_positions is not None and int(self.max_positions) < 1:
            raise SampleFeasibilityContractError("max_positions must be positive when present")
        observations = tuple(_as_mapping(item) for item in self.observations)
        if self.observation_store is not None and not isinstance(self.observation_store, ObservationPartitionStoreV1):
            raise SampleFeasibilityContractError("observation_store must be ObservationPartitionStoreV1")
        if self.observation_store is not None and observations:
            raise SampleFeasibilityContractError("streaming input cannot also retain materialized observations")
        data_provenance = _as_mapping(self.data_provenance)
        pit_provenance = _as_mapping(self.pit_provenance)
        payload = {
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "family": self.family,
            "mechanism": self.mechanism,
            "candidate_type": self.candidate_type,
            "research_start": self.research_start,
            "research_end": self.research_end,
            "holding_horizon": self.holding_horizon,
            "observations": observations,
            "observation_store": self.observation_store.to_dict() if self.observation_store is not None else None,
            "calendar_sessions": self.calendar_sessions,
            "top_n": self.top_n,
            "max_positions": self.max_positions,
            "base_cash": self.base_cash,
            "base_lot_size": self.base_lot_size,
            "small_capital_cash": self.small_capital_cash,
            "small_capital_lot_size": self.small_capital_lot_size,
            "data_provenance": data_provenance,
            "pit_provenance": pit_provenance,
            "calendar_identity": self.calendar_identity,
            "backend_type": self.backend_type,
            "generated_at": self.generated_at,
        }
        PerformanceBlindGuard.assert_blind(payload)
        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "data_provenance", data_provenance)
        object.__setattr__(self, "pit_provenance", pit_provenance)

    @classmethod
    def from_candidate(
        cls,
        candidate: Mapping[str, Any] | Any,
        policy: ValidationDecisionPolicyV2,
        *,
        observations: Iterable[Mapping[str, Any]] | None = None,
        calendar_sessions: Iterable[Any] = (),
        data_provenance: Mapping[str, Any] | None = None,
        pit_provenance: Mapping[str, Any] | None = None,
        backend_type: str = "UNKNOWN",
        generated_at: str = "",
    ) -> "CandidateSampleFeasibilityInputV1":
        item = _as_mapping(candidate)
        PerformanceBlindGuard.assert_blind(item)
        sample = item.get("sample_feasibility") or item.get("sample_feasibility_input") or {}
        if not isinstance(sample, Mapping):
            raise SampleFeasibilityContractError("sample_feasibility must be a mapping")
        selection = item.get("selection_rule") if isinstance(item.get("selection_rule"), Mapping) else {}
        candidate_observations = observations if observations is not None else sample.get("observations", ())
        return cls(
            candidate_id=str(item.get("candidate_id", "")),
            candidate_hash=str(item.get("candidate_hash") or item.get("preregistration_hash") or ""),
            family=str(item.get("family") or item.get("family_id") or item.get("strategy_family") or "UNKNOWN"),
            mechanism=str(item.get("mechanism") or item.get("strategy_family") or "UNKNOWN"),
            candidate_type=str(item.get("candidate_type") or "UNKNOWN"),
            research_start=sample.get("research_start", policy.research_start),
            research_end=sample.get("research_end", policy.research_end),
            holding_horizon=sample.get("holding_horizon", item.get("holding_period", item.get("holding_period_days"))),
            observations=tuple(candidate_observations),
            calendar_sessions=tuple(sample.get("calendar_sessions", calendar_sessions)),
            top_n=sample.get("top_n", selection.get("top_n")),
            max_positions=sample.get("max_positions", item.get("max_positions", policy.max_positions)),
            base_cash=sample.get("base_cash", policy.initial_cash),
            base_lot_size=sample.get("base_lot_size", item.get("lot_size", policy.lot_size)),
            small_capital_cash=sample.get("small_capital_cash", policy.small_capital_cash),
            small_capital_lot_size=sample.get("small_capital_lot_size", policy.small_capital_lot_size),
            data_provenance=sample.get("data_provenance", data_provenance or {}),
            pit_provenance=sample.get("pit_provenance", pit_provenance or {}),
            calendar_identity=str(sample.get("calendar_identity", "UNKNOWN")),
            backend_type=str(sample.get("backend_type", backend_type)),
            generated_at=str(sample.get("generated_at", generated_at)),
        )

    @property
    def input_hash(self) -> str:
        return stable_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "candidate-sample-feasibility-input-v1",
            "preflight_version": PREFLIGHT_VERSION,
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "family": self.family,
            "mechanism": self.mechanism,
            "candidate_type": self.candidate_type,
            "research_start": self.research_start,
            "research_end": self.research_end,
            "holding_horizon": self.holding_horizon,
            "observations": jsonable(self.observations),
            "observation_store": self.observation_store.to_dict() if self.observation_store is not None else None,
            "calendar_sessions": jsonable(self.calendar_sessions),
            "top_n": self.top_n,
            "max_positions": self.max_positions,
            "base_cash": self.base_cash,
            "base_lot_size": self.base_lot_size,
            "small_capital_cash": self.small_capital_cash,
            "small_capital_lot_size": self.small_capital_lot_size,
            "data_provenance": jsonable(self.data_provenance),
            "pit_provenance": jsonable(self.pit_provenance),
            "calendar_identity": self.calendar_identity,
            "backend_type": self.backend_type,
            "generated_at": self.generated_at,
            "outcome_blind": True,
            "performance_data_loaded": False,
        }


@dataclass(frozen=True)
class _PathCounts:
    pit_eligible_count: int
    data_complete_count: int
    execution_eligible_count: int
    selected_opportunity_count: int
    portfolio_feasible_opportunity_count: int
    lower_bound_count: int
    upper_bound_count: int
    reasons: tuple[str, ...] = ()
    unknown: bool = False


@dataclass(frozen=True)
class CandidateSampleFeasibilityResultV1:
    candidate_id: str
    candidate_hash: str
    family: str
    mechanism: str
    policy_id: str
    policy_version: str
    policy_hash: str
    minimum_required_count: int
    raw_event_or_signal_count: int
    qualified_signal_count: int
    pit_eligible_count: int
    data_complete_count: int
    execution_eligible_count: int
    selected_opportunity_count: int
    portfolio_feasible_opportunity_count: int
    lower_bound_count: int
    upper_bound_count: int
    status: str
    reason_codes: tuple[str, ...]
    base_lower_bound_count: int
    base_upper_bound_count: int
    base_status: str
    small_capital_lower_bound_count: int
    small_capital_upper_bound_count: int
    small_capital_status: str
    data_pit_provenance: Mapping[str, Any]
    input_hash: str
    calculation_hash: str
    generated_at: str = ""
    result_hash: str = ""

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES or self.base_status not in VALID_STATUSES or self.small_capital_status not in VALID_STATUSES:
            raise SampleFeasibilityContractError("invalid sample feasibility status")
        object.__setattr__(self, "reason_codes", tuple(sorted(set(self.reason_codes))))
        object.__setattr__(self, "data_pit_provenance", _as_mapping(self.data_pit_provenance))
        if not self.result_hash:
            object.__setattr__(self, "result_hash", stable_hash(self._hash_payload()))

    def _hash_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("result_hash", None)
        payload.pop("generated_at", None)
        return payload

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "candidate-sample-feasibility-result-v1",
            "preflight_version": PREFLIGHT_VERSION,
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "family": self.family,
            "mechanism": self.mechanism,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "minimum_required_count": self.minimum_required_count,
            "raw_event_or_signal_count": self.raw_event_or_signal_count,
            "qualified_signal_count": self.qualified_signal_count,
            "pit_eligible_count": self.pit_eligible_count,
            "data_complete_count": self.data_complete_count,
            "execution_eligible_count": self.execution_eligible_count,
            "selected_opportunity_count": self.selected_opportunity_count,
            "portfolio_feasible_opportunity_count": self.portfolio_feasible_opportunity_count,
            "lower_bound_count": self.lower_bound_count,
            "upper_bound_count": self.upper_bound_count,
            "base_feasibility": {"lower_bound_count": self.base_lower_bound_count, "upper_bound_count": self.base_upper_bound_count, "status": self.base_status},
            "small_capital_feasibility": {"lower_bound_count": self.small_capital_lower_bound_count, "upper_bound_count": self.small_capital_upper_bound_count, "status": self.small_capital_status},
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "data_pit_provenance": jsonable(self.data_pit_provenance),
            "input_hash": self.input_hash,
            "calculation_hash": self.calculation_hash,
            "generated_at": self.generated_at,
            "result_hash": self.result_hash,
            "outcome_blind": True,
            "performance_data_loaded": False,
            "performance_files_read": [],
        }

    def sanitized_failure_feedback(self) -> dict[str, Any] | None:
        if self.status != BLOCKED_INSUFFICIENT_FEASIBILITY:
            return None
        if "INSUFFICIENT_EVENT_OCCURRENCES" in self.reason_codes:
            high_level = "EVENT_OCCURRENCE_TOO_SPARSE"
        elif "END_OF_WINDOW_TRUNCATION" in self.reason_codes:
            high_level = "RESEARCH_WINDOW_TOO_SHORT_FOR_HOLDING_CONTRACT"
        else:
            high_level = "EXECUTABLE_OPPORTUNITY_SCARCITY"
        return {
            "category": "SAMPLE_FEASIBILITY_FAILURE",
            "mechanism": self.mechanism,
            "high_level_reason": high_level,
            "reason_code": next((code for code in self.reason_codes if code in REASON_CODES), "SAMPLE_FEASIBILITY_UNKNOWN"),
            "constraints": ["do_not_retune_same_batch", "preserve_frozen_candidate"],
            "source_trial_ids": [],
        }


class CandidateSampleFeasibilityPreflightV1:
    """Calculate structural feasibility before predictive budget reservation."""

    version = PREFLIGHT_VERSION

    def __init__(self, policy: ValidationDecisionPolicyV2):
        self.policy = policy
        self.minimum_required_count = minimum_required_sample_count(policy)

    @staticmethod
    def _unique_observations(observations: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        unique: dict[str, dict[str, Any]] = {}
        for raw in observations:
            item = _as_mapping(raw)
            unique.setdefault(_signal_key(item), item)
        return list(unique.values())

    @staticmethod
    def _group_by_date(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
        groups: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            groups.setdefault(_canonical_date(row.get("signal_date", row.get("date"))), []).append(row)
        return groups

    @staticmethod
    def _occupancy_window(
        row: Mapping[str, Any],
        candidate: CandidateSampleFeasibilityInputV1,
        *,
        fallback_signal_index: int,
    ) -> tuple[int | None, int | None]:
        signal_index = row.get("signal_session_index")
        try:
            signal_index = int(signal_index) if signal_index is not None else fallback_signal_index
        except (TypeError, ValueError):
            signal_index = fallback_signal_index
        entry_index = row.get("entry_session_index")
        try:
            entry_index = int(entry_index) if entry_index is not None else signal_index + 1
        except (TypeError, ValueError):
            entry_index = signal_index + 1
        release_index = row.get("holding_release_session_index")
        if release_index is None and candidate.holding_horizon is not None:
            release_index = entry_index + int(candidate.holding_horizon)
        try:
            release_index = int(release_index) if release_index is not None else None
        except (TypeError, ValueError):
            release_index = None
        return entry_index, release_index

    def _capacity_schedule(
        self,
        rows: Iterable[Mapping[str, Any]],
        candidate: CandidateSampleFeasibilityInputV1,
        *,
        conservative: bool = False,
    ) -> tuple[int, list[dict[str, Any]], set[str]]:
        """Accept rows in date/rank order under concurrent fixed-hold capacity.

        ``release_index`` is the first session on which the contractual holding
        window no longer occupies a slot.  A conservative schedule does not
        reuse a slot whose actual release is unknown during the research window;
        the optimistic schedule reuses it at the frozen contractual release.
        """

        groups = self._group_by_date(rows)
        if not groups:
            return 0, [], set()
        calendar_index = {
            _canonical_date(value): index
            for index, value in enumerate(candidate.calendar_sessions)
        }
        ordered_dates = sorted(
            groups,
            key=lambda date: (calendar_index.get(date, len(calendar_index)), date),
        )
        date_order = {date: index for index, date in enumerate(ordered_dates)}
        if candidate.max_positions is None:
            return sum(len(groups[date]) for date in ordered_dates), [
                {
                    "date": date,
                    "qualified_candidates": len(groups[date]),
                    "positions_entering": len(groups[date]),
                    "structural_occupied_slots_before_entry": 0,
                    "available_slots": None,
                    "accepted_opportunities": len(groups[date]),
                    "holding_release_dates_or_bounds": [],
                }
                for date in ordered_dates
            ], set()

        reasons: set[str] = set()
        active: list[int] = []
        accepted_count = 0
        diagnostics: list[dict[str, Any]] = []
        calendar_values = tuple(candidate.calendar_sessions)
        conservative_release = len(calendar_values) + 1 if calendar_values else None
        for date in ordered_dates:
            group = groups[date]
            fallback_signal_index = calendar_index.get(date, date_order[date])
            entry_index, _ = self._occupancy_window(
                group[0], candidate, fallback_signal_index=fallback_signal_index
            )
            if entry_index is None:
                reasons.add("PORTFOLIO_CAPACITY_BOUND")
                entry_index = date_order[date] + 1
            active = [release for release in active if release > entry_index]
            occupied_before = len(active)
            available = max(0, int(candidate.max_positions) - occupied_before)
            accepted = 0
            release_dates: list[Any] = []
            for row in group:
                row_entry, contractual_release = self._occupancy_window(
                    row, candidate, fallback_signal_index=fallback_signal_index
                )
                if row_entry is None or contractual_release is None:
                    reasons.add("STRUCTURE_EXIT_COUNT_UNKNOWN")
                    contractual_release = (row_entry or entry_index) + int(candidate.holding_horizon or 0)
                release_known = _bool_value(row.get("holding_release_known"))
                if release_known is False:
                    reasons.add("STRUCTURE_EXIT_COUNT_UNKNOWN")
                if len(active) >= int(candidate.max_positions):
                    continue
                release_index = conservative_release if conservative and conservative_release is not None else contractual_release
                active.append(int(release_index))
                accepted += 1
                accepted_count += 1
                if calendar_values and 0 <= int(contractual_release) < len(calendar_values):
                    release_dates.append(calendar_values[int(contractual_release)])
                else:
                    release_dates.append(int(contractual_release))
            diagnostics.append({
                "date": date,
                "qualified_candidates": len(group),
                "positions_entering": accepted,
                "structural_occupied_slots_before_entry": occupied_before,
                "available_slots": available,
                "accepted_opportunities": accepted,
                "holding_release_dates_or_bounds": release_dates,
            })
        return accepted_count, diagnostics, reasons

    def capacity_schedule(
        self,
        rows: Iterable[Mapping[str, Any]],
        candidate: CandidateSampleFeasibilityInputV1,
        *,
        conservative: bool = False,
    ) -> dict[str, Any]:
        """Expose the outcome-blind occupancy schedule for audit diagnostics."""

        accepted_count, diagnostics, reasons = self._capacity_schedule(
            rows, candidate, conservative=conservative
        )
        return {
            "accepted_count": accepted_count,
            "diagnostics": diagnostics,
            "reason_codes": sorted(reasons),
            "conservative": conservative,
            "outcome_blind": True,
        }

    def _select(self, rows: list[Mapping[str, Any]], candidate: CandidateSampleFeasibilityInputV1) -> tuple[list[Mapping[str, Any]], bool, set[str]]:
        reasons: set[str] = set()
        if candidate.top_n is None:
            return list(rows), False, reasons
        selected: list[Mapping[str, Any]] = []
        for date, group in self._group_by_date(rows).items():
            if date == "UNKNOWN_DATE" and len(group) > int(candidate.top_n):
                reasons.add("INSUFFICIENT_ELIGIBLE_DATES")
                return [], True, reasons
            if len(group) <= int(candidate.top_n):
                selected.extend(group)
                continue
            if any(row.get("rank") is None for row in group):
                reasons.add("SAMPLE_FEASIBILITY_UNKNOWN")
                return [], True, reasons
            selected.extend(sorted(group, key=lambda row: (float(row.get("rank")), str(row.get("symbol", ""))))[: int(candidate.top_n)])
        return selected, False, reasons

    def _path_counts(self, candidate: CandidateSampleFeasibilityInputV1, *, small_capital: bool) -> tuple[_PathCounts, int]:
        unique = self._unique_observations(candidate.observations)
        reasons: set[str] = set()
        unknown = False
        qualified: list[dict[str, Any]] = []
        potential: list[dict[str, Any]] = []
        pit_count = data_count = execution_count = 0
        if not unique:
            return _PathCounts(0, 0, 0, 0, 0, 0, 0, ("DATA_COVERAGE_INSUFFICIENT", "SAMPLE_FEASIBILITY_UNKNOWN"), True), 0
        for row in unique:
            signal_ok = _state(row, "signal_qualified", "qualified", default=True)
            if signal_ok is False:
                continue
            if signal_ok is None:
                unknown = True
                reasons.add("SAMPLE_FEASIBILITY_UNKNOWN")
                potential.append(row)
                continue
            pit_ok = _state(row, "pit_eligible", "universe_eligible")
            data_ok = _state(row, "data_complete", "factor_data_complete")
            execution_ok = _state(row, "execution_eligible", "tradable")
            if pit_ok is True:
                pit_count += 1
            elif pit_ok is None:
                unknown = True
                reasons.add("PIT_STATE_UNKNOWN")
            if data_ok is True:
                data_count += 1
            elif data_ok is None:
                unknown = True
                reasons.add("DATA_COVERAGE_INSUFFICIENT")
            if execution_ok is True:
                next_ok = _state(row, "next_session_eligible", "next_session")
                t1_ok = _state(row, "t1_eligible", "t_plus_1_eligible")
                if next_ok is False or t1_ok is False:
                    execution_ok = False
                elif next_ok is None or t1_ok is None:
                    execution_ok = None
            if execution_ok is True:
                for optional_name in ("active_security", "suspension_clear", "limit_state_open", "st_allowed"):
                    state = _state(row, optional_name)
                    if state is False:
                        execution_ok = False
                        break
                    if state is None and optional_name in row:
                        execution_ok = None
            if execution_ok is True:
                execution_count += 1
            elif execution_ok is None:
                unknown = True
                reasons.add("INSUFFICIENT_EXECUTABLE_OPPORTUNITIES")
            hold_ok = _holding_complete(row, candidate)
            if hold_ok is False:
                reasons.add("END_OF_WINDOW_TRUNCATION")
            elif hold_ok is None:
                unknown = True
                reasons.add("STRUCTURE_EXIT_COUNT_UNKNOWN")
            affordable = _affordable(row, candidate, small_capital=small_capital)
            if affordable is None:
                unknown = True
                reasons.add("INSUFFICIENT_EXECUTABLE_OPPORTUNITIES")
            if pit_ok is True and data_ok is True and execution_ok is True and hold_ok is True and affordable is True:
                portfolio_ok = _state(row, "portfolio_feasible", "portfolio_capacity_known", default=True)
                if portfolio_ok is False:
                    continue
                if portfolio_ok is None:
                    unknown = True
                    reasons.add("PORTFOLIO_CAPACITY_BOUND")
                    potential.append(row)
                    continue
                qualified.append(row)
            if pit_ok is not False and data_ok is not False and execution_ok is not False and hold_ok is not False and affordable is not False:
                potential.append(row)
        selected, selection_unknown, selection_reasons = self._select(qualified, candidate)
        unknown = unknown or selection_unknown
        reasons.update(selection_reasons)
        selected_count = len(selected)
        capacity_unknown = any(
            row.get("portfolio_overlap_unknown") is True
            or row.get("prior_holdings_known") is False
            or row.get("holding_release_known") is False
            for row in selected
        )
        if capacity_unknown:
            unknown = True
            reasons.add("PORTFOLIO_CAPACITY_BOUND")
        portfolio_count, _, capacity_reasons = self._capacity_schedule(candidate= candidate, rows=selected)
        reasons.update(capacity_reasons)
        if unknown:
            upper, _, upper_reasons = self._capacity_schedule(candidate= candidate, rows=potential)
            reasons.update(upper_reasons)
            if selection_unknown:
                # A date/rank selection that cannot be reconstructed does not
                # provide a provable lower-bound subset.
                lower = 0
            elif capacity_unknown:
                lower, _, lower_reasons = self._capacity_schedule(
                    candidate=candidate, rows=selected, conservative=True
                )
                reasons.update(lower_reasons)
            else:
                # Unknown rows expand the optimistic upper bound, but they do
                # not invalidate already selected rows whose PIT/data/
                # execution/holding/affordability fields are all known.  The
                # result remains UNKNOWN, so this does not open predictive
                # access; it preserves the structural lower-bound proof.
                lower = portfolio_count
        else:
            lower = upper = portfolio_count
        if upper < self.minimum_required_count and not unknown:
            reasons.add("INSUFFICIENT_EVENT_OCCURRENCES" if len(unique) < self.minimum_required_count else "INSUFFICIENT_EXECUTABLE_OPPORTUNITIES")
        return _PathCounts(pit_count, data_count, execution_count, selected_count, portfolio_count, lower, upper, tuple(sorted(reasons)), unknown), len(qualified)

    @staticmethod
    def _path_counts_from_dict(payload: Mapping[str, Any]) -> _PathCounts:
        return _PathCounts(
            int(payload["pit_eligible_count"]),
            int(payload["data_complete_count"]),
            int(payload["execution_eligible_count"]),
            int(payload["selected_opportunity_count"]),
            int(payload["portfolio_feasible_opportunity_count"]),
            int(payload["lower_bound_count"]),
            int(payload["upper_bound_count"]),
            tuple(str(item) for item in payload.get("reasons", ())),
            bool(payload.get("unknown")),
        )

    @staticmethod
    def _path_counts_to_dict(counts: _PathCounts) -> dict[str, Any]:
        return {
            "pit_eligible_count": counts.pit_eligible_count,
            "data_complete_count": counts.data_complete_count,
            "execution_eligible_count": counts.execution_eligible_count,
            "selected_opportunity_count": counts.selected_opportunity_count,
            "portfolio_feasible_opportunity_count": counts.portfolio_feasible_opportunity_count,
            "lower_bound_count": counts.lower_bound_count,
            "upper_bound_count": counts.upper_bound_count,
            "reasons": list(counts.reasons),
            "unknown": counts.unknown,
        }

    def _stream_capacity_accept(
        self,
        active: list[int],
        rows: Iterable[Mapping[str, Any]],
        candidate: CandidateSampleFeasibilityInputV1,
        *,
        conservative: bool,
        date_order: int,
    ) -> tuple[int, set[str]]:
        rows = list(rows)
        if candidate.max_positions is None:
            return len(rows), set()
        if not rows:
            return 0, set()
        calendar_index = {
            _canonical_date(value): index
            for index, value in enumerate(candidate.calendar_sessions)
        }
        date = _canonical_date(rows[0].get("signal_date", rows[0].get("date")))
        fallback_signal_index = calendar_index.get(date, date_order)
        entry_index, _ = self._occupancy_window(
            rows[0], candidate, fallback_signal_index=fallback_signal_index
        )
        reasons: set[str] = set()
        if entry_index is None:
            reasons.add("PORTFOLIO_CAPACITY_BOUND")
            entry_index = date_order + 1
        active[:] = [release for release in active if release > entry_index]
        accepted = 0
        conservative_release = len(candidate.calendar_sessions) + 1 if candidate.calendar_sessions else None
        for row in rows:
            row_entry, contractual_release = self._occupancy_window(
                row, candidate, fallback_signal_index=fallback_signal_index
            )
            if row_entry is None or contractual_release is None:
                reasons.add("STRUCTURE_EXIT_COUNT_UNKNOWN")
                contractual_release = (row_entry or entry_index) + int(candidate.holding_horizon or 0)
            if _bool_value(row.get("holding_release_known")) is False:
                reasons.add("STRUCTURE_EXIT_COUNT_UNKNOWN")
            if len(active) >= int(candidate.max_positions):
                continue
            release_index = conservative_release if conservative and conservative_release is not None else contractual_release
            active.append(int(release_index))
            accepted += 1
        return accepted, reasons

    def _path_counts_streaming(
        self,
        candidate: CandidateSampleFeasibilityInputV1,
        *,
        small_capital: bool,
        phase: str,
    ) -> tuple[_PathCounts, int]:
        store = candidate.observation_store
        if store is None:
            raise SampleFeasibilityContractError("streaming path requires an observation store")
        checkpoint = store.load_checkpoint() or {}
        saved_result = checkpoint.get("base_result") if phase == "base" else checkpoint.get("small_result")
        if saved_result is not None:
            return self._path_counts_from_dict(saved_result), int(checkpoint.get("qualified_count", 0))
        state = {
            "pit_count": 0,
            "data_count": 0,
            "execution_count": 0,
            "selected_count": 0,
            "portfolio_count": 0,
            "qualified_count": 0,
            "unknown": False,
            "reasons": [],
            "capacity_unknown": False,
            "selection_unknown": False,
            "normal_active": [],
            "upper_active": [],
            "conservative_active": [],
            "conservative_count": 0,
            "completed_partition": -1,
        }
        if checkpoint.get("phase") == phase:
            state.update(checkpoint.get("state", {}))
        reasons = set(str(item) for item in state.get("reasons", ()))
        active = [int(item) for item in state.get("normal_active", ())]
        upper_active = [int(item) for item in state.get("upper_active", ())]
        conservative_active = [int(item) for item in state.get("conservative_active", ())]
        current_date: str | None = None
        qualified_group: list[Mapping[str, Any]] = []
        potential_group: list[Mapping[str, Any]] = []
        date_order = 0

        def flush_group() -> None:
            nonlocal qualified_group, potential_group, current_date, date_order
            if current_date is None:
                return
            selected, selection_unknown, selection_reasons = self._select(
                qualified_group, candidate
            )
            state["selection_unknown"] = bool(state["selection_unknown"] or selection_unknown)
            reasons.update(selection_reasons)
            state["selected_count"] += len(selected)
            if any(
                row.get("portfolio_overlap_unknown") is True
                or row.get("prior_holdings_known") is False
                or row.get("holding_release_known") is False
                for row in selected
            ):
                state["capacity_unknown"] = True
                reasons.add("PORTFOLIO_CAPACITY_BOUND")
            accepted, capacity_reasons = self._stream_capacity_accept(
                active, selected, candidate, conservative=False, date_order=date_order
            )
            state["portfolio_count"] += accepted
            reasons.update(capacity_reasons)
            accepted_upper, upper_reasons = self._stream_capacity_accept(
                upper_active, potential_group, candidate, conservative=False, date_order=date_order
            )
            state["upper_count"] = int(state.get("upper_count", 0)) + accepted_upper
            reasons.update(upper_reasons)
            accepted_conservative, conservative_reasons = self._stream_capacity_accept(
                conservative_active, selected, candidate, conservative=True, date_order=date_order
            )
            state["conservative_count"] += accepted_conservative
            reasons.update(conservative_reasons)
            date_order += 1
            current_date = None
            qualified_group = []
            potential_group = []

        for partition_index, (_descriptor, rows) in enumerate(store.iter_partitions()):
            if partition_index <= int(state.get("completed_partition", -1)):
                continue
            for row in rows:
                row_date = _canonical_date(row.get("signal_date", row.get("date")))
                if current_date is not None and row_date != current_date:
                    flush_group()
                if current_date is None:
                    current_date = row_date
                signal_ok = _state(row, "signal_qualified", "qualified", default=True)
                if signal_ok is False:
                    continue
                if signal_ok is None:
                    state["unknown"] = True
                    reasons.add("SAMPLE_FEASIBILITY_UNKNOWN")
                    potential_group.append(row)
                    continue
                pit_ok = _state(row, "pit_eligible", "universe_eligible")
                data_ok = _state(row, "data_complete", "factor_data_complete")
                execution_ok = _state(row, "execution_eligible", "tradable")
                if pit_ok is True:
                    state["pit_count"] += 1
                elif pit_ok is None:
                    state["unknown"] = True
                    reasons.add("PIT_STATE_UNKNOWN")
                if data_ok is True:
                    state["data_count"] += 1
                elif data_ok is None:
                    state["unknown"] = True
                    reasons.add("DATA_COVERAGE_INSUFFICIENT")
                if execution_ok is True:
                    next_ok = _state(row, "next_session_eligible", "next_session")
                    t1_ok = _state(row, "t1_eligible", "t_plus_1_eligible")
                    if next_ok is False or t1_ok is False:
                        execution_ok = False
                    elif next_ok is None or t1_ok is None:
                        execution_ok = None
                if execution_ok is True:
                    for optional_name in ("active_security", "suspension_clear", "limit_state_open", "st_allowed"):
                        value = _state(row, optional_name)
                        if value is False:
                            execution_ok = False
                            break
                        if value is None and optional_name in row:
                            execution_ok = None
                if execution_ok is True:
                    state["execution_count"] += 1
                elif execution_ok is None:
                    state["unknown"] = True
                    reasons.add("INSUFFICIENT_EXECUTABLE_OPPORTUNITIES")
                hold_ok = _holding_complete(row, candidate)
                if hold_ok is False:
                    reasons.add("END_OF_WINDOW_TRUNCATION")
                elif hold_ok is None:
                    state["unknown"] = True
                    reasons.add("STRUCTURE_EXIT_COUNT_UNKNOWN")
                affordable = _affordable(row, candidate, small_capital=small_capital)
                if affordable is None:
                    state["unknown"] = True
                    reasons.add("INSUFFICIENT_EXECUTABLE_OPPORTUNITIES")
                if pit_ok is True and data_ok is True and execution_ok is True and hold_ok is True and affordable is True:
                    portfolio_ok = _state(row, "portfolio_feasible", "portfolio_capacity_known", default=True)
                    if portfolio_ok is False:
                        continue
                    if portfolio_ok is None:
                        state["unknown"] = True
                        reasons.add("PORTFOLIO_CAPACITY_BOUND")
                        potential_group.append(row)
                        continue
                    qualified_group.append(row)
                    state["qualified_count"] += 1
                if pit_ok is not False and data_ok is not False and execution_ok is not False and hold_ok is not False and affordable is not False:
                    potential_group.append(row)
            flush_group()
            state["completed_partition"] = partition_index
            state["normal_active"] = active
            state["upper_active"] = upper_active
            state["conservative_active"] = conservative_active
            state["reasons"] = sorted(reasons)
            checkpoint_payload = {
                "schema_version": "candidate-sample-feasibility-stream-checkpoint-v1",
                "phase": phase,
                "completed_partition": partition_index,
                "state": state,
                "qualified_count": state["qualified_count"],
                "outcome_blind": True,
                "performance_data_loaded": False,
            }
            if phase == "base":
                checkpoint_payload["base_result"] = None
            store.checkpoint(checkpoint_payload)
        flush_group()
        unknown = bool(state["unknown"] or state["selection_unknown"])
        if unknown:
            upper = int(state.get("upper_count", 0))
            if state["selection_unknown"]:
                lower = 0
            elif state["capacity_unknown"]:
                lower = int(state["conservative_count"])
            else:
                # Keep the proven, fully known selected subset as the lower
                # bound while retaining UNKNOWN status for unresolved rows.
                lower = int(state["portfolio_count"])
        else:
            lower = upper = int(state["portfolio_count"])
        if upper < self.minimum_required_count and not unknown:
            reasons.add(
                "INSUFFICIENT_EVENT_OCCURRENCES"
                if int(store.observation_count) < self.minimum_required_count
                else "INSUFFICIENT_EXECUTABLE_OPPORTUNITIES"
            )
        counts = _PathCounts(
            int(state["pit_count"]),
            int(state["data_count"]),
            int(state["execution_count"]),
            int(state["selected_count"]),
            int(state["portfolio_count"]),
            lower,
            upper,
            tuple(sorted(reasons)),
            unknown,
        )
        completed = store.load_checkpoint() or {}
        completed.update({"phase": "base_complete" if phase == "base" else "complete", "state": state, "qualified_count": state["qualified_count"]})
        if phase == "base":
            completed["base_result"] = self._path_counts_to_dict(counts)
        else:
            completed["small_result"] = self._path_counts_to_dict(counts)
        store.checkpoint(completed)
        return counts, int(state["qualified_count"])

    def _run_streaming(self, candidate: CandidateSampleFeasibilityInputV1) -> CandidateSampleFeasibilityResultV1:
        base, qualified_count = self._path_counts_streaming(candidate, small_capital=False, phase="base")
        small, _ = self._path_counts_streaming(candidate, small_capital=True, phase="small")
        status = self._status(base)
        reasons = set(base.reasons)
        if status == UNKNOWN:
            reasons.add("SAMPLE_FEASIBILITY_UNKNOWN")
        elif status == BLOCKED_INSUFFICIENT_FEASIBILITY and not reasons:
            reasons.add("INSUFFICIENT_EXECUTABLE_OPPORTUNITIES")
        result_payload = {
            "preflight_version": self.version,
            "candidate": candidate.to_dict(),
            "minimum_required_count": self.minimum_required_count,
            "base": base,
            "small_capital": small,
            "status": status,
            "reason_codes": sorted(reasons),
        }
        calculation_hash = stable_hash(result_payload)
        result = CandidateSampleFeasibilityResultV1(
            candidate_id=candidate.candidate_id,
            candidate_hash=candidate.candidate_hash,
            family=candidate.family,
            mechanism=candidate.mechanism,
            policy_id=self.policy.policy_id,
            policy_version=self.policy.policy_version,
            policy_hash=self.policy.policy_hash,
            minimum_required_count=self.minimum_required_count,
            raw_event_or_signal_count=candidate.observation_store.observation_count,
            qualified_signal_count=qualified_count,
            pit_eligible_count=base.pit_eligible_count,
            data_complete_count=base.data_complete_count,
            execution_eligible_count=base.execution_eligible_count,
            selected_opportunity_count=base.selected_opportunity_count,
            portfolio_feasible_opportunity_count=base.portfolio_feasible_opportunity_count,
            lower_bound_count=base.lower_bound_count,
            upper_bound_count=base.upper_bound_count,
            status=status,
            reason_codes=tuple(sorted(reasons)),
            base_lower_bound_count=base.lower_bound_count,
            base_upper_bound_count=base.upper_bound_count,
            base_status=self._status(base),
            small_capital_lower_bound_count=small.lower_bound_count,
            small_capital_upper_bound_count=small.upper_bound_count,
            small_capital_status=self._status(small),
            data_pit_provenance={"data": candidate.data_provenance, "pit": candidate.pit_provenance, "calendar_identity": candidate.calendar_identity},
            input_hash=candidate.input_hash,
            calculation_hash=calculation_hash,
            generated_at=candidate.generated_at,
        )
        candidate.observation_store.clear_checkpoint()
        candidate.observation_store.cleanup()
        return result

    def _status(self, counts: _PathCounts) -> str:
        if counts.unknown:
            return UNKNOWN
        if counts.upper_bound_count < self.minimum_required_count:
            return BLOCKED_INSUFFICIENT_FEASIBILITY
        if counts.lower_bound_count >= self.minimum_required_count:
            return PASS
        return UNKNOWN

    def run(self, candidate: CandidateSampleFeasibilityInputV1) -> CandidateSampleFeasibilityResultV1:
        if not isinstance(candidate, CandidateSampleFeasibilityInputV1):
            raise SampleFeasibilityContractError("preflight input must be CandidateSampleFeasibilityInputV1")
        self.policy.assert_active()
        PerformanceBlindGuard.assert_blind(candidate.to_dict())
        if candidate.observation_store is not None:
            return self._run_streaming(candidate)
        base, qualified_count = self._path_counts(candidate, small_capital=False)
        small, _ = self._path_counts(candidate, small_capital=True)
        status = self._status(base)
        reasons = set(base.reasons)
        if status == UNKNOWN:
            reasons.add("SAMPLE_FEASIBILITY_UNKNOWN")
        elif status == BLOCKED_INSUFFICIENT_FEASIBILITY and not reasons:
            reasons.add("INSUFFICIENT_EXECUTABLE_OPPORTUNITIES")
        result_payload = {
            "preflight_version": self.version,
            "candidate": candidate.to_dict(),
            "minimum_required_count": self.minimum_required_count,
            "base": base,
            "small_capital": small,
            "status": status,
            "reason_codes": sorted(reasons),
        }
        calculation_hash = stable_hash(result_payload)
        return CandidateSampleFeasibilityResultV1(
            candidate_id=candidate.candidate_id,
            candidate_hash=candidate.candidate_hash,
            family=candidate.family,
            mechanism=candidate.mechanism,
            policy_id=self.policy.policy_id,
            policy_version=self.policy.policy_version,
            policy_hash=self.policy.policy_hash,
            minimum_required_count=self.minimum_required_count,
            raw_event_or_signal_count=len(candidate.observations),
            qualified_signal_count=qualified_count,
            pit_eligible_count=base.pit_eligible_count,
            data_complete_count=base.data_complete_count,
            execution_eligible_count=base.execution_eligible_count,
            selected_opportunity_count=base.selected_opportunity_count,
            portfolio_feasible_opportunity_count=base.portfolio_feasible_opportunity_count,
            lower_bound_count=base.lower_bound_count,
            upper_bound_count=base.upper_bound_count,
            status=status,
            reason_codes=tuple(sorted(reasons)),
            base_lower_bound_count=base.lower_bound_count,
            base_upper_bound_count=base.upper_bound_count,
            base_status=self._status(base),
            small_capital_lower_bound_count=small.lower_bound_count,
            small_capital_upper_bound_count=small.upper_bound_count,
            small_capital_status=self._status(small),
            data_pit_provenance={"data": candidate.data_provenance, "pit": candidate.pit_provenance, "calendar_identity": candidate.calendar_identity},
            input_hash=candidate.input_hash,
            calculation_hash=calculation_hash,
            generated_at=candidate.generated_at,
        )

    def persist(self, result: CandidateSampleFeasibilityResultV1, path: str | Path) -> Path:
        target = Path(path)
        payload = result.to_dict()
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if existing.get("result_hash") != result.result_hash:
                raise SampleFeasibilityContractError("append-only preflight artifact identity conflict")
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target


__all__ = [
    "BLOCKED_INSUFFICIENT_FEASIBILITY",
    "CandidateSampleFeasibilityInputV1",
    "CandidateSampleFeasibilityPreflightV1",
    "CandidateSampleFeasibilityResultV1",
    "PASS",
    "PREFLIGHT_VERSION",
    "FORBIDDEN_PERFORMANCE_SOURCES",
    "REASON_CODES",
    "SampleFeasibilityContractError",
    "UNKNOWN",
    "minimum_required_sample_count",
    "performance_file_access_audit",
]
