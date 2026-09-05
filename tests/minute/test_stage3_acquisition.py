from datetime import date
import json

import pandas as pd

from scripts.audit_stage3_5m import build_coverage, classify_session
from scripts.stage3_5m_acquisition import (
    TRAIN_END,
    TRAIN_START,
    FINAL_TEST_EXPOSURE,
    ProgressJournal,
    active_bounds,
    build_stage3_universe,
    write_failure_ledger,
    write_immutable_json,
)
from chanlun_trader.data.minute.base import BAR_TIMESTAMP_SEMANTICS, date_chunks
from chanlun_trader.data.minute.downloader import Historical5mDownloader
from chanlun_trader.data.minute.manifest import ImmutableRawStore
from chanlun_trader.data.minute.normalizer import normalize_5m_frame


def _rows():
    return pd.DataFrame({
        "timestamp": pd.DatetimeIndex(["2024-07-31 09:35", "2024-07-31 09:40"], tz="Asia/Shanghai"),
        "open": [10.0, 10.1], "high": [10.2, 10.3], "low": [9.9, 10.0], "close": [10.1, 10.2],
        "volume": [1000, 1200], "amount": [10000, 12000],
    })


def _universe_rows():
    return [
        {"code": "sh.600000", "type": "1", "ipoDate": "1999-11-10", "outDate": "", "status": "1"},
        {"code": "sz.300001", "type": "1", "ipoDate": "2023-01-01", "outDate": "", "status": "1"},
        {"code": "sh.688001", "type": "1", "ipoDate": "2020-01-01", "outDate": "2023-12-29", "status": "0"},
        {"code": "sh.000001", "type": "2", "ipoDate": "1991-01-01", "outDate": "", "status": "1"},
        {"code": "sz.300002", "type": "1", "ipoDate": "2025-01-01", "outDate": "", "status": "1"},
    ]


def test_stage3_universe_filters_to_a_share_type_one():
    members = build_stage3_universe(_universe_rows(), [20220801, 20230103, 20231229, 20240731])
    assert {member["symbol"] for member in members} == {"600000.SH", "300001.SZ", "688001.SH"}


def test_stage3_universe_includes_delisted_symbol_during_active_window():
    members = build_stage3_universe(_universe_rows(), [20220801, 20230103, 20231229, 20240731])
    member = next(x for x in members if x["symbol"] == "688001.SH")
    assert member["delist_date"] == "2023-12-29"
    assert member["active_end_in_train"] == "2023-12-29"


def test_stage3_universe_excludes_not_yet_listed_symbol():
    members = build_stage3_universe(_universe_rows(), [20220801, 20230103, 20231229, 20240731])
    assert "300002.SZ" not in {member["symbol"] for member in members}


def test_stage3_universe_has_board_labels():
    members = build_stage3_universe(_universe_rows(), [20220801, 20230103, 20231229, 20240731])
    assert {member["board"] for member in members} == {"SH_MAIN", "GEM", "STAR"}


def test_stage3_universe_expected_sessions_are_pit_bounded():
    members = build_stage3_universe(_universe_rows(), [20220801, 20230103, 20231229, 20240731])
    assert next(x for x in members if x["symbol"] == "300001.SZ")["expected_active_sessions"] == 3


def test_active_bounds_stop_at_delist_date():
    member = {"list_date": "1990-01-01", "delist_date": "2024-04-26"}
    assert active_bounds(member) == (TRAIN_START, date(2024, 4, 26))


def test_immutable_json_is_idempotent(tmp_path):
    path = tmp_path / "manifest.json"
    first = write_immutable_json(path, {"version": "V1", "symbols": ["600000.SH"]})
    second = write_immutable_json(path, {"version": "V1", "symbols": ["600000.SH"]})
    assert first[2] == "WRITTEN" and second[2] == "UNCHANGED"
    assert first[1] == second[1]


def test_immutable_json_preserves_conflict(tmp_path):
    path = tmp_path / "manifest.json"
    write_immutable_json(path, {"version": "V1", "symbols": ["600000.SH"]})
    target, _, status = write_immutable_json(path, {"version": "V1", "symbols": ["600001.SH"]})
    assert status == "DATA_CONFLICT" and target != path and path.read_text()


def test_progress_journal_contains_resume_fields(tmp_path):
    journal = ProgressJournal(tmp_path / "progress.jsonl", 10, 2, {"600000.SH": 0}, {"600000.SH"})
    journal.set_batch(1)
    journal.set_symbol("600001.SH")
    journal({"event": "chunk_done", "symbol": "600001.SH", "current_chunk": 1, "row_count": 48})
    record = json.loads((tmp_path / "progress.jsonl").read_text().splitlines()[-1])
    assert {"total_symbols", "completed_symbols", "remaining_symbols", "current_batch", "current_chunk", "rows_downloaded", "estimated_remaining_seconds"}.issubset(record)


def test_progress_journal_starts_with_resumed_symbols(tmp_path):
    journal = ProgressJournal(tmp_path / "progress.jsonl", 2, 1, {"600000.SH": 0, "600001.SH": 1}, {"600000.SH"})
    journal._write("run_start")
    record = json.loads((tmp_path / "progress.jsonl").read_text().splitlines()[-1])
    assert record["completed_symbols"] == 1 and record["remaining_symbols"] == 1


def test_downloader_uses_seven_train_chunks():
    assert len(list(date_chunks(TRAIN_START, TRAIN_END, 120))) == 7


def test_downloader_retry_uses_bounded_exponential_backoff(tmp_path, monkeypatch):
    import chanlun_trader.data.minute.downloader as downloader_module

    waits = []
    monkeypatch.setattr(downloader_module, "sleep", lambda seconds: waits.append(seconds))

    class Provider:
        source = "fake"
        timestamp_semantics = BAR_TIMESTAMP_SEMANTICS

        def __init__(self):
            self.calls = 0
            self.reset_count = 0

        def reset_session(self):
            self.reset_count += 1

        def fetch(self, symbol, start_date, end_date):
            self.calls += 1
            if self.calls < 3:
                raise ConnectionError("temporary")
            return _rows()

    provider = Provider()
    result = Historical5mDownloader(provider, ImmutableRawStore(tmp_path / "raw"),
                                    chunk_days=120, retries=2, retry_delay=2, retry_max_delay=3).download(
                                        "600000.SH", date(2024, 7, 31), date(2024, 7, 31))
    assert result.status == "COMPLETE" and waits == [2, 3] and provider.reset_count == 2


def test_downloader_records_empty_chunk_as_resume_checkpoint(tmp_path):
    class EmptyProvider:
        source = "fake"
        timestamp_semantics = BAR_TIMESTAMP_SEMANTICS

        def fetch(self, symbol, start_date, end_date):
            return pd.DataFrame()

    result = Historical5mDownloader(EmptyProvider(), ImmutableRawStore(tmp_path / "raw"),
                                    chunk_days=120, retries=0).download(
                                        "600000.SH", date(2024, 7, 31), date(2024, 7, 31), allow_empty=True)
    assert result.status == "COMPLETE" and result.chunks[0]["status"] == "EMPTY"


def test_failure_ledger_writes_failed_chunks(tmp_path):
    path = tmp_path / "failures.csv"
    write_failure_ledger(path, [{"symbol": "600000.SH", "requested_start": "2022-08-01", "requested_end": "2022-11-28", "status": "FAILED", "retry_count": 3, "source": "baostock", "error": "timeout"}])
    assert "600000.SH" in path.read_text(encoding="utf-8")


def test_raw_store_is_immutable_for_stage3(tmp_path):
    raw = ImmutableRawStore(tmp_path / "raw")
    frame = _rows()
    path1, checksum1, status1 = raw.write_chunk("600000.SH", "2024-07-31", "2024-07-31", frame)
    changed = frame.copy()
    changed.loc[0, "close"] = 99
    path2, checksum2, status2 = raw.write_chunk("600000.SH", "2024-07-31", "2024-07-31", changed)
    assert path1 != path2 and checksum1 != checksum2 and status1 == "WRITTEN" and status2 == "DATA_CONFLICT"


def test_provider_revision_is_visible_in_manifest(tmp_path):
    raw = ImmutableRawStore(tmp_path / "raw")
    raw.write_chunk("600000.SH", "2024-07-31", "2024-07-31", _rows())
    changed = _rows()
    changed.loc[0, "close"] = 99
    raw.write_chunk("600000.SH", "2024-07-31", "2024-07-31", changed)
    assert raw.manifest.records()[-1]["status"] == "DATA_CONFLICT"


def test_complete_session_classifier():
    assert classify_session("600000.SH", 20240731, 48, {"tradestatus": "1", "volume": "10"}) == "COMPLETE_48"


def test_suspension_classifier_requires_dated_evidence():
    assert classify_session("600000.SH", 20240731, 0, {"tradestatus": "0", "volume": "0"}) == "EXPECTED_SUSPENSION"


def test_no_data_does_not_imply_suspension():
    assert classify_session("600000.SH", 20240731, 0, None) == "UNKNOWN"


def test_cross_source_conflict_is_preserved():
    assert classify_session("000895.SZ", 20240606, 46, {"tradestatus": "1", "volume": "10"}) == "CROSS_SOURCE_CONFLICT"


def test_partial_positive_volume_is_provider_gap():
    assert classify_session("600000.SH", 20240731, 47, {"tradestatus": "1", "volume": "10"}) == "PROVIDER_DATA_GAP"


def test_train_date_boundary_is_hard_limited():
    assert TRAIN_START == date(2022, 8, 1) and TRAIN_END == date(2024, 7, 31)


def test_normalized_schema_keeps_bar_end_semantics():
    frame = normalize_5m_frame(_rows(), "600000.SH", "baostock")
    assert frame["bar_time"].tolist() == ["09:35", "09:40"]
    assert set(["symbol", "timestamp", "trade_date", "bar_time", "open", "high", "low", "close", "volume", "amount", "source"]).issubset(frame.columns)


def test_independent_coverage_recomputation_counts_anomaly():
    member = {"symbol": "000895.SZ", "board": "SZ_MAIN", "list_date": "1990-01-01", "delist_date": None}
    counts = {("000895.SZ", 20240606): 46, ("000895.SZ", 20240607): 48}
    coverage, anomalies = build_coverage([member], [20240606, 20240607], counts, {
        ("000895.SZ", 20240606): {"tradestatus": "1", "volume": "100"},
    })
    assert coverage["classification_counts"]["CROSS_SOURCE_CONFLICT"] == 1
    assert anomalies[0]["classification"] == "CROSS_SOURCE_CONFLICT"


def test_dataset_version_freeze_marker_is_v1(tmp_path):
    path = tmp_path / "dataset.json"
    target, _, status = write_immutable_json(path, {"dataset_version": "TRAIN_5M_DATASET_V1", "freeze_status": "FROZEN"})
    assert target.exists() and status == "WRITTEN"
    assert json.loads(target.read_text())["dataset_version"] == "TRAIN_5M_DATASET_V1"


def test_final_test_access_policy_is_zero_for_stage3_scope():
    assert FINAL_TEST_EXPOSURE == {
        "FINAL_TEST_NEW_PHYSICAL_ACCESS": 0,
        "FINAL_TEST_NEW_ANALYTICAL_EXPOSURE": 0,
        "FINAL_TEST_NEW_DECISION_EXPOSURE": 0,
    }
