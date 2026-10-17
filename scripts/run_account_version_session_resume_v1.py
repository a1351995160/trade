"""用原 Owner 的账户版本接续证明恢复既有会话；不预览、冻结或签发新用途。"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]

from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
from chanlun_trader.research_factory.business_validation_protocol_v1 import (
    BusinessValidationProtocolV1, evaluate_business_reports, verify_exploration_binding,
)
from chanlun_trader.research_factory.campaign_scope_v1 import CampaignScopeV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.continuous_submission_v1 import reconcile_submission
from chanlun_trader.research_factory.exploration_governance import immutable
from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.run_budget import effective_execution_status
from chanlun_trader.research_factory.secure_file_reference_v1 import (
    checked_file_path, file_sha256, read_file_bytes, read_pinned_json,
)
from chanlun_trader.research_factory.strategy_submission_v1 import public_rule_factory
from scripts.lifecycle_deployment_v2 import build_submission_service


SESSION_REFS = {'config', 'runner', 'audit_start', 'bound_request', 'preview', 'freeze_started'}
PROOF = 'account_version_continuation'
ORIGINAL_RUNNER = Path('reports/continuous_universe_real_acceptance_20261010/session_public_runner_v1.py')


def require(condition, reason):
    if not condition:
        raise PermissionError('ACCOUNT_VERSION_SESSION_' + reason)


def read(path, root):
    return json.loads(read_file_bytes(path, root=root, error_code='ACCOUNT_VERSION_SESSION_PATH_INVALID'))


def digest(path, root):
    return file_sha256(path, root=root, error_code='ACCOUNT_VERSION_SESSION_PATH_INVALID')


def load_session(job_path):
    """所有旧会话原件先核哈希与 ACL，再建立同任务的公共服务。"""
    job_path = checked_file_path(job_path, error_code='ACCOUNT_VERSION_SESSION_JOB_INVALID')
    job = read(job_path, job_path.parent)
    context = AccountVersionContinuationV1.from_job(job, for_reconcile=True)
    require(context is not None and context.job_path == job_path, 'APPROVED_CONTINUATION_REQUIRED')
    context.validate(for_reconcile=True)
    require(context.time_extension is None or context.time_extension_installed, 'TIME_EXTENSION_NOT_INSTALLED')
    require(getattr(context, 'recovery', None) is None or context.recovery_installed,
            'FAILED_ACCOUNT_RECOVERY_NOT_INSTALLED')
    summary, refs = context.summary_value, context.summary_value.get('session_refs', {})
    require(set(refs) == SESSION_REFS, 'ORIGINAL_REFERENCES_REQUIRED')
    workspace, owner = Path(summary['root']), Path(summary['approval_store'])
    runner_path = checked_file_path(refs['runner']['path'], root=workspace / 'reports',
                                    error_code='ACCOUNT_VERSION_SESSION_RUNNER_INVALID')
    require(runner_path == workspace / ORIGINAL_RUNNER
        and digest(runner_path, workspace) == refs['runner']['sha256'], 'ORIGINAL_RUNNER_CHANGED')
    specification = importlib.util.spec_from_file_location('_account_version_original_session_runner', runner_path)
    require(specification is not None and specification.loader is not None, 'ORIGINAL_RUNNER_INVALID')
    legacy = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(legacy)
    require(legacy.ROOT.resolve() == workspace and legacy.OWNER_ROOT.resolve() == owner,
            'ORIGINAL_BOUNDARY_CONFLICT')
    legacy.verify_owner_boundary()
    logical_config = legacy.OWNER_ROOT / 'campaign_setup' / 'BATCH_002_RUNNER_CONFIG.json'
    require(refs['config']['path'] == str(logical_config)
        and logical_config.resolve(strict=True) == owner / 'campaign_setup' / 'BATCH_002_RUNNER_CONFIG.json',
        'ORIGINAL_CONFIG_PATH_CONFLICT')
    # MSIX 的逻辑路径仍是旧 START 的原引用；仅文件读取使用已核对的规范路径。
    config = read_pinned_json({**refs['config'], 'path': str(logical_config.resolve(strict=True))},
                             root=owner, error_code='ACCOUNT_VERSION_SESSION_CONFIG_CHANGED')
    require(set(config) == legacy.FIELDS and config.get('schema_version') == 'CODEX_SESSION_PUBLIC_RUNNER_V1'
        and Path(config['workspace_root']) == workspace
        and config['runner_sha256'] == refs['runner']['sha256'], 'ORIGINAL_CONFIG_CONFLICT')
    for key in ('campaign_id', 'batch_id', 'candidate_id'):
        require(re.fullmatch(r'[A-Za-z0-9_-]{1,120}', str(config[key])) is not None, 'IDENTIFIER_INVALID')
    require(isinstance(config['pins'], list) and len(config['pins']) == 55
        and len({reference['path'] for reference in config['pins']}) == 55, 'ORIGINAL_55_PINS_REQUIRED')
    require(sum(reference == refs['runner'] for reference in config['pins']) == 1, 'ORIGINAL_RUNNER_PIN_REQUIRED')
    replacements = {item['path']: item for item in context.approved_source_replacements()}
    for reference in [*(config[key] for key in ('deployment', 'request', 'proposal', 'context', 'rule')),
                      *config['pins']]:
        replacement = replacements.get(reference['path'])
        if replacement is None:
            legacy.pinned(reference, parse=False)
        else:
            require(Path(reference['path']).suffix == '.py'
                and replacement['old_sha256'] == reference['sha256'], 'PIN_REPLACEMENT_CONFLICT')
            legacy.pinned(replacement['archive_ref'], parse=False)
            legacy.pinned({'path': reference['path'], 'sha256': replacement['new_sha256']}, parse=False)
    own_path = workspace / 'scripts' / Path(__file__).name
    own_reference = {'path': str(own_path), 'sha256': digest(own_path, workspace)}
    require(Path(__file__).resolve() == own_path and own_reference in context.approved_runtime_sources(),
            'RESUME_RUNNER_NOT_APPROVED')
    audit = Path(config['audit_root'])
    require(audit.resolve() == audit and audit.is_relative_to(runner_path.parent) and audit != runner_path.parent,
            'AUDIT_ROOT_CONFLICT')
    fixed_paths = {'audit_start': audit / 'START.json', 'bound_request': audit / 'BOUND_REQUEST.json',
                   'preview': audit / 'PREVIEW.json', 'freeze_started': audit / 'FREEZE_STARTED.json'}
    originals = {}
    for name, path in fixed_paths.items():
        require(refs[name]['path'] == str(path), 'ORIGINAL_AUDIT_PATH_CONFLICT')
        originals[name] = read_pinned_json(refs[name], root=workspace,
                                           error_code='ACCOUNT_VERSION_SESSION_ORIGINAL_CHANGED')
    task_root = context.task_root
    task, preview = read(task_root / 'TASK.json', workspace), originals['preview']
    task_id = task['task_id']
    require(re.fullmatch(r'[a-f0-9]{64}', task_id) is not None
        and task_root.name == task_id and Path(task['job_path']) == job_path
        and task['job_sha256'] == digest(job_path, workspace)
        and task['plan_ids'] == {name: plan['plan_id'] for name, plan in job['plans'].items()}
        and preview == read(task_root / 'PREVIEW.json', workspace)
        and preview['preview_identity'] == task['preview_identity']
        and preview['preview_identity'] == stable_hash({key: value for key, value in preview.items()
                                                       if key != 'preview_identity'})
        and originals['freeze_started'] == {'task_id': task_id, 'preview_identity': task['preview_identity']},
        'ORIGINAL_TASK_OR_PREVIEW_CONFLICT')
    start = originals['audit_start']
    require(start['config'] == refs['config'] and start['runner_sha256'] == config['runner_sha256']
        and all(start.get(key) == config[key] for key in ('candidate_id', 'batch_id')),
        'ORIGINAL_START_CONFLICT')
    rule = legacy.pinned(config['rule'])
    strategy = public_rule_factory(rule, config['candidate_id'])
    require(start['rule_identity'] == strategy.rule_identity == preview['rule_identity']
        and set(job['plans']) == {config['candidate_id'] + '_' + cost for cost in ('BASE', 'STRESS')}
        and all(preview['request'].get(key) == value for key, value in originals['bound_request'].items())
        and all(item['factory_kwargs']['payload'] == rule for item in job['items'].values()),
        'ORIGINAL_RULE_OR_REQUEST_CONFLICT')
    campaign = ResearchCampaignV1(workspace, config['campaign_id'])
    view = campaign.peek_status()
    contract = legacy.protected_campaign_contract(config, view)
    submission = build_submission_service(workspace, legacy.pinned(config['deployment']),
                                           continuous_scope=CampaignScopeV1(campaign))
    require(submission.root == task_root.parent, 'ORIGINAL_SUBMISSION_ROOT_CONFLICT')
    verify_exploration_binding(submission, originals['bound_request'], contract,
                              candidate_identity=strategy.rule_identity, batch_id=config['batch_id'])
    return {'context': context, 'config': config, 'config_ref': refs['config'], 'workspace': workspace,
            'audit': audit, 'task': task, 'submission': submission, 'campaign': campaign,
            'contract': contract, 'bound_request': originals['bound_request'], 'rule_identity': strategy.rule_identity,
            'runner_sha256': own_reference['sha256']}


def run_session(job_path, *, max_steps=1):
    require(type(max_steps) is int and 1 <= max_steps <= 100, 'STEP_BOUND_INVALID')
    session = load_session(job_path)
    context, config, audit = session['context'], session['config'], session['audit']
    proof, task = context.proof(), session['task']
    campaign, submission = session['campaign'], session['submission']
    operation_id = config['candidate_id'] + '_CANDIDATE'
    with ObjectiveMutationLock(session['workspace'],
            'session-public-runner:' + config['campaign_id'] + ':' + config['candidate_id']):
        events, previous = [], None
        for path in sorted((audit / 'events').glob('*.json')):
            item = read(path, session['workspace'])
            require(path.stem == str(len(events) + 1).zfill(6) and item['previous'] == previous
                and item['identity'] == stable_hash({key: value for key, value in item.items() if key != 'identity'}),
                'EVENT_CHAIN_CHANGED')
            events.append(item)
            previous = item['identity']

        def event(action, payload):
            nonlocal previous
            item = {'action': action, 'payload': payload, 'previous': previous,
                    'recorded_at': datetime.now(timezone.utc).isoformat()}
            item['identity'] = stable_hash(item)
            immutable(audit / 'events' / (str(len(events) + 1).zfill(6) + '.json'), item)
            events.append(item)
            previous = item['identity']

        def children():
            return [row for row in campaign.peek_status()['operations'].values()
                    if row['batch_id'] == config['batch_id'] and row['kind'] != 'CANDIDATE']

        def children_terminal():
            return all(effective_execution_status(row) in ('COMPLETED', 'FAILED') and not row.get('active_segment')
                       for row in children())

        def attempt_record(outcome, evidence):
            record = {'candidate_id': config['candidate_id'], 'batch_id': config['batch_id'],
                'rule_identity': session['rule_identity'], 'config': session['config_ref'], 'outcome': outcome,
                'evidence': evidence, 'source': 'CODEX_SESSION_DRIVEN',
                'external_session_usage': 'UNKNOWN_EXTERNAL', PROOF: proof}
            record['identity'] = stable_hash(record)
            return record

        def settle_attempt(record):
            context.validate(for_dispatch=False)
            require(record['outcome'] != 'FAILED' or children_terminal(), 'FAILED_CHILDREN_NOT_TERMINAL')
            immutable(audit / 'ATTEMPT_END.json', record)
            campaign.settle_operation(operation_id, actual={'candidate_attempts': 1, 'wall_seconds': 1},
                                      outcome=record['outcome'], evidence_identity=record['identity'])
            return record

        def verify_attempt(record, operation):
            context.validate(for_dispatch=False)
            require(isinstance(record, dict) and record.get('outcome') in ('COMPLETED', 'FAILED')
                and record == attempt_record(record.get('outcome'), record.get('evidence'))
                and (operation['status'] == 'RUNNING' or (record['outcome'] == operation['status']
                    and record['identity'] == operation['evidence_identity'])), 'TERMINAL_ATTEMPT_CONFLICT')
            if record['outcome'] == 'FAILED':
                evidence = record['evidence']
                require(isinstance(evidence, dict) and set(evidence) == {
                    'error_type', 'reason', 'task_id', 'canonical_files', PROOF, 'event_identity'}
                    and evidence['task_id'] == task['task_id'] and evidence[PROOF] == proof
                    and isinstance(evidence['error_type'], str) and isinstance(evidence['reason'], str),
                    'TERMINAL_ERROR_CONFLICT')
                require(any(item['identity'] == evidence['event_identity'] and item['action'] == 'ERROR'
                    and item['payload'] == {key: value for key, value in evidence.items() if key != 'event_identity'}
                    for item in events), 'TERMINAL_ERROR_EVENT_CHANGED')
                allowed = {str(context.task_root / 'TASK.json'), str(Path(task['job_path']).parent / 'VERIFICATION.json')}
                references = evidence['canonical_files']
                require(isinstance(references, list)
                    and all(isinstance(row, dict) and set(row) == {'path', 'sha256'}
                        and isinstance(row['path'], str) and row['path'] in allowed for row in references)
                    and len({row['path'] for row in references}) == len(references)
                    and str(context.task_root / 'TASK.json') in {row['path'] for row in references}
                    and all(digest(row['path'], session['workspace']) == row['sha256'] for row in references),
                    'TERMINAL_ERROR_FILES_CHANGED')
                require(children_terminal(), 'FAILED_CHILDREN_NOT_TERMINAL')
                return
            feedback_path = audit / 'FEEDBACK.json'
            require(record['evidence'] == {'feedback': str(feedback_path),
                    'sha256': digest(feedback_path, session['workspace'])}, 'TERMINAL_FEEDBACK_CHANGED')
            feedback = read(feedback_path, session['workspace'])
            require(feedback.get(PROOF) == proof, 'TERMINAL_FEEDBACK_VERSION_CONFLICT')
            refs = BusinessValidationProtocolV1.verify_report_refs(task,
                read(context.task_root / 'PUBLIC_RESULT.json', session['workspace']), minimum_sessions=504)
            require(feedback['report_refs'] == refs, 'TERMINAL_REPORT_REFS_CHANGED')

        def finish_error(error_event):
            evidence = {**error_event['payload'], 'event_identity': error_event['identity']}
            if getattr(context, 'recovery', None) is not None and context.recovery_installed and not any(
                    effective_execution_status(row) == 'UNKNOWN' or row.get('active_segment') for row in children()):
                from scripts.run_strategy_account_v1 import account_execution_paths
                paths = account_execution_paths(context.job, context.recovery.original_failed_account)
                if paths['start'].exists() and not paths['settlement'].exists():
                    from chanlun_trader.research_factory.universe_execution_recovery_v1 import resume_long_horizon_job
                    resume_long_horizon_job(Path(task['job_path']), context.job, reconcile_only=True)
                    context.validate(for_dispatch=False)
            if not children_terminal():
                return {'status': 'WAITING_ORIGINAL_OPERATION_RECONCILIATION',
                        'task_id': task['task_id'], 'evidence': evidence, PROOF: proof}
            record = attempt_record('FAILED', evidence)
            verify_attempt(record, {'status': 'RUNNING'})
            return settle_attempt(record)

        operation = campaign.peek_status()['operations'].get(operation_id)
        require(operation is not None and operation['batch_id'] == config['batch_id']
            and operation['kind'] == 'CANDIDATE' and operation['status'] in ('RUNNING', 'COMPLETED', 'FAILED'),
            'ORIGINAL_CANDIDATE_NOT_RUNNING')
        require(operation.get('stage') == 'EXPLORATION' and operation.get('subject_identity') == stable_hash({
            'rule_identity': session['rule_identity'], 'proposal_sha256': config['proposal']['sha256'],
            'context_sha256': config['context']['sha256']}), 'ORIGINAL_CANDIDATE_IDENTITY_CONFLICT')
        failure = next((item for item in reversed(events) if item['action'] == 'ERROR'
                        and isinstance(item['payload'], dict) and item['payload'].get(PROOF) == proof), None)
        pending_event = next((item['action'] for item in reversed(events) if item['action'] in
                              ('ADVANCE_STARTED', 'ADVANCE_RETURNED', 'RECONCILE_RETURNED', 'ERROR')), None)
        if context.mirrors_pending() or pending_event == 'ADVANCE_STARTED' or any(
                effective_execution_status(row) == 'UNKNOWN' or row.get('active_segment') for row in children()):
            reconciled = reconcile_submission(submission, task['task_id'])
            context.validate(for_dispatch=False)
            event('RECONCILE_RETURNED', reconciled)
            if failure:
                return finish_error(failure)
            return {'status': 'WAITING_RECONCILIATION_REVIEW_NO_REDISPATCH',
                    'task_id': task['task_id'], PROOF: proof}
        context.validate(for_dispatch=False)
        if operation['status'] in ('COMPLETED', 'FAILED') or (audit / 'ATTEMPT_END.json').is_file():
            record = read(audit / 'ATTEMPT_END.json', session['workspace'])
            verify_attempt(record, operation)
            return settle_attempt(record) if operation['status'] == 'RUNNING' else record
        if failure:
            return finish_error(failure)
        try:
            for _ in range(max_steps):
                pending_event = next((item['action'] for item in reversed(events) if item['action'] in
                                      ('ADVANCE_STARTED', 'ADVANCE_RETURNED', 'RECONCILE_RETURNED')), None)
                if pending_event == 'ADVANCE_STARTED' or any(
                        effective_execution_status(row) == 'UNKNOWN' or row.get('active_segment') for row in children()):
                    reconciled = reconcile_submission(submission, task['task_id'])
                    context.validate(for_dispatch=False)
                    event('RECONCILE_RETURNED', reconciled)
                    return {'status': 'WAITING_RECONCILIATION_REVIEW_NO_REDISPATCH',
                            'task_id': task['task_id'], PROOF: proof}
                context.validate(for_dispatch=True)
                event('ADVANCE_STARTED', {'task_id': task['task_id'], PROOF: proof,
                                          'runner_sha256': session['runner_sha256']})
                outcome = submission.advance(task['task_id'])
                require(type(outcome.get('dispatched_segments')) is int
                    and 0 <= outcome['dispatched_segments'] <= 1, 'ADVANCE_SEGMENT_BOUND_VIOLATION')
                context.validate(for_dispatch=False)
                event('ADVANCE_RETURNED', {key: outcome.get(key) for key in ('task_id', 'status', 'dispatched_segments')})
                if outcome['status'] == 'ACCOUNT_VERIFIED':
                    canonical = read(context.task_root / 'PUBLIC_RESULT.json', session['workspace'])
                    refs = BusinessValidationProtocolV1.verify_report_refs(task, canonical, minimum_sessions=504)
                    require(canonical.get(PROOF) == proof and canonical['rule_identity'] == session['rule_identity']
                        and {key: value for key, value in outcome.items() if key != 'dispatched_segments'}
                            == {key: value for key, value in canonical.items() if key != 'dispatched_segments'},
                        'CANONICAL_PUBLIC_RESULT_CONFLICT')
                    reports = {cost: read(row['report_ref']['research_report'], session['workspace'])
                               for cost, row in refs.items()}
                    feedback = {'evaluation': evaluate_business_reports(session['contract'], reports, minimum_sessions=504),
                        'report_refs': refs, 'rule_identity': session['rule_identity'], 'task_id': task['task_id'],
                        'task_sha256': digest(context.task_root / 'TASK.json', session['workspace']),
                        'public_result_sha256': digest(context.task_root / 'PUBLIC_RESULT.json', session['workspace']),
                        'account_summaries': {cost: {key: value for key, value in report['account'].items() if key != 'episodes'}
                                              for cost, report in reports.items()},
                        'signal_evidence': {cost: report['signal'] for cost, report in reports.items()},
                        'independent_days': 0, 'formal_qualification': False, 'paper_days': 0, PROOF: proof}
                    immutable(audit / 'FEEDBACK.json', feedback)
                    return settle_attempt(attempt_record('COMPLETED', {'feedback': str(audit / 'FEEDBACK.json'),
                        'sha256': digest(audit / 'FEEDBACK.json', session['workspace'])}))
                if outcome['status'] in {'EVIDENCE_BLOCKED', 'FAILED', 'ACCOUNTS_FAILED'}:
                    raise ValueError('ACCOUNT_VERSION_SESSION_PUBLIC_TERMINAL_FAILURE:' + outcome['status'])
                if outcome['status'] not in {'ACCOUNTS_CONTINUE', 'ACCOUNTS_COMPLETED', 'VERIFIED', 'CONTINUE'}:
                    return {**outcome, PROOF: proof}
            return {**outcome, PROOF: proof}
        except Exception as error:
            # 已落盘的终态只能按同一 identity 重试结清，不能被结清错误覆盖成 FAILED。
            if (audit / 'ATTEMPT_END.json').is_file():
                raise
            evidence = {'error_type': type(error).__name__, 'reason': str(error), 'task_id': task['task_id'],
                'canonical_files': [{'path': str(path), 'sha256': digest(path, session['workspace'])} for path in
                    (context.task_root / 'TASK.json', Path(task['job_path']).parent / 'VERIFICATION.json') if path.is_file()],
                PROOF: proof}
            event('ERROR', evidence)
            return finish_error(events[-1])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True, type=Path)
    parser.add_argument('--max-steps', type=int, default=1)
    args = parser.parse_args(argv)
    result = run_session(args.job, max_steps=args.max_steps)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result.get('outcome') == 'COMPLETED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
