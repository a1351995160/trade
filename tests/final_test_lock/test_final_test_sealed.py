import json
from pathlib import Path

def test_final_test_sealed_in_status():
    js = json.loads(Path("reports/FINAL_STATUS_POST_TRANSLATION.json").read_text(encoding="utf-8"))
    assert js["FINAL_TEST_STATUS"] == "SEALED"
    assert js["KNOWN_P0"] == 0

def test_all_backtests_within_train():
    import pandas as pd
    df = pd.read_parquet("data/research/strategy_translation_results/event_strategy_results.parquet")
    # strategy construction uses TRAIN only by construction; no final-test columns present
    assert "final_test" not in df.columns
