"""有限新版报告恢复完成后准备原campaign第二批；旧V1/V2原件保持，不审批或启动研究。"""
from __future__ import annotations

import hashlib
import importlib.util
from copy import deepcopy
from pathlib import Path
import sys


WORKSPACE = Path('E:/llmwiki/trade-system-contract-port-v1')
REPORT = WORKSPACE / 'reports/continuous_universe_real_acceptance_20261010'
LEGACY_PATH = REPORT / 'session_next_batch_setup_v1.py'
LEGACY_SHA = '43e5dc74cf5c7c01c0c79449fe3fc519f850ac938ab93ef28a1261d7aea9fbb9'
VERSION = 'SESSION_NEXT_BATCH_SETUP_V3'


def _legacy():
    sys.path[:0] = [str(WORKSPACE / 'src'), str(WORKSPACE)]
    from chanlun_trader.research_factory.secure_file_reference_v1 import read_file_bytes
    raw = read_file_bytes(LEGACY_PATH, root=WORKSPACE, maximum_bytes=1024 * 1024,
                          error_code='SESSION_NEXT_V3_LEGACY_INVALID')
    if hashlib.sha256(raw).hexdigest() != LEGACY_SHA:
        raise PermissionError('SESSION_NEXT_V3_LEGACY_SOURCE_CHANGED')
    spec = importlib.util.spec_from_file_location('session_next_fixed_legacy_v1', LEGACY_PATH)
    old = importlib.util.module_from_spec(spec)
    exec(compile(raw, str(LEGACY_PATH), 'exec'), old.__dict__)
    return old


def _repair_protocol(root, reference):
    from chanlun_trader.research_factory.report_recovery_protocol_v2 import ReportRecoveryV2
    return ReportRecoveryV2(root, reference).validate(for_dispatch=False)


def _updated_source_pins(old, runner, pins, repair):
    replacements = {row['path']: row for row in repair.summary['source_replacements']}
    runtime = {row['path']: row for row in repair.summary['runtime_sources']}
    changed = []
    for reference in pins:
        value = deepcopy(reference)
        replacement = replacements.get(value['path'])
        approved_runtime = runtime.get(value['path'])
        if replacement is not None:
            old._require(value['sha256'] == replacement['old_sha256'],
                         'SESSION_NEXT_V3_ORIGINAL_SOURCE_PIN_CONFLICT')
            value['sha256'] = replacement['new_sha256']
            old._require(approved_runtime is None or approved_runtime['sha256'] == value['sha256'],
                         'SESSION_NEXT_V3_APPROVED_SOURCE_PIN_CONFLICT')
        elif approved_runtime is not None:
            # pins 来自已固定 SHA 的原受保护配置；新 SHA 必须在专门 Owner 批准的闭包内。
            value['sha256'] = approved_runtime['sha256']
        runner.pinned(value, parse=False)
        changed.append(value)
    return changed


def _load_first(old, runner, setup_sha256, repair):
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
    setup_ref = {'path': str(old.CAMPAIGN_SETUP), 'sha256': old._digest(setup_sha256)}
    setup = runner.pinned(setup_ref)
    old._require(setup.get('schema_version') == 'SESSION_REAL_CAMPAIGN_SETUP_V1'
        and setup.get('identity') == stable_hash({k: v for k, v in setup.items() if k != 'identity'})
        and setup.get('campaign_id') == old.CAMPAIGN_ID and setup.get('objective_id') == old.OBJECTIVE_ID
        and setup.get('batch_ids') == [old.FIRST_BATCH, old.NEXT_BATCH],
        'SESSION_NEXT_V3_ORIGINAL_SETUP_CONFLICT')
    first_ref = deepcopy(setup['runner_config'])
    old._fixed_reference(first_ref, old.FIRST_CONFIG)
    # 原配置字节仍冻结；不能调用会强制旧源码 live SHA 的旧 load_config。
    config = runner.pinned(first_ref)
    old._require(isinstance(config, dict) and set(config) == runner.FIELDS
        and config.get('schema_version') == 'CODEX_SESSION_PUBLIC_RUNNER_V1'
        and Path(config['workspace_root']) == WORKSPACE
        and config['campaign_id'] == old.CAMPAIGN_ID and config['batch_id'] == old.FIRST_BATCH
        and config['runner_sha256'] == old.RUNNER_SHA and Path(config['audit_root']) == old.FIRST_AUDIT
        and isinstance(config['pins'], list), 'SESSION_NEXT_V3_FIRST_CONFIG_CONFLICT')
    for key in ('deployment', 'request', 'proposal', 'context', 'rule'):
        runner.pinned(config[key], parse=False)
    source_pins = _updated_source_pins(old, runner, config['pins'], repair)
    campaign = ResearchCampaignV1(WORKSPACE, old.CAMPAIGN_ID)
    view, contract = old._campaign_view(runner, campaign, config)
    old._require(setup['contract_identity'] == contract['content_hash'],
                 'SESSION_NEXT_V3_SETUP_CONTRACT_CHANGED')
    return {'setup_ref': setup_ref, 'config_ref': first_ref, 'config': config, 'source_pins': source_pins,
            'campaign': campaign, 'view': view, 'contract': contract}


def _first_feedback(old, runner, first, repair, manifest_ref):
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.campaign_scope_v1 import CampaignScopeV1
    from chanlun_trader.research_factory.strategy_submission_v1 import public_rule_factory
    from chanlun_trader.research_factory.business_validation_protocol_v1 import (
        BusinessValidationProtocolV1, evaluate_business_reports, verify_exploration_binding)
    from scripts.lifecycle_deployment_v2 import build_submission_service
    config, view, contract = first['config'], first['view'], first['contract']
    candidate, rule = config['candidate_id'], runner.pinned(config['rule'])
    strategy = public_rule_factory(rule, candidate)
    operation = view['operations'].get(candidate + '_CANDIDATE', {})
    old._require(operation.get('status') == 'FAILED' and operation.get('batch_id') == old.FIRST_BATCH
        and operation.get('kind') == 'CANDIDATE', 'SESSION_NEXT_V3_ORIGINAL_FAILED_REQUIRED')
    start = runner.pinned(old._reference(old.FIRST_AUDIT / 'START.json'))
    old._require(start == {'config': first['config_ref'], 'runner_sha256': old.RUNNER_SHA,
        'candidate_id': candidate, 'batch_id': old.FIRST_BATCH, 'rule_identity': strategy.rule_identity,
        'source': 'CODEX_SESSION_DRIVEN', 'external_session_usage': 'UNKNOWN_EXTERNAL',
        'project_model_api_calls': 0, 'model_receipt': 'NOT_CREATED'}, 'SESSION_NEXT_V3_FIRST_START_CONFLICT')
    attempt = runner.pinned(old._reference(old.FIRST_AUDIT / 'ATTEMPT_END.json'))
    old._require(attempt.get('identity') == stable_hash({k: v for k, v in attempt.items() if k != 'identity'})
        and attempt['identity'] == operation.get('evidence_identity') and attempt.get('outcome') == 'FAILED'
        and attempt.get('candidate_id') == candidate and attempt.get('batch_id') == old.FIRST_BATCH
        and attempt.get('rule_identity') == strategy.rule_identity and attempt.get('config') == first['config_ref']
        and attempt.get('source') == 'CODEX_SESSION_DRIVEN'
        and attempt.get('external_session_usage') == 'UNKNOWN_EXTERNAL'
        and attempt.get('evidence', {}).get('reason') == 'JOB_COMPUTE_WORKER_FAILED_NO_RETRY:REPORT',
        'SESSION_NEXT_V3_ORIGINAL_REPORT_FAILURE_CONFLICT')
    record = repair.verify_receipt()
    outcome = record['outcome']
    task_id = old._digest(outcome['task_id'])
    old._require(record.get('status') == 'REPORT_RECOVERED' and record.get('repair_id') == repair.repair_id
        and record.get('manifest_ref') == manifest_ref and repair.scope_reference == manifest_ref
        and attempt['evidence'].get('task_id') == task_id,
        'SESSION_NEXT_V3_REPAIR_RECEIPT_CONFLICT')
    deployment = runner.pinned(config['deployment'])
    submission = build_submission_service(WORKSPACE, deployment,
        continuous_scope=CampaignScopeV1(first['campaign']))
    task_root = submission.root / task_id
    old._require(repair.job_path == task_root / 'account/JOB.json'
        and repair.output_root == task_root / 'report-repairs' / repair.repair_id,
        'SESSION_NEXT_V3_REPAIR_TASK_CONFLICT')
    task_ref = old._reference(task_root / 'TASK.json')
    task = runner.pinned(task_ref)
    bound = runner.pinned(old._reference(old.FIRST_AUDIT / 'BOUND_REQUEST.json'))
    preview = runner.pinned(old._reference(old.FIRST_AUDIT / 'PREVIEW.json'))
    canonical = runner.pinned(old._reference(task_root / 'PREVIEW.json'))
    frozen = runner.pinned(old._reference(old.FIRST_AUDIT / 'FREEZE_STARTED.json'))
    original = runner.pinned(config['request'])
    old._require(task.get('task_id') == task_id and task.get('submission_version') == 'FULL_UNIVERSE_SUBMISSION_V4'
        and Path(task.get('job_path', '')) == repair.job_path
        and outcome.get('rule_identity') == preview.get('rule_identity') == strategy.rule_identity
        and outcome.get('phase') == 'EXPLORATION' and outcome.get('input_identity') == task.get('input_identity')
        and canonical == preview and preview.get('preview_identity') == task.get('preview_identity')
        and preview['preview_identity'] == stable_hash({k: v for k, v in preview.items() if k != 'preview_identity'})
        and frozen == {'task_id': task_id, 'preview_identity': preview['preview_identity']}
        and task_id == stable_hash({'preview': preview['preview_identity'], 'objective_id': old.OBJECTIVE_ID})
        and {k: v for k, v in bound.items() if k != 'research_binding_ref'} == original
        and all(preview['request'].get(k) == v for k, v in bound.items()),
        'SESSION_NEXT_V3_FIRST_CANONICAL_TASK_CONFLICT')
    verify_exploration_binding(submission, bound, contract,
        candidate_identity=strategy.rule_identity, batch_id=old.FIRST_BATCH)
    refs = BusinessValidationProtocolV1.verify_report_repair_refs(task, outcome, root=WORKSPACE,
        manifest_ref=manifest_ref, minimum_sessions=504)
    old._require(set(refs) == {'BASE', 'STRESS'}
        and all(type(row.get('sessions')) is int and row['sessions'] >= 504 for row in refs.values())
        and refs['BASE']['sessions'] == refs['STRESS']['sessions'], 'SESSION_NEXT_V3_ACTUAL_504_REQUIRED')
    job = runner.pinned({'path': str(repair.job_path), 'sha256': task['job_sha256']})
    old._require(set(task['plan_ids']) == set(job.get('items', {}))
        and all(job['items'][name].get('factory_kwargs', {}).get('payload') == rule for name in task['plan_ids']),
        'SESSION_NEXT_V3_FIRST_EXECUTED_RULE_CONFLICT')
    reports = {cost: runner.pinned({'path': row['report_ref']['research_report'],
        'sha256': row['report_ref']['research_report_sha256']}) for cost, row in refs.items()}
    receipt_ref = old._reference(repair.output_root / 'REPAIR_COMPLETION.json')
    from chanlun_trader.research_factory.report_repair_protocol_v1 import MAX_REPAIR_EVIDENCE_BYTES
    from chanlun_trader.research_factory.secure_file_reference_v1 import read_pinned_json
    old._require(read_pinned_json(receipt_ref, root=WORKSPACE, maximum_bytes=MAX_REPAIR_EVIDENCE_BYTES,
        error_code='SESSION_NEXT_V3_REPAIR_RECEIPT_ORIGINAL_CONFLICT') == record,
        'SESSION_NEXT_V3_REPAIR_RECEIPT_ORIGINAL_CONFLICT')
    feedback_ref = old._reference(repair.output_root / 'FEEDBACK.json')
    feedback = runner.pinned(feedback_ref)
    rebuilt = {'evaluation': evaluate_business_reports(contract, reports, minimum_sessions=504),
        'report_refs': refs, 'rule_identity': strategy.rule_identity, 'task_id': task_id,
        'task_sha256': task_ref['sha256'], 'repair_manifest': manifest_ref, 'repair_receipt': receipt_ref,
        'original_candidate_status': 'FAILED', 'engineering_status': 'REPORT_RECOVERED',
        'account_summaries': {cost: {k: v for k, v in report['account'].items() if k != 'episodes'}
                              for cost, report in reports.items()},
        'signal_evidence': {cost: report['signal'] for cost, report in reports.items()},
        'independent_days': 0, 'formal_qualification': False, 'paper_days': 0,
        'parent_terminal_ref': repair.summary['parent_terminal_ref']}
    old._require(feedback == rebuilt, 'SESSION_NEXT_V3_FEEDBACK_REBUILD_CONFLICT')
    return {'reference': feedback_ref, 'rule_identity': strategy.rule_identity, 'task_id': task_id,
            'submission_root': submission.root, 'receipt_ref': receipt_ref, 'manifest_ref': manifest_ref}


def _capacity(old, view):
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
    from chanlun_trader.research_factory.universe_execution_profile_v1 import validate_execution_profile
    ResearchCampaignV1._dispatchable(view, 'EXPLORATION')
    base, auth = view['base_authorization'], view['authorization']
    expected_grants = {'SESSION_B001_REPORT_REPAIR_20261011', 'SESSION_B001_REPORT_RECOVERY_20261011'}
    old._require(set(view.get('grants', {})) == expected_grants,
                 'SESSION_NEXT_V3_EXACT_TWO_REPORT_GRANTS_REQUIRED')
    delta = dict.fromkeys(view['remaining'], 0)
    delta['verification_jobs'] = 1
    for grant_id, row in view['grants'].items():
        grant = row['grant']
        old._require(grant['grant_id'] == grant_id and grant['base_authorization_hash'] == stable_hash(base)
            and grant['resource_limits_delta'] == delta
            and grant['stage_limits_delta'] == {'EXPLORATION': delta}
            and grant['max_batches_delta'] == grant['max_total_predictive_trials_delta'] == 0
            and grant['expires_at'] == base['expires_at'], 'SESSION_NEXT_V3_REPORT_GRANT_SCOPE_CONFLICT')
    total_delta = {key: value * 2 for key, value in delta.items()}
    old._require(auth['stages'] == ['EXPLORATION'] and auth['max_batches'] == 2
        and auth['max_total_predictive_trials'] == 6 and auth['max_trials_per_batch'] == 3
        and auth['max_candidates_per_batch'] == auth['max_hypotheses_per_batch'] == 1
        and auth['expires_at'] == base['expires_at']
        and auth['execution_profiles'] == base['execution_profiles']
        and auth['resource_limits'] == {k: v + total_delta[k] for k, v in base['resource_limits'].items()},
        'SESSION_NEXT_V3_REPAIR_GRANT_SCOPE_CONFLICT')
    original_stages = base['scope_policy']['summary']['stage_limits']
    expected_stages = {stage: {key: value + (total_delta[key] if stage == 'EXPLORATION' else 0)
        for key, value in units.items()} for stage, units in original_stages.items()}
    old._require(auth['scope_policy']['summary']['stage_limits'] == expected_stages,
                 'SESSION_NEXT_V3_STAGE_LIMIT_SCOPE_CONFLICT')
    old._require(all(item.get('status') in ('COMPLETED', 'FAILED') and not item.get('active_segment')
        for item in view['operations'].values()), 'SESSION_NEXT_UNSETTLED_OR_UNKNOWN_OPERATION')
    old._require(not any(item.get('batch_id') == old.NEXT_BATCH for item in view['operations'].values()),
                 'SESSION_NEXT_BATCH_ALREADY_USED')
    profiles = [validate_execution_profile(p) for p in auth['execution_profiles']]
    purposes = {p['purpose']: p for p in profiles}
    old._require(len(purposes) == len(profiles) == 4 and set(purposes) == {
        'RESEARCH_PREPARATION', 'RESEARCH_ACCOUNT', 'RESEARCH_VERIFICATION', 'RESEARCH_REPORT'},
        'SESSION_NEXT_FOUR_FROZEN_PROFILES_REQUIRED')
    needed = dict.fromkeys(view['remaining'], 0)
    needed.update(candidate_attempts=1, data_experiments=1, account_jobs=2, verification_jobs=2,
        wall_seconds=1 + sum(p['total_seconds'] for p in profiles) + purposes['RESEARCH_ACCOUNT']['total_seconds'])
    old._require(all(view['remaining'].get(k, -1) >= v and
        view['stage_remaining']['EXPLORATION'].get(k, -1) >= v for k, v in needed.items())
        and view['trial_usage']['remaining_capacity'] >= 3 and view['batch_count'] < auth['max_batches'],
        'SESSION_NEXT_V3_INSUFFICIENT_BUDGET')
    return needed


def _pins(old, runner, first, proposal, feedback, repair):
    refs = [*first['source_pins'], *repair.summary['runtime_sources'], first['setup_ref'], first['config_ref'],
        feedback['reference'], feedback['receipt_ref'], feedback['manifest_ref'], proposal['proposal_ref'],
        proposal['context_ref'], proposal['rule_ref'], *proposal['design_refs'],
        {'path': str(LEGACY_PATH), 'sha256': LEGACY_SHA}, old._reference(Path(__file__))]
    retained = {str(Path(feedback[key]['path'])) for key in ('reference', 'receipt_ref', 'manifest_ref')}
    pins, seen = [], {}
    for ref in refs:
        path = Path(ref['path'])
        if (path.name in {'run_budget.json', 'run_budget_events.jsonl', 'execution_registry.json'}
            or path.is_relative_to(old.FIRST_AUDIT) or path.is_relative_to(feedback['submission_root'])):
            if str(path) not in retained:
                continue
        old._require(str(path) not in seen or seen[str(path)] == ref['sha256'],
                     'SESSION_NEXT_DUPLICATE_PIN_CONFLICT')
        if str(path) not in seen:
            runner.pinned(ref, parse=False)
            pins.append(deepcopy(ref))
            seen[str(path)] = ref['sha256']
    old._require(sum(Path(row['path']) == runner.BINDING_PATH for row in pins) == 1,
                 'SESSION_NEXT_ONE_PROTECTED_BINDING_REQUIRED')
    old._require(any(Path(row['path']) == Path(__file__) for row in repair.summary['runtime_sources']),
                 'SESSION_NEXT_V3_HELPER_NOT_IN_APPROVED_CLOSURE')
    return pins


def prepare(args):
    old = _legacy()
    from chanlun_trader.research_factory.common import stable_hash
    runner = old._load_runner()
    manifest_ref = {'path': args.report_repair_manifest, 'sha256': old._digest(args.report_repair_manifest_sha256)}
    repair = _repair_protocol(WORKSPACE, manifest_ref)
    first = _load_first(old, runner, args.campaign_setup_sha256, repair)
    feedback = _first_feedback(old, runner, first, repair, manifest_ref)
    original = runner.pinned(first['config']['request'])
    proposal = old._proposal(runner, args, feedback, first['contract'], original, first['config']['candidate_id'])
    pins = _pins(old, runner, first, proposal, feedback, repair)
    intent = {'schema_version': 'SESSION_NEXT_BATCH_INTENT_V3', 'campaign_id': old.CAMPAIGN_ID,
        'objective_id': old.OBJECTIVE_ID, 'batch_id': old.NEXT_BATCH, 'candidate_id': proposal['candidate_id'],
        'rule_identity': proposal['rule_identity'], 'contract_identity': first['contract']['content_hash'],
        'first_feedback': feedback['reference'], 'repair_manifest': manifest_ref,
        'repair_receipt': feedback['receipt_ref'], 'original_candidate_status': 'FAILED',
        'original_request_identity': stable_hash(original), 'new_request_identity': stable_hash(proposal['request']),
        'pins': pins, 'runner_sha256': old.RUNNER_SHA, 'external_session_usage': 'UNKNOWN_EXTERNAL',
        'project_model_api_calls': 0, 'new_authorization_created': False}
    view, contract = old._campaign_view(runner, first['campaign'], first['config'])
    old._require(contract == first['contract'], 'SESSION_NEXT_CONTRACT_CHANGED_DURING_PREPARATION')
    # 重新读取完整修复证据后才允许写第二批配置，不伪造原候选终态。
    old._require(_first_feedback(old, runner, {**first, 'view': view}, repair, manifest_ref) == feedback,
                 'SESSION_NEXT_V3_REPAIR_CHANGED_DURING_PREPARATION')
    existing = (old.OUTPUT / 'BATCH_002_SETUP.json').is_file()
    if not existing:
        _capacity(old, view)
    else:
        old._require(not any(row.get('status') == 'UNKNOWN' or row.get('active_segment')
            for row in view['operations'].values()), 'SESSION_NEXT_UNKNOWN_OPERATION')
    intent_ref = old._write_once(runner, old.OUTPUT / 'BATCH_002_INTENT.json', intent)
    request_ref = old._write_once(runner, old.OUTPUT / 'BATCH_002_REQUEST.json', proposal['request'])
    config = {**deepcopy(first['config']), 'batch_id': old.NEXT_BATCH, 'candidate_id': proposal['candidate_id'],
        'audit_root': str(old.NEXT_AUDIT), 'request': request_ref, 'proposal': proposal['proposal_ref'],
        'context': proposal['context_ref'], 'rule': proposal['rule_ref'], 'pins': [*pins, intent_ref],
        'runner_sha256': old.RUNNER_SHA}
    old._require(set(config) == runner.FIELDS and len(config) == 13, 'SESSION_NEXT_RUNNER_SCHEMA_CONFLICT')
    config_ref = old._write_once(runner, old.OUTPUT / 'BATCH_002_RUNNER_CONFIG.json', config)
    receipt = {'schema_version': VERSION, 'campaign_id': old.CAMPAIGN_ID, 'objective_id': old.OBJECTIVE_ID,
        'batch_id': old.NEXT_BATCH, 'candidate_id': proposal['candidate_id'], 'rule_identity': proposal['rule_identity'],
        'intent': intent_ref, 'request': request_ref, 'runner_config': config_ref,
        'first_feedback': feedback['reference'], 'repair_manifest': manifest_ref,
        'repair_receipt': feedback['receipt_ref'], 'original_candidate_status': 'FAILED',
        'runner_sha256': old.RUNNER_SHA, 'runner_argv': [str(WORKSPACE / '.venv/Scripts/python.exe'),
            '-B', '-X', 'utf8', str(old.RUNNER), '--job-config', config_ref['path'],
            '--job-config-sha256', config_ref['sha256']], 'new_authorization_created': False,
        'budget_reserved': False, 'runner_started': False, 'external_session_usage': 'UNKNOWN_EXTERNAL',
        'model_receipt': 'NOT_CREATED'}
    receipt['identity'] = stable_hash(receipt)
    receipt_ref = old._write_once(runner, old.OUTPUT / 'BATCH_002_SETUP.json', receipt)
    return {'status': 'EXISTING_BATCH_002_SETUP_VERIFIED' if existing else 'BATCH_002_SETUP_READY_ONLY',
        'setup': receipt_ref, 'runner_config': config_ref, 'runner_argv': receipt['runner_argv'],
        'runner_started': False, 'new_authorization_created': False, 'budget_reserved': False}


def parser():
    cli = _legacy().parser()
    cli.description = __doc__
    cli.add_argument('--report-recovery-manifest', dest='report_repair_manifest', required=True)
    cli.add_argument('--report-recovery-manifest-sha256', dest='report_repair_manifest_sha256', type=_legacy()._digest, required=True)
    return cli


def main(argv=None):
    import json
    import subprocess
    args = parser().parse_args(argv)
    try:
        result = prepare(args)
    except (ValueError, PermissionError, OSError, RuntimeError, KeyError, TypeError,
            subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'REJECTED', 'reason': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
