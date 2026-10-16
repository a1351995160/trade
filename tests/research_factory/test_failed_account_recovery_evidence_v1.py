"""接续治理适配单元；装配替身不代替完整 Owner/504日真实公共链认证。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory import failed_account_recovery_evidence_v1 as adapter
from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
from chanlun_trader.research_factory.failed_account_recovery_v1 import CANONICAL_SECONDS
from scripts import run_strategy_account_v1 as runner
from test_failed_account_recovery_capability_v1 import extension_case, freeze_recovery, recovery_case


def write(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


def installed_context(case):
    owner, _, _ = freeze_recovery(case)
    case['campaign'].install_failed_account_recovery(owner.scope_reference)
    case['gov'].reconcile_segment_mirrors('ARTIFICIAL_BASE')
    return adapter._fresh_context(case['job'], 'ARTIFICIAL_BASE')


def test_frozen_owner_permission_alone_does_not_make_recovery_evidence_valid(recovery_case):
    case = recovery_case
    freeze_recovery(case)
    with pytest.raises(ValueError, match='INSTALLED_OWNER_CONTEXT_REQUIRED'):
        adapter._fresh_context(case['job'], 'ARTIFICIAL_BASE')


def test_fresh_owner_context_preserves_original_failed_files_and_day_prefix(recovery_case):
    case = recovery_case
    context = installed_context(case)
    name = context.recovery.original_failed_account
    paths = [case['account'] / (name + '_SETTLEMENT.json'),
        case['account'] / (name + '_SEGMENT_000050_RESOURCE.json'),
        case['account'] / (name + '_SEGMENT_000050_CHARGE.json')]
    before = {path: path.read_bytes() for path in paths}
    checkpoint = adapter._original_failed_prefix(context)
    assert len(checkpoint['artifacts']['days']) == 277
    assert context.recovery.proof() == context.proof(name)['failed_account_recovery']
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert runner.account_execution_paths(case['job'], name)['settlement'].name == name + '_RECOVERY_SETTLEMENT.json'


@pytest.mark.parametrize('suffix', ['_SETTLEMENT.json', '_SEGMENT_000050_RESOURCE.json', '_SEGMENT_000050_CHARGE.json'])
def test_adapter_rechecks_original_failure_references_instead_of_trusting_cached_context(recovery_case, suffix):
    case = recovery_case
    context = installed_context(case)
    target = case['account'] / ('ARTIFICIAL_BASE' + suffix)
    target.write_bytes(target.read_bytes() + b'\n')
    with pytest.raises((ValueError, PermissionError), match='ORIGINAL'):
        adapter._original_failed_prefix(context)


def test_arbitrary_context_or_plain_failed_path_cannot_grant_verification(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    write(root / 'JOB.json', {'root': str(root)})
    monkeypatch.setattr(AccountVersionContinuationV1, 'from_job', lambda job: {'recovery': True})
    monkeypatch.setattr(adapter, 'reconstruct_universe_account', lambda *args, **kwargs: pytest.fail('无Owner能力不得核账'))
    result = adapter.verify_failed_account_recovery_evidence(root / 'JOB.json', name='ARTIFICIAL_BASE')
    assert result['status'] == 'FAIL' and result['advance_allowed'] is False
    assert result['reasons'] == ['FAILED_ACCOUNT_RECOVERY_EVIDENCE_OWNER_CONTEXT_REQUIRED']


def assembled_segment_evidence(tmp_path):
    """仅隔离测试资源包装断言；父重放/Owner认证由真实完整fixture另行覆盖。"""
    root = tmp_path.resolve(); name = 'ARTIFICIAL_BASE'
    proof = {'manifest_ref': {'path': 'DECLARED_ASSEMBLY_ONLY', 'sha256': '1' * 64}, 'recovery_id': '2' * 64}
    time_proof = {'manifest_ref': {'path': 'DECLARED_PREDECESSOR', 'sha256': '3' * 64}, 'extension_id': '4' * 64}
    version = {'version_id': 'RECOVERED', 'failed_account_recovery': proof, 'account_purpose_time_extension': time_proof}

    def version_at(number):
        if number <= 6:
            return None
        if number <= 34:
            return {'version_id': 'PREVIOUS'}
        if number <= 50:
            return {'version_id': 'EXTENDED', 'account_purpose_time_extension': time_proof}
        return deepcopy(version)

    receipt = {'receipt_id': 'ASSEMBLED_RECEIPT', 'source': {'origin': 'CAMPAIGN_V1'}}
    write(root / 'CONFIRMATION.json', receipt)
    job = {'root': str(root), 'input_identity': 'ASSEMBLED_INPUT',
        'items': {name: {'loader': 'trusted:loader', 'loader_kwargs': {'artificial': True}}}}
    rows, parents = [], []
    charged = 0.
    regular_seconds = (CANONICAL_SECONDS - 890.2199829000001 - 1800.) / 47
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for number in range(1, 52):
        dispatch = {'kind': name, 'segment_number': number, 'dispatch_id': 'ASSEMBLED_DISPATCH_' + str(number),
            'upper_bound_seconds': 900., 'dispatched_at': (start + timedelta(seconds=2 * number)).isoformat()}
        if number >= 35:
            dispatch['time_extension_proof'] = time_proof
        if number == 51:
            dispatch.update(failed_account_recovery_proof=proof, dispatch_identity='5' * 64)
        prefix = runner.segment_prefix(name, number)
        unknown = number in (17, 27)
        seconds = 900. if unknown else regular_seconds if number < 50 else CANONICAL_SECONDS - charged if number == 50 else 3.
        outcome = 'COMPLETED' if number == 51 else 'FAILED' if number == 50 else 'CONTINUE'
        fields = {'dispatch_id': dispatch['dispatch_id'], 'segment_number': number}
        sidecar_proof = {'account_version_continuation': version_at(number)}
        if number >= 35:
            sidecar_proof['time_extension_proof'] = time_proof
        if number == 51:
            sidecar_proof.update(failed_account_recovery_proof=proof, dispatch_identity=dispatch['dispatch_identity'])
        worker = {'pid': 1000 + number, 'purpose': name, **fields, **sidecar_proof,
            'fixture_only': 'ASSEMBLED_RESOURCE_CHECKS_NOT_REAL_WORKER'}
        write(root / (prefix + '_WORKER.json'), worker)
        if unknown:
            write(root / (prefix + '_RESUME.json'), {'dispatch_id': dispatch['dispatch_id'], 'budget_reused': True,
                'charge_basis': 'UNKNOWN_UPPER_BOUND', 'state_identity': 'a' * 64,
                'resumed_at': (start + timedelta(seconds=2 * number + 1)).isoformat(), **sidecar_proof})
            resource_digest = None
        else:
            resource = {'elapsed_wall_seconds': seconds, 'returncode': 0 if number == 51 else 1 if number == 50 else 75,
                'timed_out': number == 50, **fields, **sidecar_proof,
                'fixture_only': 'ASSEMBLED_RESOURCE_CHECKS_NOT_REAL_MEASUREMENT'}
            resource_path = root / (prefix + '_RESOURCE.json')
            write(resource_path, resource); resource_digest = adapter.original._sha(resource_path)
            write(root / (prefix + '_INPUT_ACCESS.json'), {'reader_pid': worker['pid'], 'purpose': name,
                'input_identity': job['input_identity'], **job['items'][name], **fields, **sidecar_proof})
        if number == 51:
            write(root / (prefix + '_STARTUP.json'), {'validation_complete': True, **fields, **sidecar_proof})
            write(root / (prefix + '_STATUS.json'), {'state': 'COMPLETED', **fields, **sidecar_proof})
        charge = {'charge_id': 'ASSEMBLED_FEE_' + str(number), 'seconds': seconds,
            'measured_seconds': None if unknown else seconds,
            'basis': 'UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND' if unknown else 'MEASURED_ACTIVE_WALL_SECONDS',
            'evidence_identity': resource_digest, 'outcome': outcome}
        rows.append({'dispatch': dispatch, 'charge': charge})
        parents.append({'operation_id': 'BASE', 'segment_number': number, 'outcome': outcome})
        charged += seconds
    boundary = {'segment_count': 50, 'parent_operation': {'segments': deepcopy(parents[:50])}}
    state = {'pending': None, 'segments': rows, 'charged_seconds': charged, 'failed_account_recovery_proof': proof}
    operation = {'operation_id': 'BASE', 'status': 'FAILED', 'segments': parents,
        'failed_account_recovery': {'status': 'COMPLETED'}}
    validated = []; authority = {'sha256': 'f' * 64}
    context = SimpleNamespace(
        recovery=SimpleNamespace(original_failed_account=name, proof=lambda: deepcopy(proof),
            summary_value={'accounts': {name: boundary}}),
        governance=SimpleNamespace(segment_status=lambda selected: state), proof=lambda selected: deepcopy(version),
        segment_proof=lambda selected, number: version_at(number),
        _authority_ledger_reference=lambda: deepcopy(authority),
        _require_aggregate_authority=lambda current: current == authority or pytest.fail('authority changed'),
        _validate_dispatch_from_state=lambda selected, number, current: validated.append(number))
    aggregate = {'elapsed_wall_seconds': charged, 'active_metering': True, 'segment_count': len(rows),
        'segments': [row['charge']['charge_id'] for row in rows]}
    return job, context, operation, aggregate, {'wall_seconds': charged}, validated


def test_resource_adapter_keeps_original_failed_fee_and_unknown_upper_bounds(tmp_path):
    job, context, operation, aggregate, settlement, validated = assembled_segment_evidence(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.glob('ARTIFICIAL_BASE_SEGMENT_000050_*.json')}
    result = adapter._verify_segment_resources(job, 'ARTIFICIAL_BASE', aggregate, settlement, context, operation)
    assert validated == list(range(1, 52))
    assert result['conservatively_charged_seconds'] == 1800.
    assert result['historical_failed_seconds'] > 0
    assert result['measured_seconds'] + result['conservatively_charged_seconds'] == pytest.approx(result['charged_seconds'])
    assert all(path.read_bytes() == raw for path, raw in before.items())


@pytest.mark.parametrize('change', ['original_resource', 'original_failure', 'new_proof', 'new_dispatch_identity',
    'new_startup', 'new_timeout', 'new_input', 'new_status', 'unknown_resource', 'unknown_resume', 'aggregate', 'second_failure'])
def test_resource_adapter_rejects_tampered_recovery_evidence(tmp_path, change):
    job, context, operation, aggregate, settlement, _ = assembled_segment_evidence(tmp_path)
    root = Path(job['root']); prefix = 'ARTIFICIAL_BASE_SEGMENT_000051'
    if change == 'aggregate':
        aggregate['segments'] = aggregate['segments'][:-1]
    elif change == 'second_failure':
        context.governance.segment_status('ARTIFICIAL_BASE')['segments'][-1]['charge']['outcome'] = 'FAILED'
    elif change in ('unknown_resource', 'unknown_resume'):
        target = root / ('ARTIFICIAL_BASE_SEGMENT_000017' + ('_RESOURCE.json' if change == 'unknown_resource' else '_RESUME.json'))
        if change == 'unknown_resource':
            write(target, {'elapsed_wall_seconds': 0})
        else:
            value = adapter.original._json(target); value['budget_reused'] = False; write(target, value)
    elif change in ('original_resource', 'original_failure'):
        target = root / 'ARTIFICIAL_BASE_SEGMENT_000050_RESOURCE.json'
        if change == 'original_resource':
            target.write_bytes(target.read_bytes() + b'\n')
        else:
            value = adapter.original._json(target); value.update(returncode=0, timed_out=False); write(target, value)
            context.governance.segment_status('ARTIFICIAL_BASE')['segments'][49]['charge']['evidence_identity'] = adapter.original._sha(target)
    else:
        suffix = '_RESOURCE.json' if change in ('new_timeout', 'new_dispatch_identity') else '_INPUT_ACCESS.json' if change == 'new_input' else '_STATUS.json' if change == 'new_status' else '_STARTUP.json'
        target = root / (prefix + suffix); value = adapter.original._json(target)
        if change == 'new_proof':
            value['failed_account_recovery_proof']['recovery_id'] = '0' * 64
        elif change == 'new_dispatch_identity':
            value['dispatch_identity'] = '0' * 64
        elif change == 'new_startup':
            value['validation_complete'] = False
        elif change == 'new_timeout':
            value['timed_out'] = True
        elif change == 'new_input':
            value['loader_kwargs'] = {'different': True}
        else:
            value['dispatch_id'] = 'different'
        write(target, value)
    with pytest.raises((ValueError, PermissionError)):
        adapter._verify_segment_resources(job, 'ARTIFICIAL_BASE', aggregate, settlement, context, operation)


@pytest.mark.parametrize('change', [None, 'result', 'index_hash', 'old_settlement_path'])
def test_result_index_selects_only_independent_recovery_settlement(tmp_path, change):
    root = tmp_path.resolve(); name = 'ARTIFICIAL_BASE'
    result_path = root / (name + '_RESULT.json'); write(result_path, {'fixture_only': True})
    recovery_path = root / (name + '_RECOVERY_SETTLEMENT.json'); write(recovery_path, {'completed': True})
    original_path = root / (name + '_SETTLEMENT.json'); write(original_path, {'completed': False})
    original_bytes = original_path.read_bytes()
    digest = adapter.original._sha(result_path); settlement = {'result_sha256': digest}
    row = {'result': str(result_path), 'settlement': str(recovery_path), 'sha256': digest}
    if change == 'result':
        result_path.write_bytes(result_path.read_bytes() + b'\n')
    elif change == 'index_hash':
        row['sha256'] = '0' * 64
    elif change == 'old_settlement_path':
        row['settlement'] = str(original_path)
    write(root / 'RESULTS_INDEX.json', {'items': {name: row}})
    if change is None:
        assert adapter._result_binding(root, name, {'settlement': recovery_path}, settlement)[1] == digest
    else:
        with pytest.raises(ValueError, match='RESULT_HASH_BINDING_CONFLICT'):
            adapter._result_binding(root, name, {'settlement': recovery_path}, settlement)
    assert original_path.read_bytes() == original_bytes


def economic_case(tmp_path):
    from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3
    from chanlun_trader.research_factory.universe_account_backend_v2 import UniverseAccountBackendV2
    from chanlun_trader.research_factory.universe_account_inputs_v1 import universe_input_identity_v1
    from chanlun_trader.research_factory.universe_execution_artifacts_v1 import hydrated_result
    from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile
    from universe_test_fixture_v1 import fixture, proposal
    window, bundle = fixture(days_count=64)
    strategy = ResearchRuleStrategyV3(proposal(), strategy_id='ARTIFICIAL_AUDIT')
    backend = UniverseAccountBackendV2(window, execution_profile=execution_profile(SEGMENTED_PROFILE, 4),
        checkpoint_path=str(tmp_path / 'economic' / 'CLOSE.json'), max_positions=2, max_symbol_exposure_bps=5000)
    result = backend.run(strategy, bundle, bundle['events'],
        lambda: {'input_identity': universe_input_identity_v1(bundle, window)})
    return bundle, window, hydrated_result(result), backend, strategy


def test_economic_audit_is_the_original_function_with_identical_inputs_and_output(tmp_path):
    from chanlun_trader.research_factory.universe_evidence_v1 import reconstruct_universe_account
    bundle, window, result, backend, strategy = economic_case(tmp_path)
    assert adapter.reconstruct_universe_account is reconstruct_universe_account
    options = {'initial_cash': backend.initial_cash, 'costs': backend.costs, 'strategy_id': strategy.strategy_id,
        'rule': strategy.payload, 'audit_checkpoint_path': tmp_path / 'original_AUDIT.json', 'segment_seconds': None}
    expected = reconstruct_universe_account(bundle, window, result, **options)
    actual = adapter.reconstruct_universe_account(bundle, window, result,
        **{**options, 'audit_checkpoint_path': tmp_path / 'adapter_AUDIT.json'})
    assert actual == expected


def test_original_economic_reconstructor_still_rejects_forged_metrics(tmp_path):
    bundle, window, result, backend, strategy = economic_case(tmp_path)
    forged = {**result, 'metrics': {**result['metrics'], 'net_return': result['metrics']['net_return'] + 1}}
    with pytest.raises(ValueError):
        adapter.reconstruct_universe_account(bundle, window, forged, initial_cash=backend.initial_cash,
            costs=backend.costs, strategy_id=strategy.strategy_id, rule=strategy.payload,
            audit_checkpoint_path=tmp_path / 'forged_AUDIT.json', segment_seconds=None)


def test_expired_verification_deadline_propagates_cooperative_boundary_before_owner_or_prices(tmp_path, monkeypatch):
    from chanlun_trader.research_factory.universe_account_backend_v2 import SegmentBoundary
    monkeypatch.setattr(adapter.time, 'monotonic', lambda: 10.)
    monkeypatch.setattr(adapter, '_fresh_context', lambda *args: pytest.fail('截止后不能访问Owner或价格'))
    with pytest.raises(SegmentBoundary, match='UNIVERSE_VERIFICATION_COOPERATIVE_DEADLINE'):
        adapter.verify_failed_account_recovery_evidence(tmp_path / 'JOB.json', name='ARTIFICIAL_BASE', segment_deadline=1.)
