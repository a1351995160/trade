import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.market_residual_signals_v1 import EXIT_NAMES,DEFEND,ZSCORE,residual,transform
from chanlun_trader.research_factory.train_search_batch_v1 import contract
from chanlun_trader.research_factory.fixed_account_rules import FixedAccountRules
from chanlun_trader.research_factory.degraded_execution_v2 import _run_account


@pytest.mark.parametrize('name',EXIT_NAMES)
def test_same_entry_recovery_market_boundary_and_prefix(name):
    from test_market_residual_signals_v1 import inputs
    rows,days=inputs()
    rows['value']+=np.cos(np.arange(len(rows))*.73)*.005
    rows.loc[100,'market_median']=-.01
    rows.loc[100,'value']=-.1
    old=transform(rows,days,ZSCORE);new=transform(rows,days,name)
    for col in ('value','computable','effective_available_at'):
        pd.testing.assert_series_equal(old[col],new[col])
    e=residual(rows.value,rows.market_median)
    expected=(e>=0)|((rows.market_median<=0) if name==DEFEND else False)
    np.testing.assert_array_equal(new.exit_invalidated.iloc[80:],expected.iloc[80:].astype(float))
    assert new.exit_invalidated.iloc[100]==(1 if name==DEFEND else 0)
    assert new.exit_invalidated.iloc[:80].isna().all()
    pd.testing.assert_frame_equal(new.iloc[:95],transform(rows.iloc[:95],days[:95],name))
    assert not transform(rows.drop(index=70),days,name).computable.iloc[70:].any()
    rows.loc[70,'market_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    assert transform(rows,days,name).exit_available_at.iloc[100].year==2028


@pytest.mark.parametrize('name',EXIT_NAMES)
@pytest.mark.parametrize('kind',['normal','late','missing'])
def test_actual_account_exit_timing_fallback_and_costs(name,kind):
    from test_structured_exit_trial_v1 import bundle
    b,days=bundle(name,**({kind:True} if kind!='normal' else {}))
    result=_run_account(b,('SYNTHETIC',False),None,contract(name))
    sells=[f for f in result['fills'] if f['side']=='SELL']
    assert len(sells)==3
    assert {int(f['fill_time'].strftime('%Y%m%d')) for f in sells}=={days[5 if kind=='normal' else 22]}
    assert result['metrics']['ending_equity']==pytest.approx(10000-30-18-4.4955)


@pytest.mark.parametrize('name',EXIT_NAMES)
def test_full_hazard_budget_and_contract_not_relaxed(name,tmp_path):
    from test_structured_exit_trial_v1 import bundle
    from test_train_search_batch_v1 import test_governed_increment_repeat_and_revocation
    b,days=bundle(name)
    b.hazards={s:[days[15]] for s in b.daily.symbol.unique()}
    assert not _run_account(b,('SYNTHETIC',False),None,contract(name))['fills']
    bad=contract(name);bad['exit_policy']['factor_conditions'][0]['value']=0
    with pytest.raises(ValueError,match='CONFLICT'):FixedAccountRules(bad)
    test_governed_increment_repeat_and_revocation(tmp_path,name)


def test_prior_screen_pass_requires_settled_external_failure(tmp_path,monkeypatch):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
    import run_train_search_batch_v1 as runner
    monkeypatch.setattr(runner,'BATCH',25)
    monkeypatch.setattr(runner,'INPUT',tmp_path/'input')
    def write(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value),encoding='utf-8')
    write(tmp_path/'train-search-batch-v24/RUNNER_COMPLETED.json',{'status':'SCREEN_PASS_REVIEWED_NOT_QUALIFIED'})
    w=tmp_path/'residual-fixed-window-v1'
    write(w/'FINAL_STATUS.json',dict(account_started=True,account_completed=True,report_completed=True))
    write(w/'ACCOUNT_SETTLEMENT.json',{'completed':True})
    write(w/'RESULT_SUMMARY.json',{'original_metrics':{'train_net_return':-.1}})
    write(w/'RESULTS_INDEX.json',{n:{'path':str(w/n),'sha256':runner.sha(w/n)} for n in
        ('ACCOUNT_SETTLEMENT.json','RESULT_SUMMARY.json')})
    runner.reconcile_external_followup()
    write(w/'RESULT_SUMMARY.json',{'original_metrics':{'train_net_return':.1}})
    with pytest.raises(PermissionError,match='EVIDENCE_CHANGED'):runner.reconcile_external_followup()
