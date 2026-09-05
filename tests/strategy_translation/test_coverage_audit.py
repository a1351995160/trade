import json
from pathlib import Path

def test_coverage_audit_exists_and_covers_events():
    js = json.loads(Path("reports/STRATEGY_TRANSLATION_COVERAGE_AUDIT.json").read_text(encoding="utf-8"))
    assert len(js) >= 22
    event_rows = [r for r in js if r.get("event_id")]
    assert len(event_rows) == 6

def test_every_promising_event_has_translation_status():
    js = json.loads(Path("reports/STRATEGY_TRANSLATION_COVERAGE_AUDIT.json").read_text(encoding="utf-8"))
    prom = [r for r in js if r["source"] == "TDX_TQ_DATA"]
    assert len(prom) >= 6
    assert all(r["translation_status"] in ("NOT_TRANSLATED", "PARTIAL_TRANSLATION", "EXACT_TRANSLATION", "GENERIC_BASELINE_ONLY", "NOT_STRATEGIZABLE", "DATA_LIMITED") for r in js)
