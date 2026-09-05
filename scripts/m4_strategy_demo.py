"""M4 STRATEGY PIPELINE DEMO — 3 个不同类型 Hypothesis 的 End-to-End 运行。

- 类型1: Momentum（5日动量）
- 类型2: Volatility（20日波动率）
- 类型3: Turnover（20日换手率代理）
每个因子用 PIT-safe qfq / raw 计算 -> FactorStore -> 每日 top5 信号 -> BT_ENGINE_V2 -> 指标。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

import pandas as pd

from chanlun_trader.config import load_config
from chanlun_trader.tdx_data import TdxData, list_a_stocks
from chanlun_trader.engine.data_loader import load_daily_store, load_index_closes
from chanlun_trader.engine.universe import UniverseService
from chanlun_trader.research.qfq import qfq_columns_asof
from chanlun_trader.research.factor import FactorStore
from chanlun_trader.research.backtest import build_signals_from_factor, run_v2_daily, metrics_from_result
from chanlun_trader.research.guard import ResearchDataAccessGuard

TRAIN = (20220801, 20240731)


def main() -> None:
    cfg = load_config()
    guard = ResearchDataAccessGuard()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cache_dir=cfg["tdx"].get("cache_dir"))
    stocks = list_a_stocks(tdx.vipdoc)[:20]
    symbols = [f"{s['code']}.{'SH' if s['market']==1 else 'SZ'}" for s in stocks]
    store = load_daily_store(tdx, symbols, start_date=TRAIN[0], end_date=TRAIN[1], load_qfq=False)
    cal = [int(d) for d in tdx.get_benchmark("sh000001")["date"] if TRAIN[0] <= int(d) <= TRAIN[1]]
    gbbq = tdx._load_gbbq()

    # Universe: 所有日期这些 symbols 均 eligible
    us = UniverseService()
    for d in cal:
        us.load_pit_sets({d: set(symbols)})

    factor_rows = []
    t0 = time.time()
    for st, sym in zip(stocks, symbols):
        code, market = st["code"], st["market"]
        raw = tdx.get_day(code, market)
        raw = raw[(raw["date"] >= TRAIN[0]) & (raw["date"] <= TRAIN[1])].reset_index(drop=True)
        if raw.empty:
            continue
        g = gbbq[gbbq["code"] == code].copy()
        dates = raw["date"].tolist()
        closes = raw["close"].to_numpy()
        vols = raw["volume"].to_numpy()
        amounts = raw["amount"].to_numpy()
        for idx, d in enumerate(dates):
            if idx < 21:
                continue
            # PIT qfq close series as-of d
            pit = qfq_columns_asof(raw, g, d)
            c_now = float(pit["qfq_close"].iloc[idx])
            c_prev5 = float(pit["qfq_close"].iloc[idx - 5])
            c_prev20 = float(pit["qfq_close"].iloc[idx - 20])
            mom5 = c_now / c_prev5 - 1.0 if c_prev5 > 0 else 0.0
            # 20日波动率：qfq 日收益 std
            rets = pit["qfq_close"].iloc[idx - 20:idx + 1].pct_change().dropna()
            vol20 = float(rets.std(ddof=0)) if len(rets) >= 5 else 0.0
            # 换手率代理：volume / amount? 用 amount 活跃度: amount 20日均值
            amt20 = float(amounts[idx - 20:idx + 1].mean())
            factor_rows.append({
                "symbol": sym, "timestamp": int(d),
                "value": float(mom5), "available_at": int(d), "factor_id": "F_MOM5", "factor_version": "v1",
            })
            factor_rows.append({
                "symbol": sym, "timestamp": int(d),
                "value": float(vol20), "available_at": int(d), "factor_id": "F_VOL20", "factor_version": "v1",
            })
            factor_rows.append({
                "symbol": sym, "timestamp": int(d),
                "value": float(amt20), "available_at": int(d), "factor_id": "F_AMT20", "factor_version": "v1",
            })
    factor_df = pd.DataFrame(factor_rows)
    print(f"factor rows={len(factor_df)} elapsed={time.time()-t0:.1f}s", flush=True)

    fstore = FactorStore(guard=guard)
    for fid in ("F_MOM5", "F_VOL20", "F_AMT20"):
        fstore.put(fid, "v1", factor_df[factor_df["factor_id"] == fid], allow_overwrite=True)

    out = []
    for fid, direction in (("F_MOM5", "long"), ("F_VOL20", "long"), ("F_AMT20", "long")):
        sub = factor_df[factor_df["factor_id"] == fid]
        sigs = build_signals_from_factor(sub, cal, strategy_id=f"DEMO_{fid}", top_n=5,
                                         start=TRAIN[0], end=TRAIN[1], direction_hint=direction)
        res = run_v2_daily(store, cal, sigs, max_positions=5, max_holding_days=5, universe=us)
        m = metrics_from_result(res)
        m["strategy_id"] = f"DEMO_{fid}"
        m["n_signals"] = len(sigs)
        m["n_orders"] = len(res.orders.orders)
        out.append(m)
        print(fid, {k: round(v, 4) if isinstance(v, float) else v for k, v in m.items()}, flush=True)

    out_df = pd.DataFrame(out)
    out_df.to_csv("reports/M4_STRATEGY_DEMO.csv", index=False)
    print(out_df.to_string())
    print("M4_DEMO_SAVED reports/M4_STRATEGY_DEMO.csv")


if __name__ == "__main__":
    main()
