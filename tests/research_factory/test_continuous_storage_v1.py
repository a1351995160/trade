"""准备阶段的全池扫描原件也须纳入总任务存储边界。"""
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory.continuous_storage_v1 import storage_status
from chanlun_trader.research_factory.exploration_governance import immutable


def test_pending_and_frozen_public_scan_artifacts_count_once(tmp_path):
    campaign = tmp_path / 'campaign'
    research_root = campaign / 'diagnosis_v4'
    submission = campaign / 'public'
    task_id, scan_id = 'a' * 64, 'b' * 64
    immutable(research_root / 'candidate_0001' / 'TASK.json', {'task_id': task_id})
    immutable(submission / 'continuous_intents' / (task_id + '.json'),
        {'task_id': task_id, 'scan_id': scan_id})
    scan = submission / 'signal-scans' / scan_id
    scan.mkdir(parents=True)
    (scan / 'prepared.parquet').write_bytes(b'x' * 1024)
    view = {'base_authorization': {'scope_policy': {'summary': {'storage_limits':
        {'maximum_artifact_bytes': 1024, 'minimum_free_bytes': 1}}}}}
    research = SimpleNamespace(root=research_root, submission=SimpleNamespace(root=submission),
        campaign=SimpleNamespace(directory=campaign, peek_status=lambda: view))
    actual = storage_status(research)
    assert actual['status'] == 'WAITING_STORAGE'
    assert actual['artifact_bytes'] == sum(p.stat().st_size for p in campaign.rglob('*') if p.is_file())
    assert (scan / 'prepared.parquet').stat().st_size == 1024


def test_scan_reference_cannot_escape_submission_root(tmp_path):
    campaign, submission = tmp_path / 'campaign', tmp_path / 'public'
    research_root = campaign / 'diagnosis_v4'
    task_id = 'a' * 64
    immutable(research_root / 'candidate_0001' / 'TASK.json', {'task_id': task_id})
    immutable(submission / 'continuous_intents' / (task_id + '.json'),
        {'task_id': task_id, 'scan_id': '../other'})
    view = {'base_authorization': {'scope_policy': {'summary': {'storage_limits':
        {'maximum_artifact_bytes': 1024, 'minimum_free_bytes': 1}}}}}
    research = SimpleNamespace(root=research_root, submission=SimpleNamespace(root=submission),
        campaign=SimpleNamespace(directory=campaign, peek_status=lambda: view))
    with pytest.raises(PermissionError, match='SCAN_REFERENCE_INVALID'):
        storage_status(research)
