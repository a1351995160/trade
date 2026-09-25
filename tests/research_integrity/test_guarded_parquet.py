import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from chanlun_trader.research.io_safety import GuardedResearchReader
from chanlun_trader.research.guard import FinalTestAccessViolation


def test_parquet_predicate_pushdown_guard(tmp_path):
    df = pd.DataFrame({
        "date": [20250101, 20250731, 20250801, 20250830],
        "x": [1.0, 2.0, 3.0, 4.0],
    })
    p = tmp_path / "t.parquet"
    pq.write_table(pa.Table.from_pandas(df), p)
    reader = GuardedResearchReader()
    out = reader.read_parquet(str(p), start_date=20250101, end_date=20250731)
    assert out["date"].max() <= 20250731
    assert len(out) == 2
    try:
        reader.read_parquet(str(p), start_date=20250101, end_date=20250801)
        raise AssertionError("expected FinalTestAccessViolation")
    except FinalTestAccessViolation:
        pass


def test_iso_date_parquet_is_bounded_before_materialization(tmp_path):
    path = tmp_path / "historical_states.parquet"
    pq.write_table(pa.Table.from_pandas(pd.DataFrame({
        "trade_date": ["2024-02-01", "2024-02-02", "2025-08-01"],
        "state": ["NORMAL", "ST", "NORMAL"],
    })), path)
    audit = []
    reader = GuardedResearchReader(audit_sink=audit.append)
    frame = reader.read_parquet(
        path, columns=["trade_date", "state"], date_column="trade_date",
        start_date=20240201, end_date=20240202, date_format="iso",
    )
    assert frame["trade_date"].tolist() == ["2024-02-01", "2024-02-02"]
    assert audit[0]["rows_materialized"] == 2
    assert audit[0]["max_date_materialized"] == 20240202
    try:
        reader.read_parquet(path, date_column="trade_date", start_date=20240201,
                            end_date=20250801, date_format="iso")
        raise AssertionError("expected FinalTestAccessViolation")
    except FinalTestAccessViolation:
        pass


def test_iso_date_reader_rejects_impossible_calendar_date(tmp_path):
    path = tmp_path / "invalid_date.parquet"
    pq.write_table(pa.Table.from_pandas(pd.DataFrame({
        "trade_date": ["2024-02-30"], "state": ["NORMAL"],
    })), path)
    reader = GuardedResearchReader(audit_sink=lambda _: None)
    with pytest.raises(ValueError, match="PARQUET_ISO_DATE_INVALID"):
        reader.read_parquet(path, date_column="trade_date",
                            start_date=20240201, end_date=20240331,
                            date_format="iso")


def test_final_test_lock():
    from chanlun_trader.research.guard import ResearchDataAccessGuard
    g = ResearchDataAccessGuard()
    for d in [20250801, 20260818, 20300101]:
        try:
            g.check_date(d)
            raise AssertionError("expected violation")
        except FinalTestAccessViolation:
            pass
