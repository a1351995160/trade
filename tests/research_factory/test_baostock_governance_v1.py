import pytest

from test_windowed_actions_and_train_grant import grant
from chanlun_trader.research_factory.baostock_account_v1 import CONTRACT
from chanlun_trader.research_factory.baostock_governance_v1 import BaostockGovernanceV1


def test_increment_uses_canonical_budget_and_old_receipt_cannot_be_reused(tmp_path):
    _,plan,source,evidence,parent=grant(tmp_path)
    service=BaostockGovernanceV1(tmp_path)
    plan['contracts']={'hfq':CONTRACT};plan['result_type']=CONTRACT['result_type']
    source['approval_record_sha256']=source.pop('attachment_sha256')
    before=service.budget_path.read_bytes()
    with pytest.raises(PermissionError,match='PREFLIGHT'):
        service.confirm(plan,source,preflight=lambda:evidence)
    assert service.budget_path.read_bytes()==before
    evidence.update(raw_hfq_pairing_verified=True,historical_universe_complete=True,
                    source_identity_verified=True,feasibility_passed=True)
    with pytest.raises(PermissionError,match='IDENTITY'):
        service.confirm(plan,source,preflight=lambda:evidence)
    evidence['novelty_status']='PASSED'
    from chanlun_trader.research_factory.novelty import CandidateNoveltyGateV2
    evidence['novelty_decision']=CandidateNoveltyGateV2().evaluate({'candidate_id':'hfq','factor_ids':[CONTRACT['factor_id']]}).to_dict()
    receipt=service.confirm(plan,source,preflight=lambda:evidence)
    assert service.budget_path==parent.budget_path
    assert receipt['schema_version']=='baostock-account-confirmation-v1'
    assert receipt['main_purpose']=='BAOSTOCK_ACCOUNT_MAIN'
    item=service.reserve('hfq');service.start_exposure(item['execution_id']);service.settle(item['execution_id'],1,True)
    assert service.summary()['MAIN_BACKTEST_EXPOSURES_USED']==1
    assert service.reserve('hfq')['status']=='ALREADY_ATTEMPTED'
    service.revoke('SYNTHETIC_USER_STOP')
    with pytest.raises(PermissionError):service.active()
