"""合成持久原件的只读反馈回归；不执行模型、worker或真实行情。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory.budget import BudgetExhaustedError
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.diagnosis_research_v4 import DiagnosisResearchV4
from chanlun_trader.research_factory.final_exploration_queue_v1 import FinalExplorationQueueV1
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
from chanlun_trader.research_factory.strategy_submission_v1 import public_rule_factory
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile
from chanlun_trader.research_factory.universe_research_report_v2 import default_observation_plan
from test_business_validation_protocol_v1 import PublicServiceSpy, contract


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def size(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8'))


def filesystem(root):
    return {str(path.relative_to(root)): digest(path) for path in root.rglob('*') if path.is_file()}


def synthetic_research(tmp_path, *, count=1, ready=False):
    """仅替换外部服务和冻结配置；实际 _records/_screen/queue.evidence 均执行。"""
    snapshot = capabilities()
    frozen = contract(snapshot)
    if not ready:
        frozen['final_criteria']['thresholds_each_stage']['base_complete_round_trips_minimum'] = 121
        frozen['criteria_source']['content_hash'] = stable_hash(frozen['final_criteria'])
        frozen['content_hash'] = stable_hash({key: value for key, value in frozen.items() if key != 'content_hash'})
    template = {'version': 'FULL_UNIVERSE_SUBMISSION_V4', 'phase': 'EXPLORATION',
        'universe_id': 'full_registered', 'dataset_id': 'exploration', 'account_scope': 'DATA_QUALIFIED',
        'feature_start': 20200101, 'account_start': 20200401, 'account_end': 20210101,
        'initial_cash': 50000, 'max_positions': 2, 'max_symbol_exposure_bps': 10000,
        'costs': ['BASE', 'STRESS'], 'benchmark': 'CASH_AND_PRICE_REFERENCE', 'purpose': 'EXPLORATORY',
        'authorization_ref': 'SYNTHETIC_ONLY', 'execution_profile': execution_profile(SEGMENTED_PROFILE, 252)}
    template['observation_plan'] = default_observation_plan(template)
    final_template = {**deepcopy(template), 'account_end': 20220401,
        'execution_profile': execution_profile(SEGMENTED_PROFILE, 504)}
    final_template['observation_plan'] = default_observation_plan(final_template)
    config = {'contract': frozen, 'template': template, 'final_exploration_template': final_template,
        'model_limits': {'context_max_bytes': 200000}, 'history_references': []}
    research = object.__new__(DiagnosisResearchV4)
    research.root = tmp_path / 'diagnosis_v4'
    research.config = lambda: deepcopy(config)
    research.campaign = SimpleNamespace(peek_status=lambda: {'operations': {}})
    tasks = {}
    research.submission = SimpleNamespace(root=tmp_path / 'synthetic_public',
        _task=lambda task_id: deepcopy(tasks[task_id]))
    def confirmation_read_forbidden(*args, **kwargs):
        raise AssertionError('CONFIRMATION must not be read by design context')
    research.confirmation = SimpleNamespace(status=confirmation_read_forbidden,
        human_results=confirmation_read_forbidden, load=confirmation_read_forbidden)
    write(research.root / 'business_validation' / 'RESULT.json',
          {'CONFIRMATION_SECRET': 'INDEPENDENT_NEVER_DESIGN', 'net_return': 987.654321})
    for index in range(1, count + 1):
        candidate = 'CANDIDATE_' + str(index).zfill(4)
        proposal = deepcopy(snapshot['examples']['multi_indicator_ranked'])
        identity = public_rule_factory(proposal, candidate).rule_identity
        source = research.root / candidate.lower()
        source_screen = {'passed': True, 'final_exploration_ready': False}
        write(source / 'SCREEN.json', source_screen)
        write(source / 'PROPOSAL.json', proposal)
        record = {'candidate_id': candidate, 'batch_id': 'BATCH_' + str(index).zfill(4), 'status': 'SCREENED',
            'rule_identity': identity, 'hypothesis': '趋势假设' * 300, 'change_reason': '机制修订' * 300,
            'feedback': {'codes': ['INITIAL_SCREEN_PASSED']},
            'screen': {**source_screen, 'identity': stable_hash(source_screen)},
            'artifacts': {path.name: digest(path) for path in source.glob('*.json')}}
        write(source / 'RECORD.json', record)
        request = {**deepcopy(final_template), 'rule': proposal, 'strategy_id': candidate,
            'research_binding_ref': {'binding_id': stable_hash(['SYNTHETIC_ONLY', candidate])}}
        public = PublicServiceSpy(research.submission.root, sessions=504)
        preview = public.preview(request)
        task = public.freeze(request, preview['preview_identity'])
        outcome = public.advance(task['task_id'])
        # 通用协议夹具不需要该字段；真实 controller 的筛选明确使用账户净收益。
        for reference in outcome['reports'].values():
            report_path, final_path = Path(reference['research_report']), Path(reference['final_report'])
            report = json.loads(report_path.read_text(encoding='utf-8'))
            report['account']['net_return'] = report['account']['final_equity'] / 50000 - 1
            report['report_identity'] = stable_hash({key: value for key, value in report.items()
                                                     if key != 'report_identity'})
            write(report_path, report)
            full = json.loads(final_path.read_text(encoding='utf-8'))
            full['research']['account_and_signal'] = report
            write(final_path, full)
            reference['research_report_sha256'], reference['final_report_sha256'] = digest(report_path), digest(final_path)
        tasks[task['task_id']] = task
        directory = research.root / 'final_exploration' / candidate
        for name, value in {'REQUEST': request, 'PREVIEW': preview, 'TASK': {'task_id': task['task_id']},
                            'PUBLIC_RESULT': outcome}.items():
            write(directory / (name + '.json'), value)
        screen = research._screen(directory, task, outcome, config)
        write(directory / 'SCREEN.json', screen)
        evidence = {'version': 'FINAL_EXPLORATION_QUEUE_V1', 'candidate_id': candidate,
            'batch_id': 'FINAL_' + candidate, 'rule_identity': identity,
            'source_record_identity': stable_hash(record), 'final_template_identity': stable_hash(final_template),
            'task_id': task['task_id'], 'job_path': task['job_path'], 'input_identity': task['input_identity'],
            'screen': screen, 'screen_identity': stable_hash(screen),
            'status': 'FINAL_EXPLORATION_READY' if screen['final_exploration_ready'] else 'FINAL_EXPLORATION_FAILED',
            'artifacts': {path.name: digest(path) for path in directory.glob('*.json')}}
        evidence['identity'] = stable_hash(evidence)
        write(directory / 'EVIDENCE.json', evidence)
    return research, config


@pytest.mark.parametrize('ready', [False, True])
def test_next_model_context_uses_canonical_qualitative_final_feedback_only(tmp_path, ready):
    research, config = synthetic_research(tmp_path, ready=ready)
    before = filesystem(tmp_path)
    evidence = FinalExplorationQueueV1(research).evidence()
    context = research._context(config, research._records(), 2)
    feedback = context['history']['final_exploration']
    assert feedback['total_evidence'] == 1
    assert feedback['ready_count'] == int(ready)
    assert feedback['failed_count'] == int(not ready)
    assert feedback['evidence_chain_identity'] == stable_hash([evidence[0]['identity']])
    recent = feedback['recent_evidence'][0]
    assert recent['evidence_identity'] == evidence[0]['identity']
    assert recent['evidence_sha256'] == evidence[0]['evidence_sha256']
    assert recent['source_record_identity'] == stable_hash(research._records()[0])
    code = 'FINAL_EXPLORATION_BASE_COMPLETE_ROUND_TRIPS_MINIMUM_NOT_MET'
    assert (code in recent['feedback_codes']) is not ready
    assert feedback['feedback_counts'].get(code, 0) == int(not ready)
    encoded = json.dumps(context)
    for forbidden in ('CONFIRMATION_SECRET', 'INDEPENDENT_NEVER_DESIGN', '987.654321',
                      'metrics', 'final_equity', 'reports', 'PUBLIC_RESULT', 'net_profit'):
        assert forbidden not in encoded
    assert filesystem(tmp_path) == before


def test_changed_final_artifact_cannot_supply_feedback(tmp_path):
    research, config = synthetic_research(tmp_path)
    write(research.root / 'final_exploration' / 'CANDIDATE_0001' / 'SCREEN.json', {})
    with pytest.raises(ValueError, match='FINAL_EXPLORATION_ARTIFACT_CHANGED'):
        research._context(config, research._records(), 2)


def test_context_cap_trims_details_but_keeps_complete_final_counts_and_identity(tmp_path):
    research, config = synthetic_research(tmp_path, count=3)
    records = research._records()
    complete = research._context(config, records, 4)
    expected = deepcopy(complete)
    expected['history']['recent_records'] = []
    expected['history']['omitted_record_count'] = 3
    final = expected['history']['final_exploration']
    final['recent_evidence'] = final['recent_evidence'][-1:]
    final['omitted_evidence_count'] = 2
    config['model_limits']['context_max_bytes'] = size(expected)
    before = filesystem(tmp_path)
    bounded = research._context(config, records, 4)
    assert bounded == expected
    assert size(bounded) <= config['model_limits']['context_max_bytes']
    final = bounded['history']['final_exploration']
    assert final['total_evidence'] == final['failed_count'] == 3
    assert final['feedback_counts']['FINAL_EXPLORATION_FAILED'] == 3
    assert final['evidence_chain_identity'] == complete['history']['final_exploration']['evidence_chain_identity']
    assert bounded['history']['record_chain_identity'] == stable_hash([stable_hash(row) for row in records])
    assert filesystem(tmp_path) == before
    config['model_limits']['context_max_bytes'] = 100
    with pytest.raises(BudgetExhaustedError, match='CONTEXT_CAPACITY_INSUFFICIENT'):
        research._context(config, records, 4)
    assert filesystem(tmp_path) == before


def test_design_handover_includes_verified_final_feedback_without_confirmation_read(tmp_path):
    research, config = synthetic_research(tmp_path)
    config['candidates_per_batch'] = 1
    research.campaign.peek_status = lambda: {'operations': {}, 'paused': False, 'expired': False,
        'authorization': {'resource_limits': {'candidate_attempts': 2}, 'max_batches': 2}}
    confirmation_reads = []
    def independent_status():
        confirmation_reads.append('status')
        return {'status': 'BUSINESS_EVIDENCE_RECORDED', 'business_goal_met': False,
            'CONFIRMATION_SECRET': 'INDEPENDENT_NEVER_DESIGN', 'net_return': 987.654321}
    research.confirmation.status = independent_status
    research.confirmation.protocol_path = research.root / 'business_validation' / 'RESULT.json'
    expected = research._final_exploration_feedback(research._records())
    before = filesystem(tmp_path)
    handover = research.handover(design=True)
    assert filesystem(tmp_path) == before
    assert confirmation_reads == [], 'design handover must not read confirmation.status'
    assert handover['final_exploration'] == expected
    assert handover['final_exploration']['failed_count'] == 1
    assert handover['independent_results_omitted'] is True
    encoded = json.dumps(handover)
    for forbidden in ('CONFIRMATION_SECRET', 'INDEPENDENT_NEVER_DESIGN', '987.654321',
                      'metrics', 'final_equity', 'reports', 'PUBLIC_RESULT', 'net_profit'):
        assert forbidden not in encoded
