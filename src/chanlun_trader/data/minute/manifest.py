"""5m RAW/normalized 存储与追加式 lineage manifest。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_frame(df: pd.DataFrame) -> str:
    normalized = df.copy()
    for col in normalized.columns:
        if pd.api.types.is_datetime64_any_dtype(normalized[col]):
            normalized[col] = normalized[col].astype(str)
    payload = normalized.to_json(orient="records", date_format="iso", double_precision=15)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ManifestStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, Any]) -> None:
        record = dict(record)
        record.setdefault("recorded_at", datetime.now().astimezone().isoformat())
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str, sort_keys=True) + "\n")

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]


class ImmutableRawStore:
    """只新增文件，不覆盖已有 RAW；同区间不同内容产生 DATA_CONFLICT。"""

    def __init__(self, root: str | Path = "data/market_raw/baostock/5m"):
        self.root = Path(root)
        self.manifest = ManifestStore(self.root / "manifest.jsonl")
        self.root.mkdir(parents=True, exist_ok=True)

    def write_chunk(self, symbol: str, start_date: str, end_date: str,
                    df: pd.DataFrame, metadata: dict[str, Any] | None = None) -> tuple[Path, str, str]:
        safe = symbol.replace(".", "_")
        directory = self.root / f"symbol={safe}" / f"year={start_date[:4]}"
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"{start_date}_{end_date}"
        candidate = directory / f"{stem}.parquet"
        temp = directory / f".{stem}.tmp.parquet"
        df.to_parquet(temp, index=False)
        checksum = sha256_file(temp)
        status = "WRITTEN"
        target = candidate
        if candidate.exists():
            old_checksum = sha256_file(candidate)
            if old_checksum == checksum:
                temp.unlink()
                status = "UNCHANGED"
            else:
                target = directory / f"{stem}.conflict-{checksum[:12]}.parquet"
                temp.replace(target)
                status = "DATA_CONFLICT"
        else:
            temp.replace(candidate)
        record = {
            "symbol": symbol, "requested_start": start_date, "requested_end": end_date,
            "path": str(target), "status": status, "sha256": checksum,
            "row_count": len(df), "source": "baostock", **(metadata or {}),
        }
        self.manifest.append(record)
        return target, checksum, status


class Normalized5mStore:
    def __init__(self, root: str | Path = "data/research/market_5m"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, df: pd.DataFrame) -> list[Path]:
        paths: list[Path] = []
        if df.empty:
            return paths
        for (year, month), part in df.groupby([df["trade_date"] // 10000,
                                               (df["trade_date"] // 100) % 100]):
            directory = self.root / f"year={int(year)}" / f"month={int(month):02d}"
            directory.mkdir(parents=True, exist_ok=True)
            checksum = sha256_frame(part)
            path = directory / f"part-{checksum[:16]}.parquet"
            if not path.exists():
                part.to_parquet(path, index=False)
            paths.append(path)
        return paths
