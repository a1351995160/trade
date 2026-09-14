import sys
from datetime import datetime,timezone,timedelta
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import run_train_search_sequence_v1 as mod
from chanlun_trader.research_factory.train_search_batch_v1 import contract


def fixture(tmp_path,monkeypatch):
    monkeypatch.setattr(mod,'ROOT',tmp_path)
    parent={'receipt_id':'parent'}
    expiry=datetime.now(timezone.utc)+timedelta(minutes=10)
    plan={'batches':{str(k):v for k,v in mod.BATCHES.items()},'compute_seconds':5400,
        'parent_receipt_id':'parent','expires_at':expiry.isoformat(),'code':{},
        'contracts':{n:contract(n) for names in mod.BATCHES.values() for n in names}}
    return plan,parent,expiry


def test_fixed_sequence_scope(tmp_path,monkeypatch):
    plan,parent,expiry=fixture(tmp_path,monkeypatch)
    mod.validate(plan,parent,expiry)


@pytest.mark.parametrize('change',['budget','expiry','parent','contract','candidate','revoked'])
def test_sequence_expansion_and_revocation_rejected(tmp_path,monkeypatch,change):
    plan,parent,expiry=fixture(tmp_path,monkeypatch)
    if change=='budget':plan['compute_seconds']=5401
    if change=='expiry':plan['expires_at']=(expiry+timedelta(minutes=1)).isoformat()
    if change=='parent':plan['parent_receipt_id']='other'
    if change=='contract':plan['contracts'][mod.BATCHES[12][0]]['holding_sessions']=3
    if change=='candidate':plan['batches']['12']=['other']
    if change=='revoked':(tmp_path/'revocation.json').write_text('{}')
    with pytest.raises(PermissionError):mod.validate(plan,parent,expiry)


def test_all_new_contracts_keep_account_and_authorization(tmp_path):
    from test_train_search_batch_v1 import test_governed_increment_repeat_and_revocation
    for i,name in enumerate(n for names in mod.BATCHES.values() for n in names):
        test_governed_increment_repeat_and_revocation(tmp_path/str(i),name)


def test_remaining_counts_batch_and_review_receipts_and_rejects_bad_time(tmp_path,monkeypatch):
    monkeypatch.setattr(mod,'INPUT',tmp_path/'old-input')
    monkeypatch.setattr(mod,'ROOT',tmp_path/'sequence')
    monkeypatch.setattr(mod,'authorize',lambda *args:None)
    mod.save(tmp_path/'train-search-batch-v12/resources/prepare.completed.json',{'elapsed_seconds':100})
    mod.save(tmp_path/'train-search-batch-v15/resources/account.completed.json',{'elapsed_seconds':200})
    mod.save(tmp_path/'sequence/resources/review.completed.json',{'elapsed_seconds':50})
    assert mod.remaining()==5050
    mod.save(tmp_path/'train-search-batch-v13/resources/bad.completed.json',{'elapsed_seconds':-1})
    with pytest.raises(PermissionError,match='INVALID_RESOURCE'):mod.remaining()


def test_existing_sequence_cannot_replay(tmp_path,monkeypatch):
    plan,parent,expiry=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(mod,'active',lambda:(parent,expiry))
    (tmp_path/'PLAN.json').write_text('{}')
    with pytest.raises(PermissionError,match='RECONCILE'):mod.run()


def test_batch_freeze_binds_external_plan_without_relative_path_error(monkeypatch):
    import run_train_search_batch_v1 as batch
    import execute_baostock_account_v1 as engine
    monkeypatch.setattr(batch,'BATCH',12)
    monkeypatch.setattr(batch,'sha',lambda path:'digest')
    monkeypatch.setattr(engine,'code_identity',lambda:{})
    identity=batch.code()
    assert any(key.endswith('PLAN.json') for key in identity)
