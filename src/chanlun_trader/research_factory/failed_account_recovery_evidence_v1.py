"""失败接续的只读治理证据适配；经济核账直接复用原实现。"""
from contextlib import contextmanager
import math
from pathlib import Path
import time

import pandas as pd

from ..account_startup_boundary_v1 import AccountStartupBoundaryV1, StartupBoundaryExceeded
from ..research.guard import FinalTestAccessViolation
from . import research_evidence_v1 as original
from .account_version_continuation_v1 import AccountVersionContinuationV1
from .budget import BudgetLedgerMismatchError, SearchBudgetRegistryV1
from .common import stable_hash
from .failed_account_recovery_v1 import COMMITTED_DAYS, FAILED_SEGMENT_COUNT, FailedAccountRecoveryV1
from .run_budget import effective_execution_status, execution_dispatch_identity
from .secure_file_reference_v1 import file_sha256, read_pinned_json
from .universe_evidence_v1 import reconstruct_universe_account


ADAPTER_VERSION = 'FAILED_ACCOUNT_RECOVERY_EVIDENCE_V1'


def _require(condition, reason):
    if not condition:
        raise ValueError('FAILED_ACCOUNT_RECOVERY_EVIDENCE_' + reason)


def _remaining(deadline, seconds):
    if deadline is None:
        return seconds
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        from .universe_account_backend_v2 import SegmentBoundary
        raise SegmentBoundary('UNIVERSE_VERIFICATION_COOPERATIVE_DEADLINE')
    return remaining


@contextmanager
def _startup_boundary(deadline):
    if deadline is None:
        yield None
        return
    try:
        boundary = AccountStartupBoundaryV1(deadline)
        with boundary.scope(), boundary.stage_scope('FAILED_ACCOUNT_RECOVERY_EVIDENCE_STARTUP'):
            yield boundary
    except StartupBoundaryExceeded as exc:
        from .universe_account_backend_v2 import SegmentBoundary
        raise SegmentBoundary('UNIVERSE_VERIFICATION_COOPERATIVE_DEADLINE') from exc


def _fresh_context(job, name):
    context = AccountVersionContinuationV1.from_job(job)
    _require(type(context) is AccountVersionContinuationV1
        and type(getattr(context, 'recovery', None)) is FailedAccountRecoveryV1,
        'OWNER_CONTEXT_REQUIRED')
    context.validate_sources(job)
    _require(context.recovery_installed and not context.recovery_mirror_pending
        and context.recovery.job == job and name in context.recovery.summary_value['accounts'],
        'INSTALLED_OWNER_CONTEXT_REQUIRED')
    _require(context.proof(name).get('failed_account_recovery') == context.recovery.proof(),
        'VERSION_PROOF_CONFLICT')
    return context


def _original_failed_prefix(context):
    recovery = context.recovery
    name = recovery.original_failed_account
    row = recovery.summary_value['accounts'][name]
    _require(row['status_at_install'] == 'FAILED' and row['segment_count'] == FAILED_SEGMENT_COUNT
        and row['checkpoint']['processed_days'] == COMMITTED_DAYS, 'ORIGINAL_FAILED_BOUNDARY_CONFLICT')
    references = row['segment_refs'] + [row['start_ref'], row['settlement_ref'], row['checkpoint']['archive_ref']]
    for reference in references:
        _require(file_sha256(reference['path'], root=context.root,
            error_code='FAILED_ACCOUNT_RECOVERY_EVIDENCE_ORIGINAL_CHANGED') == reference['sha256'],
            'ORIGINAL_REFERENCE_CHANGED')
    settlement = read_pinned_json(row['settlement_ref'], root=context.root,
        error_code='FAILED_ACCOUNT_RECOVERY_EVIDENCE_ORIGINAL_SETTLEMENT_CHANGED')
    _require(settlement.get('completed') is False and settlement.get('result_sha256') is None
        and settlement.get('wall_seconds') == row['charged_seconds'], 'ORIGINAL_SETTLEMENT_CONFLICT')
    checkpoint = read_pinned_json(row['checkpoint']['archive_ref'], root=context.root,
        error_code='FAILED_ACCOUNT_RECOVERY_EVIDENCE_ORIGINAL_CHECKPOINT_CHANGED')
    _require(len(checkpoint['artifacts']['days']) == COMMITTED_DAYS, 'ORIGINAL_DAY_PREFIX_CONFLICT')
    return checkpoint


def _verify_unknown_segment(root, prefix, dispatch, charge, following, receipt, context, operation):
    if dispatch.get('failed_account_recovery_proof') is None:
        # 原17/27等缺测段按原门禁验证，原上界费用和缺测事实完整保留。
        original._verify_interrupted_segment(root, prefix, dispatch, charge, following, receipt)
        return
    _require(charge['outcome'] == 'CONTINUE' and charge['seconds'] == dispatch['upper_bound_seconds']
        and charge['measured_seconds'] is None and charge['evidence_identity'] is None
        and not (root / (prefix + '_RESOURCE.json')).exists(), 'UNKNOWN_CHARGE_CONFLICT')
    resume = original._json(root / (prefix + '_RESUME.json'))
    proof = context.recovery.proof()
    _require(resume.get('dispatch_id') == dispatch['dispatch_id'] and resume.get('budget_reused') is True
        and resume.get('charge_basis') == 'UNKNOWN_UPPER_BOUND'
        and resume.get('dispatch_identity') == dispatch['dispatch_identity']
        and resume.get('failed_account_recovery_proof') == proof
        and resume.get('account_version_continuation') == context.segment_proof(dispatch['kind'], dispatch['segment_number']),
        'UNKNOWN_RESUME_NOT_PROVEN')
    worker_path = root / (prefix + '_WORKER.json')
    if resume.get('execution_not_launched') is True:
        marker_path = root / (prefix + '_MIRROR_REPAIR.json')
        marker = original._json(marker_path)
        parent = operation['segments'][dispatch['segment_number'] - 1]
        _require(not worker_path.exists() and not (root / (prefix + '_INPUT_ACCESS.json')).exists()
            and original._sha(marker_path) == resume.get('mirror_repair_sha256')
            and marker.get('version') == 'CAMPAIGN_SEGMENT_MIRROR_REPAIR_V1'
            and marker.get('receipt_id') == receipt['receipt_id']
            and marker.get('source_identity') == stable_hash(receipt['source'])
            and marker.get('dispatch_id') == dispatch['dispatch_id'] and marker.get('worker_absent') is True
            and marker.get('operation_id') == operation['operation_id']
            and marker.get('parent_dispatch_identity') == execution_dispatch_identity(parent)
            and marker.get('failed_account_recovery_proof') == proof, 'UNKNOWN_MIRROR_PROOF_CONFLICT')
    else:
        worker = original._json(worker_path)
        _require(worker.get('dispatch_id') == dispatch['dispatch_id']
            and worker.get('segment_number') == dispatch['segment_number']
            and worker.get('purpose') == dispatch['kind'] and worker.get('failed_account_recovery_proof') == proof
            and isinstance(resume.get('state_identity'), str) and len(resume['state_identity']) == 64,
            'UNKNOWN_WORKER_BINDING_CONFLICT')
        resumed = pd.Timestamp(resume['resumed_at'])
        _require(resumed.tzinfo is not None and pd.Timestamp(dispatch['dispatched_at']) <= resumed
            <= pd.Timestamp(following['dispatched_at']), 'UNKNOWN_RESUME_TIME_CONFLICT')


def _input_access(job, name, resource, worker, access, expected_version):
    _require(all(value.get('account_version_continuation') == expected_version for value in (worker, access)),
        'INPUT_ACCESS_VERSION_CONFLICT')
    direct = worker['pid'] == access['reader_pid']
    child = (access.get('resource_platform') == resource.get('resource_platform') == 'nt'
        and access.get('reader_parent_pid') == worker['pid'] == access.get('launcher_pid')
        and resource.get('launcher_pid') == worker['pid'] and access.get('windows_job_verified') is True
        and resource.get('windows_job_bound') is True)
    item = job['items'][name]
    _require((direct or child) and worker['purpose'] == access['purpose'] == name
        and access['input_identity'] == job['input_identity'] and access['loader'] == item['loader']
        and access['loader_kwargs'] == item['loader_kwargs'], 'INPUT_ACCESS_CONFLICT')


def _result_binding(root, name, paths, settlement):
    result_path = root / (name + '_RESULT.json')
    _require(result_path.resolve() == result_path and paths['settlement'].resolve() == paths['settlement'],
        'RESULT_OR_SETTLEMENT_REDIRECTED')
    digest = original._sha(result_path)
    index_path = root / 'RESULTS_INDEX.json'
    index_digest = original._sha(index_path)
    index = original._json(index_path)['items'][name]
    # Reject redirected index names before any filesystem operation on their values.
    _require(Path(index['result']) == result_path
        and Path(index['settlement']) == paths['settlement']
        and index['sha256'] == digest == settlement['result_sha256'], 'RESULT_HASH_BINDING_CONFLICT')
    return result_path, digest, index_path, index_digest


def _verify_segment_resources(job, name, aggregate, settlement, context, operation):
    from scripts import run_strategy_account_v1 as runner
    authority = context._authority_ledger_reference()
    state = context.governance.segment_status(name)
    boundary = context.recovery.summary_value['accounts'][name]
    count = boundary['segment_count']
    root = Path(job['root'])
    _require(state['pending'] is None and len(state['segments']) > count
        and state['segments'][-1]['charge']['outcome'] == 'COMPLETED'
        and effective_execution_status(operation) == 'COMPLETED'
        and operation['segments'][:count] == boundary['parent_operation'].get('segments', [])
        and state.get('failed_account_recovery_proof') == context.recovery.proof(), 'COMPLETION_NOT_PROVEN')
    original._same(aggregate['elapsed_wall_seconds'], state['charged_seconds'], 'CUMULATIVE_RESOURCE_CONFLICT', 1e-6)
    original._same(settlement['wall_seconds'], state['charged_seconds'], 'CUMULATIVE_SETTLEMENT_CONFLICT', 1e-6)
    _require(aggregate.get('active_metering') is True and aggregate.get('segment_count') == len(state['segments'])
        and aggregate.get('segments') == [row['charge']['charge_id'] for row in state['segments']], 'RESOURCE_CHAIN_CONFLICT')
    receipt = original._json(root / 'CONFIRMATION.json')
    measured = conservative = historical_failed = 0.
    for index, row in enumerate(state['segments']):
        dispatch, charge = row['dispatch'], row['charge']
        number = dispatch['segment_number']; prefix = runner.segment_prefix(name, number)
        context._validate_dispatch_from_state(name, number, state)
        if charge['basis'] == 'UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND':
            _require(index + 1 < len(state['segments']), 'UNKNOWN_CANNOT_COMPLETE_ACCOUNT')
            _verify_unknown_segment(root, prefix, dispatch, charge, state['segments'][index + 1]['dispatch'],
                receipt, context, operation)
            conservative += charge['seconds']
            continue
        resource_path = root / (prefix + '_RESOURCE.json')
        resource = original._json(resource_path)
        _require(charge['basis'] in ('MEASURED_ACTIVE_WALL_SECONDS', 'MEASURED_FAILED_RESOURCE_BOUND_VIOLATION')
            and original._sha(resource_path) == charge['evidence_identity'], 'SEGMENT_RESOURCE_RECEIPT_CONFLICT')
        original._same(resource['elapsed_wall_seconds'], charge['seconds'], 'SEGMENT_MEASUREMENT_CONFLICT', 1e-6)
        if charge['outcome'] == 'FAILED':
            _require(name == context.recovery.original_failed_account and number == count == FAILED_SEGMENT_COUNT
                and boundary['parent_operation']['segments'][index]['outcome'] == 'FAILED'
                and resource.get('dispatch_id') == dispatch['dispatch_id']
                and (resource.get('returncode') != 0 or resource.get('timed_out') is True),
                'FAILED_SEGMENT_NOT_ORIGINAL_BOUNDARY')
            historical_failed += charge['seconds']; measured += charge['seconds']
            continue
        _require(charge['basis'] == 'MEASURED_ACTIVE_WALL_SECONDS'
            and type(resource.get('returncode')) is int
            and resource['returncode'] == (0 if charge['outcome'] == 'COMPLETED' else 75)
            and not resource.get('timed_out') and charge['outcome'] in ('CONTINUE', 'PAUSED', 'COMPLETED'),
            'SEGMENT_WORKER_NOT_SUCCESSFUL')
        worker = original._json(root / (prefix + '_WORKER.json'))
        _require(all(value.get('dispatch_id') == dispatch['dispatch_id'] and value.get('segment_number') == number
            for value in (resource, worker)), 'SEGMENT_WORKER_IDENTITY_CONFLICT')
        access_path = root / (prefix + '_INPUT_ACCESS.json')
        if number > count:
            runner._validate_recovery_segment_sidecars(root, prefix, dispatch, context, name, require_startup=True)
            startup = original._json(root / (prefix + '_STARTUP.json'))
            _require(type(startup.get('validation_complete')) is bool
                and (charge['outcome'] != 'COMPLETED' or startup['validation_complete']), 'STARTUP_NOT_VALIDATED')
            if not access_path.exists():
                _require(startup['validation_complete'] is False and charge['outcome'] in ('CONTINUE', 'PAUSED'),
                    'INPUT_ACCESS_REQUIRED')
                measured += charge['seconds']
                continue
        access = original._json(access_path)
        _require(access.get('dispatch_id') == dispatch['dispatch_id'] and access.get('segment_number') == number,
            'SEGMENT_INPUT_IDENTITY_CONFLICT')
        _input_access(job, name, resource, worker, access, context.segment_proof(name, number))
        measured += charge['seconds']
    context._require_aggregate_authority(authority)
    return {'charged_seconds': state['charged_seconds'], 'measured_seconds': measured,
        'conservatively_charged_seconds': conservative, 'historical_failed_seconds': historical_failed}


def verify_failed_account_recovery_evidence(job_path, *, name, audit_checkpoint_path=None,
                                          segment_seconds=None, segment_deadline=None):
    """只为已安装的受 Owner 签名接续适配治理证明，原经济重建与输入定义不变。"""
    binding = {'governance_evidence_adapter': ADAPTER_VERSION}
    try:
        _require(segment_deadline is None or type(segment_deadline) in (int, float)
            and math.isfinite(segment_deadline), 'DEADLINE_INVALID')
        with _startup_boundary(segment_deadline) as startup:
            from scripts import run_strategy_account_v1 as runner
            root = Path(job_path).resolve().parent
            _require(Path(job_path).absolute() == root / 'JOB.json', 'JOB_PATH_CONFLICT')
            job_digest = original._sha(job_path)
            job = original._json(job_path)
            _require(Path(job['root']) == root, 'JOB_ROOT_CONFLICT')
            context = _fresh_context(job, name)
            original_checkpoint = _original_failed_prefix(context)
            plan, item = job['plans'][name], job['items'][name]
            _require(plan['plan_id'] == stable_hash({key: value for key, value in plan.items() if key != 'plan_id'})
                and item == plan['runtime'] and plan['backend']['backend'] == 'UNIVERSE_ACCOUNT_BACKEND_V2', 'PLAN_CONFLICT')
            execution_plan = context.active_plan(name); version = context.proof(name); proof = context.recovery.proof()
            binding.update(account_version_continuation=version, failed_account_recovery_proof=proof)
            qualified = item['loader'] == 'chanlun_trader.research_factory.strategy_submission_v1:load_frozen_qualified_bundle'
            _require(qualified or item['loader'] == 'chanlun_trader.research_factory.strategy_submission_v1:load_frozen_bundle',
                'OFFLINE_LOADER_UNSUPPORTED')
            from .universe_submission_v1 import validate_frozen_universe_scopes
            validate_frozen_universe_scopes(job, include_archives=True)
            receipt = original._json(root / 'CONFIRMATION.json')
            _require(receipt['receipt_id'] == stable_hash({key: value for key, value in receipt.items() if key != 'receipt_id'})
                and receipt['strategy_plans'] == job['plans'] and receipt['objective_id'] == job['objective_id']
                and receipt['input_identity'] == job['input_identity'] and receipt['budget_path'] == job['budget_path'],
                'RECEIPT_JOB_CONFLICT')
            _require(receipt['novelty'][name]['allowed'] is True and receipt['novelty'][name]['plan_id'] == plan['plan_id'],
                'NOVELTY_BINDING_CONFLICT')
            source = receipt['source']
            _require(source.get('origin') == 'CAMPAIGN_V1', 'CAMPAIGN_SOURCE_REQUIRED')
            from .etf_account_governance_v1 import validate_campaign_source
            campaign = validate_campaign_source(source, job['plans'], job['objective_id'])
            operation = campaign.status()['operations'][source['operation_ids'][name]]
            paths = runner.account_execution_paths(job, name)
            settlement = runner.validated_settlement(job, name)
            start = original._json(paths['start']); resource = original._json(paths['resource'])
            original_start = original._json(root / (name + '_START.json'))
            _require(original_start['kind'] == name and original_start['receipt_id'] == receipt['receipt_id']
                and original_start['counted_before_account_calculation'] is True, 'ORIGINAL_START_BINDING_CONFLICT')
            _require(start['kind'] == name and start['receipt_id'] == receipt['receipt_id']
                and all(settlement.get(key) == value for key, value in start.items())
                and settlement.get('completed') is True and settlement.get('error') is None, 'SETTLEMENT_CONFLICT')
            original_start_deadline = (receipt['expires_at'] if name == context.recovery.original_failed_account
                else context.recovery.execution_expires_at)
            _require(pd.Timestamp(receipt['recorded_at']) <= pd.Timestamp(original_start['started_at'])
                < pd.Timestamp(original_start_deadline)
                and pd.Timestamp(original_start['started_at']) <= pd.Timestamp(start['started_at'])
                < pd.Timestamp(context.recovery.execution_expires_at)
                and pd.Timestamp(start['started_at']) <= pd.Timestamp(settlement['settled_at']), 'AUTHORIZATION_TIME_CONFLICT')
            if name == context.recovery.original_failed_account:
                _require(start.get('original_start_sha256') == original._sha(root / (name + '_START.json'))
                    and start.get('original_settlement_sha256') == original._sha(root / (name + '_SETTLEMENT.json'))
                    and start.get('reuses_original_consumed_purpose') is True
                    and start.get('failed_account_recovery_proof') == proof, 'RECOVERY_START_CONFLICT')
            resource_layer = _verify_segment_resources(job, name, resource, settlement, context, operation)
            _require(type(resource.get('returncode')) is int and resource['returncode'] == 0
                and not resource.get('timed_out'), 'AGGREGATE_WORKER_NOT_SUCCESSFUL')
            worker, access = original._json(paths['worker']), original._json(paths['input_access'])
            _input_access(job, name, resource, worker, access, version)
            _require(all(value.get('failed_account_recovery_proof') == proof for value in (resource, worker, access)),
                'AGGREGATE_RECOVERY_PROOF_CONFLICT')
            budget = SearchBudgetRegistryV1(job['objective_id'], job['budget_path']).snapshot()
            _require(budget['settled_reservations'].get(original_start['reservation']) == 'CONSUMED', 'BUDGET_NOT_CONSUMED')
            buckets = [row for row in budget['buckets'] if row['kind'] == 'strategy_interface_account_v1'
                and row['key'] == receipt['receipt_id'] + ':' + name]
            _require(len(buckets) == 1 and buckets[0]['used'] == 1 and buckets[0]['reserved'] == 0, 'BUDGET_BUCKET_CONFLICT')
            expected_sources = {**item['source_hashes'], **plan['strategy']['source_hashes'], **plan['backend']['source_hashes']}
            _require(expected_sources and all(job['source_hashes'].get(path) == digest
                for path, digest in expected_sources.items()), 'SOURCE_COVERAGE_CONFLICT')
            for path, digest in job['source_hashes'].items():
                _require(original._sha(root / 'source-archive' / (digest + '_' + Path(path).name)) == digest,
                    'SOURCE_ARCHIVE_CONFLICT')
            result_path, digest, index_path, index_digest = _result_binding(root, name, paths, settlement)
            from .strategy_submission_v1 import load_frozen_bundle, load_frozen_qualified_bundle
            _remaining(segment_deadline, segment_seconds)
            loaded = (load_frozen_qualified_bundle if qualified else load_frozen_bundle)(**item['loader_kwargs'])
            frozen = original._json(item['loader_kwargs']['path'])
            _require(loaded['input_identity'] == job['input_identity'] and frozen['window'] == plan['backend']['window'],
                'FROZEN_INPUT_SCOPE_CONFLICT')
            result = original._json(result_path)
            _require(result['strategy_plan'] == execution_plan and result['input_identity'] == job['input_identity']
                and result.get('account_version_continuation') == version
                and result.get('execution_description') == execution_plan['backend'], 'RESULT_PLAN_OR_VERSION_CONFLICT')
            if name == context.recovery.original_failed_account:
                _require(result['artifacts']['days'][:COMMITTED_DAYS] == original_checkpoint['artifacts']['days'],
                    'RESULT_ORIGINAL_DAY_PREFIX_CHANGED')
            from .universe_execution_artifacts_v1 import hydrated_result
            hydrated = hydrated_result(result)
            checkpoint = Path(item['backend_options']['checkpoint_path'])
            if startup is not None:
                startup.completed()
            evidence_files = {path: original._sha(path) for path in (*paths.values(), index_path)}
        audit = reconstruct_universe_account(loaded['frame'], frozen['window'], hydrated,
            initial_cash=plan['backend']['initial_cash'], costs=plan['backend']['costs'], strategy_id=name,
            rule=plan['strategy']['parameters'].get('candidate_payload'),
            audit_checkpoint_path=audit_checkpoint_path or checkpoint.parent / (name + '_AUDIT.json'),
            segment_seconds=_remaining(segment_deadline, segment_seconds))
        with _startup_boundary(segment_deadline) as final_boundary:
            fresh = _fresh_context(job, name)
            _require(fresh.proof(name) == version and fresh.recovery.proof() == proof
                and original._sha(result_path) == digest and original._sha(job_path) == job_digest
                and original._sha(index_path) == index_digest
                and all(original._sha(path) == expected for path, expected in evidence_files.items()),
                'EVIDENCE_CHANGED_DURING_AUDIT')
            if final_boundary is not None:
                final_boundary.completed()
        return {'version': original.VERSION, 'governance_evidence_adapter': ADAPTER_VERSION,
            'status': 'PASS', 'advance_allowed': True, 'reasons': [], 'job_sha256': job_digest,
            'plan_id': plan['plan_id'], 'active_plan_id': execution_plan['plan_id'], 'result_sha256': digest,
            'input_identity': loaded['input_identity'], 'account_version_continuation': version,
            'failed_account_recovery_proof': proof, 'resource_accounting': resource_layer,
            'account_audit': audit, 'data_qualification': frozen.get('qualification'),
            'evidence_layers': {'account_reconciled': True, 'input_profile': loaded['frame']['profile'],
                'real_data_validity': 'NOT_ESTABLISHED_BY_ACCOUNT_RECONCILIATION', 'historical_screen': 'NOT_ASSESSED',
                'independent_validation': 'NOT_ASSESSED', 'formal_qualification': False}}
    except (FileNotFoundError, KeyError) as exc:
        return {'version': original.VERSION, **binding, 'status': 'INCOMPLETE', 'advance_allowed': False, 'reasons': [str(exc)]}
    except (ValueError, TypeError, PermissionError, OSError, BudgetLedgerMismatchError, FinalTestAccessViolation) as exc:
        return {'version': original.VERSION, **binding, 'status': 'FAIL', 'advance_allowed': False, 'reasons': [str(exc)]}
