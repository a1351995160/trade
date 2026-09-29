"""固定组合的预登记观察评审；只读取权威档案和原 Paper 账户。"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import pandas as pd

from .bounded_research_v1 import _put, _read, source_identity
from .common import stable_hash
from .forward_paper_v1 import ForwardPaperSessionV1, _stamp
from .mutation_boundary import ObjectiveMutationLock
from .portfolio_execution_v1 import PortfolioExecutionPolicyV1
from .public_strategy_archive_v3 import archive_for_ids


def _now():
    return datetime.now(timezone.utc).isoformat()


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


class PortfolioQualificationV1:
    def __init__(self, root):
        self.root = Path(root).absolute()
        _require(self.root.resolve() == self.root, 'PORTFOLIO_QUALIFICATION_PATH_REDIRECTED')

    def path(self, name):
        path = self.root / name
        _require(path.resolve() == path, 'PORTFOLIO_QUALIFICATION_PATH_REDIRECTED')
        return path

    def lock(self):
        return ObjectiveMutationLock.for_resource(self.path('PORTFOLIO.json'))

    @classmethod
    def freeze(cls, root, *, archive_root, policy, calendar, review_policy, profile='REAL_OBSERVED'):
        _require(profile in ('REAL_OBSERVED', 'SYNTHETIC'), 'PORTFOLIO_PROFILE_INVALID')
        execution = PortfolioExecutionPolicyV1.model_validate_json(json.dumps(policy['portfolio']))
        _require(len(execution.members) >= 2, 'PORTFOLIO_AT_LEAST_TWO_MEMBERS_REQUIRED')
        _require(execution.purpose == 'ENGINEERING_OBSERVATION', 'PORTFOLIO_PREQUALIFICATION_OBSERVATION_REQUIRED')
        required = {'min_complete_days', 'max_drawdown_bps', 'max_total_cost_bps',
                    'max_symbol_exposure_bps', 'min_trades_per_member', 'min_net_return_bps'}
        _require(set(review_policy) == required and all(type(v) is int for v in review_policy.values()),
                 'PORTFOLIO_REVIEW_POLICY_INVALID')
        minimum = 20 if profile == 'REAL_OBSERVED' else 2
        _require(minimum <= review_policy['min_complete_days'] <= 5040,
                 'PORTFOLIO_OBSERVATION_SAMPLE_TOO_SMALL')
        _require(all(0 < review_policy[k] <= 10000 for k in
                     ('max_drawdown_bps', 'max_total_cost_bps', 'max_symbol_exposure_bps'))
                 and review_policy['min_trades_per_member'] >= 1
                 and -10000 <= review_policy['min_net_return_bps'] <= 100000,
                 'PORTFOLIO_RISK_LIMIT_INVALID')
        _require(calendar == sorted(set(calendar)) and len(calendar) == review_policy['min_complete_days'] + 1
                 and all(type(day) is int for day in calendar), 'PORTFOLIO_EXACT_WINDOW_REQUIRED')
        for day in calendar:
            pd.Timestamp(str(day))
        created = _stamp(_now())
        _require(profile == 'SYNTHETIC' or pd.Timestamp(str(calendar[0]), tz='Asia/Shanghai') > created,
                 'PORTFOLIO_FUTURE_WINDOW_REQUIRED')
        _require(_stamp(execution.valid_until) > pd.Timestamp(str(calendar[-1]), tz='Asia/Shanghai') + pd.Timedelta(days=1),
                 'PORTFOLIO_POLICY_EXPIRES_DURING_WINDOW')
        archive = archive_for_ids(archive_root, [member.strategy_id for member in execution.members])
        members = {}
        for member in execution.members:
            item = archive.load(member.strategy_id)
            _require(item['rule_identity'] == member.rule_identity, 'PORTFOLIO_MEMBER_RULE_CONFLICT')
            engineering = archive.admission(member.strategy_id, purpose='ENGINEERING_OBSERVATION')
            _require(engineering['allowed'], 'PORTFOLIO_MEMBER_NOT_EXECUTABLE')
            members[member.strategy_id] = {'archive_hash': item['archive_hash'],
                'rule_identity': item['rule_identity'], 'source_profile': item['source_profile'],
                'formal_admission': archive.admission(member.strategy_id, purpose='FORMAL_OBSERVATION')}
        body = {'schema_version': 'PortfolioQualificationV1', 'created_at': created.isoformat(),
            'profile': profile, 'archive_root': str(Path(archive_root).absolute()),
            'policy': deepcopy(policy), 'calendar': list(calendar), 'review_policy': deepcopy(review_policy),
            'members': members, 'source_identity': source_identity(),
            'evidence_kind': 'PREREGISTERED_COMBINED_ACCOUNT_OPERATIONAL_OBSERVATION'}
        body['portfolio_id'] = stable_hash(body)
        service = cls(root)
        service.root.mkdir(parents=True, exist_ok=True)
        with service.lock():
            _put(service.path('PORTFOLIO.json'), body)
        return service

    def frozen(self):
        value = _read(self.path('PORTFOLIO.json'))
        _require(value['portfolio_id'] == stable_hash({k: v for k, v in value.items() if k != 'portfolio_id'}),
                 'PORTFOLIO_FROZEN_IDENTITY_CONFLICT')
        return value

    def bind_session(self, paper_root):
        with self.lock():
            frozen = self.frozen()
            paper = ForwardPaperSessionV1(paper_root)
            with paper.lock():
                header = paper.header()
                binding = {'portfolio_id': frozen['portfolio_id'], 'paper_root': str(paper.root),
                           'header_id': header['header_id']}
                if self.path('OBSERVATION.json').exists():
                    _require(_read(self.path('OBSERVATION.json')) == binding, 'PORTFOLIO_OBSERVATION_ALREADY_BOUND')
                    return binding
                _require(not paper._records(header), 'PORTFOLIO_RETROSPECTIVE_BINDING_FORBIDDEN')
                self._scope(frozen, header)
                _require(frozen['profile'] == 'SYNTHETIC' or _stamp(header['created_at']) >= _stamp(frozen['created_at']),
                         'PORTFOLIO_SESSION_PREDATES_FREEZE')
                _put(self.path('OBSERVATION.json'), binding)
                return binding

    @staticmethod
    def _scope(frozen, header):
        _require(header['profile'] == frozen['profile'] and header['policy'] == frozen['policy']
                 and header['calendar'] == frozen['calendar'] and header['archive_root'] == frozen['archive_root']
                 and header['source_identity'] == frozen['source_identity'], 'PORTFOLIO_OBSERVATION_SCOPE_CONFLICT')
        _require(set(header['strategies']) == set(frozen['members']), 'PORTFOLIO_MEMBER_SET_CONFLICT')
        for key, member in frozen['members'].items():
            _require(all(header['strategies'][key][field] == member[field]
                         for field in ('archive_hash', 'rule_identity', 'source_profile')), 'PORTFOLIO_MEMBER_IDENTITY_CONFLICT')

    def readiness(self):
        frozen = self.frozen()
        archive = archive_for_ids(frozen['archive_root'], list(frozen['members']))
        reasons, admissions = [], {}
        for key, member in frozen['members'].items():
            item = archive.load(key)
            _require(item['archive_hash'] == member['archive_hash'] and item['rule_identity'] == member['rule_identity'],
                     'PORTFOLIO_MEMBER_IDENTITY_CONFLICT')
            current = archive.admission(key, purpose='FORMAL_OBSERVATION')
            admissions[key] = current
            if not member['formal_admission']['allowed'] or not member['formal_admission']['strategy_qualified']:
                reasons.append('MEMBER_NOT_QUALIFIED_AT_FREEZE:' + key)
            if not current['allowed'] or not current['strategy_qualified']:
                reasons.append('MEMBER_NOT_CURRENTLY_QUALIFIED:' + key)
            if current.get('review_hash') != member['formal_admission'].get('review_hash'):
                reasons.append('MEMBER_QUALIFICATION_CHANGED:' + key)
            formal = current.get('formal_assessment')
            if current['allowed'] and (not isinstance(formal, dict) or
                    sorted(formal.get('symbols', [])) != sorted(frozen['policy']['symbols'])):
                reasons.append('MEMBER_FORMAL_SYMBOL_SCOPE_CONFLICT:' + key)
        if frozen['profile'] != 'REAL_OBSERVED':
            reasons.append('SYNTHETIC_OBSERVATION_NOT_QUALIFIED')
        if source_identity() != frozen['source_identity']:
            reasons.append('EXECUTION_SOURCE_CHANGED')
        if _stamp(_now()) >= _stamp(frozen['policy']['portfolio']['valid_until']):
            reasons.append('PORTFOLIO_POLICY_EXPIRED')
        return {'portfolio_id': frozen['portfolio_id'], 'eligible_for_formal_review': not reasons,
                'reason_codes': reasons, 'member_admissions': admissions,
                'observation_bound': self.path('OBSERVATION.json').exists()}

    def review(self):
        with self.lock():
            frozen = self.frozen()
            readiness = self.readiness()
            reasons = list(readiness['reason_codes'])
            if not readiness['observation_bound']:
                return {**readiness, 'status': 'WAITING_OBSERVATION', 'portfolio_qualified': False}
            binding = _read(self.path('OBSERVATION.json'))
            _require(binding['portfolio_id'] == frozen['portfolio_id'], 'PORTFOLIO_BINDING_CONFLICT')
            paper = ForwardPaperSessionV1(binding['paper_root'])
            with paper.lock():
                header = paper.header()
                self._scope(frozen, header)
                _require(header['header_id'] == binding['header_id'], 'PORTFOLIO_SESSION_IDENTITY_CONFLICT')
                records = paper._records(header)
                from .forward_snapshot_v1 import validate_snapshot
                for record in records:
                    snapshot = record['snapshot']
                    validate_snapshot(snapshot)
                    _require(snapshot['profile'] == header['profile'] and
                             snapshot['source_response_hash'] == stable_hash(snapshot['source_responses']),
                             'PORTFOLIO_SNAPSHOT_SOURCE_CONFLICT')
                    if header['profile'] == 'REAL_OBSERVED':
                        _require(snapshot['provider'] == 'TDX_TQ_LOCAL_UNCACHED_V1',
                                 'PORTFOLIO_OBSERVATION_PROVIDER_UNSUPPORTED')
                # 哈希链不能代替账户正确性；复用原引擎完整重放核对所有阶段。
                paper._recover(header, records)
                status = paper._status(header, records)
            if status['status'] in ('HALTED', 'REVOKED'):
                reasons.append('OBSERVATION_HALTED_OR_REVOKED')
            expected = [('CLOSE', frozen['calendar'][0])]
            expected += [(phase, day) for day in frozen['calendar'][1:] for phase in ('OPEN', 'CLOSE')]
            actual = [(row['snapshot']['phase'], row['snapshot']['market_date']) for row in records]
            _require(actual == expected[:len(actual)], 'PORTFOLIO_OBSERVATION_PHASE_CONFLICT')
            complete = actual == expected
            if not complete:
                reasons.append('OBSERVATION_WINDOW_INCOMPLETE')
            metrics, risks = _account_risk(records, frozen)
            reasons.extend(risks)
            qualified = not reasons and complete
            return {**readiness, 'status': 'QUALIFIED_FOR_FORMAL_OBSERVATION' if qualified else
                    ('REJECTED' if complete else 'WAITING_OBSERVATION'),
                    'portfolio_qualified': qualified, 'reason_codes': reasons,
                    'observation_risk_pass': bool(complete and not risks and status['status'] not in ('HALTED', 'REVOKED')),
                    'metrics': metrics, 'paper_header_id': header['header_id'],
                    'company_actions': header['company_actions'],
                    'observation_policy': deepcopy(header.get('observation_policy')),
                    'last_record_hash': records[-1]['record_hash'] if records else header['header_id'],
                    'evidence_kind': frozen['evidence_kind'], 'statistical_effectiveness_proven': False,
                    'real_execution_authorized': False}


def _account_risk(records, frozen):
    """按冻结阈值核对全部阶段，不能删掉不利交易日或只选收盘峰值。"""
    initial = frozen['policy']['initial_cash']
    _require(isinstance(initial, (int, float)) and not isinstance(initial, bool)
             and math.isfinite(initial) and initial > 0, 'PORTFOLIO_INITIAL_CASH_INVALID')
    peak, equity, max_dd, max_exposure = initial, initial, 0.0, 0.0
    counts = dict.fromkeys(frozen['members'], 0)
    trades = {}
    for record in records:
        state = record['state']
        _require(not state['invariant_errors'], 'PORTFOLIO_ACCOUNT_INVARIANT_FAILED')
        equity = state['equity']
        _require(isinstance(equity, (int, float)) and math.isfinite(equity) and equity > 0,
                 'PORTFOLIO_EQUITY_INVALID')
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 10000)
        prices = {bar['symbol']: bar['open' if record['snapshot']['phase'] == 'OPEN' else 'close']
                  for bar in record['snapshot']['payload']['bars']}
        exposures = {}
        for position in state['economic']['positions'].values():
            _require(position['strategy_id'] in counts, 'PORTFOLIO_UNKNOWN_POSITION_OWNER')
            if position['quantity']:
                symbol = position['symbol']
                _require(symbol in prices and position['quantity'] > 0, 'PORTFOLIO_EXPOSURE_DATA_MISSING')
                exposures[symbol] = exposures.get(symbol, 0) + position['quantity'] * prices[symbol]
        max_exposure = max(max_exposure, max(exposures.values(), default=0) / equity * 10000)
        for trade in state['economic']['trades']:
            key = trade['trade_id']
            if key in trades:
                _require(trades[key] == trade, 'PORTFOLIO_TRADE_REVISION')
            else:
                _require(trade['strategy_id'] in counts and math.isfinite(trade['fee']) and trade['fee'] >= 0,
                         'PORTFOLIO_TRADE_EVIDENCE_INVALID')
                trades[key] = trade
                counts[trade['strategy_id']] += 1
    cost = sum(trade['fee'] for trade in trades.values()) / initial * 10000
    net = (equity / initial - 1) * 10000
    limits = frozen['review_policy']
    reasons = []
    for value, key, reason in ((max_dd, 'max_drawdown_bps', 'COMBINED_DRAWDOWN_EXCEEDED'),
                              (cost, 'max_total_cost_bps', 'COMBINED_COST_EXCEEDED'),
                              (max_exposure, 'max_symbol_exposure_bps', 'COMBINED_SYMBOL_EXPOSURE_EXCEEDED')):
        if value > limits[key] + 1e-8:
            reasons.append(reason)
    if net + 1e-8 < limits['min_net_return_bps']:
        reasons.append('COMBINED_NET_RETURN_BELOW_FROZEN_LIMIT')
    if any(value < limits['min_trades_per_member'] for value in counts.values()):
        reasons.append('MEMBER_ACTIVITY_EVIDENCE_INSUFFICIENT')
    return {'max_drawdown_bps': max_dd, 'total_fees_bps': cost, 'max_symbol_exposure_bps': max_exposure,
            'net_return_bps': net, 'trades_per_member': counts}, reasons
