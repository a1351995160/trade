"""一次新版有账恢复的合成全链；不操作真实研究、授权或进程。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory import continuous_research_contract_v1 as contracts
from chanlun_trader.research_factory import research_capabilities_v1 as caps
from chanlun_trader.research_factory import report_recovery_capability_bridge_v2 as bridges
from chanlun_trader.research_factory import report_repair_stop_v2 as stops
from chanlun_trader.research_factory.campaign_scope_v1 import RESOURCES, grant_summary
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.report_recovery_protocol_v2 import ReportRecoveryV2, REQUIRED_RUNTIME_PATHS, REPLACEMENT_PATHS
from chanlun_trader.research_factory.report_repair_protocol_v1 import ReportRepairV1
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
from test_report_repair_protocol_v1 import case, approve, ref, write


def test_process_identity_probe_reads_current_process_in_utc():
    import os
    created = stops._process_created_at(os.getpid())
    assert created.tzinfo == timezone.utc and created <= datetime.now(timezone.utc)


def test_pure_proof_scope_rechecks_changes_before_return_and_copies_values():
    source, calls = {'identity': 'original'}, []
    def read():
        calls.append(True)
        return deepcopy(source)
    @stops._terminal_proof_scope
    def validate():
        cached = stops._within_terminal_proof(('SYNTHETIC',), read)
        cached['identity'] = 'caller mutation'
        assert stops._within_terminal_proof(('SYNTHETIC',), read)['identity'] == 'original'
        source['identity'] = 'changed after first proof'
        return 'must not escape'
    with pytest.raises(PermissionError, match='STOP_TERMINAL_CHANGED_DURING_VALIDATION'):
        validate()
    assert len(calls) == 2 and stops._TERMINAL_PROOFS.get() is None


def test_pure_proof_scope_exception_resets_and_next_call_reads_again():
    calls = []
    def read():
        calls.append(True)
        return {'identity': len(calls)}
    @stops._terminal_proof_scope
    def fail():
        stops._within_terminal_proof(('SYNTHETIC',), read)
        raise RuntimeError('SYNTHETIC_VALIDATION_FAILURE')
    with pytest.raises(RuntimeError, match='SYNTHETIC_VALIDATION_FAILURE'):
        fail()
    assert stops._TERMINAL_PROOFS.get() is None
    assert stops._within_terminal_proof(('SYNTHETIC',), read) == {'identity': 2}


def configure_public_report_fixture(job, checks, index, root):
    """固定零交易504日原件；不运行或冒称真实账户核验。"""
    from chanlun_trader.research_factory.strategy_report_v1 import build_report
    from chanlun_trader.research_factory.universe_account_inputs_v1 import prepare_universe_account_inputs_v1
    from chanlun_trader.research_factory.universe_execution_artifacts_v1 import DayArtifacts
    from chanlun_trader.research_factory.universe_research_report_v2 import default_observation_plan
    from chanlun_trader.research_factory.universe_submission_v1 import freeze_universe_bundle
    from universe_test_fixture_v1 import fixture
    window, bundle = fixture(days_count=564)
    inputs = prepare_universe_account_inputs_v1(bundle, window)
    account = Path(job['root'])
    snapshot_root = account.parent / 'SYNTHETIC_FROZEN_INPUT'
    snapshot_root.mkdir()
    snapshot, frames = freeze_universe_bundle({'bundle': bundle, 'window': inputs.window,
        'input_identity': inputs.input_identity}, snapshot_root)
    input_ref = ref(snapshot)
    for path in [snapshot, *map(Path, frames)]:
        reference = ref(path)
        job['source_hashes'][str(path)] = reference['sha256']
        target = account / 'source-archive' / (reference['sha256'] + '_' + path.name)
        target.write_bytes(path.read_bytes())
    job.update(input_identity=inputs.input_identity, benchmark_mode='CASH_AND_PRICE_REFERENCE',
        observation_plan=default_observation_plan(inputs.window),
        resource_job_total_seconds=job['resources']['total_seconds'] * 2)
    for name, item in job['items'].items():
        item.update(loader='chanlun_trader.research_factory.strategy_submission_v1:load_frozen_bundle',
            loader_kwargs={**input_ref, 'input_identity': inputs.input_identity},
            factory='chanlun_trader.research_factory.strategy_submission_v1:public_rule_factory',
            execution_profile=deepcopy(job['resources']), observation_plan=deepcopy(job['observation_plan']),
            benchmark_mode=job['benchmark_mode'])
        item['backend_options'].update(window=deepcopy(inputs.window), initial_cash=50000)
        job['plans'][name].update(runtime=deepcopy(item), backend={'backend': 'UNIVERSE_ACCOUNT_BACKEND_V2',
            'window': deepcopy(inputs.window), 'initial_cash': 50000, 'execution_profile': deepcopy(job['resources'])})
        artifacts = DayArtifacts(account / (name + '_SYNTHETIC_DAYS'), identity=stable_hash(name))
        first = inputs.calendar.index(inputs.window['account_start'])
        for number in range(first, len(inputs.calendar)):
            day, previous = inputs.calendar[number], inputs.calendar[number - 1]
            previous_text = str(previous)
            artifacts.commit(day, {'account': {'date': day, 'cash': 50000., 'equity': 50000., 'positions': []},
                'scan': {'date': day, 'rows': [{'symbol': symbol, 'side': 'HOLD', 'reason': 'SYNTHETIC_NO_SIGNAL',
                    'conditions': {'ready': True, 'buy': False, 'sell': False, 'market_filter': True}}
                    for symbol in inputs.window['symbols']]},
                'decision': {'plan': {'decision_at': previous_text[:4] + '-' + previous_text[4:6] + '-' + previous_text[6:]
                    + 'T15:00:00+08:00', 'next_session': day, 'intents': [], 'excluded': []}},
                'allocation_records': [], 'skipped_intents': []})
        metrics = {'net_return': 0., 'max_drawdown': 0., 'total_fees': 0., 'trade_count': 0}
        report = build_report(strategy={'version': 'SYNTHETIC_ZERO_ACCOUNT'}, stage='ACCOUNT_BACKTEST',
            status='COMPLETED', reasons=[], metrics=metrics,
            artifacts={'trades': 'SYNTHETIC', 'ledger': 'SYNTHETIC', 'source_result': 'SYNTHETIC'},
            benchmark={'status': 'NOT_RUN'}, limitations=['仅合成报告运输夹具，不代表真实账户或核验。'])
        result = {'result_schema': 'UNIVERSE_SHARDED_RESULT_V2', 'input_identity': inputs.input_identity,
            'artifacts': artifacts.manifest(), 'execution_description': {'window': inputs.window},
            'account_policy': {'initial_cash': 50000, 'portfolio': {'max_positions': 5}}, 'fills': [],
            'final_account_checkpoint': {'economic': {'lots': {}, 'orders': {}, 'receivables': {}, 'action_audit': []}},
            'report': report, 'metrics': metrics}
        result_ref = write(account / (name + '_RESULT.json'), result)
        settlement = write(account / (name + '_SETTLEMENT.json'), {
            'completed': True, 'error': None, 'result_sha256': result_ref['sha256']})
        index[name] = {'result': result_ref['path'], 'sha256': result_ref['sha256'], 'settlement': settlement['path']}
        checks[name].update(result_sha256=result_ref['sha256'], input_identity=inputs.input_identity)


_SYNTHETIC_REPORT_WORKER = r'''
from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
handshake = worker_resource_handshake()
import json, os, sys
from copy import deepcopy
from pathlib import Path
from scripts.run_strategy_account_v1 import read_json, save, sha
from chanlun_trader.research_factory.strategy_submission_v1 import load_frozen_bundle, public_rule_factory
from chanlun_trader.research_factory.universe_account_inputs_v1 import prepare_universe_account_inputs_v1
from chanlun_trader.research_factory.universe_execution_artifacts_v1 import hydrated_result
from chanlun_trader.research_factory.universe_research_report_v2 import build_research_reports
from chanlun_trader.research_factory.universe_signal_funnel_v1 import build_signal_funnel_stream_v1, funnel_day_packets_v1
from chanlun_trader.research_factory.universe_benchmark_v1 import _universe_price_reference_from_inputs
from chanlun_trader.research_factory.strategy_report_v1 import render_markdown
path, name, output, repair_id = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), sys.argv[4]
job = read_json(path); folder = output / 'COMPUTE_REPORT'; item = job['items'][name]
execution = handshake['execution']; prefix = 'SEGMENT_' + str(execution['segment_number']).zfill(6)
save(folder / (prefix + '_ACCESS.json'), {'reader_pid': os.getpid(), 'dispatch_id': execution['dispatch_id']})
bundle = load_frozen_bundle(**item['loader_kwargs'])['frame']
inputs = prepare_universe_account_inputs_v1(bundle, item['backend_options']['window'])
raw = read_json(path.parent / (name + '_RESULT.json'))
dual = build_research_reports(hydrated_result(raw), inputs, job['observation_plan'],
    report_checkpoint_path=folder / (name + '_OBSERVATION_CHECKPOINT.json'), segment_seconds=750)
strategy = public_rule_factory(item['factory_kwargs']['payload'], name)
funnel = build_signal_funnel_stream_v1(rule_identity=strategy.rule_identity, strategy_id=name,
    calendar=inputs.calendar, day_packets=funnel_day_packets_v1(raw, inputs.calendar),
    report_checkpoint_path=folder / (name + '_FUNNEL_CHECKPOINT.json'), segment_seconds=750,
    expected_decision_sessions=inputs.calendar[inputs.calendar.index(inputs.window['account_start']):])
save(output / (name + '_RESEARCH_REPORT.json'), dual); save(output / (name + '_SIGNAL_FUNNEL.json'), funnel)
value = {'member': name, 'research_report': str(output / (name + '_RESEARCH_REPORT.json')),
    'research_report_sha256': sha(output / (name + '_RESEARCH_REPORT.json')),
    'funnel': str(output / (name + '_SIGNAL_FUNNEL.json')), 'funnel_sha256': sha(output / (name + '_SIGNAL_FUNNEL.json'))}
save(folder / ('RESULT_' + name + '.json'), value)
final_sha = None
if all((output / (member + '_RESEARCH_REPORT.json')).exists() for member in job['plans']):
    benchmark = _universe_price_reference_from_inputs(inputs, initial_cash=50000,
        report_checkpoint_path=output / 'PRICE_REFERENCE_CHECKPOINT.json')
    for member in job['plans']:
        source = path.parent / (member + '_RESULT.json'); report = deepcopy(read_json(source)['report'])
        report['artifacts']['source_result'] = {'path': str(source), 'sha256': sha(source)}
        report['research'] = {'source_result_sha256': sha(source),
            'account_and_signal': read_json(output / (member + '_RESEARCH_REPORT.json')),
            'funnel': read_json(output / (member + '_SIGNAL_FUNNEL.json'))}
        report['benchmark'] = benchmark
        save(output / (member + '_REPORT.json'), report)
        (output / (member + '_REPORT.md')).write_text(render_markdown(report), encoding='utf-8')
    save(folder / 'FINAL_REPORTS.json', {'job_sha256': sha(path), 'repair_id': repair_id,
        'reports': {member: {'final_report': str(output / (member + '_REPORT.json')),
            'final_report_sha256': sha(output / (member + '_REPORT.json'))} for member in job['plans']}})
    final_sha = sha(folder / 'FINAL_REPORTS.json')
save(folder / (prefix + '_STATUS.json'), {'state': 'COMPLETED', 'dispatch_id': execution['dispatch_id'],
    'member': name, 'result_sha256': sha(folder / ('RESULT_' + name + '.json')),
    **({'final_reports_sha256': final_sha} if final_sha else {})})
'''


def stop_parent(case, monkeypatch, *, known_failed=False, pending=False):
    parent, _, _ = approve(case)
    meter = parent.meter()
    meter.start()
    write(parent.compute_root / 'SCOPE.json', {'authority': parent.authority, 'request': parent.request,
        'stage': 'REPORT', 'job_sha256': parent.summary['original']['job']['sha256'], 'repair_ref': parent.scope_reference})
    dispatch = meter.dispatch()
    resource = write(parent.compute_root / 'SEGMENT_000001_RESOURCE.json', {
        'returncode': 1 if known_failed else 75, 'timed_out': False,
        'dispatch_id': dispatch['dispatch_id'], 'elapsed_wall_seconds': 3, 'member': next(iter(case['job']['plans']))})
    write(parent.compute_root / 'SEGMENT_000001_STATUS.json', {'dispatch_id': dispatch['dispatch_id'],
        'state': 'FAILED' if known_failed else 'CONTINUE'})
    write(parent.compute_root / 'SEGMENT_000001_WORKER.json', {'dispatch_id': dispatch['dispatch_id'], 'pid': 987654323})
    if not pending:
        meter.charge(1, seconds=3, evidence_identity=resource['sha256'], outcome='FAILED' if known_failed else 'CONTINUE')
    for relative in stops.STOP_SOURCE_PATHS:
        path = case['root'] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'# SYNTHETIC trusted stop controller\n')
    proposal = case['root'] / 'reports/recovery/REPORT_RENDER_CONTINUATION_RECOVERY_PROPOSAL_CN.md'
    proposal.parent.mkdir(parents=True)
    proposal.write_text('SYNTHETIC approved finite recovery', encoding='utf-8')
    proposal_ref = ref(proposal)
    base = case['campaign'].peek_status()['base_authorization']
    human = {'schema_version': 'HUMAN_REPORT_RENDER_RECOVERY_CONFIRMATION_V1',
        'origin': 'CURRENT_USER_REPLY_TO_EXACT_FINITE_REPORT_RENDER_RECOVERY_PROPOSAL',
        'proposal': proposal_ref, 'parent_repair_id': parent.repair_id,
        'parent_manifest_sha256': parent.scope_reference['sha256'], 'campaign_id': base['authorization_id'],
        'objective_id': base['objective_id'], 'verification_jobs_delta': 1, 'effective_verification_jobs_from': 5,
        'effective_verification_jobs_to': 6, 'stage': 'EXPLORATION', 'maximum_additional_report_recoveries': 1,
        'recovery_total_seconds': 28800, 'worker_seconds': 900, 'memory_mib': 2048,
        'campaign_total_wall_seconds': 288002, 'expires_at': base['expires_at'],
        'account_replay_permitted': False, 'old_records_immutable': True,
        'controlled_boundary_settlement_authorized': True, 'exact_source_version_recovery_authorized': True,
        'strategy_threshold_changes_permitted': False, 'project_model_api_calls': 0, 'user_reply': 'synthetic approved'}
    human_ref = write(proposal.with_name('HUMAN_REPORT_RENDER_RECOVERY_CONFIRMATION.json'), human)
    summary = stops.ReportRepairStopV2.summary(case['root'], parent.scope_reference, 'SYNTHETIC finite recovery',
        controller_pid=987654321, reader_pid=987654322, proposal_ref=proposal_ref, human_confirmation_ref=human_ref)
    approval = case['approvals'].approve(summary, approver='SYNTHETIC_OWNER', capability=case['capability'])
    request_ref = stops.ReportRepairStopV2.request(case['root'], summary, approval_ref=approval)
    monkeypatch.setattr(stops, '_process_created_at', lambda pid: None)
    return parent, meter, summary, approval, request_ref


def recovery(case, monkeypatch, *, known_failed=False, freeze=True):
    parent, old_meter, stop_summary, _, stop_ref = stop_parent(case, monkeypatch, known_failed=known_failed)
    terminal_ref = stops.ReportRepairStopV2.settle(case['root'], stop_ref)
    base = case['campaign'].peek_status()['base_authorization']
    current = capabilities()
    replacements = []
    for relative in sorted(REPLACEMENT_PATHS):
        path = case['root'] / relative
        old = case['job']['source_hashes'][str(path)]
        path.write_bytes(('# SYNTHETIC newly approved recovery ' + path.name).encode())
        replacements.append({'path': str(path), 'old_sha256': old, 'new_sha256': ref(path)['sha256'],
            'archive_ref': ref(Path(case['job']['root']) / 'source-archive' / (old + '_' + path.name))})
    for relative in REQUIRED_RUNTIME_PATHS:
        path = case['root'] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('# SYNTHETIC approved runtime ' + path.name).encode())
    current['full_universe']['source_hashes']['universe_account_inputs_v1.py'] = next(
        row['new_sha256'] for row in replacements if row['path'].endswith('universe_account_inputs_v1.py'))
    current['fingerprint'] = stable_hash({key: value for key, value in current.items() if key != 'fingerprint'})
    monkeypatch.setattr(caps, 'capabilities', lambda: deepcopy(current))
    monkeypatch.setattr(contracts, 'capabilities', lambda: deepcopy(current))
    monkeypatch.setattr(bridges, 'capabilities', lambda: deepcopy(current))
    bridge_summary = bridges.ReportRecoveryCapabilityBridgeV2.summary(case['root'], base,
        recovery_context={'parent_terminal_ref': terminal_ref,
            'recovery_id': ReportRecoveryV2.recovery_identity(base['authorization_id'], parent.scope_reference),
            'proposal_ref': stop_summary['proposal_ref'], 'human_confirmation_ref': stop_summary['human_confirmation_ref']},
        source_replacements=replacements)
    bridge_approval = case['approvals'].approve(bridge_summary, approver='SYNTHETIC_OWNER', capability=case['capability'])
    bridge_ref = bridges.ReportRecoveryCapabilityBridgeV2.install(case['root'], base, bridge_summary, approval_ref=bridge_approval)
    delta = dict.fromkeys(RESOURCES, 0)
    delta['verification_jobs'] = 1
    grant = {'grant_id': ('SESSION_B001_REPORT_RECOVERY_20261011' if case.get('public_transport') else 'SYNTHETIC_ONE_REPORT_RECOVERY'), 'base_authorization_hash': stable_hash(base),
        'resource_limits_delta': delta, 'stage_limits_delta': {'EXPLORATION': deepcopy(delta)},
        'max_batches_delta': 0, 'max_total_predictive_trials_delta': 0, 'expires_at': base['expires_at']}
    summary = ReportRecoveryV2.summary(case['root'], parent_terminal_ref=terminal_ref, grant=grant,
        source_replacements=[{key: value for key, value in item.items() if key != 'archive_ref'} for item in replacements],
        runtime_source_refs=[ref(case['root'] / path) for path in sorted(REQUIRED_RUNTIME_PATHS)],
        capability_bridge_ref=bridge_ref)
    grant_approval = case['approvals'].approve(grant_summary(base, grant), approver='SYNTHETIC_OWNER', capability=case['capability'])
    approval = case['approvals'].approve(summary, approver='SYNTHETIC_OWNER', capability=case['capability'])
    if not freeze:
        return None, parent, terminal_ref, summary, approval, grant_approval
    reference = ReportRecoveryV2.freeze(case['root'], summary, approval_ref=approval, grant_approval_ref=grant_approval)
    return ReportRecoveryV2(case['root'], reference).validate(), parent, terminal_ref, summary, approval, grant_approval


@pytest.mark.parametrize('case', ['recovery_chain'], indirect=True)
def test_reservation_before_grant_crash_has_no_new_free_increment(case, monkeypatch):
    from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
    _, _, _, summary, approval, grant_approval = recovery(case, monkeypatch, freeze=False)
    before = case['campaign'].peek_status()
    original = ResearchCampaignV1.add_grant
    def crash(*args, **kwargs):
        raise RuntimeError('SYNTHETIC_CRASH_BEFORE_GRANT_APPEND')
    with monkeypatch.context() as patch:
        patch.setattr(ResearchCampaignV1, 'add_grant', crash)
        with pytest.raises(RuntimeError, match='CRASH_BEFORE_GRANT'):
            ReportRecoveryV2.freeze(case['root'], summary, approval_ref=approval, grant_approval_ref=grant_approval)
    prefix = case['campaign'].peek_status()
    operation_id = 'report_recovery_' + summary['repair_id']
    assert prefix['operations'][operation_id]['status'] == 'RESERVED'
    assert summary['grant']['grant']['grant_id'] not in prefix['grants']
    assert prefix['remaining']['verification_jobs'] == before['remaining']['verification_jobs'] - 1
    assert prefix['remaining']['wall_seconds'] == before['remaining']['wall_seconds'] - 28800
    manifest = Path(summary['original']['task']['path']).parent / 'report-repairs' / summary['repair_id'] / 'MANIFEST.json'
    with pytest.raises(PermissionError, match='INCREMENT_NOT_REGISTERED'):
        ReportRecoveryV2(case['root'], ref(manifest)).validate()
    # 即使另一用途占用原有余量，也不能消耗尚未追加的专用+1。
    # 其余用途不能越过真实现有余额；未追加的grant不作为可用资源。
    with pytest.raises(RuntimeError, match='RESOURCE_LIMIT'):
        case['campaign'].reserve_operation(operation_id='OTHER_VERIFY', batch_id='batch_001', stage='EXPLORATION',
            kind='VERIFY', subject_identity=stable_hash('other'),
            upper_bounds={'verification_jobs': 1, 'wall_seconds': prefix['remaining']['wall_seconds'] + 1})
    monkeypatch.setattr(ResearchCampaignV1, 'add_grant', original)
    reference = ReportRecoveryV2.freeze(case['root'], summary, approval_ref=approval, grant_approval_ref=grant_approval)
    context = ReportRecoveryV2(case['root'], reference).validate()
    after = context.campaign.peek_status()
    assert len(after['grants']) == len(before['grants']) + 1
    assert after['reserved']['verification_jobs'] == 1
    assert after['remaining']['verification_jobs'] == before['remaining']['verification_jobs']


@pytest.mark.parametrize('case', ['recovery_chain'], indirect=True)
def test_inherited_failure_refs_and_records_are_still_rechecked(case, monkeypatch):
    context, _, terminal, _, _, _ = recovery(case, monkeypatch)
    failure = context.summary['original_failure']
    references = [failure[key] for key in ('attempt_end', 'scope', 'compute_start', 'dispatch', 'charge', 'resource')]
    references += failure['candidate_evidence']
    for reference in references:
        path = Path(reference['path'])
        original = path.read_bytes()
        path.write_bytes(original + b' ')
        try:
            with pytest.raises(PermissionError, match='ORIGINAL_FAILURE_CHANGED|ORIGINAL_CHANGED'):
                stops.read_report_repair_terminal(case['root'], terminal)
        finally:
            path.write_bytes(original)
    ledger = context.campaign.budget_path.with_name('run_budget_events.jsonl')
    original = ledger.read_bytes()
    for operation_id in (failure['operation']['operation_id'], failure['candidate_operation']['operation_id']):
        events = [json.loads(line) for line in original.splitlines() if line]
        event = next(row for row in events if row['event_type'] == 'CAMPAIGN_OPERATION_SETTLED'
                     and row['payload']['operation_id'] == operation_id)
        event['payload']['evidence_identity'] = stable_hash('synthetic altered original failure')
        event['event_id'] = stable_hash({key: event[key] for key in ('run_id', 'event_type', 'payload')})
        ledger.write_text('\n'.join(json.dumps(row) for row in events) + '\n', encoding='utf-8')
        try:
            with pytest.raises(PermissionError, match='ORIGINAL_FAILURE_LEDGER_CHANGED'):
                context.validate(for_dispatch=False)
        finally:
            ledger.write_bytes(original)


@pytest.mark.parametrize('case', ['recovery_chain'], indirect=True)
@pytest.mark.parametrize('known_failed', [False, True])
def test_stop_then_versioned_recovery_reserves_exact_increment(case, monkeypatch, known_failed):
    context, parent, terminal_ref, summary, approval, grant_approval = recovery(case, monkeypatch, known_failed=known_failed)
    view = case['campaign'].peek_status()
    assert view['authorization']['resource_limits']['verification_jobs'] == 6
    assert view['authorization']['resource_limits']['wall_seconds'] == 288002
    assert view['operations'][parent.campaign_operation['operation_id']]['status'] == 'FAILED'
    assert view['operations'][context.campaign_operation['operation_id']]['status'] == 'RESERVED'
    assert view['reserved']['verification_jobs'] == 1
    assert context.control_paused() is False
    assert stops.read_report_repair_terminal(case['root'], terminal_ref)['terminal_kind'] == (
        'KNOWN_FAILED' if known_failed else 'CONTROLLED_STOP')
    assert ReportRecoveryV2.freeze(case['root'], summary, approval_ref=approval, grant_approval_ref=grant_approval) == context.scope_reference
    with pytest.raises((PermissionError, ValueError)):
        parent.validate()
    context.control('PAUSE', 'synthetic pause new recovery only')
    assert context.control_paused() is True
    context.control('RESUME', 'synthetic resume same recovery')
    assert context.control_paused() is False


@pytest.mark.parametrize('case', ['recovery_chain'], indirect=True)
@pytest.mark.parametrize('block', ['pending', 'pid', 'owner', 'archive'])
def test_stop_rejects_unsettled_active_or_unapproved_sources(case, monkeypatch, block):
    parent, meter, summary, approval, request_ref = stop_parent(case, monkeypatch, pending=block == 'pending')
    if block == 'pid':
        monkeypatch.setattr(stops, '_process_created_at', lambda pid: datetime.fromisoformat('2020-01-01T00:00:00+00:00'))
    elif block == 'owner':
        (case['approvals'].directory / (approval['approval_id'] + '.json')).unlink()
    elif block == 'archive':
        request = json.loads(Path(request_ref['path']).read_text(encoding='utf-8'))
        item = request['source_archive'][0]
        Path(item['archive_ref']['path']).write_bytes(b'# synthetic replaced archive')
        item['archive_ref'] = ref(Path(item['archive_ref']['path']))
        request['identity'] = stable_hash({key: value for key, value in request.items() if key != 'identity'})
        request_ref = write(Path(request_ref['path']), request)
    with pytest.raises((PermissionError, ValueError)):
        stops.ReportRepairStopV2.settle(case['root'], request_ref)
    assert case['campaign'].peek_status()['operations'][parent.campaign_operation['operation_id']]['status'] == 'RUNNING'


@pytest.mark.parametrize('case', ['recovery_large_chain'], indirect=True)
def test_stop_never_reads_or_copies_large_original_data(case, monkeypatch):
    original_read = stops.read_file_bytes
    observed = []
    def read(path, **kwargs):
        assert Path(path).suffix == '.py' or Path(path).name == 'run_budget_events.jsonl'
        observed.append(str(path))
        return original_read(path, **kwargs)
    monkeypatch.setattr(stops, 'read_file_bytes', read)
    parent, _, summary, _, reference = stop_parent(case, monkeypatch)
    assert len(summary['original_data_refs']) == 2
    terminal = stops.ReportRepairStopV2.settle(case['root'], reference)
    assert stops.read_report_repair_terminal(case['root'], terminal)['original_data_refs'] == summary['original_data_refs']
    assert all(path.suffix == '.py' for path in (parent.output_root / 'stop-source-archive').iterdir())
    assert not list(parent.output_root.rglob('*.parquet'))


@pytest.mark.parametrize('case', ['recovery_chain'], indirect=True)
def test_failure_after_stop_request_is_captured_without_rewriting_charge(case, monkeypatch):
    parent, meter, _, _, request = stop_parent(case, monkeypatch, pending=True)
    resource = write(parent.compute_root / 'SEGMENT_000001_RESOURCE.json', {
        'returncode': 1, 'timed_out': False, 'dispatch_id': meter.status()['pending']['dispatch_id']})
    meter.charge(1, seconds=3, evidence_identity=resource['sha256'], outcome='FAILED')
    before = (parent.compute_root / 'COMPUTE_SEGMENT_000001_CHARGE.json').read_bytes()
    operation = parent.campaign.peek_status()['operations'][parent.campaign_operation['operation_id']]
    terminal = stops.ReportRepairStopV2.settle(case['root'], request)
    record = stops.read_report_repair_terminal(case['root'], terminal)
    assert record['terminal_kind'] == 'FAILED_AFTER_STOP'
    assert parent.campaign.peek_status()['operations'][parent.campaign_operation['operation_id']] == operation
    assert (parent.compute_root / 'COMPUTE_SEGMENT_000001_CHARGE.json').read_bytes() == before
    assert stops.ReportRepairStopV2.settle(case['root'], request) == terminal


@pytest.mark.parametrize('case', ['recovery_chain'], indirect=True)
@pytest.mark.parametrize('identity', ['reused', 'original', 'equal', 'unavailable'])
def test_stop_distinguishes_historical_pid_reuse_without_ignoring_original_process(case, monkeypatch, identity):
    parent, meter, _, _, request = stop_parent(case, monkeypatch)
    charged_at = datetime.fromisoformat(meter.status()['segments'][0]['charge']['charged_at'])
    access_at = charged_at - timedelta(seconds=1)
    write(parent.compute_root / 'SEGMENT_000001_ACCESS.json', {
        'dispatch_id': meter.status()['segments'][0]['dispatch']['dispatch_id'],
        'reader_pid': 20248, 'accessed_at': access_at.isoformat()})
    def created(pid):
        if pid != 20248:
            return None
        if identity == 'unavailable':
            raise PermissionError('STOP_PROCESS_IDENTITY_UNAVAILABLE')
        return charged_at + timedelta(seconds={'reused': 1, 'original': -1, 'equal': 0}[identity])
    monkeypatch.setattr(stops, '_process_created_at', created)
    if identity == 'reused':
        terminal = stops.ReportRepairStopV2.settle(case['root'], request)
        assert stops.read_report_repair_terminal(case['root'], terminal)['terminal_kind'] == 'CONTROLLED_STOP'
    else:
        with pytest.raises(PermissionError, match='STOP_PROCESS_STILL_ACTIVE|STOP_PROCESS_IDENTITY_UNAVAILABLE'):
            stops.ReportRepairStopV2.settle(case['root'], request)
        assert parent.campaign.peek_status()['operations'][parent.campaign_operation['operation_id']]['status'] == 'RUNNING'


@pytest.mark.parametrize('case', ['recovery_chain'], indirect=True)
def test_new_failure_unknown_and_changed_source_cannot_create_another_recovery(case, monkeypatch):
    context, _, _, summary, approval, grant_approval = recovery(case, monkeypatch)
    meter = context.meter()
    meter.start()
    segment = meter.dispatch()
    context.campaign.mark_unknown(context.campaign_operation['operation_id'], 'synthetic lost receipt')
    with pytest.raises(PermissionError, match='FAILED_OR_UNKNOWN'):
        context.validate()
    reconciler = context.meter(for_dispatch=False)
    reconciler.charge(segment['number'], seconds=None, outcome='FAILED')
    with pytest.raises(PermissionError, match='FAILED_OR_UNKNOWN'):
        context.validate()
    forged = deepcopy(summary)
    forged['repair_id'] = stable_hash('synthetic second recovery id')
    forged_approval = case['approvals'].approve(forged, approver='SYNTHETIC_OWNER', capability=case['capability'])
    with pytest.raises(PermissionError, match='SUMMARY_CHANGED'):
        ReportRecoveryV2.freeze(case['root'], forged, approval_ref=forged_approval, grant_approval_ref=grant_approval)
    source = case['root'] / 'src/chanlun_trader/research_factory/universe_benchmark_v1.py'
    source.write_bytes(b'# synthetic unapproved later version')
    with pytest.raises(PermissionError):
        context.validate(for_dispatch=False)


@pytest.mark.parametrize('case', ['recovery_public_chain'], indirect=True)
def test_public_transport_recovers_dual_504_reports_receipt_feedback_and_reentry(case, monkeypatch):
    """真实受限运输+报告库的合成链；不是生产worker或真实市场/账户验收。"""
    import os
    import sys
    from scripts import run_report_recovery_v2 as cli, run_strategy_account_v1 as runner
    from chanlun_trader import synthetic_batch_resources as resources
    from chanlun_trader.research_factory.business_validation_protocol_v1 import BusinessValidationProtocolV1
    from test_session_report_recovery_helpers_v2 import helper, require
    from types import SimpleNamespace
    case['public_transport'] = True
    context, parent, _, _, _, _ = recovery(case, monkeypatch)
    originals = {path: path.read_bytes() for path in [context.job_path,
        context.job_path.parent / 'VERIFICATION.json', *[Path(row[key]['path'])
        for row in context.summary['original']['accounts'].values() for key in ('result', 'settlement')]]}
    actual_root = Path(runner.__file__).resolve().parents[1]
    actual_bounded = resources.run_bounded_worker
    calls = []
    def bounded(command, **options):
        member = command[command.index('--member') + 1]
        assert options['memory_mib'] == 2048 and 0 < options['wall_seconds'] <= 890
        options['root'] = actual_root
        options['environment']['PYTHONPATH'] = os.pathsep.join((str(actual_root / 'src'), str(actual_root)))
        result = actual_bounded([sys.executable, '-c', _SYNTHETIC_REPORT_WORKER,
            str(context.job_path), member, str(context.output_root), context.repair_id], **options)
        calls.append(result)
        return result
    monkeypatch.setattr(runner, 'REPO', case['root'])
    monkeypatch.setattr(resources, 'run_bounded_worker', bounded)
    first = cli.advance(case['root'], context.scope_reference)
    assert first['status'] == 'CONTINUE' and first['dispatched_segments'] == 1
    complete = cli.advance(case['root'], context.scope_reference)
    assert complete['status'] == 'REPORT_RECOVERED' and complete['dispatched_segments'] == 1
    assert len(calls) == 2 and all(row['returncode'] == 0 and not row['timed_out'] for row in calls)
    if os.name == 'nt':
        assert all(row['windows_job_bound'] for row in calls)
    receipt_path = context.output_root / 'REPAIR_COMPLETION.json'
    saved = receipt_path.read_bytes()
    receipt = context.verify_receipt()
    assert receipt['outcome']['dispatched_segments'] == 2
    refs = BusinessValidationProtocolV1.verify_report_repair_refs(case['task'], receipt['outcome'],
        root=case['root'], manifest_ref=context.scope_reference, minimum_sessions=504)
    assert {cost: row['sessions'] for cost, row in refs.items()} == {'BASE': 504, 'STRESS': 504}
    # 包括“receipt已落盘、返回前中断”的同参数重入；当次0不能改变不可变累计回执。
    repeated = cli.advance(case['root'], context.scope_reference)
    assert repeated['receipt_identity'] == complete['receipt_identity'] and repeated['dispatched_segments'] == 0
    assert len(calls) == 2 and receipt_path.read_bytes() == saved
    feedback = cli.feedback(case['root'], context.scope_reference)
    value = json.loads(Path(feedback['feedback']['path']).read_text(encoding='utf-8'))
    assert value['engineering_status'] == 'REPORT_RECOVERED' and value['independent_days'] == 0
    assert value['original_candidate_status'] == 'FAILED' and value['formal_qualification'] is False
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert context.campaign.peek_status()['operations'][parent.campaign_operation['operation_id']]['status'] == 'FAILED'
    # V3只读容量检查使用真正上述链的campaign，未创建第二批或增批额度。
    view = context.campaign.peek_status()
    needs = helper('session_next_batch_setup_v3.py')._capacity(SimpleNamespace(_require=require, NEXT_BATCH='batch_002'), view)
    assert needs['account_jobs'] == 2 and needs['verification_jobs'] == 2
