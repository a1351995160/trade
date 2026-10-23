"""纯词法复用不得复用文件系统观察或改变原件安全检查。"""
from pathlib import Path
import os
import stat
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory import secure_file_reference_v1 as secure


def test_warm_lexical_cache_still_checks_every_component_before_and_after_open(tmp_path, monkeypatch):
    target = tmp_path / '中文 Owner' / 'record.json'
    target.parent.mkdir()
    target.write_bytes(b'original')
    original = secure._component_metadata
    observations = []

    def lstat(path):
        observations.append(str(path))
        return original(path)

    monkeypatch.setattr(secure, '_component_metadata', lstat)
    assert secure.read_file_bytes(target, error_code='SAFE', root=tmp_path) == b'original'
    first = observations[:]
    observations.clear()
    assert secure.read_file_bytes(target, error_code='SAFE', root=tmp_path) == b'original'
    assert observations == first
    for component in (*reversed(target.parents), target):
        assert observations.count(str(component)) >= 2


@pytest.mark.parametrize('component', ['parent', 'file'])
@pytest.mark.parametrize('state', ['reparse', 'symlink', 'missing'])
def test_warm_path_cache_does_not_hide_changed_component(tmp_path, monkeypatch, component, state):
    parent = tmp_path / 'parent'
    parent.mkdir()
    target = parent / 'record.json'
    target.write_bytes(b'original')
    assert secure.read_file_bytes(target, error_code='SAFE', root=tmp_path) == b'original'
    changed = parent if component == 'parent' else target
    original = Path.lstat
    reached = []
    if state == 'missing':
        target.unlink()
        if component == 'parent':
            parent.rmdir()

    def lstat(path):
        reached.append(path)
        if path == changed:
            if state == 'missing':
                raise FileNotFoundError
            return SimpleNamespace(st_mode=stat.S_IFLNK if state == 'symlink' else stat.S_IFDIR,
                st_file_attributes=0x400 if state == 'reparse' else 0)
        return original(path)

    def forbidden(*args, **kwargs):
        pytest.fail('changed original must be rejected before open')

    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(secure, '_component_metadata', lstat)
    monkeypatch.setattr(secure.os, 'open', forbidden)
    with pytest.raises(ValueError, match='^SAFE$'):
        secure.read_file_bytes(target, error_code='SAFE', root=tmp_path)
    assert changed in reached


def test_warm_cached_path_does_not_bypass_a_different_root(tmp_path, monkeypatch):
    target = tmp_path / 'allowed.json'
    target.write_bytes(b'original')
    assert secure.read_file_bytes(target, error_code='SAFE', root=tmp_path) == b'original'

    def forbidden(*args, **kwargs):
        pytest.fail('out-of-root cached path must not touch filesystem')

    monkeypatch.setattr(Path, 'lstat', forbidden)
    monkeypatch.setattr(secure, '_component_metadata', forbidden)
    monkeypatch.setattr(secure.os, 'open', forbidden)
    with pytest.raises(ValueError, match='^SAFE$'):
        secure.read_file_bytes(target, error_code='SAFE', root=tmp_path / 'other')


def test_lexical_cache_is_bounded_and_preserves_spelling(tmp_path):
    for number in range(4100):
        path = tmp_path / (str(number) + '.json')
        assert secure._lexical_reference_path(path) == path
        secure._reference_components(str(path))
    assert secure._lexical_path_text.cache_info().currsize <= 4096
    assert secure._reference_components.cache_info().currsize <= 4096
    for name in ('MixedCase.json', 'mixedcase.json'):
        assert str(secure._lexical_reference_path(tmp_path / name)) == str(tmp_path / name)


def test_native_component_query_matches_lstat_and_is_fresh_after_attribute_change(tmp_path):
    target = tmp_path / '中文 original.json'
    target.write_bytes(b'original')
    before = secure._component_metadata(target)
    real = target.lstat()
    assert stat.S_IFMT(before.st_mode) == stat.S_IFMT(real.st_mode)
    assert getattr(before, 'st_file_attributes', 0) == getattr(real, 'st_file_attributes', 0)
    if os.name == 'nt':
        import ctypes
        setter = secure._WINDOWS_FILES.SetFileAttributesW
        setter.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        setter.restype = ctypes.c_int
        try:
            assert setter(str(target), before.st_file_attributes | 1)
            assert secure._component_metadata(target).st_file_attributes & 1
        finally:
            assert setter(str(target), before.st_file_attributes)
    else:
        try:
            target.chmod(0o400)
            assert secure._component_metadata(target).st_mode & 0o777 == 0o400
        finally:
            target.chmod(stat.S_IMODE(real.st_mode))


def test_real_parent_junction_or_symlink_is_rejected_before_open(tmp_path, monkeypatch):
    # E: 可为非 NTFS 工作区；Windows 重解析点用本机临时卷的独占目录实测。
    if os.name == 'nt':
        directory = Path(os.environ['LOCALAPPDATA']) / 'Temp'
        with tempfile.TemporaryDirectory(prefix='recovery-reference-link-', dir=directory) as value:
            root = Path(value)
            assert root.parent == directory and root.name.startswith('recovery-reference-link-')
            _assert_real_parent_link(root, monkeypatch)
    else:
        _assert_real_parent_link(tmp_path, monkeypatch)


def _assert_real_parent_link(tmp_path, monkeypatch):
    target = tmp_path / 'original'
    target.mkdir()
    record = target / 'record.json'
    record.write_bytes(b'original')
    assert secure.read_file_bytes(record, error_code='SAFE', root=tmp_path) == b'original'
    alias = tmp_path / 'alias'
    if os.name == 'nt':
        completed = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(alias), str(target)],
            capture_output=True)
        assert completed.returncode == 0, completed.stderr
        assert alias.lstat().st_file_attributes & 0x400
    else:
        alias.symlink_to(target, target_is_directory=True)
    try:
        def forbidden(*args, **kwargs):
            pytest.fail('reparse parent must be rejected before open')

        monkeypatch.setattr(secure.os, 'open', forbidden)
        with pytest.raises(ValueError, match='^SAFE$'):
            secure.read_file_bytes(alias / 'record.json', error_code='SAFE', root=tmp_path)
    finally:
        if os.name == 'nt':
            assert alias.is_relative_to(tmp_path) and alias.lstat().st_file_attributes & 0x400
            alias.rmdir()
        else:
            alias.unlink()
    assert record.read_bytes() == b'original'


@pytest.mark.parametrize('code,expected', [(2, FileNotFoundError), (3, FileNotFoundError),
    (5, PermissionError), (267, NotADirectoryError)])
def test_component_query_keeps_missing_denied_and_not_directory_errors(tmp_path, monkeypatch, code, expected):
    if os.name == 'nt':
        import ctypes

        def failed(path):
            ctypes.set_last_error(code)
            return 0xffffffff

        monkeypatch.setattr(secure, '_WINDOWS_ATTRIBUTES', failed)
    else:
        def failed(path):
            raise expected('declared metadata failure')

        monkeypatch.setattr(Path, 'lstat', failed)
    with pytest.raises(expected):
        secure._component_metadata(tmp_path)
