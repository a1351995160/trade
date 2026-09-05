import pandas as pd
from pathlib import Path

def test_full_engine_red_team_results_exist():
    df = pd.read_parquet("data/research/strategy_translation_results/full_engine_red_team_results.parquet")
    assert len(df) >= 20
    assert set(df["stress"]).issuperset({"cost_x2", "cost_x3", "slippage_x2", "slippage_x3", "delay_1", "delay_2"})

def test_all_stress_has_metrics_no_placeholder():
    df = pd.read_parquet("data/research/strategy_translation_results/full_engine_red_team_results.parquet")
    for _, r in df.iterrows():
        assert pd.notna(r["total_return"])
        assert pd.notna(r["sharpe"])
        assert pd.notna(r["profit_factor"])
        assert pd.notna(r["trade_count"])
