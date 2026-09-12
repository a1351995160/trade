"""明确用户决定的受限探索治理适配；不调用或放宽正式预测授权。"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from .budget import SearchBudgetRegistryV1
from .common import stable_hash, now_timestamp
from .mutation_boundary import ObjectiveMutationLock


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def immutable(path, payload):
    path = Path(path)
    if path.exists():
        if read_json(path) != payload:
            raise ValueError('EXPLORATION_IMMUTABLE_CONFLICT')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:
        json.dump(payload,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


class ExplorationGovernanceServiceV1:
    def __init__(self, root):
        self.root=Path(root).absolute()
        if self.root.resolve() != self.root:
            raise ValueError('EXPLORATION_ROOT_REDIRECTED')
        self.root.mkdir(parents=True,exist_ok=True)
        self.receipt_path=self.root/'governance/confirmation.json'
        self.budget_path=self.root/'governance/search_budget_registry.json'
        self.journal=self.root/'governance/exposure_events.jsonl'

    def lock(self):
        return ObjectiveMutationLock.for_resource(self.receipt_path)

    def events(self):
        if not self.journal.exists(): return []
        return [json.loads(line) for line in self.journal.read_text(encoding='utf-8').splitlines() if line]

    def append(self, event):
        event={**event,'recorded_at':now_timestamp()}
        self.journal.parent.mkdir(parents=True,exist_ok=True)
        with self.journal.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n')
            stream.flush(); os.fsync(stream.fileno())

    def confirm(self, plan, source):
        if source.get('origin')!='USER_EXPLICIT_PLAN_APPROVAL_VIA_CODEX' or not source.get('thread_id') or not source.get('attachment_sha256') or not source.get('approval_statement'):
            raise PermissionError('EXPLICIT_APPROVAL_SOURCE_REQUIRED')
        if len(plan['contracts'])!=4 or plan['limit']!=6 or plan['wall_limit']!=5400 or plan['result_type']!='EXPLORATORY_RAW_PRICE_RELATION':
            raise ValueError('EXPLORATION_SCOPE_MISMATCH')
        if datetime.now(timezone.utc)>=datetime.fromisoformat(plan['expires_at']):
            raise PermissionError('EXPLORATION_EXPIRED')
        with self.lock():
            receipt={'schema_version':'exploration-confirmation-v1','plan':plan,'source':source,
                     'plan_id':stable_hash(plan),'canonical_increment_budget':str(self.budget_path),
                     'authorization_origin':'USER_PLAN_LEVEL_NOT_PER_CANDIDATE',
                     'old_budget_mutation':False,'old_history_reset':False}
            if self.receipt_path.exists():
                old=read_json(self.receipt_path)
                if old['plan']!=plan or old['source']!=source:
                    raise ValueError('EXPLORATION_CONFIRMATION_CONFLICT')
                receipt=old
            else:
                receipt['recorded_at']=now_timestamp()
                receipt['receipt_id']=stable_hash(receipt)
                immutable(self.receipt_path,receipt)
            budget=SearchBudgetRegistryV1(plan['objective_id'],self.budget_path)
            budget.register_exploration_increment(receipt['plan_id'],list(plan['contracts']))
            return receipt

    def active(self):
        receipt=read_json(self.receipt_path)
        if receipt['receipt_id']!=stable_hash({k:v for k,v in receipt.items() if k!='receipt_id'}):
            raise PermissionError('EXPLORATION_RECEIPT_CORRUPT')
        if (self.root/'governance/revocation.json').exists():
            raise PermissionError('EXPLORATION_REVOKED')
        if datetime.now(timezone.utc)>=datetime.fromisoformat(receipt['plan']['expires_at']):
            raise PermissionError('EXPLORATION_EXPIRED')
        return receipt

    def revoke(self, reason):
        with self.lock():
            immutable(self.root/'governance/revocation.json',{'reason':reason})

    def reserve(self, contract_id, repair=None):
        with self.lock():
            receipt=self.active(); plan=receipt['plan']
            if contract_id not in plan['contracts']: raise PermissionError('UNAPPROVED_EXPLORATION_CONTRACT')
            events=self.events()
            if repair:
                if set(repair)!={'fix_commit','red_evidence','green_evidence','affected_contract'} or repair['affected_contract']!=contract_id:
                    raise PermissionError('CONFIRMED_REPAIR_EVIDENCE_REQUIRED')
                if not all(Path(repair[k]).is_file() for k in ('red_evidence','green_evidence')) or len(repair['fix_commit'])!=40:
                    raise PermissionError('CONFIRMED_REPAIR_EVIDENCE_REQUIRED')
                if not any(e.get('contract_id')==contract_id and e['event']=='EXPOSURE_STARTED' for e in events):
                    raise PermissionError('NO_PRIOR_EXPOSURE_TO_RECOMPUTE')
            repair_id=stable_hash(repair) if repair else None
            execution_id=stable_hash([receipt['plan_id'],contract_id,repair_id])
            matching=[e for e in events if e.get('execution_id')==execution_id]
            if matching:
                return {'execution_id':execution_id,'status':'ALREADY_ATTEMPTED','events':matching}
            started={e['execution_id'] for e in events if e['event']=='RESERVED'}
            terminal={e['execution_id'] for e in events if e['event']=='SETTLED'}
            if started-terminal: raise PermissionError('UNSETTLED_EXPLORATION_REQUIRES_RECONCILIATION')
            used=sum(e.get('wall_seconds',0) for e in events if e['event']=='SETTLED')
            if used>=plan['wall_limit']: raise PermissionError('EXPLORATION_TIME_EXHAUSTED')
            budget=SearchBudgetRegistryV1(plan['objective_id'],self.budget_path)
            if budget.snapshot()['active_reservations']:
                raise PermissionError('ORPHAN_RESERVATION_REQUIRES_RECONCILIATION')
            reservation=self._reserve_budget(budget,receipt['plan_id'],contract_id,repair_id)
            self.append({'event':'RESERVED','execution_id':execution_id,'contract_id':contract_id,
                         'reservation_id':reservation,'repair':repair})
            return {'status':'RESERVED','execution_id':execution_id,'reservation_id':reservation,
                    'wall_seconds':min(900,plan['wall_limit']-used,
                        (datetime.fromisoformat(plan['expires_at'])-datetime.now(timezone.utc)).total_seconds())}

    def _reserve_budget(self,budget,plan_id,contract_id,repair_id):
        return budget.reserve_exploration(plan_id,contract_id,repair_id=repair_id)

    def start_exposure(self, execution_id):
        with self.lock():
            receipt=self.active()
            events=[e for e in self.events() if e.get('execution_id')==execution_id]
            if len(events)!=1 or events[0]['event']!='RESERVED':
                raise PermissionError('EXPLORATION_START_STATE_INVALID')
            budget=SearchBudgetRegistryV1(receipt['plan']['objective_id'],self.budget_path)
            # 先持久记账再进行价格表现计算；崩溃后保守保留消费，不自动免费重跑。
            budget.consume(events[0]['reservation_id'])
            self.append({'event':'EXPOSURE_STARTED','execution_id':execution_id,'contract_id':events[0]['contract_id']})

    def settle(self, execution_id, wall_seconds, completed):
        with self.lock():
            receipt=read_json(self.receipt_path)
            events=[e for e in self.events() if e.get('execution_id')==execution_id]
            if any(e['event']=='SETTLED' for e in events): return
            if not events: raise ValueError('UNKNOWN_EXPLORATION_EXECUTION')
            exposed=any(e['event']=='EXPOSURE_STARTED' for e in events)
            budget=SearchBudgetRegistryV1(receipt['plan']['objective_id'],self.budget_path)
            reservation=events[0]['reservation_id']
            consumed=budget.snapshot()['settled_reservations'].get(reservation)=='CONSUMED'
            if not exposed and not consumed: budget.release(reservation)
            self.append({'event':'SETTLED','execution_id':execution_id,'contract_id':events[0]['contract_id'],
                         'wall_seconds':wall_seconds,'completed':completed,
                         'exposure_status':'CONFIRMED' if exposed and completed else 'POSSIBLE_CHARGED' if consumed else 'NOT_STARTED'})

    def summary(self):
        events=self.events(); receipt=read_json(self.receipt_path)
        settled=[e for e in events if e['event']=='SETTLED']
        budget=SearchBudgetRegistryV1(receipt['plan']['objective_id'],self.budget_path)
        return {'plan_id':receipt['plan_id'],'receipt_id':receipt['receipt_id'],
                'protocols_planned':4,'protocols_executed':sum(e['completed'] for e in settled),
                'real_price_exposures_confirmed':sum(e['exposure_status']=='CONFIRMED' for e in settled),
                'possible_charged_exposures':sum(e['exposure_status']=='POSSIBLE_CHARGED' for e in settled),
                'exposures_charged':budget.used('exploration_increment',receipt['plan_id']),
                'repair_exposures_used':budget.used('exploration_repair',receipt['plan_id']),
                'compute_wall_seconds':sum(e['wall_seconds'] for e in settled),
                'research_qualification_trials':0,'result_type':'EXPLORATORY_RAW_PRICE_RELATION',
                'formal_plan_status':'BLOCKED','v4_status':'CALIBRATION_LIMITATION_NOT_APPROVED',
                'AUTONOMOUS_STRATEGY_GOAL_COMPLETED':False,'READY_FOR_REAL_TRIAL':False,'R1_FULLY_CLOSED':False}
