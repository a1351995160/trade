"""隔离人工父事件回放；Owner resolver 装配替身不代表真实签发或生产安装。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os

import pytest

from chanlun_trader.research_factory import account_purpose_time_extension_v1 as extensions
from chanlun_trader.research_factory import failed_account_recovery_v1 as recoveries
from chanlun_trader.research_factory import research_campaign_v1 as campaigns
from chanlun_trader.research_factory.budget import BudgetExhaustedError, BudgetLedgerMismatchError
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.campaign_scope_v1 import stage_remaining
from chanlun_trader.research_factory.mutation_boundary import MutationBusyError
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.run_budget import (
    AutonomousRunBudgetV2, effective_execution_status, effective_operation_allocation,
)
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile


CANONICAL_FAILED_SECONDS = 42339.896402100036


@pytest.fixture
def recovery_budget_case(tmp_path, monkeypatch):
    """只用 tmp_path；真实预算/Trial/父事件，纯签发读取层为显式人工装配替身。"""
    root = tmp_path.resolve()
    profile = execution_profile(SEGMENTED_PROFILE, 504)
    units = dict.fromkeys(AutonomousRunBudgetV2.resource_names, 10)
    units['wall_seconds'] = 288002
    authorization = {'authorization_id': 'artificial_failed_account_budget', 'objective_id': 'artificial_objective',
        'resource_limits': units, 'stages': ['EXPLORATION'],
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        'max_batches': 3, 'max_total_predictive_trials': 4, 'max_trials_per_batch': 2,
        'max_hypotheses_per_batch': 2, 'max_candidates_per_batch': 2, 'execution_profiles': [profile]}
    campaign = ResearchCampaignV1.create(root, authorization)
    mapping = {'ARTIFICIAL_BASE': 'BASE', 'ARTIFICIAL_STRESS': 'STRESS'}
    for operation_id in mapping.values():
        campaign.reserve_operation(operation_id=operation_id, batch_id='ARTIFICIAL_BATCH', stage='EXPLORATION',
            kind='ACCOUNT', subject_identity='artificial_' + operation_id,
            upper_bounds={'account_jobs': 1, 'wall_seconds': 28800}, execution_profile=profile)
    campaign.start_operation('BASE')
    base = campaign.peek_status()['base_authorization']
    extension_ref = {'path': str(root / 'ARTIFICIAL_TIME_EXTENSION.json'), 'sha256': stable_hash('artificial_extension')}
    extension_proof = {'manifest_ref': extension_ref, 'extension_id': stable_hash('artificial_extension_id')}
    original = campaign.peek_status()['operations']
    extension_accounts = {}
    for purpose, operation_id in mapping.items():
        operation = original[operation_id]
        extension_accounts[purpose] = {'operation_id': operation_id, 'original_profile': profile,
            'original_upper_bounds': operation['upper_bounds'], 'old_total_seconds': 28800,
            'effective_total_seconds': 72000, 'additional_reserved_seconds': 43200,
            'segment_count': 0, 'charged_seconds': 0, 'parent_segment_prefix_hash': stable_hash([]),
            'next_segment_number': 1}
    extension_value = {'proof': extension_proof, 'expires_at': base['expires_at'],
        'base_authorization_identity': stable_hash(base), 'aggregate_reserved_delta': 86400,
        'operation_ids': mapping, 'accounts': extension_accounts}

    def artificial_extension_resolver(reference, **kwargs):
        assert reference == extension_ref and os.path.normcase(str(kwargs['root'])) == os.path.normcase(str(root))
        return deepcopy(extension_value)

    monkeypatch.setattr(extensions, 'resolve_time_extension_manifest', artificial_extension_resolver)
    campaign.install_account_purpose_time_extension(extension_ref)
    for number in range(1, 51):
        campaign.start_execution_segment('BASE', segment_number=number)
        seconds = 850.0 if number < 50 else CANONICAL_FAILED_SECONDS - 49 * 850.0
        campaign.end_execution_segment('BASE', segment_number=number, seconds=seconds,
            evidence_identity='artificial_resource_' + str(number), outcome='FAILED' if number == 50 else 'CONTINUE')
    campaign.settle_operation('BASE', actual={'account_jobs': 1, 'wall_seconds': 42340}, outcome='FAILED',
        evidence_identity='artificial_original_failed_settlement')
    before = campaign.peek_status()
    accounts = {}
    for purpose, operation_id in mapping.items():
        operation = before['operations'][operation_id]
        row = {'operation_id': operation_id, 'status_at_install': operation['status'],
            'original_profile': profile, 'original_upper_bounds': operation['upper_bounds'],
            'effective_total_seconds': 72000, 'charged_seconds': operation.get('active_wall_seconds', 0),
            'segment_count': len(operation.get('segments', [])),
            'parent_segment_prefix_hash': stable_hash(operation.get('segments', [])),
            'next_segment_number': len(operation.get('segments', [])) + 1,
            'remaining_reserved_seconds': 29660 if operation_id == 'BASE' else 0,
            'time_extension_proof': extension_proof, 'parent_operation': deepcopy(operation)}
        if operation_id == 'BASE':
            row.update(parent_actual=deepcopy(operation['actual']),
                original_settlement={key: operation[key] for key in ('actual', 'outcome', 'evidence_identity')},
                checkpoint={'processed_days': 277, 'fixture_only': True})
        accounts[purpose] = row
    manifest_path = root / 'ARTIFICIAL_RECOVERY_RESOLVER_FIXTURE.json'
    manifest_path.write_text(json.dumps({'fixture_only': True, 'not_owner_approval': True}), encoding='utf-8')
    manifest_ref = {'path': str(manifest_path), 'sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest()}
    proof = {'manifest_ref': manifest_ref, 'recovery_id': stable_hash('artificial_recovery_id')}
    resolved = {'proof': proof, 'operation_id': 'BASE', 'original_failed_account': 'ARTIFICIAL_BASE',
        'failed_purpose': 'ARTIFICIAL_BASE', 'operation_ids': mapping, 'accounts': accounts,
        'account': accounts['ARTIFICIAL_BASE'], 'expires_at': base['expires_at'],
        'base_authorization_identity': stable_hash(base), 'summary_value': {'fixture_only': True}}
    case = {'root': root, 'campaign': campaign, 'manifest_ref': manifest_ref, 'resolved': resolved,
        'before': before, 'proof': proof, 'proof_revoked': False,
        'original_events': campaign.budget_path.with_name('run_budget_events.jsonl').read_bytes()}

    def artificial_recovery_resolver(reference, **kwargs):
        assert reference == manifest_ref and os.path.normcase(str(kwargs['root'])) == os.path.normcase(str(root))
        assert kwargs['authorization_id'] == base['authorization_id']
        assert kwargs['objective_id'] == base['objective_id']
        if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != manifest_ref['sha256']:
            raise PermissionError('ARTIFICIAL_RESOLVER_FIXTURE_CHANGED')
        if kwargs.get('for_dispatch') and case['proof_revoked']:
            raise PermissionError('ARTIFICIAL_RESOLVER_PROOF_REVOKED')
        return deepcopy(case['resolved'])

    monkeypatch.setattr(recoveries, 'resolve_failed_account_recovery_manifest', artificial_recovery_resolver)
    return case


def install(case):
    return case['campaign'].install_failed_account_recovery(case['manifest_ref'])


def charge(case, seconds, *, outcome='CONTINUE'):
    campaign = case['campaign']
    operation = campaign.peek_status()['operations']['BASE']
    number = len(operation['segments']) + 1
    dispatch = campaign.start_execution_segment('BASE', segment_number=number)
    return campaign.end_execution_segment('BASE', segment_number=number, seconds=seconds,
        evidence_identity='artificial_recovered_resource_' + str(number), outcome=outcome,
        dispatch_identity=dispatch['dispatch_identity'])


def settle(case, outcome='COMPLETED'):
    operation = case['campaign'].peek_status()['operations']['BASE']
    delta = math.ceil(operation['active_wall_seconds']) - operation['actual']['wall_seconds']
    return case['campaign'].settle_failed_account_recovery('BASE', actual_delta={'wall_seconds': delta},
        outcome=outcome, evidence_identity='artificial_recovery_settlement')


def assert_conservation(view):
    for name in AutonomousRunBudgetV2.resource_names:
        assert view['used'][name] + view['reserved'][name] + view['remaining'][name] == view['authorization']['resource_limits'][name]


def test_one_atomic_install_preserves_failed_operation_and_trial(recovery_budget_case):
    case = recovery_budget_case
    operation = install(case)
    view = case['campaign'].peek_status()
    before = case['before']
    assert operation['status'] == operation['outcome'] == 'FAILED'
    assert operation['actual'] == before['operations']['BASE']['actual']
    assert operation['segments'] == before['operations']['BASE']['segments']
    assert effective_execution_status(operation) == 'RUNNING'
    assert effective_operation_allocation(operation)['wall_seconds'] == 72000
    assert view['used']['wall_seconds'] == 42340
    assert view['reserved']['wall_seconds'] == 72000 + 29660
    assert view['remaining']['wall_seconds'] == 144002
    staged = deepcopy(view)
    staged['authorization']['scope_policy'] = {'summary': {'stage_limits': {
        'EXPLORATION': deepcopy(view['authorization']['resource_limits'])}}}
    assert stage_remaining(staged)['EXPLORATION']['wall_seconds'] == 144002
    assert view['trial_usage'] == before['trial_usage']
    assert view['batch_count'] == before['batch_count']
    assert view['authorization'] == before['authorization'] and view['grants'] == before['grants']
    stress = view['operations']['STRESS']
    assert effective_execution_status(stress) == 'RESERVED'
    assert 'failed_account_recovery' not in stress
    assert stress['failed_account_recovery_proof'] == operation['failed_account_recovery_proof'] == case['proof']
    assert case['campaign'].budget_path.with_name('run_budget_events.jsonl').read_bytes().startswith(case['original_events'])
    assert_conservation(view)
    head = case['campaign'].budget_path.with_name('run_budget_events.jsonl').read_bytes()
    assert install(case) == operation
    assert case['campaign'].settle_operation('BASE', actual={'account_jobs': 1, 'wall_seconds': 42340},
        outcome='FAILED', evidence_identity='artificial_original_failed_settlement') == operation
    assert case['campaign'].budget_path.with_name('run_budget_events.jsonl').read_bytes() == head
    case['campaign'].budget_path.unlink()
    rebuilt = ResearchCampaignV1(case['root'], case['campaign'].authorization_id).peek_status()
    assert rebuilt['operations'] == view['operations'] and rebuilt['trial_usage'] == view['trial_usage']
    assert (rebuilt['used'], rebuilt['reserved'], rebuilt['remaining']) == (view['used'], view['reserved'], view['remaining'])


@pytest.mark.parametrize(('seconds', 'expected_delta'), [([0.05, 0.05], 0), ([0.05, 0.05, 0.01], 1), ([0.2, 0.2], 1)])
def test_cumulative_ceil_difference_does_not_round_each_segment(recovery_budget_case, seconds, expected_delta):
    case = recovery_budget_case
    install(case)
    for index, amount in enumerate(seconds):
        charge(case, amount, outcome='COMPLETED' if index == len(seconds) - 1 else 'CONTINUE')
    operation = settle(case)
    view = case['campaign'].peek_status()
    assert operation['status'] == 'FAILED' and effective_execution_status(operation) == 'COMPLETED'
    assert operation['actual']['wall_seconds'] == 42340
    assert operation['failed_account_recovery']['actual_delta']['wall_seconds'] == expected_delta
    assert view['used']['wall_seconds'] == 42340 + expected_delta
    assert view['reserved']['wall_seconds'] == 72000
    assert operation['segments'][:50] == case['before']['operations']['BASE']['segments']
    assert view['trial_usage'] == case['before']['trial_usage']
    assert effective_operation_allocation(operation)['wall_seconds'] == 42340 + expected_delta
    assert settle(case) == operation
    with pytest.raises(BudgetLedgerMismatchError, match='SETTLEMENT_CONFLICT'):
        case['campaign'].settle_failed_account_recovery('BASE', actual_delta={'wall_seconds': expected_delta + 1},
            outcome='COMPLETED', evidence_identity='artificial_recovery_settlement')
    assert_conservation(view)


def test_install_does_not_remove_failed_campaign_pause(recovery_budget_case):
    case = recovery_budget_case
    case['campaign'].pause('artificial original failed-segment pause')
    install(case)
    assert case['campaign'].peek_status()['paused']
    with pytest.raises(PermissionError, match='PAUSED'):
        case['campaign'].start_execution_segment('BASE', segment_number=51)
    case['campaign'].resume('artificial explicit Owner-authorized recovery resume')
    assert case['campaign'].start_execution_segment('BASE', segment_number=51)['segment_number'] == 51


def test_dispatch_identity_late_callback_and_duplicate_charge(recovery_budget_case):
    case = recovery_budget_case
    install(case)
    campaign = case['campaign']
    first = campaign.start_execution_segment('BASE', segment_number=51)
    for identity in (None, '0' * 64):
        with pytest.raises(PermissionError, match='DISPATCH_IDENTITY_CONFLICT'):
            campaign.end_execution_segment('BASE', segment_number=51, seconds=0.2,
                evidence_identity='artificial_first', dispatch_identity=identity)
    result = campaign.end_execution_segment('BASE', segment_number=51, seconds=0.2,
        evidence_identity='artificial_first', dispatch_identity=first['dispatch_identity'])
    second = campaign.start_execution_segment('BASE', segment_number=52)
    assert campaign.end_execution_segment('BASE', segment_number=51, seconds=0.2,
        evidence_identity='artificial_first', dispatch_identity=first['dispatch_identity']) == result
    assert campaign.peek_status()['operations']['BASE']['active_segment'] == second
    with pytest.raises(PermissionError, match='DISPATCH_IDENTITY_CONFLICT'):
        campaign.end_execution_segment('BASE', segment_number=52, seconds=0.2,
            evidence_identity='artificial_first', dispatch_identity=first['dispatch_identity'])
    with pytest.raises(BudgetLedgerMismatchError, match='CHARGE_CONFLICT'):
        campaign.end_execution_segment('BASE', segment_number=51, seconds=0.3,
            evidence_identity='artificial_first', dispatch_identity=first['dispatch_identity'])
    assert campaign.peek_status()['operations']['BASE']['active_wall_seconds'] == CANONICAL_FAILED_SECONDS + 0.2


def test_recovery_second_failure_is_terminal_without_second_trial(recovery_budget_case):
    case = recovery_budget_case
    install(case)
    charge(case, 900.1, outcome='FAILED')
    operation = case['campaign'].peek_status()['operations']['BASE']
    assert operation['status'] == effective_execution_status(operation) == 'FAILED'
    with pytest.raises(PermissionError):
        case['campaign'].start_execution_segment('BASE', segment_number=52)
    settle(case, 'FAILED')
    assert install(case)['failed_account_recovery']['status'] == 'FAILED'
    with pytest.raises(PermissionError):
        case['campaign'].start_operation('STRESS')
    assert case['campaign'].peek_status()['trial_usage'] == case['before']['trial_usage']


def test_stress_uses_existing_trial_and_cannot_inherit_a_failed_retry(recovery_budget_case):
    case = recovery_budget_case
    install(case)
    charge(case, 0.2, outcome='COMPLETED')
    settle(case)
    campaign = case['campaign']
    campaign.start_operation('STRESS')
    dispatch = campaign.start_execution_segment('STRESS', segment_number=1)
    assert dispatch['failed_account_recovery_proof'] == case['proof']
    campaign.end_execution_segment('STRESS', segment_number=1, seconds=0.2, outcome='FAILED',
        evidence_identity='artificial_stress_failure', dispatch_identity=dispatch['dispatch_identity'])
    campaign.settle_operation('STRESS', actual={'account_jobs': 1, 'wall_seconds': 1}, outcome='FAILED',
        evidence_identity='artificial_stress_settlement')
    view = campaign.peek_status()
    assert view['operations']['STRESS']['status'] == 'FAILED'
    assert 'failed_account_recovery' not in view['operations']['STRESS']
    assert view['used']['wall_seconds'] == 42342 and view['reserved']['wall_seconds'] == 0
    assert view['trial_usage']['completed_trial_ids'] == ['BASE', 'STRESS']
    with pytest.raises(PermissionError):
        campaign.start_execution_segment('STRESS', segment_number=2)
    with pytest.raises(PermissionError, match='NOT_INSTALLED'):
        campaign.settle_failed_account_recovery('STRESS', actual_delta={'wall_seconds': 1},
            outcome='FAILED', evidence_identity='artificial_disallowed_stress_retry')
    assert_conservation(view)


@pytest.mark.parametrize('reason', ['expiry', 'campaign_revoke', 'proof_revoke'])
def test_already_dispatched_costs_can_settle_after_expiry_or_revocation(recovery_budget_case, monkeypatch, reason):
    case = recovery_budget_case
    install(case)
    campaign = case['campaign']
    dispatch = campaign.start_execution_segment('BASE', segment_number=51)
    if reason == 'expiry':
        expires_at = datetime.fromisoformat(case['before']['authorization']['expires_at'])

        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return (expires_at + timedelta(seconds=1)).astimezone(tz)

        monkeypatch.setattr(campaigns, 'datetime', ExpiredClock)
    elif reason == 'campaign_revoke':
        campaign.revoke('artificial revocation after dispatch')
    else:
        case['proof_revoked'] = True
    campaign.end_execution_segment('BASE', segment_number=51, seconds=0.2, outcome='COMPLETED',
        evidence_identity='artificial_late_resource', dispatch_identity=dispatch['dispatch_identity'])
    settle(case)
    with pytest.raises((PermissionError, BudgetExhaustedError)):
        campaign.start_operation('STRESS')
    view = campaign.peek_status()
    assert view['used']['wall_seconds'] == 42341 and view['reserved']['wall_seconds'] == 72000
    assert_conservation(view)


@pytest.mark.parametrize('last_outcome', [None, 'CONTINUE', 'PAUSED'])
def test_revoked_recovery_can_close_paid_prefix_before_next_dispatch(recovery_budget_case, last_outcome):
    case = recovery_budget_case
    install(case)
    if last_outcome is not None:
        charge(case, 0.2, outcome=last_outcome)
    case['campaign'].revoke('artificial stop between segments')
    operation = settle(case, 'FAILED')
    view = case['campaign'].peek_status()
    assert operation['status'] == effective_execution_status(operation) == 'FAILED'
    assert operation['actual']['wall_seconds'] == 42340
    assert view['used']['wall_seconds'] == (42340 if last_outcome is None else 42341)
    assert view['reserved']['wall_seconds'] == 72000
    assert view['trial_usage'] == case['before']['trial_usage']
    assert_conservation(view)


@pytest.mark.parametrize('stress_state', ['RUNNING', 'UNKNOWN', 'ACTIVE'])
def test_plain_failed_and_changed_stress_boundary_do_not_gain_retry(recovery_budget_case, stress_state):
    case = recovery_budget_case
    with pytest.raises(PermissionError, match='CONTINUATION_NOT_AUTHORIZED'):
        case['campaign'].start_execution_segment('BASE', segment_number=51)
    with pytest.raises(PermissionError, match='NOT_INSTALLED'):
        case['campaign'].settle_failed_account_recovery('BASE', actual_delta={'wall_seconds': 0},
            outcome='FAILED', evidence_identity='artificial_unapproved')
    case['campaign'].start_operation('STRESS')
    if stress_state == 'UNKNOWN':
        case['campaign'].mark_unknown('STRESS', 'artificial unknown receipt')
    elif stress_state == 'ACTIVE':
        case['campaign'].start_execution_segment('STRESS', segment_number=1)
    with pytest.raises(BudgetLedgerMismatchError, match='PROOF_INVALID'):
        install(case)
    assert 'failed_account_recovery' not in case['campaign'].peek_status()['operations']['BASE']


@pytest.mark.parametrize('field', ['parent_segment_prefix_hash', 'charged_seconds', 'remaining_reserved_seconds'])
def test_original_fee_prefix_and_remaining_cannot_be_changed_by_install(recovery_budget_case, field):
    case = recovery_budget_case
    account = case['resolved']['account']
    account[field] = '0' * 64 if field == 'parent_segment_prefix_hash' else account[field] + 1
    before = case['campaign'].budget_path.with_name('run_budget_events.jsonl').read_bytes()
    with pytest.raises(BudgetLedgerMismatchError, match='PROOF_INVALID'):
        install(case)
    assert case['campaign'].budget_path.with_name('run_budget_events.jsonl').read_bytes() == before
    assert case['campaign'].peek_status()['operations']['BASE'] == case['before']['operations']['BASE']


def test_parallel_install_has_single_event_and_no_new_trial(recovery_budget_case):
    case = recovery_budget_case

    def attempt(_):
        try:
            return install(case)['failed_account_recovery']['proof']
        except MutationBusyError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, range(2)))
    assert case['proof'] in outcomes
    rows = [json.loads(line) for line in case['campaign'].budget_path.with_name('run_budget_events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert sum(row['event_type'] == 'CAMPAIGN_FAILED_ACCOUNT_RECOVERY_INSTALLED' for row in rows) == 1
    assert case['campaign'].peek_status()['trial_usage'] == case['before']['trial_usage']


def test_recovery_and_other_purpose_compete_for_real_headroom(recovery_budget_case):
    case = recovery_budget_case
    campaign = case['campaign']
    competing_seconds = case['before']['remaining']['wall_seconds'] - 29660 + 1

    def attempt(action):
        try:
            if action == 'recovery':
                install(case)
            else:
                campaign.reserve_operation(operation_id='ARTIFICIAL_VERIFY', batch_id='VERIFY_BATCH',
                    stage='EXPLORATION', kind='VERIFY', subject_identity='artificial_other_purpose',
                    upper_bounds={'verification_jobs': 1, 'wall_seconds': competing_seconds})
            return action
        except (MutationBusyError, BudgetExhaustedError, BudgetLedgerMismatchError):
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, ('recovery', 'verification')))
    assert sum(outcome is not None for outcome in outcomes) == 1
    view = campaign.peek_status()
    assert view['remaining']['wall_seconds'] >= 0
    assert_conservation(view)


def test_exact_72000_boundary_releases_only_recovery_reservation(recovery_budget_case):
    case = recovery_budget_case
    install(case)
    for _ in range(32):
        charge(case, 900.0)
    operation = case['campaign'].peek_status()['operations']['BASE']
    charge(case, 72000 - operation['active_wall_seconds'], outcome='COMPLETED')
    operation = settle(case)
    view = case['campaign'].peek_status()
    assert operation['active_wall_seconds'] == 72000
    assert operation['failed_account_recovery']['actual_delta']['wall_seconds'] == 29660
    assert view['used']['wall_seconds'] == view['reserved']['wall_seconds'] == 72000
    with pytest.raises(PermissionError):
        case['campaign'].start_execution_segment('BASE', segment_number=len(operation['segments']) + 1)
    assert view['trial_usage'] == case['before']['trial_usage']
    assert_conservation(view)


def test_unknown_recovery_blocks_dispatch_but_can_charge_dispatched_segment(recovery_budget_case):
    case = recovery_budget_case
    install(case)
    campaign = case['campaign']
    dispatch = campaign.start_execution_segment('BASE', segment_number=51)
    campaign.mark_unknown('BASE', 'artificial receipt pending')
    operation = campaign.peek_status()['operations']['BASE']
    assert operation['status'] == 'FAILED' and effective_execution_status(operation) == 'UNKNOWN'
    with pytest.raises(PermissionError):
        campaign.start_execution_segment('BASE', segment_number=52)
    campaign.end_execution_segment('BASE', segment_number=51, outcome='FAILED',
        dispatch_identity=dispatch['dispatch_identity'])
    settle(case, 'FAILED')
    assert campaign.peek_status()['used']['wall_seconds'] == math.ceil(CANONICAL_FAILED_SECONDS + 900)


def test_unsettled_or_refunded_recovery_cannot_release_reservation(recovery_budget_case):
    case = recovery_budget_case
    install(case)
    charge(case, 0.2)
    with pytest.raises(BudgetLedgerMismatchError, match='SETTLEMENT_CONFLICT'):
        settle(case)
    charge(case, 0.2, outcome='COMPLETED')
    with pytest.raises(BudgetLedgerMismatchError, match='SETTLEMENT_CONFLICT'):
        case['campaign'].settle_failed_account_recovery('BASE', actual_delta={'wall_seconds': 0},
            outcome='COMPLETED', evidence_identity='artificial_refund_attempt')
    with pytest.raises(ValueError, match='SETTLEMENT_INVALID'):
        case['campaign'].settle_failed_account_recovery('BASE', actual_delta={'account_jobs': 1, 'wall_seconds': 1},
            outcome='COMPLETED', evidence_identity='artificial_new_trial_attempt')
    assert case['campaign'].peek_status()['reserved']['wall_seconds'] == 101660
