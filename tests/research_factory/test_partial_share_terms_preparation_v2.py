import hashlib
import json

import pytest

from scripts.prepare_universe_actions_v2 import prepare_universe_actions_v2
from tests.research_factory.test_universe_actions_v1 import fixture, action, prepare


def share_declaration(manifest, *, credit=20230105, trade_evidence=None):
    """公告证明到账，vendor 原件独立证明上市日。"""
    catalog = json.loads((manifest.parent/'actions/SOURCE_CATALOG.json').read_text(encoding='utf-8'))
    source = next(s for s in catalog['sources'] if s['kind'] == 'DIVIDEND' and s['symbol'] == '000001.SZ')
    document = manifest.parent/'share_notice.txt'
    document.write_text('本次转增来自股本溢价。新股份于20230105直接记入账户。', encoding='utf-8')
    digest = hashlib.sha256(document.read_bytes()).hexdigest()
    record = {'symbol': '000001.SZ', 'effective_date': 20230105,
        'source_response_sha256': source['sha256'],
        'document': {'path': str(document), 'sha256': digest, 'published_date': 20230103,
            'source_url': 'https://example.test/share_notice',
            'text': {'path': str(document), 'sha256': digest, 'original_sha256': digest,
                     'extraction_policy': 'PLAIN_TEXT_IDENTITY'}},
        'tax_rule': {'kind': 'CAPITALIZATION_SHARE_PREMIUM_EXEMPT',
                     'source': 'https://example.test/share_notice', 'document_sha256': digest,
                     'evidence_quote': '本次转增来自股本溢价。'},
        'share_credit_date': credit,
        'date_evidence': {'share_credit_date': {'kind': 'SOURCE',
            'source': 'https://example.test/share_notice',
            'evidence_quote': '新股份于20230105直接记入账户。'}}}
    if trade_evidence is not None:
        record['tradable_date'] = None if trade_evidence['kind'] == 'UNKNOWN' else 20230110
        record['date_evidence']['tradable_date'] = trade_evidence
    path = manifest.parent/'partial_share_terms.json'
    path.write_text(json.dumps({'version': 'UNIVERSE_SHARE_TERMS_RESOLUTIONS_V1',
                               'resolutions': [record]}, ensure_ascii=False), encoding='utf-8')
    return path, source


def setup_share(tmp_path, **kwargs):
    manifest, acquisition = fixture(tmp_path, by_code={'sz.000001': [action(
        dividReserveToStockPs='.3', dividStockMarketDate='2023-01-09', dividCashPsBeforeTax='0')]})
    prepare(manifest, acquisition)
    declaration, source = share_declaration(manifest, **kwargs)
    return manifest, declaration, source


def materialize(manifest, declaration):
    return prepare_universe_actions_v2(manifest=manifest,
        source_catalog=manifest.parent/'actions/SOURCE_CATALOG.json',
        action_gaps=manifest.parent/'actions/ACTION_GAPS.json',
        output_dir=manifest.parent/'actions_v2', share_terms=declaration)


def load(manifest, name):
    return json.loads((manifest.parent/'actions_v2'/name).read_text(encoding='utf-8'))


@pytest.mark.parametrize('trade_evidence', [None, {'kind': 'UNKNOWN', 'source': ''},
    {'kind': 'MODELED', 'source': 'explicit_model_not_a_new_listing_source'}])
def test_partial_terms_preserve_independent_vendor_listing_source(tmp_path, trade_evidence):
    manifest, declaration, source = setup_share(tmp_path, trade_evidence=trade_evidence)
    result = materialize(manifest, declaration)
    event, = load(manifest, 'EVENTS.json')
    assert event['share_credit_date'] == 20230105
    assert event['tradable_date'] == 20230109
    assert event['date_evidence']['tradable_date'] == {'kind': 'SOURCE', 'source': source['path']}
    assert event['terms_resolution_applied_fields'] == ['share_credit_date', 'tax_rule']
    assert load(manifest, 'ACCOUNT_TERMS_GAPS.json') == []
    assert result['account_terms_covered_symbol_count'] == 2
    assert not result['account_executed'] and not result['budget_created']


def test_partial_tax_proof_does_not_default_missing_credit_date(tmp_path):
    manifest, declaration, _ = setup_share(tmp_path)
    value = json.loads(declaration.read_text(encoding='utf-8'))
    value['resolutions'][0].pop('share_credit_date')
    value['resolutions'][0]['date_evidence'] = {}
    declaration.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    result = materialize(manifest, declaration)
    event, = load(manifest, 'EVENTS.json')
    assert event['share_credit_date'] is None
    assert event['tradable_date'] == 20230109
    assert load(manifest, 'ACCOUNT_TERMS_GAPS.json')[0]['missing_information'] == ['SHARE_CREDIT_DATE_EVIDENCE']
    assert result['account_terms_covered_symbol_count'] == 1


def test_claim_without_date_value_fails_instead_of_erasing_source(tmp_path):
    manifest, declaration, _ = setup_share(tmp_path, credit=None)
    result = materialize(manifest, declaration)
    assert load(manifest, 'EVENTS.json') == []
    assert load(manifest, 'PRICE_GAPS.json')[0]['reason'] == 'SHARE_TERMS_PROVEN_DATE_REQUIRED'
    assert result['account_terms_covered_symbol_count'] == 1


def test_partial_credit_proof_still_requires_tax_evidence(tmp_path):
    manifest, declaration, source = setup_share(tmp_path)
    value = json.loads(declaration.read_text(encoding='utf-8'))
    value['resolutions'][0]['tax_rule'] = {'kind': 'UNKNOWN', 'source': ''}
    declaration.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    result = materialize(manifest, declaration)
    event, = load(manifest, 'EVENTS.json')
    assert event['share_credit_date'] == 20230105
    assert event['date_evidence']['tradable_date']['source'] == source['path']
    assert load(manifest, 'ACCOUNT_TERMS_GAPS.json')[0]['missing_information'] == ['CAPITAL_RESERVE_ORIGIN_OR_BONUS_TAX_RULE']
    assert result['account_terms_covered_symbol_count'] == 1
