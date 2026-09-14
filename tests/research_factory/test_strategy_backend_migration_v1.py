"""全部现有账户类型接入同一计划/预算/worker；只使用合成数据。"""
from datetime import datetime,timezone,timedelta
from pathlib import Path
import importlib.util
import json
import os

import pytest

from chanlun_trader.research_factory.strategy_interface_v1 import prepare,backend_for,evaluate_novelty,run
from chanlun_trader.research_factory.etf_grid_rules_v1 import ETFGridStrategy
from chanlun_trader.research_factory.stock_strategy_v1 import StockFactorStrategy
from chanlun_trader.research_factory.degraded_execution_v2 import CONTRACT
from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1

IDENTITY='SYNTHETIC_INTERFACE_DATA'


def load_synthetic_etf():
    from test_etf_grid_account_v1 import synthetic
    return {'frame':synthetic(),'actions':[],'input_identity':IDENTITY}


def load_synthetic_stock(contract=None):
    from test_degraded_execution_v2 import degraded
    from chanlun_trader.research_factory.common import stable_hash
    bundle=degraded();bundle.input_identity=IDENTITY
    bundle.contract_identity=stable_hash(contract or CONTRACT)
    return {'frame':bundle,'actions':bundle.actions,'input_identity':IDENTITY}


def load_synthetic_failure():raise RuntimeError('SYNTHETIC_LOADER_FAILURE')


def authorize(root,plans,input_identity=IDENTITY):
    path=root.parent/'budget.json';budget=SearchBudgetRegistryV1('SYNTHETIC',path)
    budget.register_objective(12)
    if not budget.used('objective','SYNTHETIC'):budget.consume(budget.reserve('objective','SYNTHETIC',12))
    gov=StrategyBatchGovernanceV1(root,path,'SYNTHETIC',plans)
    gov.confirm({'origin':'USER_EXPLICIT_CURRENT_TASK','statement':'SYNTHETIC_TEST_ONLY',
        'approved_plan_ids':{k:p['plan_id'] for k,p in plans.items()},
        'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()},
        {'input_identity':input_identity,'novelty':evaluate_novelty(plans,[])})
    return gov


@pytest.mark.parametrize('benchmark',[False,True])
def test_grid_and_buyhold_via_same_interface_preserve_original_accounts(tmp_path,benchmark):
    from chanlun_trader.research_factory.etf_grid_account_v1 import run_account
    strategy=ETFGridStrategy(benchmark);backend=backend_for(strategy);plan=prepare(strategy,backend)
    gov=authorize(tmp_path/'run',{strategy.strategy_id:plan});gov.start(strategy.strategy_id)
    data=load_synthetic_etf()
    out=run(strategy,backend,**data,active_check=lambda:gov.active_execution(strategy.strategy_id))
    legacy=run_account(data['frame'],[],benchmark=benchmark,active_check=lambda:None)
    assert out['metrics']==legacy['metrics'] and out['fills']==legacy['fills'] and out['ledgers']==legacy['ledgers']
    assert set(out['ledgers'])==({'hold'} if benchmark else {'core','grid'})
    assert out['report']['artifacts']['ledger']=={'in_memory':'ledgers'}


@pytest.mark.parametrize('legacy',[False,True])
def test_stock_same_interface_and_new_name_needs_no_account_branch(tmp_path,legacy):
    contract=dict(CONTRACT) if legacy else {**CONTRACT,'signal_version':'SYNTHETIC_UNLISTED_RULE','holding_sessions':3}
    strategy=StockFactorStrategy(contract,legacy);backend=backend_for(strategy);plan=prepare(strategy,backend)
    gov=authorize(tmp_path/'run',{strategy.strategy_id:plan});gov.start(strategy.strategy_id)
    data=load_synthetic_stock(contract)
    out=run(strategy,backend,**data,active_check=lambda:gov.active_execution(strategy.strategy_id))
    assert out['status']=='COMPLETE' and len({f['symbol'] for f in out['fills']})==3
    assert out['metrics']['total_stamp_tax']>0
    assert out['report']['metrics']['net_return']==out['metrics']['train_net_return']
    assert out['report']['artifacts']['ledger']=={'in_memory':'final_account_checkpoint'}


def test_stock_partial_does_not_become_complete_report(tmp_path):
    strategy=StockFactorStrategy(CONTRACT,True);backend=backend_for(strategy)
    gov=authorize(tmp_path/'run',{strategy.strategy_id:prepare(strategy,backend)});gov.start(strategy.strategy_id)
    data=load_synthetic_stock();bundle=data['frame']
    bundle.hazards={s:[20220809] for s in bundle.daily.symbol.unique()}
    bundle.states.loc[bundle.states.trade_date=='20220808','suspension_status']='SUSPENDED'
    out=run(strategy,backend,**data,active_check=lambda:gov.active_execution(strategy.strategy_id))
    assert out['status']=='PARTIAL_UNSUPPORTED_EVENT' and out['metrics'] is None
    assert all(v is None for v in out['report']['metrics'].values())


def runner():
    path=Path(__file__).resolve().parents[2]/'scripts/run_strategy_account_v1.py'
    spec=importlib.util.spec_from_file_location('generic_account_runner',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize('kind',['grid','stock'])
def test_original_bounded_worker_freeze_consume_run_settle_and_archive(tmp_path,monkeypatch,kind):
    mod=runner();root=tmp_path/'job'
    item={'factory':'chanlun_trader.research_factory.etf_grid_rules_v1:ETFGridStrategy',
          'loader':'test_strategy_backend_migration_v1:load_synthetic_etf'} if kind=='grid' else {
          'factory':'chanlun_trader.research_factory.stock_strategy_v1:StockFactorStrategy',
          'factory_kwargs':{'contract':{**CONTRACT,'signal_version':'SYNTHETIC_WORKER_RULE'},'legacy':False},
          'loader':'test_strategy_backend_migration_v1:load_synthetic_stock',
          'loader_kwargs':{'contract':{**CONTRACT,'signal_version':'SYNTHETIC_WORKER_RULE'}}}
    config={'items':[item],'objective_id':'SYNTHETIC','budget_path':str(tmp_path/'budget.json'),'input_identity':IDENTITY}
    job=mod.freeze_config(config,root)
    with pytest.raises(FileNotFoundError):mod.execute(root/'JOB.json')
    assert not list(root.glob('*_START.json'))
    gov=authorize(root,job['plans'])
    monkeypatch.setenv('PYTHONPATH',str(Path(__file__).parent)+os.pathsep+os.environ.get('PYTHONPATH',''))
    index=mod.execute(root/'JOB.json')
    name=next(iter(index));result=json.loads(Path(index[name]['result']).read_text(encoding='utf-8'))
    assert result['report']['qualification']=='NOT_ASSESSED'
    assert json.loads((root/(name+'_RESOURCE.json')).read_text(encoding='utf-8'))['returncode']==0
    assert SearchBudgetRegistryV1('SYNTHETIC',gov.budget_path).used('objective','SYNTHETIC')==12
    assert Path(index[name]['report']).exists() and mod.status(root/'JOB.json')['progress_percent']==100
    with pytest.raises(PermissionError,match='ALREADY_ATTEMPTED'):mod.execute(root/'JOB.json')


def test_worker_failure_is_settled_and_never_becomes_free_retry(tmp_path,monkeypatch):
    mod=runner();root=tmp_path/'job'
    job=mod.freeze_config({'items':[{'factory':'chanlun_trader.research_factory.etf_grid_rules_v1:ETFGridStrategy',
        'loader':'test_strategy_backend_migration_v1:load_synthetic_failure'}],
        'objective_id':'SYNTHETIC','budget_path':str(tmp_path/'budget.json'),'input_identity':IDENTITY},root)
    gov=authorize(root,job['plans'])
    monkeypatch.setenv('PYTHONPATH',str(Path(__file__).parent)+os.pathsep+os.environ.get('PYTHONPATH',''))
    with pytest.raises(RuntimeError,match='NO_RETRY'):mod.execute(root/'JOB.json')
    name=next(iter(job['plans']));status=mod.status(root/'JOB.json')
    assert status['items'][name]['state']=='FAILED'
    assert json.loads((root/(name+'_FAILURE.json')).read_text(encoding='utf-8'))['message']=='SYNTHETIC_LOADER_FAILURE'
    assert SearchBudgetRegistryV1('SYNTHETIC',gov.budget_path).used(gov.budget_kind,gov.active()['receipt_id']+':'+name)==1
    with pytest.raises(PermissionError):mod.execute(root/'JOB.json')


def test_common_job_benchmark_uses_all_matching_dates(tmp_path,monkeypatch):
    mod=runner();root=tmp_path/'job'
    items=[{'factory':'chanlun_trader.research_factory.etf_grid_rules_v1:ETFGridStrategy',
        'factory_kwargs':{'benchmark':flag},'loader':'test_strategy_backend_migration_v1:load_synthetic_etf'} for flag in (False,True)]
    job=mod.freeze_config({'items':items,'benchmark_id':'ETF_BUY_HOLD','objective_id':'SYNTHETIC',
        'budget_path':str(tmp_path/'budget.json'),'input_identity':IDENTITY},root)
    authorize(root,job['plans'])
    monkeypatch.setenv('PYTHONPATH',str(Path(__file__).parent)+os.pathsep+os.environ.get('PYTHONPATH',''))
    mod.execute(root/'JOB.json')
    report=json.loads((root/'ETF_CORE_GRID_REPORT.json').read_text(encoding='utf-8'))
    assert report['benchmark']['status']=='AVAILABLE'
    assert report['benchmark']['strategy_id']=='ETF_BUY_HOLD'
