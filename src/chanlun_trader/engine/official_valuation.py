"""单一、明确的**正式估值口径**（绑定应有交易日历与事件语义）。

V3 独立复核（BULL_V3_INDEPENDENT_REVIEW.md 第 7 节）确认的缺陷及本版修复：

  V3-V  `require_every_session` 用"快照里出现过哪些日期"当应有日期集合，
        因此 (a) 中间整日丢失被静默跳过；(b) **末日整日丢失**会缩短 end_date
        并按缩短后的端点重算基准；(c) NaN 权益被接受。

本版确立：
  - **应有日历由调用方显式传入**（原运行 calendar / start / end），
    不再从快照反推；任何应有交易日缺少合格事件 -> **抛错 fail-closed**。
  - **末日缺失必须报错**，不得把 end_date 悄悄缩短。
  - 权益必须为**有限正数**；NaN/inf/非数 -> 抛错。
  - 基准端点必须与账户端点**完全一致**；缺原端点 -> 返回 None（**不按截短端点重算**）。
  - 证据保留 `timestamp` / `event_kind` / `event_sequence` / `calendar_identity`。
  - 旧 date-only 归档**保持原样**，不推测补写时间戳（另见 `legacy_dedup_first_curve`）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

# 正式估值事件：盘后结算（15:30）。日线数据在该时点已完整发布。
OFFICIAL_VALUATION_EVENT = "AFTER_CLOSE"
OFFICIAL_VALUATION_TIME = (15, 30)
# 历史对账口径（仅用于与旧报告比对，不用于正式结论）
LEGACY_RECONCILIATION_EVENT = "DEDUP_FIRST"


class OfficialValuationError(RuntimeError):
    """正式估值口径无法成立（缺应有事件、非有限权益、端点不一致等）。"""


def _finite_positive(value, where: str) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise OfficialValuationError("non-numeric equity at %s: %r" % (where, value))
    if not math.isfinite(v):
        raise OfficialValuationError("non-finite equity at %s: %r" % (where, value))
    if v <= 0:
        raise OfficialValuationError("non-positive equity at %s: %r" % (where, value))
    return v


@dataclass(frozen=True)
class ValuationPoint:
    """一个正式估值点（保留完整事件身份，便于独立复核）。"""

    date: int
    timestamp: str
    event_kind: str
    event_sequence: int          # 该日同事件的原始插入序号
    equity: float

    def to_dict(self) -> dict:
        return {"date": int(self.date), "timestamp": self.timestamp,
                "event_kind": self.event_kind, "event_sequence": int(self.event_sequence),
                "equity": float(self.equity)}


@dataclass(frozen=True)
class OfficialValuation:
    points: Tuple[ValuationPoint, ...]
    event: str
    start_date: int
    end_date: int
    calendar_identity: str = ""

    @property
    def dates(self) -> Tuple[int, ...]:
        return tuple(p.date for p in self.points)

    @property
    def equity(self) -> Tuple[float, ...]:
        return tuple(p.equity for p in self.points)

    def total_return(self, initial_cash: float) -> float:
        if not self.points:
            raise OfficialValuationError("empty official valuation")
        base = _finite_positive(initial_cash, "initial_cash")
        return float(self.points[-1].equity / base - 1.0)

    def max_drawdown(self) -> float:
        if not self.points:
            raise OfficialValuationError("empty official valuation")
        s = pd.Series(self.equity)
        return float((s / s.cummax() - 1.0).min())

    def sharpe(self, periods: int = 252) -> float:
        if len(self.points) < 3:
            return 0.0
        r = pd.Series(self.equity).pct_change().dropna()
        sd = float(r.std())
        return float(r.mean() / sd * (periods ** 0.5)) if sd > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "event": self.event,
            "start_date": int(self.start_date),
            "end_date": int(self.end_date),
            "calendar_identity": self.calendar_identity,
            "n_days": len(self.points),
            "points": [p.to_dict() for p in self.points],
        }


def official_equity_curve(snapshots: Sequence,
                          calendar: Optional[Sequence[int]] = None,
                          start_date: Optional[int] = None,
                          end_date: Optional[int] = None,
                          event: str = OFFICIAL_VALUATION_EVENT,
                          calendar_identity: str = "") -> OfficialValuation:
    """按**应有日历 + 明确事件**抽取正式逐日净值。

    `calendar`（应有交易日，升序）**必填**：正式入口必须接收可信的运行日历。
    `start_date`/`end_date` 指定正式区间的起止。

    **`calendar=None` 会抛 `OfficialValuationError`**（V4-P2-01 修复）：
    从快照反推的"应有日历"无法发现**整日缺失**（含末日缺失），
    会把被缩短的端点当成完整区间。若确实需要无日历对照，
    请显式调用 `diagnostic_curve_without_calendar()`（返回
    `official=False` / `NON_OFFICIAL_DIAGNOSTIC`，**不得**作为正式结论）。

    检查（全部 fail-closed）：
      1. 应有日历内每一天都必须有该事件快照；缺任一天 -> 抛错（含**末日**）。
      2. 权益必须为有限正数。
      3. 正式区间的首/末必须与给定 start/end 一致（未给则取应有日历首末）。
    """
    if event != OFFICIAL_VALUATION_EVENT:
        raise OfficialValuationError("unsupported official event: %s" % event)
    hh, mm = OFFICIAL_VALUATION_TIME
    by_date: Dict[int, List[Tuple[int, str, float]]] = {}
    seq = 0
    for s in snapshots:
        ts = s.timestamp
        if ts.hour != hh or ts.minute != mm:
            continue
        seq += 1
        d = int(ts.strftime("%Y%m%d"))
        by_date.setdefault(d, []).append((seq, ts.isoformat(), float(s.equity)))

    if calendar is None:
        # V4 修复（复核 V4-P2-01）：正式入口**必须**接收可信运行日历。
        # 从快照反推日历无法发现"整日缺失"（含末日缺失），
        # 因此不再返回普通 OfficialValuation，而是**直接拒绝**。
        # 兼容/诊断用途请显式调用 `diagnostic_curve_without_calendar()`。
        raise OfficialValuationError(
            "official valuation requires an explicit run calendar; "
            "snapshot-inferred calendar cannot detect whole-session loss "
            "(use diagnostic_curve_without_calendar() for non-official diagnostics)")
    if True:
        cal_id = calendar_identity or "CALLER_SUPPLIED"
        expected = [int(d) for d in calendar]
        if start_date is not None:
            expected = [d for d in expected if d >= int(start_date)]
        if end_date is not None:
            expected = [d for d in expected if d <= int(end_date)]
        if not expected:
            raise OfficialValuationError("empty expected calendar after start/end filter")

    # 1) 应有日历完整性（含末日）
    missing = [d for d in expected if d not in by_date]
    if missing:
        raise OfficialValuationError(
            "official valuation missing %s event on %d expected session(s): %s%s"
            % (event, len(missing), missing[:5],
               " (INCLUDES END SESSION)" if missing and missing[-1] == expected[-1] else ""))

    # 2) 端点一致性
    if start_date is not None and expected[0] != int(start_date):
        raise OfficialValuationError("official start mismatch: %s != %s"
                                     % (expected[0], start_date))
    if end_date is not None and expected[-1] != int(end_date):
        raise OfficialValuationError("official end mismatch: %s != %s"
                                     % (expected[-1], end_date))

    points: List[ValuationPoint] = []
    for d in expected:
        rows = by_date[d]
        # 同日同事件多条 -> 按**原始插入序号**取最后（结算终态）。
        # 禁止按 equity 大小排序挑选。
        last = max(rows, key=lambda r: r[0])
        eq = _finite_positive(last[2], "date=%d" % d)
        points.append(ValuationPoint(date=d, timestamp=last[1], event_kind=event,
                                     event_sequence=last[0], equity=eq))
    return OfficialValuation(points=tuple(points), event=event,
                             start_date=points[0].date, end_date=points[-1].date,
                             calendar_identity=cal_id)


def diagnostic_curve_without_calendar(snapshots: Sequence,
                                      event: str = OFFICIAL_VALUATION_EVENT) -> dict:
    """**非正式诊断**输出：无独立日历时从快照反推。

    明确标记 `official=False`，**不得**冒充官方完整估值结果：
    整日缺失（含末日缺失）无法被发现，收益率可能被缩短的端点污染。
    仅用于排障/对照，不得用于报告结论。
    """
    hh, mm = OFFICIAL_VALUATION_TIME
    by_date = {}
    for s in snapshots:
        ts = s.timestamp
        if ts.hour != hh or ts.minute != mm:
            continue
        by_date.setdefault(int(ts.strftime("%Y%m%d")), []).append(float(s.equity))
    dates = sorted(by_date)
    return {
        "official": False,
        "status": "NON_OFFICIAL_DIAGNOSTIC",
        "reason": "no independent run calendar supplied; whole-session loss undetectable",
        "event": event,
        "calendar_identity": "INFERRED_FROM_SNAPSHOTS",
        "dates": dates,
        "n_days": len(dates),
        "final_equity": by_date[dates[-1]][-1] if dates else None,
        "warning": "不得作为正式估值结论；正式口径必须传 calendar",
    }


def legacy_dedup_first_curve(snapshots: Sequence) -> OfficialValuation:
    """**历史对账专用**口径（旧 v2_metrics：按日期去重保留首个快照）。

    **不推测补写时间戳**：`timestamp` 保持原样，`event_kind` 标注为
    `DEDUP_FIRST_DATE_ONLY`，`event_sequence` 为原始序号。
    """
    rows = [(int(s.timestamp.strftime("%Y%m%d")), s.timestamp.isoformat(), float(s.equity))
            for s in snapshots]
    df = pd.DataFrame(rows, columns=["date", "ts", "equity"])
    dd = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    points = tuple(ValuationPoint(date=int(r.date), timestamp=str(r.ts),
                                  event_kind=LEGACY_RECONCILIATION_EVENT,
                                  event_sequence=i, equity=float(r.equity))
                   for i, r in enumerate(dd.itertuples()))
    return OfficialValuation(points=points, event=LEGACY_RECONCILIATION_EVENT,
                             start_date=points[0].date, end_date=points[-1].date,
                             calendar_identity="LEGACY_DATE_ONLY")


def benchmark_return_aligned(bench_df, start_date: int, end_date: int) -> Optional[float]:
    """基准收益：必须使用**与账户完全相同的起止端点**。

    端点缺失 -> 返回 `None`（**不按截短端点悄悄重算**）。
    """
    if bench_df is None or bench_df.empty:
        return None
    d = bench_df[(bench_df["date"] >= int(start_date)) & (bench_df["date"] <= int(end_date))]
    d = d.sort_values("date")
    if d.empty:
        return None
    first, last = int(d["date"].iloc[0]), int(d["date"].iloc[-1])
    if first != int(start_date) or last != int(end_date):
        return None
    p0, p1 = float(d["close"].iloc[0]), float(d["close"].iloc[-1])
    if not (math.isfinite(p0) and math.isfinite(p1)) or p0 <= 0:
        return None
    return float(p1 / p0 - 1.0)


def compare_events(snapshots: Sequence, calendar: Sequence[int],
                   start_date: Optional[int] = None,
                   end_date: Optional[int] = None) -> dict:
    """并列输出正式口径与历史对账口径。`calendar` **必填**（正式口径要求）。"""
    official = official_equity_curve(snapshots, calendar=calendar,
                                     start_date=start_date, end_date=end_date)
    legacy = legacy_dedup_first_curve(snapshots)
    return {
        "official_event": official.event,
        "official_start": official.start_date,
        "official_end": official.end_date,
        "official_final_equity": official.equity[-1],
        "official_calendar_identity": official.calendar_identity,
        "legacy_event": legacy.event,
        "legacy_final_equity": legacy.equity[-1],
        "legacy_note": "仅历史对账列，不作为正式结论口径；不推测补写时间戳",
    }
