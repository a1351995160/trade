"""PIT 历史资格表 —— 三态可用时间 + 双轴 + 逐日覆盖 + 有效期 + 冲突归并。

V3 独立复核（BULL_V3_INDEPENDENT_REVIEW.md 第 4、5 节）确认的缺陷及本版修复：

  V3-T  空串同时表示"已知/未知/不适用"三义，导致
        (a) NEXT_SESSION_OPEN 末日 None 被存成空串后变成"无限制"；
        (b) 显式更晚 available_at 被另一条轴覆盖。
        -> 引入三态 `AvailabilityKind`：KNOWN / UNKNOWN / NOT_APPLICABLE；
           **空串严格只表示"该轴未声明"**；`UNKNOWN` 用显式哨兵；
           门控取**所有已声明约束中最晚者**（显式更晚必然生效）；
           任一声明为 UNKNOWN -> 门控 UNKNOWN（fail-closed）。
           时间一律用**时区感知**对象比较，非法/无法解析 -> UNKNOWN。

  V3-C  valid_to 与日度冲突未穿过执行层。
        -> 日度模式按 (symbol, date) **归并**：相同证据去重；
           **矛盾状态 -> CONFLICT 并保留全部来源**，不凭首行/末行决定。
           `valid_to` 随记录导出，供 SecurityMaster 保留并检查。
"""
from __future__ import annotations

import bisect
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import pandas as pd

ST_NORMAL = "NORMAL"
ST_SPECIAL = "ST"
ST_UNKNOWN = "UNKNOWN"
ST_CONFLICT = "CONFLICT"

# 只有 NORMAL 才是"确认非风险警示"，因此只有 NORMAL 合格。
ELIGIBLE_STATES = (ST_NORMAL,)

# ---- 可用时间三态 ----------------------------------------------------------
# 空串严格表示"该轴未声明约束"（NOT_APPLICABLE），**不再**兼表"未知"。
AVAIL_NOT_DECLARED = ""
# 显式哨兵：约束存在，但可用时间不可知（如 NEXT_SESSION_OPEN 在日历末端无下一日）。
AVAIL_UNKNOWN = "UNKNOWN"


class AvailabilityKind:
    """可用时间三态。禁止用一个空值同时表示三种含义。"""

    KNOWN = "KNOWN"                     # 已知具体时刻
    UNKNOWN = "UNKNOWN"                 # 约束存在但时间不可知 -> fail-closed
    NOT_APPLICABLE = "NOT_APPLICABLE"   # 该轴未声明约束


# 来源合同：日度观测 vs 事件持续
class CoverageMode:
    DAILY_OBSERVATION = "DAILY_OBSERVATION"   # 每交易日必须有观测行，否则 UNKNOWN
    EVENT_PERSISTENT = "EVENT_PERSISTENT"     # 事件持续到下次变更（须显式声明，可带 valid_to）


# 知识轴规则（由**调用方显式传入**，不由本模块擅自默认成更早值）
DEFAULT_KNOWLEDGE_RULE = "SAME_DAY_OPEN"
NEXT_SESSION_KNOWLEDGE_RULE = "NEXT_SESSION_OPEN"

# 日度观测状态编码（紧凑存储）
_CODE = {ST_NORMAL: 0, ST_SPECIAL: 1, ST_UNKNOWN: 2}
_DECODE = {0: ST_NORMAL, 1: ST_SPECIAL, 2: ST_UNKNOWN}
# 冲突编码
_CODE_CONFLICT = 3
_DECODE[3] = ST_CONFLICT

_TZ = "Asia/Shanghai"


def _parse_ts(value) -> Optional[pd.Timestamp]:
    """把时间输入解析为**时区感知** Timestamp；无法解析返回 None。"""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        ts = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            ts = pd.Timestamp(text)
        except (ValueError, TypeError):
            return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        try:
            return ts.tz_localize(_TZ)
        except Exception:
            return None
    try:
        return ts.tz_convert(_TZ)
    except Exception:
        return None


def parse_availability(value) -> Tuple[str, Optional[pd.Timestamp]]:
    """把可用时间输入解析为 (kind, timestamp)。

    空串/None        -> NOT_APPLICABLE（该轴未声明）
    哨兵 UNKNOWN     -> UNKNOWN（约束存在但不可知）
    合法时间字符串   -> KNOWN（时区感知；跨时区等价写法视为同一时刻）
    非法时间         -> UNKNOWN（fail-closed，不默认"过去已知"）
    """
    if value is None:
        return AvailabilityKind.NOT_APPLICABLE, None
    text = str(value).strip()
    if text == "":
        return AvailabilityKind.NOT_APPLICABLE, None
    if text.upper() == AVAIL_UNKNOWN:
        return AvailabilityKind.UNKNOWN, None
    ts = _parse_ts(text)
    if ts is None:
        return AvailabilityKind.UNKNOWN, None
    return AvailabilityKind.KNOWN, ts


def resolve_gate(*values) -> Tuple[str, Optional[pd.Timestamp]]:
    """把多个轴的可用时间约束合成为**单一门控**。

    规则（fail-closed）：
      - 任一约束为 UNKNOWN -> 门控 UNKNOWN（不得放行）
      - 否则取所有 KNOWN 中**最晚**者（显式更晚约束必然生效）
      - 全为 NOT_APPLICABLE -> NOT_APPLICABLE（无约束）
    """
    known: List[pd.Timestamp] = []
    for v in values:
        kind, ts = parse_availability(v)
        if kind == AvailabilityKind.UNKNOWN:
            return AvailabilityKind.UNKNOWN, None
        if kind == AvailabilityKind.KNOWN and ts is not None:
            known.append(ts)
    if not known:
        return AvailabilityKind.NOT_APPLICABLE, None
    return AvailabilityKind.KNOWN, max(known)


def gate_allows(gate_kind: str, gate_ts: Optional[pd.Timestamp],
                as_of) -> bool:
    """给定知识时刻 as_of，门控是否已放行。"""
    if gate_kind == AvailabilityKind.UNKNOWN:
        return False
    if gate_kind == AvailabilityKind.NOT_APPLICABLE:
        return True
    if gate_ts is None:
        return False
    ref = _parse_ts(as_of)
    if ref is None:
        return False
    return gate_ts <= ref


def normalize_symbol(code: str, market: Optional[int] = None) -> str:
    """把 6 位代码（+可选 market）规范成 000506.SZ / 600900.SH。"""
    code = str(code).strip()
    if "." in code:
        left, right = code.split(".", 1)
        if right.upper() in ("SH", "SZ"):
            return "%s.%s" % (left.zfill(6), right.upper())
    code = code.zfill(6)
    if market is not None:
        return "%s.%s" % (code, "SH" if int(market) == 1 else "SZ")
    return "%s.%s" % (code, "SH" if code.startswith(("5", "6", "9")) else "SZ")


def _iso(day: int, hh: int, mm: int) -> str:
    return "%04d-%02d-%02dT%02d:%02d:00+08:00" % (
        day // 10000, (day // 100) % 100, day % 100, hh, mm)


def next_session_available_at(effective_date: int,
                              calendar: Sequence[int]) -> Optional[str]:
    """NEXT_SESSION_OPEN 规则下的可用时间。

    找到更晚交易日 -> 该日 09:30 的 ISO 串；
    **找不到 -> 返回 None**（调用方必须存为 AVAIL_UNKNOWN，不得当"无限制"）。
    """
    for d in calendar:
        if int(d) > int(effective_date):
            return _iso(int(d), 9, 30)
    return None


@dataclass(frozen=True)
class EligibilityRecord:
    """一条 PIT 资格记录（双轴 + 有效期 + 覆盖合同 + 冲突来源）。"""

    symbol: str
    effective_date: int
    st_status: str
    source: str
    available_at: str = AVAIL_NOT_DECLARED
    conflict_sources: Tuple[str, ...] = ()
    valid_to: Optional[int] = None
    coverage_mode: str = CoverageMode.EVENT_PERSISTENT
    effective_available_at: str = AVAIL_NOT_DECLARED
    researcher_available_at: str = AVAIL_NOT_DECLARED

    def gate(self) -> Tuple[str, Optional[pd.Timestamp]]:
        """该记录的合成门控：取所有已声明约束中最晚者，任一 UNKNOWN 即 UNKNOWN。"""
        return resolve_gate(self.effective_available_at,
                            self.researcher_available_at,
                            self.available_at)

    def to_dict(self) -> dict:
        kind, ts = self.gate()
        return {
            "symbol": self.symbol,
            "effective_date": int(self.effective_date),
            "st_status": self.st_status,
            "source": self.source,
            "available_at": self.available_at,
            "effective_available_at": self.effective_available_at,
            "researcher_available_at": self.researcher_available_at,
            "gate_kind": kind,
            "gate_at": None if ts is None else ts.isoformat(),
            "conflict_sources": list(self.conflict_sources),
            "valid_to": None if self.valid_to is None else int(self.valid_to),
            "coverage_mode": self.coverage_mode,
        }


class HistoricalEligibilityTable:
    """PIT 资格表：双轴可用时间、逐日覆盖、有效期、冲突归并。"""

    def __init__(self, coverage_mode: str = CoverageMode.DAILY_OBSERVATION) -> None:
        self.coverage_mode = coverage_mode
        self._rows: Dict[str, List[EligibilityRecord]] = {}
        # 日度存储：symbol -> {date: {"statuses": {..}, "sources": {..}, "eff": str, "res": str}}
        self._daily: Dict[str, Dict[int, dict]] = {}
        self._sources: Set[str] = set()
        self._calendar: List[int] = []

    # ---------- 事件模式 ----------
    def add(self, rec: EligibilityRecord) -> None:
        self._rows.setdefault(rec.symbol, []).append(rec)
        self._sources.add(rec.source)

    # ---------- 日度模式 ----------
    def add_daily(self, symbol: str, day: int, st_status: str, source: str,
                  available_at: Optional[str] = None,
                  researcher_available_at: Optional[str] = None) -> None:
        """加入一条日度观测（双轴分开；空串=该轴未声明；None 知识轴->UNKNOWN 由调用方决定）。"""
        sym = normalize_symbol(symbol)
        d = int(day)
        eff = available_at if available_at is not None else _iso(d, 9, 30)
        # 知识轴未显式给出时，按"该轴未声明"处理（由调用方显式传入规则结果）
        res = researcher_available_at if researcher_available_at is not None else AVAIL_NOT_DECLARED
        slot = self._daily.setdefault(sym, {}).setdefault(
            d, {"statuses": set(), "sources": set(), "eff": eff, "res": res})
        slot["statuses"].add(st_status)
        slot["sources"].add(source)
        # 双轴：同一天多条时取**较晚**约束（更保守），UNKNOWN 优先
        for key, val in (("eff", eff), ("res", res)):
            cur_kind, cur_ts = parse_availability(slot[key])
            new_kind, new_ts = parse_availability(val)
            if new_kind == AvailabilityKind.UNKNOWN:
                slot[key] = AVAIL_UNKNOWN
            elif cur_kind == AvailabilityKind.UNKNOWN:
                pass
            elif new_kind == AvailabilityKind.KNOWN:
                if cur_kind != AvailabilityKind.KNOWN or new_ts > cur_ts:
                    slot[key] = val
        self._sources.add(source)

    def finalize(self) -> "HistoricalEligibilityTable":
        """事件模式：按 (symbol, effective_date) 归并，**只有完整约束等价才去重**。

        V4 修复（复核 V4-P1-01）：
          - 同 effective_date 同 st_status 但**约束不同**（更晚 available_at、
            UNKNOWN 门控、valid_to、来源不同）时，**必须合并约束**，
            不能只保留 `group[0]` 而丢弃较晚/未知约束。
          - 相邻记录仅在**完整语义等价**（状态 + 门控 + valid_to + 来源集合）时才压缩，
            因此"较晚同状态记录显式到期"不会被状态去重吃掉。
          - 同 effective_date **状态矛盾** -> CONFLICT 并保留全部来源。
          - **不按来源字母序**隐式决定优先级：同状态时按**约束最保守**合并
            （门控取最晚、UNKNOWN 优先、valid_to 取最早）。
        """
        for sym, rows in self._rows.items():
            rows.sort(key=lambda r: (r.effective_date, r.source))
            merged: List[EligibilityRecord] = []
            i = 0
            while i < len(rows):
                d = rows[i].effective_date
                group = [r for r in rows if r.effective_date == d]
                statuses = {r.st_status for r in group}
                if len(statuses) > 1:
                    merged.append(EligibilityRecord(
                        symbol=sym, effective_date=d, st_status=ST_CONFLICT,
                        source="MULTI_SOURCE_CONFLICT",
                        available_at=_latest_raw([r.available_at for r in group]),
                        conflict_sources=tuple(sorted({r.source for r in group})),
                        coverage_mode=CoverageMode.EVENT_PERSISTENT,
                        effective_available_at=_latest_raw(
                            [r.effective_available_at for r in group]),
                        researcher_available_at=_latest_raw(
                            [r.researcher_available_at for r in group]),
                        valid_to=_earliest_valid_to([r.valid_to for r in group]),
                    ))
                elif len(group) == 1:
                    merged.append(group[0])
                else:
                    # 同状态多来源：**合并全部约束**（不得只取 group[0]）
                    status = next(iter(statuses))
                    merged.append(EligibilityRecord(
                        symbol=sym, effective_date=d, st_status=status,
                        source="+".join(sorted({r.source for r in group})),
                        available_at=_latest_raw([r.available_at for r in group]),
                        coverage_mode=CoverageMode.EVENT_PERSISTENT,
                        effective_available_at=_latest_raw(
                            [r.effective_available_at for r in group]),
                        researcher_available_at=_latest_raw(
                            [r.researcher_available_at for r in group]),
                        valid_to=_earliest_valid_to([r.valid_to for r in group]),
                    ))
                i += len(group)
            # 压缩：仅当**完整语义等价**时才丢弃（否则保留较晚约束）
            compact: List[EligibilityRecord] = []
            for r in merged:
                if compact and _semantically_equal(compact[-1], r):
                    continue
                compact.append(r)
            self._rows[sym] = compact
        return self

    @classmethod
    def from_pit_st_csv(cls, csv_path, calendar: Sequence[int],
                        source: str = "baostock.query_history_k_data_plus.isST",
                        coverage_mode: str = CoverageMode.DAILY_OBSERVATION,
                        researcher_rule: Optional[str] = None
                        ) -> "HistoricalEligibilityTable":
        """从逐股 PIT isST CSV 构建（日度观测合同）。

        `researcher_rule` 必须由**调用方显式传入**：
          - `SAME_DAY_OPEN`：来源当日开盘前已发布；
          - `NEXT_SESSION_OPEN`：来源收盘后发布，次一交易日开盘才可知；
            日历末端无下一日 -> 存 AVAIL_UNKNOWN（fail-closed）。
        不传则记 `NOT_DECLARED`，使知识轴无约束（由调用方负责声明；
        本模块不擅自把批量历史状态升级为"当日开盘已知"）。
        """
        table = cls(coverage_mode=coverage_mode)
        table._calendar = [int(d) for d in calendar]
        table.researcher_rule = researcher_rule
        path = Path(csv_path)
        if not path.exists():
            return table
        with path.open(encoding="utf-8") as fh:
            fh.readline()
            for line in fh:
                parts = line.rstrip("\n").split(",")
                if len(parts) < 3:
                    continue
                code, day, is_st = parts[0].strip(), parts[1].strip(), parts[2].strip()
                if not code or not day.isdigit():
                    continue
                d = int(day)
                status = (ST_SPECIAL if is_st == "1"
                          else (ST_NORMAL if is_st == "0" else ST_UNKNOWN))
                if researcher_rule == "SAME_DAY_OPEN":
                    res = _iso(d, 9, 30)
                elif researcher_rule == "NEXT_SESSION_OPEN":
                    nxt = next_session_available_at(d, table._calendar)
                    res = nxt if nxt is not None else AVAIL_UNKNOWN
                else:
                    res = AVAIL_NOT_DECLARED
                table.add_daily(code, d, status, source,
                                available_at=_iso(d, 9, 30), researcher_available_at=res)
        return table.finalize()

    # ---------- 查询 ----------
    def _daily_entry(self, sym: str, day: int) -> Optional[dict]:
        slots = self._daily.get(sym)
        if not slots:
            return None
        return slots.get(int(day))

    def _event_record(self, sym: str, day: int) -> Optional[EligibilityRecord]:
        rows = self._rows.get(sym)
        if not rows:
            return None
        cur = None
        for r in rows:
            if r.effective_date <= int(day):
                cur = r
            else:
                break
        return cur

    def record_on(self, symbol: str, date: int,
                  as_of: Optional[str] = None) -> Optional[EligibilityRecord]:
        """返回该日适用的资格记录；None 表示**无可用证据**（UNKNOWN）。

        - 日度合同：必须命中当日观测；矛盾状态 -> CONFLICT；双轴门控。
        - 事件合同：取 <= 该日的最后变更点；检查 valid_to 与门控。
        `as_of` 为知识时刻（时区感知比较）。
        """
        sym = normalize_symbol(symbol)
        d = int(date)
        if sym in self._daily:
            slot = self._daily_entry(sym, d)
            if slot is None:
                return None
            statuses = slot["statuses"]
            if len(statuses) > 1:
                return EligibilityRecord(
                    symbol=sym, effective_date=d, st_status=ST_CONFLICT,
                    source="MULTI_SOURCE_CONFLICT",
                    available_at=slot["res"] if slot["res"] else slot["eff"],
                    conflict_sources=tuple(sorted(slot["sources"])),
                    coverage_mode=CoverageMode.DAILY_OBSERVATION,
                    effective_available_at=slot["eff"],
                    researcher_available_at=slot["res"])
            status = next(iter(statuses))
            rec = EligibilityRecord(
                symbol=sym, effective_date=d, st_status=status,
                source=",".join(sorted(slot["sources"])),
                available_at=slot["res"] if slot["res"] else slot["eff"],
                coverage_mode=CoverageMode.DAILY_OBSERVATION,
                effective_available_at=slot["eff"],
                researcher_available_at=slot["res"])
            if as_of is not None:
                kind, ts = rec.gate()
                if not gate_allows(kind, ts, as_of):
                    return None
            return rec
        rec = self._event_record(sym, d)
        if rec is None:
            return None
        if rec.valid_to is not None and d > int(rec.valid_to):
            return None
        if as_of is not None:
            kind, ts = rec.gate()
            if not gate_allows(kind, ts, as_of):
                return None
        return rec

    def status_on(self, symbol: str, date: int,
                  as_of: Optional[str] = None) -> str:
        rec = self.record_on(symbol, date, as_of=as_of)
        return rec.st_status if rec is not None else ST_UNKNOWN

    def is_eligible(self, symbol: str, date: int,
                    as_of: Optional[str] = None) -> bool:
        """只有**明确、未过期、且门控已放行**的 NORMAL 才合格。"""
        return self.status_on(symbol, date, as_of=as_of) in ELIGIBLE_STATES

    def availability_of(self, symbol: str, date: int) -> Optional[str]:
        """返回该日的知识轴可用时间（供调用方显式声明与审计）。"""
        sym = normalize_symbol(symbol)
        if sym in self._daily:
            slot = self._daily_entry(sym, int(date))
            if slot is None:
                return None
            return slot["res"] or slot["eff"]
        rec = self._event_record(sym, int(date))
        return rec.available_at if rec is not None else None

    # ---------- 覆盖度 ----------
    def coverage(self, calendar: Sequence[int], symbols: Iterable[str],
                 known_dates: Optional[Dict[str, Set[int]]] = None) -> dict:
        calset = {int(d) for d in calendar}
        sym_list = sorted({normalize_symbol(s) for s in symbols})
        total = covered = 0
        per_symbol_gaps: Dict[str, int] = {}
        for sym in sym_list:
            if known_dates is not None:
                have = {int(x) for x in known_dates.get(sym, set())}
            else:
                have = set(self._daily.get(sym, {}))
            total += len(calset)
            covered += len(calset & have)
            gap = len(calset - have)
            if gap:
                per_symbol_gaps[sym] = gap
        return {
            "coverage_contract": self.coverage_mode,
            "researcher_rule": getattr(self, "researcher_rule", None),
            "calendar_days": len(calset),
            "symbols": len(sym_list),
            "symbol_day_total": total,
            "symbol_day_covered": covered,
            "symbol_day_uncovered": total - covered,
            "symbols_with_gaps": len(per_symbol_gaps),
            "worst_gap_symbols": sorted(per_symbol_gaps.items(), key=lambda x: -x[1])[:10],
            "note": "未覆盖的交易日查询返回 UNKNOWN（fail-closed），不沿用上一观测。",
        }

    def conflict_days(self) -> List[dict]:
        out = []
        for sym, slots in self._daily.items():
            for d, slot in slots.items():
                if len(slot["statuses"]) > 1:
                    out.append({"symbol": sym, "date": int(d),
                                "statuses": sorted(slot["statuses"]),
                                "sources": sorted(slot["sources"])})
        return sorted(out, key=lambda x: (x["symbol"], x["date"]))

    def symbols(self) -> List[str]:
        return sorted(set(self._rows) | set(self._daily))

    def sources(self) -> List[str]:
        return sorted(self._sources)

    def status_counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for slots in self._daily.values():
            for slot in slots.values():
                s = ST_CONFLICT if len(slot["statuses"]) > 1 else next(iter(slot["statuses"]))
                out[s] = out.get(s, 0) + 1
        for rows in self._rows.values():
            for r in rows:
                out[r.st_status] = out.get(r.st_status, 0) + 1
        return out


def _earliest_valid_to(values: Sequence[Optional[int]]) -> Optional[int]:
    """多来源合并时取**最早**有效期（最保守：更早失效）。None 表示无限制。"""
    finite = [int(v) for v in values if v is not None]
    return min(finite) if finite else None


def _semantically_equal(a: "EligibilityRecord", b: "EligibilityRecord") -> bool:
    """两条记录是否**完整语义等价**（用于压缩去重）。

    V4 修复：不能只比 st_status —— 门控、valid_to、来源、覆盖合同都要一致，
    否则较晚约束或显式到期会被错误丢弃。
    """
    ka, ta = a.gate()
    kb, tb = b.gate()
    return (a.st_status == b.st_status
            and ka == kb and ta == tb
            and a.valid_to == b.valid_to
            and a.source == b.source
            and a.coverage_mode == b.coverage_mode
            and tuple(a.conflict_sources) == tuple(b.conflict_sources))


def _latest_raw(values: Sequence[str]) -> str:
    """在若干原始可用时间串中取**较晚**者；任一 UNKNOWN 则 UNKNOWN。"""
    kind, ts = resolve_gate(*values)
    if kind == AvailabilityKind.UNKNOWN:
        return AVAIL_UNKNOWN
    if kind == AvailabilityKind.NOT_APPLICABLE or ts is None:
        return AVAIL_NOT_DECLARED
    return ts.isoformat()
