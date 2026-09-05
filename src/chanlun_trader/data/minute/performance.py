"""Derived performance accelerators for the frozen 5m dataset.

The classes in this module never replace or mutate canonical TRAIN_5M_DATASET_V1
files. They build immutable derived indexes/sidecars and fail closed when their
dataset identity is stale.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


INDEX_VERSION = "MINUTE_DATASET_INDEX_V1"
SESSION_SUMMARY_VERSION = "5M_SESSION_SUMMARY_V1"
EVENT_WINDOW_CACHE_VERSION = "EVENT_WINDOW_CACHE_V1"
FAST_VERIFY = "FAST_VERIFY"
FULL_CRYPTO_VERIFY = "FULL_CRYPTO_VERIFY"


class DatasetIdentityError(RuntimeError):
    """Raised when a derived artifact cannot be used for the requested dataset."""


class IncrementalVerificationError(RuntimeError):
    """Raised when FAST_VERIFY cannot safely prove that data is unchanged."""


def normalize_path(value: str | Path) -> Path:
    return Path(str(value).replace("\\", os.sep))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with normalize_path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_stat(value: Any) -> Any:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _column_bounds(parquet_file: pq.ParquetFile, column_name: str) -> tuple[Any, Any]:
    try:
        column_index = parquet_file.schema.names.index(column_name)
    except ValueError as exc:
        raise DatasetIdentityError(f"canonical parquet is missing {column_name}: {parquet_file}") from exc
    minimum = None
    maximum = None
    for row_group_index in range(parquet_file.metadata.num_row_groups):
        statistics = parquet_file.metadata.row_group(row_group_index).column(column_index).statistics
        if statistics is None or not statistics.has_min_max:
            raise DatasetIdentityError(f"missing parquet statistics for {column_name}: {parquet_file}")
        lower = _decode_stat(statistics.min)
        upper = _decode_stat(statistics.max)
        minimum = lower if minimum is None or lower < minimum else minimum
        maximum = upper if maximum is None or upper > maximum else maximum
    return minimum, maximum


def _load_manifest_entries(manifest_path: Path) -> list[dict[str, Any]]:
    entries = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not entries:
        raise DatasetIdentityError(f"empty normalized manifest: {manifest_path}")
    return entries


@dataclass(frozen=True)
class MinuteDatasetIndex:
    """Metadata-only index from symbol/date ranges to canonical parquet paths."""

    path: Path
    dataset_version: str
    manifest_hash: str
    index_version: str = INDEX_VERSION

    @property
    def metadata_path(self) -> Path:
        return self.path.with_suffix(".json")

    @classmethod
    def build(
        cls,
        manifest_path: str | Path,
        output_path: str | Path,
        *,
        dataset_version: str,
        manifest_hash: str,
        index_version: str = INDEX_VERSION,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> "MinuteDatasetIndex":
        manifest_path = normalize_path(manifest_path)
        output_path = normalize_path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists() and output_path.with_suffix(".json").exists():
            return cls.open(output_path, dataset_version=dataset_version, manifest_hash=manifest_hash, index_version=index_version)

        rows: list[dict[str, Any]] = []
        entries = _load_manifest_entries(manifest_path)
        total_entries = len(entries)
        for processed_entries, entry in enumerate(entries, start=1):
            source_path = normalize_path(entry["path"])
            if not source_path.is_file():
                raise DatasetIdentityError(f"manifest path is missing: {source_path}")
            parquet_file = pq.ParquetFile(source_path)
            symbol_min, symbol_max = _column_bounds(parquet_file, "symbol")
            date_min, date_max = _column_bounds(parquet_file, "trade_date")
            if symbol_min != symbol_max:
                raise DatasetIdentityError(f"a normalized fragment contains multiple symbols: {source_path}")
            rows.append({
                "dataset_version": dataset_version,
                "manifest_hash": manifest_hash,
                "index_version": index_version,
                "symbol": str(symbol_min),
                "start_date": int(date_min),
                "end_date": int(date_max),
                "path": str(source_path).replace("\\", "/"),
                "row_count": int(entry.get("row_count", parquet_file.metadata.num_rows)),
                "available_rows": int(parquet_file.metadata.num_rows),
                "size_bytes": int(entry.get("size_bytes", source_path.stat().st_size)),
                "row_group_count": int(parquet_file.metadata.num_row_groups),
                "sha256": str(entry.get("sha256", "")),
            })
            if progress_callback is not None:
                progress_callback(processed_entries, total_entries)
        frame = pd.DataFrame(rows).sort_values(["symbol", "start_date", "path"]).reset_index(drop=True)
        frame.to_parquet(output_path, index=False)
        metadata = {
            "dataset_version": dataset_version,
            "manifest_hash": manifest_hash,
            "index_version": index_version,
            "created_at": _now(),
            "entry_count": int(len(frame)),
            "path": str(output_path).replace("\\", "/"),
        }
        output_path.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return cls.open(output_path, dataset_version=dataset_version, manifest_hash=manifest_hash, index_version=index_version)

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        dataset_version: str,
        manifest_hash: str,
        index_version: str = INDEX_VERSION,
    ) -> "MinuteDatasetIndex":
        path = normalize_path(path)
        metadata_path = path.with_suffix(".json")
        if not path.is_file() or not metadata_path.is_file():
            raise DatasetIdentityError(f"minute dataset index is incomplete: {path}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        expected = {"dataset_version": dataset_version, "manifest_hash": manifest_hash, "index_version": index_version}
        actual = {key: metadata.get(key) for key in expected}
        if actual != expected:
            raise DatasetIdentityError(f"stale minute dataset index identity: expected={expected}, actual={actual}")
        return cls(path=path, **expected)

    def frame(self) -> pd.DataFrame:
        cached = getattr(self, "_frame_cache", None)
        if cached is None:
            cached = pd.read_parquet(self.path)
            object.__setattr__(self, "_frame_cache", cached)
        return cached

    def lookup(self, symbol: str, start_date: int | None = None, end_date: int | None = None) -> pd.DataFrame:
        frame = self.frame()
        frame = frame[frame["symbol"].astype(str) == str(symbol)]
        if start_date is not None:
            frame = frame[frame["end_date"] >= int(start_date)]
        if end_date is not None:
            frame = frame[frame["start_date"] <= int(end_date)]
        return frame.sort_values(["start_date", "path"]).reset_index(drop=True)

    def paths_for_range(self, symbol: str, start_date: int, end_date: int) -> list[Path]:
        frame = self.lookup(symbol, start_date, end_date)
        return [normalize_path(path) for path in frame["path"].tolist()]


@dataclass(frozen=True)
class SessionSummaryStore:
    root: Path
    dataset_version: str
    manifest_hash: str
    summary_version: str = SESSION_SUMMARY_VERSION

    @property
    def metadata_path(self) -> Path:
        return self.root / "metadata.json"

    @classmethod
    def build(
        cls,
        index: MinuteDatasetIndex,
        output_root: str | Path,
        *,
        complete_bar_count: int = 48,
        progress_callback: Callable[[int, int, int], None] | None = None,
    ) -> "SessionSummaryStore":
        output_root = normalize_path(output_root)
        final_root = output_root / f"5m_session_summary_{index.manifest_hash[:16]}"
        metadata_path = final_root / "metadata.json"
        if metadata_path.exists():
            return cls.open(final_root, dataset_version=index.dataset_version, manifest_hash=index.manifest_hash)
        if final_root.exists():
            raise DatasetIdentityError(f"incomplete session summary exists: {final_root}")
        final_root.mkdir(parents=True, exist_ok=False)
        writers: dict[str, pq.ParquetWriter] = {}
        row_count = 0
        source_row_count = 0
        partition_paths: dict[str, str] = {}
        try:
            index_entries = index.frame().to_dict("records")
            total_entries = len(index_entries)
            for processed_entries, entry in enumerate(index_entries, start=1):
                source_path = normalize_path(entry["path"])
                columns = ["symbol", "trade_date", "timestamp", "volume", "amount", "source"]
                table = pq.read_table(source_path, columns=columns)
                frame = table.to_pandas()
                if frame.empty:
                    continue
                source_row_count += len(frame)
                frame["trade_date"] = pd.to_numeric(frame["trade_date"], errors="raise").astype(int)
                frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="raise")
                frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)
                frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce").fillna(0.0)
                grouped = frame.sort_values(["symbol", "trade_date", "timestamp"]).groupby(["symbol", "trade_date"], sort=True)
                summary = grouped.agg(
                    bar_count=("timestamp", "size"),
                    first_bar_time=("timestamp", "min"),
                    last_bar_time=("timestamp", "max"),
                    volume_sum=("volume", "sum"),
                    amount_sum=("amount", "sum"),
                    source=("source", "first"),
                ).reset_index()
                summary["data_status"] = summary["bar_count"].map(
                    lambda value: "COMPLETE_48" if int(value) == complete_bar_count else "PARTIAL_SESSION"
                )
                summary["source_path"] = str(source_path).replace("\\", "/")
                summary["dataset_version"] = index.dataset_version
                summary["manifest_hash"] = index.manifest_hash
                for (year, month), partition in summary.groupby(
                    [summary["trade_date"] // 10000, (summary["trade_date"] // 100) % 100], sort=True
                ):
                    key = f"{int(year):04d}-{int(month):02d}"
                    relative = Path(f"year={int(year):04d}") / f"month={int(month):02d}" / "part-000.parquet"
                    target = final_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    part_table = pa.Table.from_pandas(partition.reset_index(drop=True), preserve_index=False)
                    if key not in writers:
                        writers[key] = pq.ParquetWriter(target, part_table.schema, compression="zstd")
                        partition_paths[key] = str(relative).replace("\\", "/")
                    writers[key].write_table(part_table)
                    row_count += len(partition)
                if progress_callback is not None:
                    progress_callback(processed_entries, total_entries, row_count)
        finally:
            for writer in writers.values():
                writer.close()
        metadata = {
            "dataset_version": index.dataset_version,
            "manifest_hash": index.manifest_hash,
            "summary_version": SESSION_SUMMARY_VERSION,
            "created_at": _now(),
            "complete_bar_count": complete_bar_count,
            "row_count": row_count,
            "source_row_count": source_row_count,
            "partitions": partition_paths,
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return cls.open(final_root, dataset_version=index.dataset_version, manifest_hash=index.manifest_hash)

    @classmethod
    def open(
        cls,
        root: str | Path,
        *,
        dataset_version: str,
        manifest_hash: str,
        summary_version: str = SESSION_SUMMARY_VERSION,
    ) -> "SessionSummaryStore":
        root = normalize_path(root)
        metadata_path = root / "metadata.json"
        if not metadata_path.is_file():
            raise DatasetIdentityError(f"session summary metadata is missing: {root}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        expected = {"dataset_version": dataset_version, "manifest_hash": manifest_hash, "summary_version": summary_version}
        actual = {key: metadata.get(key) for key in expected}
        if actual != expected:
            raise DatasetIdentityError(f"stale session summary identity: expected={expected}, actual={actual}")
        return cls(root=root, dataset_version=dataset_version, manifest_hash=manifest_hash, summary_version=summary_version)

    def lookup(self, symbol: str, start_date: int, end_date: int) -> pd.DataFrame:
        metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        frames: list[pd.DataFrame] = []
        start_period = int(start_date) // 100
        end_period = int(end_date) // 100
        selected_partitions = []
        for key, relative in metadata.get("partitions", {}).items():
            try:
                period = int(str(key).replace("-", ""))
            except ValueError:
                period = None
            if period is None or start_period <= period <= end_period:
                selected_partitions.append(relative)
        for relative in selected_partitions:
            frame = pd.read_parquet(
                self.root / relative,
                filters=[("symbol", "==", str(symbol)), ("trade_date", ">=", int(start_date)), ("trade_date", "<=", int(end_date))],
            )
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).sort_values(["trade_date", "symbol"]).reset_index(drop=True)

    def has_session(self, symbol: str, trade_date: int) -> bool:
        return not self.lookup(symbol, int(trade_date), int(trade_date)).empty


class IncrementalManifestVerifier:
    """Fail-closed FAST/FULL verifier for immutable manifest-listed parquet files."""

    def __init__(
        self,
        manifest_path: str | Path,
        cache_path: str | Path,
        *,
        dataset_version: str,
        manifest_hash: str,
        expected_manifest_hashes: Mapping[str | Path, str] | None = None,
        additional_manifest_hashes: Mapping[str | Path, str] | None = None,
    ) -> None:
        self.manifest_path = normalize_path(manifest_path)
        self.cache_path = normalize_path(cache_path)
        self.dataset_version = dataset_version
        self.manifest_hash = manifest_hash
        self.expected_manifest_hashes = {normalize_path(path): value for path, value in (expected_manifest_hashes or {}).items()}
        self.additional_manifest_hashes = {
            normalize_path(path): value for path, value in (additional_manifest_hashes or {}).items()
        }

    def _entries(self) -> list[dict[str, Any]]:
        return _load_manifest_entries(self.manifest_path)

    def _cache_identity(self) -> dict[str, Any]:
        return {
            "dataset_version": self.dataset_version,
            "manifest_hash": self.manifest_hash,
            "additional_manifest_hashes": {
                str(path).replace("\\", "/"): value
                for path, value in sorted(self.additional_manifest_hashes.items(), key=lambda item: str(item[0]))
            },
        }

    def verify(self, mode: str = FAST_VERIFY) -> dict[str, Any]:
        if mode not in {FAST_VERIFY, FULL_CRYPTO_VERIFY}:
            raise ValueError(f"unsupported verification mode: {mode}")
        started = time.perf_counter()
        entries = self._entries()
        if mode == FULL_CRYPTO_VERIFY:
            return self._full_verify(entries, started)
        return self._fast_verify(entries, started)

    def _full_verify(self, entries: list[dict[str, Any]], started: float) -> dict[str, Any]:
        manifest_hash = sha256_file(self.manifest_path)
        if manifest_hash != self.manifest_hash:
            raise DatasetIdentityError(f"normalized manifest hash mismatch: {manifest_hash} != {self.manifest_hash}")
        manifests = {str(self.manifest_path).replace("\\", "/"): manifest_hash}
        manifest_stats = {
            str(self.manifest_path).replace("\\", "/"): {
                "size_bytes": self.manifest_path.stat().st_size,
                "mtime_ns": self.manifest_path.stat().st_mtime_ns,
            }
        }
        for path, expected_hash in self.additional_manifest_hashes.items():
            actual_hash = sha256_file(path)
            if actual_hash != expected_hash:
                raise DatasetIdentityError(f"additional manifest hash mismatch: {path}")
            key = str(path).replace("\\", "/")
            manifests[key] = actual_hash
            manifest_stats[key] = {"size_bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
        files: dict[str, dict[str, Any]] = {}
        for entry in entries:
            path = normalize_path(entry["path"])
            stat = path.stat()
            actual_hash = sha256_file(path)
            expected_hash = str(entry.get("sha256", ""))
            if expected_hash and actual_hash != expected_hash:
                raise DatasetIdentityError(f"canonical parquet hash mismatch: {path}")
            files[str(path).replace("\\", "/")] = {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": actual_hash}
        cache = {
            **self._cache_identity(), "created_at": _now(), "manifests": manifests,
            "manifest_stats": manifest_stats, "files": files,
        }
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {
            "status": "PASS", "mode": FULL_CRYPTO_VERIFY, "ok": True,
            "files_checked": len(files), "hashes_computed": len(files) + len(manifests), "hashes_skipped": 0,
            "elapsed_seconds": time.perf_counter() - started,
        }

    def _fast_verify(self, entries: list[dict[str, Any]], started: float) -> dict[str, Any]:
        if not self.cache_path.is_file():
            raise IncrementalVerificationError("FAST_VERIFY requires a prior FULL_CRYPTO_VERIFY cache")
        cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        if {key: cache.get(key) for key in self._cache_identity()} != self._cache_identity():
            raise IncrementalVerificationError("FAST_VERIFY cache identity mismatch; run FULL_CRYPTO_VERIFY")
        changed: list[str] = []
        manifests = cache.get("manifests", {})
        manifest_paths = [self.manifest_path, *self.additional_manifest_hashes]
        for path in manifest_paths:
            key = str(path).replace("\\", "/")
            if key not in manifests:
                changed.append(key)
                continue
            try:
                stat = path.stat()
            except OSError:
                changed.append(key)
                continue
            previous = cache.get("manifest_stats", {}).get(key, {})
            if (not previous or int(previous.get("size_bytes", -1)) != stat.st_size or
                    int(previous.get("mtime_ns", -1)) != stat.st_mtime_ns):
                changed.append(key)
        files = cache.get("files", {})
        for entry in entries:
            path = normalize_path(entry["path"])
            key = str(path).replace("\\", "/")
            if key not in files:
                changed.append(key)
                continue
            stat = path.stat()
            previous = files[key]
            if int(previous.get("size_bytes", -1)) != stat.st_size or int(previous.get("mtime_ns", -1)) != stat.st_mtime_ns:
                changed.append(key)
        if changed:
            raise IncrementalVerificationError(f"FAST_VERIFY invalidated by {len(changed)} changed files; run FULL_CRYPTO_VERIFY")
        return {
            "status": "PASS", "mode": FAST_VERIFY, "ok": True,
            "files_checked": len(entries), "hashes_computed": 0, "hashes_skipped": len(entries) + len(manifests),
            "elapsed_seconds": time.perf_counter() - started,
        }


class CheckpointIdentityError(DatasetIdentityError):
    """Raised when a resume checkpoint belongs to a different computation."""


class ResearchCheckpoint:
    """Small, atomic, identity-bound checkpoint store for derived run state."""

    def __init__(self, path: str | Path, *, dataset_version: str, code_snapshot_hash: str,
                 config_hash: str, strategy_hash: str) -> None:
        self.path = normalize_path(path)
        self.identity = {
            "dataset_version": str(dataset_version),
            "code_snapshot_hash": str(code_snapshot_hash),
            "config_hash": str(config_hash),
            "strategy_hash": str(strategy_hash),
        }

    def _empty(self) -> dict[str, Any]:
        return {"identity": self.identity, "completed_units": {}, "updated_at": _now()}

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._empty()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("identity") != self.identity:
            raise CheckpointIdentityError("checkpoint identity mismatch; refuse incompatible resume")
        payload.setdefault("completed_units", {})
        return payload

    def reset(self) -> dict[str, Any]:
        payload = self._empty()
        self._write(payload)
        return payload

    def mark_completed(self, unit: str, *, artifact: str | None = None) -> dict[str, Any]:
        payload = self.load()
        payload["completed_units"][str(unit)] = {"artifact": artifact, "completed_at": _now()}
        payload["updated_at"] = _now()
        self._write(payload)
        return payload

    def is_completed(self, unit: str) -> bool:
        return str(unit) in self.load().get("completed_units", {})

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)


@dataclass(frozen=True)
class EventWindowKey:
    dataset_version: str
    manifest_hash: str
    symbol: str
    event_date: int
    window_definition: str
    cache_version: str = EVENT_WINDOW_CACHE_VERSION


class EventWindowCache:
    """Bounded deterministic LRU cache keyed by dataset and window identity."""

    def __init__(self, *, dataset_version: str, manifest_hash: str, max_items: int = 256, max_bytes: int = 512 * 1024 * 1024) -> None:
        self.dataset_version = dataset_version
        self.manifest_hash = manifest_hash
        self.max_items = int(max_items)
        self.max_bytes = int(max_bytes)
        self._items: OrderedDict[EventWindowKey, tuple[pd.DataFrame, int]] = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def _validate_key(self, key: EventWindowKey) -> None:
        if key.dataset_version != self.dataset_version or key.manifest_hash != self.manifest_hash:
            raise DatasetIdentityError("event window cache key identity does not match the active dataset")

    @staticmethod
    def _size(frame: pd.DataFrame) -> int:
        return int(frame.memory_usage(index=True, deep=True).sum())

    def get(self, key: EventWindowKey) -> pd.DataFrame | None:
        self._validate_key(key)
        item = self._items.get(key)
        if item is None:
            self.misses += 1
            return None
        self.hits += 1
        self._items.move_to_end(key)
        return item[0]

    def put(self, key: EventWindowKey, frame: pd.DataFrame) -> None:
        self._validate_key(key)
        size = self._size(frame)
        if key in self._items:
            _, old_size = self._items.pop(key)
            self._bytes -= old_size
        if size > self.max_bytes:
            return
        self._items[key] = (frame, size)
        self._bytes += size
        while len(self._items) > self.max_items or self._bytes > self.max_bytes:
            _, (_, old_size) = self._items.popitem(last=False)
            self._bytes -= old_size
            self.evictions += 1

    def get_or_load(self, key: EventWindowKey, loader: Callable[[], pd.DataFrame]) -> pd.DataFrame:
        cached = self.get(key)
        if cached is not None:
            return cached
        frame = loader()
        self.put(key, frame)
        return frame

    def stats(self) -> dict[str, int]:
        return {"items": len(self._items), "bytes": self._bytes, "hits": self.hits, "misses": self.misses, "evictions": self.evictions}


class ResearchMinuteReader:
    """Predicate-pruned reader over the derived minute dataset index."""

    def __init__(self, index: MinuteDatasetIndex, *, cache: EventWindowCache | None = None) -> None:
        self.index = index
        self.cache = cache

    def read_symbol_range(
        self,
        symbol: str,
        start_date: int,
        end_date: int,
        *,
        columns: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        columns = list(columns or ["symbol", "timestamp", "trade_date", "bar_time", "open", "high", "low", "close", "volume", "amount", "source"])
        frames: list[pd.DataFrame] = []
        for path in self.index.paths_for_range(symbol, start_date, end_date):
            table = pq.read_table(path, columns=columns, filters=[("symbol", "==", str(symbol)), ("trade_date", ">=", int(start_date)), ("trade_date", "<=", int(end_date))])
            frame = table.to_pandas()
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame(columns=columns)
        result = pd.concat(frames, ignore_index=True)
        result["trade_date"] = pd.to_numeric(result["trade_date"], errors="raise").astype(int)
        result = result[(result["trade_date"] >= int(start_date)) & (result["trade_date"] <= int(end_date))]
        return result.drop_duplicates(["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    def read_event_window(
        self,
        symbol: str,
        event_date: int,
        pre_sessions: int,
        post_sessions: int,
        calendar: Sequence[int],
        *,
        columns: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        try:
            position = list(calendar).index(int(event_date))
        except ValueError as exc:
            raise ValueError(f"event date is not in the supplied trading calendar: {event_date}") from exc
        start = max(0, position - int(pre_sessions))
        end = min(len(calendar) - 1, position + int(post_sessions))
        start_date = int(calendar[start])
        end_date = int(calendar[end])
        window_definition = f"pre={int(pre_sessions)};post={int(post_sessions)};columns={','.join(columns or [])}"
        if self.cache is None:
            return self.read_symbol_range(symbol, start_date, end_date, columns=columns)
        key = EventWindowKey(self.index.dataset_version, self.index.manifest_hash, str(symbol), int(event_date), window_definition)
        return self.cache.get_or_load(key, lambda: self.read_symbol_range(symbol, start_date, end_date, columns=columns))
