"""声明式公共提交：固定本地工厂及冻结输入，执行仍受原治理批准约束。"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

import pandas as pd

from .common import stable_hash
from .exploration_governance import immutable, read_json
from .research_data_provider_v1 import day
from .research_rule_strategy_v3 import ResearchRuleStrategyV3
from .research_capabilities_v1 import capabilities
from ..research.guard import ResearchDataAccessGuard

MODULE = 'chanlun_trader.research_factory.strategy_submission_v1'
REQUEST_FIELDS = {'strategy_id','rule','dataset_id','feature_start','account_start','account_end',
                  'symbols','initial_cash','max_positions','max_symbol_exposure_bps','costs',
                  'benchmark','purpose','authorization_ref'}


def public_rule_factory(payload, strategy_id):
    return ResearchRuleStrategyV3(payload, strategy_id=strategy_id)


def public_benchmark_factory(payload, strategy_id):
    from .research_benchmark_v1 import FullPoolBuyHoldStrategyV1
    return FullPoolBuyHoldStrategyV1(payload, strategy_id=strategy_id)


def frozen_frame_schemas(bundle):
    schemas = {}
    for key in ('daily','turn','states'):
        frame = bundle[key]
        schema = {'columns': list(frame.columns), 'dtypes': {name:str(dtype) for name,dtype in frame.dtypes.items()},
            'index': frame.index.tolist(), 'index_name': frame.index.name,
            'index_dtype': str(frame.index.dtype), 'index_class': type(frame.index).__name__}
        if isinstance(frame.index,pd.RangeIndex):
            schema['range'] = {'start':frame.index.start,'stop':frame.index.stop,'step':frame.index.step}
        schemas[key] = schema
    return schemas


def _restore_frame(records, schema):
    required = {'columns','dtypes','index','index_name','index_dtype','index_class'}
    if not isinstance(schema,dict) or set(schema) - {'range'} != required:
        raise ValueError('SUBMISSION_FRAME_SCHEMA_REQUIRED')
    columns,dtypes,index = schema['columns'],schema['dtypes'],schema['index']
    allowed = {'object','str','string','bool','int64','int32','uint64','uint32','float64','float32'}
    if (not isinstance(columns,list) or any(not isinstance(c,str) for c in columns)
            or len(set(columns)) != len(columns) or set(dtypes) != set(columns)
            or any(dtype not in allowed for dtype in dtypes.values())
            or schema['index_dtype'] not in {'int64','int32','uint64','uint32'}
            or schema['index_class'] not in {'RangeIndex','Index','Int64Index','UInt64Index'}
            or not isinstance(index,list) or len(index)!=len(records)
            or any(type(i) is not int for i in index)
            or any(not isinstance(row,dict) or set(row)!=set(columns) for row in records)):
        raise ValueError('SUBMISSION_FRAME_SCHEMA_UNSUPPORTED')
    frame = pd.DataFrame(records,columns=columns).astype(dtypes)
    if schema['index_class']=='RangeIndex':
        bounds = schema.get('range')
        if not isinstance(bounds,dict) or set(bounds)!={'start','stop','step'} or any(type(v) is not int for v in bounds.values()):
            raise ValueError('SUBMISSION_FRAME_INDEX_INVALID')
        restored = pd.RangeIndex(**bounds,name=schema['index_name'])
        if restored.tolist()!=index:
            raise ValueError('SUBMISSION_FRAME_INDEX_CONFLICT')
        frame.index = restored
    else:
        frame.index = pd.Index(index,dtype=schema['index_dtype'],name=schema['index_name'])
    return frame


def load_frozen_bundle(path, sha256, input_identity):
    """仅由公共服务生成的受冻结计划绑定参数调用；不重新查询供应商。"""
    source = Path(path).absolute()
    if source.resolve() != source or not source.is_file():
        raise ValueError('SUBMISSION_SNAPSHOT_REDIRECTED')
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError('SUBMISSION_SNAPSHOT_CHANGED')
    value = json.loads(raw)
    if value.get('snapshot_version') == 'UNIVERSE_FROZEN_INPUT_V1':
        from .universe_submission_v1 import restore_universe_bundle
        if value['input_identity'] != input_identity:
            raise ValueError('SUBMISSION_INPUT_IDENTITY_CONFLICT')
        return restore_universe_bundle(value, source)
    bundle = value['bundle']
    for key in ('daily','turn','states'):
        bundle[key] = _restore_frame(bundle[key], value.get('frame_schemas', {}).get(key))
    from .rule_account_backend_v2 import rule_input_identity
    if value['input_identity'] != input_identity or rule_input_identity(bundle, value['window']) != input_identity:
        raise ValueError('SUBMISSION_INPUT_IDENTITY_CONFLICT')
    return {'frame': bundle, 'actions': bundle['events'], 'input_identity': input_identity}


def load_frozen_qualified_bundle(path, sha256, input_identity, parent_path, parent_sha256):
    """从计划绑定的完整父输入重算资格，不能用自报排除名单绕过全池检查。"""
    from .universe_submission_v1 import restore_universe_bundle
    from .universe_qualified_scope_v1 import qualify_universe_bundle
    source, parent = Path(path).absolute(), Path(parent_path).absolute()
    if (source.resolve() != source or parent.resolve() != parent
            or parent != source.parent / 'PARENT' / 'INPUT.json'):
        raise ValueError('UNIVERSE_QUALIFIED_PARENT_PATH_CONFLICT')
    raw, parent_raw = source.read_bytes(), parent.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256 or hashlib.sha256(parent_raw).hexdigest() != parent_sha256:
        raise ValueError('SUBMISSION_SNAPSHOT_CHANGED')
    snapshot, parent_snapshot = json.loads(raw), json.loads(parent_raw)
    restored = restore_universe_bundle(parent_snapshot, parent)
    parent_prepared = {**parent_snapshot, 'bundle': restored['frame']}
    receipt = snapshot['bundle'].get('qualified_scope', {})
    derived = qualify_universe_bundle(parent_prepared, required_fields=receipt.get('required_fields', ()),
                                      warmup_bars=receipt.get('warmup_bars', 0))
    if (not derived['ready'] or derived['input_identity'] != input_identity
            or snapshot['input_identity'] != input_identity or derived['scope_receipt'] != receipt
            or derived['window'] != snapshot['window']):
        raise ValueError('UNIVERSE_QUALIFIED_DERIVATION_CONFLICT')
    # 先释放父表和派生检查视图，避免同时持有两份全市场表。
    del restored, parent_prepared, derived
    import gc
    gc.collect()
    return restore_universe_bundle(snapshot, source)


class StrategySubmissionV1:
    """resolver 引用现有批准，返回 objective_id/budget_path 及 provider 授权。

    它不能由请求指定可执行模块。freeze 不创建批准；原治理批准完成后才能 start。
    """
    def __init__(self, provider, authorization_resolver, output_root, capabilities_snapshot=None):
        self.provider = provider
        if not callable(authorization_resolver):
            raise ValueError('SUBMISSION_AUTHORITY_RESOLVER_REQUIRED')
        self.authority = authorization_resolver
        self.root = Path(output_root).absolute()
        if self.root.resolve() != self.root:
            raise ValueError('SUBMISSION_ROOT_REDIRECTED')
        self.capabilities = capabilities_snapshot or (lambda: capabilities(data_catalog=self.provider.catalog()))

    def preview(self, request):
        if isinstance(request, dict) and request.get('version') in {'FULL_UNIVERSE_SUBMISSION_V1', 'FULL_UNIVERSE_SUBMISSION_V2'}:
            from .universe_submission_v1 import preview_universe
            return preview_universe(self, request)
        if not isinstance(request, dict) or set(request) != REQUEST_FIELDS:
            raise ValueError('SUBMISSION_REQUEST_FIELDS_INVALID')
        request = deepcopy(request)
        if not isinstance(request['dataset_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]+', request['dataset_id']):
            raise ValueError('SUBMISSION_DATASET_ID_INVALID')
        strategy = public_rule_factory(request['rule'], request['strategy_id'])
        for scenario in ('BASE', 'STRESS'):
            public_rule_factory(request['rule'], request['strategy_id'] + '_' + scenario)
        if request['purpose'] != 'EXPLORATORY':
            raise ValueError('SUBMISSION_PURPOSE_UNSUPPORTED')
        if request['benchmark'] not in ('NONE', 'FULL_POOL_BUY_HOLD'):
            raise ValueError('SUBMISSION_BENCHMARK_UNSUPPORTED')
        if request['costs'] != ['BASE','STRESS']:
            raise ValueError('SUBMISSION_BASE_AND_STRESS_REQUIRED')
        cash = request['initial_cash']
        if type(cash) not in (int,float) or not math.isfinite(cash) or not 0 < cash < 1e12:
            raise ValueError('SUBMISSION_CASH_INVALID')
        symbols = request['symbols']
        if (not isinstance(symbols,list) or not symbols 
                or any(not isinstance(s,str) or not re.fullmatch(r'(?:00[0-9]{4}\.SZ|60[0-9]{4}\.SH)',s) for s in symbols) or len(set(symbols)) != len(symbols)):
            raise ValueError('SUBMISSION_SYMBOLS_INVALID')
        request['symbols'] = sorted(symbols)
        if request['benchmark'] == 'FULL_POOL_BUY_HOLD':
            from .research_benchmark_v1 import benchmark_payload
            public_benchmark_factory(benchmark_payload(request['symbols']), request['strategy_id'] + '_BENCHMARK')
        if type(request['max_positions']) is not int or not 1 <= request['max_positions'] <= len(symbols):
            raise ValueError('SUBMISSION_MAX_POSITIONS_INVALID')
        if type(request['max_symbol_exposure_bps']) is not int or not 1 <= request['max_symbol_exposure_bps'] <= 10000:
            raise ValueError('SUBMISSION_EXPOSURE_INVALID')
        if not isinstance(request['authorization_ref'], str) or not request['authorization_ref']:
            raise ValueError('SUBMISSION_AUTHORIZATION_REFERENCE_REQUIRED')
        for key in ('feature_start','account_start','account_end'):
            request[key] = day(request[key])
        if not request['feature_start'] < request['account_start'] < request['account_end']:
            raise ValueError('SUBMISSION_WINDOW_INVALID')
        ResearchDataAccessGuard().check_range(request['feature_start'],request['account_end'])
        datasets = {item['dataset_id']:item for item in self.provider.catalog()['datasets']}
        data = datasets.get(request['dataset_id'])
        if data is None or not set(symbols) <= set(data['symbols']):
            raise ValueError('SUBMISSION_DATASET_OR_POOL_NOT_COVERED')
        if request['feature_start'] < day(data['start']) or request['account_end'] > day(data['end']):
            raise ValueError('SUBMISSION_DATA_WINDOW_NOT_COVERED')
        value = {'request': request, 'rule_identity': strategy.rule_identity,
            'actual_rule': strategy.definition, 'data_metadata': data,
            'capabilities': self.capabilities(), 'required_fields': list(strategy.requirements.fields),
            'status': 'PREVIEW_ONLY_CONTENT_AND_AUTHORIZATION_NOT_CHECKED',
            'limitations': (['未选择基准，不能评价相对基准的表现。'] if request['benchmark'] == 'NONE' else
                ['基准按完整股票池等权一次买入；现金整手约束可能导致部分证券未成交。']) + ['预览不读取行情、不授予执行许可。']}
        return {**value, 'preview_identity': stable_hash(value)}

    def freeze(self, request, preview_identity):
        preview = self.preview(request)
        if preview['preview_identity'] != preview_identity:
            raise ValueError('SUBMISSION_PREVIEW_CHANGED')
        normalized = preview['request']
        features = {item['id']: item for item in preview['capabilities']['features']}
        required = ['indicator_rules']
        for field, feature in [('stop_loss_pct','cost_stop'), ('take_profit_pct','take_profit'), ('trailing_pct','trailing_stop')]:
            if normalized['rule']['exits'][field] is not None:
                required.append(feature)
        if any(not features.get(key, {}).get('public_entry') for key in required):
            raise ValueError('SUBMISSION_PUBLIC_CAPABILITY_NOT_CONNECTED')
        authority = self.authority(normalized['authorization_ref'])
        if not isinstance(authority, dict) or not authority.get('objective_id') or not authority.get('budget_path'):
            raise ValueError('SUBMISSION_AUTHORITY_INVALID')
        task_id = stable_hash({'preview': preview_identity, 'objective_id': authority['objective_id']})
        root = self.root / task_id
        if self.root.resolve() != self.root:
            raise ValueError('SUBMISSION_ROOT_REDIRECTED')
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            root.mkdir()
        except FileExistsError:
            raise ValueError('SUBMISSION_ALREADY_FROZEN_OR_INCOMPLETE_NO_OVERWRITE') from None
        immutable(root / 'PREVIEW.json', preview)
        immutable(root / 'FREEZE_INTENT.json', {'task_id': task_id, 'preview_identity': preview_identity,
            'objective_id': authority['objective_id'], 'automatic_retry': False})
        try:
            full_universe = normalized.get('version') in {'FULL_UNIVERSE_SUBMISSION_V1', 'FULL_UNIVERSE_SUBMISSION_V2'}
            qualified_universe = normalized.get('version') == 'FULL_UNIVERSE_SUBMISSION_V2'
            if full_universe:
                # 数据准备与必要条件计算全部由900秒/2048MiB受限进程完成。
                # 此处只接收其固定元数据，不在宿主进程再次加载全市场表。
                scanned = self.scan(request, preview_identity)
                if ((qualified_universe and scanned.get('qualified_account_ready') is not True)
                        or (not qualified_universe and scanned.get('coverage', {}).get('account_data_ready') is not True)):
                    raise ValueError('UNIVERSE_ACCOUNT_INPUT_NOT_READY:' + scanned['status'])
                if qualified_universe and normalized['max_positions'] > len(scanned['qualification_scope']['qualified_symbols']):
                    raise ValueError('SUBMISSION_QUALIFIED_POSITION_LIMIT_EXCEEDS_SCOPE')
                from .universe_submission_v1 import adopt_frozen_universe_bundle
                from .universe_scan_service_v1 import validated_scan_snapshot
                scanned_input = validated_scan_snapshot(self, scanned)
                parent_dependencies = []
                if qualified_universe:
                    parent_root = root / 'PARENT'
                    parent_root.mkdir()
                    _, parent_path, parent_dependencies = adopt_frozen_universe_bundle(
                        scanned_input['parent_path'], parent_root,
                        input_identity=scanned_input['parent_input_identity'],
                        snapshot_sha256=scanned_input['parent_sha256'])
                    parent_dependencies.append(str(parent_path))
                prepared, snapshot_path, frame_dependencies = adopt_frozen_universe_bundle(
                    scanned_input['path'], root, input_identity=scanned['input_identity'],
                    snapshot_sha256=scanned_input['sha256'])
                if qualified_universe:
                    immutable(root / 'QUALIFICATION_SCOPE.json', prepared['bundle']['qualified_scope'])
                    import shutil
                    for name in ('EXCLUSIONS.csv', 'REPORT_CN.md'):
                        shutil.copyfile(Path(scanned_input['path']).parent / name, root / name)
                    parent_dependencies.extend(str(root / name) for name in
                        ('QUALIFICATION_SCOPE.json', 'EXCLUSIONS.csv', 'REPORT_CN.md'))
            else:
                prepared = self.provider.prepare(normalized['dataset_id'], symbols=normalized['symbols'],
                    feature_start=normalized['feature_start'], account_start=normalized['account_start'],
                    account_end=normalized['account_end'], purpose=normalized['purpose'],
                    required_fields=preview['required_fields'], authorization=authority['data_authorization'])
        except Exception as exc:
            immutable(root / 'FREEZE_FAILURE.json', {'type': type(exc).__name__, 'message': str(exc),
                'automatic_retry': False})
            raise
        if not full_universe:
            frame_dependencies = []
            snapshot = deepcopy(prepared)
            snapshot['frame_schemas'] = frozen_frame_schemas(prepared['bundle'])
            for key in ('daily','turn','states'):
                snapshot['bundle'][key] = snapshot['bundle'][key].to_dict('records')
            snapshot_path = root / 'INPUT.json'
            immutable(snapshot_path, snapshot)
        digest = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
        items = []
        for cost in normalized['costs']:
            items.append({'factory': MODULE + ':public_rule_factory',
                'factory_kwargs': {'payload': normalized['rule'], 'strategy_id': normalized['strategy_id'] + '_' + cost},
                'loader': MODULE + ':load_frozen_bundle',
                'loader_kwargs': {'path': str(snapshot_path), 'sha256': digest, 'input_identity': prepared['input_identity']},
                'dependency_files': [str(snapshot_path), str(root / 'PREVIEW.json'), *frame_dependencies,
                    str(Path(__file__).with_name('research_capabilities_v1.py')),
                    str(Path(__file__).with_name('research_data_provider_v1.py'))],
                'backend_options': {'window': prepared['window'], 'costs': cost,
                    'initial_cash': normalized['initial_cash'], 'max_positions': normalized['max_positions'],
                    'max_symbol_exposure_bps': normalized['max_symbol_exposure_bps']}})
            if full_universe:
                if qualified_universe:
                    items[-1]['loader'] = MODULE + ':load_frozen_qualified_bundle'
                    items[-1]['loader_kwargs'].update(parent_path=str(parent_path),
                        parent_sha256=hashlib.sha256(parent_path.read_bytes()).hexdigest())
                    items[-1]['dependency_files'].extend(parent_dependencies)
                    items[-1]['dependency_files'].append(str(Path(__file__).with_name('universe_qualified_scope_v1.py')))
                items[-1]['backend_options'].update(backend_version='UNIVERSE_ACCOUNT_BACKEND_V1',
                    checkpoint_path=str(root / 'account' / (normalized['strategy_id'] + '_' + cost + '_CHECKPOINT.json')))
                items[-1]['dependency_files'].extend(str(Path(__file__).with_name(name)) for name in
                    ('universe_data_provider_v1.py', 'baostock_universe_adapter_v1.py', 'tdx_research_adapter_v1.py', 'research_universe_v1.py',
                     'universe_submission_v1.py', 'universe_account_inputs_v1.py', 'universe_benchmark_v1.py',
                     'universe_execution_recovery_v1.py', 'universe_status_v1.py', 'universe_scan_service_v1.py'))
        benchmark_id = None
        if normalized['benchmark'] == 'FULL_POOL_BUY_HOLD':
            from .research_benchmark_v1 import benchmark_payload
            benchmark_id = normalized['strategy_id'] + '_BENCHMARK'
            benchmark = deepcopy(items[0])
            benchmark['factory'] = MODULE + ':public_benchmark_factory'
            benchmark['factory_kwargs'] = {'payload': benchmark_payload(normalized['symbols']), 'strategy_id': benchmark_id}
            benchmark['backend_options'] = {'window': prepared['window'], 'costs': 'BASE', 'initial_cash': normalized['initial_cash']}
            items.append(benchmark)
        config = {'objective_id': authority['objective_id'], 'budget_path': str(authority['budget_path']),
                  'input_identity': prepared['input_identity'], 'items': items, 'benchmark_id': benchmark_id}
        if full_universe:
            config['benchmark_mode'] = normalized['benchmark']
        immutable(root / 'PREVIEW.json', preview)
        immutable(root / 'CONFIG.json', config)
        from scripts.run_strategy_account_v1 import freeze_config
        freeze_config(config, root / 'account')
        receipt = {'task_id': task_id, 'preview_identity': preview_identity,
            'input_identity': prepared['input_identity'], 'job_path': str(root / 'account' / 'JOB.json'),
            'objective_id': authority['objective_id'], 'status': 'FROZEN_REQUIRES_EXISTING_GOVERNANCE_APPROVAL',
            'benchmark': normalized['benchmark'], 'qualification': prepared['qualification'],
            'job_sha256': hashlib.sha256((root / 'account' / 'JOB.json').read_bytes()).hexdigest(),
            'plan_ids': {name: plan['plan_id'] for name, plan in read_json(root / 'account' / 'JOB.json')['plans'].items()}}
        if full_universe:
            receipt['submission_version'] = normalized['version']
            if qualified_universe:
                receipt['qualification_scope'] = prepared['bundle']['qualified_scope']
        immutable(root / 'TASK.json', receipt)
        return receipt

    def _task(self, task_id):
        if not isinstance(task_id,str) or not re.fullmatch(r'[0-9a-f]{64}',task_id):
            raise ValueError('SUBMISSION_TASK_ID_INVALID')
        root = self.root / task_id
        if root.resolve() != root:
            raise ValueError('SUBMISSION_TASK_REDIRECTED')
        task = read_json(root / 'TASK.json')
        if task['task_id'] != task_id or Path(task['job_path']) != root / 'account' / 'JOB.json':
            raise ValueError('SUBMISSION_TASK_BINDING_INVALID')
        return task

    def approval_preview(self, task_id):
        task = self._task(task_id)
        path = Path(task['job_path'])
        from scripts.run_strategy_account_v1 import validate_sources
        job = read_json(path)
        validate_sources(job)
        if task.get('job_sha256') != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError('SUBMISSION_FROZEN_JOB_CHANGED')
        plan_ids = {name: plan['plan_id'] for name,plan in job['plans'].items()}
        if task.get('plan_ids') != plan_ids:
            raise ValueError('SUBMISSION_FROZEN_PLAN_CHANGED')
        preview = read_json(path.parent.parent / 'PREVIEW.json')
        if (preview['preview_identity'] != task['preview_identity'] or
                stable_hash({k:v for k,v in preview.items() if k!='preview_identity'}) != task['preview_identity']):
            raise ValueError('SUBMISSION_FROZEN_PREVIEW_CHANGED')
        from .universe_status_v1 import universe_task_metadata_v1
        metadata = universe_task_metadata_v1(self, task_id) if task.get('submission_version') == 'FULL_UNIVERSE_SUBMISSION_V2' else {}
        return {**metadata, 'task_id':task_id,'preview_identity':task['preview_identity'],'plan_ids':plan_ids,
                'request':preview['request'],'rule_identity':preview['rule_identity'],
                'input_identity':task['input_identity'],'job_sha256':task['job_sha256']}

    def approve(self, task_id, preview_identity, *, operation_ids=None):
        summary = self.approval_preview(task_id)
        if summary['preview_identity'] != preview_identity:
            raise ValueError('SUBMISSION_APPROVAL_PREVIEW_CHANGED')
        request = summary['request']
        authority = self.authority(request['authorization_ref'])
        permit = authority.get('account_authorization')
        fields = {'purpose','rule_identity','initial_cash','symbols','feature_start','account_start','account_end',
                  'max_positions','max_symbol_exposure_bps','costs','benchmark','max_account_jobs'}
        campaign_ref = permit.get('campaign_ref') if isinstance(permit,dict) else None
        valid_fields = fields if campaign_ref is None else (fields - {'rule_identity'}) | {'campaign_ref'}
        if not isinstance(permit,dict) or set(permit)!=valid_fields or permit['purpose']!='FROZEN_PUBLIC_ACCOUNT_PLANS':
            raise PermissionError('SUBMISSION_ACCOUNT_AUTHORIZATION_REQUIRED')
        if campaign_ref is None and permit['rule_identity']!=summary['rule_identity']:
            raise PermissionError('SUBMISSION_ACCOUNT_RULE_OUTSIDE_AUTHORITY')
        exact = {'initial_cash','max_positions','max_symbol_exposure_bps','costs','benchmark'}
        if (any(permit[key]!=request[key] for key in exact)
                or not isinstance(permit['symbols'],list) or sorted(permit['symbols'])!=request['symbols']
                or any(day(permit[key])!=request[key] for key in ('feature_start','account_start','account_end'))
                or type(permit['max_account_jobs']) is not int or not 1 <= len(summary['plan_ids']) <= permit['max_account_jobs']):
            raise PermissionError('SUBMISSION_ACCOUNT_SCOPE_OUTSIDE_AUTHORITY')
        expires = datetime.fromisoformat(authority['expires_at'])
        if expires.tzinfo is None or expires<=datetime.now(timezone.utc):
            raise PermissionError('SUBMISSION_ACCOUNT_AUTHORITY_EXPIRED')
        source = deepcopy(authority.get('source',{}))
        if source.get('origin')!='USER_EXPLICIT_CURRENT_TASK' or not source.get('statement'):
            raise PermissionError('SUBMISSION_EXISTING_ACCOUNT_AUTHORITY_REQUIRED')
        task = self._task(task_id)
        from scripts.run_strategy_account_v1 import service
        job = read_json(task['job_path'])
        if job['objective_id']!=authority['objective_id'] or Path(job['budget_path'])!=Path(authority['budget_path']):
            raise PermissionError('SUBMISSION_ACCOUNT_OBJECTIVE_OUTSIDE_AUTHORITY')
        source.update(expires_at=authority['expires_at'],approved_plan_ids=summary['plan_ids'],
            authorization_ref=request['authorization_ref'],authorization_identity=stable_hash(authority))
        if request.get('version') == 'FULL_UNIVERSE_SUBMISSION_V2':
            source['qualified_scope_identity'] = summary['qualification_scope']['scope_identity']
        inputs = {'input_identity':job['input_identity'],
                  'novelty':{name:{'allowed':True,'plan_id':value,'reason':'EXPLICIT_FROZEN_PUBLIC_PLAN'}
                             for name,value in summary['plan_ids'].items()}}
        gov = service(job)
        if campaign_ref is not None:
            if not isinstance(campaign_ref,dict) or set(campaign_ref)!={'root','authorization_id'}:
                raise PermissionError('SUBMISSION_REGISTERED_CAMPAIGN_REQUIRED')
            if not isinstance(operation_ids,dict) or set(operation_ids)!=set(summary['plan_ids']):
                raise PermissionError('SUBMISSION_CAMPAIGN_OPERATIONS_REQUIRED')
            from .research_campaign_v1 import ResearchCampaignV1
            campaign = ResearchCampaignV1(campaign_ref['root'],campaign_ref['authorization_id'])
            if gov.receipt_path.exists():
                receipt = gov.active()
                expected_source = {'origin': 'CAMPAIGN_V1', 'campaign_root': str(campaign.root),
                    'authorization_id': campaign.authorization_id, 'operation_ids': operation_ids,
                    'expires_at': campaign.status()['authorization']['expires_at']}
                if receipt['source'] != expected_source or receipt['input_identity'] != inputs['input_identity'] or receipt['novelty'] != inputs['novelty']:
                    raise PermissionError('SUBMISSION_EXISTING_APPROVAL_CONFLICT')
                from .budget import SearchBudgetRegistryV1
                with gov.lock():
                    budget = SearchBudgetRegistryV1(job['objective_id'], job['budget_path'])
                    for name in job['plans']:
                        budget.register(gov.budget_kind, receipt['receipt_id'] + ':' + name, 1)
            else:
                receipt = gov.confirm_campaign_scope(campaign,operation_ids,inputs)
            return {'task_id':task_id,'status':'APPROVED','receipt_id':receipt['receipt_id'],
                    'plan_ids':summary['plan_ids'],'strategy_qualified':False}
        if operation_ids is not None:
            raise PermissionError('SUBMISSION_FIXED_AUTHORITY_CANNOT_USE_CAMPAIGN')
        from .mutation_boundary import ObjectiveMutationLock
        # 引用名称和登记内容共同绑定一次批准，不能通过重命名策略重复使用同一许可。
        claim_path = self.root / 'account-approvals' / (stable_hash(request['authorization_ref']) + '.json')
        claim = {'task_id':task_id,'authority_identity':stable_hash(authority),'plan_ids':summary['plan_ids']}
        with ObjectiveMutationLock.for_resource(claim_path):
            if claim_path.exists() and read_json(claim_path)!=claim:
                raise PermissionError('SUBMISSION_ACCOUNT_AUTHORITY_ALREADY_BOUND')
            immutable(claim_path,claim)
            if gov.receipt_path.exists():
                receipt = gov.active()
                if receipt['source']!=source or receipt['input_identity']!=job['input_identity']:
                    raise PermissionError('SUBMISSION_EXISTING_APPROVAL_CONFLICT')
                # 原confirm在回执持久化后才登记桶；同一回执的崩溃恢复不重置消费。
                from .budget import SearchBudgetRegistryV1
                with gov.lock():
                    budget=SearchBudgetRegistryV1(job['objective_id'],job['budget_path'])
                    for name in job['plans']:
                        budget.register(gov.budget_kind,receipt['receipt_id']+':'+name,1)
            else:
                receipt = gov.confirm(source,inputs)
        return {'task_id':task_id,'status':'APPROVED','receipt_id':receipt['receipt_id'],
                'plan_ids':summary['plan_ids'],'strategy_qualified':False}

    def start(self, task_id):
        from scripts.run_strategy_account_v1 import execute_accounts, report_account_job
        from .research_evidence_v1 import verify_job_evidence
        task = self._task(task_id)
        path = Path(task['job_path'])
        metadata = {}
        if task.get('submission_version') in {'FULL_UNIVERSE_SUBMISSION_V1', 'FULL_UNIVERSE_SUBMISSION_V2'}:
            from .universe_status_v1 import universe_task_metadata_v1
            metadata = universe_task_metadata_v1(self, task_id)
            job = read_json(path)
            if task['plan_ids'] != {name: plan['plan_id'] for name, plan in job['plans'].items()}:
                raise ValueError('SUBMISSION_FROZEN_PLAN_CHANGED')
        execute_accounts(path, recover=True)
        job = read_json(path)
        checks = {name: verify_job_evidence(path, name=name) for name in job['plans']}
        evidence = {'job_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'items': checks,
                    'advance_allowed': all(item.get('advance_allowed') is True for item in checks.values())}
        immutable(path.parent / 'VERIFICATION.json', evidence)
        if not evidence['advance_allowed']:
            return {'task_id': task_id, 'status': 'EVIDENCE_BLOCKED', 'verification': evidence,
                    'strategy_qualified': False, 'reports': None}
        reports = report_account_job(path)
        return {**metadata, 'task_id': task_id, 'status': 'ACCOUNT_VERIFIED', 'verification': evidence,
                'strategy_qualified': False, 'reports': reports}

    def diagnose(self, request, preview_identity):
        from .universe_status_v1 import diagnose_universe
        return diagnose_universe(self, request, preview_identity)

    def scan(self, request, preview_identity):
        from .universe_scan_service_v1 import scan_universe
        return scan_universe(self, request, preview_identity)

    def resume(self, task_id):
        from .universe_execution_recovery_v1 import resume_universe_job
        task = self._task(task_id)
        if hashlib.sha256(Path(task['job_path']).read_bytes()).hexdigest() != task['job_sha256']:
            raise ValueError('SUBMISSION_FROZEN_JOB_CHANGED')
        resume_universe_job(task['job_path'])
        return self.start(task_id)

    def status(self, task_id):
        from scripts.run_strategy_account_v1 import status
        task = self._task(task_id)
        path = Path(task['job_path'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != task['job_sha256']:
            raise ValueError('SUBMISSION_FROZEN_JOB_CHANGED')
        state = status(path)
        evidence_path = path.parent / 'VERIFICATION.json'
        evidence = read_json(evidence_path) if evidence_path.exists() else None
        if evidence is not None and evidence.get('job_sha256') != task['job_sha256']:
            raise ValueError('SUBMISSION_VERIFICATION_JOB_CHANGED')
        from .universe_status_v1 import universe_task_metadata_v1
        metadata = universe_task_metadata_v1(self, task_id) if task.get('submission_version') in {'FULL_UNIVERSE_SUBMISSION_V1', 'FULL_UNIVERSE_SUBMISSION_V2'} else {}
        return {**metadata, **state, 'task_id': task_id,
                'recorded_verification': evidence, 'freshly_reverified': False,
                'reports': {name: str(path.parent / (name + '_REPORT.md'))
                            for name in task['plan_ids'] if (path.parent / (name + '_REPORT.md')).exists()},
                'levels': {'account_execution': 'COMPLETED' if state['completed'] == state['total'] else 'INCOMPLETE',
                           'historical_screen': 'NOT_ASSESSED', 'independent_validation': 'NOT_RUN',
                           'strategy_qualification': 'NOT_GRANTED'},
                'strategy_qualified': False}
