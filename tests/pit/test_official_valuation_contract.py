"""正式估值口径合同回归（语义移植自 V5 已审阅修复）。

覆盖合同 B：
  - 正式入口必须有**独立日历**与固定起止；缺日历即拒绝；
  - 缺中间日 / 缺末日 / 非有限权益 -> 拒绝；
  - 非正式无日历诊断明确分离（不得冒充官方完整收益）；
  - 旧 date-only 归档不推测补写时间戳。

全部使用**合成快照**，不读取真实行情。
"""
import pandas as pd
import pytest

from src.chanlun_trader.engine.ledger import LedgerSnapshot
from src.chanlun_trader.engine.official_valuation import (
    OfficialValuationError, benchmark_return_aligned,
    diagnostic_curve_without_calendar, legacy_dedup_first_curve,
    official_equity_curve)

CAL = [20240801, 20240802, 20240805]


def _snap(d, hh, mm, eq):
    ts = pd.Timestamp("%04d-%02d-%02d %02d:%02d:00"
                      % (d // 10000, (d // 100) % 100, d % 100, hh, mm),
                      tz="Asia/Shanghai")
    return LedgerSnapshot(timestamp=ts, cash=eq, market_value=0.0, equity=eq,
                          realized_pnl=0.0, unrealized_pnl=0.0, positions=0, turnover=0.0)


def _full():
    out = []
    for d, eo, ec in ((20240801, 10000.0, 10100.0), (20240802, 10100.0, 10150.0),
                      (20240805, 10150.0, 10200.0)):
        out += [_snap(d, 9, 30, eo), _snap(d, 15, 0, ec - 5), _snap(d, 15, 30, ec)]
    return out


def test_official_requires_explicit_calendar():
    """正式入口缺日历必须拒绝（不得从快照反推并当官方结果）。"""
    with pytest.raises(OfficialValuationError):
        official_equity_curve(_full())


def test_official_picks_after_close_and_keeps_event_identity():
    ov = official_equity_curve(_full(), calendar=CAL)
    assert list(ov.equity) == [10100.0, 10150.0, 10200.0]
    assert all(p.event_kind == "AFTER_CLOSE" and p.timestamp and p.event_sequence > 0
               for p in ov.points)
    assert ov.calendar_identity == "CALLER_SUPPLIED"


def test_duplicate_same_event_takes_last_inserted_not_max():
    """同日同事件多条 -> 按插入顺序取末条（禁止按数值挑大）。"""
    s = [_snap(20240801, 15, 30, 10100.0), _snap(20240801, 15, 30, 10050.0)]
    assert list(official_equity_curve(s, calendar=[20240801]).equity) == [10050.0]


def test_missing_middle_session_rejected():
    s = [x for x in _full() if x.timestamp.strftime("%Y%m%d") != "20240802"]
    with pytest.raises(OfficialValuationError):
        official_equity_curve(s, calendar=CAL)


def test_missing_last_session_rejected_without_end_truncation():
    s = [x for x in _full() if x.timestamp.strftime("%Y%m%d") != "20240805"]
    with pytest.raises(OfficialValuationError) as exc:
        official_equity_curve(s, calendar=CAL, start_date=20240801, end_date=20240805)
    assert "INCLUDES END SESSION" in str(exc.value)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_non_finite_or_invalid_equity_rejected(bad):
    s = [_snap(20240801, 15, 30, 10000.0), _snap(20240802, 15, 30, bad)]
    with pytest.raises(OfficialValuationError):
        official_equity_curve(s, calendar=[20240801, 20240802])


def test_empty_calendar_rejected():
    with pytest.raises(OfficialValuationError):
        official_equity_curve([], calendar=[])


def test_diagnostic_is_explicitly_non_official():
    """无日历兼容路径必须标为非正式诊断，不得冒充官方完整结果。"""
    diag = diagnostic_curve_without_calendar(_full())
    assert diag["official"] is False
    assert diag["status"] == "NON_OFFICIAL_DIAGNOSTIC"
    assert diag["calendar_identity"] == "INFERRED_FROM_SNAPSHOTS"
    # 它无法发现整日缺失——这是它不能当正式口径的原因
    s_mid = [x for x in _full() if x.timestamp.strftime("%Y%m%d") != "20240802"]
    assert diagnostic_curve_without_calendar(s_mid)["n_days"] == 2


def test_benchmark_endpoints_must_match_exactly():
    """基准缺原端点 -> None（不得按截短端点重算）。"""
    bench = pd.DataFrame({"date": [20240801, 20240802], "close": [100.0, 101.0]})
    assert benchmark_return_aligned(bench, 20240801, 20240802) is not None
    assert benchmark_return_aligned(bench, 20240801, 20240805) is None


def test_legacy_archive_not_backfilled_with_inferred_timestamps():
    lg = legacy_dedup_first_curve(_full())
    assert lg.event == "DEDUP_FIRST"
    assert lg.calendar_identity == "LEGACY_DATE_ONLY"
    assert all(p.event_kind == "DEDUP_FIRST" for p in lg.points)