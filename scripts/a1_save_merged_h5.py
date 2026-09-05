"""Save merged h5 TRAIN factor+label table for A1 concentration/walk-forward/placebo (avoid rebuild)."""
from __future__ import annotations
import sys, time
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import pandas as pd
from m7_discovery_round2 import build_features

TRAIN_START, TRAIN_END = 20220801, 20240731
FACTORS = ["F_REV20","F_REV5","F_LOWVOLBURST","F_LOWAMT","F_VOLCOMP_LOW","F_GAP","F_UP5_INV","F_IND_DISP_LOW",
           "R2_REV10","R2_REV30","R2_REV_COMPOSITE","R2_REV20_TOPLIQ","R2_LOWAMT_TOPLIQ","R2_LOWVOL_TOPLIQ",
           "R2_LOWAMT_HIGHPRICE","R2_REV20_NOLIMIT","R2_REV20_INDUP","R2_GAP_NOTUP5"]

def main():
    t0 = time.time()
    ft = build_features()[["symbol","date"] + FACTORS]
    ft = ft[(ft["date"] >= TRAIN_START) & (ft["date"] <= TRAIN_END)]
    lab = pd.read_parquet("data/research/tradable_returns.parquet")
    lab = lab[(lab["horizon"]==5) & (lab["timestamp"]>=TRAIN_START) & (lab["timestamp"]<=TRAIN_END)]
    lab = lab.rename(columns={"tradable_return":"future_return"})[["symbol","timestamp","future_return"]]
    m = ft.merge(lab, left_on=["symbol","date"], right_on=["symbol","timestamp"], how="inner")
    m = m.drop(columns=["timestamp"])
    m.to_parquet("data/research/robustification_results/a1_merged_h5_train.parquet", index=False)
    print("saved", m.shape, round(time.time()-t0,1))

if __name__ == "__main__":
    main()
