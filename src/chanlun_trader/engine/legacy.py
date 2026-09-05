"""V1 -> V2 Legacy 适配层。

把旧 Signal(code, signal_date: int, ...) 映射为 V2 Signal:
    generated_at = signal_date 15:00 (日线收盘后)
    execution_policy = NEXT_SESSION_OPEN
    direction 由旧 direction 字段映射
"""
from __future__ import annotations

from typing import List

import pandas as pd

from .signal import ExecutionPolicy, Signal, Side
from .time_types import tz_aware


class LegacySignalAdapter:
    @staticmethod
    def from_v1(v1_signal) -> Signal:
        d = int(v1_signal.signal_date)
        y, m, day = d // 10000, (d // 100) % 100, d % 100
        generated = tz_aware(y, m, day, 15, 0)
        direction = Side.BUY
        if getattr(v1_signal, "direction", None) is not None:
            raw = str(getattr(v1_signal, "direction", "BUY")).upper()
            direction = Side.SELL if raw in ("SELL", "SHORT", "EXIT") else Side.BUY
        return Signal(
            strategy_id="legacy",
            signal_id=f"legacy-{v1_signal.code}-{d}-{getattr(v1_signal, 'signal_type', 'GENERIC')}",
            symbol=v1_signal.code,
            generated_at=generated,
            direction=direction,
            score=float(getattr(v1_signal, "score", 0.0) or 0.0),
            signal_type=str(getattr(v1_signal, "signal_type", "GENERIC")),
            execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
            metadata={
                "legacy_signal_date": d,
                "legacy_stop_low": float(getattr(v1_signal, "stop_low", 0.0) or 0.0),
            },
        )

    @classmethod
    def convert_many(cls, v1_signals) -> List[Signal]:
        return [cls.from_v1(s) for s in v1_signals]
