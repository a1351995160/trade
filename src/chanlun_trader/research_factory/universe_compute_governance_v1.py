"""准备、独立核验和报告的同授权有界计算用途，不借父进程免费执行。"""
from datetime import datetime, timezone
import math
from pathlib import Path

from .budget import SearchBudgetRegistryV1
from .common import stable_hash
from .exploration_governance import immutable, read_json
from .mutation_boundary import ObjectiveMutationLock
from .universe_execution_profile_v1 import (
    SEGMENTED_PROFILE, execution_profile, segment_allowance, segment_charge,
)


STAGES={'PREPARATION':('RESEARCH_PREPARATION','preparation_jobs','DATA'),
        'VERIFICATION':('RESEARCH_VERIFICATION','verification_jobs','VERIFY'),
        'REPORT':('RESEARCH_REPORT','report_jobs','VERIFY')}


def authorized_compute_profile(authority, request, stage):
    if stage not in STAGES:
        raise ValueError('UNIVERSE_COMPUTE_STAGE_INVALID')
    expected=execution_profile(SEGMENTED_PROFILE,request['execution_profile']['account_sessions'],STAGES[stage][0])
    limits=authority.get('compute_authorization')
    if (not isinstance(limits,dict) or set(limits) != {item[1] for item in STAGES.values()}
            or any(type(value) is not int or value < 1 for value in limits.values())
            or expected not in authority.get('execution_profiles',[])):
        raise PermissionError('UNIVERSE_COMPUTE_PROFILE_AND_BUDGET_AUTHORIZATION_REQUIRED')
    return expected


class UniverseComputeGovernanceV1:
    def __init__(self,root,authority,request,stage,*,campaign_operation=None):
        self.root=Path(root).absolute(); self.authority=authority; self.request=request; self.stage=stage
        if self.root.resolve() != self.root:
            raise PermissionError('UNIVERSE_COMPUTE_ROOT_REDIRECTED')
        self.profile=authorized_compute_profile(authority,request,stage)
        self.budget_path=Path(authority['budget_path']).absolute()
        self.budget_kind='universe_compute_'+stage.lower()+'_v1'
        # 登记别名只是请求引用；同一冻结授权实体不能通过改名再取得额度。
        self.budget_key=stable_hash(authority)
        if campaign_operation is None:
            campaign_ref=authority.get('account_authorization',{}).get('campaign_ref')
            if campaign_ref is not None:
                if not isinstance(campaign_ref,dict) or set(campaign_ref) != {'root','authorization_id'}:
                    raise PermissionError('UNIVERSE_COMPUTE_CAMPAIGN_REFERENCE_INVALID')
                campaign_operation={**campaign_ref,'operation_id':'compute_'+stage.lower()+'_'+stable_hash(request)[:48]}
        self.campaign_operation=campaign_operation
        source=authority.get('source',{})
        if source.get('origin') != 'USER_EXPLICIT_CURRENT_TASK' or not source.get('statement'):
            raise PermissionError('UNIVERSE_COMPUTE_APPROVAL_SOURCE_REQUIRED')
        self.binding={'stage':stage,'profile':self.profile,'authorization_identity':stable_hash(authority),
            'request_identity':stable_hash(request),'objective_id':authority['objective_id'],
            'budget_path':str(self.budget_path),'budget_key':self.budget_key,
            'expires_at':authority['expires_at'],'root':str(self.root),
            'campaign_operation':campaign_operation}
        self.binding['compute_identity']=stable_hash(self.binding)

    def lock(self):
        return ObjectiveMutationLock.for_resource(self.budget_path)

    def active(self):
        if datetime.now(timezone.utc)>=datetime.fromisoformat(self.binding['expires_at']):
            raise PermissionError('UNIVERSE_COMPUTE_AUTHORIZATION_EXPIRED')
        if (self.root/'REVOKED.json').exists():
            raise PermissionError('UNIVERSE_COMPUTE_REVOKED')
        if self.campaign_operation:
            from .research_campaign_v1 import ResearchCampaignV1
            ref=self.campaign_operation
            campaign=ResearchCampaignV1(ref['root'],ref['authorization_id'])
            view=campaign.status()
            operation=view['operations'].get(ref['operation_id'])
            campaign._dispatchable(view,operation['stage'] if operation else 'EXPLORATION')
        return self.binding

    def start(self):
        with self.lock():
            self.active(); path=self.root/'COMPUTE_START.json'
            if path.exists():
                if read_json(path)['binding'] != self.binding:
                    raise PermissionError('UNIVERSE_COMPUTE_START_IDENTITY_CONFLICT')
                return read_json(path)
            budget=SearchBudgetRegistryV1(self.authority['objective_id'],self.budget_path)
            if self.campaign_operation:
                from .research_campaign_v1 import ResearchCampaignV1
                ref=self.campaign_operation; campaign=ResearchCampaignV1(ref['root'],ref['authorization_id'])
                resource='data_experiments' if self.stage=='PREPARATION' else 'verification_jobs'
                campaign.reserve_operation(operation_id=ref['operation_id'],batch_id='compute_'+stable_hash(self.request)[:48],
                    stage='EXPLORATION',kind=STAGES[self.stage][2],subject_identity=self.binding['compute_identity'],
                    upper_bounds={resource:1,'wall_seconds':self.profile['total_seconds']},execution_profile=self.profile)
            budget.register(self.budget_kind,self.budget_key,self.authority['compute_authorization'][STAGES[self.stage][1]])
            reservation=budget.reserve(self.budget_kind,self.budget_key)
            record={'binding':self.binding,'reservation':reservation,'started_at':datetime.now(timezone.utc).isoformat()}
            immutable(path,record); budget.consume(reservation)
            if self.campaign_operation:
                from .research_campaign_v1 import ResearchCampaignV1
                ref=self.campaign_operation
                ResearchCampaignV1(ref['root'],ref['authorization_id']).start_operation(ref['operation_id'])
            return record

    def status(self):
        start=read_json(self.root/'COMPUTE_START.json')
        if start['binding'] != self.binding:
            raise PermissionError('UNIVERSE_COMPUTE_START_IDENTITY_CONFLICT')
        budget=SearchBudgetRegistryV1(self.authority['objective_id'],self.budget_path).snapshot()
        if budget['settled_reservations'].get(start['reservation']) != 'CONSUMED':
            raise PermissionError('UNIVERSE_COMPUTE_NOT_CONSUMED')
        charged=0.; rows=[]; pending=None; head=None
        for path in sorted(self.root.glob('COMPUTE_SEGMENT_*_DISPATCH.json')):
            value=read_json(path); number=len(rows)+1
            upper=value.get('upper_bound_seconds')
            if (pending is not None or (rows and rows[-1]['charge']['outcome'] in ('COMPLETED','FAILED'))
                    or path.name!='COMPUTE_SEGMENT_'+str(number).zfill(6)+'_DISPATCH.json'
                    or value.get('number') != number or value.get('previous_head') != head
                    or value.get('compute_identity') != self.binding['compute_identity']
                    or type(upper) not in (int,float) or not math.isfinite(upper)
                    or not 0<upper<=min(self.profile['worker_seconds'],self.profile['total_seconds']-charged)
                    or value.get('dispatch_id') != stable_hash({k:v for k,v in value.items() if k!='dispatch_id'})):
                raise PermissionError('UNIVERSE_COMPUTE_SEGMENT_CHAIN_CONFLICT')
            charge_path=self.root/('COMPUTE_SEGMENT_'+str(number).zfill(6)+'_CHARGE.json')
            row={'dispatch':value,'charge':None}
            if charge_path.exists():
                charge=read_json(charge_path)
                amount,basis=segment_charge(charge['measured_seconds'],value['upper_bound_seconds'],charge['evidence_identity'],outcome=charge.get('outcome'))
                if (charge['dispatch_id']!=value['dispatch_id'] or charge['seconds']!=amount or charge['basis']!=basis
                        or charge['outcome'] not in ('CONTINUE','COMPLETED','FAILED')
                        or charge['charge_id']!=stable_hash({k:v for k,v in charge.items() if k!='charge_id'})):
                    raise PermissionError('UNIVERSE_COMPUTE_CHARGE_CONFLICT')
                charged+=amount; head=charge['charge_id']; row['charge']=charge
            else:
                pending=value
            rows.append(row)
        if any(not (self.root/path.name.replace('_CHARGE.json','_DISPATCH.json')).is_file()
               for path in self.root.glob('COMPUTE_SEGMENT_*_CHARGE.json')):
            raise PermissionError('UNIVERSE_COMPUTE_ORPHAN_CHARGE')
        failed=bool(rows and rows[-1]['charge'] and rows[-1]['charge']['outcome']=='FAILED')
        if charged>self.profile['total_seconds'] and not failed:
            raise PermissionError('UNIVERSE_COMPUTE_CUMULATIVE_BOUND_EXCEEDED')
        return {'charged_seconds':charged,'pending':pending,'segments':rows,'head':head,
                'next_segment_number':len(rows)+1,'profile':self.profile,
                'remaining_seconds':max(0,self.profile['total_seconds']-charged),
                'resource_overrun':max(0,charged-self.profile['total_seconds']),
                'dispatch_overrun':sum(max(0,row['charge']['seconds']-row['dispatch']['upper_bound_seconds'])
                    for row in rows if row['charge'])}

    def dispatch(self):
        with self.lock():
            self.active(); state=self.status()
            if state['pending'] or (state['segments'] and state['segments'][-1]['charge']['outcome'] in ('COMPLETED','FAILED')):
                raise PermissionError('UNIVERSE_COMPUTE_PENDING_OR_TERMINAL')
            upper=segment_allowance(self.profile,state['charged_seconds'],
                seconds_to_expiry=(datetime.fromisoformat(self.binding['expires_at'])-datetime.now(timezone.utc)).total_seconds())
            value={'number':state['next_segment_number'],'compute_identity':self.binding['compute_identity'],
                'upper_bound_seconds':upper,'previous_head':state['head'],'dispatched_at':datetime.now(timezone.utc).isoformat()}
            value['dispatch_id']=stable_hash(value)
            if self.campaign_operation:
                from .research_campaign_v1 import ResearchCampaignV1
                ref=self.campaign_operation
                parent=ResearchCampaignV1(ref['root'],ref['authorization_id']).start_execution_segment(ref['operation_id'],
                    segment_number=value['number'],profile=self.profile,upper_bound_seconds=upper)
                value['dispatched_at']=parent['dispatched_at'];value['dispatch_id']=stable_hash({k:v for k,v in value.items() if k!='dispatch_id'})
            immutable(self.root/('COMPUTE_SEGMENT_'+str(value['number']).zfill(6)+'_DISPATCH.json'),value)
            return value

    def charge(self,number,*,seconds=None,evidence_identity=None,outcome='CONTINUE'):
        if outcome not in ('CONTINUE','COMPLETED','FAILED'):
            raise ValueError('UNIVERSE_COMPUTE_OUTCOME_INVALID')
        with self.lock():
            state=self.status(); pending=state['pending']
            if pending is None or pending['number'] != number:
                raise PermissionError('UNIVERSE_COMPUTE_SEGMENT_NOT_ACTIVE')
            amount,basis=segment_charge(seconds,pending['upper_bound_seconds'],evidence_identity,outcome=outcome)
            value={'dispatch_id':pending['dispatch_id'],'measured_seconds':seconds,'seconds':amount,'basis':basis,
                'evidence_identity':evidence_identity,'outcome':outcome,'charged_at':datetime.now(timezone.utc).isoformat()}
            value['charge_id']=stable_hash(value)
            if self.campaign_operation:
                from .research_campaign_v1 import ResearchCampaignV1
                ref=self.campaign_operation
                campaign=ResearchCampaignV1(ref['root'],ref['authorization_id'])
                parent=campaign.end_execution_segment(ref['operation_id'],segment_number=number,seconds=seconds,
                    evidence_identity=evidence_identity,outcome=outcome)
                value['charged_at']=parent['charged_at'];value['charge_id']=stable_hash({k:v for k,v in value.items() if k!='charge_id'})
                if outcome in ('COMPLETED','FAILED'):
                    resource='data_experiments' if self.stage=='PREPARATION' else 'verification_jobs'
                    campaign.settle_operation(ref['operation_id'],actual={resource:1,'wall_seconds':max(1,math.ceil(state['charged_seconds']+amount))},
                        outcome=outcome,evidence_identity=evidence_identity or value['charge_id'])
            immutable(self.root/('COMPUTE_SEGMENT_'+str(number).zfill(6)+'_CHARGE.json'),value)
            return value

    def reconcile_segment_mirrors(self):
        """父计算事件是唯一收费依据；同身份补镜像不能重放计算。"""
        with self.lock():
            if not self.campaign_operation:return self.status()
            from .research_campaign_v1 import ResearchCampaignV1
            ref=self.campaign_operation;campaign=ResearchCampaignV1(ref['root'],ref['authorization_id'])
            operation=campaign.status()['operations'][ref['operation_id']]
            if (operation['subject_identity']!=self.binding['compute_identity']
                    or operation.get('execution_profile')!=self.profile or operation['kind']!=STAGES[self.stage][2]
                    or operation['stage']!='EXPLORATION'):
                raise PermissionError('UNIVERSE_COMPUTE_CAMPAIGN_MIRROR_CONFLICT')
            start=read_json(self.root/'COMPUTE_START.json')
            if start['binding']!=self.binding:raise PermissionError('UNIVERSE_COMPUTE_START_IDENTITY_CONFLICT')
            charged=0.;head=None
            for number,parent in enumerate(operation.get('segments',[]),1):
                if parent['segment_number']!=number or parent['profile_hash']!=self.profile['profile_hash']:
                    raise PermissionError('UNIVERSE_COMPUTE_CAMPAIGN_MIRROR_CONFLICT')
                dispatch={'number':number,'compute_identity':self.binding['compute_identity'],
                    'upper_bound_seconds':parent['upper_bound_seconds'],'previous_head':head,
                    'dispatched_at':parent['dispatched_at']}
                dispatch['dispatch_id']=stable_hash(dispatch)
                prefix='COMPUTE_SEGMENT_'+str(number).zfill(6)
                dispatch_path=self.root/(prefix+'_DISPATCH.json')
                if not dispatch_path.exists():
                    worker_prefix='PREPARE_' if self.stage=='PREPARATION' else 'SEGMENT_'
                    worker_root=self.root.parent if self.stage=='PREPARATION' else self.root
                    worker=worker_root/(worker_prefix+str(number).zfill(6)+'_WORKER.json')
                    access=worker_root/(worker_prefix+str(number).zfill(6)+'_ACCESS.json')
                    immutable(self.root/(prefix+'_MIRROR_REPAIR.json'),{'version':'CAMPAIGN_SEGMENT_MIRROR_REPAIR_V1',
                        'compute_identity':self.binding['compute_identity'],'operation_id':ref['operation_id'],
                        'dispatch_id':dispatch['dispatch_id'],'worker_absent':not worker.exists() and not access.exists(),
                        'parent_dispatch_identity':stable_hash({key:parent[key] for key in
                            ('operation_id','segment_number','profile_hash','upper_bound_seconds','dispatched_at')})})
                immutable(dispatch_path,dispatch)
                if 'basis' in parent:
                    charge={'dispatch_id':dispatch['dispatch_id'],**{key:parent[key] for key in
                        ('measured_seconds','seconds','basis','evidence_identity','outcome','charged_at')}}
                    charge['charge_id']=stable_hash(charge)
                    immutable(self.root/('COMPUTE_SEGMENT_'+str(number).zfill(6)+'_CHARGE.json'),charge)
                    charged+=charge['seconds'];head=charge['charge_id']
                elif (self.root/('COMPUTE_SEGMENT_'+str(number).zfill(6)+'_CHARGE.json')).exists():
                    raise PermissionError('UNIVERSE_COMPUTE_UNPROVEN_LOCAL_CHARGE')
            state=self.status()
            if len(state['segments'])!=len(operation.get('segments',[])) or state['charged_seconds']!=operation.get('active_wall_seconds',0):
                raise PermissionError('UNIVERSE_COMPUTE_CAMPAIGN_MIRROR_CONFLICT')
            # charge事件已提交、settle尚未提交的同用途关闭，不重新执行worker。
            if (state['segments'] and state['segments'][-1]['charge'] is not None
                    and state['segments'][-1]['charge']['outcome'] in ('COMPLETED','FAILED') and operation['status']=='RUNNING'):
                last=state['segments'][-1]['charge'];resource='data_experiments' if self.stage=='PREPARATION' else 'verification_jobs'
                campaign.settle_operation(ref['operation_id'],actual={resource:1,'wall_seconds':max(1,math.ceil(charged))},
                    outcome=last['outcome'],evidence_identity=last['evidence_identity'] or last['charge_id'])
            return state
