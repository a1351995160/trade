import json
from pathlib import Path

def test_a3_gate_pass():
    g = json.loads(Path("reports/A3_GATE.json").read_text(encoding="utf-8"))
    assert g["A3_PASS"] is True
    assert g["EXTERNAL_PACKAGE_COUNT"] == 3
    assert g["EXTERNAL_SEEDS_IMPORTED"] == 310
    assert g["DEEP_FUSION_SEEDS_IMPORTED"] == 60

def test_zips_preserved():
    zips = list(Path("research_inputs/external_alpha").glob("*.zip"))
    assert len(zips) == 3
