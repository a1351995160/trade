"""原期限之后的公开账户治理；人工合法旧历史，不启动经济worker。"""
from datetime import datetime, timedelta
import json
import math

import pytest

from chanlun_trader.research_factory import account_purpose_time_extension_v1 as extension
from chanlun_trader.research_factory import account_version_continuation_v1 as versions
from chanlun_trader.research_factory import etf_account_governance_v1 as governance
from chanlun_trader.research_factory import failed_account_recovery_v1 as recovery
from chanlun_trader.research_factory import research_campaign_v1 as campaigns
from chanlun_trader.research_factory import universe_execution_recovery_v1 as reconciliation
from chanlun_trader.research_factory.run_budget import effective_execution_status
from scripts import run_strategy_account_v1 as runner
from test_account_purpose_time_extension_v1 import control, write
from test_failed_account_calendar_deadline_owner_v1 import calendar_owner_case


def test_expired_original_receipt_needs_installed_calendar_and_new_deadline_is_absolute(calendar_owner_case, monkeypatch):
    case = calendar_owner_case
    old = case['calendar_summary']['expires_at']
    deadline = case['calendar_summary']['calendar_deadline_extension']['expires_at']
    current = datetime.fromisoformat(old) + timedelta(seconds=1)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current.astimezone(tz) if tz else current.replace(tzinfo=None)

    for module in (extension, versions, governance, recovery, campaigns, reconciliation):
        monkeypatch.setattr(module, 'datetime', Clock)
    ledger = case['campaign'].budget_path.with_name('run_budget_events.jsonl')
    original_ledger = ledger.read_bytes()
    receipt_bytes = case['gov'].receipt_path.read_bytes()
    with pytest.raises(PermissionError, match='NOT_INSTALLED'):
        case['gov']._active_receipt()
    assert ledger.read_bytes() == original_ledger
    case['campaign'].install_failed_account_recovery(case['calendar_reference'])
    case['gov'].reconcile_segment_mirrors('ARTIFICIAL_BASE')
    case['campaign'].resume('artificial explicit finite calendar recovery resume')
    control(case, 'RESUME')
    case['gov'].start_recovery('ARTIFICIAL_BASE')
    receipt = case['gov'].active_execution('ARTIFICIAL_BASE')
    assert receipt['expires_at'] == old
    context = versions.AccountVersionContinuationV1.from_job(case['job'], for_dispatch=True)
    assert context.recovery.execution_expires_at == deadline
    assert context.project_receipt(receipt, 'ARTIFICIAL_BASE')['expires_at'] == deadline
    dispatch = case['gov'].start_segment('ARTIFICIAL_BASE')
    assert dispatch['segment_number'] == 51 and 70 < dispatch['upper_bound_seconds'] <= 900
    context.record_dispatch('ARTIFICIAL_BASE', dispatch)
    reference = write(case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000051_RESOURCE.json', {
        'fixture_only': 'ARTIFICIAL_PAID_CONTINUE_NOT_REAL_WORKER_MEASUREMENT',
        'dispatch_id': dispatch['dispatch_id'], 'dispatch_identity': dispatch['dispatch_identity'],
        'elapsed_wall_seconds': .2, 'time_extension_proof': case['extension'].proof(),
        'failed_account_recovery_proof': context.recovery.proof(),
        'account_version_continuation': context.proof('ARTIFICIAL_BASE')})
    case['gov'].end_segment('ARTIFICIAL_BASE', 51, seconds=.2, evidence_identity=reference['sha256'],
        dispatch_identity=dispatch['dispatch_identity'])
    monkeypatch.setattr(runner, 'validate_sources',
        lambda job: versions.AccountVersionContinuationV1.from_job(job).validate_sources(job))
    job_path = case['account'] / 'JOB.json'
    result = reconciliation.resume_long_horizon_job(job_path, case['job'], reconcile_only=True)
    assert result['dispatched_segments'] == 0
    view = case['campaign'].peek_status()
    operation = view['operations']['BASE']
    assert operation['status'] == 'FAILED' and effective_execution_status(operation) == 'RUNNING'
    assert operation['actual']['wall_seconds'] == 42340
    assert not (case['account'] / 'ARTIFICIAL_BASE_RECOVERY_SETTLEMENT.json').exists()
    assert not (case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000052_DISPATCH.json').exists()
    assert case['gov'].receipt_path.read_bytes() == receipt_bytes
    assert ledger.read_bytes().startswith(original_ledger)
    before = ledger.read_bytes()
    current = datetime.fromisoformat(deadline)
    with pytest.raises(PermissionError, match='EXPIRED'):
        case['gov']._active_receipt()
    assert ledger.read_bytes() == before
    assert case['gov'].receipt_path.read_bytes() == receipt_bytes
    # 新期限到了仍可离线核账，但不能新派发；仅结清已付费接续，不触碰原50。
    result = reconciliation.resume_long_horizon_job(job_path, case['job'], reconcile_only=True)
    assert result['dispatched_segments'] == 0
    view = case['campaign'].peek_status()
    operation = view['operations']['BASE']
    assert operation['status'] == effective_execution_status(operation) == 'FAILED'
    assert operation['actual']['wall_seconds'] == 42340
    assert view['used']['wall_seconds'] == math.ceil(operation['active_wall_seconds'])
    settlement = json.loads((case['account'] / 'ARTIFICIAL_BASE_RECOVERY_SETTLEMENT.json').read_text(encoding='utf-8'))
    assert settlement['completed'] is False
    assert not (case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000052_DISPATCH.json').exists()
    assert all(path.read_bytes() == raw for path, raw in case['calendar_unchanged'].items()
               if path not in (ledger, case['gov'].budget_path, case['campaign'].budget_path))
