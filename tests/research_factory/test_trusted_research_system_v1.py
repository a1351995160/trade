"""跨阶段接手：确认等待不暂停探索；模型离线不阻断已授权合成 Paper。"""
from datetime import datetime, timedelta
from copy import deepcopy

from test_diagnosis_research_v3 import setup, Proposals
from test_forward_paper_v1 import paper_source
from test_lifecycle_service_v2 import configured
from test_campaign_confirmation_v1 import setup_case
from chanlun_trader.research_factory.lifecycle_service_v2 import LifecycleServiceV2
from chanlun_trader.research_factory.trusted_research_host_v1 import TrustedResearchHostV1
from chanlun_trader.research_factory.diagnosis_research_v3 import DiagnosisResearchV3
from chanlun_trader.execution_policy import ExecutionPolicy


def test_waiting_confirmation_model_offline_paper_and_operator_resume(tmp_path, paper_source):
    (tmp_path / 'research').mkdir()
    (tmp_path / 'confirmation').mkdir()
    research, model = setup(tmp_path / 'research', Proposals(invalid=True))
    original, paper, args = configured(tmp_path, paper_source)
    confirmation, _, future = setup_case(tmp_path / 'confirmation')
    preview = confirmation.preview(selected=['passed'], not_before=future)
    confirmation.freeze(selected=['passed'], not_before=future, preview_identity=preview['preview_identity'])
    assert confirmation.readiness()['status'] == 'WAITING_METHOD_APPLICABILITY'
    bindings = {**original.bindings, 'research': {'kind': 'RESEARCH',
        'root': str(research.root), 'implementation': 'DIAGNOSIS_V3'}}
    registry = {str(research.root): research}
    service = LifecycleServiceV2(tmp_path, bindings, synthetic_clock=original.synthetic_clock,
                                 research_services=registry)
    service.create_job(**args)
    service.jobs.start('paper_job')
    research_args = {**args, 'job_id': 'research_job', 'binding_id': 'research', 'max_calls': 2,
        'stages': [{**args['stages'][0], 'key': 'CANDIDATE_1'},
                   {**args['stages'][0], 'key': 'CANDIDATE_2',
                    'not_before': args['stages'][0]['not_after'],
                    'not_after': (datetime.fromisoformat(args['stages'][0]['not_after']) + timedelta(minutes=5)).isoformat()}]}
    service.create_job(**research_args)
    service.jobs.start('research_job')
    # 不启动后台，仅调用同一宿主的一个调度轮；全部使用合成时钟/提案。
    host = TrustedResearchHostV1(service)
    first = host.tick()
    assert first['paper_job']['status'] == 'COMPLETED'
    assert model.calls == 1
    assert research.status()['progress']['completed_attempts'] == 1
    counts = deepcopy(research.campaign.status()['used'])
    stages = paper.status()['completed_stages']
    # 新操作者重建服务，默认模型无法证明硬预算，研究等待；Paper原件仍可恢复。
    offline = DiagnosisResearchV3(research.campaign, research.submission)
    recovered = LifecycleServiceV2(tmp_path, bindings, synthetic_clock=lambda: original.synthetic_clock() + timedelta(minutes=5),
        research_services={str(research.root): offline})
    second = TrustedResearchHostV1(recovered).tick()
    assert second['paper_job']['status'] == 'COMPLETED'
    assert research.campaign.status()['used'] == counts
    assert paper.status()['completed_stages'] == stages
    assert paper.status()['real_observation_days'] == 0
    assert not paper.status()['strategy_qualified']
    assert confirmation.readiness()['status'] == 'WAITING_METHOD_APPLICABILITY'
    permissions = recovered.operation_permissions(ExecutionPolicy(mode='GOVERNED', workspace_kind='SYNTHETIC'))
    assert 'paper_job' in permissions['job_ids']
    assert recovered.operation_permissions(ExecutionPolicy()) == {'create_binding_ids': [], 'job_ids': []}
