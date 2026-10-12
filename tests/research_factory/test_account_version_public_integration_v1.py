"""纯合成部署的版本接续全链；旧初始化前缀标明 producer，后缀使用公共真实 worker。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import textwrap

import pytest


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _ref(path):
    return {'path': str(path), 'sha256': _sha(path)}


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf-8')


def _install_synthetic_capabilities(root):
    """只供临时部署：保留完整能力元信息，每次仍重读全部源码SHA。"""
    path = root / 'src' / 'sitecustomize.py'
    path.write_text(textwrap.dedent('''\
        # 纯合成验收提供者；不缓存 Owner、guard、预算或验证结论。
        from copy import deepcopy
        import hashlib
        from pathlib import Path
        import sys
        from chanlun_trader.research_factory import research_capabilities_v1 as catalog
        from chanlun_trader.research_factory.common import stable_hash

        native_capabilities = catalog.capabilities
        captured = native_capabilities()
        source_root = Path(catalog.__file__).parent

        def synthetic_capabilities(*, data_catalog=None):
            snapshot = deepcopy(captured)
            for group in (snapshot, snapshot['full_universe'], snapshot['long_horizon']):
                group['source_hashes'] = {name: hashlib.sha256(source_root.joinpath(name).read_bytes()).hexdigest()
                    for name in group['source_hashes']}
            snapshot['implementation_sha256'] = snapshot['source_hashes']['research_capabilities_v1.py']
            snapshot['data'] = deepcopy(data_catalog) if data_catalog is not None else {'status': 'UNKNOWN'}
            snapshot.pop('fingerprint')
            snapshot['fingerprint'] = stable_hash(snapshot)
            return snapshot

        for name, module in tuple(sys.modules.items()):
            if name.startswith('chanlun_trader.research_factory') and getattr(module, 'capabilities', None) is native_capabilities:
                module.capabilities = synthetic_capabilities
        catalog.capabilities = synthetic_capabilities
        ''').lstrip(), encoding='utf-8')
    declaration = root / 'reports' / 'synthetic_capability_producer' / 'PRODUCER.json'
    _write(declaration, {'schema_version': 'SYNTHETIC_FULL_CAPABILITIES_PROVIDER_V1',
        'producer_ref': _ref(path), 'frozen_before_owner_approval': True,
        'metadata': 'FULL_NATIVE_SNAPSHOT', 'source_hashes': 'ALL_DYNAMIC_CURRENT_SOURCE_BYTES',
        'owner_guard_and_public_workers': 'ORIGINAL_IMPLEMENTATIONS',
        'real_market_or_900_second_performance_acceptance': False})
    return declaration


def _public_case(root):
    """冻结前指定504日；沿用原合成提供器及规则，不接触真实账户或行情。"""
    from chanlun_trader.research_factory.campaign_scope_v1 import (
        CampaignScopeV1, OwnerApprovalStoreV1, bind_campaign_scope, scope_summary,
    )
    from chanlun_trader.research_factory.continuous_research_contract_v1 import continuous_research_contract
    from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
    from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
    from chanlun_trader.research_factory.run_budget import AutonomousRunBudgetV2
    from chanlun_trader.research_factory.strategy_submission_v1 import StrategySubmissionV1, public_rule_factory
    from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile
    import test_universe_submission_v1 as fixtures
    from test_continuous_research_contract_v1 import build, scope
    from test_long_horizon_public_submission_v1 import long_public_case
    from test_strategy_submission_v1 import connected, node
    from universe_test_fixture_v1 import fixture

    # 只替换合成数据生成参数，产品profile和最终门槛均保持504日。
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(fixtures, 'fixture', lambda: fixture(days_count=564))
        legacy, request, authority, _ = long_public_case(root / 'synthetic_inputs')
    authority['expires_at'] = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
    request['execution_profile'] = execution_profile(SEGMENTED_PROFILE, 504)
    authority['execution_profiles'] = [execution_profile(SEGMENTED_PROFILE, 504, purpose)
        for purpose in ('RESEARCH_ACCOUNT', 'RESEARCH_PREPARATION', 'RESEARCH_VERIFICATION', 'RESEARCH_REPORT')]
    authority['account_authorization']['execution_profile'] = deepcopy(request['execution_profile'])
    request['rule'].update(version='RESEARCH_RULE_STRATEGY_V4',
        hypothesis='三板块合成趋势及波动评分', change_reason='验证一次初始化版本接续',
        indicator_instances=[{'instance_id': 'ma', 'id': 'MA', 'version': 'MA_ARITHMETIC_V1',
                              'params': {'window': 2}},
                             {'instance_id': 'vol', 'id': 'ROLLING_VOLATILITY',
                              'version': 'ROLLING_VOLATILITY_V1', 'params': {'window': 2}}],
        buy=node('gt', node('field', 'close'),
                 node('indicator', 'ma', output='ma', version='MA_ARITHMETIC_V1')),
        selection={'score': node('indicator', 'vol', output='volatility', version='ROLLING_VOLATILITY_V1'),
                   'direction': 'ASCENDING', 'tie_breaker': 'SYMBOL_ASCENDING'})
    snapshot = capabilities()
    frozen = scope(snapshot, confirmation=False)
    dataset = legacy.provider.catalog()['datasets'][0]
    frozen.update(universe_id=request['universe_id'], universe_hash=dataset['universe_identity'],
        max_positions=request['max_positions'], execution_profiles=deepcopy(authority['execution_profiles']),
        indicator_roles={'MA': ['BUY', 'SELL'], 'ROLLING_VOLATILITY': ['SCORE']})
    frozen['data_routes']['EXPLORATION'].update(dataset_id='sample', dataset_hash=dataset['metadata_hash'],
        start='2020-01-01', end='2025-07-31')
    original_contract = build(snapshot, frozen_scope=frozen)
    contract = continuous_research_contract(objective_id=original_contract['objective_id'], scope=frozen,
        exploration_policy={**original_contract['exploration_policy'], 'minimum_account_sessions': 504},
        final_criteria=original_contract['final_criteria'], criteria_source=original_contract['criteria_source'],
        capabilities_snapshot=snapshot)
    units = dict.fromkeys(AutonomousRunBudgetV2.resource_names, 100000)
    units.update(account_jobs=8, candidate_attempts=4, wall_seconds=1000000)
    stages = {phase: {name: amount // 2 for name, amount in units.items()}
        for phase in ('EXPLORATION', 'CONFIRMATION')}
    authorization = {'authorization_id': 'SYNTHETIC_ACCOUNT_VERSION', 'objective_id': contract['objective_id'],
        'resource_limits': units, 'stages': list(stages),
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(),
        'max_batches': 4, 'max_total_predictive_trials': 20, 'max_trials_per_batch': 10,
        'max_hypotheses_per_batch': 2, 'max_candidates_per_batch': 2,
        'execution_profiles': contract['scope']['execution_profiles']}
    owner = object()
    approvals = OwnerApprovalStoreV1(root.parent / 'protected_synthetic_owner', owner_capability=owner)
    summary = scope_summary(root, authorization, contract, stages)
    approved = approvals.approve(summary, approver='SYNTHETIC_FIXTURE_OWNER', capability=owner)
    authorization = bind_campaign_scope(root, authorization, contract=contract, stage_limits=stages,
        approvals=approvals, approval_ref=approved)
    campaign = ResearchCampaignV1.create(root, authorization)
    authority['objective_id'] = contract['objective_id']

    def restored():
        current_campaign = ResearchCampaignV1(root, authorization['authorization_id'])
        return StrategySubmissionV1(legacy.provider, lambda ref: deepcopy(authority), root / 'reports' / 'public',
            connected, continuous_scope=CampaignScopeV1(current_campaign))

    service = restored()
    request = service.bind_research_request(request, phase='EXPLORATION',
        candidate_identity=public_rule_factory(request['rule'], request['strategy_id']).rule_identity,
        batch_id='SYNTHETIC_SECOND_BATCH')
    return service, request, campaign, approvals, owner, restored


def _initialize_only(job_path, name, number):
    """合成旧前缀producer：受限进程真实读合成原件、提交特征，刻意不执行账户日。"""
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    from chanlun_trader.research_factory.universe_account_inputs_v1 import _prepare_owned_universe_account_inputs_v1
    from chanlun_trader.research_factory.universe_signal_scan_v2 import UniverseSignalScanV2
    from scripts import run_strategy_account_v1 as runner
    handshake = worker_resource_handshake()
    job = runner.read_json(job_path)
    runner.validate_sources(job)
    gov = runner.service(job)
    dispatch = gov.segment_status(name)['pending']
    assert handshake['execution'] == {'purpose': name, 'segment_number': number,
        'dispatch_id': dispatch['dispatch_id']}
    gov.active_execution(name)
    item = job['items'][name]
    strategy = runner.resolve(item['factory'])(**item.get('factory_kwargs', {}))
    backend = runner.backend_for(strategy, item['backend_options'])
    assert runner.prepare(strategy, backend, item) == job['plans'][name]
    root = Path(job['root'])
    prefix = runner.segment_prefix(name, number)
    runner.save(root / (prefix + '_INPUT_ACCESS.json'), {'reader_pid': os.getpid(),
        'reader_parent_pid': os.getppid(), 'launcher_pid': handshake['launcher_pid'],
        'resource_platform': os.name, 'windows_job_verified': handshake.get('windows_job_verified', False),
        'input_identity': job['input_identity'], 'loader': item['loader'], 'loader_kwargs': item['loader_kwargs'],
        'purpose': name, 'segment_number': number, 'dispatch_id': dispatch['dispatch_id'],
        'synthetic_prefix_producer': 'INITIALIZATION_ONLY_NO_ACCOUNT_DAY'})
    data = runner.resolve(item['loader'])(**item['loader_kwargs'])
    inputs = _prepare_owned_universe_account_inputs_v1(data['frame'], backend.window,
        required_fields=strategy.requirements.fields, warmup_bars=strategy.requirements.warmup_sessions)
    scanner = UniverseSignalScanV2(strategy, inputs, root=root / (strategy.strategy_id + '_FEATURES'))
    scanner.values._mmap.close()
    runner.save(root / (prefix + '_STATUS.json'), {'state': 'CONTINUE', 'dispatch_id': dispatch['dispatch_id'],
        'phase': 'FEATURES', 'last_day': None, 'synthetic_prefix_producer': 'INITIALIZATION_ONLY_NO_ACCOUNT_DAY'})
    return 75


def _charged_prefix(path, name, *, paused):
    from chanlun_trader.synthetic_batch_resources import run_bounded_worker
    from scripts import run_strategy_account_v1 as runner
    job = runner.read_json(path)
    gov = runner.service(job)
    if not (path.parent / (name + '_START.json')).exists():
        gov.start(name)
    dispatch = gov.start_segment(name)
    number = dispatch['segment_number']
    prefix = runner.segment_prefix(name, number)
    started = time.monotonic()
    result = run_bounded_worker([sys.executable, str(Path(__file__).resolve()), '--initialize', str(path), name,
        str(number)], root=runner.REPO, memory_mib=2048, wall_seconds=120,
        environment=dict(os.environ), execution={'purpose': name, 'segment_number': number,
            'dispatch_id': dispatch['dispatch_id']}, measure_peak_memory=True,
        on_started=lambda pid: runner.save(path.parent / (prefix + '_WORKER.json'), {'pid': pid,
            'purpose': name, 'segment_number': number, 'dispatch_id': dispatch['dispatch_id']}))
    elapsed = time.monotonic() - started
    result = {key: value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value
        for key, value in result.items()}
    result.update(elapsed_wall_seconds=elapsed, segment_number=number, dispatch_id=dispatch['dispatch_id'],
        synthetic_prefix_producer='INITIALIZATION_ONLY_NO_ACCOUNT_DAY')
    resource_path = path.parent / (prefix + '_RESOURCE.json')
    runner.save(resource_path, result)
    assert result['returncode'] == 75 and not result['timed_out'], result
    gov.end_segment(name, number, seconds=elapsed, evidence_identity=runner.sha(resource_path),
        outcome='PAUSED' if paused else 'CONTINUE')


def _scenario(root):
    from chanlun_trader.research_factory.account_version_capability_bridge_v1 import (
        AccountVersionCapabilityBridgeV1, BUSINESS_PATH, REQUIRED_RUNTIME_PATHS,
    )
    from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
    from chanlun_trader.research_factory.business_validation_protocol_v1 import BusinessValidationProtocolV1
    from test_continuous_submission_v1 import budget_usage
    from scripts import run_strategy_account_v1 as runner
    import sitecustomize as producer
    from chanlun_trader.research_factory.research_capabilities_v1 import capabilities

    declaration = root / 'reports' / 'synthetic_capability_producer' / 'PRODUCER.json'
    assert _read(declaration)['producer_ref'] == _ref(root / 'src' / 'sitecustomize.py')
    assert capabilities() == producer.native_capabilities()

    source = root / BUSINESS_PATH
    current_bytes = source.read_bytes()
    # 合成旧版本仅增加说明注释：不同源码身份，交易和策略语义完全一致。
    source.write_bytes(current_bytes + b'\n# Synthetic predecessor for version-continuation integration.\n')
    assert capabilities() == producer.native_capabilities()
    service, request, campaign, approvals, owner, restored = _public_case(root)
    preview = service.preview(request)
    task_id = service.freeze(request, preview['preview_identity'])['task_id']
    history = []
    for _ in range(8):
        outcome = service.advance(task_id)
        history.append(outcome['status'])
        if outcome['status'] == 'APPROVED':
            break
    else:
        raise AssertionError(history)
    task = service._task(task_id)
    path = Path(task['job_path'])
    job = _read(path)
    assert job['resources']['account_sessions'] == 504 and job['resources']['total_seconds'] == 28800
    assert job['resource_job_total_seconds'] == 57600
    name = next(iter(job['plans']))
    _charged_prefix(path, name, paused=False)
    service.pause(task_id)
    _charged_prefix(path, name, paused=True)
    assert service.advance(task_id)['status'] == 'ACCOUNTS_PAUSED'
    gov = runner.service(job)
    before_state = gov.segment_status(name)
    assert before_state['charged_seconds'] > 0 and len(before_state['segments']) == 2
    assert not list(path.parent.glob('*_CHECKPOINT.json'))
    features = {item: item.read_bytes() for item in (path.parent / (name + '_FEATURES')).glob('*.json')}
    assert len([item for item in features if item.name.endswith(('.SZ.json', '.SH.json'))]) == 3
    before = budget_usage(campaign)
    base = campaign.peek_status()['base_authorization']
    originals = {item: item.read_bytes() for item in (path, path.parent / 'CONFIRMATION.json',
        path.parent / (name + '_START.json'), campaign.authorization_path)}
    originals.update({item: item.read_bytes() for item in path.parent.glob(name + '_SEGMENT_*_CHARGE.json')})
    archive = root / 'reports' / 'synthetic_source_archive' / source.name
    archive.parent.mkdir(parents=True)
    archive.write_bytes(source.read_bytes())
    source.write_bytes(current_bytes)
    assert capabilities() == producer.native_capabilities()
    replacements = [{'path': str(source), 'old_sha256': _sha(archive), 'new_sha256': _sha(source),
        'archive_ref': _ref(archive)}]
    runtime_refs = [_ref(root / relative) for relative in sorted(REQUIRED_RUNTIME_PATHS)]
    summary = AccountVersionCapabilityBridgeV1.summary(root, base,
        source_replacements=replacements, runtime_source_refs=runtime_refs)
    approved = approvals.approve(summary, approver='SYNTHETIC_FIXTURE_OWNER', capability=owner)
    bridge_ref = AccountVersionCapabilityBridgeV1.install(root, base, summary, approval_ref=approved)
    proposal = root / 'reports' / 'synthetic_source_archive' / 'PROPOSAL.json'
    ci = proposal.with_name('CI.json')
    _write(proposal, {'purpose': 'SYNTHETIC_ACCOUNT_VERSION_INTEGRATION', 'real_acceptance': False})
    _write(ci, {'status': 'PASSED', 'head': 'a' * 40, 'runtime_sources': summary['runtime_sources'],
        'source_replacements': summary['source_replacements'], 'synthetic_fixture': True})
    continuation_summary = AccountVersionContinuationV1.summary(root, authorization_id=campaign.authorization_id,
        task_ref=_ref(path.parent.parent / 'TASK.json'), capability_bridge_ref=bridge_ref,
        proposal_ref=_ref(proposal), ci_ref=_ref(ci), session_refs={'synthetic_capability_producer': _ref(declaration)})
    approved = approvals.approve(continuation_summary, approver='SYNTHETIC_FIXTURE_OWNER', capability=owner)
    manifest_ref = AccountVersionContinuationV1.freeze(root, continuation_summary, approval_ref=approved)
    assert AccountVersionContinuationV1.freeze(root, continuation_summary, approval_ref=approved) == manifest_ref
    assert budget_usage(campaign) == before
    assert all(item.read_bytes() == raw for item, raw in originals.items())
    context = AccountVersionContinuationV1.from_job(job)
    proof = context.proof()
    assert len(continuation_summary['accounts'][name]['features']['receipts']) == 3
    assert all(context.active_plan(member)['plan_id'] != plan['plan_id'] for member, plan in job['plans'].items())
    runner.control_job(path, 'RESUME')
    service = restored()
    for _ in range(20):
        outcome = service.advance(task_id)
        history.append(outcome['status'])
        assert outcome['dispatched_segments'] <= 1, outcome
        assert outcome['status'] != 'EVIDENCE_BLOCKED', outcome
        if outcome['status'] == 'ACCOUNT_VERIFIED':
            break
    else:
        raise AssertionError(history)
    assert outcome['account_version_continuation'] == outcome['verification']['account_version_continuation'] == proof
    public = _read(path.parent.parent / 'PUBLIC_RESULT.json')
    assert public['account_version_continuation'] == proof
    feedback = BusinessValidationProtocolV1.verify_report_refs(task, outcome, minimum_sessions=504)
    assert set(feedback) == {'BASE', 'STRESS'}
    for member, reference in outcome['reports'].items():
        member_proof = context.proof(member)
        assert _read(path.parent / (member + '_RESULT.json'))['account_version_continuation'] == member_proof
        assert _read(reference['final_report'])['account_version_continuation'] == member_proof
        assert feedback['STRESS' if member.endswith('STRESS') else 'BASE']['account_version_continuation'] == member_proof
        assert feedback['STRESS' if member.endswith('STRESS') else 'BASE']['sessions'] == 504
        assert _read(path.parent / (member + '_RESOURCE.json'))['segment_count'] >= (3 if member == name else 1)
    after_state = gov.segment_status(name)
    assert after_state['charged_seconds'] > before_state['charged_seconds']
    assert after_state['segments'][:2] == before_state['segments']
    assert after_state['segments'][2]['dispatch']['segment_number'] == 3
    assert all(item.read_bytes() == raw for item, raw in originals.items())
    assert all(item.read_bytes() == raw for item, raw in features.items())
    usage = campaign.peek_status()
    assert usage['base_authorization'] == base and usage['used']['account_jobs'] == 2
    assert usage['used']['data_experiments'] == 1 and len(usage['operations']) == 5
    assert set(usage['operations']) == set(before['operations']) | {
        key for key, value in usage['operations'].items() if value['kind'] == 'VERIFY'}
    assert all(value['status'] == 'COMPLETED' for value in usage['operations'].values())
    settled = budget_usage(campaign)
    assert restored().advance(task_id) == public
    assert budget_usage(campaign) == settled
    _write(root / 'SYNTHETIC_INTEGRATION_RESULT.json', {'status': 'PASSED', 'history': history,
        'account_sessions': 504, 'charged_prefix_segments': 2, 'original_start_preserved': True,
        'account_jobs': usage['used']['account_jobs'], 'preparation_jobs': usage['used']['data_experiments'],
        'version_proof': proof, 'feedback_costs': sorted(feedback), 'real_acceptance': False})


def _check_synthetic_capabilities(root):
    import sitecustomize as producer
    from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
    from chanlun_trader.research_factory.account_version_capability_bridge_v1 import BUSINESS_PATH
    source = root / BUSINESS_PATH
    original = source.read_bytes()
    try:
        initial = capabilities()
        assert initial == producer.native_capabilities()
        source.write_bytes(original + b'\n# Synthetic source identity change.\n')
        changed = capabilities()
        assert changed == producer.native_capabilities()
        assert changed['fingerprint'] != initial['fingerprint']
        data = {'datasets': [{'dataset_id': 'synthetic_only'}]}
        assert capabilities(data_catalog=data) == producer.native_capabilities(data_catalog=data)
    finally:
        source.write_bytes(original)
    assert capabilities() == initial


def test_synthetic_capability_provider_preserves_complete_snapshot_and_dynamic_sources(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    root = tmp_path / 'synthetic_capability_provider'
    for folder in ('src', 'scripts'):
        shutil.copytree(repo / folder, root / folder, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    declaration = _install_synthetic_capabilities(root)
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1',
        'PYTHONPATH': os.pathsep.join((str(root / 'src'), str(root)))}
    completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--check-capabilities', str(root)],
        cwd=root, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    assert completed.returncode == 0, completed.stdout + '\n' + completed.stderr
    assert _read(declaration)['producer_ref'] == _ref(root / 'src' / 'sitecustomize.py')


@pytest.mark.skipif(os.environ.get('RUN_LONG_ACCOUNT_VERSION_INTEGRATION') != '1',
    reason='慢速合成504日双成本完整验收；显式 RUN_LONG_ACCOUNT_VERSION_INTEGRATION=1 执行')
def test_public_account_version_continuation_preserves_prefix_and_completes_real_workers(tmp_path):
    """独立复制源码防止修改当前部署；只有人工数据进入真实受限公共后缀。"""
    repo = Path(__file__).resolve().parents[2]
    root = tmp_path / 'synthetic_project'
    ignored = shutil.ignore_patterns('__pycache__', '*.pyc')
    for folder in ('src', 'scripts'):
        shutil.copytree(repo / folder, root / folder, ignore=ignored)
    _install_synthetic_capabilities(root)
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1',
        'PYTHONPATH': os.pathsep.join((str(root / 'src'), str(root), str(Path(__file__).parent))),
        **{key: '1' for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    try:
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--scenario', str(root)],
            cwd=root, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=14400)
    except subprocess.TimeoutExpired as error:
        for name, value in (('STDOUT', error.stdout), ('STDERR', error.stderr)):
            output = value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''
            (root / ('SCENARIO_' + name + '.log')).write_text(output, encoding='utf-8')
        _write(root / 'SYNTHETIC_INTEGRATION_RESULT.json', {'status': 'TIMED_OUT',
            'timeout_seconds': error.timeout, 'real_acceptance': False})
        raise
    (root / 'SCENARIO_STDOUT.log').write_text(completed.stdout, encoding='utf-8')
    (root / 'SCENARIO_STDERR.log').write_text(completed.stderr, encoding='utf-8')
    assert completed.returncode == 0, completed.stdout + '\n' + completed.stderr
    result = _read(root / 'SYNTHETIC_INTEGRATION_RESULT.json')
    assert result['status'] == 'PASSED' and result['feedback_costs'] == ['BASE', 'STRESS']
    assert result['account_sessions'] == 504 and result['charged_prefix_segments'] == 2
    assert result['account_jobs'] == 2 and result['preparation_jobs'] == 1


if __name__ == '__main__':
    if sys.argv[1] == '--initialize':
        raise SystemExit(_initialize_only(Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])))
    elif sys.argv[1] == '--check-capabilities':
        _check_synthetic_capabilities(Path(sys.argv[2]))
    else:
        assert sys.argv[1] == '--scenario'
        _scenario(Path(sys.argv[2]))
