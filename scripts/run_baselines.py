"""Research Engine Sanity Baselines。"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chanlun_trader.chan import Signal
from chanlun_trader.config import load_config
from chanlun_trader.metrics import compute_metrics
from chanlun_trader.strategy_runner import CustomBacktestRunner
from chanlun_trader.tdx_data import TdxData, list_a_stocks


def _next_day(cal, d):
    idx = cal.index(d) if d in cal else None
    if idx is None:
        import bisect
        idx = bisect.bisect_right(cal, d) - 1
    return cal[idx + 1] if idx is not None and idx + 1 < len(cal) else None


def _add_days(cal, d, n):
    import bisect
    idx = bisect.bisect_right(cal, d) - 1
    return cal[idx + n] if idx >= 0 and idx + n < len(cal) else None


def baseline_001_random(cfg, dfs, calendar):
    out = []
    uni_sets = cfg.get("__universe_sets__", {})
    rng = random.Random(42)
    for d in calendar:
        members = sorted(uni_sets.get(d, []))
        if len(members) < 10:
            picks = members
        else:
            picks = rng.sample(members, 10)
        for code in picks:
            df = dfs.get(code)
            if df is None or df.empty:
                continue
            pos = df["date"].searchsorted(d, side="right") - 1
            if pos < 0 or int(df.iloc[pos]["date"]) != d:
                continue
            c = float(df.iloc[pos]["close"])
            out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="BASE_001",
                              price_ref=c, stop_low=0.0, score=float(rng.random())))
            exit_sig = _add_days(calendar, d, 10)
            if exit_sig:
                out.append(Signal(code=code, signal_date=exit_sig, direction="sell", signal_type="SX",
                                  price_ref=0.0, stop_low=0.0, score=0.0))
    return out


def baseline_002_buyhold(cfg, dfs, calendar):
    out = []
    uni_sets = cfg.get("__universe_sets__", {})
    first = None
    for d in calendar:
        if uni_sets.get(d):
            first = d
            break
    if first is None:
        return out
    for code in sorted(uni_sets.get(first, [])):
        df = dfs.get(code)
        if df is None or df.empty:
            continue
        pos = df["date"].searchsorted(first, side="right") - 1
        if pos < 0 or int(df.iloc[pos]["date"]) != first:
            continue
        c = float(df.iloc[pos]["close"])
        out.append(Signal(code=code, signal_date=first, direction="buy", signal_type="BASE_002",
                          price_ref=c, stop_low=0.0, score=float(pos)))
    return out


def baseline_003_ma20(cfg, dfs, calendar):
    out = []
    for code, df in dfs.items():
        c = df["close"]
        ma20 = c.rolling(20).mean()
        mask = c > ma20
        idx = np.where(mask)[0]
        for i in idx:
            if i < 20 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="BASE_003",
                                  price_ref=float(c.iloc[i]), stop_low=float(c.iloc[i] * 0.95), score=float(c.iloc[i] / ma20.iloc[i] - 1)))
                exit_sig = _add_days(calendar, d, 10)
                if exit_sig:
                    out.append(Signal(code=code, signal_date=exit_sig, direction="sell", signal_type="SX",
                                      price_ref=0.0, stop_low=0.0, score=0.0))
    return out


def baseline_004_mom20_rank(cfg, dfs, calendar):
    out = []
    uni_sets = cfg.get("__universe_sets__", {})
    for d in calendar:
        rows = []
        members = uni_sets.get(d)
        for code, df in dfs.items():
            if members is not None and code not in members:
                continue
            if len(df) < 21:
                continue
            pos = df["date"].searchsorted(d, side="right") - 1
            if pos < 20:
                continue
            if int(df.iloc[pos]["date"]) != d:
                continue
            mom = float(df.iloc[pos]["close"] / df.iloc[pos - 20]["close"] - 1)
            rows.append((code, mom, pos))
        if not rows:
            continue
        rows.sort(key=lambda x: -x[1])
        for code, mom, pos in rows[:10]:
            c = float(dfs[code].iloc[pos]["close"])
            out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="BASE_004",
                              price_ref=c, stop_low=float(c * 0.95), score=mom))
            exit_sig = _add_days(calendar, d, 10)
            if exit_sig:
                out.append(Signal(code=code, signal_date=exit_sig, direction="sell", signal_type="SX",
                                  price_ref=0.0, stop_low=0.0, score=0.0))
    return out

BASELINES = {
    "BASELINE_001": baseline_001_random,
    "BASELINE_002": baseline_002_buyhold,
    "BASELINE_003": baseline_003_ma20,
    "BASELINE_004": baseline_004_mom20_rank,
    "BASELINE_005": lambda cfg, dfs, calendar: [],
}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-n", type=int, default=500)
    parser.add_argument("--start", type=str, default="2022-08-01")
    parser.add_argument("--end", type=str, default="2024-07-31")
    args = parser.parse_args()

    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
    bench = tdx.get_benchmark("sh000001")
    calendar = [int(d) for d in bench["date"] if int(args.start.replace("-", "")) <= int(d) <= int(args.end.replace("-", ""))]

    stocks = list_a_stocks(tdx.vipdoc)
    amount_trail = {}
    for st in stocks:
        raw = tdx.get_day(st["code"], st["market"])
        if raw.empty or len(raw) < 120:
            continue
        raw = raw.sort_values("date")
        dates = raw["date"].astype(np.int64)
        amount_trail[st["code"]] = pd.Series(raw["amount"].rolling(250).mean().to_numpy(), index=dates)
    uni_sets = {}
    union = set()
    for d in calendar:
        vals = []
        for code, ser in amount_trail.items():
            pos = ser.index.searchsorted(d, side="right") - 1
            if pos < 0:
                continue
            v = ser.iloc[pos]
            if pd.notna(v):
                vals.append((code, float(v)))
        vals.sort(key=lambda x: -x[1])
        top = {c for c, v in vals[: args.top_n]}
        uni_sets[d] = top
        union |= top

    dfs = {}
    code_market = {s["code"]: s["market"] for s in stocks}
    for code in union:
        raw = tdx.get_day(code, code_market[code])
        if raw.empty:
            continue
        dfs[code] = raw.sort_values("date").reset_index(drop=True)

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

    for sid, func in BASELINES.items():
        cfg["backtest"]["max_positions"] = 10
        if sid == "BASELINE_002":
            cfg["backtest"]["max_positions"] = args.top_n
        signals = func(cfg, dfs, calendar)
        runner = CustomBacktestRunner(tdx, cfg, signals, universe_codes=sorted(union))
        result = runner.run()
        m = compute_metrics(result, tdx.get_benchmark(cfg["backtest"].get("benchmark", "sh000300")))
        print(
            f"{sid}: sigs={len(signals)} trades={m['trade_count']} win={m['win_rate']:.4f} "
            f"ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} "
            f"pf={m['profit_factor']:.2f} avg_hold={m['avg_holding_days']:.1f}",
            flush=True,
        )


if __name__ == "__main__":
    main()
