import json
from pathlib import Path

def test_a6_gate_pass():
    g = json.loads(Path("reports/A6_GATE.json").read_text(encoding="utf-8"))
    assert g["A6_PASS"] is True
    assert g["STRATEGIES_ROBUST_PRETEST"] == 0

def test_red_team_results_exist():
    import pandas as pd
    r = pd.read_parquet("data/research/red_team_results.parquet")
    assert len(r) >= 40
    assert set(r["verdict"]) == {"REJECTED"}
