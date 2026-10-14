"""人工后继治理闭包：历史版本、原经济状态及缺镜像恢复均不可跳过。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory.account_version_capability_bridge_v1 import AccountVersionCapabilityBridgeV1
from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
from chanlun_trader.research_factory.campaign_scope_v1 import validate_scope_policy
from chanlun_trader.research_factory import account_version_continuation_v1 as continuation_module
from chanlun_trader.research_factory import etf_account_governance_v1 as governance_module
from test_account_purpose_time_extension_v1 import extension_case, control, install as freeze, write


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


@pytest.fixture
def runtime_case(extension_case):
    case = extension_case
    previous_ref = case['spec']['predecessor_manifest_ref']
    previous = read(previous_ref['path'])['summary']
    previous_proof = {'manifest_ref': previous_ref, 'version_id': previous['version_id'],
        'original_job_sha256': previous['original']['job']['sha256'],
        'active_plan_ids': {name: plan['plan_id'] for name, plan in previous['active_plans'].items()},
        'purpose': 'ARTIFICIAL_BASE'}
    # 人工原 producer 在 freeze 前保留 V1 已有段的准确版本侧车。
    for number in range(7, 35):
        prefix = 'ARTIFICIAL_BASE_SEGMENT_' + str(number).zfill(6)
        dispatch = read(case['account'] / (prefix + '_DISPATCH.json'))
        write(case['account'] / (prefix + '_EXECUTION_VERSION.json'), {
            'schema_version': 'ACCOUNT_VERSION_SEGMENT_V1', 'dispatch_id': dispatch['dispatch_id'],
            'segment_number': number, 'account_version_continuation': previous_proof})
    context, summary, _ = freeze(case)
    case.update(extension=context, extension_summary=summary, previous=previous,
        previous_proof=previous_proof,
        original_files={path: path.read_bytes() for path in case['account'].glob('*_SEGMENT_*_*.json')},
        original_checkpoint=(case['account'] / 'ARTIFICIAL_BASE_STATE.json').read_bytes())
    return case


def context(case, *, for_reconcile=False):
    return AccountVersionContinuationV1.from_job(case['job'], for_reconcile=for_reconcile)


def installed_context(case):
    case['campaign'].install_account_purpose_time_extension(case['extension'].scope_reference)
    value = context(case, for_reconcile=True)
    value.reconcile_segment_mirrors()
    return context(case)


def dispatch_successor(case):
    value = installed_context(case)
    control(case, 'RESUME')
    dispatch = case['gov'].start_segment('ARTIFICIAL_BASE')
    value.record_dispatch('ARTIFICIAL_BASE', dispatch)
    return value, dispatch


def successor_resource(case, value, dispatch, *, seconds=12.):
    prefix = 'ARTIFICIAL_BASE_SEGMENT_' + str(dispatch['segment_number']).zfill(6)
    return write(case['account'] / (prefix + '_RESOURCE.json'), {
        'dispatch_id': dispatch['dispatch_id'], 'segment_number': dispatch['segment_number'],
        'elapsed_wall_seconds': seconds, 'returncode': 75, 'timed_out': False,
        'account_version_continuation': value.proof('ARTIFICIAL_BASE'),
        'time_extension_proof': dispatch['time_extension_proof'], 'artificial_measurement': True})


def test_approved_freeze_maps_exact_sources_but_does_not_activate_time_or_plan(runtime_case):
    case = runtime_case; before = case['campaign'].budget_path.with_name('run_budget_events.jsonl').read_bytes()
    value = context(case)
    assert value.time_extension is not None and not value.time_extension_installed
    assert value.active_plan('ARTIFICIAL_BASE') == case['previous']['active_plans']['ARTIFICIAL_BASE']
    assert value.proof('ARTIFICIAL_BASE') == case['previous_proof']
    assert case['campaign'].peek_status()['reserved']['wall_seconds'] == 57600
    assert validate_scope_policy(case['root'], case['base']) == case['base']['scope_policy']
    historical = AccountVersionCapabilityBridgeV1.read_historical(case['root'], case['base'],
        reference=case['previous']['capability_bridge_ref'], time_extension_ref=case['extension'].scope_reference)
    assert historical['reference'] == case['previous']['capability_bridge_ref']
    assert case['campaign'].budget_path.with_name('run_budget_events.jsonl').read_bytes() == before
    with pytest.raises(PermissionError, match='TIME_EXTENSION_NOT_INSTALLED'):
        value.validate(for_dispatch=True, name='ARTIFICIAL_BASE')


def test_parent_install_without_child_mirror_requires_dedicated_reconciliation(runtime_case):
    case = runtime_case
    case['campaign'].install_account_purpose_time_extension(case['extension'].scope_reference)
    before = case['campaign'].budget_path.with_name('run_budget_events.jsonl').read_bytes()
    value = context(case, for_reconcile=True)
    assert value.mirrors_pending()
    with pytest.raises(PermissionError, match='TIME_EXTENSION_MIRROR_REQUIRED'):
        value.active_plan('ARTIFICIAL_BASE')
    with pytest.raises(PermissionError, match='TIME_EXTENSION_MIRROR_REQUIRED'):
        context(case)
    value.reconcile_segment_mirrors()
    assert not value.mirrors_pending()
    assert value.governance.segment_status('ARTIFICIAL_BASE')['pending'] is None
    assert case['campaign'].budget_path.with_name('run_budget_events.jsonl').read_bytes() == before
    assert not (case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000035_DISPATCH.json').exists()


def test_successor_proof_preserves_all_historical_segments_and_economic_state(runtime_case):
    case = runtime_case; value, dispatch = dispatch_successor(case)
    assert dispatch['segment_number'] == 35 and dispatch['time_extension_proof'] == case['extension'].proof()
    assert value.segment_proof('ARTIFICIAL_BASE', 6) is None
    assert value.segment_proof('ARTIFICIAL_BASE', 7) == value.segment_proof('ARTIFICIAL_BASE', 34) == case['previous_proof']
    successor = value.proof('ARTIFICIAL_BASE')
    assert value.segment_proof('ARTIFICIAL_BASE', 35) == successor
    assert successor['account_purpose_time_extension'] == case['extension'].proof()
    assert successor['predecessor_active_plan_ids'] == case['previous_proof']['active_plan_ids']
    assert successor['active_plan_ids'] != successor['predecessor_active_plan_ids']
    for name, old in case['previous']['active_plans'].items():
        new = value.active_plan(name)
        assert new['backend'] == old['backend'] and new['strategy'] == old['strategy']
        assert new['runtime']['execution_profile']['total_seconds'] == 28800
        assert {key: item for key, item in new['runtime'].items() if key != 'source_hashes'} == {
            key: item for key, item in old['runtime'].items() if key != 'source_hashes'}
    assert all(path.read_bytes() == raw for path, raw in case['original_files'].items())
    assert (case['account'] / 'ARTIFICIAL_BASE_STATE.json').read_bytes() == case['original_checkpoint']


@pytest.mark.parametrize('suffix', ['_RESOURCE.json', '_WORKER.json', '_INPUT_ACCESS.json', '_STATUS.json'])
def test_new_measurement_or_worker_cannot_strip_or_substitute_successor_proof(runtime_case, suffix):
    case = runtime_case; value, dispatch = dispatch_successor(case)
    prefix = 'ARTIFICIAL_BASE_SEGMENT_000035'
    write(case['account'] / (prefix + suffix), {'dispatch_id': dispatch['dispatch_id'],
        'segment_number': 35, 'account_version_continuation': case['previous_proof'],
        'time_extension_proof': dispatch['time_extension_proof']})
    with pytest.raises(PermissionError, match='SEGMENT_SUCCESSOR_VERSION_CONFLICT'):
        value.validate_dispatch('ARTIFICIAL_BASE', 35)


def test_new_resource_and_charge_bind_same_compact_proof(runtime_case):
    case = runtime_case; value, dispatch = dispatch_successor(case)
    reference = successor_resource(case, value, dispatch)
    case['gov'].end_segment('ARTIFICIAL_BASE', 35, seconds=12., evidence_identity=reference['sha256'], outcome='CONTINUE')
    assert value.validate_dispatch('ARTIFICIAL_BASE', 35)['account_version_continuation'] == value.proof('ARTIFICIAL_BASE')
    state = case['gov'].segment_status('ARTIFICIAL_BASE')
    assert state['charged_seconds'] == 27212. and state['effective_total_seconds'] == 72000
    assert state['segments'][-1]['charge']['time_extension_proof'] == dispatch['time_extension_proof']


def test_context_repairs_parent_only_dispatch_above_original_remaining_without_redispatch(runtime_case, monkeypatch):
    case = runtime_case; value, first = dispatch_successor(case)
    resource = successor_resource(case, value, first, seconds=900.)
    case['gov'].end_segment('ARTIFICIAL_BASE', 35, seconds=900., evidence_identity=resource['sha256'], outcome='CONTINUE')
    # 原28800只余700；已Owner批准的后继仍允许下一段900。
    assert case['gov'].segment_status('ARTIFICIAL_BASE')['charged_seconds'] == 28100
    original = governance_module.immutable

    def crash_after_parent(path, payload):
        if path.name == 'ARTIFICIAL_BASE_SEGMENT_000036_DISPATCH.json':
            raise RuntimeError('ARTIFICIAL_PARENT_ONLY_SUCCESSOR_DISPATCH')
        return original(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(governance_module, 'immutable', crash_after_parent)
        with pytest.raises(RuntimeError, match='PARENT_ONLY_SUCCESSOR_DISPATCH'):
            case['gov'].start_segment('ARTIFICIAL_BASE')
    event_path = case['campaign'].budget_path.with_name('run_budget_events.jsonl')
    original_events = event_path.read_bytes()
    recovered = context(case, for_reconcile=True)
    assert recovered.mirrors_pending()
    recovered.reconcile_segment_mirrors()
    state = recovered.governance.segment_status('ARTIFICIAL_BASE')
    assert state['pending']['segment_number'] == 36 and state['pending']['upper_bound_seconds'] == 900
    assert state['charged_seconds'] == 28100 and event_path.read_bytes() == original_events
    assert recovered.validate_dispatch('ARTIFICIAL_BASE', 36)['account_version_continuation'] == recovered.proof('ARTIFICIAL_BASE')
    for suffix in ('_WORKER.json', '_INPUT_ACCESS.json', '_RESOURCE.json', '_CHARGE.json'):
        assert not (case['account'] / ('ARTIFICIAL_BASE_SEGMENT_000036' + suffix)).exists()


@pytest.mark.parametrize('target', ['governance_archive', 'old_owner_approval', 'bridge_binding', 'economic_source'])
def test_historical_bridge_never_bypasses_missing_or_changed_old_authority(runtime_case, target):
    case = runtime_case
    if target == 'governance_archive':
        Path(case['spec']['source_replacements'][0]['archive_ref']['path']).write_bytes(b'changed archive')
    elif target == 'old_owner_approval':
        path = case['approvals'].directory / (case['base']['scope_policy']['approval_ref']['approval_id'] + '.json')
        path.unlink()
    elif target == 'bridge_binding':
        bridge = case['previous']['capability_bridge_ref']
        summary = read(bridge['path'])['summary']
        AccountVersionCapabilityBridgeV1._binding_path(case['approvals'], case['base'],
            summary['live_capabilities_fingerprint']).unlink()
    else:
        Path(case['spec']['economic_source_refs'][0]['path']).write_bytes(b'changed economic source')
    with pytest.raises((PermissionError, ValueError, FileNotFoundError)):
        context(case)


def test_combined_session_pin_mapping_retains_original_archive_and_final_bytes(runtime_case):
    value = installed_context(runtime_case)
    rows = {row['path']: row for row in value.approved_source_replacements()}
    for row in runtime_case['spec']['source_replacements']:
        assert rows[row['path']]['new_sha256'] == row['new_sha256']
    for row in runtime_case['previous']['source_replacements']:
        assert rows[row['path']]['old_sha256'] == row['old_sha256']
        assert rows[row['path']]['archive_ref'] == row['archive_ref']
    runtime = {row['path']: row['sha256'] for row in value.approved_runtime_sources()}
    for row in runtime_case['spec']['runtime_source_refs']:
        assert runtime[row['path']] == row['sha256']


@pytest.mark.parametrize('method', ['mirrors_pending', 'reconcile_segment_mirrors'])
def test_aggregate_rejects_valid_parent_event_append_before_missing_version_write(runtime_case, monkeypatch, method):
    case = runtime_case; value = installed_context(case)
    if method == 'reconcile_segment_mirrors':
        control(case, 'RESUME')
        case['gov'].start_segment('ARTIFICIAL_BASE')  # 已派发但尚未写版本侧车，未启动 worker。
    before_operations = case['campaign'].peek_status()['operations']
    event_path = case['campaign'].budget_path.with_name('run_budget_events.jsonl')
    before_events = event_path.read_bytes()
    original = AccountVersionContinuationV1._validate_dispatch_from_state
    appended = False

    def append_during_enumeration(self, name, number, state):
        nonlocal appended
        result = original(self, name, number, state)
        if number == 7 and not appended:
            appended = True
            case['campaign'].pause('ARTIFICIAL_CONCURRENT_PARENT_EVENT')
        return result

    monkeypatch.setattr(AccountVersionContinuationV1, '_validate_dispatch_from_state', append_during_enumeration)
    with pytest.raises(PermissionError, match='AGGREGATE_AUTHORITY_CHANGED'):
        getattr(value, method)()
    assert appended and event_path.read_bytes() != before_events
    after = case['campaign'].peek_status()
    assert after['paused'] and after['operations'] == before_operations
    assert all(path.read_bytes() == raw for path, raw in case['original_files'].items())
    assert (case['account'] / 'ARTIFICIAL_BASE_STATE.json').read_bytes() == case['original_checkpoint']
    for suffix in ('_EXECUTION_VERSION.json', '_MIRROR_REPAIR.json', '_WORKER.json', '_INPUT_ACCESS.json', '_RESOURCE.json', '_CHARGE.json'):
        assert not (case['account'] / ('ARTIFICIAL_BASE_SEGMENT_000035' + suffix)).exists()


@pytest.mark.parametrize('method', ['mirrors_pending', 'reconcile_segment_mirrors'])
@pytest.mark.parametrize('target', ['owner', 'governance_source', 'later_sidecar'])
def test_aggregate_still_rejects_owner_source_or_later_sidecar_change(runtime_case, monkeypatch, method, target):
    case = runtime_case; value = installed_context(case)
    pending_path = case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000035_DISPATCH.json'
    pending_raw = None
    if method == 'reconcile_segment_mirrors':
        control(case, 'RESUME')
        case['gov'].start_segment('ARTIFICIAL_BASE')
        pending_raw = pending_path.read_bytes()  # 原派发已支付预留；尚无版本侧车或 worker。
    event_path = case['campaign'].budget_path.with_name('run_budget_events.jsonl')
    before_events = event_path.read_bytes()
    if target == 'owner':
        changed = case['approvals'].directory / (case['extension'].approval_ref['approval_id'] + '.json')
    elif target == 'governance_source':
        changed = Path(case['spec']['source_replacements'][0]['path'])
    else:
        changed = case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000034_EXECUTION_VERSION.json'
    before_changed = changed.read_bytes()
    original = AccountVersionContinuationV1._validate_dispatch_from_state
    altered = False

    def alter_during_enumeration(self, name, number, state):
        nonlocal altered
        result = original(self, name, number, state)
        if number == 7 and not altered:
            altered = True
            if target == 'owner':
                changed.unlink()
            elif target == 'governance_source':
                changed.write_bytes(b'artificial changed governance during enumeration\n')
            else:
                row = read(changed)
                row['account_version_continuation']['version_id'] = 'f' * 64
                changed.write_text(json.dumps(row), encoding='utf-8')
        return result

    monkeypatch.setattr(AccountVersionContinuationV1, '_validate_dispatch_from_state', alter_during_enumeration)
    try:
        with pytest.raises((PermissionError, ValueError, FileNotFoundError)) as rejection:
            getattr(value, method)()
        if target == 'owner':
            assert isinstance(rejection.value, PermissionError)
            assert str(rejection.value) == 'OWNER_APPROVAL_NOT_REGISTERED'
        assert altered and event_path.read_bytes() == before_events
        assert all(path.read_bytes() == raw for path, raw in case['original_files'].items() if path != changed)
        assert (case['account'] / 'ARTIFICIAL_BASE_STATE.json').read_bytes() == case['original_checkpoint']
        if pending_raw is None:
            assert not pending_path.exists()
        else:
            assert pending_path.read_bytes() == pending_raw
        for suffix in ('_EXECUTION_VERSION.json', '_MIRROR_REPAIR.json', '_WORKER.json', '_INPUT_ACCESS.json',
                '_RESOURCE.json', '_CHARGE.json'):
            assert not (case['account'] / ('ARTIFICIAL_BASE_SEGMENT_000035' + suffix)).exists()
    finally:
        changed.write_bytes(before_changed)  # 仅撤销本测试的人工破坏，产品不得改写已冻结原件。


def test_existing_repair_marker_rechecks_owner_before_missing_version_write(runtime_case, monkeypatch):
    case = runtime_case; value = installed_context(case)
    control(case, 'RESUME')
    case['gov'].start_segment('ARTIFICIAL_BASE')
    prefix = 'ARTIFICIAL_BASE_SEGMENT_000035'
    marker = case['account'] / (prefix + '_MIRROR_REPAIR.json')
    version = case['account'] / (prefix + '_EXECUTION_VERSION.json')
    dispatch = case['account'] / (prefix + '_DISPATCH.json')
    original_dispatch = dispatch.read_bytes()
    event_path = case['campaign'].budget_path.with_name('run_budget_events.jsonl')
    original_events = event_path.read_bytes()
    original_immutable = continuation_module.immutable
    interrupted = False

    def interrupt_before_version(path, payload):
        nonlocal interrupted
        if Path(path) == version:
            interrupted = True
            assert marker.exists()
            raise RuntimeError('ARTIFICIAL_VERSION_WRITE_INTERRUPTION')
        return original_immutable(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(continuation_module, 'immutable', interrupt_before_version)
        with pytest.raises(RuntimeError, match='^ARTIFICIAL_VERSION_WRITE_INTERRUPTION$'):
            value.reconcile_segment_mirrors()
    assert interrupted and marker.exists() and not version.exists()
    original_marker = marker.read_bytes()  # 由公共 reconcile 实际生成，未手工构造恢复证明。
    assert dispatch.read_bytes() == original_dispatch and event_path.read_bytes() == original_events
    assert all(path.read_bytes() == raw for path, raw in case['original_files'].items())
    assert (case['account'] / 'ARTIFICIAL_BASE_STATE.json').read_bytes() == case['original_checkpoint']
    approval = case['approvals'].directory / (case['extension'].approval_ref['approval_id'] + '.json')
    original_approval = approval.read_bytes()
    original_validation = AccountVersionContinuationV1._validate_dispatch_from_state
    revoked = False

    def revoke_after_historical_dispatch(self, name, number, state):
        nonlocal revoked
        result = original_validation(self, name, number, state)
        if number == 7 and not revoked:
            revoked = True
            approval.unlink()
        return result

    try:
        with monkeypatch.context() as patch:
            patch.setattr(AccountVersionContinuationV1, '_validate_dispatch_from_state', revoke_after_historical_dispatch)
            with pytest.raises(PermissionError, match='^OWNER_APPROVAL_NOT_REGISTERED$'):
                value.reconcile_segment_mirrors()
        assert revoked and marker.read_bytes() == original_marker
        assert dispatch.read_bytes() == original_dispatch and event_path.read_bytes() == original_events
        assert all(path.read_bytes() == raw for path, raw in case['original_files'].items())
        assert (case['account'] / 'ARTIFICIAL_BASE_STATE.json').read_bytes() == case['original_checkpoint']
        for suffix in ('_EXECUTION_VERSION.json', '_WORKER.json', '_INPUT_ACCESS.json', '_RESOURCE.json', '_CHARGE.json'):
            assert not (case['account'] / (prefix + suffix)).exists()
    finally:
        approval.write_bytes(original_approval)  # 仅撤销本测试的人工权限删除。


def test_public_dispatch_validation_rechecks_owner_on_each_call(runtime_case):
    case = runtime_case; value = installed_context(case)
    assert value.validate_dispatch('ARTIFICIAL_BASE', 34)['account_version_continuation'] == case['previous_proof']
    approval = case['approvals'].directory / (case['extension'].approval_ref['approval_id'] + '.json')
    before = approval.read_bytes()
    approval.unlink()
    try:
        with pytest.raises(PermissionError, match='OWNER_APPROVAL_NOT_REGISTERED'):
            value.validate_dispatch('ARTIFICIAL_BASE', 34)
    finally:
        approval.write_bytes(before)
