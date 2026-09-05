"""交易成本压力测试。

用法：
    python stress_tests/cost_stress.py --stride N --commission-mult 1 --slippage 0.001 --start 2022-08-01 --end 2024-07-31
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import chanlun_trader.backtest as btmod
from chanlun_trader.backtest import BacktestRunner
from chanlun_trader.config import load_config
from chanlun_trader.metrics import compute_metrics
from chanlun_trader.tdx_data import TdxData, list_a_stocks


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--stride", type=int, default=0)
    parser.add_argument("--commission-mult", type=float, default=1.0)
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--start", type=str, default="2022-08-01")
    parser.add_argument("--end", type=str, default="2024-07-31")
    args = parser.parse_args()

    cfg = load_config()
    cfg["backtest"]["start"] = args.start
    cfg["backtest"]["end"] = args.end
    cfg["backtest"]["commission_rate"] = cfg["backtest"]["commission_rate"] * args.commission_mult
    cfg["backtest"]["slippage"] = args.slippage
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
    orig = btmod.list_a_stocks
    if args.stride > 0:
        all_stocks = list_a_stocks(tdx.vipdoc)
        sample = all_stocks[:: args.stride]
        btmod.list_a_stocks = lambda vipdoc: list(sample)
        print(f"stride={args.stride}，样本 {len(sample)} 只", flush=True)

    t0 = time.time()
    result = BacktestRunner(tdx, cfg).run(limit=None)
    metrics = compute_metrics(result, tdx.get_benchmark(cfg["backtest"].get("benchmark", "sh000300")))
    print(
        f"commission_mult={args.commission_mult} slippage={args.slippage}: "
        f"trades={metrics['trade_count']} win={metrics['win_rate']:.4f} ret={metrics['total_return']:.4f} "
        f"dd={metrics['max_drawdown']:.4f} sharpe={metrics['sharpe']:.2f} pf={metrics['profit_factor']:.2f} "
        f"({time.time() - t0:.0f}s)",
        flush=True,
    )
    btmod.list_a_stocks = orig


if __name__ == "__main__":
    main()
