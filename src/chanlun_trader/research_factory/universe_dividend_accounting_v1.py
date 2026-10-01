"""全范围现金分红账本版本：保留真实到账日期与获息 lot 的补税归属。

2015-101 第二条规定转让时计算扣收，2012-85 第三/八条规定 FIFO
和自然月/年。模型在成交时扣款；这不是已核对的券商交收时刻。
来源：https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html
https://www.chinatax.gov.cn/chinatax/n810341/n810765/n812151/201211/c1082457/content.html
"""
from __future__ import annotations

from ..engine.corporate_accounting_v1 import CorporateActionAccountingV1
from ..engine.individual_dividend_accounting_v1 import (
    IndividualDividendAccountingV1, dividend_tax_rate,
)
from ..engine.signal import Side


TAX_TIMING_POLICY = "SALE_FILL_MODELED_2015_101"
TAX_TIMING_SOURCE = "https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html"


class UniverseDividendAccountingV1(IndividualDividendAccountingV1):
    """仅对新全范围版本解除旧的到账前售出限制，其他账本语义复用。

    登记日捕获的数量不会因随后卖出被删除；除息形成含税应收，支付日
    到账。补税按实际售出的受益 lot 计算一次，即使红利仍未支付。
    """

    version = "UniverseDividendAccountingV1"

    def __init__(self, initial_cash, events, dataset_id):
        super().__init__(initial_cash, events, dataset_id)
        # 提前验证条款，不等持仓碰到未知事件时才发现数据不合格。
        for event in self.events:
            self._cash_terms(event)
        self.dividend_tax_timing = {
            "policy": TAX_TIMING_POLICY, "source": TAX_TIMING_SOURCE,
            "broker_collection_time_verified": False,
            "tax_basis": "SOLD_RECORD_DATE_BENEFICIARY_LOTS_FIFO",
        }

    def apply_fill(self, fill, order_id="", lot_id=None):
        if fill.side != Side.SELL:
            return super().apply_fill(fill, order_id, lot_id)
        before = {key: lot.remaining_quantity for key, lot in self.lots.items()
                  if lot.symbol == fill.symbol and lot.remaining_quantity}
        # 复用既有成交、FIFO 与成本核算；不调用旧类的到账前出售阻断。
        trade, message = CorporateActionAccountingV1.apply_fill(self, fill, order_id, lot_id)
        if message != "OK":
            return trade, message
        for event in self.events:
            entitled = self.dividend_lots.get(event["event_id"])
            if event["symbol"] != fill.symbol or entitled is None:
                continue
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
                    "timestamp": str(fill.fill_time), "policy": TAX_TIMING_POLICY,
                    "source": event["terms"]["tax_rule"]["source"],
                    "payment_date": event["payment_date"],
                    "dividend_paid": event["event_id"] in self.payments,
                    "broker_collection_time_verified": False})
        return trade, message
