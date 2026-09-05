import struct
import tempfile
from pathlib import Path
import pandas as pd
from chanlun_trader.research.io_safety import read_day_file_range, read_lc5_file_range

# local constants for temp fixtures
DAY = struct.Struct("<IIIIIfI4s")
LC5 = struct.Struct("<HHfffffII")
RESEARCH_END = 20250731


def _make_day(path, dates):
    with open(path, "wb") as f:
        for d in dates:
            f.write(DAY.pack(d, 1000, 1000, 1000, 1000, 1.0, 1000, b"\0" * 4))


def _make_lc5(path, date_minute):
    def code(d):
        y, m, dd = d // 10000, (d // 100) % 100, d % 100
        return (y - 2004) * 2048 + m * 100 + dd
    def mcode(t):
        hh, mm = t // 100, t % 100
        return hh * 60 + mm
    with open(path, "wb") as f:
        for d, t in date_minute:
            f.write(LC5.pack(code(d), mcode(t), 1.0, 1.0, 1.0, 1.0, 1.0, 10, 0))


from chanlun_trader.research.io_safety import GuardedResearchReader


def test_physical_future_data_not_read_parquet_categories():
    reader = GuardedResearchReader()
    # factor / label / event / market tables: all must materialize zero rows after RESEARCH_END
    checks = [
        ("data/research/robustification_results/a4_feature_train.parquet", "date"),
        ("data/research/tradable_returns.parquet", "timestamp"),
        ("data/research/event_store/E_LIMITUP_v1.parquet", "event_time"),
        ("data/tdx/clean/market_sentiment.parquet", "date"),
    ]
    for path, col in checks:
        if not Path(path).exists():
            continue
        df = reader.read_parquet(path, date_column=col, start_date=0, end_date=20250731)
        assert len(df) > 0
        assert df[col].max() <= 20250731


def test_physical_future_data_not_read(tmp_path):
    # mixed safe + future day file
    day = tmp_path / "test.day"
    _make_day(day, [20250730, 20250731, 20250801, 20250802])
    df = read_day_file_range(str(day), start_date=20250730, end_date=RESEARCH_END)
    assert df["date"].max() <= RESEARCH_END
    assert len(df) == 2

    # mixed safe + future lc5 file
    lc5 = tmp_path / "test.lc5"
    _make_lc5(lc5, [(20250730, 935), (20250731, 935), (20250801, 935), (20250802, 935)])
    df2 = read_lc5_file_range(str(lc5), start_date=20250730, end_date=RESEARCH_END)
    assert df2["date"].max() <= RESEARCH_END
    assert len(df2) == 2
