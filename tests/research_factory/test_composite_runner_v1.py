import sys
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timezone,timedelta

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import run_composite_research_v1 as mod


def setup(tmp_path,monkeypatch,status='FIXED_QUEUE_EXHAUSTED_NO_ROBUST_CANDIDATE'):
    monkeypatch.setattr(mod,'ROOT',tmp_path/'batch')
    monkeypatch.setattr(mod,'INPUT',tmp_path/'input')
    monkeypatch.setattr(mod,'active',lambda:({},datetime.now(timezone.utc)+timedelta(hours=1)))
    mod.save(tmp_path/'train-search-sequence-v2/COMPLETED.json',{'status':status})


def test_unresolved_prior_sequence_rejected(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch,'ENGINEERING_FAILURE_NEEDS_TRIAGE')
    with pytest.raises(PermissionError,match='RECONCILIATION'):mod.run()
    assert not (mod.ROOT/'RUNNER_STARTED.json').exists()


def test_existing_started_cannot_replay(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch)
    mod.save(mod.ROOT/'RUNNER_STARTED.json',{'pid':1})
    with pytest.raises(PermissionError,match='REPLAY'):mod.run()


def test_child_failure_is_recorded_without_report_or_retry(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch)
    calls=[]
    monkeypatch.setattr(mod.subprocess,'run',lambda *a,**k:(calls.append(a),SimpleNamespace(returncode=1))[1])
    monkeypatch.setattr(mod,'artifact',lambda *a:pytest.fail('Must not report failed batch'))
    with pytest.raises(RuntimeError,match='BATCH_FAILED'):mod.run()
    assert len(calls)==1
    assert mod.read(mod.ROOT/'RUNNER_COMPLETED.json')['status']=='ENGINEERING_FAILURE_NEEDS_TRIAGE'


def test_review_failure_never_recorded_as_success(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch)
    monkeypatch.setattr(mod.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0))
    mod.save(mod.ROOT/'DELIVERY_STATUS.json',{'candidates':[{'candidate':'x','resources':[],'screen_passed':True}]})
    def artifact(args,label):
        if label.startswith('review'):raise RuntimeError('review failed')
    monkeypatch.setattr(mod,'artifact',artifact)
    with pytest.raises(RuntimeError,match='review failed'):mod.run()
    assert mod.read(mod.ROOT/'RUNNER_COMPLETED.json')['status']=='ENGINEERING_FAILURE_NEEDS_TRIAGE'


def test_batch18_uses_previous17_and_performs_learning(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch)
    monkeypatch.setattr(mod,'BATCH',18)
    mod.save(tmp_path/'train-search-batch-v17/RUNNER_COMPLETED.json',{'status':'COMPOSITE_BATCH_COMPLETED_NO_SCREEN_PASS'})
    monkeypatch.setattr(mod.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0))
    mod.save(mod.ROOT/'DELIVERY_STATUS.json',{'candidates':[{'candidate':'x','resources':[],'screen_passed':False}]})
    calls=[]
    monkeypatch.setattr(mod,'artifact',lambda args,label:calls.append((args,label)))
    mod.run()
    assert calls[-1]==(['scripts/review_failed_composites_v1.py','--batch','18'],'failure-learning')
    assert mod.read(mod.ROOT/'RUNNER_COMPLETED.json')['status']=='COMPOSITE_BATCH_COMPLETED_NO_SCREEN_PASS'


def test_failure_learning_preserves_no_outcome_and_rejects_missing_exposed_result(tmp_path,monkeypatch):
    import review_failed_composites_v1 as review
    monkeypatch.setattr(review,'ROOT',tmp_path/'learning')
    monkeypatch.setattr(review,'active',lambda:None)
    monkeypatch.setenv('CODEX_THREAD_ID','synthetic-thread')
    review.save(tmp_path/'PREREGISTRATION.json',{'contracts':{'x':{}}})
    row={'candidate':'x','evidence':{},'account_exposures':0,'repair_exposures':0,'novelty_allowed':True}
    review.save(tmp_path/'DELIVERY_STATUS.json',{'candidates':[row]})
    review.save(tmp_path/'x/FEASIBILITY.json',{'passed':False})
    review.run()
    saved=review.read(review.ROOT/'SUMMARY.json')['candidates'][0]
    assert saved['status']=='FEASIBILITY_REJECTED' and saved['stored_metrics'] is None
    monkeypatch.setattr(review,'ROOT',tmp_path/'learning2')
    review.save(tmp_path/'x/EXECUTION.json',{'execution_id':'exposed'})
    with pytest.raises(PermissionError,match='EXPOSED_RESULT_MISSING'):review.run()
