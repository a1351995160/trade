"""人工完整Owner/扩时/失败生命周期；不读取真实价格或许可，不启动worker。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest

from chanlun_trader.research_factory import failed_account_recovery_v1 as recovery
from chanlun_trader.research_factory import account_purpose_time_extension_v1 as extension
from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
from chanlun_trader.research_factory.campaign_scope_v1 import OwnerApprovalStoreV1, validate_scope_policy
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.universe_execution_artifacts_v1 import DayArtifacts
from chanlun_trader.research_factory.universe_execution_profile_v1 import segment_charge
from test_account_purpose_time_extension_v1 import (
    control, extension_case, install as install_extension, pin, write,
)


def append_checkpoint_days(case, target):
    """人工日工件只追加，同一状态保留现金/持仓/待单；不冒充真实引擎执行。"""
    name = 'ARTIFICIAL_BASE'; path = Path(case['job']['items'][name]['backend_options']['checkpoint_path'])
    checkpoint = json.loads(path.read_text(encoding='utf-8')); old = deepcopy(checkpoint)
    store = DayArtifacts(case['account'] / (name + '_DAYS'), identity=checkpoint['execution_identity'],
        days=checkpoint['artifacts']['days'])
    calendar = case['job']['plans'][name]['backend']['window']['calendar']; first = 60
    for day in calendar[first + len(store.days):first + target]:
        store.commit(day, {'date': day, 'account': {'cash': 48750, 'lots': [{'quantity': 100}]},
            'decision': {}, 'scan': {}, 'fixture_only': 'ARTIFICIAL_FAILED_ACCOUNT_RECOVERY_DAYS'})
    checkpoint.update(artifacts=store.manifest(), last_day=store.days[-1]['date'])
    checkpoint['state_identity'] = stable_hash({key: value for key, value in checkpoint.items() if key != 'state_identity'})
    path.write_text(json.dumps(checkpoint, ensure_ascii=False), encoding='utf-8')
    assert checkpoint['artifacts']['days'][:len(old['artifacts']['days'])] == old['artifacts']['days']
    assert all(checkpoint[key] == old[key] for key in ('ledger', 'orders', 'engine'))
    return checkpoint


def produce_artificial_time_extension_failure_prefix(case, owner_extension):
    """生成人工旧35–50历史，随后由真实父/子/版本验证器完整重放。

    这不是公共派发或worker计量记录；Owner扩时仍由真实服务批准、安装。
    不改原1–34段及UNKNOWN缺件，不替换新51段的公共派发、恢复安装或验证。
    """
    account = case['account']; name = 'ARTIFICIAL_BASE'; profile = case['profile']
    existing = sorted(account.glob(name + '_SEGMENT_*_CHARGE.json'))
    assert len(existing) == 34
    charged = sum((json.loads(path.read_text(encoding='utf-8'))['seconds'] for path in existing), 0.0)
    head = json.loads(existing[-1].read_text(encoding='utf-8'))['charge_id']
    # 旧扩时安装后proof不随各段收费变化；只从真实已验证context取得一次。
    version = AccountVersionContinuationV1.from_job(case['job'])
    version_proof = version.proof(name); time_proof = owner_extension.proof()
    assert version.time_extension_installed and version_proof['account_purpose_time_extension'] == time_proof
    assert 'failed_account_recovery' not in version_proof
    ledger = case['campaign'].budget_path.with_name('run_budget_events.jsonl')
    initial_event_count = len(ledger.read_text(encoding='utf-8').splitlines())
    events = []; clock = datetime.now(timezone.utc)

    def event(kind, payload, timestamp):
        value = {'schema_version': 'autonomous-run-budget-event-v1', 'run_id': case['base']['authorization_id'],
            'event_type': kind, 'payload': payload, 'timestamp': timestamp}
        value['event_id'] = stable_hash({key: value[key] for key in ('run_id', 'event_type', 'payload')})
        events.append(value)

    for number in range(35, 51):
        prefix = name + '_SEGMENT_' + str(number).zfill(6)
        dispatched_at = (clock + timedelta(microseconds=2 * number)).isoformat()
        charged_at = (clock + timedelta(microseconds=2 * number + 1)).isoformat()
        parent_dispatch = {'operation_id': 'BASE', 'segment_number': number, 'profile_hash': profile['profile_hash'],
            'upper_bound_seconds': 900., 'dispatched_at': dispatched_at, 'time_extension_proof': deepcopy(time_proof)}
        dispatch = {'schema_version': 'strategy-account-segment-dispatch-v1', 'kind': name,
            'receipt_id': case['receipt']['receipt_id'], 'segment_number': number, 'profile_hash': profile['profile_hash'],
            'charged_before': charged, 'upper_bound_seconds': 900., 'previous_head': head,
            'dispatched_at': dispatched_at, 'time_extension_proof': deepcopy(time_proof)}
        dispatch['dispatch_id'] = stable_hash(dispatch)
        write(account / (prefix + '_DISPATCH.json'), dispatch)
        write(account / (prefix + '_EXECUTION_VERSION.json'), {'schema_version': 'ACCOUNT_VERSION_SEGMENT_V1',
            'dispatch_id': dispatch['dispatch_id'], 'segment_number': number,
            'account_version_continuation': deepcopy(version_proof)})
        seconds = 900. if number < 50 else recovery.CANONICAL_SECONDS - charged
        outcome = 'CONTINUE' if number < 50 else 'FAILED'
        resource = write(account / (prefix + '_RESOURCE.json'), {
            'fixture_only': 'ARTIFICIAL_OLD_TIME_EXTENSION_FAILURE_PREFIX', 'artificial_segment': number,
            'not_real_worker_measurement_or_charge': True, 'artificial_measured_seconds': seconds,
            'dispatch_id': dispatch['dispatch_id'], 'elapsed_wall_seconds': seconds,
            'time_extension_proof': deepcopy(time_proof), 'account_version_continuation': deepcopy(version_proof),
            'returncode': 75 if number < 50 else 1})
        amount, basis = segment_charge(seconds, dispatch['upper_bound_seconds'], resource['sha256'], outcome=outcome)
        parent_charge = {'operation_id': 'BASE', 'segment_number': number, 'measured_seconds': seconds,
            'seconds': amount, 'basis': basis, 'evidence_identity': resource['sha256'], 'outcome': outcome,
            'charged_at': charged_at, 'time_extension_proof': deepcopy(time_proof)}
        charge = {'schema_version': 'strategy-account-segment-charge-v1', 'dispatch_id': dispatch['dispatch_id'],
            **{key: deepcopy(parent_charge[key]) for key in ('measured_seconds', 'seconds', 'basis',
                'evidence_identity', 'outcome', 'charged_at', 'time_extension_proof')}}
        charge['charge_id'] = stable_hash(charge)
        write(account / (prefix + '_CHARGE.json'), charge)
        event('CAMPAIGN_EXECUTION_SEGMENT_DISPATCHED', parent_dispatch, dispatched_at)
        event('CAMPAIGN_EXECUTION_SEGMENT_CHARGED', parent_charge, charged_at)
        if basis == 'MEASURED_FAILED_RESOURCE_BOUND_VIOLATION':
            event('CAMPAIGN_PAUSED', {'reason': 'DISPATCH_RESOURCE_BOUND_VIOLATION', 'operation_id': 'BASE',
                'sequence': initial_event_count + len(events)}, charged_at)
        charged += amount; head = charge['charge_id']
    assert charged == recovery.CANONICAL_SECONDS
    with ledger.open('a', encoding='utf-8') as output:
        output.write(''.join(json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n' for value in events))
    return head


@pytest.fixture
def recovery_case(extension_case):
    """真实Owner扩时＋人工旧35–50历史＋真实结算/完整重放；没有worker或真实派发。"""
    case = extension_case; owner_extension, _, _ = install_extension(case)
    case['campaign'].install_account_purpose_time_extension(owner_extension.scope_reference)
    case['gov'].reconcile_segment_mirrors('ARTIFICIAL_BASE')
    control(case, 'RESUME')
    expected_head = produce_artificial_time_extension_failure_prefix(case, owner_extension)
    case['gov'].settle('ARTIFICIAL_BASE', completed=False, seconds=recovery.CANONICAL_SECONDS,
        result_hash=None, error='ARTIFICIAL_ORIGINAL_WORKER_FAILED')
    append_checkpoint_days(case, recovery.COMMITTED_DAYS)
    control(case, 'PAUSE')
    version = AccountVersionContinuationV1.from_job(case['job'])
    authority = version._authority_ledger_reference()
    parent = case['campaign']._peek_execution_status(); op = parent['operations']['BASE']
    state = case['gov'].segment_status('ARTIFICIAL_BASE')
    version._parent_prefix(state, op, version.summary_value['accounts']['ARTIFICIAL_BASE']['segment_count'])
    for number in range(1, 51):
        version._validate_dispatch_from_state('ARTIFICIAL_BASE', number, state)
    version._require_aggregate_authority(authority)
    assert len(state['segments']) == len(op['segments']) == 50
    assert state['pending'] is None and op.get('active_segment') is None
    assert state['head'] == expected_head and state['next_segment_number'] == 51
    assert state['charged_seconds'] == recovery.CANONICAL_SECONDS
    assert state['segments'][-1]['charge']['outcome'] == 'FAILED'
    assert state['segments'][-1]['charge']['basis'] == 'MEASURED_FAILED_RESOURCE_BOUND_VIOLATION'
    assert op['status'] == 'FAILED' and op['active_wall_seconds'] == recovery.CANONICAL_SECONDS
    assert op['actual']['wall_seconds'] == 42340 and op['actual']['account_jobs'] == 1
    assert 'BASE' in parent['trial_usage']['completed_trial_ids']
    assert parent['operations']['STRESS']['status'] == 'RESERVED'
    assert parent['operations']['STRESS'].get('active_segment') is None
    checkpoint = json.loads(Path(case['job']['items']['ARTIFICIAL_BASE']['backend_options']['checkpoint_path'])
        .read_text(encoding='utf-8'))
    assert len(checkpoint['artifacts']['days']) == recovery.COMMITTED_DAYS
    case['artificial_recovery_prefix_replay'] = {'first': 35, 'last': 50, 'full_segment_count': 50,
        'charged_seconds': state['charged_seconds'], 'next_segment_number': state['next_segment_number'],
        'parent_and_child_and_version_real_replay': True, 'not_real_dispatch_or_measurement': True}
    worker = case['root'] / 'scripts/run_strategy_account_v1.py'
    archive = case['root'] / 'reports' / 'worker_before_failed_account_recovery.py'
    archive.write_bytes(worker.read_bytes()); old_hash = pin(worker)['sha256']
    worker.write_bytes(b'artificial production failed-account recovery runtime\n')
    protocol = case['root'] / 'src/chanlun_trader/research_factory/failed_account_recovery_v1.py'
    protocol.write_bytes(b'artificial owner recovery protocol source\n')
    added_runtime = [pin(protocol)]
    for relative in sorted(recovery.REQUIRED_RUNTIME_PATHS - {
            'src/chanlun_trader/research_factory/failed_account_recovery_v1.py'}):
        source = case['root'] / relative; source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'artificial pinned recovery governance runtime\n'); added_runtime.append(pin(source))
    replacements = [{'path': str(worker), 'old_sha256': old_hash, 'new_sha256': pin(worker)['sha256'],
        'archive_ref': pin(archive)}]
    runtime = sorted([pin(Path(ref['path'])) for ref in owner_extension.summary_value['runtime_sources']]
        + added_runtime, key=lambda ref: ref['path'])
    economic = deepcopy(owner_extension.summary_value['economic_sources'])
    proposal = case['root'] / 'reports' / 'failed_account_recovery.md'
    proposal.write_text('人工Owner批准原失败用途一次性接续；不增加额度或试验。', encoding='utf-8')
    ci_ref = write(case['root'] / 'reports' / 'failed_account_recovery_ci.json', {
        'status': 'PASSED', 'head': 'c' * 40, 'runtime_sources': runtime,
        'source_replacements': replacements, 'economic_sources': economic,
        'fixture_only': 'ARTIFICIAL_CI_EVIDENCE_NOT_PRODUCTION_ACCEPTANCE'})
    case['extension'] = owner_extension
    case['recovery_spec'] = {'authorization_id': case['base']['authorization_id'], 'task_ref': case['task_ref'],
        'predecessor_manifest_ref': owner_extension.scope_reference, 'source_replacements': replacements,
        'runtime_source_refs': runtime, 'economic_source_refs': economic, 'proposal_ref': pin(proposal), 'ci_ref': ci_ref}
    return case


def freeze_recovery(case):
    summary = recovery.FailedAccountRecoveryV1.summary(case['root'], **case['recovery_spec'])
    approval = case['approvals'].approve(summary, approver='artificial_owner', capability=case['token'])
    reference = recovery.FailedAccountRecoveryV1.freeze(case['root'], summary, approval_ref=approval)
    return recovery.FailedAccountRecoveryV1.read(case['root'], reference), summary, approval


def test_owner_freeze_preserves_original_failure_costs_and_economic_prefix(recovery_case):
    case = recovery_case; name = 'ARTIFICIAL_BASE'
    paths = [case['campaign'].authorization_path, case['campaign'].budget_path,
        case['campaign'].budget_path.with_name('run_budget_events.jsonl'), case['gov'].budget_path,
        case['account'] / 'JOB.json', case['account'] / (name + '_SETTLEMENT.json'),
        case['account'] / (name + '_SEGMENT_000050_CHARGE.json'),
        Path(case['job']['items'][name]['backend_options']['checkpoint_path'])]
    before = {path: path.read_bytes() for path in paths}
    context, value, approval = freeze_recovery(case)
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert context.original_failed_account == value['original_failed_account'] == name
    account = value['accounts'][name]
    assert account['status_at_install'] == 'FAILED' and account['segment_count'] == 50
    assert account['charged_seconds'] == recovery.CANONICAL_SECONDS and account['parent_actual']['wall_seconds'] == 42340
    assert account['remaining_reserved_seconds'] == value['restored_reserved_seconds'] == 29660
    assert account['next_segment_number'] == 51 and account['checkpoint']['processed_days'] == 277
    assert value['accounts']['ARTIFICIAL_STRESS']['remaining_reserved_seconds'] == 0
    assert value['accounts']['ARTIFICIAL_STRESS']['status_at_install'] == 'RESERVED'
    assert all(context.effective_total_seconds(kind) == 72000 for kind in case['job']['plans'])
    assert Path(account['checkpoint']['archive_ref']['path']).read_bytes() == before[paths[-1]]
    assert recovery.FailedAccountRecoveryV1.freeze(case['root'], value, approval_ref=approval) == context.scope_reference
    assert recovery.FailedAccountRecoveryV1.from_job(case['job']).proof() == context.proof()
    for kind, plan in case['extension'].summary_value['successor_plans'].items():
        current = context.successor_plan(kind)
        assert current['backend'] == plan['backend'] and current['strategy'] == plan['strategy']
        assert {key: item for key, item in current['runtime'].items() if key != 'source_hashes'} == {
            key: item for key, item in plan['runtime'].items() if key != 'source_hashes'}


def test_resolver_and_scope_do_not_bootstrap_from_campaign_or_account_api(recovery_case, monkeypatch):
    case = recovery_case
    def forbidden(*args, **kwargs):
        raise AssertionError('Owner proof reader entered campaign/account execution API')
    for name in ('peek_status', 'status', '_peek_execution_status'):
        monkeypatch.setattr(ResearchCampaignV1, name, forbidden)
    monkeypatch.setattr(StrategyBatchGovernanceV1, 'segment_status', forbidden)
    monkeypatch.setattr(AccountVersionContinuationV1, 'validate', forbidden)
    context, value, _ = freeze_recovery(case)
    result = recovery.resolve_failed_account_recovery_manifest(context.scope_reference, root=case['root'],
        authorization_id=case['base']['authorization_id'], objective_id=case['base']['objective_id'])
    assert result['proof'] == context.proof() and result['account'] == value['accounts']['ARTIFICIAL_BASE']
    assert result['operation_id'] == 'BASE' and result['operation_ids'] == {'ARTIFICIAL_BASE': 'BASE', 'ARTIFICIAL_STRESS': 'STRESS'}
    assert validate_scope_policy(case['root'], case['base']) == case['base']['scope_policy']


def test_only_a_fresh_signed_context_may_inherit_old_sources(recovery_case):
    case = recovery_case; context, _, _ = freeze_recovery(case); previous = case['extension'].summary_value
    assert context.require_predecessor(case['extension'].scope_reference) == previous
    assert recovery.validate_predecessor_sources(previous, recovery=context) == context.summary_value['source_replacements']
    with pytest.raises(PermissionError, match='SIGNED_CONTEXT_REQUIRED'):
        recovery.validate_predecessor_sources(previous, recovery={'historical': True})
    with pytest.raises(TypeError):
        recovery.FailedAccountRecoveryV1.read(case['root'], context.scope_reference, historical=True)
    fake = recovery.FailedAccountRecoveryV1(case['root'], {'path': str(case['root'] / 'reports' / 'fake.json'), 'sha256': '0' * 64})
    fake.summary_value = deepcopy(context.summary_value)
    with pytest.raises((PermissionError, ValueError)):
        recovery.validate_predecessor_sources(previous, recovery=fake)
    changed = deepcopy(previous); changed['expires_at'] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with pytest.raises(PermissionError, match='PREDECESSOR_SUMMARY_CONFLICT'):
        recovery.validate_predecessor_sources(changed, recovery=context)


def test_generic_service_has_no_owner_signing_capability(recovery_case):
    case = recovery_case; value = recovery.FailedAccountRecoveryV1.summary(case['root'], **case['recovery_spec'])
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_REQUIRED'):
        OwnerApprovalStoreV1(case['approvals'].directory).approve(value, approver='ordinary_cli', capability=object())
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_BINDING_INVALID'):
        recovery.FailedAccountRecoveryV1.freeze(case['root'], value, approval_ref={})
    assert not (Path(case['task_ref']['path']).parent / 'failed-account-recoveries').exists()


@pytest.mark.parametrize('changed', ['resource', 'charge', 'settlement', 'day', 'source', 'archive', 'checkpoint', 'economic', 'ci', 'predecessor'])
def test_freeze_rejects_changes_to_approved_old_and_new_boundary(recovery_case, changed):
    case = recovery_case; value = recovery.FailedAccountRecoveryV1.summary(case['root'], **case['recovery_spec'])
    approval = case['approvals'].approve(value, approver='artificial_owner', capability=case['token'])
    if changed in ('resource', 'charge'):
        target = case['account'] / ('ARTIFICIAL_BASE_SEGMENT_000050_' + changed.upper() + '.json')
    elif changed == 'settlement': target = case['account'] / 'ARTIFICIAL_BASE_SETTLEMENT.json'
    elif changed == 'day': target = next((case['account'] / 'ARTIFICIAL_BASE_DAYS').glob('*.json.gz'))
    elif changed == 'source': target = Path(case['recovery_spec']['source_replacements'][0]['path'])
    elif changed == 'archive': target = Path(case['recovery_spec']['source_replacements'][0]['archive_ref']['path'])
    elif changed == 'checkpoint': target = Path(case['job']['items']['ARTIFICIAL_BASE']['backend_options']['checkpoint_path'])
    elif changed == 'economic': target = Path(case['recovery_spec']['economic_source_refs'][0]['path'])
    elif changed == 'ci': target = Path(case['recovery_spec']['ci_ref']['path'])
    else: target = Path(case['extension'].scope_reference['path'])
    target.write_bytes(target.read_bytes() + b'\n')
    with pytest.raises((PermissionError, ValueError)):
        recovery.FailedAccountRecoveryV1.freeze(case['root'], value, approval_ref=approval)


def test_governance_only_summary_cannot_change_economic_inventory(recovery_case):
    case = recovery_case; spec = deepcopy(case['recovery_spec']); spec['economic_source_refs'].pop()
    with pytest.raises(PermissionError, match='ECONOMIC_INVENTORY_CHANGED'):
        recovery.FailedAccountRecoveryV1.summary(case['root'], **spec)
    spec = deepcopy(case['recovery_spec']); economic = Path(spec['economic_source_refs'][0]['path'])
    archive = case['root'] / 'reports' / 'forbidden_economic_source.py'; archive.write_bytes(economic.read_bytes())
    old = pin(economic)['sha256']; economic.write_bytes(b'forbidden changed economic source\n')
    spec['source_replacements'].append({'path': str(economic), 'old_sha256': old,
        'new_sha256': pin(economic)['sha256'], 'archive_ref': pin(archive)})
    with pytest.raises(PermissionError): recovery.FailedAccountRecoveryV1.summary(case['root'], **spec)


def test_checkpoint_may_append_after_install_but_old_277_days_cannot_change(recovery_case):
    case = recovery_case; context, _, _ = freeze_recovery(case)
    append_checkpoint_days(case, 278)
    assert recovery.FailedAccountRecoveryV1.read(case['root'], context.scope_reference).proof() == context.proof()
    with pytest.raises(PermissionError):
        recovery.FailedAccountRecoveryV1.read(case['root'], context.scope_reference, for_install=True)
    first = context.summary_value['accounts']['ARTIFICIAL_BASE']['checkpoint']['day_refs'][0]
    Path(first['path']).write_bytes(b'changed original economic day')
    with pytest.raises(PermissionError): recovery.FailedAccountRecoveryV1.read(case['root'], context.scope_reference)


@pytest.mark.parametrize('extension_case', [{'unmeasured_segments': [17, 27]}], indirect=True)
def test_previously_charged_unknown_segments_keep_their_missing_resources(recovery_case):
    case = recovery_case; absent = [case['account'] / ('ARTIFICIAL_BASE_SEGMENT_' + str(number).zfill(6)
        + '_RESOURCE.json') for number in (17, 27)]
    assert not any(path.exists() for path in absent)
    context, value, _ = freeze_recovery(case)
    assert value['accounts']['ARTIFICIAL_BASE']['charged_seconds'] == recovery.CANONICAL_SECONDS
    references = {ref['path'] for ref in value['accounts']['ARTIFICIAL_BASE']['segment_refs']}
    assert not references.intersection(str(path) for path in absent)
    assert not any(path.exists() for path in absent)
    assert recovery.FailedAccountRecoveryV1.read(case['root'], context.scope_reference).proof() == context.proof()


def test_old_manifest_read_requires_explicit_signed_source_inheritance(recovery_case):
    case = recovery_case; context, _, _ = freeze_recovery(case)
    automatically_inherited = extension.AccountPurposeTimeExtensionV1.read(case['root'], case['extension'].scope_reference)
    assert automatically_inherited.recovery.proof() == context.proof()
    inherited = extension.AccountPurposeTimeExtensionV1.read(case['root'], case['extension'].scope_reference, recovery=context)
    assert inherited.scope_reference == case['extension'].scope_reference
    with pytest.raises(PermissionError):
        extension.AccountPurposeTimeExtensionV1.read(case['root'], case['extension'].scope_reference, recovery={})


def test_expiry_and_revocation_only_block_new_dispatch(recovery_case, monkeypatch):
    case = recovery_case; context, _, _ = freeze_recovery(case)
    case['campaign'].install_failed_account_recovery(context.scope_reference)
    assert recovery.FailedAccountRecoveryV1.read(case['root'], context.scope_reference, for_dispatch=True).proof() == context.proof()
    future = datetime.now(timezone.utc) + timedelta(days=2)
    class Later(datetime):
        @classmethod
        def now(cls, tz=None): return future
    with monkeypatch.context() as clock_patch:
        clock_patch.setattr(recovery, 'datetime', Later)
        assert recovery.FailedAccountRecoveryV1.read(case['root'], context.scope_reference).proof() == context.proof()
        with pytest.raises(PermissionError, match='EXPIRED'):
            recovery.FailedAccountRecoveryV1.read(case['root'], context.scope_reference, for_dispatch=True)
    case['campaign'].revoke('artificial owner revoked new dispatch')
    assert recovery.FailedAccountRecoveryV1.read(case['root'], context.scope_reference).proof() == context.proof()
    with pytest.raises(PermissionError, match='REVOKED'):
        recovery.FailedAccountRecoveryV1.read(case['root'], context.scope_reference, for_dispatch=True)


def test_no_second_freeze_or_wrong_predecessor_can_create_retry(recovery_case):
    case = recovery_case; context, value, _ = freeze_recovery(case)
    changed = deepcopy(value); changed['expires_at'] = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    changed['summary_identity'] = stable_hash({key: item for key, item in changed.items() if key != 'summary_identity'})
    approval = case['approvals'].approve(changed, approver='artificial_owner', capability=case['token'])
    with pytest.raises(PermissionError, match='SLOT_CONFLICT'):
        recovery.FailedAccountRecoveryV1.freeze(case['root'], changed, approval_ref=approval)
    with pytest.raises(PermissionError, match='PREDECESSOR_REFERENCE_CONFLICT'):
        context.require_predecessor(context.scope_reference)


@pytest.mark.skipif(os.name != 'nt', reason='Windows授权根大小写身份')
def test_owner_proof_preserves_approved_root_spelling(recovery_case):
    case = recovery_case; context, value, _ = freeze_recovery(case)
    reopened = recovery.FailedAccountRecoveryV1.read(os.path.normcase(str(case['root'])), context.scope_reference)
    assert str(reopened.root) == value['root'] and reopened.proof() == context.proof()
