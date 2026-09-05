"""Event Registry / EventStore。"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import pandas as pd

from .guard import ResearchDataAccessGuard

DEFAULT_EVENT_DIR = Path("data/research/event_registry")
DEFAULT_EVENT_STORE_DIR = Path("data/research/event_store")


@dataclass
class EventDefinition:
    event_id: str
    version: str
    name: str
    family: str
    description: str
    event_time_semantics: str
    available_at_semantics: str
    PIT_safe: bool
    inputs: list
    created_at: str
    status: str = "DISCOVERED"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EventDefinition":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


class EventRegistry:
    def __init__(self, path: Path = DEFAULT_EVENT_DIR / "registry.json"):
        self.path = Path(path)
        self._items: dict[tuple[str, str], EventDefinition] = {}

    def register(self, e: EventDefinition) -> None:
        self._items[(e.event_id, e.version)] = e

    def get(self, event_id: str, version: str | None = None) -> EventDefinition | None:
        if version:
            return self._items.get((event_id, version))
        matches = [e for (eid, _), e in self._items.items() if eid == event_id]
        return max(matches, key=lambda e: e.version) if matches else None

    def items(self) -> list:
        return list(self._items.values())

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"events": [e.to_dict() for e in self._items.values()]}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.path

    @classmethod
    def load(cls, path: Path = DEFAULT_EVENT_DIR / "registry.json") -> "EventRegistry":
        reg = cls(path)
        p = Path(path)
        if p.exists():
            payload = json.loads(p.read_text(encoding="utf-8"))
            for d in payload.get("events", []):
                reg.register(EventDefinition.from_dict(d))
        return reg


class EventStore:
    """统一 Event Store：event_id, event_version, symbol, event_time, available_at, event_type, payload_json。

    event_type in {ENTER, STAY, EXIT, REENTER}。available_at <= decision_time 才可读。
    """

    def __init__(self, root: Path = DEFAULT_EVENT_STORE_DIR, guard: ResearchDataAccessGuard | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.guard = guard or ResearchDataAccessGuard()

    def _path(self, event_id: str, version: str) -> Path:
        return self.root / f"{event_id}_{version}.parquet"

    def put(self, event_id: str, version: str, df: pd.DataFrame, allow_overwrite: bool = False) -> Path:
        if df.empty:
            raise ValueError("empty event frame")
        for col in ("symbol", "event_time", "available_at", "event_type"):
            if col not in df.columns:
                raise ValueError(f"missing column {col}")
        path = self._path(event_id, version)
        if path.exists() and not allow_overwrite:
            raise FileExistsError(f"event version already exists: {path}")
        self.guard.check_frame(df, "event_time")
        df = df.copy()
        df["event_id"] = event_id
        df["event_version"] = version
        if "payload_json" not in df.columns:
            df["payload_json"] = "{}"
        df.to_parquet(path, index=False)
        return path

    def query(self, event_id: str, version: str, symbol: str | None = None,
              start: int = 0, end: int = 99_999_999, as_of: int | None = None) -> pd.DataFrame:
        path = self._path(event_id, version)
        if not path.exists():
            return pd.DataFrame()
        df = pd.read_parquet(path)
        df = df[(df["event_time"] >= start) & (df["event_time"] <= end)]
        if symbol:
            df = df[df["symbol"] == symbol]
        if as_of is not None:
            self.guard.check_date(as_of, f"event {event_id} as_of")
            df = df[df["available_at"] <= as_of]
        return df.reset_index(drop=True)
