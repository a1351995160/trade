"""Regression coverage for evidence retained outside pytest's named basetemp."""
import hashlib
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / 'tests/ci/pytest_resource_artifact_paths.py'
PLAN = runpy.run_path(str(HELPER))['evidence_plan']


def fixture(tmp_path, suite='failed-account-worker'):
    temp = tmp_path / 'runner'; temp.mkdir()
    base = temp / (suite + '-tests'); base.mkdir()
    root = temp / 'rf_abcdefgh'; root.mkdir()
    marker = base / 'RESEARCH_FACTORY_TMP_ROOT.json'
    marker.write_text(json.dumps({'root': str(root), 'exclusive': True}), encoding='utf-8')
    return temp, base, root, marker


def test_short_root_uploads_original_supervisor_bytes(tmp_path):
    temp, base, root, marker = fixture(tmp_path)
    resource = root / 'SUPERVISOR_RESOURCE.json'; resource.write_bytes(b'{"actual":true}\r\n')
    before = hashlib.sha256(resource.read_bytes()).hexdigest()
    plan = PLAN(temp, 'failed-account-worker')
    assert (root / '**/SUPERVISOR_*').as_posix() in plan['artifact_paths']
    assert (temp / 'failed-account-*-tests/**/SUPERVISOR_*').as_posix() in plan['artifact_paths']
    assert plan['marker_sha256'] == hashlib.sha256(marker.read_bytes()).hexdigest()
    assert hashlib.sha256(resource.read_bytes()).hexdigest() == before
    assert plan['evidence_bytes_modified'] is False


def test_original_basetemp_remains_supported(tmp_path):
    temp = tmp_path / 'runner'; temp.mkdir()
    base = temp / 'failed-account-worker-tests'; base.mkdir()
    (base / 'SUPERVISOR_RESOURCE.json').write_text('{}', encoding='utf-8')
    plan = PLAN(temp, 'failed-account-worker')
    assert plan['short_root'] is None
    assert plan['artifact_paths'] == [
        'failed-account-source-identity.json', 'pytest-resource-artifact-paths.json',
        (temp / 'failed-account-*-resource-evidence/**').as_posix(),
        (temp / 'failed-account-*-tests/**/SUPERVISOR_*').as_posix(),
    ]


def test_missing_worker_resource_is_rejected_before_upload(tmp_path):
    temp, _, _, _ = fixture(tmp_path)
    with pytest.raises(ValueError, match='resource evidence missing'):
        PLAN(temp, 'failed-account-worker')


@pytest.mark.parametrize('change', ['outside', 'nested', 'not_exclusive', 'extra_key', 'newline', 'wrong_name'])
def test_root_marker_cannot_expand_job_scope(tmp_path, change):
    temp, _, root, marker = fixture(tmp_path, 'failed-account-owner')
    value = {'root': str(root), 'exclusive': True}
    if change == 'outside': value['root'] = str(tmp_path / 'rf_abcdefgh')
    if change == 'nested': value['root'] = str(root / 'rf_ijklmnop')
    if change == 'not_exclusive': value['exclusive'] = False
    if change == 'extra_key': value['allow_outside'] = True
    if change == 'newline': value['root'] += '\npaths=/**'
    if change == 'wrong_name': value['root'] = str(temp / 'shared')
    marker.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError): PLAN(temp, 'failed-account-owner')


def test_live_reparse_metadata_is_checked(tmp_path, monkeypatch):
    temp, _, root, _ = fixture(tmp_path, 'failed-account-owner')
    original = Path.lstat
    def changed(path):
        value = original(path)
        if path == root: return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=1024)
        return value
    monkeypatch.setattr(Path, 'lstat', changed)
    with pytest.raises(ValueError, match='reparse'): PLAN(temp, 'failed-account-owner')


@pytest.mark.parametrize('suite,base,suffix', [
    ('workflow', 'autonomous-completion-tests', '**/DATA_WORKER*.json'),
    ('full-universe', 'full-universe-tests', '**/SCALE_RESOURCE_EVIDENCE.json'),
])
def test_other_original_evidence_uploads_follow_same_marker(tmp_path, suite, base, suffix):
    temp = tmp_path / 'runner'; temp.mkdir(); (temp / base).mkdir()
    root = temp / 'rf_abcdefgh'; root.mkdir()
    (temp / base / 'RESEARCH_FACTORY_TMP_ROOT.json').write_text(json.dumps({'root': str(root), 'exclusive': True}), encoding='utf-8')
    plan = PLAN(temp, suite)
    assert (root / suffix).as_posix() in plan['artifact_paths']
    assert any(base in p for p in plan['artifact_paths'])


def test_workflow_uses_plan_for_all_three_existing_artifacts():
    workflow = (REPO / '.github/workflows/autonomous-completion.yml').read_text(encoding='utf-8')
    assert 'python tests/ci/pytest_resource_artifact_paths.py --suite ${{ matrix.suite }}' in workflow
    assert workflow.count('path: ${{ steps.pytest-evidence-paths.outputs.paths }}') == 3
    assert 'tests/research_factory/test_ci_pytest_resource_artifact_paths.py' in workflow
