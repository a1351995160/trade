"""公共报告应兼容规则账户，并以原件内容核对结算。"""
import json
from copy import deepcopy
import pytest
from scripts.run_strategy_account_v1 import save, sha, write_reports
from chanlun_trader.research_factory.strategy_report_v1 import build_report


def fixture_job(tmp_path):
    index = {}
    metrics = dict(net_return=0., max_drawdown=0., total_fees=0., trade_count=0)
    for name in ('candidate', 'benchmark'):
        result = {'daily_accounts': [{'date': 20240102, 'equity': 50000}],
                  'metrics': metrics, 'fills': [], 'final_account_checkpoint': {},
                  'report': build_report(strategy={'version': 'V3'}, stage='ACCOUNT_BACKTEST',
                    status='COMPLETED', reasons=[], metrics=metrics,
                    artifacts={'trades': {}, 'ledger': {}, 'source_result': {'in_memory': True}},
                    benchmark={'status': 'NOT_RUN'}, limitations=[])}
        output, settlement = tmp_path / (name + '.json'), tmp_path / (name + '_settlement.json')
        save(output, result)
        digest = sha(output)
        save(settlement, {'result_sha256': digest})
        index[name] = {'result': str(output), 'sha256': digest, 'settlement': str(settlement)}
    return {'root': str(tmp_path), 'benchmark_id': 'benchmark'}, index


def test_daily_accounts_benchmark_report(tmp_path):
    job, index = fixture_job(tmp_path)
    write_reports(job, index)
    report = json.loads((tmp_path / 'candidate_REPORT.json').read_text(encoding='utf-8'))
    assert report['benchmark']['status'] == 'AVAILABLE'
    assert report['qualification'] == 'NOT_ASSESSED'


def test_result_content_tamper_cannot_reuse_settlement(tmp_path):
    job, index = fixture_job(tmp_path)
    path = tmp_path / 'candidate.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    value['metrics']['net_return'] = 123
    path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(PermissionError, match='REPORT_SETTLEMENT_CONFLICT'):
        write_reports(job, index)
