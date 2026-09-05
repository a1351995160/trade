"""A6: Red Team stress on the 4 positive-return long-only strategies (label-based stress around BT_ENGINE_V2 base)."""
from __future__ import annotations
import json, sqlite3, time
from pathlib import Path
import numpy as np
import pandas as pd

FT = "data/research/robustification_results/a4_feature_train.parquet"
DAILY = "data/research/daily_all.parquet"
POSITIVE = ["F_IND_DISP_LOW","R2_REV30","R2_LOWAMT_HIGHPRICE","vol20_low"]
COMMISSION = 0.00025
STAMP = 0.0005
SLIPPAGE = 0.001

def main():
    t0=time.time()
    ft = pd.read_parquet(FT)
    daily = pd.read_parquet(DAILY, columns=["symbol","date","open","close"])
    daily = daily[(daily["date"]>=20220801)&(daily["date"]<=20240731)].sort_values(["symbol","date"]).reset_index(drop=True)
    cal = sorted(ft["date"].unique().tolist())
    rebal = cal[::5]
    # delay returns
    daily["open_d0"] = daily["open"]
    daily["open_d1"] = daily.groupby("symbol")["open"].shift(-1)
    daily["open_d2"] = daily.groupby("symbol")["open"].shift(-2)
    daily["open_d3"] = daily.groupby("symbol")["open"].shift(-3)
    daily["close_d5"] = daily.groupby("symbol")["close"].shift(-4)   # T+1 open to T+5 close? label h5 used close[i+5]/open[i+1]; close[i+5] = shift(-5)?? 
    # Note: fwd_ret5 in ft = close[i+5]/open[i+1]-1 (label convention). We'll use ft["fwd_ret5"] directly for base.
    # For delay stress we compute from daily using the same convention: entry T+1 -> close T+5 => close.shift(-5)/open.shift(-1)
    daily["close_d4"] = daily.groupby("symbol")["close"].shift(-4)
    daily["close_d5"] = daily.groupby("symbol")["close"].shift(-5)
    daily["close_d6"] = daily.groupby("symbol")["close"].shift(-6)
    daily["close_d7"] = daily.groupby("symbol")["close"].shift(-7)
    daily["ret_base"] = daily["close_d5"]/daily["open_d1"] - 1
    daily["ret_delay1"] = daily["close_d6"]/daily["open_d2"] - 1
    daily["ret_delay2"] = daily["close_d7"]/daily["open_d3"] - 1
    daily_d = daily[["symbol","date","ret_base","ret_delay1","ret_delay2"]].drop_duplicates(["symbol","date"])

    # industry map
    conn = sqlite3.connect("data/research_full.db")
    cur = conn.cursor()
    cur.execute("SELECT row_key, payload_json FROM industry_membership")
    ind_map = {}
    for rk, payload in cur.fetchall():
        code = rk.split(":")[1] if ":" in rk else None
        if not code: continue
        obj = json.loads(payload)
        ind_map[code] = obj.get("industry_code","UNKNOWN")
    conn.close()
    ft["code6"] = ft["symbol"].str[:6]
    ft["sector"] = ft["code6"].map(ind_map).fillna("UNKNOWN")
    print("loaded in", round(time.time()-t0,1), flush=True)

    rows=[]
    for col in POSITIVE:
        fac = ft[["symbol","date",col,"fwd_ret5","amt_rank_pct","amt_rank_top500","sector"]].dropna(subset=[col,"fwd_ret5"])
        fac = fac[np.isfinite(fac[col])]
        base = fac[fac["date"].isin(rebal)].copy()
        base = base.sort_values(["date",col,"symbol"], ascending=[True,False,True]).groupby("date").head(100)
        # merge delay returns
        base = base.merge(daily_d, on=["symbol","date"], how="left")
        for stress, retcol, cost_mult, slip_mult, filter_fn in [
            ("base", "ret_base", 1.0, 1.0, None),
            ("cost_x2", "ret_base", 2.0, 1.0, None),
            ("cost_x3", "ret_base", 3.0, 1.0, None),
            ("slippage_x2", "ret_base", 1.0, 2.0, None),
            ("slippage_x3", "ret_base", 1.0, 3.0, None),
            ("delay_1", "ret_delay1", 1.0, 1.0, None),
            ("delay_2", "ret_delay2", 1.0, 1.0, None),
            ("universe_top300", "ret_base", 1.0, 1.0, lambda d: d[d["amt_rank_pct"]>=0.94]),
            ("universe_top500", "ret_base", 1.0, 1.0, lambda d: d[d["amt_rank_top500"]==1]),
            ("universe_top800", "ret_base", 1.0, 1.0, lambda d: d[d["amt_rank_pct"]>=0.84]),
        ]:
            b = base if filter_fn is None else filter_fn(base)
            if len(b)==0:
                rows.append(dict(strategy_id=f"S_{col}_T100_H5", stress=stress, period_return=None, sharpe=None, maxdd=None, note="no data"))
                continue
            net = b[retcol].mean() - (2*COMMISSION*cost_mult + 2*SLIPPAGE*slip_mult + STAMP*cost_mult)
            # equity curve from period returns
            eq = (1+net)**np.arange(len(b.groupby("date"))) if False else None
            # compute per-period returns for maxdd/sharpe using each period's mean net
            per = b.groupby("date")[retcol].mean().sort_index()
            per = per - (2*COMMISSION*cost_mult + 2*SLIPPAGE*slip_mult + STAMP*cost_mult)
            eq = (1+per).cumprod()
            dd = (eq/eq.cummax()-1).min()
            sharpe = float(np.sqrt(52)*per.mean()/per.std(ddof=1)) if len(per)>1 and per.std(ddof=1)>0 else 0.0
            rows.append(dict(strategy_id=f"S_{col}_T100_H5", stress=stress, period_return=float(per.mean()),
                             total_return=float(eq.iloc[-1]-1), sharpe=sharpe, maxdd=float(dd), n_periods=len(per)))
        # best month / sector removal on base
        per = base.groupby("date")["ret_base"].mean().sort_index()
        per = per - (2*COMMISSION + 2*SLIPPAGE + STAMP)
        # best month removal
        month = per.index.astype(str).str[:6]
        best_m = per.groupby(month).sum().idxmax()
        rem_month = per[month != best_m]
        # best sector removal: PnL by sector via contribution = per-date sector mean ret
        base["ret_net"] = base["ret_base"] - (2*COMMISSION + 2*SLIPPAGE + STAMP)
        sec_contrib = base.groupby("sector")["ret_net"].sum().sort_values(ascending=False)
        best_sec = sec_contrib.index[0] if len(sec_contrib) else "NONE"
        rem_sec = base[base["sector"]!=best_sec].groupby("date")["ret_net"].mean().sort_index()
        for stress, per2 in [("remove_best_month", rem_month), ("remove_best_sector", rem_sec)]:
            eq2 = (1+per2).cumprod()
            rows.append(dict(strategy_id=f"S_{col}_T100_H5", stress=stress, period_return=float(per2.mean()),
                             total_return=float(eq2.iloc[-1]-1), maxdd=float((eq2/eq2.cummax()-1).min()),
                             sharpe=float(np.sqrt(52)*per2.mean()/per2.std(ddof=1)) if per2.std(ddof=1)>0 else 0.0,
                             n_periods=len(per2), note=f"removed={best_m if stress.endswith('month') else best_sec}"))
        # bootstrap (monthly block bootstrap, 200)
        per_series = per.copy()
        month = per_series.index.astype(str).str[:6]
        blocks = [per_series[month==m].values for m in sorted(set(month))]
        rng = np.random.default_rng(42)
        boot_means = []
        for _ in range(200):
            sample = [rng.choice(b) for b in blocks]
            boot_means.append(float(np.mean(np.array(sample))))
        rows.append(dict(strategy_id=f"S_{col}_T100_H5", stress="bootstrap", period_return=float(np.mean(boot_means)),
                         total_return=None, sharpe=None, maxdd=None,
                         note=f"boot_ci_low={np.percentile(boot_means,5):.5f} boot_ci_high={np.percentile(boot_means,95):.5f}"))
        # placebo: random 100 stocks per rebalance date -> full cross-sectional daily mean (unbiased estimator)
        all_syms = fac[["symbol","date","fwd_ret5"]].dropna()
        daily_mean = all_syms.groupby("date")["fwd_ret5"].mean().mean()
        placebo_net = daily_mean - (2*COMMISSION + 2*SLIPPAGE + STAMP)
        rows.append(dict(strategy_id=f"S_{col}_T100_H5", stress="placebo_random_stock", period_return=float(placebo_net),
                         total_return=None, sharpe=None, maxdd=None,
                         note="random-100 expected period return approximated by cross-sectional mean"))
        print(col, "done", round(time.time()-t0,1), flush=True)
    out = pd.DataFrame(rows)
    out.to_parquet("data/research/red_team_results.parquet", index=False)
    out.to_csv("reports/STRATEGY_RED_TEAM_REPORT.csv", index=False)
    print("saved", len(out), "rows")
    print(out.pivot(index="strategy_id", columns="stress", values="period_return").to_string())

if __name__ == "__main__":
    main()
