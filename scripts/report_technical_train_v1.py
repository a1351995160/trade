"""技术研究批次的原指标用户交付；设计侧只见筛选原因，不补算统计。"""
import json
import os
from datetime import datetime,timezone
from pathlib import Path

from run_baostock_account_v1 import ROOT as INPUT,PARENT,read,sha,save
from report_train_search_v1 import reason

CASES={5:['MACD_CROSS_HOLD_20','KDJ_OVERSOLD_CROSS_HOLD_20'],
       6:['CHAN_BOTTOM_MACD_HOLD_20','MONTHLY_REVERSAL_HOLD_20'],
       7:['HIGH_252_HOLD_20','LOW_MAX_20_HOLD_20']}
ROOT=INPUT.parent/'technical-train-delivery-v1'


def run():
    from chanlun_trader.research_factory.train_search_batch_v1 import TrainSearchGovernanceV1
    from chanlun_trader.research_factory.common import stable_hash
    save(ROOT/'REPORT_RULE.json',{'cases':CASES,'script_sha256':sha(Path(__file__)),
        'purpose':'REQUESTING_USER_ORIGINAL_METRICS_ONLY','new_result_access':True,
        'new_price_experiments':0,'design_feedback':'FIXED_SCREEN_REASONS_ONLY'})
    lines=['# 六项技术规则研究（TRAIN探索）','','只复制原账户指标，不计算新增统计。已计原费用，不是独立样本外盈利证明。','',
           '|候选|状态|期末权益元|TRAIN净回报|最大回撤|关闭lot|',
           '|---|---|---:|---:|---:|---:|']
    index,blind={},{}
    for batch,names in CASES.items():
        for name in names:
            directory=INPUT.parent/f'train-search-batch-v{batch}'/name
            path=directory/'RESULT.json'
            if not path.exists():
                status='NO_ACCOUNT_RESULT'
                evidence={}
                for filename in ['REJECTED.json','READY.json']:
                    if (directory/filename).exists():
                        evidence[filename]={'path':str(directory/filename),'sha256':sha(directory/filename)}
                        status=('NOVELTY_REJECTED' if filename=='REJECTED.json' else
                                'ACCOUNT_NOT_COMPLETED' if read(directory/filename)['feasibility_passed'] else
                                'FEASIBILITY_NOT_READY')
                index[name]={'status':status,'evidence':evidence}
                blind[name]=status
                lines.append(f'|{name}|{status}|—|—|—|—|')
                continue
            feedback=read(directory/'FEEDBACK.json')
            if sha(path)!=feedback['result_sha256']:
                raise PermissionError('RESULT_HASH_CONFLICT')
            governance=TrainSearchGovernanceV1(PARENT,name)
            receipt=governance.active()
            settlement=governance.summary()
            eid=read(directory/'EXECUTION.json')['execution_id']
            terminal=[e for e in settlement['events'] if e['event']=='SETTLED' and e['execution_id']==eid]
            if len(terminal)!=1 or not terminal[0]['completed']:
                raise PermissionError('UNSETTLED_RESULT')
            save(ROOT/(name+'.access.json'),{'reader_pid':os.getpid(),'thread':os.environ.get('CODEX_THREAD_ID'),
                'recipient':'REQUESTING_USER','purpose':'COPY_ORIGINAL_ACCOUNT_METRICS',
                'path':str(path),'sha256':sha(path),'at':datetime.now(timezone.utc).isoformat()})
            result=read(path)
            if stable_hash(result['contract']) not in receipt['plan']['contracts'] or result['input_identity']!=receipt['plan']['input_identity']:
                raise PermissionError('RESULT_IDENTITY_CONFLICT')
            blind[name]=reason(result)
            m=result['metrics']
            index[name]={'result':str(path),'sha256':sha(path),'execution_id':eid,
                'receipt':str(governance.receipt_path),'metrics':m,'status':result['status'],
                'main_used':settlement['MAIN_BACKTEST_EXPOSURES_USED'],
                'repair_used':settlement['REPAIR_BACKTEST_EXPOSURES_USED']}
            cells=f"{m['ending_equity']:.2f}|{m['train_net_return']:.2%}|{m['max_drawdown']:.2%}|{m['closed_lots']}" if m else '—|—|—|—'
            lines.append(f"|{name}|{blind[name]}|{cells}|")
    lines+=['',f'原结果、完整原指标与结算索引：[RESULTS_INDEX.json]({(ROOT/"RESULTS_INDEX.json").as_posix()})。',
        '六项同等列示。缺结果不重跑补造；未通过可行性不是策略亏损。',
        '缠论为封闭底分型+MACD简化规则。HFQ/RAW比例派生OHLC不是厂商时点证明。',
        '原始费用、容量、历史池及hazard规则未放宽；回溯数据、历史曝光与事后hazard限制保留。',
        'STRICT_TRAIN_INPUT_READY=false；READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false；AUTONOMOUS_STRATEGY_GOAL_COMPLETED=false。']
    save(ROOT/'RESULTS_INDEX.json',index)
    save(ROOT/'BLIND_FEEDBACK.json',blind)
    save(ROOT/'STATUS.json',{
        'selected_train_screen_candidates':[n for n,r in blind.items() if r=='TRAIN_SCREEN_PASSED_NOT_QUALIFIED'],
        'candidate_priority':'FROZEN_TRAIN_CANDIDATE_REQUIRES_ROBUSTNESS_AND_INDEPENDENT_EVIDENCE',
        'new_main_exposures':sum(r.get('main_used',0) for r in index.values()),
        'new_repair_exposures':sum(r.get('repair_used',0) for r in index.values()),
        'STRICT_TRAIN_INPUT_READY':False,'READY_FOR_REAL_TRIAL':False,
        'R1_FULLY_CLOSED':False,'AUTONOMOUS_STRATEGY_GOAL_COMPLETED':False})
    with (ROOT/'REPORT.md').open('x',encoding='utf-8') as f:
        f.write('\n'.join(lines)+'\n')
    print(json.dumps({'report':str(ROOT/'REPORT.md'),'feedback':blind},ensure_ascii=False))


if __name__=='__main__':
    run()
