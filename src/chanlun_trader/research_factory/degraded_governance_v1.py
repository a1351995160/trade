"""降级用途独立回执；增量仍进入原权威SearchBudgetRegistry。"""
from datetime import datetime, timezone

from .train_execution_governance_v1 import TrainExecutionGovernanceV1
from .exploration_governance import immutable, read_json
from .budget import SearchBudgetRegistryV1
from .common import stable_hash, now_timestamp
from .degraded_execution_v2 import CONTRACT


class DegradedGovernanceV1(TrainExecutionGovernanceV1):
    contract = CONTRACT
    novelty_status = 'AUTHORIZED_FIXED_REFERENCE_REPRODUCTION'
    schema_version = 'degraded-train-confirmation-v1'
    authorization_origin = 'USER_PURPOSE_LIMITED_DEGRADED_MAIN_AND_CONFIRMED_REPAIR'
    main_purpose = 'DEGRADED_TRAIN_ACCOUNT_MAIN'
    repair_purpose = 'DEGRADED_TRAIN_ACCOUNT_REPAIR'
    def __init__(self, root):
        super().__init__(root)
        self.receipt_path=self.root/'governance/degraded_train_v1/confirmation.json'
        self.journal=self.root/'governance/degraded_train_v1/exposure_events.jsonl'

    def confirm(self, plan, source, *, preflight):
        evidence=preflight()
        if evidence.get('status')!='READY' or evidence.get('input_identity')!=plan['input_identity'] or evidence.get('feasibility_passed') is not True:
            raise PermissionError('DEGRADED_INPUT_OR_FEASIBILITY_NOT_READY')
        if source.get('origin')!='USER_EXPLICIT_PLAN_APPROVAL_VIA_CODEX' or not all(source.get(k) for k in ['thread_id','approval_record_sha256','approval_statement']):
            raise PermissionError('EXPLICIT_MESSAGE_APPROVAL_SOURCE_REQUIRED')
        if (len(plan['contracts'])!=1 or plan['limit']!=2 or plan['wall_limit']!=1800
                or plan['result_type']!=self.contract['result_type'] or list(plan['contracts'].values())!=[self.contract]):
            raise ValueError('DEGRADED_FIXED_SCOPE_MISMATCH')
        expiry=datetime.fromisoformat(plan['expires_at'])
        if not datetime.now(timezone.utc)<expiry<=datetime.fromisoformat(self.contract['expires_at']):
            raise PermissionError('DEGRADED_APPROVAL_EXPIRED_OR_EXTENDED')
        parent=read_json(self.root/'governance/confirmation.json')
        if parent['receipt_id']!=stable_hash({k:v for k,v in parent.items() if k!='receipt_id'}):
            raise PermissionError('PARENT_RECEIPT_CORRUPT')
        if parent['plan']['objective_id']!=plan['objective_id'] or expiry>datetime.fromisoformat(parent['plan']['expires_at']):
            raise PermissionError('PARENT_IDENTITY_OR_EXPIRY_CONFLICT')
        if evidence.get('novelty_status')!=self.novelty_status:
            raise PermissionError('FIXED_REFERENCE_IDENTITY_NOT_VERIFIED')
        with self.lock():
            if (self.root/'governance/revocation.json').exists() or (self.receipt_path.parent/'revocation.json').exists():
                raise PermissionError('APPROVAL_REVOKED')
            receipt={'schema_version':self.schema_version,'plan':plan,'source':source,
                'plan_id':stable_hash(plan),'parent_receipt_id':parent['receipt_id'],
                'canonical_increment_budget':str(self.budget_path),'preflight':evidence,
                'authorization_origin':self.authorization_origin,
                'main_purpose':self.main_purpose,'repair_purpose':self.repair_purpose}
            if self.receipt_path.exists():
                old=read_json(self.receipt_path)
                if old['receipt_id']!=stable_hash({k:v for k,v in old.items() if k!='receipt_id'}) or any(old.get(k)!=v for k,v in receipt.items()):
                    raise PermissionError('DEGRADED_RECEIPT_CONFLICT')
                receipt=old
            else:
                receipt['recorded_at']=now_timestamp();receipt['receipt_id']=stable_hash(receipt)
                immutable(self.receipt_path,receipt)
            # 原组件以新plan_id隔离1主+1修复；不改旧桶额度、消费或其他Objective。
            registry=SearchBudgetRegistryV1(plan['objective_id'],self.budget_path)
            registry.register_train_execution_increment(receipt['plan_id'],next(iter(plan['contracts'])))
            return receipt
