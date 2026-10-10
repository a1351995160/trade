"""持续全池研究的只读分层验收；显式 --run 才推进既有受信任务。"""
from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.business_validation_protocol_v1 import (
    BusinessValidationProtocolV1, evaluate_business_reports, verify_exploration_binding,
)
from chanlun_trader.research_factory.exploration_governance import read_json
from chanlun_trader.research_factory.strategy_submission_v1 import public_rule_factory
from chanlun_trader.presentation import ZhCNPresentation


VERSION = 'CONTINUOUS_UNIVERSE_ACCEPTANCE_V1'


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _error_code(exc):
    message = str(exc)
    return message if isinstance(exc, (ValueError, PermissionError)) and re.fullmatch(r'[A-Z0-9_]+', message) else type(exc).__name__


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(value, key):
    return value.get(key) == stable_hash({name: item for name, item in value.items() if name != key})


def rule_mechanism_signature(rule):
    """最低结构差异证据：忽略数值调参，保留指标身份、角色和表达式拓扑。"""
    aliases = {item['instance_id']: item['id'] for item in rule['indicator_instances']}
    def node(value):
        if not isinstance(value, dict):
            if isinstance(value, str) and value in aliases:
                return {'indicator_id': aliases[value]}
            return {'literal_type': 'NUMBER' if type(value) in (int, float) else type(value).__name__}
        return {'op': value.get('op'), 'args': [node(child) for child in value.get('args', [])]}
    return stable_hash({'buy': node(rule['buy']), 'sell': node(rule['sell']),
        'market_filter': node(rule['market_filter']), 'score': node(rule['selection']['score']),
        'score_direction': rule['selection']['direction']})


def _ledger_files(research):
    directory = Path(research.campaign.directory)
    return {name: _digest(directory / name) if (directory / name).is_file() else None
        for name in ('authorization.json', 'run_budget.json', 'run_budget_events.jsonl')}


def _model_proof(research, directory, context, budget):
    """验证 HTTP gateway 原始请求/成功回执和原累计预算结算；不调用 gateway。"""
    request = read_json(directory / 'model' / 'REQUEST.json')
    success = read_json(directory / 'model' / 'SUCCESS.json')
    receipt = read_json(directory / 'model' / 'INVOCATION.json')
    guarantee = read_json(directory / 'MODEL_GUARANTEE.json')
    proposal = read_json(directory / 'PROPOSAL.json')
    policy, usage = request.get('policy', {}), receipt.get('usage', {})
    _require(_identity(request, 'request_hash') and request.get('context_hash') == stable_hash(context),
             'MODEL_REQUEST_CONTEXT_NOT_BOUND')
    contract = getattr(research.invoker, 'contract_sha256', None)
    _require(isinstance(contract, str) and re.fullmatch(r'[0-9a-f]{64}', contract)
        and request.get('contract_sha256') == contract, 'TRUSTED_MODEL_GATEWAY_NOT_CONFIGURED')
    _require(receipt.get('synthetic') is False and receipt.get('origin') == 'REAL_TRUSTED_BUDGET_GATEWAY_HTTP'
        and receipt.get('runtime_version') == 'TRUSTED_MODEL_BUDGET_GATEWAY_V1'
        and receipt.get('state') == 'COMPLETED' and receipt.get('hard_budget_exceeded') is False
        and receipt.get('provider_request_id'), 'REAL_MODEL_RECEIPT_REQUIRED')
    limits = research.config()['model_limits']
    expected_guarantee = {'protocol': 'TRUSTED_MODEL_BUDGET_GATEWAY_V1', 'contract_sha256': contract,
        'policy_id': request.get('policy_id'), 'policy': policy, 'enforced': True}
    _require(guarantee.get('enforced') is True and guarantee.get('evidence_identity') == stable_hash(expected_guarantee)
        and guarantee.get('max_tokens') == limits['max_tokens']
        and guarantee.get('max_cost_microunits') == limits['max_cost_microunits']
        and policy.get('max_total_tokens') <= limits['max_tokens']
        and policy.get('max_cost_microunits') <= limits['max_cost_microunits']
        and request.get('schema_hash') == stable_hash(request.get('output_schema'))
        and request.get('model_id') == getattr(research.invoker, 'model_id', None)
        and policy.get('protocol') == 'TRUSTED_MODEL_BUDGET_GATEWAY_V1'
        and policy.get('contract_sha256') == contract and policy.get('model_id') == request['model_id']
        and policy.get('currency') == 'USD'
        and policy.get('max_calls') == 1 and policy.get('tools') == []
        and policy.get('external_input_refs') == [] and policy.get('conversation_state') is None
        and request.get('policy_id') == stable_hash(policy), 'MODEL_HARD_BOUND_PROOF_MISSING')
    _require(set(usage) == {'input_tokens', 'output_tokens', 'total_tokens', 'cost_microunits', 'model_calls'}
        and all(type(value) is int and value >= 0 for value in usage.values())
        and usage['model_calls'] == 1 and usage['total_tokens'] == usage['input_tokens'] + usage['output_tokens']
        and usage['input_tokens'] <= policy['max_input_tokens']
        and usage['output_tokens'] <= policy['max_output_tokens']
        and usage['total_tokens'] <= policy['max_total_tokens']
        and usage['cost_microunits'] <= policy['max_cost_microunits'], 'MODEL_USAGE_OR_HARD_BOUND_INVALID')
    for key in ('context_hash', 'request_hash', 'schema_hash', 'contract_sha256', 'policy_id', 'invocation_id', 'model_id'):
        _require(receipt.get(key) == request.get(key)
            and success.get('outcome', {}).get(key) == request.get(key), 'MODEL_RECEIPT_BINDING_CONFLICT')
    _require(success.get('request_hash') == request['request_hash']
        and success.get('context_hash') == stable_hash(context)
        and receipt.get('success_hash') == stable_hash(success)
        and receipt.get('response_hash') == stable_hash(proposal)
        and receipt.get('proposal') == proposal
        and success['outcome'].get('usage') == usage
        and success['outcome'].get('protocol') == 'TRUSTED_MODEL_BUDGET_GATEWAY_V1'
        and success['outcome'].get('state') == 'COMPLETED'
        and success['outcome'].get('tools') == [] and success['outcome'].get('external_input_refs') == [],
        'MODEL_SUCCESS_OR_RESPONSE_CHANGED')
    candidate = read_json(directory / 'INTENT.json')['candidate_id']
    operation = budget['operations'].get(candidate + '_MODEL', {})
    _require(operation.get('status') == 'COMPLETED' and operation.get('evidence_identity') == stable_hash(receipt)
        and operation.get('actual', {}).get('model_calls') == usage['model_calls']
        and operation.get('actual', {}).get('model_tokens') == usage['total_tokens']
        and operation.get('actual', {}).get('model_cost_microunits') == usage['cost_microunits'],
        'MODEL_RECEIPT_NOT_IN_ORIGINAL_CUMULATIVE_LEDGER')
    return {'receipt_identity': stable_hash(receipt), 'usage': usage}


def _history_proof(context, earlier):
    history = context.get('history', {})
    counts = Counter(code for row in earlier for code in row['feedback']['codes'])
    expected_recent = [{'candidate_id': row['candidate_id'], 'rule_identity': row['rule_identity'],
        'hypothesis': row['hypothesis'], 'change_reason': row['change_reason'],
        'feedback_codes': row['feedback']['codes']} for row in earlier]
    recent = history.get('recent_records', [])
    _require(history.get('version') == 'DETERMINISTIC_HISTORY_V1'
        and history.get('total_attempts') == len(earlier)
        and history.get('record_chain_identity') == stable_hash([stable_hash(row) for row in earlier])
        and history.get('all_rule_identities_hash') == stable_hash([row['rule_identity'] for row in earlier])
        and history.get('feedback_counts') == dict(sorted(counts.items()))
        and history.get('omitted_record_count') == len(earlier) - len(recent)
        and recent == (expected_recent[-len(recent):] if recent else [])
        and context.get('evidence_boundary') == 'EXPLORATION_QUALITATIVE_ONLY_NO_CONFIRMATION_RESULTS_OR_DATA',
        'LEGAL_COMPLETE_PRIOR_HISTORY_NOT_BOUND')


def _independent_proof(service, protocol, admission):
    """重验维护者批准的元数据原件；不调用 admission loader 或重新读快照行情。"""
    from chanlun_trader.research_factory.continuous_universe_lifecycle_v1 import PinnedIndependentAdmissionV1
    loader = service.admit
    _require(isinstance(loader, PinnedIndependentAdmissionV1)
        and admission.get('admission_config_ref') == loader.reference,
        'PINNED_TRUSTED_INDEPENDENT_ADMISSION_REQUIRED')
    def approved(reference):
        _require(isinstance(reference, dict) and set(reference) == {'path', 'sha256', 'approval_ref'},
                 'INDEPENDENT_OWNER_ARTIFACT_REFERENCE_INVALID')
        path = Path(reference['path'])
        _require(path.is_absolute() and path.resolve() == path and _digest(path) == reference['sha256'],
                 'INDEPENDENT_OWNER_ARTIFACT_CHANGED')
        value = read_json(path)
        loader.approvals.require(reference['approval_ref'], value)
        return value
    config = approved(loader.reference)
    reference = admission.get('prior_access_review_ref')
    review = approved(reference)
    binding = config.get('trusted_data_access', {}).get('protocol_binding', {})
    projection = admission.get('snapshot_projection', {})
    metadata = admission.get('metadata', {})
    _require(config.get('schema_version') == 'PINNED_INDEPENDENT_ADMISSION_V1'
        and config.get('prior_access_review') == reference
        and config.get('request_fields') == admission.get('request_fields')
        and binding.get('protocol_id') == protocol['protocol_identity']
        and binding.get('protocol_sha256') == _digest(service.protocol_path)
        and binding.get('independent_evidence_id') == stable_hash(review)
        and binding.get('independent_evidence_sha256') == reference['sha256'],
        'INDEPENDENT_PROTOCOL_DATA_SCOPE_NOT_BOUND')
    _require(review.get('schema_version') == 'CANONICAL_INDEPENDENT_DATA_REVIEW_V1'
        and review.get('protocol_identity') == protocol['protocol_identity']
        and review.get('protocol_sha256') == _digest(service.protocol_path)
        and review.get('dataset_id') == metadata.get('dataset_id')
        and review.get('manifest_sha256') == metadata.get('content_hash')
        and review.get('snapshot_projection_hash') == projection.get('projection_hash')
        and review.get('snapshot_refs_identity') == stable_hash(projection.get('snapshot_refs'))
        and review.get('trusted_route') == protocol['preview']['contract']['scope']['data_routes']['CONFIRMATION']
        and review.get('review_method') == 'OWNER_PRIOR_ACCESS_REVIEW_AND_REGISTERED_SOURCE_PROVENANCE_V1'
        and review.get('review_outcome') == 'APPROVED_FUTURE_UNSEEN'
        and admission.get('prior_access_review_identity') == stable_hash(review)
        and _identity(projection, 'projection_hash') and projection.get('profile') == 'REAL_OBSERVED'
        and projection.get('source_authentication') == 'CANONICAL_SNAPSHOT_STORE',
        'INDEPENDENT_PRIOR_ACCESS_REVIEW_NOT_BOUND')
    from chanlun_trader.research_factory.continuous_submission_v1 import request_scope
    catalog = {row['dataset_id']: row for row in service.submission.provider.catalog()['datasets']}
    for candidate in protocol['preview']['selected']:
        request = read_json(service._candidate_path(candidate, 'REQUEST.json'))
        scope = service.submission.continuous_scope.resolve(request['research_binding_ref'], for_dispatch=False)
        expected_scope = request_scope(request, protocol['preview']['contract'], catalog[request['dataset_id']])
        expected_scope['data_route_proof'] = {'protocol_sha256': _digest(service.protocol_path),
            'admission_sha256': _digest(service.root / 'ADMISSION.json')}
        _require(request.get('phase') == scope.get('phase') == 'CONFIRMATION'
            and request.get('purpose') == 'INDEPENDENT_BUSINESS_VALIDATION'
            and scope.get('candidate_identity') == protocol['preview']['rules'][candidate]
            and scope.get('request') == expected_scope,
            'INDEPENDENT_PUBLIC_STAGE_BINDING_CONFLICT')
    return True


def _public_proof(research, directory, row, contract):
    request, preview = read_json(directory / 'REQUEST.json'), read_json(directory / 'PREVIEW.json')
    outcome, task_ref = read_json(directory / 'PUBLIC_RESULT.json'), read_json(directory / 'TASK.json')
    _require(request.get('version') == 'FULL_UNIVERSE_SUBMISSION_V4'
        and request.get('phase') == 'EXPLORATION' and request.get('purpose') == 'EXPLORATORY'
        and re.fullmatch(r'[0-9a-f]{64}', request.get('research_binding_ref', {}).get('binding_id', '')),
        'PUBLIC_V4_EXPLORATION_BINDING_REQUIRED')
    strategy = public_rule_factory(request['rule'], request['strategy_id'])
    _require(strategy.rule_identity == row['rule_identity'] == preview.get('rule_identity')
        and len(strategy.parameters.get('factor_ids', [])) >= 2, 'PUBLIC_MULTI_INDICATOR_RULE_NOT_PROVEN')
    verify_exploration_binding(research.submission, request, contract,
        candidate_identity=strategy.rule_identity, batch_id=row['batch_id'])
    task = research.submission._task(task_ref['task_id'])
    job = Path(task['job_path'])
    _require(task.get('submission_version') == 'FULL_UNIVERSE_SUBMISSION_V4'
        and _digest(job) == task.get('job_sha256') and outcome.get('task_id') == task['task_id']
        and outcome.get('status') == 'ACCOUNT_VERIFIED'
        and outcome.get('verification', {}).get('advance_allowed') is True
        and outcome['verification'].get('job_sha256') == task['job_sha256']
        and read_json(job.parent / 'VERIFICATION.json') == outcome['verification'],
        'ORIGINAL_PUBLIC_ACCOUNT_VERIFICATION_REQUIRED')
    scope = task.get('qualification_scope', {})
    targets, qualified = scope.get('target_symbols', []), scope.get('qualified_symbols', [])
    excluded = [item['symbol'] for item in scope.get('excluded', [])]
    _require(_identity(scope, 'scope_identity') and targets and qualified
        and targets == sorted(set(targets)) and qualified == sorted(set(qualified))
        and not set(qualified) & set(excluded) and len(excluded) == len(set(excluded))
        and sorted(qualified + excluded) == targets and not scope.get('blocking_global_gaps')
        and preview.get('request', {}).get('symbols') == targets
        and scope.get('projected_input_identity') == task['input_identity'],
        'FULL_REGISTERED_TARGET_DENOMINATOR_NOT_PROVEN')
    # 固定部署的 provider 登记身份；不重新读取行情，不用模型自报的 real 标志。
    from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1, PROVIDER_ADAPTERS
    provider = research.submission.provider
    _require(isinstance(provider, UniverseDataProviderV1), 'TRUSTED_REGISTERED_REAL_DATA_PROVIDER_REQUIRED')
    _, manifest, digest, universe = provider._datasets[request['dataset_id']]
    source_names = [manifest.get('master', {}).get('source', '')] + [item.get('source_id', '')
        for item in manifest['files'].values()]
    _require(manifest.get('adapter') in PROVIDER_ADAPTERS
        and all(isinstance(value, str) and value and 'SYNTHETIC' not in value.upper() for value in source_names)
        and scope.get('parent_manifest_hash') == digest
        and scope.get('parent_universe_identity') == universe.universe_identity
        and targets == list(universe.target_symbols)
        and universe.universe_identity == contract['scope']['universe_hash'],
        'REGISTERED_REAL_DATA_SOURCE_OR_UNIVERSE_BINDING_REQUIRED')
    reports, accounts = outcome.get('reports', {}), {}
    _require(isinstance(reports, dict) and len(reports) == 2, 'DUAL_COST_ORIGINAL_REPORTS_REQUIRED')
    for name, reference in reports.items():
        cost = 'STRESS' if name.endswith('STRESS') else 'BASE' if name.endswith('BASE') else None
        path = Path(reference['research_report'])
        _require(cost and cost not in accounts and path.resolve() == path.absolute()
            and path.parent == job.parent and _digest(path) == reference['research_report_sha256'],
            'ORIGINAL_RESEARCH_REPORT_CHANGED')
        report = read_json(path)
        _require(_identity(report, 'report_identity') and report.get('input_identity') == task['input_identity']
            and report.get('version') == 'UNIVERSE_RESEARCH_REPORT_V2'
            and report.get('signal', {}).get('scope_count') == len(qualified)
            and report['signal'].get('account_independent_denominator') is True,
            'REPORT_IDENTITY_OR_INDEPENDENT_SIGNAL_DENOMINATOR_INVALID')
        funnel_path = Path(reference['funnel'])
        _require(funnel_path.parent == job.parent and _digest(funnel_path) == reference['funnel_sha256'],
                 'ORIGINAL_SIGNAL_FUNNEL_CHANGED')
        funnel = read_json(funnel_path)
        _require(_identity(funnel, 'identity') and funnel.get('rule_identity') == strategy.rule_identity,
                 'SIGNAL_FUNNEL_RULE_IDENTITY_CONFLICT')
        account = report['account']
        _require(account.get('initial_cash') == 50000 and type(account.get('sessions')) is int
            and account['sessions'] >= 1, 'REAL_ACCOUNT_CASH_OR_SESSIONS_INVALID')
        accounts[cost] = {'sessions': account['sessions'], 'report_identity': report['report_identity']}
    _require(set(accounts) == {'BASE', 'STRESS'} and accounts['BASE']['sessions'] == accounts['STRESS']['sessions'],
             'DUAL_COST_ACCOUNT_SCOPE_CONFLICT')
    verified = BusinessValidationProtocolV1.verify_report_refs(task, outcome, minimum_sessions=1)
    business = evaluate_business_reports(contract,
        {cost: read_json(item['report_ref']['research_report']) for cost, item in verified.items()}, minimum_sessions=504)
    return {'task_id': task['task_id'], 'rule_identity': strategy.rule_identity,
        'mechanism_signature': rule_mechanism_signature(request['rule']),
        'target_count': len(targets), 'qualified_count': len(qualified), 'excluded_count': len(excluded),
        'scope_identity': scope['scope_identity'], 'accounts': accounts,
        'final_business_criteria_met': business['business_criteria_met'], 'business_reporting_gaps': business['reason_codes']}


def audit_continuous_universe_acceptance(research):
    """读取已恢复服务的原件，结果不授予权限，也不会进入下一批设计上下文。"""
    before = _ledger_files(research)
    config, state, design = research.config(), research.status(), research.handover(design=True)
    contract, budget = config['contract'], state['budget']
    blockers, errors, model_proofs, public_proofs, histories, nonqualifying = [], [], [], [], [], []
    promoted_proofs = []
    base = budget.get('base_authorization', {})
    authorization_bound = (config.get('campaign_authorization_hash') == stable_hash(base)
        and base.get('scope_policy', {}).get('summary', {}).get('contract') == contract
        and base.get('scope_policy', {}).get('approval_ref'))
    if not authorization_bound:
        blockers.append('EXISTING_OWNER_APPROVED_FINITE_CAMPAIGN_REQUIRED')
    design_safe = (design.get('independent_results_omitted') is True
        and not any(key in design for key in ('channels', 'confirmation', 'reports', 'metadata')))
    if not design_safe:
        blockers.append('DESIGN_HANDOVER_INDEPENDENT_RESULT_BOUNDARY_INVALID')
    records = state.get('attempts', [])
    mechanisms, model_batches = set(), set()
    for directory in sorted(Path(research.root).glob('candidate_*')):
        if not (directory / 'CONTEXT.json').is_file():
            continue
        try:
            intent, context = read_json(directory / 'INTENT.json'), read_json(directory / 'CONTEXT.json')
            _require(intent.get('config_identity') == stable_hash(config), 'CANDIDATE_FROZEN_CONFIG_CHANGED')
            earlier = [row for row in records if int(row['candidate_id'].rsplit('_', 1)[-1]) < intent['index']]
            _history_proof(context, earlier)
            histories.append({'candidate_id': intent['candidate_id'], 'prior_attempts': len(earlier),
                              'record_chain_identity': context['history']['record_chain_identity']})
            if (directory / 'model' / 'INVOCATION.json').exists():
                try:
                    model = _model_proof(research, directory, context, budget)
                    model_proofs.append({**model, 'candidate_id': intent['candidate_id'], 'batch_id': intent['batch_id']})
                    model_batches.add(intent['batch_id'])
                except (ValueError, KeyError, TypeError, OSError, AttributeError) as exc:
                    receipt = read_json(directory / 'model' / 'INVOCATION.json')
                    operation = budget.get('operations', {}).get(intent['candidate_id'] + '_MODEL', {})
                    error = {'candidate': directory.name, 'reason_code': _error_code(exc)}
                    # 合法失败和显式合成样本保留在家族中，但不充当真实成功回执。
                    (nonqualifying if operation.get('status') == 'FAILED' or receipt.get('synthetic') is True else errors).append(error)
            if (directory / 'RECORD.json').exists() and (directory / 'PUBLIC_RESULT.json').exists():
                row = read_json(directory / 'RECORD.json')
                _require(row in records and row.get('mechanisms') == context.get('allocated_mechanisms')
                    and row.get('mechanisms') == contract['scope']['mechanism_combinations'][
                        (intent['index'] - 1) % len(contract['scope']['mechanism_combinations'])],
                    'FROZEN_MECHANISM_ASSIGNMENT_CHANGED')
                proof = _public_proof(research, directory, row, contract)
                public_proofs.append({**proof, 'candidate_id': row['candidate_id'],
                    'batch_id': row['batch_id'], 'evidence_source': 'INITIAL'})
                mechanisms.add(tuple(row['mechanisms']))
        except (ValueError, KeyError, TypeError, OSError, AttributeError) as exc:
            errors.append({'candidate': directory.name, 'reason_code': _error_code(exc)})
    # 晋级任务保留初筛RECORD；单独重核长期公共任务，不把初筛短窗当成504日结果。
    if list((Path(research.root) / 'final_exploration').glob('CANDIDATE_*/EVIDENCE.json')):
        try:
            from chanlun_trader.research_factory.final_exploration_queue_v1 import FinalExplorationQueueV1
            for evidence in FinalExplorationQueueV1(research).evidence():
                row = next(row for row in records if row['candidate_id'] == evidence['candidate_id'])
                proof = _public_proof(research, Path(evidence['evidence_path']).parent,
                    {**row, 'batch_id': evidence['batch_id']}, contract)
                _require(proof['task_id'] == evidence['task_id']
                    and proof['rule_identity'] == evidence['rule_identity'],
                    'PROMOTED_FINAL_PUBLIC_TASK_IDENTITY_CONFLICT')
                promoted = {**proof, 'candidate_id': row['candidate_id'], 'batch_id': evidence['batch_id'],
                    'evidence_source': 'PROMOTED', 'evidence_identity': evidence['identity'],
                    'evidence_path': evidence['evidence_path'], 'evidence_sha256': evidence['evidence_sha256'],
                    'source_record_identity': evidence['source_record_identity'],
                    'final_template_identity': evidence['final_template_identity'], 'status': evidence['status']}
                promoted_proofs.append(promoted)
                public_proofs.append(promoted)
                mechanisms.add(tuple(row['mechanisms']))
        except (ValueError, KeyError, TypeError, OSError, AttributeError, StopIteration) as exc:
            errors.append({'candidate': 'FINAL_EXPLORATION', 'reason_code': _error_code(exc)})
    model_gate = len(model_proofs) >= 2 and len(model_batches) >= 2
    if not model_gate:
        blockers.append('TWO_DISTINCT_BATCHES_REAL_HARD_BOUNDED_MODEL_RECEIPTS_REQUIRED')
    history_gate = any(item['prior_attempts'] >= 1 for item in histories) and model_gate
    if not history_gate:
        blockers.append('SECOND_REAL_BATCH_LEGAL_COMPLETE_HISTORY_REQUIRED')
    account_gate = (len(public_proofs) >= 2 and len(mechanisms) >= 2
        and len({row['rule_identity'] for row in public_proofs}) >= 2
        and len({row['mechanism_signature'] for row in public_proofs}) >= 2)
    if not account_gate:
        blockers.append('TWO_DIFFERENT_MECHANISMS_REAL_PUBLIC_V4_DUAL_COST_ACCOUNTS_REQUIRED')
    actual_504 = sorted({row['candidate_id'] for row in public_proofs
        if min(value['sessions'] for value in row['accounts'].values()) >= 504})
    final_ready = set(state.get('final_exploration_ready', [])) & {
        row['candidate_id'] for row in public_proofs if row['final_business_criteria_met']
        and (row['evidence_source'] == 'INITIAL' or row['status'] == 'FINAL_EXPLORATION_READY')}
    if not final_ready:
        blockers.append('FINAL_504_ACCOUNT_DAYS_AND_FROZEN_BUSINESS_THRESHOLDS_NOT_MET')
    unknown = [name for name, row in budget.get('operations', {}).items() if row.get('status') == 'UNKNOWN'
        or row.get('active_segment') is not None]
    ledger_gate = (bool(before['authorization.json']) and bool(before['run_budget_events.jsonl'])
        and authorization_bound and not unknown)
    usage = {key: sum(row['usage'][field] for row in model_proofs) for key, field in
        (('model_calls', 'model_calls'), ('model_tokens', 'total_tokens'), ('model_cost_microunits', 'cost_microunits'))}
    if any(budget.get('used', {}).get(key, -1) < amount for key, amount in usage.items()):
        ledger_gate = False
    if not ledger_gate:
        blockers.append('ORIGINAL_CUMULATIVE_BUDGET_OR_RECOVERY_NOT_VERIFIED')
    confirmation = state.get('channels', {}).get('CONFIRMATION', {})
    independent_gate = False
    independent = {'status': confirmation.get('status', 'WAITING'), 'actual_account_sessions': None,
                   'real_independence_verified': False, 'results_visible_to_design': False}
    protocol_service = getattr(research, 'confirmation', None)
    if protocol_service is not None and protocol_service.protocol_path.exists():
        try:
            frozen = protocol_service.load()
            user_results = protocol_service.human_results()
            sessions = [item['sessions'] for row in user_results.values()
                        for item in row['verified_report_refs'].values()]
            admission_path = protocol_service.root / 'ADMISSION.json'
            admission = read_json(admission_path) if admission_path.exists() else {}
            independent_gate = (frozen['preview']['profile'] == 'REAL_OBSERVED'
                and len(user_results) == len(frozen['preview']['selected']) and bool(sessions) and min(sessions) >= 252
                and set(frozen['preview']['selected']) <= final_ready
                and admission.get('source_authenticated') is True
                and admission.get('prior_access_review_passed') is True and admission.get('post_freeze_unseen') is True
                and admission.get('snapshot_projection', {}).get('profile') == 'REAL_OBSERVED'
                and _independent_proof(protocol_service, frozen, admission))
            independent.update(actual_account_sessions=min(sessions) if sessions else None,
                               real_independence_verified=independent_gate)
        except (ValueError, KeyError, TypeError, OSError, PermissionError, AttributeError) as exc:
            errors.append({'candidate': 'CONFIRMATION', 'reason_code': _error_code(exc)})
    if not independent_gate:
        blockers.append('REAL_POST_FREEZE_INDEPENDENT_252_ACCOUNT_DAYS_NOT_VERIFIED')
    business_gate = independent_gate and confirmation.get('business_goal_met') is True
    if not business_gate:
        blockers.append('FROZEN_FINAL_BUSINESS_GOAL_NOT_MET')
    after = _ledger_files(research)
    unchanged = before == after
    if not unchanged:
        blockers.append('READ_ONLY_AUDIT_LEDGER_CHANGED_OR_CONCURRENT_WRITER')
    gates = {'approved_campaign': bool(authorization_bound), 'design_boundary': design_safe,
        'two_real_model_batches': model_gate, 'second_batch_history': history_gate,
        'two_real_public_accounts_different_mechanisms': account_gate,
        'original_cumulative_budget_and_recovery': bool(ledger_gate), 'final_exploration_504': bool(final_ready),
        'independent_252': independent_gate, 'final_business_goal': business_gate, 'read_only_ledger': unchanged}
    passed = all(gates.values()) and not errors
    result = {'version': VERSION, 'status': 'PASSED' if passed else 'PARTIAL' if model_proofs or public_proofs else 'BLOCKED',
        'delivery_gate_passed': passed, 'exit_code': 0 if passed else 2, 'read_only': True,
        'research_status': state.get('status'), 'contract_identity': contract['content_hash'],
        'gates': gates, 'blockers': sorted(set(blockers)), 'evidence_errors': errors,
        'nonqualifying_evidence': nonqualifying,
        'layers': {'engineering_artifact_audit': 'PASSED' if design_safe and unchanged and not errors else 'PARTIAL',
            'software_tests': 'NOT_EXECUTED_BY_THIS_AUDIT',
            'real_model': {'verified_receipts': len(model_proofs), 'verified_batches': len(model_batches)},
            'real_data_accounts': {'verified_public_tasks': len(public_proofs), 'mechanism_combinations': len(mechanisms),
                                   'mechanism_structure_signatures': len({row['mechanism_signature'] for row in public_proofs}),
                                   'actual_504_candidates': actual_504,
                                   'verified_promoted_public_tasks': len(promoted_proofs)},
            'independent_business_validation': independent, 'strategy_business_goal_met': business_gate},
        'formal_qualification': {'strategy_qualified': state.get('strategy_qualified') is True,
                                'source': 'CANONICAL_STATUS_SEPARATE_FROM_BUSINESS_GOAL'},
        'paper': {'actual_days': state.get('paper_actual_days', 0), 'qualified': state.get('paper_qualified') is True},
        'budget': {'original_authorization_identity': stable_hash(base), 'ledger_files': after,
                   'used': budget.get('used'), 'reserved': budget.get('reserved'), 'remaining': budget.get('remaining'),
                   'unresolved_operations': unknown, 'resume_existing_campaign_only': True},
        'proofs': {'models': model_proofs, 'histories': histories, 'public_accounts': public_proofs,
                   'promoted_final_exploration': promoted_proofs},
        'qualification_granted': False, 'automatic_paid_calls': 0}
    return result


def _human(report):
    lines = ['持续全池研究验收：' + ZhCNPresentation.state_name(report['status'], include_code=True),
        '真实交付门槛：' + ('通过' if report['delivery_gate_passed'] else '未通过'),
        '审计结果不授予正式资格；Paper 状态独立显示。']
    layers = report.get('layers', {})
    if layers:
        lines.extend(['已核验真实模型回执：' + str(layers['real_model']['verified_receipts']),
            '已核验真实公共账户任务：' + str(layers['real_data_accounts']['verified_public_tasks']),
            '独立账户实际日数：' + str(layers['independent_business_validation']['actual_account_sessions']),
            '正式策略资格：' + ('已取得' if report['formal_qualification']['strategy_qualified'] else '未取得'),
            'Paper 实际日数：' + str(report['paper']['actual_days'])])
    lines.extend('等待原因：' + ZhCNPresentation.reason_title(code, include_code=True) for code in report['blockers'])
    return '\n'.join(lines)


def main(argv=None, *, loader=None):
    parser = argparse.ArgumentParser(description='只读验收已批准的持续全池研究；缺真实证据时退出码为 2。')
    parser.add_argument('--workspace-root', type=Path, required=True)
    parser.add_argument('--deployment-config', type=Path, required=True)
    parser.add_argument('--research-id', required=True)
    parser.add_argument('--run', action='store_true', help='显式推进既有受信服务；可能消费原已批准预算')
    parser.add_argument('--max-steps', type=int, default=1)
    parser.add_argument('--json', action='store_true', help='输出 canonical JSON')
    args = parser.parse_args(argv)
    if not 1 <= args.max_steps <= 1000:
        parser.error('--max-steps 必须为 1 到 1000')
    steps = 0
    try:
        if loader is None:
            from scripts.lifecycle_deployment_v2 import load_continuous_research
            loader = load_continuous_research
        config = json.loads(args.deployment_config.read_text(encoding='utf-8-sig'))
        research = loader(args.workspace_root, config, args.research_id)
        if args.run:
            for _ in range(args.max_steps):
                steps += 1
                result = research.advance()
                status = result.get('status', '')
                if status.startswith('WAITING') or status in {'PAUSED', 'REVOKED', 'RECONCILIATION_REQUIRED', 'CREATED', 'BUSINESS_GOAL_MET'}:
                    break
        report = audit_continuous_universe_acceptance(research)
        report['execution_requested'], report['advance_calls'] = args.run, steps
    except Exception as exc:
        # 不回显异常消息、部署内容、gateway URL 或凭据。
        report = {'version': VERSION, 'status': 'BLOCKED', 'delivery_gate_passed': False, 'exit_code': 2,
            'read_only': not args.run, 'blockers': ['TRUSTED_APPROVED_DEPLOYMENT_OR_EVIDENCE_UNAVAILABLE'],
            'execution_requested': args.run, 'advance_calls': steps,
            'error_type': type(exc).__name__, 'qualification_granted': False}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) if args.json else _human(report))
    return report['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
