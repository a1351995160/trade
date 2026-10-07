"""最后一份报告的排版不能再次加载完整行情；复用仍需绑定原输入。"""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts import run_strategy_account_v1 as runner
from chanlun_trader.research_factory.universe_account_inputs_v1 import prepare_universe_account_inputs_v1
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile
from test_long_horizon_cooperative_deadline_v1 import clock, compute_worker
from universe_test_fixture_v1 import fixture


def final_report_worker(tmp_path, monkeypatch):
    timer = clock(monkeypatch)
    path, member, observed = compute_worker(tmp_path, monkeypatch, timer, 'REPORT')
    window, bundle = fixture(prices=[12., 13., 13.])
    inputs = prepare_universe_account_inputs_v1(bundle, window)
    job = runner.read_json(path)
    job.update(input_identity=inputs.input_identity, benchmark_mode='CASH_AND_PRICE_REFERENCE',
               resources=execution_profile(SEGMENTED_PROFILE, len(window['calendar']) - 60))
    job['items'][member]['backend_options'] = {'window': window, 'initial_cash': 50000}
    path.write_text(json.dumps(job), encoding='utf-8')
    folder = path.parent / 'COMPUTE_REPORT'
    scope = runner.read_json(folder / 'SCOPE.json')
    scope['job_sha256'] = runner.sha(path)
    (folder / 'SCOPE.json').write_text(json.dumps(scope), encoding='utf-8')
    runner.HANDSHAKE['execution']['scope_sha256'] = runner.sha(folder / 'SCOPE.json')
    source = path.parent / (member + '_RESULT.json')
    source.write_text(json.dumps({'report': {'artifacts': {}, 'limitations': [], 'strategy_id': member},
                                 'ledger': {}, 'metrics': {}}), encoding='utf-8')
    (path.parent / 'VERIFICATION.json').write_text(json.dumps({'job_sha256': runner.sha(path),
        'advance_allowed': True, 'items': {member: {'result_sha256': runner.sha(source)}}}), encoding='utf-8')
    settlement = path.parent / (member + '_SETTLEMENT.json')
    runner.save(settlement, {'result_sha256': runner.sha(source)})
    runner.save(path.parent / 'RESULTS_INDEX.json', {'items': {member: {
        'result': str(source), 'sha256': runner.sha(source), 'settlement': str(settlement)}}})

    def loader():
        observed['loader_calls'] += 1
        if observed['loader_calls'] > 1:
            raise MemoryError('REPORT_REDUNDANT_FULL_MARKET_READ')
        timer.now += 570
        return {'frame': bundle}

    strategy = SimpleNamespace(rule_identity='RULE', requirements=SimpleNamespace(fields=[], warmup_sessions=0))
    monkeypatch.setattr(runner, 'resolve', lambda name: {
        'fixture:loader': loader, 'fixture:strategy': lambda: strategy}[name])
    monkeypatch.setattr('chanlun_trader.research_factory.universe_account_inputs_v1._prepare_owned_universe_account_inputs_v1',
                        lambda *args, **kwargs: inputs)
    monkeypatch.setattr('chanlun_trader.research_factory.universe_research_report_v2.build_research_reports',
                        lambda *args, **kwargs: {'details_manifest': {}, 'report_identity': 'REPORT'})
    monkeypatch.setattr('chanlun_trader.research_factory.universe_signal_funnel_v1.build_signal_funnel_stream_v1',
                        lambda *args, **kwargs: {'details_manifest': {}, 'identity': 'FUNNEL'})
    monkeypatch.setattr('chanlun_trader.research_factory.universe_signal_funnel_v1.funnel_day_packets_v1', lambda *args: [])
    monkeypatch.setattr('chanlun_trader.research_factory.strategy_report_v1.render_markdown', lambda report: 'fixed report')
    return path, member, observed, inputs, timer


def test_final_report_renderer_does_not_reload_full_market(tmp_path, monkeypatch):
    path, member, observed, inputs, _ = final_report_worker(tmp_path, monkeypatch)
    assert runner._long_horizon_compute_worker(path, 'REPORT', 1, member) == 0
    assert observed['loader_calls'] == 1
    report = runner.read_json(path.parent / (member + '_REPORT.json'))
    assert report['benchmark']['input_identity'] == inputs.input_identity
    assert report['benchmark']['metrics']['net_return'] == pytest.approx(13 / 12 - 1)
    assert report['benchmark']['investable'] is False
    assert (path.parent / (member + '_REPORT.md')).read_text() == 'fixed report'
    assert runner.read_json(path.parent / 'COMPUTE_REPORT/SEGMENT_000001_STATUS.json')['state'] == 'COMPLETED'


@pytest.mark.parametrize('conflict', ['identity', 'window', 'capital', 'profile', 'type', 'scan'])
def test_prepared_report_input_rejects_scope_conflicts_before_writing(tmp_path, monkeypatch, conflict):
    path, member, observed, inputs, _ = final_report_worker(tmp_path, monkeypatch)
    job = runner.read_json(path)
    supplied = inputs
    if conflict == 'identity':
        job['input_identity'] = 'different input'
    elif conflict == 'window':
        job['items'][member]['backend_options']['window']['account_start'] = inputs.calendar[61]
    elif conflict == 'capital':
        job['items']['OTHER'] = deepcopy(job['items'][member])
        job['items']['OTHER']['backend_options']['initial_cash'] = 1000000
    elif conflict == 'profile':
        job['resources'].pop('profile_id')
    elif conflict == 'type':
        supplied = SimpleNamespace(**inputs.__dict__)
    else:
        supplied = prepare_universe_account_inputs_v1(inputs.bundle, inputs.window, stage='SCAN')
    index = runner.read_json(path.parent / 'RESULTS_INDEX.json')['items']
    with pytest.raises(PermissionError, match='REPORT_PREPARED_INPUT_SCOPE_CONFLICT'):
        runner.write_reports(job, index, _universe_inputs=supplied)
    assert observed['loader_calls'] == 0
    assert not (path.parent / (member + '_REPORT.json')).exists()


def test_mutated_prepared_input_cannot_produce_a_report(tmp_path, monkeypatch):
    path, member, observed, inputs, _ = final_report_worker(tmp_path, monkeypatch)
    inputs.bundle['daily'].loc[0, 'close'] += .1
    with pytest.raises(ValueError, match='UNIVERSE_INPUT_IDENTITY_CHANGED'):
        runner.report_account_job(path, _universe_inputs=inputs)
    assert observed['loader_calls'] == 0
    assert not (path.parent / (member + '_REPORT.json')).exists()


def test_reused_input_does_not_bypass_result_settlement(tmp_path, monkeypatch):
    path, member, observed, inputs, _ = final_report_worker(tmp_path, monkeypatch)
    (path.parent / (member + '_SETTLEMENT.json')).write_text(
        json.dumps({'result_sha256': 'changed result'}), encoding='utf-8')
    with pytest.raises(PermissionError, match='REPORT_SETTLEMENT_CONFLICT'):
        runner.report_account_job(path, _universe_inputs=inputs)
    assert observed['loader_calls'] == 0
    assert not (path.parent / (member + '_REPORT.json')).exists()


@pytest.mark.parametrize('render_elapsed', [False, True])
def test_final_render_cannot_claim_completion_after_shared_deadline(tmp_path, monkeypatch, render_elapsed):
    path, member, observed, _, timer = final_report_worker(tmp_path, monkeypatch)
    if render_elapsed:
        def render(report):
            timer.now += 300
            return 'fixed report'
        monkeypatch.setattr('chanlun_trader.research_factory.strategy_report_v1.render_markdown', render)
    else:
        def funnel(*args, **kwargs):
            timer.now += 260
            return {'details_manifest': {}, 'identity': 'FUNNEL'}
        monkeypatch.setattr('chanlun_trader.research_factory.universe_signal_funnel_v1.build_signal_funnel_stream_v1', funnel)
    assert runner._long_horizon_compute_worker(path, 'REPORT', 1, member) == 75
    assert observed['loader_calls'] == 1
    status = runner.read_json(path.parent / 'COMPUTE_REPORT/SEGMENT_000001_STATUS.json')
    assert status['state'] == 'CONTINUE' and status['phase'] == 'REPORT_RENDER'


def test_public_renderer_without_prepared_input_keeps_existing_loader_path(tmp_path, monkeypatch):
    path, member, observed, _, _ = final_report_worker(tmp_path, monkeypatch)
    runner.save(path.parent / (member + '_RESEARCH_REPORT.json'), {'details_manifest': {}})
    runner.save(path.parent / (member + '_SIGNAL_FUNNEL.json'), {'details_manifest': {}})
    runner.report_account_job(path)
    assert observed['loader_calls'] == 1
    assert runner.read_json(path.parent / (member + '_REPORT.json'))['benchmark']['investable'] is False
