from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from chanlun_trader.data.minute.base import BAR_TIMESTAMP_SEMANTICS, BarTimestampSemantics, date_chunks
from chanlun_trader.data.minute.baostock_provider import BaoStock5MinProvider
from chanlun_trader.data.minute.downloader import Historical5mDownloader
from chanlun_trader.data.minute.manifest import ImmutableRawStore, ManifestStore, Normalized5mStore
from chanlun_trader.data.minute.normalizer import normalize_5m_frame
from chanlun_trader.data.minute.tdx_raw_hq_provider import TdxRawHQProvider
from chanlun_trader.data.minute.universe import STAGE1_SYMBOLS, stage2_manifest, stage2_symbols
from chanlun_trader.data.minute.validator import DataQualityError, validate_5m


def _raw_rows(symbol="600519.SH"):
    return pd.DataFrame({
        "timestamp": pd.DatetimeIndex(["2024-07-31 09:35", "2024-07-31 09:35", "2024-07-31 09:40"],
                                       tz="Asia/Shanghai"),
        "open": [10.0, 10.0, 10.1], "high": [10.2, 10.2, 10.3],
        "low": [9.9, 9.9, 10.0], "close": [10.1, 10.1, 10.2],
        "volume": [1000, 1000, 1200], "amount": [10000, 10000, 12000],
        "source_symbol": ["sh.600519"] * 3,
    })


def test_baostock_chunking_uses_natural_day_ranges():
    chunks = list(date_chunks(date(2022, 8, 1), date(2024, 7, 31), 120))
    assert len(chunks) == 7
    assert chunks[0] == (date(2022, 8, 1), date(2022, 11, 28))
    assert chunks[-1][1] == date(2024, 7, 31)


def test_stage2_manifest_is_fixed_and_board_balanced():
    manifest = stage2_manifest()
    assert len(manifest) == 50
    assert len(stage2_symbols()) == len(set(stage2_symbols()))
    assert not set(stage2_symbols()).intersection(STAGE1_SYMBOLS)
    assert {item["board"] for item in manifest} == {"SH_MAIN", "SZ_MAIN", "GEM", "STAR"}
    assert {board: sum(item["board"] == board for item in manifest)
            for board in {item["board"] for item in manifest}} == {
                "SH_MAIN": 20, "SZ_MAIN": 15, "GEM": 10, "STAR": 5
            }


class FakeBaoStockResult:
    fields = ["date", "time", "code", "open", "high", "low", "close", "volume", "amount", "adjustflag"]
    error_code = "0"
    error_msg = ""

    def __init__(self):
        self.rows = [["2024-07-31", "20240731093500000", "sh.600519", "10", "10.2", "9.9", "10.1", "1000", "10000", "3"]]
        self.index = -1

    def next(self):
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self):
        return self.rows[self.index]


class FakeBaoStock:
    def __init__(self):
        self.login_count = 0
        self.logout_count = 0
        self.adjustflags = []

    def login(self):
        self.login_count += 1
        return type("Login", (), {"error_code": "0", "error_msg": ""})()

    def logout(self):
        self.logout_count += 1

    def query_history_k_data_plus(self, code, fields, **kwargs):
        self.adjustflags.append(kwargs["adjustflag"])
        return FakeBaoStockResult()


def test_baostock_session_reuses_login_and_keeps_no_adjustment():
    api = FakeBaoStock()
    provider = BaoStock5MinProvider(api)
    with provider.session():
        first = provider.fetch("600519.SH", date(2024, 7, 31), date(2024, 7, 31))
        second = provider.fetch("600519.SH", date(2024, 7, 31), date(2024, 7, 31))
        assert len(first) == len(second) == 1
        assert api.login_count == 1 and api.logout_count == 0
    assert api.logout_count == 1
    assert api.adjustflags == ["3", "3"]


def test_baostock_session_can_be_reset_after_remote_disconnect():
    api = FakeBaoStock()
    provider = BaoStock5MinProvider(api)
    with provider.session():
        provider.reset_session()
        assert api.login_count == 2 and api.logout_count == 1
    assert api.logout_count == 2


def test_normalizer_deduplicates_and_sets_bar_end_semantics():
    out = normalize_5m_frame(_raw_rows(), "600519.SH", "baostock")
    assert BAR_TIMESTAMP_SEMANTICS == BarTimestampSemantics.BAR_END
    assert list(out["bar_time"]) == ["09:35", "09:40"]
    assert len(out) == 2
    assert out["timestamp"].dt.tz is not None
    assert out["volume"].dtype.kind in "iu"


def test_malformed_row_is_rejected():
    out = normalize_5m_frame(_raw_rows().iloc[[0]], "600519.SH", "baostock")
    out.loc[0, "open"] = float("nan")
    report = validate_5m(out, BarTimestampSemantics.BAR_END, strict=False)
    assert report.malformed_count > 0
    with pytest.raises(DataQualityError):
        validate_5m(out, BarTimestampSemantics.BAR_END, strict=True)


def test_session_time_validation_rejects_lunch_or_non_session_label():
    frame = normalize_5m_frame(_raw_rows().iloc[[0]], "600519.SH", "baostock")
    frame.loc[0, "timestamp"] = pd.Timestamp("2024-07-31 11:35", tz="Asia/Shanghai")
    frame.loc[0, "bar_time"] = "11:35"
    report = validate_5m(frame, BarTimestampSemantics.BAR_END, strict=False)
    assert report.invalid_session_count == 1


class FakeProvider:
    source = "fake"
    timestamp_semantics = BarTimestampSemantics.BAR_END

    def __init__(self):
        self.calls = 0

    def fetch(self, symbol, start_date, end_date):
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("transient")
        return _raw_rows(symbol)


def test_downloader_retry_resume_and_manifest(tmp_path):
    provider = FakeProvider()
    raw = ImmutableRawStore(tmp_path / "raw")
    normalized = Normalized5mStore(tmp_path / "normalized")
    downloader = Historical5mDownloader(provider, raw, normalized, chunk_days=120, retries=1)
    first = downloader.download("600519.SH", date(2024, 7, 31), date(2024, 7, 31))
    assert first.status == "COMPLETE"
    assert first.chunks[0]["retry_count"] == 1
    calls = provider.calls
    second = downloader.download("600519.SH", date(2024, 7, 31), date(2024, 7, 31))
    assert second.status == "COMPLETE"
    assert second.chunks[0]["status"] == "RESUMED"
    assert provider.calls == calls
    records = raw.manifest.records()
    assert records and records[-1]["sha256"]


def test_raw_store_is_immutable_and_audits_conflict(tmp_path):
    raw = ImmutableRawStore(tmp_path / "raw")
    a = _raw_rows().iloc[[0]]
    b = a.copy()
    b.loc[b.index[0], "close"] = 99.0
    path1, checksum1, status1 = raw.write_chunk("600519.SH", "2024-07-31", "2024-07-31", a)
    path2, checksum2, status2 = raw.write_chunk("600519.SH", "2024-07-31", "2024-07-31", b)
    assert path1.exists() and path2.exists() and path1 != path2
    assert checksum1 != checksum2 and status1 == "WRITTEN" and status2 == "DATA_CONFLICT"
    assert raw.manifest.records()[-1]["status"] == "DATA_CONFLICT"


class FakeAPI:
    def __init__(self, connected=True):
        self.connected = connected

    def connect(self, host, port, time_out=3.0):
        return self.connected

    def disconnect(self):
        return None

    def get_security_bars(self, category, market, code, start, count):
        return [{"datetime": "2024-07-31 09:35:00", "open": 10.0, "high": 10.2,
                 "low": 9.9, "close": 10.1, "vol": 1000, "amount": 10000}]


def test_tdx_failover_and_start_pagination():
    created = []

    def factory():
        created.append(1)
        return FakeAPI(connected=len(created) > 1)

    provider = TdxRawHQProvider(servers=(("bad", "127.0.0.1", 7709), ("good", "127.0.0.1", 7709)),
                                api_factory=factory)
    df = provider.fetch("600519.SH", date(2024, 7, 31), date(2024, 7, 31))
    assert len(df) == 1 and provider.failures[0]["status"] == "connect_failed"
    assert provider.capability()["role"] == "SECONDARY/RECENT/GAP_FILL/CROSS_CHECK"
    normalized = normalize_5m_frame(df, "600519.SH", provider.source, provider.timestamp_semantics)
    assert {"symbol", "timestamp", "open", "high", "low", "close", "volume", "amount", "source"}.issubset(normalized.columns)


class PagedAPI(FakeAPI):
    def get_security_bars(self, category, market, code, start, count):
        day = "2024-07-31" if start == 0 else "2024-07-30"
        return [{"datetime": f"{day} 09:35:00", "open": 10.0, "high": 10.2,
                 "low": 9.9, "close": 10.1, "vol": 1000, "amount": 10000}]


def test_tdx_start_offset_paginates_toward_older_data():
    calls = []

    class TracedPagedAPI(PagedAPI):
        def get_security_bars(self, category, market, code, start, count):
            calls.append(start)
            return super().get_security_bars(category, market, code, start, count)

    provider = TdxRawHQProvider(servers=(("good", "127.0.0.1", 7709),),
                                api_factory=TracedPagedAPI, max_pages=3)
    df = provider.fetch("600519.SH", date(2024, 7, 30), date(2024, 7, 31))
    assert len(df) == 2
    assert calls == [0, 800]
