import json
import hashlib
import pytest

from scripts.prepare_universe_actions_v2 import prepare_universe_actions_v2
from tests.research_factory.test_universe_actions_v1 import fixture, action, prepare


def prepare_v2(manifest, acquisition, **kw):
    prepare(manifest, acquisition)
    if kw.pop('resolve_cash', False):
        catalog = json.loads((manifest.parent/'actions/SOURCE_CATALOG.json').read_text(encoding='utf-8'))
        sources = [s['sha256'] for s in catalog['sources'] if s['kind']=='DIVIDEND' and s['symbol']=='000001.SZ']
        document = manifest.parent/'official.txt'
        document.write_text('年度派0.286；一季派0.091。', encoding='utf-8')
        resolution = manifest.parent/'cash_resolution.json'
        resolution.write_text(json.dumps({'version':'UNIVERSE_CASH_COMPONENT_RESOLUTIONS_V1',
            'resolutions':[{'symbol':'000001.SZ','effective_date':20230105,'record_date':20230104,
                'payment_date':20230106,'source_response_sha256s':sources,
                'document':{'path':str(document),'sha256':hashlib.sha256(document.read_bytes()).hexdigest(),
                            'published_date':20230103,'source_url':'https://example.test/official',
                            'text':{'path':str(document),'sha256':hashlib.sha256(document.read_bytes()).hexdigest(),
                              'original_sha256':hashlib.sha256(document.read_bytes()).hexdigest(),
                              'extraction_policy':'PLAIN_TEXT_IDENTITY'}},
                'components':[{'project_id':'ANNUAL','cash_per_share':.286,'evidence_quote':'年度派0.286'},
                              {'project_id':'Q1','cash_per_share':.091,'evidence_quote':'一季派0.091'}]}]}),encoding='utf-8')
        kw['cash_components'] = resolution
    return prepare_universe_actions_v2(manifest=manifest,
        source_catalog=manifest.parent/'actions/SOURCE_CATALOG.json',
        action_gaps=manifest.parent/'actions/ACTION_GAPS.json',
        output_dir=manifest.parent/'actions_v2', **kw)


def load(manifest, name):
    return json.loads((manifest.parent/'actions_v2'/name).read_text(encoding='utf-8'))


def test_distinct_same_day_dividends_combine_and_preserve_components(tmp_path):
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [
        action(dividCashPsBeforeTax='.286'), action(dividCashPsBeforeTax='.091')]})
    result = prepare_v2(manifest, acquisition, resolve_cash=True)
    assert result['merged_cash_group_count'] == 1
    assert result['price_covered_symbol_count'] == result['account_terms_covered_symbol_count'] == 2
    events = load(manifest, 'EVENTS.json')
    assert len(events) == 1 and events[0]['terms']['cash_per_share'] == .377
    assert [e['terms']['cash_per_share'] for e in events[0]['cash_components']] == [.286, .091]
    assert load(manifest,'PRICE_GAPS.json') == []


def test_two_cash_values_do_not_prove_distinct_projects(tmp_path):
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [
        action(dividCashPsBeforeTax='.286'), action(dividCashPsBeforeTax='.091')]})
    result = prepare_v2(manifest, acquisition)
    assert result['merged_cash_group_count'] == 0
    assert result['price_covered_symbol_count'] == 1
    assert load(manifest, 'PRICE_GAPS.json')[0]['reason'] == 'SAME_DATE_CASH_DISTINCT_PROJECT_EVIDENCE_REQUIRED'


@pytest.mark.parametrize('mutation,reason', [('quote','QUOTE_NOT_IN_BOUND_TEXT'),
                                           ('text','DOCUMENT_TEXT_CHANGED')])
def test_source_bound_cash_resolution_rejects_wrong_quote_or_changed_text(tmp_path, mutation, reason):
    from scripts.prepare_universe_actions_v2 import _cash_resolutions
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [
        action(dividCashPsBeforeTax='.286'), action(dividCashPsBeforeTax='.091')]})
    prepare_v2(manifest, acquisition, resolve_cash=True)
    path = manifest.parent/'cash_resolution.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    if mutation == 'quote':
        value['resolutions'][0]['components'][0]['evidence_quote'] = '另外一个方案也发红利'
        path.write_text(json.dumps(value, ensure_ascii=False),encoding='utf-8')
    else:
        (manifest.parent/'official.txt').write_text('已变成另一份公告',encoding='utf-8')
        # PDF原件和文本通常为两个文件；此fixture用同一文本原件，保持原件hash校验独立通过。
        record = value['resolutions'][0]
        record['document']['sha256'] = hashlib.sha256((manifest.parent/'official.txt').read_bytes()).hexdigest()
        record['document']['text']['original_sha256'] = record['document']['sha256']
        path.write_text(json.dumps(value, ensure_ascii=False),encoding='utf-8')
    with pytest.raises(ValueError, match=reason):
        _cash_resolutions(path)


def test_share_prices_can_be_known_while_account_terms_remain_unknown(tmp_path):
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [action(
        dividReserveToStockPs='.3', dividStockMarketDate='2023-01-09')]})
    result = prepare_v2(manifest, acquisition)
    assert result['price_covered_symbol_count'] == 2
    assert result['account_terms_covered_symbol_count'] == 1
    events = load(manifest,'EVENTS.json')
    share = next(e for e in events if e['event_type']=='CAPITALIZATION')
    assert (share['terms']['ratio_numerator'], share['terms']['ratio_denominator']) == (13,10)
    assert share['terms']['tax_rule']['kind'] == 'UNKNOWN'
    assert share['share_credit_date'] is None
    assert load(manifest,'ACCOUNT_TERMS_GAPS.json')
    assert not result['account_executed'] and not result['budget_created']


def test_missing_cash_is_not_invented_as_zero_for_shares(tmp_path):
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [action(
        dividReserveToStockPs='.3', dividStockMarketDate='2023-01-09',dividCashPsBeforeTax='')]})
    result = prepare_v2(manifest, acquisition)
    assert result['price_covered_symbol_count'] == 1
    assert load(manifest,'EVENTS.json') == []


def test_pure_share_event_accepts_explicit_zero_cash_without_creating_cash_event(tmp_path):
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [action(
        dividReserveToStockPs='.3', dividStockMarketDate='2023-01-09',dividCashPsBeforeTax='0')]})
    result = prepare_v2(manifest, acquisition)
    assert result['price_covered_symbol_count'] == 2
    assert [e['event_type'] for e in load(manifest,'EVENTS.json')] == ['CAPITALIZATION']


def test_same_date_cash_with_different_payment_dates_is_not_blindly_summed(tmp_path):
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [
        action(dividCashPsBeforeTax='.286'), action(dividCashPsBeforeTax='.091',dividPayDate='2023-01-09')]})
    result = prepare_v2(manifest, acquisition)
    assert result['price_covered_symbol_count'] == 1
    assert load(manifest,'EVENTS.json') == []
