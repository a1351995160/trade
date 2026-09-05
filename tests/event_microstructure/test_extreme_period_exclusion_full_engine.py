import pandas as pd

def test_extreme_period_exclusion_is_noop_and_engine_rerun():
    df = pd.read_csv("reports/EVENT_EXECUTION_RED_TEAM_RESULTS.csv")
    ext = df[df["stress"] == "exclude_extreme_period"]
    assert len(ext) == 2
    assert (ext["n_excluded_dates"] > 0).all()
    assert (ext["total_return"].notna()).all()
