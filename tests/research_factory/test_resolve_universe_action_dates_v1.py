from copy import deepcopy
import hashlib
import json

import pytest

from chanlun_trader.research.guard import FinalTestAccessViolation
import scripts.resolve_universe_action_dates_v1 as resolver


def factor(day, fore='0.974801', back='10.967811', adjust='10.967811'):
    return {'code': 'sz.000021', 'dividOperateDate': day,
            'foreAdjustFactor': fore, 'backAdjustFactor': back, 'adjustFactor': adjust}


def market(day, close, preclose, status='1'):
    return {'date': day, 'close': close, 'preclose': preclose, 'tradestatus': status}


def interpret(rows, index, **kwargs):
    options = {'action_dates': {20230726}, 'listing_date': 19940202,
               'market_rows': [market('2023-07-27', '19.5800', '19.3800'),
                               market('2023-07-28', '19.7800', '19.5800')],
               'calendar': [20230726, 20230727, 20230728]}
    options.update(kwargs)
    return resolver.interpret_factor_row_v1(rows, index, **options)


def test_unit_to_cumulative_jump_needs_unchanged_fore_back_and_real_price_pair():
    rows = [factor('2023-07-26', adjust='1.000000'), factor('2023-07-28')]
    original = deepcopy(rows)
    status, meaning, evidence = interpret(rows, 1)
    assert status == resolver.VERIFIED
    assert meaning == 'UNIT_ANCHOR_TO_UNCHANGED_CUMULATIVE_FACTORS'
    assert evidence['previous_session'] == 20230727
    assert evidence['preclose_equals_previous_close']
    assert rows == original
    mismatched = [market('2023-07-27', '19.5800', '19.3800'),
                  market('2023-07-28', '19.7800', '19.4500')]
    assert interpret(rows, 1, market_rows=mismatched)[0] == 'UNKNOWN'


def test_same_fore_back_does_not_explain_nonunit_adjust_field_change():
    rows = [factor('2023-07-26', adjust='0.988591'), factor('2023-07-28')]
    status, meaning, _ = interpret(rows, 1)
    assert status == 'UNKNOWN'
    assert meaning == 'UNCHANGED_FORE_BACK_WITH_UNEXPLAINED_ADJUST_FACTOR'


def test_cumulative_factor_change_is_unknown_despite_continuous_market_price():
    rows = [factor('2023-07-26', adjust='1.000000'),
            factor('2023-07-28', fore='0.975801', back='10.968811', adjust='10.968811')]
    assert interpret(rows, 1)[0] == 'UNKNOWN'


def test_all_three_identical_factor_values_support_only_repeated_row_explanation():
    rows = [factor('2023-07-26'), factor('2023-07-28')]
    assert interpret(rows, 1, market_rows=[])[0:2] == (resolver.VERIFIED, 'REPEATED_CUMULATIVE_FACTORS')
    assert interpret(rows, 1, action_dates={20230726, 20230728})[0] == 'UNKNOWN'


def test_observed_quote_start_unit_row_does_not_require_forward_factor_one():
    rows = [factor('2023-07-28', fore='0.315961', back='1.000000', adjust='1.000000')]
    status, meaning, evidence = interpret(rows, 0, listing_date=20230728,
        market_rows=[market('2023-07-28', '39.8000', '27.0000')])
    assert status == resolver.VERIFIED and meaning == 'INITIAL_OBSERVED_QUOTE_UNIT_FACTOR_ROW'
    assert evidence['first_market_date'] == 20230728
    assert interpret(rows, 0, listing_date=20230727,
                     market_rows=[market('2023-07-28', '39.8000', '27.0000')])[0] == 'UNKNOWN'


def test_delayed_initial_unit_row_needs_observed_listing_start_and_continuity():
    rows = [factor('2023-07-28', back='1.000000', adjust='1.000000')]
    quotes = [market('2023-07-26', '42.2000', '31.3300'),
              market('2023-07-27', '45.6100', '42.2000'),
              market('2023-07-28', '49.6200', '45.6100')]
    assert interpret(rows, 0, listing_date=20230726, market_rows=quotes)[0:2] == (
        resolver.VERIFIED, 'INITIAL_UNIT_FACTOR_ANCHOR')
    assert interpret(rows, 0, listing_date=20230726, market_rows=quotes[1:])[0] == 'UNKNOWN'


def test_missing_or_suspended_price_pair_cannot_explain_unit_transition():
    rows = [factor('2023-07-26', adjust='1.000000'), factor('2023-07-28')]
    assert interpret(rows, 1, market_rows=[])[0] == 'UNKNOWN'
    quotes = [market('2023-07-27', '19.5800', '19.3800'),
              market('2023-07-28', '19.7800', '19.5800', status='0')]
    assert interpret(rows, 1, market_rows=quotes)[0] == 'UNKNOWN'


def test_sealed_registered_scope_is_rejected_before_header_body_or_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(resolver, '_response_header', lambda *_: pytest.fail('header read'))
    monkeypatch.setattr(resolver, '_read_response', lambda *_: pytest.fail('body or hash read'))
    source = {'physical_start': 20220801, 'physical_end': 20250801}
    with pytest.raises(FinalTestAccessViolation):
        resolver._action_source(source, tmp_path / 'audit.jsonl')


def test_sealed_request_is_rejected_before_header_body_or_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(resolver, '_response_header', lambda *_: pytest.fail('header read'))
    monkeypatch.setattr(resolver, '_read_response', lambda *_: pytest.fail('body or hash read'))
    source = {'physical_start': 20220801, 'physical_end': 20250731,
              'api': 'query_adjust_factor',
              'request': {'start_date': '2022-08-01', 'end_date': '2025-08-01'}}
    with pytest.raises(FinalTestAccessViolation):
        resolver._action_source(source, tmp_path / 'audit.jsonl')


def receipt_fixture(tmp_path):
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path

    sources = []
    dividend = {'code': 'sz.000021', 'dividOperateDate': '2023-07-26',
        'dividRegistDate': '2023-07-25', 'dividStocksPs': '0', 'dividReserveToStockPs': '',
        'dividStockMarketDate': '', 'dividPayDate': '2023-07-26',
        'dividPlanDate': '2023-07-20', 'dividCashPsBeforeTax': '0.13'}
    for kind, query, values in (
        ('DIVIDEND', {'code': 'sz.000021', 'year': '2023', 'yearType': 'operate'}, [dividend]),
        ('ADJUST', {'code': 'sz.000021', 'start_date': '2023-07-01', 'end_date': '2023-07-31'},
         [factor('2023-07-26', adjust='1.000000'), factor('2023-07-28')])):
        fields = list(values[0])
        raw = [[row[field] for field in fields] for row in values]
        api = 'query_dividend_data' if kind == 'DIVIDEND' else 'query_adjust_factor'
        path = write(tmp_path / (kind + '.json'), {'provider': 'BaoStock', 'api': api,
            'request': query, 'fields': fields, 'error_code': '0',
            'historical_available_at_verified': False, 'raw_rows': raw,
            'raw_rows_sha256': hashlib.sha256(json.dumps(raw, ensure_ascii=False,
                                                       separators=(',', ':')).encode()).hexdigest()})
        sources.append({'symbol': '000021.SZ', 'kind': kind,
            'year': 2023 if kind == 'DIVIDEND' else None, 'api': api, 'request': query,
            'path': str(path), 'sha256': resolver._sha(path), 'origin': 'FROZEN_COLLECTOR_RESPONSE',
            'physical_start': 20230101 if kind == 'DIVIDEND' else 20230701,
            'physical_end': 20231231 if kind == 'DIVIDEND' else 20230731,
            'request_verified': True, 'source_year_type': 'operate' if kind == 'DIVIDEND' else None,
            'row_count': len(raw)})
    catalog = write(tmp_path / 'catalog.json', {'sources': sources})
    fields = ','.join(sorted(resolver._RAW_FIELDS))
    query = {'code': 'sz.000021', 'fields': fields, 'frequency': 'd', 'adjustflag': '3',
             'start_date': '2023-07-01', 'end_date': '2023-07-31'}
    market_path = tmp_path / 'responses/000021.SZ/3.json'
    quotes = [market('2023-07-27', '19.5800', '19.3800'),
              market('2023-07-28', '19.7800', '19.5800')]
    for row in quotes:
        row.update(code='sz.000021', adjustflag='3')
    write(market_path, {'query': query, 'fields': sorted(resolver._RAW_FIELDS),
                       'error_code': '0', 'completed_at': '2026-10-02T00:00:00+00:00', 'rows': quotes})
    started_path = write(market_path.with_name('3.started.json'), {'query': query})
    access_path = write(market_path.with_name('3.access.json'),
        {'error_code': '0', 'path': str(market_path), 'row_count': 2, 'sha256': resolver._sha(market_path)})
    plan = write(tmp_path / 'market_plan.json', {'symbols': ['000021.SZ'], 'start': '2023-07-01',
                                               'end': '2023-07-31'})
    market_catalog = write(tmp_path / 'market_catalog.json', {'read_plan_path': str(plan),
        'read_plan_sha256': resolver._sha(plan), 'responses': [{'symbol': '000021.SZ',
            'path': str(market_path), 'sha256': resolver._sha(market_path),
            'started_sha256': resolver._sha(started_path), 'access_sha256': resolver._sha(access_path),
            'start': 20230701, 'end': 20230731, 'row_count': 2}]})
    calendar_path = write(tmp_path / 'calendar.json', [20230726, 20230727, 20230728])
    manifest = write(tmp_path / 'manifest.json', {'master': {'records': [
        {'symbol': '000021.SZ', 'listing_date': '1994-02-02'}], 'completeness_evidence': {'verified': False}},
        'files': {'calendar.json': {'kind': 'CALENDAR', 'start': 20230701, 'end': 20230731,
                                   'sha256': resolver._sha(calendar_path)},
                  'coverage.json': {'kind': 'CORPORATE_ACTION_COVERAGE', 'evidence': {
                      'source_catalog_path': 'catalog.json', 'source_catalog_sha256': resolver._sha(catalog)}},
                  'reference.json': {'kind': 'REFERENCE_PRICES', 'evidence': {
                      'source_catalog_path': 'market_catalog.json',
                      'source_catalog_sha256': resolver._sha(market_catalog)}}}})
    gaps = write(tmp_path / 'gaps.json', [{'symbol': '000021.SZ', 'effective_date': 20230728,
                                         'reason': resolver.DATE_CONFLICT, 'status': 'UNKNOWN'}])
    output = tmp_path / 'resolution'
    resolver.resolve_universe_action_dates_v1(source_catalog=catalog, gaps=gaps, manifest=manifest,
        output_dir=output, feature_start=20230701, account_end=20230731)
    return output / 'ACTION_DATE_RESOLUTION_RECEIPT.json', catalog, gaps, manifest, market_path


def test_receipt_verifier_replays_sources_and_predicates_without_writing_files(tmp_path):
    receipt, catalog, gaps, manifest, _ = receipt_fixture(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    accepted = resolver.verify_action_date_resolution_receipt_v1(receipt, catalog, gaps, manifest)
    assert accepted == {('000021.SZ', 20230728)}
    assert {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()} == before


def test_receipt_verified_string_does_not_override_tampered_market_predicate(tmp_path):
    receipt, catalog, gaps, manifest, _ = receipt_fixture(tmp_path)
    value = json.loads(receipt.read_text(encoding='utf-8'))
    value['resolutions'][0]['factor_and_market_comparison']['preclose_equals_previous_close'] = False
    receipt.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='PREDICATE_NOT_REPRODUCED'):
        resolver.verify_action_date_resolution_receipt_v1(receipt, catalog, gaps, manifest)


def test_receipt_verifier_rejects_algorithm_or_raw_market_source_changes(tmp_path):
    receipt, catalog, gaps, manifest, market_path = receipt_fixture(tmp_path)
    value = json.loads(receipt.read_text(encoding='utf-8'))
    value['algorithm']['sha256'] = '0' * 64
    receipt.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='ALGORITHM_SOURCE_CHANGED'):
        resolver.verify_action_date_resolution_receipt_v1(receipt, catalog, gaps, manifest)
    value['algorithm']['sha256'] = resolver._sha(resolver.__file__)
    receipt.write_text(json.dumps(value), encoding='utf-8')
    market_path.write_text(market_path.read_text(encoding='utf-8') + '\n', encoding='utf-8')
    with pytest.raises(ValueError, match='MARKET_OR_RECEIPT_SHA_CHANGED'):
        resolver.verify_action_date_resolution_receipt_v1(receipt, catalog, gaps, manifest)
