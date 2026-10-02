import hashlib
import json

import pytest

from scripts import classify_universe_action_evidence_v1 as classifier


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path):
    stage = tmp_path / 'package' / 'staging'
    stage.mkdir(parents=True)
    symbols = ['000001.SZ', '000002.SZ', '600001.SH', '600002.SH', '600003.SH']
    paths = {role: tmp_path / (role + '.json') for role in classifier.INPUT_ROLES}
    paths.update(gbbq_window=stage / 'gbbq_window.jsonl',
                 actions_manifest=stage / 'actions_manifest.json',
                 gbbq_read_audit=stage.parent / 'GBBQ_READ_AUDIT.json')
    requests, resolutions, gaps = [], [], []
    for symbol in symbols:
        gap = {'symbol': symbol, 'effective_date': 20230703, 'year': 2023,
               'reason': 'ADJUST_AND_ACTION_DATES_CONFLICT', 'status': 'UNKNOWN',
               'kind': 'CORPORATE_ACTION'}
        gaps.append(gap)
        requests.append({'symbol': symbol, 'effective_date': 20230703,
            'related_action_date': 20230703, 'reason': 'INITIAL_NONUNIT_FACTOR_WITHOUT_ACTION',
            'announcement_publication_window': {'start': 20230504, 'end': 20230703},
            'status': 'NOT_EXECUTED', 'automatic_retry': False,
            'final_test_guard_required_before_any_hash_or_body_read': True,
            'maximum_metadata_pages': 1, 'maximum_selected_original_documents': 2,
            'raw_preclose_equals_previous_close': True,
            'evidence_type': 'EXACT_ACTION_IMPLEMENTATION_OR_CAPITAL_CHANGE_NOTICE'})
        resolutions.append({'symbol': symbol, 'effective_date': 20230703, 'status': 'UNKNOWN',
            'original_gap': gap, 'interpretation': 'INITIAL_NONUNIT_FACTOR_WITHOUT_ACTION',
            'historical_available_at_verified': False, 'global_no_other_event_claim': False,
            'adjust_sources': [{'raw_row': {'code': symbol[-2:].lower() + '.' + symbol[:6],
                'dividOperateDate': '2023-07-03', 'adjustFactor': '1.5',
                'foreAdjustFactor': '1.5', 'backAdjustFactor': '1.5'}}]})
    write(paths['queue'], {'version': 'TARGETED_UNKNOWN_DATE_EVIDENCE_QUEUE_V2',
        'request_count': len(requests), 'requests': requests,
        'original_30_unknown_statuses_preserved': True, 'unbounded_search_authorized': False,
        'automatic_execution': False, 'automatic_retry': False,
        'total_selected_original_document_limit': 2 * len(requests)})
    write(paths['receipt'], {'version': 'UNIVERSE_ACTION_DATE_RESOLUTION_V1',
                            'resolutions': resolutions})
    write(paths['price_gaps'], gaps)
    rows = []
    for symbol, category, day in [(symbols[0], 1, 20230703), (symbols[0], 15, 20230703),
                                  (symbols[1], 15, 20230703), (symbols[2], 5, 20230703),
                                  (symbols[3], 1, 20230630)]:
        rows.append({'market': 0 if symbol.endswith('.SZ') else 1, 'code': symbol[:6],
            'symbol': symbol, 'datetime': day, 'category': category,
            'hongli_panqianliutong': 2.2799999714, 'peigujia_qianzongguben': 0.0,
            'songgu_qianzongguben': 0.0, 'peigu_houzongguben': 0.0})
    paths['gbbq_window'].write_text('\n'.join(json.dumps(row) for row in rows) + '\n',
                                   encoding='utf-8')
    write(paths['actions_manifest'], {'version': 'WindowedCorporateActionDatasetV1',
        'dataset_id': 'OWNER_GBBQ_' + 'a' * 64, 'source_identity': 'a' * 64,
        'start': 20220722, 'end': 20240731,
        'coverage': {'complete': False, 'accounting_supported': False},
        'physical_window_attestation': {'owner': 'USER_AUTHORIZED_LOCAL_DATA_OWNER',
            'authorization_sha256': 'b' * 64, 'start': 20220722, 'end': 20240731,
            'window_enforced_before_export': True, 'not_derived_from_current_incident': True}})
    write(paths['gbbq_read_audit'], {'source_sha256': 'a' * 64,
        'output_sha256': sha(paths['gbbq_window']), 'window_records': len(rows),
        'parser_version': 'pytdx.GbbqReader', 'parser_sha256': 'c' * 64})
    return paths


def classify(paths, out, expected=None):
    return classifier.classify_universe_action_evidence_v1(
        **paths, output_dir=out, expected_sha256=expected or {role: sha(path)
                                                            for role, path in paths.items()})


def rewrite_rows(paths, mutation):
    rows = [json.loads(line) for line in paths['gbbq_window'].read_text(encoding='utf-8').splitlines()]
    mutation(rows)
    paths['gbbq_window'].write_text('\n'.join(json.dumps(row) for row in rows) + '\n',
                                   encoding='utf-8')
    audit = load(paths['gbbq_read_audit'])
    audit.update(output_sha256=sha(paths['gbbq_window']), window_records=len(rows))
    write(paths['gbbq_read_audit'], audit)


def test_classifies_records_without_qualifying_or_changing_originals(tmp_path):
    paths = fixture(tmp_path)
    before = {role: path.read_bytes() for role, path in paths.items()}
    out = tmp_path / 'classified'
    summary = classify(paths, out)
    result = load(out / 'ACTION_DATE_EVIDENCE_CLASSIFICATION.json')
    queue = load(out / 'TARGETED_ACTION_DATE_ANNOUNCEMENT_QUEUE.json')
    assert summary['target_count'] == summary['unknown_count'] == 5
    assert summary['exact_record_target_count'] == 3
    assert summary['exact_category_target_counts'] == {'1': 1, '5': 1, '15': 2}
    assert summary['resolved_count'] == 0
    assert result['classifications'][0]['exact_categories'] == [1, 15]
    original = result['classifications'][0]['gbbq_exact_rows'][0]
    assert original['raw_fields']['hongli_panqianliutong'] == 2.2799999714
    assert original['line_number'] == 1 and original['relation'] == 'EXACT_EFFECTIVE_DATE'
    assert result['inputs']['gbbq_window']['sha256'] == sha(paths['gbbq_window'])
    assert result['source_identity']['files']['scripts/classify_universe_action_evidence_v1.py']
    for item in result['classifications']:
        assert item['status'] == item['price_event_model_status'] == item['account_terms_status'] == 'UNKNOWN'
        assert item['tax_rule'] == 'UNKNOWN' and item['share_ratio'] is None
        assert not item['record_presence_implies_qualification'] and not item['verified_non_event']
        assert not item['global_no_other_event_claim']
    assert queue['request_count'] == 5 and queue['total_selected_original_document_limit'] == 10
    assert not queue['automatic_execution'] and not queue['unbounded_search_authorized']
    for actual, request in zip(queue['requests'], load(paths['queue'])['requests']):
        assert actual['announcement_publication_window'] == request['announcement_publication_window']
        assert actual['effective_date'] == request['effective_date']
        assert actual['status'] == 'NOT_EXECUTED' and actual['classification_status'] == 'UNKNOWN'
    assert {role: path.read_bytes() for role, path in paths.items()} == before


def test_category_15_raw_values_do_not_prove_share_ratio_tax_or_account_pass(tmp_path):
    paths = fixture(tmp_path)
    rewrite_rows(paths, lambda rows: rows[2].update(songgu_qianzongguben=30.0,
                                                   peigu_houzongguben=250000000.0))
    out = tmp_path / 'classified'
    classify(paths, out)
    item = load(out / 'ACTION_DATE_EVIDENCE_CLASSIFICATION.json')['classifications'][1]
    assert item['share_ratio'] is None and item['tax_rule'] == 'UNKNOWN'
    assert item['account_terms_status'] == item['price_event_model_status'] == 'UNKNOWN'
    assert item['share_credit_date'] is None and item['tradable_date'] is None
    assert 'CATEGORY_15_EVENT_MAPPING_NOT_IMPLEMENTED' in item['implementation_capability_gaps']
    assert 'RESTRUCTURING_IMPLEMENTATION_NOTICE' in item['suggested_evidence']['announcement_types']
    assert 'EX_PRICE_FORMULA_AND_REFERENCE_PRICE' in item['suggested_evidence']['required_proofs']


def test_missing_exact_record_and_nearby_record_are_not_no_event_proof(tmp_path):
    paths = fixture(tmp_path)
    out = tmp_path / 'classified'
    classify(paths, out)
    items = load(out / 'ACTION_DATE_EVIDENCE_CLASSIFICATION.json')['classifications']
    assert items[3]['exact_categories'] == [] and items[3]['nearby_categories'] == [1]
    assert items[3]['gbbq_nearby_rows'][0]['raw_fields']['datetime'] == 20230630
    assert not items[4]['gbbq_nearby_rows'] and not items[4]['gbbq_exact_rows']
    assert items[4]['difference_classifications'] == ['NO_TARGET_RECORD_IN_BOUNDED_PACKAGE']
    for item in items[3:]:
        assert item['status'] == 'UNKNOWN' and not item['verified_non_event']
        assert 'BOUNDED_SOURCE_COMPLETENESS_UNPROVEN' in item['implementation_capability_gaps']


def test_original_audit_count_before_symbol_filter_is_distinct_from_export_count(tmp_path):
    paths = fixture(tmp_path)
    audit = load(paths['gbbq_read_audit'])
    audit['window_records'] = 8
    write(paths['gbbq_read_audit'], audit)
    summary = classify(paths, tmp_path / 'classified')
    assert summary['package_row_count'] == 5
    assert summary['original_audit_window_record_count'] == 8
    assert summary['unknown_count'] == 5 and summary['resolved_count'] == 0


@pytest.mark.parametrize('role', classifier.INPUT_ROLES)
def test_changed_bound_input_is_rejected_without_output(tmp_path, role):
    paths = fixture(tmp_path)
    expected = {name: sha(path) for name, path in paths.items()}
    with paths[role].open('a', encoding='utf-8') as stream:
        stream.write(' ')
    out = tmp_path / 'classified'
    with pytest.raises(ValueError, match='INPUT_CHANGED:' + role):
        classify(paths, out, expected)
    assert not out.exists()


@pytest.mark.parametrize('mutation,reason', [
    ({'datetime': 20240801}, 'ROW_OUTSIDE_WINDOW'),
    ({'datetime': 20240732}, 'DATE_INVALID'),
    ({'datetime': 20250801}, 'ROW_OUTSIDE_WINDOW'),
    ({'symbol': '000003.SZ'}, 'STOCK_OR_CATEGORY_CONFLICT'),
    ({'code': '000003'}, 'STOCK_OR_CATEGORY_CONFLICT'),
    ({'market': 1}, 'STOCK_OR_CATEGORY_CONFLICT'),
    ({'hongli_panqianliutong': float('nan')}, 'NUMBER_INVALID')])
def test_invalid_gbbq_identity_or_window_is_rejected_even_when_rebound(tmp_path, mutation, reason):
    paths = fixture(tmp_path)
    rewrite_rows(paths, lambda rows: rows[0].update(mutation))
    with pytest.raises(ValueError, match=reason):
        classify(paths, tmp_path / 'classified')
    assert not (tmp_path / 'classified').exists()


@pytest.mark.parametrize('change', ['unbounded', 'attestation_missing', 'not_before_export', 'wrong_owner'])
def test_unbounded_or_unattested_package_is_rejected_before_window_hash_or_body_read(tmp_path, monkeypatch, change):
    paths = fixture(tmp_path)
    manifest = load(paths['actions_manifest'])
    if change == 'unbounded':
        manifest['start'] = manifest['physical_window_attestation']['start'] = 19900101
    elif change == 'attestation_missing':
        manifest.pop('physical_window_attestation')
    elif change == 'not_before_export':
        manifest['physical_window_attestation']['window_enforced_before_export'] = False
    else:
        manifest['physical_window_attestation']['owner'] = 'UNKNOWN'
    write(paths['actions_manifest'], manifest)
    expected = {role: sha(path) for role, path in paths.items()}
    read_bound, window_reads = classifier._read_bound, []
    protected_paths = {paths['gbbq_window'], paths['gbbq_window'].parent / 'events.jsonl'}
    read_bytes = type(paths['gbbq_window']).read_bytes

    def protected_read(path):
        if path in protected_paths:
            raise AssertionError('Unattested window or events must not be read or hashed')
        return read_bytes(path)

    def guarded(path, expected, role):
        if role == 'gbbq_window':
            window_reads.append(path)
        return read_bound(path, expected, role)

    monkeypatch.setattr(classifier, '_read_bound', guarded)
    monkeypatch.setattr(type(paths['gbbq_window']), 'read_bytes', protected_read)
    with pytest.raises(ValueError, match='BOUNDED_PACKAGE_REQUIRED'):
        classify(paths, tmp_path / 'classified', expected)
    assert not window_reads and not (tmp_path / 'classified').exists()


@pytest.mark.parametrize('change,reason', [('queue_stock', 'UNKNOWN_SET_CHANGED'),
    ('queue_date', 'TARGET_SCOPE_CONFLICT'), ('receipt_date', 'RECEIPT_DATE_CONFLICT'),
    ('receipt_stock', 'RECEIPT_STOCK_CONFLICT')])
def test_wrong_stock_or_date_cannot_be_attached_to_target(tmp_path, change, reason):
    paths = fixture(tmp_path)
    if change.startswith('queue'):
        queue = load(paths['queue'])
        queue['requests'][0]['symbol' if change == 'queue_stock' else 'effective_date'] = (
            '000003.SZ' if change == 'queue_stock' else 20230702)
        write(paths['queue'], queue)
    else:
        receipt = load(paths['receipt'])
        row = receipt['resolutions'][0]['adjust_sources'][0]['raw_row']
        row['code' if change == 'receipt_stock' else 'dividOperateDate'] = (
            'sz.000003' if change == 'receipt_stock' else '2023-07-02')
        write(paths['receipt'], receipt)
    with pytest.raises(ValueError, match=reason):
        classify(paths, tmp_path / 'classified')


def test_package_source_identity_must_match_original_audit(tmp_path):
    paths = fixture(tmp_path)
    audit = load(paths['gbbq_read_audit'])
    audit['source_sha256'] = 'd' * 64
    write(paths['gbbq_read_audit'], audit)
    with pytest.raises(ValueError, match='PACKAGE_IDENTITY_CONFLICT'):
        classify(paths, tmp_path / 'classified')


def test_existing_output_is_immutable(tmp_path):
    paths = fixture(tmp_path)
    out = tmp_path / 'classified'
    classify(paths, out)
    before = {path.name: path.read_bytes() for path in out.iterdir()}
    with pytest.raises(ValueError, match='NEW_OUTPUT_REQUIRED'):
        classify(paths, out)
    assert {path.name: path.read_bytes() for path in out.iterdir()} == before


def test_cli_reads_only_explicit_six_input_bindings(tmp_path, capsys):
    paths = fixture(tmp_path)
    bindings = tmp_path / 'bindings.json'
    write(bindings, {'version': classifier.BINDINGS_VERSION,
        'inputs': {role: {'path': str(path), 'sha256': sha(path)} for role, path in paths.items()}})
    out = tmp_path / 'classified'
    classifier.main(['--input-bindings', str(bindings), '--output-dir', str(out), '--json'])
    assert json.loads(capsys.readouterr().out)['unknown_count'] == 5
    assert load(out / 'TARGETED_ACTION_DATE_ANNOUNCEMENT_QUEUE.json')['request_count'] == 5
