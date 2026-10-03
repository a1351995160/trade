"""报告日边界续跑、已提交明细保护和末日删失不重复。"""
from copy import deepcopy
import json
import time

import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.universe_account_backend_v2 import SegmentBoundary
from chanlun_trader.research_factory.universe_report_state_v1 import OwnReportState, iter_report_details
from chanlun_trader.research_factory.universe_research_report_v2 import (
    _Observations, build_research_reports, default_observation_plan,
)
from chanlun_trader.research_factory.universe_signal_funnel_v1 import build_signal_funnel_stream_v1, funnel_day_packets_v1
from test_universe_research_report_v2 import prepared, result_for
from test_universe_signal_funnel_v1 import CALENDAR, RULE, case, packets


def report_case():
    inputs = prepared()
    days, symbol = inputs.window['calendar'][60:], inputs.window['symbols'][0]
    opportunities = {(symbol, day) for day in [*days[:6], *days[10:14], days[-1]]}
    result = result_for(inputs, opportunities, {(symbol, days[2])})
    result['strategy_plan'] = {'strategy': {'parameters': {'candidate_payload': {
        'selection': {'direction': 'DESCENDING'}, 'identity': 'frozen-rule'}}}}
    result['account_policy']['portfolio'] = {'max_positions': 1}
    return inputs, result, default_observation_plan(inputs.window)


def test_real_deadline_continuation_equals_continuous_signal_and_account(tmp_path, monkeypatch):
    inputs, result, plan = report_case()
    rows = []
    reference = build_research_reports(result, inputs, plan, event_sink=rows.append)
    original = _Observations.observe
    def delayed(self, *args):
        time.sleep(.002)
        return original(self, *args)
    monkeypatch.setattr(_Observations, 'observe', delayed)
    checkpoint, continuations = tmp_path / 'REPORT.json', 0
    for _ in range(80):
        try:
            report = build_research_reports(result, inputs, plan, report_checkpoint_path=checkpoint, segment_seconds=.06)
            break
        except SegmentBoundary as boundary:
            assert boundary.phase == 'REPORT'
            continuations += 1
    else:
        pytest.fail('bounded report did not make progress')
    assert continuations > 1
    assert report['account'] == reference['account']
    assert report['signal'] == reference['signal']
    assert list(iter_report_details(report['details_manifest'])) == rows
    assert report['report_identity'] == stable_hash({key: value for key, value in report.items() if key != 'report_identity'})
    saved = json.loads(checkpoint.read_text(encoding='utf-8'))
    assert saved['complete'] and saved['origin'] == 'OWN_REPORT_AGGREGATION_ONLY'
    assert saved['state']['observation_cursor'] == len(result['scan_days'])
    assert len(saved['details_manifest']['days']) == len(result['scan_days'])


def test_crash_after_detail_write_quarantines_tail_and_keeps_verified_prefix(tmp_path, monkeypatch):
    inputs, result, plan = report_case()
    checkpoint = tmp_path / 'REPORT.json'
    original, crashed = OwnReportState.save, False
    def interrupted(self):
        nonlocal crashed
        if len(self.details.days) == 3 and not crashed:
            crashed = True
            raise RuntimeError('SIMULATED_PROCESS_INTERRUPTION_AFTER_DETAIL_WRITE')
        return original(self)
    monkeypatch.setattr(OwnReportState, 'save', interrupted)
    with pytest.raises(RuntimeError, match='PROCESS_INTERRUPTION'):
        build_research_reports(result, inputs, plan, report_checkpoint_path=checkpoint)
    saved = json.loads(checkpoint.read_text(encoding='utf-8'))
    assert len(saved['details_manifest']['days']) == 2
    root = tmp_path / 'REPORT_DETAILS'
    prefix = {row['file']: (root / row['file']).read_bytes() for row in saved['details_manifest']['days']}
    monkeypatch.setattr(OwnReportState, 'save', original)
    resumed = build_research_reports(result, inputs, plan, report_checkpoint_path=checkpoint)
    reference_rows = []
    reference = build_research_reports(result, inputs, plan, event_sink=reference_rows.append)
    assert resumed['signal'] == reference['signal']
    assert list(iter_report_details(resumed['details_manifest'])) == reference_rows
    assert all((root / file).read_bytes() == value for file, value in prefix.items())
    assert len(list((root / 'UNCOMMITTED').iterdir())) == 1


@pytest.mark.parametrize('target', ['checkpoint', 'details', 'rule', 'observation'])
def test_completed_report_cache_rejects_changed_state_bytes_rule_or_plan(tmp_path, target):
    inputs, result, plan = report_case()
    path = tmp_path / 'REPORT.json'
    report = build_research_reports(result, inputs, plan, report_checkpoint_path=path)
    if target == 'checkpoint':
        value = json.loads(path.read_text(encoding='utf-8'))
        value['state']['opportunities'] += 1
        path.write_text(json.dumps(value), encoding='utf-8')
    elif target == 'details':
        row = report['details_manifest']['days'][0]
        (tmp_path / 'REPORT_DETAILS' / row['file']).write_bytes(b'changed')
    elif target == 'rule':
        result['strategy_plan']['strategy']['parameters']['candidate_payload']['identity'] = 'another-rule'
    else:
        plan['horizons'] = [3, 5, 10]
    with pytest.raises(ValueError, match='CHANGED|CONFLICT|INVALID'):
        build_research_reports(result, inputs, plan, report_checkpoint_path=path)


def test_completed_report_is_reused_without_recomputing_or_reemitting(tmp_path, monkeypatch):
    inputs, result, plan = report_case()
    path = tmp_path / 'REPORT.json'
    first = build_research_reports(result, inputs, plan, report_checkpoint_path=path)
    monkeypatch.setattr(_Observations, '__init__', lambda *args: pytest.fail('completed report recomputed'))
    assert build_research_reports(result, inputs, plan, report_checkpoint_path=path, segment_seconds=0) == first


def test_zero_deadline_commits_no_detail_and_external_sink_is_not_transactional(tmp_path):
    inputs, result, plan = report_case()
    path = tmp_path / 'REPORT.json'
    with pytest.raises(SegmentBoundary):
        build_research_reports(result, inputs, plan, report_checkpoint_path=path, segment_seconds=0)
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['state']['account_cursor'] == 0 and not saved['details_manifest']['days']
    with pytest.raises(ValueError, match='OWNS_DETAIL_SINK'):
        build_research_reports(result, inputs, plan, report_checkpoint_path=path, event_sink=lambda row: None)


def test_funnel_interrupted_daily_commit_equals_continuous_counts_and_tail(tmp_path, monkeypatch):
    data = case()
    values = packets(data)
    common = dict(rule_identity=RULE, strategy_id='A', calendar=CALENDAR,
                  expected_decision_sessions=[20240726, 20240730])
    reference_rows = []
    reference = build_signal_funnel_stream_v1(**common, day_packets=values, event_sink=reference_rows.append)
    checkpoint = tmp_path / 'FUNNEL.json'
    original, crashed = OwnReportState.save, False
    def interrupted(self):
        nonlocal crashed
        if len(self.details.days) == 2 and not crashed:
            crashed = True
            raise RuntimeError('FUNNEL_TAIL_UNCOMMITTED')
        return original(self)
    monkeypatch.setattr(OwnReportState, 'save', interrupted)
    with pytest.raises(RuntimeError, match='TAIL_UNCOMMITTED'):
        build_signal_funnel_stream_v1(**common, day_packets=values, report_checkpoint_path=checkpoint)
    monkeypatch.setattr(OwnReportState, 'save', original)
    resumed = build_signal_funnel_stream_v1(**common, day_packets=values, report_checkpoint_path=checkpoint)
    assert resumed['counts'] == reference['counts']
    assert resumed['detail_chain_identity'] == reference['detail_chain_identity']
    assert resumed['counts']['tail_signals'] == 1
    assert list(iter_report_details(resumed['details_manifest'])) == reference_rows
    changed = deepcopy(values)
    changed[0]['scan_days'][0]['rows'][0]['reason'] = 'CHANGED'
    with pytest.raises(ValueError, match='IDENTITY_CONFLICT'):
        build_signal_funnel_stream_v1(**common, day_packets=changed, report_checkpoint_path=checkpoint)


def test_sharded_report_and_funnel_resume_read_only_new_days_and_reject_source_byte_tamper(tmp_path, monkeypatch):
    from test_universe_evidence_v2 import case as actual_case
    from chanlun_trader.research_factory.research_rule_strategy_v4 import ResearchRuleStrategyV4
    from chanlun_trader.research_factory.universe_account_inputs_v1 import UniverseAccountInputsV1
    from chanlun_trader.research_factory.universe_execution_artifacts_v1 import ArtifactSequence, hydrated_result
    bundle, window, rule, raw = actual_case(tmp_path / 'job', scored=True)
    inputs = UniverseAccountInputsV1(bundle, window, stage='ACCOUNT', required_fields=('close',), warmup_bars=1)
    result, plan = hydrated_result(raw), default_observation_plan(window)
    checkpoint = tmp_path / 'REPORT.json'
    original_save = OwnReportState.save
    def interrupt(self):
        if len(self.details.days) == 3:
            raise RuntimeError('REPORT_REAL_SHARDS_INTERRUPTED')
        return original_save(self)
    monkeypatch.setattr(OwnReportState, 'save', interrupt)
    with pytest.raises(RuntimeError, match='SHARDS_INTERRUPTED'):
        build_research_reports(result, inputs, plan, report_checkpoint_path=checkpoint)
    monkeypatch.setattr(OwnReportState, 'save', original_save)
    seen, original_read = [], ArtifactSequence.read_day
    def tracked(self, index):
        if self.manifest['root'] == raw['artifacts']['root']:
            seen.append(index)
        return original_read(self, index)
    monkeypatch.setattr(ArtifactSequence, 'read_day', tracked)
    report = build_research_reports(result, inputs, plan, report_checkpoint_path=checkpoint)
    assert seen and min(seen) >= 2
    frozen = ResearchRuleStrategyV4(rule, strategy_id='auditv2')
    common = dict(rule_identity=frozen.rule_identity, strategy_id='auditv2', calendar=window['calendar'],
                  expected_decision_sessions=window['calendar'][60:])
    funnel_path = tmp_path / 'FUNNEL.json'
    monkeypatch.setattr(OwnReportState, 'save', interrupt)
    with pytest.raises(RuntimeError, match='SHARDS_INTERRUPTED'):
        build_signal_funnel_stream_v1(**common, day_packets=funnel_day_packets_v1(raw, window['calendar']),
                                     report_checkpoint_path=funnel_path)
    monkeypatch.setattr(OwnReportState, 'save', original_save)
    seen.clear()
    funnel = build_signal_funnel_stream_v1(**common, day_packets=funnel_day_packets_v1(raw, window['calendar']),
                                         report_checkpoint_path=funnel_path)
    assert seen and min(seen) >= 1
    assert funnel['counts']['opportunity_signals'] == report['signal']['opportunity_security_days']
    assert funnel['counts']['tail_signals'] == 3
    row = raw['artifacts']['days'][0]
    from pathlib import Path
    file = Path(raw['artifacts']['root']) / row['file']
    file.write_bytes(file.read_bytes() + b'changed-source-prefix')
    with pytest.raises(ValueError, match='ARTIFACT_BYTES_CHANGED'):
        build_research_reports(result, inputs, plan, report_checkpoint_path=checkpoint)
    with pytest.raises(ValueError, match='ARTIFACT_BYTES_CHANGED'):
        build_signal_funnel_stream_v1(**common, day_packets=funnel_day_packets_v1(raw, window['calendar']),
                                     report_checkpoint_path=funnel_path)
