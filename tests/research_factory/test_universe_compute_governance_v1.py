"""准备、核验、报告分别授权，但段数不能重置同一个用途预算。"""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json

import pytest

from chanlun_trader.research_factory.budget import BudgetExhaustedError,SearchBudgetRegistryV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.universe_compute_governance_v1 import UniverseComputeGovernanceV1
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE,execution_profile


def meter(tmp_path,stage='REPORT',*,root='compute',authority=None):
    auth=authority or {'objective_id':'FIXED','budget_path':str(tmp_path/'budget.json'),
        'expires_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),
        'source':{'origin':'USER_EXPLICIT_CURRENT_TASK','statement':'三个计算用途各一次'},
        'compute_authorization':{'preparation_jobs':1,'verification_jobs':1,'report_jobs':1},
        'execution_profiles':[execution_profile(SEGMENTED_PROFILE,252,p) for p in
            ('RESEARCH_PREPARATION','RESEARCH_VERIFICATION','RESEARCH_REPORT')]}
    request={'authorization_ref':'same_scope','execution_profile':execution_profile(SEGMENTED_PROFILE,252)}
    return UniverseComputeGovernanceV1(tmp_path/root,auth,request,stage)


def test_each_stage_consumes_once_and_continuation_never_recharges_trials(tmp_path):
    report=meter(tmp_path);start=report.start()
    for seconds in (100,20):
        segment=report.dispatch()
        report.charge(segment['number'],seconds=seconds,evidence_identity='a'*64)
    third=report.dispatch()
    report.charge(third['number'],seconds=None,outcome='COMPLETED')
    state=report.status()
    assert state['charged_seconds']==1020
    assert state['segments'][-1]['charge']['basis']=='UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND'
    assert report.start()==start
    with pytest.raises(PermissionError,match='PENDING_OR_TERMINAL'):
        report.dispatch()
    prepare=meter(tmp_path,'PREPARATION',root='prepare',authority=report.authority);prepare.start()
    budget=SearchBudgetRegistryV1('FIXED',report.budget_path).snapshot()
    assert len(budget['settled_reservations'])==2
    assert set(budget['settled_reservations'].values())=={'CONSUMED'}


def test_renaming_report_root_cannot_reset_same_scope_quota(tmp_path):
    first=meter(tmp_path);first.start()
    second=meter(tmp_path,root='renamed',authority=first.authority)
    with pytest.raises(BudgetExhaustedError):
        second.start()


def test_expiry_is_absolute_while_pause_does_not_create_compute_charge(tmp_path):
    first=meter(tmp_path);first.start()
    assert first.status()['charged_seconds']==0
    first.binding['expires_at']='2000-01-01T00:00:00+00:00'
    with pytest.raises(PermissionError,match='AUTHORIZATION_EXPIRED'):
        first.dispatch()


def test_changed_authority_and_unproven_seconds_rejected(tmp_path):
    first=meter(tmp_path);first.start();dispatch=first.dispatch()
    with pytest.raises(ValueError):
        first.charge(dispatch['number'],seconds=1)
    changed=deepcopy(first.authority);changed['compute_authorization']['report_jobs']=2
    with pytest.raises(PermissionError,match='IDENTITY_CONFLICT'):
        meter(tmp_path,authority=changed).status()


def test_rehashed_resource_widening_or_orphan_charge_is_not_accepted(tmp_path):
    first=meter(tmp_path);first.start();first.dispatch()
    path=first.root/'COMPUTE_SEGMENT_000001_DISPATCH.json'
    value=json.loads(path.read_text(encoding='utf-8'));value['upper_bound_seconds']=14400
    value['dispatch_id']=stable_hash({k:v for k,v in value.items() if k!='dispatch_id'})
    path.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises(PermissionError,match='SEGMENT_CHAIN_CONFLICT'):
        first.status()
