"""全范围账户的只读核验：从成交及冻结输入独立重建经济结果。

不调用 Broker、账本、扫描器或退出评估器作为核验答案。指标公式与 DSL
解释器复用，因此这里证明数据/条件/决策的绑定，不声称独立验证指标公式。
历史成本、状态可见时点、开盘流动性仍为明确的模型假设，不授予策略资格。
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
import math

import pandas as pd

from .common import stable_hash


VERSION = "UNIVERSE_EVIDENCE_V1"
_BACKEND = "UNIVERSE_ACCOUNT_BACKEND_V1"
_PRICE_POLICY = "RAW_PLUS_ENTITLED_GROSS_CASH_V1"
_SHARE_PRICE_POLICY = "RAW_IN_ORIGINAL_SHARE_UNITS_PLUS_ENTITLED_CASH_V2"
_TAX_POLICY = "SALE_FILL_MODELED_2015_101"
_PRIORITY = ("EXIT_FIXED_COST_STOP", "EXIT_TRAILING_STOP", "EXIT_FIXED_TAKE_PROFIT")


def _require(value, reason):
    if not value:
        raise ValueError("UNIVERSE_AUDIT_" + reason)


def _number(value):
    _require(not isinstance(value, bool) and isinstance(value, (int, float))
             and math.isfinite(value), "NONFINITE_NUMBER")
    return float(value)


def _same(actual, expected, reason, tolerance=1e-6):
    _require(abs(_number(actual) - _number(expected)) <= tolerance, reason)


def _tree(actual, expected, reason):
    """身份键/顺序精确比较，经济浮点只容许远低于一分钱的计算误差。"""
    if isinstance(expected, dict):
        _require(isinstance(actual, dict) and set(actual) == set(expected), reason)
        for key in expected:
            _tree(actual[key], expected[key], reason + ":" + str(key))
    elif isinstance(expected, list):
        _require(isinstance(actual, list) and len(actual) == len(expected), reason)
        for a, e in zip(actual, expected):
            _tree(a, e, reason)
    elif isinstance(expected, float):
        _same(actual, expected, reason)
    else:
        _require(actual == expected and (not isinstance(expected, (bool, int, str))
                 or type(actual) is type(expected)), reason)


def _stamp(day, close=False):
    return pd.Timestamp(str(day), tz="Asia/Shanghai") + pd.Timedelta(
        hours=15 if close else 9, minutes=0 if close else 30)


def _time(value):
    stamp = pd.Timestamp(value)
    _require(not pd.isna(stamp) and stamp.tzinfo is not None, "AWARE_TIME_REQUIRED")
    return stamp.tz_convert("Asia/Shanghai")


def _fee(side, quantity, price, costs):
    value = quantity * price
    commission = max(value * costs["commission_rate"], costs["min_commission"])
    stamp_tax = value * costs["stamp_tax_rate"] if side == "SELL" else 0.
    return round(commission + stamp_tax, 4)


def _tax_rate(bought, sold):
    start, end = _time(bought).normalize(), _time(sold).normalize()
    _require(end > start, "DIVIDEND_TAX_HOLDING_PERIOD")
    return .20 if end <= start + pd.DateOffset(months=1) else (
        .10 if end <= start + pd.DateOffset(years=1) else 0.)


def _opening_permission(inputs, symbol, day, side, *, modeled_price=None):
    """独立制度推导；不调用 BoardExecutionPolicy/PriceLimitModel 的计算方法。"""
    state = inputs.state(symbol, day, asof=_stamp(day))
    _require(state["state_known"], "OPEN_STATE_UNKNOWN")
    _require(20140613 <= day <= 20250731, "BOARD_DATE_UNSUPPORTED")
    board = ("SH_MAIN" if symbol.startswith("60") and symbol.endswith(".SH") else
             "SZ_MAIN" if symbol.startswith("00") and symbol.endswith(".SZ") else
             "CHINEXT" if symbol.startswith("30") and symbol.endswith(".SZ") else None)
    _require(board is not None and state["board"] == board, "BOARD_IDENTITY_CONFLICT")
    _require(state["st_status"] in {"NORMAL", "ST"}, "ST_STATUS_UNKNOWN")
    _require(state.get("special_trading_status", "NORMAL") == "NORMAL", "SPECIAL_SESSION_UNSUPPORTED")
    listing = inputs.listing_dates.get(symbol)
    _require(type(listing) is int and listing <= day, "LISTING_IDENTITY_UNKNOWN")
    index = inputs.session_index(day)
    if listing < inputs.calendar[0]:
        proof = inputs.bundle.get("listing_sessions_before_calendar", {}).get(symbol)
        _require(index >= 4 or (type(proof) is int and proof >= 5), "LISTING_SESSION_HISTORY_REQUIRED")
        ordinal = None
    else:
        _require(listing in inputs.calendar, "LISTING_CALENDAR_REQUIRED")
        ordinal = index - inputs.session_index(listing) + 1
    registered = listing >= (20200824 if board == "CHINEXT" else 20230410)
    no_limit = registered and ordinal is not None and ordinal <= 5
    _require(registered or ordinal != 1, "LEGACY_IPO_OPENING_UNSUPPORTED")
    if not state["listed"] or state["delisted"]:
        return False, "SECURITY_NOT_LISTED"
    if state["suspension_status"] != "TRADING":
        return False, "SUSPENDED"
    if side == "BUY" and (state["st_status"] == "ST" or not state["universe_member"]
                           or state["eligibility_status"] != "ELIGIBLE"):
        return False, "SECURITY_NOT_ELIGIBLE"
    raw = inputs.bar(symbol, day)
    _require(raw is not None, "TRADING_BAR_MISSING")
    prior_day = inputs.calendar[index - 1] if index else None
    prior = inputs.bar(symbol, prior_day) if prior_day else None
    if prior is None or _number(prior["volume"]) <= 0:
        return False, "LIQUIDITY_REFERENCE_UNAVAILABLE"
    if no_limit:
        return True, "REGISTERED_IPO_NO_DAILY_LIMIT"
    pct = Decimal("0.20") if board == "CHINEXT" and day >= 20200824 else (
        Decimal("0.05") if state["st_status"] == "ST" else Decimal("0.10"))
    tick = Decimal("0.01")
    reference, opening = Decimal(str(_number(raw["prev_close"]))), Decimal(str(_number(raw["open"])))
    _require(reference > 0 and opening > 0, "PRICE_REFERENCE_INVALID")
    upper = (reference * (1 + pct)).quantize(tick, rounding=ROUND_HALF_UP)
    lower = (reference * (1 - pct)).quantize(tick, rounding=ROUND_HALF_UP)
    minimum_st_tick = board == "SH_MAIN" and state["st_status"] == "ST" and reference < Decimal("0.10")
    if day >= 20230410 or minimum_st_tick:
        upper = max(upper, reference + tick)
        lower = min(lower, reference - tick)
    upper, lower = max(tick, upper), max(tick, lower)
    _require(lower <= opening <= upper, "OPEN_OUTSIDE_BOARD_LIMIT")
    if side == "BUY" and opening == upper:
        return False, "LIMIT_UP_OPEN_DAILY_CONSERVATIVE"
    if side == "SELL" and opening == lower:
        return False, "LIMIT_DOWN_OPEN_DAILY_CONSERVATIVE"
    if modeled_price is not None and not lower <= Decimal(str(_number(modeled_price))) <= upper:
        return False, "MODELED_FILL_PRICE_OUTSIDE_DAILY_LIMIT"
    return True, "OK"


def _condition_frames(strategy, inputs):
    """单独遍历股票重建条件与准备身份；明确复用指标/DSL，禁止调用扫描器。"""
    from ..engine.conditions_v2 import ConditionContext
    from .causal_dividend_features_v1 import causal_hfq_bars, causal_hfq_bars_v2
    from .research_rule_strategy_v2 import _field_references, evaluate_condition
    turns = {str(s): frame.set_index("date") for s, frame in inputs.turn.groupby("symbol", sort=False)}
    groups = {str(s): frame for s, frame in inputs.daily.groupby("symbol", sort=False)}
    frames, preparation = {}, []
    fields_needed = {name for expr in strategy.expressions.values() for name in _field_references(expr)}
    for symbol in inputs.symbols:
        raw = groups.get(symbol)
        raw = raw.loc[raw.volume.gt(0)].sort_values("date").copy() if raw is not None else None
        if raw is None or raw.empty:
            frames[symbol] = pd.DataFrame(columns=["buy", "sell", "market_filter", "ready"])
            preparation.append({"symbol": symbol, "status": "NO_VALID_BARS"})
            continue
        raw["adjustflag"] = "3"
        actions = tuple(e for e in inputs.events if e["symbol"] == symbol
                        and int(raw.date.min()) < e["effective_date"] <= int(raw.date.max()))
        price_transform = (causal_hfq_bars_v2 if any(e.get("price_version") == "CASH_AND_SHARES_V2"
                           or e["event_type"] in {"BONUS", "CAPITALIZATION"} for e in actions)
                           else causal_hfq_bars)
        bars, price_trace = price_transform(raw, actions)
        bars = bars.set_index("date")
        own_turn = turns.get(symbol)
        vendor = own_turn["turn"].reindex(bars.index) if own_turn is not None and "turn" in own_turn else None
        matrix = strategy.build_feature_matrix(bars, vendor)
        values = {f"{a}.{o}": matrix[f"{a}.{o}"] for a, o in strategy.references}
        ready = {name: matrix[name + "__ready"] for name in values}
        fields = {name: matrix[name] for name in fields_needed if name in matrix}
        context = ConditionContext(values, fields, matrix.index, ready=ready)
        calculated = pd.DataFrame({name: evaluate_condition(expr, context)
                                   for name, expr in strategy.expressions.items()}, index=matrix.index)
        if "market_filter" not in calculated:
            calculated["market_filter"] = 1.
        finite = pd.Series(True, index=matrix.index)
        for name, series in values.items():
            finite &= ready[name].eq(True) & series.map(lambda value: math.isfinite(float(value)))
        for series in fields.values():
            finite &= series.map(lambda value: math.isfinite(float(value)))
        calculated["ready"] = finite & calculated[["buy", "sell", "market_filter"]].notna().all(axis=1)
        frames[symbol] = calculated
        records = [{**{str(k): None if pd.isna(v) else bool(v) if k == "ready" else float(v)
                        for k, v in row.items()}, "date": int(day)} for day, row in calculated.iterrows()]
        preparation.append({"symbol": symbol, "status": "COMPUTED", "bars": len(matrix),
                            "conditions_hash": stable_hash(records), "price_trace": price_trace})
    return frames, preparation


def _truth(frame, day):
    if day not in frame.index:
        return {"buy": None, "sell": None, "market_filter": None, "ready": False,
                "reason": "NO_COMPLETED_BAR"}
    row = frame.loc[day]
    return {**{name: None if pd.isna(row[name]) else bool(row[name]) for name in
               ("buy", "sell", "market_filter")}, "ready": bool(row["ready"]), "reason": "COMPUTED"}


class _Reconstruction:
    """核验器自己的普通字典经济状态；不实例化系统账本。"""
    def __init__(self, inputs, strategy, cash, costs, policy):
        self.inputs, self.strategy, self.costs, self.policy = inputs, strategy, costs, policy
        self.cash = cash
        self.lots, self.positions, self.prices, self.last_exit = {}, {}, {}, {}
        self.entitlements, self.rights, self.receivables = {}, {}, {}
        self.applied, self.paid, self.action_audit = set(), set(), []
        self.events = [e for e in inputs.events if e["record_date"] >= inputs.window["account_start"]]
        self.share_events = any(e["event_type"] in {"BONUS", "CAPITALIZATION"} for e in self.events)
        self.pending_share_credits, self.bonus_parent_lots = {}, {}
        self.share_price_factors, self.cash_price_adjustments = {}, {}
        self.dividend_record_factors, self.share_tax_lots = {}, {}
        self.share_tax = 0.
        self.price_policy = _SHARE_PRICE_POLICY if self.share_events else _PRICE_POLICY
        self.new_share_lots, self.no_permitted_quantity_ids = set(), []
        self.tax = self.income = self.fees = self.turnover = self.realized = 0.
        self.rule_states, self.trailing, self.exit_rows = {}, {}, []
        self.trade_count = 0

    def quantity(self, symbol):
        return sum(lot["remaining_quantity"] for lot in self.lots.values() if lot["symbol"] == symbol)

    def sellable(self, lot, day):
        # 送转lot继承原入场时点，但可卖日由股份来源单独约束，不能套用原股T+1。
        return lot["remaining_quantity"] > 0 and _time(lot["sellable_from"]) <= _stamp(day)

    def equity(self):
        _require(all(symbol in self.prices for symbol in self.positions if self.quantity(symbol)), "HOLDING_MARK_MISSING")
        return self.cash + sum(self.receivables.values()) + sum(
            self.quantity(symbol) * self.prices[symbol] for symbol in self.positions)

    def snapshot(self, day, *, opening=False):
        market_value = float(sum(self.quantity(s) * self.prices[s] for s in self.positions))
        unrealized = float(sum((self.prices[s] - p["average_cost"]) * p["quantity"]
                               for s, p in self.positions.items() if p["quantity"]))
        receivable = float(sum(self.receivables.values()))
        timestamp = _stamp(day) if opening else _stamp(day, close=True) + pd.Timedelta(minutes=30)
        # 原账本先把不含应收的权益取4位，再把应收加入；此处显式核对该合同。
        return {"timestamp": timestamp.isoformat(), "cash": round(self.cash, 4),
            "market_value": round(market_value, 4),
            "equity": round(round(self.cash + market_value, 4) + receivable, 4),
            "realized_pnl": round(self.realized, 4), "unrealized_pnl": round(unrealized, 4),
            "positions": sum(bool(p["quantity"]) for p in self.positions.values()),
            "turnover": round(self.turnover, 4), "cash_receivable": receivable, "available_cash": self.cash}

    def economic_identity(self):
        return stable_hash({"initial_cash": self.policy["initial_cash"], "cash": self.cash,
            "reserved_cash": 0., "prices": self.prices,
            "positions": {self.strategy.strategy_id + ":" + s: p for s, p in self.positions.items()},
            "lots": self.public_lots()})

    def open_actions(self, day):
        stamp = str(_stamp(day))
        self.new_share_lots = set()
        # 同日现金和股份都基于登记份额；先形成现金应收，再扩展股份数量。
        ordered = (sorted(self.events, key=lambda e: (e["effective_date"],
                    e["event_type"] != "CASH_DIVIDEND", e["event_id"])) if self.share_events else self.events)
        for event in ordered:
            key = event["event_id"]
            if event["effective_date"] <= day and key not in self.applied:
                _require(event["effective_date"] == day and key in self.entitlements, "ACTION_HISTORY_MISSING")
                held = self.quantity(event["symbol"])
                if event["event_type"] == "CASH_DIVIDEND":
                    quantity = self.entitlements[key]
                    if quantity:
                        cash_per_share = _number(event["terms"]["cash_per_share"])
                        amount = quantity * cash_per_share
                        self.receivables[key] = amount
                        self.income += amount
                        if self.share_events:
                            for lot_id, factor in self.dividend_record_factors[key].items():
                                self.cash_price_adjustments.setdefault(lot_id, {})[key] = cash_per_share * factor
                else:
                    self.share_action(event, day)
                self.applied.add(key)
                self.action_audit.append({"event_id": key, "phase": "EFFECTIVE", "timestamp": stamp,
                                          "held_quantity": held})
            if key in self.receivables and event.get("payment_date", day + 1) <= day:
                amount = self.receivables.pop(key)
                self.cash += amount
                self.paid.add(key)
                self.action_audit.append({"event_id": key, "phase": "PAYMENT", "amount": amount, "timestamp": stamp})
        for key, credit in list(self.pending_share_credits.items()):
            if credit["credit_date"] <= day:
                self.action_audit.append({"event_id": key, "phase": "SHARE_CREDIT",
                                          "lots": deepcopy(credit["lots"]), "timestamp": stamp})
                del self.pending_share_credits[key]

    def share_action(self, event, day):
        """直接用有理数重建登记股份与剩余成本，不调用生产股份账本。"""
        terms, key = event["terms"], event["event_id"]
        numerator, denominator = terms["ratio_numerator"], terms["ratio_denominator"]
        _require(type(numerator) is int and type(denominator) is int
                 and numerator > denominator > 0 and event["units"] == "NEW_SHARES_PER_OLD_SHARE",
                 "SHARE_TERMS_INVALID")
        ratio = Fraction(numerator, denominator)
        _require(day <= event["share_credit_date"] <= event["tradable_date"], "SHARE_DATES_CONFLICT")
        for field in ("share_credit_date", "tradable_date"):
            evidence = event["date_evidence"][field]
            _require(evidence["kind"] in {"SOURCE", "MODELED"} and bool(evidence["source"])
                     and evidence["source"] != "UNKNOWN", "SHARE_DATE_EVIDENCE_REQUIRED")
        tax_rule = terms["tax_rule"]
        _require(bool(tax_rule["source"]) and tax_rule["source"] != "UNKNOWN", "SHARE_TAX_SOURCE_REQUIRED")
        exempt = tax_rule["kind"] == "CAPITALIZATION_SHARE_PREMIUM_EXEMPT"
        _require(exempt or tax_rule["kind"] == "BONUS_DEFERRED_INDIVIDUAL_2015_101", "SHARE_TAX_RULE_UNKNOWN")
        _require(not exempt or event["event_type"] == "CAPITALIZATION", "SHARE_TAX_EXEMPT_ACTION_CONFLICT")
        if not exempt:
            _require(_number(tax_rule["taxable_cash_per_new_share"]) > 0
                     and tax_rule["allocation_policy"] == "CHILD_LOTS_MODELED"
                     and bool(tax_rule["allocation_source"]) and tax_rule["allocation_source"] != "UNKNOWN",
                     "SHARE_TAX_ALLOCATION_REQUIRED")
        _require(not any(self.lots[lot_id]["symbol"] == event["symbol"]
                         for credit in self.pending_share_credits.values() for lot_id in credit["lots"]),
                 "OVERLAPPING_UNCREDITED_SHARE_ACTION")
        own = {lot_id: lot["remaining_quantity"] for lot_id, lot in self.lots.items()
               if lot["symbol"] == event["symbol"] and lot["remaining_quantity"]}
        _require(own == self.entitlements[key], "SHARE_ENTITLEMENT_CHANGED_OR_MISSING")
        changes = []
        for lot_id, quantity in own.items():
            extra = quantity * (ratio - 1)
            _require(extra.denominator == 1 and extra > 0, "FRACTIONAL_SHARES_UNSUPPORTED")
            changes.append((lot_id, int(extra)))
        credited = {}
        for lot_id, extra in changes:
            lot = self.lots[lot_id]
            original_basis = lot["cost"] / lot["quantity"]
            original_remaining_cost = original_basis * lot["remaining_quantity"]
            child_id = f"lot-{len(self.lots) + 1:06d}"
            sellable = max(_time(lot["sellable_from"]), _stamp(event["tradable_date"]))
            sellable_day = int(sellable.strftime("%Y%m%d"))
            child = {**lot, "lot_id": child_id, "quantity": extra, "remaining_quantity": extra,
                     "cost": original_remaining_cost * (1 - 1 / float(ratio)),
                     "sellable_from": sellable.isoformat(),
                     "sellable_from_session": sellable_day,
                     "sellable_from_session_index": (self.inputs.session_index(sellable_day)
                          if sellable_day in self.inputs.calendar else None), "_starts_position": False}
            lot["cost"] /= float(ratio)
            self.lots[child_id] = child
            self.new_share_lots.add(child_id)
            self.bonus_parent_lots[child_id] = lot_id
            credited[child_id] = extra
            factor = self.share_price_factors.get(lot_id, 1.) * float(ratio)
            self.share_price_factors[lot_id] = self.share_price_factors[child_id] = factor
            self.cash_price_adjustments[child_id] = deepcopy(self.cash_price_adjustments.get(lot_id, {}))
            if not exempt:
                self.share_tax_lots.setdefault(key, {})[child_id] = extra
            _same(lot["cost"] * lot["remaining_quantity"] / lot["quantity"] + child["cost"],
                  original_remaining_cost, "SHARE_REMAINING_COST_CONFLICT")
        if credited:
            self.pending_share_credits[key] = {"credit_date": event["share_credit_date"], "lots": credited}
            position = self.positions[event["symbol"]]
            active = [lot for lot in self.lots.values()
                      if lot["position_id"] == position["position_id"] and lot["remaining_quantity"]]
            position["quantity"] = sum(lot["remaining_quantity"] for lot in active)
            basis = sum(lot["cost"] * lot["remaining_quantity"] / lot["quantity"] for lot in active)
            position["average_cost"] = basis / position["quantity"]
        if event["symbol"] in self.prices:
            self.prices[event["symbol"]] /= float(ratio)

    def mark(self, day, *, opening=False):
        for symbol, position in self.positions.items():
            if not self.quantity(symbol):
                continue
            state, raw = self.inputs.state(symbol, day), self.inputs.bar(symbol, day)
            _require(state["state_known"] and state["listed"] and not state["delisted"], "UNSETTLED_HOLDING_STATE")
            if state["suspension_status"] == "TRADING":
                _require(raw is not None, "HOLDING_BAR_MISSING")
                self.prices[symbol] = _number(raw["open" if opening else "close"])
                position["unrealized_pnl"] = (self.prices[symbol] - position["average_cost"]) * position["quantity"]
            else:
                _require(state["suspension_status"] == "SUSPENDED" and symbol in self.prices, "STALE_MARK_UNVERIFIED")

    def record_actions(self, day):
        for event in self.events:
            if event["record_date"] != day:
                continue
            key = event["event_id"]
            _require(key not in self.entitlements, "DUPLICATE_ENTITLEMENT")
            own = {key: lot["remaining_quantity"] for key, lot in self.lots.items()
                   if lot["symbol"] == event["symbol"] and lot["remaining_quantity"]}
            if event["event_type"] == "CASH_DIVIDEND":
                self.entitlements[key], self.rights[key] = sum(own.values()), own
                if self.share_events:
                    self.dividend_record_factors[key] = {lot_id: self.share_price_factors.get(lot_id, 1.)
                                                        for lot_id in own}
            else:
                self.entitlements[key] = own
            self.action_audit.append({"event_id": key, "phase": "RECORD_CLOSE",
                                      "quantity": sum(own.values()), "timestamp": str(
                                          _stamp(day, close=True) + pd.Timedelta(minutes=30))})

    def fill(self, trade, day):
        symbol, side, quantity = trade["symbol"], trade["side"], trade["quantity"]
        _require(type(quantity) is int and quantity > 0, "FILL_QUANTITY_INVALID")
        raw = self.inputs.bar(symbol, day)
        permission, _ = _opening_permission(self.inputs, symbol, day, side)
        _require(permission, "FILL_NOT_TRADABLE")
        price = round(_number(raw["open"]) * (1 + (1 if side == "BUY" else -1) * self.costs["slippage_bps"]), 4)
        _require(_opening_permission(self.inputs, symbol, day, side, modeled_price=price)[0], "MODELED_FILL_PRICE_OUTSIDE_DAILY_LIMIT")
        gross, fee = quantity * price, _fee(side, quantity, price, self.costs)
        _same(trade["price"], price, "FILL_PRICE_CONFLICT")
        _same(trade["gross_value"], gross, "FILL_GROSS_CONFLICT")
        _same(trade["fee"], fee, "FILL_FEE_CONFLICT")
        _require(trade.get("reality_flag", "OK") == "OK", "UNSUPPORTED_TRADE_REALITY")
        stamp, session = _stamp(day), self.inputs.session_index(day)
        self.trade_count += 1
        _require(trade["trade_id"] == f"tr-{self.trade_count:06d}", "TRADE_SEQUENCE_CONFLICT")
        realized = 0.
        if side == "BUY":
            _require(quantity % 100 == 0 and self.cash + 1e-8 >= gross + fee, "BUY_LOT_OR_CASH_CONFLICT")
            if not self.quantity(symbol):
                expected_position = f"pos-{sum(1 for lot in self.lots.values() if lot.get('_starts_position')) + 1:06d}"
                self.positions[symbol] = {"position_id": expected_position, "symbol": symbol,
                    "strategy_id": self.strategy.strategy_id, "quantity": 0, "average_cost": 0.,
                    "opened_at": stamp.isoformat(), "updated_at": stamp.isoformat(),
                    "realized_pnl": 0., "unrealized_pnl": 0.}
                starts_position = True
            else:
                starts_position = False
            position = self.positions[symbol]
            lot_id = f"lot-{len(self.lots) + 1:06d}"
            _require(trade["lot_id"] == lot_id, "BUY_LOT_ID_CONFLICT")
            next_day = self.inputs.calendar[session + 1] if session + 1 < len(self.inputs.calendar) else None
            sellable = _stamp(next_day) if next_day else stamp.normalize() + pd.Timedelta(days=1, hours=9, minutes=30)
            self.lots[lot_id] = {"lot_id": lot_id, "position_id": position["position_id"], "symbol": symbol,
                "strategy_id": self.strategy.strategy_id, "buy_time": stamp.isoformat(),
                "quantity": quantity, "remaining_quantity": quantity, "cost": gross + fee,
                "sellable_from": sellable.isoformat(), "entry_session": day, "entry_session_index": session,
                "sellable_from_session": next_day, "sellable_from_session_index": session + 1 if next_day else None,
                "entry_price": price, "exit_due_session": None, "exit_due_index": None,
                "exit_state": "OPEN", "exit_reason": "", "_starts_position": starts_position}
            self.cash -= gross + fee
        else:
            _require(side == "SELL" and self.quantity(symbol) >= quantity, "SELL_HOLDING_CONFLICT")
            position = self.positions[symbol]
            candidates = [(key, lot) for key, lot in self.lots.items() if lot["symbol"] == symbol
                and lot["position_id"] == position["position_id"] and lot["remaining_quantity"]
                and self.sellable(lot, day)]
            candidates.sort(key=lambda item: _time(item[1]["buy_time"]))
            if trade.get("lot_id") is not None:
                candidates = [(key, lot) for key, lot in candidates if key == trade["lot_id"]]
            _require(sum(lot["remaining_quantity"] for _, lot in candidates) >= quantity, "T1_OR_FIFO_CONFLICT")
            remaining, sold = quantity, {}
            for key, lot in candidates:
                amount = min(remaining, lot["remaining_quantity"])
                if not amount:
                    break
                realized += (price - lot["cost"] / lot["quantity"]) * amount - fee * amount / quantity
                lot["remaining_quantity"] -= amount
                sold[key] = amount
                remaining -= amount
                if not lot["remaining_quantity"]:
                    lot["exit_state"] = "CLOSED"
                elif lot["exit_state"] in {"EXIT_DUE", "SELL_PENDING", "PARTIALLY_FILLED"}:
                    lot["exit_state"] = "PARTIALLY_FILLED"
            self.cash += gross - fee
            for event in self.events:
                key = event["event_id"]
                share = event["event_type"] in {"BONUS", "CAPITALIZATION"}
                rights = self.share_tax_lots.get(key) if share else self.rights.get(key)
                if event["symbol"] != symbol or rights is None:
                    continue
                tax_rule = event["terms"]["tax_rule"]
                basis = tax_rule["taxable_cash_per_new_share"] if share else event["terms"]["cash_per_share"]
                for lot_id, amount in sold.items():
                    taxed = min(amount, rights.get(lot_id, 0))
                    if not taxed:
                        continue
                    rate = _tax_rate(self.lots[lot_id]["buy_time"], stamp)
                    tax = round(taxed * _number(basis) * rate, 4)
                    rights[lot_id] -= taxed
                    self.tax += tax
                    self.cash -= tax
                    row = {"event_id": key, "phase": "DEFERRED_SHARE_TAX" if share else "DEFERRED_INDIVIDUAL_TAX",
                        "lot_id": lot_id, "quantity": taxed, "rate": rate, "amount": tax,
                        "timestamp": str(stamp), "policy": _TAX_POLICY, "source": tax_rule["source"]}
                    if share:
                        self.share_tax += tax
                        row.update(allocation_policy=tax_rule["allocation_policy"],
                                   allocation_source=tax_rule["allocation_source"], tax_allocation_verified=False)
                    else:
                        row.update(payment_date=event["payment_date"], dividend_paid=key in self.paid,
                                   broker_collection_time_verified=False)
                    self.action_audit.append(row)
            if not self.quantity(symbol):
                self.last_exit[symbol] = session
                position["unrealized_pnl"] = 0.
            position["realized_pnl"] += realized
        _require(trade["position_id"] == position["position_id"], "POSITION_ID_CONFLICT")
        _same(trade["realized_pnl"], realized, "REALIZED_PNL_CONFLICT")
        own = [lot for lot in self.lots.values() if lot["position_id"] == position["position_id"] and lot["remaining_quantity"]]
        position["quantity"] = sum(lot["remaining_quantity"] for lot in own)
        basis = sum(lot["cost"] * lot["remaining_quantity"] / lot["quantity"] for lot in own)
        position["average_cost"] = basis / position["quantity"] if position["quantity"] else 0.
        position["updated_at"] = stamp.isoformat()
        self.prices[symbol] = price
        self.fees += fee
        self.turnover += gross
        self.realized += realized
        _require(self.cash >= -1e-6, "NEGATIVE_CASH")

    def public_lots(self):
        return {key: {name: value for name, value in lot.items() if not name.startswith("_")}
                for key, lot in self.lots.items()}

    def risk_exits(self, day):
        risk = self.strategy.payload["exits"]
        if not self.strategy.exit_rules.enabled:
            return {}
        due = defaultdict(list)
        for key in sorted(self.lots):
            lot = self.lots[key]
            if not lot["remaining_quantity"]:
                continue
            security, raw = self.inputs.state(lot["symbol"], day), self.inputs.bar(lot["symbol"], day)
            if raw is None or security["suspension_status"] != "TRADING":
                continue
            parent_id = self.bonus_parent_lots.get(key)
            if key not in self.trailing and parent_id in self.trailing:
                self.trailing[key] = deepcopy(self.trailing[parent_id])
            gross, ids = 0., []
            if self.share_events:
                adjustments = self.cash_price_adjustments.get(key, {})
                gross, ids = sum(adjustments.values()), list(adjustments)
            else:
                for event in self.events:
                    if (event["event_type"] == "CASH_DIVIDEND" and event["symbol"] == lot["symbol"]
                            and event["effective_date"] <= day
                            and self.rights.get(event["event_id"], {}).get(key, 0)):
                        gross += _number(event["terms"]["cash_per_share"])
                        ids.append(event["event_id"])
            factor = self.share_price_factors.get(key, 1.)
            close, anchor = _number(raw["close"]) * factor + gross, lot["entry_price"]
            evaluations, reasons = [], []
            if lot["exit_state"] in {"EXIT_DUE", "SELL_PENDING", "PARTIALLY_FILLED"}:
                reasons = [lot["exit_reason"] or "EXIT_PENDING_RETRY"]
            else:
                hits = []
                if risk["stop_loss_pct"] is not None and close <= anchor * (1 - risk["stop_loss_pct"]):
                    hits.append(_PRIORITY[0])
                if risk["trailing_pct"] is not None:
                    trail = self.trailing.setdefault(key, {"peak_close": anchor, "activated": False, "line": 0., "peak_session": None})
                    if close > trail["peak_close"]:
                        trail["peak_close"], trail["peak_session"] = close, day
                    if trail["peak_close"] >= anchor * (1 + risk["trailing_activate_pct"]):
                        trail["activated"] = True
                    if trail["activated"]:
                        trail["line"] = max(trail["line"], trail["peak_close"] * (1 - risk["trailing_pct"]))
                        if close <= trail["line"]:
                            hits.append(_PRIORITY[1])
                if risk["take_profit_pct"] is not None and close >= anchor * (1 + risk["take_profit_pct"]):
                    hits.append(_PRIORITY[2])
                evaluation = {"trade_session": day, "lot_id": key, "symbol": lot["symbol"],
                              "state": "OPEN", "close": close, "anchor": anchor}
                if hits:
                    primary = next(reason for reason in _PRIORITY if reason in hits)
                    lot.update(exit_state="EXIT_DUE", exit_reason=primary, exit_due_session=day)
                    evaluation.update(state="EXIT_DUE", triggered=hits, primary_reason=primary)
                    reasons = [primary]
                evaluations.append(evaluation)
            trace = {"date": day, "symbol": lot["symbol"], "lot_id": key,
                "entry_price": anchor, "raw_close": _number(raw["close"]), "comparison_close": close,
                "entitled_gross_cash_per_share": gross, "event_ids": ids, "price_policy": self.price_policy,
                "evaluations": evaluations, "reasons": reasons}
            if self.share_events:
                trace["share_price_factor"] = factor
            self.exit_rows.append(trace)
            if reasons:
                due[lot["symbol"]].append(key)
        return due

    def decisions(self, day, frames):
        risk = self.risk_exits(day)
        session, records, rows = self.inputs.session_index(day), [], []
        for symbol in self.inputs.symbols:
            truth, qualification = _truth(frames[symbol], day), self.inputs.scan_status(symbol, day)
            previous = self.rule_states.get(symbol, {})
            quantity = self.quantity(symbol)
            current = {"rule_identity": self.strategy.rule_identity, "last_session_index": session,
                       "exit_pending": bool(quantity and previous.get("exit_pending", False))}
            active = [lot for lot in self.lots.values() if lot["symbol"] == symbol and lot["remaining_quantity"]]
            held = session - min((lot["entry_session_index"] for lot in active), default=session)
            side, reason, weight = "HOLD", "HOLD", 0.
            rule = self.strategy.payload
            if quantity and (current["exit_pending"] or held >= rule["max_hold_sessions"]
                             or (held >= rule["min_hold_sessions"] and truth["sell"] is True)):
                current["exit_pending"] = True
                side, reason = "SELL", "EXIT_PENDING" if previous.get("exit_pending") else "SELL"
            elif quantity:
                pass
            elif truth["sell"] is True:
                reason = "EXIT_PRIORITY"
            elif symbol in self.last_exit and session - self.last_exit[symbol] <= rule["cooldown_sessions"]:
                reason = "COOLDOWN"
            elif not truth["ready"] or any(truth[k] is None for k in ("buy", "sell", "market_filter")):
                reason = "CONDITION_UNKNOWN"
            elif not truth["buy"] or not truth["market_filter"]:
                reason = "CONDITION_FALSE"
            else:
                side, reason, weight = "BUY", "BUY", rule["target_weight"]
            if side == "BUY" and not qualification["entry_eligible"]:
                side, reason = "HOLD", "SECURITY_NOT_ELIGIBLE"
            record = {"strategy_id": self.strategy.strategy_id, "symbol": symbol,
                      "side": side, "reason": reason, "target_weight": weight}
            if risk.get(symbol) and side != "SELL":
                record.update(side="SELL", reason="RULE_RISK_EXIT_V3", target_weight=0., exit_lot_ids=sorted(risk[symbol]))
            self.rule_states[symbol] = current
            records.append(record)
            rows.append({"symbol": symbol, "conditions": truth, "qualification": qualification,
                         "side": record["side"], "reason": record["reason"]})
        return records, rows


def _policy(result, inputs, strategy, initial_cash, costs):
    from .board_execution_policy_v1 import board_policy_identity
    policy = result["account_policy"]
    _same(policy["initial_cash"], initial_cash, "INITIAL_CASH_CONFLICT")
    _require(policy["symbols"] == list(inputs.symbols), "POLICY_SYMBOLS_CONFLICT")
    portfolio = {"overlap": "SEPARATE_STRATEGY_LOTS", "lot_size": 100, **policy["portfolio"]}
    _require(portfolio["policy_id"] == _BACKEND and portfolio["purpose"] == "ENGINEERING_OBSERVATION", "POLICY_PURPOSE_CONFLICT")
    _require(portfolio["members"] == [{"strategy_id": strategy.strategy_id,
        "rule_identity": strategy.rule_identity, "weight_bps": 10000, "priority": 0}], "POLICY_MEMBERS_CONFLICT")
    _require(type(portfolio["max_positions"]) is int and 1 <= portfolio["max_positions"] <= len(inputs.symbols)
             and type(portfolio["max_symbol_exposure_bps"]) is int and 1 <= portfolio["max_symbol_exposure_bps"] <= 10000
             and portfolio["max_buy_turnover_bps"] == 10000 and portfolio["lot_size"] == 100
             and portfolio["overlap"] == "SEPARATE_STRATEGY_LOTS", "POLICY_LIMITS_INVALID")
    _require(_time(portfolio["valid_until"]) > _stamp(inputs.calendar[-1], close=True), "POLICY_EXPIRED")
    _require(result["board_policy_identity"] == inputs.bundle["board_policy_identity"] == board_policy_identity(), "BOARD_POLICY_IDENTITY_CONFLICT")
    share_events = any(e["event_type"] in {"BONUS", "CAPITALIZATION"}
                       and e["record_date"] >= inputs.window["account_start"] for e in inputs.events)
    expected_price_policy = _SHARE_PRICE_POLICY if share_events else _PRICE_POLICY
    _require(result["exit_price_policy"] == expected_price_policy, "EXIT_PRICE_POLICY_CONFLICT")
    description = result["execution_description"]
    if share_events:
        _require(description.get("supported_exit_price_policies") == [_PRICE_POLICY, _SHARE_PRICE_POLICY],
                 "EXIT_PRICE_POLICY_CAPABILITY_CONFLICT")
    _require(description["backend"] == _BACKEND and description["window"] == inputs.window
             and description["costs"] == costs and description["initial_cash"] == initial_cash
             and description["max_positions"] == portfolio["max_positions"]
             and description["max_symbol_exposure_bps"] == portfolio["max_symbol_exposure_bps"]
             and description["board_policy_identity"] == result["board_policy_identity"]
             and description["exit_price_policy"] == _PRICE_POLICY
             and description["liquidity"] == "PREVIOUS_EXCHANGE_SESSION", "EXECUTION_DESCRIPTION_CONFLICT")
    if "strategy_plan" in result:
        _require(result["strategy_plan"]["backend"] == description, "FROZEN_BACKEND_CONFLICT")
    return policy, portfolio


def _plan(plan, previous_day, day, decisions, account, portfolio, inputs):
    _require(plan["plan_id"] == "PORTFOLIO_PAPER_" + stable_hash({k: v for k, v in plan.items() if k != "plan_id"}), "PLAN_HASH_CONFLICT")
    _require(plan["input_identity"] == inputs.input_identity and plan["next_session"] == day
             and _time(plan["decision_at"]) == _stamp(previous_day, close=True) + pd.Timedelta(minutes=30), "PLAN_SCOPE_CONFLICT")
    _require(plan["policy_hash"] == stable_hash(portfolio) and plan["members"] == portfolio["members"], "PLAN_POLICY_CONFLICT")
    _require(plan["account_identity"] == account.economic_identity(), "PLAN_ACCOUNT_IDENTITY_CONFLICT")
    holdings = [lot for lot in account.public_lots().values() if lot["remaining_quantity"]]
    _tree(plan["holdings"], holdings, "PLAN_HOLDINGS_CONFLICT")
    _require(plan["status"] == "PLANNED" and plan["usage_qualified"] is False
             and plan["real_execution_authorized"] is False and "SELL_PROCEEDS_NOT_SPENDABLE" in plan["limits"], "PLAN_QUALIFICATION_CONFLICT")
    intents, excluded = [], []
    normalized = sorted(decisions, key=lambda d: (d["side"] != "SELL", d["strategy_id"], d["symbol"]))
    for item in normalized:
        if item["side"] == "HOLD":
            continue
        normalized_item = {"strategy_id": item["strategy_id"], "symbol": item["symbol"], "side": item["side"],
                           "priority": 0, "target_weight": float(item["target_weight"]), "reason": item["reason"]}
        if "exit_lot_ids" in item:
            normalized_item["exit_lot_ids"] = item["exit_lot_ids"]
        if item["side"] == "SELL" and not account.quantity(item["symbol"]):
            excluded.append({**normalized_item, "reason": "NO_OWNED_POSITION"})
            continue
        index = len(intents)
        normalized_item["intent_id"] = "PORTFOLIO_INTENT_" + stable_hash([plan["account_identity"],
            plan["policy_hash"], inputs.input_identity, plan["decision_at"], day, index, dict(normalized_item)])
        intents.append(normalized_item)
    _tree(plan["intents"], intents, "PLAN_INTENTS_CONFLICT")
    _tree(plan["excluded"], excluded, "PLAN_EXCLUSIONS_CONFLICT")


def _buy_limits(account, portfolio, symbol, price, pending, opening_cash, opening_equity, spent, gross, target):
    active = {s for s in account.positions if account.quantity(s)}
    pending_buy = [order for order in pending if order["side"] == "BUY"]
    if symbol not in active | {order["symbol"] for order in pending_buy} and len(active | {o["symbol"] for o in pending_buy}) >= portfolio["max_positions"]:
        return None
    if any(o["side"] == "SELL" and o["symbol"] == symbol for o in pending):
        return None
    def reference(s):
        raw = account.inputs.bar(s, account._current_day)
        if raw is not None and account.inputs.state(s, account._current_day)["suspension_status"] == "TRADING":
            return _number(raw["open"]) * (1 + account.costs["slippage_bps"])
        return account.prices[s]
    exposure = account.quantity(symbol) * price
    strategy_exposure = sum(account.quantity(s) * (price if s == symbol else reference(s)) for s in active)
    pending_gross = pending_cost = pending_symbol = 0.
    for order in pending_buy:
        value_price = price if order["symbol"] == symbol else reference(order["symbol"])
        value = order["remaining"] * value_price
        pending_gross += value
        pending_cost += value + _fee("BUY", order["remaining"], value_price, account.costs)
        if order["symbol"] == symbol:
            pending_symbol += value
    equity = account.equity()
    return {"cash": max(0., min(account.cash, opening_cash - spent) - pending_cost),
        "symbol": max(0., equity * portfolio["max_symbol_exposure_bps"] / 10000 - exposure - pending_symbol),
        "strategy": max(0., equity - strategy_exposure - pending_cost),
        "target": max(0., equity * target - exposure - pending_symbol),
        "turnover": max(0., opening_equity - gross - pending_gross)}


def _execute_day(account, day, plan, orders, fills, portfolio, previous_equity, skips):
    """另写的算术执行核验；不使用系统撮合、risk、order manager 方法。"""
    account._current_day = day
    opening_cash, opening_equity = account.cash, account.equity()
    all_orders, pending = [], []
    consumed = set()
    eligibility_skip_ids = []
    session_filled = defaultdict(int)
    if account.share_events:
        supplied = defaultdict(int)
        for trade in fills:
            _require(type(trade["quantity"]) is int and trade["quantity"] > 0, "FILL_QUANTITY_INVALID")
            supplied[trade["symbol"]] += trade["quantity"]
        previous_day = account.inputs.calendar[account.inputs.session_index(day) - 1]
        for symbol, quantity in supplied.items():
            prior = account.inputs.bar(symbol, previous_day)
            capacity = int(_number(prior["volume"]) * .10) if prior is not None else 0
            _require(quantity <= capacity, "SYMBOL_SESSION_VOLUME_CAPACITY_EXCEEDED")
    for intent in plan["intents"]:
        if "exit_lot_ids" in intent:
            expanded = []
            for key in intent["exit_lot_ids"]:
                expanded.append(dict(intent, _lot_id=key))
                for child_id, parent_id in account.bonus_parent_lots.items():
                    if (parent_id == key and child_id in account.new_share_lots
                            and child_id not in intent["exit_lot_ids"]
                            and account.lots[child_id]["remaining_quantity"]
                            and account.lots[child_id]["exit_state"] in {"EXIT_DUE", "SELL_PENDING", "PARTIALLY_FILLED"}):
                        expanded.append(dict(intent, _lot_id=child_id))
        else:
            expanded = [intent]
        for item in expanded:
            symbol, side = item["symbol"], item["side"]
            state = account.inputs.state(symbol, day)
            intent_id = item["intent_id"] + ":" + item["_lot_id"] if "_lot_id" in item else item["intent_id"]
            matching = [order for order in orders if order["intent_id"] == intent_id]
            if state["suspension_status"] != "TRADING" or not state["listed"] or state["delisted"]:
                _require(not matching, "ORDER_WHILE_SUSPENDED")
                continue
            if side == "BUY" and (state["universe_member"] is not True or state["eligibility_status"] != "ELIGIBLE"):
                _require(not matching, "BUY_ORDER_WHILE_NOT_ELIGIBLE_AT_OPEN")
                _require(not any(trade["symbol"] == symbol and trade["side"] == "BUY" for trade in fills),
                         "BUY_FILL_WHILE_NOT_ELIGIBLE_AT_OPEN")
                matching_skips = skips.get(intent_id, [])
                _require(matching_skips == [{"intent_id": intent_id, "reason": "SECURITY_NOT_ELIGIBLE_AT_OPEN"}],
                         "ENTRY_ELIGIBILITY_SKIP_CONFLICT")
                eligibility_skip_ids.append(intent_id)
                continue
            if side == "SELL":
                eligible = [lot for lot in account.lots.values() if lot["symbol"] == symbol
                    and account.sellable(lot, day)
                    and ("_lot_id" not in item or lot["lot_id"] == item["_lot_id"])]
                quantity = sum(lot["remaining_quantity"] for lot in eligible)
            else:
                raw = account.inputs.bar(symbol, day)
                _require(raw is not None, "BUY_BAR_MISSING")
                price = _number(raw["open"]) * (1 + account.costs["slippage_bps"])
                limits = _buy_limits(account, portfolio, symbol, price, pending, opening_cash, opening_equity, 0., 0., item["target_weight"])
                quantity = int(min(limits.values()) / price / 100) * 100 if limits else 0
                while quantity and quantity * price + _fee(side, quantity, price, account.costs) > min(limits["cash"], limits["strategy"]) + 1e-9:
                    quantity -= 100
            if not quantity:
                _require(not matching, "ORDER_WITHOUT_PERMITTED_QUANTITY")
                if account.share_events:
                    _tree(skips.get(intent_id, []), [{"intent_id": intent_id, "reason": "NO_PERMITTED_QUANTITY"}],
                          "SHARE_LOCKED_OR_ZERO_QUANTITY_SKIP_CONFLICT")
                    account.no_permitted_quantity_ids.append(intent_id)
                continue
            _require(len(matching) == 1, "MISSING_OR_DUPLICATE_ORDER")
            order = matching[0]
            _require(order["order_id"] not in consumed and order["strategy_id"] == account.strategy.strategy_id
                     and order["symbol"] == symbol and order["side"] == side
                     and order.get("lot_id") == item.get("_lot_id") and order["time_in_force"] == "DAY"
                     and order["priority"] == 0 and order["metadata"]["plan_id"] == plan["plan_id"], "ORDER_BINDING_CONFLICT")
            for field in ("created_at", "submitted_at", "eligible_at"):
                _require(_time(order[field]) == _stamp(day), "ORDER_NEXT_OPEN_TIME_CONFLICT")
            consumed.add(order["order_id"])
            modeled = {"order": order, "symbol": symbol, "side": side, "remaining": quantity, "intent": item}
            pending.append(modeled)
            all_orders.append(modeled)
    _require(consumed == {o["order_id"] for o in orders}, "UNPLANNED_ORDER")
    _require([o["order"]["sequence"] for o in all_orders] == sorted(o["order"]["sequence"] for o in all_orders), "ORDER_PRIORITY_CONFLICT")
    spent = gross = 0.
    fills_used = []
    for modeled in all_orders:
        order, symbol, side, original = modeled["order"], modeled["symbol"], modeled["side"], modeled["remaining"]
        allowed, _ = _opening_permission(account.inputs, symbol, day, side)
        raw = account.inputs.bar(symbol, day)
        prior = account.inputs.bar(symbol, account.inputs.calendar[account.inputs.session_index(day) - 1])
        price = round(_number(raw["open"]) * (1 + (1 if side == "BUY" else -1) * account.costs["slippage_bps"]), 4)
        if allowed:
            allowed, _ = _opening_permission(account.inputs, symbol, day, side, modeled_price=price)
        capacity = int(_number(prior["volume"]) * .10) if allowed else 0
        if account.share_events:
            capacity = max(0, capacity - session_filled[symbol])
        quantity = min(original, capacity) if allowed else 0
        final_order_quantity = original
        if side == "BUY" and quantity:
            quantity = quantity // 100 * 100
            if quantity:
                quantity = min(quantity, int(min(account.cash, account.equity()) / price / 100) * 100)
                final_order_quantity = quantity
            limits = _buy_limits(account, portfolio, symbol, price, [p for p in pending if p is not modeled],
                opening_cash, opening_equity, spent, gross, modeled["intent"]["target_weight"])
            cost = quantity * price + _fee("BUY", quantity, price, account.costs)
            active_count = sum(bool(account.quantity(s)) for s in account.positions)
            if (not limits or cost > min(limits["cash"], limits["strategy"]) + 1e-9
                    or quantity * price > min(limits["symbol"], limits["target"], limits["turnover"]) + 1e-9
                    or active_count >= portfolio["max_positions"]
                    or quantity * price > previous_equity * portfolio["max_symbol_exposure_bps"] / 10000 + 1e-9):
                quantity = 0
        matching = [trade for trade in fills if trade["order_id"] == order["order_id"]]
        _require(len(matching) == (1 if quantity else 0), "ORDER_FILL_MISSING_OR_UNEXPECTED")
        _require(order["quantity"] == final_order_quantity and order["filled_quantity"] == quantity
                 and order["remaining_quantity"] == final_order_quantity - quantity, "ORDER_FILL_TOTAL_CONFLICT")
        _require(order["status"] == ("FILLED" if quantity and quantity == final_order_quantity else
                 "EXPIRED" if quantity else "REJECTED"), "ORDER_FINAL_STATUS_CONFLICT")
        _same(order["total_fee"], _fee(side, quantity, price, account.costs) if quantity else 0., "ORDER_FEE_TOTAL_CONFLICT")
        if matching:
            _require(matching[0]["quantity"] == quantity and matching[0]["symbol"] == symbol
                     and matching[0]["side"] == side, "FILL_CAPACITY_OR_SCOPE_CONFLICT")
            if side == "SELL":
                eligible = [key for key, lot in account.lots.items() if lot["symbol"] == symbol
                    and account.sellable(lot, day)
                    and (order.get("lot_id") is None or key == order["lot_id"])]
                expected_lot = order.get("lot_id") or (eligible[0] if len(eligible) == 1 else None)
                _require(matching[0].get("lot_id") == expected_lot, "SELL_FIFO_LOT_ID_CONFLICT")
            account.fill(matching[0], day)
            fills_used.append(matching[0])
            if account.share_events:
                session_filled[symbol] += quantity
            if side == "BUY":
                gross += quantity * price
                spent += quantity * price + _fee(side, quantity, price, account.costs)
        pending.remove(modeled)
    _require(fills_used == fills, "FILL_SEQUENCE_OR_UNPLANNED_TRADE")
    return eligibility_skip_ids


def reconstruct_universe_account(bundle, window, result, *, initial_cash, costs, strategy_id, rule):
    """独立可重跑核验；失败直接拒绝，不以结果自报 reconciliation 为证据。"""
    from .formal_account_backend_v1 import normalized_costs
    from .research_rule_strategy_v3 import ResearchRuleStrategyV3
    from .universe_account_inputs_v1 import prepare_universe_account_inputs_v1
    strategy = ResearchRuleStrategyV3(rule, strategy_id=strategy_id)
    costs = normalized_costs(costs)
    inputs = prepare_universe_account_inputs_v1(bundle, window, stage="ACCOUNT",
        required_fields=strategy.requirements.fields, warmup_bars=strategy.requirements.warmup_sessions)
    _require(result["execution_version"] == _BACKEND and result["profile"] == inputs.bundle["profile"]
             and result["input_identity"] == inputs.input_identity, "INPUT_OR_BACKEND_IDENTITY_CONFLICT")
    _require(result["strategy_qualified"] is False and result["independent_confirmation_eligible"] is False,
             "UNSUPPORTED_QUALIFICATION")
    policy, portfolio = _policy(result, inputs, strategy, initial_cash, costs)
    frames, preparation = _condition_frames(strategy, inputs)
    _tree(result["scan_preparation"], preparation, "SCAN_PREPARATION_CONFLICT")
    scanner_identity = stable_hash({"version": "UNIVERSE_SIGNAL_SCAN_V1", "input_identity": inputs.input_identity,
                                  "rule_identity": strategy.rule_identity, "preparation": preparation})
    _require(result["scanner_identity"] == scanner_identity, "SCANNER_IDENTITY_CONFLICT")
    _require(result["execution_identity"] == stable_hash({"description": result["execution_description"],
        "rule": strategy.rule_identity, "scanner": scanner_identity}), "EXECUTION_IDENTITY_CONFLICT")
    first = inputs.session_index(inputs.window["account_start"])
    days = list(inputs.calendar[first:])
    _require(first > 0 and [row["date"] for row in result["daily_accounts"]] == days
             and [row["date"] for row in result["scan_days"]] == days
             and [row["date"] for row in result["decisions"]] == list(inputs.calendar[first - 1:-1]), "ACCOUNT_OR_SCAN_DATES_CONFLICT")
    checkpoint, fills = result["final_account_checkpoint"], result["fills"]
    skip_rows = checkpoint.get("skipped_intents")
    _require(isinstance(skip_rows, list) and all(isinstance(skip, dict)
             and isinstance(skip.get("intent_id"), str) and isinstance(skip.get("reason"), str)
             for skip in skip_rows), "SKIPPED_INTENTS_INVALID")
    skips = defaultdict(list)
    for skip in skip_rows:
        skips[skip["intent_id"]].append(skip)
    economic = checkpoint["economic"]
    _require(economic["trades"] == fills and not economic["open_order_ids"]
             and not checkpoint["invariant_errors"], "FINAL_EXPORT_OR_OPEN_ORDERS_CONFLICT")
    _require(len(economic["snapshots"]) == 2 * len(days), "SNAPSHOT_COVERAGE_CONFLICT")
    _require(len({t["trade_id"] for t in fills}) == len(fills), "DUPLICATE_TRADE")
    by_day, orders_by_day = defaultdict(list), defaultdict(list)
    previous_time = None
    for trade in fills:
        stamp = _time(trade["fill_time"])
        day = int(stamp.strftime("%Y%m%d"))
        _require(day in days and stamp == _stamp(day) and trade["symbol"] in inputs.symbols
                 and trade["strategy_id"] == strategy_id and (previous_time is None or previous_time <= stamp), "FILL_SCOPE_OR_TIME_CONFLICT")
        by_day[day].append(trade)
        previous_time = stamp
    for key, order in economic["orders"].items():
        day = int(_time(order["created_at"]).strftime("%Y%m%d"))
        _require(key == order["order_id"] and day in days, "ORDER_SCOPE_CONFLICT")
        orders_by_day[day].append(order)
    for rows in orders_by_day.values():
        rows.sort(key=lambda order: order["sequence"])
    account = _Reconstruction(inputs, strategy, _number(initial_cash), costs, policy)
    decisions, daily, peak, drawdown, previous_equity = [], [], float(initial_cash), 0., float(initial_cash)
    expected_eligibility_skip_ids = []
    for index, day in enumerate(days):
        decision_row = result["decisions"][index]
        _tree(decision_row["decisions"], decisions, "CONSUMED_DECISIONS_CONFLICT")
        previous_day = inputs.calendar[first + index - 1]
        _plan(decision_row["plan"], previous_day, day, decisions, account, portfolio, inputs)
        account.open_actions(day)
        account.mark(day, opening=True)
        expected_eligibility_skip_ids.extend(_execute_day(account, day, decision_row["plan"],
            orders_by_day[day], by_day[day], portfolio, previous_equity, skips))
        _tree(economic["snapshots"][2 * index], account.snapshot(day, opening=True), "OPEN_SNAPSHOT_CONFLICT")
        account.record_actions(day)
        account.mark(day)
        close_snapshot = account.snapshot(day)
        _tree(economic["snapshots"][2 * index + 1], close_snapshot, "CLOSE_SNAPSHOT_CONFLICT")
        equity = account.equity()
        positions = [{"strategy_id": strategy_id, "symbol": symbol, "quantity": account.quantity(symbol)}
                     for symbol in sorted(account.positions)]
        stale = [{"symbol": symbol, "status": "STALE_VERIFIED_SUSPENSION"} for symbol in account.positions
                 if account.quantity(symbol) and inputs.state(symbol, day)["suspension_status"] == "SUSPENDED"]
        reconstructed = {"date": day, "cash": account.cash, "equity": equity, "positions": positions,
                         "stale_valuations": stale}
        _tree(result["daily_accounts"][index], reconstructed, "DAILY_ACCOUNT_CONFLICT")
        daily.append(reconstructed)
        peak, previous_equity = max(peak, equity), close_snapshot["equity"]
        drawdown = max(drawdown, 1 - equity / peak)
        decisions, rows = account.decisions(day, frames)
        scan = result["scan_days"][index]
        _require(scan["target"] == scan["processed"] == len(inputs.symbols)
                 and [row["symbol"] for row in scan["rows"]] == list(inputs.symbols), "FULL_SCAN_COVERAGE_CONFLICT")
        _tree(scan["rows"], rows, "SCAN_CONDITIONS_OR_DECISIONS_CONFLICT")
        _require(scan["identity"] == stable_hash(rows), "SCAN_IDENTITY_CONFLICT")
    _require(sorted(skip["intent_id"] for skip in skip_rows
                    if skip["reason"] == "SECURITY_NOT_ELIGIBLE_AT_OPEN")
             == sorted(expected_eligibility_skip_ids), "UNEXPECTED_ENTRY_ELIGIBILITY_SKIP")
    if account.share_events:
        _require(sorted(skip["intent_id"] for skip in skip_rows if skip["reason"] == "NO_PERMITTED_QUANTITY")
                 == sorted(account.no_permitted_quantity_ids), "UNEXPECTED_ZERO_QUANTITY_SKIP")
    _same(economic["cash"], account.cash, "FINAL_CASH_CONFLICT")
    _same(economic["reserved_cash"], 0., "FINAL_RESERVED_CASH_CONFLICT")
    _same(checkpoint["equity"], daily[-1]["equity"], "FINAL_EQUITY_CONFLICT")
    _tree(economic["lots"], account.public_lots(), "FINAL_LOTS_CONFLICT")
    _tree(economic["positions"], {strategy_id + ":" + s: p for s, p in account.positions.items()}, "FINAL_POSITIONS_CONFLICT")
    _tree(economic["entitlements"], account.entitlements, "FINAL_ENTITLEMENTS_CONFLICT")
    _tree(economic["dividend_lots"], account.rights, "FINAL_DIVIDEND_RIGHTS_CONFLICT")
    _tree(economic["receivables"], account.receivables, "FINAL_RECEIVABLES_CONFLICT")
    _tree(economic["applied"], sorted(account.applied), "FINAL_APPLIED_ACTIONS_CONFLICT")
    _tree(economic["payments"], sorted(account.paid), "FINAL_PAYMENTS_CONFLICT")
    _tree(economic["action_audit"], account.action_audit, "FINAL_ACTION_AUDIT_CONFLICT")
    _same(economic["action_income"], account.income, "FINAL_DIVIDEND_INCOME_CONFLICT")
    _same(economic["dividend_tax_withheld"], account.tax, "FINAL_DIVIDEND_TAX_CONFLICT")
    if account.share_events:
        for field in ("pending_share_credits", "bonus_parent_lots", "share_price_factors",
                      "cash_price_adjustments", "dividend_record_factors", "share_tax_lots"):
            _tree(economic[field], getattr(account, field), "FINAL_" + field.upper() + "_CONFLICT")
        _same(economic["share_tax_withheld"], account.share_tax, "FINAL_SHARE_TAX_CONFLICT")
        _tree(economic["share_tax_timing"], {"policy": _TAX_POLICY, "tax_allocation_verified": False,
              "allocation_policy": "CHILD_LOTS_MODELED"}, "FINAL_SHARE_TAX_TIMING_CONFLICT")
        _tree(economic["last_exit_dates"], {strategy_id + ":" + symbol: inputs.calendar[index]
              for symbol, index in account.last_exit.items()}, "FINAL_LAST_EXIT_DATES_CONFLICT")
    _tree(economic["ledger_counters"], [sum(bool(lot["_starts_position"]) for lot in account.lots.values()),
        len(account.lots), len(fills)], "FINAL_LEDGER_COUNTERS_CONFLICT")
    _tree(checkpoint["rule_states"], {strategy_id + ":" + s: v for s, v in account.rule_states.items()}, "FINAL_RULE_STATES_CONFLICT")
    if strategy.exit_rules.enabled:
        _tree(checkpoint["rule_exit_states"], {strategy_id: {"price_policy": account.price_policy,
            "trailing": account.trailing, "evaluations": account.exit_rows}}, "EXIT_TRACE_OR_TRAILING_CONFLICT")
    else:
        _require(not checkpoint.get("rule_exit_states"), "UNCONFIGURED_EXIT_TRACE")
    metrics = {"net_return": daily[-1]["equity"] / initial_cash - 1, "max_drawdown": drawdown,
               "total_fees": account.fees, "trade_count": len(fills)}
    _tree(result["metrics"], metrics, "METRICS_CONFLICT")
    inputs.assert_unchanged()
    evidence = {"version": VERSION, "daily_accounts": daily, "metrics": metrics, "dividend_tax": account.tax,
        "exit_behavior": "VERIFIED" if strategy.exit_rules.enabled else "NOT_CONFIGURED", "exit_rows": account.exit_rows,
        "scanner_identity": scanner_identity, "board_policy_identity": result["board_policy_identity"],
        "input_identity": inputs.input_identity, "receivables": account.receivables,
        "method_scopes": {"cash_fifo_tax_receivables": "INDEPENDENT_ARITHMETIC",
            "opening_price_board_and_quantity": "INDEPENDENT_DECIMAL_AND_CAPACITY_ARITHMETIC",
            "signals": "RECOMPUTED_WITH_SHARED_INDICATOR_AND_DSL_FORMULAS",
            "full_universe_scan": "EXACT_SYMBOL_SET_EACH_ACCOUNT_SESSION",
            "risk_exits": "INDEPENDENT_CLOSE_ENTITLEMENT_AND_NEXT_OPEN_RECONSTRUCTION"},
        "unverified": ["历史状态可见时间与成本仍为模型；未验证券商实际扣款时刻。",
                       "没有开盘队列、盘口、盘中临停证据；涨跌停线开盘保守不成交。",
                       "指标公式和DSL实现复用；这不是对公式本身的独立数学证明。",
                       "窗口最后一天新买入的下一真实交易日不在输入中；该日不进行窗口外卖出。",
                       "历史盈利和独立核账不等于统计可靠、独立样本通过或Paper资格。"],
        "strategy_qualified": False, "independent_confirmation_eligible": False}
    if account.share_events:
        evidence.update(share_tax=account.share_tax, pending_share_credits=deepcopy(account.pending_share_credits),
                        tax_allocation_verified=False)
        evidence["method_scopes"]["share_entitlements_cost_credit_tradability_tax"] = "INDEPENDENT_RATIONAL_AND_FIFO_ARITHMETIC"
        evidence["unverified"].append("送股税款按有来源的CHILD_LOTS_MODELED分摊；未认证中国结算实际税权分配。")
    return evidence
