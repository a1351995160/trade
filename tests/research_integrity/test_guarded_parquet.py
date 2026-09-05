import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
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


def test_final_test_lock():
    from chanlun_trader.research.guard import ResearchDataAccessGuard
    g = ResearchDataAccessGuard()
    for d in [20250801, 20260818, 20300101]:
        try:
            g.check_date(d)
            raise AssertionError("expected violation")
        except FinalTestAccessViolation:
            pass
