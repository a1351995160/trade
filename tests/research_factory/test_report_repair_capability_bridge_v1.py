"""纯合成的维护者版本桥；不读真实研究或执行任何行情/账户。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory import continuous_research_contract_v1 as contracts
from chanlun_trader.research_factory import report_repair_capability_bridge_v1 as bridges
from chanlun_trader.research_factory.campaign_scope_v1 import (
    OwnerApprovalStoreV1, bind_campaign_scope, scope_summary, validate_scope_policy,
)
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.run_budget import AutonomousRunBudgetV2
from test_continuous_research_contract_v1 import build


def _fingerprint(value):
    value['fingerprint'] = stable_hash({key: item for key, item in value.items() if key != 'fingerprint'})
    return value


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    current = bridges.capabilities()
    source = tmp_path / bridges.SOURCE_PATH
    source.parent.mkdir(parents=True)
    source.write_bytes(Path(bridges.__file__).with_name(bridges.SOURCE_NAME).read_bytes())
    archived = tmp_path / 'reports' / 'source-archive' / 'original_inputs.py'
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b'original synthetic implementation\n')
    old_hash = hashlib.sha256(archived.read_bytes()).hexdigest()
    old = deepcopy(current)
    old['full_universe']['source_hashes'][bridges.SOURCE_NAME] = old_hash
    _fingerprint(old)
    monkeypatch.setattr(contracts, 'capabilities', lambda: deepcopy(old))
    contract = build(old)
    units = dict.fromkeys(AutonomousRunBudgetV2.resource_names, 100)
    stages = {stage: {name: amount // 2 for name, amount in units.items()}
              for stage in ('EXPLORATION', 'CONFIRMATION')}
    authorization = {'authorization_id': 'repair_campaign', 'objective_id': contract['objective_id'],
        'resource_limits': units, 'stages': list(stages),
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        'max_batches': 2, 'max_total_predictive_trials': 2, 'max_trials_per_batch': 2,
        'max_hypotheses_per_batch': 2, 'max_candidates_per_batch': 2,
        'execution_profiles': contract['scope']['execution_profiles']}
    capability = object()
    approvals = OwnerApprovalStoreV1(tmp_path / 'protected_owner', owner_capability=capability)
    summary = scope_summary(tmp_path, authorization, contract, stages)
    ref = approvals.approve(summary, approver='synthetic_owner', capability=capability)
    bound = bind_campaign_scope(tmp_path, authorization, contract=contract, stage_limits=stages,
        approvals=approvals, approval_ref=ref)
    campaign = ResearchCampaignV1.create(tmp_path, bound)
    base = campaign.peek_status()['base_authorization']
    monkeypatch.setattr(contracts, 'capabilities', lambda: deepcopy(current))
    monkeypatch.setattr(bridges, 'capabilities', lambda: deepcopy(current))
    replacements = [{'path': str(source), 'old_sha256': old_hash,
        'new_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'archive_ref': {'path': str(archived), 'sha256': old_hash}}]
    return {'root': tmp_path, 'campaign': campaign, 'base': base, 'capability': capability,
        'approvals': approvals, 'replacements': replacements, 'old': old, 'current': current}


def _summary(fixture):
    return bridges.ReportRepairCapabilityBridgeV1.summary(fixture['root'], fixture['base'],
        source_replacements=fixture['replacements'])


def _install(fixture):
    summary = _summary(fixture)
    approval = fixture['approvals'].approve(summary, approver='synthetic_owner', capability=fixture['capability'])
    ref = bridges.ReportRepairCapabilityBridgeV1.install(fixture['root'], fixture['base'], summary, approval_ref=approval)
    return summary, ref


def test_changed_capability_without_owner_bridge_still_fails(fixture):
    with pytest.raises(ValueError, match='CONTINUOUS_CAPABILITIES_CHANGED_NEW_VERSION_REQUIRED'):
        fixture['campaign'].peek_status()
    assert not (fixture['approvals'].directory / 'report_repair_capability_bridges').exists()


def test_approved_bridge_preserves_original_authorization_budget_and_old_snapshot(fixture):
    campaign = fixture['campaign']
    original = {path: path.read_bytes() for path in (
        campaign.authorization_path, campaign.budget_path, campaign.budget_path.with_name('run_budget_events.jsonl'))}
    summary, reference = _install(fixture)
    captured = bridges.ReportRepairCapabilityBridgeV1.read(fixture['root'], fixture['base'], reference=reference)
    assert captured['capabilities_snapshot'] == fixture['old']
    assert captured['record']['summary'] == summary
    assert captured['reference'] == reference
    assert summary['original_capabilities_fingerprint'] == fixture['old']['fingerprint']
    assert summary['live_capabilities_fingerprint'] == fixture['current']['fingerprint']
    assert campaign.peek_status()['base_authorization'] == fixture['base']
    assert validate_scope_policy(fixture['root'], fixture['base']) == fixture['base']['scope_policy']
    assert _install(fixture)[1] == reference
    assert all(path.read_bytes() == raw for path, raw in original.items())


@pytest.mark.parametrize('change', ['feature', 'other_source', 'data', 'other_long_source'])
def test_bridge_rejects_every_change_outside_the_one_input_hash(fixture, monkeypatch, change):
    snapshot = deepcopy(fixture['current'])
    if change == 'feature':
        snapshot['features'][0]['public_entry'] = False
    elif change == 'other_source':
        snapshot['full_universe']['source_hashes']['universe_submission_v1.py'] = stable_hash('changed')
    elif change == 'other_long_source':
        snapshot['long_horizon']['source_hashes']['universe_account_backend_v2.py'] = stable_hash('changed')
    else:
        snapshot['data'] = {'status': 'OTHER'}
    _fingerprint(snapshot)
    monkeypatch.setattr(bridges, 'capabilities', lambda: snapshot)
    with pytest.raises(PermissionError, match='OTHER_CAPABILITIES_CHANGED'):
        _summary(fixture)


@pytest.mark.parametrize('change', ['empty', 'extra', 'path', 'old_hash', 'new_hash', 'archive_hash'])
def test_source_allowlist_and_archive_identity_are_exact(fixture, change):
    entries = fixture['replacements']
    if change == 'empty':
        entries.clear()
    elif change == 'extra':
        entries.append(deepcopy(entries[0]))
    elif change == 'path':
        entries[0]['path'] = str(fixture['root'] / 'src/other.py')
    elif change == 'old_hash':
        entries[0]['old_sha256'] = stable_hash('different original')
    elif change == 'new_hash':
        entries[0]['new_sha256'] = stable_hash('different live')
    else:
        entries[0]['archive_ref']['sha256'] = stable_hash('different archive')
    with pytest.raises((PermissionError, ValueError)):
        _summary(fixture)


def test_bridge_requires_both_original_and_new_owner_approval(fixture):
    summary = _summary(fixture)
    policy = fixture['base']['scope_policy']
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_BINDING_INVALID'):
        bridges.ReportRepairCapabilityBridgeV1.install(fixture['root'], fixture['base'], summary,
            approval_ref=policy['approval_ref'])
    (fixture['approvals'].directory / (policy['approval_ref']['approval_id'] + '.json')).unlink()
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_NOT_REGISTERED'):
        _summary(fixture)


@pytest.mark.parametrize('change', ['expiry', 'root', 'cash', 'budget', 'campaign_id'])
def test_bridge_cannot_change_scope_or_extend_authorization(fixture, change):
    _, reference = _install(fixture)
    base = deepcopy(fixture['base'])
    if change == 'expiry':
        base['expires_at'] = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    elif change == 'root':
        base['root'] = str(fixture['root'] / 'other')
    elif change == 'cash':
        base['scope_policy']['summary']['contract']['scope']['initial_cash'] = 1000000
    elif change == 'campaign_id':
        base['authorization_id'] = 'different_campaign'
    else:
        base['resource_limits']['verification_jobs'] += 1
    with pytest.raises((PermissionError, ValueError)):
        bridges.ReportRepairCapabilityBridgeV1.read(fixture['root'], base, reference=reference)
    assert fixture['campaign'].peek_status()['base_authorization'] == fixture['base']


@pytest.mark.parametrize('change', ['archive', 'live', 'record', 'owner', 'reference'])
def test_registered_bridge_rechecks_files_owner_and_explicit_reference(fixture, change):
    _, reference = _install(fixture)
    if change == 'archive':
        Path(fixture['replacements'][0]['archive_ref']['path']).write_bytes(b'changed archive')
    elif change == 'live':
        Path(fixture['replacements'][0]['path']).write_bytes(b'changed live')
    elif change == 'record':
        Path(reference['path']).write_text('{}', encoding='utf-8')
    elif change == 'owner':
        (fixture['approvals'].directory / (reference['approval_ref']['approval_id'] + '.json')).unlink()
    else:
        reference['sha256'] = stable_hash('different reference')
    with pytest.raises((PermissionError, ValueError)):
        bridges.ReportRepairCapabilityBridgeV1.read(fixture['root'], fixture['base'], reference=reference)


def test_immutable_binding_rejects_a_second_approved_archive_reference(fixture):
    _install(fixture)
    old = Path(fixture['replacements'][0]['archive_ref']['path'])
    other = old.with_name('second_archive.py')
    other.write_bytes(old.read_bytes())
    fixture['replacements'][0]['archive_ref']['path'] = str(other)
    with pytest.raises(PermissionError, match='OWNER_RECORD_IDENTITY_CONFLICT'):
        _install(fixture)


def test_recomputed_record_cannot_replace_owner_approval(fixture):
    _, reference = _install(fixture)
    record_path = Path(reference['path'])
    record = json.loads(record_path.read_text(encoding='utf-8'))
    record['summary']['allowed_action'] = 'ANY_SOURCE_CHANGE'
    record['identity'] = stable_hash({key: value for key, value in record.items() if key != 'identity'})
    record_path.write_text(json.dumps(record), encoding='utf-8')
    with pytest.raises(ValueError, match='RECORD_CHANGED'):
        bridges.ReportRepairCapabilityBridgeV1.read(fixture['root'], fixture['base'], reference=reference)


def test_summary_is_read_only_and_rejects_an_invented_live_fingerprint(fixture, monkeypatch):
    files = {path: path.read_bytes() for path in fixture['root'].rglob('*') if path.is_file()}
    _summary(fixture)
    assert {path: path.read_bytes() for path in fixture['root'].rglob('*') if path.is_file()} == files
    changed = deepcopy(fixture['current'])
    changed['fingerprint'] = fixture['old']['fingerprint']
    monkeypatch.setattr(bridges, 'capabilities', lambda: changed)
    with pytest.raises(PermissionError, match='CURRENT_SNAPSHOT_INVALID'):
        _summary(fixture)


def test_installed_bridge_cannot_cover_a_later_unapproved_capability_change(fixture, monkeypatch):
    _, reference = _install(fixture)
    changed = deepcopy(fixture['current'])
    changed['long_horizon']['source_hashes']['universe_account_backend_v2.py'] = stable_hash('another repair')
    _fingerprint(changed)
    monkeypatch.setattr(bridges, 'capabilities', lambda: changed)
    monkeypatch.setattr(contracts, 'capabilities', lambda: changed)
    with pytest.raises(ValueError, match='CONTINUOUS_CAPABILITIES_CHANGED_NEW_VERSION_REQUIRED'):
        bridges.ReportRepairCapabilityBridgeV1.read(fixture['root'], fixture['base'], reference=reference)
    with pytest.raises(ValueError, match='CONTINUOUS_CAPABILITIES_CHANGED_NEW_VERSION_REQUIRED'):
        fixture['campaign'].peek_status()


def test_bridge_is_bound_to_original_root_not_just_the_same_campaign_name(fixture):
    _, reference = _install(fixture)
    other = fixture['root'] / 'another_root'
    other.mkdir()
    with pytest.raises(PermissionError, match='ROOT_CONFLICT'):
        bridges.ReportRepairCapabilityBridgeV1.read(other, fixture['base'], reference=reference)
