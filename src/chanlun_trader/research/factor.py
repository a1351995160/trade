"""Factor Registry / FactorStore / FactorLineage。"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import pandas as pd

from .guard import ResearchDataAccessGuard

DEFAULT_FACTOR_DIR = Path("data/research/factor_registry")
DEFAULT_FACTOR_STORE_DIR = Path("data/research/factor_store")


@dataclass
class FactorDefinition:
    factor_id: str
    version: str
    name: str
    family: str
    description: str
    formula: str
    inputs: list
    lookback: int
    frequency: str
    available_at_rule: str
    feature_price_mode: str
    PIT_safe: bool
    direction_hint: str
    created_at: str
    status: str = "DISCOVERED"   # DISCOVERED / PROMISING / ROBUST / REJECTED / DEPRECATED

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FactorDefinition":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


class FactorRegistry:
    def __init__(self, path: Path = DEFAULT_FACTOR_DIR / "registry.json"):
        self.path = Path(path)
        self._items: dict[tuple[str, str], FactorDefinition] = {}

    def register(self, f: FactorDefinition) -> None:
        if f.version != "v1" and f.version in ("",):
            raise ValueError("factor_version is required")
        self._items[(f.factor_id, f.version)] = f

    def get(self, factor_id: str, version: str | None = None) -> FactorDefinition | None:
        if version:
            return self._items.get((factor_id, version))
        matches = [f for (fid, _), f in self._items.items() if fid == factor_id]
        return max(matches, key=lambda f: f.version) if matches else None

    def items(self) -> list:
        return list(self._items.values())

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"factors": [f.to_dict() for f in self._items.values()]}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.path

    @classmethod
    def load(cls, path: Path = DEFAULT_FACTOR_DIR / "registry.json") -> "FactorRegistry":
        reg = cls(path)
        p = Path(path)
        if p.exists():
            payload = json.loads(p.read_text(encoding="utf-8"))
            for d in payload.get("factors", []):
                reg.register(FactorDefinition.from_dict(d))
        return reg


class FactorStore:
    """统一 Factor Store：factor_id, factor_version, symbol, timestamp, value, available_at。

    禁止每个实验重算自己的版本；版本不可覆盖。可用 as_of 查询做 PIT 读取。
    """

    def __init__(self, root: Path = DEFAULT_FACTOR_STORE_DIR, guard: ResearchDataAccessGuard | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.guard = guard or ResearchDataAccessGuard()

    def _path(self, factor_id: str, version: str) -> Path:
        return self.root / f"{factor_id}_{version}.parquet"

    def put(self, factor_id: str, version: str, df: pd.DataFrame, allow_overwrite: bool = False) -> Path:
        if df.empty:
            raise ValueError("empty factor frame")
        for col in ("symbol", "timestamp", "value", "available_at"):
            if col not in df.columns:
                raise ValueError(f"missing column {col}")
        path = self._path(factor_id, version)
        if path.exists() and not allow_overwrite:
            raise FileExistsError(f"factor version already exists: {path}")
        self.guard.check_frame(df, "timestamp")
        df = df.copy()
        df["factor_id"] = factor_id
        df["factor_version"] = version
        df = df[["factor_id", "factor_version", "symbol", "timestamp", "value", "available_at"]]
        df.to_parquet(path, index=False)
        return path

    def query(self, factor_id: str, version: str, symbol: str | None = None,
              start: int = 0, end: int = 99_999_999, as_of: int | None = None) -> pd.DataFrame:
        path = self._path(factor_id, version)
        if not path.exists():
            return pd.DataFrame()
        df = pd.read_parquet(path)
        df = df[(df["timestamp"] >= start) & (df["timestamp"] <= end)]
        if symbol:
            df = df[df["symbol"] == symbol]
        if as_of is not None:
            self.guard.check_date(as_of, f"factor {factor_id} as_of")
            df = df[df["available_at"] <= as_of]
        return df.reset_index(drop=True)


class FactorLineage:
    """Raw Data -> Transformation -> Factor -> Experiment -> Strategy -> Daily Candidate 追踪。"""

    def __init__(self, path: Path = DEFAULT_FACTOR_DIR / "lineage.jsonl"):
        self.path = Path(path)

    def record(self, factor_id: str, factor_version: str, raw_inputs: list, transformation: str,
               experiment_id: str | None = None, strategy_id: str | None = None,
               notes: str = "") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "factor_id": factor_id, "factor_version": factor_version,
            "raw_inputs": raw_inputs, "transformation": transformation,
            "experiment_id": experiment_id, "strategy_id": strategy_id,
            "notes": notes, "recorded_at": pd.Timestamp.now().isoformat(),
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame()
        return pd.read_json(self.path, lines=True)
