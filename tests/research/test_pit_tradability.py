import json
from datetime import date, datetime

from chanlun_trader.research.pit_tradability import (
    ALL_STOCK_FIELDS,
    HISTORY_FIELDS,
    PITStateStore,
    build_normalized_state,
    canonical_bytes,
)


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(payload))


def test_normalized_state_preserves_transitions_and_unknowns(tmp_path):
    raw = tmp_path / "raw"
    normalized = tmp_path / "normalized"
    calendar = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    _write(raw / "trade_calendar.json", {"trade_dates": [x.isoformat() for x in calendar]})
    _write(raw / "stock_basic.json", {
        "fetched_at": "2026-08-23T10:00:00+08:00",
        "rows": [
            {"code": "sz.000001", "code_name": "A", "ipoDate": "2020-01-01", "outDate": "", "type": "1", "status": "1"},
            {"code": "sh.600001", "code_name": "B", "ipoDate": "2024-01-03", "outDate": "2024-01-03", "type": "1", "status": "0"},
        ],
    })
    _write(raw / "history" / "symbol=000001_SZ.json", {
        "source": "baostock.query_history_k_data_plus",
        "fields": list(HISTORY_FIELDS),
        "rows": [
            {"date": "2024-01-02", "code": "sz.000001", "tradestatus": "1", "isST": "0"},
            {"date": "2024-01-03", "code": "sz.000001", "tradestatus": "0", "isST": "1"},
            {"date": "2024-01-04", "code": "sz.000001", "tradestatus": "1", "isST": "0"},
        ],
    })
    _write(raw / "history" / "symbol=600001_SH.json", {
        "source": "baostock.query_history_k_data_plus",
        "fields": list(HISTORY_FIELDS),
        "rows": [{"date": "2024-01-03", "code": "sh.600001", "tradestatus": "1", "isST": "0"}],
    })
    for day, rows in {
        "2024-01-02": [{"code": "sz.000001", "tradeStatus": "1", "code_name": "A"}],
        "2024-01-03": [{"code": "sz.000001", "tradeStatus": "1", "code_name": "A"}, {"code": "sh.600001", "tradeStatus": "1", "code_name": "B"}],
        "2024-01-04": [{"code": "sz.000001", "tradeStatus": "1", "code_name": "A"}],
    }.items():
        _write(raw / "all_stock" / f"trade_date={day}.json", {"fields": list(ALL_STOCK_FIELDS), "trade_date": day, "rows": rows})

    manifest = build_normalized_state(raw, normalized, calendar[0], calendar[-1])
    assert manifest["coverage"]["source_conflict_count"] == 1
    assert manifest["coverage"]["st_coverage_ratio"] == 1.0
    assert manifest["coverage"]["suspension_coverage_ratio"] < 1.0

    store = PITStateStore(normalized)
    assert store.get_st_status("000001.SZ", "2024-01-02") == "NORMAL"
    assert store.get_st_status("000001.SZ", "2024-01-03") == "ST"
    assert store.get_trading_status("000001.SZ", "2024-01-03") == "UNKNOWN"
    assert store.get_trading_status("000001.SZ", "2024-01-04") == "TRADING"
    assert store.get_st_status("000001.SZ", "2024-01-03", datetime.fromisoformat("2024-01-03T09:30:00+08:00")) == "UNKNOWN"
    assert store.get_universe_row("sz.000001", "2024-01-02")["universe_member"] is True
    assert "000001.SZ" in store.get_universe_members("2024-01-02")

    snapshots = (normalized / "pit_universe_v2" / "trade_date=2024-01-02.jsonl").read_text(encoding="utf-8")
    assert '"reason_codes": ["NOT_LISTED"]' in snapshots

    repeated = tmp_path / "normalized_repeat"
    build_normalized_state(raw, repeated, calendar[0], calendar[-1])
    first_files = sorted(path.relative_to(normalized) for path in normalized.rglob("*") if path.is_file() and path.name != "manifest.json")
    second_files = sorted(path.relative_to(repeated) for path in repeated.rglob("*") if path.is_file() and path.name != "manifest.json")
    assert first_files == second_files
    assert all((normalized / relative).read_bytes() == (repeated / relative).read_bytes() for relative in first_files)
    first_manifest = json.loads((normalized / "manifest.json").read_text(encoding="utf-8"))
    second_manifest = json.loads((repeated / "manifest.json").read_text(encoding="utf-8"))
    first_manifest.pop("build_timestamp")
    second_manifest.pop("build_timestamp")
    assert first_manifest == second_manifest


def test_current_status_is_not_backfilled_and_date_boundary_is_hard(tmp_path):
    from pytest import raises
    from chanlun_trader.research.pit_tradability import ensure_research_range

    with raises(ValueError):
        ensure_research_range(date(2022, 8, 1), date(2025, 8, 1))
