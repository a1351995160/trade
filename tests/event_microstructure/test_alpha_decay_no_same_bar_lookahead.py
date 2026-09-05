import pandas as pd

def test_intraday_decay_points_marked_unknown_not_estimated():
    df = pd.read_csv("reports/T1_ALPHA_DECAY_CURVE.csv")
    intra = df[df["entry"].isin(["plus5m", "plus10m", "plus15m", "plus30m"])]
    assert len(intra) == 8
    assert (intra["note"].str.contains("DATA_UNKNOWN")).all()
    assert intra["mean_gross"].isna().all()
