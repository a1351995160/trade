"""外窗固定候选仍通过原权威增量、预留、曝光和结算组件。"""
from .degraded_governance_v1 import DegradedGovernanceV1
from .monthly_window_v1 import contract


class MonthlyWindowGovernanceV1(DegradedGovernanceV1):
    contract=contract()
    schema_version='monthly-fixed-window-confirmation-v1'
    authorization_origin='USER_FIXED_WINDOW_RELEASE_AND_EXPLICIT_TEN_DATE_GATE'
    main_purpose='MONTHLY_INDEPENDENT_WINDOW_MAIN'
    repair_purpose='MONTHLY_INDEPENDENT_WINDOW_CONFIRMED_ENGINEERING_REPAIR'

    def __init__(self,root):
        super().__init__(root)
        self.receipt_path=self.root/'governance/monthly_independent_window_v1/confirmation.json'
        self.journal=self.receipt_path.parent/'exposure_events.jsonl'

    def confirm(self,plan,source,*,preflight):
        if not source.get('window_release_sha256') or not source.get('window_gate_approval_sha256'):
            raise PermissionError('EXPLICIT_WINDOW_AND_GATE_APPROVAL_REQUIRED')
        evidence=preflight()
        if evidence.get('source_candidate_hash')!=self.contract['source_candidate_contract_hash']:
            raise PermissionError('FIXED_CANDIDATE_LINEAGE_CONFLICT')
        return super().confirm(plan,source,preflight=lambda:evidence)
