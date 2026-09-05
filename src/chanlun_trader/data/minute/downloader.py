"""可恢复、追加式的历史 5m 分片下载器。"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from time import sleep
from typing import Any, Callable

import pandas as pd

from .base import (BAR_TIMESTAMP_SEMANTICS, DownloadReport, MinuteDataProvider, ChunkRequest,
                   date_chunks, normalize_symbol)
from .manifest import ImmutableRawStore, Normalized5mStore, sha256_file
from .normalizer import normalize_5m_frame


class Historical5mDownloader:
    def __init__(self, provider: MinuteDataProvider, raw_store: ImmutableRawStore | None = None,
                 normalized_store: Normalized5mStore | None = None, chunk_days: int = 120,
                 retries: int = 2, retry_delay: float = 0.0, retry_max_delay: float = 60.0,
                 request_delay: float = 0.0,
                 progress_callback: Callable[[dict[str, Any]], None] | None = None):
        self.provider = provider
        self.raw_store = raw_store or ImmutableRawStore()
        self.normalized_store = normalized_store or Normalized5mStore()
        self.chunk_days = chunk_days
        self.retries = retries
        self.retry_delay = retry_delay
        self.retry_max_delay = retry_max_delay
        self.request_delay = request_delay
        self.progress_callback = progress_callback
        self._request_records: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for record in self.raw_store.manifest.records():
            self._index_record(record)

    @staticmethod
    def _record_key(record: dict[str, Any]) -> tuple[str, str, str] | None:
        symbol = record.get("symbol")
        requested_start = record.get("requested_start")
        requested_end = record.get("requested_end")
        if not symbol or not requested_start or not requested_end:
            return None
        return str(symbol), str(requested_start), str(requested_end)

    def _index_record(self, record: dict[str, Any]) -> None:
        key = self._record_key(record)
        if key is not None:
            self._request_records.setdefault(key, []).append(record)

    def _append_manifest(self, record: dict[str, Any]) -> None:
        self.raw_store.manifest.append(record)
        self._index_record(record)

    def _progress(self, **event: Any) -> None:
        if self.progress_callback is not None:
            self.progress_callback(event)

    def _completed(self, req: ChunkRequest) -> dict[str, Any] | None:
        key = (req.symbol, req.requested_start.isoformat(), req.requested_end.isoformat())
        for record in reversed(self._request_records.get(key, [])):
            if (record.get("symbol") == req.symbol and
                    record.get("requested_start") == req.requested_start.isoformat() and
                    record.get("requested_end") == req.requested_end.isoformat() and
                    ((record.get("status") == "EMPTY" and record.get("row_count", 0) == 0) or
                     (record.get("status") == "COMPLETE" and
                      Path(record.get("raw_path", record.get("path", ""))).exists() and
                      all(Path(p).exists() for p in record.get("normalized_paths", []))))):
                raw_path = record.get("raw_path", record.get("path"))
                if record.get("status") == "EMPTY" or sha256_file(raw_path) == record.get("sha256"):
                    return record
        return None

    def _repair_from_raw(self, req: ChunkRequest) -> dict[str, Any] | None:
        """把已有 RAW 重新标准化，避免 partial recovery 重新请求远端。"""
        key = (req.symbol, req.requested_start.isoformat(), req.requested_end.isoformat())
        for record in reversed(self._request_records.get(key, [])):
            if (record.get("symbol") == req.symbol and
                    record.get("requested_start") == req.requested_start.isoformat() and
                    record.get("requested_end") == req.requested_end.isoformat() and
                    record.get("status") in {"WRITTEN", "UNCHANGED"}):
                raw_path = record.get("path")
                if not raw_path or not Path(raw_path).exists():
                    continue
                raw = pd.read_parquet(raw_path)
                normalized = normalize_5m_frame(
                    raw, req.symbol, record.get("source", self.provider.source),
                    getattr(self.provider, "timestamp_semantics", BAR_TIMESTAMP_SEMANTICS),
                )
                paths = [str(p) for p in self.normalized_store.write(normalized)]
                complete = {**record, "status": "COMPLETE", "raw_path": raw_path,
                            "normalized_paths": paths, "row_count": len(normalized)}
                self._append_manifest(complete)
                return complete
        return None

    def download(self, symbol: str, start_date: date, end_date: date,
                 resume: bool = True, allow_empty: bool = False) -> DownloadReport:
        symbol = normalize_symbol(symbol)
        report = DownloadReport(symbol, start_date, end_date)
        chunks = list(date_chunks(start_date, end_date, self.chunk_days))
        for chunk_number, (chunk_start, chunk_end) in enumerate(chunks, start=1):
            req = ChunkRequest(symbol, chunk_start, chunk_end)
            self._progress(event="chunk_start", symbol=symbol, current_chunk=chunk_number,
                           total_chunks=len(chunks), requested_start=chunk_start.isoformat(),
                           requested_end=chunk_end.isoformat())
            if resume:
                existing = self._completed(req)
                if existing:
                    report.chunks.append({**existing, "status": "RESUMED"})
                    self._progress(event="chunk_done", symbol=symbol, current_chunk=chunk_number,
                                   total_chunks=len(chunks), status="RESUMED",
                                   row_count=existing.get("row_count", 0), retry_count=0)
                    continue
                repaired = self._repair_from_raw(req)
                if repaired:
                    report.chunks.append({**repaired, "status": "REPAIRED_FROM_RAW"})
                    self._progress(event="chunk_done", symbol=symbol, current_chunk=chunk_number,
                                   total_chunks=len(chunks), status="REPAIRED_FROM_RAW",
                                   row_count=repaired.get("row_count", 0), retry_count=0)
                    continue
            last_error = ""
            for retry in range(self.retries + 1):
                try:
                    if self.request_delay:
                        sleep(self.request_delay)
                    raw = self.provider.fetch(symbol, chunk_start, chunk_end)
                    normalized = normalize_5m_frame(
                        raw, symbol, self.provider.source,
                        getattr(self.provider, "timestamp_semantics", BAR_TIMESTAMP_SEMANTICS),
                    )
                    if normalized.empty:
                        if allow_empty:
                            empty_record = {
                                "symbol": symbol, "requested_start": chunk_start.isoformat(),
                                "requested_end": chunk_end.isoformat(), "row_count": 0,
                                "status": "EMPTY", "retry_count": retry,
                                "source": self.provider.source,
                                "fetched_at": datetime.now().astimezone().isoformat(),
                                "empty_reason": "provider_returned_no_rows",
                            }
                            self._append_manifest(empty_record)
                            report.chunks.append(empty_record)
                            self._progress(event="chunk_done", symbol=symbol, current_chunk=chunk_number,
                                           total_chunks=len(chunks), status="EMPTY", row_count=0,
                                           retry_count=retry)
                            break
                        raise ValueError("provider returned no rows")
                    path, checksum, status = self.raw_store.write_chunk(
                        symbol, chunk_start.isoformat(), chunk_end.isoformat(), raw,
                        metadata={
                            "actual_start": normalized["timestamp"].min().isoformat(),
                        "actual_end": normalized["timestamp"].max().isoformat(),
                            "retry_count": retry,
                            "fetched_at": datetime.now().astimezone().isoformat(),
                        },
                    )
                    normalized_paths = [str(p) for p in self.normalized_store.write(normalized)]
                    record = {
                        "symbol": symbol, "requested_start": chunk_start.isoformat(),
                        "requested_end": chunk_end.isoformat(),
                        "actual_start": normalized["timestamp"].min().isoformat(),
                        "actual_end": normalized["timestamp"].max().isoformat(),
                        "row_count": len(normalized), "status": status,
                        "retry_count": retry, "source": self.provider.source,
                        "fetched_at": datetime.now().astimezone().isoformat(),
                        "sha256": checksum, "raw_path": str(path), "raw_status": status,
                        "normalized_paths": normalized_paths,
                    }
                    # Completion is recorded only after normalized materialization;
                    # a RAW write alone is not enough for safe resume.
                    self._append_manifest({**record, "status": "COMPLETE"})
                    report.chunks.append(record)
                    self._progress(event="chunk_done", symbol=symbol, current_chunk=chunk_number,
                                   total_chunks=len(chunks), status="COMPLETE",
                                   row_count=len(normalized), retry_count=retry)
                    break
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    self._progress(event="chunk_retry", symbol=symbol, current_chunk=chunk_number,
                                   total_chunks=len(chunks), status="RETRYING", retry_count=retry + 1,
                                   error=last_error)
                    if retry < self.retries and self.retry_delay:
                        reset_session = getattr(self.provider, "reset_session", None)
                        if callable(reset_session):
                            reset_session()
                        sleep(min(self.retry_delay * (2 ** retry), self.retry_max_delay))
            else:
                failure = {
                    "symbol": symbol, "requested_start": chunk_start.isoformat(),
                    "requested_end": chunk_end.isoformat(), "status": "FAILED",
                    "retry_count": self.retries, "source": self.provider.source,
                    "error": last_error,
                }
                self._append_manifest(failure)
                report.failures.append(failure)
                self._progress(event="chunk_failed", symbol=symbol, current_chunk=chunk_number,
                               total_chunks=len(chunks), status="FAILED", row_count=0,
                               retry_count=self.retries, error=last_error)
        self._progress(event="symbol_done", symbol=symbol, status=report.status,
                       chunk_count=len(report.chunks), failure_count=len(report.failures),
                       row_count=sum(int(chunk.get("row_count", 0)) for chunk in report.chunks))
        return report

    def download_universe(self, symbols: list[str], start_date: date, end_date: date,
                          batch_size: int = 50) -> list[DownloadReport]:
        reports: list[DownloadReport] = []
        for i in range(0, len(symbols), batch_size):
            for symbol in symbols[i:i + batch_size]:
                reports.append(self.download(symbol, start_date, end_date, resume=True))
        return reports
