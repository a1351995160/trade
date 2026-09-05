import struct
from chanlun_trader.research.io_safety import read_day_file_range, read_lc5_file_range

DAY = struct.Struct("<IIIIIfI4s")
LC5 = struct.Struct("<HHfffffII")
R = 20250731


def _make_day(path, dates):
    with open(path, "wb") as f:
        for d in dates:
            f.write(DAY.pack(d, 1000, 1000, 1000, 1000, 1.0, 1000, b"\0" * 4))


def _make_lc5(path, pairs):
    def code(d):
        y, m, dd = d // 10000, (d // 100) % 100, d % 100
        return (y - 2004) * 2048 + m * 100 + dd
    def mc(t):
        return (t // 100) * 60 + (t % 100)
    with open(path, "wb") as f:
        for d, t in pairs:
            f.write(LC5.pack(code(d), mc(t), 1.0, 1.0, 1.0, 1.0, 1.0, 10, 0))


def test_lc5_range_reader_no_future_materialization(tmp_path):
    p = tmp_path / "x.lc5"
    _make_lc5(p, [(20250731, 930), (20250731, 935), (20250801, 930), (20250801, 935)])
    df = read_lc5_file_range(str(p), end_date=R)
    assert set(df["date"].unique()) == {20250731}
    assert len(df) == 2


def test_day_range_reader_no_future_materialization(tmp_path):
    p = tmp_path / "x.day"
    _make_day(p, [20250730, 20250731, 20250801, 20250802])
    df = read_day_file_range(str(p), end_date=R)
    assert set(df["date"].unique()) == {20250730, 20250731}
    assert len(df) == 2
