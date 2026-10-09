import errno
import json
import os
from pathlib import Path

import pytest

from chanlun_trader.research_factory import universe_execution_state_v2 as state


@pytest.mark.skipif(os.name != 'nt', reason='实际Windows文件共享语义')
def test_windows_reader_release_allows_atomic_checkpoint_commit(tmp_path, monkeypatch):
    path = tmp_path / 'CLOSE.json'
    before = {'last_day': 192, 'cash': 50000}
    after = {'last_day': 193, 'cash': 49999}
    state.write_snapshot(path, before)
    original = path.read_bytes()
    replace = state.os.replace
    failures = []
    with path.open('rb') as reader:
        def replace_after_reader_release(source, target):
            try:
                return replace(source, target)
            except PermissionError as error:
                failures.append(error.winerror)
                assert reader.read() == original
                assert path.read_bytes() == original
                reader.close()
                raise

        with monkeypatch.context() as patch:
            patch.setattr(state.os, 'replace', replace_after_reader_release)
            state.write_snapshot(path, after)
    assert failures and all(code in {5, 32, 33} for code in failures)
    assert json.loads(path.read_bytes()) == after
    assert not list(tmp_path.glob('.close_*'))


@pytest.mark.parametrize('winerror', [5, 32, 33])
def test_transient_windows_replace_reuses_original_complete_temp(tmp_path, monkeypatch, winerror):
    path = tmp_path / 'CLOSE.json'
    state.write_snapshot(path, {'last_day': 192})
    original = path.read_bytes()
    replace = state.os.replace
    attempts = []
    error = PermissionError(errno.EACCES, 'temporary Windows sharing violation')
    error.winerror = winerror

    def shared_replace(source, target):
        attempts.append((source, target, Path(source).read_bytes()))
        if len(attempts) == 1:
            assert path.read_bytes() == original
            raise error
        return replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(state.os, 'replace', shared_replace)
        state.write_snapshot(path, {'last_day': 193})
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert json.loads(path.read_bytes()) == {'last_day': 193}
    assert not list(tmp_path.glob('.close_*'))


def test_persistent_windows_denial_is_bounded_and_keeps_previous_close(tmp_path, monkeypatch):
    path = tmp_path / 'CLOSE.json'
    state.write_snapshot(path, {'last_day': 192})
    original = path.read_bytes()
    elapsed = [0.]
    attempts = []
    error = PermissionError(errno.EACCES, 'persistent access denial')
    error.winerror = 5

    def denied(source, target):
        attempts.append((source, target))
        assert path.read_bytes() == original
        raise error

    with monkeypatch.context() as patch:
        patch.setattr(state.os, 'replace', denied)
        patch.setattr(state.time, 'monotonic', lambda: elapsed[0])
        patch.setattr(state.time, 'sleep', lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
        with pytest.raises(PermissionError) as caught:
            state.write_snapshot(path, {'last_day': 193})
    assert caught.value is error
    assert len(attempts) > 1 and 0 < elapsed[0] <= 1.01
    assert path.read_bytes() == original
    assert not list(tmp_path.glob('.close_*'))


@pytest.mark.parametrize('winerror', [None, 19])
def test_nonsharing_errors_fail_immediately_without_changing_previous_close(tmp_path, monkeypatch, winerror):
    path = tmp_path / 'CLOSE.json'
    state.write_snapshot(path, {'last_day': 192})
    original = path.read_bytes()
    attempts = []
    error = PermissionError(errno.EACCES, 'not a Windows sharing error')
    if winerror is not None:
        error.winerror = winerror

    def denied(source, target):
        attempts.append((source, target))
        raise error

    with monkeypatch.context() as patch:
        patch.setattr(state.os, 'replace', denied)
        with pytest.raises(PermissionError) as caught:
            state.write_snapshot(path, {'last_day': 193})
    assert caught.value is error and len(attempts) == 1
    assert path.read_bytes() == original
    assert not list(tmp_path.glob('.close_*'))
