"""显式送转条款的全范围账户；旧现金分红账本保持原版本。"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import math

import pandas as pd

from ..engine.corporate_accounting_v1 import CorporateActionAccountingV1, at_open
from ..engine.individual_dividend_accounting_v1 import dividend_tax_rate
from ..engine.signal import Side
from ..engine.time_types import ensure_aware
from .universe_dividend_accounting_v1 import UniverseDividendAccountingV1, TAX_TIMING_POLICY, TAX_TIMING_SOURCE


SHARE_TYPES = {"BONUS", "CAPITALIZATION"}
EXEMPT_KIND = "CAPITALIZATION_SHARE_PREMIUM_EXEMPT"
BONUS_TAX_KIND = "BONUS_DEFERRED_INDIVIDUAL_2015_101"
PRICE_POLICY = "RAW_IN_ORIGINAL_SHARE_UNITS_PLUS_ENTITLED_CASH_V2"


def _source(value):
    return isinstance(value, str) and bool(value.strip()) and value != "UNKNOWN"


class UniverseCorporateAccountingV2(UniverseDividendAccountingV1):
    """新增股在除权时计经济归属，到账前单列且上市前不可卖。

    税务明确的股本溢价转增可处理；应税送股只接受有依据的显式建模
    分摊口径，不声称该模型已核验中国结算的税款分配。
    """
    version = "UniverseCorporateAccountingV2"

    def __init__(self, initial_cash, events, dataset_id):
        CorporateActionAccountingV1.__init__(self, initial_cash, events, dataset_id)
        self.dividend_lots = {}
        self.dividend_tax_withheld = 0.0
        self.dividend_tax_timing = {
            "policy": TAX_TIMING_POLICY, "source": TAX_TIMING_SOURCE,
            "broker_collection_time_verified": False,
            "tax_basis": "SOLD_RECORD_DATE_BENEFICIARY_LOTS_FIFO",
        }
        self.share_price_factors = {}
        self.cash_price_adjustments = {}
        self.dividend_record_factors = {}
        self.share_tax_lots = {}
        self.share_tax_withheld = 0.0
        self.last_exit_dates = {}
        self.share_tax_timing = {
            "policy": TAX_TIMING_POLICY, "tax_allocation_verified": False,
            "allocation_policy": "CHILD_LOTS_MODELED",
        }
        share_days = set()
        for event in self.events:
            if event.get("event_type") == "CASH_DIVIDEND":
                self._cash_terms(event)
            elif event.get("event_type") in SHARE_TYPES:
                self._share_terms(event)
                key = (event["symbol"], event["effective_date"])
                if key in share_days:
                    self._reject("SAME_DAY_SHARE_ACTIONS_REQUIRE_COMBINED_TERMS")
                share_days.add(key)
            else:
                self._reject("UNIVERSE_CORPORATE_EVENT_UNSUPPORTED_V2")

    def _share_terms(self, event):
        try:
            terms = event["terms"]
            numerator, denominator = terms["ratio_numerator"], terms["ratio_denominator"]
            if (type(numerator) is not int or type(denominator) is not int
                    or min(numerator, denominator) <= 0
                    or event["units"] != "NEW_SHARES_PER_OLD_SHARE"
                    or not _source(event["source"])):
                raise ValueError()
            ratio = Fraction(numerator, denominator)
            if ratio <= 1:
                raise ValueError()
            record, effective = at_open(event["record_date"]), at_open(event["effective_date"])
            credit, tradable = at_open(event["share_credit_date"]), at_open(event["tradable_date"])
            if not record < effective <= credit <= tradable:
                self._reject("SHARE_CREDIT_TIMING_UNSUPPORTED_V2")
            for key in ("share_credit_date", "tradable_date"):
                evidence = event["date_evidence"][key]
                if evidence["kind"] not in {"SOURCE", "MODELED"} or not _source(evidence["source"]):
                    self._reject("SHARE_DATE_EVIDENCE_UNKNOWN")
            tax = terms["tax_rule"]
            if not _source(tax["source"]):
                self._reject("SHARE_TAX_SOURCE_UNKNOWN")
            if tax["kind"] == EXEMPT_KIND:
                if event["event_type"] != "CAPITALIZATION":
                    self._reject("SHARE_PREMIUM_EXEMPT_ACTION_CONFLICT")
            elif tax["kind"] == BONUS_TAX_KIND:
                basis = tax["taxable_cash_per_new_share"]
                if isinstance(basis, bool) or not isinstance(basis, (int, float)) or not math.isfinite(basis) or basis <= 0:
                    self._reject("BONUS_TAX_BASIS_UNKNOWN")
                if tax.get("allocation_policy") != "CHILD_LOTS_MODELED" or not _source(tax.get("allocation_source")):
                    self._reject("BONUS_TAX_ALLOCATION_SOURCE_UNKNOWN")
            else:
                self._reject("SHARE_TAX_RULE_UNKNOWN")
            return ratio, tradable
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            if self.blocked_reason:
                raise
            self._reject("SHARE_ACTION_TERMS_UNKNOWN_V2")

    def on_close(self, ts):
        CorporateActionAccountingV1.on_close(self, ts)
        day = int(pd.Timestamp(ts).strftime("%Y%m%d"))
        for event in self.events:
            key = event["event_id"]
            if event["event_type"] != "CASH_DIVIDEND" or event["record_date"] != day or key in self.dividend_lots:
                continue
            lots = {lot.lot_id: lot.remaining_quantity for lot in self.lots.values()
                    if lot.symbol == event["symbol"] and lot.remaining_quantity}
            if sum(lots.values()) != self.entitlements[key]:
                self._reject("DIVIDEND_LOT_ENTITLEMENT_MISMATCH")
            self.dividend_lots[key] = lots
            self.dividend_record_factors[key] = {lot_id: self.share_price_factors.get(lot_id, 1.) for lot_id in lots}

    def on_open(self, ts):
        self._check_active()
        ts = ensure_aware(ts)
        day = int(ts.strftime("%Y%m%d"))
        # 同日现金先固定到原股单位，再让新股继承比较口径；不依赖输入列表顺序。
        events = sorted(self.events, key=lambda event: (event["effective_date"],
                        event["event_type"] != "CASH_DIVIDEND", event["event_id"]))
        for event in events:
            key = event["event_id"]
            if event["effective_date"] <= day and key not in self.applied:
                if event["effective_date"] != day:
                    self._reject("MISSED_ACTION_EVENT_REQUIRES_CHECKPOINT_RECONCILIATION")
                held = self.total_quantity(event["symbol"])
                if key not in self.entitlements:
                    self._reject("DIVIDEND_ENTITLEMENT_HISTORY_MISSING" if event["event_type"] == "CASH_DIVIDEND"
                                 else "BONUS_ENTITLEMENT_CHANGED_OR_MISSING_V2")
                if event["event_type"] == "CASH_DIVIDEND":
                    entitled = self.entitlements[key]
                    if entitled:
                        net = self._cash_terms(event)
                        self.receivables[key] = entitled * net
                        self.action_income += entitled * net
                        for lot_id in self.dividend_lots[key]:
                            self.cash_price_adjustments.setdefault(lot_id, {})[key] = (
                                net * self.dividend_record_factors[key][lot_id])
                else:
                    self._split(event, ts)
                self.applied.add(key)
                self.action_audit.append({"event_id": key, "phase": "EFFECTIVE", "timestamp": str(ts), "held_quantity": held})
            if key in self.receivables and key not in self.payments and event["payment_date"] <= day:
                amount = self.receivables.pop(key)
                self.cash += amount
                self.payments.add(key)
                self.action_audit.append({"event_id": key, "phase": "PAYMENT", "amount": amount, "timestamp": str(ts)})
        for key, credit in list(self.pending_share_credits.items()):
            if credit["credit_date"] <= day:
                self.action_audit.append({"event_id": key, "phase": "SHARE_CREDIT", "lots": credit["lots"], "timestamp": str(ts)})
                del self.pending_share_credits[key]

    def _split(self, event, ts):
        ratio, tradable = self._share_terms(event)
        if any(self.lots[key].symbol == event["symbol"] for item in self.pending_share_credits.values() for key in item["lots"]):
            self._reject("OVERLAPPING_UNCREDITED_SHARE_ACTIONS_UNSUPPORTED")
        lots = [lot for lot in self.lots.values() if lot.symbol == event["symbol"] and lot.remaining_quantity]
        captured = self.entitlements.get(event["event_id"])
        if captured != {lot.lot_id: lot.remaining_quantity for lot in lots}:
            self._reject("BONUS_ENTITLEMENT_CHANGED_OR_MISSING_V2")
        changes = []
        for lot in lots:
            new = captured[lot.lot_id] * (ratio - 1)
            if new.denominator != 1:
                self._reject("FRACTIONAL_SHARES_UNSUPPORTED_V2")
            changes.append((lot, int(new)))
        credited = {}
        for lot, new in changes:
            basis = lot.cost / lot.quantity
            factor = self.share_price_factors.get(lot.lot_id, 1.) * float(ratio)
            child = replace(lot, lot_id=self._new_lot_id(), quantity=new, remaining_quantity=new,
                cost=basis * captured[lot.lot_id] * (1 - 1 / float(ratio)),
                sellable_from=max(lot.sellable_from, tradable),
                sellable_from_session=int(tradable.strftime("%Y%m%d")), sellable_from_session_index=None)
            lot.cost /= float(ratio)
            self.lots[child.lot_id] = child
            self.bonus_parent_lots[child.lot_id] = lot.lot_id
            self.share_price_factors[lot.lot_id] = self.share_price_factors[child.lot_id] = factor
            self.cash_price_adjustments[child.lot_id] = deepcopy(self.cash_price_adjustments.get(lot.lot_id, {}))
            credited[child.lot_id] = new
            if event["terms"]["tax_rule"]["kind"] == BONUS_TAX_KIND:
                self.share_tax_lots.setdefault(event["event_id"], {})[child.lot_id] = new
        if credited:
            self.pending_share_credits[event["event_id"]] = {"credit_date": event["share_credit_date"], "lots": credited}
        self._sync_costs(event["symbol"])
        if event["symbol"] in self.last_price:
            self.last_price[event["symbol"]] /= float(ratio)

    def apply_fill(self, fill, order_id="", lot_id=None):
        if fill.side != Side.SELL:
            return CorporateActionAccountingV1.apply_fill(self, fill, order_id, lot_id)
        before = {key: lot.remaining_quantity for key, lot in self.lots.items()
                  if lot.symbol == fill.symbol and lot.remaining_quantity}
        trade, message = CorporateActionAccountingV1.apply_fill(self, fill, order_id, lot_id)
        if message != "OK":
            return trade, message
        if not self.position_qty(fill.strategy_id, fill.symbol):
            self.last_exit_dates[fill.strategy_id + ':' + fill.symbol] = int(fill.fill_time.strftime('%Y%m%d'))
        for event in self.events:
            key = event["event_id"]
            share = event["event_type"] in SHARE_TYPES
            rights = self.share_tax_lots.get(key) if share else self.dividend_lots.get(key)
            if event["symbol"] != fill.symbol or rights is None:
                continue
            for lot_key, previous in before.items():
                taxed = min(previous - self.lots[lot_key].remaining_quantity, rights.get(lot_key, 0))
                if taxed <= 0:
                    continue
                rate = dividend_tax_rate(self.lots[lot_key].buy_time, fill.fill_time)
                tax = event["terms"]["tax_rule"]
                basis = tax["taxable_cash_per_new_share"] if share else event["terms"]["cash_per_share"]
                amount = round(taxed * float(basis) * rate, 4)
                rights[lot_key] -= taxed
                self.cash -= amount
                self.dividend_tax_withheld += amount
                row = {"event_id": key, "phase": "DEFERRED_SHARE_TAX" if share else "DEFERRED_INDIVIDUAL_TAX",
                       "lot_id": lot_key, "quantity": taxed, "rate": rate, "amount": amount,
                       "timestamp": str(fill.fill_time), "policy": TAX_TIMING_POLICY, "source": tax["source"]}
                if share:
                    self.share_tax_withheld += amount
                    row.update(allocation_policy=tax["allocation_policy"], allocation_source=tax["allocation_source"],
                               tax_allocation_verified=False)
                else:
                    row.update(payment_date=event["payment_date"], dividend_paid=key in self.payments,
                               broker_collection_time_verified=False)
                self.action_audit.append(row)
        return trade, message
