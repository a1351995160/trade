"""发布既有六账户验收的元数据；不读行情、不重跑账户、不授予策略资格。"""
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re

from .common import stable_hash
from .universe_execution_profile_v1 import (
    CONTINUOUS_PROFILE, ENGINEERING_PURPOSE, SEGMENTED_PROFILE, execution_profile,
)


VERSION = 'PUBLISHED_LONG_HORIZON_ACCEPTANCE_V1'
ACCEPTANCE_VERSION = 'LONG_HORIZON_UNIVERSE_ACCEPTANCE_V1'
PREFIX = Path('reports/long_horizon_universe_acceptance_v1')
METADATA = PREFIX / 'published_metadata'
BOUNDARY = 'PUBLISHED_METADATA_ONLY_NO_MARKET_READ_OR_REAUDIT'
FEATURES = ['long_horizon_account', 'volatility_rank', 'signal_account_dual_report']
STATE_FIELDS = ('ledger', 'engine', 'orders', 'order_counters', 'broker_fill_counter', 'events',
    'event_counter', 'rule_states', 'rule_exits', 'bars', 'states', 'skips', 'allocations',
    'decisions', 'peak', 'drawdown', 'last_day', 'calendar_identity')
ECONOMIC_FIELDS = ('fills', 'metrics', 'final_account_checkpoint', 'scanner_identity', 'scan_preparation', 'account_policy')


def _require(value, reason):
    if not value:
        raise ValueError('LONG_PUBLICATION_' + reason)


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _file(path):
    path = Path(path).absolute()
    _require(path.resolve() == path and path.is_file(), 'ARTIFACT_MISSING_OR_REDIRECTED:' + str(path))
    return path


def _sha(path):
    return hashlib.sha256(_file(path).read_bytes()).hexdigest()


def _json(path):
    return json.loads(_file(path).read_bytes())


def _self_hash(value, key, reason):
    _require(value.get(key) == stable_hash({k: v for k, v in value.items() if k != key}), reason)


def _code_relative(value):
    parts = PurePosixPath(str(value).replace('\\', '/')).parts
    starts = [i for i, part in enumerate(parts) if part in ('src', 'scripts')]
    if not starts or not str(value).endswith('.py'):
        return None
    relative = PurePosixPath(*parts[starts[-1]:])
    _require('..' not in relative.parts and relative.suffix == '.py', 'SOURCE_PATH_INVALID')
    return relative.as_posix()


def _snapshot_sources(snapshot):
    values = {}
    for component in (snapshot, snapshot['full_universe'], snapshot['long_horizon']):
        _require(isinstance(component['source_hashes'], dict) and component['source_hashes'], 'SOURCE_CLOSURE_MISSING')
        for name, digest in component['source_hashes'].items():
            relative = _code_relative(name) or 'src/chanlun_trader/research_factory/' + name
            _require(relative.endswith('.py') and _hash(digest), 'SOURCE_HASH_INVALID')
            _require(relative not in values or values[relative] == digest, 'SOURCE_CLOSURE_CONFLICT')
            values[relative] = digest
    return values


def _profile(profile, count, reference=False):
    expected = execution_profile(CONTINUOUS_PROFILE if reference else SEGMENTED_PROFILE,
        count, ENGINEERING_PURPOSE if reference else 'RESEARCH_ACCOUNT')
    _require(profile == expected, 'PROFILE_CONFLICT')
    return expected


def _bound(resource, upper, profile):
    seconds = resource.get('elapsed_wall_seconds')
    peak = resource.get('peak_memory_mib')
    _require(type(seconds) in (int, float) and math.isfinite(seconds) and 0 <= seconds <= upper,
        'MEASURED_RESOURCE_BOUND_CONFLICT')
    _require(resource.get('timed_out') is False and resource.get('windows_job_bound') is True
        and resource.get('resource_platform') == 'nt'
        and ('memory_mib' not in resource or resource['memory_mib'] == profile['memory_mib'])
        and ('wall_seconds' not in resource or type(resource['wall_seconds']) in (int, float) and 0 < resource['wall_seconds'] <= upper)
        and resource.get('memory_measurement') == 'WINDOWS_JOB_PEAK_COMMIT'
        and type(peak) in (int, float) and math.isfinite(peak) and 0 <= peak <= profile['memory_mib'],
        'WINDOWS_RESOURCE_BOUND_NOT_PROVEN')
    return seconds


def _budget(budget, start, kind, key):
    _require(budget.get('settled_reservations', {}).get(start['reservation']) == 'CONSUMED', 'BUDGET_NOT_CONSUMED')
    buckets = [item for item in budget['buckets'] if item['kind'] == kind and item['key'] == key]
    _require(len(buckets) == 1 and type(buckets[0]['used']) is int and buckets[0]['used'] >= 1
        and buckets[0]['reserved'] == 0 and buckets[0]['used'] <= buckets[0]['limit'], 'BUDGET_BUCKET_CONFLICT')
    return buckets[0]


def _metrics_match(actual, audited):
    if not isinstance(actual, dict) or set(actual) != set(audited): return False
    for key, value in audited.items():
        other = actual[key]
        if key == 'trade_count':
            if type(value) is not int or type(other) is not int or value != other: return False
        elif (type(value) not in (int, float) or type(other) not in (int, float)
                or not math.isfinite(value) or not math.isfinite(other) or abs(value-other) > 1e-6):
            return False
    return True


def _snapshot_material(value):
    return {**value, 'frames': {kind: {key: field for key, field in info.items() if key != 'path'}
        for kind, info in value['frames'].items()}}


def _scope(scope, preview, input_identity, window):
    _self_hash(scope, 'scope_identity', 'QUALIFICATION_HASH_CONFLICT')
    targets, qualified = scope['target_symbols'], scope['qualified_symbols']
    excluded = [item['symbol'] for item in scope['excluded']]
    _require(targets == sorted(set(targets)) and qualified == sorted(set(qualified)) and qualified
        and len(excluded) == len(set(excluded)) and not set(qualified).intersection(excluded)
        and sorted(qualified + excluded) == targets and not scope['blocking_global_gaps'], 'QUALIFICATION_PARTITION_CONFLICT')
    # 投影身份在附加回执前生成；最终身份含回执，由冻结 INPUT/JOB 与准备输出另行绑定。
    _require(_hash(scope['projected_input_identity']) and _hash(input_identity)
        and scope['projected_input_identity'] != input_identity
        and window == {**scope['parent_window'], 'symbols': qualified}
        and scope['parent_window']['symbols'] == targets, 'QUALIFIED_INPUT_SCOPE_CONFLICT')
    if preview is not None:
        _self_hash(preview, 'preview_identity', 'PREVIEW_HASH_CONFLICT')
        _require(preview['request']['symbols'] == targets and preview['data_metadata']['target_symbols'] == targets,
            'REGISTERED_POOL_SHRUNK')
        datasets = [row for row in preview['capabilities']['data']['datasets']
            if row['dataset_id'] == preview['request']['dataset_id']]
        _require(len(datasets) == 1 and datasets[0] == preview['data_metadata'], 'REGISTERED_DATASET_SCOPE_CONFLICT')
        for board, prefix in (('SZ_MAIN', '00'), ('SH_MAIN', '60'), ('CHINEXT', '30')):
            count = sum(symbol.startswith(prefix) for symbol in targets)
            _require(count > 0 and preview['data_metadata']['by_board'][board]['target_count'] == count,
                'THREE_BOARD_TARGET_CONFLICT')
    _require(all(any(symbol.startswith(prefix) for symbol in qualified) for prefix in ('00', '60', '30')),
        'THREE_BOARD_QUALIFIED_SCOPE_REQUIRED')
    _require(all(item.get('reasons') for item in scope['excluded']), 'EXCLUSION_REASON_MISSING')
    return {'target_count': len(targets), 'qualified_count': len(qualified), 'excluded_count': len(excluded)}


def _segments(rows, read, profile, receipt, name, *, compute_identity=None, access_scope=None, outputs=None):
    _require(isinstance(rows, list) and rows, 'SEGMENTS_MISSING')
    charged, measured, conservative, head = 0., 0., 0., None
    paused = []
    for number, refs in enumerate(rows, 1):
        dispatch, charge = read(refs['dispatch']), read(refs['charge'])
        _self_hash(dispatch, 'dispatch_id', 'DISPATCH_HASH_CONFLICT')
        _self_hash(charge, 'charge_id', 'CHARGE_HASH_CONFLICT')
        upper = dispatch['upper_bound_seconds']
        _require(type(upper) in (int, float) and math.isfinite(upper)
            and 0 < upper <= min(profile['worker_seconds'], profile['total_seconds'] - charged)
            and dispatch['previous_head'] == head and charge['dispatch_id'] == dispatch['dispatch_id'], 'SEGMENT_CHAIN_CONFLICT')
        if compute_identity is None:
            _require(dispatch['segment_number'] == number and dispatch['kind'] == name
                and dispatch['receipt_id'] == receipt and dispatch['profile_hash'] == profile['profile_hash']
                and dispatch['charged_before'] == charged, 'ACCOUNT_DISPATCH_SCOPE_CONFLICT')
        else:
            _require(dispatch['number'] == number and dispatch['compute_identity'] == compute_identity, 'COMPUTE_DISPATCH_SCOPE_CONFLICT')
        _require(charge['outcome'] in {'CONTINUE', 'PAUSED', 'COMPLETED'}
            and (charge['outcome'] == 'COMPLETED') == (number == len(rows)), 'SEGMENT_COMPLETION_CONFLICT')
        if charge['basis'] == 'UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND':
            resumed = read(refs['resume'])
            _require(number < len(rows) and charge['outcome'] == 'CONTINUE'
                and charge['measured_seconds'] is None and charge['seconds'] == upper
                and resumed['dispatch_id'] == dispatch['dispatch_id'] and resumed.get('budget_reused') is True,
                'UNKNOWN_SEGMENT_RECOVERY_NOT_PROVEN')
            conservative += upper
        else:
            _require(charge['basis'] == 'MEASURED_ACTIVE_WALL_SECONDS', 'MEASUREMENT_BASIS_INVALID')
            resource, worker, access = (read(refs[key]) for key in ('resource', 'worker', 'access'))
            status = read(refs['status']) if 'status' in refs else {}
            if name == 'PREPARATION' and compute_identity is not None:
                _require(resource.get('phase') == ('QUALIFY' if number == len(rows) else 'PREPARE')
                    and access.get('phase') == resource['phase'] and access.get('dispatch_id') == dispatch['dispatch_id'],
                    'PREPARATION_PHASE_CONFLICT')
                status = {'state': 'COMPLETED' if resource['returncode'] == 0 else 'CONTINUE', 'dispatch_id': dispatch['dispatch_id']}
            elif compute_identity is not None:
                _require(access.get('compute_identity') == compute_identity and access.get('stage') == name, 'COMPUTE_ACCESS_CONFLICT')
            seconds = _bound(resource, upper, profile)
            _require(refs['resource']['sha256'] == charge['evidence_identity']
                and charge['measured_seconds'] == charge['seconds'] == seconds
                and all(value['dispatch_id'] == dispatch['dispatch_id'] for value in (resource, worker, status)),
                'SEGMENT_RESOURCE_BINDING_CONFLICT')
            if compute_identity is None:
                _require(access['dispatch_id'] == dispatch['dispatch_id'] and worker['purpose'] == access['purpose'] == name
                    and all(value['segment_number'] == number for value in (worker, access, resource)), 'ACCOUNT_ACCESS_CONFLICT')
            if access_scope is not None:
                _require(all(access.get(key) == value for key, value in access_scope.items()), 'INPUT_ACCESS_SCOPE_CONFLICT')
            if outputs is not None and status['state'] == 'COMPLETED' and name != 'PREPARATION':
                member = name if compute_identity is None else status.get('member') if name == 'REPORT' else 'verification'
                _require(member in outputs and status.get('result_sha256') == outputs[member]['sha256'], 'COMPUTE_OUTPUT_HASH_CONFLICT')
            _require(access.get('windows_job_verified') is True and access.get('launcher_pid') == worker['pid']
                == access.get('reader_parent_pid') == resource.get('launcher_pid'), 'WORKER_BOUNDARY_CONFLICT')
            _require(resource['returncode'] == (0 if charge['outcome'] == 'COMPLETED' else 75)
                or compute_identity is not None and resource['returncode'] == 0
                and status['state'] == 'COMPLETED', 'WORKER_EXIT_CONFLICT')
            _require(status['state'] == ('COMPLETED' if resource['returncode'] == 0 else 'CONTINUE'), 'WORKER_STATUS_CONFLICT')
            if charge['outcome'] == 'PAUSED': paused.append(status)
            measured += seconds
        charged += charge['seconds']; head = charge['charge_id']
    _require(charged <= profile['total_seconds'], 'CUMULATIVE_BOUND_EXCEEDED')
    return charged, measured, conservative, paused


def _compute(proof, read, budget, count, stage, case, job, preview=None):
    start, aggregate, scope = (read(proof[key]) for key in ('start', 'resource', 'scope'))
    binding = start['binding']; _self_hash(binding, 'compute_identity', 'COMPUTE_HASH_CONFLICT')
    profile = execution_profile(SEGMENTED_PROFILE, count, 'RESEARCH_' + stage)
    _require(binding['stage'] == stage and binding['profile'] == profile, 'COMPUTE_PROFILE_CONFLICT')
    authority = scope['compute_authority'] if stage == 'PREPARATION' else scope['authority']
    request = scope['preview']['request'] if stage == 'PREPARATION' else scope['request']
    _require(binding['authorization_identity'] == stable_hash(authority) and binding['request_identity'] == stable_hash(request)
        and profile in authority['execution_profiles'] and binding['budget_key'] == stable_hash(authority), 'COMPUTE_AUTHORITY_CONFLICT')
    if stage != 'PREPARATION': _require(scope['job_sha256'] == case['job']['sha256'] and scope['stage'] == stage, 'COMPUTE_JOB_CONFLICT')
    outputs = proof['outputs']
    if stage == 'PREPARATION':
        _require(set(outputs) == {'preparation'}, 'PREPARATION_OUTPUT_REQUIRED')
        _self_hash(scope, 'intent_identity', 'SCAN_INTENT_HASH_CONFLICT')
        output = read(outputs['preparation'])
        _self_hash(output, 'scan_identity', 'SCAN_OUTPUT_HASH_CONFLICT')
        scan_receipt, original_input = read(proof['scan_receipt']), read(proof['input'])
        _self_hash(scan_receipt, 'receipt_identity', 'SCAN_RECEIPT_HASH_CONFLICT')
        _require(scope['root'] == case['preparation_root'] and scope['preview'] == preview
            and scan_receipt['intent_identity'] == scope['intent_identity']
            and scan_receipt['scan_identity'] == output['scan_identity']
            and output['scan_id'] == scope['scan_id']
            and output['scope']['preview_identity'] == preview['preview_identity']
            and output['scope']['authorization_identity'] == scope['authorization_identity']
            and scan_receipt['artifacts'][str(Path(scope['root'])/'RESULT.json')]['sha256'] == outputs['preparation']['sha256']
            and scan_receipt['artifacts'][str(Path(scope['root'])/'INPUT.json')]['sha256'] == proof['input']['sha256']
            and _snapshot_material(original_input) == _snapshot_material(read(case['input'])), 'SCAN_ADOPTION_CHAIN_CONFLICT')
        _require(output['status'] == 'QUALIFIED_SCOPE_READY' and output['qualified_account_ready'] is True
            and output['qualified_input_identity'] == job['input_identity']
            and output['qualification_scope'] == read(case['qualification'])
            and output['processed_target_count'] == len(request['symbols']) and output['compute_consumed'] is True,
            'PREPARATION_OUTPUT_SCOPE_CONFLICT')
    elif stage == 'VERIFICATION':
        _require(set(outputs) == {'verification'} and read(outputs['verification']) == read(case['verification']), 'VERIFICATION_OUTPUT_CONFLICT')
    else:
        _require(set(outputs) == set(job['plans']), 'REPORT_OUTPUT_MATRIX_CONFLICT')
        for member, reference in outputs.items():
            output = read(reference); account = case['accounts'][member]
            _require(output['member'] == member and output['research_report_sha256'] == account['report']['sha256']
                and output['funnel_sha256'] == account['funnel']['sha256'], 'REPORT_OUTPUT_CONFLICT')
    _budget(budget, start, 'universe_compute_' + stage.lower() + '_v1', binding['budget_key'])
    access_scope = None if stage == 'PREPARATION' else {'input_identity': job['input_identity'],
        'observation_plan': job['observation_plan'], 'purpose': 'AUTHORIZED_POST_ACCOUNT_EVALUATION'}
    charged, _, _, _ = _segments(proof['segments'], read, profile, None, stage,
        compute_identity=binding['compute_identity'], access_scope=access_scope, outputs=outputs)
    if stage == 'REPORT':
        completed = set()
        for row in proof['segments']:
            if 'status' not in row: continue
            if read(row['charge'])['basis'] != 'MEASURED_ACTIVE_WALL_SECONDS': continue
            status = read(row['status'])
            if status['state'] != 'COMPLETED': continue
            resource, access = read(row['resource']), read(row['access'])
            member = status.get('member')
            _require(member in outputs and resource.get('member') == access.get('member') == member
                and resource['returncode'] == 0, 'REPORT_MEMBER_COMPLETION_CONFLICT')
            completed.add(member)
        _require(completed == set(outputs), 'ALL_REPORT_MEMBERS_REQUIRED')
    total = aggregate['elapsed_wall_seconds'] if stage == 'PREPARATION' else aggregate['charged_seconds']
    _require(total == charged, 'COMPUTE_CUMULATIVE_CONFLICT')


def _validate(repo, receipt, snapshot, read):
    _require(set(receipt) == {'version', 'source_hashes', 'feature_ids', 'frozen', 'real', 'cases', 'self_hash'}, 'FIELDS_INVALID')
    _self_hash(receipt, 'self_hash', 'HASH_CONFLICT')
    _require(receipt['version'] == VERSION and receipt['feature_ids'] == FEATURES, 'VERSION_OR_FEATURES_INVALID')
    sources = receipt['source_hashes']; expected = _snapshot_sources(snapshot)
    _require(all(sources.get(key) == digest for key, digest in expected.items()), 'SOURCE_CLOSURE_CHANGED')
    for relative, digest in sources.items():
        _require(_code_relative(relative) == relative and _hash(digest)
            and _sha(repo / relative) == digest, 'SOURCE_BYTES_CHANGED:' + relative)
    frozen, real = read(receipt['frozen']), read(receipt['real'])
    _self_hash(frozen, 'acceptance_identity', 'FROZEN_HASH_CONFLICT'); _self_hash(real, 'run_identity', 'REAL_HASH_CONFLICT')
    _require(frozen['version'] == real['version'] == ACCEPTANCE_VERSION
        and real['acceptance_identity'] == frozen['acceptance_identity']
        and real['status'] == 'REAL_252_504_ACCOUNT_VERIFIED' and real['strategy_qualified'] is False,
        'REAL_ACCEPTANCE_NOT_ESTABLISHED')
    # 部署登记数据会改变任务指纹；公开查询不重新读取市场或要求相同部署目录。
    # 原任务指纹仍精确绑定原能力快照，当前实现部分必须完全一致。
    for preview in frozen['previews'].values():
        original_snapshot = preview['capabilities']
        _self_hash(original_snapshot, 'fingerprint', 'FROZEN_CAPABILITY_HASH_CONFLICT')
        _require(original_snapshot['fingerprint'] == frozen['capability_fingerprint']
            and {key: value for key, value in original_snapshot.items() if key not in {'fingerprint', 'data'}}
            == {key: value for key, value in snapshot.items() if key not in {'fingerprint', 'data'}}, 'FROZEN_CAPABILITY_IMPLEMENTATION_CHANGED')
    _require(sources['scripts/run_long_horizon_universe_acceptance_v1.py'] == frozen['source_sha256'], 'HARNESS_SOURCE_CHANGED')
    criteria = {'actual_sessions': [252, 504], 'actual_account_purposes': 6, 'shared_cash': 50000,
        'positive_return_required': False, 'qualified_scope': 'ALL_DATA_QUALIFIED',
        'segmented_504_pause_thresholds_per_cost': [80, 251, 390], 'continuous_execution_restore': False,
        'independent_audit_required': True, 'funnel_closed_required': True, 'strategy_qualified': False}
    _require(all(frozen['criteria'].get(key) == value for key, value in criteria.items()), 'CRITERIA_CONFLICT')
    _require(set(real['cases']) == {'252', '504'} and set(frozen['requests']) == {'252', '504'}
        and [case['role'] for case in receipt['cases']] == ['252', '504', 'REFERENCE'], 'SIX_ACCOUNT_MATRIX_REQUIRED')
    results, states, summaries, scopes, jobs = {}, {}, {}, {}, {}
    all_sources = dict(expected)
    for case in receipt['cases']:
        role = case['role']; count = 252 if role == '252' else 504; reference = role == 'REFERENCE'
        job, verification, confirmation, index, budget, scope, input_snapshot = (read(case[key]) for key in
            ('job', 'verification', 'confirmation', 'index', 'budget', 'qualification', 'input'))
        jobs[role], scopes[role] = job, scope
        _require(case['original_job'] == (real['reference_job'] if reference else real['cases'][role]['job_path']), 'JOB_REFERENCE_CONFLICT')
        profile = _profile(job['resources'], count, reference)
        _require(len(job['plans']) == 2 and set(job['items']) == set(job['plans']) == set(case['accounts'])
            == set(verification['items']) == set(index['items']), 'TWO_COSTS_REQUIRED')
        _self_hash(confirmation, 'receipt_id', 'CONFIRMATION_HASH_CONFLICT')
        _require(confirmation['strategy_plans'] == job['plans'] and confirmation['objective_id'] == job['objective_id']
            and confirmation['budget_path'] == job['budget_path'] and confirmation['input_identity'] == job['input_identity'], 'CONFIRMATION_SCOPE_CONFLICT')
        source = confirmation['source']
        _require(source['origin'] == 'USER_EXPLICIT_CURRENT_TASK' and source.get('statement')
            and source['approved_plan_ids'] == {name: plan['plan_id'] for name, plan in job['plans'].items()}
            and source['qualified_scope_identity'] == scope['scope_identity'], 'ACCOUNT_APPROVAL_CONFLICT')
        if reference: _require(source.get('engineering_authorization') == ENGINEERING_PURPOSE, 'ENGINEERING_APPROVAL_REQUIRED')
        _require(verification['job_sha256'] == case['job']['sha256'] and verification['advance_allowed'] is True, 'AUDIT_JOB_CONFLICT')
        _require(budget['objective_id'] == job['objective_id'], 'BUDGET_OBJECTIVE_CONFLICT')
        preview = None if reference else frozen['previews'][role]
        results[role], states[role], summaries[role] = {}, {}, {}
        scenarios = set()
        for name, plan in job['plans'].items():
            _self_hash(plan, 'plan_id', 'PLAN_HASH_CONFLICT')
            backend, item = plan['backend'], job['items'][name]
            _require(plan['runtime'] == item and backend['backend'] == 'UNIVERSE_ACCOUNT_BACKEND_V2'
                and backend['initial_cash'] == 50000 and backend['execution_profile'] == profile
                and item['execution_profile'] == profile, 'PLAN_SCOPE_CONFLICT')
            _require(item['loader_kwargs']['sha256'] == case['input']['sha256']
                and input_snapshot['snapshot_version'] == 'UNIVERSE_FROZEN_INPUT_V1'
                and input_snapshot['input_identity'] == job['input_identity']
                and input_snapshot['window'] == backend['window'] and input_snapshot['bundle']['qualified_scope'] == scope,
                'FROZEN_INPUT_METADATA_CONFLICT')
            for component in (item, plan['strategy'], backend):
                _require(component.get('source_hashes') and all(job['source_hashes'].get(path) == digest
                    for path, digest in component['source_hashes'].items()), 'JOB_SOURCE_COVERAGE_CONFLICT')
            window = backend['window']; days = window['calendar'][window['calendar'].index(window['account_start']):]
            _require(len(days) == count and days[-1] == window['account_end'], 'EXACT_252_504_SESSIONS_REQUIRED')
            scenarios.add(name.rsplit('_', 1)[-1]); refs = case['accounts'][name]
            result, start, settled, resource, report, funnel, checkpoint = (read(refs[key]) for key in
                ('result', 'start', 'settlement', 'resource', 'report', 'funnel', 'checkpoint'))
            result_sha = refs['result']['sha256']; proof = verification['items'][name]
            _require(result['strategy_plan'] == plan and result['input_identity'] == job['input_identity']
                and result['execution_description'] == backend and result['strategy_qualified'] is False
                and result['result_schema'] == 'UNIVERSE_SHARDED_RESULT_V2', 'RESULT_SCOPE_CONFLICT')
            payload = plan['strategy']['parameters']['candidate_payload']
            request = frozen['requests']['504' if reference else role]
            _require(payload == request['rule'] == snapshot['examples']['multi_indicator_ranked']
                and request['account_scope'] == 'DATA_QUALIFIED' and request['costs'] == ['BASE', 'STRESS'], 'FIXED_RULE_CONFLICT')
            _require(proof['status'] == 'PASS' and proof['advance_allowed'] is True and proof['reasons'] == []
                and proof['plan_id'] == plan['plan_id'] and proof['input_identity'] == job['input_identity']
                and proof['result_sha256'] == result_sha == settled['result_sha256'] == index['items'][name]['sha256']
                and proof['evidence_layers']['account_reconciled'] is True
                and proof['evidence_layers']['input_profile'] == 'HISTORICAL_MODELED', 'INDEPENDENT_PASS_NOT_PROVEN')
            audit = proof['account_audit']
            _require(audit['version'] == 'UNIVERSE_EVIDENCE_V2' and audit['input_identity'] == job['input_identity']
                and [row['date'] for row in audit['daily_accounts']] == days and _metrics_match(result['metrics'], audit['metrics'])
                and audit['strategy_qualified'] is False and result['reconciliation']['passed'] is True
                and result['reconciliation']['audit_identity'] == stable_hash(audit), 'AUDIT_CONTENT_NOT_PROVEN')
            _require(start['kind'] == name and start['receipt_id'] == confirmation['receipt_id']
                and start['counted_before_account_calculation'] is True
                and all(settled.get(key) == value for key, value in start.items())
                and settled['completed'] is True and settled['error'] is None, 'SETTLEMENT_CONFLICT')
            bucket = _budget(budget, start, 'strategy_interface_account_v1', confirmation['receipt_id'] + ':' + name)
            _require(bucket['used'] == 1 and bucket['limit'] == 1, 'ACCOUNT_CONSUMPTION_CONFLICT')
            charged, measured, conservative, paused = _segments(refs['segments'], read, profile, confirmation['receipt_id'], name,
                access_scope={'input_identity': job['input_identity'], 'loader': item['loader'], 'loader_kwargs': item['loader_kwargs']},
                outputs={name: refs['result']})
            if reference:
                _require(len(refs['segments']) == 1 and conservative == 0 and not paused and not case['controls'],
                    'CONTINUOUS_REFERENCE_MUST_NOT_RESTORE')
            _require(resource['returncode'] == 0 and resource['timed_out'] is False
                and resource['active_metering'] is True and resource['segment_count'] == len(refs['segments'])
                and resource['elapsed_wall_seconds'] == settled['wall_seconds'] == charged
                and resource['segments'] == [read(row['charge'])['charge_id'] for row in refs['segments']]
                and proof['resource_accounting'] == {'charged_seconds': charged, 'measured_seconds': measured,
                    'conservatively_charged_seconds': conservative}, 'ACCOUNT_CUMULATIVE_CONFLICT')
            _self_hash(checkpoint, 'state_identity', 'CHECKPOINT_HASH_CONFLICT')
            _require(checkpoint['last_day'] == days[-1] and checkpoint['execution_identity'] == result['execution_identity'], 'FINAL_STATE_CONFLICT')
            manifest = result['artifacts']; _self_hash(manifest, 'manifest_identity', 'ARTIFACT_MANIFEST_CONFLICT')
            head = stable_hash(['UNIVERSE_EXECUTION_ARTIFACTS_V1', manifest['identity']])
            for day in manifest['days']:
                _self_hash(day, 'chain', 'DAY_CHAIN_CONFLICT')
                _require(day['previous'] == head and _hash(day['payload_identity']) and _hash(day['sha256']), 'DAY_IDENTITY_CONFLICT')
                head = day['chain']
            _require([row['date'] for row in manifest['days']] == days and head == manifest['head']
                and checkpoint['artifacts'] == manifest, 'DAY_COVERAGE_CONFLICT')
            _self_hash(report, 'report_identity', 'REPORT_HASH_CONFLICT'); _self_hash(funnel, 'identity', 'FUNNEL_HASH_CONFLICT')
            _require(report['input_identity'] == job['input_identity'] and report['strategy_qualified'] is False
                and report['paper_qualified'] is False and funnel['strategy_id'] == name
                and funnel['rule_identity'] == plan['strategy']['parameters']['rule_identity'], 'REPORT_SCOPE_CONFLICT')
            _require(report['account']['sessions'] == count and report['account']['initial_cash'] == 50000
                and report['account']['reconciliation'] == result['reconciliation']
                and report['signal']['scope_count'] == len(window['symbols'])
                and report['signal']['observation_plan'] == job['observation_plan']
                and report['signal']['observation_plan_identity'] == stable_hash(job['observation_plan'])
                and report['signal']['account_independent_denominator'] is True,
                'DUAL_REPORT_NOT_ESTABLISHED')
            counts = funnel['counts']
            _require(funnel['version'] == 'UNIVERSE_SIGNAL_FUNNEL_V1' and funnel['mode'] == 'DAY_STREAM'
                and funnel['calendar_identity'] == stable_hash(window['calendar'])
                and counts['scan_rows'] == count * len(window['symbols'])
                and counts['opportunity_signals'] == sum(funnel['opportunity_dispositions'].values())
                and counts['fills'] == len(result['fills']) and _hash(funnel['detail_chain_identity'])
                and counts['orders'] == len(result['final_account_checkpoint']['economic']['orders'])
                and all(counts[key] == sum(funnel['layer_counts_by_side'][side][key] for side in ('BUY', 'SELL'))
                    for key in ('intents', 'orders', 'fills')), 'FUNNEL_CLOSURE_NOT_ESTABLISHED')
            summary = _scope(scope, preview, job['input_identity'], window)
            results[role][name], states[role][name], summaries[role][name] = result, checkpoint, summary
            if role == '504':
                interrupts = [row for row in real['cases']['504']['interrupts'] if row['purpose'] == name]
                _require([row['threshold'] for row in interrupts] == [80, 251, 390] and len(paused) == 3, 'THREE_ACTUAL_PAUSES_REQUIRED')
                controls = [read(reference) for reference in case['controls']]
                control_head = None
                for sequence, control in enumerate(controls, 1):
                    _self_hash(control, 'event_id', 'CONTROL_HASH_CONFLICT')
                    _require(control['sequence'] == sequence and control['previous_head'] == control_head
                        and control['job_sha256'] == case['job']['sha256'] and control['receipt_id'] == confirmation['receipt_id']
                        and control['event'] in {'PAUSE', 'RESUME'}, 'CONTROL_CHAIN_CONFLICT')
                    control_head = control['event_id']
                for number, row in enumerate(interrupts):
                    _require(row['control']['status'] == 'PAUSE_REQUESTED'
                        and any(control['event'] == 'PAUSE' and control['event_id'] == row['control']['control_identity'] for control in controls),
                        'ACTUAL_PAUSE_CONTROL_NOT_PROVEN')
                    _require(row['threshold'] <= row['committed_days_at_request'] < ([251, 390, 504][number])
                        and _hash(row['state_identity']) and days.index(paused[number]['last_day']) + 1 >= row['committed_days_at_request'],
                        'ACTUAL_PAUSE_BOUNDARY_CONFLICT')
        _require(scenarios == {'BASE', 'STRESS'}, 'BASE_AND_STRESS_REQUIRED')
        for path, digest in job['source_hashes'].items():
            relative = _code_relative(path)
            if relative is None: continue
            _require(all_sources.get(relative, digest) == digest and sources.get(relative) == digest, 'JOB_SOURCE_CLOSURE_CONFLICT')
            all_sources[relative] = digest
        for stage, proof in case['compute'].items():
            _compute(proof, read, budget, count, stage, case, job, preview)
        _require(set(case['compute']) == ({'VERIFICATION', 'REPORT'} if reference else {'PREPARATION', 'VERIFICATION', 'REPORT'}),
            'ALL_COMPUTE_PURPOSES_REQUIRED')
    _require(set(results['504']) == set(results['REFERENCE']) == set(real['comparisons']), 'REFERENCE_COST_MATRIX_CONFLICT')
    _require(jobs['504']['input_identity'] == jobs['REFERENCE']['input_identity'] and scopes['504'] == scopes['REFERENCE'], 'REFERENCE_INPUT_CONFLICT')
    for name, left in results['504'].items():
        right, comparison = results['REFERENCE'][name], real['comparisons'][name]
        _require(comparison['passed'] is True and comparison['sessions'] == 504
            and all(left[key] == right[key] for key in ECONOMIC_FIELDS)
            and comparison['economic_identity'] == stable_hash({key: left[key] for key in ECONOMIC_FIELDS}), 'EXACT_ECONOMIC_COMPARISON_REQUIRED')
        daily = [row['payload_identity'] for row in left['artifacts']['days']]
        _require(daily == [row['payload_identity'] for row in right['artifacts']['days']]
            and comparison['daily_content_identity'] == stable_hash(daily), 'EXACT_DAILY_COMPARISON_REQUIRED')
        material = {key: states['504'][name][key] for key in STATE_FIELDS}
        _require(material == {key: states['REFERENCE'][name][key] for key in STATE_FIELDS}
            and comparison.get('full_engine_state_identity') == stable_hash(material), 'FULL_ENGINE_STATE_COMPARISON_REQUIRED')
    _require(set(sources) == set(all_sources) | {'scripts/run_long_horizon_universe_acceptance_v1.py',
        'src/chanlun_trader/research_factory/long_horizon_acceptance_publication_v1.py'}, 'SOURCE_CLOSURE_INCOMPLETE')
    return {'status': 'PUBLISHED_METADATA_VERIFIED', 'version': VERSION, 'feature_ids': FEATURES,
        'evidence_fingerprint': receipt['self_hash'], 'source_fingerprint': stable_hash(sources), 'account_count': 6,
        'actual_sessions': [252, 504], 'segmented_accounts': 4, 'continuous_reference_accounts': 2,
        'initial_cash': 50000, 'coverage': {key: next(iter(summaries[key].values())) for key in ('252', '504')},
        'assurance': BOUNDARY, 'strategy_qualified': False, 'independent_validation': 'NOT_RUN', 'paper': 'NOT_RUN'}


def _reference(path):
    relative = PurePosixPath(path)
    _require(not relative.is_absolute() and '..' not in relative.parts and '\\' not in path and ':' not in path
        and Path(path).is_relative_to(METADATA) and relative.suffix == '.json', 'METADATA_REFERENCE_INVALID')
    return Path(path)


def published_long_horizon_acceptance(repo, snapshot):
    """只验证复制的 JSON 与当前源码闭包；不跟随其行情或每日明细路径。"""
    try:
        repo = Path(repo).absolute(); _require(repo.resolve() == repo, 'REPO_REDIRECTED')
        receipt = _json(repo / PREFIX / 'PUBLISHED_ACCEPTANCE.json')
        def read(ref):
            _require(set(ref) == {'path', 'sha256'} and _hash(ref['sha256']), 'ARTIFACT_REFERENCE_INVALID')
            path = repo / _reference(ref['path'])
            _require(_sha(path) == ref['sha256'], 'ARTIFACT_HASH_CONFLICT')
            return _json(path)
        return _validate(repo, receipt, snapshot, read)
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        return {'status': 'NOT_ACCEPTED', 'version': VERSION, 'reason': str(exc), 'feature_ids': [],
            'evidence_fingerprint': None, 'assurance': BOUNDARY, 'strategy_qualified': False}


def publish_long_horizon_acceptance(repo, acceptance_root):
    """维护者仅发布已有闭合证据；原文件缺失或矛盾时不生成通过凭证。"""
    from .research_capabilities_v1 import capabilities
    repo, root = Path(repo).absolute(), Path(acceptance_root).absolute()
    _require(repo.resolve() == repo and root.resolve() == root, 'ROOT_REDIRECTED')
    snapshot = capabilities(); sources = _snapshot_sources(snapshot); raw_files = {}; original_files = {}
    def ref(original, relative):
        original = _file(original); raw = original.read_bytes(); json.loads(raw)
        path = (METADATA / relative).as_posix(); _reference(path)
        _require(path not in raw_files or raw_files[path] == raw, 'COPY_REFERENCE_CONFLICT')
        raw_files[path] = raw; original_files[path] = original
        return {'path': path, 'sha256': hashlib.sha256(raw).hexdigest()}
    def code(path, digest):
        relative = _code_relative(path)
        if relative is None: return
        _require(_sha(path) == digest == _sha(repo / relative), 'ORIGINAL_OR_CURRENT_SOURCE_CHANGED:' + relative)
        _require(sources.get(relative, digest) == digest, 'SOURCE_CLOSURE_CONFLICT')
        sources[relative] = digest
    frozen_ref = ref(root/'FROZEN_ACCEPTANCE.json', Path('FROZEN_ACCEPTANCE.json'))
    real_ref = ref(root/'REAL_ACCEPTANCE.json', Path('REAL_ACCEPTANCE.json'))
    real = _json(root/'REAL_ACCEPTANCE.json'); cases = []
    for role in ('252', '504', 'REFERENCE'):
        original_job = Path(real['reference_job'] if role == 'REFERENCE' else real['cases'][role]['job_path'])
        job = _json(original_job); folder = original_job.parent; base = Path(role)
        case = {'role': role, 'original_job': str(original_job),
            **{key: ref(folder/name, base/name) for key, name in
                (('job', 'JOB.json'), ('verification', 'VERIFICATION.json'), ('confirmation', 'CONFIRMATION.json'), ('index', 'RESULTS_INDEX.json'))},
            'budget': ref(job['budget_path'], base/'BUDGET.json'), 'accounts': {}, 'compute': {},
            'controls': [ref(path, base/path.name) for path in sorted(folder.glob('CONTROL_*.json'))]}
        input_root = Path(next(iter(job['items'].values()))['loader_kwargs']['path']).parent
        case['input'] = ref(input_root/'INPUT.json', base/'INPUT.json')
        case['qualification'] = ref(input_root/'QUALIFICATION_SCOPE.json', base/'QUALIFICATION_SCOPE.json')
        preparation_root = None if role == 'REFERENCE' else Path(real['cases'][role]['preparation_root'])
        if preparation_root is not None: case['preparation_root'] = str(preparation_root)
        for path, digest in job['source_hashes'].items(): code(path, digest)
        def segment_refs(physical, number, *, account=None, preparation=False):
            prefix = account + '_SEGMENT_' if account else 'PREPARE_' if preparation else 'SEGMENT_'
            prefix += str(number).zfill(6)
            dispatch_prefix = prefix if account else 'COMPUTE_SEGMENT_' + str(number).zfill(6)
            output = {}
            for key, suffix in (('dispatch', '_DISPATCH.json'), ('charge', '_CHARGE.json'), ('resource', '_RESOURCE.json'),
                    ('worker', '_WORKER.json'), ('access', '_INPUT_ACCESS.json' if account else '_ACCESS.json'),
                    ('status', '_STATUS.json'), ('resume', '_RESUME.json')):
                path = physical/(dispatch_prefix+suffix) if key in ('dispatch', 'charge') else (physical.parent if preparation else physical)/(prefix+suffix)
                if path.exists(): output[key] = ref(path, base/('segments_'+(account or ('PREPARATION' if preparation else physical.name)))/path.name)
            return output
        for name, item in job['items'].items():
            account_base = base/name
            refs = {key: ref(folder/(name+suffix), account_base/(name+suffix)) for key, suffix in
                (('result', '_RESULT.json'), ('start', '_START.json'), ('settlement', '_SETTLEMENT.json'),
                 ('resource', '_RESOURCE.json'), ('report', '_RESEARCH_REPORT.json'), ('funnel', '_SIGNAL_FUNNEL.json'))}
            checkpoint = Path(item['backend_options']['checkpoint_path'])
            refs['checkpoint'] = ref(checkpoint, account_base/checkpoint.name)
            refs['segments'] = [segment_refs(folder, number, account=name) for number, _ in enumerate(sorted(folder.glob(name+'_SEGMENT_*_DISPATCH.json')), 1)]
            case['accounts'][name] = refs
        for stage in (('VERIFICATION', 'REPORT') if role == 'REFERENCE' else ('PREPARATION', 'VERIFICATION', 'REPORT')):
            physical = preparation_root/'COMPUTE' if stage == 'PREPARATION' else folder/('COMPUTE_'+stage)
            scope = preparation_root/'SCAN_INTENT.json' if stage == 'PREPARATION' else physical/'SCOPE.json'
            resource = preparation_root/'RESOURCE.json' if stage == 'PREPARATION' else physical/'RESOURCE_TOTAL.json'
            stage_base = base/('compute_'+stage)
            case['compute'][stage] = {'start': ref(physical/'COMPUTE_START.json', stage_base/'COMPUTE_START.json'),
                'scope': ref(scope, stage_base/scope.name), 'resource': ref(resource, stage_base/resource.name),
                'outputs': ({'preparation': ref(preparation_root/'RESULT.json', stage_base/'RESULT.json')} if stage == 'PREPARATION' else
                    {'verification': ref(physical/'RESULT.json', stage_base/'RESULT.json')} if stage == 'VERIFICATION' else
                    {name: ref(physical/('RESULT_'+name+'.json'), stage_base/('RESULT_'+name+'.json')) for name in job['plans']}),
                'segments': [segment_refs(physical, number, preparation=stage=='PREPARATION')
                    for number, _ in enumerate(sorted(physical.glob('COMPUTE_SEGMENT_*_DISPATCH.json')), 1)]}
            if stage == 'PREPARATION':
                case['compute'][stage].update(scan_receipt=ref(preparation_root/'SCAN_RECEIPT.json', stage_base/'SCAN_RECEIPT.json'),
                    input=ref(preparation_root/'INPUT.json', stage_base/'INPUT.json'))
        cases.append(case)
    for relative in ('scripts/run_long_horizon_universe_acceptance_v1.py',
            'src/chanlun_trader/research_factory/long_horizon_acceptance_publication_v1.py'):
        sources[relative] = _sha(repo/relative)
    receipt = {'version': VERSION, 'source_hashes': dict(sorted(sources.items())), 'feature_ids': FEATURES,
        'frozen': frozen_ref, 'real': real_ref, 'cases': cases}
    receipt['self_hash'] = stable_hash(receipt)
    _validate(repo, receipt, snapshot, lambda reference: json.loads(raw_files[reference['path']]))
    for relative, raw in raw_files.items():
        # 复制前再次确认原件，避免检查和发布之间被替换。
        _require(original_files[relative].read_bytes() == raw, 'ORIGINAL_CHANGED_DURING_PUBLICATION')
        _write(repo/relative, raw)
    _write(repo/PREFIX/'PUBLISHED_ACCEPTANCE.json', (json.dumps(receipt, ensure_ascii=False, indent=2)+'\n').encode('utf-8'))
    return published_long_horizon_acceptance(repo, snapshot)


def _write(path, raw):
    path = Path(path).absolute(); _require(path.resolve() == path, 'COPY_PATH_REDIRECTED')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _require(path.read_bytes() == raw, 'EXISTING_PUBLICATION_CONFLICT')
        return
    with path.open('xb') as stream:
        stream.write(raw)
