"""已有公告相对引用必须按明确资料根解析，保留原文哈希和目录边界。"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import prepare_long_horizon_data_v1 as preparation


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('separator', ['/', '\\'])
def test_relative_announcement_uses_explicit_main_even_when_current_directory_has_same_path(tmp_path, monkeypatch, separator):
    main = tmp_path/'main/trade-system-contract-port-v1'
    cwd = tmp_path/'other/trade-system-contract-port-v1'
    relative = 'reports/share_announcement_audit_v3/bucket_c/supplement_review_v1/COMPLETE_FINAL.json'
    target, decoy = main/relative, cwd/relative
    for path in (target, decoy):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'same reviewed content')
    monkeypatch.chdir(cwd)
    relocations = []
    text = relative.replace('/', separator)
    result = preparation.inherited_path_v1(text, sha(target), main, relocations)
    assert result == target and result != decoy
    assert relocations[0]['original_path'] == text and relocations[0]['same_content_verified'] is True


@pytest.mark.parametrize('text', ['../outside.json', 'reports/../../outside.json',
    'E:reports/review.json', '\\\\server\\share\\review.json', '\\\\?\\E:\\review.json', 'E:/../review.json', '\\reports\\review.json'])
def test_untrusted_relative_or_drive_paths_cannot_change_resolution_scope(tmp_path, text):
    main = tmp_path/'trade-system-contract-port-v1'; main.mkdir()
    with pytest.raises(ValueError, match='INHERITED_SOURCE_PATH_INVALID'):
        preparation.inherited_path_v1(text, '0'*64, main, [])


def test_missing_main_relative_does_not_fall_back_to_current_directory(tmp_path, monkeypatch):
    main = tmp_path/'main/trade-system-contract-port-v1'; main.mkdir(parents=True)
    other = tmp_path/'other'; other.mkdir()
    (other/'report.json').write_bytes(b'only current-directory copy')
    monkeypatch.chdir(other)
    with pytest.raises(ValueError, match='MISSING_OR_CHANGED'):
        preparation.inherited_path_v1('report.json', sha(other/'report.json'), main, [])


def test_relative_hash_change_remains_failure_and_original_declaration_is_not_mutated(tmp_path):
    main = tmp_path/'trade-system-contract-port-v1'
    target = main/'reports/review.json'; target.parent.mkdir(parents=True); target.write_bytes(b'original')
    declaration = {'review': {'path': 'reports\\review.json', 'sha256': sha(target)}, 'terms': ['unchanged']}
    before = deepcopy(declaration)
    relocated = preparation.relocate_declarations_v1(declaration, main, [])
    assert declaration == before and relocated['review']['path'] == str(target)
    target.write_bytes(b'altered')
    with pytest.raises(ValueError, match='MISSING_OR_CHANGED'):
        preparation.relocate_declarations_v1(declaration, main, [])


def test_absolute_existing_source_and_missing_old_checkout_retained_copy(tmp_path):
    main = tmp_path/'main/trade-system-contract-port-v1'
    target = main/'reports/review.json'; target.parent.mkdir(parents=True); target.write_bytes(b'original')
    assert preparation.inherited_path_v1(str(target), sha(target), main, []) == target
    old = tmp_path/'removed/trade-system-contract-port-v1/reports/review.json'
    assert preparation.inherited_path_v1(str(old), sha(target), main, []) == target


def junction(link, destination):
    if os.name == 'nt':
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(destination)], capture_output=True)
        if result.returncode: pytest.skip('当前Windows不能创建测试目录junction')
    else:
        link.symlink_to(destination, target_is_directory=True)


def test_relative_directory_redirect_and_redirected_legacy_root_are_rejected(tmp_path):
    main = tmp_path/'trade-system-contract-port-v1'; main.mkdir()
    outside = tmp_path/'other'; outside.mkdir(); target = outside/'review.json'; target.write_bytes(b'original')
    link = main/'reports'; junction(link, outside)
    try:
        with pytest.raises(ValueError, match='MISSING_OR_CHANGED'):
            preparation.inherited_path_v1('reports/review.json', sha(target), main, [])
        with pytest.raises(ValueError, match='ROOT_REDIRECTED'):
            preparation.inherited_path_v1('review.json', sha(target), link, [])
    finally:
        if os.name == 'nt': link.rmdir()
        else: link.unlink()
    assert target.read_bytes() == b'original'
