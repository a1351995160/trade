"""只验证会话恢复路由及原件闭包；不调用真实行情或原 Owner 根。"""
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory.common import stable_hash
from scripts import run_account_version_session_resume_v1 as runner
from test_account_version_feedback_v1 import digest, versioned_reports, write


class ExistingCandidate:
    def __init__(self, config, rule_identity):
        self.operation_id = config['candidate_id'] + '_CANDIDATE'
        self.operations = {self.operation_id: {'batch_id': config['batch_id'], 'kind': 'CANDIDATE',
            'stage': 'EXPLORATION', 'status': 'RUNNING', 'subject_identity': stable_hash({
                'rule_identity': rule_identity, 'proposal_sha256': config['proposal']['sha256'],
                'context_sha256': config['context']['sha256']})}}
        self.settlements = []

    def peek_status(self):
        return {'operations': deepcopy(self.operations)}

    def settle_operation(self, operation_id, **kwargs):
        assert operation_id == self.operation_id
        self.settlements.append(deepcopy(kwargs))
        self.operations[operation_id].update(status=kwargs['outcome'], evidence_identity=kwargs['evidence_identity'])


def route_session(tmp_path, monkeypatch, *, complete=False):
    context, task, outcome = versioned_reports(tmp_path / 'public', monkeypatch)
    context.task_root = Path(task['job_path']).parent.parent
    context.validate = lambda **kwargs: context
    context.mirrors_pending = lambda: False
    config = {'candidate_id': 'SYNTHETIC', 'campaign_id': 'SYNTHETIC_CAMPAIGN', 'batch_id': 'batch_002',
              'proposal': {'sha256': 'a' * 64}, 'context': {'sha256': 'b' * 64}}
    audit = tmp_path / 'audit'
    write(audit / 'START.json', {'SYNTHETIC_ORIGINAL_START': True})
    campaign = ExistingCandidate(config, outcome['rule_identity'])
    calls = []
    def forbidden(*args, **kwargs):
        pytest.fail('账户版本接续不得再次预览、冻结、绑定、批准或创建候选')
    def advance(task_id):
        calls.append(task_id)
        assert task_id == task['task_id']
        return deepcopy(outcome) if complete else {'task_id': task_id, 'status': 'ACCOUNTS_CONTINUE', 'dispatched_segments': 1}
    submission = SimpleNamespace(advance=advance, preview=forbidden, freeze=forbidden,
                                 bind_research_request=forbidden, approve=forbidden)
    write(context.task_root / 'TASK.json', task)
    if complete:
        write(context.task_root / 'PUBLIC_RESULT.json', {**outcome, 'dispatched_segments': 0})
    session = {'context': context, 'task': task, 'config': config, 'audit': audit, 'workspace': tmp_path,
               'submission': submission, 'campaign': campaign, 'contract': {}, 'bound_request': {},
               'rule_identity': outcome['rule_identity'], 'config_ref': {'SYNTHETIC': True}, 'runner_sha256': 'c' * 64}
    monkeypatch.setattr(runner, 'load_session', lambda path: session)
    return session, calls


def test_resume_routes_only_same_existing_task_and_dispatches_one_step(tmp_path, monkeypatch):
    session, calls = route_session(tmp_path, monkeypatch)
    start = (session['audit'] / 'START.json').read_bytes()
    result = runner.run_session(tmp_path / 'unused.json')
    assert calls == [session['task']['task_id']]
    assert result['status'] == 'ACCOUNTS_CONTINUE' and result['dispatched_segments'] == 1
    assert result[runner.PROOF] == session['context'].proof()
    assert (session['audit'] / 'START.json').read_bytes() == start
    assert len(list((session['audit'] / 'events').glob('*.json'))) == 2
    assert session['campaign'].settlements == []
    assert not list(tmp_path.rglob('CONTROL_*.json'))


def test_unknown_original_child_reconciles_without_redispatch(tmp_path, monkeypatch):
    session, calls = route_session(tmp_path, monkeypatch)
    session['campaign'].operations['BASE_ACCOUNT'] = {'batch_id': 'batch_002', 'kind': 'ACCOUNT', 'status': 'UNKNOWN'}
    reconciled = []
    monkeypatch.setattr(runner, 'reconcile_submission',
                        lambda service, task_id: reconciled.append(task_id) or {'dispatched_segments': 0})
    result = runner.run_session(tmp_path / 'unused.json')
    assert result['status'] == 'WAITING_RECONCILIATION_REVIEW_NO_REDISPATCH'
    assert reconciled == [session['task']['task_id']] and calls == []
    assert session['campaign'].settlements == []


def test_canonical_feedback_keeps_member_versions_and_completed_resume_is_idempotent(tmp_path, monkeypatch):
    session, calls = route_session(tmp_path, monkeypatch, complete=True)
    monkeypatch.setattr(runner, 'evaluate_business_reports',
                        lambda *args, **kwargs: {'business_criteria_met': False})
    completed = runner.run_session(tmp_path / 'unused.json')
    assert completed['outcome'] == 'COMPLETED'
    feedback = runner.read(session['audit'] / 'FEEDBACK.json', tmp_path)
    assert feedback[runner.PROOF] == session['context'].proof()
    assert {cost: row[runner.PROOF] for cost, row in feedback['report_refs'].items()} == {
        cost: session['context'].proof(cost) for cost in ('BASE', 'STRESS')}
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    assert runner.run_session(tmp_path / 'unused.json') == completed
    assert calls == [session['task']['task_id']] and len(session['campaign'].settlements) == 1
    assert before == {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}


def test_completed_feedback_tamper_is_rejected_without_redispatch(tmp_path, monkeypatch):
    session, calls = route_session(tmp_path, monkeypatch, complete=True)
    monkeypatch.setattr(runner, 'evaluate_business_reports', lambda *args, **kwargs: {})
    runner.run_session(tmp_path / 'unused.json')
    write(session['audit'] / 'FEEDBACK.json', {})
    with pytest.raises(PermissionError, match='ACCOUNT_VERSION_SESSION_TERMINAL_FEEDBACK_CHANGED'):
        runner.run_session(tmp_path / 'unused.json')
    assert len(calls) == 1 and len(session['campaign'].settlements) == 1


def original_children(session, *, base='COMPLETED', stress='FAILED', active=False):
    for name, status in (('BASE_ACCOUNT', base), ('STRESS_ACCOUNT', stress)):
        session['campaign'].operations[name] = {'batch_id': session['config']['batch_id'],
            'kind': 'ACCOUNT', 'status': status}
    if active:
        session['campaign'].operations['BASE_ACCOUNT']['active_segment'] = {'number': 7}


@pytest.mark.parametrize('failure', ['worker_error', 'report_error', 'EVIDENCE_BLOCKED', 'FAILED', 'ACCOUNTS_FAILED'])
def test_public_failures_record_real_error_and_settle_only_terminal_original_children(tmp_path, monkeypatch, failure):
    session, calls = route_session(tmp_path, monkeypatch, complete=failure == 'report_error')
    original_children(session)
    advance = session['submission'].advance
    def fail_advance(task_id):
        advance(task_id)
        if failure == 'worker_error':
            raise PermissionError('SYNTHETIC_WORKER_FAILED_AFTER_SETTLEMENT')
        return {'task_id': task_id, 'status': failure, 'dispatched_segments': 0}
    if failure == 'report_error':
        def fail_report(*args, **kwargs):
            raise ValueError('SYNTHETIC_REPORT_VALIDATION_FAILED')
        monkeypatch.setattr(runner, 'evaluate_business_reports', fail_report)
    else:
        session['submission'].advance = fail_advance
    record = runner.run_session(tmp_path / 'unused.json')
    assert record['outcome'] == 'FAILED' and record[runner.PROOF] == session['context'].proof()
    assert record['evidence']['error_type'] == ('PermissionError' if failure == 'worker_error' else 'ValueError')
    assert failure in record['evidence']['reason'] or record['evidence']['reason'].startswith('SYNTHETIC_')
    event_path = sorted((session['audit'] / 'events').glob('*.json'))[-1]
    error = runner.read(event_path, tmp_path)
    assert error['action'] == 'ERROR' and error['identity'] == record['evidence']['event_identity']
    assert {key: value for key, value in record['evidence'].items() if key != 'event_identity'} == error['payload']
    assert session['campaign'].settlements == [{'actual': {'candidate_attempts': 1, 'wall_seconds': 1},
        'outcome': 'FAILED', 'evidence_identity': record['identity']}]
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    assert runner.run_session(tmp_path / 'unused.json') == record
    assert len(calls) == 1 and len(session['campaign'].settlements) == 1
    assert before == {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}


@pytest.mark.parametrize('status,active', [('RESERVED', False), ('RUNNING', False), ('UNKNOWN', False), ('COMPLETED', True)])
def test_failure_waits_for_original_operations_without_redispatch_or_early_candidate_settlement(
        tmp_path, monkeypatch, status, active):
    session, calls = route_session(tmp_path, monkeypatch)
    original_children(session, base='RUNNING', stress='RESERVED')
    advance = session['submission'].advance
    def fail_advance(task_id):
        advance(task_id)
        original_children(session, base=status, stress='COMPLETED', active=active)
        raise ValueError('SYNTHETIC_ORIGINAL_OPERATION_NOT_FINAL')
    session['submission'].advance = fail_advance
    reconciled = []
    monkeypatch.setattr(runner, 'reconcile_submission',
                        lambda service, task_id: reconciled.append(task_id) or {'dispatched_segments': 0})
    for _ in range(2):
        result = runner.run_session(tmp_path / 'unused.json')
        assert result['status'] == 'WAITING_ORIGINAL_OPERATION_RECONCILIATION'
        assert result['evidence']['reason'] == 'SYNTHETIC_ORIGINAL_OPERATION_NOT_FINAL'
        assert not (session['audit'] / 'ATTEMPT_END.json').exists()
        assert session['campaign'].settlements == [] and len(calls) == 1
    errors = [runner.read(path, tmp_path) for path in (session['audit'] / 'events').glob('*.json')]
    assert sum(item['action'] == 'ERROR' for item in errors) == 1
    assert len(reconciled) == int(status == 'UNKNOWN' or active)
    original_children(session)
    record = runner.run_session(tmp_path / 'unused.json')
    assert record['outcome'] == 'FAILED' and len(calls) == 1
    assert len(session['campaign'].settlements) == 1


@pytest.mark.parametrize('outcome', ['COMPLETED', 'FAILED'])
def test_saved_attempt_recovers_settlement_crash_with_same_identity_and_no_dispatch(tmp_path, monkeypatch, outcome):
    session, calls = route_session(tmp_path, monkeypatch, complete=outcome == 'COMPLETED')
    original_children(session)
    monkeypatch.setattr(runner, 'evaluate_business_reports', lambda *args, **kwargs: {})
    if outcome == 'FAILED':
        advance = session['submission'].advance
        def fail_advance(task_id):
            advance(task_id)
            raise ValueError('SYNTHETIC_FAILURE_BEFORE_CANDIDATE_SETTLEMENT')
        session['submission'].advance = fail_advance
    settle = session['campaign'].settle_operation
    def crash_settlement(*args, **kwargs):
        raise RuntimeError('SYNTHETIC_CRASH_AFTER_ATTEMPT_END')
    session['campaign'].settle_operation = crash_settlement
    with pytest.raises(RuntimeError, match='SYNTHETIC_CRASH_AFTER_ATTEMPT_END'):
        runner.run_session(tmp_path / 'unused.json')
    path = session['audit'] / 'ATTEMPT_END.json'
    saved = runner.read(path, tmp_path)
    assert saved['outcome'] == outcome and session['campaign'].settlements == []
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    session['campaign'].settle_operation = settle
    monkeypatch.setattr(runner, 'reconcile_submission', lambda *args: pytest.fail('已有终态不得再次派发或核账'))
    assert runner.run_session(tmp_path / 'unused.json') == saved
    assert len(calls) == 1 and len(session['campaign'].settlements) == 1
    assert session['campaign'].settlements[0]['evidence_identity'] == saved['identity']
    assert before == {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}


@pytest.mark.parametrize('changed', ['error_event', 'canonical_file', 'attempt_outcome'])
def test_failed_terminal_requires_original_error_chain_files_and_settlement_identity(tmp_path, monkeypatch, changed):
    session, calls = route_session(tmp_path, monkeypatch)
    original_children(session)
    advance = session['submission'].advance
    def fail_advance(task_id):
        advance(task_id)
        raise ValueError('SYNTHETIC_TERMINAL_FAILURE')
    session['submission'].advance = fail_advance
    runner.run_session(tmp_path / 'unused.json')
    if changed == 'error_event':
        path = sorted((session['audit'] / 'events').glob('*.json'))[-1]
        value = runner.read(path, tmp_path)
        value['payload']['reason'] = 'SYNTHETIC_RETARGETED_ERROR'
        value['identity'] = stable_hash({key: item for key, item in value.items() if key != 'identity'})
        write(path, value)
    elif changed == 'canonical_file':
        write(session['context'].task_root / 'TASK.json', {'SYNTHETIC_CHANGED_TASK': True})
    else:
        path = session['audit'] / 'ATTEMPT_END.json'
        value = runner.read(path, tmp_path)
        value['outcome'] = 'COMPLETED'
        value['identity'] = stable_hash({key: item for key, item in value.items() if key != 'identity'})
        write(path, value)
    with pytest.raises(PermissionError, match='TERMINAL_'):
        runner.run_session(tmp_path / 'unused.json')
    assert len(calls) == 1 and len(session['campaign'].settlements) == 1


@pytest.mark.parametrize('steps', [0, -1, 101, True])
def test_invalid_step_bound_does_not_load_any_session(tmp_path, monkeypatch, steps):
    monkeypatch.setattr(runner, 'load_session', lambda path: pytest.fail('无效步数不能读取原件'))
    with pytest.raises(PermissionError, match='STEP_BOUND_INVALID'):
        runner.run_session(tmp_path / 'unused.json', max_steps=steps)


def pinned_session(tmp_path, monkeypatch, *, owner_alias=False):
    """替换原 ACL 调用和 Owner context；保留实际路径、55 pins 与原件哈希核验。"""
    from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
    from chanlun_trader.research_factory.strategy_submission_v1 import public_rule_factory
    workspace, owner = tmp_path / 'workspace', tmp_path / 'owner'
    logical_owner = tmp_path / 'msix-logical-owner' if owner_alias else owner
    if owner_alias:
        real_resolve = Path.resolve
        def resolve(path, *args, **kwargs):
            if path.is_relative_to(logical_owner):
                path = owner / path.relative_to(logical_owner)
            return real_resolve(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'resolve', resolve)
    original_runner = workspace / runner.ORIGINAL_RUNNER
    original_runner.parent.mkdir(parents=True)
    original_runner.write_text('# SYNTHETIC ORIGINAL RUNNER', encoding='utf-8')
    own = workspace / 'scripts' / Path(runner.__file__).name
    own.parent.mkdir(parents=True)
    own.write_text('# SYNTHETIC RESUME RUNNER', encoding='utf-8')
    monkeypatch.setattr(runner, '__file__', str(own))
    ref = lambda path: {'path': str(path), 'sha256': digest(path)}
    source = workspace / 'src' / 'wiring.py'
    source.parent.mkdir(parents=True)
    source.write_text('# OLD', encoding='utf-8')
    old_source = ref(source)
    archive = original_runner.parent / 'archive' / 'wiring.py'
    archive.parent.mkdir()
    archive.write_bytes(source.read_bytes())
    source.write_text('# NEW', encoding='utf-8')
    pins = [ref(original_runner), old_source]
    for index in range(53):
        path = original_runner.parent / ('PIN_' + str(index) + '.json')
        write(path, {'SYNTHETIC': index})
        pins.append(ref(path))
    candidate = 'SYNTHETIC'
    rule = capabilities()['examples']['multi_indicator_ranked']
    identity = public_rule_factory(rule, candidate).rule_identity
    config = {'schema_version': 'CODEX_SESSION_PUBLIC_RUNNER_V1', 'workspace_root': str(workspace),
        'campaign_id': 'SYNTHETIC_CAMPAIGN', 'batch_id': 'batch_002', 'candidate_id': candidate,
        'audit_root': str(original_runner.parent / 'audit'), 'pins': pins,
        'runner_sha256': digest(original_runner)}
    for key in ('deployment', 'request', 'proposal', 'context', 'rule'):
        path = original_runner.parent / (key + '.json')
        write(path, rule if key == 'rule' else {'SYNTHETIC': key})
        config[key] = ref(path)
    config_path = owner / 'campaign_setup' / 'BATCH_002_RUNNER_CONFIG.json'
    write(config_path, config)
    config_reference = {**ref(config_path), 'path': str(logical_owner / config_path.relative_to(owner))}
    audit = Path(config['audit_root'])
    bound = {'phase': 'EXPLORATION'}
    preview = {'request': bound, 'rule_identity': identity}
    preview['preview_identity'] = stable_hash(preview)
    task_id = stable_hash('SYNTHETIC_TASK')
    task_root = workspace / 'reports' / 'public' / task_id
    job_path = task_root / 'account' / 'JOB.json'
    plans = {candidate + '_' + cost: {'plan_id': stable_hash(cost)} for cost in ('BASE', 'STRESS')}
    write(job_path, {'root': str(job_path.parent), 'plans': plans,
                     'items': {name: {'factory_kwargs': {'payload': rule}} for name in plans}})
    task = {'task_id': task_id, 'job_path': str(job_path), 'job_sha256': digest(job_path),
            'preview_identity': preview['preview_identity'], 'plan_ids': {name: plan['plan_id'] for name, plan in plans.items()}}
    write(task_root / 'TASK.json', task)
    write(task_root / 'PREVIEW.json', preview)
    original_values = {'audit_start': ('START', {'config': config_reference,
        'runner_sha256': config['runner_sha256'], 'candidate_id': candidate, 'batch_id': 'batch_002', 'rule_identity': identity}),
        'preview': ('PREVIEW', preview), 'bound_request': ('BOUND_REQUEST', bound),
        'freeze_started': ('FREEZE_STARTED', {'task_id': task_id, 'preview_identity': preview['preview_identity']})}
    session_refs = {'config': config_reference, 'runner': ref(original_runner)}
    for key, (name, value) in original_values.items():
        path = audit / (name + '.json')
        write(path, value)
        session_refs[key] = ref(path)
    context = SimpleNamespace(job_path=job_path, task_root=task_root, validate=lambda **kwargs: None,
        mirrors_pending=lambda: False, time_extension=None, time_extension_installed=False,
        summary_value={'root': str(workspace), 'approval_store': str(owner), 'session_refs': session_refs,
            'runtime_sources': [ref(own)], 'source_replacements': [{'path': str(source),
                'old_sha256': old_source['sha256'], 'new_sha256': digest(source), 'archive_ref': ref(archive)}]})
    context.approved_source_replacements = lambda: deepcopy(context.summary_value['source_replacements'])
    context.approved_runtime_sources = lambda: deepcopy(context.summary_value['runtime_sources'])
    monkeypatch.setattr(runner.AccountVersionContinuationV1, 'from_job',
                        classmethod(lambda cls, job, **kwargs: context))
    acl = []
    def pinned(reference, *, parse=True):
        path = Path(reference['path'])
        if digest(path) != reference['sha256']:
            raise ValueError('SYNTHETIC_PIN_CHANGED')
        return runner.read(path, workspace) if parse else None
    def load_legacy(module):
        module.ROOT, module.OWNER_ROOT, module.FIELDS = workspace, logical_owner, set(config)
        module.verify_owner_boundary = lambda: acl.append('verified')
        module.pinned = pinned
        module.protected_campaign_contract = lambda *args: {'SYNTHETIC_CONTRACT': True}
    monkeypatch.setattr(runner.importlib.util, 'spec_from_file_location',
                        lambda *args: SimpleNamespace(loader=SimpleNamespace(exec_module=load_legacy),
                                                      name='synthetic_legacy', submodule_search_locations=None,
                                                      has_location=False, cached=None, origin=None))
    monkeypatch.setattr(runner.importlib.util, 'module_from_spec', lambda spec: SimpleNamespace())
    monkeypatch.setattr(runner, 'ResearchCampaignV1', lambda *args: SimpleNamespace(peek_status=lambda: {}))
    monkeypatch.setattr(runner, 'build_submission_service', lambda *args, **kwargs: SimpleNamespace(root=task_root.parent))
    monkeypatch.setattr(runner, 'verify_exploration_binding', lambda *args, **kwargs: None)
    return {'job': job_path, 'context': context, 'source': source, 'archive': archive, 'acl': acl,
            'config': config, 'config_path': config_path, 'workspace': workspace,
            'config_reference': config_reference, 'logical_owner': logical_owner, 'rule_identity': identity}


def test_resume_load_checks_acl_original_55_pins_and_exact_new_source_mapping(tmp_path, monkeypatch):
    case = pinned_session(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    session = runner.load_session(case['job'])
    assert session['context'] is case['context'] and case['acl'] == ['verified']
    assert before == {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}


def test_parent_first_mirror_crash_loads_via_reconcile_mode_and_never_reaches_dispatch(tmp_path, monkeypatch):
    case = pinned_session(tmp_path, monkeypatch)
    context = case['context']
    pending, checks, discoveries, reconciled = [True], [], [], []
    def validate(**kwargs):
        checks.append(deepcopy(kwargs))
        if pending[0] and not kwargs.get('for_reconcile'):
            raise PermissionError('ACCOUNT_VERSION_PARENT_CHARGE_CONFLICT')
        return context
    def discover(cls, job, **kwargs):
        discoveries.append(deepcopy(kwargs))
        return validate(**kwargs)
    def mirrors_pending():
        validate(for_reconcile=True)
        return pending[0]
    context.validate, context.mirrors_pending = validate, mirrors_pending
    context.proof = lambda: {'SYNTHETIC_PROTECTED_CONTINUATION': True}
    monkeypatch.setattr(runner.AccountVersionContinuationV1, 'from_job', classmethod(discover))
    campaign = ExistingCandidate(case['config'], case['rule_identity'])
    original_children({'campaign': campaign, 'config': case['config']}, base='RUNNING', stress='RESERVED')
    monkeypatch.setattr(runner, 'ResearchCampaignV1', lambda *args: campaign)
    submission = SimpleNamespace(root=context.task_root.parent,
        advance=lambda *args: pytest.fail('父先落盘核账入口不能派发、预览或冻结'))
    monkeypatch.setattr(runner, 'build_submission_service', lambda *args, **kwargs: submission)
    def reconcile(service, task_id):
        assert service is submission and task_id == context.task_root.name
        assert pending[0]
        reconciled.append(task_id)
        pending[0] = False
        return {'status': 'PUBLIC_RECONCILED', 'dispatched_segments': 0}
    monkeypatch.setattr(runner, 'reconcile_submission', reconcile)
    result = runner.run_session(case['job'])
    assert result['status'] == 'WAITING_RECONCILIATION_REVIEW_NO_REDISPATCH'
    assert discoveries == [{'for_reconcile': True}] and reconciled == [context.task_root.name]
    assert checks[-1] == {'for_dispatch': False} and not any(row.get('for_dispatch') for row in checks)
    audit = Path(case['config']['audit_root'])
    assert [runner.read(path, case['workspace'])['action'] for path in (audit / 'events').glob('*.json')] == ['RECONCILE_RETURNED']
    assert campaign.settlements == [] and not (audit / 'ATTEMPT_END.json').exists()


def test_reconcile_context_cannot_bypass_strict_validation_to_dispatch(tmp_path, monkeypatch):
    session, calls = route_session(tmp_path, monkeypatch)
    checks = []
    def validate(**kwargs):
        checks.append(kwargs)
        if not kwargs.get('for_reconcile'):
            raise PermissionError('SYNTHETIC_STRICT_OWNER_OR_PREFIX_CONFLICT')
    session['context'].validate = validate
    with pytest.raises(PermissionError, match='SYNTHETIC_STRICT_OWNER_OR_PREFIX_CONFLICT'):
        runner.run_session(tmp_path / 'unused.json')
    assert calls == [] and checks == [{'for_dispatch': False}]
    assert session['campaign'].settlements == []
    assert not (session['audit'] / 'ATTEMPT_END.json').exists()


def test_resume_load_preserves_original_msix_logical_reference_and_reads_canonical_owner(tmp_path, monkeypatch):
    case = pinned_session(tmp_path, monkeypatch, owner_alias=True)
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    session = runner.load_session(case['job'])
    assert case['logical_owner'] != Path(case['context'].summary_value['approval_store'])
    assert session['config_ref'] == case['config_reference']
    assert session['config_ref']['path'] != str(case['config_path'])
    assert case['acl'] == ['verified']
    assert before == {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}


@pytest.mark.parametrize('changed', ['current_source', 'old_archive', 'pin_count', 'own_runtime', 'missing_mapping'])
def test_resume_load_rejects_unapproved_current_old_or_missing_closure(tmp_path, monkeypatch, changed):
    case = pinned_session(tmp_path, monkeypatch)
    if changed == 'current_source':
        case['source'].write_text('# UNAPPROVED NEW', encoding='utf-8')
    elif changed == 'old_archive':
        case['archive'].write_text('# UNAPPROVED OLD', encoding='utf-8')
    elif changed == 'pin_count':
        case['config']['pins'].pop()
        write(case['config_path'], case['config'])
        case['context'].summary_value['session_refs']['config']['sha256'] = digest(case['config_path'])
    elif changed == 'missing_mapping':
        case['context'].summary_value['source_replacements'] = []
    else:
        case['context'].summary_value['runtime_sources'] = []
    with pytest.raises((ValueError, PermissionError)):
        runner.load_session(case['job'])


def test_resume_cli_help_bootstraps_source_imports_without_pythonpath(tmp_path):
    root = Path(__file__).resolve().parents[2]
    environment = {key: value for key, value in os.environ.items() if key not in ('PYTHONPATH', 'PYTHONHOME')}
    result = subprocess.run([sys.executable, str(root / 'scripts' / 'run_account_version_session_resume_v1.py'),
                             '--help'], cwd=tmp_path, env=environment, capture_output=True, text=True,
                            timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert '--job' in result.stdout and '--max-steps' in result.stdout
