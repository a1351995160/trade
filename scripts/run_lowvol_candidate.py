"""官方候选 LOWVOL_QFQ_V60_H25_N10 的独立运行器。

用法:
  python -u scripts/run_lowvol_candidate.py --start 2022-08-01 --end 2024-07-31
  python -u scripts/run_lowvol_candidate.py --start 2024-08-01 --end 2025-07-31
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from chanlun_trader.config import load_config
from chanlun_trader.exposure_runner import PositionExposureRunner
from chanlun_trader.metrics import compute_metrics
from chanlun_trader.tdx_data import TdxData
from run_lowvol_qfq_confirm import build_universe_qfq, lowvol_signals_qfq


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-08-01")
    ap.add_argument("--end", default="2024-07-31")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--hold", type=int, default=25)
    ap.add_argument("--vol-window", type=int, default=60)
    args = ap.parse_args()

    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
    print(f"build universe {args.start}..{args.end}", flush=True)
    calendar, uni_sets, union, dfs, _ = build_universe_qfq(tdx, cfg, args.start, args.end)

    bc = dict(cfg["backtest"])
    bc.update(start=args.start, end=args.end, stop_loss_pct=0.0, max_holding_days=args.hold,
              max_positions=args.top, max_picks_per_day=0, max_per_industry_per_day=0)
    cfg["backtest"] = bc
    cfg["trailing_stop"] = {"enabled": False}
    cfg["index_filter"] = {"enabled": False}
    cfg["universe"] = {"amount_top_n": 500, "amount_lookback": 250, "min_amount_ma20": 0}
    cfg["__universe_sets__"] = uni_sets
    cfg["__exposure_by_date__"] = {int(d): 1.0 for d in calendar}

    sigs = lowvol_signals_qfq(calendar, uni_sets, dfs, vol_window=args.vol_window, top_n=args.top)
    print("signals", len(sigs), flush=True)
    runner = PositionExposureRunner(tdx, cfg, sigs, universe_codes=union)
    result = runner.run()
    m = compute_metrics(result, tdx.get_benchmark("sh000300"))

    print("\n===== LOWVOL_QFQ V60 H25 N10 =====")
    print(f"period        {args.start} ~ {args.end}")
    print(f"ret           {m['total_return']:.4f}")
    print(f"annual        {m['annual_return']:.4f}")
    print(f"max_dd        {m['max_drawdown']:.4f}")
    print(f"sharpe        {m['sharpe']:.2f}")
    print(f"sortino       {m['sortino']:.2f}")
    print(f"calmar        {m['calmar']:.2f}")
    print(f"pf            {m['profit_factor']:.2f}")
    print(f"win_rate      {m['win_rate']:.4f}")
    print(f"trades        {m['trade_count']}")
    print(f"bench_ret     {m['benchmark_return']:.4f}")

    eq = pd.Series([e for d, e in result["equity_curve"]])
    eq.index = pd.to_datetime([str(d) for d, e in result["equity_curve"]], format="%Y%m%d")
    eq = eq[~eq.index.duplicated(keep="last")].sort_index()
    monthly = eq.resample("ME").last().pct_change().dropna()
    print(f"months_pos    {int((monthly > 0).sum())}/{len(monthly)}")
    print("monthly returns:")
    for dt, r in monthly.items():
        print(f"  {dt:%Y-%m} {r:+.4f}")


if __name__ == "__main__":
    main()
