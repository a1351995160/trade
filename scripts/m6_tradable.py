"""Tradable forward returns: event at T -> entry T+1 open -> exit T+h close."""
from __future__ import annotations
import sys, time
sys.path.insert(0, "src")
sys.path.insert(0, "scripts")
import pandas as pd
from m6_lib import load_daily

def main():
    daily = load_daily()
    rows = []
    t0 = time.time()
    for sym, g in daily.groupby("symbol", sort=True):
        g = g.sort_values("date").reset_index(drop=True)
        dates = g["date"].to_numpy(dtype=int)
        open_ = g["open"].to_numpy(dtype=float)
        close = g["close"].to_numpy(dtype=float)
        for i in range(len(g)):
            d = int(dates[i])
            for h in (1, 2, 3, 5, 7, 10):
                entry = i + 1
                exit_ = entry + h - 1
                if exit_ >= len(g) or open_[entry] <= 0 or close[exit_] <= 0:
                    continue
                rows.append({"symbol": sym, "timestamp": d, "horizon": h,
                             "tradable_return": float(close[exit_] / open_[entry] - 1.0),
                             "available_at": int(dates[exit_])})
    df = pd.DataFrame(rows)
    df.to_parquet("data/research/tradable_returns.parquet", index=False)
    print("saved", df.shape, "rows", time.time()-t0)

if __name__ == "__main__":
    main()
