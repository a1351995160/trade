"""同一权威预算的跨批、恢复、并发和分阶段停止边界。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import shutil

import pytest

from chanlun_trader.research_factory.budget import BudgetExhaustedError, BudgetLedgerMismatchError
from chanlun_trader.research_factory.mutation_boundary import MutationBusyError
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.run_budget import AutonomousRunBudgetV1, AutonomousRunBudgetV2


def authorization():
    return {'authorization_id': 'campaign_one', 'objective_id': 'objective',
            'resource_limits': {name: 10000 for name in AutonomousRunBudgetV2.resource_names},
            'stages': ['EXPLORATION', 'CONFIRMATION'],
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            'max_batches': 3, 'max_total_predictive_trials': 4, 'max_trials_per_batch': 2,
            'max_hypotheses_per_batch': 2, 'max_candidates_per_batch': 2}


def reserve(service, op='op1', batch='batch1', kind='ACCOUNT', stage='EXPLORATION', subject=None):
    resource = {'ACCOUNT': 'account_jobs', 'MODEL': 'model_calls', 'CANDIDATE': 'candidate_attempts'}[kind]
    units = {resource: 1, 'wall_seconds': 20}
    extra = {}
    if kind == 'MODEL':
        units.update(model_tokens=100, model_cost_microunits=20)
        extra['cost_bound_evidence'] = 'trusted_adapter_enforced_limit_receipt'
    return service.reserve_operation(operation_id=op, batch_id=batch, stage=stage, kind=kind,
        subject_identity=subject or op, upper_bounds=units, **extra)


def settle(service, op='op1', kind='ACCOUNT', outcome='COMPLETED'):
    resource = {'ACCOUNT': 'account_jobs', 'MODEL': 'model_calls', 'CANDIDATE': 'candidate_attempts'}[kind]
    actual = {resource: 1, 'wall_seconds': 1}
    if kind == 'MODEL':
        actual.update(model_tokens=50, model_cost_microunits=10)
    return service.settle_operation(op, actual=actual, outcome=outcome, evidence_identity='verified_receipt_' + op)


def test_two_batches_share_limits_and_rebuild_from_events(tmp_path):
    config = authorization()
    config['resource_limits']['account_jobs'] = 2
    service = ResearchCampaignV1.create(tmp_path, config)
    for op, batch in [('one', 'batch1'), ('two', 'batch2')]:
        reserve(service, op, batch)
        service.start_operation(op)
        settle(service, op)
    before = service.status()
    assert before['used']['account_jobs'] == 2 and before['batch_count'] == 2
    service.budget_path.unlink()  # 模拟可重建快照丢失；权威事件仍在。
    restored = ResearchCampaignV1(tmp_path, 'campaign_one')
    assert restored.status()['used'] == before['used']
    assert restored.status()['trial_usage'] == before['trial_usage']
    with pytest.raises(BudgetExhaustedError):
        reserve(restored, 'three', 'batch3')


def test_duplicate_names_do_not_reset_experiment_and_pause_blocks_dispatch(tmp_path):
    service = ResearchCampaignV1.create(tmp_path, authorization())
    original = reserve(service, subject='same_rule_data_cost')
    assert reserve(service, subject='same_rule_data_cost') == original
    with pytest.raises(BudgetLedgerMismatchError, match='DUPLICATE_SUBJECT'):
        reserve(service, 'renamed', 'batch2', subject='same_rule_data_cost')
    service.pause('user pause')
    with pytest.raises(PermissionError, match='PAUSED'):
        service.start_operation('op1')
    with pytest.raises(PermissionError, match='PAUSED'):
        reserve(service, 'new')
    service.resume('explicit resume')
    service.start_operation('op1')
    with pytest.raises(PermissionError, match='ALREADY_DISPATCHED'):
        service.start_operation('op1')


def test_confirmation_wait_does_not_block_exploration(tmp_path):
    service = ResearchCampaignV1.create(tmp_path, authorization())
    reserve(service, 'confirmation', stage='CONFIRMATION')
    service.set_stage('CONFIRMATION', 'WAITING', 'independent data unavailable')
    with pytest.raises(PermissionError, match='STAGE_WAITING'):
        service.start_operation('confirmation')
    reserve(service, 'exploration')
    assert service.start_operation('exploration')['status'] == 'RUNNING'


def test_unknown_model_keeps_upper_bound_and_failed_call_is_not_free(tmp_path):
    service = ResearchCampaignV1.create(tmp_path, authorization())
    reserve(service, kind='MODEL')
    service.start_operation('op1')
    service.mark_unknown('op1', 'provider receipt lost')
    view = ResearchCampaignV1(tmp_path, 'campaign_one').status()
    assert view['reserved']['model_calls'] == 1
    assert view['reserved']['model_tokens'] == 100
    assert view['reserved']['model_cost_microunits'] == 20
    with pytest.raises(PermissionError, match='ALREADY_DISPATCHED'):
        service.start_operation('op1')
    settle(service, kind='MODEL', outcome='FAILED')
    assert service.status()['used']['model_calls'] == 1
    with pytest.raises(ValueError, match='BOUND_EVIDENCE'):
        service.reserve_operation(operation_id='no_bound', batch_id='batch1', stage='EXPLORATION', kind='MODEL',
                                  subject_identity='x', upper_bounds={'model_calls': 1, 'wall_seconds': 1})


def test_invalid_candidate_still_consumes_candidate_attempt(tmp_path):
    service = ResearchCampaignV1.create(tmp_path, authorization())
    reserve(service, kind='CANDIDATE')
    service.start_operation('op1')
    settle(service, kind='CANDIDATE', outcome='FAILED')
    assert service.status()['used']['candidate_attempts'] == 1


def test_event_committed_before_snapshot_crash_cannot_redispatch(tmp_path, monkeypatch):
    service = ResearchCampaignV1.create(tmp_path, authorization())
    reserve(service)
    original = AutonomousRunBudgetV2._persist
    def crash_after_start(self):
        if self._events and self._events[-1]['event_type'] == 'CAMPAIGN_OPERATION_STARTED':
            raise RuntimeError('simulated crash after durable start')
        return original(self)
    with monkeypatch.context() as patch:
        patch.setattr(AutonomousRunBudgetV2, '_persist', crash_after_start)
        with pytest.raises(RuntimeError, match='simulated crash'):
            service.start_operation('op1')
    restored = ResearchCampaignV1(tmp_path, 'campaign_one')
    assert restored.status()['operations']['op1']['status'] == 'RUNNING'
    with pytest.raises(PermissionError, match='ALREADY_DISPATCHED'):
        restored.start_operation('op1')
    assert restored.status()['trial_usage']['remaining_capacity'] == 3


def test_parallel_reservation_does_not_exceed_last_slot(tmp_path):
    config = authorization()
    config['resource_limits']['account_jobs'] = 1
    service = ResearchCampaignV1.create(tmp_path, config)
    def attempt(number):
        try:
            reserve(ResearchCampaignV1(tmp_path, 'campaign_one'), 'op' + str(number))
            return 'reserved'
        except (MutationBusyError, BudgetExhaustedError):
            return 'blocked'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert results.count('reserved') == 1
    assert service.status()['reserved']['account_jobs'] == 1


def test_copied_root_and_old_budget_entry_cannot_reset(tmp_path):
    root = tmp_path / 'registered'
    root.mkdir()
    config = authorization()
    service = ResearchCampaignV1.create(root, config)
    copied = tmp_path / 'copied'
    shutil.copytree(root, copied)
    with pytest.raises(BudgetLedgerMismatchError, match='ROOT_CONFLICT'):
        ResearchCampaignV1(copied, 'campaign_one').status()
    with pytest.raises(BudgetLedgerMismatchError, match='RESOURCE_AWARE_ENTRY'):
        AutonomousRunBudgetV1(run_id='campaign_one', objective_id='objective', path=service.budget_path,
                             **{key: config[key] for key in config if key.startswith('max_')})


def test_usage_above_bound_pauses_all_new_work_and_keeps_reservation(tmp_path):
    service = ResearchCampaignV1.create(tmp_path, authorization())
    reserve(service)
    service.start_operation('op1')
    with pytest.raises(BudgetLedgerMismatchError, match='EXCEEDS_RESERVED'):
        service.settle_operation('op1', actual={'account_jobs': 1, 'wall_seconds': 21},
                                 outcome='COMPLETED', evidence_identity='receipt')
    assert service.status()['paused']
    assert service.status()['reserved']['account_jobs'] == 1


def test_expiry_and_truncated_event_history_fail_closed(tmp_path):
    config = authorization()
    config['expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    expired = ResearchCampaignV1.create(tmp_path, config)
    with pytest.raises(BudgetExhaustedError, match='EXPIRED'):
        reserve(expired)
    other = tmp_path / 'other'
    other.mkdir()
    service = ResearchCampaignV1.create(other, authorization())
    reserve(service)
    path = service.budget_path.with_name('run_budget_events.jsonl')
    lines = path.read_text(encoding='utf-8').splitlines()
    path.write_text('\n'.join(lines[:-1]) + '\n', encoding='utf-8')
    with pytest.raises(BudgetLedgerMismatchError, match='TRUNCATED'):
        service.status()


@pytest.mark.parametrize('resource', AutonomousRunBudgetV2.resource_names)
def test_each_resource_dimension_blocks_dispatch_at_total_limit(tmp_path, resource):
    config = authorization()
    config['resource_limits'][resource] = 0
    service = ResearchCampaignV1.create(tmp_path, config)
    bounds = {name: 1 for name in AutonomousRunBudgetV2.resource_names}
    with pytest.raises(BudgetExhaustedError, match='CAMPAIGN_RESOURCE_LIMIT'):
        service.reserve_operation(operation_id='all_resources', batch_id='batch1', stage='EXPLORATION',
            kind='MODEL', subject_identity='one', upper_bounds=bounds,
            cost_bound_evidence='synthetic_enforced_upper_bound')
    assert service.status()['operations'] == {}
