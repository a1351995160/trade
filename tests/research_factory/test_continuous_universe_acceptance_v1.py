"""只用合成原件检验验收器；这些测试不证明真实模型/行情/账户闭环。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from chanlun_trader.research_factory.common import stable_hash
from scripts.run_continuous_universe_acceptance_v1 import (
    _independent_proof, audit_continuous_universe_acceptance, main, rule_mechanism_signature,
)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class SyntheticAuditResearch:
    """内存服务与明确合成文件只用于单位合同，不是部署 loader。"""
    def __init__(self, root):
        self.root = root / 'campaign' / 'diagnosis_v4'
        self.root.mkdir(parents=True)
        self.campaign = SimpleNamespace(directory=self.root.parent)
        self.invoker = SimpleNamespace(contract_sha256=stable_hash('fixture_contract'), model_id='fixture_model')
        contract = {'scope': {'mechanism_combinations': [['TREND'], ['REVERSION']]}}
        contract['content_hash'] = stable_hash(contract)
        base = {'scope_policy': {'summary': {'contract': contract}, 'approval_ref': {'fixture': True}},
                'resource_limits': {'model_calls': 4}, 'max_batches': 4}
        self.configuration = {'contract': contract, 'model_limits': {'max_tokens': 100, 'max_cost_microunits': 100},
                              'campaign_authorization_hash': stable_hash(base)}
        self.records, self.advance_calls, self.confirmation = [], 0, None
        self.budget = {'base_authorization': base, 'operations': {},
            'used': {'model_calls': 0, 'model_tokens': 0, 'model_cost_microunits': 0},
            'reserved': {}, 'remaining': {'model_calls': 4}}
        save(self.campaign.directory / 'authorization.json', {'SYNTHETIC_TEST': True})
        save(self.campaign.directory / 'run_budget.json', {'SYNTHETIC_TEST': True})
        (self.campaign.directory / 'run_budget_events.jsonl').write_text('{"SYNTHETIC_TEST":true}\n', encoding='utf-8')

    def config(self):
        return deepcopy(self.configuration)

    def status(self):
        return {'status': 'WAITING_DATA', 'budget': deepcopy(self.budget), 'attempts': deepcopy(self.records),
            'final_exploration_ready': [], 'channels': {'CONFIRMATION': {'status': 'WAITING_DATA'}},
            'strategy_qualified': False, 'paper_actual_days': 0}

    def handover(self, design=True):
        assert design is True
        return {'independent_results_omitted': True}

    def advance(self):
        self.advance_calls += 1
        return {'status': 'WAITING_DATA', 'dispatched_segments': 0}


def add_model_fixture(research, index, *, synthetic=False):
    """构造协议形状；即使形状合格也不能满足整体验收中的真实账户门槛。"""
    candidate, batch = 'CANDIDATE_' + str(index).zfill(4), 'BATCH_' + str(index).zfill(4)
    directory = research.root / ('candidate_' + str(index).zfill(4))
    earlier = research.records[:]
    history = {'version': 'DETERMINISTIC_HISTORY_V1', 'total_attempts': len(earlier),
        'record_chain_identity': stable_hash([stable_hash(row) for row in earlier]),
        'all_rule_identities_hash': stable_hash([row['rule_identity'] for row in earlier]),
        'feedback_counts': {'MODEL_OUTPUT_INVALID': len(earlier)} if earlier else {},
        'recent_records': [{'candidate_id': row['candidate_id'], 'rule_identity': row['rule_identity'],
            'hypothesis': row['hypothesis'], 'change_reason': row['change_reason'],
            'feedback_codes': row['feedback']['codes']} for row in earlier], 'omitted_record_count': 0}
    context = {'history': history, 'allocated_mechanisms': ['TREND'] if index == 1 else ['REVERSION'],
               'evidence_boundary': 'EXPLORATION_QUALITATIVE_ONLY_NO_CONFIRMATION_RESULTS_OR_DATA'}
    save(directory / 'INTENT.json', {'candidate_id': candidate, 'batch_id': batch, 'index': index,
                                    'config_identity': stable_hash(research.configuration)})
    save(directory / 'CONTEXT.json', context)
    policy = {'protocol': 'TRUSTED_MODEL_BUDGET_GATEWAY_V1', 'contract_sha256': research.invoker.contract_sha256,
        'model_id': 'fixture_model', 'currency': 'USD', 'max_input_tokens': 100, 'max_output_tokens': 100, 'max_total_tokens': 100,
        'max_cost_microunits': 100, 'max_calls': 1, 'tools': [], 'external_input_refs': [], 'conversation_state': None}
    schema = {'type': 'object'}
    request = {'context_hash': stable_hash(context), 'policy': policy, 'policy_id': stable_hash(policy),
        'schema_hash': stable_hash(schema), 'output_schema': schema,
        'contract_sha256': research.invoker.contract_sha256, 'model_id': 'fixture_model',
        'invocation_id': str(index).zfill(32)}
    request['request_hash'] = stable_hash(request)
    usage = {'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 20, 'cost_microunits': 5, 'model_calls': 1}
    outcome = {key: request[key] for key in ('context_hash', 'request_hash', 'schema_hash',
        'contract_sha256', 'policy_id', 'invocation_id', 'model_id')}
    outcome.update(protocol='TRUSTED_MODEL_BUDGET_GATEWAY_V1', state='COMPLETED', usage=usage,
                   tools=[], external_input_refs=[])
    success = {'context_hash': stable_hash(context), 'request_hash': request['request_hash'], 'outcome': outcome}
    proposal = {'SYNTHETIC_TEST': True, 'hypothesis': '合成输出；不是实际提案。'}
    receipt = {**outcome, 'origin': 'REAL_TRUSTED_BUDGET_GATEWAY_HTTP', 'synthetic': synthetic,
        'runtime_version': 'TRUSTED_MODEL_BUDGET_GATEWAY_V1', 'hard_budget_exceeded': False,
        'provider_request_id': 'fixture_' + str(index), 'proposal': proposal,
        'response_hash': stable_hash(proposal), 'success_hash': stable_hash(success)}
    expected_guarantee = {'protocol': 'TRUSTED_MODEL_BUDGET_GATEWAY_V1',
        'contract_sha256': research.invoker.contract_sha256, 'policy_id': request['policy_id'],
        'policy': policy, 'enforced': True}
    guarantee = {'enforced': True, 'max_tokens': 100, 'max_cost_microunits': 100,
                 'evidence_identity': stable_hash(expected_guarantee)}
    for name, value in [('REQUEST', request), ('SUCCESS', success), ('INVOCATION', receipt)]:
        save(directory / 'model' / (name + '.json'), value)
    save(directory / 'MODEL_GUARANTEE.json', guarantee)
    save(directory / 'PROPOSAL.json', proposal)
    research.budget['operations'][candidate + '_MODEL'] = {'status': 'COMPLETED',
        'evidence_identity': stable_hash(receipt), 'actual': {'model_calls': 1, 'model_tokens': 20, 'model_cost_microunits': 5}}
    for key, value in {'model_calls': 1, 'model_tokens': 20, 'model_cost_microunits': 5}.items():
        research.budget['used'][key] += value
    row = {'candidate_id': candidate, 'batch_id': batch, 'rule_identity': None,
        'status': 'REJECTED', 'hypothesis': '合成', 'change_reason': '测试',
        'feedback': {'codes': ['MODEL_OUTPUT_INVALID']}}
    research.records.append(row)
    return directory


def test_default_audit_is_read_only_and_missing_external_evidence_blocks_delivery(tmp_path):
    research = SyntheticAuditResearch(tmp_path)
    before = {str(path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    report = audit_continuous_universe_acceptance(research)
    assert report['status'] == 'BLOCKED' and report['exit_code'] == 2
    assert report['delivery_gate_passed'] is False
    assert report['gates']['independent_252'] is False
    assert report['gates']['final_exploration_504'] is False
    assert report['formal_qualification']['strategy_qualified'] is False
    assert report['paper']['actual_days'] == 0
    assert research.advance_calls == 0
    assert before == {str(path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}


def test_synthetic_receipts_never_satisfy_real_model_gate(tmp_path):
    research = SyntheticAuditResearch(tmp_path)
    add_model_fixture(research, 1, synthetic=True)
    add_model_fixture(research, 2, synthetic=True)
    report = audit_continuous_universe_acceptance(research)
    assert report['layers']['real_model']['verified_receipts'] == 0
    assert report['delivery_gate_passed'] is False
    assert len(report['nonqualifying_evidence']) == 2


def test_bound_receipt_shape_and_second_batch_history_still_do_not_prove_real_account_loop(tmp_path):
    research = SyntheticAuditResearch(tmp_path)
    add_model_fixture(research, 1)
    add_model_fixture(research, 2)
    report = audit_continuous_universe_acceptance(research)
    assert report['gates']['two_real_model_batches'] is True
    assert report['gates']['second_batch_history'] is True
    assert report['status'] == 'PARTIAL' and report['exit_code'] == 2
    assert report['layers']['real_data_accounts']['verified_public_tasks'] == 0
    assert report['layers']['strategy_business_goal_met'] is False


def test_prior_failed_family_chain_and_actual_usage_cannot_be_reset_or_omitted(tmp_path):
    research = SyntheticAuditResearch(tmp_path)
    add_model_fixture(research, 1)
    second = add_model_fixture(research, 2)
    context = json.loads((second / 'CONTEXT.json').read_text(encoding='utf-8'))
    context['history']['record_chain_identity'] = stable_hash([])
    save(second / 'CONTEXT.json', context)
    report = audit_continuous_universe_acceptance(research)
    assert any(row['reason_code'] == 'LEGAL_COMPLETE_PRIOR_HISTORY_NOT_BOUND' for row in report['evidence_errors'])
    assert report['gates']['second_batch_history'] is False
    research.budget['used']['model_tokens'] = 0
    assert audit_continuous_universe_acceptance(research)['gates']['original_cumulative_budget_and_recovery'] is False


def test_unknown_usage_and_concurrent_ledger_change_do_not_pass_read_only_gate(tmp_path):
    research = SyntheticAuditResearch(tmp_path)
    research.budget['operations']['UNRESOLVED'] = {'status': 'UNKNOWN'}
    assert audit_continuous_universe_acceptance(research)['budget']['unresolved_operations'] == ['UNRESOLVED']
    original = research.handover
    def changed(design=True):
        (research.campaign.directory / 'run_budget_events.jsonl').write_text('changed', encoding='utf-8')
        return original(design=design)
    research.handover = changed
    report = audit_continuous_universe_acceptance(research)
    assert report['gates']['read_only_ledger'] is False


def test_cli_default_does_not_advance_and_explicit_run_stops_on_waiting(tmp_path, capsys):
    research = SyntheticAuditResearch(tmp_path)
    config = tmp_path / 'DEPLOYMENT.json'
    save(config, {'fixture': True})
    argv = ['--workspace-root', str(tmp_path), '--deployment-config', str(config), '--research-id', 'existing', '--json']
    def loader(root, supplied, research_id):
        assert Path(root) == tmp_path and supplied == {'fixture': True} and research_id == 'existing'
        return research
    assert main(argv, loader=loader) == 2
    assert research.advance_calls == 0
    assert json.loads(capsys.readouterr().out)['advance_calls'] == 0
    assert main(argv + ['--run', '--max-steps', '20'], loader=loader) == 2
    assert research.advance_calls == 1
    assert json.loads(capsys.readouterr().out)['advance_calls'] == 1


def test_loader_failure_is_nonzero_and_does_not_print_config_or_exception_secrets(tmp_path, capsys):
    config = tmp_path / 'DEPLOYMENT.json'
    save(config, {'bearer_token': 'do_not_print_this_secret'})
    def unavailable(*_):
        raise PermissionError('do_not_print_this_secret')
    assert main(['--workspace-root', str(tmp_path), '--deployment-config', str(config),
                 '--research-id', 'missing', '--json'], loader=unavailable) == 2
    output = capsys.readouterr().out
    assert 'do_not_print_this_secret' not in output
    assert json.loads(output)['status'] == 'BLOCKED'


def test_numeric_parameter_change_does_not_prove_different_mechanism():
    from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
    rule = capabilities()['examples']['multi_indicator_ranked']
    changed = deepcopy(rule)
    changed['indicator_instances'][0]['params'] = {'period': 13}
    assert rule_mechanism_signature(rule) == rule_mechanism_signature(changed)
    changed['buy']['op'] = 'not'
    changed['buy']['args'] = [deepcopy(rule['buy'])]
    assert rule_mechanism_signature(rule) != rule_mechanism_signature(changed)


def test_self_reported_independence_flags_do_not_replace_pinned_trusted_loader():
    service = SimpleNamespace(admit=lambda _: {'source_authenticated': True})
    with pytest.raises(ValueError, match='PINNED_TRUSTED_INDEPENDENT_ADMISSION_REQUIRED'):
        _independent_proof(service, {}, {'source_authenticated': True, 'post_freeze_unseen': True})


@pytest.mark.parametrize('status', ['FINAL_EXPLORATION_READY', 'FINAL_EXPLORATION_FAILED'])
def test_audit_routes_promoted_tasks_to_final_gate_without_replacing_short_records(tmp_path, monkeypatch, status):
    """只隔离队列与审计路由；原件核验函数和真实交付另有门槛。"""
    from chanlun_trader.research_factory.final_exploration_queue_v1 import FinalExplorationQueueV1
    import scripts.run_continuous_universe_acceptance_v1 as acceptance
    research = SyntheticAuditResearch(tmp_path)
    research.submission = SimpleNamespace()
    evidence = []
    for index, mechanism in enumerate(('TREND', 'REVERSION'), start=1):
        candidate = 'CANDIDATE_' + str(index).zfill(4)
        row = {'candidate_id': candidate, 'batch_id': 'BATCH_' + str(index).zfill(4),
            'rule_identity': stable_hash(mechanism), 'mechanisms': [mechanism],
            'screen': {'passed': True, 'final_exploration_ready': False}}
        research.records.append(row)
        path = research.root / 'final_exploration' / candidate / 'EVIDENCE.json'
        save(path, {'SYNTHETIC_QUEUE_INTERFACE': True})
        evidence.append({'candidate_id': candidate, 'batch_id': 'FINAL_' + candidate,
            'task_id': stable_hash(candidate), 'rule_identity': row['rule_identity'],
            'identity': stable_hash([candidate, status]), 'evidence_path': str(path),
            'evidence_sha256': stable_hash('fixture_bytes'), 'source_record_identity': stable_hash(row),
            'final_template_identity': stable_hash('frozen_final_template'), 'status': status})
    original_status = research.status
    research.status = lambda: {**original_status(),
        'final_exploration_ready': [row['candidate_id'] for row in research.records]}
    monkeypatch.setattr(FinalExplorationQueueV1, 'evidence', lambda _: deepcopy(evidence))
    seen_batches = []
    def public_proof(_, directory, row, contract):
        seen_batches.append(row['batch_id'])
        return {'task_id': stable_hash(row['candidate_id']), 'rule_identity': row['rule_identity'],
            'mechanism_signature': stable_hash(row['mechanisms']),
            'accounts': {cost: {'sessions': 504} for cost in ('BASE', 'STRESS')},
            'final_business_criteria_met': True, 'business_reporting_gaps': []}
    monkeypatch.setattr(acceptance, '_public_proof', public_proof)
    original_records = deepcopy(research.records)
    report = audit_continuous_universe_acceptance(research)
    assert seen_batches == [row['batch_id'] for row in evidence]
    assert report['layers']['real_data_accounts']['verified_promoted_public_tasks'] == 2
    assert report['gates']['final_exploration_504'] is (status == 'FINAL_EXPLORATION_READY')
    assert len(report['proofs']['promoted_final_exploration']) == 2
    assert report['delivery_gate_passed'] is False  # 没有真实模型、独立资料与完成报告。
    assert research.records == original_records and research.advance_calls == 0


def test_registered_independent_audit_requires_protocol_and_admission_in_stage_binding(tmp_path):
    """隔离元信息审计分支；模拟Owner接口不构成真实批准或独立资料。"""
    from chanlun_trader.research_factory.continuous_submission_v1 import request_scope
    from chanlun_trader.research_factory.continuous_universe_lifecycle_v1 import PinnedIndependentAdmissionV1
    from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
    from test_business_validation_protocol_v1 import setup_case
    service, _, arguments, protocol = setup_case(tmp_path, capabilities())
    protocol_hash = hashlib.sha256(service.protocol_path.read_bytes()).hexdigest()
    projection = {'profile': 'REAL_OBSERVED', 'source_authentication': 'CANONICAL_SNAPSHOT_STORE',
                  'snapshot_refs': [{'synthetic_interface_only': True}]}
    projection['projection_hash'] = stable_hash(projection)
    route = arguments['contract']['scope']['data_routes']['CONFIRMATION']
    request = {**arguments['requests']['passed'], 'phase': 'CONFIRMATION',
        'purpose': 'INDEPENDENT_BUSINESS_VALIDATION', 'dataset_id': route['dataset_id']}
    fields = {key: request[key] for key in ('dataset_id', 'feature_start', 'account_start', 'account_end', 'universe_id')}
    metadata = {'dataset_id': route['dataset_id'], 'content_hash': route['dataset_hash']}
    review = {'schema_version': 'CANONICAL_INDEPENDENT_DATA_REVIEW_V1',
        'protocol_identity': protocol['protocol_identity'], 'protocol_sha256': protocol_hash,
        'dataset_id': metadata['dataset_id'], 'manifest_sha256': metadata['content_hash'],
        'snapshot_projection_hash': projection['projection_hash'],
        'snapshot_refs_identity': stable_hash(projection['snapshot_refs']), 'trusted_route': route,
        'review_method': 'OWNER_PRIOR_ACCESS_REVIEW_AND_REGISTERED_SOURCE_PROVENANCE_V1',
        'review_outcome': 'APPROVED_FUTURE_UNSEEN'}
    def pin(name, value):
        path = tmp_path / name
        save(path, value)
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'approval_ref': {'synthetic_owner_interface': stable_hash(value)}}
    review_ref = pin('SYNTHETIC_REVIEW.json', review)
    config = {'schema_version': 'PINNED_INDEPENDENT_ADMISSION_V1', 'prior_access_review': review_ref,
        'request_fields': fields, 'trusted_data_access': {'protocol_binding': {
            'protocol_id': protocol['protocol_identity'], 'protocol_sha256': protocol_hash,
            'independent_evidence_id': stable_hash(review), 'independent_evidence_sha256': review_ref['sha256']}}}
    config_ref = pin('SYNTHETIC_CONFIG.json', config)
    def owner_interface(reference, value):
        assert reference == {'synthetic_owner_interface': stable_hash(value)}
    service.admit = PinnedIndependentAdmissionV1(tmp_path, config_ref,
        approvals=SimpleNamespace(require=owner_interface), submission=SimpleNamespace(), protocol_path=service.protocol_path)
    admission = {'admission_config_ref': config_ref, 'prior_access_review_ref': review_ref,
        'request_fields': fields, 'snapshot_projection': projection, 'metadata': metadata,
        'prior_access_review_identity': stable_hash(review)}
    save(service.root / 'ADMISSION.json', admission)
    save(service._candidate_path('passed', 'REQUEST.json'), request)
    dataset = {'dataset_id': metadata['dataset_id'], 'metadata_hash': metadata['content_hash'],
               'universe_identity': arguments['contract']['scope']['universe_hash']}
    protected = {'candidate_identity': protocol['preview']['rules']['passed'], 'phase': 'CONFIRMATION',
        'request': {**request_scope(request, arguments['contract'], dataset), 'data_route_proof': {
            'protocol_sha256': protocol_hash,
            'admission_sha256': hashlib.sha256((service.root / 'ADMISSION.json').read_bytes()).hexdigest()}}}
    service.submission = SimpleNamespace(provider=SimpleNamespace(catalog=lambda: {'datasets': [dataset]}),
        continuous_scope=SimpleNamespace(resolve=lambda *args, **kwargs: deepcopy(protected)))
    assert _independent_proof(service, protocol, admission) is True
    protected['request'].pop('data_route_proof')
    with pytest.raises(ValueError, match='INDEPENDENT_PUBLIC_STAGE_BINDING_CONFLICT'):
        _independent_proof(service, protocol, admission)
