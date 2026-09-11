"""共享账本上的组合研究预览；组合政策与内容身份均不授予运行资格。"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..engine.fee import ChinaAStockFeeModel
from .common import stable_hash
from .daily_plan import account_identity, preview_daily_plan


class StrategyAllocationV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    candidate_id: str = Field(min_length=1)
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cash_bps: int = Field(ge=0, le=10000)
    priority: int = Field(ge=0)


class PortfolioPreviewPolicyV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    allocations: tuple[StrategyAllocationV1, ...]
    overlap: Literal["ONE_STRATEGY_PER_SYMBOL", "SEPARATE_STRATEGY_LOTS"]
    buy_sell_conflict: Literal["BLOCK_BUY_WHEN_EXIT_DUE"]
    max_positions: int = Field(ge=1)
    max_symbol_exposure_bps: int = Field(ge=1, le=10000)
    max_buy_turnover_bps: int = Field(ge=0, le=10000)
    valid_until: str

    @model_validator(mode="after")
    def validate_policy(self):
        if len({item.candidate_id for item in self.allocations}) != len(self.allocations):
            raise ValueError("PORTFOLIO_DUPLICATE_STRATEGY")
        if sum(item.cash_bps for item in self.allocations) > 10000:
            raise ValueError("PORTFOLIO_CASH_OVERALLOCATED")
        expiry = pd.Timestamp(self.valid_until)
        if pd.isna(expiry) or expiry.tzinfo is None:
            raise ValueError("PORTFOLIO_EXPIRY_AWARE_REQUIRED")
        return self

    @property
    def content_hash(self):
        return stable_hash(self.model_dump(mode="json"))


def preview_portfolio_plan(policy: PortfolioPreviewPolicyV1, sources: dict, ledger, *, plan_at) -> dict:
    """sources由正式caller准备；每个值为record/contract/policy/inputs，不接受计划结果注入。"""
    policy = PortfolioPreviewPolicyV1.model_validate(policy.model_dump())
    stamp = pd.Timestamp(plan_at)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("PORTFOLIO_TIME_AWARE_REQUIRED")
    stamp = stamp.tz_convert("Asia/Shanghai")
    if stamp != stamp.normalize() + pd.Timedelta(hours=15):
        raise ValueError("PORTFOLIO_T_CLOSE_REQUIRED")
    if (not all(math.isfinite(value) and value >= 0 for value in (ledger.cash, ledger.reserved_cash, ledger.current_equity()))
            or ledger.reserved_cash > ledger.cash):
        raise ValueError("PORTFOLIO_ACCOUNT_INVALID")
    if ledger.check_invariants():
        raise ValueError("PORTFOLIO_ACCOUNT_INVARIANTS")
    before = account_identity(ledger)
    allowed = {item.candidate_id for item in policy.allocations}
    if any(lot.remaining_quantity and lot.strategy_id not in allowed for lot in ledger.lots.values()):
        raise ValueError("PORTFOLIO_UNASSIGNED_HOLDINGS")
    reasons, plans, entries, holdings = [], [], [], []
    remaining_cash = ledger.available_cash()
    equity = ledger.current_equity()
    buy_capacity = equity * policy.max_buy_turnover_bps / 10000
    exposure = {}
    owners = {}
    for position in ledger.positions.values():
        if position.quantity:
            exposure[position.symbol] = exposure.get(position.symbol, 0.) + position.quantity * ledger.last_price.get(position.symbol, position.average_cost)
            owners.setdefault(position.symbol, set()).add(position.strategy_id)
    slots = sum(position.quantity > 0 for position in ledger.positions.values())
    expired = stamp > pd.Timestamp(policy.valid_until)
    for allocation in sorted(policy.allocations, key=lambda item: (item.priority, item.candidate_id)):
        candidate_id = allocation.candidate_id
        source = sources.get(candidate_id)
        reason = "POLICY_EXPIRED" if expired else "STRATEGY_INPUT_MISSING" if source is None else None
        if not reason and (source["contract"].candidate_id != candidate_id or source["contract"].content_hash != allocation.contract_hash):
            reason = "STRATEGY_VERSION_CHANGED"
        if reason:
            reasons.append({"candidate_id": candidate_id, "reason": reason})
            holdings.extend({"candidate_id": candidate_id, "lot_id": lot.lot_id, "symbol": lot.symbol,
                "action": "NOT_READY", "reason": reason, "quantity": lot.remaining_quantity,
                "sellable_quantity": ledger.sellable_lot_quantity(lot.lot_id, stamp), "source_plan_id": None}
                for lot in ledger.lots.values() if lot.strategy_id == candidate_id and lot.remaining_quantity)
            continue
        # 子视图只投影真实账本；不写回、不产生第二套账务真相。
        view = deepcopy(ledger)
        view.cash = ledger.available_cash() * allocation.cash_bps / 10000
        view.reserved_cash = 0.
        view.positions = {key: value for key, value in view.positions.items() if value.strategy_id == candidate_id}
        view.lots = {key: value for key, value in view.lots.items() if value.strategy_id == candidate_id}
        plan = preview_daily_plan(source["record"], source["contract"], source["policy"], source["inputs"], view, plan_at=stamp)
        plans.append(plan)
        holdings.extend({**item, "candidate_id": candidate_id, "source_plan_id": plan["plan_id"]} for item in plan["holdings"])
        if plan["status"] == "NOT_READY":
            reasons.append({"candidate_id": candidate_id, "reason": "STRATEGY_DATA_NOT_READY"})
    exits = {item["symbol"] for item in holdings if item["action"] == "EXIT"}
    # 先收齐所有退出，再分配买入，避免顺序使低优先级退出被忽略。
    for plan in plans:
        candidate_id = plan["candidate_id"]
        execution = sources[candidate_id]["policy"]
        fee = ChinaAStockFeeModel(execution.commission_rate, execution.min_commission, execution.stamp_tax_rate)
        for entry in plan["entries"]:
            quantity, reason = entry["quantity"], entry["reason"]
            symbol, price = entry["symbol"], entry["reference_price"]
            if reasons:
                quantity, reason = 0, "PORTFOLIO_MEMBER_NOT_READY"
            elif symbol in exits:
                quantity, reason = 0, "EXIT_BUY_CONFLICT"
            elif policy.overlap == "ONE_STRATEGY_PER_SYMBOL" and owners.get(symbol, set()) - {candidate_id}:
                quantity, reason = 0, "SYMBOL_OWNED_BY_OTHER_STRATEGY"
            elif slots >= policy.max_positions:
                quantity, reason = 0, "PORTFOLIO_POSITION_LIMIT"
            while quantity:
                gross = quantity * price
                cost = gross + fee.calc("BUY", quantity, price).total_fee
                if (cost <= remaining_cash and gross <= buy_capacity
                        and exposure.get(symbol, 0.) + gross <= equity * policy.max_symbol_exposure_bps / 10000):
                    break
                quantity = max(0, quantity - execution.lot_size)
                reason = "PORTFOLIO_CASH_EXPOSURE_OR_TURNOVER_LIMIT"
            estimated_fee = fee.calc("BUY", quantity, price).total_fee if quantity else 0.
            remaining_cash -= quantity * price + estimated_fee
            buy_capacity -= quantity * price
            exposure[symbol] = exposure.get(symbol, 0.) + quantity * price
            if quantity:
                owners.setdefault(symbol, set()).add(candidate_id)
                slots += 1
            entries.append({**entry, "quantity": quantity, "estimated_fee": estimated_fee,
                "action": "BUY_PLAN" if quantity else "NO_TRADE", "reason": reason,
                "candidate_id": candidate_id, "source_plan_id": plan["plan_id"]})
    payload = {"schema_version": "portfolio-research-preview-v1", "plan_at": str(stamp),
        "policy": policy.model_dump(mode="json"), "policy_hash": policy.content_hash,
        "account_identity": before, "source_plans": plans, "entries": entries, "holdings": holdings,
        "cash": ledger.cash, "available_cash": ledger.available_cash(), "unallocated_cash": remaining_cash,
        "equity": equity, "excluded": reasons,
        "status": "NOT_READY" if reasons else "RESEARCH_PREVIEW" if plans else "NO_STRATEGIES",
        "strategy_count": len(plans), "execution_ready": False, "usage_qualification": "NOT_VERIFIED",
        "limitations": ["QUALIFIED_STRATEGIES_REQUIRED", "APPROVED_PORTFOLIO_POLICY_REQUIRED", "SELL_PROCEEDS_NOT_SPENDABLE", "T_CLOSE_ESTIMATES_ONLY"]}
    payload["plan_id"] = "PORTFOLIO_PLAN_" + stable_hash(payload)
    return payload
