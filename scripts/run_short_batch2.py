import sys, csv, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
from chanlun_trader.config import load_config
from chanlun_trader.tdx_data import TdxData
from short_horizon_lab import prep_data, run_one, sig_cs_rev, sig_gap_rev, sig_gap_cont, sig_pv_strength, sig_lower_shadow, TRAIN

cfg = load_config()
tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
print("prep data", flush=True)
cal, uni, union, pre = prep_data(cfg, tdx, TRAIN)
print("data ready", len(pre), flush=True)

ledger = Path("experiments/short_horizon_ledger.csv")
write_header = not ledger.exists()

experiments = [
    ("CSR_E1", "ShortReversal", sig_cs_rev(cal, uni, pre, lookback=5, hold=3), 3),
    ("CSR_E2", "ShortReversal", sig_cs_rev(cal, uni, pre, lookback=5, hold=5), 5),
    ("CSR_E3", "ShortReversal", sig_cs_rev(cal, uni, pre, lookback=10, hold=5), 5),
    ("CSR_E4", "ShortReversal", sig_cs_rev(cal, uni, pre, lookback=5, vol_max=0.035, hold=5), 5),
    ("GAPR_F1", "GapReversal", sig_gap_rev(cal, uni, pre, gap_min=0.03, gap_max=0.10, hold=5), 5),
    ("GAPR_F2", "GapReversal", sig_gap_rev(cal, uni, pre, gap_min=0.04, gap_max=0.12, hold=3), 3),
    ("GAPC_F3", "GapContinuation", sig_gap_cont(cal, uni, pre, gap_min=0.03, gap_max=0.12, hold=5), 5),
    ("GAPC_F4", "GapContinuation", sig_gap_cont(cal, uni, pre, gap_min=0.04, gap_max=0.15, hold=8), 8),
    ("PVS_G1", "PriceVolume", sig_pv_strength(cal, uni, pre, amt_mult=2.0, ret_min=0.03, hold=5), 5),
    ("PVS_G2", "PriceVolume", sig_pv_strength(cal, uni, pre, amt_mult=2.5, ret_min=0.05, hold=5), 5),
    ("LSH_H1", "LowerShadow", sig_lower_shadow(cal, uni, pre, drop_min=0.04, shadow_min=0.015, hold=5), 5),
    ("LSH_H2", "LowerShadow", sig_lower_shadow(cal, uni, pre, drop_min=0.06, shadow_min=0.02, hold=5), 5),
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
            print(f"{exp_id}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f} wr={m['win_rate']:.3f} avgHold={avg_hold:.1f} medHold={med_hold:.1f} tradesY={trades_year:.0f} turn={turn:.1f} cost={cost_drag:.4f} n={m['trade_count']} sigs={len(sigs)} ({time.time()-t0:.0f}s)", flush=True)
            w.writerow([exp_id, fam, round(m['total_return'],4), round(m['max_drawdown'],4), round(m['sharpe'],2),
                        round(m['profit_factor'],2), round(m['win_rate'],3), round(avg_hold,1), round(med_hold,1),
                        round(trades_year,0), round(turn,1), round(cost_drag,4), m['trade_count'], len(sigs), ""])
            f.flush()
        except Exception as e:
            print(f"{exp_id}: ERROR {e}", flush=True)
            w.writerow([exp_id, fam, "ERR", "", "", "", "", "", "", "", "", "", "", len(sigs), str(e)])
            f.flush()
print("BATCH2 DONE", flush=True)
