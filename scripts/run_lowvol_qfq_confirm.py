"""LowVol 候选的 qfq 特征口径确认。

原 lowvol_signals_var 用 raw close 计算波动率；分红/送转会造成波动率失真。
本脚本用 qfq_close 计算波动率，并按冻结参数重跑候选的关键实验。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from chanlun_trader.config import load_config
from chanlun_trader.chan import Signal
from chanlun_trader.exposure_runner import PositionExposureRunner
from chanlun_trader.metrics import compute_metrics
from chanlun_trader.tdx_data import TdxData, list_a_stocks
from decomp_lib import build_universe_data


def build_universe_qfq(tdx, cfg, start, end, top_n=500, amount_lookback=250):
    """build_universe_data + qfq_close 列（波动率/收益类特征统一用前复权）。"""
    calendar, uni_sets, union, dfs, idx_close = build_universe_data(
        tdx, cfg, start, end, top_n=top_n, amount_lookback=amount_lookback
    )
    stocks = list_a_stocks(tdx.vipdoc)
    code_market = {s["code"]: s["market"] for s in stocks}
    for code in union:
        df = tdx.get_qfq_day(code, code_market[code])
        if df.empty:
            continue
        df = df.sort_values("date").reset_index(drop=True)
        dfs[code]["qfq_close"] = df["qfq_close"].to_numpy()
        dfs[code]["qfq_open"] = df["qfq_open"].to_numpy()
        dfs[code]["qfq_high"] = df["qfq_high"].to_numpy()
        dfs[code]["qfq_low"] = df["qfq_low"].to_numpy()
    return calendar, uni_sets, union, dfs, idx_close


def lowvol_signals_qfq(calendar, uni_sets, dfs, vol_window=60, top_n=10):
    """波动率 = qfq_close.pct_change().rolling(vol_window).std()。"""
    pre = {}
    for code, df in dfs.items():
        if df.empty or "qfq_close" not in df.columns:
            continue
        vol = df["qfq_close"].pct_change().rolling(vol_window).std().to_numpy()
        pre[code] = (df["date"].to_numpy(), df["qfq_close"].to_numpy(), vol)
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
            out.append(Signal(code=code, signal_date=int(d), direction="buy", signal_type="LOWVOL_QFQ",
                              price_ref=c, stop_low=0.0, score=score))
    return out


def run_spec(tdx, cfg, calendar, uni_sets, union, dfs, pr, vw=60, top_n=10, hold=25,
             cost_mult=1.0, delay=0):
    cfg = {k: v for k, v in cfg.items()}
    bc = dict(cfg["backtest"])
    bc.update(start=pr[0], end=pr[1], stop_loss_pct=0.0, max_holding_days=hold,
              max_positions=top_n, max_picks_per_day=0, max_per_industry_per_day=0,
              commission_rate=0.00025 * cost_mult,
              min_commission=5.0 if cost_mult >= 1 else 0.0,
              stamp_tax_rate=0.0005 * cost_mult, slippage=0.001 * cost_mult)
    cfg["backtest"] = bc
    cfg["trailing_stop"] = {"enabled": False}
    cfg["index_filter"] = {"enabled": False}
    cfg["universe"] = {"amount_top_n": 500, "amount_lookback": 250, "min_amount_ma20": 0}
    cfg["__universe_sets__"] = uni_sets
    cfg["__exposure_by_date__"] = {int(d): 1.0 for d in calendar}
    sigs = lowvol_signals_qfq(calendar, uni_sets, dfs, vol_window=vw, top_n=top_n)
    if delay:
        cm = {d: i for i, d in enumerate(calendar)}
        ns = []
        for s in sigs:
            i = cm.get(int(s.signal_date))
            if i is None or i + delay >= len(calendar):
                continue
            s.signal_date = int(calendar[i + delay])
            ns.append(s)
        sigs = ns
    r = PositionExposureRunner(tdx, cfg, sigs, universe_codes=union).run()
    m = compute_metrics(r, tdx.get_benchmark("sh000300"))
    return m


def main():
    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
    TRAIN = ("2022-08-01", "2024-07-31")
    VAL = ("2024-08-01", "2025-07-31")

    print("build TRAIN qfq universe", flush=True)
    cal_tr, uni_tr, union_tr, dfs_tr, _ = build_universe_qfq(tdx, cfg, TRAIN[0], TRAIN[1])
    print("build VAL qfq universe", flush=True)
    cal_va, uni_va, union_va, dfs_va, _ = build_universe_qfq(tdx, cfg, VAL[0], VAL[1])

    periods = {"TRAIN": (TRAIN, cal_tr, uni_tr, union_tr, dfs_tr),
               "VALIDATION": (VAL, cal_va, uni_va, union_va, dfs_va)}

    # 1) 冻结参数 base
    for pname, (pr, cal, uni, union, dfs) in periods.items():
        m = run_spec(tdx, cfg, cal, uni, union, dfs, pr)
        print(f"BASE {pname}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f} trades={m['trade_count']}", flush=True)

    # 2) TRAIN 参数网格
    for vw in [40, 60, 80]:
        for hold in [20, 30, 40]:
            for topn in [10, 20]:
                m = run_spec(tdx, cfg, cal_tr, uni_tr, union_tr, dfs_tr, TRAIN, vw=vw, top_n=topn, hold=hold)
                print(f"GRID vw{vw}_h{hold}_n{topn}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f}", flush=True)

    # 3) 成本/延迟压力
    for pname, (pr, cal, uni, union, dfs) in periods.items():
        for cm in [2.0, 3.0]:
            m = run_spec(tdx, cfg, cal, uni, union, dfs, pr, cost_mult=cm)
            print(f"COST x{cm} {pname}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f}", flush=True)
        for dly in [1, 2]:
            m = run_spec(tdx, cfg, cal, uni, union, dfs, pr, delay=dly)
            print(f"DELAY {dly} {pname}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f}", flush=True)

    # 4) 半年度
    periods2 = {
        "H1": ("2022-08-01", "2023-01-31"), "H2": ("2023-02-01", "2023-07-31"),
        "H3": ("2023-08-01", "2024-01-31"), "H4": ("2024-02-01", "2024-07-31"),
        "V1": ("2024-08-01", "2025-01-31"), "V2": ("2025-02-01", "2025-07-31"),
    }
    for name, (s, e) in periods2.items():
        cal, uni, union, dfs, _ = build_universe_qfq(tdx, cfg, s, e)
        m = run_spec(tdx, cfg, cal, uni, union, dfs, (s, e))
        print(f"HALF {name}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f} bench={m['benchmark_return']:.4f}", flush=True)

    print("QFQ CONFIRM FINISHED", flush=True)


if __name__ == "__main__":
    main()
