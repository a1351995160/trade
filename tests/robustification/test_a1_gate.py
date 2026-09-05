import json
from pathlib import Path

def test_a1_gate_pass():
    g = json.loads(Path("reports/A1_GATE.json").read_text(encoding="utf-8"))
    assert g["A1_PASS"] is True
    assert g["factors_audited"] == 18

def test_a1_verdicts_exist():
    import pandas as pd
    v = pd.read_parquet("data/research/robustification_results/a1_verdicts_final.parquet")
    assert len(v) == 18
    assert {"robust_pretest","promising","rejected"}.issubset(v.columns)
    assert int(v["robust_pretest"].sum()) + int(v["promising"].sum()) + int(v["rejected"].sum()) == 18
