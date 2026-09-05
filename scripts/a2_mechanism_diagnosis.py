"""A2: mechanism registry + factor->mechanism mapping + report."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

MECHANISMS = [
    {"mechanism_id":"M_REV_MEAN_REVERSION","name":"General Mean Reversion","family":"Reversal","description":"Broad cross-sectional short-term reversal after overreaction","notes":""},
    {"mechanism_id":"M_REV_T1_DELAYED_SUPPLY","name":"T+1 Delayed Supply","family":"Reversal","description":"T+1 rule delays selling pressure; forced supply pushes prices below fundamentals then rebounds","notes":"A-share specific"},
    {"mechanism_id":"M_REV_ATTENTION_OVERREACTION","name":"Attention Overreaction","family":"Reversal","description":"High attention / large moves overshoot; reversal as attention fades","notes":""},
    {"mechanism_id":"M_REV_SELLING_EXHAUSTION","name":"Selling Exhaustion","family":"Reversal","description":"Persistent decline exhausts sellers; price stabilizes and rebounds","notes":""},
    {"mechanism_id":"M_REV_INDUSTRY_CORRECTION","name":"Industry Correction","family":"Reversal","description":"Sector-wide correction then sector-relative rebound","notes":""},
    {"mechanism_id":"M_LIQUIDITY_RISK_PREMIUM","name":"Liquidity Risk Premium","family":"Liquidity","description":"Low turnover/amount stocks earn premium compensating liquidity risk","notes":""},
    {"mechanism_id":"M_ATTENTION_LOW_HEAT","name":"Low Attention / Low Heat","family":"Attention","description":"Stocks ignored by market have positive drift until discovered","notes":""},
    {"mechanism_id":"M_VOLATILITY_RISK_PREMIUM","name":"Volatility Compression Premium","family":"Volatility","description":"Low volatility / volatility compression stocks earn risk-adjusted premium","notes":"weak in TRAIN raw"},
    {"mechanism_id":"M_FLOW_ABSORPTION","name":"Flow Absorption","family":"Flow","description":"Price absorbs selling flow without further decline; informed accumulation","notes":""},
    {"mechanism_id":"M_OVERNIGHT_GAP_REVERSAL","name":"Overnight Gap Quality","family":"PricePath","description":"Overnight gap with follow-through; quality depends on prior streak and heat","notes":""},
    {"mechanism_id":"M_DISPERSION_LOW","name":"Low Dispersion","family":"Sector","description":"Low cross-sectional dispersion within industry predicts continuation / stability","notes":""},
    {"mechanism_id":"M_TOP_LIQUIDITY_FILTER","name":"Top500 Liquidity Filter","family":"Liquidity","description":"Restricting to liquid Top500 pool removes small-cap shell noise and improves capacity","notes":""},
    {"mechanism_id":"M_PRICE_POSITION","name":"Price Position","family":"PricePath","description":"Price relative to 20d median conditions trend vs reversal","notes":""},
    {"mechanism_id":"M_SECTOR_MOMENTUM","name":"Sector Momentum Filter","family":"Sector","description":"Only trade reversal when industry not deteriorating","notes":""},
    {"mechanism_id":"M_STREAK_FADE","name":"Short-Streak Fade","family":"Reversal","description":"5-day up streak reverts; chase supply exhausted","notes":""},
]

def main():
    mech_dir = Path("data/research/mechanism_registry")
    mech_dir.mkdir(parents=True, exist_ok=True)
    (mech_dir / "registry.json").write_text(json.dumps({"mechanisms": MECHANISMS}, indent=2, ensure_ascii=False), encoding="utf-8")
    print("mechanism registry written")

MAPPING = [
    ("F_REV20","M_REV_T1_DELAYED_SUPPLY",["M_REV_ATTENTION_OVERREACTION","M_REV_SELLING_EXHAUSTION","M_REV_INDUSTRY_CORRECTION"],0.75,
     "size_neutral RankIC 0.0185 vs raw 0.0272 -> size explains ~32%; industry_neutral 0.0257 -> industry not main driver. Remaining most consistent with T+1 delayed supply + attention overreaction."),
    ("F_REV5","M_REV_ATTENTION_OVERREACTION",["M_REV_MEAN_REVERSION","M_REV_T1_DELAYED_SUPPLY"],0.55,
     "short 5d reversal; weaker placebo superiority (real 0.0262 vs threshold 0.0304); attention overreaction with T+1 supply likely."),
    ("R2_REV10","M_REV_T1_DELAYED_SUPPLY",["M_REV_ATTENTION_OVERREACTION"],0.7,
     "10d reversal stable; size_neutral 0.0169."),
    ("R2_REV30","M_REV_T1_DELAYED_SUPPLY",["M_REV_SELLING_EXHAUSTION","M_REV_ATTENTION_OVERREACTION"],0.8,
     "strongest 30d reversal; size_neutral 0.0241 retains most alpha; likely forced supply unwind + exhaustion."),
    ("R2_REV_COMPOSITE","M_REV_T1_DELAYED_SUPPLY",["M_REV_ATTENTION_OVERREACTION","M_REV_MEAN_REVERSION"],0.75,
     "5d+20d composite; diversified reversal horizon; size_neutral 0.0180."),
    ("R2_REV20_TOPLIQ","M_REV_T1_DELAYED_SUPPLY",["M_TOP_LIQUIDITY_FILTER","M_FLOW_ABSORPTION"],0.75,
     "reversal in Top500; liquidity filter improves capacity; raw 0.0378, size_neutral 0.0203."),
    ("F_LOWAMT","M_LIQUIDITY_RISK_PREMIUM",["M_ATTENTION_LOW_HEAT","M_REV_T1_DELAYED_SUPPLY"],0.6,
     "size_neutral ~0.0004 -> largely a size/liquidity proxy; low amount alone not robust after size neutral."),
    ("F_LOWVOLBURST","M_ATTENTION_LOW_HEAT",["M_LIQUIDITY_RISK_PREMIUM"],0.55,
     "size_neutral -0.0034 -> explained by size; not independent alpha."),
    ("R2_LOWAMT_TOPLIQ","M_TOP_LIQUIDITY_FILTER",["M_ATTENTION_LOW_HEAT","M_FLOW_ABSORPTION"],0.7,
     "low amount inside Top500; raw 0.0527, size_neutral 0.0132; conditional factor, top quintile undefined outside active subset."),
    ("R2_LOWVOL_TOPLIQ","M_TOP_LIQUIDITY_FILTER",["M_ATTENTION_LOW_HEAT","M_FLOW_ABSORPTION"],0.7,
     "low volume ratio inside Top500; same mechanism as LOWAMT_TOPLIQ (corr~1)."),
    ("R2_LOWAMT_HIGHPRICE","M_FLOW_ABSORPTION",["M_LIQUIDITY_RISK_PREMIUM","M_PRICE_POSITION"],0.65,
     "low amount + price above 20d median; absorption with price strength; train h5 0.0459, size_neutral 0.0143."),
    ("F_UP5_INV","M_STREAK_FADE",["M_REV_ATTENTION_OVERREACTION"],0.6,
     "fade 5-day up streak; train h5 0.0224; placebo threshold fails (0.0304) -> moderate."),
    ("F_IND_DISP_LOW","M_DISPERSION_LOW",["M_SECTOR_MOMENTUM","M_ATTENTION_LOW_HEAT"],0.6,
     "low industry dispersion; train h5 0.0396, size_neutral 0.0117."),
    ("R2_REV20_INDUP","M_SECTOR_MOMENTUM",["M_REV_T1_DELAYED_SUPPLY"],0.6,
     "reversal only when industry r5 > -2%; train h5 0.0312; size_neutral 0.0083."),
    ("R2_REV20_NOLIMIT","M_REV_T1_DELAYED_SUPPLY",["M_REV_SELLING_EXHAUSTION"],0.55,
     "excludes 20d limit-down names; wf fold2 negative -> temporal weak."),
    ("F_GAP","M_OVERNIGHT_GAP_REVERSAL",["M_REV_ATTENTION_OVERREACTION"],0.5,
     "overnight gap alone weak in TRAIN; extreme-sensitive; rejected as standalone."),
    ("R2_GAP_NOTUP5","M_OVERNIGHT_GAP_REVERSAL",["M_STREAK_FADE"],0.55,
     "gap fade when not in 5-day streak; still weak in TRAIN; rejected standalone."),
    ("F_VOLCOMP_LOW","M_VOLATILITY_RISK_PREMIUM",["M_ATTENTION_LOW_HEAT"],0.4,
     "vol compression premium weak in TRAIN; wf fold3 negative; rejected."),
]


map_df = pd.DataFrame([{
    "factor_id": f, "primary_mechanism_id": p, "secondary_mechanism_ids": s,
    "mechanism_confidence": c, "alternative_explanation": alt,
} for f, p, s, c, alt in MAPPING])
map_dir = Path("data/research/factor_mechanism_map")
map_dir.mkdir(parents=True, exist_ok=True)
map_df.to_parquet(map_dir / "factor_mechanism_map.parquet", index=False)
map_df.to_csv("reports/FACTOR_MECHANISM_MAP.csv", index=False)
print("factor mechanism map written")

edges = []
for _, r in map_df.iterrows():
    edges.append({"source": r["factor_id"], "target": r["primary_mechanism_id"], "relation": "primary", "confidence": r["mechanism_confidence"]})
    for sec in r["secondary_mechanism_ids"]:
        edges.append({"source": r["factor_id"], "target": sec, "relation": "secondary", "confidence": 0.5})
edge_dir = Path("data/research/mechanism_edges")
edge_dir.mkdir(parents=True, exist_ok=True)
pd.DataFrame(edges).to_parquet(edge_dir / "factor_mechanism_edges.parquet", index=False)

lines = ["# FACTOR MECHANISM DIAGNOSIS (A2)", ""]
lines.append("## Diagnosis for 15 surviving factors (9 ROBUST_PRETEST + 6 PROMISING) and 3 rejected for completeness")
lines.append("")
for _, r in map_df.iterrows():
    sec = ", ".join(r["secondary_mechanism_ids"])
    lines.append(f"### {r['factor_id']}")
    lines.append(f"- primary_mechanism: `{r['primary_mechanism_id']}` (confidence {r['mechanism_confidence']})")
    lines.append(f"- secondary: {sec}")
    lines.append(f"- alternative_explanation: {r['alternative_explanation']}")
    lines.append("")
lines.append("## Key conclusions")
lines.append("- Reversal factors survive size neutralization (RankIC drops ~30% but stays positive) and industry neutralization (mostly unchanged) -> not pure size/industry proxy.")
lines.append("- Most likely mechanism for 20D/30D reversal: **T+1 delayed supply** plus attention overreaction; selling exhaustion is secondary and needs flow data to separate.")
lines.append("- Low amount / low volume burst are largely size/liquidity proxies (size-neutral RankIC ~0).")
lines.append("- Low amount * Top500 retains positive after size neutral (0.013) -> liquidity filter + low-heat is a distinct conditional mechanism.")
lines.append("- Overnight gap standalone is weak/extreme-sensitive; only conditional variants survive.")
Path("reports/FACTOR_MECHANISM_DIAGNOSIS.md").write_text("\n".join(lines), encoding="utf-8")
print("A2 report written")


if __name__ == "__main__":
    main()
