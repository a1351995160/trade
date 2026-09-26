"""冻结有界探索档案与观察准入；探索评审不授予正式统计资格。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

from .bounded_research_v1 import BoundedResearchSessionV1, _put, _read
from .common import stable_hash
from .durability import _atomic_write
from .exploration_governance import read_json
from .mutation_boundary import ObjectiveMutationLock
from .research_diagnostics_v1 import diagnose, feedback_view


def _require(condition, code):
    if not condition:
        raise ValueError('STRATEGY_ARCHIVE_' + code)


def _without(value, key):
    return {k: v for k, v in value.items() if k != key}


def _stamp(value):
    stamp = datetime.fromisoformat(value)
    _require(stamp.tzinfo is not None, 'TIMEZONE_REQUIRED')
    return stamp


class BoundedStrategyArchiveV1:
    """本地权威目录持有证据；公开准入不接受调用方的资格布尔值。"""

    def __init__(self, root):
        self.root = Path(root).absolute()
        _require(self.root.resolve() == self.root, 'PATH_REDIRECTED')

    def _path(self, strategy_id, name):
        _require(isinstance(strategy_id, str) and re.fullmatch(r'BS_[0-9a-f]{64}', strategy_id), 'ID_INVALID')
        path = self.root / strategy_id / name
        _require(path.resolve() == path and path.is_relative_to(self.root), 'PATH_REDIRECTED')
        return path

    def _lock(self, strategy_id):
        return ObjectiveMutationLock.for_resource(self._path(strategy_id, 'ARCHIVE.json'))

    @staticmethod
    def _validate(evidence, origin):
        scope, candidate = evidence['session'], evidence['candidate']
        name = origin['candidate_id']
        _require(re.fullmatch(r'CANDIDATE_00[1-5]', name), 'CANDIDATE_REQUIRED')
        _require(scope['scope_id'] == stable_hash(_without(scope, 'scope_id'))
                 and origin['scope_id'] == scope['scope_id'] == candidate['scope_id'], 'SCOPE_CONFLICT')
        _require(scope['version'] == 'BOUNDED_RESEARCH_V1' and scope['purpose'] == 'EXPLORATION_ONLY'
                 and scope['qualification'] == 'NOT_ASSESSED'
                 and scope['input_manifest']['profile'] in ('SYNTHETIC', 'S1_MODELED_DAILY'), 'SCOPE_INVALID')
        _require(int(name[-3:]) <= scope['max_attempts']
                 and {'MATERIALIZE', 'BACKTEST'} <= set(scope['actions']), 'SCOPE_ACTION_INVALID')
        plan, result = candidate['plan'], evidence['result']
        _require(plan['plan_id'] == stable_hash(_without(plan, 'plan_id'))
                 and plan['strategy']['strategy_id'] == name
                 and result['strategy_plan'] == plan, 'PLAN_CONFLICT')
        _require(result['input_identity'] == scope['input_manifest']['input_identity'], 'INPUT_CONFLICT')
        receipt, start, settlement = (evidence[k] for k in ('receipt', 'start', 'settlement'))
        expected_source = {'origin': 'BOUNDED_RESEARCH_SCOPE', 'scope_root': origin['source_root'],
                           'scope_id': scope['scope_id'], 'candidate_id': name, 'expires_at': scope['expires_at']}
        _require(receipt['receipt_id'] == stable_hash(_without(receipt, 'receipt_id'))
                 and receipt['source'] == expected_source
                 and receipt['strategy_plans'] == {name: plan}
                 and receipt['objective_id'] == scope['objective_id']
                 and receipt['input_identity'] == result['input_identity']
                 and receipt['budget_path'] == str(Path(origin['source_root']) / 'search_budget_registry.json')
                 and receipt['purposes'] == [name] and receipt['expires_at'] == scope['expires_at']
                 and receipt['exposures'] == 1 and receipt['repair_allowance'] == 0
                 and receipt['novelty'][name]['allowed'] is True
                 and receipt['novelty'][name]['plan_id'] == plan['plan_id'], 'RECEIPT_CONFLICT')
        _require(start['kind'] == name and start['receipt_id'] == receipt['receipt_id']
                 and start['counted_before_account_calculation'] is True
                 and settlement['completed'] is True and settlement['error'] is None
                 and all(settlement[k] == v for k, v in start.items())
                 and settlement['result_sha256'] == stable_hash(result), 'SETTLEMENT_CONFLICT')
        _require(_stamp(scope['recorded_at']) <= _stamp(candidate['frozen_at'])
                 <= _stamp(receipt['recorded_at']) <= _stamp(start['started_at'])
                 <= _stamp(settlement['settled_at'])
                 and _stamp(start['started_at']) < _stamp(scope['expires_at']), 'TIMELINE_CONFLICT')
        attempt, budget = evidence['attempt'], evidence['budget']
        reservation = 'TRIAL-' + stable_hash({'objective_id': scope['objective_id'], 'batch_id': scope['scope_id'],
                                             'family_id': 'SEARCH', 'candidate_id': name})[:20]
        _require(attempt == {'reservation': reservation, 'scope_id': scope['scope_id']}
                 and budget['objective_id'] == scope['objective_id'], 'ATTEMPT_CONFLICT')
        settled, active = budget['settled_reservations'], budget['active_reservations']
        _require(all(settled.get(key) == 'CONSUMED' and key not in active
                     for key in (reservation, start['reservation'])), 'BUDGET_NOT_CONSUMED')
        buckets = {(b['kind'], b['key']): b for b in budget['buckets']}
        _require(len(buckets) == len(budget['buckets']), 'BUDGET_DUPLICATE')
        for row in buckets.values():
            _require(all(type(row[k]) is int and row[k] >= 0 for k in ('used', 'reserved', 'limit'))
                     and row['used'] + row['reserved'] <= row['limit'], 'BUDGET_INVALID')
        from .etf_account_governance_v1 import StrategyBatchGovernanceV1
        for key in [('candidate', name), (StrategyBatchGovernanceV1.budget_kind, receipt['receipt_id'] + ':' + name)]:
            _require(key in buckets and buckets[key]['used'] == buckets[key]['limit'] == 1
                     and buckets[key]['reserved'] == 0, 'BUDGET_BINDING_CONFLICT')
        for key, limit in [(('objective', scope['objective_id']), scope['max_attempts'] + 1),
                           (('batch', scope['scope_id']), scope['max_attempts'] + 1),
                           (('family', 'SEARCH'), scope['max_attempts'])]:
            _require(key in buckets and buckets[key]['used'] >= 1 and buckets[key]['limit'] == limit,
                     'BUDGET_BINDING_CONFLICT')
        registry = evidence['trials']
        events = registry['events']
        _require(registry['registry_hash'] == stable_hash(events) and registry['event_count'] == len(events),
                 'TRIAL_REGISTRY_CONFLICT')
        for event in events:
            _require(event['payload_hash'] == stable_hash({**event, 'payload_hash': ''}), 'TRIAL_HASH_CONFLICT')
        own = [e for e in events if e['trial_id'] == name]
        _require(all(e['candidate_id'] == name and e['candidate_preregistration_hash'] == plan['plan_id']
                     and e['classification'] is None and e['created_at'] == candidate['frozen_at']
                     for e in own), 'TRIAL_BINDING_CONFLICT')
        kinds = [e['event_type'] for e in own]
        _require(kinds == ['REGISTERED_BEFORE_PERFORMANCE', 'PERFORMANCE_ACCESSED', 'EXPLORATION_COMPLETED']
                 and own[0]['performance_accessed'] is False
                 and all(e['performance_accessed'] is True for e in own[1:]), 'TRIAL_SEQUENCE_CONFLICT')
        diagnostic = evidence['diagnostic']
        _require(diagnostic['diagnostic_hash'] == stable_hash(_without(diagnostic, 'diagnostic_hash'))
                 and diagnostic['trial_id'] == name and diagnostic['result_hash'] == stable_hash(result),
                 'DIAGNOSTIC_CONFLICT')
        recomputed = diagnose(result, trial_id=name)
        _require(all(diagnostic[k] == recomputed[k] for k in ('account_state', 'metrics', 'reason_codes')),
                 'DIAGNOSTIC_CONFLICT')
        _require(evidence['feedback'] == feedback_view(diagnostic)
                 and evidence['decision'] == {'reason': 'EXPLORATION_RECORDED', 'qualification': 'NOT_ASSESSED'},
                 'COMPLETION_CONFLICT')

    def freeze(self, session_root, candidate_id):
        session = BoundedResearchSessionV1(session_root)
        _require(isinstance(candidate_id, str) and re.fullmatch(r'CANDIDATE_00[1-5]', candidate_id), 'CANDIDATE_REQUIRED')
        with session.lock():
            scope = session.scope()
            origin = {'scope_id': scope['scope_id'], 'candidate_id': candidate_id, 'source_root': str(session.root)}
            strategy_id = 'BS_' + stable_hash({'scope_id': scope['scope_id'], 'candidate_id': candidate_id})
            with self._lock(strategy_id):
                if self._path(strategy_id, 'ARCHIVE.json').exists():
                    return self.load(strategy_id)
                evidence = {'session': scope}
                for key, filename in [('candidate', 'CANDIDATE'), ('attempt', 'ATTEMPT'), ('result', 'RESULT'),
                                      ('diagnostic', 'DIAGNOSTIC'), ('feedback', 'FEEDBACK'), ('decision', 'DECISION')]:
                    evidence[key] = _read(session.path(candidate_id, filename + '.json'))
                for key, filename in [('receipt', 'CONFIRMATION'), ('start', candidate_id + '_START'),
                                      ('settlement', candidate_id + '_SETTLEMENT')]:
                    evidence[key] = read_json(session.path(candidate_id, 'governance', filename + '.json'))
                evidence['budget'] = read_json(session.path('search_budget_registry.json'))
                evidence['trials'] = read_json(session.path('trial_registry.json'))
                self._validate(evidence, origin)
                candidate = evidence['candidate']
                archive = {'schema_version': 'BOUNDED_STRATEGY_ARCHIVE_V1', 'strategy_id': strategy_id,
                           'rule_identity': candidate['plan']['strategy']['parameters']['rule_identity'],
                           'proposal': candidate['proposal'], 'origin': origin, 'plan': candidate['plan'],
                           'review_state': 'EXPLORATORY_REVIEW_ONLY', 'qualification': 'NOT_ASSESSED',
                           'source_profile': scope['input_manifest']['profile'], 'source_identity': scope['source_identity'],
                           'exposure': 'EXPLORATION_EXPOSED', 'evidence': evidence,
                           'source_evidence_hashes': {k: stable_hash(v) for k, v in evidence.items()}}
                archive['archive_hash'] = stable_hash(archive)
                _put(self._path(strategy_id, 'ARCHIVE.json'), archive)
                self._commit_head(strategy_id, archive['archive_hash'])
                return archive

    def load(self, strategy_id):
        archive = _read(self._path(strategy_id, 'ARCHIVE.json'))
        _require(archive['strategy_id'] == strategy_id and archive['archive_hash'] == stable_hash(_without(archive, 'archive_hash')),
                 'HASH_CONFLICT')
        evidence, origin = archive['evidence'], archive['origin']
        self._validate(evidence, origin)
        _require(strategy_id == 'BS_' + stable_hash({'scope_id': origin['scope_id'], 'candidate_id': origin['candidate_id']})
                 and archive['source_evidence_hashes'] == {k: stable_hash(v) for k, v in evidence.items()}
                 and archive['plan'] == evidence['candidate']['plan'] and archive['proposal'] == evidence['candidate']['proposal']
                 and archive['rule_identity'] == archive['plan']['strategy']['parameters']['rule_identity']
                 and archive['qualification'] == 'NOT_ASSESSED'
                 and archive['review_state'] == 'EXPLORATORY_REVIEW_ONLY'
                 and archive['schema_version'] == 'BOUNDED_STRATEGY_ARCHIVE_V1'
                 and archive['source_identity'] == evidence['session']['source_identity']
                 and archive['exposure'] == 'EXPLORATION_EXPOSED'
                 and archive['source_profile'] == evidence['session']['input_manifest']['profile'], 'PROJECTION_CONFLICT')
        return archive

    def review(self, strategy_id):
        archive = self.load(strategy_id)
        diagnostic = diagnose(archive['evidence']['result'], trial_id=archive['origin']['candidate_id'])
        try:
            from .bounded_candidate_v1 import BoundedVoteStrategy
            strategy = BoundedVoteStrategy(archive['proposal'], strategy_id=archive['origin']['candidate_id'])
            strategy.validate()
            executable = (strategy.rule_identity == archive['rule_identity']
                          and strategy.parameters == archive['plan']['strategy']['parameters'])
        except (ValueError, KeyError, TypeError):
            executable = False
        metrics = diagnostic['metrics']
        report = {'schema_version': 'BOUNDED_STRATEGY_REVIEW_V1', 'strategy_id': strategy_id,
                  'archive_hash': archive['archive_hash'], 'source_profile': archive['source_profile'],
                  'review_state': 'EXPLORATORY_REVIEW_ONLY', 'qualification': 'BLOCKED',
                  'strategy_qualified': False, 'current_rule_executable': executable,
                  'historical_account_state': diagnostic['account_state'], 'metrics': metrics,
                  'diagnostic': diagnostic, 'reason_codes': [*diagnostic['reason_codes'], 'FORMAL_STATISTICAL_METHOD_NOT_APPROVED'],
                  'assessment': {
                      'economic': '本次历史探索的净收益和回撤仅描述已见样本，不证明未来有效。',
                      'sample': '账户天数和成交笔数不是独立样本数量，不能替代预登记统计检验。',
                      'execution': '账户证据已核对。' if diagnostic['account_state'] == 'COMPLETE' else '账户证据未完整核对，暂停观察准入。',
                      'cost': '列出实际记录的费用；尚无独立成本压力复验，费用加回不等于重新回测。',
                      'independence': '本轮数据已经用于候选生成和比较；尚缺冻结后独立确认窗口与批准的统计方法。',
                      'publication': '历史发布时间仍按建模假设处理，不能据此宣称严格历史可得。'}}
        if not executable:
            report['reason_codes'].append('CURRENT_RULE_NOT_EXECUTABLE')
        from .formal_assessment_v1 import FormalAssessmentServiceV1
        formal = FormalAssessmentServiceV1(self.root).assessment_for(strategy_id)
        if formal is not None:
            report['formal_assessment'] = formal
            report['strategy_qualified'] = formal['strategy_qualified']
            report['qualification'] = formal['decision']
            report['reason_codes'] = formal['reason_codes']
        report['review_hash'] = stable_hash(report)
        return report

    def _head(self, strategy_id, archive_hash):
        path = self._path(strategy_id, 'REVOKED.json')
        revoke = _read(path) if path.exists() else None
        if revoke is not None:
            _require(revoke['strategy_id'] == strategy_id and revoke['archive_hash'] == archive_hash
                     and revoke['revoked'] is True, 'REVOCATION_CONFLICT')
        return {'strategy_id': strategy_id, 'archive_hash': archive_hash,
                'revocation_hash': stable_hash(revoke) if revoke else None}

    def _commit_head(self, strategy_id, archive_hash):
        head = self._head(strategy_id, archive_hash)
        _atomic_write(self._path(strategy_id, 'HEAD.json'), {**head, '_integrity': stable_hash(head)})

    def admission(self, strategy_id, *, purpose):
        _require(purpose in ('ENGINEERING_OBSERVATION', 'FORMAL_OBSERVATION'), 'PURPOSE_INVALID')
        with self._lock(strategy_id):
            report = self.review(strategy_id)
            head = _read(self._path(strategy_id, 'HEAD.json'))
            _require(head == self._head(strategy_id, report['archive_hash']), 'COMMITTED_HEAD_CONFLICT')
            reasons = []
            if head['revocation_hash'] is not None:
                reasons.append('STRATEGY_REVOKED')
            if not report['current_rule_executable']:
                reasons.append('CURRENT_RULE_NOT_EXECUTABLE')
            if report['historical_account_state'] != 'COMPLETE':
                reasons.append(report['historical_account_state'])
            if purpose == 'FORMAL_OBSERVATION':
                formal = report.get('formal_assessment')
                if formal is None:
                    reasons.extend(['INDEPENDENT_CONFIRMATION_REQUIRED', 'FORMAL_STATISTICAL_METHOD_NOT_APPROVED'])
                elif not formal['strategy_qualified']:
                    reasons.extend(formal['reason_codes'])
                    reasons.append('FORMAL_ASSESSMENT_NOT_QUALIFIED')
                else:
                    from .bounded_research_v1 import source_identity
                    if formal['source_identity'] != source_identity():
                        reasons.append('FORMAL_EXECUTION_SOURCE_CHANGED')
                if report['source_profile'] == 'SYNTHETIC':
                    reasons.append('SYNTHETIC_SOURCE_NOT_QUALIFIED')
            return {'allowed': not reasons, 'strategy_qualified': bool(not reasons and report['strategy_qualified']), 'reason_codes': reasons,
                    'archive_hash': report['archive_hash'], 'review_hash': report['review_hash'],
                    'purpose': purpose, 'source_profile': report['source_profile'],
                    **({'formal_assessment': report['formal_assessment']} if 'formal_assessment' in report else {})}

    def revoke(self, strategy_id, reason):
        _require(isinstance(reason, str) and bool(reason.strip()), 'REVOCATION_REASON_REQUIRED')
        with self._lock(strategy_id):
            archive = self.load(strategy_id)
            path = self._path(strategy_id, 'REVOKED.json')
            if not path.exists():
                head = _read(self._path(strategy_id, 'HEAD.json'))
                _require(head == self._head(strategy_id, archive['archive_hash']), 'COMMITTED_HEAD_CONFLICT')
                _put(path, {'strategy_id': strategy_id, 'archive_hash': archive['archive_hash'],
                            'reason': reason, 'revoked': True, 'recorded_at': datetime.now(timezone.utc).isoformat()})
            self._commit_head(strategy_id, archive['archive_hash'])
            return _read(path)
