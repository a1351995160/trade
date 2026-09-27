"""协议准备不授予资格；复用正式服务的明确合成档案夹具。"""
from copy import deepcopy

import pytest

from chanlun_trader.research_factory import validation_protocol_v2 as protocol
from chanlun_trader.research_factory.common import stable_hash
from test_formal_assessment_v1 import harness


@pytest.fixture
def scope(harness, monkeypatch):
    service, family, register, run, calls, evidence = harness
    ids = family(members=2)
    monkeypatch.setattr('chanlun_trader.research_factory.strategy_qualification_v1.BoundedStrategyArchiveV1',
                        lambda root: service.archive)
    args = {'archive_root': service.archive.root, 'strategy_ids': ids,
            'symbols': ['000001.SZ', '600000.SH'], 'not_before': 20990105}
    return args, service, register


def test_preview_does_not_create_protocol_or_consume_budget(scope):
    args, service, _ = scope
    before = service._plans()
    view = protocol.preview_protocol(**args)
    assert view['status'] == 'PREPARATION_ONLY'
    assert view['confirmation_budget_consumed'] == 0 and not view['strategy_qualified']
    assert view['requirements']['close_snapshots_required'] == 564
    assert service._plans() == before


def test_freeze_repeat_same_identity_and_formal_binding(scope):
    args, service, register = scope
    first = protocol.freeze_protocol(**args)
    assert protocol.freeze_protocol(**args) == first
    path = service.archive.load(args['strategy_ids'][0])['origin']['source_root']
    from pathlib import Path
    path = Path(path) / 'VALIDATION_PROTOCOL_V2.json'
    assert protocol.load_protocol(path) == first
    registered = register(args['strategy_ids'], not_before=args['not_before'], protocol_path=path)
    assert service.plan(registered['batch_id'])['validation_protocol']['protocol_hash'] == stable_hash(first)


def test_sealed_history_is_not_granted_by_relabeling(scope):
    args, _, _ = scope
    value = protocol.preview_protocol(**args, route='SEALED_HISTORICAL', metadata_report={
        'authority': 'READ_ONLY_METADATA_PROJECTION',
        'datasets': [{'historical_independence': 'EXPOSED', 'independent_confirmation_eligible': True}]})
    assert value['status'] == 'UNSUPPORTED_ROUTE'
    assert not value['requirements']['route_executable']
    assert not value['strategy_qualified']
    assert 'REFERENCED_HISTORY_ALREADY_EXPOSED' in value['reason_codes']


def test_incomplete_family_and_changed_scope_rejected(scope):
    args, service, _ = scope
    with pytest.raises(ValueError, match='MEMBER_OMITTED'):
        protocol.preview_protocol(**{**args, 'strategy_ids': args['strategy_ids'][:1]})
    protocol.freeze_protocol(**args)
    with pytest.raises(ValueError, match='ALREADY_FROZEN'):
        protocol.freeze_protocol(**{**args, 'not_before': 20990201})


def test_changed_archive_invalidates_frozen_protocol(scope, monkeypatch):
    args, service, _ = scope
    frozen = protocol.freeze_protocol(**args)
    from pathlib import Path
    path = Path(frozen['preview']['source_root']) / 'VALIDATION_PROTOCOL_V2.json'
    original = service.archive.load
    def changed(key):
        value = deepcopy(original(key))
        value['rule_identity'] = stable_hash('OTHER_RULE')
        return value
    monkeypatch.setattr(service.archive, 'load', changed)
    with pytest.raises(ValueError, match='ARCHIVE_CHANGED'):
        protocol.load_protocol(path)


@pytest.mark.parametrize('day', [True, 20260230, 2026092])
def test_invalid_date_rejected(scope, day):
    args, _, _ = scope
    with pytest.raises(ValueError):
        protocol.preview_protocol(**{**args, 'not_before': day})
