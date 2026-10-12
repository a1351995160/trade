"""元信息 CLI 不持有 Owner 能力；摘要与批准参数必须保留原绑定。"""
import hashlib
import json

import pytest

from scripts import setup_account_version_continuation_v1 as cli


def reference(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')
    return str(path), hashlib.sha256(path.read_bytes()).hexdigest()


def test_changed_spec_is_rejected_before_planning_or_install(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    path, digest = reference(tmp_path / 'SPEC.json', {'authorization_id': 'synthetic'})
    (tmp_path / 'SPEC.json').write_text('{}', encoding='utf-8')
    monkeypatch.setattr(cli, 'plan', lambda spec: pytest.fail('变摘要不得进入规划'))
    with pytest.raises(ValueError, match='ACCOUNT_VERSION_SETUP_REFERENCE'):
        cli.main(['plan', '--spec', path, '--spec-sha256', digest])


def test_freeze_uses_registered_owner_reference_payloads(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    spec_path, spec_sha = reference(tmp_path / 'SPEC.json', {'synthetic_spec': True})
    bridge = {'approval_id': 'a' * 64, 'summary_hash': 'b' * 64}
    approval = {'approval_id': 'c' * 64, 'summary_hash': 'd' * 64}
    bridge_path, bridge_sha = reference(tmp_path / 'BRIDGE_APPROVAL_REF.json', bridge)
    approval_path, approval_sha = reference(tmp_path / 'APPROVAL_REF.json', approval)
    calls = []
    monkeypatch.setattr(cli, 'freeze', lambda spec, **kwargs:
        calls.append((spec, kwargs)) or {'status': 'SYNTHETIC_NO_DISPATCH'})
    assert cli.main(['freeze', '--spec', spec_path, '--spec-sha256', spec_sha,
        '--bridge-approval', bridge_path, '--bridge-approval-sha256', bridge_sha,
        '--approval', approval_path, '--approval-sha256', approval_sha]) == 0
    assert calls == [({'synthetic_spec': True}, {'bridge_approval_ref': bridge, 'approval_ref': approval})]
    assert json.loads(capsys.readouterr().out)['status'] == 'SYNTHETIC_NO_DISPATCH'


def test_freeze_requires_explicit_registered_owner_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    path, digest = reference(tmp_path / 'SPEC.json', {})
    monkeypatch.setattr(cli, 'freeze', lambda *a, **k: pytest.fail('没有批准不进入安装'))
    with pytest.raises(SystemExit) as exc:
        cli.main(['freeze', '--spec', path, '--spec-sha256', digest])
    assert exc.value.code == 2


def test_plan_rejects_additional_scope_fields_before_owner_access():
    with pytest.raises(ValueError, match='ACCOUNT_VERSION_SETUP_SPEC_FIELDS_INVALID'):
        cli.plan({'authorization_id': 'synthetic', 'extra_budget': 1})
