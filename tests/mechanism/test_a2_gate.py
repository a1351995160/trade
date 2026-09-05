import json
from pathlib import Path

def test_a2_gate_pass():
    g = json.loads(Path("reports/A2_GATE.json").read_text(encoding="utf-8"))
    assert g["A2_PASS"] is True
    assert g["factors_mapped"] == 18

def test_factor_mechanism_map_exists():
    import pandas as pd
    m = pd.read_parquet("data/research/factor_mechanism_map/factor_mechanism_map.parquet")
    assert len(m) == 18
    assert {"primary_mechanism_id","secondary_mechanism_ids","alternative_explanation"}.issubset(m.columns)
