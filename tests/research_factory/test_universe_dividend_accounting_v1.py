"""延迟现金红利的应收、支付、受益 lot 和恢复，不使用新账本算法。"""
import pandas as pd
import pytest

from chanlun_trader.engine.corporate_accounting_v1 import CorporateActionError
from chanlun_trader.engine.fill import Fill
from chanlun_trader.engine.individual_dividend_accounting_v1 import IndividualDividendAccountingV1
from chanlun_trader.engine.signal import Side
from chanlun_trader.research_factory.universe_dividend_accounting_v1 import (
    TAX_TIMING_POLICY, UniverseDividendAccountingV1,
)


def at(day, time="09:30"):
    return pd.Timestamp(f"{day} {time}", tz="Asia/Shanghai")


def delayed_event():
    return {"event_id": "DELAYED", "symbol": "300001.SZ", "event_type": "CASH_DIVIDEND",
        "record_date": 20240103, "effective_date": 20240104, "payment_date": 20240108,
        "source": "SYNTHETIC_ACTION_SOURCE", "source_published_at": "2024-01-02",
        "units": "CNY_PER_SHARE", "terms": {"cash_per_share": .5,
            "tax_rule": {"kind": "DEFERRED_INDIVIDUAL_2015_101",
                "source": "https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html"}}}


def fill(ledger, side, quantity, day, price=10.):
    result, message = ledger.apply_fill(Fill(f"{side.value}-{day}-{quantity}", "ORDER", "RULE",
        "300001.SZ", side, quantity, price, at(day)))
    assert message == "OK"
    return result


def test_delayed_payment_is_receivable_and_cannot_fund_earlier_orders():
    ledger = UniverseDividendAccountingV1(2000., [delayed_event()], "FIXTURE")
    fill(ledger, Side.BUY, 100, "2024-01-02")
    ledger.on_close(at("2024-01-03", "15:30"))
    ledger.on_open(at("2024-01-04"))
    assert ledger.cash == 1000.
    assert ledger.cash_receivable == 50.
    assert ledger.current_equity() == 2050.
    ledger.on_open(at("2024-01-05"))
    assert ledger.cash == 1000.
    ledger.on_open(at("2024-01-08"))
    assert ledger.cash == 1050.
    assert ledger.cash_receivable == 0.
    ledger.on_open(at("2024-01-08"))
    assert ledger.cash == 1050.


def test_sell_before_payment_keeps_entitlement_and_withholds_once():
    ledger = UniverseDividendAccountingV1(2000., [delayed_event()], "FIXTURE")
    fill(ledger, Side.BUY, 100, "2024-01-02")
    ledger.on_close(at("2024-01-03", "15:30"))
    ledger.on_open(at("2024-01-04"))
    fill(ledger, Side.SELL, 40, "2024-01-05", price=9.5)
    assert ledger.cash == 1376.
    assert ledger.cash_receivable == 50.
    assert ledger.dividend_tax_withheld == 4.
    assert ledger.entitlements["DELAYED"] == 100
    restored = UniverseDividendAccountingV1.restore(ledger.checkpoint(), [delayed_event()], "FIXTURE")
    restored.on_open(at("2024-01-08"))
    restored.on_open(at("2024-01-08"))
    fill(restored, Side.SELL, 60, "2024-01-09", price=9.5)
    assert restored.cash == 1990.
    assert restored.cash_receivable == 0.
    assert restored.dividend_tax_withheld == 10.
    assert sum(r["amount"] for r in restored.action_audit
               if r["phase"] == "DEFERRED_INDIVIDUAL_TAX") == 10.
    assert restored.check_invariants() == []
    taxes = [r for r in restored.action_audit if r["phase"] == "DEFERRED_INDIVIDUAL_TAX"]
    assert taxes[0]["dividend_paid"] is False
    assert taxes[1]["dividend_paid"] is True
    assert all(r["policy"] == TAX_TIMING_POLICY and r["broker_collection_time_verified"] is False
               for r in taxes)


def test_later_new_lots_do_not_receive_old_dividend_or_pay_old_tax():
    ledger = UniverseDividendAccountingV1(3000., [delayed_event()], "FIXTURE")
    fill(ledger, Side.BUY, 100, "2024-01-02")
    ledger.on_close(at("2024-01-03", "15:30"))
    ledger.on_open(at("2024-01-04"))
    fill(ledger, Side.BUY, 100, "2024-01-04", price=9.5)
    fill(ledger, Side.SELL, 150, "2024-01-05", price=9.5)
    assert ledger.entitlements["DELAYED"] == 100
    assert ledger.dividend_tax_withheld == 10.
    ledger.on_open(at("2024-01-08"))
    fill(ledger, Side.SELL, 50, "2024-01-09", price=9.5)
    assert ledger.dividend_tax_withheld == 10.
    assert ledger.cash == 2990.


def test_old_ledger_still_refuses_sale_before_payment():
    ledger = IndividualDividendAccountingV1(2000., [delayed_event()], "OLD")
    fill(ledger, Side.BUY, 100, "2024-01-02")
    ledger.on_close(at("2024-01-03", "15:30"))
    ledger.on_open(at("2024-01-04"))
    with pytest.raises(CorporateActionError, match="DIVIDEND_SALE_BEFORE_PAYMENT_UNSUPPORTED"):
        fill(ledger, Side.SELL, 100, "2024-01-05")


@pytest.mark.parametrize("kind", ["RIGHTS", "BONUS", "DELISTING"])
def test_unsupported_actions_are_not_guessed_or_dropped(kind):
    event = delayed_event()
    event["event_type"] = kind
    with pytest.raises(CorporateActionError, match="INDIVIDUAL_DIVIDEND_EVENT_UNSUPPORTED"):
        UniverseDividendAccountingV1(2000., [event], "FIXTURE")


def test_unknown_tax_or_payment_timing_rejected_before_account_runs():
    event = delayed_event()
    event["terms"]["tax_rule"]["source"] = "UNKNOWN"
    with pytest.raises(CorporateActionError):
        UniverseDividendAccountingV1(2000., [event], "FIXTURE")
    event = delayed_event()
    event["payment_date"] = 20240102
    with pytest.raises(CorporateActionError):
        UniverseDividendAccountingV1(2000., [event], "FIXTURE")
