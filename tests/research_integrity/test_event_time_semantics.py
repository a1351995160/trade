import pandas as pd
from chanlun_trader.research.event_time import next_session_date, semantics_for
from chanlun_trader.research.validation import extreme_regime_status, execution_gap_status

CAL = [20241030, 20241031, 20241101, 20241104]


def test_invalid_yyyymmdd_plus_one_removed():
    assert next_session_date(CAL, 20241031) == 20241101
    assert next_session_date(CAL, 20241031) != 20241032
    assert next_session_date(CAL, 20241104) is None


def test_event_generated_at_after_available_at():
    s = semantics_for("E_LIMITUP", 20241031, CAL)
    assert pd.Timestamp(s.available_at) <= pd.Timestamp(s.earliest_signal_at)
    assert "20241101" in s.earliest_execution


def test_limitup_available_at_close_confirmed():
    s = semantics_for("E_CONSEC_LIMIT", 20241031, CAL)
    assert "15:00:01" in s.available_at
    assert s.confidence == "HIGH"


def test_extreme_regime_not_applicable():
    # 2024-09-24..2024-10-08 is outside TRAIN 20220801..20240731
    assert extreme_regime_status(20220801, 20240731, 20240924, 20241008) == "NOT_APPLICABLE"


def test_execution_gap_unmeasurable_when_no_5m():
    assert execution_gap_status(False, False, False) == "UNMEASURABLE"
    assert execution_gap_status(True, True, True) == "MEASURED"
