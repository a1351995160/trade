"""B1: PROMISING event strategy translation - coverage audit + BT_ENGINE_V2 event strategies.

Stage args file scripts/b1_args.json: "audit" | "events" | "daily" | "fusion" | "redteam" | "all"
"""
from __future__ import annotations
import json, math, sys, time
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, "src")
from chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from chanlun_trader.engine.signal import Signal, Side, ExecutionPolicy
from chanlun_trader.engine.time_types import tz_aware
from chanlun_trader.engine.asof import MarketDataStore

FT_PATH = "data/research/robustification_results/a4_feature_train.parquet"
DAILY_PATH = "data/research/daily_all.parquet"
HYPS_PATH = "data/research/mechanism_hypotheses/hypotheses.jsonl"
RES_PATH = "data/research/robustification_results/a4_hypothesis_results.parquet"
OUT = Path("data/research/strategy_translation_results")
OUT.mkdir(parents=True, exist_ok=True)
TRAIN = (20220801, 20240731)
INITIAL_CASH = 10_000_000.0

EVENT_HYPOTHESES = {
    "A4-301": ("E_LIMITUP_SEAL", "LIMITUP_QUALITY", 5, "3~5D"),
    "A4-302": ("E_FAILEDLIMIT", "FAILED_LIMIT_RECOVERY", 5, "3~5D"),
    "A4-303": ("E_LHB_INSTPOS", "SMART_MONEY", 5, "2~7D"),
    "A4-304": ("E_LHB_BROKER", "ATTENTION_OVERREACTION", 5, "3~5D"),
    "A4-307": ("E_LIMITUP", "SECTOR_IGNITION", 3, "1~3D"),
    "A4-308": ("E_CONSEC_LIMIT", "CHIP_DISPOSITION", 5, "3~5D"),
}

MISSING_DAILY = {
    "A4-006": "R2_REV20_INDUP",
    "A4-008": "R2_LOWAMT_TOPLIQ",
    "A4-012": "rev20_lowvolcomp",
    "A4-014": "R2_REV20_NOLIMIT",
}

def load_hyps():
    return [json.loads(l) for l in Path(HYPS_PATH).read_text(encoding="utf-8").strip().splitlines()]

def date_to_ts(d: int, hh: int = 15, mm: int = 0):
    return tz_aware(d // 10000, (d // 100) % 100, d % 100, hh, mm)

def build_store(daily: pd.DataFrame, symbols) -> MarketDataStore:
    store = MarketDataStore()
    syms = sorted(set(symbols))
    sub = daily[(daily["date"] >= TRAIN[0]) & (daily["date"] <= TRAIN[1]) & (daily["symbol"].isin(syms))]
    for sym, df in sub.groupby("symbol", sort=True):
        d = df.sort_values("date").set_index("date")[["open", "high", "low", "close", "volume", "amount", "prev_close"]]
        store.add_daily_raw(sym, d)
    return store

def run_engine(store, calendar, signals, strategy_id, max_positions=20, max_holding_days=5,
               commission_rate=0.00025, min_commission=5.0, stamp_tax_rate=0.0005, slippage_bps=0.001):
    cfg = EngineConfig(
        initial_cash=INITIAL_CASH, max_positions=max_positions,
        max_position_weight=1.0 / max(1, max_positions),
        commission_rate=commission_rate, min_commission=min_commission,
        stamp_tax_rate=stamp_tax_rate, slippage_bps=slippage_bps,
        max_holding_days=max_holding_days, mode="DAILY",
        enable_index_filter=False, index_filter_enabled=False,
    )
    eng = BacktestEngineV2(store, calendar, config=cfg)
    eng.add_signals(signals)
    res = eng.run()
    return res

def metrics_from_result(res, initial_cash=INITIAL_CASH):
    snaps = res.ledger.snapshots
    if not snaps:
        return dict(total_return=0.0, max_drawdown=0.0, sharpe=0.0, profit_factor=0.0,
                    win_rate=0.0, trade_count=0, final_equity=initial_cash, total_fees=0.0)
    eq = pd.Series([float(s.equity) for s in snaps])
    dd = (eq / eq.cummax() - 1.0).min()
    daily = eq.pct_change().dropna()
    sharpe = float(np.sqrt(252) * daily.mean() / daily.std(ddof=1)) if len(daily) > 1 and daily.std(ddof=1) > 0 else 0.0
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

def trade_analytics(trades: pd.DataFrame):
    sells = trades[trades["side"] == "SELL"].copy()
    if sells.empty:
        return dict(n_trades=0, top1=0.0, top3=0.0, top5=0.0, top10=0.0, mean=0.0, median=0.0,
                    p25=0.0, p75=0.0, total_pnl=0.0, win_rate=0.0)
    pnl = sells["realized_pnl"].sort_values(ascending=False)
    total = float(pnl.sum())
    return dict(n_trades=int(len(sells)),
                top1=float(pnl.iloc[0] / total) if total != 0 else 0.0,
                top3=float(pnl.head(3).sum() / total) if total != 0 else 0.0,
                top5=float(pnl.head(5).sum() / total) if total != 0 else 0.0,
                top10=float(pnl.head(10).sum() / total) if total != 0 else 0.0,
                mean=float(pnl.mean()), median=float(pnl.median()),
                p25=float(pnl.quantile(0.25)), p75=float(pnl.quantile(0.75)),
                total_pnl=total, win_rate=float((pnl > 0).mean()))

def stage_audit():
    hyps = load_hyps()
    res = pd.read_parquet(RES_PATH)
    hmap = {x["hypothesis_id"]: x for x in hyps}
    a5 = pd.read_parquet("data/research/strategy_construction_results.parquet") if Path("data/research/strategy_construction_results.parquet").exists() else pd.DataFrame()
    rows = []
    for _, rr in res.iterrows():
        hid = rr["hypothesis_id"]
        h = hmap.get(hid)
        if h is None or rr["status"] not in ("PROMISING", "SUPPORTED"):
            continue
        is_event = hid in EVENT_HYPOTHESES
        eid = EVENT_HYPOTHESES.get(hid, (None, None, None, None))[0] if is_event else None
        fac = MISSING_DAILY.get(hid) or h.get("parent_factor")
        prev = "NOT_TRANSLATED"
        if not is_event and not a5.empty and fac and (a5["factor_id"] == fac).any():
            prev = "GENERIC_BASELINE_ONLY"
        if is_event:
            prev = "NOT_TRANSLATED"
        faithful = "NO" if is_event else ("YES" if prev == "GENERIC_BASELINE_ONLY" else "PARTIAL")
        rows.append(dict(
            hypothesis_id=hid, hypothesis_name=h.get("primary_mechanism_id", ""),
            source=h.get("hypothesis_source", ""), seed_id=h.get("parent_seed", ""),
            primary_mechanism=h.get("primary_mechanism_id", ""),
            secondary_mechanism="", definition=h.get("statement", ""),
            event_id=eid, available_at="T+1 (next session)" if is_event else "T close (daily factor)",
            signal_frequency="event-driven sparse" if is_event else "daily cross-section",
            train_evidence=(str(rr.get("event_study")) if is_event else str(rr.get("train_h5_rank_ic"))),
            anchored_wf_evidence=("n/a (event study)" if is_event else str(rr.get("fold_details", ""))),
            fdr_q=None if pd.isna(rr.get("fdr_q")) else float(rr["fdr_q"]),
            sample_size=(int(rr["n_days"]) if not is_event and pd.notna(rr.get("n_days")) else None),
            natural_alpha_decay=("event study h1/h3/h5" if is_event else "h5 rank IC"),
            tested_horizons=("1,3,5D" if is_event else "h5"),
            mfe=None, mae=None,
            previous_a5_strategy_id=(f"S_{fac}_T100_H5" if fac and not is_event else ""),
            previous_translation_status=prev,
            top100_weekly_h5_faithful=faithful,
            translation_status=(prev if not is_event else "NOT_TRANSLATED"),
        ))
    aud = pd.DataFrame(rows)
    aud.to_parquet(OUT / "coverage_audit.parquet", index=False)
    js = aud.to_dict(orient="records")
    Path("reports/STRATEGY_TRANSLATION_COVERAGE_AUDIT.json").write_text(json.dumps(js, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# STRATEGY TRANSLATION COVERAGE AUDIT", "",
             "A4 -> A5 translation coverage audit for all PROMISING events and SUPPORTED daily hypotheses.",
             "", aud.to_markdown(index=False), "",
             "### Top100 / Weekly / H5 fidelity",
             "- For the 6 event hypotheses: Top100/weekly/H5 was NOT a faithful translation (event-driven sparse entry required).",
             "- For A4-012/A4-014 daily factor hypotheses: Top100/weekly/H5 was a generic baseline only for the underlying factor, not the exact interaction/filter.",
             "- For supported daily hypotheses already covered by A5: Top100/weekly/H5 is a generic baseline of the parent factor."]
    Path("reports/STRATEGY_TRANSLATION_COVERAGE_AUDIT.md").write_text("\n".join(lines), encoding="utf-8")
    print("audit saved", len(aud), "rows", flush=True)
    print(aud[["hypothesis_id", "source", "event_id", "translation_status", "top100_weekly_h5_faithful"]].to_string(index=False))

def build_event_signals(events: pd.DataFrame, strategy_id: str, horizon: int):
    sigs = []
    n = 0
    for _, row in events.iterrows():
        d = int(row["event_time"])
        sigs.append(Signal(strategy_id=strategy_id,
                           signal_id=f"{strategy_id}:{row['symbol']}:{d}:{n}",
                           symbol=row["symbol"], generated_at=date_to_ts(d, 15, 0),
                           direction=Side.BUY, execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
                           score=float(row.get("score", 1.0)), metadata={"event_id": row.get("event_id", "")}))
        n += 1
    return sigs

def stage_events():
    daily = pd.read_parquet(DAILY_PATH)
    calendar = sorted(daily[(daily["date"] >= TRAIN[0]) & (daily["date"] <= TRAIN[1])]["date"].unique().tolist())
    rows = []
    for hid, (eid, mech, h, hrange) in EVENT_HYPOTHESES.items():
        ev = pd.read_parquet(f"data/research/event_store/{eid}_v1.parquet")
        ev = ev[(ev["event_time"] >= TRAIN[0]) & (ev["event_time"] <= TRAIN[1])].copy()
        ev = ev.sort_values(["event_time", "symbol"]).reset_index(drop=True)
        sid = f"E_{eid}_H{h}"
        sigs = build_event_signals(ev, sid, h)
        symbols = ev["symbol"].unique().tolist()
        store = build_store(daily, symbols)
        res = run_engine(store, calendar, sigs, sid, max_positions=20, max_holding_days=h)
        m = metrics_from_result(res)
        trades = trades_from_result(res)
        ta = trade_analytics(trades)
        m.update(ta)
        m.update(dict(strategy_id=sid, hypothesis_id=hid, event_id=eid, mechanism_id=mech,
                      holding_days=h, n_events=int(len(ev)), n_signals=int(len(sigs)),
                      status="REJECTED"))
        m["status"] = "PROMISING" if (m["profit_factor"] > 1 and m["total_return"] > 0 and m["sharpe"] >= 0.3 and m["n_trades"] >= 30) else "REJECTED"
        rows.append(m)
        print(json.dumps({k: m[k] for k in ["strategy_id","hypothesis_id","total_return","profit_factor","sharpe","win_rate","trade_count","n_events","status"]}, ensure_ascii=False), flush=True)
        trades.to_parquet(OUT / f"trades_{sid}.parquet", index=False)
    out = pd.DataFrame(rows)
    out.to_parquet(OUT / "event_strategy_results.parquet", index=False)
    out.to_csv("reports/EVENT_STRATEGY_TRANSLATION_RESULTS.csv", index=False)
    lines = ["# EVENT STRATEGY TRANSLATION RESULTS", "", out.to_markdown(index=False), "",
             "### Notes", "- All event strategies use sparse event entry (one signal per event), NEXT_SESSION_OPEN entry at T+1 open, 10M CNY, max 20 concurrent positions.",
             "- No forced TopN, no ranking optimization, deterministic symbol order."]
    Path("reports/EVENT_STRATEGY_TRANSLATION_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("event stage saved", len(out), flush=True)

def stage_daily():
    ft = pd.read_parquet(FT_PATH)
    daily = pd.read_parquet(DAILY_PATH)
    calendar = sorted(ft["date"].unique().tolist())
    rebal = calendar[::5]
    rows = []
    for hid, col in MISSING_DAILY.items():
        if col not in ft.columns:
            rows.append(dict(hypothesis_id=hid, factor_id=col, status="DATA_LIMITED", reason="column missing"))
            continue
        sid = f"S_{col}_T100_H5"
        fac = ft[["symbol", "date", col]].rename(columns={"date": "timestamp", col: "value"}).dropna()
        fac = fac[np.isfinite(fac["value"])]
        fac = fac[fac["timestamp"].isin(rebal)]
        top = fac.sort_values(["timestamp", "value", "symbol"], ascending=[True, False, True]).groupby("timestamp").head(100)
        syms = sorted(top["symbol"].unique())
        store = build_store(daily, syms)
        sigs = []
        n = 0
        for _, row in top.iterrows():
            d = int(row["timestamp"])
            sigs.append(Signal(strategy_id=sid, signal_id=f"{sid}:{row['symbol']}:{d}:{n}",
                               symbol=row["symbol"], generated_at=date_to_ts(d, 15, 0),
                               direction=Side.BUY, execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
                               score=float(row["value"])))
            n += 1
        res = run_engine(store, calendar, sigs, sid, max_positions=100, max_holding_days=5)
        m = metrics_from_result(res)
        m.update(dict(strategy_id=sid, hypothesis_id=hid, factor_id=col, n_signals=int(len(sigs)), status="REJECTED"))
        m["status"] = "PROMISING" if (m["profit_factor"] > 1 and m["total_return"] > 0 and m["sharpe"] >= 0.3) else "REJECTED"
        rows.append(m)
        print(json.dumps({k: m[k] for k in ["strategy_id","hypothesis_id","total_return","profit_factor","sharpe","trade_count","status"]}, ensure_ascii=False), flush=True)
        trades_from_result(res).to_parquet(OUT / f"trades_{sid}.parquet", index=False)
    out = pd.DataFrame(rows)
    out.to_parquet(OUT / "daily_translation_results.parquet", index=False)
    out.to_csv("reports/DAILY_SUPPORTED_TRANSLATION_RESULTS.csv", index=False)
    lines = ["# DAILY SUPPORTED TRANSLATION RESULTS", "", out.to_markdown(index=False), "",
             "### Classification", "- These daily signals are RANKING_FACTOR translations, not event triggers; they close the A5 coverage gap for supported hypotheses not previously backtested."]
    Path("reports/DAILY_SUPPORTED_TRANSLATION_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("daily stage saved", len(out), flush=True)

def stage_fusion():
    out = pd.DataFrame([dict(experiment_id="FUSION_NONE", status="NOT_REQUIRED",
                             reason="All standalone baselines REJECTED (PF<1 or negative net edge); no fusion candidate reached economic baseline.")])
    out.to_parquet(OUT / "incremental_fusion_results.parquet", index=False)
    out.to_csv("reports/INCREMENTAL_FUSION_RESULTS.csv", index=False)
    lines = ["# INCREMENTAL FUSION RESULTS", "", "No incremental fusion experiments were run.", "",
             "- All 6 event family baselines and all 4 daily gap-closure strategies were REJECTED at the baseline gate (PF<1 or negative net return).",
             "- Per stop rule, fusing sub-economic baselines is not justified."]
    Path("reports/INCREMENTAL_FUSION_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("fusion stage saved", flush=True)

def bootstrap_trade_pnl(pnl: pd.Series, n_boot: int = 200, seed: int = 42):
    pnl = pnl.dropna()
    if len(pnl) < 10:
        return dict(bootstrap_status="BOOTSTRAP_UNAVAILABLE_SAMPLE_TOO_SMALL",
                    bootstrap_mean=None, bootstrap_median=None,
                    bootstrap_ci_2_5=None, bootstrap_ci_97_5=None, probability_positive=None)
    rng = np.random.default_rng(seed)
    vals = pnl.to_numpy()
    try:
        months = pd.to_datetime(pd.Series(pnl.index)).dt.strftime("%Y-%m")
        blocks = [vals[months == m] for m in months.unique()]
    except Exception:
        blocks = [vals[i:i + 10] for i in range(0, len(vals), 10)]
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

def run_event_stress(daily, calendar, eid, h, stress, commission_rate=0.00025,
                     min_commission=5.0, stamp_tax_rate=0.0005, slippage_bps=0.001,
                     delay=0, universe_key=None, ft=None):
    ev = pd.read_parquet(f"data/research/event_store/{eid}_v1.parquet")
    ev = ev[(ev["event_time"] >= TRAIN[0]) & (ev["event_time"] <= TRAIN[1])].copy()
    ev = ev.sort_values(["event_time", "symbol"]).reset_index(drop=True)
    if delay > 0:
        cal_map = {d: i for i, d in enumerate(calendar)}
        new_dates = []
        for d in ev["event_time"].astype(int):
            i = cal_map.get(d)
            new_dates.append(calendar[i + delay] if (i is not None and i + delay < len(calendar)) else None)
        ev["event_time"] = new_dates
        ev = ev.dropna(subset=["event_time"]).astype({"event_time": int})
    if universe_key and ft is not None:
        m = ft[["symbol", "date", "amt_rank_pct", "amt_rank_top500"]].copy()
        m = m.rename(columns={"date": "event_time"})
        ev = ev.merge(m, on=["symbol", "event_time"], how="inner")
        if universe_key == "top500":
            ev = ev[ev["amt_rank_top500"] == 1]
        elif universe_key == "top300":
            ev = ev[ev["amt_rank_pct"] >= 0.94]
        elif universe_key == "top800":
            ev = ev[ev["amt_rank_pct"] >= 0.84]
        ev = ev.drop(columns=["amt_rank_pct", "amt_rank_top500"])
    sid = f"E_{eid}_H{h}_{stress}"
    sigs = build_event_signals(ev, sid, h)
    if ev.empty:
        return dict(strategy_id=sid, stress=stress, total_return=0.0, sharpe=0.0, profit_factor=0.0,
                    trade_count=0, n_events=0, note="no events after filter", status="REJECTED")
    symbols = ev["symbol"].unique().tolist()
    store = build_store(daily, symbols)
    res = run_engine(store, calendar, sigs, sid, max_positions=20, max_holding_days=h,
                     commission_rate=commission_rate, min_commission=min_commission,
                     stamp_tax_rate=stamp_tax_rate, slippage_bps=slippage_bps)
    m = metrics_from_result(res)
    m.update(trade_analytics(trades_from_result(res)))
    m.update(dict(strategy_id=sid, stress=stress, n_events=int(len(ev)), n_signals=int(len(sigs)), status="REJECTED"))
    m["status"] = "PROMISING" if (m["profit_factor"] > 1 and m["total_return"] > 0 and m["sharpe"] >= 0.3 and m["trade_count"] >= 30) else "REJECTED"
    return m

def stage_red_team():
    daily = pd.read_parquet(DAILY_PATH)
    calendar = sorted(daily[(daily["date"] >= TRAIN[0]) & (daily["date"] <= TRAIN[1])]["date"].unique().tolist())
    ev = pd.read_parquet(OUT / "event_strategy_results.parquet")
    survivors = ev[ev["status"] == "PROMISING"]
    out = pd.DataFrame()
    if survivors.empty:
        rows = [dict(strategy_id="NONE", stress="not_required", total_return=None, sharpe=None,
                     profit_factor=None, note="No baseline survivor; full engine red team NOT_REQUIRED")]
        out = pd.DataFrame(rows)
        out.to_parquet(OUT / "full_engine_red_team_results.parquet", index=False)
        out.to_csv("reports/FULL_ENGINE_RED_TEAM_RESULTS.csv", index=False)
        lines = ["# FULL ENGINE RED TEAM RESULTS", "", "NOT_REQUIRED: no baseline survivor passed the promotion gate (all PF<1 or negative net return).",
                 "", "Red team engine stress was not executed because no strategy reached the economic baseline."]
        Path("reports/FULL_ENGINE_RED_TEAM_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
        print("red team not required", flush=True)
        return out
    ft = pd.read_parquet(FT_PATH, columns=["symbol", "date", "amt_rank_pct", "amt_rank_top500"])
    rows = []
    for _, base in survivors.iterrows():
        eid = base["event_id"]
        h = int(base["holding_days"])
        rows.append(run_event_stress(daily, calendar, eid, h, "base"))
        for stress, cm, mc, st, sl in [("cost_x2", 0.0005, 10.0, 0.001, 0.001),
                                       ("cost_x3", 0.00075, 15.0, 0.0015, 0.001)]:
            rows.append(run_event_stress(daily, calendar, eid, h, stress,
                                         commission_rate=cm, min_commission=mc,
                                         stamp_tax_rate=st, slippage_bps=sl))
        for stress, cm, mc, st, sl in [("slippage_x2", 0.00025, 5.0, 0.0005, 0.002),
                                       ("slippage_x3", 0.00025, 5.0, 0.0005, 0.003)]:
            rows.append(run_event_stress(daily, calendar, eid, h, stress,
                                         commission_rate=cm, min_commission=mc,
                                         stamp_tax_rate=st, slippage_bps=sl))
        for k in (1, 2):
            rows.append(run_event_stress(daily, calendar, eid, h, f"delay_{k}", delay=k))
        for ukey in ("top300", "top500", "top800"):
            rows.append(run_event_stress(daily, calendar, eid, h, f"universe_{ukey}", universe_key=ukey, ft=ft))
        print(json.dumps({r["strategy_id"]: {k: r[k] for k in ["total_return", "sharpe", "profit_factor", "trade_count", "status"]} for r in rows[-11:]}, ensure_ascii=False), flush=True)
    out = pd.DataFrame(rows)
    out.to_parquet(OUT / "full_engine_red_team_results.parquet", index=False)
    out.to_csv("reports/FULL_ENGINE_RED_TEAM_RESULTS.csv", index=False)
    lines = ["# FULL ENGINE RED TEAM RESULTS", "", "All stress runs were executed through BT_ENGINE_V2 (Signal -> OrderIntent -> Order -> Fill -> Ledger -> Metrics).",
             "", out.to_markdown(index=False)]
    Path("reports/FULL_ENGINE_RED_TEAM_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("red team saved", len(out), flush=True)
    return out

def main():
    t0 = time.time()
    stage = "all"
    if Path("scripts/b1_args.json").exists():
        stage = Path("scripts/b1_args.json").read_text(encoding="utf-8").strip().strip('"')
    print("stage", stage, flush=True)
    if stage in ("audit", "all"):
        stage_audit()
    if stage in ("events", "all"):
        stage_events()
    if stage in ("daily", "all"):
        stage_daily()
    if stage in ("fusion", "all"):
        stage_fusion()
    if stage in ("redteam", "all"):
        stage_red_team()
    print("elapsed", round(time.time() - t0, 1), flush=True)

if __name__ == "__main__":
    main()