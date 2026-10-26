"""JSON 原件哈希与通用规范编码等价；不缓存身份、费用或权限。"""
import json
import random

import pytest

from chanlun_trader.account_startup_boundary_v1 import StartupBoundaryExceeded
from chanlun_trader.research_factory import account_purpose_time_extension_v1 as extension
from chanlun_trader.research_factory import campaign_scope_v1 as scope
from chanlun_trader.research_factory.common import canonical_json, stable_hash


@pytest.mark.parametrize('text', (
    'null', 'true', 'false', '0', '-0.0', '1e-300', '1e300',
    '9007199254740993', 'NaN', 'Infinity', '-Infinity',
    '"中文🚀\\n"', '[]', '{}',
    '{"z":1,"a":[null,true,false,0,-0.0,1.25,{"嵌套":"é"}]}',
    '{"duplicate":1,"duplicate":2,"a":{"z":1,"a":2}}',
))
def test_parsed_json_hash_is_byte_identical_to_original_canonical_hash(text):
    value = json.loads(text)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    assert encoded == canonical_json(value)
    assert extension._hash_json_tree(value) == stable_hash(value)


def test_many_nested_event_shapes_keep_original_hash_and_detect_changed_payload():
    rng = random.Random(26026)
    for number in range(50):
        value = json.loads(json.dumps({
            'run_id': '人工原任务', 'event_type': 'CAMPAIGN_EXECUTION_SEGMENT_CHARGED',
            'payload': {'segment_number': number, 'seconds': rng.uniform(0, 900),
                'proof': [{'sha256': format(rng.getrandbits(256), '064x'),
                    'path': 'E:\\artificial\\费用.json'} for _ in range(number % 7)],
                'unknown': None, 'valid': True},
        }))
        before = extension._hash_json_tree(value)
        assert before == stable_hash(value)
        value['payload']['seconds'] += 1
        assert extension._hash_json_tree(value) == stable_hash(value) != before


@pytest.mark.parametrize('field', ('identity', 'summary_identity'))
def test_json_identity_checks_each_current_tree_and_rejects_tampering(field):
    value = json.loads('{"nested":{"fee":9.0},"items":[1,2]}')
    value[field] = stable_hash(value)
    assert extension._json_identity(value, field) == extension._identity(value, field) is True
    value['nested']['fee'] = 10.0
    assert extension._json_identity(value, field) == extension._identity(value, field) is False
    assert not extension._json_identity(None, field)


def test_json_hash_keeps_original_lone_surrogate_encoding_error():
    value = json.loads('"\\ud800"')
    for calculate in (stable_hash, extension._hash_json_tree):
        with pytest.raises(UnicodeEncodeError):
            calculate(value)


@pytest.mark.parametrize('stop_at', ('VERIFICATION_JSON_HASH', 'VERIFICATION_JSON_HASH_COMPLETE'))
def test_json_hash_checks_cooperative_deadline_before_and_after_native_encoding(monkeypatch, stop_at):
    seen = []

    def checkpoint(stage):
        seen.append(stage)
        if stage == stop_at:
            raise StartupBoundaryExceeded(stage)

    monkeypatch.setattr(scope, 'startup_checkpoint', checkpoint)
    with pytest.raises(StartupBoundaryExceeded):
        extension._hash_json_tree({'payload': [1, 2, 3]})
    assert seen[-1] == stop_at


def test_owner_return_is_independent_and_next_require_reads_original_again(tmp_path):
    capability = object()
    store = scope.OwnerApprovalStoreV1(tmp_path / 'owner', owner_capability=capability)
    summary = json.loads('{"values":[null,true,0,-0.0,1e-300,"中文",{"fee":9.0}]}')
    reference = store.approve(summary, approver='artificial_owner', capability=capability)
    path = store.directory / (reference['approval_id'] + '.json')
    first = store.require(reference, summary)
    original = json.loads(path.read_text(encoding='utf8'))
    assert first == original
    first['summary']['values'][-1]['fee'] = 99
    assert store.require(reference, summary) == original
    original['summary']['values'][-1]['fee'] = 100
    path.write_text(json.dumps(original, ensure_ascii=True), encoding='utf8')
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_IDENTITY_CONFLICT'):
        store.require(reference, summary)


def test_owner_caller_summary_keeps_general_hash_normalization_and_error_order(tmp_path):
    capability = object()
    store = scope.OwnerApprovalStoreV1(tmp_path / 'owner', owner_capability=capability)
    summary = {'value': 'allowed'}
    reference = store.approve(summary, approver='artificial_owner', capability=capability)
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_BINDING_INVALID'):
        store.require(reference, {'value': 'changed'})
    (store.directory / (reference['approval_id'] + '.json')).unlink()
    with pytest.raises(PermissionError, match='OWNER_APPROVAL_NOT_REGISTERED'):
        store.require(reference, summary)


def test_parsed_json_owner_path_has_same_assertions_and_fresh_revocation(tmp_path):
    capability = object()
    store = scope.OwnerApprovalStoreV1(tmp_path / 'owner', owner_capability=capability)
    summary = json.loads('{"items":[null,true,0,-0.0,1e-300,"中文",{"fee":9.0}]}')
    reference = store.approve(summary, approver='artificial_owner', capability=capability)
    assert store.require_json(reference, summary) == store.require(reference, summary)
    changed = json.loads(json.dumps(summary)); changed['items'][-1]['fee'] = 10.0
    for require in (store.require, store.require_json):
        with pytest.raises(PermissionError, match='OWNER_APPROVAL_BINDING_INVALID'):
            require(reference, changed)
    path = store.directory / (reference['approval_id'] + '.json')
    returned = store.require_json(reference, summary)
    returned['summary']['items'].append('mutated caller')
    assert store.require_json(reference, summary)['summary'] == summary
    path.unlink()
    for require in (store.require, store.require_json):
        with pytest.raises(PermissionError, match='OWNER_APPROVAL_NOT_REGISTERED'):
            require(reference, summary)

