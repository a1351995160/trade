"""限定训练数据读入的物理字节范围。"""
import struct

import pytest

from scripts.prepare_bounded_train_snapshot import day_window


def record(day, close):
    return struct.pack("<IIIIIfI4s", day, close, close, close, close, 10.0, 100, b"\0"*4)


def test_date_seek_does_not_materialize_validation_or_final_prices(tmp_path):
    path = tmp_path / "sh600000.day"
    path.write_bytes(record(20220729, 999) + record(20220801, 1000) + record(20240731, 1100)
                     + record(20240801, 999999) + record(20250801, 888888))
    before = path.read_bytes()
    frame, audit = day_window(path, 20220801, 20240731)
    assert frame.date.tolist() == [20220801, 20240731]
    assert frame.close.tolist() == [10.0, 11.0]
    assert audit["price_byte_range_half_open"] == [32, 96]
    assert audit["price_bytes_read"] == 64
    assert audit["full_file_hash"] is None
    assert path.read_bytes() == before


@pytest.mark.parametrize("start,end", [(20220801, 20240801), (20220729, 20240731)])
def test_bad_window_rejected_before_file_open(tmp_path, start, end):
    with pytest.raises(ValueError, match="TRAIN_WINDOW_REQUIRED"):
        day_window(tmp_path / "absent.day", start, end)


def test_corrupt_size_and_duplicate_date_rejected(tmp_path):
    path = tmp_path / "sh600000.day"
    path.write_bytes(b"x")
    with pytest.raises(ValueError, match="RECORD_SIZE"):
        day_window(path, 20220801, 20240731)
    path.write_bytes(record(20220801, 1000)*2)
    with pytest.raises(ValueError, match="DATE_INDEX"):
        day_window(path, 20220801, 20240731)
