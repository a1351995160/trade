"""LowVol Candidate 稳健性检验（冻结规格：vw=60, topN=10, hold=30, no stop）。"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from chanlun_trader.config import load_config
from chanlun_trader.exposure_runner import PositionExposureRunner
from chanlun_trader.metrics import compute_metrics
from chanlun_trader.tdx_data import TdxData
from decomp_lib import build_universe_data
from run_alpha_confirmation import lowvol_signals_var

FIELD_NAMES = [
    "test", "period", "universe", "strategy", "delay", "cost_mult",
    "trade_count", "win_rate", "total_return", "annual_return", "max_drawdown",
    "sharpe", "sortino", "calmar", "profit_factor", "avg_holding_days",
    "benchmark_return", "timestamp",
]


def run_spec(tdx, cfg, calendar, uni_sets, union, dfs, delay=0, cost_mult=1.0, vw=60, top_n=10, hold=30):
    cfg = {k: v for k, v in cfg.items()}
    bc = dict(cfg["backtest"])
    bc["stop_loss_pct"] = 0.0
    bc["max_holding_days"] = hold
    bc["max_positions"] = top_n
    bc["max_picks_per_day"] = 0
    bc["max_per_industry_per_day"] = 0
    bc["commission_rate"] = 0.00025 * cost_mult
    bc["min_commission"] = 5.0 if cost_mult >= 1 else 0.0
    bc["stamp_tax_rate"] = 0.0005 * cost_mult
    bc["slippage"] = 0.001 * cost_mult
    cfg["backtest"] = bc
    tc = dict(cfg["trailing_stop"])
    tc["enabled"] = False
    cfg["trailing_stop"] = tc
    cfg["index_filter"] = {"enabled": False}
    cfg["universe"] = {"amount_top_n": 500, "amount_lookback": 250, "min_amount_ma20": 0}
    cfg["__universe_sets__"] = uni_sets
    cfg["__exposure_by_date__"] = {int(d): 1.0 for d in calendar}
    sigs = lowvol_signals_var(calendar, uni_sets, dfs, vol_window=vw, top_n=top_n)
    if delay > 0:
        # 延迟 entry 信号：signal_date 后移 delay 个交易日（更晚发出）
        cal_map = {d: i for i, d in enumerate(calendar)}
        new_sigs = []
        for s in sigs:
            i = cal_map.get(int(s.signal_date), None)
            if i is None:
                continue
            j = i + delay
            if j >= len(calendar):
                continue
            s.signal_date = int(calendar[j])
            new_sigs.append(s)
        sigs = new_sigs
    runner = PositionExposureRunner(tdx, cfg, sigs, universe_codes=union)
    result = runner.run()
    m = compute_metrics(result, tdx.get_benchmark(cfg["backtest"].get("benchmark", "sh000300")))
    return result, m, sigs


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", type=str, default="")
    args = parser.parse_args()

    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
    ledger = Path("experiments/lowvol_robustness_ledger.csv")
    if not ledger.exists():
        with open(ledger, "w", newline="", encoding="utf-8-sig") as f:
            f.write(",".join(FIELD_NAMES) + "\n")

    def record(row):
        with open(ledger, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=FIELD_NAMES, extrasaction="ignore")
            w.writerow(row)

    def base_row(test, period, result, m, universe="TOP500", delay=0, cost_mult=1.0):
        return {
            "test": test, "period": period, "universe": universe, "strategy": "LOWVOL_V60_H30_N10",
            "delay": delay, "cost_mult": cost_mult,
            "trade_count": m["trade_count"], "win_rate": f"{m['win_rate']:.4f}",
            "total_return": f"{m['total_return']:.4f}", "annual_return": f"{m['annual_return']:.4f}",
            "max_drawdown": f"{m['max_drawdown']:.4f}", "sharpe": f"{m['sharpe']:.4f}",
            "sortino": f"{m['sortino']:.4f}", "calmar": f"{m['calmar']:.4f}",
            "profit_factor": f"{m['profit_factor']:.4f}",
            "avg_holding_days": f"{m['avg_holding_days']:.1f}",
            "benchmark_return": f"{m['benchmark_return']:.4f}",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }

    TRAIN = ("2022-08-01", "2024-07-31")
    VAL = ("2024-08-01", "2025-07-31")

    # 建立 TRAIN / VALIDATION universe
    print("build TRAIN universe", flush=True)
    cal_tr, uni_tr, union_tr, dfs_tr, _ = build_universe_data(tdx, cfg, TRAIN[0], TRAIN[1], top_n=500)
    print("build VAL universe", flush=True)
    cal_va, uni_va, union_va, dfs_va, _ = build_universe_data(tdx, cfg, VAL[0], VAL[1], top_n=500)
    periods = {"TRAIN": (TRAIN, cal_tr, uni_tr, union_tr, dfs_tr), "VALIDATION": (VAL, cal_va, uni_va, union_va, dfs_va)}

    # 1) Frozen spec base
    if not args.only or args.only == "base":
        for pname, (pr, cal, uni, union, dfs) in periods.items():
            cfg["backtest"]["start"] = pr[0]
            cfg["backtest"]["end"] = pr[1]
            result, m, sigs = run_spec(tdx, cfg, cal, uni, union, dfs)
            print(f"BASE {pname}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f} trades={m['trade_count']}", flush=True)
            record(base_row("BASE", pname, result, m))

    # 2) Parameter grid on TRAIN only
    if not args.only or args.only == "grid":
        cfg["backtest"]["start"] = TRAIN[0]
        cfg["backtest"]["end"] = TRAIN[1]
        for vw in [40, 60, 80]:
            for hold in [20, 30, 40]:
                for topn in [10, 20]:
                    result, m, sigs = run_spec(tdx, cfg, cal_tr, uni_tr, union_tr, dfs_tr, vw=vw, top_n=topn, hold=hold)
                    tag = f"GRID vw{vw}_h{hold}_n{topn}"
                    print(f"{tag}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f}", flush=True)
                    row = base_row("GRID", "TRAIN", result, m)
                    row["strategy"] = f"LOWVOL_V{vw}_H{hold}_N{topn}"
                    record(row)

    # 3) Cost stress
    if not args.only or args.only == "cost":
        for pname, (pr, cal, uni, union, dfs) in periods.items():
            cfg["backtest"]["start"] = pr[0]
            cfg["backtest"]["end"] = pr[1]
            for cmult in [1.0, 2.0, 3.0]:
                result, m, sigs = run_spec(tdx, cfg, cal, uni, union, dfs, cost_mult=cmult)
                print(f"COST x{cmult} {pname}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f}", flush=True)
                record(base_row("COST_STRESS", pname, result, m, cost_mult=cmult))

    # 4) Signal delay
    if not args.only or args.only == "delay":
        for pname, (pr, cal, uni, union, dfs) in periods.items():
            cfg["backtest"]["start"] = pr[0]
            cfg["backtest"]["end"] = pr[1]
            for dly in [0, 1, 2]:
                result, m, sigs = run_spec(tdx, cfg, cal, uni, union, dfs, delay=dly)
                print(f"DELAY {dly} {pname}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f}", flush=True)
                record(base_row("DELAY", pname, result, m, delay=dly))

    # 5) Universe stress top300 / top800
    if not args.only or args.only == "universe":
        for topn in [300, 800]:
            for pname, pr in [("TRAIN", TRAIN), ("VALIDATION", VAL)]:
                cal, uni, union, dfs, _ = build_universe_data(tdx, cfg, pr[0], pr[1], top_n=topn)
                cfg["backtest"]["start"] = pr[0]
                cfg["backtest"]["end"] = pr[1]
                result, m, sigs = run_spec(tdx, cfg, cal, uni, union, dfs)
                print(f"UNIV {topn} {pname}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f}", flush=True)
                record(base_row("UNIV_STRESS", pname, result, m, universe=f"TOP{topn}"))

    # 6) Remove winners (TRAIN + VALIDATION frozen trades)
    if not args.only or args.only == "remove_winners":
        for pname, (pr, cal, uni, union, dfs) in periods.items():
            cfg["backtest"]["start"] = pr[0]
            cfg["backtest"]["end"] = pr[1]
            result, m, sigs = run_spec(tdx, cfg, cal, uni, union, dfs)
            trades = result["trades"]
            pnls = sorted([t.pnl for t in trades], reverse=True)
            total = sum(pnls)
            for remove_n in [0, 3, 5, 10]:
                removed = pnls[remove_n:]
                remaining = sum(removed)
                pct = remaining / total * 100 if total else 0.0
                print(f"REMOVE_TOP{remove_n} {pname}: remaining_sum={remaining:.0f} vs total={total:.0f} ({pct:.1f}%)", flush=True)
                record({
                    "test": "REMOVE_WINNERS", "period": pname, "universe": "TOP500",
                    "strategy": f"LOWVOL_V60_H30_N10_REM{remove_n}",
                    "delay": 0, "cost_mult": 1.0,
                    "trade_count": len(removed), "win_rate": "",
                    "total_return": f"{remaining:.1f}", "annual_return": "",
                    "max_drawdown": "", "sharpe": "", "sortino": "", "calmar": "",
                    "profit_factor": "", "avg_holding_days": "",
                    "benchmark_return": f"{m['benchmark_return']:.4f}",
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                })

    print("ROBUSTNESS RUN FINISHED", flush=True)


if __name__ == "__main__":
    main()
