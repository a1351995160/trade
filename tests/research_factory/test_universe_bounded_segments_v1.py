"""单步边界统计实际受限 worker 调用；使用隔离合成资料。"""
from pathlib import Path

import pytest

from chanlun_trader.research_factory import universe_scan_service_v1 as scan
from chanlun_trader.research_factory.universe_compute_governance_v1 import UniverseComputeGovernanceV1
from test_long_horizon_public_submission_v1 import long_public_case


def preparation_case(tmp_path):
    service,request,authority,_=long_public_case(tmp_path)
    preview=service.preview(request)
    return service,request,authority,preview


def test_begin_and_each_preparation_advance_dispatch_at_most_one_real_worker(tmp_path,monkeypatch):
    service,request,_,preview=preparation_case(tmp_path)
    actual=scan.run_bounded_worker;dispatches=[]
    def observe(command,**kwargs):
        dispatches.append(kwargs['execution']['phase'])
        return actual(command,**kwargs)
    monkeypatch.setattr(scan,'run_bounded_worker',observe)
    begun=scan.begin_universe_preparation(service,request,preview['preview_identity'])
    assert not begun['complete'] and begun['next_phase']=='PREPARE'
    assert begun['dispatched_segments']==0 and dispatches==[]
    root=Path(begun['root'])
    assert not (root/'COMPUTE'/'COMPUTE_START.json').exists()
    first=scan.advance_universe_preparation(service,request,preview['preview_identity'])
    assert not first['complete'] and first['next_phase']=='QUALIFY'
    assert first['dispatched_segments']==1 and dispatches==['PREPARE']
    assert not (root/'SCAN_RECEIPT.json').exists()
    with pytest.raises(PermissionError,match='NOT_COMPLETED'):
        scan.finalize_universe_preparation(service,first)
    second=scan.advance_universe_preparation(service,request,preview['preview_identity'])
    assert second['complete'] and second['next_phase'] is None
    assert second['dispatched_segments']==1 and dispatches==['PREPARE','QUALIFY']
    frozen_input=scan.finalize_universe_preparation(service,second)
    assert frozen_input['input_identity']==second['result']['input_identity']
    repeated=scan.advance_universe_preparation(service,request,preview['preview_identity'])
    assert repeated['complete'] and repeated['dispatched_segments']==0
    frozen=service.freeze(request,preview['preview_identity'])
    assert Path(frozen['job_path']).is_file() and dispatches==['PREPARE','QUALIFY']


def test_account_verification_and_report_steps_count_actual_worker_dispatches(tmp_path,monkeypatch):
    from scripts import run_strategy_account_v1 as runner
    import chanlun_trader.synthetic_batch_resources as resources
    service,request,authority,preview=preparation_case(tmp_path)
    frozen=service.freeze(request,preview['preview_identity'])
    service.approve(frozen['task_id'],preview['preview_identity'])
    path=frozen['job_path'];job=runner.read_json(path)
    actual=resources.run_bounded_worker;dispatches=[]
    def observe(command,**kwargs):
        dispatches.append(dict(kwargs['execution']))
        return actual(command,**kwargs)
    monkeypatch.setattr(resources,'run_bounded_worker',observe)
    for _ in range(8):
        before=len(dispatches)
        result=runner.execute_long_horizon_accounts(path,recover=True,step=True)
        assert len(dispatches)-before==result['dispatched_segments']<=1
        if result['status']=='COMPLETED':break
        assert result['status']=='CONTINUE'
    else:pytest.fail('隔离短账户未在预计段数内结束')
    names=list(job['plans'])
    assert [row['purpose'] for row in dispatches]==names
    assert (Path(job['root'])/'RESULTS_INDEX.json').is_file()
    for stage in ('VERIFICATION','REPORT'):
        for _ in range(12):
            before=len(dispatches)
            result=runner.run_long_horizon_compute(path,authority,preview['request'],stage,step=True)
            assert len(dispatches)-before==result['dispatched_segments']<=1
            if result['status']=='COMPLETED':break
            assert result['status']=='CONTINUE'
        else:pytest.fail(stage+'隔离任务未在预计段数内结束')
        before=len(dispatches)
        cached=runner.run_long_horizon_compute(path,authority,preview['request'],stage,step=True)
        assert cached['status']=='COMPLETED' and cached['dispatched_segments']==0
        assert len(dispatches)==before
        if stage=='VERIFICATION':
            runner.save(Path(job['root'])/'VERIFICATION.json',cached['result'])
    report_dispatches=[row for row in dispatches if row['purpose']=='REPORT']
    assert {row['member'] for row in report_dispatches}==set(names)
    assert all(runner.service(job).segment_status(name)['pending'] is None for name in names)
    budget=runner.read_json(authority['budget_path'])
    assert len(budget['settled_reservations'])==5


def test_unknown_account_and_compute_segment_are_not_redispatched(tmp_path,monkeypatch):
    from scripts import run_strategy_account_v1 as runner
    import chanlun_trader.synthetic_batch_resources as resources
    service,request,authority,preview=preparation_case(tmp_path)
    frozen=service.freeze(request,preview['preview_identity'])
    service.approve(frozen['task_id'],preview['preview_identity'])
    job=runner.read_json(frozen['job_path']);governance=runner.service(job)
    name=next(iter(job['plans']))
    governance.start(name);governance.start_segment(name)
    monkeypatch.setattr(resources,'run_bounded_worker',lambda *a,**k:pytest.fail('未知账户不能重派'))
    with pytest.raises(PermissionError,match='INTERRUPTED_USE_RESUME'):
        runner.execute_long_horizon_accounts(frozen['job_path'],recover=True,step=True)
    assert governance.segment_status(name)['pending'] is not None
    root=Path(job['root'])/'COMPUTE_VERIFICATION'
    runner.save(root/'SCOPE.json',{'job_sha256':runner.sha(frozen['job_path']),
                                'authority':authority,'request':preview['request'],'stage':'VERIFICATION'})
    meter=UniverseComputeGovernanceV1(root,authority,preview['request'],'VERIFICATION')
    meter.start();meter.dispatch()
    with pytest.raises(PermissionError,match='INTERRUPTED_RECONCILIATION_REQUIRED'):
        runner.run_long_horizon_compute(frozen['job_path'],authority,preview['request'],'VERIFICATION',step=True)
    assert meter.status()['pending'] is not None


def test_unknown_preparation_segment_keeps_same_dispatch_without_worker(tmp_path,monkeypatch):
    service,request,authority,preview=preparation_case(tmp_path)
    begun=scan.begin_universe_preparation(service,request,preview['preview_identity'])
    meter=UniverseComputeGovernanceV1(Path(begun['root'])/'COMPUTE',authority,preview['request'],'PREPARATION')
    meter.start();pending=meter.dispatch()
    monkeypatch.setattr(scan,'run_bounded_worker',lambda *a,**k:pytest.fail('未知准备不能重派'))
    with pytest.raises(PermissionError,match='INTERRUPTED_RECONCILIATION_REQUIRED'):
        scan.advance_universe_preparation(service,request,preview['preview_identity'])
    assert meter.status()['pending']==pending and len(meter.status()['segments'])==1
