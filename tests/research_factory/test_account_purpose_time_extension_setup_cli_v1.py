"""通用 setup CLI 只规划/核验/冻结已有批准，不持 Owner 对象能力。"""
import importlib.util
import json
from pathlib import Path

import pytest

from test_account_purpose_time_extension_v1 import extension_case, install, pin, write


@pytest.fixture
def setup_cli(extension_case, monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'scripts/setup_account_purpose_time_extension_v1.py'
    spec = importlib.util.spec_from_file_location('synthetic_account_purpose_time_extension_setup', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', extension_case['root'])
    return module, extension_case


def test_plan_reads_exact_spec_without_owner_signing_or_reservation(setup_cli, capsys):
    cli, case = setup_cli; reference = write(case['root'] / 'reports/spec.json', case['spec'])
    ledger = case['campaign'].budget_path.with_name('run_budget_events.jsonl'); before = ledger.read_bytes()
    assert cli.main(['plan', '--spec', reference['path'], '--spec-sha256', reference['sha256']]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'ACCOUNT_PURPOSE_TIME_EXTENSION_PLANNED_NO_DISPATCH'
    assert result['summary']['aggregate_reserved_delta'] == 86400
    assert result['dispatched_segments'] == 0 and ledger.read_bytes() == before
    assert not (Path(case['task_ref']['path']).parent / 'account-purpose-time-extensions').exists()


def test_freeze_requires_registered_approval_arguments(setup_cli):
    cli, case = setup_cli; reference = write(case['root'] / 'reports/spec.json', case['spec'])
    with pytest.raises(SystemExit) as error:
        cli.main(['freeze', '--spec', reference['path'], '--spec-sha256', reference['sha256']])
    assert error.value.code == 2


def test_spec_sha_change_is_rejected_before_planning(setup_cli):
    cli, case = setup_cli; reference = write(case['root'] / 'reports/spec.json', case['spec'])
    Path(reference['path']).write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='SETUP_REFERENCE_CHANGED'):
        cli.main(['plan', '--spec', reference['path'], '--spec-sha256', reference['sha256']])


def test_inspect_returns_original_proof_without_dispatch(setup_cli, capsys):
    cli, case = setup_cli; context, _, _ = install(case)
    assert cli.main(['inspect', '--manifest', context.scope_reference['path'],
        '--manifest-sha256', context.scope_reference['sha256']]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['account_purpose_time_extension'] == context.proof()
    assert result['dispatched_segments'] == 0
