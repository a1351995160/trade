"""固定 TRAIN CLI；只使用临时合成 Parquet 和受保护审批 fixture。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory.campaign_scope_v1 import OwnerApprovalStoreV1
from scripts import project_research_train_v1 as cli
from test_research_dataset_projection_v1 import case, prepare, sha, write


def deployment(tmp_path):
    _, provider, root, _, days, grant, args, _ = case(tmp_path)
    owner = object()
    approvals = OwnerApprovalStoreV1(tmp_path / 'owner', owner_capability=owner)
    reference = approvals.approve(grant, approver='SYNTHETIC_OWNER', capability=owner)
    record = tmp_path / 'data_authorization.json'
    write(record, grant)
    authority = tmp_path / 'data_deployment.json'
    write(authority, {'schema_version': 'TRUSTED_RESEARCH_DATA_DEPLOYMENT_V1',
        'owner_approval_store_root': str(approvals.directory),
        'records': {'deployed-ref': {'path': str(record), 'sha256': sha(record), 'approval_reference': reference}}})
    config = {'schema_version': 'TRUSTED_TRAIN_PROJECTION_DEPLOYMENT_V1',
        'trusted_data_deployment': {'path': str(authority), 'sha256': sha(authority)},
        'registered_parents': {'parent': {'root': str(root), 'manifest_path': 'parent_manifest.json',
                                        'manifest_sha256': provider._datasets['parent'][2]}}}
    path = tmp_path / 'projection_deployment.json'
    write(path, config)
    return path, config, days, args, approvals, reference


def arguments(path, args):
    return ['--deployment-config', str(path), '--deployment-sha256', sha(path),
        '--dataset-id', 'parent', '--authorization-ref', 'deployed-ref', '--output-root', str(args['output_root']),
        '--train-start', str(args['train_start']), '--train-end', str(args['train_end']),
        '--account-start', str(args['account_start'])]


def test_help_needs_no_deployment_or_data(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(['--help'])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert '--deployment-sha256' in help_text and '--authorization-ref' in help_text
    assert '--resolver' not in help_text and '--parent-root' not in help_text


def test_fixed_builder_and_cli_publish_child_readable_by_ordinary_provider(tmp_path, capsys):
    path, _, days, args, _, _ = deployment(tmp_path)
    assert cli.main(arguments(path, args)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['independent_confirmation_eligible'] is False
    prepared = prepare(result, days)
    assert prepared['qualification']['account_data_ready']
    assert prepared['bundle']['daily'].date.max() == days[70]
    assert len(prepared['window']['symbols']) == 3
    assert Path(result['receipt_path']).is_file()


@pytest.mark.parametrize('change', ['pin', 'dataset', 'extra_resolver', 'parent_path', 'parent_hash'])
def test_fixed_builder_rejects_changed_registration_before_price_read(tmp_path, monkeypatch, change):
    path, config, _, _, _, _ = deployment(tmp_path)
    config = deepcopy(config)
    digest, dataset = sha(path), 'parent'
    if change == 'pin':
        digest = 'f' * 64
    elif change == 'dataset':
        dataset = 'not-registered'
    elif change == 'extra_resolver':
        config['resolver_module'] = 'arbitrary:load'
    elif change == 'parent_path':
        config['registered_parents']['parent']['manifest_path'] = '../outside.json'
    else:
        config['registered_parents']['parent']['manifest_sha256'] = 'f' * 64
    if change not in {'pin', 'dataset'}:
        write(path, config)
        digest = sha(path)
    from chanlun_trader.research_factory import research_dataset_projection_v1 as projection
    monkeypatch.setattr(projection, '_sha', lambda *a: pytest.fail('登记拒绝前不可读取行情'))
    with pytest.raises(ValueError):
        cli.build_train_projection(path, digest, dataset)


def test_cli_owner_revocation_denies_before_any_parent_market_hash(tmp_path, monkeypatch):
    path, _, _, args, approvals, reference = deployment(tmp_path)
    (approvals.directory / (reference['approval_id'] + '.json')).unlink()
    from chanlun_trader.research_factory import research_dataset_projection_v1 as projection
    monkeypatch.setattr(projection, '_sha', lambda *a: pytest.fail('缺少批准不可读取行情'))
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_NOT_REGISTERED'):
        cli.main(arguments(path, args))
    assert not args['output_root'].exists()
