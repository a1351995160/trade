"""统一报告展示层：仅呈现调用方已有结果，不重算绩效、不作资格裁决。"""
from copy import deepcopy


def build_report(*, strategy, stage, status, reasons, metrics, artifacts,
                 benchmark, limitations):
    if stage not in {'INPUT_CHECK','ACCOUNT_BACKTEST'}:
        raise ValueError('UNKNOWN_REPORT_STAGE')
    if set(metrics)!={'net_return','max_drawdown','total_fees','trade_count'}:
        raise ValueError('EXPLICIT_METRICS_REQUIRED')
    if stage=='INPUT_CHECK' and any(v is not None for v in metrics.values()):
        raise ValueError('INPUT_CHECK_CANNOT_CLAIM_PERFORMANCE')
    if set(artifacts)!={'trades','ledger','source_result'}:
        raise ValueError('EXPLICIT_ARTIFACT_REFERENCES_REQUIRED')
    if not artifacts['source_result']:
        raise ValueError('SOURCE_RESULT_REQUIRED')
    if benchmark.get('status') not in {'NOT_RUN','NOT_COMPARABLE','AVAILABLE'}:
        raise ValueError('EXPLICIT_BENCHMARK_STATUS_REQUIRED')
    return deepcopy({'schema_version':'STRATEGY_REPORT_V1','strategy':strategy,
        'stage':stage,'status':status,'reasons':list(reasons),'metrics':metrics,
        'artifacts':artifacts,'benchmark':benchmark,'limitations':list(limitations),
        'qualification':'NOT_ASSESSED','execution_authorization_granted':False})


def render_markdown(report):
    def display(value):return '尚未生成' if value is None else str(value)
    lines=[f"# {report['strategy']['version']}",f"阶段：{report['stage']}；状态：{report['status']}",
           '', '| 项目 | 原有结果 |','|---|---|']
    for key,label in [('net_return','净收益率（比例）'),('max_drawdown','最大回撤（比例）'),
                      ('total_fees','总费用（元）'),('trade_count','交易计数（原口径）')]:
        lines.append(f"| {label} | {display(report['metrics'][key])} |")
    lines.extend(['',f"基准：{report['benchmark']['status']}",
                  '原因：'+('；'.join(report['reasons']) or '本阶段无拒绝原因')])
    for key,value in report['artifacts'].items():lines.append(f'{key}：{display(value)}')
    lines.extend(['','限制：',*['- '+v for v in report['limitations']]])
    return '\n\n'.join(lines[:2])+'\n'+'\n'.join(lines[2:])+'\n'
