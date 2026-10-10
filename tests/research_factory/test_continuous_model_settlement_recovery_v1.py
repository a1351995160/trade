"""停派后的历史结算测试；合成模型回执，无外部付费和行情读取。"""
from datetime import datetime, timedelta

import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.diagnosis_research_v4 import DiagnosisResearchV4
from chanlun_trader.research_factory.exploration_governance import immutable, read_json
from test_diagnosis_research_v4 import research_case


@pytest.mark.parametrize('stop', ['pause', 'expiry', 'revoke'])
def test_original_model_receipt_settles_after_stop_without_new_guarantee_or_paid_call(tmp_path, monkeypatch, stop):
    research, model, accesses, restore = research_case(tmp_path, unknown=True)
    research.start()
    research.advance()
    assert model.calls == 1 and research.campaign.peek_status()['reserved']['model_calls'] == 1
    if stop == 'expiry':
        from chanlun_trader.research_factory import research_campaign_v1 as campaigns
        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(hours=2)
        monkeypatch.setattr(campaigns, 'datetime', ExpiredClock)
    else:
        getattr(research.campaign, stop)('SYNTHETIC_STOP_BEFORE_RECEIPT')
    gets = []
    def recover_original(folder, context_hash):
        gets.append(context_hash)
        context = read_json(folder.parent / 'CONTEXT.json')
        assert context_hash == stable_hash(context)
        proposal = model.rules[0]
        immutable(folder / 'INVOCATION.json', {'synthetic': True, 'context_hash': context_hash,
            'response_hash': stable_hash(proposal), 'usage': {'input_tokens': 1, 'output_tokens': 1,
                'total_tokens': 2, 'cost_microunits': 1, 'model_calls': 1}})
        immutable(folder / 'RESPONSE.json', proposal)
        return True
    monkeypatch.setattr(model, 'can_recover', recover_original)
    monkeypatch.setattr(model, 'enforce_budget_limits', lambda **kwargs: pytest.fail('历史结算不得安装新保证'))
    restored = DiagnosisResearchV4(research.campaign, restore(), invoker=model)
    result = restored.advance()
    view = restored.campaign.peek_status()
    assert len(gets) == 1 and model.calls == 1
    assert view['used']['model_calls'] == 1 and view['reserved']['model_calls'] == 0
    assert view['operations']['CANDIDATE_0001_MODEL']['status'] == 'COMPLETED'
    assert (research.root / 'candidate_0001' / 'PROPOSAL.json').exists()
    assert result['goal_complete'] is False and result.get('dispatched_segments', 0) == 0
    restored.advance()
    assert len(gets) == 1 and model.calls == 1 and accesses == []
    assert not list(tmp_path.rglob('*_START.json'))


def test_revoked_unknown_model_keeps_original_reservation_without_second_call(tmp_path, monkeypatch):
    research, model, accesses, restore = research_case(tmp_path, unknown=True)
    research.start()
    research.advance()
    research.campaign.revoke('SYNTHETIC_UNKNOWN_STOP')
    gets = []
    monkeypatch.setattr(model, 'enforce_budget_limits', lambda **kwargs: pytest.fail('未知历史调用不得重派'))
    monkeypatch.setattr(model, 'can_recover', lambda *args: gets.append(args) or False)
    restored = DiagnosisResearchV4(research.campaign, restore(), invoker=model)
    for _ in range(2):
        assert restored.advance()['goal_complete'] is False
    view = restored.campaign.peek_status()
    assert len(gets) == 2 and model.calls == 1 and accesses == []
    assert view['reserved']['model_calls'] == 1 and view['used']['model_calls'] == 0
    assert view['operations']['CANDIDATE_0001_MODEL']['status'] == 'UNKNOWN'
