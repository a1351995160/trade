"""私有读取视图的容量回退、返回截止与协作截止反例；不签发真实能力。"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory import account_purpose_time_extension_v1 as extension
from chanlun_trader.research_factory import secure_file_reference_v1 as secure


@pytest.mark.parametrize('late_stage', ('return_hash', 'owner'))
@pytest.mark.parametrize('mode', ('for_install', 'for_dispatch', 'historical'))
def test_final_io_cannot_return_expired_execution_context(tmp_path, monkeypatch, late_stage, mode):
    deadline = datetime(2030, 1, 2, tzinfo=timezone.utc)
    clock = {'now': deadline - timedelta(seconds=1)}
    path = tmp_path / 'source.py'
    path.write_bytes(b'original')
    context = SimpleNamespace(summary_value={'approval_store': str(tmp_path)},
        approval_ref={'approval_id': 'artificial'}, execution_expires_at=deadline.isoformat())
    observed = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock['now']

    def require(reference, summary):
        observed.append('owner')
        if late_stage == 'owner':
            clock['now'] = deadline

    original_verify = extension._VerificationReadView.verify_reused_files

    def verify(view):
        original_verify(view)
        observed.append('return_hash')
        if late_stage == 'return_hash':
            clock['now'] = deadline

    monkeypatch.setattr(extension, 'datetime', Clock)
    monkeypatch.setattr(extension, 'OwnerApprovalStoreV1', lambda directory: SimpleNamespace(require_json=require))
    monkeypatch.setattr(extension._VerificationReadView, 'verify_reused_files', verify)

    @extension._verified_recovery_reader
    def reader(*, for_install=False, for_dispatch=False):
        assert clock['now'] < deadline
        digest = extension.file_sha256(path, root=tmp_path, error_code='SOURCE_CHANGED')
        assert extension.file_sha256(path, root=tmp_path, error_code='SOURCE_CHANGED') == digest
        return context

    if mode == 'historical':
        assert reader() is context
    else:
        with pytest.raises(PermissionError, match='FAILED_ACCOUNT_RECOVERY_EXPIRED'):
            reader(**{mode: True})
    assert observed == ['return_hash', 'owner']
    assert extension._VERIFICATION_READ_VIEW.get() is None


def test_large_file_digest_does_not_keep_raw_bytes_and_fallback_still_verifies(tmp_path, monkeypatch):
    path = tmp_path / 'large.bin'
    raw = b'x' * (extension._VERIFICATION_RAW_LIMIT + 1)
    path.write_bytes(raw)
    original = secure._regular_reader
    opened = []

    def counted(*args, **kwargs):
        opened.append(Path(args[0]))
        return original(*args, **kwargs)

    monkeypatch.setattr(secure, '_regular_reader', counted)
    with extension._verification_read_scope() as view:
        expected = extension.file_sha256(path, root=tmp_path, error_code='LARGE_CHANGED')
        assert extension.file_sha256(path, root=tmp_path, error_code='LARGE_CHANGED') == expected
        assert extension.read_file_bytes(path, root=tmp_path, error_code='LARGE_CHANGED') == raw
        assert extension.read_file_bytes(path, root=tmp_path, error_code='LARGE_CHANGED') == raw
        assert view.raw_bytes == 0
        assert all(record['raw'] is None for record in view.files.values())
    assert len(opened) == 4


def test_total_raw_capacity_falls_back_without_skipping_return_hash(tmp_path, monkeypatch):
    raw = b'x' * extension._VERIFICATION_RAW_LIMIT
    count = extension._VERIFICATION_RAW_TOTAL // len(raw) + 1
    paths = [tmp_path / (str(number) + '.bin') for number in range(count)]
    for path in paths:
        path.write_bytes(raw)
    original = secure._regular_reader
    opened = []

    def counted(*args, **kwargs):
        opened.append(Path(args[0]))
        return original(*args, **kwargs)

    monkeypatch.setattr(secure, '_regular_reader', counted)
    with extension._verification_read_scope() as view:
        for path in paths:
            extension.file_sha256(path, root=tmp_path, error_code='CAPACITY_CHANGED')
            assert extension.read_file_bytes(path, root=tmp_path, error_code='CAPACITY_CHANGED') == raw
            assert extension.read_file_bytes(path, root=tmp_path, error_code='CAPACITY_CHANGED') == raw
        assert view.raw_bytes <= extension._VERIFICATION_RAW_TOTAL
        assert sum(record['raw'] is None for record in view.files.values()) == 1
    assert opened.count(paths[-1]) == 3
    assert all(opened.count(path) == 3 for path in paths[:-1])


@pytest.mark.parametrize('stage', ('VERIFICATION_JSON_COPY', 'VERIFICATION_JSON_COPY_COMPLETE'))
def test_json_tree_copy_keeps_cooperative_checks_around_native_codec(monkeypatch, stage):
    from chanlun_trader.account_startup_boundary_v1 import StartupBoundaryExceeded
    observed = []

    def checkpoint(name):
        observed.append(name)
        if name == stage:
            raise StartupBoundaryExceeded(name)

    monkeypatch.setattr(extension, 'startup_checkpoint', checkpoint)
    with pytest.raises(StartupBoundaryExceeded):
        extension._copy_json_tree({'value': [1, 2]})
    assert observed[-1] == stage
