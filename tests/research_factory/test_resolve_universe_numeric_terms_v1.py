import json
from fractions import Fraction

import pytest

from chanlun_trader.research.guard import FinalTestAccessViolation
from scripts.resolve_universe_numeric_terms_v1 import (
    BINDING_VERSION, VERIFIED, resolve_universe_numeric_terms_v1,
    verify_numeric_terms_resolution_receipt_v1,
)
from tests.research_factory.test_universe_actions_v1 import action, fixture, prepare, sha, write


def numeric_fixture(tmp_path, *, baostock=None, raw_overrides=None, duplicate=False,
                    event_overrides=None, capital_status=None):
    original_row = baostock or action(dividCashPsBeforeTax='', dividPayDate='',
        dividReserveToStockPs='.3', dividStockMarketDate='2023-01-09')
    original_manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [original_row]})
    prepare(original_manifest, acquisition)
    manifest = original_manifest.parent / 'manifest_actions_v1.json'
    catalog = manifest.parent / 'actions/SOURCE_CATALOG.json'
    gaps = manifest.parent / 'numeric_gaps.json'
    write(gaps, [{'symbol': '000001.SZ', 'effective_date': 20230105, 'year': 2023,
                  'kind': 'CORPORATE_ACTION', 'reason': 'ACTION_NUMERIC_TERM_UNKNOWN',
                  'status': 'UNKNOWN'}])
    package = tmp_path / 'tdx_package'
    package.mkdir()
    raw = {'symbol': '000001.SZ', 'code': '000001', 'market': 0, 'category': 1,
           'datetime': 20230105, 'hongli_panqianliutong': 0,
           'peigujia_qianzongguben': 0, 'songgu_qianzongguben': 3.0,
           'peigu_houzongguben': 0, **(raw_overrides or {})}
    window = package / 'gbbq_window.jsonl'
    window.write_text(json.dumps(raw) + '\n' + (json.dumps(raw) + '\n' if duplicate else '')
                      + ''.join(json.dumps({**raw, **companion}) + '\n'
                                for companion in (capital_status or [])),
                      encoding='utf-8')
    ratio = 1 + Fraction(str(raw['songgu_qianzongguben'])) / 10
    event = {'symbol': '000001.SZ', 'effective_date': 20230105,
             'event_id': sha(window) + ':10:BONUS', 'event_type': 'BONUS',
             'source': 'TDX_GBBQ_OWNER_EXPORT:' + 'a' * 64, 'units': 'NEW_SHARES_PER_OLD_SHARE',
             'terms': {'raw_shares_per_ten': raw['songgu_qianzongguben'],
                       'ratio_numerator': ratio.numerator, 'ratio_denominator': ratio.denominator},
             **(event_overrides or {})}
    events = package / 'events.jsonl'
    events.write_text(json.dumps(event) + '\n', encoding='utf-8')
    audit = tmp_path / 'GBBQ_READ_AUDIT.json'
    write(audit, {'source_sha256': 'a' * 64, 'output_sha256': sha(window),
                  'parser_version': 'pytdx.GbbqReader', 'parser_sha256': 'b' * 64})
    tdx_manifest = package / 'actions_manifest.json'
    write(tdx_manifest, {'version': 'WindowedCorporateActionDatasetV1', 'start': 20230103,
        'end': 20230109, 'dataset_id': 'OWNER_GBBQ_' + 'a' * 64, 'source_identity': 'a' * 64,
        'events_file': 'events.jsonl', 'events_sha256': sha(events),
        'coverage': {'symbols': ['000001.SZ'], 'complete': False, 'accounting_supported': False},
        'physical_window_attestation': {'start': 20230103, 'end': 20230109,
            'owner': 'SYNTHETIC_OWNER', 'authorization_sha256': 'c' * 64,
            'window_enforced_before_export': True, 'not_derived_from_current_incident': True}})
    binding = tmp_path / 'tdx_binding.json'
    write(binding, {'version': BINDING_VERSION, 'start': 20230103, 'end': 20230109,
        'inputs': {key: {'path': str(path), 'sha256': sha(path)} for key, path in (
            ('tdx_manifest', tdx_manifest), ('tdx_window', window), ('tdx_read_audit', audit))}})
    return {'source_catalog': catalog, 'numeric_gaps': gaps, 'manifest': manifest,
            'tdx_binding': binding, 'output_dir': tmp_path / 'numeric_resolution'}


def resolve(inputs):
    result = resolve_universe_numeric_terms_v1(**inputs)
    receipt = inputs['output_dir'] / 'NUMERIC_TERMS_RESOLUTION_RECEIPT.json'
    return result, receipt, json.loads(receipt.read_text(encoding='utf-8'))


def test_zero_cash_resolution_reproduces_original_and_exact_event(tmp_path):
    inputs = numeric_fixture(tmp_path)
    before = inputs['source_catalog'].read_bytes()
    result, receipt, value = resolve(inputs)
    accepted = verify_numeric_terms_resolution_receipt_v1(receipt,
        source_catalog=inputs['source_catalog'], manifest=inputs['manifest'])
    assert result['verified_count'] == 1 and len(accepted) == 1
    record = value['resolutions'][0]
    assert record['status'] == VERIFIED
    assert record['original_sources'][0]['original_row']['dividCashPsBeforeTax'] == ''
    assert record['tdx_event']['event_type'] == 'BONUS'
    assert record['cash_per_share'] == 0 and record['tax_rule'] == 'UNKNOWN'
    assert record['account_terms_qualified'] is False and record['share_credit_date'] is None
    assert inputs['source_catalog'].read_bytes() == before


@pytest.mark.parametrize('optional_field,positive_field', [
    ('dividStocksPs', 'dividReserveToStockPs'),
    ('dividReserveToStockPs', 'dividStocksPs'),
])
def test_optional_blank_share_rate_has_same_bound_ratio_as_explicit_zero(
        tmp_path, optional_field, positive_field):
    from scripts.prepare_universe_actions_v1 import SHARE_RATE_POLICY
    records = []
    for name, optional_value in [('blank', ''), ('zero', '0')]:
        own = tmp_path / name
        own.mkdir()
        inputs = numeric_fixture(own, baostock=action(dividCashPsBeforeTax='', dividPayDate='',
            **{optional_field: optional_value, positive_field: '.3'}))
        result, receipt, value = resolve(inputs)
        assert result['verified_count'] == 1
        assert len(verify_numeric_terms_resolution_receipt_v1(receipt,
            source_catalog=inputs['source_catalog'], manifest=inputs['manifest'])) == 1
        record = value['resolutions'][0]
        assert record['original_sources'][0]['original_row'][optional_field] == optional_value
        assert record['optional_share_rate_interpretation_policy'] == SHARE_RATE_POLICY
        assert record['original_sources'][0]['original_row']['dividCashPsBeforeTax'] == ''
        records.append(record)
    assert records[0]['positive_share_comparison'] == records[1]['positive_share_comparison']
    assert records[0]['positive_share_comparison']['baostock_share_rate'] == .3


@pytest.mark.parametrize('invalid_rate', ['NaN', '-.1', 'unknown'])
def test_optional_share_policy_does_not_replace_invalid_numeric_rate(tmp_path, invalid_rate):
    inputs = numeric_fixture(tmp_path, baostock=action(dividCashPsBeforeTax='', dividPayDate='',
        dividStocksPs=invalid_rate, dividReserveToStockPs='.3'))
    result, _, value = resolve(inputs)
    assert result['verified_count'] == 0
    assert value['resolutions'][0]['status'] == 'UNKNOWN'


def test_both_blank_share_rates_do_not_create_positive_share_evidence(tmp_path):
    inputs = numeric_fixture(tmp_path, baostock=action(dividCashPsBeforeTax='', dividPayDate='',
        dividStocksPs='', dividReserveToStockPs=''))
    result, _, value = resolve(inputs)
    assert result['verified_count'] == 0
    assert 'POSITIVE_SHARE_RATE_REQUIRED' in value['resolutions'][0]['reason']


@pytest.mark.parametrize('raw_overrides,event_overrides,reason', [
    ({'symbol': '300001.SZ'}, None, 'UNREGISTERED_SYMBOL'),
    ({'datetime': 20230106}, None, 'EXACT_SINGLE_CATEGORY_ONE'),
    ({'code': '300001'}, None, 'SECURITY_IDENTITY_CONFLICT'),
    ({'songgu_qianzongguben': 4}, None, 'SHARE_RATIO_CONFLICT'),
    ({'hongli_panqianliutong': 1e-12}, None, 'NONZERO_CASH_OR_RIGHTS'),
    ({'peigu_houzongguben': 1e-12}, None, 'NONZERO_CASH_OR_RIGHTS'),
    ({'peigujia_qianzongguben': 1e-12}, None, 'NONZERO_CASH_OR_RIGHTS'),
    ({'songgu_qianzongguben': 'NaN'}, None, 'NUMBER_INVALID'),
    ({'category': 2}, None, 'EXACT_SINGLE_CATEGORY_ONE'),
    (None, {'event_type': 'RIGHTS'}, 'EXACT_BONUS_EVENT'),
    (None, {'effective_date': 20230106}, 'EXACT_BONUS_EVENT'),
    (None, {'event_id': 'wrong:10:BONUS'}, 'EVENT_SOURCE_OR_TERMS_CONFLICT'),
])
def test_no_zero_resolution_when_cross_source_terms_disagree(tmp_path, raw_overrides,
                                                            event_overrides, reason):
    if raw_overrides and raw_overrides.get('songgu_qianzongguben') == 'NaN':
        # 测试非有限原数值，不让 fixture 的 Fraction 构造先报错。
        inputs = numeric_fixture(tmp_path)
        anchor = json.loads(inputs['tdx_binding'].read_text())
        window = inputs['tdx_binding'].parent / 'tdx_package/gbbq_window.jsonl'
        raw = json.loads(window.read_text())
        raw.update(raw_overrides)
        window.write_text(json.dumps(raw) + '\n', encoding='utf-8')
        audit = inputs['tdx_binding'].parent / 'GBBQ_READ_AUDIT.json'
        proof = json.loads(audit.read_text())
        proof['output_sha256'] = sha(window)
        write(audit, proof)
        anchor['inputs']['tdx_window']['sha256'] = sha(window)
        anchor['inputs']['tdx_read_audit']['sha256'] = sha(audit)
        write(inputs['tdx_binding'], anchor)
    else:
        inputs = numeric_fixture(tmp_path, raw_overrides=raw_overrides, event_overrides=event_overrides)
    if reason == 'UNREGISTERED_SYMBOL':
        with pytest.raises(ValueError, match=reason):
            resolve(inputs)
    else:
        result, receipt, value = resolve(inputs)
        assert result['verified_count'] == 0
        assert reason in value['resolutions'][0]['reason']
        assert verify_numeric_terms_resolution_receipt_v1(receipt,
            source_catalog=inputs['source_catalog'], manifest=inputs['manifest']) == {}


def test_exact_duplicate_tdx_record_is_rejected(tmp_path):
    result, _, value = resolve(numeric_fixture(tmp_path, duplicate=True))
    assert result['verified_count'] == 0
    assert 'EXACT_SINGLE_CATEGORY_ONE' in value['resolutions'][0]['reason']


@pytest.mark.parametrize('category', [5, 9])
def test_same_day_capital_status_is_preserved_without_inventing_cash_terms(tmp_path, category):
    # 与真实000657.SZ的category9字段形状相同；每股送转率与股本数量分开。
    companion = {'category': category, 'hongli_panqianliutong': 94769.4453125,
        'peigujia_qianzongguben': 107500.625, 'songgu_qianzongguben': 123200.2734375,
        'peigu_houzongguben': 139750.8125}
    inputs = numeric_fixture(tmp_path, capital_status=[companion])
    result, receipt, value = resolve(inputs)
    assert result['verified_count'] == 1
    record = value['resolutions'][0]
    assert record['tdx_row']['category'] == 1 and record['cash_per_share'] == 0
    state, = record['tdx_capital_status_rows']
    assert state['row']['category'] == category
    assert state['per_old_share_entitlement_terms'] is False
    assert state['share_capital_state_qualified'] is False
    assert state['ratio_diagnostic']['same_capital_change_cause_claimed'] is False
    assert len(verify_numeric_terms_resolution_receipt_v1(receipt,
        source_catalog=inputs['source_catalog'], manifest=inputs['manifest'])) == 1


def test_different_capital_growth_is_diagnosed_without_relaxing_entitlement_rate(tmp_path):
    companion = {'category': 5, 'hongli_panqianliutong': 1000,
        'peigujia_qianzongguben': 2000, 'songgu_qianzongguben': 1700,
        'peigu_houzongguben': 3400}
    result, _, value = resolve(numeric_fixture(tmp_path, capital_status=[companion]))
    assert result['verified_count'] == 1
    record = value['resolutions'][0]
    diagnostic = record['tdx_capital_status_rows'][0]['ratio_diagnostic']
    assert diagnostic['declared_entitlement_ratio'] == 1.3
    assert diagnostic['total_capital_ratio'] == 1.7
    assert diagnostic['total_capital_ratio_difference'] == pytest.approx(.4)
    assert record['share_capital_state_qualified'] is False
    assert record['positive_share_comparison']['relative_tolerance'] == 2e-7


@pytest.mark.parametrize('companion,reason', [
    ({'category': 11}, 'COMPOUND_OR_UNKNOWN_CATEGORY'),
    ({'category': 15}, 'COMPOUND_OR_UNKNOWN_CATEGORY'),
    ({'category': 77}, 'COMPOUND_OR_UNKNOWN_CATEGORY'),
    ({'category': 9, 'code': '300001'}, 'SECURITY_IDENTITY_CONFLICT'),
    ({'category': 9, 'hongli_panqianliutong': 3000}, 'CAPITAL_STATUS_COUNTS_INVALID'),
    ({'category': 9, 'peigu_houzongguben': 0}, 'CAPITAL_STATUS_COUNTS_INVALID'),
    ({'category': 9, 'songgu_qianzongguben': 'NaN'}, 'NUMBER_INVALID'),
    ({'category': 9, 'hongli_panqianliutong': -1}, 'NUMBER_INVALID'),
])
def test_compound_unknown_or_malformed_capital_companion_stays_unknown(tmp_path, companion, reason):
    status = {'category': 9, 'hongli_panqianliutong': 1000,
        'peigujia_qianzongguben': 2000, 'songgu_qianzongguben': 1300,
        'peigu_houzongguben': 2600, **companion}
    result, _, value = resolve(numeric_fixture(tmp_path, capital_status=[status]))
    assert result['verified_count'] == 0
    assert reason in value['resolutions'][0]['reason']


def test_duplicate_or_conflicting_capital_status_does_not_grant_zero_resolution(tmp_path):
    status = {'category': 9, 'hongli_panqianliutong': 1000,
        'peigujia_qianzongguben': 2000, 'songgu_qianzongguben': 1300,
        'peigu_houzongguben': 2600}
    result, _, value = resolve(numeric_fixture(tmp_path, capital_status=[status, status]))
    assert result['verified_count'] == 0
    assert 'CAPITAL_STATUS_DUPLICATE_OR_CONFLICT' in value['resolutions'][0]['reason']


def test_float32_nonzero_share_comparison_keeps_exact_zero_gate(tmp_path):
    result, _, value = resolve(numeric_fixture(tmp_path,
        raw_overrides={'songgu_qianzongguben': 3.0000002384}))
    assert result['verified_count'] == 1
    assert value['resolutions'][0]['positive_share_comparison']['zero_fields_use_exact_comparison']


@pytest.mark.parametrize('field', ['symbol', 'effective_date', 'cash_per_share', 'source_sha'])
def test_modified_resolution_cannot_be_consumed(tmp_path, field):
    inputs = numeric_fixture(tmp_path)
    _, receipt, value = resolve(inputs)
    row = value['resolutions'][0]
    if field == 'source_sha':
        row['original_sources'][0]['binding']['sha256'] = 'd' * 64
    else:
        row[field] = {'symbol': '300001.SZ', 'effective_date': 20230106, 'cash_per_share': 0.01}[field]
    write(receipt, value)
    with pytest.raises(ValueError, match='PREDICATE_NOT_REPRODUCED'):
        verify_numeric_terms_resolution_receipt_v1(receipt,
            source_catalog=inputs['source_catalog'], manifest=inputs['manifest'])


@pytest.mark.parametrize('role', ['tdx_manifest', 'tdx_window', 'tdx_read_audit'])
def test_bound_tdx_package_change_is_rejected_before_rows(tmp_path, role):
    from pathlib import Path
    inputs = numeric_fixture(tmp_path)
    _, receipt, _ = resolve(inputs)
    anchor = json.loads(inputs['tdx_binding'].read_text())
    path = Path(anchor['inputs'][role]['path'])
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='BOUND_SOURCE_CHANGED'):
        verify_numeric_terms_resolution_receipt_v1(receipt,
            source_catalog=inputs['source_catalog'], manifest=inputs['manifest'])


def test_changed_baostock_original_is_rejected(tmp_path):
    from pathlib import Path
    inputs = numeric_fixture(tmp_path)
    _, receipt, value = resolve(inputs)
    path = Path(value['resolutions'][0]['original_sources'][0]['binding']['path'])
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='ORIGINAL_SHA_CHANGED'):
        verify_numeric_terms_resolution_receipt_v1(receipt,
            source_catalog=inputs['source_catalog'], manifest=inputs['manifest'])


def test_unregistered_catalog_cannot_grant_numeric_resolution(tmp_path):
    inputs = numeric_fixture(tmp_path)
    metadata = json.loads(inputs['manifest'].read_text())
    for item in metadata['files'].values():
        item.pop('evidence', None)
    write(inputs['manifest'], metadata)
    with pytest.raises((ValueError, KeyError), match='source_catalog_path|CATALOG_REGISTRATION'):
        resolve(inputs)


def test_future_scope_rejected_before_opening_tdx_rows(tmp_path, monkeypatch):
    from pathlib import Path
    inputs = numeric_fixture(tmp_path)
    anchor = json.loads(inputs['tdx_binding'].read_text())
    anchor['end'] = 20250801
    write(inputs['tdx_binding'], anchor)
    original = Path.open
    accessed = []
    def track(path, *args, **kwargs):
        if path.name == 'gbbq_window.jsonl':
            accessed.append(str(path))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', track)
    with pytest.raises(FinalTestAccessViolation):
        resolve(inputs)
    assert accessed == []


def test_invalid_physical_attestation_rejected_before_hashing_data(tmp_path, monkeypatch):
    from pathlib import Path
    from scripts import resolve_universe_numeric_terms_v1 as resolver
    inputs = numeric_fixture(tmp_path)
    anchor = json.loads(inputs['tdx_binding'].read_text())
    package_manifest = Path(anchor['inputs']['tdx_manifest']['path'])
    value = json.loads(package_manifest.read_text())
    value['physical_window_attestation']['window_enforced_before_export'] = False
    write(package_manifest, value)
    anchor['inputs']['tdx_manifest']['sha256'] = sha(package_manifest)
    write(inputs['tdx_binding'], anchor)
    original = resolver._sha
    accessed = []
    def track(path):
        if Path(path).name in {'gbbq_window.jsonl', 'events.jsonl'}:
            accessed.append(str(path))
        return original(path)
    monkeypatch.setattr(resolver, '_sha', track)
    with pytest.raises(ValueError, match='PHYSICAL_PACKAGE_PROOF_INVALID'):
        resolve(inputs)
    assert accessed == []
