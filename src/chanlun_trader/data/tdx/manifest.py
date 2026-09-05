"""数据 Manifest：记录每次下载/缓存的元信息。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pandas as pd

MANIFEST_PATH = Path("data/tdx/data_manifest.parquet")

_COLUMNS = [
    "source", "dataset", "field", "start_date", "end_date", "actual_start", "actual_end",
    "rows", "symbols", "download_time", "skill", "provider_version", "checksum", "errors", "status",
]


def record_manifest(record: dict[str, Any]) -> None:
    record.setdefault("download_time", time.strftime("%Y-%m-%d %H:%M:%S"))
    record.setdefault("skill", "tdx-tq-local+tdx-quant")
    record.setdefault("provider_version", "1.0.0")
    record.setdefault("status", "OK")
    row = pd.DataFrame([record])
    if MANIFEST_PATH.exists():
        old = pd.read_parquet(MANIFEST_PATH)
        df = pd.concat([old, row], ignore_index=True)
    else:
        df = row
    df = df[["source", "dataset", "field", "start_date", "end_date", "actual_start", "actual_end",
             "rows", "symbols", "download_time", "skill", "provider_version", "checksum", "errors", "status"]]
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(MANIFEST_PATH, index=False)


def load_manifest() -> pd.DataFrame:
    if MANIFEST_PATH.exists():
        return pd.read_parquet(MANIFEST_PATH)
    return pd.DataFrame(columns=_COLUMNS)
