"""数据元信息和暴露拒绝路径，不读取封存行情。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.research_data_qualification_v1 import (
    audit_data_qualification, inventory_metadata, read_governance_exposures,
)


def manifest():
    return {'dataset_id': 'SAMPLE', 'paths': ['sealed.csv'], 'source': 'TDX',
            'captured_at': '2026-09-27T01:00:00+00:00', 'symbols': ['000001.SZ', '600001.SH'],
            'window': {'start': 20220101, 'end': 20230101}, 'fields': ['close', 'volume'],
            'units': {'close': 'CNY', 'volume': 'SHARES'}, 'adjustment': 'RAW',
            'state_basis': 'HISTORICAL_MODELED', 'corporate_action_basis': 'VERIFIED_CASH_EVENTS',
            'availability_basis': 'MODELED', 'content_hash': 'a' * 64,
            'input_identity': 'original_input'}


def test_same_content_new_path_does_not_become_independent():
    item = manifest()
    item['paths'] = ['new-directory/copy.csv']
    row = audit_data_qualification([item], [{'content_hash': 'a' * 64}])['datasets'][0]
    assert row['historical_independence'] == 'EXPOSED'
    assert not row['independent_confirmation_eligible']
    assert 'CONTENT_HASH_MATCH' in row['exposure_matches'][0]['reasons']


def test_adjustment_and_content_change_cannot_hide_related_window():
    item = manifest()
    item.update(adjustment='FORWARD_ADJUSTED', content_hash='b' * 64, input_identity='new')
    exposure = {'symbols': ['000001.SZ'], 'window': {'start': '2022-06-01', 'end': '2022-06-20'}}
    row = audit_data_qualification([item], [exposure])['datasets'][0]
    assert row['historical_independence'] == 'EXPOSED'
    assert row['exposure_matches'][0]['reasons'] == ['RELATED_SECURITY_WINDOW_EXPOSED']


def test_unexposed_declaration_cannot_authorize_independent_review():
    item = manifest()
    item.update(access_history='UNEXPOSED', independent_confirmation_eligible=True)
    row = audit_data_qualification([item], [])['datasets'][0]
    assert row['metadata_complete']
    assert row['historical_independence'] == 'UNKNOWN'
    assert not row['source_authenticated']
    assert not row['independent_confirmation_eligible']


@pytest.mark.parametrize('key,expected_research', [('units', False), ('state_basis', True)])
def test_missing_units_or_state_limits_metadata_uses(key, expected_research):
    item = manifest()
    del item[key]
    row = audit_data_qualification([item], [])['datasets'][0]
    assert row['metadata_research_ready'] is expected_research
    assert not row['metadata_account_ready']


def test_new_and_delisted_symbols_and_original_manifest_preserved():
    item = manifest()
    item['security_lifecycle'] = {'000001.SZ': 'NEW_LISTING', '600001.SH': 'DELISTED'}
    original = deepcopy(item)
    result = audit_data_qualification([item], [])
    assert result['datasets'][0]['metadata'] == original
    assert item == original
    assert len(result['datasets'][0]['metadata']['symbols']) == 2
    assert result == audit_data_qualification([item], [])


def test_bad_dates_unknown_units_and_duplicate_ids_rejected():
    item = manifest()
    item['window']['start'] = 20220230
    item['units']['volume'] = 'UNKNOWN'
    row = audit_data_qualification([item], [])['datasets'][0]
    assert not row['metadata_research_ready']
    assert 'INVALID_OR_MISSING_WINDOW' in row['reasons']
    assert 'FIELD_UNITS_INCOMPLETE' in row['reasons']
    with pytest.raises(ValueError, match='DATASET_ID'):
        audit_data_qualification([item, item], [])


def governance(tmp_path, *, started=True):
    window = {'symbols': ['000001.SZ'], 'feature_start': 20220407,
              'account_start': 20220706, 'account_end': 20240731}
    plan = {'backend': {'window': window}}
    plan['plan_id'] = stable_hash(plan)
    receipt = {'strategy_plans': {'CANDIDATE': plan}, 'input_identity': 'INPUT',
               'budget_path': str(tmp_path / 'budget.json'), 'objective_id': 'OBJ'}
    receipt['receipt_id'] = stable_hash(receipt)
    (tmp_path / 'CONFIRMATION.json').write_text(json.dumps(receipt), encoding='utf-8')
    if started:
        (tmp_path / 'CANDIDATE_START.json').write_text(json.dumps({
            'kind': 'CANDIDATE', 'receipt_id': receipt['receipt_id'],
            'reservation': 'RES-000001'}), encoding='utf-8')
    return receipt


@pytest.mark.parametrize('started', [True, False])
def test_canonical_metadata_read_without_result_or_budget_access(tmp_path, monkeypatch, started):
    governance(tmp_path, started=started)
    (tmp_path / 'CANDIDATE_RESULT.json').write_text('secret prices and returns', encoding='utf-8')
    (tmp_path / 'budget.json').write_text('untouched', encoding='utf-8')
    original_read = Path.read_bytes
    read_paths = []

    def guarded(path):
        assert path.name in ('CONFIRMATION.json', 'CANDIDATE_START.json')
        read_paths.append(path.name)
        return original_read(path)

    monkeypatch.setattr(Path, 'read_bytes', guarded)
    exposures = read_governance_exposures(tmp_path)
    assert exposures[0]['window']['feature_start'] == 20220407
    assert exposures[0]['input_identity'] == 'INPUT'
    assert exposures[0]['purpose'] == ('ACCOUNT_STARTED' if started else 'ACCOUNT_AUTHORIZED_POSSIBLE_EXPOSURE')
    assert len(read_paths) == (2 if started else 1)


def test_tampered_canonical_receipt_and_start_fail_closed(tmp_path):
    receipt = governance(tmp_path)
    start = tmp_path / 'CANDIDATE_START.json'
    start.write_text(json.dumps({'kind': 'OTHER', 'receipt_id': receipt['receipt_id']}), encoding='utf-8')
    with pytest.raises(ValueError, match='START_BINDING'):
        read_governance_exposures(tmp_path)
    receipt['input_identity'] = 'tampered'
    (tmp_path / 'CONFIRMATION.json').write_text(json.dumps(receipt), encoding='utf-8')
    with pytest.raises(ValueError, match='RECEIPT_HASH'):
        read_governance_exposures(tmp_path)


def test_inventory_never_reads_content_and_keeps_missing_roots(tmp_path, monkeypatch):
    (tmp_path / 'sealed.csv').write_text('secret', encoding='utf-8')

    def forbidden(*args, **kwargs):
        raise AssertionError('Content must not be read')

    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    monkeypatch.setattr(Path, 'read_text', forbidden)
    result = inventory_metadata([tmp_path, tmp_path / 'missing'])
    assert not result['content_read']
    assert result['roots'][0]['entries'][0]['name'] == 'sealed.csv'
    assert result['roots'][1]['status'] == 'UNAVAILABLE'
