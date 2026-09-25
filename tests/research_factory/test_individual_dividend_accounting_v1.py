"""Personal A-share dividend payment and sale-time withholding."""
import pandas as pd
import pytest

from chanlun_trader.engine.fill import Fill
from chanlun_trader.engine.individual_dividend_accounting_v1 import (
    IndividualDividendAccountingV1, dividend_tax_rate,
)
from chanlun_trader.engine.signal import Side


def at(day: str, time: str = "09:30"):
    return pd.Timestamp(f"{day} {time}", tz="Asia/Shanghai")


def event():
    return {
        "event_id": "SPDB_2024_DIVIDEND", "symbol": "600000.SH",
        "event_type": "CASH_DIVIDEND", "record_date": 20240717,
        "effective_date": 20240718, "payment_date": 20240718,
        "terms": {"cash_per_share": 0.321,
                  "tax_rule": {"kind": "DEFERRED_INDIVIDUAL_2015_101",
                               "source": "https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html"}},
        "units": "CNY_PER_SHARE", "source": "ISSUER_2024_07_11",
        "source_published_at": "2024-07-11",
    }


def fill(ledger, side, quantity, day, price=10.0):
    receipt, message = ledger.apply_fill(Fill(
        f"{side.value}-{day}-{quantity}", "order", "FIXED", "600000.SH",
        side, quantity, price, at(day)))
    assert message == "OK"
    return receipt


def test_gross_dividend_arrives_before_tax_and_tax_is_withheld_on_sale():
    ledger = IndividualDividendAccountingV1(2000, [event()], "S1")
    fill(ledger, Side.BUY, 100, "2024-07-16")
    ledger.on_close(at("2024-07-17", "15:30"))
    ledger.on_open(at("2024-07-18"))
    assert ledger.cash == pytest.approx(1032.1)
    assert ledger.action_income == pytest.approx(32.1)
    assert ledger.dividend_tax_withheld == 0
    fill(ledger, Side.SELL, 100, "2024-07-19")
    assert ledger.dividend_tax_withheld == pytest.approx(6.42)
    assert ledger.cash == pytest.approx(2025.68)
    assert ledger.check_invariants() == []


def test_only_record_date_lots_are_taxed_and_restore_does_not_duplicate_tax():
    ledger = IndividualDividendAccountingV1(3000, [event()], "S1")
    fill(ledger, Side.BUY, 100, "2024-07-16")
    ledger.on_close(at("2024-07-17", "15:30"))
    ledger.on_open(at("2024-07-18"))
    fill(ledger, Side.BUY, 100, "2024-07-18")
    fill(ledger, Side.SELL, 40, "2024-07-19")
    assert ledger.dividend_tax_withheld == pytest.approx(2.568)
    restored = IndividualDividendAccountingV1.restore(ledger.checkpoint(), [event()], "S1")
    fill(restored, Side.SELL, 160, "2024-07-22")
    assert restored.dividend_tax_withheld == pytest.approx(6.42)
    assert restored.dividend_lots[event()["event_id"]]["lot-000001"] == 0
    assert restored.check_invariants() == []


def test_restore_before_payment_pays_once_then_taxes_once():
    ledger = IndividualDividendAccountingV1(2000, [event()], "S1")
    fill(ledger, Side.BUY, 100, "2024-07-16")
    ledger.on_close(at("2024-07-17", "15:30"))
    restored = IndividualDividendAccountingV1.restore(ledger.checkpoint(), [event()], "S1")
    restored.on_open(at("2024-07-18"))
    restored.on_open(at("2024-07-18"))
    assert restored.cash == pytest.approx(1032.1)
    assert restored.action_income == pytest.approx(32.1)
    fill(restored, Side.SELL, 100, "2024-07-19")
    assert restored.dividend_tax_withheld == pytest.approx(6.42)
    assert restored.cash == pytest.approx(2025.68)


@pytest.mark.parametrize("sale,rate", [
    ("2024-02-15", 0.20), ("2024-02-16", 0.10),
    ("2025-01-15", 0.10), ("2025-01-16", 0.0),
])
def test_holding_period_uses_calendar_month_and_year_boundaries(sale, rate):
    assert dividend_tax_rate(at("2024-01-15"), at(sale)) == rate
