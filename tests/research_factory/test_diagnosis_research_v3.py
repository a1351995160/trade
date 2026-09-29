"""连续研究通过真实公共数据、审批、账户与证据链运行；模型使用标注的合成提案。"""
from copy import deepcopy
import json
import pytest
from pathlib import Path

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.diagnosis_research_v3 import DiagnosisResearchV3, SCREEN_VERSION
from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
from chanlun_trader.research_factory.strategy_submission_v1 import StrategySubmissionV1
from test_research_campaign_v1 import authorization
from test_research_data_provider_v1 import dataset, provider
from test_strategy_submission_v1 import request


class Proposals:
    def __init__(self, invalid=False, unknown=False):
        self.calls, self.contexts = 0, []
        self.invalid, self.unknown = invalid, unknown

    def enforce_budget_limits(self, *, max_tokens, max_cost_microunits):
        return {'enforced': True, 'max_tokens': max_tokens, 'max_cost_microunits': max_cost_microunits,
                'provider': 'SYNTHETIC_NO_BILLING', 'evidence_identity': 'LOCAL_FIXED_OUTPUT_TEST'}

    def can_recover(self, directory, context_hash):
        return (Path(directory) / 'INVOCATION.json').exists()

    def invoke(self, context, *, staging_dir, timeout_seconds):
        path = Path(staging_dir) / 'INVOCATION.json'
        if path.exists():
            return json.loads(path.read_text())['proposal']
        self.calls += 1
        self.contexts.append(deepcopy(context))
        if self.unknown:
            raise RuntimeError('unknown provider result')
        rule = request()['rule']
        rule['max_hold_sessions'] = self.calls
        if self.invalid:
            rule['version'] = 'INVALID'
        receipt = {'context_hash': stable_hash(context), 'response_hash': stable_hash(rule),
                   'proposal': rule, 'synthetic': True}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt), encoding='utf-8')
        return rule


def setup(tmp_path, invoker=None):
    args = dataset(tmp_path / 'data')
    auth = authorization()
    auth.update(max_batches=2, max_trials_per_batch=4, max_total_predictive_trials=8)
    auth['resource_limits']['candidate_attempts'] = 2
    campaign = ResearchCampaignV1.create(tmp_path, auth)
    value = request()
    value['benchmark'] = 'FULL_POOL_BUY_HOLD'
    for key in ('feature_start', 'account_start', 'account_end'):
        value[key] = args[key]
    authority = {'objective_id': auth['objective_id'], 'budget_path': str(tmp_path / 'account_budget.json'),
        'data_authorization': args['authorization'], 'expires_at': auth['expires_at'],
        'source': {'origin': 'USER_EXPLICIT_CURRENT_TASK', 'statement': 'Synthetic campaign test'}}
    permit = {key: value[key] for key in ('initial_cash','symbols','feature_start','account_start','account_end',
              'max_positions','max_symbol_exposure_bps','costs','benchmark')}
    authority['account_authorization'] = {**permit, 'purpose': 'FROZEN_PUBLIC_ACCOUNT_PLANS',
        'campaign_ref': {'root': str(tmp_path), 'authorization_id': auth['authorization_id']}, 'max_account_jobs': 3}
    submission = StrategySubmissionV1(provider(tmp_path / 'data', []), lambda ref: deepcopy(authority), tmp_path / 'tasks')
    model = invoker or Proposals()
    service = DiagnosisResearchV3.create(campaign, submission,
        template={key: val for key, val in value.items() if key not in ('rule','strategy_id')},
        screen_policy={'version':SCREEN_VERSION, 'min_base_net_return':0, 'min_stress_net_return':0,
                       'max_drawdown':.2, 'min_trade_count':1, 'min_subperiod_net_return':0,
                       'require_positive_excess':True},
        model_limits={'timeout_seconds':20, 'max_tokens':100, 'max_cost_microunits':20,
                      'cost_bound_evidence':'synthetic_hard_bound', 'account_wall_seconds':60}, invoker=model)
    return service, model


def test_two_batches_real_public_accounts_all_fail_normally_and_resume(tmp_path):
    service, model = setup(tmp_path)
    result = service.run()
    assert result['status'] == 'AUTHORIZED_ATTEMPTS_COMPLETE', result.get('stop_reason')
    assert len(result['attempts']) == 2 and model.calls == 2
    assert result['selected'] == [] and result['strategy_qualified'] is False
    assert result['budget']['batch_count'] == 2
    assert result['budget']['used']['account_jobs'] == 6
    assert result['budget']['used']['verification_jobs'] == 6
    assert result['budget']['used']['data_experiments'] == 2
    assert result['budget']['used']['model_calls'] == 2
    assert result['budget']['used']['model_tokens'] == 200
    assert result['budget']['used']['model_cost_microunits'] == 40
    assert model.contexts[1]['failure_classes'] and model.contexts[1]['previous_designs']
    assert 'capability_fingerprint' in model.contexts[1]
    for directory in sorted(service.root.glob('candidate_*')):
        for kind in ('DATA', 'VERIFY'):
            resource = json.loads((directory / (kind + '_WORKER_RESOURCE.json')).read_text())
            access = json.loads((directory / (kind + '_WORKER_ACCESS.json')).read_text())
            assert resource['returncode'] == 0 and resource['timed_out'] is False
            assert resource['wall_limit_seconds'] == 60
            assert access['wall_seconds'] == 60
            assert resource['process_limit'] == access['process_limit'] == (3 if kind == 'DATA' else 2)
            assert resource['process_limit_enforced'] == access['process_limit_enforced']
        task = json.loads((directory / 'TASK.json').read_text())
        account = Path(task['job_path']).parent
        assert json.loads((account / 'VERIFICATION.json').read_text())['advance_allowed'] is True
        assert len(list(account.glob('*_REPORT.md'))) == 3
    before = deepcopy(result['budget']['used'])
    assert service.run()['budget']['used'] == before and model.calls == 2
    material = service.confirmation_material()
    assert material['profile'] == 'SYNTHETIC' and len(material['exposures']) == 2
    assert len(material['attempts']) == 2 and material['selected'] == []


def test_unknown_model_is_never_called_again(tmp_path):
    service, model = setup(tmp_path, Proposals(unknown=True))
    assert service.run()['status'] == 'RECONCILIATION_REQUIRED'
    result = service.run()
    assert result['status'] == 'WAITING_MODEL_RECONCILIATION'
    assert model.calls == 1 and result['budget']['reserved']['model_calls'] == 1
    assert result['budget']['reserved']['model_tokens'] == 100


def test_invalid_candidates_consume_attempts_without_accounts(tmp_path):
    service, model = setup(tmp_path, Proposals(invalid=True))
    result = service.run()
    assert result['status'] == 'AUTHORIZED_ATTEMPTS_COMPLETE', result.get('stop_reason')
    assert result['budget']['used']['candidate_attempts'] == 2
    assert result['budget']['used']['account_jobs'] == 0
    assert all(row['status'] == 'REJECTED' for row in result['attempts'])


def test_transfer_to_new_model_preserves_prior_memory_and_frozen_limits(tmp_path):
    service, original = setup(tmp_path, Proposals(invalid=True))
    config = service.config()
    first = service._attempt(1, config, [])
    replacement = Proposals(invalid=True)
    recovered = DiagnosisResearchV3(service.campaign, service.submission, replacement)
    result = recovered.run()
    assert result['status'] == 'AUTHORIZED_ATTEMPTS_COMPLETE'
    assert original.calls == replacement.calls == 1
    assert replacement.contexts[0]['previous_designs'][0]['hypothesis'] == first['hypothesis']
    assert replacement.contexts[0]['failure_classes'] == [first['feedback']]
    assert result['budget']['used']['model_calls'] == 2


def test_changed_capability_rejected_before_data_or_account(tmp_path):
    service, model = setup(tmp_path)
    original = model.invoke
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        snapshot = service.submission.capabilities()
        snapshot['fingerprint'] = 'changed'
        service.submission.capabilities = lambda: deepcopy(snapshot)
        return result
    model.invoke = changed
    result = service.run()
    assert result['attempts'][0]['feedback']['codes'] == ['CAPABILITY_CHANGED']
    assert result['budget']['used']['data_experiments'] <= 1


def test_record_artifact_change_blocks_resume(tmp_path):
    service, model = setup(tmp_path, Proposals(invalid=True))
    service.run()
    path = service.root / 'candidate_0001' / 'PROPOSAL.json'
    path.write_text('{}', encoding='utf-8')
    result = service.run()
    assert result['status'] == 'RECONCILIATION_REQUIRED'
    assert 'DIAGNOSIS_RESEARCH_RECORD_CHANGED' in result['stop_reason']
    assert model.calls == 2


def test_real_default_adapter_cannot_claim_unverified_fee_cap(tmp_path):
    from chanlun_trader.research_factory.bounded_model_v1 import BoundedCodexInvokerV1
    with pytest.raises(PermissionError, match='HARD_BUDGET_UNVERIFIED'):
        setup(tmp_path, BoundedCodexInvokerV1())
    assert not list(tmp_path.glob('**/CONFIG.json'))


@pytest.mark.parametrize('kind', ['DATA', 'VERIFY'])
def test_bounded_data_worker_timeout_preserves_start_and_forbids_retry(tmp_path, kind):
    service, model = setup(tmp_path)
    directory = service.root / 'timeout_probe'
    with pytest.raises(RuntimeError, match='WORKER_FAILED_NO_RETRY'):
        service._bounded(directory, kind, {'task': {}, 'name': 'probe', 'policy': {}}, .001)
    resource = json.loads((directory / (kind + '_WORKER_RESOURCE.json')).read_text())
    assert resource['timed_out'] is True
    assert resource['wall_limit_seconds'] == .001
    before = (directory / (kind + '_WORKER_START.json')).read_bytes()
    with pytest.raises(RuntimeError, match='WORKER_FAILED_NO_RETRY'):
        service._bounded(directory, kind, {'task': {}, 'name': 'probe', 'policy': {}}, .001)
    assert (directory / (kind + '_WORKER_START.json')).read_bytes() == before


def test_tick_yields_after_one_candidate_and_resumes_without_redispatch(tmp_path):
    service, model = setup(tmp_path, Proposals(invalid=True))
    assert service.status()['progress']['remaining_attempts'] == 2
    first = service.tick()
    assert first['status'] == 'READY' and model.calls == 1
    assert first['progressed_candidate_id'] == 'CANDIDATE_0001'
    assert first['progress']['completed_attempts'] == 1
    assert first['progress']['next_candidate_id'] == 'CANDIDATE_0002'
    replacement = Proposals(invalid=True)
    resumed = DiagnosisResearchV3(service.campaign, service.submission, replacement)
    second = resumed.tick()
    assert second['status'] == 'AUTHORIZED_ATTEMPTS_COMPLETE' and replacement.calls == 1
    assert second['progress']['remaining_attempts'] == 0
    assert resumed.tick()['status'] == 'AUTHORIZED_ATTEMPTS_COMPLETE'
    assert replacement.calls == 1


def test_tick_wait_status_matches_unknown_model_and_pause(tmp_path):
    service, model = setup(tmp_path, Proposals(unknown=True))
    service.campaign.pause('explicit pause')
    assert service.status()['status'] == 'PAUSED_OR_SCOPE_WAITING'
    service.campaign.resume('explicit resume')
    service.tick()
    assert service.status()['status'] == 'WAITING_MODEL_RECONCILIATION'
    assert service.tick()['status'] == 'WAITING_MODEL_RECONCILIATION'
    assert model.calls == 1


def test_restored_default_model_status_explicitly_reports_hard_budget_block(tmp_path):
    service, _ = setup(tmp_path)
    restored = DiagnosisResearchV3(service.campaign, service.submission)
    state = restored.status()
    assert state['status'] == 'PAUSED_OR_SCOPE_WAITING'
    assert state['stop_reason'] == 'DIAGNOSIS_MODEL_HARD_BUDGET_UNVERIFIED'


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows venv launcher process quota')
def test_actual_windows_venv_data_freeze_uses_bounded_third_process(tmp_path, monkeypatch):
    import venv
    import site
    from chanlun_trader.research_factory import diagnosis_research_v3 as module
    service, _ = setup(tmp_path)
    environment = tmp_path / 'worker_venv'
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(environment)
    # 嵌套venv只继承base环境；显式复用本次测试环境中已安装的依赖，不联网安装。
    (environment / 'Lib' / 'site-packages' / 'test_dependencies.pth').write_text(
        '\n'.join(site.getsitepackages()) + '\n', encoding='utf-8')
    monkeypatch.setattr(module.sys, 'executable', str(environment / 'Scripts' / 'python.exe'))
    submitted = {**service.config()['template'], 'strategy_id': 'VENV_DATA', 'rule': request()['rule']}
    preview = service.submission.preview(submitted)
    directory = service.root / 'venv_data'
    task = service._task(directory, submitted, preview, 60)
    resource = json.loads((directory / 'DATA_WORKER_RESOURCE.json').read_text(encoding='utf-8'))
    handshake = json.loads((directory / 'DATA_WORKER_ACCESS.json').read_text(encoding='utf-8'))
    assert Path(task['job_path']).is_file()
    assert resource['returncode'] == 0 and resource['timed_out'] is False
    assert resource['windows_job_bound'] is True
    assert resource['process_limit'] == handshake['process_limit'] == 3
    assert resource['process_limit_enforced'] is handshake['process_limit_enforced'] is True
