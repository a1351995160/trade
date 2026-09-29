"""公共V3与每日计划CLI只分派固定服务，不动态加载调用方代码。"""
from types import SimpleNamespace
import pytest
from scripts import run_strategy_lifecycle_v1 as cli


def test_public_freeze_dispatches_exact_job_name_and_archive(monkeypatch):
    seen=[]
    class Archive:
        def __init__(self,root): seen.append(root)
        def freeze(self,path,name):
            seen.extend([path,name])
            return {'strategy_id':'PS_fixed'}
    monkeypatch.setattr(cli,'PublicStrategyArchiveV3',Archive)
    args=cli.parser().parse_args(['public-freeze','--archive-root','archives','--job-path','job/JOB.json','--name','candidate'])
    assert cli.execute(args)=={'strategy_id':'PS_fixed'}
    assert seen==['archives','job/JOB.json','candidate']


@pytest.mark.parametrize('operation',['review','revoke-strategy'])
def test_review_revoke_use_fixed_id_version_router(monkeypatch,operation):
    seen=[]
    archive=SimpleNamespace(review=lambda key: {'review':key},revoke=lambda key,reason:{'revoke':key,'reason':reason})
    def route(root,ids):
        seen.append((root,ids))
        return archive
    monkeypatch.setattr(cli,'archive_for_ids',route)
    words=[operation,'--archive-root','archives','--strategy-id','PS_fixed']
    if operation=='revoke-strategy': words+=['--reason','stop']
    result=cli.execute(cli.parser().parse_args(words))
    assert seen==[('archives',['PS_fixed'])]
    assert result==({'review':'PS_fixed'} if operation=='review' else {'revoke':'PS_fixed','reason':'stop'})


def test_daily_plan_dispatches_existing_paper_session(monkeypatch):
    import chanlun_trader.research_factory.trusted_daily_plan_v1 as plans
    session=object()
    monkeypatch.setattr(cli,'ForwardPaperSessionV1',lambda root:session if root=='paper' else None)
    monkeypatch.setattr(plans,'trusted_daily_plan',lambda value:{'same_session':value is session})
    assert cli.execute(cli.parser().parse_args(['daily-plan','--root','paper']))=={'same_session':True}
