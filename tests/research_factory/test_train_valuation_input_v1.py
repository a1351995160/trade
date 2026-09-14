from datetime import datetime,timezone
import pytest

import prepare_train_valuation_input_v1 as v


def payload():
    return {'error_code':'0','fields':v.FIELDS.split(','),'rows':[
        {'date':'2022-08-01','code':'sz.000001','peTTM':'-2','pbMRQ':'','turn':'0'}]}


def test_negative_valuation_missing_and_zero_turn_preserved():
    p=payload();result=v.validate_response(p,'000001.SZ')
    assert result['missing']=={'peTTM':0,'pbMRQ':1,'turn':0}
    assert p['rows'][0]['peTTM']=='-2' and p['rows'][0]['pbMRQ']==''


@pytest.mark.parametrize('field,value',[('date','2025-01-01'),('code','sz.000002'),('turn','-1'),('pbMRQ','nan')])
def test_response_identity_window_and_numeric_reject(field,value):
    p=payload();p['rows'][0][field]=value
    with pytest.raises(ValueError):v.validate_response(p,'000001.SZ')


def test_duplicate_and_missing_field_rejected():
    p=payload();p['rows']*=2
    with pytest.raises(ValueError,match='DATE_CONFLICT'):v.validate_response(p,'000001.SZ')
    p=payload();p['fields'].pop()
    with pytest.raises(ValueError,match='FIELDS_CONFLICT'):v.validate_response(p,'000001.SZ')


def test_delegated_read_receipt_repeat_revocation_and_no_budget_change(tmp_path,monkeypatch):
    parent=tmp_path/'parent';inp=tmp_path/'input';root=tmp_path/'output'
    monkeypatch.setattr(v,'PARENT',parent);monkeypatch.setattr(v,'INPUT',inp);monkeypatch.setattr(v,'ROOT',root)
    monkeypatch.setattr(v,'RECEIPT',parent/'governance/train_valuation_input_v1/confirmation.json')
    monkeypatch.setenv('CODEX_THREAD_ID','synthetic-thread')
    expiry=datetime(2027,1,1,tzinfo=timezone.utc)
    monkeypatch.setattr(v,'active',lambda:({'receipt_id':'parent','plan':{'objective_id':'objective'}},expiry))
    v.save(inp/'READ_PLAN.json',{'symbols':[f'{i:06}.SZ' for i in range(5182)],'start':v.START,'end':v.END})
    budget=parent/'governance/search_budget_registry.json';v.save(budget,{'untouched':12});before=budget.read_bytes()
    first=v.confirm();assert v.confirm()==first;assert v.guard()==first
    assert budget.read_bytes()==before
    assert first['plan']['new_performance_allowance']==0
    v.save(v.RECEIPT.parent/'revocation.json',{'reason':'test'})
    with pytest.raises(PermissionError,match='REVOKED'):v.guard()
    assert budget.read_bytes()==before
