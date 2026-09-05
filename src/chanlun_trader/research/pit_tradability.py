"""PIT security state and tradability contracts for Phase 4.1.

This module deliberately keeps provider evidence, normalized state, and universe
decisions separate.  A missing bar or missing provider row is never converted to
``SUSPENDED`` or ``TRADING`` implicitly.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from chanlun_trader.research.guard import FINAL_TEST_START, RESEARCH_END


RAW_DATASET_VERSION = "BAOSTOCK_PIT_SECURITY_STATE_RAW_V1"
NORMALIZED_DATASET_VERSION = "PIT_UNIVERSE_DATASET_V2"
HISTORY_FIELDS = ("date", "code", "tradestatus", "isST")
ALL_STOCK_FIELDS = ("code", "tradeStatus", "code_name")
ST_NORMAL = "NORMAL"
ST_SPECIAL = "ST"
ST_UNKNOWN = "UNKNOWN"
TRADING = "TRADING"
SUSPENDED = "SUSPENDED"
STATUS_UNKNOWN = "UNKNOWN"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def normalize_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    if "." in text:
        left, right = text.split(".", 1)
        if left in {"SH", "SZ"} and len(right) == 6 and right.isdigit():
            return f"{right}.{left}"
        code, market = left, right
        if len(code) == 6 and code.isdigit() and market in {"SH", "SZ"}:
            return f"{code}.{market}"
    if text.startswith(("SH", "SZ")) and len(text) == 8 and text[2:].isdigit():
        return f"{text[2:]}.{text[:2]}"
    if len(text) == 6 and text.isdigit():
        return f"{text}.{('SH' if text.startswith(('5', '6', '9')) else 'SZ')}"
    raise ValueError(f"invalid BaoStock A-share symbol: {value}")


def source_symbol(symbol: str) -> str:
    code, market = normalize_symbol(symbol).split(".")
    return f"{market.lower()}.{code}"


def parse_day(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    return date.fromisoformat(text[:10])


def day_text(value: Any) -> str | None:
    parsed = parse_day(value)
    return parsed.isoformat() if parsed else None


def map_is_st(value: Any) -> str:
    text = str(value or "").strip()
    if text == "0":
        return ST_NORMAL
    if text == "1":
        return ST_SPECIAL
    return ST_UNKNOWN


def map_trade_status(value: Any) -> str:
    text = str(value or "").strip()
    if text == "1":
        return TRADING
    if text == "0":
        return SUSPENDED
    return STATUS_UNKNOWN


def board_for_symbol(symbol: str) -> str:
    code, market = normalize_symbol(symbol).split(".")
    if market == "SH" and code.startswith("68"):
        return "STAR"
    if market == "SZ" and code.startswith("3"):
        return "GEM"
    return f"{market}_MAIN"


def canonical_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(payload: Any) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_research_range(start: date, end: date) -> None:
    if start > end:
        raise ValueError("start date must not be after end date")
    end_code = int(end.strftime("%Y%m%d"))
    research_end = date.fromisoformat(str(RESEARCH_END)) if isinstance(RESEARCH_END, str) else date(
        RESEARCH_END // 10000, (RESEARCH_END // 100) % 100, RESEARCH_END % 100
    )
    if end_code >= FINAL_TEST_START or end > research_end:
        raise ValueError(
            f"PIT security-state acquisition cannot access Final Test: end={end.isoformat()} "
            f"research_end={RESEARCH_END} final_test_start={FINAL_TEST_START}"
        )


def next_available_at(trade_date: date, calendar: list[date]) -> str:
    """Conservative daily-state availability: next known session open."""
    for candidate in calendar:
        if candidate > trade_date:
            return datetime.combine(candidate, time(9, 30), tzinfo=SHANGHAI).isoformat()
    return datetime.combine(trade_date, time(15, 0), tzinfo=SHANGHAI).isoformat()


def _json_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _raw_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows", [])
    return [dict(row) for row in rows if isinstance(row, dict)]


def load_history_payloads(raw_root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    history_root = raw_root / "history"
    for path in sorted(history_root.glob("symbol=*.json")):
        payload = _json_load(path)
        raw_symbol = payload.get("symbol") or path.stem.removeprefix("symbol=").replace("_", ".")
        symbol = normalize_symbol(raw_symbol)
        result[symbol] = payload
    return result


def load_all_stock_payloads(raw_root: Path) -> dict[date, dict[str, dict[str, Any]]]:
    result: dict[date, dict[str, dict[str, Any]]] = {}
    all_stock_root = raw_root / "all_stock"
    for path in sorted(all_stock_root.glob("trade_date=*.json")):
        payload = _json_load(path)
        trade_date = parse_day(payload.get("trade_date"))
        if not trade_date:
            continue
        result[trade_date] = {
            normalize_symbol(row["code"]): row
            for row in _raw_rows(payload)
            if row.get("code") and _safe_symbol(row.get("code"))
        }
    return result


def _safe_symbol(value: Any) -> str | None:
    try:
        return normalize_symbol(value)
    except ValueError:
        return None


def load_calendar(raw_root: Path) -> list[date]:
    payload = _json_load(raw_root / "trade_calendar.json")
    return sorted(day for day in (parse_day(x) for x in payload.get("trade_dates", [])) if day)


def _active(member: dict[str, Any], trade_date: date) -> bool:
    list_date = parse_day(member.get("ipoDate"))
    out_date = parse_day(member.get("outDate"))
    return bool(list_date and list_date <= trade_date and (out_date is None or trade_date <= out_date))


def _record_time(trade_date: date) -> str:
    return datetime.combine(trade_date, time(15, 0), tzinfo=SHANGHAI).isoformat()


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _raw_history_map(payload: dict[str, Any]) -> dict[date, dict[str, Any]]:
    result: dict[date, dict[str, Any]] = {}
    for row in _raw_rows(payload):
        trade_date = parse_day(row.get("date"))
        if trade_date:
            result[trade_date] = row
    return result


def build_normalized_state(
    raw_root: str | Path,
    output_root: str | Path,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Build deterministic ST/suspension/master/universe stores from raw evidence."""
    ensure_research_range(start_date, end_date)
    raw_root = Path(raw_root)
    output_root = Path(output_root)
    calendar = [day for day in load_calendar(raw_root) if start_date <= day <= end_date]
    if not calendar:
        raise ValueError("raw trade calendar has no dates in requested range")
    basic_payload = _json_load(raw_root / "stock_basic.json")
    basic_rows = [row for row in _raw_rows(basic_payload) if str(row.get("type", "")) == "1"]
    basics: dict[str, dict[str, Any]] = {}
    for row in basic_rows:
        symbol = _safe_symbol(row.get("code"))
        if symbol:
            basics[symbol] = row
    history = load_history_payloads(raw_root)
    all_stock = load_all_stock_payloads(raw_root)

    master_rows: list[dict[str, Any]] = []
    for symbol in sorted(basics):
        row = basics[symbol]
        master_rows.append({
            "symbol": symbol,
            "exchange": symbol.split(".")[1],
            "security_type": "A_SHARE_STOCK",
            "board": board_for_symbol(symbol),
            "list_date": day_text(row.get("ipoDate")),
            "delist_date": day_text(row.get("outDate")),
            "first_known_at": basic_payload.get("fetched_at"),
            "last_known_at": basic_payload.get("fetched_at"),
            "source": "baostock.query_stock_basic",
            "source_record_time": basic_payload.get("fetched_at"),
            "evidence_quality": "LIFECYCLE_DATES_ONLY_CURRENT_STATUS_NOT_BACKFILLED",
            "current_status_retained_only_as_raw": row.get("status"),
            "version": "PIT_SECURITY_MASTER_V2",
        })
    master_by_symbol = {row["symbol"]: row for row in master_rows}

    history_by_symbol = {symbol: _raw_history_map(payload) for symbol, payload in history.items()}
    conflicts: list[dict[str, Any]] = []
    coverage = {
        "active_symbol_days": 0,
        "st_known_symbol_days": 0,
        "st_unknown_symbol_days": 0,
        "suspension_known_symbol_days": 0,
        "suspension_unknown_symbol_days": 0,
        "universe_resolved_symbol_days": 0,
        "universe_unknown_symbol_days": 0,
        "source_conflict_count": 0,
        "by_year": {},
        "by_exchange": {},
        "by_board": {},
    }

    st_by_symbol: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in master_by_symbol}
    suspension_by_symbol: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in master_by_symbol}
    universe_by_date: dict[date, list[dict[str, Any]]] = {day: [] for day in calendar}

    for trade_date in calendar:
        population = all_stock.get(trade_date)
        population_available = population is not None
        for symbol, master in master_by_symbol.items():
            list_date = parse_day(master.get("list_date"))
            delist_date = parse_day(master.get("delist_date"))
            listed = bool(list_date and trade_date >= list_date)
            delisted = bool(delist_date and trade_date > delist_date)
            if not listed or delisted:
                universe_by_date[trade_date].append({
                    "trade_date": trade_date.isoformat(),
                    "symbol": symbol,
                    "exists": False,
                    "listed": listed,
                    "delisted": delisted,
                    "security_type": master["security_type"],
                    "exchange": master["exchange"],
                    "board": master["board"],
                    "listing_age": (trade_date - list_date).days if listed and list_date else None,
                    "st_status": ST_UNKNOWN,
                    "suspension_status": STATUS_UNKNOWN,
                    "universe_member": False,
                    "tradability_status": STATUS_UNKNOWN,
                    "eligibility_status": "INELIGIBLE",
                    "reason_codes": ["NOT_LISTED" if not listed else "DELISTED"],
                    "available_at": next_available_at(trade_date, calendar),
                    "source_lineage": {"security_master": "baostock.query_stock_basic"},
                })
                continue
            coverage["active_symbol_days"] += 1
            year_key = str(trade_date.year)
            for group, key in ((coverage["by_year"], year_key),
                               (coverage["by_exchange"], master["exchange"]),
                               (coverage["by_board"], master["board"])):
                bucket = group.setdefault(key, {"active": 0, "st_unknown": 0, "suspension_unknown": 0, "universe_unknown": 0})
                bucket["active"] += 1

            history_row = history_by_symbol.get(symbol, {}).get(trade_date)
            population_row = population.get(symbol) if population_available else None
            st_status = map_is_st(history_row.get("isST") if history_row else None)
            history_trade_status = map_trade_status(history_row.get("tradestatus") if history_row else None)
            population_trade_status = map_trade_status(population_row.get("tradeStatus") if population_row else None)
            final_trade_status = history_trade_status
            suspension_source = "baostock.query_history_k_data_plus"
            if history_trade_status == STATUS_UNKNOWN and population_trade_status != STATUS_UNKNOWN:
                final_trade_status = population_trade_status
                suspension_source = "baostock.query_all_stock"
            elif history_trade_status != STATUS_UNKNOWN and population_trade_status != STATUS_UNKNOWN and history_trade_status != population_trade_status:
                conflicts.append({
                    "symbol": symbol,
                    "trade_date": trade_date.isoformat(),
                    "field": "tradestatus/tradeStatus",
                    "source_a": "baostock.query_history_k_data_plus",
                    "value_a": history_row.get("tradestatus") if history_row else None,
                    "source_b": "baostock.query_all_stock",
                    "value_b": population_row.get("tradeStatus") if population_row else None,
                    "evidence": {"history_row": history_row, "all_stock_row": population_row},
                    "resolution_rule": "CONFLICT_FAIL_CLOSED",
                    "final_status": STATUS_UNKNOWN,
                })
                final_trade_status = STATUS_UNKNOWN
                suspension_source = "SecurityStateConflictStore"
            if final_trade_status == STATUS_UNKNOWN:
                coverage["suspension_unknown_symbol_days"] += 1
                coverage["by_year"][year_key]["suspension_unknown"] += 1
                coverage["by_exchange"][master["exchange"]]["suspension_unknown"] += 1
                coverage["by_board"][master["board"]]["suspension_unknown"] += 1
            else:
                coverage["suspension_known_symbol_days"] += 1

            if st_status == ST_UNKNOWN:
                coverage["st_unknown_symbol_days"] += 1
                coverage["by_year"][year_key]["st_unknown"] += 1
                coverage["by_exchange"][master["exchange"]]["st_unknown"] += 1
                coverage["by_board"][master["board"]]["st_unknown"] += 1
            else:
                coverage["st_known_symbol_days"] += 1

            membership = True if population_row is not None else (None if not population_available else False)
            lifecycle_exists = True
            universe_status = "UNKNOWN"
            reasons: list[str] = []
            if membership is None:
                reasons.append("DAILY_POPULATION_MISSING")
            elif membership is False:
                reasons.append("SYMBOL_NOT_IN_DAILY_POPULATION")
            if membership is True and st_status == ST_NORMAL and final_trade_status == TRADING:
                universe_status = "ELIGIBLE"
            elif membership is False or st_status == ST_SPECIAL or final_trade_status == SUSPENDED:
                universe_status = "INELIGIBLE"
            else:
                reasons.extend(x for x in ("ST_STATUS_UNKNOWN" if st_status == ST_UNKNOWN else None,
                                           "SUSPENSION_STATUS_UNKNOWN" if final_trade_status == STATUS_UNKNOWN else None) if x)
            if universe_status == "UNKNOWN":
                coverage["universe_unknown_symbol_days"] += 1
                coverage["by_year"][year_key]["universe_unknown"] += 1
                coverage["by_exchange"][master["exchange"]]["universe_unknown"] += 1
                coverage["by_board"][master["board"]]["universe_unknown"] += 1
            else:
                coverage["universe_resolved_symbol_days"] += 1

            known_at = _record_time(trade_date)
            available_at = next_available_at(trade_date, calendar)
            st_by_symbol[symbol].append({
                "symbol": symbol,
                "effective_from": trade_date.isoformat(),
                "effective_to": trade_date.isoformat(),
                "status": st_status,
                "name_at_time": None,
                "known_at": known_at,
                "available_at": available_at,
                "source": "baostock.query_history_k_data_plus" if history_row else "UNKNOWN",
                "source_evidence": {"row": history_row, "fields": list(HISTORY_FIELDS)},
                "confidence": "EXPLICIT_DAILY_FIELD" if st_status != ST_UNKNOWN else "UNKNOWN",
                "version": "PIT_ST_STATE_CONTRACT_V1",
            })
            suspension_by_symbol[symbol].append({
                "symbol": symbol,
                "trade_date": trade_date.isoformat(),
                "status": final_trade_status,
                "effective_at": known_at,
                "known_at": known_at,
                "available_at": available_at,
                "source": suspension_source,
                "evidence": {"history_row": history_row, "all_stock_row": population_row},
                "confidence": "EXPLICIT_DAILY_FIELD" if final_trade_status != STATUS_UNKNOWN else "UNKNOWN",
                "version": "PIT_SUSPENSION_STATE_CONTRACT_V1",
            })
            universe_by_date[trade_date].append({
                "trade_date": trade_date.isoformat(),
                "symbol": symbol,
                "exists": lifecycle_exists,
                "listed": True,
                "delisted": False,
                "security_type": master["security_type"],
                "exchange": master["exchange"],
                "board": master["board"],
                "listing_age": (trade_date - parse_day(master["list_date"])).days if parse_day(master["list_date"]) else None,
                "st_status": st_status,
                "suspension_status": final_trade_status,
                "universe_member": membership,
                "tradability_status": final_trade_status,
                "eligibility_status": universe_status,
                "reason_codes": sorted(set(reasons)),
                "available_at": available_at,
                "source_lineage": {
                    "history": history.get(symbol, {}).get("source"),
                    "all_stock": "baostock.query_all_stock" if population_available else None,
                    "security_master": "baostock.query_stock_basic",
                },
            })

    for symbol in sorted(master_by_symbol):
        safe = symbol.replace(".", "_")
        _write_jsonl(output_root / "st_state" / f"symbol={safe}.jsonl", st_by_symbol[symbol])
        _write_jsonl(output_root / "suspension_state" / f"symbol={safe}.jsonl", suspension_by_symbol[symbol])
    for trade_date in calendar:
        _write_jsonl(output_root / "pit_universe_v2" / f"trade_date={trade_date.isoformat()}.jsonl", universe_by_date[trade_date])
    _write_jsonl(output_root / "conflicts" / "security_state_conflicts.jsonl", conflicts)
    (output_root / "security_master_v2").mkdir(parents=True, exist_ok=True)
    (output_root / "security_master_v2" / "records.json").write_bytes(canonical_bytes(master_rows))

    coverage["source_conflict_count"] = len(conflicts)
    for name in ("st", "suspension", "universe"):
        known_key = {"st": "st_known_symbol_days", "suspension": "suspension_known_symbol_days", "universe": "universe_resolved_symbol_days"}[name]
        unknown_key = {"st": "st_unknown_symbol_days", "suspension": "suspension_unknown_symbol_days", "universe": "universe_unknown_symbol_days"}[name]
        total = coverage[known_key] + coverage[unknown_key]
        coverage[f"{name}_coverage_ratio"] = round(coverage[known_key] / total, 8) if total else 0.0
    manifest = {
        "dataset_version": NORMALIZED_DATASET_VERSION,
        "raw_dataset_version": RAW_DATASET_VERSION,
        "coverage_start": calendar[0].isoformat(),
        "coverage_end": calendar[-1].isoformat(),
        "trade_day_count": len(calendar),
        "security_count": len(master_rows),
        "source_versions": {"baostock": basic_payload.get("provider_version")},
        "source_checksums": {
            "stock_basic": basic_payload.get("checksum"),
            "raw_root_listing": sha256_bytes(sorted(str(path.relative_to(raw_root)) for path in raw_root.rglob("*.json"))),
        },
        "coverage": coverage,
        "pit_policy": {
            "current_status_backfill_used": "NO",
            "missing_bar_auto_suspension_used": "NO",
            "conflict_resolution": "UNKNOWN_FAIL_CLOSED",
            "available_at_rule": "NEXT_SESSION_OPEN",
        },
        "code_hash": sha256_file(Path(__file__)),
        "build_timestamp": datetime.now().astimezone().isoformat(),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "manifest.json").write_bytes(canonical_bytes(manifest))
    return manifest


class PITStateStore:
    """Read API over the normalized PIT state and universe stores."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._universe_cache: dict[date, dict[str, dict[str, Any]]] = {}

    def get_st_status(self, symbol: str, trade_date: date | str, as_of: datetime | None = None) -> str:
        day = parse_day(trade_date)
        if day is None:
            return ST_UNKNOWN
        path = self.root / "st_state" / f"symbol={normalize_symbol(symbol).replace('.', '_')}.jsonl"
        return _lookup(path, day, as_of, "status", "effective_from") or ST_UNKNOWN

    def get_trading_status(self, symbol: str, trade_date: date | str, as_of: datetime | None = None) -> str:
        day = parse_day(trade_date)
        if day is None:
            return STATUS_UNKNOWN
        path = self.root / "suspension_state" / f"symbol={normalize_symbol(symbol).replace('.', '_')}.jsonl"
        return _lookup(path, day, as_of, "status", "trade_date") or STATUS_UNKNOWN

    def get_universe_rows(self, trade_date: date | str) -> dict[str, dict[str, Any]] | None:
        """Return the canonical PIT universe snapshot for one trade date.

        ``None`` means the snapshot file is missing.  An empty mapping is a
        valid snapshot only when the canonical file exists and contains no
        rows; callers must not interpret a missing file as an empty universe.
        """
        day = parse_day(trade_date)
        if day is None:
            return None
        if day in self._universe_cache:
            return {symbol: dict(row) for symbol, row in self._universe_cache[day].items()}
        path = self.root / "pit_universe_v2" / f"trade_date={day.isoformat()}.jsonl"
        if not path.exists():
            return None
        rows: dict[str, dict[str, Any]] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            symbol = _safe_symbol(row.get("symbol"))
            if symbol:
                rows[symbol] = dict(row)
        self._universe_cache[day] = rows
        return {symbol: dict(row) for symbol, row in rows.items()}

    def get_universe_row(self, symbol: str, trade_date: date | str) -> dict[str, Any] | None:
        """Return one canonical PIT universe row, preserving UNKNOWN fields."""
        normalized = normalize_symbol(symbol)
        rows = self.get_universe_rows(trade_date)
        if rows is None:
            return None
        row = rows.get(normalized)
        return dict(row) if row is not None else None

    def get_universe_members(self, trade_date: date | str) -> list[str] | None:
        """Return only rows explicitly eligible in the canonical snapshot."""
        rows = self.get_universe_rows(trade_date)
        if rows is None:
            return None
        return sorted(
            symbol for symbol, row in rows.items()
            if row.get("eligibility_status") == "ELIGIBLE"
            and row.get("universe_member") is True
            and row.get("st_status") == ST_NORMAL
            and row.get("tradability_status") == TRADING
        )


def _lookup(path: Path, day: date, as_of: datetime | None, value_key: str, day_key: str) -> str | None:
    if not path.exists():
        return None
    selected: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if parse_day(row.get(day_key)) != day:
            continue
        if as_of is not None:
            available = pd.Timestamp(row.get("available_at"))
            if available.to_pydatetime() > as_of:
                continue
        selected = row
    return str(selected.get(value_key)) if selected else None
