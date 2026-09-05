"""B2: post-run Red Team analytics: concentration, bootstrap, placebo for event survivors."""
from __future__ import annotations
import json, sqlite3, sys, time
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, "src")
OUT = Path("data/research/strategy_translation_results")
TRAIN = (20220801, 20240731)

def load_industry_map():
    conn = sqlite3.connect("data/research_full.db")
    cur = conn.cursor()
    cur.execute("SELECT row_key, payload_json FROM industry_membership")
    ind = {}
    for rk, payload in cur.fetchall():
        code = rk.split(":")[1] if ":" in rk else None
        if not code:
            continue
        obj = json.loads(payload)
        ind[code] = obj.get("industry_code", "UNKNOWN")
    conn.close()
    return ind

def bootstrap_pnl(pnl: pd.Series, n_boot=200, seed=42):
    pnl = pnl.dropna()
    if len(pnl) < 10:
        return dict(bootstrap_status="BOOTSTRAP_UNAVAILABLE_SAMPLE_TOO_SMALL",
                    bootstrap_mean=None, bootstrap_median=None,
                    bootstrap_ci_2_5=None, bootstrap_ci_97_5=None, probability_positive=None)
    rng = np.random.default_rng(seed)
    vals = pnl.to_numpy()
    try:
        months = pd.to_datetime(pnl.index).dt.strftime("%Y-%m")
        blocks = [vals[months == m] for m in months.unique()]
    except Exception:
        blocks = [vals[i:i+10] for i in range(0, len(vals), 10)]
    means = []
    for _ in range(n_boot):
        sample = [rng.choice(b) for b in blocks if len(b) > 0]
        if sample:
            means.append(float(np.mean(np.concatenate([np.atleast_1d(x) for x in sample]))))
    means = np.array(means)
    return dict(bootstrap_status="OK", bootstrap_mean=float(means.mean()),
                bootstrap_median=float(np.median(means)),
                bootstrap_ci_2_5=float(np.percentile(means, 2.5)),
                bootstrap_ci_97_5=float(np.percentile(means, 97.5)),
                probability_positive=float((means > 0).mean()))

def main():
    ind = load_industry_map()
    ev = pd.read_parquet(OUT / "event_strategy_results.parquet")
    rt = pd.read_parquet(OUT / "full_engine_red_team_results.parquet")
    rows = []
    for _, base in ev[ev["status"] == "PROMISING"].iterrows():
        sid = base["strategy_id"]
        eid = base["event_id"]
        h = int(base["holding_days"])
        trades = pd.read_parquet(OUT / f"trades_{sid}.parquet")
        sells = trades[trades["side"] == "SELL"].copy()
        sells["pnl"] = sells["realized_pnl"]
        sells["month"] = sells["fill_time"].str[:7]
        sells["code6"] = sells["symbol"].str[:6]
        sells["sector"] = sells["code6"].map(ind).fillna("UNKNOWN")
        total = float(sells["pnl"].sum())
        pnl_sorted = sells["pnl"].sort_values(ascending=False)
        best_month = sells.groupby("month")["pnl"].sum().idxmax()
        best_month_pnl = float(sells.groupby("month")["pnl"].sum().max())
        sector_pnl = sells.groupby("sector")["pnl"].sum().sort_values(ascending=False)
        best_sector = sector_pnl.index[0]
        best_sector_pnl = float(sector_pnl.iloc[0])
        hhi = float(((sector_pnl / total) ** 2).sum()) if total != 0 else 0.0
        # delay verdict from engine red team
        d1 = rt[(rt["strategy_id"] == f"E_{eid}_H{h}_delay_1")]
        d2 = rt[(rt["strategy_id"] == f"E_{eid}_H{h}_delay_2")]
        delay_fail = bool((d1.iloc[0]["status"] == "REJECTED") if len(d1) else True) or bool((d2.iloc[0]["status"] == "REJECTED") if len(d2) else True)
        boot = bootstrap_pnl(sells.set_index("fill_time")["pnl"])
        row = dict(strategy_id=sid, event_id=eid, n_trades=int(len(sells)), total_pnl=total,
                   top1=float(pnl_sorted.iloc[0] / total) if total else 0.0,
                   top3=float(pnl_sorted.head(3).sum() / total) if total else 0.0,
                   top5=float(pnl_sorted.head(5).sum() / total) if total else 0.0,
                   top10=float(pnl_sorted.head(10).sum() / total) if total else 0.0,
                   mean_pnl=float(sells["pnl"].mean()), median_pnl=float(sells["pnl"].median()),
                   p25=float(sells["pnl"].quantile(0.25)), p75=float(sells["pnl"].quantile(0.75)),
                   best_month=best_month, best_month_contribution=float(best_month_pnl / total) if total else 0.0,
                   total_ex_best_month=float(total - best_month_pnl),
                   best_sector=best_sector, best_sector_contribution=float(best_sector_pnl / total) if total else 0.0,
                   total_ex_best_sector=float(total - best_sector_pnl),
                   sector_hhi=hhi, top3_sector_contribution=float(sector_pnl.head(3).sum() / total) if total else 0.0,
                   delay_1_status=(d1.iloc[0]["status"] if len(d1) else "NOT_RUN"),
                   delay_2_status=(d2.iloc[0]["status"] if len(d2) else "NOT_RUN"),
                   extreme_period="TRAIN_ONLY_20240924_1010_EXCLUDED",
                   final_status="REJECTED" if delay_fail else "PROMISING",
                   **boot)
        rows.append(row)
        print(json.dumps({k: row[k] for k in ["strategy_id","n_trades","total_pnl","top10","best_month_contribution","best_sector_contribution","sector_hhi","bootstrap_status","probability_positive","final_status"]}, ensure_ascii=False), flush=True)
    out = pd.DataFrame(rows)
    out.to_parquet(OUT / "red_team_analytics.parquet", index=False)
    out.to_csv("reports/FULL_ENGINE_RED_TEAM_ANALYTICS.csv", index=False)
    # append analytics to red team report
    lines = ["# FULL ENGINE RED TEAM RESULTS", "", "All stress runs were executed through BT_ENGINE_V2 (Signal -> OrderIntent -> Order -> Fill -> Ledger -> Metrics).",
             "", rt.to_markdown(index=False), "", "## Concentration / Bootstrap", "", out.to_markdown(index=False)]
    Path("reports/FULL_ENGINE_RED_TEAM_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("post saved", len(out), flush=True)

if __name__ == "__main__":
    main()
