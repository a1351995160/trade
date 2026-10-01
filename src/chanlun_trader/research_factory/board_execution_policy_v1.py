"""全范围账户的历史板块制度；独立于旧 V2 日线近似政策。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import math
import re
from typing import Mapping, Sequence

from ..engine.security_state import ChinaPriceLimitModel, PriceLimitState, SecurityMaster
from ..engine.time_types import date_key, ensure_aware
from .common import stable_hash

VERSION = "BOARD_EXECUTION_POLICY_V1"
SUPPORTED_FROM = 20140613
SUPPORTED_TO = 20250731
CHINEXT_REFORM = 20200824
MAIN_REGISTRATION_REFORM = 20230410

# 原字节哈希来自 2026-10-01 对以下官方 URL 的有界只读下载。
# 它们是来源快照身份，不代表未来联网抓取的页面字节仍会相同。
_SOURCES = (
    {"source_id": "SZSE_CHINEXT_2020", "url": "https://investor.szse.cn/institute/rules/t20200807_580310.html",
     "sha256": "2461b1906837abba74263c703bdd0a88556549a6f7e648f3e31a4dc56347c154"},
    {"source_id": "SZSE_CHINEXT_EFFECTIVE", "url": "https://www.szse.cn/aboutus/trends/news/t20200821_580924.html",
     "sha256": "cba74610a5c582e2ac94f3bb21a04f2e5fa1fa175131b0cd287f9bbfc311b1ae"},
    {"source_id": "SZSE_IPO_2014", "url": "https://www.szse.cn/www/disclosure/notice/company/t20140613_508770.html",
     "sha256": "c3925fa2ace1c252cb0fd515adf7327f296330773f0cf59adbd0d56cb13dcb69"},
    {"source_id": "SZSE_TRADE_2020", "url": "https://www.szse.cn/lawrules/rule/repeal/rules/P020231230545338442079.pdf",
     "sha256": "348218aab3164083e52057f7313d7d9d7e29f3701b464bc3cc030b600fc23215"},
    {"source_id": "SZSE_MAIN_2023", "url": "https://investor.szse.cn/knowledge/qa/t20230306_599093.html",
     "sha256": "b001c495439287f9a78f4fca8a9c93c22008f7be2ac2773799c0e61f9779b501"},
    {"source_id": "SZSE_LOT_TICK_2023", "url": "https://investor.szse.cn/institute/rules/t20230706_601604.html",
     "sha256": "89e04919809f4d4f514915a4850966d4f638c88ff76674adb7d3ef51d9d86621"},
    {"source_id": "SSE_TRADE_2023", "url": "https://www.sse.com.cn/lawandrules/sselawsrules2025/repeal/rules/c/10824490/files/dcbe58edb194451d93f19b1f7dd8fb4c.docx",
     "sha256": "7aa2319f6dcf597be1e86b3b69d7c2ad0e6acb2a5d0cc6be48a01af602fded40"},
    {"source_id": "MAIN_REGISTRATION_EFFECTIVE", "url": "https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20230404_5719112.shtml",
     "sha256": "cd14cf31f250e9e7ebfb812ae691b27d9042031ff04de83f88b5471b0cd195b4"},
    {"source_id": "SSE_IPO_2014", "url": "https://english.sse.com.cn/news/newsrelease/c/4947622.shtml",
     "sha256": "fa829963a7fe3683a8a3117f9de2b0e163dbe9eb047324d55dac85b9feebadca"},
    {"source_id": "SSE_ST_MIN_TICK_2013", "url": "https://edu.sse.com.cn/best/article/mcontent/c/4731114.shtml",
     "sha256": "091b3328a09d056698b00e518d8e9e9f05dca612988c51e483f4e535c6ba5bd5"},
)


def describe_board_policy_v1() -> dict:
    """可冻结的制度语义；页面文案或调用方不能改写制度来源。"""
    return {
        "version": VERSION, "supported_from": SUPPORTED_FROM, "supported_to": SUPPORTED_TO,
        "sources": deepcopy(list(_SOURCES)), "tick_size": "0.01", "buy_lot_size": 100,
        "rounding": "DECIMAL_ROUND_HALF_UP", "minimum_price": "0.01",
        "ordinary_limits": {"SH_MAIN": .10, "SZ_MAIN": .10, "CHINEXT_PRE_20200824": .10,
                            "CHINEXT_FROM_20200824": .20},
        "st_limits": {"SH_MAIN": .05, "SZ_MAIN": .05, "CHINEXT_PRE_20200824": .05,
                      "CHINEXT_FROM_20200824": .20},
        "sh_main_st_minimum_absolute_limit": {"reference_below": "0.10", "amount": "0.01",
            "source_id": "SSE_ST_MIN_TICK_2013"},
        "ipo": {"legacy_first_session": {"up_pct": .44, "down_pct": .36,
                    "reference": "ISSUE_PRICE", "opening_execution": "UNSUPPORTED_LEGACY_IPO_OPENING_DAY"},
                "registered_first_five_sessions": "NO_DAILY_PRICE_LIMIT",
                "chinext_registered_from": CHINEXT_REFORM, "main_registered_from": MAIN_REGISTRATION_REFORM},
        "opening_model": "EXACT_TICK_LIMIT_CONSERVATIVE_NO_QUEUE", "t_plus_one": True,
        "missing_identity": "FAIL_CLOSED", "st_buy_eligibility": "EXCLUDED",
        "unsupported_special_sessions": ["RELISTING", "DELISTING_PERIOD", "EXCHANGE_SPECIAL"],
        "intraday_order_book_and_temporary_halts": "NOT_MODELED_BY_DAILY_OPEN_EXECUTION",
    }


def board_policy_identity() -> str:
    return stable_hash(describe_board_policy_v1())


def _day(value) -> int:
    if type(value) is not int:
        raise ValueError("BOARD_POLICY_DATE_INVALID")
    try:
        datetime.strptime(str(value), "%Y%m%d")
    except ValueError as exc:
        raise ValueError("BOARD_POLICY_DATE_INVALID") from exc
    return value


def _value(state, key, default=None):
    return state.get(key, default) if isinstance(state, Mapping) else getattr(state, key, default)


def canonical_board(symbol: str, source_board: str) -> str:
    """核对代码、交易所、来源板块三者；GEM 是唯一创业板别名。"""
    if not isinstance(symbol, str) or not re.fullmatch(r"\d{6}\.(?:SZ|SH)", symbol):
        raise ValueError("BOARD_SYMBOL_UNSUPPORTED")
    expected = ("SZ_MAIN" if symbol.endswith(".SZ") and symbol.startswith("00") else
                "SH_MAIN" if symbol.endswith(".SH") and symbol.startswith("60") else
                "CHINEXT" if symbol.endswith(".SZ") and symbol.startswith("30") else None)
    if expected is None:
        raise ValueError("BOARD_SYMBOL_UNSUPPORTED")
    actual = "CHINEXT" if source_board == "GEM" else source_board
    if actual not in {"SZ_MAIN", "SH_MAIN", "CHINEXT"}:
        raise ValueError("BOARD_IDENTITY_UNKNOWN")
    if actual != expected:
        raise ValueError("BOARD_IDENTITY_CONFLICT")
    return actual


@dataclass(frozen=True)
class BoardSessionPolicyV1:
    symbol: str
    trade_date: int
    board: str
    source_board: str
    listing_date: int
    listing_session: int | None
    regime_id: str
    up_pct: float | None
    down_pct: float | None
    no_price_limit: bool
    lot_size: int
    tick_size: str
    source_ids: tuple[str, ...]
    board_policy_identity: str
    price_reference: str = "EXCHANGE_REFERENCE_PREVIOUS_CLOSE"

    def to_dict(self) -> dict:
        return asdict(self)

    def limit_prices(self, reference_price: float) -> tuple[float | None, float | None]:
        reference = _price(reference_price)
        if self.no_price_limit:
            return None, None
        tick = Decimal(self.tick_size)
        up = (reference * (1 + Decimal(str(self.up_pct)))).quantize(tick, rounding=ROUND_HALF_UP)
        down = (reference * (1 - Decimal(str(self.down_pct)))).quantize(tick, rounding=ROUND_HALF_UP)
        minimum_st_tick = (self.board == "SH_MAIN" and self.regime_id == "RISK_WARNING_5_PERCENT"
                           and reference < Decimal("0.10"))
        if self.trade_date >= MAIN_REGISTRATION_REFORM or minimum_st_tick:
            # 2023 规则与更早的沪市低价风险警示规则都明确最小 tick。
            if up - reference < tick:
                up = reference + tick
            if reference - down < tick:
                down = reference - tick
        return float(max(tick, up)), float(max(tick, down))


def _price(value) -> Decimal:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("BOARD_REFERENCE_PRICE_INVALID")
    return Decimal(str(value))


class BoardExecutionPolicyV1:
    def __init__(self, *, calendar: Sequence[int], listing_dates: Mapping[str, int] | None = None):
        days = tuple(_day(day) for day in calendar)
        if not days or days != tuple(sorted(set(days))):
            raise ValueError("BOARD_CALENDAR_INVALID")
        self.calendar = days
        self._calendar_index = {day: i for i, day in enumerate(days)}
        self.listing_dates = {symbol: _day(day) for symbol, day in (listing_dates or {}).items()}

    @property
    def identity(self) -> str:
        return board_policy_identity()

    def describe(self) -> dict:
        return describe_board_policy_v1()

    def resolve(self, symbol: str, trade_date: int, security_state) -> BoardSessionPolicyV1:
        day = _day(trade_date)
        if not SUPPORTED_FROM <= day <= SUPPORTED_TO:
            raise ValueError("BOARD_POLICY_DATE_UNSUPPORTED")
        if day not in self._calendar_index:
            raise ValueError("BOARD_TRADE_DATE_NOT_IN_CALENDAR")
        source_board = _value(security_state, "board")
        board = canonical_board(symbol, source_board)
        if _value(security_state, "symbol", symbol) != symbol:
            raise ValueError("BOARD_STATE_SYMBOL_CONFLICT")
        status = _value(security_state, "st_status")
        if status not in {"NORMAL", "ST"}:
            raise ValueError("BOARD_ST_STATUS_UNKNOWN")
        if _value(security_state, "is_st", status == "ST") != (status == "ST"):
            raise ValueError("BOARD_ST_STATUS_CONFLICT")
        if _value(security_state, "special_trading_status", "NORMAL") != "NORMAL":
            raise ValueError("BOARD_SPECIAL_TRADING_SESSION_UNSUPPORTED")
        source_listing = _value(security_state, "listing_date")
        listing = self.listing_dates.get(symbol, source_listing)
        if listing is None:
            raise ValueError("BOARD_LISTING_DATE_REQUIRED")
        listing = _day(listing)
        if source_listing is not None and source_listing != listing:
            raise ValueError("BOARD_LISTING_DATE_CONFLICT")
        if day < listing:
            raise ValueError("BOARD_BEFORE_LISTING")
        if listing < self.calendar[0]:
            # 窗口缺失上市初期日历时，不能凭自然日天数认定前五交易日已结束。
            prior_count = _value(security_state, "listing_sessions_before_calendar")
            observed_sessions = self._calendar_index[day] + 1
            if observed_sessions < 5 and (type(prior_count) is not int or prior_count < 5):
                raise ValueError("BOARD_LISTING_SESSION_HISTORY_REQUIRED")
            listing_session = None
        else:
            if listing not in self._calendar_index:
                raise ValueError("BOARD_LISTING_DATE_NOT_IN_CALENDAR")
            listing_session = self._calendar_index[day] - self._calendar_index[listing] + 1
        registered = listing >= (CHINEXT_REFORM if board == "CHINEXT" else MAIN_REGISTRATION_REFORM)
        ipo = registered and listing_session is not None and listing_session <= 5
        legacy_ipo = not registered and listing_session == 1
        if board == "CHINEXT":
            pct = .20 if day >= CHINEXT_REFORM else .05 if status == "ST" else .10
            source_ids = ("SZSE_CHINEXT_2020", "SZSE_CHINEXT_EFFECTIVE", "SZSE_LOT_TICK_2023")
        else:
            pct = .05 if status == "ST" else .10
            source_ids = (("SSE_TRADE_2023", "SSE_IPO_2014", "SSE_ST_MIN_TICK_2013") if board == "SH_MAIN" else
                          ("SZSE_TRADE_2020", "SZSE_MAIN_2023", "SZSE_IPO_2014"))
        if registered:
            source_ids += ("MAIN_REGISTRATION_EFFECTIVE",) if board != "CHINEXT" else ()
        regime = ("REGISTERED_IPO_FIRST_FIVE" if ipo else "LEGACY_IPO_FIRST_SESSION" if legacy_ipo else
                  "CHINEXT_20_PERCENT" if board == "CHINEXT" and day >= CHINEXT_REFORM else
                  "RISK_WARNING_5_PERCENT" if status == "ST" else "ORDINARY_10_PERCENT")
        return BoardSessionPolicyV1(symbol, day, board, source_board, listing, listing_session,
            regime, None if ipo else .44 if legacy_ipo else pct,
            None if ipo else .36 if legacy_ipo else pct, ipo, 100, "0.01", source_ids,
            self.identity, "ISSUE_PRICE" if legacy_ipo else "EXCHANGE_REFERENCE_PREVIOUS_CLOSE")


class BoardPriceLimitModelV1(ChinaPriceLimitModel):
    """同 Broker 接口，开盘只读取开盘价/参考价与冻结证券状态。"""
    def __init__(self, security_master: SecurityMaster, *, calendar: Sequence[int],
                 listing_dates: Mapping[str, int] | None = None, pit_enforced: bool = True):
        if pit_enforced is not True:
            raise ValueError("BOARD_POLICY_PIT_REQUIRED")
        super().__init__(security_master, pit_enforced=True)
        self.policy = BoardExecutionPolicyV1(calendar=calendar, listing_dates=listing_dates)

    def session_policy(self, symbol: str, ts) -> BoardSessionPolicyV1:
        return self.policy.resolve(symbol, date_key(ensure_aware(ts)), self.master.as_of(symbol, ts))

    def limit_pct(self, symbol: str, ts) -> float | None:
        result = self.session_policy(symbol, ts)
        if result.regime_id == "LEGACY_IPO_FIRST_SESSION":
            raise ValueError("ASYMMETRIC_IPO_LIMIT_REQUIRES_SESSION_POLICY")
        return result.up_pct

    def limit_prices(self, symbol: str, ts, prev_close: float) -> tuple:
        return self.session_policy(symbol, ts).limit_prices(prev_close)

    def classify_daily(self, symbol: str, ts, bar: dict | None) -> PriceLimitState:
        state = self.master.as_of(symbol, ts)
        result = self.policy.resolve(symbol, date_key(ensure_aware(ts)), state)
        if bar is None or state.suspended:
            return PriceLimitState.SUSPENDED
        opening = _price(bar.get("open"))
        if result.regime_id == "LEGACY_IPO_FIRST_SESSION":
            raise ValueError("UNSUPPORTED_LEGACY_IPO_OPENING_DAY")
        if result.no_price_limit:
            return PriceLimitState.NORMAL
        up, down = result.limit_prices(bar.get("prev_close"))
        if opening > Decimal(str(up)) or opening < Decimal(str(down)):
            raise ValueError("BOARD_OPEN_OUTSIDE_PRICE_LIMIT")
        # 日线开盘没有队列证据：到达上/下限时保守等待，不读取当日 high/low/volume。
        if opening == Decimal(str(up)):
            return PriceLimitState.LIMIT_UP_OPENED
        if opening == Decimal(str(down)):
            return PriceLimitState.LIMIT_DOWN_OPENED
        return PriceLimitState.NORMAL

    def _can_trade(self, symbol: str, ts, bar: dict | None, *, buy: bool) -> tuple[bool, str]:
        state = self.master.as_of(symbol, ts)
        self.session_policy(symbol, ts)
        if not state.listed or state.delisted:
            return False, "SECURITY_NOT_LISTED"
        if buy and state.st_status == "ST":
            return False, "ST_NOT_ELIGIBLE"
        classification = self.classify_daily(symbol, ts, bar)
        if classification == PriceLimitState.SUSPENDED:
            return False, "SUSPENDED"
        if buy and classification == PriceLimitState.LIMIT_UP_OPENED:
            return False, "LIMIT_UP_OPEN_DAILY_CONSERVATIVE"
        if not buy and classification == PriceLimitState.LIMIT_DOWN_OPENED:
            return False, "LIMIT_DOWN_OPEN_DAILY_CONSERVATIVE"
        return True, "OK"

    def can_buy_at_open(self, symbol: str, ts, bar: dict | None) -> tuple[bool, str]:
        return self._can_trade(symbol, ts, bar, buy=True)

    def can_sell_at_open(self, symbol: str, ts, bar: dict | None) -> tuple[bool, str]:
        return self._can_trade(symbol, ts, bar, buy=False)
