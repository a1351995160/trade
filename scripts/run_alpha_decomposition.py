"""Alpha Decomposition & Market Timing Discovery 主运行器。

输出：experiments/alpha_decomposition_ledger.csv
"""
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

from chanlun_trader.config import load_config
from chanlun_trader.exposure_runner import ExposureRunner
from chanlun_trader.market_state import build_market_state, exposure_from_rule
from chanlun_trader.metrics import compute_metrics
from chanlun_trader.tdx_data import TdxData

from decomp_lib import (
    build_universe_data,
    diagnostic_from_trades,
    entry_mom20_signals,
    exit_atr_stop_signals,
    exit_signals_for_buys,
    find_first_full_day,
    selection_signals,
    timing_signals,
)


FIELD_NAMES = [
    "component", "strategy_id", "rule", "trade_count", "win_rate", "total_return",
    "annual_return", "max_drawdown", "sharpe", "sortino", "calmar", "profit_factor",
    "avg_holding_days", "benchmark_return", "exposure_mean", "stop_loss_count",
    "stop_loss_cost", "reentry_count", "whipsaw_count", "decision", "timestamp",
]


def make_runner(tdx, cfg, signals, union, exposure_map=None):
    cfg = {k: v for k, v in cfg.items()}
    if exposure_map is not None:
        cfg["__exposure_by_date__"] = exposure_map
    return ExposureRunner(tdx, cfg, signals, universe_codes=union)


def run_engine(tdx, cfg, signals, union, benchmark, exposure_map=None, max_positions=None, max_hold=None):
    if max_positions is not None:
        cfg["backtest"]["max_positions"] = max_positions
    if max_hold is not None:
        cfg["backtest"]["max_holding_days"] = max_hold
    runner = make_runner(tdx, cfg, signals, union, exposure_map=exposure_map)
    result = runner.run()
    m = compute_metrics(result, benchmark)
    return result, m


def record(ledger_path, row):
    with open(ledger_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELD_NAMES, extrasaction="ignore")
        writer.writerow(row)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="2022-08-01")
    parser.add_argument("--end", type=str, default="2024-07-31")
    parser.add_argument("--top-n", type=int, default=500)
    parser.add_argument("--only", type=str, default="")  # timing|selection|exit|exposure|stop
    args = parser.parse_args()

    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
    calendar, uni_sets, union, dfs, idx_close = build_universe_data(tdx, cfg, args.start, args.end, top_n=args.top_n)
    print(f"Calendar {len(calendar)} days, union {len(union)}", flush=True)
    benchmark = tdx.get_benchmark(cfg["backtest"].get("benchmark", "sh000300"))
    ms = build_market_state(calendar, uni_sets, dfs, idx_close)
    print("Market state built", flush=True)

    cfg["backtest"]["start"] = args.start
    cfg["backtest"]["end"] = args.end
    cfg["backtest"]["stop_loss_pct"] = 0.0
    cfg["backtest"]["max_holding_days"] = 999
    cfg["backtest"]["max_picks_per_day"] = 0
    cfg["backtest"]["max_per_industry_per_day"] = 0
    cfg["trailing_stop"]["enabled"] = False
    cfg["index_filter"]["enabled"] = False
    cfg["universe"]["amount_top_n"] = args.top_n
    cfg["universe"]["amount_lookback"] = 250
    cfg["universe"]["min_amount_ma20"] = 0
    cfg["__universe_sets__"] = uni_sets

    ledger_path = Path("experiments/alpha_decomposition_ledger.csv")
    if not ledger_path.exists():
        with open(ledger_path, "w", newline="", encoding="utf-8-sig") as f:
            f.write(",".join(FIELD_NAMES) + "\n")

    # 基准市场状态
    def row_base(component, sid, rule, m, result, exposure_map=None, diag=None):
        expo_mean = float(np.mean(list(exposure_map.values()))) if exposure_map else 1.0
        return {
            "component": component, "strategy_id": sid, "rule": rule,
            "trade_count": m["trade_count"], "win_rate": f"{m['win_rate']:.4f}",
            "total_return": f"{m['total_return']:.4f}", "annual_return": f"{m['annual_return']:.4f}",
            "max_drawdown": f"{m['max_drawdown']:.4f}", "sharpe": f"{m['sharpe']:.4f}",
            "sortino": f"{m['sortino']:.4f}", "calmar": f"{m['calmar']:.4f}",
            "profit_factor": f"{m['profit_factor']:.4f}",
            "avg_holding_days": f"{m['avg_holding_days']:.1f}",
            "benchmark_return": f"{m['benchmark_return']:.4f}", "exposure_mean": f"{expo_mean:.3f}",
            "stop_loss_count": diag.get("stop_loss_count", 0) if diag else 0,
            "stop_loss_cost": f"{diag.get('stop_loss_cost', 0.0):.1f}" if diag else "0",
            "reentry_count": diag.get("reentry_count", 0) if diag else 0,
            "whipsaw_count": diag.get("whipsaw_count", 0) if diag else 0,
            "decision": "", "timestamp": datetime.now().isoformat(timespec="seconds"),
        }

    # ------------------------------------------------------------ A. TIMING ONLY
    if not args.only or args.only == "timing":
        print("=== TIMING ONLY ===", flush=True)
        pool_date = find_first_full_day(calendar, uni_sets, min_n=args.top_n)
        pool_codes = sorted(uni_sets[pool_date]) if pool_date else []
        print(f"Pool date {pool_date}, pool size {len(pool_codes)}", flush=True)
        rules = [
            "ALWAYS", "CASH", "IDX_MA20", "IDX_MA60", "IDX_MOM20", "IDX_MOM60",
            "IDX_DD60", "IDX_VOL20", "BREADTH_MA20", "BREADTH_ADV",
            "COMPOSITE_TB", "COMPOSITE_TV", "COMPOSITE_BV", "COMPOSITE_TBV",
        ]
        for rule in rules:
            expo_map = exposure_from_rule(ms, rule)
            signals = timing_signals(calendar, pool_codes, expo_map, pool_date=pool_date)
            result, m = run_engine(tdx, cfg, signals, union, benchmark, max_positions=args.top_n)
            diag = diagnostic_from_trades(result["trades"])
            print(
                f"TIMING {rule}: trades={m['trade_count']} ret={m['total_return']:.4f} "
                f"dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f} "
                f"expo={float(np.mean(list(expo_map.values()))):.3f}",
                flush=True,
            )
            record(ledger_path, row_base("TIMING", rule, rule, m, result, expo_map, diag))

    # ------------------------------------------------------------ B. SELECTION ONLY
    if not args.only or args.only == "selection":
        print("=== SELECTION ONLY ===", flush=True)
        sel_ids = [
            "SEL_RANDOM", "SEL_MOM20", "SEL_MOM60", "SEL_RS120", "SEL_TRENDQ",
            "SEL_LOWVOL", "SEL_LIQUIDITY", "SEL_VOLTREND", "SEL_VOLADJMOM",
            "SEL_MULTIHORIZON", "SEL_STRENGTH",
        ]
        for sid in sel_ids:
            t0 = time.time()
            signals = selection_signals(calendar, uni_sets, dfs, sid, hold_days=20, max_picks=10)
            result, m = run_engine(tdx, cfg, signals, union, benchmark, max_positions=10, max_hold=20)
            diag = diagnostic_from_trades(result["trades"])
            print(
                f"SELECTION {sid}: trades={m['trade_count']} ret={m['total_return']:.4f} "
                f"dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f} "
                f"hold={m['avg_holding_days']:.1f} ({time.time()-t0:.0f}s)",
                flush=True,
            )
            record(ledger_path, row_base("SELECTION", sid, sid, m, result, diag=diag))

    # ------------------------------------------------------------ C. EXIT ONLY
    if not args.only or args.only == "exit":
        print("=== EXIT ONLY ===", flush=True)
        buy_signals = entry_mom20_signals(calendar, uni_sets, dfs, max_picks=10)
        exit_modes = ["FIXED_5", "FIXED_10", "FIXED_20", "FIXED_60", "NONE", "SIGNAL_MA10", "SIGNAL_MA20", "HYBRID_20_MA10"]
        mode_hold = {"FIXED_5": 5, "FIXED_10": 10, "FIXED_20": 20, "FIXED_60": 60, "NONE": 999, "SIGNAL_MA10": 999, "SIGNAL_MA20": 999, "HYBRID_20_MA10": 20}
        for mode in exit_modes:
            sells = exit_signals_for_buys(calendar, dfs, buy_signals, mode)
            signals = buy_signals + sells
            result, m = run_engine(tdx, cfg, signals, union, benchmark, max_positions=10, max_hold=mode_hold[mode])
            diag = diagnostic_from_trades(result["trades"])
            print(
                f"EXIT {mode}: trades={m['trade_count']} ret={m['total_return']:.4f} "
                f"dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f} "
                f"hold={m['avg_holding_days']:.1f}",
                flush=True,
            )
            record(ledger_path, row_base("EXIT", mode, mode, m, result, diag=diag))
        # ATR 止损
        atr_signals = exit_atr_stop_signals(buy_signals, dfs, atr_period=20, mult=3.0)
        result, m = run_engine(tdx, cfg, atr_signals, union, benchmark, max_positions=10, max_hold=999)
        diag = diagnostic_from_trades(result["trades"])
        print(f"EXIT ATR3: trades={m['trade_count']} ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f}", flush=True)
        record(ledger_path, row_base("EXIT", "ATR3", "ATR3", m, result, diag=diag))
        # Trailing 止损
        cfg_trail = {k: v for k, v in cfg.items()}
        cfg_trail["trailing_stop"]["enabled"] = True
        cfg_trail["trailing_stop"]["activate_pct"] = 0.10
        cfg_trail["trailing_stop"]["trail_pct"] = 0.08
        signals = [s for s in buy_signals]
        result, m = run_engine(tdx, cfg_trail, signals, union, benchmark, max_positions=10, max_hold=999)
        diag = diagnostic_from_trades(result["trades"])
        print(f"EXIT TRAILING: trades={m['trade_count']} ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f}", flush=True)
        record(ledger_path, row_base("EXIT", "TRAILING", "TRAILING", m, result, diag=diag))
    # ------------------------------------------------------------ D. EXPOSURE ONLY
    if not args.only or args.only == "exposure":
        print("=== EXPOSURE ONLY ===", flush=True)
        base_signals = selection_signals(calendar, uni_sets, dfs, "SEL_MOM20", hold_days=20, max_picks=10)
        base_result, base_m = run_engine(tdx, cfg, base_signals, union, benchmark, max_positions=10, max_hold=20)
        eq = pd.Series([e for d, e in base_result["equity_curve"]])
        eq.index = [d for d, e in base_result["equity_curve"]]
        eq = eq[~eq.index.duplicated(keep="last")].sort_index()
        ret = eq.pct_change().fillna(0.0)
        rules = ["ALWAYS", "COMPOSITE_TBV", "COMPOSITE_TBV_3S", "COMPOSITE_TBV_100_30_0", "COMPOSITE_TBV_100_50_20", "CASH"]
        for rule in rules:
            expo_map = exposure_from_rule(ms, rule)
            nav = 1.0
            prev_expo = 1.0
            cost = 0.003
            for d in ret.index:
                target = expo_map.get(int(d), 1.0)
                nav *= (1.0 + target * float(ret.loc[d]))
                nav *= (1.0 - abs(target - prev_expo) * cost)
                prev_expo = target
            total_ret = nav - 1.0
            n_days = len(ret)
            ann = ((1.0 + total_ret) ** (252.0 / n_days) - 1.0) if n_days and total_ret > -1 else 0.0
            print(f"EXPOSURE {rule}: overlay_ret={total_ret:.4f} mean_expo={float(np.mean(list(expo_map.values()))):.3f}", flush=True)
            record(ledger_path, {
                "component": "EXPOSURE", "strategy_id": rule, "rule": rule,
                "trade_count": 0, "win_rate": "", "total_return": f"{total_ret:.4f}",
                "annual_return": f"{ann:.4f}", "max_drawdown": "", "sharpe": "", "sortino": "",
                "calmar": "", "profit_factor": "", "avg_holding_days": "",
                "benchmark_return": f"{base_m['benchmark_return']:.4f}",
                "exposure_mean": f"{float(np.mean(list(expo_map.values()))):.3f}",
                "stop_loss_count": 0, "stop_loss_cost": "0", "reentry_count": 0, "whipsaw_count": 0,
                "decision": "", "timestamp": datetime.now().isoformat(timespec="seconds"),
            })
    # ------------------------------------------------------------ STOP LOSS DIAGNOSTIC
    if not args.only or args.only == "stop":
        print("=== STOP LOSS DIAGNOSTIC ===", flush=True)
        from chanlun_trader.chan import Signal as _S
        base_buys = entry_mom20_signals(calendar, uni_sets, dfs, max_picks=10)
        fixed20 = exit_signals_for_buys(calendar, dfs, base_buys, "FIXED_20")
        stop_modes = {"NOSTOP": None, "STOP_2PCT": 0.02, "STOP_3PCT": 0.03, "STOP_5PCT": 0.05, "STOP_8PCT": 0.08}
        for mode, stop in stop_modes.items():
            if stop is None:
                signals = base_buys + fixed20
            else:
                stopped = [
                    _S(code=s.code, signal_date=s.signal_date, direction=s.direction,
                       signal_type=s.signal_type, price_ref=s.price_ref,
                       stop_low=s.price_ref * (1 - stop), score=s.score)
                    for s in base_buys
                ]
                signals = stopped + fixed20
            result, m = run_engine(tdx, cfg, signals, union, benchmark, max_positions=10, max_hold=20)
            diag = diagnostic_from_trades(result["trades"])
            print(
                f"STOP {mode}: trades={m['trade_count']} ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} "
                f"pf={m['profit_factor']:.2f} whipsaw={diag['whipsaw_count']} stop_cost={diag['stop_loss_cost']:.0f}",
                flush=True,
            )
            record(ledger_path, row_base("STOP", mode, mode, m, result, diag=diag))

    print("ALPHA DECOMPOSITION RUN FINISHED", flush=True)


if __name__ == "__main__":
    main()
