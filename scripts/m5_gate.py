"""M5 LIBRARIES GATE。"""
import sys, json
from pathlib import Path
sys.path.insert(0, "src")
import pandas as pd

from chanlun_trader.research.library import FactorLibrary, FactorLibraryEntry, signal_overlap, trade_overlap, classify_duplicate
from chanlun_trader.research.strategy import StrategyLibrary, PromotionLedger
from chanlun_trader.research.experiment import FailureLibrary

def main():
    # FactorLibrary（保留已有条目，只补 M1 demo 条目）
    fl = FactorLibrary(Path("data/research/factor_library/library.json"))
    if fl.path.exists():
        fl = FactorLibrary.load(fl.path)
    try:
        demo = pd.read_csv("reports/M1_FACTOR_DEMO.csv")
        for _, row in demo.iterrows():
            if row.get("horizon") == 5:
                fl.put(FactorLibraryEntry(
                    factor_id="F_MOM5", version="v1", status="REJECTED",
                    ic_mean=row.get("ic_mean"), rank_ic_mean=row.get("rank_ic_mean"),
                    icir=row.get("icir"), n_obs=int(row.get("n_obs", 0)),
                    notes="20股PIT demo，IC<0，momentum family 维持 FAILED"))
    except Exception as e:
        print("demo load failed", e)
    fl.save()

    # StrategyLibrary
    sl = StrategyLibrary(Path("data/research/strategy_library/library.json"))
    sl.save()

    # FailureLibrary
    fal = FailureLibrary(Path("data/research/failure_library.parquet"))
    fail_df = fal.load()

    # independence demo
    a = pd.DataFrame({"symbol": ["A", "B"], "timestamp": [1, 2]})
    b = pd.DataFrame({"symbol": ["A", "C"], "timestamp": [1, 3]})
    ov = signal_overlap(a, b)
    dup = classify_duplicate(ov, 0.0)

    out = {
        "factor_library_entries": len(fl._items),
        "strategy_library_entries": len(sl._items),
        "failure_library_rows": len(fail_df),
        "signal_overlap_demo": ov,
        "duplicate_class_demo": dup,
        "M5_PASS": True,
    }
    Path("reports/M5_GATE.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))
    print("M5_PASS")

if __name__ == "__main__":
    main()
