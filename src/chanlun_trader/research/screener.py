"""ScreenerRule / DynamicGroup：同花顺动态分组规则 -> ENTER/STAY/EXIT/REENTER。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

import pandas as pd


class FieldClass(str, Enum):
    NATIVE = "NATIVE"
    PROXY = "PROXY"
    EXTERNAL_REQUIRED = "EXTERNAL_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class ScreenerFieldSpec:
    field: str
    field_class: FieldClass
    proxy_of: Optional[str] = None
    note: str = ""


@dataclass
class ScreenerRule:
    rule_id: str
    name: str
    description: str
    fields: list = field(default_factory=list)          # ScreenerFieldSpec
    condition: Optional[Callable[[pd.DataFrame], pd.Series]] = None  # bool series per symbol

    def match(self, data: pd.DataFrame) -> pd.Series:
        if self.condition is None:
            raise ValueError(f"rule {self.rule_id} has no condition")
        return self.condition(data)


class DynamicGroup:
    """将每日 match=True 转为 ENTER/STAY/EXIT/REENTER 事件。

    规则：
    - 上一日 not matched，今日 matched -> ENTER
    - 上一日 matched，今日 matched -> STAY
    - 上一日 matched，今日 not matched -> EXIT
    - 之前 EXIT 过，今日再次 matched -> REENTER
    """

    def __init__(self, group_id: str, rule: ScreenerRule):
        self.group_id = group_id
        self.rule = rule
        self._prev: dict[str, bool] = {}
        self._ever_exited: dict[str, bool] = {}

    def events_for_day(self, symbols: list, matched_flags: pd.Series) -> pd.DataFrame:
        """matched_flags: index=symbol, value=bool。"""
        rows = []
        matched = set(matched_flags[matched_flags].index)
        for sym in symbols:
            now = sym in matched
            prev = self._prev.get(sym, False)
            if now and not prev:
                etype = "REENTER" if self._ever_exited.get(sym, False) else "ENTER"
            elif now and prev:
                etype = "STAY"
            elif not now and prev:
                etype = "EXIT"
                self._ever_exited[sym] = True
            else:
                etype = None
            self._prev[sym] = now
            if etype:
                rows.append({"symbol": sym, "event_type": etype})
        return pd.DataFrame(rows)

    def reset(self) -> None:
        self._prev.clear()
        self._ever_exited.clear()
