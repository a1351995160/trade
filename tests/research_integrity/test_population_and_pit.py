import pandas as pd
from pathlib import Path
from chanlun_trader.research.validation import IndependentAcceptanceValidator, RequiredEvidenceCompletenessValidator


def test_future_distribution_mutation():
    p = Path("data/research/audit/market_sentiment_pit_audit.parquet")
    assert p.exists()
    sp = pd.read_parquet(p).sort_index()
    for T in [20230131, 20231031, 20240131, 20240731]:
        for col in ["height", "limit_up"]:
            stored = sp.loc[T, col + "_pit_pct"]
            recomputed = sp.loc[:T, col].rank(pct=True).iloc[-1]
            assert abs(float(stored) - float(recomputed)) < 1e-12
            # full-sample version is different somewhere
    assert (sp["height_full_pct"] - sp["height_pit_pct"]).abs().max() > 0.01


def test_event_population_consistency():
    cmp = pd.read_csv("data/research/audit/event_population_comparison.csv")
    first = cmp[cmp["population"] == "FIRST_EVENT_PER_SYMBOL"].set_index("event_id")["n"]
    all_ = cmp[cmp["population"] == "ALL_EVENTS"].set_index("event_id")["n"]
    assert (all_ - first).abs().sum() > 0
    assert int(all_.loc["E_CONSEC_LIMIT"]) > int(first.loc["E_CONSEC_LIMIT"])


def test_event_clustered_inference():
    cmp = pd.read_csv("data/research/audit/event_population_comparison.csv")
    assert cmp["date_block_bootstrap_mean"].notna().all()
    assert cmp["ci2_5"].notna().all()
    assert cmp["ci97_5"].notna().all()
    # block bootstrap CIs must be finite and ordered
    assert (cmp["ci2_5"] <= cmp["date_block_bootstrap_mean"]).all()
    assert (cmp["date_block_bootstrap_mean"] <= cmp["ci97_5"]).all()


def test_required_evidence_completeness():
    v = RequiredEvidenceCompletenessValidator()
    v.require("A", True, True).require("B", True, True).require("C", True, False)
    assert not v.passed
    v2 = RequiredEvidenceCompletenessValidator()
    v2.require("A", True, True).require("B", True, True)
    assert v2.passed


def test_acceptance_recomputes_metrics():
    v = IndependentAcceptanceValidator()
    out = v.validate_trade_metrics("x", [1.0, -0.5, 0.5, -0.25])
    assert out["n_trades"] == 4
    assert out["profit_factor"] > 1.0
    wc = v.validate_winner_concentration("x", [10.0, -1.0, -1.0, -1.0])
    assert wc["top1"] > 0.5
    assert wc["winner_concentration_status"] == "FAIL"


def test_final_test_incident_persisted():
    p = Path("data/research/audit/final_test_access_incidents.jsonl")
    assert p.exists()
    import json
    lines = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) >= 1
    assert lines[0]["physical_read"] is True
    assert lines[0]["final_test_physical_access"] == "ACCIDENTAL_NON_ANALYTICAL"
