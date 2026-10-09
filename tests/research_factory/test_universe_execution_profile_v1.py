"""登记规格与同一用途 continuation 的真实持久化和预算反例。"""
from datetime import datetime, timedelta, timezone
import json

import pytest

from chanlun_trader.research_factory.budget import BudgetExhaustedError, SearchBudgetRegistryV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.run_budget import AutonomousRunBudgetV2
from chanlun_trader.research_factory.universe_execution_profile_v1 import (
    CONTINUOUS_PROFILE, ENGINEERING_PURPOSE, SEGMENTED_PROFILE,
    execution_profile, segment_allowance, segment_charge, validate_execution_profile,
)


def profile(sessions=252):
    return execution_profile(SEGMENTED_PROFILE,sessions)


def plans(resource=None):
    value={'strategy':{'strategy_id':'FIXED'},'runtime':{}}
    if resource is not None:
        value['runtime']['execution_profile']=resource
    value['plan_id']=stable_hash(value)
    return {'FIXED':value}


def governance(tmp_path,resource=None):
    frozen=plans(resource or profile())
    service=StrategyBatchGovernanceV1(tmp_path/'account',tmp_path/'budget.json','OBJECTIVE',frozen)
    source={'origin':'USER_EXPLICIT_CURRENT_TASK','statement':'固定用途测试',
        'approved_plan_ids':{'FIXED':frozen['FIXED']['plan_id']},
        'expires_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}
    inputs={'input_identity':'FROZEN','novelty':{'FIXED':{'allowed':True,'plan_id':frozen['FIXED']['plan_id']}}}
    service.confirm(source,inputs)
    service.start('FIXED')
    return service


def campaign_config(resource):
    return {'authorization_id':'long_campaign','objective_id':'OBJECTIVE',
        'resource_limits':{name:100000 for name in AutonomousRunBudgetV2.resource_names},
        'stages':['EXPLORATION'],'expires_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),
        'max_batches':2,'max_total_predictive_trials':4,'max_trials_per_batch':2,
        'max_hypotheses_per_batch':2,'max_candidates_per_batch':2,'execution_profiles':[resource]}


def campaign(tmp_path,resource=None,kind='ACCOUNT'):
    value=resource or profile()
    service=ResearchCampaignV1.create(tmp_path,campaign_config(value))
    units={'ACCOUNT':'account_jobs','DATA':'data_experiments','VERIFY':'verification_jobs'}
    service.reserve_operation(operation_id='operation',batch_id='batch',stage='EXPLORATION',kind=kind,
        subject_identity='FROZEN_RULE_INPUT_COST',upper_bounds={units[kind]:1,'wall_seconds':value['total_seconds']},
        execution_profile=value)
    service.start_operation('operation')
    return service


@pytest.mark.parametrize('sessions,total',[(1,14400),(252,14400),(253,28800),(504,28800)])
def test_registered_bounds_do_not_accept_user_timeout(sessions,total):
    value=profile(sessions)
    assert value['worker_seconds']==900 and value['total_seconds']==total
    assert (value['memory_mib'],value['threads'],value['concurrency'])==(2048,1,1)
    assert validate_execution_profile(value)==value
    changed={**value,'worker_seconds':999999}
    changed['profile_hash']=stable_hash({k:v for k,v in changed.items() if k!='profile_hash'})
    with pytest.raises(PermissionError,match='IDENTITY_CONFLICT'):
        validate_execution_profile(changed)


@pytest.mark.parametrize('sessions',[0,505,True,252.0,None])
def test_invalid_session_counts_are_rejected(sessions):
    with pytest.raises(ValueError,match='SESSION_BOUND'):
        execution_profile(SEGMENTED_PROFILE,sessions)


def test_continuous_reference_requires_engineering_purpose_and_authorization(tmp_path):
    with pytest.raises(PermissionError,match='PURPOSE_REQUIRED'):
        execution_profile(CONTINUOUS_PROFILE,504)
    value=execution_profile(CONTINUOUS_PROFILE,504,ENGINEERING_PURPOSE)
    assert value['worker_seconds']==value['total_seconds']==28800
    with pytest.raises(PermissionError,match='AUTHORIZATION_REQUIRED'):
        ResearchCampaignV1.create(tmp_path,campaign_config(value))
    config=campaign_config(value); config['engineering_authorization']=ENGINEERING_PURPOSE
    service=ResearchCampaignV1.create(tmp_path,config)
    assert service.status()['authorization']['execution_profiles']==[value]
    with pytest.raises(PermissionError,match='AUTHORIZATION_REQUIRED'):
        governance(tmp_path/'ordinary',value)


def test_unknown_profile_and_extra_fields_are_rejected():
    with pytest.raises(ValueError,match='NOT_REGISTERED'):
        execution_profile('arbitrary_timeout',252)
    with pytest.raises(PermissionError,match='IDENTITY_CONFLICT'):
        validate_execution_profile({**profile(),'skip_audit':True})


def test_pause_wait_has_no_compute_charge_but_expiry_advances():
    value=profile()
    assert segment_allowance(value,30,seconds_to_expiry=70000)==900
    assert segment_allowance(value,30,seconds_to_expiry=400)==400
    with pytest.raises(PermissionError,match='EXPIRED'):
        segment_allowance(value,30,seconds_to_expiry=0)
    with pytest.raises(PermissionError,match='RESOURCE_EXHAUSTED'):
        segment_allowance(value,14400,seconds_to_expiry=1000)
    assert segment_allowance(value,14350,seconds_to_expiry=1000)==50


@pytest.mark.parametrize('seconds',[float('nan'),float('inf'),-1,True])
def test_invalid_segment_measurement_is_not_zero_cost(seconds):
    with pytest.raises(ValueError,match='MEASUREMENT_REQUIRED'):
        segment_charge(seconds,900,'receipt')


def test_unknown_segment_is_charged_full_dispatch_bound():
    assert segment_charge(None,900,None)==(900,'UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND')
    with pytest.raises(ValueError,match='MEASUREMENT_REQUIRED'):
        segment_charge(1,900,None)
    with pytest.raises(PermissionError,match='EXCEEDS_DISPATCH_BOUND'):
        segment_charge(901,900,'real_resource_receipt')


def test_ten_segments_consume_one_account_start_and_settle_exact_sum(tmp_path):
    service=governance(tmp_path)
    for number in range(1,11):
        dispatch=service.start_segment('FIXED',segment_number=number)
        assert dispatch['upper_bound_seconds']==900
        service.end_segment('FIXED',number,seconds=number,evidence_identity='resource_'+str(number),
                            outcome='COMPLETED' if number==10 else 'CONTINUE')
    restored=StrategyBatchGovernanceV1(service.root,service.budget_path,service.objective_id,service.plans)
    state=restored.segment_status('FIXED')
    assert state['charged_seconds']==55 and len(state['segments'])==10
    budget=SearchBudgetRegistryV1(service.objective_id,service.budget_path)
    receipt=json.loads(service.receipt_path.read_text(encoding='utf-8'))
    assert budget.used(service.budget_kind,receipt['receipt_id']+':FIXED')==1
    with pytest.raises(PermissionError,match='CUMULATIVE_USAGE_CONFLICT'):
        service.settle('FIXED',completed=True,seconds=1,result_hash='result')
    service.settle('FIXED',completed=True,seconds=55,result_hash='result')
    with pytest.raises(PermissionError):
        restored.start_segment('FIXED')


def test_pending_segment_crash_is_not_free_and_restart_preserves_sequence(tmp_path):
    service=governance(tmp_path)
    service.start_segment('FIXED')
    restored=StrategyBatchGovernanceV1(service.root,service.budget_path,service.objective_id,service.plans)
    with pytest.raises(PermissionError,match='ALREADY_DISPATCHED'):
        restored.start_segment('FIXED')
    with pytest.raises(PermissionError,match='SETTLEMENT_REQUIRED'):
        restored.settle('FIXED',completed=False,seconds=0,result_hash=None,error='crash')
    charge=restored.end_segment('FIXED',1)
    assert charge['seconds']==900
    assert restored.segment_status('FIXED')['charged_seconds']==900
    with pytest.raises(PermissionError,match='NUMBER_CONFLICT'):
        restored.start_segment('FIXED',segment_number=1)
    assert restored.start_segment('FIXED')['segment_number']==2


def test_profile_tampering_and_charge_chain_tampering_fail_closed(tmp_path):
    service=governance(tmp_path)
    with pytest.raises(PermissionError,match='PROFILE_CONFLICT'):
        service.start_segment('FIXED',profile(504))
    service.start_segment('FIXED')
    service.end_segment('FIXED',1,seconds=100,evidence_identity='resource')
    path=service.root/'FIXED_SEGMENT_000001_CHARGE.json'
    value=json.loads(path.read_text(encoding='utf-8')); value['seconds']=0
    path.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises(PermissionError,match='CHARGE_CHAIN_CONFLICT'):
        service.segment_status('FIXED')


def test_unfinished_scope_cannot_reset_whole_account_budget(tmp_path):
    service=governance(tmp_path)
    service.start_segment('FIXED')
    with pytest.raises(PermissionError,match='ALREADY_ATTEMPTED'):
        service.start('FIXED')
    service.end_segment('FIXED',1,seconds=1,evidence_identity='resource',outcome='FAILED')
    with pytest.raises(PermissionError,match='TERMINAL_NO_REPLAY'):
        service.start_segment('FIXED')


def test_campaign_many_segments_stay_one_trial_and_do_not_double_count_reservation(tmp_path):
    service=campaign(tmp_path)
    for number in range(1,11):
        service.start_execution_segment('operation',segment_number=number)
        service.end_execution_segment('operation',segment_number=number,seconds=number,
            evidence_identity='resource_'+str(number),outcome='COMPLETED' if number==10 else 'CONTINUE')
    state=service.status()
    assert state['active_wall_seconds']==55
    assert state['reserved']['account_jobs']==1 and state['reserved']['wall_seconds']==14400
    assert state['used']['wall_seconds']==0 and state['remaining']['wall_seconds']==100000-14400
    assert state['trial_usage']['performance_accessed_trial_ids']==['operation']
    with pytest.raises(ValueError,match='ACTIVE_USAGE_CANNOT_BE_ERASED'):
        service.settle_operation('operation',actual={'account_jobs':1,'wall_seconds':54},
            outcome='COMPLETED',evidence_identity='audit')
    service.settle_operation('operation',actual={'account_jobs':1,'wall_seconds':55},
        outcome='COMPLETED',evidence_identity='audit')
    assert service.status()['used']['account_jobs']==1 and service.status()['used']['wall_seconds']==55
    service.budget_path.unlink()
    restored=ResearchCampaignV1(tmp_path,'long_campaign')
    assert restored.status()['used']==service.status()['used']
    assert restored.status()['trial_usage']['completed_trial_ids']==['operation']


@pytest.mark.parametrize('kind,purpose',[('DATA','RESEARCH_PREPARATION'),('VERIFY','RESEARCH_VERIFICATION'),
                                       ('VERIFY','RESEARCH_REPORT')])
def test_preparation_verification_and_reports_have_the_same_bounded_continuation(tmp_path,kind,purpose):
    value=execution_profile(SEGMENTED_PROFILE,252,purpose)
    service=campaign(tmp_path,value,kind)
    service.start_execution_segment('operation',segment_number=1)
    service.end_execution_segment('operation',segment_number=1,seconds=2,evidence_identity='receipt')
    assert service.status()['operations']['operation']['active_wall_seconds']==2


def test_campaign_unknown_crash_pause_expiry_and_final_settlement(tmp_path,monkeypatch):
    service=campaign(tmp_path)
    original=AutonomousRunBudgetV2._persist
    def crash(self):
        if self._events[-1]['event_type']=='CAMPAIGN_EXECUTION_SEGMENT_DISPATCHED':
            raise RuntimeError('after durable dispatch')
        original(self)
    with monkeypatch.context() as patch:
        patch.setattr(AutonomousRunBudgetV2,'_persist',crash)
        with pytest.raises(RuntimeError,match='durable dispatch'):
            service.start_execution_segment('operation',segment_number=1)
    restored=ResearchCampaignV1(tmp_path,'long_campaign')
    assert restored.status()['operations']['operation']['active_segment']['segment_number']==1
    restored.end_execution_segment('operation',segment_number=1)
    assert restored.status()['active_wall_seconds']==900
    restored.pause('temporary pause')
    with pytest.raises(PermissionError,match='PAUSED'):
        restored.start_execution_segment('operation',segment_number=2)
    assert restored.status()['active_wall_seconds']==900
    restored.resume('continue same authorization')
    class ExpiredClock(datetime):
        @staticmethod
        def now(tz): return datetime.now(timezone.utc)+timedelta(days=2)
    with monkeypatch.context() as patch:
        patch.setattr('chanlun_trader.research_factory.research_campaign_v1.datetime',ExpiredClock)
        with pytest.raises(BudgetExhaustedError,match='EXPIRED'):
            restored.start_execution_segment('operation',segment_number=2)
    # 到期阻断下一段，但仍允许把已经运行的成本结算进原用途。
    restored.settle_operation('operation',actual={'account_jobs':1,'wall_seconds':900},
        outcome='FAILED',evidence_identity='crash_receipt')


def test_campaign_profile_must_be_authorized_before_reservation(tmp_path):
    service=ResearchCampaignV1.create(tmp_path,campaign_config(profile()))
    other=profile(504)
    with pytest.raises(PermissionError,match='PROFILE_NOT_AUTHORIZED'):
        service.reserve_operation(operation_id='bad',batch_id='batch',stage='EXPLORATION',kind='ACCOUNT',
            subject_identity='different',upper_bounds={'account_jobs':1,'wall_seconds':other['total_seconds']},
            execution_profile=other)
    assert service.status()['operations']=={}


def test_campaign_account_governance_profiles_are_bound_to_same_operation(tmp_path):
    value=profile(); frozen=plans(value)
    service=ResearchCampaignV1.create(tmp_path,campaign_config(value))
    service.reserve_operation(operation_id='operation',batch_id='batch',stage='EXPLORATION',kind='ACCOUNT',
        subject_identity=frozen['FIXED']['plan_id'],upper_bounds={'account_jobs':1,'wall_seconds':14400},
        execution_profile=value)
    gov=StrategyBatchGovernanceV1(tmp_path/'account',tmp_path/'search.json','OBJECTIVE',frozen)
    gov.confirm_campaign_scope(service,{'FIXED':'operation'},
        {'input_identity':'FROZEN','novelty':{'FIXED':{'allowed':True,'plan_id':frozen['FIXED']['plan_id']}}})
    gov.start('FIXED')
    gov.start_segment('FIXED'); gov.end_segment('FIXED',1,seconds=3,evidence_identity='host_resource')
    assert service.status()['active_wall_seconds']==gov.segment_status('FIXED')['charged_seconds']==3
    assert service.status()['trial_usage']['performance_accessed_trial_ids']==['operation']
