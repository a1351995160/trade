"""人工277日真实经济状态/50段声明费用之后，一次生产恢复的完整公共链。

旧段费用为明确人工治理producer，绝不冒充真实worker实测。277日状态由原有受限
native backend producer生成；新段及VERIFY/REPORT/FEEDBACK、双原生对照走真实公共实现。
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from failed_account_startup_charge_fixture_v1 import assert_public_startup_cutoff_charge

from test_account_version_public_integration_v1 import (
    _install_synthetic_capabilities, _read, _ref, _sha, _write,
)
from test_account_purpose_time_extension_integration_v1 import (
    _full_public_case, _install_predecessor, _charge_trade_prefix, _compare_native_reference,
    _complete_public_account_and_reports, _finish_synthetic_session,
)


TIMEOUT_SECONDS = 172800
CANONICAL = 42339.896402100036
OLD34 = 28741.393096800028
PRE50 = 41449.676419200034


def phase(root, action, **payload):
    value = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'phase': action,
             'fixture_only': True, **payload}
    with (root / 'RECOVERY_SCENARIO_PHASES.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=True) + '\n')
    _write(root / 'RECOVERY_SCENARIO_PHASE.json', value)


def artificial_old_fee_segment(job_path, name, *, seconds, outcome='CONTINUE'):
    """只生成声明的人工旧治理计量；不替代新恢复worker的实际metering。"""
    from scripts import run_strategy_account_v1 as runner
    job = _read(job_path); gov = runner.service(job)
    context = runner.validate_sources(job)
    context.validate(for_dispatch=True, name=name)
    dispatch = gov.start_segment(name)
    context.record_dispatch(name, dispatch)
    prefix = runner.segment_prefix(name, dispatch['segment_number'])
    resource = {'fixture_only': 'ARTIFICIAL_OLD_FAILURE_HISTORY_NOT_REAL_WORKER_MEASUREMENT',
        'dispatch_id': dispatch['dispatch_id'], 'segment_number': dispatch['segment_number'],
        'elapsed_wall_seconds': seconds, 'returncode': 1 if outcome == 'FAILED' else 75,
        'timed_out': outcome == 'FAILED', 'peak_memory_mib': 1800.,
        'performance_claimed': False}
    if dispatch.get('time_extension_proof') is not None:
        resource['time_extension_proof'] = dispatch['time_extension_proof']
    version = context.segment_proof(name, dispatch['segment_number'])
    artificial_pid = 100000 + dispatch['segment_number']
    resource['account_version_continuation'] = version
    resource['resource_platform'] = 'posix'
    common = {'fixture_only': 'ARTIFICIAL_OLD_HISTORY_NOT_REAL_WORKER_OBSERVATION',
        'not_real_worker_measurement': True, 'dispatch_id': dispatch['dispatch_id'],
        'segment_number': dispatch['segment_number'], 'purpose': name,
        'account_version_continuation': version}
    if dispatch.get('time_extension_proof') is not None:
        common['time_extension_proof'] = dispatch['time_extension_proof']
    runner.save(job_path.parent / (prefix + '_WORKER.json'), {**common, 'pid': artificial_pid})
    item = job['items'][name]
    runner.save(job_path.parent / (prefix + '_INPUT_ACCESS.json'), {**common, 'reader_pid': artificial_pid,
        'input_identity': job['input_identity'], 'loader': item['loader'], 'loader_kwargs': item['loader_kwargs']})
    path = job_path.parent / (prefix + '_RESOURCE.json')
    runner.save(path, resource)
    gov.end_segment(name, dispatch['segment_number'], seconds=seconds,
        evidence_identity=_sha(path), outcome=outcome)


def artificial_fill_fee_prefix(job_path, name, *, last_segment, target):
    from scripts import run_strategy_account_v1 as runner
    gov = runner.service(_read(job_path))
    while True:
        state = gov.segment_status(name)
        remaining = last_segment - len(state['segments'])
        if remaining == 0:
            assert state['charged_seconds'] == target
            return
        assert remaining > 0
        seconds = ((target - state['charged_seconds']) if remaining == 1
                   else (target - state['charged_seconds']) / remaining)
        assert 0 < seconds <= 900, (state['charged_seconds'], remaining, target)
        artificial_old_fee_segment(job_path, name, seconds=seconds)


def original_session(root, context, task, campaign, restored, config, rule_identity):
    audit = root / 'reports' / 'synthetic_original_session'
    _write(audit / 'START.json', {'producer': 'DECLARED_SYNTHETIC_SESSION_ROUTING_ADAPTER'})
    return {'context': context, 'task': task, 'config': config, 'audit': audit, 'workspace': root,
        'submission': restored(), 'campaign': campaign,
        'contract': campaign.peek_status()['base_authorization']['scope_policy']['summary']['contract'],
        'bound_request': {}, 'rule_identity': rule_identity, 'config_ref': _ref(audit / 'START.json'),
        'runner_sha256': _sha(root / 'scripts/run_account_version_session_resume_v1.py')}


def scenario(root):
    from chanlun_trader.research_factory.account_purpose_time_extension_v1 import (
        AccountPurposeTimeExtensionV1, GOVERNANCE_PATHS as EXTENSION_PATHS, source_inventory,
    )
    from chanlun_trader.research_factory.failed_account_recovery_v1 import (
        FailedAccountRecoveryV1, GOVERNANCE_PATHS as RECOVERY_PATHS, source_inventory as recovery_inventory,
    )
    from chanlun_trader.research_factory.account_version_capability_bridge_v1 import BUSINESS_PATH
    from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
    from chanlun_trader.research_factory.business_validation_protocol_v1 import BusinessValidationProtocolV1
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.strategy_submission_v1 import public_rule_factory
    from scripts import run_strategy_account_v1 as runner
    from scripts import run_account_version_session_resume_v1 as session_runner

    phase(root, 'DECLARED_ORIGINAL_PREDECESSOR')
    canonical = {root / p: (root / p).read_bytes() for p in EXTENSION_PATHS if (root / p).is_file()}
    for path, raw in canonical.items():
        path.write_bytes(raw + b'\n# Synthetic original governance identity before extension.\n')
    worker_path = root / 'scripts/run_strategy_account_v1.py'
    business = root / BUSINESS_PATH; business_raw = business.read_bytes()
    business.write_bytes(business_raw + b'\n# Synthetic original input predecessor identity.\n')
    service, request, campaign, approvals, owner, restored = _full_public_case(root)
    config = {'candidate_id': request['strategy_id'], 'campaign_id': campaign.authorization_id,
        'batch_id': 'SYNTHETIC_SECOND_BATCH', 'proposal': {'sha256': 'd' * 64}, 'context': {'sha256': 'e' * 64}}
    candidate = request['strategy_id'] + '_CANDIDATE'
    campaign.reserve_operation(operation_id=candidate, batch_id=config['batch_id'], stage='EXPLORATION',
        kind='CANDIDATE', subject_identity=stable_hash({
            'rule_identity': public_rule_factory(request['rule'], request['strategy_id']).rule_identity,
            'proposal_sha256': config['proposal']['sha256'], 'context_sha256': config['context']['sha256']}),
        upper_bounds={'candidate_attempts': 1, 'wall_seconds': 1})
    campaign.start_operation(candidate)
    task, job_path, base_name, version_ref = _install_predecessor(root, business, business_raw,
        service, request, campaign, approvals, owner)
    job = _read(job_path); gov = runner.service(job)
    runner.control_job(job_path, 'RESUME')
    checkpoint = Path(job['items'][base_name]['backend_options']['checkpoint_path'])
    stop_day = job['plans'][base_name]['backend']['window']['calendar'][60 + 276]
    phase(root, 'NATIVE_BOUNDED_277_DAY_ECONOMIC_PREFIX')
    for _ in range(32):
        if checkpoint.exists() and _read(checkpoint).get('last_day') == stop_day:
            break
        _charge_trade_prefix(job_path, base_name, stop_day)
    else:
        raise AssertionError('native prefix did not reach 277 within declared old segment boundary')
    boundary = _read(checkpoint)
    assert len(boundary['artifacts']['days']) == 277
    assert boundary['broker_fill_counter'] > 0 and boundary['orders'] and boundary['rule_exits']
    artificial_fill_fee_prefix(job_path, base_name, last_segment=34, target=OLD34)
    runner.control_job(job_path, 'PAUSE')

    inventory = source_inventory(root, authorization_id=campaign.authorization_id,
        task_ref=_ref(job_path.parent.parent / 'TASK.json'), predecessor_manifest_ref=version_ref)
    archive_root = root / 'reports/synthetic_failed_recovery_sources'; archive_root.mkdir(parents=True)
    replacements = []
    for path, raw in canonical.items():
        current = raw + b'\n# Synthetic governance immediately before failed recovery.\n' if path == worker_path else raw
        if str(path) in inventory['source_hashes']:
            archive = archive_root / (_sha(path) + '_' + path.name); archive.write_bytes(path.read_bytes())
            replacements.append({'path': str(path), 'old_sha256': _sha(archive),
                'new_sha256': hashlib.sha256(current).hexdigest(), 'archive_ref': _ref(archive)})
        path.write_bytes(current)
    replacements.sort(key=lambda row: row['path'])
    runtime = sorted([_ref(p) for p in canonical], key=lambda row: row['path'])
    proposal = archive_root / 'TIME_EXTENSION_PROPOSAL.json'; ci = archive_root / 'TIME_EXTENSION_CI.json'
    _write(proposal, {'fixture_only': True, 'same_original_account_limit': 72000})
    _write(ci, {'status': 'PASSED', 'head': 'b' * 40, 'runtime_sources': runtime,
        'source_replacements': replacements, 'economic_sources': inventory['economic_sources'],
        'fixture_only': 'ARTIFICIAL_OWNER_BINDING_NOT_REAL_CI'})
    summary = AccountPurposeTimeExtensionV1.summary(root, authorization_id=campaign.authorization_id,
        task_ref=_ref(job_path.parent.parent / 'TASK.json'), predecessor_manifest_ref=version_ref,
        source_replacements=replacements, runtime_source_refs=runtime,
        economic_source_refs=inventory['economic_sources'], proposal_ref=_ref(proposal), ci_ref=_ref(ci))
    approval = approvals.approve(summary, approver='SYNTHETIC_OWNER', capability=owner)
    extension_ref = AccountPurposeTimeExtensionV1.freeze(root, summary, approval_ref=approval)
    campaign.install_account_purpose_time_extension(extension_ref)
    gov.reconcile_segment_mirrors(base_name)
    runner.control_job(job_path, 'RESUME')
    artificial_fill_fee_prefix(job_path, base_name, last_segment=49, target=PRE50)
    artificial_old_fee_segment(job_path, base_name, seconds=CANONICAL - PRE50, outcome='FAILED')
    gov.settle(base_name, completed=False, seconds=CANONICAL, result_hash=None,
        error='ARTIFICIAL_ORIGINAL_WORKER_TIMEOUT_FIXED_GOVERNANCE_HISTORY')
    assert gov.segment_status(base_name)['charged_seconds'] == CANONICAL
    original = {p: p.read_bytes() for p in job_path.parent.rglob('*') if p.is_file() and not p.name.endswith('.lock')}
    checkpoint_raw = checkpoint.read_bytes()
    before = campaign.peek_status(); operations = before['operations']; mapping = gov.active()['source']['operation_ids']
    assert operations[mapping[base_name]]['status'] == 'FAILED'
    assert operations[mapping[base_name]]['actual']['wall_seconds'] == 42340
    old_context = AccountVersionContinuationV1.from_job(job)
    session = original_session(root, old_context, task, campaign, restored, config,
        public_rule_factory(request['rule'], request['strategy_id']).rule_identity)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(session_runner, 'load_session', lambda path: session)
        failed = session_runner.run_session(job_path, max_steps=1)
    assert failed['status'] == 'WAITING_ORIGINAL_OPERATION_RECONCILIATION'
    error_refs = {p: p.read_bytes() for p in (session['audit'] / 'events').glob('*.json')}
    phase(root, 'ORIGINAL_FAILED50_PRESERVED', original_days=277, original_canonical=CANONICAL)

    old_inventory = recovery_inventory(root, authorization_id=campaign.authorization_id,
        task_ref=_ref(job_path.parent.parent / 'TASK.json'), predecessor_manifest_ref=extension_ref)
    old_worker = archive_root / 'WORKER_BEFORE_RECOVERY.py'; old_worker.write_bytes(worker_path.read_bytes())
    worker_path.write_bytes(canonical[worker_path])
    replacements = [{'path': str(worker_path), 'old_sha256': _sha(old_worker), 'new_sha256': _sha(worker_path),
        'archive_ref': _ref(old_worker)}]
    runtime = sorted([_ref(root / p) for p in RECOVERY_PATHS if (root / p).is_file()], key=lambda row: row['path'])
    proposal = archive_root / 'RECOVERY_PROPOSAL.json'; ci = archive_root / 'RECOVERY_CI.json'
    _write(proposal, {'fixture_only': True, 'one_failed_account_recovery': True,
        'preserve_original_failed_and_277_days': True, 'restored_integer_reservation': 29660})
    _write(ci, {'status': 'PASSED', 'head': 'c' * 40, 'runtime_sources': runtime,
        'source_replacements': replacements, 'economic_sources': old_inventory['economic_sources'],
        'fixture_only': 'ARTIFICIAL_OWNER_BINDING_NOT_REAL_CI'})
    summary = FailedAccountRecoveryV1.summary(root, authorization_id=campaign.authorization_id,
        task_ref=_ref(job_path.parent.parent / 'TASK.json'), predecessor_manifest_ref=extension_ref,
        source_replacements=replacements, runtime_source_refs=runtime,
        economic_source_refs=old_inventory['economic_sources'], proposal_ref=_ref(proposal), ci_ref=_ref(ci))
    approval = approvals.approve(summary, approver='SYNTHETIC_OWNER', capability=owner)
    manifest = FailedAccountRecoveryV1.freeze(root, summary, approval_ref=approval)
    assert checkpoint.read_bytes() == checkpoint_raw
    assert all(p.read_bytes() == raw for p, raw in original.items())
    campaign.install_failed_account_recovery(manifest)
    gov.reconcile_segment_mirrors(base_name)
    context = AccountVersionContinuationV1.from_job(job); context.validate()
    after = campaign.peek_status()
    assert after['used'] == before['used']
    assert after['reserved']['wall_seconds'] == before['reserved']['wall_seconds'] + 29660
    assert after['operations'][mapping[base_name]]['status'] == 'FAILED'
    assert after['operations'][mapping[base_name]]['actual'] == operations[mapping[base_name]]['actual']
    assert set(after['operations']) == set(operations)
    assert context.recovery.original_failed_account == base_name
    phase(root, 'RECOVERY_INSTALLED_STRICT_ZERO_DISPATCH')
    public_service = restored()
    phase(root, 'ACTUAL_PUBLIC_STARTUP_CUTOFF_AND_CHARGE')
    startup_charge = assert_public_startup_cutoff_charge(runner, public_service, job_path, task['task_id'])
    phase(root, 'PUBLIC_RECOVERY_ACCOUNT_AND_REPORTS_RUNNING')
    history = _complete_public_account_and_reports(runner, public_service, job_path, task['task_id'])
    public = _read(context.task_root / 'PUBLIC_RESULT.json')
    feedback = BusinessValidationProtocolV1.verify_report_refs(task, public, minimum_sessions=504)
    assert set(feedback) == {'BASE', 'STRESS'}
    final = _read(checkpoint)
    assert final['execution_identity'] == boundary['execution_identity']
    assert final['artifacts']['days'][:277] == boundary['artifacts']['days']
    assert final['artifacts']['days'][277]['date'] == job['plans'][base_name]['backend']['window']['calendar'][337]
    economic_prefix = runner.segment_prefix(base_name, 52)
    assert _read(job_path.parent / (economic_prefix + '_STARTUP.json'))['validation_complete'] is True
    assert (job_path.parent / (economic_prefix + '_INPUT_ACCESS.json')).is_file()
    # 原checkpoint本身允许追加；所有旧日、失败、费用、START与旧sidecar保持。
    assert all(p.read_bytes() == raw for p, raw in original.items() if p != checkpoint)
    assert all(p.read_bytes() == raw for p, raw in error_refs.items())
    assert _read(job_path.parent / (base_name + '_SETTLEMENT.json'))['completed'] is False
    assert _read(job_path.parent / (base_name + '_RECOVERY_SETTLEMENT.json'))['completed'] is True
    references = {}
    for name in job['plans']:
        result = _read(job_path.parent / (name + '_RESULT.json'))
        assert len(result['artifacts']['days']) == 504 and result['reconciliation']['passed']
        assert result['metrics']['trade_count'] > 0
        assert result['account_version_continuation'] == context.proof(name)
        phase(root, 'BOUNDED_NATIVE_REFERENCE', purpose=name)
        references[name] = _compare_native_reference(root, job_path, name, result)
    canonical_feedback = _finish_synthetic_session(root, job_path, context, task, public, campaign, restored, config)
    assert all(p.read_bytes() == raw for p, raw in error_refs.items())
    value = {'status': 'PASSED', 'account_days_per_cost': 504, 'preserved_original_days': 277,
        'first_new_day_ordinal': 278, 'first_new_segment': 51, 'first_economic_segment': 52,
        'startup_charge_evidence_ref': startup_charge['startup_charge_evidence_ref'],
        'old_failed_settlement_preserved': True,
        'old_canonical_seconds': CANONICAL, 'restored_integer_reservation': 29660,
        'original_state_and_error_prefix_preserved': True, 'proof': context.proof(), 'history': history,
        'native_continuous_reference_refs': references, 'canonical_synthetic_feedback_ref': canonical_feedback,
        'old_fee_producer': 'ARTIFICIAL_FIXED_50_SEGMENT_GOVERNANCE_HISTORY_NOT_REAL_MEASUREMENT',
        'prefix_producer': 'ORIGINAL_BOUNDED_NATIVE_BACKEND_TO_DAY277_WITH_REAL_ORDERS_AND_STATE_CODEC',
        'source_predecessor_producer': 'COMMENTS_ONLY_SAME_HEAD_ECONOMIC_IMPLEMENTATION',
        'session_adapter': 'LOAD_SESSION_ASSEMBLY_ONLY_PUBLIC_ROUTING_AND_FEEDBACK_ORIGINAL',
        'real_owner_55pins_acl_certification': False, 'real_market_or_performance_acceptance': False}
    _write(root / 'SYNTHETIC_FAILED_ACCOUNT_RECOVERY_INTEGRATION_RESULT.json', value)
    phase(root, 'PASSED_FULL_RECOVERY_SYNTHETIC')


@pytest.mark.skipif(os.environ.get('RUN_LONG_FAILED_ACCOUNT_RECOVERY_INTEGRATION') != '1',
    reason='完整人工双504/277日原状态/50段费用/双原生对照须显式启用')
def test_full_504_day_failed_account_recovery_keeps_277_days_fees_and_public_feedback(tmp_path):
    repo = Path(__file__).resolve().parents[2]; root = (tmp_path / 'r').resolve()
    for folder in ('src', 'scripts'):
        shutil.copytree(repo / folder, root / folder, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    _install_synthetic_capabilities(root)
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONPATH': os.pathsep.join((str(root / 'src'), str(root), str(Path(__file__).parent))),
        **{key: '1' for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    argv = [sys.executable, str(Path(__file__).resolve()), '--scenario', str(root)]
    _write(root / 'SCENARIO_LAUNCH.json', {'argv': argv, 'root': str(root),
        'test_helper_source_sha256': _sha(Path(__file__).parent / 'failed_account_startup_charge_fixture_v1.py'),
        'source_sha256': {str(p.relative_to(root)): _sha(p) for folder in ('src', 'scripts')
            for p in sorted((root / folder).rglob('*.py'))}, 'test_source_sha256': _sha(Path(__file__).resolve()),
        'timeout_seconds': TIMEOUT_SECONDS, 'real_market_or_performance_acceptance': False})
    try:
        completed = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        for name, content in [('STDOUT', error.stdout), ('STDERR', error.stderr)]:
            if isinstance(content, bytes):
                content = content.decode('utf-8', errors='replace')
            (root / ('SCENARIO_' + name + '.log')).write_text(content or '', encoding='utf-8')
        _write(root / 'SCENARIO_TRANSPORT_RESULT.json', {'status': 'TIMED_OUT', 'returncode': None})
        raise
    (root / 'SCENARIO_STDOUT.log').write_text(completed.stdout, encoding='utf-8')
    (root / 'SCENARIO_STDERR.log').write_text(completed.stderr, encoding='utf-8')
    _write(root / 'SCENARIO_TRANSPORT_RESULT.json', {'status': 'EXITED', 'returncode': completed.returncode})
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert _read(root / 'SYNTHETIC_FAILED_ACCOUNT_RECOVERY_INTEGRATION_RESULT.json')['status'] == 'PASSED'


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--scenario':
        scenario(Path(sys.argv[2]).resolve(strict=True))
    else:
        raise SystemExit('expected --scenario <isolated-root>')
