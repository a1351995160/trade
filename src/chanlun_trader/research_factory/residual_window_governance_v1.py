"""本次明确窗口核验委托，经原权威组件登记专用增量，旧桶不变。"""
from .degraded_governance_v1 import DegradedGovernanceV1
from .residual_window_v1 import contract,NAME,TURNOVER_VERSIONS,SCALE_VERSIONS,TREND_RISK_VERSIONS,STOCK_TREND_ONLY


class ResidualWindowGovernanceV1(DegradedGovernanceV1):
    contract=contract()
    schema_version='residual-fixed-window-confirmation-v1'
    authorization_origin='USER_EXPLICIT_FIXED_CANDIDATE_EXTERNAL_CHECK'
    main_purpose='RESIDUAL_FIXED_EXTERNAL_WINDOW_MAIN'
    repair_purpose='RESIDUAL_FIXED_EXTERNAL_WINDOW_CONFIRMED_REPAIR'

    def __init__(self,root,name=NAME):
        self.contract=contract(name)
        if name!=NAME:
            self.schema_version='response-confirmation-window-v1-'+name
            self.authorization_origin='USER_DELEGATED_FIXED_TRAIN_WINNER_EXTERNAL_CHECK'
            self.main_purpose='RESPONSE_CONFIRMATION_WINDOW_MAIN_'+name
            self.repair_purpose='RESPONSE_CONFIRMATION_WINDOW_REPAIR_'+name
        if name in TURNOVER_VERSIONS:
            self.schema_version='turnover-fixed-window-v1'
            self.main_purpose='TURNOVER_FIXED_WINDOW_MAIN'
            self.repair_purpose='TURNOVER_FIXED_WINDOW_CONFIRMED_REPAIR'
        if name in SCALE_VERSIONS:
            self.schema_version='scale-fixed-window-v1'
            self.main_purpose='SCALE_FIXED_WINDOW_MAIN'
            self.repair_purpose='SCALE_FIXED_WINDOW_CONFIRMED_REPAIR'
        if name in TREND_RISK_VERSIONS:
            self.schema_version='trend-risk-fixed-window-v1'
            self.main_purpose='TREND_RISK_FIXED_WINDOW_MAIN'
            self.repair_purpose='TREND_RISK_FIXED_WINDOW_CONFIRMED_REPAIR'
        if name==STOCK_TREND_ONLY:
            self.schema_version='stock-trend-only-fixed-window-v1'
            self.main_purpose='STOCK_TREND_ONLY_FIXED_WINDOW_MAIN'
            self.repair_purpose='STOCK_TREND_ONLY_FIXED_WINDOW_CONFIRMED_REPAIR'
        super().__init__(root)
        self.receipt_path=(self.root/'governance/residual_fixed_window_v1/confirmation.json' if name==NAME else
                           self.root/'governance/response_confirmation_window_v1'/name/'confirmation.json')
        if name in TURNOVER_VERSIONS:self.receipt_path=self.root/'governance/turnover_fixed_window_v1/confirmation.json'
        if name in SCALE_VERSIONS:self.receipt_path=self.root/'governance/scale_fixed_window_v1/confirmation.json'
        if name in TREND_RISK_VERSIONS:self.receipt_path=self.root/'governance/trend_risk_fixed_window_v1/confirmation.json'
        if name==STOCK_TREND_ONLY:self.receipt_path=self.root/'governance/stock_trend_only_fixed_window_v1/confirmation.json'
        self.journal=self.receipt_path.parent/'exposure_events.jsonl'

    def confirm(self,plan,source,*,preflight):
        if not source.get('window_release_sha256') or not source.get('approved_plan_sha256'):
            raise PermissionError('EXPLICIT_RESIDUAL_WINDOW_APPROVAL_REQUIRED')
        evidence=preflight()
        if evidence.get('source_candidate_hash')!=self.contract['source_candidate_contract_hash']:
            raise PermissionError('FIXED_CANDIDATE_LINEAGE_CONFLICT')
        return super().confirm(plan,source,preflight=lambda:evidence)
