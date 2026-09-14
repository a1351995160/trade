from dataclasses import replace
from datetime import datetime,timezone,timedelta
import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.strategy_interface_v1 import (
    Requirements,Decision,QuantityOrder,TargetWeight,prepare,run,evaluate_novelty)
from chanlun_trader.research_factory.etf_grid_account_v1 import ETFDailyBackend
from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1


def frame():
    dates=pd.bdate_range(end='2022-07-29',periods=120).append(pd.bdate_range('2022-08-01',periods=6))
    price=np.full(len(dates),4.)
    return pd.DataFrame(dict(date=[int(d.strftime('%Y%m%d')) for d in dates],open=price,close=price,
        high=price+.1,low=price-.1,amount_cny=1e9,volume_shares=3e8))


class QuantityPlugin:
    strategy_id='SYNTHETIC_QUANTITY_PLUGIN'
    parameters={'rule_definition':'BUY_200_THEN_SELL_100_PER_CLOSE_BY_LOT'}
    source_files=()
    requirements=Requirements('ETF','1D','RAW',('close',),120,('QUANTITY_ORDER',),capabilities=('SELL_LOT',))

    def on_close(self,c):
        assert len(c.history)==c.index+1
        qty=c.account['quantity'];state=c.state
        # 尝试改变收到的副本，不能改变引擎行情或现金。
        c.history.loc[:,'close']=999.;c.account['cash']=99999999
        if qty:
            lot=next(k for k,v in c.account['lots'].items() if v['remaining_quantity'])
            return Decision('PARTIAL_LOT_EXIT',QuantityOrder('SELL',100,lot),state)
        if not state.get('entered'):
            return Decision('ENTER',QuantityOrder('BUY',200),{'entered':True})
        return Decision('STAY_CASH',state=state)


def authorized(tmp_path,strategy):
    backend=ETFDailyBackend();plan=prepare(strategy,backend);plans={strategy.strategy_id:plan}
    path=tmp_path/'budget.json';budget=SearchBudgetRegistryV1('SYNTHETIC',path)
    budget.register_objective(12);budget.consume(budget.reserve('objective','SYNTHETIC',12))
    gov=StrategyBatchGovernanceV1(tmp_path/'run',path,'SYNTHETIC',plans)
    gov.confirm({'origin':'USER_EXPLICIT_CURRENT_TASK','statement':'SYNTHETIC_TEST_ONLY',
        'approved_plan_ids':{strategy.strategy_id:plan['plan_id']},
        'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()},
        {'input_identity':'SYNTHETIC','novelty':evaluate_novelty(plans,[])})
    return backend,gov


def test_new_quantity_plugin_uses_common_account_governance_and_report(tmp_path):
    plugin=QuantityPlugin();backend,gov=authorized(tmp_path,plugin)
    with pytest.raises(PermissionError,match='START_AND_CONSUMPTION'):
        run(plugin,backend,frame=frame(),actions=[],input_identity='SYNTHETIC',active_check=gov.active)
    gov.start(plugin.strategy_id)
    out=run(plugin,backend,frame=frame(),actions=[],input_identity='SYNTHETIC',
            active_check=lambda:gov.active_execution(plugin.strategy_id))
    assert [f['quantity'] for f in out['fills']]==[200,100,100]
    assert [r['quantity'] for r in out['daily'][:3]]==[200,100,0]
    assert all(f['price']<5 for f in out['fills'])
    assert all(1000<=d['cash']<11000 for d in out['daily'])
    assert out['report']['metrics']==out['metrics']
    assert out['report']['qualification']=='NOT_ASSESSED'
    gov.settle(plugin.strategy_id,completed=True,seconds=1,result_hash='SYNTHETIC')
    with pytest.raises(PermissionError):gov.start(plugin.strategy_id)
    with pytest.raises(PermissionError):gov.active_execution(plugin.strategy_id)
    assert SearchBudgetRegistryV1('SYNTHETIC',gov.budget_path).used('objective','SYNTHETIC')==12


@pytest.mark.parametrize('change',[{'frequency':'5m'},{'asset':'FUTURES'},{'price_view':'ADJUSTED'},
    {'intents':('LIMIT_ORDER',)},{'capabilities':('SEGREGATED_BUCKETS',)}])
def test_unsupported_capabilities_rejected_before_account_or_permission(change):
    plugin=QuantityPlugin();plugin.requirements=replace(plugin.requirements,**change)
    with pytest.raises(ValueError,match='UNSUPPORTED'):prepare(plugin,ETFDailyBackend())


def test_parameter_change_cannot_reuse_plan_and_novelty(tmp_path):
    plugin=QuantityPlugin();backend,gov=authorized(tmp_path,plugin)
    plan=prepare(plugin,backend)
    gate=evaluate_novelty({plugin.strategy_id:plan},[{'candidate_id':'OLD', 'candidate_hash':plan['plan_id']}])
    assert not gate[plugin.strategy_id]['allowed']
    plugin.parameters={'rule_definition':'CHANGED'}
    with pytest.raises(PermissionError,match='NOT_AUTHORIZED'):
        run(plugin,backend,frame=frame(),actions=[],input_identity='SYNTHETIC',active_check=gov.active)


def test_declared_intent_and_sizing_boundary():
    from chanlun_trader.research_factory.strategy_interface_v1 import validate_decision
    req=QuantityPlugin.requirements
    for intent in (TargetWeight(.5),QuantityOrder('SELL',99),QuantityOrder('BUY',100,'lot-1')):
        with pytest.raises(ValueError):validate_decision(Decision('TEST',intent),req)


@pytest.mark.parametrize('index,expected',[
    (0,'7fd5e305de9a343a12a16daa8b946b7d2490091f2ea0dc99fcbe5bee2361dbef'),
    (1,'c456bf8768614381502c88d2c87437baf3f087cc09a63dacee83438e296f760a'),
    (2,'6f00554abf5f608e13f957d234f4b2ffeb6dcdb8e9b2d8afac37c3e4184e01e2'),
    (3,'92d5f58e26ea723505acc7f334ba882c025d74d814efdc2300c72f81f4115465'),
    (4,'a883daef7607069a64a807c5082f3a29a056e49f8b482b9469ffd01ecd2b4251')])
def test_full_account_parity_against_frozen_pre_refactor_synthetic_output(index,expected):
    # 基线由已冻结旧实现生成，覆盖逐日账户、订单、成交、状态、账本；无真实行情。
    import json,hashlib
    from chanlun_trader.research_factory.etf_grid_account_v1 import run_policy_account
    from chanlun_trader.research_factory.etf_trend_risk_hypothesis_v1 import FAMILY,BENCHMARK,contract
    dates=pd.bdate_range(end='2022-07-29',periods=120).append(pd.bdate_range('2022-08-01',periods=160))
    x=np.arange(len(dates));price=3.6+.002*x+.18*np.sin(x/9)+.05*np.cos(x/2)
    data=pd.DataFrame(dict(date=[int(d.strftime('%Y%m%d')) for d in dates],open=price,close=price,
        high=price+.05,low=price-.05,amount_cny=1e9,volume_shares=3e8))
    name=(*FAMILY,BENCHMARK)[index]
    receipt={'contracts':{name:contract(name)},'input_identity':'SYNTHETIC','novelty':{name:{'allowed':True}}}
    result=run_policy_account(data,[],candidate=name,input_identity='SYNTHETIC',active_check=lambda:receipt)
    assert hashlib.sha256(json.dumps(result,sort_keys=True,default=str).encode()).hexdigest()==expected


def test_generic_receipt_revocation_and_missing_warmup(tmp_path):
    plugin=QuantityPlugin();backend,gov=authorized(tmp_path,plugin);gov.start(plugin.strategy_id)
    with pytest.raises(ValueError,match='WARMUP'):
        run(plugin,backend,frame=frame().iloc[1:],actions=[],input_identity='SYNTHETIC',
            active_check=lambda:gov.active_execution(plugin.strategy_id))
    (gov.root/'REVOKED.json').write_text('{}')
    with pytest.raises(PermissionError,match='REVOKED'):gov.active_execution(plugin.strategy_id)
