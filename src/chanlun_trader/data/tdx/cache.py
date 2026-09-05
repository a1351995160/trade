"""Parquet 缓存：data/tdx/raw 与 data/tdx/clean 分离。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

RAW_DIR = Path("data/tdx/raw")
CLEAN_DIR = Path("data/tdx/clean")


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def save_raw(dataset: str, start: str, end: str, payload: Any, symbols: list[str] | None = None) -> Path:
    """保存 TQ 原始返回。"""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    key = _hash(f"{dataset}|{start}|{end}|{symbols or []}")
    path = RAW_DIR / f"{dataset}_{start}_{end}_{key}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def load_raw(dataset: str, start: str, end: str, symbols: list[str] | None = None) -> Any | None:
    key = _hash(f"{dataset}|{start}|{end}|{symbols or []}")
    path = RAW_DIR / f"{dataset}_{start}_{end}_{key}.json"
    if not path.exists():
        # 同名数据集取最新
        cands = sorted(RAW_DIR.glob(f"{dataset}_{start}_{end}_*.json"))
        if cands:
            return json.loads(cands[-1].read_text(encoding="utf-8"))
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_clean(dataset: str, df: pd.DataFrame, manifest: dict | None = None) -> Path:
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    path = CLEAN_DIR / f"{dataset}.parquet"
    df.to_parquet(path, index=False)
    if manifest:
        meta = CLEAN_DIR / f"{dataset}.meta.json"
        meta.write_text(json.dumps(manifest, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def load_clean(dataset: str) -> pd.DataFrame | None:
    path = CLEAN_DIR / f"{dataset}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return None


def raw_checksum(payload: Any) -> str:
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
