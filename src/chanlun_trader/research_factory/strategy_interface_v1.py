"""策略接入合同：本地可信规则插件只接收快照，账户行为由后端执行。"""
from dataclasses import asdict, dataclass
from copy import deepcopy
from pathlib import Path
from typing import Protocol
import hashlib
import inspect
import math

from .common import stable_hash


@dataclass(frozen=True)
class Requirements:
    asset: str
    frequency: str
    price_view: str
    fields: tuple[str, ...]
    warmup_sessions: int
    intents: tuple[str, ...]
    execution: str = 'NEXT_SESSION_OPEN'
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class TargetWeight:
    weight: float
    increase_existing: bool = True


@dataclass(frozen=True)
class QuantityOrder:
    side: str
    quantity: int
    lot_id: str | None = None


@dataclass(frozen=True)
class BucketOrder:
    bucket: str
    order: QuantityOrder
    key: str
    capital_limit: float | None = None
    metadata: dict | None = None


@dataclass(frozen=True)
class BucketDecision:
    orders: tuple[BucketOrder, ...]
    state: dict
    records: tuple[dict, ...] = ()


@dataclass(frozen=True)
class Decision:
    reason: str
    intent: TargetWeight | QuantityOrder | None = None
    state: dict | None = None
    metadata: dict | None = None


@dataclass(frozen=True)
class Context:
    # history是截止当前收盘的副本；account/state为副本，无账户可写引用。
    history: object
    calendar: tuple[int, ...]
    index: int
    account: dict
    state: dict


class Strategy(Protocol):
    strategy_id: str
    parameters: dict
    requirements: Requirements
    source_files: tuple[str, ...]


class CloseStrategy(Strategy, Protocol):
    def on_close(self, context: Context) -> Decision | BucketDecision: ...


class RankedRuleStrategy(Strategy, Protocol):
    def build_rules(self): ...


def describe(strategy: Strategy) -> dict:
    import re
    if not isinstance(strategy.strategy_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',strategy.strategy_id) or strategy.requirements.warmup_sessions < 0:
        raise ValueError('INVALID_STRATEGY_DECLARATION')
    source=inspect.getsourcefile(type(strategy))
    if source is None:raise ValueError('STRATEGY_SOURCE_FILE_REQUIRED')
    paths={str(Path(source).resolve()),*map(str,strategy.source_files)}
    hashes={str(Path(p).resolve()):hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sorted(paths)}
    # 参数和显式依赖源码属于身份；改规则不能复用旧回执。
    value={'interface':'STRATEGY_INTERFACE_V1','strategy_id':strategy.strategy_id,
           'parameters':deepcopy(strategy.parameters),'requirements':asdict(strategy.requirements),
           'implementation_options':deepcopy(getattr(strategy,'implementation_options',{})),
           'source_hashes':hashes}
    # 持久化后tuple/list必须保持同一合同。
    import json
    return json.loads(json.dumps(value,allow_nan=False))


def validate_decision(value: Decision, requirements: Requirements) -> None:
    if not isinstance(value,Decision) or not isinstance(value.reason,str) or not value.reason:
        raise ValueError('INVALID_STRATEGY_DECISION')
    intent=value.intent
    if intent is None:return
    if isinstance(intent,TargetWeight):
        if 'TARGET_WEIGHT' not in requirements.intents:raise ValueError('UNDECLARED_INTENT')
        if isinstance(intent.weight,bool) or not isinstance(intent.weight,(int,float)) or not math.isfinite(intent.weight) or not 0<=intent.weight<=1:
            raise ValueError('UNSUPPORTED_TARGET_WEIGHT')
    elif isinstance(intent,QuantityOrder):
        if 'QUANTITY_ORDER' not in requirements.intents:raise ValueError('UNDECLARED_INTENT')
        if intent.side not in ('BUY','SELL') or type(intent.quantity) is not int or intent.quantity<=0 or intent.quantity%100:
            raise ValueError('INVALID_QUANTITY_ORDER')
        if intent.lot_id is not None and (intent.side!='SELL' or not isinstance(intent.lot_id,str)):
            raise ValueError('INVALID_LOT_BINDING')
    else:raise ValueError('UNSUPPORTED_INTENT')


def prepare(strategy: Strategy, backend, runtime=None) -> dict:
    """无行情、无预算写入的接入检查；不支持的能力在执行前拒绝。"""
    if hasattr(strategy,'validate'):strategy.validate()
    if hasattr(backend,'validate_strategy'):backend.validate_strategy(strategy)
    declaration=describe(strategy)
    backend.check(strategy.requirements)
    plan={'strategy':declaration,'backend':backend.describe()}
    if runtime is not None:
        for path,digest in runtime['source_hashes'].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=digest:raise PermissionError('RUNTIME_SOURCE_CHANGED')
        plan['runtime']=deepcopy(runtime)
    return {**plan,'plan_id':stable_hash(plan)}


def backend_for(strategy,options=None):
    """按账户能力选后端；注册新候选不修改此处的策略名称列表。"""
    options=options or {}
    if strategy.requirements.asset=='A_SHARE':
        from .stock_strategy_v1 import StockAccountBackend
        if options:raise ValueError('STOCK_BACKEND_OPTIONS_UNSUPPORTED')
        backend=StockAccountBackend()
    elif strategy.requirements.asset=='ETF':
        from .etf_grid_account_v1 import ETFDailyBackend,ETFBucketBackend
        if 'SEGREGATED_BUCKETS' in strategy.requirements.capabilities:
            if set(options)-{'budgets'}:raise ValueError('BUCKET_BACKEND_OPTIONS_UNSUPPORTED')
            backend=ETFBucketBackend(**({'budgets':strategy.account_budgets} if not options and hasattr(strategy,'account_budgets') else options))
        else:
            if options:raise ValueError('ETF_BACKEND_OPTIONS_UNSUPPORTED')
            backend=ETFDailyBackend()
    else:raise ValueError('NO_SUPPORTED_ACCOUNT_BACKEND')
    backend.check(strategy.requirements)
    return backend


def evaluate_novelty(plans: dict, historical_candidates: list) -> dict:
    """使用原正式判重组件；调用方先取得合法历史投影，不自动扫描报告。"""
    from .novelty import CandidateNoveltyGateV2
    candidates=[]
    for name,plan in plans.items():
        params=plan['strategy']['parameters']
        candidates.append({'candidate_id':name,'candidate_hash':plan['plan_id'],
            'mechanism':params.get('rule_definition',params),
            'factor_ids':params.get('factor_ids',[]),
            'semantic_fingerprint':params.get('rule_definition',params),'parameter_fingerprint':params})
    return {c['candidate_id']:{**CandidateNoveltyGateV2().evaluate(c,historical_candidates=historical_candidates,
        same_batch_candidates=[other for other in candidates if other is not c]).to_dict(),
        'plan_id':c['candidate_hash']} for c in candidates}


def run(strategy: Strategy, backend, *, frame, actions, input_identity, active_check, runtime=None):
    """所有新策略公共入口；许可必须绑定准备结果和输入，不自行授予曝光。"""
    plan=prepare(strategy,backend,runtime)
    def guard():
        receipt=active_check()
        if receipt.get('strategy_plans',{}).get(strategy.strategy_id)!=plan or receipt.get('input_identity')!=input_identity:
            raise PermissionError('STRATEGY_PLAN_OR_INPUT_NOT_AUTHORIZED')
        if receipt.get('novelty',{}).get(strategy.strategy_id,{}).get('allowed') is not True:
            raise PermissionError('STRATEGY_NOVELTY_REQUIRED')
        if receipt.get('execution_purpose')!=strategy.strategy_id or receipt.get('execution_consumed') is not True:
            raise PermissionError('STRATEGY_START_AND_CONSUMPTION_REQUIRED')
        # 重复prepare检查源码由worker在加载/冻结边界执行，循环只核对参数快照。
        if (strategy.parameters!=plan['strategy']['parameters'] or
            stable_hash(asdict(strategy.requirements))!=stable_hash(plan['strategy']['requirements']) or
            getattr(strategy,'implementation_options',{})!=plan['strategy']['implementation_options']):
            raise PermissionError('STRATEGY_PARAMETERS_CHANGED')
        return receipt
    guard()
    result=backend.run(strategy,frame,actions,guard)
    if prepare(strategy,backend,runtime)!=plan:raise PermissionError('STRATEGY_SOURCE_CHANGED_DURING_RUN')
    from .strategy_report_v1 import build_report
    result['strategy_plan']=plan
    original=result.get('metrics')
    metrics={k:None for k in ('net_return','max_drawdown','total_fees','trade_count')}
    if original is not None:
        metrics.update(net_return=original.get('net_return',original.get('train_net_return')),
            max_drawdown=original.get('max_drawdown'),total_fees=original.get('total_fees'),
            trade_count=original.get('trade_count',len(result['fills'])))
    ledger_key=next(k for k in ('ledger','ledgers','final_account_checkpoint') if k in result)
    result['report']=build_report(strategy={'version':plan['strategy']['interface'],**plan['strategy']},
        stage='ACCOUNT_BACKTEST',status=result.get('status','MODEL_ACCOUNT_COMPLETED'),
        reasons=[] if original is not None else [result.get('status','METRICS_UNAVAILABLE')],metrics=metrics,
        artifacts={'trades':{'in_memory':'fills'},'ledger':{'in_memory':ledger_key},
                   'source_result':{'in_memory':True,'plan_id':plan['plan_id']}},
        benchmark={'status':'NOT_RUN'},limitations=['模型结果；未自动授予统计资格。'])
    return result
