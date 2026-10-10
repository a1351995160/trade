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
        return self._confirm_validated(source,inputs)

    def confirm_campaign_scope(self, campaign, operation_ids, inputs, *, research_binding_ref=None):
        """已有总任务派生精确账户用途，不冒充新的逐候选用户确认。"""
        source = {'origin': 'CAMPAIGN_V1', 'campaign_root': str(campaign.root),
                  'authorization_id': campaign.authorization_id, 'operation_ids': operation_ids,
                  'expires_at': campaign.status()['authorization']['expires_at']}
        if research_binding_ref is not None:
            from .campaign_scope_v1 import CampaignScopeV1
            binding = CampaignScopeV1(campaign).resolve(research_binding_ref)
            source.update(research_binding_ref=research_binding_ref, phase=binding['phase'],
                          batch_id=binding['batch_id'], expires_at=binding['expires_at'])
        validate_campaign_source(source, self.plans, self.objective_id)
        return self._confirm_validated(source, inputs)

    def start(self, name):
        receipt = self.active()
        if receipt['source'].get('origin') == 'CAMPAIGN_V1':
            source = receipt['source']
            campaign = validate_campaign_source(source, self.plans, self.objective_id)
            if name not in source['operation_ids']:
                raise PermissionError('CAMPAIGN_ACCOUNT_PURPOSE_NOT_AUTHORIZED')
            # 先记总资源START；此后崩溃保留未知消费，绝不再次派发。
            campaign.start_operation(source['operation_ids'][name])
        return super().start(name)

    def confirm_research_scope(self,session,name):
        """父研究任务覆盖本候选；不伪造用户逐候选确认，也不扩充父任务范围。"""
        if self.root != session.path(name,'governance') or self.budget_path != session.path('search_budget_registry.json'):
            raise PermissionError('STRATEGY_SCOPE_ROOT_CONFLICT')
        scope=session.authorize_plan(name,self.plans)
        if self.objective_id!=scope['objective_id']:
            raise PermissionError('STRATEGY_SCOPE_OBJECTIVE_CONFLICT')
        source={'origin':'BOUNDED_RESEARCH_SCOPE','scope_root':str(session.root),
                'scope_id':scope['scope_id'],'candidate_id':name,'expires_at':scope['expires_at']}
        return self._confirm_validated(source,{'input_identity':scope['input_manifest']['input_identity'],
            'novelty':{name:{'allowed':True,'plan_id':self.plans[name]['plan_id'],
                       'reason':'FROZEN_REFERENCE' if name=='REFERENCE' else 'BOUNDED_RULE_IDENTITY_CHECKED'}}})

    def _confirm_validated(self,source,inputs):
        if not inputs.get('input_identity') or any(inputs.get('novelty',{}).get(name,{}).get('allowed') is not True or inputs['novelty'][name].get('plan_id')!=self.plans[name]['plan_id'] for name in self.kinds):
            raise PermissionError('STRATEGY_INPUT_OR_NOVELTY_REQUIRED')
        expires=datetime.fromisoformat(source['expires_at'])
        now=datetime.now(timezone.utc)
        if expires.tzinfo is None or expires<=now:raise PermissionError('STRATEGY_APPROVAL_EXPIRED')
        if self.receipt_path.exists():raise PermissionError('EXISTING_STRATEGY_RECEIPT_NO_RECONFIRM')
        for name, plan in self.plans.items():
            profile = plan.get('runtime', {}).get('execution_profile')
            if profile is not None:
                from .universe_execution_profile_v1 import validate_execution_profile, CONTINUOUS_PROFILE, ENGINEERING_PURPOSE
                validate_execution_profile(profile)
                if (profile['profile_id'] == CONTINUOUS_PROFILE and source.get('origin') != 'CAMPAIGN_V1'
                        and source.get('engineering_authorization') != ENGINEERING_PURPOSE):
                    raise PermissionError('ENGINEERING_CONTINUOUS_REFERENCE_AUTHORIZATION_REQUIRED')
        with self.lock():
            receipt={'strategy_plans':self.plans,'source':source,'input_identity':inputs['input_identity'],
                'novelty':inputs['novelty'],'objective_id':self.objective_id,'purposes':list(self.kinds),
                'recorded_at':now.isoformat(),'expires_at':expires.isoformat(),'budget_path':str(self.budget_path),
                'repair_allowance':0,'exposures':len(self.kinds)}
            receipt['receipt_id']=stable_hash(receipt);immutable(self.receipt_path,receipt)
            budget=SearchBudgetRegistryV1(self.objective_id,self.budget_path)
            for kind in self.kinds:budget.register(self.budget_kind,receipt['receipt_id']+':'+kind,1)
        return receipt

    def reconcile_research_scope(self,session,name):
        """仅补齐确认已提交、账户尚未启动时的同一确定性用途桶。"""
        if self.root != session.path(name,'governance') or self.budget_path != session.path('search_budget_registry.json'):
            raise PermissionError('STRATEGY_SCOPE_ROOT_CONFLICT')
        with self.lock():
            receipt=self.active()
            scope=session.authorize_plan(name,self.plans)
            source=receipt['source']
            if (source.get('origin')!='BOUNDED_RESEARCH_SCOPE' or source.get('scope_id')!=scope['scope_id']
                    or source.get('candidate_id')!=name or set(self.kinds)!={name}
                    or receipt['input_identity']!=scope['input_manifest']['input_identity']):
                raise PermissionError('STRATEGY_SCOPE_IDENTITY_CONFLICT')
            if (self.root/(name+'_START.json')).exists():
                raise PermissionError('ETF_ALREADY_ATTEMPTED_NO_REPLAY')
            budget=SearchBudgetRegistryV1(self.objective_id,self.budget_path)
            budget.register(self.budget_kind,receipt['receipt_id']+':'+name,1)
            return receipt

    def active_execution(self,name):
        if name not in self.kinds:raise PermissionError('UNAPPROVED_STRATEGY')
        receipt=self.active();start=read_json(self.root/(name+'_START.json'))
        if start['receipt_id']!=receipt['receipt_id'] or (self.root/(name+'_SETTLEMENT.json')).exists():
            raise PermissionError('STRATEGY_EXECUTION_NOT_ACTIVE')
        if receipt['source'].get('origin') == 'CAMPAIGN_V1':
            campaign = validate_campaign_source(receipt['source'], self.plans, self.objective_id)
            operation = campaign.status()['operations'][receipt['source']['operation_ids'][name]]
            if operation['status'] != 'RUNNING':
                raise PermissionError('CAMPAIGN_ACCOUNT_OPERATION_NOT_RUNNING')
        budget=SearchBudgetRegistryV1(self.objective_id,self.budget_path)
        budget.reconcile(expected_used={(self.budget_kind,receipt['receipt_id']+':'+name):1})
        return {**receipt,'execution_purpose':name,'execution_consumed':True}

    def _segment_profile(self, name, profile):
        from .universe_execution_profile_v1 import validate_execution_profile
        if name not in self.plans:
            raise PermissionError('UNAPPROVED_STRATEGY')
        frozen = self.plans[name].get('runtime', {}).get('execution_profile')
        if frozen is None:
            raise PermissionError('LEGACY_ACCOUNT_CONTINUATION_NOT_SUPPORTED')
        value = validate_execution_profile(frozen)
        if profile is not None and validate_execution_profile(profile) != value:
            raise PermissionError('STRATEGY_SEGMENT_PROFILE_CONFLICT')
        return value

    def dispatched_execution(self,name):
        """已派发用途的离线结清证据；到期、撤销或暂停不抹去已经发生的消费。"""
        receipt=read_json(self.receipt_path);start=read_json(self.root/(name+'_START.json'))
        if (receipt['receipt_id']!=stable_hash({k:v for k,v in receipt.items() if k!='receipt_id'})
                or receipt['strategy_plans']!=self.plans or receipt['objective_id']!=self.objective_id
                or receipt['budget_path']!=str(self.budget_path)
                or start['receipt_id']!=receipt['receipt_id'] or start['kind']!=name):
            raise PermissionError('STRATEGY_SEGMENT_START_CONFLICT')
        budget=SearchBudgetRegistryV1(self.objective_id,self.budget_path).snapshot()
        if budget['settled_reservations'].get(start['reservation'])!='CONSUMED':
            raise PermissionError('STRATEGY_EXECUTION_NOT_CONSUMED')
        if receipt['source'].get('origin')=='CAMPAIGN_V1':
            validate_campaign_source(receipt['source'],self.plans,self.objective_id)
        return receipt

    def segment_status(self, name, profile=None):
        """只读原用途的不可变段链；不重新登记预算，也不读取绩效。"""
        value = self._segment_profile(name, profile)
        start = read_json(self.root/(name+'_START.json'))
        receipt = read_json(self.receipt_path)
        if (start['receipt_id'] != receipt['receipt_id'] or start['kind'] != name
                or receipt['receipt_id'] != stable_hash({k:v for k,v in receipt.items() if k!='receipt_id'})
                or receipt['strategy_plans'] != self.plans):
            raise PermissionError('STRATEGY_SEGMENT_START_CONFLICT')
        rows=[]; charged=0.; head=None; pending=None
        charge_paths=set()
        for path in sorted(self.root.glob(name+'_SEGMENT_*_DISPATCH.json')):
            dispatch=read_json(path); number=len(rows)+1
            if (dispatch.get('segment_number') != number or dispatch.get('previous_head') != head
                    or dispatch.get('profile_hash') != value['profile_hash']
                    or dispatch.get('receipt_id') != receipt['receipt_id'] or dispatch.get('kind') != name
                    or dispatch.get('dispatch_id') != stable_hash({k:v for k,v in dispatch.items() if k!='dispatch_id'})
                    or dispatch.get('charged_before') != charged or pending is not None
                    or type(dispatch.get('upper_bound_seconds')) not in (int,float)
                    or not 0 < dispatch['upper_bound_seconds'] <= min(value['worker_seconds'],value['total_seconds']-charged)
                    or (rows and rows[-1]['charge']['outcome'] in ('COMPLETED','FAILED'))):
                raise PermissionError('STRATEGY_SEGMENT_DISPATCH_CHAIN_CONFLICT')
            charge_path=self.root/(name+'_SEGMENT_'+str(number).zfill(6)+'_CHARGE.json')
            row={'dispatch':dispatch, 'charge':None}
            if charge_path.exists():
                charge_paths.add(charge_path)
                charge=read_json(charge_path)
                from .universe_execution_profile_v1 import segment_charge
                seconds,basis=segment_charge(charge.get('measured_seconds'), dispatch['upper_bound_seconds'],
                                             charge.get('evidence_identity'),outcome=charge.get('outcome'))
                if (charge.get('charge_id') != stable_hash({k:v for k,v in charge.items() if k!='charge_id'})
                        or charge.get('dispatch_id') != dispatch['dispatch_id'] or charge.get('seconds') != seconds
                        or charge.get('basis') != basis
                        or charge.get('outcome') not in ('CONTINUE','PAUSED','COMPLETED','FAILED')):
                    raise PermissionError('STRATEGY_SEGMENT_CHARGE_CHAIN_CONFLICT')
                charged+=seconds; head=charge['charge_id']; row['charge']=charge
            else:
                pending=dispatch
            rows.append(row)
        if set(self.root.glob(name+'_SEGMENT_*_CHARGE.json')) != charge_paths:
            raise PermissionError('STRATEGY_SEGMENT_ORPHAN_CHARGE')
        failed=bool(rows and rows[-1]['charge'] and rows[-1]['charge']['outcome']=='FAILED')
        if charged > value['total_seconds'] and not failed:
            raise PermissionError('STRATEGY_SEGMENT_CUMULATIVE_BOUND_EXCEEDED')
        return {'profile':value,'segments':rows,'charged_seconds':charged,'head':head,
                'pending':pending,'next_segment_number':len(rows)+1,
                'remaining_seconds':max(0,value['total_seconds']-charged),
                'resource_overrun':max(0,charged-value['total_seconds']),
                'dispatch_overrun':sum(max(0,row['charge']['seconds']-row['dispatch']['upper_bound_seconds'])
                    for row in rows if row['charge'])}

    def start_segment(self, name, profile=None, segment_number=None):
        """原 START 已消费一次用途；本调用仅继续该用途的计算。"""
        from .universe_execution_profile_v1 import segment_allowance
        with self.lock():
            receipt=self.active_execution(name)
            state=self.segment_status(name, profile)
            if state['pending'] is not None:
                raise PermissionError('STRATEGY_SEGMENT_ALREADY_DISPATCHED')
            if state['segments'] and state['segments'][-1]['charge']['outcome'] in ('COMPLETED','FAILED'):
                raise PermissionError('STRATEGY_SEGMENT_TERMINAL_NO_REPLAY')
            number=state['next_segment_number']
            if segment_number is not None and segment_number != number:
                raise PermissionError('STRATEGY_SEGMENT_NUMBER_CONFLICT')
            now=datetime.now(timezone.utc)
            upper=segment_allowance(state['profile'],state['charged_seconds'],
                seconds_to_expiry=(datetime.fromisoformat(receipt['expires_at'])-now).total_seconds())
            parent=None
            if receipt['source'].get('origin') == 'CAMPAIGN_V1':
                source=receipt['source']; campaign=validate_campaign_source(source,self.plans,self.objective_id)
                parent=campaign.start_execution_segment(source['operation_ids'][name],segment_number=number,
                                                        profile=state['profile'],upper_bound_seconds=upper)
            record={'schema_version':'strategy-account-segment-dispatch-v1','kind':name,
                'receipt_id':receipt['receipt_id'],'segment_number':number,'profile_hash':state['profile']['profile_hash'],
                'charged_before':state['charged_seconds'],'upper_bound_seconds':upper,
                'previous_head':state['head'],'dispatched_at':parent['dispatched_at'] if parent else now.isoformat()}
            record['dispatch_id']=stable_hash(record)
            immutable(self.root/(name+'_SEGMENT_'+str(number).zfill(6)+'_DISPATCH.json'),record)
            return record

    def end_segment(self,name,segment_number,*,seconds=None,evidence_identity=None,outcome='CONTINUE'):
        from .universe_execution_profile_v1 import segment_charge
        if outcome not in ('CONTINUE','PAUSED','COMPLETED','FAILED'):
            raise ValueError('STRATEGY_SEGMENT_OUTCOME_INVALID')
        with self.lock():
            self.dispatched_execution(name)
            state=self.segment_status(name)
            if (state['pending'] is None or type(segment_number) is not int
                    or segment_number != state['pending']['segment_number']):
                raise PermissionError('STRATEGY_SEGMENT_NOT_ACTIVE')
            dispatch=state['pending']; charged,basis=segment_charge(seconds,dispatch['upper_bound_seconds'],evidence_identity,outcome=outcome)
            receipt=read_json(self.receipt_path)
            record={'schema_version':'strategy-account-segment-charge-v1','dispatch_id':dispatch['dispatch_id'],
                'measured_seconds':seconds,'seconds':charged,'basis':basis,'evidence_identity':evidence_identity,
                'outcome':outcome,'charged_at':datetime.now(timezone.utc).isoformat()}
            record['charge_id']=stable_hash(record)
            if receipt['source'].get('origin') == 'CAMPAIGN_V1':
                source=receipt['source']; campaign=validate_campaign_source(source,self.plans,self.objective_id)
                parent=campaign.end_execution_segment(source['operation_ids'][name],segment_number=segment_number,
                    seconds=seconds,evidence_identity=evidence_identity,outcome=outcome)
                record['charged_at']=parent['charged_at'];record['charge_id']=stable_hash({k:v for k,v in record.items() if k!='charge_id'})
            immutable(self.root/(name+'_SEGMENT_'+str(segment_number).zfill(6)+'_CHARGE.json'),record)
            return record

    def reconcile_segment_mirrors(self,name):
        """只补父事件已提交的同身份本地镜像，不派发、不收费、不改授权。"""
        with self.lock():
            receipt=self.dispatched_execution(name)
            if receipt['source'].get('origin')!='CAMPAIGN_V1':return self.segment_status(name)
            source=receipt['source'];campaign=validate_campaign_source(source,self.plans,self.objective_id)
            operation=campaign.status()['operations'][source['operation_ids'][name]]
            profile=self._segment_profile(name,None);charged=0.;head=None
            start=read_json(self.root/(name+'_START.json'))
            if start['receipt_id']!=receipt['receipt_id'] or start['kind']!=name:
                raise PermissionError('STRATEGY_SEGMENT_START_CONFLICT')
            for number,parent in enumerate(operation.get('segments',[]),1):
                if parent['segment_number']!=number or parent['profile_hash']!=profile['profile_hash']:
                    raise PermissionError('STRATEGY_CAMPAIGN_SEGMENT_MIRROR_CONFLICT')
                dispatch={'schema_version':'strategy-account-segment-dispatch-v1','kind':name,
                    'receipt_id':receipt['receipt_id'],'segment_number':number,'profile_hash':profile['profile_hash'],
                    'charged_before':charged,'upper_bound_seconds':parent['upper_bound_seconds'],
                    'previous_head':head,'dispatched_at':parent['dispatched_at']}
                dispatch['dispatch_id']=stable_hash(dispatch)
                prefix=name+'_SEGMENT_'+str(number).zfill(6)
                dispatch_path=self.root/(prefix+'_DISPATCH.json')
                if not dispatch_path.exists():
                    immutable(self.root/(prefix+'_MIRROR_REPAIR.json'),{'version':'CAMPAIGN_SEGMENT_MIRROR_REPAIR_V1',
                        'receipt_id':receipt['receipt_id'],'source_identity':stable_hash(source),
                        'operation_id':source['operation_ids'][name],'dispatch_id':dispatch['dispatch_id'],
                        'parent_dispatch_identity':stable_hash({key:parent[key] for key in
                            ('operation_id','segment_number','profile_hash','upper_bound_seconds','dispatched_at')}),
                        'worker_absent':not (self.root/(prefix+'_WORKER.json')).exists()
                            and not (self.root/(prefix+'_INPUT_ACCESS.json')).exists()})
                immutable(dispatch_path,dispatch)
                if 'basis' in parent:
                    charge={'schema_version':'strategy-account-segment-charge-v1','dispatch_id':dispatch['dispatch_id'],
                        **{key:parent[key] for key in ('measured_seconds','seconds','basis','evidence_identity','outcome','charged_at')}}
                    charge['charge_id']=stable_hash(charge)
                    immutable(self.root/(name+'_SEGMENT_'+str(number).zfill(6)+'_CHARGE.json'),charge)
                    charged+=charge['seconds'];head=charge['charge_id']
                elif (self.root/(name+'_SEGMENT_'+str(number).zfill(6)+'_CHARGE.json')).exists():
                    raise PermissionError('STRATEGY_CAMPAIGN_UNPROVEN_LOCAL_CHARGE')
            state=self.segment_status(name)
            if len(state['segments'])!=len(operation.get('segments',[])) or state['charged_seconds']!=operation.get('active_wall_seconds',0):
                raise PermissionError('STRATEGY_CAMPAIGN_SEGMENT_MIRROR_CONFLICT')
            return state

    def settle(self,kind,*,completed,seconds,result_hash,error=None):
        if self.plans.get(kind,{}).get('runtime',{}).get('execution_profile') is not None:
            state=self.segment_status(kind)
            if state['pending'] is not None or not state['segments']:
                raise PermissionError('STRATEGY_SEGMENT_SETTLEMENT_REQUIRED')
            if seconds != state['charged_seconds']:
                raise PermissionError('STRATEGY_SEGMENT_CUMULATIVE_USAGE_CONFLICT')
            last=state['segments'][-1]['charge']['outcome']
            if completed and last != 'COMPLETED':
                raise PermissionError('STRATEGY_SEGMENT_COMPLETION_NOT_PROVEN')
            receipt=self.dispatched_execution(kind)
            if receipt['source'].get('origin')=='CAMPAIGN_V1':
                import math
                source=receipt['source'];campaign=validate_campaign_source(source,self.plans,self.objective_id)
                campaign.settle_operation(source['operation_ids'][kind],actual={'account_jobs':1,'wall_seconds':max(1,math.ceil(seconds))},
                    outcome='COMPLETED' if completed else 'FAILED',evidence_identity=result_hash or state['head'])
        return super().settle(kind,completed=completed,seconds=seconds,result_hash=result_hash,error=error)

    def active(self):
        receipt=read_json(self.receipt_path)
        if receipt['receipt_id']!=stable_hash({k:v for k,v in receipt.items() if k!='receipt_id'}):
            raise PermissionError('STRATEGY_RECEIPT_HASH_CONFLICT')
        if receipt['objective_id']!=self.objective_id or receipt['strategy_plans']!=self.plans or receipt['budget_path']!=str(self.budget_path):
            raise PermissionError('STRATEGY_PLAN_CONFLICT')
        if (self.root/'REVOKED.json').exists():raise PermissionError('STRATEGY_REVOKED')
        if datetime.now(timezone.utc)>=datetime.fromisoformat(receipt['expires_at']):raise PermissionError('STRATEGY_EXPIRED')
        if receipt['source'].get('origin') == 'CAMPAIGN_V1':
            validate_campaign_source(receipt['source'], self.plans, self.objective_id)
        if receipt['source'].get('origin')=='BOUNDED_RESEARCH_SCOPE':
            from .bounded_research_v1 import BoundedResearchSessionV1
            source=receipt['source'];session=BoundedResearchSessionV1(source['scope_root'])
            if self.root!=session.path(source['candidate_id'],'governance'):
                raise PermissionError('STRATEGY_SCOPE_ROOT_CONFLICT')
            scope=session.authorize_plan(source['candidate_id'],self.plans)
            if scope['scope_id']!=source['scope_id']:
                raise PermissionError('STRATEGY_SCOPE_IDENTITY_CONFLICT')
        return receipt


def validate_campaign_source(source, plans, objective_id):
    """也供离线证据门核对父授权；历史核验不要求父授权仍未到期。"""
    from .research_campaign_v1 import ResearchCampaignV1
    fields = {'origin', 'campaign_root', 'authorization_id', 'operation_ids', 'expires_at'}
    continuous = 'research_binding_ref' in source
    if continuous:
        fields |= {'research_binding_ref', 'phase', 'batch_id'}
    if set(source) != fields or source['origin'] != 'CAMPAIGN_V1':
        raise PermissionError('CAMPAIGN_ACCOUNT_SOURCE_INVALID')
    campaign = ResearchCampaignV1(source['campaign_root'], source['authorization_id'])
    current = campaign.status()
    authorization = current['authorization']
    phase = 'EXPLORATION'
    expiry = current['base_authorization']['expires_at']
    if continuous:
        from .campaign_scope_v1 import CampaignScopeV1
        binding = CampaignScopeV1(campaign).resolve(source['research_binding_ref'], for_dispatch=False)
        phase, expiry = binding['phase'], binding['expires_at']
        if source['phase'] != phase or source['batch_id'] != binding['batch_id']:
            raise PermissionError('CAMPAIGN_ACCOUNT_PHASE_CONFLICT')
    mapping = source['operation_ids']
    if (authorization['objective_id'] != objective_id or source['expires_at'] != expiry
            or not isinstance(mapping, dict) or set(mapping) != set(plans) or len(set(mapping.values())) != len(mapping)):
        raise PermissionError('CAMPAIGN_ACCOUNT_AUTHORITY_CONFLICT')
    for name, operation_id in mapping.items():
        operation = current['operations'].get(operation_id, {})
        if (operation.get('kind') != 'ACCOUNT' or operation.get('stage') != phase
                or (continuous and operation.get('batch_id') != binding['batch_id'])
                or operation.get('subject_identity') != plans[name]['plan_id']
                or operation.get('status') not in {'RESERVED', 'RUNNING', 'UNKNOWN', 'COMPLETED', 'FAILED'}):
            raise PermissionError('CAMPAIGN_ACCOUNT_OPERATION_CONFLICT')
        profile=plans[name].get('runtime',{}).get('execution_profile')
        if profile is not None and profile != operation.get('execution_profile'):
            raise PermissionError('CAMPAIGN_ACCOUNT_PROFILE_CONFLICT')
    return campaign
