"""本次ETF指令的用途回执；同一权威预算新增两条固定账户曝光，不复活旧计划。"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
from .budget import SearchBudgetRegistryV1
from .common import stable_hash
from .exploration_governance import immutable, read_json
from .mutation_boundary import ObjectiveMutationLock
from .etf_grid_account_v1 import CONTRACT

KINDS=('GRID_MAIN','BUY_HOLD_BENCHMARK')


class ETFAccountGovernanceV1:
    kinds=KINDS
    budget_kind='etf_account_v1'
    def __init__(self,root,budget_path,objective_id):
        self.root=Path(root);self.budget_path=Path(budget_path);self.objective_id=objective_id
        self.receipt_path=self.root/'CONFIRMATION.json'

    def lock(self):return ObjectiveMutationLock.for_resource(self.budget_path)

    def confirm(self,source,inputs):
        if source.get('statement')!='先完成当前ETF账户验证' or source.get('origin')!='USER_EXPLICIT_CURRENT_TASK':
            raise PermissionError('CURRENT_ETF_APPROVAL_REQUIRED')
        if source.get('spread_model_reply')!='允许按上述模型假设回测':raise PermissionError('SPREAD_APPROVAL_REQUIRED')
        if inputs.get('basic_input_checks')!='MODEL_BASIC_INPUT_CHECKS_PASSED' or not inputs.get('source_manifest_hash'):
            raise PermissionError('ETF_INPUT_OR_CODE_IDENTITY_REQUIRED')
        if self.receipt_path.exists():raise PermissionError('EXISTING_ETF_RECEIPT_RECONCILE_NO_RECONFIRM')
        with self.lock():
            now=datetime.now(timezone.utc)
            receipt={'objective_id':self.objective_id,'contract':CONTRACT,'inputs':inputs,'source':source,
                'purposes':list(KINDS),'recorded_at':now.isoformat(),'expires_at':(now+timedelta(minutes=30)).isoformat(),
                'wall_seconds_cap':1800,'cap_basis':'OPERATOR_SELF_LIMIT_FOR_THIS_TWO_ACCOUNT_REQUEST',
                'authorization_basis':'NEW_ETF_REQUEST_NOT_EXTENSION_OF_EXPIRED_STOCK_PLAN',
                'budget_path':str(self.budget_path),'repair_allowance':0}
            receipt['receipt_id']=stable_hash(receipt)
            immutable(self.receipt_path,receipt)
            budget=SearchBudgetRegistryV1(self.objective_id,self.budget_path)
            for kind in KINDS:budget.register('etf_account_v1',receipt['receipt_id']+':'+kind,1)
        return receipt

    def active(self):
        receipt=read_json(self.receipt_path)
        if receipt['receipt_id']!=stable_hash({k:v for k,v in receipt.items() if k!='receipt_id'}):
            raise PermissionError('ETF_RECEIPT_HASH_CONFLICT')
        if receipt['objective_id']!=self.objective_id or receipt['contract']!=CONTRACT:raise PermissionError('ETF_CONTRACT_CONFLICT')
        if (self.root/'REVOKED.json').exists():raise PermissionError('ETF_REVOKED')
        if datetime.now(timezone.utc)>=datetime.fromisoformat(receipt['expires_at']):raise PermissionError('ETF_EXPIRED')
        return receipt

    def start(self,kind):
        if kind not in self.kinds:raise PermissionError('UNAPPROVED_ETF_VARIANT')
        with self.lock():
            receipt=self.active();path=self.root/(kind+'_START.json')
            if path.exists():raise PermissionError('ETF_ALREADY_ATTEMPTED_NO_REPLAY')
            for other in self.kinds:
                if (self.root/(other+'_START.json')).exists() and not (self.root/(other+'_SETTLEMENT.json')).exists():
                    raise PermissionError('ETF_UNSETTLED_RECONCILIATION_REQUIRED')
            budget=SearchBudgetRegistryV1(self.objective_id,self.budget_path)
            reservation=budget.reserve(self.budget_kind,receipt['receipt_id']+':'+kind)
            record={'kind':kind,'reservation':reservation,'receipt_id':receipt['receipt_id'],
                'started_at':datetime.now(timezone.utc).isoformat(),'counted_before_account_calculation':True}
            immutable(path,record)
            budget.consume(reservation)
            return record

    def settle(self,kind,*,completed,seconds,result_hash,error=None):
        with self.lock():
            start=read_json(self.root/(kind+'_START.json'))
            budget=SearchBudgetRegistryV1(self.objective_id,self.budget_path)
            budget.reconcile(expected_used={(self.budget_kind,start['receipt_id']+':'+kind):1})
            record={**start,'completed':completed,'wall_seconds':seconds,'result_sha256':result_hash,
                'error':error,'settled_at':datetime.now(timezone.utc).isoformat(),'repair_exposures':0}
            immutable(self.root/(kind+'_SETTLEMENT.json'),record)
            return record


class ETFFamilyGovernanceV1(ETFAccountGovernanceV1):
    """当前四候选加一个共享基准；不延长旧批准、不增加修复或搜索用途。"""
    from .etf_trend_risk_hypothesis_v1 import FAMILY, BENCHMARK
    kinds=(*FAMILY,BENCHMARK)
    budget_kind='etf_family_account_v1'

    def confirm(self,source,inputs):
        from .etf_trend_risk_hypothesis_v1 import FAMILY,BENCHMARK,contract
        if source.get('origin')!='USER_EXPLICIT_CURRENT_TASK' or source.get('statement')!='你直接走完为止，告诉我结果就行了':
            raise PermissionError('CURRENT_FAMILY_APPROVAL_REQUIRED')
        if inputs.get('contracts')!={k:contract(k) for k in self.kinds}:
            raise PermissionError('FAMILY_CONTRACT_CONFLICT')
        if any(inputs.get('novelty',{}).get(k,{}).get('allowed') is not True for k in FAMILY):
            raise PermissionError('FAMILY_NOVELTY_REJECTED')
        if inputs.get('basic_input_checks')!='MODEL_BASIC_INPUT_CHECKS_PASSED' or not inputs.get('source_manifest_hash') or not inputs.get('input_identity'):
            raise PermissionError('FAMILY_INPUT_OR_SOURCE_REQUIRED')
        if self.receipt_path.exists():raise PermissionError('EXISTING_ETF_RECEIPT_RECONCILE_NO_RECONFIRM')
        with self.lock():
            now=datetime.now(timezone.utc)
            receipt={**inputs,'objective_id':self.objective_id,'source':source,'purposes':list(self.kinds),
                'recorded_at':now.isoformat(),'expires_at':(now+timedelta(minutes=60)).isoformat(),
                'wall_seconds_cap':4500,'worker_seconds_cap':900,'memory_mib':2048,'concurrency':1,
                'synthetic_calibration_charged_seconds':900,'account_remaining_seconds':3600,
                'cap_basis':'CURRENT_FIXED_FIVE_ACCOUNT_REQUEST_OPERATOR_75_MINUTE_LIMIT',
                'authorization_basis':'CURRENT_BATCH_COMPLETION_NOT_OLD_PLAN_RENEWAL',
                'budget_path':str(self.budget_path),'repair_allowance':0,'qualification':False}
            receipt['novelty'][BENCHMARK]={'allowed':True,'reason':'FROZEN_SHARED_CONTROL_NOT_SEARCH_CANDIDATE'}
            receipt['receipt_id']=stable_hash(receipt)
            immutable(self.receipt_path,receipt)
            budget=SearchBudgetRegistryV1(self.objective_id,self.budget_path)
            for kind in self.kinds:budget.register(self.budget_kind,receipt['receipt_id']+':'+kind,1)
        return receipt


    def active(self):
        from .etf_trend_risk_hypothesis_v1 import contract
        receipt=read_json(self.receipt_path)
        if receipt['receipt_id']!=stable_hash({k:v for k,v in receipt.items() if k!='receipt_id'}):
            raise PermissionError('ETF_RECEIPT_HASH_CONFLICT')
        if receipt['objective_id']!=self.objective_id or receipt['contracts']!={k:contract(k) for k in self.kinds}:
            raise PermissionError('ETF_CONTRACT_CONFLICT')
        if (self.root/'REVOKED.json').exists():raise PermissionError('ETF_REVOKED')
        if datetime.now(timezone.utc)>=datetime.fromisoformat(receipt['expires_at']):raise PermissionError('ETF_EXPIRED')
        return receipt


class StrategyBatchGovernanceV1(ETFAccountGovernanceV1):
    """按冻结计划清单登记用途；增加规则插件不再增加策略名称分支。"""
    budget_kind='strategy_interface_account_v1'

    def __init__(self,root,budget_path,objective_id,plans):
        from copy import deepcopy
        import re
        super().__init__(root,budget_path,objective_id)
        self.plans=deepcopy(plans);self.kinds=tuple(plans)
        if not plans:raise ValueError('EMPTY_STRATEGY_BATCH')
        for name,plan in plans.items():
            if not re.fullmatch(r'[A-Za-z0-9_-]+',name) or plan['strategy']['strategy_id']!=name or plan['plan_id']!=stable_hash({k:v for k,v in plan.items() if k!='plan_id'}):
                raise ValueError('STRATEGY_PLAN_IDENTITY_CONFLICT')

    def confirm(self,source,inputs):
        expected={name:plan['plan_id'] for name,plan in self.plans.items()}
        if source.get('origin')!='USER_EXPLICIT_CURRENT_TASK' or not source.get('statement') or source.get('approved_plan_ids')!=expected:
            raise PermissionError('EXACT_STRATEGY_PLANS_APPROVAL_REQUIRED')
        if not inputs.get('input_identity') or any(inputs.get('novelty',{}).get(name,{}).get('allowed') is not True or inputs['novelty'][name].get('plan_id')!=self.plans[name]['plan_id'] for name in self.kinds):
            raise PermissionError('STRATEGY_INPUT_OR_NOVELTY_REQUIRED')
        expires=datetime.fromisoformat(source['expires_at'])
        now=datetime.now(timezone.utc)
        if expires.tzinfo is None or expires<=now:raise PermissionError('STRATEGY_APPROVAL_EXPIRED')
        if self.receipt_path.exists():raise PermissionError('EXISTING_STRATEGY_RECEIPT_NO_RECONFIRM')
        with self.lock():
            receipt={'strategy_plans':self.plans,'source':source,'input_identity':inputs['input_identity'],
                'novelty':inputs['novelty'],'objective_id':self.objective_id,'purposes':list(self.kinds),
                'recorded_at':now.isoformat(),'expires_at':expires.isoformat(),'budget_path':str(self.budget_path),
                'repair_allowance':0,'exposures':len(self.kinds)}
            receipt['receipt_id']=stable_hash(receipt);immutable(self.receipt_path,receipt)
            budget=SearchBudgetRegistryV1(self.objective_id,self.budget_path)
            for kind in self.kinds:budget.register(self.budget_kind,receipt['receipt_id']+':'+kind,1)
        return receipt

    def active_execution(self,name):
        if name not in self.kinds:raise PermissionError('UNAPPROVED_STRATEGY')
        receipt=self.active();start=read_json(self.root/(name+'_START.json'))
        if start['receipt_id']!=receipt['receipt_id'] or (self.root/(name+'_SETTLEMENT.json')).exists():
            raise PermissionError('STRATEGY_EXECUTION_NOT_ACTIVE')
        budget=SearchBudgetRegistryV1(self.objective_id,self.budget_path)
        budget.reconcile(expected_used={(self.budget_kind,receipt['receipt_id']+':'+name):1})
        return {**receipt,'execution_purpose':name,'execution_consumed':True}

    def active(self):
        receipt=read_json(self.receipt_path)
        if receipt['receipt_id']!=stable_hash({k:v for k,v in receipt.items() if k!='receipt_id'}):
            raise PermissionError('STRATEGY_RECEIPT_HASH_CONFLICT')
        if receipt['objective_id']!=self.objective_id or receipt['strategy_plans']!=self.plans or receipt['budget_path']!=str(self.budget_path):
            raise PermissionError('STRATEGY_PLAN_CONFLICT')
        if (self.root/'REVOKED.json').exists():raise PermissionError('STRATEGY_REVOKED')
        if datetime.now(timezone.utc)>=datetime.fromisoformat(receipt['expires_at']):raise PermissionError('STRATEGY_EXPIRED')
        return receipt
