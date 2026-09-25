"""个人 A 股现金红利：发放时到账，卖出获息股份时按持有期补税。"""
from __future__ import annotations

import pandas as pd

from .corporate_accounting_v1 import CorporateActionAccountingV1, CorporateActionError
from .signal import Side


TAX_KIND = "DEFERRED_INDIVIDUAL_2015_101"


def dividend_tax_rate(buy_time, sell_time) -> float:
    """财税〔2015〕101 号税率，持有期按财税〔2012〕85 号自然月/年计算。"""
    bought = pd.Timestamp(buy_time).normalize()
    sold = pd.Timestamp(sell_time).normalize()
    if sold <= bought:
        raise CorporateActionError("DIVIDEND_TAX_HOLDING_PERIOD_INVALID")
    if sold <= bought + pd.DateOffset(months=1):
        return 0.20
    if sold <= bought + pd.DateOffset(years=1):
        return 0.10
    return 0.0


class IndividualDividendAccountingV1(CorporateActionAccountingV1):
    """仅支持有来源条款的现金分红；其他公司行动继续 fail-closed。"""

    version = "IndividualDividendAccountingV1"

    def __init__(self, initial_cash, events, dataset_id):
        if any(event.get("event_type") != "CASH_DIVIDEND"
               or event.get("terms", {}).get("tax_rule", {}).get("kind") != TAX_KIND
               for event in events):
            raise CorporateActionError("INDIVIDUAL_DIVIDEND_EVENT_UNSUPPORTED")
        super().__init__(initial_cash, events, dataset_id)
        self.dividend_lots = {}
        self.dividend_tax_withheld = 0.0

    def _cash_terms(self, event):
        tax_rule = event.get("terms", {}).get("tax_rule", {})
        if tax_rule.get("kind") != TAX_KIND or not tax_rule.get("source"):
            self._reject("INDIVIDUAL_DIVIDEND_TAX_SOURCE_UNKNOWN")
        # 发放日先按含税金额入账；税款在出售享有该次红利的股份时扣收。
        validated = {**event, "terms": {**event["terms"],
                     "tax_rule": {"kind": "EXPLICIT_NET", "source": tax_rule["source"]}}}
        return super()._cash_terms(validated)

    def on_close(self, ts):
        super().on_close(ts)
        day = int(pd.Timestamp(ts).strftime("%Y%m%d"))
        for event in self.events:
            event_id = event["event_id"]
            if event["record_date"] != day or event_id in self.dividend_lots:
                continue
            lots = {lot.lot_id: lot.remaining_quantity for lot in self.lots.values()
                    if lot.symbol == event["symbol"] and lot.remaining_quantity}
            if sum(lots.values()) != self.entitlements[event_id]:
                self._reject("DIVIDEND_LOT_ENTITLEMENT_MISMATCH")
            self.dividend_lots[event_id] = lots

    def apply_fill(self, fill, order_id="", lot_id=None):
        if fill.side != Side.SELL:
            return super().apply_fill(fill, order_id, lot_id)
        sale_day = int(fill.fill_time.strftime("%Y%m%d"))
        before = {key: lot.remaining_quantity for key, lot in self.lots.items()
                  if lot.symbol == fill.symbol and lot.remaining_quantity}
        for event in self.events:
            if event["symbol"] != fill.symbol or event["event_id"] not in self.dividend_lots:
                continue
            entitled = self.dividend_lots[event["event_id"]]
            if (event["record_date"] < sale_day < event["payment_date"]
                    and any(before.get(key, 0) and quantity for key, quantity in entitled.items())):
                self._reject("DIVIDEND_SALE_BEFORE_PAYMENT_UNSUPPORTED")
        trade, message = super().apply_fill(fill, order_id, lot_id)
        if message != "OK":
            return trade, message
        for event in self.events:
            if event["symbol"] != fill.symbol or event["event_id"] not in self.dividend_lots:
                continue
            entitled = self.dividend_lots[event["event_id"]]
            for key, previous in before.items():
                sold = previous - self.lots[key].remaining_quantity
                taxed = min(sold, entitled.get(key, 0))
                if taxed <= 0:
                    continue
                rate = dividend_tax_rate(self.lots[key].buy_time, fill.fill_time)
                amount = round(taxed * float(event["terms"]["cash_per_share"]) * rate, 4)
                entitled[key] -= taxed
                self.cash -= amount
                self.dividend_tax_withheld += amount
                self.action_audit.append({"event_id": event["event_id"],
                    "phase": "DEFERRED_INDIVIDUAL_TAX", "lot_id": key,
                    "quantity": taxed, "rate": rate, "amount": amount,
                    "timestamp": str(fill.fill_time)})
        return trade, message
