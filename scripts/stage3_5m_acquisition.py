"""Stage 3 full-market TRAIN 5m acquisition with immutable checkpoints."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from time import sleep
from typing import Any, Iterable

from chanlun_trader.data.minute import BaoStock5MinProvider, Historical5mDownloader
from chanlun_trader.data.minute.base import BAR_TIMESTAMP_SEMANTICS, date_chunks, normalize_symbol
from chanlun_trader.data.minute.manifest import ImmutableRawStore, sha256_file
from chanlun_trader.data.minute.pit_security_master import board_for_symbol


TRAIN_START = date(2022, 8, 1)
TRAIN_END = date(2024, 7, 31)
RAW_ROOT = Path("data/market_raw/baostock/5m")
NORMALIZED_ROOT = Path("data/research/market_5m")
UNIVERSE_PATH = Path("data/research/stage3_universe_manifest.json")
PROGRESS_PATH = Path("reports/STAGE3_5M_ACQUISITION_PROGRESS.jsonl")
REPORT_PATH = Path("reports/STAGE3_5M_ACQUISITION_REPORT.json")
FAILURE_PATH = Path("reports/STAGE3_5M_FAILURE_LEDGER.csv")
CHUNK_DAYS = 120
FINAL_TEST_EXPOSURE = {
    "FINAL_TEST_NEW_PHYSICAL_ACCESS": 0,
    "FINAL_TEST_NEW_ANALYTICAL_EXPOSURE": 0,
    "FINAL_TEST_NEW_DECISION_EXPOSURE": 0,
}


def _read_result(result: Any) -> list[dict[str, Any]]:
    if str(result.error_code) != "0":
        raise RuntimeError(f"BaoStock query failed: {result.error_code} {result.error_msg}")
    rows: list[dict[str, Any]] = []
    while result.next():
        rows.append(dict(zip(result.fields, result.get_row_data())))
    return rows


def query_stock_basic(provider: BaoStock5MinProvider) -> list[dict[str, Any]]:
    return _read_result(provider.bs.query_stock_basic())


def query_trade_calendar(provider: BaoStock5MinProvider,
                         start_date: date = TRAIN_START,
                         end_date: date = TRAIN_END) -> list[int]:
    rows = _read_result(provider.bs.query_trade_dates(
        start_date=start_date.isoformat(), end_date=end_date.isoformat(),
    ))
    return sorted(
        int(str(row["calendar_date"]).replace("-", ""))
        for row in rows if str(row.get("is_trading_day", "")) == "1"
    )


def _date_or_none(value: Any) -> date | None:
    text = str(value or "").strip()
    return date.fromisoformat(text) if text else None


def build_stage3_universe(rows: Iterable[dict[str, Any]], calendar_dates: Iterable[int],
                          start_date: date = TRAIN_START,
                          end_date: date = TRAIN_END) -> list[dict[str, Any]]:
    calendar = [date(int(day) // 10000, int(day) // 100 % 100, int(day) % 100)
                for day in calendar_dates]
    result: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("type", "")) != "1":
            continue
        raw_code = str(row.get("code", "")).lower()
        if "." not in raw_code:
            continue
        market, code = raw_code.split(".", 1)
        if market not in {"sh", "sz"} or len(code) != 6 or not code.isdigit():
            continue
        list_date = _date_or_none(row.get("ipoDate"))
        delist_date = _date_or_none(row.get("outDate"))
        if list_date is None or list_date > end_date or (delist_date and delist_date < start_date):
            continue
        symbol = normalize_symbol(f"{code}.{market.upper()}")
        active_dates = [day for day in calendar if list_date <= day <= (delist_date or end_date)]
        if not active_dates:
            continue
        result.append({
            "symbol": symbol,
            "exchange": market.upper(),
            "board": board_for_symbol(symbol),
            "list_date": list_date.isoformat(),
            "delist_date": delist_date.isoformat() if delist_date else None,
            "valid_from": list_date.isoformat(),
            "valid_to": delist_date.isoformat() if delist_date else None,
            "active_start_in_train": active_dates[0].isoformat(),
            "active_end_in_train": active_dates[-1].isoformat(),
            "expected_active_sessions": len(active_dates),
            "code_name": str(row.get("code_name", "")),
            "source_type": str(row.get("type")),
            "source_status": str(row.get("status", "")),
        })
    result.sort(key=lambda item: item["symbol"])
    symbols = [item["symbol"] for item in result]
    if len(symbols) != len(set(symbols)):
        raise ValueError("Stage3 universe contains duplicate symbols")
    return result


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def active_bounds(member: dict[str, Any]) -> tuple[date, date]:
    listed = date.fromisoformat(str(member["list_date"]))
    start = max(TRAIN_START, listed)
    delisted = date.fromisoformat(str(member["delist_date"])) if member.get("delist_date") else TRAIN_END
    return start, min(TRAIN_END, delisted)


def chunk_keys_for_member(member: dict[str, Any]) -> list[tuple[str, str]]:
    start, end = active_bounds(member)
    return [(a.isoformat(), b.isoformat()) for a, b in date_chunks(start, end, CHUNK_DAYS)]


def write_immutable_json(path: Path, payload: dict[str, Any]) -> tuple[Path, str, str]:
    encoded = _canonical_bytes(payload)
    checksum = hashlib.sha256(encoded).hexdigest()
    target = path
    status = "WRITTEN"
    if path.exists():
        if path.read_bytes() == encoded:
            return path, checksum, "UNCHANGED"
        target = path.with_name(f"{path.stem}.conflict-{checksum[:12]}{path.suffix}")
        if target.exists() and target.read_bytes() == encoded:
            return target, checksum, "UNCHANGED_CONFLICT"
        status = "DATA_CONFLICT"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(encoded)
    return target, checksum, status


def build_universe_payload(members: list[dict[str, Any]], calendar_dates: list[int]) -> dict[str, Any]:
    return {
        "manifest": "STAGE3_UNIVERSE_MANIFEST",
        "version": "STAGE3_UNIVERSE_MANIFEST_V1",
        "source": "baostock.query_stock_basic + baostock.query_trade_dates",
        "source_filter": "type=1 A-share stocks with lifecycle overlap in 2022-08-01..2024-07-31",
        "scope": {"start": TRAIN_START.isoformat(), "end": TRAIN_END.isoformat()},
        "calendar_dates": calendar_dates,
        "symbol_count": len(members),
        "symbols": members,
    }


def _complete_keys(records: list[dict[str, Any]], members: list[dict[str, Any]]) -> dict[str, set[tuple[str, str]]]:
    by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = (str(record.get("symbol")), str(record.get("requested_start")), str(record.get("requested_end")))
        by_key[key].append(record)
    complete: dict[str, set[tuple[str, str]]] = {member["symbol"]: set() for member in members}
    for member in members:
        symbol = member["symbol"]
        for key in chunk_keys_for_member(member):
            candidates = by_key.get((symbol, key[0], key[1]), [])
            for record in reversed(candidates):
                raw_path = Path(str(record.get("raw_path") or record.get("path") or ""))
                if (record.get("status") == "EMPTY" and record.get("row_count", 0) == 0) or (
                        record.get("status") == "COMPLETE" and raw_path.is_file() and
                        record.get("sha256") == sha256_file(raw_path)):
                    complete[symbol].add(key)
                    break
    return complete


def _symbol_summary(records: list[dict[str, Any]], members: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_key[(str(record.get("symbol")), str(record.get("requested_start")),
                str(record.get("requested_end")))].append(record)
    summary: dict[str, dict[str, Any]] = {}
    for member in members:
        symbol = member["symbol"]
        expected = chunk_keys_for_member(member)
        complete_records: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for start, end in sorted(expected):
            candidates = by_key.get((symbol, start, end), [])
            failures.extend(record for record in candidates if record.get("status") == "FAILED")
            for record in reversed(candidates):
                raw_path = Path(str(record.get("raw_path") or record.get("path") or ""))
                if (record.get("status") == "EMPTY" and record.get("row_count", 0) == 0) or (
                        record.get("status") == "COMPLETE" and raw_path.is_file() and
                        record.get("sha256") == sha256_file(raw_path)):
                    complete_records.append(record)
                    break
        complete_count = len(complete_records)
        status = "COMPLETE" if complete_count == len(expected) else "PARTIAL" if complete_count else "FAILED"
        summary[symbol] = {
            "symbol": symbol,
            "status": status,
            "complete_chunks": complete_count,
            "expected_chunks": len(expected),
            "failed_chunks": len(failures),
            "row_count": sum(int(record.get("row_count", 0)) for record in complete_records),
            "raw_status_counts": dict(__import__("collections").Counter(
                str(record.get("raw_status", record.get("status", "WRITTEN"))) for record in complete_records
            )),
        }
    return summary


class ProgressJournal:
    def __init__(self, path: Path, total_symbols: int, batch_size: int,
                 symbol_index: dict[str, int], initial_complete: set[str]):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.total_symbols = total_symbols
        self.batch_size = batch_size
        self.symbol_index = symbol_index
        self.completed = set(initial_complete)
        self.partial: set[str] = set()
        self.failed: set[str] = set()
        self.processed: set[str] = set()
        self.rows_downloaded = 0
        self.retry_count = 0
        self.started = time.monotonic()
        self.current_batch = 0
        self.current_symbol: str | None = None
        self.current_chunk: int | None = None

    def _write(self, event: str, **extra: Any) -> None:
        elapsed = time.monotonic() - self.started
        finished = len(self.completed) + len(self.partial) + len(self.failed)
        remaining = max(0, self.total_symbols - finished)
        measured = len(self.processed)
        estimated = (elapsed / measured * remaining) if measured else None
        record = {
            "event": event,
            "total_symbols": self.total_symbols,
            "completed_symbols": len(self.completed),
            "partial_symbols": len(self.partial),
            "failed_symbols": len(self.failed),
            "remaining_symbols": remaining,
            "current_batch": self.current_batch,
            "current_symbol": self.current_symbol,
            "current_chunk": self.current_chunk,
            "rows_downloaded": self.rows_downloaded,
            "retry_count": self.retry_count,
            "elapsed_seconds": round(elapsed, 3),
            "estimated_remaining_seconds": round(estimated, 3) if estimated is not None else None,
            "recorded_at": datetime.now().astimezone().isoformat(),
            **extra,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def set_batch(self, batch_number: int) -> None:
        self.current_batch = batch_number

    def set_symbol(self, symbol: str) -> None:
        self.current_symbol = symbol
        self.current_chunk = None

    def record_symbol(self, symbol: str, status: str) -> None:
        self.processed.add(symbol)
        self.completed.discard(symbol)
        self.partial.discard(symbol)
        self.failed.discard(symbol)
        if status == "COMPLETE":
            self.completed.add(symbol)
        elif status == "PARTIAL":
            self.partial.add(symbol)
        else:
            self.failed.add(symbol)
        self._write("symbol_summary", symbol=symbol, status=status)

    def __call__(self, event: dict[str, Any]) -> None:
        self.current_symbol = event.get("symbol", self.current_symbol)
        self.current_chunk = event.get("current_chunk", self.current_chunk)
        self.rows_downloaded += int(event.get("row_count", 0) or 0)
        self.retry_count += int(event.get("retry_count", 0) or 0) if event.get("event") == "chunk_retry" else 0
        self._write(str(event.get("event", "progress")), **{k: v for k, v in event.items() if k != "event"})


def write_failure_ledger(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["symbol", "requested_start", "requested_end", "status", "retry_count", "source", "error"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in fields})


def _git_state() -> dict[str, Any]:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], text=True).splitlines()
        return {"commit": commit, "dirty": bool(dirty), "dirty_file_count": len(dirty)}
    except Exception as exc:
        return {"commit": None, "dirty": None, "error": f"{type(exc).__name__}: {exc}"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Acquire the full PIT TRAIN historical 5m universe")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--batch-pause", type=float, default=2.0)
    parser.add_argument("--request-delay", type=float, default=0.25)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    parser.add_argument("--retry-max-delay", type=float, default=30.0)
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--universe-path", type=Path, default=UNIVERSE_PATH)
    parser.add_argument("--progress-path", type=Path, default=PROGRESS_PATH)
    parser.add_argument("--report-path", type=Path, default=REPORT_PATH)
    parser.add_argument("--failure-path", type=Path, default=FAILURE_PATH)
    return parser.parse_args()


def _load_or_build_universe(provider: BaoStock5MinProvider, path: Path) -> tuple[dict[str, Any], Path, str, str]:
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != "STAGE3_UNIVERSE_MANIFEST_V1":
            raise ValueError(f"unsupported Stage3 universe manifest: {path}")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        return payload, path, checksum, "EXISTING"
    calendar_dates = query_trade_calendar(provider)
    members = build_stage3_universe(query_stock_basic(provider), calendar_dates)
    payload = build_universe_payload(members, calendar_dates)
    target, checksum, status = write_immutable_json(path, payload)
    return payload, target, checksum, status


def main() -> int:
    args = parse_args()
    if args.batch_size < 1 or args.batch_pause < 0 or args.request_delay < 0:
        raise SystemExit("batch-size must be positive and pauses/delays must not be negative")
    if args.retries < 0 or args.retry_delay < 0 or args.retry_max_delay < 0:
        raise SystemExit("retry settings must not be negative")
    provider = BaoStock5MinProvider()
    with provider.session():
        universe_payload, universe_path, universe_hash, universe_status = _load_or_build_universe(provider, args.universe_path)
        members = list(universe_payload["symbols"])
        symbols = [member["symbol"] for member in members]
        if args.symbols:
            requested = [normalize_symbol(symbol) for symbol in args.symbols]
            unknown = sorted(set(requested) - set(symbols))
            if unknown:
                raise SystemExit(f"symbols outside Stage3 PIT universe: {unknown}")
            symbols = requested
        if args.prepare_only:
            print(json.dumps({"status": "PREPARED", "universe_path": str(universe_path),
                              "universe_hash": universe_hash, "symbol_count": len(symbols)}, ensure_ascii=False))
            return 0
        raw_store = ImmutableRawStore(RAW_ROOT)
        existing_records = raw_store.manifest.records()
        selected_members = [member for member in members if member["symbol"] in symbols]
        complete = _complete_keys(existing_records, selected_members)
        initial_complete = {member["symbol"] for member in selected_members
                            if len(complete[member["symbol"]]) == len(chunk_keys_for_member(member))}
        symbol_index = {symbol: index for index, symbol in enumerate(symbols)}
        progress = ProgressJournal(args.progress_path, len(symbols), args.batch_size, symbol_index, initial_complete)
        progress._write("run_start", universe_path=str(universe_path), universe_hash=universe_hash,
                        universe_status=universe_status, scope_start=TRAIN_START.isoformat(),
                        scope_end=TRAIN_END.isoformat(), batch_size=args.batch_size)
        downloader = Historical5mDownloader(
            provider, raw_store=raw_store, chunk_days=CHUNK_DAYS, retries=args.retries,
            retry_delay=args.retry_delay, retry_max_delay=args.retry_max_delay,
            request_delay=args.request_delay, progress_callback=progress,
        )
        for offset in range(0, len(symbols), args.batch_size):
            batch_number = offset // args.batch_size + 1
            progress.set_batch(batch_number)
            batch = symbols[offset:offset + args.batch_size]
            for symbol in batch:
                progress.set_symbol(symbol)
                print(f"START {symbol} batch={batch_number}", flush=True)
                member = next(item for item in selected_members if item["symbol"] == symbol)
                active_start, active_end = active_bounds(member)
                result = downloader.download(symbol, active_start, active_end, resume=True, allow_empty=True)
                progress.record_symbol(symbol, result.status)
                print(f"DONE {symbol} {result.status} chunks={len(result.chunks)} failures={len(result.failures)}", flush=True)
            if offset + args.batch_size < len(symbols) and args.batch_pause:
                sleep(args.batch_pause)
        final_records = raw_store.manifest.records()
        summary = _symbol_summary(final_records, selected_members)
        expected_failure_keys = {
            (member["symbol"], start, end)
            for member in selected_members
            for start, end in chunk_keys_for_member(member)
        }
        write_failure_ledger(args.failure_path, [record for record in final_records
                                                 if record.get("status") == "FAILED" and
                                                 (str(record.get("symbol")), str(record.get("requested_start")),
                                                  str(record.get("requested_end"))) in expected_failure_keys])
        counts = {status: sum(item["status"] == status for item in summary.values())
                  for status in ("COMPLETE", "PARTIAL", "FAILED")}
        report = {
            "report": "STAGE3_5M_ACQUISITION_REPORT",
            "scope": {"start": TRAIN_START.isoformat(), "end": TRAIN_END.isoformat()},
            "source": {"name": "BaoStock5MinProvider", "adjustflag": "3", "frequency": "5",
                       "role": "PRIMARY_HISTORICAL_5M_SOURCE", "timestamp_semantics": BAR_TIMESTAMP_SEMANTICS.value},
            "universe_manifest": str(universe_path),
            "universe_manifest_sha256": universe_hash,
            "symbol_count_expected": len(symbols),
            "symbol_count_complete": counts["COMPLETE"],
            "symbol_count_partial": counts["PARTIAL"],
            "symbol_count_failed": counts["FAILED"],
            "complete_chunk_count": sum(item["complete_chunks"] for item in summary.values()),
            "expected_chunk_count": sum(len(chunk_keys_for_member(member)) for member in selected_members),
            "rows_downloaded": sum(item["row_count"] for item in summary.values()),
            "failure_ledger": str(args.failure_path),
            "progress_path": str(args.progress_path),
            "batch_size": args.batch_size,
            "batch_pause_seconds": args.batch_pause,
            "request_delay_seconds": args.request_delay,
            "retry_limit": args.retries,
            "retry_delay_seconds": args.retry_delay,
            "retry_max_delay_seconds": args.retry_max_delay,
            **{key.lower(): value for key, value in FINAL_TEST_EXPOSURE.items()},
            "symbols": list(summary.values()),
            "git_state": _git_state(),
        }
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        md = args.report_path.with_suffix(".md")
        md.write_text("\n".join([
            "# Stage3 5m Acquisition Report", "",
            f"- Scope: `{TRAIN_START}` ~ `{TRAIN_END}`; source `BaoStock5MinProvider`, `frequency=5`, `adjustflag=3`.",
            f"- Universe: `{len(symbols)}` symbols from `{universe_path}`; manifest SHA-256 `{universe_hash}`.",
            f"- Symbols: complete `{counts['COMPLETE']}`, partial `{counts['PARTIAL']}`, failed `{counts['FAILED']}`.",
            f"- Chunks: `{report['complete_chunk_count']}/{report['expected_chunk_count']}` complete; rows `{report['rows_downloaded']}`.",
            f"- Checkpoint: `{args.progress_path}`; failure ledger: `{args.failure_path}`.",
            "- Final Test new physical/analytical/decision exposure: `0/0/0`.", "",
        ]) + "\n", encoding="utf-8")
        progress._write("run_end", status="COMPLETE" if counts["PARTIAL"] == counts["FAILED"] == 0 else "PARTIAL",
                        completed_symbols=counts["COMPLETE"], partial_symbols=counts["PARTIAL"],
                        failed_symbols=counts["FAILED"])
        print(json.dumps({"status": report["symbol_count_complete"] == report["symbol_count_expected"] and "COMPLETE" or "PARTIAL",
                          "symbols": len(symbols), "complete": counts["COMPLETE"],
                          "partial": counts["PARTIAL"], "failed": counts["FAILED"],
                          "rows": report["rows_downloaded"]}, ensure_ascii=False))
        return 0 if counts["PARTIAL"] == counts["FAILED"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
