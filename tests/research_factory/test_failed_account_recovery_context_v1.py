"""第三层Owner继承、原50段版本和新51段；全为人工合法生命周期。"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory.account_purpose_time_extension_v1 import (
    AccountPurposeTimeExtensionV1, resolve_time_extension_manifest,
)
from chanlun_trader.research_factory.account_version_capability_bridge_v1 import AccountVersionCapabilityBridgeV1
from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
from chanlun_trader.research_factory.failed_account_recovery_v1 import FailedAccountRecoveryV1
from chanlun_trader.research_factory.run_budget import effective_execution_status
from test_account_purpose_time_extension_v1 import control, extension_case, pin
from test_failed_account_recovery_capability_v1 import recovery_case, freeze_recovery


@pytest.fixture
def installed_recovery_context(recovery_case):
    case = recovery_case; owner, summary, _ = freeze_recovery(case)
    original_controls = {path: path.read_bytes() for path in case['account'].glob('CONTROL_*.json')}
    case['campaign'].install_failed_account_recovery(owner.scope_reference)
    context = AccountVersionContinuationV1.from_job(case['job'], for_reconcile=True)
    assert context.recovery_installed and context.recovery_mirror_pending
    context = context.reconcile_segment_mirrors()
    assert not context.mirrors_pending()
    # 仅解开人工超限引起的campaign暂停；原账户CONTROL历史原样保留。
    case['campaign'].resume('ARTIFICIAL_OWNER_INSTALLED_RECOVERY_DISPATCH_AUTHORIZATION')
    context = AccountVersionContinuationV1.from_job(case['job'])
    assert all(path.read_bytes() == raw for path, raw in original_controls.items())
    assert context.recovery.proof() == owner.proof()
    case.update(recovery_owner=owner, recovery_summary=summary, recovery_context=context,
        original_controls=original_controls)
    return case


def test_three_layer_plans_source_pins_and_original_budget_identity(installed_recovery_context):
    case = installed_recovery_context; context = case['recovery_context']; owner = case['recovery_owner']
    context.validate(for_dispatch=True, name='ARTIFICIAL_BASE')
    for name in case['job']['plans']:
        assert context.active_plan(name) == owner.successor_plan(name)
        assert context.active_item(name) == owner.successor_plan(name)['runtime']
        assert context.proof(name)['failed_account_recovery'] == owner.proof()
        assert context.proof(name)['account_purpose_time_extension'] == case['extension'].proof()
    replacements = {row['path']: row for row in context.approved_source_replacements()}
    worker = str(case['root'] / 'scripts/run_strategy_account_v1.py')
    assert replacements[worker]['old_sha256'] == case['job']['source_hashes'][worker]
    assert replacements[worker]['new_sha256'] == pin(Path(worker))['sha256']
    runtime = {row['path']: row['sha256'] for row in context.approved_runtime_sources()}
    assert all(runtime[ref['path']] == ref['sha256'] for ref in owner.summary_value['runtime_sources'])
    assert owner.summary_value['economic_sources'] == case['extension'].summary_value['economic_sources']
    view = case['campaign']._peek_execution_status(); failed = view['operations']['BASE']
    assert failed['status'] == 'FAILED' and effective_execution_status(failed) == 'RUNNING'
    assert failed['actual']['wall_seconds'] == 42340 and failed['active_wall_seconds'] == 42339.896402100036
    assert 'BASE' in view['trial_usage']['completed_trial_ids']
    assert view['operations']['STRESS']['effective_upper_bounds']['wall_seconds'] == 72000


def test_original_50_segment_proofs_remain_exact_and_51_gets_new_layer(installed_recovery_context):
    case = installed_recovery_context; context = case['recovery_context']; name = 'ARTIFICIAL_BASE'
    state = case['gov'].segment_status(name)
    old_paths = sorted(case['account'].glob(name + '_SEGMENT_*_EXECUTION_VERSION.json'))
    before = {path: path.read_bytes() for path in old_paths}
    for number in range(1, 51):
        expected = context.segment_proof(name, number)
        context._validate_dispatch_from_state(name, number, state)
        if number <= 6: assert expected is None
        elif number <= 34: assert 'account_purpose_time_extension' not in expected
        else:
            assert expected['account_purpose_time_extension'] == case['extension'].proof()
            assert 'failed_account_recovery' not in expected
    case['gov'].start_recovery(name)
    dispatch = case['gov'].start_segment(name, segment_number=51)
    record = context.record_dispatch(name, dispatch)
    assert dispatch['segment_number'] == 51 and dispatch['charged_before'] == 42339.896402100036
    assert dispatch['failed_account_recovery_proof'] == case['recovery_owner'].proof()
    assert record['failed_account_recovery_proof'] == dispatch['failed_account_recovery_proof']
    assert record['dispatch_identity'] == dispatch['dispatch_identity']
    assert record['account_version_continuation']['failed_account_recovery'] == case['recovery_owner'].proof()
    assert context.validate_dispatch(name, 51) == record
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert not (case['account'] / (name + '_RECOVERY_SETTLEMENT.json')).exists()


def test_exact_historical_pause_can_be_consumed_but_new_pause_cannot(installed_recovery_context):
    case = installed_recovery_context; context = case['recovery_context']
    assert case['recovery_summary']['control_boundary']['paused'] is True
    assert case['recovery_summary']['control_boundary']['consumes_historical_pause'] is True
    assert context.execution_paused() is False
    context.validate(for_dispatch=True, name='ARTIFICIAL_BASE')
    control(case, 'PAUSE')
    assert context.execution_paused() is True
    with pytest.raises(PermissionError, match='PAUSED'):
        context.validate(for_dispatch=True, name='ARTIFICIAL_BASE')
    fake_control = {'head': case['recovery_summary']['control_boundary']['head'], 'paused': False, 'events': []}
    with pytest.raises(PermissionError, match='CONTROL_VIEW_CHANGED'):
        context.execution_paused(fake_control)


def test_stable_resume_control_boundary_does_not_require_fake_pause(recovery_case):
    case = recovery_case; control(case, 'RESUME')
    context, summary, _ = freeze_recovery(case)
    assert summary['control_boundary']['paused'] is False
    assert summary['control_boundary']['consumes_historical_pause'] is False
    assert FailedAccountRecoveryV1.read(case['root'], context.scope_reference, for_install=True).proof() == context.proof()


def test_old_readers_only_inherit_the_unique_fresh_signed_successor(installed_recovery_context):
    case = installed_recovery_context; owner = case['recovery_owner']; ext = case['extension']
    inherited = AccountPurposeTimeExtensionV1.read(case['root'], ext.scope_reference)
    assert inherited.recovery.proof() == owner.proof()
    resolved = resolve_time_extension_manifest(ext.scope_reference, root=case['root'],
        authorization_id=case['base']['authorization_id'], objective_id=case['base']['objective_id'])
    assert resolved['proof'] == ext.proof() and resolved['aggregate_reserved_delta'] == 86400
    bridge_ref = ext.summary_value['predecessor_bridge_ref']
    bridge = AccountVersionCapabilityBridgeV1.read_historical(case['root'], case['base'], reference=bridge_ref,
        time_extension_ref=ext.scope_reference, recovery=owner)
    assert bridge['reference'] == bridge_ref
    fake = FailedAccountRecoveryV1(case['root'], {'path': str(case['root'] / 'reports' / 'fake_manifest.json'), 'sha256': '0' * 64})
    fake.summary_value = deepcopy(owner.summary_value)
    for invalid in ({'historical': True}, fake):
        with pytest.raises((PermissionError, ValueError)):
            AccountPurposeTimeExtensionV1.read(case['root'], ext.scope_reference, recovery=invalid)
        with pytest.raises((PermissionError, ValueError)):
            AccountVersionCapabilityBridgeV1.read(case['root'], case['base'], reference=bridge_ref, recovery=invalid)


def test_missing_recovery_mirror_only_repairs_and_never_dispatches(recovery_case, monkeypatch):
    case = recovery_case; owner, _, _ = freeze_recovery(case)
    case['campaign'].install_failed_account_recovery(owner.scope_reference)
    with pytest.raises(PermissionError): AccountVersionContinuationV1.from_job(case['job'])
    context = AccountVersionContinuationV1.from_job(case['job'], for_reconcile=True)
    assert context.recovery_mirror_pending and context.mirrors_pending()
    from scripts import run_strategy_account_v1 as runner
    from chanlun_trader.research_factory.universe_execution_recovery_v1 import resume_long_horizon_job
    def forbidden(*args, **kwargs): raise AssertionError('mirror repair started account execution')
    monkeypatch.setattr(runner, 'execute_accounts', forbidden)
    result = resume_long_horizon_job(case['account'] / 'JOB.json', case['job'])
    assert result['status'] == 'WAITING_RECONCILIATION_REVIEW_NO_REDISPATCH' and result['dispatched_segments'] == 0
    assert not (case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000051_DISPATCH.json').exists()
    assert not (case['account'] / 'ARTIFICIAL_BASE_RECOVERY_START.json').exists()
    assert not AccountVersionContinuationV1.from_job(case['job']).mirrors_pending()


def test_new_dispatch_sidecar_must_match_both_new_proof_and_identity(installed_recovery_context):
    case = installed_recovery_context; context = case['recovery_context']; name = 'ARTIFICIAL_BASE'
    case['gov'].start_recovery(name); dispatch = case['gov'].start_segment(name, segment_number=51)
    context.record_dispatch(name, dispatch)
    path = context._dispatch_path(name, 51); value = json.loads(path.read_text(encoding='utf-8'))
    value['dispatch_identity'] = '0' * 64; path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(PermissionError, match='DISPATCH_VERSION_CONFLICT'):
        context.validate_dispatch(name, 51)


@pytest.fixture(scope='module')
def fresh_reader_context(_research_short_tmp_root):
    """只读反例共享一次合法277日生命周期；每次公开调用仍完整 fresh。"""
    with pytest.MonkeyPatch.context() as patch:
        case = extension_case.__wrapped__(Path(tempfile.mkdtemp(prefix='ctx_', dir=_research_short_tmp_root)),
            patch, SimpleNamespace(param={}))
        case = recovery_case.__wrapped__(case)
        yield installed_recovery_context.__wrapped__(case)


@pytest.mark.parametrize(('reader', 'expected_reads'), (
    ('extension_provided', 1), ('extension_discovered', 1),
    ('bridge_provided', 2), ('bridge_discovered', 2), ('context', 11),
))
def test_public_readers_keep_fresh_proofs_without_nested_full_reads(
        fresh_reader_context, monkeypatch, reader, expected_reads):
    case = fresh_reader_context; owner = case['recovery_owner']; ext = case['extension']
    original = FailedAccountRecoveryV1.read.__func__; calls = []

    def counted(cls, *args, **kwargs):
        calls.append((args, kwargs))
        return original(cls, *args, **kwargs)

    monkeypatch.setattr(FailedAccountRecoveryV1, 'read', classmethod(counted))
    if reader.startswith('extension'):
        result = AccountPurposeTimeExtensionV1.read(case['root'], ext.scope_reference,
            recovery=owner if reader.endswith('provided') else None)
        assert result.recovery.proof() == owner.proof()
        assert result.recovery.summary_value == owner.summary_value
    elif reader.startswith('bridge'):
        reference = ext.summary_value['predecessor_bridge_ref']
        result = AccountVersionCapabilityBridgeV1.read_historical(case['root'], case['base'],
            reference=reference, time_extension_ref=ext.scope_reference,
            recovery=owner if reader.endswith('provided') else None)
        assert result['reference'] == reference
        assert result['capabilities_snapshot'] == owner.summary_value['capabilities_snapshot']
    else:
        result = AccountVersionContinuationV1.from_job(case['job'])
        assert result.recovery.proof() == owner.proof() and result.recovery_installed
    assert len(calls) == expected_reads


@pytest.mark.parametrize('provided', (True, False))
def test_recovery_owner_is_checked_before_outer_original_references(fresh_reader_context, monkeypatch, provided):
    from chanlun_trader.research_factory import account_purpose_time_extension_v1 as extension
    case = fresh_reader_context; owner = case['recovery_owner']; ext = case['extension']
    approval = Path(owner.summary_value['approval_store']) / (owner.approval_ref['approval_id'] + '.json')
    original = extension._refs; later = []

    def watched(references, root):
        if ext.summary_value['proposal_ref'] in references and ext.summary_value['ci_ref'] in references:
            later.append(True)
            raise AssertionError('missing recovery Owner reached outer original references')
        return original(references, root)

    raw = approval.read_bytes(); approval.unlink()
    monkeypatch.setattr(extension, '_refs', watched)
    try:
        with pytest.raises(PermissionError, match='OWNER_APPROVAL'):
            AccountPurposeTimeExtensionV1.read(case['root'], ext.scope_reference, recovery=owner if provided else None)
        assert not later
    finally:
        approval.write_bytes(raw)


@pytest.mark.parametrize('mutation', ('owner', 'source', 'charge'))
@pytest.mark.parametrize('provided', (True, False))
def test_extension_final_fresh_check_rejects_changes_after_owner_bootstrap(
        fresh_reader_context, monkeypatch, mutation, provided):
    from chanlun_trader.research_factory import account_purpose_time_extension_v1 as extension
    case = fresh_reader_context; owner = case['recovery_owner']; ext = case['extension']
    target = (Path(owner.summary_value['approval_store']) / (owner.approval_ref['approval_id'] + '.json')
        if mutation == 'owner' else case['root'] / 'scripts/run_strategy_account_v1.py'
        if mutation == 'source' else case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000050_CHARGE.json')
    before = {path: path.read_bytes() for path in case['account'].rglob('*') if path.is_file()}
    raw = target.read_bytes(); fired = []; original = extension._refs

    def changed_after_refs(references, root):
        value = original(references, root)
        if not fired and ext.summary_value['proposal_ref'] in references and ext.summary_value['ci_ref'] in references:
            fired.append(True)
            target.unlink() if mutation == 'owner' else target.write_bytes(raw + b'\n')
        return value

    monkeypatch.setattr(extension, '_refs', changed_after_refs)
    try:
        with pytest.raises((PermissionError, ValueError)):
            AccountPurposeTimeExtensionV1.read(case['root'], ext.scope_reference, recovery=owner if provided else None)
        assert fired
    finally:
        target.write_bytes(raw)
    assert {path: path.read_bytes() for path in case['account'].rglob('*') if path.is_file()} == before
    assert not (case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000051_DISPATCH.json').exists()


@pytest.mark.parametrize('mutation', ('owner', 'source'))
def test_bridge_final_fresh_check_covers_its_pinned_record_read(
        fresh_reader_context, monkeypatch, mutation):
    from chanlun_trader.research_factory import account_purpose_time_extension_v1 as extension
    from chanlun_trader.research_factory import account_version_capability_bridge_v1 as bridge
    from chanlun_trader.research_factory import failed_account_recovery_v1 as recovery
    case = fresh_reader_context; owner = case['recovery_owner']; ext = case['extension']
    reference = ext.summary_value['predecessor_bridge_ref']
    pinned = {key: reference[key] for key in ('path', 'sha256')}
    target = (Path(owner.summary_value['approval_store']) / (owner.approval_ref['approval_id'] + '.json')
        if mutation == 'owner' else case['root'] / 'scripts/run_strategy_account_v1.py')
    raw = target.read_bytes(); armed = []; fired = []
    original_extension = AccountPurposeTimeExtensionV1.read.__func__

    def after_extension(cls, *args, **kwargs):
        value = original_extension(cls, *args, **kwargs)
        armed.append(True)
        return value

    def watch(original):
        def read_then_change(value, root):
            result = original(value, root)
            if armed and not fired and value == pinned:
                fired.append(True)
                target.unlink() if mutation == 'owner' else target.write_bytes(raw + b'\n')
            return result
        return read_then_change

    monkeypatch.setattr(AccountPurposeTimeExtensionV1, 'read', classmethod(after_extension))
    monkeypatch.setattr(bridge, '_read', watch(bridge._read))
    monkeypatch.setattr(recovery, '_read', watch(recovery._read))
    try:
        with pytest.raises((PermissionError, ValueError)):
            AccountVersionCapabilityBridgeV1.read_historical(case['root'], case['base'],
                reference=reference, time_extension_ref=ext.scope_reference, recovery=owner)
        assert fired
    finally:
        target.write_bytes(raw)
    assert not (case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000051_DISPATCH.json').exists()
