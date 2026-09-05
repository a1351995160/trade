"""Outcome-blind real sample-feasibility provider.

The provider materializes only structural observations for a frozen candidate.
It deliberately stops before any predictive trial, backtest, return, PnL, or
portfolio-result path.  The default readers use the repository's canonical
daily, event, factor-registry, calendar, and PIT raw stores; tests may inject
the same structural rows through the constructor without changing semantics.
"""
from __future__ import annotations

from datetime import date, datetime, time
from dataclasses import fields
import gc
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .common import stable_hash
from .context import PerformanceBlindGuard
from .sample_feasibility import CandidateSampleFeasibilityInputV1, ObservationPartitionStoreV1
from chanlun_trader.engine.security_state import ChinaPriceLimitModel, SecurityMaster, SecurityState
from chanlun_trader.research.guard import RESEARCH_END, ResearchDataAccessGuard
from chanlun_trader.research.io_safety import GuardedResearchReader, read_day_file_range
from chanlun_trader.research.pit_tradability import (
    NORMALIZED_DATASET_VERSION,
    PITStateStore,
    ST_NORMAL,
    ST_UNKNOWN,
    TRADING,
    STATUS_UNKNOWN,
    SUSPENDED,
    map_is_st,
    map_trade_status,
    normalize_symbol,
    source_symbol,
)
from chanlun_trader.research.strategy_candidate import StrategyCandidateSpec
from chanlun_trader.research.unified_factor import (
    AsOfDataView,
    FactorCompiler,
    UnifiedFactorRegistry,
)
from chanlun_trader.research.validation_policy_v2 import ValidationDecisionPolicyV2


SHANGHAI = ZoneInfo("Asia/Shanghai")
PROVIDER_VERSION = "RealSampleFeasibilityProviderV1"
EXECUTION_CONTRACT_VERSION = "A_SHARE_NEXT_SESSION_OPEN_T1_V1"
PIT_DATASET_VERSION = NORMALIZED_DATASET_VERSION
LHB_EVENT_SEMANTICS_VERSION = "LHB_EVENT_AVAILABILITY_CONTRACT_V2"
BENCHMARK_CACHE_CONTRACT_VERSION = "BENCHMARK_CACHE_RANGE_CONTRACT_V1"
EVENT_ROW_SCHEMA_VERSION = "event-materialization-row-v1"
DERIVED_BENCHMARK_EVENT_TYPE = "DERIVED_BENCHMARK_EVENT"
DERIVED_BENCHMARK_SOURCE_ID = "benchmark_index_daily:sh000300"
SUPPORTED_FAMILIES = frozenset({"DAILY_EVENT", "DAILY_CROSS_SECTIONAL", "DAILY_FACTOR"})
CANONICAL_DAILY_WARMUP_START = 20210802


def _day(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return int(pd.Timestamp(value).strftime("%Y%m%d"))
    text = str(value).strip()
    digits = text.replace("-", "")[:8]
    return int(digits) if len(digits) == 8 and digits.isdigit() else None


def _timestamp(value: Any, *, default_day: int | None = None) -> pd.Timestamp | None:
    if value is None or value == "":
        if default_day is None:
            return None
        value = str(default_day)
    if isinstance(value, (int, np.integer)) and 19000000 <= int(value) <= 21000000:
        value = str(int(value))
    try:
        parsed = pd.Timestamp(value)
        if parsed.tzinfo is None:
            parsed = parsed.tz_localize(SHANGHAI)
        else:
            parsed = parsed.tz_convert(SHANGHAI)
        return parsed
    except (TypeError, ValueError):
        return None


def _close_ts(day: int) -> pd.Timestamp:
    return pd.Timestamp(str(day), tz=SHANGHAI) + pd.Timedelta(hours=15)


def _close_confirmed_ts(day: int) -> pd.Timestamp:
    """Return the first timestamp after the completed-bar close boundary."""

    return _close_ts(day) + pd.Timedelta(seconds=1)


def _open_text(day: int) -> str:
    return datetime.combine(
        date(int(str(day)[:4]), int(str(day)[4:6]), int(str(day)[6:])),
        time(9, 30),
        tzinfo=SHANGHAI,
    ).isoformat()


def _safe_symbol(value: Any) -> str | None:
    try:
        return normalize_symbol(value)
    except (TypeError, ValueError):
        return None


def _finite(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _json_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _identity(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    stat = path.stat()
    return {"path": str(path), "exists": True, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


class BenchmarkCoverageError(RuntimeError):
    """Raised when the benchmark cannot prove complete requested coverage."""


def _field_ops(node: Any, found: set[str] | None = None) -> set[str]:
    if found is None:
        found = set()
    if isinstance(node, Mapping):
        if node.get("op"):
            found.add(str(node["op"]))
        for value in node.values():
            _field_ops(value, found)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _field_ops(value, found)
    return found


class _RawPitSource:
    """Read canonical normalized PIT state, with raw-only fixture fallback."""

    def __init__(self, root: Path, calendar: tuple[int, ...], audit: list[str]):
        self.raw_root = root / "data/research/security_state/raw"
        self.normalized_root = root / "data/research/security_state/normalized"
        self.calendar = calendar
        self.audit = audit
        self._canonical_manifest = self.normalized_root / "manifest.json"
        self._canonical = PITStateStore(self.normalized_root) if self._canonical_manifest.exists() else None
        self._basics: dict[str, dict[str, Any]] | None = None
        self._all_stock: dict[int, dict[str, dict[str, Any]]] = {}
        self._history: dict[str, dict[int, dict[str, Any]]] = {}
        self._canonical_universe_cache: dict[int, dict[str, dict[str, Any]] | None] = {}

    @property
    def uses_canonical_store(self) -> bool:
        return self._canonical is not None

    def provenance(self) -> dict[str, Any]:
        if self._canonical is not None:
            self.audit.append(str(self._canonical_manifest))
            return {
                "mode": "CANONICAL_NORMALIZED",
                "dataset_version": PIT_DATASET_VERSION,
                "root": str(self.normalized_root),
                "manifest": _identity(self._canonical_manifest),
                "raw_fallback_used": False,
            }
        return {
            "mode": "RAW_FIXTURE_FALLBACK",
            "dataset_version": "BAOSTOCK_PIT_SECURITY_STATE_RAW_V1",
            "root": str(self.raw_root),
            "manifest": {"path": str(self._canonical_manifest), "exists": False},
            "raw_fallback_used": True,
        }

    def _load_basics(self) -> dict[str, dict[str, Any]]:
        if self._basics is not None:
            return self._basics
        path = self.raw_root / "stock_basic.json"
        if not path.exists():
            self._basics = {}
            return self._basics
        rows = (_json_load(path) or {}).get("rows", [])
        self.audit.append(str(path))
        basics: dict[str, dict[str, Any]] = {}
        for row in rows:
            if str(row.get("type", "")) != "1":
                continue
            symbol = _safe_symbol(row.get("code"))
            if symbol:
                basics[symbol] = dict(row)
        self._basics = basics
        return basics

    def _load_all_stock(self, day: int) -> dict[str, dict[str, Any]] | None:
        if day in self._all_stock:
            return self._all_stock[day]
        text = str(day)
        iso = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
        candidates = (
            self.raw_root / "all_stock" / f"trade_date={iso}.json",
            self.raw_root / "all_stock" / f"trade_date={day}.json",
        )
        path = next((candidate for candidate in candidates if candidate.exists()), None)
        if path is None:
            return None
        self.audit.append(str(path))
        rows = (_json_load(path) or {}).get("rows", [])
        values: dict[str, dict[str, Any]] = {}
        for row in rows:
            symbol = _safe_symbol(row.get("code"))
            if symbol:
                values[symbol] = dict(row)
        self._all_stock[day] = values
        return values

    def _load_history(self, symbol: str) -> dict[int, dict[str, Any]] | None:
        if symbol in self._history:
            return self._history[symbol]
        normalized = normalize_symbol(symbol)
        code, market = normalized.split(".")
        candidates = (
            self.raw_root / "history" / f"symbol={code}_{market}.json",
            self.raw_root / "history" / f"symbol={source_symbol(normalized).replace('.', '_')}.json",
        )
        path = next((candidate for candidate in candidates if candidate.exists()), None)
        if path is None:
            self._history[symbol] = {}
            return None
        self.audit.append(str(path))
        rows = (_json_load(path) or {}).get("rows", [])
        values: dict[int, dict[str, Any]] = {}
        for row in rows:
            parsed = _day(row.get("date"))
            if parsed is not None:
                values[parsed] = dict(row)
        self._history[symbol] = values
        return values

    @staticmethod
    def _active(row: Mapping[str, Any], day: int) -> bool | None:
        ipo = _day(row.get("ipoDate"))
        out = _day(row.get("outDate"))
        if ipo is None:
            return None
        return bool(ipo <= day and (out is None or day <= out))

    @staticmethod
    def _canonical_row_state(row: Mapping[str, Any]) -> dict[str, Any]:
        st_status = str(row.get("st_status") or ST_UNKNOWN)
        tradability = str(row.get("tradability_status") or STATUS_UNKNOWN)
        listed = row.get("listed")
        delisted = row.get("delisted")
        exists = row.get("exists")
        active = True if listed is True and delisted is False and exists is True else False if listed is False or delisted is True or exists is False else None
        st_allowed = True if st_status == ST_NORMAL else False if st_status == "ST" else None
        suspension_clear = True if tradability == TRADING else False if tradability == SUSPENDED else None
        eligibility = str(row.get("eligibility_status") or "UNKNOWN")
        pit = True if eligibility == "ELIGIBLE" else False if eligibility == "INELIGIBLE" else None
        return {
            "pit_eligible": pit,
            "universe_eligible": pit,
            "active_security": active,
            "security_active": active,
            "st_status": st_status,
            "st_allowed": st_allowed,
            "tradability_status": tradability,
            "suspension_clear": suspension_clear,
            "suspension_status": tradability,
            "tradability_known": st_status != ST_UNKNOWN and tradability != STATUS_UNKNOWN,
            "pit_state_source": "CANONICAL_NORMALIZED_PIT_UNIVERSE_V2",
            "pit_available_at": row.get("available_at"),
            "pit_reason_codes": list(row.get("reason_codes") or ()),
            "pit_source_lineage": row.get("source_lineage", {}),
        }

    def _canonical_state(self, symbol: str, day: int) -> dict[str, Any] | None:
        if self._canonical is None:
            return None
        if day not in self._canonical_universe_cache:
            self._canonical_universe_cache[day] = self._canonical.get_universe_rows(day)
        rows = self._canonical_universe_cache[day]
        if rows is None:
            return {
                "pit_eligible": None,
                "universe_eligible": None,
                "active_security": None,
                "security_active": None,
                "st_status": ST_UNKNOWN,
                "st_allowed": None,
                "tradability_status": STATUS_UNKNOWN,
                "suspension_clear": None,
                "suspension_status": STATUS_UNKNOWN,
                "tradability_known": False,
                "pit_state_source": "CANONICAL_NORMALIZED_PIT_UNIVERSE_V2",
                "pit_unknown_reason": "CANONICAL_UNIVERSE_SNAPSHOT_MISSING",
            }
        row = rows.get(symbol)
        if row is None:
            return {
                "pit_eligible": None,
                "universe_eligible": None,
                "active_security": None,
                "security_active": None,
                "st_status": ST_UNKNOWN,
                "st_allowed": None,
                "tradability_status": STATUS_UNKNOWN,
                "suspension_clear": None,
                "suspension_status": STATUS_UNKNOWN,
                "tradability_known": False,
                "pit_state_source": "CANONICAL_NORMALIZED_PIT_UNIVERSE_V2",
                "pit_unknown_reason": "CANONICAL_SYMBOL_DATE_ROW_MISSING",
            }
        return self._canonical_row_state(row)

    def state(self, symbol: str, day: int) -> dict[str, Any]:
        normalized = _safe_symbol(symbol)
        if normalized is None:
            return {"pit_eligible": None, "active_security": None, "st_allowed": None, "suspension_clear": None, "tradability_known": False}
        symbol = normalized
        canonical = self._canonical_state(symbol, day)
        if canonical is not None:
            return canonical
        basics = self._load_basics()
        all_stock = self._load_all_stock(day)
        history = self._load_history(symbol)
        basic = basics.get(symbol)
        history_row = (history or {}).get(day)
        population_row = (all_stock or {}).get(symbol) if all_stock is not None else None
        if basic is None or all_stock is None:
            return {"pit_eligible": None, "active_security": None, "st_allowed": None, "suspension_clear": None, "tradability_known": False}
        active = self._active(basic, day)
        if active is None:
            return {"pit_eligible": None, "active_security": None, "st_allowed": None, "suspension_clear": None, "tradability_known": False}
        st_value = map_is_st(history_row.get("isST")) if history_row else ST_UNKNOWN
        trade_value = map_trade_status(history_row.get("tradestatus")) if history_row else STATUS_UNKNOWN
        if trade_value == STATUS_UNKNOWN and population_row is not None:
            trade_value = map_trade_status(population_row.get("tradeStatus"))
        st_allowed = st_value == ST_NORMAL if st_value != ST_UNKNOWN else None
        suspension_clear = trade_value == TRADING if trade_value != STATUS_UNKNOWN else None
        membership = symbol in all_stock
        pit = bool(active and membership and st_allowed is True and suspension_clear is True)
        if st_allowed is None or suspension_clear is None:
            pit = None
        return {
            "pit_eligible": pit,
            "universe_eligible": pit,
            "active_security": active,
            "security_active": active,
            "st_status": st_value,
            "st_allowed": st_allowed,
            "tradability_status": trade_value,
            "suspension_clear": suspension_clear,
            "suspension_status": "CLEAR" if suspension_clear is True else ("SUSPENDED" if suspension_clear is False else "UNKNOWN"),
            "tradability_known": True,
        }

    def universe(self, day: int) -> list[str]:
        if self._canonical is not None:
            return self._canonical.get_universe_members(day) or []
        states = self._load_all_stock(day)
        if states is None:
            return []
        return [symbol for symbol in sorted(states) if self.state(symbol, day).get("pit_eligible") is True]

    def clear_partition_cache(self) -> None:
        """Release date/symbol PIT material after one deterministic partition."""

        self._all_stock.clear()
        self._history.clear()
        self._canonical_universe_cache.clear()
        if self._canonical is not None:
            self._canonical._universe_cache.clear()


class RealSampleFeasibilityProviderV1:
    """Materialize real, outcome-blind structural observations for preflight."""

    version = PROVIDER_VERSION

    def __init__(
        self,
        root: str | Path = ".",
        *,
        calendar_sessions: Iterable[Any] | None = None,
        daily_frame: pd.DataFrame | None = None,
        event_rows: Mapping[str, Iterable[Mapping[str, Any]] | pd.DataFrame] | None = None,
        factor_frames: Mapping[str, pd.DataFrame | Iterable[Mapping[str, Any]]] | None = None,
        pit_rows: Iterable[Mapping[str, Any]] | Mapping[Any, Any] | None = None,
        structural_observations: Iterable[Mapping[str, Any]] | Callable[[Any, ValidationDecisionPolicyV2], Iterable[Mapping[str, Any]]] | None = None,
        benchmark_frame: pd.DataFrame | None = None,
        streaming: bool = True,
        partition_session_count: int = 20,
    ):
        self.root = Path(root)
        self.guard = ResearchDataAccessGuard()
        self.reader = GuardedResearchReader(self.guard)
        self.calendar_sessions_override = tuple(_day(item) for item in (calendar_sessions or ()) if _day(item) is not None)
        self.daily_frame_override = daily_frame.copy() if daily_frame is not None else None
        self.event_rows_override = dict(event_rows or {})
        self.factor_frames_override = dict(factor_frames or {})
        self.pit_rows_override = pit_rows
        self.structural_observations = structural_observations
        self.benchmark_frame_override = benchmark_frame.copy() if benchmark_frame is not None else None
        self.streaming = bool(streaming)
        self.partition_session_count = max(1, int(partition_session_count))
        self._audit: list[str] = []
        self._daily: pd.DataFrame | None = None
        self._calendar: tuple[int, ...] | None = None
        self._pit: _RawPitSource | None = None
        self._factor_cache: dict[tuple[str, ...], dict[tuple[int, str], dict[str, Any]]] = {}
        self._factor_warmup_contracts: dict[str, dict[str, Any]] = {}
        self._registry: UnifiedFactorRegistry | None = None
        self._pit_cache: dict[tuple[int, str], dict[str, Any]] = {}
        self._event_cache: dict[str, list[dict[str, Any]]] = {}
        self._event_contract_cache: dict[str, dict[str, Any] | None] = {}
        self._benchmark: pd.DataFrame | None = None
        self._benchmark_source: dict[str, Any] | None = None
        self._benchmark_coverage: dict[str, Any] | None = None
        self._benchmark_cache_requests: list[dict[str, Any]] = []
        self._derived_event_definitions: dict[str, dict[str, Any]] = {}
        self._derived_event_days: dict[str, set[int]] = {}
        self._factor_single_cache: dict[str, dict[tuple[int, str], dict[str, Any]]] = {}
        self._inline_event_definitions: dict[str, dict[str, Any]] = {}
        self._inline_factor_definitions: dict[str, dict[str, Any]] = {}
        self._execution_cache: dict[tuple[int, str], dict[str, Any]] = {}
        self._execution_master: SecurityMaster | None = None
        self._execution_limit_model: ChinaPriceLimitModel | None = None

    def __call__(self, candidate: Any, policy: ValidationDecisionPolicyV2) -> CandidateSampleFeasibilityInputV1:
        return self.build(candidate, policy)

    @staticmethod
    def _candidate_payload(candidate: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        if hasattr(candidate, "to_dict"):
            raw = candidate.to_dict()
        elif isinstance(candidate, Mapping):
            raw = dict(candidate)
        else:
            raise ValueError("frozen candidate must be a mapping or expose to_dict()")
        PerformanceBlindGuard.assert_blind(raw)
        record = dict(raw)
        if isinstance(raw.get("candidate"), Mapping):
            nested = dict(raw["candidate"])
            nested.setdefault("signal_predicate", raw.get("signal_predicate", {}))
            nested.setdefault("exit_predicate", raw.get("exit_predicate", {}))
            nested.setdefault("semantic_status", raw.get("semantic_status"))
            nested.setdefault("semantic_fingerprint", raw.get("semantic_fingerprint"))
            nested.setdefault("record_preregistration_hash", raw.get("preregistration_hash"))
            record = raw
            raw = nested
        return {str(key): value for key, value in raw.items()}, record

    @staticmethod
    def _conditions(candidate: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        predicate = candidate.get("signal_predicate") if isinstance(candidate.get("signal_predicate"), Mapping) else {}
        factors = predicate.get("factor_conditions", candidate.get("factor_conditions", ()))
        events = predicate.get("event_conditions", candidate.get("event_conditions", ()))
        signal_logic = candidate.get("signal_logic") if isinstance(candidate.get("signal_logic"), Mapping) else {}
        if not events:
            events = [{"event_id": value, "required": True} for value in signal_logic.get("event_dependencies", ())]
        return [dict(item) for item in factors if isinstance(item, Mapping)], [dict(item) for item in events if isinstance(item, Mapping)]

    @staticmethod
    def _attach_contract_event_identities(
        event_conditions: Iterable[Mapping[str, Any]],
        record: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        identities = record.get("factor_event_registry_identities")
        if not isinstance(identities, Mapping):
            return [dict(item) for item in event_conditions]
        result: list[dict[str, Any]] = []
        for condition in event_conditions:
            item = dict(condition)
            identity = identities.get(str(item.get("event_id")))
            if isinstance(identity, Mapping):
                item["_contract_event_identity"] = dict(identity)
            result.append(item)
        return result

    @staticmethod
    def _canonical_event_row(
        row: Mapping[str, Any],
        *,
        event_id: str,
        event_day: int,
        available_at_date: int | None,
        available_at_ts: pd.Timestamp | None,
        event_version: str,
        event_semantic_version: Any,
        event_available_at_semantics: Any,
        event_same_day_supported: Any,
        event_semantic_contract: Any,
        materializer_name: str,
        event_type: Any = None,
        source_id: Any = None,
    ) -> dict[str, Any]:
        """Normalize every event path to one auditable row contract.

        ``event_time``/``available_at`` retain the repaired-store date fields;
        their timestamp counterparts are explicit so structural and predictive
        callers cannot silently compare different availability semantics.
        ``eligible_at`` means the event is eligible for evaluation, while the
        next tradable entry session remains a separate execution field.
        """

        available_text = available_at_ts.isoformat() if available_at_ts is not None else None
        normalized_available_date = available_at_date if available_at_date is not None else _day(available_at_ts)
        payload = dict(row)
        payload.update({
            "symbol": str(row.get("symbol")),
            "event_id": str(row.get("event_id") or event_id),
            "event_type": str(row.get("event_type") or event_type or event_id),
            "event_version": str(row.get("event_version") or event_version),
            "schema_version": EVENT_ROW_SCHEMA_VERSION,
            "source_id": str(row.get("source_id") or source_id or f"event_store_repaired:{event_id}"),
            "materializer_name": str(row.get("materializer_name") or materializer_name),
            "provider": PROVIDER_VERSION,
            "event_time": int(event_day),
            "event_time_semantics": "TRADE_DATE",
            "event_trade_date": int(event_day),
            "available_at": row.get("available_at") if row.get("available_at") is not None else normalized_available_date,
            "available_at_date": normalized_available_date,
            "available_at_ts": available_text,
            "eligible_at": available_text,
            "eligible_at_semantics": "EVENT_AVAILABLE_AT",
            "event_available_at": available_text,
            "event_available_at_date": normalized_available_date,
            "event_semantic_version": event_semantic_version,
            "event_available_at_semantics": event_available_at_semantics,
            "event_same_day_supported": event_same_day_supported,
            "event_semantic_contract": event_semantic_contract,
        })
        return payload

    @staticmethod
    def _factor_ids(candidate: Mapping[str, Any], factor_conditions: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
        values = [str(item.get("factor_id")) for item in candidate.get("factor_bindings", ()) if isinstance(item, Mapping) and item.get("factor_id")]
        values.extend(str(item.get("factor_id")) for item in factor_conditions if item.get("factor_id"))
        values.extend(str(item) for item in candidate.get("factor_ids", ()) if item)
        return tuple(dict.fromkeys(values))

    @staticmethod
    def _inline_factor_bindings(candidate: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        supported: dict[str, dict[str, Any]] = {}
        for item in candidate.get("factor_bindings", ()):
            if not isinstance(item, Mapping) or not item.get("factor_id"):
                continue
            formula = "".join(str(item.get("formula", "")).lower().split())
            if formula == "(close-low)/max(high-low,epsilon)" and str(item.get("available_at", "")).upper() == "T_CLOSE":
                supported[str(item["factor_id"])] = dict(item)
        return supported

    def _prime_inline_contracts(
        self,
        candidate: Mapping[str, Any],
        factor_ids: tuple[str, ...],
        event_conditions: Iterable[Mapping[str, Any]],
        start: int,
    ) -> None:
        for factor_id, binding in self._inline_factor_bindings(candidate).items():
            if factor_id not in factor_ids:
                continue
            self._inline_factor_definitions[factor_id] = binding
            self._factor_warmup_contracts[factor_id] = {
                "factor_id": factor_id,
                "version": str(binding.get("factor_version") or "inline-v1"),
                "lookback": 0,
                "min_warmup_bars": 0,
                "cross_sectional": False,
                "minimum_pit_universe_history": 0,
                "first_legally_computable_date": start,
                "warmup_truncation": "NONE",
                "source": "INLINE_FROZEN_DEFINITION",
            }
        for condition in event_conditions:
            definition = condition.get("definition")
            if (
                condition.get("event_id")
                and isinstance(definition, Mapping)
                and str(condition.get("available_at_semantics", "")).upper() == "T_CLOSE"
                and str(definition.get("low_to_previous_close_operator", "")).upper() == "LE"
                and str(definition.get("close_to_previous_close_operator", "")).upper() == "GE"
                and definition.get("close_above_open") is True
                and definition.get("positive_range_required") is True
            ):
                self._inline_event_definitions[str(condition["event_id"])] = dict(condition)

    def _materialize_inline_factors(
        self,
        candidate: Mapping[str, Any],
        factor_ids: tuple[str, ...],
        daily: pd.DataFrame,
        start: int,
        end: int,
        values: dict[tuple[int, str], dict[str, Any]],
    ) -> None:
        if daily.empty:
            return
        bindings = self._inline_factor_bindings(candidate)
        required = {"date", "symbol", "high", "low", "close"}
        if not required.issubset(daily.columns):
            return
        frame = daily[(daily["date"] >= start) & (daily["date"] <= end)]
        spread = pd.to_numeric(frame["high"], errors="coerce") - pd.to_numeric(frame["low"], errors="coerce")
        close = pd.to_numeric(frame["close"], errors="coerce")
        low = pd.to_numeric(frame["low"], errors="coerce")
        for factor_id in factor_ids:
            binding = bindings.get(factor_id)
            if binding is None:
                continue
            score = (close - low) / spread.where(spread > 0)
            for day, symbol, factor_value in zip(frame["date"], frame["symbol"], score):
                if not _finite(factor_value):
                    continue
                entry = values.setdefault((int(day), str(symbol)), {})
                if factor_id not in entry:
                    entry[factor_id] = float(factor_value)
                    entry.setdefault("factor_available_at", {})[factor_id] = _close_ts(int(day)).isoformat()
            self._inline_factor_definitions[factor_id] = binding
            self._factor_warmup_contracts[factor_id] = {
                "factor_id": factor_id,
                "version": str(binding.get("factor_version") or "inline-v1"),
                "lookback": 0,
                "min_warmup_bars": 0,
                "cross_sectional": False,
                "minimum_pit_universe_history": 0,
                "first_legally_computable_date": start,
                "warmup_truncation": "NONE",
                "source": "INLINE_FROZEN_DEFINITION",
            }

    def _materialize_inline_event(
        self,
        condition: Mapping[str, Any],
        daily: pd.DataFrame,
        start: int,
        end: int,
    ) -> list[dict[str, Any]]:
        event_id = str(condition.get("event_id", ""))
        definition = condition.get("definition")
        if not event_id or not isinstance(definition, Mapping) or daily.empty:
            return []
        supported = (
            str(condition.get("available_at_semantics", "")).upper() == "T_CLOSE"
            and str(definition.get("low_to_previous_close_operator", "")).upper() == "LE"
            and str(definition.get("close_to_previous_close_operator", "")).upper() == "GE"
            and definition.get("close_above_open") is True
            and definition.get("positive_range_required") is True
        )
        required = {"date", "symbol", "open", "high", "low", "close", "prev_close"}
        if not supported or not required.issubset(daily.columns):
            return []
        try:
            low_threshold = float(definition["low_to_previous_close_value"])
            close_threshold = float(definition["close_to_previous_close_value"])
        except (KeyError, TypeError, ValueError):
            return []
        if not (_finite(low_threshold) and _finite(close_threshold)):
            return []
        frame = daily[(daily["date"] >= start) & (daily["date"] <= end)].copy()
        previous = pd.to_numeric(frame["prev_close"], errors="coerce")
        low = pd.to_numeric(frame["low"], errors="coerce")
        close = pd.to_numeric(frame["close"], errors="coerce")
        open_ = pd.to_numeric(frame["open"], errors="coerce")
        high = pd.to_numeric(frame["high"], errors="coerce")
        mask = (
            (previous > 0)
            & (high > low)
            & ((low / previous) <= low_threshold)
            & ((close / previous) >= close_threshold)
            & (close > open_)
        )
        self._inline_event_definitions[event_id] = dict(condition)
        return [self._canonical_event_row(
            {"symbol": str(row.symbol)},
            event_id=event_id,
            event_day=int(row.date),
            available_at_date=int(row.date),
            available_at_ts=_close_confirmed_ts(int(row.date)),
            event_version="inline-v1",
            event_semantic_version="inline-v1",
            event_available_at_semantics="T_CLOSE",
            event_same_day_supported=True,
            event_semantic_contract="INLINE_FROZEN_DEFINITION",
            materializer_name=f"{PROVIDER_VERSION}._materialize_inline_event",
            event_type="INLINE_FROZEN_EVENT",
            source_id=f"inline:{event_id}",
        ) for row in frame.loc[mask, ["date", "symbol"]].itertuples(index=False)]

    def _event_source_identity(self) -> dict[str, Any]:
        if self._derived_event_definitions:
            return {
                "exists": True,
                "mode": "CONTRACT_DEFINED_BENCHMARK_DERIVATION",
                "definitions": self._derived_event_definitions,
                "source": self._benchmark_source or {},
            }
        if self._inline_event_definitions:
            return {
                "exists": True,
                "mode": "INLINE_FROZEN_DEFINITION",
                "definitions": self._inline_event_definitions,
            }
        return _identity(self.root / "data/research/event_store_repaired")

    def _factor_source_identity(self) -> dict[str, Any]:
        identity = _identity(self.root / "data/research/factor_library_v1/registry.json")
        if self._inline_factor_definitions:
            identity = {
                **identity,
                "exists": True,
                "inline_definitions": self._inline_factor_definitions,
            }
        return identity

    @staticmethod
    def _candidate_type(candidate: Mapping[str, Any]) -> str:
        return str(candidate.get("strategy_family") or candidate.get("family") or candidate.get("family_id") or candidate.get("candidate_type") or "UNKNOWN")

    def _window(self, candidate: Mapping[str, Any], policy: ValidationDecisionPolicyV2) -> tuple[int, int]:
        sample = candidate.get("sample_feasibility") if isinstance(candidate.get("sample_feasibility"), Mapping) else {}
        start = _day(sample.get("research_start", candidate.get("research_start", policy.research_start)))
        end = _day(sample.get("research_end", candidate.get("research_end", policy.research_end)))
        if start is None or end is None or start > end:
            raise ValueError("research window is invalid")
        self.guard.check_range(start, end, "real sample feasibility")
        return start, end

    def _load_calendar(self, start: int, end: int) -> tuple[int, ...]:
        if self._calendar is None:
            if self.calendar_sessions_override:
                values = self.calendar_sessions_override
            else:
                path = self.root / "data/research/security_state/raw/trade_calendar.json"
                if not path.exists():
                    self._calendar = tuple()
                    return self._calendar
                self._audit.append(str(path))
                values = tuple(_day(item) for item in (_json_load(path) or {}).get("trade_dates", ()))
            values = tuple(sorted(set(item for item in values if item is not None)))
            self.guard.check_int_iterable(values, "calendar")
            self._calendar = values
        return tuple(item for item in self._calendar if start <= item <= end)

    def _load_daily(self, start: int, end: int, warmup: int) -> pd.DataFrame:
        if self._daily is None:
            if self.daily_frame_override is not None:
                frame = self.daily_frame_override.copy()
            else:
                path = self.root / "data/research/daily_all.parquet"
                if not path.exists():
                    return pd.DataFrame()
                frame = self.reader.read_parquet(
                    path,
                    columns=["symbol", "date", "open", "high", "low", "close", "volume", "amount", "prev_close"],
                    start_date=warmup,
                    end_date=end,
                )
                self._audit.append(str(path))
            if "timestamp" in frame.columns and "date" not in frame.columns:
                frame = frame.rename(columns={"timestamp": "date"})
            required = {"symbol", "date", "open", "high", "low", "close", "volume"}
            if not required.issubset(frame.columns):
                return pd.DataFrame()
            frame = frame.copy()
            frame["date"] = frame["date"].map(_day)
            frame["symbol"] = frame["symbol"].map(_safe_symbol)
            frame = frame.dropna(subset=["date", "symbol"])
            frame["date"] = frame["date"].astype(int)
            if "prev_close" not in frame.columns:
                frame["prev_close"] = frame.sort_values(["symbol", "date"]).groupby("symbol")["close"].shift(1)
            self._daily = frame.sort_values(["date", "symbol"]).reset_index(drop=True)
        return self._daily[(self._daily["date"] >= warmup) & (self._daily["date"] <= end)].copy()

    def _pit_state(self, symbol: str, day: int) -> dict[str, Any]:
        key = (day, symbol)
        if key in self._pit_cache:
            return dict(self._pit_cache[key])
        override = self.pit_rows_override
        if override is not None:
            value: Any = None
            if isinstance(override, Mapping):
                value = override.get(key) or override.get(f"{day}:{symbol}")
                if value is None:
                    date_values = override.get(day) or override.get(str(day))
                    if isinstance(date_values, Mapping):
                        value = date_values.get(symbol)
            else:
                for item in override:
                    if _day(item.get("date", item.get("trade_date"))) == day and _safe_symbol(item.get("symbol")) == symbol:
                        value = item
                        break
            state = dict(value) if isinstance(value, Mapping) else {"pit_eligible": None}
        else:
            if self._pit is None:
                calendar = self._calendar or tuple()
                self._pit = _RawPitSource(self.root, calendar, self._audit)
            state = self._pit.state(symbol, day)
        self._pit_cache[key] = dict(state)
        return dict(state)

    def _pit_universe(self, day: int) -> list[str]:
        if self.pit_rows_override is not None:
            if isinstance(self.pit_rows_override, Mapping):
                date_values = self.pit_rows_override.get(day) or self.pit_rows_override.get(str(day)) or {}
                candidates = date_values.keys() if isinstance(date_values, Mapping) else ()
            else:
                candidates = (_safe_symbol(item.get("symbol")) for item in self.pit_rows_override)
            return sorted(symbol for symbol in candidates if symbol and self._pit_state(str(symbol), day).get("pit_eligible") is True)
        if self._pit is None:
            self._pit = _RawPitSource(self.root, self._calendar or tuple(), self._audit)
        return self._pit.universe(day)

    def _load_registry(self) -> UnifiedFactorRegistry | None:
        if self._registry is not None:
            return self._registry
        paths = (
            self.root / "data/research/factor_library_v1/registry.json",
            self.root / "data/research/unified_factor_registry/registry.json",
        )
        for path in paths:
            if path.exists():
                self._audit.append(str(path))
                self._registry = UnifiedFactorRegistry.read(path)
                return self._registry
        return None

    def _factor_values(self, factor_ids: tuple[str, ...], daily: pd.DataFrame, end: int) -> dict[tuple[int, str], dict[str, Any]]:
        key = tuple(factor_ids)
        if key in self._factor_cache:
            return self._factor_cache[key]
        values: dict[tuple[int, str], dict[str, Any]] = {}
        research_first = min(self._calendar) if self._calendar else None
        for factor_id in factor_ids:
            cached_factor = self._factor_single_cache.get(factor_id)
            if cached_factor is not None:
                for row_key, cached_row in cached_factor.items():
                    entry = values.setdefault(row_key, {})
                    entry[factor_id] = cached_row.get(factor_id)
                    if cached_row.get("factor_available_at"):
                        entry.setdefault("factor_available_at", {})[factor_id] = cached_row["factor_available_at"].get(factor_id)
                continue
            single_values: dict[tuple[int, str], dict[str, Any]] = {}
            injected = self.factor_frames_override.get(factor_id)
            if injected is not None:
                frame = injected.copy() if isinstance(injected, pd.DataFrame) else pd.DataFrame(list(injected))
                if "timestamp" not in frame.columns and "date" in frame.columns:
                    frame = frame.rename(columns={"date": "timestamp"})
                for row in frame.to_dict("records"):
                    day = _day(row.get("timestamp", row.get("date")))
                    symbol = _safe_symbol(row.get("symbol"))
                    if day is None or symbol is None:
                        continue
                    if research_first is not None and day < research_first:
                        continue
                    entry = values.setdefault((day, symbol), {})
                    entry[factor_id] = row.get("value", row.get(factor_id))
                    entry.setdefault("factor_available_at", {})[factor_id] = row.get("available_at", day)
                    single_entry = single_values.setdefault((day, symbol), {})
                    single_entry[factor_id] = entry[factor_id]
                    single_entry.setdefault("factor_available_at", {})[factor_id] = entry["factor_available_at"][factor_id]
                self._factor_warmup_contracts.setdefault(factor_id, {
                    "factor_id": factor_id,
                    "version": "injected",
                    "lookback": None,
                    "min_warmup_bars": None,
                    "cross_sectional": False,
                    "minimum_pit_universe_history": 0,
                    "first_legally_computable_date": None,
                    "source": "INJECTED_STRUCTURAL_FACTOR_FIXTURE",
                })
                self._factor_single_cache[factor_id] = single_values
                continue
            registry = self._load_registry()
            definition = registry.get(factor_id, "v1") if registry else None
            if definition is not None:
                pit_manifest_path = self.root / "data/research/security_state/normalized/manifest.json"
                pit_manifest = _json_load(pit_manifest_path) if pit_manifest_path.exists() else {}
                self._factor_warmup_contracts[factor_id] = {
                    "factor_id": definition.factor_id,
                    "version": definition.version,
                    "lookback": int(definition.lookback),
                    "min_warmup_bars": int(definition.min_warmup_bars),
                    "cross_sectional": bool(definition.cross_sectional),
                    "minimum_pit_universe_history": 1 if definition.cross_sectional else 0,
                    "pit_universe_coverage_start": pit_manifest.get("coverage_start"),
                    "daily_warmup_start": int(daily["date"].min()) if not daily.empty else None,
                    "first_legally_computable_date": research_first,
                    "warmup_truncation": "NONE" if research_first is not None else "UNKNOWN",
                    "source": "UNIFIED_FACTOR_REGISTRY",
                }
            if definition is None or definition.implementation_status != "EXECUTABLE" or definition.data_support_status != "FULL" or definition.pit_status != "PIT_VERIFIED":
                self._factor_single_cache[factor_id] = {}
                continue
            if definition.cross_sectional and _field_ops(definition.operator_graph) & {"rank", "zscore", "scale"}:
                if self._pit is None:
                    self._pit = _RawPitSource(self.root, self._calendar or tuple(), self._audit)
            view = AsOfDataView(
                frame=daily,
                data_manifest=stable_hash(_identity(self.root / "data/research/daily_all.parquet")),
                universe_version=PIT_DATASET_VERSION,
                universe_by_date=(lambda ts: self._pit_universe(int(ts))),
                as_of=end,
            )
            try:
                result = FactorCompiler(guard=self.guard, registry=registry).execute(definition, view, as_of=end)
            except Exception:
                self._factor_single_cache[factor_id] = {}
                continue
            if research_first is not None:
                result = result[result["timestamp"] >= research_first]
            for timestamp, raw_symbol, value, available_at in zip(
                result["timestamp"].tolist(),
                result["symbol"].tolist(),
                result["value"].tolist(),
                result["available_at"].tolist(),
            ):
                day = _day(timestamp)
                symbol = _safe_symbol(raw_symbol)
                if day is None or symbol is None:
                    continue
                entry = values.setdefault((day, symbol), {})
                entry[factor_id] = value
                entry.setdefault("factor_available_at", {})[factor_id] = available_at if available_at is not None else day
                single_entry = single_values.setdefault((day, symbol), {})
                single_entry[factor_id] = value
                single_entry.setdefault("factor_available_at", {})[factor_id] = available_at if available_at is not None else day
            self._factor_single_cache[factor_id] = single_values
        self._factor_cache[key] = values
        return values

    def _load_events(self, event_id: str, start: int, end: int) -> list[dict[str, Any]]:
        if event_id in self._event_cache:
            return list(self._event_cache[event_id])
        contract = self._event_contract(event_id)
        injected = self.event_rows_override.get(event_id)
        if injected is not None:
            frame = injected.copy() if isinstance(injected, pd.DataFrame) else pd.DataFrame(list(injected))
            rows = [dict(row) for row in frame.to_dict("records")]
        else:
            if not contract or contract.get("PIT_safe") is not True or str(contract.get("version")) not in {"v1", "v2"}:
                return []
            candidates = (self.root / f"data/research/event_store_repaired/{event_id}_v2.parquet", self.root / f"data/research/event_store_repaired/{event_id}.parquet")
            path = next((item for item in candidates if item.exists()), None)
            if path is None:
                return []
            rows_frame = self.reader.read_parquet(
                path,
                columns=["symbol", "event_time", "available_at", "available_at_date", "available_at_ts", "event_id", "event_version"],
                date_column="available_at_date",
                start_date=start,
                end_date=self._event_source_end(end),
            )
            self._audit.append(str(path))
            rows = [dict(row) for row in rows_frame.to_dict("records")]
        normalized: list[dict[str, Any]] = []
        for row in rows:
            symbol = _safe_symbol(row.get("symbol"))
            event_day = _day(row.get("event_trade_date", row.get("event_time")))
            available_day = _day(row.get("available_at_date", row.get("available_at")))
            available_ts = _timestamp(row.get("available_at_ts", row.get("available_at")), default_day=available_day)
            if symbol is None or event_day is None:
                continue
            normalized.append(self._canonical_event_row(
                {**row, "symbol": symbol},
                event_id=event_id,
                event_day=event_day,
                available_at_date=available_day,
                available_at_ts=available_ts,
                event_version=str(row.get("event_version") or "v1"),
                event_semantic_version=contract.get("version") if contract else None,
                event_available_at_semantics=contract.get("available_at_semantics") if contract else None,
                event_same_day_supported=contract.get("same_day_supported", True) if contract else True,
                event_semantic_contract=contract.get("contract_id") if contract else None,
                materializer_name=f"{PROVIDER_VERSION}._load_events",
            ))
        self._event_cache[event_id] = normalized
        return list(normalized)

    def _event_contract(self, event_id: str) -> dict[str, Any] | None:
        if event_id in self._event_contract_cache:
            cached = self._event_contract_cache[event_id]
            return dict(cached) if cached is not None else None
        paths = (
            self.root / "data/research/event_registry/registry_v2.json",
            self.root / "data/research/event_registry/registry.json",
        )
        contract: dict[str, Any] | None = None
        for path in paths:
            if not path.exists():
                continue
            self._audit.append(str(path))
            payload = _json_load(path) or {}
            entry = next((item for item in payload.get("events", ()) if str(item.get("event_id")) == event_id), None)
            if entry is not None:
                contract = dict(entry)
                break
        self._event_contract_cache[event_id] = contract
        return dict(contract) if contract is not None else None

    def _event_source_end(self, end: int) -> int:
        """Include the next session used by the repaired event date bucket.

        The canonical event timestamp is the signal-session close-confirmed
        instant, while the repaired parquet partition key stores the next
        eligible session date.  A partition ending at ``end`` therefore has
        to read one source date beyond ``end`` and filter by event trade date
        after loading.  Without this bounded extension, boundary events are
        silently omitted from structural replay.
        """

        for session in self._calendar or ():
            if int(session) > int(end):
                return int(session)
        return int(end)

    def _benchmark_path(self) -> Path | None:
        candidates = (
            self.root / "data/research/benchmark_index_daily.parquet",
            self.root / "data/tdx/clean/benchmark_index_daily.parquet",
        )
        for path in candidates:
            if path.exists():
                return path
        config_path = self.root / "config.yaml"
        if not config_path.exists():
            return None
        try:
            import yaml

            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            vipdoc = Path(str(((config.get("tdx") or {}).get("vipdoc") or "")))
            if not vipdoc.is_absolute():
                vipdoc = self.root / vipdoc
            path = vipdoc / "sh" / "lday" / "sh000300.day"
            return path if path.exists() else None
        except (OSError, TypeError, ValueError):
            return None

    @property
    def benchmark_coverage(self) -> dict[str, Any]:
        """Return the last explicit benchmark cache coverage contract."""

        return dict(self._benchmark_coverage or {})

    @staticmethod
    def _benchmark_source_version(source: Mapping[str, Any]) -> str:
        return stable_hash({
            "dataset_id": source.get("dataset_id"),
            "benchmark_identity": source.get("benchmark_identity"),
            "mode": source.get("mode"),
            "path": source.get("path"),
            "size": source.get("size"),
            "mtime_ns": source.get("mtime_ns"),
        })

    def _benchmark_coverage_contract(
        self,
        frame: pd.DataFrame,
        *,
        requested_start: int,
        requested_end: int,
        source: Mapping[str, Any],
    ) -> dict[str, Any]:
        dates = set(int(value) for value in frame["date"].tolist()) if not frame.empty else set()
        expected_dates = tuple(
            day for day in (self._calendar or ())
            if int(requested_start) <= int(day) <= int(requested_end)
        )
        missing_dates = [day for day in expected_dates if day not in dates]
        covered_start = int(frame["date"].min()) if not frame.empty else None
        covered_end = int(frame["date"].max()) if not frame.empty else None
        bounds_ok = (
            covered_start is not None
            and covered_end is not None
            and covered_start <= int(requested_start)
            and covered_end >= int(requested_end)
        )
        complete = bool(bounds_ok and not missing_dates)
        return {
            "contract_version": BENCHMARK_CACHE_CONTRACT_VERSION,
            "requested_start": int(requested_start),
            "requested_end": int(requested_end),
            "covered_start": covered_start,
            "covered_end": covered_end,
            "covered_rows": int(len(frame)),
            "covered_sessions": int(len(dates)),
            "expected_sessions": int(len(expected_dates)) if expected_dates else None,
            "missing_requested_dates": missing_dates,
            "coverage_status": "COMPLETE" if complete else "PARTIAL",
            "coverage_complete": complete,
            "benchmark_identity": str(source.get("benchmark_identity") or "sh000300"),
            "dataset_id": str(source.get("dataset_id") or "benchmark_index_daily"),
            "data_version": str(source.get("data_version") or "UNKNOWN"),
            "provider_version": PROVIDER_VERSION,
        }

    def _load_benchmark(self, end: int, start: int = CANONICAL_DAILY_WARMUP_START) -> pd.DataFrame:
        """Load an explicitly covered benchmark range; never widen a cache hit silently."""

        requested_start = int(start)
        requested_end = int(end)
        self.guard.check_range(requested_start, requested_end, "benchmark cache")
        if self._benchmark is not None and self._benchmark_coverage:
            coverage = self._benchmark_coverage
            if (
                coverage.get("coverage_complete") is True
                and int(coverage.get("covered_start")) <= requested_start
                and int(coverage.get("covered_end")) >= requested_end
            ):
                self._benchmark_cache_requests.append({
                    "requested_start": requested_start,
                    "requested_end": requested_end,
                    "action": "CACHE_HIT_FULLY_COVERED",
                    "covered_start": coverage.get("covered_start"),
                    "covered_end": coverage.get("covered_end"),
                })
                return self._benchmark

        load_start = min(CANONICAL_DAILY_WARMUP_START, requested_start)
        if self._benchmark_coverage and self._benchmark_coverage.get("covered_start") is not None:
            load_start = min(load_start, int(self._benchmark_coverage["covered_start"]))
        load_end = requested_end
        if self._benchmark_coverage and self._benchmark_coverage.get("covered_end") is not None:
            load_end = max(load_end, int(self._benchmark_coverage["covered_end"]))

        if self.benchmark_frame_override is not None:
            frame = self.benchmark_frame_override.copy()
            source = {
                "mode": "INJECTED_STRUCTURAL_BENCHMARK_FIXTURE",
                "dataset_id": "benchmark_index_daily",
                "benchmark_identity": "sh000300",
            }
        else:
            path = self._benchmark_path()
            if path is None:
                self._benchmark_coverage = {
                    "contract_version": BENCHMARK_CACHE_CONTRACT_VERSION,
                    "requested_start": requested_start,
                    "requested_end": requested_end,
                    "covered_start": None,
                    "covered_end": None,
                    "covered_rows": 0,
                    "covered_sessions": 0,
                    "expected_sessions": None,
                    "missing_requested_dates": [],
                    "coverage_status": "PARTIAL",
                    "coverage_complete": False,
                    "benchmark_identity": "sh000300",
                    "dataset_id": "benchmark_index_daily",
                    "data_version": "UNKNOWN",
                    "provider_version": PROVIDER_VERSION,
                    "failure_reason": "BENCHMARK_SOURCE_NOT_FOUND",
                }
                raise BenchmarkCoverageError("benchmark source is missing; structural coverage is incomplete")
            if path.suffix.lower() == ".parquet":
                frame = self.reader.read_parquet(
                    path,
                    columns=["date", "close", "volume"],
                    date_column="date",
                    start_date=load_start,
                    end_date=load_end,
                )
            else:
                frame = read_day_file_range(
                    path,
                    start_date=load_start,
                    end_date=load_end,
                    guard=self.guard,
                )
            self._audit.append(str(path))
            source = {
                "mode": "CANONICAL_BENCHMARK_INDEX_DAILY",
                "dataset_id": "benchmark_index_daily",
                "benchmark_identity": "sh000300",
                "path": str(path),
                "exists": True,
                "size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
            }
        required = {"date", "close", "volume"}
        if not required.issubset(frame.columns):
            self._benchmark_coverage = {
                "contract_version": BENCHMARK_CACHE_CONTRACT_VERSION,
                "requested_start": requested_start,
                "requested_end": requested_end,
                "covered_start": None,
                "covered_end": None,
                "covered_rows": 0,
                "covered_sessions": 0,
                "expected_sessions": None,
                "missing_requested_dates": [],
                "coverage_status": "PARTIAL",
                "coverage_complete": False,
                "benchmark_identity": str(source.get("benchmark_identity") or "sh000300"),
                "dataset_id": str(source.get("dataset_id") or "benchmark_index_daily"),
                "data_version": "UNKNOWN",
                "provider_version": PROVIDER_VERSION,
                "failure_reason": "BENCHMARK_REQUIRED_COLUMNS_MISSING",
            }
            raise BenchmarkCoverageError("benchmark source lacks date/close/volume; structural coverage is incomplete")
        frame = frame.copy()
        frame["date"] = frame["date"].map(_day)
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
        frame = frame.dropna(subset=["date", "close", "volume"])
        frame["date"] = frame["date"].astype(int)
        self.guard.check_frame(frame, "date")
        frame = frame.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
        source = dict(source)
        source["data_version"] = self._benchmark_source_version(source)
        coverage = self._benchmark_coverage_contract(
            frame,
            requested_start=requested_start,
            requested_end=requested_end,
            source=source,
        )
        self._benchmark_coverage = coverage
        source["coverage"] = coverage
        self._benchmark = frame
        self._benchmark_source = source
        self._benchmark_cache_requests.append({
            "requested_start": requested_start,
            "requested_end": requested_end,
            "load_start": load_start,
            "load_end": load_end,
            "action": "INITIAL_LOAD" if len(self._benchmark_cache_requests) == 0 else "RELOAD_EXPANDED_RANGE",
            "covered_start": coverage.get("covered_start"),
            "covered_end": coverage.get("covered_end"),
            "coverage_status": coverage.get("coverage_status"),
        })
        if coverage.get("coverage_complete") is not True:
            raise BenchmarkCoverageError(
                f"benchmark cache coverage is incomplete for requested range {requested_start}:{requested_end}"
            )
        return self._benchmark

    def _materialize_contract_defined_event(
        self,
        condition: Mapping[str, Any],
        daily: pd.DataFrame,
        start: int,
        end: int,
    ) -> list[dict[str, Any]]:
        event_id = str(condition.get("event_id") or "")
        identity = condition.get("_contract_event_identity")
        if not event_id or not isinstance(identity, Mapping):
            return []
        formula = " ".join(str(identity.get("formula") or "").casefold().split())
        expected_formula = (
            "on signal date t, close_t is greater than the maximum close of the preceding five completed sessions "
            "and volume_t is at least the median volume of the preceding twenty completed sessions"
        )
        if (
            identity.get("registry_kind") != "CONTRACT_DEFINED_DERIVED_EVENT"
            or identity.get("dataset_id") != "benchmark_index_daily"
            or identity.get("pit_safe") is not True
            or str(condition.get("available_at_semantics") or identity.get("available_at_semantics") or "").upper() != "T_CLOSE"
            or formula != expected_formula
            or daily.empty
        ):
            return []
        benchmark = self._load_benchmark(end)
        if benchmark.empty:
            return []
        event_frame = benchmark.copy()
        if self._calendar:
            event_frame = event_frame[event_frame["date"].isin(set(self._calendar))]
        event_frame["prior_close_max_5"] = event_frame["close"].shift(1).rolling(5, min_periods=5).max()
        event_frame["prior_volume_median_20"] = event_frame["volume"].shift(1).rolling(20, min_periods=20).median()
        event_days = event_frame.loc[
            (event_frame["date"] >= start)
            & (event_frame["date"] <= end)
            & (event_frame["close"] > event_frame["prior_close_max_5"])
            & (event_frame["volume"] >= event_frame["prior_volume_median_20"]),
            "date",
        ].astype(int)
        if event_days.empty:
            return []
        stocks = daily[daily["date"].isin(set(event_days))][["date", "symbol"]].drop_duplicates()
        if stocks.empty:
            return []
        known_event_days = self._derived_event_days.setdefault(event_id, set())
        known_event_days.update(int(day) for day in event_days.tolist())
        self._derived_event_definitions[event_id] = {
            "event_id": event_id,
            "event_type": DERIVED_BENCHMARK_EVENT_TYPE,
            "identity_version": identity.get("identity_version"),
            "schema_version": EVENT_ROW_SCHEMA_VERSION,
            "source_id": DERIVED_BENCHMARK_SOURCE_ID,
            "dataset_id": identity.get("dataset_id"),
            "formula": identity.get("formula"),
            "event_time_semantics": "TRADE_DATE",
            "available_at_semantics": "T_CLOSE",
            "eligible_at_semantics": "EVENT_AVAILABLE_AT",
            "availability_rule": "T_CLOSE+1S_AFTER_COMPLETED_BAR",
            "materializer_name": f"{PROVIDER_VERSION}._materialize_contract_defined_event",
            "provider": PROVIDER_VERSION,
            "event_days": int(len(known_event_days)),
            "source": self._benchmark_source or {},
        }
        rows: list[dict[str, Any]] = []
        for row in stocks.itertuples(index=False):
            day = int(row.date)
            rows.append(self._canonical_event_row(
                {"symbol": str(row.symbol)},
                event_id=event_id,
                event_day=day,
                available_at_date=day,
                available_at_ts=_close_confirmed_ts(day),
                event_version=str(identity.get("identity_version") or "v1").lower(),
                event_semantic_version=str(identity.get("identity_version") or "v1").lower(),
                event_available_at_semantics="T_CLOSE",
                event_same_day_supported=True,
                event_semantic_contract="CONTRACT_DEFINED_DERIVED_EVENT",
                materializer_name=f"{PROVIDER_VERSION}._materialize_contract_defined_event",
                event_type=DERIVED_BENCHMARK_EVENT_TYPE,
                source_id=DERIVED_BENCHMARK_SOURCE_ID,
            ))
        return rows

    def _resolve_event_rows(
        self,
        condition: Mapping[str, Any],
        daily: pd.DataFrame,
        start: int,
        end: int,
        *,
        partition: bool = False,
    ) -> list[dict[str, Any]]:
        """Resolve one event through the canonical contract-first chain.

        Structural preflight and predictive validation must agree on the
        event source.  In particular, a frozen contract-defined benchmark
        event is not an inline event merely because no repaired event parquet
        exists.  Keep the fallback order in one place so the two execution
        paths cannot silently diverge again.
        """

        event_id = str(condition.get("event_id") or "")
        if not event_id:
            return []
        loader = self._load_events_partition if partition else self._load_events
        event_rows = loader(event_id, start, end)
        if not event_rows:
            event_rows = self._materialize_contract_defined_event(condition, daily, start, end)
        if not event_rows and self._event_contract(event_id) is None:
            event_rows = self._materialize_inline_event(condition, daily, start, end)
        return event_rows

    def materialize_event_rows(
        self,
        event_conditions: Iterable[Mapping[str, Any]],
        daily: pd.DataFrame,
        start: int,
        end: int,
        *,
        record: Mapping[str, Any] | None = None,
    ) -> tuple[dict[tuple[int, str], list[dict[str, Any]]], dict[str, dict[str, Any]]]:
        """Materialize frozen event conditions for any outcome-blind caller.

        The returned event values are suitable for the corrected predictive
        engine.  The second value is a compact, non-outcome resolution audit
        used by recovery previews and parity reports.
        """

        conditions = self._attach_contract_event_identities(event_conditions, record or {})
        if self._calendar is None:
            self._load_calendar(CANONICAL_DAILY_WARMUP_START, end)
        event_values: dict[tuple[int, str], list[dict[str, Any]]] = {}
        resolutions: dict[str, dict[str, Any]] = {}
        for condition in conditions:
            event_id = str(condition.get("event_id") or "")
            rows = self._resolve_event_rows(condition, daily, start, end)
            for row in rows:
                event_values.setdefault((int(row["event_trade_date"]), str(row["symbol"])), []).append(row)
            definition = self._derived_event_definitions.get(event_id)
            identity = condition.get("_contract_event_identity")
            mode = "CONTRACT_DEFINED_BENCHMARK_DERIVATION" if definition else "INLINE_FROZEN_DEFINITION" if event_id in self._inline_event_definitions else "EVENT_STORE"
            resolutions[event_id] = {
                "event_id": event_id,
                "materialized": bool(rows),
                "row_count": len(rows),
                "mode": mode,
                "identity_hash": stable_hash(identity) if isinstance(identity, Mapping) else None,
                "event_days": definition.get("event_days") if isinstance(definition, Mapping) else None,
            }
        return event_values, resolutions

    def event_source_identity(self) -> dict[str, Any]:
        """Expose the same outcome-blind source identity used in provenance."""

        return dict(self._event_source_identity())

    @staticmethod
    def _compare(value: float, operator: str, target: float) -> bool:
        return {"GT": value > target, "GE": value >= target, "LT": value < target, "LE": value <= target, "EQ": value == target, "NE": value != target}.get(operator.upper(), False)

    def _factor_passes(self, values: Mapping[str, Any], conditions: Iterable[Mapping[str, Any]]) -> bool | None:
        for condition in conditions:
            factor_id = str(condition.get("factor_id", ""))
            if factor_id not in values or not _finite(values[factor_id]):
                return None
            # Rank-only candidates do not have a threshold predicate.  Their
            # frozen factor condition states which finite factor participates
            # in the later cross-sectional ranking step.  Treating the absent
            # ``operator/value`` pair as an unknown threshold incorrectly
            # turns every row into ``signal_qualified=None`` and destroys the
            # structural lower bound before ranking can run.
            rank_transform = str(condition.get("transform") or "").upper()
            rank_comparison = str(condition.get("comparison") or "").upper()
            if (
                "operator" not in condition
                and "value" not in condition
                and (
                    rank_transform in {"CROSS_SECTIONAL_PERCENTILE", "RANK", "RANK_ONLY"}
                    or rank_comparison in {"RANK_ASC", "RANK_DESC"}
                )
            ):
                continue
            try:
                if not self._compare(float(values[factor_id]), str(condition.get("operator", "")), float(condition["value"])):
                    return False
            except (KeyError, TypeError, ValueError):
                return None
        return True

    def _reset_execution_partition_cache(self) -> None:
        """Reset execution-only state at the bounded partition boundary."""

        self._execution_cache.clear()
        self._execution_master = SecurityMaster()
        self._execution_limit_model = ChinaPriceLimitModel(self._execution_master)

    def _execution_state(self, symbol: str, signal_day: int, next_day: int, daily_by_key: Mapping[tuple[int, str], Mapping[str, Any]]) -> dict[str, Any]:
        cache_key = (int(next_day), str(symbol))
        cached = self._execution_cache.get(cache_key)
        if cached is not None:
            return dict(cached)
        explicit = self._pit_state(symbol, next_day)
        bar = daily_by_key.get((next_day, symbol))
        if bar is None:
            result = {"execution_eligible": None, "next_session_eligible": None, "limit_state_open": None, "entry_price": None}
            self._execution_cache[cache_key] = result
            return dict(result)
        try:
            entry_price = float(bar.get("open"))
        except (TypeError, ValueError):
            entry_price = None
        if entry_price is None or not np.isfinite(entry_price) or entry_price <= 0:
            result = {"execution_eligible": None, "next_session_eligible": None, "limit_state_open": None, "entry_price": entry_price}
            self._execution_cache[cache_key] = result
            return dict(result)
        if self._execution_master is None or self._execution_limit_model is None:
            self._reset_execution_partition_cache()
        assert self._execution_master is not None
        assert self._execution_limit_model is not None
        self._execution_master.add_state(SecurityState(symbol=symbol, asof_date=next_day, is_st=explicit.get("st_allowed") is False))
        try:
            limit_ok, limit_reason = self._execution_limit_model.can_buy_at_open(symbol, pd.Timestamp(str(next_day), tz=SHANGHAI), dict(bar))
        finally:
            # The result is cached by (next_day, symbol); the shared model
            # must not retain every partition's SecurityState object.
            self._execution_master._states.pop(symbol, None)
        active = explicit.get("active_security")
        suspended = explicit.get("suspension_clear")
        if active is None or suspended is None or explicit.get("st_allowed") is None:
            eligible = None
        else:
            eligible = bool(active and suspended and explicit.get("st_allowed") and limit_ok)
        result = {
            "execution_eligible": eligible,
            "next_session_eligible": eligible,
            "limit_state_open": eligible if limit_ok else False,
            "limit_state": limit_reason,
            "active_security": active,
            "suspension_clear": suspended,
            "st_allowed": explicit.get("st_allowed"),
            "entry_price": entry_price,
        }
        self._execution_cache[cache_key] = result
        return dict(result)

    def _direct_rows(self, candidate: Any, policy: ValidationDecisionPolicyV2) -> list[dict[str, Any]] | None:
        if self.structural_observations is None:
            return None
        rows = self.structural_observations(candidate, policy) if callable(self.structural_observations) else self.structural_observations
        result = [dict(row) for row in rows]
        PerformanceBlindGuard.assert_blind(result)
        return result

    def build(self, candidate: Any, policy: ValidationDecisionPolicyV2) -> CandidateSampleFeasibilityInputV1:
        uses_injected_source = any(
            value is not None
            for value in (
                self.structural_observations,
                self.daily_frame_override,
                self.event_rows_override or None,
                self.factor_frames_override or None,
                self.pit_rows_override,
            )
        )
        if self.streaming and not uses_injected_source:
            return self._build_streaming(candidate, policy)
        return self._build_legacy(candidate, policy)

    def _build_legacy(self, candidate: Any, policy: ValidationDecisionPolicyV2) -> CandidateSampleFeasibilityInputV1:
        policy.assert_active()
        payload, record = self._candidate_payload(candidate)
        PerformanceBlindGuard.assert_blind(payload)
        start, end = self._window(payload, policy)
        family = self._candidate_type(payload)
        cross_sectional = "CROSS_SECTIONAL" in family.upper()
        factor_conditions, event_conditions = self._conditions(payload)
        event_conditions = self._attach_contract_event_identities(event_conditions, record)
        factor_ids = self._factor_ids(payload, factor_conditions)
        self._prime_inline_contracts(payload, factor_ids, event_conditions, start)
        candidate_hash = str(payload.get("candidate_hash") or payload.get("preregistration_hash") or record.get("preregistration_hash") or "")
        if not candidate_hash:
            raise ValueError("frozen candidate hash is required")
        record_hash = payload.get("record_preregistration_hash")
        if record_hash is not None and str(payload.get("preregistration_hash")) != str(record_hash):
            raise ValueError("semantic record and candidate preregistration hashes differ")
        required_candidate_fields = {"candidate_id", "candidate_status", "factor_bindings", "signal_logic", "preregistration_hash", "fingerprint"}
        if required_candidate_fields.issubset(payload):
            candidate_fields = {item.name for item in fields(StrategyCandidateSpec)}
            frozen = StrategyCandidateSpec.from_dict({key: value for key, value in payload.items() if key in candidate_fields})
            if frozen.candidate_status != "VALIDATION_READY":
                raise ValueError("sample feasibility requires a frozen VALIDATION_READY candidate")
            if frozen.preregistration_hash != str(payload.get("preregistration_hash")):
                raise ValueError("frozen candidate preregistration hash mismatch")
        sample = payload.get("sample_feasibility") if isinstance(payload.get("sample_feasibility"), Mapping) else {}
        holding = int(sample.get("holding_horizon", payload.get("holding_period", payload.get("holding_period_days", 0))) or 0)
        selection = payload.get("selection_rule") if isinstance(payload.get("selection_rule"), Mapping) else {}
        top_n = sample.get("top_n", selection.get("top_n"))
        max_positions = int(sample.get("max_positions", payload.get("max_positions", policy.max_positions)) or policy.max_positions)
        calendar = self._load_calendar(start, end)
        direct = self._direct_rows(candidate, policy)
        if direct is not None:
            observations = direct
            daily = pd.DataFrame()
        elif family not in SUPPORTED_FAMILIES or not calendar:
            observations = []
            daily = pd.DataFrame()
        else:
            registry = self._load_registry()
            lookback = max(
                [int(registry.get(item, "v1").lookback) for item in factor_ids if registry and registry.get(item, "v1")]
                or [20]
            )
            full_calendar = self._calendar or calendar
            if start in full_calendar:
                start_index = full_calendar.index(start)
                warmup_index = start_index - lookback - 2
                warmup = 20210802 if warmup_index < 0 else full_calendar[warmup_index]
            else:
                warmup = max(20210802, start - (lookback * 3))
            daily = self._load_daily(start, end, warmup)
            daily_by_key = {(int(row["date"]), str(row["symbol"])): row for row in daily.to_dict("records")}
            factor_values = self._factor_values(factor_ids, daily, end) if factor_ids else {}
            self._materialize_inline_factors(payload, factor_ids, daily, start, end, factor_values)
            event_values: dict[tuple[int, str], list[dict[str, Any]]] = {}
            for condition in event_conditions:
                event_rows = self._resolve_event_rows(condition, daily, start, end)
                for row in event_rows:
                    event_values.setdefault((int(row["event_trade_date"]), str(row["symbol"])), []).append(row)
            rows: list[dict[str, Any]] = []
            if event_conditions:
                keys = sorted(event_values)
            else:
                keys = sorted(key for key in factor_values if start <= key[0] <= end)
            for signal_day, symbol in keys:
                values = dict(factor_values.get((signal_day, symbol), {}))
                available = values.pop("factor_available_at", {})
                event_rows = event_values.get((signal_day, symbol), [])
                event_ok: bool | None = True
                event_evidence: list[dict[str, Any]] = []
                if event_conditions:
                    event_ok = True
                    for condition in event_conditions:
                        event_id = str(condition.get("event_id"))
                        matches = [event for event in event_rows if str(event.get("event_id")) == event_id]
                        legal_match = next(
                            (
                                event for event in matches
                                if event.get("event_available_at") is not None
                                and event.get("event_same_day_supported", True) is True
                                and _timestamp(event.get("event_available_at")) <= _close_confirmed_ts(signal_day)
                            ),
                            None,
                        )
                        if legal_match is not None:
                            event_evidence.append({
                                "event_id": legal_match.get("event_id"),
                                "event_version": legal_match.get("event_version"),
                                "event_semantic_version": legal_match.get("event_semantic_version"),
                                "event_semantic_contract": legal_match.get("event_semantic_contract"),
                                "event_trade_date": legal_match.get("event_trade_date"),
                                "event_available_at": legal_match.get("event_available_at"),
                            })
                            continue
                        event_ok = False
                        if matches:
                            future = matches[0]
                            event_evidence.append({
                                "event_id": future.get("event_id"),
                                "event_version": future.get("event_version"),
                                "event_semantic_version": future.get("event_semantic_version"),
                                "event_semantic_contract": future.get("event_semantic_contract"),
                                "event_trade_date": future.get("event_trade_date"),
                                "event_available_at": future.get("event_available_at"),
                                "event_available_at_future": True,
                            })
                factor_values_complete = all(factor_id in values and _finite(values[factor_id]) for factor_id in factor_ids)
                factor_ok = self._factor_passes(values, factor_conditions)
                if factor_ok is True and not factor_values_complete:
                    factor_ok = None
                factor_available_ok = all(_timestamp(item, default_day=signal_day) <= _close_ts(signal_day) for item in available.values()) if available else (not factor_ids)
                state = self._pit_state(symbol, signal_day)
                next_day = next((item for item in calendar if item > signal_day), None)
                execution = self._execution_state(symbol, signal_day, next_day, daily_by_key) if next_day is not None else {"execution_eligible": None, "next_session_eligible": None, "limit_state_open": None, "entry_price": None}
                hold_complete = None if holding <= 0 or signal_day not in calendar else calendar.index(signal_day) + holding + 1 < len(calendar)
                entry_session_index = calendar.index(next_day) if next_day in calendar else None
                holding_release_session_index = (
                    entry_session_index + holding
                    if entry_session_index is not None and holding > 0
                    else None
                )
                base_affordable = None
                small_affordable = None
                if execution.get("entry_price") is not None:
                    base_affordable = float(execution["entry_price"]) * int(sample.get("base_lot_size", payload.get("lot_size", policy.lot_size)) or policy.lot_size) <= float(sample.get("base_cash", policy.initial_cash) or policy.initial_cash)
                    small_affordable = float(execution["entry_price"]) * int(sample.get("small_capital_lot_size", policy.small_capital_lot_size) or policy.small_capital_lot_size) <= float(sample.get("small_capital_cash", policy.small_capital_cash) or policy.small_capital_cash)
                qualified: bool | None
                if event_ok is False or factor_ok is False:
                    qualified = False
                elif event_ok is None or factor_ok is None or not factor_available_ok:
                    qualified = None
                else:
                    # Raw signal qualification is kept separate from PIT and
                    # execution gates so the preflight can report a missing
                    # PIT state as UNKNOWN instead of silently dropping it.
                    qualified = True
                rows.append({
                    "signal_id": f"{payload.get('candidate_id')}:{signal_day}:{symbol}",
                    "candidate_id": payload.get("candidate_id"),
                    "signal_date": signal_day,
                    "signal_session_index": calendar.index(signal_day) if signal_day in calendar else None,
                    "symbol": symbol,
                    "factor_id": factor_ids[0] if factor_ids else None,
                    "factor_values": values,
                    "factor_available_at": available,
                    "factor_data_complete": factor_ok is True and factor_available_ok,
                    "data_complete": (
                        True if factor_ok is True and factor_available_ok and event_ok is True
                        else False if factor_ok is False or event_ok is False or not factor_available_ok
                        else None
                    ),
                    "event_id": event_evidence[0]["event_id"] if event_evidence else (str(event_conditions[0].get("event_id")) if event_conditions else None),
                    "event_evidence": event_evidence,
                    "event_trade_date": signal_day,
                    "event_available_at": event_evidence[0].get("event_available_at") if event_evidence else None,
                    "event_semantic_version": event_evidence[0].get("event_semantic_version") if event_evidence else None,
                    "event_semantic_contract": event_evidence[0].get("event_semantic_contract") if event_evidence else None,
                    "event_data_complete": event_ok is True if event_conditions else True,
                    "signal_qualified": qualified,
                    "pit_eligible": state.get("pit_eligible"),
                    "universe_eligible": state.get("universe_eligible", state.get("pit_eligible")),
                    "universe_as_of": _close_ts(signal_day).isoformat(),
                    "active_security": state.get("active_security"),
                    "st_allowed": state.get("st_allowed"),
                    "suspension_clear": state.get("suspension_clear"),
                    "tradability_status": state.get("tradability_status"),
                    "next_session": next_day,
                    "next_session_open": _open_text(next_day) if next_day else None,
                    "entry_session_index": entry_session_index,
                    "holding_release_session_index": holding_release_session_index,
                    # A fixed holding horizon plus the canonical trading
                    # calendar determines the release session even for a
                    # cross-sectional candidate.  Streaming capacity state is
                    # carried across date/partition boundaries, so marking
                    # this known release as unknown would manufacture a false
                    # capacity interval.
                    "holding_release_known": True if holding_release_session_index is not None else None,
                    "next_session_eligible": execution.get("next_session_eligible"),
                    "execution_eligible": execution.get("execution_eligible"),
                    "limit_state_open": execution.get("limit_state_open"),
                    "limit_state": execution.get("limit_state"),
                    "t1_eligible": True,
                    "t_plus_1_contract": "BUY_SETTLEMENT_T_PLUS_1",
                    "holding_complete": hold_complete,
                    "remaining_sessions": len(calendar) - calendar.index(signal_day) - 1 if signal_day in calendar else None,
                    "entry_price": execution.get("entry_price"),
                    "base_affordable": base_affordable,
                    "small_capital_affordable": small_affordable,
                    "rank": None,
                    "source_observation_hash": stable_hash({"candidate_hash": candidate_hash, "signal_day": signal_day, "symbol": symbol, "events": event_evidence, "factors": values}),
                })
            self._assign_ranks(rows, payload)
            observations = rows
        generated_at = datetime.now(tz=SHANGHAI).isoformat()
        source_audit = {
            "performance_files_read": [],
            "forbidden_performance_sources_checked": ["trades.csv", "equity.csv", "performance_metrics", "bootstrap_reports", "multiple_testing.json", "final_decisions"],
            "forbidden_performance_sources_read": False,
            "canonical_paths_read": sorted(set(self._audit)),
        }
        provenance = {
            "provider": PROVIDER_VERSION,
            "provider_version": PROVIDER_VERSION,
            "candidate_hash": candidate_hash,
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "policy_hash": policy.policy_hash,
            "research_window": {"start": start, "end": end},
            "daily_source": _identity(self.root / "data/research/daily_all.parquet"),
            "event_source": self._event_source_identity(),
            "benchmark_cache": {
                "coverage": self.benchmark_coverage,
                "requests": list(self._benchmark_cache_requests),
                "source": dict(self._benchmark_source or {}),
            },
            "factor_registry_source": self._factor_source_identity(),
            "pit_source": PIT_DATASET_VERSION,
            "pit_source_provenance": self._pit.provenance() if self._pit is not None else {"mode": "NOT_REQUIRED"},
            "factor_warmup_contracts": [self._factor_warmup_contracts[key] for key in sorted(self._factor_warmup_contracts)],
            "execution_contract": EXECUTION_CONTRACT_VERSION,
            "factor_ids": list(factor_ids),
            "event_ids": [str(item.get("event_id")) for item in event_conditions],
            "source_read_audit": source_audit,
            "observation_count": len(observations),
            "observation_hash": stable_hash(observations),
            "outcome_blind": True,
            "performance_data_loaded": False,
        }
        pit_source_detail = (
            self._pit.provenance()
            if self.pit_rows_override is None and self._pit is not None
            else {"mode": "INJECTED_STRUCTURAL_PIT_FIXTURE"}
        )
        pit_provenance = {
            "dataset_version": PIT_DATASET_VERSION,
            "source": str(pit_source_detail.get("root", "INJECTED_STRUCTURAL_PIT_FIXTURE")),
            "source_detail": pit_source_detail,
            "unknown_state_fail_closed": True,
            "calendar_identity": stable_hash(calendar),
        }
        result = CandidateSampleFeasibilityInputV1(
            candidate_id=str(payload.get("candidate_id", "")),
            candidate_hash=candidate_hash,
            family=family,
            mechanism=str(payload.get("mechanism") or family),
            candidate_type=str(payload.get("candidate_type") or family),
            research_start=start,
            research_end=end,
            holding_horizon=holding if holding > 0 else None,
            observations=tuple(observations),
            calendar_sessions=calendar,
            top_n=int(top_n) if top_n is not None else None,
            max_positions=max_positions,
            base_cash=float(sample.get("base_cash", policy.initial_cash) or policy.initial_cash),
            base_lot_size=int(sample.get("base_lot_size", payload.get("lot_size", policy.lot_size)) or policy.lot_size),
            small_capital_cash=float(sample.get("small_capital_cash", policy.small_capital_cash) or policy.small_capital_cash),
            small_capital_lot_size=int(sample.get("small_capital_lot_size", policy.small_capital_lot_size) or policy.small_capital_lot_size),
            data_provenance=provenance,
            pit_provenance=pit_provenance,
            calendar_identity=stable_hash(calendar),
            backend_type="REAL_FACTORY",
            generated_at=generated_at,
        )
        PerformanceBlindGuard.assert_blind(result.to_dict())
        return result

    @staticmethod
    def _structural_projection(row: Mapping[str, Any]) -> dict[str, Any]:
        fields = (
            "signal_id", "candidate_id", "signal_date", "signal_session_index", "symbol",
            "signal_qualified", "qualified", "pit_eligible", "universe_eligible", "data_complete",
            "factor_data_complete", "execution_eligible", "tradable", "next_session_eligible",
            "next_session", "t1_eligible", "t_plus_1_eligible", "holding_complete",
            "holding_sessions_complete", "remaining_sessions", "holding_sessions_available",
            "base_affordable", "small_capital_affordable", "portfolio_feasible",
            "portfolio_capacity_known", "prior_holdings_known", "portfolio_overlap_unknown",
            "holding_release_known", "entry_session_index", "holding_release_session_index",
            "active_security", "suspension_clear", "limit_state_open", "st_allowed", "rank",
        )
        return {name: row[name] for name in fields if name in row}

    def _load_daily_partition(self, start: int, end: int, warmup: int) -> pd.DataFrame:
        path = self.root / "data/research/daily_all.parquet"
        if not path.exists():
            return pd.DataFrame()
        frame = self.reader.read_parquet(
            path,
            columns=["symbol", "date", "open", "high", "low", "close", "volume", "amount", "prev_close"],
            start_date=warmup,
            end_date=end,
        )
        self._audit.append(str(path))
        if "timestamp" in frame.columns and "date" not in frame.columns:
            frame = frame.rename(columns={"timestamp": "date"})
        required = {"symbol", "date", "open", "high", "low", "close", "volume"}
        if not required.issubset(frame.columns):
            return pd.DataFrame()
        frame = frame.copy()
        frame["date"] = frame["date"].map(_day)
        frame["symbol"] = frame["symbol"].map(_safe_symbol)
        frame = frame.dropna(subset=["date", "symbol"])
        frame["date"] = frame["date"].astype(int)
        if "prev_close" not in frame.columns:
            frame["prev_close"] = frame.sort_values(["symbol", "date"]).groupby("symbol")["close"].shift(1)
        return frame.sort_values(["date", "symbol"]).reset_index(drop=True)

    def _factor_values_partition(
        self,
        factor_ids: tuple[str, ...],
        daily: pd.DataFrame,
        start: int,
        end: int,
        history: pd.DataFrame | None = None,
        history_limit: int = 32,
    ) -> dict[tuple[int, str], dict[str, Any]]:
        values: dict[tuple[int, str], dict[str, Any]] = {}
        registry = self._load_registry()
        if registry is None or daily.empty:
            return values
        factor_daily = daily[daily["date"] <= int(end)].copy()
        if history is not None and not history.empty:
            factor_daily = pd.concat([history, factor_daily], ignore_index=True)
            factor_daily = factor_daily.drop_duplicates(subset=["date", "symbol"], keep="last")
            factor_daily = factor_daily.sort_values(["date", "symbol"]).reset_index(drop=True)
            if history_limit > 0:
                factor_daily = factor_daily.groupby("symbol", group_keys=False).tail(int(history_limit)).reset_index(drop=True)
        if factor_daily.empty:
            return values
        pit_manifest_path = self.root / "data/research/security_state/normalized/manifest.json"
        pit_manifest = _json_load(pit_manifest_path) if pit_manifest_path.exists() else {}
        executable_definitions = []
        for factor_id in factor_ids:
            definition = registry.get(factor_id, "v1")
            if definition is None:
                continue
            self._factor_warmup_contracts.setdefault(factor_id, {
                "factor_id": definition.factor_id,
                "version": definition.version,
                "lookback": int(definition.lookback),
                "min_warmup_bars": int(definition.min_warmup_bars),
                "cross_sectional": bool(definition.cross_sectional),
                "minimum_pit_universe_history": 1 if definition.cross_sectional else 0,
                "pit_universe_coverage_start": pit_manifest.get("coverage_start"),
                "daily_warmup_start": int(daily["date"].min()),
                "first_legally_computable_date": start,
                "warmup_truncation": "NONE",
                "source": "UNIFIED_FACTOR_REGISTRY",
            })
            if definition.implementation_status != "EXECUTABLE" or definition.data_support_status != "FULL" or definition.pit_status != "PIT_VERIFIED":
                continue
            if definition.cross_sectional and _field_ops(definition.operator_graph) & {"rank", "zscore", "scale"} and self._pit is None:
                self._pit = _RawPitSource(self.root, self._calendar or tuple(), self._audit)
            executable_definitions.append(definition)
        if not executable_definitions:
            return values
        view = AsOfDataView(
            frame=factor_daily,
            data_manifest=stable_hash(_identity(self.root / "data/research/daily_all.parquet")),
            universe_version=PIT_DATASET_VERSION,
            universe_by_date=(lambda ts: self._pit_universe(int(ts))),
            as_of=end,
        )
        compiler = FactorCompiler(guard=self.guard, registry=registry)
        results_by_factor = {}
        try:
            results_by_factor = compiler.execute_batch(executable_definitions, view, as_of=end)
        except Exception:
            # Preserve the legacy fail-closed behavior when one definition
            # cannot be evaluated in a shared batch.
            for definition in executable_definitions:
                try:
                    results_by_factor[definition.factor_id] = compiler.execute(definition, view, as_of=end)
                except Exception:
                    continue
        for factor_id, result in results_by_factor.items():
            for timestamp, raw_symbol, value, available_at in zip(
                result["timestamp"].tolist(),
                result["symbol"].tolist(),
                result["value"].tolist(),
                result["available_at"].tolist(),
            ):
                day = _day(timestamp)
                symbol = _safe_symbol(raw_symbol)
                if day is None or symbol is None or not (start <= day <= end):
                    continue
                entry = values.setdefault((day, symbol), {})
                entry[factor_id] = value
                entry.setdefault("factor_available_at", {})[factor_id] = available_at if available_at is not None else day
        return values

    def _load_events_partition(self, event_id: str, start: int, end: int) -> list[dict[str, Any]]:
        contract = self._event_contract(event_id)
        injected = self.event_rows_override.get(event_id)
        if injected is not None:
            frame = injected.copy() if isinstance(injected, pd.DataFrame) else pd.DataFrame(list(injected))
            rows = [dict(row) for row in frame.to_dict("records")]
        else:
            if not contract or contract.get("PIT_safe") is not True or str(contract.get("version")) not in {"v1", "v2"}:
                return []
            candidates = (
                self.root / f"data/research/event_store_repaired/{event_id}_v2.parquet",
                self.root / f"data/research/event_store_repaired/{event_id}.parquet",
            )
            path = next((item for item in candidates if item.exists()), None)
            if path is None:
                return []
            frame = self.reader.read_parquet(
                path,
                columns=["symbol", "event_time", "available_at", "available_at_date", "available_at_ts", "event_id", "event_version"],
                date_column="available_at_date",
                start_date=start,
                end_date=self._event_source_end(end),
            )
            self._audit.append(str(path))
            rows = [dict(row) for row in frame.to_dict("records")]
        normalized: list[dict[str, Any]] = []
        for row in rows:
            symbol = _safe_symbol(row.get("symbol"))
            event_day = _day(row.get("event_trade_date", row.get("event_time")))
            available_day = _day(row.get("available_at_date", row.get("available_at")))
            available_ts = _timestamp(row.get("available_at_ts", row.get("available_at")), default_day=available_day)
            if symbol is None or event_day is None or not (start <= event_day <= end):
                continue
            normalized.append(self._canonical_event_row(
                {**row, "symbol": symbol},
                event_id=event_id,
                event_day=event_day,
                available_at_date=available_day,
                available_at_ts=available_ts,
                event_version=str(row.get("event_version") or "v1"),
                event_semantic_version=contract.get("version") if contract else None,
                event_available_at_semantics=contract.get("available_at_semantics") if contract else None,
                event_same_day_supported=contract.get("same_day_supported", True) if contract else True,
                event_semantic_contract=contract.get("contract_id") if contract else None,
                materializer_name=f"{PROVIDER_VERSION}._load_events_partition",
            ))
        return normalized

    def _stream_manifest_path(self, candidate_hash: str, manifest_id: str) -> Path:
        directory = self.root / "data/research/research_factory/structural_partitions_v1" / candidate_hash
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{manifest_id}.json"

    @staticmethod
    def _update_factor_history(
        history: pd.DataFrame | None,
        daily: pd.DataFrame,
        end: int,
        limit: int,
    ) -> pd.DataFrame:
        current = daily[daily["date"] <= int(end)] if not daily.empty else pd.DataFrame()
        if history is None or history.empty:
            combined = current.copy()
        elif current.empty:
            combined = history.copy()
        else:
            combined = pd.concat([history, current], ignore_index=True)
        if combined.empty:
            return pd.DataFrame()
        combined = combined.drop_duplicates(subset=["date", "symbol"], keep="last")
        combined = combined.sort_values(["date", "symbol"])
        return combined.groupby("symbol", group_keys=False).tail(int(limit)).reset_index(drop=True)

    def _build_streaming(self, candidate: Any, policy: ValidationDecisionPolicyV2) -> CandidateSampleFeasibilityInputV1:
        policy.assert_active()
        payload, record = self._candidate_payload(candidate)
        PerformanceBlindGuard.assert_blind(payload)
        start, end = self._window(payload, policy)
        family = self._candidate_type(payload)
        cross_sectional = "CROSS_SECTIONAL" in family.upper()
        factor_conditions, event_conditions = self._conditions(payload)
        event_conditions = self._attach_contract_event_identities(event_conditions, record)
        factor_ids = self._factor_ids(payload, factor_conditions)
        self._prime_inline_contracts(payload, factor_ids, event_conditions, start)
        candidate_hash = str(payload.get("candidate_hash") or payload.get("preregistration_hash") or record.get("preregistration_hash") or "")
        if not candidate_hash:
            raise ValueError("frozen candidate hash is required")
        required_candidate_fields = {"candidate_id", "candidate_status", "factor_bindings", "signal_logic", "preregistration_hash", "fingerprint"}
        if required_candidate_fields.issubset(payload):
            candidate_fields = {item.name for item in fields(StrategyCandidateSpec)}
            frozen = StrategyCandidateSpec.from_dict({key: value for key, value in payload.items() if key in candidate_fields})
            if frozen.candidate_status != "VALIDATION_READY" or frozen.preregistration_hash != str(payload.get("preregistration_hash")):
                raise ValueError("frozen candidate contract is not validation-ready")
        sample = payload.get("sample_feasibility") if isinstance(payload.get("sample_feasibility"), Mapping) else {}
        holding = int(sample.get("holding_horizon", payload.get("holding_period", payload.get("holding_period_days", 0))) or 0)
        selection = payload.get("selection_rule") if isinstance(payload.get("selection_rule"), Mapping) else {}
        top_n = sample.get("top_n", selection.get("top_n"))
        max_positions = int(sample.get("max_positions", payload.get("max_positions", policy.max_positions)) or policy.max_positions)
        calendar = self._load_calendar(start, end)
        if family not in SUPPORTED_FAMILIES or not calendar:
            return self._build_legacy(candidate, policy)
        calendar_index = {day: index for index, day in enumerate(calendar)}
        next_session_by_day = dict(zip(calendar, calendar[1:]))
        registry = self._load_registry()
        lookback = max(
            [int(registry.get(item, "v1").lookback) for item in factor_ids if registry and registry.get(item, "v1")]
            or [20]
        )
        data_identity = _identity(self.root / "data/research/daily_all.parquet")
        factor_identity = _identity(self.root / "data/research/factor_library_v1/registry.json")
        pit_identity = _identity(self.root / "data/research/security_state/normalized/manifest.json")
        manifest_basis = {
            "provider_version": PROVIDER_VERSION,
            "streaming_algorithm_version": "STREAMING_PROVIDER_V1_20260903_02_RANK_ONLY_COMPOSITE_PARITY",
            "benchmark_cache_contract_version": BENCHMARK_CACHE_CONTRACT_VERSION,
            "canonical_payload_hash": stable_hash(payload),
            "candidate_hash": candidate_hash,
            "policy_hash": policy.policy_hash,
            "research_window": {"start": start, "end": end},
            "calendar": calendar,
            "factor_ids": factor_ids,
            "data_identity": data_identity,
            "factor_identity": factor_identity,
            "pit_identity": pit_identity,
            "partition_session_count": self.partition_session_count,
        }
        manifest_id = stable_hash(manifest_basis)
        manifest_path = self._stream_manifest_path(candidate_hash, manifest_id)
        manifest: dict[str, Any]
        if manifest_path.exists():
            manifest = _json_load(manifest_path)
        else:
            manifest = {
                "schema_version": "full-window-structural-partition-manifest-v1",
                "manifest_id": manifest_id,
                "manifest_basis": manifest_basis,
                "partition_policy": {"session_count": self.partition_session_count, "date_aligned": True},
                "partitions": [],
                "observation_count": 0,
                "observation_hash": "",
                "completed_partition_count": 0,
                "outcome_blind": True,
                "performance_data_loaded": False,
            }
        existing_by_index = {int(item["index"]): item for item in manifest.get("partitions", ())}
        digest = hashlib.sha256()
        for descriptor in manifest.get("partitions", ()):
            path = manifest_path.parent / str(descriptor["file"])
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        digest.update((line + "\n").encode("utf-8"))
        factor_history: pd.DataFrame | None = None
        history_limit = max(int(lookback) + 2, 32)
        expected_partition_count = (len(calendar) + self.partition_session_count - 1) // self.partition_session_count
        cache_complete = set(existing_by_index) == set(range(expected_partition_count)) and all(
            (manifest_path.parent / str(item["file"])).exists()
            for item in existing_by_index.values()
        )
        partition_offsets = () if cache_complete else enumerate(range(0, len(calendar), self.partition_session_count))
        for partition_index, offset in partition_offsets:
            signal_start = calendar[offset]
            signal_end = calendar[min(len(calendar) - 1, offset + self.partition_session_count - 1)]
            descriptor = existing_by_index.get(partition_index)
            next_day = next_session_by_day.get(signal_end)
            load_end = next_day if next_day is not None else signal_end
            warmup_index = max(0, offset - lookback - 2)
            warmup = CANONICAL_DAILY_WARMUP_START if warmup_index == 0 else calendar[warmup_index]
            if descriptor is not None and (manifest_path.parent / str(descriptor["file"])).exists():
                if factor_ids:
                    daily = self._load_daily_partition(warmup, load_end, warmup)
                    factor_history = self._update_factor_history(factor_history, daily, signal_end, history_limit)
                    del daily
                    gc.collect()
                continue
            daily = self._load_daily_partition(warmup, load_end, warmup)
            factor_values = self._factor_values_partition(factor_ids, daily, signal_start, signal_end, factor_history, history_limit) if factor_ids else {}
            self._materialize_inline_factors(payload, factor_ids, daily, signal_start, signal_end, factor_values)
            event_values: dict[tuple[int, str], list[dict[str, Any]]] = {}
            for condition in event_conditions:
                event_rows = self._resolve_event_rows(condition, daily, signal_start, signal_end, partition=True)
                for row in event_rows:
                    event_values.setdefault((int(row["event_trade_date"]), str(row["symbol"])), []).append(row)
            keys = sorted(event_values) if event_conditions else sorted(key for key in factor_values if signal_start <= key[0] <= signal_end)
            signal_symbols = {str(symbol) for _day_value, symbol in keys}
            next_days = {next_session_by_day.get(int(signal_day)) for signal_day, _symbol in keys}
            next_days.discard(None)
            execution_rows = daily[
                daily["date"].isin(next_days) & daily["symbol"].isin(signal_symbols)
            ]
            daily_by_key = {
                (int(row["date"]), str(row["symbol"])): row
                for row in execution_rows.to_dict("records")
            }
            del execution_rows
            self._reset_execution_partition_cache()
            rows: list[dict[str, Any]] = []
            for signal_day, symbol in keys:
                values = dict(factor_values.get((signal_day, symbol), {}))
                available = values.pop("factor_available_at", {})
                event_rows = event_values.get((signal_day, symbol), [])
                event_ok: bool | None = True
                event_evidence: list[dict[str, Any]] = []
                if event_conditions:
                    for condition in event_conditions:
                        event_id = str(condition.get("event_id"))
                        matches = [event for event in event_rows if str(event.get("event_id")) == event_id]
                        legal_match = next((event for event in matches if event.get("event_available_at") is not None and event.get("event_same_day_supported", True) is True and _timestamp(event.get("event_available_at")) <= _close_confirmed_ts(signal_day)), None)
                        if legal_match is not None:
                            event_evidence.append({key: legal_match.get(key) for key in ("event_id", "event_version", "event_semantic_version", "event_semantic_contract", "event_trade_date", "event_available_at")})
                        else:
                            event_ok = False
                            if matches:
                                future = matches[0]
                                event_evidence.append({key: future.get(key) for key in ("event_id", "event_version", "event_semantic_version", "event_semantic_contract", "event_trade_date", "event_available_at")} | {"event_available_at_future": True})
                factor_values_complete = all(factor_id in values and _finite(values[factor_id]) for factor_id in factor_ids)
                factor_ok = self._factor_passes(values, factor_conditions)
                if factor_ok is True and not factor_values_complete:
                    factor_ok = None
                factor_available_ok = all(_timestamp(item, default_day=signal_day) <= _close_ts(signal_day) for item in available.values()) if available else (not factor_ids)
                state = self._pit_state(symbol, signal_day)
                next_session = next_session_by_day.get(signal_day)
                execution = self._execution_state(symbol, signal_day, next_session, daily_by_key) if next_session is not None else {"execution_eligible": None, "next_session_eligible": None, "limit_state_open": None, "entry_price": None}
                hold_complete = None if holding <= 0 or signal_day not in calendar else calendar_index[signal_day] + holding + 1 < len(calendar)
                entry_session_index = calendar_index.get(next_session)
                holding_release_session_index = entry_session_index + holding if entry_session_index is not None and holding > 0 else None
                base_affordable = small_affordable = None
                if execution.get("entry_price") is not None:
                    base_affordable = float(execution["entry_price"]) * int(sample.get("base_lot_size", payload.get("lot_size", policy.lot_size)) or policy.lot_size) <= float(sample.get("base_cash", policy.initial_cash) or policy.initial_cash)
                    small_affordable = float(execution["entry_price"]) * int(sample.get("small_capital_lot_size", policy.small_capital_lot_size) or policy.small_capital_lot_size) <= float(sample.get("small_capital_cash", policy.small_capital_cash) or policy.small_capital_cash)
                if event_ok is False or factor_ok is False:
                    qualified = False
                elif event_ok is None or factor_ok is None or not factor_available_ok:
                    qualified = None
                else:
                    qualified = True
                rows.append({
                    "signal_id": f"{payload.get('candidate_id')}:{signal_day}:{symbol}",
                    "candidate_id": payload.get("candidate_id"),
                    "signal_date": signal_day,
                    "signal_session_index": calendar_index.get(signal_day),
                    "symbol": symbol,
                    "factor_values": values,
                    "factor_data_complete": factor_ok is True and factor_available_ok,
                    "data_complete": True if factor_ok is True and factor_available_ok and event_ok is True else False if factor_ok is False or event_ok is False or not factor_available_ok else None,
                    "event_id": event_evidence[0]["event_id"] if event_evidence else (str(event_conditions[0].get("event_id")) if event_conditions else None),
                    "event_available_at": event_evidence[0].get("event_available_at") if event_evidence else None,
                    "signal_qualified": qualified,
                    "pit_eligible": state.get("pit_eligible"),
                    "universe_eligible": state.get("universe_eligible", state.get("pit_eligible")),
                    "active_security": state.get("active_security"),
                    "st_allowed": state.get("st_allowed"),
                    "suspension_clear": state.get("suspension_clear"),
                    "next_session": next_session,
                    "entry_session_index": entry_session_index,
                    "holding_release_session_index": holding_release_session_index,
                    "holding_release_known": True if holding_release_session_index is not None else None,
                    "next_session_eligible": execution.get("next_session_eligible"),
                    "execution_eligible": execution.get("execution_eligible"),
                    "limit_state_open": execution.get("limit_state_open"),
                    "t1_eligible": True,
                    "holding_complete": hold_complete,
                    "remaining_sessions": len(calendar) - calendar_index[signal_day] - 1 if signal_day in calendar else None,
                    "base_affordable": base_affordable,
                    "small_capital_affordable": small_affordable,
                    "rank": None,
                })
            self._assign_ranks(rows, payload)
            projected = [self._structural_projection(row) for row in rows]
            PerformanceBlindGuard.assert_blind(projected)
            partition_name = f"{manifest_id}_partition_{partition_index:04d}_{signal_start}_{signal_end}.jsonl"
            partition_path = manifest_path.parent / partition_name
            with partition_path.open("w", encoding="utf-8", newline="\n") as handle:
                for row in projected:
                    encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    handle.write(encoded + "\n")
                    digest.update((encoded + "\n").encode("utf-8"))
            descriptor = {
                "index": partition_index,
                "partition_id": stable_hash({"manifest_id": manifest_id, "index": partition_index, "start": signal_start, "end": signal_end}),
                "signal_start": signal_start,
                "signal_end": signal_end,
                "row_count": len(projected),
                "file": partition_name,
            }
            manifest["partitions"] = [item for item in manifest.get("partitions", ()) if int(item["index"]) != partition_index] + [descriptor]
            manifest["partitions"] = sorted(manifest["partitions"], key=lambda item: int(item["index"]))
            manifest["observation_count"] = sum(int(item.get("row_count", 0)) for item in manifest["partitions"])
            manifest["observation_hash"] = digest.hexdigest()
            manifest["completed_partition_count"] = partition_index + 1
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            if factor_ids:
                factor_history = self._update_factor_history(factor_history, daily, signal_end, history_limit)
            del rows, projected, factor_values, event_values, daily_by_key, daily
            self._reset_execution_partition_cache()
            self._pit_cache.clear()
            if self._pit is not None:
                self._pit.clear_partition_cache()
            gc.collect()
        store = ObservationPartitionStoreV1.open(manifest_path)
        generated_at = datetime.now(tz=SHANGHAI).isoformat()
        source_audit = {
            "performance_files_read": [],
            "forbidden_performance_sources_checked": ["trades.csv", "equity.csv", "performance_metrics", "bootstrap_reports", "multiple_testing.json", "final_decisions"],
            "forbidden_performance_sources_read": False,
            "canonical_paths_read": sorted(set(self._audit)),
        }
        provenance = {
            "provider": PROVIDER_VERSION,
            "provider_version": PROVIDER_VERSION,
            "execution_mode": "STREAMING_PARTITIONED",
            "candidate_hash": candidate_hash,
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "policy_hash": policy.policy_hash,
            "research_window": {"start": start, "end": end},
            "daily_source": data_identity,
            "event_source": self._event_source_identity(),
            "benchmark_cache": {
                "coverage": self.benchmark_coverage,
                "requests": list(self._benchmark_cache_requests),
                "source": dict(self._benchmark_source or {}),
            },
            "factor_registry_source": self._factor_source_identity(),
            "pit_source": PIT_DATASET_VERSION,
            "pit_source_provenance": self._pit.provenance() if self._pit is not None else {"mode": "NOT_REQUIRED"},
            "factor_warmup_contracts": [self._factor_warmup_contracts[key] for key in sorted(self._factor_warmup_contracts)],
            "execution_contract": EXECUTION_CONTRACT_VERSION,
            "factor_ids": list(factor_ids),
            "event_ids": [str(item.get("event_id")) for item in event_conditions],
            "source_read_audit": source_audit,
            "observation_count": store.observation_count,
            "observation_hash": store.observation_hash,
            "observation_store": store.to_dict(),
            "outcome_blind": True,
            "performance_data_loaded": False,
        }
        pit_source_detail = self._pit.provenance() if self._pit is not None else {"mode": "NOT_REQUIRED"}
        result = CandidateSampleFeasibilityInputV1(
            candidate_id=str(payload.get("candidate_id", "")),
            candidate_hash=candidate_hash,
            family=family,
            mechanism=str(payload.get("mechanism") or family),
            candidate_type=str(payload.get("candidate_type") or family),
            research_start=start,
            research_end=end,
            holding_horizon=holding if holding > 0 else None,
            observation_store=store,
            calendar_sessions=calendar,
            top_n=int(top_n) if top_n is not None else None,
            max_positions=max_positions,
            base_cash=float(sample.get("base_cash", policy.initial_cash) or policy.initial_cash),
            base_lot_size=int(sample.get("base_lot_size", payload.get("lot_size", policy.lot_size)) or policy.lot_size),
            small_capital_cash=float(sample.get("small_capital_cash", policy.small_capital_cash) or policy.small_capital_cash),
            small_capital_lot_size=int(sample.get("small_capital_lot_size", policy.small_capital_lot_size) or policy.small_capital_lot_size),
            data_provenance=provenance,
            pit_provenance={"dataset_version": PIT_DATASET_VERSION, "source": str(pit_source_detail.get("root", "")), "source_detail": pit_source_detail, "unknown_state_fail_closed": True, "calendar_identity": stable_hash(calendar)},
            calendar_identity=stable_hash(calendar),
            backend_type="REAL_FACTORY",
            generated_at=generated_at,
        )
        PerformanceBlindGuard.assert_blind(result.to_dict())
        return result

    @staticmethod
    def _assign_ranks(rows: list[dict[str, Any]], candidate: Mapping[str, Any]) -> None:
        bindings = [item for item in candidate.get("factor_bindings", ()) if isinstance(item, Mapping)]
        primary = [item for item in bindings if item.get("role") == "PRIMARY_ALPHA"] or bindings[:1]
        confirmations = [item for item in bindings if item.get("role") == "CONFIRMATION"]
        signal_logic = candidate.get("signal_logic") if isinstance(candidate.get("signal_logic"), Mapping) else {}
        ranking_id = str(signal_logic.get("ranking_factor_id") or (primary[0].get("factor_id") if primary else ""))
        ranking_rule = candidate.get("ranking_rule") if isinstance(candidate.get("ranking_rule"), Mapping) else {}
        composite_components = [
            dict(item)
            for item in ranking_rule.get("components", ())
            if isinstance(item, Mapping) and item.get("factor_id")
        ]
        composite_percentile = (
            str(ranking_rule.get("type") or "").upper() == "COMPOSITE_PERCENTILE"
            and str(ranking_rule.get("weighting") or "").upper() == "EQUAL"
            and bool(composite_components)
        )
        for day in sorted({row.get("signal_date") for row in rows}):
            group = [row for row in rows if row.get("signal_date") == day and row.get("signal_qualified") is True]
            if composite_percentile:
                # The frozen contract uses equal-weighted cross-sectional
                # percentiles.  Use stable ordinal percentiles with symbol as
                # the deterministic tie-breaker: best=1, worst=0.  This is
                # outcome-blind and depends only on same-session factor values.
                component_scores: dict[str, dict[str, float]] = {}
                group_size = len(group)
                for component in composite_components:
                    factor_id = str(component["factor_id"])
                    direction = str(component.get("direction") or "DESC").upper()
                    eligible = [
                        row for row in group
                        if _finite(row.get("factor_values", {}).get(factor_id))
                    ]
                    reverse = direction in {"DESC", "DESCENDING", "POSITIVE"}
                    eligible.sort(
                        key=lambda row: (
                            -float(row.get("factor_values", {}).get(factor_id))
                            if reverse
                            else float(row.get("factor_values", {}).get(factor_id)),
                            str(row.get("symbol", "")),
                        )
                    )
                    denominator = max(1, len(eligible) - 1)
                    component_scores[factor_id] = {
                        str(row.get("signal_id") or f"{row.get('signal_date')}:{row.get('symbol')}"): (
                            1.0 if len(eligible) <= 1 else 1.0 - (index / denominator)
                        )
                        for index, row in enumerate(eligible)
                    }

                def composite_score(row: Mapping[str, Any]) -> float:
                    row_id = str(row.get("signal_id") or f"{row.get('signal_date')}:{row.get('symbol')}")
                    scores = [
                        component_scores.get(str(component["factor_id"]), {}).get(row_id)
                        for component in composite_components
                    ]
                    if any(score is None for score in scores):
                        return float("nan")
                    return float(np.mean([float(score) for score in scores]))

                group.sort(
                    key=lambda row: (
                        -(composite_score(row) if _finite(composite_score(row)) else -np.inf),
                        str(row.get("symbol", "")),
                    )
                )
                for rank, row in enumerate(group, 1):
                    row["rank"] = rank
                continue

            def score(row: Mapping[str, Any], items: list[Mapping[str, Any]], fallback: str = "") -> float:
                selected = items or ([{"factor_id": fallback, "direction": "POSITIVE"}] if fallback else [])
                values = []
                for item in selected:
                    value = row.get("factor_values", {}).get(str(item.get("factor_id")))
                    if not _finite(value):
                        return float("nan")
                    values.append(-float(value) if str(item.get("direction", "POSITIVE")) == "NEGATIVE" else float(value))
                return float(np.mean(values)) if values else 0.0
            group.sort(key=lambda row: (-(score(row, primary, ranking_id) if _finite(score(row, primary, ranking_id)) else -np.inf), -(score(row, confirmations) if _finite(score(row, confirmations)) else -np.inf), str(row.get("symbol", ""))))
            for rank, row in enumerate(group, 1):
                row["rank"] = rank


__all__ = ["BENCHMARK_CACHE_CONTRACT_VERSION", "BenchmarkCoverageError", "EXECUTION_CONTRACT_VERSION", "LHB_EVENT_SEMANTICS_VERSION", "PIT_DATASET_VERSION", "PROVIDER_VERSION", "RealSampleFeasibilityProviderV1"]
