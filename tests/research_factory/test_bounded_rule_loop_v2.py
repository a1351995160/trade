"""V2 模型合同、公共状态账户、原治理和档案贯通；使用明确合成输入。"""
from copy import deepcopy
import json

import pytest

from chanlun_trader.research_factory.bounded_candidate_v1 import candidate_capabilities, validate_candidate
from chanlun_trader.research_factory.bounded_model_v1 import candidate_output_schema
from chanlun_trader.research_factory.bounded_research_v1 import _read
from chanlun_trader.research_factory.bounded_research_v2 import (
    BoundedResearchSessionV2, diagnose_rule_result, fixed_reference_payload,
)
from chanlun_trader.research_factory.rule_account_backend_v2 import rule_input_identity
from chanlun_trader.research_factory.research_rule_strategy_v2 import CAPABILITY, ResearchRuleStrategyV2
from chanlun_trader.research_factory.strategy_qualification_v1 import BoundedStrategyArchiveV1
from test_historical_process_v1 import historical_fixture
from test_research_rule_strategy_v2 import payload


def test_explicit_capability_keeps_legacy_candidates_separate():
    assert isinstance(validate_candidate(payload(), strategy_id='rule', capability=CAPABILITY), ResearchRuleStrategyV2)
    with pytest.raises(ValueError, match='FIELDS_INVALID'):
        validate_candidate(payload(), strategy_id='legacy')
    schema = candidate_output_schema(candidate_capabilities(capability=CAPABILITY))
    assert schema['properties']['version']['enum'] == [CAPABILITY]
    assert schema['properties']['cooldown_sessions']['maximum'] == 60
    assert schema['additionalProperties'] is False
    assert schema['$defs']['node']['anyOf']
    assert 'threshold' in candidate_output_schema(candidate_capabilities())['required']


def test_seed_feedback_is_frozen_qualitative_whitelist():
    from chanlun_trader.research_factory.bounded_research_v2 import _seed_feedback
    from chanlun_trader.research_factory.research_screening_v1 import REASONS
    value = [{'source_history_hash': 'a'*64, 'entries': [
        {'reason_code': 'COST_STRESS_FAILED', 'high_level_reason': REASONS['COST_STRESS_FAILED']}]}]
    assert _seed_feedback({'seed_failure_knowledge': value}) == value
    altered = deepcopy(value)
    altered[0]['entries'][0]['high_level_reason'] = 'actual return was 40%'
    with pytest.raises(ValueError, match='SEED_FEEDBACK'):
        _seed_feedback({'seed_failure_knowledge': altered})


def test_invoker_selects_rule_schema_and_reuses_verified_model_receipt(tmp_path, monkeypatch):
    from chanlun_trader.research_factory import bounded_model_v1 as model
    from chanlun_trader.research_factory.codex_backend import CodexInvocationResultV1
    calls = []
    class Executor:
        def __init__(self, executable, *, design_only):
            assert design_only
        def execute(self, request, **kwargs):
            schema = json.loads(kwargs['output_schema_path'].read_text(encoding='utf-8'))
            assert schema['properties']['version']['enum'] == [CAPABILITY]
            assert 'cooldown_sessions' in request.prompt
            calls.append(request.prompt)
            return CodexInvocationResultV1(response_text=json.dumps(payload()), stdout='{"type":"turn.completed"}\n')
    monkeypatch.setattr(model, 'SubprocessCodexExecutorV1', Executor)
    monkeypatch.setattr(model, 'discover_codex_executable', lambda: 'synthetic-executor')
    context = {'capabilities': candidate_capabilities(capability=CAPABILITY)}
    invoker = model.BoundedCodexInvokerV1()
    for _ in range(2):
        proposal = invoker.invoke(context, staging_dir=tmp_path/'model', timeout_seconds=1)
        validate_candidate(proposal, strategy_id='candidate', capability=CAPABILITY)
    assert len(calls) == 1


def session_fixture(tmp_path, monkeypatch, attempts=2):
    window, bundle = historical_fixture()
    identity = rule_input_identity(bundle, window)
    bundle['input_identity'] = identity
    manifest = {'profile':'SYNTHETIC', 'input_identity':identity,
                'candidate_capability':CAPABILITY, 'window':window}
    session = BoundedResearchSessionV2.create(tmp_path/'session', objective_id='RULE_TEST',
        input_manifest=manifest, approval_statement='合成有限研究测试', max_attempts=attempts)
    def loader(manifest, strategy):
        assert isinstance(strategy, ResearchRuleStrategyV2)
        return deepcopy(bundle), deepcopy(bundle['events'])
    return session, loader


class SyntheticInvoker:
    def __init__(self, proposals):
        self.proposals = proposals
        self.contexts = []

    def invoke(self, context, **kwargs):
        self.contexts.append(deepcopy(context))
        return deepcopy(self.proposals[len(self.contexts)-1])


def test_stateful_candidates_archive_and_duplicate_are_budgeted(tmp_path, monkeypatch):
    session, loader = session_fixture(tmp_path, monkeypatch)
    model = SyntheticInvoker([payload(), payload()])
    status = session.run(loader=loader, invoker=model)
    assert status['status'] == 'ATTEMPT_BUDGET_EXHAUSTED', status
    assert len(model.contexts) == 2
    assert model.contexts[1]['failure_knowledge']
    assert model.contexts[1]['previous_designs']
    assert _read(session.path('CANDIDATE_002','DECISION.json'))['reason'] == 'DUPLICATE_RULE'
    result = _read(session.path('CANDIDATE_001','RESULT.json'))
    assert result['reconciliation']['passed']
    assert result['final_account_checkpoint']['rule_states']
    assert 'chain' not in result
    archive_service = BoundedStrategyArchiveV1(tmp_path/'archives')
    archive = archive_service.freeze(session.root, 'CANDIDATE_001')
    report = archive_service.review(archive['strategy_id'])
    assert report['current_rule_executable']
    assert report['historical_account_state'] == 'COMPLETE'
    assert not report['strategy_qualified']
    before = session.path('search_budget_registry.json').read_bytes()
    assert session.run(loader=loader, invoker=model)['status'] == 'ATTEMPT_BUDGET_EXHAUSTED'
    assert session.path('search_budget_registry.json').read_bytes() == before
    assert len(model.contexts) == 2


def test_unsupported_and_fixed_reference_do_not_run_candidate_account(tmp_path, monkeypatch):
    session, loader = session_fixture(tmp_path, monkeypatch)
    model = SyntheticInvoker([{'python':'arbitrary code'}, fixed_reference_payload()])
    result = session.run(loader=loader, invoker=model)
    assert result['status'] == 'ATTEMPT_BUDGET_EXHAUSTED', result
    assert not session.path('CANDIDATE_001','EXECUTION_STARTED.json').exists()
    assert not session.path('CANDIDATE_002','EXECUTION_STARTED.json').exists()
    assert _read(session.path('CANDIDATE_001','DECISION.json'))['reason'] == 'UNSUPPORTED_CANDIDATE'
    assert _read(session.path('CANDIDATE_002','DECISION.json'))['reason'] == 'DUPLICATE_RULE'


def test_model_interruption_has_no_free_retry(tmp_path, monkeypatch):
    session, loader = session_fixture(tmp_path, monkeypatch, attempts=1)
    class Broken:
        calls = 0
        def invoke(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError('INJECTED_MODEL_INTERRUPTION')
    model = Broken()
    status = session.run(loader=loader, invoker=model)
    assert status['status'] == 'BLOCKED'
    budget = session.path('search_budget_registry.json').read_bytes()
    session.run(loader=loader, invoker=model)
    assert model.calls == 1
    assert session.path('search_budget_registry.json').read_bytes() == budget


def test_result_metrics_tamper_is_not_a_strategy_failure(tmp_path, monkeypatch):
    session, loader = session_fixture(tmp_path, monkeypatch, attempts=1)
    status = session.run(loader=loader, invoker=SyntheticInvoker([payload()]))
    assert status['status'] == 'ATTEMPT_BUDGET_EXHAUSTED', status
    result = _read(session.path('CANDIDATE_001','RESULT.json'))
    result['metrics']['total_fees'] += 1
    report = diagnose_rule_result(result, trial_id='CANDIDATE_001')
    assert report['account_state'] == 'ACCOUNT_INCOMPLETE'
    assert 'EXPLORATORY_LOSS' not in report['reason_codes']


def test_changed_source_blocks_before_account_or_model(tmp_path, monkeypatch):
    from chanlun_trader.research_factory import bounded_research_v1 as old
    from chanlun_trader.research_factory import bounded_research_v2 as new
    session, loader = session_fixture(tmp_path, monkeypatch, attempts=1)
    monkeypatch.setattr(old, 'source_identity', lambda: 'INTENTIONAL_SOURCE_CHANGE')
    monkeypatch.setattr(new, 'source_identity', lambda: 'INTENTIONAL_SOURCE_CHANGE')
    model = SyntheticInvoker([payload()])
    result = session.run(loader=loader, invoker=model)
    assert result['status'] == 'BLOCKED' and result['reason'] == 'BOUNDED_SOURCE_CHANGED'
    assert not model.contexts
    assert not session.path('REFERENCE', 'EXECUTION_STARTED.json').exists()


@pytest.mark.parametrize('corruption', ['missing_day', 'equity_delta'])
def test_complete_diagnostic_requires_full_window_and_reconciled_equity(corruption):
    from test_rule_account_backend_v2 import execute
    window, bundle = historical_fixture()
    result = execute(window, bundle)
    if corruption == 'missing_day':
        result['daily_accounts'] = result['daily_accounts'][1:]
        result['reconciliation']['days'] -= 1
    else:
        result['daily_accounts'][0]['equity_difference'] = 99
    report = diagnose_rule_result(result, trial_id='broken')
    assert report['account_state'] != 'COMPLETE'
    assert 'EXPLORATORY_LOSS' not in report['reason_codes']
