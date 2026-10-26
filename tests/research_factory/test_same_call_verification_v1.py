"""单次只读核验复用；原件、下一次调用及执行边界不能继承缓存。"""
from pathlib import Path
import json

import pytest

from chanlun_trader.research_factory import account_purpose_time_extension_v1 as extension
from chanlun_trader.research_factory import secure_file_reference_v1 as secure


def test_same_call_reads_hashes_and_pinned_json_share_bytes(tmp_path, monkeypatch):
    path = tmp_path / 'evidence.json'
    path.write_bytes(b'{"value": [1, 2]}')
    original = secure._regular_reader
    opened = []

    def counted(*args, **kwargs):
        opened.append(Path(args[0]))
        return original(*args, **kwargs)

    monkeypatch.setattr(secure, '_regular_reader', counted)
    with extension._verification_read_scope():
        digest = extension.file_sha256(path, root=tmp_path, error_code='PIN_CHANGED')
        pin = {'path': str(path), 'sha256': digest}
        first = extension.read_pinned_json(pin, root=tmp_path, error_code='PIN_CHANGED')
        first['value'].append(3)
        assert extension.read_pinned_json(pin, root=tmp_path, error_code='PIN_CHANGED') == {'value': [1, 2]}
        assert extension.read_file_bytes(path, root=tmp_path, error_code='PIN_CHANGED') == path.read_bytes()
        assert len(opened) == 2
    # 返回前逐字节复核所有被复用的原件；不是只检查mtime。
    assert len(opened) == 3
    assert extension.read_pinned_json(pin, root=tmp_path, error_code='PIN_CHANGED') == {'value': [1, 2]}
    assert len(opened) == 4


@pytest.mark.parametrize('change', ('bytes', 'same_size_restored_mtime', 'missing'))
def test_return_boundary_rejects_changes_even_after_reuse(tmp_path, change):
    path = tmp_path / 'evidence.json'
    path.write_bytes(b'{"value": 1}')
    metadata = path.stat()
    with pytest.raises(ValueError, match='PIN_CHANGED'):
        with extension._verification_read_scope():
            extension.read_file_bytes(path, root=tmp_path, error_code='PIN_CHANGED')
            expected = extension.file_sha256(path, root=tmp_path, error_code='PIN_CHANGED')
            assert extension.file_sha256(path, root=tmp_path, error_code='PIN_CHANGED') == expected
            if change == 'missing':
                path.unlink()
            else:
                path.write_bytes(b'{"value": 2}')
                if change == 'same_size_restored_mtime':
                    import os
                    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))


def test_next_call_and_nested_call_start_fresh(tmp_path):
    path = tmp_path / 'source.py'
    path.write_bytes(b'old')
    with extension._verification_read_scope():
        first = extension.file_sha256(path, root=tmp_path, error_code='SOURCE_CHANGED')
        path.write_bytes(b'new')
        with extension._verification_read_scope():
            second = extension.file_sha256(path, root=tmp_path, error_code='SOURCE_CHANGED')
            assert second != first
    with extension._verification_read_scope():
        assert extension.file_sha256(path, root=tmp_path, error_code='SOURCE_CHANGED') == second


def test_exception_discards_view_and_does_not_hide_next_mutation(tmp_path):
    path = tmp_path / 'source.py'
    path.write_bytes(b'old')
    with pytest.raises(RuntimeError, match='declared failure'):
        with extension._verification_read_scope():
            old = extension.file_sha256(path, root=tmp_path, error_code='SOURCE_CHANGED')
            raise RuntimeError('declared failure')
    path.write_bytes(b'new')
    assert extension.file_sha256(path, root=tmp_path, error_code='SOURCE_CHANGED') != old


@pytest.mark.parametrize('bad', ('pin', 'root', 'size'))
def test_reuse_preserves_pin_root_size_and_link_checks(tmp_path, monkeypatch, bad):
    path = tmp_path / 'evidence.json'
    path.write_bytes(b'{"value": 1}')
    with extension._verification_read_scope():
        digest = extension.file_sha256(path, root=tmp_path, error_code='EVIDENCE_INVALID')
        pin = {'path': str(path), 'sha256': digest}
        if bad == 'pin':
            pin['sha256'] = '0' * 64
        with pytest.raises(ValueError, match='EVIDENCE_INVALID'):
            extension.read_pinned_json(pin, root=tmp_path / 'elsewhere' if bad == 'root' else tmp_path,
                error_code='EVIDENCE_INVALID', maximum_bytes=1 if bad == 'size' else 1024)


def test_return_boundary_rejects_reparse_replacement_without_opening_it(tmp_path, monkeypatch):
    from types import SimpleNamespace
    path = tmp_path / 'evidence.json'
    path.write_bytes(b'{"value": 1}')
    original = Path.lstat
    cache_returned = []

    def reparse(current, *args, **kwargs):
        value = original(current, *args, **kwargs)
        if current == path:
            return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
        return value

    with pytest.raises(ValueError, match='EVIDENCE_INVALID'):
        with extension._verification_read_scope():
            extension.read_file_bytes(path, root=tmp_path, error_code='EVIDENCE_INVALID')
            digest = extension.file_sha256(path, root=tmp_path, error_code='EVIDENCE_INVALID')
            monkeypatch.setattr(Path, 'lstat', reparse)
            # 命中只使用已验证的旧字节，不打开替换路径；返回前必须拒绝。
            assert extension.read_pinned_json({'path': str(path), 'sha256': digest},
                root=tmp_path, error_code='EVIDENCE_INVALID') == {'value': 1}
            cache_returned.append(True)
    assert cache_returned == [True]


def test_json_event_decoding_is_same_call_only_and_returns_independent_values():
    lines = [b'{"event_type": "A", "payload": {"value": 1}}\n']
    with extension._verification_read_scope():
        first = extension._verification_json_lines(lines)
        first[0]['payload']['value'] = 2
        assert extension._verification_json_lines(lines)[0]['payload']['value'] == 1
    assert extension._verification_json_lines(lines)[0]['payload']['value'] == 1


@pytest.mark.parametrize('lines', (
    [b'{"event": 1},{"event": 2}\n'],
    [b'{"event": 1},{"event": 2}\n', b'{"a": 1\n', b'"b": 2}\n'],
))
def test_ledger_reuse_keeps_one_complete_json_event_per_physical_line(lines):
    # 简单数组拼接能接受这两例，故不能替代原逐物理行校验。
    assert json.loads(b'[' + b','.join(lines) + b']')
    for scoped in (False, True):
        if scoped:
            with extension._verification_read_scope():
                with pytest.raises(ValueError):
                    extension._verification_json_lines(lines)
        else:
            with pytest.raises(ValueError):
                extension._verification_json_lines(lines)


def test_ledger_reuse_preserves_individual_line_bom_and_does_not_cache_mutations():
    lines = [b'\xef\xbb\xbf{"event": 1}\n', b'\xef\xbb\xbf{"event": 2}\n']
    with extension._verification_read_scope():
        assert extension._verification_json_lines(lines) == [{'event': 1}, {'event': 2}]
        assert extension._verification_json_lines(lines) == [{'event': 1}, {'event': 2}]
        lines.append(b'{"event":3},{"event":4}\n')
        with pytest.raises(ValueError):
            extension._verification_json_lines(lines)


@pytest.mark.parametrize('reader', ('recovery', 'bridge'))
def test_reference_uses_original_safe_open_without_a_third_component_walk(tmp_path, monkeypatch, reader):
    from chanlun_trader.research_factory import failed_account_recovery_v1 as recovery
    module = recovery if reader == 'recovery' else recovery.bridges
    path = tmp_path / 'original.json'
    path.write_bytes(b'{"original": true}')
    native = secure._reject_link_components
    walks = []

    def counted(value):
        walks.append(str(value))
        return native(value)

    monkeypatch.setattr(secure, '_reject_link_components', counted)
    assert module._reference(path, tmp_path)['sha256'] == extension.file_sha256(
        path, root=tmp_path, error_code='UNCHANGED')
    assert len(walks) == 4  # 两次独立读取各保留打开前后检查。
    with pytest.raises(ValueError, match='PATH_INVALID'):
        module._reference(path, tmp_path / 'outside')


def test_parsed_json_tree_copy_preserves_values_types_and_independent_mutability():
    from copy import deepcopy
    original = json.loads('{"nested":[null,true,false,0,-0.0,1.25,"中文\\ud800",{"value":1}],"large":9007199254740993}')
    expected = deepcopy(original)
    first = extension._copy_json_tree(original)
    second = extension._copy_json_tree(original)
    assert first == expected == second
    assert type(first['large']) is int and type(first['nested'][4]) is float
    first['nested'][-1]['value'] = 2
    assert second == original == expected


@pytest.mark.parametrize('relative', (False, True))
def test_benchmark_output_cannot_write_into_fixture_even_with_relative_path(tmp_path, monkeypatch, relative):
    import runpy
    output = runpy.run_path(str(Path(__file__).parents[1] / 'benchmarks/compare_failed_account_read_v1.py'))['benchmark_output']
    root = tmp_path / 'fresh-recovery-readers0'
    root.mkdir()
    monkeypatch.chdir(root)
    path = Path('report.json') if relative else root / 'report.json'
    with pytest.raises(AssertionError, match='BENCHMARK_OUTPUT_INSIDE_FIXTURE'):
        output(root, path)
    assert not (root / 'report.json').exists()
    assert output(root, Path('../report.json')) == tmp_path / 'report.json'
