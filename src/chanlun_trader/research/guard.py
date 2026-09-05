"""Research data access guard: hard lock on FINAL TEST data.

FINAL_TEST_START = 2025-08-01. 未获用户明确授权前，研究代码禁止读取
>= 2025-08-01 的任何数据（Daily / 5m / 龙虎榜 / 涨停 / 资金流 / 财务 / 行情等）。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterable

FINAL_TEST_START = 2025_08_01
RESEARCH_END = 2025_07_31


class FinalTestAccessViolation(RuntimeError):
    """研究代码试图访问 FINAL TEST（2025-08-01+）数据。"""

    def __init__(self, msg: str = ""):
        super().__init__(
            msg
            or f"FINAL_TEST_ACCESS_VIOLATION: research data access >= {FINAL_TEST_START} is sealed."
        )


class UnsafeLegacyQfqAccessError(RuntimeError):
    """正式研究上下文禁止使用 full-sample legacy V1 qfq。"""


_RESEARCH_CONTEXT: ContextVar[bool] = ContextVar("research_context", default=False)


@contextmanager
def research_context():
    token = _RESEARCH_CONTEXT.set(True)
    try:
        yield
    finally:
        _RESEARCH_CONTEXT.reset(token)


def is_research_context() -> bool:
    return _RESEARCH_CONTEXT.get()


@dataclass(frozen=True)
class ResearchDataAccessGuardConfig:
    """Guard 配置。final_test_start/research_end 默认使用项目硬锁。"""

    final_test_start: int = FINAL_TEST_START
    research_end: int = RESEARCH_END
    allow_override: bool = False  # 只有用户明确授权 FINAL TEST 时才可设为 True


class ResearchDataAccessGuard:
    """所有研究数据访问的强制闸门。

    规则：
    - date < FINAL_TEST_START 且 <= research_end：放行。
    - date >= FINAL_TEST_START：raise FinalTestAccessViolation。
    - date > research_end（但 < final_test_start 不可能，两者相等边界）: 放行到 research_end。
    """

    def __init__(self, config: ResearchDataAccessGuardConfig | None = None):
        self.config = config or ResearchDataAccessGuardConfig()

    # ---- date guards ----
    def check_date(self, date: int, label: str = "") -> None:
        if date >= self.config.final_test_start:
            raise FinalTestAccessViolation(
                f"date={date} {label} >= FINAL_TEST_START={self.config.final_test_start}"
            )
        if date > self.config.research_end:
            raise FinalTestAccessViolation(
                f"date={date} {label} > RESEARCH_END={self.config.research_end}"
            )

    def check_range(self, start_date: int, end_date: int, label: str = "") -> None:
        self.check_date(start_date, label + " start")
        self.check_date(end_date, label + " end")

    # ---- dataframe guard ----
    def check_frame(self, df: Any, date_column: str = "date") -> None:
        """检查 DataFrame 的日期列没有 FINAL TEST 数据。"""
        if df is None:
            return
        if date_column not in df.columns:
            return
        dates = df[date_column]
        bad = dates[dates >= self.config.final_test_start]
        if len(bad):
            raise FinalTestAccessViolation(
                f"DataFrame contains {len(bad)} rows with {date_column} >= FINAL_TEST_START "
                f"(max={int(bad.max())})"
            )

    # ---- iterable guard ----
    def check_int_iterable(self, dates: Iterable[int], label: str = "") -> None:
        for d in dates:
            self.check_date(int(d), label)

    @property
    def research_end(self) -> int:
        return self.config.research_end

    @property
    def final_test_start(self) -> int:
        return self.config.final_test_start
