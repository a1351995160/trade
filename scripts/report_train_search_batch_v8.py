"""后续批次盲化交付；不读取精确收益或价格数据，不重算试验。"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os

from run_baostock_account_v1 import ROOT as INPUT, read, save, sha

ROOT=INPUT.parent/'train-search-batch-v8'
BATCH=8


def run():
    prereg=read(ROOT/'PREREGISTRATION.json')
    output=[];access=[]
    def audited(path):
        value=read(path)
        access.append({'path':str(path),'sha256':sha(path),'reader_pid':os.getpid(),
            'recipient':'REQUESTING_USER_AND_DESIGN_BINARY_ONLY',
            'purpose':f'BATCH_{BATCH}_EXISTING_BLINDED_STATUS','at':datetime.now(timezone.utc).isoformat()})
        return value
    for name in prereg['contracts']:
        row={'candidate':name,'novelty_allowed':prereg['novelty'][name]['allowed'],
             'screen_passed':None,'account_exposures':0,'repair_exposures':0,'evidence':{}}
        directory=ROOT/name
        if (directory/'FEASIBILITY.json').exists():
            value=audited(directory/'FEASIBILITY.json')
            row.update(feasibility_passed=value['passed'],counts=value['counts'],
                candidate_paths=value['candidate_paths'],
                rejection_counts=dict(Counter(p['reason'] for p in value['paths'])))
            row['evidence']['feasibility']=str(directory/'FEASIBILITY.json')
        if (directory/'FEEDBACK.json').exists():
            feedback=audited(directory/'FEEDBACK.json')
            result=directory/'RESULT.json'
            if sha(result)!=feedback['result_sha256']:
                raise PermissionError('RESULT_HASH_CONFLICT')
            # 只哈希原结果字节，不解析精确绩效。
            row.update(status=feedback['status'],screen_passed=feedback['screen_passed'])
            row['evidence'].update(result=str(result),result_sha256=feedback['result_sha256'])
        if (directory/'SETTLEMENT.json').exists():
            settlement=audited(directory/'SETTLEMENT.json')
            row.update(account_exposures=settlement['MAIN_BACKTEST_EXPOSURES_USED'],
                       repair_exposures=settlement['REPAIR_BACKTEST_EXPOSURES_USED'])
            row['evidence'].update(settlement=str(directory/'SETTLEMENT.json'),
                                   settlement_sha256=sha(directory/'SETTLEMENT.json'))
        receipts=[]
        for stage in ['prepare','account']:
            path=ROOT/'resources'/f'{name}-{stage}.completed.json'
            if path.exists():
                receipt=audited(path)
                receipts.append({'stage':stage,'returncode':receipt['returncode'],
                    'timed_out':receipt['timed_out'],'elapsed_seconds':receipt['elapsed_seconds']})
                if receipt['returncode']:
                    row['status']='ENGINEERING_FAILED_SEE_RECEIPT'
        row['resources']=receipts
        if 'status' not in row:
            row['status']='FEASIBILITY_REJECTED' if row.get('feasibility_passed') is False else 'NOT_COMPLETED'
        output.append(row)
    status={'batch':BATCH,'contracts_sha256':sha(ROOT/'PREREGISTRATION.json'),'candidates':output,
        'main_exposures':sum(r['account_exposures'] for r in output),
        'repair_exposures':sum(r['repair_exposures'] for r in output),
        'worker_seconds':sum(x['elapsed_seconds'] for r in output for x in r['resources']),
        'qualification':False,'exact_performance_read_by_design':False,
        'READY_FOR_REAL_TRIAL':False,'R1_FULLY_CLOSED':False,'AUTONOMOUS_STRATEGY_GOAL_COMPLETED':False}
    save(ROOT/'DELIVERY_STATUS.json',status)
    save(ROOT/'DELIVERY_ACCESS.json',access)
    lines=[f'# 第{BATCH}批训练探索结果','',
        '仅既有训练区；已经多次曝光。筛选通过不代表稳健、独立验证或正式资格。','',
        '|候选|可行性|账户状态|训练筛选|主/修复曝光|',
        '|---|---|---|---|---|']
    for row in output:
        lines.append(f"|{row['candidate']}|{row.get('feasibility_passed')}|{row['status']}|{row['screen_passed']}|{row['account_exposures']}/{row['repair_exposures']}|")
    lines+=['','精确原结果留存各候选RESULT.json；本报告不读取其指标，不以结果调参。',
        '新颖性、可行性统计、原结果身份和结算索引见DELIVERY_STATUS.json；实际读取记录见DELIVERY_ACCESS.json。',
        '无策略净回报正且关闭lot>=30的候选即报告未找到；不降低筛选或独立窗口门槛。']
    report=ROOT/'DELIVERY.md'
    with report.open('x',encoding='utf-8') as stream:stream.write('\n'.join(lines)+'\n')
    print(json.dumps(status,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--batch',type=int,choices=list(range(8,44)),default=8)
    BATCH=parser.parse_args().batch
    ROOT=INPUT.parent/f'train-search-batch-v{BATCH}'
    from batch39_input_recovery_v1 import resolve
    ROOT=resolve(ROOT)
    run()
