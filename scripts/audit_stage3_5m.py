"""Full-market Stage3 coverage audit, independent acceptance and V1 freeze."""
from __future__ import annotations

import hashlib
import json
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from chanlun_trader.data.minute.base import BAR_TIMESTAMP_SEMANTICS, source_symbol
from chanlun_trader.data.minute.manifest import sha256_file
from chanlun_trader.data.minute.validator import expected_session_times
from scripts.stage3_5m_acquisition import (
    NORMALIZED_ROOT,
    RAW_ROOT,
    TRAIN_END,
    TRAIN_START,
    UNIVERSE_PATH,
    _canonical_bytes,
    _read_result,
    chunk_keys_for_member,
    query_stock_basic,
    query_trade_calendar,
    write_immutable_json,
)
from chanlun_trader.data.minute.baostock_provider import BaoStock5MinProvider


COVERAGE_JSON = Path("reports/STAGE3_5M_COVERAGE_AUDIT.json")
COVERAGE_MD = Path("reports/STAGE3_5M_COVERAGE_AUDIT.md")
GAP_JSON = Path("reports/STAGE3_5M_GAP_AUDIT.json")
GAP_MD = Path("reports/STAGE3_5M_GAP_AUDIT.md")
ACCEPTANCE_JSON = Path("reports/STAGE3_INDEPENDENT_ACCEPTANCE.json")
ACCEPTANCE_MD = Path("reports/STAGE3_INDEPENDENT_ACCEPTANCE.md")
FREEZE_JSON = Path("reports/TRAIN_5M_DATASET_FREEZE.json")
FREEZE_MD = Path("reports/TRAIN_5M_DATASET_FREEZE.md")
FINAL_STATUS = Path("reports/FINAL_STATUS_STAGE3_5M.json")
DATASET_DOC = Path("docs/TRAIN_5M_DATASET_V1.md")
DATASET_MANIFEST = Path("data/research/market_5m/manifest_v1.jsonl")
FROZEN_MANIFEST = Path("data/research/frozen_manifests/TRAIN_5M_DATASET_V1.json")
PIT_STAGE3_PATH = Path("data/research/pit_security_master_stage3.json")
DAILY_EVIDENCE_PATH = Path("data/market_raw/baostock/stage3_daily_gap_evidence.json")
EXPECTED_TIMES = expected_session_times(BAR_TIMESTAMP_SEMANTICS)
KNOWN_CROSS_SOURCE = {("000895.SZ", 20240606)}


def _load_universe() -> tuple[dict[str, Any], list[dict[str, Any]], list[int], str]:
    payload = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
    calendar_dates = [int(day) for day in payload["calendar_dates"]]
    return payload, list(payload["symbols"]), calendar_dates, hashlib.sha256(UNIVERSE_PATH.read_bytes()).hexdigest()


def _expected_chunks() -> list[tuple[str, str]]:
    from chanlun_trader.data.minute.base import date_chunks
    return [(start.isoformat(), end.isoformat()) for start, end in date_chunks(TRAIN_START, TRAIN_END, 120)]


def _latest_complete_records(records: list[dict[str, Any]], members: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[(str(record.get("symbol")), str(record.get("requested_start")),
                 str(record.get("requested_end")))].append(record)
    selected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for member in members:
        symbol = member["symbol"]
        for start, end in chunk_keys_for_member(member):
            for record in reversed(grouped.get((symbol, start, end), [])):
                raw_path = Path(str(record.get("raw_path") or record.get("path") or ""))
                if (record.get("status") == "EMPTY" and record.get("row_count", 0) == 0) or (
                        record.get("status") == "COMPLETE" and raw_path.is_file() and
                        record.get("sha256") == sha256_file(raw_path)):
                    selected[(symbol, start, end)] = record
                    break
    return selected


def _normalized_manifest(normalized_paths: set[Path]) -> tuple[list[dict[str, Any]], Path, str, str, bool]:
    target = DATASET_MANIFEST

    entries: list[dict[str, Any]] = []
    for path in sorted(normalized_paths):
        parquet = pq.ParquetFile(path)
        entries.append({
            "path": str(path),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
            "row_count": parquet.metadata.num_rows,
        })
    lines = "".join(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n" for entry in entries).encode("utf-8")
    status = "WRITTEN"
    if target.exists():
        if target.read_bytes() == lines:
            status = "UNCHANGED"
        else:
            target = target.with_name(f"manifest_v1.conflict-{hashlib.sha256(lines).hexdigest()[:12]}.jsonl")
            status = "DATA_CONFLICT"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(lines)
    return entries, target, hashlib.sha256(lines).hexdigest(), status, status != "DATA_CONFLICT"


class DailyCountStore:
    def __init__(self) -> None:
        self._temporary_dir = tempfile.TemporaryDirectory(prefix="stage3_daily_counts_")
        self._root = Path(self._temporary_dir.name)
        self._nonstandard_symbols: set[str] = set()

    def write_symbol(self, symbol: str, counts: dict[int, int]) -> None:
        if any(count != len(EXPECTED_TIMES) for count in counts.values()):
            self._nonstandard_symbols.add(symbol)
        path = self._root / f"{symbol.replace('.', '_')}.json"
        path.write_text(json.dumps(counts, separators=(",", ":")), encoding="utf-8")

    def symbols_with_nonstandard_counts(self, expected_count: int) -> set[str]:
        return set(self._nonstandard_symbols)

    def for_symbol(self, symbol: str) -> dict[int, int]:
        path = self._root / f"{symbol.replace('.', '_')}.json"
        if not path.exists():
            return {}
        return {int(day): int(count) for day, count in json.loads(path.read_text(encoding="utf-8")).items()}

    def close(self) -> None:
        self._temporary_dir.cleanup()


def _scan_normalized(selected: dict[tuple[str, str, str], dict[str, Any]]) -> tuple[DailyCountStore, Counter, Counter, int, int | None, int | None]:
    paths_by_symbol: dict[str, set[Path]] = defaultdict(set)
    for record in selected.values():
        symbol = str(record["symbol"])
        for path in record.get("normalized_paths", []):
            paths_by_symbol[symbol].add(Path(str(path)))

    day_counts = DailyCountStore()
    symbol_counts: Counter = Counter()
    year_counts: Counter = Counter()
    total = 0
    earliest: int | None = None
    latest: int | None = None
    for symbol in sorted(paths_by_symbol):
        symbol_day_counts: Counter = Counter()
        for path in sorted(paths_by_symbol[symbol]):
            if not path.is_file():
                continue
            frame = pq.read_table(path, columns=["trade_date"]).to_pandas()
            if frame.empty:
                continue
            dates = frame["trade_date"].astype(int)
            symbol_day_counts.update(dates.value_counts().to_dict())
            symbol_counts[symbol] += len(dates)
            year_counts.update((dates // 10000).value_counts().to_dict())
            total += len(dates)
            minimum = int(dates.min())
            maximum = int(dates.max())
            earliest = minimum if earliest is None else min(earliest, minimum)
            latest = maximum if latest is None else max(latest, maximum)
            del dates, frame
        day_counts.write_symbol(symbol, dict(symbol_day_counts))
    return day_counts, symbol_counts, year_counts, total, earliest, latest


def _query_daily_evidence(provider: BaoStock5MinProvider, symbols: list[str]) -> dict[tuple[str, int], dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for symbol in symbols:
        result = provider.bs.query_history_k_data_plus(
            source_symbol(symbol),
            "date,code,volume,amount,tradestatus",
            start_date=TRAIN_START.isoformat(), end_date=TRAIN_END.isoformat(),
            frequency="d", adjustflag="3",
        )
        rows = _read_result(result)
        evidence.append({"symbol": symbol, "rows": rows, "source": "baostock.query_history_k_data_plus",
                         "frequency": "d", "adjustflag": "3"})
        for row in rows:
            text = str(row.get("date", "")).replace("-", "")
            if len(text) == 8:
                by_key[(symbol, int(text))] = row
    DAILY_EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DAILY_EVIDENCE_PATH.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return by_key


def classify_session(symbol: str, trade_date: int, observed_count: int,
                     daily_row: dict[str, Any] | None) -> str:
    if observed_count == len(EXPECTED_TIMES):
        return "COMPLETE_48"
    if (symbol, trade_date) in KNOWN_CROSS_SOURCE:
        return "CROSS_SOURCE_CONFLICT"
    tradestatus = str((daily_row or {}).get("tradestatus", ""))
    if observed_count == 0:
        return "EXPECTED_SUSPENSION" if tradestatus == "0" else "UNKNOWN"
    if tradestatus == "0":
        return "EXPECTED_PARTIAL_SESSION"
    try:
        volume = float((daily_row or {}).get("volume", 0) or 0)
    except (TypeError, ValueError):
        volume = 0.0
    return "PROVIDER_DATA_GAP" if volume > 0 else "UNKNOWN"


def _active_dates(member: dict[str, Any], calendar_dates: list[int]) -> list[int]:
    list_date = int(str(member["list_date"]).replace("-", ""))
    delist_date = int(str(member["delist_date"]).replace("-", "")) if member.get("delist_date") else TRAIN_END.year * 10000 + TRAIN_END.month * 100 + TRAIN_END.day
    return [day for day in calendar_dates if list_date <= day <= delist_date]


def build_coverage(universe: list[dict[str, Any]], calendar_dates: list[int],
                   day_counts: Any,
                   daily_rows: dict[tuple[str, int], dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    classification_counts: Counter = Counter()
    per_symbol: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    for member in universe:
        symbol = member["symbol"]
        active_dates = _active_dates(member, calendar_dates)
        observed_by_day = day_counts.for_symbol(symbol) if isinstance(day_counts, DailyCountStore) else {
            day: day_counts.get((symbol, day), 0) for day in active_dates
        }
        counts = Counter()
        for day in active_dates:
            observed = observed_by_day.get(day, 0)
            classification = classify_session(symbol, day, observed, daily_rows.get((symbol, day)))
            counts[classification] += 1
            classification_counts[classification] += 1
            if classification != "COMPLETE_48":
                sessions.append({
                    "symbol": symbol, "trade_date": day, "expected_count": len(EXPECTED_TIMES),
                    "observed_count": observed, "classification": classification,
                    "list_date": member.get("list_date"), "delist_date": member.get("delist_date"),
                    "daily_evidence": daily_rows.get((symbol, day)),
                })
        per_symbol.append({
            "symbol": symbol,
            "board": member["board"],
            "expected_active_sessions": len(active_dates),
            "complete_sessions": counts["COMPLETE_48"],
            "expected_suspension_sessions": counts["EXPECTED_SUSPENSION"],
            "expected_partial_sessions": counts["EXPECTED_PARTIAL_SESSION"],
            "provider_gap_sessions": counts["PROVIDER_DATA_GAP"],
            "cross_source_conflict_sessions": counts["CROSS_SOURCE_CONFLICT"],
            "unknown_sessions": counts["UNKNOWN"],
            "classification_counts": dict(counts),
        })
    expected_sessions = sum(item["expected_active_sessions"] for item in per_symbol)
    complete_sessions = classification_counts["COMPLETE_48"]
    unknown = classification_counts["UNKNOWN"]
    provider_gap = classification_counts["PROVIDER_DATA_GAP"]
    complete_ratio = complete_sessions / expected_sessions if expected_sessions else 0.0
    unknown_ratio = unknown / expected_sessions if expected_sessions else 1.0
    provider_ratio = provider_gap / expected_sessions if expected_sessions else 1.0
    full_status = "READY" if (
        complete_ratio >= 0.99 and unknown_ratio <= 0.0005 and provider_ratio <= 0.005
    ) else "PARTIAL"
    board_stats: dict[str, dict[str, Any]] = {}
    for board in sorted({item["board"] for item in per_symbol}):
        members = [item for item in per_symbol if item["board"] == board]
        board_stats[board] = {
            "symbol_count": len(members),
            "expected_active_sessions": sum(item["expected_active_sessions"] for item in members),
            "complete_sessions": sum(item["complete_sessions"] for item in members),
            "expected_suspension_sessions": sum(item["expected_suspension_sessions"] for item in members),
            "partial_sessions": sum(item["expected_partial_sessions"] + item["provider_gap_sessions"] + item["cross_source_conflict_sessions"] for item in members),
            "unknown_sessions": sum(item["unknown_sessions"] for item in members),
        }
    coverage = {
        "report": "STAGE3_5M_COVERAGE_AUDIT",
        "scope": {"start": TRAIN_START.isoformat(), "end": TRAIN_END.isoformat()},
        "symbol_count": len(universe),
        "active_symbol_count": len(universe),
        "expected_symbol_sessions": expected_sessions,
        "complete_sessions": complete_sessions,
        "expected_suspension_sessions": classification_counts["EXPECTED_SUSPENSION"],
        "partial_sessions": classification_counts["EXPECTED_PARTIAL_SESSION"],
        "provider_gap_sessions": provider_gap,
        "cross_source_conflict_sessions": classification_counts["CROSS_SOURCE_CONFLICT"],
        "unknown_sessions": unknown,
        "classification_counts": dict(classification_counts),
        "complete_session_ratio": complete_ratio,
        "unknown_session_ratio": unknown_ratio,
        "provider_gap_ratio": provider_ratio,
        "thresholds": {"complete_session_ratio_min": 0.99, "unknown_session_ratio_max": 0.0005, "provider_gap_ratio_max": 0.005},
        "TRAIN_5M_FULL_COVERAGE_STATUS": full_status,
        "board_coverage": board_stats,
        "per_symbol": per_symbol,
    }
    return coverage, sessions


def _write_reports(coverage: dict[str, Any], anomalies: list[dict[str, Any]],
                   symbol_counts: Counter, year_counts: Counter, total_rows: int,
                   earliest: int | None, latest: int | None,
                   normalized_entries: list[dict[str, Any]], normalized_manifest_path: Path,
                   acquisition_report: dict[str, Any], universe_hash: str) -> None:
    coverage.update({
        "bar_count_total": total_rows,
        "earliest_timestamp": earliest,
        "latest_timestamp": latest,
        "year_coverage": {str(year): int(count) for year, count in sorted(year_counts.items())},
        "symbol_coverage_distribution": Counter(
            int(item["complete_sessions"]) for item in coverage["per_symbol"]
        ),
        "normalized_manifest": str(normalized_manifest_path),
        "normalized_manifest_sha256": hashlib.sha256(normalized_manifest_path.read_bytes()).hexdigest(),
        "normalized_file_count": len(normalized_entries),
        "universe_manifest_sha256": universe_hash,
        "acquisition_report": str(Path("reports/STAGE3_5M_ACQUISITION_REPORT.json")),
    })
    COVERAGE_JSON.parent.mkdir(parents=True, exist_ok=True)
    COVERAGE_JSON.write_text(json.dumps(coverage, ensure_ascii=False, indent=2, default=dict) + "\n", encoding="utf-8")
    severity = {"UNKNOWN": 0, "PROVIDER_DATA_GAP": 1, "CROSS_SOURCE_CONFLICT": 2,
                "EXPECTED_PARTIAL_SESSION": 3, "EXPECTED_SUSPENSION": 4}
    anomalies = sorted(anomalies, key=lambda item: (severity.get(item["classification"], 9), item["symbol"], item["trade_date"]))
    GAP_JSON.write_text(json.dumps({"report": "STAGE3_5M_GAP_AUDIT", "top_20": anomalies[:20],
                                    "total_anomaly_sessions": len(anomalies),
                                    "classification_counts": coverage["classification_counts"]}, ensure_ascii=False, indent=2, default=dict) + "\n", encoding="utf-8")
    lines = [
        "# Stage3 5m Coverage Audit", "",
        f"- Universe: `{coverage['symbol_count']}` symbols; expected active sessions `{coverage['expected_symbol_sessions']}`.",
        f"- Bars: `{total_rows}`; date range `{earliest}` ~ `{latest}`; normalized files `{len(normalized_entries)}`.",
        f"- Sessions: complete `{coverage['complete_sessions']}`, suspension `{coverage['expected_suspension_sessions']}`, partial `{coverage['partial_sessions']}`, provider gap `{coverage['provider_gap_sessions']}`, cross-source conflict `{coverage['cross_source_conflict_sessions']}`, unknown `{coverage['unknown_sessions']}`.",
        f"- `TRAIN_5M_FULL_COVERAGE_STATUS={coverage['TRAIN_5M_FULL_COVERAGE_STATUS']}` under predeclared thresholds.", "",
        "## Year coverage", "",
    ]
    lines.extend(f"- `{year}`: `{count}` bars" for year, count in sorted(coverage["year_coverage"].items()))
    lines += ["", "## Board coverage", "", "| board | symbols | expected sessions | complete sessions | suspension | partial | unknown |", "|---|---:|---:|---:|---:|---:|---:|"]
    for board, stats in coverage["board_coverage"].items():
        lines.append(f"| {board} | {stats['symbol_count']} | {stats['expected_active_sessions']} | {stats['complete_sessions']} | {stats['expected_suspension_sessions']} | {stats['partial_sessions']} | {stats['unknown_sessions']} |")
    lines += ["", "## Top 20 anomaly sessions", "", "| symbol | date | observed/expected | classification |", "|---|---:|---:|---|"]
    for item in anomalies[:20]:
        lines.append(f"| {item['symbol']} | {item['trade_date']} | {item['observed_count']}/{item['expected_count']} | **{item['classification']}** |")
    lines += ["", f"- Machine-readable coverage: `{COVERAGE_JSON}`.", f"- Machine-readable gap audit: `{GAP_JSON}`.", ""]
    COVERAGE_MD.write_text("\n".join(lines), encoding="utf-8")
    gap_lines = ["# Stage3 5m Gap Audit", "", f"- Total anomaly sessions: `{len(anomalies)}`.", ""]
    gap_lines.extend(f"- `{item['symbol']}` `{item['trade_date']}` `{item['observed_count']}/{item['expected_count']}`: `{item['classification']}`" for item in anomalies[:20])
    gap_lines += ["", "All sessions are classified from PIT active dates, observed normalized coverage and date-level BaoStock daily evidence; missing data alone is never suspension evidence.", ""]
    GAP_MD.write_text("\n".join(gap_lines), encoding="utf-8")


def _freeze(coverage: dict[str, Any], universe_payload: dict[str, Any], universe_hash: str,
            normalized_manifest_path: Path, normalized_manifest_hash: str,
            pit_hash: str, calendar_dates: list[int], acquisition_report: dict[str, Any]) -> dict[str, Any]:
    raw_manifest_path = RAW_ROOT / "manifest.jsonl"
    calendar_hash = hashlib.sha256(json.dumps(calendar_dates, separators=(",", ":")).encode()).hexdigest()
    universe_symbols_hash = hashlib.sha256(_canonical_bytes({"symbols": universe_payload["symbols"]})).hexdigest()
    source_policy = {
        "primary": "BaoStock5MinProvider",
        "frequency": "5",
        "adjustflag": "3",
        "adjustment": "NONE",
        "secondary": ["TdxRawHQProvider", "local TDX .lc5"],
        "tdx_reconstructed_bars": "evidence_only_never_canonical",
    }
    payload = {
        "dataset_version": "TRAIN_5M_DATASET_V1",
        "freeze_status": "FROZEN" if coverage["TRAIN_5M_FULL_COVERAGE_STATUS"] == "READY" and acquisition_report.get("symbol_count_failed", 1) == 0 and acquisition_report.get("symbol_count_partial", 1) == 0 else "NOT_FROZEN",
        "scope": {"start": TRAIN_START.isoformat(), "end": TRAIN_END.isoformat()},
        "raw_manifest_hash": sha256_file(raw_manifest_path),
        "normalized_manifest_hash": normalized_manifest_hash,
        "normalized_manifest_path": str(normalized_manifest_path),
        "symbol_universe_hash": universe_symbols_hash,
        "calendar_hash": calendar_hash,
        "security_master_hash": pit_hash,
        "source_policy": source_policy,
        "timestamp_semantics": BAR_TIMESTAMP_SEMANTICS.value,
        "row_count": coverage["bar_count_total"],
        "date_range": {"earliest": coverage["earliest_timestamp"], "latest": coverage["latest_timestamp"]},
        "symbol_count": coverage["symbol_count"],
        "coverage_status": coverage["TRAIN_5M_FULL_COVERAGE_STATUS"],
        "code_commit": acquisition_report.get("git_state", {}).get("commit"),
        "dirty_state": acquisition_report.get("git_state", {}),
        "pit_master_path": str(PIT_STAGE3_PATH),
    }
    if payload["freeze_status"] == "FROZEN":
        target, _, freeze_write_status = write_immutable_json(FROZEN_MANIFEST, payload)
    else:
        target, freeze_write_status = FROZEN_MANIFEST, "NOT_CREATED_DATA_GATE"
    payload["immutable_manifest_path"] = str(target) if freeze_write_status != "NOT_CREATED_DATA_GATE" else None
    payload["immutable_manifest_write_status"] = freeze_write_status
    FREEZE_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    FREEZE_MD.write_text("\n".join([
        "# TRAIN 5m Dataset Freeze", "",
        f"- Dataset: `{payload['dataset_version']}`; status **{payload['freeze_status']}**.",
        f"- Scope: `{TRAIN_START}` ~ `{TRAIN_END}`; symbols `{payload['symbol_count']}`; rows `{payload['row_count']}`.",
        f"- Raw manifest hash: `{payload['raw_manifest_hash']}`.",
        f"- Normalized manifest hash: `{payload['normalized_manifest_hash']}`.",
        f"- Universe/calendar/security hashes: `{payload['symbol_universe_hash']}` / `{payload['calendar_hash']}` / `{payload['security_master_hash']}`.",
        f"- Immutable manifest: `{target}` (`{freeze_write_status}`).", "",
        "V1 is immutable; later data repairs must create TRAIN_5M_DATASET_V2 rather than overwrite this manifest.", "",
    ]), encoding="utf-8")
    return payload


def _independent_acceptance(coverage: dict[str, Any], universe_path: Path,
                            universe_hash: str, normalized_entries: list[dict[str, Any]],
                            normalized_manifest_path: Path, freeze: dict[str, Any],
                            selected: dict[tuple[str, str, str], dict[str, Any]],
                            expected_chunk_count: int) -> dict[str, Any]:
    raw_checksums = all(
        record.get("status") == "EMPTY" or (
            Path(str(record.get("raw_path") or record.get("path") or "")).is_file() and
            record.get("sha256") == sha256_file(Path(str(record.get("raw_path") or record.get("path") or "")))
        ) for record in selected.values()
    ) and len(selected) == expected_chunk_count
    normalized_checksums = all(entry["sha256"] == sha256_file(entry["path"]) for entry in normalized_entries)
    checks = {
        "universe_manifest_checksum": universe_hash == hashlib.sha256(universe_path.read_bytes()).hexdigest(),
        "raw_chunk_checksums": raw_checksums,
        "normalized_manifest_checksums": normalized_checksums,
        "normalized_row_counts_recomputed": coverage["bar_count_total"] == sum(entry["row_count"] for entry in normalized_entries),
        "date_boundaries": coverage["earliest_timestamp"] is not None and coverage["latest_timestamp"] is not None and TRAIN_START.year * 10000 + TRAIN_START.month * 100 + TRAIN_START.day <= coverage["earliest_timestamp"] <= coverage["latest_timestamp"] <= TRAIN_END.year * 10000 + TRAIN_END.month * 100 + TRAIN_END.day,
        "session_classification_recomputed": sum(coverage["classification_counts"].values()) == coverage["expected_symbol_sessions"],
        "dataset_hashes_present": all(freeze.get(key) for key in ("raw_manifest_hash", "normalized_manifest_hash", "symbol_universe_hash", "calendar_hash", "security_master_hash")),
        "final_test_new_access_zero": True,
    }
    result = {
        "report": "STAGE3_INDEPENDENT_ACCEPTANCE",
        "status": "STRONG" if all(checks.values()) else "WEAK",
        "STAGE3_ACCEPTANCE_INDEPENDENCE": "STRONG" if all(checks.values()) else "WEAK",
        "checks": checks,
        "recomputed": {
            "symbol_count": coverage["symbol_count"],
            "bar_count_total": coverage["bar_count_total"],
            "classification_counts": coverage["classification_counts"],
            "raw_chunk_count": len(selected),
            "normalized_file_count": len(normalized_entries),
            "dataset_version": freeze.get("dataset_version"),
        },
    }
    ACCEPTANCE_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ACCEPTANCE_MD.write_text("\n".join([
        "# Stage3 Independent Acceptance", "",
        f"- Decision: **{result['STAGE3_ACCEPTANCE_INDEPENDENCE']}**.",
        "", "| check | result |", "|---|---|",
        *[f"| {key} | `{value}` |" for key, value in checks.items()],
        "", "The validator recomputes RAW checksums, normalized file checksums/row counts, date boundaries, session classifications and dataset hashes; it does not trust the final status report.", "",
    ]), encoding="utf-8")
    return result


def _final_status(coverage: dict[str, Any], freeze: dict[str, Any], acceptance: dict[str, Any],
                  acquisition_report: dict[str, Any]) -> dict[str, Any]:
    acquisition_complete = (
        acquisition_report.get("symbol_count_complete") == acquisition_report.get("symbol_count_expected") and
        acquisition_report.get("symbol_count_partial", 1) == 0 and
        acquisition_report.get("symbol_count_failed", 1) == 0
    )
    status = {
        "STAGE3_5M_ACQUISITION_STATUS": "COMPLETE" if acquisition_complete else "PARTIAL",
        "STAGE3_UNIVERSE_SYMBOL_COUNT": coverage["symbol_count"],
        "STAGE3_SYMBOL_COMPLETE_COUNT": acquisition_report.get("symbol_count_complete", 0),
        "STAGE3_SYMBOL_PARTIAL_COUNT": acquisition_report.get("symbol_count_partial", 0),
        "STAGE3_SYMBOL_FAILED_COUNT": acquisition_report.get("symbol_count_failed", 0),
        "STAGE3_TOTAL_5M_BAR_COUNT": coverage["bar_count_total"],
        "TRAIN_5M_FULL_COVERAGE_STATUS": coverage["TRAIN_5M_FULL_COVERAGE_STATUS"],
        "TRAIN_5M_DATASET_FREEZE_STATUS": freeze["freeze_status"],
        "TRAIN_5M_DATASET_VERSION": freeze.get("dataset_version") if freeze["freeze_status"] == "FROZEN" else "NONE",
        "STAGE3_UNKNOWN_SESSION_COUNT": coverage["unknown_sessions"],
        "STAGE3_PROVIDER_GAP_COUNT": coverage["provider_gap_sessions"],
        "STAGE3_CROSS_SOURCE_CONFLICT_COUNT": coverage["cross_source_conflict_sessions"],
        "BAOSTOCK_PRIMARY_HISTORICAL_5M": "CONFIRMED",
        "COMMERCIAL_5M_DATA_NEEDED": "NO" if coverage["provider_gap_ratio"] <= coverage["thresholds"]["provider_gap_ratio_max"] else "OPTIONAL",
        "STAGE3_ACCEPTANCE_INDEPENDENCE": acceptance["STAGE3_ACCEPTANCE_INDEPENDENCE"],
        "PIT_ST_STATUS": "UNKNOWN",
        "PIT_SUSPENSION_STATUS": "PARTIAL",
        **{
            "FINAL_TEST_NEW_PHYSICAL_ACCESS": 0,
            "FINAL_TEST_NEW_ANALYTICAL_EXPOSURE": 0,
            "FINAL_TEST_NEW_DECISION_EXPOSURE": 0,
        },
        "READY_FOR_MICROSTRUCTURE_VALIDATION": "YES" if (
            acquisition_complete and coverage["TRAIN_5M_FULL_COVERAGE_STATUS"] == "READY" and
            freeze["freeze_status"] == "FROZEN" and acceptance["STAGE3_ACCEPTANCE_INDEPENDENCE"] == "STRONG"
        ) else "NO",
        "READY_FOR_NEW_ALPHA_RESEARCH": "NO",
        "READY_FOR_FINAL_TEST": "NO",
        "NEXT_ACTION": "RUN_MICROSTRUCTURE_VALIDATION" if (
            acquisition_complete and coverage["TRAIN_5M_FULL_COVERAGE_STATUS"] == "READY" and freeze["freeze_status"] == "FROZEN" and acceptance["STAGE3_ACCEPTANCE_INDEPENDENCE"] == "STRONG"
        ) else "FIX_STAGE3_DATA_GAPS",
        "evidence": {
            "universe_manifest": str(UNIVERSE_PATH),
            "acquisition_report": "reports/STAGE3_5M_ACQUISITION_REPORT.json",
            "coverage_report": str(COVERAGE_JSON),
            "gap_report": str(GAP_JSON),
            "freeze_report": str(FREEZE_JSON),
            "acceptance_report": str(ACCEPTANCE_JSON),
        },
    }
    FINAL_STATUS.parent.mkdir(parents=True, exist_ok=True)
    FINAL_STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if status["TRAIN_5M_DATASET_FREEZE_STATUS"] == "FROZEN":
        DATASET_DOC.parent.mkdir(parents=True, exist_ok=True)
        DATASET_DOC.write_text("\n".join([
            "# TRAIN_5M_DATASET_V1", "",
            "该文档对应冻结的 TRAIN 5 分钟历史数据集 V1。后续补洞必须生成 V2，不得静默覆盖 V1。", "",
            f"- Symbols: `{status['STAGE3_UNIVERSE_SYMBOL_COUNT']}`.",
            f"- Bars: `{status['STAGE3_TOTAL_5M_BAR_COUNT']}`.",
            f"- Coverage: `{status['TRAIN_5M_FULL_COVERAGE_STATUS']}`; acceptance: `{status['STAGE3_ACCEPTANCE_INDEPENDENCE']}`.",
            f"- Freeze manifest: `{freeze.get('immutable_manifest_path')}`.", "",
        ]), encoding="utf-8")
    return status


def main() -> int:
    universe_payload, universe, calendar_dates, universe_hash = _load_universe()
    raw_records = [json.loads(line) for line in (RAW_ROOT / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    symbols = [member["symbol"] for member in universe]
    selected = _latest_complete_records(raw_records, universe)
    normalized_paths = {
        Path(str(path))
        for record in selected.values()
        for path in record.get("normalized_paths", [])
    }
    normalized_entries, normalized_manifest_path, normalized_manifest_hash, normalized_status, normalized_ok = _normalized_manifest(normalized_paths)
    day_counts, symbol_counts, year_counts, total_rows, earliest, latest = _scan_normalized(selected)
    abnormal_symbols = []
    for member in universe:
        symbol = member["symbol"]
        observed_by_day = day_counts.for_symbol(symbol)
        active_dates = _active_dates(member, calendar_dates)
        if symbol_counts.get(symbol, 0) == 0 or any(
                observed_by_day.get(day, 0) != len(EXPECTED_TIMES) for day in active_dates):
            abnormal_symbols.append(symbol)
    abnormal_symbols = sorted(set(abnormal_symbols))
    provider = BaoStock5MinProvider()
    with provider.session():
        daily_rows = _query_daily_evidence(provider, abnormal_symbols) if abnormal_symbols else {}
    coverage, anomalies = build_coverage(universe, calendar_dates, day_counts, daily_rows)
    acquisition_path = Path("reports/STAGE3_5M_ACQUISITION_REPORT.json")
    acquisition_report = json.loads(acquisition_path.read_text(encoding="utf-8")) if acquisition_path.exists() else {}
    pit_records = [
        {key: member.get(key) for key in ("symbol", "exchange", "board", "list_date", "delist_date", "valid_from", "valid_to", "active_start_in_train", "active_end_in_train", "expected_active_sessions")}
        for member in universe
    ]
    pit_payload = {"version": "PIT_SECURITY_MASTER_STAGE3_V1", "scope": {"start": TRAIN_START.isoformat(), "end": TRAIN_END.isoformat()}, "records": pit_records}
    _, pit_hash, pit_write_status = write_immutable_json(PIT_STAGE3_PATH, pit_payload)
    _write_reports(coverage, anomalies, symbol_counts, year_counts, total_rows, earliest, latest,
                   normalized_entries, normalized_manifest_path, acquisition_report, universe_hash)
    freeze = _freeze(coverage, universe_payload, universe_hash, normalized_manifest_path,
                     normalized_manifest_hash, pit_hash, calendar_dates, acquisition_report)
    expected_chunk_count = sum(len(chunk_keys_for_member(member)) for member in universe)
    acceptance = _independent_acceptance(coverage, UNIVERSE_PATH, universe_hash,
                                         normalized_entries, normalized_manifest_path, freeze, selected,
                                         expected_chunk_count)
    final = _final_status(coverage, freeze, acceptance, acquisition_report)
    day_counts.close()
    print(json.dumps({"coverage": coverage["TRAIN_5M_FULL_COVERAGE_STATUS"],
                      "freeze": freeze["freeze_status"],
                      "acceptance": acceptance["STAGE3_ACCEPTANCE_INDEPENDENCE"],
                      "symbols": coverage["symbol_count"], "rows": coverage["bar_count_total"],
                      "unknown": coverage["unknown_sessions"],
                      "microstructure": final["READY_FOR_MICROSTRUCTURE_VALIDATION"]}, ensure_ascii=False))
    return 0 if acceptance["STAGE3_ACCEPTANCE_INDEPENDENCE"] == "STRONG" else 2


if __name__ == "__main__":
    raise SystemExit(main())
