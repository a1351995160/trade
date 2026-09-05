import json
from pathlib import Path

def test_validation_exposure_controlled():
    g = json.loads(Path("reports/A8_GATE.json").read_text(encoding="utf-8"))
    assert g["NEW_FORMAL_VALIDATION_EXPOSURE"] <= 1
    assert g["VALIDATION_REUSE_RISK"] in ("LOW","MEDIUM","HIGH")

def test_no_new_research_uses_validation():
    import pandas as pd
    # all A4 hypothesis results must be TRAIN-only evidence
    r = pd.read_parquet("data/research/robustification_results/a4_hypothesis_results.parquet")
    assert len(r) == 60
    # no validation rank ic columns in A4 result
    assert "validation_rank_ic" not in r.columns
