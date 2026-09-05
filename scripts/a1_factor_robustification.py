"""A1: Robustify the 18 existing PROMISING factors. Raw IC decay / quantile / temporal stability."""
from __future__ import annotations
import json, sqlite3, sys, time
from pathlib import Path
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import numpy as np
import pandas as pd
from m7_discovery_round2 import build_features

TRAIN_START, TRAIN_END = 20220801, 20240731
RESEARCH_END = 20250731
FACTORS = ["F_REV20","F_REV5","F_LOWVOLBURST","F_LOWAMT","F_VOLCOMP_LOW","F_GAP","F_UP5_INV","F_IND_DISP_LOW",
           "R2_REV10","R2_REV30","R2_REV_COMPOSITE","R2_REV20_TOPLIQ","R2_LOWAMT_TOPLIQ","R2_LOWVOL_TOPLIQ",
           "R2_LOWAMT_HIGHPRICE","R2_REV20_NOLIMIT","R2_REV20_INDUP","R2_GAP_NOTUP5"]
HORIZONS = [1,2,3,5,7,10,20]

def rankdata_cols(X):
    """Column-wise ranks, average ties, returns float array."""
    R = np.empty_like(X, dtype=float)
    for j in range(X.shape[1]):
        R[:,j] = pd.Series(X[:,j]).rank().to_numpy()
    return R

def safe_ic(arr):
    arr = np.asarray(arr, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return None, None, None, None, len(arr)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if len(arr) > 1 else None
    icir = float(mean/std) if std and std > 0 else None
    pos = float((arr > 0).mean())
    return mean, std, icir, pos, len(arr)

def compute_metrics(m, factors, horizons=None):
    """m: DataFrame with symbol,date,future_return + factor columns (single horizon merged)."""
    rows = []
    horizons = horizons or [1]
    # loop over dates once per merged frame; but frame is single horizon
    date_ics = {f: [] for f in factors}
    date_rics = {f: [] for f in factors}
    q_acc = {f: {q: [0.0, 0] for q in range(5)} for f in factors}
    td_acc = {f: [0.0, 0] for f in factors}
    t5_acc = {f: [0.0, 0] for f in factors}
    for date, g in m.groupby("date"):
        X = g[factors].to_numpy(dtype=float)
        y = g["future_return"].to_numpy(dtype=float)
        n = len(y)
        if n < 10:
            continue
        valid = np.isfinite(X).all(axis=1) & np.isfinite(y)
        X = X[valid]; y = y[valid]
        if len(y) < 10:
            continue
        Xz = X - X.mean(axis=0, keepdims=True)
        yz = y - y.mean()
        ssx = np.sqrt((Xz*Xz).sum(axis=0)); ssy = np.sqrt((yz*yz).sum())
        denom = ssx * ssy
        pcorr = np.zeros(X.shape[1]); pcorr[:] = np.nan
        mask = denom > 0
        pcorr[mask] = ((Xz[:,mask] * yz[:,None]).sum(axis=0)) / denom[mask]
        R = rankdata_cols(X)
        Rz = R - R.mean(axis=0, keepdims=True)
        ry = pd.Series(y).rank().to_numpy()
        ryz = ry - ry.mean()
        ssr = np.sqrt((Rz*Rz).sum(axis=0)); ssry = np.sqrt((ryz*ryz).sum())
        rdenom = ssr * ssry
        rcorr = np.zeros(X.shape[1]); rcorr[:] = np.nan
        rmask = rdenom > 0
        rcorr[rmask] = ((Rz[:,rmask] * ryz[:,None]).sum(axis=0)) / rdenom[rmask]
        # rank pct for bins
        Rpct = R / n
        for j, f in enumerate(factors):
            if np.isfinite(pcorr[j]): date_ics[f].append(pcorr[j])
            if np.isfinite(rcorr[j]): date_rics[f].append(rcorr[j])
            qidx = np.clip((Rpct[:,j] * 5).astype(int), 0, 4)
            for q in range(5):
                mq = qidx == q
                if mq.sum() > 0:
                    q_acc[f][q][0] += float(y[mq].sum()); q_acc[f][q][1] += int(mq.sum())
            td = Rpct[:,j] >= 0.9
            if td.sum() > 0:
                td_acc[f][0] += float(y[td].sum()); td_acc[f][1] += int(td.sum())
            t5 = Rpct[:,j] >= 0.95
            if t5.sum() > 0:
                t5_acc[f][0] += float(y[t5].sum()); t5_acc[f][1] += int(t5.sum())
    for f in factors:
        row = {"factor_id": f, "horizon": horizons[0]}
        row["n_obs"] = sum(v[1] for v in q_acc[f].values())
        ic_mean, ic_std, icir, pos, n_days = safe_ic(date_ics[f])
        ric_mean, ric_std, ricir, rpos, n_rdays = safe_ic(date_rics[f])
        row.update(dict(ic_mean=ic_mean, ic_std=ic_std, icir=icir, ic_pos_ratio=pos, ic_days=n_days,
                        rank_ic_mean=ric_mean, rank_ic_std=ric_std, rank_icir=ricir,
                        rank_ic_pos_ratio=rpos, rank_ic_days=n_rdays))
        for q in range(5):
            s, c = q_acc[f][q]
            row[f"q{q+1}_mean"] = s/c if c else None
        s, c = td_acc[f]; row["top_decile_mean"] = s/c if c else None
        s, c = t5_acc[f]; row["top5pct_mean"] = s/c if c else None
        qv = [row[f"q{q+1}_mean"] for q in range(5)]
        qv2 = [v for v in qv if v is not None]
        row["monotonic"] = bool(len(qv2) >= 3 and ((np.diff(qv2) >= 0).all() or (np.diff(qv2) <= 0).all()))
        rows.append(row)
    return pd.DataFrame(rows)

def main():
    t0 = time.time()
    ft_all = build_features()[["symbol","date"] + FACTORS]
    print("features", ft_all.shape, round(time.time()-t0,1), flush=True)
    tr = pd.read_parquet("data/research/tradable_returns.parquet")
    h20 = pd.read_parquet("data/research/tradable_returns_h20.parquet")
    labels = pd.concat([tr, h20], ignore_index=True)
    labels = labels.rename(columns={"tradable_return": "future_return"})[["symbol","timestamp","horizon","future_return"]]
    print("labels", labels.shape, "h:", sorted(labels["horizon"].unique()), flush=True)

    outdir = Path("data/research/robustification_results")
    outdir.mkdir(parents=True, exist_ok=True)
    all_rows = []
    subsets = {"TRAIN": (TRAIN_START, TRAIN_END),
               "FULL": (TRAIN_START, RESEARCH_END),
               "EXCL_EXTREME": (None, None)}
    for subset, (s, e) in subsets.items():
        ft = ft_all
        lab = labels
        if subset == "TRAIN":
            ft = ft_all[(ft_all["date"] >= s) & (ft_all["date"] <= e)]
            lab = labels[(labels["timestamp"] >= s) & (labels["timestamp"] <= e)]
        elif subset == "FULL":
            ft = ft_all[(ft_all["date"] >= s) & (ft_all["date"] <= e)]
            lab = labels[(labels["timestamp"] >= s) & (labels["timestamp"] <= e)]
        else:
            mask_ft = ~ft_all["date"].between(20240924, 20241008)
            mask_lab = ~labels["timestamp"].between(20240924, 20241008)
            ft = ft_all[mask_ft]; lab = labels[mask_lab]
        for h in HORIZONS:
            lh = lab[lab["horizon"] == h]
            m = ft.merge(lh, left_on=["symbol","date"], right_on=["symbol","timestamp"], how="inner")
            if m.empty:
                continue
            t1 = time.time()
            res = compute_metrics(m, FACTORS, horizons=[h])
            res["subset"] = subset
            all_rows.append(res)
            print(subset, "h", h, "rows", len(res), "elapsed", round(time.time()-t1,1), flush=True)
    df = pd.concat(all_rows, ignore_index=True)
    df.to_parquet(outdir / "a1_ic_quantile.parquet", index=False)
    df.to_csv("reports/A1_IC_QUANTILE.csv", index=False)
    summary = df[df["horizon"].isin([3,5,7])].to_dict(orient="records")
    Path("reports/A1_IC_QUANTILE.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("A1 core saved", df.shape, "total elapsed", round(time.time()-t0,1))

if __name__ == "__main__":
    main()
