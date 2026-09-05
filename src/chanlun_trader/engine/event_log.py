"""V2 BacktestEventLog — 事件日志与追溯。"""
from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional

from .events import BacktestEvent


class BacktestEventLog:
    def __init__(self):
        self._events: List[BacktestEvent] = []
        self._seq = 0

    def log(self, ev: BacktestEvent) -> BacktestEvent:
        self._seq += 1
        ev.event_id = f"evt-{self._seq:06d}"
        self._events.append(ev)
        return ev

    def by_order(self, order_id: str) -> List[BacktestEvent]:
        return [e for e in self._events if e.order_id == order_id]

    def by_symbol(self, symbol: str) -> List[BacktestEvent]:
        return [e for e in self._events if e.symbol == symbol]

    def to_records(self) -> List[dict]:
        out = []
        for e in self._events:
            d = asdict(e)
            d["timestamp"] = str(e.timestamp)
            out.append(d)
        return out

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)
