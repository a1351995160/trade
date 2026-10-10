"""V4 公共语法与网关客户端隔离测试；脚本服务不是真实模型/硬费用验收。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from urllib.error import URLError

import jsonschema
import pytest

from chanlun_trader.research_factory import budget_gateway_model_v1 as gateway
from chanlun_trader.research_factory.bounded_model_v1 import (
    BoundedCodexInvokerV1, candidate_output_schema, candidate_prompt,
)
from chanlun_trader.research_factory.bounded_research_v1 import _read
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.context import PerformanceLeakError
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
from chanlun_trader.research_factory.research_rule_strategy_v4 import ResearchRuleStrategyV4, rule_capabilities


def design_context():
    return {'capabilities': {**rule_capabilities(), 'capability': 'RESEARCH_RULE_STRATEGY_V4'},
            'failure_classes': ['NO_SIGNAL'], 'evidence_boundary': 'EXPLORATION_ONLY'}


def proposal():
    return deepcopy(capabilities()['examples']['multi_indicator_ranked'])


def node(op, *args, **params):
    return {'op': op, 'args': list(args), 'params': params}


def test_public_v4_example_and_numeric_score_match_schema_and_parser():
    value = proposal()
    value['selection']['score'] = node('div', node('add', value['selection']['score'], node('const', value=1)),
                                      node('sub', node('field', 'close'), node('ref', node('field', 'close'), periods=1)))
    schema = candidate_output_schema(design_context()['capabilities'])
    jsonschema.validate(value, schema)
    rule = ResearchRuleStrategyV4(value, strategy_id='MODEL_V4')
    assert rule.selection['score']['op'] == 'div'
    assert rule.requirements.capabilities[0] == 'RESEARCH_RULE_STRATEGY_V4'
    assert schema['properties']['selection']['properties']['tie_breaker'] == {'enum': ['SYMBOL_ASCENDING']}
    prompt = candidate_prompt(design_context())
    assert 'RESEARCH_RULE_STRATEGY_V4' in prompt and 'add/sub/mul/div' in prompt


@pytest.mark.parametrize('score', [
    node('gt', node('field', 'close'), node('const', value=10)),
    node('eval', 'import os'), node('indicator', 'volatility', output='made_up', version='made_up'),
    node('mul', node('field', 'close'), node('const', value=1), source='file:///prices.csv'),
])
def test_boolean_arbitrary_code_and_invalid_scores_rejected(score):
    value = proposal()
    value['selection']['score'] = score
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, candidate_output_schema(design_context()['capabilities']))
    with pytest.raises(ValueError):
        ResearchRuleStrategyV4(value, strategy_id='BAD_V4')


def test_outside_indicator_parameter_and_extra_code_are_rejected():
    value = proposal()
    value['indicator_instances'][0]['params']['window'] = 1000
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, candidate_output_schema(design_context()['capabilities']))
    with pytest.raises(ValueError):
        ResearchRuleStrategyV4(value, strategy_id='BAD_PARAMS')
    value = proposal()
    value['code'] = 'arbitrary python'
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, candidate_output_schema(design_context()['capabilities']))


def contract():
    return {'protocol': gateway.PROTOCOL, 'model_id': 'reviewed-model-snapshot', 'provider': 'reviewed-provider',
            'currency': 'USD', 'enforcement': deepcopy(gateway.ENFORCEMENT), 'max_calls_per_invocation': 1,
            'max_total_tokens': 100000, 'max_cost_microunits': 10000000,
            'deployment_evidence_sha256': 'a' * 64, 'pricing_evidence_sha256': 'b' * 64,
            'valid_until': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}


class ScriptedGateway:
    """仅替换 urllib transport；生产构造器没有可注入计费 callable。"""
    def __init__(self):
        self.contract = contract()
        self.calls = []
        self.outcomes = {}
        self.response_text = json.dumps(proposal())
        self.usage = {'input_tokens': 1200, 'output_tokens': 300, 'total_tokens': 1500,
                      'cost_microunits': 100, 'model_calls': 1}
        self.fail_after_dispatch = False
        self.pending = False
        self.mutate_policy = False
        self.wrong_binding = False

    def open(self, request, timeout):
        body = None if request.data is None else json.loads(request.data)
        self.calls.append((request.get_method(), request.full_url, body))
        assert request.get_header('Authorization') == 'Bearer test-only-credential'
        if request.full_url.endswith('/v1/contract'):
            value = self.contract
        elif request.full_url.endswith('/v1/policies'):
            value = deepcopy(body)
            if self.mutate_policy:
                value['policy']['max_calls'] = 2
        elif request.get_method() == 'POST':
            assert request.full_url == 'https://gateway.test/research/v1/invocations'
            value = {'protocol': gateway.PROTOCOL, **{key: body[key] for key in (
                'invocation_id', 'request_hash', 'context_hash', 'schema_hash', 'contract_sha256', 'policy_id', 'model_id')},
                'state': 'COMPLETED', 'response_text': self.response_text, 'usage': deepcopy(self.usage),
                'provider_request_id': 'provider-request-123', 'duration_seconds': 2.5,
                'tools': [], 'external_input_refs': []}
            self.outcomes[body['invocation_id']] = deepcopy(value)
            if self.fail_after_dispatch:
                raise URLError('test-only-credential must never reach diagnostics')
            if self.wrong_binding:
                value['context_hash'] = 'another-context'
        else:
            value = self.outcomes[request.full_url.rsplit('/', 1)[-1]]
            if self.pending:
                value = {key: item for key, item in value.items() if key not in ('usage', 'response_text')}
                value.update(state='UNKNOWN', worst_case_reservation_retained=True)
        return BytesIO(json.dumps(value).encode())


@pytest.fixture
def scripted(monkeypatch):
    service = ScriptedGateway()
    monkeypatch.setattr(gateway, 'build_opener', lambda *args: service)
    return service


def invoker(service):
    return gateway.TrustedBudgetGatewayInvokerV1(endpoint='https://gateway.test/research',
        model_id=service.contract['model_id'], bearer_token='test-only-credential',
        trusted_contract_sha256=stable_hash(service.contract))


def install(instance):
    return instance.enforce_budget_limits(max_tokens=5000, max_cost_microunits=1000)


def paid_dispatches(service):
    return [row for row in service.calls if row[0] == 'POST' and row[1].endswith('/v1/invocations')]


def test_default_codex_still_has_no_unverified_hard_budget():
    assert not callable(getattr(BoundedCodexInvokerV1(), 'enforce_budget_limits', None))


@pytest.mark.parametrize('endpoint', ['http://gateway.test', 'https://u:p@gateway.test',
                                      'https://gateway.test?token=x', 'https://gateway.test#fragment'])
def test_gateway_requires_fixed_https_endpoint(endpoint):
    with pytest.raises(ValueError, match='FIXED_HTTPS'):
        gateway.TrustedBudgetGatewayInvokerV1(endpoint=endpoint, model_id='model', bearer_token='token',
                                             trusted_contract_sha256='a' * 64)


def test_missing_guarantee_does_not_dispatch_or_write_request(scripted, tmp_path):
    instance = invoker(scripted)
    with pytest.raises(PermissionError, match='HARD_BUDGET_UNVERIFIED'):
        instance.invoke(design_context(), staging_dir=tmp_path, timeout_seconds=2)
    assert scripted.calls == [] and not (tmp_path / 'REQUEST.json').exists()


def test_unpinned_or_weak_contract_and_changed_policy_never_dispatch(scripted):
    instance = invoker(scripted)
    scripted.contract['provider'] = 'unreviewed-provider'
    with pytest.raises(PermissionError, match='UNVERIFIED'):
        install(instance)
    assert not paid_dispatches(scripted)
    scripted.contract['enforcement']['atomic_worst_case_cost_reservation'] = False
    instance = invoker(scripted)
    with pytest.raises(PermissionError, match='UNVERIFIED'):
        install(instance)
    scripted.contract['enforcement']['atomic_worst_case_cost_reservation'] = True
    scripted.mutate_policy = True
    instance = invoker(scripted)
    with pytest.raises(PermissionError, match='POLICY_INSTALL_UNVERIFIED'):
        install(instance)
    assert not paid_dispatches(scripted)


def test_expired_contract_cannot_install(scripted):
    scripted.contract['valid_until'] = '2000-01-01T00:00:00Z'
    with pytest.raises(PermissionError, match='EXPIRED'):
        install(invoker(scripted))
    assert not paid_dispatches(scripted)


def test_installed_policy_binds_limits_tools_and_actual_usage(scripted, tmp_path):
    instance = invoker(scripted)
    guarantee = install(instance)
    assert install(instance) == guarantee
    context = design_context()
    assert instance.invoke(context, staging_dir=tmp_path, timeout_seconds=2) == proposal()
    request = _read(tmp_path / 'REQUEST.json')
    assert request['policy']['max_calls'] == 1
    assert request['policy']['max_total_tokens'] == guarantee['max_tokens'] == 5000
    assert request['policy']['max_cost_microunits'] == guarantee['max_cost_microunits'] == 1000
    assert request['policy']['tools'] == request['policy']['external_input_refs'] == []
    assert request['policy']['conversation_state'] is None
    assert request['schema_hash'] == stable_hash(candidate_output_schema(context['capabilities']))
    assert 'test-only-credential' not in (tmp_path / 'REQUEST.json').read_text(encoding='utf-8')
    receipt = _read(tmp_path / 'INVOCATION.json')
    assert receipt['usage'] == scripted.usage
    assert receipt['context_hash'] == stable_hash(context)
    assert receipt['provider_request_id'] == 'provider-request-123'
    assert receipt['hard_budget_exceeded'] is False
    assert instance.invoke(context, staging_dir=tmp_path, timeout_seconds=2) == proposal()
    assert len(paid_dispatches(scripted)) == 1


def test_timeout_reconciles_original_id_via_get_and_never_reposts(scripted, tmp_path):
    instance = invoker(scripted)
    install(instance)
    scripted.fail_after_dispatch = True
    with pytest.raises(RuntimeError, match='TRANSPORT_UNKNOWN') as error:
        instance.invoke(design_context(), staging_dir=tmp_path, timeout_seconds=2)
    assert 'test-only-credential' not in str(error.value)
    request = _read(tmp_path / 'REQUEST.json')
    restored = invoker(scripted)
    install(restored)
    scripted.pending = True
    assert not restored.can_recover(tmp_path, stable_hash(design_context()))
    with pytest.raises(RuntimeError, match='UNFINISHED_RECONCILIATION_REQUIRED'):
        restored.invoke(design_context(), staging_dir=tmp_path, timeout_seconds=2)
    assert not (tmp_path / 'SUCCESS.json').exists()
    scripted.pending = False
    assert restored.can_recover(tmp_path, stable_hash(design_context()))
    assert restored.invoke(design_context(), staging_dir=tmp_path, timeout_seconds=2) == proposal()
    assert len(paid_dispatches(scripted)) == 1
    assert any(method == 'GET' and url.endswith('/' + request['invocation_id']) for method, url, _ in scripted.calls)


def test_expired_guarantee_can_only_recover_original_request_without_reinstall(scripted, tmp_path, monkeypatch):
    instance = invoker(scripted)
    install(instance)
    scripted.fail_after_dispatch = True
    with pytest.raises(RuntimeError, match='TRANSPORT_UNKNOWN'):
        instance.invoke(design_context(), staging_dir=tmp_path, timeout_seconds=2)
    original_calls = len(scripted.calls)
    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=2)
    monkeypatch.setattr(gateway, 'datetime', ExpiredClock)
    restored = invoker(scripted)
    assert restored.recover(design_context(), staging_dir=tmp_path, timeout_seconds=2) == proposal()
    assert [method for method, _, _ in scripted.calls[original_calls:]] == ['GET']
    assert len(paid_dispatches(scripted)) == 1
    with pytest.raises(PermissionError, match='EXPIRED'):
        install(restored)
    empty = tmp_path / 'never_dispatched'
    with pytest.raises(PermissionError, match='RECOVERY_REQUEST_REQUIRED'):
        restored.recover(design_context(), staging_dir=empty, timeout_seconds=2)
    with pytest.raises(PermissionError, match='HARD_BUDGET_UNVERIFIED'):
        invoker(scripted).invoke(design_context(), staging_dir=empty, timeout_seconds=2)
    assert not (empty / 'REQUEST.json').exists() and len(paid_dispatches(scripted)) == 1


def test_success_checkpoint_finishes_without_new_http(scripted, tmp_path, monkeypatch):
    instance = invoker(scripted)
    install(instance)
    original_put = gateway._put
    def interrupt_receipt(path, payload):
        if path.name == 'INVOCATION.json':
            raise RuntimeError('injected interruption')
        return original_put(path, payload)
    monkeypatch.setattr(gateway, '_put', interrupt_receipt)
    with pytest.raises(RuntimeError, match='injected interruption'):
        instance.invoke(design_context(), staging_dir=tmp_path, timeout_seconds=2)
    calls = len(scripted.calls)
    monkeypatch.setattr(gateway, '_put', original_put)
    assert instance.can_recover(tmp_path, stable_hash(design_context()))
    assert instance.invoke(design_context(), staging_dir=tmp_path, timeout_seconds=2) == proposal()
    assert len(scripted.calls) == calls


@pytest.mark.parametrize('text', ['not JSON', '[1, 2]', '{"x": NaN}'])
def test_invalid_output_preserves_actual_consumption(scripted, tmp_path, text):
    instance = invoker(scripted)
    install(instance)
    scripted.response_text = text
    with pytest.raises(ValueError, match='INVALID_JSON'):
        instance.invoke(design_context(), staging_dir=tmp_path, timeout_seconds=2)
    assert _read(tmp_path / 'INVOCATION.json')['usage'] == scripted.usage
    with pytest.raises(ValueError, match='INVALID_JSON'):
        instance.invoke(design_context(), staging_dir=tmp_path, timeout_seconds=2)
    assert len(paid_dispatches(scripted)) == 1


def test_actual_overrun_is_saved_unclipped_and_blocks(scripted, tmp_path):
    instance = invoker(scripted)
    install(instance)
    scripted.usage['cost_microunits'] = 1001
    with pytest.raises(PermissionError, match='HARD_BUDGET_BREACHED'):
        instance.invoke(design_context(), staging_dir=tmp_path, timeout_seconds=2)
    receipt = _read(tmp_path / 'INVOCATION.json')
    assert receipt['usage']['cost_microunits'] == 1001 and receipt['hard_budget_exceeded'] is True
    assert len(paid_dispatches(scripted)) == 1


def test_response_binding_conflict_saved_for_reconciliation(scripted, tmp_path):
    instance = invoker(scripted)
    install(instance)
    scripted.wrong_binding = True
    with pytest.raises(ValueError, match='OUTCOME_IDENTITY_CONFLICT'):
        instance.invoke(design_context(), staging_dir=tmp_path, timeout_seconds=2)
    assert (tmp_path / 'UNVERIFIED_RESPONSE.json').exists()
    assert not (tmp_path / 'INVOCATION.json').exists()
    assert len(paid_dispatches(scripted)) == 1


def test_context_feedback_leak_blocked_before_dispatch(scripted, tmp_path):
    instance = invoker(scripted)
    install(instance)
    context = design_context()
    context['confirmation'] = {'net_return': .9}
    with pytest.raises(PerformanceLeakError):
        instance.invoke(context, staging_dir=tmp_path, timeout_seconds=2)
    assert not paid_dispatches(scripted)


def test_changed_context_or_budget_cannot_recover_old_invocation(scripted, tmp_path):
    instance = invoker(scripted)
    install(instance)
    instance.invoke(design_context(), staging_dir=tmp_path, timeout_seconds=2)
    with pytest.raises(ValueError, match='IDENTITY_CONFLICT'):
        instance.can_recover(tmp_path, stable_hash({'changed': 'context'}))
    instance.enforce_budget_limits(max_tokens=6000, max_cost_microunits=1000)
    with pytest.raises(ValueError, match='BUDGET_CHANGED'):
        instance.invoke(design_context(), staging_dir=tmp_path, timeout_seconds=2)
    assert len(paid_dispatches(scripted)) == 1


def test_redirect_handler_never_forwards_credential_or_context():
    assert gateway._NoRedirect().redirect_request(None, None, 307, None, None, 'https://other.test') is None
