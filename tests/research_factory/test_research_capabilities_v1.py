from copy import deepcopy
import pytest
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities, require_current, render_markdown


def test_capability_query_separates_engine_entry_and_evidence():
    snapshot = capabilities()
    stop = next(item for item in snapshot['features'] if item['id'] == 'cost_stop')
    assert stop['engine'] is True
    assert stop['public_entry'] is True
    atr = next(item for item in snapshot['features'] if item['id'] == 'atr_stop')
    assert atr['engine'] is True and atr['public_entry'] is False
    assert stop['evidence'] == 'NOT_ACCEPTED'
    assert snapshot['data']['status'] == 'UNKNOWN'
    assert '按实际买入成本止损' in render_markdown(snapshot)
    assert require_current(snapshot['fingerprint']) == snapshot


def test_data_change_invalidates_capability_snapshot():
    snapshot = capabilities(data_catalog={'status': 'UNKNOWN'})
    with pytest.raises(ValueError, match='CAPABILITY_SNAPSHOT_STALE'):
        require_current(snapshot['fingerprint'], data_catalog={'status': 'INFERRED'})


def test_forged_evidence_changes_do_not_change_service():
    forged = deepcopy(capabilities())
    forged['features'][0]['evidence'] = 'VALIDATED'
    assert capabilities()['features'][0]['evidence'] == 'NOT_ACCEPTED'
