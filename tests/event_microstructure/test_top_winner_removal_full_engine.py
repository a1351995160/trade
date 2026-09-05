import pandas as pd
from pathlib import Path

def test_top_winner_removal_rows_exist_and_are_engine_reruns():
    df = pd.read_csv("reports/EVENT_EXECUTION_RED_TEAM_RESULTS.csv")
    removals = df[df["stress"].str.startswith("remove_top")]
    assert len(removals) >= 10
    for _, r in removals.iterrows():
        assert pd.notna(r["total_return"])
        assert pd.notna(r["profit_factor"])
        assert r["n_excluded_symbols"] > 0
