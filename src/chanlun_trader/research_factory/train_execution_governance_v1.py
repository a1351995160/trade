"""同一权威预算中的1主+1修复增量；复用原锁、消费、结算、撤销及恢复规则。"""
from datetime import datetime,timezone

from .exploration_governance import ExplorationGovernanceServiceV1, immutable, read_json
from .budget import SearchBudgetRegistryV1
from .common import stable_hash, now_timestamp


class TrainExecutionGovernanceV1(ExplorationGovernanceServiceV1):
    def __init__(self,original_exploration_root):
        super().__init__(original_exploration_root)
        # 预算仍是同一SearchBudgetRegistry，不复制账本；新回执/事件独立版本。
        self.receipt_path=self.root/'governance/train_execution_v1/confirmation.json'
        self.journal=self.root/'governance/train_execution_v1/exposure_events.jsonl'

    def lock(self):
        from .mutation_boundary import ObjectiveMutationLock
        return ObjectiveMutationLock.for_resource(self.budget_path)

    def confirm(self,plan,source,*,preflight):
        from .train_account_runner_v1 import FIXED_CONTRACT
        evidence=preflight()
        if evidence.get('status')!='READY' or evidence.get('input_identity')!=plan['input_identity']:
            raise PermissionError('TRAIN_EXECUTION_INPUT_NOT_READY')
        if source.get('origin')!='USER_EXPLICIT_PLAN_APPROVAL_VIA_CODEX' or not all(source.get(k) for k in ['thread_id','attachment_sha256','approval_statement']):
            raise PermissionError('EXPLICIT_APPROVAL_SOURCE_REQUIRED')
        if len(plan['contracts'])!=1 or plan['limit']!=2 or plan['wall_limit']!=1800 or plan['result_type']!='TRAIN_EXECUTION_BACKTEST_EXPLORATORY':
            raise ValueError('TRAIN_EXECUTION_SCOPE_MISMATCH')
        if next(iter(plan['contracts'].values()))!=FIXED_CONTRACT:raise ValueError('FIXED_REFERENCE_PARAMETERS_CHANGED')
        expiry=datetime.fromisoformat(plan['expires_at'])
        if not datetime.now(timezone.utc)<expiry<=datetime.fromisoformat('2026-09-14T10:05:03+08:00'):
            raise PermissionError('TRAIN_EXECUTION_EXPIRED_OR_EXTENDED')
        parent=read_json(self.root/'governance/confirmation.json')
        if parent['receipt_id']!=stable_hash({k:v for k,v in parent.items() if k!='receipt_id'}):
            raise PermissionError('PARENT_RECEIPT_CORRUPT')
        if parent['plan']['objective_id']!=plan['objective_id'] or expiry>datetime.fromisoformat(parent['plan']['expires_at']):
            raise PermissionError('PARENT_OBJECTIVE_OR_DEADLINE_CONFLICT')
        if evidence.get('novelty_status')!='AUTHORIZED_FIXED_REFERENCE_REPRODUCTION':
            raise PermissionError('FIXED_REFERENCE_NOVELTY_PURPOSE_NOT_VERIFIED')
        with self.lock():
            if (self.root/'governance/revocation.json').exists():raise PermissionError('PARENT_REVOKED')
            receipt={'schema_version':'train-execution-confirmation-v1','plan':plan,'source':source,
                'plan_id':stable_hash(plan),'parent_receipt_id':parent['receipt_id'],
                'canonical_increment_budget':str(self.budget_path),'preflight':evidence,
                'authorization_origin':'USER_FIXED_REFERENCE_ACCOUNT_BACKTEST_NOT_ALPHA_SEARCH'}
            if self.receipt_path.exists():
                previous=read_json(self.receipt_path)
                if previous['receipt_id']!=stable_hash({k:v for k,v in previous.items() if k!='receipt_id'}):
                    raise PermissionError('TRAIN_RECEIPT_CORRUPT')
                if any(previous.get(k)!=v for k,v in receipt.items()):raise ValueError('TRAIN_RECEIPT_CONFLICT')
                receipt=previous
            else:
                receipt['recorded_at']=now_timestamp();receipt['receipt_id']=stable_hash(receipt)
                immutable(self.receipt_path,receipt)
            budget=SearchBudgetRegistryV1(plan['objective_id'],self.budget_path)
            budget.register_train_execution_increment(receipt['plan_id'],next(iter(plan['contracts'])))
            return receipt

    def _reserve_budget(self,budget,plan_id,contract_id,repair_id):
        return budget.reserve_train_execution(plan_id,contract_id,repair_id=repair_id)

    def active(self):
        receipt=super().active()
        if (self.receipt_path.parent/'revocation.json').exists():raise PermissionError('TRAIN_EXECUTION_REVOKED')
        return receipt

    def reserve(self,contract_id,repair=None):
        if repair:
            from .evidence_paths import within_root
            red=read_json(within_root(repair['red_evidence'],self.root.parent))
            green=read_json(within_root(repair['green_evidence'],self.root.parent))
            if (red.get('status')!='FAIL' or green.get('status')!='PASS' or not red.get('case_id')
                    or red['case_id']!=green.get('case_id') or red.get('affected_contract')!=contract_id
                    or green.get('fix_commit')!=repair['fix_commit']):
                raise PermissionError('MATCHED_RED_GREEN_REPAIR_EVIDENCE_REQUIRED')
        return super().reserve(contract_id,repair)

    def revoke(self,reason):
        with self.lock():immutable(self.receipt_path.parent/'revocation.json',{'reason':reason})

    def summary(self):
        receipt=read_json(self.receipt_path)
        budget=SearchBudgetRegistryV1(receipt['plan']['objective_id'],self.budget_path)
        return {'MAIN_BACKTEST_EXPOSURES_USED':budget.used('train_execution_main',receipt['plan_id']),
            'REPAIR_BACKTEST_EXPOSURES_USED':budget.used('train_execution_repair',receipt['plan_id']),
            'events':self.events(),'receipt_id':receipt['receipt_id']}
