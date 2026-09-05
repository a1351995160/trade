import json
from pathlib import Path

def test_a7_gate_pass():
    g = json.loads(Path("reports/A7_GATE.json").read_text(encoding="utf-8"))
    assert g["A7_PASS"] is True
    assert g["PRODUCTION_CANDIDATES_COUNT"] == 0
    assert g["NO_TRADE_DEFAULT"] is True

def test_snapshot_production_empty():
    snap = json.loads(Path("data/research/daily_selection_v2/20240731.json").read_text(encoding="utf-8"))
    assert snap["production_candidates"] == []
    assert snap["no_trade"] is True
    assert snap["selection_date"] == 20240731
