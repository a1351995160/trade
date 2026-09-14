from datetime import datetime,timezone
import pandas as pd
import pytest

import prepare_turnover_window_input_v1 as v


def payload(symbol='000001.SZ',value='0'):
    return {'query':v.query(symbol),'error_code':'0','fields':v.FIELDS.split(','),'rows':[
        {'code':v.query(symbol)['code'],'date':'2025-07-04','turn':value}]}


def test_exact_fields_identity_calendar_and_missing():
    p=payload();days=[20250704,20250707,20250708]
    p['rows'].append({'code':'sz.000001','date':'2025-07-08','turn':''})
    f=v.aligned(p,'000001.SZ',days)
    assert f.timestamp.tolist()==days and f.turn.iloc[0]==0
    assert f.turn.iloc[1:].isna().all()
    assert f.missing_reason.tolist()==['PRESENT','SOURCE_ROW_NOT_RETURNED','SOURCE_TURN_FIELD_EMPTY']
    with pytest.raises(ValueError,match='RESPONSE_CONFLICT'):v.validate(p,'000002.SZ',days)
    p['query']=v.query('000002.SZ')
    with pytest.raises(ValueError,match='IDENTITY'):v.validate(p,'000002.SZ',days)


@pytest.mark.parametrize('value',['-1','nan','inf'])
def test_bad_turn_rejected(value):
    with pytest.raises(ValueError,match='VALUE_INVALID'):v.validate(payload(value=value),'000001.SZ',[20250704])


def test_dates_duplicates_and_no_numeric_scope_expansion():
    p=payload();p['rows']*=2
    with pytest.raises(ValueError,match='DUPLICATE'):v.validate(p,'000001.SZ',[20250704])
    with pytest.raises(ValueError,match='DATE'):v.validate(payload(),'000001.SZ',[20250707])
    p=payload();p['fields'].append('close')
    with pytest.raises(ValueError,match='RESPONSE_CONFLICT'):v.validate(p,'000001.SZ',[20250704])


def test_receipt_repeat_revoke_calendar_conflict_and_old_budget_unchanged(tmp_path,monkeypatch):
    from chanlun_trader.research_factory.train_search_batch_v1 import contract
    for field,path in [('INPUT',tmp_path/'input'),('TRAIN',tmp_path/'train'),('ROOT',tmp_path/'out'),('PARENT',tmp_path/'parent'),('RECEIPT',tmp_path/'parent/governance/turnover_window_input_v1/confirmation.json')]:monkeypatch.setattr(v,field,path)
    monkeypatch.setenv('CODEX_THREAD_ID','synthetic')
    parent={'receipt_id':'parent','plan':{'objective_id':'objective'}}
    monkeypatch.setattr(v,'active',lambda:(parent,datetime(2027,1,1,tzinfo=timezone.utc)))
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2024-10-09','2026-07-31')]
    meta={'inputs':{},'files':{},'contract':{},'sessions':days,'symbols':[f'{i:06}.SZ' for i in range(5235)]}
    meta['input_identity']=v.stable_hash({'inputs':{},'files':{},'contract':{},'release':'release'})
    v.save(v.INPUT/'INPUT_MANIFEST.json',meta);v.save(v.INPUT/'CALENDAR_WINDOW.json',{'sessions':[pd.Timestamp(str(d)).date().isoformat() for d in days]})
    v.save(v.INPUT/'WINDOW_RELEASE.json',{'identity':'release'})
    v.save(v.TRAIN/'PREREGISTRATION.json',{'contracts':{v.NAME:contract(v.NAME)}})
    v.save(v.TRAIN/v.NAME/'FEEDBACK.json',{'screen_passed':True})
    v.save(v.TRAIN/v.NAME/'SETTLEMENT.json',{'test':True});v.save(v.TRAIN/v.NAME/'robustness-review-v1/SUMMARY.json',{})
    budget=v.PARENT/'governance/search_budget_registry.json';v.save(budget,{'used':12});original=budget.read_bytes()
    r=v.confirm();assert v.confirm()==r and budget.read_bytes()==original
    assert r['start']=='2025-07-04' and r['performance_allowance']==0 and r['wall_seconds']==9000
    class AfterExpiry(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2028,1,1,tzinfo=timezone.utc)
    monkeypatch.setattr(v,'datetime',AfterExpiry)
    with pytest.raises(PermissionError,match='EXPIRED'):v.guard()
    monkeypatch.setattr(v,'datetime',datetime)
    v.save(v.ROOT/'revocation.json',{'test':True})
    with pytest.raises(PermissionError,match='REVOKED'):v.guard()


def test_materialized_roundtrip_keeps_all_rows_and_hashes(tmp_path,monkeypatch):
    import chanlun_trader.synthetic_batch_resources as resources
    monkeypatch.setattr(v,'ROOT',tmp_path/'out');monkeypatch.setattr(v,'RECEIPT',tmp_path/'receipt.json')
    r={'receipt_id':'synthetic','symbols':['000001.SZ','000002.SZ'],'sessions':[20250704,20250707],'evaluation_window':[20250801,20260731]}
    monkeypatch.setattr(v,'guard',lambda:r)
    monkeypatch.setattr(resources,'worker_resource_handshake',lambda:{'execution':{'purpose':'TURNOVER_WINDOW_INPUT_V1','stage':'materialize'}})
    v.save(v.RECEIPT,r);v.save(v.ROOT/'FETCH_COMPLETED.json',{})
    for symbol in r['symbols']:
        p=v.ROOT/'responses'/symbol/'response.json';v.save(p,payload(symbol));v.save(p.parent/'access.json',{'sha256':v.sha(p)})
    v.materialize();f=pd.read_parquet(v.ROOT/'TURNOVER.parquet');ready=v.read(v.ROOT/'READY.json')
    assert len(f)==4 and f.turn.notna().sum()==2 and f.turn.dropna().eq(0).all()
    assert ready['files']['TURNOVER.parquet']==v.sha(v.ROOT/'TURNOVER.parquet')
    assert ready['READY_FOR_REAL_TRIAL'] is False


def test_bounded_failure_is_recorded_and_not_replayed(tmp_path,monkeypatch):
    import chanlun_trader.synthetic_batch_resources as resources
    monkeypatch.setattr(v,'ROOT',tmp_path/'out')
    r={'symbols':['000001.SZ']*4,'wall_seconds':9000,'expires_at':'2027-01-01T00:00:00+00:00'}
    monkeypatch.setattr(v,'guard',lambda:r);monkeypatch.setattr(v,'confirm',lambda:r)
    calls=[]
    def fail(*args,**kwargs):
        assert kwargs['wall_seconds']==120 and kwargs['memory_mib']==2048
        calls.append(kwargs['execution']);kwargs['on_started'](123)
        return {'returncode':1,'stdout':b'','stderr':b'test stop','timed_out':False}
    monkeypatch.setattr(resources,'run_bounded_worker',fail)
    with pytest.raises(RuntimeError,match='WORKER_FAILED'):v.run(probe=True)
    assert v.read(v.ROOT/'resources/fetch-0-4.completed.json')['returncode']==1
    with pytest.raises(PermissionError,match='NO_AUTOMATIC'):v.run(probe=True)
    assert len(calls)==1
