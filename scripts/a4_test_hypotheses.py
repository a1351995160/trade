"""A4: test 60 mechanism-driven hypotheses on TRAIN + anchored walk-forward (h5 daily factors + event studies)."""
from __future__ import annotations
import json, math, time
from pathlib import Path
import pandas as pd
import numpy as np

FT_PATH = "data/research/robustification_results/a4_feature_train.parquet"
HYPS_PATH = "data/research/mechanism_hypotheses/hypotheses.jsonl"
OUT_DIR = Path("data/research/robustification_results")
OUT_DIR.mkdir(parents=True, exist_ok=True)

FOLDS = [(20220801, 20230831), (20230901, 20240331), (20240401, 20240731)]
TRAIN = (20220801, 20240731)

# hypotheses that already duplicate existing factor columns (use existing column directly)
EXISTING_COL = {
    "A4-006": "R2_REV20_INDUP",
    "A4-008": "R2_LOWAMT_TOPLIQ",
    "A4-009": "R2_LOWAMT_HIGHPRICE",
    "A4-011": "R2_REV_COMPOSITE",
    "A4-012": "rev20_lowvolcomp",
    "A4-013": "R2_GAP_NOTUP5",
    "A4-014": "R2_REV20_NOLIMIT",
    "A4-015": "R2_LOWAMT_HIGHPRICE",
    "A4-016": "R2_REV20_TOPLIQ",
}

def main():
    t0 = time.time()
    ft = pd.read_parquet(FT_PATH)
    hyps = [json.loads(l) for l in open(HYPS_PATH, encoding="utf-8") if l.strip()]
    # precompute helper columns
    ft["r20_avg5"] = ft["r20"] / 4.0
    ft["decel"] = ft["r5"] - ft["r20_avg5"]
    ft["overnight_neg"] = (ft["overnight_ret"] < 0).astype(float)
    ft["lowvol_flag"] = (ft["vol_comp"] < 1.0).astype(float)
    ft["lowvolratio_flag"] = (ft["vol_ratio"] < 1.0).astype(float)
    ft["amt_low"] = -ft["amt_ratio"]
    ft["amt_top500_low"] = -ft["amt_ratio"] * ft["amt_rank_top500"].astype(float)
    ft["amt_top500_low_pricepos"] = -ft["amt_ratio"] * ft["amt_rank_top500"].astype(float) * ft["price_rank20"].astype(float)
    ft["rev20_lowvolratio"] = -ft["r20"] * ft["lowvolratio_flag"]
    ft["rev20_lowvolcomp"] = -ft["r20"] * ft["lowvol_flag"]
    ft["rev20_decel"] = -ft["r20"] * (ft["decel"] > 0).astype(float)
    ft["rev20_overnightneg"] = -ft["r20"] * ft["overnight_neg"]
    ft["rev30_top500"] = -ft["r30"] * ft["amt_rank_top500"].astype(float)
    ft["rev30_pricepos"] = -ft["r30"] * ft["price_rank20"].astype(float)
    ft["rev20_inddisp_low"] = -ft["r20"] * (ft["ind_r5_std"] < ft.groupby("date")["ind_r5_std"].transform("median")).astype(float)
    ft["up5inv_nonlimit"] = -ft["up5"] * (ft["ret"] < 0.095).astype(float)
    ft["rev2030"] = -0.5 * ft["r20"] - 0.5 * ft["r30"]
    ft["volcomp_low_pricepos"] = -ft["vol_comp"] * ft["price_rank20"].astype(float)
    ft["vol20_low"] = -ft["vol20"]
    ft["rev20_lowturn"] = -ft["r20"] * (ft["turnover_proxy"] < ft.groupby("date")["turnover_proxy"].transform("median")).astype(float)
    ft["micro_pos_amt"] = ft["intraday_pos"] * (ft["amt_ratio"] > 1.0).astype(float)
    ft["micro_asym"] = ft["intraday_ret"] - ft["overnight_ret"]
    ft["micro_path"] = ft["intraday_ret"]
    ft["micro_elasticity"] = ft["ret"] / (ft["amt_ratio"] + 0.5)
    ft["micro_amtchg"] = ft["amt_ratio"] - ft.groupby("symbol")["amt_ratio"].shift(5)
    ft["micro_amt_lead"] = ft["amt_ratio"] - ft.groupby("date")["amt_ratio"].transform("median")
    ft["limit_up_pct_signal"] = ft["limit_up_pct"]
    print("feature table", ft.shape, "elapsed", round(time.time()-t0,1))
    return ft, hyps


ft, hyps = main()

# compute date-level market sentiment shift for A4-305
sent_date = ft.groupby("date")[["height_pct","limit_up_pct"]].first()
sent_date["height_rising"] = (sent_date["height_pct"] - sent_date["height_pct"].shift(5)) > 0
ft = ft.merge(sent_date[["height_rising"]].reset_index(), on="date", how="left")
ft["rev20_height_rising"] = -ft["r20"] * ft["height_rising"].astype(float)

# expression map: hypothesis_id -> column name
EXPR = {
    "A4-001": "rev20_decel",
    "A4-002": "rev20_overnightneg",
    "A4-003": "rev20_lowvolratio",
    "A4-004": "rev30_top500",
    "A4-005": "rev30_pricepos",
    "A4-007": "rev20_inddisp_low",
    "A4-010": "up5inv_nonlimit",
    "A4-101": "micro_pos_amt",
    "A4-102": "micro_asym",
    "A4-103": "micro_path",
    "A4-104": "micro_elasticity",
    "A4-105": "micro_amtchg",
    "A4-106": "micro_amt_lead",
    "A4-113": "volcomp_low_pricepos",
    "A4-115": "vol20_low",
    "A4-214": "rev20_lowturn",
    "A4-305": "rev20_height_rising",
}

# hypotheses marked not directly computable (honest data/recipe limitation)
LIMITED = {
    "A4-112": "requires retail/active-trade data not available in clean daily layer",
    "A4-204": "requires TQ money-flow/large-order definition; not available in current clean layer",
    "A4-207": "requires TQ large order; not available",
    "A4-208": "requires announcement/news calendar + sector breadth; not available",
    "A4-210": "requires policy regime classification; not available",
    "A4-309": "5m data coverage starts after TRAIN (2024-10), cannot be tested in TRAIN",
    "A4-310": "5m data coverage starts after TRAIN (2024-10), cannot be tested in TRAIN",
}

# event hypotheses
EVENT_HYPOTHESES = {
    "A4-301": ("E_LIMITUP_SEAL", "E_LIMITUP"),
    "A4-302": ("E_FAILEDLIMIT", None),
    "A4-303": ("E_LHB_INSTPOS", None),
    "A4-304": ("E_LHB_BROKER", None),
    "A4-306": (None, None),  # market-level test
    "A4-307": ("E_LIMITUP", None),  # first-limit proxy: all first limit events
    "A4-308": ("E_CONSEC_LIMIT", None),
}
print("expr map", len(EXPR), "limited", len(LIMITED), "event", len(EVENT_HYPOTHESES))

# ---------- daily factor evaluation ----------
def daily_rank_ic(sub, col):
    d = sub[[col, "fwd_ret5", "date"]].dropna()
    if len(d) < 30:
        return None, None, 0
    out = []
    for dt, g in d.groupby("date"):
        if len(g) < 30:
            continue
        r = g[col].rank(method="average")
        rr = g["fwd_ret5"].rank(method="average")
        if r.std() > 0 and rr.std() > 0:
            out.append(np.corrcoef(r, rr)[0, 1])
    if len(out) < 30:
        return None, None, 0
    arr = np.array(out)
    return float(arr.mean()), float(arr.std()), len(arr)

def fold_rank_ic(sub, col):
    res = {}
    for i, (a, b) in enumerate(FOLDS):
        d = sub[(sub["date"] >= a) & (sub["date"] <= b)]
        m, s, n = daily_rank_ic(d, col)
        res[f"fold{i+1}_rank_ic"] = m
        res[f"fold{i+1}_n_days"] = n
    return res

def eval_daily(h, ft, pvals, m):
    col = None
    status = "UNKNOWN"
    evidence = ""
    if h["hypothesis_id"] in EXISTING_COL:
        col = EXISTING_COL[h["hypothesis_id"]]
        evidence = f"existing factor column {col}"
    elif h["hypothesis_id"] in EXPR:
        col = EXPR[h["hypothesis_id"]]
        evidence = f"computed factor {col}"
    else:
        return None
    train_ic, train_std, n = daily_rank_ic(ft, col)
    if train_ic is None:
        return dict(hypothesis_id=h["hypothesis_id"], status="REJECTED", verdict_reason="INSUFFICIENT_SAMPLE",
                    train_h5_rank_ic=None, n_days=0, fdr_q=None, fold_details={}, evidence=evidence)
    tstat = train_ic / (train_std / math.sqrt(n)) if train_std and n else 0.0
    p = 2 * (1 - math.erf(abs(tstat) / math.sqrt(2))) if n > 1 else 1.0
    pvals.append((h["hypothesis_id"], p))
    folds = fold_rank_ic(ft, col)
    pos_folds = sum(1 for k in ["fold1_rank_ic","fold2_rank_ic","fold3_rank_ic"] if folds.get(k) is not None and folds.get(k) > 0)
    # fold coverage requirement
    fold_ok = sum(1 for k in ["fold1_rank_ic","fold2_rank_ic","fold3_rank_ic"] if folds.get(k) is not None)
    q = p * m / 1.0  # temporary; BH-FDR applied after collecting pvals
    return dict(hypothesis_id=h["hypothesis_id"], status="PENDING", verdict_reason="",
                train_h5_rank_ic=train_ic, train_h5_icir=train_ic/train_std if train_std else None,
                train_h5_tstat=tstat, n_days=n, p_value=p,
                fold_details=folds, positive_folds=pos_folds, fold_ok=fold_ok,
                evidence=evidence)

# ---------- event evaluation ----------
def event_study(event_id):
    p = Path(f"data/research/event_store/{event_id}_v1.parquet")
    if not p.exists():
        return None
    ev = pd.read_parquet(p)
    ev = ev[(ev["event_time"] >= 20220801) & (ev["event_time"] <= 20240731)].copy()
    ev["date"] = ev["event_time"].astype(int)
    ev = ev.drop_duplicates("symbol", keep="first")
    lab = pd.read_parquet("data/research/tradable_returns.parquet")
    out = {}
    for h in (1, 3, 5):
        lh = lab[(lab["horizon"] == h) & (lab["timestamp"] >= 20220801) & (lab["timestamp"] <= 20240731)][["symbol","timestamp","tradable_return"]].rename(columns={"timestamp":"date"})
        evh = ev.merge(lh, on=["symbol","date"], how="inner")
        if len(evh) == 0:
            out[f"h{h}"] = dict(n=0, mean=None, win_rate=None)
            continue
        out[f"h{h}"] = dict(n=len(evh), mean=float(evh["tradable_return"].mean()), win_rate=float((evh["tradable_return"]>0).mean()))
    return out

def event_verdict(h):
    eid, _ = EVENT_HYPOTHESES.get(h["hypothesis_id"], (None, None))
    es = event_study(eid) if eid else None
    if es is None:
        return dict(hypothesis_id=h["hypothesis_id"], status="REJECTED", verdict_reason="EVENT_DATA_UNAVAILABLE",
                    event_study=None)
    # simple support: h5 mean > 0 and n >= 30
    h5 = es.get("h5", {})
    supported = bool(h5.get("n",0) >= 30 and (h5.get("mean") or 0) > 0)
    return dict(hypothesis_id=h["hypothesis_id"], status="PROMISING" if supported else "REJECTED",
                verdict_reason="EVENT_STUDY" if supported else "EVENT_H5_NOT_POSITIVE",
                event_study=es)

print("evaluating daily hypotheses...", flush=True)
pvals = []
m_daily = len(EXISTING_COL) + len(EXPR)
results = []
for h in hyps:
    hid = h["hypothesis_id"]
    if hid in LIMITED:
        results.append(dict(hypothesis_id=hid, status="REJECTED", verdict_reason="DATA_COMPUTATION_LIMITED",
                            train_h5_rank_ic=None, n_days=0, p_value=None, fdr_q=None,
                            fold_details={}, evidence=LIMITED[hid]))
    elif hid in EVENT_HYPOTHESES:
        results.append(event_verdict(h))
    elif hid == "A4-306":
        # market-level: limit-up count rebound -> next 5d market return
        sent = pd.read_parquet("data/tdx/clean/market_sentiment.parquet")
        sp = sent[sent["pos"] == 0].pivot(index="date", columns="semantic", values="value")
        sp["limit_up_pct"] = sp["limit_up"].rank(pct=True)
        sp["rebound"] = ((sp["limit_up_pct"] > 0.5) & (sp["limit_up_pct"].shift(1) <= 0.5)).astype(int)
        bench = pd.read_parquet("data/research/daily_all.parquet", columns=["symbol","date","close"])
        mkt = bench[(bench["date"]>=20220801)&(bench["date"]<=20240731)].groupby("date")["close"].mean().pct_change().shift(-1)
        # rough equal-weight market next 5d return
        mkt5 = bench[(bench["date"]>=20220801)&(bench["date"]<=20240731)].groupby("date")["close"].mean().pct_change(5).shift(-5)
        sig = sp[["rebound"]].join(mkt5.rename("mkt_ret5"), how="inner")
        pos = sig[sig["rebound"]==1]["mkt_ret5"].dropna()
        results.append(dict(hypothesis_id=hid, status="PROMISING" if (len(pos)>=10 and pos.mean()>0) else "REJECTED",
                            verdict_reason=f"market-level rebound n={len(pos)} mean5d={pos.mean():.4f}" if len(pos) else "NO_SIGNAL_DAYS",
                            event_study=dict(n=int(len(pos)), mean=float(pos.mean()) if len(pos) else None)))
    else:
        rec = eval_daily(h, ft, pvals, m_daily)
        if rec:
            results.append(rec)

# BH-FDR across daily-tested hypotheses
p_sorted = sorted([(hid,p) for hid,p in pvals if p is not None], key=lambda x: x[1])
q_map = {}
prev = None
for i, (hid, p) in enumerate(p_sorted):
    rank = i + 1
    q = p * m_daily / rank
    if prev is not None:
        q = max(q, prev)
    q_map[hid] = min(q, 1.0)
    prev = q

for rec in results:
    hid = rec["hypothesis_id"]
    if hid in q_map:
        rec["fdr_q"] = q_map[hid]
        q = q_map[hid]
        tic = rec.get("train_h5_rank_ic")
        pf = rec.get("positive_folds", 0)
        if tic is None or q > 0.10 or tic < 0.02:
            rec["status"] = "REJECTED"
            rec["verdict_reason"] = "TRAIN_OR_FDR_FAIL" if q > 0.10 or tic < 0.02 else "INSUFFICIENT_SAMPLE"
        elif pf >= 3:
            rec["status"] = "SUPPORTED"
            rec["verdict_reason"] = "TRAIN_POSITIVE_ALL_FOLDS_POSITIVE_FDR_OK"
        elif pf == 2:
            rec["status"] = "PROMISING"
            rec["verdict_reason"] = "TRAIN_POSITIVE_FDR_OK_TWO_FOLDS"
        else:
            rec["status"] = "REJECTED"
            rec["verdict_reason"] = "WALK_FORWARD_FAIL"

res_df = pd.DataFrame(results)
res_df.to_parquet(OUT_DIR / "a4_hypothesis_results.parquet", index=False)
res_df.to_csv("reports/A4_HYPOTHESIS_RESULTS.csv", index=False)
print(res_df["status"].value_counts())
print(res_df.groupby("hypothesis_source")["status"].value_counts())
print(res_df[res_df["status"]!="REJECTED"][["hypothesis_id","status","train_h5_rank_ic","positive_folds","fdr_q","verdict_reason"]].to_string(index=False))
print("done in", round(time.time()-time.time(),1))
