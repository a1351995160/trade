"""Walk-Forward 验证脚本（简化版）。

使用 config.data_split 中 validation/test 两个样本外区间，
用固定参数分别回测并汇总输出。训练区间仅用于参考。

用法：
    python validation/run_walk_forward.py [--stride N]
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
    parser.add_argument("--stride", type=int, default=0, help="只取每第 N 只股票，0 表示全市场")
    args = parser.parse_args()

    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
    orig = btmod.list_a_stocks
    if args.stride > 0:
        all_stocks = list_a_stocks(tdx.vipdoc)
        sample = all_stocks[:: args.stride]
        btmod.list_a_stocks = lambda vipdoc: list(sample)
        print(f"使用 stride={args.stride}，样本 {len(sample)} 只", flush=True)

    split = cfg["backtest"]["data_split"]
    periods = [
        ("train", split["train_start"], split["train_end"]),
        ("validation", split["validation_start"], split["validation_end"]),
        ("test", split["test_start"], split["test_end"]),
    ]
    for name, start, end in periods:
        cfg2 = load_config()
        cfg2["backtest"]["start"] = start
        cfg2["backtest"]["end"] = end
        t0 = time.time()
        runner = BacktestRunner(tdx, cfg2)
        result = runner.run(limit=None)
        metrics = compute_metrics(result, tdx.get_benchmark(cfg2["backtest"].get("benchmark", "sh000300")))
        print(
            f"{name} {start}~{end}: trades={metrics['trade_count']} win={metrics['win_rate']:.4f} "
            f"ret={metrics['total_return']:.4f} dd={metrics['max_drawdown']:.4f} "
            f"sharpe={metrics['sharpe']:.2f} pf={metrics['profit_factor']:.2f} "
            f"({time.time() - t0:.0f}s)",
            flush=True,
        )
    btmod.list_a_stocks = orig


if __name__ == "__main__":
    main()
