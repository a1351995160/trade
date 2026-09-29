"""V3 分笔保护退出：收盘确认、次日 RAW 成交；状态由快照重放恢复。"""
from copy import deepcopy
from dataclasses import asdict
import math

from ..engine.daily_exit_v2 import DailyExitEvaluatorV2

EXIT_PRICE_POLICY = "RAW_PLUS_PAID_GROSS_CASH_V1"


def validate_exit_actions(events):
    for event in events:
        if (event.get("event_type") != "CASH_DIVIDEND"
                or event.get("payment_date") != event.get("effective_date")):
            raise ValueError("RULE_EXIT_CORPORATE_ACTION_UNSUPPORTED")


class RuleExitAdapterV3:
    def __init__(self, strategy_id, rules):
        self.strategy_id = strategy_id
        self.evaluator = DailyExitEvaluatorV2(strategy_id, "RULE_EXIT_V3", rules)
        self.trace = []

    def evaluate(self, ledger, store, calendar, day):
        validate_exit_actions(getattr(ledger, "events", []))
        indices = {int(value): index for index, value in enumerate(calendar)}
        result = []
        for lot in sorted(ledger.lots.values(), key=lambda row: row.lot_id):
            if lot.strategy_id != self.strategy_id or not lot.remaining_quantity:
                continue
            raw = store.get_daily_bar(lot.symbol, day, price_mode="raw")
            if raw is None or not math.isfinite(float(raw["close"])) or float(raw["close"]) <= 0:
                raise ValueError("RULE_EXIT_COMPLETED_RAW_PRICE_REQUIRED")
            if not math.isfinite(lot.entry_price) or lot.entry_price <= 0 or lot.entry_session_index is None:
                raise ValueError("RULE_EXIT_REAL_LOT_ANCHOR_REQUIRED")
            paid = 0.0
            event_ids = []
            for event in getattr(ledger, "events", []):
                if event["symbol"] != lot.symbol or event["effective_date"] > day:
                    continue
                entitled = getattr(ledger, "dividend_lots", {}).get(event["event_id"], {}).get(lot.lot_id, 0)
                if entitled:
                    if event["event_id"] not in ledger.payments:
                        raise ValueError("RULE_EXIT_DIVIDEND_PAYMENT_REQUIRED")
                    paid += float(event["terms"]["cash_per_share"])
                    event_ids.append(event["event_id"])
            adjusted = {**raw, "close": float(raw["close"]) + paid}
            class PriceView:
                def get_daily_bar(self, symbol, session, price_mode="raw"):
                    if symbol != lot.symbol or session != day or price_mode != "raw":
                        raise ValueError("RULE_EXIT_PRICE_VIEW_SCOPE")
                    return adjusted
            before = len(self.evaluator.evaluations)
            decisions = self.evaluator.evaluate([lot], day, indices[day], PriceView(), session_index_of=indices)
            self.trace.append({"date": day, "lot_id": lot.lot_id, "symbol": lot.symbol,
                "entry_price": lot.entry_price, "raw_close": float(raw["close"]),
                "comparison_close": adjusted["close"], "paid_gross_cash_per_share": paid,
                "event_ids": event_ids, "price_policy": EXIT_PRICE_POLICY,
                "evaluations": deepcopy(self.evaluator.evaluations[before:]),
                "reasons": [item.reason_code for item in decisions]})
            result.extend(decisions)
        return result

    def state(self):
        return {"price_policy": EXIT_PRICE_POLICY,
            "price_policy_note": "RAW收盘价加已到账含税红利作总收益触发；费用与补税仅在账户盈亏扣除。",
            "trailing": {key: asdict(value) for key, value in sorted(self.evaluator._v1.trailing.items())},
            "evaluations": deepcopy(self.trace)}
