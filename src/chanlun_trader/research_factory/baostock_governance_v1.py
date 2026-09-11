"""同Objective的用途增量；继承原锁、父授权、到期、计费和修复证明。"""
from .baostock_account_v1 import CONTRACT
from .degraded_governance_v1 import DegradedGovernanceV1


class BaostockGovernanceV1(DegradedGovernanceV1):
    contract = CONTRACT
    novelty_status = 'PASSED'
    schema_version = 'baostock-account-confirmation-v1'
    authorization_origin = 'USER_HFQ_SIGNAL_RAW_ACCOUNT_EXPLORATION'
    main_purpose = 'BAOSTOCK_ACCOUNT_MAIN'
    repair_purpose = 'BAOSTOCK_ACCOUNT_CONFIRMED_REPAIR'

    def __init__(self, root):
        super().__init__(root)
        self.receipt_path = self.root/'governance/baostock_account_v1/confirmation.json'
        self.journal = self.receipt_path.parent/'exposure_events.jsonl'

    def confirm(self, plan, source, *, preflight):
        evidence = preflight()
        if any(evidence.get(k) is not True for k in
               ['raw_hfq_pairing_verified', 'historical_universe_complete', 'source_identity_verified']):
            raise PermissionError('BAOSTOCK_INPUT_PREFLIGHT_INCOMPLETE')
        decision = evidence.get('novelty_decision', {})
        if (decision.get('schema_version') != 'candidate-novelty-decision-v2' or
                decision.get('candidate_id') not in plan['contracts'] or
                decision.get('allowed') is not True or not decision.get('comparison_set_hash')):
            raise PermissionError('CANONICAL_NOVELTY_IDENTITY_REQUIRED')
        return super().confirm(plan, source, preflight=lambda: evidence)
