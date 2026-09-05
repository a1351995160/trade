import pandas as pd
from chanlun_trader.research.red_team_utils import ReportConsistencyValidator

def test_status_numeric_consistency():
    df = pd.DataFrame([
        dict(strategy_id="A", status="PROMISING", profit_factor=1.2, total_return=0.1, sharpe=0.4),
        dict(strategy_id="B", status="REJECTED", profit_factor=0.8, total_return=-0.1, sharpe=-0.2),
    ])
    v = ReportConsistencyValidator().validate_status_vs_numbers(df)
    assert v.passed
    assert v.report()["report_consistency_status"] == "PASS"

def test_rejected_but_numbers_meet_gate_flags_error():
    df = pd.DataFrame([dict(strategy_id="C", status="REJECTED", profit_factor=1.3, total_return=0.2, sharpe=0.5)])
    v = ReportConsistencyValidator().validate_status_vs_numbers(df)
    assert not v.passed
    assert v.report()["errors"]
