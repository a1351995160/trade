"""只读已生成ETF输入核验结果，输出统一模板；不读行情或启动回测。"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from chanlun_trader.research_factory.etf_grid_spec_v1 import SPEC
from chanlun_trader.research_factory.strategy_report_v1 import build_report, render_markdown

ROOT=Path('E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/etf-grid-train-v1/input-probe-v1')


def main():
    source=ROOT/'VOLUME_DECODED_PREFLIGHT_V1.json'
    raw=source.read_bytes();result=json.loads(raw)
    if result['no_outcome'] is not True or result['account_started'] is not False:
        raise ValueError('NOT_INPUT_ONLY_RESULT')
    report=build_report(strategy=SPEC.as_dict(),stage='INPUT_CHECK',status=result['status'],
        reasons=result['reasons'],metrics=dict.fromkeys(['net_return','max_drawdown','total_fees','trade_count']),
        artifacts={'trades':None,'ledger':None,'source_result':{'path':str(source),'sha256':hashlib.sha256(raw).hexdigest()}},
        benchmark={'status':'NOT_RUN'},limitations=[
            '仅基本输入核验通过；ETF账户接线和用途回执尚未完成。',
            '历史价差UNKNOWN；用户批准双边各0.1%模型滑点。',
            '格式推导不是厂商认证；不能据此宣称策略盈利或正式资格。'])
    report['access']={'reader_pid':os.getpid(),'read_at':datetime.now(timezone.utc).isoformat(),
                      'purpose':'USER_REQUESTED_STANDARD_REPORT_ADAPTER','price_exposures':0}
    for name,content in [('STANDARD_STATUS_V1.json',json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)),
                         ('STANDARD_STATUS_V1.md',render_markdown(report))]:
        with (ROOT/name).open('x',encoding='utf-8') as f:f.write(content)
    print(ROOT/'STANDARD_STATUS_V1.md')


if __name__=='__main__':main()
