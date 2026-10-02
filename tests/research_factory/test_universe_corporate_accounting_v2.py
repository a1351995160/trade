"""送转登记、成本、可卖日与显式税分配；全部使用合成来源。"""
import pandas as pd
import pytest

from chanlun_trader.engine.corporate_accounting_v1 import CorporateActionError
from chanlun_trader.engine.fill import Fill
from chanlun_trader.engine.signal import Side
from chanlun_trader.research_factory.universe_corporate_accounting_v2 import UniverseCorporateAccountingV2
from chanlun_trader.research_factory.universe_account_backend_v1 import UniverseBacktestEngineV1, UniverseBrokerV1
from chanlun_trader.research_factory.universe_dividend_accounting_v1 import UniverseDividendAccountingV1
from chanlun_trader.research_factory.universe_rule_exit_v1 import UniverseRuleExitV1
from chanlun_trader.research_factory.forward_paper_engine_v1 import ledger_rule_account
from chanlun_trader.engine.engine import EngineConfig
from chanlun_trader.engine.asof import MarketDataStore
from chanlun_trader.engine.daily_exit_v2 import DailyExitRuleSetV2
from chanlun_trader.engine.order import Order, TimeInForce
from chanlun_trader.engine.time_types import EventKind


def at(day, time="09:30"):
    return pd.Timestamp(f"2024-01-{day:02d} {time}", tz="Asia/Shanghai")


def share_event(taxable=False):
    tax = {"kind": "CAPITALIZATION_SHARE_PREMIUM_EXEMPT", "source": "SYNTHETIC_SHARE_PREMIUM_DOCUMENT"}
    if taxable:
        tax = {"kind": "BONUS_DEFERRED_INDIVIDUAL_2015_101", "source": "SYNTHETIC_TAX_DOCUMENT",
               "taxable_cash_per_new_share": 1., "allocation_policy": "CHILD_LOTS_MODELED",
               "allocation_source": "SYNTHETIC_EXPLICIT_ALLOCATION_MODEL"}
    return {"event_id": "SHARE", "symbol": "300001.SZ", "event_type": "BONUS" if taxable else "CAPITALIZATION",
            "record_date": 20240103, "effective_date": 20240104,
            "share_credit_date": 20240108, "tradable_date": 20240109,
            "source": "SYNTHETIC_IMPLEMENTATION_DOCUMENT", "source_published_at": "2024-01-02",
            "units": "NEW_SHARES_PER_OLD_SHARE", "terms": {"ratio_numerator": 13, "ratio_denominator": 10, "tax_rule": tax},
            "date_evidence": {name: {"kind": "SOURCE", "source": "SYNTHETIC_DATE_DOCUMENT"}
                              for name in ("share_credit_date", "tradable_date")}}


def cash_event():
    return {"event_id": "CASH", "symbol": "300001.SZ", "event_type": "CASH_DIVIDEND",
            "record_date": 20240103, "effective_date": 20240104, "payment_date": 20240108,
            "source": "SYNTHETIC_IMPLEMENTATION_DOCUMENT", "source_published_at": "2024-01-02",
            "units": "CNY_PER_SHARE", "terms": {"cash_per_share": .8,
                "tax_rule": {"kind": "DEFERRED_INDIVIDUAL_2015_101", "source": "SYNTHETIC_TAX_DOCUMENT"}}}


def fill(ledger, side, quantity, day, price=10., lot_id=None):
    trade, message = ledger.apply_fill(Fill(f"{side.value}-{day}-{quantity}", "ORDER", "RULE",
        "300001.SZ", side, quantity, price, at(day)), lot_id=lot_id)
    assert message == "OK"
    if side == Side.BUY:
        ledger.lots[trade.lot_id].entry_price = price
    return trade


def prepared(events, quantity=100, initial_cash=1000.):
    ledger = UniverseCorporateAccountingV2(initial_cash, events, "SYNTHETIC_DATASET")
    parent = fill(ledger, Side.BUY, quantity, 2).lot_id
    ledger.on_close(at(3, "15:30"))
    return ledger, parent


def test_pure_capitalization_zero_cash_credit_and_sellable_are_distinct():
    event = share_event()
    ledger, parent = prepared([event])
    assert ledger.cash == 0.
    ledger.on_open(at(4))
    child = next(key for key in ledger.lots if key != parent)
    assert ledger.entitlements["SHARE"] == {parent: 100}
    assert ledger.lots[child].quantity == ledger.lots[child].remaining_quantity == 30
    assert ledger.lots[child].buy_time == ledger.lots[parent].buy_time
    assert ledger.sellable_lot_quantity(parent, at(4)) == 100
    assert ledger.sellable_lot_quantity(child, at(8)) == 0
    assert ledger.lots[parent].cost + ledger.lots[child].cost == pytest.approx(1000.)
    ledger.mark_to_market("300001.SZ", 10 / 1.3, at(4))
    assert ledger.current_equity() == pytest.approx(1000.)
    checkpoint = ledger.checkpoint()
    resumed = UniverseCorporateAccountingV2.restore(checkpoint, [event], "SYNTHETIC_DATASET")
    for account in (ledger, resumed):
        account.on_open(at(8))
        assert not account.pending_share_credits
        assert account.sellable_lot_quantity(child, at(9)) == 30
        fill(account, Side.SELL, 30, 9, price=10 / 1.3, lot_id=child)
        account.on_open(at(9))
        assert account.dividend_tax_withheld == 0.
        assert account.check_invariants() == []
    assert ledger.checkpoint() == resumed.checkpoint()


def test_cash_plus_shares_retains_cash_rights_after_old_shares_are_sold():
    events = [share_event(), cash_event()]
    ledger, parent = prepared(events, initial_cash=2000.)
    ledger.on_open(at(4))
    child = next(key for key in ledger.lots if key != parent)
    assert ledger.cash_receivable == 80.
    assert ledger.cash_price_adjustments[parent] == ledger.cash_price_adjustments[child] == {"CASH": .8}
    fill(ledger, Side.SELL, 100, 5, price=9.2 / 1.3, lot_id=parent)
    assert ledger.dividend_tax_withheld == 16.
    assert ledger.entitlements["CASH"] == 100
    assert ledger.lots[child].remaining_quantity == 30
    assert ledger.last_exit_dates == {}
    ledger.on_open(at(8))
    fill(ledger, Side.SELL, 30, 9, price=9.2 / 1.3, lot_id=child)
    assert ledger.dividend_tax_withheld == 16.
    assert ledger.cash == pytest.approx(1984.)
    assert ledger.last_exit_dates == {'RULE:300001.SZ': 20240109}
    account = ledger_rule_account(ledger, 'RULE', '300001.SZ',
        [20240102, 20240103, 20240104, 20240105, 20240108, 20240109], 20240109)
    assert account['last_exit_session_index'] == 5


def test_explicit_modeled_bonus_tax_is_consumed_once_on_sold_new_shares():
    event = share_event(taxable=True)
    ledger, parent = prepared([event], initial_cash=2000.)
    ledger.on_open(at(4))
    child = next(key for key in ledger.lots if key != parent)
    fill(ledger, Side.SELL, 100, 5, price=10 / 1.3, lot_id=parent)
    assert ledger.share_tax_withheld == 0.
    ledger.on_open(at(8))
    fill(ledger, Side.SELL, 10, 9, price=10 / 1.3, lot_id=child)
    restored = UniverseCorporateAccountingV2.restore(ledger.checkpoint(), [event], "SYNTHETIC_DATASET")
    fill(restored, Side.SELL, 20, 10, price=10 / 1.3, lot_id=child)
    assert restored.share_tax_withheld == restored.dividend_tax_withheld == 6.
    assert restored.share_tax_lots["SHARE"][child] == 0
    taxes = [row for row in restored.action_audit if row["phase"] == "DEFERRED_SHARE_TAX"]
    assert [row["quantity"] for row in taxes] == [10, 20]
    assert all(row["tax_allocation_verified"] is False for row in taxes)


@pytest.mark.parametrize("change", [
    lambda event: event["terms"].pop("tax_rule"),
    lambda event: event["terms"]["tax_rule"].update(kind="UNKNOWN"),
    lambda event: event.pop("date_evidence"),
    lambda event: event.update(share_credit_date=20240102),
    lambda event: event["terms"]["tax_rule"].pop("allocation_source"),
])
def test_unknown_share_terms_are_rejected_before_trading(change):
    event = share_event(taxable=True)
    change(event)
    with pytest.raises(CorporateActionError):
        UniverseCorporateAccountingV2(1000., [event], "SYNTHETIC_DATASET")


def test_fractional_credit_and_changed_record_entitlement_do_not_mutate_basis():
    event = share_event()
    event["terms"].update(ratio_numerator=4, ratio_denominator=3)
    ledger, parent = prepared([event])
    with pytest.raises(CorporateActionError, match="FRACTIONAL_SHARES_UNSUPPORTED_V2"):
        ledger.on_open(at(4))
    assert ledger.lots[parent].cost == 1000. and len(ledger.lots) == 1
    changed = share_event()
    changed['effective_date'] = 20240105
    ledger, parent = prepared([changed])
    fill(ledger, Side.SELL, 100, 4, lot_id=parent)
    with pytest.raises(CorporateActionError, match="BONUS_ENTITLEMENT_CHANGED_OR_MISSING_V2"):
        ledger.on_open(at(5))
    assert len(ledger.lots) == 1


def test_universe_clock_preserves_credit_tradability_instead_of_buy_t1():
    ledger, parent = prepared([share_event()])
    ledger.on_open(at(4))
    child = next(key for key in ledger.lots if key != parent)
    calendar = [20240102, 20240103, 20240104, 20240105, 20240108, 20240109]
    engine = UniverseBacktestEngineV1(MarketDataStore(), calendar,
        config=EngineConfig(initial_cash=1000., persist_run_manifest=False), source_identity=("SYNTHETIC", False))
    engine.ledger = ledger
    engine._sync_lot_contract_fields()
    assert ledger.lots[child].sellable_from == at(9)
    assert ledger.lots[child].sellable_from_session_index == 5
    assert ledger.lots[child].entry_session_index == ledger.lots[parent].entry_session_index == 0


def test_share_and_cash_exit_comparison_does_not_trigger_false_stop():
    ledger, parent = prepared([share_event(), cash_event()], initial_cash=2000.)
    ledger.on_open(at(4))
    store = MarketDataStore()
    raw = 9.2 / 1.3
    bars = pd.DataFrame([{"date": 20240104, "open": raw, "high": raw, "low": raw,
                          "close": raw, "volume": 10000., "amount": raw * 10000., "prev_close": raw}]).set_index("date")
    store.add_daily_raw("300001.SZ", bars)
    for lot in ledger.lots.values():
        lot.entry_session_index = 0
    adapter = UniverseRuleExitV1("RULE", DailyExitRuleSetV2(stop_loss_pct=.05))
    assert adapter.evaluate(ledger, store, [20240102, 20240103, 20240104], 20240104) == []
    assert all(row["comparison_close"] == pytest.approx(10.) and row["share_price_factor"] == 1.3
               for row in adapter.trace)


def test_old_cash_ledger_rejects_shares_and_cash_economics_are_unchanged():
    with pytest.raises(CorporateActionError, match="INDIVIDUAL_DIVIDEND_EVENT_UNSUPPORTED"):
        UniverseDividendAccountingV1(1000., [share_event()], "SYNTHETIC_DATASET")
    ledgers = [UniverseDividendAccountingV1(2000., [cash_event()], "SYNTHETIC_DATASET"),
               UniverseCorporateAccountingV2(2000., [cash_event()], "SYNTHETIC_DATASET")]
    for ledger in ledgers:
        fill(ledger, Side.BUY, 100, 2)
        ledger.on_close(at(3, "15:30"))
        ledger.on_open(at(4))
        fill(ledger, Side.SELL, 100, 5, price=9.2)
        ledger.on_open(at(8))
    assert ledgers[0].cash == ledgers[1].cash == 1984.
    for name in ("entitlements", "dividend_lots", "receivables", "dividend_tax_withheld", "action_audit"):
        assert getattr(ledgers[0], name) == getattr(ledgers[1], name)


def test_new_shares_inherit_trailing_peak_from_before_ex_date():
    ledger, parent = prepared([share_event()], initial_cash=2000.)
    ledger.lots[parent].entry_session_index = 0
    adapter = UniverseRuleExitV1('RULE', DailyExitRuleSetV2(trailing_activate_pct=.05, trailing_pct=.03))
    store = MarketDataStore()
    bars = pd.DataFrame([{'date': day, 'open': price, 'high': price, 'low': price,
                         'close': price, 'volume': 10000., 'amount': price * 10000., 'prev_close': price}
                        for day, price in [(20240103, 12.), (20240104, 11.5 / 1.3)]]).set_index('date')
    store.add_daily_raw('300001.SZ', bars)
    calendar = [20240102, 20240103, 20240104]
    assert adapter.evaluate(ledger, store, calendar, 20240103) == []
    ledger.on_open(at(4))
    exits = adapter.evaluate(ledger, store, calendar, 20240104)
    assert len(exits) == 2
    assert all(exit.reason_code == 'EXIT_TRAILING_STOP' for exit in exits)
    assert all(state.peak_close == 12. for state in adapter.evaluator._v1.trailing.values())


def test_parent_and_child_orders_share_session_capacity_even_after_broker_restore():
    event = share_event()
    event.update(share_credit_date=20240104, tradable_date=20240104)
    ledger, parent = prepared([event], quantity=1000, initial_cash=10000.)
    ledger.on_open(at(4))
    child = next(key for key in ledger.lots if key != parent)
    engine = UniverseBacktestEngineV1(MarketDataStore(), [20240102, 20240103, 20240104, 20240105],
        config=EngineConfig(initial_cash=10000., persist_run_manifest=False), source_identity=('SYNTHETIC', False))
    engine._build()
    bars = pd.DataFrame([{'date': day, 'open': price, 'high': price, 'low': price, 'close': price,
                         'volume': volume, 'amount': price * volume, 'prev_close': price}
                        for day, price, volume in [(20240103, 10., 1500.), (20240104, 10 / 1.3, 2000.),
                                                   (20240105, 10 / 1.3, 9000.)]]).set_index('date')
    engine.store.add_daily_raw('300001.SZ', bars)

    class PermittedPrice:
        def can_sell_at_open(self, symbol, ts, bar):
            return True, 'OK'

        def limit_prices(self, symbol, ts, reference):
            return None, None

    def broker_for(account):
        return UniverseBrokerV1(engine.store, engine.clock, account, engine.order_manager,
                                price_limit_model=PermittedPrice())

    def order_for(lot_id, quantity, day):
        order = Order(order_id='', strategy_id='RULE', intent_id='SYNTHETIC', signal_id='SYNTHETIC',
                      symbol='300001.SZ', side=Side.SELL, quantity=quantity, created_at=at(day),
                      eligible_at=at(day), lot_id=lot_id, time_in_force=TimeInForce.DAY)
        engine.order_manager.create_order(order, at(day))
        engine.order_manager.submit(order, at(day))
        return order

    broker = broker_for(ledger)
    parent_order = order_for(parent, 1000, 4)
    child_order = order_for(child, 300, 4)
    broker.process_orders(EventKind.SESSION_OPEN, at(4))
    assert parent_order.filled_quantity == 150
    assert child_order.filled_quantity == 0 and child_order.reason_code == 'PARTICIPATION_LIMIT'
    assert sum(trade.quantity for trade in ledger.trades if trade.side == Side.SELL) == 150
    engine.order_manager.cancel(parent_order, at(4), 'SYNTHETIC_RESTORE')
    restored = UniverseCorporateAccountingV2.restore(ledger.checkpoint(), [event], 'SYNTHETIC_DATASET')
    broker = broker_for(restored)
    same_day = order_for(child, 300, 4)
    broker.process_orders(EventKind.SESSION_OPEN, at(4))
    assert same_day.filled_quantity == 0 and same_day.reason_code == 'PARTICIPATION_LIMIT'
    next_day = order_for(child, 300, 5)
    broker.process_orders(EventKind.SESSION_OPEN, at(5))
    assert next_day.filled_quantity == 200
