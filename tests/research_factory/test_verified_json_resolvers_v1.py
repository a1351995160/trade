"""已验证摘要的 resolver 复制契约；只用内存上下文，不进入账户或 Owner 路径。"""
from copy import deepcopy
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory import account_purpose_time_extension_v1 as extension
from chanlun_trader.research_factory import failed_account_recovery_v1 as recovery


class CallerReference(dict):
    """保留调用者引用的 deepcopy 协议，不能把它当作已验证 JSON 摘要。"""


@pytest.fixture(params=('extension', 'recovery'))
def resolver_case(request, monkeypatch, tmp_path):
    summary = json.loads(r'''
        {
          "authorization_id": "ARTIFICIAL-AUTHORIZATION",
          "objective_id": "ARTIFICIAL-OBJECTIVE",
          "base_authorization_identity": "artificial-base-identity",
          "expires_at": "2030-01-01T00:00:00+00:00",
          "aggregate_reserved_delta": 43200,
          "original_failed_account": "BASE",
          "restored_reserved_seconds": 29660,
          "accounts": {
            "BASE": {
              "operation_id": "BASE-OP", "old_total_seconds": 28800,
              "effective_total_seconds": 72000, "additional_reserved_seconds": 43200,
              "original_profile": {"profile_hash": "base-profile"},
              "segment_count": 50, "charged_seconds": 42339.896402100036,
              "charge_head": "base-head", "parent_segment_prefix_hash": "base-prefix",
              "next_segment_number": 51, "notes": {"tags": ["base"]}
            },
            "STRESS": {
              "operation_id": "STRESS-OP", "old_total_seconds": 28800,
              "effective_total_seconds": 72000, "additional_reserved_seconds": 43200,
              "original_profile": {"profile_hash": "stress-profile"},
              "segment_count": 0, "charged_seconds": 0.0,
              "charge_head": null, "parent_segment_prefix_hash": "stress-prefix",
              "next_segment_number": 1, "notes": {"tags": ["stress"]}
            }
          },
          "source_replacements": [{"nested": {"tags": ["source"]}}],
          "runtime_sources": [{"nested": {"tags": ["runtime"]}}],
          "economic_sources": [{"nested": {"tags": ["economic"]}}],
          "capabilities_snapshot": {"nested": {"tags": ["capability"]}},
          "successor_plans": {
            "BASE": {
              "nested": {"tags": ["plan"]},
              "unicode": ["\u4e2d\u6587", "e\u0301", "\ud83d\ude80", "\ud800"],
              "numbers": [0, -0.0, 1e-300, 1e300]
            }
          },
          "calendar_deadline_extension": {"nested": {"tags": ["deadline"]}},
          "calendar_task_scope": {"nested": {"tags": ["calendar-scope"]}}
        }
    ''')
    manifest_ref = CallerReference(path=tmp_path / 'caller-manifest.json', sha256='a' * 64)
    manifest_ref.metadata = {'tags': ['caller-only']}
    calls = []
    proofs = []

    def proof():
        value = {'manifest_ref': deepcopy(manifest_ref), 'proof_call': len(proofs) + 1}
        proofs.append(value)
        return value

    context = SimpleNamespace(summary_value=summary, extension_id='artificial-extension',
        recovery_id='artificial-recovery', execution_expires_at='2030-01-02T00:00:00+00:00', proof=proof)
    owner = (extension.AccountPurposeTimeExtensionV1 if request.param == 'extension'
        else recovery.FailedAccountRecoveryV1)
    resolver = (extension.resolve_time_extension_manifest if request.param == 'extension'
        else recovery.resolve_failed_account_recovery_manifest)

    def read(root, reference, **flags):
        calls.append((root, reference, flags))
        return context

    monkeypatch.setattr(owner, 'read', staticmethod(read))

    def resolve(**flags):
        return resolver(manifest_ref, root=tmp_path, authorization_id=summary['authorization_id'],
            objective_id=summary['objective_id'], **flags)

    return SimpleNamespace(kind=request.param, summary=summary, manifest_ref=manifest_ref,
        calls=calls, proofs=proofs, owner=owner, read=read, resolver=resolver, resolve=resolve, root=tmp_path)


def test_resolvers_keep_read_flags_proof_and_caller_deepcopy(resolver_case):
    case = resolver_case
    result = case.resolve(for_dispatch=True, for_install=True)

    assert case.calls == [(case.root, case.manifest_ref, {'for_dispatch': True, 'for_install': True})]
    assert case.calls[0][1] is case.manifest_ref
    assert result['proof'] is case.proofs[0]
    assert type(result['manifest_ref']) is CallerReference
    assert isinstance(result['manifest_ref']['path'], Path)
    assert result['manifest_ref'] == case.manifest_ref
    assert result['manifest_ref'] is not case.manifest_ref
    result['manifest_ref'].metadata['tags'].append('returned-reference')
    assert case.manifest_ref.metadata == {'tags': ['caller-only']}
    assert result['proof']['manifest_ref'].metadata == {'tags': ['caller-only']}


def test_resolver_account_alias_and_each_returned_tree_are_independent(resolver_case):
    case = resolver_case
    before = deepcopy(case.summary)
    result = case.resolve()

    assert result['accounts'] == case.summary['accounts']
    assert result['accounts'] is not case.summary['accounts']
    assert result['summary_value'] == case.summary
    assert result['summary_value']['accounts'] is not result['accounts']
    if case.kind == 'recovery':
        assert result['account'] is result['accounts']['BASE']
    result['accounts']['BASE']['notes']['tags'].append('account-result')
    assert result['summary_value']['accounts']['BASE']['notes'] == {'tags': ['base']}

    assert result['successor_plans'] == result['active_plans'] == case.summary['successor_plans']
    assert result['successor_plans'] is not result['active_plans']
    result['successor_plans']['BASE']['nested']['tags'].append('successor-result')
    assert result['active_plans']['BASE']['nested'] == {'tags': ['plan']}
    result['active_plans']['BASE']['nested']['tags'].append('active-result')
    assert result['summary_value']['successor_plans']['BASE']['nested'] == {'tags': ['plan']}

    for key in ('source_replacements', 'runtime_sources', 'economic_sources'):
        assert result[key] == case.summary[key]
        assert result[key] is not result['summary_value'][key]
        result[key][0]['nested']['tags'].append('returned-' + key)
        assert result['summary_value'][key] == case.summary[key]
    if case.kind == 'recovery':
        for key in ('capabilities_snapshot', 'calendar_deadline_extension', 'calendar_task_scope'):
            assert result[key] == case.summary[key]
            assert result[key] is not result['summary_value'][key]
            result[key]['nested']['tags'].append('returned-' + key)
            assert result['summary_value'][key] == case.summary[key]
    result['summary_value']['accounts']['STRESS']['notes']['tags'].append('summary-result')
    assert result['accounts']['STRESS']['notes'] == {'tags': ['stress']}
    assert case.summary == before


def test_each_resolver_call_reads_fresh_and_does_not_reuse_result_trees(resolver_case):
    case = resolver_case
    first = case.resolve()
    first['accounts']['BASE']['notes']['tags'].append('first-account')
    first['active_plans']['BASE']['nested']['tags'].append('first-active')
    first['summary_value']['runtime_sources'].clear()
    case.summary['accounts']['BASE']['notes']['tags'].append('new-source-state')

    second = case.resolve()

    assert len(case.calls) == 2
    assert len(case.proofs) == 2
    assert second['proof'] is case.proofs[1]
    assert second['accounts']['BASE']['notes'] == {'tags': ['base', 'new-source-state']}
    assert second['active_plans']['BASE']['nested'] == {'tags': ['plan']}
    assert second['summary_value']['runtime_sources'] == case.summary['runtime_sources']
    assert first['accounts'] is not second['accounts']
    assert first['summary_value'] is not second['summary_value']


def test_nested_resolver_calls_keep_separate_returned_trees(resolver_case, monkeypatch):
    case = resolver_case
    nested = []

    def read_with_nested_call(root, reference, **flags):
        context = case.read(root, reference, **flags)
        if len(case.calls) == 1:
            nested.append(case.resolve())
        return context

    monkeypatch.setattr(case.owner, 'read', staticmethod(read_with_nested_call))
    outer = case.resolve()

    assert len(case.calls) == 2
    assert len(case.proofs) == 2
    assert nested[0]['accounts'] is not outer['accounts']
    assert nested[0]['summary_value'] is not outer['summary_value']
    nested[0]['accounts']['BASE']['notes']['tags'].append('nested-result')
    assert outer['accounts']['BASE']['notes'] == {'tags': ['base']}
    assert case.summary['accounts']['BASE']['notes'] == {'tags': ['base']}


def test_resolver_keeps_conflict_error_before_copy_or_proof(resolver_case, monkeypatch):
    case = resolver_case
    checkpoints = []
    monkeypatch.setattr(extension, 'startup_checkpoint', checkpoints.append)
    prefix = 'ACCOUNT_PURPOSE_TIME_EXTENSION_' if case.kind == 'extension' else 'FAILED_ACCOUNT_RECOVERY_'

    with pytest.raises(PermissionError, match=prefix + 'AUTHORIZATION_OR_OBJECTIVE_CONFLICT'):
        case.resolver(case.manifest_ref, root=case.root, authorization_id='wrong-authorization',
            objective_id=case.summary['objective_id'])

    assert len(case.calls) == 1
    assert checkpoints == []
    assert case.proofs == []


def test_resolver_json_copies_preserve_scalars_and_cooperative_checkpoints(resolver_case, monkeypatch):
    case = resolver_case
    case.summary['successor_plans']['BASE']['numbers'].extend(json.loads('[NaN, Infinity, -Infinity]'))
    checkpoints = []
    monkeypatch.setattr(extension, 'startup_checkpoint', checkpoints.append)
    result = case.resolve()

    assert checkpoints
    assert len(checkpoints) % 2 == 0
    assert all(pair == ('VERIFICATION_JSON_COPY', 'VERIFICATION_JSON_COPY_COMPLETE')
        for pair in zip(checkpoints[::2], checkpoints[1::2]))
    plan = result['successor_plans']['BASE']
    assert plan['unicode'] == ['中文', 'e\u0301', '\U0001f680', '\ud800']
    numbers = plan['numbers']
    assert numbers[:4] == [0, -0.0, 1e-300, 1e300]
    assert math.copysign(1.0, numbers[1]) == -1.0
    assert math.isnan(numbers[4])
    assert numbers[5:] == [math.inf, -math.inf]


@pytest.mark.parametrize('stop_at', ('VERIFICATION_JSON_COPY', 'VERIFICATION_JSON_COPY_COMPLETE'))
def test_resolver_does_not_swallow_copy_checkpoint_failure(resolver_case, monkeypatch, stop_at):
    case = resolver_case
    before = deepcopy(case.summary)
    checkpoints = []

    def checkpoint(stage):
        checkpoints.append(stage)
        if stage == stop_at:
            raise TimeoutError('artificial-copy-deadline')

    monkeypatch.setattr(extension, 'startup_checkpoint', checkpoint)
    with pytest.raises(TimeoutError, match='artificial-copy-deadline'):
        case.resolve()

    assert checkpoints == (['VERIFICATION_JSON_COPY'] if stop_at == 'VERIFICATION_JSON_COPY'
        else ['VERIFICATION_JSON_COPY', 'VERIFICATION_JSON_COPY_COMPLETE'])
    assert len(case.calls) == 1
    assert case.proofs == []
    assert case.summary == before
