from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts import collect_universe_gaps_v1 as collector
from scripts import prepare_long_horizon_data_v1 as preparation
from scripts.prepare_long_horizon_data_v1 import (
    inherited_path_v1, prepare_long_horizon_data_v1, relocate_declarations_v1,
)
from chanlun_trader.research_factory.baostock_raw_supplement_v1 import RAW_FIELDS
from tests.research_factory.test_baostock_raw_supplement_v1 import RawClient
from tests.research_factory.test_tdx_research_adapter_v1 import DATES, SYMBOLS, daily, evidence
from tests.research_factory.test_universe_actions_v1 import Response, action, legacy_catalog, sha, write


class AllDaysClient(RawClient):
    def __init__(self, *, conflict=False, factor_rows=False):
        super().__init__({})
        self.conflict = conflict
        self.factor_rows = factor_rows

    def query_adjust_factor(self, **query):
        if not self.factor_rows:
            return super().query_adjust_factor(**query)
        return Response(['code', 'dividOperateDate', 'foreAdjustFactor', 'backAdjustFactor', 'adjustFactor'],
            [[query['code'], str(pd.Timestamp(str(day)).date()), '2', '2', '2'] for day in DATES[1:3]])

    def query_history_k_data_plus(self, **query):
        fields = query['fields'].split(',')
        rows = []
        for date in DATES:
            r = {'date': str(pd.Timestamp(str(date)).date()), 'code': query['code'],
                'open': '10', 'high': '11', 'low': '9', 'close': '10',
                'preclose': '10', 'volume': '1000', 'amount': '10000', 'adjustflag': '3',
                'tradestatus': '1', 'isST': '0'}
            if self.conflict and query['code'] == 'sz.000001' and date == DATES[1]:
                r['close'] = '10.5'
            rows.append([r[f] for f in fields])
        return Response(fields, rows)


def setup(tmp_path, *, omit=None, conflict=False, factor_rows=False, execute=True):
    legacy = tmp_path / 'trade-system-contract-port-v1'
    base = legacy / 'data/full_universe'
    base.mkdir(parents=True)
    daily().to_parquet(base / 'daily.parquet', index=False)
    write(base / 'SOURCE_QUALIFICATION.json', [{'symbol': s,
        'cache_present': True, 'origin_status': 'EXACT_RAW_WINDOW_MATCH',
        'indicator_qualification': 'RAW_PRICE_AND_MODELED_UNIT_READY', 'reasons': []} for s in SYMBOLS])
    write(base / 'OLD_ACTION_CATALOG.json', {'sources': []})
    write(legacy / 'data/research/security_state/raw/trade_calendar.json',
        {'source': 'baostock.query_trade_dates', 'query_date_range': {'start': '2023-03-01',
            'end': '2023-03-06'}, 'trade_dates': [str(pd.Timestamp(str(d)).date()) for d in DATES]})
    proof = evidence(sha(base / 'daily.parquet'))
    manifest = {'adapter': 'TDX_FULL_UNIVERSE_V1', 'universe_id': 'OLD_REGISTERED',
        'start': DATES[0], 'end': DATES[-1], 'universe_scope': {'start': DATES[0], 'end': DATES[-1]},
        'source_qualification_ref': 'SOURCE_QUALIFICATION.json',
        'corporate_action_preparation': {'source_catalog': 'OLD_ACTION_CATALOG.json'},
        'master': {'source': 'synthetic_master', 'completeness_evidence': {'verified': False},
            'records': [{'symbol': s, 'listing_date': 20000103} for s in SYMBOLS]},
        'files': {'daily.parquet': {'kind': 'DAILY', 'format': 'PARQUET',
            'source_id': 'tdx_daily', 'symbols': SYMBOLS, 'start': DATES[0], 'end': DATES[-1],
            'sha256': sha(base / 'daily.parquet'), 'evidence': proof},
            'SOURCE_QUALIFICATION.json': {'kind': 'SOURCE_QUALIFICATION', 'format': 'JSON',
                'source_id': 'source_qualification', 'start': DATES[0], 'end': DATES[-1],
                'sha256': sha(base / 'SOURCE_QUALIFICATION.json')}}}
    path = base / 'manifest.json'
    write(path, manifest)
    requests = []
    for symbol in SYMBOLS:
        code = symbol[-2:].lower() + '.' + symbol[:6]
        if symbol != omit:
            requests.append({'file': f'RAW_{symbol}.json', 'api': 'query_history_k_data_plus',
                'request': {'code': code, 'fields': ','.join(sorted(RAW_FIELDS)),
                    'start_date': '2023-03-01', 'end_date': '2023-03-06',
                    'frequency': 'd', 'adjustflag': '3'}})
        requests.extend([{'file': f'ADJUST_{symbol}.json', 'api': 'query_adjust_factor',
            'request': {'code': code, 'start_date': '2023-03-01', 'end_date': '2023-03-06'}},
            {'file': f'DIVIDEND_{symbol}_2023.json', 'api': 'query_dividend_data',
            'request': {'code': code, 'year': '2023', 'yearType': 'operate'}}])
    queue = {'version': 'UNIVERSE_BAOSTOCK_SUPPLEMENTS_V1', 'request_count': len(requests),
        'authorization_source': {'origin': 'USER_EXPLICIT_CURRENT_TASK', 'statement': '合成测试',
            'scope': 'SYNTHETIC'}, 'batches': [{'batch_id': 'COMPLETE',
                'purpose': 'EXPLORATORY_ENGINEERING_ACCEPTANCE', 'authorization_scope': 'SYNTHETIC',
                'max_requests': 9, 'requests': requests}]}
    queue_path = tmp_path / 'queue.json'
    write(queue_path, queue)
    acquisition = tmp_path / 'acquisition'
    if execute:
        collector.collect(queue_path, acquisition, client=AllDaysClient(conflict=conflict, factor_rows=factor_rows))
    return {'base_manifest': path, 'legacy_root': legacy, 'acquisition_root': acquisition,
        'output_root': tmp_path / 'prepared', 'feature_start': DATES[0],
        'account_start': DATES[1], 'account_end': DATES[-1]}


def two_generation_collection(tmp_path):
    from scripts import prepare_universe_collection_continuation_v1 as continuation
    args = setup(tmp_path, execute=False)
    queue_path = tmp_path / 'queue.json'
    queue = json.loads(queue_path.read_bytes())
    original = queue['batches'][0]
    queue['batches'] = [{**original, 'batch_id': f'BATCH_{index // 3}',
        'requests': original['requests'][index:index + 3]} for index in range(0, 9, 3)]
    write(queue_path, queue)

    def failing_client(fail_at):
        client, calls = AllDaysClient(), []
        for api in ('query_history_k_data_plus', 'query_adjust_factor', 'query_dividend_data'):
            normal = getattr(client, api)
            def request(*, _normal=normal, **query):
                calls.append(query)
                if len(calls) == fail_at:
                    response = Response([], [])
                    response.error_code = '10002007'
                    response.error_msg = 'synthetic receive failure'
                    return response
                return _normal(**query)
            setattr(client, api, request)
        return client

    roots = [args['acquisition_root'], tmp_path / 'continued_1', tmp_path / 'continued_2']
    for index, failure in enumerate((5, 2)):
        with pytest.raises(RuntimeError, match='BAOSTOCK_RESPONSE_FAILED'):
            collector.collect(queue_path, roots[index], client=failing_client(failure))
        plan = tmp_path / f'continuation_{index + 1}'
        continuation.prepare_continuation(roots[index], plan,
            collection_output_root=roots[index + 1], reason='合成完整池续采回归')
        queue_path = plan / 'ACQUISITION_BATCHES.json'
    collector.collect(queue_path, roots[-1], client=AllDaysClient())
    args['acquisition_root'] = roots[-1]
    return args, roots


def test_long_preparation_inherits_completed_and_partial_raw_across_two_continuations(tmp_path):
    args, roots = two_generation_collection(tmp_path)
    originals = {path: path.read_bytes() for root in roots for path in root.rglob('*') if path.is_file()}
    result = prepare_long_horizon_data_v1(**args)
    assert result['target_count'] == 3 and result['corporate_actions']['account_terms_covered_symbol_count'] == 3
    started = json.loads((args['output_root'] / 'PREPARATION_STARTED.json').read_bytes())
    collection = started['collection']
    assert collection['request_count'] == 11 and collection['success_count'] == 9
    assert collection['root_planned_request_count'] == 9 and collection['generation'] == 2
    catalog = json.loads((args['output_root'] / 'RAW_SOURCE_CATALOG.json').read_bytes())
    assert {row['symbol'] for row in catalog['responses']} == set(SYMBOLS)
    assert {str(Path(row['result_path']).parents[2]) for row in catalog['responses']} == {str(roots[0]), str(roots[2])}
    partial = next(row for row in collection['successful_requests']
        if row['request_item']['api'] == 'query_history_k_data_plus' and not row['batch_completed'])
    assert partial['collection_root'] == str(roots[0]) and partial['parent_batch_status'] == 'BLOCKED'
    assert all(path.read_bytes() == content for path, content in originals.items())


def test_long_preparation_still_rejects_incomplete_continuation_leaf(tmp_path):
    args, roots = two_generation_collection(tmp_path)
    args['acquisition_root'] = roots[1]
    with pytest.raises(ValueError, match='LONG_DATA_COLLECTION_NOT_COMPLETE'):
        prepare_long_horizon_data_v1(**args)
    assert not args['output_root'].exists()


def additional_cross_year_catalog(tmp_path, *, cash='0.2'):
    catalog = legacy_catalog(tmp_path, event=action(dividOperateDate='2023-03-03',
        dividRegistDate='2023-03-02', dividPayDate='2023-03-06',
        dividPlanDate='2022-12-30', dividCashPsBeforeTax=cash))
    declaration = json.loads(catalog.read_bytes())
    registration = Path(declaration['datasets'][0]['manifest_path'])
    metadata = json.loads(registration.read_bytes())
    for row in metadata['files'].values():
        row.update(start=20221230, end=20241231)
    write(registration, metadata)
    return catalog, registration


def test_additional_legacy_catalog_preserves_cross_year_physical_bounds_and_both_catalog_identities(tmp_path):
    args = setup(tmp_path)
    catalog, registration = additional_cross_year_catalog(tmp_path / 'supplement')
    duplicate = tmp_path / 'same_sources_catalog.json'
    duplicate.write_bytes(catalog.read_bytes())
    originals = {path: path.read_bytes() for path in (catalog, registration, duplicate,
        args['base_manifest'], Path(json.loads(catalog.read_bytes())['roots']['A']) / 'DIVIDEND_000001.SZ_2023.json')}
    prepare_long_horizon_data_v1(**args, additional_legacy_catalog=[catalog, duplicate])
    merged = json.loads((args['output_root'] / 'LEGACY_ACTION_CATALOG_EXTENDED.json').read_bytes())
    assert merged['original_source_catalog']['sha256'] == sha(args['base_manifest'].parent / 'OLD_ACTION_CATALOG.json')
    assert {row['path'] for row in merged['additional_legacy_catalogs']} == {str(catalog), str(duplicate)}
    assert all(row['datasets'][0]['sha256'] == sha(registration) for row in merged['additional_legacy_catalogs'])
    sources = json.loads((args['output_root'] / 'actions_v1/SOURCE_CATALOG.json').read_bytes())['sources']
    extra = [row for row in sources if row['source_year_type'] == 'report']
    assert len(extra) == 1 and extra[0]['physical_start'] == 20221230 and extra[0]['physical_end'] == 20241231
    assert extra[0]['sha256'] == sha(Path(extra[0]['path']))
    assert all(path.read_bytes() == content for path, content in originals.items())


@pytest.mark.parametrize('changed', ['raw', 'conflicting_query'])
def test_additional_legacy_catalog_changed_or_conflicting_original_is_rejected(tmp_path, changed):
    base = tmp_path / 'base_catalog.json'
    write(base, {'roots': {}, 'datasets': []})
    catalog, _ = additional_cross_year_catalog(tmp_path / 'supplement')
    additions = [catalog]
    if changed == 'raw':
        source = catalog.parent / 'legacy/DIVIDEND_000001.SZ_2023.json'
        source.write_bytes(source.read_bytes() + b' ')
        reason = 'ACTION_RAW_SOURCE_SHA_CHANGED'
    else:
        conflicting, _ = additional_cross_year_catalog(tmp_path / 'conflicting', cash='0.3')
        additions.append(conflicting)
        reason = 'LONG_DATA_ADDITIONAL_LEGACY_IDENTITY_CONFLICT'
    output = tmp_path / 'output'
    output.mkdir()
    with pytest.raises(ValueError, match=reason):
        preparation._merge_legacy_action_catalogs(base, additions, output)
    assert not (output / 'LEGACY_ACTION_CATALOG_EXTENDED.json').exists()


@pytest.mark.parametrize('changed', ['catalog', 'registration'])
def test_additional_registration_changed_during_verification_cannot_receive_old_identity(tmp_path, monkeypatch, changed):
    from scripts import prepare_universe_actions_v1 as actions
    base = tmp_path / 'base_catalog.json'
    write(base, {'roots': {}, 'datasets': []})
    catalog, registration = additional_cross_year_catalog(tmp_path / 'supplement')
    original = actions._read_response
    def read_and_change(item, audit):
        result = original(item, audit)
        target = catalog if changed == 'catalog' else registration
        target.write_bytes(target.read_bytes() + b' ')
        return result
    monkeypatch.setattr(actions, '_read_response', read_and_change)
    output = tmp_path / 'output'
    output.mkdir()
    with pytest.raises(ValueError, match='LONG_DATA_ADDITIONAL_LEGACY_REGISTRATION_CHANGED'):
        preparation._merge_legacy_action_catalogs(base, catalog, output)
    assert not (output / 'LEGACY_ACTION_CATALOG_EXTENDED.json').exists()


def test_full_registered_three_board_data_uses_real_native_raw_and_new_hashes(tmp_path):
    args = setup(tmp_path)
    original = Path(args['base_manifest']).read_bytes()
    result = prepare_long_horizon_data_v1(**args)
    assert result['target_count'] == 3 and result['target_list_unchanged'] is True
    assert result['calendar_sessions'] == 4 and result['account_sessions'] == 3
    assert result['state_rows'] == result['reference_rows'] == 12
    assert result['raw_pairing_conflicts'] == 0
    assert result['corporate_actions']['account_terms_covered_symbol_count'] == 3
    manifest = json.loads(Path(result['manifest_path']).read_text(encoding='utf-8'))
    assert sorted(r['symbol'] for r in manifest['master']['records']) == SYMBOLS
    assert manifest['master']['completeness_evidence'] == {'verified': False}
    assert result['historical_availability'] == 'MODELED' and not result['strategy_qualified']
    states = pd.read_parquet(args['output_root'] / 'states.parquet')
    assert set(states.available_at) == {'MODELED'}
    assert not states.historical_available_at_verified.any()
    assert Path(args['base_manifest']).read_bytes() == original
    assert (args['output_root'] / 'date_resolution/ACTION_DATE_RESOLUTION_RECEIPT.json').is_file()


def test_missing_raw_security_does_not_silently_shrink_registered_pool(tmp_path):
    args = setup(tmp_path, omit='300001.SZ')
    with pytest.raises(ValueError, match='FULL_REGISTERED_POOL'):
        prepare_long_horizon_data_v1(**args)
    assert not args['output_root'].exists()


def test_same_day_tdx_vendor_conflict_is_explicit_not_reference_fabrication(tmp_path):
    args = setup(tmp_path, conflict=True)
    result = prepare_long_horizon_data_v1(**args)
    assert result['target_count'] == 3 and result['raw_pairing_conflicts'] == 1
    assert result['reference_rows'] == 11 and result['state_rows'] == 12
    gaps = json.loads((args['output_root'] / 'RAW_PAIRING_GAPS.json').read_text(encoding='utf-8'))
    assert gaps[0]['symbol'] == '000001.SZ' and gaps[0]['date'] == DATES[1]


def test_new_date_proof_replays_native_collector_market_identity_and_keeps_unknown(tmp_path):
    args = setup(tmp_path, factor_rows=True)
    result = prepare_long_horizon_data_v1(**args)
    output = args['output_root']
    from scripts.resolve_universe_action_dates_v1 import verify_action_date_resolution_receipt_v1
    accepted = verify_action_date_resolution_receipt_v1(
        output / 'date_resolution/ACTION_DATE_RESOLUTION_RECEIPT.json',
        output / 'actions_v1/SOURCE_CATALOG.json', output / 'actions_v1/ACTION_GAPS.json',
        output / 'manifest_actions_stage1.json')
    assert accepted == {(s, DATES[2]) for s in SYMBOLS}
    gaps = json.loads((output / 'actions_v2/PRICE_GAPS.json').read_text(encoding='utf-8'))
    assert {g['effective_date'] for g in gaps} == {DATES[1]}
    assert result['corporate_actions']['price_covered_symbol_count'] == 0


def test_moved_documents_require_exact_hash_and_new_declaration_preserves_old(tmp_path):
    legacy = tmp_path / 'primary' / preparation.PROJECT_ROOT.name
    actual = legacy / 'reports/notice.txt'
    actual.parent.mkdir(parents=True)
    actual.write_text('既有公告原文', encoding='utf-8')
    old = tmp_path / 'removed' / preparation.PROJECT_ROOT.name / 'reports/notice.txt'
    declaration = {'path': str(old), 'sha256': sha(actual), 'published_date': 20230301}
    before = deepcopy(declaration)
    relocation = []
    result = relocate_declarations_v1(declaration, legacy, relocation)
    assert declaration == before and result['path'] == str(actual)
    assert result['sha256'] == before['sha256'] and relocation[0]['same_content_verified']
    actual.write_text('变更正文', encoding='utf-8')
    with pytest.raises(ValueError, match='MISSING_OR_CHANGED'):
        inherited_path_v1(str(old), declaration['sha256'], legacy, [])


def registered_qualification_with_stale_hint(args):
    """复现真实v6清单形状；三个证券仅用于本地合同测试，不冒充全市场验收。"""
    path = args['base_manifest']
    metadata = json.loads(path.read_bytes())
    registered = 'missing_tdx_v1/source_qualification.json'
    original = path.parent / 'SOURCE_QUALIFICATION.json'
    target = path.parent / registered
    target.parent.mkdir(parents=True)
    target.write_bytes(original.read_bytes())
    original.unlink()
    metadata['files'][registered] = metadata['files'].pop('SOURCE_QUALIFICATION.json')
    hint = 'states_v1/PER_STOCK_GAPS.json'
    metadata['source_qualification_ref'] = hint
    hint_path = path.parent / hint
    hint_path.parent.mkdir(parents=True)
    hint_path.write_bytes(b'not registered source qualification; do not read')
    write(path, metadata)
    return metadata, target, hint_path


def test_registered_qualification_kind_overrides_stale_unregistered_hint_and_records_both(tmp_path, monkeypatch):
    args = setup(tmp_path)
    metadata, source, hint = registered_qualification_with_stale_hint(args)
    original_manifest = args['base_manifest'].read_bytes()
    original_qualification = source.read_bytes()
    reads = []
    original_load = preparation._load
    def read_registered(path):
        reads.append(Path(path))
        assert Path(path) != hint, '未登记hint不能成为读取授权'
        return original_load(path)
    monkeypatch.setattr(preparation, '_load', read_registered)
    result = prepare_long_horizon_data_v1(**args)
    assert source in reads and hint not in reads
    assert result['target_count'] == len(SYMBOLS) and result['state_rows'] == 12
    selection = result['source_qualification_selection']
    assert selection['declared_hint'] == 'states_v1/PER_STOCK_GAPS.json'
    assert selection['registered_ref'] == 'missing_tdx_v1/source_qualification.json'
    assert selection['registered_sha256_verified'] is True and selection['declared_hint_read'] is False
    assert selection['original_sha256'] == sha(source)
    final = json.loads(Path(result['manifest_path']).read_bytes())
    registered = final['files']['SOURCE_QUALIFICATION.json']
    assert final['source_qualification_ref'] == 'SOURCE_QUALIFICATION.json'
    assert registered['evidence']['selection_receipt_sha256'] == sha(args['output_root']/'SOURCE_QUALIFICATION_SELECTION.json')
    assert json.loads((args['output_root']/'SOURCE_QUALIFICATION.json').read_bytes()) == json.loads(original_qualification)
    assert args['base_manifest'].read_bytes() == original_manifest and source.read_bytes() == original_qualification


@pytest.mark.parametrize('change,reason', [
    ('changed', 'QUALIFICATION_CHANGED'), ('duplicate_kind', 'SINGLE_REGISTERED_QUALIFICATION_REQUIRED'),
    ('missing_kind', 'SINGLE_REGISTERED_QUALIFICATION_REQUIRED'),
    ('missing_symbol', 'QUALIFICATION_TARGET_MISMATCH'), ('duplicate_symbol', 'QUALIFICATION_TARGET_MISMATCH')])
def test_registered_qualification_still_rejects_changed_ambiguous_or_incomplete_sources(tmp_path, change, reason):
    args = setup(tmp_path)
    metadata, source, hint = registered_qualification_with_stale_hint(args)
    registered = 'missing_tdx_v1/source_qualification.json'
    if change == 'changed':
        source.write_bytes(source.read_bytes() + b' ')
    elif change == 'duplicate_kind':
        metadata['files']['another_qualification.json'] = deepcopy(metadata['files'][registered])
    elif change == 'missing_kind':
        metadata['files'].pop(registered)
    else:
        values = json.loads(source.read_bytes())
        if change == 'missing_symbol': values.pop()
        else: values.append(deepcopy(values[0]))
        write(source, values)
        metadata['files'][registered]['sha256'] = sha(source)
    write(args['base_manifest'], metadata)
    with pytest.raises(ValueError, match=reason):
        prepare_long_horizon_data_v1(**args)
    assert (args['output_root']/'PREPARATION_STARTED.json').exists()
    assert not (args['output_root']/'PREPARATION_COMPLETE.json').exists()
    assert not list(args['output_root'].glob('daily_*.parquet'))
