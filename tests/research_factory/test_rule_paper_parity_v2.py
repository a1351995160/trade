"""同一规则经公共回测与持久 Paper 入口的合成对照，不增加真实观察日。"""
from copy import deepcopy

import pandas as pd

from chanlun_trader.research_factory.bounded_research_v2 import BoundedResearchSessionV2
from chanlun_trader.research_factory.forward_paper_v1 import ForwardPaperSessionV1
from chanlun_trader.research_factory.forward_snapshot_v1 import SnapshotStoreV1
from chanlun_trader.research_factory.research_rule_strategy_v2 import CAPABILITY, ResearchRuleStrategyV2
from chanlun_trader.research_factory.rule_account_backend_v2 import RuleAccountBackendV2, rule_input_identity
from chanlun_trader.research_factory.strategy_interface_v1 import prepare, run
from chanlun_trader.research_factory.strategy_qualification_v1 import BoundedStrategyArchiveV1
from test_bounded_rule_loop_v2 import SyntheticInvoker
from test_historical_process_v1 import historical_fixture
from test_research_rule_strategy_v2 import payload


def test_public_rule_backtest_and_persistent_paper_match_and_resume(tmp_path):
    window, bundle = historical_fixture()
    identity = rule_input_identity(bundle, window)
    bundle['input_identity'] = identity
    research = BoundedResearchSessionV2.create(tmp_path/'research', objective_id='PARITY_SYNTHETIC',
        input_manifest={'profile':'SYNTHETIC', 'input_identity':identity, 'window':window,
                        'candidate_capability':CAPABILITY},
        approval_statement='合成跨入口验证', max_attempts=1)
    status = research.run(loader=lambda *_: (deepcopy(bundle), []), invoker=SyntheticInvoker([payload()]))
    assert status['status'] == 'ATTEMPT_BUDGET_EXHAUSTED', status
    archive = BoundedStrategyArchiveV1(tmp_path/'archive')
    frozen = archive.freeze(research.root, 'CANDIDATE_001')
    key = frozen['strategy_id']
    strategy = ResearchRuleStrategyV2(frozen['proposal'], strategy_id=key)
    backend = RuleAccountBackendV2(window)
    plan = prepare(strategy, backend)
    receipt = {'strategy_plans':{key:plan}, 'input_identity':identity,
               'novelty':{key:{'allowed':True}}, 'execution_purpose':key, 'execution_consumed':True}
    result = run(strategy, backend, frame=bundle, actions=[], input_identity=identity, active_check=lambda: receipt)
    days = [day for day in window['calendar'] if day >= window['account_start']]
    next_day = int((pd.Timestamp(str(days[-1])) + pd.offsets.BDay()).strftime('%Y%m%d'))
    clock = [pd.Timestamp(str(days[0]), tz='Asia/Shanghai') + pd.Timedelta(hours=14)]
    warmup = {'bars':bundle['daily'].loc[bundle['daily'].date < days[0]].to_dict('records'),
              'turn':bundle['turn'].loc[bundle['turn'].date < days[0]].to_dict('records'),
              'states':[], 'corporate_actions':[], 'corporate_actions_complete':True,
              'source_profile':'SYNTHETIC'}
    policy = {'initial_cash':1_000_000, 'symbols':window['symbols'],
              'open_delay_minutes':5, 'close_delay_minutes':120,
              'portfolio':{'policy_id':'PARITY_SYNTHETIC', 'members':[{'strategy_id':key,
                  'rule_identity':frozen['rule_identity'], 'weight_bps':10000, 'priority':0}],
                  'purpose':'ENGINEERING_OBSERVATION', 'max_positions':2,
                  'max_symbol_exposure_bps':5000, 'max_buy_turnover_bps':10000,
                  'valid_until':(pd.Timestamp(str(next_day),tz='Asia/Shanghai')+pd.Timedelta(days=2)).isoformat()}}
    paper = ForwardPaperSessionV1.create(tmp_path/'paper', archive_root=archive.root,
        strategy_ids=[key], policy=policy, calendar=[*days,next_day], warmup=warmup,
        profile='SYNTHETIC', clock=lambda:clock[0])
    snapshots = SnapshotStoreV1(tmp_path/'snapshots')
    prior = {r['symbol']:r for r in warmup['bars'] if r['date'] == window['calendar'][window['calendar'].index(days[0])-1]}
    for index, day in enumerate(days):
        rows = bundle['daily'].loc[bundle['daily'].date == day].to_dict('records')
        for phase in (['CLOSE'] if index == 0 else ['OPEN','CLOSE']):
            clock[0] = pd.Timestamp(str(day),tz='Asia/Shanghai') + pd.Timedelta(
                hours=9 if phase == 'OPEN' else 15, minutes=30)
            bars = deepcopy(rows)
            if phase == 'OPEN':
                for row in bars:
                    row.update(high=row['open'], low=row['open'], close=row['open'],
                               volume=prior[row['symbol']]['volume'], amount=prior[row['symbol']]['amount'])
            value = snapshots.record_synthetic(phase=phase, market_date=day, received_at=clock[0].isoformat(),
                payload={'bars':bars, 'turn':bundle['turn'].loc[bundle['turn'].date == day].to_dict('records'),
                    'states':[{'symbol':s,'date':day,'listed':True,'delisted':False,
                               'is_st':False,'board':'MAIN','suspended':False} for s in window['symbols']],
                    'corporate_actions':[], 'corporate_actions_complete':True})
            current = paper.ingest(snapshots.root, value['snapshot_id'])
            if index == 5:
                paper = ForwardPaperSessionV1(paper.root, clock=lambda:clock[0])
                assert paper.ingest(snapshots.root, value['snapshot_id']) == current
        account = result['daily_accounts'][index]
        assert abs(current['state']['economic']['cash'] - account['cash']) <= .02
        assert current['state']['invariant_errors'] == []
        prior = {row['symbol']:row for row in rows}
    fields = ('strategy_id','symbol','side','quantity','price','fee','gross_value','fill_time')
    project = lambda rows: [{field:row[field] for field in fields} for row in rows]
    assert result['fills']
    assert project(current['state']['economic']['trades']) == project(result['fills'])
    assert current['state']['rule_states'] == result['final_account_checkpoint']['rule_states']
    assert current['real_observation_days'] == 0
    assert current['strategy_qualified'] is False
