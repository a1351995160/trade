"""一次同根人工 STRESS 失败接续，使用原账本协议与新签发证明。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory import artificial_stress_repair_v1 as repair
from chanlun_trader.research_factory import research_campaign_v1 as campaigns
from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
from chanlun_trader.research_factory.budget import BudgetLedgerMismatchError
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.run_budget import (
    effective_execution_status, effective_segment_allowance, execution_dispatch_identity,
)
from test_account_purpose_time_extension_v1 import control, pin, write
from test_governance_source_continuation_v2 import continued_case
from test_governance_source_revision_v1 import revised_case


def test_repair_paths_keep_original_recovery_discovery_and_scope_slots_distinct(tmp_path):
    from chanlun_trader.research_factory.failed_account_recovery_v1 import _job_manifest_reference

    account = tmp_path / 'reports/tasks/task/account'
    account.mkdir(parents=True)
    task = account.parent / 'TASK.json'
    campaign = tmp_path / 'reports/research_campaigns/campaign'
    campaign.mkdir(parents=True)
    ledger = campaign / 'run_budget_events.jsonl'
    owner = tmp_path / 'owner'
    owner.mkdir()
    old_manifest = task.parent / 'failed-account-recoveries/original/MANIFEST.json'
    old_slot = campaign / 'failed-account-recovery-slots/original.json'
    old_ref = write(old_manifest, {'summary': {'root': str(tmp_path)}})
    write(old_slot, {'recovery_id': 'original', 'manifest_ref': old_ref})
    job = {'root': str(account)}
    predecessor = SimpleNamespace(root=tmp_path, summary={'task_ref': {'path': str(task)},
        'ledger': {'path': str(ledger)}, 'approval_store': str(owner), 'identity': 'fixture-v2'})
    manifest, _, marker = repair._paths(predecessor)
    expected = _job_manifest_reference(job)
    write(manifest, {'fixture_only': True})
    write(marker, {'manifest_ref': pin(manifest)})
    assert _job_manifest_reference(job) == expected == (str(tmp_path), old_ref, account)
    assert list(old_slot.parent.glob('*.json')) == [old_slot]


def test_artificial_stress_repair_preserves_failed_trial_and_dispatches_once(continued_case, monkeypatch):
    case = continued_case
    root, account, gov, campaign = (case[key] for key in ('root', 'account', 'gov', 'campaign'))
    name = 'ARTIFICIAL_STRESS'
    control(case, 'RESUME')
    context = AccountVersionContinuationV1.from_job(case['job'])
    dispatched = gov.start_segment(name)
    context.record_dispatch(name, dispatched)
    number = dispatched['segment_number']
    stem = name + '_SEGMENT_' + str(number).zfill(6)
    dispatch_id = {key: dispatched[key] for key in (
        'dispatch_id', 'dispatch_identity', 'segment_number', 'time_extension_proof', 'failed_account_recovery_proof')}
    write(account / (stem + '_WORKER.json'), dispatch_id)
    write(account / (stem + '_FAILURE.json'), {
        **dispatch_id, 'exception_type': 'PermissionError', 'message': 'RUNTIME_SOURCE_CHANGED', 'automatic_retry': False})
    write(account / (stem + '_STARTUP.json'), {
        **dispatch_id, 'validation_complete': False, 'stages': [
            {'stage': stage, 'completed': complete} for stage, complete in (
                ('JOB_READ', True), ('VALIDATE_SOURCES', True), ('GOVERNANCE_SCOPE', True), ('STRATEGY_PREPARE', False))]})
    evidence = write(account / (stem + '_RESOURCE.json'), {
        **dispatch_id, 'returncode': 1, 'timed_out': False, 'elapsed_wall_seconds': .2})
    gov.end_segment(name, number, seconds=.2, evidence_identity=evidence['sha256'],
        outcome='FAILED', dispatch_identity=dispatched['dispatch_identity'])
    prior = gov.segment_status(name)
    gov.settle(name, completed=False, seconds=prior['charged_seconds'], result_hash=None)
    control(case, 'PAUSE')

    context = AccountVersionContinuationV1.from_job(case['job'])
    v2 = context.recovery.governance_revision
    checkpoint = Path(case['job']['items'][name]['backend_options']['checkpoint_path'])
    checkpoint.write_bytes(checkpoint.read_bytes() + b' ')
    historical = {path: path.read_bytes() for path in account.rglob('*') if path.is_file() and '.lock' not in path.name}
    view = campaign.peek_status()
    op_id = v2.summary['operation_ids'][name]
    operation = deepcopy(view['operations'][op_id])
    ledger = Path(v2.summary['ledger']['path'])
    before = ledger.read_bytes()
    folder = root / 'reports' / 'repair-stage'
    folder.mkdir()
    source = root / repair.PREDECESSOR_MODULE
    archive = folder / 'old_second.py'
    archive.write_bytes(source.read_bytes())
    candidate = folder / 'new_second.py'
    candidate.write_bytes(source.read_bytes() + b'\n# artificial stress repair fixture\n')
    rows = [{'path': str(source), 'old_sha256': pin(source)['sha256'], 'new_sha256': pin(candidate)['sha256'],
        'archive_ref': pin(archive), 'candidate_ref': pin(candidate)}]
    module = folder / 'module.py'
    module.write_bytes((Path(__file__).resolve().parents[2] / repair.MODULE_PATH).read_bytes())
    added = {'path': str(root / repair.MODULE_PATH), 'sha256': pin(module)['sha256']}
    limit = 72000 - operation['actual']['wall_seconds']
    human_value = {
        'schema_version': repair.HUMAN_VERSION, 'approved': True, 'artificial_only': True, 'root': str(root),
        'job_ref': v2.summary['job_ref'], 'predecessor_ref': v2.reference, 'purpose': name,
        'maximum_repairs': 1, 'each_account_seconds': 72000, 'global_wall_seconds': v2.summary['global_wall_seconds'],
        'execution_expires_at': datetime.fromisoformat(v2.execution_expires_at).astimezone(
            timezone(timedelta(hours=8))).isoformat(),
        'failed_segment_number': number, 'next_segment_number': number + 1,
        'next_account_day': 2, 'completed_days_to_preserve': 505, 'settled_segments_to_preserve': 53,
        'max_remaining_seconds': limit, 'requires_exact_source_and_protocol_gates': True, 'all_fresh_assertions_required': True,
        'allow_new_task_candidate_purpose_grant_or_root': False, 'allow_economic_source_or_input_change': False,
        'allow_budget_increase': False, 'allow_deadline_extension': False, 'automatic_retry': False, 'real_account_actions_allowed': False,
    }
    human = write(folder / 'human.json', human_value)
    verification = write(folder / 'verification.json', {
        'status': 'PASSED', 'scope': 'ARTIFICIAL_STRESS_REPAIR', 'human_authorization_ref': human,
        'source_replacements': rows, 'added_runtime_ref': added, 'fixture_only': 'ARTIFICIAL_PROTOCOL_CONTRACT'})
    inputs = dict(replacements=rows, added_runtime_ref=added, verification_ref=verification, human_authorization_ref=human)
    for key, value in (('max_remaining_seconds', limit - 1), ('allow_deadline_extension', True), ('maximum_repairs', 2)):
        rejected = write(folder / (key + '.json'), {**human_value, key: value})
        with pytest.raises(PermissionError, match='HUMAN_SCOPE_CONFLICT|ACCOUNT_BOUNDARY_CONFLICT'):
            repair.prepare(context, **{**inputs, 'human_authorization_ref': rejected})
    assert ledger.read_bytes() == before

    summary = repair.prepare(context, **inputs)
    assert summary['checkpoint_refs'][name] == pin(checkpoint)
    assert summary['checkpoint_refs'][name]['sha256'] != v2.summary['accounts'][name]['checkpoint_archive']['sha256']
    approval = case['approvals'].approve(summary, approver='artificial_owner', capability=case['token'])

    class AtExpiry(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(summary['expires_at']).astimezone(tz or timezone.utc)

    with monkeypatch.context() as patch:
        patch.setattr(context, 'job', {**context.job, 'root': str(account.parent)})
        with pytest.raises(PermissionError, match='INSTALL_JOB_CHANGED'):
            repair.install(context, summary, approval, added_runtime_candidate_ref=pin(module))
    assert pin(source)['sha256'] == pin(archive)['sha256']
    assert not (root / repair.MODULE_PATH).exists()
    reference = repair.install(context, summary, approval, added_runtime_candidate_ref=pin(module))
    assert ledger.read_bytes() == before
    pending = AccountVersionContinuationV1.from_job(case['job'])
    assert pending.recovery.scope_reference == context.recovery.scope_reference
    with pytest.raises(PermissionError, match='ARTIFICIAL_STRESS_REPAIR_PARENT_ACTIVATION_REQUIRED'):
        pending.recovery.governance_revision.validate_return(for_dispatch=True)

    require_json = repair.OwnerApprovalStoreV1.require_json

    def revoked_owner(store, item, value):
        if item == approval:
            raise PermissionError('ARTIFICIAL_REPAIR_FIXTURE_OWNER_REVOKED')
        return require_json(store, item, value)

    with monkeypatch.context() as patch:
        patch.setattr(repair.OwnerApprovalStoreV1, 'require_json', revoked_owner)
        with pytest.raises(PermissionError, match='FIXTURE_OWNER_REVOKED'):
            campaign.install_artificial_stress_repair(reference)
    with monkeypatch.context() as patch:
        patch.setattr(repair, 'datetime', AtExpiry)
        with pytest.raises(BudgetLedgerMismatchError, match='ARTIFICIAL_STRESS_REPAIR_SUBMISSION_INVALID'):
            campaign.install_artificial_stress_repair(reference)
    assert ledger.read_bytes() == before
    activated = campaign.install_artificial_stress_repair(reference)
    assert ledger.read_bytes().startswith(before)
    assert len(ledger.read_bytes().splitlines()) == len(before.splitlines()) + 1
    new_op = activated[op_id]
    assert new_op['status'] == operation['status'] == 'FAILED'
    assert new_op['actual'] == operation['actual']
    assert new_op['segments'] == operation['segments']
    assert new_op['recovery_boundary'] == operation['recovery_boundary']
    assert effective_execution_status(new_op) == 'RUNNING'
    assert new_op['failed_account_recovery']['remaining_reserved_seconds'] == limit
    assert new_op['failed_account_recovery']['charge_ceiling_seconds'] - prior['charged_seconds'] == pytest.approx(limit)
    fresh = campaign.peek_status()
    for key in ('used', 'grants', 'trial_usage', 'authorization'):
        assert fresh[key] == view[key]
    assert fresh['reserved']['wall_seconds'] - view['reserved']['wall_seconds'] == limit
    assert all(path.read_bytes() == data for path, data in historical.items())

    post_activation = ledger.read_bytes()
    with pytest.raises(PermissionError, match='PARENT_BOUNDARY_CONFLICT'):
        campaign.install_artificial_stress_repair(reference)
    with pytest.raises(BudgetLedgerMismatchError, match='DERIVED_FIELDS_FORBIDDEN'):
        campaign._budget_with_view()[0].campaign_event('CAMPAIGN_OPERATION_STARTED', {
            'operation_id': op_id, 'charge_ceiling_seconds': 72001})
    assert ledger.read_bytes() == post_activation
    installed = AccountVersionContinuationV1.from_job(case['job'])
    installed_revision = installed.recovery.governance_revision
    assert installed_revision.segment_proof(name, number) == v2.segment_proof(name, number)
    assert installed_revision.segment_proof(name, number + 1)['manifest_ref'] == reference
    assert installed_revision.runtime_additions()[-1] == added
    assert installed_revision.runtime_additions()[-2]['sha256'] == rows[0]['new_sha256']
    failure_path = account / (stem + '_FAILURE.json')
    failure_bytes = failure_path.read_bytes()
    try:
        failure_path.write_bytes(failure_bytes + b' ')
        with pytest.raises(PermissionError, match='CHANGED|INVALID'):
            AccountVersionContinuationV1.from_job(case['job'], for_dispatch=True)
    finally:
        failure_path.write_bytes(failure_bytes)

    with monkeypatch.context() as patch:
        patch.setattr(repair, 'datetime', AtExpiry)
        with pytest.raises(PermissionError, match='ARTIFICIAL_STRESS_REPAIR_EXPIRED'):
            installed_revision.validate_return(for_dispatch=True)
    assert ledger.read_bytes() == post_activation

    control(case, 'RESUME')
    AccountVersionContinuationV1.from_job(case['job'], for_dispatch=True)
    installed_at = datetime.fromisoformat(new_op['failed_account_recovery']['repair_installed_at'])
    backdated = installed_at - timedelta(microseconds=1)

    class BeforeActivation(datetime):
        @classmethod
        def now(cls, tz=None):
            return backdated.astimezone(tz or timezone.utc)

    with monkeypatch.context() as patch:
        patch.setattr(campaigns, 'datetime', BeforeActivation)
        with pytest.raises(PermissionError, match='DISPATCH_BEFORE_INSTALLATION'):
            campaign.start_execution_segment(op_id, segment_number=number + 1)
    assert ledger.read_bytes() == post_activation
    started = gov.start_recovery(name)
    assert started['original_settlement_sha256'] == summary['boundary']['settlement_ref']['sha256']
    recovered = gov.start_segment(name, segment_number=number + 1)
    installed.record_dispatch(name, recovered)
    assert recovered['segment_number'] == number + 1
    assert recovered['charged_before'] == prior['charged_seconds']
    assert recovered['upper_bound_seconds'] == 900
    assert gov.segment_status(name)['pending'] == recovered
    parent_after_dispatch = ledger.read_bytes()
    budget = campaign._budget_with_view()[0]
    event = budget._events[-1]
    assert event['event_type'] == 'CAMPAIGN_EXECUTION_SEGMENT_DISPATCHED'
    item = event['payload']
    item['dispatched_at'] = backdated.isoformat()
    item['dispatch_identity'] = execution_dispatch_identity(item)
    event['event_id'] = stable_hash({key: event[key] for key in ('run_id', 'event_type', 'payload')})
    with pytest.raises(BudgetLedgerMismatchError, match='CAMPAIGN_FAILED_ACCOUNT_RECOVERY_DISPATCH_CONFLICT'):
        budget.campaign_view()
    assert ledger.read_bytes() == parent_after_dispatch
    recovered_ref = write(account / (name + '_SEGMENT_' + str(number + 1).zfill(6) + '_RESOURCE.json'), {
        **{key: recovered[key] for key in dispatch_id}, 'returncode': 0, 'timed_out': False, 'elapsed_wall_seconds': .2})
    gov.end_segment(name, number + 1, seconds=.2, evidence_identity=recovered_ref['sha256'],
        outcome='COMPLETED', dispatch_identity=recovered['dispatch_identity'])
    seconds = gov.segment_status(name)['charged_seconds']
    gov.settle_recovery(name, completed=True, seconds=seconds, result_hash='fixture-recovered-result')
    finished = campaign.peek_status()
    assert finished['operations'][op_id]['status'] == 'FAILED'
    assert effective_execution_status(finished['operations'][op_id]) == 'COMPLETED'
    assert finished['trial_usage'] == view['trial_usage']
    assert all(path.read_bytes() == data for path, data in historical.items())
    assert not (account / (name + '_SEGMENT_' + str(number + 2).zfill(6) + '_DISPATCH.json')).exists()


def test_stress_repair_cap_is_distinct_from_original_total():
    from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile
    profile = execution_profile(SEGMENTED_PROFILE, 504)
    proof = {'manifest_ref': {}, 'extension_id': 'fixture'}
    ceiling = 52421.31471520029 + 19578
    allowed = effective_segment_allowance(profile, ceiling - 100, seconds_to_expiry=900,
        effective_total_seconds=72000, time_extension_proof=proof, charge_ceiling_seconds=ceiling)
    assert allowed == 100
    with pytest.raises(PermissionError, match='COOPERATIVE_WINDOW_EXHAUSTED'):
        effective_segment_allowance(profile, ceiling - 70, seconds_to_expiry=900,
            effective_total_seconds=72000, time_extension_proof=proof, charge_ceiling_seconds=ceiling)
    with pytest.raises(PermissionError, match='LIMIT_CONFLICT'):
        effective_segment_allowance(profile, ceiling - 100, seconds_to_expiry=900,
            effective_total_seconds=72000, time_extension_proof=proof, charge_ceiling_seconds=72001)
