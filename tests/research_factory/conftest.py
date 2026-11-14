"""跨模块共享一次完整504日诊断夹具；补丁仅在构建期间生效。"""
import json
from pathlib import Path
import tempfile

import pytest


@pytest.fixture(scope='session')
def completed_rule_diagnosis(tmp_path_factory):
    from test_diagnosis_research_v2 import create_service, trend_payload
    from test_research_rule_strategy_v2 import payload, node
    from test_bounded_rule_loop_v2 import SyntheticInvoker
    with pytest.MonkeyPatch.context() as patch:
        service, loader = create_service(tmp_path_factory.mktemp('diagnosis-rule-full')/'workflow', patch, attempts=2)
        # 首个没有交易的候选失败，第二个明确合成趋势规则成功，完整家族不可省略失败者。
        failed = payload()
        failed['buy'] = node('gt',node('field','close'),node('const',value=1000))
        invoker = SyntheticInvoker([failed,trend_payload()])
        status = service.tick(loader=loader,invoker=invoker)
        while 'screening' not in status and status['status'] != 'BLOCKED':
            status = service.tick(loader=loader,invoker=invoker)
    assert status['status'] == 'READY_FOR_INDEPENDENT_REVIEW', status
    assert status['screening']['selected'] == ['CANDIDATE_002']
    assert len(invoker.contexts) == 2
    # 会话缓存保存已完成结果，不能让合成数据补丁泄漏至其他模块。
    yield service, loader, invoker, status


@pytest.fixture(scope='session')
def _research_short_tmp_root(tmp_path_factory):
    """保留每次运行的独占目录，使 Windows 人工来源归档不依赖系统长路径开关。"""
    parent = tmp_path_factory.getbasetemp()
    root = Path(tempfile.mkdtemp(prefix='rf_', dir=parent.parent))
    (parent / 'RESEARCH_FACTORY_TMP_ROOT.json').write_text(
        json.dumps({'root': str(root), 'exclusive': True}, ensure_ascii=True), encoding='utf-8')
    return root


@pytest.fixture
def tmp_path(_research_short_tmp_root):
    """每个用例仍获得全新空目录，保留全部工件，不改变测试或账户预算。"""
    return Path(tempfile.mkdtemp(prefix='t_', dir=_research_short_tmp_root))
