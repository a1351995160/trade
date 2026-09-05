"""M2 RESEARCH EVALUATION GATE — FactorEvaluator / EventStudy。"""
import sys, json
from pathlib import Path
sys.path.insert(0, "src")
import pandas as pd
from chanlun_trader.research.factor import FactorStore
from chanlun_trader.research.label import LabelStore
from chanlun_trader.research.evaluation import FactorEvaluator
from chanlun_trader.research.event_study import EventStudy

def main():
    fs = FactorStore(Path("data/research/factor_store"))
    f = fs.query("F_MOM5", "v1")
    ls = LabelStore(Path("data/research/label_store"))
    labels = []
    for p in ls.root.glob("labels_*.parquet"):
        labels.append(pd.read_parquet(p))
    labels = pd.concat(labels) if labels else pd.DataFrame()
    ev = FactorEvaluator()
    res = ev.evaluate("F_MOM5", "v1", f, labels, horizon=5)
    es = EventStudy()
    events = pd.DataFrame({"symbol": f["symbol"].iloc[:10].tolist(), "event_time": f["timestamp"].iloc[:10].tolist()})
    er = es.run("DEMO_EVT", "v1", events, labels, horizon=5)
    out = {
        "factor_rows": len(f),
        "label_rows": len(labels),
        "factor_ic_mean": res.ic_mean,
        "factor_n_obs": res.n_obs,
        "event_study_n": er.n,
        "M2_PASS": res.n_obs > 1000 and res.ic_mean is not None and er.n >= 1,
    }
    Path("reports/M2_GATE.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))
    print("M2_PASS" if out["M2_PASS"] else "M2_FAIL")

if __name__ == "__main__":
    main()
