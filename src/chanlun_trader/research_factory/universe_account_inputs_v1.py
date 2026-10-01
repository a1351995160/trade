"""全范围研究输入 V1：独立日历、稀疏行情与有来源的历史状态。

本模块只核对数据合同，不采集、不计算收益，也不授予独立验证资格。
缺状态和缺行情分别保留；停牌不通过填充 OHLC 变成正常交易日。
"""
from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
import hashlib
import math
import re
from typing import Any

import numpy as np
import pandas as pd
from pandas.core.util.hashing import combine_hash_arrays

from ..engine.individual_dividend_accounting_v1 import (
    IndividualDividendAccountingV1,
)
from .common import canonical_json, stable_hash


INPUT_VERSION = "UNIVERSE_ACCOUNT_INPUTS_V1"
_SYMBOL = re.compile(r"(?:00\d{4}\.SZ|60\d{4}\.SH|30\d{4}\.SZ)")
_HASH = re.compile(r"[0-9a-f]{64}")
_RAW_PRICES = ("open", "high", "low", "close")
_RAW_ACTIVITY = ("volume", "amount")
_STATE_FIELDS = (
    "listed", "delisted", "universe_member", "eligibility_status", "st_status",
    "suspension_status", "board",
)


def _day(value: Any) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError("UNIVERSE_DATE_INVALID")
    try:
        text = str(value).replace("-", "")
        if not re.fullmatch(r"\d{8}", text):
            raise ValueError()
        return int(pd.Timestamp(text).strftime("%Y%m%d"))
    except (ValueError, TypeError, OverflowError):
        raise ValueError("UNIVERSE_DATE_INVALID") from None


def normalized_universe_window_v1(window: dict) -> dict:
    required = {"symbols", "feature_start", "account_start", "account_end", "calendar"}
    if not isinstance(window, dict) or set(window) != required:
        raise ValueError("UNIVERSE_WINDOW_FIELDS_INVALID")
    symbols = window["symbols"]
    if (not isinstance(symbols, (list, tuple)) or not symbols
            or any(not isinstance(s, str) or _SYMBOL.fullmatch(s) is None for s in symbols)
            or len(symbols) != len(set(symbols))):
        raise ValueError("UNIVERSE_SYMBOLS_INVALID")
    if not isinstance(window["calendar"], (list, tuple)) or not window["calendar"]:
        raise ValueError("UNIVERSE_CALENDAR_INVALID")
    days = [_day(d) for d in window["calendar"]]
    start, account, end = (_day(window[k]) for k in
                           ("feature_start", "account_start", "account_end"))
    if (days != sorted(set(days)) or start != days[0] or end != days[-1]
            or account not in days or account > end):
        raise ValueError("UNIVERSE_WINDOW_OR_CALENDAR_INVALID")
    return {"symbols": sorted(symbols), "feature_start": start,
            "account_start": account, "account_end": end, "calendar": days}


def _keys_sorted(frame: pd.DataFrame, keys: list[str]) -> bool:
    return pd.MultiIndex.from_arrays([frame[name].array
                                      for name in keys]).is_monotonic_increasing


def _frame_identity(frame: pd.DataFrame, sort: list[str]) -> str:
    """哈希保留真实缺值和列类型；只按键排序，不建立逐行字典副本。"""
    # index=False 不需要重设索引；规范化表已排序时不再复制全表。
    selected = (frame if _keys_sorted(frame, sort) else frame.sort_values(sort)).copy(deep=False)
    digest = hashlib.sha256()
    digest.update(stable_hash([(str(c), str(selected[c].dtype))
                               for c in selected.columns]).encode())
    # 只需列名；零行视图保留原 dtype 选择语义，避免复制全部对象列。
    object_columns = set(selected.iloc[:0].select_dtypes(include="object").columns)

    def column_hashes():
        # 保留旧 map 的类型推断，但同时只持有一列映射结果。
        for name, series in selected.items():
            if name in object_columns:
                series = series.map(lambda value: canonical_json(value)
                    if isinstance(value, (dict, list, tuple, set, frozenset)) else value)
            yield pd.util.hash_pandas_object(series, index=False).to_numpy(copy=False)

    # 与 pandas DataFrame/index=False 相同的列顺序和组合过程。
    hashes = combine_hash_arrays(column_hashes(), len(selected.columns))
    digest.update(memoryview(hashes))
    return digest.hexdigest()


def universe_input_identity_v1(bundle: dict, window: dict) -> str:
    w = normalized_universe_window_v1(window)
    states = bundle["states"]
    state_date = "trade_date" if "trade_date" in states else "effective_date"
    return stable_hash({"version": INPUT_VERSION, "window": w,
        "profile": bundle.get("profile"),
        "frames": {"daily": _frame_identity(bundle["daily"], ["symbol", "date"]),
                   "turn": _frame_identity(bundle.get("turn", pd.DataFrame(
                       columns=["symbol", "date"])), ["symbol", "date"]),
                   "states": _frame_identity(states, ["symbol", state_date])},
        "calendar": bundle.get("calendar"), "events": bundle.get("events"),
        "corporate_actions_complete": bundle.get("corporate_actions_complete"),
        "corporate_action_coverage": bundle.get("corporate_action_coverage"),
        "source_hashes": bundle.get("source_hashes"),
        "universe_identity": bundle.get("universe_identity"),
        "source_identity": bundle.get("source_identity"),
        "source_qualification": bundle.get("source_qualification"),
        "board_policy_identity": bundle.get("board_policy_identity"),
        "listing_dates": bundle.get("listing_dates"),
        "listing_date_sources": bundle.get("listing_date_sources"),
        "listing_sessions_before_calendar": bundle.get("listing_sessions_before_calendar"),
        "calendar_source": bundle.get("calendar_source"),
        "field_sources": bundle.get("field_sources"),
        "historical_availability": bundle.get("historical_availability"),
        "price_basis": bundle.get("price_basis")})


class UniverseAccountInputsV1:
    """按索引查询，避免为每个证券/交易日复制完整状态字典。

    stage=SCAN 可报告缺口并继续信号准备；stage=ACCOUNT 要求全窗口通过。
    historical_availability 始终按来源保留，MODELED 不提升为已验证。
    """

    version = INPUT_VERSION

    def __init__(self, bundle: dict, window: dict, *, stage: str = "ACCOUNT",
                 required_fields=(), warmup_bars: int = 0):
        if stage == "SIGNAL":
            stage = "SCAN"
        if stage not in {"SCAN", "ACCOUNT"}:
            raise ValueError("UNIVERSE_INPUT_STAGE_INVALID")
        if (type(warmup_bars) is not int or warmup_bars < 0
                or not isinstance(required_fields, (list, tuple, set, frozenset))
                or not set(required_fields) <= set((*_RAW_PRICES, *_RAW_ACTIVITY,
                                                   "prev_close", "turn"))):
            raise ValueError("UNIVERSE_REQUIRED_FIELDS_INVALID")
        if not isinstance(bundle, dict):
            raise ValueError("UNIVERSE_BUNDLE_INVALID")
        self.window = normalized_universe_window_v1(window)
        self.calendar = tuple(self.window["calendar"])
        self.symbols = tuple(self.window["symbols"])
        self._symbol_set = frozenset(self.symbols)
        self.stage, self.required_fields, self.warmup_bars = stage, frozenset(required_fields), warmup_bars
        self.bundle = dict(bundle)
        self._session_indices = {d: i for i, d in enumerate(self.calendar)}
        self._open_times = {d: pd.Timestamp(str(d), tz='Asia/Shanghai') + pd.Timedelta(hours=9, minutes=30)
                            for d in self.calendar}
        self._close_times = {d: pd.Timestamp(str(d), tz='Asia/Shanghai') + pd.Timedelta(hours=15)
                             for d in self.calendar}
        if tuple(bundle.get("calendar", ())) != self.calendar:
            raise ValueError("UNIVERSE_CALENDAR_COVERAGE_CONFLICT")
        if bundle.get("profile") not in {"HISTORICAL_MODELED", "REAL_OBSERVED"}:
            raise ValueError("UNIVERSE_INPUT_PROFILE_INVALID")
        self.historical_availability = bundle.get("historical_availability", "MODELED"
            if bundle["profile"] == "HISTORICAL_MODELED" else "UNKNOWN")
        if self.historical_availability not in {"MODELED", "UNKNOWN", "VERIFIED"}:
            raise ValueError("UNIVERSE_AVAILABILITY_INVALID")
        if bundle["profile"] == "HISTORICAL_MODELED" and self.historical_availability == "VERIFIED":
            raise ValueError("UNIVERSE_PROFILE_AVAILABILITY_CONFLICT")
        self.source_hashes = deepcopy(bundle.get("source_hashes", {}))
        if (not isinstance(self.source_hashes, dict) or not self.source_hashes
                or any(not isinstance(k, str) or not k or not isinstance(v, str)
                       or _HASH.fullmatch(v) is None for k, v in self.source_hashes.items())):
            raise ValueError("UNIVERSE_SOURCE_HASHES_INVALID")
        self.daily = self._frame(bundle.get("daily"), "date", "daily")
        self.turn = self._frame(bundle.get("turn", pd.DataFrame(
            columns=["symbol", "date"])), "date", "turn")
        self.states = bundle.get("states")
        if not isinstance(self.states, pd.DataFrame) or "symbol" not in self.states:
            raise ValueError("UNIVERSE_STATES_FRAME_INVALID")
        has_daily, has_interval = "trade_date" in self.states, "effective_date" in self.states
        if has_daily == has_interval:
            raise ValueError("UNIVERSE_STATE_AXIS_INVALID")
        self._state_date = "trade_date" if has_daily else "effective_date"
        self.states = self._frame(self.states, self._state_date, "states", interval=has_interval)
        if has_interval and "valid_to" not in self.states:
            raise ValueError("UNIVERSE_STATE_INTERVAL_END_REQUIRED")
        if has_interval:
            converted = {v: _day(v) for v in self.states.valid_to.unique()}
            self.states["valid_to"] = self.states.valid_to.map(converted)
            if (self.states.valid_to < self.states.effective_date).any():
                raise ValueError("UNIVERSE_STATE_INTERVAL_INVALID")
            for _, group in self.states.groupby("symbol", sort=False):
                if any(left >= right for left, right in zip(
                        group.valid_to.tolist(), group.effective_date.tolist()[1:])):
                    raise ValueError("UNIVERSE_STATE_INTERVAL_OVERLAP")
        self._daily_index = pd.MultiIndex.from_frame(self.daily[["symbol", "date"]])
        self._turn_index = self.turn.set_index(["symbol", "date"], drop=False)
        self._states_index = pd.MultiIndex.from_frame(self.states[["symbol", self._state_date]])
        self._daily_columns = {name: self.daily[name].to_numpy(copy=False) for name in self.daily}
        self._state_columns = {name: self.states[name].to_numpy(copy=False) for name in self.states}
        self._interval_indices = {symbol: tuple(group[self._state_date])
            for symbol, group in self.states.groupby("symbol", sort=False)} if has_interval else {}
        self.listing_dates, self.listing_date_sources = self._listing_metadata()
        if not isinstance(bundle.get("events"), (list, tuple)):
            raise ValueError("UNIVERSE_ACTION_LIST_INVALID")
        self.events = tuple(deepcopy(bundle["events"]))
        self._global_gaps: list[str] = []
        self.source_qualification = deepcopy(bundle.get('source_qualification'))
        if self.source_qualification is not None:
            if (not isinstance(self.source_qualification, dict)
                    or not set(self.source_qualification) <= self._symbol_set
                    or any(not isinstance(row, dict)
                           or row.get('indicator_qualification') not in {'RAW_PRICE_AND_MODELED_UNIT_READY', 'UNKNOWN'}
                           or row.get('origin_status') not in {'EXACT_RAW_WINDOW_MATCH', 'UNKNOWN', 'RAW_CACHE_CONFLICT'}
                           or not isinstance(row.get('reasons'), list)
                           or any(not isinstance(reason, str) for reason in row['reasons'])
                           for row in self.source_qualification.values())):
                raise ValueError('UNIVERSE_SOURCE_QUALIFICATION_INVALID')
        if bundle["profile"] == "REAL_OBSERVED":
            self._global_gaps.append("UNIVERSE_REAL_OBSERVED_EVIDENCE_NOT_QUALIFIED")
        for name in ("universe_identity", "source_identity", "board_policy_identity"):
            value = bundle.get(name)
            if not isinstance(value, str) or _HASH.fullmatch(value) is None:
                self._global_gaps.append("UNIVERSE_" + name.upper() + "_MISSING")
        from .board_execution_policy_v1 import board_policy_identity
        if bundle.get("board_policy_identity") != board_policy_identity():
            self._global_gaps.append("UNIVERSE_BOARD_POLICY_IDENTITY_CONFLICT")
        if not self._registered_source(bundle.get("calendar_source")):
            self._global_gaps.append("UNIVERSE_CALENDAR_SOURCE_UNREGISTERED")
        if (bundle.get("price_basis", {}).get("execution") != "RAW"
                and ("adjustflag" not in self.daily or not self.daily.adjustflag.astype(str).eq("3").all())):
            self._global_gaps.append("UNIVERSE_RAW_PRICE_BASIS_UNVERIFIED")
        self._validate_events()
        self._coverage_rows = self._action_coverage_rows()
        valid_bars = (self.daily[["symbol", "date"]].loc[self.daily.volume.gt(0)]
                      if 'volume' in self.daily else self.daily.iloc[:0][["symbol", "date"]])
        self._bar_dates = {symbol: tuple(group.date) for symbol, group in
                           valid_bars.groupby("symbol", sort=False)}
        del valid_bars
        self._reference_gaps = self._price_reference_gaps()
        self.bundle.update(daily=self.daily, turn=self.turn, states=self.states,
                           events=list(self.events), calendar=list(self.calendar),
                           listing_dates=deepcopy(self.listing_dates),
                           listing_date_sources=deepcopy(self.listing_date_sources))
        self.coverage = self._coverage()
        self.input_identity = universe_input_identity_v1(self.bundle, self.window)
        if stage == "ACCOUNT":
            self.require_account_ready()

    def _frame(self, value, date_col: str, name: str, *, interval=False) -> pd.DataFrame:
        if not isinstance(value, pd.DataFrame) or not {"symbol", date_col} <= set(value):
            raise ValueError("UNIVERSE_FRAME_FIELDS_INVALID:" + name)
        # 全表 deep copy 会把分块对象列再合并成一个巨大副本；逐位置复制仍保持列独立。
        frame = value.copy(deep=False)
        for index in range(len(value.columns)):
            frame.isetitem(index, value.iloc[:, index].copy(deep=True))
        converted = {v: _day(v) for v in frame[date_col].unique()}
        frame[date_col] = frame[date_col].map(converted)
        if (frame.duplicated(["symbol", date_col]).any()
                or not set(frame.symbol) <= set(self.symbols)):
            raise ValueError("UNIVERSE_FRAME_KEYS_INVALID:" + name)
        if not interval and not set(frame[date_col]) <= set(self.calendar):
            raise ValueError("UNIVERSE_FRAME_DATE_OUTSIDE_CALENDAR:" + name)
        if not _keys_sorted(frame, ["symbol", date_col]):
            frame.sort_values(["symbol", date_col], inplace=True)
        frame.reset_index(drop=True, inplace=True)
        return frame

    def _registered_source(self, value) -> bool:
        if not isinstance(value, str) or not value or value in {"UNKNOWN", "MODELED"}:
            return False
        return value in self.source_hashes or any(h in value for h in self.source_hashes.values())

    def _listing_metadata(self) -> tuple[dict, dict]:
        declared = self.bundle.get("listing_dates", {})
        sources = self.bundle.get("listing_date_sources", {})
        if (not isinstance(declared, dict) or not isinstance(sources, dict)
                or not set(declared) <= set(self.symbols) or not set(sources) <= set(self.symbols)):
            raise ValueError("UNIVERSE_LISTING_METADATA_INVALID")
        dates_by_symbol, evidence_by_symbol = {}, {}
        for symbol, value in declared.items():
            dates_by_symbol[symbol] = {_day(value)}
            declared_sources = sources.get(symbol, ())
            if isinstance(declared_sources, str):
                declared_sources = [declared_sources]
            if isinstance(declared_sources, (list, tuple)):
                evidence_by_symbol[symbol] = {s for s in declared_sources if self._registered_source(s)}
        if "listing_date" in self.states:
            # 只分组一次、对每个证券的唯一日期/来源检查一次；不逐日 iterrows。
            source_column = next((k for k in ("listing_date_source", "source", "state_source")
                                  if k in self.states), None)
            columns = ["symbol", "listing_date"] + ([source_column] if source_column is not None else [])
            known = self.states[columns]
            known = known.loc[known.listing_date.notna()]
            for symbol, own in known.groupby("symbol", sort=False):
                dates_by_symbol.setdefault(symbol, set()).update(
                    _day(int(v)) if isinstance(v, (float, np.floating)) and float(v).is_integer()
                    else _day(v) for v in own.listing_date.unique())
                if source_column is not None:
                    evidence_by_symbol.setdefault(symbol, set()).update(
                        s for s in own[source_column].dropna().unique() if self._registered_source(s))
        result, bound_sources = {}, {}
        for symbol, dates in dates_by_symbol.items():
            if len(dates) > 1:
                raise ValueError("UNIVERSE_LISTING_DATE_CONFLICT:" + symbol)
            result[symbol] = next(iter(dates))
            if evidence_by_symbol.get(symbol):
                bound_sources[symbol] = sorted(evidence_by_symbol[symbol])
        return result, bound_sources

    def _price_reference_gaps(self) -> set[tuple[str, int]]:
        """现金事件必须解释 raw 参考价跳变；不以缓存 shift 填补官方昨收。"""
        result = set()
        for symbol, rows in self.daily.groupby("symbol", sort=False):
            actions = [e for e in self.events if e.get("symbol") == symbol
                       and e.get("event_type") == "CASH_DIVIDEND"]
            previous = None
            for row in rows.itertuples(index=False):
                if previous is not None and hasattr(row, "prev_close"):
                    try:
                        ref = float(row.prev_close)
                        before = float(previous.close)
                        cash = sum(float(e["terms"]["cash_per_share"]) for e in actions
                                   if int(previous.date) < _day(e["effective_date"]) <= int(row.date))
                        if math.isfinite(ref) and math.isfinite(before) and abs(ref - (before - cash)) > .011:
                            result.add((symbol, int(row.date)))
                    except (KeyError, TypeError, ValueError, AttributeError):
                        result.add((symbol, int(row.date)))
                previous = row
        return result

    def _validate_events(self):
        seen = set()
        for event in self.events:
            if (not isinstance(event, dict) or not isinstance(event.get("event_id"), str)
                    or not event["event_id"] or event["event_id"] in seen
                    or event.get("symbol") not in self.symbols):
                raise ValueError("UNIVERSE_ACTION_ID_OR_SYMBOL_INVALID")
            seen.add(event["event_id"])
            if event.get("event_type") != "CASH_DIVIDEND":
                self._global_gaps.append("UNIVERSE_UNSUPPORTED_ACTION:" + event["event_id"])
                continue
            try:
                record, effective, payment = (_day(event[k]) for k in
                                              ("record_date", "effective_date", "payment_date"))
                published = _day(str(event["source_published_at"])[:10])
                if (record < 20150908 or not record < effective <= payment or published > record
                        or (self.calendar[0] <= record <= self.calendar[-1] and record not in self.calendar)
                        or (self.calendar[0] <= effective <= self.calendar[-1] and effective not in self.calendar)
                        or not self._registered_source(event.get("source"))):
                    raise ValueError()
                IndividualDividendAccountingV1(0, [event], "INPUT_VALIDATION")._cash_terms(event)
            except (KeyError, TypeError, ValueError):
                self._global_gaps.append("UNIVERSE_CASH_ACTION_TERMS_UNKNOWN:" + event["event_id"])

    def _action_coverage_rows(self) -> list[dict]:
        value = self.bundle.get("corporate_action_coverage")
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, (list, tuple)) or not value:
            self._global_gaps.append("UNIVERSE_CORPORATE_COVERAGE_MISSING")
            return []
        if self.bundle.get("corporate_actions_complete") is not True:
            self._global_gaps.append("UNIVERSE_CORPORATE_ACTIONS_INCOMPLETE")
        result = []
        for row in value:
            try:
                symbols = row.get("symbols", [row.get("symbol")])
                if (not isinstance(symbols, (list, tuple)) or not symbols
                        or not set(symbols) <= set(self.symbols) or row.get("complete") is not True
                        or not self._registered_source(row.get("source"))):
                    raise ValueError()
                start, end = _day(row["start"]), _day(row["end"])
                if start > end:
                    raise ValueError()
                result.append({**deepcopy(row), "symbols": frozenset(symbols),
                               "start": start, "end": end})
            except (KeyError, TypeError, ValueError):
                self._global_gaps.append("UNIVERSE_CORPORATE_COVERAGE_INVALID")
        return result

    def session_index(self, day: int) -> int:
        try:
            return self._session_indices[_day(day)]
        except KeyError:
            raise ValueError("UNIVERSE_SESSION_OUTSIDE_CALENDAR") from None

    def bar(self, symbol: str, day: int) -> dict | None:
        day = day if isinstance(day, (int, np.integer)) and day in self._session_indices else _day(day)
        try:
            position = self._daily_index.get_loc((symbol, day))
        except KeyError:
            return None
        return {name: values[position].item() if isinstance(values[position], np.generic)
                else values[position] for name, values in self._daily_columns.items()}

    def state(self, symbol: str, day: int, *, asof=None) -> dict:
        day = day if isinstance(day, (int, np.integer)) and day in self._session_indices else _day(day)
        unknown = {"symbol": symbol, "trade_date": day, "listed": None, "delisted": None,
                   "universe_member": None, "eligibility_status": "UNKNOWN", "st_status": "UNKNOWN",
                   "suspension_status": "UNKNOWN", "board": "UNKNOWN", "state_known": False,
                   "reason": "UNIVERSE_STATE_MISSING", "historical_availability": "UNKNOWN"}
        if day not in self._session_indices or symbol not in self._symbol_set:
            return {**unknown, "reason": "UNIVERSE_STATE_OUTSIDE_SCOPE"}
        if self._state_date == "trade_date":
            key = (symbol, day)
        else:
            days = self._interval_indices.get(symbol, ())
            index = bisect_right(days, day) - 1
            if index < 0:
                return unknown
            key = (symbol, days[index])
        try:
            position = self._states_index.get_loc(key)
        except KeyError:
            return unknown
        row = {name: values[position].item() if isinstance(values[position], np.generic)
               else values[position] for name, values in self._state_columns.items()}
        if self._state_date == "effective_date" and day > row["valid_to"]:
            return {**unknown, "reason": "UNIVERSE_STATE_INTERVAL_EXPIRED"}
        if not set(_STATE_FIELDS) <= set(row):
            return {**unknown, "reason": "UNIVERSE_STATE_FIELDS_MISSING"}
        boolean_known = all(isinstance(row[k], (bool, np.bool_)) for k in
                            ("listed", "delisted", "universe_member"))
        if (not boolean_known or row["st_status"] not in {"NORMAL", "ST"}
                or row["suspension_status"] not in {"TRADING", "SUSPENDED", "NOT_LISTED", "DELISTED"}
                or row["eligibility_status"] not in {"ELIGIBLE", "INELIGIBLE"}):
            return {**row, "state_known": False, "reason": "UNIVERSE_STATE_UNKNOWN",
                    "historical_availability": row.get("availability_status", "UNKNOWN")}
        expected_board = "SH_MAIN" if symbol.startswith("60") else (
            "CHINEXT" if symbol.startswith("30") else "SZ_MAIN")
        board = "CHINEXT" if row["board"] == "GEM" else row["board"]
        if board != expected_board:
            return {**row, "state_known": False, "reason": "UNIVERSE_STATE_BOARD_CONFLICT"}
        if (row["delisted"] and (row["listed"] or row["suspension_status"] != "DELISTED")
                or not row["listed"] and not row["delisted"]
                and row["suspension_status"] != "NOT_LISTED"
                or row["listed"] and row["suspension_status"] not in {"TRADING", "SUSPENDED"}):
            return {**row, "state_known": False, "reason": "UNIVERSE_STATE_LIFECYCLE_CONFLICT"}
        source = row.get("source", row.get("state_source"))
        if not self._registered_source(source):
            return {**row, "state_known": False, "reason": "UNIVERSE_STATE_SOURCE_UNREGISTERED"}
        availability = row.get("availability_status", self.historical_availability)
        if availability not in {"MODELED", "VERIFIED"}:
            return {**row, "state_known": False, "reason": "UNIVERSE_STATE_AVAILABILITY_UNKNOWN"}
        if asof is None:
            asof = self._open_times[day]
        if asof is not None:
            now = pd.Timestamp(asof)
            if now.tzinfo is None:
                raise ValueError("UNIVERSE_ASOF_TIMEZONE_REQUIRED")
            for name in ("available_at", "effective_available_at", "researcher_available_at"):
                value = row.get(name)
                if value is None or (not isinstance(value, str) and pd.isna(value)) or value == "":
                    continue
                if value in {"UNKNOWN", "MODELED"}:
                    if value == "UNKNOWN" or availability != "MODELED":
                        return {**row, "state_known": False, "reason": "UNIVERSE_STATE_AVAILABILITY_UNKNOWN"}
                    continue
                try:
                    stamp = pd.Timestamp(value)
                    if stamp.tzinfo is None or stamp > now:
                        return {**row, "state_known": False, "reason": "UNIVERSE_STATE_NOT_YET_AVAILABLE"}
                except (ValueError, TypeError):
                    return {**row, "state_known": False, "reason": "UNIVERSE_STATE_AVAILABILITY_UNKNOWN"}
        return {**row, "symbol": symbol, "trade_date": day, "board": board,
                "source_board": row["board"], "state_known": True, "reason": "OK",
                "historical_availability": availability}

    def _bar_gaps(self, row: dict, *, account_day: bool) -> list[str]:
        reasons = []
        required = {*_RAW_PRICES, *_RAW_ACTIVITY, *(self.required_fields - {"turn"})}
        if account_day:
            required.add("prev_close")
        for field in sorted(required):
            try:
                number = float(row[field])
                if (isinstance(row[field], (bool, np.bool_)) or not math.isfinite(number)
                        or number < 0 or field in {*_RAW_PRICES, "prev_close"} and number <= 0):
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                reasons.append("UNIVERSE_RAW_FIELD_INVALID:" + field)
        if any(reason.endswith(tuple(_RAW_PRICES)) for reason in reasons):
            return reasons
        if (float(row["high"]) < max(float(row[k]) for k in ("open", "close", "low"))
                or float(row["low"]) > min(float(row[k]) for k in ("open", "close", "high"))):
            reasons.append("UNIVERSE_RAW_OHLC_CONFLICT")
        if "adjustflag" in row and str(row["adjustflag"]) != "3":
            reasons.append("UNIVERSE_EXECUTION_REQUIRES_RAW_PRICE")
        return reasons

    def scan_status(self, symbol: str, day: int) -> dict:
        """资格、信号数据与成交数据分开；缺口不能返回一个假 FALSE。"""
        state = self.state(symbol, day)
        row = self.bar(symbol, day)
        return self._scan_status(symbol, day, state, row)

    def _scan_status(self, symbol: str, day: int, state: dict, row: dict | None) -> dict:
        reasons: list[str] = []
        source_ready = True
        if self.source_qualification is not None:
            qualification = self.source_qualification.get(symbol, {})
            source_ready = (qualification.get('indicator_qualification') == 'RAW_PRICE_AND_MODELED_UNIT_READY'
                            and qualification.get('origin_status') == 'EXACT_RAW_WINDOW_MATCH')
            if not source_ready:
                reasons.append('UNIVERSE_SOURCE_NOT_QUALIFIED')
                reasons.extend(qualification.get('reasons', []))
        if not state["state_known"]:
            reasons.append(state["reason"])
        listed = state["state_known"] and bool(state["listed"]) and not state["delisted"]
        trading = listed and state["suspension_status"] == "TRADING"
        if state["state_known"] and not listed:
            if row is not None:
                reasons.append("UNIVERSE_BAR_OUTSIDE_LISTED_LIFECYCLE")
            status = "DELISTED" if state["delisted"] else "NOT_LISTED"
        elif state["state_known"] and state["suspension_status"] == "SUSPENDED":
            if row is not None:
                reasons.extend(self._bar_gaps(row, account_day=False))
                if not reasons and float(row["volume"]) > 0:
                    reasons.append("UNIVERSE_SUSPENSION_ACTIVITY_CONFLICT")
            status = "SUSPENDED"
        elif trading:
            if row is None:
                reasons.append("UNIVERSE_TRADING_BAR_MISSING")
            else:
                reasons.extend(self._bar_gaps(row, account_day=False))
                if "turn" in self.required_fields:
                    key = (symbol, day)
                    try:
                        value = float(self._turn_index.loc[key, "turn"])
                        if not math.isfinite(value) or value < 0:
                            raise ValueError()
                    except (KeyError, TypeError, ValueError):
                        reasons.append("UNIVERSE_REQUIRED_TURN_MISSING")
            status = "TRADING"
        else:
            status = "UNKNOWN"
        signal_ready = trading and not reasons and source_ready
        if signal_ready and self.warmup_bars:
            if bisect_right(self._bar_dates.get(symbol, ()), day) < self.warmup_bars:
                signal_ready = False
                status = "WARMUP_INSUFFICIENT"
        entry_eligible = (signal_ready and bool(state["universe_member"])
                          and state["eligibility_status"] == "ELIGIBLE" and state["st_status"] == "NORMAL"
                          and row is not None and float(row["volume"]) > 0)
        return {"symbol": symbol, "date": day, "status": status,
                "signal_ready": signal_ready, "entry_eligible": entry_eligible,
                "bar_present": row is not None, "state_known": state["state_known"],
                "reported_zero_volume": bool(row is not None and not reasons
                    and float(row.get("volume", -1)) == 0), "gap_reasons": sorted(set(reasons)),
                "historical_availability": state.get("historical_availability", "UNKNOWN")}

    def _coverage(self) -> dict:
        gaps: dict[tuple[str, str], dict] = {}
        per_symbol = []
        by_board = {board: {"target_count": 0, "cached_count": 0, "account_qualified_count": 0,
                            "identity_unknown_count": 0, "identity_conflict_count": 0}
                    for board in ("SZ_MAIN", "SH_MAIN", "CHINEXT")}
        for symbol in self.symbols:
            counts: dict[str, int] = {}
            gap_count = 0
            for day in self.calendar:
                state = self.state(symbol, day)
                row = self.bar(symbol, day)
                result = self._scan_status(symbol, day, state, row)
                counts[result["status"]] = counts.get(result["status"], 0) + 1
                reasons = list(result["gap_reasons"])
                if (symbol, day) in self._reference_gaps:
                    reasons.append("UNIVERSE_UNEXPLAINED_PRICE_REFERENCE")
                account_day = day >= self.window["account_start"]
                if account_day:
                    closed = self.state(symbol, day, asof=self._close_times[day])
                    if not closed["state_known"]:
                        reasons.append(closed["reason"])
                    if state["state_known"] and state["listed"]:
                        if symbol not in self.listing_dates or symbol not in self.listing_date_sources:
                            reasons.append("UNIVERSE_LISTING_DATE_UNVERIFIED")
                        elif self.listing_dates[symbol] > day:
                            reasons.append("UNIVERSE_LISTING_DATE_LIFECYCLE_CONFLICT")
                if account_day and state["state_known"] and state["delisted"]:
                    reasons.append("UNIVERSE_DELISTING_SETTLEMENT_UNSUPPORTED")
                if state["state_known"] and state["listed"] and not state["delisted"]:
                    if not any(symbol in r["symbols"] and r["start"] <= day <= r["end"]
                               for r in self._coverage_rows):
                        reasons.append("UNIVERSE_CORPORATE_COVERAGE_GAP")
                    if account_day and state["suspension_status"] == "TRADING" and row is not None:
                        reasons.extend(self._bar_gaps(row, account_day=True))
                for reason in set(reasons):
                    gap_count += 1
                    key = (symbol, reason)
                    if key not in gaps:
                        gaps[key] = {"symbol": symbol, "reason": reason, "count": 0,
                                     "first_date": day, "last_date": day, "example_dates": []}
                    gap = gaps[key]
                    gap["count"] += 1
                    gap["last_date"] = day
                    if len(gap["example_dates"]) < 3:
                        gap["example_dates"].append(day)
            per_symbol.append({"symbol": symbol, "session_count": len(self.calendar),
                               "status_counts": counts,
                               "gap_count": gap_count})
            scope_board = "SH_MAIN" if symbol.startswith("60") else (
                "CHINEXT" if symbol.startswith("30") else "SZ_MAIN")
            board = by_board[scope_board]
            board["target_count"] += 1
            board["cached_count"] += int(symbol in self._bar_dates)
            board["account_qualified_count"] += int(gap_count == 0 and not self._global_gaps)
            board["identity_conflict_count"] += int((symbol, "UNIVERSE_STATE_BOARD_CONFLICT") in gaps)
            board["identity_unknown_count"] += int(any((symbol, reason) in gaps for reason in
                ("UNIVERSE_STATE_MISSING", "UNIVERSE_STATE_FIELDS_MISSING", "UNIVERSE_STATE_UNKNOWN")))
        return {"schema_version": INPUT_VERSION, "target_symbol_count": len(self.symbols),
            "calendar_session_count": len(self.calendar), "bar_count": len(self.daily),
            "global_gaps": sorted(set(self._global_gaps)),
            "gaps": [gaps[k] for k in sorted(gaps)], "per_symbol": per_symbol, "by_board": by_board,
            "account_data_ready": not gaps and not self._global_gaps,
            "historical_availability": self.historical_availability,
            "historical_independence": "UNKNOWN", "independent_confirmation_eligible": False,
            "strategy_qualified": False}

    def require_account_ready(self):
        if not self.coverage["account_data_ready"]:
            first = (self.coverage["global_gaps"] or
                     [g["reason"] for g in self.coverage["gaps"]])[0]
            raise ValueError("UNIVERSE_ACCOUNT_INPUT_NOT_READY:" + first)
        return self

    def assert_unchanged(self):
        if (self.listing_dates != self.bundle["listing_dates"]
                or self.listing_date_sources != self.bundle["listing_date_sources"]
                or universe_input_identity_v1(self.bundle, self.window) != self.input_identity):
            raise ValueError("UNIVERSE_INPUT_IDENTITY_CHANGED")


def prepare_universe_account_inputs_v1(bundle: dict, window: dict, *, stage="ACCOUNT",
                                       required_fields=(), warmup_bars=0) -> UniverseAccountInputsV1:
    return UniverseAccountInputsV1(bundle, window, stage=stage,
                                  required_fields=required_fields, warmup_bars=warmup_bars)
