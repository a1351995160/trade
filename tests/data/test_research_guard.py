import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pandas as pd
import pytest

from chanlun_trader.research.guard import (
    FinalTestAccessViolation,
    ResearchDataAccessGuard,
    RESEARCH_END,
    FINAL_TEST_START,
)


def test_guard_allows_research_dates():
    g = ResearchDataAccessGuard()
    g.check_date(2022_01_01)
    g.check_date(RESEARCH_END)
    g.check_range(2022_08_01, 2025_07_31)


def test_guard_rejects_final_test_date():
    g = ResearchDataAccessGuard()
    with pytest.raises(FinalTestAccessViolation):
        g.check_date(FINAL_TEST_START)
    with pytest.raises(FinalTestAccessViolation):
        g.check_date(2026_01_01)
    with pytest.raises(FinalTestAccessViolation):
        g.check_range(2025_07_01, 2025_08_05)


def test_guard_rejects_final_test_dataframe():
    g = ResearchDataAccessGuard()
    df = pd.DataFrame({"date": [20250601, 20250801, 20250731]})
    with pytest.raises(FinalTestAccessViolation):
        g.check_frame(df, "date")


def test_guard_ok_dataframe_passes():
    g = ResearchDataAccessGuard()
    df = pd.DataFrame({"date": [20220101, 20250731]})
    g.check_frame(df, "date")
