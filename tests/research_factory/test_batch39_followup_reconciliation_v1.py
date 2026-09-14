import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))


def test_train_settlement_event_schema_and_incomplete_rejection(monkeypatch,tmp_path):
    import run_train_search_batch_v1 as batch
    monkeypatch.setattr(batch,'BATCH',40);monkeypatch.setattr(batch,'INPUT',tmp_path/'input')
    monkeypatch.setattr(batch,'sha',lambda p:'hash')
    tally={'events':[{'event':'SETTLED','completed':True}], 'MAIN_BACKTEST_EXPOSURES_USED':1,'REPAIR_BACKTEST_EXPOSURES_USED':0}
    def read(p):
        n=Path(p).name
        if n=='RECOVERY.json':return {'original_evidence':{'old':'hash'}}
        if n=='RUNNER_COMPLETED.json':return {'status':'COMPOSITE_BATCH_COMPLETED_NO_SCREEN_PASS'}
        if n=='DELIVERY_STATUS.json':return {'candidates':[{'screen_passed':False,'evidence':{'result':'result','result_sha256':'hash','settlement':'settlement','settlement_sha256':'hash'}}]}
        return tally
    monkeypatch.setattr(batch,'read',read)
    batch.reconcile_batch39_recovery()
    tally['events'][0]['completed']=False
    with pytest.raises(PermissionError,match='RECOVERY_NOT_FAILED_COMPLETE'):batch.reconcile_batch39_recovery()
