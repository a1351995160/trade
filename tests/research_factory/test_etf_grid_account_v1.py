import numpy as np
import pandas as pd
import pytest
from chanlun_trader.research_factory.etf_grid_account_v1 import run_account, ETFFeesV1, indicators


def synthetic():
    dates=list(pd.bdate_range(end='2022-07-29',periods=120))+list(pd.bdate_range('2022-08-01',periods=5))
    prices=np.r_[np.linspace(2,4,120),[3.75,4,4.1,4.1,4.1]]
    return pd.DataFrame({'date':[int(d.strftime('%Y%m%d')) for d in dates],'open':prices,
        'close':prices,'high':prices+.12,'low':prices-.12,'amount_cny':1e9,'volume_shares':3e8})


def test_fees_and_causal_indicators():
    assert ETFFeesV1().calc('SELL',200,4).stamp_tax==0
    assert ETFFeesV1().calc('BUY',200,4).total_fee==5.008
    f=synthetic();a=indicators(f);f.loc[124,'close']=100
    b=indicators(f)
    pd.testing.assert_frame_equal(a.iloc[:124],b.iloc[:124])


def test_account_grid_core_separate_and_next_open():
    result=run_account(synthetic(),[],benchmark=False,active_check=lambda:None)
    fills=result['fills'];grid=[x for x in fills if x['bucket']=='grid']
    assert len(grid)==2
    assert [f['quantity'] for f in grid]==[200,200]
    assert str(grid[0]['fill_time']).startswith('2022-08-02 09:30')
    assert str(grid[1]['fill_time']).startswith('2022-08-03 09:30')
    assert len([f for f in fills if f['bucket']=='core'])==1
    assert all(row['reserved_cash']==1000 and row['cash']>=1000 for row in result['daily'])
    assert all(o['metadata'].get('grid') or o['side'].value=='SELL' for o in result['orders']['grid'])


def test_benchmark_holds_and_requires_active_authorization():
    with pytest.raises(PermissionError):run_account(synthetic(),[],benchmark=True,active_check=None)
    def expired():raise PermissionError('EXPIRED')
    with pytest.raises(PermissionError):run_account(synthetic(),[],benchmark=True,active_check=expired)
    result=run_account(synthetic(),[],benchmark=True,active_check=lambda:None)
    assert len(result['fills'])==1 and result['fills'][0]['bucket']=='hold'


def test_dividend_record_ex_and_payment_separate():
    event={'event_id':'SYNTHETIC_DIV','symbol':'510300.SH','event_type':'CASH_DIVIDEND',
        'record_date':20220801,'effective_date':20220802,'payment_date':20220804,
        'source':'SYNTHETIC','units':'CNY_PER_SHARE','terms':{'cash_per_share':.1,
        'tax_rule':{'kind':'EXPLICIT_NET','source':'SYNTHETIC'}}}
    result=run_account(synthetic(),[event],benchmark=True,active_check=lambda:None)
    rows=result['daily'];qty=rows[0]['buckets']['hold']['quantity']
    assert rows[1]['buckets']['hold']['receivable']==pytest.approx(qty*.1)
    assert rows[3]['buckets']['hold']['receivable']==0
    assert rows[3]['cash']-rows[0]['cash']==pytest.approx(qty*.1)


def test_shared_policy_account_wiring_and_receipt_boundary():
    from chanlun_trader.research_factory.etf_grid_account_v1 import run_policy_account
    from chanlun_trader.research_factory.etf_trend_risk_hypothesis_v1 import FAMILY,BENCHMARK,contract
    with pytest.raises(PermissionError):
        run_policy_account(synthetic(),[],candidate=FAMILY[0],input_identity='SYNTHETIC',active_check=lambda:{})
    for name in (*FAMILY,BENCHMARK):
        receipt={'contracts':{name:contract(name)},'input_identity':'SYNTHETIC','novelty':{name:{'allowed':True}}}
        out=run_policy_account(synthetic(),[],candidate=name,input_identity='SYNTHETIC',active_check=lambda:receipt)
        assert out['completed'] and len(out['daily'])==5
        assert all(d['cash']>=1000 for d in out['daily'])
        assert all(f['stamp_tax']==0 for f in out['fills'])
        for f in out['fills']:
            order=next(o for o in out['orders'] if o['order_id']==f['order_id'])
            assert f['fill_time']>order['created_at']
        if name==FAMILY[1]:assert out['fills']


def test_five_account_receipts_result_serialization_and_summary(tmp_path,monkeypatch,capsys):
    import importlib.util
    import json
    from pathlib import Path
    from chanlun_trader.research_factory.etf_trend_risk_hypothesis_v1 import FAMILY,contract
    from chanlun_trader.research_factory.etf_grid_account_v1 import run_policy_account
    from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
    path=Path(__file__).resolve().parents[2]/'scripts/run_etf_grid_account_v1.py'
    spec=importlib.util.spec_from_file_location('etf_runner_synthetic',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    monkeypatch.setattr(mod,'BASE',tmp_path/'data');monkeypatch.setattr(mod,'BUDGET',tmp_path/'budget.json')
    monkeypatch.setattr(mod,'OBJECTIVE','SYNTHETIC')
    budget=SearchBudgetRegistryV1('SYNTHETIC',mod.BUDGET);budget.register_objective(12)
    budget.consume(budget.reserve('objective','SYNTHETIC',12))
    gov=mod.family_service();mod.save(gov.root/'PREVIOUS_BUDGET_SNAPSHOT.json',budget.snapshot())
    gov.confirm({'origin':'USER_EXPLICIT_CURRENT_TASK','statement':'你直接走完为止，告诉我结果就行了'},
        {'contracts':{k:contract(k) for k in gov.kinds},'input_identity':'SYNTHETIC',
         'source_manifest_hash':'SYNTHETIC','basic_input_checks':'MODEL_BASIC_INPUT_CHECKS_PASSED',
         'novelty':{k:{'allowed':True} for k in FAMILY}})
    for name in gov.kinds:
        gov.start(name)
        result=run_policy_account(synthetic(),[],candidate=name,input_identity='SYNTHETIC',active_check=gov.active)
        output=gov.root/(name+'_RESULT.json');mod.save(output,result)
        gov.settle(name,completed=True,seconds=1,result_hash=mod.sha(output))
    mod.summarize_family()
    summary=json.loads((gov.root/'SUMMARY.json').read_text(encoding='utf-8'))
    assert len(summary['results'])==5 and summary['new_main_exposures']==5
    assert summary['old_buckets_unchanged'] and summary['repair_exposures']==0
    assert all(v['pvalue'] is None for v in summary['results'].values())
