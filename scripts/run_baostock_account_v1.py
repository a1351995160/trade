"""已批准固定计划的数据物化；原资源限制器内顺序获取，不计算信号。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

SOURCE = Path(__file__).resolve().parents[1]
LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
BASE = Path('E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1')
ROOT = BASE/'baostock-account-v1'
PARENT = BASE.parent/'revised-exploration-v1'
FIELDS = {'3': 'date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,isST',
          '1': 'date,code,close,adjustflag'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    from chanlun_trader.research_factory.exploration_governance import immutable
    immutable(Path(path), value)


def quality_path(batch):
    alias = ROOT/'quality-alias-v1'/f'batch-{batch}.json'
    if batch == 14 and alias.exists():
        from baostock_alias_v1 import evidence
        item = evidence(ROOT)
        if not item or read(alias)['alias_identity'] != item['identity']:
            raise PermissionError('ALIAS_QUALITY_IDENTITY_CHANGED')
        return alias
    revised = ROOT/'quality-ipo-v1'/f'batch-{batch}.json'
    return revised if revised.exists() else ROOT/'quality'/f'batch-{batch}.json'


def resume_revision(root=None):
    from chanlun_trader.research_factory.common import stable_hash
    root = ROOT if root is None else root
    value = read(root/'ACQUISITION_RESUME_V1.json')
    if value['identity'] != stable_hash({k:v for k,v in value.items() if k != 'identity'}):
        raise PermissionError('RESUME_REVISION_IDENTITY_CHANGED')
    for path, expected in value['evidence'].items():
        if sha(path) != expected:
            raise PermissionError('RESUME_EVIDENCE_CHANGED')
    if (value['total_seconds'] != 21600 or value['retry'] != ['002853.SZ','1']
            or value['worker_symbols'] != 50):
        raise PermissionError('RESUME_SCOPE_CHANGED')
    return value


def response_directory(symbol, flag, root=None):
    root = ROOT if root is None else root
    directory = root/'responses'/symbol
    if symbol == '002853.SZ' and flag == '1' and (root/'ACQUISITION_RESUME_V1.json').exists():
        resume_revision(root)
        return directory/'attempt-2'
    return directory


def register_resume():
    from chanlun_trader.research_factory.common import stable_hash
    active()
    if (ROOT/'ACQUISITION_RESUME_V1.json').exists():
        return resume_revision()
    thread = os.environ.get('CODEX_THREAD_ID')
    if not thread:
        raise PermissionError('ACTUAL_THREAD_ID_REQUIRED')
    original = ROOT/'responses/002853.SZ'
    if (original/'1.json').exists() or (original/'1.access.json').exists():
        raise PermissionError('INTERRUPTED_RESPONSE_STATE_CHANGED')
    failed = ROOT/'resources/fetch-hfq1-6.json'
    if not read(failed)['timed_out']:
        raise PermissionError('EXPECTED_TIMEOUT_MISSING')
    paths = [SOURCE/'docs/baostock-acquisition-resume-decision-v1.md',
             ROOT/'READ_PLAN.json', ROOT/'APPROVAL_FACT.json',
             original/'1.started.json', failed]
    value = {'purpose':'INPUT_ACQUISITION_ONLY_NO_AUTOMATIC_BACKTEST',
        'origin':'USER_EXPLICIT_APPROVAL', 'reader':thread,
        'approval_statement':'批准，能成功读数之后不需要你持续监控，告诉我循环监控的脚本就好了',
        'registered_at':datetime.now(timezone.utc).isoformat(),
        'total_seconds':21600,'worker_symbols':50,'retry':['002853.SZ','1'],
        'evidence':{str(p):sha(p) for p in paths}}
    value['identity'] = stable_hash(value)
    save(ROOT/'ACQUISITION_RESUME_V1.json', value)
    return value


def active():
    from chanlun_trader.research_factory.common import stable_hash
    if os.environ.get('CHANLUN_TEST_ISOLATION') == '1':
        raise PermissionError('REAL_INPUT_CANNOT_USE_SYNTHETIC_ISOLATION')
    parent = read(PARENT/'governance/confirmation.json')
    if parent['receipt_id'] != stable_hash({k:v for k,v in parent.items() if k != 'receipt_id'}):
        raise PermissionError('PARENT_RECEIPT_CORRUPT')
    expiry = min(datetime.fromisoformat(parent['plan']['expires_at']),
                 datetime.fromisoformat('2026-09-14T10:05:03+08:00'))
    if datetime.now(timezone.utc) >= expiry:
        raise PermissionError('APPROVAL_EXPIRED')
    if any(p.exists() for p in [PARENT/'governance/revocation.json',
            PARENT/'governance/baostock_account_v1/revocation.json']):
        raise PermissionError('APPROVAL_REVOKED')
    return parent, expiry


def freeze():
    parent, expiry = active()
    package = BASE/'baostock-account-preparation-v2/CONFIRMATION_PACKAGE.json'
    if sha(package) != '4a2aafeb7f07811ae7cac1ec7ce7413f055c4ec06bfe2f43e44b1e8fb8aed448':
        raise ValueError('APPROVED_PACKAGE_CHANGED')
    manifest = read(BASE/'materialized-v3/MANIFEST.json')
    paths = [BASE/'materialized-v3'/n for n in ['CALENDAR.json','HISTORICAL_POOL_AND_STATE.parquet']]
    for p in paths:
        if sha(p) != manifest['files'][p.name]['sha256']:
            raise ValueError('HISTORICAL_INPUT_CHANGED')
    universe = BASE/'degraded-train-v1/FROZEN_UNIVERSE.json'
    index = read(BASE/'degraded-train-v1/RESULTS_INDEX.json')
    if sha(universe) != index['files'][universe.name]['sha256']:
        raise ValueError('UNIVERSE_CHANGED')
    symbols = read(universe)['symbols']
    if len(symbols) != 5182 or len(set(symbols)) != 5182:
        raise ValueError('UNIVERSE_IDENTITY_CONFLICT')
    thread = os.environ.get('CODEX_THREAD_ID')
    if not thread:
        raise PermissionError('ACTUAL_THREAD_ID_REQUIRED')
    save(ROOT/'APPROVAL_FACT.json', {'origin':'USER_EXPLICIT_PLAN_APPROVAL_VIA_CODEX',
        'thread_id':thread,'approval_statement':'批准',
        'approved_package_path':str(package),'approved_package_sha256':sha(package),
        'scope':'ONE_FIXED_MAIN_AND_ONE_CONFIRMED_ENGINEERING_REPAIR',
        'source_kind':'CURRENT_USER_MESSAGE_IN_REPLY_TO_SPECIFIC_CONFIRMATION_PACKAGE',
        'parent_receipt_id':parent['receipt_id'],'expires_at':expiry.isoformat()})
    save(ROOT/'READ_PLAN.json', {'symbols':sorted(symbols),'start':'2022-07-22','end':'2024-07-31',
        'fields':FIELDS,'frequency':'d','provider':'BaoStock5MinProvider.session',
        'files':{str(p):sha(p) for p in [*paths,universe]},
        'purpose':'APPROVED_RAW_HFQ_INPUT_MATERIALIZATION_NO_SIGNAL_NO_OUTCOME',
        'batch_symbols':200,'per_worker_seconds':900,'memory_mib':2048,'numeric_threads':1,
        'total_seconds':5400,'account_seconds_reserved':1800,
        'budget_before_sha256':sha(PARENT/'governance/search_budget_registry.json'),
        'first_frozen_at':datetime.now(timezone.utc).isoformat(),
        'reader':thread,'recipient':'REQUESTING_USER','no_new_budget_registered':True})


def fetch(batch, resume_start=None):
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    worker_resource_handshake()
    from importlib.metadata import version
    from chanlun_trader.data.minute.baostock_provider import BaoStock5MinProvider
    active()
    if resume_start is None:
        for previous in range(batch):
            if not quality_path(previous).exists():
                validate_batch(previous)
    plan = read(ROOT/'READ_PLAN.json')
    provider = BaoStock5MinProvider()
    with provider.session():
        start = batch*200 if resume_start is None else resume_start
        end = (batch+1)*200 if resume_start is None else min(start+50,len(plan['symbols']))
        for symbol in plan['symbols'][start:end]:
            for flag, fields in FIELDS.items():
                active()
                directory = response_directory(symbol, flag)
                target = directory/(flag+'.json')
                started = directory/(flag+'.started.json')
                query = {'code':symbol[-2:].lower()+'.'+symbol[:6], 'fields':fields,
                    'start_date':plan['start'],'end_date':plan['end'],'frequency':'d','adjustflag':flag}
                if started.exists():
                    access = directory/(flag+'.access.json')
                    if (target.exists() and access.exists() and sha(target) == read(access)['sha256']
                            and read(started)['query'] == query and read(target)['query'] == query
                            and read(access)['error_code'] == '0'):
                        continue
                    raise PermissionError('REQUEST_ALREADY_ATTEMPTED_NO_AUTOMATIC_RETRY:'+symbol+':'+flag)
                save(started, {'query':query,'started_at':datetime.now(timezone.utc).isoformat(),
                    'reader_pid':os.getpid(),'provider_version':version('baostock'),
                    'loaded_driver_sha256':LOADED_SOURCE_SHA256})
                response = provider.bs.query_history_k_data_plus(**query)
                rows = []
                while response.error_code == '0' and response.next():
                    rows.append(dict(zip(response.fields,response.get_row_data())))
                save(target, {'query':query,'fields':response.fields,'rows':rows,
                    'error_code':response.error_code,'error_msg':response.error_msg,
                    'completed_at':datetime.now(timezone.utc).isoformat()})
                save(directory/(flag+'.access.json'), {'path':str(target),'sha256':sha(target),
                    'row_count':len(rows),'error_code':response.error_code,
                    'purpose':plan['purpose'],'reader_pid':os.getpid(),'recipient':'REQUESTING_USER'})
                if response.error_code != '0':
                    raise RuntimeError('PROVIDER_RESPONSE_FAILED_NO_RETRY')
            print(json.dumps({'symbol':symbol,'status':'RESPONSES_ARCHIVED'}),flush=True)
    if resume_start is None or end % 200 == 0 or end == len(plan['symbols']):
        validate_batch(batch)


def validate_batch(batch):
    import pyarrow.parquet as pq
    from chanlun_trader.research_factory.baostock_input_v1 import verify_pair
    active()
    plan = read(ROOT/'READ_PLAN.json')
    for name, expected in plan['files'].items():
        if sha(name) != expected:
            raise ValueError('FROZEN_INPUT_IDENTITY_CHANGED')
    days = read(BASE/'materialized-v3/CALENDAR.json')['sessions']
    symbols = plan['symbols'][batch*200:(batch+1)*200]
    state_path = BASE/'materialized-v3/HISTORICAL_POOL_AND_STATE.parquet'
    meta = pq.ParquetFile(state_path)
    column = meta.schema.names.index('trade_date')
    for i in range(meta.metadata.num_row_groups):
        stats = meta.metadata.row_group(i).column(column).statistics
        if (not stats or not stats.has_min_max or
                not 20220722 <= int(str(stats.min).replace('-','')) <= int(str(stats.max).replace('-','')) <= 20240731):
            raise PermissionError('STATE_PHYSICAL_WINDOW_UNPROVEN')
    states = pq.read_table(state_path, filters=[('symbol','in',symbols)], use_threads=False,
        columns=['symbol','trade_date','listed','delisted','universe_member',
                 'eligibility_status','st_status','suspension_status']).to_pandas(use_threads=False)
    groups = {s:g.to_dict('records') for s,g in states.groupby('symbol')}
    results = []
    for symbol in symbols:
        values = []
        for flag in ['3','1']:
            p = response_directory(symbol, flag)/(flag+'.json')
            access = read(p.with_name(flag+'.access.json'))
            if sha(p) != access['sha256']:
                raise ValueError('RESPONSE_HASH_CHANGED')
            values.append(read(p))
        results.append(verify_pair(symbol,*values,set(days),groups.get(symbol,[])))
    save(ROOT/'quality-ipo-v1'/f'batch-{batch}.json', {'results':results,
        'passed':all(r['passed'] for r in results),'reader_pid':os.getpid(),
        'state_sha256':sha(state_path),'purpose':'NO_SIGNAL_INPUT_VERIFICATION'})
    if not all(r['passed'] for r in results):
        raise ValueError('INPUT_QUALITY_FAILED_SEE_EXACT_BATCH_EVIDENCE')


def factor_probe():
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    worker_resource_handshake()
    from chanlun_trader.data.minute.baostock_provider import BaoStock5MinProvider
    active()
    plan = read(ROOT/'FACTOR_BASELINE_READ_PLAN.json')
    provider = BaoStock5MinProvider()
    with provider.session():
        for symbol in plan['symbols']:
            active()
            path = ROOT/'factor-baseline'/f'{symbol}.json'
            if path.with_suffix('.started.json').exists():
                raise PermissionError('FACTOR_REQUEST_ALREADY_ATTEMPTED')
            query = {'code':symbol[-2:].lower()+'.'+symbol[:6],
                     'start_date':plan['start'],'end_date':plan['end']}
            save(path.with_suffix('.started.json'),{'query':query,'reader_pid':os.getpid(),
                'started_at':datetime.now(timezone.utc).isoformat()})
            response = provider.bs.query_adjust_factor(**query)
            rows = []
            while response.error_code == '0' and response.next():
                rows.append(dict(zip(response.fields,response.get_row_data())))
            save(path, {'query':query,'error_code':response.error_code,
                'error_msg':response.error_msg,'fields':response.fields,'rows':rows})
            save(path.with_suffix('.access.json'),{'path':str(path),'sha256':sha(path),
                'rows':len(rows),'reader_pid':os.getpid(),'purpose':plan['purpose']})


def acquire():
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    if not (ROOT/'READ_PLAN.json').exists():
        freeze()
    plan = read(ROOT/'READ_PLAN.json')
    if read(ROOT/'ENCODING_CORRECTION.json')['correct_hfq_flag'] != '1':
        raise PermissionError('ENCODING_CORRECTION_REQUIRED')
    used = read(ROOT/'resources/fetch-0.json')['elapsed_seconds']
    for name in ['factor-probe.json','quality-ipo-0.json','quality-ipo-2.json']:
        path = ROOT/'resources'/name
        if path.exists():
            used += read(path)['elapsed_seconds']
    for batch in range((len(plan['symbols'])+199)//200):
        receipt = ROOT/'resources'/f'fetch-hfq1-{batch}.json'
        if receipt.exists():
            old = read(receipt); used += old['elapsed_seconds']
            if old['returncode']:
                proof_path = ROOT/'QUALITY_RECONCILIATION.json'
                if not proof_path.exists():
                    raise RuntimeError('PRIOR_FETCH_FAILED_RECONCILIATION_REQUIRED')
                proof = read(proof_path)
                if (batch != 2 or proof['failed_process_sha256'] != sha(receipt) or
                        proof['new_quality_sha256'] != sha(quality_path(batch)) or
                        not read(quality_path(batch))['passed']):
                    raise PermissionError('EXACT_FAILED_BATCH_RECONCILIATION_REQUIRED')
            continue
        _, expiry = active()
        seconds = min(900,5400-used,(expiry-datetime.now(timezone.utc)).total_seconds())
        if seconds <= 0:
            raise PermissionError('TOTAL_PLAN_RESOURCE_LIMIT_REACHED')
        env = {**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONIOENCODING':'utf-8',
            **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
        start = time.monotonic()
        result = run_bounded_worker([sys.executable,str(Path(__file__)),'--fetch-worker',str(batch)],
            root=SOURCE,memory_mib=2048,wall_seconds=seconds,environment=env,
            execution={'purpose':plan['purpose'],'approval_sha256':sha(ROOT/'APPROVAL_FACT.json')},
            on_started=lambda pid:save(ROOT/'resources'/f'fetch-hfq1-{batch}.started.json',
                {'pid':pid,'wall_seconds':seconds,'memory_mib':2048,'numeric_threads':1}))
        elapsed = time.monotonic()-start; used += elapsed
        save(receipt, {'elapsed_seconds':elapsed,
            **{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
        print(json.dumps({'batch':batch,'returncode':result['returncode'],'seconds_used':used}),flush=True)
        if result['returncode']:
            raise RuntimeError('FETCH_FAILED_EVIDENCE_PRESERVED')


def resume_acquire():
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    revision = resume_revision()
    plan = read(ROOT/'READ_PLAN.json')
    # 原成功六批必须有通过证据；第七批失败由修订中的精确哈希保留。
    for batch in range(6):
        if not read(quality_path(batch))['passed']:
            raise PermissionError('PRIOR_QUALITY_NOT_PASSED')
    for start_index in range(1200,len(plan['symbols']),50):
        _, expiry = active()
        label = f'resume-fetch-{start_index}'
        receipt = ROOT/'resources'/f'{label}.json'
        started_path = ROOT/'resources'/f'{label}.started.json'
        if receipt.exists():
            if read(receipt)['returncode'] != 0:
                from baostock_alias_v1 import evidence
                item = evidence(ROOT) if start_index == 2950 else None
                if (not item or item['files'].get(str(receipt)) != sha(receipt)
                        or not read(quality_path(14))['passed']):
                    raise PermissionError('RESUME_FAILED_NO_AUTOMATIC_RETRY')
            continue
        if started_path.exists():
            raise PermissionError('UNSETTLED_RESUME_WORKER_REQUIRES_RECONCILIATION')
        used = sum(read(p).get('elapsed_seconds',0) for p in (ROOT/'resources').glob('*.json'))
        seconds = min(900,revision['total_seconds']-used,(expiry-datetime.now(timezone.utc)).total_seconds())
        if seconds <= 0:
            raise PermissionError('TOTAL_PLAN_RESOURCE_LIMIT_REACHED')
        env = {**os.environ,'PYTHONPATH':str(SOURCE/'src'),'PYTHONIOENCODING':'utf-8',
            **{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
        started = time.monotonic()
        result = run_bounded_worker([sys.executable,str(Path(__file__)),'--resume-worker',str(start_index)],
            root=SOURCE,memory_mib=2048,wall_seconds=seconds,environment=env,
            execution={'purpose':revision['purpose'],'revision_identity':revision['identity']},
            on_started=lambda pid:save(started_path,{'pid':pid,'wall_seconds':seconds,
                'memory_mib':2048,'numeric_threads':1}))
        save(receipt,{'elapsed_seconds':time.monotonic()-started,
            **{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in result.items()}})
        print(json.dumps({'start_index':start_index,'returncode':result['returncode']}),flush=True)
        if result['returncode'] != 0:
            raise RuntimeError('RESUME_FETCH_FAILED_EVIDENCE_PRESERVED')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fetch-worker',type=int)
    parser.add_argument('--factor-worker',action='store_true')
    parser.add_argument('--validate-worker',type=int)
    parser.add_argument('--register-resume',action='store_true')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--resume-worker',type=int)
    args = parser.parse_args()
    if args.register_resume:
        register_resume()
    elif args.resume:
        resume_acquire()
    elif args.resume_worker is not None:
        resume_revision()
        if args.resume_worker < 1200 or args.resume_worker % 50:
            raise PermissionError('RESUME_WORKER_RANGE_INVALID')
        fetch(args.resume_worker//200,args.resume_worker)
    elif args.factor_worker:
        factor_probe()
    elif args.validate_worker is not None:
        from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
        worker_resource_handshake()
        validate_batch(args.validate_worker)
    elif args.fetch_worker is not None:
        fetch(args.fetch_worker)
    else:
        acquire()
