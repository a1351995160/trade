"""原合法277日/50FAILED人工Owner契约上的等价与I/O证据，不派发worker。"""
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from chanlun_trader.account_startup_boundary_v1 import AccountStartupBoundaryV1
from chanlun_trader.research_factory import failed_account_recovery_v1 as recovery
from chanlun_trader.research_factory import secure_file_reference_v1 as secure
from chanlun_trader.research_factory import continuous_research_contract_v1 as contracts
from test_account_purpose_time_extension_v1 import extension_case
from test_failed_account_recovery_capability_v1 import recovery_case, freeze_recovery


@pytest.fixture(scope='module')
def fresh_reader_context(tmp_path_factory):
    """仅构造本读取器所需的Owner；对照可显式复用已生成的同类人工夹具。"""
    supplied = os.environ.get('RECOVERY_READ_BENCHMARK_ARTIFICIAL_ROOT')
    if supplied:
        root = Path(supplied)
        assert root.name.startswith('fresh-recovery-readers')
        manifests = list((root / 'reports/tasks').glob('*/failed-account-recoveries/*/MANIFEST.json'))
        assert len(manifests) == 1
        raw = manifests[0].read_bytes()
        value = json.loads(raw)
        assert value['summary']['original_failed_account'] == 'ARTIFICIAL_BASE'
        assert set(value['summary']['accounts']) == {'ARTIFICIAL_BASE', 'ARTIFICIAL_STRESS'}
        current = recovery.bridges.capabilities()
        current['full_universe']['source_hashes']['universe_account_inputs_v1.py'] = hashlib.sha256(
            (root / recovery.bridges.BUSINESS_PATH).read_bytes()).hexdigest()
        recovery.bridges._fingerprint(current)
        assert current['fingerprint'] == value['summary']['live_capabilities_fingerprint']
        # 与原extension_case相同的人工业务实现投影；不从摘要伪造能力或放松验证。
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(contracts, 'capabilities', lambda: deepcopy(current))
            patch.setattr(recovery.bridges, 'capabilities', lambda: deepcopy(current))
            owner = recovery.FailedAccountRecoveryV1.read(root, {
                'path': str(manifests[0]), 'sha256': hashlib.sha256(raw).hexdigest()})
            assert owner.root == root
            directory = root / 'reports/research_campaigns' / recovery.stable_hash(owner.summary_value['authorization_id'])
            yield {'root': root, 'account': owner.job_path.parent, 'job': owner.job,
                'campaign': SimpleNamespace(budget_path=directory / 'run_budget.json'), 'recovery_owner': owner}
        return
    with pytest.MonkeyPatch.context() as patch:
        case = extension_case.__wrapped__(tmp_path_factory.mktemp('fresh-recovery-readers'),
            patch, SimpleNamespace(param={}))
        case = recovery_case.__wrapped__(case)
        owner, _, _ = freeze_recovery(case)
        case['recovery_owner'] = owner
        yield case


def _result(context):
    return {'summary': context.summary_value, 'proof': context.proof(),
        'expires_at': context.execution_expires_at, 'approval': context.approval_ref}


@pytest.mark.parametrize('traced', (False, True))
def test_original_read_body_and_scoped_read_have_equal_results_and_fewer_opens(
        fresh_reader_context, monkeypatch, tmp_path, traced):
    case = fresh_reader_context
    original = recovery.FailedAccountRecoveryV1.read.__func__.__wrapped__
    real_reader = secure._regular_reader
    rows = []
    outputs = []
    before = {p: p.read_bytes() for p in case['account'].rglob('*') if p.is_file()}
    for optimized in (False, True):
        opened = []

        def counted(*args, **kwargs):
            opened.append(str(args[0]))
            return real_reader(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(secure, '_regular_reader', counted)
            boundary = AccountStartupBoundaryV1(time.monotonic() + 830) if traced else None
            began = time.perf_counter()
            with boundary.scope() if boundary is not None else nullcontext():
                if optimized:
                    context = recovery.FailedAccountRecoveryV1.read(case['root'], case['recovery_owner'].scope_reference)
                else:
                    context = original(recovery.FailedAccountRecoveryV1,
                        case['root'], case['recovery_owner'].scope_reference)
            elapsed = time.perf_counter() - began
        outputs.append(_result(context))
        rows.append({'optimized': optimized, 'traced': traced, 'elapsed_seconds': elapsed,
            'regular_reader_opens': len(opened), 'unique_paths': len(set(opened))})
    assert outputs[0] == outputs[1]
    assert rows[1]['regular_reader_opens'] < rows[0]['regular_reader_opens']
    assert {p: p.read_bytes() for p in case['account'].rglob('*') if p.is_file()} == before
    assert not (case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000051_DISPATCH.json').exists()
    result = {'fixture_only': 'ARTIFICIAL_277_DAYS_50_FAILED_OWNER_READ_NO_WORKER',
        'baseline': 'SAME_ORIGINAL_ASSERTION_BODY_WITHOUT_READ_VIEW', 'rows': rows,
        'equal_summary_proof_expiry_and_approval': True, 'changed_account_files': [], 'dispatched_segments': 0}
    (tmp_path / 'READ_EQUIVALENCE_BENCHMARK.json').write_text(
        json.dumps(result, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))


@pytest.mark.parametrize('mutation', ('owner', 'source', 'charge', 'day', 'ledger'))
def test_each_read_rejects_changed_originals_with_and_without_reuse(
        fresh_reader_context, mutation):
    case = fresh_reader_context
    owner = case['recovery_owner']
    target = (Path(owner.summary_value['approval_store']) / (owner.approval_ref['approval_id'] + '.json')
        if mutation == 'owner' else case['root'] / 'scripts/run_strategy_account_v1.py'
        if mutation == 'source' else case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000050_CHARGE.json'
        if mutation == 'charge' else next((case['account'] / 'ARTIFICIAL_BASE_DAYS').glob('*.json.gz'))
        if mutation == 'day' else case['campaign'].budget_path.with_name('run_budget_events.jsonl'))
    raw = target.read_bytes()
    original = recovery.FailedAccountRecoveryV1.read.__func__.__wrapped__
    try:
        target.unlink() if mutation == 'owner' else target.write_bytes(raw + b'\n')
        for read in (lambda: original(recovery.FailedAccountRecoveryV1, case['root'], owner.scope_reference),
                lambda: recovery.FailedAccountRecoveryV1.read(case['root'], owner.scope_reference)):
            with pytest.raises((PermissionError, ValueError)):
                read()
    finally:
        target.write_bytes(raw)
    assert not (case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000051_DISPATCH.json').exists()


@pytest.mark.parametrize('mutation', ('owner', 'job', 'ledger'))
def test_return_boundary_rejects_mutation_of_reused_json_originals(
        fresh_reader_context, monkeypatch, mutation):
    case = fresh_reader_context
    owner = case['recovery_owner']
    target = (Path(owner.summary_value['approval_store']) / (owner.approval_ref['approval_id'] + '.json')
        if mutation == 'owner' else case['account'] / 'JOB.json'
        if mutation == 'job' else case['campaign'].budget_path.with_name('run_budget_events.jsonl'))
    raw = target.read_bytes()
    original = recovery._session_boundary
    fired = []

    def change_after_checks(*args, **kwargs):
        value = original(*args, **kwargs)
        if not fired:
            fired.append(True)
            target.unlink() if mutation == 'owner' else target.write_bytes(raw + b'\n')
        return value

    monkeypatch.setattr(recovery, '_session_boundary', change_after_checks)
    try:
        with pytest.raises((PermissionError, ValueError)):
            recovery.FailedAccountRecoveryV1.read(case['root'], owner.scope_reference)
        assert fired
    finally:
        target.write_bytes(raw)
    assert not (case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000051_DISPATCH.json').exists()


@pytest.mark.parametrize('mutation', ('source', 'charge', 'day'))
def test_hash_only_references_keep_original_check_position_and_next_call_is_fresh(
        fresh_reader_context, monkeypatch, mutation):
    case = fresh_reader_context
    owner = case['recovery_owner']
    target = (case['root'] / 'scripts/run_strategy_account_v1.py' if mutation == 'source'
        else case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000050_CHARGE.json' if mutation == 'charge'
        else next((case['account'] / 'ARTIFICIAL_BASE_DAYS').glob('*.json.gz')))
    raw = target.read_bytes()
    boundary = recovery._session_boundary
    body = recovery.FailedAccountRecoveryV1.read.__func__.__wrapped__
    outputs = []
    for read in (lambda: body(recovery.FailedAccountRecoveryV1, case['root'], owner.scope_reference),
            lambda: recovery.FailedAccountRecoveryV1.read(case['root'], owner.scope_reference)):
        fired = []

        def change_after_last_hash(*args, **kwargs):
            value = boundary(*args, **kwargs)
            if not fired:
                target.write_bytes(raw + b'\n')
                fired.append(True)
            return value

        try:
            with monkeypatch.context() as patch:
                patch.setattr(recovery, '_session_boundary', change_after_last_hash)
                outputs.append(_result(read()))
            assert fired
            with pytest.raises((PermissionError, ValueError)):
                read()
        finally:
            target.write_bytes(raw)
    assert outputs[0] == outputs[1]
    assert not (case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000051_DISPATCH.json').exists()
