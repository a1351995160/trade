"""人工 Owner 许可下两原账户的原子预留、历史计费及断电镜像边界。"""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from chanlun_trader.research_factory.budget import BudgetLedgerMismatchError
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory import etf_account_governance_v1 as governance_module
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.run_budget import AutonomousRunBudgetV2, effective_segment_allowance
from test_account_purpose_time_extension_v1 import extension_case, control, install as freeze, write


@pytest.fixture
def installed_case(extension_case):
    context, summary, _ = freeze(extension_case)
    extension_case['context'] = context
    extension_case['summary'] = summary
    extension_case['campaign'].install_account_purpose_time_extension(context.scope_reference)
    return extension_case


def events(case):
    return case['campaign'].budget_path.with_name('run_budget_events.jsonl')


def rows(case):
    return [json.loads(line) for line in events(case).read_text(encoding='utf-8').splitlines()]


def new_resource(case, dispatch, seconds):
    path = case['account'] / ('ARTIFICIAL_BASE_SEGMENT_' + str(dispatch['segment_number']).zfill(6) + '_RESOURCE.json')
    return write(path, {'dispatch_id': dispatch['dispatch_id'], 'elapsed_wall_seconds': seconds,
        'time_extension_proof': deepcopy(dispatch['time_extension_proof']), 'artificial_measurement': True})


def test_two_original_reservations_change_in_one_owner_proven_event(extension_case):
    case = extension_case; campaign = case['campaign']
    context, summary, _ = freeze(case)
    before = campaign.peek_status(); before_rows = rows(case)
    originals = {path: path.read_bytes() for path in case['account'].glob('*_SEGMENT_*_*.json')}
    checkpoint = case['account'] / 'ARTIFICIAL_BASE_STATE.json'; checkpoint_raw = checkpoint.read_bytes()
    installed = campaign.install_account_purpose_time_extension(context.scope_reference)
    after = campaign.peek_status(); appended = rows(case)[len(before_rows):]
    assert len(appended) == 1 and appended[0]['event_type'] == 'CAMPAIGN_ACCOUNT_PURPOSE_TIME_EXTENDED'
    assert appended[0]['payload']['proof'] == context.proof()
    assert set(installed) == {'BASE', 'STRESS'}
    for operation_id in installed:
        old, new = before['operations'][operation_id], after['operations'][operation_id]
        assert new['upper_bounds'] == old['upper_bounds']
        assert new['execution_profile'] == old['execution_profile']
        assert new.get('segments', []) == old.get('segments', [])
        assert new['effective_total_seconds'] == new['effective_upper_bounds']['wall_seconds'] == 72000
        assert new['account_purpose_time_extension'] == context.proof()
    assert after['operations']['BASE']['active_wall_seconds'] == summary['accounts']['ARTIFICIAL_BASE']['charged_seconds']
    assert after['reserved']['wall_seconds'] == before['reserved']['wall_seconds'] + 86400
    assert after['remaining']['wall_seconds'] == before['remaining']['wall_seconds'] - 86400
    assert after['stage_remaining']['EXPLORATION']['wall_seconds'] == before['stage_remaining']['EXPLORATION']['wall_seconds'] - 86400
    assert after['used'] == before['used'] and after['trial_usage'] == before['trial_usage']
    assert after['grants'] == before['grants'] and after['authorization'] == before['authorization']
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert checkpoint.read_bytes() == checkpoint_raw
    event_raw = events(case).read_bytes()
    campaign.install_account_purpose_time_extension(context.scope_reference)
    assert events(case).read_bytes() == event_raw


def test_unapproved_or_constructed_event_cannot_reserve_time(extension_case):
    case = extension_case; before = events(case).read_bytes()
    payload = {'proof': {'manifest_ref': {'path': str(case['root'] / 'fake.json'), 'sha256': 'a' * 64},
        'extension_id': 'b' * 64}, 'installed_at': datetime.now(timezone.utc).isoformat()}
    with pytest.raises((PermissionError, BudgetLedgerMismatchError, ValueError)):
        case['campaign']._budget().campaign_event('CAMPAIGN_ACCOUNT_PURPOSE_TIME_EXTENDED', payload)
    assert events(case).read_bytes() == before
    context, _, _ = freeze(case)
    payload['proof'] = context.proof(); payload['effective_total_seconds'] = 100000
    with pytest.raises(BudgetLedgerMismatchError):
        case['campaign']._budget().campaign_event('CAMPAIGN_ACCOUNT_PURPOSE_TIME_EXTENDED', payload)
    assert events(case).read_bytes() == before


def test_committed_extension_replays_both_accounts_after_snapshot_crash(extension_case, monkeypatch):
    case = extension_case; context, _, _ = freeze(case)
    original = AutonomousRunBudgetV2._persist
    def crash(self):
        if self._events and self._events[-1]['event_type'] == 'CAMPAIGN_ACCOUNT_PURPOSE_TIME_EXTENDED':
            raise RuntimeError('ARTIFICIAL_CRASH_AFTER_ATOMIC_EXTENSION')
        return original(self)
    with monkeypatch.context() as patch:
        patch.setattr(AutonomousRunBudgetV2, '_persist', crash)
        with pytest.raises(RuntimeError, match='AFTER_ATOMIC_EXTENSION'):
            case['campaign'].install_account_purpose_time_extension(context.scope_reference)
    restored = ResearchCampaignV1(case['root'], case['base']['authorization_id']).peek_status()
    assert restored['reserved']['wall_seconds'] == 144000
    assert all(restored['operations'][name]['effective_total_seconds'] == 72000 for name in ('BASE', 'STRESS'))
    assert restored['operations']['BASE']['active_wall_seconds'] == 27200
    case['campaign'].budget_path.unlink()
    assert case['campaign'].peek_status()['reserved'] == restored['reserved']


def test_local_extension_mirror_requires_dedicated_reconcile(installed_case):
    case = installed_case; gov = case['gov']; before = events(case).read_bytes()
    with pytest.raises(PermissionError, match='MIRROR_REQUIRED'):
        gov.segment_status('ARTIFICIAL_BASE')
    with pytest.raises(PermissionError, match='MIRROR_REQUIRED'):
        gov.start_segment('ARTIFICIAL_BASE')
    pending = gov.segment_status_for_reconcile('ARTIFICIAL_BASE')
    assert pending['time_extension_mirror_pending'] and pending['pending'] is None
    repaired = gov.reconcile_segment_mirrors('ARTIFICIAL_BASE')
    assert not repaired['time_extension_mirror_pending'] and repaired['next_segment_number'] == 35
    assert repaired['charged_seconds'] == 27200 and repaired['remaining_seconds'] == 44800
    assert events(case).read_bytes() == before
    assert not (case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000035_DISPATCH.json').exists()


def test_new_resource_and_charge_bind_same_proof_and_settle_above_old_limit(installed_case):
    case = installed_case; gov = case['gov']; gov.reconcile_segment_mirrors('ARTIFICIAL_BASE')
    old_rows = deepcopy(gov.segment_status('ARTIFICIAL_BASE')['segments'])
    for number in range(35, 42):
        dispatch = gov.start_segment('ARTIFICIAL_BASE')
        assert dispatch['segment_number'] == number and dispatch['upper_bound_seconds'] == 900
        assert dispatch['time_extension_proof'] == case['context'].proof()
        if number == 35:
            before = events(case).read_bytes()
            with pytest.raises((ValueError, PermissionError)):
                gov.end_segment('ARTIFICIAL_BASE', number, seconds=900, evidence_identity='unbound_measurement')
            assert events(case).read_bytes() == before
        evidence = new_resource(case, dispatch, 900)
        charge = gov.end_segment('ARTIFICIAL_BASE', number, seconds=900, evidence_identity=evidence['sha256'],
            outcome='COMPLETED' if number == 41 else 'CONTINUE')
        assert charge['time_extension_proof'] == dispatch['time_extension_proof']
    state = gov.segment_status('ARTIFICIAL_BASE')
    assert state['segments'][:34] == old_rows
    assert all('time_extension_proof' not in row['dispatch'] and 'time_extension_proof' not in row['charge'] for row in old_rows)
    assert state['profile']['total_seconds'] == 28800 and state['charged_seconds'] == 33500
    settlement = gov.settle('ARTIFICIAL_BASE', completed=True, seconds=33500, result_hash='artificial_final_account')
    assert settlement['time_extension_proof'] == case['context'].proof()
    view = case['campaign'].peek_status()
    assert view['operations']['BASE']['upper_bounds']['wall_seconds'] == 28800
    assert view['operations']['BASE']['actual']['wall_seconds'] == 33500
    assert view['used']['wall_seconds'] == 33500 and view['reserved']['wall_seconds'] == 72000
    assert view['used']['account_jobs'] == view['reserved']['account_jobs'] == 1


@pytest.mark.parametrize('suffix', ['_DISPATCH.json', '_CHARGE.json'])
def test_parent_commit_repairs_exact_new_proof_mirror_without_recharge(installed_case, monkeypatch, suffix):
    case = installed_case; gov = case['gov']; gov.reconcile_segment_mirrors('ARTIFICIAL_BASE')
    original = governance_module.immutable
    def crash(path, value):
        if path.name.endswith(suffix):raise RuntimeError('ARTIFICIAL_NEW_PROOF_MIRROR_CRASH')
        return original(path, value)
    if suffix == '_DISPATCH.json':
        with monkeypatch.context() as patch:
            patch.setattr(governance_module, 'immutable', crash)
            with pytest.raises(RuntimeError, match='MIRROR_CRASH'):gov.start_segment('ARTIFICIAL_BASE')
        before = events(case).read_bytes(); state = gov.reconcile_segment_mirrors('ARTIFICIAL_BASE')
        dispatch = state['pending']; assert dispatch['time_extension_proof'] == case['context'].proof()
        assert state['charged_seconds'] == 27200 and events(case).read_bytes() == before
        gov.end_segment('ARTIFICIAL_BASE', 35)  # 无测量只能扣原派发900上界。
        charge = gov.segment_status('ARTIFICIAL_BASE')['segments'][-1]['charge']
        assert charge['seconds'] == 900 and charge['measured_seconds'] is None
        assert charge['basis'] == 'UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND'
    else:
        dispatch = gov.start_segment('ARTIFICIAL_BASE'); evidence = new_resource(case, dispatch, 850)
        with monkeypatch.context() as patch:
            patch.setattr(governance_module, 'immutable', crash)
            with pytest.raises(RuntimeError, match='MIRROR_CRASH'):
                gov.end_segment('ARTIFICIAL_BASE', 35, seconds=850, evidence_identity=evidence['sha256'])
        before = events(case).read_bytes(); state = gov.reconcile_segment_mirrors('ARTIFICIAL_BASE')
        assert state['pending'] is None and state['charged_seconds'] == 28050
        assert state['segments'][-1]['charge']['time_extension_proof'] == case['context'].proof()
        assert events(case).read_bytes() == before


def test_known_unusable_extended_segment_is_rejected_before_dispatch(installed_case):
    case = installed_case; before = events(case).read_bytes()
    with pytest.raises(PermissionError, match='BOUND_CONFLICT'):
        case['campaign'].start_execution_segment('BASE', segment_number=35, upper_bound_seconds=70)
    assert events(case).read_bytes() == before
    profile = case['job']['resources']; original = deepcopy(profile); proof = case['context'].proof()
    assert effective_segment_allowance(profile, 28741.393096800028, seconds_to_expiry=99999,
        effective_total_seconds=72000, time_extension_proof=proof) == 900
    assert effective_segment_allowance(profile, 28741.393096800028, seconds_to_expiry=99999) == pytest.approx(58.606903199972294)
    assert effective_segment_allowance(profile, 71900, seconds_to_expiry=99999,
        effective_total_seconds=72000, time_extension_proof=proof) == 100
    with pytest.raises(PermissionError, match='COOPERATIVE_WINDOW_EXHAUSTED'):
        effective_segment_allowance(profile, 71930, seconds_to_expiry=99999,
            effective_total_seconds=72000, time_extension_proof=proof)
    with pytest.raises(PermissionError, match='RESOURCE_EXHAUSTED'):
        effective_segment_allowance(profile, 72000, seconds_to_expiry=99999,
            effective_total_seconds=72000, time_extension_proof=proof)
    assert profile == original


@pytest.mark.parametrize('extension_case', [{'historical_failed_other_candidate': True}], indirect=True)
def test_historical_failed_other_candidate_does_not_block_target_pair(extension_case):
    case = extension_case; campaign = case['campaign']
    # 无关旧候选已由夹具在前驱 B 仍生效时，经真实公共预算入口结 FAILED。
    failed = [row['payload'] for row in rows(case) if row['event_type'] == 'CAMPAIGN_OPERATION_SETTLED'
        and row['payload'].get('operation_id') == 'old_failed']
    assert len(failed) == 1 and failed[0]['outcome'] == 'FAILED'
    context, _, _ = freeze(case)
    campaign.install_account_purpose_time_extension(context.scope_reference)
    assert campaign.peek_status()['operations']['old_failed']['status'] == 'FAILED'
    assert campaign.peek_status()['operations']['BASE']['effective_total_seconds'] == 72000


@pytest.mark.parametrize('extension_case', [
    {'resource_wall': 140000},
    {'resource_wall': 200000, 'stage_wall': 140000},
], indirect=['extension_case'])
def test_global_or_exploration_shortfall_never_commits_half_pair(extension_case):
    case = extension_case
    before = events(case).read_bytes()
    with pytest.raises(PermissionError, match='RESERVATION_CAPACITY_INSUFFICIENT'):
        freeze(case)
    assert events(case).read_bytes() == before
    # 拒绝后 C 源码尚无许可，不能调用当前公开 scope；权威原事件须逐字节不变。
    reserved = {row['payload']['operation_id']: row['payload'] for row in rows(case)
        if row['event_type'] == 'CAMPAIGN_OPERATION_RESERVED'}
    assert set(reserved) == {'BASE', 'STRESS'}
    assert sum(row['upper_bounds']['wall_seconds'] for row in reserved.values()) == 57600
    assert all(row['upper_bounds']['wall_seconds'] == row['execution_profile']['total_seconds'] == 28800
        and 'effective_total_seconds' not in row for row in reserved.values())
    assert not any(row['event_type'] == 'CAMPAIGN_ACCOUNT_PURPOSE_TIME_EXTENDED' for row in rows(case))


@pytest.mark.parametrize('event', ['unknown', 'active'])
def test_changed_target_boundary_blocks_install_without_new_reservation(extension_case, event):
    case = extension_case; context, _, _ = freeze(case)
    if event == 'unknown':case['campaign'].mark_unknown('BASE', 'artificial_unknown_target')
    else:case['campaign'].start_execution_segment('BASE', segment_number=35)
    before = events(case).read_bytes()
    with pytest.raises((PermissionError, BudgetLedgerMismatchError, ValueError)):
        case['campaign'].install_account_purpose_time_extension(context.scope_reference)
    assert events(case).read_bytes() == before
    assert 'effective_total_seconds' not in case['campaign'].peek_status()['operations']['BASE']


@pytest.mark.parametrize('change,missing', [
    ('owner', 'dispatch'), ('authority', 'dispatch'),
    ('owner', 'charge'), ('authority', 'charge'),
    ('owner', 'quota'), ('authority', 'quota'),
], ids=['owner-dispatch', 'authority-dispatch', 'owner-charge', 'authority-charge',
        'owner-quota', 'authority-quota'])
def test_reconcile_rechecks_owner_and_authority_before_first_new_mirror(installed_case, monkeypatch,
        change, missing):
    """父端已提交、子端尚缺时，中途撤权或追加父事件必须在首次补写前拒绝。"""
    case = installed_case; gov = case['gov']; name = 'ARTIFICIAL_BASE'
    original_immutable = governance_module.immutable
    if missing != 'quota':
        gov.reconcile_segment_mirrors(name)
        control(case, 'RESUME')
        dispatch = gov.start_segment(name)
        if missing == 'dispatch':
            gov.end_segment(name, 35)  # 人工无测量段按原900秒上界结清，不伪造RESOURCE。
            assert gov.segment_status(name)['charged_seconds'] == 28100
            suffix = 'ARTIFICIAL_BASE_SEGMENT_000036_DISPATCH.json'

            def commit_parent_only():
                gov.start_segment(name)
        else:
            evidence = new_resource(case, dispatch, 12.)
            suffix = 'ARTIFICIAL_BASE_SEGMENT_000035_CHARGE.json'

            def commit_parent_only():
                gov.end_segment(name, 35, seconds=12., evidence_identity=evidence['sha256'])

        def crash_before_local_mirror(path, payload):
            if path.name == suffix:
                raise RuntimeError('ARTIFICIAL_PARENT_ONLY_PREWRITE_CRASH')
            return original_immutable(path, payload)

        with monkeypatch.context() as patch:
            patch.setattr(governance_module, 'immutable', crash_before_local_mirror)
            with pytest.raises(RuntimeError, match='PARENT_ONLY_PREWRITE_CRASH'):
                commit_parent_only()
        missing_paths = [case['account'] / suffix]
        if missing == 'dispatch':
            missing_paths.append(case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000036_MIRROR_REPAIR.json')
    else:
        missing_paths = [case['account'] / 'ACCOUNT_PURPOSE_TIME_EXTENSION.json']
    assert all(not path.exists() for path in missing_paths)
    original_files = {path: path.read_bytes() for path in case['account'].glob('*.json')}
    original_events = events(case).read_bytes()
    approval = case['approvals'].directory / (case['context'].approval_ref['approval_id'] + '.json')
    approval_bytes = approval.read_bytes()
    changed = False

    def change_authority():
        nonlocal changed
        changed = True
        if change == 'owner':
            approval.unlink()
        else:
            case['campaign'].pause('ARTIFICIAL_PARENT_ONLY_PREWRITE_AUTHORITY_CHANGE')

    def change_during_history(path, payload):
        result = original_immutable(path, payload)
        if path.name == 'ARTIFICIAL_BASE_SEGMENT_000007_DISPATCH.json' and not changed:
            change_authority()
        return result

    original_extension_state = gov._time_extension_state

    def change_after_extension_read(*args, **kwargs):
        result = original_extension_state(*args, **kwargs)
        if result['time_extension_mirror_pending'] and not changed:
            change_authority()
        return result

    try:
        with monkeypatch.context() as patch:
            if missing == 'quota':
                patch.setattr(gov, '_time_extension_state', change_after_extension_read)
            else:
                patch.setattr(governance_module, 'immutable', change_during_history)
            expected = 'OWNER_APPROVAL_NOT_REGISTERED' if change == 'owner' else 'AUTHORITY_CHANGED'
            with pytest.raises(PermissionError, match=expected):
                gov.reconcile_segment_mirrors(name)
        assert changed
        assert all(not path.exists() for path in missing_paths)
        assert all(path.read_bytes() == raw for path, raw in original_files.items())
        if change == 'owner':
            assert events(case).read_bytes() == original_events
        else:
            assert events(case).read_bytes().startswith(original_events)
        assert not list(case['account'].glob('*_SEGMENT_*_WORKER.json'))
        assert not list(case['account'].glob('*_SEGMENT_*_INPUT_ACCESS.json'))
    finally:
        if change == 'owner' and changed:
            approval.write_bytes(approval_bytes)  # 只恢复本用例人工Owner，保留失败工件供诊断。
