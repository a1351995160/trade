"""仅合成 Owner/源码/终态引用；不读真实价格或启动账户、报告。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory import continuous_research_contract_v1 as contracts
from chanlun_trader.research_factory import report_recovery_capability_bridge_v2 as bridges
from chanlun_trader.research_factory import report_repair_capability_bridge_v1 as old_bridges
from chanlun_trader.research_factory.campaign_scope_v1 import (
    OwnerApprovalStoreV1, bind_campaign_scope, scope_summary, validate_scope_policy,
)
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.run_budget import AutonomousRunBudgetV2
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile
from test_continuous_research_contract_v1 import build


def _fingerprint(value):
    value['fingerprint'] = stable_hash({key: item for key, item in value.items() if key != 'fingerprint'})
    return value


def _reference(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    return _reference(path)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    current = bridges.capabilities()
    repository = Path(bridges.__file__).resolve().parents[3]
    job_root = root / 'reports' / 'synthetic_job'
    archive = job_root / 'source-archive'
    archive.mkdir(parents=True)
    sources, replacements = {}, []
    for relative in sorted(bridges.SOURCE_PATHS):
        source = root / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes((repository / relative).read_bytes())
        original = ('synthetic original ' + relative).encode()
        digest = hashlib.sha256(original).hexdigest()
        archived = archive / (digest + '_' + source.name)
        archived.write_bytes(original)
        sources[str(source)] = digest
        replacements.append({'path': str(source), 'old_sha256': digest,
            'new_sha256': _reference(source)['sha256'], 'archive_ref': _reference(archived)})
    untouched = root / 'scripts' / 'unchanged_synthetic.py'
    untouched.write_bytes(b'synthetic unchanged implementation')
    digest = _reference(untouched)['sha256']
    sources[str(untouched)] = digest
    (archive / (digest + '_' + untouched.name)).write_bytes(untouched.read_bytes())
    old = deepcopy(current)
    for section in (old, old['full_universe'], old['long_horizon']):
        for entry in replacements:
            name = Path(entry['path']).name
            if name in section['source_hashes']:
                section['source_hashes'][name] = entry['old_sha256']
    _fingerprint(old)
    monkeypatch.setattr(contracts, 'capabilities', lambda: deepcopy(old))
    contract = build(old)
    units = dict.fromkeys(AutonomousRunBudgetV2.resource_names, 100)
    units.update(verification_jobs=4, account_jobs=4, wall_seconds=288002,
                 model_calls=0, model_tokens=0, model_cost_microunits=0)
    stages = {stage: {name: amount // 2 for name, amount in units.items()}
              for stage in ('EXPLORATION', 'CONFIRMATION')}
    authorization = {'authorization_id': 'synthetic_recovery', 'objective_id': contract['objective_id'],
        'resource_limits': units, 'stages': list(stages),
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        'max_batches': 2, 'max_total_predictive_trials': 2, 'max_trials_per_batch': 2,
        'max_hypotheses_per_batch': 2, 'max_candidates_per_batch': 2,
        'execution_profiles': contract['scope']['execution_profiles']}
    capability = object()
    approvals = OwnerApprovalStoreV1(root / 'protected_owner', owner_capability=capability)
    scope = scope_summary(root, authorization, contract, stages)
    approval = approvals.approve(scope, approver='synthetic_owner', capability=capability)
    bound = bind_campaign_scope(root, authorization, contract=contract, stage_limits=stages,
                               approvals=approvals, approval_ref=approval)
    campaign = ResearchCampaignV1.create(root, bound)
    base = campaign.peek_status()['base_authorization']
    job_ref = _write(job_root / 'JOB.json', {'root': str(job_root),
        'objective_id': base['objective_id'], 'source_hashes': sources})
    profile = execution_profile(SEGMENTED_PROFILE, 504, 'RESEARCH_REPORT')
    parent_summary = {'base_authorization_identity': stable_hash(base),
        'authorization_id': base['authorization_id'], 'objective_id': base['objective_id'],
        'approval_store': str(approvals.directory), 'repair_id': stable_hash('parent_repair'),
        'task_id': stable_hash('original_task'), 'original': {'job': job_ref}, 'execution_profile': profile}
    parent_approval = approvals.approve(parent_summary, approver='synthetic_owner', capability=capability)
    parent = {'schema_version': 'REPORT_REPAIR_MANIFEST_V1', 'summary': parent_summary,
              'approval_ref': parent_approval}
    parent['identity'] = stable_hash(parent)
    parent_ref = _write(root / 'reports' / 'parent_repair' / 'MANIFEST.json', parent)
    proposal_path = root / 'reports' / 'repair' / 'REPORT_RENDER_CONTINUATION_RECOVERY_PROPOSAL_CN.md'
    proposal_path.parent.mkdir()
    proposal_path.write_text('合成的有限报告恢复批准方案', encoding='utf-8')
    proposal_ref = _reference(proposal_path)
    human = {'schema_version': 'HUMAN_REPORT_RENDER_RECOVERY_CONFIRMATION_V1',
        'origin': 'CURRENT_USER_REPLY_TO_EXACT_FINITE_REPORT_RENDER_RECOVERY_PROPOSAL',
        'proposal': proposal_ref, 'parent_repair_id': parent_summary['repair_id'],
        'parent_manifest_sha256': parent_ref['sha256'], 'campaign_id': base['authorization_id'],
        'objective_id': base['objective_id'], 'verification_jobs_delta': 1,
        'effective_verification_jobs_from': 5, 'effective_verification_jobs_to': 6,
        'stage': 'EXPLORATION', 'maximum_additional_report_recoveries': 1,
        'recovery_total_seconds': profile['total_seconds'], 'worker_seconds': profile['worker_seconds'],
        'memory_mib': profile['memory_mib'], 'campaign_total_wall_seconds': base['resource_limits']['wall_seconds'],
        'expires_at': base['expires_at'], 'account_replay_permitted': False, 'old_records_immutable': True,
        'controlled_boundary_settlement_authorized': True, 'exact_source_version_recovery_authorized': True,
        'strategy_threshold_changes_permitted': False, 'project_model_api_calls': 0, 'user_reply': '批准这一次有限恢复'}
    human_ref = _write(proposal_path.with_name('HUMAN_REPORT_RENDER_RECOVERY_CONFIRMATION.json'), human)
    stop_ref = _write(root / 'reports' / 'stop' / 'REQUEST.json',
        {'summary': {'proposal_ref': proposal_ref, 'human_confirmation_ref': human_ref}})
    terminal = {'schema_version': 'REPORT_REPAIR_STOP_TERMINAL_V2', 'status': 'REPORT_REPAIR_STOPPED',
        'authorization_id': base['authorization_id'], 'base_authorization_identity': stable_hash(base),
        'parent_manifest_ref': parent_ref, 'stop_request_ref': stop_ref,
        'repair_id': parent_summary['repair_id'], 'task_id': parent_summary['task_id']}
    terminal['identity'] = stable_hash(terminal)
    terminal_ref = _write(root / 'reports' / 'stop' / 'TERMINAL.json', terminal)
    context = {'parent_terminal_ref': terminal_ref,
        'recovery_id': stable_hash({'version': bridges.RECOVERY_VERSION,
            'authorization_id': base['authorization_id'], 'parent_manifest_ref': parent_ref}),
        'proposal_ref': proposal_ref, 'human_confirmation_ref': human_ref}
    # 终态协议由专门测试验证；这里替身仍检查所引用字节，不能忽略终态引用。
    monkeypatch.setattr(bridges, '_terminal', lambda path, ref, auth: bridges._read(ref, path))
    monkeypatch.setattr(contracts, 'capabilities', lambda: deepcopy(current))
    monkeypatch.setattr(bridges, 'capabilities', lambda: deepcopy(current))
    return {'root': root, 'campaign': campaign, 'base': base, 'capability': capability,
        'approvals': approvals, 'replacements': replacements, 'old': old, 'current': current,
        'context': context, 'terminal': terminal, 'human': human, 'untouched': untouched}


def _summary(fixture):
    return bridges.ReportRecoveryCapabilityBridgeV2.summary(fixture['root'], fixture['base'],
        recovery_context=fixture['context'], source_replacements=fixture['replacements'])


def _install(fixture):
    summary = _summary(fixture)
    approval = fixture['approvals'].approve(summary, approver='synthetic_owner', capability=fixture['capability'])
    reference = bridges.ReportRecoveryCapabilityBridgeV2.install(fixture['root'], fixture['base'], summary,
                                                               approval_ref=approval)
    return summary, reference


def _repin_human(fixture, **changes):
    human = {**deepcopy(fixture['human']), **changes}
    reference = fixture['context']['human_confirmation_ref']
    fixture['context']['human_confirmation_ref'] = _write(Path(reference['path']), human)
    terminal = deepcopy(fixture['terminal'])
    request_ref = terminal['stop_request_ref']
    request = bridges._read(request_ref, fixture['root'])
    request['summary']['human_confirmation_ref'] = fixture['context']['human_confirmation_ref']
    terminal['stop_request_ref'] = _write(Path(request_ref['path']), request)
    terminal['identity'] = stable_hash({key: value for key, value in terminal.items() if key != 'identity'})
    reference = fixture['context']['parent_terminal_ref']
    fixture['context']['parent_terminal_ref'] = _write(Path(reference['path']), terminal)


def test_no_bridge_no_relaxation_and_summary_read_only(fixture):
    files = {path: path.read_bytes() for path in fixture['root'].rglob('*') if path.is_file()}
    _summary(fixture)
    assert {path: path.read_bytes() for path in fixture['root'].rglob('*') if path.is_file()} == files
    assert not bridges.ReportRecoveryCapabilityBridgeV2.is_registered(fixture['root'], fixture['base'])
    with pytest.raises(ValueError, match='CONTINUOUS_CAPABILITIES_CHANGED_NEW_VERSION_REQUIRED'):
        fixture['campaign'].peek_status()


def test_installed_bridge_preserves_old_authorization_budget_and_contract(fixture):
    campaign = fixture['campaign']
    files = {path: path.read_bytes() for path in (campaign.authorization_path, campaign.budget_path,
             campaign.budget_path.with_name('run_budget_events.jsonl'))}
    summary, reference = _install(fixture)
    result = bridges.ReportRecoveryCapabilityBridgeV2.read(fixture['root'], fixture['base'], reference=reference)
    assert result['record']['summary'] == summary
    assert result['capabilities_snapshot'] == fixture['old']
    assert result['reference'] == reference
    assert summary['parent_terminal_identity'] == fixture['terminal']['identity']
    assert summary['original_capabilities_fingerprint'] == fixture['old']['fingerprint']
    assert summary['live_capabilities_fingerprint'] == fixture['current']['fingerprint']
    assert campaign.peek_status()['base_authorization'] == fixture['base']
    assert validate_scope_policy(fixture['root'], fixture['base']) == fixture['base']['scope_policy']
    assert _install(fixture)[1] == reference
    assert all(path.read_bytes() == value for path, value in files.items())


@pytest.mark.parametrize('change', ['empty', 'duplicate', 'other_path', 'old_sha', 'new_sha', 'archive_sha', 'archive_path', 'extra_key'])
def test_source_and_original_archive_allowlist_is_exact(fixture, change):
    entries = fixture['replacements']
    if change == 'empty':
        entries.clear()
    elif change == 'duplicate':
        entries[-1] = deepcopy(entries[0])
    elif change == 'other_path':
        entries[0]['path'] = str(fixture['untouched'])
    elif change in ('old_sha', 'new_sha'):
        entries[0][change + '256'] = stable_hash('wrong source')
    elif change == 'archive_sha':
        entries[0]['archive_ref']['sha256'] = stable_hash('wrong archive')
    elif change == 'archive_path':
        original = Path(entries[0]['archive_ref']['path'])
        other = original.with_name('same_bytes_other_archive.py')
        other.write_bytes(original.read_bytes())
        entries[0]['archive_ref']['path'] = str(other)
    else:
        entries[0]['any_source'] = True
    with pytest.raises((ValueError, PermissionError)):
        _summary(fixture)


@pytest.mark.parametrize('name', ['run_strategy_account_v1.py', 'universe_benchmark_v1.py', 'universe_account_inputs_v1.py', 'unchanged_synthetic.py'])
def test_live_sources_rechecked_even_when_outside_capabilities_hashes(fixture, name):
    _, reference = _install(fixture)
    path = fixture['untouched'] if name == fixture['untouched'].name else Path(next(
        entry['path'] for entry in fixture['replacements'] if Path(entry['path']).name == name))
    path.write_bytes(path.read_bytes() + b'\nchanged after approval')
    with pytest.raises(PermissionError, match='UNAPPROVED_SOURCE_CHANGED'):
        bridges.ReportRecoveryCapabilityBridgeV2.read(fixture['root'], fixture['base'], reference=reference)


@pytest.mark.parametrize('change', ['feature', 'source', 'data', 'long_source', 'fingerprint'])
def test_unapproved_capability_changes_cannot_be_reconstructed(fixture, monkeypatch, change):
    current = deepcopy(fixture['current'])
    if change == 'feature':
        current['features'][0]['public_entry'] = False
    elif change == 'source':
        current['full_universe']['source_hashes']['universe_submission_v1.py'] = stable_hash('other change')
    elif change == 'long_source':
        current['long_horizon']['source_hashes']['universe_account_backend_v2.py'] = stable_hash('other change')
    elif change == 'data':
        current['data'] = {'status': 'changed'}
    if change != 'fingerprint':
        _fingerprint(current)
    else:
        current['fingerprint'] = fixture['old']['fingerprint']
    monkeypatch.setattr(bridges, 'capabilities', lambda: current)
    with pytest.raises(PermissionError, match='CURRENT_SNAPSHOT_INVALID|OTHER_CAPABILITIES_CHANGED'):
        _summary(fixture)


@pytest.mark.parametrize('change', ['context_extra', 'recovery_id', 'terminal_status', 'terminal_base', 'terminal_task', 'human_binding', 'human_type'])
def test_terminal_context_binding_is_exact(fixture, change):
    context = fixture['context']
    if change == 'context_extra':
        context['allow_retry'] = True
    elif change == 'recovery_id':
        context['recovery_id'] = stable_hash('other recovery')
    elif change == 'human_binding':
        context['human_confirmation_ref'] = {'path': context['human_confirmation_ref']['path'], 'sha256': stable_hash('other')}
    elif change == 'human_type':
        context['human_confirmation_ref'] = None
    else:
        terminal = deepcopy(fixture['terminal'])
        key = {'terminal_status': 'status', 'terminal_base': 'base_authorization_identity', 'terminal_task': 'task_id'}[change]
        terminal[key] = 'COMPLETED' if change == 'terminal_status' else stable_hash('other')
        terminal['identity'] = stable_hash({key: value for key, value in terminal.items() if key != 'identity'})
        context['parent_terminal_ref'] = _write(Path(context['parent_terminal_ref']['path']), terminal)
    with pytest.raises((ValueError, PermissionError)):
        _summary(fixture)


@pytest.mark.parametrize('changes', [
    {'verification_jobs_delta': 2}, {'verification_jobs_delta': True},
    {'effective_verification_jobs_from': 6}, {'maximum_additional_report_recoveries': 2},
    {'memory_mib': 4096}, {'worker_seconds': 901}, {'recovery_total_seconds': 28801},
    {'account_replay_permitted': True}, {'old_records_immutable': False},
    {'strategy_threshold_changes_permitted': True}, {'stage': 'CONFIRMATION'},
    {'expires_at': '2030-01-01T00:00:00+00:00'}, {'project_model_api_calls': 1},
    {'controlled_boundary_settlement_authorized': False}, {'exact_source_version_recovery_authorized': False},
])
def test_repinned_human_evidence_cannot_expand_approved_scope(fixture, changes):
    _repin_human(fixture, **changes)
    with pytest.raises(PermissionError, match='HUMAN_SCOPE_CONFLICT'):
        _summary(fixture)


def test_both_original_and_new_owner_approval_required(fixture):
    summary = _summary(fixture)
    policy = fixture['base']['scope_policy']
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_BINDING_INVALID'):
        bridges.ReportRecoveryCapabilityBridgeV2.install(fixture['root'], fixture['base'], summary,
            approval_ref=policy['approval_ref'])
    (fixture['approvals'].directory / (policy['approval_ref']['approval_id'] + '.json')).unlink()
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_NOT_REGISTERED'):
        _summary(fixture)


@pytest.mark.parametrize('change', ['record', 'owner', 'reference', 'archive', 'terminal', 'human', 'proposal'])
def test_read_rechecks_every_frozen_proof(fixture, change):
    _, reference = _install(fixture)
    if change == 'owner':
        (fixture['approvals'].directory / (reference['approval_ref']['approval_id'] + '.json')).unlink()
    elif change == 'reference':
        reference['sha256'] = stable_hash('wrong reference')
    else:
        path = {'record': reference['path'], 'archive': fixture['replacements'][0]['archive_ref']['path'],
            'terminal': fixture['context']['parent_terminal_ref']['path'],
            'human': fixture['context']['human_confirmation_ref']['path'],
            'proposal': fixture['context']['proposal_ref']['path']}[change]
        Path(path).write_bytes(b'changed frozen evidence')
    with pytest.raises((ValueError, PermissionError)):
        bridges.ReportRecoveryCapabilityBridgeV2.read(fixture['root'], fixture['base'], reference=reference)


def test_registered_v2_is_preferred_and_corruption_cannot_fallback_to_v1(fixture, monkeypatch):
    _, reference = _install(fixture)
    def forbidden(*args, **kwargs):
        pytest.fail('registered V2 must never fall back to inputs-only V1')
    monkeypatch.setattr(old_bridges.ReportRepairCapabilityBridgeV1, 'read', forbidden)
    assert validate_scope_policy(fixture['root'], fixture['base']) == fixture['base']['scope_policy']
    Path(reference['path']).write_bytes(b'{}')
    with pytest.raises((ValueError, PermissionError)):
        fixture['campaign'].peek_status()


def test_bridge_root_and_original_authorization_cannot_be_changed(fixture):
    _, reference = _install(fixture)
    changed = deepcopy(fixture['base'])
    changed['resource_limits']['verification_jobs'] += 1
    with pytest.raises((ValueError, PermissionError)):
        bridges.ReportRecoveryCapabilityBridgeV2.read(fixture['root'], changed, reference=reference)
    other = fixture['root'] / 'other'
    other.mkdir()
    with pytest.raises(PermissionError, match='ROOT_CONFLICT'):
        bridges.ReportRecoveryCapabilityBridgeV2.read(other, fixture['base'], reference=reference)


def test_same_base_and_live_version_cannot_install_another_approved_context(fixture):
    _, reference = _install(fixture)
    _repin_human(fixture, user_reply='另一份合成文字')
    with pytest.raises(PermissionError, match='OWNER_RECORD_IDENTITY_CONFLICT'):
        _install(fixture)
    assert Path(reference['path']).is_file()


def test_replacement_order_does_not_change_content_addressed_summary(fixture):
    expected = _summary(fixture)
    fixture['replacements'].reverse()
    assert _summary(fixture) == expected


@pytest.mark.parametrize('field', ['parent_terminal_ref', 'proposal_ref', 'human_confirmation_ref'])
def test_malformed_context_references_fail_closed(fixture, field):
    fixture['context'][field] = {'path': '.', 'sha256': 'INVALID', 'extra': True}
    with pytest.raises((PermissionError, ValueError)):
        _summary(fixture)


def test_metadata_reference_cannot_return_an_array(fixture):
    path = fixture['root'] / 'reports' / 'not-an-object.json'
    reference = _write(path, [])
    with pytest.raises(PermissionError, match='EVIDENCE_OBJECT_REQUIRED'):
        bridges._read(reference, fixture['root'])
