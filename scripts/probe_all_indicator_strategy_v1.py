"""Run the fixed 51-indicator integration pilot on bounded historical data."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
EXTERNAL_DATA_ROOT = Path(
    "E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/baostock-account-v1"
).resolve()
REPORT_PATH = ROOT / "reports/all_indicator_fixed_strategy_pilot_20260925/PROBE.json"

from chanlun_trader.engine.custom_indicators_v2 import register_custom_indicators  # noqa: E402
from chanlun_trader.engine.indicator_registry_v2 import default_registry  # noqa: E402
from chanlun_trader.engine.indicators_v2 import IndicatorFrameV2  # noqa: E402
from chanlun_trader.engine.asof import MarketDataStore  # noqa: E402
from chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig  # noqa: E402
from chanlun_trader.engine.individual_dividend_accounting_v1 import IndividualDividendAccountingV1  # noqa: E402
from chanlun_trader.engine.official_valuation import official_equity_curve  # noqa: E402
from chanlun_trader.engine.signal import Signal, Side  # noqa: E402
from chanlun_trader.research.io_safety import GuardedResearchReader  # noqa: E402
from chanlun_trader.research_factory.degraded_execution_v2 import (  # noqa: E402
    DegradedAccountEngineV2, DegradedPriceLimitV1, DegradedStateMasterV1,
)


STRATEGY_ID = "ALL_51_EQUAL_VOTE_BAOSTOCK_TURN_V1"
TURNOVER_VERSION = "TURNOVER_RATE_BAOSTOCK_TURN_V1"
START_DATE = 20231009
END_DATE = 20240731
ACCOUNT_START_DATE = 20240201
ACCOUNT_END_DATE = 20240531
SYMBOLS = ("000001.SZ", "600000.SH")
INDICATOR_COUNT = 51
BUY_VOTES = 26
CATALOG_SHA256 = "a74f3936989489ad8080d0f62fa9e1acd8fe09426c25fc72be3cbdda20d75f6f"


def known_bool(value, expected: bool) -> bool:
    return isinstance(value, (bool, np.bool_)) and bool(value) is expected


class StateGatedPilotEngine(BacktestEngineV2):
    """Apply the existing historical execution-state gate to the fixed pilot."""

    def _build(self):
        super()._build()
        self.broker.price_limit = DegradedPriceLimitV1(self.security_master)


class IndividualDividendPilotEngine(DegradedAccountEngineV2):
    """Use the existing event clock with explicit personal-dividend accounting."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.hazards = {}
        self.unsupported_lots = {}
        self.entry_rejections = []
        self.sellability_projections = []

    def _build(self):
        super()._build()
        self.ledger = IndividualDividendAccountingV1(
            self.config.initial_cash, self.action_dataset.events, self.action_dataset.dataset_id)
        self.broker.ledger = self.ledger
        self.risk.ledger = self.ledger
        self.broker.price_limit = DegradedPriceLimitV1(self.security_master)


def vendor_turnover_rate(data, *, vendor_turn: pd.Series) -> IndicatorFrameV2:
    """BaoStock daily ``turn`` is already a percentage; do not infer shares."""
    if not vendor_turn.index.equals(data.index):
        raise ValueError("INDEX_MISMATCH:TURNOVER_RATE:vendor_turn")
    values = pd.to_numeric(vendor_turn, errors="coerce")
    ready = np.isfinite(values.to_numpy(dtype=float)) & (values.to_numpy(dtype=float) >= 0)
    return IndicatorFrameV2(
        indicator_id="TURNOVER_RATE", version=TURNOVER_VERSION, index=data.index,
        columns={"turnover_rate": values},
        ready=pd.Series(ready, index=data.index),
        segment=pd.Series(np.cumsum(~ready), index=data.index), warmup_bars=1,
    )


def pilot_registry():
    registry = default_registry()
    register_custom_indicators(registry)
    original = registry.get("TURNOVER_RATE", "TURNOVER_RATE_V1")
    registry.register(replace(
        original, version=TURNOVER_VERSION, params={}, inputs=(),
        requires_extra_data=("vendor_turn",),
        formula_note="BaoStock 日线 turn 原值，单位百分比；仅用于本固定策略试验",
        evidence={"IMPLEMENTED": True, "REGISTERED": True},
        implementation_path="scripts/probe_all_indicator_strategy_v1.py",
    ), vendor_turnover_rate)
    return registry


def approved_existing_file(path: Path, *, fixture_root: Path | None = None) -> Path:
    """Keep this fixed pilot inside its known source roots, including tests."""
    resolved = path.resolve(strict=True)
    roots = (ROOT, EXTERNAL_DATA_ROOT)
    if fixture_root is not None:
        roots = (*roots, fixture_root.resolve(strict=True))
    if not resolved.is_file() or not any(resolved.is_relative_to(root) for root in roots):
        raise ValueError(f"PILOT_INPUT_PATH_NOT_ALLOWED:{path}")
    return resolved


def sha256_file(path: Path, *, fixture_root: Path | None = None) -> str:
    safe_path = approved_existing_file(path, fixture_root=fixture_root)
    digest = hashlib.sha256()
    with safe_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def strategy_definition(registry) -> dict:
    specs = [spec for spec in registry.specs()
             if not (spec.indicator_id == "TURNOVER_RATE" and spec.version != TURNOVER_VERSION)]
    if len(specs) != INDICATOR_COUNT or len({s.indicator_id for s in specs}) != INDICATOR_COUNT:
        raise ValueError("FROZEN_INDICATOR_CATALOG_CHANGED")
    indicators = [
        {"id": spec.indicator_id, "version": spec.version,
         "primary_output": spec.outputs[0], "params": dict(spec.params),
         "formula_hash": registry.formula_hash(spec.indicator_id, spec.version)}
        for spec in specs
    ]
    catalog_hash = hashlib.sha256(json.dumps(
        indicators, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    if catalog_hash != CATALOG_SHA256:
        raise ValueError("FROZEN_INDICATOR_CATALOG_CHANGED")
    return {
        "strategy_id": STRATEGY_ID,
        "indicator_count": INDICATOR_COUNT,
        "catalog_sha256": catalog_hash,
        "indicators": indicators,
        "rule": {
            "vote": "first registered output rises from the previous completed daily bar",
            "all_required": True,
            "buy_at_close_if_votes_at_least": BUY_VOTES,
            "sell_at_close_if_votes_below": BUY_VOTES,
            "earliest_order_execution": "NEXT_SESSION_OPEN",
            "weighting": "one vote per indicator; no fitted weights",
            "purpose": "mechanical integration probe; no claim of predictive merit",
        },
    }


def decision_trace(results: dict, definition: dict) -> list[dict]:
    """Every ready indicator supplies one vote to both sides of the rule."""
    items = definition["indicators"]
    if len(results) != INDICATOR_COUNT or {x["id"] for x in items} != set(results):
        raise ValueError("ALL_INDICATORS_REQUIRED_FOR_DECISION")
    first = results[items[0]["id"]].index
    values = {}
    ready = {}
    for item in items:
        result = results[item["id"]]
        if not result.index.equals(first):
            raise ValueError("INDICATOR_TIME_INDEX_MISMATCH")
        values[item["id"]] = result.output(item["primary_output"]).to_numpy(dtype=float)
        ready[item["id"]] = result.ready().to_numpy(dtype=bool)
    trace = []
    for i in range(1, len(first)):
        if not all(ready[item["id"]][i] and ready[item["id"]][i - 1]
                   and np.isfinite(values[item["id"]][i])
                   and np.isfinite(values[item["id"]][i - 1]) for item in items):
            continue
        votes = sum(values[item["id"]][i] > values[item["id"]][i - 1]
                    for item in items)
        trace.append({"date": int(first[i]), "rising_votes": int(votes),
                      "decision_at_close": "BUY" if votes >= BUY_VOTES else "SELL"})
    return trace


def run_chain(frame: pd.DataFrame, decisions: dict, definition: dict,
              daily_hash: str, turn_hash: str, states_hash: str,
              actions_hash: str, *, execution_states: pd.DataFrame | None = None,
              historical_states_hash: str | None = None,
              account_end_date: int = ACCOUNT_END_DATE,
              corporate_events: tuple[dict, ...] | None = None) -> dict:
    """Exercise the existing event engine, then recompute each account close."""
    account_bars = frame.loc[frame["date"].between(ACCOUNT_START_DATE, account_end_date)]
    calendar = sorted(int(day) for day in account_bars["date"].unique())
    if not calendar or calendar[0] != ACCOUNT_START_DATE or calendar[-1] != account_end_date:
        raise ValueError("ACCOUNT_CALENDAR_INCOMPLETE")
    store = MarketDataStore()
    for symbol in SYMBOLS:
        bars = frame.loc[frame["symbol"] == symbol].sort_values("date")
        if set(calendar) - set(bars["date"].astype(int)):
            raise ValueError(f"ACCOUNT_DAILY_COVERAGE_INCOMPLETE:{symbol}")
        store.add_daily_raw(symbol, bars.set_index("date")[[
            "open", "high", "low", "close", "volume", "amount", "prev_close"]])

    source_hash = hashlib.sha256(
        f"{daily_hash}:{turn_hash}:{states_hash}:{actions_hash}"
        f"{':' + historical_states_hash if historical_states_hash else ''}".encode()).hexdigest()
    config = EngineConfig(
        initial_cash=1_000_000.0, max_positions=len(SYMBOLS),
        max_position_weight=1.0 / len(SYMBOLS),
        max_holding_days=1000, mode="DAILY", feature_price_mode="raw",
        start_date=calendar[0], end_date=calendar[-1],
        enable_index_filter=False, index_filter_enabled=False,
        strategy_hash=definition.get("rule_identity", definition["catalog_sha256"]), data_manifest_hash=source_hash,
        execution_model_version=("ALL_51_S1_INDIVIDUAL_DIVIDEND_V1" if corporate_events is not None
                                 else "ALL_51_PILOT_STATE_GATED_V1" if execution_states is not None
                                 else "ALL_51_PILOT_ENGINE_V2_NEXT_OPEN_V1"),
        persist_run_manifest=False,
    )
    if corporate_events is not None and execution_states is None:
        raise ValueError("CORPORATE_ACCOUNT_REQUIRES_EXECUTION_STATES")
    if execution_states is None:
        engine = BacktestEngineV2(store, calendar, config=config, seed=0)
    else:
        required = {"symbol", "trade_date", "listed", "delisted", "universe_member",
                    "eligibility_status", "st_status", "suspension_status", "board"}
        if not required <= set(execution_states):
            raise ValueError("EXECUTION_STATES_FIELDS_MISSING")
        if execution_states.duplicated(["symbol", "trade_date"]).any():
            raise ValueError("EXECUTION_STATES_DUPLICATE")
        master = DegradedStateMasterV1(execution_states)
        if corporate_events is None:
            engine = StateGatedPilotEngine(
                store, calendar, config=config, seed=0, security_master=master)
        else:
            if any(event["record_date"] < calendar[0]
                   or event["effective_date"] > calendar[-1]
                   or event["payment_date"] != event["effective_date"]
                   for event in corporate_events):
                raise ValueError("CORPORATE_EVENT_OUTSIDE_SUPPORTED_ACCOUNT_WINDOW")
            action_dataset = SimpleNamespace(
                dataset_id="ALL_51_S1_PERSONAL_CASH_DIVIDENDS_V1",
                events=corporate_events, coverage_symbols=SYMBOLS,
                manifest_sha256=actions_hash,
            )
            engine = IndividualDividendPilotEngine(
                store, calendar, config=config, seed=0, security_master=master,
                action_dataset=action_dataset)
    signals = []
    for symbol in SYMBOLS:
        for item in decisions[symbol]:
            day = item["date"]
            if day not in calendar:
                continue
            timestamp = pd.Timestamp(str(day)).tz_localize("Asia/Shanghai") + pd.Timedelta(hours=15, minutes=30)
            signals.append(Signal(
                strategy_id=definition.get("execution_strategy_id", STRATEGY_ID),
                signal_id=f"{definition.get('execution_strategy_id', STRATEGY_ID)}:{symbol}:{day}",
                symbol=symbol, generated_at=timestamp,
                direction=Side(item["decision_at_close"]),
                metadata={"rising_votes": item["rising_votes"]},
            ))
    engine.add_signals(signals)
    result = engine.run()
    valuation = official_equity_curve(
        result.equity_curve, calendar=calendar, start_date=calendar[0],
        end_date=calendar[-1], calendar_identity=source_hash,
    )
    orders = result.orders.orders
    trades = sorted(result.trades, key=lambda trade: (trade.fill_time, trade.trade_id))
    order_fill_qty = {order_id: 0 for order_id in orders}
    cash = config.initial_cash
    quantities = {symbol: 0 for symbol in SYMBOLS}
    independent_lots = []
    entitlements = {}
    independent_dividend_income = 0.0
    independent_dividend_tax = 0.0
    checks = []
    issues = list(result.ledger.check_invariants())
    trade_index = 0
    state_rows = (execution_states.set_index(["symbol", "trade_date"])
                  if execution_states is not None else None)
    t1_sell_checks = 0
    snapshots = {int(s.timestamp.strftime("%Y%m%d")): s
                 for s in result.equity_curve if s.timestamp.hour == 15 and s.timestamp.minute == 30}
    for day in calendar:
        for event in corporate_events or ():
            if event["payment_date"] == day:
                if event["event_id"] not in entitlements:
                    issues.append(f"DIVIDEND_ENTITLEMENT_MISSING:{event['event_id']}")
                amount = entitlements.get(event["event_id"], 0) * float(event["terms"]["cash_per_share"])
                cash += amount
                independent_dividend_income += amount
        while trade_index < len(trades) and int(trades[trade_index].fill_time.strftime("%Y%m%d")) <= day:
            trade = trades[trade_index]
            order = orders.get(trade.order_id)
            side = trade.side.value
            gross = float(trade.quantity * trade.price)
            expected_fee = round(max(gross * config.commission_rate, config.min_commission)
                                 + (gross * config.stamp_tax_rate if side == "SELL" else 0.0), 4)
            trade_day = int(trade.fill_time.strftime("%Y%m%d"))
            open_price = float(store.get_daily_bar(trade.symbol, trade_day)["open"])
            expected_price = round(open_price * (1 + config.slippage_bps if side == "BUY"
                                                 else 1 - config.slippage_bps), 4)
            if (order is None or trade.fill_time <= order.created_at
                    or trade_day <= int(order.created_at.strftime("%Y%m%d"))):
                issues.append(f"ORDER_TIMING_INVALID:{trade.trade_id}")
            if order is not None:
                order_fill_qty[order.order_id] += trade.quantity
                if order.symbol != trade.symbol or order.side != trade.side:
                    issues.append(f"ORDER_TRADE_IDENTITY_MISMATCH:{trade.trade_id}")
            if not np.isclose(trade.gross_value, gross, rtol=0, atol=1e-6):
                issues.append(f"TRADE_GROSS_MISMATCH:{trade.trade_id}")
            if not np.isclose(trade.fee, expected_fee, rtol=0, atol=1e-4):
                issues.append(f"TRADE_FEE_MISMATCH:{trade.trade_id}")
            if not np.isclose(trade.price, expected_price, rtol=0, atol=1e-6):
                issues.append(f"NEXT_OPEN_PRICE_MISMATCH:{trade.trade_id}")
            if trade.reality_flag != "OK":
                issues.append(f"UNSUPPORTED_TRADE:{trade.trade_id}")
            if state_rows is not None:
                key = (trade.symbol, trade_day)
                if key not in state_rows.index:
                    issues.append(f"FILL_WITHOUT_EXECUTION_STATE:{trade.trade_id}")
                else:
                    state = state_rows.loc[key]
                    if (state["suspension_status"] != "TRADING"
                            or not known_bool(state["listed"], True)
                            or not known_bool(state["delisted"], False)
                            or (side == "BUY" and (state["st_status"] != "NORMAL"
                                or not known_bool(state["universe_member"], True)
                                or state["eligibility_status"] != "ELIGIBLE"))):
                        issues.append(f"FILL_CONFLICTS_WITH_EXECUTION_STATE:{trade.trade_id}")
            cash += -gross - trade.fee if side == "BUY" else gross - trade.fee
            quantities[trade.symbol] += trade.quantity if side == "BUY" else -trade.quantity
            if side == "BUY":
                independent_lots.append({"symbol": trade.symbol, "buy_date": trade_day,
                                         "remaining": trade.quantity, "dividends": {}})
            else:
                remaining = trade.quantity
                for lot in independent_lots:
                    if lot["symbol"] != trade.symbol or lot["remaining"] <= 0 or remaining <= 0:
                        continue
                    sold = min(lot["remaining"], remaining)
                    t1_sell_checks += 1
                    if trade_day <= lot["buy_date"]:
                        issues.append(f"T1_SELLABILITY_VIOLATION:{trade.trade_id}")
                    for event in corporate_events or ():
                        if event["symbol"] != trade.symbol:
                            continue
                        entitled = min(sold, lot["dividends"].get(event["event_id"], 0))
                        if entitled:
                            bought = pd.Timestamp(str(lot["buy_date"]))
                            sold_at = pd.Timestamp(str(trade_day))
                            rate = (0.20 if sold_at <= bought + pd.DateOffset(months=1)
                                    else 0.10 if sold_at <= bought + pd.DateOffset(years=1) else 0.0)
                            tax = round(entitled * float(event["terms"]["cash_per_share"]) * rate, 4)
                            cash -= tax
                            independent_dividend_tax += tax
                            lot["dividends"][event["event_id"]] -= entitled
                    lot["remaining"] -= sold
                    remaining -= sold
                if remaining:
                    issues.append(f"INDEPENDENT_LOT_SELL_EXCEEDED:{trade.trade_id}")
            if cash < -1e-6 or quantities[trade.symbol] < 0:
                issues.append(f"INDEPENDENT_ACCOUNT_NEGATIVE:{trade.trade_id}")
            trade_index += 1
        for event in corporate_events or ():
            if event["record_date"] == day:
                entitled = 0
                for lot in independent_lots:
                    if lot["symbol"] == event["symbol"] and lot["remaining"]:
                        lot["dividends"][event["event_id"]] = lot["remaining"]
                        entitled += lot["remaining"]
                entitlements[event["event_id"]] = entitled
        if any(sum(lot["remaining"] for lot in independent_lots if lot["symbol"] == symbol)
               != quantities[symbol] for symbol in SYMBOLS):
            issues.append(f"INDEPENDENT_LOT_QUANTITY_MISMATCH:{day}")
        market_value = sum(quantities[symbol] * float(store.get_daily_bar(symbol, day)["close"])
                           for symbol in SYMBOLS)
        snapshot = snapshots[day]
        delta = max(abs(snapshot.cash - cash), abs(snapshot.market_value - market_value),
                    abs(snapshot.equity - (cash + market_value)))
        if delta > 1e-3:
            issues.append(f"DAILY_ACCOUNT_MISMATCH:{day}:{delta:.6f}")
        checks.append({"date": day, "cash": round(cash, 4),
                       "market_value": round(market_value, 4),
                       "equity": round(cash + market_value, 4),
                       "position_quantities": dict(quantities),
                       "max_abs_ledger_delta": round(delta, 6)})
    if trade_index != len(trades):
        issues.append("TRADE_OUTSIDE_ACCOUNT_WINDOW")
    for order in orders.values():
        if order_fill_qty[order.order_id] != order.filled_quantity:
            issues.append(f"ORDER_FILL_QUANTITY_MISMATCH:{order.order_id}")
    if corporate_events is not None:
        if not np.isclose(result.ledger.action_income, independent_dividend_income,
                          rtol=0, atol=1e-4):
            issues.append("DIVIDEND_INCOME_MISMATCH")
        if not np.isclose(result.ledger.dividend_tax_withheld, independent_dividend_tax,
                          rtol=0, atol=1e-4):
            issues.append("DIVIDEND_TAX_MISMATCH")
    return {
        "status": "RECONCILED_DIAGNOSTIC" if not issues else "RECONCILIATION_FAILED",
        "account_dates": [calendar[0], calendar[-1]],
        "n_account_days": len(calendar), "n_signals": len(signals),
        "n_orders": len(orders), "n_trades": len(trades),
        "n_no_trade_days": len(calendar) - len({int(t.fill_time.strftime("%Y%m%d")) for t in trades}),
        "n_independent_t1_lot_checks": t1_sell_checks,
        "execution_assumptions": {
            "initial_cash": config.initial_cash, "max_positions": config.max_positions,
            "max_position_weight": config.max_position_weight,
            "fee_commission_rate": config.commission_rate,
            "min_commission": config.min_commission,
            "sell_stamp_tax_rate": config.stamp_tax_rate,
            "slippage_fraction": config.slippage_bps,
            "lot_size": config.lot_size,
            "fill_reference": "NEXT_SESSION_OPEN_DAILY_BAR",
            "daily_volume_participation_limit": 0.10,
            "index_filter_enabled": False, "pit_engine_gate_enabled": False,
            "historical_execution_state_gate": execution_states is not None,
            "account_tax_profile": ("INDIVIDUAL_A_SHARE_2015_101"
                                    if corporate_events is not None else "NOT_MODELED"),
        },
        "run_identity": result.context.to_dict(),
        "engine_summary": result.summary(),
        "official_valuation": valuation.to_dict(),
        "independent_account_checks": checks,
        "issues": issues,
        "trades": [{"id": t.trade_id, "order_id": t.order_id,
                    "symbol": t.symbol, "side": t.side.value,
                    "date": int(t.fill_time.strftime("%Y%m%d")),
                    "quantity": t.quantity, "price": t.price,
                    "gross_value": t.gross_value, "fee": t.fee,
                    "reality_flag": t.reality_flag} for t in trades],
        "orders": [{"id": o.order_id, "signal_id": o.signal_id,
                    "symbol": o.symbol, "side": o.side.value,
                    "created_at": str(o.created_at), "quantity": o.quantity,
                    "filled_quantity": o.filled_quantity, "status": o.status.value,
                    "last_event_message": result.event_log.by_order(o.order_id)[-1].message}
                   for o in orders.values()],
        "order_status_counts": {status: sum(o.status.value == status for o in orders.values())
                                for status in sorted({o.status.value for o in orders.values()})},
        "sizing_skips": result.sizing_skips,
        "corporate_account": ({"events": list(corporate_events),
                               "event_audit": result.ledger.action_audit,
                               "independent_entitlements": entitlements,
                               "dividend_income": independent_dividend_income,
                               "dividend_tax_withheld": independent_dividend_tax}
                              if corporate_events is not None else None),
        "scope": ("S1_PERSONAL_DIVIDEND_ACCOUNT_DIAGNOSTIC; no alpha or PIT source-publication certification"
                  if corporate_events is not None
                  else "MECHANICAL_INTEGRATION_ONLY; no alpha, PIT eligibility or corporate-action certification"),
    }


def probe(daily_path: Path, turn_path: Path | None = None,
          states_path: Path | None = None, actions_path: Path | None = None,
          turn_manifest_path: Path | None = None,
          fixture_root: Path | None = None) -> dict:
    daily_path = approved_existing_file(daily_path, fixture_root=fixture_root)
    turn_path = (approved_existing_file(turn_path, fixture_root=fixture_root)
                 if turn_path is not None else None)
    states_path = (approved_existing_file(states_path, fixture_root=fixture_root)
                   if states_path is not None else None)
    actions_path = (approved_existing_file(actions_path, fixture_root=fixture_root)
                    if actions_path is not None else None)
    turn_manifest_path = (approved_existing_file(turn_manifest_path, fixture_root=fixture_root)
                          if turn_manifest_path is not None else None)
    audit = []
    reader = GuardedResearchReader(audit_sink=audit.append)
    frame = reader.read_parquet(
        daily_path, columns=["symbol", "date", "open", "high", "low", "close",
                             "volume", "amount", "prev_close", "adjustflag"],
        start_date=START_DATE, end_date=END_DATE,
    )
    vendor_turn = None
    if turn_path is not None:
        vendor_turn = reader.read_parquet(
            turn_path, columns=["symbol", "date", "volume", "turn", "tradestatus"],
            start_date=START_DATE, end_date=END_DATE,
        )
        if vendor_turn.duplicated(["symbol", "date"]).any():
            raise ValueError("DUPLICATE_BAOSTOCK_TURN")
    state_frame = None
    if states_path is not None:
        state_frame = reader.read_parquet(
            states_path, columns=["symbol", "trade_date", "listed", "delisted",
                                  "universe_member", "eligibility_status", "st_status",
                                  "suspension_status"], date_column="trade_date",
            start_date=ACCOUNT_START_DATE, end_date=ACCOUNT_END_DATE,
        )
    actions = json.loads(actions_path.read_text(encoding="utf-8")) if actions_path else None
    turn_manifest = (json.loads(turn_manifest_path.read_text(encoding="utf-8"))
                     if turn_manifest_path else None)
    registry = pilot_registry()
    definition = strategy_definition(registry)
    samples = {}
    blockers = set()
    if turn_manifest is not None and (turn_path is None
            or turn_manifest.get("parquet_sha256") != sha256_file(turn_path, fixture_root=fixture_root)
            or turn_manifest.get("provider") != "BaoStock"
            or turn_manifest.get("api") != "query_history_k_data_plus"
            or turn_manifest.get("adjustflag") != "3"
            or turn_manifest.get("requested_dates") != [START_DATE, END_DATE]
            or turn_manifest.get("symbols") != list(SYMBOLS)
            or not {"date", "code", "volume", "turn", "tradestatus"}.issubset(
                turn_manifest.get("fields", []))):
        blockers.add("BAOSTOCK_TURN_MANIFEST_INVALID")
    prefix_checks = {}
    if state_frame is None:
        blockers.add("ACCOUNT_HISTORICAL_STATES_MISSING")
    elif state_frame.duplicated(["symbol", "trade_date"]).any():
        blockers.add("ACCOUNT_HISTORICAL_STATES_DUPLICATE")
    else:
        expected_days = set(frame.loc[frame["date"].between(
            ACCOUNT_START_DATE, ACCOUNT_END_DATE), "date"].astype(int))
        for symbol in SYMBOLS:
            rows = state_frame.loc[state_frame["symbol"] == symbol]
            eligible = (rows["listed"].eq(True) & rows["delisted"].eq(False)
                        & rows["universe_member"].eq(True)
                        & rows["eligibility_status"].eq("ELIGIBLE")
                        & rows["st_status"].eq("NORMAL")
                        & rows["suspension_status"].eq("TRADING"))
            if set(rows["trade_date"].astype(int)) != expected_days or not eligible.all():
                blockers.add(f"ACCOUNT_HISTORICAL_STATE_INVALID:{symbol}")
    if actions is None:
        blockers.add("ACCOUNT_CORPORATE_ACTION_SCREEN_MISSING")
    elif (actions.get("provider") != "BaoStock"
          or actions.get("api") != "query_dividend_data"
          or actions.get("queried_report_years") != [2022, 2023, 2024]
          or actions.get("symbols") != list(SYMBOLS)
          or actions.get("account_dates") != [ACCOUNT_START_DATE, ACCOUNT_END_DATE]
          or actions.get("account_window_events") != []):
        blockers.add("ACCOUNT_CORPORATE_ACTION_SCREEN_INVALID_OR_EVENT_PRESENT")
    result_series = {}
    for symbol in SYMBOLS:
        bars = frame.loc[frame["symbol"] == symbol].sort_values("date")
        if bars.empty or bars["date"].duplicated().any():
            blockers.add(f"DAILY_COVERAGE_INVALID:{symbol}")
            samples[symbol] = {"rows": len(bars), "indicators": {}}
            continue
        if not bars["adjustflag"].astype(str).eq("3").all():
            blockers.add(f"DAILY_NOT_UNADJUSTED:{symbol}")
        index = pd.Index(bars["date"].astype(int).to_numpy(), name="date")
        series = {name: pd.Series(bars[name].to_numpy(dtype=float), index=index)
                  for name in ("open", "high", "low", "close", "volume", "amount", "prev_close")}
        turn = None
        if vendor_turn is not None:
            matching = vendor_turn.loc[vendor_turn["symbol"] == symbol].set_index("date")
            if (set(matching.index.astype(int)) != set(index)
                    or not matching["tradestatus"].eq(1).all()
                    or not np.isfinite(matching["turn"].to_numpy(dtype=float)).all()
                    or not matching["turn"].ge(0).all()):
                blockers.add(f"BAOSTOCK_TURN_COVERAGE_INVALID:{symbol}")
            else:
                matching = matching.reindex(index)
                if not np.allclose(matching["volume"].to_numpy(dtype=float),
                                   series["volume"].to_numpy(dtype=float), rtol=0, atol=1e-6):
                    blockers.add(f"BAOSTOCK_TURN_VOLUME_MISMATCH:{symbol}")
                turn = pd.Series(matching["turn"].to_numpy(dtype=float), index=index)
        indicators = {}
        computed = {}
        for item in definition["indicators"]:
            indicator_id = item["id"]
            try:
                result = registry.compute(
                    indicator_id, series["close"], version=item["version"],
                    high=series["high"], low=series["low"], open_=series["open"],
                    volume=series["volume"], amount=series["amount"],
                    prev_close=series["prev_close"], params=item["params"],
                    extra_data={"vendor_turn": turn} if turn is not None else None,
                )
                computed[indicator_id] = result
                values = result.output(item["primary_output"])
                ready = result.ready() & np.isfinite(values)
                indicators[indicator_id] = {
                    "status": "COMPUTABLE",
                    "ready_rows": int(ready.sum()),
                    "last_ready": bool(ready.iloc[-1]),
                }
                if not ready.iloc[-1]:
                    blockers.add(f"INDICATOR_NOT_READY:{symbol}:{indicator_id}")
            except (ValueError, TypeError) as exc:
                reason = str(exc)
                indicators[indicator_id] = {"status": "BLOCKED", "reason": reason}
                blockers.add(reason)
        samples[symbol] = {
            "rows": len(bars), "first_date": int(index[0]), "last_date": int(index[-1]),
            "indicators": indicators,
        }
        if turn is not None and len(computed) == INDICATOR_COUNT:
            checked = 0
            for target_day in (20240131, 20240329, 20240531):
                candidates = index[index <= target_day]
                if len(candidates) < 2:
                    continue
                day = int(candidates[-1])
                prefix = {name: values.loc[:day] for name, values in series.items()}
                for item in definition["indicators"]:
                    indicator_id = item["id"]
                    replay = registry.compute(
                        indicator_id, prefix["close"], version=item["version"],
                        high=prefix["high"], low=prefix["low"], open_=prefix["open"],
                        volume=prefix["volume"], amount=prefix["amount"],
                        prev_close=prefix["prev_close"], params=item["params"],
                        extra_data={"vendor_turn": turn.loc[:day]} if indicator_id == "TURNOVER_RATE" else None,
                    )
                    full = computed[indicator_id]
                    actual = float(replay.output(item["primary_output"]).loc[day])
                    expected = float(full.output(item["primary_output"]).loc[day])
                    if (bool(replay.ready().loc[day]) != bool(full.ready().loc[day])
                            or not np.isclose(actual, expected, rtol=1e-10, atol=1e-10,
                                              equal_nan=True)):
                        blockers.add(f"INDICATOR_PREFIX_REPLAY_MISMATCH:{symbol}:{indicator_id}:{day}")
                    checked += 1
            prefix_checks[symbol] = {"n_indicator_cut_checks": checked,
                                     "cut_targets": [20240131, 20240329, 20240531]}
        result_series[symbol] = computed

    # Missing any vote blocks the entire diagnostic chain.
    decisions = ({symbol: decision_trace(result_series[symbol], definition)
                  for symbol in SYMBOLS} if not blockers else {})
    daily_hash = sha256_file(daily_path, fixture_root=fixture_root)
    turn_hash = sha256_file(turn_path, fixture_root=fixture_root) if turn_path is not None else None
    states_hash = sha256_file(states_path, fixture_root=fixture_root) if states_path is not None else None
    actions_hash = sha256_file(actions_path, fixture_root=fixture_root) if actions_path is not None else None
    chain = run_chain(frame, decisions, definition, daily_hash, turn_hash,
                      states_hash, actions_hash) if not blockers else None
    if chain is not None and chain["issues"]:
        blockers.add("ACCOUNT_RECONCILIATION_FAILED")
    return {
        "strategy": definition,
        "code_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                             text=True).strip(),
        "probe_source_sha256": sha256_file(Path(__file__)),
        "input": {"path": str(daily_path.resolve()), "sha256": daily_hash,
                  "requested_dates": [START_DATE, END_DATE], "symbols": list(SYMBOLS)},
        "turn_input": (
            {"path": str(turn_path.resolve()), "sha256": turn_hash,
             "provider": "BaoStock", "field": "turn", "unit": "percent",
             "historical_available_at_verified": False,
             "manifest_path": str(turn_manifest_path.resolve()) if turn_manifest_path else None,
             "manifest_sha256": (sha256_file(turn_manifest_path, fixture_root=fixture_root)
                                  if turn_manifest_path else None),
             "provenance_manifest_matched": turn_manifest is not None and
                 "BAOSTOCK_TURN_MANIFEST_INVALID" not in blockers}
            if turn_path is not None else None),
        "states_input": ({"path": str(states_path.resolve()), "sha256": states_hash}
                         if states_path is not None else None),
        "actions_input": ({"path": str(actions_path.resolve()), "sha256": actions_hash,
                           "account_window_events": len(actions["account_window_events"])}
                          if actions_path is not None else None),
        "read_audit": audit,
        "samples": samples,
        "prefix_causality_checks": prefix_checks,
        "blockers": sorted(blockers),
        "decision_trace": decisions,
        "signal_status": "BLOCKED" if chain is None else "FIXED_RULE_SIGNALS_SUBMITTED",
        "account_status": "NOT_RUN" if chain is None else chain["status"],
        "chain": chain,
        "research_conclusion": "NO_STRATEGY_VALIDITY_CLAIM",
        "research_assessment": {
            "mechanical_chain": "PASSED" if chain is not None and not blockers else "BLOCKED",
            "real_data_scope": "TWO_BANK_STOCKS_76_SESSIONS" if chain is not None else "NONE",
            "strategy_effectiveness": "UNDETERMINED",
            "paper_eligibility": "NOT_EVALUATED",
            "limitations": [
                "vendor turnover publication timestamp not independently verified",
                "corporate actions checked only with BaoStock dividend records in the account window",
                "historical state snapshot checked but not wired into the engine PIT gate",
                "two symbols and one development window; no out-of-sample or statistical test",
                "daily-bar next-open fills do not prove live fillability",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-parquet", type=Path, required=True)
    parser.add_argument("--turn-parquet", type=Path)
    parser.add_argument("--turn-manifest-json", type=Path)
    parser.add_argument("--states-parquet", type=Path)
    parser.add_argument("--actions-json", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.resolve() != REPORT_PATH.resolve():
        parser.error(f"--report must be {REPORT_PATH}")
    result = probe(args.daily_parquet, args.turn_parquet,
                   args.states_parquet, args.actions_json, args.turn_manifest_json)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"signal_status": result["signal_status"],
                      "blockers": result["blockers"],
                      "report": str(REPORT_PATH)}, ensure_ascii=False))
    return 2 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
