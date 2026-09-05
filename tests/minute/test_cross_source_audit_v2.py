import pandas as pd

from scripts.cross_source_5m_audit_v2 import _pair_metrics


def _frame(close: float, source: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "symbol": "600016.SH",
        "timestamp": pd.Timestamp("2024-10-09 09:35:00", tz="Asia/Shanghai"),
        "trade_date": 20241009,
        "bar_time": "09:35",
        "open": 4.08, "high": 4.10, "low": 4.00, "close": close,
        "volume": 100, "amount": 400.0, "source": source,
    }])


def test_cross_source_no_reference_data_is_not_mismatch():
    result = _pair_metrics(_frame(4.08, "baostock"), pd.DataFrame(), "baostock", "tdx_raw_hq")
    assert result["classification"] == "NO_REFERENCE_DATA"
    assert result["matched_bars"] == 0


def test_cross_source_tolerance_and_true_mismatch_are_distinct():
    within = _pair_metrics(_frame(4.08, "baostock"), _frame(4.09, "tdx_raw_hq"), "baostock", "tdx_raw_hq")
    mismatch = _pair_metrics(_frame(4.08, "baostock"), _frame(4.20, "tdx_raw_hq"), "baostock", "tdx_raw_hq")
    assert within["classification"] == "MATCHED_WITHIN_TOLERANCE"
    assert mismatch["classification"] == "TRUE_DATA_MISMATCH"
    assert within["matched_bars"] == mismatch["matched_bars"] == 1
