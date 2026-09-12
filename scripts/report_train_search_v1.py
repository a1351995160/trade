"""评估侧读取八份既有结果，向用户保存原指标，设计侧只见固定筛选原因。"""
import os
from datetime import datetime, timezone
from pathlib import Path

from run_baostock_account_v1 import ROOT as INPUT, read, save, sha

CASES = {1:['MOMENTUM_5','STABILITY_20'],2:['MOMENTUM_60','LIQUIDITY_20'],
         3:['STABILITY_20_HOLD_20','LIQUIDITY_20_HOLD_20'],
         4:['MOMENTUM_60_HOLD_20_MARKET_5','STABILITY_20_HOLD_20_MARKET_5']}
ROOT = INPUT.parent/'train-search-delivery-v2'


def reason(result):
    metrics = result.get('metrics')
    if result['status']!='COMPLETE' or metrics is None:
        return 'INCOMPLETE_NO_PROFITABILITY_CONCLUSION'
    reasons = []
    if metrics['train_net_return']<=0:
        reasons.append('TRAIN_NET_RETURN_NOT_POSITIVE')
    if metrics['closed_lots']<30:
        reasons.append('FEWER_THAN_30_CLOSED_LOTS')
    return '+'.join(reasons) if reasons else 'TRAIN_SCREEN_PASSED_NOT_QUALIFIED'


def run():
    import json
    from chanlun_trader.research_factory.train_search_batch_v1 import TrainSearchGovernanceV1
    from run_baostock_account_v1 import PARENT
    from chanlun_trader.research_factory.common import stable_hash
    save(ROOT/'REPORT_RULE.json',{'purpose':'USER_REPORT_OF_EIGHT_EXISTING_TRAIN_RESULTS',
        'script_sha256':sha(Path(__file__)), 'cases':CASES,
        'metrics':'COPY_ORIGINAL_METRICS_ONLY_NO_NEW_STATISTICS',
        'design_feedback':'EXISTING_SCREEN_COMPONENT_FAILURE_REASONS_ONLY',
        'new_result_information_access':True,'new_price_experiments':0})
    table=['# 八项账户研究结果（仅TRAIN探索）','',
        '以下指标直接来自已完成的原账户结果，已计入原费用。它们不是独立样本外证据，不保证未来盈利。',
        '本报告由评估脚本生成，精确数值交付请求用户；设计侧只接收固定筛选原因。','',
        '|候选|期末模型权益（元）|TRAIN模型净回报|最大回撤|已关闭lot|筛选结果|',
        '|---|---:|---:|---:|---:|---|']
    index, feedback = {}, {}
    access = []
    for batch,names in CASES.items():
        for name in names:
            directory=INPUT.parent/f'train-search-batch-v{batch}'/name
            path=directory/'RESULT.json'
            outcome=read(directory/'FEEDBACK.json')
            if sha(path)!=outcome['result_sha256']:
                raise PermissionError('RESULT_HASH_CONFLICT')
            governance=TrainSearchGovernanceV1(PARENT,name)
            receipt=governance.active()
            settlement=governance.summary()
            execution=read(directory/'EXECUTION.json')['execution_id']
            terminal=[e for e in settlement['events'] if e['event']=='SETTLED' and e['execution_id']==execution]
            if len(terminal)!=1 or not terminal[0]['completed']:
                raise PermissionError('RESULT_SETTLEMENT_NOT_COMPLETE')
            access.append({'path':str(path),'sha256':sha(path),'execution_id':execution,
                'reader_pid':os.getpid(),'reader_thread':os.environ.get('CODEX_THREAD_ID'),
                'recipient':'REQUESTING_USER','purpose':'ORIGINAL_METRIC_REPORT',
                'read_at':datetime.now(timezone.utc).isoformat()})
            save(ROOT/(name+'.access.json'),access[-1])
            result=read(path)
            if (stable_hash(result['contract']) not in receipt['plan']['contracts'] or
                    result['input_identity']!=receipt['plan']['input_identity']):
                raise PermissionError('RESULT_CONTRACT_OR_INPUT_CONFLICT')
            feedback[name]=reason(result)
            m=result['metrics']
            table.append(f"|{name}|{m['ending_equity']:.2f}|{m['train_net_return']:.2%}|"
                         f"{m['max_drawdown']:.2%}|{m['closed_lots']}|{feedback[name]}|")
            index[name]={'result':str(path),'sha256':sha(path),'execution_id':execution,
                'receipt':str(governance.receipt_path),'receipt_sha256':sha(governance.receipt_path),
                'input_identity':result['input_identity'],'metrics':m,
                'main_used':settlement['MAIN_BACKTEST_EXPOSURES_USED'],
                'repair_used':settlement['REPAIR_BACKTEST_EXPOSURES_USED']}
    table += ['','所有八项同等报告；窗口/持有期变体并不构成独立机制证据。',
        '保留旧12次消费与全部历史曝光；本轮8次主试验，修复0次。新颖性历史语义仍不完整。',
        '股票池状态、HFQ回溯版本和事后hazard排除仍有局限。未运行V4、正式Trial、Validation、Final Test、Paper或真实订单。',
        'STRICT_TRAIN_INPUT_READY=false；READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false；AUTONOMOUS_STRATEGY_GOAL_COMPLETED=false。']
    save(ROOT/'RESULTS_INDEX.json',index)
    save(ROOT/'BLIND_FEEDBACK.json',feedback)
    with (ROOT/'REPORT.md').open('x',encoding='utf-8') as stream:
        stream.write('\n'.join(table)+'\n')
    print(json.dumps({'feedback':feedback,'report':str(ROOT/'REPORT.md')},ensure_ascii=False))


if __name__=='__main__':
    run()
