"""所有账户后端共用的冻结/受限worker/结算入口；不自动生成批准。"""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import shutil
import sys
import time

HANDSHAKE=None
if __name__=='__main__' and '--worker' in sys.argv:
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    HANDSHAKE=worker_resource_handshake()

from chanlun_trader.research_factory.strategy_interface_v1 import prepare,backend_for,run
from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
from chanlun_trader.research_factory.exploration_governance import immutable,read_json

REPO=Path(__file__).resolve().parents[1]
if str(REPO/'scripts') not in sys.path:sys.path.insert(0,str(REPO/'scripts'))


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path,value):
    immutable(path,json.loads(json.dumps(value,ensure_ascii=False,default=str,allow_nan=False)))


def resolve(name):
    module,attribute=name.split(':',1)
    if not module or not attribute or '.' in attribute:raise ValueError('DIRECT_LOCAL_CALLABLE_REQUIRED')
    value=getattr(importlib.import_module(module),attribute)
    if not callable(value) or inspect.getsourcefile(value) is None:raise ValueError('LOCAL_SOURCE_CALLABLE_REQUIRED')
    return value


def load_etf_snapshot(identity_path,identity_sha256,calendar_path,calendar_sha256,actions_path,actions_sha256):
    """复用已校验ETF快照，不重新下载、不改变原价/分红定义。"""
    import pandas as pd
    for path,digest in ((identity_path,identity_sha256),(calendar_path,calendar_sha256),(actions_path,actions_sha256)):
        if sha(path)!=digest:raise PermissionError('ETF_LOADER_EVIDENCE_CHANGED')
    identity=read_json(identity_path)
    if identity.get('basic_input_checks')!='MODEL_BASIC_INPUT_CHECKS_PASSED' or sha(identity['path'])!=identity['sha256']:
        raise PermissionError('ETF_LOADER_INPUT_NOT_VERIFIED')
    calendar=read_json(calendar_path);frame=pd.read_parquet(identity['path'])
    if list(map(int,frame.date))!=calendar['warmup']+calendar['train']:raise ValueError('ETF_LOADER_CALENDAR_CONFLICT')
    return {'frame':frame,'actions':read_json(actions_path)['actions'],'input_identity':identity}


def load_stock_provider_snapshot(base_manifest_path,base_manifest_sha256,feature_manifest_path,feature_manifest_sha256,
                                 feature_path,contract):
    """调用已经固化的BaoStock Provider，再接冻结策略因子；不混用复权成交价。"""
    import pandas as pd
    import prepare_baostock_account_v1 as provider
    from chanlun_trader.research_factory.common import stable_hash
    if Path(base_manifest_path).resolve()!=(provider.ROOT/'INPUT_MANIFEST.json').resolve() or sha(base_manifest_path)!=base_manifest_sha256:
        raise PermissionError('STOCK_PROVIDER_MANIFEST_CONFLICT')
    if sha(feature_manifest_path)!=feature_manifest_sha256:raise PermissionError('STOCK_FEATURE_MANIFEST_CONFLICT')
    manifest=read_json(feature_manifest_path)
    if sha(feature_path)!=manifest['features_sha256']:raise PermissionError('STOCK_FEATURE_FILE_CONFLICT')
    bundle=provider.load_bundle()
    if manifest['parent_input_identity']!=bundle.input_identity or manifest['input_identity']!=stable_hash([bundle.input_identity,manifest['features_sha256'],contract]):
        raise PermissionError('STOCK_FEATURE_LINEAGE_OR_CONTRACT_CONFLICT')
    bundle.ready_factors=pd.read_parquet(feature_path);bundle.factor_identity=manifest['features_sha256']
    bundle.input_identity=manifest['input_identity'];bundle.contract_identity=stable_hash(contract)
    return {'frame':bundle,'actions':bundle.actions,'input_identity':bundle.input_identity}


load_stock_provider_snapshot.source_files=tuple(str(REPO/p) for p in (
    'scripts/prepare_baostock_account_v1.py','scripts/baostock_alias_v1.py','scripts/run_baostock_account_v1.py',
    'src/chanlun_trader/research_factory/baostock_price_views_v1.py',
    'src/chanlun_trader/research_factory/baostock_account_v1.py'))


def freeze_config(config,root):
    """只导入可信本地插件/Provider，不调用数据loader、不计算绩效。"""
    root=Path(root)
    if root.exists():raise PermissionError('JOB_ROOT_ALREADY_EXISTS_NO_OVERWRITE')
    import subprocess
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
    dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=REPO,text=True,encoding='utf-8'))
    plans={};items={};sources={str(Path(__file__).resolve()):sha(__file__)}
    for item in config['items']:
        factory=resolve(item['factory']);loader=resolve(item['loader'])
        strategy=factory(**item.get('factory_kwargs',{}));name=strategy.strategy_id
        if name in items:raise ValueError('DUPLICATE_STRATEGY_ID')
        backend=backend_for(strategy,item.get('backend_options'))
        paths={inspect.getsourcefile(factory),inspect.getsourcefile(loader),__file__,
            str(REPO/'src/chanlun_trader/research_factory/etf_account_governance_v1.py'),
            str(REPO/'src/chanlun_trader/research_factory/budget.py'),
            str(REPO/'src/chanlun_trader/synthetic_batch_resources.py'),
            *getattr(loader,'source_files',()),*item.get('dependency_files',[])}
        runtime={**item,'benchmark_id':config.get('benchmark_id'),'source_identity':[commit,dirty],
                 'source_hashes':{str(Path(p).resolve()):sha(p) for p in paths}}
        plan=prepare(strategy,backend,runtime);plans[name]=plan;items[name]=runtime
        sources.update(runtime['source_hashes']);sources.update(plan['strategy']['source_hashes'])
        sources.update(plan['backend']['source_hashes'])
    if not plans:raise ValueError('EMPTY_JOB')
    if config.get('benchmark_id') is not None and config['benchmark_id'] not in plans:raise ValueError('BENCHMARK_NOT_IN_FROZEN_JOB')
    job={'plans':plans,'items':items,'root':str(root.resolve()),'objective_id':config['objective_id'],
        'budget_path':config['budget_path'],'input_identity':config['input_identity'],
        'source_hashes':sources,'benchmark_id':config.get('benchmark_id'),'resources':{'worker_seconds':900,'memory_mib':2048,
            'total_seconds':min(4500,900*len(plans)),'threads':1,'concurrency':1},
        'prepared_at':datetime.now(timezone.utc).isoformat()}
    save(root/'JOB.json',job)
    for path,digest in sources.items():
        target=root/'source-archive'/(digest+'_'+Path(path).name);target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():shutil.copyfile(path,target)
    return job


def service(job):return StrategyBatchGovernanceV1(job['root'],job['budget_path'],job['objective_id'],job['plans'])


def validate_sources(job):
    if set(job['items'])!=set(job['plans']) or any(job['items'][k]!=job['plans'][k]['runtime'] for k in job['plans']):
        raise PermissionError('JOB_RUNTIME_PLAN_CONFLICT')
    if any(item.get('benchmark_id')!=job.get('benchmark_id') for item in job['items'].values()):
        raise PermissionError('JOB_BENCHMARK_CONFLICT')
    for path,digest in job['source_hashes'].items():
        if sha(path)!=digest:raise PermissionError('JOB_FROZEN_SOURCE_CHANGED:'+path)


def worker(path,name):
    if HANDSHAKE is None or HANDSHAKE['execution']!={'purpose':name}:raise PermissionError('BOUNDED_WORKER_REQUIRED')
    job=read_json(path);validate_sources(job);gov=service(job);gov.active_execution(name)
    item=job['items'][name];strategy=resolve(item['factory'])(**item.get('factory_kwargs',{}))
    backend=backend_for(strategy,item.get('backend_options'))
    if prepare(strategy,backend,item)!=job['plans'][name]:raise PermissionError('WORKER_PLAN_CONFLICT')
    root=Path(job['root'])
    save(root/(name+'_INPUT_ACCESS.json'),{'reader_pid':os.getpid(),'input_identity':job['input_identity'],
        'loader':item['loader'],'loader_kwargs':item.get('loader_kwargs',{}),'purpose':name,
        'accessed_at':datetime.now(timezone.utc).isoformat()})
    try:
        data=resolve(item['loader'])(**item.get('loader_kwargs',{}))
        if data['input_identity']!=job['input_identity']:raise PermissionError('LOADED_INPUT_IDENTITY_CONFLICT')
        result=run(strategy,backend,frame=data['frame'],actions=data['actions'],input_identity=data['input_identity'],
            active_check=lambda:gov.active_execution(name),runtime=item)
        save(root/(name+'_RESULT.json'),result)
    except Exception as exc:
        save(root/(name+'_FAILURE.json'),{'exception_type':type(exc).__name__,'message':str(exc),
            'evidence':getattr(exc,'evidence',None),'automatic_retry':False})
        raise


def execute(path):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    job=read_json(path);validate_sources(job);gov=service(job);root=Path(job['root']);gov.active()
    if root.resolve()!=Path(path).resolve().parent:raise PermissionError('JOB_ROOT_IDENTITY_CONFLICT')
    # 历史失败/未结算不能被本入口自动重跑。
    if any((root/(name+'_START.json')).exists() for name in job['plans']):raise PermissionError('JOB_ALREADY_ATTEMPTED_RECONCILE_NO_REPLAY')
    consumed=0.
    for name in job['plans']:
        receipt=gov.active()
        remaining=min(min(4500,900*len(job['plans']))-consumed,
            (datetime.fromisoformat(receipt['expires_at'])-datetime.now(timezone.utc)).total_seconds())
        if remaining<=0:raise PermissionError('JOB_RESOURCE_OR_APPROVAL_EXHAUSTED')
        validate_sources(job);gov.start(name);begin=time.monotonic()
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1',
            **{k:'1' for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')}}
        env['PYTHONPATH']=str(REPO/'src')+os.pathsep+env.get('PYTHONPATH','')
        env.pop('CHANLUN_TEST_ISOLATION',None)
        try:
            resource=run_bounded_worker([sys.executable,str(Path(__file__).resolve()),'--worker',name,'--job',str(Path(path).resolve())],
                root=REPO,memory_mib=2048,wall_seconds=min(900,remaining),environment=env,execution={'purpose':name},
                on_started=lambda pid:save(root/(name+'_WORKER.json'),{'pid':pid,'purpose':name}))
        except Exception as exc:
            resource={'returncode':None,'timed_out':False,'error_type':type(exc).__name__,'error':str(exc)}
        seconds=time.monotonic()-begin;consumed+=seconds
        save(root/(name+'_RESOURCE.json'),{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in resource.items()})
        output=root/(name+'_RESULT.json');complete=resource['returncode']==0 and output.exists()
        gov.settle(name,completed=complete,seconds=seconds,result_hash=sha(output) if output.exists() else None,
                   error=None if complete else 'WORKER_FAILED_SEE_RESOURCE')
        if not complete:raise RuntimeError('JOB_WORKER_FAILED_NO_RETRY')
    index={name:{'result':str(root/(name+'_RESULT.json')),'sha256':sha(root/(name+'_RESULT.json')),
                 'settlement':str(root/(name+'_SETTLEMENT.json'))} for name in job['plans']}
    write_reports(job,index)
    save(root/'RESULTS_INDEX.json',{'items':index,'worker_seconds':consumed,'exposures':len(index),'repair_exposures':0})
    return index


def write_reports(job,index):
    from copy import deepcopy
    from chanlun_trader.research_factory.strategy_report_v1 import render_markdown
    root=Path(job['root']);results={name:read_json(item['result']) for name,item in index.items()}
    def dates(result):
        if 'daily' in result:return [str(row['date']) for row in result['daily']]
        return [str(row['timestamp'])[:10].replace('-','') for row in result['daily_account']]
    control=results.get(job.get('benchmark_id'))
    for name,result in results.items():
        if read_json(index[name]['settlement'])['result_sha256']!=index[name]['sha256']:
            raise PermissionError('REPORT_SETTLEMENT_CONFLICT')
        report=deepcopy(result['report'])
        report['artifacts']['source_result']={'path':index[name]['result'],'sha256':index[name]['sha256']}
        report['artifacts']['trades']=index[name]['result']+'#fills'
        ledger_key=next(k for k in ('ledger','ledgers','final_account_checkpoint') if k in result)
        report['artifacts']['ledger']=index[name]['result']+'#'+ledger_key
        if control is not None:
            comparable=dates(result)==dates(control) and result.get('metrics') is not None and control.get('metrics') is not None
            report['benchmark']={'status':'AVAILABLE' if comparable else 'NOT_COMPARABLE',
                'strategy_id':job['benchmark_id'],'metrics':control['report']['metrics'] if comparable else None}
            report['limitations'].append('日期一致不代表实际风险暴露一致；不据此直接宣称alpha。')
        save(root/(name+'_REPORT.json'),report)
        with (root/(name+'_REPORT.md')).open('x',encoding='utf-8') as stream:stream.write(render_markdown(report))
        index[name]['report']=str(root/(name+'_REPORT.md'))
    save(root/'REPORT_ACCESS.json',{'reader_pid':os.getpid(),'purpose':'FIXED_JOB_RESULTS_REPORT',
        'result_hashes':{name:item['sha256'] for name,item in index.items()},'accessed_at':datetime.now(timezone.utc).isoformat()})


def status(path):
    """只读执行进度与结算原因，不读取收益或持仓。"""
    job=read_json(path);root=Path(job['root']);states={}
    for name in job['plans']:
        settled=root/(name+'_SETTLEMENT.json');started=root/(name+'_START.json')
        if settled.exists():
            value=read_json(settled)
            states[name]={'state':'COMPLETED' if value['completed'] else 'FAILED','error':value['error'],
                'wall_seconds':value['wall_seconds'],'resource_path':str(root/(name+'_RESOURCE.json'))}
        elif started.exists():states[name]={'state':'UNSETTLED_CHECK_WORKER','started_at':read_json(started)['started_at']}
        else:states[name]={'state':'NOT_STARTED'}
    complete=sum(v['state']=='COMPLETED' for v in states.values())
    return {'completed':complete,'total':len(states),'progress_percent':100*complete/len(states),
            'report_index_exists':(root/'RESULTS_INDEX.json').exists(),'items':states}


if __name__=='__main__':
    parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare');group.add_argument('--execute',action='store_true');group.add_argument('--worker');group.add_argument('--status',action='store_true')
    parser.add_argument('--root');parser.add_argument('--job');args=parser.parse_args()
    if args.prepare:
        if not args.root:parser.error('--root required')
        freeze_config(read_json(args.prepare),args.root)
    elif args.worker:worker(args.job,args.worker)
    elif args.status:print(json.dumps(status(args.job),ensure_ascii=False))
    else:print(json.dumps(execute(args.job),ensure_ascii=False))
