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
WORKER_STARTED_AT=None
if __name__=='__main__' and ('--worker' in sys.argv or '--compute' in sys.argv):
    # 时钟先于限制握手、领域导入和资料加载；这些时间都属于原段。
    WORKER_STARTED_AT=time.monotonic()
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    HANDSHAKE=worker_resource_handshake()

from chanlun_trader.research_factory.strategy_interface_v1 import prepare,backend_for,run
from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
from chanlun_trader.research_factory.exploration_governance import immutable,read_json

REPO=Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:sys.path.insert(0,str(REPO))
if str(REPO/'scripts') not in sys.path:sys.path.insert(0,str(REPO/'scripts'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


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
    if any(item.get('backend_options', {}).get('backend_version') in {'UNIVERSE_ACCOUNT_BACKEND_V1','UNIVERSE_ACCOUNT_BACKEND_V2'}
           for item in config['items']):
        from chanlun_trader.research_factory.universe_submission_v1 import validate_universe_freeze_scopes
        validate_universe_freeze_scopes(config)
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
        if item.get('backend_options', {}).get('backend_version') in {'UNIVERSE_ACCOUNT_BACKEND_V1','UNIVERSE_ACCOUNT_BACKEND_V2'}:
            mode = config.get('benchmark_mode', 'NONE')
            if mode not in {'NONE', 'CASH_AND_PRICE_REFERENCE'}:
                raise ValueError('JOB_UNIVERSE_BENCHMARK_MODE_INVALID')
            runtime['benchmark_mode'] = mode
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
    if 'execution_profile' in config:
        from chanlun_trader.research_factory.universe_execution_profile_v1 import validate_execution_profile
        profile=validate_execution_profile(config['execution_profile'])
        if any(plan['backend']['backend'] != 'UNIVERSE_ACCOUNT_BACKEND_V2'
                or items[name].get('execution_profile') != profile
                or plan['backend'].get('execution_profile') != profile for name,plan in plans.items()):
            raise ValueError('JOB_LONG_HORIZON_PROFILE_CONFLICT')
        job['resources']=profile
        job['observation_plan']=config['observation_plan']
        job['resource_job_total_seconds']=profile['total_seconds']*len(plans)
    elif any(plan['backend']['backend'] == 'UNIVERSE_ACCOUNT_BACKEND_V2' for plan in plans.values()):
        raise ValueError('JOB_LONG_HORIZON_PROFILE_REQUIRED')
    if 'benchmark_mode' in config:
        if config['benchmark_mode'] not in {'NONE', 'CASH_AND_PRICE_REFERENCE'}:
            raise ValueError('JOB_UNIVERSE_BENCHMARK_MODE_INVALID')
        job['benchmark_mode'] = config['benchmark_mode']
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
    if any(item.get('benchmark_mode') != job.get('benchmark_mode', 'NONE')
           for name, item in job['items'].items()
           if job['plans'][name]['backend']['backend'] in {'UNIVERSE_ACCOUNT_BACKEND_V1','UNIVERSE_ACCOUNT_BACKEND_V2'}):
        raise PermissionError('JOB_UNIVERSE_BENCHMARK_CONFLICT')
    if (any(plan['backend']['backend'] == 'UNIVERSE_ACCOUNT_BACKEND_V2' for plan in job['plans'].values())
            and 'profile_id' not in job.get('resources', {})):
        raise PermissionError('JOB_LONG_HORIZON_PROFILE_REQUIRED')
    if any(plan['backend']['backend'] in {'UNIVERSE_ACCOUNT_BACKEND_V1','UNIVERSE_ACCOUNT_BACKEND_V2'} for plan in job['plans'].values()):
        from chanlun_trader.research_factory.universe_submission_v1 import validate_frozen_universe_scopes
        validate_frozen_universe_scopes(job)
    if 'profile_id' in job.get('resources', {}):
        from chanlun_trader.research_factory.universe_execution_profile_v1 import validate_execution_profile
        profile=validate_execution_profile(job['resources'])
        if (job.get('resource_job_total_seconds') != profile['total_seconds']*len(job['plans'])
                or any(plan['backend']['backend'] != 'UNIVERSE_ACCOUNT_BACKEND_V2'
                    or plan['runtime'].get('execution_profile') != profile
                    or plan['backend'].get('execution_profile') != profile
                    or plan['runtime'].get('observation_plan') != job.get('observation_plan')
                    for plan in job['plans'].values())):
            raise PermissionError('JOB_FROZEN_EXECUTION_PROFILE_CONFLICT')
    for path,digest in job['source_hashes'].items():
        if sha(path)!=digest:raise PermissionError('JOB_FROZEN_SOURCE_CHANGED:'+path)


def worker(path,name,segment_number=None):
    if segment_number is not None:
        return long_horizon_worker(path,name,segment_number)
    if HANDSHAKE is None or HANDSHAKE['execution']!={'purpose':name}:raise PermissionError('BOUNDED_WORKER_REQUIRED')
    job=read_json(path);validate_sources(job);gov=service(job);gov.active_execution(name)
    item=job['items'][name];strategy=resolve(item['factory'])(**item.get('factory_kwargs',{}))
    backend=backend_for(strategy,item.get('backend_options'))
    if prepare(strategy,backend,item)!=job['plans'][name]:raise PermissionError('WORKER_PLAN_CONFLICT')
    root=Path(job['root'])
    save(root/(name+'_INPUT_ACCESS.json'),{'reader_pid':os.getpid(),'reader_parent_pid':os.getppid(),
        'launcher_pid':HANDSHAKE.get('launcher_pid'),'resource_platform':os.name,
        'windows_job_verified':HANDSHAKE.get('windows_job_verified',False),'input_identity':job['input_identity'],
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


def execute_accounts(path, *, recover=False):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    job=read_json(path);validate_sources(job);gov=service(job);root=Path(job['root'])
    if 'profile_id' in job.get('resources', {}):
        return execute_long_horizon_accounts(path,recover=recover)
    if root.resolve()!=Path(path).resolve().parent:raise PermissionError('JOB_ROOT_IDENTITY_CONFLICT')
    # 历史失败/未结算不能被本入口自动重跑。
    if not recover and any((root/(name+'_START.json')).exists() for name in job['plans']):raise PermissionError('JOB_ALREADY_ATTEMPTED_RECONCILE_NO_REPLAY')
    consumed=0.
    for name in job['plans']:
        if (root/(name+'_SETTLEMENT.json')).exists():
            settled=validated_settlement(job,name)
            consumed+=settled['wall_seconds']
            continue
        if (root/(name+'_START.json')).exists():
            settled=reconcile_account(path,name)
            consumed+=settled['wall_seconds']
            continue
        receipt=gov.active()
        remaining=min(min(4500,900*len(job['plans']))-consumed,
            (datetime.fromisoformat(receipt['expires_at'])-datetime.now(timezone.utc)).total_seconds())
        if receipt['source'].get('origin') == 'CAMPAIGN_V1':
            from chanlun_trader.research_factory.etf_account_governance_v1 import validate_campaign_source
            campaign = validate_campaign_source(receipt['source'], job['plans'], job['objective_id'])
            operation = campaign.status()['operations'][receipt['source']['operation_ids'][name]]
            remaining = min(remaining, operation['upper_bounds']['wall_seconds'])
        if remaining<=0:raise PermissionError('JOB_RESOURCE_OR_APPROVAL_EXHAUSTED')
        validate_sources(job);gov.start(name);begin=time.monotonic()
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1',
            **{k:'1' for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')}}
        env['PYTHONPATH']=os.pathsep.join((str(REPO/'src'),str(REPO),env.get('PYTHONPATH','')))
        env.pop('CHANLUN_TEST_ISOLATION',None)
        try:
            resource=run_bounded_worker([sys.executable,str(Path(__file__).resolve()),'--worker',name,'--job',str(Path(path).resolve())],
                root=REPO,memory_mib=2048,wall_seconds=min(900,remaining),environment=env,execution={'purpose':name},
                on_started=lambda pid:save(root/(name+'_WORKER.json'),{'pid':pid,'purpose':name}))
        except Exception as exc:
            resource={'returncode':None,'timed_out':False,'error_type':type(exc).__name__,'error':str(exc)}
        seconds=time.monotonic()-begin;consumed+=seconds
        resource['elapsed_wall_seconds']=seconds
        save(root/(name+'_RESOURCE.json'),{k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in resource.items()})
        output=root/(name+'_RESULT.json');complete=resource['returncode']==0 and not resource.get('timed_out') and output.exists()
        gov.settle(name,completed=complete,seconds=seconds,result_hash=sha(output) if output.exists() else None,
                   error=None if complete else 'WORKER_FAILED_SEE_RESOURCE')
        if not complete:raise RuntimeError('JOB_WORKER_FAILED_NO_RETRY')
    index={name:{'result':str(root/(name+'_RESULT.json')),'sha256':sha(root/(name+'_RESULT.json')),
                 'settlement':str(root/(name+'_SETTLEMENT.json')),'report':str(root/(name+'_REPORT.md'))} for name in job['plans']}
    if (root/'RESULTS_INDEX.json').exists():
        previous=read_json(root/'RESULTS_INDEX.json')['items']
        if set(previous)!=set(index) or any(previous[name].get(key)!=row[key] for name,row in index.items() for key in ('result','sha256','settlement')):
            raise PermissionError('JOB_EXISTING_INDEX_CONFLICT')
        return previous
    save(root/'RESULTS_INDEX.json',{'items':index,'worker_seconds':consumed,'exposures':len(index),'repair_exposures':0})
    return index


def segment_prefix(name,number):
    if type(number) is not int or number < 1:
        raise ValueError('JOB_SEGMENT_NUMBER_INVALID')
    return name+'_SEGMENT_'+str(number).zfill(6)


def control_state(job):
    from chanlun_trader.research_factory.common import stable_hash
    root=Path(job['root']);head=None;paused=False;rows=[]
    for path in sorted(root.glob('CONTROL_*.json')):
        record=read_json(path)
        if (record.get('sequence') != len(rows)+1 or record.get('previous_head') != head
                or record.get('job_sha256') != sha(root/'JOB.json')
                or record.get('event') not in ('PAUSE','RESUME')
                or record.get('event_id') != stable_hash({k:v for k,v in record.items() if k!='event_id'})):
            raise PermissionError('JOB_CONTROL_CHAIN_CONFLICT')
        head=record['event_id'];paused=record['event']=='PAUSE';rows.append(record)
    return {'paused':paused,'head':head,'events':rows}


def control_job(path,event):
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock
    job=read_json(path);validate_sources(job);root=Path(job['root'])
    if 'profile_id' not in job.get('resources', {}) or event not in ('PAUSE','RESUME'):
        raise PermissionError('JOB_LONG_HORIZON_CONTROL_REQUIRED')
    with ObjectiveMutationLock.for_resource(root/'CONTROL.lock'):
        gov=service(job)
        started=next((name for name in job['plans'] if (root/(name+'_START.json')).exists()),None)
        receipt=gov.dispatched_execution(started) if event=='RESUME' and started else gov.active()
        state=control_state(job)
        compute_done=root/'COMPUTE_REPORT'/'COMPUTE_START.json'
        if all((root/(name+'_SETTLEMENT.json')).exists() for name in job['plans']) and compute_done.exists():
            scope=read_json(root/'COMPUTE_REPORT'/'SCOPE.json')
            from chanlun_trader.research_factory.universe_compute_governance_v1 import UniverseComputeGovernanceV1
            completed=UniverseComputeGovernanceV1(root/'COMPUTE_REPORT',scope['authority'],scope['request'],'REPORT').status()
            terminal=completed['segments'] and completed['segments'][-1]['charge']['outcome'] in ('COMPLETED','FAILED')
        else:terminal=False
        if terminal:
            raise PermissionError('JOB_CONTROL_TERMINAL')
        if state['paused'] == (event=='PAUSE'):
            return state
        record={'sequence':len(state['events'])+1,'previous_head':state['head'],'event':event,
            'job_sha256':sha(root/'JOB.json'),'receipt_id':receipt['receipt_id'],
            'recorded_at':datetime.now(timezone.utc).isoformat()}
        record['event_id']=stable_hash(record)
        save(root/('CONTROL_'+str(record['sequence']).zfill(6)+'.json'),record)
        return control_state(job)


def record_segment_failure(folder,prefix,dispatch_id,scope_identity,exc):
    save(Path(folder)/(prefix+'_FAILURE.json'),{'exception_type':type(exc).__name__,'message':str(exc),
        'dispatch_id':dispatch_id,'scope_identity':scope_identity,
        'evidence':getattr(exc,'evidence',None),'automatic_retry':False})


def long_horizon_worker(path,name,number):
    job=read_json(path);root=Path(job['root']);pending=service(job).segment_status(name)['pending']
    try:
        return _long_horizon_worker(path,name,number)
    except Exception as exc:
        if pending and pending['segment_number']==number:
            record_segment_failure(root,segment_prefix(name,number),pending['dispatch_id'],sha(path),exc)
        raise


def cooperative_deadline(profile_id,upper_bound,begin):
    """新分段规格共享 worker 绝对截止，固定留60秒提交与退出；硬上限不变。"""
    from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE
    if profile_id!=SEGMENTED_PROFILE:
        return begin+upper_bound*.8
    if WORKER_STARTED_AT is None:
        raise PermissionError('JOB_LONG_HORIZON_WORKER_CLOCK_REQUIRED')
    if HANDSHAKE['wall_seconds']<=60:
        raise PermissionError('JOB_LONG_HORIZON_COOPERATIVE_WINDOW_EXHAUSTED')
    return WORKER_STARTED_AT+HANDSHAKE['wall_seconds']-60


def cooperative_remaining(deadline,profile_id):
    from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE
    remaining=deadline-time.monotonic()
    if profile_id==SEGMENTED_PROFILE:
        if remaining<=0:
            from chanlun_trader.research_factory.universe_account_backend_v2 import SegmentBoundary
            raise SegmentBoundary('UNIVERSE_WORKER_COOPERATIVE_DEADLINE')
        return remaining
    return max(.01,remaining)


def _long_horizon_worker(path,name,number):
    from chanlun_trader.research_factory.universe_account_backend_v2 import SegmentBoundary
    job=read_json(path);validate_sources(job);gov=service(job);root=Path(job['root'])
    state=gov.segment_status(name);dispatch=state['pending'];prefix=segment_prefix(name,number)
    expected={'purpose':name,'segment_number':number,'dispatch_id':dispatch['dispatch_id'] if dispatch else None}
    if (dispatch is None or dispatch['segment_number'] != number or HANDSHAKE is None
            or HANDSHAKE.get('execution') != expected
            or HANDSHAKE['memory_mib'] != state['profile']['memory_mib']
            or not 0 < HANDSHAKE['wall_seconds'] <= dispatch['upper_bound_seconds']):
        raise PermissionError('JOB_LONG_HORIZON_WORKER_SCOPE_CONFLICT')
    gov.active_execution(name)
    item=job['items'][name];strategy=resolve(item['factory'])(**item.get('factory_kwargs',{}))
    backend=backend_for(strategy,item['backend_options'])
    if prepare(strategy,backend,item) != job['plans'][name]:
        raise PermissionError('WORKER_PLAN_CONFLICT')
    save(root/(prefix+'_INPUT_ACCESS.json'),{'reader_pid':os.getpid(),'reader_parent_pid':os.getppid(),
        'launcher_pid':HANDSHAKE.get('launcher_pid'),'resource_platform':os.name,
        'windows_job_verified':HANDSHAKE.get('windows_job_verified',False),'input_identity':job['input_identity'],
        'loader':item['loader'],'loader_kwargs':item['loader_kwargs'],'purpose':name,
        'segment_number':number,'dispatch_id':dispatch['dispatch_id'],
        'accessed_at':datetime.now(timezone.utc).isoformat()})
    begin=time.monotonic()
    try:
        deadline=(None if job['resources']['purpose']=='ENGINEERING_CONTINUOUS_REFERENCE'
            else cooperative_deadline(job['resources']['profile_id'],dispatch['upper_bound_seconds'],begin))
        if deadline is not None:
            cooperative_remaining(deadline,job['resources']['profile_id'])
        data=resolve(item['loader'])(**item['loader_kwargs'])
        if data['input_identity'] != job['input_identity']:
            raise PermissionError('LOADED_INPUT_IDENTITY_CONFLICT')
        backend.segment_seconds=(None if deadline is None
            else cooperative_remaining(deadline,job['resources']['profile_id']))
        first_active=True
        def active():
            nonlocal first_active
            receipt={**gov.active_execution(name),'execution_pause_requested':control_state(job)['paused']}
            # 公共 prepare 后的首次 guard 仍在引擎之前；日内 guard 不新增中断点。
            if first_active and deadline is not None:
                backend.segment_seconds=cooperative_remaining(deadline,job['resources']['profile_id'])
            first_active=False
            return receipt
        output=root/(name+'_RESULT.json')
        if output.exists():
            result=completion_tail_result(job,name,gov.segment_status(name))
            from chanlun_trader.research_factory.universe_account_inputs_v1 import _prepare_owned_universe_account_inputs_v1
            from chanlun_trader.research_factory.universe_execution_artifacts_v1 import hydrated_result
            from chanlun_trader.research_factory.universe_evidence_v1 import reconstruct_universe_account
            from chanlun_trader.research_factory.common import stable_hash
            inputs=_prepare_owned_universe_account_inputs_v1(data['frame'],backend.window,
                required_fields=strategy.requirements.fields,warmup_bars=strategy.requirements.warmup_sessions)
            checkpoint=Path(item['backend_options']['checkpoint_path'])
            audit=reconstruct_universe_account(inputs.bundle,inputs.window,hydrated_result(result),
                initial_cash=backend.initial_cash,costs=backend.costs,strategy_id=strategy.strategy_id,rule=strategy.payload,
                audit_checkpoint_path=checkpoint.parent/(strategy.strategy_id+'_AUDIT.json'),
                segment_seconds=(None if deadline is None else cooperative_remaining(deadline,job['resources']['profile_id'])))
            if result.get('reconciliation',{}).get('audit_identity')!=stable_hash(audit):
                raise PermissionError('JOB_COMPLETION_TAIL_AUDIT_CONFLICT')
        else:
            result=run(strategy,backend,frame=data['frame'],actions=data['actions'],input_identity=data['input_identity'],
                active_check=active,runtime=item)
            save(output,result)
        save(root/(prefix+'_STATUS.json'),{'state':'COMPLETED','dispatch_id':dispatch['dispatch_id'],
            'result_sha256':sha(root/(name+'_RESULT.json'))})
        return 0
    except SegmentBoundary as exc:
        checkpoint=Path(item['backend_options']['checkpoint_path'])
        value=read_json(checkpoint) if checkpoint.exists() else {}
        save(root/(prefix+'_STATUS.json'),{'state':'CONTINUE','dispatch_id':dispatch['dispatch_id'],
            'phase':getattr(exc,'phase','ACCOUNT'),'last_day':value.get('last_day'),
            'state_identity':value.get('state_identity'),'checkpoint_sha256':sha(checkpoint) if checkpoint.exists() else None,
            'reason':str(exc)})
        return 75


def completion_tail_result(job,name,state):
    """宿主回执丢失后的最终快照只核验，不重放交易日，也不改原 RESULT。"""
    from chanlun_trader.research_factory.common import stable_hash
    root=Path(job['root']);output=root/(name+'_RESULT.json');result=read_json(output)
    checkpoint=Path(job['items'][name]['backend_options']['checkpoint_path'])
    snapshot=read_json(checkpoint)
    if (snapshot.get('state_identity')!=stable_hash({k:v for k,v in snapshot.items() if k!='state_identity'})
            or snapshot.get('last_day')!=job['plans'][name]['backend']['window']['account_end']
            or result.get('input_identity')!=job['input_identity']
            or result.get('execution_description')!=job['plans'][name]['backend']
            or result.get('execution_identity')!=snapshot.get('execution_identity')
            or result.get('artifacts')!=snapshot.get('artifacts')
            or not result.get('reconciliation',{}).get('passed')):
        raise PermissionError('JOB_COMPLETION_TAIL_STATE_CONFLICT')
    for row in state['segments']:
        charge=row['charge'];dispatch=row['dispatch']
        if charge is None or charge['outcome']!='CONTINUE' or charge['basis']!='UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND':
            continue
        prefix=segment_prefix(name,dispatch['segment_number'])
        status_path=root/(prefix+'_STATUS.json');resume_path=root/(prefix+'_RESUME.json')
        status_value=read_json(status_path) if status_path.exists() else {}
        resumed=read_json(resume_path) if resume_path.exists() else {}
        if (status_value.get('state')=='COMPLETED' and status_value.get('dispatch_id')==dispatch['dispatch_id']
                and status_value.get('result_sha256')==sha(output) and resumed.get('dispatch_id')==dispatch['dispatch_id']
                and resumed.get('state_identity')==sha(checkpoint)):
            return result
    raise PermissionError('JOB_COMPLETION_TAIL_ORIGINAL_STATUS_NOT_PROVEN')


def execute_long_horizon_accounts(path,*,recover=False):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    from chanlun_trader.research_factory.universe_execution_profile_v1 import worker_wall_seconds
    from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock
    job=read_json(path);validate_sources(job);gov=service(job);root=Path(job['root'])
    if root.resolve() != Path(path).resolve().parent:
        raise PermissionError('JOB_ROOT_IDENTITY_CONFLICT')
    with ObjectiveMutationLock.for_resource(root/'LONG_EXECUTION.lock'):
        if not recover and any((root/(name+'_START.json')).exists() for name in job['plans']):
            raise PermissionError('JOB_ALREADY_ATTEMPTED_RECONCILE_NO_REPLAY')
        for name in job['plans']:
            if (root/(name+'_SETTLEMENT.json')).exists():
                validated_settlement(job,name)
                continue
            if control_state(job)['paused']:
                return {'status':'PAUSED','items':status(path)['items']}
            if not (root/(name+'_START.json')).exists():
                gov.start(name)
            state=gov.segment_status(name)
            if state['pending'] is not None:
                raise PermissionError('JOB_LONG_HORIZON_INTERRUPTED_USE_RESUME')
            if state['segments'] and state['segments'][-1]['charge']['outcome'] == 'FAILED':
                raise PermissionError('JOB_WORKER_FAILED_NO_RETRY')
            if (root/(name+'_RESULT.json')).exists():
                if state['segments'] and state['segments'][-1]['charge']['outcome']=='COMPLETED':
                    prefix=segment_prefix(name,state['segments'][-1]['dispatch']['segment_number'])
                    for suffix in ('_WORKER.json','_INPUT_ACCESS.json'):
                        save(root/(name+suffix),read_json(root/(prefix+suffix)))
                    resource=read_json(root/(prefix+'_RESOURCE.json'))
                    resource.update(elapsed_wall_seconds=state['charged_seconds'],segment_count=len(state['segments']),
                                    active_metering=True,segments=[row['charge']['charge_id'] for row in state['segments']])
                    save(root/(name+'_RESOURCE.json'),resource)
                    gov.settle(name,completed=True,seconds=state['charged_seconds'],result_hash=sha(root/(name+'_RESULT.json')))
                    continue
                completion_tail_result(job,name,state)
            while True:
                dispatch=gov.start_segment(name);number=dispatch['segment_number'];prefix=segment_prefix(name,number)
                env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1',
                    **{key:'1' for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')}}
                env['PYTHONPATH']=str(REPO/'src')+os.pathsep+str(REPO)
                env.pop('CHANLUN_TEST_ISOLATION',None)
                begin=time.monotonic()
                try:
                    resource=run_bounded_worker([sys.executable,str(Path(__file__).resolve()),'--worker',name,
                        '--job',str(Path(path).resolve()),'--segment',str(number)],root=REPO,
                        memory_mib=job['resources']['memory_mib'],wall_seconds=worker_wall_seconds(dispatch['upper_bound_seconds']),
                        measure_peak_memory=True,
                        environment=env,execution={'purpose':name,'segment_number':number,'dispatch_id':dispatch['dispatch_id']},
                        on_started=lambda pid:save(root/(prefix+'_WORKER.json'),{'pid':pid,'purpose':name,
                            'segment_number':number,'dispatch_id':dispatch['dispatch_id']}))
                except Exception as exc:
                    resource={'returncode':None,'timed_out':False,'error_type':type(exc).__name__,'error':str(exc)}
                elapsed=time.monotonic()-begin
                resource={key:value.decode('utf-8',errors='replace') if isinstance(value,bytes) else value
                          for key,value in resource.items()}
                resource.update(elapsed_wall_seconds=elapsed,segment_number=number,dispatch_id=dispatch['dispatch_id'])
                resource_path=root/(prefix+'_RESOURCE.json');save(resource_path,resource)
                status_path=root/(prefix+'_STATUS.json')
                status_value=read_json(status_path) if status_path.exists() else {}
                output=root/(name+'_RESULT.json')
                within_bound=elapsed<=dispatch['upper_bound_seconds']
                complete=(within_bound and resource.get('returncode')==0 and not resource.get('timed_out') and output.exists()
                    and status_value.get('state')=='COMPLETED' and status_value.get('dispatch_id')==dispatch['dispatch_id']
                    and status_value.get('result_sha256')==sha(output))
                continuation=(within_bound and resource.get('returncode')==75 and not resource.get('timed_out')
                    and status_value.get('state')=='CONTINUE' and status_value.get('dispatch_id')==dispatch['dispatch_id'])
                paused=continuation and control_state(job)['paused']
                outcome='COMPLETED' if complete else 'PAUSED' if paused else 'CONTINUE' if continuation else 'FAILED'
                gov.end_segment(name,number,seconds=elapsed,evidence_identity=sha(resource_path),outcome=outcome)
                state=gov.segment_status(name)
                if paused:
                    return {'status':'PAUSED','items':status(path)['items']}
                if continuation:
                    continue
                # 历史公共证据消费者得到末段访问绑定和整个用途的真实累计耗时。
                for suffix in ('_WORKER.json','_INPUT_ACCESS.json'):
                    source=root/(prefix+suffix)
                    if source.exists():save(root/(name+suffix),read_json(source))
                resource.update(elapsed_wall_seconds=state['charged_seconds'],segment_count=len(state['segments']),
                                active_metering=True,segments=[row['charge']['charge_id'] for row in state['segments']])
                save(root/(name+'_RESOURCE.json'),resource)
                gov.settle(name,completed=complete,seconds=state['charged_seconds'],result_hash=sha(output) if output.exists() else None,
                    error=None if complete else 'WORKER_FAILED_SEE_SEGMENT_RESOURCE')
                if not complete:
                    raise RuntimeError('JOB_WORKER_FAILED_NO_RETRY')
                break
        index={name:{'result':str(root/(name+'_RESULT.json')),'sha256':sha(root/(name+'_RESULT.json')),
            'settlement':str(root/(name+'_SETTLEMENT.json')),'report':str(root/(name+'_REPORT.md'))} for name in job['plans']}
        save(root/'RESULTS_INDEX.json',{'items':index,
            'worker_seconds':sum(gov.segment_status(name)['charged_seconds'] for name in job['plans']),
            'exposures':len(index),'repair_exposures':0})
        return index


def validated_settlement(job, name):
    """恢复消费累计前核对原 START、结算和资源，不接受自报已完成。"""
    import math
    from chanlun_trader.research_factory.common import stable_hash
    root=Path(job['root'])
    settled=read_json(root/(name+'_SETTLEMENT.json'));start=read_json(root/(name+'_START.json'))
    receipt=read_json(root/'CONFIRMATION.json');resource=read_json(root/(name+'_RESOURCE.json'))
    seconds=settled.get('wall_seconds')
    if 'profile_id' in job.get('resources', {}):
        state=service(job).segment_status(name)
        if state['pending'] is not None or seconds != state['charged_seconds']:
            raise PermissionError('JOB_LONG_HORIZON_RESOURCE_CHAIN_CONFLICT')
        for row in state['segments']:
            path=root/(segment_prefix(name,row['dispatch']['segment_number'])+'_RESOURCE.json')
            if row['charge']['basis']=='MEASURED_ACTIVE_WALL_SECONDS' and sha(path)!=row['charge']['evidence_identity']:
                raise PermissionError('JOB_LONG_HORIZON_SEGMENT_RESOURCE_CHANGED')
    if (receipt['receipt_id']!=stable_hash({k:v for k,v in receipt.items() if k!='receipt_id'})
            or receipt['strategy_plans']!=job['plans'] or start['receipt_id']!=receipt['receipt_id']
            or start['kind']!=name or any(settled.get(k)!=v for k,v in start.items())
            or settled.get('completed') is not True or settled.get('error') is not None
            or type(resource.get('returncode')) is not int or resource['returncode']!=0 or resource.get('timed_out')
            or type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<0
            or not (root/(name+'_RESULT.json')).exists() or sha(root/(name+'_RESULT.json'))!=settled['result_sha256']):
        raise PermissionError('JOB_FAILED_OR_SETTLEMENT_CONFLICT_NO_REPLAY')
    return settled


def long_horizon_compute_worker(path,stage,number,member=None):
    from chanlun_trader.research_factory.universe_compute_governance_v1 import UniverseComputeGovernanceV1
    job=read_json(path);folder=Path(job['root'])/('COMPUTE_'+stage);scope=read_json(folder/'SCOPE.json')
    pending=UniverseComputeGovernanceV1(folder,scope['authority'],scope['request'],stage).status()['pending']
    try:
        return _long_horizon_compute_worker(path,stage,number,member)
    except Exception as exc:
        if pending and pending['number']==number:
            record_segment_failure(folder,'SEGMENT_'+str(number).zfill(6),pending['dispatch_id'],sha(folder/'SCOPE.json'),exc)
        raise


def _long_horizon_compute_worker(path,stage,number,member=None):
    """核验和信号标签只在已授权受限评价进程中读取。"""
    from chanlun_trader.research_factory.universe_compute_governance_v1 import UniverseComputeGovernanceV1
    begin=time.monotonic()
    job=read_json(path);validate_sources(job);root=Path(job['root']);folder=root/('COMPUTE_'+stage)
    scope=read_json(folder/'SCOPE.json')
    if scope['job_sha256'] != sha(path) or scope['stage'] != stage:
        raise PermissionError('JOB_COMPUTE_SCOPE_CHANGED')
    meter=UniverseComputeGovernanceV1(folder,scope['authority'],scope['request'],stage)
    state=meter.status();pending=state['pending']
    expected={'purpose':stage,'compute_identity':meter.binding['compute_identity'],'segment_number':number,
        'dispatch_id':pending['dispatch_id'] if pending else None,'scope_sha256':sha(folder/'SCOPE.json'),'member':member}
    if (HANDSHAKE is None or pending is None or pending['number'] != number or HANDSHAKE.get('execution') != expected
            or HANDSHAKE['memory_mib'] != meter.profile['memory_mib']
            or not 0 < HANDSHAKE['wall_seconds'] <= pending['upper_bound_seconds']):
        raise PermissionError('JOB_COMPUTE_WORKER_SCOPE_CONFLICT')
    meter.active()
    save(folder/('SEGMENT_'+str(number).zfill(6)+'_ACCESS.json'),{'reader_pid':os.getpid(),
        'reader_parent_pid':os.getppid(),'launcher_pid':HANDSHAKE.get('launcher_pid'),
        'resource_platform':os.name,'windows_job_verified':HANDSHAKE.get('windows_job_verified',False),
        'compute_identity':meter.binding['compute_identity'],'stage':stage,'member':member,
        'purpose':'AUTHORIZED_POST_ACCOUNT_EVALUATION','input_identity':job['input_identity'],
        'observation_plan':job['observation_plan'],'accessed_at':datetime.now(timezone.utc).isoformat()})
    profile_id=meter.profile['profile_id']
    deadline=cooperative_deadline(profile_id,pending['upper_bound_seconds'],begin)
    if stage=='VERIFICATION':
        from chanlun_trader.research_factory.research_evidence_v1 import verify_job_evidence
        from chanlun_trader.research_factory.universe_account_backend_v2 import SegmentBoundary
        checks={};verified_one=False
        for name in job['plans']:
            verified=folder/('VERIFIED_'+name+'.json')
            if verified.exists():
                row=read_json(verified)
                if row['job_sha256'] != sha(path) or row.get('source_result_sha256') != sha(root/(name+'_RESULT.json')):
                    raise PermissionError('JOB_VERIFICATION_PROGRESS_CHANGED')
                checks[name]=row['verification'];continue
            # 每段至多新增一个成员核验；第二次严格冷加载须留给下一受限进程。
            if verified_one and profile_id=='LONG_HORIZON_SEGMENTED_V1':
                save(folder/('SEGMENT_'+str(number).zfill(6)+'_STATUS.json'),{'state':'CONTINUE',
                    'dispatch_id':pending['dispatch_id'],'phase':'INDEPENDENT_VERIFICATION'})
                return 75
            try:
                options={'segment_seconds':cooperative_remaining(deadline,profile_id)}
                from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE
                if profile_id==SEGMENTED_PROFILE:
                    options['segment_deadline']=deadline
                checks[name]=verify_job_evidence(path,name=name,**options)
            except SegmentBoundary:
                save(folder/('SEGMENT_'+str(number).zfill(6)+'_STATUS.json'),{'state':'CONTINUE',
                    'dispatch_id':pending['dispatch_id'],'phase':'INDEPENDENT_VERIFICATION'})
                return 75
            save(verified,{'job_sha256':sha(path),'source_result_sha256':sha(root/(name+'_RESULT.json')),
                           'verification':checks[name]})
            verified_one=True
        value={'job_sha256':sha(path),'items':checks,'advance_allowed':all(row.get('advance_allowed') is True for row in checks.values())}
        save(folder/'RESULT.json',value)
        save(folder/('SEGMENT_'+str(number).zfill(6)+'_STATUS.json'),{'state':'COMPLETED',
            'dispatch_id':pending['dispatch_id'],'member':member,'result_sha256':sha(folder/'RESULT.json')})
        return 0
    if stage!='REPORT' or member not in job['plans']:
        raise PermissionError('JOB_COMPUTE_REPORT_MEMBER_INVALID')
    verification=read_json(root/'VERIFICATION.json')
    if verification['job_sha256'] != sha(path) or not verification['advance_allowed']:
        raise PermissionError('JOB_REPORT_RECONCILIATION_REQUIRED')
    from chanlun_trader.research_factory.universe_account_inputs_v1 import _prepare_owned_universe_account_inputs_v1
    from chanlun_trader.research_factory.universe_execution_artifacts_v1 import hydrated_result
    from chanlun_trader.research_factory.universe_research_report_v2 import build_research_reports
    from chanlun_trader.research_factory.universe_signal_funnel_v1 import build_signal_funnel_stream_v1,funnel_day_packets_v1
    from chanlun_trader.research_factory.universe_account_backend_v2 import SegmentBoundary
    try:
        cooperative_remaining(deadline,profile_id)
        item=job['items'][member]; data=resolve(item['loader'])(**item['loader_kwargs'])
        strategy=resolve(item['factory'])(**item['factory_kwargs'])
        inputs=_prepare_owned_universe_account_inputs_v1(data['frame'],item['backend_options']['window'],
            required_fields=strategy.requirements.fields,warmup_bars=strategy.requirements.warmup_sessions)
        source=root/(member+'_RESULT.json');raw=read_json(source);result=hydrated_result(raw)
        if verification['items'][member]['result_sha256'] != sha(source):
            raise PermissionError('JOB_REPORT_VERIFIED_RESULT_CHANGED')
        dual=build_research_reports(result,inputs,job['observation_plan'],
            report_checkpoint_path=folder/(member+'_OBSERVATION_CHECKPOINT.json'),
            segment_seconds=cooperative_remaining(deadline,profile_id))
        calendar=inputs.window['calendar'];first=calendar.index(inputs.window['account_start'])
        funnel=build_signal_funnel_stream_v1(rule_identity=strategy.rule_identity,strategy_id=member,calendar=calendar,
            day_packets=funnel_day_packets_v1(raw,calendar),
            report_checkpoint_path=folder/(member+'_FUNNEL_CHECKPOINT.json'),
            segment_seconds=cooperative_remaining(deadline,profile_id),
            expected_decision_sessions=calendar[first:])
    except SegmentBoundary:
        save(folder/('SEGMENT_'+str(number).zfill(6)+'_STATUS.json'),{'state':'CONTINUE',
            'dispatch_id':pending['dispatch_id'],'phase':'REPORT','member':member})
        return 75
    meter.active();save(root/(member+'_RESEARCH_REPORT.json'),dual);save(root/(member+'_SIGNAL_FUNNEL.json'),funnel)
    save(root/(member+'_RESEARCH_DETAILS.json'),{'job_sha256':sha(path),'result_sha256':sha(source),
        'observation_plan':job['observation_plan'],'signal_details':dual['details_manifest'],
        'funnel_details':funnel['details_manifest'],'report_identity':dual['report_identity'],
        'funnel_identity':funnel['identity'],'compute_identity':meter.binding['compute_identity']})
    value={'member':member,'research_report':str(root/(member+'_RESEARCH_REPORT.json')),
        'research_report_sha256':sha(root/(member+'_RESEARCH_REPORT.json')),'funnel':str(root/(member+'_SIGNAL_FUNNEL.json')),
        'funnel_sha256':sha(root/(member+'_SIGNAL_FUNNEL.json'))}
    output_path=folder/('RESULT_'+member+'.json');save(output_path,value)
    if all((root/(name+'_RESEARCH_REPORT.json')).exists() for name in job['plans']):
        try:
            cooperative_remaining(deadline,profile_id)
            report_account_job(path,_universe_inputs=inputs,_deadline=deadline)
            cooperative_remaining(deadline,profile_id)
        except SegmentBoundary:
            save(folder/('SEGMENT_'+str(number).zfill(6)+'_STATUS.json'),{'state':'CONTINUE',
                'dispatch_id':pending['dispatch_id'],'phase':'REPORT_RENDER','member':member})
            return 75
    save(folder/('SEGMENT_'+str(number).zfill(6)+'_STATUS.json'),{'state':'COMPLETED',
        'dispatch_id':pending['dispatch_id'],'member':member,'result_sha256':sha(output_path)})
    return 0


def proven_compute_result(folder,member,state):
    """只有成功受限进程及累计资源链才证明缓存完成，文件存在本身不能代替。"""
    output=folder/('RESULT_'+member+'.json' if member is not None else 'RESULT.json')
    if not output.exists():return False
    for row in state['segments']:
        charge=row['charge']
        if charge is None or charge['basis']!='MEASURED_ACTIVE_WALL_SECONDS' or charge['outcome']=='FAILED':continue
        prefix='SEGMENT_'+str(row['dispatch']['number']).zfill(6)
        resource_path=folder/(prefix+'_RESOURCE.json');record_path=folder/(prefix+'_STATUS.json')
        if not record_path.exists():continue
        record=read_json(record_path);resource=read_json(resource_path)
        if (record.get('state')=='COMPLETED' and record.get('member')==member
                and record.get('dispatch_id')==row['dispatch']['dispatch_id']
                and record.get('result_sha256')==sha(output) and sha(resource_path)==charge['evidence_identity']
                and resource.get('returncode')==0 and not resource.get('timed_out')):
            return True
    return False


def run_long_horizon_compute(path,authority,request,stage):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    from chanlun_trader.research_factory.universe_execution_profile_v1 import worker_wall_seconds
    from chanlun_trader.research_factory.universe_compute_governance_v1 import UniverseComputeGovernanceV1
    from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock
    job=read_json(path);validate_sources(job);root=Path(job['root']);folder=root/('COMPUTE_'+stage)
    if stage not in ('VERIFICATION','REPORT') or 'profile_id' not in job.get('resources', {}):
        raise PermissionError('JOB_LONG_HORIZON_COMPUTE_REQUIRED')
    scope={'job_sha256':sha(path),'authority':authority,'request':request,'stage':stage}
    with ObjectiveMutationLock.for_resource(folder/'EXECUTE.lock'):
        save(folder/'SCOPE.json',scope)
        meter=UniverseComputeGovernanceV1(folder,authority,request,stage)
        if (folder/'COMPUTE_START.json').exists():
            previous=meter.status()
            if previous['pending'] is not None:
                raise PermissionError('JOB_COMPUTE_INTERRUPTED_RECONCILIATION_REQUIRED')
            if previous['segments'] and previous['segments'][-1]['charge']['outcome']=='COMPLETED':
                expected_members=list(job['plans']) if stage=='REPORT' else [None]
                if any(not proven_compute_result(folder,name,previous) for name in expected_members):
                    raise PermissionError('JOB_COMPUTE_COMPLETED_RESULT_NOT_PROVEN')
                if stage=='VERIFICATION':return read_json(folder/'RESULT.json')
                return {name:read_json(folder/('RESULT_'+name+'.json')) for name in job['plans']}
            if previous['segments'] and previous['segments'][-1]['charge']['outcome']=='FAILED':
                raise PermissionError('JOB_COMPUTE_FAILED_NO_AUTOMATIC_RETRY')
        meter.start();members=list(job['plans']) if stage=='REPORT' else [None]
        outputs={}
        for index,member in enumerate(members):
            output_path=folder/('RESULT_'+member+'.json' if member is not None else 'RESULT.json')
            if proven_compute_result(folder,member,meter.status()):
                outputs[member]=read_json(output_path)
                continue
            while True:
                if control_state(job)['paused']:
                    return {'status':'PAUSED','stage':stage,'charged_seconds':meter.status()['charged_seconds']}
                dispatch=meter.dispatch();number=dispatch['number'];prefix='SEGMENT_'+str(number).zfill(6)
                env={**os.environ,'PYTHONPATH':str(REPO/'src')+os.pathsep+str(REPO),'PYTHONDONTWRITEBYTECODE':'1',
                    **{name:'1' for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')}}
                env.pop('CHANLUN_TEST_ISOLATION',None);begin=time.monotonic()
                command=[sys.executable,str(Path(__file__).resolve()),'--compute',stage,'--job',str(Path(path).resolve()),'--segment',str(number)]
                if member is not None:command.extend(['--member',member])
                try:
                    resource=run_bounded_worker(command,root=REPO,memory_mib=meter.profile['memory_mib'],measure_peak_memory=True,
                        wall_seconds=worker_wall_seconds(dispatch['upper_bound_seconds']),environment=env,
                        execution={'purpose':stage,'compute_identity':meter.binding['compute_identity'],'segment_number':number,
                            'dispatch_id':dispatch['dispatch_id'],'scope_sha256':sha(folder/'SCOPE.json'),'member':member},
                        on_started=lambda pid:save(folder/(prefix+'_WORKER.json'),{'pid':pid,'dispatch_id':dispatch['dispatch_id']}))
                except Exception as exc:
                    resource={'returncode':None,'timed_out':False,'error':str(exc)}
                elapsed=time.monotonic()-begin
                resource={k:v.decode('utf-8',errors='replace') if isinstance(v,bytes) else v for k,v in resource.items()}
                resource.update(elapsed_wall_seconds=elapsed,dispatch_id=dispatch['dispatch_id'],member=member)
                resource_path=folder/(prefix+'_RESOURCE.json');save(resource_path,resource)
                output_path=folder/('RESULT_'+member+'.json' if member is not None else 'RESULT.json')
                status_path=folder/(prefix+'_STATUS.json');recorded=read_json(status_path) if status_path.exists() else {}
                within_bound=elapsed<=dispatch['upper_bound_seconds']
                complete=(within_bound and resource.get('returncode')==0 and not resource.get('timed_out') and output_path.exists()
                    and recorded.get('state')=='COMPLETED' and recorded.get('dispatch_id')==dispatch['dispatch_id']
                    and recorded.get('member')==member and recorded.get('result_sha256')==sha(output_path))
                continuation=within_bound and resource.get('returncode')==75 and not resource.get('timed_out')
                continuation=continuation and recorded.get('dispatch_id')==dispatch['dispatch_id'] and recorded.get('state')=='CONTINUE'
                meter.charge(number,seconds=elapsed,evidence_identity=sha(resource_path),
                    outcome='COMPLETED' if complete and index==len(members)-1 else 'CONTINUE' if complete or continuation else 'FAILED')
                if continuation:continue
                if not complete:
                    raise RuntimeError('JOB_COMPUTE_WORKER_FAILED_NO_RETRY:'+stage)
                outputs[member]=read_json(output_path);break
        save(folder/'RESOURCE_TOTAL.json',{'charged_seconds':meter.status()['charged_seconds'],
            'profile':meter.profile,'compute_identity':meter.binding['compute_identity']})
        return outputs[None] if stage=='VERIFICATION' else outputs


def reconcile_long_horizon_compute(path,stage):
    """明确恢复时先证明旧进程已退出；同一用途保守结算未知段。"""
    from chanlun_trader.research_factory.universe_compute_governance_v1 import UniverseComputeGovernanceV1
    from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock
    from chanlun_trader.research_daemon_state import DaemonInstanceLockV1
    job=read_json(path);validate_sources(job);folder=Path(job['root'])/('COMPUTE_'+stage)
    if not (folder/'COMPUTE_START.json').exists():return
    scope=read_json(folder/'SCOPE.json')
    if scope['job_sha256']!=sha(path) or scope['stage']!=stage:
        raise PermissionError('JOB_COMPUTE_SCOPE_CHANGED')
    meter=UniverseComputeGovernanceV1(folder,scope['authority'],scope['request'],stage)
    with ObjectiveMutationLock.for_resource(folder/'EXECUTE.lock'):
        meter.reconcile_segment_mirrors()
        state=meter.status();pending=state['pending']
        if pending is None:return
        number=pending['number'];prefix='SEGMENT_'+str(number).zfill(6)
        worker=folder/(prefix+'_WORKER.json');access=folder/(prefix+'_ACCESS.json')
        if not worker.exists():
            proof_path=folder/('COMPUTE_'+prefix+'_MIRROR_REPAIR.json')
            proof=read_json(proof_path) if proof_path.exists() else {}
            if proof.get('worker_absent') is True and proof.get('dispatch_id')==pending['dispatch_id']:
                meter.charge(number,outcome='CONTINUE')
                save(folder/(prefix+'_RESUME.json'),{'dispatch_id':pending['dispatch_id'],
                    'charge_basis':'UNKNOWN_UPPER_BOUND','mirror_repair_sha256':sha(proof_path),
                    'budget_reused':True,'execution_not_launched':True})
                return
            raise PermissionError('JOB_COMPUTE_WORKER_START_UNKNOWN')
        pids=[read_json(worker)['pid']]
        if access.exists():pids.append(read_json(access)['reader_pid'])
        if any(DaemonInstanceLockV1._pid_alive(int(pid)) for pid in pids):
            raise PermissionError('JOB_COMPUTE_WORKER_STILL_ACTIVE')
        resource_path=folder/(prefix+'_RESOURCE.json');seconds=None;evidence=None;outcome='CONTINUE'
        failure_path=folder/(prefix+'_FAILURE.json')
        if failure_path.exists():
            failure=read_json(failure_path)
            if failure.get('dispatch_id')!=pending['dispatch_id'] or failure.get('scope_identity')!=sha(folder/'SCOPE.json'):
                raise PermissionError('JOB_COMPUTE_FAILURE_DISPATCH_CONFLICT')
            outcome='FAILED';evidence=sha(failure_path)
        if resource_path.exists():
            resource=read_json(resource_path)
            if resource['dispatch_id']!=pending['dispatch_id']:
                raise PermissionError('JOB_COMPUTE_RESOURCE_DISPATCH_CONFLICT')
            seconds=resource['elapsed_wall_seconds'];evidence=sha(resource_path)
            recorded_path=folder/(prefix+'_STATUS.json');recorded=read_json(recorded_path) if recorded_path.exists() else {}
            member=resource.get('member');output=folder/('RESULT_'+member+'.json' if member else 'RESULT.json')
            if (outcome!='FAILED' and seconds<=pending['upper_bound_seconds']
                    and resource.get('returncode')==0 and not resource.get('timed_out') and output.exists()
                    and recorded.get('dispatch_id')==pending['dispatch_id'] and recorded.get('state')=='COMPLETED'
                    and recorded.get('member')==member and recorded.get('result_sha256')==sha(output)):
                complete=(folder/'RESULT.json').exists() if stage=='VERIFICATION' else all(
                    (folder/('RESULT_'+name+'.json')).exists() for name in job['plans'])
                outcome='COMPLETED' if complete else 'CONTINUE'
            elif not (outcome!='FAILED' and seconds<=pending['upper_bound_seconds'] and resource.get('returncode')==75
                      and not resource.get('timed_out') and recorded.get('state')=='CONTINUE'
                      and recorded.get('dispatch_id')==pending['dispatch_id']):
                outcome='FAILED'
        meter.charge(number,seconds=seconds,evidence_identity=evidence,outcome=outcome)
        save(folder/(prefix+'_RESUME.json'),{'dispatch_id':pending['dispatch_id'],'outcome':outcome,
            'budget_reused':True,'charge_basis':'MEASURED' if seconds is not None else 'UNKNOWN_UPPER_BOUND'})
        if outcome=='FAILED':raise PermissionError('JOB_COMPUTE_KNOWN_FAILURE_NO_RETRY')


def reconcile_account(path, name):
    """只结算已退出且有成功资源回执的原 worker，不重新调用 worker。"""
    import math
    from chanlun_trader.research_daemon_state import DaemonInstanceLockV1
    job=read_json(path);validate_sources(job);root=Path(job['root'])
    if name not in job['plans'] or root.resolve()!=Path(path).resolve().parent:
        raise PermissionError('JOB_RECONCILE_SCOPE_CONFLICT')
    required=[root/(name+suffix) for suffix in ('_START.json','_WORKER.json','_RESOURCE.json','_RESULT.json','_INPUT_ACCESS.json')]
    if not all(p.is_file() and p.resolve()==p for p in required):
        raise PermissionError('JOB_UNKNOWN_WORKER_NO_AUTOMATIC_RETRY')
    start,worker_record,resource=map(read_json,required[:3])
    access=read_json(required[4]);item=job['items'][name]
    direct = worker_record.get('pid')==access.get('reader_pid')
    windows_child = (resource.get('resource_platform')=='nt' and access.get('resource_platform')=='nt'
        and resource.get('windows_job_bound') is True and access.get('windows_job_verified') is True
        and worker_record.get('pid')==access.get('reader_parent_pid')==access.get('launcher_pid')==resource.get('launcher_pid'))
    if (not (direct or windows_child) or access.get('purpose')!=name
            or access.get('input_identity')!=job['input_identity'] or access.get('loader')!=item['loader']
            or access.get('loader_kwargs')!=item['loader_kwargs']):
        raise PermissionError('JOB_RECONCILE_INPUT_ACCESS_CONFLICT')
    receipt=read_json(root/'CONFIRMATION.json')
    from chanlun_trader.research_factory.common import stable_hash
    if (receipt['receipt_id']!=stable_hash({k:v for k,v in receipt.items() if k!='receipt_id'})
            or receipt['strategy_plans']!=job['plans'] or start['receipt_id']!=receipt['receipt_id']
            or start['kind']!=name or worker_record['purpose']!=name):
        raise PermissionError('JOB_RECONCILE_BINDING_CONFLICT')
    if DaemonInstanceLockV1._pid_alive(int(worker_record['pid'])):
        raise PermissionError('JOB_WORKER_STILL_ACTIVE')
    seconds=resource.get('elapsed_wall_seconds')
    if (type(resource.get('returncode')) is not int or resource.get('returncode')!=0 or resource.get('timed_out') or type(seconds) not in (int,float)
            or not math.isfinite(seconds) or seconds<0):
        raise PermissionError('JOB_WORKER_COMPLETION_NOT_PROVEN_NO_RETRY')
    return service(job).settle(name,completed=True,seconds=seconds,result_hash=sha(required[3]),error=None)


def report_account_job(path,*,_universe_inputs=None,_deadline=None):
    job=read_json(path);validate_sources(job)
    index=read_json(Path(job['root'])/'RESULTS_INDEX.json')['items']
    write_reports(job,index,_universe_inputs=_universe_inputs,_deadline=_deadline)
    return index


def execute(path):
    """兼容原 CLI：初次执行账户后立即出报告；恢复由分阶段宿主管理。"""
    execute_accounts(path)
    return report_account_job(path)


def write_reports(job,index,*,_universe_inputs=None,_deadline=None):
    from copy import deepcopy
    from chanlun_trader.research_factory.strategy_report_v1 import render_markdown
    root=Path(job['root']);results={name:read_json(item['result']) for name,item in index.items()}
    def dates(result):
        if 'daily' in result:return [str(row['date']) for row in result['daily']]
        if 'daily_accounts' in result:return [str(row['date']).replace('-', '') for row in result['daily_accounts']]
        return [str(row['timestamp'])[:10].replace('-','') for row in result['daily_account']]
    control=results.get(job.get('benchmark_id'))
    universe_reference = None
    if job.get('benchmark_mode') == 'CASH_AND_PRICE_REFERENCE':
        from chanlun_trader.research_factory.universe_benchmark_v1 import (
            _universe_price_reference_from_inputs,universe_price_reference)
        item = next(iter(job['items'].values()))
        options = item['backend_options']
        if _universe_inputs is None:
            data = resolve(item['loader'])(**item['loader_kwargs'])
            universe_reference = universe_price_reference(data['frame'], options['window'], initial_cash=options['initial_cash'])
        else:
            from chanlun_trader.research_factory.universe_account_inputs_v1 import (
                UniverseAccountInputsV1,normalized_universe_window_v1)
            from chanlun_trader.research_factory.universe_execution_profile_v1 import validate_execution_profile
            if (type(_universe_inputs) is not UniverseAccountInputsV1 or _universe_inputs.stage!='ACCOUNT'
                    or 'profile_id' not in job.get('resources',{})
                    or _universe_inputs.input_identity!=job['input_identity']
                    or any(normalized_universe_window_v1(row['backend_options']['window'])!=_universe_inputs.window
                           or row['backend_options']['initial_cash']!=options['initial_cash']
                           for row in job['items'].values())):
                raise PermissionError('REPORT_PREPARED_INPUT_SCOPE_CONFLICT')
            validate_execution_profile(job['resources'])
            _universe_inputs.assert_unchanged()
            universe_reference = _universe_price_reference_from_inputs(_universe_inputs,
                initial_cash=options['initial_cash'],deadline=_deadline)
    for name,result in results.items():
        if sha(index[name]['result'])!=index[name]['sha256'] or read_json(index[name]['settlement'])['result_sha256']!=index[name]['sha256']:
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
        if universe_reference is not None:
            report['benchmark'] = universe_reference
            report['limitations'].extend(universe_reference['limitations'])
        if job.get('items', {}).get(name, {}).get('loader') == 'chanlun_trader.research_factory.strategy_submission_v1:load_frozen_qualified_bundle':
            scope_path = Path(job['items'][name]['loader_kwargs']['path']).parent / 'QUALIFICATION_SCOPE.json'
            scope = read_json(scope_path)
            report['limitations'].extend([
                f"本次检查登记全池{len(scope['target_symbols'])}只，合格执行{len(scope['qualified_symbols'])}只，排除{len(scope['excluded'])}只。",
                '这是按整个区间资料可用性回顾确定的合格范围账户结果，不是完整市场账户结果，也不证明历史可投资范围完整。',
                f"[完整范围和逐股排除证据]({scope_path.as_posix()})；[中文排除清单]({(scope_path.parent / 'EXCLUSIONS.csv').as_posix()})。"])
        if 'profile_id' in job.get('resources', {}):
            dual_path=root/(name+'_RESEARCH_REPORT.json');funnel_path=root/(name+'_SIGNAL_FUNNEL.json')
            dual=read_json(dual_path);funnel=read_json(funnel_path)
            report['research']={'account_and_signal':dual,'funnel':funnel,'source_result_sha256':index[name]['sha256']}
            report['limitations'].extend([
                f"[实际账户与全信号两份报告]({dual_path.as_posix()})；[信号到成交原因]({funnel_path.as_posix()})。",
                '信号观察不按实际成交筛选，理论单股权益不含费用和税；它与真实账户盈利是两个问题。',
                f"登记规格{job['resources']['profile_id']}，{job['resources']['account_sessions']}个账户交易日包含空仓日。",
                '新评分与长期运行仍是研究能力；没有自动取得正式方法适用、统计资格或Paper资格。'])
        save(root/(name+'_REPORT.json'),report)
        markdown=render_markdown(report);target=root/(name+'_REPORT.md')
        if target.exists():
            if target.read_text(encoding='utf-8')!=markdown:raise PermissionError('REPORT_EXISTING_CONTENT_CONFLICT')
        else:
            with target.open('x',encoding='utf-8') as stream:stream.write(markdown)
        index[name]['report']=str(root/(name+'_REPORT.md'))
    if (root/'REPORT_ACCESS.json').exists():
        previous=read_json(root/'REPORT_ACCESS.json')
        if previous.get('result_hashes')!={name:item['sha256'] for name,item in index.items()}:
            raise PermissionError('REPORT_ACCESS_IDENTITY_CONFLICT')
        return
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
        if 'profile_id' in job.get('resources', {}):
            from chanlun_trader.research_daemon_state import DaemonInstanceLockV1
            profile=job['resources'];value=states[name]
            value.update(account_sessions=profile['account_sessions'],profile_id=profile['profile_id'],
                charged_seconds=0,remaining_seconds=profile['total_seconds'],processed_sessions=0,last_day=None,phase='NOT_STARTED')
            if started.exists():
                state=service(job).segment_status(name)
                value.update(charged_seconds=state['charged_seconds'],remaining_seconds=state['remaining_seconds'],
                    resource_overrun=state['resource_overrun'],dispatch_overrun=state['dispatch_overrun'])
                if state['segments']:
                    last_status=root/(segment_prefix(name,state['segments'][-1]['dispatch']['segment_number'])+'_STATUS.json')
                    if last_status.exists():value['phase']=read_json(last_status).get('phase','ACCOUNT')
                checkpoint=Path(job['items'][name]['backend_options']['checkpoint_path'])
                if checkpoint.exists():
                    snapshot=read_json(checkpoint);last=snapshot.get('last_day')
                    calendar=job['plans'][name]['backend']['window']['calendar']
                    start=job['plans'][name]['backend']['window']['account_start']
                    value.update(last_day=last,processed_sessions=sum(start<=d<=last for d in calendar) if last else 0)
                if not settled.exists():
                    pending=state['pending']
                    if pending:
                        worker_path=root/(segment_prefix(name,pending['segment_number'])+'_WORKER.json')
                        alive=worker_path.exists() and DaemonInstanceLockV1._pid_alive(int(read_json(worker_path)['pid']))
                        value['state']='RUNNING' if alive else 'RESUME_REQUIRED'
                    else:
                        value['state']='FAILED' if state['segments'] and state['segments'][-1]['charge']['outcome']=='FAILED' else\
                            'PAUSED' if control_state(job)['paused'] else 'READY_TO_CONTINUE'
    complete=sum(v['state']=='COMPLETED' for v in states.values())
    value={'completed':complete,'total':len(states),'progress_percent':100*complete/len(states),
           'report_index_exists':(root/'RESULTS_INDEX.json').exists(),'items':states}
    if 'profile_id' in job.get('resources', {}):
        from chanlun_trader.research_factory.universe_compute_governance_v1 import UniverseComputeGovernanceV1
        from chanlun_trader.research_daemon_state import DaemonInstanceLockV1
        compute={}
        for stage in ('VERIFICATION','REPORT'):
            folder=root/('COMPUTE_'+stage);row={'state':'NOT_STARTED','charged_seconds':0}
            if (folder/'COMPUTE_START.json').exists():
                scope=read_json(folder/'SCOPE.json')
                if scope['job_sha256']!=sha(path):raise PermissionError('JOB_COMPUTE_SCOPE_CHANGED')
                meter=UniverseComputeGovernanceV1(folder,scope['authority'],scope['request'],stage)
                state=meter.status();last=state['segments'][-1] if state['segments'] else None
                row.update(charged_seconds=state['charged_seconds'],profile=meter.profile,
                    remaining_seconds=state['remaining_seconds'],resource_overrun=state['resource_overrun'],
                    dispatch_overrun=state['dispatch_overrun'])
                if state['pending']:
                    worker=folder/('SEGMENT_'+str(state['pending']['number']).zfill(6)+'_WORKER.json')
                    alive=worker.exists() and DaemonInstanceLockV1._pid_alive(int(read_json(worker)['pid']))
                    row['state']='RUNNING' if alive else 'RESUME_REQUIRED'
                else:row['state']=last['charge']['outcome'] if last else 'READY_TO_CONTINUE'
            compute[stage]=row
        value.update(compute_stages=compute,paused=control_state(job)['paused'],execution_profile=job['resources'],
            stage='ACCOUNT' if complete<len(states) else 'COMPLETED' if compute['REPORT']['state']=='COMPLETED'
                else 'REPORT' if compute['VERIFICATION']['state']=='COMPLETED' else 'VERIFICATION')
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare');group.add_argument('--execute',action='store_true');group.add_argument('--worker');group.add_argument('--status',action='store_true')
    group.add_argument('--capabilities', action='store_true');group.add_argument('--compute',choices=('VERIFICATION','REPORT'))
    parser.add_argument('--root');parser.add_argument('--job');parser.add_argument('--segment',type=int);parser.add_argument('--member');args=parser.parse_args()
    if args.capabilities:
        from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
        print(json.dumps(capabilities(), ensure_ascii=False))
    elif args.prepare:
        if not args.root:parser.error('--root required')
        freeze_config(read_json(args.prepare),args.root)
    elif args.worker:raise SystemExit(worker(args.job,args.worker,args.segment))
    elif args.compute:raise SystemExit(long_horizon_compute_worker(args.job,args.compute,args.segment,args.member))
    elif args.status:print(json.dumps(status(args.job),ensure_ascii=False))
    else:print(json.dumps(execute(args.job),ensure_ascii=False))
