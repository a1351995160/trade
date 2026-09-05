import sys, time, json, hashlib
from pathlib import Path
import pandas as pd
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
from collections import Counter
from chanlun_trader.config import load_config
from chanlun_trader.tdx_data import TdxData, list_a_stocks
from run_lowvol_qfq_confirm import build_universe_qfq, lowvol_signals_qfq
from short_horizon_lab import sig_tight_breakout, TRAIN
from v2_certification import sym, prep_from_dfs, v1_signals_to_v2, make_store_from_dfs, LOWVOL, TB, TB_TOPN
from chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from chanlun_trader.engine.universe import UniverseService
from chanlun_trader.engine.signal import Side
from chanlun_trader.engine.corporate_action import CorporateActionGuard

cfg = load_config(); tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
stocks = list_a_stocks(tdx.vipdoc); code_market = {s["code"]: s["market"] for s in stocks}
period = TRAIN
t0=time.time(); cal, uni, union, dfs, idx = build_universe_qfq(tdx, cfg, period[0], period[1], top_n=500, amount_lookback=250)
print("universe", len(cal), len(union), round(time.time()-t0,1), flush=True)
pre = prep_from_dfs(dfs)
lv_sigs = lowvol_signals_qfq(cal, uni, dfs, vol_window=LOWVOL["vol_window"], top_n=LOWVOL["top_n"])
tb_sigs = sig_tight_breakout(cal, uni, pre, range_lb=TB["range_lb"], range_max=TB["range_max"], vol_mult=TB["vol_mult"])
store = make_store_from_dfs(union, dfs, code_market)
us = UniverseService()
for d, codes in uni.items():
    us.load_pit_sets({int(d): {sym(c, code_market[c]) for c in codes}})

def build_engine(sigs, topn, hold):
    w = 1.0 / max(1, topn)
    ecfg = EngineConfig(initial_cash=1_000_000.0, max_positions=topn, max_position_weight=w,
                        mode="DAILY", enable_index_filter=False, index_filter_enabled=False,
                        max_holding_days=hold)
    return BacktestEngineV2(store, cal, config=ecfg, universe=us)

def run_v2_engine(sigs, topn, hold):
    eng = build_engine(sigs, topn, hold)
    v2sigs = v1_signals_to_v2(sigs, code_market)
    eng.add_signals(v2sigs)
    t0=time.time(); res = eng.run(); res.elapsed = time.time()-t0
    return res, v2sigs

def trade_hash(res):
    rows = [f"{t.symbol}|{t.side.name}|{t.quantity}|{t.price}|{t.fill_time}|{t.order_id}|{t.realized_pnl:.4f}" for t in res.ledger.trades]
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()[:16]

def equity_hash(res):
    rows = [f"{s.timestamp}|{s.equity:.4f}" for s in res.ledger.snapshots]
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()[:16]

def order_hash(res):
    rows = [f"{o.order_id}|{o.symbol}|{o.side.name}|{o.quantity}|{o.status.name}" for o in sorted(res.orders.orders.values(), key=lambda o: o.order_id)]
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()[:16]

report = {"period": period, "audits": {}}

for name, sigs, topn, hold in (
    ("LOWVOL_QFQ_V60_H25_N10", lv_sigs, LOWVOL["top_n"], LOWVOL["hold"]),
    ("TB_H6_N15", tb_sigs, TB_TOPN, TB["hold"]),
):
    print("== audit", name, flush=True)
    res, v2sigs = run_v2_engine(sigs, topn, hold)
    a = {}
    # determinism (3 runs)
    hashes = []
    for i in range(3):
        r2, _ = run_v2_engine(sigs, topn, hold)
        hashes.append((trade_hash(r2), equity_hash(r2), order_hash(r2)))
    a["determinism"] = {"runs": hashes, "all_equal": len(set(hashes)) == 1}

    # T+1 audit: every sell fill quantity <= sellable_before
    sell_audits = res.ledger.fill_audit
    t1_violations = [x for x in sell_audits if x["fill_quantity"] > x["sellable_before"]]
    a["t1_audit"] = {"n_sell_fills": len(sell_audits), "violations": len(t1_violations), "sample": sell_audits[:5]}

    # Universe audit: filled buy orders
    buy_trades = [t for t in res.ledger.trades if t.side == Side.BUY]
    uni_violations = [t.symbol for t in buy_trades if not us.is_eligible(t.symbol, t.fill_time)]
    reject_reasons = Counter()
    for ev in res.event_log._events:
        if getattr(ev, "order_status", None) is not None:
            st = getattr(ev, "order_status", None)
            if hasattr(st, "name") and st.name in ("REJECTED", "EXPIRED"):
                reject_reasons[getattr(ev, "message", "")] += 1
    a["universe_audit"] = {"n_buy_fills": len(buy_trades), "not_eligible": len(uni_violations),
                           "reject_reasons": dict(reject_reasons)}

    # Corporate action audit: crossing trades
    guard = CorporateActionGuard.from_tdx(tdx)
    ca_cross = 0
    for t in res.ledger.trades:
        if t.side == Side.SELL:
            bd = None
            for b in res.ledger.trades:
                if b.side == Side.BUY and b.position_id == t.position_id:
                    bd = int(b.fill_time.strftime("%Y%m%d")); break
            if bd is None:
                continue
            sd = int(t.fill_time.strftime("%Y%m%d"))
            if guard.has_event_between(t.symbol, bd, sd):
                ca_cross += 1
    a["corporate_action_audit"] = {"guard_events": sum(len(v) for v in guard.events.values()),
                                   "crossing_sell_trades": ca_cross}

    # AsOf audit: all signals have data at generated_at; no future
    n_no_data = 0
    for s in v2sigs:
        d = int(s.generated_at.strftime("%Y%m%d"))
        if store.get_daily_bar(s.symbol, d, price_mode='qfq') is None:
            n_no_data += 1
    a["asof_audit"] = {"n_signals": len(v2sigs), "missing_feature_bar": n_no_data,
                       "all_generated_at_15_00": all(s.generated_at.strftime("%H:%M") == "15:00" for s in v2sigs)}

    # Ledger invariants
    inv = res.ledger.check_invariants()
    cash_neg = [s for s in res.ledger.snapshots if s.cash < -1e-9]
    eq_err_max = 0.0
    for s in res.ledger.snapshots:
        eq_err_max = max(eq_err_max, abs(s.equity - (s.cash + s.market_value)))
    a["ledger_audit"] = {"invariants": inv, "cash_negative_snapshots": len(cash_neg),
                         "max_equity_error": float(eq_err_max),
                         "n_trades": len(res.ledger.trades), "n_sell_fills": len(sell_audits)}
    a["n_orders"] = len(res.orders.orders)
    a["order_status"] = Counter(o.status.name for o in res.orders.orders.values())
    a["final_equity"] = res.ledger.snapshots[-1].equity if res.ledger.snapshots else None
    report["audits"][name] = a
    print(json.dumps(a, indent=2, default=str)[:1200], flush=True)

Path("reports/certification_audits.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
print("SAVED reports/certification_audits.json")
