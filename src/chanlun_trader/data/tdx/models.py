"""语义化数据模型。所有专业数据必须携带 available_at / earliest_tradable_time。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Availability(str, Enum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    ONLINE_ONLY = "ONLINE_ONLY"
    UNAVAILABLE = "UNAVAILABLE"
    PIT_UNSAFE = "PIT_UNSAFE"


@dataclass(frozen=True)
class LHBEvent:
    code: str
    event_date: int
    buy_amount: float | None          # 万元
    sell_amount: float | None         # 万元
    net_amount: float | None          # 万元
    inst_buy_amount: float | None     # 机构买入 万元
    inst_sell_amount: float | None    # 机构卖出 万元
    broker_buy_amount: float | None   # 营业部买入 万元
    broker_sell_amount: float | None  # 营业部卖出 万元
    hgt_buy_amount: float | None      # 沪深股通买入 万元
    hgt_sell_amount: float | None     # 沪深股通卖出 万元
    pro_net_buy: float | None         # 专业机构买卖净额 万元
    reason: str = ""
    available_at: int = 0             # T 收盘后
    earliest_tradable_time: int = 0   # T+1 开盘

    @classmethod
    def from_gp_row(cls, code: str, date: int, row: dict) -> "LHBEvent":
        v = row.get("Value") or []
        def f(i):
            try:
                return float(v[i])
            except Exception:
                return None
        buy = f(0) if len(v) > 0 else None
        sell = f(1) if len(v) > 1 else None
        net = round(buy - sell, 4) if buy is not None and sell is not None else None
        return cls(
            code=code, event_date=date, buy_amount=buy, sell_amount=sell, net_amount=net,
            inst_buy_amount=None, inst_sell_amount=None,
            broker_buy_amount=buy, broker_sell_amount=sell,
            hgt_buy_amount=None, hgt_sell_amount=None, pro_net_buy=None,
            available_at=date + 1, earliest_tradable_time=date + 1,
        )


@dataclass(frozen=True)
class LimitUpEvent:
    code: str
    event_date: int
    status: int                    # 2涨停 1曾涨停 -1曾跌停 -2跌停
    seal_amount: float | None      # 封单金额 万元（跌停为负）
    first_limit_time: str | None   # 首次涨停时间 HHMM
    open_count: int | None         # 开板次数
    max_seal_amount: float | None  # 涨停最大封单额 万元
    seal_ratio: float | None       # 封成比
    available_at: int = 0
    earliest_tradable_time: int = 0


@dataclass(frozen=True)
class MarketSentiment:
    date: int
    limit_up_count: float | None
    once_limit_up_count: float | None
    limit_down_count: float | None
    once_limit_down_count: float | None
    board_seal_money: float | None      # 封板成功资金 亿元
    board_fail_money: float | None      # 封板失败资金 亿元
    lhb_buy_amount: float | None        # 亿元
    lhb_sell_amount: float | None       # 亿元
    lhb_inst_buy: float | None
    lhb_inst_sell: float | None
    lhb_broker_buy: float | None
    lhb_broker_sell: float | None
    lhb_hgt_buy: float | None
    lhb_hgt_sell: float | None
    streak_count: float | None          # 连板家数（含ST/新股）
    streak_count_ex_st: float | None
    market_height: float | None
    board_2plus_count: float | None
    up_count: float | None
    down_count: float | None
    total_seal_amount: float | None     # 涨停封单 亿元
    dt_seal_amount: float | None        # 跌停封单 亿元
    available_at: int = 0
    earliest_tradable_time: int = 0


@dataclass(frozen=True)
class FinanceSnapshot:
    code: str
    report_period: int          # tag_time 报告期
    announce_date: int          # announce_time 公告日期
    field_values: dict = field(default_factory=dict)  # FNxx -> float
    available_at: int = 0       # announce_date 盘后
    earliest_tradable_time: int = 0  # announce_date + 1


@dataclass(frozen=True)
class DailyBar:
    code: str
    date: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    available_at: int = 0       # T 收盘后
