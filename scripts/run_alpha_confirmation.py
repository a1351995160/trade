"""Alpha Confirmation Phase 主运行器。

全引擎 Dynamic Exposure、Timing Ablation、Signal Delay、LowVol 参数稳定性、Holding 稳定性。
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from chanlun_trader.config import load_config
from chanlun_trader.exposure_runner import PositionExposureRunner
from chanlun_trader.market_state import build_market_state, exposure_from_rule
from chanlun_trader.metrics import compute_metrics
from chanlun_trader.tdx_data import TdxData
from decomp_lib import build_universe_data, selection_signals

FIELD_NAMES = [
    "part", "strategy_id", "rule", "selection", "hold_days", "vol_window", "top_n",
    "trade_count", "win_rate", "total_return", "annual_return", "max_drawdown",
    "sharpe", "sortino", "calmar", "profit_factor", "avg_holding_days",
    "exposure_mean", "switch_count", "turnover_est", "benchmark_return", "timestamp",
]


def delayed_exposure(ms, rule, delay, calendar):
    """返回按 delay 个交易日延迟的 exposure map（key=exec date 的 state date）。"""
    base = exposure_from_rule(ms, rule)
    if delay <= 0:
        return base
    out = {}
    for i, d in enumerate(calendar):
        j = i - delay
        src = calendar[j] if j >= 0 else calendar[0]
        out[int(d)] = base.get(int(src), 1.0)
    return out


def turnover_from_trades(trades, equity_curve):
    if not trades:
        return 0.0
    eq = pd.Series([e for d, e in equity_curve])
    avg_eq = float(eq.mean()) if len(eq) else 1.0
    buy_notional = sum(t.shares * t.buy_price for t in trades)
    years = 2.0
    return buy_notional / years / avg_eq


def run_full(tdx, cfg, signals, union, benchmark, expo_map, max_positions=10, max_hold=20):
    cfg = dict(cfg)
    cfg["__exposure_by_date__"] = expo_map
    cfg["backtest"]["max_positions"] = max_positions
    cfg["backtest"]["max_holding_days"] = max_hold
    runner = PositionExposureRunner(tdx, cfg, signals, universe_codes=union)
    result = runner.run()
    m = compute_metrics(result, benchmark)
    return result, m


def row_of(part, sid, rule, selection, hold, vol_window, top_n, m, result, expo_map, switch_count):
    return {
        "part": part, "strategy_id": sid, "rule": rule, "selection": selection,
        "hold_days": hold, "vol_window": vol_window, "top_n": top_n,
        "trade_count": m["trade_count"], "win_rate": f"{m['win_rate']:.4f}",
        "total_return": f"{m['total_return']:.4f}", "annual_return": f"{m['annual_return']:.4f}",
        "max_drawdown": f"{m['max_drawdown']:.4f}", "sharpe": f"{m['sharpe']:.4f}",
        "sortino": f"{m['sortino']:.4f}", "calmar": f"{m['calmar']:.4f}",
        "profit_factor": f"{m['profit_factor']:.4f}",
        "avg_holding_days": f"{m['avg_holding_days']:.1f}",
        "exposure_mean": f"{float(np.mean(list(expo_map.values()))):.3f}",
        "switch_count": switch_count,
        "turnover_est": f"{turnover_from_trades(result['trades'], result['equity_curve']):.2f}",
        "benchmark_return": f"{m['benchmark_return']:.4f}",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


def switch_count(expo_map):
    keys = sorted(expo_map.keys())
    prev = None
    n = 0
    for k in keys:
        v = round(expo_map[k], 2)
        if prev is not None and v != prev:
            n += 1
        prev = v
    return n


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="2022-08-01")
    parser.add_argument("--end", type=str, default="2024-07-31")
    parser.add_argument("--only", type=str, default="")
    args = parser.parse_args()

    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
    calendar, uni_sets, union, dfs, idx = build_universe_data(tdx, cfg, args.start, args.end, top_n=500)
    ms = build_market_state(calendar, uni_sets, dfs, idx)
    benchmark = tdx.get_benchmark(cfg["backtest"].get("benchmark", "sh000300"))
    print(f"Calendar {len(calendar)}, union {len(union)}", flush=True)

    cfg["backtest"]["start"] = args.start
    cfg["backtest"]["end"] = args.end
    cfg["backtest"]["stop_loss_pct"] = 0.0
    cfg["backtest"]["max_holding_days"] = 20
    cfg["backtest"]["max_picks_per_day"] = 0
    cfg["backtest"]["max_per_industry_per_day"] = 0
    cfg["trailing_stop"]["enabled"] = False
    cfg["index_filter"]["enabled"] = False
    cfg["universe"]["amount_top_n"] = 500
    cfg["universe"]["amount_lookback"] = 250
    cfg["universe"]["min_amount_ma20"] = 0
    cfg["__universe_sets__"] = uni_sets

    ledger = Path("experiments/alpha_confirmation_ledger.csv")
    if not ledger.exists():
        with open(ledger, "w", newline="", encoding="utf-8-sig") as f:
            f.write(",".join(FIELD_NAMES) + "\n")

    def record(row):
        with open(ledger, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=FIELD_NAMES, extrasaction="ignore")
            w.writerow(row)

    base_signals = selection_signals(calendar, uni_sets, dfs, "SEL_MOM20", hold_days=20, max_picks=10)

    # Part 1: Full Engine Dynamic Exposure on SEL_MOM20 + Timing Ablation
    if not args.only or args.only == "exposure":
        print("=== PART1 FULL ENGINE EXPOSURE / TIMING ABLATION ===", flush=True)
        rules = [
            "ALWAYS", "IDX_MA20", "BREADTH_MA20", "IDX_VOL20",
            "COMPOSITE_TBV", "COMPOSITE_TB", "COMPOSITE_TV", "COMPOSITE_BV",
            "COMPOSITE_TBV_3S", "COMPOSITE_TBV_100_30_0", "COMPOSITE_TBV_100_50_20", "CASH",
        ]
        for rule in rules:
            em = exposure_from_rule(ms, rule)
            result, m = run_full(tdx, cfg, base_signals, union, benchmark, em, max_positions=10, max_hold=20)
            sc = switch_count(em)
            print(
                f"EXP {rule}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} "
                f"pf={m['profit_factor']:.2f} trades={m['trade_count']} switch={sc} expo={float(np.mean(list(em.values()))):.3f}",
                flush=True,
            )
            record(row_of("EXPOSURE", rule, rule, "SEL_MOM20", 20, 20, 10, m, result, em, sc))

    # Part 2: Signal Delay 0/1/2/3
    if not args.only or args.only == "delay":
        print("=== PART2 SIGNAL DELAY ===", flush=True)
        for rule in ["COMPOSITE_TBV", "COMPOSITE_TBV_3S"]:
            for delay in [0, 1, 2, 3]:
                em = delayed_exposure(ms, rule, delay, calendar)
                result, m = run_full(tdx, cfg, base_signals, union, benchmark, em, max_positions=10, max_hold=20)
                sc = switch_count(em)
                print(
                    f"DELAY {rule} +{delay}d: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} "
                    f"sharpe={m['sharpe']:.2f} trades={m['trade_count']} switch={sc}",
                    flush=True,
                )
                record(row_of("DELAY", f"{rule}_D{delay}", rule, "SEL_MOM20", 20, 20, 10, m, result, em, sc))

    # Part 3: LowVol 参数稳定性（vol window / top N）
    if not args.only or args.only == "lowvol":
        print("=== PART3 LOWVOL STABILITY ===", flush=True)
        # 需要按 vol_window 生成不同 LowVol 信号。复用 decomp_lib 的选择函数，通过临时修改策略 id。
        for vw in [20, 30, 40, 60, 80]:
            # 用 selection_signals 的 SEL_LOWVOL 逻辑，但波动窗口固定 20；这里为窗口稳定性，写专用生成。
            sigs = lowvol_signals_var(calendar, uni_sets, dfs, vol_window=vw, top_n=10)
            result, m = run_full(tdx, cfg, sigs, union, benchmark, {int(d): 1.0 for d in calendar}, max_positions=10, max_hold=20)
            print(f"LOWVOL vw={vw}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f}", flush=True)
            record(row_of("LOWVOL_STAB", f"LOWVOL_{vw}", "ALWAYS", "LOWVOL", 20, vw, 10, m, result, {int(d): 1.0 for d in calendar}, 0))
        for tn in [5, 10, 20, 30]:
            sigs = lowvol_signals_var(calendar, uni_sets, dfs, vol_window=20, top_n=tn)
            result, m = run_full(tdx, cfg, sigs, union, benchmark, {int(d): 1.0 for d in calendar}, max_positions=tn, max_hold=20)
            print(f"LOWVOL topN={tn}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f}", flush=True)
            record(row_of("LOWVOL_STAB", f"LOWVOL_TOPN{tn}", "ALWAYS", "LOWVOL", 20, 20, tn, m, result, {int(d): 1.0 for d in calendar}, 0))

    # Part 4: Holding 稳定性（LowVol）
    if not args.only or args.only == "holding":
        print("=== PART4 HOLDING STABILITY ===", flush=True)
        sigs = lowvol_signals_var(calendar, uni_sets, dfs, vol_window=20, top_n=10)
        for hd in [10, 15, 20, 25, 30, 40]:
            result, m = run_full(tdx, cfg, sigs, union, benchmark, {int(d): 1.0 for d in calendar}, max_positions=10, max_hold=hd)
            print(f"HOLD {hd}d: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f}", flush=True)
            record(row_of("HOLDING", f"HOLD_{hd}", "ALWAYS", "LOWVOL", hd, 20, 10, m, result, {int(d): 1.0 for d in calendar}, 0))

    # Part 5: Selection 在相同 Exposure 下的独立贡献
    if not args.only or args.only == "selection":
        print("=== PART5 SELECTION UNDER SAME EXPOSURE ===", flush=True)
        em = exposure_from_rule(ms, "COMPOSITE_TBV")
        for sid in ["SEL_RANDOM", "SEL_MOM20", "SEL_RS120", "SEL_LOWVOL"]:
            sigs = selection_signals(calendar, uni_sets, dfs, sid, hold_days=20, max_picks=10)
            result, m = run_full(tdx, cfg, sigs, union, benchmark, em, max_positions=10, max_hold=20)
            print(f"SEL {sid} @TBV: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f}", flush=True)
            record(row_of("SELECTION_EXP", sid, "COMPOSITE_TBV", sid, 20, 20, 10, m, result, em, switch_count(em)))

    print("ALPHA CONFIRMATION RUN FINISHED", flush=True)


def lowvol_signals_var(calendar, uni_sets, dfs, vol_window=20, top_n=10):
    """按指定波动窗口生成 LowVol 选股信号（固定 20 日持有，无止损）。

    波动率 = close.pct_change().rolling(vol_window).std()，与分解阶段 SEL_LOWVOL 口径一致。
    """
    from chanlun_trader.chan import Signal
    pre = {}
    for code, df in dfs.items():
        if df.empty:
            continue
        px = df["qfq_close"] if "qfq_close" in df.columns else df["close"]
        vol = px.pct_change().rolling(vol_window).std().to_numpy()
        pre[code] = (df["date"].to_numpy(), px.to_numpy(), vol)
    out = []
    for d in calendar:
        members = uni_sets.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, close_arr, vol_arr = item
            pos = np.searchsorted(dates, d, side="right") - 1
            if pos < vol_window:
                continue
            if int(dates[pos]) != int(d):
                continue
            c = float(close_arr[pos])
            vol = vol_arr[pos]
            if pd.isna(vol):
                continue
            rows.append((code, -float(vol), c, pos))
        if not rows:
            continue
        rows.sort(key=lambda x: -x[1])
        for code, score, c, pos in rows[:top_n]:
            out.append(Signal(code=code, signal_date=int(d), direction="buy", signal_type="LOWVOL",
                              price_ref=c, stop_low=0.0, score=score))
    return out


if __name__ == "__main__":
    main()
