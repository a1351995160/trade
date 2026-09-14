from datetime import datetime, timezone, timedelta
import pytest
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.etf_account_governance_v1 import ETFAccountGovernanceV1


def test_increment_uses_same_registry_preserves_old_spend_and_prevents_replay(tmp_path,monkeypatch):
    path=tmp_path/'budget.json';objective='SYNTHETIC_OBJECTIVE'
    old=SearchBudgetRegistryV1(objective,path);old.register_objective(12)
    old.consume(old.reserve('objective',objective,12))
    service=ETFAccountGovernanceV1(tmp_path/'etf',path,objective)
    source={'statement':'先完成当前ETF账户验证','origin':'USER_EXPLICIT_CURRENT_TASK',
            'spread_model_reply':'允许按上述模型假设回测'}
    service.confirm(source,{'basic_input_checks':'MODEL_BASIC_INPUT_CHECKS_PASSED','source_manifest_hash':'SYNTHETIC'})
    service.start('GRID_MAIN')
    with pytest.raises(PermissionError):service.start('GRID_MAIN')
    with pytest.raises(PermissionError):service.start('BUY_HOLD_BENCHMARK')
    service.settle('GRID_MAIN',completed=True,seconds=1,result_hash='SYNTHETIC')
    service.start('BUY_HOLD_BENCHMARK')
    service.settle('BUY_HOLD_BENCHMARK',completed=True,seconds=1,result_hash='SYNTHETIC')
    current=SearchBudgetRegistryV1(objective,path)
    assert current.used('objective',objective)==12 and current.remaining('objective',objective)==0
    with pytest.raises(PermissionError):service.confirm(source,{})
    from chanlun_trader.research_factory import etf_account_governance_v1 as mod
    class FutureClock:
        @staticmethod
        def now(tz):return datetime.now(timezone.utc)+timedelta(hours=1)
        fromisoformat=staticmethod(datetime.fromisoformat)
    monkeypatch.setattr(mod,'datetime',FutureClock)
    with pytest.raises(PermissionError,match='EXPIRED'):service.active()
    monkeypatch.undo()
    (service.root/'REVOKED.json').write_text('{}')
    with pytest.raises(PermissionError,match='REVOKED'):service.active()


def test_family_five_purposes_old_budget_and_boundaries(tmp_path,monkeypatch):
    from chanlun_trader.research_factory.etf_account_governance_v1 import ETFFamilyGovernanceV1
    from chanlun_trader.research_factory.etf_trend_risk_hypothesis_v1 import FAMILY,contract
    path=tmp_path/'budget.json';old=SearchBudgetRegistryV1('SYNTHETIC',path)
    old.register_objective(12);old.consume(old.reserve('objective','SYNTHETIC',12))
    gov=ETFFamilyGovernanceV1(tmp_path/'family',path,'SYNTHETIC')
    source={'origin':'USER_EXPLICIT_CURRENT_TASK','statement':'你直接走完为止，告诉我结果就行了'}
    inputs={'contracts':{k:contract(k) for k in gov.kinds},'input_identity':'SYNTHETIC',
            'source_manifest_hash':'SYNTHETIC','basic_input_checks':'MODEL_BASIC_INPUT_CHECKS_PASSED',
            'novelty':{k:{'allowed':True} for k in FAMILY}}
    with pytest.raises(PermissionError):gov.confirm({},inputs)
    inputs['novelty'][FAMILY[0]]['allowed']=False
    with pytest.raises(PermissionError):gov.confirm(source,inputs)
    inputs['novelty'][FAMILY[0]]['allowed']=True
    gov.confirm(source,inputs)
    with pytest.raises(PermissionError):gov.start('GRID_MAIN')
    for k in gov.kinds:
        gov.start(k)
        with pytest.raises(PermissionError):gov.start(k)
        gov.settle(k,completed=True,seconds=1,result_hash='SYNTHETIC')
    assert SearchBudgetRegistryV1('SYNTHETIC',path).used('objective','SYNTHETIC')==12
    from chanlun_trader.research_factory import etf_account_governance_v1 as mod
    class FutureClock:
        @staticmethod
        def now(tz):return datetime.now(timezone.utc)+timedelta(hours=2)
        fromisoformat=staticmethod(datetime.fromisoformat)
    monkeypatch.setattr(mod,'datetime',FutureClock)
    with pytest.raises(PermissionError,match='EXPIRED'):gov.active()
    monkeypatch.undo()
    (gov.root/'REVOKED.json').write_text('{}')
    with pytest.raises(PermissionError,match='REVOKED'):gov.active()
