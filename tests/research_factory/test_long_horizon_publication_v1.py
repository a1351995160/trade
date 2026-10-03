"""合成元数据仅验证发布合同；这些fixture不是市场数据或六账户真实验收。"""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory import long_horizon_acceptance_publication_v1 as publication
from chanlun_trader.research_factory.universe_execution_profile_v1 import (
    CONTINUOUS_PROFILE, ENGINEERING_PURPOSE, SEGMENTED_PROFILE, execution_profile,
)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(value, key):
    value[key] = stable_hash({name: item for name, item in value.items() if name != key})
    return value


def fixture(root):
    """构造所有绑定，故意不创建任何行情或日明细文件。"""
    repo, archive, acceptance = root/'repo', root/'source_archive', root/'original_evidence'
    code = ['src/chanlun_trader/research_factory/core.py',
        'src/chanlun_trader/research_factory/full.py', 'src/chanlun_trader/research_factory/long.py',
        'scripts/run_long_horizon_universe_acceptance_v1.py',
        'src/chanlun_trader/research_factory/long_horizon_acceptance_publication_v1.py']
    source_hashes = {}
    for relative in code:
        path = repo/relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Path(publication.__file__).read_bytes() if path.name == Path(publication.__file__).name else b'# synthetic test-only source\n')
        other = archive/relative; other.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(path, other)
        source_hashes[str(other)] = hashlib.sha256(other.read_bytes()).hexdigest()
    rule = {'version': 'RESEARCH_RULE_STRATEGY_V4', 'indicator_instances': [
        {'id': name, 'instance_id': name.lower()} for name in ('MA', 'RSI', 'ROLLING_VOLATILITY')]}
    snapshot = {'data': {'status': 'UNKNOWN'}, 'source_hashes': {'core.py': source_hashes[str(archive/code[0])]},
        'full_universe': {'source_hashes': {'full.py': source_hashes[str(archive/code[1])]}},
        'long_horizon': {'source_hashes': {'long.py': source_hashes[str(archive/code[2])]}},
        'examples': {'multi_indicator_ranked': rule}}
    identity(snapshot, 'fingerprint')
    targets = ['000001.SZ', '000002.SZ', '300001.SZ', '600000.SH']
    qualified = ['000001.SZ', '300001.SZ', '600000.SH']
    dataset = {'dataset_id': 'synthetic_registered_all', 'target_symbols': targets,
        'by_board': {name: {'target_count': sum(symbol.startswith(prefix) for symbol in targets)}
            for name, prefix in (('SZ_MAIN', '00'), ('SH_MAIN', '60'), ('CHINEXT', '30'))}}
    deployed = identity({**deepcopy(snapshot), 'data': {'datasets': [dataset]}}, 'fingerprint')
    observation = {'version': 'SYNTHETIC_OBSERVATION_PLAN_FOR_CONTRACT_TEST'}
    frozen = {'version': publication.ACCEPTANCE_VERSION, 'capability_fingerprint': deployed['fingerprint'],
        'source_sha256': source_hashes[str(archive/code[3])], 'requests': {}, 'previews': {},
        'criteria': {'actual_sessions': [252, 504], 'actual_account_purposes': 6, 'shared_cash': 50000,
            'positive_return_required': False, 'qualified_scope': 'ALL_DATA_QUALIFIED',
            'segmented_504_pause_thresholds_per_cost': [80, 251, 390], 'continuous_execution_restore': False,
            'independent_audit_required': True, 'funnel_closed_required': True, 'strategy_qualified': False}}
    real = {'version': publication.ACCEPTANCE_VERSION, 'status': 'REAL_252_504_ACCOUNT_VERIFIED',
        'cases': {}, 'comparisons': {}, 'strategy_qualified': False}
    pairs = {}
    for role in ('252', '504', 'REFERENCE'):
        count = 252 if role == '252' else 504
        reference = role == 'REFERENCE'; profile = execution_profile(
            CONTINUOUS_PROFILE if reference else SEGMENTED_PROFILE, count,
            ENGINEERING_PURPOSE if reference else 'RESEARCH_ACCOUNT')
        folder = acceptance/role/'account'; input_root = acceptance/role/'input'
        preparation_root = acceptance/'service'/'signal-scans'/('synthetic_scan_'+role)
        input_identity = stable_hash(['synthetic', count]); objective = 'SYNTHETIC_' + role
        days = [(date(2022, 1, 1) + timedelta(days=number)).strftime('%Y%m%d') for number in range(count)]
        window = {'calendar': days, 'account_start': days[0], 'account_end': days[-1], 'symbols': qualified}
        scope = identity({'target_symbols': targets, 'qualified_symbols': qualified,
            'excluded': [{'symbol': '000002.SZ', 'reasons': ['SYNTHETIC_MISSING_EVIDENCE']}],
            'blocking_global_gaps': [], 'parent_window': {**window, 'symbols': targets},
            'projected_input_identity': input_identity}, 'scope_identity')
        save(input_root/'QUALIFICATION_SCOPE.json', scope)
        input_metadata = {'snapshot_version': 'UNIVERSE_FROZEN_INPUT_V1', 'input_identity': input_identity,
            'window': window, 'bundle': {'qualified_scope': scope},
            'frames': {kind: {'path': str(input_root/(kind+'.parquet')), 'rows': 12,
                'sha256': stable_hash(['uncreated synthetic market bytes', kind, count])} for kind in ('daily', 'turn', 'states')}}
        input_sha = save(input_root/'INPUT.json', input_metadata)
        if not reference:
            request = {'rule': rule, 'account_scope': 'DATA_QUALIFIED', 'costs': ['BASE', 'STRESS'],
                'symbols': targets, 'execution_profile': profile, 'authorization_ref': 'SYNTHETIC_' + role,
                'dataset_id': dataset['dataset_id']}
            frozen['requests'][role] = request
            frozen['previews'][role] = identity({'request': request, 'capabilities': deepcopy(deployed),
                'data_metadata': deepcopy(dataset)}, 'preview_identity')
        else:
            request = {**frozen['requests']['504'], 'authorization_ref': 'SYNTHETIC_REFERENCE'}
        budget_path = acceptance/role/'BUDGET.json'
        names = ['SYNTHETIC_' + suffix for suffix in ('BASE', 'STRESS')]
        job = {'root': str(folder), 'resources': profile, 'objective_id': objective, 'budget_path': str(budget_path),
            'input_identity': input_identity, 'source_hashes': source_hashes, 'plans': {}, 'items': {}, 'observation_plan': observation}
        for name in names:
            item = {'execution_profile': profile, 'source_hashes': source_hashes, 'loader': 'synthetic:never_read_market',
                'loader_kwargs': {'path': str(input_root/'INPUT.json'), 'sha256': input_sha},
                'backend_options': {'checkpoint_path': str(folder/'account'/(name+'_CHECKPOINT.json'))}}
            backend = {'backend': 'UNIVERSE_ACCOUNT_BACKEND_V2', 'initial_cash': 50000,
                'execution_profile': profile, 'window': window, 'source_hashes': source_hashes}
            plan = identity({'runtime': item, 'backend': backend, 'strategy': {'source_hashes': source_hashes,
                'parameters': {'candidate_payload': rule, 'rule_identity': stable_hash(rule)}}}, 'plan_id')
            job['items'][name], job['plans'][name] = item, plan
        job_sha = save(folder/'JOB.json', job)
        source = {'origin': 'USER_EXPLICIT_CURRENT_TASK', 'statement': 'synthetic contract test only',
            'approved_plan_ids': {name: plan['plan_id'] for name, plan in job['plans'].items()},
            'qualified_scope_identity': scope['scope_identity']}
        if reference: source['engineering_authorization'] = ENGINEERING_PURPOSE
        confirmation = identity({'strategy_plans': job['plans'], 'objective_id': objective, 'budget_path': str(budget_path),
            'input_identity': input_identity, 'source': source}, 'receipt_id')
        save(folder/'CONFIRMATION.json', confirmation)
        budget = {'objective_id': objective, 'buckets': [], 'settled_reservations': {}}
        proofs, index, interrupts = {}, {}, []
        control_head, control_sequence = None, 0
        for name, plan in job['plans'].items():
            execution_identity = stable_hash([role, name])
            manifest = {'version': 'UNIVERSE_EXECUTION_ARTIFACTS_V1', 'root': str(folder/'uncreated_market_details'),
                'identity': execution_identity, 'days': []}
            head = stable_hash([manifest['version'], execution_identity])
            for day in days:
                row = identity({'date': day, 'file': 'UNCREATED_'+day+'.json.gz', 'sha256': stable_hash(['bytes', day, name]),
                    'previous': head, 'payload_identity': stable_hash([day, name])}, 'chain')
                manifest['days'].append(row); head = row['chain']
            manifest['head'] = head; identity(manifest, 'manifest_identity')
            state = {key: {'synthetic': key, 'cost': name} for key in publication.STATE_FIELDS}
            state.update(last_day=days[-1], calendar_identity=stable_hash(days), execution_identity=execution_identity,
                artifacts=manifest, version='UNIVERSE_EXECUTION_STATE_V2')
            identity(state, 'state_identity')
            save(Path(plan['runtime']['backend_options']['checkpoint_path']), state)
            metrics = {'net_return': -.01, 'max_drawdown': .02}
            audit = {'version': 'UNIVERSE_EVIDENCE_V2', 'input_identity': input_identity,
                'daily_accounts': [{'date': day, 'difference': 0} for day in days], 'metrics': metrics, 'strategy_qualified': False}
            result = {'strategy_plan': plan, 'input_identity': input_identity, 'execution_description': plan['backend'],
                'execution_identity': execution_identity, 'result_schema': 'UNIVERSE_SHARDED_RESULT_V2', 'strategy_qualified': False,
                'metrics': metrics, 'artifacts': manifest, 'reconciliation': {'passed': True, 'audit_identity': stable_hash(audit)}}
            result.update({key: {'synthetic': key, 'cost': name} for key in publication.ECONOMIC_FIELDS if key != 'metrics'})
            result['fills'] = []
            result['final_account_checkpoint'] = {'economic': {'orders': []}}
            result_sha = save(folder/(name+'_RESULT.json'), result)
            start = {'kind': name, 'receipt_id': confirmation['receipt_id'], 'counted_before_account_calculation': True,
                'reservation': role+'_'+name}
            save(folder/(name+'_START.json'), start)
            budget['settled_reservations'][start['reservation']] = 'CONSUMED'
            budget['buckets'].append({'kind': 'strategy_interface_account_v1', 'key': confirmation['receipt_id']+':'+name,
                'used': 1, 'reserved': 0, 'limit': 1})
            charged, head, charge_ids = 0., None, []
            thresholds = [80, 251, 390] if role == '504' else []
            for number in range(1, len(thresholds)+2):
                final = number == len(thresholds)+1
                prefix = name+'_SEGMENT_'+str(number).zfill(6)
                dispatch = identity({'segment_number': number, 'kind': name, 'receipt_id': confirmation['receipt_id'],
                    'profile_hash': profile['profile_hash'], 'charged_before': charged, 'upper_bound_seconds': profile['worker_seconds'],
                    'previous_head': head}, 'dispatch_id')
                save(folder/(prefix+'_DISPATCH.json'), dispatch)
                resource = {'elapsed_wall_seconds': 2., 'returncode': 0 if final else 75, 'timed_out': False,
                    'windows_job_bound': True, 'resource_platform': 'nt', 'memory_measurement': 'WINDOWS_JOB_PEAK_COMMIT',
                    'peak_memory_mib': 100., 'launcher_pid': 123, 'segment_number': number, 'dispatch_id': dispatch['dispatch_id']}
                resource_sha = save(folder/(prefix+'_RESOURCE.json'), resource)
                save(folder/(prefix+'_WORKER.json'), {'pid': 123, 'purpose': name, 'segment_number': number, 'dispatch_id': dispatch['dispatch_id']})
                save(folder/(prefix+'_INPUT_ACCESS.json'), {'windows_job_verified': True, 'launcher_pid': 123,
                    'reader_parent_pid': 123, 'purpose': name, 'segment_number': number, 'dispatch_id': dispatch['dispatch_id'],
                    'input_identity': input_identity, 'loader': plan['runtime']['loader'], 'loader_kwargs': plan['runtime']['loader_kwargs']})
                status = {'state': 'COMPLETED' if final else 'CONTINUE', 'dispatch_id': dispatch['dispatch_id'],
                    'last_day': days[-1] if final else days[thresholds[number-1]-1]}
                if final: status['result_sha256'] = result_sha
                save(folder/(prefix+'_STATUS.json'), status)
                charge = identity({'dispatch_id': dispatch['dispatch_id'], 'measured_seconds': 2., 'seconds': 2.,
                    'basis': 'MEASURED_ACTIVE_WALL_SECONDS', 'evidence_identity': resource_sha,
                    'outcome': 'COMPLETED' if final else 'PAUSED'}, 'charge_id')
                save(folder/(prefix+'_CHARGE.json'), charge)
                head = charge['charge_id']; charged += 2.; charge_ids.append(head)
                if not final:
                    control_sequence += 1
                    control = identity({'sequence': control_sequence, 'previous_head': control_head, 'event': 'PAUSE',
                        'job_sha256': job_sha, 'receipt_id': confirmation['receipt_id']}, 'event_id')
                    control_head = control['event_id']; save(folder/('CONTROL_'+str(control_sequence).zfill(6)+'.json'), control)
                    interrupts.append({'purpose': name, 'threshold': thresholds[number-1], 'committed_days_at_request': thresholds[number-1],
                        'state_identity': stable_hash([name, number]), 'control': {'status': 'PAUSE_REQUESTED', 'control_identity': control_head}})
                    control_sequence += 1
                    control = identity({'sequence': control_sequence, 'previous_head': control_head, 'event': 'RESUME',
                        'job_sha256': job_sha, 'receipt_id': confirmation['receipt_id']}, 'event_id')
                    control_head = control['event_id']; save(folder/('CONTROL_'+str(control_sequence).zfill(6)+'.json'), control)
            save(folder/(name+'_SETTLEMENT.json'), {**start, 'completed': True, 'error': None, 'result_sha256': result_sha, 'wall_seconds': charged})
            save(folder/(name+'_RESOURCE.json'), {**resource, 'elapsed_wall_seconds': charged, 'segment_count': len(charge_ids),
                'active_metering': True, 'segments': charge_ids})
            report = identity({'input_identity': input_identity, 'strategy_qualified': False, 'paper_qualified': False,
                'account': {'sessions': count, 'initial_cash': 50000, 'reconciliation': result['reconciliation']},
                'signal': {'scope_count': len(qualified), 'observation_plan': observation,
                    'observation_plan_identity': stable_hash(observation), 'account_independent_denominator': True}}, 'report_identity')
            funnel = identity({'version': 'UNIVERSE_SIGNAL_FUNNEL_V1', 'mode': 'DAY_STREAM',
                'strategy_id': name, 'rule_identity': stable_hash(rule), 'calendar_identity': stable_hash(days),
                'counts': {'scan_rows': count*len(qualified), 'opportunity_signals': 0, 'intents': 0, 'orders': 0, 'fills': 0},
                'opportunity_dispositions': {}, 'detail_chain_identity': stable_hash(['synthetic funnel', count]),
                'layer_counts_by_side': {side: {'intents': 0, 'orders': 0, 'fills': 0} for side in ('BUY', 'SELL')}}, 'identity')
            save(folder/(name+'_RESEARCH_REPORT.json'), report); save(folder/(name+'_SIGNAL_FUNNEL.json'), funnel)
            proofs[name] = {'status': 'PASS', 'advance_allowed': True, 'reasons': [], 'plan_id': plan['plan_id'],
                'input_identity': input_identity, 'result_sha256': result_sha, 'account_audit': audit,
                'evidence_layers': {'account_reconciled': True, 'input_profile': 'HISTORICAL_MODELED'},
                'resource_accounting': {'charged_seconds': charged, 'measured_seconds': charged, 'conservatively_charged_seconds': 0.}}
            index[name] = {'sha256': result_sha}
            pairs[role, name] = (result, state)
        verification = {'job_sha256': job_sha, 'advance_allowed': True, 'items': proofs}
        save(folder/'VERIFICATION.json', verification); save(folder/'RESULTS_INDEX.json', {'items': index})
        for stage in (('VERIFICATION', 'REPORT') if reference else ('PREPARATION', 'VERIFICATION', 'REPORT')):
            compute = preparation_root/'COMPUTE' if stage == 'PREPARATION' else folder/('COMPUTE_'+stage)
            compute_profile = execution_profile(SEGMENTED_PROFILE, count, 'RESEARCH_'+stage)
            authority = {'execution_profiles': [compute_profile], 'source': 'synthetic_authority_' + role}
            binding = identity({'stage': stage, 'profile': compute_profile, 'authorization_identity': stable_hash(authority),
                'request_identity': stable_hash(request), 'budget_key': stable_hash(authority)}, 'compute_identity')
            compute_start = {'binding': binding, 'reservation': role+'_'+stage}
            save(compute/'COMPUTE_START.json', compute_start)
            budget['settled_reservations'][compute_start['reservation']] = 'CONSUMED'
            budget['buckets'].append({'kind': 'universe_compute_'+stage.lower()+'_v1', 'key': binding['budget_key'], 'used': 1, 'reserved': 0, 'limit': 1})
            if stage == 'PREPARATION':
                intent = identity({'root': str(preparation_root), 'compute_authority': authority,
                    'preview': deepcopy(frozen['previews'][role]), 'scan_id': preparation_root.name,
                    'authorization_identity': stable_hash(authority)}, 'intent_identity')
                save(preparation_root/'SCAN_INTENT.json', intent)
                original_input = deepcopy(input_metadata)
                for kind, info in original_input['frames'].items(): info['path'] = str(preparation_root/(kind+'.parquet'))
                original_input_sha = save(preparation_root/'INPUT.json', original_input)
                output = identity({'status': 'QUALIFIED_SCOPE_READY', 'qualified_account_ready': True,
                    'qualified_input_identity': input_identity, 'qualification_scope': scope,
                    'processed_target_count': len(targets), 'compute_consumed': True,
                    'scan_id': intent['scan_id'], 'scope': {'preview_identity': intent['preview']['preview_identity'],
                        'authorization_identity': intent['authorization_identity']}}, 'scan_identity')
                output_sha = save(preparation_root/'RESULT.json', output)
                save(preparation_root/'SCAN_RECEIPT.json', identity({'intent_identity': intent['intent_identity'],
                    'scan_identity': output['scan_identity'], 'artifacts': {
                        str(preparation_root/'INPUT.json'): {'sha256': original_input_sha},
                        str(preparation_root/'RESULT.json'): {'sha256': output_sha}}}, 'receipt_identity'))
                members = ['PREPARE', 'QUALIFY']
            else:
                save(compute/'SCOPE.json', {'authority': authority, 'request': request, 'stage': stage, 'job_sha256': job_sha})
                members = names if stage == 'REPORT' else ['verification']
            charged, head = 0., None
            for number, member in enumerate(members, 1):
                final = number == len(members)
                dispatch_prefix = 'COMPUTE_SEGMENT_'+str(number).zfill(6)
                prefix = ('PREPARE_' if stage == 'PREPARATION' else 'SEGMENT_')+str(number).zfill(6)
                physical = preparation_root if stage == 'PREPARATION' else compute
                dispatch = identity({'number': number, 'compute_identity': binding['compute_identity'],
                    'upper_bound_seconds': 900, 'previous_head': head}, 'dispatch_id')
                save(compute/(dispatch_prefix+'_DISPATCH.json'), dispatch)
                resource = {'elapsed_wall_seconds': 3., 'returncode': 75 if stage == 'PREPARATION' and not final else 0,
                    'timed_out': False, 'windows_job_bound': True, 'resource_platform': 'nt',
                    'memory_measurement': 'WINDOWS_JOB_PEAK_COMMIT', 'peak_memory_mib': 100.,
                    'launcher_pid': 456, 'dispatch_id': dispatch['dispatch_id']}
                if stage == 'PREPARATION': resource['phase'] = member
                elif stage == 'REPORT': resource['member'] = member
                resource_sha = save(physical/(prefix+'_RESOURCE.json'), resource)
                save(physical/(prefix+'_WORKER.json'), {'pid': 456, 'dispatch_id': dispatch['dispatch_id']})
                access = {'windows_job_verified': True, 'launcher_pid': 456, 'reader_parent_pid': 456,
                    'compute_identity': binding['compute_identity'], 'stage': stage, 'input_identity': input_identity,
                    'observation_plan': observation, 'purpose': 'AUTHORIZED_POST_ACCOUNT_EVALUATION'}
                if stage == 'PREPARATION': access.update(phase=member, dispatch_id=dispatch['dispatch_id'])
                elif stage == 'REPORT': access['member'] = member
                save(physical/(prefix+'_ACCESS.json'), access)
                if stage != 'PREPARATION':
                    output_path = compute/('RESULT_'+member+'.json' if stage == 'REPORT' else 'RESULT.json')
                    output = {'member': member, 'research_report': str(folder/(member+'_RESEARCH_REPORT.json')),
                        'research_report_sha256': hashlib.sha256((folder/(member+'_RESEARCH_REPORT.json')).read_bytes()).hexdigest(),
                        'funnel': str(folder/(member+'_SIGNAL_FUNNEL.json')),
                        'funnel_sha256': hashlib.sha256((folder/(member+'_SIGNAL_FUNNEL.json')).read_bytes()).hexdigest()} if stage == 'REPORT' else verification
                    output_sha = save(output_path, output)
                    save(physical/(prefix+'_STATUS.json'), {'state': 'COMPLETED', 'member': member,
                        'dispatch_id': dispatch['dispatch_id'], 'result_sha256': output_sha})
                charge = identity({'dispatch_id': dispatch['dispatch_id'], 'measured_seconds': 3., 'seconds': 3.,
                    'basis': 'MEASURED_ACTIVE_WALL_SECONDS', 'evidence_identity': resource_sha,
                    'outcome': 'COMPLETED' if final else 'CONTINUE'}, 'charge_id')
                save(compute/(dispatch_prefix+'_CHARGE.json'), charge); charged += 3.; head = charge['charge_id']
            save(preparation_root/'RESOURCE.json' if stage == 'PREPARATION' else compute/'RESOURCE_TOTAL.json',
                {'elapsed_wall_seconds': charged} if stage == 'PREPARATION' else {'charged_seconds': charged})
        save(budget_path, budget)
        if reference: real['reference_job'] = str(folder/'JOB.json')
        else: real['cases'][role] = {'job_path': str(folder/'JOB.json'), 'interrupts': interrupts, 'preparation_root': str(preparation_root)}
    for name in names:
        result, state = pairs['504', name]
        real['comparisons'][name] = {'passed': True, 'sessions': 504,
            'economic_identity': stable_hash({key: result[key] for key in publication.ECONOMIC_FIELDS}),
            'daily_content_identity': stable_hash([row['payload_identity'] for row in result['artifacts']['days']]),
            'full_engine_state_identity': stable_hash({key: state[key] for key in publication.STATE_FIELDS})}
    identity(frozen, 'acceptance_identity'); save(acceptance/'FROZEN_ACCEPTANCE.json', frozen)
    real['acceptance_identity'] = frozen['acceptance_identity']; identity(real, 'run_identity')
    save(acceptance/'REAL_ACCEPTANCE.json', real)
    return repo, acceptance, archive, snapshot


@pytest.fixture
def contract(tmp_path, monkeypatch):
    value = fixture(tmp_path)
    from chanlun_trader.research_factory import research_capabilities_v1
    monkeypatch.setattr(research_capabilities_v1, 'capabilities', lambda: deepcopy(value[3]))
    return value


def test_publish_six_account_metadata_and_read_without_originals_or_market(contract):
    repo, acceptance, archive, snapshot = contract
    before = deepcopy(snapshot)
    result = publication.publish_long_horizon_acceptance(repo, acceptance)
    assert result['status'] == 'PUBLISHED_METADATA_VERIFIED', result
    assert result['account_count'] == 6 and result['actual_sessions'] == [252, 504]
    assert result['coverage']['504'] == {'target_count': 4, 'qualified_count': 3, 'excluded_count': 1}
    assert 'volatility_rank' in result['feature_ids']
    assert result['strategy_qualified'] is False and result['paper'] == 'NOT_RUN'
    assert not list(repo.rglob('*.gz'))
    shutil.rmtree(acceptance); shutil.rmtree(archive)
    assert publication.published_long_horizon_acceptance(repo, snapshot) == result
    assert snapshot == before


def mutate(path, edit, own_identity=None):
    value = json.loads(path.read_bytes()); edit(value)
    if own_identity: identity(value, own_identity)
    save(path, value)


@pytest.mark.parametrize('change', ['missing', 'source_archive', 'current_source', 'short', 'fewer', 'engineering',
    'fake_pass', 'full_engine', 'budget', 'bound', 'overrun', 'compute_output', 'pause_control', 'account_access'])
def test_publication_rejects_incomplete_or_conflicting_original_metadata(contract, change):
    repo, acceptance, archive, snapshot = contract
    folder = acceptance/'504'/'account'
    if change == 'missing': (folder/'VERIFICATION.json').unlink()
    elif change == 'source_archive': (archive/'src/chanlun_trader/research_factory/full.py').write_bytes(b'changed')
    elif change == 'current_source': (repo/'src/chanlun_trader/research_factory/long.py').write_bytes(b'changed')
    elif change == 'short':
        mutate(folder/'JOB.json', lambda row: row['resources'].update(account_sessions=22))
    elif change == 'fewer':
        mutate(folder/'JOB.json', lambda row: row['plans'].pop('SYNTHETIC_STRESS'))
    elif change == 'engineering':
        mutate(acceptance/'REFERENCE'/'account'/'CONFIRMATION.json', lambda row: row['source'].pop('engineering_authorization'), 'receipt_id')
    elif change == 'fake_pass':
        mutate(folder/'VERIFICATION.json', lambda row: row['items']['SYNTHETIC_BASE'].update(account_audit={'passed': True}))
    elif change == 'full_engine':
        mutate(acceptance/'REAL_ACCEPTANCE.json', lambda row: row['comparisons']['SYNTHETIC_BASE'].pop('full_engine_state_identity'), 'run_identity')
    elif change == 'budget':
        mutate(acceptance/'504'/'BUDGET.json', lambda row: row['settled_reservations'].update({'504_SYNTHETIC_BASE': 'RELEASED'}))
    elif change in ('bound', 'overrun'):
        mutate(folder/'SYNTHETIC_BASE_SEGMENT_000001_RESOURCE.json',
            lambda row: row.update(windows_job_bound=False) if change == 'bound' else row.update(elapsed_wall_seconds=901.))
    elif change == 'compute_output':
        mutate(folder/'COMPUTE_REPORT'/'RESULT_SYNTHETIC_BASE.json', lambda row: row.update(funnel_sha256=stable_hash('fake')))
    elif change == 'pause_control':
        mutate(acceptance/'REAL_ACCEPTANCE.json', lambda row: row['cases']['504']['interrupts'][0]['control'].update(control_identity=stable_hash('fake')), 'run_identity')
    else:
        mutate(folder/'SYNTHETIC_BASE_SEGMENT_000001_INPUT_ACCESS.json', lambda row: row.update(input_identity=stable_hash('foreign')))
    with pytest.raises((ValueError, KeyError)):
        publication.publish_long_horizon_acceptance(repo, acceptance)
    assert not (repo/publication.PREFIX/'PUBLISHED_ACCEPTANCE.json').exists()


@pytest.mark.parametrize('change', ['missing', 'bytes', 'snapshot', 'current_source', 'receipt', 'escape'])
def test_published_reader_fails_closed(contract, change):
    repo, acceptance, archive, snapshot = contract
    publication.publish_long_horizon_acceptance(repo, acceptance)
    path = repo/publication.PREFIX/'PUBLISHED_ACCEPTANCE.json'
    receipt = json.loads(path.read_bytes())
    if change == 'missing': (repo/receipt['cases'][0]['verification']['path']).unlink()
    elif change == 'bytes': (repo/receipt['cases'][0]['job']['path']).write_bytes(b'{}')
    elif change == 'snapshot': snapshot['source_hashes']['core.py'] = stable_hash('different')
    elif change == 'current_source': (repo/'src/chanlun_trader/research_factory/long.py').write_bytes(b'changed')
    elif change == 'receipt': mutate(path, lambda row: row.update(feature_ids=['cost_stop']))
    else:
        mutate(path, lambda row: row['cases'][0]['job'].update(path='../../market/JOB.json'), 'self_hash')
    result = publication.published_long_horizon_acceptance(repo, snapshot)
    assert result['status'] == 'NOT_ACCEPTED' and result['strategy_qualified'] is False
    assert result['feature_ids'] == []


def test_deployed_data_catalog_does_not_invalidate_same_code_publication(contract):
    repo, acceptance, archive, snapshot = contract
    frozen = json.loads((acceptance/'FROZEN_ACCEPTANCE.json').read_bytes())
    assert frozen['capability_fingerprint'] != snapshot['fingerprint']
    result = publication.publish_long_horizon_acceptance(repo, acceptance)
    assert result['status'] == 'PUBLISHED_METADATA_VERIFIED', result
    other_catalog = identity({**deepcopy(snapshot), 'data': {'status': 'READ_ONLY_QUERY_WITHOUT_MARKET_READ'}}, 'fingerprint')
    assert publication.published_long_horizon_acceptance(repo, other_catalog) == result


def test_report_evidence_requires_each_costs_own_successful_worker(contract):
    repo, acceptance, archive, snapshot = contract
    folder = acceptance/'504'/'account'/'COMPUTE_REPORT'
    base_output_sha = hashlib.sha256((folder/'RESULT_SYNTHETIC_BASE.json').read_bytes()).hexdigest()
    mutate(folder/'SEGMENT_000002_STATUS.json', lambda row: row.update(member='SYNTHETIC_BASE', result_sha256=base_output_sha))
    mutate(folder/'SEGMENT_000002_ACCESS.json', lambda row: row.update(member='SYNTHETIC_BASE'))
    mutate(folder/'SEGMENT_000002_RESOURCE.json', lambda row: row.update(member='SYNTHETIC_BASE'))
    resource_sha = hashlib.sha256((folder/'SEGMENT_000002_RESOURCE.json').read_bytes()).hexdigest()
    mutate(folder/'COMPUTE_SEGMENT_000002_CHARGE.json', lambda row: row.update(evidence_identity=resource_sha), 'charge_id')
    with pytest.raises(ValueError, match='ALL_REPORT_MEMBERS_REQUIRED'):
        publication.publish_long_horizon_acceptance(repo, acceptance)


def test_independent_pass_requires_account_audit_content_even_when_all_other_hashes_are_rebound(contract):
    repo, acceptance, archive, snapshot = contract
    folder = acceptance/'504'/'account'
    mutate(folder/'VERIFICATION.json', lambda row: row['items']['SYNTHETIC_BASE'].update(account_audit={'passed': True}))
    verification = json.loads((folder/'VERIFICATION.json').read_bytes())
    output_sha = save(folder/'COMPUTE_VERIFICATION'/'RESULT.json', verification)
    mutate(folder/'COMPUTE_VERIFICATION'/'SEGMENT_000001_STATUS.json', lambda row: row.update(result_sha256=output_sha))
    with pytest.raises(KeyError, match='version'):
        publication.publish_long_horizon_acceptance(repo, acceptance)


def rebind_account_result(folder, name):
    result_sha = hashlib.sha256((folder/(name+'_RESULT.json')).read_bytes()).hexdigest()
    mutate(folder/(name+'_SETTLEMENT.json'), lambda row: row.update(result_sha256=result_sha))
    mutate(folder/'RESULTS_INDEX.json', lambda row: row['items'][name].update(sha256=result_sha))
    mutate(folder/'VERIFICATION.json', lambda row: row['items'][name].update(result_sha256=result_sha))
    status_path = sorted(folder.glob(name+'_SEGMENT_*_STATUS.json'))[-1]
    mutate(status_path, lambda row: row.update(result_sha256=result_sha))
    verification = json.loads((folder/'VERIFICATION.json').read_bytes())
    output_sha = save(folder/'COMPUTE_VERIFICATION'/'RESULT.json', verification)
    mutate(folder/'COMPUTE_VERIFICATION'/'SEGMENT_000001_STATUS.json', lambda row: row.update(result_sha256=output_sha))


@pytest.mark.parametrize('difference,accepted', [(5e-7, True), (2e-6, False)])
def test_independent_audit_preserves_upstream_money_tolerance(contract, difference, accepted):
    repo, acceptance, archive, snapshot = contract
    folder = acceptance/'252'/'account'
    mutate(folder/'SYNTHETIC_BASE_RESULT.json', lambda row: row['metrics'].update(net_return=row['metrics']['net_return']+difference))
    rebind_account_result(folder, 'SYNTHETIC_BASE')
    if accepted:
        result = publication.publish_long_horizon_acceptance(repo, acceptance)
        assert result['status'] == 'PUBLISHED_METADATA_VERIFIED', result
    else:
        with pytest.raises(ValueError, match='AUDIT_CONTENT_NOT_PROVEN'):
            publication.publish_long_horizon_acceptance(repo, acceptance)


def test_preparation_adoption_requires_same_frame_hashes_without_opening_market(contract):
    repo, acceptance, archive, snapshot = contract
    scan = acceptance/'service'/'signal-scans'/'synthetic_scan_504'
    mutate(scan/'INPUT.json', lambda row: row['frames']['daily'].update(sha256=stable_hash('foreign market')))
    original_input_sha = hashlib.sha256((scan/'INPUT.json').read_bytes()).hexdigest()
    mutate(scan/'SCAN_RECEIPT.json', lambda row: row['artifacts'][str(scan/'INPUT.json')].update(sha256=original_input_sha), 'receipt_identity')
    with pytest.raises(ValueError, match='SCAN_ADOPTION_CHAIN_CONFLICT'):
        publication.publish_long_horizon_acceptance(repo, acceptance)
    assert not list(repo.rglob('*.parquet'))


def test_measured_peak_memory_required_even_with_job_bound_true(contract):
    repo, acceptance, archive, snapshot = contract
    folder = acceptance/'252'/'account'
    resource_path = folder/'SYNTHETIC_BASE_SEGMENT_000001_RESOURCE.json'
    mutate(resource_path, lambda row: row.update(memory_measurement='NOT_MEASURED'))
    resource_sha = hashlib.sha256(resource_path.read_bytes()).hexdigest()
    mutate(folder/'SYNTHETIC_BASE_SEGMENT_000001_CHARGE.json', lambda row: row.update(evidence_identity=resource_sha), 'charge_id')
    with pytest.raises(ValueError, match='WINDOWS_RESOURCE_BOUND_NOT_PROVEN'):
        publication.publish_long_horizon_acceptance(repo, acceptance)


def test_funnel_requires_closed_signal_denominator_even_when_summary_hashes_rebound(contract):
    repo, acceptance, archive, snapshot = contract
    folder = acceptance/'252'/'account'
    mutate(folder/'SYNTHETIC_BASE_SIGNAL_FUNNEL.json', lambda row: row['counts'].update(scan_rows=1), 'identity')
    funnel_sha = hashlib.sha256((folder/'SYNTHETIC_BASE_SIGNAL_FUNNEL.json').read_bytes()).hexdigest()
    output = folder/'COMPUTE_REPORT'/'RESULT_SYNTHETIC_BASE.json'
    mutate(output, lambda row: row.update(funnel_sha256=funnel_sha))
    output_sha = hashlib.sha256(output.read_bytes()).hexdigest()
    mutate(folder/'COMPUTE_REPORT'/'SEGMENT_000001_STATUS.json', lambda row: row.update(result_sha256=output_sha))
    with pytest.raises(ValueError, match='FUNNEL_CLOSURE_NOT_ESTABLISHED'):
        publication.publish_long_horizon_acceptance(repo, acceptance)


def test_unknown_segment_cannot_prove_one_reports_success(contract):
    repo, acceptance, archive, snapshot = contract
    folder = acceptance/'504'/'account'/'COMPUTE_REPORT'
    first_dispatch = json.loads((folder/'COMPUTE_SEGMENT_000001_DISPATCH.json').read_bytes())
    first_charge_path = folder/'COMPUTE_SEGMENT_000001_CHARGE.json'
    mutate(first_charge_path, lambda row: row.update(basis='UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND',
        measured_seconds=None, seconds=900., evidence_identity=None), 'charge_id')
    first_charge = json.loads(first_charge_path.read_bytes())
    save(folder/'SEGMENT_000001_RESUME.json', {'dispatch_id': first_dispatch['dispatch_id'], 'budget_reused': True})
    second_dispatch_path = folder/'COMPUTE_SEGMENT_000002_DISPATCH.json'
    mutate(second_dispatch_path, lambda row: row.update(previous_head=first_charge['charge_id']), 'dispatch_id')
    dispatch_id = json.loads(second_dispatch_path.read_bytes())['dispatch_id']
    for suffix in ('WORKER', 'RESOURCE', 'STATUS'):
        mutate(folder/('SEGMENT_000002_'+suffix+'.json'), lambda row: row.update(dispatch_id=dispatch_id))
    resource_sha = hashlib.sha256((folder/'SEGMENT_000002_RESOURCE.json').read_bytes()).hexdigest()
    mutate(folder/'COMPUTE_SEGMENT_000002_CHARGE.json', lambda row: row.update(dispatch_id=dispatch_id,
        evidence_identity=resource_sha), 'charge_id')
    save(folder/'RESOURCE_TOTAL.json', {'charged_seconds': 903.})
    with pytest.raises(ValueError, match='ALL_REPORT_MEMBERS_REQUIRED'):
        publication.publish_long_horizon_acceptance(repo, acceptance)
