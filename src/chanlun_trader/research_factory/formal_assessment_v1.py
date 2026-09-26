"""冻结后独立确认：权威预算、真实账户复验与有条件的 Paper 准入。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .bounded_research_v1 import _put, _read, source_identity
from .common import stable_hash
from .mutation_boundary import ObjectiveMutationLock
from .formal_statistics_v1 import METHOD_HASH, METHOD_SPEC, family_test, load_calibration
from . import formal_statistics_v2 as statistics_v2
from .formal_account_backend_v1 import (
    prepare_formal_account, run_formal_account, validate_formal_result, window_input_identity,
)
from .formal_evidence_v1 import build_confirmation_bundle


POLICY = {'version': 'FORMAL_ASSESSMENT_V1', 'warmup_sessions': 60, 'account_sessions': 504,
          'benchmark': 'EQUAL_WEIGHT_BUY_AND_HOLD', 'costs': ['BASE', 'STRESS'],
          'minimum_net_return': 0., 'maximum_drawdown': .25,
          'require_positive_net_excess_each_half': True,
          'interpretation': 'CONDITIONAL_EVIDENCE_FOR_PAPER_NOT_PROOF_OF_FUTURE_PROFIT'}

# 绑定本轮实际执行的完整校准；调用方不能提交自编 p 值文件批准方法。
# 新方法必须另作预登记和审核，不能替换本次失败记录。
CALIBRATION_HASH = '9aa67aa7f480424cdf46f21725ab420fdc72f777f623478fe66ac6039543d6ec'
# 只有完成本版预登记校准并核验完整报告后才填入，None 不允许准入。
V2_CALIBRATION_HASH = '427c509ffa60f2ad7ca122a5fc519b7aa602eb15826d9421e159fb5ee1074e22'


def _method(method_hash):
    if method_hash == METHOD_HASH:
        return METHOD_SPEC, CALIBRATION_HASH, family_test
    if method_hash == statistics_v2.METHOD_HASH:
        return statistics_v2.METHOD_SPEC, V2_CALIBRATION_HASH, statistics_v2.family_test
    raise ValueError('FORMAL_ASSESSMENT_UNKNOWN_METHOD')


def _statistics(plan, excess):
    spec, _, test = _method(plan['method_hash'])
    return test(excess, alpha=plan['alpha_budget'] * spec['test_alpha_fraction'])


def _load_method(path):
    path = _safe_root(path)
    # 先读取声明仅用于选择验证器，任何批准都来自完整验证和固定报告身份。
    preregistration = path.parent / 'PREREGISTRATION.json'
    if preregistration.exists():
        declared = _read(preregistration)['method_hash']
    else:
        declared = _read(path)['method_hash'] if path.exists() else METHOD_HASH
    _, expected, _ = _method(declared)
    _require(expected is not None, 'UNREVIEWED_CALIBRATION')
    calibration = (statistics_v2.load_calibration(path, expected_hash=expected)
                   if declared == statistics_v2.METHOD_HASH else load_calibration(path))
    actual = calibration.get('method_hash', METHOD_HASH)
    _require(actual == declared and expected is not None and stable_hash(calibration) == expected,
             'UNREVIEWED_CALIBRATION')
    return calibration


def _require(value, reason):
    if not value:
        raise ValueError('FORMAL_ASSESSMENT_' + reason)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _safe_root(value):
    path = Path(value).absolute()
    _require(path.resolve() == path and '..' not in path.parts, 'PATH_REDIRECTED')
    return path


def adjudicate(*, plan, evidence, results, statistics, method_approved):
    """只供权威服务从已核验账户派生；公共准入不接受此函数的输出。"""
    reports = {}
    benchmark = results.get('BENCHMARK_BASE')
    for member, frozen in plan['family'].items():
        reasons, failed = [], []
        base, stress = results.get(member + '_BASE'), results.get(member + '_STRESS')
        if frozen is None or base is None or stress is None or benchmark is None:
            reasons.append('COMPLETE_ACCOUNT_EVIDENCE_REQUIRED')
        else:
            if base['metrics']['net_return'] <= 0:
                failed.append('NONPOSITIVE_NET_RETURN')
            if stress['metrics']['net_return'] <= 0:
                failed.append('COST_STRESS_FAILED')
            if min(base['metrics']['max_drawdown'], stress['metrics']['max_drawdown']) < -.25:
                failed.append('DRAWDOWN_LIMIT_EXCEEDED')
            excess = [a['net_return'] - b['net_return'] for a, b in
                      zip(base['daily_returns'], benchmark['daily_returns'])]
            if any(sum(part) <= 0 for part in (excess[:252], excess[252:])):
                failed.append('SUBPERIOD_EXCESS_NOT_POSITIVE')
            if not statistics['supported'][member]:
                reasons.append('STATISTICAL_SUPPORT_INSUFFICIENT')
        if not method_approved:
            reasons.append('FORMAL_STATISTICAL_METHOD_NOT_APPROVED')
        if plan.get('method_hash') == statistics_v2.METHOD_HASH:
            # 合成校准不能证明真实账户的共同均值及分段近似独立；本轮不开放该资格门。
            reasons.append('REAL_RETURN_PROCESS_APPLICABILITY_NOT_ESTABLISHED')
        if evidence.get('qualification_evidence_eligible') is not True:
            reasons.append('INDEPENDENT_REAL_EXECUTION_EVIDENCE_REQUIRED')
        if plan['profile'] != 'REAL_OBSERVED' or (frozen and frozen['source_profile'] == 'SYNTHETIC'):
            reasons.append('SYNTHETIC_SOURCE_NOT_QUALIFIED')
        verdict = 'REJECTED' if failed else ('CONTINUE_RESEARCH' if reasons else 'PAPER_ELIGIBLE')
        reports[member] = {'decision': verdict, 'strategy_qualified': verdict == 'PAPER_ELIGIBLE',
                           'reason_codes': failed + reasons, 'strategy_id': frozen['strategy_id'] if frozen else None,
                           'adjusted_p': statistics['adjusted_p'][member],
                           'metrics': {key: result['metrics'] if result else None
                                       for key, result in [('BASE', base), ('STRESS', stress)]},
                           'interpretation': POLICY['interpretation']}
    return reports


class FormalAssessmentServiceV1:
    """单机受信目录；复制输出不能获得新预算，源研究目录永久绑定一个权威目录。"""

    def __init__(self, archive_root):
        from .strategy_qualification_v1 import BoundedStrategyArchiveV1
        self.archive = BoundedStrategyArchiveV1(archive_root)
        self.root = _safe_root(self.archive.root.parent / 'formal-assessment-authority-v1')

    def lock(self):
        return ObjectiveMutationLock.for_resource(self.root / 'AUTHORITY.json')

    def path(self, batch, name):
        _require(isinstance(batch, str) and re.fullmatch(r'FA_[0-9a-f]{64}', batch), 'BATCH_ID_INVALID')
        return _safe_root(self.root / batch / name)

    def _plans(self):
        return [_read(path) for path in sorted(self.root.glob('FA_*/PLAN.json'))]

    def readiness(self, *, strategy_ids, calibration_path):
        """当前正式评审，不开启确认窗口，不消耗确认账户或错误率预算。"""
        calibration = _load_method(calibration_path)
        failed = [row for row in calibration['summary']['conditions']
                  if row['support_domain'] and not row['passed']]
        decisions = {}
        for key in strategy_ids:
            archived = self.archive.load(key)
            review = self.archive.review(key)
            reasons = ['INDEPENDENT_CONFIRMATION_REQUIRED', 'INDEPENDENT_COST_STRESS_REQUIRED']
            if calibration.get('method_hash') == statistics_v2.METHOD_HASH:
                reasons.append('REAL_RETURN_PROCESS_APPLICABILITY_NOT_ESTABLISHED')
            if not calibration['method_approved']:
                reasons.insert(0, 'FORMAL_STATISTICAL_CALIBRATION_FAILED')
            if archived['source_profile'] == 'SYNTHETIC':
                reasons.append('SYNTHETIC_SOURCE_NOT_QUALIFIED')
            if review['historical_account_state'] != 'COMPLETE':
                reasons.append('HISTORICAL_ACCOUNT_EVIDENCE_INCOMPLETE')
            decisions[key] = {'decision': 'CONTINUE_RESEARCH', 'strategy_qualified': False,
                              'archive_hash': archived['archive_hash'], 'reason_codes': reasons,
                              'historical_metrics': review['metrics']}
        return {'version': POLICY['version'], 'status': 'FORMAL_READINESS_REVIEWED',
                'strategy_qualified': False, 'decisions': decisions,
                'method_hash': calibration.get('method_hash', METHOD_HASH),
                'calibration_hash': stable_hash(calibration), 'method_approved': calibration['method_approved'],
                'failed_support_checks': len(failed), 'failed_checks': failed,
                'historical_data_reused_as_independent': False, 'confirmation_budget_consumed': 0,
                'next_action': ('REDESIGN_AND_PREREGISTER_STATISTICAL_METHOD'
                                if not calibration['method_approved'] else
                                'ESTABLISH_REAL_PROCESS_APPLICABILITY_AND_INDEPENDENT_WINDOW'),
                'method_scope': _method(calibration.get('method_hash', METHOD_HASH))[0]['interpretation'],
                'real_process_applicability': 'NOT_ESTABLISHED',
                'interpretation': '准入准备评审；没有独立收益证据时不对策略本身作有效或无效的判决。'}

    def register(self, *, strategy_ids, symbols, not_before, calibration_path, profile='REAL_OBSERVED'):
        _require(profile in ('REAL_OBSERVED', 'SYNTHETIC'), 'PROFILE_INVALID')
        _require(isinstance(strategy_ids, list) and 1 <= len(strategy_ids) <= 5
                 and len(set(strategy_ids)) == len(strategy_ids), 'FAMILY_INVALID')
        symbols = sorted(symbols)
        _require(len(symbols) == len(set(symbols)) == 2 and all(
            re.fullmatch(r'(60[0-9]{4}\.SH|00[0-9]{4}\.SZ)', x) for x in symbols), 'TWO_MAIN_BOARD_SYMBOLS_REQUIRED')
        now = _now()
        today = int(datetime.fromisoformat(now).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d'))
        _require(type(not_before) is int and not_before > today, 'FUTURE_START_REQUIRED')
        datetime.strptime(str(not_before), '%Y%m%d')
        calibration = _load_method(calibration_path)
        method_hash = calibration.get('method_hash', METHOD_HASH)
        method_spec, _, _ = _method(method_hash)
        _require(method_spec['alpha_budgets'] == METHOD_SPEC['alpha_budgets']
                 and method_spec['sessions'] == POLICY['account_sessions'], 'METHOD_POLICY_CONFLICT')
        # 失败校准可登记工程演练，但不能消耗真实确认窗口后才发现方法未批准。
        _require(profile == 'SYNTHETIC' or calibration['method_approved'], 'CALIBRATION_NOT_APPROVED')
        archives = [self.archive.load(key) for key in sorted(strategy_ids)]
        scopes = {a['origin']['scope_id'] for a in archives}
        roots = {a['origin']['source_root'] for a in archives}
        _require(len(scopes) == len(roots) == 1, 'ONE_COMPLETE_RESEARCH_FAMILY_REQUIRED')
        origin = _safe_root(next(iter(roots)))
        scope = archives[0]['evidence']['session']
        family = {f'CANDIDATE_{i:03d}': None for i in range(1, scope['max_attempts'] + 1)}
        _require(all((origin / key / 'DECISION.json').exists() for key in family), 'RESEARCH_FAMILY_NOT_CLOSED')
        # 不允许只归档历史赢家；存在成功结果的候选必须全部进入冻结家族。
        available = {key for key in family if (origin / key / 'RESULT.json').exists()}
        _require(available == {a['origin']['candidate_id'] for a in archives}, 'FAMILY_MEMBER_OMITTED')
        for a in archives:
            _require(self.archive.admission(a['strategy_id'], purpose='ENGINEERING_OBSERVATION')['allowed'],
                     'ARCHIVE_NOT_EXECUTABLE')
            _require(profile == 'SYNTHETIC' or a['source_profile'] != 'SYNTHETIC', 'REAL_ARCHIVE_REQUIRED')
            family[a['origin']['candidate_id']] = {key: a[key] for key in
                ('strategy_id', 'archive_hash', 'rule_identity', 'proposal', 'source_profile')}
        binding = {'authority_root': str(self.root), 'scope_id': scope['scope_id']}
        with ObjectiveMutationLock.for_resource(origin / 'FORMAL_AUTHORITY.json'), self.lock():
            _put(origin / 'FORMAL_AUTHORITY.json', binding)
            authority = {'version': POLICY['version'], 'alpha_budgets': METHOD_SPEC['alpha_budgets']}
            _put(self.root / 'AUTHORITY.json', authority)
            plans = self._plans()
            _require(len(plans) < 3, 'LIFETIME_BATCH_BUDGET_EXHAUSTED')
            _require(not any(p['scope_id'] == scope['scope_id'] for p in plans), 'FAMILY_ALREADY_REGISTERED')
            slot = len(plans) + 1
            plan = {'version': POLICY['version'], 'frozen_at': now, 'not_before': not_before,
                    'profile': profile, 'scope_id': scope['scope_id'], 'source_root': str(origin),
                    'archive_root': str(self.archive.root), 'authority_root': str(self.root),
                    'family': family, 'symbols': symbols, 'slot': slot,
                    'alpha_budget': method_spec['alpha_budgets'][slot-1], 'policy': POLICY,
                    'method_hash': method_hash, 'calibration_hash': stable_hash(calibration),
                    'source_identity': source_identity(), 'account_budget': 1 + 2 * len(archives)}
            plan['batch_id'] = 'FA_' + stable_hash(plan)
            _put(self.path(plan['batch_id'], 'CALIBRATION.json'), calibration)
            # PLAN 是预算占用的提交记录；之后失败、取消均不返还。
            _put(self.path(plan['batch_id'], 'PLAN.json'), plan)
            return self.status(plan['batch_id'])

    def plan(self, batch):
        plan = _read(self.path(batch, 'PLAN.json'))
        method_spec, calibration_hash, _ = _method(plan['method_hash'])
        _require(plan['batch_id'] == batch == 'FA_' + stable_hash(
            {k: v for k, v in plan.items() if k != 'batch_id'}), 'PLAN_HASH_CONFLICT')
        _require(plan['authority_root'] == str(self.root) and plan['archive_root'] == str(self.archive.root)
                 and plan['policy'] == POLICY
                 and plan['alpha_budget'] == method_spec['alpha_budgets'][plan['slot']-1], 'PLAN_SCOPE_CONFLICT')
        _require(_read(Path(plan['source_root']) / 'FORMAL_AUTHORITY.json') ==
                 {'authority_root': str(self.root), 'scope_id': plan['scope_id']}, 'AUTHORITY_BINDING_CONFLICT')
        calibration = _read(self.path(batch, 'CALIBRATION.json'))
        _require(calibration_hash is not None and
                 calibration.get('method_hash', METHOD_HASH) == plan['method_hash'] and
                 stable_hash(calibration) == plan['calibration_hash'] == calibration_hash, 'CALIBRATION_CHANGED')
        for item in plan['family'].values():
            if item:
                a = self.archive.load(item['strategy_id'])
                _require(all(a[key] == value for key, value in item.items()), 'ARCHIVE_CHANGED')
        return plan

    def status(self, batch):
        plan = self.plan(batch)
        report = self.path(batch, 'REPORT.json')
        if report.exists():
            return self.report(batch)
        failed = self.path(batch, 'FAILED.json')
        return {'batch_id': batch, 'status': 'BLOCKED' if failed.exists() else 'WAITING_FOR_INDEPENDENT_WINDOW',
                'strategy_qualified': False, 'slot': plan['slot'], 'alpha_budget': plan['alpha_budget'],
                'not_before': plan['not_before'], 'required_warmup_sessions': 60, 'required_account_sessions': 504,
                'snapshot_root': str(self.path(batch, 'snapshots')), 'calendar_root': str(self.path(batch, 'calendar')),
                'reason_codes': [_read(failed)['reason']] if failed.exists() else ['INDEPENDENT_CONFIRMATION_REQUIRED']}

    def run(self, batch, *, snapshot_root, snapshot_ids, open_snapshot_ids, calendar_root, calendar_id):
        with self.lock():
            plan = self.plan(batch)
            _require(plan['source_identity'] == source_identity(), 'SOURCE_CHANGED_AFTER_FREEZE')
            if self.path(batch, 'REPORT.json').exists():
                return self.report(batch)
            if self.path(batch, 'FAILED.json').exists():
                return self.status(batch)
            inputs = {'snapshot_root': str(_safe_root(snapshot_root)), 'snapshot_ids': snapshot_ids,
                      'open_snapshot_ids': open_snapshot_ids, 'calendar_root': str(_safe_root(calendar_root)),
                      'calendar_id': calendar_id}
            _require(inputs['snapshot_root'] == str(self.path(batch, 'snapshots'))
                     and inputs['calendar_root'] == str(self.path(batch, 'calendar')), 'FROZEN_CAPTURE_ROOT_REQUIRED')
            _put(self.path(batch, 'INPUTS.json'), inputs)
            # 记录暴露在任何行情读取之前；错误输入也不能换窗口再试。
            started = self.path(batch, 'EXPOSURE_STARTED.json')
            if not started.exists():
                _put(started, {'batch_id': batch, 'inputs_hash': stable_hash(inputs), 'started_at': _now()})
            try:
                prepared = build_confirmation_bundle(**inputs, symbols=plan['symbols'], not_before=plan['not_before'],
                    frozen_at=plan['frozen_at'], profile=plan['profile'], warmup_sessions=60, account_sessions=504)
                bundle, window, evidence = (prepared[key] for key in ('bundle', 'window', 'evidence'))
                identity = window_input_identity(bundle, window)
                _put(self.path(batch, 'DATA_EVIDENCE.json'), {'input_identity': identity, 'window': window, 'evidence': evidence})
                results = {}
                jobs = [('BENCHMARK_BASE', None, 'BASE')]
                for member, frozen in plan['family'].items():
                    if frozen:
                        jobs.extend((member + '_' + cost, frozen['proposal'], cost) for cost in ('BASE', 'STRESS'))
                for key, proposal, cost in jobs:
                    account_plan = prepare_formal_account(proposal, strategy_id=key, window=window, costs=cost)
                    receipt = {'strategy_plans': {key: account_plan}, 'input_identity': identity,
                               'novelty': {key: {'allowed': True}}, 'execution_purpose': key, 'execution_consumed': True}
                    start = {'batch_id': batch, 'account_key': key, 'receipt': receipt,
                             'charged_before_execution': True, 'cost': cost}
                    _put(self.path(batch, key + '_START.json'), start)
                    result_path = self.path(batch, key + '_RESULT.json')
                    def guard(receipt=receipt):
                        _require(source_identity() == plan['source_identity'], 'SOURCE_CHANGED_DURING_RUN')
                        return receipt
                    if result_path.exists():
                        result = _read(result_path)
                    else:
                        result = run_formal_account(proposal, strategy_id=key, bundle=bundle, window=window,
                            costs=cost, input_identity=identity, active_check=guard)
                        _put(result_path, result)
                    _require(result['strategy_plan'] == account_plan and result['input_identity'] == identity,
                             'ACCOUNT_BINDING_CONFLICT')
                    validate_formal_result(result, bundle=bundle, window=window, costs=cost)
                    _put(self.path(batch, key + '_SETTLEMENT.json'),
                         {'start_hash': stable_hash(start), 'result_hash': stable_hash(result)})
                    results[key] = result
                excess = self._excess(plan, results)
                stats = _statistics(plan, excess)
                _put(self.path(batch, 'STATISTICS.json'), stats)
                decisions = adjudicate(plan=plan, evidence=evidence, results=results, statistics=stats,
                    method_approved=_read(self.path(batch, 'CALIBRATION.json'))['method_approved'])
                report = {'batch_id': batch, 'status': 'ASSESSED', 'profile': plan['profile'],
                          'strategy_qualified': any(v['strategy_qualified'] for v in decisions.values()),
                          'decisions': decisions, 'statistics': stats, 'data_evidence_hash': stable_hash(evidence),
                          'account_hashes': {key: stable_hash(value) for key, value in results.items()},
                          'plan_hash': stable_hash(plan), 'symbols': plan['symbols'],
                          'account_budget_consumed': len(results), 'alpha_budget_consumed': plan['alpha_budget'],
                          'limitations': [_method(plan['method_hash'])[0]['interpretation'],
                                          'NO_BROKER_EXECUTION_AUTHORIZED']}
                _put(self.path(batch, 'REPORT.json'), report)
                return report
            except (ValueError, KeyError, TypeError, OSError) as exc:
                _put(self.path(batch, 'FAILED.json'), {'reason': type(exc).__name__ + ':' + str(exc),
                                                    'budget_refunded': False})
                raise

    @staticmethod
    def _excess(plan, results):
        benchmark = results['BENCHMARK_BASE']['daily_returns']
        _require(len(benchmark) == 504 and len({x['date'] for x in benchmark}) == 504, 'ACCOUNT_DATES_INVALID')
        output = {}
        for member, frozen in plan['family'].items():
            if frozen is None:
                output[member] = [float('nan')] * 504
                continue
            for cost in ('BASE', 'STRESS'):
                result = results[member + '_' + cost]
                _require(result['status'] == 'RECONCILED_DIAGNOSTIC' and
                         [x['date'] for x in result['daily_returns']] == [x['date'] for x in benchmark],
                         'ACCOUNT_RECONCILIATION_REQUIRED')
            output[member] = [a['net_return'] - b['net_return'] for a, b in
                             zip(results[member + '_BASE']['daily_returns'], benchmark)]
        return output

    def report(self, batch):
        plan = self.plan(batch)
        report = _read(self.path(batch, 'REPORT.json'))
        recorded_data = _read(self.path(batch, 'DATA_EVIDENCE.json'))
        evidence = recorded_data['evidence']
        inputs = _read(self.path(batch, 'INPUTS.json'))
        exposure = _read(self.path(batch, 'EXPOSURE_STARTED.json'))
        _require(exposure['batch_id'] == batch and exposure['inputs_hash'] == stable_hash(inputs)
                 and inputs['snapshot_root'] == str(self.path(batch, 'snapshots'))
                 and inputs['calendar_root'] == str(self.path(batch, 'calendar')), 'EXPOSURE_BINDING_CONFLICT')
        prepared = build_confirmation_bundle(**inputs, symbols=plan['symbols'], not_before=plan['not_before'],
            frozen_at=plan['frozen_at'], profile=plan['profile'], warmup_sessions=60, account_sessions=504)
        _require(prepared['evidence'] == evidence and prepared['window'] == recorded_data['window']
                 and window_input_identity(prepared['bundle'], prepared['window']) == recorded_data['input_identity'],
                 'DATA_EVIDENCE_CHANGED')
        results = {}
        expected = {'BENCHMARK_BASE'} | {member + '_' + cost for member, item in plan['family'].items()
                                       if item for cost in ('BASE', 'STRESS')}
        _require(set(report['account_hashes']) == expected, 'ACCOUNT_MEMBERSHIP_CONFLICT')
        for key, digest in report['account_hashes'].items():
            result = _read(self.path(batch, key + '_RESULT.json'))
            start = _read(self.path(batch, key + '_START.json'))
            member, cost = key.rsplit('_', 1)
            proposal = None if member == 'BENCHMARK' else plan['family'][member]['proposal']
            account_plan = prepare_formal_account(proposal, strategy_id=key, window=recorded_data['window'], costs=cost)
            expected_start = {'batch_id': batch, 'account_key': key, 'cost': cost, 'charged_before_execution': True,
                'receipt': {'strategy_plans': {key: account_plan}, 'input_identity': recorded_data['input_identity'],
                            'novelty': {key: {'allowed': True}}, 'execution_purpose': key, 'execution_consumed': True}}
            _require(start == expected_start and result['strategy_plan'] == account_plan
                     and result['input_identity'] == recorded_data['input_identity'], 'ACCOUNT_BINDING_CONFLICT')
            validate_formal_result(result, bundle=prepared['bundle'], window=recorded_data['window'], costs=cost)
            _require(stable_hash(result) == digest and _read(self.path(batch, key + '_SETTLEMENT.json')) ==
                     {'start_hash': stable_hash(start), 'result_hash': digest}, 'SETTLEMENT_CONFLICT')
            results[key] = result
        stats = _read(self.path(batch, 'STATISTICS.json'))
        recomputed = _statistics(plan, self._excess(plan, results))
        _require(stats == report['statistics'] and report['plan_hash'] == stable_hash(plan)
                 and stats == recomputed and report['data_evidence_hash'] == stable_hash(evidence), 'REPORT_BINDING_CONFLICT')
        decisions = adjudicate(plan=plan, evidence=evidence, results=results, statistics=stats,
            method_approved=_read(self.path(batch, 'CALIBRATION.json'))['method_approved'])
        _require(report['decisions'] == decisions and report['strategy_qualified'] ==
                 any(v['strategy_qualified'] for v in decisions.values()), 'REPORT_DECISION_CONFLICT')
        return report

    def assessment_for(self, strategy_id):
        for plan in self._plans():
            for member, frozen in plan['family'].items():
                if frozen and frozen['strategy_id'] == strategy_id:
                    result = self.status(plan['batch_id'])
                    decision = result['decisions'][member] if 'decisions' in result else {
                        'decision': 'CONTINUE_RESEARCH', 'strategy_qualified': False,
                        'reason_codes': result['reason_codes']}
                    return {**decision, 'batch_id': plan['batch_id'], 'report_hash': stable_hash(result),
                            'symbols': plan['symbols'], 'source_identity': plan['source_identity']}
        return None

    def feedback(self, batch):
        report = self.report(batch)
        return {'purpose': 'NEW_VERSION_REQUIRES_NEW_FUTURE_CONFIRMATION',
                'source_report_hash': stable_hash(report),
                'candidates': {key: {'decision': value['decision'], 'reason_codes': value['reason_codes']}
                               for key, value in report['decisions'].items()}}
