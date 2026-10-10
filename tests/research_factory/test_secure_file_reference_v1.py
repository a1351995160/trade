"""文件引用安全边界：合成文件，不访问真实研究资料。"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from chanlun_trader.research.guard import configured_access_authority
from chanlun_trader.research_factory.secure_file_reference_v1 import (
    checked_directory_path, file_sha256, read_file_bytes, read_pinned_json, validated_reference_path,
)
from chanlun_trader.research_factory.train_projection_admission_v1 import _pinned_json
from scripts.run_continuous_universe_acceptance_v1 import _digest


CODE = 'SAFE_REFERENCE_UNAVAILABLE'


def pin(path, content):
    raw = json.dumps(content).encode('utf-8')
    path.write_bytes(raw)
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def test_fixed_external_reference_is_legal_but_task_cannot_escape_its_root(tmp_path):
    workspace, owner = tmp_path / 'workspace', tmp_path / 'external-owner'
    workspace.mkdir()
    owner.mkdir()
    reference = pin(owner / 'DEPLOYMENT.json', {'fixed': True})
    assert read_pinned_json(reference, error_code=CODE) == {'fixed': True}
    assert checked_directory_path(owner, error_code=CODE) == owner
    assert file_sha256(reference['path'], error_code=CODE) == reference['sha256']
    assert _digest(reference['path']) == reference['sha256']
    with pytest.raises(ValueError, match='^' + CODE + '$'):
        read_pinned_json(reference, error_code=CODE, root=workspace)
    with pytest.raises(ValueError, match='^ACCEPTANCE_ARTIFACT_REFERENCE_INVALID$'):
        _digest(reference['path'], root=workspace)


@pytest.mark.parametrize('kind', ['missing', 'directory', 'bad_hash', 'invalid_json', 'wrong_shape'])
def test_file_state_does_not_change_public_reference_error(tmp_path, kind):
    reference = pin(tmp_path / 'registered.json', {'fixed': True})
    if kind == 'missing':
        Path(reference['path']).unlink()
    elif kind == 'directory':
        Path(reference['path']).unlink()
        Path(reference['path']).mkdir()
    elif kind == 'bad_hash':
        reference['sha256'] = '0' * 64
    elif kind == 'invalid_json':
        Path(reference['path']).write_bytes(b'not json')
        reference['sha256'] = hashlib.sha256(b'not json').hexdigest()
    else:
        reference['extra'] = True
    with pytest.raises(ValueError, match='^' + CODE + '$'):
        read_pinned_json(reference, error_code=CODE)
    with pytest.raises(ValueError, match='^TRAIN_ADMISSION_REFERENCE_INVALID:test$'):
        _pinned_json(reference, 'test')


@pytest.mark.parametrize('suffix', ['../outside.json', 'NUL.json', 'COM1.txt', 'file.json:stream', 'trailing.'])
def test_ambiguous_or_device_paths_are_rejected_before_read(tmp_path, suffix):
    path = str(tmp_path) + os.sep + suffix
    with pytest.raises(ValueError, match='^' + CODE + '$'):
        validated_reference_path(path, error_code=CODE)


def test_relative_path_and_bad_hash_are_rejected_without_filesystem_probe(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail('invalid reference must not probe the filesystem')
    monkeypatch.setattr(Path, 'resolve', forbidden)
    with pytest.raises(ValueError, match='^' + CODE + '$'):
        validated_reference_path('relative.json', error_code=CODE)
    with pytest.raises(ValueError, match='^' + CODE + '$'):
        read_pinned_json({'path': '/somewhere/secret.json', 'sha256': 'invalid'}, error_code=CODE)


def test_file_and_parent_symlinks_are_rejected(tmp_path):
    original, alias = tmp_path / 'original', tmp_path / 'alias'
    original.mkdir()
    reference = pin(original / 'fixed.json', {'fixed': True})
    try:
        alias.symlink_to(original, target_is_directory=True)
        (tmp_path / 'file-alias.json').symlink_to(reference['path'])
    except OSError:
        pytest.skip('当前 Windows 未授予测试创建符号链接的权限')
    for path in (alias / 'fixed.json', tmp_path / 'file-alias.json'):
        with pytest.raises(ValueError, match='^' + CODE + '$'):
            read_file_bytes(path, error_code=CODE)


def test_opened_file_must_match_checked_object(tmp_path, monkeypatch):
    target, substituted = tmp_path / 'target.json', tmp_path / 'outside.json'
    target.write_bytes(b'allowed')
    substituted.write_bytes(b'not allowed')
    original_open = os.open
    monkeypatch.setattr(os, 'open', lambda _path, flags: original_open(substituted, flags))
    with pytest.raises(ValueError, match='^' + CODE + '$'):
        read_file_bytes(target, error_code=CODE, root=tmp_path)


def test_json_size_bound_and_pending_absolute_reference(tmp_path):
    path = tmp_path / 'pending.json'
    assert validated_reference_path(path, error_code=CODE) == path
    reference = pin(path, {'fixed': True})
    with pytest.raises(ValueError, match='^' + CODE + '$'):
        read_pinned_json(reference, error_code=CODE, maximum_bytes=1)


@pytest.mark.parametrize('state', ['missing', 'regular_file'])
def test_owner_store_invalid_states_have_same_error(tmp_path, state):
    store = tmp_path / 'external-owner'
    if state == 'regular_file':
        store.write_text('not a directory', encoding='utf-8')
    reference = pin(tmp_path / 'DEPLOYMENT.json', {
        'schema_version': 'TRUSTED_RESEARCH_DATA_DEPLOYMENT_V1',
        'owner_approval_store_root': str(store), 'records': {}})
    with pytest.raises(ValueError, match='^TRUSTED_DATA_APPROVAL_STORE_INVALID$'):
        configured_access_authority(reference['path'], expected_sha256=reference['sha256'])


def test_fixed_external_owner_directory_remains_supported(tmp_path):
    workspace, owner = tmp_path / 'workspace', tmp_path / 'external-owner'
    workspace.mkdir()
    owner.mkdir()
    reference = pin(workspace / 'DEPLOYMENT.json', {
        'schema_version': 'TRUSTED_RESEARCH_DATA_DEPLOYMENT_V1',
        'owner_approval_store_root': str(owner), 'records': {}})
    authority = configured_access_authority(reference['path'], expected_sha256=reference['sha256'])
    assert authority._deployment_identity == reference
