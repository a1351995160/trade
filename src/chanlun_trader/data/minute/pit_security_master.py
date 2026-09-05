"""最小、显式未知语义的 PIT Security Master。

该模块只把可追溯的上市/退市日期作为生命周期证据；当前证券快照不被
当作历史 ST 或停牌状态。停牌与 ST 独立保留为 UNKNOWN，除非调用方提供
带日期的明确证据。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

import json

from .base import normalize_symbol


class SecurityState(StrEnum):
    NOT_LISTED = "NOT_LISTED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELISTED = "DELISTED"
    UNKNOWN = "UNKNOWN"


def _date_text(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def _date_value(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def board_for_symbol(symbol: str, manifest_board: str | None = None) -> str:
    """返回固定 manifest board；没有 manifest 时只按代码前缀做静态归类。"""
    if manifest_board:
        return str(manifest_board)
    code, market = normalize_symbol(symbol).split(".")
    if market == "SH" and code.startswith("68"):
        return "STAR"
    if market == "SH":
        return "SH_MAIN"
    if code.startswith("3"):
        return "GEM"
    return "SZ_MAIN"


@dataclass(frozen=True)
class PITSecurityRecord:
    symbol: str
    exchange: str
    board: str
    list_date: str | None
    delist_date: str | None
    valid_from: str | None
    valid_to: str | None
    pit_listing_status: str
    pit_delist_status: str
    pit_st_status: str
    pit_suspension_status: str
    source: str
    evidence: str
    code_name: str | None = None

    def as_of(self, asof_date: int | str | date,
              market_data_present: bool | None = None,
              suspension_status: str | None = None) -> dict[str, str | None]:
        """以给定日期返回生命周期状态及独立的停牌状态。

        `market_data_present` 只能作为带日期的外部观测证据；缺少该证据时，
        生命周期仍可判断，但不会把“没有分钟数据”推断成停牌。
        """
        day = _as_date(asof_date)
        listed = _date_value(self.list_date)
        delisted = _date_value(self.delist_date)
        if listed is None:
            state = SecurityState.UNKNOWN
        elif day < listed:
            state = SecurityState.NOT_LISTED
        elif delisted is not None and day > delisted:
            state = SecurityState.DELISTED
        elif suspension_status == SecurityState.SUSPENDED.value:
            state = SecurityState.SUSPENDED
        elif market_data_present is False and suspension_status is None:
            state = SecurityState.UNKNOWN
        elif market_data_present is True:
            state = SecurityState.ACTIVE
        else:
            state = SecurityState.ACTIVE
        suspension = suspension_status or self.pit_suspension_status
        return {
            "symbol": self.symbol,
            "asof_date": day.isoformat(),
            "security_state": state.value,
            "pit_listing_status": self.pit_listing_status,
            "pit_delist_status": self.pit_delist_status,
            "pit_st_status": self.pit_st_status,
            "pit_suspension_status": suspension,
        }


def _as_date(value: int | str | date) -> date:
    if isinstance(value, date):
        return value
    text = str(value)
    if text.isdigit() and len(text) == 8:
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return date.fromisoformat(text)


class PITSecurityMaster:
    """由固定 universe 的带日期元数据组成的 PIT master。"""

    def __init__(self, records: Iterable[PITSecurityRecord] = ()):
        self._records = {normalize_symbol(r.symbol): r for r in records}

    def add(self, record: PITSecurityRecord) -> None:
        self._records[normalize_symbol(record.symbol)] = record

    def get(self, symbol: str) -> PITSecurityRecord | None:
        return self._records.get(normalize_symbol(symbol))

    def SecurityStateAsOf(self, symbol: str, asof_date: int | str | date,
                          market_data_present: bool | None = None,
                          suspension_status: str | None = None) -> dict[str, str | None]:
        record = self.get(symbol)
        if record is None:
            return {
                "symbol": normalize_symbol(symbol),
                "asof_date": _as_date(asof_date).isoformat(),
                "security_state": SecurityState.UNKNOWN.value,
                "pit_listing_status": "UNKNOWN",
                "pit_delist_status": "UNKNOWN",
                "pit_st_status": "UNKNOWN",
                "pit_suspension_status": "UNKNOWN",
            }
        return record.as_of(asof_date, market_data_present, suspension_status)

    def to_dicts(self) -> list[dict[str, Any]]:
        return [asdict(self._records[symbol]) for symbol in sorted(self._records)]

    def write_json(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dicts(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def read_json(cls, path: str | Path) -> "PITSecurityMaster":
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(PITSecurityRecord(**row) for row in rows)


def make_record(symbol: str, basic: dict[str, Any], board: str) -> PITSecurityRecord:
    symbol = normalize_symbol(symbol)
    list_date = _date_text(basic.get("ipoDate"))
    delist_date = _date_text(basic.get("outDate"))
    return PITSecurityRecord(
        symbol=symbol,
        exchange=symbol.split(".")[1],
        board=board_for_symbol(symbol, board),
        list_date=list_date,
        delist_date=delist_date,
        valid_from=list_date,
        valid_to=delist_date,
        pit_listing_status="PASS" if list_date else "UNKNOWN",
        pit_delist_status="PASS" if "outDate" in basic else "UNKNOWN",
        pit_st_status="UNKNOWN",
        pit_suspension_status="UNKNOWN",
        source="baostock.query_stock_basic",
        evidence="ipoDate/outDate are lifecycle dates; current status/ST/suspension are not projected backward",
        code_name=_date_text(basic.get("code_name")),
    )


def SecurityStateAsOf(master: PITSecurityMaster, symbol: str,
                      asof_date: int | str | date,
                      market_data_present: bool | None = None,
                      suspension_status: str | None = None) -> dict[str, str | None]:
    """正式的 PIT 状态入口，保留驼峰名称以对应业务接口约定。"""
    return master.SecurityStateAsOf(symbol, asof_date, market_data_present, suspension_status)
