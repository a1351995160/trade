"""最终探索工程回归：合成初筛原件、真实公共504日受限worker，不代表策略有效。"""
from copy import deepcopy
from pathlib import Path

import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.diagnosis_research_v4 import DiagnosisResearchV4
from chanlun_trader.research_factory.exploration_governance import immutable, read_json
from chanlun_trader.research_factory.final_exploration_queue_v1 import (
    FinalExplorationQueueV1, validate_final_exploration_template,
)
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile
from chanlun_trader.research_factory.universe_research_report_v2 import default_observation_plan
from test_continuous_submission_v1 import continuous_public_case


def case(tmp_path, monkeypatch, *, final=True, passed=True):
    import test_continuous_submission_v1 as public
    import test_universe_submission_v1 as raw
    from universe_test_fixture_v1 import fixture
    monkeypatch.setattr(raw, 'fixture', lambda: fixture(days_count=564,
        prices=[12 + number * .004 for number in range(504)]))
    original = public.long_public_case
    def with_profiles(path):
        service, request, authority, accesses = original(path)
        authority['execution_profiles'] = [execution_profile(SEGMENTED_PROFILE, sessions, purpose)
            for sessions in (20, 504) for purpose in
            ('RESEARCH_ACCOUNT', 'RESEARCH_PREPARATION', 'RESEARCH_VERIFICATION', 'RESEARCH_REPORT')]
        return service, request, authority, accesses
    monkeypatch.setattr(public, 'long_public_case', with_profiles)
    submission, request, campaign, accesses, restore = continuous_public_case(tmp_path,
        exploration_minimum_sessions=1, candidates_budget=2)
    template = {key: deepcopy(value) for key, value in request.items()
        if key not in {'strategy_id', 'rule', 'research_binding_ref'}}
    final_template = deepcopy(template)
    final_template['execution_profile'] = execution_profile(SEGMENTED_PROFILE, 504)
    calendar = read_json(tmp_path / 'data' / 'calendar.json')
    template['account_end'] = calendar[79]
    template['observation_plan'] = default_observation_plan(template)
    contract = campaign.peek_status()['base_authorization']['scope_policy']['summary']['contract']
    research = DiagnosisResearchV4.create(campaign, submission, contract=contract, template=template,
        model_limits={'timeout_seconds': 1, 'max_tokens': 10, 'max_cost_microunits': 10,
                      'context_max_bytes': 200000},
        **({'final_exploration_template': final_template} if final else {}))
    directory = research.root / 'candidate_0001'
    rule = request['rule']
    from chanlun_trader.research_factory.research_rule_strategy_v4 import ResearchRuleStrategyV4
    identity = ResearchRuleStrategyV4(rule, strategy_id='CANDIDATE_0001').rule_identity
    campaign.reserve_operation(operation_id='CANDIDATE_0001_CANDIDATE', batch_id='BATCH_0001',
        stage='EXPLORATION', kind='CANDIDATE', subject_identity=stable_hash('SYNTHETIC_INITIAL_SCREEN'),
        upper_bounds={'candidate_attempts': 1, 'wall_seconds': 1})
    campaign.start_operation('CANDIDATE_0001_CANDIDATE')
    immutable(directory / 'CONTEXT.json', {'history': {'recent_records': []},
        'allocated_mechanisms': contract['scope']['mechanism_combinations'][0],
        'fixture': 'SYNTHETIC_INITIAL_SCREEN_NOT_REAL_RESEARCH'})
    immutable(directory / 'PROPOSAL.json', rule)
    screen = {'passed': passed, 'final_exploration_ready': False,
              'fixture': 'SYNTHETIC_INITIAL_SCREEN_NOT_REAL_RESEARCH'}
    immutable(directory / 'SCREEN.json', screen)
    research._record(directory, 'CANDIDATE_0001', 'BATCH_0001', status='SCREENED',
        feedback=['SYNTHETIC_INITIAL_SCREEN'], rule_identity=identity,
        screen={'passed': passed, 'identity': stable_hash(screen), 'final_exploration_ready': False})
    return research, final_template, accesses, restore


def snapshot(root):
    return {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_missing_final_template_waits_without_model_or_state_writes(tmp_path, monkeypatch):
    research, _, accesses, _ = case(tmp_path, monkeypatch, final=False)
    queue = FinalExplorationQueueV1(research)
    before = snapshot(tmp_path)
    assert queue.status()['status'] == 'WAITING_FINAL_EXPLORATION_DATA'
    assert queue.evidence() == [] and queue.verified_evidence('CANDIDATE_0001') is None
    assert snapshot(tmp_path) == before and accesses == []
    assert queue.advance()['status'] == 'WAITING_FINAL_EXPLORATION_DATA'
    assert research.campaign.peek_status()['used']['model_calls'] == 0
    assert not list(tmp_path.rglob('*_START.json'))


@pytest.mark.parametrize('change', ['scope', 'short_profile', 'narrow_window', 'observation'])
def test_final_template_cannot_retune_scope_or_lower_final_sessions(tmp_path, monkeypatch, change):
    research, final, _, _ = case(tmp_path, monkeypatch, final=False)
    if change == 'scope':
        final['max_positions'] += 1
    elif change == 'short_profile':
        final['execution_profile'] = execution_profile(SEGMENTED_PROFILE, 20)
    elif change == 'narrow_window':
        final['account_start'] = final['account_end']
    else:
        final['observation_plan']['horizons'] = [1]
    with pytest.raises((ValueError, PermissionError), match='FINAL_EXPLORATION_'):
        validate_final_exploration_template(final, research.config()['template'], research.config()['contract'])


def test_failed_initial_screen_never_enters_final_queue(tmp_path, monkeypatch):
    research, _, accesses, _ = case(tmp_path, monkeypatch, passed=False)
    queue = FinalExplorationQueueV1(research)
    assert queue.status()['pending'] == []
    assert queue.advance()['status'] == 'NO_FINAL_EXPLORATION_PENDING'
    assert research.campaign.peek_status()['used']['account_jobs'] == 0 and accesses == []


def test_real_public_504_promotion_preserves_rule_record_and_one_worker_bound(tmp_path, monkeypatch):
    from chanlun_trader import synthetic_batch_resources as resources
    from chanlun_trader.research_factory import universe_scan_service_v1 as scans
    research, _, _, restore = case(tmp_path, monkeypatch)
    queue = FinalExplorationQueueV1(research)
    record_path = research.root / 'candidate_0001' / 'RECORD.json'
    original_record = record_path.read_bytes()
    original = resources.run_bounded_worker
    launches = []
    def actual_worker(*args, **kwargs):
        launches.append(deepcopy(kwargs.get('execution')))
        return original(*args, **kwargs)
    monkeypatch.setattr(resources, 'run_bounded_worker', actual_worker)
    monkeypatch.setattr(scans, 'run_bounded_worker', actual_worker)
    for number in range(60):
        before = len(launches)
        result = queue.advance()
        assert len(launches) - before <= 1
        assert result['dispatched_segments'] == len(launches) - before
        assert result['model_calls'] == 0
        assert record_path.read_bytes() == original_record
        if number == 2:
            research = DiagnosisResearchV4(research.campaign, restore())
            queue = FinalExplorationQueueV1(research)
        if result['status'] in {'FINAL_EXPLORATION_READY', 'FINAL_EXPLORATION_FAILED'}:
            break
        assert result['status'] not in {'WAITING_FINAL_EXPLORATION_DATA', 'EVIDENCE_BLOCKED'}, result
    else:
        pytest.fail(str(result))
    evidence = queue.verified_evidence('CANDIDATE_0001')
    assert evidence['request']['execution_profile']['account_sessions'] == 504
    assert evidence['request']['rule'] == read_json(research.root / 'candidate_0001' / 'PROPOSAL.json')
    assert evidence['outcome']['status'] == 'ACCOUNT_VERIFIED'
    assert evidence['screen']['final_checks']['sessions'] is True
    assert evidence['status'] == 'FINAL_EXPLORATION_FAILED'  # 平滑合成趋势不足100完整回合。
    job = read_json(evidence['job_path'])
    for name in job['plans']:
        report = read_json(Path(evidence['job_path']).parent / (name + '_RESEARCH_REPORT.json'))
        assert report['account']['sessions'] == 504
    view = research.campaign.peek_status()
    assert view['used']['model_calls'] == 0 and view['used']['account_jobs'] == 2
    assert {row['stage'] for row in view['operations'].values()} == {'EXPLORATION'}
    assert {row['batch_id'] for row in view['operations'].values() if row['kind'] != 'CANDIDATE'} == {'FINAL_CANDIDATE_0001'}
    before, count = snapshot(tmp_path), len(launches)
    queue.status()
    queue.evidence()
    assert snapshot(tmp_path) == before and len(launches) == count
    assert queue.advance()['status'] == 'NO_FINAL_EXPLORATION_PENDING'
    assert len(launches) == count and record_path.read_bytes() == original_record
    report_path = Path(evidence['job_path']).parent / (next(iter(job['plans'])) + '_RESEARCH_REPORT.json')
    report_path.write_text('{}', encoding='utf-8')
    with pytest.raises((ValueError, KeyError), match='CHANGED|report_identity'):
        queue.evidence()
