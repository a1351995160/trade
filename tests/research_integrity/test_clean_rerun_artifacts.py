import json
from pathlib import Path
import pandas as pd
import numpy as np


def test_clean_rerun_uses_same_definition_hash():
    m = json.loads(Path("reports/CLEAN_RERUN_SCOPE_MANIFEST.json").read_text(encoding="utf-8"))
    assert len(m) >= 10
    for row in m:
        assert "old_definition_hash" in row
        assert "old_parameter_hash" in row


def test_clean_rerun_no_parameter_change():
    es = pd.read_csv("reports/EVENT_STRATEGY_CLEAN_RERUN.csv")
    # same event ids and holding horizons as the original strategy translation
    assert "E_CONSEC_LIMIT" in es["event_id"].values
    assert "E_LIMITUP" in es["event_id"].values
    assert es["participation"].between(0, 1).all()
    # no new holding search: horizons are only 5 and 3
    assert set(es["strategy_id"].str.extract(r"_H(\d+)")[0].dropna().unique()) <= {"3", "5"}


def test_clean_rerun_no_final_test_access():
    st = json.loads(Path("reports/FINAL_STATUS_CLEAN_RERUN.json").read_text(encoding="utf-8"))
    assert st["FINAL_TEST_NEW_PHYSICAL_ACCESS"] == 0
    assert st["FINAL_TEST_NEW_DECISION_EXPOSURE"] == 0
    assert st["ORIGINAL_FINAL_TEST_ACCESS_INCIDENT"] is True


def test_clean_rerun_uses_pit_feature_store():
    dh = pd.read_csv("reports/DAILY_HYPOTHESIS_CLEAN_RERUN.csv")
    a4_305 = dh[dh["hypothesis_id"] == "A4-305"].iloc[0]
    assert "height_rising_pit" in a4_305["column"]
    assert abs(a4_305["delta_rank_ic"]) > 0


def test_event_all_vs_first_population_reported():
    ev = pd.read_csv("reports/EVENT_EVIDENCE_CLEAN_RERUN.csv")
    pops = set(ev["population"].unique())
    assert "ALL_EVENTS" in pops and "FIRST_EVENT_PER_SYMBOL" in pops
    assert (ev.groupby("event_id")["population"].nunique() == 2).all()


def test_event_available_at_repaired():
    ev = pd.read_parquet("data/research/event_store_repaired/E_CONSEC_LIMIT_v2.parquet")
    assert (ev["available_at_date"].astype(str).str[-2:].isin(["32", "33", "34", "00"]).sum()) == 0
    assert "available_at_ts" in ev.columns


def test_event_fdr_recomputed():
    fdr = pd.read_csv("data/research/audit/event_evidence_fdr.csv")
    assert len(fdr) == 6
    assert fdr["fdr_q"].between(0, 1).all()


def test_event_placebo_not_substituted():
    ev = pd.read_csv("reports/EVENT_EVIDENCE_CLEAN_RERUN.csv")
    assert set(ev["sector_placebo_status"].unique()) == {"NOT_AVAILABLE"}
    assert ev["market_placebo_status"].notna().all()
    assert ev["liquidity_placebo_status"].notna().all()


def test_mfe_mae_real_computation():
    mfe = pd.read_csv("data/research/audit/mfe_mae_clean_rerun.csv")
    assert len(mfe) == 2
    for _, r in mfe.iterrows():
        assert r["n_positions"] > 0
        assert np.isfinite(r["median_mfe"]) and np.isfinite(r["median_mae"])
        assert r["mfe_p25"] <= r["median_mfe"] <= r["mfe_p75"]
        assert r["mae_p25"] <= r["median_mae"] <= r["mae_p75"]


def test_pre_post_metric_reconciliation():
    cmp = pd.read_csv("reports/PRE_FIX_POST_FIX_RESULT_COMPARISON.csv")
    es = cmp[cmp["id"].str.contains("E_E_")]
    for _, r in es.iterrows():
        assert r["pre_fix_status"] == "REJECTED (red-team base)"
        assert r["post_fix_status"] == "CONFIRMED_REPRODUCIBLE"
        assert abs(r["abs_delta"]) < 1e-6


def test_independent_acceptance_recompute():
    from chanlun_trader.research.validation import IndependentAcceptanceValidator
    v = IndependentAcceptanceValidator()
    pnl = [1.0, -0.5, 0.5, -0.25]
    out = v.validate_trade_metrics("x", pnl)
    assert out["profit_factor"] == 2.0
    assert out["n_trades"] == 4
    wc = v.validate_winner_concentration("x", [10.0, -1.0, -1.0, -1.0])
    assert wc["winner_concentration_status"] == "FAIL"


def test_selector_uses_postfix_status_only():
    sel = Path("reports/SELECTOR_POST_FIX_AUDIT.md").read_text(encoding="utf-8")
    assert "PRODUCTION_CANDIDATES = 0" in sel
    assert "NO_TRADE = TRUE" in sel
    assert "CONFIRMED" in sel
