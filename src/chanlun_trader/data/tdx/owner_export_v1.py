"""OWNER进程专用：定点源读取，输出只含批准窗口；不计算策略表现。"""
import hashlib
import json
from pathlib import Path
import struct

import numpy as np
import pandas as pd

from .providers import TDXProvider
from chanlun_trader.tdx_data import _DAY_STRUCT

START,END=20220722,20240731
OWNER='USER_AUTHORIZED_LOCAL_DATA_OWNER'


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def write_json(path,value):
    with Path(path).open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False,default=str)


def read_day_window(path,sessions):
    """复用原TDX记录合同，仅在日期键证明允许后读取该条价格。"""
    path=Path(path);before=path.stat();rows=[];digest=hashlib.sha256()
    if before.st_size%32:raise ValueError('TDX_RECORD_SIZE_CONFLICT')
    wanted=set(sessions)
    with path.open('rb') as f:
        for offset in range(0,before.st_size,32):
            f.seek(offset);day=struct.unpack('<I',f.read(4))[0]
            if day not in wanted:continue
            f.seek(offset);raw=f.read(32);digest.update(raw)
            row=_DAY_STRUCT.unpack(raw)
            rows.append(dict(date=row[0],open=row[1]/100,high=row[2]/100,low=row[3]/100,close=row[4]/100,
                amount_encoded=float(row[5]),volume_encoded=int(row[6])))
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('TDX_SOURCE_CHANGED')
    if len({r['date'] for r in rows})!=len(rows):raise ValueError('TDX_DUPLICATE_DAY')
    return rows,{'path':str(path),'size':before.st_size,'mtime_ns':before.st_mtime_ns,'window_sha256':digest.hexdigest(),'rows':len(rows)}


class OwnerDailyProviderV1(TDXProvider):
    """原统一TQ客户端；禁用全响应落盘、填充与默认当前日期。"""
    def get_daily(self,symbol,start=START,end=END):
        if not START<=start<=end<=END:raise PermissionError('OWNER_TQ_WINDOW_INVALID')
        raw=self.client.request('get_market_data',{'stock_list':[symbol],'field_list':['Open','High','Low','Close','Volume','Amount'],
            'period':'1d','start_time':str(start),'end_time':str(end),'count':0,'dividend_type':'none','fill_data':False},use_cache=False)
        if not isinstance(raw,dict):raise ValueError('TQ_DAILY_SHAPE_UNKNOWN')
        tab=raw.get(symbol)
        if tab is None and not raw:return []
        if not isinstance(tab,dict):raise ValueError('TQ_DAILY_SYMBOL_SHAPE_UNKNOWN')
        if str(tab.get('ErrorId','0'))!='0':raise ValueError('TQ_SYMBOL_DATA_UNAVAILABLE')
        dates=tab.get('Date',[]);fields=['Open','High','Low','Close','Volume','Amount']
        if any(len(tab.get(k,[]))!=len(dates) for k in fields):raise ValueError('TQ_COLUMN_LENGTH_CONFLICT')
        result=[]
        for i,value in enumerate(dates):
            day=int(str(value).replace('-',''))
            if not start<=day<=end:raise PermissionError('TQ_WINDOW_VIOLATION_RESPONSE_NOT_PERSISTED')
            row={k.lower():float(tab[k][i]) for k in fields}
            if not all(np.isfinite(v) for v in row.values()):raise ValueError('TQ_NONFINITE_DATA')
            result.append(dict(symbol=symbol,date=day,**row))
        if len({r['date'] for r in result})!=len(result):raise ValueError('TQ_DUPLICATE_DAY')
        return result


def compare_sources(tdx,tq):
    """唯一事前规则：日期完全一致，价格0.005元，量额倍率全部行通过。"""
    left={r['date']:r for r in tdx};right={r['date']:r for r in tq}
    diagnostics=[];prices=bool(left) and set(left)==set(right)
    for day in sorted(set(left)|set(right)):
        a,b=left.get(day),right.get(day)
        if a is None or b is None:
            diagnostics.append({'date':day,'status':'DATE_MISSING'});continue
        diffs={k:abs(a[k]-b[k]) for k in ['open','high','low','close']}
        prices=prices and max(diffs.values())<=0.005
        diagnostics.append({'date':day,'price_differences':diffs,'tdx_volume':a['volume_encoded'],'tq_volume':b['volume'],
            'tdx_amount':a['amount_encoded'],'tq_amount':b['amount']})
    scales={}
    for name,candidates in [('volume',[1,100]),('amount',[1,10000])]:
        checks=[]
        complete=bool(diagnostics) and all('tdx_'+name in r for r in diagnostics)
        for ratio in candidates:
            valid=complete and any(r['tq_'+name]>0 for r in diagnostics)
            for r in diagnostics:
                if 'tdx_'+name not in r:valid=False;continue
                a,b=r['tdx_'+name],r['tq_'+name]*ratio
                # 量允许最多1股的接口舍入；金额容忍float32/万元显示舍入，逐行不跳过。
                tolerance=1 if name=='volume' else max(1,abs(a)*1e-6)
                if abs(a-b)>tolerance:valid=False
            checks.append({'ratio':ratio,'all_rows_pass':bool(valid)})
        winners=[r['ratio'] for r in checks if r['all_rows_pass']]
        scales[name]={'checks':checks,'unique_ratio':winners[0] if len(winners)==1 else None}
    return {'price_semantics_compatible':bool(prices),'scales':scales,'rows':diagnostics}


def parse_gbbq_window(path,audit_sink=None):
    """全历史只存在本函数所在OWNER进程内；异常不携带原始事件值。"""
    from pytdx.reader import GbbqReader
    import inspect
    path=Path(path)
    with path.open('rb') as stream:count=struct.unpack('<I',stream.read(4))[0]
    if path.stat().st_size!=4+count*29:raise ValueError('GBBQ_FORMAT_SIZE_NOT_4_PLUS_29N')
    before=sha(path)
    identity={'source_sha256':before,'parser_version':'pytdx.GbbqReader',
        'parser_sha256':sha(inspect.getfile(GbbqReader)),'input_records':count}
    if audit_sink:audit_sink(identity)
    try:frame=GbbqReader().get_df(str(path))
    except Exception:raise ValueError('GBBQ_INSTALLED_PARSER_FAILED_NO_RAW_DETAILS') from None
    if len(frame)!=count or sha(path)!=before:raise ValueError('GBBQ_COUNT_OR_IDENTITY_CONFLICT')
    try:
        pd.to_datetime(frame.datetime.astype(str),format='%Y%m%d',errors='raise')
        if not frame.code.str.fullmatch(r'\d{6}').all():raise ValueError()
    except Exception:raise ValueError('GBBQ_PARSED_SCHEMA_UNRELIABLE') from None
    window=frame[(frame.datetime>=START)&(frame.datetime<=END)].copy()
    del frame
    window_records=len(window)
    # gbbq含其他市场不等于解析错误；本批准成员只有SH/SZ，按其市场身份选择。
    window=window[window.market.isin([0,1])].copy()
    window['symbol']=window.code+window.market.map({0:'.SZ',1:'.SH'})
    audit={**identity,'window_records':window_records,
        'outside_discarded':count-window_records}
    if audit_sink:audit_sink(audit)
    return window,audit
