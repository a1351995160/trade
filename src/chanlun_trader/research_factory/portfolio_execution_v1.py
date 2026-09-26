"""前瞻模拟组合的计划与成交前风险；只有原引擎账本可结算资金与持仓。"""
from __future__ import annotations

from dataclasses import asdict, replace
import math
import re
from collections.abc import Mapping
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..engine.risk import RiskManager, RiskResult
from ..engine.signal import Side
from .common import jsonable, stable_hash
from .daily_plan import account_identity


class PortfolioMemberV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    strategy_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,120}$")
    rule_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    weight_bps: int = Field(ge=1, le=10000)
    priority: int = Field(ge=0)


class PortfolioExecutionPolicyV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    policy_id: str = Field(min_length=1)
    members: tuple[PortfolioMemberV1, ...]
    purpose: Literal["ENGINEERING_OBSERVATION", "FORMAL_OBSERVATION"]
    max_positions: int = Field(ge=1)
    max_symbol_exposure_bps: int = Field(ge=1, le=10000)
    max_buy_turnover_bps: int = Field(ge=0, le=10000)
    valid_until: str
    overlap: Literal["ONE_STRATEGY_PER_SYMBOL", "SEPARATE_STRATEGY_LOTS"] = "SEPARATE_STRATEGY_LOTS"
    lot_size: int = Field(default=100, ge=1)

    @model_validator(mode="after")
    def validate_scope(self):
        if len({member.strategy_id for member in self.members}) != len(self.members):
            raise ValueError("PORTFOLIO_DUPLICATE_MEMBER")
        if sum(member.weight_bps for member in self.members) > 10000:
            raise ValueError("PORTFOLIO_OVERALLOCATED")
        _stamp(self.valid_until)
        return self

    @property
    def content_hash(self):
        return stable_hash(self.model_dump(mode="json"))


def _stamp(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("PORTFOLIO_AWARE_TIME_REQUIRED")
    return stamp.tz_convert("Asia/Shanghai")


def _admission_identity(admissions):
    return {key: {field: value.get(field) for field in
                  ("allowed", "strategy_qualified", "archive_hash", "review_hash", "source_profile")}
            for key, value in sorted(admissions.items())}


def _admitted(value, purpose):
    return (isinstance(value, Mapping) and value.get("allowed") is True
            and isinstance(value.get("archive_hash"), str)
            and re.fullmatch(r"[0-9a-f]{64}", value["archive_hash"]) is not None
            and (purpose != "FORMAL_OBSERVATION" or (value.get("strategy_qualified") is True
                 and isinstance(value.get("review_hash"), str)
                 and re.fullmatch(r"[0-9a-f]{64}", value["review_hash"]) is not None)))


def build_portfolio_plan(*, policy, decisions, ledger, admissions,
                         input_identity, decision_at, next_session):
    """admissions 必须由调用方从权威档案读取，不能用用户提供的布尔值替代。"""
    policy = PortfolioExecutionPolicyV1.model_validate(policy.model_dump())
    stamp = _stamp(decision_at)
    if type(next_session) is not int:
        raise ValueError("PORTFOLIO_NEXT_SESSION_REQUIRED")
    next_day = pd.Timestamp(str(next_session), tz="Asia/Shanghai")
    if next_day.normalize() <= stamp.normalize() or not input_identity:
        raise ValueError("PORTFOLIO_INPUT_OR_SESSION_INVALID")
    if ledger.check_invariants():
        raise ValueError("PORTFOLIO_LEDGER_INVALID")
    members = {member.strategy_id: member for member in policy.members}
    if set(admissions) != set(members):
        raise ValueError("PORTFOLIO_MEMBER_ADMISSIONS_REQUIRED")
    valid = {key for key, value in admissions.items() if _admitted(value, policy.purpose)}
    if stamp >= _stamp(policy.valid_until) or next_day >= _stamp(policy.valid_until):
        valid = set()
    normalized, seen = [], set()
    for decision in decisions:
        key = (decision["strategy_id"], decision["symbol"])
        if key in seen or key[0] not in members or decision["side"] not in ("BUY", "SELL", "HOLD"):
            raise ValueError("PORTFOLIO_DECISION_CONFLICT")
        if not isinstance(key[1], str) or not key[1]:
            raise ValueError("PORTFOLIO_SYMBOL_INVALID")
        seen.add(key)
        target_weight = decision.get("target_weight", 1.0)
        if (isinstance(target_weight, bool) or not isinstance(target_weight, (int, float))
                or not math.isfinite(target_weight) or not 0 <= target_weight <= 1):
            raise ValueError("PORTFOLIO_TARGET_WEIGHT_INVALID")
        normalized.append({"strategy_id": key[0], "symbol": key[1], "side": decision["side"],
                           "priority": members[key[0]].priority,
                           "target_weight": float(target_weight),
                           "reason": str(decision.get("reason", "STRATEGY_DECISION"))})
    holdings = [jsonable(asdict(lot)) for lot in ledger.lots.values() if lot.remaining_quantity]
    unknown_holdings = any(lot["strategy_id"] not in members for lot in holdings)
    exits = {item["symbol"] for item in normalized if item["side"] == "SELL"
             and ledger.position_qty(item["strategy_id"], item["symbol"]) > 0}
    owners = {}
    for lot in holdings:
        owners.setdefault(lot["symbol"], set()).add(lot["strategy_id"])
    intents, excluded = [], []
    for item in sorted(normalized, key=lambda row: (row["side"] != "SELL", row["priority"], row["strategy_id"], row["symbol"])):
        reason = None
        if item["side"] == "HOLD":
            continue
        if item["side"] == "BUY":
            if unknown_holdings:
                reason = "UNASSIGNED_HOLDINGS"
            elif item["strategy_id"] not in valid:
                reason = "STRATEGY_NOT_ADMITTED_OR_POLICY_EXPIRED"
            elif item["symbol"] in exits:
                reason = "EXIT_BUY_CONFLICT"
            elif policy.overlap == "ONE_STRATEGY_PER_SYMBOL" and owners.get(item["symbol"], set()) - {item["strategy_id"]}:
                reason = "SYMBOL_OWNED_BY_OTHER_STRATEGY"
        elif ledger.position_qty(item["strategy_id"], item["symbol"]) <= 0:
            reason = "NO_OWNED_POSITION"
        if reason:
            excluded.append({**item, "reason": reason})
        else:
            intents.append(item)
            if item["side"] == "BUY":
                owners.setdefault(item["symbol"], set()).add(item["strategy_id"])
    payload = {"schema_version": "PORTFOLIO_PAPER_PLAN_V1", "decision_at": stamp.isoformat(),
               "next_session": next_session, "input_identity": input_identity,
               "account_identity": account_identity(ledger), "policy_hash": policy.content_hash,
               "members": [member.model_dump() for member in policy.members],
               "admissions": _admission_identity(admissions), "intents": intents, "excluded": excluded,
               "holdings": holdings, "status": "PLANNED" if valid else "NO_ADMITTED_STRATEGIES",
               "usage_qualified": bool(valid) and len(valid) == len(members) and
                    policy.purpose == "FORMAL_OBSERVATION",
               "real_execution_authorized": False,
               "limits": ["OPEN_PRICE_AND_FEE_RECHECK", "SELL_PROCEEDS_NOT_SPENDABLE",
                          "SIMULATED_EXECUTION_ONLY"]}
    # intent 身份不依赖执行时的订单序号，Paper事务可据此映射幂等回执。
    for index, intent in enumerate(intents):
        intent["intent_id"] = "PORTFOLIO_INTENT_" + stable_hash([payload["account_identity"],
            policy.content_hash, input_identity, stamp.isoformat(), next_session, index, dict(intent)])
    payload["plan_id"] = "PORTFOLIO_PAPER_" + stable_hash(payload)
    return payload


def validate_portfolio_plan(plan, *, policy, ledger, input_identity, event_at, admissions):
    if plan.get("plan_id") != "PORTFOLIO_PAPER_" + stable_hash({key: value for key, value in plan.items() if key != "plan_id"}):
        raise ValueError("PORTFOLIO_PLAN_HASH_CONFLICT")
    stamp = _stamp(event_at)
    if (stamp <= _stamp(plan["decision_at"]) or int(stamp.strftime("%Y%m%d")) != plan["next_session"]):
        raise ValueError("PORTFOLIO_PLAN_SESSION_CHANGED")
    if (plan["policy_hash"] != policy.content_hash or plan["account_identity"] != account_identity(ledger)
            or plan["input_identity"] != input_identity):
        raise ValueError("PORTFOLIO_PLAN_CONTEXT_CHANGED")
    # 撤销不隐去原持仓。成交风险会再次拒绝买入，退出意图仍可处理。
    if set(admissions) != {member.strategy_id for member in policy.members}:
        raise ValueError("PORTFOLIO_MEMBER_ADMISSIONS_REQUIRED")
    for member in policy.members:
        old, current = plan["admissions"][member.strategy_id], admissions[member.strategy_id]
        if old["archive_hash"] != current.get("archive_hash"):
            raise ValueError("PORTFOLIO_STRATEGY_VERSION_CHANGED")
    return True


class PortfolioExecutionRiskV1(RiskManager):
    """叠加在原成交前校验上的组合限制；不修改账本，不预支卖出款。"""
    def __init__(self, ledger, config, *, policy, fee_model, admission_check,
                 orders_provider, prices, session_at, universe=None):
        # Broker原逻辑还会按1/max_positions二次缩量。组合已经显式分配数量，
        # 向Broker提供中性上限；全部原风控仍由base_risk和下方组合校验执行。
        self.base_config = config
        broker_config = replace(config, max_positions=1, max_position_weight=1.0)
        super().__init__(ledger, broker_config, universe=universe)
        self.base_risk = RiskManager(ledger, config, universe=universe)
        self.policy, self.fee_model = policy, fee_model
        self.admission_check, self.orders_provider = admission_check, orders_provider
        self.prices = dict(prices)
        self.session_at = _stamp(session_at)
        self.opening_cash = ledger.available_cash()
        self.opening_equity = ledger.current_equity()
        self.start_trade_count = len(ledger.trades)
        self.members = {member.strategy_id: member for member in policy.members}
        self.admission_hashes = {key: admission_check(key).get("archive_hash") for key in self.members}

    def _price(self, symbol):
        value = float(self.prices.get(symbol, self.ledger.last_price.get(symbol, float("nan"))))
        if not math.isfinite(value) or value <= 0:
            raise ValueError("PORTFOLIO_OBSERVED_PRICE_MISSING")
        return value

    def _limits(self, strategy_id, symbol, price, ts, *, exclude=None, target_weight=1.0):
        if (isinstance(target_weight, bool) or not isinstance(target_weight, (int, float))
                or not math.isfinite(target_weight) or not 0 <= target_weight <= 1):
            raise ValueError("PORTFOLIO_TARGET_WEIGHT_INVALID")
        if strategy_id not in self.members:
            raise ValueError("PORTFOLIO_MEMBER_UNKNOWN")
        if _stamp(ts) >= _stamp(self.policy.valid_until):
            raise ValueError("PORTFOLIO_POLICY_EXPIRED")
        current_admission = self.admission_check(strategy_id)
        if not _admitted(current_admission, self.policy.purpose):
            raise ValueError("PORTFOLIO_STRATEGY_NOT_ADMITTED")
        if current_admission.get("archive_hash") != self.admission_hashes[strategy_id]:
            raise ValueError("PORTFOLIO_STRATEGY_VERSION_CHANGED")
        if not math.isfinite(price) or price <= 0 or self.ledger.check_invariants():
            raise ValueError("PORTFOLIO_ACCOUNT_OR_PRICE_INVALID")
        equity = self.ledger.current_equity()
        if not all(math.isfinite(value) and value >= 0 for value in (equity, self.ledger.available_cash(), self.opening_cash)):
            raise ValueError("PORTFOLIO_ACCOUNT_OR_PRICE_INVALID")
        exposure = strategy_exposure = pending_cost = pending_symbol = pending_strategy = pending_gross = 0.0
        strategy_symbol_exposure = pending_strategy_symbol = 0.0
        position_keys = set()
        for position in self.ledger.positions.values():
            if position.quantity <= 0:
                continue
            if position.strategy_id not in self.members:
                raise ValueError("PORTFOLIO_UNASSIGNED_HOLDINGS")
            value = position.quantity * (price if position.symbol == symbol else self._price(position.symbol))
            position_keys.add((position.strategy_id, position.symbol))
            exposure += value if position.symbol == symbol else 0
            strategy_exposure += value if position.strategy_id == strategy_id else 0
            if position.strategy_id == strategy_id and position.symbol == symbol:
                strategy_symbol_exposure += value
        if (self.policy.overlap == "ONE_STRATEGY_PER_SYMBOL"
                and any(owner != strategy_id and ticker == symbol for owner, ticker in position_keys)):
            raise ValueError("PORTFOLIO_SYMBOL_OWNED_BY_OTHER_STRATEGY")
        for order in self.orders_provider():
            if not order.is_open or order is exclude or (exclude is not None and order.order_id and order.order_id == exclude.order_id):
                continue
            if order.strategy_id not in self.members:
                raise ValueError("PORTFOLIO_UNASSIGNED_ORDER")
            if order.side == Side.SELL:
                if order.symbol == symbol:
                    raise ValueError("PORTFOLIO_EXIT_BUY_CONFLICT")
                continue
            value = order.remaining_quantity * (price if order.symbol == symbol else self._price(order.symbol))
            fee = self.fee_model.calc("BUY", order.remaining_quantity,
                                      price if order.symbol == symbol else self._price(order.symbol)).total_fee
            pending_cost += value + fee
            pending_gross += value
            pending_symbol += value if order.symbol == symbol else 0
            pending_strategy += value + fee if order.strategy_id == strategy_id else 0
            if order.strategy_id == strategy_id and order.symbol == symbol:
                pending_strategy_symbol += value
            position_keys.add((order.strategy_id, order.symbol))
            if (self.policy.overlap == "ONE_STRATEGY_PER_SYMBOL" and order.symbol == symbol
                    and order.strategy_id != strategy_id):
                raise ValueError("PORTFOLIO_SYMBOL_OWNED_BY_OTHER_STRATEGY")
        if (strategy_id, symbol) not in position_keys and len(position_keys) >= self.policy.max_positions:
            raise ValueError("PORTFOLIO_POSITION_LIMIT")
        bought = [trade for trade in self.ledger.trades[self.start_trade_count:] if trade.side == Side.BUY]
        spent = sum(trade.gross_value + trade.fee for trade in bought)
        gross = sum(trade.gross_value for trade in bought)
        return {
            "cash": max(0., min(self.ledger.available_cash(), self.opening_cash - spent) - pending_cost),
            "symbol": max(0., equity * self.policy.max_symbol_exposure_bps / 10000 - exposure - pending_symbol),
            "strategy": max(0., equity * self.members[strategy_id].weight_bps / 10000 - strategy_exposure - pending_strategy),
            "target": max(0., equity * self.members[strategy_id].weight_bps / 10000 * target_weight
                          - strategy_symbol_exposure - pending_strategy_symbol),
            "turnover": max(0., self.opening_equity * self.policy.max_buy_turnover_bps / 10000 - gross - pending_gross),
        }

    def buy_quantity(self, strategy_id, symbol, price, ts, *, target_weight=1.0):
        try:
            limits = self._limits(strategy_id, symbol, price, ts, target_weight=target_weight)
        except ValueError:
            return 0
        quantity = int(min(limits.values()) / price / self.policy.lot_size) * self.policy.lot_size
        while quantity > 0:
            cost = quantity * price + self.fee_model.calc("BUY", quantity, price).total_fee
            if cost <= min(limits["cash"], limits["strategy"]) + 1e-9:
                break
            quantity -= self.policy.lot_size
        return quantity

    def pre_trade(self, order, ts, estimated_price, index_ok=True):
        if order.side == Side.SELL:
            if order.lot_id:
                lot = self.ledger.lots.get(order.lot_id)
                if lot is None or lot.strategy_id != order.strategy_id or lot.symbol != order.symbol:
                    return RiskResult(False, "PORTFOLIO_LOT_OWNER_CONFLICT")
            return self.base_risk.pre_trade(order, ts, estimated_price, index_ok=index_ok)
        try:
            limits = self._limits(order.strategy_id, order.symbol, estimated_price, ts, exclude=order,
                                  target_weight=order.metadata.get("target_weight", 1.0))
        except ValueError as exc:
            return RiskResult(False, str(exc))
        value = order.quantity * estimated_price
        cost = value + self.fee_model.calc("BUY", order.quantity, estimated_price).total_fee
        if (cost > limits["cash"] + 1e-9 or cost > limits["strategy"] + 1e-9
                or value > limits["symbol"] + 1e-9 or value > limits["turnover"] + 1e-9
                or value > limits["target"] + 1e-9):
            return RiskResult(False, "PORTFOLIO_CASH_FEE_EXPOSURE_OR_TURNOVER_LIMIT")
        return self.base_risk.pre_trade(order, ts, estimated_price, index_ok=index_ok)
