"""文件引用安全边界：合成文件，不访问真实研究资料。"""
import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from chanlun_trader.research.guard import configured_access_authority
from chanlun_trader.research_factory import secure_file_reference_v1 as secure_reference
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


@pytest.mark.parametrize('reader', ['validate', 'bytes', 'hash', 'json'])
def test_outside_task_root_is_rejected_before_any_filesystem_probe(tmp_path, monkeypatch, reader):
    root = tmp_path / 'task'
    outside = tmp_path / 'task-other' / 'checkpoint.json'

    def forbidden(*_args, **_kwargs):
        pytest.fail('out-of-root reference must not probe the filesystem')

    monkeypatch.setattr(Path, 'resolve', forbidden)
    monkeypatch.setattr(Path, 'lstat', forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    functions = {
        'validate': lambda: validated_reference_path(outside, error_code=CODE, root=root),
        'bytes': lambda: read_file_bytes(outside, error_code=CODE, root=root),
        'hash': lambda: file_sha256(outside, error_code=CODE, root=root),
        'json': lambda: read_pinned_json({'path': str(outside), 'sha256': '0' * 64},
                                       error_code=CODE, root=root),
    }
    with pytest.raises(ValueError, match='^' + CODE + '$'):
        functions[reader]()


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


@pytest.mark.parametrize('component', ['parent', 'file'])
@pytest.mark.parametrize('link_type', ['symlink', 'reparse'])
@pytest.mark.parametrize('bounded', [False, True])
def test_each_link_component_is_rejected_before_read(tmp_path, monkeypatch, component, link_type, bounded):
    parent = tmp_path / 'parent'
    parent.mkdir()
    target = parent / 'fixed.json'
    target.write_bytes(b'{}')
    linked = parent if component == 'parent' else target
    original_lstat = Path.lstat
    visited = []

    def lstat(path):
        visited.append(path)
        if path == linked:
            return SimpleNamespace(st_mode=stat.S_IFLNK if link_type == 'symlink' else stat.S_IFDIR,
                                   st_file_attributes=0x400 if link_type == 'reparse' else 0)
        return original_lstat(path)

    def forbidden(*_args, **_kwargs):
        pytest.fail('link rejection must not resolve or open the target')

    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(Path, 'resolve', forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    with pytest.raises(ValueError, match='^' + CODE + '$'):
        read_file_bytes(target, error_code=CODE, root=tmp_path if bounded else None)
    assert linked in visited
    if component == 'parent':
        assert target not in visited


@pytest.mark.parametrize('bounded', [False, True])
def test_pending_multilevel_tail_stops_at_first_missing_component(tmp_path, monkeypatch, bounded):
    missing = tmp_path / 'pending'
    target = missing / 'nested' / 'checkpoint.json'
    original_lstat = Path.lstat
    visited = []

    def lstat(path):
        visited.append(path)
        return original_lstat(path)

    def forbidden(*_args, **_kwargs):
        pytest.fail('pending reference must not resolve or open the target')

    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(Path, 'resolve', forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    assert validated_reference_path(target, error_code=CODE, root=tmp_path if bounded else None) == target
    assert visited[-1] == missing
    assert target not in visited


def test_existing_parent_file_cannot_be_treated_as_missing_tail(tmp_path):
    parent = tmp_path / 'not-a-directory'
    parent.write_bytes(b'{}')
    with pytest.raises(ValueError, match='^' + CODE + '$'):
        validated_reference_path(parent / 'checkpoint.json', error_code=CODE, root=tmp_path)


def test_missing_fixed_reference_keeps_private_waiting_cause(tmp_path):
    reference = {'path': str(tmp_path / 'pending' / 'registration.json'), 'sha256': '0' * 64}
    with pytest.raises(ValueError, match='^' + CODE + '$') as error:
        read_pinned_json(reference, error_code=CODE)
    assert isinstance(error.value.__cause__, FileNotFoundError)


def test_opened_file_must_match_checked_object(tmp_path, monkeypatch):
    target, substituted = tmp_path / 'target.json', tmp_path / 'outside.json'
    target.write_bytes(b'allowed')
    substituted.write_bytes(b'not allowed')
    original_open = os.open
    monkeypatch.setattr(os, 'open', lambda _path, flags: original_open(substituted, flags))
    with pytest.raises(ValueError, match='^' + CODE + '$'):
        read_file_bytes(target, error_code=CODE, root=tmp_path)


@pytest.mark.parametrize('bounded', [False, True])
def test_native_open_supports_fixed_unicode_space_path(tmp_path, monkeypatch, bounded):
    owner = tmp_path / '外部 Owner'
    owner.mkdir()
    target = owner / '固定 原件.json'
    target.write_bytes(b'approved')
    original_open = os.open
    opened_paths = []

    def open_native(path, flags):
        assert isinstance(path, str)
        assert path == os.path.normcase(os.path.normpath(str(target)))
        opened_paths.append(path)
        return original_open(path, flags)

    monkeypatch.setattr(os, 'open', open_native)
    assert read_file_bytes(target, error_code=CODE, root=owner if bounded else None) == b'approved'
    assert len(opened_paths) == 1


@pytest.mark.parametrize('bounded', [False, True])
def test_native_representation_cannot_redirect_checked_path_to_prefix_sibling(tmp_path, monkeypatch, bounded):
    owner = tmp_path / 'owner'
    sibling = tmp_path / 'owner-other'
    owner.mkdir()
    sibling.mkdir()
    target, redirected = owner / 'fixed.json', sibling / 'fixed.json'
    target.write_bytes(b'approved')
    redirected.write_bytes(b'outside')
    original_checked, original_normpath = secure_reference.checked_file_path, os.path.normpath
    checked = []

    def checked_file(*args, **kwargs):
        result = original_checked(*args, **kwargs)
        checked.append(result)
        return result

    def normpath(path):
        if str(path) == str(target):
            assert checked == [target]
            return str(redirected)
        return original_normpath(path)

    def forbidden(*_args, **_kwargs):
        pytest.fail('native out-of-root path must be rejected before open')

    monkeypatch.setattr(secure_reference, 'checked_file_path', checked_file)
    monkeypatch.setattr(os.path, 'normpath', normpath)
    monkeypatch.setattr(os, 'open', forbidden)
    with pytest.raises(ValueError, match='^' + CODE + '$'):
        read_file_bytes(target, error_code=CODE, root=owner if bounded else None)
    assert checked == [target]


def test_native_boundary_accepts_drive_root_but_not_equal_root_file(tmp_path, monkeypatch):
    target = tmp_path / 'fixed.json'
    target.write_bytes(b'approved')
    assert read_file_bytes(target, error_code=CODE, root=Path(target.anchor)) == b'approved'

    def forbidden(*_args, **_kwargs):
        pytest.fail('a directory boundary must not be opened as its own file')

    monkeypatch.setattr(os, 'open', forbidden)
    with pytest.raises(ValueError, match='^' + CODE + '$'):
        read_file_bytes(target, error_code=CODE, root=target)


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
