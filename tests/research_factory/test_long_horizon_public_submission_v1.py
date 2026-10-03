"""长期公共入口完整工程验收，合成原件不冒充真实市场或盈利证据。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory.universe_execution_profile_v1 import (
    CONTINUOUS_PROFILE, SEGMENTED_PROFILE, execution_profile,
)
from chanlun_trader.research_factory.universe_research_report_v2 import default_observation_plan
from test_universe_submission_v1 import public_universe_case


def long_public_case(tmp_path):
    service,request,authority,accesses=public_universe_case(tmp_path)
    request.update(version='FULL_UNIVERSE_SUBMISSION_V3',account_scope='DATA_QUALIFIED',
        execution_profile=execution_profile(SEGMENTED_PROFILE,20),
        observation_plan=default_observation_plan(request))
    authority['execution_profiles']=[execution_profile(SEGMENTED_PROFILE,20,purpose) for purpose in
        ('RESEARCH_ACCOUNT','RESEARCH_PREPARATION','RESEARCH_VERIFICATION','RESEARCH_REPORT')]
    authority['compute_authorization']={'preparation_jobs':1,'verification_jobs':1,'report_jobs':1}
    preview=service.preview(request)
    normalized=preview['request']
    authority['account_authorization']={'purpose':'FROZEN_PUBLIC_ACCOUNT_PLANS',
        'rule_identity':preview['rule_identity'],'max_account_jobs':2,
        **{key:deepcopy(normalized[key]) for key in ('initial_cash','symbols','feature_start','account_start','account_end',
            'max_positions','max_symbol_exposure_bps','costs','benchmark','execution_profile','observation_plan')}}
    return service,request,authority,accesses


def test_long_preview_is_metadata_only_and_has_exact_registered_profile(tmp_path):
    service,request,authority,accesses=long_public_case(tmp_path)
    service.authority=lambda ref:pytest.fail('预览不可访问授权')
    preview=service.preview(request)
    assert preview['request']['symbols']==['000001.SZ','300001.SZ','600000.SH']
    assert preview['request']['execution_profile']==request['execution_profile']
    assert accesses==[] and not Path(authority['budget_path']).exists()


@pytest.mark.parametrize('change',['continuous','profile','observation','symbols'])
def test_long_preview_rejects_scope_and_profile_widening(tmp_path,change):
    service,request,_,accesses=long_public_case(tmp_path)
    if change=='continuous':
        request['execution_profile']=execution_profile(CONTINUOUS_PROFILE,20,'ENGINEERING_CONTINUOUS_REFERENCE')
    elif change=='profile':
        request['execution_profile']['worker_seconds']=14400
    elif change=='observation':
        request['observation_plan']['horizons']=[1,5,10]
    else:
        request['symbols']=['000001.SZ']
    with pytest.raises((ValueError,PermissionError)):
        service.preview(request)
    assert accesses==[]


def test_long_freeze_requires_compute_authority_before_raw_data(tmp_path):
    service,request,authority,accesses=long_public_case(tmp_path)
    del authority['compute_authorization']
    with pytest.raises(PermissionError,match='UNIVERSE_COMPUTE_PROFILE_AND_BUDGET_AUTHORIZATION_REQUIRED'):
        service.freeze(request,service.preview(request)['preview_identity'])
    assert accesses==[] and not Path(authority['budget_path']).exists()


def test_long_public_bounded_workers_complete_both_costs_and_dual_reports(tmp_path):
    service,request,authority,_=long_public_case(tmp_path)
    preview=service.preview(request)
    frozen=service.freeze(request,preview['preview_identity'])
    root=Path(frozen['job_path']).parent
    job=json.loads(Path(frozen['job_path']).read_text(encoding='utf-8'))
    assert job['resources']==request['execution_profile']
    assert all(plan['backend']['backend']=='UNIVERSE_ACCOUNT_BACKEND_V2' for plan in job['plans'].values())
    service.approve(frozen['task_id'],preview['preview_identity'])
    completed=service.start(frozen['task_id'])
    assert completed['status']=='ACCOUNT_VERIFIED',completed.get('verification')
    assert completed['verification']['advance_allowed'] is True
    assert completed['strategy_qualified'] is False
    for name in job['plans']:
        report=json.loads((root/(name+'_RESEARCH_REPORT.json')).read_text(encoding='utf-8'))
        funnel=json.loads((root/(name+'_SIGNAL_FUNNEL.json')).read_text(encoding='utf-8'))
        assert report['strategy_qualified'] is False and report['paper_qualified'] is False
        assert report['input_identity']==job['input_identity']
        assert funnel['identity']
        assert (root/(name+'_REPORT.md')).is_file()
        assert len(list(root.glob(name+'_START.json')))==1
    budget=json.loads(Path(authority['budget_path']).read_text(encoding='utf-8'))
    assert len(budget['settled_reservations'])==5
    assert set(budget['settled_reservations'].values())=={'CONSUMED'}
    repeated=service.start(frozen['task_id'])
    assert repeated['status']=='ACCOUNT_VERIFIED'
    after=json.loads(Path(authority['budget_path']).read_text(encoding='utf-8'))
    assert after['settled_reservations']==budget['settled_reservations']
    assert after['buckets']==budget['buckets']


def test_long_profile_requires_actual_calendar_session_count(tmp_path):
    service,request,authority,_=long_public_case(tmp_path)
    request['execution_profile']=execution_profile(SEGMENTED_PROFILE,19)
    authority['execution_profiles']=[execution_profile(SEGMENTED_PROFILE,19,purpose) for purpose in
        ('RESEARCH_ACCOUNT','RESEARCH_PREPARATION','RESEARCH_VERIFICATION','RESEARCH_REPORT')]
    with pytest.raises(ValueError,match='UNIVERSE_ACCOUNT_INPUT_NOT_READY:SCAN_BLOCKED'):
        service.freeze(request,service.preview(request)['preview_identity'])


def test_public_v4_score_dispatch_is_explicit_and_old_full_protocol_rejects_it(tmp_path):
    from test_research_rule_strategy_v4 import payload
    service,request,_,accesses=long_public_case(tmp_path)
    request['rule']=payload()
    preview=service.preview(request)
    assert preview['actual_rule']['rule']['selection']['direction']=='ASCENDING'
    assert preview['required_warmup_bars']==101 and accesses==[]
    request['version']='FULL_UNIVERSE_SUBMISSION_V2'
    del request['execution_profile'];del request['observation_plan']
    with pytest.raises(ValueError,match='UNIVERSE_LEGACY_RULE_VERSION_REQUIRED'):
        service.preview(request)


def test_pause_before_start_preserves_original_account_budget(tmp_path):
    from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
    from scripts.run_strategy_account_v1 import control_job
    service,request,authority,_=long_public_case(tmp_path)
    preview=service.preview(request)
    frozen=service.freeze(request,preview['preview_identity'])
    service.approve(frozen['task_id'],preview['preview_identity'])
    budget=SearchBudgetRegistryV1(authority['objective_id'],authority['budget_path']).snapshot()
    assert len(budget['settled_reservations'])==1
    paused=service.pause(frozen['task_id'])
    assert paused['status']=='PAUSE_REQUESTED'
    assert service.start(frozen['task_id'])['status']=='PAUSED'
    after=SearchBudgetRegistryV1(authority['objective_id'],authority['budget_path']).snapshot()
    assert after['settled_reservations']==budget['settled_reservations']
    assert after['buckets']==budget['buckets']
    assert service.status(frozen['task_id'])['completed']==0
    state=control_job(frozen['job_path'],'RESUME')
    assert state['paused'] is False


def test_long_publication_cli_uses_same_read_only_reader_without_deployment(monkeypatch, capsys):
    from scripts import run_trusted_research_v1 as cli
    from chanlun_trader.research_factory import research_capabilities_v1 as catalog
    expected = {'status': 'NOT_ACCEPTED', 'strategy_qualified': False, 'feature_ids': []}
    monkeypatch.setattr(catalog, 'published_long_horizon_acceptance', lambda: expected)
    monkeypatch.setattr(cli, 'build_submission_service', lambda *args: pytest.fail('发布查询不能加载部署或数据'))
    assert cli.main(['publication', '--long-horizon']) == 0
    assert json.loads(capsys.readouterr().out) == expected
