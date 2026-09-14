"""继续研究中的既有盈利候选核验；复用原描述口径，精确数值仅写用户私有报告。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from run_baostock_account_v1 import ROOT as INPUT,PARENT,read,save,sha,active
from review_monthly_robustness_v1 import describe,RULE
from chanlun_trader.research_factory.train_search_batch_v1 import TrainSearchGovernanceV1
from chanlun_trader.research_factory.common import stable_hash


def run(batch,name):
    active()
    from batch39_input_recovery_v1 import resolve
    base=resolve(INPUT.parent/f'train-search-batch-v{batch}')
    prereg=read(base/'PREREGISTRATION.json')
    if name not in prereg['contracts']:raise PermissionError('CANDIDATE_NOT_FROZEN')
    case=base/name
    feedback=read(case/'FEEDBACK.json')
    if feedback['screen_passed'] is not True:raise PermissionError('NO_PROMISING_CANDIDATE')
    service=TrainSearchGovernanceV1(PARENT,name)
    receipt=service.active()
    execution=read(case/'EXECUTION.json')['execution_id']
    settled=[e for e in service.summary()['events'] if e['event']=='SETTLED' and e['execution_id']==execution]
    if len(settled)!=1 or not settled[0]['completed']:raise PermissionError('SETTLEMENT_REQUIRED')
    source=case/'RESULT.json'
    if sha(source)!=feedback['result_sha256']:raise PermissionError('RESULT_CHANGED')
    out=case/'robustness-review-v1'
    now=datetime.now(timezone.utc).isoformat()
    save(out/'RULE.json',{'rule':RULE,'candidate':name,'batch':batch,
        'source':str(source),'sha256':sha(source),'receipt_sha256':sha(service.receipt_path),
        'script_sha256':sha(Path(__file__)),'calculator_sha256':sha(Path(__file__).with_name('review_monthly_robustness_v1.py')),
        'approval_statement':'那继续找吧，找到能用的为止','purpose':'PROMISING_CANDIDATE_USABILITY_REVIEW',
        'reader':os.environ['CODEX_THREAD_ID'],'at':now})
    save(out/'ACCESS.json',{'reader_pid':os.getpid(),'recipient':'REQUESTING_USER',
        'source':str(source),'sha256':sha(source),'purpose':'POSTHOC_DESCRIPTIVE_ROBUSTNESS',
        'new_result_information_access':True,'new_price_experiments':0,'at':now})
    value=read(source)
    if (stable_hash(value['contract']) not in receipt['plan']['contracts']
            or value['input_identity']!=receipt['plan']['input_identity']):
        raise PermissionError('RESULT_CONTRACT_INPUT_CONFLICT')
    summary=describe(value)
    save(out/'SUMMARY.json',summary)
    save(out/'DESIGN_FEEDBACK.json',{'candidate':name,'flags':summary['flags'],
        'meaning':'LIMITED_DESCRIPTIVE_CHECKS_NOT_QUALIFICATION','new_price_experiments':0,
        'independent_data_evaluated':False,'qualified':False})
    lines=[f'# {name} 已有训练结果核验','',
        'POSTHOC_DESCRIPTIVE_ROBUSTNESS；不是独立验证或正式资格。未重读行情或重跑账户。','',
        '|观察年份|实际窗口|模型净变化/元|回报|','|---|---|---:|---:|']
    for row in summary['periods']['years']:
        lines.append(f"|{row['period']}|{row['from']}至{row['to']}|{row['net_change_cny']:.2f}|{row['return']:.2%}|")
    lines+=['','2022/2024仅部分年份，未年化。完整月份及成本、盈利集中度见SUMMARY.json。','',
        '诊断标记：'+(', '.join(summary['flags']) or '本组有限检查未触发警示；不能证明稳健。'),
        '成本敏感性仅原路径静态扣减，不是账户重跑；盈利集中度不删除原交易。',
        '该TRAIN已反复研究，不能称样本外。未读取月末专用窗口或其他封存数据。',
        'READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false；AUTONOMOUS_STRATEGY_GOAL_COMPLETED=false。']
    with (out/'REPORT.md').open('x',encoding='utf-8') as stream:stream.write('\n'.join(lines)+'\n')
    print(json.dumps({'candidate':name,'flags':summary['flags'],'report':str(out/'REPORT.md'),
        'independent_data_evaluated':False,'qualified':False},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--batch',type=int,choices=list(range(9,44)),required=True)
    parser.add_argument('--name',required=True)
    args=parser.parse_args()
    run(args.batch,args.name)
