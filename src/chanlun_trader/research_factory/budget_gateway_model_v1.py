"""固定 HTTPS 预算网关客户端；远端硬限制的部署证据是显式外部前提。

这不是 OpenAI/Codex 的费用限制实现。只有维护者核验并固定合同哈希的网关
才可安装策略；合同必须由远端在付费调用前强制执行。未部署服务、未取得
真实强制证据或未完成真实调用时，不能据此宣称持续模型研究已验收。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

from .bounded_model_v1 import candidate_output_schema, candidate_prompt
from .bounded_research_v1 import _put, _read
from .common import stable_hash
from .context import PerformanceBlindGuard


PROTOCOL = 'TRUSTED_MODEL_BUDGET_GATEWAY_V1'
ENFORCEMENT = {
    'exact_input_count_before_paid_dispatch': True,
    'provider_output_limit_includes_reasoning': True,
    'atomic_worst_case_cost_reservation': True,
    'financial_liability_never_exceeds_reserved_usd': True,
    'no_tools_files_network_or_conversation_state': True,
    'at_most_one_paid_dispatch_per_invocation_id': True,
    'unknown_outcome_keeps_worst_case_reservation': True,
    'policy_install_and_status_queries_are_nonbillable': True,
}
USAGE_KEYS = {'input_tokens', 'output_tokens', 'total_tokens', 'cost_microunits', 'model_calls'}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 包括同域重定向；凭证与研究上下文只发给维护者配置的固定端点。
        return None


class TrustedBudgetGatewayInvokerV1:
    """使用受保护部署配置，不接受研究上下文提供 URL、凭证或预算证明。

contract SHA 是维护者对实际部署的信任锚，不是模型传入的字符串批准。
HTTP 认证与 TLS 仅确认所信任的服务；远端的预付责任上限、精确计量和
单次派发实现仍必须另有真实审计/验收证据，客户端不能凭布尔声明创造它们。
"""

    def __init__(self, *, endpoint, model_id, bearer_token, trusted_contract_sha256):
        parsed = urlsplit(endpoint)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or '\\' in endpoint):
            raise ValueError('MODEL_GATEWAY_FIXED_HTTPS_ENDPOINT_REQUIRED')
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError('MODEL_GATEWAY_MODEL_REQUIRED')
        if (not isinstance(bearer_token, str) or not bearer_token
                or any(character in bearer_token for character in '\r\n')):
            raise ValueError('MODEL_GATEWAY_CREDENTIAL_REQUIRED')
        if not isinstance(trusted_contract_sha256, str) or not re.fullmatch('[0-9a-f]{64}', trusted_contract_sha256):
            raise ValueError('MODEL_GATEWAY_TRUSTED_DEPLOYMENT_REQUIRED')
        self.endpoint = endpoint.rstrip('/')
        self.model_id = model_id
        self.contract_sha256 = trusted_contract_sha256
        self._bearer_token = bearer_token
        self._opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler())
        self._policy = None
        self._contract = None

    def _http(self, method, path, body=None, *, timeout_seconds=10):
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0):
            raise ValueError('MODEL_GATEWAY_TIMEOUT_INVALID')
        payload = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
        request = Request(self.endpoint + path, data=payload, method=method, headers={
            'Authorization': 'Bearer ' + self._bearer_token,
            'Content-Type': 'application/json', 'Accept': 'application/json',
        })
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                # 响应大小是本地资源界限；HTTP timeout 不被用作费用保障。
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError('MODEL_GATEWAY_RESPONSE_TOO_LARGE')
            result = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if not isinstance(result, dict):
                raise ValueError('MODEL_GATEWAY_RESPONSE_INVALID')
            return result
        except HTTPError as error:
            raise RuntimeError('MODEL_GATEWAY_HTTP_' + str(error.code)) from None
        except (URLError, TimeoutError, OSError):
            # 不输出可能含凭证、上下文或服务端秘密的异常/响应体，也不自动重试。
            raise RuntimeError('MODEL_GATEWAY_TRANSPORT_UNKNOWN') from None

    @staticmethod
    def _positive(value):
        return type(value) is int and value > 0

    def _check_contract(self, contract):
        if (stable_hash(contract) != self.contract_sha256 or contract.get('protocol') != PROTOCOL
                or contract.get('model_id') != self.model_id or contract.get('currency') != 'USD'
                or contract.get('enforcement') != ENFORCEMENT or contract.get('max_calls_per_invocation') != 1
                or not self._positive(contract.get('max_total_tokens'))
                or not self._positive(contract.get('max_cost_microunits'))
                or not isinstance(contract.get('provider'), str) or not contract['provider'].strip()
                or any(not isinstance(contract.get(key), str) or not re.fullmatch('[0-9a-f]{64}', contract[key])
                       for key in ('deployment_evidence_sha256', 'pricing_evidence_sha256'))):
            raise PermissionError('MODEL_GATEWAY_HARD_BUDGET_UNVERIFIED')
        try:
            expiry = datetime.fromisoformat(contract['valid_until'].replace('Z', '+00:00'))
            valid = expiry.tzinfo is not None and datetime.now(timezone.utc) < expiry
        except (KeyError, TypeError, ValueError, AttributeError):
            valid = False
        if not valid:
            raise PermissionError('MODEL_GATEWAY_HARD_BUDGET_EXPIRED')

    def enforce_budget_limits(self, *, max_tokens, max_cost_microunits):
        """安装远端不可变策略；该端点按合同禁止派发模型或产生费用。"""
        if not self._positive(max_tokens) or not self._positive(max_cost_microunits):
            raise ValueError('MODEL_GATEWAY_BUDGET_INVALID')
        contract = self._http('GET', '/v1/contract')
        self._check_contract(contract)
        if max_tokens > contract['max_total_tokens'] or max_cost_microunits > contract['max_cost_microunits']:
            raise PermissionError('MODEL_GATEWAY_BUDGET_OUTSIDE_TRUSTED_CONTRACT')
        policy = {'protocol': PROTOCOL, 'contract_sha256': self.contract_sha256, 'model_id': self.model_id,
                  'max_input_tokens': max_tokens, 'max_output_tokens': max_tokens, 'max_total_tokens': max_tokens,
                  'max_cost_microunits': max_cost_microunits, 'currency': 'USD', 'max_calls': 1,
                  'tools': [], 'external_input_refs': [], 'conversation_state': None}
        policy_id = stable_hash(policy)
        expected = {'protocol': PROTOCOL, 'contract_sha256': self.contract_sha256, 'policy_id': policy_id,
                    'policy': policy, 'enforced': True}
        installed = self._http('POST', '/v1/policies', expected)
        if installed != expected:
            raise PermissionError('MODEL_GATEWAY_POLICY_INSTALL_UNVERIFIED')
        self._contract, self._policy = deepcopy(contract), deepcopy(expected)
        return {'enforced': True, 'provider': contract['provider'], 'max_tokens': max_tokens,
                'max_cost_microunits': max_cost_microunits, 'currency': 'USD',
                'evidence_identity': stable_hash(expected), 'contract_sha256': self.contract_sha256,
                'policy_id': policy_id, 'max_calls': 1, 'tools': []}

    def _check_request(self, request, context_hash):
        content = {key: value for key, value in request.items() if key != 'request_hash'}
        if (request.get('request_hash') != stable_hash(content) or request.get('context_hash') != context_hash
                or request.get('contract_sha256') != self.contract_sha256 or request.get('model_id') != self.model_id
                or request.get('schema_hash') != stable_hash(request.get('output_schema'))
                or request.get('policy_id') != stable_hash(request.get('policy'))
                or not isinstance(request.get('invocation_id'), str)
                or not re.fullmatch('[0-9a-f]{32}', request['invocation_id'])):
            raise ValueError('MODEL_GATEWAY_REQUEST_IDENTITY_CONFLICT')
        if self._policy is not None and (request['policy_id'] != self._policy['policy_id']
                                        or request['policy'] != self._policy['policy']):
            raise ValueError('MODEL_GATEWAY_BUDGET_CHANGED')

    def _validate_outcome(self, outcome, request):
        bindings = {'protocol': PROTOCOL, **{key: request[key] for key in (
            'invocation_id', 'request_hash', 'context_hash', 'schema_hash', 'contract_sha256', 'policy_id', 'model_id')}}
        if any(outcome.get(key) != value for key, value in bindings.items()):
            raise ValueError('MODEL_GATEWAY_OUTCOME_IDENTITY_CONFLICT')
        state = outcome.get('state')
        if state in {'RUNNING', 'UNKNOWN', 'NOT_FOUND'}:
            if outcome.get('worst_case_reservation_retained') is not True:
                raise ValueError('MODEL_GATEWAY_UNKNOWN_RESERVATION_UNVERIFIED')
            return False
        if state not in {'COMPLETED', 'FAILED', 'REJECTED'}:
            raise ValueError('MODEL_GATEWAY_OUTCOME_STATE_INVALID')
        usage = outcome.get('usage')
        if (not isinstance(usage, dict) or set(usage) != USAGE_KEYS
                or any(type(value) is not int or value < 0 for value in usage.values())
                or usage['total_tokens'] != usage['input_tokens'] + usage['output_tokens']
                or (usage['model_calls'] == 0 and any(usage.values()))
                or (state == 'COMPLETED' and (usage['model_calls'] < 1 or not isinstance(outcome.get('response_text'), str)))
                or (usage['model_calls'] > 0 and (not isinstance(outcome.get('provider_request_id'), str)
                                                 or not outcome['provider_request_id']))
                or outcome.get('tools') != [] or outcome.get('external_input_refs') != []
                or type(outcome.get('duration_seconds')) not in (int, float)
                or not math.isfinite(outcome['duration_seconds']) or outcome['duration_seconds'] < 0):
            raise ValueError('MODEL_GATEWAY_USAGE_OR_TOOL_EVIDENCE_INVALID')
        return True

    def _save_outcome(self, directory, outcome, request):
        try:
            terminal = self._validate_outcome(outcome, request)
        except ValueError:
            _put(directory / 'UNVERIFIED_RESPONSE.json', {'request_hash': request['request_hash'], 'response': outcome})
            raise
        if not terminal:
            raise RuntimeError('MODEL_GATEWAY_UNFINISHED_RECONCILIATION_REQUIRED')
        # 原始真实用量先落盘；即使超耗或输出损坏，也不得丢弃或裁成合规。
        _put(directory / 'SUCCESS.json', {'context_hash': request['context_hash'],
             'request_hash': request['request_hash'], 'outcome': outcome})

    def _finish(self, directory, request):
        success = _read(directory / 'SUCCESS.json')
        outcome = success['outcome']
        if (success.get('context_hash') != request['context_hash']
                or success.get('request_hash') != request['request_hash']
                or not self._validate_outcome(outcome, request)):
            raise ValueError('MODEL_GATEWAY_SUCCESS_IDENTITY_CONFLICT')
        usage, policy = outcome['usage'], request['policy']
        exceeded = (usage['input_tokens'] > policy['max_input_tokens']
                    or usage['output_tokens'] > policy['max_output_tokens']
                    or usage['total_tokens'] > policy['max_total_tokens']
                    or usage['cost_microunits'] > policy['max_cost_microunits']
                    or usage['model_calls'] > policy['max_calls'])
        proposal = None
        if outcome['state'] == 'COMPLETED':
            try:
                proposal = json.loads(outcome['response_text'],
                    parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                if not isinstance(proposal, dict):
                    proposal = None
            except (ValueError, TypeError):
                pass
        receipt = {'context_hash': request['context_hash'], 'request_hash': request['request_hash'],
            'schema_hash': request['schema_hash'], 'contract_sha256': self.contract_sha256,
            'policy_id': request['policy_id'], 'invocation_id': request['invocation_id'],
            'proposal': proposal, 'response_hash': stable_hash(proposal), 'success_hash': stable_hash(success),
            'model_id': self.model_id, 'runtime_version': PROTOCOL,
            'provider_request_id': outcome.get('provider_request_id'), 'duration_seconds': outcome['duration_seconds'],
            'usage': usage, 'origin': 'REAL_TRUSTED_BUDGET_GATEWAY_HTTP', 'synthetic': False,
            'state': outcome['state'], 'hard_budget_exceeded': exceeded}
        _put(directory / 'INVOCATION.json', receipt)
        if exceeded:
            raise PermissionError('MODEL_GATEWAY_HARD_BUDGET_BREACHED')
        if outcome['state'] != 'COMPLETED':
            raise RuntimeError('MODEL_GATEWAY_MODEL_' + outcome['state'])
        if proposal is None:
            raise ValueError('BOUNDED_MODEL_INVALID_JSON')
        return proposal

    def can_recover(self, staging_dir, context_hash):
        directory = Path(staging_dir)
        if not (directory / 'REQUEST.json').exists():
            return False
        request = _read(directory / 'REQUEST.json')
        self._check_request(request, context_hash)
        if not (directory / 'SUCCESS.json').exists():
            outcome = self._http('GET', '/v1/invocations/' + request['invocation_id'])
            if not self._validate_outcome(outcome, request):
                return False
            self._save_outcome(directory, outcome, request)
        success = _read(directory / 'SUCCESS.json')
        if (success.get('context_hash') != context_hash or success.get('request_hash') != request['request_hash']
                or not self._validate_outcome(success['outcome'], request)):
            raise ValueError('MODEL_GATEWAY_SUCCESS_IDENTITY_CONFLICT')
        return True

    def invoke(self, context, *, staging_dir, timeout_seconds):
        PerformanceBlindGuard.assert_blind(context)
        directory = Path(staging_dir)
        if (directory / 'REQUEST.json').exists():
            return self.recover(context, staging_dir=directory, timeout_seconds=timeout_seconds)
        if self._policy is None or self._contract is None:
            raise PermissionError('MODEL_GATEWAY_HARD_BUDGET_UNVERIFIED')
        self._check_contract(self._contract)
        context_hash = stable_hash(context)
        schema = candidate_output_schema(context.get('capabilities', {}))
        request = {'protocol': PROTOCOL, 'invocation_id': uuid4().hex, 'context_hash': context_hash,
            'schema_hash': stable_hash(schema), 'output_schema': schema, 'prompt': candidate_prompt(context),
            'contract_sha256': self.contract_sha256, 'model_id': self.model_id,
            'policy_id': self._policy['policy_id'], 'policy': deepcopy(self._policy['policy'])}
        request['request_hash'] = stable_hash(request)
        directory.mkdir(parents=True, exist_ok=True)
        _put(directory / 'REQUEST.json', request)
        outcome = self._http('POST', '/v1/invocations', request, timeout_seconds=timeout_seconds)
        self._save_outcome(directory, outcome, request)
        return self._finish(directory, request)

    def recover(self, context, *, staging_dir, timeout_seconds):
        """仅核对并查询原调用；授权/价格保证到期后仍可结算历史责任。"""
        PerformanceBlindGuard.assert_blind(context)
        directory = Path(staging_dir)
        if not (directory / 'REQUEST.json').is_file():
            raise PermissionError('MODEL_GATEWAY_RECOVERY_REQUEST_REQUIRED')
        request = _read(directory / 'REQUEST.json')
        self._check_request(request, stable_hash(context))
        if not (directory / 'SUCCESS.json').exists():
            # 本地写入与外部派发不可能原子提交；即使远端 NOT_FOUND 也禁止重 POST。
            outcome = self._http('GET', '/v1/invocations/' + request['invocation_id'], timeout_seconds=timeout_seconds)
            self._save_outcome(directory, outcome, request)
        return self._finish(directory, request)
