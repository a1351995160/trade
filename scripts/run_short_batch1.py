import sys, csv, time, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
from chanlun_trader.config import load_config
from chanlun_trader.tdx_data import TdxData
from short_horizon_lab import prep_data, run_one, sig_pullback, sig_oversold, sig_breakout, sig_cs_rank, TRAIN

cfg = load_config()
tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
print("prep data", flush=True)
cal, uni, union, pre = prep_data(cfg, tdx, TRAIN)
print("data ready", len(pre), flush=True)

ledger = Path("experiments/short_horizon_ledger.csv")
write_header = not ledger.exists()

experiments = [
    ("PB_A1", "Pullback", sig_pullback(cal, uni, pre, pull_min=0.03, pull_max=0.08, vol_shrink=0.85, hold=5), 5),
    ("PB_A2", "Pullback", sig_pullback(cal, uni, pre, pull_min=0.03, pull_max=0.10, vol_shrink=0.75, hold=5), 5),
    ("PB_A3", "Pullback", sig_pullback(cal, uni, pre, pull_min=0.03, pull_max=0.08, vol_shrink=0.85, hold=8), 8),
    ("OS_B1", "OversoldBounce", sig_oversold(cal, uni, pre, drop_min=0.07, vol_ratio_max=1.5, hold=5), 5),
    ("OS_B2", "OversoldBounce", sig_oversold(cal, uni, pre, drop_min=0.09, vol_ratio_max=1.5, hold=5), 5),
    ("OS_B3", "OversoldBounce", sig_oversold(cal, uni, pre, drop_min=0.07, vol_ratio_max=1.2, hold=3), 3),
    ("BO_C1", "Breakout", sig_breakout(cal, uni, pre, bk_lb=20, vol_mult=1.5, hold=5), 5),
    ("BO_C2", "Breakout", sig_breakout(cal, uni, pre, bk_lb=20, vol_mult=2.0, hold=5), 5),
    ("BO_C3", "Breakout", sig_breakout(cal, uni, pre, bk_lb=40, vol_mult=1.5, hold=8), 8),
    ("CS_D1", "CrossSectional", sig_cs_rank(cal, uni, pre, lookback=5, mode="rs", hold=5), 5),
    ("CS_D2", "CrossSectional", sig_cs_rank(cal, uni, pre, lookback=10, mode="rs", hold=5), 5),
    ("CS_D3", "CrossSectional", sig_cs_rank(cal, uni, pre, lookback=5, mode="voladj", hold=5), 5),
    ("CS_D4", "CrossSectional", sig_cs_rank(cal, uni, pre, lookback=10, mode="voladj", hold=5), 5),
]

with open(ledger, "a", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    if write_header:
        w.writerow(["exp_id","family","train_ret","train_dd","train_sharpe","train_pf","win_rate",
                    "avg_hold","med_hold","trades_year","turnover","cost_drag","n_trades","signals","note"])
    for exp_id, fam, sigs, hold in experiments:
        t0 = time.time()
        try:
            m, avg_hold, med_hold, turn, trades_year, cost_drag, r = run_one(tdx, cfg, cal, uni, union, pre, sigs, TRAIN, hold)
            note = ""
            print(f"{exp_id}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f} wr={m['win_rate']:.3f} avgHold={avg_hold:.1f} medHold={med_hold:.1f} tradesY={trades_year:.0f} turn={turn:.1f} cost={cost_drag:.4f} n={m['trade_count']} sigs={len(sigs)} ({time.time()-t0:.0f}s)", flush=True)
            w.writerow([exp_id, fam, round(m['total_return'],4), round(m['max_drawdown'],4), round(m['sharpe'],2),
                        round(m['profit_factor'],2), round(m['win_rate'],3), round(avg_hold,1), round(med_hold,1),
                        round(trades_year,0), round(turn,1), round(cost_drag,4), m['trade_count'], len(sigs), note])
            f.flush()
        except Exception as e:
            print(f"{exp_id}: ERROR {e}", flush=True)
            w.writerow([exp_id, fam, "ERR", "", "", "", "", "", "", "", "", "", "", len(sigs), str(e)])
            f.flush()
print("BATCH1 DONE", flush=True)
