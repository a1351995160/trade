from datetime import date

import pandas as pd

from scripts.close_5m_gap_gates import _baostock_15m_gap_evidence, _reconstruct_tdx_transaction_bar
from chanlun_trader.data.minute.base import BAR_TIMESTAMP_SEMANTICS
from chanlun_trader.data.minute.gap_resolution import build_gap_evidence, classify_gap
from chanlun_trader.data.minute.pit_security_master import (
    PITSecurityMaster,
    PITSecurityRecord,
    SecurityState,
)
from chanlun_trader.data.minute.validator import expected_session_times


def _bars(times):
    return pd.DataFrame({
        "symbol": ["000895.SZ"] * len(times),
        "timestamp": pd.to_datetime([f"2024-06-06 {t}:00+08:00" for t in times]),
        "bar_time": times,
    })


def test_gap_resolution_keeps_provider_gap_and_missing_times():
    times = sorted(expected_session_times(BAR_TIMESTAMP_SEMANTICS) - {"13:40", "13:45"})
    daily = pd.DataFrame([{"date": 20240606, "volume": 9159866}])
    evidence = build_gap_evidence(
        "000895.SZ", 20240606, _bars(times), _bars(times), pd.DataFrame(), pd.DataFrame(), daily,
        security_state="ACTIVE", suspension_state="UNKNOWN",
    )
    assert evidence.observed_count == 46
    assert evidence.missing_bar_times == ("13:40", "13:45")
    assert evidence.classification == "PROVIDER_DATA_GAP"


def test_empty_provider_data_is_not_suspension_without_explicit_evidence():
    assert classify_gap(0, 48, 0, None, explicit_suspension=False) == "UNKNOWN"
    assert classify_gap(0, 48, 0, None, explicit_suspension=True) == "EXPECTED_SUSPENSION"


def test_tdx_transaction_reference_bar_uses_bar_end_window_and_shares_conversion():
    bar = _reconstruct_tdx_transaction_bar([
        {"time": "13:35", "price": 25.20, "vol": 99},
        {"time": "13:37", "price": 25.01, "vol": 1000},
        {"time": "13:40", "price": 24.98, "vol": 1508},
        {"time": "13:41", "price": 24.90, "vol": 500},
    ], "13:40")
    assert bar is not None
    assert bar["volume_lots"] == 2508
    assert bar["volume_shares"] == 250800
    assert (bar["open"], bar["high"], bar["low"], bar["close"]) == (25.01, 25.01, 24.98, 24.98)
    assert classify_gap(46, 48, 1, 9159866, cross_source_evidence=True) == "CROSS_SOURCE_CONFLICT"


def test_pit_security_state_is_date_as_of_and_keeps_suspension_unknown():
    master = PITSecurityMaster([PITSecurityRecord(
        symbol="600900.SH", exchange="SH", board="SH_MAIN",
        list_date="2003-11-18", delist_date=None,
        valid_from="2003-11-18", valid_to=None,
        pit_listing_status="PASS", pit_delist_status="PASS",
        pit_st_status="UNKNOWN", pit_suspension_status="UNKNOWN",
        source="test", evidence="dated list date",
    )])
    assert master.SecurityStateAsOf("600900.SH", 20031117)["security_state"] == SecurityState.NOT_LISTED
    assert master.SecurityStateAsOf("600900.SH", 20221026)["security_state"] == SecurityState.ACTIVE
    assert master.SecurityStateAsOf("600900.SH", 20221026, market_data_present=False)["security_state"] == SecurityState.UNKNOWN
    assert master.SecurityStateAsOf("600900.SH", 20221026, suspension_status="SUSPENDED")["security_state"] == SecurityState.SUSPENDED


def test_pit_snapshot_status_does_not_fill_historical_st_or_suspension():
    from chanlun_trader.data.minute.pit_security_master import make_record

    record = make_record("000895.SZ", {
        "ipoDate": "1998-12-10", "outDate": "", "status": "1", "code_name": "x",
    }, "SZ_MAIN")
    assert record.pit_st_status == "UNKNOWN"
    assert record.pit_suspension_status == "UNKNOWN"


def test_15m_aggregate_confirms_activity_without_reconstructing_5m_bars():
    class Result:
        error_code = "0"
        error_msg = "success"
        fields = ["date", "time", "code", "open", "high", "low", "close", "volume", "amount", "adjustflag"]

        def __init__(self):
            self._row = {
                "date": "2024-06-06", "time": "20240606134500000", "code": "sz.000895",
                "open": "25.0400", "high": "25.0400", "low": "24.9500", "close": "24.9700",
                "volume": "566700", "amount": "14158368.0000", "adjustflag": "3",
            }
            self._read = False

        def next(self):
            if self._read:
                return False
            self._read = True
            return True

        def get_row_data(self):
            return [self._row[field] for field in self.fields]

    class BaoStock:
        def query_history_k_data_plus(self, *args, **kwargs):
            assert kwargs["frequency"] == "15"
            assert kwargs["adjustflag"] == "3"
            return Result()

    class Provider:
        bs = BaoStock()

    five_minute = pd.DataFrame([{
        "symbol": "000895.SZ", "timestamp": pd.Timestamp("2024-06-06 13:35:00+08:00"),
        "trade_date": 20240606, "bar_time": "13:35", "volume": 82800, "amount": 2070960.0,
    }])
    evidence = _baostock_15m_gap_evidence(Provider(), "000895.SZ", 20240606, five_minute)

    assert evidence["inferred_missing_interval_activity"] is True
    assert evidence["inferred_missing_interval_volume"] == 483900
    assert "individual 5m OHLCV is not reconstructed" in evidence["interpretation"]
