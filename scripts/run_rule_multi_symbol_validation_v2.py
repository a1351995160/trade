"""多证券历史账户的有限工程核验，不生成候选、不授予独立或策略资格。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from scripts.run_historical_process_research_v1 import build_bundle
from chanlun_trader.research_factory.bounded_research_v1 import _put, _read, source_identity
from chanlun_trader.research_factory.bounded_research_v2 import fixed_reference_payload
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
from chanlun_trader.research_factory.exploration_governance import read_json
from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock
from chanlun_trader.research_factory.research_rule_strategy_v2 import ResearchRuleStrategyV2
from chanlun_trader.research_factory.rule_account_backend_v2 import RuleAccountBackendV2, rule_input_identity
from chanlun_trader.research_factory.strategy_interface_v1 import prepare, run


def execute(*, data_root, symbols, output, approval_statement):
    output = Path(output).absolute()
    if output.resolve() != output:
        raise ValueError('MULTI_SYMBOL_OUTPUT_REDIRECTED')
    with ObjectiveMutationLock.for_resource(output/'RUN.json'):
        return _execute(data_root=data_root, symbols=symbols, output=output,
                        approval_statement=approval_statement)


def _saved_binding(governance, name, plan, identity):
    receipt = read_json(governance.receipt_path)
    start = read_json(governance.root/(name+'_START.json'))
    budget = read_json(governance.budget_path)
    if (receipt['receipt_id'] != stable_hash({k:v for k,v in receipt.items() if k != 'receipt_id'})
            or receipt['strategy_plans'] != {name:plan} or receipt['input_identity'] != identity
            or receipt['objective_id'] != governance.objective_id
            or receipt['budget_path'] != str(governance.budget_path)
            or receipt['source']['approved_plan_ids'] != {name:plan['plan_id']}
            or start['receipt_id'] != receipt['receipt_id'] or start['kind'] != name
            or start['counted_before_account_calculation'] is not True
            or budget['settled_reservations'].get(start['reservation']) != 'CONSUMED'
            or start['reservation'] in budget['active_reservations']):
        raise ValueError('MULTI_SYMBOL_GOVERNANCE_BINDING_CONFLICT')
    SearchBudgetRegistryV1(governance.objective_id, governance.budget_path).reconcile(
        expected_used={(governance.budget_kind,receipt['receipt_id']+':'+name):1})
    if (governance.root/'REVOKED.json').exists():
        raise PermissionError('MULTI_SYMBOL_REVOKED')
    return receipt, start


def _execute(*, data_root, symbols, output, approval_statement):
    window, bundle, coverage = build_bundle(data_root, symbols=symbols)
    identity = rule_input_identity(bundle, window)
    proposal = fixed_reference_payload()
    proposal.update(hypothesis='按代码冻结多股共享账户工程核验，不是收益选择或正式基准。',
                    change_reason='仅扩大工程证券覆盖，不搜索参数。', target_weight=1/len(symbols))
    name = 'MULTI_SYMBOL_ENGINEERING'
    strategy = ResearchRuleStrategyV2(proposal, strategy_id=name)
    backend = RuleAccountBackendV2(window, max_positions=len(symbols),
                                   max_symbol_exposure_bps=10000//len(symbols))
    plan = prepare(strategy, backend)
    output = Path(output).absolute()
    if output.resolve() != output:
        raise ValueError('MULTI_SYMBOL_OUTPUT_REDIRECTED')
    source = source_identity()
    scope = {'version':'MULTI_SYMBOL_ENGINEERING_V2', 'plan':plan, 'input_identity':identity,
             'source_identity':source, 'coverage':coverage, 'source_hashes':bundle['source_hashes'],
             'purpose':'FIXED_SCOPE_ENGINEERING_NOT_STRATEGY_SEARCH', 'strategy_qualified':False}
    _put(output/'SCOPE.json', scope)
    governance = StrategyBatchGovernanceV1(output/'governance', output/'search_budget_registry.json',
                                         'AUTONOMOUS_COMPLETION_MULTI_SYMBOL_V2', {name:plan})
    result_path = output/'RESULT.json'
    def summarize(result, seconds):
        summary = {'status':'REAL_HISTORICAL_ENGINEERING_REPLAY_PASSED', 'symbols':window['symbols'],
            'account_days':len(result['daily_accounts']), 'cash_events':len(bundle['events']),
            'reconciliation':result['reconciliation'], 'metrics':result['metrics'],
            'result_hash':stable_hash(result), 'repeat_identical':True,
            'runtime_seconds':seconds, 'strategy_qualified':False,
            'independent_evidence':False, 'historical_available_at_verified':False,
            'limitations':['只证明冻结证券与资金范围；不代表全A股、独立验证或正式统计范围。']}
        _put(output/'SUMMARY.json', summary)
        return summary
    proof_path = output/'VERIFIED_EXECUTION.json'
    def commit_verified(proof):
        receipt, start = _saved_binding(governance, name, plan, identity)
        result = proof['result']
        if (proof['scope_hash'] != stable_hash(scope) or proof['receipt_id'] != receipt['receipt_id']
                or proof['start_hash'] != stable_hash(start) or proof['repeat_hash'] != stable_hash(result)):
            raise ValueError('MULTI_SYMBOL_REPLAY_PROOF_CONFLICT')
        if result['strategy_plan'] != plan or result['input_identity'] != identity:
            raise ValueError('MULTI_SYMBOL_SAVED_INPUT_CONFLICT')
        _put(result_path, result)
        settlement_path = governance.root/(name+'_SETTLEMENT.json')
        if not settlement_path.exists():
            governance.settle(name, completed=True, seconds=proof['runtime_seconds'],
                              result_hash=stable_hash(result))
        settlement = read_json(settlement_path)
        if (settlement['completed'] is not True or settlement['error'] is not None
                or any(settlement.get(k) != v for k,v in start.items())
                or settlement['result_sha256'] != stable_hash(result)):
            raise ValueError('MULTI_SYMBOL_SETTLEMENT_CONFLICT')
        return summarize(result, settlement['wall_seconds'])
    if proof_path.exists():
        return commit_verified(_read(proof_path))
    if result_path.exists():
        raise ValueError('MULTI_SYMBOL_REPLAY_PROOF_MISSING')
    if not governance.receipt_path.exists():
        governance.confirm({'origin':'USER_EXPLICIT_CURRENT_TASK', 'statement':approval_statement,
            'approved_plan_ids':{name:plan['plan_id']},
            'expires_at':(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()},
            {'input_identity':identity, 'novelty':{name:{'allowed':True,'plan_id':plan['plan_id'],
                'reason':'FIXED_ENGINEERING_SCOPE_NOT_NEW_SEARCH_CANDIDATE'}}})
    governance.start(name)
    started = time.monotonic()
    def guard():
        if source_identity() != source:
            raise PermissionError('MULTI_SYMBOL_SOURCE_CHANGED')
        return governance.active_execution(name)
    try:
        def account():
            return run(strategy, backend, frame=bundle, actions=bundle['events'],
                       input_identity=identity, active_check=guard)
        result = account()
        repeated = account()
        if stable_hash(result) != stable_hash(repeated):
            raise ValueError('MULTI_SYMBOL_REPLAY_CONFLICT')
        receipt, start = _saved_binding(governance, name, plan, identity)
        proof = {'result':result, 'repeat_hash':stable_hash(repeated), 'scope_hash':stable_hash(scope),
                 'receipt_id':receipt['receipt_id'], 'start_hash':stable_hash(start),
                 'runtime_seconds':time.monotonic()-started}
        _put(proof_path, proof)
    except Exception as exc:
        governance.settle(name, completed=False, seconds=time.monotonic()-started,
                          result_hash=None, error=type(exc).__name__+':'+str(exc))
        raise
    return commit_verified(proof)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',required=True,type=Path)
    parser.add_argument('--symbols',required=True,nargs='+')
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--approval-statement',required=True)
    print(json.dumps(execute(**vars(parser.parse_args())),ensure_ascii=False,indent=2))
