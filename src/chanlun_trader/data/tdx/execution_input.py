"""交易级TRAIN输入V1：只建模日线可见性，保留历史证据和未知状态。"""
from __future__ import annotations

import pandas as pd

from chanlun_trader.data_adapter import Bar, DataCapability
from .providers import TDXProvider


def modeled_close(day: int) -> pd.Timestamp:
    return pd.Timestamp(str(day),tz='Asia/Shanghai')+pd.Timedelta(hours=15,microseconds=1)


def effective_time(day: int, source_published_at=None, original_available_at=None):
    times=[modeled_close(day)]
    for value in (source_published_at,original_available_at):
        if value is None or value=='UNKNOWN' or pd.isna(value): continue
        stamp=pd.Timestamp(value)
        if stamp.tzinfo is None: raise ValueError('TIME_EVIDENCE_MUST_BE_AWARE')
        times.append(stamp)
    return max(times)


def classify_state(row: dict) -> str:
    if row.get('conflict') or row.get('eligibility_status')=='CONFLICT': return 'CONFLICT'
    if row.get('universe_member') is False: return 'NOT_MEMBER'
    st,susp=row.get('st_status'),row.get('suspension_status')
    if st not in {'NORMAL','ST'} or susp not in {'TRADING','SUSPENDED'}: return 'UNKNOWN'
    if susp=='SUSPENDED': return 'KNOWN_SUSPENDED'
    if st=='ST': return 'KNOWN_ST'
    if row.get('universe_member') is True and row.get('eligibility_status')=='ELIGIBLE': return 'KNOWN_ELIGIBLE'
    return 'UNKNOWN'


class ExecutionTrainingAdapterV1:
    """遵守DataAdapter接口；volume/amount单位未证实时拒绝提供成交Bar。"""
    def __init__(self, frame, calendar, *, units_verified=False):
        if list(calendar)!=sorted(set(calendar)): raise ValueError('CALENDAR_INVALID')
        if frame.duplicated(['symbol','date']).any(): raise ValueError('DUPLICATE_SECURITY_DATE')
        if not set(frame.date)<=set(calendar): raise ValueError('BAR_OUTSIDE_CALENDAR')
        self.frame=frame.copy(); self.calendar=list(calendar); self.units_verified=units_verified

    def capability(self):
        return DataCapability(fields={'open','high','low','close'},periods={'day'},
            history_start=self.calendar[0],history_end=self.calendar[-1])

    def available_at(self, field, code, date):
        # 不把本轮时间假设回填进旧DataAdapter的真实历史时间接口。
        return None

    def observed_history(self, code, now):
        now=pd.Timestamp(now)
        if now.tzinfo is None: raise ValueError('CLOCK_MUST_BE_AWARE')
        frame=self.frame[self.frame.symbol==code]
        if frame.empty: return frame.copy()
        visible=frame.apply(lambda r: effective_time(int(r.date),r.get('source_published_at'),r.get('available_at'))<=now,axis=1)
        return frame.loc[visible].copy()

    def get_bars(self, code, period='day'):
        if period!='day': raise ValueError('DAILY_ONLY')
        if not self.units_verified: raise ValueError('EXECUTION_UNITS_UNVERIFIED')
        return [Bar(code=code,date=int(r.date),open=r.open,high=r.high,low=r.low,close=r.close,
                    volume=r.volume,amount=r.amount) for r in self.frame[self.frame.symbol==code].itertuples()]

    def next_open(self, signal_day):
        i=self.calendar.index(signal_day)
        if i+1==len(self.calendar): return None
        return pd.Timestamp(str(self.calendar[i+1]),tz='Asia/Shanghai')+pd.Timedelta(hours=9,minutes=30)


class BoundedTQEvidenceProviderV1(TDXProvider):
    """复用TQClient获取受限日历；已知不隔离窗口的公司行动接口不可用。"""
    def calendar(self,start,end,market='SH'):
        if not 20220701<=int(start)<=int(end)<=20240731: raise ValueError('CALENDAR_SCOPE')
        result=self.client.request('get_trading_calendar',{'market':market,'start_time':str(start),'end_time':str(end)},use_cache=False,retries=1)
        values=result['Date'] if isinstance(result,dict) else result
        days=[int(str(v).replace('-','')) for v in values]
        if days!=sorted(set(days)) or any(not int(start)<=d<=int(end) for d in days): raise ValueError('PROVIDER_CALENDAR_SCOPE_CONFLICT')
        return days

    def corporate_actions(self,symbol,start,end):
        if not 20220701<=int(start)<=int(end)<=20240731: raise ValueError('ACTION_SCOPE')
        # 本机已实证忽略日期参数；禁止再次先接收跨窗事件再过滤。
        raise PermissionError('PROVIDER_ACTION_WINDOW_NOT_ENFORCED; OWNER_WINDOWED_EXPORT_REQUIRED')


def normalize_windowed_actions(rows, sessions, symbols):
    """验收所有者已在上游窗口化的事件；不负责读取混合文件或定义会计规则。"""
    normalized, identities = [], set()
    required = {'event_id','symbol','effective_date','event_type','terms','units','source','source_published_at'}
    for row in rows:
        if not required <= row.keys():
            raise ValueError('ACTION_FIELDS_MISSING')
        if row['symbol'] not in symbols or row['effective_date'] not in sessions:
            raise ValueError('ACTION_EXPORT_SCOPE_CONFLICT')
        if row['event_id'] in identities:
            raise ValueError('ACTION_IDENTITY_CONFLICT')
        identities.add(row['event_id'])
        normalized.append(dict(row))
    return normalized


# 沿用既有TRAIN诊断reader的定长日期键定位算法；旧caller的窗口断言不变。
import hashlib
import struct
from pathlib import Path
from chanlun_trader.research.io_safety import _DAY_STRUCT

FIELDS = ["date", "open", "high", "low", "close", "amount_encoded", "volume_encoded"]

def read_warmup_day_window(path, sessions):
    """只seek日期键以定位，再读取允许区间；全文hash永不计算。"""
    if len(sessions) != 6 or list(sessions) != sorted(set(sessions)) or not 20220701 <= sessions[0] <= sessions[-1] < 20220801:
        raise ValueError("EXACT_SIX_WARMUP_SESSIONS_REQUIRED")
    start, end = sessions[0], sessions[-1]
    path = Path(path)
    before = path.stat()
    if before.st_size % _DAY_STRUCT.size:
        raise ValueError("TDX_RECORD_SIZE_CONFLICT")
    n = before.st_size // _DAY_STRUCT.size
    date_reads = []
    with path.open("rb") as handle:
        def date_at(i):
            handle.seek(i * 32)
            data = handle.read(4)
            if len(data) != 4:
                raise ValueError("TRUNCATED_DATE_KEY")
            date_reads.append(i * 32)
            return struct.unpack("<I", data)[0]

        def lower_bound(target):
            lo, hi = 0, n
            while lo < hi:
                mid = (lo + hi) // 2
                if date_at(mid) < target:
                    lo = mid + 1
                else:
                    hi = mid
            return lo

        left, right = lower_bound(start), lower_bound(end + 1)
        # 首先逐一读允许段的日期键；遇异常不读取该段价格。
        dates = [date_at(i) for i in range(left, right)]
        if dates != sorted(set(dates)) or any(d not in sessions for d in dates):
            raise ValueError("TDX_WINDOW_DATE_INDEX_CONFLICT")
        handle.seek(left * 32)
        data = handle.read((right - left) * 32)
    if len(data) != (right - left) * 32:
        raise ValueError("TRUNCATED_ALLOWED_RECORDS")
    rows = []
    for encoded in _DAY_STRUCT.iter_unpack(data):
        rows.append([encoded[0], *(x / 100.0 for x in encoded[1:5]), encoded[5], encoded[6]])
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("SOURCE_CHANGED_DURING_READ")
    return pd.DataFrame(rows, columns=FIELDS), {
        "path": str(path), "record_range_half_open": [left, right],
        "price_byte_range_half_open": [left * 32, right * 32], "price_bytes_read": len(data),
        "date_key_offsets": sorted(set(date_reads)), "date_key_bytes_read": len(date_reads) * 4,
        "window_bytes_sha256": hashlib.sha256(data).hexdigest(), "full_file_hash": None,
        "rows": len(rows), "date_index_assumption": "TDX_CHRONOLOGICAL_FIXED_RECORD_FORMAT",
    }
