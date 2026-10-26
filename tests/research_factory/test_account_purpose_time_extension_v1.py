"""原用途限时证明的人工 Owner/事件/168日工件；不读取真实市场或真实许可。"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

from chanlun_trader.research_factory import account_purpose_time_extension_v1 as extension
from chanlun_trader.research_factory import account_version_capability_bridge_v1 as bridges
from chanlun_trader.research_factory import continuous_research_contract_v1 as contracts
from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
from chanlun_trader.research_factory.campaign_scope_v1 import OwnerApprovalStoreV1, bind_campaign_scope, scope_summary
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
from chanlun_trader.research_factory.exploration_governance import immutable
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.run_budget import AutonomousRunBudgetV2
from chanlun_trader.research_factory.universe_execution_artifacts_v1 import DayArtifacts
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile
from test_continuous_research_contract_v1 import build


def pin(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def write(path, value):
    immutable(path, value)
    return pin(path)


def control(case, event):
    paths = sorted(case['account'].glob('CONTROL_*.json'))
    previous = json.loads(paths[-1].read_text(encoding='utf-8')) if paths else None
    value = {'sequence': len(paths) + 1, 'previous_head': previous['event_id'] if previous else None,
        'event': event, 'job_sha256': case['job_ref']['sha256'], 'receipt_id': case['receipt']['receipt_id']}
    value['event_id'] = stable_hash(value)
    return write(case['account'] / ('CONTROL_' + str(len(paths) + 1).zfill(6) + '.json'), value)


def append_parent_event(case, kind, payload):
    """仅构造被拒绝的人工边界；不经真实执行入口派发。"""
    value = {'run_id': case['base']['authorization_id'], 'event_type': kind, 'payload': payload}
    value['event_id'] = stable_hash(value)
    with case['campaign'].budget_path.with_name('run_budget_events.jsonl').open('a', encoding='utf-8') as output:
        output.write(json.dumps(value) + '\n')


def assert_canonical_legacy_parent_child_hashes(case, parents):
    """纯数据重建产品既有镜像规范；0.0的序列化身份必须从首段保持到最终head。"""
    account = case['account']; name = 'ARTIFICIAL_BASE'; charged = 0.; head = None
    for parent in parents:
        number = parent['segment_number']; prefix = name + '_SEGMENT_' + str(number).zfill(6)
        dispatch = {'schema_version': 'strategy-account-segment-dispatch-v1', 'kind': name,
            'receipt_id': case['receipt']['receipt_id'], 'segment_number': number,
            'profile_hash': parent['profile_hash'], 'charged_before': charged,
            'upper_bound_seconds': parent['upper_bound_seconds'], 'previous_head': head,
            'dispatched_at': parent['dispatched_at']}
        dispatch['dispatch_id'] = stable_hash(dispatch)
        actual_dispatch = json.loads((account / (prefix + '_DISPATCH.json')).read_text(encoding='utf-8'))
        assert type(actual_dispatch['charged_before']) is float
        assert actual_dispatch == dispatch
        charge = {'schema_version': 'strategy-account-segment-charge-v1', 'dispatch_id': dispatch['dispatch_id'],
            **{key: parent[key] for key in ('measured_seconds', 'seconds', 'basis', 'evidence_identity', 'outcome', 'charged_at')}}
        charge['charge_id'] = stable_hash(charge)
        assert json.loads((account / (prefix + '_CHARGE.json')).read_text(encoding='utf-8')) == charge
        charged += charge['seconds']; head = charge['charge_id']
    return charged, head


def produce_artificial_legacy_segment_prefix(case, first, last, *, final_outcome, version_proof=None):
    """仅生产人工旧历史，不调用派发；随后由真实父预算及子治理完整重放验证。

    每段800秒是人工测量数据，RESOURCE明确标识不是实际worker测量或真实收费。
    两块分别覆盖原V1安装前1–6段和安装后7–34段，不减少被核验的历史段数。
    """
    account = case['account']; name = 'ARTIFICIAL_BASE'; profile = case['profile']
    assert first >= 1 and last >= first and final_outcome in ('CONTINUE', 'PAUSED')
    existing = sorted(account.glob(name + '_SEGMENT_*_CHARGE.json'))
    assert len(existing) == first - 1
    charged = sum((json.loads(path.read_text(encoding='utf-8'))['seconds'] for path in existing), 0.0)
    head = json.loads(existing[-1].read_text(encoding='utf-8'))['charge_id'] if existing else None
    event_rows = []; clock = datetime.now(timezone.utc)
    unmeasured = set(case.get('artificial_unmeasured_segments', ()))
    for number in range(first, last + 1):
        prefix = name + '_SEGMENT_' + str(number).zfill(6)
        dispatched_at = (clock + timedelta(microseconds=2 * number)).isoformat()
        charged_at = (clock + timedelta(microseconds=2 * number + 1)).isoformat()
        parent_dispatch = {'operation_id': 'BASE', 'segment_number': number, 'profile_hash': profile['profile_hash'],
            'upper_bound_seconds': 900., 'dispatched_at': dispatched_at}
        dispatch = {'schema_version': 'strategy-account-segment-dispatch-v1', 'kind': name,
            'receipt_id': case['receipt']['receipt_id'], 'segment_number': number, 'profile_hash': profile['profile_hash'],
            'charged_before': charged, 'upper_bound_seconds': 900., 'previous_head': head, 'dispatched_at': dispatched_at}
        dispatch['dispatch_id'] = stable_hash(dispatch)
        write(account / (prefix + '_DISPATCH.json'), dispatch)
        resource = None; resource_path = account / (prefix + '_RESOURCE.json')
        if number not in unmeasured:
            value = {'artificial_segment': number, 'fixture_only': 'CANONICAL_LEGACY_SEGMENT_PRODUCER',
                'artificial_measured_seconds': 800., 'not_real_worker_measurement_or_charge': True,
                'dispatch_id': dispatch['dispatch_id'], 'elapsed_wall_seconds': 800.}
            if number == 7 and case.get('artificial_resource_fault') == 'dispatch':
                value['dispatch_id'] = '0' * 64
            if number == 7 and case.get('artificial_resource_fault') == 'seconds':
                value['elapsed_wall_seconds'] = 799.
            resource = write(resource_path, value)
        seconds = 900. if number in unmeasured else 800.
        parent_charge = {'operation_id': 'BASE', 'segment_number': number,
            'measured_seconds': None if number in unmeasured else 800., 'seconds': seconds,
            'basis': 'UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND' if number in unmeasured else 'MEASURED_ACTIVE_WALL_SECONDS',
            'evidence_identity': resource['sha256'] if resource else None,
            'outcome': final_outcome if number == last else 'CONTINUE', 'charged_at': charged_at}
        charge = {'schema_version': 'strategy-account-segment-charge-v1', 'dispatch_id': dispatch['dispatch_id'],
            **{key: parent_charge[key] for key in ('measured_seconds', 'seconds', 'basis', 'evidence_identity', 'outcome', 'charged_at')}}
        charge['charge_id'] = stable_hash(charge)
        write(account / (prefix + '_CHARGE.json'), charge)
        if number == 7 and case.get('artificial_resource_fault') == 'missing':
            resource_path.unlink()
        if number == 7 and case.get('artificial_resource_fault') == 'sha':
            resource_path.write_bytes(resource_path.read_bytes() + b'\n')
        if version_proof is not None:
            write(account / (prefix + '_EXECUTION_VERSION.json'), {'schema_version': 'ACCOUNT_VERSION_SEGMENT_V1',
                'dispatch_id': dispatch['dispatch_id'], 'segment_number': number,
                'account_version_continuation': deepcopy(version_proof)})
        for kind, payload in (('CAMPAIGN_EXECUTION_SEGMENT_DISPATCHED', parent_dispatch),
                ('CAMPAIGN_EXECUTION_SEGMENT_CHARGED', parent_charge)):
            event = {'schema_version': 'autonomous-run-budget-event-v1', 'run_id': case['base']['authorization_id'],
                'event_type': kind, 'payload': payload, 'timestamp': charged_at}
            event['event_id'] = stable_hash({key: event[key] for key in ('run_id', 'event_type', 'payload')})
            event_rows.append(event)
        charged += seconds; head = charge['charge_id']
    with case['campaign'].budget_path.with_name('run_budget_events.jsonl').open('a', encoding='utf-8') as output:
        output.write(''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in event_rows))
    # 产品的预算和本地子链校验仍完整执行；没有mock许可、scope或重放validator。
    view = case['campaign'].peek_status(); state = case['gov'].segment_status(name)
    parent = view['operations']['BASE']
    assert len(state['segments']) == len(parent['segments']) == last
    expected_seconds = 800. * last + 100. * len(unmeasured & set(range(1, last + 1)))
    assert state['charged_seconds'] == parent['active_wall_seconds'] == expected_seconds
    assert state['next_segment_number'] == last + 1 and state['pending'] is None
    assert state['head'] == head and parent.get('active_segment') is None
    assert view['operations']['STRESS']['status'] == 'RESERVED'
    assert view['operations']['STRESS'].get('active_segment') is None
    canonical_seconds, canonical_head = assert_canonical_legacy_parent_child_hashes(case, parent['segments'])
    assert canonical_seconds == state['charged_seconds'] and canonical_head == state['head']
    case.setdefault('artificial_prefix_replays', []).append({'segments': last, 'seconds': state['charged_seconds'],
        'next_segment_number': state['next_segment_number'], 'parent_and_child_real_replay': True})


@pytest.fixture
def extension_case(tmp_path, monkeypatch, request):
    root = tmp_path.resolve(); current = bridges.capabilities(); old = deepcopy(current)
    old_input = b'artificial old input implementation\n'; input_path = root / bridges.BUSINESS_PATH
    input_path.parent.mkdir(parents=True); input_path.write_bytes(b'artificial approved input implementation\n')
    old_hash = hashlib.sha256(old_input).hexdigest()
    old['full_universe']['source_hashes']['universe_account_inputs_v1.py'] = old_hash; bridges._fingerprint(old)
    monkeypatch.setattr(contracts, 'capabilities', lambda: deepcopy(old))
    monkeypatch.setattr(bridges, 'capabilities', lambda: deepcopy(old))
    contract = build(old); profile = execution_profile(SEGMENTED_PROFILE, 504)
    units = dict.fromkeys(AutonomousRunBudgetV2.resource_names, 200000)
    parameters = getattr(request, 'param', {})
    units['wall_seconds'] = parameters.get('resource_wall', 200000)
    stages = deepcopy(units); stages['wall_seconds'] = parameters.get('stage_wall', units['wall_seconds'])
    auth = {'authorization_id': 'synthetic_time_extension', 'objective_id': contract['objective_id'],
        'resource_limits': units, 'stages': ['EXPLORATION'], 'expires_at': (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        'max_batches': 2, 'max_total_predictive_trials': 4, 'max_trials_per_batch': 3,
        'max_hypotheses_per_batch': 1, 'max_candidates_per_batch': 1,
        'execution_profiles': contract['scope']['execution_profiles']}
    token = object(); approvals = OwnerApprovalStoreV1(root / 'owner', owner_capability=token)
    original_summary = scope_summary(root, auth, contract, {'EXPLORATION': stages})
    approval = approvals.approve(original_summary, approver='artificial_owner', capability=token)
    campaign = ResearchCampaignV1.create(root, bind_campaign_scope(root, auth, contract=contract,
        stage_limits={'EXPLORATION': stages}, approvals=approvals, approval_ref=approval))
    base = campaign.peek_status()['base_authorization']; task_id = stable_hash('artificial-time-extension-task')
    task_root = root / 'reports' / 'tasks' / task_id; account = task_root / 'account'; account.mkdir(parents=True)
    runtime_refs = []
    for relative in sorted(bridges.REQUIRED_RUNTIME_PATHS):
        path = root / relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'artificial original governance runtime\n'); runtime_refs.append(pin(path))
    worker = root / 'scripts/run_strategy_account_v1.py'; old_worker = pin(worker)
    archives = account / 'source-archive'; archives.mkdir()
    input_archive = archives / (old_hash + '_' + input_path.name); input_archive.write_bytes(old_input)
    (archives / (old_worker['sha256'] + '_' + worker.name)).write_bytes(worker.read_bytes())
    calendar = [int((date(2020, 1, 1) + timedelta(days=index)).strftime('%Y%m%d')) for index in range(564)]
    plans = {}; items = {}
    for cost in ('BASE', 'STRESS'):
        name = 'ARTIFICIAL_' + cost
        item = {'factory': 'artificial', 'backend_options': {'checkpoint_path': str(account / (name + '_STATE.json'))},
            'execution_profile': profile, 'source_hashes': {str(input_path): old_hash, str(worker): old_worker['sha256']}}
        plan = {'strategy': {'strategy_id': name, 'source_hashes': {}}, 'runtime': item,
            'backend': {'backend': 'UNIVERSE_ACCOUNT_BACKEND_V2', 'execution_profile': profile,
                'window': {'symbols': ['000001.SZ'], 'calendar': calendar, 'account_start': calendar[60]},
                'cost': cost, 'source_hashes': {str(input_path): old_hash}}}
        plan['plan_id'] = stable_hash(plan); plans[name] = plan; items[name] = item
        campaign.reserve_operation(operation_id=cost, batch_id='BATCH', stage='EXPLORATION', kind='ACCOUNT',
            subject_identity=plan['plan_id'], upper_bounds={'account_jobs': 1, 'wall_seconds': 28800}, execution_profile=profile)
    governance = StrategyBatchGovernanceV1(account, campaign.directory / 'execution_registry.json', auth['objective_id'], plans)
    receipt = governance.confirm_campaign_scope(campaign, {'ARTIFICIAL_BASE': 'BASE', 'ARTIFICIAL_STRESS': 'STRESS'},
        {'input_identity': stable_hash('artificial-inputs'), 'novelty': {name: {'allowed': True, 'plan_id': plan['plan_id']}
            for name, plan in plans.items()}})
    governance.start('ARTIFICIAL_BASE')
    case = {'root': root, 'campaign': campaign, 'base': base, 'approvals': approvals, 'token': token, 'account': account,
        'gov': governance, 'receipt': receipt, 'profile': profile,
        'artificial_unmeasured_segments': parameters.get('unmeasured_segments', ()),
        'artificial_resource_fault': parameters.get('resource_fault')}
    produce_artificial_legacy_segment_prefix(case, 1, 6, final_outcome='PAUSED')
    job = {'root': str(account), 'objective_id': auth['objective_id'], 'input_identity': stable_hash('artificial-inputs'),
        'budget_path': str(governance.budget_path), 'plans': plans, 'items': items, 'resources': profile,
        'resource_job_total_seconds': 57600, 'source_hashes': {str(input_path): old_hash, str(worker): old_worker['sha256']}}
    job_ref = write(account / 'JOB.json', job); preview = {'request': {'phase': 'EXPLORATION'}}
    preview['preview_identity'] = stable_hash(preview); write(task_root / 'PREVIEW.json', preview)
    task_ref = write(task_root / 'TASK.json', {'task_id': task_id, 'job_path': str(account / 'JOB.json'),
        'job_sha256': job_ref['sha256'], 'input_identity': job['input_identity'],
        'plan_ids': {name: plan['plan_id'] for name, plan in plans.items()}, 'preview_identity': preview['preview_identity']})
    case.update(job=job, job_ref=job_ref, task_ref=task_ref)
    control(case, 'PAUSE')
    current['full_universe']['source_hashes']['universe_account_inputs_v1.py'] = pin(input_path)['sha256']; bridges._fingerprint(current)
    monkeypatch.setattr(contracts, 'capabilities', lambda: deepcopy(current))
    monkeypatch.setattr(bridges, 'capabilities', lambda: deepcopy(current))
    replacements = [{'path': str(input_path), 'old_sha256': old_hash, 'new_sha256': pin(input_path)['sha256'],
        'archive_ref': pin(input_archive)}]
    bridge_summary = bridges.AccountVersionCapabilityBridgeV1.summary(root, base, source_replacements=replacements,
        runtime_source_refs=runtime_refs)
    bridge_approval = approvals.approve(bridge_summary, approver='artificial_owner', capability=token)
    bridge_ref = bridges.AccountVersionCapabilityBridgeV1.install(root, base, bridge_summary, approval_ref=bridge_approval)
    proposal = root / 'reports' / 'prior.md'; proposal.write_text('artificial prior permission', encoding='utf-8')
    prior_ci = write(root / 'reports' / 'prior_ci.json', {'status': 'PASSED', 'head': 'a' * 40,
        'runtime_sources': bridge_summary['runtime_sources'], 'source_replacements': bridge_summary['source_replacements']})
    prior_summary = AccountVersionContinuationV1.summary(root, authorization_id=auth['authorization_id'], task_ref=task_ref,
        capability_bridge_ref=bridge_ref, proposal_ref=pin(proposal), ci_ref=prior_ci)
    prior_approval = approvals.approve(prior_summary, approver='artificial_owner', capability=token)
    prior_ref = AccountVersionContinuationV1.freeze(root, prior_summary, approval_ref=prior_approval)
    control(case, 'RESUME')
    prior_context = AccountVersionContinuationV1(root, prior_ref).validate()
    produce_artificial_legacy_segment_prefix(case, 7, 34, final_outcome='PAUSED',
        version_proof=prior_context.proof('ARTIFICIAL_BASE'))
    control(case, 'PAUSE')
    store = DayArtifacts(account / 'ARTIFICIAL_BASE_DAYS', identity=stable_hash('artificial-economic-execution'))
    for day in calendar[60:228]:
        store.commit(day, {'date': day, 'account': {'cash': 48750, 'lots': [{'quantity': 100}]}, 'decision': {}, 'scan': {}})
    checkpoint = {'version': 'UNIVERSE_EXECUTION_STATE_V2', 'execution_identity': store.identity,
        'calendar_identity': stable_hash(calendar), 'last_day': calendar[227], 'artifacts': store.manifest(),
        'ledger': {'cash': 48750, 'lots': {'lot1': {'quantity': 100, 'cost': 1250}}},
        'orders': {'pending1': {'quantity': 100}}, 'engine': {'_pending_exit_lots': {'lot1': 100}}}
    checkpoint['state_identity'] = stable_hash(checkpoint)
    write(Path(items['ARTIFICIAL_BASE']['backend_options']['checkpoint_path']), checkpoint)
    if parameters.get('historical_failed_other_candidate'):
        # 原治理B仍在有效Owner闭包内时，以真实公共事件构造无关旧失败；不回切源码或伪造许可。
        campaign.reserve_operation(operation_id='old_failed', batch_id='OLD', stage='EXPLORATION', kind='CANDIDATE',
            subject_identity='old_unrelated', upper_bounds={'candidate_attempts': 1, 'wall_seconds': 1})
        campaign.start_operation('old_failed')
        campaign.settle_operation('old_failed', actual={'candidate_attempts': 1, 'wall_seconds': 1},
            outcome='FAILED', evidence_identity='artificial_historical_failed')
    governance_archive = root / 'reports' / 'worker_before_extension.py'; governance_archive.write_bytes(worker.read_bytes())
    worker.write_bytes(b'artificial quota-aware governance runtime\n')
    protocol = root / 'src/chanlun_trader/research_factory/account_purpose_time_extension_v1.py'
    protocol.write_bytes(b'artificial extension proof source\n')
    new_replacements = [{'path': str(worker), 'old_sha256': old_worker['sha256'], 'new_sha256': pin(worker)['sha256'],
        'archive_ref': pin(governance_archive)}]
    runtime = sorted([pin(worker), pin(protocol)], key=lambda row: row['path'])
    economic = sorted([pin(input_path), pin(root / 'scripts/setup_account_version_continuation_v1.py'),
        pin(root / 'src/chanlun_trader/research_factory/research_evidence_v1.py')], key=lambda row: row['path'])
    proposal = root / 'reports' / 'extension.md'; proposal.write_text('artificial bounded extension', encoding='utf-8')
    ci = write(root / 'reports' / 'extension_ci.json', {'status': 'PASSED', 'head': 'b' * 40,
        'runtime_sources': runtime, 'source_replacements': new_replacements, 'economic_sources': economic})
    case['spec'] = {'authorization_id': auth['authorization_id'], 'task_ref': task_ref, 'predecessor_manifest_ref': prior_ref,
        'source_replacements': new_replacements, 'runtime_source_refs': runtime, 'economic_source_refs': economic,
        'proposal_ref': pin(proposal), 'ci_ref': ci}
    return case


def install(case):
    summary = extension.AccountPurposeTimeExtensionV1.summary(case['root'], **case['spec'])
    approval = case['approvals'].approve(summary, approver='artificial_owner', capability=case['token'])
    reference = extension.AccountPurposeTimeExtensionV1.freeze(case['root'], summary, approval_ref=approval)
    return extension.AccountPurposeTimeExtensionV1.read(case['root'], reference), summary, approval


def test_freeze_is_write_once_and_does_not_change_budget_or_original_state(extension_case):
    case = extension_case
    paths = [case['campaign'].authorization_path, case['campaign'].budget_path,
        case['campaign'].budget_path.with_name('run_budget_events.jsonl'), case['gov'].budget_path,
        case['account'] / 'JOB.json', case['account'] / 'ARTIFICIAL_BASE_START.json',
        Path(case['job']['items']['ARTIFICIAL_BASE']['backend_options']['checkpoint_path'])]
    before = {path: path.read_bytes() for path in paths}
    context, summary, approval = install(case)
    assert case['artificial_prefix_replays'] == [
        {'segments': 6, 'seconds': 4800., 'next_segment_number': 7, 'parent_and_child_real_replay': True},
        {'segments': 34, 'seconds': 27200., 'next_segment_number': 35, 'parent_and_child_real_replay': True}]
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert context.effective_total_seconds('ARTIFICIAL_BASE') == 72000
    assert summary['accounts']['ARTIFICIAL_BASE']['charged_seconds'] == 27200
    assert summary['accounts']['ARTIFICIAL_BASE']['next_segment_number'] == 35
    assert summary['accounts']['ARTIFICIAL_BASE']['checkpoint']['processed_days'] == 168
    resource_refs = {row['path']: row['sha256'] for row in summary['accounts']['ARTIFICIAL_BASE']['segment_refs']
        if row['path'].endswith('_RESOURCE.json')}
    assert len(resource_refs) == 34
    for number in range(1, 35):
        prefix = case['account'] / ('ARTIFICIAL_BASE_SEGMENT_' + str(number).zfill(6))
        charge = json.loads(Path(str(prefix) + '_CHARGE.json').read_text(encoding='utf-8'))
        assert resource_refs[str(prefix) + '_RESOURCE.json'] == charge['evidence_identity']
    assert summary['aggregate_reserved_delta'] == 86400
    assert extension.AccountPurposeTimeExtensionV1.freeze(case['root'], summary, approval_ref=approval) == context.scope_reference
    assert extension.AccountPurposeTimeExtensionV1.from_job(case['job']).proof() == context.proof()
    checkpoint = summary['accounts']['ARTIFICIAL_BASE']['checkpoint']
    assert Path(checkpoint['archive_ref']['path']).read_bytes() == before[Path(checkpoint['reference']['path'])]


def test_governance_projection_keeps_all_economic_plan_fields(extension_case):
    case = extension_case; context, summary, _ = install(case)
    prior = json.loads(Path(case['spec']['predecessor_manifest_ref']['path']).read_text(encoding='utf-8'))['summary']['active_plans']
    for name in prior:
        plan = context.successor_plan(name)
        assert plan['plan_id'] != prior[name]['plan_id']
        assert plan['backend'] == prior[name]['backend']
        assert plan['strategy'] == prior[name]['strategy']
        assert {key: value for key, value in plan['runtime'].items() if key != 'source_hashes'} == {
            key: value for key, value in prior[name]['runtime'].items() if key != 'source_hashes'}


@pytest.mark.skipif(os.name != 'nt', reason='Windows目录大小写身份回归')
def test_normalized_authorization_root_preserves_frozen_path_spelling_and_proof(extension_case):
    case = extension_case; context, value, _ = install(case)
    manifest_path = Path(context.scope_reference['path']); before = manifest_path.read_bytes()
    normalized_root = os.path.normcase(str(case['root']))
    reopened = extension.AccountPurposeTimeExtensionV1.read(normalized_root, context.scope_reference,
        base_authorization=case['base'], for_install=True)
    assert str(reopened.root) == value['root']
    assert reopened.summary_value == value and reopened.proof() == context.proof()
    result = extension.resolve_time_extension_manifest(context.scope_reference, root=normalized_root,
        authorization_id=case['base']['authorization_id'], objective_id=case['base']['objective_id'])
    assert result['proof'] == context.proof() and manifest_path.read_bytes() == before


def test_generic_reader_cannot_create_owner_approval(extension_case):
    case = extension_case; value = extension.AccountPurposeTimeExtensionV1.summary(case['root'], **case['spec'])
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_BINDING_INVALID'):
        extension.AccountPurposeTimeExtensionV1.freeze(case['root'], value, approval_ref={})
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_REQUIRED'):
        OwnerApprovalStoreV1(case['approvals'].directory).approve(value, approver='ordinary_cli', capability=object())


@pytest.mark.parametrize('change', ['checkpoint', 'day', 'source', 'archive', 'task', 'proposal', 'ci', 'resume', 'unknown', 'active'])
def test_first_freeze_rechecks_approved_boundary(extension_case, change):
    case = extension_case; value = extension.AccountPurposeTimeExtensionV1.summary(case['root'], **case['spec'])
    approval = case['approvals'].approve(value, approver='artificial_owner', capability=case['token'])
    if change == 'checkpoint':
        path = Path(case['job']['items']['ARTIFICIAL_BASE']['backend_options']['checkpoint_path'])
        row = json.loads(path.read_text()); row['ledger']['cash'] += 1
        row['state_identity'] = stable_hash({key: item for key, item in row.items() if key != 'state_identity'})
        path.write_text(json.dumps(row), encoding='utf-8')
    elif change == 'day':
        next((case['account'] / 'ARTIFICIAL_BASE_DAYS').glob('*.json.gz')).write_bytes(b'changed')
    elif change == 'source':
        Path(case['spec']['source_replacements'][0]['path']).write_bytes(b'changed governance')
    elif change == 'archive':
        Path(case['spec']['source_replacements'][0]['archive_ref']['path']).write_bytes(b'changed archive')
    elif change == 'task':
        Path(case['task_ref']['path']).write_text('{}', encoding='utf-8')
    elif change in ('proposal', 'ci'):
        Path(case['spec'][change + '_ref']['path']).write_text('{}', encoding='utf-8')
    elif change == 'resume':
        control(case, 'RESUME')
    elif change == 'unknown':
        append_parent_event(case, 'CAMPAIGN_OPERATION_UNKNOWN', {'operation_id': 'BASE'})
    else:
        append_parent_event(case, 'CAMPAIGN_EXECUTION_SEGMENT_DISPATCHED', {'operation_id': 'BASE', 'segment_number': 35,
            'profile_hash': case['job']['resources']['profile_hash'], 'upper_bound_seconds': 900.,
            'dispatched_at': datetime.now(timezone.utc).isoformat()})
    with pytest.raises((PermissionError, ValueError)):
        extension.AccountPurposeTimeExtensionV1.freeze(case['root'], value, approval_ref=approval)


def test_resolver_binds_original_operations_and_has_no_campaign_recursion(extension_case, monkeypatch):
    case = extension_case; context, value, _ = install(case)
    def forbidden(*args, **kwargs):
        raise AssertionError('pure proof reader called campaign')
    monkeypatch.setattr(ResearchCampaignV1, 'peek_status', forbidden)
    monkeypatch.setattr(ResearchCampaignV1, 'status', forbidden)
    result = extension.resolve_time_extension_manifest(context.scope_reference, root=case['root'],
        authorization_id=case['base']['authorization_id'], objective_id=case['base']['objective_id'])
    assert result['operation_ids'] == {'ARTIFICIAL_BASE': 'BASE', 'ARTIFICIAL_STRESS': 'STRESS'}
    assert result['limits']['BASE']['effective_total_seconds'] == 72000
    assert result['operation_boundaries']['BASE']['charged_seconds'] == 27200
    assert result['active_plans'] == value['successor_plans']
    with pytest.raises(PermissionError, match='AUTHORIZATION_OR_OBJECTIVE_CONFLICT'):
        extension.resolve_time_extension_manifest(context.scope_reference, root=case['root'],
            authorization_id='another', objective_id=case['base']['objective_id'])


def test_first_summary_and_freeze_do_not_call_campaign_or_governance(extension_case, monkeypatch):
    case = extension_case
    def forbidden(*args, **kwargs):
        raise AssertionError('Owner proof bootstrapped through a live campaign/governance reader')
    monkeypatch.setattr(ResearchCampaignV1, 'peek_status', forbidden)
    monkeypatch.setattr(ResearchCampaignV1, 'status', forbidden)
    monkeypatch.setattr(StrategyBatchGovernanceV1, 'segment_status', forbidden)
    context, value, _ = install(case)
    assert value['accounts']['ARTIFICIAL_BASE']['segment_count'] == 34
    assert extension.AccountPurposeTimeExtensionV1.read(case['root'], context.scope_reference,
        for_install=True).proof() == context.proof()


def test_governance_allowlist_cannot_remove_a_backend_dependency(extension_case):
    case = extension_case
    previous = json.loads(Path(case['spec']['predecessor_manifest_ref']['path']).read_text(encoding='utf-8'))['summary']
    replacement = case['spec']['source_replacements'][0]
    previous['active_plans']['ARTIFICIAL_BASE']['backend']['source_hashes'][replacement['path']] = replacement['old_sha256']
    with pytest.raises(PermissionError, match='ECONOMIC_DEPENDENCY_SOURCE_CHANGED'):
        extension._sources(case['root'], previous, case['job'], case['spec']['source_replacements'],
            case['spec']['runtime_source_refs'], case['spec']['economic_source_refs'])


def test_economic_source_cannot_be_replaced_even_with_a_new_owner_summary(extension_case):
    case = extension_case; spec = deepcopy(case['spec'])
    source = Path(spec['economic_source_refs'][0]['path']); archive = case['root'] / 'reports' / 'forbidden_business.py'
    archive.write_bytes(source.read_bytes()); old = pin(source)['sha256']; source.write_bytes(b'changed economic source')
    spec['source_replacements'].append({'path': str(source), 'old_sha256': old,
        'new_sha256': pin(source)['sha256'], 'archive_ref': pin(archive)})
    with pytest.raises(PermissionError, match='GOVERNANCE_SOURCE_ALLOWLIST_CONFLICT'):
        extension.AccountPurposeTimeExtensionV1.summary(case['root'], **spec)


def test_changed_proof_and_checkpoint_archive_are_rejected(extension_case):
    case = extension_case; context, value, _ = install(case)
    archive = Path(value['accounts']['ARTIFICIAL_BASE']['checkpoint']['archive_ref']['path'])
    archive.write_text('{}', encoding='utf-8')
    with pytest.raises(PermissionError, match='CHECKPOINT_ARCHIVE_CHANGED'):
        extension.AccountPurposeTimeExtensionV1.read(case['root'], context.scope_reference)


def test_noncapable_token_file_is_not_an_approval(extension_case):
    case = extension_case; value = extension.AccountPurposeTimeExtensionV1.summary(case['root'], **case['spec'])
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_BINDING_INVALID'):
        extension.AccountPurposeTimeExtensionV1.freeze(case['root'], value,
            approval_ref={'path': str(case['root'] / 'reports' / 'token.json'), 'sha256': 'a' * 64})


def test_existing_slot_rejects_a_conflicting_owner_approval(extension_case):
    case = extension_case; context, value, _ = install(case)
    altered = deepcopy(value); altered['expires_at'] = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    altered['summary_identity'] = stable_hash({key: item for key, item in altered.items() if key != 'summary_identity'})
    approval = case['approvals'].approve(altered, approver='artificial_owner', capability=case['token'])
    with pytest.raises(PermissionError, match='SLOT_CONFLICT'):
        extension.AccountPurposeTimeExtensionV1.freeze(case['root'], altered, approval_ref=approval)
    assert extension.AccountPurposeTimeExtensionV1.read(case['root'], context.scope_reference).proof() == context.proof()


def test_global_and_stage_reservation_capacity_is_required(extension_case):
    case = extension_case
    append_parent_event(case, 'CAMPAIGN_OPERATION_RESERVED', {'operation_id': 'OTHER', 'stage': 'EXPLORATION',
        'kind': 'DATA', 'subject_identity': stable_hash('unrelated-artificial-reservation'), 'batch_id': 'OTHER',
        'upper_bounds': {'data_experiments': 1, 'wall_seconds': 100000}})
    with pytest.raises(PermissionError, match='RESERVATION_CAPACITY_INSUFFICIENT'):
        extension.AccountPurposeTimeExtensionV1.summary(case['root'], **case['spec'])


def test_expired_proof_remains_historical_but_cannot_authorize_dispatch(extension_case, monkeypatch):
    case = extension_case; context, _, _ = install(case)
    future = datetime.now(timezone.utc) + timedelta(days=2)
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return future
    monkeypatch.setattr(extension, 'datetime', Later)
    assert extension.AccountPurposeTimeExtensionV1.read(case['root'], context.scope_reference).extension_id == context.extension_id
    with pytest.raises(PermissionError, match='EXPIRED'):
        extension.AccountPurposeTimeExtensionV1.read(case['root'], context.scope_reference, for_dispatch=True)


@pytest.mark.parametrize(('extension_case', 'reason'), [
    ({'resource_fault': 'missing'}, 'MEASURED_RESOURCE_REQUIRED'),
    ({'resource_fault': 'sha'}, 'MEASURED_RESOURCE_SHA_CONFLICT'),
    ({'resource_fault': 'dispatch'}, 'MEASURED_RESOURCE_DISPATCH_CONFLICT'),
    ({'resource_fault': 'seconds'}, 'MEASURED_RESOURCE_SECONDS_CONFLICT'),
], indirect=['extension_case'])
def test_owner_summary_requires_each_measured_resource_binding(extension_case, reason):
    case = extension_case
    with pytest.raises(PermissionError, match=reason):
        extension.AccountPurposeTimeExtensionV1.summary(case['root'], **case['spec'])
    assert not (Path(case['task_ref']['path']).parent / 'account-purpose-time-extensions').exists()


@pytest.mark.parametrize('extension_case', [{'unmeasured_segments': [17, 27]}], indirect=True)
def test_unknown_upper_bound_charges_preserve_missing_resource_without_fabrication(extension_case):
    case = extension_case
    absent = [case['account'] / ('ARTIFICIAL_BASE_SEGMENT_' + str(number).zfill(6) + '_RESOURCE.json')
        for number in (17, 27)]
    assert not any(path.exists() for path in absent)
    context, value, _ = install(case)
    row = value['accounts']['ARTIFICIAL_BASE']
    assert row['segment_count'] == 34 and row['charged_seconds'] == 27400.
    assert row['checkpoint']['processed_days'] == 168 and row['next_segment_number'] == 35
    assert not any(path.exists() for path in absent)
    assert not ({str(path) for path in absent} & {reference['path'] for reference in row['segment_refs']})
    assert extension.AccountPurposeTimeExtensionV1.read(case['root'], context.scope_reference,
        for_install=True).proof() == context.proof()



def test_reference_keeps_open_checks_without_a_third_component_walk(tmp_path, monkeypatch, record_property):
    from chanlun_trader.research_factory import secure_file_reference_v1 as secure
    target = tmp_path / '原件.json'
    target.write_bytes(b'approved bytes')
    native = secure._reject_link_components
    calls = []

    def counted(path):
        calls.append(str(path))
        return native(path)

    monkeypatch.setattr(secure, '_reject_link_components', counted)
    assert extension._reference(target, tmp_path) == pin(target)
    record_property('full_component_walks_per_reference', len(calls))
    assert len(calls) == 2, calls



@pytest.mark.parametrize('kind', ['relative', 'outside', 'parent', 'ads', 'missing', 'directory'])
def test_reference_retains_original_path_and_read_error_codes(tmp_path, kind):
    root = tmp_path / 'root'
    root.mkdir()
    target = root / 'fixed.json'
    target.write_bytes(b'original')
    values = {'relative': 'fixed.json', 'outside': tmp_path / 'outside.json',
              'parent': root / '..' / 'fixed.json', 'ads': str(target) + ':hidden',
              'missing': root / 'missing.json', 'directory': root}
    code = 'REFERENCE_CHANGED' if kind in ('missing', 'directory') else 'PATH_INVALID'
    with pytest.raises(ValueError, match='ACCOUNT_PURPOSE_TIME_EXTENSION_' + code):
        extension._reference(values[kind], root)


def test_reference_rejects_opened_object_replacement(tmp_path, monkeypatch):
    target, other = tmp_path / 'fixed.json', tmp_path / 'other.json'
    target.write_bytes(b'approved')
    other.write_bytes(b'substituted')
    native = os.open
    monkeypatch.setattr(os, 'open', lambda _path, flags: native(other, flags))
    with pytest.raises(ValueError, match='ACCOUNT_PURPOSE_TIME_EXTENSION_REFERENCE_CHANGED'):
        extension._reference(target, tmp_path)


@pytest.mark.parametrize('link_type', ['symlink', 'reparse'])
def test_reference_rejects_parent_links_before_open(tmp_path, monkeypatch, link_type):
    import stat
    from types import SimpleNamespace
    target = tmp_path / 'parent' / 'fixed.json'
    target.parent.mkdir()
    target.write_bytes(b'approved')
    native = Path.lstat

    def changed(path):
        if path == target.parent:
            return SimpleNamespace(st_mode=stat.S_IFLNK if link_type == 'symlink' else stat.S_IFDIR,
                                   st_file_attributes=0x400 if link_type == 'reparse' else 0)
        return native(path)

    def unexpected_open(*_args):
        pytest.fail('linked reference must not reach native open')

    monkeypatch.setattr(Path, 'lstat', changed)
    monkeypatch.setattr(extension._secure_files, '_component_metadata', changed)
    monkeypatch.setattr(os, 'open', unexpected_open)
    with pytest.raises(ValueError, match='ACCOUNT_PURPOSE_TIME_EXTENSION_PATH_INVALID'):
        extension._reference(target, tmp_path)
