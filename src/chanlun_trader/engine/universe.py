"""V2 UniverseService — 禁止 union=所有曾入选股票 成为交易准入依据。"""
from __future__ import annotations

from typing import Dict, Set

import pandas as pd

from .time_types import date_key, ensure_aware


class UniverseService:
    def __init__(self):
        self._sets: Dict[int, Set[str]] = {}

    def set_universe(self, d: int, symbols):
        self._sets[int(d)] = set(symbols)

    def load_pit_sets(self, mapping: Dict[int, Set[str]]):
        self._sets.update({int(k): set(v) for k, v in mapping.items()})

    def is_eligible(self, symbol: str, ts) -> bool:
        d = date_key(ensure_aware(ts))
        keys = sorted(k for k in self._sets if k <= d)
        if not keys:
            return False
        return symbol in self._sets[keys[-1]]

    def snapshot(self, ts) -> Set[str]:
        d = date_key(ensure_aware(ts))
        keys = sorted(k for k in self._sets if k <= d)
        if not keys:
            return set()
        return set(self._sets[keys[-1]])
