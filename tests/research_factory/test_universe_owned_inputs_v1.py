"""同 worker 的严格子输入复用；冻结事实、所有权和要求仍逐次验证。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from chanlun_trader.research_factory import universe_account_inputs_v1 as account_inputs
from chanlun_trader.research_factory.strategy_submission_v1 import (
    load_frozen_bundle, load_frozen_qualified_bundle,
)
from test_universe_qualified_submission_v2 import qualified_case, _freeze, _read


@pytest.fixture
def frozen_case(tmp_path):
    service, request, _, _ = qualified_case(tmp_path)
    frozen = _freeze(service, request)
    job = _read(frozen['job_path'])
    item = next(iter(job['items'].values()))
    return deepcopy(item['loader_kwargs']), deepcopy(item['backend_options']['window'])


def counted_strict_construction(monkeypatch):
    calls = []
    initialize = account_inputs.UniverseAccountInputsV1._initialize

    def counted(inputs, bundle, window, **options):
        if options['stage'] == 'ACCOUNT' and options['require_ready']:
            calls.append((options['copy_frames'] if 'copy_frames' in options else True,
                          frozenset(options['required_fields']), options['warmup_bars']))
        return initialize(inputs, bundle, window, **options)

    monkeypatch.setattr(account_inputs.UniverseAccountInputsV1, '_initialize', counted)
    return calls


def prepare_owned(loaded, window, **changes):
    receipt = loaded['frame']['qualified_scope']
    return account_inputs._prepare_owned_universe_account_inputs_v1(
        loaded['frame'], window, required_fields=changes.get('required_fields', receipt['required_fields']),
        warmup_bars=changes.get('warmup_bars', receipt['warmup_bars']))


def test_loader_and_owned_prepare_share_one_strict_child_and_frozen_identity(frozen_case, monkeypatch):
    args, window = frozen_case
    calls = counted_strict_construction(monkeypatch)
    loaded = load_frozen_qualified_bundle(**args)
    inputs = prepare_owned(loaded, window)
    assert len(calls) == 1 and calls[0][0] is False
    assert inputs.bundle is loaded['frame']
    assert inputs.input_identity == loaded['input_identity'] == args['input_identity']
    assert inputs._copy_frames is False
    for name in ('daily', 'turn', 'states'):
        assert inputs.bundle[name] is getattr(inputs, name)
    ordinary = load_frozen_bundle(**{key: args[key] for key in ('path', 'sha256', 'input_identity')})
    assert set(ordinary['frame']) == set(loaded['frame'])
    assert '_owned_inputs' not in loaded['frame']
    for name in ('daily', 'turn', 'states'):
        pd.testing.assert_frame_equal(ordinary['frame'][name], loaded['frame'][name], check_exact=True)
    inputs.assert_unchanged()
    reordered = {**window, 'symbols': list(reversed(window['symbols']))}
    assert prepare_owned(loaded, reordered) is inputs
    assert len(calls) == 1
    # 每个新 loader 都完整恢复父包并重新严格派生；复用只存在于当前对象生命期。
    second = load_frozen_qualified_bundle(**args)
    second_inputs = prepare_owned(second, window)
    assert len(calls) == 2 and second_inputs is not inputs


def test_normal_dict_and_public_constructor_do_not_reuse_owned_receipt(frozen_case, monkeypatch):
    args, window = frozen_case
    calls = counted_strict_construction(monkeypatch)
    loaded = load_frozen_qualified_bundle(**args)
    retained = prepare_owned(loaded, window)
    ordinary = {'frame': dict(loaded['frame'])}
    normal_inputs = prepare_owned(ordinary, window)
    public_inputs = account_inputs.prepare_universe_account_inputs_v1(
        loaded['frame'], window, required_fields=retained.required_fields, warmup_bars=retained.warmup_bars)
    assert len(calls) == 3
    assert normal_inputs is not retained and public_inputs is not retained
    close = public_inputs.daily.close.iloc[0]
    loaded['frame']['daily'].loc[0, 'close'] += 1
    assert public_inputs.daily.close.iloc[0] == close


@pytest.mark.parametrize('changed', ['daily', 'metadata', 'scope', 'frame_object'])
def test_owned_bundle_mutation_is_rejected_before_reuse(frozen_case, changed):
    args, window = frozen_case
    loaded = load_frozen_qualified_bundle(**args)
    if changed == 'daily':
        loaded['frame']['daily'].loc[0, 'close'] += 1
    elif changed == 'metadata':
        loaded['frame']['source_identity'] = '0' * 64
    elif changed == 'scope':
        loaded['frame']['qualified_scope']['excluded'][0]['reasons'].append('FAKE')
    else:
        loaded['frame']['daily'] = loaded['frame']['daily'].copy(deep=False)
    with pytest.raises(ValueError, match='UNIVERSE_INPUT_IDENTITY_CHANGED|UNIVERSE_OWNED_INPUT_BINDING_INVALID'):
        prepare_owned(loaded, window)


@pytest.mark.parametrize('changed', ['window', 'fields', 'warmup'])
def test_owned_inputs_require_exact_normalized_window_fields_and_warmup(frozen_case, monkeypatch, changed):
    args, window = frozen_case
    loaded = load_frozen_qualified_bundle(**args)
    calls = counted_strict_construction(monkeypatch)
    changes = {}
    if changed == 'window':
        window['account_start'] = window['calendar'][window['calendar'].index(window['account_start']) + 1]
    elif changed == 'fields':
        changes['required_fields'] = []
    else:
        changes['warmup_bars'] = loaded['frame']['qualified_scope']['warmup_bars'] + 1
    with pytest.raises(ValueError, match='UNIVERSE_OWNED_INPUT_REQUIREMENTS_CONFLICT'):
        prepare_owned(loaded, window, **changes)
    assert calls == []


def test_self_reported_dictionary_attribute_cannot_claim_owned_inputs(frozen_case, monkeypatch):
    args, window = frozen_case
    loaded = load_frozen_qualified_bundle(**args)

    class SelfReportedBundle(dict):
        pass

    reported = SelfReportedBundle(loaded['frame'])
    reported._owned_inputs = loaded['frame']._owned_inputs
    calls = counted_strict_construction(monkeypatch)
    inputs = prepare_owned({'frame': reported}, window)
    assert len(calls) == 1 and inputs.bundle is not loaded['frame']
    with pytest.raises(ValueError, match='UNIVERSE_OWNED_INPUT_OWNER_INVALID'):
        account_inputs._OwnedUniverseBundleV1(inputs, object())


def test_process_change_cannot_reuse_owned_inputs(frozen_case, monkeypatch):
    args, window = frozen_case
    loaded = load_frozen_qualified_bundle(**args)
    process = account_inputs.os.getpid()
    monkeypatch.setattr(account_inputs.os, 'getpid', lambda: process + 1)
    with pytest.raises(ValueError, match='UNIVERSE_OWNED_INPUT_BINDING_INVALID'):
        prepare_owned(loaded, window)


@pytest.mark.parametrize('physical', [True, False])
def test_owned_loader_still_restores_and_checks_formal_child_parquet(frozen_case, physical):
    args, _ = frozen_case
    source = Path(args['path'])
    snapshot = _read(source)
    path = Path(snapshot['frames']['daily']['path'])
    frame = pd.read_parquet(path)
    frame['close'] = frame['close'].astype('float32')
    frame.to_parquet(path, index=False)
    if not physical:
        # 重新哈希物理文件仍不能改变被冻结的类型和逻辑身份。
        snapshot['frames']['daily']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        source.write_text(json.dumps(snapshot), encoding='utf-8')
        args['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    error = 'UNIVERSE_FROZEN_FRAME_CHANGED' if physical else 'UNIVERSE_FROZEN_INPUT_CHANGED'
    with pytest.raises(ValueError, match=error):
        load_frozen_qualified_bundle(**args)


@pytest.mark.parametrize('changed', ['parent_window', 'omitted_scope'])
def test_rehashed_scope_conflict_cannot_skip_full_parent_derivation(frozen_case, changed):
    args, _ = frozen_case
    if changed == 'parent_window':
        source = Path(args['parent_path'])
        snapshot = _read(source)
        loaded = load_frozen_bundle(path=str(source), sha256=args['parent_sha256'],
                                    input_identity=snapshot['input_identity'])
        calendar = snapshot['window']['calendar']
        snapshot['window']['account_start'] = calendar[calendar.index(snapshot['window']['account_start']) + 1]
        snapshot['input_identity'] = account_inputs.universe_input_identity_v1(loaded['frame'], snapshot['window'])
        source.write_text(json.dumps(snapshot), encoding='utf-8')
        args['parent_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    else:
        source = Path(args['path'])
        snapshot = _read(source)
        loaded = load_frozen_bundle(**{key: args[key] for key in ('path', 'sha256', 'input_identity')})
        del snapshot['bundle']['qualified_scope']
        del loaded['frame']['qualified_scope']
        args['input_identity'] = account_inputs.universe_input_identity_v1(loaded['frame'], snapshot['window'])
        snapshot['input_identity'] = args['input_identity']
        source.write_text(json.dumps(snapshot), encoding='utf-8')
        args['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='UNIVERSE_QUALIFIED_DERIVATION_CONFLICT'):
        load_frozen_qualified_bundle(**args)



def test_owned_bundle_and_input_reference_cycle_is_collectable(frozen_case):
    import gc
    import weakref
    args, window = frozen_case
    loaded = load_frozen_qualified_bundle(**args)
    inputs = prepare_owned(loaded, window)
    inputs_ref = weakref.ref(inputs)
    daily_ref = weakref.ref(inputs.daily)
    del inputs, loaded
    gc.collect()
    assert inputs_ref() is None and daily_ref() is None
