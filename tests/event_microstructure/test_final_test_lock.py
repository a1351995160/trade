import json
from pathlib import Path

def test_final_test_still_sealed():
    js = json.loads(Path("reports/FINAL_STATUS_EVENT_MICROSTRUCTURE.json").read_text(encoding="utf-8"))
    assert js["FINAL_TEST_STATUS"] == "SEALED"
    assert js["KNOWN_P0"] == 0
    assert js["READY_FOR_FINAL_TEST"] == "NO"
