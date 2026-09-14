"""固定月末反转的事后描述性核验；只读原结果，不读行情、不重跑账户。"""
import json
import os
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

from run_baostock_account_v1 import ROOT as INPUT,PARENT,read,save,sha,active

NAME='MONTHLY_REVERSAL_HOLD_20'
CASE=INPUT.parent/'train-search-batch-v6'/NAME
ROOT=INPUT.parent/'monthly-robustness-review-v2'
RULE={
    'terminal_snapshot':'Only one pair of fully identical final snapshots may represent one session; retain both originals',
    'type':'POSTHOC_DESCRIPTIVE_ROBUSTNESS_NOT_QUALIFICATION',
    'periods':'All observed calendar months and calendar years; partial years labelled; boundary equity carries forward',
    'cost_multipliers':[1.5,2.0],
    'cost_formula':'ending_equity-initial_cash-(multiplier-1)*(total_fees+total_slippage_cost)',
    'cost_limit':'STATIC_ORIGINAL_FILL_PATH_HAIRCUT_NOT_ACCOUNT_RERUN; total_fees already includes stamp tax',
    'concentration_top_counts':[1,3,5],
    'concentration':'closed-lot realised net PnL, including allocated buy/sell costs; no date/lot deleted from original',
    'concentration_limit':'ARITHMETIC_SENSITIVITY_NOT_COUNTERFACTUAL_STRATEGY',
    'rounding_tolerance_cny':0.01,
    'new_price_experiments':0,'new_result_information_access':True,
    'no_p_q_bootstrap_or_new_acceptance_threshold':True,
    'independence':'TRAIN_REUSED; LEGACY_VALIDATION_EXPOSED; FINAL_TEST_NOT_ACCESSED',
}


def describe(result, *, approved_weekly_window=False):
    import pandas as pd
    if result['status']!='COMPLETE' or not result.get('metrics'):
        raise ValueError('COMPLETE_ORIGINAL_ACCOUNT_REQUIRED')
    m=result['metrics']; state=result['final_account_checkpoint']['state']
    initial=float(state['initial_cash']); snapshots=result['daily_account']
    snapshot_records=len(snapshots)
    if len(snapshots)>1 and snapshots[-1]==snapshots[-2]:
        snapshots=snapshots[:-1]
    stamps=[pd.Timestamp(s['timestamp']).tz_convert('Asia/Shanghai') for s in snapshots]
    bounds=(20250801,20260731) if approved_weekly_window else (20220801,20240731)
    if approved_weekly_window and result.get('evaluation_window')!=list(bounds):
        raise ValueError('APPROVED_WEEKLY_RESULT_WINDOW_REQUIRED')
    if stamps!=sorted(set(stamps)) or not all(bounds[0]<=int(s.strftime('%Y%m%d'))<=bounds[1] for s in stamps):
        raise ValueError('ORIGINAL_TRAIN_DAILY_IDENTITY_CONFLICT')
    if abs(snapshots[-1]['equity']-m['ending_equity'])>RULE['rounding_tolerance_cny']:
        raise ValueError('ENDING_EQUITY_CONFLICT')
    fees=sum(f['total_fee'] for f in result['fills'])
    slip=sum(f['slippage_cost'] for f in result['fills'])
    if abs(fees-m['total_fees'])>0.01 or abs(slip-m['total_slippage_cost'])>0.01:
        raise ValueError('ORIGINAL_COST_COMPONENT_CONFLICT')
    periods={}
    for label,fmt in [('months','%Y-%m'),('years','%Y')]:
        groups=defaultdict(list)
        for stamp,snapshot in zip(stamps,snapshots):
            groups[stamp.strftime(fmt)].append((stamp,snapshot))
        previous=initial; rows=[]
        for period,group in groups.items():
            end=group[-1][1]['equity']
            rows.append({'period':period,'from':group[0][0].date().isoformat(),
                         'to':group[-1][0].date().isoformat(),'start_equity':previous,'end_equity':end,
                         'net_change_cny':end-previous,'return':end/previous-1 if previous>0 else None,
                         'observed_sessions':len(group)})
            previous=end
        periods[label]=rows
    closed={key:0.0 for key,lot in state['lots'].items() if lot['remaining_quantity']==0}
    for fill in result['fills']:
        if fill['side']=='SELL':
            for allocation in fill['lot_allocations']:
                if allocation['lot_id'] in closed:
                    closed[allocation['lot_id']]+=allocation['realized_pnl']
    if len(closed)!=m['closed_lots']:
        raise ValueError('CLOSED_LOT_COUNT_CONFLICT')
    winners=sorted((p for p in closed.values() if p>0),reverse=True)
    gross=sum(winners); closed_net=sum(closed.values())
    sensitivity=[{'top_n':n,'top_winner_pnl':sum(winners[:n]),
                  'share_of_gross_winning_pnl':sum(winners[:n])/gross if gross else None,
                  'closed_net_excluding_top_winners':closed_net-sum(winners[:n])}
                 for n in RULE['concentration_top_counts']]
    cost=[{'multiple':k,'static_net_change_cny':m['ending_equity']-initial-(k-1)*(fees+slip),
           'static_return':(m['ending_equity']-initial-(k-1)*(fees+slip))/initial}
          for k in RULE['cost_multipliers']]
    evidence_flags=[]
    if cost[-1]['static_net_change_cny']<=0:evidence_flags.append('STATIC_X2_COST_ERASES_PROFIT')
    if sensitivity[0]['closed_net_excluding_top_winners']<=0:evidence_flags.append('CLOSED_NET_DEPENDS_ON_TOP_WINNER')
    if any(p['net_change_cny']<=0 for p in periods['years']):evidence_flags.append('NOT_POSITIVE_IN_EVERY_OBSERVED_YEAR')
    return {'snapshot_records':snapshot_records,'logical_sessions':len(snapshots),
            'original_metrics':m,'periods':periods,'static_cost_sensitivity':cost,
            'closed_lot_concentration':sensitivity,'closed_lot_net_pnl':closed_net,
            'terminal_unrealized_pnl':snapshots[-1]['unrealized_pnl'],
            'terminal_positions':snapshots[-1]['positions'],
            'flags':evidence_flags,'meaning':'DESCRIPTIVE_WARNINGS_NOT_FORMAL_PASS_FAIL'}


def run():
    from chanlun_trader.research_factory.train_search_batch_v1 import TrainSearchGovernanceV1,contract
    from chanlun_trader.research_factory.common import stable_hash
    active()
    governance=TrainSearchGovernanceV1(PARENT,NAME)
    receipt=governance.active(); settlement=governance.summary()
    execution=read(CASE/'EXECUTION.json')['execution_id']
    terminal=[e for e in settlement['events'] if e['event']=='SETTLED' and e['execution_id']==execution]
    if len(terminal)!=1 or not terminal[0]['completed']:raise PermissionError('SETTLEMENT_REQUIRED')
    path=CASE/'RESULT.json'; expected=read(CASE/'FEEDBACK.json')['result_sha256']
    if sha(path)!=expected:raise PermissionError('RESULT_CHANGED')
    save(ROOT/'PREREGISTRATION.json',{'rule':RULE,'script_sha256':sha(Path(__file__)),
        'result':str(path),'result_sha256':expected,'contract_id':stable_hash(contract(NAME)),
        'receipt':str(governance.receipt_path),'receipt_sha256':sha(governance.receipt_path),
        'execution_id':execution,'approved_request':'再核验它的稳健性和独立数据表现',
        'thread':os.environ.get('CODEX_THREAD_ID'),'at':datetime.now(timezone.utc).isoformat()})
    save(ROOT/'ACCESS.json',{'reader_pid':os.getpid(),'recipient':'REQUESTING_USER',
        'purpose':'FROZEN_POSTHOC_ROBUSTNESS','path':str(path),'sha256':expected,
        'new_result_information_access':True,'new_price_experiments':0,
        'at':datetime.now(timezone.utc).isoformat()})
    result=read(path)
    if stable_hash(result['contract']) not in receipt['plan']['contracts'] or result['input_identity']!=receipt['plan']['input_identity']:
        raise PermissionError('ORIGINAL_RESULT_IDENTITY_CONFLICT')
    report=describe(result)
    save(ROOT/'SUMMARY.json',report)
    lines=['# 月末反转：已有结果稳健性核验','','POSTHOC_DESCRIPTIVE_ROBUSTNESS；不是独立样本外验证或正式资格。','',
           '固定规则后只读原账户结果；未读行情、未改信号、未重跑回测。以下全部口径在本次结果读取前固定。','',
           '|区间|实际日期|模型净变化（元）|区间回报|','|---|---|---:|---:|']
    for p in report['periods']['years']:
        lines.append(f"|{p['period']}|{p['from']}至{p['to']}|{p['net_change_cny']:.2f}|{p['return']:.2%}|")
    lines+=['',f"原快照{report['snapshot_records']}条，对应{report['logical_sessions']}个session；仅引擎末端完全相同快照按一个日期计，原件未删。",
            '2022与2024为部分年份；不年化。所有月份在SUMMARY.json中同等保留。','',
            '|原成交路径成本倍数|静态净变化（元）|静态回报|','|---|---:|---:|']
    for row in report['static_cost_sensitivity']:
        lines.append(f"|{row['multiple']}|{row['static_net_change_cny']:.2f}|{row['static_return']:.2%}|")
    lines+=['','该成本扣减保持原成交路径不变，没有重算资金不足、数量或成交机会；不能冒充原引擎COMBINED_X2回测。印花税已含在原total_fees，未重复加税。','',
            '|最大盈利lot数量|占毛盈利比例|关闭lot净盈利减去这些盈利（元）|','|---|---:|---:|']
    for row in report['closed_lot_concentration']:
        share=row['share_of_gross_winning_pnl']
        lines.append(f"|{row['top_n']}|{format(share,'.2%') if share is not None else 'UNDEFINED'}|{row['closed_net_excluding_top_winners']:.2f}|")
    lines+=['','上述为算术敏感性，不删除原交易，也不代表可以提前识别或不做这些交易。',
            f"期末未实现PnL：{report['terminal_unrealized_pnl']:.2f}元；持仓数：{report['terminal_positions']}。",
            '诊断标记：'+(', '.join(report['flags']) or '本组有限描述检查未触发警示，不能据此证明稳健。'),
            '', '独立数据尚未执行：旧Validation 2024-08-01至2025-07-31已曝光；2025-08-01起为原禁用Final Test。',
            '既有研究不完整的曝光历史、回溯HFQ与事后hazard筛选局限仍在；所有正式完成/就绪标志保持false。']
    with (ROOT/'REPORT.md').open('x',encoding='utf-8') as stream:stream.write('\n'.join(lines)+'\n')
    save(ROOT/'DESIGN_FEEDBACK.json',{'flags':report['flags'],'qualification':False,
        'independent_data_evaluated':False,'new_price_experiments':0})
    print(json.dumps({'report':str(ROOT/'REPORT.md'),'flags':report['flags'],
                      'independent_data_evaluated':False,'new_price_experiments':0},ensure_ascii=False))


if __name__=='__main__':run()
