"""同源能力与三板块说明不得冒用旧验收凭证或缓存完整性。"""
from copy import deepcopy

import pytest

from chanlun_trader.research_factory.research_capabilities_v1 import (
    capabilities, full_universe_capabilities_v1, render_markdown, require_current,
)
from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3, rule_capabilities


def test_three_boards_use_identical_v3_indicator_catalog_and_business_capabilities():
    snapshot = capabilities()
    scope = snapshot['full_universe']
    rules = rule_capabilities()
    assert scope['indicator_ids'] == [item['id'] for item in rules['indicators']]
    assert scope['indicator_count'] == len(rules['indicators'])
    assert {item['id'] for item in scope['boards']} == {'SZ_MAIN', 'SH_MAIN', 'CHINEXT'}
    assert len({tuple(item['supported_features']) for item in scope['boards']}) == 1
    assert all(item['indicator_catalog_sha256'] == rules['catalog_sha256'] for item in scope['boards'])
    assert scope['selection_policy'] == 'ALL_REGISTERED_TARGETS_THEN_STRATEGY_SIGNALS'
    assert scope['cash_policy'] == 'ONE_SHARED_ACCOUNT_ACROSS_BOARDS'


def test_new_path_has_separate_unaccepted_engineering_real_and_strategy_states():
    scope = full_universe_capabilities_v1()
    assert scope['engineering_evidence'] == 'ENGINEERING_NOT_ACCEPTED'
    assert scope['real_evidence'] == 'REAL_NOT_ACCEPTED'
    assert scope['strategy_qualified'] is False
    assert all(item['real_evidence'] == 'REAL_NOT_ACCEPTED' for item in scope['boards'])
    assert {'atr_stop', 'volatility_rank', 'STAR', 'BSE'} <= set(scope['unsupported'])
    assert all(len(digest) == 64 for digest in scope['source_hashes'].values())


def test_cached_market_size_does_not_become_qualified_or_complete_by_display():
    catalog = {'datasets': [{'dataset_id': 'cached_market', 'target_count': 4536,
        'completeness': 'UNIVERSE_COMPLETENESS_UNKNOWN', 'data_qualification': 'CONTENT_NOT_VALIDATED',
        'by_board': {'SZ_MAIN': {'target_count': 1478}, 'SH_MAIN': {'target_count': 1681},
                     'CHINEXT': {'target_count': 1377}}}]}
    snapshot = capabilities(data_catalog=catalog)
    assert snapshot['data']['datasets'][0]['data_qualification'] == 'CONTENT_NOT_VALIDATED'
    assert snapshot['data']['datasets'][0]['completeness'] == 'UNIVERSE_COMPLETENESS_UNKNOWN'
    catalog['datasets'][0]['target_count'] = 8
    assert snapshot['data']['datasets'][0]['target_count'] == 4536
    with pytest.raises(ValueError, match='CAPABILITY_SNAPSHOT_STALE'):
        require_current(snapshot['fingerprint'], data_catalog=catalog)


def test_multitype_indicator_example_is_executable_v3_and_filter_is_same_symbol():
    snapshot = capabilities()
    example = snapshot['examples']['multi_indicator']
    strategy = ResearchRuleStrategyV3(example, strategy_id='DOCUMENTATION_MULTITYPE')
    assert len({item['id'] for item in example['indicator_instances']}) >= 3
    assert example['market_filter']['args'][0]['args'] == ['volume']
    assert set(strategy.requirements.fields) >= {'close', 'volume'}
    assert '同证券' in snapshot['full_universe']['market_filter_scope']
    forged = deepcopy(example)
    forged['market_filter'] = forged['buy']
    with pytest.raises(ValueError, match='RULE_INDICATOR_UNSUPPORTED'):
        ResearchRuleStrategyV3(forged, strategy_id='NOT_A_MARKET_INDEX')


def test_chinese_generated_guide_explains_unknowns_and_separate_board_acceptance():
    text = render_markdown()
    assert '创业板' in text and '深圳主板' in text and '上海主板' in text
    assert 'FULL_UNIVERSE_SUBMISSION_V1' in text
    assert '缓存股票数' in text and '历史长度不足' in text
    assert 'REAL_NOT_ACCEPTED' in text and 'ENGINEERING_NOT_ACCEPTED' in text
    assert '不手填缩小股票名单' in text
    assert 'market_filter只引用同一股票' in text
