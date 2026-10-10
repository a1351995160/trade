"""持续研究工程回归；模型为明确标记的合成替身，账户使用真实公共受限进程。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.diagnosis_research_v4 import DiagnosisResearchV4
from chanlun_trader.research_factory.exploration_governance import immutable, read_json
from test_continuous_submission_v1 import continuous_public_case


class SyntheticModel:
    def __init__(self, rules, *, invalid=False, unknown=False, overrun=False):
        self.rules, self.calls = deepcopy(rules), 0
        self.invalid, self.unknown, self.overrun = invalid, unknown, overrun

    def enforce_budget_limits(self, *, max_tokens, max_cost_microunits):
        return {'enforced': True, 'max_tokens': max_tokens, 'max_cost_microunits': max_cost_microunits,
                'evidence_identity': stable_hash('SYNTHETIC_NO_PROVIDER_OR_BILLING'), 'synthetic': True}

    def can_recover(self, folder, context_hash):
        return (folder / 'INVOCATION.json').is_file() and read_json(folder / 'INVOCATION.json')['context_hash'] == context_hash

    def invoke(self, context, *, staging_dir, timeout_seconds):
        if (staging_dir / 'INVOCATION.json').exists():
            if self.invalid:
                raise ValueError('SYNTHETIC_INVALID_RESPONSE')
            return read_json(staging_dir / 'RESPONSE.json')
        self.calls += 1
        if self.unknown:
            raise TimeoutError('SYNTHETIC_UNKNOWN_NO_RECEIPT')
        proposal = self.rules[min(self.calls-1, len(self.rules)-1)]
        immutable(staging_dir / 'INVOCATION.json', {'synthetic': True, 'context_hash': stable_hash(context),
            'response_hash': stable_hash(proposal), 'usage': {'input_tokens': 1, 'output_tokens': 1,
                'total_tokens': 2, 'cost_microunits': 2000 if self.overrun else 1, 'model_calls': 1}})
        immutable(staging_dir / 'RESPONSE.json', proposal)
        if self.invalid:
            raise ValueError('SYNTHETIC_INVALID_RESPONSE')
        return proposal


def research_case(tmp_path, **model_options):
    submission, request, campaign, accesses, restore_submission = continuous_public_case(
        tmp_path, exploration_minimum_sessions=1, candidates_budget=2)
    rule = request['rule']
    second = deepcopy(rule)
    second['buy']['op'] = 'lt'
    second['hypothesis'] = '同一全池的反转而非追涨机制'
    second['change_reason'] = '检验与第一批方向相反的可否定假设'
    model = SyntheticModel([rule, second], **model_options)
    contract = campaign.peek_status()['base_authorization']['scope_policy']['summary']['contract']
    template = {key: deepcopy(value) for key, value in request.items()
                if key not in {'strategy_id', 'rule', 'research_binding_ref'}}
    research = DiagnosisResearchV4.create(campaign, submission, contract=contract, template=template,
        model_limits={'timeout_seconds': 10, 'max_tokens': 100, 'max_cost_microunits': 100,
                      'context_max_bytes': 200000}, invoker=model, candidates_per_batch=1)
    return research, model, accesses, restore_submission


def test_two_batches_use_actual_public_workers_and_restore_original_state(tmp_path, monkeypatch):
    from chanlun_trader import synthetic_batch_resources as resources
    from chanlun_trader.research_factory import universe_scan_service_v1 as scans
    research, model, accesses, restore_submission = research_case(tmp_path)
    launches = []
    original = resources.run_bounded_worker
    def bounded(*args, **kwargs):
        launches.append(deepcopy(kwargs['execution']))
        return original(*args, **kwargs)
    monkeypatch.setattr(resources, 'run_bounded_worker', bounded)
    monkeypatch.setattr(scans, 'run_bounded_worker', bounded)
    assert research.status()['status'] == 'CREATED'
    assert research.advance()['started'] is False and model.calls == 0 and not launches
    research.start()
    for number in range(80):
        before = len(launches)
        result = research.advance()
        assert len(launches) - before <= 1
        assert result.get('dispatched_segments', 0) == len(launches) - before
        assert result['status'] != 'RECONCILIATION_REQUIRED', result
        if number == 3:
            research = DiagnosisResearchV4(research.campaign, restore_submission(), invoker=model)
        if len(research.status()['attempts']) == 2:
            break
    else:
        pytest.fail(str(result))
    state = research.status()
    assert model.calls == 2 and len(state['attempts']) == 2
    assert {row['batch_id'] for row in state['attempts']} == {'BATCH_0001', 'BATCH_0002'}
    assert len({row['rule_identity'] for row in state['attempts']}) == 2
    assert all(row['status'] == 'SCREENED' for row in state['attempts'])
    assert state['goal_complete'] is False and state['strategy_qualified'] is False
    assert state['paper_actual_days'] == 0
    assert state['budget']['used']['model_calls'] == 2 and state['budget']['used']['account_jobs'] == 4
    assert state['budget']['used']['data_experiments'] == 2 and state['budget']['used']['verification_jobs'] == 4
    assert state['budget']['batch_count'] == 2
    context = read_json(research.root / 'candidate_0002' / 'CONTEXT.json')
    assert context['history']['total_attempts'] == 1 and context['history']['feedback_counts']
    assert context['evidence_boundary'] == 'EXPLORATION_QUALITATIVE_ONLY_NO_CONFIRMATION_RESULTS_OR_DATA'
    assert context['allocated_mechanisms'] != read_json(research.root / 'candidate_0001' / 'CONTEXT.json')['allocated_mechanisms']
    before, calls = deepcopy(state['budget']), len(launches)
    assert research.advance()['status'] == 'WAITING_TOTAL_AUTHORIZATION'
    assert research.status()['budget'] == before and len(launches) == calls and model.calls == 2
    assert research.confirmation_material()['profile'] == 'SYNTHETIC'


def test_invalid_paid_model_output_is_retained_and_next_batch_continues(tmp_path):
    research, model, accesses, _ = research_case(tmp_path, invalid=True)
    research.start()
    for _ in range(2):
        research.advance()
    state = research.status()
    assert len(state['attempts']) == 2 and all(row['status'] == 'REJECTED' for row in state['attempts'])
    assert state['budget']['used']['model_calls'] == 2 and state['budget']['used']['candidate_attempts'] == 2
    assert state['budget']['used']['account_jobs'] == 0 and accesses == []
    assert state['goal_complete'] is False


def test_unknown_paid_call_is_not_repeated_and_keeps_reservation(tmp_path):
    research, model, accesses, restore_submission = research_case(tmp_path, unknown=True)
    research.start()
    research.advance()
    restored = DiagnosisResearchV4(research.campaign, restore_submission(), invoker=model)
    restored.advance()
    state = restored.status()
    assert model.calls == 1 and state['budget']['reserved']['model_calls'] == 1
    assert state['budget']['used']['model_calls'] == 0 and state['status'] == 'RECONCILIATION_REQUIRED'
    assert any(item['status'] == 'UNKNOWN' for item in state['budget']['operations'].values())
    assert accesses == [] and not state['goal_complete']


def test_model_actual_overrun_is_debt_in_canonical_ledger_not_truncated(tmp_path):
    research, model, accesses, restore_submission = research_case(tmp_path, overrun=True)
    research.start()
    research.advance()
    restored = DiagnosisResearchV4(research.campaign, restore_submission(), invoker=model)
    state = restored.status()
    assert state['budget']['used']['model_cost_microunits'] == 2000
    assert state['budget']['resource_violation'] is True and state['budget']['paused'] is True
    assert state['budget']['reserved']['model_calls'] == 0
    assert state['budget']['operations']['CANDIDATE_0001_MODEL']['resource_overrun']['model_cost_microunits'] == 1900
    restored.advance()
    assert model.calls == 1 and accesses == []


def test_no_hard_budget_model_blocks_only_new_proposal_and_context_stays_bounded(tmp_path):
    research, _, accesses, _ = research_case(tmp_path)
    research.invoker = object()
    research.start()
    result = research.advance()
    assert result['waiting_reason'] == 'HARD_BUDGET_UNSUPPORTED'
    assert research.status()['status'] == 'WAITING_MODEL'
    assert research.campaign.peek_status()['reserved']['model_calls'] == 0 and accesses == []
    records = [{'candidate_id': 'OLD_'+str(i), 'rule_identity': stable_hash(i), 'hypothesis': 'x'*300,
                'change_reason': 'y'*300, 'feedback': {'codes': ['NO_PROFIT']}} for i in range(100)]
    config = deepcopy(research.config())
    config['model_limits']['context_max_bytes'] = 20000
    context = research._context(config, records, 101)
    assert len(json.dumps(context, ensure_ascii=False, sort_keys=True).encode('utf-8')) <= 20000
    assert context['history']['feedback_counts'] == {'NO_PROFIT': 100}
    assert context['history']['omitted_record_count'] > 0
