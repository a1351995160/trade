"""一次零日账户接续的合成边界；不读取真实行情或 Owner 根。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory import account_version_capability_bridge_v1 as bridges
from chanlun_trader.research_factory import continuous_research_contract_v1 as contracts
from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
from chanlun_trader.research_factory.campaign_scope_v1 import OwnerApprovalStoreV1, bind_campaign_scope, scope_summary
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
from chanlun_trader.research_factory.exploration_governance import immutable
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.run_budget import AutonomousRunBudgetV2
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile
from test_continuous_research_contract_v1 import build


def pin(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def write(path, value):
    immutable(path, value)
    return pin(path)


@pytest.fixture
def case(tmp_path, monkeypatch, request):
    root = tmp_path.resolve()
    current = bridges.capabilities()
    source = root / bridges.BUSINESS_PATH
    source.parent.mkdir(parents=True)
    source.write_bytes(Path(bridges.__file__).with_name('universe_account_inputs_v1.py').read_bytes())
    archived = root / 'reports' / 'old_inputs.py'
    archived.parent.mkdir()
    archived.write_bytes(b'old synthetic input implementation\n')
    foundation_hash = pin(archived)['sha256']
    intermediate_bytes = b'first approved synthetic input implementation\n'
    fixture_mode = getattr(request, 'param', False)
    runner_validation = fixture_mode == 'RUNNER_VALIDATION'
    with_predecessor = False if runner_validation else fixture_mode
    old_hash = hashlib.sha256(intermediate_bytes).hexdigest() if with_predecessor else foundation_hash
    old = deepcopy(current)
    old['full_universe']['source_hashes']['universe_account_inputs_v1.py'] = foundation_hash
    bridges._fingerprint(old)
    monkeypatch.setattr(contracts, 'capabilities', lambda: deepcopy(old))
    monkeypatch.setattr(bridges, 'capabilities', lambda: deepcopy(old))
    contract = build(old)
    resource = execution_profile(SEGMENTED_PROFILE, 504)
    units = dict.fromkeys(AutonomousRunBudgetV2.resource_names, 200000)
    stages = {'EXPLORATION': deepcopy(units)}
    auth = {'authorization_id': 'synthetic_continuation', 'objective_id': contract['objective_id'],
        'resource_limits': units, 'stages': ['EXPLORATION'],
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        'max_batches': 2, 'max_total_predictive_trials': 4, 'max_trials_per_batch': 3,
        'max_hypotheses_per_batch': 1, 'max_candidates_per_batch': 1,
        'execution_profiles': contract['scope']['execution_profiles']}
    token = object(); approvals = OwnerApprovalStoreV1(root / 'protected_owner', owner_capability=token)
    scope = scope_summary(root, auth, contract, stages)
    approval = approvals.approve(scope, approver='synthetic_owner', capability=token)
    bound = bind_campaign_scope(root, auth, contract=contract, stage_limits=stages,
        approvals=approvals, approval_ref=approval)
    campaign = ResearchCampaignV1.create(root, bound)
    base = campaign.peek_status()['base_authorization']
    task_id = stable_hash('synthetic_task')
    task_root = root / 'reports' / 'tasks' / task_id
    account = task_root / 'account'; account.mkdir(parents=True)
    old_archive = account / 'source-archive' / (old_hash + '_' + source.name)
    old_archive.parent.mkdir(); old_archive.write_bytes(intermediate_bytes if with_predecessor else archived.read_bytes())
    plans, items = {}, {}
    for cost in ('BASE', 'STRESS'):
        name = 'SYNTHETIC_' + cost
        item = {'factory': 'synthetic', 'backend_options': {'checkpoint_path': str(account / (name + '_STATE.json'))},
            'execution_profile': resource, 'source_hashes': {str(source): old_hash}}
        if runner_validation:
            item['benchmark_mode'] = 'NONE'  # 真实 runner 字段在首次签发前构造。
        plan = {'strategy': {'strategy_id': name, 'source_hashes': {}}, 'runtime': item,
            'backend': {'backend': 'UNIVERSE_ACCOUNT_BACKEND_V2', 'execution_profile': resource,
                'window': {'symbols': ['000001.SZ', '000002.SZ', '000004.SZ'], 'calendar': [20200101, 20200102]},
                'cost': cost, 'source_hashes': {str(source): old_hash}}}
        plan['plan_id'] = stable_hash(plan); plans[name] = plan; items[name] = item
        campaign.reserve_operation(operation_id=cost, batch_id='BATCH', stage='EXPLORATION', kind='ACCOUNT',
            subject_identity=plan['plan_id'], upper_bounds={'account_jobs': 1, 'wall_seconds': 28800}, execution_profile=resource)
    gov = StrategyBatchGovernanceV1(account, campaign.directory / 'execution_registry.json', auth['objective_id'], plans)
    gov.confirm_campaign_scope(campaign, {'SYNTHETIC_BASE': 'BASE', 'SYNTHETIC_STRESS': 'STRESS'},
        {'input_identity': stable_hash('inputs'), 'novelty': {name: {'allowed': True, 'plan_id': plan['plan_id']}
            for name, plan in plans.items()}})
    gov.start('SYNTHETIC_BASE')
    for index in range(1, 7):
        dispatch = gov.start_segment('SYNTHETIC_BASE')
        resource_ref = write(account / ('SYNTHETIC_BASE_SEGMENT_' + str(index).zfill(6) + '_RESOURCE.json'),
            {'synthetic': True, 'segment': index})
        gov.end_segment('SYNTHETIC_BASE', index, seconds=800., evidence_identity=resource_ref['sha256'],
            outcome='PAUSED' if index == 6 else 'CONTINUE')
    job = {'root': str(account), 'objective_id': auth['objective_id'], 'input_identity': stable_hash('inputs'),
        'budget_path': str(gov.budget_path), 'plans': plans, 'items': items, 'resources': resource,
        'resource_job_total_seconds': 57600, 'source_hashes': {str(source): old_hash}}
    job_ref = write(account / 'JOB.json', job)
    preview = {'request': {'phase': 'EXPLORATION'}}
    preview['preview_identity'] = stable_hash(preview)
    write(task_root / 'PREVIEW.json', preview)
    task_ref = write(task_root / 'TASK.json', {'task_id': task_id, 'job_path': str(account / 'JOB.json'),
        'job_sha256': job_ref['sha256'], 'input_identity': job['input_identity'],
        'plan_ids': {name: plan['plan_id'] for name, plan in plans.items()}, 'preview_identity': preview['preview_identity']})
    control = {'sequence': 1, 'previous_head': None, 'event': 'PAUSE', 'job_sha256': job_ref['sha256'],
        'receipt_id': gov.active()['receipt_id']}
    control['event_id'] = stable_hash(control)
    write(account / 'CONTROL_000001.json', control)
    runtime_refs = []
    for relative in sorted(bridges.REQUIRED_RUNTIME_PATHS):
        path = root / relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic runtime closure\n'); runtime_refs.append(pin(path))
    predecessor_ref = None
    if with_predecessor:
        from chanlun_trader.research_factory import report_repair_capability_bridge_v1 as old_bridges
        new_bytes = source.read_bytes(); source.write_bytes(intermediate_bytes)
        previous = deepcopy(current)
        previous['full_universe']['source_hashes']['universe_account_inputs_v1.py'] = old_hash
        bridges._fingerprint(previous)
        monkeypatch.setattr(old_bridges, 'capabilities', lambda: deepcopy(previous))
        prior_summary = old_bridges.ReportRepairCapabilityBridgeV1.summary(root, base, source_replacements=[{
            'path': str(source), 'old_sha256': foundation_hash, 'new_sha256': old_hash, 'archive_ref': pin(archived)}])
        prior_approval = approvals.approve(prior_summary, approver='synthetic_prior_owner', capability=token)
        predecessor_ref = old_bridges.ReportRepairCapabilityBridgeV1.install(root, base, prior_summary, approval_ref=prior_approval)
        source.write_bytes(new_bytes)
        monkeypatch.setattr(old_bridges, 'capabilities', lambda: deepcopy(current))
    replacements = [{'path': str(source), 'old_sha256': old_hash, 'new_sha256': pin(source)['sha256'],
        'archive_ref': pin(old_archive)}]
    monkeypatch.setattr(contracts, 'capabilities', lambda: deepcopy(current))
    monkeypatch.setattr(bridges, 'capabilities', lambda: deepcopy(current))
    bridge_summary = bridges.AccountVersionCapabilityBridgeV1.summary(root, base,
        source_replacements=replacements, runtime_source_refs=runtime_refs, predecessor_bridge_ref=predecessor_ref)
    bridge_approval = approvals.approve(bridge_summary, approver='synthetic_owner', capability=token)
    bridge_ref = bridges.AccountVersionCapabilityBridgeV1.install(root, base, bridge_summary, approval_ref=bridge_approval)
    proposal = root / 'reports' / 'proposal.md'; proposal.write_text('一次有限版本接续\n', encoding='utf-8')
    ci_ref = write(root / 'reports' / 'ci.json', {'status': 'PASSED', 'head': 'a' * 40,
        'runtime_sources': bridge_summary['runtime_sources'], 'source_replacements': bridge_summary['source_replacements']})
    return {'root': root, 'campaign': campaign, 'base': base, 'approvals': approvals, 'token': token,
        'bridge_summary': bridge_summary, 'bridge_ref': bridge_ref, 'task_ref': task_ref, 'job': job, 'gov': gov,
        'proposal_ref': pin(proposal), 'ci_ref': ci_ref, 'current': current, 'old': old,
        'predecessor_ref': predecessor_ref}


def summary(case):
    return AccountVersionContinuationV1.summary(case['root'], authorization_id=case['base']['authorization_id'],
        task_ref=case['task_ref'], capability_bridge_ref=case['bridge_ref'], proposal_ref=case['proposal_ref'], ci_ref=case['ci_ref'])


def install(case):
    value = summary(case)
    approval = case['approvals'].approve(value, approver='synthetic_owner', capability=case['token'])
    ref = AccountVersionContinuationV1.freeze(case['root'], value, approval_ref=approval)
    return AccountVersionContinuationV1(case['root'], ref).validate(), value, approval


def resume(case):
    """使用原公共 CONTROL 合同追加 RESUME，不修改既有暂停事件。"""
    root = case['gov'].root
    previous = json.loads((root / 'CONTROL_000001.json').read_text(encoding='utf-8'))
    value = {'sequence': 2, 'previous_head': previous['event_id'], 'event': 'RESUME',
        'job_sha256': previous['job_sha256'], 'receipt_id': previous['receipt_id'],
        'recorded_at': datetime.now(timezone.utc).isoformat()}
    value['event_id'] = stable_hash(value)
    return write(root / 'CONTROL_000002.json', value)


def test_install_preserves_ledger_start_and_reservations_and_is_idempotent(case):
    paths = [case['campaign'].authorization_path, case['campaign'].budget_path,
        case['campaign'].budget_path.with_name('run_budget_events.jsonl'), case['gov'].budget_path,
        case['gov'].root / 'SYNTHETIC_BASE_START.json', case['gov'].root / 'JOB.json']
    before = {path: path.read_bytes() for path in paths}
    ctx, value, approval = install(case)
    assert ctx.governance.segment_status('SYNTHETIC_BASE')['next_segment_number'] == 7
    assert ctx.governance.segment_status('SYNTHETIC_BASE')['charged_seconds'] == 4800.
    assert case['campaign'].peek_status()['operations']['STRESS']['status'] == 'RESERVED'
    assert AccountVersionContinuationV1.freeze(case['root'], value, approval_ref=approval) == ctx.scope_reference
    changed = {}
    for path, original in before.items():
        actual = path.read_bytes()
        if actual != original:
            change = {'before_sha256': hashlib.sha256(original).hexdigest(),
                'after_sha256': hashlib.sha256(actual).hexdigest()}
            if path.suffix == '.json':
                old_value, new_value = json.loads(original), json.loads(actual)
                change['changed_top_level_fields'] = sorted(key for key in old_value.keys() | new_value.keys()
                    if old_value.get(key) != new_value.get(key))
            changed[str(path)] = change
    assert not changed, json.dumps(changed, ensure_ascii=False, sort_keys=True)
    assert AccountVersionContinuationV1.from_job(case['job']).proof() == ctx.proof()
    assert {k: v for k, v in ctx.proof('SYNTHETIC_BASE').items() if k != 'purpose'} == ctx.proof()


def test_versioned_segments_extend_original_prefix_and_receipt_projection(case):
    ctx, value, approval = install(case)
    gov = case['gov']; dispatch = gov.start_segment('SYNTHETIC_BASE')
    assert ctx.validate(for_dispatch=True).proof() == ctx.proof()
    with pytest.raises(PermissionError, match='ACCOUNT_VERSION_PAUSED'):
        ctx.record_dispatch('SYNTHETIC_BASE', dispatch)
    resume(case)
    ctx.record_dispatch('SYNTHETIC_BASE', dispatch)
    assert ctx.validate_dispatch('SYNTHETIC_BASE', 6) is None
    assert ctx.segment_proof('SYNTHETIC_BASE', 7) == ctx.proof('SYNTHETIC_BASE')
    assert ctx.validate_dispatch('SYNTHETIC_BASE', 7)['dispatch_id'] == dispatch['dispatch_id']
    projected = ctx.project_receipt(gov.active_execution('SYNTHETIC_BASE'), 'SYNTHETIC_BASE')
    assert projected['strategy_plans']['SYNTHETIC_BASE'] == ctx.active_plan('SYNTHETIC_BASE')
    assert projected['receipt_id'] == gov.active()['receipt_id']
    gov.end_segment('SYNTHETIC_BASE', 7, seconds=5, evidence_identity='synthetic-new-resource')
    checkpoint = Path(case['job']['items']['SYNTHETIC_BASE']['backend_options']['checkpoint_path'])
    write(checkpoint, {'last_day': 20200102})
    assert ctx.validate().governance.segment_status('SYNTHETIC_BASE')['charged_seconds'] == 4805.
    assert AccountVersionContinuationV1.freeze(case['root'], value, approval_ref=approval) == ctx.scope_reference


@pytest.mark.parametrize('change', ['day', 'active', 'unknown', 'resume', 'source', 'archive'])
def test_first_freeze_rejects_unsafe_boundaries(case, change):
    gov = case['gov']
    if change == 'day':
        write(Path(case['job']['items']['SYNTHETIC_BASE']['backend_options']['checkpoint_path']), {'last_day': 1})
    elif change == 'active':
        gov.start_segment('SYNTHETIC_BASE')
    elif change == 'unknown':
        case['campaign'].mark_unknown('BASE', 'synthetic interrupted')
    elif change == 'resume':
        path = gov.root / 'CONTROL_000002.json'
        old = json.loads((gov.root / 'CONTROL_000001.json').read_text(encoding='utf-8'))
        value = {**old, 'sequence': 2, 'previous_head': old['event_id'], 'event': 'RESUME'}
        value['event_id'] = stable_hash({k: v for k, v in value.items() if k != 'event_id'}); write(path, value)
    else:
        path = Path(case['bridge_summary']['source_replacements'][0]['path']) if change == 'source' else (
            gov.root / 'source-archive' / (case['bridge_summary']['source_replacements'][0]['old_sha256'] + '_universe_account_inputs_v1.py'))
        path.write_bytes(b'changed')
    with pytest.raises((ValueError, PermissionError)):
        summary(case)


def test_manifest_and_sidecar_cannot_be_self_approved(case):
    ctx, _, _ = install(case)
    resume(case)
    dispatch = case['gov'].start_segment('SYNTHETIC_BASE')
    with pytest.raises((ValueError, PermissionError)):
        ctx.validate_dispatch('SYNTHETIC_BASE', 7)
    ctx.record_dispatch('SYNTHETIC_BASE', dispatch)
    sidecar = ctx._dispatch_path('SYNTHETIC_BASE', 7)
    sidecar.write_text('{}', encoding='utf-8')
    with pytest.raises((ValueError, PermissionError)):
        ctx.validate_dispatch('SYNTHETIC_BASE', 7)
    manifest = json.loads(Path(ctx.scope_reference['path']).read_text(encoding='utf-8'))
    manifest['summary']['grant_delta'] = 1
    manifest['identity'] = stable_hash({k: v for k, v in manifest.items() if k != 'identity'})
    Path(ctx.scope_reference['path']).write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises((ValueError, PermissionError)):
        ctx.validate()


def test_unrelated_old_jobs_keep_no_continuation_semantics(tmp_path):
    assert AccountVersionContinuationV1.from_job({}) is None
    assert AccountVersionContinuationV1.from_job({'root': str(tmp_path)}) is None


@pytest.mark.parametrize('crash_at,seconds', [('DISPATCH', None), ('CHARGE', 5.), ('CHARGE', 0.)])
def test_public_reconcile_repairs_parent_first_crash_without_dispatch_or_charge(case, monkeypatch, crash_at, seconds):
    from chanlun_trader.research_factory import etf_account_governance_v1 as governance
    from scripts import run_strategy_account_v1 as runner
    ctx, _, _ = install(case)
    resume(case)
    gov = case['gov']; name = 'SYNTHETIC_BASE'
    suffix = '_SEGMENT_000007_' + crash_at + '.json'
    original_write = governance.immutable

    def crash_before_local_mirror(path, value):
        if str(path).endswith(suffix):
            raise RuntimeError('synthetic crash after parent event')
        return original_write(path, value)

    if crash_at == 'CHARGE':
        dispatch = gov.start_segment(name)
        ctx.record_dispatch(name, dispatch)
    with monkeypatch.context() as patch:
        patch.setattr(governance, 'immutable', crash_before_local_mirror)
        with pytest.raises(RuntimeError, match='synthetic crash after parent event'):
            if crash_at == 'DISPATCH':
                gov.start_segment(name)
            else:
                gov.end_segment(name, 7, seconds=seconds, evidence_identity='synthetic-resource')
    ledger = case['campaign'].budget_path.with_name('run_budget_events.jsonl')
    before = ledger.read_bytes()
    with pytest.raises(PermissionError, match='PARENT_'):
        ctx.validate()
    with pytest.raises(PermissionError, match='DISPATCH_MODE_INVALID'):
        ctx.validate(for_dispatch=True, for_reconcile=True)
    assert ctx.mirrors_pending() is True
    repaired = runner.reconcile_account_version_prefix(case['job'])
    assert repaired.proof() == ctx.proof()
    assert ledger.read_bytes() == before
    state = repaired.governance.segment_status(name)
    assert len(state['segments']) == 7
    assert state['charged_seconds'] == 4800. + (seconds or 0.)
    assert repaired.mirrors_pending() is False
    assert repaired.validate_dispatch(name, 7)['account_version_continuation'] == ctx.proof(name)
    assert not (gov.root / (name + '_SEGMENT_000007_WORKER.json')).exists()
    if crash_at == 'DISPATCH':
        proof = json.loads((gov.root / (name + '_SEGMENT_000007_MIRROR_REPAIR.json')).read_text(encoding='utf-8'))
        assert proof['worker_absent'] is True


def test_reconcile_never_backfills_version_proof_for_existing_worker(case, monkeypatch):
    from chanlun_trader.research_factory import etf_account_governance_v1 as governance
    from scripts import run_strategy_account_v1 as runner
    ctx, _, _ = install(case)
    resume(case)
    name = 'SYNTHETIC_BASE'; original_write = governance.immutable

    def crash_before_local_dispatch(path, value):
        if str(path).endswith('_SEGMENT_000007_DISPATCH.json'):
            raise RuntimeError('synthetic crash after parent event')
        return original_write(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(governance, 'immutable', crash_before_local_dispatch)
        with pytest.raises(RuntimeError):
            case['gov'].start_segment(name)
    write(case['gov'].root / (name + '_SEGMENT_000007_WORKER.json'), {'pid': 1})
    with pytest.raises(PermissionError, match='DISPATCH_VERSION_WORKER_ALREADY_PRESENT'):
        runner.reconcile_account_version_prefix(case['job'])
    assert not ctx._dispatch_path(name, 7).exists()
    assert not (case['gov'].root / (name + '_SEGMENT_000007_DISPATCH.json')).exists()


@pytest.mark.parametrize('conflict', [None, 'marker', 'worker', 'access'])
def test_reconcile_dispatch_written_before_version_sidecar_is_proven_not_launched(case, conflict):
    from scripts import run_strategy_account_v1 as runner
    ctx, _, _ = install(case)
    resume(case)
    name = 'SYNTHETIC_BASE'; gov = case['gov']
    dispatch = gov.start_segment(name)
    prefix = name + '_SEGMENT_000007'
    marker = gov.root / (prefix + '_MIRROR_REPAIR.json')
    if conflict is not None:
        path = marker if conflict == 'marker' else gov.root / (prefix + (
            '_WORKER.json' if conflict == 'worker' else '_INPUT_ACCESS.json'))
        write(path, {'synthetic_conflict': True})
    ledger = case['campaign'].budget_path.with_name('run_budget_events.jsonl')
    before = ledger.read_bytes()
    if conflict is not None:
        with pytest.raises(PermissionError):
            runner.reconcile_account_version_prefix(case['job'])
        assert not ctx._dispatch_path(name, 7).exists()
        assert ledger.read_bytes() == before
        return
    assert ctx.mirrors_pending() is True
    repaired = runner.reconcile_account_version_prefix(case['job'])
    assert repaired.mirrors_pending() is False
    assert repaired.validate_dispatch(name, 7)['dispatch_id'] == dispatch['dispatch_id']
    proof = json.loads(marker.read_text(encoding='utf-8'))
    assert proof['worker_absent'] is True and proof['dispatch_id'] == dispatch['dispatch_id']
    assert ledger.read_bytes() == before
    assert repaired.governance.segment_status(name)['pending'] == dispatch
    assert repaired.governance.segment_status(name)['charged_seconds'] == 4800.
