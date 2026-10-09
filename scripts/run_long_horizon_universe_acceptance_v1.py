"""维护者的252/504日固定规则验收；只引用已登记授权，不搜索盈利参数。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for local in (ROOT, ROOT / 'src'):
    if str(local) not in sys.path:
        sys.path.insert(0, str(local))

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.exploration_governance import immutable, read_json
from chanlun_trader.research_factory.universe_execution_artifacts_v1 import ArtifactSequence
from chanlun_trader.research_factory.universe_execution_profile_v1 import (
    CONTINUOUS_PROFILE, ENGINEERING_PURPOSE, SEGMENTED_PROFILE, execution_profile,
)
from chanlun_trader.research_factory.universe_research_report_v2 import default_observation_plan

VERSION = 'LONG_HORIZON_UNIVERSE_ACCEPTANCE_V1'
CONFIG_VERSION = 'LONG_HORIZON_UNIVERSE_ACCEPTANCE_CONFIG_V1'


def fixed_requests(config, snapshot):
    fields = {'version', 'windows', 'authorization_refs', 'engineering_authorization_ref'}
    if (not isinstance(config, dict) or set(config) != fields or config['version'] != CONFIG_VERSION
            or set(config['windows']) != {'252', '504'} or set(config['authorization_refs']) != {'252', '504'}
            or not config['engineering_authorization_ref']):
        raise ValueError('LONG_ACCEPTANCE_CONFIG_INVALID')
    rule = deepcopy(snapshot['examples']['multi_indicator_ranked'])
    if {item['id'] for item in rule['indicator_instances']} != {'MA', 'RSI', 'ROLLING_VOLATILITY'}:
        raise ValueError('LONG_ACCEPTANCE_FIXED_MECHANISM_CHANGED')
    requests = {}
    for count in (252, 504):
        window = config['windows'][str(count)]
        if set(window) != {'dataset_id', 'universe_id', 'feature_start', 'account_start', 'account_end'}:
            raise ValueError('LONG_ACCEPTANCE_WINDOW_INVALID')
        requests[str(count)] = {'version': 'FULL_UNIVERSE_SUBMISSION_V3', 'strategy_id': 'LONG_U' + str(count),
            'rule': deepcopy(rule), **deepcopy(window), 'account_scope': 'DATA_QUALIFIED',
            'initial_cash': 50000, 'max_positions': 2, 'max_symbol_exposure_bps': 5000,
            'costs': ['BASE', 'STRESS'], 'benchmark': 'CASH_AND_PRICE_REFERENCE', 'purpose': 'EXPLORATORY',
            'authorization_ref': config['authorization_refs'][str(count)],
            'execution_profile': execution_profile(SEGMENTED_PROFILE, count),
            'observation_plan': default_observation_plan(window)}
    return requests


def reference_config(primary_job, *, root, objective_id, budget_path):
    """只改变计算规格和新用途目录；证券、规则、成交模型与输入原件完全继承。"""
    if (primary_job['resources']['profile_id'] != SEGMENTED_PROFILE
            or primary_job['resources']['account_sessions'] != 504
            or len(primary_job['items']) != 2):
        raise ValueError('LONG_ACCEPTANCE_PRIMARY_504_REQUIRED')
    profile = execution_profile(CONTINUOUS_PROFILE, 504, ENGINEERING_PURPOSE)
    items = []
    for name, original in primary_job['items'].items():
        item = deepcopy(original)
        item['execution_profile'] = profile
        item['backend_options'].update(execution_profile=profile,
            checkpoint_path=str(Path(root) / 'account' / (name + '_CHECKPOINT.json')))
        items.append(item)
    return {'items': items, 'objective_id': objective_id, 'budget_path': budget_path,
        'input_identity': primary_job['input_identity'], 'benchmark_mode': primary_job['benchmark_mode'],
        'execution_profile': profile, 'observation_plan': primary_job['observation_plan']}


def mechanism_requests(request, snapshot):
    """只读预检三个预先固定的机制；不会为了验收增加账户或搜索参数。"""
    node = lambda op, args=(), params=None: {'op': op, 'args': list(args), 'params': params or {}}
    rule = request['rule']
    rsi = next(item for item in rule['indicator_instances'] if item['id'] == 'RSI')
    definition = next(item for item in snapshot['rules']['indicators'] if item['id'] == 'RSI')
    value = node('indicator', [rsi['instance_id']], {'output': definition['outputs'][0], 'version': rsi['version']})
    variants = {'trend': deepcopy(request), 'reversal': deepcopy(request), 'breakout': deepcopy(request)}
    variants['reversal']['rule']['buy'] = node('and', [
        node('lt', [deepcopy(value), node('const', params={'value': 30})]),
        node('gt', [node('field', ['close']), node('field', ['open'])])])
    variants['reversal']['rule']['hypothesis'] = '深度超卖后收阳确认；固定机制只预检，不声称盈利'
    variants['breakout']['rule']['buy'] = node('and', [
        node('gt', [deepcopy(value), node('const', params={'value': 55})]),
        node('cross_up', [node('field', ['close']), node('ref', [node('field', ['close'])], {'periods': 20})])])
    variants['breakout']['rule']['hypothesis'] = '较强RSI配合价格突破过去价格；固定机制只预检，不声称盈利'
    for name, variant in variants.items():
        variant['strategy_id'] = 'LONG_PREVIEW_' + name.upper()
    return variants


def reference_source(authority, primary, request):
    """连续对照须继承同一精确账户范围，并另获工程用途许可。"""
    if authority.get('engineering_authorization') != ENGINEERING_PURPOSE:
        raise PermissionError('LONG_ACCEPTANCE_REFERENCE_AUTHORITY_REQUIRED')
    from chanlun_trader.research_factory.universe_scan_service_v1 import _authorize
    _authorize(authority, request)
    permit = authority.get('account_authorization', {})
    exact = ('initial_cash', 'symbols', 'feature_start', 'account_start', 'account_end',
             'max_positions', 'max_symbol_exposure_bps', 'costs', 'benchmark', 'observation_plan')
    if (any(permit.get(key) != request[key] for key in exact)
            or permit.get('rule_identity') != next(iter(primary['plans'].values()))['strategy']['parameters']['rule_identity']
            or permit.get('execution_profile') != execution_profile(CONTINUOUS_PROFILE, 504, ENGINEERING_PURPOSE)
            or permit.get('max_account_jobs') != 2 or permit.get('purpose') != 'FROZEN_PUBLIC_ACCOUNT_PLANS'):
        raise PermissionError('LONG_ACCEPTANCE_REFERENCE_SCOPE_OUTSIDE_AUTHORITY')
    source_input = Path(next(iter(primary['items'].values()))['loader_kwargs']['path'])
    scope = read_json(source_input.parent / 'QUALIFICATION_SCOPE.json')
    return {**authority['source'], 'expires_at': authority['expires_at'],
            'engineering_authorization': ENGINEERING_PURPOSE, 'authorization_identity': stable_hash(authority),
            'qualified_scope_identity': scope['scope_identity']}


def compare_accounts(segmented, reference):
    """比较经济与决策内容，不把不同计算用途的身份/耗时当作收益差异。"""
    if (segmented['input_identity'] != reference['input_identity']
            or segmented['execution_description']['execution_profile']['account_sessions'] != 504
            or reference['execution_description']['execution_profile']['purpose'] != ENGINEERING_PURPOSE):
        raise ValueError('LONG_ACCEPTANCE_COMPARISON_SCOPE_CONFLICT')
    fields = ('fills', 'metrics', 'final_account_checkpoint', 'scanner_identity', 'scan_preparation', 'account_policy')
    for field in fields:
        if stable_hash(segmented[field]) != stable_hash(reference[field]):
            raise ValueError('LONG_ACCEPTANCE_CONTINUOUS_CONFLICT:' + field)
    left, right = ArtifactSequence(segmented['artifacts'], 'account'), ArtifactSequence(reference['artifacts'], 'account')
    if len(left) != 504 or len(right) != 504:
        raise ValueError('LONG_ACCEPTANCE_504_DAYS_REQUIRED')
    for index in range(504):
        if left.manifest['days'][index]['date'] != right.manifest['days'][index]['date']:
            raise ValueError('LONG_ACCEPTANCE_DATES_CONFLICT')
        if stable_hash(left.read_day(index)) != stable_hash(right.read_day(index)):
            raise ValueError('LONG_ACCEPTANCE_DAY_CONTENT_CONFLICT:' + str(index))
    # 经济导出会规范化成交字段；另外检查完整收盘状态，覆盖broker计数及原始fill_id。
    states = [read_json(Path(result['strategy_plan']['runtime']['backend_options']['checkpoint_path']))
              for result in (segmented, reference)]
    for state in states:
        if state.get('state_identity') != stable_hash({key: value for key, value in state.items() if key != 'state_identity'}):
            raise ValueError('LONG_ACCEPTANCE_ENGINE_STATE_IDENTITY_CONFLICT')
    state_fields = ('ledger', 'engine', 'orders', 'order_counters', 'broker_fill_counter', 'events',
                    'event_counter', 'rule_states', 'rule_exits', 'bars', 'states', 'skips',
                    'allocations', 'decisions', 'peak', 'drawdown', 'last_day', 'calendar_identity')
    for field in state_fields:
        if stable_hash(states[0][field]) != stable_hash(states[1][field]):
            raise ValueError('LONG_ACCEPTANCE_ENGINE_STATE_CONFLICT:' + field)
    return {'passed': True, 'sessions': 504, 'economic_identity': stable_hash({key: segmented[key] for key in fields}),
            'daily_content_identity': stable_hash([row['payload_identity'] for row in left.manifest['days']]),
            'full_engine_state_identity': stable_hash({key: states[0][key] for key in state_fields})}


def check_funnel(result, funnel):
    """从实际账户原件核对各层数量；条件机会不能由持仓或评分缩小。"""
    counts = funnel['counts']
    window = result['execution_description']['window']
    expected = len(result['artifacts']['days']) * len(window['symbols'])
    orders = result['final_account_checkpoint']['economic']['orders']
    if (counts.get('scan_rows') != expected or counts.get('fills') != len(result['fills'])
            or counts.get('orders') != len(orders)
            or counts.get('opportunity_signals') != sum(funnel['opportunity_dispositions'].values())
            or sum(row['orders'] for row in funnel['layer_counts_by_side'].values()) != len(orders)
            or sum(row['fills'] for row in funnel['layer_counts_by_side'].values()) != len(result['fills'])):
        raise ValueError('LONG_ACCEPTANCE_FUNNEL_QUANTITY_CONFLICT')
    return {'passed': True, 'expected_scan_rows': expected, **counts}


def resource_summary(rows, reference):
    """汇总实际 Windows 宿主计量；不把暂停时间或短窗外推当成实测。"""
    scopes = {}
    evidence_dirs = set()
    for role, row in {**rows, 'REFERENCE': {'job_path': str(Path(reference['root']) / 'JOB.json')}}.items():
        job = reference if role == 'REFERENCE' else read_json(Path(row['job_path']))
        folder = Path(job['root'])
        evidence_dirs.add(folder.parent if role != 'REFERENCE' else folder)
        for name in job['plans']:
            resources = [read_json(path) for path in sorted(folder.glob(name + '_SEGMENT_*_RESOURCE.json'))]
            total = read_json(folder / (name + '_RESOURCE.json'))
            scopes[role + '/' + name] = {'kind': 'ACCOUNT', 'account_sessions': job['resources']['account_sessions'],
                'profile': job['resources'], 'charged_seconds': total['elapsed_wall_seconds'],
                'segments': len(resources), 'peak_memory_mib': max(item['peak_memory_mib'] for item in resources),
                'memory_measurement': sorted({item['memory_measurement'] for item in resources})}
            result = read_json(folder / (name + '_RESULT.json'))
            check_funnel(result, read_json(folder / (name + '_SIGNAL_FUNNEL.json')))
        stages = {'VERIFICATION': folder / 'COMPUTE_VERIFICATION', 'REPORT': folder / 'COMPUTE_REPORT'}
        if role != 'REFERENCE':
            preparation = Path(row['preparation_root'])
            evidence_dirs.add(preparation)
            stages['PREPARATION'] = preparation / 'COMPUTE'
        for stage, physical in stages.items():
            resources = [read_json(path) for path in sorted((physical.parent if stage == 'PREPARATION' else physical).glob(
                'PREPARE_*_RESOURCE.json' if stage == 'PREPARATION' else 'SEGMENT_*_RESOURCE.json'))]
            total = read_json(physical.parent / 'RESOURCE.json' if stage == 'PREPARATION' else physical / 'RESOURCE_TOTAL.json')
            scopes[role + '/' + stage] = {'kind': stage, 'charged_seconds': total.get('charged_seconds', total.get('elapsed_wall_seconds')),
                'segments': len(resources), 'peak_memory_mib': max(item['peak_memory_mib'] for item in resources),
                'memory_measurement': sorted({item['memory_measurement'] for item in resources})}
    seen = set()
    disk_bytes = 0
    for folder in evidence_dirs:
        for path in folder.rglob('*'):
            if path.is_file() and str(path) not in seen:
                seen.add(str(path)); disk_bytes += path.stat().st_size
    return {'version': 'LONG_HORIZON_REAL_RESOURCE_SUMMARY_V1', 'scopes': scopes,
            'charged_seconds': sum(item['charged_seconds'] for item in scopes.values()),
            'evidence_disk_bytes': disk_bytes, 'evidence_file_count': len(seen),
            'excludes': ['external_data_acquisition', 'native_source_preparation', 'paused_idle_time'],
            'disk_roots': sorted(str(path) for path in evidence_dirs)}


def _public_execute(service, task, *, workspace, deployment, output, interrupt):
    """由真实公共CLI执行；维护者仅在已冻结阈值提出安全暂停，再沿原任务恢复。"""
    job = read_json(task['job_path'])
    completed_path = Path(output) / 'EXECUTION.json'
    if completed_path.exists():
        return read_json(completed_path)
    requests = {name: [80, 251, 390] for name in job['plans']} if interrupt else {}
    records = [read_json(path) for path in sorted(Path(output).glob('INTERRUPT_*.json'))]
    for row in records:
        if row['purpose'] not in requests or not requests[row['purpose']] or requests[row['purpose']].pop(0) != row['threshold']:
            raise ValueError('LONG_ACCEPTANCE_INTERRUPT_HISTORY_CONFLICT')
    run_number = len(list(Path(output).glob('PUBLIC_*.log')))
    operation = 'resume' if run_number else 'start'
    while True:
        run_number += 1
        log = Path(output) / ('PUBLIC_' + str(run_number).zfill(3) + '.log')
        with log.open('xb') as stream:
            process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/run_trusted_research_v1.py'), operation,
                '--workspace-root', str(workspace), '--deployment', str(deployment), '--task-id', task['task_id']],
                cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
            sent = False
            while process.poll() is None:
                if not sent:
                    for name, thresholds in requests.items():
                        checkpoint = Path(job['items'][name]['backend_options']['checkpoint_path'])
                        if (not thresholds or not checkpoint.exists()
                                or (Path(job['root']) / (name + '_SETTLEMENT.json')).exists()):
                            continue
                        state = read_json(checkpoint)
                        elapsed_days = len(state['artifacts']['days'])
                        if elapsed_days >= thresholds[0] and elapsed_days < 504:
                            threshold = thresholds.pop(0)
                            paused = service.pause(task['task_id'])
                            record = {'purpose': name, 'threshold': threshold, 'committed_days_at_request': elapsed_days,
                                      'state_identity': state['state_identity'], 'control': paused}
                            immutable(Path(output) / ('INTERRUPT_' + str(len(records) + 1).zfill(3) + '.json'), record)
                            records.append(record)
                            sent = True
                            break
                time.sleep(.5)
        if process.returncode != 0:
            raise RuntimeError('LONG_ACCEPTANCE_PUBLIC_EXECUTION_FAILED:' + str(log))
        status = service.status(task['task_id'])
        if sent:
            operation = 'resume'
            continue
        if interrupt and any(requests.values()):
            raise ValueError('LONG_ACCEPTANCE_ACTUAL_INTERRUPTS_NOT_COMPLETED')
        evidence = read_json(Path(job['root']) / 'VERIFICATION.json')
        if not evidence['advance_allowed'] or any(item['status'] != 'PASS' for item in evidence['items'].values()):
            raise ValueError('LONG_ACCEPTANCE_PUBLIC_AUDIT_FAILED')
        for name in job['plans']:
            report = read_json(Path(job['root']) / (name + '_RESEARCH_REPORT.json'))
            funnel = read_json(Path(job['root']) / (name + '_SIGNAL_FUNNEL.json'))
            if report['strategy_qualified'] or not funnel['identity']:
                raise ValueError('LONG_ACCEPTANCE_PUBLIC_REPORT_CONFLICT')
            check_funnel(read_json(Path(job['root']) / (name + '_RESULT.json')), funnel)
        value = {'status': status, 'interrupts': records, 'public_runs': run_number, 'advance_allowed': True}
        immutable(completed_path, value)
        return value


def run_acceptance(service, config, *, output_root, workspace, deployment, execute=False):
    root = Path(output_root).absolute()
    if root.resolve() != root or not root.is_relative_to(Path(workspace).absolute()):
        raise ValueError('LONG_ACCEPTANCE_OUTPUT_OUTSIDE_WORKSPACE')
    snapshot = service.capabilities()
    requests = fixed_requests(config, snapshot)
    previews = {key: service.preview(request) for key, request in requests.items()}
    mechanisms = {key: service.preview(request) for key, request in mechanism_requests(requests['504'], snapshot).items()}
    if len({item['rule_identity'] for item in mechanisms.values()}) != 3:
        raise ValueError('LONG_ACCEPTANCE_THREE_DISTINCT_MECHANISMS_REQUIRED')
    for preview in previews.values():
        if (preview['request']['symbols'] != sorted(preview['data_metadata']['target_symbols'])
                or any(preview['data_metadata']['by_board'][board]['target_count'] < 1
                       for board in ('SZ_MAIN', 'SH_MAIN', 'CHINEXT'))):
            raise ValueError('LONG_ACCEPTANCE_THREE_BOARD_FULL_TARGET_REQUIRED')
    plan = {'version': VERSION, 'config': config, 'requests': requests, 'previews': previews, 'mechanism_previews': mechanisms,
        'capability_fingerprint': snapshot['fingerprint'], 'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'criteria': {'actual_sessions': [252, 504], 'actual_account_purposes': 6, 'shared_cash': 50000,
            'positive_return_required': False, 'qualified_scope': 'ALL_DATA_QUALIFIED',
            'segmented_504_pause_thresholds_per_cost': [80, 251, 390], 'continuous_execution_restore': False,
            'independent_audit_required': True, 'funnel_closed_required': True, 'strategy_qualified': False}}
    plan['acceptance_identity'] = stable_hash(plan)
    immutable(root / 'FROZEN_ACCEPTANCE.json', plan)
    if not execute:
        return {'status': 'FROZEN_REQUIRES_EXECUTION', 'acceptance_identity': plan['acceptance_identity']}
    rows = {}
    for count, request in requests.items():
        preview = previews[count]
        authority = service.authority(request['authorization_ref'])
        task_id = stable_hash({'preview': preview['preview_identity'], 'objective_id': authority['objective_id']})
        task = service._task(task_id) if (service.root / task_id / 'TASK.json').exists() else service.freeze(request, preview['preview_identity'])
        summary = service.approval_preview(task['task_id'])
        if summary['input_identity'] != task['input_identity'] or len(summary['plan_ids']) != 2:
            raise ValueError('LONG_ACCEPTANCE_FROZEN_SCOPE_CHANGED')
        service.approve(task['task_id'], preview['preview_identity'])
        case = root / count
        immutable(case / 'TASK.json', task)
        execution = _public_execute(service, task, workspace=workspace, deployment=deployment, output=case, interrupt=count == '504')
        archived_scan = service.scan(request, preview['preview_identity'])
        rows[count] = {'task_id': task['task_id'], 'job_path': task['job_path'],
                      'preparation_root': str(service.root / 'signal-scans' / archived_scan['scan_id']), **execution}
    primary = read_json(rows['504']['job_path'])
    authority = service.authority(config['engineering_authorization_ref'])
    source = reference_source(authority, primary, previews['504']['request'])
    reference_root = root / 'continuous_reference' / 'account'
    from scripts.run_strategy_account_v1 import freeze_config, service as governance, execute_accounts, run_long_horizon_compute
    job_path = reference_root / 'JOB.json'
    reference = (read_json(job_path) if job_path.exists() else freeze_config(reference_config(primary,
        root=reference_root, objective_id=authority['objective_id'], budget_path=authority['budget_path']), reference_root))
    gov = governance(reference)
    if not gov.receipt_path.exists():
        gov.confirm({**source,
            'approved_plan_ids': {name: value['plan_id'] for name, value in reference['plans'].items()}},
            {'input_identity': reference['input_identity'], 'novelty': {name: {'allowed': True, 'plan_id': value['plan_id']}
                for name, value in reference['plans'].items()}})
    execute_accounts(job_path, recover=True)
    request = {**requests['504'], 'authorization_ref': config['engineering_authorization_ref']}
    proof = run_long_horizon_compute(job_path, authority, request, 'VERIFICATION')
    if not proof['advance_allowed']:
        raise ValueError('LONG_ACCEPTANCE_REFERENCE_AUDIT_FAILED')
    immutable(reference_root / 'VERIFICATION.json', proof)
    reports = run_long_horizon_compute(job_path, authority, request, 'REPORT')
    comparisons = {name: compare_accounts(read_json(Path(primary['root']) / (name + '_RESULT.json')),
                                         read_json(reference_root / (name + '_RESULT.json'))) for name in primary['plans']}
    result = {'version': VERSION, 'acceptance_identity': plan['acceptance_identity'], 'status': 'REAL_252_504_ACCOUNT_VERIFIED',
        'cases': rows, 'reference_job': str(job_path), 'comparisons': comparisons, 'reference_reports': reports,
        'resources': resource_summary(rows, reference),
        'strategy_qualified': False, 'independent_validation': 'NOT_RUN', 'paper': 'NOT_RUN'}
    result['run_identity'] = stable_hash(result)
    immutable(root / 'REAL_ACCEPTANCE.json', result)
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('workspace-root', 'deployment', 'acceptance-config', 'output-root'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    from scripts.lifecycle_deployment_v2 import build_submission_service
    service = build_submission_service(args.workspace_root, read_json(Path(args.deployment)))
    result = run_acceptance(service, read_json(Path(args.acceptance_config)), output_root=args.output_root,
        workspace=args.workspace_root, deployment=args.deployment, execute=args.execute)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
