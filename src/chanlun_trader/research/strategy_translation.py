"""Mechanism-specific strategy translation helpers.

This module is deliberately scoped to TRAIN (2022-08-01 through 2024-07-31).
All parquet reads pass through :class:`ResearchDataAccessGuard` before any
research calculation is performed.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from chanlun_trader.engine.asof import MarketDataStore
from chanlun_trader.engine.corporate_action import CorporateActionGuard
from chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from chanlun_trader.engine.signal import ExecutionPolicy, Signal, Side
from chanlun_trader.engine.time_types import tz_aware
from chanlun_trader.research.event import EventRegistry
from chanlun_trader.research.guard import ResearchDataAccessGuard
from chanlun_trader.research.io_safety import GuardedResearchReader
from chanlun_trader.research.strategy import StrategyDefinition


TRAIN_START = 20220801
TRAIN_END = 20240731
FINAL_TEST_START = 20250801
PROMISING_IDS = ["A4-301", "A4-302", "A4-303", "A4-304", "A4-307", "A4-308", "A4-012", "A4-014"]
SUPPORTED_DAILY_IDS = [
    "A4-001", "A4-002", "A4-004", "A4-005", "A4-006", "A4-008", "A4-009",
    "A4-011", "A4-015", "A4-016", "A4-113", "A4-115", "A4-214", "A4-305",
]
EVENT_CONFIG = {
    "A4-301": {"event_id": "E_LIMITUP_SEAL", "holding": 3, "translation": "EXACT_TRANSLATION"},
    "A4-302": {"event_id": "E_FAILEDLIMIT", "holding": 3, "translation": "PARTIAL_TRANSLATION"},
    "A4-303": {"event_id": "E_LHB_INSTPOS", "holding": 3, "translation": "EXACT_TRANSLATION"},
    "A4-304": {"event_id": "E_LHB_BROKER", "holding": 5, "translation": "PARTIAL_TRANSLATION"},
    "A4-307": {"event_id": "E_LIMITUP", "holding": 3, "translation": "PARTIAL_TRANSLATION"},
    "A4-308": {"event_id": "E_CONSEC_LIMIT", "holding": 5, "translation": "PARTIAL_TRANSLATION"},
}
DAILY_CONFIG = {
    "A4-012": {"factor": "rev20_lowvolcomp", "holding": 5},
    "A4-014": {"factor": "R2_REV20_NOLIMIT", "holding": 5},
}


@dataclass(frozen=True)
class TranslationConfig:
    train_start: int = TRAIN_START
    train_end: int = TRAIN_END
    initial_cash: float = 10_000_000.0
    max_positions: int = 10
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    slippage_bps: float = 0.001
    engine_version: str = "BT_ENGINE_V2"
    data_version: str = "TRAIN_20220801_20240731"
    universe: str = "TRAIN_DAILY_RAW_EVENT_OR_FACTOR_ACTIVE"


def canonical_hash(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def guarded_read_parquet(path: str | Path, guard: ResearchDataAccessGuard,
                         date_columns: Iterable[str] = ()) -> pd.DataFrame:
    cols = list(date_columns)
    reader = GuardedResearchReader(guard)
    if not cols:
        return reader.read_parquet(path, date_column=None)
    return reader.read_parquet(path, date_column=cols[0], date_columns=cols)


def load_train_daily(cfg: TranslationConfig, guard: ResearchDataAccessGuard) -> pd.DataFrame:
    df = guarded_read_parquet("data/research/daily_all.parquet", guard, ["date"])
    df = df[(df["date"] >= cfg.train_start) & (df["date"] <= cfg.train_end)].copy()
    df["date"] = df["date"].astype(int)
    return df


def load_train_features(cfg: TranslationConfig, guard: ResearchDataAccessGuard) -> pd.DataFrame:
    df = guarded_read_parquet(
        "data/research/robustification_results/a4_feature_train.parquet", guard, ["date"]
    )
    df = df[(df["date"] >= cfg.train_start) & (df["date"] <= cfg.train_end)].copy()
    df["date"] = df["date"].astype(int)
    df["rev20_lowvolcomp"] = -df["r20"] * (df["vol_comp"] < 1.0).astype(float)
    return df


def load_event(event_id: str, cfg: TranslationConfig, guard: ResearchDataAccessGuard) -> pd.DataFrame:
    df = guarded_read_parquet(
        f"data/research/event_store/{event_id}_v1.parquet", guard, ["event_time", "available_at"]
    )
    df = df[(df["event_time"] >= cfg.train_start) & (df["event_time"] <= cfg.train_end)].copy()
    df["event_time"] = df["event_time"].astype(int)
    df["available_at"] = df["available_at"].astype(int)
    return df.sort_values(["event_time", "symbol"]).reset_index(drop=True)


def trading_calendar(daily: pd.DataFrame) -> list[int]:
    return sorted(daily["date"].drop_duplicates().astype(int).tolist())


def _session_pos(calendar: list[int]) -> dict[int, int]:
    return {d: i for i, d in enumerate(calendar)}


def _next_session(calendar: list[int], date: int, offset: int = 1) -> int | None:
    pos = _session_pos(calendar).get(int(date))
    if pos is None or pos + offset >= len(calendar):
        return None
    return int(calendar[pos + offset])


def build_market_store(daily: pd.DataFrame, symbols: Iterable[str]) -> MarketDataStore:
    symbols = set(symbols)
    store = MarketDataStore()
    cols = ["open", "high", "low", "close", "volume", "amount", "prev_close"]
    for symbol, group in daily[daily["symbol"].isin(symbols)].groupby("symbol", sort=False):
        frame = group.sort_values("date").set_index("date")[cols]
        store.add_daily_raw(str(symbol), frame)
    return store


def _series_by_symbol(daily: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {str(s): g.sort_values("date").reset_index(drop=True) for s, g in daily.groupby("symbol", sort=False)}


def _candidate_path(row: dict, daily_by_symbol: dict[str, pd.DataFrame], calendar: list[int], holding: int) -> dict | None:
    symbol = str(row["symbol"])
    frame = daily_by_symbol.get(symbol)
    if frame is None:
        return None
    dates = frame["date"].to_numpy(dtype=int)
    event_date = int(row["event_time"])
    idxs = np.flatnonzero(dates == event_date)
    if len(idxs) == 0:
        return None
    event_idx = int(idxs[0])
    signal_date = int(row.get("signal_date", event_date))
    signal_idxs = np.flatnonzero(dates == signal_date)
    if len(signal_idxs) == 0:
        return None
    signal_idx = int(signal_idxs[0])
    entry_idx = signal_idx + 1
    exit_idx = entry_idx + holding - 1
    if exit_idx >= len(frame):
        return None
    entry = float(frame.iloc[entry_idx]["open"])
    if entry <= 0:
        return None
    window = frame.iloc[entry_idx:exit_idx + 1]
    exit_close = float(window.iloc[-1]["close"])
    if exit_close <= 0:
        return None
    return {
        **row,
        "event_idx": event_idx,
        "signal_idx": signal_idx,
        "entry_idx": entry_idx,
        "exit_idx": exit_idx,
        "entry_date": int(frame.iloc[entry_idx]["date"]),
        "exit_date": int(frame.iloc[exit_idx]["date"]),
        "entry_open": entry,
        "exit_close": exit_close,
        "gross_return": exit_close / entry - 1.0,
        "mfe": float(window["high"].max() / entry - 1.0),
        "mae": float(window["low"].min() / entry - 1.0),
    }


def _limit_dates(daily: pd.DataFrame, cfg: TranslationConfig, guard: ResearchDataAccessGuard) -> dict[str, list[int]]:
    lim = guarded_read_parquet("data/tdx/clean/limit_events.parquet", guard, ["event_date", "available_at"])
    lim = lim[(lim["event_date"] >= cfg.train_start) & (lim["event_date"] <= cfg.train_end)]
    return {
        str(symbol): sorted(group["event_date"].astype(int).tolist())
        for symbol, group in lim.groupby("code", sort=False)
    }


def _has_prior_limit(symbol: str, date: int, limit_dates: dict[str, list[int]], calendar: list[int], window: int = 5) -> bool:
    pos = _session_pos(calendar).get(int(date))
    if pos is None:
        return False
    prior = set(calendar[max(0, pos - window):pos])
    return any(d in prior for d in limit_dates.get(symbol, []))


def prepare_event_candidates(hypothesis_id: str, events: pd.DataFrame, daily: pd.DataFrame,
                             cfg: TranslationConfig, guard: ResearchDataAccessGuard) -> tuple[list[dict], dict]:
    calendar = trading_calendar(daily)
    daily_by_symbol = _series_by_symbol(daily)
    limit_dates = _limit_dates(daily, cfg, guard) if hypothesis_id in {"A4-303", "A4-308"} else {}
    holding = EVENT_CONFIG[hypothesis_id]["holding"]
    raw: list[dict] = []
    excluded: dict[str, int] = {}
    for item in events.to_dict("records"):
        item["symbol"] = str(item["symbol"])
        item["event_time"] = int(item["event_time"])
        item["signal_date"] = item["event_time"]
        frame = daily_by_symbol.get(item["symbol"])
        if frame is None:
            excluded["NO_DAILY_BAR"] = excluded.get("NO_DAILY_BAR", 0) + 1
            continue
        dates = frame["date"].to_numpy(dtype=int)
        event_idx = np.flatnonzero(dates == item["event_time"])
        if len(event_idx) == 0:
            excluded["NO_EVENT_BAR"] = excluded.get("NO_EVENT_BAR", 0) + 1
            continue
        event_idx = int(event_idx[0])

        if hypothesis_id == "A4-303" and _has_prior_limit(item["symbol"], item["event_time"], limit_dates, calendar):
            excluded["NON_CONSECUTIVE_FILTER"] = excluded.get("NON_CONSECUTIVE_FILTER", 0) + 1
            continue
        if hypothesis_id == "A4-304":
            next_idx = event_idx + 1
            if next_idx >= len(frame):
                excluded["NO_T1_OPEN"] = excluded.get("NO_T1_OPEN", 0) + 1
                continue
            event_close = float(frame.iloc[event_idx]["close"])
            next_open = float(frame.iloc[next_idx]["open"])
            if event_close <= 0 or next_open <= event_close:
                excluded["NO_T1_HIGH_OPEN"] = excluded.get("NO_T1_HIGH_OPEN", 0) + 1
                continue
            item["signal_date"] = int(frame.iloc[next_idx]["date"])
            item["availability_note"] = "T+1_OPEN_CONFIRMED_AT_T1_CLOSE"
        if hypothesis_id == "A4-308":
            prior = [d for d in limit_dates.get(item["symbol"], []) if d < item["event_time"]]
            prior = [d for d in prior if _has_prior_limit(item["symbol"], item["event_time"], {item["symbol"]: [d]}, calendar)]
            if not prior:
                excluded["NO_PRIOR_LIMIT_REFERENCE"] = excluded.get("NO_PRIOR_LIMIT_REFERENCE", 0) + 1
                continue
            prior_date = max(prior)
            prev_rows = frame[frame["date"] == prior_date]
            cur_rows = frame[frame["date"] == item["event_time"]]
            if prev_rows.empty or cur_rows.empty or float(cur_rows.iloc[0]["volume"]) > float(prev_rows.iloc[0]["volume"]):
                excluded["NO_VOLUME_DISPOSITION"] = excluded.get("NO_VOLUME_DISPOSITION", 0) + 1
                continue
        candidate = _candidate_path(item, daily_by_symbol, calendar, holding)
        if candidate is None:
            excluded["EDGE_OF_TRAIN_WINDOW"] = excluded.get("EDGE_OF_TRAIN_WINDOW", 0) + 1
            continue
        raw.append(candidate)

    # One open position per symbol: overlapping events do not create synthetic top-ups.
    kept: list[dict] = []
    last_exit: dict[str, int] = {}
    for candidate in sorted(raw, key=lambda x: (x["signal_date"], x["symbol"], x["event_time"])):
        if candidate["signal_date"] <= last_exit.get(candidate["symbol"], -1):
            excluded["OVERLAP_DEDUP"] = excluded.get("OVERLAP_DEDUP", 0) + 1
            continue
        last_exit[candidate["symbol"]] = int(candidate["exit_date"])
        candidate["candidate_id"] = f"{hypothesis_id}:{candidate['symbol']}:{candidate['event_time']}"
        kept.append(candidate)
    return kept, {"raw_count": len(raw), "kept_count": len(kept), "excluded": excluded}


def build_event_signals(strategy_id: str, candidates: list[dict]) -> list[Signal]:
    signals: list[Signal] = []
    for candidate in candidates:
        event_date = int(candidate["event_time"])
        signal_date = int(candidate["signal_date"])
        exit_date = int(candidate["exit_date"])
        symbol = str(candidate["symbol"])
        cid = candidate["candidate_id"]
        signals.append(Signal(
            strategy_id=strategy_id,
            signal_id=f"{cid}:BUY",
            symbol=symbol,
            generated_at=tz_aware(signal_date // 10000, (signal_date // 100) % 100, signal_date % 100, 15, 0),
            direction=Side.BUY,
            score=0.0,
            signal_type="EVENT_ENTRY",
            execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
            metadata={"event_time": event_date, "candidate_id": cid, "available_at": signal_date},
            source_event_ids=[cid],
        ))
        signals.append(Signal(
            strategy_id=strategy_id,
            signal_id=f"{cid}:SELL",
            symbol=symbol,
            generated_at=tz_aware(exit_date // 10000, (exit_date // 100) % 100, exit_date % 100, 15, 0),
            direction=Side.SELL,
            score=1.0,
            signal_type="SCHEDULED_EXIT",
            execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
            metadata={"event_time": event_date, "candidate_id": cid, "available_at": exit_date},
            source_event_ids=[cid],
        ))
    return signals


def build_daily_signals(strategy_id: str, hypothesis_id: str, features: pd.DataFrame,
                        daily: pd.DataFrame, cfg: TranslationConfig) -> tuple[list[dict], list[Signal]]:
    holding = DAILY_CONFIG[hypothesis_id]["holding"]
    factor = DAILY_CONFIG[hypothesis_id]["factor"]
    ft = features.copy()
    if factor not in ft.columns:
        raise KeyError(f"missing factor {factor}")
    ft = ft[["symbol", "date", factor]].rename(columns={factor: "value"}).dropna()
    ft = ft[np.isfinite(ft["value"]) & (ft["value"] > 0)]
    calendar = trading_calendar(daily)
    daily_by_symbol = _series_by_symbol(daily)
    candidates: list[dict] = []
    for date, group in ft.groupby("date", sort=True):
        group = group.sort_values(["value", "symbol"], ascending=[False, True]).head(cfg.max_positions)
        for row in group.to_dict("records"):
            row["symbol"] = str(row["symbol"])
            row["event_time"] = int(date)
            row["signal_date"] = int(date)
            candidate = _candidate_path(row, daily_by_symbol, calendar, holding)
            if candidate is not None:
                candidate["candidate_id"] = f"{hypothesis_id}:{candidate['symbol']}:{candidate['event_time']}"
                candidates.append(candidate)
    # Keep one position per symbol during its scheduled holding interval.
    kept: list[dict] = []
    last_exit: dict[str, int] = {}
    for candidate in sorted(candidates, key=lambda x: (x["signal_date"], x["symbol"])):
        if candidate["signal_date"] <= last_exit.get(candidate["symbol"], -1):
            continue
        last_exit[candidate["symbol"]] = int(candidate["exit_date"])
        kept.append(candidate)
    return kept, build_event_signals(strategy_id, kept)


def run_engine(store: MarketDataStore, calendar: list[int], signals: list[Signal], cfg: TranslationConfig,
               cost_mult: float = 1.0, slip_mult: float = 1.0, delay_sessions: int = 0) -> object:
    if delay_sessions:
        pos = _session_pos(calendar)
        shifted: list[Signal] = []
        for signal in signals:
            d = int(signal.generated_at.strftime("%Y%m%d"))
            p = pos.get(d)
            if p is None or p + delay_sessions >= len(calendar):
                continue
            nd = calendar[p + delay_sessions]
            shifted.append(Signal(
                strategy_id=signal.strategy_id,
                signal_id=f"{signal.signal_id}:DELAY{delay_sessions}",
                symbol=signal.symbol,
                generated_at=tz_aware(nd // 10000, (nd // 100) % 100, nd % 100, 15, 0),
                direction=signal.direction,
                score=signal.score,
                signal_type=signal.signal_type,
                execution_policy=signal.execution_policy,
                metadata={**signal.metadata, "delay_sessions": delay_sessions},
                source_event_ids=signal.source_event_ids,
            ))
        signals = shifted
    engine_cfg = EngineConfig(
        initial_cash=cfg.initial_cash,
        max_positions=cfg.max_positions,
        max_position_weight=1.0 / cfg.max_positions,
        commission_rate=cfg.commission_rate * cost_mult,
        min_commission=cfg.min_commission * cost_mult,
        stamp_tax_rate=cfg.stamp_tax_rate * cost_mult,
        slippage_bps=cfg.slippage_bps * slip_mult,
        max_holding_days=0,
        mode="DAILY",
        enable_index_filter=False,
        index_filter_enabled=False,
        start_date=cfg.train_start,
        end_date=cfg.train_end,
        corporate_action_guard=CorporateActionGuard(),
    )
    engine = BacktestEngineV2(store, calendar, config=engine_cfg)
    engine.add_signals(signals)
    return engine.run()


def _closed_trade_returns(result) -> list[dict]:
    orders = result.orders.orders
    buys: dict[tuple[str, str], list[dict]] = {}
    out: list[dict] = []
    for trade in result.ledger.trades:
        if trade.side == Side.BUY:
            buys.setdefault((trade.strategy_id, trade.symbol), []).append({
                "quantity": int(trade.quantity),
                "price": float(trade.price),
                "fill_time": trade.fill_time,
            })
            continue
        queue = buys.get((trade.strategy_id, trade.symbol), [])
        if not queue:
            continue
        buy = queue[0]
        qty = min(int(trade.quantity), int(buy["quantity"]))
        gross_pnl = (float(trade.price) - float(buy["price"])) * qty
        base = float(buy["price"]) * qty
        signal_id = orders.get(trade.order_id).signal_id if trade.order_id in orders else ""
        out.append({
            "trade_id": trade.trade_id,
            "symbol": trade.symbol,
            "buy_date": int(buy["fill_time"].strftime("%Y%m%d")),
            "sell_date": int(trade.fill_time.strftime("%Y%m%d")),
            "gross_pnl": gross_pnl,
            "net_pnl": float(trade.realized_pnl),
            "gross_return": gross_pnl / base if base else 0.0,
            "net_return": float(trade.realized_pnl) / base if base else 0.0,
            "signal_id": signal_id,
            "reality_flag": trade.reality_flag,
        })
        if qty >= buy["quantity"]:
            queue.pop(0)
        else:
            buy["quantity"] -= qty
    return out


def engine_metrics(result, cfg: TranslationConfig) -> dict:
    snaps = result.ledger.snapshots
    equity = pd.Series([float(s.equity) for s in snaps]) if snaps else pd.Series(dtype=float)
    daily_ret = equity.pct_change().dropna()
    sells = [t for t in result.ledger.trades if t.side == Side.SELL]
    wins = [float(t.realized_pnl) for t in sells if t.realized_pnl > 0]
    losses = [float(t.realized_pnl) for t in sells if t.realized_pnl <= 0]
    pf = float(sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else (float("inf") if wins else 0.0)
    closed = _closed_trade_returns(result)
    return {
        "gross_total_return": None,
        "net_total_return": float(equity.iloc[-1] / cfg.initial_cash - 1.0) if len(equity) else 0.0,
        "max_drawdown": float((equity / equity.cummax() - 1.0).min()) if len(equity) else 0.0,
        "sharpe": float(np.sqrt(252.0) * daily_ret.mean() / daily_ret.std(ddof=1)) if len(daily_ret) > 1 and daily_ret.std(ddof=1) > 0 else 0.0,
        "profit_factor": pf,
        "win_rate": float(len(wins) / len(sells)) if sells else 0.0,
        "trade_count": len(sells),
        "closed_trade_count": len(closed),
        "total_fees": float(result.ledger.total_fees),
        "turnover": float(result.ledger.turnover),
        "open_positions": sum(1 for p in result.ledger.positions.values() if p.quantity > 0),
        "order_count": len(result.orders.orders),
        "fill_count": len(result.ledger.trades),
        "rejected_order_count": sum(1 for o in result.orders.orders.values() if str(o.status) in {"OrderStatus.REJECTED", "OrderStatus.EXPIRED"}),
        "rejection_reasons": sorted({str(o.reason) for o in result.orders.orders.values() if str(o.reason)}),
        "invariants": result.ledger.check_invariants(),
        "closed_trades": closed,
    }


def run_variants(store: MarketDataStore, calendar: list[int], signals: list[Signal], cfg: TranslationConfig) -> tuple[object, dict]:
    base = run_engine(store, calendar, signals, cfg)
    no_slip = run_engine(store, calendar, signals, cfg, slip_mult=0.0)
    no_cost = run_engine(store, calendar, signals, TranslationConfig(**{**asdict(cfg), "commission_rate": 0.0, "min_commission": 0.0, "stamp_tax_rate": 0.0}), slip_mult=0.0)
    base_m = engine_metrics(base, cfg)
    no_slip_m = engine_metrics(no_slip, cfg)
    no_cost_m = engine_metrics(no_cost, cfg)
    base_closed = base_m.pop("closed_trades")
    no_slip_m.pop("closed_trades")
    no_cost_m.pop("closed_trades")
    base_m["gross_total_return"] = no_cost_m["net_total_return"]
    base_m["slippage_drag"] = no_slip_m["net_total_return"] - base_m["net_total_return"]
    base_m["cost_drag"] = no_cost_m["net_total_return"] - no_slip_m["net_total_return"]
    base_m["gross_edge"] = float(np.mean([x["gross_return"] for x in base_closed])) if base_closed else 0.0
    base_m["net_edge"] = float(np.mean([x["net_return"] for x in base_closed])) if base_closed else 0.0
    base_m["closed_trades"] = base_closed
    return base, base_m


def bootstrap_summary(values: Iterable[float], seed: int = 42, draws: int = 200,
                      min_values: int = 20, min_blocks: int = 3) -> dict:
    arr = np.asarray([float(x) for x in values if x is not None and np.isfinite(x)], dtype=float)
    if len(arr) < min_values:
        return {"bootstrap_status": "BOOTSTRAP_UNAVAILABLE_SAMPLE_TOO_SMALL"}
    blocks = []
    n = max(1, int(math.ceil(len(arr) / min_blocks)))
    for i in range(0, len(arr), n):
        blocks.append(arr[i:i + n])
    if len(blocks) < min_blocks:
        return {"bootstrap_status": "BOOTSTRAP_UNAVAILABLE_SAMPLE_TOO_SMALL"}
    rng = np.random.default_rng(seed)
    means = []
    medians = []
    for _ in range(draws):
        sample = np.concatenate([blocks[int(rng.integers(0, len(blocks)))] for _ in blocks])[:len(arr)]
        means.append(float(np.mean(sample)))
        medians.append(float(np.median(sample)))
    return {
        "bootstrap_status": "PASS",
        "bootstrap_mean": float(np.mean(means)),
        "bootstrap_median": float(np.median(medians)),
        "bootstrap_ci_2_5": float(np.percentile(means, 2.5)),
        "bootstrap_ci_97_5": float(np.percentile(means, 97.5)),
        "probability_positive": float(np.mean(np.asarray(means) > 0)),
    }


def concentration_summary(closed_trades: list[dict], candidates: list[dict]) -> dict:
    if not closed_trades:
        return {"top1_contribution": None, "top3_contribution": None, "top5_contribution": None,
                "top10_contribution": None, "best_month_contribution": None, "event_date_hhi": None}
    pnl = np.asarray([float(x["net_pnl"]) for x in closed_trades])
    total = float(pnl.sum())
    result = {}
    for n in (1, 3, 5, 10):
        result[f"top{n}_contribution"] = float(np.sort(pnl)[::-1][:n].sum() / total) if total else None
    by_month = pd.Series(pnl, index=[str(x["sell_date"])[:6] for x in closed_trades]).groupby(level=0).sum()
    result["best_month_contribution"] = float(by_month.max() / total) if total else None
    event_dates = [str(c.get("event_time")) for c in candidates]
    counts = pd.Series(event_dates).value_counts() if event_dates else pd.Series(dtype=float)
    probs = counts / counts.sum() if len(counts) else counts
    result["event_date_hhi"] = float((probs ** 2).sum()) if len(probs) else None
    result["best_sector_contribution"] = None
    result["sector_hhi"] = None
    result["sector_status"] = "DATA_LIMITED_PIT_INDUSTRY_HISTORY_UNAVAILABLE"
    result["extreme_cluster_status"] = "OUTSIDE_TRAIN_WINDOW_NOT_READ"
    return result


def placebo_summary(candidates: list[dict], daily: pd.DataFrame, seed: int = 42) -> dict:
    if not candidates:
        return {"same_day_random_mean": None, "market_matched_mean": None, "liquidity_matched_mean": None,
                "sector_matched_mean": None, "sector_matched_status": "DATA_LIMITED_PIT_INDUSTRY_HISTORY_UNAVAILABLE"}
    by_date = {int(d): g for d, g in daily.groupby("date")}
    rng = np.random.default_rng(seed)
    random_returns = []
    market_returns = []
    liq_returns = []
    for c in candidates:
        group = by_date.get(int(c["signal_date"]))
        if group is None or group.empty:
            continue
        valid = group[group["open"] > 0]
        if valid.empty:
            continue
        # Same-day random and market-matched controls share the event's executable window.
        pick = valid.iloc[int(rng.integers(0, len(valid)))]
        pick_frame = daily[(daily["symbol"] == pick["symbol"]) & (daily["date"] >= c["entry_date"]) & (daily["date"] <= c["exit_date"])]
        if len(pick_frame) >= 1:
            first_open = float(pick_frame.iloc[0]["open"])
            last_close = float(pick_frame.iloc[-1]["close"])
            if first_open > 0:
                random_returns.append(last_close / first_open - 1.0)
        same = []
        for symbol, g in daily.groupby("symbol", sort=False):
            g = g.sort_values("date")
            start = g[g["date"] == c["entry_date"]]
            end = g[g["date"] == c["exit_date"]]
            if not start.empty and not end.empty and float(start.iloc[0]["open"]) > 0:
                same.append(float(end.iloc[0]["close"]) / float(start.iloc[0]["open"]) - 1.0)
        if same:
            market_returns.append(float(np.mean(same)))
        if "amount" in valid.columns:
            target = float(group[group["symbol"] == c["symbol"]]["amount"].median()) if c["symbol"] in set(group["symbol"]) else float(valid["amount"].median())
            liq = valid.iloc[(valid["amount"] - target).abs().argsort()[:1]]
            if not liq.empty:
                sym = liq.iloc[0]["symbol"]
                g = daily[daily["symbol"] == sym]
                start = g[g["date"] == c["entry_date"]]
                end = g[g["date"] == c["exit_date"]]
                if not start.empty and not end.empty and float(start.iloc[0]["open"]) > 0:
                    liq_returns.append(float(end.iloc[0]["close"]) / float(start.iloc[0]["open"]) - 1.0)
    mean = lambda x: float(np.mean(x)) if x else None
    return {
        "same_day_random_mean": mean(random_returns),
        "market_matched_mean": mean(market_returns),
        "liquidity_matched_mean": mean(liq_returns),
        "sector_matched_mean": None,
        "sector_matched_status": "DATA_LIMITED_PIT_INDUSTRY_HISTORY_UNAVAILABLE",
    }


def make_strategy_definition(strategy_id: str, hypothesis_id: str, mechanism_id: str, source_seed_id: str,
                             signal_definition: dict, event_definition: dict, holding: int,
                             entry_policy: str = "NEXT_SESSION_OPEN") -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id=strategy_id,
        version="v1",
        hypothesis_id=hypothesis_id,
        parent_hypothesis_id=hypothesis_id,
        mechanism_id=mechanism_id,
        source_seed_id=source_seed_id,
        signal_definition_hash=canonical_hash(signal_definition),
        event_definition_hash=canonical_hash(event_definition),
        factors=signal_definition.get("factors", []),
        events=event_definition.get("events", []),
        universe="TRAIN_PIT_DAILY_RAW",
        ranking="frozen_event_quality_then_symbol_asc",
        ranking_policy="frozen_event_quality_then_symbol_asc",
        position_count_policy="up_to_10_real_candidates_no_fill_padding",
        entry=entry_policy,
        available_at_rule=event_definition.get("available_at", "T_CLOSE"),
        holding=holding,
        holding_policy=f"natural_horizon_{holding}D_from_A4_decay_plateau",
        exit="SCHEDULED_NEXT_SESSION_OPEN",
        exit_policy="signal_at_horizon_close_then_next_session_open",
        execution_policy=entry_policy,
        risk="A_SHARE_T1_PRICE_LIMIT_SUSPENSION_CORPORATE_ACTION_GUARD",
        risk_policy="A_SHARE_T1_PRICE_LIMIT_SUSPENSION_CORPORATE_ACTION_GUARD",
        capital_assumption="10M_CNY_BASELINE",
        cost_model="commission_2.5bp_min5_stamp_5bp_sell",
        slippage_model="fixed_10bp_each_side",
        engine_version="BT_ENGINE_V2",
        data_version="TRAIN_RAW_20220801_20240731",
    )
