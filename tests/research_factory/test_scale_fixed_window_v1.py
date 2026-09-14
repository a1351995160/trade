import sys
from pathlib import Path
from datetime import datetime,timezone,timedelta
import pandas as pd
import pytest
from chanlun_trader.research_factory.scale_proxy_signals_v1 import SMALL
from chanlun_trader.research_factory.residual_window_v1 import contract
from chanlun_trader.research_factory.common import stable_hash

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))


def test_original_account_budget_and_contract(tmp_path):
    from test_residual_window_v1 import test_actual_account_external_window_keeps_raw_price_and_fixed_exit as account
    from test_residual_window_v1 import test_canonical_increment_preserves_parent_and_rejects_replay as budget
    from chanlun_trader.research_factory.train_search_batch_v1 import contract as training
    old=training(SMALL);new=contract(SMALL)
    assert {k for k in old if old[k]!=new[k]}=={'version','adapter_version','purpose','result_type'}
    account(SMALL);budget(tmp_path,SMALL)


@pytest.mark.parametrize('all_st',[False,True])
def test_source_features_missing_turnover_retained(tmp_path,monkeypatch,all_st):
    import prepare_turnover_window_input_v1 as data
    root=tmp_path/'turnover';root.mkdir();monkeypatch.setattr(data,'ROOT',root)
    pd.DataFrame({'symbol':pd.Series(dtype='str'),'timestamp':pd.Series(dtype='int64'),'turn':pd.Series(dtype='float64')}).to_parquet(root/'TURNOVER.parquet',index=False)
    from test_residual_window_v1 import test_reuse_source_to_new_feature_bundle_and_hash_rejection as check
    check(tmp_path,monkeypatch,all_st,SMALL)


def test_read_reuse_receipt_expiry_revocation_and_identity(tmp_path,monkeypatch):
    import scale_window_input_reuse_v1 as reuse
    monkeypatch.setattr(reuse,'RECEIPT',tmp_path/'confirmation.json')
    expiry=datetime.now(timezone.utc)+timedelta(hours=1)
    parent={'receipt_id':'parent','plan':{'objective_id':'objective'}}
    monkeypatch.setattr(reuse,'active',lambda:(parent,expiry))
    monkeypatch.setattr(reuse,'evidence',lambda:({'receipt_id':'old','expires_at':expiry.isoformat()},{'input_identity':'input'},{'exact':'hash'}))
    monkeypatch.setenv('CODEX_THREAD_ID','synthetic')
    r=reuse.confirm();assert r['performance_allowance']==0 and r['new_queries']==0
    assert reuse.confirm()==r
    revoked=tmp_path/'revocation.json';revoked.write_text('{}')
    with pytest.raises(PermissionError,match='REVOKED'):reuse.guard()
    revoked.unlink()
    monkeypatch.setattr(reuse,'active',lambda:(parent,datetime.now(timezone.utc)-timedelta(seconds=1)))
    with pytest.raises(PermissionError,match='EXPIRED'):reuse.guard()
    monkeypatch.setattr(reuse,'active',lambda:(parent,expiry))
    changed={**r,'candidate':'UNAPPROVED'};changed['receipt_id']=stable_hash({k:v for k,v in changed.items() if k!='receipt_id'})
    reuse.RECEIPT.write_text(__import__('json').dumps(changed))
    with pytest.raises(PermissionError,match='CONFLICT'):reuse.guard()
