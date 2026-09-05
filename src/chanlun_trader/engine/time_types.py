"""统一时间类型 — 所有 V2 时间必须是 timezone-aware datetime (Asia/Shanghai)。

禁止继续使用 signal_date:int 作为引擎唯一时间概念。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Iterable, List, Optional, Sequence, Tuple

import pandas as pd
import pytz

TZ = "Asia/Shanghai"
_TZINFO = pytz.timezone(TZ)


def tz_aware(y: int, m: int, d: int, hh: int = 0, mm: int = 0, ss: int = 0) -> pd.Timestamp:
    return pd.Timestamp(datetime(y, m, d, hh, mm, ss), tz=TZ)


def ensure_aware(ts) -> pd.Timestamp:
    if isinstance(ts, pd.Timestamp):
        t = ts
    else:
        t = pd.Timestamp(ts)
    if t.tzinfo is None:
        return t.tz_localize(_TZINFO)
    if getattr(t.tz, "zone", None) == TZ or str(t.tz) == TZ:
        return t
    return t.tz_convert(TZ)


def date_key(ts) -> int:
    """YYYYMMDD int key from a timestamp (in Asia/Shanghai)."""
    t = ensure_aware(ts)
    return int(t.strftime("%Y%m%d"))


class SessionKind(str, Enum):
    MORNING = "MORNING"
    LUNCH_BREAK = "LUNCH_BREAK"
    AFTERNOON = "AFTERNOON"


class EventKind(str, Enum):
    """Clock event kinds, ordered by time-of-day within one trading day."""
    SESSION_OPEN = "SESSION_OPEN"          # 09:30 开盘
    BAR_OPEN = "BAR_OPEN"                  # 5分钟 bar 开始（仅 intraday）
    BAR_CLOSE = "BAR_CLOSE"                # 5分钟/日线 bar 完成
    SESSION_CLOSE = "SESSION_CLOSE"        # 15:00 收盘
    AFTER_CLOSE = "AFTER_CLOSE"            # 15:30 盘后事件数据可用


# A-share session times (exchange time)
MORNING_OPEN = time(9, 30)
MORNING_CLOSE = time(11, 30)
AFTERNOON_OPEN = time(13, 0)
AFTERNOON_CLOSE = time(15, 0)
AFTER_CLOSE_TIME = time(15, 30)

# 5-minute bar boundaries for a normal A-share session
_BAR_MINUTES = 5


@dataclass(frozen=True)
class TradingSession:
    date: int  # YYYYMMDD
    session: SessionKind
    start: pd.Timestamp
    end: pd.Timestamp
    is_trading: bool = True


@dataclass
class TradingCalendar:
    """交易日历 + 会话定义。输入为 YYYYMMDD 的交易日列表（升序）。"""

    trading_days: List[int] = field(default_factory=list)

    def __post_init__(self):
        self.trading_days = sorted(set(int(d) for d in self.trading_days))
        self._pos = {d: i for i, d in enumerate(self.trading_days)}

    def contains(self, d: int) -> bool:
        return int(d) in self._pos

    def next_day(self, d: int, offset: int = 1) -> Optional[int]:
        idx = self._pos.get(int(d))
        if idx is None:
            raise KeyError(f"{d} is not a trading day")
        j = idx + offset
        if 0 <= j < len(self.trading_days):
            return self.trading_days[j]
        return None

    def prev_day(self, d: int, offset: int = 1) -> Optional[int]:
        return self.next_day(d, -offset)

    def date_index(self, d: int) -> int:
        return self._pos[int(d)]

    def slice(self, start_date: int, end_date: int) -> "TradingCalendar":
        days = [d for d in self.trading_days if start_date <= d <= end_date]
        return TradingCalendar(days)

    @staticmethod
    def session_start_ts(d: int, session: SessionKind) -> pd.Timestamp:
        y, m, day = d // 10000, (d // 100) % 100, d % 100
        if session == SessionKind.MORNING:
            return tz_aware(y, m, day, MORNING_OPEN.hour, MORNING_OPEN.minute)
        if session == SessionKind.AFTERNOON:
            return tz_aware(y, m, day, AFTERNOON_OPEN.hour, AFTERNOON_OPEN.minute)
        return tz_aware(y, m, day, 11, 30)

    @staticmethod
    def session_end_ts(d: int, session: SessionKind) -> pd.Timestamp:
        y, m, day = d // 10000, (d // 100) % 100, d % 100
        if session == SessionKind.MORNING:
            return tz_aware(y, m, day, MORNING_CLOSE.hour, MORNING_CLOSE.minute)
        if session == SessionKind.AFTERNOON:
            return tz_aware(y, m, day, AFTERNOON_CLOSE.hour, AFTERNOON_CLOSE.minute)
        return tz_aware(y, m, day, 13, 0)


@dataclass(frozen=True)
class ClockEvent:
    kind: EventKind
    timestamp: pd.Timestamp
    date: int  # YYYYMMDD

    def __lt__(self, other: "ClockEvent") -> bool:
        if self.timestamp != other.timestamp:
            return self.timestamp < other.timestamp
        return self.kind.value < other.kind.value


class TradingClock:
    """混合事件驱动时钟。

    DAILY 模式: 每天 SESSION_OPEN(09:30) -> BAR_CLOSE(15:00, 代表日线bar完成) -> AFTER_CLOSE(15:30)
    5MIN 模式:   每天 SESSION_OPEN -> BAR_CLOSE(每5分钟) -> SESSION_CLOSE -> AFTER_CLOSE
    """

    def __init__(self, calendar: TradingCalendar, mode: str = "DAILY"):
        if mode not in ("DAILY", "5MIN"):
            raise ValueError("mode must be DAILY or 5MIN")
        self.calendar = calendar
        self.mode = mode
        self.events: List[ClockEvent] = self._build_events()

    def _build_events(self) -> List[ClockEvent]:
        evs: List[ClockEvent] = []
        for d in self.calendar.trading_days:
            y, m, day = d // 10000, (d // 100) % 100, d % 100
            evs.append(ClockEvent(EventKind.SESSION_OPEN, tz_aware(y, m, day, 9, 30), d))
            if self.mode == "5MIN":
                for sess, start_h, start_m, end_h, end_m in (
                    (SessionKind.MORNING, 9, 35, 11, 30),
                    (SessionKind.AFTERNOON, 13, 5, 15, 0),
                ):
                    t = datetime(y, m, day, start_h, start_m)
                    end = datetime(y, m, day, end_h, end_m)
                    while t <= end:
                        evs.append(ClockEvent(EventKind.BAR_CLOSE, pd.Timestamp(t, tz=TZ), d))
                        t += timedelta(minutes=_BAR_MINUTES)
            else:
                evs.append(ClockEvent(EventKind.BAR_CLOSE, tz_aware(y, m, day, 15, 0), d))
            evs.append(ClockEvent(EventKind.SESSION_CLOSE, tz_aware(y, m, day, 15, 0), d))
            evs.append(ClockEvent(EventKind.AFTER_CLOSE, tz_aware(y, m, day, 15, 30), d))
        evs.sort(key=lambda e: (e.timestamp, e.kind.value))
        return evs

    def next_bar_ts(self, symbol: str, ts: pd.Timestamp) -> pd.Timestamp:
        """给定时间之后的下一个 bar close（对日线返回次日 15:00）。"""
        d = date_key(ts)
        if self.mode == "5MIN":
            for ev in self.events:
                if ev.kind == EventKind.BAR_CLOSE and ev.timestamp > ensure_aware(ts):
                    return ev.timestamp
            return ensure_aware(ts)
        nd = self.calendar.next_day(d)
        if nd is None:
            return ensure_aware(ts)
        y, m, day = nd // 10000, (nd // 100) % 100, nd % 100
        return tz_aware(y, m, day, 15, 0)

    def next_session_open_ts(self, ts: pd.Timestamp) -> pd.Timestamp:
        d = date_key(ts)
        nd = self.calendar.next_day(d)
        if nd is None:
            return ensure_aware(ts)
        y, m, day = nd // 10000, (nd // 100) % 100, nd % 100
        return tz_aware(y, m, day, 9, 30)

    def events_between(self, start_ts, end_ts) -> List[ClockEvent]:
        s, e = ensure_aware(start_ts), ensure_aware(end_ts)
        return [ev for ev in self.events if s <= ev.timestamp <= e]
