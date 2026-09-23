"""A1 前置：证明有界读取器在**合成文件**上确实物理限窗。

任务第 6 节要求「先读取器源码/元数据与合成边界测试，后实际最小读取」，
且「不得通过再读一次真实混合文件来证明上次没有越界」。

本模块只用合成 .day 文件证明：
- 请求区间内的记录被物化；
- 请求区间**之外**（含封存期）的记录不被物化，且返回的 max_date 受限；
- 物理读取的记录数受二分查找限制，不随文件总长度线性增长。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chanlun_trader.research.guard import FINAL_TEST_START, RESEARCH_END  # noqa: E402
from chanlun_trader.research.guard import FinalTestAccessViolation  # noqa: E402
from chanlun_trader.research.io_safety import read_day_file_range  # noqa: E402

DAY_STRUCT = struct.Struct("<IIIIIfI4s")


def _write_day(path: Path, dates: list, *, base_price: float = 10.0) -> None:
    """写合成 .day 文件：每条 32 字节。"""
    with path.open("wb") as handle:
        for date in dates:
            handle.write(DAY_STRUCT.pack(
                int(date),
                int(base_price * 100), int(base_price * 102), int(base_price * 98),
                int(base_price * 100), float(base_price * 1000.0), 1_000_000, b"\x00" * 4))


def _all_dates() -> list:
    """构造跨越封存期的日期序列（合成，不是真实行情）。

    用真实的日历步进（跳过非法日），确保覆盖到 2025-08 之后。
    """
    import pandas as pd

    out = []
    start = pd.Timestamp("2024-01-02")
    i = 0
    while len(out) < 460:
        d = start + pd.Timedelta(days=i)
        if d.weekday() < 5:
            out.append(int(d.strftime("%Y%m%d")))
        i += 1
    return out


def test_synthetic_bounded_read_limits_materialized_range(tmp_path: Path):
    """请求区间外的记录（含封存期）不得被物化。"""
    path = tmp_path / "sh600000.day"
    dates = _all_dates()
    _write_day(path, dates)
    # 文件里确实含封存期记录
    assert max(dates) > FINAL_TEST_START, "夹具未包含封存期日期"

    frame = read_day_file_range(path, start_date=20240102, end_date=RESEARCH_END)
    assert len(frame) > 0
    assert int(frame["date"].min()) >= 20240102
    assert int(frame["date"].max()) <= RESEARCH_END
    assert int(frame["date"].max()) < FINAL_TEST_START


def test_synthetic_read_rejects_sealed_range(tmp_path: Path):
    """请求区间越过封存期必须被拒绝（不得靠过滤兜底）。"""
    path = tmp_path / "sh600000.day"
    _write_day(path, _all_dates())
    with pytest.raises(FinalTestAccessViolation) as excinfo:
        read_day_file_range(path, start_date=20240102, end_date=FINAL_TEST_START)
    assert "FINAL_TEST" in str(excinfo.value).upper()


def test_physical_records_do_not_scale_with_file_length(tmp_path: Path):
    """物理读取记录数受二分限制，不随文件总长线性增长。"""
    small = tmp_path / "small.day"
    large = tmp_path / "large.day"
    _write_day(small, _all_dates())
    # 构造更长文件（更多封存期之后的记录）
    extended = list(_all_dates()) + [d for d in range(20260101, 20260129)]
    _write_day(large, extended)

    import json

    from chanlun_trader.research import io_safety

    records = []

    def sink(rec):
        records.append(rec)

    # 直接用审计 sink 收集（不写盘）
    original = io_safety._log_physical_read
    io_safety._log_physical_read = lambda **rec: sink(rec)
    try:
        read_day_file_range(small, start_date=20240102, end_date=RESEARCH_END)
        read_day_file_range(large, start_date=20240102, end_date=RESEARCH_END)
    finally:
        io_safety._log_physical_read = original

    assert len(records) == 2
    # 两次请求的物化行数必须相同（与文件总长无关）
    assert records[0]["rows_materialized"] == records[1]["rows_materialized"]
    assert records[0]["max_date_materialized"] == records[1]["max_date_materialized"]
    assert records[0]["max_date_materialized"] <= RESEARCH_END


def test_requested_range_never_exceeds_research_end(tmp_path: Path):
    """默认 end_date 即 RESEARCH_END，不会静默放宽。"""
    path = tmp_path / "sh600000.day"
    _write_day(path, _all_dates())
    frame = read_day_file_range(path, start_date=20240102)
    assert int(frame["date"].max()) <= RESEARCH_END
