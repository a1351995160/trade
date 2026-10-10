"""V4 仅增加总任务/阶段绑定；账户策略、全池资格和计算仍使用公共实现。"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from .common import stable_hash
from .exploration_governance import immutable, read_json
from .formal_account_backend_v1 import BASE_COSTS, STRESS_COSTS
from .research_data_provider_v1 import day

VERSION = 'FULL_UNIVERSE_SUBMISSION_V4'
LONG_VERSIONS = {'FULL_UNIVERSE_SUBMISSION_V3', VERSION}


def request_scope(request, contract, dataset):
    scope = contract['scope']
    if request['universe_id'] != scope['universe_id'] or dataset['universe_identity'] != scope['universe_hash']:
        raise PermissionError('CONTINUOUS_REGISTERED_UNIVERSE_CHANGED')
    return {name: deepcopy(scope[name]) for name in ('universe_id', 'universe_hash', 'rule_version',
        'cost_profiles', 'execution_profiles')} | {
        'initial_cash': request['initial_cash'], 'max_positions': request['max_positions'],
        'phase': request['phase'], 'dataset_id': request['dataset_id'],
        'dataset_hash': dataset['metadata_hash'],
        'feature_start': _iso_day(request['feature_start']), 'account_start': _iso_day(request['account_start']),
        'account_end': _iso_day(request['account_end']),
        'public_request_identity': stable_hash({key: value for key, value in request.items()
        if key not in {'research_binding_ref', 'authorization_ref', 'symbols', 'feature_start', 'account_start', 'account_end'}} |
        {key: _iso_day(request[key]) for key in ('feature_start', 'account_start', 'account_end')})}


def _iso_day(value):
    value = str(day(value))
    return value[:4] + '-' + value[4:6] + '-' + value[6:]


def _parent_authority(service, request):
    resolver = getattr(service, 'independent_authority', None) if request['phase'] == 'CONFIRMATION' else None
    return (resolver or service.authority)(request['authorization_ref'])


def _independent_binding(service, request, contract):
    if request['phase'] == 'EXPLORATION' and 'dataset_id' not in contract['scope']['data_routes']['EXPLORATION']:
        resolver = getattr(service, 'exploration_admission', None)
        if not callable(resolver):
            raise PermissionError('CONTINUOUS_TRAIN_PROJECTION_ADMISSION_REQUIRED')
        proof = resolver()
        window = proof.get('request_fields', {})
        if (proof.get('dataset_id') != request['dataset_id']
                or not window.get('feature_start', '') <= _iso_day(request['feature_start'])
                    <= _iso_day(request['account_start']) <= _iso_day(request['account_end']) <= window.get('account_end', '')
                or _iso_day(request['account_start']) < window.get('account_start', '')):
            raise PermissionError('CONTINUOUS_TRAIN_PROJECTION_WINDOW_CONFLICT')
        return {'data_route_proof': proof}
    route = contract['scope']['data_routes']['CONFIRMATION']
    if request['phase'] != 'CONFIRMATION':
        return {}
    if route is None:
        raise PermissionError('CONTINUOUS_INDEPENDENT_ROUTE_REQUIRED')
    admit = getattr(service, 'independent_admission', None)
    path = getattr(service, 'independent_protocol_path', None)
    if not callable(admit) or path is None:
        raise PermissionError('CONTINUOUS_AUTHENTICATED_FUTURE_ADMISSION_REQUIRED')
    path = Path(path).absolute()
    expected = service.continuous_scope.campaign.directory / 'diagnosis_v4' / 'business_validation' / 'PROTOCOL.json'
    if path != expected or path.resolve() != path:
        raise PermissionError('CONTINUOUS_INDEPENDENT_PROTOCOL_PATH_CONFLICT')
    protocol = read_json(path)
    admission_path = path.parent / 'ADMISSION.json'
    admission = read_json(admission_path)
    actual = admit({**protocol, 'frozen_admission': admission})
    if actual != admission or any(actual.get(key) is not True for key in
            ('source_authenticated', 'prior_access_review_passed', 'post_freeze_unseen')):
        raise PermissionError('CONTINUOUS_FUTURE_ADMISSION_CHANGED')
    return {'data_route_proof': {'protocol_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'admission_sha256': hashlib.sha256(admission_path.read_bytes()).hexdigest()}}


def _check_rule_scope(rule, scope):
    aliases = {item['instance_id']: item['id'] for item in rule['indicator_instances']}
    used = set()
    def walk(node, role):
        if not isinstance(node, dict):
            return
        if node.get('op') == 'indicator':
            indicator_id = aliases[node['args'][0]]
            if role not in scope['indicator_roles'].get(indicator_id, []):
                raise PermissionError('CONTINUOUS_INDICATOR_ROLE_OUTSIDE_SCOPE')
            used.add(indicator_id)
        for child in node.get('args', []):
            walk(child, role)
    walk(rule['buy'], 'BUY')
    walk(rule['sell'], 'SELL')
    walk(rule['market_filter'], 'BUY')
    walk(rule['selection']['score'], 'SCORE')
    if len(used) < 2:
        raise PermissionError('CONTINUOUS_MULTI_INDICATOR_REQUIRED')
    selection = rule['selection']
    allowed = scope['ranking_variables']
    def score_ops(node):
        if node['op'] not in allowed['score_operators']:
            raise PermissionError('CONTINUOUS_SCORE_OPERATOR_OUTSIDE_SCOPE')
        for child in node['args']:
            if isinstance(child, dict):
                score_ops(child)
    score_ops(selection['score'])
    if selection['direction'] not in allowed['score_directions'] or selection['tie_breaker'] != allowed['tie_breaker']:
        raise PermissionError('CONTINUOUS_SELECTION_OUTSIDE_SCOPE')


def bind_request(service, request, *, batch_id, candidate_identity, phase):
    if service.continuous_scope is None:
        raise PermissionError('CONTINUOUS_SCOPE_DEPLOYMENT_REQUIRED')
    request = deepcopy(request)
    request.update(version=VERSION, phase=phase,
        purpose='EXPLORATORY' if phase == 'EXPLORATION' else 'INDEPENDENT_BUSINESS_VALIDATION')
    base = service.continuous_scope.campaign.peek_status()['base_authorization']
    contract = base['scope_policy']['summary']['contract']
    parent = _parent_authority(service, request)
    from .universe_data_provider_v1 import service_trusted_scope
    trusted = service_trusted_scope(service, request, parent)
    datasets = {item['dataset_id']: item for item in service.provider.catalog(trusted_scope=trusted)['datasets']}
    dataset = datasets.get(request['dataset_id'])
    if dataset is None:
        raise PermissionError('CONTINUOUS_DATASET_NOT_REGISTERED')
    _check_rule_scope(request['rule'], contract['scope'])
    from .strategy_submission_v1 import public_rule_factory
    if public_rule_factory(request['rule'], request['strategy_id']).rule_identity != candidate_identity:
        raise PermissionError('CONTINUOUS_RULE_IDENTITY_CONFLICT')
    expiry = service.continuous_scope.campaign.peek_status()['authorization']['expires_at']
    request['research_binding_ref'] = service.continuous_scope.delegate(batch_id=batch_id,
        candidate_identity=candidate_identity, phase=phase,
        request={**request_scope(request, contract, dataset), **_independent_binding(service, request, contract)}, expires_at=expiry)
    return request


def continuous_authority(service, request):
    if service.continuous_scope is None:
        raise PermissionError('CONTINUOUS_SCOPE_DEPLOYMENT_REQUIRED')
    binding = service.continuous_scope.resolve(request['research_binding_ref'])
    campaign = service.continuous_scope.campaign
    base = campaign.peek_status()['base_authorization']
    contract = base['scope_policy']['summary']['contract']
    parent = _parent_authority(service, request)
    from .universe_data_provider_v1 import service_trusted_scope
    trusted = service_trusted_scope(service, request, parent)
    datasets = {item['dataset_id']: item for item in service.provider.catalog(trusted_scope=trusted)['datasets']}
    dataset = datasets.get(request['dataset_id'])
    if dataset is None or {**request_scope(request, contract, dataset), **_independent_binding(service, request, contract)} != binding['request']:
        raise PermissionError('CONTINUOUS_PUBLIC_REQUEST_CHANGED')
    from .strategy_submission_v1 import public_rule_factory
    if (binding['phase'] != request['phase']
            or public_rule_factory(request['rule'], request['strategy_id']).rule_identity != binding['candidate_identity']):
        raise PermissionError('CONTINUOUS_RULE_OR_PHASE_CHANGED')
    _check_rule_scope(request['rule'], contract['scope'])
    parent = _parent_authority(service, request)
    if parent.get('objective_id') != base['objective_id']:
        raise PermissionError('CONTINUOUS_DEPLOYMENT_OBJECTIVE_CONFLICT')
    scope = contract['scope']
    route = scope['data_routes'][request['phase']]
    symbols = sorted(dataset['target_symbols'])
    permit = {key: deepcopy(request[key]) for key in ('initial_cash', 'max_positions',
        'max_symbol_exposure_bps', 'costs', 'benchmark', 'feature_start', 'account_start', 'account_end',
        'execution_profile', 'observation_plan')}
    permit.update(purpose='FROZEN_PUBLIC_ACCOUNT_PLANS', symbols=symbols, max_account_jobs=2,
        campaign_ref={'root': str(campaign.root), 'authorization_id': campaign.authorization_id})
    authority = {'objective_id': base['objective_id'],
        'budget_path': str(campaign.directory / 'execution_registry.json'),
        'expires_at': binding['expires_at'],
        'data_authorization': {'authorization_id': binding['binding_id'], 'purpose': request['purpose'],
            'dataset_ids': [request['dataset_id']], 'start': route['start'], 'end': route['end']},
        'account_authorization': permit, 'execution_profiles': deepcopy(scope['execution_profiles']),
        'compute_authorization': {'preparation_jobs': 1, 'verification_jobs': 1, 'report_jobs': 1},
        'source': {'origin': 'CONTINUOUS_CAMPAIGN_SCOPE_V1', 'research_binding_ref': request['research_binding_ref'],
                   'campaign_root': str(campaign.root), 'authorization_id': campaign.authorization_id}}
    for key in ('trusted_deployment', 'trusted_data_access'):
        if key in parent:
            authority[key] = deepcopy(parent[key])
    return authority


def preview_continuous(service, request):
    from .strategy_submission_v1 import REQUEST_FIELDS
    fields = (REQUEST_FIELDS - {'symbols'}) | {'version', 'universe_id', 'account_scope',
        'execution_profile', 'observation_plan', 'phase', 'research_binding_ref'}
    if not isinstance(request, dict) or set(request) != fields or request.get('version') != VERSION:
        raise ValueError('CONTINUOUS_PUBLIC_REQUEST_FIELDS_INVALID')
    if request['phase'] not in ('EXPLORATION', 'CONFIRMATION'):
        raise ValueError('CONTINUOUS_PUBLIC_PHASE_INVALID')
    purpose = 'EXPLORATORY' if request['phase'] == 'EXPLORATION' else 'INDEPENDENT_BUSINESS_VALIDATION'
    if request['purpose'] != purpose:
        raise PermissionError('CONTINUOUS_PUBLIC_PURPOSE_CONFLICT')
    request = deepcopy(request)
    for key in ('feature_start', 'account_start', 'account_end'):
        request[key] = day(request[key])
    authority = continuous_authority(service, request)
    from .universe_submission_v1 import preview_universe
    from .universe_data_provider_v1 import service_trusted_scope
    trusted_scope = service_trusted_scope(service, request, authority)
    legacy = {key: value for key, value in request.items() if key not in {'phase', 'research_binding_ref'}}
    legacy.update(version='FULL_UNIVERSE_SUBMISSION_V3', purpose='EXPLORATORY')
    preview = preview_universe(service, legacy, trusted_scope=trusted_scope)
    preview.pop('preview_identity')
    request['symbols'] = preview['request']['symbols']
    preview.update(request=request, parent_binding_identity=request['research_binding_ref']['binding_id'],
        phase=request['phase'], authority_identity=stable_hash(authority))
    preview['preview_identity'] = stable_hash(preview)
    return preview


def begin_freeze(service, request, preview_identity):
    preview = service.preview(request)
    if preview['preview_identity'] != preview_identity:
        raise ValueError('SUBMISSION_PREVIEW_CHANGED')
    authority = continuous_authority(service, request)
    task_id = stable_hash({'preview': preview_identity, 'objective_id': authority['objective_id']})
    if (service.root / task_id / 'TASK.json').exists():
        return service._task(task_id)
    from .universe_scan_service_v1 import begin_universe_preparation
    begun = begin_universe_preparation(service, request, preview_identity)
    intent = {'task_id': task_id, 'request': deepcopy(request), 'preview_identity': preview_identity,
              'authority_identity': stable_hash(authority), 'scan_id': begun['scan_id']}
    immutable(service.root / 'continuous_intents' / (task_id + '.json'), intent)
    return {'task_id': task_id, 'preview_identity': preview_identity, 'status': 'PREPARING',
            'dispatched_segments': 0, 'strategy_qualified': False}


def advance_submission(service, task_id):
    from .mutation_boundary import ObjectiveMutationLock
    with ObjectiveMutationLock.for_resource(service.root / ('advance_' + task_id)):
        task_root = service.root / task_id
        if not (task_root / 'TASK.json').exists():
            intent = read_json(service.root / 'continuous_intents' / (task_id + '.json'))
            request = intent['request']
            authority = continuous_authority(service, request)
            if stable_hash(authority) != intent['authority_identity']:
                raise PermissionError('CONTINUOUS_FREEZE_AUTHORITY_CHANGED')
            from .universe_scan_service_v1 import advance_universe_preparation
            prepared = advance_universe_preparation(service, request, intent['preview_identity'])
            if prepared['complete']:
                task = service._freeze_prepared(request, intent['preview_identity'])
                return {**task, 'status': 'FROZEN', 'dispatched_segments': prepared['dispatched_segments']}
            return {**prepared, 'task_id': task_id, 'status': 'PREPARING', 'strategy_qualified': False}
        task = service._task(task_id)
        path = Path(task['job_path'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != task['job_sha256']:
            raise ValueError('CONTINUOUS_FROZEN_JOB_CHANGED')
        preview = read_json(task_root / 'PREVIEW.json')
        request = {key: value for key, value in preview['request'].items() if key != 'symbols'}
        authority = continuous_authority(service, request)
        result_path = task_root / 'PUBLIC_RESULT.json'
        if result_path.exists():
            return read_json(result_path)
        if not (path.parent / 'CONFIRMATION.json').exists():
            service.approve(task_id, task['preview_identity'])
            return {'task_id': task_id, 'status': 'APPROVED', 'dispatched_segments': 0, 'strategy_qualified': False}
        from scripts.run_strategy_account_v1 import execute_long_horizon_accounts, run_long_horizon_compute
        accounts = execute_long_horizon_accounts(path, recover=True, step=True)
        if accounts['status'] != 'COMPLETED' or accounts['dispatched_segments']:
            return {**accounts, 'task_id': task_id, 'status': 'ACCOUNTS_' + accounts['status'], 'strategy_qualified': False}
        verification = run_long_horizon_compute(path, authority, preview['request'], 'VERIFICATION', step=True)
        if verification['status'] != 'COMPLETED':
            return {**verification, 'task_id': task_id, 'strategy_qualified': False}
        evidence = verification['result']
        immutable(path.parent / 'VERIFICATION.json', evidence)
        if not evidence['advance_allowed']:
            return {'task_id': task_id, 'status': 'EVIDENCE_BLOCKED', 'verification': evidence,
                    'dispatched_segments': verification['dispatched_segments'], 'strategy_qualified': False}
        if verification['dispatched_segments']:
            return {'task_id': task_id, 'status': 'VERIFIED', 'dispatched_segments': 1, 'strategy_qualified': False}
        reports = run_long_horizon_compute(path, authority, preview['request'], 'REPORT', step=True)
        if reports['status'] != 'COMPLETED':
            return {**reports, 'task_id': task_id, 'strategy_qualified': False}
        result = {'task_id': task_id, 'status': 'ACCOUNT_VERIFIED', 'verification': evidence,
            'reports': reports['result'], 'dispatched_segments': reports['dispatched_segments'],
            'input_identity': task['input_identity'], 'rule_identity': preview['rule_identity'],
            'phase': request['phase'], 'strategy_qualified': False}
        immutable(result_path, {**result, 'dispatched_segments': 0})
        return result


def reconcile_submission(service, task_id):
    """停派后的同用途结算；仅核原件与已派发段，不刷新授权或读取行情。"""
    import re
    from .mutation_boundary import ObjectiveMutationLock
    if not isinstance(task_id,str) or not re.fullmatch(r'[a-f0-9]{64}',task_id):
        raise ValueError('SUBMISSION_TASK_ID_INVALID')
    with ObjectiveMutationLock.for_resource(service.root/('advance_'+task_id)):
        task_root=service.root/task_id
        if not (task_root/'TASK.json').exists():
            intent=read_json(service.root/'continuous_intents'/(task_id+'.json'))
            scan_root=service.root/'signal-scans'/intent['scan_id']
            if scan_root.resolve()!=scan_root:
                raise PermissionError('CONTINUOUS_RECONCILE_SCAN_ROOT_CHANGED')
            scan=read_json(scan_root/'SCAN_INTENT.json')
            if (scan['scan_id']!=intent['scan_id'] or scan['preview']['preview_identity']!=intent['preview_identity']
                    or stable_hash(scan['compute_authority'])!=intent['authority_identity']
                    or intent['task_id']!=task_id or task_id!=stable_hash({'preview':intent['preview_identity'],
                        'objective_id':scan['compute_authority']['objective_id']})):
                raise PermissionError('CONTINUOUS_RECONCILE_PREPARATION_CHANGED')
            request=scan['preview']['request']
            binding=service.continuous_scope.resolve(request['research_binding_ref'],for_dispatch=False)
            if (binding['phase']!=request['phase']
                    or scan['compute_authority']['source']['research_binding_ref']!=request['research_binding_ref']):
                raise PermissionError('CONTINUOUS_RECONCILE_BINDING_CHANGED')
            from .universe_compute_governance_v1 import UniverseComputeGovernanceV1
            if not (scan_root/'COMPUTE'/'COMPUTE_START.json').exists():
                return {'task_id':task_id,'status':'NO_PUBLIC_RECONCILIATION_PENDING','dispatched_segments':0}
            meter=UniverseComputeGovernanceV1(scan_root/'COMPUTE',scan['compute_authority'],request,
                'PREPARATION',for_dispatch=False)
            ref=meter.campaign_operation
            operation=service.continuous_scope.campaign.peek_status()['operations'][ref['operation_id']]
            if operation['status'] in ('COMPLETED','FAILED') and meter.status()['pending'] is None:
                return {'task_id':task_id,'status':'NO_PUBLIC_RECONCILIATION_PENDING','dispatched_segments':0}
            from .universe_scan_service_v1 import reconcile_long_preparation
            result=reconcile_long_preparation(scan_root)
            return {'task_id':task_id,'status':'PUBLIC_RECONCILED','stage':'PREPARATION',
                    'result':result,'dispatched_segments':0}
        task=service._task(task_id)
        path=Path(task['job_path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=task['job_sha256']:
            raise PermissionError('CONTINUOUS_FROZEN_JOB_CHANGED')
        from scripts import run_strategy_account_v1 as runner
        job=read_json(path);runner.validate_sources(job)
        preview=read_json(task_root/'PREVIEW.json')
        if (preview['preview_identity']!=task['preview_identity']
                or stable_hash({key:item for key,item in preview.items() if key!='preview_identity'})!=task['preview_identity']):
            raise PermissionError('CONTINUOUS_RECONCILE_PREVIEW_CHANGED')
        request=preview['request']
        binding=service.continuous_scope.resolve(request['research_binding_ref'],for_dispatch=False)
        if binding['phase']!=request['phase']:
            raise PermissionError('CONTINUOUS_RECONCILE_BINDING_CHANGED')
        stages=[]
        account_pending=any((path.parent/(name+'_START.json')).exists()
            and not (path.parent/(name+'_SETTLEMENT.json')).exists() for name in job['plans'])
        if account_pending:
            from .universe_execution_recovery_v1 import resume_long_horizon_job
            resume_long_horizon_job(path,job,reconcile_only=True)
            stages.append('ACCOUNT')
        from .universe_compute_governance_v1 import UniverseComputeGovernanceV1
        for stage in ('VERIFICATION','REPORT'):
            folder=path.parent/('COMPUTE_'+stage)
            if not (folder/'COMPUTE_START.json').exists():
                continue
            scope=read_json(folder/'SCOPE.json')
            if (scope['job_sha256']!=task['job_sha256'] or scope['stage']!=stage
                    or scope['request']!=request):
                raise PermissionError('CONTINUOUS_RECONCILE_COMPUTE_SCOPE_CHANGED')
            meter=UniverseComputeGovernanceV1(folder,scope['authority'],request,stage,for_dispatch=False)
            operation=service.continuous_scope.campaign.peek_status()['operations'][meter.campaign_operation['operation_id']]
            if operation['status'] in ('COMPLETED','FAILED') and meter.status()['pending'] is None:
                continue
            runner.reconcile_long_horizon_compute(path,stage)
            stages.append(stage)
        return {'task_id':task_id,'status':'PUBLIC_RECONCILED' if stages else 'NO_PUBLIC_RECONCILIATION_PENDING',
                'stages':stages,'dispatched_segments':0}
