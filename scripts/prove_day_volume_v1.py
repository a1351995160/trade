"""当前精确DAY清单的量纲取证；原件只读，日期判断前不读取行情字段。"""
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import struct
import sys
import time

SOURCE = Path(__file__).resolve().parents[1]
BASE = Path('E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/materialized-v3')
ROOT = BASE.parent / 'degraded-volume-semantics-v1'
OWNER = Path('E:/llmwiki/owner-execution-export-v1/run-v2')
SAMPLES = ['600000.SH','600004.SH','000001.SZ','000002.SZ']


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(name, value):
    from chanlun_trader.research_factory.exploration_governance import immutable
    immutable(ROOT/name, value)


def freeze():
    from chanlun_trader.research_factory.volume_semantics_v1 import RULES
    from pytdx.reader import TdxDailyBarReader
    import inspect
    if datetime.now(timezone.utc) >= datetime.fromisoformat('2026-09-14T10:05:03+08:00'):
        raise PermissionError('ORIGINAL_APPROVAL_EXPIRED')
    plan = read(BASE.parent/'INPUT_READ_PLAN_V2.json')
    files = {s:r['path'] for s,r in plan['warmup_sources']}
    if len(files) != 5036:
        raise ValueError('SOURCE_LIST_IDENTITY_CONFLICT')
    save('VOLUME_SEMANTICS_PREREGISTRATION.json', {'rules':RULES,'samples':SAMPLES,
        'record_length':32,'layout':'<IIIIIfI4s','volume_offset':24,'volume_type':'uint32',
        'files':files,'parser_sha256':sha(SOURCE/'src/chanlun_trader/tdx_data.py'),
        'pytdx_version':importlib.metadata.version('pytdx'),
        'installed_reader_sha256':sha(inspect.getfile(TdxDailyBarReader)),
        'source_directory':'E:/new_tdx_mock/vipdoc', 'source_list_sha256':sha(BASE.parent/'INPUT_READ_PLAN_V2.json'),
        'daily_snapshot_sha256':sha(BASE/'DAILY.parquet'), 'units_sha256':sha(OWNER/'staging/units.json'),
        'amount_evidence_sha256':sha(OWNER/'staging/unit_source_evidence.json'),
        'created_at':datetime.now(timezone.utc).isoformat(),
        'authorization':'CURRENT_USER_EXPLICIT_VOLUME_SEMANTICS_REQUEST_FROM_F7425F8',
        'reader_thread_id':os.environ.get('CODEX_THREAD_ID','CURRENT_LOCAL_TASK'),
        'code_sha256':sha(Path(__file__)),
        'rules_source_sha256':sha(SOURCE/'src/chanlun_trader/research_factory/volume_semantics_v1.py')})


def validate():
    import numpy as np
    import pandas as pd
    import pyarrow.parquet as pq
    from chanlun_trader.tdx_data import _DAY_STRUCT
    from chanlun_trader.research_factory.volume_semantics_v1 import RULES, summarize, decide
    p=read(ROOT/'VOLUME_SEMANTICS_PREREGISTRATION.json')
    if p['rules']!=RULES or sha(Path(__file__))!=p['code_sha256']:
        raise ValueError('PREREGISTRATION_CODE_CONFLICT')
    if _DAY_STRUCT.size!=32 or _DAY_STRUCT.format!='<IIIIIfI4s':
        raise ValueError('BINARY_LAYOUT_CONFLICT')
    units=read(OWNER/'staging/units.json'); prior=read(OWNER/'staging/unit_source_evidence.json')
    if sha(OWNER/'staging/units.json')!=p['units_sha256'] or sha(OWNER/'staging/unit_source_evidence.json')!=p['amount_evidence_sha256']:
        raise ValueError('AMOUNT_EVIDENCE_CHANGED')
    if units['amount_unit']!='CNY' or not all(x['scales']['amount']['unique_ratio']==10000 for x in prior['comparison']):
        raise ValueError('AMOUNT_CNY_NOT_PROVEN')
    if sha(BASE/'DAILY.parquet')!=p['daily_snapshot_sha256']:
        raise ValueError('FROZEN_DAY_SNAPSHOT_CHANGED')
    # 已冻结快照只含TRAIN和六预热；投影TRAIN，与当前二进制逐值绑定，不覆盖原值。
    table=pq.ParquetFile(BASE/'DAILY.parquet')
    idx=table.schema.names.index('date')
    for g in range(table.metadata.num_row_groups):
        st=table.metadata.row_group(g).column(idx).statistics
        if st is None or not 20220722<=st.min<=st.max<=20240731:
            raise PermissionError('SNAPSHOT_WINDOW_UNPROVEN')
    frame=pq.read_table(BASE/'DAILY.parquet',columns=['symbol','date','open','high','low','close','amount_encoded','volume_encoded'],
        filters=[('date','>=',20220801),('date','<=',20240731)],use_threads=False).to_pandas(use_threads=False)
    grouped={s:g.set_index('date') for s,g in frame.groupby('symbol',sort=False)}
    del frame
    chunks=[]; markets=[]; sample_values={s:[] for s in SAMPLES}
    exclusions={'AMOUNT_NONPOSITIVE_OR_NONFINITE':0,'VOLUME_NONPOSITIVE':0,'INVALID_OHLC':0}
    layout=[]; mismatch=0; total=0
    sessions=set(read(BASE/'CALENDAR.json')['train_sessions'])
    access=ROOT/'RAW_ACCESS.jsonl'
    with access.open('x',encoding='utf-8') as journal:
        for symbol,name in p['files'].items():
            path=Path(name); stat=path.stat()
            if path.suffix.lower()!='.day' or not path.resolve().is_relative_to(Path('E:/new_tdx_mock/vipdoc').resolve()):
                raise PermissionError('SOURCE_PATH_OUTSIDE_FIXED_ROOT')
            if stat.st_size%32:
                raise ValueError('RECORD_LENGTH_CONFLICT:'+symbol)
            journal.write(json.dumps({'path':str(path),'symbol':symbol,'phase':'READ_STARTED','pid':os.getpid(),
                'date_key_only_outside_train':True,'size':stat.st_size,'mtime_ns':stat.st_mtime_ns})+'\n');journal.flush()
            digest=hashlib.sha256(); rows=[]; dates=[]
            with path.open('rb',buffering=0) as stream:
                for offset in range(0,stat.st_size,32):
                    stream.seek(offset); prefix=stream.read(4)
                    day=struct.unpack('<I',prefix)[0]
                    if day not in sessions:
                        continue
                    raw=prefix+stream.read(28); digest.update(raw)
                    row=_DAY_STRUCT.unpack(raw)
                    if struct.unpack_from('<I',raw,24)[0]!=row[6]:
                        raise ValueError('VOLUME_OFFSET_CONFLICT')
                    dates.append(day)
                    rows.append([row[1]/100,row[2]/100,row[3]/100,row[4]/100,row[5],row[6]])
            if (path.stat().st_size,path.stat().st_mtime_ns)!=(stat.st_size,stat.st_mtime_ns):
                raise ValueError('SOURCE_CHANGED_DURING_READ')
            data=np.asarray(rows,dtype=float).reshape(-1,6); total+=len(data)
            original=grouped.pop(symbol,None)
            if original is None or len(set(dates))!=len(dates) or set(original.index)!=set(dates):
                mismatch+=1
            elif not np.array_equal(data,original.loc[dates,['open','high','low','close','amount_encoded','volume_encoded']].to_numpy(dtype=float),equal_nan=True):
                mismatch+=1
            invalid=~np.isfinite(data[:,:4]).all(axis=1) | (data[:,:4]<=0).any(axis=1) | (data[:,1]<data[:,2]) | (data[:,0]<data[:,2]) | (data[:,0]>data[:,1]) | (data[:,3]<data[:,2]) | (data[:,3]>data[:,1])
            bad_amount=~invalid & (~np.isfinite(data[:,4]) | (data[:,4]<=0)); bad_volume=~invalid & ~bad_amount & (data[:,5]<=0)
            exclusions['INVALID_OHLC']+=int(invalid.sum());exclusions['AMOUNT_NONPOSITIVE_OR_NONFINITE']+=int(bad_amount.sum());exclusions['VOLUME_NONPOSITIVE']+=int(bad_volume.sum())
            valid=data[~invalid & ~bad_amount & ~bad_volume][:,[4,5,2,1]]
            chunks.append(valid);markets.extend([symbol[-2:]]*len(valid))
            if symbol in SAMPLES:
                sample_values[symbol]=valid
            info={'symbol':symbol,'path':str(path),'record_length':32,'train_records':len(data),
                'train_bytes_sha256':digest.hexdigest(),'volume_offset':24,'volume_type':'uint32',
                'file_sha256':sha(path) if symbol in SAMPLES else None}
            layout.append(info);journal.write(json.dumps({**info,'phase':'READ_FINISHED'})+'\n');journal.flush()
    all_values=np.concatenate(chunks); market=np.asarray(markets); groups={}
    for name,data in {'ALL':all_values,'SH':all_values[market=='SH'],'SZ':all_values[market=='SZ'],**sample_values}.items():
        if not len(data):
            raise ValueError('PREREGISTERED_GROUP_EMPTY:'+name)
        groups[name]={hyp:summarize(*data.T,scale) for hyp,scale in RULES['hypotheses'].items()}
    derived=decide(groups)
    if mismatch or grouped:
        derived='UNKNOWN'
    save('BINARY_LAYOUT_VALIDATION.json',{'layout':'<IIIIIfI4s','parser_sha256':p['parser_sha256'],
        'installed_reader_sha256':p['installed_reader_sha256'],'pytdx_version':p['pytdx_version'],
        'source_directory':p['source_directory'],'files':layout,'snapshot_conflicting_symbols':mismatch,
        'frozen_snapshot_symbols_without_raw_check':sorted(grouped),'total_train_bars':total})
    save('VOLUME_DIMENSIONAL_VALIDATION.json',{'type':RULES['version'],'derived_volume_unit':derived,
        'preregistration_sha256':sha(ROOT/'VOLUME_SEMANTICS_PREREGISTRATION.json'),
        'groups':groups,'excluded':exclusions,'total_bars':total,'valid_bars':len(all_values),
        'snapshot_conflicting_symbols':mismatch,'eps':RULES['eps'],'no_outcome':True,
        'amount_evidence':'VERIFIED_DERIVED_CNY_10000X','public_corroboration_required_before_contract':True})
    print(json.dumps({'derived_volume_unit':derived,'total_bars':total,'valid_bars':len(all_values),
        'excluded':exclusions,'snapshot_conflicting_symbols':mismatch,'ALL':groups['ALL']}))


if __name__=='__main__':
    if '--worker' in sys.argv:
        from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
        worker_resource_handshake();validate()
    else:
        if (ROOT/'VOLUME_SEMANTICS_PREREGISTRATION.json').exists():
            raise PermissionError('PREREGISTERED_RUN_ALREADY_EXISTS_NO_AUTOMATIC_REPEAT')
        freeze()
        from chanlun_trader.synthetic_batch_resources import run_bounded_worker
        start=time.monotonic()
        env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONIOENCODING':'utf-8',
            **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
        result=run_bounded_worker([sys.executable,str(Path(__file__)),'--worker'],root=SOURCE,
            memory_mib=2048,wall_seconds=900,environment=env,
            on_started=lambda pid:save('WORKER_STARTED.json',{'pid':pid,'memory_mib':2048,'wall_seconds':900}),
            execution={'purpose':'VOLUME_DIMENSIONAL_VALIDATION_NO_OUTCOME'})
        save('WORKER_RESULT.json',{'elapsed_seconds':time.monotonic()-start,'returncode':result['returncode'],
            'timed_out':result['timed_out'],'stdout':result['stdout'].decode('utf-8',errors='replace'),
            'stderr':result['stderr'].decode('utf-8',errors='replace')})
        print(result['stdout'].decode('utf-8',errors='replace'));print(result['stderr'].decode('utf-8',errors='replace'))
        sys.exit(result['returncode'])
