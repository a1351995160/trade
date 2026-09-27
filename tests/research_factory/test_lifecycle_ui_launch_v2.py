import json

import pytest

from scripts.run_ui import lifecycle_service


def test_explicit_ui_configuration_is_readonly_on_assembly(tmp_path):
    config = tmp_path / 'lifecycle.json'
    config.write_text(json.dumps({'bindings': {}}), encoding='utf-8')
    service = lifecycle_service(tmp_path, config)
    assert service.inspect()['bindings'] == {}
    assert list(tmp_path.iterdir()) == [config]


def test_ui_loader_requires_frozen_path_window_and_input_identity(tmp_path, monkeypatch):
    import scripts.run_historical_process_research_v1 as history
    import chanlun_trader.research_factory.rule_account_backend_v2 as backend
    config = tmp_path / 'lifecycle.json'
    config.write_text(json.dumps({'bindings': {}}), encoding='utf-8')
    calls = []
    def build(path, *, symbols):
        calls.append((path, symbols))
        return {'window': 'fixed', 'symbols': ['sh.600000', 'sz.000001', 'sz.000002']}, {'events': []}, {}
    monkeypatch.setattr(history, 'build_bundle', build)
    monkeypatch.setattr(backend, 'rule_input_identity', lambda *_: 'verified_hash')
    monkeypatch.setattr(backend, 'rule_window', lambda value: value)
    service = lifecycle_service(tmp_path, config)
    assert not calls
    manifest = {'data_root': str(tmp_path / 'data'), 'profile': 'HISTORICAL_MODELED',
                'candidate_capability': 'RESEARCH_RULE_STRATEGY_V2', 'window': {'window': 'fixed', 'symbols': ['sh.600000', 'sz.000001', 'sz.000002']},
                'input_identity': 'verified_hash'}
    bundle, events = service.research_loader(manifest, None)
    assert bundle['input_identity'] == 'verified_hash' and events == () and len(calls) == 1
    with pytest.raises(ValueError, match='INPUT_CHANGED'):
        service.research_loader({**manifest, 'input_identity': 'other'}, None)
    assert calls[0][1] == manifest['window']['symbols']
    before = len(calls)
    with pytest.raises(ValueError, match='DATA_SCOPE'):
        service.research_loader({**manifest, 'data_root': str(tmp_path.parent / 'outside')}, None)
    assert len(calls) == before


def test_ui_config_cannot_redirect_paths_or_enable_execution(tmp_path):
    config = tmp_path / 'lifecycle.json'
    config.write_text(json.dumps({'bindings': {}, 'mode': 'GOVERNED'}), encoding='utf-8')
    with pytest.raises(ValueError, match='CONFIG_FIELDS'):
        lifecycle_service(tmp_path, config)
    with pytest.raises(ValueError, match='RESEARCH_ROOT_REQUIRED'):
        lifecycle_service(None, config)
    with pytest.raises(ValueError, match='OUTSIDE_WORKSPACE'):
        lifecycle_service(tmp_path / 'child', config)

def test_cli_and_ui_share_qualified_loader(tmp_path, monkeypatch):
    from scripts import run_strategy_lifecycle_v1 as cli
    import scripts.lifecycle_deployment_v2 as deployment
    import chanlun_trader.research_factory.lifecycle_service_v2 as services
    bindings = tmp_path / 'bindings.json'
    bindings.write_text('{}', encoding='utf-8')
    marker = object()
    monkeypatch.setattr(deployment, 'qualified_research_loader', lambda root: marker)
    seen = []
    class Service:
        def __init__(self, root, bindings, *, research_loader):
            seen.append(research_loader)
        def inspect(self):
            return {'bindings': {}}
    monkeypatch.setattr(services, 'LifecycleServiceV2', Service)
    args = cli.parser().parse_args(['lifecycle-preview', '--workspace-root', str(tmp_path),
                                  '--bindings', str(bindings)])
    assert cli.execute(args) == {'bindings': {}}
    config = tmp_path / 'deployment.json'
    config.write_text(json.dumps({'bindings': {}}), encoding='utf-8')
    lifecycle_service(tmp_path, config)
    assert seen == [marker, marker]
