"""已固定快照的限定接入；历史身份 UNVERIFIED，历史批次入口禁用。"""
from __future__ import annotations
import csv
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping
import numpy as np
import pandas as pd
from chanlun_trader.engine.portfolio_exit import PortfolioExitEvaluatorV1  # noqa: E402
from chanlun_trader.engine.signal import Side  # noqa: E402
from chanlun_trader.engine.time_types import ensure_aware  # noqa: E402
from chanlun_trader.research.strategy_validation import compiled_signals_to_engine_signals, write_json

from chanlun_trader.research_factory.source_dependencies import load_legacy_module
legacy = load_legacy_module()
RESEARCH_END = legacy.RESEARCH_END


def jsonable(value: Any) -> Any:
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, Side):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return jsonable(value.__dict__)
    return value


def write_csv(path: Path, rows: Iterable[Mapping[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: jsonable(row.get(field, "")) for field in fields})


def corrected_order_record(order: Any) -> dict[str, Any]:
    return {
        "order_id": order.order_id,
        "symbol": order.symbol,
        "side": order.side.value,
        "quantity": order.quantity,
        "filled_quantity": order.filled_quantity,
        "status": order.status.value,
        "reason": order.reason,
        "reason_code": order.reason_code,
        "created_at": str(order.created_at),
        "strategy_id": order.strategy_id,
        "position_id": order.position_id or "",
        "lot_id": order.lot_id or "",
        "signal_id": order.signal_id,
        "intent_id": order.intent_id,
    }


def corrected_trade_record(trade: Any) -> dict[str, Any]:
    return {
        "trade_id": trade.trade_id,
        "symbol": trade.symbol,
        "side": trade.side.value,
        "quantity": trade.quantity,
        "price": trade.price,
        "fee": trade.fee,
        "fill_time": str(trade.fill_time),
        "realized_pnl": trade.realized_pnl,
        "reality_flag": trade.reality_flag,
        "position_id": trade.position_id,
        "lot_id": trade.lot_id or "",
        "order_id": trade.order_id,
    }


def corrected_snapshot_record(snapshot: Any, regimes: Mapping[int, str]) -> dict[str, Any]:
    d = int(snapshot.timestamp.strftime("%Y%m%d"))
    return {
        "timestamp": str(snapshot.timestamp),
        "equity": snapshot.equity,
        "cash": snapshot.cash,
        "market_value": snapshot.market_value,
        "positions": snapshot.positions,
        "turnover": snapshot.turnover,
        "regime": regimes.get(d, "UNKNOWN"),
        "realized_pnl": snapshot.realized_pnl,
        "unrealized_pnl": snapshot.unrealized_pnl,
    }


def session_index(calendar: list[int]) -> dict[int, int]:
    return {int(day): index for index, day in enumerate(calendar)}


def percentile(values: list[int], q: float) -> float | None:
    return float(np.quantile(np.asarray(values, dtype=float), q)) if values else None


def compute_metrics_v3(result: Any, initial_cash: float, regimes: Mapping[int, str], calendar: list[int],
                       fixed_holding_sessions: int) -> dict[str, Any]:
    snapshots = list(result.ledger.snapshots)
    sells = [trade for trade in result.ledger.valid_trades if trade.side == Side.SELL]
    buys = {trade.lot_id: trade for trade in result.ledger.valid_trades if trade.side == Side.BUY and trade.lot_id}
    idx = session_index(calendar)
    holding_sessions: list[int] = []
    calendar_days: list[int] = []
    exit_delays: list[int] = []
    for trade in sells:
        buy = buys.get(trade.lot_id)
        if buy is None:
            continue
        buy_date = int(buy.fill_time.strftime("%Y%m%d"))
        sell_date = int(trade.fill_time.strftime("%Y%m%d"))
        if buy_date not in idx or sell_date not in idx:
            continue
        actual = idx[sell_date] - idx[buy_date]
        holding_sessions.append(actual)
        calendar_days.append(int((trade.fill_time.date() - buy.fill_time.date()).days))
        lot = result.ledger.lots.get(str(trade.lot_id)) if trade.lot_id else None
        due_index = lot.exit_due_index if lot is not None else idx[buy_date] + fixed_holding_sessions
        if due_index is None:
            due_index = idx[buy_date] + fixed_holding_sessions
        triggers = [event.payload["trade_session_index"] for event in result.event_log
            if event.event_type == "EXIT_DECISION" and event.lot_id == trade.lot_id
            and event.payload.get("reason_code") == "EXIT_DUE_STRUCTURE_INVALIDATION"]
        if triggers:
            due_index = min(triggers)
        exit_delays.append(max(0, idx[sell_date] - (int(due_index) + 1)))
    equities = [float(item.equity) for item in snapshots]
    if equities:
        peak = equities[0]
        max_dd = 0.0
        for value in equities:
            peak = max(peak, value)
            max_dd = min(max_dd, value / peak - 1.0 if peak else 0.0)
        net = equities[-1] / initial_cash - 1.0
        session_count = max(1, idx.get(int(snapshots[-1].timestamp.strftime("%Y%m%d")), 0) - idx.get(int(snapshots[0].timestamp.strftime("%Y%m%d")), 0))
        annualized = (1.0 + net) ** (252.0 / session_count) - 1.0 if net > -1 else -1.0
    else:
        net, max_dd, annualized = 0.0, 0.0, 0.0
    pnl = [float(t.realized_pnl) for t in sells]
    wins = [value for value in pnl if value > 0]
    losses = [-value for value in pnl if value < 0]
    status_counts = Counter()
    for event in result.event_log:
        if getattr(event, "event_type", "") == "ORDER_EVENT":
            status_counts[getattr(getattr(event, "order_status", None), "value", "UNKNOWN")] += 1
    end_snapshot = snapshots[-1] if snapshots else None
    open_lots = [lot for lot in result.ledger.lots.values() if lot.remaining_quantity > 0]
    due_open = [lot for lot in open_lots if lot.exit_state in {"EXIT_DUE", "SELL_PENDING", "PARTIALLY_FILLED"}]
    due_without_intent = [lot for lot in open_lots if lot.exit_due_index is not None and lot.exit_state == "OPEN" and len(calendar) - 1 >= lot.exit_due_index]
    return {
        "initial_cash": initial_cash,
        "final_equity": end_snapshot.equity if end_snapshot else initial_cash,
        "net_return": net,
        "annualized_return": annualized,
        "max_drawdown": max_dd,
        "closed_trade_count": len(sells),
        "win_rate": len(wins) / len(pnl) if pnl else None,
        "profit_factor": sum(wins) / sum(losses) if losses else (float("inf") if wins else None),
        "gross_profit": sum(wins),
        "gross_loss": -sum(losses),
        "average_trade_pnl": float(np.mean(pnl)) if pnl else None,
        "median_trade_pnl": float(np.median(pnl)) if pnl else None,
        "average_holding_sessions": float(np.mean(holding_sessions)) if holding_sessions else None,
        "median_holding_sessions": float(np.median(holding_sessions)) if holding_sessions else None,
        "p90_holding_sessions": percentile(holding_sessions, 0.90),
        "p95_holding_sessions": percentile(holding_sessions, 0.95),
        "max_holding_sessions": max(holding_sessions) if holding_sessions else None,
        "average_exit_delay_sessions": float(np.mean(exit_delays)) if exit_delays else 0.0,
        "max_exit_delay_sessions": max(exit_delays) if exit_delays else 0,
        "average_calendar_holding_days": float(np.mean(calendar_days)) if calendar_days else None,
        "median_calendar_holding_days": float(np.median(calendar_days)) if calendar_days else None,
        "turnover": float(result.ledger.turnover),
        "turnover_ratio": float(result.ledger.turnover) / initial_cash if initial_cash else None,
        "total_fees": float(result.ledger.total_fees),
        "average_exposure": sum(float(s.market_value) / max(float(s.equity), 1e-9) for s in snapshots) / len(snapshots) if snapshots else 0.0,
        "open_positions_at_end": sum(1 for p in result.ledger.positions.values() if p.quantity > 0),
        "open_lot_count_at_end": len(open_lots),
        "legal_pending_exit_count_at_end": len(due_open),
        "overdue_open_without_pending_sell_count": len(due_without_intent),
        "unsupported_trade_count": result.ledger.unsupported_trade_count,
        "certification_status": result.ledger.certification_status,
        "invariant_errors": result.ledger.check_invariants(),
        "order_count": len(result.orders.orders),
        "filled_order_count": sum(1 for order in result.orders.orders.values() if order.filled_quantity > 0),
        "order_status_counts": dict(status_counts),
        "realized_pnl": float(end_snapshot.realized_pnl) if end_snapshot else 0.0,
        "unrealized_pnl": float(end_snapshot.unrealized_pnl) if end_snapshot else 0.0,
        "open_market_value": float(end_snapshot.market_value) if end_snapshot else 0.0,
        "session_calendar_definition": "zero-based unified market trading-session index",
    }


def recompute_cost_stress_metrics(base_engine: Any, base_metrics: Mapping[str, Any], policy: Any,
                                  initial_cash: float, *, fee_mult: float, stamp_mult: float,
                                  slip_mult: float) -> dict[str, Any]:
    """Reprice the corrected fill path without regenerating signals or exits.

    Cost stress is a deterministic transformation of the corrected fills.  It
    intentionally keeps quantities, lot assignment, T+1 eligibility, pending
    exits, and order statuses unchanged; only fill price and fee cash flows are
    recomputed.  This avoids repeating the same lifecycle replay four times.
    """
    # 仅复用执行轨迹统计；扩展的 BASE 财务字段不能悄悄进入压力结果。
    reusable = ("initial_cash", "average_holding_sessions", "median_holding_sessions", "p90_holding_sessions",
        "p95_holding_sessions", "max_holding_sessions", "average_exit_delay_sessions", "max_exit_delay_sessions",
        "average_calendar_holding_days", "median_calendar_holding_days", "open_positions_at_end",
        "open_lot_count_at_end", "legal_pending_exit_count_at_end", "overdue_open_without_pending_sell_count",
        "unsupported_trade_count", "certification_status", "invariant_errors", "order_count", "filled_order_count",
        "order_status_counts", "open_market_value", "session_calendar_definition", "candidate_id",
        "candidate_preregistration_hash", "trial_id", "portfolio_id", "signal_diagnostics", "engine_version",
        "execution_model_version", "source_identity")
    metrics = {key: base_metrics[key] for key in reusable if key in base_metrics}
    base_bps = float(policy.slippage_bps)
    commission_rate = float(policy.commission_rate) * fee_mult
    min_commission = float(policy.min_commission) if fee_mult >= 1.0 else 0.0
    stamp_rate = float(policy.stamp_tax_rate) * stamp_mult
    lot_state: dict[str, dict[str, Any]] = {}
    fifo_by_symbol: dict[str, list[str]] = {}
    pnl: list[float] = []
    cash = float(initial_cash)
    total_fees = 0.0
    turnover = 0.0
    cash_deltas: list[tuple[pd.Timestamp, float]] = []

    trades = sorted(base_engine.ledger.valid_trades, key=lambda item: item.fill_time)
    for trade in trades:
        is_buy = trade.side == Side.BUY
        if base_bps:
            reference = trade.price / (1.0 + base_bps if is_buy else 1.0 - base_bps)
        else:
            reference = float(trade.price)
        stress_bps = base_bps * slip_mult
        stress_price = round(reference * (1.0 + stress_bps if is_buy else 1.0 - stress_bps), 4)
        gross = float(trade.quantity) * stress_price
        commission = max(gross * commission_rate, min_commission)
        stamp = gross * stamp_rate if not is_buy else 0.0
        fee = round(commission + stamp, 4)
        total_fees += fee
        turnover += gross
        if is_buy:
            lot_id = str(trade.lot_id or f"synthetic-{trade.trade_id}")
            lot_state[lot_id] = {"symbol": trade.symbol, "quantity": int(trade.quantity),
                                 "remaining": int(trade.quantity), "cost": gross + fee}
            fifo_by_symbol.setdefault(trade.symbol, []).append(lot_id)
            cash_flow = -(gross + fee)
        else:
            requested = int(trade.quantity)
            candidate_ids = [str(trade.lot_id)] if trade.lot_id and str(trade.lot_id) in lot_state else list(fifo_by_symbol.get(trade.symbol, []))
            remaining = requested
            realized = 0.0
            for lot_id in candidate_ids:
                if remaining <= 0:
                    break
                lot = lot_state[lot_id]
                if lot["remaining"] <= 0:
                    continue
                quantity = min(remaining, int(lot["remaining"]))
                avg_cost = float(lot["cost"]) / max(int(lot["quantity"]), 1)
                realized += (stress_price - avg_cost) * quantity
                lot["remaining"] -= quantity
                remaining -= quantity
            realized -= fee
            pnl.append(realized)
            cash_flow = gross - fee
        cash += cash_flow
        cash_deltas.append((trade.fill_time, cash_flow))

    base_cash_flow = 0.0
    scenario_cash_flow = 0.0
    deltas_by_time: dict[pd.Timestamp, float] = {}
    for trade, (_, scenario_flow) in zip(trades, cash_deltas):
        base_flow = (-(float(trade.gross_value) + float(trade.fee))
                     if trade.side == Side.BUY
                     else float(trade.gross_value) - float(trade.fee))
        base_cash_flow += base_flow
        scenario_cash_flow += scenario_flow
        deltas_by_time[trade.fill_time] = deltas_by_time.get(trade.fill_time, 0.0) + scenario_flow - base_flow

    snapshots = list(base_engine.ledger.snapshots)
    cumulative_delta = 0.0
    equity_path: list[float] = []
    ordered_deltas = sorted(deltas_by_time.items(), key=lambda item: item[0])
    delta_index = 0
    for snapshot in snapshots:
        while delta_index < len(ordered_deltas) and ordered_deltas[delta_index][0] <= snapshot.timestamp:
            cumulative_delta += ordered_deltas[delta_index][1]
            delta_index += 1
        equity_path.append(float(snapshot.equity) + cumulative_delta)
    final_equity = equity_path[-1] if equity_path else cash + float(base_metrics.get("open_market_value", 0.0))
    net_return = final_equity / initial_cash - 1.0 if initial_cash else 0.0
    if equity_path:
        peak = equity_path[0]
        max_drawdown = 0.0
        for value in equity_path:
            peak = max(peak, value)
            max_drawdown = min(max_drawdown, value / peak - 1.0 if peak else 0.0)
    else:
        max_drawdown = 0.0
    wins = [value for value in pnl if value > 0]
    losses = [-value for value in pnl if value < 0]
    sessions = max(1, len({s.timestamp.date() for s in snapshots}) - 1)
    open_cost = sum(lot["cost"] * lot["remaining"] / lot["quantity"] for lot in lot_state.values())
    metrics.update({
        "final_equity": float(final_equity),
        "net_return": float(net_return),
        "annualized_return": (1.0 + net_return) ** (252.0 / sessions) - 1.0 if net_return > -1 else -1.0,
        "average_exposure": sum(float(s.market_value) / max(equity, 1e-9)
            for s, equity in zip(snapshots, equity_path)) / len(snapshots) if snapshots else 0.0,
        "unrealized_pnl": round(float(base_metrics.get("open_market_value", 0.0)) - open_cost, 4),
        "max_drawdown": float(max_drawdown),
        "closed_trade_count": len(pnl),
        "win_rate": len(wins) / len(pnl) if pnl else None,
        "profit_factor": sum(wins) / sum(losses) if losses else (float("inf") if wins else None),
        "gross_profit": sum(wins),
        "gross_loss": -sum(losses),
        "average_trade_pnl": float(np.mean(pnl)) if pnl else None,
        "median_trade_pnl": float(np.median(pnl)) if pnl else None,
        "turnover": float(turnover),
        "turnover_ratio": float(turnover) / initial_cash if initial_cash else None,
        "total_fees": float(total_fees),
        "realized_pnl": round(float(sum(pnl)), 4),
        "stress_execution_mode": "COST_RECALCULATION_ON_CORRECTED_FILL_PATH",
        "stress_fill_path_unchanged": True,
        "stress_order_and_exit_semantics_reused": True,
    })
    return metrics


def run_corrected_candidate(root: Path, record: Any, trial_id: str, factor_values: pd.DataFrame,
                            store: Any, exec_calendar: list[int], universe: Mapping[int, set[str]],
                            status_map: Any, regimes: Mapping[int, str], events: Mapping[tuple[int, str], Mapping[str, dict[str, Any]]],
                            index_close: Mapping[int, float], policy: Any, *, portfolio_name: str,
                            initial_cash: float | None = None, fee_mult: float = 1.0,
                            stamp_mult: float = 1.0, slip_mult: float = 1.0,
                            evidence_run_id: str | None = None,
                            write_evidence: bool = False,
                            evidence_root: Path | None = None) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    candidate = record.candidate
    if candidate.price_mode.upper() != "RAW" or set(candidate.required_frequency) != {"DAILY"} or store.feature_price_mode != "raw":
        raise ValueError("R1_UNSUPPORTED_PRICE_OR_FREQUENCY")
    if candidate.candidate_type != "DAILY_SIGNAL" or candidate.entry_timing.get("type") != "NEXT_SESSION_OPEN" or candidate.entry_timing.get("signal_time") != "T_CLOSE":
        raise ValueError("R1_UNSUPPORTED_ENTRY_TIMING")
    if (candidate.max_positions != policy.max_positions or candidate.lot_size_contract.get("size") != policy.lot_size
            or (initial_cash is not None and initial_cash != policy.initial_cash)):
        raise ValueError("R1_POLICY_EXECUTION_MISMATCH")
    if (not exec_calendar or exec_calendar != sorted(set(exec_calendar))
            or min(exec_calendar) < policy.research_start or max(exec_calendar) > min(policy.research_end, RESEARCH_END)):
        raise ValueError("R1_CALENDAR_OUTSIDE_POLICY")
    if record.exit_predicate.exit_type not in {"FIXED_HOLD", "STRUCTURE_INVALIDATION"}:
        raise ValueError("R1_UNSUPPORTED_EXIT_CONTRACT")
    if policy.max_participation_rate != 0.10:
        raise ValueError("R1_UNSUPPORTED_PARTICIPATION_CONTRACT")
    if any(item.get("mode") == "HARD_GATE" for item in record.signal_predicate.regime_conditions) and any(
            regimes.get(d, "UNKNOWN") == "UNKNOWN" for d in exec_calendar):
        raise ValueError("R1_BENCHMARK_REQUIRED_NOT_AVAILABLE")
    if "available_at" not in factor_values:
        raise ValueError("R1_FACTOR_AVAILABLE_AT_EVIDENCE_MISSING")
    if write_evidence and (evidence_root is None or not evidence_root.is_absolute()
            or evidence_root.resolve().is_relative_to(root.resolve())
            or evidence_root.resolve().is_relative_to(Path(__file__).resolve().parents[1])):
        raise ValueError("R1_SEPARATE_EVIDENCE_ROOT_REQUIRED")
    compiler = legacy.StrategyCandidateCompilerV2().compile(record)
    factor_ids = [str(item["factor_id"]) for item in candidate.factor_bindings]
    factor_view = factor_values.set_index(["date", "symbol"], drop=False)
    engine = legacy.make_engine(store, exec_calendar, universe, index_close, policy,
                                record.preregistration_hash, initial_cash=initial_cash,
                                fee_mult=fee_mult, stamp_mult=stamp_mult, slip_mult=slip_mult)
    calendar_pos = session_index(exec_calendar)
    date_to_next = {day: exec_calendar[i + 1] for i, day in enumerate(exec_calendar[:-1])}
    event_condition = record.signal_predicate.event_conditions[0] if record.signal_predicate.event_conditions else None
    event_id_required = str(event_condition["event_id"]) if event_condition else None
    is_event = candidate.candidate_type == "EVENT_SIGNAL"
    signal_minute = 30 if is_event else 0
    emitted_records: list[dict[str, Any]] = []
    qualified_records: list[dict[str, Any]] = []
    day_cache: dict[int, tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]] = {}
    stats = Counter()

    def build_day(d: int, ts: pd.Timestamp) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        if d in day_cache:
            return day_cache[d]
        try:
            day = factor_view.xs(d, level="date", drop_level=False)
        except KeyError:
            day_cache[d] = ([], {})
            return day_cache[d]
        next_open = legacy.ts_for_date(date_to_next[d], 9, 30) if d in date_to_next else ts
        positions = {p.symbol: p for p in engine.ledger.positions.values() if p.quantity > 0}
        active_symbols = set(universe.get(d, set()))
        if event_id_required:
            event_symbols = {symbol for (event_date, symbol), bucket in events.items()
                             if event_date == d and event_id_required in bucket}
            candidate_symbols = event_symbols | set(positions)
        else:
            candidate_symbols = active_symbols | set(positions)
        day = day[day["symbol"].isin(candidate_symbols)]
        entry_mask = pd.Series(True, index=day.index, dtype=bool)
        rank_only = (
            str(record.signal_predicate.predicate_type).upper() == "RANK_ONLY"
            or str(record.signal_predicate.logic).upper() == "RANK_ONLY"
        )
        if not rank_only:
            for condition in (*record.signal_predicate.factor_conditions, *record.signal_predicate.interaction_conditions):
                value = pd.to_numeric(day[str(condition["factor_id"])], errors="coerce")
                target = float(condition["value"])
                operator = str(condition["operator"])
                if operator == "GT":
                    entry_mask &= value > target
                elif operator == "GE":
                    entry_mask &= value >= target
                elif operator == "LT":
                    entry_mask &= value < target
                elif operator == "LE":
                    entry_mask &= value <= target
                elif operator == "EQ":
                    entry_mask &= value == target
                elif operator == "NE":
                    entry_mask &= value != target
        day = day[entry_mask | day["symbol"].isin(set(positions))]
        rows: list[dict[str, Any]] = []
        factor_state: dict[str, dict[str, Any]] = {}
        for row in day.itertuples(index=False):
            symbol = str(row.symbol)
            tradable, status_reason = status_map.tradable(symbol, d)
            volume = float(row.volume) if pd.notna(row.volume) else 0.0
            position = positions.get(symbol)
            position_open = bool(position and position.quantity > 0)
            event_bucket = events.get((d, symbol), {})
            event = event_bucket.get(event_id_required) if event_id_required else None
            values = legacy.factor_row_values(row, factor_ids)
            row_payload = {
                "symbol": symbol,
                "generated_at": ensure_aware(ts),
                "available_at": ensure_aware(ts),
                "next_session_open": next_open,
                "universe_as_of": ensure_aware(ts),
                "universe_eligible": symbol in universe.get(d, set()),
                "tradable": bool(tradable and volume > 0),
                "affordable": True,
                "factor_values": values,
                "factor_available_at": {factor_id: row.available_at for factor_id in factor_ids},
                "position_open": position_open,
                "entry_session_index": (calendar_pos.get(int(position.opened_at.strftime("%Y%m%d"))) if position_open and position.opened_at is not None else None),
                "current_session_index": calendar_pos[d],
                "regime_label": regimes.get(d, "UNKNOWN"),
                "event_id": str(event["event_id"]) if event else "",
                "event_available_at": event["available_at_ts"] if event else None,
                "event_trade_date": legacy.ts_for_date(d, 15, 0) if event else None,
            }
            qualified_records.append(row_payload)
            rows.append(row_payload)
            factor_state[symbol] = {"values": values, "available_at": ensure_aware(row.available_at)}
            if status_reason != "PIT_STATUS_EXPLICIT_NORMAL_TRADING":
                stats[status_reason] += 1
        stats["rows_seen"] += len(rows)
        day_cache[d] = (rows, factor_state)
        return day_cache[d]

    def strategy_fn(_view: Any, ts: pd.Timestamp, d: int) -> list[Any]:
        if ts.hour != 15 or ts.minute != signal_minute or d not in date_to_next:
            return []
        rows, _ = build_day(d, ensure_aware(ts))
        compiled = compiler.emit_entry_signals(rows)
        signals = compiled_signals_to_engine_signals(compiled)
        stats["entry_signal_days"] += 1
        stats["entry_signals"] += len(signals)
        for signal, item in zip(signals, compiled):
            emitted_records.append({
                "generated_at": str(signal.generated_at),
                "symbol": signal.symbol,
                "action": "BUY",
                "score": signal.score,
                "reason": item.reason,
                "event_id": "",
                "source_factors": ",".join(item.source_factors),
            })
        return signals

    exit_evaluator = PortfolioExitEvaluatorV1(
        candidate_id=candidate.candidate_id,
        portfolio_id=candidate.candidate_id,
        contract={
            "exit_type": record.exit_predicate.exit_type,
            "fixed_holding_sessions": int(candidate.holding_period),
            "factor_conditions": list(record.exit_predicate.factor_conditions),
            "logic": record.exit_predicate.logic,
        },
    )

    def exit_fn(_view: Any, ts: pd.Timestamp, d: int, ledger: Any) -> list[Any]:
        if ts.hour != 15 or ts.minute != signal_minute or d not in calendar_pos:
            return []
        _, factor_state = build_day(d, ensure_aware(ts))
        return exit_evaluator.evaluate(ledger.lots.values(), d, calendar_pos[d], factor_state, ensure_aware(ts))

    result = engine.run(strategy_fn=strategy_fn, exit_fn=exit_fn)
    engine._sync_lot_contract_fields()
    metrics = compute_metrics_v3(result, float(policy.initial_cash if initial_cash is None else initial_cash), regimes, exec_calendar, int(candidate.holding_period))
    metrics.update({
        "candidate_id": candidate.candidate_id,
        "candidate_preregistration_hash": record.preregistration_hash,
        "trial_id": trial_id,
        "portfolio_id": portfolio_name,
        "signal_diagnostics": dict(stats),
        "engine_version": result.context.engine_version,
        "execution_model_version": result.context.execution_model_version,
        "source_identity": {"kind": "LOCAL_WORKTREE_SNAPSHOT", "historical_provenance": "UNVERIFIED",
            "corrected_sha256": legacy.sha256(Path(__file__)), "helper_sha256": legacy.sha256(Path(legacy.__file__))},
    })
    if write_evidence:
        evidence_root.mkdir(parents=True, exist_ok=True)
        write_csv(evidence_root / "entry_signals.csv", emitted_records,
                  ["generated_at", "symbol", "action", "score", "reason", "event_id", "source_factors"])
        write_csv(evidence_root / "orders.csv", [corrected_order_record(order) for order in result.orders.orders.values()],
                  ["order_id", "symbol", "side", "quantity", "filled_quantity", "status", "reason", "reason_code", "created_at", "strategy_id", "position_id", "lot_id", "signal_id", "intent_id"])
        write_csv(evidence_root / "intents.csv", [corrected_order_record(order) for order in result.orders.orders.values()],
                  ["order_id", "symbol", "side", "quantity", "filled_quantity", "status", "reason", "reason_code", "created_at", "strategy_id", "position_id", "lot_id", "signal_id", "intent_id"])
        write_csv(evidence_root / "fills.csv", [corrected_trade_record(trade) for trade in result.ledger.trades],
                  ["trade_id", "symbol", "side", "quantity", "price", "fee", "fill_time", "realized_pnl", "reality_flag", "position_id", "lot_id", "order_id"])
        write_csv(evidence_root / "trades.csv", [corrected_trade_record(trade) for trade in result.ledger.valid_trades if trade.side == Side.SELL],
                  ["trade_id", "symbol", "side", "quantity", "price", "fee", "fill_time", "realized_pnl", "reality_flag", "position_id", "lot_id", "order_id"])
        write_csv(evidence_root / "equity.csv", [corrected_snapshot_record(snap, regimes) for snap in result.ledger.snapshots],
                  ["timestamp", "equity", "cash", "market_value", "positions", "turnover", "regime", "realized_pnl", "unrealized_pnl"])
        write_csv(evidence_root / "entry_qualified_rows.csv", qualified_records,
                  ["symbol", "generated_at", "available_at", "next_session_open", "universe_as_of", "universe_eligible", "tradable", "affordable", "factor_values", "factor_available_at", "position_open", "entry_session_index", "current_session_index", "regime_label", "event_id", "event_available_at", "event_trade_date"])
        exit_events = [event for event in result.event_log if event.event_type == "EXIT_DECISION"]
        write_csv(evidence_root / "exit_decisions.csv", [
            {"event_id": event.event_id, "timestamp": str(event.timestamp), "symbol": event.symbol, "lot_id": event.lot_id,
             "strategy_id": event.strategy_id, **event.payload}
            for event in exit_events
        ], ["event_id", "timestamp", "symbol", "lot_id", "strategy_id", "candidate_id", "state", "reason_code", "trade_session", "trade_session_index"])
        write_csv(evidence_root / "order_events.csv", [
            {"event_id": event.event_id, "timestamp": str(event.timestamp), "event_type": event.event_type,
             "strategy_id": event.strategy_id, "symbol": event.symbol, "signal_id": event.signal_id,
             "intent_id": event.intent_id, "order_id": event.order_id, "position_id": event.position_id,
             "lot_id": event.lot_id, "status": getattr(event.order_status, "value", ""), "message": getattr(event, "message", "")}
            for event in result.event_log if event.event_type == "ORDER_EVENT"
        ], ["event_id", "timestamp", "event_type", "strategy_id", "symbol", "signal_id", "intent_id", "order_id", "position_id", "lot_id", "status", "message"])
        write_csv(evidence_root / "lot_lifecycle.csv", [
            {"lot_id": lot.lot_id, "symbol": lot.symbol, "strategy_id": lot.strategy_id, "entry_session": lot.entry_session,
             "entry_session_index": lot.entry_session_index, "entry_price": lot.entry_price, "quantity": lot.quantity,
             "remaining_quantity": lot.remaining_quantity, "sellable_from_session": lot.sellable_from_session,
             "exit_due_session": lot.exit_due_session, "exit_due_index": lot.exit_due_index,
             "exit_state": lot.exit_state, "exit_reason": lot.exit_reason}
            for lot in result.ledger.lots.values()
        ], ["lot_id", "symbol", "strategy_id", "entry_session", "entry_session_index", "entry_price", "quantity", "remaining_quantity", "sellable_from_session", "exit_due_session", "exit_due_index", "exit_state", "exit_reason"])
        write_csv(evidence_root / "rejection_reasons.csv", [
            {"order_id": event.order_id, "lot_id": event.lot_id, "symbol": event.symbol, "session": int(event.timestamp.strftime("%Y%m%d")), "reason_code": event.message}
            for event in result.event_log if event.event_type == "ORDER_EVENT" and getattr(event.order_status, "value", "") == "REJECTED"
        ], ["order_id", "lot_id", "symbol", "session", "reason_code"])
        write_json(evidence_root / "metrics.json", metrics)
        metrics["evidence_dir"] = str(evidence_root)
    return result, metrics, {"entry_signals": emitted_records, "qualified_rows": qualified_records}


def bootstrap_result(pnl: list[float], iterations: int, seed: int) -> dict[str, Any]:
    if not pnl:
        return {"status": "NOT_APPLICABLE", "iterations": iterations, "seed": seed, "p_value": 1.0}
    values = np.asarray(pnl, dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.choice(values, size=(iterations, len(values)), replace=True).sum(axis=1)
    return {
        "status": "COMPLETE",
        "iterations": iterations,
        "seed": seed,
        "mean_pnl": float(values.mean()),
        "probability_positive": float(np.mean(draws > 0)),
        "p_value": float(np.mean(draws <= 0)),
        "pnl_quantiles": {str(q): float(np.quantile(draws, q)) for q in (0.025, 0.5, 0.975)},
    }


def concentration(result: Any, initial_cash: float) -> dict[str, Any]:
    pnl = sorted([float(trade.realized_pnl) for trade in result.ledger.valid_trades if trade.side == Side.SELL], reverse=True)
    positive = [value for value in pnl if value > 0]
    gross_positive = sum(positive)
    total = sum(pnl)
    return {
        "closed_trade_count": len(pnl),
        "total_realized_pnl": total,
        "positive_pnl": gross_positive,
        "top_positive_pnl_share": {str(n): (sum(positive[:n]) / gross_positive if gross_positive else None) for n in (1, 5, 10)},
        "return_after_removing_top_1_trade": (total - sum(positive[:1])) / initial_cash,
        "return_after_removing_top_5_trades": (total - sum(positive[:5])) / initial_cash,
        "return_after_removing_top_10_trades": (total - sum(positive[:10])) / initial_cash,
        "method": "corrected engine closed-trade PnL decomposition; no selection or optimization",
    }


def subperiods(result: Any, initial_cash: float) -> dict[str, Any]:
    periods = {"P1": (20220801, 20230731), "P2": (20230801, 20240731), "P3": (20240801, 20250731)}
    output: dict[str, Any] = {}
    for name, (start, end) in periods.items():
        snaps = [snap for snap in result.ledger.snapshots if start <= int(snap.timestamp.strftime("%Y%m%d")) <= end]
        sells = [trade for trade in result.ledger.valid_trades if trade.side == Side.SELL and start <= int(trade.fill_time.strftime("%Y%m%d")) <= end]
        start_equity = float(snaps[0].equity) if snaps else None
        end_equity = float(snaps[-1].equity) if snaps else None
        output[name] = {
            "start_equity": start_equity,
            "end_equity": end_equity,
            "net_return": end_equity / start_equity - 1.0 if start_equity else None,
            "closed_trade_count": len(sells),
            "realized_pnl": sum(float(trade.realized_pnl) for trade in sells),
        }
    return output


def regime_split(result: Any, regimes: Mapping[int, str]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for regime in ("BULL", "BEAR", "UNKNOWN"):
        trades = [trade for trade in result.ledger.valid_trades if trade.side == Side.SELL and regimes.get(int(trade.fill_time.strftime("%Y%m%d")), "UNKNOWN") == regime]
        snapshots = [snap for snap in result.ledger.snapshots if regimes.get(int(snap.timestamp.strftime("%Y%m%d")), "UNKNOWN") == regime]
        output[regime] = {"snapshot_count": len(snapshots), "closed_trade_count": len(trades), "realized_pnl": sum(float(trade.realized_pnl) for trade in trades)}
    return output


def classification(result: Mapping[str, Any], bootstrap: Mapping[str, Any], cost: Mapping[str, Any]) -> str:
    if result.get("invariant_errors") or result.get("certification_status") == "INVALID":
        return "INVALID"
    if int(result.get("closed_trade_count", 0)) < 30 or bootstrap.get("status") != "COMPLETE":
        return "INSUFFICIENT_EVIDENCE"
    combined = cost.get("COMBINED_X2", {}).get("net_return")
    if float(result.get("net_return", 0.0)) <= 0 or (result.get("profit_factor") is not None and float(result["profit_factor"]) <= 1.0) or combined is None or float(combined) <= 0:
        return "REJECTED"
    if float(bootstrap.get("p_value", 1.0)) >= 0.05:
        return "WEAK"
    return "RESEARCH_PASSED"


def main_run(*args, **kwargs):
    raise RuntimeError("HISTORICAL_EXECUTION_DISABLED:main_run")


def main(*args, **kwargs):
    raise RuntimeError("HISTORICAL_EXECUTION_DISABLED:main")


def invalidate_old_run(*args, **kwargs):
    raise RuntimeError("HISTORICAL_EXECUTION_DISABLED:invalidate_old_run")


def create_corrected_trial_registry(*args, **kwargs):
    raise RuntimeError("HISTORICAL_EXECUTION_DISABLED:create_corrected_trial_registry")


if __name__ == "__main__":
    raise SystemExit(main())
