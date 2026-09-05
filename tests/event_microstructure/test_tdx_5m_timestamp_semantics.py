import sys
from pathlib import Path
sys.path.insert(0, "scripts")
import c1_event_microstructure as c

def test_lc5_first_bar_is_0935_not_0930():
    p = Path("E:/new_tdx_mock/vipdoc/sh/fzline/sh600000.lc5")
    rec = c.parse_lc5_first_record(p)
    assert rec["date"] >= 20241009
    assert rec["minute"] == 935  # bar labelled by END time; no 09:30 bar

def test_tdx_date_encoding_formula():
    p = Path("E:/new_tdx_mock/vipdoc/sz/fzline/sz000001.lc5")
    rec = c.parse_lc5_first_record(p)
    assert 20241001 <= rec["date"] <= 20250801
