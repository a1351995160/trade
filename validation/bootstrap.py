"""交易 Bootstrap / 随机重排：对历史交易顺序做 Monte Carlo。

用法：
    python validation/bootstrap.py --trades data/output/trades_XXXX.csv --n 1000
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", type=str, required=True)
    parser.add_argument("--n", type=int, default=1000)
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0)
    args = parser.parse_args()

    df = pd.read_csv(args.trades)
    pnl = df["pnl"].to_numpy(dtype=float)
    returns = []
    max_dds = []
    rng = np.random.default_rng(42)
    for _ in range(args.n):
        seq = rng.choice(pnl, size=len(pnl), replace=True)
        eq = args.initial_cash + np.cumsum(seq)
        ret = eq[-1] / args.initial_cash - 1.0
        cummax = np.maximum.accumulate(eq)
        dd = float((eq / cummax - 1.0).min())
        returns.append(ret)
        max_dds.append(dd)
    returns = np.array(returns)
    max_dds = np.array(max_dds)
    print(f"bootstrap n={args.n}")
    print(f"return median={np.median(returns):.4f} 5%={np.percentile(returns, 5):.4f} 95%={np.percentile(returns, 95):.4f}")
    print(f"P(return>0)={(returns > 0).mean():.4f}")
    print(f"maxDD median={np.median(max_dds):.4f} 5%={np.percentile(max_dds, 5):.4f}")
    print(f"P(maxDD<-0.20)={(max_dds < -0.20).mean():.4f}")


if __name__ == "__main__":
    main()
