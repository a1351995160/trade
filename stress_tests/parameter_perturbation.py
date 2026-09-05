"""参数扰动测试：对关键参数周围取值分别回测，观察是否出现参数平台。

用法：
    python stress_tests/parameter_perturbation.py --stride N --start 2022-08-01 --end 2024-07-31
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
    parser.add_argument("--start", type=str, default="2022-08-01")
    parser.add_argument("--end", type=str, default="2024-07-31")
    args = parser.parse_args()

    base_cfg = load_config()
    tdx = TdxData(base_cfg["tdx"]["vipdoc"], base_cfg["tdx"]["gbbq"], base_cfg["tdx"].get("cache_dir"))
    orig = btmod.list_a_stocks
    if args.stride > 0:
        all_stocks = list_a_stocks(tdx.vipdoc)
        sample = all_stocks[:: args.stride]
        btmod.list_a_stocks = lambda vipdoc: list(sample)
        print(f"stride={args.stride}，样本 {len(sample)} 只", flush=True)

    grid = [
        ("stop_loss_pct", [0.02, 0.03, 0.04]),
        ("b3_breakout_vol_ratio", [1.5, 2.0, 2.5]),
        ("b3_zg_margin", [0.0, 0.02, 0.04]),
        ("trend_ma_fast", [0, 5, 10]),
    ]
    for key, values in grid:
        for val in values:
            cfg = load_config()
            cfg["backtest"]["start"] = args.start
            cfg["backtest"]["end"] = args.end
            if key == "stop_loss_pct":
                cfg["backtest"]["stop_loss_pct"] = val
            else:
                cfg["signal_filter"][key] = val
            t0 = time.time()
            result = BacktestRunner(tdx, cfg).run(limit=None)
            m = compute_metrics(result, tdx.get_benchmark(cfg["backtest"].get("benchmark", "sh000300")))
            print(
                f"{key}={val}: trades={m['trade_count']} win={m['win_rate']:.4f} ret={m['total_return']:.4f} "
                f"dd={m['max_drawdown']:.4f} pf={m['profit_factor']:.2f} ({time.time() - t0:.0f}s)",
                flush=True,
            )
    btmod.list_a_stocks = orig


if __name__ == "__main__":
    main()
