"""数据适配器接口。

当前本地能力：通达信日线 OHLCVA + 复权因子（gbbq）。
未来接入 5 分钟、资金流、龙虎榜等数据时，统一实现本接口，避免回测代码
直接依赖具体数据文件格式。

设计原则：最小可用。不引入数据库、不引入缓存框架。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Bar:
    code: str
    date: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    period: str = "day"


@dataclass
class DataCapability:
    fields: set[str] = field(default_factory=set)
    periods: set[str] = field(default_factory=set)
    history_start: int | None = None
    history_end: int | None = None

    def has(self, field: str) -> bool:
        return field in self.fields


class DataAdapter(Protocol):
    """统一数据接口。新数据源实现：get_bars / capability / available_at。"""

    def capability(self) -> DataCapability:
        """返回数据源能力描述。"""
        ...

    def get_bars(self, code: str, period: str = "day") -> list[Bar]:
        """返回该标的指定周期的全部历史 Bar。"""
        ...

    def available_at(self, field: str, code: str, date: int) -> int | None:
        """返回该字段在给定标的/日期上的真实可获取日期；无法确认时返回 None。

        回测中若 available_at 返回 None，调用方必须拒绝把该字段当作当天已知信息。
        """
        return None


class TdxDailyAdapter:
    """通达信日线适配器（当前唯一本地全历史数据源）。"""

    def __init__(self, tdx) -> None:
        self._tdx = tdx

    def capability(self) -> DataCapability:
        return DataCapability(
            fields={"open", "high", "low", "close", "volume", "amount", "qfq"},
            periods={"day"},
            history_start=20210802,
            history_end=None,
        )

    def get_bars(self, code: str, market: int = 0, adjust: str = "qfq") -> list[Bar]:
        df = self._tdx.get_qfq_day(code, market) if adjust == "qfq" else self._tdx.get_day(code, market)
        if df.empty:
            return []
        out: list[Bar] = []
        for row in df.itertuples():
            if adjust == "qfq":
                o, h, l, c = row.qfq_open, row.qfq_high, row.qfq_low, row.qfq_close
            else:
                o, h, l, c = row.open, row.high, row.low, row.close
            out.append(Bar(code=code, date=int(row.date), open=float(o), high=float(h),
                           low=float(l), close=float(c), volume=float(row.volume),
                           amount=float(row.amount)))
        return out

    def available_at(self, field: str, code: str, date: int) -> int | None:
        # 日线 OHLCVA 在当日收盘后可获取。用下一个自然日近似；实际交易日历由调用方处理。
        if field in self.capability().fields:
            return date + 1
        return None


class TDXDataAdapter:
    """TDX 原生数据适配器：把 TDXProviders 的语义数据接入统一 DataAdapter。

    策略层只依赖本类的方法（get_daily/get_lhb/get_sentiment/get_limit/get_finance），
    不感知 GP/SC/FN 字段与 HTTP 细节。
    """

    def __init__(self, providers=None) -> None:
        if providers is None:
            from chanlun_trader.data.tdx.providers import TDXProviders
            providers = TDXProviders()
        self.providers = providers

    def get_daily(self, code: str, start: str, end: str, dividend_type: str = "qfq") -> list[Bar]:
        df = self.providers.daily.get_daily([code], start, end,
                                            dividend_type="front" if dividend_type == "qfq" else "none")
        out = []
        for r in df.itertuples():
            out.append(Bar(code=r.code, date=int(r.date), open=float(r.open), high=float(r.high),
                           low=float(r.low), close=float(r.close), volume=float(r.volume),
                           amount=float(r.amount)))
        return out

    def get_lhb(self, code: str, start: str, end: str):
        return self.providers.lhb.get_stock_events(code, start, end)

    def get_sentiment(self, start: str, end: str):
        return self.providers.sentiment.get_sentiment(start, end)

    def get_limit_event(self, code: str, start: str, end: str):
        return self.providers.limit.get_stock_events(code, start, end)

    def get_finance(self, code: str, as_of_date: int):
        return self.providers.finance.get_snapshot(code, as_of_date)

    def available_at(self, field: str, code: str, date: int) -> int | None:
        if field in {"lhb", "limit_event", "finance"}:
            return int(date) + 1
        if field == "sentiment":
            return int(date) + 1
        if field == "daily":
            return int(date) + 1
        return None
