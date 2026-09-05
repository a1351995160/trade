"""A1 remaining: walk-forward, concentration removal, turnover/autocorr, fixed placebo. Uses saved merged h5 TRAIN."""
from __future__ import annotations
import json, sqlite3, sys, time
from pathlib import Path
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import numpy as np
import pandas as pd
from chanlun_trader.research.validation import purged_walk_forward_splits

FACTORS = ["F_REV20","F_REV5","F_LOWVOLBURST","F_LOWAMT","F_VOLCOMP_LOW","F_GAP","F_UP5_INV","F_IND_DISP_LOW",
           "R2_REV10","R2_REV30","R2_REV_COMPOSITE","R2_REV20_TOPLIQ","R2_LOWAMT_TOPLIQ","R2_LOWVOL_TOPLIQ",
           "R2_LOWAMT_HIGHPRICE","R2_REV20_NOLIMIT","R2_REV20_INDUP","R2_GAP_NOTUP5"]
OUT = Path("data/research/robustification_results")

def load_industry_map():
    con = sqlite3.connect("data/research_full.db")
    rows = con.execute("SELECT payload_json FROM industry_membership").fetchall()
    con.close()
    d = {}
    for (p,) in rows:
        obj = json.loads(p)
        code = obj.get("code"); ind = obj.get("industry_code")
        if code and ind: d[code] = ind
    return d

def rankdata(a):
    return pd.Series(a).rank().to_numpy()

def rank_ic_vec(Xr, yr):
    Xz = Xr - Xr.mean(axis=0, keepdims=True)
    yz = yr - yr.mean()
    denom = np.sqrt((Xz*Xz).sum(axis=0) * (yz*yz).sum())
    out = np.zeros(Xr.shape[1]); out[:] = np.nan
    ok = denom > 0
    out[ok] = ((Xz[:,ok] * yz[:,None]).sum(axis=0)) / denom[ok]
    return out

def main():
    t0 = time.time()
    m = pd.read_parquet(OUT / "a1_merged_h5_train.parquet")
    ind_map = load_industry_map()
    m["ind_code"] = m["symbol"].str[:6].map(ind_map)
    print("loaded", m.shape, round(time.time()-t0,1), flush=True)

    # ---- autocorr / turnover ----
    ac_rows = []
    for f in FACTORS:
        pivot = m.pivot_table(index="date", columns="symbol", values=f)
        if len(pivot) > 2:
            factor_autocorr = float(pivot.mean(axis=1).autocorr())
            prev = pivot.shift(1)
            rank_corrs = []
            for ts in pivot.index[1:]:
                a = pivot.loc[ts]; b = prev.loc[ts]
                mm = a.notna() & b.notna()
                if mm.sum() >= 5:
                    ra = a[mm].rank(); rb = b[mm].rank()
                    c = np.corrcoef(ra, rb)[0,1]
                    if np.isfinite(c): rank_corrs.append(c)
            turnover = float(1.0 - np.nanmean(rank_corrs)) if rank_corrs else None
        else:
            factor_autocorr = turnover = None
        ac_rows.append({"factor_id": f, "horizon": 5, "factor_autocorr": factor_autocorr, "factor_turnover": turnover})
    ac = pd.DataFrame(ac_rows)
    ac.to_parquet(OUT / "a1_autocorr_turnover_h5.parquet", index=False)
    ac.to_csv("reports/A1_AUTOCORR_TURNOVER_H5.csv", index=False)
    print("autocorr/turnover saved", round(time.time()-t0,1), flush=True)

    # ---- walk-forward: 3 purged splits on TRAIN dates ----
    dates = sorted(m["date"].unique())
    splits = purged_walk_forward_splits(dates, n_splits=3, embargo=10, min_train_ratio=0.5)
    print("walk-forward splits", [(len(a), len(b)) for a,b in splits], flush=True)
    wf_rows = []
    for fold, (train_dates, test_dates) in enumerate(splits, 1):
        g = m[m["date"].isin(test_dates)]
        for f in FACTORS:
            arr = []
            for d, sub in g.groupby("date"):
                if len(sub) < 30: continue
                x = rankdata(sub[f].to_numpy(dtype=float)); y = rankdata(sub["future_return"].to_numpy(dtype=float))
                xz = x - x.mean(); yz = y - y.mean()
                den = np.sqrt((xz*xz).sum()*(yz*yz).sum())
                if den > 0: arr.append(float((xz*yz).sum()/den))
            arr = np.array(arr, dtype=float); arr = arr[np.isfinite(arr)]
            wf_rows.append({"factor_id": f, "fold": fold, "n_days": len(arr),
                            "rank_ic_mean": float(arr.mean()) if len(arr) else None,
                            "rank_ic_pos_ratio": float((arr>0).mean()) if len(arr) else None})
    wf = pd.DataFrame(wf_rows)
    wf.to_parquet(OUT / "a1_walkforward_h5.parquet", index=False)
    wf.to_csv("reports/A1_WALKFORWARD_H5.csv", index=False)
    print("walk-forward saved", round(time.time()-t0,1), flush=True)

    # ---- concentration removal ----
    conc_rows = []
    for f in FACTORS:
        sub = m[["date","symbol","future_return","ind_code",f]].dropna(subset=[f,"future_return"])
        # daily rank pct
        sub["rpct"] = sub.groupby("date")[f].transform(lambda s: s.rank(pct=True))
        top = sub[sub["rpct"] >= 0.8].copy()
        base_top_mean = float(top["future_return"].mean()) if len(top) else None
        # monthly contribution: mean top-quintile future_return by month
        top["ym"] = (top["date"] // 100).astype(int)
        month_mean = top.groupby("ym")["future_return"].mean()
        best_month = int(month_mean.idxmax()) if len(month_mean) else None
        worst_month = int(month_mean.idxmin()) if len(month_mean) else None
        remove_best_month_mean = float(top[top["ym"] != best_month]["future_return"].mean()) if len(top) and best_month is not None else None
        # sector contribution within top quintile
        sec_mean = top.groupby("ind_code")["future_return"].mean().dropna()
        top3_sec = sec_mean.nlargest(3)
        hhi = float(((sec_mean**2).sum()) / max(1e-12, (sec_mean.sum()**2))) if len(sec_mean) else None
        best_sec = sec_mean.idxmax() if len(sec_mean) else None
        remove_best_sector_mean = float(top[top["ind_code"] != best_sec]["future_return"].mean()) if best_sec and len(top) else None
        top3_sec_contrib = float((top[top["ind_code"].isin(top3_sec.index)]["future_return"].sum() / top["future_return"].sum())) if len(top) and top["future_return"].sum()!=0 else None
        # remove top extreme observations (top 1% of future returns)
        thr = sub["future_return"].quantile(0.99)
        sub2 = sub[sub["future_return"] <= thr]
        top2 = sub2[sub2["rpct"] >= 0.8]
        remove_extreme_mean = float(top2["future_return"].mean()) if len(top2) else None
        # remove worst month too (robustness)
        remove_worst_month_mean = float(top[top["ym"] != worst_month]["future_return"].mean()) if len(top) and worst_month is not None else None
        conc_rows.append({
            "factor_id": f, "horizon": 5,
            "top_quintile_mean": base_top_mean,
            "best_month": best_month, "worst_month": worst_month,
            "remove_best_month_mean": remove_best_month_mean,
            "remove_worst_month_mean": remove_worst_month_mean,
            "best_sector": str(best_sec),
            "remove_best_sector_mean": remove_best_sector_mean,
            "top3_sector_contrib": top3_sec_contrib,
            "sector_hhi": hhi,
            "remove_top1pct_extreme_mean": remove_extreme_mean,
        })
    conc = pd.DataFrame(conc_rows)
    conc.to_parquet(OUT / "a1_concentration_h5.parquet", index=False)
    conc.to_csv("reports/A1_CONCENTRATION_H5.csv", index=False)
    print("concentration saved", round(time.time()-t0,1), flush=True)

    # ---- fixed placebo: cross-sectional shuffle (20 iters) ----
    # Precompute per-date rank matrix and y rank once
    date_arr = {}
    for d, g in m.groupby("date"):
        if len(g) < 30: continue
        X = g[FACTORS].to_numpy(dtype=float)
        y = g["future_return"].to_numpy(dtype=float)
        ok = np.isfinite(X).all(axis=1) & np.isfinite(y)
        X = X[ok]; y = y[ok]
        if len(y) < 30: continue
        Xr = np.column_stack([rankdata(X[:,j]) for j in range(X.shape[1])])
        yr = rankdata(y)
        date_arr[d] = (Xr, yr)
    print("precomputed dates", len(date_arr), round(time.time()-t0,1), flush=True)
    rng = np.random.default_rng(12345)
    p_rows = []
    for it in range(20):
        for d, (Xr, yr) in date_arr.items():
            idx = rng.permutation(len(yr))
            ric = rank_ic_vec(Xr[idx, :], yr)
            for j, f in enumerate(FACTORS):
                if np.isfinite(ric[j]):
                    p_rows.append({"factor_id": f, "iteration": it, "date": d, "placebo_rank_ic": ric[j]})
    pl = pd.DataFrame(p_rows)
    pl_sum = pl.groupby("factor_id").agg(placebo_rank_ic_mean=("placebo_rank_ic","mean"),
                                          placebo_rank_ic_std=("placebo_rank_ic","std"),
                                          placebo_n=("placebo_rank_ic","count")).reset_index()
    pl_sum.to_parquet(OUT / "a1_placebo_h5_fixed.parquet", index=False)
    pl_sum.to_csv("reports/A1_PLACEBO_H5_FIXED.csv", index=False)
    print("placebo fixed saved", round(time.time()-t0,1), flush=True)


if __name__ == "__main__":
    main()
