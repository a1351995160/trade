import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from weekly_progress_v1 import snapshot


def put(root,name,value):
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(value),encoding='utf-8')


def test_old_failure_does_not_override_running_recovery(tmp_path):
    put(tmp_path,'resources/prepare-old.started.json',{'at':'2026-09-13T08:00:00+00:00','pid':1})
    put(tmp_path,'resources/prepare-old.completed.json',{'returncode':1,'stderr':'MemoryError: old','elapsed_seconds':600})
    put(tmp_path,'resources/prepare-new.started.json',{'at':'2026-09-13T09:00:00+00:00','pid':2})
    state=snapshot(tmp_path,lambda _:True)
    assert state['current_worker']['name']=='prepare-new'
    assert state['current_error'] is None and len(state['historical_failures'])==1
    assert state['stages'][1]['done'] is None


def test_dead_worker_and_partial_json_are_not_success(tmp_path):
    put(tmp_path,'resources/account-main.started.json',{'at':'2026-09-13T09:00:00+00:00','pid':2})
    (tmp_path/'FINAL_STATUS.json').write_text('{',encoding='utf-8')
    state=snapshot(tmp_path,lambda _:False)
    assert '进程已退出' in state['status'] and state['warnings'] and not state['complete']


def test_current_failure_shows_original_cause(tmp_path):
    put(tmp_path,'resources/prepare-new.started.json',{'at':'2026-09-13T09:00:00+00:00','pid':2})
    put(tmp_path,'resources/prepare-new.completed.json',{'returncode':1,'elapsed_seconds':750,
        'stderr':'Traceback\nnumpy._core._exceptions._ArrayMemoryError: allocation failed'})
    state=snapshot(tmp_path,lambda _:False)
    assert state['status']=='当前执行已失败'
    assert '内存分配失败' in state['current_error']['error']


def test_completion_requires_all_index_hashes(tmp_path):
    put(tmp_path,'FINAL_STATUS.json',{'account_completed':True,'report_completed':True})
    put(tmp_path,'ACCOUNT_SETTLEMENT.json',{'completed':True})
    names=['ACCOUNT_RESULT.json','ACCOUNT_SETTLEMENT.json','RESULT_SUMMARY.json','RESULT_REVIEW_RULE.json','RESULT_REVIEW_ACCESS.json','ACCOUNT_REPORT.md']
    index={}
    for name in names:
        if not (tmp_path/name).exists():put(tmp_path,name,{})
        p=tmp_path/name;index[name]={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    put(tmp_path,'RESULTS_INDEX.json',index)
    assert snapshot(tmp_path)['complete']
    (tmp_path/'ACCOUNT_RESULT.json').write_text('changed')
    assert not snapshot(tmp_path)['complete']
