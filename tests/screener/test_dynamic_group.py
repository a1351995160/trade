import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pandas as pd

from chanlun_trader.research.screener import DynamicGroup, ScreenerRule


def test_dynamic_group_enter_stay_exit_reenter():
    rule = ScreenerRule(rule_id="R1", name="连续上涨5日", description="")
    g = DynamicGroup("G1", rule)
    s1 = pd.Series({"A": True, "B": False})
    e1 = g.events_for_day(["A", "B"], s1)
    assert e1.iloc[0]["event_type"] == "ENTER"
    s2 = pd.Series({"A": True, "B": True})
    e2 = g.events_for_day(["A", "B"], s2)
    assert e2.iloc[0]["event_type"] == "STAY"
    assert e2.iloc[1]["event_type"] == "ENTER"
    s3 = pd.Series({"A": False, "B": True})
    e3 = g.events_for_day(["A", "B"], s3)
    assert e3.iloc[0]["event_type"] == "EXIT"
    assert e3.iloc[1]["event_type"] == "STAY"
    s4 = pd.Series({"A": True, "B": True})
    e4 = g.events_for_day(["A", "B"], s4)
    assert e4.iloc[0]["event_type"] == "REENTER"
    assert e4.iloc[1]["event_type"] == "STAY"
