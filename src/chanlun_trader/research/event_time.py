"""Event time semantics: available_at / earliest_signal_at / earliest_execution.

Rule: YYYYMMDD+1 integer arithmetic is forbidden. Always use TradingCalendar
next session for date shifts, and timestamp-level available_at for events.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable

from chanlun_trader.engine.time_types import tz_aware


@dataclass(frozen=True)
class EventTimeSemantics:
    event_id: str
    event_time: int
    observation_complete_at: str
    source_publish_at: str | None
    available_at: str
    earliest_signal_at: str
    earliest_execution: str
    confidence: str = "HIGH"
    note: str = ""


LIMITUP_CLOSE_CONFIRMED = {
    "E_LIMITUP", "E_CONSEC_LIMIT", "E_FAILEDLIMIT", "E_LIMITUP_SEAL", "E_LIMITUP_SENT",
}

LHB_LOW_CONFIDENCE = {"E_LHB_INSTPOS", "E_LHB_BROKER"}


def next_session_date(calendar: Iterable[int], d: int) -> int:
    """Return the first calendar session strictly after d. Never uses +1 integer arithmetic."""
    best = None
    for c in calendar:
        c = int(c)
        if c > int(d):
            if best is None or c < best:
                best = c
    return best


def make_available_at_ts(d: int, hh: int, mm: int, ss: int = 0):
    return tz_aware(d // 10000, (d // 100) % 100, d % 100, hh, mm, ss)


def semantics_for(event_id: str, event_date: int, calendar: Iterable[int]) -> EventTimeSemantics:
    cal = sorted(set(int(c) for c in calendar))
    if event_id in LIMITUP_CLOSE_CONFIRMED:
        avail = make_available_at_ts(event_date, 15, 0, 1)
        sig = make_available_at_ts(event_date, 15, 0, 1)
        ex = next_session_date(cal, event_date)
        return EventTimeSemantics(
            event_id=event_id, event_time=event_date,
            observation_complete_at=f"{event_date} 15:00:00 Asia/Shanghai (close-confirmed)",
            source_publish_at=f"{event_date} 15:00:00+ (derived from daily bar close)",
            available_at=avail.isoformat(), earliest_signal_at=sig.isoformat(),
            earliest_execution=f"{ex} 09:30:00 Asia/Shanghai (NEXT_SESSION_OPEN)",
            confidence="HIGH",
            note="Limit-up/consec/failed/seal state is only final after T close; available_at=T 15:00:01, execution=T+1 open.")
    if event_id in LHB_LOW_CONFIDENCE:
        ex = next_session_date(cal, event_date)
        return EventTimeSemantics(
            event_id=event_id, event_time=event_date,
            observation_complete_at=f"{event_date} close (data vendor aggregation)",
            source_publish_at=None,
            available_at=make_available_at_ts(ex, 8, 0, 0).isoformat(),
            earliest_signal_at=make_available_at_ts(ex, 8, 1, 0).isoformat(),
            earliest_execution=f"{ex} 09:30:00 Asia/Shanghai (NEXT_SESSION_OPEN)",
            confidence="LOW",
            note="TQ/TDX LHB publish time not confirmed; conservative T+1 08:00. PROMOTION_CEILING=PROMISING.")
    # Default: close-confirmed on event day
    avail = make_available_at_ts(event_date, 15, 0, 1)
    ex = next_session_date(cal, event_date)
    return EventTimeSemantics(
        event_id=event_id, event_time=event_date,
        observation_complete_at=f"{event_date} 15:00:00 Asia/Shanghai (close-confirmed default)",
        source_publish_at=None,
        available_at=avail.isoformat(), earliest_signal_at=avail.isoformat(),
        earliest_execution=f"{ex} 09:30:00 Asia/Shanghai (NEXT_SESSION_OPEN)",
        confidence="MEDIUM",
        note="Default close-confirmed semantics.")
