"""新增额度仍由原权威组件登记；原策略及历史账目不变。"""
from .degraded_governance_v1 import DegradedGovernanceV1
from .weekly_window_v1 import contract


class WeeklyWindowGovernanceV1(DegradedGovernanceV1):
    contract=contract()
    schema_version='weekly-fixed-window-confirmation-v1'
    authorization_origin='USER_EXPLICIT_WEEKLY_FIXED_WINDOW_APPROVAL'
    main_purpose='WEEKLY_FIXED_EXTERNAL_WINDOW_MAIN'
    repair_purpose='WEEKLY_FIXED_EXTERNAL_WINDOW_CONFIRMED_ENGINEERING_REPAIR'

    def __init__(self,root):
        super().__init__(root)
        self.receipt_path=self.root/'governance/weekly_fixed_window_v1/confirmation.json'
        self.journal=self.receipt_path.parent/'exposure_events.jsonl'

    def confirm(self,plan,source,*,preflight):
        if not source.get('window_release_sha256') or not source.get('approved_plan_sha256'):
            raise PermissionError('EXPLICIT_WEEKLY_WINDOW_APPROVAL_REQUIRED')
        evidence=preflight()
        if evidence.get('source_candidate_hash')!=self.contract['source_candidate_contract_hash']:
            raise PermissionError('FIXED_CANDIDATE_LINEAGE_CONFLICT')
        return super().confirm(plan,source,preflight=lambda:evidence)
