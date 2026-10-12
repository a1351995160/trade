"""能力桥精确旧链、新源码闭包与原 Owner 身份的合成反例。"""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from chanlun_trader.research_factory import account_version_capability_bridge_v1 as bridges
from chanlun_trader.research_factory import account_version_continuation_v1 as continuation
from chanlun_trader.research_factory.campaign_scope_v1 import OwnerApprovalStoreV1
from chanlun_trader.research_factory.common import stable_hash
from test_account_version_continuation_v1 import case, install, pin, summary, write


def test_checked_owner_reader_preserves_approval_without_resolve_or_write_capability(tmp_path, monkeypatch):
    owner = tmp_path.resolve() / 'protected_owner'; owner.mkdir()
    token = object(); writer = OwnerApprovalStoreV1(owner, owner_capability=token)
    value = {'synthetic_owner_scope': 'exact'}
    reference = writer.approve(value, approver='synthetic_owner', capability=token)

    def reject_resolve(*args, **kwargs):
        pytest.fail('已检查目录的只读入口不得再次 resolve')

    monkeypatch.setattr(Path, 'resolve', reject_resolve)
    reader = OwnerApprovalStoreV1.from_checked_directory(owner)
    assert reader.directory == writer.directory
    assert reader.require(reference, value)['approval_id'] == reference['approval_id']
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_REQUIRED'):
        reader.approve(value, approver='synthetic_owner', capability=token)
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_BINDING_INVALID'):
        reader.require(reference, {'synthetic_owner_scope': 'different'})


@pytest.mark.parametrize('kind', ['string', 'relative', 'traversal', 'file', 'missing'])
def test_checked_owner_reader_rejects_unchecked_or_invalid_directory(tmp_path, kind):
    root = tmp_path.resolve()
    source = root / 'regular_file'; source.write_text('not a directory', encoding='utf-8')
    value = {'string': str(root), 'relative': Path('relative-owner'),
        'traversal': root / '..' / root.name, 'file': source, 'missing': root / 'missing'}[kind]
    with pytest.raises(ValueError, match='OWNER_APPROVAL_CHECKED_DIRECTORY_'):
        OwnerApprovalStoreV1.from_checked_directory(value)


def test_checked_owner_reader_rejects_reparse_directory(tmp_path, monkeypatch):
    from types import SimpleNamespace
    owner = tmp_path.resolve(); original_stat = Path.lstat

    def reparse_stat(path):
        metadata = original_stat(path)
        return SimpleNamespace(st_mode=metadata.st_mode, st_file_attributes=0x400) if path == owner else metadata

    monkeypatch.setattr(Path, 'lstat', reparse_stat)
    with pytest.raises(ValueError, match='OWNER_APPROVAL_CHECKED_DIRECTORY_INVALID'):
        OwnerApprovalStoreV1.from_checked_directory(owner)


def test_legacy_owner_constructor_still_resolves_directory(tmp_path, monkeypatch):
    logical = Path('legacy-relative-owner'); canonical = tmp_path.resolve()
    resolved = []

    def legacy_resolve(path):
        resolved.append(path)
        return canonical

    monkeypatch.setattr(Path, 'resolve', legacy_resolve)
    token = object(); store = OwnerApprovalStoreV1(logical, owner_capability=token)
    assert resolved == [logical] and store.directory == canonical and store._owner_capability is token


@pytest.mark.parametrize('case', [True], indirect=True)
def test_two_approved_capability_versions_resolve_to_original_contract(case):
    result = bridges.AccountVersionCapabilityBridgeV1.read(case['root'], case['base'], reference=case['bridge_ref'])
    assert result['capabilities_snapshot'] == case['old']
    assert result['record']['summary']['predecessor_bridge_ref'] == case['predecessor_ref']
    assert result['record']['summary']['previous_capabilities_fingerprint'] != case['old']['fingerprint']
    assert install(case)[0].proof()['original_job_sha256'] == pin(case['gov'].root / 'JOB.json')['sha256']


def test_closed_source_allowlist_and_whole_capability_snapshot(case, monkeypatch):
    value = case['bridge_summary']
    for change in ('other_source', 'other_policy', 'fingerprint'):
        altered = deepcopy(case['current'])
        if change == 'other_source':
            altered['long_horizon']['source_hashes']['universe_signal_scan_v2.py'] = stable_hash('unapproved')
        elif change == 'other_policy':
            altered['full_universe']['selection_policy'] = 'UNAPPROVED_SELECTION'
        else:
            altered['fingerprint'] = stable_hash('invented')
        if change != 'fingerprint':
            bridges._fingerprint(altered)
        with monkeypatch.context() as patch:
            patch.setattr(bridges, 'capabilities', lambda: altered)
            with pytest.raises((ValueError, PermissionError)):
                bridges.AccountVersionCapabilityBridgeV1.summary(case['root'], case['base'],
                    source_replacements=value['source_replacements'], runtime_source_refs=value['runtime_sources'])
    changes = deepcopy(value['source_replacements'])
    changes[0]['path'] = str(case['root'] / 'src' / 'unapproved_algorithm.py')
    with pytest.raises((ValueError, PermissionError)):
        bridges.AccountVersionCapabilityBridgeV1.summary(case['root'], case['base'],
            source_replacements=changes, runtime_source_refs=value['runtime_sources'])
    with pytest.raises(PermissionError, match='PROTOCOL_SOURCE_PIN_REQUIRED'):
        bridges.AccountVersionCapabilityBridgeV1.summary(case['root'], case['base'],
            source_replacements=value['source_replacements'], runtime_source_refs=[])


def test_same_source_version_cannot_install_another_approved_archive_identity(case):
    value = deepcopy(case['bridge_summary'])
    archive = Path(value['source_replacements'][0]['archive_ref']['path'])
    other = archive.parent / 'other_archive.py'; other.write_bytes(archive.read_bytes())
    value['source_replacements'][0]['archive_ref'] = pin(other)
    approval = case['approvals'].approve(value, approver='synthetic_owner', capability=case['token'])
    with pytest.raises(PermissionError, match='OWNER_RECORD_IDENTITY_CONFLICT'):
        bridges.AccountVersionCapabilityBridgeV1.install(case['root'], case['base'], value, approval_ref=approval)


def test_three_committed_feature_receipts_are_reused_and_tampering_fails(case):
    folder = case['gov'].root / 'SYNTHETIC_BASE_FEATURES'; folder.mkdir()
    array = np.zeros((3, 2, 4), dtype='float64'); np.save(folder / 'CONDITIONS.npy', array)
    identity = stable_hash('synthetic-feature-cache')
    write(folder / 'PREPARATION_BINDING.json', {'identity': identity})
    import hashlib
    receipt = write(folder / '000001.SZ.json', {'identity': identity, 'preparation': [],
        'array_hash': hashlib.sha256(array[0].tobytes()).hexdigest()})
    for index, symbol in enumerate(('000002.SZ', '000004.SZ'), 1):
        write(folder / (symbol + '.json'), {'identity': identity, 'preparation': [],
            'array_hash': hashlib.sha256(array[index].tobytes()).hexdigest()})
    ctx, _, _ = install(case)
    assert len(ctx.summary_value['accounts']['SYNTHETIC_BASE']['features']['receipts']) == 3
    assert ctx.summary_value['accounts']['SYNTHETIC_BASE']['features']['receipts'][0]['reference'] == receipt
    values = np.load(folder / 'CONDITIONS.npy', mmap_mode='r+')
    values[0, 0, 0] = 1.; values.flush(); values._mmap.close()
    with pytest.raises(PermissionError, match='FEATURE_RECEIPT_CHANGED'):
        ctx.validate()


def test_expiry_blocks_dispatch_but_keeps_historical_validation(case, monkeypatch):
    ctx, _, _ = install(case)
    after = datetime.fromisoformat(ctx.summary_value['expires_at']) + timedelta(seconds=1)
    class AfterExpiry(datetime):
        @classmethod
        def now(cls, tz=None):
            return after
    monkeypatch.setattr(continuation, 'datetime', AfterExpiry)
    assert ctx.validate(for_dispatch=False).proof() == ctx.proof()
    with pytest.raises(PermissionError, match='EXPIRED'):
        ctx.validate(for_dispatch=True, name='SYNTHETIC_BASE')


def test_missing_original_owner_approval_fails_closed(case):
    path = case['approvals'].directory / (case['base']['scope_policy']['approval_ref']['approval_id'] + '.json')
    path.unlink()
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_NOT_REGISTERED'):
        bridges.AccountVersionCapabilityBridgeV1.read(case['root'], case['base'])


def test_session_alias_preserves_original_reference_and_checks_canonical_owner(case, monkeypatch):
    owner = case['approvals'].directory.resolve()
    canonical = owner / 'campaign_setup' / 'BATCH_002_RUNNER_CONFIG.json'
    canonical.parent.mkdir()
    write(canonical, {'synthetic_config': True})
    logical_root = case['root'] / 'msix_logical_owner'
    logical = logical_root / 'campaign_setup' / canonical.name
    original = {'path': str(logical), 'sha256': pin(canonical)['sha256']}
    resolve = Path.resolve

    def resolve_alias(path, strict=False):
        if path.is_relative_to(logical_root):
            return resolve(owner / path.relative_to(logical_root), strict=strict)
        return resolve(path, strict=strict)

    monkeypatch.setattr(Path, 'resolve', resolve_alias)
    value = continuation.AccountVersionContinuationV1.summary(case['root'],
        authorization_id=case['base']['authorization_id'], task_ref=case['task_ref'],
        capability_bridge_ref=case['bridge_ref'], proposal_ref=case['proposal_ref'],
        ci_ref=case['ci_ref'], session_refs={'config': original})
    approval = case['approvals'].approve(value, approver='synthetic_owner', capability=case['token'])
    reference = continuation.AccountVersionContinuationV1.freeze(case['root'], value, approval_ref=approval)
    ctx = continuation.AccountVersionContinuationV1(case['root'], reference).validate()
    assert ctx.summary_value['session_refs']['config'] == original
    canonical.write_text('{}', encoding='utf-8')
    with pytest.raises(PermissionError, match='SESSION_REFERENCE_CHANGED'):
        ctx.validate()
