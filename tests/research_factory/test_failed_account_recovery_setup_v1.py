"""通用setup只消费已有Owner批准；不能安装、预留或派发。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts import setup_failed_account_recovery_v1 as setup
from test_account_purpose_time_extension_v1 import extension_case, pin, write
from test_failed_account_recovery_capability_v1 import recovery_case


def test_plan_freeze_inspect_require_pinned_spec_and_preserve_original_budget(recovery_case, monkeypatch, capsys):
    case = recovery_case
    monkeypatch.setattr(setup, 'ROOT', case['root'])
    unchanged = [case['campaign'].budget_path, case['gov'].budget_path,
        case['account'] / 'ARTIFICIAL_BASE_SETTLEMENT.json',
        case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000050_CHARGE.json',
        Path(case['job']['items']['ARTIFICIAL_BASE']['backend_options']['checkpoint_path'])]
    before = {path: path.read_bytes() for path in unchanged}
    spec = write(case['root'] / 'reports' / 'setup_spec.json', case['recovery_spec'])
    assert setup.main(['plan', '--spec', spec['path'], '--spec-sha256', spec['sha256']]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned['dispatched_segments'] == 0
    assert planned['status'] == 'FAILED_ACCOUNT_RECOVERY_PLANNED_NO_DISPATCH'
    with pytest.raises(PermissionError):
        setup.freeze(case['recovery_spec'], approval_ref={})
    approval = case['approvals'].approve(planned['summary'], approver='artificial_owner', capability=case['token'])
    approval_wrapper = write(case['root'] / 'reports' / 'setup_existing_approval_ref.json', approval)
    assert setup.main(['freeze', '--spec', spec['path'], '--spec-sha256', spec['sha256'],
        '--approval', approval_wrapper['path'], '--approval-sha256', approval_wrapper['sha256']]) == 0
    frozen = json.loads(capsys.readouterr().out)
    assert frozen['reservation_delta_applied'] == frozen['dispatched_segments'] == 0
    manifest = frozen['manifest_ref']
    assert setup.main(['inspect', '--manifest', manifest['path'], '--manifest-sha256', manifest['sha256']]) == 0
    assert json.loads(capsys.readouterr().out)['dispatched_segments'] == 0
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert len(case['gov'].segment_status('ARTIFICIAL_BASE')['segments']) == 50
    assert not (case['account'] / 'ACCOUNT_FAILED_RECOVERY.json').exists()


def test_setup_rejects_changed_reference_and_extra_execution_fields(recovery_case, monkeypatch):
    case = recovery_case
    monkeypatch.setattr(setup, 'ROOT', case['root'])
    spec = write(case['root'] / 'reports' / 'setup_spec.json', case['recovery_spec'])
    Path(spec['path']).write_bytes(Path(spec['path']).read_bytes() + b'\n')
    with pytest.raises(ValueError, match='SETUP_REFERENCE_CHANGED'):
        setup.read_reference(spec['path'], spec['sha256'])
    changed = deepcopy(case['recovery_spec']); changed['dispatch'] = True
    with pytest.raises(ValueError, match='SPEC_FIELDS_INVALID'):
        setup.plan(changed)
    assert not (Path(case['task_ref']['path']).parent / 'failed-account-recoveries').exists()
