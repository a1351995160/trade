"""公共提交边界：预览不读取行情；冻结不自行批准；配置不能携带插件。"""
from copy import deepcopy
from pathlib import Path

import pytest

from chanlun_trader.research_factory.strategy_submission_v1 import StrategySubmissionV1
from chanlun_trader.research_factory.research_rule_strategy_v3 import CAPABILITY, EXECUTION_MODE


def node(op, *args, **params):
    return {'op': op, 'args': list(args), 'params': params}


def request():
    rule = {'version':CAPABILITY, 'hypothesis':'测试提交', 'change_reason':'固定规则',
        'buy':node('gt',node('field','close'),node('const',value=10)),
        'sell':node('lt',node('field','close'),node('const',value=5)),
        'market_filter':None,'min_hold_sessions':0,'max_hold_sessions':20,
        'cooldown_sessions':0,'target_weight':.3,'indicator_instances':[],
        'exits':{'execution_mode':EXECUTION_MODE,'stop_loss_pct':None,'take_profit_pct':None,
                 'trailing_activate_pct':None,'trailing_pct':None}}
    return {'strategy_id':'SIMPLE','rule':rule,'dataset_id':'sample',
        'feature_start':20230102,'account_start':20230403,'account_end':20230410,
        'symbols':['000003.SZ','600004.SH'],'initial_cash':50000,'max_positions':2,
        'max_symbol_exposure_bps':5000,'costs':['BASE','STRESS'],'benchmark':'NONE',
        'purpose':'EXPLORATORY','authorization_ref':'registered-authorization'}


class Provider:
    def catalog(self):
        return {'datasets':[{'dataset_id':'sample','symbols':['000003.SZ','600004.SH'],
            'start':20230102,'end':20230430,'metadata_hash':'fixed'}]}

    def prepare(self, *args, **kwargs):
        raise AssertionError('market data must not be accessed')


def service(tmp_path):
    return StrategySubmissionV1(Provider(),lambda ref: (_ for _ in ()).throw(
        AssertionError('authority must not be accessed')),tmp_path)


def test_preview_is_deterministic_and_does_not_read_content(tmp_path):
    s = service(tmp_path)
    before = request()
    a = s.preview(before)
    b = s.preview(deepcopy(before))
    assert a['preview_identity'] == b['preview_identity']
    assert before == request()
    assert a['request']['initial_cash'] == 50000
    assert a['required_fields'] == ['close']
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('field,value', [('initial_cash',100000),('max_positions',1),('account_end',20230411)])
def test_parameter_change_invalidates_preview_before_content(tmp_path, field, value):
    s = service(tmp_path)
    r = request()
    frozen = s.preview(r)['preview_identity']
    r[field] = value
    with pytest.raises(ValueError,match='SUBMISSION_PREVIEW_CHANGED'):
        s.freeze(r,frozen)


@pytest.mark.parametrize('extra', ['factory','loader','dependency_files','qualified','judge','budget_path'])
def test_request_cannot_inject_execution_or_authority(tmp_path, extra):
    r = request()
    r[extra] = 'untrusted'
    with pytest.raises(ValueError,match='SUBMISSION_REQUEST_FIELDS_INVALID'):
        service(tmp_path).preview(r)


def test_unconnected_capability_blocks_before_authority_or_market(tmp_path):
    s = service(tmp_path)
    r = request()
    s.capabilities = lambda: {'features':[{'id':'indicator_rules','public_entry':False}]}
    with pytest.raises(ValueError,match='SUBMISSION_PUBLIC_CAPABILITY_NOT_CONNECTED'):
        s.freeze(r,s.preview(r)['preview_identity'])


def test_unknown_benchmark_not_silently_omitted(tmp_path):
    r = request()
    r['benchmark'] = 'UNREGISTERED_CONTROL'
    with pytest.raises(ValueError,match='SUBMISSION_BENCHMARK_UNSUPPORTED'):
        service(tmp_path).preview(r)


def test_unknown_pool_and_task_path_refused(tmp_path):
    s = service(tmp_path)
    r = request()
    r['symbols'].append('000005.SZ')
    with pytest.raises(ValueError,match='SUBMISSION_DATASET_OR_POOL_NOT_COVERED'):
        s.preview(r)
    with pytest.raises(ValueError,match='SUBMISSION_TASK_ID_INVALID'):
        s.status('../outside')


@pytest.mark.parametrize('symbols',[[],['000003.SZ','000003.SZ'],[{}],None,'000003.SZ'])
def test_bad_symbol_types_are_controlled_rejections(tmp_path,symbols):
    value = request()
    value['symbols'] = symbols
    with pytest.raises(ValueError,match='SUBMISSION_SYMBOLS_INVALID'):
        service(tmp_path).preview(value)


def connected():
    return {'features':[{'id':key,'public_entry':True} for key in
        ('indicator_rules','cost_stop','take_profit','trailing_stop')]}


def authority(tmp_path):
    return {'objective_id':'TEST_OBJECTIVE','budget_path':str(tmp_path / 'budget.json'),
            'data_authorization':{'authorization_id':'authorized'}}


def test_failed_prepare_cannot_be_repeated_to_reset_exposure(tmp_path):
    class Failing(Provider):
        calls = 0
        def prepare(self,*args,**kwargs):
            self.calls += 1
            raise ValueError('source failed')
    data = Failing()
    s = StrategySubmissionV1(data,lambda ref:authority(tmp_path),tmp_path / 'tasks',connected)
    value = request()
    identity = s.preview(value)['preview_identity']
    with pytest.raises(ValueError,match='source failed'):
        s.freeze(value,identity)
    with pytest.raises(ValueError,match='ALREADY_FROZEN_OR_INCOMPLETE'):
        s.freeze(value,identity)
    assert data.calls == 1
    assert len(list((tmp_path/'tasks').glob('*/FREEZE_FAILURE.json'))) == 1


def test_real_freeze_config_and_loader_roundtrip(tmp_path):
    import json
    import pandas as pd
    from chanlun_trader.research_factory.rule_account_backend_v2 import rule_input_identity
    from chanlun_trader.research_factory.strategy_submission_v1 import load_frozen_bundle
    dates = [int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2023-01-02','2023-04-10')]
    symbols = request()['symbols']
    window = {'symbols':symbols,'calendar':dates,'feature_start':dates[0],
              'account_start':20230403,'account_end':dates[-1]}
    bundle = {'profile':'HISTORICAL_MODELED','calendar':dates,'events':[],
        'corporate_actions_complete':True,'source_hashes':{'execution_profile':'HISTORICAL_MODELED','synthetic':'a'*64},
        'open_snapshots':[],'close_snapshots':[],
        'daily':pd.DataFrame([{'symbol':s,'date':d,'open':10.,'high':11.,'low':9.,'close':10.,
            'prev_close':10.,'volume':1000.,'amount':10000.,'adjustflag':'3'} for s in symbols for d in dates]),
        'turn':pd.DataFrame([{'symbol':s,'date':d,'volume':1000.,'turn':1.,'tradestatus':1} for s in symbols for d in dates]),
        'states':pd.DataFrame([{'symbol':s,'trade_date':d,'listed':True,'delisted':False,'universe_member':True,
            'eligibility_status':'ELIGIBLE','st_status':'NORMAL','suspension_status':'TRADING','board':'MAIN'}
            for s in symbols for d in dates if d>=20230403])}
    expected = rule_input_identity(bundle,window)
    class Prepared(Provider):
        def prepare(self,*args,**kwargs):
            return {'bundle':deepcopy(bundle),'window':window,'input_identity':expected,
                    'qualification':{'independent_confirmation_eligible':False}}
    s = StrategySubmissionV1(Prepared(),lambda ref:authority(tmp_path),tmp_path/'tasks',connected)
    value = request()
    result = s.freeze(value,s.preview(value)['preview_identity'])
    job = json.loads(Path(result['job_path']).read_text(encoding='utf-8'))
    assert len(job['plans']) == 2
    assert job['input_identity'] == expected
    for item in job['items'].values():
        loaded = load_frozen_bundle(**item['loader_kwargs'])
        assert loaded['input_identity'] == expected
        pd.testing.assert_frame_equal(loaded['frame']['daily'],bundle['daily'])
        assert item['backend_options']['initial_cash'] == 50000
        assert item['loader_kwargs']['path'] in item['source_hashes']
    assert not list(Path(result['job_path']).parent.glob('*CONFIRMATION*'))
    path = Path(next(iter(job['items'].values()))['loader_kwargs']['path'])
    with path.open('ab') as stream:
        stream.write(b' ')
    with pytest.raises(ValueError,match='SUBMISSION_SNAPSHOT_CHANGED'):
        load_frozen_bundle(**next(iter(job['items'].values()))['loader_kwargs'])


def approval_case(tmp_path):
    from datetime import datetime,timedelta,timezone
    from test_research_data_provider_v1 import dataset,provider
    args=dataset(tmp_path/'data')
    value=request()
    for key in ('feature_start','account_start','account_end'):
        value[key]=args[key]
    auth={'objective_id':'APPROVE_TEST','budget_path':str(tmp_path/'budget.json'),
          'data_authorization':args['authorization'],
          'source':{'origin':'USER_EXPLICIT_CURRENT_TASK','statement':'Synthetic fixed plan approval test'},
          'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
    s=StrategySubmissionV1(provider(tmp_path/'data',[]),lambda ref:deepcopy(auth),tmp_path/'tasks',connected)
    preview=s.preview(value)
    permit={key:preview['request'][key] for key in ('initial_cash','symbols','feature_start','account_start','account_end',
            'max_positions','max_symbol_exposure_bps','costs','benchmark')}
    auth['account_authorization']={**permit,'purpose':'FROZEN_PUBLIC_ACCOUNT_PLANS',
                                  'rule_identity':preview['rule_identity'],'max_account_jobs':2}
    task=s.freeze(value,preview['preview_identity'])
    return s,task,auth


def test_explicit_approve_reuses_original_receipt_without_budget_increase(tmp_path):
    from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
    s,task,auth=approval_case(tmp_path)
    shown=s.approval_preview(task['task_id'])
    assert shown['plan_ids']==task['plan_ids'] and len(shown['plan_ids'])==2
    first=s.approve(task['task_id'],task['preview_identity'])
    budget=SearchBudgetRegistryV1(auth['objective_id'],auth['budget_path'])
    before=budget.snapshot()['buckets']
    second=s.approve(task['task_id'],task['preview_identity'])
    assert first==second and budget.snapshot()['buckets']==before
    assert all(row['used']==0 for row in before)


@pytest.mark.parametrize('change',['cash','symbols','expiry','missing','max_jobs','rule'])
def test_account_approval_does_not_inherit_data_authorization(tmp_path,change):
    s,task,auth=approval_case(tmp_path)
    if change=='cash':auth['account_authorization']['initial_cash']=100000
    elif change=='symbols':auth['account_authorization']['symbols']=['000003.SZ']
    elif change=='expiry':auth['expires_at']='2000-01-01T00:00:00+00:00'
    elif change=='missing':auth.pop('account_authorization')
    elif change=='max_jobs':auth['account_authorization']['max_account_jobs']=1
    else:auth['account_authorization']['rule_identity']='f'*64
    with pytest.raises(PermissionError):
        s.approve(task['task_id'],task['preview_identity'])
    assert not (Path(task['job_path']).parent/'CONFIRMATION.json').exists()
