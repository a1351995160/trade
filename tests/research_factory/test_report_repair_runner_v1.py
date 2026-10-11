"""报告修复只隔离报告输出，不改变原账户、核验和普通入口。"""
import json
from types import SimpleNamespace

import pytest

from scripts import run_strategy_account_v1 as runner
from chanlun_trader.research_factory.business_validation_protocol_v1 import BusinessValidationProtocolV1
from test_long_horizon_report_input_reuse_v1 import final_report_worker


def test_repair_reference_cannot_enter_verification_or_account(tmp_path):
    for stage in ('VERIFICATION','ACCOUNT','PREPARATION'):
        with pytest.raises(PermissionError,match='REPORT_REPAIR_STAGE_REQUIRED'):
            runner.report_repair_context(tmp_path/'JOB.json',stage,{'path':'unused','sha256':'0'*64})
    assert not list(tmp_path.iterdir())


def test_unapproved_output_context_cannot_render_report(tmp_path,monkeypatch):
    path,member,_,inputs,_=final_report_worker(tmp_path,monkeypatch)
    job=runner.read_json(path)
    index=runner.read_json(path.parent/'RESULTS_INDEX.json')['items']
    with pytest.raises(PermissionError,match='REPORT_REPAIR_CONTEXT_REQUIRED'):
        runner.write_reports(job,index,_universe_inputs=inputs,
            _report_repair=SimpleNamespace(output_root=tmp_path/'spoofed'))
    assert not (tmp_path/'spoofed').exists()


def test_report_context_keeps_source_result_and_settlement_original(tmp_path,monkeypatch):
    # 此例仅测试已验证上下文的路径传递；真实Owner/计费边界在协议集成测试中验证。
    path,member,_,inputs,_=final_report_worker(tmp_path,monkeypatch)
    job=runner.read_json(path)
    index=runner.read_json(path.parent/'RESULTS_INDEX.json')['items']
    original_index=(path.parent/'RESULTS_INDEX.json').read_bytes()
    from chanlun_trader.research_factory.report_repair_protocol_v1 import ReportRepairV1
    context=object.__new__(ReportRepairV1)
    context.output_root=tmp_path/'separate-report';context.output_root.mkdir()
    runner.save(context.output_root/(member+'_RESEARCH_REPORT.json'),{'details_manifest':{}})
    runner.save(context.output_root/(member+'_SIGNAL_FUNNEL.json'),{'details_manifest':{}})
    runner.write_reports(job,index,_universe_inputs=inputs,_report_repair=context)
    rendered=json.loads((context.output_root/(member+'_REPORT.json')).read_text(encoding='utf-8'))
    assert rendered['artifacts']['source_result']['path']==str(path.parent/(member+'_RESULT.json'))
    assert (context.output_root/(member+'_REPORT.md')).is_file()
    assert not (path.parent/(member+'_REPORT.json')).exists()
    assert (path.parent/'RESULTS_INDEX.json').read_bytes()==original_index
    assert (context.output_root/'REPORT_ACCESS.json').is_file()


def test_repair_report_verifier_rejects_duck_typed_context_before_read(tmp_path):
    with pytest.raises(ValueError,match='REPORT_REPAIR_CONTEXT_REQUIRED'):
        BusinessValidationProtocolV1.verify_report_refs({'task_id':'task','job_path':str(tmp_path/'JOB.json')},
            {'task_id':'task','status':'ACCOUNT_VERIFIED','verification':{'advance_allowed':True}},
            minimum_sessions=504,_report_repair=SimpleNamespace(output_root=tmp_path/'spoofed'))


@pytest.mark.parametrize('render_fails',[False,True])
def test_repaired_final_reports_are_anchored_only_after_successful_worker_render(tmp_path,monkeypatch,render_fails):
    path,member,observed,_,_=final_report_worker(tmp_path,monkeypatch)
    from chanlun_trader.research_factory.report_repair_protocol_v1 import ReportRepairV1
    from chanlun_trader.research_factory.universe_account_backend_v2 import SegmentBoundary
    from chanlun_trader.research_factory.universe_compute_governance_v1 import UniverseComputeGovernanceV1
    context=object.__new__(ReportRepairV1)
    context.output_root=tmp_path/'separate-report'
    context.compute_root=context.output_root/'COMPUTE_REPORT'
    context.repair_id='FIXED_SYNTHETIC_REPAIR'
    reference={'path':'SYNTHETIC','sha256':'0'*64}
    scope=runner.read_json(path.parent/'COMPUTE_REPORT/SCOPE.json')
    scope['repair_ref']=reference
    runner.save(context.compute_root/'SCOPE.json',scope)
    meter=UniverseComputeGovernanceV1(None,None,None,None)
    context.meter=lambda:meter
    monkeypatch.setattr(runner,'report_repair_context',lambda *args,**kwargs:context)
    monkeypatch.setattr(runner,'validate_sources',lambda *args,**kwargs:None)
    runner.HANDSHAKE['execution']['scope_sha256']=runner.sha(context.compute_root/'SCOPE.json')
    if render_fails:
        def render(*args,**kwargs):
            raise SegmentBoundary('SYNTHETIC_RENDER_BOUNDARY')
        monkeypatch.setattr(runner,'report_account_job',render)
    assert runner._long_horizon_compute_worker(path,'REPORT',1,member,_report_repair_ref=reference)==(75 if render_fails else 0)
    state=runner.read_json(context.compute_root/'SEGMENT_000001_STATUS.json')
    manifest=context.compute_root/'FINAL_REPORTS.json'
    assert observed['loader_calls']==1
    if render_fails:
        assert not manifest.exists() and 'final_reports_sha256' not in state
        assert state['state']=='CONTINUE'
    else:
        assert state['final_reports_sha256']==runner.sha(manifest)
        assert runner.read_json(manifest)=={'job_sha256':runner.sha(path),'repair_id':context.repair_id,
            'reports':{member:{'final_report':str(context.output_root/(member+'_REPORT.json')),
                'final_report_sha256':runner.sha(context.output_root/(member+'_REPORT.json'))}}}
        assert not (path.parent/'FINAL_REPORTS.json').exists()
