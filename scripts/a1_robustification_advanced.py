"""A1 advanced: neutralization, temporal stability, bootstrap, placebo for h5 TRAIN."""
from __future__ import annotations
import json, sqlite3, sys, time
from pathlib import Path
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import numpy as np
import pandas as pd
from m7_discovery_round2 import build_features

TRAIN_START, TRAIN_END = 20220801, 20240731
FACTORS = ["F_REV20","F_REV5","F_LOWVOLBURST","F_LOWAMT","F_VOLCOMP_LOW","F_GAP","F_UP5_INV","F_IND_DISP_LOW",
           "R2_REV10","R2_REV30","R2_REV_COMPOSITE","R2_REV20_TOPLIQ","R2_LOWAMT_TOPLIQ","R2_LOWVOL_TOPLIQ",
           "R2_LOWAMT_HIGHPRICE","R2_REV20_NOLIMIT","R2_REV20_INDUP","R2_GAP_NOTUP5"]
H = 5

def load_industry_map():
    con = sqlite3.connect("data/research_full.db")
    rows = con.execute("SELECT payload_json FROM industry_membership").fetchall()
    con.close()
    d = {}
    for (p,) in rows:
        obj = json.loads(p)
        code = obj.get("code")
        ind = obj.get("industry_code")
        if code and ind:
            d[code] = ind
    return d

def rankdata(a):
    return pd.Series(a).rank().to_numpy()

def main():
    t0 = time.time()
    ft_all = build_features()[["symbol","date"] + FACTORS]
    up = pd.read_parquet("data/research/universe_proxy_m6.parquet")[["symbol","date","amt_rank_pct"]]
    ind_map = load_industry_map()
    print("maps", len(ind_map), round(time.time()-t0,1), flush=True)
    ft_all = ft_all.merge(up, on=["symbol","date"], how="left")
    ft_all["ind_code"] = ft_all["symbol"].str[:6].map(ind_map)
    print("merged", round(time.time()-t0,1), flush=True)
    lab = pd.read_parquet("data/research/tradable_returns.parquet")
    lab = lab[(lab["horizon"]==H) & (lab["timestamp"]>=TRAIN_START) & (lab["timestamp"]<=TRAIN_END)]
    lab = lab.rename(columns={"tradable_return":"future_return"})[["symbol","timestamp","future_return"]]
    ft = ft_all[(ft_all["date"]>=TRAIN_START) & (ft_all["date"]<=TRAIN_END)]
    m = ft.merge(lab, left_on=["symbol","date"], right_on=["symbol","timestamp"], how="inner")
    print("merged h5 train", m.shape, round(time.time()-t0,1), flush=True)

    daily = {v: {f: [] for f in FACTORS} for v in ["raw","size_neutral","ind_neutral","ind_size_neutral"]}
    # also daily returns for bootstrap later
    rows = []
    for date, g in m.groupby("date"):
        g = g.sort_values("symbol")
        X = g[FACTORS].to_numpy(dtype=float)
        y = g["future_return"].to_numpy(dtype=float)
        size = g["amt_rank_pct"].to_numpy(dtype=float)
        indc = g["ind_code"].to_numpy()
        n = len(y)
        if n < 30:
            continue
        valid = np.isfinite(X).all(axis=1) & np.isfinite(y) & np.isfinite(size)
        X = X[valid]; y = y[valid]; size = size[valid]; indc = indc[valid]
        n = len(y)
        if n < 30:
            continue
        # raw rank IC
        def rank_ic(F):
            Fz = F - F.mean(axis=0, keepdims=True)
            yz = y - y.mean()
            denom = np.sqrt((Fz*Fz).sum(axis=0) * (yz*yz).sum())
            out = np.zeros(F.shape[1]); out[:] = np.nan
            ok = denom > 0
            out[ok] = ((Fz[:,ok] * yz[:,None]).sum(axis=0)) / denom[ok]
            return out
        Xr = np.column_stack([rankdata(X[:,j]) for j in range(X.shape[1])])
        yr = rankdata(y)
        raw_ric = rank_ic(Xr)
        # size neutral residual: regress each factor rank on size rank
        sr = rankdata(size)
        srz = sr - sr.mean()
        ss = (srz*srz).sum()
        res_size = np.empty_like(Xr)
        for j in range(X.shape[1]):
            x = Xr[:,j]
            beta = (x - x.mean()) @ srz / ss if ss > 0 else 0.0
            res_size[:,j] = x - beta * sr
        size_ric = rank_ic(res_size)
        # industry neutral: subtract industry mean of factor rank
        res_ind = np.empty_like(Xr)
        for j in range(X.shape[1]):
            x = Xr[:,j].copy()
            for code in np.unique(indc):
                mask = indc == code
                if mask.sum() > 1:
                    x[mask] = x[mask] - x[mask].mean()
            res_ind[:,j] = x
        ind_ric = rank_ic(res_ind)
        # industry + size
        res_both = np.empty_like(Xr)
        for j in range(X.shape[1]):
            x = res_ind[:,j]
            beta = (x - x.mean()) @ srz / ss if ss > 0 else 0.0
            res_both[:,j] = x - beta * sr
        both_ric = rank_ic(res_both)
        for j, f in enumerate(FACTORS):
            daily["raw"][f].append(raw_ric[j])
            daily["size_neutral"][f].append(size_ric[j])
            daily["ind_neutral"][f].append(ind_ric[j])
            daily["ind_size_neutral"][f].append(both_ric[j])
        rows.append(date)
    dates = pd.Series(rows, name="date")
    print("daily loop done", len(rows), round(time.time()-t0,1), flush=True)

    outdir = Path("data/research/robustification_results")
    out = []
    for v in daily:
        for f in FACTORS:
            arr = np.array([x for x in daily[v][f] if np.isfinite(x)], dtype=float)
            if len(arr) == 0:
                continue
            out.append({"factor_id": f, "variant": v, "horizon": H,
                        "n_days": len(arr), "rank_ic_mean": float(arr.mean()),
                        "rank_ic_std": float(arr.std(ddof=1)),
                        "rank_icir": float(arr.mean()/arr.std(ddof=1)) if arr.std(ddof=1) > 0 else None,
                        "rank_ic_pos_ratio": float((arr>0).mean())})
    neu = pd.DataFrame(out)
    neu.to_parquet(outdir / "a1_neutralization_h5.parquet", index=False)
    neu.to_csv("reports/A1_NEUTRALIZATION_H5.csv", index=False)
    print("neutralization saved")
    # temporal stability by year/half on raw daily IC
    ts_rows = []
    for f in FACTORS:
        pairs = [(d, x) for d, x in zip(rows, daily["raw"][f]) if np.isfinite(x)]
        if not pairs:
            continue
        arr = np.array([x for _, x in pairs], dtype=float)
        s = pd.Series(arr, index=pd.to_datetime([str(d) for d, _ in pairs], format="%Y%m%d"))
        for period in ["Y","6M"]:
            if period == "Y":
                g = s.groupby(s.index.year)
            else:
                g = s.groupby(s.index.year.astype(str) + "H" + ((s.index.month>6)+1).astype(str))
            for name, grp in g:
                if len(grp) >= 5:
                    ts_rows.append({"factor_id": f, "period": period, "bucket": str(name),
                                    "rank_ic_mean": float(grp.mean()), "n_days": int(len(grp))})
    ts = pd.DataFrame(ts_rows)
    ts.to_parquet(outdir / "a1_temporal_stability_h5.parquet", index=False)
    ts.to_csv("reports/A1_TEMPORAL_STABILITY_H5.csv", index=False)
    print("temporal saved", ts.shape)

    # bootstrap raw daily IC (iid, 200 draws)
    rng = np.random.default_rng(42)
    boot_rows = []
    for f in FACTORS:
        arr = np.array([x for x in daily["raw"][f] if np.isfinite(x)], dtype=float)
        if len(arr) < 20:
            continue
        means = []
        for _ in range(200):
            s = rng.choice(arr, size=len(arr), replace=True)
            means.append(float(s.mean()))
        means = np.array(means)
        boot_rows.append({"factor_id": f, "rank_ic_mean": float(arr.mean()),
                          "boot_mean": float(means.mean()),
                          "boot_ci_low": float(np.percentile(means, 5)),
                          "boot_ci_high": float(np.percentile(means, 95)),
                          "boot_pct_positive": float((means>0).mean())})
    boot = pd.DataFrame(boot_rows)
    boot.to_parquet(outdir / "a1_bootstrap_h5.parquet", index=False)
    boot.to_csv("reports/A1_BOOTSTRAP_H5.csv", index=False)
    print("bootstrap saved")

    # placebo: cross-sectional shuffle 10 iterations per date
    placebo_rows = []
    for f in FACTORS:
        vals = []
        for _ in range(10):
            rng.shuffle(m[f].to_numpy())
            # shuffle within date groups: easier recompute from m with shuffled factor
        # do groupby shuffle
    # simpler placebo: recompute rank IC on shuffled factor values
    m_shuf = m.copy()
    rng = np.random.default_rng(7)
    p_rows = []
    for it in range(10):
        for f in FACTORS:
            m_shuf[f] = m_shuf.groupby("date")[f].transform(lambda s: s.sample(frac=1.0, random_state=it).to_numpy())
        for f in FACTORS:
            arr = []
            for date, g in m_shuf.groupby("date"):
                if len(g) < 30: continue
                x = rankdata(g[f].to_numpy(dtype=float)); yy = rankdata(g["future_return"].to_numpy(dtype=float))
                xz = x - x.mean(); yz = yy - yy.mean()
                d = np.sqrt((xz*xz).sum()*(yz*yz).sum())
                if d > 0: arr.append(float((xz*yz).sum()/d))
            p_rows.append({"factor_id": f, "iteration": it, "placebo_rank_ic": float(np.nanmean(arr))})
    pl = pd.DataFrame(p_rows)
    pl_sum = pl.groupby("factor_id").agg(placebo_rank_ic_mean=("placebo_rank_ic","mean"),
                                          placebo_rank_ic_std=("placebo_rank_ic","std")).reset_index()
    pl_sum.to_parquet(outdir / "a1_placebo_h5.parquet", index=False)
    pl_sum.to_csv("reports/A1_PLACEBO_H5.csv", index=False)
    print("placebo saved", round(time.time()-t0,1))


if __name__ == "__main__":
    main()
