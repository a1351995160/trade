"""CLI 只转发公共服务，不签发新权限或运行配置携带的代码。"""
import json
from unittest.mock import Mock

import pytest

from scripts import run_trusted_research_v1 as cli


@pytest.mark.parametrize('operation,method,tail,expected', [
    ('capabilities', 'capabilities', [], ()),
    ('preview', 'preview', ['--request', 'REQUEST'], ({'strategy_id': 'example'},)),
    ('freeze', 'freeze', ['--request', 'REQUEST', '--preview-identity', 'frozen'], ({'strategy_id': 'example'}, 'frozen')),
    ('approval', 'approval_preview', ['--task-id', 'task'], ('task',)),
    ('approve', 'approve', ['--task-id', 'task', '--preview-identity', 'approved'], ('task', 'approved')),
    ('start', 'start', ['--task-id', 'task'], ('task',)),
    ('status', 'status', ['--task-id', 'task'], ('task',)),
])
def test_commands_use_same_public_service(tmp_path, monkeypatch, capsys, operation, method, tail, expected):
    deployment = tmp_path / 'deployment.json'
    deployment.write_text('{}')
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'strategy_id': 'example'}))
    service = Mock()
    getattr(service, method).return_value = {'status': 'PUBLIC_RESULT'}
    builder = Mock(return_value=service)
    monkeypatch.setattr(cli, 'build_submission_service', builder)
    assert cli.main([operation, '--workspace-root', str(tmp_path), '--deployment', str(deployment),
                     *[str(request) if value == 'REQUEST' else value for value in tail]]) == 0
    builder.assert_called_once_with(str(tmp_path), {})
    getattr(service, method).assert_called_once_with(*expected)
    assert json.loads(capsys.readouterr().out) == {'status': 'PUBLIC_RESULT'}


def test_deployment_cannot_load_arbitrary_code(tmp_path, capsys):
    deployment = tmp_path / 'deployment.json'
    deployment.write_text(json.dumps({'loader': 'untrusted.module:execute'}))
    assert cli.main(['capabilities', '--workspace-root', str(tmp_path), '--deployment', str(deployment)]) == 1
    assert 'SUBMISSION_DEPLOYMENT_CONFIG_INVALID' in capsys.readouterr().err


def test_service_permission_failure_is_nonzero(tmp_path, monkeypatch, capsys):
    deployment = tmp_path / 'deployment.json'
    deployment.write_text('{}')
    service = Mock()
    service.start.side_effect = PermissionError('EXISTING_AUTHORITY_REQUIRED')
    monkeypatch.setattr(cli, 'build_submission_service', lambda *_: service)
    assert cli.main(['start', '--workspace-root', str(tmp_path), '--deployment', str(deployment), '--task-id', 'task']) == 1
    assert 'EXISTING_AUTHORITY_REQUIRED' in capsys.readouterr().err


def test_approval_requires_exact_preview_identity(tmp_path):
    with pytest.raises(SystemExit) as exc:
        cli.parser().parse_args(['approve', '--workspace-root', str(tmp_path), '--deployment', 'config.json', '--task-id', 'task'])
    assert exc.value.code == 2


def test_publication_command_does_not_build_execution_service(tmp_path, monkeypatch, capsys):
    from chanlun_trader.research_factory import research_capabilities_v1 as catalog
    result = {'status': 'NOT_ACCEPTED', 'feature_ids': [], 'strategy_qualified': False}
    reader = Mock(return_value=result)
    builder = Mock(side_effect=AssertionError('publication must not load deployment'))
    monkeypatch.setattr(catalog, 'published_acceptance', reader)
    monkeypatch.setattr(cli, 'build_submission_service', builder)
    assert cli.main(['publication']) == 0
    reader.assert_called_once_with()
    builder.assert_not_called()
    assert json.loads(capsys.readouterr().out) == result
    with pytest.raises(SystemExit):
        cli.parser().parse_args(['publication', '--request', 'market.json'])
