"""V4 增加冻结数值评分；买卖就绪与评分就绪独立。"""
from copy import copy, deepcopy
from pathlib import Path
import math
import re

import pandas as pd

from chanlun_trader.engine.daily_exit_v2 import DailyExitRuleSetV2
from . import research_rule_strategy_v2 as v2
from . import research_rule_strategy_v3 as v3
from .common import stable_hash
from .strategy_interface_v1 import Decision, Requirements
from .universe_selection_v1 import DIRECTIONS, TIE_BREAKER, evaluate_score_series, selection_metadata


CAPABILITY = 'RESEARCH_RULE_STRATEGY_V4'
ARITHMETIC = {'add', 'sub', 'mul', 'div'}


def _parse_score(node, catalog, *, level=1, counter=None):
    counter = [0] if counter is None else counter
    counter[0] += 1
    if level > 6 or counter[0] > 64:
        raise ValueError('RULE_EXPRESSION_LIMIT')
    if not isinstance(node, dict) or set(node) != {'op', 'args', 'params'}:
        raise ValueError('RULE_EXPRESSION_FIELDS')
    name, args, params = node['op'], node['args'], node['params']
    if not isinstance(name, str) or not isinstance(args, list) or not isinstance(params, dict):
        raise ValueError('RULE_EXPRESSION_TYPES')
    if name not in ARITHMETIC | {'const', 'field', 'indicator', 'ref'}:
        raise ValueError('RULE_SCORE_OPERATOR_UNSUPPORTED')
    if name in {'const', 'field', 'indicator'}:
        return v2._parse(node, catalog)
    if name == 'ref':
        if len(args) != 1 or set(params) != {'periods'}:
            raise ValueError('RULE_REF_PARAMETERS')
        v2._integer(params['periods'], 0, 60, 'periods')
    elif len(args) != 2 or params:
        raise ValueError('RULE_SCORE_OPERATOR_PARAMETERS')
    children = tuple(_parse_score(child, catalog, level=level + 1, counter=counter) for child in args)
    return v2.Expr(name, children, dict(params))


def validate_rule_payload(payload):
    expected = v2.PAYLOAD_FIELDS | {'indicator_instances', 'exits', 'selection'}
    if not isinstance(payload, dict) or set(payload) != expected or payload['version'] != CAPABILITY:
        raise ValueError('RULE_PAYLOAD_FIELDS_OR_VERSION')
    selection = payload['selection']
    if (not isinstance(selection, dict) or set(selection) != {'score', 'direction', 'tie_breaker'}
            or not isinstance(selection['direction'], str) or selection['direction'] not in DIRECTIONS
            or selection['tie_breaker'] != TIE_BREAKER):
        raise ValueError('RULE_SELECTION_FIELDS_OR_DIRECTION')
    base = {key: value for key, value in payload.items() if key != 'selection'}
    base['version'] = v3.CAPABILITY
    canonical = v3.validate_rule_payload(base)
    catalog = {item['instance_id']: item for item in v3._instances(canonical['indicator_instances'])}
    score = _parse_score(selection['score'], catalog)
    canonical['version'] = CAPABILITY
    canonical['selection'] = {**selection, 'score': score.to_dict()}
    return canonical


def rule_capabilities():
    value = deepcopy(v3.rule_capabilities())
    value.update(version=CAPABILITY, selection={'operators': sorted(ARITHMETIC | {'const', 'field', 'indicator', 'ref'}),
        'directions': sorted(DIRECTIONS), 'tie_breaker': TIE_BREAKER,
        'unknown_score': 'SCORE_UNKNOWN', 'scope': 'SAME_STRATEGY_COMPLETED_CLOSE',
        'qualification': 'EXPLORATORY_ONLY'})
    return value


class ResearchRuleStrategyV4(v3.ResearchRuleStrategyV3):
    source_files = (str(Path(__file__).resolve()), *v3.ResearchRuleStrategyV3.source_files,
                    str(Path(__file__).with_name('universe_selection_v1.py')))

    def __init__(self, payload, *, strategy_id):
        if not isinstance(strategy_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', strategy_id):
            raise ValueError('RULE_STRATEGY_ID_INVALID')
        self.strategy_id = strategy_id
        self.payload = validate_rule_payload(payload)
        instances = {item['instance_id']: item for item in v3._instances(self.payload['indicator_instances'])}
        self.expressions = {key: v2._parse(value, instances, boolean=True, market=key == 'market_filter')
                            for key in ('buy', 'sell', 'market_filter') if (value := self.payload[key]) is not None}
        self.selection = deepcopy(self.payload['selection'])
        self.score_expression = _parse_score(self.selection['score'], instances)
        self.condition_references = sorted({ref for expr in self.expressions.values() for ref in v2._references(expr)})
        self.score_references = sorted(set(v2._references(self.score_expression)))
        self.references = sorted(set(self.condition_references) | set(self.score_references))
        if {alias for alias, _ in self.references} != set(instances):
            raise ValueError('RULE_UNUSED_INSTANCE')
        self.condition_fields = sorted({field for expr in self.expressions.values() for field in v2._field_references(expr)})
        self.score_fields = sorted(set(v2._field_references(self.score_expression)))
        fields = set(self.condition_fields) | set(self.score_fields)
        for item in instances.values():
            fields.update(item['inputs'])
            fields.update('turn' if name == 'vendor_turn' else name for name in item['extra_data'])
        fields.add('close')
        self.condition_warmup_sessions = max(v3._warmup(expr, instances) for expr in self.expressions.values())
        self.score_warmup_sessions = v3._warmup(self.score_expression, instances)
        self.requirements = Requirements('A_SHARE', '1D', 'CAUSAL_HFQ_FEATURE_RAW_EXECUTION', tuple(sorted(fields)),
            max(self.condition_warmup_sessions, self.score_warmup_sessions), ('TARGET_WEIGHT',),
            capabilities=(CAPABILITY, 'EXECUTION_STATE', 'PERSONAL_CASH_DIVIDEND'))
        self.exit_policy = v3.exit_policy()
        self.exit_rules = DailyExitRuleSetV2(**{key: value for key, value in self.payload['exits'].items() if key != 'execution_mode'})
        rule = {key: value for key, value in self.payload.items() if key not in {'hypothesis', 'change_reason'}}
        catalog_hash = rule_capabilities()['catalog_sha256']
        self.rule_identity = stable_hash({'rule': rule, 'catalog_sha256': catalog_hash, 'exit_policy': self.exit_policy})
        self.definition = {'strategy_id': strategy_id, 'catalog_sha256': catalog_hash, 'rule_identity': self.rule_identity,
            'rule': rule, 'indicators': list(instances.values()), 'exit_policy': self.exit_policy}
        self.parameters = {'rule_definition': deepcopy(self.definition), 'candidate_payload': deepcopy(self.payload),
            'rule_identity': self.rule_identity, 'factor_ids': sorted({item['id'] for item in instances.values()})}

    def validate(self):
        super().validate()
        expected = type(self)(self.payload, strategy_id=self.strategy_id)
        if any(getattr(self, key) != getattr(expected, key) for key in (
                'selection', 'score_expression', 'condition_references', 'score_references',
                'condition_fields', 'score_fields', 'condition_warmup_sessions', 'score_warmup_sessions')):
            raise ValueError('RULE_CHANGED_AFTER_FREEZE')

    def evaluate_selection(self, matrix):
        return evaluate_score_series(self.score_expression, matrix, references=self.score_references)

    def condition_readiness(self, matrix):
        ready = pd.Series(True, index=matrix.index)
        for alias, output in self.condition_references:
            key = f'{alias}.{output}'
            values = matrix.get(key, pd.Series(float('nan'), index=matrix.index))
            mask = matrix.get(key + '__ready', pd.Series(False, index=matrix.index))
            ready &= mask.eq(True) & values.map(lambda value: math.isfinite(float(value)))
        for field in self.condition_fields:
            values = matrix.get(field, pd.Series(float('nan'), index=matrix.index))
            ready &= values.map(lambda value: math.isfinite(float(value)))
        return ready

    def on_signal_close(self, context):
        # 原状态/持有/退出守卫只读取买卖依赖，不让评分专用预热吞掉买入机会。
        condition_view = copy(self)
        condition_view.references = self.condition_references
        decision = v2.ResearchRuleStrategyV2.on_close(condition_view, context)
        score, score_ready = self.evaluate_selection(context.history)
        value = float(score.iloc[-1]) if bool(score_ready.iloc[-1]) else None
        condition_ready = bool(self.condition_readiness(context.history).iloc[-1])
        condition_ready &= all(type(value) is bool for value in decision.metadata.values())
        metadata = {**decision.metadata, 'condition_ready': condition_ready,
            'selection': selection_metadata(value, rule_identity=self.rule_identity, direction=self.selection['direction'])}
        metadata['raw_signal'] = bool(metadata['condition_ready'] and metadata.get('buy') is True
            and metadata.get('sell') is False and metadata.get('market_filter', True) is True)
        if decision.reason == 'BUY' and not metadata['selection']['score_ready']:
            return Decision('SCORE_UNKNOWN', None, decision.state, metadata)
        return Decision(decision.reason, decision.intent, decision.state, metadata)
