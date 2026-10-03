"""宿主在父事件与本地镜像之间死亡，只补镜像，不重复收费或执行。"""
from copy import deepcopy
import json

import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.universe_compute_governance_v1 import UniverseComputeGovernanceV1
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE,execution_profile
from test_universe_compute_governance_v1 import meter
from test_universe_execution_profile_v1 import campaign_config,plans,profile


def account_campaign(tmp_path):
    resource=profile();frozen=plans(resource)
    (tmp_path/'parent').mkdir()
    campaign=ResearchCampaignV1.create(tmp_path/'parent',campaign_config(resource))
    campaign.reserve_operation(operation_id='account',batch_id='batch',stage='EXPLORATION',kind='ACCOUNT',
        subject_identity=frozen['FIXED']['plan_id'],upper_bounds={'account_jobs':1,'wall_seconds':14400},execution_profile=resource)
    gov=StrategyBatchGovernanceV1(tmp_path/'account',tmp_path/'budget.json','OBJECTIVE',frozen)
    gov.confirm_campaign_scope(campaign,{'FIXED':'account'},{'input_identity':'FROZEN',
        'novelty':{'FIXED':{'allowed':True,'plan_id':frozen['FIXED']['plan_id']}}})
    gov.start('FIXED')
    return gov,campaign


def crash_mirror(monkeypatch,module,suffix):
    import importlib
    target=importlib.import_module(module);original=target.immutable
    def injected(path,value):
        if path.name.endswith(suffix):raise RuntimeError('HOST_CRASH_AFTER_PARENT_BEFORE_LOCAL')
        return original(path,value)
    monkeypatch.setattr(target,'immutable',injected)


def test_account_parent_dispatch_and_charge_reconstruct_exact_same_local_ids(tmp_path,monkeypatch):
    gov,campaign=account_campaign(tmp_path)
    with monkeypatch.context() as patch:
        crash_mirror(patch,'chanlun_trader.research_factory.etf_account_governance_v1','_DISPATCH.json')
        with pytest.raises(RuntimeError,match='HOST_CRASH'):
            gov.start_segment('FIXED')
    before=deepcopy(campaign.status())
    restored=gov.reconcile_segment_mirrors('FIXED')
    assert restored['pending']['segment_number']==1
    assert campaign.status()['operations']==before['operations']
    dispatch=deepcopy(restored['pending'])
    with monkeypatch.context() as patch:
        crash_mirror(patch,'chanlun_trader.research_factory.etf_account_governance_v1','_CHARGE.json')
        with pytest.raises(RuntimeError,match='HOST_CRASH'):
            gov.end_segment('FIXED',1,seconds=42,evidence_identity='resource')
    restored=gov.reconcile_segment_mirrors('FIXED')
    assert restored['charged_seconds']==42 and restored['pending'] is None
    assert restored['segments'][0]['dispatch']==dispatch
    assert campaign.status()['trial_usage']['performance_accessed_trial_ids']==['account']
    assert campaign.status()['reserved']['account_jobs']==1
    assert gov.reconcile_segment_mirrors('FIXED')==restored
    assert gov.start_segment('FIXED')['segment_number']==2


def test_parent_proof_cannot_overwrite_conflicting_local_dispatch(tmp_path):
    gov,_=account_campaign(tmp_path);gov.start_segment('FIXED')
    path=gov.root/'FIXED_SEGMENT_000001_DISPATCH.json'
    record=json.loads(path.read_text(encoding='utf-8'));record['upper_bound_seconds']=500
    record['dispatch_id']=stable_hash({k:v for k,v in record.items() if k!='dispatch_id'})
    path.write_text(json.dumps(record),encoding='utf-8')
    with pytest.raises((ValueError,PermissionError)):
        gov.reconcile_segment_mirrors('FIXED')


@pytest.mark.parametrize('failure',['local_charge','parent_settlement'])
def test_compute_parent_commits_repair_without_duplicate_data_trial(tmp_path,monkeypatch,failure):
    first=meter(tmp_path,'PREPARATION');auth=deepcopy(first.authority)
    resource=execution_profile(SEGMENTED_PROFILE,252,'RESEARCH_PREPARATION')
    config=campaign_config(resource);config['objective_id']='FIXED'
    (tmp_path/'parent').mkdir()
    campaign=ResearchCampaignV1.create(tmp_path/'parent',config)
    auth['account_authorization']={'campaign_ref':{'root':str(campaign.root),'authorization_id':campaign.authorization_id}}
    compute=meter(tmp_path,'PREPARATION',authority=auth);compute.start()
    with monkeypatch.context() as patch:
        crash_mirror(patch,'chanlun_trader.research_factory.universe_compute_governance_v1','_DISPATCH.json')
        with pytest.raises(RuntimeError,match='HOST_CRASH'):
            compute.dispatch()
    restored=compute.reconcile_segment_mirrors()
    assert restored['pending']['number']==1
    with monkeypatch.context() as patch:
        if failure=='local_charge':
            crash_mirror(patch,'chanlun_trader.research_factory.universe_compute_governance_v1','_CHARGE.json')
        else:
            patch.setattr(ResearchCampaignV1,'settle_operation',lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('HOST_CRASH')))
        with pytest.raises(RuntimeError,match='HOST_CRASH'):
            compute.charge(1,seconds=7,evidence_identity='resource',outcome='COMPLETED')
    restored=compute.reconcile_segment_mirrors()
    assert restored['charged_seconds']==7 and restored['pending'] is None
    assert campaign.status()['used']['data_experiments']==1
    assert campaign.status()['used']['wall_seconds']==7
    assert len(campaign.status()['trial_usage']['completed_trial_ids'])==1
    assert compute.reconcile_segment_mirrors()==restored
