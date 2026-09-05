"""Compute tradable forward return labels incl. h20 and save long table."""
from __future__ import annotations
import sys, time
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import pandas as pd
from m6_lib import load_daily

def main():
    t0 = time.time()
    daily = load_daily()
    daily = daily[daily["date"] <= 20250731]
    print("daily loaded", daily.shape, round(time.time()-t0,1), flush=True)
    rows = []
    for sym, g in daily.groupby("symbol", sort=True):
        g = g.sort_values("date").reset_index(drop=True)
        dates = g["date"].to_numpy(dtype=int)
        open_ = g["open"].to_numpy(dtype=float)
        close = g["close"].to_numpy(dtype=float)
        for i in range(len(g)):
            d = int(dates[i])
            entry = i + 1
            exit_ = entry + 20 - 1
            if exit_ >= len(g) or open_[entry] <= 0 or close[exit_] <= 0:
                continue
            rows.append({"symbol": sym, "timestamp": d, "horizon": 20,
                         "tradable_return": float(close[exit_] / open_[entry] - 1.0),
                         "available_at": int(dates[exit_])})
    h20 = pd.DataFrame(rows)
    h20.to_parquet("data/research/tradable_returns_h20.parquet", index=False)
    print("h20 saved", h20.shape, "elapsed", round(time.time()-t0,1), flush=True)

if __name__ == "__main__":
    main()
