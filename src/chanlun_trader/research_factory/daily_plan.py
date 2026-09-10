"""冻结策略的每日研究预览；复用正式语义，不产生策略使用资格或执行许可。"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import math
import json
import os
from pathlib import Path
import re

import pandas as pd

from ..engine.portfolio_exit import PortfolioExitEvaluatorV1
from ..engine.fee import ChinaAStockFeeModel
from ..engine.slippage import FixedBpsSlippage
from ..engine.signal import PortfolioTarget, TargetType
from ..engine.sizing import FixedSlotSizer, LotSizeModel
from .common import stable_hash, canonical_json
from .source_dependencies import load_corrected_module


def account_identity(ledger) -> str:
    return stable_hash({"initial_cash": ledger.initial_cash, "cash": ledger.cash,
        "reserved_cash": ledger.reserved_cash, "prices": ledger.last_price,
        "positions": {key: asdict(value) for key, value in ledger.positions.items()},
        "lots": {key: asdict(value) for key, value in ledger.lots.items()}})


def preview_daily_plan(record, contract, policy, inputs, ledger, *, plan_at) -> dict:
    """仅接受caller已经准备的输入；预览结果不能直接提交订单。"""
    stamp = pd.Timestamp(plan_at)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("PLAN_TIME_AWARE_REQUIRED")
    stamp = stamp.tz_convert("Asia/Shanghai")
    day = int(stamp.strftime("%Y%m%d"))
    if stamp != stamp.normalize() + pd.Timedelta(hours=15):
        raise ValueError("PLAN_T_CLOSE_REQUIRED")
    calendar = inputs["exec_calendar"]
    if day not in calendar or not policy.research_start <= day <= policy.research_end:
        raise ValueError("PLAN_OUTSIDE_FROZEN_WINDOW")
    if (contract.reconstruct_candidate().to_dict() != record.to_dict()
            or inputs["input_diagnostics"]["contract_hash"] != contract.content_hash):
        raise ValueError("PLAN_CONTRACT_IDENTITY_CONFLICT")
    if not all(math.isfinite(value) and value >= 0 for value in (ledger.cash, ledger.reserved_cash)):
        raise ValueError("PLAN_ACCOUNT_INVALID")
    if ledger.reserved_cash > ledger.cash:
        raise ValueError("PLAN_ACCOUNT_INVALID")
    if ledger.check_invariants():
        raise ValueError("PLAN_ACCOUNT_INVARIANTS")
    if any(lot.strategy_id != record.candidate.candidate_id for lot in ledger.lots.values()):
        raise ValueError("PLAN_ACCOUNT_STRATEGY_CONFLICT")
    if any(lot.buy_time > stamp for lot in ledger.lots.values()):
        raise ValueError("PLAN_FUTURE_POSITION")
    corrected = load_corrected_module()
    corrected.validate_corrected_inputs(record, policy, inputs["store"], calendar, inputs["regimes"], inputs["factor_values"])
    working = deepcopy(ledger)
    before = account_identity(ledger)
    positions = corrected.session_index(calendar)
    for lot in working.lots.values():
        entry_day = int(lot.buy_time.strftime("%Y%m%d"))
        if entry_day not in positions:
            raise ValueError("PLAN_POSITION_CALENDAR_NOT_READY")
        lot.entry_session = entry_day
        lot.entry_session_index = positions[entry_day]
    next_days = {value: calendar[index + 1] for index, value in enumerate(calendar[:-1])}
    if day not in inputs["universe"]:
        raise ValueError("PLAN_PIT_NOT_READY")
    required = inputs["universe"][day] | {lot.symbol for lot in working.lots.values() if lot.remaining_quantity}
    day_factors = inputs["factor_values"].loc[inputs["factor_values"].date == day]
    if not required <= set(day_factors.symbol):
        raise ValueError("PLAN_FACTOR_COVERAGE")
    for position in working.positions.values():
        if position.quantity:
            bar = inputs["store"].get_daily_bar(position.symbol, day, price_mode="raw")
            if not bar or not math.isfinite(float(bar["close"])) or bar["close"] <= 0:
                raise ValueError("PLAN_PRICE_NOT_READY")
            working.mark_to_market(position.symbol, float(bar["close"]), stamp)
    stats = Counter()
    rows, factors = corrected.build_corrected_day(record, {
        **inputs, "factor_view": inputs["factor_values"].set_index(["date", "symbol"], drop=False),
        "calendar_pos": positions, "date_to_next": next_days,
    }, working, day, stamp, stats)
    compiler = corrected.legacy.StrategyCandidateCompilerV2().compile(record)
    readiness = sorted({item.qualification_reason for item in compiler.qualify_rows(rows)
        if item.qualification_reason.startswith(("FUTURE_", "INVALID_", "MISSING_", "FACTOR_AVAILABLE_AT_", "NON_FINITE_"))})
    if stats["INVALID_FACTOR_AVAILABLE_AT"]:
        readiness.append("INVALID_FACTOR_AVAILABLE_AT")
    if day not in next_days:
        readiness.append("NEXT_SESSION_NOT_AVAILABLE")
    signals = compiler.emit_entry_signals(rows) if day in next_days else []
    evaluator = PortfolioExitEvaluatorV1(candidate_id=record.candidate.candidate_id,
        portfolio_id=record.candidate.candidate_id, contract={
            "exit_type": record.exit_predicate.exit_type,
            "fixed_holding_sessions": int(record.candidate.holding_period),
            "factor_conditions": list(record.exit_predicate.factor_conditions),
            "logic": record.exit_predicate.logic,
        })
    exits = evaluator.evaluate(working.lots.values(), day, positions[day], factors, stamp)
    due = {item.lot_id: item for item in exits}
    holdings = [{"lot_id": lot.lot_id, "symbol": lot.symbol,
        "action": "EXIT" if lot.lot_id in due else "HOLD",
        "reason": due[lot.lot_id].reason_code if lot.lot_id in due else "HOLDING_CONTRACT_NOT_DUE",
        "quantity": lot.remaining_quantity, "sellable_quantity": working.sellable_lot_quantity(lot.lot_id, stamp),
        "sellable_from": str(lot.sellable_from)} for lot in working.lots.values() if lot.remaining_quantity]
    entries = []
    sizer, lots = FixedSlotSizer(policy.max_positions), LotSizeModel(policy.lot_size)
    fee = ChinaAStockFeeModel(policy.commission_rate, policy.min_commission, policy.stamp_tax_rate)
    slip = FixedBpsSlippage(policy.slippage_bps)
    for signal in signals:
        bar = inputs["store"].get_daily_bar(signal.symbol, day, price_mode="raw")
        price = float(bar["close"]) if bar else 0.0
        if not math.isfinite(price) or price <= 0:
            raise ValueError("PLAN_PRICE_NOT_READY")
        price = slip.apply("BUY", price)
        target = PortfolioTarget(record.candidate.candidate_id, signal.symbol, TargetType.WEIGHT,
            1 / policy.max_positions, stamp)
        quantity = sizer.size_buy(target, working, price, lots, policy.max_positions)
        slots = sum(position.quantity > 0 for position in working.positions.values()) + sum(item["quantity"] > 0 for item in entries)
        if slots >= policy.max_positions:
            quantity = 0
        while quantity and quantity * price + fee.calc("BUY", quantity, price).total_fee > working.available_cash():
            quantity = lots.round_buy(quantity - policy.lot_size)
        estimated_fee = fee.calc("BUY", quantity, price).total_fee if quantity else 0.0
        # 这里只为预览分配可用现金；正式成交仍由同一ledger/broker结算。
        working.reserved_cash += quantity * price + estimated_fee
        entries.append({"symbol": signal.symbol, "action": "BUY_PLAN" if quantity else "NO_TRADE",
            "quantity": quantity, "reference_price": price, "price_basis": "T_CLOSE_ESTIMATE",
            "estimated_fee": estimated_fee,
            "reason": signal.reason if quantity else "CASH_LOT_OR_POSITION_LIMIT",
            "score": signal.score, "signal_time": str(signal.generated_at),
            "earliest_execution": str(corrected.legacy.ts_for_date(next_days[day], 9, 30))})
    payload = {"schema_version": "daily-plan-preview-v1", "plan_at": str(stamp),
        "candidate_id": record.candidate.candidate_id, "contract_hash": contract.content_hash,
        "policy_hash": stable_hash(policy.to_dict()), "input_identity": inputs["input_diagnostics"]["input_identity"],
        "account_identity": before, "source_identity": {"corrected_sha256": corrected.legacy.sha256(Path(corrected.__file__))},
        "entries": entries, "holdings": holdings, "signal_diagnostics": dict(stats),
        "status": "NOT_READY" if readiness else "RESEARCH_PREVIEW" if entries or holdings else "NO_TRADE",
        "readiness_reasons": readiness,
        "execution_ready": False, "usage_qualification": "NOT_VERIFIED",
        "invalidated_by": ["ACCOUNT_CHANGED", "CONTRACT_CHANGED", "DATA_CHANGED", "NEXT_SESSION_PASSED"],
        "limitations": ["FEES_AND_FILL_RECHECK_REQUIRED", "QUALIFIED_STRATEGY_REQUIRED", "WITHIN_FROZEN_RESEARCH_WINDOW_ONLY"]}
    payload["plan_id"] = "PLAN_" + stable_hash(payload)
    return payload


class DailyPlanArchiveV1:
    """保存不可覆盖的研究预览；读取历史不会使历史计划重新有效。"""
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _path(self, plan_id: str) -> Path:
        if not re.fullmatch(r"PLAN_[0-9a-f]{64}", plan_id):
            raise ValueError("PLAN_ID_INVALID")
        return self.path / (plan_id + ".json")

    @staticmethod
    def _verify(plan: dict) -> None:
        if (plan.get("schema_version") != "daily-plan-preview-v1"
                or plan.get("execution_ready") is not False
                or plan.get("usage_qualification") != "NOT_VERIFIED"
                or plan.get("plan_id") != "PLAN_" + stable_hash({key: value for key, value in plan.items() if key != "plan_id"})):
            raise ValueError("PLAN_ARCHIVE_IDENTITY_CONFLICT")

    def publish(self, plan: dict) -> Path:
        self._verify(plan)
        target = self._path(plan["plan_id"])
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("x", encoding="utf-8") as stream:
                stream.write(canonical_json(plan) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if self.read(plan["plan_id"]) != json.loads(canonical_json(plan)):
                raise ValueError("PLAN_ARCHIVE_IDENTITY_CONFLICT")
        return target

    def read(self, plan_id: str) -> dict:
        plan = json.loads(self._path(plan_id).read_text(encoding="utf-8"))
        self._verify(plan)
        if plan["plan_id"] != plan_id:
            raise ValueError("PLAN_ARCHIVE_IDENTITY_CONFLICT")
        return plan

    def compare(self, plan_id: str, current_preview: dict) -> dict:
        old = self.read(plan_id)
        self._verify(current_preview)
        changed = [key for key in ("candidate_id", "contract_hash", "policy_hash", "input_identity",
                   "account_identity", "source_identity", "plan_at") if old[key] != current_preview[key]]
        return {"plan_id": plan_id, "current_plan_id": current_preview["plan_id"],
                "status": "STALE" if changed else "CURRENT_RESEARCH_PREVIEW", "changed": changed,
                "execution_ready": False}
