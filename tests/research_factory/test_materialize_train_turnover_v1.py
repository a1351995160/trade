import pytest

from materialize_train_turnover_v1 import aligned
from test_train_valuation_input_v1 import payload


def test_all_calendar_days_preserved_and_empty_not_zero():
    p=payload();days=[20220801,20220802,20220803]
    p['rows'].append({'date':'2022-08-03','code':'sz.000001','peTTM':'-3','pbMRQ':'','turn':''})
    frame=aligned(p,'000001.SZ',days)
    assert frame.timestamp.tolist()==days
    assert frame.turn.iloc[0]==0
    assert frame.turn.iloc[1:].isna().all()
    assert frame.missing_reason.tolist()==['PRESENT','SOURCE_ROW_NOT_RETURNED','SOURCE_TURN_FIELD_EMPTY']
    assert frame.source_record_present.tolist()==[True,False,True]


def test_non_session_rejected_and_no_financial_columns():
    p=payload()
    with pytest.raises(ValueError,match='NON_SESSION'):aligned(p,'000001.SZ',[20220802])
    frame=aligned(p,'000001.SZ',[20220801])
    assert 'peTTM' not in frame and 'pbMRQ' not in frame


def test_empty_symbol_still_has_all_calendar_days():
    p=payload();p['rows']=[]
    frame=aligned(p,'000001.SZ',[20220801,20220802])
    assert len(frame)==2 and frame.turn.isna().all()
    assert frame.missing_reason.eq('SOURCE_ROW_NOT_RETURNED').all()


def test_actual_materialization_roundtrip_and_source_tamper(tmp_path,monkeypatch):
    import pandas as pd
    import materialize_train_turnover_v1 as m
    import chanlun_trader.synthetic_batch_resources as resources
    source_root=tmp_path/'source';root=source_root/'turnover-input-v1';inp=tmp_path/'original'
    calendar=tmp_path/'calendar.json';receipt=tmp_path/'receipt.json'
    monkeypatch.setattr(m,'ROOT',root);monkeypatch.setattr(m,'INPUT',inp);monkeypatch.setattr(m,'CALENDAR',calendar)
    monkeypatch.setattr(m.source,'ROOT',source_root);monkeypatch.setattr(m.source,'RECEIPT',receipt)
    authority={'receipt_id':'test-receipt','plan':{'symbols':['000001.SZ','000002.SZ']}}
    monkeypatch.setattr(m.source,'guard',lambda:authority)
    m.save(receipt,authority);m.save(calendar,{'sessions':[20220801,20220802]})
    m.save(inp/'INPUT_MANIFEST.json',{'historical':{str(calendar):m.sha(calendar)}})
    m.save(source_root/'FETCH_COMPLETED.json',{'symbols':2})
    for symbol in authority['plan']['symbols']:
        p=payload();p['rows'][0]['code']=symbol[-2:].lower()+'.'+symbol[:6]
        path=source_root/'responses'/symbol/'response.json'
        m.save(path,p);m.save(path.parent/'access.json',{'sha256':m.sha(path)})
    monkeypatch.setattr(resources,'worker_resource_handshake',lambda:{'execution':{'purpose':'MATERIALIZE_TRAIN_TURNOVER_V1','code_sha256':m.sha(m.Path(m.__file__))}})
    m.worker()
    frame=pd.read_parquet(root/'TURNOVER.parquet');ready=m.read(root/'READY.json')
    assert len(frame)==4 and frame.turn.isna().sum()==2
    assert ready['output']['sha256']==m.sha(root/'TURNOVER.parquet')
    assert ready['financial_fields_eligible'] is False and ready['STRICT_TRAIN_INPUT_READY'] is False
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(PermissionError,match='BOUND_SOURCE_CHANGED'):m.guard()
