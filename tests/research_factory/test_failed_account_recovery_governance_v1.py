"""真实人工Owner生命周期上的父子治理；不读取真实价格、不启动worker。"""
from copy import deepcopy
import json
import math

import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.run_budget import effective_execution_status, execution_dispatch_identity
from test_account_purpose_time_extension_v1 import control, extension_case, pin, write
from test_failed_account_recovery_capability_v1 import freeze_recovery, recovery_case


NAME = 'ARTIFICIAL_BASE'


def installed(case):
    paths = [case['account'] / (NAME + suffix) for suffix in ('_START.json', '_SETTLEMENT.json')]
    paths += sorted(case['account'].glob(NAME + '_SEGMENT_*.json'))
    originals = {path: path.read_bytes() for path in paths}
    context, _, _ = freeze_recovery(case)
    case['campaign'].install_failed_account_recovery(context.scope_reference)
    case['recovery'] = context
    case['original_files'] = originals
    return context


def ready(case):
    context = installed(case)
    case['gov'].reconcile_segment_mirrors(NAME)
    # 原失败超限引起的父暂停独立解除；人工控制链追加 RESUME 不改历史 PAUSE。
    case['campaign'].resume('artificial explicit Owner-authorized recovery resume')
    control(case, 'RESUME')
    case['gov'].start_recovery(NAME)
    return context


def resource_and_charge(case, seconds, *, name=NAME, outcome='CONTINUE', dispatch=None):
    gov = case['gov']
    dispatch = dispatch or gov.start_segment(name)
    prefix = name + '_SEGMENT_' + str(dispatch['segment_number']).zfill(6)
    reference = write(case['account'] / (prefix + '_RESOURCE.json'), {
        'fixture_only': 'ARTIFICIAL_GOVERNANCE_RESOURCE_NOT_WORKER_MEASUREMENT',
        'dispatch_id': dispatch['dispatch_id'], 'dispatch_identity': dispatch['dispatch_identity'],
        'elapsed_wall_seconds': seconds, 'time_extension_proof': case['extension'].proof(),
        'failed_account_recovery_proof': case['recovery'].proof()})
    charge = gov.end_segment(name, dispatch['segment_number'], seconds=seconds,
        evidence_identity=reference['sha256'], outcome=outcome, dispatch_identity=dispatch['dispatch_identity'])
    return dispatch, charge, reference


def assert_originals(case):
    assert all(path.read_bytes() == raw for path, raw in case['original_files'].items())


def test_missing_recovery_mirror_requires_explicit_zero_dispatch_repair(recovery_case):
    case = recovery_case
    context = installed(case)
    before = case['campaign']._peek_execution_status()
    events_path = case['campaign'].budget_path.with_name('run_budget_events.jsonl')
    events = events_path.read_bytes()
    with pytest.raises(PermissionError, match='FAILED_RECOVERY_MIRROR_REQUIRED'):
        case['gov'].segment_status(NAME)
    state = case['gov'].segment_status_for_reconcile(NAME)
    assert state['failed_account_recovery_mirror_pending'] and state['next_segment_number'] == 51
    assert state['effective_execution_status'] == 'RUNNING'
    repaired = case['gov'].reconcile_segment_mirrors(NAME)
    assert not repaired['failed_account_recovery_mirror_pending']
    assert repaired['pending'] is None and len(repaired['segments']) == 50
    assert repaired['segments'][-1]['charge']['outcome'] == 'FAILED'
    assert not (case['account'] / (NAME + '_RECOVERY_START.json')).exists()
    assert not (case['account'] / (NAME + '_SEGMENT_000051_DISPATCH.json')).exists()
    expected = {'schema_version': 'strategy-account-failed-recovery-mirror-v1',
        'receipt_id': case['receipt']['receipt_id'], 'source_identity': stable_hash(case['receipt']['source']),
        'operation_ids': case['receipt']['source']['operation_ids'], 'proof': context.proof()}
    assert json.loads((case['account'] / 'ACCOUNT_FAILED_RECOVERY.json').read_text(encoding='utf-8')) == expected
    after = case['campaign']._peek_execution_status()
    assert events_path.read_bytes() == events
    assert after['trial_usage'] == before['trial_usage']
    assert (after['used'], after['reserved'], after['remaining']) == (before['used'], before['reserved'], before['remaining'])
    assert_originals(case)


def test_segment_51_has_same_owner_proof_and_independent_start_settlement(recovery_case):
    case = recovery_case
    context = ready(case)
    before = case['campaign']._peek_execution_status()
    start = case['gov'].start_recovery(NAME)
    assert start['original_start_sha256'] == pin(case['account'] / (NAME + '_START.json'))['sha256']
    assert start['original_settlement_sha256'] == pin(case['account'] / (NAME + '_SETTLEMENT.json'))['sha256']
    assert start['failed_account_recovery_proof'] == context.proof()
    assert case['gov'].active_execution(NAME)['execution_purpose'] == NAME
    dispatch, charge, _ = resource_and_charge(case, 0.2, outcome='COMPLETED')
    assert dispatch['segment_number'] == 51
    assert dispatch['failed_account_recovery_proof'] == charge['failed_account_recovery_proof'] == context.proof()
    assert dispatch['dispatch_identity'] == charge['dispatch_identity']
    parent = case['campaign']._peek_execution_status()['operations']['BASE']
    assert dispatch['dispatch_identity'] == execution_dispatch_identity(parent['segments'][-1])
    assert parent['status'] == 'FAILED' and effective_execution_status(parent) == 'COMPLETED'
    seconds = case['gov'].segment_status(NAME)['charged_seconds']
    settlement = case['gov'].settle_recovery(NAME, completed=True, seconds=seconds, result_hash='artificial_result')
    assert all(settlement[key] == value for key, value in start.items())
    assert settlement['wall_seconds'] == seconds and settlement['completed']
    assert case['gov'].settle_recovery(NAME, completed=True, seconds=seconds, result_hash='artificial_result') == settlement
    after = case['campaign']._peek_execution_status()
    assert after['used']['wall_seconds'] == 42341 and after['reserved']['wall_seconds'] == 72000
    assert after['trial_usage'] == before['trial_usage']
    assert after['operations']['BASE']['actual']['wall_seconds'] == 42340
    assert after['operations']['BASE']['failed_account_recovery']['actual_delta']['wall_seconds'] == 1
    assert_originals(case)
    with pytest.raises(PermissionError, match='RECOVERY_SETTLEMENT_REQUIRED'):
        case['gov'].settle(NAME, completed=True, seconds=seconds, result_hash='artificial_result')


def test_late_charge_cannot_bind_to_following_dispatch(recovery_case):
    case = recovery_case
    ready(case)
    first, first_charge, reference = resource_and_charge(case, 0.05)
    second = case['gov'].start_segment(NAME)
    assert case['gov'].end_segment(NAME, 51, seconds=0.05, evidence_identity=reference['sha256'],
        dispatch_identity=first['dispatch_identity']) == first_charge
    assert case['gov'].segment_status(NAME)['pending'] == second
    for identity in (None, first['dispatch_identity']):
        with pytest.raises(PermissionError, match='DISPATCH_IDENTITY_CONFLICT'):
            case['gov'].end_segment(NAME, 52, seconds=0.05, evidence_identity=reference['sha256'],
                dispatch_identity=identity)
    resource_and_charge(case, 0.05, dispatch=second, outcome='COMPLETED')
    seconds = case['gov'].segment_status(NAME)['charged_seconds']
    case['gov'].settle_recovery(NAME, completed=True, seconds=seconds, result_hash='artificial_subsecond_result')
    after = case['campaign']._peek_execution_status()
    assert after['used']['wall_seconds'] == 42340
    assert after['operations']['BASE']['failed_account_recovery']['actual_delta']['wall_seconds'] == 0
    assert_originals(case)


def test_parent_only_dispatch_requires_repair_and_cannot_implicitly_start(recovery_case):
    case = recovery_case
    ready(case)
    parent = case['campaign'].start_execution_segment('BASE', segment_number=51)
    with pytest.raises(PermissionError, match='MIRROR_REQUIRED'):
        case['gov'].segment_status(NAME)
    events_path = case['campaign'].budget_path.with_name('run_budget_events.jsonl')
    events = events_path.read_bytes()
    state = case['gov'].reconcile_segment_mirrors(NAME)
    assert events_path.read_bytes() == events
    assert state['pending']['dispatch_identity'] == parent['dispatch_identity']
    marker = json.loads((case['account'] / (NAME + '_SEGMENT_000051_MIRROR_REPAIR.json')).read_text(encoding='utf-8'))
    assert marker['parent_dispatch_identity'] == execution_dispatch_identity(parent)
    assert marker['dispatch_identity'] == parent['dispatch_identity']
    assert marker['failed_account_recovery_proof'] == case['recovery'].proof()
    assert marker['worker_absent']
    assert not (case['account'] / (NAME + '_SEGMENT_000051_WORKER.json')).exists()
    assert_originals(case)


def test_repair_aborts_if_parent_authority_changes_before_write(recovery_case):
    case = recovery_case
    installed(case)

    def revoke_before_mirror():
        case['campaign'].revoke('artificial authority changed during mirror repair')

    with pytest.raises(PermissionError, match='RECONCILE_AUTHORITY_CHANGED'):
        case['gov'].reconcile_segment_mirrors(NAME, _before_immutable=revoke_before_mirror)
    assert not (case['account'] / 'ACCOUNT_FAILED_RECOVERY.json').exists()
    assert not (case['account'] / (NAME + '_SEGMENT_000051_DISPATCH.json')).exists()
    assert_originals(case)


@pytest.mark.parametrize('changed', ['proof', 'identity'])
def test_resource_proof_and_parent_identity_are_required_before_billing(recovery_case, changed):
    case = recovery_case
    ready(case)
    dispatch = case['gov'].start_segment(NAME)
    resource = {'fixture_only': 'ARTIFICIAL_REJECTED_GOVERNANCE_RESOURCE',
        'dispatch_id': dispatch['dispatch_id'], 'dispatch_identity': dispatch['dispatch_identity'],
        'elapsed_wall_seconds': 0.2, 'time_extension_proof': case['extension'].proof(),
        'failed_account_recovery_proof': deepcopy(case['recovery'].proof())}
    if changed == 'proof':
        resource['failed_account_recovery_proof']['recovery_id'] = '0' * 64
    else:
        resource['dispatch_identity'] = '0' * 64
    reference = write(case['account'] / (NAME + '_SEGMENT_000051_RESOURCE.json'), resource)
    before = case['campaign']._peek_execution_status()['operations']['BASE']
    with pytest.raises(PermissionError, match='RESOURCE_CONFLICT'):
        case['gov'].end_segment(NAME, 51, seconds=0.2, evidence_identity=reference['sha256'],
            dispatch_identity=dispatch['dispatch_identity'])
    after = case['campaign']._peek_execution_status()['operations']['BASE']
    assert after['active_segment'] == before['active_segment']
    assert after['active_wall_seconds'] == before['active_wall_seconds']
    assert_originals(case)


def test_second_failure_settles_only_recovery_and_blocks_next_account(recovery_case):
    case = recovery_case
    ready(case)
    resource_and_charge(case, 0.2, outcome='FAILED')
    seconds = case['gov'].segment_status(NAME)['charged_seconds']
    settlement = case['gov'].settle_recovery(NAME, completed=False, seconds=seconds,
        result_hash=None, error='ARTIFICIAL_SECOND_FAILURE')
    assert not settlement['completed'] and settlement['error'] == 'ARTIFICIAL_SECOND_FAILURE'
    with pytest.raises(PermissionError):
        case['gov'].start_segment(NAME)
    with pytest.raises(PermissionError):
        case['gov'].start_recovery(NAME)
    with pytest.raises(PermissionError):
        case['gov'].start('ARTIFICIAL_STRESS')
    view = case['campaign']._peek_execution_status()
    assert view['operations']['BASE']['status'] == 'FAILED'
    assert view['operations']['BASE']['failed_account_recovery']['status'] == 'FAILED'
    assert view['used']['wall_seconds'] == math.ceil(seconds) and view['reserved']['wall_seconds'] == 72000
    assert_originals(case)


def test_revocation_after_dispatch_still_allows_already_incurred_costs_to_settle(recovery_case):
    case = recovery_case
    ready(case)
    dispatch = case['gov'].start_segment(NAME)
    case['campaign'].revoke('artificial revoke after dispatch')
    write(case['account'] / 'REVOKED.json', {'fixture_only': True, 'reason': 'artificial account revoke'})
    resource_and_charge(case, 0.2, dispatch=dispatch, outcome='FAILED')
    seconds = case['gov'].segment_status(NAME)['charged_seconds']
    case['gov'].settle_recovery(NAME, completed=False, seconds=seconds,
        result_hash=None, error='ARTIFICIAL_REVOKED_WORKER_STOP')
    view = case['campaign']._peek_execution_status()
    assert view['revoked'] and view['used']['wall_seconds'] == 42341
    assert view['reserved']['wall_seconds'] == 72000
    with pytest.raises(PermissionError):
        case['gov'].start_segment(NAME)
    assert_originals(case)


def test_unbound_failed_account_and_conflicting_mirror_are_rejected(recovery_case):
    case = recovery_case
    with pytest.raises(PermissionError):
        case['gov'].start_recovery(NAME)
    context = installed(case)
    mirror = case['account'] / 'ACCOUNT_FAILED_RECOVERY.json'
    bad = {'schema_version': 'strategy-account-failed-recovery-mirror-v1',
        'receipt_id': case['receipt']['receipt_id'], 'source_identity': stable_hash(case['receipt']['source']),
        'operation_ids': case['receipt']['source']['operation_ids'], 'proof': deepcopy(context.proof())}
    bad['proof']['recovery_id'] = '0' * 64
    write(mirror, bad)
    with pytest.raises(PermissionError, match='RECOVERY_MIRROR_CONFLICT'):
        case['gov'].segment_status(NAME)
    with pytest.raises(PermissionError, match='RECOVERY_MIRROR_CONFLICT'):
        case['gov'].reconcile_segment_mirrors(NAME)
    assert_originals(case)


@pytest.mark.parametrize('extension_case', [{'unmeasured_segments': [17, 27]}], indirect=True)
def test_repair_preserves_charged_unknown_original_segments(recovery_case):
    case = recovery_case
    installed(case)
    absent = [case['account'] / (NAME + '_SEGMENT_' + str(number).zfill(6) + '_RESOURCE.json') for number in (17, 27)]
    assert not any(path.exists() for path in absent)
    state = case['gov'].reconcile_segment_mirrors(NAME)
    assert not any(path.exists() for path in absent)
    assert state['charged_seconds'] == case['recovery'].summary_value['accounts'][NAME]['charged_seconds']
    assert state['segments'][16]['charge']['basis'] == state['segments'][26]['charge']['basis'] == 'UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND'
    assert_originals(case)


def test_stress_inherits_source_proof_without_new_quota_or_failure_recovery(recovery_case):
    case = recovery_case
    ready(case)
    with pytest.raises(PermissionError, match='OTHER_PURPOSE_NOT_SETTLED'):
        case['gov'].start('ARTIFICIAL_STRESS')
    resource_and_charge(case, 0.2, outcome='COMPLETED')
    seconds = case['gov'].segment_status(NAME)['charged_seconds']
    case['gov'].settle_recovery(NAME, completed=True, seconds=seconds, result_hash='artificial_base_result')
    before = case['campaign']._peek_execution_status()
    start = case['gov'].start('ARTIFICIAL_STRESS')
    assert start['failed_account_recovery_proof'] == case['recovery'].proof()
    dispatch, charge, _ = resource_and_charge(case, 0.2, name='ARTIFICIAL_STRESS', outcome='FAILED')
    assert dispatch['segment_number'] == 1 and charge['failed_account_recovery_proof'] == case['recovery'].proof()
    case['gov'].settle('ARTIFICIAL_STRESS', completed=False, seconds=0.2,
        result_hash=None, error='ARTIFICIAL_STRESS_FAILURE')
    after = case['campaign']._peek_execution_status()
    assert len(after['operations']) == len(before['operations'])
    assert after['used']['account_jobs'] == 2 and after['reserved']['wall_seconds'] == 0
    assert 'failed_account_recovery' not in after['operations']['STRESS']
    with pytest.raises(PermissionError):
        case['gov'].start_recovery('ARTIFICIAL_STRESS')
    with pytest.raises(PermissionError):
        case['gov'].start_segment('ARTIFICIAL_STRESS')
    assert_originals(case)
