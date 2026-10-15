"""人工成交及分红跨168日治理接续；临时部署不接触真实行情或原Owner。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from test_account_version_public_integration_v1 import (
    _charged_prefix, _install_synthetic_capabilities, _public_case, _read, _ref, _sha, _write,
)


def trading_fixture():
    """第168日除息、第171日支付；原持仓和应收必须跨安装边界保留。"""
    from universe_test_fixture_v1 import fixture
    window, bundle = fixture(days_count=564,
        prices=[12. + .02 * offset + .12 * math.sin(offset / 9.) for offset in range(504)])
    days = window['calendar']
    bundle['events'] = [{'event_id': 'SYNTHETIC_BOUNDARY_DIVIDEND_' + symbol,
        'symbol': symbol, 'event_type': 'CASH_DIVIDEND', 'record_date': days[226],
        'effective_date': days[227], 'payment_date': days[230], 'source': 'events',
        'source_published_at': str(days[220]), 'units': 'CNY_PER_SHARE',
        'terms': {'cash_per_share': .4, 'tax_rule': {'kind': 'DEFERRED_INDIVIDUAL_2015_101',
            'source': 'SYNTHETIC_TAX_FIXTURE'}}} for symbol in window['symbols']]
    bundle['source_hashes']['events'] = hashlib.sha256(
        json.dumps(bundle['events']).encode('utf-8')).hexdigest()
    changed = bundle['daily'].date.ge(days[227])
    bundle['daily'].loc[changed, ['open', 'high', 'low', 'close', 'prev_close']] -= .4
    return window, bundle


def trading_rule():
    from test_strategy_submission_v1 import node
    return {'buy': node('and', node('gt', node('field', 'close'), node('const', value=11)),
            node('gt', node('indicator', 'ma', output='ma', version='MA_ARITHMETIC_V1'), node('const', value=11))),
        'indicator_instances': [{'instance_id': 'ma', 'id': 'MA', 'version': 'MA_ARITHMETIC_V1',
            'params': {'window': 2}}, {'instance_id': 'vol', 'id': 'ROLLING_VOLATILITY',
            'version': 'ROLLING_VOLATILITY_V1', 'params': {'window': 2}}],
        'selection': {'score': node('indicator', 'vol', output='volatility',
            version='ROLLING_VOLATILITY_V1'), 'direction': 'ASCENDING', 'tie_breaker': 'SYMBOL_ASCENDING'},
        'max_hold_sessions': 200, 'exits': {'execution_mode': 'CLOSE_CONFIRM_NEXT_SESSION_OPEN',
            'stop_loss_pct': .08, 'take_profit_pct': None,
            'trailing_activate_pct': .05, 'trailing_pct': .04}}


_SYNTHETIC_LIFETIME = timedelta(hours=48)
_SYNTHETIC_TIMEOUT_SECONDS = int(_SYNTHETIC_LIFETIME.total_seconds())


def _full_public_case(root):
    return _public_case(root, fixture_factory=trading_fixture, rule_overrides=trading_rule(),
        authorization_lifetime=_SYNTHETIC_LIFETIME)


def _complete_public_account_and_reports(runner, service, job_path, task_id):
    # 原公共阻塞循环保留每段完整guard和收费，避免逐段重复进入提交服务。
    accounts = runner.execute_long_horizon_accounts(job_path, recover=True)
    assert accounts and 'status' not in accounts, accounts
    history = []
    for _ in range(120):
        outcome = service.advance(task_id)
        history.append(outcome['status'])
        assert outcome.get('dispatched_segments', 0) <= 1
        assert outcome['status'] not in ('FAILED', 'EVIDENCE_BLOCKED', 'UNKNOWN'), outcome
        if outcome['status'] == 'ACCOUNT_VERIFIED':
            return history
    raise AssertionError(history)


def test_full_synthetic_owner_lifetime_matches_transport_and_two_account_limits(tmp_path):
    started = datetime.now(timezone.utc)
    service, request, campaign, _, _, _ = _full_public_case(tmp_path)
    authority = service.authority_for_request(request)
    expires = datetime.fromisoformat(authority['expires_at'])
    base = campaign.peek_status()['base_authorization']
    assert (expires - started).total_seconds() >= _SYNTHETIC_TIMEOUT_SECONDS - 30
    assert base['expires_at'] == authority['expires_at']
    assert _SYNTHETIC_TIMEOUT_SECONDS > 2 * 72000


def test_full_synthetic_account_phase_uses_existing_public_blocking_entry_before_reports():
    from types import SimpleNamespace
    calls = []

    def accounts(path, *, recover=False, step=False):
        calls.append(('accounts', path, recover, step))
        return {'BASE': {}, 'STRESS': {}}

    def advance(task_id):
        assert calls == [('accounts', Path('JOB.json'), True, False)]
        return {'status': 'ACCOUNT_VERIFIED', 'dispatched_segments': 0}

    history = _complete_public_account_and_reports(SimpleNamespace(execute_long_horizon_accounts=accounts),
        SimpleNamespace(advance=advance), Path('JOB.json'), 'SYNTHETIC_TASK')
    assert history == ['ACCOUNT_VERIFIED']


def test_artificial_168th_close_contains_real_lots_orders_exit_state_and_dividend_receivable(tmp_path):
    from chanlun_trader.research_factory.research_rule_strategy_v4 import ResearchRuleStrategyV4
    from chanlun_trader.research_factory.universe_account_backend_v2 import UniverseAccountBackendV2, SegmentBoundary
    from chanlun_trader.research_factory.universe_account_inputs_v1 import universe_input_identity_v1
    from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile
    from universe_test_fixture_v1 import proposal
    window, bundle = trading_fixture()
    rule = proposal()
    rule.update(version='RESEARCH_RULE_STRATEGY_V4', **trading_rule())
    strategy = ResearchRuleStrategyV4(rule, strategy_id='SYNTHETIC_NATIVE_BOUNDARY')
    path = tmp_path / 'CHECKPOINT.json'
    backend = UniverseAccountBackendV2(window, execution_profile=execution_profile(SEGMENTED_PROFILE, 504),
        checkpoint_path=str(path), max_positions=2, max_symbol_exposure_bps=5000)
    input_identity = universe_input_identity_v1(bundle, window)
    with pytest.raises(SegmentBoundary, match='CLOSE_INTERRUPTED'):
        backend.run(strategy, bundle, bundle['events'],
            lambda: {'input_identity': input_identity},
            stop_after_date=window['calendar'][227])
    boundary = _read(path)
    state = boundary['ledger']['state']
    assert len(boundary['artifacts']['days']) == 168
    assert boundary['broker_fill_counter'] >= 2 and boundary['orders'] and boundary['rule_exits']
    assert any(lot['remaining_quantity'] > 0 and lot['cost'] > 0 for lot in state['lots'].values())
    assert sum(state['receivables'].values()) > 0
    assert state['entitlements'] and state['dividend_lots'] and not state['payments']


def test_public_artificial_fixture_registers_all_dividend_events_before_freeze(tmp_path):
    service, request, _, _, _, _ = _public_case(tmp_path,
        fixture_factory=trading_fixture, rule_overrides=trading_rule())
    preview = service.preview(request)
    assert preview['request']['execution_profile']['account_sessions'] == 504
    assert preview['coverage']['target_count'] == 3
    events = _read(tmp_path / 'synthetic_inputs/data/events.json')
    assert len(events) == 3 and all(row['event_type'] == 'CASH_DIVIDEND' for row in events)
    assert {row['symbol'] for row in events} == {'000001.SZ', '300001.SZ', '600000.SH'}
    scanned = service.scan(request, preview['preview_identity'])
    assert scanned['qualified_account_ready'] is True
    assert scanned['qualification_scope']['qualified_symbols'] == sorted(row['symbol'] for row in events)
    assert scanned['qualification_scope']['excluded'] == []


def _prefix_worker(job_path, name, number, stop_day):
    """测试producer仅用原backend的stop_after_date；prepare/guard/撮合/状态codec均原实现。"""
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    from chanlun_trader.research_factory.universe_account_backend_v2 import SegmentBoundary
    from scripts import run_strategy_account_v1 as runner
    started = time.monotonic()
    handshake = worker_resource_handshake()
    job = runner.read_json(job_path)
    context = runner.validate_sources(job)
    gov = runner.service(job)
    dispatch = gov.segment_status(name)['pending']
    assert handshake['execution'] == {'purpose': name, 'segment_number': number,
        'dispatch_id': dispatch['dispatch_id']}
    context.validate_dispatch(name, number)
    item, plan = context.active_item(name), context.active_plan(name)
    strategy = runner.resolve(item['factory'])(**item.get('factory_kwargs', {}))
    backend = runner.backend_for(strategy, item['backend_options'])
    assert runner.prepare(strategy, backend, item) == plan
    root = Path(job['root'])
    prefix = runner.segment_prefix(name, number)
    runner.save(root / (prefix + '_INPUT_ACCESS.json'), {'reader_pid': os.getpid(),
        'reader_parent_pid': os.getppid(), 'launcher_pid': handshake['launcher_pid'],
        'resource_platform': os.name, 'windows_job_verified': handshake.get('windows_job_verified', False),
        'input_identity': job['input_identity'], 'loader': item['loader'], 'loader_kwargs': item['loader_kwargs'],
        'purpose': name, 'segment_number': number, 'dispatch_id': dispatch['dispatch_id'],
        'account_version_continuation': context.proof(name),
        'synthetic_prefix_producer': 'NATIVE_BACKEND_STOP_AFTER_ACCOUNT_DAY_168'})
    data = runner.resolve(item['loader'])(**item['loader_kwargs'])
    assert data['input_identity'] == job['input_identity']
    backend.segment_seconds = handshake['wall_seconds'] - 60 - (time.monotonic() - started)
    assert backend.segment_seconds > 0
    native_run = backend.run

    def stop_at_boundary(strategy, bundle, actions, guard):
        return native_run(strategy, bundle, actions, guard, stop_after_date=stop_day)

    backend.run = stop_at_boundary

    def active():
        return context.project_receipt({**gov.active_execution(name),
            'execution_pause_requested': runner.control_state(job)['paused']}, name)

    try:
        runner.run(strategy, backend, frame=data['frame'], actions=data['actions'],
            input_identity=data['input_identity'], active_check=active, runtime=item)
    except SegmentBoundary as error:
        checkpoint = Path(item['backend_options']['checkpoint_path'])
        value = _read(checkpoint)
        runner.save(root / (prefix + '_STATUS.json'), {'state': 'CONTINUE',
            'dispatch_id': dispatch['dispatch_id'], 'phase': 'ACCOUNT', 'reason': str(error),
            'last_day': value['last_day'], 'state_identity': value['state_identity'],
            'checkpoint_sha256': _sha(checkpoint), 'account_version_continuation': context.proof(name)})
        return 75
    raise AssertionError('168日producer不可完成或覆盖完整504日结果')


def _charge_trade_prefix(job_path, name, stop_day):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    from chanlun_trader.research_factory.universe_execution_profile_v1 import worker_wall_seconds
    from scripts import run_strategy_account_v1 as runner
    job = _read(job_path)
    gov = runner.service(job)
    context = runner.validate_sources(job)
    context.validate(for_dispatch=True, name=name)
    dispatch = gov.start_segment(name)
    context.record_dispatch(name, dispatch)
    number = dispatch['segment_number']
    prefix = runner.segment_prefix(name, number)
    started = time.monotonic()
    resource = run_bounded_worker([sys.executable, str(Path(__file__).resolve()), '--prefix',
        str(job_path), name, str(number), str(stop_day)], root=runner.REPO, memory_mib=2048,
        wall_seconds=worker_wall_seconds(dispatch['upper_bound_seconds']), environment=dict(os.environ),
        execution={'purpose': name, 'segment_number': number, 'dispatch_id': dispatch['dispatch_id']},
        measure_peak_memory=True,
        on_started=lambda pid: runner.save(job_path.parent / (prefix + '_WORKER.json'), {'pid': pid,
            'purpose': name, 'segment_number': number, 'dispatch_id': dispatch['dispatch_id'],
            'account_version_continuation': context.proof(name)}))
    elapsed = time.monotonic() - started
    resource = {key: value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value
        for key, value in resource.items()}
    resource.update(elapsed_wall_seconds=elapsed, segment_number=number, dispatch_id=dispatch['dispatch_id'],
        synthetic_prefix_producer='NATIVE_BACKEND_STOP_AFTER_ACCOUNT_DAY_168')
    resource_path = job_path.parent / (prefix + '_RESOURCE.json')
    runner.save(resource_path, resource)
    assert resource['returncode'] == 75 and not resource['timed_out'], resource
    assert elapsed <= dispatch['upper_bound_seconds']
    gov.end_segment(name, number, seconds=elapsed, evidence_identity=_sha(resource_path), outcome='CONTINUE')


def _install_predecessor(root, source, original_bytes, service, request, campaign, approvals, owner):
    from chanlun_trader.research_factory.account_version_capability_bridge_v1 import (
        AccountVersionCapabilityBridgeV1, REQUIRED_RUNTIME_PATHS,
    )
    from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
    preview = service.preview(request)
    task_id = service.freeze(request, preview['preview_identity'])['task_id']
    for _ in range(8):
        if service.advance(task_id)['status'] == 'APPROVED':
            break
    else:
        raise AssertionError('合成原任务未批准')
    task = service._task(task_id)
    job_path = Path(task['job_path'])
    job = _read(job_path)
    name = next(name for name in job['plans'] if name.endswith('_BASE'))
    service.pause(task_id)
    _charged_prefix(job_path, name, paused=True)
    base = campaign.peek_status()['base_authorization']
    archive = root / 'reports' / 'synthetic_quota_source_archive' / 'business_predecessor.py'
    archive.parent.mkdir(parents=True)
    archive.write_bytes(source.read_bytes())
    source.write_bytes(original_bytes)
    replacements = [{'path': str(source), 'old_sha256': _sha(archive), 'new_sha256': _sha(source),
        'archive_ref': _ref(archive)}]
    bridge_summary = AccountVersionCapabilityBridgeV1.summary(root, base,
        source_replacements=replacements,
        runtime_source_refs=[_ref(root / path) for path in sorted(REQUIRED_RUNTIME_PATHS)])
    approved = approvals.approve(bridge_summary, approver='SYNTHETIC_QUOTA_OWNER', capability=owner)
    bridge_ref = AccountVersionCapabilityBridgeV1.install(root, base, bridge_summary, approval_ref=approved)
    proposal, ci = archive.with_name('PREDECESSOR_PROPOSAL.json'), archive.with_name('PREDECESSOR_CI.json')
    _write(proposal, {'synthetic_fixture': True, 'purpose': 'ORIGINAL_ZERO_DAY_VERSION_CONTINUATION'})
    _write(ci, {'status': 'PASSED', 'head': 'a' * 40, 'runtime_sources': bridge_summary['runtime_sources'],
        'source_replacements': bridge_summary['source_replacements'], 'synthetic_fixture': True})
    summary = AccountVersionContinuationV1.summary(root, authorization_id=campaign.authorization_id,
        task_ref=_ref(job_path.parent.parent / 'TASK.json'), capability_bridge_ref=bridge_ref,
        proposal_ref=_ref(proposal), ci_ref=_ref(ci))
    approved = approvals.approve(summary, approver='SYNTHETIC_QUOTA_OWNER', capability=owner)
    predecessor = AccountVersionContinuationV1.freeze(root, summary, approval_ref=approved)
    return task, job_path, name, predecessor


def _reference_worker(job_path, name):
    """独立人工连续对照只用原生引擎及独立核账，不持研究Owner能力。"""
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    from scripts import run_strategy_account_v1 as runner
    handshake = worker_resource_handshake()
    assert handshake['memory_mib'] == 2048
    job = _read(job_path)
    item = deepcopy(job['items'][name])
    folder = Path(job['root']).parents[3] / 'synthetic_quota_continuous_reference' / name
    folder.mkdir(parents=True)
    item['backend_options']['checkpoint_path'] = str(folder / 'CHECKPOINT.json')
    strategy = runner.resolve(item['factory'])(**item.get('factory_kwargs', {}))
    backend = runner.backend_for(strategy, item['backend_options'])
    data = runner.resolve(item['loader'])(**item['loader_kwargs'])
    result = backend.run(strategy, data['frame'], data['actions'],
        lambda: {'input_identity': data['input_identity']})
    _write(folder / 'RESULT.json', result)


def _compare_native_reference(root, job_path, name, result):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    from chanlun_trader.research_factory.universe_execution_artifacts_v1 import ArtifactSequence
    folder = job_path.parent.parents[3] / 'synthetic_quota_continuous_reference' / name
    resource = run_bounded_worker([sys.executable, str(Path(__file__).resolve()), '--reference',
        str(job_path), name], root=root, memory_mib=2048, wall_seconds=900,
        on_started=lambda pid: _write(folder.with_name(name + '_WORKER.json'), {'pid': pid,
            'started_at': datetime.now(timezone.utc).isoformat()}),
        environment=dict(os.environ), measure_peak_memory=True,
        execution={'purpose': 'SYNTHETIC_CONTINUOUS_REFERENCE_' + name, 'segment_number': 1,
            'dispatch_id': 'c' * 64})
    resource = {key: value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value
        for key, value in resource.items()}
    _write(folder / 'RESOURCE.json', resource)
    assert resource['returncode'] == 0 and not resource['timed_out'], resource
    reference = _read(folder / 'RESULT.json')
    assert reference['reconciliation']['passed']
    assert reference['fills'] == result['fills']
    assert reference['metrics'] == result['metrics']
    assert reference['final_account_checkpoint'] == result['final_account_checkpoint']
    assert list(ArtifactSequence(reference['artifacts'], 'account')) == list(ArtifactSequence(result['artifacts'], 'account'))
    return _ref(folder / 'RESULT.json')


def test_native_reference_records_bounded_worker_launch(tmp_path, monkeypatch):
    """原生对照也必须满足资源包装器的启动回调契约。"""
    from inspect import signature
    from chanlun_trader import synthetic_batch_resources as resources

    worker_signature = signature(resources.run_bounded_worker)
    job_path = tmp_path / 'reports/public/task/account/JOB.json'

    class Started(Exception):
        pass

    def start_only(*args, **kwargs):
        worker_signature.bind(*args, **kwargs)
        assert kwargs['memory_mib'] == 2048 and kwargs['wall_seconds'] == 900
        kwargs['on_started'](12345)
        assert not (tmp_path / 'synthetic_quota_continuous_reference/FULL_FIXTURE_BASE').exists()
        raise Started

    monkeypatch.setattr(resources, 'run_bounded_worker', start_only)
    with pytest.raises(Started):
        _compare_native_reference(tmp_path, job_path, 'FULL_FIXTURE_BASE', {})
    worker = _read(tmp_path / 'synthetic_quota_continuous_reference/FULL_FIXTURE_BASE_WORKER.json')
    assert worker['pid'] == 12345


def _finish_synthetic_session(root, job_path, context, task, public, campaign, restored, config):
    """只替换本机原会话装配producer；实际公共run_session写FEEDBACK并结清原候选。"""
    from scripts import run_account_version_session_resume_v1 as session_runner
    audit = root / 'reports' / 'synthetic_original_session'
    _write(audit / 'START.json', {'producer': 'DECLARED_SYNTHETIC_SESSION_ROUTING_ADAPTER'})
    session = {'context': context, 'task': task, 'config': config, 'audit': audit, 'workspace': root,
        'submission': restored(), 'campaign': campaign, 'contract': campaign.peek_status()[
            'base_authorization']['scope_policy']['summary']['contract'], 'bound_request': {},
        'rule_identity': public['rule_identity'], 'config_ref': _ref(audit / 'START.json'),
        'runner_sha256': _sha(root / 'scripts/run_account_version_session_resume_v1.py')}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(session_runner, 'load_session', lambda path: session)
        completed = session_runner.run_session(job_path, max_steps=1)
        assert completed['outcome'] == 'COMPLETED'
        assert session_runner.run_session(job_path, max_steps=1) == completed
    feedback = _read(audit / 'FEEDBACK.json')
    assert feedback['account_version_continuation'] == context.proof()
    assert feedback['public_result_sha256'] == _sha(context.task_root / 'PUBLIC_RESULT.json')
    assert feedback['task_sha256'] == _sha(context.task_root / 'TASK.json')
    assert set(feedback['report_refs']) == {'BASE', 'STRESS'}
    return _ref(audit / 'FEEDBACK.json')


def _scenario(root):
    from chanlun_trader.research_factory.account_purpose_time_extension_v1 import (
        AccountPurposeTimeExtensionV1, GOVERNANCE_PATHS, source_inventory,
    )
    from chanlun_trader.research_factory.account_version_capability_bridge_v1 import BUSINESS_PATH
    from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
    from chanlun_trader.research_factory.business_validation_protocol_v1 import BusinessValidationProtocolV1
    from scripts import run_strategy_account_v1 as runner
    # 仅人工前驱说明注释造成旧治理SHA差异；新/旧均执行本head的相同经济实现。
    governance_bytes = {root / path: (root / path).read_bytes() for path in GOVERNANCE_PATHS
        if (root / path).is_file()}
    for path, raw in governance_bytes.items():
        path.write_bytes(raw + b'\n# Synthetic governance predecessor source identity.\n')
    source = root / BUSINESS_PATH
    original_bytes = source.read_bytes()
    source.write_bytes(original_bytes + b'\n# Synthetic original input predecessor identity.\n')
    service, request, campaign, approvals, owner, restored = _full_public_case(root)
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.strategy_submission_v1 import public_rule_factory
    session_config = {'candidate_id': request['strategy_id'], 'campaign_id': campaign.authorization_id,
        'batch_id': 'SYNTHETIC_SECOND_BATCH', 'proposal': {'sha256': 'd' * 64}, 'context': {'sha256': 'e' * 64}}
    candidate_operation = request['strategy_id'] + '_CANDIDATE'
    campaign.reserve_operation(operation_id=candidate_operation, batch_id=session_config['batch_id'],
        stage='EXPLORATION', kind='CANDIDATE', subject_identity=stable_hash({
            'rule_identity': public_rule_factory(request['rule'], request['strategy_id']).rule_identity,
            'proposal_sha256': session_config['proposal']['sha256'],
            'context_sha256': session_config['context']['sha256']}),
        upper_bounds={'candidate_attempts': 1, 'wall_seconds': 1})
    campaign.start_operation(candidate_operation)
    task, job_path, base_name, predecessor_ref = _install_predecessor(root, source, original_bytes,
        service, request, campaign, approvals, owner)
    job = _read(job_path)
    gov = runner.service(job)
    runner.control_job(job_path, 'RESUME')
    stop_day = job['plans'][base_name]['backend']['window']['calendar'][60 + 167]
    checkpoint = Path(job['items'][base_name]['backend_options']['checkpoint_path'])
    for _ in range(40):
        if checkpoint.exists() and _read(checkpoint)['last_day'] == stop_day:
            break
        _charge_trade_prefix(job_path, base_name, stop_day)
    else:
        raise AssertionError('合成前缀未到168日')
    runner.control_job(job_path, 'PAUSE')
    boundary = _read(checkpoint)
    assert len(boundary['artifacts']['days']) == 168
    assert boundary['broker_fill_counter'] >= 2 and boundary['orders'] and boundary['rule_exits']
    before_view = campaign.peek_status()
    assert before_view['operations'][gov.active()['source']['operation_ids'][base_name]]['status'] == 'RUNNING'
    state_before = gov.segment_status(base_name)
    assert state_before['charged_seconds'] > 0 and state_before['pending'] is None
    immutable_prefix = {path: path.read_bytes() for path in [job_path,
        job_path.parent / 'CONFIRMATION.json', job_path.parent / (base_name + '_START.json'),
        Path(predecessor_ref['path']), *job_path.parent.glob('*_SEGMENT_*_DISPATCH.json'),
        *job_path.parent.glob('*_SEGMENT_*_CHARGE.json'),
        *Path(boundary['artifacts']['root']).glob('*.json.gz')]}
    checkpoint_bytes = checkpoint.read_bytes()
    inventory = source_inventory(root, authorization_id=campaign.authorization_id,
        task_ref=_ref(job_path.parent.parent / 'TASK.json'), predecessor_manifest_ref=predecessor_ref)
    replacements = []
    archive_root = root / 'reports' / 'synthetic_quota_source_archive'
    for path, raw in governance_bytes.items():
        if str(path) in inventory['source_hashes']:
            archive = archive_root / (_sha(path) + '_' + path.name)
            archive.write_bytes(path.read_bytes())
            replacements.append({'path': str(path), 'old_sha256': _sha(archive),
                'new_sha256': hashlib.sha256(raw).hexdigest(), 'archive_ref': _ref(archive)})
        path.write_bytes(raw)
    replacements.sort(key=lambda row: row['path'])
    runtime = sorted([_ref(path) for path in governance_bytes], key=lambda row: row['path'])
    economic = inventory['economic_sources']
    proposal, ci = archive_root / 'QUOTA_PROPOSAL.json', archive_root / 'QUOTA_CI.json'
    _write(proposal, {'synthetic_fixture': True, 'cumulative_seconds_per_account': 72000,
        'aggregate_reserved_delta': 86400, 'economic_state_conversion': False})
    _write(ci, {'status': 'PASSED', 'head': 'b' * 40, 'runtime_sources': runtime,
        'source_replacements': replacements, 'economic_sources': economic, 'synthetic_fixture': True})
    summary = AccountPurposeTimeExtensionV1.summary(root, authorization_id=campaign.authorization_id,
        task_ref=_ref(job_path.parent.parent / 'TASK.json'), predecessor_manifest_ref=predecessor_ref,
        source_replacements=replacements, runtime_source_refs=runtime,
        economic_source_refs=economic, proposal_ref=_ref(proposal), ci_ref=_ref(ci))
    approved = approvals.approve(summary, approver='SYNTHETIC_QUOTA_OWNER', capability=owner)
    manifest_ref = AccountPurposeTimeExtensionV1.freeze(root, summary, approval_ref=approved)
    assert checkpoint.read_bytes() == checkpoint_bytes
    assert all(path.read_bytes() == raw for path, raw in immutable_prefix.items())
    campaign.install_account_purpose_time_extension(manifest_ref)
    gov.reconcile_segment_mirrors(base_name)
    context = AccountVersionContinuationV1.from_job(job)
    context.validate()
    after_install = campaign.peek_status()
    assert after_install['used'] == before_view['used']
    assert after_install['reserved']['wall_seconds'] == before_view['reserved']['wall_seconds'] + 86400
    assert after_install['remaining']['wall_seconds'] == before_view['remaining']['wall_seconds'] - 86400
    assert set(after_install['operations']) == set(before_view['operations'])
    assert gov.segment_status(base_name)['charged_seconds'] == state_before['charged_seconds']
    assert context.active_plan(base_name)['backend'] == inventory['predecessor_summary']['active_plans'][base_name]['backend']
    assert checkpoint.read_bytes() == checkpoint_bytes
    runner.control_job(job_path, 'RESUME')
    service = restored()
    history = _complete_public_account_and_reports(runner, service, job_path, task['task_id'])
    public = _read(job_path.parent.parent / 'PUBLIC_RESULT.json')
    assert public['account_version_continuation'] == context.proof()
    feedback = BusinessValidationProtocolV1.verify_report_refs(task, public, minimum_sessions=504)
    assert set(feedback) == {'BASE', 'STRESS'}
    final = _read(checkpoint)
    assert final['execution_identity'] == boundary['execution_identity']
    assert final['artifacts']['days'][:168] == boundary['artifacts']['days']
    assert final['artifacts']['days'][168]['date'] == job['plans'][base_name]['backend']['window']['calendar'][228]
    assert all(path.read_bytes() == raw for path, raw in immutable_prefix.items())
    reference_refs = {}
    for name in job['plans']:
        result = _read(job_path.parent / (name + '_RESULT.json'))
        assert len(result['artifacts']['days']) == 504 and result['reconciliation']['passed']
        assert result['metrics']['trade_count'] > 0
        assert result['final_account_checkpoint']['economic']['action_audit']
        assert result['account_version_continuation'] == context.proof(name)
        assert feedback['STRESS' if name.endswith('STRESS') else 'BASE']['account_version_continuation'] == context.proof(name)
        reference_refs[name] = _compare_native_reference(root, job_path, name, result)
    canonical_feedback_ref = _finish_synthetic_session(root, job_path, context, task, public,
        campaign, restored, session_config)
    _write(root / 'SYNTHETIC_TIME_EXTENSION_INTEGRATION_RESULT.json', {'status': 'PASSED',
        'account_days_per_cost': 504, 'boundary_days': 168, 'first_successor_day_index': 169,
        'original_prefix_preserved': True, 'economic_execution_identity_preserved': True,
        'old_charged_seconds': state_before['charged_seconds'], 'new_reserved_delta': 86400,
        'history': history, 'feedback_costs': sorted(feedback), 'proof': context.proof(),
        'canonical_synthetic_feedback_ref': canonical_feedback_ref, 'native_continuous_reference_refs': reference_refs,
        'synthetic_source_identity_producer': 'COMMENTS_ONLY_BOTH_GOVERNANCE_VERSIONS_EXECUTE_THIS_HEAD',
        'synthetic_session_adapter': 'ONLY_LOAD_SESSION_ASSEMBLY_REPLACED_PUBLIC_ROUTING_AND_FEEDBACK_ORIGINAL',
        'real_market_or_performance_acceptance': False})


@pytest.mark.skipif(os.environ.get('RUN_LONG_ACCOUNT_PURPOSE_TIME_EXTENSION_INTEGRATION') != '1',
    reason='完整人工双成本504日持仓/分红接续，需要显式启用')
def test_full_504_day_time_extension_keeps_168_day_economic_state_and_public_proofs(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    root = tmp_path / 'q'
    for folder in ('src', 'scripts'):
        shutil.copytree(repo / folder, root / folder, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    _install_synthetic_capabilities(root)
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONPATH': os.pathsep.join((str(root / 'src'), str(root), str(Path(__file__).parent))),
        **{key: '1' for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    argv = [sys.executable, str(Path(__file__).resolve()), '--scenario', str(root)]
    _write(root / 'SCENARIO_LAUNCH.json', {'argv': argv, 'root': str(root),
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'source_sha256': {str(path.relative_to(root)): _sha(path)
            for folder in ('src', 'scripts') for path in sorted((root / folder).rglob('*.py'))},
        'test_source_sha256': _sha(Path(__file__).resolve()),
        'timeout_seconds': _SYNTHETIC_TIMEOUT_SECONDS, 'real_market_or_performance_acceptance': False})
    try:
        completed = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=_SYNTHETIC_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        for label, output in (('STDOUT', error.stdout), ('STDERR', error.stderr)):
            if isinstance(output, bytes):
                output = output.decode('utf-8', errors='replace')
            (root / ('SCENARIO_' + label + '.log')).write_text(output or '', encoding='utf-8')
        _write(root / 'SCENARIO_TRANSPORT_RESULT.json', {'status': 'TIMED_OUT',
            'timeout_seconds': _SYNTHETIC_TIMEOUT_SECONDS, 'returncode': None})
        raise
    (root / 'SCENARIO_STDOUT.log').write_text(completed.stdout, encoding='utf-8')
    (root / 'SCENARIO_STDERR.log').write_text(completed.stderr, encoding='utf-8')
    _write(root / 'SCENARIO_TRANSPORT_RESULT.json', {'status': 'EXITED',
        'returncode': completed.returncode})
    assert completed.returncode == 0, completed.stdout + '\n' + completed.stderr
    result = _read(root / 'SYNTHETIC_TIME_EXTENSION_INTEGRATION_RESULT.json')
    assert result['status'] == 'PASSED' and result['account_days_per_cost'] == 504


if __name__ == '__main__':
    if sys.argv[1] == '--prefix':
        raise SystemExit(_prefix_worker(Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]), int(sys.argv[5])))
    if sys.argv[1] == '--reference':
        _reference_worker(Path(sys.argv[2]), sys.argv[3])
        raise SystemExit(0)
    assert sys.argv[1] == '--scenario'
    _scenario(Path(sys.argv[2]))
