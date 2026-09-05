"""RESEARCH ROUND 1 - Broad Search。"""
from __future__ import annotations

import argparse
import csv
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chanlun_trader.alpha_strategies import ALL_STRATEGIES
from chanlun_trader.config import load_config
from chanlun_trader.metrics import compute_metrics
from chanlun_trader.strategy_runner import CustomBacktestRunner
from chanlun_trader.tdx_data import TdxData, list_a_stocks


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-size", type=int, default=800)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start", type=str, default="2022-08-01")
    parser.add_argument("--end", type=str, default="2024-07-31")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))

    all_stocks = list_a_stocks(tdx.vipdoc)
    rng = random.Random(args.seed)
    sample = rng.sample(all_stocks, min(args.sample_size, len(all_stocks)))
    codes = [s["code"] for s in sample]
    print(f"Universe sample: {len(codes)} stocks, seed={args.seed}", flush=True)

    dfs = {}
    for s in sample:
        df = tdx.get_qfq_day(s["code"], s["market"])
        if not df.empty:
            df = df.sort_values("date").reset_index(drop=True)
            dfs[s["code"]] = df
    print(f"Loaded qfq data for {len(dfs)} stocks", flush=True)

    bench = tdx.get_benchmark("sh000001")
    if bench is None or len(bench) == 0:
        print("No SH000001 benchmark, abort", flush=True)
        return
    calendar = [int(d) for d in bench["date"] if int(args.start.replace("-", "")) <= int(d) <= int(args.end.replace("-", ""))]
    print(f"Calendar days: {len(calendar)}", flush=True)
    import pandas as pd
    cfg["__index_close__"] = pd.Series(bench["close"].to_numpy(), index=bench["date"].astype("int64"))
    cfg["__index_ma__"] = pd.Series(
        bench["close"].rolling(20).mean().to_numpy(), index=bench["date"].astype("int64")
    )

    cfg["backtest"]["start"] = args.start
    cfg["backtest"]["end"] = args.end
    cfg["backtest"]["stop_loss_pct"] = 0.0
    cfg["backtest"]["max_holding_days"] = 999
    cfg["backtest"]["max_picks_per_day"] = 0
    cfg["backtest"]["max_per_industry_per_day"] = 0
    cfg["trailing_stop"]["enabled"] = False
    cfg["index_filter"]["enabled"] = False
    cfg["universe"]["amount_top_n"] = 0
    cfg["universe"]["min_amount_ma20"] = 50_000_000

    fieldnames = [
        "experiment_id", "parent_experiment_id", "strategy_id", "strategy_family",
        "hypothesis", "code_version", "framework_version", "data_version", "universe_version",
        "parameters", "train_period", "validation_period", "random_seed",
        "trade_count", "win_rate", "total_return", "annual_return", "max_drawdown",
        "sharpe", "sortino", "calmar", "profit_factor", "max_consecutive_losses",
        "avg_holding_days", "benchmark_return", "decision", "reject_reason", "timestamp",
    ]
    ledger_path = Path("experiments/research_ledger.csv")
    write_header = not ledger_path.exists()
    with open(ledger_path, "a", newline="", encoding="utf-8-sig") as f:
        if write_header:
            f.write(",".join(fieldnames) + "\n")

    strategies = list(ALL_STRATEGIES.items())
    if args.limit > 0:
        strategies = strategies[: args.limit]

    for strategy_id, func in strategies:
        t0 = time.time()
        try:
            signals = func(cfg, dfs, calendar)
            gen_time = time.time() - t0
            t1 = time.time()
            runner = CustomBacktestRunner(tdx, cfg, signals, universe_codes=codes)
            result = runner.run()
            m = compute_metrics(result, tdx.get_benchmark(cfg["backtest"].get("benchmark", "sh000300")))
            run_time = time.time() - t1
            decision = "WATCH"
            reject_reason = ""
            if m["trade_count"] < 20:
                decision = "REJECT"
                reject_reason = "trade_count<20"
            elif m["total_return"] < -0.15 and m["profit_factor"] < 0.8:
                decision = "REJECT"
                reject_reason = "negative_alpha"
            print(
                f"{strategy_id}: sigs={len(signals)} trades={m['trade_count']} win={m['win_rate']:.4f} "
                f"ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} "
                f"pf={m['profit_factor']:.2f} gen={gen_time:.1f}s run={run_time:.1f}s decision={decision}",
                flush=True,
            )
            row = {
                "experiment_id": f"EXP-{datetime.now():%Y%m%d%H%M%S}-{strategy_id}",
                "parent_experiment_id": "",
                "strategy_id": strategy_id,
                "strategy_family": strategy_id.split("_")[0],
                "hypothesis": (func.__doc__ or "").strip(),
                "code_version": "chanlun-trader v1",
                "framework_version": "BT_ENGINE_V1",
                "data_version": "tdx_vipdoc_2026-08-14",
                "universe_version": f"U_RANDOM_{args.sample_size}_SEED{args.seed}",
                "parameters": "default",
                "train_period": f"{args.start}~{args.end}",
                "validation_period": "",
                "random_seed": args.seed,
                "trade_count": m["trade_count"],
                "win_rate": f"{m['win_rate']:.4f}",
                "total_return": f"{m['total_return']:.4f}",
                "annual_return": f"{m['annual_return']:.4f}",
                "max_drawdown": f"{m['max_drawdown']:.4f}",
                "sharpe": f"{m['sharpe']:.4f}",
                "sortino": f"{m['sortino']:.4f}",
                "calmar": f"{m['calmar']:.4f}",
                "profit_factor": f"{m['profit_factor']:.4f}",
                "max_consecutive_losses": m["max_consecutive_losses"],
                "avg_holding_days": f"{m['avg_holding_days']:.1f}",
                "benchmark_return": f"{m['benchmark_return']:.4f}",
                "decision": decision,
                "reject_reason": reject_reason,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
            with open(ledger_path, "a", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writerow(row)
        except Exception as e:
            print(f"{strategy_id}: ERROR {e}", flush=True)
            row = {
                "experiment_id": f"EXP-{datetime.now():%Y%m%d%H%M%S}-{strategy_id}",
                "parent_experiment_id": "",
                "strategy_id": strategy_id,
                "strategy_family": strategy_id.split("_")[0],
                "hypothesis": (func.__doc__ or "").strip(),
                "code_version": "chanlun-trader v1",
                "framework_version": "BT_ENGINE_V1",
                "data_version": "tdx_vipdoc_2026-08-14",
                "universe_version": f"U_RANDOM_{args.sample_size}_SEED{args.seed}",
                "parameters": "default",
                "train_period": f"{args.start}~{args.end}",
                "validation_period": "",
                "random_seed": args.seed,
                "decision": "ERROR",
                "reject_reason": str(e)[:200],
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
            with open(ledger_path, "a", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writerow(row)


if __name__ == "__main__":
    main()
