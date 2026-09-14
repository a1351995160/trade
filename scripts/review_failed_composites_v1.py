"""本次用户明确批准的失败复盘；只读两份结算原结果，记录设计端实际曝光。"""
from datetime import datetime,timezone
import os
from pathlib import Path
import json
import argparse

from run_baostock_account_v1 import ROOT as INPUT,PARENT,active,read,save,sha
from chanlun_trader.research_factory.train_search_batch_v1 import TrainSearchGovernanceV1
from chanlun_trader.research_factory.common import stable_hash

ROOT=INPUT.parent/'train-search-batch-v16/failure-learning-v1'


def summarize(value):
    metrics=value['metrics']
    if metrics is None:raise ValueError('NO_COMPLETE_METRICS')
    failed=[]
    if value['status']!='COMPLETE':failed.append('ACCOUNT_NOT_COMPLETE')
    if metrics['train_net_return']<=0:failed.append('TRAIN_NET_RETURN_NOT_POSITIVE')
    if metrics['closed_lots']<30:failed.append('CLOSED_LOTS_BELOW_30')
    return {'status':value['status'],'stored_metrics':metrics,'failed_original_conditions':failed,
        'interpretation':'DESCRIPTIVE_FAILURE_REVIEW_NOT_CAUSAL_ATTRIBUTION',
        'limits':'No counterfactual exit, market regime, fee-free rerun, parameter sweep or paired comparison'}


def run():
    active()
    base=ROOT.parent
    index=read(base/'DELIVERY_STATUS.json')
    prereg=read(base/'PREREGISTRATION.json')
    save(ROOT/'RULE.json',{'scope':str(base),'rule':'Report all indexed stored metrics and each failed original screen condition; no new price calculation',
        'approval':'先告诉我为什么没有通过训练筛选，你要从里面吸收经验，然后运用到下一次的思考策略中',
        'new_result_information_access':True,'design_performance_exposure':True,
        'independence':'TRAIN_REPEATEDLY_EXPOSED','script_sha256':sha(Path(__file__)),
        'index_sha256':sha(base/'DELIVERY_STATUS.json'),'at':datetime.now(timezone.utc).isoformat()})
    results=[]
    for row in index['candidates']:
        name=row['candidate'];case=base/name
        if name not in prereg['contracts']:raise PermissionError('EXACT_RESULT_IDENTITY_CONFLICT')
        if 'result' not in row['evidence']:
            # 未通过前置门槛不产生账户结果，不能把合法拒绝误报为丢失绩效。
            if row['account_exposures'] or row['repair_exposures'] or (case/'EXECUTION.json').exists():
                raise PermissionError('EXPOSED_RESULT_MISSING')
            source=case/('REJECTED.json' if not row['novelty_allowed'] else 'FEASIBILITY.json')
            save(ROOT/f'ACCESS-{name}.json',{'reader_pid':os.getpid(),'reader_thread':os.environ['CODEX_THREAD_ID'],
                'recipient':'REQUESTING_USER_AND_CURRENT_DESIGN_SESSION_EXPLICITLY_AUTHORIZED',
                'purpose':'PRE_EXECUTION_REJECTION_REVIEW','path':str(source),'sha256':sha(source),
                'new_price_experiments':0,'at':datetime.now(timezone.utc).isoformat()})
            rejection=read(source)
            key='allowed' if not row['novelty_allowed'] else 'passed'
            if rejection[key] is not False:raise PermissionError('UNEXECUTED_REJECTION_CONFLICT')
            result={'candidate':name,'status':'NOVELTY_REJECTED' if key=='allowed' else 'FEASIBILITY_REJECTED',
                'stored_metrics':None,'failed_original_conditions':['PRE_EXECUTION_GATE_REJECTED'],
                'interpretation':'NO_ACCOUNT_OUTCOME; NOT_A_LOSS_OR_ZERO_RETURN'}
            save(ROOT/f'{name}.json',result);results.append(result)
            continue
        source=Path(row['evidence']['result'])
        if name not in prereg['contracts'] or source.resolve()!=(case/'RESULT.json').resolve():
            raise PermissionError('EXACT_RESULT_IDENTITY_CONFLICT')
        if sha(source)!=row['evidence']['result_sha256']:raise PermissionError('RESULT_HASH_CONFLICT')
        service=TrainSearchGovernanceV1(PARENT,name);receipt=service.active()
        eid=read(case/'EXECUTION.json')['execution_id']
        events=[e for e in service.summary()['events'] if e['event']=='SETTLED' and e['execution_id']==eid]
        if len(events)!=1 or not events[0]['completed']:raise PermissionError('SETTLEMENT_REQUIRED')
        save(ROOT/f'ACCESS-{name}.json',{'reader_pid':os.getpid(),'reader_thread':os.environ['CODEX_THREAD_ID'],
            'recipient':'REQUESTING_USER_AND_CURRENT_DESIGN_SESSION_EXPLICITLY_AUTHORIZED',
            'purpose':'FAILURE_LEARNING_FROM_EXISTING_RESULTS','path':str(source),'sha256':sha(source),
            'receipt_id':receipt['receipt_id'],'new_price_experiments':0,'at':datetime.now(timezone.utc).isoformat()})
        value=read(source)
        if stable_hash(value['contract']) not in receipt['plan']['contracts'] or value['input_identity']!=receipt['plan']['input_identity']:
            raise PermissionError('RESULT_CONTRACT_INPUT_CONFLICT')
        result={'candidate':name,**summarize(value)}
        save(ROOT/f'{name}.json',result);results.append(result)
    save(ROOT/'SUMMARY.json',{'candidates':results,'new_price_experiments':0,'design_performance_exposed':True})
    print(json.dumps(results,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--batch',type=int,choices=[16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43],default=16)
    batch=parser.parse_args().batch
    from batch39_input_recovery_v1 import resolve
    ROOT=resolve(INPUT.parent/f'train-search-batch-v{batch}')/'failure-learning-v1'
    run()
