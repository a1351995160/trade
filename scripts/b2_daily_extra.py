"""B2: run the two computed-helper supported daily hypotheses not stored in feature table."""
from __future__ import annotations
import sys, time
sys.path.insert(0, "src")
import numpy as np
import pandas as pd
from b1_strategy_translation import (build_store, run_engine, metrics_from_result, trades_from_result,
                                     FT_PATH, DAILY_PATH, OUT, date_to_ts)
from chanlun_trader.engine.signal import Signal, Side, ExecutionPolicy

def main():
    t0 = time.time()
    ft = pd.read_parquet(FT_PATH)
    daily = pd.read_parquet(DAILY_PATH)
    calendar = sorted(ft["date"].unique().tolist())
    rebal = calendar[::5]
    ft["rev20_lowvolcomp"] = -ft["r20"] * (ft["vol_comp"] < 1.0).astype(float)
    ft["rev20_lowturn"] = -ft["r20"] * (ft["turnover_proxy"] < ft.groupby("date")["turnover_proxy"].transform("median")).astype(float)
    rows = []
    for hid, col in [("A4-012", "rev20_lowvolcomp"), ("A4-214", "rev20_lowturn")]:
        sid = f"S_{col}_T100_H5"
        fac = ft[["symbol", "date", col]].rename(columns={"date": "timestamp", col: "value"}).dropna()
        fac = fac[np.isfinite(fac["value"])]
        fac = fac[fac["timestamp"].isin(rebal)]
        top = fac.sort_values(["timestamp", "value", "symbol"], ascending=[True, False, True]).groupby("timestamp").head(100)
        syms = sorted(top["symbol"].unique())
        store = build_store(daily, syms)
        sigs = []
        n = 0
        for _, row in top.iterrows():
            d = int(row["timestamp"])
            sigs.append(Signal(strategy_id=sid, signal_id=f"{sid}:{row['symbol']}:{d}:{n}",
                               symbol=row["symbol"], generated_at=date_to_ts(d, 15, 0),
                               direction=Side.BUY, execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
                               score=float(row["value"])))
            n += 1
        res = run_engine(store, calendar, sigs, sid, max_positions=100, max_holding_days=5)
        m = metrics_from_result(res)
        m.update(dict(strategy_id=sid, hypothesis_id=hid, factor_id=col, n_signals=int(len(sigs)), status="REJECTED"))
        m["status"] = "PROMISING" if (m["profit_factor"] > 1 and m["total_return"] > 0 and m["sharpe"] >= 0.3) else "REJECTED"
        rows.append(m)
        print(m["strategy_id"], m["hypothesis_id"], m["total_return"], m["profit_factor"], m["sharpe"], m["status"], flush=True)
        trades_from_result(res).to_parquet(OUT / f"trades_{sid}.parquet", index=False)
    out = pd.DataFrame(rows)
    out.to_parquet(OUT / "daily_extra_results.parquet", index=False)
    out.to_csv("reports/DAILY_SUPPORTED_TRANSLATION_RESULTS_EXTRA.csv", index=False)
    print("saved", len(out), round(time.time()-t0,1), flush=True)

if __name__ == "__main__":
    main()
