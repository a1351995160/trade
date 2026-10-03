"""冻结收盘评分与同策略确定性顺序；未知评分不能变成零分。"""
from copy import deepcopy
import math

import pandas as pd

from ..engine.conditions_v2 import ConditionContext, evaluate_condition


VERSION = 'UNIVERSE_SELECTION_V1'
DIRECTIONS = {'ASCENDING', 'DESCENDING'}
TIE_BREAKER = 'SYMBOL_ASCENDING'


def evaluate_score_series(expression, matrix, *, references):
    """计算因果数值表达式；结果有限且依赖就绪才有排序资格。"""
    values, ready = {}, {}
    for alias, output in references:
        key = f'{alias}.{output}'
        values[key] = pd.to_numeric(matrix.get(key, pd.Series(float('nan'), index=matrix.index)), errors='coerce')
        mask = matrix.get(key + '__ready', pd.Series(False, index=matrix.index))
        ready[key] = mask.map(lambda value: isinstance(value, bool) and value)
    from .research_rule_strategy_v2 import FIELDS, _field_references
    fields = {key: pd.to_numeric(matrix.get(key, pd.Series(float('nan'), index=matrix.index)), errors='coerce')
              for key in FIELDS}
    context = ConditionContext(values, fields, matrix.index, ready=ready)
    score = evaluate_condition(expression, context)
    mask = score.map(lambda value: math.isfinite(float(value)))
    for key in values:
        mask &= ready[key] & values[key].map(lambda value: math.isfinite(float(value)))
    for key in _field_references(expression):
        mask &= fields[key].map(lambda value: math.isfinite(float(value)))
    return score.where(mask), mask


def selection_metadata(score, *, rule_identity, direction, score_ready=None, rank=None):
    if not isinstance(direction, str) or direction not in DIRECTIONS or not isinstance(rule_identity, str) or not rule_identity:
        raise ValueError('UNIVERSE_SELECTION_IDENTITY_INVALID')
    finite = type(score) in (int, float) and math.isfinite(score)
    if score_ready is not None and type(score_ready) is not bool:
        raise ValueError('UNIVERSE_SCORE_READINESS_INVALID')
    known = finite and score_ready is not False
    if rank is not None and (type(rank) is not int or rank < 1 or not known):
        raise ValueError('UNIVERSE_SELECTION_RANK_INVALID')
    return {'version': VERSION, 'rule_identity': rule_identity,
            'score': float(score) if known else None, 'score_ready': known,
            'direction': direction, 'tie_breaker': TIE_BREAKER, 'rank': rank}


def selection_sort_key(metadata, *, symbol):
    """调用方先按 SELL、成员优先级、strategy_id 分组，再使用本键。"""
    if not isinstance(metadata, dict) or set(metadata) != {
            'version', 'rule_identity', 'score', 'score_ready', 'direction', 'tie_breaker', 'rank'}:
        raise ValueError('UNIVERSE_SELECTION_METADATA_FIELDS')
    if (metadata['version'] != VERSION or not isinstance(metadata['direction'], str) or metadata['direction'] not in DIRECTIONS
            or metadata['tie_breaker'] != TIE_BREAKER or not isinstance(symbol, str) or not symbol):
        raise ValueError('UNIVERSE_SELECTION_METADATA_INVALID')
    canonical = selection_metadata(metadata['score'], rule_identity=metadata['rule_identity'],
        direction=metadata['direction'], score_ready=metadata['score_ready'], rank=metadata['rank'])
    if canonical != metadata:
        raise ValueError('UNIVERSE_SELECTION_METADATA_INVALID')
    if not metadata['score_ready']:
        return (1, 0., symbol)
    score = metadata['score'] if metadata['direction'] == 'ASCENDING' else -metadata['score']
    return (0, score, symbol)


def rank_candidates(records, *, rule_identity, direction):
    """输入为同策略同收盘证券记录；保留未知行但不给它名次。"""
    if not isinstance(records, (list, tuple)) or any(not isinstance(row, dict) for row in records):
        raise ValueError('UNIVERSE_SELECTION_RECORDS_INVALID')
    result, symbols = [], set()
    for row in records:
        symbol = row.get('symbol')
        if not isinstance(symbol, str) or not symbol or symbol in symbols:
            raise ValueError('UNIVERSE_SELECTION_SYMBOL_INVALID')
        symbols.add(symbol)
        item = deepcopy(row)
        item['selection'] = selection_metadata(row.get('score'), rule_identity=rule_identity,
            direction=direction, score_ready=row.get('score_ready'))
        result.append(item)
    result.sort(key=lambda row: selection_sort_key(row['selection'], symbol=row['symbol']))
    for rank, row in enumerate(result, 1):
        if row['selection']['score_ready']:
            row['selection']['rank'] = rank
    return result
