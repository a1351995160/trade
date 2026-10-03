"""同策略冻结评分顺序、未知分数与元数据篡改。"""
from copy import deepcopy

import pytest

from chanlun_trader.research_factory.universe_selection_v1 import (
    rank_candidates, selection_metadata, selection_sort_key,
)


def test_high_score_larger_code_first_and_equal_score_symbol_tie():
    rows = [{'symbol': '000001.SZ', 'score': 1.}, {'symbol': '600000.SH', 'score': 3.},
        {'symbol': '300001.SZ', 'score': 3.}, {'symbol': '000002.SZ', 'score': None, 'score_ready': False}]
    ranked = rank_candidates(rows, rule_identity='rule', direction='DESCENDING')
    assert [row['symbol'] for row in ranked] == ['300001.SZ', '600000.SH', '000001.SZ', '000002.SZ']
    assert [row['selection']['rank'] for row in ranked] == [1, 2, 3, None]
    assert rows[0].get('selection') is None
    assert ranked == rank_candidates(list(reversed(rows)), rule_identity='rule', direction='DESCENDING')


def test_low_volatility_ascending_and_unknown_never_zero():
    rows = [{'symbol': '000001.SZ', 'score': .02}, {'symbol': '600000.SH', 'score': .01},
        {'symbol': '300001.SZ', 'score': float('inf')}, {'symbol': '300002.SZ', 'score': float('nan')}]
    ranked = rank_candidates(rows, rule_identity='rule', direction='ASCENDING')
    assert [row['symbol'] for row in ranked] == ['600000.SH', '000001.SZ', '300001.SZ', '300002.SZ']
    assert [row['selection']['score'] for row in ranked] == [.01, .02, None, None]


@pytest.mark.parametrize('changes', [{'score': float('nan')}, {'score': True}, {'direction': 'RANDOM'},
    {'tie_breaker': 'RANDOM'}, {'score_ready': True, 'score': None}, {'rank': 0}, {'extra': 1}])
def test_claimed_score_metadata_tampering_rejected(changes):
    metadata = selection_metadata(2., rule_identity='rule', direction='DESCENDING', rank=1)
    metadata.update(changes)
    with pytest.raises(ValueError):
        selection_sort_key(metadata, symbol='000001.SZ')


def test_duplicate_symbols_and_invalid_inputs_rejected():
    with pytest.raises(ValueError, match='SYMBOL'):
        rank_candidates([{'symbol': 'A', 'score': 1.}, {'symbol': 'A', 'score': 2.}],
            rule_identity='rule', direction='DESCENDING')
    with pytest.raises(ValueError):
        selection_metadata(1., rule_identity='rule', direction='DESCENDING', score_ready=1)
    metadata = selection_metadata(None, rule_identity='rule', direction='DESCENDING')
    assert selection_sort_key(deepcopy(metadata), symbol='A') == (1, 0., 'A')
