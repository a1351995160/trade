"""宿主健康、真实进程锁与账户恢复；合成账户不作为真实观察证据。"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory.trusted_research_host_v1 import TrustedResearchHostV1
from chanlun_trader.research_daemon_state import DaemonAlreadyRunningError
from scripts.run_strategy_account_v1 import execute_accounts, report_account_job
from test_research_evidence_v1 import evidence, read, write


def empty_service(root):
    return SimpleNamespace(root=root,jobs=SimpleNamespace(root=root/'jobs'))


def test_offline_does_not_create_lock_and_bad_lock_is_blocked(tmp_path):
    host = TrustedResearchHostV1(empty_service(tmp_path))
    assert host.status()=={'status':'OFFLINE','background_enabled':False}
    assert not host.root.exists()
    host.root.mkdir()
    (host.root/'HOST.lock').write_text('{broken')
    assert host.status()['status']=='BLOCKED'
    assert not host.stop()['background_enabled']


def test_real_process_pid_and_two_hosts_cannot_dispatch(tmp_path):
    host = TrustedResearchHostV1(empty_service(tmp_path))
    code = ("import sys, os; from chanlun_trader.research_daemon_state import DaemonInstanceLockV1; "
            "lock=DaemonInstanceLockV1(sys.argv[1],'LIFECYCLE_HOST'); lock.acquire(run_id='test'); "
            "print('READY:'+str(os.getpid()),flush=True); sys.stdin.readline(); lock.release()")
    flags = subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    process = subprocess.Popen([sys.executable,'-c',code,str(host.lock.path)],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
        creationflags=flags,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1',
            'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'src')+os.pathsep+os.environ.get('PYTHONPATH','')})
    try:
        ready = process.stdout.readline().strip()
        assert ready.startswith('READY:'), ready
        child_pid = int(ready.split(':')[1])
        status = host.status()
        assert status['status']=='RUNNING' and status['pid']==child_pid
        with pytest.raises(DaemonAlreadyRunningError):
            host.lock.acquire(run_id='second')
        assert host.stop()['status']=='STOP_REQUESTED'
    finally:
        process.communicate(chr(10),timeout=10)
    assert host.status()['status']=='OFFLINE'


def test_environment_failure_does_not_claim_running(tmp_path,monkeypatch):
    from chanlun_trader.research_factory import trusted_research_host_v1 as module
    monkeypatch.setattr(module,'environment_check',lambda:{'ready':False,'missing':['pandas']})
    host = TrustedResearchHostV1(empty_service(tmp_path))
    with pytest.raises(ValueError,match='HOST_DEPENDENCIES_MISSING'):
        host.run(once=True)
    assert not host.status()['background_enabled']


def test_long_tick_has_heartbeat_and_releases_lock(tmp_path,monkeypatch):
    from chanlun_trader.research_factory import trusted_research_host_v1 as module
    import time
    monkeypatch.setattr(module,'environment_check',lambda:{'ready':True,'missing':[]})
    host = TrustedResearchHostV1(empty_service(tmp_path),interval=.02)
    beats=[]
    original=host.lock.heartbeat
    def beat():
        original();beats.append(True)
    monkeypatch.setattr(host.lock,'heartbeat',beat)
    monkeypatch.setattr(host,'tick',lambda:(time.sleep(.08) or {}))
    assert host.run(once=True)['status']=='STOPPED'
    assert beats and host.status()['status']=='OFFLINE'


def test_completed_account_recovers_index_without_worker_or_budget_repeat(tmp_path,monkeypatch):
    root=evidence(tmp_path)
    (root/'RESULTS_INDEX.json').unlink()
    import chanlun_trader.synthetic_batch_resources as resources
    monkeypatch.setattr(resources,'run_bounded_worker',lambda *a,**k:pytest.fail('must not run worker'))
    from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
    job=read(root/'JOB.json')
    budget=SearchBudgetRegistryV1(job['objective_id'],job['budget_path'])
    before=budget.snapshot()['settled_reservations']
    index=execute_accounts(root/'JOB.json',recover=True)
    assert set(index)=={'case'}
    assert budget.snapshot()['settled_reservations']==before
    report_account_job(root/'JOB.json')
    original=(root/'case_REPORT.json').read_bytes()
    (root/'case_REPORT.md').unlink()
    report_account_job(root/'JOB.json')
    assert (root/'case_REPORT.json').read_bytes()==original
    assert (root/'case_REPORT.md').exists()


def test_result_and_resource_before_settlement_can_reconcile_without_retrade(tmp_path,monkeypatch):
    root=evidence(tmp_path)
    (root/'case_SETTLEMENT.json').unlink()
    (root/'RESULTS_INDEX.json').unlink()
    resource=read(root/'case_RESOURCE.json');resource['elapsed_wall_seconds']=1
    write(root/'case_RESOURCE.json',resource)
    from chanlun_trader.research_daemon_state import DaemonInstanceLockV1
    monkeypatch.setattr(DaemonInstanceLockV1,'_pid_alive',staticmethod(lambda pid:False))
    import chanlun_trader.synthetic_batch_resources as resources
    monkeypatch.setattr(resources,'run_bounded_worker',lambda *a,**k:pytest.fail('must not run worker'))
    execute_accounts(root/'JOB.json',recover=True)
    assert read(root/'case_SETTLEMENT.json')['completed'] is True


def test_unknown_worker_is_not_restarted(tmp_path,monkeypatch):
    root=evidence(tmp_path)
    (root/'case_SETTLEMENT.json').unlink()
    (root/'case_RESOURCE.json').unlink()
    import chanlun_trader.synthetic_batch_resources as resources
    monkeypatch.setattr(resources,'run_bounded_worker',lambda *a,**k:pytest.fail('must not run worker'))
    with pytest.raises(PermissionError,match='UNKNOWN_WORKER'):
        execute_accounts(root/'JOB.json',recover=True)
    assert not (root/'case_SETTLEMENT.json').exists()


def test_public_binding_freezes_job_and_refuses_arbitrary_stages(tmp_path):
    import hashlib
    from chanlun_trader.research_factory.lifecycle_service_v2 import LifecycleServiceV2
    root=evidence(tmp_path)
    path=root/'JOB.json'
    binding={'account':{'kind':'PUBLIC_ACCOUNT','job_path':str(path),
        'job_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}}
    service=LifecycleServiceV2(tmp_path,binding)
    now=datetime.now(timezone.utc)
    with pytest.raises(ValueError,match='PUBLIC_STAGES_REQUIRED'):
        service.create_job('bad',binding_id='account',expires_at=(now+timedelta(hours=1)).isoformat(),
            max_calls=3,stages=[])
    reference=service._public_stage(binding['account'],{'key':'VERIFY'},recover=False)
    assert reference['advance_allowed']
    service._public_stage(binding['account'],{'key':'REPORT'},recover=False)
    assert (root/'case_REPORT.md').exists()
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError,match='PUBLIC_JOB_CHANGED'):
        service._public_stage(binding['account'],{'key':'REPORT'},recover=True)


def test_public_start_restores_reports_only_after_evidence_gate(tmp_path,monkeypatch):
    from chanlun_trader.research_factory.strategy_submission_v1 import StrategySubmissionV1
    from scripts import run_strategy_account_v1 as account
    task_id='a'*64
    task_root=tmp_path/task_id
    task_root.mkdir()
    root=evidence(task_root)
    write(task_root/'TASK.json',{'task_id':task_id,'job_path':str(root/'JOB.json')})
    service=StrategySubmissionV1(None,lambda _:None,tmp_path)
    original=account.report_account_job
    calls=[]
    def interrupted(path):
        calls.append('report')
        raise OSError('after verify before report')
    monkeypatch.setattr(account,'report_account_job',interrupted)
    with pytest.raises(OSError,match='after verify'):
        service.start(task_id)
    assert read(root/'VERIFICATION.json')['advance_allowed']
    before=(root/'case_SETTLEMENT.json').read_bytes()
    monkeypatch.setattr(account,'report_account_job',original)
    result=service.start(task_id)
    assert result['status']=='ACCOUNT_VERIFIED'
    assert not result['strategy_qualified']
    assert (root/'case_SETTLEMENT.json').read_bytes()==before


def test_public_start_does_not_report_failed_evidence(tmp_path,monkeypatch):
    from chanlun_trader.research_factory.strategy_submission_v1 import StrategySubmissionV1
    from scripts import run_strategy_account_v1 as account
    from chanlun_trader.research_factory import research_evidence_v1 as checks
    task_id='b'*64
    task_root=tmp_path/task_id
    task_root.mkdir()
    root=evidence(task_root)
    write(task_root/'TASK.json',{'task_id':task_id,'job_path':str(root/'JOB.json')})
    service=StrategySubmissionV1(None,lambda _:None,tmp_path)
    monkeypatch.setattr(checks,'verify_job_evidence',lambda *a,**k:{'status':'FAIL','advance_allowed':False,'reasons':['test denial']})
    monkeypatch.setattr(account,'report_account_job',lambda *a,**k:pytest.fail('failed evidence must not report'))
    assert service.start(task_id)['status']=='EVIDENCE_BLOCKED'
    assert not (root/'case_REPORT.md').exists()


def test_actual_bounded_worker_reports_controlled_launcher_and_child_pid(tmp_path):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    code=("import os,json; from chanlun_trader.synthetic_batch_resources import worker_resource_handshake; "
          "h=worker_resource_handshake(); print(json.dumps(dict(pid=os.getpid(),ppid=os.getppid(),handshake=h)))")
    started=[]
    result=run_bounded_worker([sys.executable,'-c',code],root=tmp_path,memory_mib=256,wall_seconds=20,
        on_started=started.append,execution={'purpose':'PID_TEST'},
        environment={**os.environ,'PYTHONDONTWRITEBYTECODE':'1',
            'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'src')+os.pathsep+os.environ.get('PYTHONPATH','')})
    assert result['returncode']==0,result['stderr'].decode(errors='replace')
    child=json.loads(result['stdout'])
    assert result['launcher_pid']==started[0]==child['handshake']['launcher_pid']
    assert child['pid']==started[0] or (os.name=='nt' and child['ppid']==started[0])
    if os.name=='nt':
        assert result['windows_job_bound'] and child['handshake']['windows_job_verified']


def test_host_stop_after_active_job_prevents_next_dispatch(tmp_path):
    service = empty_service(tmp_path)
    calls = []
    host = TrustedResearchHostV1(service)
    for name in ('first', 'second'):
        path = service.jobs.root / name / 'CONFIG.json'
        path.parent.mkdir(parents=True)
        path.write_text('{}')
    def dispatch(name):
        calls.append(name)
        host.stop()
        return {'status': 'COMPLETED'}
    service.jobs.tick = dispatch
    assert host.run(once=True)['status'] == 'STOPPED'
    assert calls == ['first']
