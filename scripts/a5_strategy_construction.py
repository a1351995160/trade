"""A5: deterministic long-only strategy construction from ROBUST_PRETEST factors (BT_ENGINE_V2).

Direction: long high factor value (reversal losers etc). Top100, weekly rebalance (5 sessions), holding 5 days.
Config: initial_cash 10M CNY, max_positions=100, NEXT_SESSION_OPEN, commission 2.5bp min5, stamp 5bp sell, slippage 10bp.
"""
from __future__ import annotations
import json, time
from pathlib import Path
import numpy as np
import pandas as pd
from chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from chanlun_trader.engine.signal import Signal, Side, ExecutionPolicy
from chanlun_trader.engine.time_types import tz_aware
from chanlun_trader.engine.asof import MarketDataStore

FT = "data/research/robustification_results/a4_feature_train.parquet"
DAILY = "data/research/daily_all.parquet"
CANDIDATES = ["F_REV20","F_REV5","F_UP5_INV","F_IND_DISP_LOW","R2_REV10","R2_REV30",
              "R2_REV_COMPOSITE","R2_REV20_TOPLIQ","R2_LOWAMT_HIGHPRICE","vol20_low","volcomp_low_pricepos"]

def main():
    t0 = time.time()
    ft = pd.read_parquet(FT)
    daily = pd.read_parquet(DAILY)
    cal = sorted(ft["date"].unique().tolist())
    rebal_dates = cal[::5]
    results = []
    for col in CANDIDATES:
        if col not in ft.columns:
            results.append({"strategy_id": f"S_{col}_T100_H5", "factor_id": col, "status": "SKIPPED", "reason": "column missing"})
            continue
        name = f"S_{col}_T100_H5"
        fac = ft[["symbol","date",col]].rename(columns={"date":"timestamp",col:"value"}).dropna()
        fac = fac[np.isfinite(fac["value"])]
        fac = fac[fac["timestamp"].isin(rebal_dates)]
        top = fac.sort_values(["timestamp","value","symbol"], ascending=[True, False, True]).groupby("timestamp").head(100)
        syms = sorted(top["symbol"].unique())
        store = MarketDataStore()
        dsub = daily[(daily["date"]>=20220801)&(daily["date"]<=20240731)&(daily["symbol"].isin(syms))]
        for sym, df in dsub.groupby("symbol"):
            d = df.sort_values("date").set_index("date")[["open","high","low","close","volume","amount","prev_close"]]
            store.add_daily_raw(sym, d)
        sigs = []
        for _, row in top.iterrows():
            d = int(row["timestamp"])
            sigs.append(Signal(strategy_id=name, signal_id=f"{name}:{row['symbol']}:{d}:{len(sigs)}",
                symbol=row["symbol"], generated_at=tz_aware(d//10000,(d//100)%100,d%100,15,0),
                direction=Side.BUY, execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN, score=float(row["value"])))
        cfg = EngineConfig(initial_cash=10_000_000, max_positions=100, max_position_weight=0.01,
                           max_holding_days=5, mode="DAILY", enable_index_filter=False, index_filter_enabled=False)
        eng = BacktestEngineV2(store, cal, config=cfg)
        eng.add_signals(sigs)
        res = eng.run()
        sells = [t for t in res.ledger.trades if t.side == Side.SELL]
        wins = [t.realized_pnl for t in sells if t.realized_pnl > 0]
        losses = [t.realized_pnl for t in sells if t.realized_pnl <= 0]
        eq = pd.Series([float(s.equity) for s in res.ledger.snapshots])
        ret = eq.pct_change().dropna()
        sharpe = float(np.sqrt(252)*ret.mean()/ret.std(ddof=1)) if len(ret)>1 and ret.std(ddof=1)>0 else 0.0
        rec = {
            "strategy_id": name, "factor_id": col, "status": "PENDING",
            "top_n": 100, "holding_days": 5, "initial_cash": 10_000_000,
            "n_signals": len(sigs), "n_sells": len(sells),
            "total_return": float(eq.iloc[-1]/10_000_000-1),
            "max_drawdown": float((eq/eq.cummax()-1).min()),
            "sharpe": sharpe,
            "profit_factor": float(sum(wins)/abs(sum(losses))) if losses else None,
            "win_rate": float(len(wins)/len(sells)) if sells else None,
            "runtime_sec": round(time.time()-t0,1),
        }
        # A5 preliminary status: positive + sharpe>=0.3 => PROMISING else REJECTED
        rec["status"] = "PROMISING" if (rec["total_return"] > 0 and rec["sharpe"] >= 0.3) else "REJECTED"
        results.append(rec)
        print(json.dumps(rec, ensure_ascii=False), flush=True)
    out = pd.DataFrame(results)
    out.to_parquet("data/research/strategy_construction_results.parquet", index=False)
    out.to_csv("reports/STRATEGY_CONSTRUCTION_RESULTS.csv", index=False)
    print("saved", len(out), "rows in", round(time.time()-t0,1), "s")

if __name__ == "__main__":
    main()
