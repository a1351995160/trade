import sys
from pathlib import Path

import pandas as pd
import pytest

from chanlun_trader.research_factory.residual_window_v1 import contract
from chanlun_trader.research_factory.train_search_batch_v1 import contract as training
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.fixed_account_rules import FixedAccountRules

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
NAME='LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_20'


def test_fixed_rule_account_and_governance(tmp_path):
    from test_residual_window_v1 import test_actual_account_external_window_keeps_raw_price_and_fixed_exit as account
    from test_residual_window_v1 import test_canonical_increment_preserves_parent_and_rejects_replay as budget
    old=training(NAME);new=contract(NAME)
    assert {k for k in old if old[k]!=new[k]}=={'version','adapter_version','purpose','result_type'}
    assert new['source_candidate_contract_hash']==stable_hash(old)
    assert FixedAccountRules(new).signal_window==(20250801,20260731)
    for key,value in [('holding_sessions',19),('signal_window',[20250801,20260801])]:
        with pytest.raises((ValueError,PermissionError)):FixedAccountRules({**new,key:value})
    account(NAME);budget(tmp_path,NAME)


def test_supplement_identity_hash_and_physical_window(tmp_path,monkeypatch):
    import execute_residual_window_v1 as exe
    import prepare_turnover_window_input_v1 as data
    monkeypatch.setattr(exe,'IS_TURNOVER',True);monkeypatch.setattr(exe,'NAME',NAME)
    monkeypatch.setattr(exe,'contract',lambda:contract(NAME))
    monkeypatch.setattr(data,'ROOT',tmp_path);monkeypatch.setattr(data,'RECEIPT',tmp_path/'receipt.json')
    receipt={'receipt_id':'SYNTHETIC','candidate':NAME,'source_candidate_hash':contract(NAME)['source_candidate_contract_hash']}
    monkeypatch.setattr(data,'guard',lambda:receipt)
    exe.save(data.RECEIPT,receipt)
    frame=pd.DataFrame({'symbol':['000001.SZ'],'timestamp':[20250704],'turn':[0.]})
    frame.to_parquet(tmp_path/'TURNOVER.parquet',index=False)
    for name in ('COVERAGE.json','MATERIALIZATION_RULE.json'):exe.save(tmp_path/name,{})
    def ready():
        payload={'receipt_id':'SYNTHETIC','files':{name:exe.sha(tmp_path/name) for name in ('TURNOVER.parquet','COVERAGE.json','MATERIALIZATION_RULE.json')},'request_window':[20250704,20260731],'evaluation_window':[20250801,20260731]}
        return {**payload,'input_identity':stable_hash(payload),'status':'TURNOVER_WINDOW_INPUT_READY_NOT_QUALIFIED'}
    value=ready();exe.save(tmp_path/'READY.json',value)
    assert len(exe.turnover_input_evidence())==5
    receipt['source_candidate_hash']='different'
    with pytest.raises(PermissionError,match='IDENTITY'):exe.turnover_input_evidence()
    receipt['source_candidate_hash']=contract(NAME)['source_candidate_contract_hash']
    (tmp_path/'COVERAGE.json').write_text('changed')
    with pytest.raises(PermissionError,match='CHANGED'):exe.turnover_input_evidence()
    frame['timestamp']=20260801;frame.to_parquet(tmp_path/'TURNOVER.parquet',index=False)
    monkeypatch.setattr(exe,'read',lambda path:ready() if Path(path).name=='READY.json' else {})
    with pytest.raises(PermissionError,match='PHYSICAL'):exe.turnover_input_evidence()
    def revoked():raise PermissionError('REVOKED')
    monkeypatch.setattr(data,'guard',revoked)
    with pytest.raises(PermissionError,match='REVOKED'):exe.turnover_input_evidence()


@pytest.mark.parametrize('all_st',[False,True])
def test_original_source_to_turnover_features(tmp_path,monkeypatch,all_st):
    import prepare_turnover_window_input_v1 as data
    root=tmp_path/'turnover';root.mkdir();monkeypatch.setattr(data,'ROOT',root)
    # 固定源fixture证券/日历；缺换手保留不可计算，不强迫生成成交。
    pd.DataFrame({'symbol':pd.Series(dtype='str'),'timestamp':pd.Series(dtype='int64'),'turn':pd.Series(dtype='float64')}).to_parquet(root/'TURNOVER.parquet',index=False)
    from test_residual_window_v1 import test_reuse_source_to_new_feature_bundle_and_hash_rejection as check
    check(tmp_path,monkeypatch,all_st,NAME)
    if not all_st:
        import prepare_residual_window_v1 as prep
        import json
        meta=json.loads((tmp_path/'new/INPUT_MANIFEST.json').read_text())
        days=meta['sessions']
        from chanlun_trader.research_factory.weekly_parquet_v1 import compact_read
        states=compact_read(tmp_path/'new/STATES.parquet')
        state=states[states.symbol=='600000.SH'].sort_values('trade_date').reset_index(drop=True)
        raw=json.loads((tmp_path/'old/responses/probe-3.json').read_text())['rows']
        hfq=json.loads((tmp_path/'old/responses/probe-1.json').read_text())['rows']
        subset=[int(r['date'].replace('-','')) for r in raw]
        state=state[state.trade_date.astype(str).str.replace('-','').astype(int).isin(subset)].reset_index(drop=True)
        base=prep.eligible_base('600000.SH',raw,hfq,subset,state)
        prices={int(r['date'].replace('-','')):float(r['close']) for r in raw}
        assert not base.empty
        assert base.signal_close.tolist()==pytest.approx([2*prices[d] for d in base.timestamp])
        assert 'close' not in base  # 原始成交列不会覆盖信号价格。
