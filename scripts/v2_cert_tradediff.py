import sys, time, json
from pathlib import Path
import pandas as pd
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
from collections import Counter
from chanlun_trader.config import load_config
from chanlun_trader.tdx_data import TdxData, list_a_stocks
from run_lowvol_qfq_confirm import build_universe_qfq, lowvol_signals_qfq
from short_horizon_lab import sig_tight_breakout, TRAIN
from v2_certification import sym, prep_from_dfs, v1_signals_to_v2, make_store_from_dfs, run_v1, run_v2, LOWVOL, TB, TB_TOPN
from chanlun_trader.engine.signal import Side

cfg = load_config(); tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
stocks = list_a_stocks(tdx.vipdoc); code_market = {s["code"]: s["market"] for s in stocks}
period = TRAIN
t0=time.time(); cal, uni, union, dfs, idx = build_universe_qfq(tdx, cfg, period[0], period[1], top_n=500, amount_lookback=250)
print("universe", len(cal), len(union), round(time.time()-t0,1), flush=True)
pre = prep_from_dfs(dfs)
store = make_store_from_dfs(union, dfs, code_market)

report = {}
for name, sigs, hold, topn in (("LOWVOL_QFQ_V60_H25_N10", lowvol_signals_qfq(cal, uni, dfs, vol_window=LOWVOL["vol_window"], top_n=LOWVOL["top_n"]), LOWVOL["hold"], LOWVOL["top_n"]),
                              ("TB_H6_N15", sig_tight_breakout(cal, uni, pre, range_lb=TB["range_lb"], range_max=TB["range_max"], vol_mult=TB["vol_mult"]), TB["hold"], TB_TOPN)):
    r1, m1 = run_v1(cfg, tdx, period, cal, uni, union, dfs, sigs, hold, topn)
    v1 = pd.DataFrame([{
        "code": t.code, "buy_date": int(t.buy_date), "buy_price": float(t.buy_price), "shares": int(t.shares),
        "sell_date": int(t.sell_date), "sell_price": float(t.sell_price), "pnl": float(t.pnl),
        "sell_reason": t.sell_reason, "holding_days": int(t.holding_days),
        "fee_est": 0.0,
    } for t in r1["trades"] if t.sell_reason != "open"])
    v1 = v1.sort_values(["buy_date", "code"]).reset_index(drop=True)

    v2sigs = v1_signals_to_v2(sigs, code_market)
    res = run_v2(store, cal, uni, union, code_market, v2sigs, topn, 1_000_000.0, hold)
    v2_rows = []
    buys = {t.order_id: t for t in res.ledger.trades if t.side == Side.BUY}
    for t in res.ledger.trades:
        if t.side == Side.SELL:
            # pair with latest buy of same position before sell? approximate: earliest buy in same position
            pos_buys = [b for b in res.ledger.trades if b.side == Side.BUY and b.position_id == t.position_id]
            if not pos_buys:
                continue
            b = pos_buys[0]
            v2_rows.append({
                "code": t.symbol.split(".")[0], "buy_date": int(b.fill_time.strftime("%Y%m%d")),
                "buy_price": float(b.price), "shares": int(t.quantity),
                "sell_date": int(t.fill_time.strftime("%Y%m%d")), "sell_price": float(t.price),
                "pnl": float(t.realized_pnl), "sell_reason": t.order_id, "holding_days": 0,
                "fee_est": 0.0, "reality": t.reality_flag,
            })
    v2 = pd.DataFrame(v2_rows).sort_values(["buy_date", "code"]).reset_index(drop=True)

    # first divergence on buy date+code joined by shares? compare by buy_date/code
    v1k = v1[["buy_date", "code"]].copy(); v1k["_i"] = v1.index
    v2k = v2[["buy_date", "code"]].copy(); v2k["_i"] = v2.index
    merged = v1k.merge(v2k, on=["buy_date", "code"], how="outer", suffixes=("_v1", "_v2"), indicator=True)
    first_diff = merged[merged["_merge"] != "both"].head(5).to_dict("records")
    common = merged[merged["_merge"] == "both"]
    print(name, "v1 trades", len(v1), "v2 sells", len(v2), "common", len(common), flush=True)
    print("first_diff", first_diff, flush=True)
    report[name] = {
        "v1_metrics": {k: m1[k] for k in ("total_return","max_drawdown","sharpe","profit_factor","win_rate","trade_count")},
        "v1_trades": len(v1), "v2_sells": len(v2),
        "first_diff": first_diff,
        "v1_head": v1.head(8).to_dict("records"),
        "v2_head": v2.head(8).to_dict("records"),
        "v2_rejected_reasons": None,
    }
    # v2 reject reasons
    reasons = Counter()
    for ev in res.event_log._events:
        st = getattr(ev, "order_status", None)
        if st is not None and hasattr(st, "name") and st.name == "REJECTED":
            reasons[getattr(ev, "message", "")] += 1
    report[name]["v2_rejected_reasons"] = dict(reasons)

Path("reports/certification_tradediff.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
print("SAVED reports/certification_tradediff.json")
