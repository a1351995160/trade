"""FactorLibrary / Alpha Independence / Duplicate Alpha。"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import pandas as pd

from .factor import FactorRegistry, FactorDefinition

DEFAULT_FACTOR_LIBRARY_PATH = Path("data/research/factor_library/library.json")


@dataclass
class FactorLibraryEntry:
    factor_id: str
    version: str
    status: str          # DISCOVERED / PROMISING / ROBUST / REJECTED / DEPRECATED
    ic_mean: float | None = None
    rank_ic_mean: float | None = None
    icir: float | None = None
    n_obs: int = 0
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FactorLibraryEntry":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


class FactorLibrary:
    def __init__(self, path: Path = DEFAULT_FACTOR_LIBRARY_PATH):
        self.path = Path(path)
        self._items: dict[tuple[str, str], FactorLibraryEntry] = {}

    def put(self, entry: FactorLibraryEntry) -> None:
        self._items[(entry.factor_id, entry.version)] = entry

    def get(self, factor_id: str, version: str | None = None) -> FactorLibraryEntry | None:
        if version:
            return self._items.get((factor_id, version))
        matches = [e for (fid, _), e in self._items.items() if fid == factor_id]
        return max(matches, key=lambda e: e.version) if matches else None

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"factors": [e.to_dict() for e in self._items.values()]}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.path

    @classmethod
    def load(cls, path: Path = DEFAULT_FACTOR_LIBRARY_PATH) -> "FactorLibrary":
        lib = cls(path)
        p = Path(path)
        if p.exists():
            payload = json.loads(p.read_text(encoding="utf-8"))
            for d in payload.get("factors", []):
                lib.put(FactorLibraryEntry.from_dict(d))
        return lib


def daily_return_correlation(daily_equity_a: pd.Series, daily_equity_b: pd.Series) -> float:
    a = daily_equity_a.pct_change().dropna()
    b = daily_equity_b.pct_change().dropna()
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(j) < 5:
        return float("nan")
    return float(j.iloc[:, 0].corr(j.iloc[:, 1]))


def signal_overlap(signals_a: pd.DataFrame, signals_b: pd.DataFrame) -> float:
    """signals DataFrame: symbol, timestamp。返回 Jaccard overlap。"""
    if signals_a.empty or signals_b.empty:
        return 0.0
    sa = set(zip(signals_a["symbol"], signals_a["timestamp"]))
    sb = set(zip(signals_b["symbol"], signals_b["timestamp"]))
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def trade_overlap(trades_a: pd.DataFrame, trades_b: pd.DataFrame) -> float:
    """trades DataFrame: symbol, sell_date。返回卖出日 Jaccard overlap。"""
    if trades_a.empty or trades_b.empty:
        return 0.0
    sa = set(zip(trades_a["symbol"], trades_a["sell_date"]))
    sb = set(zip(trades_b["symbol"], trades_b["sell_date"]))
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def classify_duplicate(overlap: float, return_corr: float, threshold: float = 0.7) -> str:
    if overlap >= threshold or return_corr >= threshold:
        return "VARIANT"
    return "INDEPENDENT"
