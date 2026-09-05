"""Build daily_all.parquet cache for all A-share daily bars 2021-08-01..2025-07-31."""
import sys, time
from pathlib import Path
sys.path.insert(0, "src")
import pandas as pd
from chanlun_trader.config import load_config
from chanlun_trader.tdx_data import TdxData, list_a_stocks

def main():
    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cache_dir=cfg["tdx"].get("cache_dir"))
    stocks = list_a_stocks(tdx.vipdoc)
    out = Path("data/research/daily_all.parquet")
    rows = []
    t0 = time.time()
    for i, st in enumerate(stocks):
        code, market = st["code"], st["market"]
        raw = tdx.get_day(code, market)
        if raw is None or raw.empty:
            continue
        raw = raw[(raw["date"] >= 20210801) & (raw["date"] <= 20250731)].copy()
        if raw.empty:
            continue
        sym = f"{code}.{'SH' if market == 1 else 'SZ'}"
        raw["symbol"] = sym
        raw["prev_close"] = raw["close"].shift(1)
        rows.append(raw[["symbol", "date", "open", "high", "low", "close", "volume", "amount", "prev_close"]])
        if i % 500 == 0:
            print(i, len(stocks), f"{time.time()-t0:.0f}s", flush=True)
    df = pd.concat(rows, ignore_index=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print("saved", out, len(df), "rows", df["symbol"].nunique(), "symbols")

if __name__ == "__main__":
    main()
