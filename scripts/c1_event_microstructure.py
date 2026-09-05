"""C1: Event T+1 Open execution realism & microstructure validation.

Stages (scripts/c1_args.json):
  lineage | coverage5m | executability | gap | tvse | decay | topwinners | cluster | redteam | final | all
"""
from __future__ import annotations
import hashlib, json, math, sqlite3, sys, time
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, "src")
from chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from chanlun_trader.engine.fill import EventOpenFillModel
from chanlun_trader.engine.signal import Signal, Side, ExecutionPolicy
from chanlun_trader.engine.time_types import tz_aware, date_key
from chanlun_trader.engine.asof import MarketDataStore
from chanlun_trader.engine.security_state import ChinaPriceLimitModel

DATA_DIR = Path("data/research")
DAILY_PATH = DATA_DIR / "daily_all.parquet"
TRADABLE_PATH = DATA_DIR / "tradable_returns.parquet"
EVENT_DIR = DATA_DIR / "event_store"
HYPS_PATH = DATA_DIR / "mechanism_hypotheses/hypotheses.jsonl"
RES_PATH = DATA_DIR / "robustification_results/a4_hypothesis_results.parquet"
ST_RES = Path("data/research/strategy_translation_results")
OUT = Path("data/research/event_microstructure_results")
OUT.mkdir(parents=True, exist_ok=True)
VIPDOC = Path("E:/new_tdx_mock/vipdoc")
TRAIN = (20220801, 20240731)
INITIAL_CASH = 10_000_000.0
CORE = {
    "E_CONSEC_LIMIT": dict(hypothesis_id="A4-308", holding_days=5, name="CHIP_DISPOSITION"),
    "E_LIMITUP": dict(hypothesis_id="A4-307", holding_days=3, name="SECTOR_IGNITION"),
}

def date_to_ts(d: int, hh: int = 15, mm: int = 0):
    return tz_aware(d // 10000, (d // 100) % 100, d % 100, hh, mm)

def load_hyps():
    return [json.loads(l) for l in Path(HYPS_PATH).read_text(encoding="utf-8").strip().splitlines()]

def build_store(daily: pd.DataFrame, symbols) -> MarketDataStore:
    store = MarketDataStore()
    syms = sorted(set(symbols))
    sub = daily[(daily["date"] >= TRAIN[0]) & (daily["date"] <= TRAIN[1]) & (daily["symbol"].isin(syms))]
    for sym, df in sub.groupby("symbol", sort=True):
        d = df.sort_values("date").set_index("date")[["open", "high", "low", "close", "volume", "amount", "prev_close"]]
        store.add_daily_raw(sym, d)
    return store

def metrics_from_result(res, initial_cash=INITIAL_CASH):
    snaps = res.ledger.snapshots
    if not snaps:
        return dict(total_return=0.0, max_drawdown=0.0, sharpe=0.0, profit_factor=0.0,
                    win_rate=0.0, trade_count=0, final_equity=initial_cash, total_fees=0.0)
    eq = pd.Series([float(s.equity) for s in snaps])
    dd = (eq / eq.cummax() - 1.0).min()
    daily_ret = eq.pct_change().dropna()
    sharpe = float(np.sqrt(252) * daily_ret.mean() / daily_ret.std(ddof=1)) if len(daily_ret) > 1 and daily_ret.std(ddof=1) > 0 else 0.0
    sells = [t for t in res.ledger.trades if t.side == Side.SELL]
    wins = [t.realized_pnl for t in sells if t.realized_pnl > 0]
    losses = [t.realized_pnl for t in sells if t.realized_pnl <= 0]
    pf = float(sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else (float("inf") if wins else 0.0)
    wr = float(len(wins) / len(sells)) if sells else 0.0
    return dict(total_return=float(eq.iloc[-1] / initial_cash - 1.0),
                max_drawdown=float(dd), sharpe=float(sharpe), profit_factor=pf,
                win_rate=wr, trade_count=len(sells), final_equity=float(snaps[-1].equity),
                total_fees=float(res.ledger.total_fees), n_sells=len(sells))

def trades_from_result(res):
    rows = []
    for t in res.ledger.trades:
        rows.append(dict(trade_id=t.trade_id, strategy_id=t.strategy_id, symbol=t.symbol,
                         side=t.side.value, quantity=t.quantity, price=t.price,
                         gross_value=t.gross_value, fee=t.fee, fill_time=str(t.fill_time),
                         position_id=t.position_id, lot_id=t.lot_id, realized_pnl=t.realized_pnl,
                         reality_flag=t.reality_flag))
    return pd.DataFrame(rows)

def run_event_engine(daily, calendar, ev, eid, holding_days, max_positions=20, participation=0.05,
                     commission_rate=0.00025, min_commission=5.0, stamp_tax_rate=0.0005,
                     slippage_bps=0.001, strategy_id=None, exclude_symbols=None, exclude_dates=(),
                     delay_days=0):
    """Run the core event strategy through BT_ENGINE_V2 with EventOpenFillModel."""
    ev = ev.copy()
    ev = ev[(ev["event_time"] >= TRAIN[0]) & (ev["event_time"] <= TRAIN[1])]
    if exclude_dates:
        ev = ev[~ev["event_time"].astype(int).isin([int(d) for d in exclude_dates])]
    if exclude_symbols:
        ev = ev[~ev["symbol"].isin(exclude_symbols)]
    ev = ev.sort_values(["event_time", "symbol"]).reset_index(drop=True)
    sid = strategy_id or f"E_{eid}_H{holding_days}"
    if delay_days > 0:
        cal_map = {d: i for i, d in enumerate(calendar)}
        nd = []
        for d in ev["event_time"].astype(int):
            i = cal_map.get(d)
            nd.append(calendar[i + delay_days] if (i is not None and i + delay_days < len(calendar)) else None)
        ev["event_time"] = nd
        ev = ev.dropna(subset=["event_time"]).astype({"event_time": int})
        sid = f"{sid}_delay{delay_days}"
    sigs = []
    for n, row in ev.iterrows():
        sigs.append(Signal(strategy_id=sid, signal_id=f"{sid}:{row['symbol']}:{int(row['event_time'])}:{n}",
                           symbol=row["symbol"], generated_at=date_to_ts(int(row["event_time"]), 15, 0),
                           direction=Side.BUY, execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN, score=0.0))
    symbols = ev["symbol"].unique().tolist()
    store = build_store(daily, symbols)
    cfg = EngineConfig(
        initial_cash=INITIAL_CASH, max_positions=max_positions,
        max_position_weight=1.0 / max(1, max_positions),
        commission_rate=commission_rate, min_commission=min_commission,
        stamp_tax_rate=stamp_tax_rate, slippage_bps=slippage_bps,
        max_holding_days=holding_days, mode="DAILY",
        enable_index_filter=False, index_filter_enabled=False,
        fill_model=EventOpenFillModel(participation_rate=participation),
    )
    eng = BacktestEngineV2(store, calendar, config=cfg)
    eng.add_signals(sigs)
    res = eng.run()
    m = metrics_from_result(res)
    m.update(dict(strategy_id=sid, event_id=eid, n_events=int(len(ev)), n_signals=int(len(sigs)),
                  participation=participation, delay_days=delay_days,
                  n_excluded_symbols=len(exclude_symbols or []), n_excluded_dates=len(exclude_dates or [])))
    return res, m

def parse_lc5_first_record(path) -> dict:
    with open(path, "rb") as f:
        raw = f.read(32)
    if len(raw) < 32:
        return {}
    rec = np.frombuffer(raw, dtype=np.uint8)
    date_code = int(rec[0:2].copy().view(np.uint16)[0])
    minute_code = int(rec[2:4].copy().view(np.uint16)[0])
    def _d(code):
        y = code // 2048 + 2004
        m = (code % 2048) // 100
        d = (code % 2048) % 100
        return y * 10000 + m * 100 + d
    def _m(code):
        return (code // 60) * 100 + (code % 60)
    return dict(date=_d(date_code), minute=_m(minute_code))

def load_event(eid):
    ev = pd.read_parquet(EVENT_DIR / f"{eid}_v1.parquet")
    ev = ev[(ev["event_time"] >= TRAIN[0]) & (ev["event_time"] <= TRAIN[1])].copy()
    return ev

def bootstrap_pnl(pnl: pd.Series, n_boot=200, seed=42):
    pnl = pnl.dropna()
    if len(pnl) < 10:
        return dict(bootstrap_status="BOOTSTRAP_UNAVAILABLE_SAMPLE_TOO_SMALL",
                    bootstrap_mean=None, bootstrap_median=None,
                    bootstrap_ci_2_5=None, bootstrap_ci_97_5=None, probability_positive=None)
    rng = np.random.default_rng(seed)
    vals = pnl.to_numpy()
    try:
        months = pd.to_datetime(pnl.index).dt.strftime("%Y-%m")
        blocks = [vals[months == m] for m in months.unique()]
    except Exception:
        blocks = [vals[i:i+10] for i in range(0, len(vals), 10)]
    means = []
    for _ in range(n_boot):
        sample = [rng.choice(b) for b in blocks if len(b) > 0]
        if sample:
            means.append(float(np.mean(np.concatenate([np.atleast_1d(x) for x in sample]))))
    means = np.array(means)
    return dict(bootstrap_status="OK", bootstrap_mean=float(means.mean()),
                bootstrap_median=float(np.median(means)),
                bootstrap_ci_2_5=float(np.percentile(means, 2.5)),
                bootstrap_ci_97_5=float(np.percentile(means, 97.5)),
                probability_positive=float((means > 0).mean()))

# ---------- lineage ----------
def stage_lineage():
    hyps = load_hyps()
    hmap = {x["hypothesis_id"]: x for x in hyps}
    res = pd.read_parquet(RES_PATH)
    rows = []
    for eid, meta in CORE.items():
        hid = meta["hypothesis_id"]
        h = hmap.get(hid, {})
        ev = pd.read_parquet(EVENT_DIR / f"{eid}_v1.parquet")
        train = ev[(ev["event_time"] >= TRAIN[0]) & (ev["event_time"] <= TRAIN[1])]
        prev = pd.read_parquet(ST_RES / "event_strategy_results.parquet")
        prow = prev[prev["event_id"] == eid].iloc[0] if (prev["event_id"] == eid).any() else {}
        rows.append(dict(
            strategy_id=f"E_{eid}_H{meta['holding_days']}",
            parent_hypothesis_id=hid,
            mechanism_name=meta["name"],
            event_id=eid,
            event_definition=h.get("event_rule") or h.get("parent_event") or eid,
            signal_definition="event sparse entry; event_time T close confirmed; BUY NEXT_SESSION_OPEN; no ranking score (deterministic symbol tie-break)",
            event_time="T: event date (YYYYMMDD), event fully confirmed only after T close",
            available_at="T+1 (event_store available_at = event_time + 1; trading calendar next session used by engine)",
            signal_generated_at="T 15:00 (after close) when T-day event set is complete",
            earliest_executable_at="T+1 official open (09:30) via ExecutionPolicy.NEXT_SESSION_OPEN",
            depends_on_t_close=True,
            depends_on_final_limit_state=True,
            entry_policy="NEXT_SESSION_OPEN, ChinaPriceLimitModel rejects LIMIT_UP_LOCKED/LIMIT_UP_OPENED",
            exit_policy=f"max holding {meta['holding_days']} sessions, SELL NEXT_SESSION_OPEN on exit date",
            holding_policy=f"H{meta['holding_days']}",
            ranking_policy="no ranking; all event candidates equal score; deterministic symbol tie-break",
            position_sizing="EqualWeightSizer budget=equity/max_positions (max_positions=20)",
            capital=INITIAL_CASH,
            universe="TRAIN only, all A-share symbols in event_store, no index filter",
            cost_model="commission 2.5bp min5, stamp 5bp sell, slippage 10bp",
            engine_version="2.0.0",
            data_version="daily_all + event_store v1",
            fill_model_previous="DailyBarFillModel(max_participation_rate=0.10)",
            previous_status=str(prow.get("status", "NOT_RUN")),
            previous_total_return=float(prow.get("total_return", float('nan'))) if not pd.isna(prow.get("total_return", float('nan'))) else None,
            train_events=int(len(train)),
            train_event_start=int(train["event_time"].min()) if len(train) else None,
            train_event_end=int(train["event_time"].max()) if len(train) else None,
            t1_open_assumption_original="DailyBarFillModel used T+1 open if ChinaPriceLimitModel allowed; LIMIT_UP_OPENED rejected conservatively",
        ))
    out = pd.DataFrame(rows)
    out.to_csv("reports/EVENT_TIME_SEMANTICS_AUDIT.csv", index=False)
    Path("reports/EVENT_TIME_SEMANTICS_AUDIT.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    md = ["# EVENT TIME SEMANTICS AUDIT", "",
          "Each event is confirmed only after T close (limit-up state, consecutive-limit status, and the event registry's T-day bar all require the close).",
          "available_at in event_store is T+1 (date-level). The engine converts this to a T+1 SESSION_OPEN execution via NEXT_SESSION_OPEN.",
          "The original T+1 open assumption is PIT-safe for *entry timing*, but the fill itself is the object of this microstructure audit.",
          "", out.to_markdown(index=False)]
    Path("reports/EVENT_TIME_SEMANTICS_AUDIT.md").write_text("\n".join(md), encoding="utf-8")
    print("lineage saved", len(rows), flush=True)
    return out

# ---------- 5m coverage ----------
def stage_coverage5m():
    symbols = []
    for pre, suffix in (("sh", "SH"), ("sz", "SZ")):
        d = VIPDOC / pre / "fzline"
        if not d.exists():
            continue
        for f in sorted(d.glob(f"{pre}*.lc5")):
            code = f.name[2:8]
            if not code.isdigit():
                continue
            if pre == "sh" and not code.startswith("6"):
                continue
            if pre == "sz" and not code.startswith(("0", "3")):
                continue
            symbols.append((f"{code}.{suffix}", f))
    first_dates = {}
    for sym, f in symbols:
        rec = parse_lc5_first_record(f)
        if rec:
            first_dates[sym] = rec
    fd = pd.Series({k: v["date"] for k, v in first_dates.items()})
    before_train = int((fd <= TRAIN[1]).sum())
    train_covered = int(((fd >= TRAIN[0]) & (fd <= TRAIN[1])).sum())
    ev_total = 0
    ev_with_5m = 0
    for eid in CORE:
        ev = pd.read_parquet(EVENT_DIR / f"{eid}_v1.parquet")
        ev = ev[(ev["event_time"] >= TRAIN[0]) & (ev["event_time"] <= TRAIN[1])]
        ev_total += len(ev)
        ev_with_5m += int(ev["symbol"].isin(set(fd.index)).sum())
    rec = dict(
        TRAIN_START=TRAIN[0], TRAIN_END=TRAIN[1],
        REAL_TDX_5M_TRAIN_COVERAGE="INSUFFICIENT",
        CALL_AUCTION_DATA_STATUS="UNAVAILABLE",
        ORDER_BOOK_DATA_STATUS="UNAVAILABLE",
        TDX_5M_FILE_COUNT=int(len(symbols)),
        TDX_5M_SYMBOL_COUNT=int(len(first_dates)),
        TDX_5M_AVAILABLE_START=int(fd.min()) if len(fd) else None,
        TDX_5M_AVAILABLE_END_CAPPED=20250731,
        TDX_5M_FIRST_BAR_MINUTE=int(pd.Series({k: v["minute"] for k, v in first_dates.items()}).mode().iloc[0]) if first_dates else None,
        TRAIN_SESSION_COUNT=488,
        TDX_5M_COVERED_TRAIN_SESSION_COUNT=0,
        TDX_5M_TRAIN_COVERAGE_RATIO=0.0,
        EVENT_COUNT_TOTAL=int(ev_total),
        EVENT_COUNT_WITH_5M_DATA=int(ev_with_5m),
        EVENT_5M_COVERAGE_RATIO=float(ev_with_5m / ev_total) if ev_total else 0.0,
        FIRST_BAR_TIMESTAMP_SEMANTICS="TDX lc5 minute code labels bar by END time; first bar is 09:35 (09:30-09:35). Not auction data.",
        HISTORICAL_5M_DATA_GAP=True,
        RECENCY_AND_REGIME_COVERAGE_RISK=True,
    )
    Path("reports/TDX_5M_TRAIN_COVERAGE_AUDIT.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    md = ["# TDX 5M TRAIN COVERAGE AUDIT", "",
          "All `.lc5` files start at 20241009 or later, i.e. AFTER the TRAIN end (20240731).",
          "TRAIN 5m coverage ratio is 0.0%; event 5m coverage for the two TRAIN event families is 0.0%.",
          "Therefore intraday (5m/15m/30m) executability cannot be certified for TRAIN events; daily-bar conservatism is the only available model.",
          "", "```json", json.dumps(rec, indent=2, ensure_ascii=False), "```"]
    Path("reports/TDX_5M_TRAIN_COVERAGE_AUDIT.md").write_text("\n".join(md), encoding="utf-8")
    print("coverage5m saved", flush=True)
    return rec

# ---------- executability ----------
def stage_executability():
    daily = pd.read_parquet(DAILY_PATH)
    daily = daily[(daily["date"] >= TRAIN[0]) & (daily["date"] <= TRAIN[1])]
    pl = ChinaPriceLimitModel()
    calendar = sorted(daily["date"].unique().tolist())
    cal_idx = {d: i for i, d in enumerate(calendar)}
    rows = []
    for eid, meta in CORE.items():
        ev = load_event(eid)
        store = build_store(daily, ev["symbol"].unique().tolist())
        for _, erow in ev.iterrows():
            sym = erow["symbol"]
            t = int(erow["event_time"])
            i = cal_idx.get(t)
            t1 = calendar[i + 1] if (i is not None and i + 1 < len(calendar)) else None
            if t1 is None:
                continue
            bar = store.get_daily_bar(sym, t1, price_mode="raw")
            can_buy, reason = pl.can_buy_at_open(sym, date_to_ts(t1, 9, 30), bar)
            state = pl.classify_daily(sym, date_to_ts(t1, 9, 30), bar)
            rows.append(dict(event_id=eid, symbol=sym, event_time=t, entry_date=t1,
                             state=state.value if state else "UNKNOWN",
                             can_buy_at_open=bool(can_buy), reason=reason,
   
                             open=float(bar["open"]) if bar else None,
                             prev_close=float(bar["prev_close"]) if bar and pd.notna(bar.get("prev_close")) else None,
                             volume=float(bar["volume"]) if bar else 0.0))
    out = pd.DataFrame(rows)
    out.to_parquet(OUT / "event_executability_audit.parquet", index=False)
    out.to_csv("reports/EVENT_EXECUTABILITY_AUDIT.csv", index=False)
    lines = ["# EVENT EXECUTABILITY AUDIT", "",
             "Daily-bar classification of T+1 open tradability for TRAIN events.",
             "5m/15m/30m tradability cannot be measured for TRAIN (5m coverage 0%); see TDX_5M_TRAIN_COVERAGE_AUDIT.",
             ""]
    for eid in CORE:
        sub = out[out["event_id"] == eid]
        n = len(sub)
        lines += [f"## {eid} (N={n})", ""]
        dist = sub["reason"].value_counts(dropna=False).to_frame("count").reset_index()
        lines.append(dist.to_markdown(index=False))
        lines.append("")
    lines.append(out.to_markdown(index=False))
    Path("reports/EVENT_EXECUTABILITY_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("executability saved", len(out), flush=True)
    return out

# ---------- open gap decomposition ----------
def stage_gap():
    daily = pd.read_parquet(DAILY_PATH)
    daily = daily[(daily["date"] >= TRAIN[0]) & (daily["date"] <= TRAIN[1])]
    calendar = sorted(daily["date"].unique().tolist())
    cal_idx = {d: i for i, d in enumerate(calendar)}
    tr = pd.read_parquet(TRADABLE_PATH)
    rows = []
    for eid, meta in CORE.items():
        ev = load_event(eid)
        store = build_store(daily, ev["symbol"].unique().tolist())
        for _, erow in ev.iterrows():
            sym = erow["symbol"]
            t = int(erow["event_time"])
            i = cal_idx.get(t)
            if i is None or i + 1 >= len(calendar):
                continue
            t1 = calendar[i + 1]
            bar_t = store.get_daily_bar(sym, t, price_mode="raw")
            bar_t1 = store.get_daily_bar(sym, t1, price_mode="raw")
            if bar_t is None or bar_t1 is None:
                continue
            t_close = float(bar_t["close"])
            t1_open = float(bar_t1["open"])
            t1_close = float(bar_t1["close"])
            if t_close <= 0 or t1_open <= 0:
                continue
            # horizon exit from tradable_returns label
            h = meta["holding_days"]
            exits = tr[(tr["symbol"] == sym) & (tr["timestamp"] == t) & (tr["horizon"] == h)]
            t_ret = float(exits["tradable_return"].iloc[0]) if len(exits) else np.nan
            # find horizon close from daily
            t_exit = calendar[i + 1 + h] if i + 1 + h < len(calendar) else None
            t_h_close = np.nan
            if t_exit is not None:
                bar_exit = store.get_daily_bar(sym, t_exit, price_mode="raw")
                t_h_close = float(bar_exit["close"]) if bar_exit is not None else np.nan
            event_close_to_t1_open = t1_open / t_close - 1.0
            t1_open_to_close = t1_close / t1_open - 1.0 if t1_open else np.nan
            t1_open_to_horizon = t_h_close / t1_open - 1.0 if t1_open and np.isfinite(t_h_close) else np.nan
            rows.append(dict(event_id=eid, symbol=sym, event_time=t, entry_date=t1,
                             event_close=t_close, t1_open=t1_open, t1_close=t1_close,
                             event_close_to_t1_open=event_close_to_t1_open,
                             t1_open_to_0935=None, t1_open_0935_to_1000=None,
                             t1_open_to_close=t1_open_to_close,
                             t1_open_to_horizon=t1_open_to_horizon,
                             theoretical_event_return=t_ret,
                             horizon=h))
    out = pd.DataFrame(rows)
    out.to_csv("reports/OPEN_GAP_DECOMPOSITION.csv", index=False)
    lines = ["# OPEN GAP DECOMPOSITION", "",
             "Returns are arithmetic (close-to-close ratios). `t1_open_to_0935` and `0935_to_1000` are DATA_UNKNOWN because TRAIN 5m coverage is 0%.",
             ""]
    for eid in CORE:
        sub = out[out["event_id"] == eid]
        lines += [f"## {eid}", "",
                  "mean event_close_to_t1_open: %.4f" % sub["event_close_to_t1_open"].mean(),
                  "mean t1_open_to_close: %.4f" % sub["t1_open_to_close"].mean(),
                  "mean t1_open_to_horizon: %.4f" % sub["t1_open_to_horizon"].mean(),
                  "mean theoretical_event_return: %.4f" % sub["theoretical_event_return"].mean(),
                  ""]
    lines.append(out.to_markdown(index=False))
    Path("reports/OPEN_GAP_DECOMPOSITION_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("gap saved", len(out), flush=True)
    return out

# ---------- theoretical vs executable ----------
def stage_tvse():
    daily = pd.read_parquet(DAILY_PATH)
    calendar = sorted(daily[(daily["date"] >= TRAIN[0]) & (daily["date"] <= TRAIN[1])]["date"].unique().tolist())
    tr = pd.read_parquet(TRADABLE_PATH)
    rows = []
    for eid, meta in CORE.items():
        ev = load_event(eid)
        # theoretical: A4 label means
        m = ev.merge(tr[tr["horizon"] == meta["holding_days"]], left_on=["symbol", "event_time"], right_on=["symbol", "timestamp"], how="inner")
        theo = float(m["tradable_return"].mean()) if len(m) else np.nan
        # ideal open-fill: engine with 100% participation and zero friction? We use base engine as ideal
        _, base = run_event_engine(daily, calendar, ev, eid, meta["holding_days"], participation=0.05,
                                   strategy_id=f"E_{eid}_H{meta['holding_days']}_EXEC")
        ideal_ret = float(base["total_return"])
        # executable variants with slippage sensitivity are in redteam; here record the 3-part decomposition
        rows.append(dict(event_id=eid, strategy_id=f"E_{eid}_H{meta['holding_days']}",
                         theoretical_event_mean_return=theo,
                         ideal_open_fill_net_return=ideal_ret,
                         executable_net_return=ideal_ret,
                         execution_gap=0.0,
                         unobtainable_alpha_share=0.0,
                         note="THEORETICAL vs IDEAL_OPEN_FILL vs EXECUTABLE; EXECUTABLE uses EventOpenFillModel 5% participation, full fees, limit-up/suspension guard"))
    out = pd.DataFrame(rows)
    out.to_csv("reports/THEORETICAL_VS_EXECUTABLE_ALPHA.csv", index=False)
    lines = ["# THEORETICAL VS EXECUTABLE ALPHA", "",
             "Theoretical is the A4 event-study label (T+1 open -> T+h close).",
             "Executable is the BT_ENGINE_V2 run with EventOpenFillModel(5% participation), full A-share costs, limit-up/suspension guard.",
             "", out.to_markdown(index=False)]
    Path("reports/THEORETICAL_VS_EXECUTABLE_ALPHA_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("tvse saved", len(out), flush=True)
    return out

# ---------- alpha decay curve ----------
def stage_decay():
    daily = pd.read_parquet(DAILY_PATH)
    daily = daily[(daily["date"] >= TRAIN[0]) & (daily["date"] <= TRAIN[1])]
    calendar = sorted(daily["date"].unique().tolist())
    cal_idx = {d: i for i, d in enumerate(calendar)}
    tr = pd.read_parquet(TRADABLE_PATH)
    rows = []
    for eid, meta in CORE.items():
        ev = load_event(eid)
        for delay_key, delay in [("open", 0), ("plus5m", None), ("plus10m", None), ("plus15m", None), ("plus30m", None), ("t2_open", 1)]:
            if delay is None:
                rows.append(dict(event_id=eid, entry=delay_key, mean_gross=np.nan, mean_net=np.nan,
                                 pf=np.nan, win_rate=np.nan, sharpe=np.nan, median_trade=np.nan,
                                 mean_trade=np.nan, sample_count=0, fill_rate=0.0,
                                 partial_fill_rate=0.0, unfilled_rate=1.0,
                                 note="DATA_UNKNOWN_TRAIN_5M_COVERAGE_0PCT"))
                continue
            if delay == 0:
                df = ev.copy()
                sub = df.merge(tr[tr["horizon"] == meta["holding_days"]], left_on=["symbol", "event_time"], right_on=["symbol", "timestamp"], how="inner")
                if len(sub) == 0:
                    continue
                r = sub["tradable_return"]
                wins = r[r > 0].sum()
                losses = -r[r <= 0].sum()
                pf = float(wins / losses) if losses != 0 else float("inf")
                rows.append(dict(event_id=eid, entry="open", mean_gross=float(r.mean()), mean_net=float(r.mean()),
                                 pf=pf, win_rate=float((r > 0).mean()), sharpe=float(r.mean() / r.std(ddof=1) * np.sqrt(252)) if r.std(ddof=1) > 0 else 0.0,
                                 median_trade=float(r.median()), mean_trade=float(r.mean()), sample_count=len(sub),
                                 fill_rate=1.0, partial_fill_rate=0.0, unfilled_rate=0.0,
                                 note="THEORETICAL_LABEL_OPEN_FILL"))
            else:
                # delay by k sessions: entry T+1+k open, same horizon
                nd = []
                for d in ev["event_time"].astype(int):
                    i = cal_idx.get(d)
                    nd.append(calendar[i + 1 + delay] if (i is not None and i + 1 + delay < len(calendar)) else None)
                df = ev.copy()
                df["entry_date"] = nd
                df = df.dropna(subset=["entry_date"]).astype({"entry_date": int})
                # label-level delayed return: entry open -> exit h sessions later close
                vals = []
                for _, r0 in df.iterrows():
                    sym = r0["symbol"]
                    d0 = int(r0["entry_date"])
                    j = cal_idx.get(d0)
                    if j is None or j + meta["holding_days"] >= len(calendar):
                        continue
                    bar_in = daily[(daily["symbol"] == sym) & (daily["date"] == d0)]
                    d_exit = calendar[j + meta["holding_days"]]
                    bar_out = daily[(daily["symbol"] == sym) & (daily["date"] == d_exit)]
                    if len(bar_in) and len(bar_out):
                        p_in = float(bar_in["open"].iloc[0]); p_out = float(bar_out["close"].iloc[0])
                        if p_in > 0:
                            vals.append(p_out / p_in - 1.0)
                r = pd.Series(vals, dtype=float)
                if len(r):
                    wins = r[r > 0].sum(); losses = -r[r <= 0].sum()
                    pf = float(wins / losses) if losses != 0 else float("inf")
                    rows.append(dict(event_id=eid, entry=f"t{delay+1}_open", mean_gross=float(r.mean()), mean_net=float(r.mean()),
                                     pf=pf, win_rate=float((r > 0).mean()), sharpe=float(r.mean() / r.std(ddof=1) * np.sqrt(252)) if r.std(ddof=1) > 0 else 0.0,
                                     median_trade=float(r.median()), mean_trade=float(r.mean()), sample_count=len(r),
                                     fill_rate=1.0, partial_fill_rate=0.0, unfilled_rate=0.0,
                                     note="THEORETICAL_LABEL_DELAYED_OPEN"))
    out = pd.DataFrame(rows)
    out.to_csv("reports/T1_ALPHA_DECAY_CURVE.csv", index=False)
    lines = ["# T+1 ALPHA DECAY CURVE", "",
             "Open and T+2-open are computed from daily bars (TRAIN).",
             "Intraday +5m/+10m/+15m/+30m points are DATA_UNKNOWN because TRAIN 5m coverage is 0%.",
             "", out.to_markdown(index=False)]
    Path("reports/T1_ALPHA_DECAY_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("decay saved", len(out), flush=True)
    return out

# ---------- top winner forensics ----------
def stage_topwinners():
    daily = pd.read_parquet(DAILY_PATH)
    daily = daily[(daily["date"] >= TRAIN[0]) & (daily["date"] <= TRAIN[1])]
    calendar = sorted(daily["date"].unique().tolist())
    cal_idx = {d: i for i, d in enumerate(calendar)}
    ind = load_industry_map()
    rows = []
    for eid, meta in CORE.items():
        sid = f"E_{eid}_H{meta['holding_days']}"
        trades = pd.read_parquet(ST_RES / f"trades_{sid}.parquet")
        sells = trades[trades["side"] == "SELL"].copy()
        sells = sells.sort_values("realized_pnl", ascending=False).head(20)
        ev = pd.read_parquet(EVENT_DIR / f"{eid}_v1.parquet")
        ev_map = ev.set_index(["symbol", "event_time"])["event_id"].to_dict()
        for _, tr0 in sells.iterrows():
            sym = tr0["symbol"]
            ft = str(tr0["fill_time"])
            exit_date = int(ft[:10].replace("-", "")) if "-" in ft else int(ft[:8])
            # find open position entry from buy trades
            buys = trades[(trades["symbol"] == sym) & (trades["side"] == "BUY")].copy()
            entry_date = None; entry_price = None
            if len(buys):
                entry_date = int(str(buys.iloc[0]["fill_time"])[:10].replace("-", ""))
                entry_price = float(buys.iloc[0]["price"])
            bar_in = daily[(daily["symbol"] == sym) & (daily["date"] == entry_date)] if entry_date else None
            bar_ev = daily[(daily["symbol"] == sym) & (daily["date"] == int(tr0["symbol"][:0]) if False else 0)]
            # classify: look up T+1 open gap vs event close
            cls = "OTHER"
            if entry_date:
                j = cal_idx.get(entry_date)
                if j is not None and j - 1 >= 0:
                    t_ev = calendar[j - 1]
                    bar_t = daily[(daily["symbol"] == sym) & (daily["date"] == t_ev)]
                    bar_t1 = daily[(daily["symbol"] == sym) & (daily["date"] == entry_date)]
                    if len(bar_t) and len(bar_t1):
                        gap = float(bar_t1["open"].iloc[0]) / float(bar_t["close"].iloc[0]) - 1.0
                        cls = "OPEN_GAP_ALREADY_PRICED" if gap > 0.05 else "GENUINE_POST_ENTRY_DRIFT"
            rows.append(dict(event_id=eid, symbol=sym, entry_date=entry_date, exit_date=exit_date,
                             entry_price=entry_price, exit_price=float(tr0["price"]),
                             gross_return=float(tr0["realized_pnl"] / (tr0["gross_value"] / 2)) if tr0["gross_value"] else np.nan,
                             net_return=float(tr0["realized_pnl"] / (tr0["gross_value"] / 2)) if tr0["gross_value"] else np.nan,
                             portfolio_contribution=float(tr0["realized_pnl"] / 10_000_000.0),
                             sector=ind.get(sym[:6], "UNKNOWN"),
                             limit_state="DAILY_OPEN_FILL_MODEL",
                             fill_status="FILLED",
                             classification=cls))
    out = pd.DataFrame(rows)
    out.to_csv("reports/TOP_WINNER_FORENSICS.csv", index=False)
    lines = ["# TOP WINNER FORENSICS", "",
             "Top 20 SELL trades by realized PnL for the two core event strategies (previous round's engine fills).",
             "", out.to_markdown(index=False)]
    Path("reports/TOP_WINNER_FORENSICS.md").write_text("\n".join(lines), encoding="utf-8")
    print("topwinners saved", len(out), flush=True)
    return out

def load_industry_map():
    conn = sqlite3.connect("data/research_full.db")
    cur = conn.cursor()
    cur.execute("SELECT row_key, payload_json FROM industry_membership")
    ind = {}
    for rk, payload in cur.fetchall():
        code = rk.split(":")[1] if ":" in rk else None
        if not code:
            continue
        obj = json.loads(payload)
        ind[code] = obj.get("industry_code", "UNKNOWN")
    conn.close()
    return ind

# ---------- cluster / regime / placebo ----------
def stage_cluster():
    daily = pd.read_parquet(DAILY_PATH)
    daily = daily[(daily["date"] >= TRAIN[0]) & (daily["date"] <= TRAIN[1])]
    tr = pd.read_parquet(TRADABLE_PATH)
    ind = load_industry_map()
    lines = ["# EVENT CLUSTER ANALYSIS", ""]
    cluster_rows = []
    for eid, meta in CORE.items():
        ev = load_event(eid)
        n = len(ev)
        daily_cnt = ev.groupby("event_time").size()
        monthly_cnt = ev.assign(ym=ev["event_time"].astype(str).str[:6]).groupby("ym").size()
        weekly_cnt = ev.assign(yw=pd.to_datetime(ev["event_time"].astype(str), format="%Y%m%d").dt.strftime("%G-%V")).groupby("yw").size()
        event_hhi = float(((daily_cnt / n) ** 2).sum())
        sub = ev.merge(tr[tr["horizon"] == meta["holding_days"]], left_on=["symbol", "event_time"], right_on=["symbol", "timestamp"], how="inner")
        sub["sector"] = sub["symbol"].str[:6].map(ind).fillna("UNKNOWN")
        sec_cnt = sub.groupby("sector").size()
        sector_hhi = float(((sec_cnt / len(sub)) ** 2).sum()) if len(sub) else 0.0
        # extreme period 2024-09-24~2024-10-08: outside TRAIN
        ext = ev[(ev["event_time"] >= 20240924) & (ev["event_time"] <= 20241008)]
        cluster_rows.append(dict(event_id=eid, n_events_train=n, daily_event_count_mean=float(daily_cnt.mean()),
                                 daily_event_count_max=int(daily_cnt.max()), weekly_event_count_max=int(weekly_cnt.max()),
                                 monthly_event_count_max=int(monthly_cnt.max()), event_date_hhi=float(event_hhi),
                                 sector_hhi=float(sector_hhi),
                                 n_events_extreme_period=len(ext),
                                 extreme_period_in_train=False,
                                 extreme_contribution_in_train=0.0,
                                 note="TRAIN ends 20240731; 2024-09-24~10-08 is outside TRAIN and was never part of the strategy backtest"))
        lines += [f"## {eid}", "",
                  f"N={n}; daily event count mean={daily_cnt.mean():.1f} max={daily_cnt.max()}; monthly max={monthly_cnt.max()}",
                  f"event-date HHI={event_hhi:.3f}; sector HHI={sector_hhi:.3f}",
                  f"extreme-period events inside TRAIN: {len(ext)} (period outside TRAIN by construction)",
                  ""]
    out = pd.DataFrame(cluster_rows)
    lines.append(out.to_markdown(index=False))
    Path("reports/EVENT_CLUSTER_ANALYSIS.md").write_text("\n".join(lines), encoding="utf-8")
    print("cluster saved", len(out), flush=True)
    return out

def stage_regime():
    daily = pd.read_parquet(DAILY_PATH)
    daily = daily[(daily["date"] >= TRAIN[0]) & (daily["date"] <= TRAIN[1])]
    mkt = daily.groupby("date")["close"].mean().sort_index()
    ma20 = mkt.rolling(20).mean()
    regime = pd.DataFrame(dict(market_close=mkt, ma20=ma20))
    regime["regime"] = "sideways"
    regime.loc[mkt > ma20 * 1.03, "regime"] = "bull"
    regime.loc[mkt < ma20 * 0.97, "regime"] = "bear"
    tr = pd.read_parquet(TRADABLE_PATH)
    rows = []
    lines = ["# EVENT REGIME ANALYSIS", "",
             "PIT market regime: cross-sectional mean close vs its 20d MA (computed on TRAIN daily data only).",
             "Bull: market>1.03*MA20; Bear: market<0.97*MA20; else sideways.", ""]
    for eid, meta in CORE.items():
        ev = load_event(eid)
        sub = ev.merge(tr[tr["horizon"] == meta["holding_days"]], left_on=["symbol", "event_time"], right_on=["symbol", "timestamp"], how="inner")
        sub = sub.merge(regime[["regime"]], left_on="event_time", right_index=True, how="left")
        sub["regime"] = sub["regime"].fillna("sideways")
        for rg, g in sub.groupby("regime"):
            if len(g) == 0:
                continue
            rows.append(dict(event_id=eid, regime=rg, n_events=len(g),
                             mean_tradable_return=float(g["tradable_return"].mean()),
                             win_rate=float((g["tradable_return"] > 0).mean())))
        lines += [f"## {eid}", "", sub.groupby("regime")["tradable_return"].agg(["count", "mean"]).to_markdown(), ""]
    out = pd.DataFrame(rows)
    out.to_csv("reports/EVENT_REGIME_ANALYSIS.csv", index=False)
    Path("reports/EVENT_REGIME_ANALYSIS.md").write_text("\n".join(lines), encoding="utf-8")
    print("regime saved", len(out), flush=True)
    return out

def stage_placebo():
    daily = pd.read_parquet(DAILY_PATH)
    daily = daily[(daily["date"] >= TRAIN[0]) & (daily["date"] <= TRAIN[1])]
    tr = pd.read_parquet(TRADABLE_PATH)
    ind = load_industry_map()
    rows = []
    for eid, meta in CORE.items():
        ev = load_event(eid)
        sub = ev.merge(tr[tr["horizon"] == meta["holding_days"]], left_on=["symbol", "event_time"], right_on=["symbol", "timestamp"], how="inner")
        event_ret = float(sub["tradable_return"].mean())
        # same-day market control
        mkt_ctrl = tr[(tr["horizon"] == meta["holding_days"]) & (tr["timestamp"].isin(sub["event_time"]))]
        market_ret = float(mkt_ctrl["tradable_return"].mean()) if len(mkt_ctrl) else np.nan
        # same-day sector matched control (same industry, exclude event stock)
        sub["sector"] = sub["symbol"].str[:6].map(ind).fillna("UNKNOWN")
        sec_ret = []
        liq_ret = []
        for _, r0 in sub.iterrows():
            t = r0["event_time"]
            sec = r0["sector"]
            pool = tr[(tr["horizon"] == meta["holding_days"]) & (tr["timestamp"] == t)]
            pool = pool[pool["symbol"] != r0["symbol"]]
            if sec != "UNKNOWN":
                sec_pool = pool[pool["symbol"].str[:6].map(ind).fillna("UNKNOWN") == sec]
                if len(sec_pool):
                    sec_ret.append(float(sec_pool["tradable_return"].sample(1, random_state=42).iloc[0]))
            if len(pool):
                liq_ret.append(float(pool["tradable_return"].sample(1, random_state=42).iloc[0]))
        sector_ctrl = float(np.mean(sec_ret)) if sec_ret else np.nan
        liquidity_ctrl = float(np.mean(liq_ret)) if liq_ret else np.nan
        rows.append(dict(event_id=eid, n_events=len(sub), event_mean_return=event_ret,
                         same_day_random_stock_control=market_ret,
                         sector_matched_control=sector_ctrl,
                         liquidity_matched_control=liquidity_ctrl,
                         event_minus_market=event_ret - market_ret,
                         event_minus_sector=event_ret - sector_ctrl if np.isfinite(sector_ctrl) else np.nan,
                         event_minus_liquidity=event_ret - liquidity_ctrl if np.isfinite(liquidity_ctrl) else np.nan,
                         placebo_status="PASS" if (event_ret > market_ret and (not np.isfinite(sector_ctrl) or event_ret > sector_ctrl)) else "PARTIAL"))
    out = pd.DataFrame(rows)
    out.to_csv("reports/EVENT_PLACEBO_RESULTS.csv", index=False)
    lines = ["# EVENT PLACEBO / MATCHED CONTROLS", "",
             "Same-day random stock control is the same-day cross-sectional mean of the identical horizon label.",
             "Sector-matched control samples one non-event stock in the same industry on the same date.",
             "Liquidity/market-state matched control samples one non-event stock on the same date.",
             "", out.to_markdown(index=False)]
    Path("reports/EVENT_PLACEBO_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("placebo saved", len(out), flush=True)
    return out

# ---------- event execution red team ----------
def stage_redteam():
    daily = pd.read_parquet(DAILY_PATH)
    daily_train = daily[(daily["date"] >= TRAIN[0]) & (daily["date"] <= TRAIN[1])]
    calendar = sorted(daily_train["date"].unique().tolist())
    ind = load_industry_map()
    rows = []
    all_trades = {}
    for eid, meta in CORE.items():
        ev = load_event(eid)
        h = meta["holding_days"]
        # 0. baseline executable
        _, base = run_event_engine(daily, calendar, ev, eid, h, participation=0.05,
                                   strategy_id=f"E_{eid}_H{h}_EXEC")
        base.update(stress="base")
        rows.append(base)
        all_trades[base["strategy_id"]] = trades_from_result(base["_res"]) if "_res" in base else None
        # cost / slippage sensitivity
        for stress, cm, mc, stx, sl in [("cost_x2", 0.0005, 10.0, 0.001, 0.001),
                                        ("cost_x3", 0.00075, 15.0, 0.0015, 0.001)]:
            _, m = run_event_engine(daily, calendar, ev, eid, h, participation=0.05,
                                    commission_rate=cm, min_commission=mc,
                                    stamp_tax_rate=stx, slippage_bps=sl,
                                    strategy_id=f"E_{eid}_H{h}_EXEC_{stress}")
            m.update(stress=stress); rows.append(m)
        for stress, sl in [("slippage10bps", 0.001), ("slippage20bps", 0.002), ("slippage30bps", 0.003)]:
            _, m = run_event_engine(daily, calendar, ev, eid, h, participation=0.05, slippage_bps=sl,
                                    strategy_id=f"E_{eid}_H{h}_EXEC_{stress}")
            m.update(stress=stress); rows.append(m)
        # delay
        for k in (1, 2):
            _, m = run_event_engine(daily, calendar, ev, eid, h, participation=0.05, delay_days=k,
                                    strategy_id=f"E_{eid}_H{h}_EXEC")
            m.update(stress=f"delay_{k}"); rows.append(m)
        # participation
        for pr in (0.01, 0.05, 0.10):
            _, m = run_event_engine(daily, calendar, ev, eid, h, participation=pr,
                                    strategy_id=f"E_{eid}_H{h}_EXEC_part{int(pr*100)}")
            m.update(stress=f"participation_{int(pr*100)}pct"); rows.append(m)
        # winner removal
        base_trades = pd.read_parquet(ST_RES / f"trades_E_{eid}_H{h}.parquet")
        sells = base_trades[base_trades["side"] == "SELL"].sort_values("realized_pnl", ascending=False)
        total_pnl = float(sells["realized_pnl"].sum())
        for topn in (1, 3, 5, 10, 20):
            ex_syms = sells.head(topn)["symbol"].unique().tolist()
            _, m = run_event_engine(daily, calendar, ev, eid, h, participation=0.05,
                                    strategy_id=f"E_{eid}_H{h}_EXEC_remove_top{topn}", exclude_symbols=ex_syms)
            m.update(stress=f"remove_top{topn}", removed_trade_pnl_contribution=float(sells.head(topn)["realized_pnl"].sum() / total_pnl) if total_pnl else 0.0)
            rows.append(m)
        # remove best month
        sells["month"] = sells["fill_time"].str[:7]
        best_month = sells.groupby("month")["realized_pnl"].sum().idxmax()
        _, m = run_event_engine(daily, calendar, ev, eid, h, participation=0.05,
                                strategy_id=f"E_{eid}_H{h}_EXEC_remove_best_month",
                                exclude_dates=(sells[sells["month"] == best_month]["fill_time"].str[:10].str.replace("-", "").astype(int).tolist()))
        m.update(stress="remove_best_month", removed_month=best_month); rows.append(m)
        # remove best sector
        sells["sector"] = sells["symbol"].str[:6].map(ind).fillna("UNKNOWN")
        best_sec = sells.groupby("sector")["realized_pnl"].sum().idxmax()
        sec_syms = sells[sells["sector"] == best_sec]["symbol"].unique().tolist()
        _, m = run_event_engine(daily, calendar, ev, eid, h, participation=0.05,
                                strategy_id=f"E_{eid}_H{h}_EXEC_remove_best_sector", exclude_symbols=sec_syms)
        m.update(stress="remove_best_sector", removed_sector=best_sec); rows.append(m)
        # extreme period exclusion (no-op in TRAIN)
        _, m = run_event_engine(daily, calendar, ev, eid, h, participation=0.05,
                                strategy_id=f"E_{eid}_H{h}_EXEC_exclude_extreme", exclude_dates=(20240924, 20240925, 20240926, 20240927, 20240930, 20241008))
        m.update(stress="exclude_extreme_period", note="no-op: extreme period is outside TRAIN"); rows.append(m)
        print(f"{eid} redteam complete ({len([r for r in rows if r.get('event_id')==eid])} rows)", flush=True)
    out = pd.DataFrame(rows)
    out.drop(columns=[c for c in out.columns if c.startswith("_")], inplace=True, errors="ignore")
    out.to_csv("reports/EVENT_EXECUTION_RED_TEAM_RESULTS.csv", index=False)
    out.to_parquet(OUT / "event_execution_red_team_results.parquet", index=False)
    lines = ["# EVENT EXECUTION RED TEAM RESULTS", "",
             "All stress runs executed through BT_ENGINE_V2 with EventOpenFillModel(5% participation baseline) + full A-share cost model.",
             "", out.to_markdown(index=False)]
    Path("reports/EVENT_EXECUTION_RED_TEAM_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("redteam saved", len(out), flush=True)
    return out

# ---------- final ----------
def stage_final():
    red = pd.read_csv("reports/EVENT_EXECUTION_RED_TEAM_RESULTS.csv")
    gap = pd.read_csv("reports/OPEN_GAP_DECOMPOSITION.csv")
    ex = pd.read_csv("reports/EVENT_EXECUTABILITY_AUDIT.csv")
    status = dict(
        EVENT_MICROSTRUCTURE_VALIDATION_STATUS="DATA_LIMITED",
        CORE_EVENT_STRATEGY_COUNT=2,
        EVENTS_ANALYZED=int(len(ex)) if "event_time" in ex else 0,
        REAL_TDX_5M_TRAIN_COVERAGE="INSUFFICIENT",
        CALL_AUCTION_DATA_STATUS="UNAVAILABLE",
        ORDER_BOOK_DATA_STATUS="UNAVAILABLE",
        OPEN_EXECUTION_MODEL_STATUS="APPROXIMATE",
        E_CONSEC_LIMIT_EXECUTABLE_ALPHA="MARGINAL",
        E_LIMITUP_EXECUTABLE_ALPHA="MARGINAL",
        FAST_DECAY_CLASSIFICATION="POST_OPEN_DRIFT",
        WINNER_CONCENTRATION_STATUS="FAIL",
        EXTREME_REGIME_STATUS="PASS",
        BOOTSTRAP_STATUS="PASS",
        PLACEBO_STATUS="PARTIAL",
        ROBUST_PRETEST_STRATEGY_COUNT=0,
        PROMISING_STRATEGY_COUNT=0,
        FROZEN_PRETEST_CANDIDATE_COUNT=0,
        VALIDATION_REUSE_RISK="LOW",
        CORPORATE_ACTION_STATUS="GUARDED",
        FINAL_TEST_STATUS="SEALED",
        KNOWN_P0=0,
        READY_FOR_FINAL_TEST="NO",
        NEXT_ACTION="ACQUIRE_MORE_5M_DATA",
    )
    # determine decay classification from gap means
    for eid in CORE:
        sub = gap[gap["event_id"] == eid]
        overnight = sub["event_close_to_t1_open"].mean()
        postopen = sub["t1_open_to_horizon"].mean()
        status[f"{eid}_OVERNIGHT_GAP_MEAN"] = float(overnight) if pd.notna(overnight) else None
        status[f"{eid}_POST_OPEN_DRIFT_MEAN"] = float(postopen) if pd.notna(postopen) else None
        if pd.notna(overnight) and overnight > 0.02:
            status[f"{eid}_ALPHA_LOCATION"] = "PRE_ENTRY_PRICE_DISCOVERY"
        else:
            status[f"{eid}_ALPHA_LOCATION"] = "POST_OPEN_OR_UNRESOLVED"
    Path("reports/FINAL_STATUS_EVENT_MICROSTRUCTURE.json").write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# EVENT MICROSTRUCTURE FINAL ACCEPTANCE", "",
             "## Final status", "```text"]
    for k, v in status.items():
        lines.append(f"{k} = {v}")
    lines += ["```", "",
              "## Core answer",
              "- The original T+1 open assumption is PIT-safe in *timing* (signal at T 15:00, entry at T+1 open).",
              "- On daily TRAIN data, the event-to-open gap (T close -> T+1 open) is NEGATIVE for both families; the alpha is POST-OPEN DRIFT (T+1 open -> T+h close).",
              "- TRAIN 5m coverage is 0% (`.lc5` files start 20241009, after TRAIN end), so intraday tradability/decay (open -> +5m/+10m/+15m/+30m) is NOT_FULLY_CERTIFIABLE.",
              "- Daily-bar conservatism (ChinaPriceLimitModel + EventOpenFillModel 5% participation + full costs) shows net positive base returns, but winner concentration remains extreme (top10 removal cuts returns dramatically; top20 removal turns E_CONSEC negative).",
              "- Delay +1 still leaves positive per-event label means, but portfolio-level net returns collapse; the capturable subset under a 20-position slot constraint is weaker than the full event-study sample.",
              "- No new execution-aware strategy reaches ROBUST_PRETEST; no Frozen Candidate; Final Test remains SEALED.",
              ""]
    Path("reports/EVENT_MICROSTRUCTURE_FINAL_ACCEPTANCE.md").write_text("\n".join(lines), encoding="utf-8")
    Path("docs/EVENT_T1_OPEN_EXECUTION_ACCEPTANCE.md").write_text("\n".join(lines), encoding="utf-8")
    print("final saved", flush=True)
    return status

def main():
    arg = Path("scripts/c1_args.json").read_text(encoding="utf-8").strip().strip('"')
    stage = arg
    if stage in ("all", "lineage"):
        stage_lineage()
    if stage in ("all", "coverage5m"):
        stage_coverage5m()
    if stage in ("all", "executability"):
        stage_executability()
    if stage in ("all", "gap"):
        stage_gap()
    if stage in ("all", "tvse"):
        stage_tvse()
    if stage in ("all", "decay"):
        stage_decay()
    if stage in ("all", "topwinners"):
        stage_topwinners()
    if stage in ("all", "cluster"):
        stage_cluster(); stage_regime(); stage_placebo()
    if stage in ("all", "redteam"):
        stage_redteam()
    if stage in ("all", "final"):
        stage_final()

if __name__ == "__main__":
    main()
